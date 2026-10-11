#!/usr/bin/env python3
"""Exact HATCH selection adapter.

This adapter reuses qto_cad_geometry instance expansion and the existing HATCH
geometry routine. It is not a second geometry engine.
"""
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
GEOM = _load("qto_cad_geometry", ROOT / "_qto-shared-evidence/scripts/qto_cad_geometry.py")
EXTRACT = _load("extract_surface_regions", Path(__file__).resolve().parent / "extract_surface_regions.py")


def select_hatches(source_dxf: Path, selection: dict, *, layout: str):
    digest = hashlib.sha256(source_dxf.read_bytes()).hexdigest()
    if selection.get("source_sha256", "").lower() != digest.lower():
        raise SurfaceSelectionError("source_sha256 does not match DXF")
    if selection.get("layout") != layout:
        raise SurfaceSelectionError("layout does not match")

    wanted = set(selection.get("cadinst_ids", []))
    if not wanted:
        raise SurfaceSelectionError("cadinst_ids required")

    doc, _ = GEOM.read_dxf_with_diagnostics(source_dxf)
    found = []
    for identity, entity, failure, _ in GEOM.walk(doc.modelspace()):
        if failure or entity.dxftype() != "HATCH":
            continue
        object_id = "CADINST-" + "/".join(identity)
        if object_id in wanted:
            found.append((object_id, entity))

    missing = sorted(wanted - {x[0] for x in found})
    if missing:
        raise SurfaceSelectionError({"unmatched_cadinst": missing})

    return {
        "source_sha256": digest,
        "layout": layout,
        "regions": [
            {
                "cadinst_id": object_id,
                "area_m2": EXTRACT.hatch_geometry(entity, 1.0).area,
            }
            for object_id, entity in found
        ],
    }
