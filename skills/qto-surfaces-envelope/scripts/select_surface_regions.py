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


def _reject_unverified_ancestors(objects, oid):
    seen = set()
    current = oid
    while current:
        if current in seen:
            raise SurfaceSelectionError("parent_object_id cycle detected")
        seen.add(current)
        row = objects.get(current)
        if row is None:
            raise SurfaceSelectionError("missing parent_object_id")
        if row.get("entity_type") == "INSERT":
            issues = row.get("issues") or []
            text = " ".join(str(x) for x in issues)
            if ("外部参照需核验载入与版本；当前仅列出实际可见定义对象" in text
                    or "可能含动态块或扩展数据；可见状态需原生CAD或模型另核" in text):
                raise SurfaceSelectionError(
                    {"unverified_parent_insert": current}
                )
        current = row.get("parent_object_id")


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
    from ezdxf.math import Matrix44
    if not isinstance(values, (list, tuple)) or len(values) != 16:
        raise SurfaceSelectionError("invalid instance_transform")
    return Matrix44.ucs((values[0], values[4], values[8]), (values[1], values[5], values[9]), (values[2], values[6], values[10]), (values[3], values[7], values[11]))


def _transform_entity(entity, matrix):
    if not matrix:
        return entity
    from ezdxf.transform import copies
    logs, entities = copies([entity], _workbench_matrix(matrix))
    if logs:
        raise SurfaceSelectionError({"instance_transform_failed": logs[0].message})
    return entities[0]


def _unit_scale_from_evidence(doc, unit, unit_evidence):
    insunits = int(doc.header.get("$INSUNITS", 0))

    if unit not in {"mm", "m"}:
        raise SurfaceSelectionError("unknown declared unit")

    if insunits not in {0, 4, 6}:
        raise SurfaceSelectionError("unsupported INSUNITS value")

    if insunits == 4 and unit != "mm":
        raise SurfaceSelectionError("declared unit conflicts with INSUNITS")

    if insunits == 6 and unit != "m":
        raise SurfaceSelectionError("declared unit conflicts with INSUNITS")

    if insunits == 0:
        if not isinstance(unit_evidence, str) or not unit_evidence.strip():
            raise SurfaceSelectionError("unknown unit without traceable evidence")

    return {"mm": 0.001, "m": 1.0}[unit]


def _resolve_selection_ids(objects, selection):
    cadinst_ids = selection.get("cadinst_ids")
    layer_names = selection.get("layer_names")


    if "cadinst_ids" in selection:
        if (not isinstance(cadinst_ids, list) or not cadinst_ids or
                any(not isinstance(x, str) or not x for x in cadinst_ids)):
            raise SurfaceSelectionError("invalid cadinst_ids")
        if len(cadinst_ids) != len(set(cadinst_ids)):
            raise SurfaceSelectionError("duplicate cadinst_ids")
        selected = []
        for oid in cadinst_ids:
            if oid not in objects or objects[oid].get("entity_type") != "HATCH":
                raise SurfaceSelectionError({"invalid_cadinst": oid})
            selected.append(oid)
    else:
        selected = None


    if "layer_names" in selection:
        if (not isinstance(layer_names, list) or not layer_names or
                any(not isinstance(x, str) or not x for x in layer_names)):
            raise SurfaceSelectionError("invalid layer_names")
        hatch_layers = {
            row.get("effective_layer")
            for row in objects.values()
            if row.get("entity_type") == "HATCH"
        }
        unmatched = [x for x in layer_names if x not in hatch_layers]
        if unmatched:
            raise SurfaceSelectionError({"unmatched_layer_names": unmatched})
        layer_selected = [
            oid for oid, row in objects.items()
            if row.get("entity_type") == "HATCH"
            and row.get("effective_layer") in layer_names
        ]
        if not layer_selected:
            raise SurfaceSelectionError("no layer match")
        selected = layer_selected if selected is None else [
            oid for oid in selected if oid in layer_selected
        ]


    if selected is None or not selected:
        raise SurfaceSelectionError("selection matched no hatch")
    return selected


def select_hatches(source_dxf: Path, selection: dict, *, index: dict, unit: str, unit_evidence: str):
    digest = _sha256(source_dxf)
    objects = _verified_objects(index)
    if index.get("source_identity_issues"):
        raise SurfaceSelectionError("index source_identity_issues prevent normal quantity output")
    if index.get("source", {}).get("sha256") != digest:
        raise SurfaceSelectionError("source_sha256 does not match verified index")
    layout = index.get("layout")
    if selection.get("source_sha256") != digest or selection.get("layout") != layout:
        raise SurfaceSelectionError("selection binding mismatch")
    raw_ids = _resolve_selection_ids(objects, selection)
    doc, diagnostics = EXTRACT.read_dxf_with_diagnostics(source_dxf)
    if diagnostics.get("source_identity_issues"):
        raise SurfaceSelectionError("source_identity_issues prevent normal quantity output")
    scale = _unit_scale_from_evidence(doc, unit, unit_evidence)
    regions = []
    for oid in raw_ids:
        row = objects[oid]
        _reject_unverified_ancestors(objects, oid)
        if row.get("entity_type") != "HATCH":
            raise SurfaceSelectionError({"cadinst_not_hatch": oid})
        entity = _entity_by_handle(doc, row.get("source_handle"))
        if entity is None:
            raise SurfaceSelectionError({"unresolved_source_handle": oid})
        geometry = EXTRACT.hatch_geometry(_transform_entity(entity, row.get("instance_transform")), scale)
        regions.append({
            "cadinst_id": oid,
            "region_id": oid,
            "area_m2": geometry.area,
            "bounds_m": list(geometry.bounds),
            "polygons": EXTRACT.geometry_record(geometry),
            "status": "geometry_extracted",
        })
    _verified_objects(index)
    if _sha256(source_dxf) != digest:
        raise SurfaceSelectionError("source changed during selection")
    return {
        "source_sha256": digest,
        "layout": layout,
        "unit": unit,
        "unit_evidence": unit_evidence,
        "source_read_diagnostics": diagnostics.get("source_read_warnings", []),
        "source_identity_issues": diagnostics.get("source_identity_issues", []),
        "index_sha256": index["index_sha256"],
        "conversion_warnings": index.get("conversion_warnings", []),
        "formal_adoption_allowed": False,
        "adoption_scope": "working",
        "regions": regions,
    }


def main():
    import argparse
    import json
    import sys


    parser = argparse.ArgumentParser()
    parser.add_argument("--dxf", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--selection-file", required=True)
    parser.add_argument("--unit", required=True, choices=["mm", "m"])
    parser.add_argument("--unit-evidence", default="")
    args = parser.parse_args()


    try:
        index = json.loads(Path(args.index).read_text(encoding="utf-8"))
        selection = json.loads(Path(args.selection_file).read_text(encoding="utf-8"))
        result = select_hatches(
            Path(args.dxf),
            selection,
            index=index,
            unit=args.unit,
            unit_evidence=args.unit_evidence,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2




if __name__ == "__main__":
    raise SystemExit(main())
