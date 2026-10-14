#!/usr/bin/env python3
"""Bounded standard-DXF viewport evidence, without engineering adoption.

The in-memory API consumes a current document and its complete Model workbench
index; the caller must keep those inputs bound to the same source. The CLI
constructs that index from a hashed source itself and does not accept old index
files. Projection matrices use ezdxf's convention, not the QTO instance-matrix
convention. Actual source Z and a chosen inverse-projection plane remain distinct.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from collections import Counter
from pathlib import Path
import sys

from ezdxf.math import Matrix44, Vec3

from qto_cad_geometry import read_dxf_with_diagnostics, transform_point
from qto_cad_source_contract import verify_source_file
from qto_recognition_workbench import SCHEMA as INDEX_SCHEMA, build_index, index_digest

SCHEMA = "qto-standard-viewport-evidence/1.0"
DOMAIN = "positive_Z_top_orthographic_zero_twist_rectangular_no_depth_clip"
_PARENT_COVERAGE_ONLY_ISSUES = frozenset({
    "块范围仅覆盖已解析内容，范围外仍可能存在未解析子对象",
})
_KEYS = (
    "handle", "id", "status", "flags", "center", "width", "height",
    "view_center_point", "view_height", "view_target_point", "view_direction_vector",
    "view_twist_angle", "clipping_boundary_handle", "front_clip_plane_z_value",
    "back_clip_plane_z_value", "ucs_per_viewport", "ucs_origin", "ucs_x_axis",
    "ucs_y_axis", "render_mode",
)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value, lengths=(2, 3)):
    return isinstance(value, (list, tuple)) and len(value) in lengths and all(_finite(n) for n in value)


def _box(value):
    return _vector(value, (4,)) and value[0] <= value[2] and value[1] <= value[3]


def _record(value):
    """Make unsupported nonfinite input reportable without invalid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Vec3) or hasattr(value, "x"):
        return [_record(n) for n in value]
    if isinstance(value, (list, tuple)):
        return [_record(n) for n in value]
    return value


