#!/usr/bin/env python3
"""Frame-by-frame pipe inference inputs from one neutral CAD package, non-interactive.

A family mapping is the model's working recognition at layer / block-family level.
This entry materialises it per frame into explicit object lists (a working
recognition ledger) and a frozen profile, then runs the existing adapter
`build_pipe_inference_input.py` for each frame. Objects no family covers are
listed as pending work; a frame that fails leaves step / object class / raw error
diagnostics and never stops the other frames. Nothing here is a professional
decision: layer and block names only become recognition through the mapping's
evidence and the model identity written on it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))

import build_pipe_inference_input as adapter

VERSION = "qto-pipe-batch-frame-adapter/1.4.1"
MAPPING_SCHEMA = "qto-pipe-batch-family-mapping/1.0"
FRAMES_SCHEMA = "qto-pipe-batch-frames/1.0"
SUMMARY_SCHEMA = "qto-pipe-batch-conversion-summary/1.0"
FAMILY_KINDS = {"layer", "block", "root", "text"}
EXCLUDED_SYSTEM_LABEL = "排除对象（无系统）"
REVIEW_TYPES = adapter.LINE_TYPES | {"CIRCLE", "INSERT"}
ID_COLUMNS = ("frame_id", "图页编号", "图框编号", "编号", "sheet_id")
NAME_COLUMNS = ("name", "图名", "图纸名称")
BOUND_COLUMNS = (("x1", "y1", "x2", "y2"), ("xmin", "ymin", "xmax", "ymax"))
SCALE_COLUMNS = ("scale_factor", "比例系数")
STATUS_COLUMNS = ("status", "状态")
STEP_FRAMES, STEP_MAPPING, STEP_SCAN, STEP_WRITE, STEP_ADAPTER, STEP_INFERENCE = (
    "frames", "mapping", "scan", "write_profile", "adapter", "inference")


class BatchInputError(ValueError):
    pass


def fail(message: str) -> None:
    raise BatchInputError(message)


def sha256(path: Path) -> str:
    return adapter.sha256(path)


def load_json(path: Path) -> dict[str, Any]:
    return adapter.load_json(path)


def text(value: Any, label: str) -> str:
    return adapter.text(value, label)


def finite(value: Any, label: str) -> float:
    return adapter.finite(value, label)


def parse_number(value: Any, label: str) -> float:
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            fail(f"{label} 不是数值: {value!r}")
    return finite(value, label)


# ---------------------------------------------------------------- frames
# Each loader returns raw records first; numeric parsing and id validation run only
# for the rows --only-status / --frame-id select (registration 20261015-0154), so a
# malformed row the host never asked for cannot stop the whole batch.


def frame_record(frame_id: str, name: str, bounds: list[float], extra: dict[str, Any]) -> dict[str, Any]:
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        fail(f"图框 {frame_id} 坐标框无效（须 xmin<xmax, ymin<ymax）: {bounds}")
    if not re.fullmatch(r"[A-Za-z0-9_.\-一-鿿]+", frame_id):
        fail(f"图框编号只能含字母、数字、下划线、点、连字符或汉字: {frame_id!r}")
    return {"frame_id": frame_id, "name": name, "bounds": bounds, **extra}


def raw_frames_json(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if data.get("schema_version") != FRAMES_SCHEMA:
        fail(f"frames.schema_version 必须为 {FRAMES_SCHEMA}")
    raw = data.get("frames")
    if not isinstance(raw, list) or not raw:
        fail("frames.frames 必须是非空数组")
    records = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            fail(f"frames[{index}] 必须是对象")

        def build(item: dict[str, Any] = item, index: int = index) -> dict[str, Any]:
            frame_id = text(item.get("frame_id"), f"frames[{index}].frame_id")
            bounds = item.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4:
                fail(f"frames[{index}].bounds 必须是 [xmin, ymin, xmax, ymax]")
            bounds = [finite(v, f"{frame_id}.bounds") for v in bounds]
            extra = {key: item[key] for key in ("status", "system", "floor", "scale_factor", "basis", "note") if key in item}
            return frame_record(frame_id, str(item.get("name") or frame_id), bounds, extra)
        records.append({"frame_id": str(item.get("frame_id") or "").strip(), "status": str(item.get("status") or ""),
                        "label": f"frames[{index}]", "build": build})
    return records


def pick_column(header: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {name.strip().lower(): name for name in header}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def raw_frames_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = [name.strip() for name in (reader.fieldnames or [])]
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not rows:
        fail("图页台账 CSV 没有数据行")
    id_column = pick_column(header, ID_COLUMNS)
    if id_column is None:
        fail(f"图页台账缺少图框编号列（可用列名: {', '.join(ID_COLUMNS)}）")
    bound_columns = next((cols for cols in BOUND_COLUMNS if all(pick_column(header, (c,)) for c in cols)), None)
    if bound_columns is None:
        fail("图页台账缺少坐标框列（x1,y1,x2,y2 或 xmin,ymin,xmax,ymax）")
    bound_columns = tuple(pick_column(header, (c,)) for c in bound_columns)
    name_column = pick_column(header, NAME_COLUMNS)
    scale_column = pick_column(header, SCALE_COLUMNS)
    status_column = pick_column(header, STATUS_COLUMNS)
    system_column = pick_column(header, ("system", "专业系统", "系统"))
    floor_column = pick_column(header, ("floor", "楼层区域", "楼层"))
    basis_column = pick_column(header, ("basis", "依据"))
    records = []
    for number, row in enumerate(rows, 2):
        frame_id = row.get(id_column, "")

        def build(row: dict[str, str] = row, number: int = number, frame_id: str = frame_id) -> dict[str, Any]:
            if not frame_id:
                fail(f"图页台账第 {number} 行缺少图框编号")
            bounds = [parse_number(row.get(col, ""), f"{frame_id}.{col}") for col in bound_columns]
            bounds = [min(bounds[0], bounds[2]), min(bounds[1], bounds[3]), max(bounds[0], bounds[2]), max(bounds[1], bounds[3])]
            extra: dict[str, Any] = {"source_row": number}
            if scale_column and row.get(scale_column, ""):
                extra["scale_factor"] = parse_number(row[scale_column], f"{frame_id}.{scale_column}")
            for key, column in (("status", status_column), ("system", system_column), ("floor", floor_column), ("basis", basis_column)):
                if column and row.get(column, ""):
                    extra[key] = row[column]
            return frame_record(frame_id, row.get(name_column, "") if name_column else frame_id, bounds, extra)
        records.append({"frame_id": frame_id, "status": row.get(status_column, "") if status_column else "",
                        "label": f"图页台账第 {number} 行", "build": build})
    return records


def load_frames(path: Path, only_status: str | None = None, frame_ids: list[str] | None = None,
                skip_invalid: bool = False, diagnostics: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Select first, validate second; invalid selected rows stop the batch unless skip_invalid."""
    records = raw_frames_csv(path) if path.suffix.lower() == ".csv" else raw_frames_json(path)
    wanted = set(frame_ids or [])
    unknown = wanted - {item["frame_id"] for item in records}
    if unknown:
        fail(f"--frame-id 未在图框清单中: {', '.join(sorted(unknown))}")
    selected = [item for item in records
                if (only_status is None or item["status"] == only_status) and (not wanted or item["frame_id"] in wanted)]
    counts = Counter(item["frame_id"] for item in selected)
    frames = []
    for item in selected:
        try:
            if counts[item["frame_id"]] > 1:
                fail(f"图框编号重复: {item['frame_id']}")
            frames.append(item["build"]())
        except BatchInputError as exc:
            message = f"{item['label']}: {exc}"
            if not skip_invalid:
                fail(message)
            if diagnostics is not None:
                diagnostics.append({"frame_id": item["frame_id"] or None, "step": STEP_FRAMES, "object_class": "图框",
                                    "error": message, "action": "skipped_invalid_frame"})
    return frames


