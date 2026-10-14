#!/usr/bin/env python3
"""Convert the full AutoLISP block TSV into frozen neutral NDJSON + closure metadata."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


VERSION = "qto-full-block-converter/1.1.0"
HEADERS = [
    "handle", "dxf_type", "object_name", "layer", "x", "y", "z",
    "xmin", "ymin", "xmax", "ymax", "length",
    "start_x", "start_y", "start_z", "end_x", "end_y", "end_z",
    "closed", "text", "block_name", "rotation", "xscale", "yscale", "zscale",
    "radius", "start_angle", "end_angle", "parent_block", "attributes",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def number(value: str):
    if value in {"", None}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def point(x: str, y: str, z: str):
    values = [number(x), number(y), number(z)]
    return values if values[0] is not None and values[1] is not None else None


def bbox(row: dict[str, str]):
    values = [number(row[key]) for key in ("xmin", "ymin", "xmax", "ymax")]
    return values if all(value is not None for value in values) else None


def input_rows(path: Path):
    with path.open("r", encoding="gb18030", errors="replace", newline="") as handle:
        for line_number, values in enumerate(csv.reader(handle, delimiter="\t"), 1):
            if len(values) < len(HEADERS):
                values += [""] * (len(HEADERS) - len(values))
            elif len(values) > len(HEADERS):
                values = values[: len(HEADERS) - 1] + ["|".join(values[len(HEADERS) - 1 :])]
            yield line_number, dict(zip(HEADERS, values))


def convert(source_tsv: Path, output: Path, source_hash: str) -> dict:
    counts = Counter()
    layers = Counter()
    parents = Counter()
    replacement_records = 0
    with output.open("w", encoding="utf-8", newline="\n") as target:
        for line_number, row in input_rows(source_tsv):
            handle = row["handle"] or f"NOHANDLE-{line_number}"
            kind = row["dxf_type"] or "UNKNOWN"
            length = number(row["length"])
            is_curve = kind in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE"}
            record = {
                "row_type": "entity",
                "layout": "BlockDefinition",
                "parent_block": row["parent_block"] or None,
                "record_id": f"DWG-{handle}",
                "entity_handle": handle,
                "candidate_physical_id": f"DWG-{handle}",
                "entity_type": kind,
                "object_name": row["object_name"],
                "layer": row["layer"],
                "outcome": "extracted",
                "measurement_shape": "centerline" if is_curve and length is not None else ("symbol_candidate" if kind in {"CIRCLE", "ELLIPSE"} else "not_applicable"),
                "geometry_method": "autocad_core_console_full_block_definition",
                "confidence": "A",
                "raw_unit": "Millimeters",
                "source_sha256": source_hash,
                "x": number(row["x"]),
                "y": number(row["y"]),
                "z": number(row["z"]),
                "bbox": bbox(row),
                "start": point(row["start_x"], row["start_y"], row["start_z"]),
                "end": point(row["end_x"], row["end_y"], row["end_z"]),
                "raw_length": length,
                "closed": row["closed"].lower() == "true",
                "text": row["text"] or None,
                "block_name": row["block_name"] or None,
                "rotation": number(row["rotation"]),
                "scale_factors": [number(row[key]) for key in ("xscale", "yscale", "zscale")] if row["xscale"] else None,
                "attributes": row["attributes"] or None,
                "geometry_signature": {
                    "radius": number(row["radius"]),
                    "start_angle": number(row["start_angle"]),
                    "end_angle": number(row["end_angle"]),
                } if kind == "ARC" else None,
            }
            if any("\ufffd" in str(value) for value in record.values() if value is not None):
                replacement_records += 1
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts[kind] += 1
            layers[row["layer"]] += 1
            parents[row["parent_block"]] += 1
    return {
        "record_count": sum(counts.values()),
        "entity_type_counts": dict(sorted(counts.items())),
        "layer_counts": dict(sorted(layers.items())),
        "parent_block_counts": dict(sorted(parents.items())),
        "replacement_character_records": replacement_records,
    }


def parse_block_meta(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="gb18030", errors="replace", newline="") as handle:
        for values in csv.reader(handle, delimiter="\t"):
            values += [""] * (7 - len(values))
            rows.append(
                {
                    "block_name": values[0],
                    "origin": [number(values[1]) or 0.0, number(values[2]) or 0.0, number(values[3]) or 0.0],
                    "written_record_count": int(number(values[4]) or 0),
                    "skipped_record_count": int(number(values[5]) or 0),
                    "block_flags": int(number(values[6]) or 0),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dwg", type=Path, required=True)
    parser.add_argument("--block-tsv", type=Path, required=True)
    parser.add_argument("--block-meta-tsv", type=Path, required=True)
    parser.add_argument("--expected-block-metadata", type=Path, required=True)
    parser.add_argument("--output-ndjson", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    args = parser.parse_args()

    source_hash = sha256(args.source_dwg)
    args.output_ndjson.parent.mkdir(parents=True, exist_ok=True)
    stats = convert(args.block_tsv, args.output_ndjson, source_hash)
    block_rows = parse_block_meta(args.block_meta_tsv)
    expected_payload = json.loads(args.expected_block_metadata.read_text(encoding="utf-8-sig"))
    expected_blocks = {
        str(row["block_name"]): row
        for row in expected_payload.get("blocks", [])
        if not row.get("is_layout")
    }
    actual_blocks = {str(row["block_name"]): row for row in block_rows}
    missing_blocks = sorted(set(expected_blocks) - set(actual_blocks))
    unexpected_blocks = sorted(set(actual_blocks) - set(expected_blocks))
    origin_mismatches = []
    for name in sorted(set(expected_blocks) & set(actual_blocks)):
        expected_origin = [float(value or 0.0) for value in (expected_blocks[name].get("origin") or [0.0, 0.0, 0.0])]
        actual_origin = [float(value or 0.0) for value in actual_blocks[name]["origin"]]
        if any(abs(left - right) > 1e-9 for left, right in zip(expected_origin, actual_origin)):
            origin_mismatches.append({"block_name": name, "expected": expected_origin, "actual": actual_origin})
    written = sum(row["written_record_count"] for row in block_rows)
    skipped = sum(row["skipped_record_count"] for row in block_rows)
    denominator = written + skipped
    payload = {
        "schema_version": "qto-cad-full-block-closure/1.0",
        "converter_version": VERSION,
        "source_path": str(args.source_dwg.resolve()),
        "source_sha256": source_hash,
        "source_bytes": args.source_dwg.stat().st_size,
        "raw_block_tsv": {"path": str(args.block_tsv.resolve()), "sha256": sha256(args.block_tsv), "bytes": args.block_tsv.stat().st_size},
        "raw_block_meta_tsv": {"path": str(args.block_meta_tsv.resolve()), "sha256": sha256(args.block_meta_tsv), "bytes": args.block_meta_tsv.stat().st_size},
        "block_stream": {"path": str(args.output_ndjson.resolve()), "sha256": sha256(args.output_ndjson), "bytes": args.output_ndjson.stat().st_size},
        "block_definition_count": len(block_rows),
        "expected_block_definition_count": len(expected_blocks),
        "block_set_match": not missing_blocks and not unexpected_blocks,
        "missing_blocks": missing_blocks,
        "unexpected_blocks": unexpected_blocks,
        "origin_mismatches": origin_mismatches,
        "expected_block_metadata": {
            "path": str(args.expected_block_metadata.resolve()),
            "sha256": sha256(args.expected_block_metadata),
        },
        "written_record_count": written,
        "skipped_record_count": skipped,
        "completion_rate": written / denominator if denominator else 1.0,
        "stats": stats,
        "blocks": block_rows,
        "completeness_claim": "every non-layout block definition record was enumerated; skipped records remain explicit in metadata",
    }
    args.output_metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output_metadata.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = (
        skipped == 0
        and written == stats["record_count"]
        and not missing_blocks
        and not unexpected_blocks
        and not origin_mismatches
    )
    print(json.dumps({"status": "PASS" if passed else "FAIL", "written": written, "skipped": skipped, "completion_rate": payload["completion_rate"], "missing_blocks": len(missing_blocks), "unexpected_blocks": len(unexpected_blocks), "origin_mismatches": len(origin_mismatches)}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
