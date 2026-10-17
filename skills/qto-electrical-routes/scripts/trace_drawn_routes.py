#!/usr/bin/env python3
"""Trace drawn electrical routes on the shared segment graph (第五批 L1; registration 20261016-1056 expectations 1, 3).

From a box to a device (a point-adapter instance id, or a coordinate) along the lines of the route layers: segment by
segment with handles and lengths, gaps behind symbols bridged and listed. Ends off the network, breaks, equal-length
alternatives and paths over the scope edge become to-dos; nothing falls back to a straight or right-angle distance.
A circuit label in the request is kept as evidence only: the tool never decides which circuit a line belongs to, and
it never matches device names (a system-diagram name against a plan name is the model's call, with its basis listed).
A T junction a route turns at is never joined silently (checkpoint 2 decision): each one lists both lines, the point
and the trunk part run; with a circuit label pattern in the request, the drawn labels near the route are compared
across its T junctions and different labels on the two sides are a review flag, and without one the route is flagged
as unchecked.
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
import build_review_scenes_from_tables as sheet_tables  # noqa: E402  (sheet table rows, as the coverage ledger reads them)
import qto_cad_geometry as cad_geometry  # noqa: E402
import qto_segment_graph as segment_graph  # noqa: E402
from qto_spatial import BoundsIndex  # noqa: E402

VERSION = "qto-drawn-route-tracer/1.3.1"
REQUEST_SCHEMA = "qto-drawn-route-request/1.0"
RESULT_SCHEMA = "qto-drawn-route-trace/1.0"
PATHS_SCHEMA = "qto-drawn-route-paths/1.0"
LAYER_REQUEST_SCHEMA = "qto-layer-length-request/1.0"
LAYER_RESULT_SCHEMA = "qto-layer-length/1.0"
LEGEND_SHEET_KIND = "图例"
EXCLUDABLE_SHEET_KINDS = (LEGEND_SHEET_KIND, "系统图")
UNSHEETED, UNREGIONED = "图页未定", "区域未定"
OVERLAP_ACTIONS = ("drop_overlap", "keep_both", "pending")
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


def parse_common(request: dict[str, Any], schema: str) -> dict[str, Any]:
    """What both requests share: unit, scope, route layers, expanded blocks and the segment graph tolerances."""
    if request.get("schema") != schema:
        fail(f"request.schema 必须为 {schema}")
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
    max_depth = int(request.get("max_depth", 16))
    if max_depth < 1:
        fail("max_depth 必须大于0")
    t_junctions = request.get("t_junctions", True)
    if not isinstance(t_junctions, bool):
        fail("t_junctions 须为 true 或 false")
    snap = tolerance(request, "snap_tolerance", 0.005)
    return {
        "layout": text(request.get("layout", "Model"), "layout"), "factor": factor,
        "unit_source_id": text(unit.get("source_id"), "unit.source_id"), "bounds": bounds,
        "route_re": pattern(request.get("route_layers"), "route_layers"),
        "expand": set(expand), "max_depth": max_depth, "t_junctions": t_junctions,
        "snap": snap, "gap": tolerance(request, "gap_tolerance", 0.0),
        "gap_angle_deg": tolerance(request, "gap_angle_deg", 10.0),
        "drawing_id": str(request.get("drawing_id") or "").strip(),
        "label_re": None, "label_layer_re": None, "label_radius": None,
        "overlaps": parse_overlaps(request, snap),
    }


def parse_overlaps(request: dict[str, Any], snap: float) -> dict[str, Any]:
    """L4: collinear overlap check (always on) and the project's one disposition per candidate pair."""
    raw = request.get("overlaps") or {}
    if not isinstance(raw, dict):
        fail("overlaps 必须是对象")
    angle = tolerance(raw, "angle_tolerance_deg", 0.5)
    distance = tolerance(raw, "distance_tolerance", snap)
    minimum = tolerance(raw, "min_overlap", 0.01)
    groups = raw.get("layer_groups") or []
    if not isinstance(groups, list):
        fail("overlaps.layer_groups 须为图层正则数组（同一导体身份的几个图层写成一个正则）")
    resolutions, seen = [], set()
    items = request.get("overlap_resolutions") or []
    if not isinstance(items, list):
        fail("overlap_resolutions 须为数组")
    for index, item in enumerate(items):
        where = f"overlap_resolutions[{index}]"
        ids = item.get("segment_ids") if isinstance(item, dict) else None
        if not isinstance(ids, list) or len(ids) != 2 or len({str(x) for x in ids}) != 2:
            fail(f"{where}.segment_ids 须为两个不同的段号（取自重合候选表）")
        pair = tuple(sorted(str(x) for x in ids))
        if pair in seen:
            fail(f"{where}：段对 {pair[0]}、{pair[1]} 已有一条处置，一组只许一条")
        seen.add(pair)
        action = item.get("action")
        if action not in OVERLAP_ACTIONS:
            fail(f"{where}.action 只能是 " + "、".join(OVERLAP_ACTIONS))
        drop = str(item.get("drop") or "")
        if action == "drop_overlap" and drop not in pair:
            fail(f"{where}.drop 须为这一对里要扣掉重合区间的那一段")
        resolutions.append({"pair": pair, "action": action, "drop": drop if action == "drop_overlap" else None,
                            "basis": text(item.get("basis"), f"{where}.basis")})
    return {"angle_deg": angle, "distance": distance, "min": minimum, "resolutions": resolutions,
            "groups": [pattern(g, f"overlaps.layer_groups[{i}]") for i, g in enumerate(groups)]}