# --------------------------------------------------------------- mapping


def load_mapping(path: Path) -> dict[str, Any]:
    mapping = load_json(path)
    if mapping.get("schema_version") != MAPPING_SCHEMA:
        fail(f"mapping.schema_version 必须为 {MAPPING_SCHEMA}")
    for key in ("mapping_id", "mapping_version", "expected_source_sha256", "recognized_by", "recognized_at", "source_id"):
        text(mapping.get(key), f"mapping.{key}")
    evidence = mapping.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        fail("mapping.evidence 必须是非空数组")
    resolved_evidence = []
    seen_ids = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            fail(f"mapping.evidence[{index}] 必须是对象")
        evidence_id = text(item.get("evidence_id"), f"mapping.evidence[{index}].evidence_id")
        if evidence_id in seen_ids:
            fail(f"证据编号重复: {evidence_id}")
        seen_ids.add(evidence_id)
        relative = Path(text(item.get("path"), f"{evidence_id}.path"))
        absolute = (path.parent / relative).resolve()
        if not absolute.is_file():
            fail(f"证据文件不存在: {evidence_id} -> {absolute}")
        expected = text(item.get("sha256"), f"{evidence_id}.sha256").lower()
        if sha256(absolute) != expected:
            fail(f"证据文件哈希不一致: {evidence_id}")
        if item.get("kind") not in {"drawing_view", "cad_property", "project_document", "cross_view"}:
            fail(f"{evidence_id}.kind 无效")
        text(item.get("locator"), f"{evidence_id}.locator")
        text(item.get("observation"), f"{evidence_id}.observation")
        resolved = dict(item)
        resolved["path"] = str(absolute)
        resolved["source_sha256"] = mapping["expected_source_sha256"].lower()
        resolved_evidence.append(resolved)
    mapping["evidence"] = resolved_evidence
    unit = mapping.get("unit")
    if not isinstance(unit, dict):
        fail("mapping.unit 必须是对象")
    finite(unit.get("factor_to_output"), "unit.factor_to_output")
    text(unit.get("output_unit"), "unit.output_unit")
    text(unit.get("source_id"), "unit.source_id")
    declaration = unit.get("declaration")
    if declaration is not None:
        if not isinstance(declaration, dict):
            fail("unit.declaration 必须是对象")
        text(declaration.get("raw_unit"), "unit.declaration.raw_unit")
        text(declaration.get("observation"), "unit.declaration.observation")
        ids = declaration.get("annotation_object_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or not v.strip() for v in ids):
            fail("unit.declaration.annotation_object_ids 必须是非空文字对象ID数组")
        if declaration.get("applies_to") not in adapter.UNIT_APPLIES_TO:
            fail("unit.declaration.applies_to 必须写明所引条款适用的对象：coordinates、annotation_values 或 pipe_length_elevation")
        corroboration = declaration.get("geometric_corroboration")
        if corroboration is not None and (not isinstance(corroboration, list) or not corroboration
                                          or any(not isinstance(item, dict) or not isinstance(item.get("object_ids"), list) or not item["object_ids"] for item in corroboration)):
            fail("unit.declaration.geometric_corroboration 必须是非空数组，每项带 object_ids")
        if corroboration is None and declaration["applies_to"] != "coordinates":
            fail(f"unit.declaration 所引条款适用于{adapter.UNIT_APPLIES_TO[declaration['applies_to']]}，不是图纸坐标；须提供 geometric_corroboration")
        for evidence_id in declaration.get("evidence_ids", []):
            if evidence_id not in seen_ids:
                fail(f"unit.declaration 引用了未登记证据 {evidence_id}")
    families = mapping.get("families")
    if not isinstance(families, list) or not families:
        fail("mapping.families 必须是非空数组")
    normalized = []
    family_ids = set()
    for index, raw in enumerate(families):
        if not isinstance(raw, dict):
            fail(f"families[{index}] 必须是对象")
        family = dict(raw)
        family_id = text(family.get("family_id"), f"families[{index}].family_id")
        if family_id in family_ids or family_id == "UNIT":
            fail(f"族编号重复或保留: {family_id}")
        family_ids.add(family_id)
        kind = family.get("kind")
        if kind not in FAMILY_KINDS:
            fail(f"{family_id}.kind 必须是 {sorted(FAMILY_KINDS)} 之一")
        family["layer_re"] = adapter.compile_pattern(family.get("layer_pattern", ".*"), f"{family_id}.layer_pattern")
        if kind in {"block", "root"}:
            family["block_re"] = adapter.compile_pattern(family.get("block_pattern", ".*"), f"{family_id}.block_pattern")
        if kind == "text":
            family["text_re"] = adapter.compile_pattern(family.get("text_pattern"), f"{family_id}.text_pattern")
            if family.get("disposition", "scenario") != "scenario":
                fail(f"{family_id}: 批量入口的文字族只能是 scenario；seed 需要逐条 segment_bindings，请在单图框配置里做")
            finite(family.get("max_distance"), f"{family_id}.max_distance")
        decision = str(family.get("decision", "unresolved"))
        if decision not in {"include", "exclude", "unresolved"}:
            fail(f"{family_id}.decision 无效")
        if kind != "text" and decision in {"include", "exclude"}:
            for key in ("basis",):
                text(family.get(key), f"{family_id}.{key}")
            evidence_ids = family.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids or any(v not in seen_ids for v in evidence_ids):
                fail(f"{family_id}.evidence_ids 必须引用 mapping.evidence 中的证据")
            if not any(next(e for e in resolved_evidence if e["evidence_id"] == v)["kind"] in {"drawing_view", "cad_property", "cross_view"} for v in evidence_ids):
                fail(f"{family_id} 缺图面或对象属性识别证据")
        if decision != "exclude":
            text(family.get("system"), f"{family_id}.system")
        elif not str(family.get("system") or "").strip():
            # The adapter requires a system on every rule; an excluded family has none to state,
            # so it gets an explicit label instead of failing later at the adapter step (0154).
            family["system"] = EXCLUDED_SYSTEM_LABEL
            family["system_defaulted"] = True
        family["priority"] = int(family.get("priority", 0))
        if kind == "root":
            family["entity_types"] = {str(v).upper() for v in family.get("allowed_entity_types", ["CIRCLE", "INSERT"])}
        elif kind == "layer":
            family["entity_types"] = {str(v).upper() for v in family.get("allowed_entity_types", sorted(adapter.LINE_TYPES))}
        frame_pattern = family.get("frame_pattern")
        family["frame_re"] = adapter.compile_pattern(frame_pattern, f"{family_id}.frame_pattern") if frame_pattern is not None else None
        normalized.append(family)
    mapping["families"] = normalized
    expansion = mapping.get("block_expansion", {})
    if not isinstance(expansion, dict):
        fail("mapping.block_expansion 必须是对象")
    max_depth = expansion.get("max_depth", 16)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
        fail("block_expansion.max_depth 必须是正整数")
    mapping["block_expansion"] = {"max_depth": max_depth}
    mapping["layout"] = str(mapping.get("layout") or "Model")
    return mapping


# ------------------------------------------------------------------ scan


def best_family(matches: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    if not matches:
        return None, []
    top = max(item["priority"] for item in matches)
    best = sorted((item for item in matches if item["priority"] == top), key=lambda item: item["family_id"])
    signatures = {(item.get("decision", "unresolved"), item.get("system")) for item in best}
    if len(signatures) > 1:
        return None, [item["family_id"] for item in best]
    return best[0], []


def classify_review_row(entity_type: str, layer: str, block_name: str, active, families: dict[str, list[dict[str, Any]]]):
    """Family decision for a reviewable row: (matched, conflicts, reason); reason None means a clean match.

    `active` holds the family ids that apply where the row lies (a frame, or the whole
    drawing for rows that cannot be located), so both paths share one rule.
    """
    if entity_type in {"CIRCLE", "INSERT"}:
        roots = [fam for fam in families["root"] if fam["family_id"] in active and entity_type in fam["entity_types"]
                 and fam["layer_re"].search(layer) and fam["block_re"].search(block_name)]
        matched, conflicts = best_family(roots)
        if matched is None and not conflicts and entity_type == "INSERT":
            blocks = [fam for fam in families["block"] if fam["family_id"] in active and fam["layer_re"].search(layer) and fam["block_re"].search(block_name)]
            matched, conflicts = best_family(blocks)
    else:
        layer_matches = [fam for fam in families["layer"] if fam["family_id"] in active and fam["layer_re"].search(layer)]
        layers = [fam for fam in layer_matches if entity_type in fam["entity_types"]]
        matched, conflicts = best_family(layers)
        if matched is None and not conflicts and layer_matches:
            # A layer family exists but none covers this type: diagnostic only, never an applied decision.
            diagnostic = sorted(layer_matches, key=lambda fam: (-fam["priority"], fam["family_id"]))[0]
            return diagnostic, [], "entity_type_not_covered"
    if conflicts:
        return None, conflicts, "family_conflict"
    if matched is None:
        return None, [], "unmatched_circle" if entity_type == "CIRCLE" else "no_family"
    return matched, [], None


def family_applies(family: dict[str, Any], frame: dict[str, Any]) -> bool:
    return family["frame_re"] is None or bool(family["frame_re"].search(frame["frame_id"]))


def path_key(record_id: str) -> str:
    """Instance-path key of an INSERT row, as the adapter's ownership uses it."""
    return record_id[len("CADINST-"):] if record_id.startswith("CADINST-") else record_id


def path_prefixes(instance_path: str) -> list[str]:
    prefixes, prefix = [], ""
    for part in instance_path.split("/"):
        prefix = f"{prefix}/{part}" if prefix else part
        prefixes.append(prefix)
    return prefixes


def anchor_in(row: dict[str, Any], bounds: dict[str, float]) -> bool | None:
    """True/False when the row has a usable extent or anchor; None when it has neither.

    An INSERT's transformed definition extent (scope_extent_bbox) comes first, as in the point adapter's scope_of,
    so a base-map block with a far insertion point and its drawing inside a frame belongs to that frame and its
    family decision covers its children (registration 20261017-0031)."""
    extent = row.get("scope_extent_bbox") if str(row.get("entity_type") or "").upper() == "INSERT" else None
    bbox = extent if isinstance(extent, list) and len(extent) >= 4 else row.get("bbox")
    if (isinstance(bbox, list) and len(bbox) >= 4
        and all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in bbox[:4])
        and bbox[2] >= bbox[0] and bbox[3] >= bbox[1]):
        return adapter.bbox_intersects(row, bounds)
    x, y = row.get("x"), row.get("y")
    if all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (x,y)):
        return bounds["xmin"] <= x < bounds["xmax_exclusive"] and bounds["ymin"] <= y < bounds["ymax_exclusive"]
    return None