def viewport_context(vp):
    """Retain raw parameters; accept only the explicitly verified projection domain."""
    raw, reasons = {}, []
    try:
        if vp.dxftype() != "VIEWPORT":
            raise ValueError("not_VIEWPORT")
        raw = {key: _record(vp.dxf.get(key, None)) for key in _KEYS}
        raw["frozen_layers"] = list(vp.frozen_layers)
    except (AttributeError, TypeError, ValueError) as exc:
        reasons.append("invalid_viewport_record:" + type(exc).__name__)
    out = dict(raw=raw, supported=False, unsupported_reasons=reasons, domain=DOMAIN,
               quantity_calculation=False, formal_adoption=False)
    if reasons:
        return out
    flags = raw["flags"] or 0
    if not _finite(flags) or int(flags) != flags or flags < 0:
        reasons.append("invalid_flags")
        flags = 0
    flags = int(flags)
    if (not _finite(raw["id"]) or raw["id"] <= 1
            or not _finite(raw["status"]) or raw["status"] <= 0 or flags & 131072):
        reasons.append("inactive_or_paper_overall_viewport")
    for bit, name in ((1, "perspective"), (2, "front_clip"), (4, "back_clip"),
                      (16, "front_clip_not_at_eye"), (65536, "non_rectangular_clip")):
        if flags & bit:
            reasons.append(name)
    if raw["clipping_boundary_handle"] not in (None, "", "0"):
        reasons.append("clip_boundary_handle_present")
    for key in ("center", "view_center_point", "view_target_point", "view_direction_vector"):
        if not _vector(raw[key]):
            reasons.append("nonfinite_or_missing_" + key)
    if _vector(raw["view_direction_vector"]):
        direction = Vec3(raw["view_direction_vector"])
        magnitude = math.hypot(direction.x, direction.y, direction.z)
        if (not math.isfinite(magnitude) or not magnitude or direction.z <= 0
                or abs(direction.x) / magnitude > 1e-10
                or abs(direction.y) / magnitude > 1e-10):
            reasons.append("non_top_view")
    twist = raw["view_twist_angle"]
    if twist is not None and not _finite(twist):
        reasons.append("invalid_view_twist_angle")
    elif abs(twist or 0) > 1e-12:
        reasons.append("nonzero_twist")
    for key in ("width", "height", "view_height"):
        if not _finite(raw[key]) or raw[key] <= 0:
            reasons.append("invalid_" + key)
    view_center = raw["view_center_point"]
    if _vector(view_center) and len(view_center) > 2 and abs(view_center[2]) > 1e-9:
        reasons.append("nonzero_z_view_center_point")
    # A finite target.z is valid here: no front/back clipping is enabled.
    if reasons:
        return out
    try:
        bounds = list(vp.get_modelspace_limits())
        matrix = vp.get_transformation_matrix()
        inverse = matrix.copy()
        inverse.inverse()
        center, target, vc = Vec3(raw["center"]), Vec3(raw["view_target_point"]), Vec3(view_center)
        width, height = raw["width"] / 2, raw["height"] / 2
        scale = vp.get_scale()
        if not _box(bounds) or not _finite(scale) or scale <= 0:
            raise ValueError("nonfinite_projection")
        paper = [[center.x-width, center.y-height, 0], [center.x+width, center.y-height, 0],
                 [center.x+width, center.y+height, 0], [center.x-width, center.y+height, 0]]
        back = [list(inverse.transform(p)) for p in paper]
        model = [[bounds[0], bounds[1], 0], [bounds[2], bounds[1], 0],
                 [bounds[2], bounds[3], 0], [bounds[0], bounds[3], 0]]
        forward = [list(matrix.transform(p)) for p in model]
        if not all(_vector(p, (3,)) for p in back + forward):
            raise ValueError("nonfinite_projection")
        inverse_bounds = [min(p[0] for p in back), min(p[1] for p in back),
                          max(p[0] for p in back), max(p[1] for p in back)]
        tolerance = max(bounds[2]-bounds[0], bounds[3]-bounds[1], 1) * 1e-10
        error = max(abs(a-b) for a, b in zip(bounds, inverse_bounds))
        paper_error = max(abs(a-b) for p, q in zip(forward, paper) for a, b in zip(p[:2], q[:2]))
        if error > tolerance or paper_error > max(raw["width"], raw["height"], 1) * 1e-10:
            raise ValueError("projection_crosscheck_failed")
        vw, vh = raw["view_height"] * raw["width"] / raw["height"], raw["view_height"]
        out.update(supported=True, model_bounds=bounds,
                   paper_bounds=[center.x-width, center.y-height, center.x+width, center.y+height],
                   model_to_paper_matrix=list(matrix), paper_to_model_matrix=list(inverse),
                   projection_matrix_convention="ezdxf_row_vector_row_major",
                   paper_corners=paper, inverse_projected_model_corners=back,
                   forward_projected_paper_corners=forward, api_crosscheck_max_error=error,
                   paper_roundtrip_max_error=paper_error, tolerance_raw=tolerance, scale=scale,
                   legacy_without_target_bounds=[vc.x-vw/2, vc.y-vh/2, vc.x+vw/2, vc.y+vh/2],
                   inverse_projection_plane=dict(paper_z=0, model_z=target.z,
                       meaning="chosen inverse plane; not the source entity elevation"))
    except (ArithmeticError, AttributeError, TypeError, ValueError) as exc:
        reasons.append("projection_failed:" + str(exc))
    return out


