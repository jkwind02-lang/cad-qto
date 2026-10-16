"""Shared segment graph (fifth batch L0): line work of a neutral package as a connectivity graph, geometry only.
Contract: _qto-shared-evidence/references/线段连通图.md.

Leader tracing (L2), drawn-route tracing (L1), length by layer (L3) and collinear overlap (L4) read it; none of them
gives a line a meaning here. Rules:
- LINE, LWPOLYLINE, POLYLINE, ARC parts, and LEADER vertices as straight parts; MLEADER/MULTILEADER is counted as
  unsupported, never silently dropped. Lines inside declared container blocks are expanded with the d2 instance path
  (CAD-<top handle>/<block>/CAD-<inner handle>, the point adapter's PT- ids without the prefix).
- Ends join only when they snap: within snap_tolerance, in bounded clusters (a chain of near ends never joins two ends
  farther apart than the tolerance). A crossing never joins.
- An end lying on another segment's interior is a t_contacts candidate, never an automatic join.
- A gap is bridged only within gap_tolerance and when both free ends point at each other within gap_angle_deg; an end
  with two such partners is ambiguous and bridges nothing.
- Scope uses qto_cad_geometry.bounds_intersect on each part's exact box, the point adapter's criterion; a part that
  reaches past the scope edge is kept and flagged.
Tolerances are in drawing units; the caller converts (1 mm is 0.001 in a drawing in metres).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

import qto_cad_geometry as cad_geometry
from qto_spatial import BoundsIndex, PointIndex

SCHEMA = "qto-segment-graph/1.0"
CURVE_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC"})
LEADER_TYPES = frozenset({"LEADER"})
UNSUPPORTED_LEADERS = frozenset({"MLEADER", "MULTILEADER"})
MAX_DEPTH = 16


def _part_box(part: dict[str, Any]) -> list[float]:
    points = cad_geometry.part_extrema(part)
    return [min(p[i] for p in points) for i in range(3)] + [max(p[i] for p in points) for i in range(3)]


def _part_end(part: dict[str, Any], which: str) -> list[float]:
    if part["kind"] == "line":
        return list(part[which])
    t = part["start_param"] + (part["sweep_param"] if which == "end" else 0.0)
    return cad_geometry.conic_point(part, t)


def _outward(part: dict[str, Any], which: str) -> list[float]:
    """Unit direction leaving the segment at its start or end (the way a continuation would go)."""
    if part["kind"] == "line":
        a, b = part["start"], part["end"]
        v = [x - y for x, y in zip(a, b)] if which == "start" else [y - x for x, y in zip(a, b)]
    else:
        t = part["start_param"] + (part["sweep_param"] if which == "end" else 0.0)
        tangent = [(-u * math.sin(t) + w * math.cos(t)) * part["sweep_param"] for u, w in zip(part["u"], part["v"])]
        v = [-x for x in tangent] if which == "start" else tangent
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else [0.0, 0.0, 0.0]


def _project(p: list[float], part: dict[str, Any]) -> tuple[float, float]:
    """(distance, fraction) of the closest point of part to p."""
    if part["kind"] == "line":
        a, b = part["start"], part["end"]
        v = [y - x for x, y in zip(a, b)]
        den = sum(x * x for x in v)
        t = max(0.0, min(1.0, sum((q - x) * w for q, x, w in zip(p, a, v)) / den)) if den else 0.0
        return math.dist(p, [x + w * t for x, w in zip(a, v)]), t
    from qto_pipe_topology import project_part   # conic projection, only when an arc needs it
    return project_part(p, part)


def row_parts(row: dict[str, Any], matrix=None) -> tuple[list[dict[str, Any]], str | None]:
    """(parts in WCS, problem). LEADER vertices become straight parts; rows without usable geometry give a problem."""
    entity_type = str(row.get("entity_type") or "").upper()
    if entity_type in LEADER_TYPES:
        vertices = (row.get("raw_geometry") or {}).get("vertices") or []
        try:
            points = [cad_geometry.xyz(v) for v in vertices]
        except (TypeError, ValueError, IndexError):
            return [], "引线顶点无效"
        if matrix is not None:
            points = [cad_geometry.transform_point(p, matrix) for p in points]
        if len(points) < 2:
            return [], "引线顶点少于两个"
        return [{"kind": "line", "start": a, "end": b} for a, b in zip(points, points[1:])], None
    geometry = row.get("geometry")
    if geometry is not None:
        if geometry.get("schema") != cad_geometry.GEOMETRY_SCHEMA or geometry.get("coordinate_system") != "WCS":
            return [], "几何契约或坐标系不支持，用新版中性提取器重提"
        try:
            if matrix is not None:
                geometry = cad_geometry.transform_geometry(geometry, matrix)
            parts = [dict(p) for p in geometry.get("parts") or []]
            for p in parts:
                if p.get("kind") not in ("line", "ellipse_arc"):
                    return [], f"不支持的几何部件 {p.get('kind')}"
            return parts, None
        except (KeyError, TypeError, ValueError) as exc:
            return [], f"几何无效：{exc}"
    if entity_type == "LINE" and row.get("start") is not None and row.get("end") is not None:
        a, b = cad_geometry.xyz(row["start"]), cad_geometry.xyz(row["end"])
        if matrix is not None:
            a, b = cad_geometry.transform_point(a, matrix), cad_geometry.transform_point(b, matrix)
        return [{"kind": "line", "start": a, "end": b}], None
    return [], "没有可用几何（geometry 为空），用新版中性提取器重提"


def collect_segments(layout_rows, definitions=None, origins=None, *, layer_pattern: str | None = None,
                     expand_blocks=(), scope: dict[str, float] | None = None, max_depth: int = MAX_DEPTH) -> dict[str, Any]:
    """Segments of one layout. definitions: {block name: [definition rows]}; origins: {block name: origin}. Only
    blocks named in expand_blocks are walked; a child on layer 0 takes its insert's layer (AutoCAD's ByBlock layer)."""
    layer_re = re.compile(layer_pattern) if layer_pattern else None
    definitions, origins, expand = definitions or {}, origins or {}, set(expand_blocks)
    segments: list[dict[str, Any]] = []
    out: dict[str, Any] = {"segments": segments, "degenerate_parts": [], "geometry_problems": [], "unsupported": defaultdict(int),
                           "out_of_scope_parts": 0, "expansion_problems": []}

    def take(row, layer, path, matrix):
        entity_type = str(row.get("entity_type") or "").upper()
        if layer_re is not None and not layer_re.search(layer):
            return
        if entity_type in UNSUPPORTED_LEADERS:
            out["unsupported"][entity_type] += 1
            return
        if entity_type not in CURVE_TYPES and entity_type not in LEADER_TYPES:
            return
        parts, problem = row_parts(row, matrix)
        if problem:
            out["geometry_problems"].append({"source_id": path, "entity_type": entity_type, "layer": layer, "problem": problem})
            return
        for index, part in enumerate(parts):
            length = cad_geometry.measure_part(part)[0]
            if length <= 0:
                out["degenerate_parts"].append({"source_id": path, "part_index": index})
                continue
            box = _part_box(part)
            if not cad_geometry.bounds_intersect(box, scope):
                out["out_of_scope_parts"] += 1
                continue
            inside = scope is None or (box[0] >= scope["xmin"] and box[3] < scope["xmax_exclusive"]
                                       and box[1] >= scope["ymin"] and box[4] < scope["ymax_exclusive"])
            segments.append({"segment_id": f"SEG-{path}:{index}", "source_id": path, "handle": str(row.get("entity_handle") or ""),
                             "entity_type": entity_type, "layer": layer, "part_index": index, "kind": part["kind"],
                             "start": _part_end(part, "start"), "end": _part_end(part, "end"), "length": length,
                             "bbox3d": box, "crosses_scope_edge": not inside, "part": part})

    def walk(block, matrix, layer, path, stack):
        if block in stack or len(stack) >= max_depth:
            out["expansion_problems"].append({"source_id": path, "problem": f"块循环引用或超过 {max_depth} 层：{block}"}); return
        rows = definitions.get(block)
        if rows is None:
            out["expansion_problems"].append({"source_id": path, "problem": f"块定义里没有这个块：{block}"}); return
        for child in rows:
            handle = str(child.get("entity_handle") or "")
            own = str(child.get("layer") or "")
            effective = layer if own in ("", "0") and layer else own
            child_path = f"{path}/{block}/CAD-{handle}"
            if str(child.get("entity_type") or "").upper() == "INSERT":
                name = str(child.get("block_name") or "")
                if name in expand:
                    try:
                        child_matrix = cad_geometry.compose_matrix(matrix, cad_geometry.matrix_for_insert(child, origins.get(name, [0, 0, 0])))
                    except (TypeError, ValueError) as exc:
                        out["expansion_problems"].append({"source_id": child_path, "problem": f"块内插入变换无效：{exc}"}); continue
                    walk(name, child_matrix, effective, child_path, (*stack, block))
                continue
            take(child, effective, child_path, matrix)

    for row in layout_rows:
        handle = str(row.get("entity_handle") or "")
        layer = str(row.get("layer") or "")
        if str(row.get("entity_type") or "").upper() == "INSERT":
            name = str(row.get("block_name") or "")
            if name in expand:
                try:
                    matrix = cad_geometry.matrix_for_insert(row, origins.get(name, [0, 0, 0]))
                except (TypeError, ValueError) as exc:
                    out["expansion_problems"].append({"source_id": f"CAD-{handle}", "problem": f"顶层插入变换无效：{exc}"}); continue
                walk(name, matrix, layer, f"CAD-{handle}", ())
            continue
        take(row, layer, f"CAD-{handle}", None)
    out["unsupported"] = dict(out["unsupported"])
    return out


def build_graph(segments: list[dict[str, Any]], *, snap_tolerance: float = 0.0, gap_tolerance: float = 0.0,
                gap_angle_deg: float = 10.0, t_tolerance: float | None = None) -> dict[str, Any]:
    """Nodes, components, dangling ends, T-contact candidates and bridged gaps over collect_segments' segments."""
    for name, value in (("snap_tolerance", snap_tolerance), ("gap_tolerance", gap_tolerance), ("gap_angle_deg", gap_angle_deg)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} 须为非负有限数值")
    t_tolerance = snap_tolerance if t_tolerance is None else t_tolerance
    ends = [(i, which, tuple(s[which])) for i, s in enumerate(segments) for which in ("start", "end")]
    # bounded clusters: a merge that would put two ends farther apart than the tolerance is refused
    parent = list(range(len(ends)))
    members = {i: [i] for i in parent}
    refused = []

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    if ends:
        index = PointIndex([e[2] for e in ends], max(snap_tolerance, 1e-9))
        for a, b in index.pairs(snap_tolerance):
            ra, rb = root(a), root(b)
            if ra == rb:
                continue
            if any(math.dist(ends[x][2], ends[y][2]) > snap_tolerance for x in members[ra] for y in members[rb]):
                refused.append([segments[ends[a][0]]["segment_id"], segments[ends[b][0]]["segment_id"]]); continue
            parent[rb] = ra; members[ra].extend(members.pop(rb))
    node_of, nodes = {}, {}
    for i in range(len(ends)):
        r = root(i)
        if r not in nodes:
            pts = [ends[x][2] for x in members[r]]
            nodes[r] = {"node_id": f"N-{len(nodes) + 1:05d}", "point": [math.fsum(p[k] for p in pts) / len(pts) for k in range(3)], "ends": []}
        node_of[i] = r
        nodes[r]["ends"].append({"segment_id": segments[ends[i][0]]["segment_id"], "end": ends[i][1]})
    seg_nodes = [(nodes[node_of[2 * i]]["node_id"], nodes[node_of[2 * i + 1]]["node_id"]) for i in range(len(segments))]
    degree = defaultdict(int)
    for a, b in seg_nodes:
        degree[a] += 1; degree[b] += 1
    node_list = sorted(nodes.values(), key=lambda n: n["node_id"])
    point_of = {n["node_id"]: n["point"] for n in node_list}

    # free ends: the only end at a node (degree 1)
    free = [i for i in range(len(ends)) if degree[nodes[node_of[i]]["node_id"]] == 1]
    # T contacts: a free end on another segment's interior
    t_contacts = []
    if free and segments:
        boxes = BoundsIndex([(i, s["bbox3d"]) for i, s in enumerate(segments)])
        for i in free:
            seg_index, which, p = ends[i]
            reach = t_tolerance
            for j in boxes.query_box([p[0] - reach, p[1] - reach, p[2] - reach, p[0] + reach, p[1] + reach, p[2] + reach]):
                if j == seg_index:
                    continue
                dist, fraction = _project(list(p), segments[j]["part"])
                own = nodes[node_of[i]]["node_id"]
                if dist <= reach and own not in seg_nodes[j] and 0.0 < fraction < 1.0:
                    t_contacts.append({"segment_id": segments[seg_index]["segment_id"], "end": which, "node_id": own,
                                       "on_segment_id": segments[j]["segment_id"], "distance": dist, "fraction": fraction})
    # gap bridges: two free ends within gap_tolerance (and past snapping), each pointing at the other
    bridges, ambiguous_gaps = [], []
    if gap_tolerance > snap_tolerance and len(free) > 1:
        cos_limit = math.cos(math.radians(gap_angle_deg))
        index = PointIndex([ends[i][2] for i in free], max(gap_tolerance, 1e-9))
        partners = defaultdict(list)
        for a, b in index.pairs(gap_tolerance):
            ia, ib = free[a], free[b]
            pa, pb = ends[ia][2], ends[ib][2]
            d = math.dist(pa, pb)
            if d <= snap_tolerance or node_of[ia] == node_of[ib]:
                continue
            da = _outward(segments[ends[ia][0]]["part"], ends[ia][1])
            db = _outward(segments[ends[ib][0]]["part"], ends[ib][1])
            ab = [(y - x) / d for x, y in zip(pa, pb)]
            if sum(x * y for x, y in zip(da, ab)) >= cos_limit and sum(-x * y for x, y in zip(db, ab)) >= cos_limit:
                partners[ia].append((d, ib)); partners[ib].append((d, ia))
        for ia in sorted(partners):
            if len(partners[ia]) > 1:
                ambiguous_gaps.append({"segment_id": segments[ends[ia][0]]["segment_id"], "end": ends[ia][1],
                                       "candidates": sorted(segments[ends[ib][0]]["segment_id"] for _, ib in partners[ia])})
        for ia in sorted(partners):
            if len(partners[ia]) != 1:
                continue
            d, ib = partners[ia][0]
            if ia < ib and len(partners[ib]) == 1:
                na, nb = nodes[node_of[ia]]["node_id"], nodes[node_of[ib]]["node_id"]
                bridges.append({"bridge_id": f"GAP-{len(bridges) + 1:05d}", "from_node": na, "to_node": nb, "length": d,
                                "from_segment_id": segments[ends[ia][0]]["segment_id"], "to_segment_id": segments[ends[ib][0]]["segment_id"]})
                degree[na] += 1; degree[nb] += 1
    # components over segments and bridges
    link = {n["node_id"]: n["node_id"] for n in node_list}

    def find(x):
        while link[x] != x:
            link[x] = link[link[x]]; x = link[x]
        return x
    for a, b in [*seg_nodes, *((g["from_node"], g["to_node"]) for g in bridges)]:
        ra, rb = find(a), find(b)
        if ra != rb:
            link[max(ra, rb)] = min(ra, rb)
    groups = defaultdict(lambda: {"segments": [], "nodes": set(), "bridges": []})
    for i, (a, b) in enumerate(seg_nodes):
        g = groups[find(a)]; g["segments"].append(i); g["nodes"].update((a, b))
    for g in bridges:
        groups[find(g["from_node"])]["bridges"].append(g["bridge_id"])
    components = []
    for k, (key, g) in enumerate(sorted(groups.items(), key=lambda kv: min(segments[i]["segment_id"] for i in kv[1]["segments"]))):
        components.append({"component_id": f"C-{k + 1:05d}", "segment_ids": [segments[i]["segment_id"] for i in g["segments"]],
                           "node_ids": sorted(g["nodes"]), "bridge_ids": g["bridges"],
                           "length": math.fsum(segments[i]["length"] for i in g["segments"]),
                           "layers": sorted({segments[i]["layer"] for i in g["segments"]}),
                           "dangling_node_ids": sorted(n for n in g["nodes"] if degree[n] == 1)})
    component_of = {s: c["component_id"] for c in components for s in c["segment_ids"]}
    out_segments = []
    for s, (a, b) in zip(segments, seg_nodes):
        row = {k: v for k, v in s.items() if k != "part"}
        row.update(start_node=a, end_node=b, component_id=component_of[s["segment_id"]])
        out_segments.append(row)
    return {
        "schema": SCHEMA,
        "parameters": {"snap_tolerance": snap_tolerance, "gap_tolerance": gap_tolerance, "gap_angle_deg": gap_angle_deg, "t_tolerance": t_tolerance},
        "segments": out_segments,
        "nodes": [{"node_id": n["node_id"], "point": n["point"], "degree": degree[n["node_id"]], "ends": n["ends"]} for n in node_list],
        "components": components,
        "dangling_nodes": sorted(n for n in point_of if degree[n] == 1),
        "t_contacts": t_contacts,
        "gap_bridged": bridges,
        "ambiguous_gaps": ambiguous_gaps,
        "refused_snaps": refused,
        "counts": {"segments": len(segments), "nodes": len(node_list), "components": len(components),
                   "dangling_nodes": sum(1 for n in point_of if degree[n] == 1), "t_contacts": len(t_contacts),
                   "gap_bridged": len(bridges), "ambiguous_gaps": len(ambiguous_gaps), "refused_snaps": len(refused)},
    }