def located_frame_ids(row: dict[str, Any], frames: list[dict[str, Any]], insert_index: dict[str, str], bounds_by_frame=None) -> tuple[list[str] | None, str]:
    """None means unlocatable; [] means located outside the selected frames.

    A child's missing bbox must not mean "intersects every frame". Use its own
    geometry first, then the nearest ancestor INSERT's world insertion point.
    """
    bounds = bounds_by_frame if bounds_by_frame is not None else {f['frame_id']: frame_bounds_of(f) for f in frames}
    hits = {fid: anchor_in(row, box) for fid, box in bounds.items()}
    if (row.get('location_schema_version') == 'qto-cad-location-evidence/1.0'
            and row.get('location_basis') == 'spline_control_hull' and row.get('location_status') == 'bounded'
            and row.get('measurement_geometry_available') is False):
        box = row.get('bbox')
        if (not isinstance(box, list) or len(box) != 4 or
                not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
                or box[0] > box[2] or box[1] > box[3]):
            return None, 'invalid_spline_control_hull'
        owners = [fid for fid, yes in hits.items() if yes]
        if not owners:
            return [], 'spline_control_hull_outside_selected_frames'
        contained = [fid for fid in owners if isinstance(box, list) and len(box) == 4
                     and bounds[fid]['xmin'] < box[0] <= box[2] < bounds[fid]['xmax_exclusive']
                     and bounds[fid]['ymin'] < box[1] <= box[3] < bounds[fid]['ymax_exclusive']]
        if len(owners) == 1 and contained == owners:
            return owners, 'spline_control_hull'
        return None, 'ambiguous_spline_control_hull'  # drawing-level once; never a measured curve or duplicated per frame
    if any(v is not None for v in hits.values()):
        return [fid for fid, yes in hits.items() if yes], 'own_geometry'
    for key in reversed(path_prefixes(str(row.get('instance_path') or ''))):
        raw = insert_index.get(key)
        if raw is None:
            continue
        ancestor = json.loads(raw)
        x, y = ancestor.get('x'), ancestor.get('y')
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
            owners = [fid for fid, box in bounds.items() if box['xmin'] <= x < box['xmax_exclusive'] and box['ymin'] <= y < box['ymax_exclusive']]
            return owners, 'ancestor_insertion_point'
        matches = {fid: anchor_in(ancestor, box) for fid, box in bounds.items()}
        if any(v is not None for v in matches.values()):
            owners = [fid for fid, yes in matches.items() if yes]
            return (owners if len(owners) <= 1 else None), 'ancestor_extent' if len(owners) <= 1 else 'ambiguous_ancestor_extent'
    return None, 'unlocated'


def unit_context_ids(mapping: dict[str, Any]) -> set[str]:
    declaration = (mapping.get("unit") or {}).get("declaration") or {}
    ids = set(declaration.get("annotation_object_ids") or [])
    for item in declaration.get("geometric_corroboration") or []:
        ids.update(item.get("object_ids") or [])
    return ids


def frame_bounds_of(frame: dict[str, Any]) -> dict[str, float]:
    b = frame["bounds"]
    return {"xmin": b[0], "ymin": b[1], "xmax_exclusive": b[2], "ymax_exclusive": b[3]}