def geometric_relation(row, bounds):
    """Classify complete geometry or a text anchor; unknown is never outside."""
    if not _box(bounds):
        return "unknown"
    status, box, anchor = row.get("geometry_status"), row.get("bbox"), row.get("anchor")
    if row.get("issues"):
        return "unknown"
    if status == "location_only" and _vector(anchor):
        return ("anchor_inside" if bounds[0] <= anchor[0] <= bounds[2]
                and bounds[1] <= anchor[1] <= bounds[3] else "anchor_outside")
    if status not in ("measurable", "container") or not _box(box):
        return "unknown"
    if status == "container" and not row.get("bounds_complete"):
        return "unknown"
    if box[2] < bounds[0] or box[0] > bounds[2] or box[3] < bounds[1] or box[1] > bounds[3]:
        return "outside"
    if box[0] >= bounds[0] and box[1] >= bounds[1] and box[2] <= bounds[2] and box[3] <= bounds[3]:
        return "fully_inside"
    return "boundary_overlap_candidate"


def visibility(row, byid, doc, context):
    """Recorded plotting eligibility only, not dynamic-state/occlusion acceptance."""
    reasons, path, visited = [], [], set()
    frozen = {str(name).casefold() for name in context.get("raw", {}).get("frozen_layers", [])}
    current = row
    while current is not None:
        oid, name = current.get("object_id"), current.get("effective_layer")
        if oid in visited:
            reasons.append("cyclic_parent_path:" + str(oid))
            break
        visited.add(oid)
        try:
            layer = doc.layers.get(name)
            state = dict(object_id=oid, layer=name, entity_visible=current.get("visible_in_entity"),
                         off=layer.is_off(), frozen=layer.is_frozen(), plot=layer.dxf.get("plot", 1),
                         viewport_frozen=str(name).casefold() in frozen, issues=current.get("issues"))
            path.append(state)
            if not isinstance(state["entity_visible"], bool):
                reasons.append("entity_visibility_unknown:" + str(oid))
            elif not state["entity_visible"]:
                reasons.append("entity_invisible:" + str(oid))
            if (state["off"] or state["frozen"] or not state["plot"]
                    or state["viewport_frozen"] or str(name).casefold() == "defpoints"):
                reasons.append("layer_not_plot_visible:" + str(oid))
        except (AttributeError, KeyError, TypeError, ValueError):
            reasons.append("missing_or_invalid_layer:" + str(name))
        parent = current.get("parent_object_id")
        if parent and parent not in byid:
            reasons.append("missing_parent_object:" + str(parent))
            break
        current = byid.get(parent) if parent else None
    unknown = any(reason.startswith(("cyclic_parent_path:", "missing_parent_object:", "missing_or_invalid_layer:",
                                    "entity_visibility_unknown:")) for reason in reasons)
    return dict(eligible_by_recorded_states=not reasons, reasons=reasons, ancestor_chain=path,
                recorded_state_status="unknown" if unknown else "ineligible" if reasons else "eligible",
                meaning="recorded display eligibility only; unresolved dynamic states remain separate")


def evaluate(row, byid, doc, context):
    supported = context.get("supported")
    return dict(object=copy.deepcopy(row),
                geometric_relation=geometric_relation(row, context["model_bounds"]) if supported else "unsupported_viewport",
                legacy_relation=geometric_relation(row, context["legacy_without_target_bounds"]) if supported else "unsupported_viewport",
                visibility=visibility(row, byid, doc, context),
                applicability="model_interpretation_required_no_material_or_quantity_adoption")


