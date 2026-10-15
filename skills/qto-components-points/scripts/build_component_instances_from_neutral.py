#!/usr/bin/env python3
"""Recognize point/component instances from neutral CAD block facts.

Evidence-backed model decisions can enter working counts through a frozen
profile. Working recognition and independent human adjudication stay separate.
"""

from __future__ import annotations

import argparse
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

import qto_cad_geometry as cad_geometry
from qto_actor_identity import is_model_identity

VERSION = "qto-component-neutral-adapter/1.2.1"
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


def normalize_rules(values: Any, entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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


def choose_rule(block: str, layer: str, parent_block: str, rules: list[dict[str, Any]], object_id: str | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    matches = [
        rule for rule in rules
        if rule["block_re"].search(block)
        and rule["layer_re"].search(layer)
        and rule["parent_re"].search(parent_block)
        and applies_to(rule, object_id)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="从CAD中性块事实构建点位实例、数量输入与端点交接")
    parser.add_argument("--cad-manifest", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--adjudication-ledger", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
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
        rules = normalize_rules(profile.get("rules"), entries)

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
        identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)
        dispositions: list[dict[str, Any]] = []
        adopted: list[dict[str, Any]] = []
        endpoint_instances: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        block_boxes: dict[str, list[float] | None] = {}

        def transformed_box(box, matrix):
            return cad_geometry.transform_bounds(box, matrix)

        def definition_box(name, stack=()):
            return cad_geometry.definition_bounds(name, definitions, origins, max_depth, block_boxes, stack)

        def visit(
            insert: dict[str, Any],
            parent_matrix: tuple[float, ...],
            path: str,
            stack: tuple[str, ...],
            parent_block: str,
            inherited_layer: str,
            suppressed_by: str | None,
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
            local_box = definition_box(block_name)
            # Unsupported INSERT geometry (e.g. MINSERT) has no complete
            # instance envelope; the first cell cannot justify scope pruning.
            world_box3d = transformed_box(local_box, matrix) if local_box and not insert.get("geometry_issues") else None
            world_box = [world_box3d[0], world_box3d[1], world_box3d[3], world_box3d[4]] if world_box3d else None
            in_scope = bbox_intersects({"bbox": world_box}, bounds) if world_box else True
            if not in_scope:
                return  # only complete, conservative bounds allow pruning
            if insert.get("geometry_issues"):
                dispositions.append({"object_id": f"PT-{path}", "physical_id": f"PT-{path}", "source_record_id": source_record_id,
                                     "block_name": block_name, "layer": effective_layer, "parent_block": parent_block or None,
                                     "point": [value * factor for value in world_point], "coordinate_unit": coordinate_unit,
                                     "disposition": "unresolved", "basis": "; ".join(insert["geometry_issues"]), "rule_ids": [], "instance_path": path})
                failures.append({"kind": "unsupported_insert_geometry", "instance_path": path, "issues": insert["geometry_issues"]})
                return
            physical_id = f"PT-{path}"
            next_suppressed = suppressed_by

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
                    "scope_location_basis": "transformed_geometry_extent" if world_box else "insertion_anchor_fallback_requires_review",
                    "point_basis": "insertion_anchor_not_verified_connection_port",
                    "disposition": "unresolved",
                    "basis": "未命中冻结点位识别规则",
                    "rule_ids": [],
                }
                if suppressed_by:
                    base.update(
                        {
                            "disposition": "not_applicable",
                            "basis": f"由父实例 {suppressed_by} 承担或排除专业身份，子块不单独计数",
                        }
                    )
                else:
                    rule, conflicts = choose_rule(block_name, effective_layer, parent_block, rules, physical_id)
                    if conflicts:
                        base.update({"basis": "同优先级识别规则冲突", "rule_ids": conflicts})
                    elif rule:
                        decision = rule["decision"]
                        base.update(
                            {
                                "rule_ids": [rule["rule_id"]],
                                "adjudication_id": rule.get("adjudication_id"),
                                "recognition_record": rule.get("authority"),
                                "basis": str(rule.get("basis") or f"冻结识别规则 {rule['rule_id']}"),
                            }
                        )
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
                            next_suppressed = physical_id
                        elif decision == "exclude":
                            base["disposition"] = "excluded"
                            next_suppressed = physical_id
                        elif decision == "container":
                            base["disposition"] = "not_applicable"
                        else:
                            base["disposition"] = "unresolved"
                dispositions.append(base)

            if not block_name or block_name not in definitions:
                if in_scope and not suppressed_by:
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

        physical_ids = [item["physical_id"] for item in adopted]
        if len(physical_ids) != len(set(physical_ids)):
            fail("点位识别结果出现重复 physical_id")
        disposition_counts = Counter(item["disposition"] for item in dispositions)
        if sum(disposition_counts.values()) != len(dispositions):
            fail("点位实例处置数量不守恒")

        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        disposition_path = output / "点位中性实例处置清单.jsonl"
        with disposition_path.open("w", encoding="utf-8") as handle:
            for item in sorted(dispositions, key=lambda value: value["physical_id"]):
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        count_input = {
            "schema_version": "qto-reconciled-instance-input/1.0",
            "reconciled_instances": adopted,
            "recognition_basis": recognition_basis(ledger, ledger_path),
        }
        count_input_path = output / "点位唯一实例计数输入.json"
        count_input_path.write_text(json.dumps(count_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 模型识别台账列出、块适配器却没有承接的对象（非块图符、范围外等）是来源缺口：
        # 主报告与端点交接用同一个完整性判定，缺口 ID 随交接传给消费方。
        model_object_ids = {oid for entry in entries.values() if entry.get("decision_origin") == "model" for oid in entry["object_ids"]}
        missing_model_objects = sorted(model_object_ids - {item["physical_id"] for item in dispositions})
        source_complete = bool(rules) and not disposition_counts.get("unresolved", 0) and not failures and not missing_model_objects
        endpoint_input = {
            "schema_version": "qto-network-endpoint-handoff-input/1.1",
            "recognition_basis": recognition_basis(ledger, ledger_path),
            "source_workpackage_complete": source_complete,
            "source_unresolved_instance_count": disposition_counts.get("unresolved", 0),
            "source_unmatched_recognition_object_ids": missing_model_objects,
            "source_block_expansion_failure_count": len(failures),
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
        report = {
            "schema_version": "qto-component-recognition-report/1.0",
            "adapter_version": VERSION,
            "status": "RECOGNITION_REQUIRED" if not rules else ("READY" if not disposition_counts.get("unresolved", 0) and not failures and not handoff_result.get("unresolved_count", 0) and not missing_model_objects else "REVIEW_REQUIRED"),
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
            },
            "disposition_closure_complete": sum(disposition_counts.values()) == len(dispositions),
            "point_workpackage_complete": source_complete,
            "unmatched_recognition_object_ids": missing_model_objects,
            "endpoint_unresolved_count": handoff_result.get("unresolved_count", 0),
            "unresolved_groups_top15": unresolved_groups,
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
