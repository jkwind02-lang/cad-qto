"""Source-bound symbol census. Similarity is a candidate, never a quantity decision."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import qto_cad_geometry as geo
from qto_cad_color import resolve_manifest_color

SCHEMA = "qto-symbol-census/1.0"
REQUEST_SCHEMA = "qto-symbol-census-request/1.0"
TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
CURVE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def producer_dependencies():
    """Independent census reports bind the code that constructs their full rows."""
    folder = Path(__file__).resolve().parent
    return [{"module": "_qto-shared-evidence/scripts/" + name, "sha256": digest(folder / name)}
            for name in ("qto_symbol_geometry.py", "qto_cad_geometry.py", "qto_cad_color.py")]


def snapshot(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: nonempty text required")
    return value


def positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}: positive finite number required")
    return float(value)


def bound_file(ref, base, label):
    if not isinstance(ref, dict):
        raise ValueError(f"{label}: file reference required")
    path = Path(required(ref.get("path"), label + ".path"))
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file() or digest(path) != str(ref.get("sha256", "")).lower():
        raise ValueError(f"{label}: missing file or stale SHA256")
    return path


def load_rows(path, source_hash):
    rows, seen = [], set()
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{number}: entity object required")
            for field in ("raw_geometry", "geometry"):
                if row.get(field) is not None and not isinstance(row[field], dict):
                    raise ValueError(f"{path.name}:{number}: invalid {field}")
            oid = required(row.get("record_id"), f"{path.name}:{number}.record_id")
            if row.get("row_type") != "entity" or row.get("source_sha256") != source_hash or oid in seen:
                raise ValueError(f"{path.name}:{number}: nonentity, wrong source, or duplicate identity")
            seen.add(oid)
            rows.append(row)
    return rows


def load_source(source_id, manifest_path, expected_sha256, layouts):
    path = Path(manifest_path).resolve()
    if digest(path) != expected_sha256:
        raise ValueError(f"{source_id}: stale manifest")
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema_version") != "qto-cad-neutral-evidence/1.0":
        raise ValueError("unsupported neutral schema")
    base = path.parent
    bound_file(manifest.get("authoritative_source"), base, "authoritative_source")
    bound_file(manifest.get("exchange_dxf"), base, "exchange_dxf")
    if manifest.get("exchange_conversion"):
        bound_file(manifest["exchange_conversion"], base, "exchange_conversion")
    if manifest.get("source_identity_issues"):
        raise ValueError("source entity identity conflicts must be resolved before census")
    source_hash = manifest["authoritative_source"]["sha256"].lower()
    artifacts = manifest.get("artifacts", {})
    paths = {key: bound_file(artifacts.get(key), base, key) for key in ("layout_entities", "block_definitions")}
    loaded = {key: load_rows(value, source_hash) for key, value in paths.items()}
    all_ids = [row["record_id"] for rows in loaded.values() for row in rows]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("source record identity repeated across layout and definition streams")
    for key, rows in loaded.items():
        if artifacts[key].get("count") != len(rows):
            raise ValueError(f"{key}: record count mismatch")
    known = manifest.get("layouts", [])
    if not isinstance(layouts, list) or not layouts or len(set(layouts)) != len(layouts) or any(v not in known for v in layouts):
        raise ValueError(f"{source_id}: explicit unique known layouts required; no implicit wildcard")
    if any(row.get("layout") not in known for row in loaded["layout_entities"]):
        raise ValueError("layout row outside manifest inventory")
    definitions = defaultdict(list)
    inventory = {}
    for item in manifest.get("blocks", []):
        name = required(item.get("block_name"), "blocks.block_name")
        if name in inventory:
            raise ValueError("duplicate block inventory")
        inventory[name] = item
    for row in loaded["block_definitions"]:
        name = required(row.get("parent_block"), "definition.parent_block")
        if name not in inventory:
            raise ValueError("definition missing from inventory")
        definitions[name].append(row)
    for name, item in inventory.items():
        count = item.get("direct_entity_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name}: nonnegative definition inventory count required")
        if item.get("serialized") is False and definitions[name]:
            raise ValueError(f"{name}: inventory says unserialized but definition records exist")
        if item.get("serialized", True) and count != len(definitions[name]):
            raise ValueError(f"{name}: definition count mismatch")
    return {"source_id": source_id, "manifest": manifest, "manifest_path": path,
            "manifest_sha256": expected_sha256, "source_sha256": source_hash, "layouts": layouts,
            "layout_rows": loaded["layout_entities"], "definitions": definitions, "inventory": inventory}


def curve_parts(row):
    geometry = row.get("geometry")
    if isinstance(geometry, dict) and isinstance(geometry.get("parts"), list):
        parts = copy.deepcopy(geometry["parts"])
        for part in parts:
            geo.measure_part(part)  # validate full curves, never substitute an arc chord
        return parts
    if row.get("entity_type") == "LINE" and row.get("start") is not None and row.get("end") is not None:
        return [{"kind": "line", "start": geo.xyz(row["start"]), "end": geo.xyz(row["end"])}]
    raise ValueError("complete parametric curve unavailable")


def source_geometry_issues(row):
    """Object facts govern completeness; an aggregate PASS cannot waive them.

    Signature limits (sampling size, conservative invariance, segmentation) are
    separate. They do not by themselves make complete source geometry unread.
    """
    issues = list(row.get("geometry_issue_codes") or [])
    if row.get("geometry_status") in {"unsupported", "requires_reextraction"}:
        issues.append("source_geometry_status:" + row["geometry_status"])
    if row.get("geometry_issues") and not row.get("geometry_issue_codes"):
        issues.extend("source_geometry_issue:" + str(value) for value in row["geometry_issues"])
    if row.get("entity_type") in CURVE_TYPES:
        try:
            parts = curve_parts(row)
            lengths = [geo.measure_part(part)[0] for part in parts]
            if not lengths or any(not math.isfinite(value) or value < 0 for value in lengths) or sum(lengths) <= 0:
                raise ValueError("empty, nonfinite or entirely degenerate curve geometry")
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            issues.append("source_curve_geometry_unavailable:" + str(exc))
    return sorted(set(issues))


def shape_signature(parts):
    """Conservative sampled invariant under translation, rotation, uniform scale and mirror.

    Curve type and part length distributions supplement the pairwise-distance multiset.
    This is deliberately not a semantic or uniqueness proof (homometric sets can collide).
    """
    points, lengths, kinds = [], [], Counter()
    for part in parts:
        length = geo.measure_part(part)[0]
        if length <= 0:
            raise ValueError("degenerate shape")
        kinds[part["kind"]] += 1
        lengths.append(length)
        if part["kind"] == "line":
            points.extend((geo.xyz(part["start"]), geo.xyz(part["end"])))
        elif part["kind"] == "ellipse_arc":
            points.extend(geo.conic_point(part, part["start_param"] + part["sweep_param"] * n / 16) for n in range(17))
        else:
            raise ValueError("unsupported shape primitive")
        if len(points) > 512:
            raise ValueError("oversized signature; split scope explicitly")
    if not points or len(points) > 512:
        raise ValueError("empty or oversized signature; split scope explicitly")
    distances = [math.dist(a, b) for i, a in enumerate(points) for b in points[i + 1:]]
    scale = max(distances, default=0.)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("zero or invalid shape scale")
    payload = {"kinds": sorted(kinds.items()), "distances": sorted(round(v / scale, 6) for v in distances),
               "lengths": sorted(round(v / scale, 6) for v in lengths)}
    return snapshot(payload)


def insert_issues(row, inventory):
    name = str(row.get("block_name") or "")
    item = inventory.get(name)
    issues = list(row.get("geometry_issue_codes") or [])
    raw = row.get("raw_geometry") or {}
    reference = raw.get("block_reference") or {}
    if not item or not item.get("serialized", True):
        issues.append("missing_or_unserialized_definition")
    if raw.get("row_count", 1) != 1 or raw.get("column_count", 1) != 1:
        issues.append("array_placements_unavailable")
    if reference.get("xref") or (item or {}).get("xref"):
        issues.append("xref_source_identity_unverified")
    if reference.get("dynamic_evidence"):
        issues.append("native_dynamic_state_unverified")
    return sorted(set(issues))


def expand_source(source, max_depth=16):
    """Reuse the neutral geometry transform and the established CADINST path convention."""
    objects, rows_by_id, emitted, children = [], {}, {}, defaultdict(list)
    definitions, inventory = source["definitions"], source["inventory"]
    placed = Counter()

    def emit(row, matrix, path, owner, stack, inherited_layer, inherited_issues, references, parent_color=None):
        local = copy.deepcopy(row) if not path else geo.transform_row(row, matrix, path, row_layout[0])
        # Match the pipe adapter's canonical source-row decoration before the
        # full snapshot. Colour facts are compared too, never stripped or ignored.
        local["color_evidence"] = resolve_manifest_color(row, source["manifest"], parent_color,
            source_path=f"{path}/{row.get('record_id')}" if path else None)
        if local.get("layer") in {"", "0"} and owner:
            local["source_layer"] = local.get("layer")
            local["layer"] = inherited_layer
        oid = required(local.get("record_id"), "expanded.record_id")
        if oid in rows_by_id:
            raise ValueError("duplicate expanded physical identity")
        issues = list(inherited_issues) + source_geometry_issues(local)
        kind = str(local.get("entity_type") or "")
        if kind == "INSERT":
            issues.extend(insert_issues(row, inventory))
        obj = {"source_id": source["source_id"], "object_id": oid, "source_sha256": source["source_sha256"],
               "manifest_sha256": source["manifest_sha256"], "source_record_id": row["record_id"],
               "source_definition_record_id": local.get("source_definition_record_id"),
               "instance_path": local.get("instance_path"), "owner_insert_id": owner,
               "layout": row_layout[0], "entity_type": kind, "layer": local.get("layer"),
               "block_name": local.get("block_name"), "reference_chain": references,
               "row_sha256": snapshot(local), "issues": sorted(set(issues)), "disposition": "pending"}
        rows_by_id[oid] = local
        emitted[oid] = obj
        objects.append(obj)
        if owner:
            children[owner].append(oid)
        if kind != "INSERT":
            return
        name = str(row.get("block_name") or "")
        placed[name] += 1
        if name in stack or len(stack) >= max_depth:
            obj["issues"].append("cyclic_block_reference" if name in stack else "block_depth_limit")
            return
        if "array_placements_unavailable" in issues or name not in inventory or not inventory[name].get("serialized", True):
            return  # an array container is covered, but is never one physical symbol
        try:
            child_matrix = geo.compose_matrix(matrix, geo.matrix_for_insert(row, inventory[name].get("origin", [0, 0, 0])))
        except (ValueError, TypeError, KeyError) as exc:
            obj["issues"].append("invalid_insert_transform:" + str(exc))
            return
        vectors = [geo.transform_vector(v, child_matrix) for v in ([1, 0, 0], [0, 1, 0])]
        a, b = (math.dist(v, [0, 0, 0]) for v in vectors)
        dot = sum(x * y for x, y in zip(*vectors))
        if min(a, b) <= 0 or abs(a - b) > max(a, b) * 1e-8 or abs(dot) > a * b * 1e-8:
            obj["issues"].append("nonuniform_or_sheared_transform")
        child_path = (oid.removeprefix("CADINST-")) + "/" + name
        reference = (row.get("raw_geometry") or {}).get("block_reference") or {}
        chain = references + ([{"insert_id": oid, **reference}] if reference.get("xref") else [])
        for child in definitions.get(name, []):
            emit(child, child_matrix, child_path, oid, (*stack, name), local.get("layer"), obj["issues"], chain, local["color_evidence"])
        obj["issues"] = sorted(set(obj["issues"] + [issue for child in children[oid] for issue in emitted[child]["issues"]]))

    row_layout = [""]
    for row in source["layout_rows"]:
        if row.get("layout") in source["layouts"]:
            row_layout[0] = row["layout"]
            emit(row, geo.IDENTITY, "", None, (), row.get("layer"), [], [])
    by_id = {o["object_id"]: o for o in objects}

    def descendants(oid):
        result = []
        for child in children[oid]:
            result.append(child)
            result.extend(descendants(child))
        return result

    shapes = []
    for obj in objects:
        ids = descendants(obj["object_id"]) if obj["entity_type"] == "INSERT" else [obj["object_id"]]
        if obj["entity_type"] != "INSERT" and obj["owner_insert_id"]:
            continue
        if obj["entity_type"] not in CURVE_TYPES | {"INSERT"}:
            continue
        curve_ids = [oid for oid in ids if by_id[oid]["entity_type"] in CURVE_TYPES]
        issues = sorted(set(obj["issues"] + [issue for oid in ids for issue in by_id[oid]["issues"]]))
        shape = {"shape_id": obj["object_id"], "source_id": source["source_id"], "object_ids": [obj["object_id"]],
                 "geometry_object_ids": curve_ids, "kind": "block" if obj["entity_type"] == "INSERT" else "loose_curve",
                 "signature": None, "issues": issues}
        if not issues:
            try:
                shape["signature"] = shape_signature([p for oid in curve_ids for p in curve_parts(rows_by_id[oid])])
            except (ValueError, TypeError, KeyError) as exc:
                shape["issues"].append("shape_unavailable:" + str(exc))
        shapes.append(shape)
    definition_inventory = [{"source_id": source["source_id"], "block_name": name,
                             "placed_insert_records": placed[name], "serialized": item.get("serialized", True),
                             "direct_entity_count": item.get("direct_entity_count"),
                             "state": "placed" if placed[name] else "unplaced_definition_not_physical_instance"}
                            for name, item in sorted(inventory.items())]
    return objects, rows_by_id, shapes, definition_inventory


def loose_groups(source_id, objects, rows, tolerance):
    """Connected top-level curves on the same layer; no size-based deletion."""
    groups = defaultdict(list)
    for obj in objects:
        if obj["source_id"] == source_id and obj["entity_type"] in CURVE_TYPES and not obj["owner_insert_id"]:
            groups[(obj["layout"], obj["layer"])].append(obj["object_id"])
    output = []
    for (layout, layer), ids in groups.items():
        parent = {oid: oid for oid in ids}
        bins = defaultdict(list)
        def root(oid):
            while parent[oid] != oid:
                parent[oid] = parent[parent[oid]]
                oid = parent[oid]
            return oid
        for oid in ids:
            try:
                points = [geo.xyz(p[k]) if p["kind"] == "line" else geo.conic_point(p, p["start_param"] + (p["sweep_param"] if k == "end" else 0))
                          for p in curve_parts(rows[oid]) for k in ("start", "end")]
            except (ValueError, TypeError, KeyError):
                continue
            for point in points:
                cell = tuple(math.floor(v / tolerance) for v in point)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            for old, previous in bins[(cell[0] + dx, cell[1] + dy, cell[2] + dz)]:
                                if math.dist(point, previous) <= tolerance:
                                    parent[root(oid)] = root(old)
                bins[cell].append((oid, point))
        components = defaultdict(list)
        for oid in ids:
            components[root(oid)].append(oid)
        for members in components.values():
            if len(members) < 2:
                continue
            members.sort()
            shape = {"shape_id": "GROUP-" + snapshot(members)[:20], "source_id": source_id, "object_ids": members,
                     "geometry_object_ids": members, "kind": "loose_connected_group", "signature": None, "issues": [],
                     "layout": layout, "layer": layer}
            try:
                shape["signature"] = shape_signature([p for oid in members for p in curve_parts(rows[oid])])
            except (ValueError, TypeError, KeyError) as exc:
                shape["issues"].append("shape_unavailable:" + str(exc))
            output.append(shape)
    return output


def legend_pairs(specs, object_index, row_index, shape_index):
    results = []
    for index, spec in enumerate(specs):
        sid = required(spec.get("source_id"), "legend.source_id")
        layout = required(spec.get("layout"), "legend.layout")
        bounds = spec.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in bounds) or not (bounds[0] < bounds[2] and bounds[1] < bounds[3]):
            raise ValueError("legend.bounds: finite increasing rectangle required")
        tol = positive(spec.get("row_tolerance"), "legend.row_tolerance")
        distance = positive(spec.get("max_left_distance"), "legend.max_left_distance")
        selected = []
        for key, obj in object_index.items():
            if obj["source_id"] != sid or obj["layout"] != layout:
                continue
            row = row_index[key]
            x, y = row.get("x"), row.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
                selected.append((obj, row))
        if not selected:
            raise ValueError("legend scope contains no source objects")
        for obj, row in selected:
            if obj["entity_type"] not in TEXT_TYPES or not str(row.get("text") or "").strip():
                continue
            candidates = [(other, other_row) for other, other_row in selected if other["entity_type"] == "INSERT"
                          and 0 < row["x"] - other_row["x"] <= distance and abs(row["y"] - other_row["y"]) <= tol]
            # Every plausible same-row symbol is retained. Nearest is not a semantic ruling.
            results.append({"legend_scope_index": index, "source_id": sid, "text_object_id": obj["object_id"],
                            "text_row_sha256": obj["row_sha256"], "text": row["text"],
                            "state": "candidate_pair" if len(candidates) == 1 else "ambiguous" if candidates else "unpaired",
                            "symbols": [{"object_id": other["object_id"], "row_sha256": other["row_sha256"],
                                         "signature": (shape_index.get((sid, other["object_id"])) or {}).get("signature")}
                                        for other, _ in candidates]})
    return results


def build_census(request_path):
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    if not isinstance(request, dict):
        raise ValueError("census request must be an object")
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError("unsupported census request schema")
    if not isinstance(request.get("sources"), list) or not request["sources"]:
        raise ValueError("sources: nonempty explicit source list required")
    for name in ("sources", "legends", "evidence", "object_dispositions"):
        if not isinstance(request.get(name, []), list) or any(not isinstance(item, dict) for item in request.get(name, [])):
            raise ValueError(f"{name}: list of objects required")
    objects, shapes, definitions, source_refs, row_index = [], [], [], [], {}
    definition_records, out_of_scope_records = [], []
    seen_sources = set()
    for ref in request["sources"]:
        sid = required(ref.get("source_id"), "source_id")
        if sid in seen_sources:
            raise ValueError("duplicate source_id")
        seen_sources.add(sid)
        manifest_path = bound_file(ref.get("manifest"), request_path.parent, "manifest")
        source = load_source(sid, manifest_path, ref["manifest"]["sha256"], ref.get("layouts"))
        obs, rows, shps, defs = expand_source(source)
        shps.extend(loose_groups(sid, obs, rows, positive(request.get("connectivity_tolerance", 0.001), "connectivity_tolerance")))
        objects.extend(obs)
        shapes.extend(shps)
        definitions.extend(defs)
        row_index.update({(sid, oid): row for oid, row in rows.items()})
        source_refs.append({"source_id": sid, "manifest": {"path": str(manifest_path), "sha256": digest(manifest_path)},
                            "source_sha256": source["source_sha256"], "layouts": source["layouts"],
                            "read_status": source["manifest"].get("status"),
                            "source_read_coverage": source["manifest"].get("read_coverage", {})})
        definition_records.extend({"source_id": sid, "source_record_id": row["record_id"], "parent_block": name,
                                   "row_sha256": snapshot(row), "disposition": "definition_only_not_physical_instance"}
                                  for name, rows in source["definitions"].items() for row in rows)
        out_of_scope_records.extend({"source_id": sid, "object_id": row["record_id"], "layout": row["layout"],
                                     "row_sha256": snapshot(row), "disposition": "outside_requested_layouts"}
                                    for row in source["layout_rows"] if row["layout"] not in source["layouts"])
    object_index = {(o["source_id"], o["object_id"]): o for o in objects}
    shape_index = {(s["source_id"], s["shape_id"]): s for s in shapes}
    legends = request.get("legends", [])
    for spec in legends:
        if spec.get("source_id") not in seen_sources or spec.get("layout") not in next(s["layouts"] for s in source_refs if s["source_id"] == spec.get("source_id")):
            raise ValueError("legend outside requested source/layout scope")
    pairs = legend_pairs(legends, object_index, row_index, shape_index)
    labels = defaultdict(set)
    for pair in pairs:
        if pair["state"] == "candidate_pair" and pair["symbols"][0]["signature"]:
            labels[pair["symbols"][0]["signature"]].add(pair["text"])
    matches = defaultdict(list)
    for shape in shapes:
        if shape["signature"]:
            matches[shape["signature"]].append({"source_id": shape["source_id"], "shape_id": shape["shape_id"]})
    for shape in shapes:
        peers = matches.get(shape["signature"], [])
        shape["same_shape_candidate_count"] = max(0, len(peers) - 1)
        shape["same_shape_candidate_group"] = shape["signature"] if len(peers) > 1 else None
        shape["same_shape_candidates"] = [v for v in peers[:9] if v != {"source_id": shape["source_id"], "shape_id": shape["shape_id"]}][:8]
        shape["same_shape_candidate_preview_complete"] = shape["same_shape_candidate_count"] == len(shape["same_shape_candidates"])
        shape["legend_text_candidates"] = sorted(labels.get(shape["signature"], []))
        shape["legend_conflict"] = len(shape["legend_text_candidates"]) > 1
        shape["classification"] = "candidate_only"
        # A lone curve remains a candidate for review, never automatically a symbol.
    evidence = {}
    for ev in request.get("evidence", []):
        eid = required(ev.get("evidence_id"), "evidence_id")
        key = (ev.get("source_id"), ev.get("object_id"))
        if eid in evidence or key not in object_index or ev.get("row_sha256") != object_index[key]["row_sha256"]:
            raise ValueError("duplicate, out-of-scope, or stale evidence object")
        if ev.get("kind") not in {"cad_property", "drawing_view", "cross_view"}:
            raise ValueError("evidence kind requires actual source object/view")
        required(ev.get("basis"), "evidence.basis")
        if ev["kind"] == "drawing_view":
            bound_file(ev.get("view"), request_path.parent, "evidence.view")
        evidence[eid] = ev
    dispositions, claimed = [], set()
    for decision in request.get("object_dispositions", []):
        key = (decision.get("source_id"), decision.get("object_id"))
        if key not in object_index or key in claimed or decision.get("row_sha256") != object_index[key]["row_sha256"]:
            raise ValueError("duplicate, out-of-scope, or stale disposition object")
        obj = object_index[key]
        action = decision.get("decision")
        if action not in {"exclude", "claim"} or obj["issues"]:
            raise ValueError("unsupported decision or incomplete source object")
        eids = decision.get("evidence_ids")
        if not isinstance(eids, list) or not eids or any(eid not in evidence for eid in eids):
            raise ValueError("explicit disposition requires current evidence references")
        if not any((evidence[eid]["source_id"], evidence[eid]["object_id"]) == key for eid in eids):
            raise ValueError("disposition must cite its own source object evidence; signature alone cannot decide")
        for field in ("recognized_by", "recognized_at", "basis", "scope_basis", "identity_basis", "cross_check"):
            required(decision.get(field), "disposition." + field)
        if decision.get("professional_owner") not in {"qto-pipe-networks", "qto-duct-air-systems", "qto-components-points"}:
            raise ValueError("explicit professional_owner required")
        claimed.add(key)
        entry = {**copy.deepcopy(decision), "adoption_scope": "working", "review_status": "unreviewed", "decision_origin": "model"}
        dispositions.append(entry)
        obj.update(disposition="excluded_with_evidence" if action == "exclude" else "claimed_with_evidence", professional_owner=decision["professional_owner"])
    counts = Counter(o["disposition"] for o in objects)
    incomplete = any(o["issues"] for o in objects) or any(s["read_status"] != "PASS" for s in source_refs)
    block_groups = defaultdict(list)
    for obj in objects:
        if obj["entity_type"] == "INSERT":
            block_groups[(obj["source_id"], obj["layout"], str(obj["block_name"] or ""))].append(obj)
    return {"schema_version": SCHEMA, "producer_dependencies": producer_dependencies(),
            "request": {"path": str(request_path), "sha256": digest(request_path)},
            "sources": source_refs, "objects": objects, "shapes": shapes, "definition_inventory": definitions,
            "source_definition_records": definition_records, "out_of_scope_input_objects": out_of_scope_records,
            "block_instance_groups": [{"source_id": key[0], "layout": key[1], "block_name": key[2],
                                       "insert_record_count": len(members), "incomplete_insert_record_count": sum(bool(o["issues"]) for o in members),
                                       "object_ids": [o["object_id"] for o in members], "quantity_count": None}
                                      for key, members in sorted(block_groups.items())],
            "signature_groups": [{"signature": sig, "members": members} for sig, members in sorted(matches.items()) if len(members) > 1],
            "legend_pairs": pairs, "evidence": list(evidence.values()), "explicit_dispositions": dispositions,
            "coverage": {"input_layout_rows": sum(1 for o in objects if not o["instance_path"]), "object_count": len(objects),
                         "input_definition_records": len(definition_records), "out_of_scope_layout_rows": len(out_of_scope_records),
                         "object_disposition_counts": dict(counts), "incomplete_object_count": sum(bool(o["issues"]) for o in objects),
                         "shape_count": len(shapes), "matched_shape_candidate_count": sum(bool(s["same_shape_candidates"]) for s in shapes),
                         "legend_pair_count": len(pairs), "legend_conflict_shape_count": sum(s["legend_conflict"] for s in shapes)},
            "census_status": "PARTIAL" if incomplete else "SCOPED_CENSUS_COMPLETE",
            "type_absence_proven": False, "quantity_acceptance_proven": False,
            "limitations": ["Shape and legend pairing are candidates; no valve type, size, absence or engineering quantity is established.",
                            "Unplaced definitions are inventoried separately. Pending and unread objects never mean zero.",
                            "Working explicit dispositions are model recognition, not human approval."]}


def verify_report(ref, source_manifest_path):
    """Rebuild from current raw source and evidence; a report's self-hash is not authority."""
    manifest_path = Path(source_manifest_path).resolve()
    report_path = bound_file(ref, manifest_path.parent, "symbol_census_report")
    actual = json.loads(report_path.read_text(encoding="utf-8-sig"))
    request_path = bound_file(actual.get("request"), report_path.parent, "census.request")
    expected = build_census(request_path)
    if snapshot(actual) != snapshot(expected):
        raise ValueError("census report differs from recomputed source evidence")
    matching = [s for s in expected["sources"] if Path(s["manifest"]["path"]).resolve() == manifest_path and s["manifest"]["sha256"] == digest(manifest_path)]
    if len(matching) != 1:
        raise ValueError("census must bind exactly one current source manifest")
    sid = matching[0]["source_id"]
    return expected, sid