def analytic_check(ctx, extra_z=None):
    """Cross-check the API against a closed form, without inferring entity Z."""
    if not ctx.get("supported"):
        return dict(supported=False, reasons=["unsupported_viewport"])
    try:
        raw = ctx["raw"]
        scale = raw["height"] / raw["view_height"]
        target, vc, center = (Vec3(raw[k]) for k in ("view_target_point", "view_center_point", "center"))
        mx, my = target.x+vc.x, target.y+vc.y
        hw, hh = raw["width"]/(2*scale), raw["height"]/(2*scale)
        bounds = [mx-hw, my-hh, mx+hw, my+hh]
        maximum = max(abs(a-b) for a, b in zip(bounds, ctx["model_bounds"]))
        matrix, inverse = Matrix44(ctx["model_to_paper_matrix"]), Matrix44(ctx["paper_to_model_matrix"])
        zs = list(extra_z) if extra_z is not None else [-1., 0., target.z, target.z+1.]
        if not zs or not all(_finite(z) for z in zs):
            raise ValueError("invalid_sample_depths")
        samples, back = [], []
        for x, y in ((mx, my), (bounds[0], bounds[1]), (bounds[2], bounds[3])):
            for z in zs:
                expected = [scale*(x-target.x-vc.x)+center.x, scale*(y-target.y-vc.y)+center.y, scale*(z-target.z)]
                actual = list(matrix.transform((x, y, z)))
                error = max(abs(a-b) for a, b in zip(expected, actual))
                if error > max(ctx["tolerance_raw"]*scale, 1e-9):
                    raise ValueError("closed_form_projection_mismatch")
                samples.append(dict(model=[x, y, z], closed_form=expected, API_matrix=actual, max_error=error))
        for pz in (-1., 0., 1.):
            actual, expected = list(inverse.transform((center.x, center.y, pz))), [mx, my, target.z+pz/scale]
            error = max(abs(a-b) for a, b in zip(expected, actual))
            if error > max(ctx["tolerance_raw"], 1e-8):
                raise ValueError("inverse_plane_mismatch")
            back.append(dict(paper=[center.x, center.y, pz], model=actual, expected_plane=expected, max_error=error))
        if maximum > ctx["tolerance_raw"]:
            raise ValueError("closed_form_bounds_mismatch")
        return dict(supported=True, formula="paper.x=s*(model.x-T.x-V.x)+C.x; paper.y=s*(model.y-T.y-V.y)+C.y; matrix.z=s*(model.z-T.z)",
                    closed_form_bounds=bounds, bounds_max_error=maximum, point_samples=samples,
                    inverse_depth_samples=back, meaning="inverse depth defines a plane, not actual entity elevation; algebra/API agreement is not engineering adoption")
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        return dict(supported=False, reasons=["analytic_check_failed:" + str(exc)])


