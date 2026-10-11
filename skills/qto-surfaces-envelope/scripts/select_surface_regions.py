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


def _workbench_matrix(values):
    """Convert qto column-vector affine layout to ezdxf Matrix44.

    qto stores basis columns and translation at 3/7/11. Do not pass the raw
    list to ezdxf: transform.copies requires Matrix44 and silently leaves the
    entity unchanged otherwise.
    """
    from ezdxf.math import Matrix44
    if not isinstance(values, (list, tuple)) or len(values) != 16:
        raise SurfaceSelectionError("invalid instance_transform")
    ux = (values[0], values[4], values[8])
    uy = (values[1], values[5], values[9])
    uz = (values[2], values[6], values[10])
    origin = (values[3], values[7], values[11])
    return Matrix44.ucs(ux, uy, uz, origin)


def _transform_entity(entity, matrix):
    if not matrix:
        return entity
    try:
        from ezdxf.transform import copies
        logs, entities = copies([entity], _workbench_matrix(matrix))
        if logs:
            raise SurfaceSelectionError({"instance_transform_failed": logs[0].message})
        return entities[0]
    except SurfaceSelectionError:
        raise
    except Exception as exc:
        raise SurfaceSelectionError({"instance_transform_failed": str(exc)}) from exc


def select_hatches(source_dxf: Path, selection: dict, *, index: dict, unit_scale: float):
    digest = _sha256(source_dxf)
    objects = _verified_objects(index)
    if index.get("source", {}).get("sha256") != digest:
        raise SurfaceSelectionError("source_sha256 does not match verified index")
    layout = index.get("layout")
    if selection.get("source_sha256") != digest:
        raise SurfaceSelectionError("selection source_sha256 required and must match")
    if selection.get("layout") != layout:
        raise SurfaceSelectionError("selection layout does not match verified index")
    raw_ids = selection.get("cadinst_ids", [])
    if not isinstance(raw_ids, list) or not raw_ids:
        raise SurfaceSelectionError("cadinst_ids required")
    if len(raw_ids) != len(set(raw_ids)):
        raise SurfaceSelectionError("duplicate cadinst_ids are not allowed")
    missing = sorted(set(raw_ids) - set(objects))
    if missing:
        raise SurfaceSelectionError({"unmatched_cadinst": missing})
    doc, diagnostics = EXTRACT.read_dxf_with_diagnostics(source_dxf)
    if diagnostics.get("source_identity_issues"):
        raise SurfaceSelectionError("source_identity_issues prevent normal quantity output")
    regions = []
    for oid in raw_ids:
        row = objects[oid]
        if row.get("entity_type") != "HATCH":
            raise SurfaceSelectionError({"cadinst_not_hatch": oid})
        entity = _entity_by_handle(doc, row.get("source_handle"))
        if entity is None:
            raise SurfaceSelectionError({"unresolved_source_handle": oid})
        entity = _transform_entity(entity, row.get("instance_transform"))
        try:
            geometry = EXTRACT.hatch_geometry(entity, unit_scale)
        except Exception as exc:
            raise SurfaceSelectionError({"geometry_failed": oid, "reason": str(exc)}) from exc
        regions.append({"cadinst_id": oid, "area_m2": geometry.area, "bounds_m": list(geometry.bounds)})
    return {"source_sha256": digest, "layout": layout, "source_read_diagnostics": diagnostics.get("source_read_warnings", []), "source_identity_issues": diagnostics.get("source_identity_issues", []), "regions": regions}