def pipe_context(ref, source_manifest_path, layout):
    report, sid = verify_report(ref, source_manifest_path)
    if layout not in next(s["layouts"] for s in report["sources"] if s["source_id"] == sid):
        raise ValueError("pipe layout outside symbol census scope")
    objects = {o["object_id"]: o for o in report["objects"] if o["source_id"] == sid and o["layout"] == layout}
    decisions = {}
    for decision in report["explicit_dispositions"]:
        if decision["source_id"] != sid or decision["object_id"] not in objects or decision["professional_owner"] != "qto-pipe-networks":
            continue
        if decision["decision"] == "exclude":
            if objects[decision["object_id"]]["entity_type"] not in CURVE_TYPES:
                raise ValueError("pipe symbol exclusion requires explicit curve objects; INSERT ownership uses the existing adjudication interface")
            decisions[decision["object_id"]] = decision
    candidates = defaultdict(list)
    for shape in report["shapes"]:
        if shape["source_id"] == sid and (shape["same_shape_candidates"] or shape["legend_text_candidates"]):
            for oid in shape["object_ids"]:
                if oid in objects:
                    candidates[oid].append({"shape_id": shape["shape_id"], "same_shape_candidates": shape["same_shape_candidates"],
                                            "same_shape_candidate_group": shape["same_shape_candidate_group"],
                                            "same_shape_candidate_count": shape["same_shape_candidate_count"],
                                            "legend_text_candidates": shape["legend_text_candidates"], "legend_conflict": shape["legend_conflict"],
                                            "classification": "candidate_only"})
    return {"report": ref, "source_id": sid, "objects": objects, "exclusions": decisions, "candidates": candidates}


