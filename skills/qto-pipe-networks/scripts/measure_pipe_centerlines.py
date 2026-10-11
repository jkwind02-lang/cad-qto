#!/usr/bin/env python3
"""Deterministically sum an explicitly approved pipe-centerline object set.

This script deliberately performs no professional discovery. The caller must
provide a reviewed spatial scope, exact layer-to-system mapping, unit evidence,
allowed entity types, and allowed geometry methods in a run manifest.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from qto_cad_source_contract import check_unit_factor


MANIFEST_VERSION = "qto-pipe-centerline-run/1.0"
OUTPUT_VERSION = "qto-pipe-centerline-evidence/1.0"
EVIDENCE_RE = re.compile(r"^EV-[A-Za-z0-9._-]+$")
RULE_RE = re.compile(r"^R-[A-Za-z0-9._-]+$")
HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ManifestError(ValueError):
    pass


def fail(message: str) -> None:
    raise ManifestError(message)


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        fail(f"{label} 必须是非空数组")
    return value


def require_identifiers(value: Any, label: str, pattern: re.Pattern[str]) -> list[str]:
    items = [require_text(item, label) for item in require_list(value, label)]
    invalid = [item for item in items if not pattern.fullmatch(item)]
    if invalid:
        fail(f"{label} 存在无效编号: {invalid}")
    return items


def require_hash(value: Any, label: str) -> str:
    digest = require_text(value, label).lower()
    if not HEX64_RE.fullmatch(digest):
        fail(f"{label} 必须是 64 位 SHA-256")
    return digest


def to_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        fail(f"{label} 必须是数值")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(f"{label} 必须是数值")
    if not number.is_finite():
        fail(f"{label} 必须是有限数值")
    return number


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 JSON {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"JSON 顶层必须是对象: {path}")
    return value


def forbidden_runtime_path(path: Path) -> bool:
    normalized = str(path.resolve()).lower().replace("/", "\\")
    markers = (
        "\\skills-disabled\\",
        "construction-qto-legacy",
        "\\agents\\skills\\construction-qto\\",
        "\\codex\\skills\\construction-qto\\",
    )
    return any(marker in normalized for marker in markers)


def open_ndjson(path: Path) -> Iterable[str]:
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            yield from handle
    else:
        with path.open("rt", encoding="utf-8") as handle:
            yield from handle


def parse_bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        numbers = [float(value[index]) for index in range(4)]
    except (TypeError, ValueError):
        return None
    return numbers if all(math.isfinite(number) for number in numbers) else None


def inside_half_open(x: float, y: float, bounds: dict[str, Decimal]) -> bool:
    return (
        bounds["xmin"] <= Decimal(str(x)) < bounds["xmax_exclusive"]
        and bounds["ymin"] <= Decimal(str(y)) < bounds["ymax_exclusive"]
    )


def bbox_inside_half_open(bbox: list[float], bounds: dict[str, Decimal]) -> bool:
    return (
        bounds["xmin"] <= Decimal(str(bbox[0]))
        and Decimal(str(bbox[2])) < bounds["xmax_exclusive"]
        and bounds["ymin"] <= Decimal(str(bbox[1]))
        and Decimal(str(bbox[3])) < bounds["ymax_exclusive"]
    )


def bbox_intersects_half_open(bbox: list[float], bounds: dict[str, Decimal]) -> bool:
    return (
        Decimal(str(bbox[2])) >= bounds["xmin"]
        and Decimal(str(bbox[0])) < bounds["xmax_exclusive"]
        and Decimal(str(bbox[3])) >= bounds["ymin"]
        and Decimal(str(bbox[1])) < bounds["ymax_exclusive"]
    )


def json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总显式选定的管道中心线中性事实")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--details", required=True, type=Path)
    args = parser.parse_args()

    try:
        manifest_path = args.manifest.resolve()
        manifest = load_json(manifest_path)
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            fail(f"manifest_version 必须为 {MANIFEST_VERSION}")

        calculation_id = require_text(manifest.get("calculation_id"), "calculation_id")
        source = manifest.get("source")
        selection = manifest.get("selection")
        if not isinstance(source, dict) or not isinstance(selection, dict):
            fail("source 和 selection 必须是对象")

        source_file_path = Path(require_text(source.get("source_file"), "source.source_file")).resolve()
        stream_path = Path(require_text(source.get("entity_stream"), "source.entity_stream")).resolve()
        metadata_path = Path(require_text(source.get("metadata"), "source.metadata")).resolve()
        output_path = args.output.resolve()
        details_path = args.details.resolve()
        if any(forbidden_runtime_path(path) for path in (source_file_path, stream_path, metadata_path)):
            fail("运行清单不得读取旧 Skill、旧归档或已停用 construction-qto")
        if not source_file_path.is_file() or not stream_path.is_file() or not metadata_path.is_file():
            fail("原始源文件、中性图元流或元数据文件不存在")
        protected_paths = {manifest_path, source_file_path, stream_path, metadata_path}
        if output_path == details_path or output_path in protected_paths or details_path in protected_paths:
            fail("输出 JSON、对象明细和输入文件必须使用互不相同的路径")

        expected_source_hash = require_hash(source.get("expected_source_sha256"), "source.expected_source_sha256")
        expected_stream_hash = require_hash(source.get("expected_stream_sha256"), "source.expected_stream_sha256")
        source_id = require_text(source.get("source_id"), "source.source_id")
        cache_id = require_text(source.get("cache_id"), "source.cache_id")
        actual_source_hash = sha256(source_file_path)
        if actual_source_hash != expected_source_hash:
            fail(f"原始源文件 SHA-256 不一致: {actual_source_hash}")
        actual_stream_hash = sha256(stream_path)
        if actual_stream_hash != expected_stream_hash:
            fail(f"图元流 SHA-256 不一致: {actual_stream_hash}")

        metadata = load_json(metadata_path)
        metadata_source_hash = str(metadata.get("metadata", {}).get("source_sha256", "")).lower()
        metadata_stream_hash = str(metadata.get("entity_stream_sha256", "")).lower()
        if metadata_source_hash != expected_source_hash:
            fail("元数据中的原图 SHA-256 与运行清单不一致")
        if metadata_stream_hash != expected_stream_hash:
            fail("元数据中的图元流 SHA-256 与当前图元流不一致")

        layout = require_text(selection.get("layout"), "selection.layout")
        raw_bounds = selection.get("bounds")
        if not isinstance(raw_bounds, dict):
            fail("selection.bounds 必须是对象")
        bounds = {
            key: to_decimal(raw_bounds.get(key), f"selection.bounds.{key}")
            for key in ("xmin", "xmax_exclusive", "ymin", "ymax_exclusive")
        }
        if bounds["xmin"] >= bounds["xmax_exclusive"] or bounds["ymin"] >= bounds["ymax_exclusive"]:
            fail("空间半开区间上下界无效")

        raw_unit = require_text(selection.get("raw_unit"), "selection.raw_unit")
        output_unit = require_text(selection.get("output_unit"), "selection.output_unit")
        conversion_factor = to_decimal(selection.get("conversion_factor"), "selection.conversion_factor")
        if conversion_factor <= 0:
            fail("selection.conversion_factor 必须大于 0")
        try:
            check_unit_factor(raw_unit, output_unit, float(conversion_factor))
        except ValueError as exc:
            fail(str(exc))
        if str(metadata.get("source_unit", "")) != raw_unit:
            fail("元数据 source_unit 与运行清单 raw_unit 不一致")

        scope_evidence_ids = require_identifiers(
            selection.get("scope_evidence_ids"), "selection.scope_evidence_ids", EVIDENCE_RE
        )
        unit_evidence_ids = require_identifiers(
            selection.get("unit_evidence_ids"), "selection.unit_evidence_ids", EVIDENCE_RE
        )
        rule_ids = require_identifiers(selection.get("rule_ids"), "selection.rule_ids", RULE_RE)
        allowed_entity_types = set(
            require_text(item, "selection.allowed_entity_types")
            for item in require_list(selection.get("allowed_entity_types"), "selection.allowed_entity_types")
        )
        allowed_geometry_methods = set(
            require_text(item, "selection.allowed_geometry_methods")
            for item in require_list(selection.get("allowed_geometry_methods"), "selection.allowed_geometry_methods")
        )
        allowed_confidence = set(
            require_text(item, "selection.allowed_confidence")
            for item in require_list(selection.get("allowed_confidence"), "selection.allowed_confidence")
        )

        systems = require_list(selection.get("systems"), "selection.systems")
        layer_to_system: dict[str, str] = {}
        system_evidence: dict[str, list[str]] = {}
        for index, item in enumerate(systems):
            if not isinstance(item, dict):
                fail(f"selection.systems[{index}] 必须是对象")
            name = require_text(item.get("name"), f"selection.systems[{index}].name")
            evidence_ids = require_identifiers(
                item.get("evidence_ids"), f"selection.systems[{index}].evidence_ids", EVIDENCE_RE
            )
            system_evidence[name] = evidence_ids
            for layer in require_list(item.get("layers"), f"selection.systems[{index}].layers"):
                layer_name = require_text(layer, f"selection.systems[{index}].layers")
                if layer_name in layer_to_system:
                    fail(f"图层 {layer_name} 被重复映射到多个系统")
                layer_to_system[layer_name] = name

        limitations = manifest.get("limitations", [])
        if not isinstance(limitations, list) or any(not isinstance(item, str) or not item.strip() for item in limitations):
            fail("limitations 必须是文本数组")

        details: list[dict[str, Any]] = []
        totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        method_counts: Counter[str] = Counter()
        excluded_same_layer: Counter[str] = Counter()
        duplicate_record_ids: list[str] = []
        duplicate_physical_ids: list[str] = []
        boundary_crossings: list[str] = []
        seen_record_ids: set[str] = set()
        seen_physical_ids: set[str] = set()

        for line_number, line in enumerate(open_ndjson(stream_path), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"图元流第 {line_number} 行不是有效 JSON: {exc}")
            if not isinstance(row, dict):
                continue
            if row.get("row_type") != "entity" or row.get("layout") != layout:
                continue
            layer = row.get("layer")
            if layer not in layer_to_system:
                continue
            x, y = row.get("x"), row.get("y")
            if not isinstance(x, (int, float)) or isinstance(x, bool) or not isinstance(y, (int, float)) or isinstance(y, bool):
                bbox = parse_bbox(row.get("bbox"))
                if (
                    row.get("outcome") == "extracted"
                    and row.get("measurement_shape") == "centerline"
                    and bbox is not None
                    and bbox_intersects_half_open(bbox, bounds)
                ):
                    fail(f"空间窗相交的中心线缺少数值 x/y 锚点: {row.get('record_id', line_number)}")
                continue
            if not inside_half_open(float(x), float(y), bounds):
                bbox = parse_bbox(row.get("bbox"))
                if (
                    row.get("outcome") == "extracted"
                    and row.get("measurement_shape") == "centerline"
                    and bbox is not None
                    and bbox_intersects_half_open(bbox, bounds)
                ):
                    boundary_crossings.append(str(row.get("record_id") or f"line-{line_number}"))
                continue
            if row.get("outcome") != "extracted":
                excluded_same_layer["outcome_not_extracted"] += 1
                continue
            if row.get("measurement_shape") != "centerline":
                excluded_same_layer["not_centerline"] += 1
                continue

            record_id = require_text(row.get("record_id"), f"图元流第 {line_number} 行 record_id")
            entity_type = require_text(row.get("entity_type"), f"{record_id}.entity_type")
            geometry_method = require_text(row.get("geometry_method"), f"{record_id}.geometry_method")
            confidence = require_text(row.get("confidence"), f"{record_id}.confidence")
            if entity_type not in allowed_entity_types:
                fail(f"{record_id} 的中心线实体类型 {entity_type} 未经运行清单允许")
            if geometry_method not in allowed_geometry_methods:
                fail(f"{record_id} 的几何方法 {geometry_method} 未经运行清单允许")
            if confidence not in allowed_confidence:
                fail(f"{record_id} 的置信等级 {confidence} 未经运行清单允许")
            if row.get("raw_unit") != raw_unit:
                fail(f"{record_id} 的原始单位与运行清单不一致")
            if str(row.get("source_sha256", "")).lower() != expected_source_hash:
                fail(f"{record_id} 的来源图纸 SHA-256 不一致")

            raw_length = to_decimal(row.get("raw_length", row.get("len")), f"{record_id}.raw_length")
            if raw_length <= 0:
                fail(f"{record_id} 的中心线长度必须大于 0")
            bbox = parse_bbox(row.get("bbox"))
            if bbox is None:
                fail(f"{record_id} 缺少可核验 bbox")
            if not bbox_inside_half_open(bbox, bounds):
                boundary_crossings.append(record_id)

            physical_id = require_text(
                row.get("candidate_physical_id") or record_id, f"{record_id}.candidate_physical_id"
            )
            if record_id in seen_record_ids:
                duplicate_record_ids.append(record_id)
            seen_record_ids.add(record_id)
            if physical_id in seen_physical_ids:
                duplicate_physical_ids.append(physical_id)
            seen_physical_ids.add(physical_id)

            system = layer_to_system[layer]
            output_value = raw_length * conversion_factor
            totals[system] += raw_length
            counts[system] += 1
            type_counts[entity_type] += 1
            method_counts[geometry_method] += 1
            details.append(
                {
                    "record_id": record_id,
                    "entity_handle": row.get("entity_handle", ""),
                    "candidate_physical_id": physical_id,
                    "layout": layout,
                    "layer": layer,
                    "system": system,
                    "entity_type": entity_type,
                    "geometry_method": geometry_method,
                    "confidence": confidence,
                    "raw_length": str(raw_length),
                    "raw_unit": raw_unit,
                    "output_value": str(output_value),
                    "output_unit": output_unit,
                    "x": str(x),
                    "y": str(y),
                    "bbox": json.dumps(bbox, ensure_ascii=False, separators=(",", ":")),
                    "source_sha256": expected_source_hash,
                }
            )

        if not details:
            fail("显式选择条件没有得到任何中心线对象")
        if duplicate_record_ids:
            fail(f"发现重复 record_id: {sorted(set(duplicate_record_ids))[:10]}")
        if duplicate_physical_ids:
            fail(f"发现重复物理对象: {sorted(set(duplicate_physical_ids))[:10]}")
        if boundary_crossings:
            fail(f"发现跨越空间边界的已选对象: {boundary_crossings[:10]}")

        details.sort(key=lambda item: (item["system"], item["layer"], item["record_id"]))
        signature_material = "\n".join(
            f"{item['record_id']}|{item['raw_length']}|{item['layer']}|{item['system']}" for item in details
        )
        selection_signature = hashlib.sha256(signature_material.encode("utf-8")).hexdigest()
        total_raw = sum(totals.values(), Decimal("0"))

        result = {
            "evidence_version": OUTPUT_VERSION,
            "calculation_id": calculation_id,
            "status": "CALCULATED",
            "meaning": "只表示运行清单显式选定对象的确定性中心线求和，不自动表示完整安装量或清单量",
            "source": {
                "source_id": source_id,
                "cache_id": cache_id,
                "source_file": str(source_file_path),
                "entity_stream": str(stream_path),
                "metadata": str(metadata_path),
                "source_sha256": expected_source_hash,
                "entity_stream_sha256": expected_stream_hash,
                "manifest_sha256": sha256(manifest_path),
                "unit_metadata": {
                    "source_unit_code": metadata.get("source_unit_code"),
                    "source_unit": metadata.get("source_unit"),
                    "unit_status": metadata.get("unit_status"),
                    "unit_basis": metadata.get("unit_basis"),
                },
            },
            "selection": {
                "layout": layout,
                "bounds": {key: json_number(value) for key, value in bounds.items()},
                "spatial_test": "x_y_anchor_inside_half_open_bounds_and_bbox_fully_inside",
                "layer_to_system": dict(sorted(layer_to_system.items())),
                "allowed_entity_types": sorted(allowed_entity_types),
                "allowed_geometry_methods": sorted(allowed_geometry_methods),
                "allowed_confidence": sorted(allowed_confidence),
                "scope_evidence_ids": scope_evidence_ids,
                "unit_evidence_ids": unit_evidence_ids,
                "system_evidence_ids": system_evidence,
                "rule_ids": rule_ids,
                "selection_signature": selection_signature,
            },
            "quantity": {
                "name": "显式选定管道平面已绘中心线",
                "record_count": len(details),
                "raw_value": json_number(total_raw),
                "raw_unit": raw_unit,
                "conversion_factor": json_number(conversion_factor),
                "value": json_number(total_raw * conversion_factor),
                "unit": output_unit,
                "by_system": {
                    system: {
                        "record_count": counts[system],
                        "raw_value": json_number(totals[system]),
                        "value": json_number(totals[system] * conversion_factor),
                    }
                    for system in sorted(totals)
                },
            },
            "quality_checks": {
                "source_file_hash_verified": "PASS",
                "cache_source_hash_consistency": "PASS",
                "entity_stream_hash_verified": "PASS",
                "selected_record_id_unique": True,
                "selected_physical_id_unique": True,
                "selected_bbox_within_scope": True,
                "scope_intersecting_centerline_outside_anchor_count": 0,
                "entity_type_counts": dict(sorted(type_counts.items())),
                "geometry_method_counts": dict(sorted(method_counts.items())),
                "same_layer_excluded_counts": dict(sorted(excluded_same_layer.items())),
            },
            "limitations": limitations,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        details_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        with details_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(details[0].keys()))
            writer.writeheader()
            writer.writerows(details)

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "calculation_id": calculation_id,
                    "record_count": len(details),
                    "value": result["quantity"]["value"],
                    "unit": output_unit,
                    "by_system": result["quantity"]["by_system"],
                    "selection_signature": selection_signature,
                    "output": str(output_path),
                    "details": str(details_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except ManifestError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
