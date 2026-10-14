#!/usr/bin/env python3
"""Duct fitting centreline and vertical-run inference from plan facts (reference only).

Input: explicit straight centrelines, fitting block extents and elevation tags the
model has already identified on the drawing (qto-duct-fitting-vertical-input/1.0).
Output: per fitting arm lengths to the centreline junction (GB 50856: branch to the
main centreline intersection, reducer halves to each side), accessory gaps bridged
for continuity but excluded from length, and vertical runs from elevation
differences. Every length carries an evidence level; a single-ended fitting whose
destination is unknown is listed as pending, never estimated.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from qto_cad_source_contract import canonical_scale

VERSION = "qto-duct-fitting-vertical/1.0.0"
INPUT_SCHEMA = "qto-duct-fitting-vertical-input/1.0"
RESULT_SCHEMA = "qto-duct-fitting-vertical-result/1.0"
FITTING_KINDS = {"elbow", "tee", "cross", "reducer", "rect_to_round", "tap", "offset", "unknown"}
LEVELS = ("确认", "规则推算", "方案暂估", "未决")
ELEVATION_REFERENCES = {"bottom", "top", "center"}
DEFAULT_PARAMETERS = {
    "end_snap_tolerance": 5.0,          # two drawn ends closer than this are one joint
    "fitting_end_tolerance": 15.0,      # a drawn end this far outside a fitting extent still belongs to it
    "accessory_gap_min": 30.0,          # collinear facing gap at least this long is an in-line accessory
    "accessory_gap_max": 2600.0,
    "collinear_cross_limit": 0.01,      # |u x v| below this = parallel
    "junction_max_arm": 5000.0,         # a fitting arm longer than this is not a plausible junction
    "tap_margin": 400.0,                # open end may land on a main within half width + margin
    "band_margin": 250.0,               # single-ended elbow centre within half width + margin of a main
    "riser_match_distance": 2500.0,     # plan distance between stacked single-ended elbows of adjacent floors
    "fitting_overlap_duplicate": 0.7,   # same-kind fittings overlapping this much are one physical fitting
    "accessory_gap_policy": "exclude",  # exclude (GB 50856 部件所占长度不计) or include
}


class InputError(ValueError):
    pass


def fail(message: str) -> None:
    raise InputError(message)


def num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def finite(value: Any, label: str, minimum: float | None = None) -> float:
    if not num(value):
        fail(f"{label} 必须是有限数值")
    if minimum is not None and value < minimum:
        fail(f"{label} 不能小于 {minimum}")
    return float(value)


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def point2(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) not in (2, 3):
        fail(f"{label} 必须是二维坐标")
    return [finite(value[0], label), finite(value[1], label)]


def dist(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def intersect(p1, u1, p2, u2, cross_limit):
    den = u1[0] * u2[1] - u1[1] * u2[0]
    if abs(den) < cross_limit:
        return None
    t = ((p2[0] - p1[0]) * u2[1] - (p2[1] - p1[1]) * u2[0]) / den
    return [p1[0] + t * u1[0], p1[1] + t * u1[1]]


def spec_of(segment: dict[str, Any]) -> str:
    if segment.get("diameter") is not None:
        return f"D{segment['diameter']:g}"
    if segment.get("width") is not None and segment.get("height") is not None:
        return f"{segment['width']:g}x{segment['height']:g}"
    if segment.get("width") is not None:
        return f"{segment['width']:g}x?"
    return "未知规格"


def perimeter_m(segment: dict[str, Any], factor: float) -> float | None:
    if segment.get("diameter") is not None:
        return math.pi * segment["diameter"] * factor
    if segment.get("width") is not None and segment.get("height") is not None:
        return 2 * (segment["width"] + segment["height"]) * factor
    return None


class Network:
    def __init__(self, data: dict[str, Any]):
        self.params = dict(DEFAULT_PARAMETERS)
        raw_params = data.get("parameters", {})
        if not isinstance(raw_params, dict):
            fail("parameters 必须是对象")
        for key, value in raw_params.items():
            if key == "parameter_basis":
                continue
            if key not in DEFAULT_PARAMETERS:
                fail(f"未知参数 {key}")
            if key == "accessory_gap_policy":
                if value not in {"exclude", "include"}:
                    fail("accessory_gap_policy 必须是 exclude 或 include")
                self.params[key] = value
            else:
                self.params[key] = finite(value, f"parameters.{key}", 0.0)
        if self.params["accessory_gap_max"] <= self.params["accessory_gap_min"]:
            fail("accessory_gap_max 必须大于 accessory_gap_min")
        text(raw_params.get("parameter_basis"), "parameters.parameter_basis")
        self.unit = text(data.get("unit"), "unit")
        self.output_unit = str(data.get("output_unit") or "m")
        self.factor = canonical_scale(self.unit) / canonical_scale(self.output_unit)
        self.floors = {}
        for index, item in enumerate(data.get("floors", [])):
            if not isinstance(item, dict):
                fail(f"floors[{index}] 必须是对象")
            floor_id = text(item.get("floor_id"), f"floors[{index}].floor_id")
            if floor_id in self.floors:
                fail(f"楼层重复: {floor_id}")
            level = item.get("level")
            offset = item.get("plan_offset", [0.0, 0.0])
            self.floors[floor_id] = {
                "floor_id": floor_id,
                "level": finite(level, f"{floor_id}.level") if level is not None else None,
                "level_source_id": item.get("level_source_id"),
                "level_evidence_level": str(item.get("level_evidence_level") or ("确认" if level is not None else "未决")),
                "plan_offset": point2(offset, f"{floor_id}.plan_offset"),
                "order": index,
            }
        self.segments: dict[str, dict[str, Any]] = {}
        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            fail("segments 必须是非空数组")
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict):
                fail(f"segments[{index}] 必须是对象")
            sid = text(raw.get("segment_id"), f"segments[{index}].segment_id")
            if sid in self.segments:
                fail(f"管段重复: {sid}")
            start = point2(raw.get("start"), f"{sid}.start")
            end = point2(raw.get("end"), f"{sid}.end")
            length = dist(start, end)
            if length <= 0:
                fail(f"{sid} 零长")
            floor_id = text(raw.get("floor_id"), f"{sid}.floor_id")
            if self.floors and floor_id not in self.floors:
                fail(f"{sid}.floor_id 未在 floors 中登记")
            segment = {
                "segment_id": sid, "floor_id": floor_id, "system": text(raw.get("system"), f"{sid}.system"),
                "start": start, "end": end, "length": length, "u": [(end[0] - start[0]) / length, (end[1] - start[1]) / length],
                "width": finite(raw["width"], f"{sid}.width", 0.0) if raw.get("width") is not None else None,
                "height": finite(raw["height"], f"{sid}.height", 0.0) if raw.get("height") is not None else None,
                "diameter": finite(raw["diameter"], f"{sid}.diameter", 0.0) if raw.get("diameter") is not None else None,
                "size_evidence_level": str(raw.get("size_evidence_level") or "未决"),
                "source_ids": list(raw.get("source_ids") or []),
                "elevation": None,
            }
            if segment["size_evidence_level"] not in LEVELS:
                fail(f"{sid}.size_evidence_level 必须是 {LEVELS} 之一")
            raw_elevation = raw.get("elevation")
            if raw_elevation is not None:
                if not isinstance(raw_elevation, dict):
                    fail(f"{sid}.elevation 必须是对象")
                reference = str(raw_elevation.get("reference") or "")
                if reference not in ELEVATION_REFERENCES:
                    fail(f"{sid}.elevation.reference 必须是 bottom/top/center")
                level = str(raw_elevation.get("evidence_level") or "确认")
                if level not in {"确认", "规则推算", "方案暂估"}:
                    fail(f"{sid}.elevation.evidence_level 无效")
                value = finite(raw_elevation.get("value"), f"{sid}.elevation.value")
                text(raw_elevation.get("source_id"), f"{sid}.elevation.source_id")
                segment["elevation"] = {"value": value, "reference": reference, "evidence_level": level,
                                        "source_id": raw_elevation["source_id"], "text": raw_elevation.get("text")}
            self.segments[sid] = segment
        self.fittings: list[dict[str, Any]] = []
        raw_fittings = data.get("fittings", [])
        if not isinstance(raw_fittings, list):
            fail("fittings 必须是数组")
        seen = set()
        for index, raw in enumerate(raw_fittings):
            if not isinstance(raw, dict):
                fail(f"fittings[{index}] 必须是对象")
            fid = text(raw.get("fitting_id"), f"fittings[{index}].fitting_id")
            if fid in seen:
                fail(f"管件重复: {fid}")
            seen.add(fid)
            kind = str(raw.get("kind") or "unknown")
            if kind not in FITTING_KINDS:
                fail(f"{fid}.kind 必须是 {sorted(FITTING_KINDS)} 之一")
            bbox = raw.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(num(v) for v in bbox) or bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                fail(f"{fid}.bbox 必须是 [xmin, ymin, xmax, ymax]")
            explicit_ends = raw.get("ends")
            if explicit_ends is not None:
                if not isinstance(explicit_ends, list) or not explicit_ends:
                    fail(f"{fid}.ends 必须是非空数组")
                for item in explicit_ends:
                    if not isinstance(item, dict) or item.get("segment_id") not in self.segments or item.get("end") not in {"start", "end"}:
                        fail(f"{fid}.ends 必须引用真实管段的 start/end")
            self.fittings.append({
                "fitting_id": fid, "kind": kind, "floor_id": text(raw.get("floor_id"), f"{fid}.floor_id"),
                "bbox": [float(v) for v in bbox], "source_id": raw.get("source_id"), "block_name": raw.get("block_name"),
                "explicit_ends": explicit_ends, "ends": [], "arms": [], "notes": [], "status": "unprocessed",
            })

    # ------------------------------------------------------------ geometry helpers
    def end_point(self, sid: str, which: str) -> list[float]:
        return self.segments[sid]["start"] if which == "start" else self.segments[sid]["end"]

    def outward(self, sid: str, which: str) -> list[float]:
        u = self.segments[sid]["u"]
        return [u[0], u[1]] if which == "end" else [-u[0], -u[1]]

    def project(self, segment: dict[str, Any], point: list[float]) -> tuple[float, float]:
        u = segment["u"]
        dx, dy = point[0] - segment["start"][0], point[1] - segment["start"][1]
        return dx * u[0] + dy * u[1], abs(-dx * u[1] + dy * u[0])

    def half_width(self, segment: dict[str, Any], default: float = 500.0) -> float:
        if segment.get("width") is not None:
            return segment["width"] / 2.0
        if segment.get("diameter") is not None:
            return segment["diameter"] / 2.0
        return default

    def center_elevation(self, segment: dict[str, Any]) -> tuple[float | None, str, str | None]:
        """Centre elevation in output units, its evidence level, and why it is missing."""
        elevation = segment.get("elevation")
        if elevation is None:
            return None, "未决", "无标高标注"
        value = elevation["value"]
        if elevation["reference"] == "center":
            return value, elevation["evidence_level"], None
        height = segment.get("height") if segment.get("diameter") is None else segment.get("diameter")
        if height is None:
            return None, "未决", f"标高为{elevation['reference']}但缺高度，无法换算管中心"
        half = height * self.factor / 2.0
        return (value + half if elevation["reference"] == "bottom" else value - half), elevation["evidence_level"], None


def build_connections(net: Network, result: dict[str, Any]) -> None:
    p = net.params
    ends = [(sid, which) for sid in net.segments for which in ("start", "end")]
    end_state: dict[tuple[str, str], str] = {}
    # 1. direct joints
    joints = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, b = ends[i], ends[j]
            if a[0] == b[0] or net.segments[a[0]]["floor_id"] != net.segments[b[0]]["floor_id"]:
                continue
            d = dist(net.end_point(*a), net.end_point(*b))
            if d <= p["end_snap_tolerance"]:
                joints.append({"a": list(a), "b": list(b), "distance": d})
                end_state[a] = end_state[b] = "joint"
    result["joints"] = joints
    # 2. fitting membership and duplicates
    duplicates = []
    kept: list[dict[str, Any]] = []
    for fitting in net.fittings:
        x1, y1, x2, y2 = fitting["bbox"]
        area = max((x2 - x1) * (y2 - y1), 1e-9)
        duplicate_of = None
        for other in kept:
            if other["kind"] != fitting["kind"] or other["floor_id"] != fitting["floor_id"]:
                continue
            ox1, oy1, ox2, oy2 = other["bbox"]
            ix = max(0.0, min(x2, ox2) - max(x1, ox1))
            iy = max(0.0, min(y2, oy2) - max(y1, oy1))
            if ix * iy / min(area, max((ox2 - ox1) * (oy2 - oy1), 1e-9)) >= p["fitting_overlap_duplicate"]:
                duplicate_of = other["fitting_id"]
                break
        if duplicate_of:
            fitting["status"] = "duplicate"
            fitting["duplicate_of"] = duplicate_of
            fitting["notes"].append(f"与 {duplicate_of} 同类且范围重合，按同一物理管件只计一次")
            duplicates.append(fitting)
            continue
        kept.append(fitting)
        if fitting["explicit_ends"] is not None:
            fitting["ends"] = [(item["segment_id"], item["end"]) for item in fitting["explicit_ends"]]
        else:
            tol = p["fitting_end_tolerance"]
            for sid, which in ends:
                if net.segments[sid]["floor_id"] != fitting["floor_id"]:
                    continue
                pt = net.end_point(sid, which)
                if x1 - tol <= pt[0] <= x2 + tol and y1 - tol <= pt[1] <= y2 + tol:
                    fitting["ends"].append((sid, which))
        for key in fitting["ends"]:
            end_state.setdefault(key, "fitting")
    net.fittings = kept
    result["duplicate_fittings"] = [{"fitting_id": f["fitting_id"], "duplicate_of": f["duplicate_of"], "kind": f["kind"]} for f in duplicates]
    # 3. accessory gaps between free collinear facing ends
    gaps = []
    free = [e for e in ends if e not in end_state]
    for i in range(len(free)):
        for j in range(i + 1, len(free)):
            a, b = free[i], free[j]
            sa, sb = net.segments[a[0]], net.segments[b[0]]
            if a[0] == b[0] or sa["floor_id"] != sb["floor_id"]:
                continue
            pa, pb = net.end_point(*a), net.end_point(*b)
            d = dist(pa, pb)
            if d < p["accessory_gap_min"] or d > p["accessory_gap_max"]:
                continue
            ua, ub = sa["u"], sb["u"]
            if abs(ua[0] * ub[1] - ua[1] * ub[0]) > p["collinear_cross_limit"]:
                continue
            offset = abs(-(pb[0] - pa[0]) * ua[1] + (pb[1] - pa[1]) * ua[0])
            if offset > p["end_snap_tolerance"]:
                continue
            oa = net.outward(*a)
            if (pb[0] - pa[0]) * oa[0] + (pb[1] - pa[1]) * oa[1] <= 0:
                continue
            gaps.append({"a": list(a), "b": list(b), "length": d})
    gaps.sort(key=lambda g: g["length"])
    chosen = []
    taken = set()
    for gap in gaps:
        ka, kb = tuple(gap["a"]), tuple(gap["b"])
        if ka in taken or kb in taken:
            continue
        taken.update({ka, kb})
        end_state[ka] = end_state[kb] = "gap"
        gap["gap_id"] = f"GAP-{len(chosen) + 1:03d}"
        gap["quantity_action"] = "include" if p["accessory_gap_policy"] == "include" else "exclude"
        gap["basis"] = "中心线共线、相向、断口在部件长度范围内：视为在线部件（阀门/软接）断口，拓扑续接；" + (
            "按口径计入长度" if gap["quantity_action"] == "include" else "部件所占长度不计（GB 50856）")
        gap["evidence_level"] = "规则推算"
        chosen.append(gap)
    result["accessory_gaps"] = chosen
    net.end_state = end_state
    net.ends = ends


def fitting_arms(net: Network, result: dict[str, Any]) -> None:
    p = net.params
    offset_groups: dict[tuple, dict[str, Any]] = {}
    for fitting in net.fittings:
        E = fitting["ends"]
        if not E:
            fitting["status"] = "unconnected"
            fitting["notes"].append("范围内没有本层中心线端点；管件未接入拓扑")
            continue
        pts = [(sid, which, net.end_point(sid, which), net.segments[sid]["u"]) for sid, which in E]
        if len(E) == 1:
            sid, which, e, u = pts[0]
            segment = net.segments[sid]
            od = net.outward(sid, which)
            if fitting["kind"] in {"tap", "tee", "cross"}:
                best = None
                for other in net.segments.values():
                    if other["segment_id"] == sid or other["floor_id"] != segment["floor_id"]:
                        continue
                    if abs(u[0] * other["u"][0] + u[1] * other["u"][1]) > 0.05:
                        continue
                    J = intersect(e, od, other["start"], other["u"], p["collinear_cross_limit"])
                    if J is None:
                        continue
                    along, _ = net.project(other, J)
                    if along < -p["fitting_end_tolerance"] or along > other["length"] + p["fitting_end_tolerance"]:
                        continue
                    dj = (J[0] - e[0]) * od[0] + (J[1] - e[1]) * od[1]
                    if dj <= 0 or dj > p["junction_max_arm"]:
                        continue
                    if best is None or dj < best[0]:
                        best = (dj, other["segment_id"], J)
                if best:
                    fitting["arms"].append({"segment_id": sid, "end": which, "length": best[0], "evidence_level": "规则推算",
                                            "basis": f"支管端点沿管向延伸至主管 {best[1]} 中心线交点（GB 50856 支管算至主管中心线交点）"})
                    fitting["main_segment_id"] = best[1]
                    fitting["junction"] = best[2]
                    fitting["status"] = "tap_into_main"
                    continue
                fitting["notes"].append("支管接头没有找到同层主管；按单端处理")
            x1, y1, x2, y2 = fitting["bbox"]
            ext = max((cx - e[0]) * od[0] + (cy - e[1]) * od[1] for cx in (x1, x2) for cy in (y1, y2))
            half = segment["width"] / 2.0 if segment.get("width") is not None else (segment["diameter"] / 2.0 if segment.get("diameter") is not None else None)
            plane = max(0.0, ext - half) if half is not None else max(0.0, ext)
            fitting["arms"].append({"segment_id": sid, "end": which, "length": plane,
                                    "evidence_level": "规则推算" if half is not None else "方案暂估",
                                    "basis": "单端管件：平面段取块范围沿管向伸出长度减半宽（竖向另列）" if half is not None else "单端管件：平面段取块范围沿管向伸出长度；缺宽度，未减半宽"})
            fitting["status"] = "single_end"
            fitting["plane_extension"] = ext
            continue
        if len(E) == 2:
            (s1, k1, e1, u1), (s2, k2, e2, u2) = pts
            J = intersect(e1, u1, e2, u2, p["collinear_cross_limit"])
            if J is not None and dist(J, e1) <= p["junction_max_arm"] and dist(J, e2) <= p["junction_max_arm"]:
                fitting["junction"] = J
                for sid, which, e, _ in pts:
                    fitting["arms"].append({"segment_id": sid, "end": which, "length": dist(J, e), "evidence_level": "规则推算",
                                            "basis": "两侧中心线延长线交点，各侧端点至交点"})
                fitting["status"] = "junction"
                continue
            d = dist(e1, e2)
            offset_perp = abs(-(e2[0] - e1[0]) * u1[1] + (e2[1] - e1[1]) * u1[0])
            coaxial = offset_perp <= p["end_snap_tolerance"] * 4
            for sid, which, e, _ in pts:
                fitting["arms"].append({"segment_id": sid, "end": which, "length": d / 2.0, "evidence_level": "规则推算",
                                        "basis": "两端共线或平行，无中心线交点：两侧各计端点间距的一半（变径两侧各算一半）"})
            if fitting["kind"] in {"elbow", "offset"} or (fitting["kind"] == "unknown" and coaxial):
                key = tuple(sorted([(s1, k1), (s2, k2)]))
                group = offset_groups.setdefault(key, {"ends": [list(k) for k in key], "members": [], "plan_gap": d, "floor_id": fitting["floor_id"]})
                if group["members"]:
                    fitting["notes"].append("上下翻组内重复弯头，平面段只计一次")
                    fitting["arms"] = []
                    fitting["status"] = "offset_member_duplicate"
                else:
                    fitting["status"] = "offset_candidate"
                    fitting["notes"].append("两端同轴的弯头：上翻/下翻（竖向错位）代表件，竖向长度按两侧标高差另列")
                group["members"].append(fitting["fitting_id"])
                fitting["offset_group"] = list(key)
            else:
                fitting["status"] = "coaxial_pair"
            continue
        best_pair = None
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                cross = abs(pts[i][3][0] * pts[j][3][1] - pts[i][3][1] * pts[j][3][0])
                if best_pair is None or cross < best_pair[0]:
                    best_pair = (cross, i, j)
        i, j = best_pair[1], best_pair[2]
        branches = [pt for index, pt in enumerate(pts) if index not in (i, j)]
        J = intersect(pts[i][2], pts[i][3], branches[0][2], branches[0][3], p["collinear_cross_limit"]) if branches else None
        if J is None:
            J = [sum(pt[2][0] for pt in pts) / len(pts), sum(pt[2][1] for pt in pts) / len(pts)]
            fitting["notes"].append("支管与主管平行，交点取各端点形心")
        fitting["junction"] = J
        for sid, which, e, _ in pts:
            fitting["arms"].append({"segment_id": sid, "end": which, "length": dist(J, e), "evidence_level": "规则推算",
                                    "basis": "主管中心线与支管中心线交点，各端点至交点（支管算至主管中心线交点）"})
        fitting["status"] = "junction"
    # open taps: free ends landing on a main without any fitting block
    open_taps = []
    for sid, which in net.ends:
        if (sid, which) in net.end_state:
            continue
        segment = net.segments[sid]
        e = net.end_point(sid, which)
        od = net.outward(sid, which)
        best = None
        for other in net.segments.values():
            if other["segment_id"] == sid or other["floor_id"] != segment["floor_id"]:
                continue
            if abs(segment["u"][0] * other["u"][0] + segment["u"][1] * other["u"][1]) > 0.05:
                continue
            J = intersect(e, od, other["start"], other["u"], p["collinear_cross_limit"])
            if J is None:
                continue
            along, _ = net.project(other, J)
            if along < 0 or along > other["length"]:
                continue
            dj = (J[0] - e[0]) * od[0] + (J[1] - e[1]) * od[1]
            limit = net.half_width(other) + p["tap_margin"]
            if 0 < dj <= limit and (best is None or dj < best[0]):
                best = (dj, other["segment_id"], J)
        if best:
            open_taps.append({"tap_id": f"TAP-{len(open_taps) + 1:03d}", "segment_id": sid, "end": which, "main_segment_id": best[1],
                              "junction": best[2], "length": best[0], "evidence_level": "规则推算",
                              "basis": "自由端垂直落在主管带宽内：无管件块的支管接入，端点至主管中心线交点"})
            net.end_state[(sid, which)] = "tap"
    result["open_taps"] = open_taps
    result["offset_groups"] = list(offset_groups.values())
    net.offset_groups = offset_groups


def build_runs(net: Network) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Continuity for elevation propagation: joints, gaps, taps and fittings except vertical candidates."""
    parent = {sid: sid for sid in net.segments}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    adjacency: dict[str, set[str]] = defaultdict(set)

    def link(a, b):
        parent[find(a)] = find(b)
        adjacency[a].add(b)
        adjacency[b].add(a)

    for item in net.result["joints"]:
        link(item["a"][0], item["b"][0])
    for item in net.result["accessory_gaps"]:
        link(item["a"][0], item["b"][0])
    for item in net.result["open_taps"]:
        link(item["segment_id"], item["main_segment_id"])
    for fitting in net.fittings:
        if fitting["status"] in {"offset_candidate", "offset_member_duplicate", "single_end"}:
            continue
        ids = [sid for sid, _ in fitting["ends"]]
        if fitting.get("main_segment_id"):
            ids.append(fitting["main_segment_id"])
        for a in ids:
            for b in ids:
                if a != b:
                    link(a, b)
    return {sid: find(sid) for sid in net.segments}, adjacency