def line_source_proof(row, byid, doc, ctx):
    """Compare an ordinary LINE's indexed WCS with its actual simple INSERT chain."""
    failure = dict(supported=False, source_handle=row.get("source_handle"), source_path=row.get("source_path"))
    if not ctx.get("supported"):
        return dict(failure, reasons=["unsupported_viewport"])
    try:
        path = row.get("source_path")
        if row.get("entity_type") != "LINE" or not isinstance(path, list) or not path:
            raise ValueError("ordinary_LINE_path_required")
        if row.get("issues") or row.get("geometry_status") != "measurable":
            raise ValueError("unresolved_LINE_geometry")
        if any(not isinstance(handle, str) or "[" in handle or "]" in handle for handle in path):
            raise ValueError("array_or_unknown_parent_path_unsupported")
        if path[-1] != row.get("source_handle"):
            raise ValueError("source_path_handle_mismatch")
        source = doc.entitydb.get(path[-1])
        if source is None or source.dxftype() != "LINE":
            raise ValueError("source_LINE_missing")
        parts = (row.get("geometry") or {}).get("parts", [])
        if len(parts) != 1 or parts[0].get("kind") != "line":
            raise ValueError("single_indexed_LINE_required")
        raw = [list(source.dxf.start), list(source.dxf.end)]
        indexed = [parts[0]["start"], parts[0]["end"]]
        if not all(_vector(p, (3,)) for p in raw+indexed):
            raise ValueError("invalid_LINE_endpoints")
        qto_matrix = row["instance_transform"]
        from_index = [transform_point(p, qto_matrix) for p in raw]
        if not all(_vector(p, (3,)) for p in from_index):
            raise ValueError("nonfinite_indexed_LINE_transform")
        # QTO uses column vectors; ezdxf uses row vectors. Do not change QTO.
        matrix = Matrix44([qto_matrix[j*4+i] for i in range(4) for j in range(4)])
        transform, parents, coverage_warnings = Matrix44(), [], []
        definition = doc.modelspace()
        parent_oid = None
        for depth, handle in enumerate(path[:-1]):
            parent = doc.entitydb.get(handle)
            if parent is None or parent.dxftype() != "INSERT" or parent not in definition:
                raise ValueError("unsupported_parent_path")
            if parent.dxf.row_count != 1 or parent.dxf.column_count != 1:
                raise ValueError("array_parent_unsupported")
            parent_row = byid.get("CADINST-Model/" + "/".join(path[:depth+1]))
            if parent_row is None or parent_row.get("parent_object_id") != parent_oid:
                raise ValueError("index_parent_chain_mismatch")
            parent_issues = parent_row.get("issues") or []
            if any(issue not in _PARENT_COVERAGE_ONLY_ISSUES for issue in parent_issues):
                raise ValueError("unresolved_parent_state")
            if parent_issues:
                # A sibling's missing coverage does not invalidate this known
                # LINE's owner chain or actual INSERT transform. The parent's
                # whole bounds remain incomplete and geometrically unknown.
                coverage_warnings.append(dict(object_id=parent_row["object_id"],
                                              source_handle=handle, issues=list(parent_issues)))
            definition = doc.blocks.get(parent.dxf.name)
            if definition is None or definition.block.is_xref or definition.block.is_xref_overlay:
                raise ValueError("missing_or_external_parent_definition")
            pm = parent.matrix44()
            if not all(_finite(n) for n in pm):
                raise ValueError("nonfinite_native_parent_matrix")
            transform = pm @ transform
            if not all(_finite(n) for n in transform):
                raise ValueError("nonfinite_composed_parent_matrix")
            parent_oid = parent_row["object_id"]
            parents.append(dict(handle=handle, block_name=parent.dxf.name, insert=list(parent.dxf.insert),
                                xscale=parent.dxf.xscale, yscale=parent.dxf.yscale, zscale=parent.dxf.zscale,
                                rotation=parent.dxf.rotation, extrusion=list(parent.dxf.extrusion), native_matrix=list(pm),
                                coverage_only_issues=list(parent_issues)))
        if source not in definition or row.get("parent_object_id") != parent_oid:
            raise ValueError("source_LINE_owner_chain_mismatch")
        coeff_error = max(abs(a-b) for a, b in zip(list(transform), list(matrix)))
        chain = [list(transform.transform(p)) for p in raw]
        if not all(_vector(p, (3,)) for p in chain):
            raise ValueError("nonfinite_native_LINE_endpoints")
        index_error = max(abs(a-b) for p, q in zip(from_index, indexed) for a, b in zip(p, q))
        chain_error = max(abs(a-b) for p, q in zip(chain, indexed) for a, b in zip(p, q))
        if max(coeff_error, index_error, chain_error) >= 1e-6:
            raise ValueError("source_to_index_transform_mismatch")
        r, s = ctx["raw"], ctx["scale"]
        t, v, c = (r[k] for k in ("view_target_point", "view_center_point", "center"))
        paper = [[s*(p[0]-t[0]-v[0])+c[0], s*(p[1]-t[1]-v[1])+c[1]] for p in chain]
        if not all(_vector(p, (2,)) for p in paper):
            raise ValueError("nonfinite_projected_LINE_endpoints")
        pb = ctx["paper_bounds"]
        inside = [pb[0] <= p[0] <= pb[2] and pb[1] <= p[1] <= pb[3] for p in paper]
        relation = geometric_relation(row, ctx["model_bounds"])
        if relation == "fully_inside" and not all(inside):
            raise ValueError("inside_projection_mismatch")
        if relation == "outside":
            bb = [min(p[0] for p in paper), min(p[1] for p in paper), max(p[0] for p in paper), max(p[1] for p in paper)]
            if not (bb[2] < pb[0] or bb[0] > pb[2] or bb[3] < pb[1] or bb[1] > pb[3]):
                raise ValueError("outside_projection_mismatch")
        return dict(supported=True, source_handle=row["source_handle"], source_path=path,
                    raw_LINE_start_end=raw, parent_INSERT_chain=parents,
                    parent_coverage_warnings=coverage_warnings,
                    matrix_conventions="QTO column-vector row-major translation 3/7/11; ezdxf row-vector row-major translation 12/13/14; transpose before coefficient comparison",
                    index_instance_transform=list(qto_matrix), index_transform_transposed_for_ezdxf=list(matrix),
                    composed_parent_transform=list(transform), transform_max_coefficient_error=coeff_error,
                    source_to_index_endpoint_max_error=index_error, via_parent_chain_endpoint_max_error=chain_error,
                    WCS_endpoints=chain, actual_WCS_Z=[p[2] for p in chain], paper_XY_closed_form=paper,
                    paper_bounds=pb, endpoints_inside=inside, relation=relation,
                    inverse_projection_plane=copy.deepcopy(ctx["inverse_projection_plane"]),
                    meaning="source endpoints through actual parent INSERTs; source Z is never replaced by target.z; coverage-only parent warnings do not certify the parent's complete bounds")
    except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        return dict(failure, reasons=[str(exc)])


