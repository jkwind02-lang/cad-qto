"""Lossless neutral line/conic geometry and affine measurements.

Coordinates in ``parts`` are WCS (block-local WCS for definitions). An elliptic
arc is c + u*cos(t) + v*sin(t); u/v need not stay perpendicular after an affine
transform. No professional classification or topology inference is performed.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import re
import threading
from typing import Any

GEOMETRY_SCHEMA = "qto-cad-geometry/1.1"
IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
TAU = math.tau

# These describe what the neutral reader actually obtained, not whether an
# entity is in a professional takeoff scope. Text and masks are useful evidence
# even though they are not measurable pipework or complete physical objects.
MEASURABLE_CURVE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE"}
ANNOTATION_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "LEADER", "WIPEOUT"}
_READ_DIAGNOSTIC_LOCK = threading.RLock()

# Resource declarations verified in the raw DXF OBJECTS section. This is an
# intentionally bounded set: neither graphics nor layout/block identities may
# be downgraded merely because their type or computed geometry looks alike.
_REPEATABLE_RESOURCE_DECLARATIONS = {"MATERIAL", "DICTIONARY", "XRECORD"}


def inspect_raw_identity_declarations(path, handles, *, encoding=None):
    """Inspect original ASCII tags before parser binding can replace a record.

    All value bytes participate in equality, including numeric spelling,
    whitespace, ownership and references. Hashes are evidence only; acceptance
    uses equality of complete tag tuples. No source record is rewritten.
    """
    selected = {str(h).upper() for h in handles}
    found = {h: [] for h in selected}
    references, owners, identity_counts = {}, {}, {}
    section, tags, record_line = None, [], 1
    eof_seen, active_encoding = False, encoding
    digest = hashlib.sha256()

    def finish(end_line):
        nonlocal section, eof_seen, active_encoding
        if not tags:
            return
        kind = tags[0][1].strip().decode("ascii", errors="replace").upper()
        if eof_seen:
            raise ValueError("DXF的EOF之后仍有记录")
        if kind == "SECTION":
            if section is not None:
                raise ValueError("DXF段未闭合")
            section = next((v.strip().decode("ascii", errors="replace").upper() for c, v in tags if c == 2), None)
            if not section:
                raise ValueError("DXF段名缺失")
            if section == "HEADER" and active_encoding is None:
                values = {v.decode("ascii"): tags[i+1][1].strip().decode("ascii")
                          for i, (c, v) in enumerate(tags[:-1])
                          if c == 9 and v in {b"$ACADVER", b"$DWGCODEPAGE"}}
                version = values.get("$ACADVER", "")
                if re.fullmatch(r"AC\d{4}", version):
                    from ezdxf.tools.codepage import toencoding
                    active_encoding = "utf-8" if int(version[2:]) >= 1021 else toencoding(values.get("$DWGCODEPAGE", "ANSI_1252"))
            for _, value in tags:
                value.decode(active_encoding or "ascii", errors="strict")
            return
        if kind == "ENDSEC":
            if section is None:
                raise ValueError("DXF段结束没有对应起点")
            section = None
            return
        if kind == "EOF":
            if section is not None:
                raise ValueError("DXF结束时仍有未闭合段")
            eof_seen = True
            return
        for _, value in tags:
            value.decode(active_encoding or "ascii", errors="strict")
        if section not in {"TABLES", "BLOCKS", "ENTITIES", "OBJECTS"}:
            return
        header = []
        for code, value in tags:
            if code == 100:
                break
            header.append((code, value))
        id_code = 105 if kind == "DIMSTYLE" else 5
        ids = [v.strip().decode("ascii", errors="replace").upper() for c, v in header if c == id_code]
        if len(ids) != 1 or re.fullmatch(r"[0-9A-F]+", ids[0]) is None:
            return
        handle = ids[0]
        identity_counts[handle] = identity_counts.get(handle, 0) + 1
        owner_index, brace_depth = None, 0
        for index, (code, value) in enumerate(header):
            if code == 102:
                brace_depth += 1 if value.lstrip().startswith(b"{") else -1 if value.strip() == b"}" else 0
            elif code == 330 and brace_depth == 0:
                owner_index = index
                break
        # Ownership is checked for existence but is not a forward dependency:
        # traversing a parent dictionary would incorrectly pull in unrelated
        # siblings, draw-order references and the entire drawing.
        refs, backward_refs, application_groups = set(), set(), []
        for index, (code, value) in enumerate(tags):
            if code == 102:
                if value.lstrip().startswith(b"{"):
                    application_groups.append(value.strip().upper())
                elif value.strip() == b"}" and application_groups:
                    application_groups.pop()
            if not (320 <= code <= 369 or code in {390, 480, 481, 1005}):
                continue
            target = value.strip().decode("ascii", errors="replace").upper()
            # ACAD_REACTORS records who observes this object, not a resource
            # whose full dependency graph determines its identity.
            if index == owner_index or b"{ACAD_REACTORS" in application_groups:
                backward_refs.add(target)
            else:
                refs.add(target)
        refs.discard("0")
        backward_refs.discard("0")
        references.setdefault(handle, set()).update(refs)
        owners.setdefault(handle, set()).update(backward_refs)
        if handle in found:
            payload = b"".join(str(c).encode("ascii")+b"\0"+v+b"\n" for c, v in tags)
            found[handle].append({"type": kind, "section": section,
                "line_start": record_line, "line_end": end_line,
                "raw_tags_sha256": hashlib.sha256(payload).hexdigest(),
                "raw_tags": [[c, v.hex()] for c, v in tags],
                "references": sorted(refs), "_exact_tags": tuple(tags)})

    try:
        with open(path, "rb") as stream:
            if stream.read(22).startswith(b"AutoCAD Binary DXF"):
                return {"status": "unverified", "reason": "binary_dxf_raw_identity_scan_not_supported", "records": found}
            stream.seek(0)
            line_number = 0
            while True:
                code_line = stream.readline()
                if not code_line:
                    break
                value_line = stream.readline()
                if not value_line:
                    raise ValueError("DXF组码缺少对应值")
                digest.update(code_line); digest.update(value_line)
                code = int(code_line.strip())
                value = value_line.rstrip(b"\r\n")
                line_number += 2
                if code == 0:
                    finish(line_number-2)
                    tags = []
                    record_line = line_number-1
                tags.append((code, value))
            finish(line_number)
            if not eof_seen:
                raise ValueError("DXF未包含完整EOF终止记录")
    except (OSError, ValueError, LookupError) as exc:
        return {"status": "unverified", "reason": f"raw_identity_scan_failed: {type(exc).__name__}: {exc}", "records": {}}

    candidates = {h for h, rows in found.items() if len(rows) > 1
        and all(r["section"] == "OBJECTS" and r["type"] in _REPEATABLE_RESOURCE_DECLARATIONS for r in rows)
        and all(r["_exact_tags"] == rows[0]["_exact_tags"] for r in rows[1:])}
    # Raw duplicates not reported by the parser must not disappear when they
    # are dependencies of a declaration considered for advisory treatment.
    duplicate_handles = {h for h, count in identity_counts.items() if count > 1}
    blocked = (selected | duplicate_handles)-candidates
    while True:
        dependencies = {}
        for handle in candidates:
            todo, visited, problems = list(references.get(handle, ())), set(), set()
            problems.update(owner for owner in owners.get(handle, ()) if owner in blocked or owner not in references)
            while todo:
                target = todo.pop()
                if target in visited:
                    continue
                visited.add(target)
                if target in blocked or target not in references:
                    problems.add(target)
                else:
                    todo.extend(references[target]-visited)
            dependencies[handle] = sorted(problems)
        next_blocked = (selected | duplicate_handles)-{h for h in candidates if not dependencies[h]}
        if next_blocked == blocked:
            break
        blocked = next_blocked
    for handle, rows in found.items():
        for record in rows:
            record.pop("_exact_tags", None)
    return {"status": "checked", "source_sha256": digest.hexdigest(), "records": found,
            "checked_text_encoding": active_encoding or "ascii",
            "verified_resource_repeats": sorted(h for h in candidates if not dependencies[h]),
            "dependency_identity_issues": dependencies,
            "raw_duplicate_handles": sorted(duplicate_handles),
            "value_encoding": "raw DXF value bytes represented as hexadecimal; no numeric or whitespace normalization"}


def read_dxf_with_diagnostics(path, **kwargs):
    """Read without rewriting and retain loader warnings before identity is lost.

    In particular, ezdxf can continue after duplicate handles. A successfully
    returned Drawing does not prove every colliding source entity survived.
    A handler scoped to this read/thread records that condition; it never
    edits handles, audits/repairs the document or suppresses existing handlers.
    """
    import ezdxf
    messages = []
    thread_id = threading.get_ident()

    class Capture(logging.Handler):
        def emit(self, record):
            if record.thread == thread_id and record.levelno >= logging.WARNING:
                messages.append({"level": record.levelname, "logger": record.name,
                                 "message": record.getMessage()})

    logger = logging.getLogger("ezdxf")
    handler = Capture(level=logging.WARNING)
    # An application may silence ezdxf globally. Temporarily capture warnings
    # for this read and restore its exact state, including on parse failure.
    with _READ_DIAGNOSTIC_LOCK:
        level, disabled, global_disabled_level = logger.level, logger.disabled, logger.manager.disable
        logger.addHandler(handler)
        if logger.getEffectiveLevel() > logging.WARNING:
            logger.setLevel(logging.WARNING)
        logger.disabled = False
        logging.disable(logging.NOTSET)
        try:
            document = ezdxf.readfile(path, **kwargs)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.disabled = disabled
            logging.disable(global_disabled_level)
    handles = []
    for warning in messages:
        match = re.search(r"non-unique entity handle\s*#([0-9A-F]+)", warning["message"], re.IGNORECASE)
        if match:
            handles.append(match.group(1).upper())
    inspection = (inspect_raw_identity_declarations(path, handles, encoding=document.output_encoding) if handles else
                  {"status": "not_required", "records": {}, "verified_resource_repeats": []})
    verified = set(inspection.get("verified_resource_repeats", []))
    identity_issues, identity_advisories = [], []
    for warning in messages:
        match = re.search(r"non-unique entity handle\s*#([0-9A-F]+)", warning["message"], re.IGNORECASE)
        if match:
            handle = match.group(1).upper()
            is_verified = handle in verified
            issue = {"kind": "identical_non_graphical_declaration_repeat" if is_verified else "duplicate_entity_handle",
                     "entity_handle": handle, "scope": "source_file", "message": warning["message"],
                     "recommended_action": "retain_raw_declaration_evidence" if is_verified else "native_verify_source_entity_identity",
                     "raw_declaration_check": inspection["status"],
                     "raw_records": inspection.get("records", {}).get(handle, []),
                     "dependency_identity_issues": inspection.get("dependency_identity_issues", {}).get(handle, []),
                     "parser_entity_preservation_proven": False}
            (identity_advisories if is_verified else identity_issues).append(issue)
            warning["identity_classification"] = issue["kind"]
            warning["blocks_identity_binding"] = not is_verified
    blocking_warnings = [w for w in messages if w.get("blocks_identity_binding") is not False]
    diagnostics = {"schema_version": "qto-cad-source-read-diagnostics/1.1",
                   "source_path": str(path), "reader_version": ezdxf.__version__,
                   "status": "PARTIAL" if blocking_warnings else "PASS_WITH_WARNINGS" if messages else "PASS",
                   "source_identity_issues": identity_issues,
                   "source_identity_advisories": identity_advisories,
                   "source_read_warnings": messages,
                   "source_identity_status": "conflict" if identity_issues else "verified_non_graphical_repeats" if identity_advisories else "no_conflict_reported",
                   "raw_identity_inspection": {k: v for k, v in inspection.items() if k != "records"},
                   "parser_entity_preservation_proven": False,
                   "source_rewrite_performed": False}
    return document, diagnostics


def underlay_reference_facts(entity):
    """Read native reference placement, never the external page's content.

    UNDERLAY insertion and rotation are OCS. Column vectors keep signed,
    nonuniform scale and the full extrusion basis without large-coordinate
    subtraction. The matrix maps unscaled reference coordinates to WCS.
    """
    kind = entity.dxftype()
    if kind not in {"PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY"}:
        raise ValueError("不是受支持的底图引用类型")
    dxf, ocs = entity.dxf, entity.ocs()
    insert = xyz(dxf.insert)
    scale = [float(dxf.scale_x), float(dxf.scale_y), float(dxf.scale_z)]
    angle = math.radians(float(dxf.rotation))
    c, s = math.cos(angle), math.sin(angle)
    columns = [(ocs.ux*c + ocs.uy*s)*scale[0],
               (ocs.ux*(-s) + ocs.uy*c)*scale[1], ocs.uz*scale[2]]
    origin = xyz(ocs.to_wcs(insert))
    matrix = matrix16([float(columns[j][i]) if j < 3 else origin[i]
                       for i in range(3) for j in range(4)] + [0., 0., 0., 1.])
    definition = entity.get_underlay_def()
    flags = int(dxf.flags)
    return {"reference_type": kind, "definition_handle": str(dxf.underlay_def_handle),
            "definition_status": "available" if definition is not None else "missing",
            "definition_type": definition.dxftype() if definition is not None else None,
            "declared_path": str(definition.dxf.filename) if definition is not None else None,
            "page_or_sheet": str(definition.dxf.name) if definition is not None else None,
            "insert_ocs": insert, "insert_wcs": origin, "rotation_degrees": float(dxf.rotation),
            "scale_factors": scale, "extrusion_wcs": xyz(dxf.extrusion),
            "reference_to_definition_wcs": list(matrix), "reference_to_wcs": list(matrix),
            "flags": flags, "underlay_on": bool(flags & 2), "clipping_on": bool(flags & 1),
            "clip_inside": bool(flags & 16), "contrast": int(dxf.contrast), "fade": int(dxf.fade),
            "clipping_boundary_raw": [list(p) for p in entity.boundary_path],
            "clipping_coordinate_basis": "2D OCS drawing units without scaling; retained without inferring page extent",
            "content_status": "not_read", "content_geometry_available": False,
            "page_extent": None, "dependency": {"status": "unverified", "resolution_basis": None}}


def resolve_underlay_dependency(reference, context=None):
    """Check one declared local path with an explicit original CAD context.

    No search paths, downloads or external content parsing are performed. A
    missing exact declared path does not prove the file is absent elsewhere.
    """
    result = copy.deepcopy(reference)
    declared = result.get("declared_path")
    fact = {"status": "unverified", "resolution_basis": None, "resolved_path": None,
            "content_status": "not_read", "search_paths_evaluated": False}
    result["dependency"] = fact
    if not declared:
        fact["reason"] = "underlay_definition_or_path_missing"
        return result
    # Do not probe network paths or treat a Windows drive-relative path as an
    # absolute path, or silently apply this process's current directory.
    win = PureWindowsPath(declared)
    if re.match(r"^[a-z][a-z0-9+.-]*://", declared, re.I) or declared.startswith(("\\\\", "//")):
        fact["reason"] = "remote_reference_not_probed"
        return result
    native = Path(declared)
    if win.is_absolute() and os.name != "nt":
        fact["reason"] = "foreign_absolute_path_not_probed"
        return result
    if win.drive and not win.is_absolute() or (win.root and not win.drive and os.name == "nt"):
        fact["reason"] = "drive_relative_or_root_relative_path_unverified"
        return result
    if native.is_absolute():
        candidate = native
        fact["resolution_basis"] = "declared_absolute_path"
    elif context and context.get("original_cad_directory") and context.get("basis"):
        directory = Path(context["original_cad_directory"])
        if not directory.is_absolute():
            fact["reason"] = "original_cad_directory_must_be_absolute"
            return result
        candidate = directory / Path(declared.replace("\\", os.sep))
        fact["resolution_basis"] = context["basis"]
        fact["original_cad_context"] = copy.deepcopy(context)
    else:
        fact["reason"] = "original_cad_directory_not_supplied"
        return result
    try:
        candidate = candidate.resolve()
        fact["resolved_path"] = str(candidate)
        fact["status"] = "exists_at_declared_path" if candidate.is_file() else "missing_at_declared_path"
    except OSError as exc:
        fact["reason"] = f"path_probe_failed: {type(exc).__name__}: {exc}"
    return result


def block_reference_facts(entity):
    """Read reference availability without loading an XREF or evaluating CAD code.

    The existence of cached XREF children does not establish the identity or
    currency of the referenced drawing. Dynamic-block markers only establish
    that native evaluation is needed; anonymous names alone are not proof.
    """
    name = str(entity.dxf.name)
    doc = getattr(entity, "doc", None)
    block = doc.blocks.get(name) if doc is not None else None
    result = {"block_name": name, "definition_state": "missing" if block is None else "available",
              "direct_entity_count": len(block) if block is not None else None,
              "xref": False, "xref_path": None, "dynamic_evidence": [],
              "anonymous_dynamic_candidate": name.upper().startswith("*U"),
              "native_state_verified": False}
    if block is None:
        return result
    result["xref"] = bool(block.block.is_xref or block.block.is_xref_overlay)
    if result["xref"]:
        result["xref_path"] = block.block.dxf.get("xref_path", "")
        result["definition_state"] = "cached_xref_children" if len(block) else "xref_content_unavailable"
    for owner_name, owner in (("insert", entity), ("block_record", block.block_record)):
        if owner.has_extension_dict:
            keys = list(owner.get_extension_dict().keys())
            result["dynamic_evidence"].extend(f"{owner_name}:extension:{key}" for key in keys
                if "ENHANCEDBLOCK" in key.upper() or "BLOCKREP" in key.upper())
        xdata = getattr(owner, "xdata", None)
        if xdata is not None:
            result["dynamic_evidence"].extend(f"{owner_name}:xdata:{key}" for key in xdata.data
                if "BLOCKREP" in key.upper())
    return result


def dimension_measurement_facts(entity):
    """Type-aware DIMENSION evidence; this never creates measurable linework.

    DXF definition points 10/13/14/15 are WCS. For aligned dimensions (type 1)
    the distance is not the projection onto the optional angle (group 50).
    Rotated dimensions (type 0) use that direction in the OCS plane. Other
    dimension families are retained separately, never passed through this
    linear algorithm. Stored values and text are evidence, not an override of
    contradictory defining geometry.
    """
    from ezdxf.math import Vec3
    dxf = entity.dxf
    dimtype = int(dxf.dimtype) & 15
    codes, notes = [], []
    fields = ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")
    points = {key: xyz(dxf.get(key)) for key in fields if dxf.hasattr(key)}
    text = str(dxf.get("text", ""))
    text_mode = ("automatic" if text in ("", "<>") else "suppressed" if text == " "
                 else "template" if "<>" in text else "literal_override")
    result = {"dimtype_raw": int(dxf.dimtype), "dimtype": dimtype,
              "measurement_frame": "source_entity_definition_wcs",
              "definition_points_wcs": {k: v for k, v in points.items() if k != "defpoint5"},
              "defpoint5_ocs": points.get("defpoint5"), "angle_degrees": float(dxf.get("angle", 0.)),
              "angle_present": dxf.hasattr("angle"), "extrusion": xyz(dxf.get("extrusion", (0, 0, 1))),
              "text_override": text, "text_mode": text_mode,
              "api_measurement": None, "api_error": None,
              "stored_actual_measurement": None, "geometric_measurement_candidate": None,
              "raw_measurement": None, "scaled_measurement_candidate": None,
              "linear_factor": None, "rendered_text_candidates": [],
              "measurement_kind": "linear_length" if dimtype in (0, 1) else "other_dimension",
              "method": None, "status": "evidence_only", "issue_codes": codes, "notes": notes,
              "linear_measurement_usable": False, "design_value_adopted": False}
    try:
        api = entity.get_measurement()
        result["api_measurement"] = xyz(api) if isinstance(api, Vec3) else float(api)
        if isinstance(result["api_measurement"], float) and not math.isfinite(result["api_measurement"]):
            result["api_measurement"] = None
            result["api_error"] = "non-finite measurement"
    except Exception as exc:
        result["api_error"] = f"{type(exc).__name__}: {exc}"
    if dxf.hasattr("actual_measurement"):
        stored = float(dxf.actual_measurement)
        if math.isfinite(stored):
            result["stored_actual_measurement"] = stored
        else:
            codes.append("dimension_stored_measurement_nonfinite")
    doc = getattr(entity, "doc", None)
    picture = doc.blocks.get(dxf.get("geometry", "")) if doc is not None else None
    if picture is not None:
        for child in picture:
            if child.dxftype() in {"TEXT", "MTEXT"}:
                result["rendered_text_candidates"].append({"entity_handle": child.dxf.handle,
                    "text": child.plain_text() if child.dxftype() == "MTEXT" else str(child.dxf.text)})
    # Angles, radii/diameters and ordinate vectors intentionally keep their
    # own dimensional meaning. Their cached/API values are not linear scales.
    if dimtype not in (0, 1):
        result["measurement_kind"] = {2: "angle_degrees", 3: "diameter", 4: "radius",
                                      5: "angle_degrees", 6: "ordinate_vector"}.get(dimtype, "unknown")
        return result
    if not all(key in points for key in ("defpoint2", "defpoint3")):
        codes.append("dimension_definition_points_missing")
        result["status"] = "review_required"
        return result
    a, b = Vec3(points["defpoint2"]), Vec3(points["defpoint3"])
    delta = b - a
    tolerance = max(1e-9, delta.magnitude*1e-9,
                    16*math.ulp(max(abs(v) for v in (*a, *b))))
    result["comparison_tolerance_raw"] = tolerance
    if abs(delta.dot(entity.ocs().uz)) > tolerance:
        codes.append("dimension_points_outside_ocs_plane")
    if dimtype == 1:
        value = delta.magnitude
        result["method"] = "aligned_wcs_endpoint_distance"
    else:
        direction = entity.ocs().to_wcs(Vec3.from_angle(math.radians(result["angle_degrees"])))
        value = abs(delta.dot(direction))
        result["method"] = "rotated_wcs_projection_on_ocs_direction"
    result["geometric_measurement_candidate"] = value
    if value <= tolerance:
        codes.append("dimension_zero_or_degenerate_measurement")
    api = result["api_measurement"]
    if isinstance(api, (int, float)) and abs(api-value) > tolerance:
        # A documented type/coordinate calculation can correct a library API
        # error. Keep the discrepancy visible instead of replacing history.
        notes.append("library_measurement_disagrees_with_type_aware_geometry")
    stored = result["stored_actual_measurement"]
    try:
        style = entity.override()
        factor = float(style.get("dimlfac", 1.))
        result["linear_factor"] = factor if math.isfinite(factor) else None
        if not math.isfinite(factor) or factor <= 0:
            codes.append("dimension_scale_requires_context")
        else:
            result["scaled_measurement_candidate"] = value*factor
            # DXF's stored actual measurement includes DIMLFAC for a linear
            # dimension; the library API returns unscaled geometry. Comparing
            # both directly wrongly rejects correctly scaled detail drawings.
            result["stored_measurement_comparison_basis"] = "geometry_times_positive_dimlfac"
            scaled_tolerance = max(tolerance*factor, 16*math.ulp(abs(stored))) if stored is not None else None
            if stored is not None and abs(stored-result["scaled_measurement_candidate"]) > scaled_tolerance:
                codes.append("dimension_stored_geometry_conflict")
    except Exception as exc:
        codes.append("dimension_style_unavailable")
        notes.append(f"{type(exc).__name__}: {exc}")
    if text_mode == "literal_override":
        codes.append("dimension_literal_text_requires_review")
    if text_mode == "suppressed":
        notes.append("dimension_text_suppressed")
    result["status"] = "review_required" if codes else "resolved_geometry"
    if not codes:
        result["raw_measurement"] = value
        # Template prefixes/suffixes can encode units or additional meaning.
        # The caller must still establish view, units and drawing authority.
        result["linear_measurement_usable"] = text_mode == "automatic"
    return result


def xyz(value):
    values = list(value)
    result = [float(values[0]), float(values[1]), float(values[2]) if len(values) > 2 else 0.]
    if not all(math.isfinite(n) for n in result):
        raise ValueError("几何坐标必须是有限数值")
    return result


def matrix16(matrix):
    if len(matrix) == 8:  # legacy planar affine matrix
        a, b, c, d, tx, ty, sz, tz = matrix
        matrix = (a, c, 0., tx, b, d, 0., ty, 0., 0., sz, tz, 0., 0., 0., 1.)
    if len(matrix) != 16 or not all(math.isfinite(float(n)) for n in matrix):
        raise ValueError("块变换必须是有限4×4仿射矩阵")
    result = tuple(float(n) for n in matrix)
    if result[12:] != (0., 0., 0., 1.):
        raise ValueError("不支持投影变换")
    return result


def transform_point(value, matrix):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y, z = xyz(value)
    m = matrix16(matrix)
    return [m[i]*x + m[i+1]*y + m[i+2]*z + m[i+3] for i in (0, 4, 8)]


def transform_vector(value, matrix):
    x, y, z = xyz(value)
    m = matrix16(matrix)
    return [m[i]*x + m[i+1]*y + m[i+2]*z for i in (0, 4, 8)]


def compose_matrix(parent, child):
    a, b = matrix16(parent), matrix16(child)
    return tuple(sum(a[r*4+k]*b[k*4+c] for k in range(4)) for r in range(4) for c in range(4))


def matrix_for_insert(row, origin):
    if row.get("insert_matrix_wcs") is not None:
        return matrix16(row["insert_matrix_wcs"])
    if row.get("extrusion") not in (None, [0, 0, 1], [0., 0., 1.]):
        raise ValueError("旧块缓存缺少三维变换，请重新提取")
    scales = row.get("scale_factors") or [1., 1., 1.]
    sx, sy, sz = (float(scales[i]) if i < len(scales) else 1. for i in range(3))
    rotation = float(row.get("rotation") or 0.)
    cs, sn = math.cos(rotation), math.sin(rotation)
    a, b, c, d = cs*sx, sn*sx, -sn*sy, cs*sy
    x, y, z = xyz([row.get("x", 0.), row.get("y", 0.), row.get("z", 0.)])
    ox, oy, oz = xyz(origin)
    return matrix16((a, b, c, d, x-a*ox-c*oy, y-b*ox-d*oy, sz, z-sz*oz))


def conic_point(part, t):
    cs, sn = math.cos(t), math.sin(t)
    return [c+u*cs+v*sn for c, u, v in zip(part["center"], part["u"], part["v"])]


def _integral(f, a, b, tolerance, depth=22):
    """Adaptive Simpson, failing explicitly when the requested error is unmet."""
    if a == b:
        return 0.
    if a > b:
        a, b = b, a
    def recurse(a, b, fa, fm, fb, estimate, tol, depth):
        mid = (a+b)/2
        fl, fr = f((a+mid)/2), f((mid+b)/2)
        left, right = (mid-a)*(fa+4*fl+fm)/6, (b-mid)*(fm+4*fr+fb)/6
        delta = left+right-estimate
        if abs(delta) <= 15*tol:
            return left+right+delta/15
        if depth <= 0:
            raise ValueError("曲线积分未达到误差要求；不得按弦长替代")
        return recurse(a, mid, fa, fl, fm, left, tol/2, depth-1)+recurse(mid, b, fm, fr, fb, right, tol/2, depth-1)
    # Split into short intervals to avoid periodic-function aliasing.
    n = max(1, math.ceil((b-a)/(math.pi/4)))
    total = 0.
    for i in range(n):
        lo, hi = a+(b-a)*i/n, a+(b-a)*(i+1)/n
        fa, fm, fb = f(lo), f((lo+hi)/2), f(hi)
        total += recurse(lo, hi, fa, fm, fb, (hi-lo)*(fa+4*fm+fb)/6, tolerance/n, depth)
    return total


def measure_part(part):
    if part["kind"] == "line":
        return math.dist(part["start"], part["end"]), 0.
    if part["kind"] != "ellipse_arc":
        raise ValueError("不支持的中性曲线类型")
    u, v = part["u"], part["v"]
    ru, rv = math.sqrt(sum(x*x for x in u)), math.sqrt(sum(x*x for x in v))
    sweep, start = float(part["sweep_param"]), float(part["start_param"])
    if max(ru, rv) == 0:
        return 0., 0.
    dot = sum(a*b for a, b in zip(u, v))
    if abs(ru-rv) <= 1e-12*max(ru, rv) and abs(dot) <= 1e-12*ru*rv:
        return abs(sweep)*(ru+rv)/2, 0.
    tolerance = max(1e-10, abs(sweep)*max(ru, rv)*1e-10)
    speed = lambda t: math.sqrt(sum((-a*math.sin(t)+b*math.cos(t))**2 for a, b in zip(u, v)))
    return _integral(speed, start, start+sweep, tolerance), tolerance


def part_extrema(part):
    if part["kind"] == "line":
        return [part["start"], part["end"]]
    start = float(part["start_param"])
    end = start+float(part["sweep_param"])
    lo, hi = min(start, end), max(start, end)
    params = [start, end]
    for u, v in zip(part["u"], part["v"]):
        t = math.atan2(v, u)
        for k in range(math.ceil((lo-t)/math.pi), math.floor((hi-t)/math.pi)+1):
            params.append(t+k*math.pi)
    return [conic_point(part, t) for t in params]


def summarize(geometry):
    if geometry.get("schema") != GEOMETRY_SCHEMA or geometry.get("coordinate_system") != "WCS":
        raise ValueError("几何契约或坐标系不支持，请重新提取")
    parts = geometry["parts"]
    if not parts:
        return {"anchor": None, "bbox": None, "bbox3d": None, "start": None, "end": None, "length": None, "signature": None, "length_error_estimate": None}
    measures = [measure_part(p) for p in parts]
    points = [p for part in parts for p in part_extrema(part)]
    start = parts[0]["start"] if parts[0]["kind"] == "line" else conic_point(parts[0], parts[0]["start_param"])
    last = parts[-1]
    end = last["end"] if last["kind"] == "line" else conic_point(last, last["start_param"]+last["sweep_param"])
    mins, maxs = [min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]
    return {"anchor": list(start), "bbox": [mins[0], mins[1], maxs[0], maxs[1]], "bbox3d": mins+maxs,
            "start": list(start), "end": list(end), "length": math.fsum(n for n, _ in measures),
            "length_error_estimate": math.fsum(e for _, e in measures),
            "signature": {"geometry_schema": GEOMETRY_SCHEMA, "part_count": len(parts), "closed": geometry.get("closed", False),
                          "parts": [dict(p, **{k: [v-start[i] for i,v in enumerate(p[k])] for k in ("start", "end", "center") if k in p}) for p in parts]}}


def transform_geometry(geometry, matrix):
    result = copy.deepcopy(geometry)
    for p in result["parts"]:
        if p["kind"] == "line":
            p["start"], p["end"] = transform_point(p["start"], matrix), transform_point(p["end"], matrix)
        elif p["kind"] == "ellipse_arc":
            p["center"] = transform_point(p["center"], matrix)
            p["u"], p["v"] = transform_vector(p["u"], matrix), transform_vector(p["v"], matrix)
        else:
            raise ValueError("未知几何类型，不能变换")
    return result


def transform_row(row, matrix, instance_path, target_layout):
    result = copy.deepcopy(row)
    original = str(row.get("record_id") or "NO-ID")
    result.update(source_definition_record_id=original, record_id=f"CADINST-{instance_path}/{original}", instance_path=instance_path, layout=target_layout)
    result["candidate_physical_id"] = result["record_id"]
    result["instance_transform"] = list(matrix16(matrix))
    if row.get("external_reference"):
        reference = result["external_reference"]
        reference["reference_to_wcs"] = list(compose_matrix(matrix, reference["reference_to_wcs"]))
        reference["insert_wcs"] = transform_point([0., 0., 0.], reference["reference_to_wcs"])
        reference["instance_path"] = instance_path
        reference["target_layout"] = target_layout
    anchor = transform_point([row["x"], row["y"], row.get("z", 0.)], matrix) if row.get("x") is not None and row.get("y") is not None else None
    if anchor:
        result["x"], result["y"], result["z"] = anchor
    if row.get("geometry") is not None:
        geometry = transform_geometry(row["geometry"], matrix)
        summary = summarize(geometry)
        result.update(geometry=geometry, start=summary["start"], end=summary["end"], bbox=summary["bbox"], bbox3d=summary["bbox3d"], raw_length=summary["length"],
                      geometry_signature=summary["signature"], length_error_estimate=summary["length_error_estimate"], geometry_method="wcs_affine_parametric")
    else:
        result["start"], result["end"] = transform_point(row.get("start"), matrix), transform_point(row.get("end"), matrix)
        if row.get("entity_type") == "LINE" and result["start"] is not None and result["end"] is not None:
            result["raw_length"] = math.dist(result["start"], result["end"])
        elif row.get("raw_length") is not None:
            # Older caches do not contain enough geometry for reliable affine
            # length/bounds. Keep evidence, but do not substitute a chord.
            result["raw_length"] = None
            result["geometry_status"] = "requires_reextraction"
            result["geometry_issues"] = ["旧曲线缓存缺少完整几何，请重新提取后计算"]
        box = row.get("bbox3d")
        if box is not None:
            points = [transform_point([x,y,z], matrix) for x in (box[0],box[3]) for y in (box[1],box[4]) for z in (box[2],box[5])]
            result["bbox"] = [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
        else:
            # Unknown bounds must not silently discard an in-scope instance.
            result["bbox"] = None
    return result


def entity_geometry(entity: Any):
    if entity.dxftype() in ('ACAD_PROXY_ENTITY','ACAD_PROXY_OBJECT') or entity.dxftype().startswith('TCH_'):
        from cad_native import native_geometry_facts
        return native_geometry_facts(entity)
    """Extract full curves in WCS without exploding the source drawing."""
    from ezdxf.math import Vec3
    kind = entity.dxftype()
    parts, raw, issues = [], {"entity_type": kind}, []
    issue_codes, actions = [], []
    capability = "unsupported"
    closed = bool(getattr(entity, "closed", False) or getattr(entity, "is_closed", False))
    anchor, insert_matrix, external_reference = None, None, None
    # DXFNamespace.get still raises for attributes unsupported by this entity.
    extrusion = xyz(entity.dxf.get("extrusion", (0, 0, 1))) if entity.dxf.is_supported("extrusion") else None
    raw["extrusion"] = extrusion
    evidence_points = []
    def line(a, b):
        parts.append({"kind": "line", "start": xyz(a), "end": xyz(b)})
    def arc(center, u, v, start, sweep):
        parts.append({"kind": "ellipse_arc", "center": xyz(center), "u": xyz(u), "v": xyz(v), "start_param": float(start), "sweep_param": float(sweep)})
    def unresolved(code, message, action):
        issue_codes.append(code)
        issues.append(message)
        if action not in actions:
            actions.append(action)
    if kind == "LINE":
        line(entity.dxf.start, entity.dxf.end)
        raw.update(start=xyz(entity.dxf.start), end=xyz(entity.dxf.end))
    elif kind in {"LWPOLYLINE", "POLYLINE"}:
        if kind == "POLYLINE" and not (entity.is_2d_polyline or entity.is_3d_polyline):
            unresolved("polyline_mesh_not_measured", "网格/多面网不作为多段线中心线测量", "native_geometry_read_required")
        else:
            is2d = kind == "LWPOLYLINE" or entity.is_2d_polyline
            if kind == "LWPOLYLINE":
                elevation = float(entity.dxf.elevation)
                values = [([float(x),float(y),elevation],float(b)) for x,y,b in entity.get_points("xyb")]
            else:
                elevation = float(entity.dxf.get("elevation", Vec3()).z)
                values = [(xyz(v.dxf.location),float(v.dxf.get("bulge",0.))) for v in entity.vertices]
                if is2d:
                    values = [([p[0],p[1],elevation],b) for p,b in values]
            raw.update(vertices=[p for p,b in values], bulges=[b for p,b in values], closed=closed, coordinate_system="OCS" if is2d else "WCS")
            ocs = entity.ocs()
            pairs = list(zip(values, values[1:]))
            if closed and len(values)>1:
                pairs.append((values[-1],values[0]))
            for (a,bulge),(b,_) in pairs:
                if abs(bulge)<1e-12 or not is2d:
                    line(ocs.to_wcs(a) if is2d else a, ocs.to_wcs(b) if is2d else b)
                else:
                    dx,dy=b[0]-a[0],b[1]-a[1]
                    chord=math.hypot(dx,dy)
                    if chord==0:
                        raise ValueError("弧段弦长为零")
                    factor=(1-bulge*bulge)/(4*bulge)
                    c=[(a[0]+b[0])/2-dy*factor,(a[1]+b[1])/2+dx*factor,a[2]]
                    radius=chord*(1+bulge*bulge)/(4*abs(bulge))
                    arc(ocs.to_wcs(c),ocs.ux*radius,ocs.uy*radius,math.atan2(a[1]-c[1],a[0]-c[0]),4*math.atan(bulge))
    elif kind in {"ARC","CIRCLE"}:
        ocs=entity.ocs()
        radius=float(entity.dxf.radius)
        start=math.radians(float(entity.dxf.start_angle)) if kind=="ARC" else 0.
        sweep=math.radians((float(entity.dxf.end_angle)-float(entity.dxf.start_angle))%360.) if kind=="ARC" else TAU
        raw.update(center=xyz(entity.dxf.center),radius=radius,start_param=start,sweep_param=sweep,coordinate_system="OCS")
        anchor=xyz(ocs.to_wcs(entity.dxf.center))
        arc(anchor,ocs.ux*radius,ocs.uy*radius,start,sweep)
        closed=kind=="CIRCLE"
    elif kind=="ELLIPSE":
        start,end=float(entity.dxf.start_param),float(entity.dxf.end_param)
        delta=end-start
        sweep=TAU if abs(delta)>=TAU-1e-12 else delta%TAU
        raw.update(center=xyz(entity.dxf.center),major_axis=xyz(entity.dxf.major_axis),ratio=float(entity.dxf.ratio),start_param=start,end_param=end,coordinate_system="WCS")
        anchor=xyz(entity.dxf.center)
        arc(anchor,entity.dxf.major_axis,entity.minor_axis,start,sweep)
        closed=abs(sweep-TAU)<1e-12
    elif kind=="INSERT":
        capability = "block_reference"
        anchor=xyz(entity.ocs().to_wcs(entity.dxf.insert))
        m=entity.matrix44()
        zero=m.transform((0.,0.,0.))
        # Subtracting transformed unit points at a large survey coordinate
        # destroys significant digits in the linear terms. Transform vectors
        # directly so translation never contaminates rotation/scale/shear.
        columns=[m.transform_direction(p) for p in ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))]
        insert_matrix=[float(columns[j][i]) if j<3 else float(zero[i]) for i in range(3) for j in range(4)]+[0.,0.,0.,1.]
        raw.update(insert=xyz(entity.dxf.insert),row_count=int(entity.dxf.row_count),column_count=int(entity.dxf.column_count))
        if entity.dxf.row_count>1 or entity.dxf.column_count>1:
            unresolved("array_instances_not_expanded", "阵列块需逐实例展开；当前记录不能视为一个完整实物实例", "expand_and_verify_array_instances")
        reference = block_reference_facts(entity)
        raw["block_reference"] = reference
        if reference["definition_state"] == "missing":
            unresolved("block_definition_missing", "块定义缺失；仅取得插入点和变换，不能据此认定块内对象为空", "native_resolve_block_definition")
        if reference["xref"]:
            code = "xref_content_unavailable" if reference["definition_state"] == "xref_content_unavailable" else "xref_identity_not_verified"
            unresolved(code, "外部参照内容未载入" if code == "xref_content_unavailable" else "已读取外部参照缓存内容，但参照来源、版本和加载状态尚未核验", "native_load_and_verify_xref")
        if reference["dynamic_evidence"]:
            unresolved("dynamic_state_not_evaluated", "发现动态块数据；当前读取器未核验原生可见状态和参数求值结果", "native_evaluate_dynamic_block_state")
        elif reference["anonymous_dynamic_candidate"]:
            unresolved("anonymous_block_state_unverified", "匿名块名称符合动态块候选形式；名称不能确定语义，需核对是否含动态可见状态", "native_verify_anonymous_block_state")
    elif kind in {"PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY"}:
        capability = "external_reference_evidence"
        external_reference = underlay_reference_facts(entity)
        anchor = external_reference["insert_wcs"]
        actions.append("resolve_underlay_dependency_and_read_content")
        if external_reference["definition_status"] == "missing":
            unresolved("underlay_definition_missing", "底图定义缺失；仅取得引用位置和变换，不能取得文件或页码", "native_resolve_underlay_definition")
    elif kind in {"TEXT","ATTRIB","ATTDEF"}:
        anchor=xyz(entity.ocs().to_wcs(entity.dxf.insert))
        raw["insert"]=xyz(entity.dxf.insert)
    elif kind=="MTEXT":
        anchor=xyz(entity.dxf.insert)  # MTEXT uses WCS, unlike TEXT.
    elif kind=="DIMENSION":
        anchor=xyz(entity.dxf.defpoint)
        facts = dimension_measurement_facts(entity)
        raw["dimension_measurement"] = facts
        if facts["issue_codes"]:
            issue_codes.extend(facts["issue_codes"])
            issues.append("尺寸计量待复核：" + "; ".join(facts["issue_codes"]))
            actions.append("review_dimension_geometry_text_and_native_measurement")
    elif kind=="LEADER":
        evidence_points = [xyz(p) for p in entity.vertices]
        raw.update(vertices=evidence_points, coordinate_system="WCS",
                   annotation_handle=entity.dxf.get("annotation_handle"),
                   path_type=entity.dxf.get("path_type", 0),
                   normal_vector=xyz(entity.dxf.get("normal_vector", (0, 0, 1))))
        anchor = evidence_points[0] if evidence_points else None
    elif kind=="WIPEOUT":
        evidence_points = [xyz(p) for p in entity.boundary_path_wcs()]
        raw.update(boundary_vertices=evidence_points, coordinate_system="WCS")
        anchor = evidence_points[0] if evidence_points else xyz(entity.dxf.insert)
    elif kind=="SPLINE":
        raw.update(control_points=[xyz(p) for p in entity.control_points],fit_points=[xyz(p) for p in entity.fit_points],knots=list(entity.knots),weights=list(entity.weights),degree=int(entity.dxf.degree))
        unresolved("spline_not_measured", "样条保留原始控制数据；本测量器不以控制折线或弦长冒充曲线", "native_or_exact_spline_measurement_required")
    elif kind=="HATCH":
        # HATCH is intentionally measured by the existing surface adapter. It
        # must not be presented as a neutral curve, or as an extraction crash.
        capability = "specialized_adapter_required"
        raw.update(boundary_path_count=len(entity.paths), hatch_style=int(entity.dxf.hatch_style))
        actions.append("qto-surfaces-envelope/scripts/extract_surface_regions.py")
    elif kind=="POINT":
        capability = "point_evidence"
        anchor = xyz(entity.dxf.location)
    else:
        for field in ("insert","location","center","start"):
            value=entity.dxf.get(field) if entity.dxf.is_supported(field) else None
            if value is not None:
                anchor=xyz(value)
                break
        unresolved("entity_type_not_supported", f"{kind}尚无中性几何解析器；保留对象身份，不将未取得的几何视为零", "native_geometry_read_required")
    geometry={"schema":GEOMETRY_SCHEMA,"coordinate_system":"WCS","parts":parts,"closed":closed} if parts else None
    summary=summarize(geometry) if geometry else {"start":None,"end":None,"bbox":None,"bbox3d":None,"length":None,"signature":None,"length_error_estimate":None}
    anchor=anchor or summary.get("anchor")
    if not geometry and anchor is not None:
        summary["bbox"]=[anchor[0],anchor[1],anchor[0],anchor[1]]
        summary["bbox3d"]=anchor+anchor
    if external_reference is not None:
        # A page insertion point does not bound the page. Keeping bounds unknown
        # also prevents an ROI filter from silently discarding its unknown extent.
        summary["bbox"] = summary["bbox3d"] = None
    if evidence_points:
        # Annotation and masking extents are evidence, never measured pipework.
        lo = [min(p[i] for p in evidence_points) for i in range(3)]
        hi = [max(p[i] for p in evidence_points) for i in range(3)]
        summary["bbox3d"] = lo + hi
        summary["bbox"] = [lo[0], lo[1], hi[0], hi[1]]
    if geometry is not None:
        capability = "measurable_curve"
    elif kind in MEASURABLE_CURVE_TYPES and not issues:
        unresolved("empty_curve_geometry", "曲线未产生可测线段；不能作为零长度实物计量", "native_geometry_read_required")
    elif kind in ANNOTATION_TYPES:
        capability = "annotation_evidence"
    status = "unsupported" if issues else ("requires_adapter" if capability in {"specialized_adapter_required", "external_reference_evidence"} else "supported")
    summary.update(anchor=anchor,geometry=geometry,raw_geometry=raw,geometry_status=status,
                   geometry_capability=capability,geometry_issue_codes=issue_codes,
                   recommended_actions=actions,geometry_issues=issues,
                   insert_matrix_wcs=insert_matrix,extrusion=extrusion,
                   external_reference=external_reference)
    return summary


def transform_bounds(box, matrix):
    """Conservative 3D affine bounds; never flatten unknown Z to zero."""
    if not isinstance(box, (list, tuple)) or len(box) != 6:
        return None
    points = [transform_point([x,y,z],matrix) for x in (box[0],box[3]) for y in (box[1],box[4]) for z in (box[2],box[5])]
    return [min(p[i] for p in points) for i in range(3)]+[max(p[i] for p in points) for i in range(3)]


def definition_bounds(name, definitions, origins, max_depth=16, cache=None, stack=()):
    """A missing/unsupported child makes bounds unknown, not artificially small."""
    if name in stack or len(stack) >= max_depth or name not in definitions:
        return None
    if not stack and cache is not None and name in cache:
        return cache[name]
    boxes=[]
    for row in definitions[name]:
        # In particular, an array INSERT's first cell is not its envelope.
        # Keep the parent unpruned so adapters can retain unresolved instances.
        if row.get("geometry_issues") or row.get("geometry_status") in {"unsupported", "requires_reextraction"}:
            return None
        if row.get("entity_type") == "INSERT":
            child=str(row.get("block_name") or "")
            box=definition_bounds(child,definitions,origins,max_depth,None,(*stack,name))
            if box is not None:
                box=transform_bounds(box,matrix_for_insert(row,origins.get(child,[0.,0.,0.])))
        elif row.get("geometry") is not None:
            box=row.get("bbox3d")
        else:
            box=None  # Point-only glyph extents are not a complete block envelope.
        if box is None:
            return None
        boxes.append(box)
    result=([min(b[i] for b in boxes) for i in range(3)]+[max(b[i+3] for b in boxes) for i in range(3)]) if boxes else None
    if not stack and cache is not None:
        cache[name]=result
    return result


def bounds_intersect(box, scope):
    if box is None or scope is None:
        return True
    return not (box[3] < scope["xmin"] or box[0] >= scope["xmax_exclusive"] or box[4] < scope["ymin"] or box[1] >= scope["ymax_exclusive"])