def parse_request(request: dict[str, Any]) -> dict[str, Any]:
    common = parse_common(request, REQUEST_SCHEMA)
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
    label_re = pattern(request["circuit_label_pattern"], "circuit_label_pattern") if request.get("circuit_label_pattern") else None
    if label_re is None and (request.get("circuit_label_layers") or request.get("circuit_label_radius") is not None):
        fail("写了 circuit_label_layers 或 circuit_label_radius 就要写 circuit_label_pattern（回路标注文字的正则）")
    return {
        **common, "label_re": label_re,
        "label_layer_re": pattern(request["circuit_label_layers"], "circuit_label_layers") if request.get("circuit_label_layers") else None,
        "label_radius": tolerance(request, "circuit_label_radius") if label_re else None,
        "box_re": pattern(request["box_block_pattern"], "box_block_pattern") if request.get("box_block_pattern") else None,
        "endpoint": tolerance(request, "endpoint_tolerance"), "equal": tolerance(request, "equal_length_tolerance", 0.001),
        "routes": parsed,
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
        self.texts: list[dict[str, Any]] = []
        self.circles: list[dict[str, Any]] = []
        types =segment_graph.CURVE_TYPES | segment_graph.LEADER_TYPES | segment_graph.UNSUPPORTED_LEADERS
        for row in load_stream(layout_path):
            if row.get("row_type") != "entity" or row.get("layout") != req["layout"]:
                continue
            kind = str(row.get("entity_type") or "").upper()
            if kind == "INSERT":
                self.inserts[str(row.get("entity_handle") or "").upper()] = row
            elif kind in types:
                self.curves.append(row)
            elif kind in ("TEXT", "MTEXT") and req["label_re"] is not None:
                self.texts.append(row)
            elif kind == "CIRCLE":                  # building markers of the layer-length mode (not route lines)
                self.circles.append(row)

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

    def label_texts(self, expand: set[str]) -> list[dict[str, Any]]:
        """Circuit label texts: layout texts and the texts inside the expanded blocks (walked and scoped as boxes()),
        in scope by their insertion point; the label is the part of the words the request pattern matches."""
        label_re, layer_re, found = self.req["label_re"], self.req["label_layer_re"], []

        def keep(row: dict[str, Any], matrix: tuple[float, ...], path: str) -> None:
            if str(row.get("entity_type") or "").upper() not in ("TEXT", "MTEXT"):
                return
            words = str(row.get("text") or "").strip()
            hit = label_re.search(words) if words else None
            if hit is None or (layer_re is not None and not layer_re.search(str(row.get("layer") or ""))):
                return
            point = cad_geometry.transform_point([finite(row.get(axis, 0.0), f"{row.get('record_id')}.{axis}")
                                                  for axis in ("x", "y", "z")], matrix)
            if point is None or not cad_geometry.point_in_scope(point, self.req["bounds"]):
                return
            found.append({"label": hit.group(0), "text": words[:80], "layer": str(row.get("layer") or ""),
                          "handle": f"{path}{row.get('entity_handle') or ''}", "point": point[:2]})
        for row in self.texts:
            keep(row, IDENTITY, "")

        def walk(insert, parent, path, stack):
            here = self.scope_of(insert, parent)
            name = str(insert.get("block_name") or "")
            if not here["in_scope"] or name not in expand or name in stack or len(stack) >= self.req["max_depth"]:
                return
            for child in self.definitions.get(name, ()):
                if str(child.get("entity_type") or "").upper() == "INSERT":
                    walk(child, here["matrix"], f"{path}/{name}/CAD-{child.get('entity_handle')}", (*stack, name))
                else:
                    keep(child, here["matrix"], f"{path}/{name}/")
        if expand:
            for insert in self.inserts.values():
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
    label_items = drawing.label_texts(expand) if req["label_re"] is not None else []
    got = segment_graph.collect_segments(rows, drawing.definitions, drawing.origins, layer_pattern=req["route_re"],
                                         expand_blocks=sorted(expand), scope=req["bounds"], max_depth=req["max_depth"])
    flat, got["plan"] = flatten_elevations(got["segments"], f, req["snap"] / f)
    # L4: a segment the project drops whose whole length lies on its partner is left out of the graph; a partial drop
    # stays and only the part a route walks on both lines is taken off that route
    ov_found, ov_apart = find_overlaps(flat, req)
    ov_cut, ov_conflicts = resolve_overlaps(ov_found, ov_apart, req, flat)
    reach = req["overlaps"]["distance"] / f
    left_out = {s["segment_id"] for s in flat if s["segment_id"] in ov_cut
                and (1.0 - union_length(ov_cut[s["segment_id"]])) * s["length"] <= reach}
    flat = [s for s in flat if s["segment_id"] not in left_out]
    graph = segment_graph.build_graph(flat, snap_tolerance=req["snap"] / f, gap_tolerance=req["gap"] / f,
                                      gap_angle_deg=req["gap_angle_deg"])
    segs = {s["segment_id"]: s for s in graph["segments"]}
    point_of = {n["node_id"]: n["point"] for n in graph["nodes"]}
    ov_walk = [r for r in ov_found if not set(r["segment_ids"]) & left_out and r["status"] in ("已扣重合", "未处置", "暂缓", "处置冲突")]

    # A free end lying on the middle of another straight line (an L0 T contact) is a branch of the drawn run
    # (electrical rule; request t_junctions, default on): that line is cut there and only the part run is counted.
    used_t, hint_t = [], []
    cuts: dict[str, dict[float, str]] = defaultdict(dict)
    for t in graph["t_contacts"]:
        host = segs[t["on_segment_id"]]
        if not req["t_junctions"] or host["kind"] != "line":
            hint_t.append(t)
            continue
        key = round(t["fraction"], 9)
        vid = cuts[host["segment_id"]].get(key)
        if vid is None:
            vid = cuts[host["segment_id"]][key] = f"T-{sum(len(v) for v in cuts.values()) + 1:05d}"
            a, b = host["start"], host["end"]
            point_of[vid] = [a[k] + key * (b[k] - a[k]) for k in range(3)]
        used_t.append({**t, "virtual_node_id": vid})
    edges: list[dict[str, Any]] = []
    adjacency: dict[str, list[tuple[str, float, int]]] = defaultdict(list)

    def add(a: str, b: str, weight: float, edge: dict[str, Any]) -> None:
        edges.append({**edge, "nodes": (a, b), "weight": weight})
        adjacency[a].append((b, weight, len(edges) - 1)); adjacency[b].append((a, weight, len(edges) - 1))
    for s in graph["segments"]:
        chain = [(0.0, s["start_node"]), *sorted(cuts.get(s["segment_id"], {}).items()), (1.0, s["end_node"])]
        for (fa, na), (fb, nb) in zip(chain, chain[1:]):
            add(na, nb, (fb - fa) * s["length"], {"kind": "segment", "segment_id": s["segment_id"], "f": (fa, fb)})
    bridges = {g["bridge_id"]: g for g in graph["gap_bridged"]}
    for g in graph["gap_bridged"]:
        add(g["from_node"], g["to_node"], g["length"], {"kind": "gap", "bridge_id": g["bridge_id"]})
    for t in used_t:
        add(t["node_id"], t["virtual_node_id"], t["distance"], {"kind": "t", "t": t})
    # connected parts of this network (segments, bridges and T junctions)
    link = {n: n for n in point_of}

    def find(x: str) -> str:
        while link[x] != x:
            link[x] = link[link[x]]; x = link[x]
        return x
    for e in edges:
        ra, rb = find(e["nodes"][0]), find(e["nodes"][1])
        if ra != rb:
            link[max(ra, rb)] = min(ra, rb)
    part_nodes: dict[str, list[str]] = defaultdict(list)
    for n in point_of:
        part_nodes[find(n)].append(n)
    part_edges: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(edges):
        part_edges[find(e["nodes"][0])].append(i)
    degree: dict[str, int] = defaultdict(int)
    for e in edges:
        degree[e["nodes"][0]] += 1; degree[e["nodes"][1]] += 1
    real_nodes = graph["nodes"]

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

    real_ids = {node["node_id"] for node in real_nodes}

    def attach(end: dict[str, Any]) -> tuple[dict[str, float], float | None, dict[str, float]]:
        """Line ends (real nodes) within the endpoint tolerance of the outline (0 inside it) or of the point.

        Lines drawn into that band are part of the route: each piece of the network inside the band attaches only at
        its node closest to the end (a free line end first on a tie), so the shortest way cannot start further in.
        A box feeds several circuits, so every piece in its band attaches; a device or a point takes only the closest
        piece(s), and the line of a neighbouring device that falls into the band is left out (returned as dropped)."""
        kind, geo = shape(end)
        far = (lambda p: math.dist(p[:2], geo)) if kind == "point" else (lambda p: box_distance(p, geo))
        best = min((far(node["point"]) for node in real_nodes), default=None)
        band = {n: d for n, p in point_of.items() if (d := far(p)) <= tol}
        pieces: list[tuple[float, dict[str, float]]] = []
        seen: set[str] = set()
        for first in sorted(band):
            if first in seen:
                continue
            piece, stack = [], [first]
            seen.add(first)
            while stack:
                u = stack.pop(); piece.append(u)
                for v, _, _ in adjacency.get(u, ()):
                    if v in band and v not in seen:
                        seen.add(v); stack.append(v)
            ends_here = [n for n in piece if n in real_ids]
            if not ends_here:
                continue
            low = min(band[n] for n in ends_here)
            closest = [n for n in ends_here if band[n] <= low + 1e-9]
            pieces.append((low, {n: band[n] for n in ([n for n in closest if degree[n] == 1] or closest)}))
        name = str(end.get("insert", {}).get("block_name") or "")
        feeds_many = req["box_re"] is not None and "spec" in end and "instance_id" in end["spec"] and bool(req["box_re"].search(name))
        lowest = min((low for low, _ in pieces), default=0.0)
        near, dropped = {}, {}
        for low, nodes in pieces:
            (near if feeds_many or low <= lowest + 1e-9 else dropped).update(nodes)
        return near, best, dropped

    def end_record(end: dict[str, Any], near: dict[str, float], dropped: dict[str, float]) -> dict[str, Any]:
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
        if dropped:
            record["other_line_ends_in_band"] = [{"node_id": n, "distance_m": d * f, "point_m": m(point_of[n])}
                                                 for n, d in sorted(dropped.items(), key=lambda x: (x[1], x[0]))]
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
        pred: dict[str, list[tuple[str, int]]] = {n: [] for n in sources}
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
            for v, w, i in adjacency.get(u, ()):
                nd = d + w
                if v not in dist or nd < dist[v] - eps:
                    dist[v], count[v], pred[v] = nd, count[u], [(u, i)]
                    heapq.heappush(heap, (nd, v))
                elif abs(nd - dist[v]) <= eps and v not in done and (u, i) not in pred[v]:
                    count[v] += count[u]; pred[v].append((u, i))
        if best is None:
            return {"found": False}
        reached = [t for t in targets if t in dist and dist[t] <= best + eps]
        target = min(reached, key=lambda t: (dist[t], t))
        steps, node = [], target
        while pred.get(node):
            u, i = pred[node][0]
            steps.append((u, node, i)); node = u
        return {"found": True, "count": sum(count[t] for t in reached), "length": dist[target], "steps": steps[::-1],
                "start": node, "target": target}

    def nearest_gap(sources: dict[str, float], targets: dict[str, float]) -> float | None:
        a_parts, b_parts = {find(n) for n in sources}, {find(n) for n in targets}
        a_nodes = [point_of[n] for c in a_parts for n in ([x for x in part_nodes[c] if degree[x] == 1] or part_nodes[c])]
        b_nodes = [point_of[n] for c in b_parts for n in part_nodes[c]]
        if not a_nodes or not b_nodes or len(a_nodes) * len(b_nodes) > 4_000_000:
            return None
        return min(math.dist(p[:2], q[:2]) for p in a_nodes for q in b_nodes) * f

    def pieces_of(steps: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
        """Walked edges in order; consecutive parts of one line join into one piece (fraction_range when partial)."""
        out: list[dict[str, Any]] = []
        for u, v, i in steps:
            e = edges[i]
            if e["kind"] == "segment":
                fa, fb = e["f"] if e["nodes"][0] == u else e["f"][::-1]
                last = out[-1] if out else None
                if last and last["kind"] == "segment" and last["segment_id"] == e["segment_id"] and abs(last["_f"][1] - fa) < 1e-12:
                    last["_f"] = (last["_f"][0], fb); last["_to"] = v
                    continue
                out.append({"kind": "segment", "segment_id": e["segment_id"], "_f": (fa, fb), "_from": u, "_to": v})
            elif e["kind"] == "gap":
                out.append({"kind": "gap", "bridge_id": e["bridge_id"], "_from": u, "_to": v, "length": e["weight"]})
            else:
                out.append({"kind": "t", "t": e["t"], "_from": u, "_to": v})
        result = []
        for p in out:
            a, b = point_of[p["_from"]], point_of[p["_to"]]
            if p["kind"] == "segment":
                s = segs[p["segment_id"]]
                lo, hi = sorted(p["_f"])
                row = {"kind": "segment", "segment_id": p["segment_id"], "handle": s["handle"], "layer": s["layer"],
                       "entity_type": s["entity_type"], "length_m": (hi - lo) * s["length"] * f, "from_point": m(a), "to_point": m(b),
                       "crosses_scope_edge": s["crosses_scope_edge"], "_range": (lo, hi)}
                if (lo, hi) != (0.0, 1.0):
                    row["fraction_range"] = [lo, hi]
            elif p["kind"] == "gap":
                row = {"kind": "gap", "bridge_id": p["bridge_id"], "length_m": p["length"] * f, "from_point": m(a), "to_point": m(b)}
            else:
                t = p["t"]
                row = {"kind": "t", "free_end_segment_id": t["segment_id"], "free_end_handle": segs[t["segment_id"]]["handle"],
                       "on_segment_id": t["on_segment_id"], "on_handle": segs[t["on_segment_id"]]["handle"],
                       "fraction": t["fraction"], "distance_m": t["distance"] * f, "point": m(point_of[t["virtual_node_id"]])}
            row["drawing_from"], row["drawing_to"] = a[:2], b[:2]
            result.append(row)
        for k, row in enumerate(result):              # the trunk part run next to each T junction
            if row["kind"] == "t":
                host = [q for q in result[max(k - 1, 0):k + 2] if q["kind"] == "segment" and q["segment_id"] == row["on_segment_id"]]
                if host:
                    row["on_walked_fraction_range"] = list(host[0]["_range"])
        return result

    def piece_distance(point: list[float], p: dict[str, Any]) -> float:
        """Plan distance (drawing units) from a point to a walked segment piece: its curve, or the run part of a cut line."""
        s = segs[p["segment_id"]]
        if s["kind"] != "line" and p["_range"] == (0.0, 1.0):
            return segment_graph._project([point[0], point[1], s["start"][2]], s["part"])[0]
        (ax, ay), (bx, by) = p["drawing_from"], p["drawing_to"]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / den)) if den else 0.0
        return math.hypot(point[0] - ax - t * dx, point[1] - ay - t * dy)

    def check_tees(out: dict[str, Any], pieces: list[dict[str, Any]]) -> None:
        """Checkpoint 2 decision 1: a T junction is not joined silently. The route is cut into parts at its T junctions;
        each label text within the radius belongs to the part(s) nearest to it; two labelled parts in a row (an
        unlabelled part between them is skipped) with no label in common are a review flag."""
        tees = [p for p in pieces if p["kind"] == "t"]
        if not tees:
            return
        if req["label_re"] is None:
            out["review_flags"].append({"flag": "T接未核回路标注", "t_count": len(tees),
                                        "detail": "请求没写 circuit_label_pattern，T 接两侧是不是同一回路没核"})
            return
        parts: list[list[dict[str, Any]]] = [[]]
        for p in pieces:
            if p["kind"] == "t":
                parts.append([])
            elif p["kind"] == "segment":
                parts[-1].append(p)
        r = req["label_radius"] / f
        boxes = [segs[p["segment_id"]]["bbox3d"] for p in pieces if p["kind"] == "segment"]
        x0, y0 = min(b[0] for b in boxes) - r, min(b[1] for b in boxes) - r
        x1, y1 = max(b[3] for b in boxes) + r, max(b[4] for b in boxes) + r
        found: list[dict[str, tuple[float, dict[str, Any]]]] = [{} for _ in parts]
        for item in label_items:
            x, y = item["point"]
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            d = [min((piece_distance(item["point"], p) for p in part), default=math.inf) for part in parts]
            low = min(d)
            if low > r:
                continue
            for i, v in enumerate(d):
                if v <= low + 1e-9 and (item["label"] not in found[i] or v < found[i][item["label"]][0]):
                    found[i][item["label"]] = (v, item)
        for k, row in enumerate(tees):
            row["circuit_labels_before"], row["circuit_labels_after"] = sorted(found[k]), sorted(found[k + 1])
        out["circuit_labels_by_part"] = [[{"label": label, "text": item["text"], "layer": item["layer"], "handle": item["handle"],
                                           "distance_m": v * f} for label, (v, item) in sorted(part.items())] for part in found]
        changes, last = [], None
        for i, labels in enumerate(found):
            if not labels:
                continue
            if last is not None and not set(labels) & set(found[last]):
                between = tees[last:i]
                changes.append({"labels_before": sorted(found[last]), "labels_after": sorted(labels),
                                "t_on_handles": [t["on_handle"] for t in between], "t_points": [t["point"] for t in between]})
            last = i
        if changes:
            out["review_flags"].append({"flag": "T接两侧回路标注不同", "changes": changes,
                                        "detail": "T 接可能连到了别的回路：核对平面标注和系统图，确认后再用这段长度"})

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
            near[which], best, dropped = attach(end)
            out[which] = end_record(end, near[which], dropped)
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
            members = sorted({find(n) for n in near["from"]})
            out["components"] = []
            for c in members:
                seg_edges = [edges[i] for i in part_edges[c] if edges[i]["kind"] == "segment"]
                ids = sorted({e["segment_id"] for e in seg_edges})
                out["components"].append({
                    "segment_count": len(ids), "length_m": math.fsum(e["weight"] for e in seg_edges) * f,
                    "bridge_ids": sorted(edges[i]["bridge_id"] for i in part_edges[c] if edges[i]["kind"] == "gap"),
                    "t_junction_count": sum(edges[i]["kind"] == "t" for i in part_edges[c]),
                    "layers": sorted({segs[s]["layer"] for s in ids}),
                    "dangling_points": [m(point_of[n]) for n in sorted(part_nodes[c]) if degree[n] == 1], "segment_ids": ids})
            out["status"] = "分量"; results.append(out); continue
        if set(near["from"]) & set(near["to"]):
            out["problems"].append({"problem": "起终点挂在同一节点", "node_ids": sorted(set(near["from"]) & set(near["to"]))})
            out["status"] = "待办"; results.append(out); continue
        found = shortest(near["from"], near["to"])
        if not found["found"]:
            involved = {find(n) for n in [*near["from"], *near["to"]]}
            out["problems"].append({"problem": "网络断开", "nearest_gap_m": nearest_gap(near["from"], near["to"]),
                                    "t_contact_hints": [t for t in hint_t if find(t["node_id"]) in involved][:10]})
            out["status"] = "待办"; results.append(out); continue
        if found["count"] > 1:
            out["problems"].append({"problem": "多条等长路径", "count": found["count"], "length_m": found["length"] * f})
            out["status"] = "待办"; results.append(out); continue
        for which, node_id in (("from", found["start"]), ("to", found["target"])):
            out[which].update(attach_node_id=node_id, attach_distance_m=near[which][node_id] * f)
        pieces = pieces_of(found["steps"])
        over_edge =[p["segment_id"] for p in pieces if p["kind"] == "segment" and p["crosses_scope_edge"]]
        if over_edge:
            out["problems"].append({"problem": "路径经过范围边", "segment_ids": over_edge,
                                    "detail": "跨范围边的段不能整段计入，缩小或放大范围，或把这段单列"})
            out["status"] = "待办"; results.append(out); continue
        # the boxes at the two ends are the route's own: the requested instances, and any box the end nodes touch
        own = {route["from"].get("instance_id"), route["to"].get("instance_id")} - {None}
        passed = set()
        if box_index is not None:
            for node_id in (found["start"], found["target"]):
                p = point_of[node_id]
                for key in box_index.query_box([p[0] - tol, p[1] - tol, p[0] + tol, p[1] + tol]):
                    if box_distance(p, list(box_index.boxes[key])) <= tol:
                        own.add(key)
            for p in pieces:
                if p["kind"] == "t":
                    continue
                a, b = p["drawing_from"], p["drawing_to"]
                for key in box_index.query_box([min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]):
                    if key not in own and chord_hits_box(a, b, box_index.boxes[key]):
                        passed.add(key)
        if passed:
            out["review_flags"].append({"flag": "经过别的箱", "instance_ids": sorted(passed),
                                        "detail": "回路可能在那只箱断开或串接，核对系统图"})
        start_part = find(found["start"])
        if len(part_edges[start_part]) >= len(part_nodes[start_part]):
            out["review_flags"].append({"flag": "走线网络有环，取最短"})
        check_tees(out, pieces)
        slopes = [x for x in got["plan"]["sloped_lines_in_plan"] if x["segment_id"] in {p.get("segment_id") for p in pieces}]
        if slopes:
            out["review_flags"].append({"flag": "斜线按平面投影计长", "handles": [x["handle"] for x in slopes],
                                        "length_3d_minus_plan_m": math.fsum(x["length_3d_m"] - x["length_plan_m"] for x in slopes)})
        walked: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for p in pieces:
            if p["kind"] == "segment":
                walked[p["segment_id"]].append(p["_range"])
        twice = [{"group_id": r["group_id"], "segment_ids": r["segment_ids"], "status": r["status"],
                  "walked_twice_m": walked_overlap(segs[r["segment_ids"][0]], segs[r["segment_ids"][1]],
                                                   walked[r["segment_ids"][0]], walked[r["segment_ids"][1]]) * f}
                 for r in ov_walk if all(x in walked for x in r["segment_ids"])]
        twice = [x for x in twice if x["walked_twice_m"] > 1e-9]
        taken = math.fsum(x["walked_twice_m"] for x in twice if x["status"] == "已扣重合")
        if any(x["status"] != "已扣重合" for x in twice):
            open_twice = [x for x in twice if x["status"] != "已扣重合"]
            out["review_flags"].append({"flag": "经过未处置的共线重合", "group_ids": [x["group_id"] for x in open_twice],
                                        "walked_twice_m": math.fsum(x["walked_twice_m"] for x in open_twice),
                                        "detail": "路线在同层共线重合处两段都走过，重合部分计了两次：核图后写 overlap_resolutions"})
        drawn = math.fsum(p["length_m"] for p in pieces if p["kind"] == "segment") - taken
        gaps = math.fsum(p["length_m"] for p in pieces if p["kind"] == "gap")
        clean = lambda p: {k: v for k, v in p.items() if not k.startswith("drawing_") and not k.startswith("_")}   # noqa: E731
        if twice:
            out["overlaps_walked_twice"] = twice
        out.update(status="已追踪", path_id=f"DRAWN-{rid}", drawn_length_m=drawn, bridged_gap_length_m=gaps, overlap_deducted_m=taken,
                   length_m=drawn + gaps, segments=[clean(p) for p in pieces if p["kind"] == "segment"],
                   bridged_gaps=[clean(p) for p in pieces if p["kind"] == "gap"],
                   t_junctions=[clean(p) for p in pieces if p["kind"] == "t"], _pieces=pieces)
        for p in pieces:
            if p["kind"] == "segment":
                used[p["segment_id"]].append((rid, *p["_range"]))
        results.append(out)
    shared = []
    for sid, uses in sorted(used.items()):
        overlapping = {a[0] for a in uses for b in uses if a[0] != b[0] and min(a[2], b[2]) - max(a[1], b[1]) > 1e-9}
        if overlapping:
            shared.append({"segment_id": sid, "handle": segs[sid]["handle"], "route_ids": sorted(overlapping)})
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
                          "length_basis": "actual_drawn_path", "basis_ids": [f"{prefix}走线追踪:{out['route_id']}"],
                          "source_ids": [prefix + s["segment_id"] for s in out["segments"]],
                          "source_handles": handles, "drawn_length_m": out["drawn_length_m"],
                          "bridged_gap_length_m": out["bridged_gap_length_m"], "circuit_label": out["circuit_label"],
                          "partial_segments": [{"segment_id": s["segment_id"], "fraction_range": s["fraction_range"]}
                                               for s in out["segments"] if "fraction_range" in s],
                          "review_flags": [flag["flag"] for flag in out["review_flags"]]})
    overlaps, overlap_warnings = overlap_outputs(ov_found, ov_apart, ov_conflicts)
    overlaps["left_out_of_graph"] = sorted(left_out)
    return {"graph": graph, "collected": got, "routes": results, "paths": paths, "shared": shared, "box_count": len(box_list),
            "expanded": sorted(expand), "t_junctions_used": len(used_t), "t_contacts_hint_only": len(hint_t),
            "label_texts": len(label_items), "overlaps": overlaps, "overlap_warnings": overlap_warnings}


