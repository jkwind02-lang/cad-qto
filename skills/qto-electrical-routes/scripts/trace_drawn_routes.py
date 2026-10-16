#!/usr/bin/env python3
"""Trace drawn electrical routes on the shared segment graph (第五批 L1; registration 20261016-1056 expectations 1, 3).

From a box to a device (a point-adapter instance id, or a coordinate) along the lines of the route layers: segment by
segment with handles and lengths, gaps behind symbols bridged and listed. Ends off the network, breaks, equal-length
alternatives and paths over the scope edge become to-dos; nothing falls back to a straight or right-angle distance.
A circuit label in the request is kept as evidence only: the tool never decides which circuit a line belongs to, and
it never matches device names (a system-diagram name against a plan name is the model's call, with its basis listed).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
import qto_cad_geometry as cad_geometry  # noqa: E402
import qto_segment_graph as segment_graph  # noqa: E402
from qto_spatial import BoundsIndex  # noqa: E402

VERSION = "qto-drawn-route-tracer/1.0.0"
REQUEST_SCHEMA = "qto-drawn-route-request/1.0"
RESULT_SCHEMA = "qto-drawn-route-trace/1.0"
PATHS_SCHEMA = "qto-drawn-route-paths/1.0"
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
DYNAMIC_STATE_CODES = cad_geometry.DYNAMIC_STATE_CODES
END_NAME = {"from": "起点", "to": "终点"}


class InputError(ValueError):
    pass


def fail(message: str) -> None:
    raise InputError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 JSON {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path} 顶层必须是对象")
    return value


def load_stream(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"{path}:{line_number} JSON 无效: {exc}")
            if not isinstance(value, dict):
                fail(f"{path}:{line_number} 必须是对象")
            yield value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        fail(f"{label} 必须是有限数值")
    return float(value)


def tolerance(request: dict[str, Any], key: str, default: float | None = None) -> float:
    value = request.get(key, default)
    if value is None:
        fail(f"{key} 必须写明（按输出单位米，按图符校准）")
    value = finite(value, key)
    if value < 0:
        fail(f"{key} 须为非负有限数值")
    return value


def pattern(value: Any, label: str) -> re.Pattern[str]:
    try:
        return re.compile(text(value, label), re.IGNORECASE)
    except re.error as exc:
        fail(f"{label} 正则表达式无效: {exc}")


def source_from_manifest(path: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Same checks as the point adapter: neutral schema, full WCS geometry, no identity conflicts, stream hashes."""
    manifest = load_json(path)
    if manifest.get("schema_version") != "qto-cad-neutral-evidence/1.0":
        fail("cad-manifest schema_version 必须为 qto-cad-neutral-evidence/1.0")
    if manifest.get("geometry_schema_version") != cad_geometry.GEOMETRY_SCHEMA:
        fail("CAD旧缓存缺少完整WCS几何，请用新版中性提取器重新生成；不得继承旧曲线测长")
    if manifest.get("source_identity_issues"):
        fail("CAD源实体身份存在冲突，不能按句柄认定实例和走线；先核验原图并重新取证")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        fail("CAD清单缺少 artifacts")
    resolved = []
    for key in ("layout_entities", "block_definitions"):
        artifact = artifacts.get(key)
        if not isinstance(artifact, dict):
            fail(f"CAD清单缺少 artifacts.{key}")
        stream = Path(text(artifact.get("path"), f"{key}.path"))
        if not stream.is_absolute():
            stream = path.parent / stream
        stream = stream.resolve()
        if not stream.is_file() or sha256(stream) != str(artifact.get("sha256") or "").lower():
            fail(f"CAD清单中的 {key} 缺失或哈希不一致")
        resolved.append(stream)
    return resolved[0], resolved[1], manifest


