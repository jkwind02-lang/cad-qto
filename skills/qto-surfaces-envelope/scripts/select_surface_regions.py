#!/usr/bin/env python3
"""Exact HATCH selection adapter using workbench CADINST identities."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


class SurfaceSelectionError(ValueError):
    pass


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = _load("extract_surface_regions", Path(__file__).resolve().parent / "extract_surface_regions.py")


def select_hatches(source_dxf: Path, selection: dict, *, workbench_rows: list[dict], unit_scale: float):
    digest = hashlib.sha256(source_dxf.read_bytes()).hexdigest()
    if selection.get("source_sha256", "").lower() != digest.lower():
        raise SurfaceSelectionError("source_sha256 does not match DXF")

    wanted = set(selection.get("cadinst_ids", []))
    if not wanted:
        raise SurfaceSelectionError("cadinst_ids required")

    rows = {r.get("object_id"): r for r in workbench_rows}
    missing = sorted(wanted - set(rows))
    if missing:
        raise SurfaceSelectionError({"unmatched_cadinst": missing})

    for object_id in wanted:
        row = rows[object_id]
        if row.get("entity_type") != "HATCH":
            raise SurfaceSelectionError({"cadinst_not_hatch": object_id})

    doc = EXTRACT.read_dxf_with_diagnostics(source_dxf)[0]
    found = []
    # Use existing extractor traversal only to obtain geometry. The identity
    # comes from workbench_rows, never from this traversal.
    by_source_path = {tuple(r.get("source_path", [])): r for r in workbench_rows}
    for _, entity, failure, context in EXTRACT.walk(doc.modelspace()):
        if failure or entity.dxftype() != "HATCH":
            continue
        path = tuple(context.get("source_path", [])) if isinstance(context, dict) else ()
        row = by_source_path.get(path)
        if row and row.get("object_id") in wanted:
            found.append((row["object_id"], entity))

    if len(found) != len(wanted):
        raise SurfaceSelectionError({"unresolved_geometry": sorted(wanted - {x[0] for x in found})})

    return {
        "source_sha256": digest,
        "regions": [
            {"cadinst_id": oid, "area_m2": EXTRACT.hatch_geometry(entity, unit_scale).area}
            for oid, entity in found
        ],
    }
