#!/usr/bin/env python3
"""Exact HATCH selection adapter using existing surface traversal and geometry."""
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


def select_hatches(source_dxf: Path, selection: dict, *, layout: str, unit_scale: float):
    digest = hashlib.sha256(source_dxf.read_bytes()).hexdigest()
    if selection.get("source_sha256", "").lower() != digest.lower():
        raise SurfaceSelectionError("source_sha256 does not match DXF")
    if not isinstance(layout, str) or not layout or selection.get("layout") != layout:
        raise SurfaceSelectionError("layout does not match")
    if not isinstance(unit_scale, (int, float)) or isinstance(unit_scale, bool) or unit_scale <= 0:
        raise SurfaceSelectionError("invalid unit scale")

    wanted = set(selection.get("cadinst_ids", []))
    if not wanted:
        raise SurfaceSelectionError("cadinst_ids required")

    doc = EXTRACT.read_dxf_with_diagnostics(source_dxf)[0]
    found = []
    for identity, entity, failure, _ in EXTRACT.walk(doc.modelspace()):
        object_id = "CADINST-" + "/".join(identity)
        if object_id not in wanted:
            continue
        if failure:
            raise SurfaceSelectionError({"cadinst_geometry_failure": object_id, "failure": failure})
        if entity.dxftype() != "HATCH":
            raise SurfaceSelectionError({"cadinst_not_hatch": object_id})
        found.append((object_id, entity))

    missing = sorted(wanted - {x[0] for x in found})
    if missing:
        raise SurfaceSelectionError({"unmatched_cadinst": missing})

    return {
        "source_sha256": digest,
        "layout": layout,
        "regions": [
            {"cadinst_id": object_id, "area_m2": EXTRACT.hatch_geometry(entity, unit_scale).area}
            for object_id, entity in found
        ],
    }
