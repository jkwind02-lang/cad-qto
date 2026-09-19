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


MANIFEST_VERSION = "qto-concrete-formwork-manifest/1.0"
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
        raise CalculationError(f"manifest_version必须为{MANIFEST_VERSION}")
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

    conditions = calculation.get("template_conditions", {})
    expected_conditions = {
        "raft_exterior_side": "unresolved_requires_construction_input",
        "raft_bottom": "unresolved_local_bottom_topology",
        "raft_top": "confirmed_open_no_ordinary_formwork",
        "blinding_side": "unresolved_requires_construction_input",
    }
    if conditions != expected_conditions:
        raise CalculationError("template_conditions未按限定面闭合或错误补定")

    unresolved = manifest.get("unresolved")
    if not isinstance(unresolved, list) or not unresolved:
        raise CalculationError("真实案例必须列出未决事项")
    unresolved_map = {item.get("id"): item for item in unresolved if isinstance(item, dict)}
    required_unresolved = {
        "U-LOCAL-GEOMETRY",
        "U-CONCRETE-GRADE",
        "U-BLINDING-GRADE",
        "U-FORMWORK-TYPE",
        "U-BOTTOM-FORM-COVERAGE",
        "U-BLINDING-SIDE-FORM",
        "U-RULE-GATE",
    }
    if not required_unresolved.issubset(unresolved_map):
        raise CalculationError("未决事项没有覆盖局部几何、材料、模板条件和规则适用门")

    gate = manifest.get("project_rule_gate")
    if not isinstance(gate, dict) or gate.get("status") != "INPUT_REQUIRED":
        raise CalculationError("青海试运行的project_rule_gate必须保持INPUT_REQUIRED")
    geometry_signature = canonical_hash(
        {
            "outline": {
                "handle": outline_handle,
                "physical_id": outline.get("candidate_physical_id"),
                "points": points,
            },
            "dimensions_mm": dimensions_mm,
            "text_values_mm": text_values_mm,
            "calculation_type": SUPPORTED_CALCULATION,
        }
    )

    calculations = {
        "geometry_signature": geometry_signature,
        "annotated": {
            "total_length_m": total_length_m,
            "total_width_m": total_width_m,
            "notch_length_m": notch_length_m,
            "notch_width_m": notch_width_m,
            "raft_area_m2": annotated_area_m2,
            "perimeter_segments_m": perimeter_segments_m,
            "raft_perimeter_m": annotated_perimeter_m,
        },
        "coordinate_crosscheck": {
            "area_m2": coordinate_area_m2,
            "perimeter_m": coordinate_perimeter_m,
            "area_difference_m2": area_difference_m2,
            "perimeter_difference_m": perimeter_difference_m,
            "adoption": "采用图示标注尺寸；原生坐标只作质量交叉检查",
        },
        "results": {
            "raft_gross_m3": raft_gross_m3,
            "raft_exterior_contact_condition_candidate_m2": raft_exterior_form_m2,
            "raft_exterior_formwork_m2": None,
            "raft_bottom_ordinary_formwork_m2": None,
            "blinding_area_m2": blinding_area_m2,
            "blinding_perimeter_m": blinding_perimeter_m,
            "blinding_gross_m3": blinding_gross_m3,
            "blinding_side_condition_candidate_m2": blinding_side_candidate_m2,
            "boq_raft_m3": None,
            "boq_formwork_m2": None,
        },
    }

    evidence = {
        "evidence_version": "qto-concrete-formwork-evidence/1.0",
        "case_id": case_id,
        "source_identity": {
            "dwg_path": str(dwg_path),
            "dwg_sha256": dwg_hash,
            "entity_stream_path": str(stream_path),
            "entity_stream_sha256": stream_hash,
            "metadata_path": str(metadata_path),
            "metadata_sha256": metadata_hash,
            "source_unit": expected_unit,
            "row_count": row_count,
            "entity_count": entity_count,
            "excluded_drifted_files": excluded_audit,
        },
        "evidence_entities": audited_entities,
        "calculations": calculations,
        "project_rule_gate": gate,
        "unresolved": unresolved,
        "old_runtime_used": False,
    }

    details = [
        {"对象编号": "CF-RAFT-001", "计算项": "筏板平面名义毛面积", "计算式": "L×W-缺口长×缺口宽", "数值": annotated_area_m2, "单位": "m²", "状态": "已计算", "证据": "569D;57EE;5711;5A4F;5AEB;5AB5;5B1C;57AD;57C7"},
        {"对象编号": "CF-RAFT-001", "计算项": "筏板名义毛体积", "计算式": "A×0.500", "数值": raft_gross_m3, "单位": "m³", "状态": "部分计算", "证据": "CF-RAFT-001;819D"},
        {"对象编号": "CF-RAFT-001", "计算项": "筏板外侧若支模接触几何候选值", "计算式": "P×0.500", "数值": raft_exterior_form_m2, "单位": "m²", "状态": "条件值不采用", "证据": "CF-RAFT-001;819D;U-FORMWORK-TYPE"},
        {"对象编号": "CF-RAFT-001-FACE-BOTTOM", "计算项": "筏板底面普通模板待确认量", "计算式": "局部底面拓扑及基层关系未闭合", "数值": None, "单位": "m²", "状态": "证据不足", "证据": "819D;U-BOTTOM-FORM-COVERAGE"},
        {"对象编号": "CF-BLINDING-001", "计算项": "垫层外扩后名义面积", "计算式": "A+P×d+4d²", "数值": blinding_area_m2, "单位": "m²", "状态": "已计算", "证据": "CF-RAFT-001;819D"},
        {"对象编号": "CF-BLINDING-001", "计算项": "垫层名义毛体积", "计算式": "A'×0.100", "数值": blinding_gross_m3, "单位": "m³", "状态": "部分计算", "证据": "CF-RAFT-001;819D"},
        {"对象编号": "CF-BLINDING-001-FACE-SIDE", "计算项": "垫层侧模若支模条件值", "计算式": "P'×0.100", "数值": blinding_side_candidate_m2, "单位": "m²", "状态": "条件值不采用", "证据": "CF-BLINDING-001;U-BLINDING-SIDE-FORM"},
    ]

    source_id = "SRC-QH-PUMP-DWG"
    sources = [
        {
            "资料编号": source_id,
            "路径": str(dwg_path),
            "资料类型": "冻结DWG",
            "版本身份": f"SHA-256 {dwg_hash.upper()}",
            "选择结论": "已选择",
            "原因": "与原生实体流行内来源哈希和缓存元数据闭合",
            "拟提取中性事实": "筏板轮廓、图名、标注尺寸、厚度和垫层说明",
            "未使用影响": "局部下沉、沟槽、集水井和吸水槽净拓扑尚未闭合",
        },
        {
            "资料编号": "SRC-QH-PUMP-STREAM",
            "路径": str(stream_path),
            "资料类型": "AutoCAD原生实体流",
            "版本身份": f"SHA-256 {stream_hash.upper()}；来源 {dwg_hash.upper()}",
            "选择结论": "已选择",
            "原因": "原生图元、标注和文字可对象级定位",
            "拟提取中性事实": "11,033个Model实体中的指定唯一handle",
            "未使用影响": "只使用清单明确列出的中性实体，不继承任何旧专业结论",
        },
        {
            "资料编号": "SRC-QH-PUMP-METADATA",
            "路径": str(metadata_path),
            "资料类型": "CAD缓存元数据",
            "版本身份": f"SHA-256 {metadata_hash.upper()}",
            "选择结论": "已选择",
            "原因": "确认适配器、来源哈希、实体流哈希、单位和守恒",
            "拟提取中性事实": "source_unit=Millimeters、source_sha256、entity_stream_sha256、行数",
            "未使用影响": "unit_status为人工尺寸复核覆盖，正式成熟仍需更多来源验证",
        },
    ]
    for idx, excluded in enumerate(excluded_audit, 1):
        sources.append(
            {
                "资料编号": f"SRC-QH-PUMP-DRIFT-{idx}",
                "路径": excluded["path"],
                "资料类型": "同名漂移DWG",
                "版本身份": f"当前SHA-256 {excluded['sha256'].upper()}",
                "选择结论": "已排除",
                "原因": excluded["reason"],
                "拟提取中性事实": "无；仅保存排除身份",
                "未使用影响": "不影响冻结来源毛量；不得用其替换当前缓存来源",
            }
        )

    evidence_catalog = [
        {"证据编号": "EV-CF-SOURCE-IDENTITY", "来源类型": "文件与缓存身份", "资料编号": source_id, "中性事实": f"冻结DWG、元数据和实体流闭合；Model实体{entity_count}个", "定位": f"{dwg_path}；{metadata_path}；{stream_path}", "来源身份": f"DWG {dwg_hash.upper()}；实体流 {stream_hash.upper()}", "提取方式": "本次重新计算文件哈希并逐行核对source_sha256", "质量状态": "身份通过，漂移同名DWG已排除", "限制": "缓存单位为经尺寸复核的人工覆盖", "缓存编号": "CACHE-QH-PUMP-NATIVE"},
        {"证据编号": "EV-CF-RAFT-OUTLINE", "来源类型": "CAD原生闭合轮廓", "资料编号": "SRC-QH-PUMP-STREAM", "中性事实": f"handle {outline_handle}为唯一目标正交闭合轮廓，physical_id={outline.get('candidate_physical_id')}", "定位": f"Model；handle {outline_handle}；record {outline.get('record_id')}", "来源身份": f"实体流 {stream_hash.upper()}", "提取方式": "读取原生LWPOLYLINE并计算鞋带面积/周长交叉值", "质量状态": "对象和几何通过", "限制": "坐标量距只作交叉检查，采用图示标注尺寸", "缓存编号": "CACHE-QH-PUMP-NATIVE"},
        {"证据编号": "EV-CF-RAFT-DIMS", "来源类型": "CAD标注尺寸与通用注释", "资料编号": "SRC-QH-PUMP-STREAM", "中性事实": "主体长53700mm、主体宽6800mm、两侧750mm、上下600mm、角缺口2700×1000mm；图纸要求不得量图并以标注尺寸为准，除标高外尺寸单位为mm", "定位": "Model；尺寸handles 57EE、5711、5A4F、5AEB、5AB5、5B1C、57AD、57C7；通用注释19A49；单位说明13772", "来源身份": f"实体流 {stream_hash.upper()}", "提取方式": "原生DIMENSION和TEXT/MTEXT逐项核验；750mm按显示精度采用且在0.1mm容差内", "质量状态": "尺寸链、采用优先级和单位闭合", "限制": "只适用于筏板名义外轮廓", "缓存编号": "CACHE-QH-PUMP-NATIVE"},
        {"证据编号": "EV-CF-RAFT-NOTE", "来源类型": "CAD直接文字", "资料编号": "SRC-QH-PUMP-STREAM", "中性事实": "筏板厚500mm；垫层厚100mm并超出基础外边100mm", "定位": "Model；handle 819D", "来源身份": f"实体流 {stream_hash.upper()}", "提取方式": "原生TEXT并用受控表达式提取数值", "质量状态": "厚度和外扩闭合", "限制": "文字给C20但局部详图存在C15范围冲突", "缓存编号": "CACHE-QH-PUMP-NATIVE"},
        {"证据编号": "EV-CF-RAFT-CALC", "来源类型": "确定性计算证据", "资料编号": source_id, "中性事实": f"同一几何签名{geometry_signature.upper()}生成筏板毛体积与外侧接触面", "定位": "本次计算证据JSON calculations.annotated/results", "来源身份": f"清单{canonical_hash(manifest).upper()}", "提取方式": "标注尺寸法计算，原生坐标法交叉复核", "质量状态": "公式闭合，局部净量受未决限制", "限制": "不是筏板完整净量", "缓存编号": None},
        {"证据编号": "EV-CF-BLINDING-CALC", "来源类型": "确定性计算证据", "资料编号": source_id, "中性事实": "正交轮廓尖角外扩100mm后计算垫层名义面积、体积和侧模条件值", "定位": "本次计算证据JSON calculations.results", "来源身份": f"几何签名 {geometry_signature.upper()}", "提取方式": "A'=A+P×d+4d²，P'=P+8d", "质量状态": "几何闭合，材料分类和侧模条件未闭合", "限制": "侧模条件值不进入采用量", "缓存编号": None},
        {"证据编号": "EV-CF-TEMPLATE-FACES", "来源类型": "构件面关系", "资料编号": source_id, "中性事实": "筏板外侧竖面几何可算但成型方式未确认；局部底面拓扑及基层关系未闭合；顶面敞开；垫层侧面支模条件未知", "定位": "运行清单 calculation.template_conditions；图纸handles 819D、6392、7A60、771F、7724、56AB", "来源身份": f"几何签名 {geometry_signature.upper()}", "提取方式": "按面记录几何、接触对象和决定性施工条件；未知面不形成工程量", "质量状态": "外侧候选值、底面未决和垫层侧面条件值分列", "限制": "当前没有一个模板面满足正式采用条件", "缓存编号": None},
        {"证据编号": "EV-CF-UNRESOLVED", "来源类型": "CAD局部对象、标高与材料冲突", "资料编号": "SRC-QH-PUMP-STREAM", "中性事实": "存在排水沟说明、-5.800/-6.450/-6.800非均匀标高、1200×1200闭合局部对象及C20/C15垫层标注冲突", "定位": "Model；handles 6392、7A60、771F、7724、56AB、819D、7715、6D96", "来源身份": f"实体流 {stream_hash.upper()}", "提取方式": "原生TEXT与LWPOLYLINE逐项核验，只作为停止净量和材料归类的证据", "质量状态": "未决事实已定位", "限制": "尚不能形成局部区域、侧面和材料的完整拓扑", "缓存编号": "CACHE-QH-PUMP-NATIVE"},
        {"证据编号": "EV-CF-RULE-GATE", "来源类型": "项目计量规则适用门", "资料编号": source_id, "中性事实": "项目合同采用标准和地方补充规则未在本次证据链闭合", "定位": "运行清单 project_rule_gate", "来源身份": f"清单 {canonical_hash(manifest).upper()}", "提取方式": "核对地区、招标/合同日期和合同标准字段", "质量状态": "INPUT_REQUIRED", "限制": "不形成项目清单净量", "缓存编号": None},
    ]

    rules = [
        {"规则编号": "R-CF-001", "规则名称": "任务范围与来源身份门", "规则来源": "重构任务书及公共缓存身份要求", "本次判定": "采用", "成熟状态": "试运行", "执行结果": "冻结DWG、元数据和原生实体流哈希闭合，排除同名漂移DWG", "证据位置": "EV-CF-SOURCE-IDENTITY"},
        {"规则编号": "R-CF-002", "规则名称": "同一构件几何与四种口径", "规则来源": "重构任务书第4.1、13.2节；本Skill计算与证据流程", "本次判定": "采用", "成熟状态": "试运行", "执行结果": "同一几何签名同时生成混凝土与模板链", "证据位置": "EV-CF-RAFT-OUTLINE、EV-CF-RAFT-CALC、EV-CF-TEMPLATE-FACES"},
        {"规则编号": "R-CF-003", "规则名称": "物理混凝土实体量", "规则来源": "实体体积定义；本Skill计算与证据流程", "本次判定": "采用", "成熟状态": "试运行", "执行结果": "形成筏板和垫层名义毛量；局部净量保持未决", "证据位置": "EV-CF-RAFT-CALC、EV-CF-BLINDING-CALC"},
        {"规则编号": "R-CF-004", "规则名称": "模板实际接触面", "规则来源": "实际接触面几何定义；本Skill接触面规则；GB/T 50854-2024 E.5.1仅作现行清单候选交叉参考", "本次判定": "证据不足", "成熟状态": "候选", "执行结果": "外侧成型方式、局部底面基层关系和垫层侧面支模条件未闭合，模板工程量均停止", "证据位置": "EV-CF-TEMPLATE-FACES、EV-CF-UNRESOLVED"},
        {"规则编号": "R-CF-FT-001", "规则名称": "筏板名义毛量", "规则来源": "闭合平面和棱柱体积定义；本Skill基础与垫层规则", "本次判定": "采用", "成熟状态": "试运行", "执行结果": "按标注尺寸形成带角缺口筏板面积、周长和名义毛体积；不形成模板采用量", "证据位置": "EV-CF-RAFT-DIMS、EV-CF-RAFT-NOTE、EV-CF-RAFT-CALC"},
        {"规则编号": "R-CF-FT-003", "规则名称": "筏板外侧模板需成型方式证据", "规则来源": "实际接触面定义；本Skill基础与垫层规则", "本次判定": "证据不足", "成熟状态": "候选", "执行结果": "外侧竖面几何候选值63.200m²可复算，但普通模板、胎模或其他成型方式未确认，正式结果为null", "证据位置": "EV-CF-RAFT-CALC、EV-CF-TEMPLATE-FACES"},
        {"规则编号": "R-CF-FT-002", "规则名称": "正交轮廓垫层等距外扩", "规则来源": "正交简单多边形外扩几何；本Skill基础与垫层规则", "本次判定": "采用", "成熟状态": "试运行", "执行结果": "100mm尖角外扩后形成垫层名义面积、周长和毛体积", "证据位置": "EV-CF-RAFT-OUTLINE、EV-CF-RAFT-NOTE、EV-CF-BLINDING-CALC"},
        {"规则编号": "R-CF-ZERO-001", "规则名称": "零模板量证明", "规则来源": "公共通用成果字段与状态；实际接触面定义", "本次判定": "证据不足", "成熟状态": "候选", "执行结果": "局部底面拓扑和基层关系未完整覆盖，不形成筏板底面零值", "证据位置": "EV-CF-TEMPLATE-FACES、EV-CF-UNRESOLVED"},
        {"规则编号": "R-CF-COND-001", "规则名称": "条件性模板不进入采用量", "规则来源": "实际接触面定义；公共未知量处理", "本次判定": "采用", "成熟状态": "试运行", "执行结果": "筏板外侧和垫层侧面只保留条件候选值，筏板底面保持未决，三个正式结果均为null", "证据位置": "EV-CF-RAFT-CALC、EV-CF-BLINDING-CALC、EV-CF-TEMPLATE-FACES"},
        {"规则编号": "R-CF-007", "规则名称": "项目清单规则适用门", "规则来源": "GB/T 50854-2024实施公告、地方实施文件及合同优先关系", "本次判定": "证据不足", "成熟状态": "候选", "执行结果": "当前项目合同采用标准未闭合，清单量停止", "证据位置": "EV-CF-RULE-GATE"},
    ]

    common_phys_rules = ["R-CF-001", "R-CF-002", "R-CF-003", "R-CF-FT-001"]
    q_scope = scope
    results = [
        _quantity("QTY-CF-RAFT-GROSS", _result_scope(q_scope, "消防泵房筏板", "混凝土强度待确认；名义厚500mm", "物理混凝土名义毛量"), "消防泵房筏板名义毛体积", raft_gross_m3, "m³", "部分计算", "[(53.7+0.75+0.75)×(6.8+0.6+0.6)-2.7×1.0]×0.500", ["EV-CF-RAFT-OUTLINE", "EV-CF-RAFT-DIMS", "EV-CF-RAFT-NOTE", "EV-CF-RAFT-CALC", "EV-CF-UNRESOLVED"], common_phys_rules, ["U-LOCAL-GEOMETRY", "U-CONCRETE-GRADE"], geometry_signature, extra_check={"对象编号": "CF-RAFT-001", "毛量": True, "净量闭合": False}),
        _quantity("QTY-CF-RAFT-EXTERIOR-FORM", _result_scope(q_scope, "消防泵房筏板外侧面", "普通模板、胎模或其他成型方式待确认", "外侧模板接触面积"), "消防泵房筏板外侧模板接触面积待确认", None, "m²", "证据不足", "外侧竖面几何候选值=126.400×0.500=63.200m²；成型方式未确认，当前不采用", ["EV-CF-RAFT-OUTLINE", "EV-CF-RAFT-NOTE", "EV-CF-RAFT-CALC", "EV-CF-TEMPLATE-FACES"], ["R-CF-004", "R-CF-FT-003", "R-CF-COND-001"], ["U-FORMWORK-TYPE"], geometry_signature, extra_check={"对象编号": "CF-RAFT-001-FACE-EXTERIOR", "条件候选值": raft_exterior_form_m2, "条件候选单位": "m²", "进入采用量": False}),
        _quantity("QTY-CF-RAFT-BOTTOM-FORM", _result_scope(q_scope, "消防泵房筏板底面", "局部底面和基层关系未完整闭合", "底面普通模板接触面积"), "消防泵房筏板底面普通模板量待确认", None, "m²", "证据不足", "一般筏板底面说明有垫层，但局部下沉、沟槽、井槽底面及基层关系未完整覆盖，不能证明全范围零值", ["EV-CF-RAFT-NOTE", "EV-CF-TEMPLATE-FACES", "EV-CF-UNRESOLVED"], ["R-CF-004", "R-CF-ZERO-001", "R-CF-COND-001"], ["U-BOTTOM-FORM-COVERAGE"], geometry_signature, extra_check={"对象编号": "CF-RAFT-001-FACE-BOTTOM", "进入采用量": False, "零值已确认": False}),
        _quantity("QTY-CF-BLINDING-GROSS", _result_scope(q_scope, "消防泵房筏板垫层", "厚100mm；强度适用范围冲突", "物理混凝土名义毛量"), "消防泵房筏板垫层名义毛体积", blinding_gross_m3, "m³", "部分计算", "[438.900+126.400×0.100+4×0.100²]×0.100", ["EV-CF-RAFT-OUTLINE", "EV-CF-RAFT-NOTE", "EV-CF-BLINDING-CALC", "EV-CF-UNRESOLVED"], ["R-CF-001", "R-CF-002", "R-CF-003", "R-CF-FT-002"], ["U-LOCAL-GEOMETRY", "U-BLINDING-GRADE"], geometry_signature, extra_check={"对象编号": "CF-BLINDING-001", "毛量": True, "净量闭合": False}),
        _quantity("QTY-CF-BLINDING-SIDE-FORM", _result_scope(q_scope, "消防泵房筏板垫层侧面", "是否支模待确认", "条件性模板面积"), "消防泵房筏板垫层侧模待确认量", None, "m²", "证据不足", "若确认支模，候选几何值=(126.400+8×0.100)×0.100=12.720m²；当前不采用", ["EV-CF-BLINDING-CALC", "EV-CF-TEMPLATE-FACES"], ["R-CF-COND-001"], ["U-BLINDING-SIDE-FORM"], geometry_signature, extra_check={"对象编号": "CF-BLINDING-001-FACE-SIDE", "条件候选值": blinding_side_candidate_m2, "条件候选单位": "m²", "进入采用量": False}),
        _quantity("QTY-CF-BOQ-RAFT", _result_scope(q_scope, "消防泵房筏板", "项目采用标准待确认", "清单混凝土净量"), "消防泵房筏板清单工程量待确认", None, "m³", "待确认", "项目清单标准、地方规则及局部下沉扣减未闭合", ["EV-CF-RULE-GATE", "EV-CF-RAFT-CALC"], ["R-CF-007"], ["U-RULE-GATE", "U-LOCAL-GEOMETRY"], geometry_signature),
        _quantity("QTY-CF-BOQ-FORM", _result_scope(q_scope, "消防泵房筏板及垫层模板", "项目采用标准和各面成型方式待确认", "清单模板面积"), "消防泵房筏板及垫层清单模板量待确认", None, "m²", "待确认", "项目清单标准、外侧成型方式、局部底面基层关系和垫层侧面支模条件均未闭合", ["EV-CF-RULE-GATE", "EV-CF-TEMPLATE-FACES"], ["R-CF-007"], ["U-RULE-GATE", "U-FORMWORK-TYPE", "U-BOTTOM-FORM-COVERAGE", "U-BLINDING-SIDE-FORM"], geometry_signature),
    ]

    overall_unresolved = [
        {"事项编号": item["id"], "类型": item["type"], "关联成果项": item["related_results"], "说明": item["description"], "影响": item["impact"], "所需输入": item["required_input"], "当前处理": item["handling"]}
        for item in unresolved
    ]
    result = {
        "契约版本": RESULT_VERSION,
        "任务范围": {
            "业务专业": "建筑结构",
            "主要物理对象": "消防泵房筏板及其100mm垫层",
            "系统": "现浇混凝土与模板",
            "楼栋": scope["building"],
            "楼层或区域": scope["area"],
            "图纸类型": "消防泵房结构施工图；筏板基础结构平面布置图",
            "图纸版本": f"冻结DWG SHA-256 {dwg_hash.upper()}",
            "输出维度": "物理名义毛体积、限定模板接触面、条件量和清单口径未决",
            "纳入项": ["筏板名义外轮廓与500mm厚度", "筏板外侧和底面模板关系", "100mm厚、外扩100mm垫层"],
            "排除项": ["排水沟、集水井、吸水槽及局部下沉完整净拓扑", "钢筋、土方、防水、价格和成本", "旧项目计算脚本、旧结果和旧验证"],
        },
        "资料选择": sources,
        "证据目录": evidence_catalog,
        "本次规则清单": rules,
        "工程量结果": results,
        "整体未决事项": overall_unresolved,
        "说明": "本成果是qto-concrete-formwork候选Skill的青海消防泵房试运行。采用冻结DWG和原生实体流重新前算；名义毛量不等于完整净量，清单口径未确认，旧成果只允许在计算完成后后验对照。",
    }
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
