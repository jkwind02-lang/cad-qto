#!/usr/bin/env python3
"""Build pipe inference input from neutral CAD facts and a frozen profile.

Model drawing decisions or independent adjudications bind the frozen profile.
Working recognition is not human approval; uncovered objects remain visible.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from model_recognition_basis import SCHEMA as MODEL_LEDGER_SCHEMA, load_working_entries, applies_to, recognition_basis, validate_working_basis

import qto_cad_geometry as cad_geometry
import qto_symbol_geometry as symbol_geometry
from qto_actor_identity import is_model_identity
from qto_cad_source_contract import (check_unit_factor, verify_source_file, unit_known, verify_unit_declaration_texts,
                                     verify_unit_corroboration, unit_tokens, UNIT_APPLIES_TO)

from qto_pipe_topology import RouteIndex, route_distance, project_route, geometry_parts, scale_route_geometry
from pipe_callout_stacks import targets as binding_targets, bind_stack

VERSION = "qto-pipe-neutral-adapter/1.9.1"
PROFILE_SCHEMA = "qto-pipe-recognition-profile/1.1"
LEDGER_SCHEMA = "qto-project-adjudication-ledger/1.0"
LINE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE"}
TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
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
        return re.compile(text(value, label))
    except re.error as exc:
        fail(f"{label} 正则表达式无效: {exc}")


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


def bbox_intersects(row: dict[str, Any], bounds: dict[str, float] | None) -> bool:
    """An INSERT is in scope when its transformed definition extent meets the bounds (scope_extent_bbox, the same
    test as the point adapter's scope_of; registration 20261017-0031); without that extent its own bbox decides."""
    if bounds is None:
        return True
    extent = row.get("scope_extent_bbox") if row.get("entity_type") == "INSERT" else None
    use_extent = isinstance(extent, list) and len(extent) >= 4
    bbox = extent if use_extent else row.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4 and all(isinstance(n, (int, float)) and math.isfinite(n) for n in bbox[:4]):
        if not (bbox[2] < bounds["xmin"] or bbox[0] >= bounds["xmax_exclusive"] or bbox[3] < bounds["ymin"] or bbox[1] >= bounds["ymax_exclusive"]):
            return True
        if use_extent and row.get("scope_extent_notes"):
            # K3 guard: an extent without fills or with labels at their insertion point prunes only when the
            # insertion point is outside as well
            x, y = row.get("x"), row.get("y")
            return (all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (x, y))
                    and bounds["xmin"] <= x < bounds["xmax_exclusive"] and bounds["ymin"] <= y < bounds["ymax_exclusive"])
        return False
    return True  # incomplete geometry is unresolved, not silently excluded


def scaled_point(value: Any, factor: float, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) not in (2, 3):
        fail(f"{label} 必须是二维或三维坐标")
    return [finite(item, label) * factor for item in value]


def row_point(row: dict[str, Any], factor: float) -> list[float] | None:
    if isinstance(row.get("x"), (int, float)) and isinstance(row.get("y"), (int, float)):
        result = [float(row["x"]) * factor, float(row["y"]) * factor]
        if isinstance(row.get("z"), (int, float)):
            result.append(float(row["z"]) * factor)
        return result
    bbox = row.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        return [((float(bbox[0]) + float(bbox[2])) / 2.0) * factor, ((float(bbox[1]) + float(bbox[3])) / 2.0) * factor]
    return None


def point_segment_distance(point: list[float], start: list[float], end: list[float]) -> float:
    px, py = point[:2]
    ax, ay = start[:2]
    bx, by = end[:2]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-24:
        return math.hypot(px - ax, py - ay)
    ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + ratio * dx), py - (ay + ratio * dy))


