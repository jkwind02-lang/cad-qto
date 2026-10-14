#!/usr/bin/env python3
"""Build a deterministic phase-one benchmark from neutral CAD facts.

The script deliberately produces no professional truth labels.  It freezes the
raw/scoped universes, draws a reproducible pilot sample, and prepares human
annotation tables.  Historical quantity results are neither read nor accepted
as inputs.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "qto-benchmark-universe/1.0"
BUILDER_VERSION = "build-phase1-benchmark/1.3.0"
SAMPLE_VERSION = "qto-frozen-sampling/1.0"
DEFAULT_SEED = "QTO-8F-PHASE1-PILOT-20260827-V1"
LINE_TYPES = {"LINE", "LWPOLYLINE", "ARC", "SPLINE", "CIRCLE", "ELLIPSE", "POLYLINE"}
ANNOTATION_TYPES = {"TEXT", "MTEXT", "DIMENSION", "LEADER", "ATTRIB", "ATTDEF"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return stable_hash(payload)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_jsonl_gzip(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write deterministic gzip JSONL (no timestamp or source filename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as compressed:
            for row in rows:
                payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                compressed.write(payload.encode("utf-8"))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def identity_matrix() -> tuple[float, float, float, float, float, float]:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def insert_matrix(
    row: dict[str, Any],
    block_origins: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float, float, float, float]:
    scales = row.get("scale_factors") or [1.0, 1.0, 1.0]
    sx = 1.0 if len(scales) < 1 or scales[0] is None else float(scales[0])
    sy = 1.0 if len(scales) < 2 or scales[1] is None else float(scales[1])
    angle = float(row.get("rotation") or 0.0)
    cosine, sine = math.cos(angle), math.sin(angle)
    a, b, c, d = cosine * sx, sine * sx, -sine * sy, cosine * sy
    origin = block_origins.get(str(row.get("block_name") or ""), (0.0, 0.0, 0.0))
    ox, oy = float(origin[0]), float(origin[1])
    x, y = float(row.get("x") or 0.0), float(row.get("y") or 0.0)
    return (a, b, c, d, x - a * ox - c * oy, y - b * ox - d * oy)


def compose(
    outer: tuple[float, float, float, float, float, float],
    inner: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = outer
    g, h, i, j, k, l = inner
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    )


def transform_point(
    matrix: tuple[float, float, float, float, float, float], point: list[float] | tuple[float, ...]
) -> list[float]:
    a, b, c, d, e, f = matrix
    x, y = float(point[0]), float(point[1])
    z = float(point[2]) if len(point) > 2 and point[2] is not None else 0.0
    return [a * x + c * y + e, b * x + d * y + f, z]


def transformed_bbox(
    row: dict[str, Any], matrix: tuple[float, float, float, float, float, float]
) -> list[float] | None:
    bbox = row.get("bbox")
    if bbox and len(bbox) >= 4 and all(value is not None for value in bbox[:4]):
        corners = [
            transform_point(matrix, [bbox[0], bbox[1], 0.0]),
            transform_point(matrix, [bbox[0], bbox[3], 0.0]),
            transform_point(matrix, [bbox[2], bbox[1], 0.0]),
            transform_point(matrix, [bbox[2], bbox[3], 0.0]),
        ]
        return [
            min(point[0] for point in corners),
            min(point[1] for point in corners),
            max(point[0] for point in corners),
            max(point[1] for point in corners),
        ]
    if row.get("x") is not None and row.get("y") is not None:
        point = transform_point(matrix, [row["x"], row["y"], row.get("z") or 0.0])
        return [point[0], point[1], point[0], point[1]]
    return None


def transform_optional_point(
    row: dict[str, Any], key: str, matrix: tuple[float, float, float, float, float, float]
) -> list[float] | None:
    point = row.get(key)
    return transform_point(matrix, point) if point else None


def scope_relation(bbox: list[float] | None, bounds: dict[str, float]) -> str:
    if bbox is None:
        return "UNKNOWN"
    xmin, ymin, xmax, ymax = bbox
    if xmax < bounds["xmin"] or xmin >= bounds["xmax_exclusive"]:
        return "OUTSIDE"
    if ymax < bounds["ymin"] or ymin >= bounds["ymax_exclusive"]:
        return "OUTSIDE"
    if (
        xmin >= bounds["xmin"]
        and xmax < bounds["xmax_exclusive"]
        and ymin >= bounds["ymin"]
        and ymax < bounds["ymax_exclusive"]
    ):
        return "INSIDE"
    return "INTERSECTS_BOUNDARY"


def effective_layer(child_layer: str | None, inherited_layer: str | None) -> str:
    if child_layer and child_layer != "0":
        return child_layer
    return inherited_layer or child_layer or ""


def neutral_record(
    row: dict[str, Any],
    *,
    object_id: str,
    parent_object_id: str | None,
    source_level: str,
    depth: int,
    instance_path: list[str],
    top_instance_id: str | None,
    top_block_name: str | None,
    inherited_layer: str | None,
    matrix: tuple[float, float, float, float, float, float],
    bounds: dict[str, float],
    closure_status: str,
) -> dict[str, Any]:
    bbox = transformed_bbox(row, matrix)
    anchor = None
    if row.get("x") is not None and row.get("y") is not None:
        anchor = transform_point(matrix, [row["x"], row["y"], row.get("z") or 0.0])
    return {
        "schema_version": SCHEMA_VERSION,
        "object_id": object_id,
        "parent_object_id": parent_object_id,
        "source_record_id": row.get("record_id"),
        "entity_handle": row.get("entity_handle"),
        "source_level": source_level,
        "depth": depth,
        "instance_path": instance_path,
        "top_instance_id": top_instance_id,
        "top_block_name": top_block_name,
        "parent_block": row.get("parent_block"),
        "entity_type": row.get("entity_type"),
        "layer": row.get("layer") or "",
        "effective_layer": effective_layer(row.get("layer"), inherited_layer),
        "block_name": row.get("block_name"),
        "outcome": row.get("outcome"),
        "closure_status": closure_status,
        "scope_relation": scope_relation(bbox, bounds),
        "x": anchor[0] if anchor else None,
        "y": anchor[1] if anchor else None,
        "z": anchor[2] if anchor else None,
        "bbox": bbox,
        "start": transform_optional_point(row, "start", matrix),
        "end": transform_optional_point(row, "end", matrix),
        "raw_length": row.get("raw_length"),
        "raw_unit": row.get("raw_unit"),
        "measurement_shape": row.get("measurement_shape"),
        "text": row.get("text"),
        "geometry_signature": row.get("geometry_signature"),
        "source_sha256": row.get("source_sha256"),
    }


def layer_family(layer: str) -> str:
    cleaned = (layer or "<EMPTY>").strip()
    if cleaned == "0":
        return "LAYER_0"
    tokens = re.split(r"[-_$—]+", cleaned, maxsplit=1)
    return (tokens[0] or cleaned).upper()


def block_family(block_name: str | None) -> str:
    name = (block_name or "<NO_BLOCK>").strip()
    upper = name.upper()
    if name.startswith("*U"):
        return "ANONYMOUS_DYNAMIC"
    if upper.startswith("$VALVE$"):
        return "VALVE_DYNAMIC"
    if upper.startswith("$TWTSYS$"):
        return "TWT_DYNAMIC"
    if upper.startswith("$LINEPAT$"):
        return "CAD_PATTERN"
    if upper.startswith("A$C"):
        return "ANONYMOUS_IMPORTED"
    return name


def benchmark_shape(row: dict[str, Any]) -> str:
    entity_type = row.get("entity_type")
    if entity_type in LINE_TYPES:
        return "LINE_NETWORK"
    if entity_type == "INSERT":
        return "DISCRETE_INSTANCE"
    if entity_type in ANNOTATION_TYPES:
        return "ANNOTATION_CONTEXT"
    return "OTHER_CONTEXT"


def grid_cell(row: dict[str, Any], bounds: dict[str, float], columns: int = 4, rows: int = 2) -> str:
    x = row.get("x")
    y = row.get("y")
    if x is None or y is None:
        bbox = row.get("bbox")
        if bbox:
            x, y = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
    if x is None or y is None:
        return "GRID_UNKNOWN"
    width = bounds["xmax_exclusive"] - bounds["xmin"]
    height = bounds["ymax_exclusive"] - bounds["ymin"]
    column = min(columns - 1, max(0, int((x - bounds["xmin"]) / width * columns)))
    row_index = min(rows - 1, max(0, int((y - bounds["ymin"]) / height * rows)))
    return f"G{row_index + 1}-{column + 1}"


def geometry_group_signature(row: dict[str, Any]) -> str:
    bbox = row.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    width = round(abs(float(bbox[2]) - float(bbox[0])), 1)
    height = round(abs(float(bbox[3]) - float(bbox[1])), 1)
    length = row.get("raw_length")
    rounded_length = None if length is None else round(float(length), 1)
    return "|".join(
        [str(row.get("entity_type")), layer_family(row.get("effective_layer") or ""), str(width), str(height), str(rounded_length)]
    )


def build_universes(
    model_rows: list[dict[str, Any]],
    definition_rows: list[dict[str, Any]],
    block_origins: dict[str, tuple[float, float, float]],
    bounds: dict[str, float],
    max_depth: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in definition_rows:
        parent = row.get("parent_block")
        if parent:
            definitions[parent].append(row)
    for rows in definitions.values():
        rows.sort(key=lambda item: (str(item.get("record_id")), str(item.get("entity_handle"))))

    universe: dict[str, dict[str, Any]] = {}
    closure = Counter()
    expanded_roots: list[dict[str, Any]] = []

    def add(record: dict[str, Any]) -> None:
        universe[record["object_id"]] = record

    def expand_definition(
        block_name: str,
        outer_matrix: tuple[float, float, float, float, float, float],
        *,
        top_id: str,
        top_block: str,
        parent_id: str,
        path: list[str],
        inherited: str,
        depth: int,
        stack: tuple[str, ...],
    ) -> int:
        if depth > max_depth:
            closure["max_depth"] += 1
            return 0
        if block_name in stack:
            closure["cycle"] += 1
            return 0
        rows = definitions.get(block_name)
        if not rows:
            closure["missing_definition"] += 1
            return 0
        closure["resolved_definition"] += 1
        included = 0
        next_stack = stack + (block_name,)
        for index, row in enumerate(rows):
            child_token = f"{row.get('record_id') or row.get('entity_handle') or index}#{index}"
            child_path = path + [child_token]
            child_id = "N:" + top_id + ":" + "/".join(child_path)
            child_matrix = outer_matrix
            child_closure = "RESOLVED"
            if row.get("entity_type") == "INSERT":
                child_matrix = compose(outer_matrix, insert_matrix(row, block_origins))
                nested_name = row.get("block_name")
                if nested_name and nested_name not in definitions:
                    child_closure = "DEFINITION_MISSING"
            record = neutral_record(
                row,
                object_id=child_id,
                parent_object_id=parent_id,
                source_level="NESTED",
                depth=depth,
                instance_path=[top_id] + child_path,
                top_instance_id=top_id,
                top_block_name=top_block,
                inherited_layer=inherited,
                matrix=outer_matrix,
                bounds=bounds,
                closure_status=child_closure,
            )
            relation = record["scope_relation"]
            child_inside = relation in {"INSIDE", "INTERSECTS_BOUNDARY"}
            descendant_count = 0
            if row.get("entity_type") == "INSERT" and row.get("block_name"):
                descendant_count = expand_definition(
                    row["block_name"],
                    child_matrix,
                    top_id=top_id,
                    top_block=top_block,
                    parent_id=child_id,
                    path=child_path,
                    inherited=effective_layer(row.get("layer"), inherited),
                    depth=depth + 1,
                    stack=next_stack,
                )
            if child_inside or descendant_count:
                if not child_inside and descendant_count:
                    record["scope_relation"] = "ANCESTOR_OF_IN_SCOPE"
                add(record)
                included += 1 + descendant_count
        return included

    for row in sorted(model_rows, key=lambda item: str(item.get("record_id"))):
        top_id = str(row.get("record_id") or row.get("entity_handle"))
        object_id = "M:" + top_id
        record = neutral_record(
            row,
            object_id=object_id,
            parent_object_id=None,
            source_level="MODEL",
            depth=0,
            instance_path=[top_id],
            top_instance_id=top_id if row.get("entity_type") == "INSERT" else None,
            top_block_name=row.get("block_name"),
            inherited_layer=row.get("layer"),
            matrix=identity_matrix(),
            bounds=bounds,
            closure_status=(
                "DEFINITION_MISSING"
                if row.get("entity_type") == "INSERT" and row.get("block_name") not in definitions
                else "RESOLVED"
            ),
        )
        relation = record["scope_relation"]
        direct_inside = relation in {"INSIDE", "INTERSECTS_BOUNDARY"}
        descendants = 0
        if row.get("entity_type") == "INSERT" and row.get("block_name") in definitions:
            descendants = expand_definition(
                row["block_name"],
                insert_matrix(row, block_origins),
                top_id=top_id,
                top_block=row["block_name"],
                parent_id=object_id,
                path=[f"BLOCK:{row['block_name']}"],
                inherited=row.get("layer") or "",
                depth=1,
                stack=(),
            )
        if direct_inside or descendants:
            if not direct_inside and descendants:
                record["scope_relation"] = "ANCESTOR_OF_IN_SCOPE"
            add(record)
            if row.get("entity_type") == "INSERT":
                expanded_roots.append(
                    {
                        "top_instance_id": top_id,
                        "block_name": row.get("block_name"),
                        "root_scope_relation": record["scope_relation"],
                        "in_scope_descendant_count": descendants,
                        "definition_available": row.get("block_name") in definitions,
                    }
                )

    u0 = [universe[key] for key in sorted(universe)]
    up: list[dict[str, Any]] = []
    disposition = Counter()
    for source in u0:
        row = dict(source)
        shape = benchmark_shape(row)
        row["benchmark_shape"] = shape
        row["grid_cell"] = grid_cell(row, bounds)
        row["layer_family"] = layer_family(row.get("effective_layer") or "")
        row["block_family"] = block_family(row.get("block_name")) if row.get("entity_type") == "INSERT" else ""
        row["candidate_only"] = True
        row["adjudication"] = "UNRESOLVED"
        if row["scope_relation"] == "ANCESTOR_OF_IN_SCOPE":
            disposition["EXEMPT_ANCESTRY"] += 1
            continue
        if row["source_level"] == "NESTED" and row.get("entity_type") != "INSERT":
            disposition["EXEMPT_CLOSURE_GEOMETRY"] += 1
            continue
        if row.get("entity_type") == "INSERT" and block_family(row.get("block_name")) == "CAD_PATTERN":
            disposition["EXEMPT_CAD_PATTERN"] += 1
            continue
        if shape in {"LINE_NETWORK", "DISCRETE_INSTANCE"}:
            row["processing_role"] = "PROFESSIONAL_CANDIDATE"
            row["t1_eligible"] = True
        elif shape == "ANNOTATION_CONTEXT":
            row["processing_role"] = "CONTEXT_EVIDENCE"
            row["t1_eligible"] = False
        else:
            row["processing_role"] = "OTHER_CONTEXT"
            row["t1_eligible"] = False
        up.append(row)
        disposition["PROCESS"] += 1
    up.sort(key=lambda item: item["object_id"])
    closure_report = {
        "definition_count": len(definitions),
        "definition_row_count": len(definition_rows),
        "closure_events": dict(sorted(closure.items())),
        "expanded_top_instances": expanded_roots,
        "p_disposition_counts": dict(sorted(disposition.items())),
    }
    return u0, up, closure_report


def select_probability_sample(
    eligible: list[dict[str, Any]], seed: str, total: int
) -> list[dict[str, Any]]:
    by_shape: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_shape[row["benchmark_shape"]].append(row)
    shapes = ["LINE_NETWORK", "DISCRETE_INSTANCE"]
    base = total // len(shapes)
    quotas = {shape: base for shape in shapes}
    for shape in shapes[: total - base * len(shapes)]:
        quotas[shape] += 1
    selected: list[dict[str, Any]] = []
    for shape in shapes:
        candidates = by_shape.get(shape, [])
        strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            family = row["block_family"] if shape == "DISCRETE_INSTANCE" else row["layer_family"]
            key = f"{row['grid_cell']}|{family}"
            scored = dict(row)
            scored["random_score"] = stable_hash(f"{seed}|{row['object_id']}")
            scored["sampling_stratum"] = f"{shape}|{key}"
            strata[key].append(scored)
        for values in strata.values():
            values.sort(key=lambda item: (item["random_score"], item["object_id"]))
        ordered_strata = sorted(strata, key=lambda key: stable_hash(f"{seed}|STRATUM|{shape}|{key}"))
        shape_selection: list[dict[str, Any]] = []
        index = 0
        while len(shape_selection) < min(quotas[shape], len(candidates)):
            made_progress = False
            for key in ordered_strata:
                if index < len(strata[key]):
                    shape_selection.append(strata[key][index])
                    made_progress = True
                    if len(shape_selection) >= quotas[shape]:
                        break
            if not made_progress:
                break
            index += 1
        selected.extend(shape_selection)
    selected.sort(key=lambda item: (item["benchmark_shape"], item["random_score"], item["object_id"]))
    for index, row in enumerate(selected, 1):
        row["selection_order"] = index
        row["sample_set"] = "PROBABILITY_PILOT"
    return selected


def challenge_groups(eligible: list[dict[str, Any]], seed: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    line_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        if row.get("source_level") == "NESTED" and row.get("entity_type") == "INSERT":
            groups[("NESTED_DISCRETE_INSTANCE", row.get("block_family") or str(row.get("block_name") or "<NO_BLOCK>"))].append(row)
        if row.get("closure_status") != "RESOLVED":
            groups[("MISSING_OR_INCOMPLETE_DEFINITION", row.get("block_family") or "<NO_BLOCK>")].append(row)
        if row.get("block_family") in {"ANONYMOUS_DYNAMIC", "ANONYMOUS_IMPORTED"}:
            groups[("ANONYMOUS_BLOCK_FAMILY", row["block_family"] + "|" + str(row.get("block_name")))].append(row)
        if row["benchmark_shape"] == "LINE_NETWORK":
            line_groups[geometry_group_signature(row)].append(row)
    for signature, rows in line_groups.items():
        if len(rows) >= 4:
            groups[("REPEATED_GEOMETRY_SIGNATURE", signature)].extend(rows)

    discrete = [row for row in eligible if row["benchmark_shape"] == "DISCRETE_INSTANCE" and row.get("x") is not None]
    for index, left in enumerate(discrete):
        for right in discrete[index + 1 :]:
            if left.get("block_name") != right.get("block_name") or left.get("effective_layer") != right.get("effective_layer"):
                continue
            dx = abs(float(left["x"]) - float(right["x"]))
            dy = abs(float(left["y"]) - float(right["y"]))
            if 137700.0 <= dx <= 137900.0 and dy <= 2.0:
                key = "|".join(sorted([left["object_id"], right["object_id"]]))
                groups[("CROSS_VIEW_PAIR", key)].extend([left, right])

    frame_groups: list[dict[str, Any]] = []
    challenge_rows: list[dict[str, Any]] = []
    for group_index, ((challenge_type, key), members) in enumerate(sorted(groups.items()), 1):
        unique = {row["object_id"]: row for row in members}
        ordered = sorted(unique.values(), key=lambda row: (stable_hash(f"{seed}|CHALLENGE|{row['object_id']}"), row["object_id"]))
        sample_count = 2 if challenge_type in {"REPEATED_GEOMETRY_SIGNATURE", "CROSS_VIEW_PAIR"} else 1
        chosen = ordered[: min(sample_count, len(ordered))]
        member_ids = sorted(unique)
        group_id = f"CH-{group_index:04d}"
        frame_groups.append(
            {
                "challenge_group_id": group_id,
                "challenge_type": challenge_type,
                "group_key": key,
                "member_count": len(member_ids),
                "member_ids_sha256": canonical_json_hash(member_ids),
                "selection_rule": f"stable_hash_first_{sample_count}",
                "selected_object_ids": [row["object_id"] for row in chosen],
            }
        )
        for row in chosen:
            challenge_rows.append(
                {
                    "challenge_group_id": group_id,
                    "challenge_type": challenge_type,
                    "group_key": key,
                    "group_size": len(member_ids),
                    "object_id": row["object_id"],
                    "source_record_id": row.get("source_record_id"),
                    "benchmark_shape": row["benchmark_shape"],
                    "entity_type": row.get("entity_type"),
                    "effective_layer": row.get("effective_layer"),
                    "block_name": row.get("block_name"),
                    "x": row.get("x"),
                    "y": row.get("y"),
                    "label_status": "PENDING_HUMAN",
                }
            )
    return frame_groups, challenge_rows


def svg_preview(
    sample: dict[str, Any], context_rows: Iterable[dict[str, Any]], output: Path, index: int
) -> None:
    bbox = sample.get("bbox") or [sample.get("x") or 0.0, sample.get("y") or 0.0] * 2
    center_x = (float(bbox[0]) + float(bbox[2])) / 2.0
    center_y = (float(bbox[1]) + float(bbox[3])) / 2.0
    extent = max(abs(float(bbox[2]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[1])), 2500.0)
    close_half = min(max(extent * 1.4, 2500.0), 8000.0)
    wide_half = min(max(close_half * 3.0, 7500.0), 24000.0)
    wide_bounds = (
        center_x - wide_half,
        center_y - wide_half,
        center_x + wide_half,
        center_y + wide_half,
    )

    def intersects(row_bbox: list[float] | None, bounds: tuple[float, float, float, float]) -> bool:
        if not row_bbox:
            return False
        xmin, ymin, xmax, ymax = bounds
        return not (
            float(row_bbox[2]) < xmin
            or float(row_bbox[0]) > xmax
            or float(row_bbox[3]) < ymin
            or float(row_bbox[1]) > ymax
        )

    def distance(row: dict[str, Any]) -> float:
        row_bbox = row.get("bbox") or [row.get("x") or center_x, row.get("y") or center_y] * 2
        row_x = (float(row_bbox[0]) + float(row_bbox[2])) / 2.0
        row_y = (float(row_bbox[1]) + float(row_bbox[3])) / 2.0
        return math.hypot(row_x - center_x, row_y - center_y)

    sample_id = str(sample.get("object_id") or "")
    candidate_prefix = sample_id + "/"
    selected: dict[str, dict[str, Any]] = {}
    for row in context_rows:
        row_bbox = row.get("bbox")
        if not intersects(row_bbox, wide_bounds):
            continue
        object_id = str(row.get("object_id") or row.get("source_record_id") or id(row))
        selected.setdefault(object_id, row)

    def priority(row: dict[str, Any]) -> tuple[int, float, str]:
        object_id = str(row.get("object_id") or "")
        if object_id.startswith(candidate_prefix):
            rank = 0
        elif row.get("source_level") == "MODEL":
            rank = 1
        else:
            rank = 2
        return (rank, distance(row), object_id)

    ordered_rows = sorted(selected.values(), key=priority)
    candidate_rows = [
        row for row in ordered_rows
        if str(row.get("object_id") or "").startswith(candidate_prefix)
    ][:250]
    model_context_rows = [
        row for row in ordered_rows
        if row.get("source_level") == "MODEL"
        and not str(row.get("object_id") or "").startswith(candidate_prefix)
    ][:250]
    nested_context_rows = [
        row for row in ordered_rows
        if row.get("source_level") == "NESTED"
        and not str(row.get("object_id") or "").startswith(candidate_prefix)
    ][:250]
    # Keep three evidence classes independently capped. A single dense class
    # can no longer crowd out the candidate's own expanded geometry or the
    # surrounding plan context.
    preview_rows = candidate_rows + model_context_rows + nested_context_rows

    def panel(half: float, x_offset: float, label: str, clip_id: str) -> list[str]:
        xmin, xmax = center_x - half, center_x + half
        ymin, ymax = center_y - half, center_y + half
        panel_bounds = (xmin, ymin, xmax, ymax)

        def px(x: float) -> float:
            return x_offset + 20.0 + (x - xmin) / (xmax - xmin) * 420.0

        def py(y: float) -> float:
            return 460.0 - (y - ymin) / (ymax - ymin) * 420.0

        drawings = [
            f'<text x="{x_offset + 20:.2f}" y="24" font-family="Microsoft YaHei, sans-serif" font-size="13" font-weight="700">{label}</text>',
            f'<rect x="{x_offset + 20:.2f}" y="40" width="420" height="420" fill="#f8fafc" stroke="#cbd5e1"/>',
            f'<g clip-path="url(#{clip_id})">',
        ]
        for row in preview_rows:
            row_bbox = row.get("bbox")
            if not intersects(row_bbox, panel_bounds):
                continue
            object_id = str(row.get("object_id") or "")
            is_candidate_detail = object_id.startswith(candidate_prefix)
            stroke = "#2563eb" if is_candidate_detail else ("#64748b" if row.get("source_level") == "MODEL" else "#cbd5e1")
            stroke_width = "1.6" if is_candidate_detail else ("0.9" if row.get("source_level") == "MODEL" else "0.6")
            if row.get("start") and row.get("end"):
                start, end = row["start"], row["end"]
                drawings.append(
                    f'<line x1="{px(float(start[0])):.2f}" y1="{py(float(start[1])):.2f}" '
                    f'x2="{px(float(end[0])):.2f}" y2="{py(float(end[1])):.2f}" stroke="{stroke}" stroke-width="{stroke_width}" />'
                )
            elif row.get("entity_type") in {"CIRCLE", "ARC", "ELLIPSE"} and row_bbox:
                radius_x = max(abs(px(float(row_bbox[2])) - px(float(row_bbox[0]))) / 2.0, 1.0)
                radius_y = max(abs(py(float(row_bbox[3])) - py(float(row_bbox[1]))) / 2.0, 1.0)
                drawings.append(
                    f'<ellipse cx="{px((float(row_bbox[0]) + float(row_bbox[2])) / 2.0):.2f}" '
                    f'cy="{py((float(row_bbox[1]) + float(row_bbox[3])) / 2.0):.2f}" '
                    f'rx="{radius_x:.2f}" ry="{radius_y:.2f}" fill="none" stroke="{stroke}" stroke-width="{stroke_width}" />'
                )
            elif row.get("entity_type") == "INSERT" and row.get("x") is not None:
                drawings.append(
                    f'<circle cx="{px(float(row["x"])):.2f}" cy="{py(float(row["y"])):.2f}" r="2" fill="{stroke}" />'
                )
            if row.get("text") and row.get("x") is not None and distance(row) <= half:
                visible_text = re.sub(r"\\[A-Za-z0-9;._-]+", " ", str(row["text"])).replace("\\P", " ")
                visible_text = html.escape(visible_text.strip()[:24])
                if visible_text:
                    drawings.append(
                        f'<text x="{px(float(row["x"])):.2f}" y="{py(float(row["y"])):.2f}" '
                        f'font-family="Microsoft YaHei, sans-serif" font-size="7" fill="#475569">{visible_text}</text>'
                    )

        if sample.get("start") and sample.get("end"):
            start, end = sample["start"], sample["end"]
            drawings.append(
                f'<line x1="{px(float(start[0])):.2f}" y1="{py(float(start[1])):.2f}" '
                f'x2="{px(float(end[0])):.2f}" y2="{py(float(end[1])):.2f}" stroke="#d62828" stroke-width="4" />'
            )
        elif sample.get("bbox") and float(bbox[2]) != float(bbox[0]) and float(bbox[3]) != float(bbox[1]):
            drawings.append(
                f'<rect x="{px(float(bbox[0])):.2f}" y="{py(float(bbox[3])):.2f}" '
                f'width="{abs(px(float(bbox[2])) - px(float(bbox[0]))):.2f}" '
                f'height="{abs(py(float(bbox[0])) - py(float(bbox[3]))):.2f}" '
                f'fill="none" stroke="#d62828" stroke-width="4" />'
            )
        else:
            drawings.append(
                f'<circle cx="{px(center_x):.2f}" cy="{py(center_y):.2f}" r="8" fill="none" stroke="#d62828" stroke-width="4" />'
            )
        drawings.append("</g>")
        return drawings

    label = sample.get("block_name") or sample.get("entity_type")
    title = html.escape(
        f"#{index:02d} {sample['object_id']} | {sample.get('effective_layer') or ''} | {label}"
    )
    descendants = sum(
        1 for row in preview_rows if str(row.get("object_id") or "").startswith(candidate_prefix)
    )
    svg = "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="520" viewBox="0 0 960 520">',
            '<rect width="960" height="520" fill="white"/>',
            '<defs><clipPath id="closeClip"><rect x="20" y="40" width="420" height="420"/></clipPath>'
            '<clipPath id="wideClip"><rect x="520" y="40" width="420" height="420"/></clipPath></defs>',
            *panel(close_half, 0.0, f"近景 ±{close_half / 1000:.1f}m", "closeClip"),
            *panel(wide_half, 500.0, f"环境 ±{wide_half / 1000:.1f}m", "wideClip"),
            f'<text x="20" y="486" font-family="Microsoft YaHei, sans-serif" font-size="11">{title}</text>',
            f'<text x="20" y="505" font-family="Microsoft YaHei, sans-serif" font-size="10" fill="#475569">红色=待判对象；蓝色=其块内展开图元；灰色=环境证据；候选图元 {len(preview_rows)}，块内展开 {descendants}</text>',
            '</svg>',
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dwg", type=Path, required=True)
    parser.add_argument("--model-stream", type=Path, required=True)
    parser.add_argument("--block-stream", type=Path, required=True)
    parser.add_argument("--block-metadata", type=Path, required=True)
    parser.add_argument("--scope-evidence", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--checklist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot-size", type=int, default=20)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--max-depth", type=int, default=12)
    args = parser.parse_args()

    scope_evidence = json.loads(args.scope_evidence.read_text(encoding="utf-8-sig"))
    bounds = scope_evidence["scope"]["bounds"]
    expected_source_hash = str(scope_evidence["source_sha256"]).lower()
    actual_source_hash = sha256_file(args.source_dwg)
    if actual_source_hash != expected_source_hash:
        raise SystemExit(f"source hash mismatch: expected {expected_source_hash}, actual {actual_source_hash}")

    model_rows = load_jsonl(args.model_stream)
    block_rows = load_jsonl(args.block_stream)
    block_metadata = json.loads(args.block_metadata.read_text(encoding="utf-8-sig"))
    if str(block_metadata.get("source_sha256") or "").lower() != actual_source_hash:
        raise SystemExit("block metadata source hash does not match source DWG")
    written = int(block_metadata.get("written_record_count") or 0)
    skipped = int(block_metadata.get("skipped_record_count") or 0)
    completion_rate = float(block_metadata.get("completion_rate") or 0.0)
    converted = int((block_metadata.get("stats") or {}).get("record_count") or 0)
    closure_complete = (
        skipped == 0
        and written == converted == len(block_rows)
        and completion_rate == 1.0
        and bool(block_metadata.get("block_set_match"))
        and not block_metadata.get("origin_mismatches")
    )
    block_origins = {
        str(row.get("block_name")): tuple(float(value or 0.0) for value in (row.get("origin") or [0.0, 0.0, 0.0])[:3])
        for row in block_metadata.get("blocks", [])
        if row.get("block_name")
    }
    if not closure_complete:
        raise SystemExit(
            f"block closure incomplete: written={written}, converted={converted}, loaded={len(block_rows)}, "
            f"skipped={skipped}, completion_rate={completion_rate}"
        )
    u0, up, closure = build_universes(model_rows, block_rows, block_origins, bounds, args.max_depth)
    closure["source_closure_metadata"] = {
        "path": str(args.block_metadata),
        "sha256": sha256_file(args.block_metadata),
        "block_definition_count": int(block_metadata.get("block_definition_count") or 0),
        "written_record_count": written,
        "converted_record_count": converted,
        "loaded_record_count": len(block_rows),
        "skipped_record_count": skipped,
        "completion_rate": completion_rate,
        "block_set_match": bool(block_metadata.get("block_set_match")),
        "origin_mismatch_count": len(block_metadata.get("origin_mismatches") or []),
        "status": "PASS",
    }
    eligible = [row for row in up if row.get("t1_eligible")]
    sample = select_probability_sample(eligible, args.seed, args.pilot_size)
    challenge_frame, challenge_rows = challenge_groups(eligible, args.seed)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    policy = {
        "schema_version": "qto-universe-policy/1.0",
        "policy_version": "8F-WS-P-20260827-2",
        "scope": scope_evidence["scope"],
        "source_identity": {
            "path": str(args.source_dwg),
            "sha256": actual_source_hash,
            "bytes": args.source_dwg.stat().st_size,
        },
        "extractor_inputs": {
            "model_stream": str(args.model_stream),
            "model_stream_sha256": sha256_file(args.model_stream),
            "block_definition_stream": str(args.block_stream),
            "block_definition_stream_sha256": sha256_file(args.block_stream),
            "block_closure_metadata": str(args.block_metadata),
            "block_closure_metadata_sha256": sha256_file(args.block_metadata),
        },
        "scope_membership": "bbox intersects half-open scope; point objects use transformed insertion/anchor point",
        "block_closure": {
            "recursive": True,
            "max_depth": args.max_depth,
            "outside_root_with_inside_descendant": "retain root as ancestry evidence",
            "missing_definition": "retain in-scope INSERT as explicit unresolved placeholder",
            "cycle": "record closure event; do not recurse indefinitely",
        },
        "p_exemptions": [
            {"rule": "NESTED non-INSERT geometry", "reason": "closure evidence, not an independently adjudicated physical instance"},
            {"rule": "block family CAD_PATTERN ($LinePat$*)", "reason": "CAD display pattern instance"},
            {"rule": "outside root retained only as ancestry", "reason": "scope ancestry evidence"},
        ],
        "professional_decision": "none; all professional candidates remain UNRESOLVED until frozen T1/T1.5",
    }
    policy["policy_sha256"] = canonical_json_hash({key: value for key, value in policy.items() if key != "policy_sha256"})

    u0_version = "8F-WS-U0-20260827-2"
    up_version = "8F-WS-UP-20260827-2"
    for row in u0:
        row["universe_version"] = u0_version
        row["policy_version"] = policy["policy_version"]
    for row in up:
        row["universe_version"] = up_version
        row["u0_version"] = u0_version
        row["policy_version"] = policy["policy_version"]

    u0_path = output / "U0原始对象全集.jsonl.gz"
    up_path = output / "UP处理对象全集.jsonl"
    policy_path = output / "P范围提取与豁免策略.json"
    write_jsonl_gzip(u0_path, u0)
    write_jsonl(up_path, up)
    write_json(policy_path, policy)

    direct_context = [row for row in up if row["source_level"] == "MODEL"]
    sampled_top_instances = {
        str(row.get("top_instance_id")) for row in sample if row.get("top_instance_id")
    }
    nested_context = [
        row for row in u0
        if row.get("source_level") == "NESTED"
        and str(row.get("top_instance_id") or "") in sampled_top_instances
    ]
    preview_context = direct_context + nested_context
    preview_dir = output / "20对象试标预览"
    for index, row in enumerate(sample, 1):
        preview_name = f"{index:02d}_{stable_hash(row['object_id'])[:12]}.svg"
        row["preview_path"] = str(preview_dir / preview_name)
        row["preview_name"] = preview_name
        svg_preview(row, preview_context, preview_dir / preview_name, index)

    cards = []
    for row in sample:
        label = row.get("block_name") or row.get("entity_type") or ""
        cards.append(
            f"<section class='card' id='obj-{int(row['selection_order']):02d}'>"
            f"<h2>#{int(row['selection_order']):02d} {html.escape(row['object_id'])}</h2>"
            f"<img src='20对象试标预览/{html.escape(row['preview_name'])}' alt='对象局部预览'>"
            f"<p>形态：{html.escape(row['benchmark_shape'])}；图层：{html.escape(row.get('effective_layer') or '')}；对象：{html.escape(str(label))}；源记录：{html.escape(str(row.get('source_record_id') or ''))}</p>"
            "<p>请在 <code>T1对象标签.csv</code> 填写人工结论与依据；本页不保存输入。</p>"
            "</section>"
        )
    nav = " ".join(
        f"<a href='#obj-{index:02d}'>#{index:02d}</a>" for index in range(1, len(sample) + 1)
    )
    (output / "20对象试标预览索引.html").write_text(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>20对象试标预览</title>"
        "<style>body{font-family:'Microsoft YaHei',sans-serif;margin:24px;background:#f3f4f6;color:#17202a}"
        ".card{background:white;border:1px solid #d7dde5;border-radius:10px;padding:16px;margin:0 0 18px}"
        "img{max-width:960px;width:100%;border:1px solid #d7dde5}code{background:#eef2f7;padding:2px 5px}"
        ".nav{position:sticky;top:0;background:#ffffffee;border:1px solid #d7dde5;border-radius:8px;padding:10px;margin-bottom:18px}"
        ".nav a{display:inline-block;margin:3px 5px;color:#075985;text-decoration:none}</style>"
        "</head><body><h1>八层给排水阶段一：20对象试标预览</h1>"
        "<p>该批次仅用于估算 T1 人工标注工时，不是标准答案，也不计正式准确率。</p>"
        "<p>红色是待判对象，蓝色是其嵌套块展开图元，灰色是周边中性图元。每个对象同时提供近景与环境图；证据仍不足时必须回原 DWG 并标为 <code>UNRESOLVED</code>。</p>"
        f"<nav class='nav'>{nav}</nav>"
        + "".join(cards)
        + "</body></html>",
        encoding="utf-8",
    )
    (output / "20对象试标说明.md").write_text(
        "# 20 对象试标说明\n\n"
        "1. 打开 `20对象试标预览索引.html` 查看局部图形；必要时回到原 DWG 核对。\n"
        "2. 在 `T1对象标签.csv` 中只填写人工字段：对象类别、主责专业、系统、空间、采用/排除/未决、重复组、依据、标注人和时间。\n"
        "3. 证据不足时填写 `UNRESOLVED`，不得用模型建议或未反对代替人工确认。\n"
        "4. 在 `工时记录.csv` 逐对象记录分钟数和争议；完成 20 个后再冻结正式样本规模与 6～12 人时预算。\n"
        "5. 当前概率试标与挑战样本分开，挑战样本不得混入总体性能指标。\n",
        encoding="utf-8",
    )

    probability_fields = [
        "selection_order", "sample_set", "object_id", "source_record_id", "benchmark_shape", "sampling_stratum",
        "grid_cell", "entity_type", "effective_layer", "layer_family", "block_name", "block_family", "x", "y",
        "closure_status", "random_score", "preview_path",
    ]
    write_csv(output / "概率校准样本.csv", probability_fields, sample)
    challenge_fields = [
        "challenge_group_id", "challenge_type", "group_key", "group_size", "object_id", "source_record_id",
        "benchmark_shape", "entity_type", "effective_layer", "block_name", "x", "y", "label_status",
    ]
    write_csv(output / "挑战样本.csv", challenge_fields, challenge_rows)

    t1_rows = []
    for row in sample:
        t1_rows.append(
            {
                **row,
                "candidate_type": "LINE_OR_SYMBOL_UNRESOLVED" if row["benchmark_shape"] == "LINE_NETWORK" else "INSTANCE_OR_SYMBOL_UNRESOLVED",
                "candidate_reason": "neutral geometry only; requires drawing-reading adjudication",
                "suggested_disposition": "UNRESOLVED",
                "human_object_class": "",
                "human_professional_owner": "",
                "human_system": "",
                "human_space": "8层锁定范围",
                "human_disposition": "",
                "duplicate_group": "",
                "adjudication_basis": "",
                "annotator": "",
                "adjudicated_at": "",
                "label_status": "PENDING_HUMAN",
            }
        )
    t1_fields = probability_fields + [
        "candidate_type", "candidate_reason", "suggested_disposition", "human_object_class",
        "human_professional_owner", "human_system", "human_space", "human_disposition", "duplicate_group",
        "adjudication_basis", "annotator", "adjudicated_at", "label_status",
    ]
    write_csv(output / "T1对象标签.csv", t1_fields, t1_rows)
    write_csv(
        output / "T1.5规则绑定.csv",
        [
            "object_or_group_id", "rule_candidate_id", "rule_candidate_version", "trigger_evidence", "human_rule_id",
            "human_rule_version", "human_node", "human_disposition", "scope_and_exception", "annotator",
            "adjudication_basis", "label_status",
        ],
        [],
    )
    write_csv(
        output / "T2几何标准答案.csv",
        [
            "object_id", "quantity_name", "quantity_value", "quantity_unit", "calculation_method", "unit_factor",
            "t1_label_sha256", "t1_5_binding_sha256", "human_check_status", "gold_status",
        ],
        [],
    )
    write_csv(
        output / "T3规格抽样.csv",
        [
            "object_id", "attribute_name", "candidate_value", "human_value", "evidence_id", "stratum",
            "forced_inference", "annotator", "label_status",
        ],
        [],
    )
    write_csv(
        output / "工时记录.csv",
        ["sample_set", "object_id", "annotator_role", "started_at", "ended_at", "minutes", "disputed", "notes"],
        [],
    )

    frame = {
        "schema_version": SAMPLE_VERSION,
        "sampling_version": "8F-WS-SAMPLE-20260827-1",
        "seed": args.seed,
        "pilot_size": args.pilot_size,
        "u0": {"version": u0_version, "count": len(u0), "sha256": sha256_file(u0_path)},
        "up": {"version": up_version, "count": len(up), "sha256": sha256_file(up_path)},
        "grid": {"rows": 2, "columns": 4, "bounds": bounds},
        "eligible_count": len(eligible),
        "eligible_by_shape": dict(sorted(Counter(row["benchmark_shape"] for row in eligible).items())),
        "layer_families": dict(sorted(Counter(row["layer_family"] for row in eligible).items())),
        "block_families": dict(sorted(Counter(row["block_family"] for row in eligible if row["block_family"]).items())),
        "probability_selection": {
            "method": "shape quota then deterministic round-robin over grid and layer/block family strata",
            "selected_object_ids": [row["object_id"] for row in sample],
            "selected_sha256": canonical_json_hash([row["object_id"] for row in sample]),
        },
        "challenge_groups": challenge_frame,
        "challenge_group_count": len(challenge_frame),
        "challenge_rows_sha256": canonical_json_hash(challenge_rows),
        "manual_representative_selection_prohibited": True,
    }
    write_json(output / "抽样框.json", frame)

    difference = {
        "schema_version": "qto-universe-diff/1.0",
        "u0": {"version": u0_version, "count": len(u0), "sha256": sha256_file(u0_path)},
        "up": {"version": up_version, "count": len(up), "sha256": sha256_file(up_path)},
        "policy": {"version": policy["policy_version"], "sha256": policy["policy_sha256"]},
        "u0_minus_up_count": len(u0) - len(up),
        "u0_minus_up_by_reason": closure["p_disposition_counts"],
        "u0_by_source_level": dict(sorted(Counter(row["source_level"] for row in u0).items())),
        "up_by_shape": dict(sorted(Counter(row["benchmark_shape"] for row in up).items())),
        "failure_placeholders": [
            row["object_id"] for row in u0 if row.get("closure_status") not in {None, "RESOLVED"}
        ],
        "comparison_rule": "different U0 or UP hashes cannot be directly ranked; report common/new/missing/failure sets",
    }
    write_json(output / "对象全集差异.json", difference)
    write_json(output / "块定义闭包.json", closure)

    isolation = {
        "schema_version": "qto-hidden-isolation/1.0",
        "target_minimum": "SESSION",
        "current_tier": "NOT_ESTABLISHED",
        "current_reason": "pilot labels are not frozen; current maintenance conversation has seen sampling design",
        "required_next_action": "freeze labels in an annotation task, then run hidden calculation in a physically separate new task whose prompt contains no labels or defect descriptions",
        "shared_memory_rule": "any label or defect exposure through shared memory downgrades the claim to FILE",
        "hidden_object_ids": [],
        "business_claim_allowed": False,
    }
    write_json(output / "隐藏样本隔离记录.json", isolation)

    mutations = {
        "schema_version": "qto-benchmark-mutations/1.0",
        "mutation_set_version": "QTO-MUTATIONS-1.0",
        "mutations": [
            {"id": "M01", "operation": "delete_confirmed_object", "expected_failure": "SILENT_GOLD_OBJECT_MISSING"},
            {"id": "M02", "operation": "adopt_auxiliary_symbol", "expected_failure": "DISPOSITION_MISMATCH"},
            {"id": "M03", "operation": "wrong_owner_system_or_space", "expected_failure": "CLASSIFICATION_MISMATCH"},
            {"id": "M04", "operation": "wrong_rule_binding", "expected_failure": "RULE_BINDING_MISMATCH"},
            {"id": "M05", "operation": "wrong_specification", "expected_failure": "SPECIFICATION_MISMATCH"},
            {"id": "M06", "operation": "drop_exclusion_handoff", "expected_failure": "EXCLUSION_HANDOFF_INCOMPLETE"},
            {"id": "M07", "operation": "duplicate_unique_quantity_output", "expected_failure": "DUPLICATE_UNIQUE_OUTPUT"},
            {"id": "M08", "operation": "write_unresolved_as_zero", "expected_failure": "UNRESOLVED_WRITTEN_AS_ZERO"},
        ],
    }
    write_json(output / "变异清单.json", mutations)

    status = {
        "schema_version": "qto-phase1-status/1.0",
        "phase": "阶段一",
        "status": "PILOT_READY_PENDING_HUMAN_T1" if closure_complete else "BLOCKED_BLOCK_CLOSURE_INCOMPLETE",
        "completed": [
            "source identity frozen",
            "U0/P/UP generated",
            "20-object probability pilot generated",
            "challenge groups enumerated separately",
            "human annotation templates generated",
        ],
        "blocked_by_human_authority": [
            "T1 labels require a plumbing drawing reader",
            "T1.5 bindings require rule/node expertise where applicable",
            "T2/T3 gold cannot be frozen before T1/T1.5",
            "SESSION hidden test cannot start before gold is frozen",
        ],
        "global_skill_modified": False,
        "capability_or_maturity_upgraded": False,
    }
    write_json(output / "阶段一状态.json", status)

    manifest = {
        "schema_version": "qto-phase1-benchmark-manifest/1.0",
        "benchmark_id": "8F-WS-PHASE1-20260827-V2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan": {"path": str(args.plan), "sha256": sha256_file(args.plan)},
        "checklist": {"path": str(args.checklist), "sha256": sha256_file(args.checklist)},
        "builder": {"path": str(Path(__file__).resolve()), "version": BUILDER_VERSION, "sha256": sha256_file(Path(__file__).resolve())},
        "toolchain": {},
        "source": policy["source_identity"],
        "scope": scope_evidence["scope"],
        "inputs": policy["extractor_inputs"],
        "artifacts": {},
        "historical_results_used_as_input": False,
        "global_runtime_modified": False,
        "truth_state": "pilot annotation template only; no model-generated standard answers",
    }
    benchmark_root = Path(__file__).resolve().parents[1]
    tool_paths = sorted(
        [*benchmark_root.glob("scripts/*.py"), *benchmark_root.glob("schemas/*.json"), *benchmark_root.glob("tests/*.py")],
        key=lambda path: str(path.relative_to(benchmark_root)),
    )
    for tool_path in tool_paths:
        relative = str(tool_path.relative_to(benchmark_root))
        manifest["toolchain"][relative] = {"bytes": tool_path.stat().st_size, "sha256": sha256_file(tool_path)}
    for artifact in sorted(output.rglob("*"), key=lambda path: str(path.relative_to(output))):
        if artifact.is_file() and artifact.name != "阶段一基准清单.json":
            relative = str(artifact.relative_to(output))
            manifest["artifacts"][relative] = {"bytes": artifact.stat().st_size, "sha256": sha256_file(artifact)}
    write_json(output / "阶段一基准清单.json", manifest)

    pending_score = {
        "schema_version": "qto-score-report/1.0",
        "status": "NOT_SCORED",
        "reason": "T1/T1.5 gold and SESSION hidden sample are not frozen",
        "scorer_available": False,
        "hard_gate_passed": False,
        "business_claim_allowed": False,
    }
    write_json(output / "评分报告.json", pending_score)
    (output / "评分报告.md").write_text(
        "# 阶段一评分报告\n\n"
        "当前状态：**未评分**。20 个对象试标包已经生成，但 T1/T1.5 尚未由具备专业能力的人冻结，"
        "因此不得把模型建议或未反对视为标准答案，也不得启动正式评分或宣称阶段一通过。\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