def protected_inputs(report):
    """Files a CLI output must never replace, including source and view evidence."""
    paths = {Path(report["request"]["path"]).resolve()}
    for source in report["sources"]:
        manifest_path = Path(source["manifest"]["path"]).resolve()
        paths.add(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        for ref in [manifest.get("authoritative_source"), manifest.get("exchange_dxf"), manifest.get("exchange_conversion"), *manifest.get("artifacts", {}).values()]:
            if isinstance(ref, dict) and ref.get("path"):
                value = Path(ref["path"])
                paths.add((manifest_path.parent / value).resolve() if not value.is_absolute() else value.resolve())
    for evidence in report["evidence"]:
        if evidence.get("view"):
            paths.add(bound_file(evidence["view"], Path(report["request"]["path"]).parent, "evidence.view"))
    return paths


def pipe_exclusion(row, context):
    oid = row.get("record_id")
    decision = context["exclusions"].get(oid)
    if decision is None:
        return None
    obj = context["objects"][oid]
    for key in ("source_sha256", "source_definition_record_id", "instance_path", "entity_type", "layout"):
        if row.get(key) != obj.get(key):
            raise ValueError("pipe row identity differs from current symbol evidence: " + str(oid))
    # A prescoped index is a cache, not source authority. Rehashing an edited
    # index cannot renew evidence for a different curve with the same identity.
    # Exclusions accept only curves: their rows are still unmodified source or
    # canonical transform_row records here (before route cleanup/unit scaling).
    # INSERT-only extent annotations are never compared as curve evidence.
    if snapshot(row) != obj["row_sha256"]:
        raise ValueError("pipe curve source snapshot differs from current symbol evidence: " + str(oid))
    return decision
