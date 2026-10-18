#!/usr/bin/env python3
"""Extract a deterministic neutral CAD evidence package from a DXF copy.

The script makes no professional classification.  It writes layout entities,
unique block-definition entities, failures, and a manifest that binds every
artifact to the authoritative DWG (when supplied) and the exchange DXF.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

try:
    import ezdxf
except ImportError as exc:  # pragma: no cover - exercised by deployment checks
    raise SystemExit("缺少 ezdxf；请使用工作区 Python 运行时或安装 ezdxf>=1.4") from exc


from qto_cad_geometry import GEOMETRY_SCHEMA, LOCATION_SCHEMA, entity_geometry as extract_geometry_facts, read_dxf_with_diagnostics, resolve_underlay_dependency

VERSION = "qto-cad-neutral-extractor/1.6.0"
# Entity types whose named block is drawn as part of the entity (geometry blocks).
GEOMETRY_BLOCK_TYPES = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "ACAD_TABLE", "TABLE"}
CURVE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
UNIT_NAMES = {
    0: "Unitless",
    1: "Inches",
    2: "Feet",
    3: "Miles",
    4: "Millimeters",
    5: "Centimeters",
    6: "Meters",
    7: "Kilometers",
    10: "Yards",
}


class ExtractionError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def point(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        values = list(value)
    except TypeError:
        return None
    if len(values) < 2:
        return None
    return [float(values[0]), float(values[1]), float(values[2]) if len(values) > 2 else 0.0]


def bounds(points: Iterable[list[float] | None]) -> list[float] | None:
    valid = [item for item in points if item is not None]
    if not valid:
        return None
    return [
        min(item[0] for item in valid),
        min(item[1] for item in valid),
        max(item[0] for item in valid),
        max(item[1] for item in valid),
    ]


def get_dxf(entity: Any, name: str, default: Any = None) -> Any:
    try:
        return entity.dxf.get(name, default)
    except (AttributeError, ezdxf.DXFError):
        return default


def distance(left: list[float], right: list[float]) -> float:
    return math.dist(left, right)


def polyline_length(values: list[tuple[list[float], float]], closed: bool) -> float | None:
    if len(values) < 2:
        return None
    pairs = list(zip(values, values[1:]))
    if closed:
        pairs.append((values[-1], values[0]))
    total = 0.0
    for (start, bulge), (end, _) in pairs:
        chord = distance(start, end)
        if abs(bulge) < 1e-12:
            total += chord
        else:
            theta = 4.0 * math.atan(bulge)
            radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
            total += abs(theta) * radius
    return total


def entity_text(entity: Any) -> str | None:
    kind = entity.dxftype()
    try:
        if kind == "MTEXT":
            return entity.text
        if kind in {"TEXT", "ATTRIB", "ATTDEF"}:
            return str(entity.dxf.text)
    except (AttributeError, ezdxf.DXFError):
        return None
    return None


def entity_geometry(entity: Any, location_cache=None) -> dict[str, Any]:
    """Preserve WCS parametric geometry; never substitute an end-point chord."""
    return extract_geometry_facts(entity, location_cache=location_cache)


def convert(entity: Any, *, layout: str, parent_block: str | None, source_hash: str, raw_unit: str, sequence: int, dependency_context: dict | None = None, location_cache=None) -> dict[str, Any]:
    kind = entity.dxftype()
    handle = str(get_dxf(entity, "handle") or f"NOHANDLE-{sequence}")
    geometry = entity_geometry(entity, location_cache)
    anchor = geometry["anchor"]
    block_name = str(get_dxf(entity, "name")) if kind == "INSERT" and get_dxf(entity, "name") else None
    attributes = None
    if kind == "INSERT" and getattr(entity, "attribs", None):
        attributes = {
            str(get_dxf(item, "tag", "")): str(get_dxf(item, "text", ""))
            for item in entity.attribs
        }
    record = {
        "row_type": "entity",
        "layout": layout,
        "parent_block": parent_block,
        "record_id": f"CAD-{handle}",
        "entity_handle": handle,
        "candidate_physical_id": f"CAD-{handle}",
        "entity_type": kind,
        "object_name": kind,
        "layer": str(get_dxf(entity, "layer", "")),
        "outcome": "extracted" if geometry["bbox"] is not None or kind == "INSERT" else "extracted_without_2d_anchor",
        "measurement_shape": "centerline" if kind in CURVE_TYPES and geometry["length"] is not None else ("symbol_candidate" if kind in {"CIRCLE", "ELLIPSE", "INSERT"} else "not_applicable"),
        "geometry_method": "dxf_ezdxf_type_specific",
        "confidence": "A" if kind in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "INSERT", "TEXT", "MTEXT", "ATTRIB", "ATTDEF"} else "B",
        "raw_unit": raw_unit,
        "source_sha256": source_hash,
        "x": anchor[0] if anchor else None,
        "y": anchor[1] if anchor else None,
        "z": anchor[2] if anchor else None,
        "bbox": geometry["bbox"],
        "start": geometry["start"],
        "end": geometry["end"],
        "raw_length": geometry["length"],
        "closed": bool(getattr(entity, "closed", False) or getattr(entity, "is_closed", False)),
        "text": entity_text(entity),
        "block_name": block_name,
        "rotation": (None if "invalid_text_direction" in geometry["geometry_issue_codes"] else
                     math.radians(entity.get_rotation()) if kind == "MTEXT" else
                     float(get_dxf(entity, "rotation", 0.0) or 0.0) * math.pi / 180.0),
        "scale_factors": [float(get_dxf(entity, name, 1.0) or 1.0) for name in ("xscale", "yscale", "zscale")] if kind == "INSERT" else None,
        "attributes": attributes,
        "geometry_signature": geometry["signature"],
        "geometry": geometry["geometry"],
        "raw_geometry": geometry["raw_geometry"],
        "geometry_status": geometry["geometry_status"],
        "geometry_issues": geometry["geometry_issues"],
        "geometry_capability": geometry["geometry_capability"],
        "geometry_issue_codes": geometry["geometry_issue_codes"],
        "recommended_actions": geometry["recommended_actions"],
        "bbox3d": geometry["bbox3d"],
        "extrusion": geometry["extrusion"],
        "insert_matrix_wcs": geometry["insert_matrix_wcs"],
        "length_error_estimate": geometry["length_error_estimate"],
        "external_reference": resolve_underlay_dependency(geometry["external_reference"], dependency_context) if geometry.get("external_reference") else None,
    }
    for key in ("location_schema_version", "location_basis", "location_bbox3d", "location_status", "location_error",
                "measurement_geometry_available"):
        record[key] = geometry.get(key)
    record["location_schema_version"] = LOCATION_SCHEMA
    record["location_status"] = geometry.get("location_status") or "unknown"
    record["measurement_geometry_available"] = geometry.get("geometry") is not None
    if geometry.get('native_data') is not None:
        record['native_data']=geometry['native_data']
        record['geometry_method']='tianzheng_native_fields/0.5.1'
    return record


def failed_record(entity, layout, parent_block, source_hash, raw_unit, sequence, error):
    """Retain a locatable identity even when entity conversion/serialization fails."""
    handle = str(get_dxf(entity, "handle") or f"NOHANDLE-{sequence}")
    kind = entity.dxftype()
    record = dict(row_type="entity", layout=layout, parent_block=parent_block,
                  record_id=f"CAD-{handle}", entity_handle=handle,
                  candidate_physical_id=f"CAD-{handle}", entity_type=kind,
                  object_name=kind, layer=str(get_dxf(entity, "layer", "")),
                  outcome="read_failed_identity_retained", measurement_shape="not_applicable",
                  geometry_method="dxf_ezdxf_type_specific", confidence="unresolved",
                  raw_unit=raw_unit, source_sha256=source_hash,
                  geometry_status="read_failed", geometry_capability="read_failed",
                  geometry_issues=[error], geometry_issue_codes=["entity_read_failed"],
                  recommended_actions=["native_geometry_read_required"],
                  raw_geometry={"entity_type": kind}, closed=None,
                  block_name=str(get_dxf(entity, "name", "")) if kind == "INSERT" else None)
    record.update(location_schema_version=LOCATION_SCHEMA, location_status="unknown", location_bbox3d=None,
                  location_basis=None, location_error=error, measurement_geometry_available=False)
    for key in ("x", "y", "z", "bbox", "bbox3d", "start", "end", "raw_length", "text",
                "rotation", "scale_factors", "attributes", "geometry_signature", "geometry",
                "extrusion", "insert_matrix_wcs", "length_error_estimate"):
        record[key] = None
    return record


def new_coverage():
    return {"schema_version": "qto-cad-read-coverage/1.0", "entity_count": 0,
            "capability_counts": {}, "status_counts": {}, "entity_type_counts": {},
            "pending_objects": [], "external_references": [], "read_failed_count": 0,
            "professional_scope_evaluated": False, "quantity_coverage_proven": False,
            "counting_basis": "layout entities and unique definition entities; not expanded physical instances"}


def record_coverage(coverage, record):
    coverage["entity_count"] += 1
    for field, value in (("capability_counts", record["geometry_capability"]),
                         ("status_counts", record["geometry_status"]),
                         ("entity_type_counts", record["entity_type"])):
        coverage[field][value] = coverage[field].get(value, 0) + 1
    if record["geometry_status"] == "read_failed":
        coverage["read_failed_count"] += 1
    if record.get("external_reference"):
        reference = {key: record[key] for key in
            ("record_id", "entity_handle", "entity_type", "layout", "parent_block", "layer", "external_reference")}
        coverage["external_references"].append(reference)
    if record["geometry_issues"] or record["geometry_status"] == "requires_adapter":
        coverage["pending_objects"].append({key: record[key] for key in
            ("record_id", "entity_handle", "entity_type", "layout", "parent_block", "layer",
             "geometry_status", "geometry_capability", "geometry_issue_codes", "geometry_issues", "recommended_actions")})


def write_stream(path: Path, groups: Iterable[tuple[str, str | None, Iterable[Any]]], source_hash: str, raw_unit: str, *, coverage: dict | None = None, dependency_context: dict | None = None) -> tuple[int, Counter[str], list[dict[str, Any]]]:
    location_cache = {}
    count = 0
    types: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for layout, parent_block, entities in groups:
            ordered = sorted(entities, key=lambda entity: str(get_dxf(entity, "handle", "")))
            for sequence, entity in enumerate(ordered, 1):
                try:
                    record = convert(entity, layout=layout, parent_block=parent_block, source_hash=source_hash, raw_unit=raw_unit, sequence=sequence, dependency_context=dependency_context, location_cache=location_cache)
                    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                except Exception as exc:  # retain evidence instead of silently skipping
                    record = failed_record(entity, layout, parent_block, source_hash, raw_unit, sequence, f"{type(exc).__name__}: {exc}")
                    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                target.write(encoded + "\n")
                count += 1
                types[record["entity_type"]] += 1
                if record.get("geometry_issues"):
                    failures.append({"layout": layout, "parent_block": parent_block,
                                     "record_id": record["record_id"],
                                     "entity_handle": record["entity_handle"], "entity_type": record["entity_type"],
                                     "geometry_status": record["geometry_status"],
                                     "issue_codes": record["geometry_issue_codes"],
                                     "recommended_actions": record["recommended_actions"],
                                     "error": "; ".join(record["geometry_issues"]), "record_retained": True})
                if coverage is not None:
                    record_coverage(coverage, record)
    return count, types, failures


def block_references(entities: Iterable[Any]) -> Iterable[str]:
    """Block names an entity container draws: INSERT definitions and geometry blocks (DIMENSION/TABLE)."""
    for entity in entities:
        kind = entity.dxftype()
        if kind == "INSERT":
            name = get_dxf(entity, "name")
        elif kind in GEOMETRY_BLOCK_TYPES:
            name = get_dxf(entity, "geometry")
        else:
            continue
        if name:
            yield str(name)


def referenced_block_closure(document: Any, seeds: Iterable[str], *, include_layouts: bool = True) -> set[str]:
    """Names of block definitions reachable from the layouts (recursively) and from explicit seeds.

    Paste residue (`A$C...`), old base-map blocks and library blocks that no layout inserts are
    left out: they draw nothing visible and were up to 94% of the serialized volume on as-built sets.
    MLEADER content blocks are not traced (no quantity consumer reads them).
    """
    pending: deque[str] = deque(seeds)
    if include_layouts:
        for layout in document.layouts:
            pending.extend(block_references(layout))
    closure: set[str] = set()
    while pending:
        name = pending.popleft()
        if name in closure:
            continue
        block = document.blocks.get(name)
        if block is None or block.is_any_layout:
            continue
        closure.add(name)
        pending.extend(block_references(block))
    return closure


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从DXF交换副本生成专业无关的CAD中性取证包")
    parser.add_argument("--source-dxf", required=True, type=Path)
    parser.add_argument("--source-dwg", type=Path, help="可选：作为权威来源绑定的原DWG")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--all-blocks", action="store_true",
                        help="序列化全部块定义图元，包括没有任何布局（递归）引用的块；默认只写被引用闭包，未引用块只在清单里登记")
    args = parser.parse_args(argv)
    try:
        dxf_path = args.source_dxf.resolve()
        if not dxf_path.is_file() or dxf_path.suffix.lower() != ".dxf":
            raise ExtractionError("source-dxf 必须是存在的 .dxf 文件")
        dwg_path = args.source_dwg.resolve() if args.source_dwg else None
        if dwg_path and (not dwg_path.is_file() or dwg_path.suffix.lower() != ".dwg"):
            raise ExtractionError("source-dwg 必须是存在的 .dwg 文件")
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        if dxf_path == output or (dwg_path and dwg_path == output):
            raise ExtractionError("输出目录不能覆盖输入文件")

        document, source_diagnostics = read_dxf_with_diagnostics(dxf_path)
        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        raw_unit = UNIT_NAMES.get(unit_code, f"INSUNITS-{unit_code}")
        primary_path = dwg_path or dxf_path
        # Hash each input once; large exchange files take seconds per pass.
        dxf_hash = sha256(dxf_path)
        primary_hash = sha256(dwg_path) if dwg_path else dxf_hash
        receipt_path = dxf_path.with_name(dxf_path.name + '.export.json')
        receipt_hash = sha256(receipt_path) if receipt_path.is_file() else None
        conversion, dependency_context = None, None
        if dwg_path:
            dependency_context = {"original_cad_directory": str(dwg_path.parent),
                "original_cad_path": str(dwg_path), "original_cad_sha256": primary_hash,
                "basis": "explicit_source_dwg"}
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
            if (receipt.get('output_sha256') != dxf_hash
                    or (dwg_path is not None and receipt.get('source_sha256') != primary_hash)):
                raise ExtractionError('转换记录与当前原图或DXF不一致，须重新转换')
            if not isinstance(receipt.get('exchange_geometry_review_required'), bool):
                raise ExtractionError('转换记录缺少几何复核状态')
            conversion = {'path': str(receipt_path), 'sha256': receipt_hash,
                'backend': receipt.get('backend'), 'geometry_review_required': receipt['exchange_geometry_review_required']}
            if not dependency_context and receipt.get('source'):
                origin = Path(receipt['source'])
                if (origin.is_absolute() and not str(origin).startswith(('\\\\', '//'))
                        and origin.is_file() and receipt.get('source_sha256') == sha256(origin)):
                    dependency_context = {"original_cad_directory": str(origin.parent),
                        "original_cad_path": str(origin), "original_cad_sha256": receipt['source_sha256'],
                        "basis": "verified_export_receipt", "receipt_path": str(receipt_path),
                        "receipt_sha256": receipt_hash}
        layout_path = output / "中性布局图元.ndjson"
        block_path = output / "中性块定义图元.ndjson"
        failure_path = output / "CAD提取失败对象.json"
        coverage_path = output / "CAD读取覆盖与待补读.json"
        manifest_path = output / "CAD中性取证清单.json"
        native_path = output / "CAD原生构件基础数据.json"
        from cad_native import read_document
        native = read_document(document)
        native['input']={'exchange_path':str(dxf_path),'exchange_sha256':dxf_hash,
            'original_dwg':str(dwg_path) if dwg_path else None,'original_sha256':primary_hash if dwg_path else None,
            'dxf_version':document.dxfversion,'insunits_original':unit_code,'primary_source_sha256':primary_hash}
        native['source_diagnostics']=source_diagnostics
        native['conversion']=conversion
        native['purpose']='CAD basic-data reading; no engineering quantity calculation'
        native_path.write_text(json.dumps(native,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')

        layout_groups = [(layout.name, None, layout) for layout in document.layouts]
        definition_blocks = [block for block in document.blocks if not block.is_any_layout]
        # Only definitions some layout draws (recursive INSERT / geometry blocks) or a native
        # symbol references are serialized; the rest are inventoried in the manifest only.
        layout_closure = referenced_block_closure(document, ())
        native_symbol_blocks = {row["name"] for row in native["referenced_blocks"].values()}
        native_closure = referenced_block_closure(document, native_symbol_blocks, include_layouts=False)
        referenced_blocks = layout_closure | native_closure
        serialized_blocks = [block for block in definition_blocks
                             if args.all_blocks or block.name in referenced_blocks]
        serialized_names = {block.name for block in serialized_blocks}
        block_groups = [("BlockDefinition", block.name, block) for block in serialized_blocks]
        coverage = new_coverage()
        coverage['native_read_coverage']=native['summary']
        if not args.all_blocks:
            coverage["counting_basis"] = ("layout entities and unique definition entities of blocks referenced from layouts "
                                          "(recursive INSERT, DIMENSION/TABLE geometry blocks) or by native symbols; "
                                          "unreferenced block definitions are inventoried, not serialized; not expanded physical instances")
        layout_count, layout_types, layout_failures = write_stream(layout_path, layout_groups, primary_hash, raw_unit, coverage=coverage, dependency_context=dependency_context)
        block_count, block_types, block_failures = write_stream(block_path, block_groups, primary_hash, raw_unit, coverage=coverage, dependency_context=dependency_context)
        failures = layout_failures + block_failures
        for warning in source_diagnostics['source_read_warnings']:
            if warning.get('blocks_identity_binding') is False:
                continue  # Proven identical resource declarations remain advisories.
            failures.append({'kind': 'source_read_warning', 'error': warning['message'],
                             'source_path': str(dxf_path), 'source_entity_preservation_proven': False})
        if conversion and conversion['geometry_review_required']:
            failures.append({'kind': 'exchange_copy_audited', 'error': '交换副本经过修复转换；须核对受影响对象，不能据文件完整自动认定原图几何完整', 'conversion_receipt': conversion['path']})
        failure_path.write_text(json.dumps({"failures": failures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        block_rows = []
        for block in sorted(definition_blocks, key=lambda item: item.name):
            block_rows.append({
                "block_name": block.name,
                "origin": point(block.block.dxf.base_point) or [0.0, 0.0, 0.0],
                "direct_entity_count": len(block),
                "is_xref": bool(block.block.is_xref or block.block.is_xref_overlay),
                "xref_path": block.block.dxf.get("xref_path", "") if block.block.is_xref or block.block.is_xref_overlay else None,
                "referenced_from_layouts": block.name in layout_closure,
                "referenced_by_native_symbols": block.name in native_closure,
                "serialized": block.name in serialized_names,
            })
        unserialized_rows = [row for row in block_rows if not row["serialized"]]
        block_serialization = {
            "mode": "all_blocks" if args.all_blocks else "referenced_closure",
            "closure_basis": "layout INSERT recursion, DIMENSION/TABLE geometry blocks, native symbol block references; MLEADER content blocks not traced",
            "definition_block_count": len(block_rows),
            "referenced_block_count": len(referenced_blocks),
            "serialized_block_count": len(serialized_names),
            "unserialized_block_count": len(unserialized_rows),
            "unserialized_entity_count": sum(row["direct_entity_count"] for row in unserialized_rows),
        }
        coverage["block_definition_serialization"] = block_serialization
        coverage.update(source_sha256=primary_hash, pending_object_count=len(coverage["pending_objects"]),
                        external_reference_count=len(coverage["external_references"]),
                        external_dependency_context=dependency_context,
                        status="PARTIAL" if failures or coverage["pending_objects"] else "PASS",
                        source_identity_issues=source_diagnostics['source_identity_issues'],
                        source_identity_advisories=source_diagnostics['source_identity_advisories'],
                        source_read_warnings=source_diagnostics['source_read_warnings'],
                        source_identity_status=source_diagnostics['source_identity_status'],
                        source_identity_issue_count=len(source_diagnostics['source_identity_issues']),
                        source_identity_advisory_count=len(source_diagnostics['source_identity_advisories']),
                        source_read_warning_count=len(source_diagnostics['source_read_warnings']),
                        exchange_geometry_review_required=bool(conversion and conversion["geometry_review_required"]))
        coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": "qto-cad-neutral-evidence/1.0",
            "extractor_version": VERSION,
            "geometry_schema_version": GEOMETRY_SCHEMA,
            "location_schema_version": LOCATION_SCHEMA,
            "status": coverage["status"],
            "neutral_extraction_status": "PASS" if not failures else "PARTIAL",
            "read_coverage": {key: coverage[key] for key in ("entity_count", "capability_counts", "status_counts", "read_failed_count", "pending_object_count", "external_reference_count", "source_identity_issue_count", "source_identity_advisory_count", "source_read_warning_count", "quantity_coverage_proven")},
            "professional_classification_performed": False,
            "native_decoder_baseline": "0.5.1-native-fields-inline",
            "native_read_coverage": native['summary'],
            "authoritative_source": {"path": str(primary_path), "sha256": primary_hash, "bytes": primary_path.stat().st_size},
            "exchange_dxf": {"path": str(dxf_path), "sha256": dxf_hash, "bytes": dxf_path.stat().st_size},
            "exchange_conversion": conversion,
            "external_dependency_context": dependency_context,
            "source_read_diagnostics": source_diagnostics,
            "source_identity_issues": source_diagnostics['source_identity_issues'],
            "source_identity_advisories": source_diagnostics['source_identity_advisories'],
            "drawing_unit": {"insunits_code": unit_code, "raw_unit": raw_unit, "conversion_to_business_unit": None},
            "artifacts": {
                "layout_entities": {"path": str(layout_path), "sha256": sha256(layout_path), "count": layout_count, "entity_type_counts": dict(sorted(layout_types.items()))},
                "block_definitions": {"path": str(block_path), "sha256": sha256(block_path), "count": block_count, "entity_type_counts": dict(sorted(block_types.items()))},
                "failures": {"path": str(failure_path), "sha256": sha256(failure_path), "count": len(failures)},
                "read_coverage": {"path": str(coverage_path), "sha256": sha256(coverage_path), "count": len(coverage["pending_objects"])},
                "native_basic_data": {"path": str(native_path), "sha256": sha256(native_path), "count": len(native['objects'])},
            },
            "layouts": [layout.name for layout in document.layouts],
            "blocks": block_rows,
            "block_definition_count": len(block_rows),
            "block_definition_serialization": block_serialization,
            "limitations": [
                "DXF交换副本不证明DWG代理对象已完整降级；失败对象和交换文件身份必须保留",
                "块定义只保存一次；专业层按实例路径和冻结范围决定是否递归展开",
                "默认只序列化被布局递归引用或被原生符号引用的块定义；未引用块只登记名称和图元数（blocks[].serialized=false），需要时用 --all-blocks 全写",
                "原始图层、块名和文字均为中性事实，不是专业分类",
                "读取覆盖按布局和唯一块定义图元统计，不是实物数量覆盖率；文字、定位证据不等于可测构件",
                "HATCH须由面层边界适配器处理；代理对象、三维实体、缺失参照及动态状态按待补读保留",
                "底图仅读取CAD原生定义、引用及变换；文件存在不表示内容已读取，相对路径须有显式原CAD目录或核验通过的导出记录",
                "PASS只表示本次中性读取未发现待处理项，不证明DXF转换前对象齐全或专业范围完整",
            ],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": manifest["status"], "layouts": layout_count, "block_entities": block_count,
                          "unserialized_blocks": block_serialization["unserialized_block_count"],
                          "failures": len(failures), "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0 if manifest["status"] == "PASS" else 3
    except (OSError, ezdxf.DXFError, ExtractionError, ValueError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