def inspect_viewports(doc, index):
    """Enumerate every paper viewport against one complete, unchanged Model index.

    This API performs no filesystem operations. Use build_index on the same
    current source, or the CLI below; a digest does not authenticate an arbitrary
    document supplied by a caller. Results concern evidence, never quantities.
    """
    if (index.get("schema_version") != INDEX_SCHEMA or index.get("kind") != "index"
            or index.get("layout") != "Model" or "source_identity_issues" not in index
            or index.get("index_sha256") != index_digest(index)):
        raise ValueError("需要当前同源且内容未变的Model工作台索引")
    if (index.get("index_scope", {}).get("bounds") is not None
            or index.get("index_scope", {}).get("kind") != "layout"):
        raise ValueError("全部视口清查需要完整Model索引，不能以局部索引证明空窗")
    rows = sorted(index["objects"], key=lambda r: r["object_id"])
    byid = {row["object_id"]: row for row in rows}
    if len(byid) != len(rows):
        raise ValueError("索引实例身份重复")
    layouts, unique_unknown = [], set()
    # Unknown is a source-instance set, not a sum of repeated viewport counts.
    for row in rows:
        if geometric_relation(row, [0., 0., 0., 0.]) == "unknown":
            unique_unknown.add(row["object_id"])
    for layout in sorted((lay for lay in doc.layouts if lay.name != "Model"), key=lambda lay: lay.name):
        views = []
        for vp in sorted(layout.query("VIEWPORT"), key=lambda e: (e.dxf.get("id", 0), str(e.dxf.get("handle", "")))):
            ctx = viewport_context(vp)
            record = dict(handle=ctx["raw"].get("handle"), id=ctx["raw"].get("id"), context=ctx,
                          status="UNSUPPORTED_RETAINED", relation_counts=None, unknown_object_ids=None, selections=[],
                          missing_line_relations=None)
            if ctx["supported"]:
                relations = {row["object_id"]: geometric_relation(row, ctx["model_bounds"]) for row in rows}
                unknown = [oid for oid, relation in relations.items() if relation == "unknown"]
                unique_unknown.update(unknown)
                record.update(relation_counts=dict(Counter(relations.values())), unknown_object_ids=unknown,
                              missing_line_relations=["fully_inside", "outside"],
                              analytic_check=analytic_check(ctx))
                for relation, purpose in (("fully_inside", "first_standard_LINE_inside"),
                                          ("outside", "first_standard_LINE_outside"),
                                          ("anchor_inside", "first_text_anchor_inside"),
                                          ("anchor_outside", "first_text_anchor_outside"),
                                          ("unknown", "first_unknown_retained")):
                    candidates = [row for row in rows if relations[row["object_id"]] == relation
                                  and (row.get("entity_type") == "LINE" and _finite(row.get("raw_length")) and row["raw_length"] > 1e-9
                                       if relation in ("fully_inside", "outside") else relation == "unknown" or bool(row.get("text")))
                                  and visibility(row, byid, doc, ctx)["eligible_by_recorded_states"]]
                    if candidates:
                        selected = evaluate(candidates[0], byid, doc, ctx)
                        if relation in ("fully_inside", "outside"):
                            selected["LINE_source_proof"] = line_source_proof(candidates[0], byid, doc, ctx)
                            if selected["LINE_source_proof"]["supported"]:
                                record["missing_line_relations"].remove(relation)
                        record["selections"].append(dict(purpose=purpose, evaluation=selected))
                record["line_pair_proven"] = not record["missing_line_relations"] and record["analytic_check"]["supported"]
                record["status"] = ("empty" if not rows else "SUPPORTED_LINE_PAIR_RETAINED" if not record["missing_line_relations"]
                                    else "NO_COMPLETE_LINE_PAIR_RETAINED")
                if not record["analytic_check"]["supported"]:
                    record["status"] = "ANALYTIC_CHECK_FAILED_RETAINED"
                record["known_geometry_intersects"] = any(relations[oid] in ("fully_inside", "boundary_overlap_candidate")
                                                          for oid in relations)
                record["empty_viewport_proven"] = not rows
            views.append(record)
        layouts.append(dict(layout=layout.name, viewports=views))
    return dict(schema_version=SCHEMA, kind="viewport_evidence", source=copy.deepcopy(index["source"]),
                authoritative_source=copy.deepcopy(index["authoritative_source"]), index_sha256=index["index_sha256"],
                raw_unit=index["raw_unit"], raw_insunits=doc.header.get("$INSUNITS", 0),
                unit_conversion=None, layouts=layouts,
                unique_unknown_object_ids=sorted(unique_unknown), unique_unknown_count=len(unique_unknown),
                source_identity_issues=copy.deepcopy(index["source_identity_issues"]),
                conversion_warnings=copy.deepcopy(index.get("conversion_warnings", [])),
                domain=DOMAIN, quantity_calculation=False, formal_adoption=False,
                meaning="per-viewport containment is evidence only; unknown source instances are not summed across views")


