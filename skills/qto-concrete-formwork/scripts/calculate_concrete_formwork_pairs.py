#!/usr/bin/env python3
"""Deterministic concrete/formwork paired calculator for evidence-locked manifests."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable


MANIFEST_VERSION = "qto-concrete-formwork-manifest/2.0"
RESULT_VERSION = "qto-result-minimum/1.0"
SUPPORTED_CALCULATION = "orthogonal_notched_raft_with_blinding"


class CalculationError(RuntimeError):
    """Raised when evidence or a decisive calculation input is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_number(value: Any, label: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalculationError(f"{label}必须是有限数值")
    number = float(value)
    if positive and number <= 0:
        raise CalculationError(f"{label}必须大于0")
    return number


def rect_prism_volume(length_m: float, width_m: float, height_m: float) -> float:
    return require_number(length_m, "length_m") * require_number(width_m, "width_m") * require_number(height_m, "height_m")


def wall_physical_with_opening(
    length_m: float,
    thickness_m: float,
    height_m: float,
    opening_width_m: float,
    opening_height_m: float,
) -> float:
    gross = rect_prism_volume(length_m, thickness_m, height_m)
    opening = rect_prism_volume(opening_width_m, thickness_m, opening_height_m)
    if opening > gross:
        raise CalculationError("洞口体积不能大于墙体毛体积")
    return gross - opening


def template_opening_adjustment(
    opening_width_m: float,
    opening_height_m: float,
    wall_thickness_m: float,
    *,
    threshold_m2: float = 0.3,
    include_bottom_reveal: bool = True,
) -> dict[str, float | bool]:
    width = require_number(opening_width_m, "opening_width_m")
    height = require_number(opening_height_m, "opening_height_m")
    thickness = require_number(wall_thickness_m, "wall_thickness_m")
    threshold = require_number(threshold_m2, "threshold_m2")
    opening_area = width * height
    deduct = opening_area > threshold
    if not deduct:
        return {"single_opening_area_m2": opening_area, "deduct": False, "main_faces_deduct_m2": 0.0, "reveals_add_m2": 0.0}
    reveal_length = 2 * height + width + (width if include_bottom_reveal else 0.0)
    return {
        "single_opening_area_m2": opening_area,
        "deduct": True,
        "main_faces_deduct_m2": 2 * opening_area,
        "reveals_add_m2": reveal_length * thickness,
    }


def union_area_rectangles(rectangles: Iterable[tuple[float, float, float, float]]) -> float:
    """Return exact union area for axis-aligned rectangles (x1, y1, x2, y2)."""
    rects = []
    for idx, (x1, y1, x2, y2) in enumerate(rectangles):
        if x2 <= x1 or y2 <= y1:
            raise CalculationError(f"rectangle[{idx}]边界非法")
        rects.append((float(x1), float(y1), float(x2), float(y2)))
    if not rects:
        return 0.0
    xs = sorted({x for rect in rects for x in (rect[0], rect[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((y1, y2) for x1, y1, x2, y2 in rects if x1 < right and x2 > left)
        covered = 0.0
        if intervals:
            start, end = intervals[0]
            for y1, y2 in intervals[1:]:
                if y1 <= end:
                    end = max(end, y2)
                else:
                    covered += end - start
                    start, end = y1, y2
            covered += end - start
        area += (right - left) * covered
    return area


def stair_composite_volume(parts_m3: Iterable[float]) -> float:
    values = [require_number(value, f"parts_m3[{idx}]") for idx, value in enumerate(parts_m3)]
    if not values:
        raise CalculationError("楼梯子实体不能为空")
    return math.fsum(values)


def contact_surface_inventory(surfaces: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sum unique confirmed contact faces and retain unresolved faces separately."""
    included: list[str] = []
    excluded: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    area = 0.0
    for idx, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            raise CalculationError(f"surface[{idx}]必须是对象")
        key = surface.get("key")
        state = surface.get("state")
        if not isinstance(key, str) or not key:
            raise CalculationError(f"surface[{idx}]缺少唯一key")
        if key in seen:
            raise CalculationError(f"模板面重复: {key}")
        seen.add(key)
        if state == "included":
            area += require_number(surface.get("area_m2"), f"surface[{key}].area_m2")
            included.append(key)
        elif state == "excluded":
            excluded.append(key)
        elif state == "unresolved":
            unresolved.append(key)
        else:
            raise CalculationError(f"surface[{key}]状态非法")
    return {"included_area_m2": area, "included": included, "excluded": excluded, "unresolved": unresolved}


def orthogonal_offset(area_m2: float, perimeter_m: float, offset_m: float) -> tuple[float, float]:
    area = require_number(area_m2, "area_m2")
    perimeter = require_number(perimeter_m, "perimeter_m")
    offset = require_number(offset_m, "offset_m")
    return area + perimeter * offset + 4 * offset * offset, perimeter + 8 * offset


def _reject_legacy_runtime(path: Path, label: str) -> None:
    lowered = str(path).replace("/", "\\").lower()
    forbidden_fragments = (
        "\\旧skill只读归档\\",
        "\\construction-qto\\",
        "\\skills-disabled\\",
        "\\阶段1_归档与停用\\",
        "\\.qto\\methods\\",
        "\\.qto\\quantities\\final\\",
    )
    forbidden_files = {
        "update_concrete_formwork_artifacts.py",
        "concrete-formwork-quantity-results.json",
        "concrete-formwork-validation.json",
    }
    if any(fragment in lowered for fragment in forbidden_fragments) or path.name.lower() in forbidden_files:
        raise CalculationError(f"{label}不得引用旧运行时、旧方法或旧结果: {path}")


def _resolve_input_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CalculationError(f"{label}缺少路径")
    path = Path(value).expanduser().resolve()
    _reject_legacy_runtime(path, label)
    if not path.is_file():
        raise CalculationError(f"{label}不存在: {path}")
    return path


def _expect_hash(path: Path, expected: Any, label: str) -> str:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise CalculationError(f"{label}预期SHA-256非法")
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise CalculationError(f"{label}哈希不符: expected={expected.lower()} actual={actual}")
    return actual


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalculationError(f"无法读取JSON {path}: {exc}") from exc


def _polygon_metrics(points: list[list[float]], tolerance_mm: float = 0.1) -> tuple[float, float, bool]:
    if len(points) < 4:
        raise CalculationError("轮廓点数不足")
    pts = [(float(point[0]), float(point[1])) for point in points]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    origin = pts[0]
    pts = [(x-origin[0], y-origin[1]) for x,y in pts]
    twice_area = 0.0
    perimeter = 0.0
    orthogonal = True
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        twice_area += x1 * y2 - x2 * y1
        perimeter += math.hypot(dx, dy)
        if abs(dx) > tolerance_mm and abs(dy) > tolerance_mm:
            orthogonal = False
    return abs(twice_area) / 2.0, perimeter, orthogonal


def _read_and_validate_stream(
    stream_path: Path,
    expected_source_sha: str,
    expected_rows: int,
    expected_entities: int,
    evidence_specs: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int, int]:
    wanted = {str(item.get("handle", "")).upper() for item in evidence_specs}
    if "" in wanted or len(wanted) != len(evidence_specs):
        raise CalculationError("evidence_entities的handle必须非空且唯一")
    found: dict[str, dict[str, Any]] = {}
    rows = 0
    entities = 0
    with gzip.open(stream_path, "rt", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CalculationError(f"实体流第{line_no}行JSON非法: {exc}") from exc
            if row.get("row_type") == "entity":
                entities += 1
                row_source = str(row.get("source_sha256", "")).lower()
                if row_source != expected_source_sha.lower():
                    raise CalculationError(f"实体流第{line_no}行来源哈希不符")
            entity_handle = str(row.get("entity_handle", "")).upper()
            if entity_handle in wanted:
                if entity_handle in found:
                    raise CalculationError(f"证据handle不唯一: {entity_handle}")
                found[entity_handle] = row
    if rows != expected_rows:
        raise CalculationError(f"实体流总行数不符: expected={expected_rows} actual={rows}")
    if entities != expected_entities:
        raise CalculationError(f"实体行数不符: expected={expected_entities} actual={entities}")
    missing = sorted(wanted - found.keys())
    if missing:
        raise CalculationError(f"实体流缺少证据handle: {', '.join(missing)}")
    return found, rows, entities


def _validate_evidence_entities(
    specs: list[dict[str, Any]], rows: dict[str, dict[str, Any]]
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    dimensions: dict[str, float] = {}
    audited: list[dict[str, Any]] = []
    for spec in specs:
        handle = str(spec["handle"]).upper()
        row = rows[handle]
        expected_type = spec.get("entity_type")
        if expected_type and row.get("entity_type") != expected_type:
            raise CalculationError(f"handle {handle}实体类型不符")
        for key in ("layer", "record_id", "candidate_physical_id"):
            if key in spec and row.get(key) != spec[key]:
                raise CalculationError(f"handle {handle}的{key}不符")
        if "closed" in spec and bool(row.get("closed")) is not bool(spec["closed"]):
            raise CalculationError(f"handle {handle}闭合状态不符")
        text_contains = spec.get("text_contains", [])
        if isinstance(text_contains, str):
            text_contains = [text_contains]
        text = str(row.get("text", ""))
        for fragment in text_contains:
            if fragment not in text:
                raise CalculationError(f"handle {handle}缺少预期文字: {fragment}")
        role = spec.get("role")
        if "adopted_mm" in spec:
            if not isinstance(role, str) or not role:
                raise CalculationError(f"handle {handle}尺寸缺少role")
            measured = require_number(row.get("measurement", row.get("raw_quantity")), f"handle {handle} measurement")
            expected_measured = require_number(spec.get("expected_measurement_mm"), f"handle {handle} expected_measurement_mm")
            tolerance = require_number(spec.get("tolerance_mm", 0.1), f"handle {handle} tolerance_mm", positive=False)
            if tolerance < 0 or abs(measured - expected_measured) > tolerance:
                raise CalculationError(f"handle {handle}原始尺寸不符: expected={expected_measured} actual={measured}")
            adopted = require_number(spec["adopted_mm"], f"handle {handle} adopted_mm")
            if abs(adopted - measured) > tolerance:
                raise CalculationError(f"handle {handle}采用尺寸超出显示/测量容差")
            if role in dimensions:
                raise CalculationError(f"尺寸role重复: {role}")
            dimensions[role] = adopted
        audited.append(
            {
                "handle": handle,
                "role": role,
                "entity_type": row.get("entity_type"),
                "layer": row.get("layer"),
                "record_id": row.get("record_id"),
                "candidate_physical_id": row.get("candidate_physical_id"),
                "measurement_mm": row.get("measurement", row.get("raw_quantity")) if "adopted_mm" in spec else None,
                "adopted_mm": spec.get("adopted_mm"),
                "text": text if text_contains else None,
            }
        )
    return dimensions, audited


def _extract_text_values(calculation: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, float]:
    values: dict[str, float] = {}
    for role, spec in calculation.get("text_values", {}).items():
        handle = str(spec.get("handle", "")).upper()
        if handle not in rows:
            raise CalculationError(f"文字数值{role}引用未知handle {handle}")
        pattern = spec.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise CalculationError(f"文字数值{role}缺少pattern")
        match = re.search(pattern, str(rows[handle].get("text", "")))
        if not match:
            raise CalculationError(f"文字数值{role}未匹配")
        captured = require_number(float(match.group(1)), f"文字数值{role}")
        expected = require_number(spec.get("expected_mm"), f"文字数值{role} expected_mm")
        if not math.isclose(captured, expected, rel_tol=0.0, abs_tol=1e-9):
            raise CalculationError(f"文字数值{role}不符: expected={expected} actual={captured}")
        values[role] = captured
    return values


def _result_scope(base: dict[str, str], object_name: str, specification: str, output_dimension: str) -> dict[str, str]:
    return {
        "业务专业": "建筑结构",
        "主要对象": object_name,
        "系统": "现浇混凝土与模板",
        "楼栋": base["building"],
        "楼层或区域": base["area"],
        "规格": specification,
        "输出维度": output_dimension,
    }


def _quantity(
    identifier: str,
    scope: dict[str, str],
    name: str,
    value: float | None,
    unit: str,
    status: str,
    explanation: str,
    sources: list[str],
    rules: list[str],
    unresolved: list[str],
    geometry_signature: str,
    *,
    zero_review: dict[str, Any] | None = None,
    extra_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "成果项编号": identifier,
        "范围": scope,
        "结果": {
            "名称": name,
            "数值": value,
            "数值状态": "已计算" if value is not None else "未确定",
            "计算式或说明": explanation,
            "精度": "米制输入保留原精度计算；展示值未替代中间值",
        },
        "单位": unit,
        "来源": sources,
        "规则": rules,
        "状态": status,
        "未决事项": unresolved,
        "试运行结果": True,
        "零值复核": zero_review,
        "专业检查": {
            "几何签名": geometry_signature,
            "构件几何编号": f"GEO-{geometry_signature[:16]}",
            "计量口径": "物理量" if "BOQ" not in identifier else "清单量",
        },
    }
    if extra_check:
        item["专业检查"].update(extra_check)
    return item


def calculate(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CalculationError(f"manifest_version必须为{MANIFEST_VERSION}；1.0案例输入须迁移object和逐面条件，不能沿用旧项目假定")
    case_id = manifest.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise CalculationError("case_id不能为空")
    scope = manifest.get("scope")
    if not isinstance(scope, dict) or any(not isinstance(scope.get(key), str) or not scope[key] for key in ("project", "building", "area")):
        raise CalculationError("scope必须包含project/building/area")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise CalculationError("source必须是对象")

    dwg_path = _resolve_input_path(source.get("dwg_path"), "冻结DWG")
    stream_path = _resolve_input_path(source.get("entity_stream_path"), "原生实体流")
    metadata_path = _resolve_input_path(source.get("metadata_path"), "缓存元数据")
    dwg_hash = _expect_hash(dwg_path, source.get("dwg_sha256"), "冻结DWG")
    stream_hash = _expect_hash(stream_path, source.get("entity_stream_sha256"), "原生实体流")
    metadata_hash = _expect_hash(metadata_path, source.get("metadata_sha256"), "缓存元数据")

    metadata = _load_json(metadata_path)
    metadata_info = metadata.get("metadata", {})
    expected_source_sha = str(source["dwg_sha256"]).lower()
    if str(metadata_info.get("source_sha256", "")).lower() != expected_source_sha:
        raise CalculationError("缓存元数据source_sha256与冻结DWG不一致")
    if str(metadata.get("entity_stream_sha256", "")).lower() != stream_hash.lower():
        raise CalculationError("缓存元数据entity_stream_sha256与当前实体流不一致")
    expected_unit = source.get("source_unit")
    if metadata.get("source_unit") != expected_unit:
        raise CalculationError("缓存元数据单位与清单不一致")
    if expected_unit != "Millimeters":
        raise CalculationError("当前计算类型只接受Millimeters来源")
    metadata_checks = {
        "adapter": (metadata_info.get("adapter"), source.get("expected_adapter")),
        "schema_version": (metadata_info.get("schema_version"), source.get("expected_schema_version")),
        "unit_status": (metadata.get("unit_status"), source.get("expected_unit_status")),
        "unit_basis": (metadata.get("unit_basis"), source.get("expected_unit_basis")),
    }
    for field, (actual, expected) in metadata_checks.items():
        if not isinstance(expected, str) or not expected or actual != expected:
            raise CalculationError(f"缓存元数据{field}与清单不一致")

    excluded_audit = []
    for excluded in source.get("excluded_drifted_files", []):
        path = _resolve_input_path(excluded.get("path"), "排除的漂移文件")
        actual = _expect_hash(path, excluded.get("actual_sha256"), "排除的漂移文件")
        if actual.lower() == expected_source_sha:
            raise CalculationError("排除文件与冻结来源哈希相同，排除理由不成立")
        excluded_audit.append({"path": str(path), "sha256": actual, "reason": excluded.get("reason", "来源身份不匹配")})

    evidence_specs = manifest.get("evidence_entities")
    if not isinstance(evidence_specs, list) or not evidence_specs:
        raise CalculationError("evidence_entities不能为空")
    expected_rows = int(require_number(source.get("expected_row_count"), "expected_row_count"))
    expected_entities = int(require_number(source.get("expected_entity_count"), "expected_entity_count"))
    rows, row_count, entity_count = _read_and_validate_stream(
        stream_path, expected_source_sha, expected_rows, expected_entities, evidence_specs
    )
    dimensions_mm, audited_entities = _validate_evidence_entities(evidence_specs, rows)

    calculation = manifest.get("calculation")
    if not isinstance(calculation, dict) or calculation.get("type") != SUPPORTED_CALCULATION:
        raise CalculationError(f"calculation.type必须为{SUPPORTED_CALCULATION}")
    text_values_mm = _extract_text_values(calculation, rows)

    required_dimensions = {
        "core_length",
        "core_width",
        "extension_left",
        "extension_right",
        "extension_top",
        "extension_bottom",
        "notch_length",
        "notch_width",
    }
    if set(dimensions_mm) != required_dimensions:
        missing = sorted(required_dimensions - set(dimensions_mm))
        extra = sorted(set(dimensions_mm) - required_dimensions)
        raise CalculationError(f"尺寸role不完整 missing={missing} extra={extra}")
    required_text = {"raft_thickness", "blinding_thickness", "blinding_offset"}
    if set(text_values_mm) != required_text:
        raise CalculationError("text_values必须闭合筏板厚、垫层厚和外扩")

    outline_handle = str(calculation.get("outline_handle", "")).upper()
    if outline_handle not in rows:
        raise CalculationError("outline_handle没有证据实体")
    outline = rows[outline_handle]
    if outline.get("entity_type") != "LWPOLYLINE" or not outline.get("closed"):
        raise CalculationError("目标轮廓必须是闭合LWPOLYLINE")
    try:
        geometry = json.loads(outline["geometry"])
        points = geometry["measurement_points"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CalculationError("目标轮廓缺少可读measurement_points") from exc
    coordinate_area_mm2, coordinate_perimeter_mm, orthogonal = _polygon_metrics(points)
    if not orthogonal:
        raise CalculationError("目标轮廓不是允许容差内的正交多边形")

    mm = dimensions_mm
    tv = text_values_mm
    total_length_m = (mm["core_length"] + mm["extension_left"] + mm["extension_right"]) / 1000.0
    total_width_m = (mm["core_width"] + mm["extension_top"] + mm["extension_bottom"]) / 1000.0
    notch_length_m = mm["notch_length"] / 1000.0
    notch_width_m = mm["notch_width"] / 1000.0
    raft_thickness_m = tv["raft_thickness"] / 1000.0
    blinding_thickness_m = tv["blinding_thickness"] / 1000.0
    blinding_offset_m = tv["blinding_offset"] / 1000.0
    if notch_length_m >= total_length_m or notch_width_m >= total_width_m:
        raise CalculationError("角缺口尺寸超出主体轮廓")

    annotated_area_m2 = total_length_m * total_width_m - notch_length_m * notch_width_m
    perimeter_segments_m = [
        total_length_m,
        total_width_m,
        total_length_m - notch_length_m,
        notch_width_m,
        notch_length_m,
        total_width_m - notch_width_m,
    ]
    annotated_perimeter_m = math.fsum(perimeter_segments_m)
    coordinate_area_m2 = coordinate_area_mm2 / 1_000_000.0
    coordinate_perimeter_m = coordinate_perimeter_mm / 1000.0
    area_difference_m2 = annotated_area_m2 - coordinate_area_m2
    perimeter_difference_m = annotated_perimeter_m - coordinate_perimeter_m
    area_cross_tolerance = require_number(calculation.get("coordinate_crosscheck_tolerance_m2", 0.01), "coordinate_crosscheck_tolerance_m2")
    perimeter_cross_tolerance = require_number(calculation.get("coordinate_perimeter_crosscheck_tolerance_m", 0.001), "coordinate_perimeter_crosscheck_tolerance_m")
    if abs(area_difference_m2) > area_cross_tolerance:
        raise CalculationError("标注尺寸面积与原生坐标面积差异超限")
    if abs(perimeter_difference_m) > perimeter_cross_tolerance:
        raise CalculationError("标注尺寸周长与原生坐标周长差异超限")

    raft_gross_m3 = annotated_area_m2 * raft_thickness_m
    raft_exterior_form_m2 = annotated_perimeter_m * raft_thickness_m
    blinding_area_m2, blinding_perimeter_m = orthogonal_offset(annotated_area_m2, annotated_perimeter_m, blinding_offset_m)
    blinding_gross_m3 = blinding_area_m2 * blinding_thickness_m
    blinding_side_candidate_m2 = blinding_perimeter_m * blinding_thickness_m

    obj = manifest.get('object', {})
    if any(not isinstance(obj.get(k), str) or not obj[k].strip() for k in ('id', 'name', 'blinding_id', 'blinding_name')):
        raise CalculationError('object须提供id/name/blinding_id/blinding_name')
    if obj['id'] == obj['blinding_id']:
        raise CalculationError('主体与垫层物理编号不得重复')
    # The supported shape is a single corner-notched rectangle, not an arbitrary
    # polygon with the same area/perimeter. Verify topology before offset formula.
    from shapely.geometry import Polygon, box
    polygon = Polygon([(float(p[0])/1000, float(p[1])/1000) for p in points])
    if not polygon.is_valid or polygon.is_empty:
        raise CalculationError('轮廓无效或自交')
    x0, y0, x1, y1 = polygon.bounds
    matched = False
    for length, width, nl, nw in ((total_length_m, total_width_m, notch_length_m, notch_width_m),
                                 (total_width_m, total_length_m, notch_width_m, notch_length_m)):
        if abs(x1-x0-length) > perimeter_cross_tolerance or abs(y1-y0-width) > perimeter_cross_tolerance:
            continue
        for left, bottom in ((True, True), (True, False), (False, True), (False, False)):
            xa, xb = (x0, x0+nl) if left else (x1-nl, x1)
            ya, yb = (y0, y0+nw) if bottom else (y1-nw, y1)
            expected = box(x0, y0, x1, y1).difference(box(xa, ya, xb, yb))
            if polygon.symmetric_difference(expected).area <= area_cross_tolerance:
                matched = True
    if not matched:
        raise CalculationError('轮廓不是尺寸链对应的单角缺口矩形')

    conditions = calculation.get('template_conditions', {})
    face_areas = dict(raft_exterior_side=raft_exterior_form_m2, raft_bottom=annotated_area_m2,
                      raft_top=annotated_area_m2, blinding_side=blinding_side_candidate_m2)
    if set(conditions) != set(face_areas):
        raise CalculationError('template_conditions须逐面覆盖外侧、底面、顶面、垫层侧面')
    for face, condition in conditions.items():
        if not isinstance(condition, dict) or condition.get('state') not in {'included', 'excluded', 'unresolved'}:
            raise CalculationError(f'{face}模板面状态无效')
        if not isinstance(condition.get('reason'), str) or not condition['reason'].strip():
            raise CalculationError(f'{face}缺少条件判读依据')
        handles = condition.get('evidence_handles')
        if not isinstance(handles, list) or not handles or any(str(h).upper() not in rows for h in handles):
            raise CalculationError(f'{face}必须引用当前来源证据实体')
        if condition['state'] != 'unresolved' and condition.get('coverage') != 'complete':
            raise CalculationError(f'{face}纳入或排除须有该面完整覆盖依据')
    geometry_signature = canonical_hash(dict(outline=points, dimensions_mm=dimensions_mm,
                                              text_values_mm=text_values_mm, object_id=obj['id']))
    calculations = dict(geometry_signature=geometry_signature,
        annotated=dict(total_length_m=total_length_m, total_width_m=total_width_m,
                       notch_length_m=notch_length_m, notch_width_m=notch_width_m,
                       raft_area_m2=annotated_area_m2, raft_perimeter_m=annotated_perimeter_m),
        coordinate_crosscheck=dict(area_m2=coordinate_area_m2, perimeter_m=coordinate_perimeter_mm/1000,
                                   area_difference_m2=area_difference_m2, perimeter_difference_m=perimeter_difference_m),
        results=dict(raft_gross_m3=raft_gross_m3, blinding_area_m2=blinding_area_m2,
                     blinding_perimeter_m=blinding_perimeter_m, blinding_gross_m3=blinding_gross_m3),
        face_conditions=conditions, face_geometry_m2=face_areas)
    evidence = dict(evidence_version='qto-concrete-formwork-evidence/2.0', case_id=case_id,
        source_identity=dict(dwg_path=str(dwg_path), dwg_sha256=dwg_hash,
            entity_stream_path=str(stream_path), entity_stream_sha256=stream_hash,
            metadata_path=str(metadata_path), metadata_sha256=metadata_hash,
            source_unit=expected_unit, row_count=row_count, entity_count=entity_count,
            excluded_drifted_files=excluded_audit),
        evidence_entities=audited_entities, calculations=calculations,
        project_rule_gate=manifest.get('project_rule_gate'), old_runtime_used=False)
    source_id, ev_id, rule_id = 'SRC-DRAWING', 'EV-GEOMETRY', 'R-PHYSICAL-GEOMETRY'
    sources = []
    for sid, path, digest, kind in ((source_id, dwg_path, dwg_hash, '冻结DWG'),
                                  ('SRC-STREAM', stream_path, stream_hash, '原生实体流'),
                                  ('SRC-METADATA', metadata_path, metadata_hash, '缓存元数据')):
        sources.append({'资料编号': sid, '路径': str(path), '资料类型': kind,
                        '版本身份': digest, '选择结论': '已选择', '原因': '本次来源身份核验通过',
                        '拟提取中性事实': '输入指定的轮廓、尺寸、文字和来源元数据',
                        '未使用影响': '缺少该来源时对应计算无法回溯'})
    evidence_catalog = [{'证据编号': ev_id, '资料编号': source_id, '来源类型': '源实体与尺寸交叉核验',
        '中性事实': json.dumps(dict(dimensions_mm=dimensions_mm, text_values_mm=text_values_mm), ensure_ascii=False),
        '定位': ';'.join(str(x['handle']) for x in audited_entities), '来源身份': dwg_hash,
        '提取方式': '重读实体流；核对轮廓、尺寸、文字和面条件', '质量状态': '工作计算',
        '限制': '仅名义几何，不自动推导局部净量、合同计量或施工条件'}]
    rules = [{'规则编号': rule_id, '规则名称': '同源几何体积与已识别接触面',
              '规则来源': '几何定义及本次输入的源面条件', '本次判定': '采用',
              '成熟状态': '试运行', '执行结果': '按输入逐项计算', '证据位置': ev_id}]
    results, details, unresolved = [], [], []
    for identifier, name, value, formula in (
        (obj['id'], obj['name'], raft_gross_m3, f'({total_length_m:g}×{total_width_m:g}-{notch_length_m:g}×{notch_width_m:g})×{raft_thickness_m:g}'),
        (obj['blinding_id'], obj['blinding_name'], blinding_gross_m3, f'({annotated_area_m2:g}+{annotated_perimeter_m:g}×{blinding_offset_m:g}+4×{blinding_offset_m:g}²)×{blinding_thickness_m:g}')):
        results.append(_quantity('QTY-CF-'+identifier.encode('utf8').hex()+'-GROSS', _result_scope(scope, name, '名义毛量', '物理体积'),
            name+'名义毛体积', value, 'm³', '已计算', formula, [ev_id], [rule_id], [], geometry_signature,
            extra_check={'对象编号': identifier, '毛量': True, '净量闭合': False}))
    for face, area in face_areas.items():
        condition = conditions[face]
        identifier = obj['blinding_id'] if face == 'blinding_side' else obj['id']
        name = obj['blinding_name'] if face == 'blinding_side' else obj['name']
        face_name = dict(raft_exterior_side='外侧面', raft_bottom='底面', raft_top='顶面', blinding_side='侧面')[face]
        qid = 'QTY-CF-'+identifier.encode('utf8').hex()+'-'+face
        extra = {'对象编号': identifier, '面': face, '面条件': condition}
        issues, zero = [], None
        if condition['state'] == 'unresolved':
            value, status = None, '证据不足'
            issues = ['U-'+qid]
            extra.update({'条件候选值': area, '条件候选单位': 'm²', '进入采用量': False})
            unresolved.append({'事项编号': issues[0], '类型': '缺资料', '说明': condition['reason'],
                               '当前处理': '停止受影响计算', '关联成果项': [qid],
                               '影响': name+face_name+'模板采用量未定', '所需输入': '该面接触对象与施工条件的完整证据'})
        else:
            value = area if condition['state'] == 'included' else 0.
            status = '已计算'
            if value == 0:
                zero = {'范围覆盖': name+face_name, '来源': [ev_id], '规则': [rule_id],
                        '复核结论': '已确认零值', '说明': condition['reason']}
        results.append(_quantity(qid, _result_scope(scope, name+face_name, '普通模板', '接触面积'),
            name+face_name+'普通模板', value, 'm²', status, condition['reason'], [ev_id], [rule_id],
            issues, geometry_signature, zero_review=zero, extra_check=extra))
    for item in results:
        details.append({'对象编号': item['成果项编号'], '计算项': item['结果']['名称'],
                        '计算式': item['结果']['计算式或说明'], '数值': item['结果']['数值'],
                        '单位': item['单位'], '状态': item['状态'], '证据': ';'.join(item['来源'])})
    result = {'契约版本': RESULT_VERSION, '任务范围': {'业务专业': '建筑结构', '主要物理对象': obj['name']+'及'+obj['blinding_name'],
                  '系统': '现浇混凝土与模板', '楼栋': scope['building'], '楼层或区域': scope['area'],
                  '图纸类型': '原生DWG中指定的构件轮廓与尺寸', '图纸版本': 'SHA-256 '+dwg_hash,
                  '输出维度': '名义毛体积和逐面普通模板接触面积'},
              '资料选择': sources, '证据目录': evidence_catalog, '本次规则清单': rules,
              '工程量结果': results, '整体未决事项': unresolved,
              '说明': '源图尺寸与单角缺口几何的工作计算。输出名义毛体积和输入已解释的面条件；不输出合同清单净量。'}
    return evidence, details, result


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["对象编号", "计算项", "计算式", "数值", "单位", "状态", "证据"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按证据锁定清单复算混凝土与模板配对工程量")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence-out", required=True, type=Path)
    parser.add_argument("--detail-csv", required=True, type=Path)
    parser.add_argument("--result-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest_path = args.manifest.resolve()
        _reject_legacy_runtime(manifest_path, "运行清单")
        manifest = _load_json(manifest_path)
        evidence, details, result = calculate(manifest)
        evidence["manifest_path"] = str(manifest_path)
        evidence["manifest_sha256"] = sha256_file(manifest_path)
        generator_path = Path(__file__).resolve()
        evidence["generator"] = {"path": str(generator_path), "sha256": sha256_file(generator_path)}
        _write_json(args.evidence_out.resolve(), evidence)
        _write_csv(args.detail_csv.resolve(), details)
        _write_json(args.result_out.resolve(), result)
        print(json.dumps({"status": "PASS", "case_id": manifest["case_id"], "result_count": len(result["工程量结果"]), "evidence_out": str(args.evidence_out.resolve()), "detail_csv": str(args.detail_csv.resolve()), "result_out": str(args.result_out.resolve())}, ensure_ascii=False))
        return 0
    except (CalculationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