def propagate_elevation(net: Network, adjacency: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    """Nearest tagged segment within the run by hop count; conflicting equal-hop values stay unresolved."""
    resolved: dict[str, dict[str, Any]] = {}
    for sid, segment in net.segments.items():
        own, level, why = net.center_elevation(segment)
        if own is not None:
            resolved[sid] = {"value": own, "evidence_level": level, "basis": f"本段标高标注 {segment['elevation']['source_id']}（{segment['elevation']['reference']}）", "hops": 0}
            continue
        seen = {sid}
        frontier = deque([(sid, 0)])
        found = None
        while frontier and found is None:
            hop = frontier[0][1]
            level_nodes = []
            while frontier and frontier[0][1] == hop:
                level_nodes.append(frontier.popleft()[0])
            tagged = []
            for node in level_nodes:
                value, lvl, _ = net.center_elevation(net.segments[node])
                if value is not None and node != sid:
                    tagged.append((node, value, lvl))
            if tagged:
                values = sorted({round(v, 6) for _, v, _ in tagged})
                if len(values) == 1:
                    node, value, lvl = tagged[0]
                    derived = "方案暂估" if lvl == "方案暂估" else "规则推算"
                    found = {"value": value, "evidence_level": derived, "basis": f"沿连续管路传播自 {node} 的标高（{hop} 跳，无变径/支管竖向）", "hops": hop}
                else:
                    found = {"value": None, "evidence_level": "未决", "basis": f"同距离多个不同标高 {values}，无法判定", "hops": hop}
                break
            for node in level_nodes:
                for nxt in adjacency.get(node, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        frontier.append((nxt, hop + 1))
        resolved[sid] = found or {"value": None, "evidence_level": "未决", "basis": why or "连续管路内没有标高标注", "hops": None}
    return resolved


def vertical_items(net: Network, elevations: dict[str, dict[str, Any]], result: dict[str, Any]) -> None:
    p = net.params
    items = []
    pending = result["pending"]

    def combine(levels: list[str]) -> str:
        if "未决" in levels:
            return "未决"
        if "方案暂估" in levels:
            return "方案暂估"
        return "规则推算"

    # 1. in-floor offsets
    for group in net.offset_groups.values():
        (sa, _), (sb, _) = group["ends"]
        ea, eb = elevations[sa], elevations[sb]
        segment = net.segments[sa]
        level = combine([ea["evidence_level"], eb["evidence_level"]])
        length = abs(ea["value"] - eb["value"]) if level != "未决" else None
        items.append({"vertical_id": f"VERT-{len(items) + 1:03d}", "kind": "in_floor_offset", "floor_id": group["floor_id"], "fitting_ids": group["members"],
                      "segment_ids": [sa, sb], "system": segment["system"], "spec": spec_of(segment), "length": length,
                      "evidence_level": level, "basis": f"{sa}: {ea['value']}（{ea['basis']}） / {sb}: {eb['value']}（{eb['basis']}）；上下翻按两侧管中心标高差"})
    # 2. single-ended elbows: branch rising/dropping onto a main of the same floor
    singles = [f for f in net.fittings if f["status"] == "single_end"]
    for fitting in singles:
        sid, which = fitting["ends"][0]
        segment = net.segments[sid]
        x1, y1, x2, y2 = fitting["bbox"]
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        partner = None
        for other in net.segments.values():
            if other["segment_id"] == sid or other["floor_id"] != segment["floor_id"]:
                continue
            along, perp = net.project(other, [cx, cy])
            if -p["band_margin"] <= along <= other["length"] + p["band_margin"] and perp <= net.half_width(other, 400.0) + p["band_margin"]:
                partner = other
                break
        if partner is None:
            continue
        ea, eb = elevations[sid], elevations[partner["segment_id"]]
        level = combine([ea["evidence_level"], eb["evidence_level"]])
        length = abs(ea["value"] - eb["value"]) if level != "未决" else None
        items.append({"vertical_id": f"VERT-{len(items) + 1:03d}", "kind": "branch_to_main", "floor_id": fitting["floor_id"], "fitting_ids": [fitting["fitting_id"]],
                      "segment_ids": [sid, partner["segment_id"]], "system": segment["system"], "spec": spec_of(segment), "length": length,
                      "evidence_level": level, "basis": f"支管 {sid}: {ea['value']}（{ea['basis']}） -> 主管 {partner['segment_id']}: {eb['value']}（{eb['basis']}）；支管竖向接主管按管中心标高差"})
        fitting["vertical_id"] = items[-1]["vertical_id"]
    # 3. inter-floor risers between stacked single-ended elbows
    remaining = [f for f in singles if not f.get("vertical_id")]
    floors = sorted(net.floors.values(), key=lambda f: f["order"])
    for lower, upper in zip(floors, floors[1:]):
        lowers = [f for f in remaining if f["floor_id"] == lower["floor_id"] and not f.get("vertical_id")]
        for fitting in [f for f in remaining if f["floor_id"] == upper["floor_id"] and not f.get("vertical_id")]:
            bu = fitting["bbox"]
            cu = [(bu[0] + bu[2]) / 2.0 - upper["plan_offset"][0], (bu[1] + bu[3]) / 2.0 - upper["plan_offset"][1]]
            best = None
            for candidate in lowers:
                if candidate.get("vertical_id"):
                    continue
                bl = candidate["bbox"]
                cl = [(bl[0] + bl[2]) / 2.0 - lower["plan_offset"][0], (bl[1] + bl[3]) / 2.0 - lower["plan_offset"][1]]
                d = dist(cu, cl)
                if d <= p["riser_match_distance"] and (best is None or d < best[0]):
                    best = (d, candidate)
            if best is None:
                continue
            candidate = best[1]
            su, sl = fitting["ends"][0][0], candidate["ends"][0][0]
            eu, el = elevations[su], elevations[sl]
            levels = [eu["evidence_level"], el["evidence_level"], upper["level_evidence_level"], lower["level_evidence_level"]]
            if upper["level"] is None or lower["level"] is None:
                levels.append("未决")
            level = combine(levels)
            length = abs((upper["level"] + eu["value"]) - (lower["level"] + el["value"])) if level != "未决" else None
            vid = f"VERT-{len(items) + 1:03d}"
            items.append({"vertical_id": vid, "kind": "inter_floor_riser", "floor_id": f"{lower['floor_id']}-{upper['floor_id']}",
                          "fitting_ids": [candidate["fitting_id"], fitting["fitting_id"]], "segment_ids": [sl, su],
                          "system": net.segments[su]["system"], "spec": spec_of(net.segments[su]), "length": length, "evidence_level": level,
                          "basis": f"{upper['floor_id']} {su} 中心 {eu['value']} + 层标高 {upper['level']} / {lower['floor_id']} {sl} 中心 {el['value']} + 层标高 {lower['level']}；平面错位 {best[0]:.0f}"})
            fitting["vertical_id"] = candidate["vertical_id"] = vid
    # 4. single ends with unknown destination are pending, never estimated
    for fitting in singles:
        if fitting.get("vertical_id"):
            continue
        sid, which = fitting["ends"][0]
        segment = net.segments[sid]
        pending.append({"kind": "single_end_destination_unknown", "fitting_id": fitting["fitting_id"], "segment_id": sid, "end": which,
                        "floor_id": fitting["floor_id"], "system": segment["system"], "spec": spec_of(segment),
                        "center_elevation": elevations[sid]["value"], "plane_extension": fitting.get("plane_extension"),
                        "reason": "单端管件去向不明：无同层主管带宽、无相邻层对应弯头；竖向长度待查系统图/剖面"})
    for item in items:
        if item["evidence_level"] == "未决":
            pending.append({"kind": f"vertical_{item['kind']}_unresolved", "vertical_id": item["vertical_id"], "fitting_ids": item["fitting_ids"],
                            "segment_ids": item["segment_ids"], "floor_id": item["floor_id"], "reason": item["basis"]})
    result["vertical_items"] = items


def summarize(net: Network, elevations: dict[str, dict[str, Any]], result: dict[str, Any]) -> None:
    f = net.factor
    summary: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    surface: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    part_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def add(part: str, segment: dict[str, Any], level: str, length_units: float) -> None:
        metres = length_units * f
        summary[segment["system"]][level][spec_of(segment)] += metres
        part_totals[part][level] += metres
        per = perimeter_m(segment, f)
        if per is not None:
            surface[segment["system"]][level][spec_of(segment)] += per * metres

    straight = []
    for segment in net.segments.values():
        add("straight", segment, "确认", segment["length"])
        straight.append({"segment_id": segment["segment_id"], "floor_id": segment["floor_id"], "system": segment["system"], "spec": spec_of(segment),
                         "length": segment["length"] * f, "evidence_level": "确认", "size_evidence_level": segment["size_evidence_level"],
                         "center_elevation": elevations[segment["segment_id"]]["value"], "elevation_evidence_level": elevations[segment["segment_id"]]["evidence_level"],
                         "elevation_basis": elevations[segment["segment_id"]]["basis"], "source_ids": segment["source_ids"]})
    result["straight_segments"] = straight
    fittings_out = []
    for fitting in net.fittings:
        arms = []
        for arm in fitting["arms"]:
            segment = net.segments[arm["segment_id"]]
            add("fitting", segment, arm["evidence_level"], arm["length"])
            arms.append({**arm, "length": arm["length"] * f, "system": segment["system"], "spec": spec_of(segment)})
        fittings_out.append({"fitting_id": fitting["fitting_id"], "kind": fitting["kind"], "floor_id": fitting["floor_id"], "status": fitting["status"],
                             "block_name": fitting.get("block_name"), "source_id": fitting.get("source_id"),
                             "ends": [{"segment_id": s, "end": w} for s, w in fitting["ends"]], "junction": fitting.get("junction"),
                             "main_segment_id": fitting.get("main_segment_id"), "arms": arms, "centerline_length": sum(a["length"] for a in arms),
                             "vertical_id": fitting.get("vertical_id"), "notes": fitting["notes"]})
        if fitting["status"] == "unconnected":
            result["pending"].append({"kind": "fitting_unconnected", "fitting_id": fitting["fitting_id"], "floor_id": fitting["floor_id"],
                                      "reason": "管件块范围内没有中心线端点；核对块位置、图层或中心线是否缺绘"})
    result["fittings"] = fittings_out
    for tap in result["open_taps"]:
        segment = net.segments[tap["segment_id"]]
        add("fitting", segment, tap["evidence_level"], tap["length"])
        tap["length"] = tap["length"] * f
        tap["system"], tap["spec"] = segment["system"], spec_of(segment)
    for gap in result["accessory_gaps"]:
        segment = net.segments[gap["a"][0]]
        gap["system"], gap["spec"] = segment["system"], spec_of(segment)
        if gap["quantity_action"] == "include":
            add("accessory_gap", segment, gap["evidence_level"], gap["length"])
        gap["length"] = gap["length"] * f
    for item in result["vertical_items"]:
        if item["length"] is None:
            continue
        segment = net.segments[item["segment_ids"][0]]
        metres = item["length"]
        summary[segment["system"]][item["evidence_level"]][spec_of(segment)] += metres
        part_totals["vertical"][item["evidence_level"]] += metres
        per = perimeter_m(segment, f)
        if per is not None:
            surface[segment["system"]][item["evidence_level"]][spec_of(segment)] += per * metres
    result["length_summary"] = {system: {level: dict(sorted(specs.items())) for level, specs in levels.items()} for system, levels in summary.items()}
    result["theoretical_surface_summary"] = {system: {level: dict(sorted(specs.items())) for level, specs in levels.items()} for system, levels in surface.items()}
    result["part_totals"] = {part: dict(levels) for part, levels in part_totals.items()}
    result["metrics"] = {
        "straight_length": part_totals["straight"]["确认"],
        "fitting_length_rule": part_totals["fitting"]["规则推算"],
        "fitting_length_scenario": part_totals["fitting"]["方案暂估"],
        "vertical_length_rule": part_totals["vertical"]["规则推算"],
        "vertical_length_scenario": part_totals["vertical"]["方案暂估"],
        "accessory_gap_count": len(result["accessory_gaps"]),
        "accessory_gap_length_excluded": sum(g["length"] for g in result["accessory_gaps"] if g["quantity_action"] == "exclude"),
        "pending_count": len(result["pending"]),
        "measured_length_rule": part_totals["straight"]["确认"] + part_totals["fitting"]["规则推算"] + part_totals["vertical"]["规则推算"] + part_totals["accessory_gap"]["规则推算"],
    }


def infer(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != INPUT_SCHEMA:
        fail(f"schema_version 必须为 {INPUT_SCHEMA}")
    net = Network(data)
    result: dict[str, Any] = {"schema_version": RESULT_SCHEMA, "engine_version": VERSION, "status": "REFERENCE_ONLY",
                              "package_id": data.get("package_id"), "unit": net.output_unit, "pending": []}
    net.result = result
    build_connections(net, result)
    fitting_arms(net, result)
    _, adjacency = build_runs(net)
    elevations = propagate_elevation(net, adjacency)
    vertical_items(net, elevations, result)
    summarize(net, elevations, result)
    result["parameters"] = {**net.params, "parameter_basis": data["parameters"]["parameter_basis"]}
    result["counts"] = {"segments": len(net.segments), "fittings": len(net.fittings), "duplicate_fittings": len(result["duplicate_fittings"]),
                        "joints": len(result["joints"]), "accessory_gaps": len(result["accessory_gaps"]), "open_taps": len(result["open_taps"]),
                        "vertical_items": len(result["vertical_items"]), "pending": len(result["pending"]),
                        "fitting_status": dict(sorted(Counter(f["status"] for f in result["fittings"]).items()))}
    result["business_adoption_allowed"] = False
    result["meaning"] = ("直管为已绘中心线确认量；管件中心线与竖向为按块几何、端口和标高标注的规则推算量，含假定者为方案暂估；"
                         "单端去向不明和缺标高的部分列待办，不以零或经验比例代替。参考值须经专业复核后才能进入成果。")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="风管管件中心线与竖向段推算（参考值）")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            fail("输入顶层必须是对象")
        result = infer(data)
        if args.output.resolve() == args.input.resolve():
            fail("输出不能覆盖输入")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], **result["metrics"], "output": str(args.output)}, ensure_ascii=False))
        return 0
    except (OSError, InputError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "stop": True, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