def normalized_rules(values: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        fail(f"{label} 必须是数组")
    result = []
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            fail(f"{label}[{index}] 必须是对象")
        item = dict(raw)
        item["rule_id"] = text(item.get("rule_id"), f"{label}[{index}].rule_id")
        item["layer_re"] = compile_pattern(item.get("layer_pattern", ".*"), f"{item['rule_id']}.layer_pattern")
        item["priority"] = int(item.get("priority", 0))
        result.append(item)
    return result


def load_adjudication_ledger(path: Path, expected_source_sha256: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    ledger = load_json(path)
    if ledger.get("schema_version") == MODEL_LEDGER_SCHEMA:
        return load_working_entries(ledger, path, expected_source_sha256)
    if ledger.get("schema_version") != LEDGER_SCHEMA:
        fail(f"adjudication-ledger.schema_version 必须为 {LEDGER_SCHEMA}")
    text(ledger.get("ledger_id"), "ledger_id")
    text(ledger.get("ledger_version"), "ledger_version")
    ledger_source = text(ledger.get("source_sha256"), "ledger.source_sha256").lower()
    if ledger_source != expected_source_sha256:
        fail("项目裁决台账绑定的来源哈希与识别配置不一致")
    raw_entries = ledger.get("entries")
    if not isinstance(raw_entries, list):
        fail("ledger.entries 必须是数组")
    entries: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            fail(f"ledger.entries[{index}] 必须是对象")
        entry = dict(raw)
        adjudication_id = text(entry.get("adjudication_id"), f"ledger.entries[{index}].adjudication_id")
        if adjudication_id in entries:
            fail(f"裁决ID重复: {adjudication_id}")
        if entry.get("status") != "active":
            fail(f"{adjudication_id}.status 必须为 active")
        source_type = text(entry.get("confirmation_source"), f"{adjudication_id}.confirmation_source")
        if source_type not in CONFIRMATION_SOURCES:
            fail(f"{adjudication_id}.confirmation_source 不是允许的独立确认来源")
        confirmed_by = text(entry.get("confirmed_by"), f"{adjudication_id}.confirmed_by")
        if is_model_identity(confirmed_by):
            fail(f"{adjudication_id}.confirmed_by 不能是模型或助手")
        text(entry.get("source_id"), f"{adjudication_id}.source_id")
        evidence_ids = entry.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(value, str) or not value.strip() for value in evidence_ids):
            fail(f"{adjudication_id}.evidence_ids 必须是非空文本数组")
        entries[adjudication_id] = entry
    return ledger, entries


def bind_rule_authority(rule: dict[str, Any], label: str, entries: dict[str, dict[str, Any]], decision: str) -> None:
    if decision == "unresolved":
        return
    adjudication_id = text(rule.get("adjudication_id"), f"{label}.adjudication_id")
    entry = entries.get(adjudication_id)
    if not entry:
        fail(f"{label}.adjudication_id 未在项目裁决台账中找到")
    if text(entry.get("subject_id"), f"{adjudication_id}.subject_id") != rule["rule_id"]:
        fail(f"{adjudication_id} 未绑定当前规则 {rule['rule_id']}")
    if text(entry.get("decision"), f"{adjudication_id}.decision") != decision:
        fail(f"{adjudication_id} 的裁决与规则决定不一致")
    owner = text(entry.get("professional_owner"), f"{adjudication_id}.professional_owner")
    if owner != "qto-pipe-networks":
        fail(f"{adjudication_id}.professional_owner 必须为 qto-pipe-networks")
    rule["authority"] = dict(entry)


def explicit_target_contexts(raw, target_ids, rule, source_sha, space, segments):
    """A shared label may seed separate systems only with per-target drawing evidence."""
    if not isinstance(raw, list) or not raw:
        fail(f"{rule['rule_id']} 跨系统共用标注须逐目标 shared_annotation_targets/segment_contexts")
    contexts = {}
    authority_evidence = set(rule.get('authority', {}).get('evidence_ids', []))
    for item in raw:
        if not isinstance(item, dict):
            fail('共用标注目标依据须为对象')
        sid = text(item.get('segment_id'), 'shared.segment_id')
        if sid in contexts:
            fail('共用标注目标依据重复')
        target = segments.get(sid)
        eids = item.get('evidence_ids')
        if (target is None or item.get('system') != target['system'] or item.get('space_id') != space
                or item.get('source_sha256') != source_sha or not isinstance(eids, list) or not eids
                or any(not isinstance(e, str) or e not in authority_evidence for e in eids)):
            fail(f'共用标注逐目标来源/系统/空间/证据不匹配: {sid}')
        text(item.get('basis'), 'shared.basis')
        contexts[sid] = dict(item)
    if set(contexts) != set(target_ids):
        fail('共用标注逐目标依据必须完整覆盖显式绑定，不得漏列或增列')
    return contexts


def choose_layer_rule(layer: str, rules: list[dict[str, Any]], object_id: str | None = None,
                      entity_type: str | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    matches = [item for item in rules if item["layer_re"].search(layer) and applies_to(item, object_id)
               and (entity_type is None or entity_type in {str(v).upper() for v in item.get("allowed_entity_types", sorted(LINE_TYPES))})]
    if not matches:
        return None, []
    best_priority = max(item["priority"] for item in matches)
    best = sorted((item for item in matches if item["priority"] == best_priority), key=lambda item: item["rule_id"])
    signatures = {(item.get("decision", "unresolved"), item.get("system")) for item in best}
    if len(signatures) > 1:
        return None, [item["rule_id"] for item in best]
    return best[0], []


def apply_insert_quantity_ownership(insert_rows, block_rules, root_consumed_ids,
                                    object_dispositions, candidates, candidate_meta,
                                    explicit_dispositions, derived_roots, factor,
                                    context_insert_rows):
    """A resolved symbol instance owns its descendants, before length auditing.

    A layer rule may offer curve candidates but cannot reverse a parent block's
    exclusion, or count symbol outlines in addition to an adopted accessory.
    Unresolved/unmatched containers do not claim their engineering contents.
    """
    owners = []
    for row in context_insert_rows:
        oid = str(row.get("record_id") or "")
        outside_scope = oid not in object_dispositions
        disposition = object_dispositions.get(oid, {})
        if oid not in root_consumed_ids:
            matches = [rule for rule in block_rules
                       if rule["layer_re"].search(str(row.get("layer") or ""))
                       and rule["block_re"].search(str(row.get("block_name") or ""))
                       and applies_to(rule, oid)]
            if not matches:
                continue
            priority = max(rule["priority"] for rule in matches)
            best = sorted((rule for rule in matches if rule["priority"] == priority),
                          key=lambda rule: rule["rule_id"])
            if len({(rule.get("decision", "unresolved"), rule.get("system")) for rule in best}) > 1:
                continue
            rule = best[0]
            decision = rule.get("decision", "unresolved")
            if decision not in {"include", "exclude"}:
                continue
            # An exclusion remains valid for in-scope children when the
            # INSERT anchor lies outside the window. Do not adopt an outside
            # accessory or invent its quantity from this contextual record.
            if outside_scope and decision != "exclude":
                continue
            if decision == "include" and not row_point(row, factor):
                continue
            disposition.update(disposition="adopted" if decision == "include" else "excluded",
                               basis=str(rule.get("basis") or f"冻结识别规则 {rule['rule_id']}"),
                               rule_ids=[rule["rule_id"]], adjudication_id=rule.get("adjudication_id"))
        if disposition["disposition"] not in {"adopted", "excluded"}:
            continue
        # Expanded INSERT record IDs have a CADINST- prefix; instance_path
        # itself uses the canonical root/block/child path without that prefix.
        key = oid[len("CADINST-"):] if oid.startswith("CADINST-") else oid
        owners.append((key, oid, disposition.copy()))
    owner_by_path = {value[0]: value for value in owners}
    suppressed = set()
    for oid, item in object_dispositions.items():
        path = str(item.get("instance_path") or "")
        owner = None
        prefix = ""
        for part in path.split("/"):
            prefix = prefix + "/" + part if prefix else part
            owner = owner_by_path.get(prefix)
            if owner is not None:
                break
        if owner is None:
            continue
        _, parent_id, parent = owner
        suppressed.add(oid)
        item.update(disposition="excluded" if parent["disposition"] == "excluded" else "not_applicable",
                    basis="已处置父块实例控制其后代；符号图元不另形成管长或附件数量",
                    rule_ids=parent["rule_ids"], adjudication_id=parent.get("adjudication_id"),
                    quantity_owner_object_id=parent_id)
    removed = {sid for sid, meta in candidate_meta.items()
               if str(meta["row"].get("record_id") or "") in suppressed}
    for sid in removed:
        candidates.pop(sid, None)
        candidate_meta.pop(sid, None)
    explicit_dispositions[:] = [item for item in explicit_dispositions if item["object_id"] not in removed]
    derived_roots[:] = [item for item in derived_roots if item["root_id"][len("ROOT-"):] not in suppressed]
    return [row for row in insert_rows if row["record_id"] not in suppressed]


def attach_recognition_records(dispositions, rules, detail):
    """Keep full records in the bound ledger; compact rows have fixed-size references."""
    by_id = {rule["rule_id"]: rule for rule in rules if isinstance(rule.get("authority"), dict)}
    for item in dispositions.values():
        selected = [by_id[rid] for rid in item["rule_ids"] if rid in by_id]
        refs = []
        for rule in selected:
            if "_record_ref" not in rule:
                authority = rule["authority"]
                payload = json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
                rule["_record_ref"] = {
                    "rule_id": rule["rule_id"], "adjudication_id": authority.get("adjudication_id"),
                    "subject_id": authority.get("subject_id"), "decision": authority.get("decision"),
                    "decision_origin": authority.get("decision_origin") or "independent",
                    "object_id_count": len(authority["object_ids"]) if isinstance(authority.get("object_ids"), list) else None,
                    "entry_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                }
            refs.append(rule["_record_ref"])
        item["recognition_record_refs"] = refs
        if detail == "full":
            item["recognition_records"] = [rule["authority"] for rule in selected]


def save_pending_recognition(output, dispositions, scope, working_basis, reason, rules=(), detail="compact"):
    """A not-yet-recognized work area is a checkpoint, not zero pipe quantity."""
    attach_recognition_records(dispositions, rules, detail)
    path = output / "管网中性对象处置清单.jsonl"
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in dispositions.values()), encoding="utf-8")
    report = {
        "schema_version": "qto-pipe-recognition-report/1.0", "status": "RECOGNITION_REQUIRED",
        "scope": scope, "recognition_basis": working_basis, "business_adoption_allowed": False,
        "disposition_detail": detail,
        "counts": {"scoped_rows": len(dispositions), "object_dispositions": dict(Counter(item["disposition"] for item in dispositions.values()))},
        "reason": reason, "quantity": None,
        "next_action": "模型回到图例、系统图及局部图面继续识别；只有资料确缺才请求用户补充。此处不是首轮算量完成。",
        "artifacts": {"neutral_object_dispositions": str(path)},
    }
    (output / "管网识别报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "识图待续说明.md").write_text("# 识图尚未完成\n\n" + reason + "。未形成管道工程量，不作零值。\n\n" + report["next_action"] + "\n", encoding="utf-8")


def source_from_manifest(manifest_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != "qto-cad-neutral-evidence/1.0":
        fail("cad-manifest schema_version 必须为 qto-cad-neutral-evidence/1.0")
    if manifest.get("geometry_schema_version") != cad_geometry.GEOMETRY_SCHEMA:
        fail("CAD旧缓存缺少完整WCS几何，请用新版中性提取器重新生成；不得继承旧曲线测长")
    if manifest.get("source_identity_issues"):
        fail("CAD源实体身份存在冲突，不能按句柄认定管网对象；先核验原图并重新取证")
    verify_source_file(manifest.get("authoritative_source"), "权威原图")
    verify_source_file(manifest.get("exchange_dxf"), "DXF交换副本")
    if manifest.get('exchange_conversion'):
        verify_source_file(manifest['exchange_conversion'], '转换记录')
    artifact = (manifest.get("artifacts") or {}).get("layout_entities")
    if not isinstance(artifact, dict):
        fail("CAD清单缺少 artifacts.layout_entities")
    stream_path = Path(text(artifact.get("path"), "layout_entities.path")).resolve()
    if not stream_path.is_file() or sha256(stream_path) != str(artifact.get("sha256") or "").lower():
        fail("CAD布局图元文件缺失或哈希不一致")
    block_artifact = (manifest.get("artifacts") or {}).get("block_definitions")
    if not isinstance(block_artifact, dict):
        fail("CAD清单缺少 artifacts.block_definitions")
    block_path = Path(text(block_artifact.get("path"), "block_definitions.path")).resolve()
    if not block_path.is_file() or sha256(block_path) != str(block_artifact.get("sha256") or "").lower():
        fail("CAD块定义图元文件缺失或哈希不一致")
    return stream_path, block_path, manifest


def matrix_for_insert(row: dict[str, Any], origin: list[float]) -> tuple[float, ...]:
    return cad_geometry.matrix_for_insert(row, origin)


def compose_matrix(parent: tuple[float, ...], child: tuple[float, ...]) -> tuple[float, ...]:
    return cad_geometry.compose_matrix(parent, child)


def transform_point(value: Any, matrix: tuple[float, ...]) -> list[float] | None:
    return cad_geometry.transform_point(value, matrix)


def transform_row(row: dict[str, Any], matrix: tuple[float, ...], instance_path: str, target_layout: str) -> dict[str, Any]:
    return cad_geometry.transform_row(row, matrix, instance_path, target_layout)


def empty_block_names(manifest: dict[str, Any], definitions: dict[str, Any]) -> set[str]:
    """Definitions that exist with no entities (serialized, direct_entity_count 0) are not missing (registration
    20261016-2359): they draw nothing and expand to nothing. Manifests before 0.3.2 carry no serialized flag."""
    return {str(item["block_name"]) for item in manifest.get("blocks", [])
            if isinstance(item, dict) and item.get("block_name") and cad_geometry.is_empty_definition(item)
            and str(item["block_name"]) not in definitions}


def drop_zero_length_parts(geometry: Any) -> tuple[Any, list[int]]:
    """Repeated vertices give zero-length parts (registration 20261017-0026). Dropping them merges the adjacent
    vertices, so one bad vertex no longer fails the whole frame. Returns the geometry and the dropped part indexes;
    the geometry is None when every part had zero length."""
    if not isinstance(geometry, dict) or not isinstance(geometry.get("parts"), list):
        return geometry, []
    kept, dropped = [], []
    for index, part in enumerate(geometry["parts"]):
        try:
            zero = cad_geometry.measure_part(part)[0] <= 0
        except (KeyError, TypeError, ValueError):
            zero = False   # malformed parts are judged by the route preparation, as before
        if zero:
            dropped.append(index)
        else:
            kept.append(part)
    if not dropped:
        return geometry, []
    return ({**geometry, "parts": kept} if kept else None), dropped


def expanded_manifest_rows(layout_path: Path, block_path: Path, manifest: dict[str, Any],
                           max_depth: int, failures: list[dict[str, Any]],
                           target_layout: str | None = None, scope_bounds: dict[str, float] | None = None) -> Iterable[dict[str, Any]]:
    definitions: dict[str, list[dict[str, Any]]] = {}
    for row in load_stream(block_path):
        definitions.setdefault(str(row.get("parent_block") or ""), []).append(row)
    origins = {str(item["block_name"]): item.get("origin", [0., 0., 0.]) for item in manifest.get("blocks", []) if isinstance(item, dict) and item.get("block_name")}
    empty = empty_block_names(manifest, definitions)
    extent_cache = {}

    def mark_extent(target, insert, parent_matrix):
        """scope_extent_bbox: the INSERT's transformed definition extent (2D), the scope test the point adapter's
        scope_of uses; absent when the definition is missing, empty or unsupported (then the anchor decides).
        An empty definition is marked so the row is a hint, not an expansion failure or an unrecognised object."""
        name = str(insert.get("block_name") or "")
        if name in empty:
            target["empty_block_definition"] = True
            return target
        if insert.get("geometry_issues") or name not in definitions:
            return target
        local_box, notes = cad_geometry.definition_extent(name, definitions, origins, max_depth, extent_cache, (), empty)
        box = cad_geometry.transform_bounds(local_box, compose_matrix(parent_matrix, matrix_for_insert(insert, origins.get(name, [0., 0., 0.]))))
        if box is not None:
            target["scope_extent_bbox"] = [box[0], box[1], box[3], box[4]]
            if notes:
                target["scope_extent_notes"] = cad_geometry.extent_note_labels(notes)
        return target

    def expand(insert, parent_matrix, path, stack, layout, inherited_layer):
        name = str(insert.get("block_name") or "")
        if name in empty:
            return   # draws nothing; the INSERT row itself carries empty_block_definition
        if not name or name not in definitions:
            failures.append({"object_id": insert.get("record_id"), "kind": "missing_block_definition", "block_name": name, "instance_path": path})
            return
        if name in stack or len(stack) >= max_depth:
            failures.append({"object_id": insert.get("record_id"), "kind": "cyclic_block_reference" if name in stack else "block_depth_limit", "instance_path": path})
            return
        if insert.get("geometry_issues"):
            failures.append({"object_id": insert.get("record_id"), "kind": "unsupported_insert_geometry", "instance_path": path, "issues": insert["geometry_issues"]})
            return
        matrix = compose_matrix(parent_matrix, matrix_for_insert(insert, origins.get(name, [0., 0., 0.])))
        local_box, notes = cad_geometry.definition_extent(name, definitions, origins, max_depth, extent_cache, (), empty)
        box = cad_geometry.transform_bounds(local_box, matrix)
        x, y = insert.get("x"), insert.get("y")
        anchor = (transform_point([x, y, insert.get("z") or 0.0], parent_matrix)
                  if notes and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)) else None)
        if not cad_geometry.extent_in_scope(box, notes, anchor, scope_bounds):
            return
        layer = str(insert.get("layer") or "")
        effective_layer = inherited_layer if layer in {"", "0"} else layer
        for row in definitions[name]:
            child_path = f"{path}/{name}"
            expanded = transform_row(row, matrix, child_path, layout)
            if expanded.get("layer") in {"", "0"}:
                expanded["source_layer"] = expanded.get("layer")
                expanded["layer"] = effective_layer
            if expanded.get("entity_type") == "INSERT":
                mark_extent(expanded, row, matrix)
            yield expanded
            if expanded.get("entity_type") == "INSERT":
                yield from expand(row, matrix, f"{child_path}/{row.get('record_id')}", (*stack, name), layout, effective_layer)
    for row in load_stream(layout_path):
        layout = str(row.get("layout") or "Model")
        if target_layout is not None and layout != target_layout:
            continue
        if row.get("entity_type") == "INSERT":
            mark_extent(row, row, cad_geometry.IDENTITY)
        yield row
        if row.get("entity_type") == "INSERT":
            yield from expand(row, cad_geometry.IDENTITY, str(row.get("record_id") or "LAYOUT-INSERT"), (), layout, str(row.get("layer") or ""))


PRESCOPED_SCHEMA = "qto-pipe-prescoped-rows/1.0"


def load_prescoped_rows(manifest_path: Path, manifest: dict[str, Any], profile_path: Path, profile: dict[str, Any],
                        max_depth: int) -> tuple[Iterable[dict[str, Any]], dict[str, Any]]:
    """Rows already expanded and cut to this frame by the batch entry, bound by hash.

    The batch entry reads the large neutral streams once for all frames and writes,
    per frame, the in-scope expanded rows plus the out-of-scope context this adapter
    still needs (ancestor INSERTs of in-scope children, unit statement texts and
    unit corroboration objects). The header must match the manifest artifacts and
    the frozen scope, so a prescoped file cannot be reused for another drawing or window.
    """
    ref = profile.get("prescoped_rows")
    if not isinstance(ref, dict):
        fail("profile.prescoped_rows 必须是对象")
    raw_path = Path(text(ref.get("path"), "prescoped_rows.path"))
    path = (raw_path if raw_path.is_absolute() else profile_path.parent / raw_path).resolve()
    if not path.is_file() or sha256(path) != text(ref.get("sha256"), "prescoped_rows.sha256").lower():
        fail("预扫描图元文件缺失或哈希与识别配置不一致")
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
    try:
        header = json.loads(first)
    except json.JSONDecodeError as exc:
        fail(f"预扫描图元文件首行无效: {exc}")
    if not isinstance(header, dict) or header.get("row_type") != "prescoped_header" or header.get("schema_version") != PRESCOPED_SCHEMA:
        fail(f"预扫描图元文件首行必须是 {PRESCOPED_SCHEMA} 头")
    artifacts = manifest.get("artifacts") or {}
    expected = {
        "cad_manifest_sha256": sha256(manifest_path),
        "layout_entities_sha256": str((artifacts.get("layout_entities") or {}).get("sha256") or "").lower(),
        "block_definitions_sha256": str((artifacts.get("block_definitions") or {}).get("sha256") or "").lower(),
    }
    for key, value in expected.items():
        if str(header.get(key) or "").lower() != value:
            fail(f"预扫描图元文件不是由当前CAD证据生成（{key} 不一致）")
    scope = profile.get("scope") or {}
    if header.get("layout") != str(scope.get("layout", "Model")):
        fail("预扫描图元文件的布局与识别配置范围不一致")
    if header.get("bounds") != scope.get("bounds"):
        fail("预扫描图元文件的坐标框与识别配置范围不一致")
    if header.get("max_depth") != max_depth:
        fail("预扫描图元文件的块展开深度与识别配置不一致")
    failures = header.get("block_expansion_failures")
    if not isinstance(failures, list) or not isinstance(header.get("source_total_rows"), int) or not isinstance(header.get("row_count"), int):
        fail("预扫描图元文件头缺少块展开失败、全图行数或行数")

    def rows() -> Iterable[dict[str, Any]]:
        count = 0
        with path.open("r", encoding="utf-8") as stream:
            stream.readline()
            for line_number, line in enumerate(stream, 2):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    fail(f"{path}:{line_number} JSON 无效: {exc}")
                if not isinstance(value, dict):
                    fail(f"{path}:{line_number} 必须是对象")
                count += 1
                yield value
        if count != header["row_count"]:
            fail("预扫描图元文件行数与文件头不一致")
    return rows(), header


def main() -> int:
    parser = argparse.ArgumentParser(description="从CAD中性事实构建管网对象审计和1.2推算输入")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cad-manifest", type=Path)
    source.add_argument("--neutral-stream", type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--adjudication-ledger", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--endpoint-handoff", type=Path)
    parser.add_argument("--inference-output", type=Path, help="提供时继续运行通用管网推算器")
    parser.add_argument("--disposition-detail", choices=("compact", "full"), default="compact",
                        help="处置清单默认引用识别记录；full逐对象嵌入整条记录，可能明显增大体积")
    args = parser.parse_args()

    try:
        profile_path = args.profile.resolve()
        profile = load_json(profile_path)
        if profile.get("schema_version") != PROFILE_SCHEMA:
            fail(f"profile.schema_version 必须为 {PROFILE_SCHEMA}")
        profile_id = text(profile.get("profile_id"), "profile_id")
        profile_version = text(profile.get("profile_version"), "profile_version")
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        block_expansion_failures: list[dict[str, Any]] = []
        empty_block_hits: Counter = Counter()
        degenerate_segments: list[dict[str, Any]] = []
        prescoped_header: dict[str, Any] | None = None
        if args.cad_manifest:
            stream_path, block_path, cad_manifest = source_from_manifest(args.cad_manifest.resolve())
            source_identity = cad_manifest.get("authoritative_source", {})
            expansion = profile.get("block_expansion", {})
            if not isinstance(expansion, dict):
                fail("profile.block_expansion 必须是对象")
            raw_max_depth = expansion.get("max_depth", 16)
            if isinstance(raw_max_depth, bool) or not isinstance(raw_max_depth, int) or raw_max_depth < 1:
                fail("block_expansion.max_depth 必须是正整数")
            if profile.get("prescoped_rows") is not None:
                rows, prescoped_header = load_prescoped_rows(args.cad_manifest.resolve(), cad_manifest, profile_path, profile, raw_max_depth)
                block_expansion_failures.extend(prescoped_header["block_expansion_failures"])
            else:
                rows = expanded_manifest_rows(
                    stream_path,
                    block_path,
                    cad_manifest,
                    raw_max_depth,
                    block_expansion_failures,
                    target_layout=str((profile.get("scope") or {}).get("layout", "Model")),
                    scope_bounds=(profile.get("scope") or {}).get("bounds"),
                )
        else:
            stream_path = args.neutral_stream.resolve()
            cad_manifest = None
            if not stream_path.is_file():
                fail("neutral stream 不存在")
            source_identity = {"path": str(stream_path), "sha256": sha256(stream_path)}
            rows = load_stream(stream_path)
        expected_source_sha = text(profile.get("expected_source_sha256"), "profile.expected_source_sha256").lower()
        if str(source_identity.get("sha256") or "").lower() != expected_source_sha:
            fail("识别配置绑定的来源哈希与当前CAD证据不一致")
        entity_source_sha = expected_source_sha if cad_manifest else text(profile.get("expected_entity_source_sha256", expected_source_sha), "expected_entity_source_sha256").lower()
        ledger_path = args.adjudication_ledger.resolve()
        ledger_sha = sha256(ledger_path)
        ledger_ref = profile.get("adjudication_ledger")
        if not isinstance(ledger_ref, dict):
            fail("profile.adjudication_ledger 必须是对象")
        expected_ledger_sha = text(ledger_ref.get("sha256"), "profile.adjudication_ledger.sha256").lower()
        if ledger_sha != expected_ledger_sha:
            fail("识别配置绑定的项目裁决台账哈希与当前文件不一致")
        ledger, ledger_entries = load_adjudication_ledger(ledger_path, expected_source_sha)
        working_basis = recognition_basis(ledger, ledger_path)
        protected_paths = [profile_path, ledger_path, stream_path]
        protected_paths += [(ledger_path.parent / e["path"]).resolve() for e in ledger.get("evidence", [])]
        if args.cad_manifest:
            protected_paths += [args.cad_manifest.resolve(), block_path]
            protected_paths += [Path(cad_manifest[k]["path"]).resolve() for k in ("authoritative_source", "exchange_dxf", "exchange_conversion") if cad_manifest.get(k)]
        if profile.get("prescoped_rows"):
            protected_paths.append((profile_path.parent / profile["prescoped_rows"]["path"]).resolve())
        if args.endpoint_handoff:
            protected_paths.append(args.endpoint_handoff.resolve())
        planned_outputs = [output / name for name in ("管网对象选择审计输入.json", "管网对象选择审计.json", "管网推算输入.json", "管网中性对象处置清单.jsonl", "管网识别报告.json", "识图待续说明.md")]
        if args.inference_output:
            planned_outputs.append(args.inference_output.resolve())
        def same_path(a, b):
            return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))
        if any(same_path(a, b) for a in planned_outputs for b in protected_paths):
            fail("输出路径与输入/来源文件别名冲突")
        if any(same_path(a, b) for i, a in enumerate(planned_outputs) for b in planned_outputs[i+1:]):
            fail("输出文件之间路径别名冲突")
        if text(ledger_ref.get("ledger_id"), "profile.adjudication_ledger.ledger_id") != ledger["ledger_id"]:
            fail("识别配置与项目裁决台账ID不一致")
        if text(ledger_ref.get("ledger_version"), "profile.adjudication_ledger.ledger_version") != ledger["ledger_version"]:
            fail("识别配置与项目裁决台账版本不一致")

        scope = profile.get("scope")
        if not isinstance(scope, dict):
            fail("profile.scope 必须是对象")
        layout = text(scope.get("layout", "Model"), "scope.layout")
        raw_bounds = scope.get("bounds")
        bounds = None
        if raw_bounds is not None:
            if not isinstance(raw_bounds, dict):
                fail("scope.bounds 必须是对象")
            bounds = {key: finite(raw_bounds.get(key), f"scope.bounds.{key}") for key in ("xmin", "xmax_exclusive", "ymin", "ymax_exclusive")}
            if bounds["xmin"] >= bounds["xmax_exclusive"] or bounds["ymin"] >= bounds["ymax_exclusive"]:
                fail("scope.bounds 范围无效")
        unit = profile.get("unit")
        if not isinstance(unit, dict):
            fail("profile.unit 必须是对象")
        factor = finite(unit.get("factor_to_output"), "unit.factor_to_output")
        if factor <= 0:
            fail("unit.factor_to_output 必须大于0")
        output_unit = text(unit.get("output_unit"), "unit.output_unit")
        unit_source_id = text(unit.get("source_id"), "unit.source_id")
        source_raw_unit = (cad_manifest.get("drawing_unit") or {}).get("raw_unit") if cad_manifest else unit.get("raw_unit")
        unit_declaration = None
        declared_unit = None
        pending_unit_texts: dict[str, dict[str, Any] | None] | None = None
        pending_corroboration: dict[str, dict[str, Any] | None] = {}
        if source_raw_unit is not None and not unit_known(source_raw_unit):
            # INSUNITS 0 drawings carry no unit; only a model-reviewed unit statement
            # written on this drawing may supply it, never a layer or drafting habit.
            unit_declaration = unit.get("declaration")
            if not isinstance(unit_declaration, dict):
                fail(f"CAD中性事实单位为 {source_raw_unit!r}；须在 unit.declaration 引用本图单位说明文字并绑定识别记录，单位未知不得出长度")
            declared_unit = text(unit_declaration.get("raw_unit"), "unit.declaration.raw_unit")
            check_unit_factor(declared_unit, output_unit, factor, unit.get("raw_unit"))
            annotation_ids = unit_declaration.get("annotation_object_ids")
            if not isinstance(annotation_ids, list) or not annotation_ids or any(not isinstance(v, str) or not v.strip() for v in annotation_ids) or len(set(annotation_ids)) != len(annotation_ids):
                fail("unit.declaration.annotation_object_ids 必须是不重复的中性文字对象ID数组")
            text(unit_declaration.get("observation"), "unit.declaration.observation")
            unit_applies_to = unit_declaration.get("applies_to")
            if unit_applies_to not in UNIT_APPLIES_TO:
                fail("unit.declaration.applies_to 必须写明所引条款适用的对象：coordinates（图纸坐标）、annotation_values（标注数值）或 pipe_length_elevation（管长、标高）")
            corroboration = unit_declaration.get("geometric_corroboration")
            corroboration_ids: list[str] = []
            if corroboration is not None:
                if not isinstance(corroboration, list) or not corroboration or any(not isinstance(item, dict) for item in corroboration):
                    fail("unit.declaration.geometric_corroboration 必须是非空对象数组")
                for item in corroboration:
                    ids = item.get("object_ids")
                    if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or not v.strip() for v in ids):
                        fail("geometric_corroboration[].object_ids 必须是非空对象ID数组")
                    corroboration_ids.extend(v for v in ids if v not in corroboration_ids)
            elif unit_applies_to != "coordinates":
                fail(f"所引单位条款适用于{UNIT_APPLIES_TO[unit_applies_to]}，不是图纸坐标；须提供 geometric_corroboration（图框幅面×出图比例或已知构件尺寸）才能确定坐标单位")
            unit_entry = ledger_entries.get(text(unit_declaration.get("adjudication_id"), "unit.declaration.adjudication_id"))
            if (unit_entry is None or unit_entry.get("decision") != "include" or unit_entry.get("professional_owner") != "qto-pipe-networks"
                    or not (set(annotation_ids) | set(corroboration_ids)) <= set(unit_entry.get("object_ids") or [])):
                fail("单位裁决必须引用模型已审阅的真实单位文字和佐证对象（include 记录须覆盖全部 annotation_object_ids 与 geometric_corroboration 对象）")
            pending_unit_texts = {object_id: None for object_id in annotation_ids}
            pending_corroboration = {object_id: None for object_id in corroboration_ids}
            source_raw_unit = declared_unit
        elif source_raw_unit is not None:
            check_unit_factor(source_raw_unit, output_unit, factor, unit.get("raw_unit"))

        def row_raw_unit(value: Any) -> Any:
            return declared_unit if declared_unit is not None and not unit_known(value) else value
        boundary_policy = scope.get("boundary_policy", "contained")
        if boundary_policy not in {"contained", "whole_objects"}:
            fail("scope.boundary_policy 必须为 contained 或 whole_objects")
        if boundary_policy == "whole_objects":
            text(scope.get("boundary_source_id"), "scope.boundary_source_id")
        boundary_objects = []

        symbol_context = None
        if profile.get("symbol_census_report") is not None:
            if not args.cad_manifest:
                fail("symbol_census_report requires --cad-manifest")
            symbol_context = symbol_geometry.pipe_context(profile["symbol_census_report"], args.cad_manifest.resolve(), layout)
        layer_rules = normalized_rules(profile.get("layer_rules"), "layer_rules")
        for item in layer_rules:
            decision = str(item.get("decision", "unresolved"))
            if decision not in {"include", "exclude", "unresolved"}:
                fail(f"{item['rule_id']}.decision 无效")
            bind_rule_authority(item, item["rule_id"], ledger_entries, decision)
        block_rules = normalized_rules(profile.get("block_rules", []), "block_rules")
        for item in block_rules:
            item["block_re"] = compile_pattern(item.get("block_pattern", ".*"), f"{item['rule_id']}.block_pattern")
            decision = str(item.get("decision", "unresolved"))
            if decision not in {"include", "exclude", "unresolved"}:
                fail(f"{item['rule_id']}.decision 无效")
            bind_rule_authority(item, item["rule_id"], ledger_entries, decision)
        root_rules = normalized_rules(profile.get("root_rules", []), "root_rules")
        for item in root_rules:
            item["block_re"] = compile_pattern(item.get("block_pattern", ".*"), f"{item['rule_id']}.block_pattern")
            item["entity_types"] = {str(value).upper() for value in item.get("allowed_entity_types", ["CIRCLE", "INSERT"])}
            decision = str(item.get("decision", "unresolved"))
            if decision not in {"include", "exclude", "unresolved"}:
                fail(f"{item['rule_id']}.decision 无效")
            bind_rule_authority(item, item["rule_id"], ledger_entries, decision)
        text_rules = normalized_rules(profile.get("text_rules", []), "text_rules")
        for item in text_rules:
            item["text_re"] = compile_pattern(item.get("text_pattern"), f"{item['rule_id']}.text_pattern")
            disposition = str(item.get("disposition", "scenario"))
            if disposition not in {"seed", "scenario"}:
                fail(f"{item['rule_id']}.disposition 必须是 seed 或 scenario")
            bindings = item.get("segment_bindings", {})
            if not isinstance(bindings, dict):
                fail(f"{item['rule_id']}.segment_bindings 必须为映射")
            item["segment_bindings"] = {text(oid, "绑定文字ID"): binding_targets(value, item['rule_id']) for oid, value in bindings.items()}
            shared = item.get('shared_annotation_targets', {})
            if not isinstance(shared, dict):
                fail('shared_annotation_targets 须为文字ID到逐目标依据的映射')
            if set(shared) - set(bindings):
                fail('shared_annotation_targets 不得含未列 segment_bindings 的文字ID')
            stacks = item.get("callout_stacks", [])
            if not isinstance(stacks, list) or any(not isinstance(st, dict) for st in stacks):
                fail(f"{item['rule_id']}.callout_stacks 必须为数组")
            if disposition == "seed" or item.get("shared_annotation_targets") or any(st.get("segment_contexts") for st in stacks):
                bind_rule_authority(item, item["rule_id"], ledger_entries, "include")
            if disposition == "seed":
                if not bindings and not stacks:
                    fail(f"{item['rule_id']} seed必须提供图面核对后的 segment_bindings 或 callout_stacks，最近距离不能代替")
            for text_id, target_ids in item["segment_bindings"].items():
                if not applies_to(item, text_id) or any(not applies_to(item, target_id[4:]) for target_id in target_ids):
                    fail(f"{item['rule_id']} 文字与目标管段必须都在本条识别范围内")

        binding_row_ids = {member for r in text_rules for oid, ts in r["segment_bindings"].items() for member in [oid, *[t[4:] for t in ts]]}
        for rule in text_rules:
            for stack in rule.get("callout_stacks", []):
                if not isinstance(stack, dict):
                    fail("callout_stacks 项须为对象")
                binding_row_ids.update(stack.get("text_ids", []))
                binding_row_ids.update(t[4:] for t in binding_targets(stack.get("segment_ids"), "stack.segment_ids"))
                binding_row_ids.update(p.get("record_id") for p in stack.get("leader_path", []) if isinstance(p, dict))
        binding_rows = {}

        total_rows = scoped_rows = 0
        ignored_types: Counter[str] = Counter()
        candidates: dict[str, dict[str, Any]] = {}
        candidate_meta: dict[str, dict[str, Any]] = {}
        object_dispositions: dict[str, dict[str, Any]] = {}
        explicit_dispositions: list[dict[str, Any]] = []
        recognition_unresolved: list[dict[str, Any]] = []
        text_rows: list[dict[str, Any]] = []
        insert_rows: list[dict[str, Any]] = []
        derived_roots: list[dict[str, Any]] = []
        root_consumed_ids: set[str] = set()
        context_insert_rows: list[dict[str, Any]] = []
        for row in rows:
            total_rows += 1
            if row.get("row_type") != "entity" or row.get("layout") != layout:
                continue
            if pending_unit_texts is not None and row.get("record_id") in pending_unit_texts:
                pending_unit_texts[str(row["record_id"])] = row
            if row.get("record_id") in pending_corroboration:
                pending_corroboration[str(row["record_id"])] = row
            if row.get("entity_type") == "INSERT":
                context_insert_rows.append(row)
            if not bbox_intersects(row, bounds):
                continue
            scoped_rows += 1
            if row.get("record_id") in binding_row_ids:
                binding_rows[row["record_id"]] = row
            entity_type = str(row.get("entity_type") or "").upper()
            source_record_id = text(row.get("record_id"), "record_id")
            if entity_type in LINE_TYPES:
                check_unit_factor(row_raw_unit(row.get("raw_unit")), output_unit, factor, source_raw_unit)
            if source_record_id in object_dispositions:
                fail(f"范围内中性对象ID重复: {source_record_id}")
            review_required = entity_type in LINE_TYPES or entity_type in {"CIRCLE", "INSERT"}
            empty_definition = entity_type == "INSERT" and row.get("empty_block_definition") is True
            object_dispositions[source_record_id] = {
                "object_id": source_record_id,
                "source_record_id": source_record_id,
                "entity_type": entity_type,
                "layer": str(row.get("layer") or ""),
                "block_name": row.get("block_name"),
                "instance_path": row.get("instance_path"),
                "disposition": "unresolved" if review_required and not empty_definition else "not_applicable",
                "basis": "未命中冻结专业规则，不能静默排除" if review_required and not empty_definition else "当前管网适配器不消费该中性实体类型",
                "rule_ids": [],
            }
            if symbol_context is not None:
                shape_candidates = symbol_context["candidates"].get(source_record_id, [])
                if shape_candidates:
                    object_dispositions[source_record_id]["symbol_shape_candidates"] = shape_candidates
                symbol_decision = symbol_geometry.pipe_exclusion(row, symbol_context)
                if symbol_decision is not None:
                    object_dispositions[source_record_id].update(disposition="excluded", basis=symbol_decision["basis"],
                        symbol_disposition=symbol_decision, symbol_census_report=profile["symbol_census_report"])
                    continue
            if empty_definition:
                # registration 20261016-2359: an empty definition is a hint; a block rule may still exclude or adopt it below
                empty_block_hits[str(row.get("block_name") or "")] += 1
                object_dispositions[source_record_id].update(
                    empty_definition_hint=True,
                    basis="空块定义（取证清单 direct_entity_count=0、已序列化），不画任何图元，不是缺定义；仅提示")
            if entity_type in TEXT_TYPES:
                text_rows.append(row)
            if entity_type == "INSERT":
                insert_rows.append(row)
            root_matches = [
                rule
                for rule in root_rules
                if entity_type in rule["entity_types"]
                and applies_to(rule, source_record_id)
                and rule["layer_re"].search(str(row.get("layer") or ""))
                and rule["block_re"].search(str(row.get("block_name") or ""))
            ]
            if root_matches:
                root_priority = max(rule["priority"] for rule in root_matches)
                root_best = sorted((rule for rule in root_matches if rule["priority"] == root_priority), key=lambda item: item["rule_id"])
                if len({(rule.get("decision", "unresolved"), rule.get("system")) for rule in root_best}) > 1:
                    recognition_unresolved.append({"object_id": source_record_id, "kind": "root_rule_conflict", "rule_ids": [rule["rule_id"] for rule in root_best]})
                    object_dispositions[source_record_id].update({"basis": "同优先级根节点规则冲突", "rule_ids": [rule["rule_id"] for rule in root_best]})
                    root_consumed_ids.add(source_record_id)
                    continue
                root_rule = root_best[0]
                root_decision = str(root_rule.get("decision", "unresolved"))
                root_consumed_ids.add(source_record_id)
                if root_decision == "include":
                    anchor = row_point(row, factor)
                    if not anchor:
                        recognition_unresolved.append({"object_id": source_record_id, "kind": "missing_root_point", "rule_id": root_rule["rule_id"]})
                        object_dispositions[source_record_id].update({"basis": "根节点候选缺少坐标", "rule_ids": [root_rule["rule_id"]]})
                    else:
                        derived_roots.append({
                            "root_id": f"ROOT-{source_record_id}",
                            "system": text(root_rule.get("system"), f"{root_rule['rule_id']}.system"),
                            "point": anchor,
                            "source_id": f"EV-CAD-{source_record_id}|{root_rule['rule_id']}",
                            "evidence_level": str(root_rule.get("evidence_level", "scenario")),
                            "root_role": str(root_rule.get("root_role", "vertical_candidate")),
                            "assumption_id": str(root_rule.get("assumption_id", f"A-{root_rule['rule_id']}")),
                        })
                        object_dispositions[source_record_id].update({
                            "disposition": "adopted",
                            "basis": str(root_rule.get("basis") or f"冻结根节点规则 {root_rule['rule_id']}"),
                            "rule_ids": [root_rule["rule_id"]],
                            "adjudication_id": root_rule.get("adjudication_id"),
                        })
                else:
                    object_dispositions[source_record_id].update({
                        "disposition": "excluded" if root_decision == "exclude" else "unresolved",
                        "basis": str(root_rule.get("basis") or f"冻结根节点规则 {root_rule['rule_id']}"),
                        "rule_ids": [root_rule["rule_id"]],
                        "adjudication_id": root_rule.get("adjudication_id"),
                    })
                continue
            if entity_type == "INSERT":
                continue
            if entity_type not in LINE_TYPES:
                ignored_types[entity_type or "<EMPTY>"] += 1
                continue
            rule, conflicts = choose_layer_rule(str(row.get("layer") or ""), layer_rules, source_record_id, entity_type)
            if conflicts:
                recognition_unresolved.append({"object_id": row.get("record_id"), "kind": "layer_rule_conflict", "rule_ids": conflicts, "layer": row.get("layer")})
                object_dispositions[source_record_id].update({"basis": "同优先级图层规则冲突", "rule_ids": conflicts})
                continue
            if not rule:
                continue
            allowed = {str(value).upper() for value in rule.get("allowed_entity_types", sorted(LINE_TYPES))}
            if entity_type not in allowed:
                recognition_unresolved.append({"object_id": row.get("record_id"), "kind": "entity_type_not_covered", "rule_id": rule["rule_id"], "entity_type": entity_type})
                object_dispositions[source_record_id].update({"basis": "图层规则未覆盖当前实体类型", "rule_ids": [rule["rule_id"]]})
                continue
            object_id = f"SEG-{text(row.get('record_id'), 'record_id')}"
            start = row.get("start")
            end = row.get("end")
            raw_length = row.get("raw_length")
            route_geometry, dropped_parts = drop_zero_length_parts(row.get("geometry"))
            if dropped_parts and route_geometry is None:
                recognition_unresolved.append({"object_id": object_id, "kind": "degenerate_segment_geometry", "rule_id": rule["rule_id"],
                                               "source_record_id": row.get("record_id"), "entity_handle": row.get("entity_handle")})
                object_dispositions[source_record_id].update({"basis": "图元的各段都是零长度（重复顶点），没有可计算长度", "rule_ids": [rule["rule_id"]]})
                continue
            if not isinstance(start, list) or not isinstance(end, list) or not isinstance(raw_length, (int, float)) or float(raw_length) <= 0:
                recognition_unresolved.append({"object_id": object_id, "kind": "missing_segment_geometry", "rule_id": rule["rule_id"], "source_record_id": row.get("record_id")})
                object_dispositions[source_record_id].update({"basis": "候选管段缺少可计算几何", "rule_ids": [rule["rule_id"]]})
                continue
            if dropped_parts:
                degenerate_segments.append({"object_id": object_id, "source_record_id": row.get("record_id"),
                                            "entity_handle": row.get("entity_handle"), "layer": row.get("layer"),
                                            "dropped_part_indexes": dropped_parts, "kept_part_count": len(route_geometry["parts"]),
                                            "basis": "已剔除退化段：相邻重复顶点构成的零长度段，合并相邻顶点后按其余各段计"})
            decision = str(rule.get("decision", "unresolved"))
            if decision not in {"include", "exclude", "unresolved"}:
                fail(f"{rule['rule_id']}.decision 无效")
            system = text(rule.get("system"), f"{rule['rule_id']}.system")
            if decision == "include" and bounds is not None:
                box = row.get("bbox")
                contained = (isinstance(box, list) and len(box) >= 4
                    and bounds['xmin'] <= box[0] <= box[2] < bounds['xmax_exclusive']
                    and bounds['ymin'] <= box[1] <= box[3] < bounds['ymax_exclusive'])
                if not contained:
                    boundary_objects.append(source_record_id)
                    if boundary_policy == "contained":
                        recognition_unresolved.append({"object_id": source_record_id, "kind": "scope_boundary_crossing", "bbox": box})
                        object_dispositions[source_record_id].update(disposition="unresolved", basis="对象跨越计量边界；不得把整对象长度当作窗内量。须分段，或有依据地明确选择整对象")
                        continue
            candidate = {
                "object_id": object_id,
                "system": system,
                "entity_type": entity_type,
                "measurement_shape": str(row.get("measurement_shape") or "centerline"),
                "start": scaled_point(start, factor, f"{object_id}.start"),
                "end": scaled_point(end, factor, f"{object_id}.end"),
                "length": float(raw_length) * factor,
                "source_ids": [f"EV-CAD-{row.get('record_id')}", rule["rule_id"], unit_source_id],
                "geometry_signature": row.get("geometry_signature"),
                "geometry": scale_route_geometry(route_geometry, factor),
                "space_id": str(rule.get("space_id") or scope.get("space_id") or layout),
                "physical_id": row.get("physical_id"),
            }
            candidates[object_id] = candidate
            candidate_meta[object_id] = {"row": row, "rule": rule}
            object_dispositions[source_record_id].update({"disposition": "candidate", "basis": str(rule.get("basis") or f"冻结识别规则 {rule['rule_id']}"), "rule_ids": [rule["rule_id"]], "adjudication_id": rule.get("adjudication_id")})
            if decision != "include" or rule.get("authority", {}).get("decision_origin") == "model":
                explicit_dispositions.append({
                    "object_id": object_id,
                    "decision": decision,
                    "basis": str(rule.get("basis") or rule.get("authority", {}).get("basis") or f"冻结识别规则 {rule['rule_id']}"),
                    "source_id": str(rule.get("authority", {}).get("source_id") or rule.get("source_id") or rule["rule_id"]),
                })
        if pending_unit_texts is not None:
            missing = sorted(object_id for object_id, found in pending_unit_texts.items() if found is None)
            if missing:
                fail(f"单位说明文字不在当前布局的中性事实中（块内文字须位于已展开范围）: {', '.join(missing)}")
            unit_texts = []
            for object_id, found in pending_unit_texts.items():
                if str(found.get("entity_type") or "").upper() not in TEXT_TYPES:
                    fail(f"单位说明对象 {object_id} 不是文字实体")
                unit_texts.append(str(found.get("text") or ""))
            # A statement naming several units (e.g. 管长、标高以m计，其余以mm计) cannot fix the
            # coordinate unit by quoting part of it; geometry has to agree independently.
            if len(unit_tokens("\n".join(unit_texts))) > 1 and not pending_corroboration:
                fail("所引单位说明同时写有多种单位；摘取其中一句不能确定图纸坐标单位，须提供 geometric_corroboration（图框幅面×出图比例或已知构件尺寸）")
            verify_unit_declaration_texts(unit_texts, declared_unit, unit_declaration.get("source_excerpt"))
            if pending_corroboration:
                missing = sorted(object_id for object_id, found in pending_corroboration.items() if found is None)
                if missing:
                    fail(f"单位佐证对象不在当前布局的中性事实中: {', '.join(missing)}")
                checked = verify_unit_corroboration(declared_unit, unit_declaration["geometric_corroboration"],
                                                    {oid: found.get("bbox") for oid, found in pending_corroboration.items()})
                unit_declaration = {**unit_declaration, "geometric_corroboration": checked}
            unit_declaration = {**unit_declaration, "applies_to_label": UNIT_APPLIES_TO[unit_declaration["applies_to"]],
                                "coordinate_unit_basis": "geometric_corroboration" if pending_corroboration else "coordinate_statement"}
        insert_rows = apply_insert_quantity_ownership(
            insert_rows, block_rules, root_consumed_ids, object_dispositions,
            candidates, candidate_meta, explicit_dispositions, derived_roots, factor,
            context_insert_rows)
        if not candidates:
            save_pending_recognition(output, object_dispositions, scope, working_basis, "当前识别配置尚未形成可计算管段",
                                     layer_rules + block_rules + root_rules + text_rules, args.disposition_detail)
            fail("冻结范围和识别配置没有产生可审计管段候选")

        audit_input = {
            "schema_version": "qto-pipe-object-selection-input/1.0",
            "audit_id": f"AUDIT-{profile_id}-{profile_version}",
            "unit": output_unit,
            "source_ids": [str(stream_path), str(profile_path)],
            "parameters": profile.get("object_audit_parameters", {"connection_tolerance": 0.01, "repeat_min_count": 3, "signature_precision": 6}),
            "candidates": [candidates[key] for key in sorted(candidates)],
            "dispositions": explicit_dispositions,
        }
        audit_input_path = output / "管网对象选择审计输入.json"
        audit_path = output / "管网对象选择审计.json"
        audit_input_path.write_text(json.dumps(audit_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        auditor = Path(__file__).resolve().with_name("audit_pipe_object_selection.py")
        audit_run = subprocess.run([sys.executable, str(auditor), "--input", str(audit_input_path), "--output", str(audit_path)], capture_output=True, text=True, encoding="utf-8")
        if audit_run.returncode != 0 or not audit_path.is_file():
            fail(f"对象选择审计失败: {audit_run.stderr.strip() or audit_run.stdout.strip()}")
        audit = load_json(audit_path)
        included_ids = set(audit.get("included_segment_ids", []))
        for decision_row in audit.get("decisions", []):
            segment_id = str(decision_row.get("object_id") or "")
            meta = candidate_meta.get(segment_id)
            if not meta:
                continue
            source_record_id = str(meta["row"].get("record_id") or "")
            disposition = {"include": "adopted", "exclude": "excluded", "unresolved": "unresolved"}.get(str(decision_row.get("decision")))
            if disposition:
                object_dispositions[source_record_id].update({
                    "disposition": disposition,
                    "basis": str(decision_row.get("basis") or object_dispositions[source_record_id]["basis"]),
                })
        segments = []
        for object_id in sorted(included_ids):
            item = candidates[object_id]
            segments.append({
                "segment_id": object_id,
                "system": item["system"],
                "start": item["start"],
                "end": item["end"],
                "length": item["length"],
                "source_ids": item["source_ids"],
                "dn_seeds": [],
                "geometry": item.get("geometry"),
                "space_id": item.get("space_id"),
                "physical_id": item.get("physical_id"),
            })
        if not segments:
            save_pending_recognition(output, object_dispositions, scope, working_basis, "当前对象审计后尚无可计算管段，需继续识图或补充相关证据",
                                     layer_rules + block_rules + root_rules + text_rules, args.disposition_detail)
            fail("对象审计后没有可继续计算的管段")

        if cad_manifest is None and profile.get('expected_entity_source_sha256') is None and binding_rows:
            # Legacy neutral-stream profiles bind the stream hash, not the original drawing hash.
            # The frozen stream must nevertheless carry one consistent claimed entity source.
            row_sources = {r.get('source_sha256') for r in binding_rows.values()}
            if len(row_sources) != 1 or any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h) for h in row_sources):
                fail('冻结中性流的显式绑定对象来源哈希缺失或不一致')
            entity_source_sha = next(iter(row_sources))
        dn_route_index = RouteIndex(segments)
        segment_index = {v["segment_id"]: v for v in segments}
        stack_evidence = []
        for rule in text_rules:
            for stack in rule.get("callout_stacks", []):
                context = {}
                if stack.get('segment_contexts') is not None:
                    context = explicit_target_contexts(stack['segment_contexts'], binding_targets(stack['segment_ids'], 'stack.segment_ids'),
                        rule, entity_source_sha, rule.get("space_id") or scope.get("space_id") or layout, segment_index)
                bindings, evidence = bind_stack(stack, binding_rows, segment_index, factor=factor,
                    output_unit=output_unit, source_sha256=entity_source_sha, system=rule["system"],
                    space_id=rule.get("space_id") or scope.get("space_id") or layout,
                    applies=lambda oid, r=rule: applies_to(r, oid), system_by_segment={sid:v['system'] for sid,v in context.items()})
                for oid, target_ids in bindings.items():
                    previous = rule["segment_bindings"].get(oid)
                    if previous is not None and previous != target_ids:
                        fail(f"{rule['rule_id']} 引线顺序与显式绑定冲突: {oid}")
                    rule["segment_bindings"][oid] = target_ids
                    if context:
                        rule.setdefault('shared_annotation_targets', {})[oid] = [context[sid] for sid in target_ids]
                stack_evidence.append(evidence)
        dn_candidates = []
        threshold_sensitive = False
        consumed_bindings = set()
        explicit_specs = {}
        for row in text_rows:
            oid = str(row.get("record_id") or "")
            raw_text = str(row.get("text") or "")
            anchor = row_point(row, factor)
            if not raw_text or not anchor:
                continue
            for rule in text_rules:
                if not rule["layer_re"].search(str(row.get("layer") or "")) or not applies_to(rule, oid):
                    continue
                match = rule["text_re"].search(raw_text)
                if not match:
                    continue
                dn_value = match.groupdict().get("dn") if match.groupdict() else (match.group(1) if match.groups() else match.group(0))
                digits = re.search(r"\d+", str(dn_value))
                if not digits:
                    continue
                dn = f"DN{digits.group(0)}"
                system = text(rule.get("system"), f"{rule['rule_id']}.system")
                space = rule.get("space_id") or scope.get("space_id") or layout
                explicit = rule["segment_bindings"].get(oid, [])
                if explicit:
                    consumed_bindings.add((rule['rule_id'], oid))
                    if row.get("source_sha256") != entity_source_sha or (row.get("space_id") is not None and row["space_id"] != space):
                        fail(f"{rule['rule_id']} 显式文字跨来源/空间: {oid}")
                    context = {}
                    declared_contexts = rule.get('shared_annotation_targets', {}).get(oid)
                    if declared_contexts is not None:
                        context = explicit_target_contexts(declared_contexts, explicit, rule, entity_source_sha, space, segment_index)
                    elif any(segment_index.get(sid, {}).get('system') != system for sid in explicit):
                        fail(f"{rule['rule_id']} 跨系统共用标注须逐目标 shared_annotation_targets，不能按近邻推定")
                    for target_id in explicit:
                        target = segment_index.get(target_id)
                        raw_target = binding_rows.get(target_id[4:])
                        if (target is None or target['system'] != context.get(target_id, {}).get('system', system) or target.get('space_id') != space
                                or raw_target is None or raw_target.get('source_sha256') != entity_source_sha):
                            fail(f"{rule['rule_id']} 显式DN绑定目标不可用或跨来源/系统/空间: {target_id}")
                        if target_id in explicit_specs and explicit_specs[target_id] != dn:
                            fail(f"显式DN绑定冲突: {target_id}: {explicit_specs[target_id]} / {dn}")
                        explicit_specs[target_id] = dn
                ranked = dn_route_index.nearest(anchor, system, space, 2)
                if not ranked and not explicit:
                    recognition_unresolved.append(dict(object_id=oid, kind="dn_target_unavailable", system=system, space_id=space))
                    continue
                maximum = finite(rule.get("max_distance"), f"{rule['rule_id']}.max_distance")
                minimum_margin = finite(rule.get("min_second_candidate_margin", 0.), f"{rule['rule_id']}.min_second_candidate_margin")
                if maximum < 0 or minimum_margin < 0:
                    fail("文字距离/第二候选差值不得为负")
                selected = explicit or [ranked[0][1]]
                selected_group = f"DNG-{rule['rule_id']}-{oid}"
                for target_id in selected:
                    target = segment_index[target_id]
                    distance = route_distance(anchor, target)
                    other = [d for d, sid in ranked if sid != target_id]
                    margin = min(other, default=math.inf) - distance
                    sensitive = not explicit and (distance > maximum*.8 or margin < minimum_margin)
                    disposition = rule.get("disposition", "scenario") if explicit else "scenario"
                    reason = None
                    if not explicit and (distance > maximum or margin < minimum_margin):
                        disposition = "excluded"
                        reason = "超过冻结距离或第二候选差值不足；保留候选及敏感性方案"
                    candidate = dict(candidate_id=f"DNC-{len(dn_candidates)+1:05d}", segment_id=target_id, dn=dn,
                        source_id=f"EV-CAD-{oid}", disposition=disposition, threshold_sensitive=sensitive,
                        alternative_group=selected_group, diagnostics=dict(distance=distance,
                            second_candidate_margin=None if math.isinf(margin) else margin, text=raw_text,
                            rule_id=rule['rule_id'], explicit_binding=bool(explicit), shared_targets=explicit))
                    relevant_stacks = [e for e in stack_evidence if oid in e['text_order']]
                    if explicit and target_id in context:
                        candidate['diagnostics']['shared_annotation_target'] = context[target_id]
                    if relevant_stacks:
                        candidate['diagnostics']['callout_stack_evidence'] = relevant_stacks
                    if disposition == 'excluded':
                        candidate['reason'] = reason
                    else:
                        candidate.update(evidence_level=rule.get('evidence_level', 'scenario') if explicit else 'scenario',
                            method=rule.get('method', 'explicit_text_binding') if explicit else 'proximity_candidate_only',
                            assumption_id=rule.get('assumption_id', f"A-{rule['rule_id']}"))
                    _, pi, parameter = project_route(anchor, geometry_parts(target))
                    candidate.update(part_index=pi, parameter=parameter)
                    dn_candidates.append(candidate)
                    threshold_sensitive |= sensitive
                # Preserve nearest/second and every candidate inside the frozen search radius.
                alternatives = dict((sid, d) for d, sid in ranked)
                alternatives.update((sid, d) for d, sid in dn_route_index.within(anchor, system, maximum+minimum_margin, space))
                for alt_id, distance in alternatives.items():
                    if alt_id in selected:
                        continue
                    dn_candidates.append(dict(candidate_id=f"DNC-{len(dn_candidates)+1:05d}", segment_id=alt_id, dn=dn,
                        source_id=f"EV-CAD-{oid}", disposition='excluded', alternative_group=selected_group,
                        reason='同一标注的其他绑定候选，本轮未采用；不是排除管段数量',
                        threshold_sensitive=not bool(explicit), diagnostics=dict(distance=distance, text=raw_text, rule_id=rule['rule_id'])))
                    threshold_sensitive |= not bool(explicit)
                object_dispositions[oid].update(disposition='adopted' if explicit and rule.get('disposition') == 'seed' else 'candidate',
                    basis='有来源的显式文字/逐管引线绑定' if explicit else '近邻文字仅形成方案候选',
                    rule_ids=[rule['rule_id']], adjudication_id=rule.get('adjudication_id'))
        for rule in text_rules:
            for oid in rule['segment_bindings']:
                if (rule['rule_id'], oid) not in consumed_bindings:
                    fail(f"{rule['rule_id']} 显式文字绑定未被消费（缺失、越界或文字不匹配）: {oid}")

        dn_policy = None
        if dn_candidates:
            strict_ids = [item["candidate_id"] for item in dn_candidates if item["disposition"] != "excluded" and not item["threshold_sensitive"]]
            relaxed_ids = [item["candidate_id"] for item in dn_candidates]
            scenarios = [{"scenario_id": "DN-SENS-STRICT", "candidate_ids": strict_ids}]
            if threshold_sensitive:
                scenarios.append({"scenario_id": "DN-SENS-RELAXED", "candidate_ids": relaxed_ids})
            dn_policy = {
                "policy_id": f"DN-POLICY-{profile_id}-{profile_version}",
                "detected_candidate_count": len(dn_candidates),
                "source_ids": [str(profile_path), str(stream_path)],
                "thresholds": {"defined_per_text_rule": True},
                "sensitivity_scenarios": scenarios,
            }

        valves = []
        accessory_unresolved = []
        for row in insert_rows:
            source_record_id = str(row.get("record_id") or "")
            if source_record_id in root_consumed_ids:
                continue
            layer_name = str(row.get("layer") or "")
            block_name = str(row.get("block_name") or "")
            matches = [rule for rule in block_rules if rule["layer_re"].search(layer_name) and rule["block_re"].search(block_name) and applies_to(rule, source_record_id)]
            if not matches:
                continue
            best_priority = max(rule["priority"] for rule in matches)
            best = sorted((rule for rule in matches if rule["priority"] == best_priority), key=lambda item: item["rule_id"])
            if len({(item.get("decision", "unresolved"), item.get("system")) for item in best}) > 1:
                accessory_unresolved.append({"object_id": row.get("record_id"), "kind": "block_rule_conflict", "rule_ids": [item["rule_id"] for item in best]})
                object_dispositions[source_record_id].update({"basis": "同优先级块规则冲突", "rule_ids": [item["rule_id"] for item in best]})
                continue
            rule = best[0]
            decision = str(rule.get("decision", "unresolved"))
            if decision != "include":
                if decision == "unresolved":
                    accessory_unresolved.append({"object_id": row.get("record_id"), "kind": "unresolved_accessory", "rule_id": rule["rule_id"], "block_name": block_name})
                object_dispositions[source_record_id].update({
                    "disposition": "excluded" if decision == "exclude" else "unresolved",
                    "basis": str(rule.get("basis") or f"冻结识别规则 {rule['rule_id']}"),
                    "rule_ids": [rule["rule_id"]],
                    "adjudication_id": rule.get("adjudication_id"),
                })
                continue
            anchor = row_point(row, factor)
            if not anchor:
                accessory_unresolved.append({"object_id": row.get("record_id"), "kind": "missing_accessory_point", "rule_id": rule["rule_id"]})
                object_dispositions[source_record_id].update({"basis": "附件候选缺少实例坐标", "rule_ids": [rule["rule_id"]]})
                continue
            valves.append({
                "valve_id": f"VALVE-{row.get('record_id')}",
                "system": text(rule.get("system"), f"{rule['rule_id']}.system"),
                "point": anchor,
                "instance_level": str(rule.get("instance_level", "scenario")),
                "valve_type": rule.get("valve_type"),
                "type_source_ids": list(rule.get("type_source_ids", [])),
                "block_family": str(rule.get("block_family") or block_name),
                "candidate_types": list(rule.get("candidate_types", [])),
                "assumption_id": rule.get("assumption_id"),
                "source_id": f"EV-CAD-{row.get('record_id')}",
                "source_object_id": text(row.get("record_id"), "valve source_object_id"),
            })
            object_dispositions[source_record_id].update({
                "disposition": "adopted",
                "basis": str(rule.get("basis") or f"冻结识别规则 {rule['rule_id']}"),
                "rule_ids": [rule["rule_id"]],
                "adjudication_id": rule.get("adjudication_id"),
            })

        conventional_valve_candidate_count = 0
        if profile.get("use_conventional_valve_type_candidates", True):
            valve_library_path = Path(__file__).resolve().parent.parent / "assets" / "阀门类型候选规则.json"
            valve_library = load_json(valve_library_path)
            if valve_library.get("schema_version") != "qto-conventional-valve-type-candidates/1.0":
                fail("常规阀门类型候选库版本无效")
            for valve in valves:
                if valve.get("valve_type") or valve.get("candidate_types"):
                    continue
                matched = [
                    rule for rule in valve_library.get("rules", [])
                    if isinstance(rule, dict)
                    and re.search(str(rule.get("system_pattern") or r"(?!x)x"), valve["system"], re.IGNORECASE)
                ]
                candidate_types = sorted({
                    str(candidate)
                    for rule in matched
                    for candidate in rule.get("candidate_types", [])
                    if isinstance(candidate, str) and candidate.strip()
                })
                if not candidate_types:
                    continue
                valve["candidate_types"] = candidate_types
                valve["candidate_type_source_id"] = "QTO-CONVENTIONAL-VALVE-TYPE-CANDIDATES/1.0:" + ",".join(sorted(str(rule.get("rule_id")) for rule in matched))
                valve["candidate_type_assumption_ids"] = sorted(str(rule.get("assumption_id")) for rule in matched)
                conventional_valve_candidate_count += 1

        resolved_insert_ids = {
            object_id
            for object_id, item in object_dispositions.items()
            if item["entity_type"] == "INSERT" and item["disposition"] in {"adopted", "excluded"}
        }
        for item in object_dispositions.values():
            instance_path = str(item.get("instance_path") or "")
            if item["disposition"] == "unresolved" and any(instance_path == parent_id or instance_path.startswith(parent_id + "/") for parent_id in resolved_insert_ids):
                item.update({
                    "disposition": "not_applicable",
                    "basis": "已由具备裁决依据的父块实例承担专业身份，块内图元不单独形成数量对象",
                    "rule_ids": [],
                })

        handoff = None
        terminals = list(profile.get("terminals", []))
        if args.endpoint_handoff:
            handoff = load_json(args.endpoint_handoff.resolve())
            validate_working_basis(handoff.get("recognition_basis"))
            if working_basis is None:
                working_basis = handoff.get("recognition_basis")
            if handoff.get("schema_version") not in {"qto-network-endpoint-handoff/1.0", "qto-network-endpoint-handoff/1.1"} or handoff.get("consumer_skill") != "qto-pipe-networks":
                fail("endpoint-handoff 不是面向 qto-pipe-networks 的有效交接")
            terminals = list(handoff.get("terminals", []))
            endpoint_summary = {key: handoff.get(key) for key in ("handoff_id", "required", "source_instance_count", "source_physical_instance_count", "source_endpoint_port_count", "delivered_count", "not_applicable_count", "unresolved_count", "source_id")}
            endpoint_summary["source_workpackage_complete"] = handoff.get("source_workpackage_complete", True)
            endpoint_summary["source_unresolved_instance_count"] = handoff.get("source_unresolved_instance_count", 0)
            endpoint_summary["recognition_basis"] = handoff.get("recognition_basis")
            endpoint_summary["reason"] = handoff.get("reason")
        else:
            endpoint_summary = {
                "handoff_id": f"HANDOFF-NOT-REQUIRED-{profile_id}",
                "required": False,
                "source_instance_count": len(terminals),
                "source_physical_instance_count": len(terminals),
                "source_endpoint_port_count": len(terminals),
                "delivered_count": len(terminals),
                "not_applicable_count": 0,
                "unresolved_count": 0,
                "source_id": str(profile.get("endpoint_source_id") or "PROFILE-NO-POINT-WORKPACKAGE"),
                "reason": str(profile.get("endpoint_not_required_reason") or "本工作包未接入点位专业交接；未以零代替未知端点"),
            }

        terminal_type_rules = list(profile.get("terminal_type_rules", []))
        conventional_terminal_rule_count = 0
        if profile.get("use_conventional_terminal_type_candidates", True):
            library_path = Path(__file__).resolve().parent.parent / "assets" / "末端类型DN候选规则.json"
            library = load_json(library_path)
            if library.get("schema_version") != "qto-conventional-terminal-dn-candidates/1.0":
                fail("常规末端DN候选库版本无效")
            existing_terminal_keys = {
                (str(item.get("system") or ""), str(item.get("terminal_type") or ""))
                for item in terminal_type_rules if isinstance(item, dict)
            }
            for terminal in terminals:
                if not isinstance(terminal, dict) or terminal.get("port_dn") is not None:
                    continue
                system = str(terminal.get("system") or "")
                terminal_type = str(terminal.get("terminal_type") or "")
                key = (system, terminal_type)
                if not system or not terminal_type or key in existing_terminal_keys:
                    continue
                matched = [
                    rule for rule in library.get("rules", [])
                    if isinstance(rule, dict)
                    and re.search(str(rule.get("system_pattern") or r"(?!x)x"), system, re.IGNORECASE)
                    and re.search(str(rule.get("terminal_type_pattern") or r"(?!x)x"), terminal_type, re.IGNORECASE)
                ]
                dns = {str(rule.get("port_dn") or "") for rule in matched}
                if len(dns) != 1:
                    continue
                source_rules = sorted(str(rule.get("rule_id")) for rule in matched)
                terminal_type_rules.append({
                    "rule_id": f"CAND-EXACT-{len(terminal_type_rules) + 1:04d}",
                    "system": system,
                    "terminal_type": terminal_type,
                    "port_dn": next(iter(dns)),
                    "evidence_level": "scenario",
                    "source_id": "QTO-CONVENTIONAL-TERMINAL-DN-CANDIDATES/1.0:" + ",".join(source_rules),
                    "assumption_id": str(matched[0].get("assumption_id") or f"A-CAND-{terminal_type}"),
                })
                existing_terminal_keys.add(key)
                conventional_terminal_rule_count += 1

        inference_input = {
            "schema_version": "qto-pipe-network-inference-input/1.2",
            "network_id": str(profile.get("network_id") or f"NETWORK-{profile_id}"),
            "unit": output_unit,
            "parameters": profile.get("inference_parameters", {}),
            "object_selection": audit,
            "segments": segments,
            "roots": list(profile.get("roots", [])) + derived_roots,
            "terminals": terminals,
            "endpoint_handoff": endpoint_summary,
            "terminal_type_rules": terminal_type_rules,
            "role_dn_rules": profile.get("role_dn_rules", []),
            "gap_classifications": profile.get("gap_classifications", []),
            "gap_exclusions": profile.get("gap_exclusions", []),
            "junctions": profile.get("junctions", []),
            "cross_view_links": profile.get("cross_view_links", []),
            "change_nodes": profile.get("change_nodes", []),
            "scope_boundary_nodes": profile.get("scope_boundary_nodes", []),
            "valves": valves,
        }
        if working_basis:
            inference_input["recognition_basis"] = working_basis
        attach_recognition_records(object_dispositions, layer_rules + block_rules + root_rules + text_rules,
                                   args.disposition_detail)
        if dn_candidates:
            inference_input["dn_match_candidates"] = dn_candidates
            inference_input["dn_match_policy"] = dn_policy
        if stack_evidence:
            inference_input["callout_stack_evidence"] = stack_evidence
        inference_input_path = output / "管网推算输入.json"
        inference_input_path.write_text(json.dumps(inference_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        disposition_path = output / "管网中性对象处置清单.jsonl"
        with disposition_path.open("w", encoding="utf-8") as handle:
            for object_id in sorted(object_dispositions):
                handle.write(json.dumps(object_dispositions[object_id], ensure_ascii=False, sort_keys=True) + "\n")
        disposition_counts = Counter(item["disposition"] for item in object_dispositions.values())
        if sum(disposition_counts.values()) != scoped_rows:
            fail("范围内中性对象处置数量不守恒")
        unresolved_dispositions = [
            {
                "object_id": item["object_id"],
                "kind": "uncovered_neutral_object",
                "entity_type": item["entity_type"],
                "layer": item["layer"],
                "block_name": item.get("block_name"),
                "basis": item["basis"],
                "rule_ids": item["rule_ids"],
            }
            for item in object_dispositions.values()
            if item["disposition"] == "unresolved"
        ]
        unresolved = recognition_unresolved + accessory_unresolved + block_expansion_failures + unresolved_dispositions
        conversion = cad_manifest.get('exchange_conversion') if cad_manifest else None
        if conversion and conversion.get('geometry_review_required'):
            unresolved.append({'kind': 'exchange_copy_audited', 'basis': '修复转换副本仍需核对受影响对象', 'source_id': conversion['path']})
        model_object_ids = {oid for entry in ledger_entries.values() if entry.get("decision_origin") == "model" for oid in entry["object_ids"]}
        missing_model_objects = sorted(model_object_ids - set(object_dispositions))
        unresolved.extend({"object_id": oid, "kind": "model_recognition_object_not_in_scope"} for oid in missing_model_objects)
        report = {
            "schema_version": "qto-pipe-recognition-report/1.0",
            "adapter_version": VERSION,
            "disposition_detail": args.disposition_detail,
            "status": "REVIEW_REQUIRED" if unresolved or audit.get("unresolved_count", 0) else "READY",
            "profile": {"path": str(profile_path), "sha256": sha256(profile_path), "profile_id": profile_id, "profile_version": profile_version},
            "adjudication_ledger": {"path": str(ledger_path), "sha256": ledger_sha, "ledger_id": ledger["ledger_id"], "ledger_version": ledger["ledger_version"]},
            "neutral_source": {"path": str(stream_path), "sha256": sha256(stream_path), "cad_manifest": str(args.cad_manifest.resolve()) if args.cad_manifest else None,
                               "prescoped_rows": ({"path": str(profile["prescoped_rows"]["path"]), "sha256": profile["prescoped_rows"]["sha256"],
                                                   "generator": prescoped_header.get("generator"), "scoped_by": "batch_entry_single_read"} if prescoped_header else None)},
            "scope": scope,
            "unit": {"raw_unit": source_raw_unit, "factor_to_output": factor, "output_unit": output_unit, "source_id": unit_source_id,
                     "source_declared_unit": (cad_manifest.get("drawing_unit") or {}).get("raw_unit") if cad_manifest else unit.get("raw_unit"),
                     "declaration": unit_declaration},
            "scope_boundary_objects": boundary_objects,
            "symbol_census_report": profile.get("symbol_census_report"),
            "counts": {
                "total_neutral_rows": prescoped_header["source_total_rows"] if prescoped_header else total_rows,
                "rows_read": total_rows,
                "scoped_rows": scoped_rows,
                "segment_candidates": len(candidates),
                "symbol_explicitly_excluded_objects": sum("symbol_disposition" in item for item in object_dispositions.values()),
                "symbol_candidate_objects": sum(bool(item.get("symbol_shape_candidates")) for item in object_dispositions.values()),
                "included_segments": len(segments),
                "excluded_segments": int(audit.get("excluded_count", 0)),
                "unresolved_segments": int(audit.get("unresolved_count", 0)),
                "dn_match_candidates": len(dn_candidates),
                "valves": len(valves),
                "derived_roots": len(derived_roots),
                "conventional_terminal_type_rules": conventional_terminal_rule_count,
                "conventional_valve_type_candidates": conventional_valve_candidate_count,
                "recognition_unresolved": disposition_counts.get("unresolved", 0) + len(block_expansion_failures),
                "unresolved_events": len(unresolved),
                "block_expansion_failures": len(block_expansion_failures),
                "empty_block_instances": sum(empty_block_hits.values()),
                "degenerate_segments_dropped": sum(len(item["dropped_part_indexes"]) for item in degenerate_segments),
                "object_dispositions": dict(sorted(disposition_counts.items())),
                "disposition_closure_count": sum(disposition_counts.values()),
            },
            "empty_block_definitions": dict(sorted(empty_block_hits.items())),
            "degenerate_segments": degenerate_segments,
            "ignored_entity_types": dict(sorted(ignored_types.items())),
            "unresolved": unresolved,
            "disposition_closure_complete": sum(disposition_counts.values()) == scoped_rows,
            "artifacts": {"object_selection_input": str(audit_input_path), "object_selection_audit": str(audit_path), "neutral_object_dispositions": str(disposition_path), "neutral_object_dispositions_sha256": sha256(disposition_path), "inference_input": str(inference_input_path)},
            "business_adoption_allowed": False,
            "recognition_basis": working_basis,
            "unmatched_recognition_object_ids": missing_model_objects,
            "meaning": "识别配置和对象审计已确定运行输入；候选/未决不等于专业标准答案或正式工程量",
        }
        if args.inference_output:
            inference_output = args.inference_output.resolve()
            inference_output.parent.mkdir(parents=True, exist_ok=True)
            engine = Path(__file__).resolve().with_name("infer_pipe_network.py")
            inference_run = subprocess.run([sys.executable, str(engine), "--input", str(inference_input_path), "--output", str(inference_output)], capture_output=True, text=True, encoding="utf-8")
            if inference_run.returncode != 0:
                fail(f"管网推算失败: {inference_run.stderr.strip() or inference_run.stdout.strip()}")
            report["artifacts"]["inference_output"] = str(inference_output)
        report_path = output / "管网识别报告.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], **report["counts"], "report": str(report_path)}, ensure_ascii=False))
        return 0
    except (OSError, InputError, ValueError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