def walked_overlap(a: dict[str, Any], b: dict[str, Any], on_a: list[tuple[float, float]], on_b: list[tuple[float, float]]) -> float:
    """Length (drawing units) a route walks on both of two collinear lines: its walked fraction ranges, measured along a."""
    a0, a1 = a["start"][:2], a["end"][:2]
    la = math.dist(a0, a1)
    ua = [(a1[0] - a0[0]) / la, (a1[1] - a0[1]) / la]
    b0, b1 = b["start"][:2], b["end"][:2]

    def along(t: float) -> float:
        return (b0[0] + t * (b1[0] - b0[0]) - a0[0]) * ua[0] + (b0[1] + t * (b1[1] - b0[1]) - a0[1]) * ua[1]
    first = [(lo * la, hi * la) for lo, hi in on_a]
    second = [tuple(sorted((along(lo), along(hi)))) for lo, hi in on_b]
    return math.fsum(max(0.0, min(x1, y1) - max(x0, y0)) for x0, x1 in first for y0, y1 in second)


def flatten_elevations(segments: list[dict[str, Any]], factor: float, flat_tolerance: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Plan routes are measured and joined in plan: every straight segment and every arc lying in a horizontal plane is
    moved to z = 0 before the graph is built, so ends that meet in plan meet (D003 draws LWPOLYLINEs at elevations
    7.71, 15.42). A sloped line (ends more than flat_tolerance apart in z - the snap tolerance, so float noise is not a
    slope - as D021 52DF from z 8000 to 0) is counted at its plan length and listed; an arc out of the horizontal plane
    keeps its geometry and is listed. Returns the segments and the record."""
    out, moved, sloped, tilted = [], defaultdict(int), [], []
    for s in segments:
        part = s["part"]
        if part["kind"] != "line" and (abs(part["u"][2]) > 1e-9 or abs(part["v"][2]) > 1e-9):
            tilted.append({"segment_id": s["segment_id"], "handle": s["handle"], "length_m": s["length"] * factor})
            out.append(s)
            continue
        zs = [s["start"][2], s["end"][2]] + ([] if part["kind"] == "line" else [part["center"][2]])
        if all(z == 0.0 for z in zs):
            out.append({**s, "plan_z": (0.0, 0.0)})
            continue
        part = {**part, **{k: [*part[k][:2], 0.0] for k in ("start", "end", "center") if k in part}}
        b = s["bbox3d"]
        row = {**s, "start": [*s["start"][:2], 0.0], "end": [*s["end"][:2], 0.0], "part": part, "bbox3d": [b[0], b[1], 0.0, b[3], b[4], 0.0],
               "plan_z": (min(zs), max(zs))}
        if part["kind"] == "line" and max(zs) - min(zs) > max(flat_tolerance, 1e-9):
            row["length"] = math.dist(row["start"][:2], row["end"][:2])
            sloped.append({"segment_id": s["segment_id"], "handle": s["handle"], "z_start": s["start"][2], "z_end": s["end"][2],
                           "length_3d_m": s["length"] * factor, "length_plan_m": row["length"] * factor})
        else:
            moved[f"{max(zs, key=abs):g}"] += 1
        out.append(row)
    return out, {"elevations_flattened": dict(sorted(moved.items())), "sloped_lines_in_plan": sloped, "tilted_arcs_kept": tilted}


def plan_warnings(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if record["sloped_lines_in_plan"]:
        rows.append({"warning": "sloped_lines_measured_in_plan", "count": len(record["sloped_lines_in_plan"]),
                     "length_3d_minus_plan_m": math.fsum(x["length_3d_m"] - x["length_plan_m"] for x in record["sloped_lines_in_plan"]),
                     "detail": "两端标高不同的线按平面投影计长；核对图纸，这段若真是竖向或斜向敷设，另按竖向列"})
    if record["tilted_arcs_kept"]:
        rows.append({"warning": "arcs_out_of_plan_kept", "count": len(record["tilted_arcs_kept"]),
                     "detail": "不在水平面里的弧按原几何计长、不参与平面并点，核对图纸"})
    return rows


def find_overlaps(segments: list[dict[str, Any]], req: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fifth batch L4: pairs of straight segments on one layer (or one declared layer group) lying on one line in plan
    and sharing an interval longer than min_overlap - a local redraw (D022 0.8 m) or a whole copy. Both must sit flat at
    one original elevation; a pair at different or sloped z is listed apart and never merged. Only candidates: nothing
    is taken off until the project writes a disposition for the pair."""
    cfg, f = req["overlaps"], req["factor"]
    tol, least, flat_tol = cfg["distance"] / f, cfg["min"] / f, max(req["snap"] / f, 1e-9)
    cos_tol = math.cos(math.radians(cfg["angle_deg"]))

    def group(layer: str) -> str:
        return next((f"layer_groups[{i}]" for i, rx in enumerate(cfg["groups"]) if rx.search(layer)), layer)

    def along(p: list[float], o: list[float], u: list[float]) -> float:
        return (p[0] - o[0]) * u[0] + (p[1] - o[1]) * u[1]

    def across(p: list[float], o: list[float], u: list[float]) -> float:
        return abs((p[0] - o[0]) * u[1] - (p[1] - o[1]) * u[0])
    lines = [s for s in segments if s["kind"] == "line" and s.get("plan_z") is not None and math.dist(s["start"][:2], s["end"][:2]) > 0]
    index = BoundsIndex([(s["segment_id"], [min(s["start"][0], s["end"][0]) - tol, min(s["start"][1], s["end"][1]) - tol,
                                            max(s["start"][0], s["end"][0]) + tol, max(s["start"][1], s["end"][1]) + tol]) for s in lines])
    by_id = {s["segment_id"]: s for s in lines}
    found, apart = [], []
    for a in lines:
        a0, a1 = a["start"][:2], a["end"][:2]
        la = math.dist(a0, a1)
        ua = [(a1[0] - a0[0]) / la, (a1[1] - a0[1]) / la]
        for key in index.query_box(list(index.boxes[a["segment_id"]])):
            b = by_id[key]
            if key <= a["segment_id"] or group(a["layer"]) != group(b["layer"]):
                continue
            b0, b1 = b["start"][:2], b["end"][:2]
            lb = math.dist(b0, b1)
            ub = [(b1[0] - b0[0]) / lb, (b1[1] - b0[1]) / lb]
            if abs(ua[0] * ub[0] + ua[1] * ub[1]) < cos_tol:
                continue
            t = sorted((along(b0, a0, ua), along(b1, a0, ua)))
            lo, hi = max(0.0, t[0]), min(la, t[1])
            if hi - lo <= least:
                continue
            p_lo, p_hi = [a0[0] + ua[0] * lo, a0[1] + ua[1] * lo], [a0[0] + ua[0] * hi, a0[1] + ua[1] * hi]
            if max(across(p_lo, b0, ub), across(p_hi, b0, ub)) > tol:     # apart across the shared interval
                continue
            on_b = sorted((min(max(along(p_lo, b0, ub), 0.0), lb), min(max(along(p_hi, b0, ub), 0.0), lb)))
            row = {"segment_ids": [a["segment_id"], b["segment_id"]], "handles": [a["handle"], b["handle"]], "layers": [a["layer"], b["layer"]],
                   "part_indices": [a["part_index"], b["part_index"]], "lengths_m": [la * f, lb * f], "overlap_m": (hi - lo) * f,
                   "kind": "整条重复" if hi - lo >= min(la, lb) - tol else "局部重复",
                   "from_point_m": [p_lo[0] * f, p_lo[1] * f], "to_point_m": [p_hi[0] * f, p_hi[1] * f],
                   "_spans": {a["segment_id"]: (lo / la, hi / la), b["segment_id"]: (on_b[0] / lb, on_b[1] / lb)}}
            za, zb = a["plan_z"], b["plan_z"]
            if za[1] - za[0] > flat_tol or zb[1] - zb[0] > flat_tol or abs(za[0] - zb[0]) > flat_tol:
                apart.append({**row, "z": [list(za), list(zb)]})
            else:
                found.append(row)
    found.sort(key=lambda r: r["segment_ids"]); apart.sort(key=lambda r: r["segment_ids"])
    for number, row in enumerate(found, 1):
        row["group_id"] = f"OV-{number:04d}"
    return found, apart


def union_length(spans: list[tuple[float, float]]) -> float:
    total, end = 0.0, -math.inf
    for lo, hi in sorted(spans):
        if hi > end:
            total += hi - max(lo, end); end = hi
    return total


def merge_spans(spans: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def subtract_spans(base: list[tuple[float, float]], cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Parts of the merged spans `base` not covered by the merged spans `cuts`."""
    left = []
    for lo, hi in base:
        at = lo
        for c_lo, c_hi in cuts:
            if c_hi <= at or c_lo >= hi:
                continue
            if c_lo > at:
                left.append((at, c_lo))
            at = max(at, c_hi)
        if at < hi:
            left.append((at, hi))
    return left


def resolve_overlaps(found: list[dict[str, Any]], apart: list[dict[str, Any]], req: dict[str, Any],
                     segments: list[dict[str, Any]]) -> tuple[dict[str, list[tuple[float, float]]], list[dict[str, Any]]]:
    """Attach the request's one disposition per pair; returns ({segment_id: [fraction spans to take off]} for drop_overlap,
    conflicts). A pair that is no candidate, or one at different elevations, is refused. Undisposed or pending pairs stay open.
    A drop must leave a line behind (CXR-01): candidates that share lines form a group on one line, and wherever a line
    was drawn before the drops, one must still be drawn after them - checked on the union of the group's spans, not pair
    by pair, so a cycle of "keep the other one" or drops that each keep a line but together empty a stretch are a
    disposition conflict: none of the group's drops is taken, its disposed pairs read 处置冲突 and stay open."""
    pairs = {tuple(sorted(r["segment_ids"])): r for r in found}
    apart_pairs = {tuple(sorted(r["segment_ids"])) for r in apart}
    for index, res in enumerate(req["overlaps"]["resolutions"]):
        if res["pair"] in apart_pairs:
            fail(f"段对 {res['pair'][0]}、{res['pair'][1]} 标高不同，不合并、不能写处置（列在 overlap_z_differs 待核）")
        if res["pair"] not in pairs:
            fail(f"段对 {res['pair'][0]}、{res['pair'][1]} 不是本次的共线重合候选（段号取自重合候选表）")
        row = pairs[res["pair"]]
        row["resolution"] = {"action": res["action"], "drop": res["drop"], "basis": res["basis"]}
        row["_index"] = index
    link: dict[str, str] = {}

    def root(x: str) -> str:
        while link.setdefault(x, x) != x:
            link[x] = link[link[x]]; x = link[x]
        return x
    for row in found:
        a, b = (root(x) for x in row["segment_ids"])
        link[max(a, b)] = min(a, b)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in found:
        groups[root(row["segment_ids"][0])].append(row)
    by_id = {s["segment_id"]: s for s in segments}
    f, least = req["factor"], req["overlaps"]["min"] / req["factor"]
    cut: dict[str, list[tuple[float, float]]] = defaultdict(list)
    conflicts = []
    for rows in sorted(groups.values(), key=lambda rs: rs[0]["group_id"]):
        drops = [r for r in rows if (r.get("resolution") or {}).get("action") == "drop_overlap"]
        if not drops:
            continue
        ids = sorted({x for r in rows for x in r["segment_ids"]})
        o, e = by_id[ids[0]]["start"][:2], by_id[ids[0]]["end"][:2]
        u = [(e[0] - o[0]) / math.dist(o, e), (e[1] - o[1]) / math.dist(o, e)]

        def on_axis(sid: str, t: float) -> float:
            s, q = by_id[sid]["start"], by_id[sid]["end"]
            return (s[0] + t * (q[0] - s[0]) - o[0]) * u[0] + (s[1] + t * (q[1] - s[1]) - o[1]) * u[1]
        span = {sid: tuple(sorted((on_axis(sid, 0.0), on_axis(sid, 1.0)))) for sid in ids}
        taken: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for r in drops:
            d = r["resolution"]["drop"]
            taken[d].append(tuple(sorted(on_axis(d, t) for t in r["_spans"][d])))
        before = merge_spans(span.values())
        after = merge_spans(p for sid in ids for p in subtract_spans([span[sid]], merge_spans(taken.get(sid, []))))
        lost = [p for p in subtract_spans(before, after) if p[1] - p[0] > 1e-9]
        if math.fsum(hi - lo for lo, hi in lost) <= least:
            for r in drops:
                cut[r["resolution"]["drop"]].append(r["_spans"][r["resolution"]["drop"]])
            continue
        hits = lambda r: any(min(hi, b) - max(lo, a) > 1e-9 for a, b in lost   # noqa: E731
                             for lo, hi in [tuple(sorted(on_axis(r["resolution"]["drop"], t) for t in r["_spans"][r["resolution"]["drop"]]))])
        point = lambda a: [(o[0] + u[0] * a) * f, (o[1] + u[1] * a) * f]   # noqa: E731
        for r in rows:
            if r.get("resolution"):
                r["conflict"] = True
        conflicts.append({
            "conflict_id": f"OVC-{len(conflicts) + 1:04d}", "segment_ids": ids, "handles": [by_id[x]["handle"] for x in ids],
            "group_ids": [r["group_id"] for r in rows], "lost_m": math.fsum(hi - lo for lo, hi in lost) * f,
            "lost_intervals": [{"from_point_m": point(lo), "to_point_m": point(hi), "length_m": (hi - lo) * f} for lo, hi in lost],
            "dispositions": [{"index": r["_index"], "group_id": r["group_id"], "segment_ids": r["segment_ids"],
                              "drop": r["resolution"]["drop"], "basis": r["resolution"]["basis"]} for r in sorted(drops, key=lambda r: r["_index"]) if hits(r)],
            "detail": "这几条去重处置执行后，上面的区间一段线都不剩（互相“留另一段”）：本组不扣、按没处置计；核图后改成统一留一段，"
                      "确实整段不计的走排除依据"})
    for row in found:
        action = (row.get("resolution") or {}).get("action")
        row["status"] = "处置冲突" if row.get("conflict") else {"drop_overlap": "已扣重合", "keep_both": "两段都保留", "pending": "暂缓"}.get(action, "未处置")
    return dict(cut), conflicts


def overlap_outputs(found: list[dict[str, Any]], apart: list[dict[str, Any]],
                    conflicts: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Result section and warnings of the overlap check (both requests)."""
    clean = lambda r: {k: v for k, v in r.items() if not k.startswith("_") and k != "conflict"}   # noqa: E731
    open_rows = [r for r in found if r["status"] in ("未处置", "暂缓", "处置冲突")]
    warnings = []
    if conflicts:
        warnings.append({"warning": "collinear_overlap_dispositions_conflict", "count": len(conflicts),
                         "lost_m": math.fsum(c["lost_m"] for c in conflicts),
                         "detail": "几条去重处置互相“留另一段”，执行后有重合区间一段线都不剩：这一组不扣、按没处置计；"
                                   "核图后改成统一留一段，确实整段不计的走排除依据"})
    if open_rows:
        warnings.append({"warning": "collinear_overlaps_open", "count": len(open_rows), "overlap_m": math.fsum(r["overlap_m"] for r in open_rows),
                         "detail": "同层共线重合的线段对没有处置：逐对核图，写 overlap_resolutions（drop_overlap 扣一段的重合区间，keep_both 两段都留）"})
    if apart:
        warnings.append({"warning": "collinear_overlaps_at_different_z", "count": len(apart),
                         "detail": "平面共线重合但原始标高不同或有斜线：不合并、不能处置，核对是不是不同标高的两路"})
    return {"candidates": [clean(r) for r in found], "z_differs": [clean(r) for r in apart], "conflicts": conflicts,
            "open_count": len(open_rows), "resolved": not open_rows}, warnings


def parse_layer_request(request: dict[str, Any], base: Path) -> dict[str, Any]:
    """The layer-length request (fifth batch L3): sheets as in the coverage ledger, regions, end classes, feeders."""
    req = parse_common(request, LAYER_REQUEST_SCHEMA)
    kinds = request.get("exclude_sheet_kinds", [LEGEND_SHEET_KIND])
    if not isinstance(kinds, list) or any(kind not in EXCLUDABLE_SHEET_KINDS for kind in kinds):
        fail("exclude_sheet_kinds 只能取 " + "、".join(EXCLUDABLE_SHEET_KINDS) + "（平面、详图不能整类剔除）")
    specs = request.get("sheets") or []
    specs = [specs] if isinstance(specs, dict) else specs
    if not isinstance(specs, list):
        fail("sheets 须为图页表数组（写法同点位覆盖台账的 sheets）")
    problems, files, sheets = [], [], []
    for spec in specs:
        rows, _ = sheet_tables.read_rows(spec, sheet_tables.SHEET_KEYS, base, problems, files, "sheets")
        for row in rows:
            if req["drawing_id"] and str(row.get("drawing") or "") != req["drawing_id"]:
                continue
            box = [row.get(k) for k in ("x1", "y1", "x2", "y2")]
            kind = row.get("sheet_kind") or None
            if not row.get("sheet_id") or any(v is None for v in box):
                problems.append({"where": row["_where"], "message": "图页须有 sheet_id 和 x1、y1、x2、y2"}); continue
            if kind is not None and kind not in sheet_tables.SHEET_KINDS:
                problems.append({"where": row["_where"], "message": "sheet_kind 只能是 " + "、".join(sheet_tables.SHEET_KINDS)}); continue
            x1, x2 = sorted((box[0], box[2])); y1, y2 = sorted((box[1], box[3]))
            sheets.append({"sheet_id": row["sheet_id"], "title": row.get("title") or row["sheet_id"], "kind": kind,
                           "bounds": (x1, y1, x2, y2), "area": (x2 - x1) * (y2 - y1)})
    if problems:
        fail("图页表有问题：" + "；".join(f"{p['where']} {p['message']}" for p in problems[:5]))
    regions = []
    for index, region in enumerate(request.get("regions") or []):
        polygon = region.get("polygon") if isinstance(region, dict) else None
        if not isinstance(polygon, list) or len(polygon) < 3 or any(not isinstance(p, list) or len(p) < 2 for p in polygon):
            fail(f"regions[{index}].polygon 须为至少 3 个点 [x, y]（米）")
        regions.append({"region_id": text(region.get("region_id"), f"regions[{index}].region_id"),
                        "polygon": [[finite(p[0], f"regions[{index}].polygon"), finite(p[1], f"regions[{index}].polygon")] for p in polygon]})
    req.update(exclude_kinds=list(kinds), sheets=sheets, files=files, regions=regions, endpoints=None, feeders=None)
    raw_ends = request.get("endpoints")
    if raw_ends is not None:
        if not isinstance(raw_ends, dict):
            fail("endpoints 必须是对象")
        if raw_ends.get("radius") is None:
            fail("endpoints.radius 必须写明（端点找实例的半径，按输出单位米、按本图图符定，没有默认值）")
        radius = finite(raw_ends["radius"], "endpoints.radius")
        if radius < 0:
            fail("endpoints.radius 须为非负有限数值")
        classes = []
        for index, item in enumerate(raw_ends.get("classes") or []):
            if not isinstance(item, dict) or not isinstance(item.get("class"), str) or not item["class"].strip():
                fail(f"endpoints.classes[{index}].class 必须是非空文本")
            rule_ids = item.get("rule_ids") or []
            if not isinstance(rule_ids, list) or (not rule_ids and not item.get("block_pattern")):
                fail(f"endpoints.classes[{index}] 须写 rule_ids（识别规则号数组）或 block_pattern")
            if item.get("role") not in (None, "source"):
                fail(f"endpoints.classes[{index}].role 只能是 source（箱）")
            classes.append({"class": item["class"].strip(), "rule_ids": set(map(str, rule_ids)), "source": item.get("role") == "source",
                            "block_re": pattern(item["block_pattern"], f"endpoints.classes[{index}].block_pattern") if item.get("block_pattern") else None})
        if not classes:
            fail("endpoints.classes 须为非空数组（块类别到回路类别的映射由项目给）")
        source = raw_ends.get("instances")
        if not isinstance(source, dict):
            fail("endpoints.instances 须写 path（点位中性实例处置清单）")
        path = Path(text(source.get("path"), "endpoints.instances.path"))
        path = path if path.is_absolute() else (base / path).resolve()
        if not path.is_file():
            fail(f"endpoints.instances.path 找不到：{path}")
        dispositions = set(source.get("dispositions") or ["adopted"])
        instances = []
        for row in load_stream(path):
            if row.get("disposition") not in dispositions:
                continue
            pid = text(row.get("physical_id"), f"{path.name} physical_id")
            hit = sorted({c["class"] for c in classes if (set(map(str, row.get("rule_ids") or ())) & c["rule_ids"])
                          or (c["block_re"] is not None and c["block_re"].search(str(row.get("block_name") or "")))})
            if len(hit) > 1:
                fail(f"实例 {pid} 同时命中类别 {'、'.join(hit)}，类别映射要唯一")
            box = row.get("scope_geometry_bbox")
            if isinstance(box, list) and len(box) == 4:
                box = [finite(v, f"{pid}.scope_geometry_bbox") for v in box]
            elif isinstance(row.get("point"), list) and row.get("coordinate_unit") == "m":
                x, y = finite(row["point"][0], f"{pid}.point") / req["factor"], finite(row["point"][1], f"{pid}.point") / req["factor"]
                box = [x, y, x, y]
            else:
                fail(f"实例 {pid} 没有外框 scope_geometry_bbox，也没有按米的 point")
            instances.append({"physical_id": pid, "block_name": str(row.get("block_name") or ""), "class": hit[0] if hit else None,
                              "source": bool(hit) and any(c["source"] and c["class"] == hit[0] for c in classes), "box": box})
        files.append({"role": "instances", "path": str(path), "sha256": sha256(path)})
        markers = raw_ends.get("building_markers")
        req["endpoints"] = {"radius": radius, "classes": classes, "instances": instances, "dispositions": sorted(dispositions),
                            "marker_re": pattern(markers.get("layer_pattern"), "endpoints.building_markers.layer_pattern")
                            if isinstance(markers, dict) else None}
    feeders = request.get("feeders")
    if feeders is not None:
        if req["endpoints"] is None:
            fail("feeders 要和 endpoints 一起写（设备和箱的类别由 endpoints.classes 定）")
        device_classes = feeders.get("device_classes") if isinstance(feeders, dict) else None
        if not isinstance(device_classes, list) or not device_classes:
            fail("feeders.device_classes 须为非空数组（如 [\"插座箱\"]）")
        req["feeders"] = {"device_classes": set(device_classes)}
    return req


def in_polygon(point: list[float], polygon: list[list[float]]) -> bool:
    inside, n = False, len(polygon)
    for i in range(n):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % n]
        if (y1 > point[1]) != (y2 > point[1]) and point[0] < x1 + (point[1] - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def measure_layers(drawing: Drawing, req: dict[str, Any]) -> dict[str, Any]:
    """Fifth batch L3: every line on the route layers, totalled by sheet, region and layer, legend samples dropped;
    connected parts (bridged gaps and T junctions join) listed with their dangling ends classed by the instances
    within the project's radius; socket boxes traced to the box in their part. Nothing is shared out by length."""
    f, expand = req["factor"], set(req["expand"])
    rows = [row for row in drawing.inserts.values() if str(row.get("block_name") or "") in expand] + drawing.curves
    got = segment_graph.collect_segments(rows, drawing.definitions, drawing.origins, layer_pattern=req["route_re"],
                                         expand_blocks=sorted(expand), scope=req["bounds"], max_depth=req["max_depth"])

    def sheet_at(p: list[float]) -> dict[str, Any] | None:
        hits = [s for s in req["sheets"] if s["bounds"][0] <= p[0] <= s["bounds"][2] and s["bounds"][1] <= p[1] <= s["bounds"][3]]
        return min(hits, key=lambda s: s["area"]) if hits else None

    def middle(s: dict[str, Any]) -> list[float]:
        if s["kind"] == "line":
            return [(s["start"][k] + s["end"][k]) / 2 for k in range(2)]
        part = s["part"]
        return cad_geometry.conic_point(part, part["start_param"] + part["sweep_param"] / 2)[:2]
    where: dict[str, dict[str, Any]] = {}
    kept, excluded, crossing = [], [], []
    flat, got["plan"] = flatten_elevations(got["segments"], f, req["snap"] / f)
    for s in flat:
        mid = middle(s)
        sheet = sheet_at(mid)
        ends = {(x or {}).get("sheet_id") for x in (sheet_at(s["start"]), sheet_at(s["end"]))}
        info = {"sheet_id": sheet["sheet_id"] if sheet else UNSHEETED, "sheet_kind": sheet["kind"] if sheet else None,
                "region_id": next((r["region_id"] for r in req["regions"] if in_polygon([mid[0] * f, mid[1] * f], r["polygon"])), UNREGIONED),
                "layer": s["layer"], "length_m": s["length"] * f}
        where[s["segment_id"]] = info
        if sheet and sheet["kind"] in req["exclude_kinds"]:
            excluded.append({"segment_id": s["segment_id"], "handle": s["handle"], "layer": s["layer"], **info,
                             "drawing_from": s["start"][:2], "drawing_to": s["end"][:2]})
            continue
        if len(ends) > 1:
            crossing.append({"segment_id": s["segment_id"], "handle": s["handle"], "sheet_ids": sorted(x or UNSHEETED for x in ends),
                             "sheet_id": info["sheet_id"]})
        kept.append(s)
    # L4: collinear overlaps among the counted lines; a drop_overlap disposition takes the shared interval off the
    # named segment, which stays in the graph (its ends still join)
    found, apart = find_overlaps(kept, req)
    cut, conflicts = resolve_overlaps(found, apart, req, kept)
    deducted = 0.0
    for k, s in enumerate(kept):
        if s["segment_id"] in cut:
            take = min(union_length(cut[s["segment_id"]]), 1.0) * s["length"]
            kept[k] = {**s, "length": s["length"] - take, "overlap_deducted": take}
            where[s["segment_id"]]["length_m"] = kept[k]["length"] * f
            deducted += take
    graph = segment_graph.build_graph(kept, snap_tolerance=req["snap"] / f, gap_tolerance=req["gap"] / f,
                                      gap_angle_deg=req["gap_angle_deg"])
    segs = {s["segment_id"]: s for s in graph["segments"]}
    point_of = {n["node_id"]: n["point"] for n in graph["nodes"]}
    link = {n: n for n in point_of}

    def find(x: str) -> str:
        while link[x] != x:
            link[x] = link[link[x]]; x = link[x]
        return x

    def join(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            link[max(ra, rb)] = min(ra, rb)
    degree: dict[str, int] = defaultdict(int)
    for s in graph["segments"]:
        join(s["start_node"], s["end_node"]); degree[s["start_node"]] += 1; degree[s["end_node"]] += 1
    for g in graph["gap_bridged"]:
        join(g["from_node"], g["to_node"]); degree[g["from_node"]] += 1; degree[g["to_node"]] += 1
    tees = [t for t in graph["t_contacts"] if req["t_junctions"] and segs[t["on_segment_id"]]["kind"] == "line"]
    for t in tees:                                    # a branch end on another straight line's middle joins it
        join(t["node_id"], segs[t["on_segment_id"]]["start_node"]); degree[t["node_id"]] += 1
    parts: dict[str, dict[str, Any]] = defaultdict(lambda: {"segments": [], "gaps": [], "tees": 0, "nodes": []})
    for s in graph["segments"]:
        parts[find(s["start_node"])]["segments"].append(s)
    for g in graph["gap_bridged"]:
        parts[find(g["from_node"])]["gaps"].append(g)
    for t in tees:
        parts[find(t["node_id"])]["tees"] += 1
    for n in point_of:
        parts[find(n)]["nodes"].append(n)

    ends_cfg = req["endpoints"]
    r = ends_cfg["radius"] / f if ends_cfg else 0.0
    inst_index = BoundsIndex([(i["physical_id"], i["box"]) for i in ends_cfg["instances"]]) if ends_cfg and ends_cfg["instances"] else None
    inst_of = {i["physical_id"]: i for i in ends_cfg["instances"]} if ends_cfg else {}
    source_classes = {c["class"] for c in ends_cfg["classes"] if c["source"]} if ends_cfg else set()
    markers = []
    if ends_cfg and ends_cfg["marker_re"] is not None:   # top-level circles on the marker layers, in scope by their centre
        for row in drawing.circles:
            part = ((row.get("geometry") or {}).get("parts") or [{}])[0]
            if ends_cfg["marker_re"].search(str(row.get("layer") or "")) and part.get("center") and part.get("u"):
                if cad_geometry.point_in_scope(part["center"], req["bounds"]):
                    markers.append((part["center"][:2], math.hypot(*part["u"][:2])))

    def near(p: list[float]) -> list[tuple[float, dict[str, Any]]]:
        if inst_index is None:
            return []
        keys = inst_index.query_box([p[0] - r, p[1] - r, p[0] + r, p[1] + r])
        return sorted(((d, inst_of[k]) for k in keys if (d := box_distance(p, list(inst_index.boxes[k]))) <= r),
                      key=lambda x: (x[0], x[1]["physical_id"]))

    def end_of(node: str) -> dict[str, Any]:
        p = point_of[node]
        hits = near(p)
        classes = sorted({inst["class"] for _, inst in hits if inst["class"]})
        unmapped = sorted(inst["physical_id"] for _, inst in hits if inst["class"] is None)
        row: dict[str, Any] = {"node_id": node, "point_m": [p[0] * f, p[1] * f], "classes": classes,
                               "instances": [{"physical_id": inst["physical_id"], "class": inst["class"], "distance_m": d * f} for d, inst in hits]}
        if unmapped:
            row["unmapped_instances"] = unmapped
        if unmapped or len(classes) > 1:
            row["status"] = "未决"
        elif classes:
            row["status"] = "已归类"
        elif any(max(math.dist(p[:2], c) - radius, 0.0) <= r for c, radius in markers):
            row["status"] = "楼栋示意圆"
        else:
            row["status"] = "未连"
        return row

    components, todo, by_class = [], [], defaultdict(float)
    comp_of_node: dict[str, str] = {}
    ordered = sorted(parts.values(), key=lambda part: min(s["segment_id"] for s in part["segments"]))
    for number, part in enumerate(ordered, 1):
        cid = f"LC-{number:04d}"
        for n in part["nodes"]:
            comp_of_node[n] = cid
        seg_list = sorted(part["segments"], key=lambda s: s["segment_id"])
        sheets = sorted({where[s["segment_id"]]["sheet_id"] for s in seg_list})
        crossed = [c for c in crossing if c["segment_id"] in {s["segment_id"] for s in seg_list}]
        dangling = sorted(n for n in part["nodes"] if degree[n] == 1)
        comp = {"component_id": cid, "length_m": math.fsum(s["length"] for s in seg_list) * f,
                "bridged_gap_length_m": math.fsum(g["length"] for g in part["gaps"]) * f, "segment_count": len(seg_list),
                "handles": list(dict.fromkeys(s["handle"] for s in seg_list)), "segment_ids": [s["segment_id"] for s in seg_list],
                "layers": sorted({s["layer"] for s in seg_list}), "sheets": sheets,
                "regions": sorted({where[s["segment_id"]]["region_id"] for s in seg_list}), "t_junction_count": part["tees"],
                "dangling_points": [[point_of[n][0] * f, point_of[n][1] * f] for n in dangling], "review_flags": []}
        if len(sheets) > 1 or crossed:
            comp["review_flags"].append({"flag": "跨图页", "sheets": sorted(set(sheets) | {x for c in crossed for x in c["sheet_ids"]})})
        if ends_cfg is None:
            comp.update(status="未归类", **{"class": None})
        else:
            ends = [end_of(n) for n in dangling]
            sources = sorted({inst["physical_id"] for n in part["nodes"] for _, inst in near(point_of[n]) if inst["source"]})
            load = sorted({c for e in ends if e["status"] == "已归类" for c in e["classes"] if c not in source_classes})
            if any(e["status"] == "未决" for e in ends) or len(load) > 1:
                status, cls = "未决", None
            elif load:
                status, cls = "已归类", load[0]
            else:
                status, cls = "未连", None
            comp.update(status=status, ends=ends, sources=sources, load_classes=load, **{"class": cls})
            if len(sources) > 1:
                comp["review_flags"].append({"flag": "连到两只以上箱", "instance_ids": sources, "detail": "串箱或画法相连，核对系统图"})
            if status == "未决":
                todo.append({"component_id": cid, "kind": "未决", "problem": "两端归类不清",
                             "detail": {"load_classes": load, "end_classes": [e["classes"] for e in ends if e["status"] == "未决"],
                                        "unmapped_instances": sorted({x for e in ends for x in e.get("unmapped_instances", ())})}})
            for e in ends:
                if e["status"] == "楼栋示意圆":
                    todo.append({"component_id": cid, "kind": "待办", "problem": "到楼栋示意圆，没有进户点", "detail": {"point_m": e["point_m"]}})
        for flag in comp["review_flags"]:
            todo.append({"component_id": cid, "kind": "复核", "problem": flag["flag"], "detail": {k: v for k, v in flag.items() if k != "flag"}})
        by_class[comp["class"] if comp["status"] == "已归类" else comp["status"]] += comp["length_m"]
        components.append(comp)

    feeders, l1_routes = [], []
    if req["feeders"]:
        node_index = BoundsIndex([(n, [p[0], p[1], p[0], p[1]]) for n, p in point_of.items()]) if point_of else None
        source_parts: dict[str, set[str]] = defaultdict(set)
        for comp in components:
            for pid in comp.get("sources", ()):
                source_parts[comp["component_id"]].add(pid)
        for inst in sorted(inst_of.values(), key=lambda i: i["physical_id"]):
            if inst["class"] not in req["feeders"]["device_classes"]:
                continue
            b = inst["box"]
            hits = set(node_index.query_box([b[0] - r, b[1] - r, b[2] + r, b[3] + r])) if node_index else set()
            touched = sorted({comp_of_node[n] for n in hits if box_distance(point_of[n], b) <= r})
            row = {"device": inst["physical_id"], "class": inst["class"], "component_ids": touched}
            boxes = sorted(set().union(*(source_parts[c] for c in touched))) if touched else []
            if not touched:
                row.update(status="待办", problem="离走线超出半径")
            elif len(boxes) == 1:
                row.update(status="对应", box=boxes[0])
                l1_routes.append({"route_id": f"FEED-{inst['physical_id']}", "from": {"instance_id": boxes[0]},
                                  "to": {"instance_id": inst["physical_id"]}})
            elif boxes:
                row.update(status="未决", box_candidates=boxes, problem="同一分量连到几只箱")
            else:
                row.update(status="待办", problem="分量里没有箱")
            if row["status"] != "对应":
                todo.append({"component_id": ",".join(touched), "kind": row["status"], "problem": f"{inst['class']}归箱：{row['problem']}",
                             "detail": {"device": inst["physical_id"], **({"box_candidates": boxes} if boxes else {})}})
            feeders.append(row)

    overlaps, overlap_warnings = overlap_outputs(found, apart, conflicts)
    for c in conflicts:
        comps = ",".join(sorted({comp_of_node[segs[x]["start_node"]] for x in c["segment_ids"]}))
        todo.append({"component_id": comps, "kind": "待办", "problem": "共线重合处置冲突", "detail": c})
    for row in [*found, *apart]:
        comps = ",".join(sorted({comp_of_node[segs[x]["start_node"]] for x in row["segment_ids"]}))
        detail = {"segment_ids": row["segment_ids"], "handles": row["handles"], "overlap_m": row["overlap_m"], "kind": row["kind"],
                  "from_point_m": row["from_point_m"], "to_point_m": row["to_point_m"]}
        if row.get("status") in ("未处置", "暂缓"):
            todo.append({"component_id": comps, "kind": "待办", "problem": f"共线重合{row['status']}", "detail": {"group_id": row["group_id"], **detail}})
        elif "z" in row:
            todo.append({"component_id": comps, "kind": "复核", "problem": "共线重合但标高不同", "detail": {**detail, "z": row["z"]}})

    def totals(key: str) -> list[dict[str, Any]]:
        sums: dict[Any, list[float]] = defaultdict(list)
        for s in graph["segments"]:
            sums[where[s["segment_id"]][key]].append(s["length"] * f)
        return [{key: k, "length_m": math.fsum(v), "segment_count": len(v)} for k, v in sorted(sums.items(), key=lambda kv: str(kv[0]))]
    by_sheet = totals("sheet_id")
    kind_of = {s["sheet_id"]: s["kind"] for s in req["sheets"]}
    for row in by_sheet:
        row["sheet_kind"] = kind_of.get(row["sheet_id"])
    detail: dict[tuple, list[float]] = defaultdict(list)
    for s in graph["segments"]:
        w = where[s["segment_id"]]
        detail[(w["sheet_id"], w["sheet_kind"] or "", w["region_id"], s["layer"])].append(s["length"] * f)
    class_rows = [{"class": k, "length_m": v} for k, v in sorted(by_class.items(), key=lambda kv: str(kv[0]))]
    warnings = plan_warnings(got["plan"]) + overlap_warnings
    unsheeted = [s["segment_id"] for s in graph["segments"] if where[s["segment_id"]]["sheet_id"] == UNSHEETED]
    if unsheeted:
        warnings.append({"warning": "segments_on_no_sheet", "count": len(unsheeted), "length_m": math.fsum(where[x]["length_m"] for x in unsheeted),
                         "detail": "这些段不在图页表的任何图页里：图页表可能漏页，补页后重算（图例样线也认不出来）"})
    return {
        "graph": graph, "collected": got, "components": components, "excluded": excluded, "crossing": crossing, "todo": todo,
        "feeders": feeders, "l1_routes": l1_routes, "warnings": warnings, "where": where, "comp_of_node": comp_of_node,
        "graph_points": point_of, "overlaps": overlaps,
        "totals": {"by_sheet": by_sheet, "by_region": totals("region_id"), "by_layer": totals("layer"), "by_class": class_rows,
                   "detail": [{"sheet_id": k[0], "sheet_kind": k[1], "region_id": k[2], "layer": k[3], "length_m": math.fsum(v),
                               "segment_count": len(v)} for k, v in sorted(detail.items())],
                   "counted_m": math.fsum(s["length"] for s in graph["segments"]) * f,
                   "bridged_gap_m": math.fsum(g["length"] for g in graph["gap_bridged"]) * f,
                   "excluded_m": math.fsum(x["length_m"] for x in excluded), "overlap_deducted_m": deducted * f},
        "checks": {"class_total_equals_sheet_total": abs(math.fsum(by_class.values()) - math.fsum(r["length_m"] for r in by_sheet)) < 1e-6,
                   "overlaps_resolved": overlaps["resolved"]},
    }


def write_layer_outputs(args: argparse.Namespace, drawing: Drawing, req: dict[str, Any], done: dict[str, Any]) -> int:
    f = req["factor"]
    review = bool(done["todo"] or done["warnings"])
    clean = lambda row: {k: v for k, v in row.items() if not k.startswith("drawing_")}   # noqa: E731
    ends_cfg = req["endpoints"]
    result = {
        "schema": LAYER_RESULT_SCHEMA, "version": VERSION, "status": "REVIEW_REQUIRED" if review else "REFERENCE_ONLY",
        "inputs": {"cad_manifest": str(args.cad_manifest.resolve()), "cad_manifest_sha256": sha256(args.cad_manifest),
                   "source_sha256": (drawing.manifest.get("authoritative_source") or {}).get("sha256"),
                   "request": str(args.request.resolve()), "request_sha256": sha256(args.request), "files": req["files"]},
        "parameters": {"layout": req["layout"], "route_layers": req["route_re"].pattern, "factor_to_output": f,
                       "unit_source_id": req["unit_source_id"], "scope_bounds": req["bounds"], "expanded_blocks": sorted(req["expand"]),
                       "tolerances_m": {"snap": req["snap"], "gap": req["gap"]}, "gap_angle_deg": req["gap_angle_deg"],
                       "t_junctions": req["t_junctions"], "exclude_sheet_kinds": req["exclude_kinds"], "sheet_count": len(req["sheets"]),
                       "regions": [r["region_id"] for r in req["regions"]],
                       "endpoints": None if ends_cfg is None else {
                           "radius_m": ends_cfg["radius"], "dispositions": ends_cfg["dispositions"], "instance_count": len(ends_cfg["instances"]),
                           "classes": [{"class": c["class"], "rule_ids": sorted(c["rule_ids"]), "source": c["source"],
                                        "block_pattern": c["block_re"].pattern if c["block_re"] else None} for c in ends_cfg["classes"]],
                           "building_marker_layers": ends_cfg["marker_re"].pattern if ends_cfg["marker_re"] else None},
                       "feeders": sorted(req["feeders"]["device_classes"]) if req["feeders"] else None,
                       "overlaps": overlap_parameters(req)},
        "graph": {"counts": done["graph"]["counts"], "degenerate_parts": done["collected"]["degenerate_parts"],
                  "geometry_problems": done["collected"]["geometry_problems"], "unsupported": done["collected"]["unsupported"],
                  "out_of_scope_parts": done["collected"]["out_of_scope_parts"], "ambiguous_gaps": done["graph"]["ambiguous_gaps"],
                  "refused_snaps": done["graph"]["refused_snaps"], **done["collected"]["plan"]},
        "totals": done["totals"], "checks": done["checks"], "warnings": done["warnings"], "components": done["components"],
        "excluded_segments": [clean(x) for x in done["excluded"]], "segments_crossing_sheets": done["crossing"],
        "feeders": done["feeders"], "l1_routes": done["l1_routes"], "overlaps": done["overlaps"], "todo": done["todo"],
        "meaning": "按图层量出的平面线长（只计画出的线，桥接缺口另列；共线重合只扣项目处置为 drop_overlap 的区间）；分量两端按项目给的半径和类别映射归类，"
                   "不按长度分摊，未决、未连单列；归箱只按画出的连线，与系统图回路对账另做",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "按图层量长结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    write_csv(args.output_dir / "按图层量长汇总.csv", ["sheet_id", "sheet_kind", "region_id", "layer", "length_m", "segment_count"],
              [[r["sheet_id"], r["sheet_kind"], r["region_id"], r["layer"], r["length_m"], r["segment_count"]] for r in done["totals"]["detail"]])
    write_csv(args.output_dir / "按图层量长分量.csv",
              ["component_id", "status", "class", "length_m", "bridged_gap_length_m", "segment_count", "sheets", "regions", "sources",
               "load_classes", "review_flags"],
              [[c["component_id"], c["status"], c["class"] or "", c["length_m"], c["bridged_gap_length_m"], c["segment_count"],
                "、".join(c["sheets"]), "、".join(c["regions"]), "、".join(c.get("sources", ())), "、".join(c.get("load_classes", ())),
                "、".join(x["flag"] for x in c["review_flags"])] for c in done["components"]])
    write_csv(args.output_dir / "按图层量长待办.csv", ["component_id", "kind", "problem", "detail"],
              [[t["component_id"], t["kind"], t["problem"], json.dumps(t["detail"], ensure_ascii=False)] for t in done["todo"]])
    lines = []
    for s in done["graph"]["segments"]:
        lines.append([done["comp_of_node"][s["start_node"]], "segment", s["segment_id"], s["handle"], s["layer"],
                      done["where"][s["segment_id"]]["sheet_id"], *s["start"][:2], *s["end"][:2], s["length"] * f])
    for g in done["graph"]["gap_bridged"]:
        a, b = done["graph_points"][g["from_node"]], done["graph_points"][g["to_node"]]
        lines.append([done["comp_of_node"][g["from_node"]], "gap", g["bridge_id"], "", "", "", *a[:2], *b[:2], g["length"] * f])
    for x in done["excluded"]:
        lines.append(["", "excluded", x["segment_id"], x["handle"], x["layer"], x["sheet_id"], *x["drawing_from"], *x["drawing_to"], x["length_m"]])
    write_csv(args.output_dir / "按图层量长映射线.csv",
              ["component_id", "kind", "segment_id", "handle", "layer", "sheet_id", "x1", "y1", "x2", "y2", "length_m"], lines)
    write_overlap_csv(args.output_dir / "按图层量长共线重合.csv", done["overlaps"])
    print(json.dumps({"status": result["status"], "components": len(done["components"]), "counted_m": done["totals"]["counted_m"],
                      "excluded_m": done["totals"]["excluded_m"], "todo": len(done["todo"]), "output_dir": str(args.output_dir)},
                     ensure_ascii=False))
    return 3 if review else 0


def overlap_parameters(req: dict[str, Any]) -> dict[str, Any]:
    cfg = req["overlaps"]
    return {"angle_tolerance_deg": cfg["angle_deg"], "distance_tolerance_m": cfg["distance"], "min_overlap_m": cfg["min"],
            "layer_groups": [g.pattern for g in cfg["groups"]], "resolutions": len(cfg["resolutions"])}


def write_overlap_csv(path: Path, overlaps: dict[str, Any]) -> None:
    """One row per candidate pair (group_id is what a disposition refers to by its two segment ids) and per pair at different z."""
    rows = []
    for r in [*overlaps["candidates"], *overlaps["z_differs"]]:
        res = r.get("resolution") or {}
        rows.append([r.get("group_id", ""), r.get("status", "标高不同"), r["kind"], *r["segment_ids"], *r["handles"], "、".join(dict.fromkeys(r["layers"])),
                     r["overlap_m"], *r["lengths_m"], *r["from_point_m"], *r["to_point_m"], res.get("action", ""), res.get("drop") or "", res.get("basis", ""),
                     json.dumps(r["z"]) if "z" in r else ""])
    write_csv(path, ["group_id", "status", "kind", "segment_id_a", "segment_id_b", "handle_a", "handle_b", "layers", "overlap_m",
                     "length_a_m", "length_b_m", "x1", "y1", "x2", "y2", "action", "drop", "basis", "z"], rows)


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
        raw = load_json(args.request)
        if raw.get("schema") == LAYER_REQUEST_SCHEMA:
            req = parse_layer_request(raw, args.request.resolve().parent)
            drawing = Drawing(args.cad_manifest.resolve(), req)
            return write_layer_outputs(args, drawing, req, measure_layers(drawing, req))
        req = parse_request(raw)
        drawing = Drawing(args.cad_manifest.resolve(), req)
        traced = trace_routes(drawing, req)
        graph, got = traced["graph"], traced["collected"]
        todo = [r for r in traced["routes"] if r["status"] == "待办"]
        conflicts = traced["overlaps"]["conflicts"]
        result = {
            "schema": RESULT_SCHEMA, "version": VERSION, "status": "REVIEW_REQUIRED" if todo or conflicts else "REFERENCE_ONLY",
            "inputs": {"cad_manifest": str(args.cad_manifest.resolve()), "cad_manifest_sha256": sha256(args.cad_manifest),
                       "source_sha256": (drawing.manifest.get("authoritative_source") or {}).get("sha256"),
                       "request": str(args.request.resolve()), "request_sha256": sha256(args.request)},
            "parameters": {"layout": req["layout"], "route_layers": req["route_re"].pattern, "factor_to_output": req["factor"],
                           "unit_source_id": req["unit_source_id"], "scope_bounds": req["bounds"],
                           "tolerances_m": {"snap": req["snap"], "gap": req["gap"], "endpoint": req["endpoint"], "equal_length": req["equal"]},
                           "gap_angle_deg": req["gap_angle_deg"], "graph_parameters_drawing_units": graph["parameters"],
                           "expanded_blocks": traced["expanded"],
                           "box_block_pattern": req["box_re"].pattern if req["box_re"] else None, "box_instances": traced["box_count"],
                           "t_junctions": req["t_junctions"], "t_junctions_used": traced["t_junctions_used"],
                           "t_contacts_hint_only": traced["t_contacts_hint_only"],
                           "circuit_label_pattern": req["label_re"].pattern if req["label_re"] else None,
                           "circuit_label_layers": req["label_layer_re"].pattern if req["label_layer_re"] else None,
                           "circuit_label_radius_m": req["label_radius"], "circuit_label_texts": traced["label_texts"],
                           "overlaps": overlap_parameters(req)},
            "graph": {"counts": graph["counts"], "degenerate_parts": got["degenerate_parts"], "geometry_problems": got["geometry_problems"],
                      "unsupported": got["unsupported"], "expansion_problems": got["expansion_problems"],
                      "out_of_scope_parts": got["out_of_scope_parts"], "ambiguous_gaps": graph["ambiguous_gaps"],
                      "refused_snaps": graph["refused_snaps"], **got["plan"]},
            "warnings": plan_warnings(got["plan"]) + traced["overlap_warnings"],
            "routes": [{k: v for k, v in r.items() if k != "_pieces"} for r in traced["routes"]],
            "shared_segments": traced["shared"], "overlaps": traced["overlaps"],
            "counts": {"routes": len(traced["routes"]), "traced": sum(r["status"] == "已追踪" for r in traced["routes"]),
                       "components": sum(r["status"] == "分量" for r in traced["routes"]), "todo": len(todo)},
            "meaning": "沿已画走线逐段量出的水平长度（含桥接缺口）；回路归属、系统图与平面名称对应由模型判定并列依据；待办不按直角距离补",
        }
        lines, todo_rows = [], []
        for r in traced["routes"]:
            for p in r.get("_pieces", ()):
                if p["kind"] == "t":
                    continue
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
        for c in conflicts:
            todo_rows.append([c["conflict_id"], "待办", "共线重合处置冲突", json.dumps(c, ensure_ascii=False)])
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "走线追踪结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        (args.output_dir / "走线追踪路径.json").write_text(json.dumps(
            {"schema": PATHS_SCHEMA, "unit": "m", "drawing_id": req["drawing_id"], "paths": traced["paths"]},
            ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        write_csv(args.output_dir / "走线追踪映射线.csv",
                  ["route_id", "drawing", "kind", "segment_id", "handle", "layer", "x1", "y1", "x2", "y2", "length_m"], lines)
        write_csv(args.output_dir / "走线追踪待办.csv", ["route_id", "kind", "problem", "detail"], todo_rows)
        write_overlap_csv(args.output_dir / "走线追踪共线重合.csv", traced["overlaps"])
        print(json.dumps({"status": result["status"], **result["counts"], "output_dir": str(args.output_dir)}, ensure_ascii=False))
        return 3 if todo or conflicts else 0
    except (InputError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
