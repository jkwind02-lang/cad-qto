#!/usr/bin/env python3
"""Recognize point/component instances from neutral CAD block facts.

Evidence-backed model decisions can enter working counts through a frozen
profile. Working recognition and independent human adjudication stay separate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from model_recognition_basis import SCHEMA as MODEL_LEDGER_SCHEMA, load_working_entries, applies_to, recognition_basis
from search_negative_evidence import MTEXT_CODE

import qto_cad_geometry as cad_geometry
from qto_actor_identity import is_model_identity

sys.path.insert(0, str(Path(__file__).resolve().parent))
import point_geometry_review as geometry_receipt

VERSION = "qto-component-neutral-adapter/1.7.0"
# Only these two codes can be released by a dynamic_state_adjudication on an include or exclude rule (registration
# 20261016-1709; exclude added after checkpoint 1);
# arrays, xrefs and missing definitions stay unresolved whatever the rule says.
DYNAMIC_STATE_CODES = cad_geometry.DYNAMIC_STATE_CODES
# Exact block names only: a pattern would let one sentence release every dynamic block of a drawing.
WILDCARD_RE = re.compile(r"\.\*|[?\[\]|^]|(?<!^)\*")
# Same-place review tolerances are physical (registration 20261016-1044 R2): millimetres per output coordinate unit
# come from the declared unit name, else from the drawing INSUNITS and factor_to_output.
UNIT_MM = {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0, "毫米": 1.0, "cm": 10.0, "centimeter": 10.0,
           "centimeters": 10.0, "厘米": 10.0, "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "米": 1000.0,
           "km": 1e6, "kilometers": 1e6, "in": 25.4, "inch": 25.4, "inches": 25.4, "ft": 304.8, "feet": 304.8,
           "yards": 914.4, "miles": 1609344.0}
INSUNITS_MM = {1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0, 7: 1e6, 10: 914.4}
DEFAULT_NEAR_MM = 50.0
DEFAULT_EXACT_MM = 0.001
PROFILE_SCHEMA = "qto-component-recognition-profile/1.0"
LEDGER_SCHEMA = "qto-project-adjudication-ledger/1.0"
CONFIRMATION_SOURCES = {
    "legend",
    "rendering",
    "second_source",
    "user_adjudication",
    "authoritative_project_document",
}


class InputError(ValueError):
    pass


def fail(message: str) -> None:
    raise InputError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 JSON {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path} 顶层必须是对象")
    return value


def load_stream(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"{path}:{line_number} JSON 无效: {exc}")
            if not isinstance(value, dict):
                fail(f"{path}:{line_number} 必须是对象")
            yield value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{label} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        fail(f"{label} 必须是有限数值")
    return result


def compile_pattern(value: Any, label: str) -> re.Pattern[str]:
    try:
        return re.compile(text(value, label), re.IGNORECASE)
    except re.error as exc:
        fail(f"{label} 正则表达式无效: {exc}")


def source_from_manifest(path: Path) -> tuple[Path, Path, dict[str, Any]]:
    manifest = load_json(path)
    if manifest.get("schema_version") != "qto-cad-neutral-evidence/1.0":
        fail("cad-manifest schema_version 必须为 qto-cad-neutral-evidence/1.0")
    if manifest.get("geometry_schema_version") != cad_geometry.GEOMETRY_SCHEMA:
        fail("CAD旧缓存缺少完整WCS几何，请用新版中性提取器重新生成；不得继承旧曲线测长")
    if manifest.get("source_identity_issues"):
        fail("CAD源实体身份存在冲突，不能按句柄认定点位实例；先核验原图并重新取证")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        fail("CAD清单缺少 artifacts")
    resolved = []
    for key in ("layout_entities", "block_definitions"):
        artifact = artifacts.get(key)
        if not isinstance(artifact, dict):
            fail(f"CAD清单缺少 artifacts.{key}")
        stream = Path(text(artifact.get("path"), f"{key}.path")).resolve()
        if not stream.is_file() or sha256(stream) != str(artifact.get("sha256") or "").lower():
            fail(f"CAD清单中的 {key} 缺失或哈希不一致")
        resolved.append(stream)
    return resolved[0], resolved[1], manifest


def load_ledger(path: Path, expected_source: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    ledger = load_json(path)
    if ledger.get("schema_version") == MODEL_LEDGER_SCHEMA:
        return load_working_entries(ledger, path, expected_source)
    if ledger.get("schema_version") != LEDGER_SCHEMA:
        fail(f"ledger.schema_version 必须为 {LEDGER_SCHEMA}")
    if text(ledger.get("source_sha256"), "ledger.source_sha256").lower() != expected_source:
        fail("项目裁决台账绑定的来源哈希与识别配置不一致")
    text(ledger.get("ledger_id"), "ledger_id")
    text(ledger.get("ledger_version"), "ledger_version")
    entries: dict[str, dict[str, Any]] = {}
    raw_entries = ledger.get("entries")
    if not isinstance(raw_entries, list):
        fail("ledger.entries 必须是数组")
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            fail(f"ledger.entries[{index}] 必须是对象")
        entry = dict(raw)
        adjudication_id = text(entry.get("adjudication_id"), f"ledger.entries[{index}].adjudication_id")
        if adjudication_id in entries:
            fail(f"裁决ID重复: {adjudication_id}")
        if entry.get("status") != "active":
            fail(f"{adjudication_id}.status 必须为 active")
        if text(entry.get("confirmation_source"), f"{adjudication_id}.confirmation_source") not in CONFIRMATION_SOURCES:
            fail(f"{adjudication_id}.confirmation_source 不是允许的独立确认来源")
        if is_model_identity(text(entry.get("confirmed_by"), f"{adjudication_id}.confirmed_by")):
            fail(f"{adjudication_id}.confirmed_by 不能是模型或助手")
        text(entry.get("source_id"), f"{adjudication_id}.source_id")
        evidence_ids = entry.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(item, str) or not item.strip() for item in evidence_ids):
            fail(f"{adjudication_id}.evidence_ids 必须是非空文本数组")
        entries[adjudication_id] = entry
    return ledger, entries


def dynamic_adjudication(raw: Any, rule_id: str, decision: str, evidence_ids: set[str] | None,
                         known_blocks: set[str] = frozenset()) -> dict[str, Any] | None:
    """Validated dynamic_state_adjudication: exact block names and/or an instance list, a basis and evidence.

    A name that is a block of this drawing is exact whatever its characters, so anonymous dynamic names such as
    `*U|REF63800` can be named (registration 20261017-0027); a name that is no block and looks like a pattern is
    rejected. block_names may be left out when object_ids lists the instances (adjudication by handle)."""
    if raw is None:
        return None
    label = f"{rule_id}.dynamic_state_adjudication"
    if decision not in ("include", "exclude"):
        fail(f"{label} 只能写在 include 或 exclude 规则上")
    if not isinstance(raw, dict):
        fail(f"{label} 必须是对象")
    names = raw.get("block_names")
    if names is None and raw.get("object_ids") is not None:
        names = []
    elif not isinstance(names, list) or not names:
        fail(f"{label}.block_names 必须是非空数组（精确块名）；只按实例裁决时不写 block_names、写 object_ids")
    block_names = []
    for index, name in enumerate(names):
        value = text(name, f"{label}.block_names[{index}]")
        if value not in known_blocks and (WILDCARD_RE.search(value) or not value.strip("*")):
            fail(f"{label}.block_names[{index}]={value!r} 像通配；动态块裁决只收精确块名")
        if value in block_names:
            fail(f"{label}.block_names 重复：{value}")
        block_names.append(value)
    basis = text(raw.get("basis"), f"{label}.basis")
    evidence = raw.get("evidence_ids")
    if not isinstance(evidence, list) or not evidence:
        fail(f"{label}.evidence_ids 必须是非空数组")
    evidence = [text(item, f"{label}.evidence_ids[{index}]") for index, item in enumerate(evidence)]
    if evidence_ids is not None:
        unknown = sorted(set(evidence) - evidence_ids)
        if unknown:
            fail(f"{label}.evidence_ids 引用了识别记录里没有的证据：{'、'.join(unknown)}")
    object_ids = raw.get("object_ids")
    if object_ids is not None:
        if not isinstance(object_ids, list) or not object_ids:
            fail(f"{label}.object_ids 写了就必须是非空数组")
        object_ids = [text(item, f"{label}.object_ids[{index}]") for index, item in enumerate(object_ids)]
        if len(set(object_ids)) != len(object_ids):
            fail(f"{label}.object_ids 有重复")
    return {"block_names": block_names, "basis": basis, "evidence_ids": evidence,
            "object_ids": None if object_ids is None else set(object_ids)}


def normalize_rules(values: Any, entries: dict[str, dict[str, Any]], evidence_ids: set[str] | None = None,
                    known_blocks: set[str] = frozenset()) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        fail("rules 必须是数组")
    rules = []
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            fail(f"rules[{index}] 必须是对象")
        rule = dict(raw)
        rule_id = text(rule.get("rule_id"), f"rules[{index}].rule_id")
        decision = text(rule.get("decision"), f"{rule_id}.decision")
        if decision not in {"include", "exclude", "container", "unresolved"}:
            fail(f"{rule_id}.decision 无效")
        rule.update(
            {
                "rule_id": rule_id,
                "decision": decision,
                "priority": int(rule.get("priority", 0)),
                "block_re": compile_pattern(rule.get("block_pattern", ".*"), f"{rule_id}.block_pattern"),
                "layer_re": compile_pattern(rule.get("layer_pattern", ".*"), f"{rule_id}.layer_pattern"),
                "parent_re": compile_pattern(rule.get("parent_block_pattern", ".*"), f"{rule_id}.parent_block_pattern"),
                "dynamic_adjudication": dynamic_adjudication(rule.get("dynamic_state_adjudication"), rule_id, decision,
                                                             evidence_ids, known_blocks),
                "text_cond": text_condition(rule.get("text_condition"), rule_id, decision),
            }
        )
        if decision != "unresolved":
            adjudication_id = text(rule.get("adjudication_id"), f"{rule_id}.adjudication_id")
            entry = entries.get(adjudication_id)
            if not entry:
                fail(f"{rule_id}.adjudication_id 未在项目裁决台账找到")
            expected_decision = "include" if decision == "include" else "exclude"
            if entry.get("decision_origin") == "model":
                expected_decision = decision
            if text(entry.get("subject_id"), f"{adjudication_id}.subject_id") != rule_id:
                fail(f"{adjudication_id} 未绑定当前规则 {rule_id}")
            if text(entry.get("decision"), f"{adjudication_id}.decision") != expected_decision:
                fail(f"{adjudication_id} 的裁决与规则决定不一致")
            if text(entry.get("professional_owner"), f"{adjudication_id}.professional_owner") != "qto-components-points":
                fail(f"{adjudication_id}.professional_owner 必须为 qto-components-points")
            rule["authority"] = entry
        if decision == "include":
            for key in ("category", "system", "spec", "unit"):
                rule[key] = text(rule.get(key), f"{rule_id}.{key}")
            level = str(rule.get("quantity_level", "规则推算量" if rule.get("authority", {}).get("decision_origin") == "model" else "确认量"))
            if rule.get("authority", {}).get("decision_origin") == "model" and level == "确认量":
                fail(f"{rule_id} 当前模型识别适配器仍为候选，不标识别确认量；按能力披露保留工作层级")
            if level not in {"确认量", "规则推算量", "方案暂估量"}:
                fail(f"{rule_id}.quantity_level 无效")
            rule["quantity_level"] = level
        rules.append(rule)
    return rules


def text_condition(raw: Any, rule_id: str, decision: str) -> dict[str, Any] | None:
    """Side-note text that must sit near an instance for the rule to apply (registrations 20261015-0830, 1426).
    The radius is in output coordinate units; the pattern is searched in the text without MTEXT codes, ignoring case."""
    if raw is None:
        return None
    label = f"{rule_id}.text_condition"
    if not isinstance(raw, dict):
        fail(f"{label} 必须是对象")
    if decision not in ("include", "exclude"):
        fail(f"{label} 只能写在 include 或 exclude 规则上")
    radius = finite(raw.get("radius"), f"{label}.radius")
    if radius <= 0:
        fail(f"{label}.radius 必须大于0")
    as_key = raw.get("as_instance_key", False)
    if not isinstance(as_key, bool):
        fail(f"{label}.as_instance_key 必须是 true 或 false")
    return {"pattern": compile_pattern(raw.get("pattern"), f"{label}.pattern"), "radius": radius,
            "layer_re": compile_pattern(raw.get("layer_pattern", ".*"), f"{label}.layer_pattern"),
            "basis": text(raw.get("basis"), f"{label}.basis"), "as_instance_key": as_key}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", MTEXT_CODE.sub(" ", str(value))).strip()


def choose_rule(block: str, layer: str, parent_block: str, rules: list[dict[str, Any]], object_id: str | None = None,
                text_ok: Iterable[str] = ()) -> tuple[dict[str, Any] | None, list[str]]:
    """text_ok: rules whose text_condition this instance satisfies (bind_side_texts); other text rules do not apply."""
    text_ok = set(text_ok)
    matches = [
        rule for rule in rules
        if rule["block_re"].search(block)
        and rule["layer_re"].search(layer)
        and rule["parent_re"].search(parent_block)
        and applies_to(rule, object_id)
        and (rule.get("text_cond") is None or rule["rule_id"] in text_ok)
    ]
    if not matches:
        return None, []
    priority = max(rule["priority"] for rule in matches)
    best = sorted((rule for rule in matches if rule["priority"] == priority), key=lambda item: item["rule_id"])
    signatures = {
        (
            rule["decision"],
            rule.get("category"),
            rule.get("system"),
            rule.get("spec"),
        )
        for rule in best
    }
    if len(signatures) > 1:
        return None, [rule["rule_id"] for rule in best]
    return best[0], []


def matrix_for_insert(row: dict[str, Any], origin: list[float]) -> tuple[float, ...]:
    return cad_geometry.matrix_for_insert(row, origin)


def compose(parent: tuple[float, ...], child: tuple[float, ...]) -> tuple[float, ...]:
    return cad_geometry.compose_matrix(parent, child)


def transform_point(value: Any, matrix: tuple[float, ...]) -> list[float] | None:
    return cad_geometry.transform_point(value, matrix)


def point_in_bounds(value: list[float], bounds: dict[str, float] | None) -> bool:
    return bounds is None or (
        bounds["xmin"] <= value[0] < bounds["xmax_exclusive"]
        and bounds["ymin"] <= value[1] < bounds["ymax_exclusive"]
    )


def bbox_intersects(row: dict[str, Any], bounds: dict[str, float] | None) -> bool:
    if bounds is None:
        return True
    bbox = row.get("bbox")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return point_in_bounds([float(row.get("x", 0.0)), float(row.get("y", 0.0))], bounds)
    return not (
        float(bbox[2]) < bounds["xmin"]
        or float(bbox[0]) >= bounds["xmax_exclusive"]
        or float(bbox[3]) < bounds["ymin"]
        or float(bbox[1]) >= bounds["ymax_exclusive"]
    )


def orientation(matrix: tuple[float, ...]) -> tuple[float, bool]:
    """World angle (degrees) of the block X axis and whether the insert is mirrored."""
    origin = transform_point([0.0, 0.0, 0.0], matrix)
    x_axis = [a - b for a, b in zip(transform_point([1.0, 0.0, 0.0], matrix), origin)]
    y_axis = [a - b for a, b in zip(transform_point([0.0, 1.0, 0.0], matrix), origin)]
    angle = math.degrees(math.atan2(x_axis[1], x_axis[0])) % 360.0
    return angle, x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0] < 0


def tolerance_value(raw: Any, label: str, default: float) -> float:
    value = default if raw is None else finite(raw, label)
    if value < 0:
        fail(f"{label} 不能为负")
    return value


def output_unit_scale(unit: dict[str, Any], factor: float, drawing_unit: Any) -> dict[str, Any]:
    """Millimetres per output coordinate unit and how that was established; None when it cannot be told.

    A declared m/cm/mm name that contradicts INSUNITS x factor_to_output is an input error unless the profile
    gives unit.unit_conflict_basis (real drawings often carry a wrong INSUNITS); INSUNITS=0 or unknown only
    leaves the scale to the declared name.
    """
    name = str(unit.get("output_coordinate_unit", "m")).strip()
    declared = UNIT_MM.get(name.lower())
    drawing = drawing_unit if isinstance(drawing_unit, dict) else {}
    code = drawing.get("insunits_code")
    drawing_mm = INSUNITS_MM.get(code) if isinstance(code, int) else None
    if drawing_mm is None and drawing.get("raw_unit") is not None:
        drawing_mm = UNIT_MM.get(str(drawing["raw_unit"]).strip().lower())
    inferred = drawing_mm / factor if drawing_mm else None
    notes = []
    if declared is not None and inferred is not None and not math.isclose(declared, inferred, rel_tol=1e-6):
        message = (f"unit.output_coordinate_unit={name}（每单位 {declared:g} mm）与图纸单位 INSUNITS={code} / "
                   f"{drawing.get('raw_unit')} × factor_to_output={factor:g}（每输出单位 {inferred:g} mm）矛盾")
        conflict_basis = unit.get("unit_conflict_basis")
        if not (isinstance(conflict_basis, str) and conflict_basis.strip()):
            fail(message + "；核对单位，或在 unit.unit_conflict_basis 写明以声明单位为准的依据")
        notes.append(f"{message}；按声明单位，依据：{conflict_basis.strip()}")
    if declared is not None:
        return {"output_unit_mm": declared, "scale_basis": f"声明输出单位 {name}", "notes": notes}
    if inferred is not None:
        return {"output_unit_mm": inferred,
                "scale_basis": f"图纸单位 INSUNITS={code} / {drawing.get('raw_unit')} × factor_to_output={factor:g}",
                "notes": notes}
    notes.append(f"输出单位 {name} 不是 m、cm、mm 等可换算名称，图纸 INSUNITS={code} 也推不出尺度")
    return {"output_unit_mm": None, "scale_basis": "unbound", "notes": notes}


def resolve_duplicate_tolerances(check: dict[str, Any], scale: dict[str, Any]) -> dict[str, Any]:
    """Output-unit radii for the same-place review, bound to millimetres; scale_status=unbound skips the review.

    Explicit output-unit values (near_tolerance / exact_tolerance) need tolerance_basis; *_mm values and the
    50 mm default need a known scale.
    """
    unit_mm = scale["output_unit_mm"]
    basis = check.get("tolerance_basis")
    basis = basis.strip() if isinstance(basis, str) and basis.strip() else None
    resolved: dict[str, Any] = {}
    for key, default_mm in (("near", DEFAULT_NEAR_MM), ("exact", DEFAULT_EXACT_MM)):
        raw, raw_mm = check.get(f"{key}_tolerance"), check.get(f"{key}_tolerance_mm")
        if raw is not None and raw_mm is not None:
            fail(f"duplicate_check.{key}_tolerance 与 {key}_tolerance_mm 只能写一个")
        if raw is not None:
            if basis is None:
                fail(f"duplicate_check.{key}_tolerance 按输出坐标单位理解，必须同时写 tolerance_basis")
            value = tolerance_value(raw, f"duplicate_check.{key}_tolerance", 0.0)
            resolved[key] = (value, "显式（输出坐标单位）")
        else:
            value_mm = tolerance_value(raw_mm, f"duplicate_check.{key}_tolerance_mm", default_mm)
            source = "显式（毫米）" if raw_mm is not None else f"默认 {default_mm:g} mm"
            resolved[key] = (None if unit_mm is None else value_mm / unit_mm, source, value_mm)
    if unit_mm is None and resolved["near"][0] is not None and resolved["exact"][0] is None and \
            check.get("exact_tolerance_mm") is None:
        # an explicit near radius without a scale: only exact coincidence counts as "coincident"
        resolved["exact"] = (0.0, "推不出尺度，完全重合按 0")
    if any(item[0] is None for item in resolved.values()):
        return {"scale_status": "unbound", "output_unit_mm": None, "scale_basis": scale["scale_basis"],
                "scale_notes": scale["notes"], "near_tolerance": None, "exact_tolerance": None,
                "near_tolerance_mm_effective": None, "exact_tolerance_mm_effective": None,
                "tolerance_source": {key: item[1] for key, item in resolved.items()},
                "tolerance_basis": basis or "推不出每输出单位折合多少毫米，未做同位置审查；按本图写 near_tolerance（输出坐标单位）和 tolerance_basis"}
    near, exact = resolved["near"][0], resolved["exact"][0]
    # explicit output-unit radii with a basis stay usable when the scale is unknown; they are just not convertible
    return {"scale_status": "bound" if unit_mm is not None else "explicit_output_units", "output_unit_mm": unit_mm, "scale_basis": scale["scale_basis"],
            "scale_notes": scale["notes"], "near_tolerance": near, "exact_tolerance": exact,
            "near_tolerance_mm_effective": None if unit_mm is None else near * unit_mm,
            "exact_tolerance_mm_effective": None if unit_mm is None else exact * unit_mm,
            "tolerance_source": {key: item[1] for key, item in resolved.items()},
            "tolerance_basis": basis or (f"默认值：完全重合 {DEFAULT_EXACT_MM:g} mm、近似重合 {DEFAULT_NEAR_MM:g} mm（物理尺寸，"
                                         f"按每输出单位 {unit_mm:g} mm 换算）")}


def merge_instance_groups(dispositions, adopted, endpoint_instances, geometry, raw_groups) -> dict[str, Any]:
    """Merge the sub-blocks of one exploded object (e.g. 天正分解块 Body/Cent/Hand) into the primary member's instance.

    Sub-blocks are often offset from the primary's insertion point (blade symbols inside the body, an actuator on its
    edge). A non-primary sub-block joins the primary of the same stem and orientation whose transformed outline, widened
    by anchor_tolerance, contains its insertion point; without a measurable outline the insertion points must lie within
    anchor_tolerance. Sub-blocks that fit no primary or several, and primaries that would take two sub-blocks of the same
    role, stay unmerged and are reported. Only groups declared in the profile are merged.
    """
    if raw_groups is None:
        return {"declared": False, "groups": [], "merged_member_count": 0, "unresolved_groups": []}
    if not isinstance(raw_groups, list):
        fail("instance_grouping 必须是数组")
    by_id = {item["physical_id"]: item for item in dispositions}
    merged_ids: set[str] = set()
    groups, unresolved = [], []
    for index, raw in enumerate(raw_groups):
        if not isinstance(raw, dict):
            fail(f"instance_grouping[{index}] 必须是对象")
        rule_id = text(raw.get("group_rule_id"), f"instance_grouping[{index}].group_rule_id")
        pattern = compile_pattern(raw.get("block_pattern"), f"{rule_id}.block_pattern")
        if not {"stem", "role"} <= set(pattern.groupindex):
            fail(f"{rule_id}.block_pattern 必须含命名组 stem 与 role")
        primary_role = text(raw.get("primary_role"), f"{rule_id}.primary_role")
        if raw.get("anchor_tolerance") is None:
            fail(f"{rule_id}.anchor_tolerance 必须按本图声明（输出坐标单位）")
        tolerance = tolerance_value(raw.get("anchor_tolerance"), f"{rule_id}.anchor_tolerance", 0.0)
        angle_tolerance = tolerance_value(raw.get("angle_tolerance_deg"), f"{rule_id}.angle_tolerance_deg", 0.5)
        basis = text(raw.get("basis"), f"{rule_id}.basis")
        primaries: dict[str, list[dict[str, Any]]] = defaultdict(list)
        others = []
        for item in sorted(dispositions, key=lambda value: value["physical_id"]):
            match = pattern.search(item["block_name"])
            if not match or item["physical_id"] in merged_ids or item["disposition"] in {"merged", "not_applicable"}:
                continue
            if match.group("role") == primary_role:
                primaries[match.group("stem")].append(item)
            else:
                others.append((match.group("stem"), match.group("role"), item))

        def holds(primary, item):
            a, b = geometry[primary["physical_id"]], geometry[item["physical_id"]]
            # Compare axes, not directions: sub-blocks are often inserted mirrored (negative X scale).
            if abs((a["angle"] - b["angle"] + 90.0) % 180.0 - 90.0) > angle_tolerance:
                return False
            box, (x, y) = a.get("world_box"), item["point"][:2]
            if box:
                return box[0] - tolerance <= x <= box[2] + tolerance and box[1] - tolerance <= y <= box[3] + tolerance
            return math.dist(primary["point"][:2], (x, y)) <= tolerance

        parts: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        orphans: list[list[tuple[str, dict[str, Any]]]] = []
        for stem, role, item in others:
            hits = [primary for primary in primaries.get(stem, []) if holds(primary, item)]
            if len(hits) == 1:
                parts[hits[0]["physical_id"]].append((role, item))
            elif hits:
                unresolved.append({"group_rule_id": rule_id, "stem": stem,
                                   "physical_ids": sorted([item["physical_id"]] + [hit["physical_id"] for hit in hits]),
                                   "reason": f"子块落在 {len(hits)} 个主角色 {primary_role} 实例范围内，不合并"})
            else:
                for cluster in orphans:
                    if cluster[0][0] == stem and math.dist(cluster[0][1]["point"][:2], item["point"][:2]) <= tolerance:
                        cluster.append((stem, item))
                        break
                else:
                    orphans.append([(stem, item)])
        for cluster in orphans:
            unresolved.append({"group_rule_id": rule_id, "stem": cluster[0][0],
                               "physical_ids": [item["physical_id"] for _, item in cluster],
                               "reason": f"附近没有主角色 {primary_role} 实例，不合并"})
        for primary_id, members in sorted(parts.items()):
            primary = by_id[primary_id]
            stem = pattern.search(primary["block_name"]).group("stem")
            member_ids = [primary_id] + [item["physical_id"] for _, item in members]
            repeated = sorted(role for role, count in Counter(role for role, _ in members).items() if count > 1)
            if repeated:
                unresolved.append({"group_rule_id": rule_id, "stem": stem, "physical_ids": member_ids,
                                   "reason": f"同一主角色实例下角色 {'、'.join(repeated)} 不止一个，疑似多个对象重叠，不合并"})
                continue
            boxes = [geometry[pid]["world_box"] for pid in member_ids if geometry.get(pid, {}).get("world_box")]
            merged_box = [min(box[0] for box in boxes), min(box[1] for box in boxes),
                          max(box[2] for box in boxes), max(box[3] for box in boxes)] if boxes else None
            for _, item in members:
                merged_ids.add(item["physical_id"])
                item.update({"disposition": "merged", "merged_into": primary_id,
                             "basis": f"实例归并规则 {rule_id}：{basis}"})
            primary["merged_member_ids"] = member_ids[1:]
            primary["merged_geometry_bbox"] = merged_box
            groups.append({"group_rule_id": rule_id, "stem": stem, "primary_id": primary_id,
                           "member_ids": member_ids, "merged_geometry_bbox": merged_box})
    if merged_ids:
        adopted[:] = [item for item in adopted if item["physical_id"] not in merged_ids]
        endpoint_instances[:] = [item for item in endpoint_instances if item["physical_id"] not in merged_ids]
        for item in adopted:
            if by_id[item["physical_id"]].get("merged_member_ids"):
                item["merged_member_ids"] = by_id[item["physical_id"]]["merged_member_ids"]
    return {"declared": True, "groups": groups, "merged_member_count": len(merged_ids), "unresolved_groups": unresolved}


def review_colocated_instances(dispositions, adopted, endpoint_instances, geometry, raw_check, raw_resolutions,
                               scale) -> dict[str, Any]:
    """Flag adopted instances that coincide or nearly coincide; never drop one without a declared resolution.

    Same block + same orientation within tolerance -> duplicate candidate; same category with a different block, or the
    same block turned or mirrored, at the same place -> suspected same physical object. Radii are physical (default
    50 mm near, 0.001 mm exact) converted to output coordinate units; without a scale the review is not run and the
    workpackage stays incomplete (scale_status=unbound).
    """
    check = raw_check if isinstance(raw_check, dict) else {}
    if raw_check is not None and not isinstance(raw_check, dict):
        fail("duplicate_check 必须是对象")
    angle_tolerance = tolerance_value(check.get("angle_tolerance_deg"), "duplicate_check.angle_tolerance_deg", 0.5)
    bound = resolve_duplicate_tolerances(check, scale)
    if bound["scale_status"] == "unbound":
        if raw_resolutions:
            fail("同位置审查的尺度未绑定，duplicate_resolutions 无组可处置；先按本图声明 near_tolerance 与 tolerance_basis")
        return {**bound, "angle_tolerance_deg": angle_tolerance, "review_performed": False,
                "candidate_group_count": 0, "pending_group_count": 0, "pending_instance_count": 0,
                "duplicate_excluded_count": 0, "groups": []}
    exact, near = bound["exact_tolerance"], bound["near_tolerance"]
    if near < exact:
        fail("duplicate_check.near_tolerance 不能小于 exact_tolerance")
    radius = max(near, exact)
    by_id = {item["physical_id"]: item for item in dispositions}
    rows = [item for item in adopted]
    cell = radius if radius > 0 else 1.0
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, item in enumerate(rows):
        buckets[(math.floor(item["point"][0] / cell), math.floor(item["point"][1] / cell))].append(index)
    parent = list(range(len(rows)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    kinds: dict[tuple[int, int], str] = {}
    for index, item in enumerate(rows):
        gx, gy = math.floor(item["point"][0] / cell), math.floor(item["point"][1] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in buckets.get((gx + dx, gy + dy), []):
                    if other <= index:
                        continue
                    peer = rows[other]
                    distance = math.dist(item["point"][:2], peer["point"][:2])
                    if distance > radius:
                        continue
                    a, b = geometry[item["physical_id"]], geometry[peer["physical_id"]]
                    angle_gap = abs((a["angle"] - b["angle"] + 180.0) % 360.0 - 180.0)
                    if item["block_name"] == peer["block_name"] and a["mirrored"] == b["mirrored"] and angle_gap <= angle_tolerance:
                        kind = "coincident_duplicate" if distance <= exact else "near_offset_duplicate"
                    elif item["category"] == peer["category"]:
                        # different block, or the same block turned/mirrored: maybe one object drawn twice
                        kind = "colocated_same_category"
                    else:
                        continue
                    kinds[(index, other)] = kind
                    parent[root(other)] = root(index)
    clusters: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        clusters[root(index)].append(index)
    order = ("coincident_duplicate", "near_offset_duplicate", "colocated_same_category")
    groups = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        ids = sorted(rows[index]["physical_id"] for index in members)
        pair_kinds = {kind for (i, j), kind in kinds.items() if i in members and j in members}
        points = [rows[index]["point"][:2] for index in members]
        groups.append({"group_id": "DUP-" + hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:12],
                       "kind": next(kind for kind in reversed(order) if kind in pair_kinds),
                       "pair_kinds": sorted(pair_kinds),
                       "physical_ids": ids, "block_names": sorted({rows[index]["block_name"] for index in members}),
                       "max_distance": max(math.dist(p, q) for p in points for q in points), "resolution": "pending"})
    groups.sort(key=lambda group: group["physical_ids"])
    resolutions = raw_resolutions if raw_resolutions is not None else []
    if not isinstance(resolutions, list):
        fail("duplicate_resolutions 必须是数组")
    by_members = {tuple(group["physical_ids"]): group for group in groups}
    dropped: set[str] = set()
    resolved_at: dict[str, int] = {}
    for index, raw in enumerate(resolutions):
        if not isinstance(raw, dict):
            fail(f"duplicate_resolutions[{index}] 必须是对象")
        ids = raw.get("physical_ids")
        keep = raw.get("keep")
        if not isinstance(ids, list) or not isinstance(keep, list) or not keep:
            fail(f"duplicate_resolutions[{index}] 须有 physical_ids 与非空 keep")
        group = by_members.get(tuple(sorted(ids)))
        if group is None:
            fail(f"duplicate_resolutions[{index}] 引用的重复候选组不存在：{sorted(ids)}")
        if group["group_id"] in resolved_at:
            # one decision per group: a second entry would silently stack drops onto the first
            fail(f"duplicate_resolutions[{index}] 与 duplicate_resolutions[{resolved_at[group['group_id']]}] "
                 f"处置同一重复候选组 {group['group_id']}；每组只能写一条处置")
        resolved_at[group["group_id"]] = index
        if not set(keep) <= set(ids):
            fail(f"duplicate_resolutions[{index}].keep 必须是该组成员")
        basis = text(raw.get("basis"), f"duplicate_resolutions[{index}].basis")
        drop = sorted(set(ids) - set(keep))
        group.update({"resolution": "keep_all" if not drop else "drop", "keep": sorted(set(keep)), "drop": drop, "basis": basis})
        for physical_id in drop:
            dropped.add(physical_id)
            by_id[physical_id].update({"disposition": "duplicate_excluded", "duplicate_group_id": group["group_id"],
                                       "basis": f"重复候选处置：{basis}"})
    if dropped:
        adopted[:] = [item for item in adopted if item["physical_id"] not in dropped]
        endpoint_instances[:] = [item for item in endpoint_instances if item["physical_id"] not in dropped]
    adopted_ids = {item["physical_id"] for item in adopted}
    for group in groups:
        if group["resolution"] != "pending" and not set(group["keep"]) <= adopted_ids:
            fail(f"重复候选组 {group['group_id']} 声明保留 {group['keep']}，但采用实例中缺少其中的成员")
    pending = [group for group in groups if group["resolution"] == "pending"]
    return {**bound, "angle_tolerance_deg": angle_tolerance, "review_performed": True,
            "candidate_group_count": len(groups), "pending_group_count": len(pending),
            "pending_instance_count": sum(len(group["physical_ids"]) for group in pending),
            "duplicate_excluded_count": len(dropped), "groups": groups}


def bind_side_texts(layout_path: Path, definitions: dict[str, list[dict[str, Any]]], origins: dict[str, list[float]],
                    rules: list[dict[str, Any]], target_layout: str, factor: float, max_depth: int,
                    bounds: dict[str, float] | None, scope_of: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Pre-pass for rules with text_condition (registrations 20261015-0830, 1426).

    Candidates are the instances a text rule could apply to (block, layer, parent and object_ids match); paths,
    ids and the scope test (scope_of, from the main walk) are exactly as in the main walk. Texts are layout texts plus the texts inside the definitions walked
    through on the way to a candidate (containers); the walk does not enter a candidate block, so its own label is
    not a side note. Within the radius only: one text near two candidates, or two texts near one candidate, leaves
    the candidate unresolved; no text lets the text rules fall away. Nothing is bound by nearest distance."""
    text_rules = [rule for rule in rules if rule.get("text_cond")]
    if not text_rules:
        return {}, {}
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)
    candidates: dict[str, dict[str, Any]] = {}
    texts: list[dict[str, Any]] = []

    def add_text(row: dict[str, Any], matrix: tuple[float, ...], prefix: str) -> None:
        if str(row.get("entity_type") or "").upper() not in ("TEXT", "MTEXT") or not str(row.get("text") or "").strip():
            return
        raw = transform_point([finite(row.get(axis, 0.0), f"{row.get('record_id')}.{axis}") for axis in ("x", "y", "z")], matrix)
        if raw is None:
            return
        texts.append({"text_id": f"{prefix}{row.get('entity_handle') or row.get('record_id') or len(texts)}",
                      "text": clean_text(row["text"]), "layer": str(row.get("layer") or ""),
                      "point": [raw[0] * factor, raw[1] * factor], "in_scope": point_in_bounds(raw, bounds)})

    def walk(insert: dict[str, Any], parent_matrix: tuple[float, ...], path: str, stack: tuple[str, ...],
             parent_block: str, inherited_layer: str) -> None:
        if not scope_of(insert, parent_matrix)[0]:
            return   # pruned with its subtree exactly as in the main walk, so it claims no side note (Codex retest R2)
        block_name = str(insert.get("block_name") or "")
        layer = str(insert.get("layer") or "")
        effective_layer = inherited_layer if layer in {"", "0"} and inherited_layer else layer
        physical_id = f"PT-{path}"
        hits = [rule for rule in text_rules
                if rule["block_re"].search(block_name) and rule["layer_re"].search(effective_layer)
                and rule["parent_re"].search(parent_block) and applies_to(rule, physical_id)]
        if hits:
            anchor = [finite(insert.get(axis, 0.0), f"{insert.get('record_id')}.{axis}") for axis in ("x", "y", "z")]
            raw = transform_point(anchor, parent_matrix)
            if raw is not None:
                candidates[physical_id] = {"point": [raw[0] * factor, raw[1] * factor], "rules": hits}
            return
        if block_name not in definitions or block_name in stack or len(stack) >= max_depth:
            return
        matrix = compose(parent_matrix, matrix_for_insert(insert, origins.get(block_name, [0.0, 0.0, 0.0])))
        for child in definitions[block_name]:
            if str(child.get("entity_type") or "").upper() == "INSERT":
                walk(child, matrix, f"{path}/{block_name}/{child.get('record_id') or 'NO-ID'}", (*stack, block_name),
                     block_name, effective_layer)
            else:
                add_text(child, matrix, f"{path}/{block_name}/")

    top = 0
    for row in load_stream(layout_path):
        if row.get("row_type") != "entity" or row.get("layout") != target_layout:
            continue
        if str(row.get("entity_type") or "").upper() == "INSERT":
            top += 1
            walk(row, identity, str(row.get("record_id") or f"TOP-{top}"), (), "", str(row.get("layer") or ""))
        else:
            add_text(row, identity, "")

    cell = max(rule["text_cond"]["radius"] for rule in text_rules)
    grid: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in texts:
        grid[(math.floor(item["point"][0] / cell), math.floor(item["point"][1] / cell))].append(item)
    claims: dict[str, set[str]] = defaultdict(set)   # text id -> candidates it sits near (for any text rule)
    near: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for physical_id, candidate in candidates.items():
        x, y = candidate["point"]
        gx, gy = math.floor(x / cell), math.floor(y / cell)
        pool = [item for dx in (-1, 0, 1) for dy in (-1, 0, 1) for item in grid.get((gx + dx, gy + dy), ())]
        per_rule = {}
        for rule in candidate["rules"]:
            cond = rule["text_cond"]
            found = []
            for item in pool:
                distance = math.hypot(item["point"][0] - x, item["point"][1] - y)
                if distance <= cond["radius"] and cond["layer_re"].search(item["layer"]) and cond["pattern"].search(item["text"]):
                    found.append({"text_id": item["text_id"], "text": item["text"], "layer": item["layer"],
                                  "distance": round(distance, 6)})
                    claims[item["text_id"]].add(physical_id)
            per_rule[rule["rule_id"]] = found
        near[physical_id] = per_rule

    binding: dict[str, dict[str, Any]] = {}
    for physical_id, per_rule in near.items():
        found = {item["text_id"]: item for items in per_rule.values() for item in items}
        shared = sorted(text_id for text_id in found if len(claims[text_id]) > 1)
        status = "文字共用" if shared else "多段文字" if len(found) > 1 else "已绑定" if found else "无文字"
        binding[physical_id] = {
            "status": status,
            "rule_ids": sorted(per_rule),
            "ok": sorted(rule_id for rule_id, items in per_rule.items() if items) if status == "已绑定" else [],
            "texts": sorted(found.values(), key=lambda item: (item["distance"], item["text_id"])),
            "shared_with": sorted(set().union(*(claims[text_id] for text_id in shared)) - {physical_id}) if shared else [],
        }
    key_rules = [rule for rule in text_rules if rule["text_cond"]["as_instance_key"]]
    unclaimed_keys = [
        {"text_id": item["text_id"], "text": item["text"], "layer": item["layer"], "point": item["point"],
         "rule_ids": [rule["rule_id"] for rule in key_rules
                      if rule["text_cond"]["layer_re"].search(item["layer"]) and rule["text_cond"]["pattern"].search(item["text"])]}
        for item in texts
        if item["in_scope"] and item["text_id"] not in claims
        and any(rule["text_cond"]["layer_re"].search(item["layer"]) and rule["text_cond"]["pattern"].search(item["text"])
                for rule in key_rules)
    ]
    return binding, {"text_count": len(texts), "candidate_count": len(candidates), "unclaimed_key_texts": unclaimed_keys}