def parse_endpoint(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or ("instance_id" in raw) == ("point" in raw):
        fail(f"{label} 须写 instance_id（点位实例号）或 point（输出单位坐标）其中一个")
    if "instance_id" in raw:
        return {"instance_id": text(raw["instance_id"], f"{label}.instance_id")}
    point = raw["point"]
    if not isinstance(point, list) or len(point) < 2:
        fail(f"{label}.point 须为 [x, y]")
    return {"point": [finite(point[0], f"{label}.point[0]"), finite(point[1], f"{label}.point[1]")]}


def parse_request(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        fail(f"request.schema 必须为 {REQUEST_SCHEMA}")
    unit = request.get("unit")
    if not isinstance(unit, dict):
        fail("unit 必须是对象")
    factor = finite(unit.get("factor_to_output"), "unit.factor_to_output")
    if factor <= 0:
        fail("unit.factor_to_output 必须大于0")
    if text(unit.get("output_coordinate_unit", "m"), "unit.output_coordinate_unit") != "m":
        fail("unit.output_coordinate_unit 只能是 m（走线长度按米交给路径台账）")
    scope = request.get("scope") or {}
    if not isinstance(scope, dict):
        fail("scope 必须是对象")
    bounds = None
    if scope.get("bounds") is not None:
        raw = scope["bounds"]
        if not isinstance(raw, dict):
            fail("scope.bounds 必须是对象")
        bounds = {key: finite(raw.get(key), f"scope.bounds.{key}") for key in ("xmin", "xmax_exclusive", "ymin", "ymax_exclusive")}
        if bounds["xmin"] >= bounds["xmax_exclusive"] or bounds["ymin"] >= bounds["ymax_exclusive"]:
            fail("scope.bounds 范围无效")
    expand = request.get("expand_blocks", [])
    if not isinstance(expand, list) or any(not isinstance(name, str) or not name for name in expand):
        fail("expand_blocks 须为块名数组")
    routes = request.get("routes")
    if not isinstance(routes, list) or not routes:
        fail("routes 须为非空数组")
    parsed, seen = [], set()
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            fail(f"routes[{index}] 必须是对象")
        route_id = text(route.get("route_id"), f"routes[{index}].route_id")
        if route_id in seen:
            fail(f"route_id 重复: {route_id}")
        seen.add(route_id)
        parsed.append({"route_id": route_id, "from": parse_endpoint(route.get("from"), f"routes[{index}].from"),
                       "to": parse_endpoint(route["to"], f"routes[{index}].to") if route.get("to") is not None else None,
                       "circuit_label": route.get("circuit_label")})
    max_depth = int(request.get("max_depth", 16))
    if max_depth < 1:
        fail("max_depth 必须大于0")
    return {
        "layout": text(request.get("layout", "Model"), "layout"), "factor": factor,
        "unit_source_id": text(unit.get("source_id"), "unit.source_id"), "bounds": bounds,
        "route_re": pattern(request.get("route_layers"), "route_layers"),
        "box_re": pattern(request["box_block_pattern"], "box_block_pattern") if request.get("box_block_pattern") else None,
        "expand": set(expand), "max_depth": max_depth,
        "snap": tolerance(request, "snap_tolerance", 0.005), "gap": tolerance(request, "gap_tolerance", 0.0),
        "gap_angle_deg": tolerance(request, "gap_angle_deg", 10.0), "endpoint": tolerance(request, "endpoint_tolerance"),
        "equal": tolerance(request, "equal_length_tolerance", 0.001),
        "drawing_id": str(request.get("drawing_id") or "").strip(), "routes": parsed,
    }


def box_distance(point: list[float], box: list[float]) -> float:
    return math.hypot(max(box[0] - point[0], 0.0, point[0] - box[2]), max(box[1] - point[1], 0.0, point[1] - box[3]))


def chord_hits_box(a: list[float], b: list[float], box: list[float]) -> bool:
    """Liang-Barsky: does the straight piece a-b meet the rectangle (edges included)?"""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]), (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


class Drawing:
    """Instances (scope test and outline exactly as the point adapter's scope_of), route segments and their graph."""

    def __init__(self, manifest_path: Path, req: dict[str, Any]):
        self.req = req
        layout_path, block_path, self.manifest = source_from_manifest(manifest_path)
        self.definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in load_stream(block_path):
            if row.get("row_type") == "entity":
                self.definitions[str(row.get("parent_block") or "")].append(row)
        inventory = {str(item.get("block_name")): item for item in self.manifest.get("blocks", [])
                     if isinstance(item, dict) and item.get("block_name")}
        self.origins = {name: [float(v) for v in item.get("origin", [0.0, 0.0, 0.0])[:3]] for name, item in inventory.items()}
        self.empty = {name for name, item in inventory.items()
                      if cad_geometry.is_empty_definition(item) and name not in self.definitions}
        self.block_boxes: dict[str, Any] = {}
        self.inserts: dict[str, dict[str, Any]] = {}
        self.curves: list[dict[str, Any]] = []
        types = segment_graph.CURVE_TYPES | segment_graph.LEADER_TYPES | segment_graph.UNSUPPORTED_LEADERS
        for row in load_stream(layout_path):
            if row.get("row_type") != "entity" or row.get("layout") != req["layout"]:
                continue
            kind = str(row.get("entity_type") or "").upper()
            if kind == "INSERT":
                self.inserts[str(row.get("entity_handle") or "").upper()] = row
            elif kind in types:
                self.curves.append(row)

    def scope_of(self, insert: dict[str, Any], parent: tuple[float, ...]) -> dict[str, Any]:
        """Point adapter scope_of (Codex retest R2, K3): in scope, 2D world box or None, notes, anchor, matrix."""
        name = str(insert.get("block_name") or "")
        matrix = cad_geometry.compose_matrix(parent, cad_geometry.matrix_for_insert(insert, self.origins.get(name, [0.0, 0.0, 0.0])))
        anchor = cad_geometry.transform_point([finite(insert.get(axis, 0.0), f"{insert.get('record_id')}.{axis}")
                                               for axis in ("x", "y", "z")], parent)
        dynamic_only = cad_geometry.issue_codes_within(insert, DYNAMIC_STATE_CODES)
        usable = not insert.get("geometry_issues") or dynamic_only
        if name in self.empty and usable:
            return {"in_scope": cad_geometry.point_in_scope(anchor, self.req["bounds"]), "box": None, "notes": frozenset(),
                    "anchor": anchor, "matrix": matrix}
        local, notes = cad_geometry.definition_extent(name, self.definitions, self.origins, self.req["max_depth"], self.block_boxes,
                                                      (), self.empty, DYNAMIC_STATE_CODES)
        world = cad_geometry.transform_bounds(local, matrix) if local and usable else None
        if not world:
            return {"in_scope": True, "box": None, "notes": frozenset(), "anchor": anchor, "matrix": matrix}
        return {"in_scope": cad_geometry.extent_in_scope(world, notes, anchor, self.req["bounds"]),
                "box": [world[0], world[1], world[3], world[4]], "notes": notes, "anchor": anchor, "matrix": matrix}

    def locate(self, instance_id: str) -> dict[str, Any]:
        """Walk a point-adapter instance id (PT-CAD-<handle>[/<block>/CAD-<handle>]...) down to its insert."""
        path = instance_id[3:] if instance_id.startswith("PT-") else instance_id
        parts = path.split("/")
        if not parts[0].startswith("CAD-") or len(parts) % 2 == 0:
            return {"problem": "实例在图上找不到", "detail": f"实例号写法不认识：{instance_id}"}
        insert = self.inserts.get(parts[0][4:].upper())
        if insert is None:
            return {"problem": "实例在图上找不到", "detail": f"图上没有句柄 {parts[0][4:]} 的插入"}
        parent, containers = IDENTITY, []
        for block, child in zip(parts[1::2], parts[2::2]):
            here = self.scope_of(insert, parent)
            if not here["in_scope"]:
                return {"problem": "不在范围内", "detail": f"所在的 {insert.get('block_name')} 在范围外"}
            if str(insert.get("block_name") or "") != block:
                return {"problem": "实例在图上找不到", "detail": f"插入的块是 {insert.get('block_name')}，不是 {block}"}
            handle = child[4:].upper() if child.startswith("CAD-") else ""
            found = [row for row in self.definitions.get(block, ())
                     if str(row.get("entity_type") or "").upper() == "INSERT" and str(row.get("entity_handle") or "").upper() == handle]
            if len(found) != 1:
                return {"problem": "实例在图上找不到", "detail": f"块 {block} 里没有句柄 {child[4:]} 的插入"}
            containers.append(block)
            parent, insert = here["matrix"], found[0]
        here = self.scope_of(insert, parent)
        if not here["in_scope"]:
            return {"problem": "不在范围内", "detail": "实例外框在范围外"}
        return {"insert": insert, "containers": containers, **here}

    def boxes(self, box_re: re.Pattern[str] | None, expand: set[str]) -> list[tuple[str, list[float]]]:
        """Box instances for the pass-through check: top level and inside the expanded blocks, scoped as above."""
        found: list[tuple[str, list[float]]] = []
        if box_re is None:
            return found

        def walk(insert, parent, path, stack):
            here = self.scope_of(insert, parent)
            if not here["in_scope"]:
                return
            name = str(insert.get("block_name") or "")
            if box_re.search(name) and here["box"]:
                found.append((f"PT-{path}", here["box"]))
            if name in expand and name not in stack and len(stack) < self.req["max_depth"]:
                for child in self.definitions.get(name, ()):
                    if str(child.get("entity_type") or "").upper() == "INSERT":
                        walk(child, here["matrix"], f"{path}/{name}/CAD-{child.get('entity_handle')}", (*stack, name))
        for handle, insert in self.inserts.items():
            walk(insert, IDENTITY, f"CAD-{insert.get('entity_handle')}", ())
        return found


def trace_routes(drawing: Drawing, req: dict[str, Any]) -> dict[str, Any]:
    f = req["factor"]
    ends: dict[tuple[str, str], dict[str, Any]] = {}
    expand = set(req["expand"])
    for route in req["routes"]:
        for which in ("from", "to"):
            end = route[which]
            if end is None:
                continue
            if "instance_id" in end:
                located = drawing.locate(end["instance_id"])
                if "problem" not in located:
                    expand.update(located["containers"])
                ends[(route["route_id"], which)] = {"spec": end, **located}
            else:
                ends[(route["route_id"], which)] = {"spec": end, "point": [end["point"][0] / f, end["point"][1] / f]}
    rows = [row for row in drawing.inserts.values() if str(row.get("block_name") or "") in expand] + drawing.curves
    got = segment_graph.collect_segments(rows, drawing.definitions, drawing.origins, layer_pattern=req["route_re"],
                                         expand_blocks=sorted(expand), scope=req["bounds"], max_depth=req["max_depth"])
    graph = segment_graph.build_graph(got["segments"], snap_tolerance=req["snap"] / f, gap_tolerance=req["gap"] / f,
                                      gap_angle_deg=req["gap_angle_deg"])
    segs = {s["segment_id"]: s for s in graph["segments"]}
    nodes = {n["node_id"]: n for n in graph["nodes"]}
    comps = {c["component_id"]: c for c in graph["components"]}
    comp_of_node = {n: c["component_id"] for c in graph["components"] for n in c["node_ids"]}
    adjacency: dict[str, list[tuple[str, float, str, str]]] = defaultdict(list)
    for s in graph["segments"]:
        adjacency[s["start_node"]].append((s["end_node"], s["length"], "segment", s["segment_id"]))
        adjacency[s["end_node"]].append((s["start_node"], s["length"], "segment", s["segment_id"]))
    bridges = {g["bridge_id"]: g for g in graph["gap_bridged"]}
    for g in graph["gap_bridged"]:
        adjacency[g["from_node"]].append((g["to_node"], g["length"], "gap", g["bridge_id"]))
        adjacency[g["to_node"]].append((g["from_node"], g["length"], "gap", g["bridge_id"]))
    box_list = drawing.boxes(req["box_re"], expand)
    box_index = BoundsIndex(box_list) if box_list else None
    tol = req["endpoint"] / f
    m = lambda p: [p[0] * f, p[1] * f]   # noqa: E731  drawing units -> metres

    def shape(end: dict[str, Any]) -> tuple[str, list[float]]:
        if "point" in end:
            return "point", end["point"]
        if end.get("box"):
            return "box", end["box"]
        return "point", end["anchor"][:2]

    def attach(end: dict[str, Any]) -> tuple[dict[str, float], float | None]:
        kind, geo = shape(end)
        far = (lambda p: math.dist(p[:2], geo)) if kind == "point" else (lambda p: box_distance(p, geo))
        near, best = {}, None
        for node in graph["nodes"]:
            d = far(node["point"])
            best = d if best is None or d < best else best
            if d <= tol:
                near[node["node_id"]] = d
        return near, best

    def end_record(end: dict[str, Any], near: dict[str, float]) -> dict[str, Any]:
        spec = end["spec"]
        record: dict[str, Any] = dict(spec)
        if "point" in spec:
            record.update(basis="坐标", label=f"P({spec['point'][0]:g},{spec['point'][1]:g})")
        else:
            record["label"] = spec["instance_id"]
            if end.get("box"):
                record.update(basis="外框", box_m=[*m(end["box"][:2]), *m(end["box"][2:])],
                              extent_notes=cad_geometry.extent_note_labels(end["notes"]) if end.get("notes") else [])
            elif end.get("anchor") is not None:
                record.update(basis="插入点", point_m=m(end["anchor"]), note="外框取不到，只这一端按插入点挂接")
        if near:
            node_id = min(near, key=lambda n: (near[n], n))
            record.update(attach_node_ids=sorted(near), attach_distance_m=near[node_id] * f)
        return record

    def through_box(end: dict[str, Any]) -> list[str]:
        if not end.get("box"):
            return []
        return sorted(s["segment_id"] for s in graph["segments"]
                      if chord_hits_box(s["start"], s["end"], end["box"]))[:10]

    def shortest(sources: dict[str, float], targets: dict[str, float]) -> dict[str, Any]:
        eps = req["equal"] / f
        dist = {n: 0.0 for n in sources}
        count = {n: 1 for n in sources}
        pred: dict[str, list[tuple[str, str, str]]] = {n: [] for n in sources}
        heap = [(0.0, n) for n in sorted(sources)]
        done, best = set(), None
        while heap:
            d, u = heapq.heappop(heap)
            if u in done or d > dist[u] + 1e-12:
                continue
            if best is not None and d > best + eps:
                break
            done.add(u)
            if u in targets and (best is None or d < best):
                best = d
            for v, w, kind, edge_id in adjacency.get(u, ()):
                nd = d + w
                if v not in dist or nd < dist[v] - eps:
                    dist[v], count[v], pred[v] = nd, count[u], [(u, kind, edge_id)]
                    heapq.heappush(heap, (nd, v))
                elif abs(nd - dist[v]) <= eps and v not in done and (u, kind, edge_id) not in pred[v]:
                    count[v] += count[u]; pred[v].append((u, kind, edge_id))
        if best is None:
            return {"found": False}
        reached = [t for t in targets if t in dist and dist[t] <= best + eps]
        total = sum(count[t] for t in reached)
        target = min(reached, key=lambda t: (dist[t], t))
        steps, node = [], target
        while pred.get(node):
            u, kind, edge_id = pred[node][0]
            steps.append((u, node, kind, edge_id)); node = u
        return {"found": True, "count": total, "length": dist[target], "steps": steps[::-1], "start": node, "target": target}

    def nearest_gap(sources: dict[str, float], targets: dict[str, float]) -> float | None:
        a_comps = {comp_of_node[n] for n in sources}
        b_nodes = [nodes[n]["point"] for c in {comp_of_node[n] for n in targets} for n in comps[c]["node_ids"]]
        a_nodes = [nodes[n]["point"] for c in a_comps for n in (comps[c]["dangling_node_ids"] or comps[c]["node_ids"])]
        if not a_nodes or not b_nodes or len(a_nodes) * len(b_nodes) > 4_000_000:
            return None
        return min(math.dist(p[:2], q[:2]) for p in a_nodes for q in b_nodes) * f

    results, paths, used = [], [], defaultdict(list)
    for route in req["routes"]:
        rid = route["route_id"]
        out: dict[str, Any] = {"route_id": rid, "circuit_label": route["circuit_label"], "problems": [], "review_flags": []}
        near = {}
        for which in ("from", "to"):
            end = ends.get((rid, which))
            if end is None:
                continue
            if "problem" in end:
                out[which] = dict(end["spec"])
                out["problems"].append({"problem": f"{END_NAME[which]}{end['problem']}", "detail": end["detail"]})
                continue
            near[which], best = attach(end)
            out[which] = end_record(end, near[which])
            if out[which].get("basis") == "插入点":
                out["review_flags"].append({"flag": "端点按插入点", "end": END_NAME[which]})
            if not near[which]:
                item = {"problem": f"{END_NAME[which]}离走线超出容差", "nearest_m": None if best is None else best * f,
                        "tolerance_m": req["endpoint"]}
                if through_box(end):
                    item["segments_through_outline"] = through_box(end)
                out["problems"].append(item)
        if out["problems"]:
            out["status"] = "待办"; results.append(out); continue
        if route["to"] is None:
            members = sorted({comp_of_node[n] for n in near["from"]})
            out["components"] = [{"component_id": c, "segment_count": len(comps[c]["segment_ids"]), "length_m": comps[c]["length"] * f,
                                  "bridge_ids": comps[c]["bridge_ids"], "layers": comps[c]["layers"],
                                  "dangling_points": [m(nodes[n]["point"]) for n in comps[c]["dangling_node_ids"]],
                                  "segment_ids": comps[c]["segment_ids"]} for c in members]
            out["status"] = "分量"; results.append(out); continue
        if set(near["from"]) & set(near["to"]):
            out["problems"].append({"problem": "起终点挂在同一节点", "node_ids": sorted(set(near["from"]) & set(near["to"]))})
            out["status"] = "待办"; results.append(out); continue
        found = shortest(near["from"], near["to"])
        if not found["found"]:
            hints = [t for t in graph["t_contacts"] if comp_of_node.get(t["node_id"]) in
                     {comp_of_node[n] for n in [*near["from"], *near["to"]]}][:10]
            out["problems"].append({"problem": "网络断开", "nearest_gap_m": nearest_gap(near["from"], near["to"]),
                                    "t_contact_hints": hints})
            out["status"] = "待办"; results.append(out); continue
        if found["count"] > 1:
            out["problems"].append({"problem": "多条等长路径", "count": found["count"], "length_m": found["length"] * f})
            out["status"] = "待办"; results.append(out); continue
        pieces, edge_ids = [], []
        for u, v, kind, edge_id in found["steps"]:
            a, b = nodes[u]["point"], nodes[v]["point"]
            if kind == "segment":
                s = segs[edge_id]
                pieces.append({"kind": "segment", "segment_id": edge_id, "handle": s["handle"], "layer": s["layer"],
                               "entity_type": s["entity_type"], "length_m": s["length"] * f, "from_point": m(a), "to_point": m(b),
                               "drawing_from": a[:2], "drawing_to": b[:2], "crosses_scope_edge": s["crosses_scope_edge"]})
            else:
                pieces.append({"kind": "gap", "bridge_id": edge_id, "length_m": bridges[edge_id]["length"] * f,
                               "from_point": m(a), "to_point": m(b), "drawing_from": a[:2], "drawing_to": b[:2]})
            edge_ids.append(edge_id)
        over_edge = [p["segment_id"] for p in pieces if p["kind"] == "segment" and p["crosses_scope_edge"]]
        if over_edge:
            out["problems"].append({"problem": "路径经过范围边", "segment_ids": over_edge,
                                    "detail": "跨范围边的段不能整段计入，缩小或放大范围，或把这段单列"})
            out["status"] = "待办"; results.append(out); continue
        # the boxes at the two ends are the route's own: the requested instances, and any box the end nodes touch
        own = {route["from"].get("instance_id"), route["to"].get("instance_id")} - {None}
        passed = set()
        if box_index is not None:
            for node_id in (found["start"], found["target"]):
                p = nodes[node_id]["point"]
                for key in box_index.query_box([p[0] - tol, p[1] - tol, p[0] + tol, p[1] + tol]):
                    if box_distance(p, list(box_index.boxes[key])) <= tol:
                        own.add(key)
            for p in pieces:
                a, b = p["drawing_from"], p["drawing_to"]
                for key in box_index.query_box([min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]):
                    if key not in own and chord_hits_box(a, b, box_index.boxes[key]):
                        passed.add(key)
        if passed:
            out["review_flags"].append({"flag": "经过别的箱", "instance_ids": sorted(passed),
                                        "detail": "回路可能在那只箱断开或串接，核对系统图"})
        comp = comps[comp_of_node[found["start"]]]
        if len(comp["segment_ids"]) + len(comp["bridge_ids"]) >= len(comp["node_ids"]):
            out["review_flags"].append({"flag": "走线网络有环，取最短", "component_id": comp["component_id"]})
        drawn = math.fsum(p["length_m"] for p in pieces if p["kind"] == "segment")
        gaps = math.fsum(p["length_m"] for p in pieces if p["kind"] == "gap")
        out.update(status="已追踪", path_id=f"DRAWN-{rid}", drawn_length_m=drawn, bridged_gap_length_m=gaps,
                   length_m=drawn + gaps, segments=[{k: v for k, v in p.items() if not k.startswith("drawing_")}
                                                    for p in pieces if p["kind"] == "segment"],
                   bridged_gaps=[{k: v for k, v in p.items() if not k.startswith("drawing_")} for p in pieces if p["kind"] == "gap"],
                   _pieces=pieces)
        for p in pieces:
            if p["kind"] == "segment":
                used[p["segment_id"]].append(rid)
        results.append(out)
    shared = [{"segment_id": sid, "handle": segs[sid]["handle"], "route_ids": sorted(set(rids))}
              for sid, rids in sorted(used.items()) if len(set(rids)) > 1]
    by_route = defaultdict(list)
    for row in shared:
        for rid in row["route_ids"]:
            by_route[rid].append(row)
    prefix = f"{req['drawing_id']}:" if req["drawing_id"] else ""
    for out in results:
        if by_route.get(out["route_id"]):
            rows = by_route[out["route_id"]]
            out["review_flags"].append({"flag": "与别的回路共用走线段", "segment_ids": [r["segment_id"] for r in rows],
                                        "with_route_ids": sorted({x for r in rows for x in r["route_ids"]} - {out["route_id"]})})
        if out["status"] == "已追踪":
            handles = list(dict.fromkeys(s["handle"] for s in out["segments"]))
            paths.append({"id": out["path_id"], "route_id": out["route_id"], "from_node": out["from"]["label"],
                          "to_node": out["to"]["label"], "length_m": out["length_m"], "role": "horizontal",
                          "length_basis": "actual_drawn_path", "source_ids": [prefix + s["segment_id"] for s in out["segments"]],
                          "source_handles": handles, "drawn_length_m": out["drawn_length_m"],
                          "bridged_gap_length_m": out["bridged_gap_length_m"], "circuit_label": out["circuit_label"],
                          "review_flags": [f["flag"] for f in out["review_flags"]]})
    return {"graph": graph, "collected": got, "routes": results, "paths": paths, "shared": shared, "box_count": len(box_list),
            "expanded": sorted(expand)}


def write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="沿已画走线追踪箱到设备的路径（逐段句柄与长度）")
    parser.add_argument("--cad-manifest", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        req = parse_request(load_json(args.request))
        drawing = Drawing(args.cad_manifest.resolve(), req)
        traced = trace_routes(drawing, req)
        graph, got = traced["graph"], traced["collected"]
        todo = [r for r in traced["routes"] if r["status"] == "待办"]
        result = {
            "schema": RESULT_SCHEMA, "version": VERSION, "status": "REVIEW_REQUIRED" if todo else "REFERENCE_ONLY",
            "inputs": {"cad_manifest": str(args.cad_manifest.resolve()), "cad_manifest_sha256": sha256(args.cad_manifest),
                       "source_sha256": (drawing.manifest.get("authoritative_source") or {}).get("sha256"),
                       "request": str(args.request.resolve()), "request_sha256": sha256(args.request)},
            "parameters": {"layout": req["layout"], "route_layers": req["route_re"].pattern, "factor_to_output": req["factor"],
                           "unit_source_id": req["unit_source_id"], "scope_bounds": req["bounds"],
                           "tolerances_m": {"snap": req["snap"], "gap": req["gap"], "endpoint": req["endpoint"], "equal_length": req["equal"]},
                           "gap_angle_deg": req["gap_angle_deg"], "graph_parameters_drawing_units": graph["parameters"],
                           "expanded_blocks": traced["expanded"],
                           "box_block_pattern": req["box_re"].pattern if req["box_re"] else None, "box_instances": traced["box_count"]},
            "graph": {"counts": graph["counts"], "degenerate_parts": got["degenerate_parts"], "geometry_problems": got["geometry_problems"],
                      "unsupported": got["unsupported"], "expansion_problems": got["expansion_problems"],
                      "out_of_scope_parts": got["out_of_scope_parts"], "ambiguous_gaps": graph["ambiguous_gaps"],
                      "refused_snaps": graph["refused_snaps"]},
            "routes": [{k: v for k, v in r.items() if k != "_pieces"} for r in traced["routes"]],
            "shared_segments": traced["shared"],
            "counts": {"routes": len(traced["routes"]), "traced": sum(r["status"] == "已追踪" for r in traced["routes"]),
                       "components": sum(r["status"] == "分量" for r in traced["routes"]), "todo": len(todo)},
            "meaning": "沿已画走线逐段量出的水平长度（含桥接缺口）；回路归属、系统图与平面名称对应由模型判定并列依据；待办不按直角距离补",
        }
        lines, todo_rows = [], []
        for r in traced["routes"]:
            for p in r.get("_pieces", ()):
                a, b = p["drawing_from"], p["drawing_to"]
                lines.append([r["route_id"], req["drawing_id"], p["kind"], p.get("segment_id") or p.get("bridge_id"), p.get("handle", ""),
                              p.get("layer", ""), a[0], a[1], b[0], b[1], p["length_m"]])
            for item in r["problems"]:
                todo_rows.append([r["route_id"], "待办", item["problem"],
                                  json.dumps({k: v for k, v in item.items() if k != "problem"}, ensure_ascii=False)])
            if r["status"] == "已追踪":
                for flag in r["review_flags"]:
                    todo_rows.append([r["route_id"], "复核", flag["flag"],
                                      json.dumps({k: v for k, v in flag.items() if k != "flag"}, ensure_ascii=False)])
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "走线追踪结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        (args.output_dir / "走线追踪路径.json").write_text(json.dumps(
            {"schema": PATHS_SCHEMA, "unit": "m", "drawing_id": req["drawing_id"], "paths": traced["paths"]},
            ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        write_csv(args.output_dir / "走线追踪映射线.csv",
                  ["route_id", "drawing", "kind", "segment_id", "handle", "layer", "x1", "y1", "x2", "y2", "length_m"], lines)
        write_csv(args.output_dir / "走线追踪待办.csv", ["route_id", "kind", "problem", "detail"], todo_rows)
        print(json.dumps({"status": result["status"], **result["counts"], "output_dir": str(args.output_dir)}, ensure_ascii=False))
        return 3 if todo else 0
    except (InputError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