def scan_frames(stream_path: Path, block_path: Path, cad_manifest: dict[str, Any], mapping: dict[str, Any],
                frames: list[dict[str, Any]], diagnostics: list[dict[str, Any]], work_dir: Path) -> dict[str, Any]:
    """The only read of the neutral streams: per frame, family members, pending objects and the
    frame's expanded rows (written to work_dir) so the adapter never re-expands the drawing."""
    layout = mapping["layout"]
    union = {
        "xmin": min(f["bounds"][0] for f in frames), "ymin": min(f["bounds"][1] for f in frames),
        "xmax_exclusive": max(f["bounds"][2] for f in frames), "ymax_exclusive": max(f["bounds"][3] for f in frames),
    }
    frame_bounds = {f["frame_id"]: frame_bounds_of(f) for f in frames}
    per_frame = {f["frame_id"]: {"members": {fam["family_id"]: [] for fam in mapping["families"] if family_applies(fam, f)},
                                 "pending": [], "scoped_rows": 0, "entity_counts": Counter(), "text_hits": Counter(),
                                 "empty_block_hints": Counter(),
                                 "owned_insert_keys": set(), "ancestor_keys": set(), "written_ids": set(),
                                 "body": work_dir / f"{f['frame_id']}.body.ndjson", "body_rows": 0, "expansion_failures": []}
                 for f in frames}
    handles = {frame_id: state["body"].open("w", encoding="utf-8") for frame_id, state in per_frame.items()}
    expansion_failures: list[dict[str, Any]] = []
    families = mapping["families"]
    by_kind = {kind: [fam for fam in families if fam["kind"] == kind] for kind in ("layer", "block", "root")}
    text_families = [fam for fam in families if fam["kind"] == "text"]
    # rows that cannot be placed in a frame only meet families that are not frame-specific
    drawing_families = {fam["family_id"] for fam in families if fam["frame_re"] is None}
    drawing_excluded: Counter = Counter()
    drawing_owned: set[str] = set()
    context_ids = unit_context_ids(mapping)
    context_rows: dict[str, str] = {}
    insert_index: dict[str, str] = {}   # path key -> serialized INSERT row (strings keep the index small)
    rows = adapter.expanded_manifest_rows(stream_path, block_path, cad_manifest, mapping["block_expansion"]["max_depth"],
                                          expansion_failures, target_layout=layout, scope_bounds=union)
    total_rows = 0
    drawing_pending: list[dict[str, Any]] = []
    try:
        for row in rows:
            total_rows += 1
            if row.get("row_type") != "entity" or row.get("layout") != layout:
                continue
            entity_type = str(row.get("entity_type") or "").upper()
            layer = str(row.get("layer") or "")
            block_name = str(row.get("block_name") or "")
            record_id = str(row.get("record_id") or "")
            serialized = None
            if entity_type == "INSERT":
                serialized = json.dumps(row, ensure_ascii=False)
                insert_index[path_key(record_id)] = serialized
            if record_id in context_ids:
                context_rows[record_id] = serialized or json.dumps(row, ensure_ascii=False)
            instance_path = str(row.get("instance_path") or "")
            owners, location_basis = located_frame_ids(row, frames, insert_index, frame_bounds)
            if owners is None:
                if entity_type in REVIEW_TYPES:
                    # same family rule as inside a frame: what the mapping excludes is not pending anywhere
                    matched, conflicts, reason = classify_review_row(entity_type, layer, block_name, drawing_families, by_kind)
                    item = {'object_id': record_id, 'entity_type': entity_type, 'layer': layer,
                            'block_name': row.get('block_name'), 'instance_path': row.get('instance_path'),
                            'location_basis': location_basis}
                    if reason is None and matched.get('decision') == 'exclude':
                        drawing_excluded[matched['family_id']] += 1
                        if entity_type == 'INSERT':
                            drawing_owned.add(path_key(record_id))
                    elif reason is None:
                        drawing_pending.append({**item, 'reason': 'unlocated_family_member', 'family_ids': [matched['family_id']]})
                        if entity_type == 'INSERT' and matched.get('decision') == 'include':
                            drawing_owned.add(path_key(record_id))
                    elif reason == 'entity_type_not_covered':
                        drawing_pending.append({**item, 'reason': 'unlocated_entity_type_not_covered', 'family_ids': [matched['family_id']]})
                    elif reason == 'family_conflict':
                        drawing_pending.append({**item, 'reason': 'unlocated_family_conflict', 'family_ids': conflicts})
                    else:
                        drawing_pending.append({**item, 'reason': 'unlocated_object', 'family_ids': []})
                continue
            for frame in frames:
                frame_id = frame["frame_id"]
                if frame_id not in owners:
                    continue
                state = per_frame[frame_id]
                if serialized is None:
                    serialized = json.dumps(row, ensure_ascii=False)
                handles[frame_id].write(serialized + "\n")
                state["body_rows"] += 1
                state["written_ids"].add(record_id)
                if instance_path:
                    state["ancestor_keys"].update(path_prefixes(instance_path))
                state["scoped_rows"] += 1
                state["entity_counts"][entity_type or "<EMPTY>"] += 1
                active = state["members"]
                if entity_type in adapter.TEXT_TYPES:
                    for fam in text_families:
                        if fam["family_id"] in active and fam["layer_re"].search(layer) and fam["text_re"].search(str(row.get("text") or "")):
                            state["text_hits"][fam["family_id"]] += 1
                    continue
                if entity_type not in REVIEW_TYPES:
                    continue
                item = {"object_id": record_id, "entity_type": entity_type, "layer": layer, "block_name": row.get("block_name"),
                        "instance_path": row.get("instance_path"), "location_basis": location_basis}
                matched, conflicts, reason = classify_review_row(entity_type, layer, block_name, active, by_kind)
                if reason == "entity_type_not_covered":
                    state["pending"].append({**item, "reason": "entity_type_not_covered", "family_ids": [matched["family_id"]]})
                    continue
                if reason == "family_conflict":
                    state["pending"].append({**item, "reason": "family_conflict", "family_ids": conflicts})
                    continue
                if reason == "unmatched_circle":
                    continue  # the adapter treats an unmatched circle as unresolved; it stays visible in its disposition list
                if reason == "no_family":
                    if entity_type == "INSERT" and row.get("empty_block_definition") is True:
                        state["empty_block_hints"][block_name] += 1   # draws nothing: a hint, not a to-do (20261016-2359)
                        continue
                    state["pending"].append({**item, "reason": "no_family", "family_ids": []})
                    continue
                active[matched["family_id"]].append(record_id)
                if entity_type == "INSERT" and matched.get("decision") in {"include", "exclude"}:
                    state["owned_insert_keys"].add(path_key(record_id))
    finally:
        for handle in handles.values():
            handle.close()

    # Block-expansion failures belong to the frame the failing instance lies in; the rest
    # (outside every frame, or without any position) are drawing-level and reported once.
    drawing_level: list[dict[str, Any]] = []
    for failure in expansion_failures:
        key = str(failure.get("instance_path") or "")
        raw = insert_index.get(key)
        insert_row = json.loads(raw) if raw else None
        owners = []
        if insert_row is not None:
            owners = [f["frame_id"] for f in frames if anchor_in(insert_row, frame_bounds[f["frame_id"]])]
        failure = {**failure, "block_name": failure.get("block_name") or (insert_row or {}).get("block_name"),
                   "layer": (insert_row or {}).get("layer")}
        if owners:
            for frame_id in owners:
                per_frame[frame_id]["expansion_failures"].append(failure)
        else:
            drawing_level.append(failure)
    by_kind = Counter(str(item.get("kind")) for item in drawing_level)
    for kind, count in sorted(by_kind.items()):
        blocks = Counter(str(item.get("block_name") or "") for item in drawing_level if str(item.get("kind")) == kind)
        diagnostics.append({"frame_id": None, "step": STEP_SCAN, "object_class": "INSERT（图框外或无定位的块实例）", "kind": kind,
                            "count": count, "error": f"{kind} × {count}；块名前列: " + ", ".join(f"{k or '<无名>'}:{v}" for k, v in blocks.most_common(10))})

    for state in per_frame.values():
        state["entity_counts"] = dict(sorted(state["entity_counts"].items()))
        state["text_hits"] = dict(sorted(state["text_hits"].items()))
        state["empty_block_hints"] = dict(sorted(state["empty_block_hints"].items()))
    return {"frames": per_frame, "total_rows": total_rows, "expansion_failures": len(expansion_failures),
            "drawing_level_failures": drawing_level, "drawing_level_pending": drawing_pending,
            "drawing_level_excluded": dict(sorted(drawing_excluded.items())), "drawing_level_owned": drawing_owned,
            "insert_index": insert_index, "context_rows": context_rows}


