#!/usr/bin/env python3
"""Convert an AutoCAD-generated DXF copy into a complete neutral block stream.

AutoCAD performs one database-level DWG->DXF export.  ezdxf then reads every
non-layout block in-process, avoiding hundreds of thousands of COM round trips.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import ezdxf


VERSION = "qto-dxf-block-converter/1.0.0"
CURVE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


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
    valid = [value for value in points if value is not None]
    if not valid:
        return None
    return [
        min(value[0] for value in valid),
        min(value[1] for value in valid),
        max(value[0] for value in valid),
        max(value[1] for value in valid),
    ]


def distance(left: list[float], right: list[float]) -> float:
    return math.dist(left, right)


def polyline_length(points_with_bulge: list[tuple[list[float], float]], closed: bool) -> float | None:
    if len(points_with_bulge) < 2:
        return None
    pairs = list(zip(points_with_bulge, points_with_bulge[1:]))
    if closed:
        pairs.append((points_with_bulge[-1], points_with_bulge[0]))
    total = 0.0
    for (start, bulge), (end, _) in pairs:
        chord = distance(start, end)
        if abs(bulge) < 1e-12:
            total += chord
            continue
        theta = 4.0 * math.atan(bulge)
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        total += abs(theta) * radius
    return total


def get_dxf(entity: Any, name: str, default: Any = None) -> Any:
    try:
        return entity.dxf.get(name, default)
    except (AttributeError, ezdxf.DXFError):
        return default


def entity_geometry(entity: Any) -> dict[str, Any]:
    kind = entity.dxftype()
    anchor = None
    start = None
    end = None
    bbox = None
    length = None
    signature = None
    if kind == "LINE":
        start, end = point(entity.dxf.start), point(entity.dxf.end)
        anchor, bbox = start, bounds([start, end])
        length = distance(start, end) if start and end else None
    elif kind == "LWPOLYLINE":
        values = [(point((x, y, get_dxf(entity, "elevation", 0.0))), float(bulge or 0.0)) for x, y, bulge in entity.get_points("xyb")]
        values = [(value, bulge) for value, bulge in values if value]
        points = [value for value, _ in values]
        start, end = (points[0], points[-1]) if points else (None, None)
        anchor, bbox = start, bounds(points)
        length = polyline_length(values, bool(entity.closed))
    elif kind == "POLYLINE":
        values = [(point(vertex.dxf.location), float(get_dxf(vertex, "bulge", 0.0) or 0.0)) for vertex in entity.vertices]
        values = [(value, bulge) for value, bulge in values if value]
        points = [value for value, _ in values]
        start, end = (points[0], points[-1]) if points else (None, None)
        anchor, bbox = start, bounds(points)
        length = polyline_length(values, bool(entity.is_closed))
    elif kind == "ARC":
        anchor = point(entity.dxf.center)
        radius = float(entity.dxf.radius)
        bbox = [anchor[0] - radius, anchor[1] - radius, anchor[0] + radius, anchor[1] + radius] if anchor else None
        delta = (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0
        length = radius * math.radians(delta)
        signature = {"radius": radius, "start_angle": math.radians(float(entity.dxf.start_angle)), "end_angle": math.radians(float(entity.dxf.end_angle))}
    elif kind == "CIRCLE":
        anchor = point(entity.dxf.center)
        radius = float(entity.dxf.radius)
        bbox = [anchor[0] - radius, anchor[1] - radius, anchor[0] + radius, anchor[1] + radius] if anchor else None
        length = 2.0 * math.pi * radius
        signature = {"radius": radius, "start_angle": 0.0, "end_angle": 2.0 * math.pi}
    elif kind == "ELLIPSE":
        anchor = point(entity.dxf.center)
        major = point(entity.dxf.major_axis)
        major_radius = math.dist([0.0, 0.0, 0.0], major) if major else 0.0
        minor_radius = major_radius * float(entity.dxf.ratio)
        radius = max(major_radius, minor_radius)
        bbox = [anchor[0] - radius, anchor[1] - radius, anchor[0] + radius, anchor[1] + radius] if anchor else None
        length = math.pi * (3.0 * (major_radius + minor_radius) - math.sqrt(max(0.0, (3.0 * major_radius + minor_radius) * (major_radius + 3.0 * minor_radius))))
    elif kind == "SPLINE":
        points = [point(value) for value in list(entity.control_points) + list(entity.fit_points)]
        points = [value for value in points if value]
        anchor, bbox = (points[0] if points else None), bounds(points)
    elif kind == "INSERT":
        anchor = point(entity.dxf.insert)
        bbox = bounds([anchor])
    elif kind in {"TEXT", "ATTRIB", "ATTDEF", "MTEXT"}:
        anchor = point(get_dxf(entity, "insert"))
        bbox = bounds([anchor])
    elif kind == "DIMENSION":
        anchor = point(get_dxf(entity, "defpoint"))
        bbox = bounds([anchor])
    elif kind in {"LEADER", "MULTILEADER"}:
        vertices = [point(value) for value in getattr(entity, "vertices", [])]
        vertices = [value for value in vertices if value]
        anchor, bbox = (vertices[0] if vertices else None), bounds(vertices)
    else:
        for field in ("insert", "location", "center", "start"):
            anchor = point(get_dxf(entity, field))
            if anchor:
                bbox = bounds([anchor])
                break
    return {"anchor": anchor, "bbox": bbox, "start": start, "end": end, "length": length, "signature": signature}


def entity_text(entity: Any) -> str | None:
    kind = entity.dxftype()
    try:
        if kind == "MTEXT":
            return entity.text
        if kind in {"TEXT", "ATTRIB", "ATTDEF"}:
            return entity.dxf.text
    except (AttributeError, ezdxf.DXFError):
        return None
    return None


def convert_entity(entity: Any, parent_block: str, source_hash: str, sequence: int) -> dict[str, Any]:
    kind = entity.dxftype()
    handle = str(get_dxf(entity, "handle") or f"NOHANDLE-{parent_block}-{sequence}")
    geometry = entity_geometry(entity)
    anchor = geometry["anchor"]
    block_name = str(get_dxf(entity, "name")) if kind == "INSERT" and get_dxf(entity, "name") else None
    attributes = None
    if kind == "INSERT" and getattr(entity, "attribs", None):
        attributes = "|".join(f"{get_dxf(item, 'tag', '')}={get_dxf(item, 'text', '')}" for item in entity.attribs)
    return {
        "row_type": "entity",
        "layout": "BlockDefinition",
        "parent_block": parent_block,
        "record_id": f"DWG-{handle}",
        "entity_handle": handle,
        "candidate_physical_id": f"DWG-{handle}",
        "entity_type": kind,
        "object_name": kind,
        "layer": str(get_dxf(entity, "layer", "")),
        "outcome": "extracted" if geometry["bbox"] is not None or kind == "INSERT" else "extracted_without_2d_anchor",
        "measurement_shape": "centerline" if kind in CURVE_TYPES and geometry["length"] is not None else ("symbol_candidate" if kind in {"CIRCLE", "ELLIPSE"} else "not_applicable"),
        "geometry_method": "autocad_dxf_copy_plus_ezdxf_type_specific",
        "confidence": "A" if kind in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "INSERT", "TEXT", "MTEXT", "ATTRIB", "ATTDEF"} else "B",
        "raw_unit": "Millimeters",
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
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dwg", type=Path, required=True)
    parser.add_argument("--source-dxf", type=Path, required=True)
    parser.add_argument("--expected-block-metadata", type=Path, required=True)
    parser.add_argument("--output-ndjson", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    args = parser.parse_args()

    source_hash = sha256(args.source_dwg)
    expected_payload = json.loads(args.expected_block_metadata.read_text(encoding="utf-8-sig"))
    if str(expected_payload.get("source_sha256") or "").lower() != source_hash:
        raise SystemExit("expected block metadata does not belong to the source DWG")
    expected_blocks = {str(row["block_name"]): row for row in expected_payload.get("blocks", []) if not row.get("is_layout")}

    document = ezdxf.readfile(args.source_dxf)
    missing_blocks: list[str] = []
    count_mismatches: list[dict[str, Any]] = []
    origin_mismatches: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    types = Counter()
    total = 0
    args.output_ndjson.parent.mkdir(parents=True, exist_ok=True)
    with args.output_ndjson.open("w", encoding="utf-8", newline="\n") as target:
        for name in sorted(expected_blocks):
            block = document.blocks.get(name)
            if block is None:
                missing_blocks.append(name)
                continue
            expected = expected_blocks[name]
            actual_count = len(block)
            if actual_count != int(expected.get("entity_count") or 0):
                count_mismatches.append({"block_name": name, "expected": int(expected.get("entity_count") or 0), "actual": actual_count})
            actual_origin = point(block.block.dxf.base_point) or [0.0, 0.0, 0.0]
            expected_origin = [float(value or 0.0) for value in (expected.get("origin") or [0.0, 0.0, 0.0])]
            if any(abs(left - right) > 1e-9 for left, right in zip(actual_origin, expected_origin)):
                origin_mismatches.append({"block_name": name, "expected": expected_origin, "actual": actual_origin})
            block_types = Counter()
            entities = sorted(block, key=lambda entity: str(get_dxf(entity, "handle", "")))
            for sequence, entity in enumerate(entities, 1):
                record = convert_entity(entity, name, source_hash, sequence)
                target.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                block_types[record["entity_type"]] += 1
                types[record["entity_type"]] += 1
                total += 1
            blocks.append({
                "block_name": name,
                "origin": actual_origin,
                "written_record_count": actual_count,
                "skipped_record_count": 0,
                "block_flags": 0,
                "entity_type_counts": dict(sorted(block_types.items())),
            })

    unexpected_blocks = sorted(
        block.name for block in document.blocks
        if block.name not in expected_blocks
        and str(block.name).upper() not in {"*MODEL_SPACE", "*PAPER_SPACE"}
        and not str(block.name).upper().startswith("*PAPER_SPACE")
    )
    written = sum(row["written_record_count"] for row in blocks)
    passed = not missing_blocks and not unexpected_blocks and not count_mismatches and not origin_mismatches and written == total
    payload = {
        "schema_version": "qto-cad-full-block-closure/2.0",
        "converter_version": VERSION,
        "source_path": str(args.source_dwg.resolve()),
        "source_sha256": source_hash,
        "source_bytes": args.source_dwg.stat().st_size,
        "dxf_copy": {"path": str(args.source_dxf.resolve()), "sha256": sha256(args.source_dxf), "bytes": args.source_dxf.stat().st_size},
        "block_stream": {"path": str(args.output_ndjson.resolve()), "sha256": sha256(args.output_ndjson), "bytes": args.output_ndjson.stat().st_size},
        "expected_block_metadata": {"path": str(args.expected_block_metadata.resolve()), "sha256": sha256(args.expected_block_metadata)},
        "block_definition_count": len(blocks),
        "expected_block_definition_count": len(expected_blocks),
        "written_record_count": written,
        "skipped_record_count": 0,
        "completion_rate": 1.0 if passed else 0.0,
        "block_set_match": not missing_blocks and not unexpected_blocks,
        "missing_blocks": missing_blocks,
        "unexpected_blocks": unexpected_blocks,
        "count_mismatches": count_mismatches,
        "origin_mismatches": origin_mismatches,
        "stats": {"record_count": total, "entity_type_counts": dict(sorted(types.items()))},
        "blocks": blocks,
        "completeness_claim": "all non-layout blocks and their direct entities match AutoCAD ActiveX block names, counts, and origins",
        "status": "PASS" if passed else "FAIL",
    }
    args.output_metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output_metadata.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "blocks": len(blocks), "records": total, "missing": len(missing_blocks), "unexpected": len(unexpected_blocks), "count_mismatches": len(count_mismatches), "origin_mismatches": len(origin_mismatches)}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