def side_text_basis(side: dict[str, Any]) -> str:
    texts = "、".join(f"“{item['text']}”" for item in side["texts"][:5])
    if side["status"] == "文字共用":
        return f"旁注文字共用：{texts} 也在 {len(side['shared_with'])} 个同类候选的半径内，不按远近硬绑"
    if side["status"] == "多段文字":
        return f"旁注多段文字：半径内有 {len(side['texts'])} 段符合条件的文字 {texts}，不按远近硬绑"
    return f"半径内没有符合旁注条件的文字（无文字）：{'、'.join(side['rule_ids'])}"


def main() -> int:
    parser = argparse.ArgumentParser(description="从CAD中性块事实构建点位实例、数量输入与端点交接")
    parser.add_argument("--cad-manifest", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--adjudication-ledger", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--disposition-detail", choices=("compact", "full"), default="compact",
                        help="处置清单每行只写识别记录的引用（默认）；full 另把整条识别记录写进每一行，体积随 object_ids 放大")
    args = parser.parse_args()
    try:
        profile_path = args.profile.resolve()
        profile = load_json(profile_path)
        if profile.get("schema_version") != PROFILE_SCHEMA:
            fail(f"profile.schema_version 必须为 {PROFILE_SCHEMA}")
        profile_id = text(profile.get("profile_id"), "profile_id")
        profile_version = text(profile.get("profile_version"), "profile_version")
        expected_source = text(profile.get("expected_source_sha256"), "expected_source_sha256").lower()
        layout_path, block_path, manifest = source_from_manifest(args.cad_manifest.resolve())
        source_identity = manifest.get("authoritative_source")
        if not isinstance(source_identity, dict) or str(source_identity.get("sha256") or "").lower() != expected_source:
            fail("识别配置绑定的来源哈希与当前CAD证据不一致")

        ledger_path = args.adjudication_ledger.resolve()
        ledger_sha = sha256(ledger_path)
        ledger_ref = profile.get("adjudication_ledger")
        if not isinstance(ledger_ref, dict) or ledger_sha != text(ledger_ref.get("sha256"), "adjudication_ledger.sha256").lower():
            fail("识别配置绑定的项目裁决台账哈希不一致")
        ledger, entries = load_ledger(ledger_path, expected_source)
        if text(ledger_ref.get("ledger_id"), "adjudication_ledger.ledger_id") != ledger["ledger_id"]:
            fail("识别配置与项目裁决台账ID不一致")
        if text(ledger_ref.get("ledger_version"), "adjudication_ledger.ledger_version") != ledger["ledger_version"]:
            fail("识别配置与项目裁决台账版本不一致")
        known_evidence = ({str(item.get("evidence_id")) for item in ledger.get("evidence") or [] if isinstance(item, dict)}
                          if ledger.get("schema_version") == MODEL_LEDGER_SCHEMA else None)
        inventory = {str(item.get("block_name")): item for item in manifest.get("blocks", [])
                     if isinstance(item, dict) and item.get("block_name")}
        rules = normalize_rules(profile.get("rules"), entries, known_evidence, set(inventory))

        scope = profile.get("scope")
        if not isinstance(scope, dict):
            fail("profile.scope 必须是对象")
        target_layout = text(scope.get("layout", "Model"), "scope.layout")
        raw_bounds = scope.get("bounds")
        bounds = None
        if raw_bounds is not None:
            if not isinstance(raw_bounds, dict):
                fail("scope.bounds 必须是对象")
            bounds = {
                key: finite(raw_bounds.get(key), f"scope.bounds.{key}")
                for key in ("xmin", "xmax_exclusive", "ymin", "ymax_exclusive")
            }
            if bounds["xmin"] >= bounds["xmax_exclusive"] or bounds["ymin"] >= bounds["ymax_exclusive"]:
                fail("scope.bounds 范围无效")
        unit = profile.get("unit")
        if not isinstance(unit, dict):
            fail("profile.unit 必须是对象")
        factor = finite(unit.get("factor_to_output"), "unit.factor_to_output")
        if factor <= 0:
            fail("unit.factor_to_output 必须大于0")
        coordinate_unit = text(unit.get("output_coordinate_unit", "m"), "unit.output_coordinate_unit")
        unit_source_id = text(unit.get("source_id"), "unit.source_id")
        scale = output_unit_scale(unit, factor, manifest.get("drawing_unit"))
        max_depth = int((profile.get("block_expansion") or {}).get("max_depth", 16))
        if max_depth < 1:
            fail("block_expansion.max_depth 必须大于0")

        definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in load_stream(block_path):
            if row.get("row_type") == "entity":
                definitions[str(row.get("parent_block") or "")].append(row)
        origins = {
            str(item.get("block_name")): [float(value) for value in item.get("origin", [0.0, 0.0, 0.0])[:3]]
            for item in manifest.get("blocks", [])
            if isinstance(item, dict) and item.get("block_name")
        }
        # A definition that exists but holds no entities is not a missing one (registration 20261016-2359): the
        # manifest serialized it and counted zero entities, so it has no rows in the definition stream.
        empty_blocks = {name for name, item in inventory.items()
                        if item.get("serialized") is True and item.get("direct_entity_count") == 0 and name not in definitions}
        identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)
        dispositions: list[dict[str, Any]] = []
        adopted: list[dict[str, Any]] = []
        endpoint_instances: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        block_boxes: dict[str, tuple[list[float] | None, frozenset]] = {}
        extent_incomplete: Counter = Counter()   # in-scope instances by incompleteness note (K3)
        geometry: dict[str, dict[str, Any]] = {}
        seen_blocks: dict[str, set[str]] = defaultdict(set)   # block name -> scoped physical ids
        dynamic_released: dict[str, set[str]] = defaultdict(set)   # rule id -> block names released by its adjudication
        pruned: dict[str, str] = {}   # physical id -> block name of instances cut by the bounds, with their whole subtree

        def transformed_box(box, matrix):
            return cad_geometry.transform_bounds(box, matrix)

        def record_ref(rule: dict[str, Any]) -> dict[str, Any] | None:
            """Reference to the recognition record instead of a copy per row (registration 20261017-0027: rows
            copying a 27,000-object list made one page 15-20 GB). The record itself is in the hash-bound ledger."""
            authority = rule.get("authority")
            if not isinstance(authority, dict):
                return None
            if "_record_ref" not in rule:
                payload = json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
                rule["_record_ref"] = {
                    "adjudication_id": authority.get("adjudication_id"), "subject_id": authority.get("subject_id"),
                    "decision": authority.get("decision"), "decision_origin": authority.get("decision_origin") or "independent",
                    "object_id_count": len(authority["object_ids"]) if isinstance(authority.get("object_ids"), list) else None,
                    "entry_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
            return rule["_record_ref"]

        def definition_box(name, stack=()):
            """(local box, incompleteness notes); see cad_geometry.definition_extent."""
            return cad_geometry.definition_extent(name, definitions, origins, max_depth, block_boxes, stack,
                                                  empty_blocks, DYNAMIC_STATE_CODES)

        def dynamic_only(insert: dict[str, Any]) -> bool:
            return cad_geometry.issue_codes_within(insert, DYNAMIC_STATE_CODES)

        def scope_of(insert: dict[str, Any], parent_matrix: tuple[float, ...]) -> tuple[bool, list[float] | None, frozenset]:
            """Scope test of one INSERT, shared by the main walk and the side-note pre-pass (Codex retest R2): the
            transformed geometry box must meet the scope bounds, so a far base point with the drawing inside still
            counts. Unsupported INSERT geometry (e.g. MINSERT) or no definition box gives no complete instance
            envelope and cannot justify pruning: the instance stays. A dynamic block whose only issues are the
            dynamic-state codes is scoped by its definition extent like any block (registration 20261017-0027); an
            empty definition draws nothing, so its insertion point decides (registration 20261016-2359). An extent
            without fills or with labels at their insertion point is incomplete (K3, registration 20261017-0031): it
            prunes only when the insertion point is outside as well. Returns (in scope, 2D world box, notes)."""
            block_name = str(insert.get("block_name") or "")
            matrix = compose(parent_matrix, matrix_for_insert(insert, origins.get(block_name, [0.0, 0.0, 0.0])))
            anchor = transform_point([finite(insert.get(axis, 0.0), f"{insert.get('record_id')}.{axis}")
                                      for axis in ("x", "y", "z")], parent_matrix)
            if block_name in empty_blocks and (not insert.get("geometry_issues") or dynamic_only(insert)):
                return (anchor is None or point_in_bounds(anchor, bounds)), None, frozenset()
            local_box, notes = definition_box(block_name)
            usable = not insert.get("geometry_issues") or dynamic_only(insert)
            world_box3d = transformed_box(local_box, matrix) if local_box and usable else None
            if not world_box3d:
                return True, None, frozenset()
            world_box = [world_box3d[0], world_box3d[1], world_box3d[3], world_box3d[4]]
            return cad_geometry.extent_in_scope(world_box3d, notes, anchor, bounds), world_box, notes

        def visit(
            insert: dict[str, Any],
            parent_matrix: tuple[float, ...],
            path: str,
            stack: tuple[str, ...],
            parent_block: str,
            inherited_layer: str,
            suppressed_by: dict[str, str] | None,   # disposed ancestor: physical_id, rule_id, decision
            top_level: bool,
        ) -> None:
            block_name = str(insert.get("block_name") or "")
            source_record_id = str(insert.get("record_id") or "NO-ID")
            layer = str(insert.get("layer") or "")
            effective_layer = inherited_layer if layer in {"", "0"} and inherited_layer else layer
            local_anchor = [
                finite(insert.get("x", 0.0), f"{source_record_id}.x"),
                finite(insert.get("y", 0.0), f"{source_record_id}.y"),
                finite(insert.get("z", 0.0), f"{source_record_id}.z"),
            ]
            world_point = transform_point(local_anchor, parent_matrix)
            matrix = compose(parent_matrix, matrix_for_insert(insert, origins.get(block_name, [0.0, 0.0, 0.0])))
            in_scope, world_box, extent_notes = scope_of(insert, parent_matrix)
            if not in_scope:
                pruned[f"PT-{path}"] = block_name
                return  # only complete, conservative bounds allow pruning
            guard_kept = bool(extent_notes) and not bbox_intersects({"bbox": world_box}, bounds)
            for note in extent_notes:
                extent_incomplete[note] += 1
            if guard_kept:
                extent_incomplete["insertion_point_guard"] += 1
            physical_id = f"PT-{path}"
            seen_blocks[block_name].add(physical_id)
            released = None
            is_empty = block_name in empty_blocks
            dynamic = bool(insert.get("geometry_issues")) and dynamic_only(insert)
            dynamic_basis = "transformed_geometry_extent_dynamic_state_unverified"
            # A disposed parent carries its dynamic children like ordinary ones (registration 20261017-0012), and an
            # empty definition draws nothing in any state: neither needs a dynamic-state adjudication.
            if insert.get("geometry_issues") and not (dynamic and (suppressed_by or is_empty)):
                # Decided on issue codes, not on the issue wording; text without matching codes stays unresolved.
                codes = insert.get("geometry_issue_codes")
                if not suppressed_by and dynamic:
                    rule, conflicts = choose_rule(block_name, effective_layer, parent_block, rules, physical_id,
                                                  (side_texts.get(physical_id) or {}).get("ok", ()))
                    adjudication = (rule.get("dynamic_adjudication") if rule and not conflicts and rule["decision"] in ("include", "exclude")
                                    else None)
                    if (adjudication and (not adjudication["block_names"] or block_name in adjudication["block_names"])
                            and (adjudication["object_ids"] is None or physical_id in adjudication["object_ids"])):
                        released = {"rule_id": rule["rule_id"], "basis": adjudication["basis"],
                                    "evidence_ids": adjudication["evidence_ids"], "geometry_issue_codes": sorted(set(codes)),
                                    "location_basis": ("范围裁剪按块定义的当前图形（动态可见状态未核验）；不参与外形归并" if world_box else
                                                       "插入点；动态块图形范围取不到，不参与范围裁剪和外形归并")}
                        dynamic_released[rule["rule_id"]].add(block_name)
                if released is None:
                    dispositions.append({"object_id": physical_id, "physical_id": physical_id, "source_record_id": source_record_id,
                                         "block_name": block_name, "layer": effective_layer, "parent_block": parent_block or None,
                                         "point": [value * factor for value in world_point], "coordinate_unit": coordinate_unit,
                                         "scope_geometry_bbox": world_box if dynamic else None,
                                         "scope_location_basis": dynamic_basis if dynamic and world_box else "insertion_anchor_fallback_requires_review",
                                         "disposition": "unresolved", "basis": "; ".join(insert["geometry_issues"]), "rule_ids": [],
                                         "geometry_issue_codes": codes if isinstance(codes, list) else None, "instance_path": path})
                    failures.append({"kind": "unsupported_insert_geometry", "instance_path": path, "issues": insert["geometry_issues"]})
                    return
            next_suppressed = suppressed_by
            angle, mirrored = orientation(matrix)
            # The extent of a dynamic block scopes it but is not trusted for shape grouping (state not verified);
            # neither is an incomplete extent (fills skipped, labels at their insertion point; K3).
            geometry[physical_id] = {"angle": angle, "mirrored": mirrored,
                                     "world_box": [value * factor for value in world_box] if world_box and not dynamic and not extent_notes else None}

            if in_scope:
                base = {
                    "object_id": physical_id,
                    "physical_id": physical_id,
                    "source_record_id": source_record_id,
                    "block_name": block_name,
                    "layer": effective_layer,
                    "parent_block": parent_block or None,
                    "instance_path": path,
                    "point": [value * factor for value in world_point],
                    "coordinate_unit": coordinate_unit,
                    "scope_geometry_bbox": world_box,
                    "scope_location_basis": "insertion_anchor_empty_definition" if is_empty else (
                        ("insertion_anchor_incomplete_extent_guard" if extent_notes and guard_kept else
                         dynamic_basis if dynamic else
                         "transformed_geometry_extent_incomplete" if extent_notes else "transformed_geometry_extent") if world_box else (
                            "insertion_anchor_dynamic_state_adjudicated" if released else "insertion_anchor_fallback_requires_review")),
                    **({"scope_extent_notes": cad_geometry.extent_note_labels(extent_notes)} if extent_notes else {}),
                    "point_basis": "insertion_anchor_not_verified_connection_port",
                    "disposition": "unresolved",
                    "basis": "未命中冻结点位识别规则",
                    "rule_ids": [],
                }
                if suppressed_by:
                    verb = "承担" if suppressed_by["decision"] == "include" else "排除"
                    base.update(
                        {
                            "disposition": "not_applicable",
                            "basis": (f"由父实例 {suppressed_by['physical_id']}（规则 {suppressed_by['rule_id']}）{verb}专业身份，子块不单独计数"
                                      + ("；动态子块随父实例处置，不需要动态状态裁决" if dynamic else "")),
                            "suppressed_by": suppressed_by,
                        }
                    )
                else:
                    side = side_texts.get(physical_id)
                    rule, conflicts = None, []
                    if side and side["status"] in ("文字共用", "多段文字"):
                        # Ambiguous side notes decide nothing, not even a fallback rule without text_condition.
                        base.update({"basis": side_text_basis(side), "rule_ids": side["rule_ids"],
                                     "side_text": {key: side[key] for key in ("status", "texts", "shared_with")}})
                    else:
                        rule, conflicts = choose_rule(block_name, effective_layer, parent_block, rules, physical_id,
                                                      side["ok"] if side else ())
                    if conflicts:
                        base.update({"basis": "同优先级识别规则冲突", "rule_ids": conflicts})
                    elif rule is None and side and side["status"] == "无文字":
                        base.update({"basis": side_text_basis(side), "rule_ids": side["rule_ids"],
                                     "side_text": {"status": "无文字", "texts": [], "shared_with": []}})
                    elif rule is None and is_empty and not (side and side["status"] in ("文字共用", "多段文字")):
                        base.update({"disposition": "not_applicable", "empty_definition_hint": True,
                                     "basis": "空块定义（取证清单 serialized=true、direct_entity_count=0）：块内没有图元，"
                                              "不单独计数；没有规则处置，仅提示"})
                    elif rule:
                        decision = rule["decision"]
                        base.update(
                            {
                                "rule_ids": [rule["rule_id"]],
                                "adjudication_id": rule.get("adjudication_id"),
                                "recognition_record_ref": record_ref(rule),
                                "basis": str(rule.get("basis") or f"冻结识别规则 {rule['rule_id']}"),
                            }
                        )
                        if args.disposition_detail == "full":
                            base["recognition_record"] = rule.get("authority")
                        if released:
                            base["dynamic_state_adjudication"] = released
                            base["basis"] += f"；动态块状态裁决：{released['basis']}"
                        bound = None
                        if rule.get("text_cond"):
                            bound = dict(side["texts"][0], rule_id=rule["rule_id"], radius=rule["text_cond"]["radius"])
                            base["side_text"] = {"status": "已绑定", **bound}
                            base["basis"] += f"；旁注“{bound['text']}”（距 {bound['distance']:g}）：{rule['text_cond']['basis']}"
                            if rule["text_cond"]["as_instance_key"]:
                                base["instance_key"] = bound["text"]
                        elif side:
                            # A rule without text_condition took the candidate: no side note (the usual fallback),
                            # or a note that a higher-priority rule overrode.
                            base["side_text"] = {"status": "无文字" if side["status"] == "无文字" else "有旁注未采用",
                                                 "texts": side["texts"], "shared_with": []}
                        if decision == "include":
                            base["disposition"] = "adopted"
                            evidence = f"EV-CAD-{source_record_id}|{rule['authority']['source_id']}|{unit_source_id}"
                            instance = {
                                "physical_id": physical_id,
                                "category": rule["category"],
                                "system": rule["system"],
                                "spec": rule["spec"],
                                "unit": rule["unit"],
                                "evidence": evidence,
                                "quantity_level": rule["quantity_level"],
                                "recognition_record": rule["authority"],
                                "point": base["point"],
                                "coordinate_unit": coordinate_unit,
                                "block_name": block_name,
                                "layer": effective_layer,
                                "source_record_id": source_record_id,
                            }
                            if bound:
                                instance["side_text"] = {key: bound[key] for key in ("text", "text_id", "distance", "rule_id")}
                                if "instance_key" in base:
                                    instance["instance_key"] = base["instance_key"]
                            adopted.append(instance)
                            raw_ports = rule.get("network_ports")
                            if raw_ports is None:
                                network_ports = [
                                    {
                                        "port_id": f"{physical_id}-NA",
                                        "network_disposition": "unresolved",
                                        "reason": "尚未核对网络端口；未声明不等于不适用",
                                    }
                                ]
                            else:
                                if not isinstance(raw_ports, list) or not raw_ports:
                                    fail(f"{rule['rule_id']}.network_ports 必须是非空数组")
                                network_ports = []
                                for port_index, raw_port in enumerate(raw_ports, 1):
                                    if not isinstance(raw_port, dict):
                                        fail(f"{rule['rule_id']}.network_ports[{port_index - 1}] 必须是对象")
                                    port = dict(raw_port)
                                    if (rule.get("authority", {}).get("decision_origin") == "model" and port.get("network_disposition") == "deliver"
                                            and "point" not in port and port.get("use_instance_anchor") is not True):
                                        port.update(network_disposition="unresolved", reason="模型尚未确认连接端口坐标，块插入点不能自动当端口")
                                    suffix = text(port.pop("port_id_suffix", f"PORT-{port_index}"), f"{rule['rule_id']}.port_id_suffix")
                                    port["port_id"] = f"{physical_id}-{suffix}"
                                    point_space = port.pop("point_coordinate_space", "output")
                                    if point_space not in {"output", "drawing", "block"}:
                                        fail("端口坐标系必须为output/drawing/block")
                                    if "point" in port and point_space != "output":
                                        p = port["point"]
                                        if not isinstance(p, list) or len(p) not in (2, 3):
                                            fail("端口坐标必须为二维或三维坐标")
                                        p = [finite(v, "port.point") for v in p]
                                        if point_space == "block":
                                            p = transform_point(p, matrix)
                                        port["point"] = [v * factor for v in p]
                                    port.setdefault("point", base["point"])
                                    port.setdefault("space_id", str(rule.get("space_id") or scope.get("space_id") or target_layout))
                                    port.setdefault("coordinate_unit", coordinate_unit)
                                    port.setdefault("endpoint_id", f"EP-{physical_id}-{suffix}")
                                    port.setdefault("terminal_type", rule["category"])
                                    network_ports.append(port)
                            endpoint_instances.append(
                                {
                                    "physical_id": physical_id,
                                    "category": rule["category"],
                                    "system": rule["system"],
                                    "evidence": evidence,
                                    "point": base["point"],
                                    "network_ports": network_ports,
                                }
                            )
                            next_suppressed = {"physical_id": physical_id, "rule_id": rule["rule_id"], "decision": "include"}
                        elif decision == "exclude":
                            base["disposition"] = "excluded"
                            next_suppressed = {"physical_id": physical_id, "rule_id": rule["rule_id"], "decision": "exclude"}
                        elif decision == "container":
                            base["disposition"] = "not_applicable"
                        else:
                            base["disposition"] = "unresolved"
                dispositions.append(base)

            if not block_name or block_name not in definitions:
                if in_scope and not suppressed_by and not is_empty:
                    failures.append(
                        {
                            "kind": "missing_block_definition",
                            "block_name": block_name,
                            "parent_block": parent_block,
                            "instance_path": path,
                        }
                    )
                return
            if block_name in stack:
                failures.append({"kind": "cyclic_block_reference", "block_name": block_name, "instance_path": path})
                return
            if len(stack) >= max_depth:
                failures.append({"kind": "block_depth_limit", "block_name": block_name, "instance_path": path})
                return
            matrix = compose(parent_matrix, matrix_for_insert(insert, origins.get(block_name, [0.0, 0.0, 0.0])))
            for child in definitions[block_name]:
                if str(child.get("entity_type") or "").upper() != "INSERT":
                    continue
                child_id = str(child.get("record_id") or "NO-ID")
                visit(
                    child,
                    matrix,
                    f"{path}/{block_name}/{child_id}",
                    (*stack, block_name),
                    block_name,
                    effective_layer,
                    next_suppressed,
                    False,
                )

        side_texts, side_summary = bind_side_texts(layout_path, definitions, origins, rules, target_layout, factor,
                                                   max_depth, bounds, scope_of)
        top_insert_count = 0
        for row in load_stream(layout_path):
            if (
                row.get("row_type") == "entity"
                and row.get("layout") == target_layout
                and str(row.get("entity_type") or "").upper() == "INSERT"
            ):
                top_insert_count += 1
                visit(
                    row,
                    identity,
                    str(row.get("record_id") or f"TOP-{top_insert_count}"),
                    (),
                    "",
                    str(row.get("layer") or ""),
                    None,
                    True,
                )

        # A dynamic-state adjudication must name blocks and instances that exist in scope; a typo or a stale list
        # is an input error, not a silent no-op (registration 20261016-1709). Since dynamic blocks are cut by the bounds
        # too (K2), a name or instance that lies only in what the bounds cut is listed as out of scope, not refused.
        pruned_names, todo = set(pruned.values()), list(set(pruned.values()))
        while todo:
            for child in definitions.get(todo.pop(), ()):
                name = str(child.get("block_name") or "") if str(child.get("entity_type") or "").upper() == "INSERT" else ""
                if name and name not in pruned_names:
                    pruned_names.add(name); todo.append(name)

        def cut_by_bounds(physical_id: str) -> bool:
            parts = physical_id.split("/")
            return any("/".join(parts[:end]) in pruned for end in range(1, len(parts) + 1, 2))

        dynamic_summary = []
        for rule in rules:
            adjudication = rule.get("dynamic_adjudication")
            if not adjudication:
                continue
            label = f"{rule['rule_id']}.dynamic_state_adjudication"
            unseen = [name for name in adjudication["block_names"] if name not in seen_blocks]
            missing = [name for name in unseen if name not in pruned_names]
            if missing:
                fail(f"{label}.block_names 在本图范围内没有实例：{'、'.join(missing)}")
            stray_cut = []
            if adjudication["object_ids"] is not None:
                names = adjudication["block_names"] or list(seen_blocks)
                known = set().union(*(seen_blocks[name] for name in names))
                stray_all = sorted(adjudication["object_ids"] - known)
                stray_cut = [item for item in stray_all if cut_by_bounds(item)]
                stray = [item for item in stray_all if not cut_by_bounds(item)]
                if stray and adjudication["block_names"]:
                    fail(f"{label}.object_ids 引用了本图范围内不存在、或块名不在 block_names 里的实例：{'、'.join(stray)}")
                if stray:
                    fail(f"{label}.object_ids 引用了本图范围内不存在的实例：{'、'.join(stray)}")
            released_count = sum(1 for item in dispositions
                                 if (item.get("dynamic_state_adjudication") or {}).get("rule_id") == rule["rule_id"])
            dynamic_summary.append({"rule_id": rule["rule_id"], "block_names": adjudication["block_names"],
                                    "object_ids": None if adjudication["object_ids"] is None else sorted(adjudication["object_ids"]),
                                    "basis": adjudication["basis"], "evidence_ids": adjudication["evidence_ids"],
                                    "released_instance_count": released_count,
                                    "unused_block_names": [name for name in adjudication["block_names"]
                                                           if name not in dynamic_released[rule["rule_id"]]],
                                    "out_of_scope_block_names": [name for name in unseen if name in pruned_names],
                                    "out_of_scope_object_ids": stray_cut})

        # Side-note binding summary: candidates by status, key texts no block claimed, keys on two adopted blocks
        # (registrations 20261015-0830, 1426). Only listed; ambiguous or missing notes already left blocks unresolved.
        side_binding = None
        if side_summary:
            side_rows = [item for item in dispositions if item.get("side_text")]
            keyed: dict[str, list[str]] = defaultdict(list)
            for item in adopted:
                if item.get("instance_key") is not None:
                    keyed[item["instance_key"]].append(item["physical_id"])
            side_binding = {
                "text_rule_ids": [rule["rule_id"] for rule in rules if rule.get("text_cond")],
                "texts_considered": side_summary["text_count"],
                "candidate_instance_count": len(side_rows),
                "status_counts": dict(sorted(Counter(item["side_text"]["status"] for item in side_rows).items())),
                "unclaimed_key_text_count": len(side_summary["unclaimed_key_texts"]),
                "unclaimed_key_texts": side_summary["unclaimed_key_texts"][:50],
                "duplicate_instance_keys": [{"instance_key": key, "physical_ids": sorted(ids)}
                                            for key, ids in sorted(keyed.items()) if len(ids) > 1],
            }

        # Empty definitions (registration 20261016-2359): not expansion failures; listed, with a hint for those no rule
        # disposed and a warning when a rule adopted something that draws nothing.
        warnings: list[str] = []
        empty_rows = [item for item in dispositions if item["block_name"] in empty_blocks]
        empty_summary = {
            "instance_count": len(empty_rows),
            "undisposed_instance_count": sum(1 for item in empty_rows if item.get("empty_definition_hint")),
            "blocks": [{"block_name": name, "instance_count": sum(1 for item in empty_rows if item["block_name"] == name),
                        "dispositions": dict(sorted(Counter(item["disposition"] for item in empty_rows
                                                            if item["block_name"] == name).items()))}
                       for name in sorted({item["block_name"] for item in empty_rows})],
        }
        if empty_summary["undisposed_instance_count"]:
            names = sorted({item["block_name"] for item in empty_rows if item.get("empty_definition_hint")})
            warnings.append(f"空块定义提示：{empty_summary['undisposed_instance_count']} 个实例的块定义是空的、没有规则处置，"
                            f"按不单独计数处理（块名：{'、'.join(names[:20])}）")
        adopted_empty = sorted({item["block_name"] for item in empty_rows if item["disposition"] == "adopted"})
        if adopted_empty:
            warnings.append(f"空块定义被规则采用：{'、'.join(adopted_empty[:20])} 的块定义没有图元，请核对是否误收")

        physical_ids = [item["physical_id"] for item in adopted]
        if len(physical_ids) != len(set(physical_ids)):
            fail("点位识别结果出现重复 physical_id")
        # 先按声明合并同位置子块，再对剩余采用实例做同位置重复审查；未处置的重复候选使工作包不完整
        grouping = merge_instance_groups(dispositions, adopted, endpoint_instances, geometry, profile.get("instance_grouping"))
        duplicate_review = review_colocated_instances(dispositions, adopted, endpoint_instances, geometry,
                                                      profile.get("duplicate_check"), profile.get("duplicate_resolutions"),
                                                      scale)
        scale_unbound = duplicate_review["scale_status"] == "unbound"
        disposition_by_id = {item["physical_id"]: item for item in dispositions}
        for group in duplicate_review["groups"]:
            for physical_id in group["physical_ids"]:
                disposition_by_id[physical_id].setdefault("duplicate_group_id", group["group_id"])
        pending_duplicates = duplicate_review["pending_instance_count"]
        disposition_counts = Counter(item["disposition"] for item in dispositions)
        if sum(disposition_counts.values()) != len(dispositions):
            fail("点位实例处置数量不守恒")

        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        disposition_path = output / "点位中性实例处置清单.jsonl"
        with disposition_path.open("w", encoding="utf-8") as handle:
            for item in sorted(dispositions, key=lambda value: value["physical_id"]):
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        # 模型识别台账列出、块适配器却没有承接的对象（非块图符、范围外等）是来源缺口：
        # 主报告与端点交接用同一个完整性判定，缺口 ID 随交接传给消费方。
        model_object_ids = {oid for entry in entries.values() if entry.get("decision_origin") == "model" for oid in entry["object_ids"]}
        missing_model_objects = sorted(model_object_ids - {item["physical_id"] for item in dispositions})
        source_complete = (bool(rules) and not disposition_counts.get("unresolved", 0) and not failures and not missing_model_objects
                           and not pending_duplicates and not grouping["unresolved_groups"] and not scale_unbound)
        # 计数输入带几何审查凭据：计数器重算实例集指纹，自造或改过的实例列表对不上（登记 R1）
        count_input = {
            "schema_version": "qto-reconciled-instance-input/1.0",
            "reconciled_instances": adopted,
            "recognition_basis": recognition_basis(ledger, ledger_path),
            "geometry_review": {
                "schema": geometry_receipt.SCHEMA,
                "adapter_version": VERSION,
                "profile_sha256": sha256(profile_path),
                "source_sha256": expected_source,
                "instance_set_sha256": geometry_receipt.instance_set_sha256(adopted),
                "instance_count": len(adopted),
                "output_coordinate_unit": coordinate_unit,
                "scale_status": duplicate_review["scale_status"],
                "near_tolerance_mm_effective": duplicate_review["near_tolerance_mm_effective"],
                "candidate_group_count": duplicate_review["candidate_group_count"],
                "pending_group_count": duplicate_review["pending_group_count"],
                "duplicate_excluded_count": duplicate_review["duplicate_excluded_count"],
                "workpackage_complete": source_complete,
            },
        }
        count_input_path = output / "点位唯一实例计数输入.json"
        count_input_path.write_text(json.dumps(count_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        endpoint_input = {
            "schema_version": "qto-network-endpoint-handoff-input/1.1",
            "recognition_basis": recognition_basis(ledger, ledger_path),
            "source_workpackage_complete": source_complete,
            "source_unresolved_instance_count": disposition_counts.get("unresolved", 0),
            "source_unmatched_recognition_object_ids": missing_model_objects,
            "source_block_expansion_failure_count": len(failures),
            "source_pending_duplicate_instance_count": pending_duplicates,
            "source_unresolved_grouping_count": len(grouping["unresolved_groups"]),
            "handoff_id": f"HANDOFF-{profile_id}-{profile_version}",
            "source_id": f"{profile_id}@{profile_version}:{sha256(profile_path)}",
            "consumer_skill": str(profile.get("consumer_skill") or "qto-pipe-networks"),
            "instances": endpoint_instances,
        }
        endpoint_input_path = output / "点位端点交接输入.json"
        endpoint_input_path.write_text(json.dumps(endpoint_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        endpoint_output_path = output / "点位至网络端点交接.json"
        handoff_script = Path(__file__).resolve().with_name("build_network_endpoint_handoff.py")
        handoff_run = subprocess.run(
            [sys.executable, str(handoff_script), "--input", str(endpoint_input_path), "--output", str(endpoint_output_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if handoff_run.returncode != 0:
            fail(f"端点交接生成失败: {handoff_run.stderr.strip() or handoff_run.stdout.strip()}")
        handoff_result = load_json(endpoint_output_path)
        if handoff_result.get("source_workpackage_complete") is not source_complete:
            fail("端点交接的来源完整性与点位主报告不一致")

        artifacts: dict[str, Any] = {
            "neutral_instance_dispositions": str(disposition_path),
            "neutral_instance_dispositions_sha256": sha256(disposition_path),
            "count_input": str(count_input_path),
            "endpoint_handoff_input": str(endpoint_input_path),
            "endpoint_handoff": str(endpoint_output_path),
        }
        if adopted:
            counter_script = Path(__file__).resolve().with_name("count_reconciled_instances.py")
            count_run = subprocess.run(
                [sys.executable, str(counter_script), str(count_input_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if count_run.returncode != 0:
                fail(f"点位计数失败: {count_run.stderr.strip() or count_run.stdout.strip()}")
            count_summary_path = output / "点位数量汇总.json"
            count_summary_path.write_text(count_run.stdout.strip() + "\n", encoding="utf-8")
            artifacts["count_summary"] = str(count_summary_path)

        group_counter: Counter[tuple[str, str, str, str]] = Counter()
        samples: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
        for item in dispositions:
            if item["disposition"] != "unresolved":
                continue
            key = (
                item["block_name"],
                item["layer"],
                str(item.get("parent_block") or ""),
                item["basis"],
            )
            group_counter[key] += 1
            if len(samples[key]) < 3:
                samples[key].append(item["physical_id"])
        unresolved_groups = [
            {
                "block_name": key[0],
                "layer": key[1],
                "parent_block": key[2] or None,
                "basis": key[3],
                "count": count,
                "sample_physical_ids": samples[key],
            }
            for key, count in group_counter.most_common(15)
        ]
        # 全部未决实例一行一个，不截前 15；续作和覆盖台账从这里看全量（登记 R3，点位覆盖台账契约第 2 节）
        ranked = {key: rank for rank, (key, _) in enumerate(group_counter.most_common(), start=1)}
        unresolved_csv = output / "点位未决全量.csv"
        with unresolved_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["group_rank", "group_count", "block_name", "layer", "parent_block", "basis",
                             "physical_id", "x", "y", "coordinate_unit", "instance_path"])
            rows = []
            for item in dispositions:
                if item["disposition"] != "unresolved":
                    continue
                key = (item["block_name"], item["layer"], str(item.get("parent_block") or ""), item["basis"])
                point = item.get("point") or [None, None]
                rows.append([ranked[key], group_counter[key], *key, item["physical_id"], point[0], point[1],
                             item.get("coordinate_unit"), item.get("instance_path")])
            writer.writerows(sorted(rows, key=lambda row: (row[0], row[6])))
        artifacts["unresolved_full_csv"] = str(unresolved_csv)
        artifacts["unresolved_full_csv_sha256"] = sha256(unresolved_csv)
        if side_binding is not None:
            side_csv = output / "点位旁注绑定.csv"
            with side_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["kind", "status", "physical_id", "block_name", "disposition", "rule_ids", "texts",
                                 "text_ids", "distances", "shared_with", "instance_key", "x", "y", "coordinate_unit"])
                for item in sorted((row for row in dispositions if row.get("side_text")), key=lambda row: row["physical_id"]):
                    side = item["side_text"]
                    found = side.get("texts") if "texts" in side else [side]
                    point = item.get("point") or [None, None]
                    writer.writerow(["块", side["status"], item["physical_id"], item["block_name"], item["disposition"],
                                     "；".join(item.get("rule_ids") or []), "；".join(t["text"] for t in found),
                                     "；".join(t["text_id"] for t in found), "；".join(f"{t['distance']:g}" for t in found),
                                     "；".join(side.get("shared_with") or []), item.get("instance_key") or "",
                                     point[0], point[1], item.get("coordinate_unit")])
                for item in side_summary["unclaimed_key_texts"]:
                    writer.writerow(["文字", "文字无块", "", "", "", "；".join(item["rule_ids"]), item["text"], item["text_id"],
                                     "", "", "", item["point"][0], item["point"][1], coordinate_unit])
            artifacts["side_text_binding_csv"] = str(side_csv)
        report = {
            "schema_version": "qto-component-recognition-report/1.0",
            "adapter_version": VERSION,
            "status": "RECOGNITION_REQUIRED" if not rules else ("READY" if source_complete and not handoff_result.get("unresolved_count", 0) else "REVIEW_REQUIRED"),
            "profile": {
                "path": str(profile_path),
                "sha256": sha256(profile_path),
                "profile_id": profile_id,
                "profile_version": profile_version,
            },
            "adjudication_ledger": {
                "path": str(ledger_path),
                "sha256": ledger_sha,
                "ledger_id": ledger["ledger_id"],
                "ledger_version": ledger["ledger_version"],
            },
            "neutral_source": {
                "manifest": str(args.cad_manifest.resolve()),
                "layout_entities": str(layout_path),
                "block_definitions": str(block_path),
                "source_sha256": expected_source,
            },
            "scope": scope,
            "counts": {
                "top_level_insert_count": top_insert_count,
                "scoped_insert_instance_count": len(dispositions),
                "adopted_physical_instance_count": len(adopted),
                "endpoint_port_count": sum(len(item["network_ports"]) for item in endpoint_instances),
                "block_expansion_failure_count": len(failures),
                "object_dispositions": dict(sorted(disposition_counts.items())),
                "disposition_closure_count": len(dispositions),
                "merged_member_count": grouping["merged_member_count"],
                "duplicate_candidate_group_count": duplicate_review["candidate_group_count"],
                "duplicate_pending_instance_count": pending_duplicates,
                "duplicate_excluded_count": duplicate_review["duplicate_excluded_count"],
                "duplicate_scale_status": duplicate_review["scale_status"],
                "near_tolerance_mm_effective": duplicate_review["near_tolerance_mm_effective"],
                "dynamic_state_adjudicated_count": sum(item["released_instance_count"] for item in dynamic_summary),
            },
            "dynamic_state_adjudication": dynamic_summary,
            "empty_block_definitions": empty_summary,
            "scope_extent_incomplete": {
                "basis": "块内无几何的填充（HATCH、SOLID）不计入包围盒，注记（TEXT、MTEXT、ATTDEF、ATTRIB、POINT）按插入点计；"
                         "这样算出的范围不完整，只用于范围裁剪和定页，不参与外形归并；插入点在包内的实例即使范围落在包外也保留",
                "instances_by_note": {cad_geometry.EXTENT_NOTES.get(key, "插入点在包内、按护栏保留"): count
                                      for key, count in sorted(extent_incomplete.items())}},
            "warnings": warnings,
            "disposition_detail": args.disposition_detail,
            **({"side_text_binding": side_binding} if side_binding is not None else {}),
            "instance_grouping": grouping,
            "duplicate_review": duplicate_review,
            "disposition_closure_complete": sum(disposition_counts.values()) == len(dispositions),
            "point_workpackage_complete": source_complete,
            "unmatched_recognition_object_ids": missing_model_objects,
            "endpoint_unresolved_count": handoff_result.get("unresolved_count", 0),
            "unresolved_groups_top15": unresolved_groups,
            "unresolved_group_count": len(group_counter),
            "unresolved_instance_count": sum(group_counter.values()),
            "block_expansion_failures": failures[:50],
            "artifacts": artifacts,
            "business_adoption_allowed": False,
            "recognition_basis": recognition_basis(ledger, ledger_path),
            "meaning": "由本轮有证据模型工作识别或独立裁决建立实例；未决组不阻断已识别实例工作计数，READY不等于人工复核完成",
        }
        report_path = output / "点位识别报告.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    **report["counts"],
                    "report": str(report_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, InputError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