def main(argv=None):
    parser = argparse.ArgumentParser(description="有来源绑定的标准DXF矩形顶视视口取证；不计算工程量")
    parser.add_argument("--source", required=True, type=Path, help="工作台格式的来源JSON，含path和sha256")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        source_bytes = args.source.read_bytes()
        description = json.loads(source_bytes.decode("utf-8-sig"))
        source = copy.deepcopy(description)
        authoritative = source.pop("authoritative_source", None)
        index = build_index(source, layout="Model", authoritative_source=authoritative)
        doc, _ = read_dxf_with_diagnostics(source["path"])
        output = inspect_viewports(doc, index)
        protected = [args.source, Path(source["path"]), Path(index["authoritative_source"]["path"]),
                     Path(source["path"] + ".export.json")]
        destination = args.output.resolve()
        if any(destination == p.resolve() or (args.output.exists() and p.exists() and args.output.samefile(p)) for p in protected):
            raise ValueError("输出不能覆盖输入、原图或转换记录")
        verify_source_file(index["source"], "DXF来源")
        verify_source_file(index["authoritative_source"], "权威来源")
        if args.source.read_bytes() != source_bytes:
            raise ValueError("来源描述在读取期间改变")
        payload = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(json.dumps(dict(status="PASS", output=str(args.output), quantity_calculation=False), ensure_ascii=False))
        return 0
    except (ArithmeticError, OSError, KeyError, TypeError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
