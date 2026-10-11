#!/usr/bin/env python3
"""Select HATCH regions from a verified qto recognition workbench index."""
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


HERE = Path(__file__).resolve().parent
EXTRACT = _load("extract_surface_regions", HERE / "extract_surface_regions.py")
WORKBENCH = _load("qto_recognition_workbench", Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts/qto_recognition_workbench.py")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verified_objects(index):
    try:
        return WORKBENCH._verified_index(index)
    except Exception as exc:
        raise SurfaceSelectionError(str(exc)) from exc


def _entity_by_handle(doc, handle):
    for entity in doc.modelspace():
        if str(entity.dxf.get("handle") or "") == str(handle):
            return entity
    for block in doc.blocks:
        for entity in block:
            if str(entity.dxf.get("handle") or "") == str(handle):
                return entity
    return None


def select_hatches(source_dxf: Path, selection: dict, *, index: dict, unit_scale: float):
    digest = _sha256(source_dxf)
    objects = _verified_objects(index)
    if index.get("source", {}).get("sha256") != digest:
        raise SurfaceSelectionError("source_sha256 does not match verified index")
    layout = selection.get("layout")
    if layout and layout != index.get("layout"):
        raise SurfaceSelectionError("layout does not match verified index")
    wanted = selection.get("cadinst_ids", [])
    if not wanted:
        raise SurfaceSelectionError("cadinst_ids required")
    missing = sorted(set(wanted) - set(objects))
    if missing:
        raise SurfaceSelectionError({"unmatched_cadinst": missing})

    doc = EXTRACT.read_dxf_with_diagnostics(source_dxf)[0]
    regions = []
    for oid in wanted:
        row = objects[oid]
        if row.get("entity_type") != "HATCH":
            raise SurfaceSelectionError({"cadinst_not_hatch": oid})
        entity = _entity_by_handle(doc, row.get("source_handle"))
        if entity is None:
            raise SurfaceSelectionError({"unresolved_source_handle": oid})
        try:
            geometry = EXTRACT.hatch_geometry(entity, unit_scale)
        except Exception as exc:
            raise SurfaceSelectionError({"geometry_failed": oid, "reason": str(exc)}) from exc
        regions.append({"cadinst_id": oid, "area_m2": geometry.area})

    return {"source_sha256": digest, "layout": index.get("layout"), "regions": regions}