def finalize_prescoped(frame: dict[str, Any], state: dict[str, Any], scan: dict[str, Any], mapping: dict[str, Any],
                       manifest_path: Path, cad_manifest: dict[str, Any], frame_dir: Path) -> Path:
    """Header + the frame's rows + out-of-frame context the adapter still needs."""
    extra: list[str] = []
    seen = set(state["written_ids"])
    for key in sorted(state["ancestor_keys"]):
        raw = scan["insert_index"].get(key)
        if raw is None:
            continue
        record_id = str(json.loads(raw).get("record_id") or "")
        if record_id not in seen:
            seen.add(record_id)
            extra.append(raw)
    for record_id, raw in sorted(scan["context_rows"].items()):
        if record_id not in seen:
            seen.add(record_id)
            extra.append(raw)
    artifacts = cad_manifest.get("artifacts") or {}
    header = {
        "row_type": "prescoped_header", "schema_version": adapter.PRESCOPED_SCHEMA, "generator": VERSION,
        "cad_manifest_sha256": sha256(manifest_path),
        "layout_entities_sha256": str(artifacts["layout_entities"]["sha256"]).lower(),
        "block_definitions_sha256": str(artifacts["block_definitions"]["sha256"]).lower(),
        "layout": mapping["layout"], "bounds": frame_bounds_of(frame), "max_depth": mapping["block_expansion"]["max_depth"],
        "source_total_rows": scan["total_rows"], "row_count": state["body_rows"] + len(extra),
        "frame_rows": state["body_rows"], "context_rows": len(extra),
        "block_expansion_failures": state["expansion_failures"],
    }
    frame_dir.mkdir(parents=True, exist_ok=True)
    target = frame_dir / "预扫描图元.ndjson"
    with target.open("w", encoding="utf-8") as out:
        out.write(json.dumps(header, ensure_ascii=False) + "\n")
        with state["body"].open("r", encoding="utf-8") as body:
            shutil.copyfileobj(body, out)
        for raw in extra:
            out.write(raw + "\n")
    return target


# --------------------------------------------------------------- pending


