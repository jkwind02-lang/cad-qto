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
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    import ezdxf
except ImportError as exc:  # pragma: no cover - exercised by deployment checks
    raise SystemExit("缺少 ezdxf；请使用工作区 Python 运行时或安装 ezdxf>=1.4") from exc


from qto_cad_geometry import GEOMETRY_SCHEMA, entity_geometry as extract_geometry_facts

VERSION = "qto-cad-neutral-extractor/1.1.0"
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


def entity_geometry(entity: Any) -> dict[str, Any]:
    """Preserve WCS parametric geometry; never substitute an end-point chord."""
    return extract_geometry_facts(entity)


def convert(entity: Any, *, layout: str, parent_block: str | None, source_hash: str, raw_unit: str, sequence: int) -> dict[str, Any]:
    kind = entity.dxftype()
    handle = str(get_dxf(entity, "handle") or f"NOHANDLE-{sequence}")
    geometry = entity_geometry(entity)
    anchor = geometry["anchor"]
    block_name = str(get_dxf(entity, "name")) if kind == "INSERT" and get_dxf(entity, "name") else None
    attributes = None
    if kind == "INSERT" and getattr(entity, "attribs", None):
        attributes = {
            str(get_dxf(item, "tag", "")): str(get_dxf(item, "text", ""))
            for item in entity.attribs
        }
    return {
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
        "rotation": float(get_dxf(entity, "rotation", 0.0) or 0.0) * math.pi / 180.0,
        "scale_factors": [float(get_dxf(entity, name, 1.0) or 1.0) for name in ("xscale", "yscale", "zscale")] if kind == "INSERT" else None,
        "attributes": attributes,
        "geometry_signature": geometry["signature"],
        "geometry": geometry["geometry"],
        "raw_geometry": geometry["raw_geometry"],
        "geometry_status": geometry["geometry_status"],
        "geometry_issues": geometry["geometry_issues"],
        "bbox3d": geometry["bbox3d"],
        "extrusion": geometry["extrusion"],
        "insert_matrix_wcs": geometry["insert_matrix_wcs"],
        "length_error_estimate": geometry["length_error_estimate"],
    }


def write_stream(path: Path, groups: Iterable[tuple[str, str | None, Iterable[Any]]], source_hash: str, raw_unit: str) -> tuple[int, Counter[str], list[dict[str, Any]]]:
    count = 0
    types: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for layout, parent_block, entities in groups:
            ordered = sorted(entities, key=lambda entity: str(get_dxf(entity, "handle", "")))
            for sequence, entity in enumerate(ordered, 1):
                try:
                    record = convert(entity, layout=layout, parent_block=parent_block, source_hash=source_hash, raw_unit=raw_unit, sequence=sequence)
                    target.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
                    count += 1
                    types[record["entity_type"]] += 1
                    if record.get("geometry_issues"):
                        failures.append({"layout": layout, "parent_block": parent_block,
                                         "entity_handle": record["entity_handle"], "entity_type": record["entity_type"],
                                         "error": "; ".join(record["geometry_issues"]), "record_retained": True})
                except Exception as exc:  # retain evidence instead of silently skipping
                    failures.append({
                        "layout": layout,
                        "parent_block": parent_block,
                        "entity_handle": str(get_dxf(entity, "handle", "")),
                        "entity_type": entity.dxftype(),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
    return count, types, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="从DXF交换副本生成专业无关的CAD中性取证包")
    parser.add_argument("--source-dxf", required=True, type=Path)
    parser.add_argument("--source-dwg", type=Path, help="可选：作为权威来源绑定的原DWG")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
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

        document = ezdxf.readfile(dxf_path)
        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        raw_unit = UNIT_NAMES.get(unit_code, f"INSUNITS-{unit_code}")
        primary_path = dwg_path or dxf_path
        primary_hash = sha256(primary_path)
        layout_path = output / "中性布局图元.ndjson"
        block_path = output / "中性块定义图元.ndjson"
        failure_path = output / "CAD提取失败对象.json"
        manifest_path = output / "CAD中性取证清单.json"

        layout_groups = [(layout.name, None, layout) for layout in document.layouts]
        block_groups = [
            ("BlockDefinition", block.name, block)
            for block in document.blocks
            if not block.is_any_layout
        ]
        layout_count, layout_types, layout_failures = write_stream(layout_path, layout_groups, primary_hash, raw_unit)
        block_count, block_types, block_failures = write_stream(block_path, block_groups, primary_hash, raw_unit)
        failures = layout_failures + block_failures
        failure_path.write_text(json.dumps({"failures": failures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        block_rows = []
        for block in sorted((item for item in document.blocks if not item.is_any_layout), key=lambda item: item.name):
            block_rows.append({
                "block_name": block.name,
                "origin": point(block.block.dxf.base_point) or [0.0, 0.0, 0.0],
                "direct_entity_count": len(block),
            })
        manifest = {
            "schema_version": "qto-cad-neutral-evidence/1.0",
            "extractor_version": VERSION,
            "geometry_schema_version": GEOMETRY_SCHEMA,
            "status": "PASS" if not failures else "PARTIAL",
            "professional_classification_performed": False,
            "authoritative_source": {"path": str(primary_path), "sha256": primary_hash, "bytes": primary_path.stat().st_size},
            "exchange_dxf": {"path": str(dxf_path), "sha256": sha256(dxf_path), "bytes": dxf_path.stat().st_size},
            "drawing_unit": {"insunits_code": unit_code, "raw_unit": raw_unit, "conversion_to_business_unit": None},
            "artifacts": {
                "layout_entities": {"path": str(layout_path), "sha256": sha256(layout_path), "count": layout_count, "entity_type_counts": dict(sorted(layout_types.items()))},
                "block_definitions": {"path": str(block_path), "sha256": sha256(block_path), "count": block_count, "entity_type_counts": dict(sorted(block_types.items()))},
                "failures": {"path": str(failure_path), "sha256": sha256(failure_path), "count": len(failures)},
            },
            "layouts": [layout.name for layout in document.layouts],
            "blocks": block_rows,
            "block_definition_count": len(block_rows),
            "limitations": [
                "DXF交换副本不证明DWG代理对象已完整降级；失败对象和交换文件身份必须保留",
                "块定义只保存一次；专业层按实例路径和冻结范围决定是否递归展开",
                "原始图层、块名和文字均为中性事实，不是专业分类",
            ],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": manifest["status"], "layouts": layout_count, "block_entities": block_count, "failures": len(failures), "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0 if not failures else 3
    except (OSError, ezdxf.DXFError, ExtractionError, ValueError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