def group_pending(state: dict[str, Any], insert_index: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Children of a block instance travel with the instance (registration 20261015-0156).

    Children of an instance a family already includes or excludes are not pending at
    all (the adapter gives them to their owner); the rest are grouped by top-level
    instance so a reviewer decides one block, not its hundreds of lines.
    """
    owned = state["owned_insert_keys"]
    detail, suppressed = [], 0
    for item in state["pending"]:
        path = str(item.get("instance_path") or "")
        if path and any(prefix in owned for prefix in path_prefixes(path)):
            suppressed += 1
            continue
        detail.append(item)
    groups: dict[str, dict[str, Any]] = {}
    for item in detail:
        path = str(item.get("instance_path") or "")
        top = path.split("/")[0] if path else path_key(item["object_id"])
        group = groups.get(top)
        if group is None:
            raw = insert_index.get(top)
            top_row = json.loads(raw) if raw else None
            if top_row is not None:
                group = {"top_object_id": top_row.get("record_id"), "entity_type": "INSERT", "layer": top_row.get("layer"),
                         "block_name": top_row.get("block_name")}
            else:
                group = {"top_object_id": item["object_id"], "entity_type": item["entity_type"], "layer": item["layer"],
                         "block_name": item.get("block_name")}
            group.update(member_count=0, reasons=Counter(), family_ids=set(), sample_object_ids=[], top_is_pending=False)
            groups[top] = group
        group["member_count"] += 1
        group["reasons"][item["reason"]] += 1
        group["family_ids"].update(item.get("family_ids") or [])
        if item["object_id"] == group["top_object_id"]:
            group["top_is_pending"] = True
        if len(group["sample_object_ids"]) < 5:
            group["sample_object_ids"].append(item["object_id"])
    grouped = []
    for group in groups.values():
        grouped.append({**group, "reasons": dict(sorted(group["reasons"].items())), "family_ids": sorted(group["family_ids"]),
                        "nested_members": group["member_count"] - (1 if group["top_is_pending"] else 0)})
    grouped.sort(key=lambda g: (-g["member_count"], str(g["layer"]), str(g["block_name"]), str(g["top_object_id"])))
    return detail, grouped, suppressed


def top_level_summary(grouped: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    table: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group in grouped:
        key = (str(group.get("layer") or ""), str(group.get("block_name") or ""), str(group.get("entity_type") or ""))
        entry = table.setdefault(key, {"layer": key[0], "block_name": key[1] or None, "entity_type": key[2], "groups": 0, "members": 0, "frames": set()})
        entry["groups"] += 1
        entry["members"] += group["member_count"]
        if group.get("frame_id"):
            entry["frames"].add(group["frame_id"])
    rows = [{**entry, "frames": sorted(entry["frames"])} for entry in table.values()]
    rows.sort(key=lambda e: (-e["groups"], -e["members"], e["layer"], str(e["block_name"])))
    return rows[:limit] if limit else rows


# ----------------------------------------------------------------- write


def rule_from_family(family: dict[str, Any], frame_id: str) -> dict[str, Any]:
    passthrough = {key: value for key, value in family.items()
                   if key not in {"layer_re", "block_re", "text_re", "frame_re", "entity_types", "kind", "family_id", "frame_pattern",
                                  "evidence_ids", "scope_basis", "identity_basis", "cross_check", "uncertainties"}}
    rule = {"rule_id": family["family_id"], **passthrough}
    rule["priority"] = family["priority"]
    if family["kind"] == "layer":
        rule["allowed_entity_types"] = sorted(family["entity_types"])
    elif family["kind"] == "root":
        rule["allowed_entity_types"] = sorted(family["entity_types"])
    if family["kind"] != "text" and rule.get("decision", "unresolved") in {"include", "exclude"}:
        rule["adjudication_id"] = family["family_id"]
    if family["kind"] == "text":
        rule["disposition"] = "scenario"
        rule.pop("decision", None)
    rule.setdefault("space_id", frame_id)
    return rule


def write_frame_inputs(frame: dict[str, Any], state: dict[str, Any], mapping: dict[str, Any], manifest_sha: str,
                       frame_dir: Path, prescoped_path: Path | None = None) -> tuple[Path, Path, list[str]]:
    frame_id = frame["frame_id"]
    frame_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    skipped_families = []
    rules: dict[str, list[dict[str, Any]]] = {"layer_rules": [], "block_rules": [], "root_rules": [], "text_rules": []}
    for family in mapping["families"]:
        if not family_applies(family, frame):
            continue
        members = state["members"].get(family["family_id"], [])
        needs_entry = family["kind"] != "text" and family.get("decision", "unresolved") in {"include", "exclude"}
        if needs_entry and not members:
            skipped_families.append(family["family_id"])
            continue
        if needs_entry:
            entries.append({
                "adjudication_id": family["family_id"], "subject_id": family["family_id"], "decision": family["decision"],
                "professional_owner": "qto-pipe-networks", "status": "active", "decision_origin": "model",
                "recognized_by": mapping["recognized_by"], "recognized_at": mapping["recognized_at"], "source_id": mapping["source_id"],
                "evidence_ids": list(family["evidence_ids"]), "object_ids": sorted(members), "basis": family["basis"],
                "scope_basis": str(family.get("scope_basis") or f"图框 {frame_id}（{frame.get('name') or ''}）坐标框内、按族映射批量纳入"),
                "identity_basis": str(family.get("identity_basis") or "原图哈希与中性 record_id / 实例路径"),
                "cross_check": str(family.get("cross_check") or "族级图例/系统核对，见证据；逐对象未单独核对"),
                "uncertainties": list(family.get("uncertainties") or ["族级批量识别，未逐对象核对"]),
            })
        rules[f"{family['kind']}_rules"].append(rule_from_family(family, frame_id))
    unit = dict(mapping["unit"])
    declaration = unit.pop("declaration", None)
    if declaration is not None:
        corroboration_ids = [oid for item in declaration.get("geometric_corroboration") or [] for oid in item.get("object_ids") or []]
        unit_object_ids = list(dict.fromkeys(list(declaration["annotation_object_ids"]) + corroboration_ids))
        entries.append({
            "adjudication_id": "UNIT", "subject_id": "UNIT", "decision": "include", "professional_owner": "qto-pipe-networks",
            "status": "active", "decision_origin": "model", "recognized_by": mapping["recognized_by"], "recognized_at": mapping["recognized_at"],
            "source_id": mapping["source_id"], "evidence_ids": list(declaration.get("evidence_ids") or [mapping["evidence"][0]["evidence_id"]]),
            "object_ids": unit_object_ids, "basis": str(declaration["observation"]),
            "scope_basis": "全图单位说明及其几何佐证", "identity_basis": "单位说明文字与佐证对象ID",
            "cross_check": str(declaration.get("cross_check") or "按文字原文核对单位词；坐标单位另以几何佐证核对" if corroboration_ids else declaration.get("cross_check") or "按文字原文核对单位词"),
            "uncertainties": list(declaration.get("uncertainties") or []),
        })
        unit["declaration"] = {key: declaration[key] for key in ("raw_unit", "annotation_object_ids", "observation", "applies_to", "geometric_corroboration")
                               if key in declaration}
        if declaration.get("source_excerpt") is not None:
            unit["declaration"]["source_excerpt"] = declaration["source_excerpt"]
        unit["declaration"]["adjudication_id"] = "UNIT"
        unit.setdefault("raw_unit", declaration["raw_unit"])
    ledger = {
        "schema_version": adapter.MODEL_LEDGER_SCHEMA, "ledger_id": f"{mapping['mapping_id']}-{frame_id}", "ledger_version": str(mapping["mapping_version"]),
        "source_sha256": manifest_sha, "adoption_scope": "working", "review_status": "unreviewed",
        "evidence": mapping["evidence"], "entries": entries,
        "batch": {"generator": VERSION, "mapping_id": mapping["mapping_id"], "mapping_version": mapping["mapping_version"], "frame": {k: v for k, v in frame.items()}},
    }
    ledger_path = frame_dir / "模型工作识别记录.json"
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    profile = {
        "schema_version": adapter.PROFILE_SCHEMA, "profile_id": f"{mapping['mapping_id']}-{frame_id}", "profile_version": str(mapping["mapping_version"]),
        "expected_source_sha256": manifest_sha,
        "adjudication_ledger": {"ledger_id": ledger["ledger_id"], "ledger_version": ledger["ledger_version"], "sha256": sha256(ledger_path)},
        "network_id": f"{mapping.get('network_id_prefix') or mapping['mapping_id']}-{frame_id}",
        "scope": {"layout": mapping["layout"], "space_id": frame_id, "boundary_policy": "contained", "bounds": frame_bounds_of(frame)},
        "unit": unit,
        "block_expansion": mapping["block_expansion"],
        **rules,
        "use_conventional_terminal_type_candidates": bool(mapping.get("use_conventional_terminal_type_candidates", True)),
        "use_conventional_valve_type_candidates": bool(mapping.get("use_conventional_valve_type_candidates", True)),
        "object_audit_parameters": mapping.get("object_audit_parameters", {"connection_tolerance": 0.01, "repeat_min_count": 3, "signature_precision": 6}),
        "inference_parameters": mapping.get("inference_parameters", {}),
        "roots": [], "terminals": [],
        "endpoint_not_required_reason": str(mapping.get("endpoint_not_required_reason") or "批量入口未接入点位工作包；末端按后续交接补充，未以零代替未知端点"),
        "batch": {"generator": VERSION, "frame": frame, "skipped_families_without_objects": skipped_families,
                  "defaulted_system_families": [f["family_id"] for f in mapping["families"] if f.get("system_defaulted") and family_applies(f, frame)]},
    }
    if prescoped_path is not None:
        profile["prescoped_rows"] = {"path": prescoped_path.name, "sha256": sha256(prescoped_path)}
    profile_path = frame_dir / "识别配置.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return profile_path, ledger_path, skipped_families


# ------------------------------------------------------------------- run


def run_adapter(manifest_path: Path, profile_path: Path, ledger_path: Path, frame_dir: Path, run_inference: bool,
                disposition_detail: str = "compact") -> tuple[int, str, str]:
    command = [sys.executable, "-B", str(Path(adapter.__file__).resolve()), "--cad-manifest", str(manifest_path),
               "--profile", str(profile_path), "--adjudication-ledger", str(ledger_path), "--output-dir", str(frame_dir),
               "--disposition-detail", disposition_detail]
    if run_inference:
        command += ["--inference-output", str(frame_dir / "管网推算证据.json")]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def adapter_error_text(stdout: str, stderr: str) -> str:
    for stream in (stderr, stdout):
        for line in reversed(stream.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                return line
            if isinstance(value, dict) and value.get("error"):
                return str(value["error"])
            return line
    return "适配器没有输出错误原文"


def classify_adapter_failure(error: str, state: dict[str, Any], families: list[dict[str, Any]] | None = None) -> str:
    """Best-effort object class for a diagnostic; the raw error text is kept beside it."""
    named = sorted((fam for fam in families or [] if fam["family_id"] in error), key=lambda fam: -len(fam["family_id"]))
    if named:
        fam = named[0]
        pattern = fam.get("block_pattern") if fam["kind"] in {"block", "root"} else fam.get("layer_pattern")
        return f"族 {fam['family_id']}（{fam['kind']}，{fam.get('decision', 'unresolved')}，模式 {pattern}）"
    if "管段候选" in error or "可继续计算的管段" in error:
        return "LINE/LWPOLYLINE/ARC（无 include 图层族命中）"
    if "单位" in error:
        return "单位说明文字"
    if "审计" in error:
        return "管段候选（对象选择审计）"
    if "推算" in error:
        return "管网推算输入"
    counts = state.get("entity_counts") or {}
    return ", ".join(f"{k}:{v}" for k, v in counts.items()) or "未知"


def include_line_members(state: dict[str, Any], mapping: dict[str, Any]) -> int:
    return sum(len(state["members"].get(fam["family_id"], [])) for fam in mapping["families"]
               if fam["kind"] == "layer" and fam.get("decision") == "include")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按图框批量生成管网 1.2 推算输入（非交互）")
    parser.add_argument("--cad-manifest", required=True, type=Path)
    parser.add_argument("--frames", required=True, type=Path, help="图框 JSON（qto-pipe-batch-frames/1.0）或图页台账 CSV")
    parser.add_argument("--mapping", required=True, type=Path, help="图层/块族映射 JSON（qto-pipe-batch-family-mapping/1.0）")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--only-status", default=None, help="只处理台账状态等于该值的图框，如 现行")
    parser.add_argument("--frame-id", action="append", default=[], help="只处理指定图框，可重复")
    parser.add_argument("--skip-invalid-frames", action="store_true", help="被选中的图框行格式不合格时写诊断并跳过，不整批停止")
    parser.add_argument("--run-inference", action="store_true", help="每个图框继续运行 infer_pipe_network.py")
    parser.add_argument("--max-scoped-rows", type=int, default=30000, help="单图框范围内图元行数上限提示，超过只告警不中断")
    parser.add_argument("--disposition-detail", choices=("compact", "full"), default="compact",
                        help="传给每框适配器：默认引用识别记录；full逐对象嵌入整条记录")
    args = parser.parse_args(argv)

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    diagnostics: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"schema_version": SUMMARY_SCHEMA, "generator": VERSION, "status": "FAILED", "frames": [],
                               "disposition_detail": args.disposition_detail,
                               "diagnostics": diagnostics, "business_adoption_allowed": False,
                               "meaning": "族级批量工作识别生成的运行输入；候选/未决不等于专业标准答案或正式工程量"}
    summary_path = output / "批量转换汇总.json"
    pending_path = output / "待办清单.jsonl"
    detail_path = output / "待办明细.jsonl"

    def finish(status: str, code: int) -> int:
        summary["status"] = status
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": status, "frames": len(summary["frames"]),
                          "frame_status": dict(Counter(f["status"] for f in summary["frames"])),
                          "pending_groups": sum(f.get("pending_groups", 0) for f in summary["frames"]),
                          "pending_objects": sum(f.get("pending_count", 0) for f in summary["frames"]),
                          "diagnostics": len(diagnostics), "summary": str(summary_path)}, ensure_ascii=False))
        return code

    step = STEP_FRAMES
    try:
        frames = load_frames(args.frames.resolve(), args.only_status, args.frame_id, args.skip_invalid_frames, diagnostics)
        if not frames:
            fail("没有可处理的图框（检查 --only-status / --frame-id，或被跳过的无效图框）")
        step = STEP_MAPPING
        mapping_path = args.mapping.resolve()
        mapping = load_mapping(mapping_path)
        manifest_path = args.cad_manifest.resolve()
        stream_path, block_path, cad_manifest = adapter.source_from_manifest(manifest_path)
        manifest_sha = str(cad_manifest.get("authoritative_source", {}).get("sha256") or "").lower()
        if manifest_sha != mapping["expected_source_sha256"].lower():
            fail("族映射绑定的来源哈希与当前CAD证据不一致")
        summary.update({"cad_manifest": str(manifest_path), "source_sha256": manifest_sha,
                        "mapping": {"path": str(mapping_path), "sha256": sha256(mapping_path), "mapping_id": mapping["mapping_id"], "mapping_version": mapping["mapping_version"],
                                    "defaulted_system_families": [f["family_id"] for f in mapping["families"] if f.get("system_defaulted")]},
                        "frames_file": str(args.frames.resolve()), "layout": mapping["layout"],
                        "skipped_invalid_frames": [d["frame_id"] for d in diagnostics if d.get("action") == "skipped_invalid_frame"]})
    except (OSError, BatchInputError, adapter.InputError, ValueError) as exc:
        diagnostics.append({"frame_id": None, "step": step, "object_class": "输入文件", "error": str(exc)})
        print(json.dumps({"status": "INPUT_REQUIRED", "step": step, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return finish("INPUT_REQUIRED", 2)

    runnable = []
    for frame in frames:
        scale = frame.get("scale_factor")
        if scale is not None and not math.isclose(float(scale), 1.0, rel_tol=0, abs_tol=1e-12):
            summary["frames"].append({**frame, "status": "SKIPPED", "reason": "比例系数不等于 1；单位系数须人工确认后单独配置，不自动换算"})
            diagnostics.append({"frame_id": frame["frame_id"], "step": STEP_FRAMES, "object_class": "图框", "error": f"scale_factor={scale} 不等于 1"})
            continue
        runnable.append(frame)
    if not runnable:
        return finish("INPUT_REQUIRED", 2)

    with tempfile.TemporaryDirectory(prefix="qto-batch-", dir=output) as work:
        work_dir = Path(work)
        try:
            scan = scan_frames(stream_path, block_path, cad_manifest, mapping, runnable, diagnostics, work_dir)
        except (OSError, adapter.InputError, ValueError) as exc:
            diagnostics.append({"frame_id": None, "step": STEP_SCAN, "object_class": "中性图元流", "error": str(exc)})
            print(json.dumps({"status": "INPUT_REQUIRED", "step": STEP_SCAN, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return finish("INPUT_REQUIRED", 2)
        drawing_level = scan["drawing_level_failures"]
        summary["scan"] = {"total_neutral_rows": scan["total_rows"], "block_expansion_failures": scan["expansion_failures"],
                           "neutral_stream_reads": 1}
        summary["drawing_level_expansion_failures"] = {
            "count": len(drawing_level), "by_kind": dict(sorted(Counter(str(item.get("kind")) for item in drawing_level).items())),
            "meaning": "图框外或无定位的块实例展开失败；不影响任何图框的范围内对象，只在此处列一次", "items": drawing_level}

        unlocated_state = {'pending': scan['drawing_level_pending'], 'owned_insert_keys': scan['drawing_level_owned']}
        unlocated_detail, unlocated_groups, unlocated_suppressed = group_pending(unlocated_state, scan['insert_index'])
        excluded = scan['drawing_level_excluded']
        unlocated_path, unlocated_detail_path = output / '未定位对象待办.jsonl', output / '未定位对象明细.jsonl'
        for path, rows in ((unlocated_path, unlocated_groups), (unlocated_detail_path, unlocated_detail)):
            with path.open('w', encoding='utf-8') as handle:
                for item in rows:
                    handle.write(json.dumps({'frame_id': None, 'location_status': 'unlocated', **item}, ensure_ascii=False) + '\n')
        summary['drawing_level_unlocated_objects'] = {
            'count': len(unlocated_detail), 'groups': len(unlocated_groups),
            'by_entity_type': dict(sorted(Counter(item['entity_type'] for item in unlocated_detail).items())),
            'by_reason': dict(sorted(Counter(item['reason'] for item in unlocated_detail).items())),
            'excluded_by_family': excluded, 'excluded_count': sum(excluded.values()),
            'suppressed_by_owner': unlocated_suppressed,
            'pending_list': str(unlocated_path), 'pending_detail': str(unlocated_detail_path),
            'top_level_summary': top_level_summary(unlocated_groups, 20),
            'meaning': ('自身与祖先均无法定位的对象只在全图列一次；不灌入各框待办或pending_line_objects。已定位框外对象不属于此项。'
                        '与图框内相同按不限图框的族匹配：被 exclude 族排除的只计数（excluded_by_family），不列待办；'
                        'include 族成员因无法归框仍列待办（unlocated_family_member）。')}

        all_groups: list[dict[str, Any]] = []
        with pending_path.open("w", encoding="utf-8") as pending_handle, detail_path.open("w", encoding="utf-8") as detail_handle:
            for frame in runnable:
                frame_id = frame["frame_id"]
                state = scan["frames"][frame_id]
                frame_dir = output / frame_id
                detail, grouped, suppressed = group_pending(state, scan["insert_index"])
                for group in grouped:
                    group["frame_id"] = frame_id
                    pending_handle.write(json.dumps({"frame_id": frame_id, **{k: v for k, v in group.items() if k != "frame_id"}}, ensure_ascii=False) + "\n")
                for item in detail:
                    detail_handle.write(json.dumps({"frame_id": frame_id, **item}, ensure_ascii=False) + "\n")
                all_groups.extend(grouped)
                record: dict[str, Any] = {**frame, "directory": str(frame_dir), "scoped_rows": state["scoped_rows"],
                                          "entity_counts": state["entity_counts"],
                                          "family_counts": {k: len(v) for k, v in state["members"].items()},
                                          "text_family_hits": state["text_hits"], "pending_count": len(detail),
                                          "pending_groups": len(grouped), "pending_suppressed_by_owner": suppressed,
                                          "pending_top_level_summary": top_level_summary(grouped, 20),
                                          "block_expansion_failures": len(state["expansion_failures"]),
                                          "empty_block_definitions": state["empty_block_hints"]}
                if state["scoped_rows"] > args.max_scoped_rows:
                    record["warning"] = f"范围内图元 {state['scoped_rows']} 行超过提示上限 {args.max_scoped_rows}；结果仍完整，核对图框坐标是否框大了"
                if state["scoped_rows"] == 0:
                    record.update(status="SKIPPED", reason="坐标框内没有当前布局的中性图元；核对图框坐标与布局名")
                    diagnostics.append({"frame_id": frame_id, "step": STEP_SCAN, "object_class": "图框", "error": "坐标框内没有图元"})
                    summary["frames"].append(record)
                    continue
                if include_line_members(state, mapping) == 0:
                    line_pending = sum(1 for item in detail if item["entity_type"] in adapter.LINE_TYPES)
                    record.update(status="NO_PIPES", pending_line_objects=line_pending,
                                  reason=("框内没有任何 include 管线图层族命中的图元，按“本框无管线”处理，未运行适配器；"
                                          + (f"另有 {line_pending} 个未归族线对象在待办中，若其中有管线须补族后重跑" if line_pending else "也没有未归族线对象")))
                    summary["frames"].append(record)
                    continue
                try:
                    prescoped = finalize_prescoped(frame, state, scan, mapping, manifest_path, cad_manifest, frame_dir)
                    profile_path, ledger_path, skipped = write_frame_inputs(frame, state, mapping, manifest_sha, frame_dir, prescoped)
                except (OSError, BatchInputError, ValueError) as exc:
                    record.update(status="FAILED", failed_step=STEP_WRITE, error=str(exc))
                    diagnostics.append({"frame_id": frame_id, "step": STEP_WRITE, "object_class": "识别配置/识别记录", "error": str(exc)})
                    summary["frames"].append(record)
                    continue
                record.update(profile=str(profile_path), ledger=str(ledger_path), prescoped_rows=str(prescoped), families_without_objects=skipped)
                code, stdout, stderr = run_adapter(manifest_path, profile_path, ledger_path, frame_dir, args.run_inference,
                                                  args.disposition_detail)
                report_path = frame_dir / "管网识别报告.json"
                if code != 0:
                    error = adapter_error_text(stdout, stderr)
                    failed_step = STEP_INFERENCE if "管网推算失败" in error else STEP_ADAPTER
                    record.update(status="FAILED", failed_step=failed_step, error=error, adapter_stderr=stderr.strip()[-4000:],
                                  adapter_report=str(report_path) if report_path.is_file() else None)
                    diagnostics.append({"frame_id": frame_id, "step": failed_step, "object_class": classify_adapter_failure(error, state, mapping["families"]), "error": error})
                    summary["frames"].append(record)
                    continue
                report = load_json(report_path) if report_path.is_file() else {}
                record.update(status=str(report.get("status") or "READY"), adapter_report=str(report_path),
                              inference_input=str(frame_dir / "管网推算输入.json"), adapter_counts=report.get("counts", {}),
                              adapter_unresolved_kinds=dict(Counter(str(item.get("kind")) for item in report.get("unresolved", []))))
                if args.run_inference:
                    record["inference_output"] = report.get("artifacts", {}).get("inference_output")
                summary["frames"].append(record)

    summary["pending_list"] = str(pending_path)
    summary["pending_detail"] = str(detail_path)
    summary["pending_top_level_summary"] = top_level_summary(all_groups, 50)
    statuses = Counter(f["status"] for f in summary["frames"])
    summary["counts"] = {"frames": len(summary["frames"]), "by_status": dict(sorted(statuses.items())),
                         "pending_groups": sum(f.get("pending_groups", 0) for f in summary["frames"]),
                         "pending_objects": sum(f.get("pending_count", 0) for f in summary["frames"]),
                         "unlocated_objects": len(unlocated_detail), 'unlocated_groups': len(unlocated_groups),
                         "unlocated_excluded": sum(excluded.values()),
                         "diagnostics": len(diagnostics)}
    produced = statuses.get("READY", 0) + statuses.get("REVIEW_REQUIRED", 0)
    if statuses.get("FAILED") or statuses.get("SKIPPED"):
        return finish("PARTIAL", 3) if produced else finish("FAILED", 2)
    if produced:
        return finish("REVIEW_REQUIRED" if statuses.get("REVIEW_REQUIRED") or unlocated_detail else "READY", 0)
    if unlocated_detail:
        return finish('REVIEW_REQUIRED', 0)
    return finish("NO_PIPES", 0)


if __name__ == "__main__":
    raise SystemExit(main())
