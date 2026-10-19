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
import copy
import heapq
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from qto_cad_source_contract import canonical_scale

VERSION = "qto-duct-fitting-vertical/1.4.0"
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
    "fitting_overlap_duplicate": 0.7,   # overlap generates identity candidates, never proves identity alone
    "accessory_gap_policy": "exclude",  # exclude (GB 50856 部件所占长度不计) or include
    # Single-ended lengths require an evidenced centre/body endpoint or turn geometry.
    # Legacy full/half-width readings are reported only as diagnostics, never adopted.
    "single_end_plane_policy": "geometry_evidence",
}
POLICY_PARAMETERS = {
    "accessory_gap_policy": {"exclude", "include"},
    "single_end_plane_policy": {"geometry_evidence", "extension_minus_half_width", "extension_full"},
}
ELEVATION_ORIGIN_LABELS = {"tag": "本段标高标注", "host_propagated": "宿主传播标高", "system_diagram": "系统图标高", "section": "剖面标高"}
ELEVATION_ORIGINS = set(ELEVATION_ORIGIN_LABELS)
VERTICAL_LINK_KINDS = {"segment_to_segment", "single_end_to_segment"}
LENGTH_PENDING_KINDS = {"fitting_unconnected", "fitting_system_ambiguous", "single_end_plane_unresolved", "single_end_turn_plane_unknown",
                        "single_end_horizontal_destination_unknown", "single_end_destination_unknown", "fitting_duplicate_identity_unresolved"}


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


def single_end_plane(net, fitting, sid, which):
    """Measure to an evidenced centre/body endpoint. A bbox alone is not a centreline.

    Old width/full readings remain diagnostics, never a fallback quantity. All
    distances supplied here are raw drawing units from the straight end outward.
    """
    segment = net.segments[sid]
    e, od = net.end_point(sid, which), net.outward(sid, which)
    x1, y1, x2, y2 = fitting['bbox']
    ext = max((x-e[0])*od[0] + (y-e[1])*od[1] for x in (x1,x2) for y in (y1,y2))
    # The bbox is axis-aligned, so its far corner overshoots the block's far face along the pipe.
    # The block touches the bbox side x=x2 (and y=y2), so the overshoot is at most
    # min(height*|uy|, width*|ux|) for any block shape: 0 on an exact axis, growing with block size
    # and angle. The bbox stands in for the far face only while that bound is drawing noise; the
    # pipe-parallelism parameter is no evidence of it.
    error_bound = min((y2-y1)*abs(od[1]), (x2-x1)*abs(od[0]))
    axis_aligned = error_bound <= net.params['end_snap_tolerance']
    half_width = (segment.get('width') if segment.get('width') is not None else segment.get('diameter'))
    half_width = half_width / 2. if half_width is not None else None
    scenarios = {'extension_full': max(0.,ext), 'extension_minus_half_width': max(0.,ext-half_width) if half_width is not None else None}
    result = {'length': None, 'method': None, 'scenarios': scenarios, 'extension': ext, 'half_width': half_width,
              'extension_basis': 'axis_aligned_bbox' if axis_aligned else 'rotated_bbox_upper_bound',
              'extension_error_bound': error_bound}
    g = fitting.get('single_end_geometry')
    if g is None:
        return {**result, 'reason': '缺 single_end_geometry：块外包框不能证明管件中心或本体端点；旧整段/减半宽读法只作诊断'}
    label = fitting['fitting_id'] + '.single_end_geometry'
    relation = g.get('endpoint_relation')
    if relation not in {'at_connection', 'already_at_center', 'reversed', 'unknown'}:
        fail(f'{label}.endpoint_relation 必须明确为 at_connection/already_at_center/reversed/unknown')
    if relation in {'reversed', 'unknown'}:
        out = {**result, 'reason': '块方向相反或管段端点与管件的关系未明，须核对连接点与中心线，不按外包框给数'}
        if relation == 'reversed' and 'riser_projection' in g:
            # A reversed block may still show where the riser stands; that locates a riser pairing
            # (scenario only), never a plane length.
            if g.get('turn_plane') != 'vertical':
                fail(f'{label}: riser_projection 必须声明 turn_plane=vertical')
            span = g['riser_projection']
            if not isinstance(span, list) or len(span) != 2:
                fail(f'{label}.riser_projection 必须是沿来向的两条立管投影边距离 [近边,远边]')
            near, far = [finite(v, label+'.riser_projection', 0.) for v in span]
            if far <= near or far > ext + net.params['fitting_end_tolerance']:
                out['reason'] += '；所给立管投影两边次序或范围不符，不能用于配对'
            else:
                out['pairing_offset'] = (near+far)/2.
        return out
    tol = net.params['fitting_end_tolerance']
    snap = net.params['end_snap_tolerance']
    extent = ext if axis_aligned else None   # block far face along the pipe, when known
    if 'extent_along_pipe' in g:
        given = finite(g['extent_along_pipe'], label+'.extent_along_pipe', 0.)
        # where the bbox stands in for the far face the two must agree; otherwise it is only an upper bound
        if given <= 0 or given > ext + tol or (axis_aligned and given < ext - error_bound - tol):
            return {**result, 'reason': 'extent_along_pipe 与块范围不符：外包框可代用时须与外包框伸出一致，否则不能超过外包框上限'}
        extent = given
        result.update(extension=given, extension_basis='host_measured_along_pipe', extension_error_bound=0.)
    bound = extent if extent is not None else ext
    candidates = {}
    if 'centerline_point' in g:
        c = point2(g['centerline_point'], label+'.centerline_point')
        along = (c[0]-e[0])*od[0] + (c[1]-e[1])*od[1]
        perp = abs((c[0]-e[0])*od[1] - (c[1]-e[1])*od[0])
        # a centre within the end snap tolerance behind the drawn end is drawing noise, not a reversal
        if perp > tol or along < -snap or along > bound + tol or not (x1-tol <= c[0] <= x2+tol and y1-tol <= c[1] <= y2+tol):
            return {**result, 'reason': '所给中心/本体端点不在管向前方、块范围或中心线上，须核对方向与已伸入长度'}
        candidates['centerline_point'] = max(0., along)
    if relation == 'already_at_center':
        if 'centerline_point' not in candidates or candidates['centerline_point'] > snap:
            return {**result, 'reason': 'already_at_center 必须有与管段端点吻合的 centerline_point，不能仅声明后置零'}
        candidates = {'centerline_point': 0.}
    elif fitting['kind'] in {'reducer', 'rect_to_round'}:
        if g.get('turn_plane') == 'vertical' or 'riser_projection' in g or 'riser_depth' in g:
            fail(f'{label}: 变径/天圆地方是在线本体，不套用立管半高或竖向转弯读法')
        if 'body_length' in g:
            length = finite(g['body_length'], label+'.body_length', 0.)
            if length <= 0 or length > bound + tol:
                return {**result, 'reason': '本体长度必须为正且不超出连接点前方的本体范围'}
            candidates['body_length'] = length
    else:
        if 'riser_projection' in g:
            if g.get('turn_plane') != 'vertical':
                fail(f'{label}: riser_projection 必须声明 turn_plane=vertical')
            span = g['riser_projection']
            if not isinstance(span, list) or len(span) != 2:
                fail(f'{label}.riser_projection 必须是沿来向的两条立管投影边距离 [近边,远边]')
            near, far = [finite(v, label+'.riser_projection', 0.) for v in span]
            if far <= near or far > bound + tol:
                return {**result, 'reason': '立管投影两边次序或范围不符，须核对块方向'}
            candidates['riser_projection_midpoint'] = (near+far)/2.
        if 'flange_allowance' in g:
            if g.get('turn_plane') != 'vertical':
                fail(f'{label}: 外皮扣减必须声明 turn_plane=vertical；不猜转弯平面')
            depth = g.get('riser_depth', segment.get('diameter') if segment.get('diameter') is not None else segment.get('height'))
            if depth is None:
                return {**result, 'reason': '缺转弯后立管沿来向的尺寸 riser_depth/管高，不能用管宽代替'}
            depth = finite(depth, label+'.riser_depth', 0.)
            flange = finite(g['flange_allowance'], label+'.flange_allowance', 0.)
            if extent is None:
                return {**result, 'reason': '管段不平行于坐标轴，外包框伸出可能比沿管向伸出多出 extension_error_bound；外皮扣减须给 extent_along_pipe（沿管向量得的块远端距离）'}
            length = extent - depth/2. - flange
            if depth <= 0 or length < 0:
                return {**result, 'reason': '伸出长度不足以扣除半管高和远端法兰，方向或端点关系待核；不能裁成零'}
            candidates['outer_face_minus_half_depth'] = length
    if not candidates:
        return {**result, 'reason': '须提供中心点、立管投影两边、带法兰依据的半管高扣减，或在线件本体长度'}
    scenarios.update(candidates)
    if max(candidates.values()) - min(candidates.values()) > tol:
        return {**result, 'reason': '多种几何依据推出的中心/本体长度互相冲突，须复核后再计量'}
    method = next(k for k in ('centerline_point','riser_projection_midpoint','outer_face_minus_half_depth','body_length') if k in candidates)
    return {**result, 'length': candidates[method], 'method': method, 'reason': None}


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
            if key in POLICY_PARAMETERS:
                if value not in POLICY_PARAMETERS[key]:
                    fail(f"{key} 必须是 {' 或 '.join(sorted(POLICY_PARAMETERS[key]))}")
                self.params[key] = value
            else:
                self.params[key] = finite(value, f"parameters.{key}", 0.0)
        if self.params["accessory_gap_max"] <= self.params["accessory_gap_min"]:
            fail("accessory_gap_max 必须大于 accessory_gap_min")
        text(raw_params.get("parameter_basis"), "parameters.parameter_basis")
        self.unit = text(data.get("unit"), "unit")
        self.output_unit = str(data.get("output_unit") or "m")
        self.factor = (1.0 if self.unit == "raw_DU" else canonical_scale(self.unit) / canonical_scale(self.output_unit))
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
        if not isinstance(raw_segments, list) or (not raw_segments and not data.get("drawn_curve_segments") and not data.get("riser_projection_candidates") and "coverage" not in data):
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
                "length_evidence_level": str(raw.get("length_evidence_level") or "确认"),
                "source_ids": list(raw.get("source_ids") or []),
                "space_id": raw.get("space_id"),
                "source_plane_z": raw["start"][2] if raw.get("source_geometry_refs") and len(raw["start"])==3 else None,
                "elevation": None,
            }
            if segment["length_evidence_level"] not in LEVELS[:3]:
                fail(f"{sid}.length_evidence_level 无效")
            if segment["size_evidence_level"] not in LEVELS:
                fail(f"{sid}.size_evidence_level 必须是 {LEVELS} 之一")
            raw_elevation = raw.get("elevation")
            if raw_elevation is not None:
                if not isinstance(raw_elevation, dict):
                    fail(f"{sid}.elevation 必须是对象")
                reference = str(raw_elevation.get("reference") or "")
                if reference not in ELEVATION_REFERENCES:
                    fail(f"{sid}.elevation.reference 必须是 bottom/top/center")
                origin = str(raw_elevation.get("origin") or "tag")
                if origin not in ELEVATION_ORIGINS:
                    fail(f"{sid}.elevation.origin 必须是 {sorted(ELEVATION_ORIGINS)} 之一")
                level = str(raw_elevation.get("evidence_level") or ("确认" if origin == "tag" else ""))
                if level not in {"确认", "规则推算", "方案暂估"}:
                    fail(f"{sid}.elevation.evidence_level 无效（origin={origin} 时必须显式给出）")
                if origin == "host_propagated" and level == "确认":
                    fail(f"{sid}.elevation：宿主沿管路传播的标高不是本段标注，evidence_level 只能是 规则推算 或 方案暂估")
                value = finite(raw_elevation.get("value"), f"{sid}.elevation.value")
                if raw_elevation.get('unit') is not None:
                    value *= canonical_scale(raw_elevation['unit']) / canonical_scale(self.output_unit)
                text(raw_elevation.get("source_id"), f"{sid}.elevation.source_id")
                segment["elevation"] = {"value": value, "reference": reference, "evidence_level": level, "origin": origin,
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
            identity = raw.get("physical_identity")
            if identity is not None:
                if not isinstance(identity, dict):
                    fail(f"{fid}.physical_identity 必须是含来源、实例编号和依据的对象")
                identity = {key: text(identity.get(key), f"{fid}.physical_identity.{key}")
                            for key in ("source_id", "instance_id", "basis")}
            self.fittings.append({
                "fitting_id": fid, "kind": kind, "floor_id": text(raw.get("floor_id"), f"{fid}.floor_id"),
                "bbox": [float(v) for v in bbox], "source_id": raw.get("source_id"), "block_name": raw.get("block_name"),
                "explicit_ends": explicit_ends, "ends": [], "arms": [], "notes": [], "status": "unprocessed",
                "system": text(raw['system'], f'{fid}.system') if raw.get('system') is not None else None,
                "single_end_geometry": raw.get('single_end_geometry'),
                "physical_identity": identity,
            })
            geometry = raw.get('single_end_geometry')
            if geometry is not None:
                if not isinstance(geometry, dict):
                    fail(f'{fid}.single_end_geometry 必须是对象')
                text(geometry.get('source_id'), f'{fid}.single_end_geometry.source_id')
                text(geometry.get('basis'), f'{fid}.single_end_geometry.basis')
                if geometry.get('turn_plane') not in (None, 'vertical', 'horizontal'):
                    fail(f'{fid}.single_end_geometry.turn_plane 无效')
        fitting_ids = {f["fitting_id"] for f in self.fittings}
        self.vertical_links: list[dict[str, Any]] = []
        raw_links = data.get("vertical_links", [])
        if not isinstance(raw_links, list):
            fail("vertical_links 必须是数组")
        link_ids: set[str] = set()
        for index, raw in enumerate(raw_links):
            if not isinstance(raw, dict):
                fail(f"vertical_links[{index}] 必须是对象")
            lid = text(raw.get("link_id"), f"vertical_links[{index}].link_id")
            if lid in link_ids:
                fail(f"竖向连接重复: {lid}")
            link_ids.add(lid)
            kind = raw.get("kind")
            if kind not in VERTICAL_LINK_KINDS:
                fail(f"{lid}.kind 必须是 {sorted(VERTICAL_LINK_KINDS)} 之一")
            target = text(raw.get("to_segment_id"), f"{lid}.to_segment_id")
            if target not in self.segments:
                fail(f"{lid}.to_segment_id 不是已登记管段")
            link = {"link_id": lid, "kind": kind, "to_segment_id": target, "source_id": text(raw.get("source_id"), f"{lid}.source_id"),
                    "basis": text(raw.get("basis"), f"{lid}.basis"), "evidence_level": str(raw.get("evidence_level") or "规则推算")}
            if link["evidence_level"] not in {"确认", "规则推算", "方案暂估"}:
                fail(f"{lid}.evidence_level 无效")
            if kind == "segment_to_segment":
                link["segment_id"] = text(raw.get("segment_id"), f"{lid}.segment_id")
                if link["segment_id"] not in self.segments or link["segment_id"] == target:
                    fail(f"{lid}.segment_id 必须是不同于 to_segment_id 的已登记管段")
            else:
                link["fitting_id"] = text(raw.get("fitting_id"), f"{lid}.fitting_id")
                if link["fitting_id"] not in fitting_ids:
                    fail(f"{lid}.fitting_id 不是已登记管件")
            anchors = raw.get("connection_ends")
            if anchors is not None:
                if kind != "segment_to_segment" or not isinstance(anchors, list) or not 1 <= len(anchors) <= 2:
                    fail(f"{lid}.connection_ends 仅用于segment_to_segment，须为1或2个有据管段端口")
                allowed = {link["segment_id"], target}
                seen_segments = set()
                link["connection_ends"] = []
                for anchor in anchors:
                    if (not isinstance(anchor, dict) or anchor.get("segment_id") not in allowed
                            or anchor.get("end") not in {"start", "end"} or anchor["segment_id"] in seen_segments):
                        fail(f"{lid}.connection_ends 必须引用连接两侧互不重复的真实管段start/end")
                    seen_segments.add(anchor["segment_id"])
                    link["connection_ends"].append((anchor["segment_id"], anchor["end"]))
            self.vertical_links.append(link)

    # ------------------------------------------------------------ geometry helpers
    def same_source_plane(self, a, b):
        if a.get("space_id") != b.get("space_id"):
            return False
        za,zb=a.get("source_plane_z"),b.get("source_plane_z")
        if za is None or zb is None:return za is None and zb is None
        return abs(za-zb)<=self.params["end_snap_tolerance"]

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
    joints, contacts = [], []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, b = ends[i], ends[j]
            if a[0] == b[0] or net.segments[a[0]]["floor_id"] != net.segments[b[0]]["floor_id"]:
                continue
            if not net.same_source_plane(net.segments[a[0]],net.segments[b[0]]):
                continue
            d = dist(net.end_point(*a), net.end_point(*b))
            if d > p["end_snap_tolerance"]:
                continue
            sa, sb = net.segments[a[0]]["system"], net.segments[b[0]]["system"]
            if sa != sb:
                # never linked, but a coincident end of another system may be a mislabelled system
                contacts.append({"a": list(a), "b": list(b), "distance": d, "systems": [sa, sb]})
                continue
            if frozenset((a,b)) in getattr(net,'curve_blocked_endpoint_pairs',set()):
                result['pending'].append({'kind':'curve_port_legacy_joint_blocked','a':list(a),'b':list(b),
                    'reason':'含曲线复杂端口身份未决；禁止仅看剩余两条直线建立同路标高传播'})
                continue
            joints.append({"a": list(a), "b": list(b), "distance": d})
            end_state[a] = end_state[b] = "joint"
    result["joints"] = joints
    # A bare endpoint tee/cross has no fitting block to provide main/branch metadata.
    # Find its coincident-end node first; collinear opposite-facing arms continue one run,
    # while the other directions cross a branch boundary. Two-arm bends remain continuous.
    endpoint_graph = defaultdict(set)
    for joint in joints:
        a, b = tuple(joint['a']), tuple(joint['b'])
        endpoint_graph[a].add(b)
        endpoint_graph[b].add(a)
    visited = set()
    for seed in endpoint_graph:
        if seed in visited:
            continue
        component, stack = set(), [seed]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(endpoint_graph[node] - component)
        visited.update(component)
        if len({sid for sid, _ in component}) < 3:
            continue
        local = [j for j in joints if tuple(j['a']) in component and tuple(j['b']) in component]
        def through(joint):
            ua, ub = net.outward(*joint['a']), net.outward(*joint['b'])
            return (abs(ua[0] * ub[1] - ua[1] * ub[0]) <= p['collinear_cross_limit']
                    and ua[0] * ub[0] + ua[1] * ub[1] < 0)
        if not any(through(j) for j in local):
            continue  # no supported through-run identity; do not invent a main direction
        for joint in local:
            ua, ub = net.outward(*joint['a']), net.outward(*joint['b'])
            if through(joint):
                joint['elevation_relation'] = 'same_run'
            elif abs(ua[0] * ub[1] - ua[1] * ub[0]) > p['collinear_cross_limit']:
                joint['elevation_relation'] = 'branch_boundary'
    result["cross_system_contacts"] = contacts
    for item in contacts:
        result["pending"].append({"kind": "cross_system_contact", "a": item["a"], "b": item["b"], "systems": item["systems"],
                                  "reason": "不同风系统的端点重合：不自动连接、不传播标高；核对系统归属是否写错，确属同一管路须改为同系统"})
    # 2. fitting membership and duplicates
    duplicates = []
    kept: list[dict[str, Any]] = []
    for fitting in net.fittings:
        x1, y1, x2, y2 = fitting["bbox"]
        if fitting['explicit_ends'] is not None:
            candidates = [(v['segment_id'], v['end']) for v in fitting['explicit_ends']]
            if any(net.segments[s]['floor_id'] != fitting['floor_id'] for s, _ in candidates):
                fail(f"{fitting['fitting_id']}: 显式端口不属于管件楼层")
        else:
            tol = p['fitting_end_tolerance']
            candidates = [(s,w) for s,w in ends if net.segments[s]['floor_id'] == fitting['floor_id']
                          and (fitting['system'] is None or net.segments[s]['system'] == fitting['system'])
                          and x1-tol <= net.end_point(s,w)[0] <= x2+tol and y1-tol <= net.end_point(s,w)[1] <= y2+tol]
        systems = {net.segments[s]['system'] for s,_ in candidates}
        if fitting['explicit_ends'] is not None and (len(systems) > 1 or (fitting['system'] and systems and systems != {fitting['system']})):
            fail(f"{fitting['fitting_id']}: 显式管件端口跨风系统，不能自动连通")
        ambiguous = len(systems) > 1
        if not ambiguous:
            fitting['ends'] = candidates
            fitting['system'] = fitting['system'] or next(iter(systems), None)
        # Duplicate evidence is considered only within the same system/ownership group.
        # An evidenced same physical block can have unresolved ownership and one combined pending.
        # tagged keys: a system may legitimately be named "ambiguous"
        group_key = ('ambiguous',) if ambiguous else ('system', fitting['system'])
        fitting['_ownership_group'] = group_key
        fitting['_candidate_ends'] = candidates
        fitting['_candidate_systems'] = systems

    # Check the complete claimed physical group before choosing any representative. A copy with
    # no explicit ports must not erase one member's ports and then swallow a conflicting member.
    identity_groups = defaultdict(list)
    for fitting in net.fittings:
        identity = fitting['physical_identity']
        if identity:
            key = (fitting['kind'], fitting['floor_id'], fitting['_ownership_group'], identity['source_id'], identity['instance_id'])
            identity_groups[key].append(fitting)
    identity_conflicts = set()
    for members in identity_groups.values():
        port_sets = {frozenset((e['segment_id'], e['end']) for e in f['explicit_ends'])
                     for f in members if f['explicit_ends'] is not None}
        if len(port_sets) > 1:
            ids = sorted(f['fitting_id'] for f in members)
            identity_conflicts.update(ids)
            result['pending'].append({'kind': 'fitting_duplicate_identity_unresolved', 'fitting_ids': ids,
                'physical_identity': members[0]['physical_identity'],
                'explicit_port_sets': [sorted(list(ports)) for ports in sorted(port_sets, key=lambda value: sorted(value))],
                'reason': '同一来源物理身份组含不同有效显式端口，整组身份凭据冲突；合并前保留全部成员及端口证据，不让无端口代表覆盖冲突'})

    # Where a non-conflicting group has explicit port evidence, consume that member as the
    # representative before an unported copy; inferred bbox ports cannot replace known ports.
    for fitting in sorted(net.fittings, key=lambda item: item['explicit_ends'] is None):
        x1, y1, x2, y2 = fitting['bbox']
        group_key = fitting['_ownership_group']
        ambiguous = group_key == ('ambiguous',)
        candidates, systems = fitting['_candidate_ends'], fitting['_candidate_systems']
        area = max((x2 - x1) * (y2 - y1), 1e-9)
        duplicate_of = None
        for other in kept:
            other_key = ('ambiguous',) if other['status'] == 'system_ambiguous' else ('system', other['system'])
            if other["kind"] != fitting["kind"] or other["floor_id"] != fitting["floor_id"] or other_key != group_key:
                continue
            if fitting['fitting_id'] in identity_conflicts or other['fitting_id'] in identity_conflicts:
                continue
            ox1, oy1, ox2, oy2 = other["bbox"]
            ix = max(0.0, min(x2, ox2) - max(x1, ox1))
            iy = max(0.0, min(y2, oy2) - max(y1, oy1))
            if ix * iy / min(area, max((ox2 - ox1) * (oy2 - oy1), 1e-9)) < p["fitting_overlap_duplicate"]:
                continue
            explicit = fitting["explicit_ends"]
            other_explicit = other["explicit_ends"]
            identity = fitting["physical_identity"]
            other_identity = other["physical_identity"]
            identity_key = (identity["source_id"], identity["instance_id"]) if identity else None
            other_identity_key = (other_identity["source_id"], other_identity["instance_id"]) if other_identity else None
            ports = {(v["segment_id"], v["end"]) for v in explicit} if explicit is not None else None
            other_ports = {(v["segment_id"], v["end"]) for v in other_explicit} if other_explicit is not None else None
            if explicit is not None and other_explicit is not None:
                if ports != other_ports:
                    # Different evidenced ports identify different physical connections, even where
                    # opposite bends occupy almost the same plan envelope.
                    if identity_key is not None and identity_key == other_identity_key:
                        result["pending"].append({"kind": "fitting_duplicate_identity_unresolved",
                            "fitting_ids": [other["fitting_id"], fitting["fitting_id"]],
                            "reason": "同物身份凭据与不同有效显式端口冲突，保留两件待核，不以身份声明覆盖端口证据"})
                    continue
            if identity_key is not None and other_identity_key is not None and identity_key != other_identity_key:
                if ports is not None and ports == other_ports:
                    result["pending"].append({"kind": "fitting_duplicate_identity_unresolved",
                        "fitting_ids": [other["fitting_id"], fitting["fitting_id"]],
                        "reason": "不同有据物理身份与相同显式端口冲突，保留两件待核，不因bbox或端口一致覆盖身份"})
                continue
            if (ports is not None and ports == other_ports) or (identity_key is not None and identity_key == other_identity_key):
                duplicate_of = other
                break
            result["pending"].append({"kind": "fitting_duplicate_identity_unresolved",
                "fitting_ids": [other["fitting_id"], fitting["fitting_id"]],
                "reason": "同类管件范围重合只构成重复候选；缺相同有效显式端口或同来源命名空间的物理身份凭据，保留两件待核，不按bbox强制合并"})
        if duplicate_of:
            fitting["status"] = "duplicate"
            fitting["duplicate_of"] = duplicate_of["fitting_id"]
            fitting["notes"].append(f"与 {duplicate_of['fitting_id']} 同类同归属组、范围重合且显式端口或有据物理身份相同，按同一物理连接的重复表示只计一次")
            if ambiguous:
                duplicate_of['ambiguous_pending']['duplicate_fitting_ids'].append(fitting['fitting_id'])
            duplicates.append(fitting)
            continue
        if ambiguous:
            fitting['status'] = 'system_ambiguous'
            fitting['plane_length_unresolved'] = True
            for key in candidates:
                end_state.setdefault(key, 'ambiguous_fitting')
            fitting['ambiguous_pending'] = {'kind':'fitting_system_ambiguous','fitting_id':fitting['fitting_id'],
                                            'systems':sorted(systems),'duplicate_fitting_ids':[],
                                            'reason':'块范围覆盖不同风系统；须声明管件system或同系统显式端口'}
            result['pending'].append(fitting['ambiguous_pending'])
            kept.append(fitting)
            continue
        kept.append(fitting)
        for key in fitting["ends"]:
            end_state.setdefault(key, "fitting")
    net.fittings = kept
    result["duplicate_fittings"] = [{"fitting_id": f["fitting_id"], "duplicate_of": f["duplicate_of"], "kind": f["kind"],
                                    **({"explicit_ends": f["explicit_ends"]} if f["explicit_ends"] is not None else {}),
                                    **({"physical_identity": f["physical_identity"]} if f["physical_identity"] else {})} for f in duplicates]
    # Source curve ends are occupied candidates, even when their continuation is unresolved.
    # They must never be bridged a second time by the straight-only gap/tap heuristics.
    for endpoint in getattr(net, "curve_endpoint_contacts", set()):
        end_state.setdefault(endpoint, "source_curve_candidate")
    # 3. accessory gaps between free collinear facing ends
    gaps = []
    free = [e for e in ends if e not in end_state]
    for i in range(len(free)):
        for j in range(i + 1, len(free)):
            a, b = free[i], free[j]
            sa, sb = net.segments[a[0]], net.segments[b[0]]
            if a[0] == b[0] or sa["floor_id"] != sb["floor_id"] or sa['system'] != sb['system']:
                continue
            if not net.same_source_plane(sa,sb):
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
        if fitting['status'] == 'system_ambiguous':
            continue
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
                    if other["segment_id"] == sid or other["floor_id"] != segment["floor_id"] or other['system'] != segment['system']:
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
            measured = single_end_plane(net, fitting, sid, which)
            g = fitting.get('single_end_geometry') or {}
            fitting['status'] = ('single_end_inline' if fitting['kind'] in {'reducer','rect_to_round'} else
                                 'single_end' if g.get('turn_plane') == 'vertical' else
                                 'single_end_horizontal' if g.get('turn_plane') == 'horizontal' else 'single_end_unresolved')
            fitting['plane_extension'] = measured['extension']
            fitting['plane_extension_basis'] = measured['extension_basis']
            fitting['plane_extension_error_bound'] = measured['extension_error_bound']
            fitting['plane_scenarios'] = measured['scenarios']
            if measured['length'] is None:
                fitting['plane_length_unresolved'] = True
                fitting['single_end_block_reasons'] = [measured['reason']]
                if measured.get('pairing_offset') is not None:
                    fitting['pairing_center'] = [e[0]+od[0]*measured['pairing_offset'], e[1]+od[1]*measured['pairing_offset']]
                result['pending'].append({'kind':'single_end_plane_unresolved','fitting_id':fitting['fitting_id'],
                                          'segment_id':sid,'reason':measured['reason']})
            else:
                fitting['plane_center'] = [e[0]+od[0]*measured['length'], e[1]+od[1]*measured['length']]
                fitting['arms'].append({'segment_id':sid,'end':which,'length':measured['length'],
                                        'evidence_level':'规则推算','basis':f"单端几何 {measured['method']}：{g['source_id']}；{g['basis']}",
                                        'plane_scenarios':measured['scenarios'],'plane_policy':measured['method'],
                                        'half_width':measured['half_width'],'extension_basis':measured['extension_basis']})
            if fitting['status'] == 'single_end_unresolved':
                fitting.setdefault('single_end_block_reasons', []).append('转弯平面未声明为 vertical')
                result['pending'].append({'kind':'single_end_turn_plane_unknown','fitting_id':fitting['fitting_id'],
                                          'reason':'单端件转弯平面未声明为vertical，不按邻近或跨层位置猜竖向连接'})
            elif fitting['status'] == 'single_end_horizontal':
                result['pending'].append({'kind':'single_end_horizontal_destination_unknown','fitting_id':fitting['fitting_id'],
                                          'reason':'水平转弯另端去向未给出；不按竖向连接处理'})
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
        fitting["main_segment_ids"] = [pts[i][0], pts[j][0]]
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
            if other["segment_id"] == sid or other["floor_id"] != segment["floor_id"] or other['system'] != segment['system']:
                continue
            if not net.same_source_plane(segment,other):
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
    """Keep same-run continuity separate from crossing a main/branch boundary."""
    parent = {sid: sid for sid in net.segments}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    adjacency: dict[str, set[str]] = defaultdict(set)
    branch_adjacency: dict[str, set[str]] = defaultdict(set)

    def fitting_branch(fitting, a, b):
        main_ids = set(fitting.get("main_segment_ids", ()))
        if main_ids:
            return not {a, b} <= main_ids
        if fitting["kind"] in {"tap", "tee", "cross"}:
            ua, ub = net.segments[a]["u"], net.segments[b]["u"]
            return abs(ua[0] * ub[1] - ua[1] * ub[0]) > net.params["collinear_cross_limit"]
        return False

    # Endpoints may also meet exactly at a tee. Its explicit main/branch relation must
    # not be bypassed by the generic coincident-end joint created earlier.
    branch_pairs = {frozenset((item["segment_id"], item["main_segment_id"])) for item in net.result["open_taps"]}
    branch_pairs.update(getattr(net,'curve_branch_pairs',set()))
    for fitting in net.fittings:
        ids = [sid for sid, _ in fitting["ends"]]
        if fitting.get("main_segment_id"):
            branch_pairs.update(frozenset((sid, fitting["main_segment_id"])) for sid in ids)
        else:
            branch_pairs.update(frozenset((a, b)) for a in ids for b in ids if a != b and fitting_branch(fitting, a, b))

    def link(a, b, branch=False):
        if net.segments[a]['system'] != net.segments[b]['system']:
            fail(f'{a}/{b}: 连续关系跨风系统，不能传播标高')
        parent[find(a)] = find(b)
        graph = branch_adjacency if branch or frozenset((a, b)) in branch_pairs else adjacency
        graph[a].add(b)
        graph[b].add(a)

    for item in net.result["joints"]:
        link(item["a"][0], item["b"][0], branch=item.get('elevation_relation') == 'branch_boundary')
    for item in net.result["accessory_gaps"]:
        link(item["a"][0], item["b"][0])
    for item in net.result["open_taps"]:
        link(item["segment_id"], item["main_segment_id"], branch=True)
    for fitting in net.fittings:
        if fitting["status"] in {"offset_candidate", "offset_member_duplicate", 'system_ambiguous'} or fitting['status'].startswith('single_end'):
            continue
        ids = [sid for sid, _ in fitting["ends"]]
        if fitting.get("main_segment_id"):
            for sid in ids:
                link(sid, fitting["main_segment_id"], branch=True)
            continue
        for a in ids:
            for b in ids:
                if a != b:
                    link(a, b, branch=fitting_branch(fitting, a, b))
    net.elevation_branch_adjacency = branch_adjacency
    return {sid: find(sid) for sid in net.segments}, adjacency


def propagate_elevation(net: Network, adjacency: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    """Prefer any same-run tag to main/branch fallback; fallback is scenario evidence only."""
    resolved: dict[str, dict[str, Any]] = {}
    for sid, segment in net.segments.items():
        own, level, why = net.center_elevation(segment)
        if own is not None:
            # A given elevation (tag, system diagram, section or host propagation) always wins over
            # script propagation; the script only fills segments that carry no elevation at all.
            source = ELEVATION_ORIGIN_LABELS[segment["elevation"]["origin"]]
            resolved[sid] = {"value": own, "evidence_level": level, "origin": segment["elevation"]["origin"],
                             "basis": f"{source} {segment['elevation']['source_id']}（{segment['elevation']['reference']}）", "hops": 0}
            continue
        best = {sid: (0, 0)}
        frontier = [(0, 0, sid)]
        found = None
        tagged = []
        found_cost = None
        while frontier and found is None:
            crossings, hop, node = heapq.heappop(frontier)
            cost = (crossings, hop)
            if cost != best[node]:
                continue
            if found_cost is not None and cost > found_cost:
                break
            value, lvl, _ = net.center_elevation(net.segments[node])
            if value is not None and node != sid:
                found_cost = cost
                tagged.append((node, value, lvl))
                continue
            for extra, graph in ((0, adjacency), (1, net.elevation_branch_adjacency)):
                for nxt in sorted(graph.get(node, ())):
                    candidate_cost = (crossings + extra, hop + 1)
                    if nxt not in best or candidate_cost < best[nxt]:
                        best[nxt] = candidate_cost
                        heapq.heappush(frontier, (*candidate_cost, nxt))
        if tagged:
            crossings, hop = found_cost
            values = sorted({round(v, 6) for _, v, _ in tagged})
            if len(values) == 1:
                node, value, lvl = sorted(tagged)[0]
                derived = "方案暂估" if crossings or lvl == "方案暂估" else "规则推算"
                basis = (f"跨主支管接头 {crossings} 次取 {node} 的标高作同高假定（{hop} 跳，无同支管标高依据，仅方案暂估）" if crossings else
                         f"沿同一连续管路传播自 {node} 的标高（{hop} 跳，不跨主支管边界或竖向候选）")
                found = {"value": value, "evidence_level": derived, "origin": "script_propagated", "basis": basis, "hops": hop,
                         "branch_boundaries": crossings}
            else:
                found = {"value": None, "evidence_level": "未决", "basis": f"同优先级同距离多个不同标高 {values}，无法判定", "hops": hop,
                         "branch_boundaries": crossings}
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

    # 0. host-declared vertical connections: the host has judged which pipes join; the script
    #    only takes the centre-elevation difference and keeps the host as the source.
    host_connections: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    fittings_by_id = {f["fitting_id"]: f for f in net.fittings}
    for link in net.vertical_links:
        fitting = None
        if link["kind"] == "segment_to_segment":
            sa, sb = link["segment_id"], link["to_segment_id"]
        else:
            fitting = fittings_by_id[link["fitting_id"]]
            if fitting["status"] in {"single_end_unresolved", "single_end_horizontal"}:
                fail(f"{link['link_id']}: 单端管件 {fitting['fitting_id']} 未声明竖向转弯（状态 {fitting['status']}）；"
                     "须在该管件 single_end_geometry 中写 turn_plane: vertical 及中心点、立管投影或法兰外伸等几何依据后再声明竖向连接")
            if fitting["status"] == "single_end_inline":
                fail(f"{link['link_id']}: 管件 {fitting['fitting_id']} 是单端变径/天圆地方（在线本体），不能作为竖向弯头声明竖向连接")
            if fitting["status"] != "single_end":
                fail(f"{link['link_id']}: 管件 {fitting['fitting_id']} 状态为 {fitting['status']}，不是单端管件，不能按单端竖向连接")
            if fitting.get("vertical_id"):
                fail(f"{link['link_id']}: 管件 {fitting['fitting_id']} 已有竖向连接")
            sa, sb = fitting["ends"][0][0], link["to_segment_id"]
        anchors = list(link.get("connection_ends", ()))
        if fitting is not None:
            anchors = [fitting["ends"][0]]
        pair = frozenset((sa, sb))
        for previous in host_connections[pair]:
            old = dict(previous["anchors"])
            new = dict(anchors)
            # Different anchored ports can prove two real connections between the same runs.
            # Non-overlapping/incomplete hints cannot prove that two declarations are distinct.
            shared = old.keys() & new.keys()
            if not shared or any(old[sid] == new[sid] for sid in shared):
                fail(f"{link['link_id']}: {sa} 与 {sb} 的竖向连接重复声明或缺少不同连接端口依据")
        if net.segments[sa]['system'] != net.segments[sb]['system']:
            fail(f"{link['link_id']}: 竖向连接跨风系统，不能自动合并数量")
        ea, eb = elevations[sa], elevations[sb]
        levels = [ea["evidence_level"], eb["evidence_level"], link["evidence_level"]]
        floor_a, floor_b = net.segments[sa]["floor_id"], net.segments[sb]["floor_id"]
        base_a = base_b = 0.0
        floor_note = ""
        if floor_a != floor_b:
            fa, fb = net.floors.get(floor_a), net.floors.get(floor_b)
            if fa is None or fb is None or fa["level"] is None or fb["level"] is None:
                levels.append("未决")
                floor_note = "；跨层但缺层标高"
            else:
                base_a, base_b = fa["level"], fb["level"]
                levels += [fa["level_evidence_level"], fb["level_evidence_level"]]
                floor_note = f"；层标高 {floor_a} {base_a} / {floor_b} {base_b}"
        level = combine(levels)
        length = abs((base_a + ea["value"]) - (base_b + eb["value"])) if level != "未决" else None
        vid = f"VERT-{len(items) + 1:03d}"
        segment = net.segments[sa]
        items.append({"vertical_id": vid, "kind": "host_declared_link", "link_id": link["link_id"], "link_kind": link["kind"], "origin": "宿主判定",
                      "floor_id": floor_a if floor_a == floor_b else f"{floor_a}-{floor_b}", "fitting_ids": [fitting["fitting_id"]] if fitting else [],
                      "segment_ids": [sa, sb], "system": segment["system"], "spec": spec_of(segment), "length": length, "evidence_level": level,
                      "basis": f"宿主判定 {link['link_id']}（{link['source_id']}：{link['basis']}）；{sa}: {ea['value']}（{ea['basis']}） / {sb}: {eb['value']}（{eb['basis']}）{floor_note}；按管中心标高差"})
        items[-1]["connection_ends"] = [{"segment_id": sid, "end": end} for sid, end in anchors]
        host_connections[pair].append({"item": items[-1], "anchors": anchors})
        if fitting is not None:
            fitting["vertical_id"] = vid
    # 1. in-floor offsets
    for group in net.offset_groups.values():
        (sa, _), (sb, _) = group["ends"]
        ea, eb = elevations[sa], elevations[sb]
        segment = net.segments[sa]
        level = combine([ea["evidence_level"], eb["evidence_level"]])
        length = abs(ea["value"] - eb["value"]) if level != "未决" else None
        items.append({"vertical_id": f"VERT-{len(items) + 1:03d}", "kind": "in_floor_offset", "floor_id": group["floor_id"], "fitting_ids": group["members"],
                      "segment_ids": [sa, sb], "system": segment["system"], "spec": spec_of(segment), "length": length,
                       "_connection_ends": group["ends"],
                      "evidence_level": level, "basis": f"{sa}: {ea['value']}（{ea['basis']}） / {sb}: {eb['value']}（{eb['basis']}）；上下翻按两侧管中心标高差"})
    # 2. single-ended elbows: branch rising/dropping onto a main of the same floor
    singles = [f for f in net.fittings if f["status"] == "single_end" and not f.get('plane_length_unresolved')]
    for fitting in singles:
        if fitting.get("vertical_id"):
            continue
        sid, which = fitting["ends"][0]
        segment = net.segments[sid]
        x1, y1, x2, y2 = fitting["bbox"]
        cx, cy = fitting['plane_center']
        partner = None
        for other in net.segments.values():
            if other["segment_id"] == sid or other["floor_id"] != segment["floor_id"] or other['system'] != segment['system']:
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
                       "_connection_ends": [(sid, which)],
                      "evidence_level": level, "basis": f"支管 {sid}: {ea['value']}（{ea['basis']}） -> 主管 {partner['segment_id']}: {eb['value']}（{eb['basis']}）；支管竖向接主管按管中心标高差"})
        fitting["vertical_id"] = items[-1]["vertical_id"]
    # 3. inter-floor risers between stacked single-ended elbows
    remaining = [f for f in singles if not f.get("vertical_id")]
    floors = sorted(net.floors.values(), key=lambda f: f["order"])
    for lower, upper in zip(floors, floors[1:]):
        lowers = [f for f in remaining if f["floor_id"] == lower["floor_id"] and not f.get("vertical_id")]
        for fitting in [f for f in remaining if f["floor_id"] == upper["floor_id"] and not f.get("vertical_id")]:
            cu = [fitting['plane_center'][0] - upper['plan_offset'][0], fitting['plane_center'][1] - upper['plan_offset'][1]]
            best = None
            for candidate in lowers:
                if candidate.get("vertical_id"):
                    continue
                if net.segments[candidate['ends'][0][0]]['system'] != net.segments[fitting['ends'][0][0]]['system']:
                    continue
                cl = [candidate['plane_center'][0] - lower['plan_offset'][0], candidate['plane_center'][1] - lower['plan_offset'][1]]
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
                           "_connection_ends": [candidate["ends"][0], fitting["ends"][0]],
                          "system": net.segments[su]["system"], "spec": spec_of(net.segments[su]), "length": length, "evidence_level": level,
                          "basis": f"{upper['floor_id']} {su} 中心 {eu['value']} + 层标高 {upper['level']} / {lower['floor_id']} {sl} 中心 {el['value']} + 层标高 {lower['level']}；平面错位 {best[0]:.0f}"})
            fitting["vertical_id"] = candidate["vertical_id"] = vid

    # Freeze one physical-candidate universe before any host supersession: resolved and
    # geometry-blocked branches are not separate opportunities to claim uniqueness.
    blocked = [f for f in net.fittings if len(f["ends"]) == 1 and not f.get("vertical_id") and not f.get("vertical_identity_pending")
               and (f["status"] == "single_end_unresolved" or (f["status"] == "single_end" and f.get("plane_length_unresolved")))]
    blocked_ids = {f["fitting_id"] for f in blocked}
    open_ends = [f for f in singles if not f.get("vertical_id") and not f.get("vertical_identity_pending")] + blocked

    def locate(fitting):
        if fitting.get("plane_center") and not fitting.get("plane_length_unresolved"):
            return fitting["plane_center"], "plane_center"
        if fitting.get("pairing_center"):
            return fitting["pairing_center"], "reversed_riser_projection"
        return net.end_point(*fitting["ends"][0]), "segment_end_approximation"

    blocked_pairs = []
    for lower, upper in zip(floors, floors[1:]):
        for a in [f for f in open_ends if f["floor_id"] == lower["floor_id"]]:
            for b in [f for f in open_ends if f["floor_id"] == upper["floor_id"]]:
                if a["fitting_id"] not in blocked_ids and b["fitting_id"] not in blocked_ids:
                    continue
                sa, sb = a["ends"][0][0], b["ends"][0][0]
                if net.segments[sa]["system"] != net.segments[sb]["system"]:
                    continue
                (pa, basis_a), (pb, basis_b) = locate(a), locate(b)
                d = dist([pa[0] - lower["plan_offset"][0], pa[1] - lower["plan_offset"][1]],
                         [pb[0] - upper["plan_offset"][0], pb[1] - upper["plan_offset"][1]])
                if d <= p["riser_match_distance"]:
                    blocked_pairs.append((d, lower, upper, a, b, {a["fitting_id"]: basis_a, b["fitting_id"]: basis_b}))
    all_connection_candidates = defaultdict(set)
    for item in items:
        if item['kind'] != 'host_declared_link':
            all_connection_candidates[frozenset(item['segment_ids'])].add(frozenset(tuple(e) for e in item['_connection_ends']))
    for _, _, _, a, b, _ in blocked_pairs:
        ports = (a['ends'][0], b['ends'][0])
        all_connection_candidates[frozenset((ports[0][0], ports[1][0]))].add(frozenset(tuple(e) for e in ports))

    def match_host(item):
        """Return a unique evidenced host, None for a different connection, or 'ambiguous'."""
        pair = frozenset(item["segment_ids"])
        hosts = host_connections.get(pair, ())
        candidate_count = len(all_connection_candidates[pair])
        matches, unknown = [], False
        ports = dict(item["_connection_ends"])
        for host in hosts:
            anchors = dict(host["anchors"])
            if not anchors:
                if candidate_count == 1 and len(hosts) == 1:
                    matches.append(host)
                else:
                    unknown = True
                continue
            shared = anchors.keys() & ports.keys()
            if any(anchors[sid] != ports[sid] for sid in shared):
                if any(anchors[sid] == ports[sid] for sid in shared):
                    unknown = True  # one port claimed twice with contradictory partner ports
                continue
            if anchors.items() <= ports.items():
                matches.append(host)
            else:
                unknown = True
        if len(matches) == 1 and not unknown:
            return matches[0]
        return "ambiguous" if matches or unknown else None

    def supersede(item, host):
        for fid in item["fitting_ids"]:
            fitting = fittings_by_id[fid]
            if host == "ambiguous":
                fitting.pop("vertical_id", None)
                fitting["vertical_identity_pending"] = True
            else:
                fitting["vertical_id"] = host["item"]["vertical_id"]
        if host == "ambiguous":
            pending.append({"kind": "vertical_connection_identity_unresolved", "segment_ids": item["segment_ids"],
                "fitting_ids": item["fitting_ids"], "candidate": {k: v for k, v in item.items() if not k.startswith("_")},
                "reason": "同一管段对有多处不同端口竖向候选，显式声明缺唯一对应证据；保留候选待核，不按整对吞并，也不与宿主项重复计量。补connection_ends或明确单端管件连接"})

    retained = []
    for item in items:
        if item["kind"] == "host_declared_link":
            retained.append(item)
            continue
        host = match_host(item)
        if host is not None:
            supersede(item, host)
            if host != "ambiguous" and item["kind"] == "in_floor_offset":
                for group in net.offset_groups.values():
                    if group["members"] == item["fitting_ids"]:
                        group["superseded_by_host_link"] = True
        else:
            retained.append(item)
    items = retained
    for item in items:
        anchors = item.pop("_connection_ends", None)
        if anchors is not None:
            item["connection_ends"] = [{"segment_id": sid, "end": end} for sid, end in anchors]
    # 3b. stacked candidates where one end's single-end geometry is unresolved (reversed or unknown
    #     relation, conflicting evidence, turn plane not declared). They never enter the rule quantity.
    #     The pair is reported against both ends and as one combined item; where every blocked end
    #     shows a riser projection the riser is listed as a scenario outside the totals.
    scenarios, partners, scenario_used = [], defaultdict(list), set()
    for d, lower, upper, a, b, bases in sorted(blocked_pairs, key=lambda pair: pair[0]):
        ids = [a["fitting_id"], b["fitting_id"]]
        sa, sb = a["ends"][0][0], b["ends"][0][0]
        candidate = {"segment_ids": [sa, sb], "fitting_ids": ids, "_connection_ends": [a["ends"][0], b["ends"][0]]}
        host = match_host(candidate)
        if host is not None:
            supersede(candidate, host)
            continue
        partners[ids[0]].append(ids[1]); partners[ids[1]].append(ids[0])
        stuck = [f for f in (a, b) if f["fitting_id"] in blocked_ids]
        why = "；".join(f"{f['fitting_id']}：{'、'.join(f.get('single_end_block_reasons') or ['单端几何未决'])}" for f in stuck)
        entry = {"kind": "inter_floor_riser_blocked_by_single_end_geometry", "floor_id": f"{lower['floor_id']}-{upper['floor_id']}",
                 "fitting_ids": ids, "segment_ids": [sa, sb], "system": net.segments[sb]["system"], "plan_distance": d,
                 "blocked_fitting_ids": [f["fitting_id"] for f in stuck], "position_basis": bases,
                 "reason": f"层间立管候选被单端几何未决阻断：{lower['floor_id']} {ids[0]} 与 {upper['floor_id']} {ids[1]} 平面错位 {d:.0f}，"
                           f"未配对、不计入规则推算。{why}。核对块方向和连接点后补单端几何重跑，或由宿主在 vertical_links 判定"}
        can_scenario = (all(f["status"] == "single_end" for f in (a, b)) and "segment_end_approximation" not in bases.values()
                        and not scenario_used & set(ids))
        if can_scenario:
            el, eu = elevations[sa], elevations[sb]
            levels = [el["evidence_level"], eu["evidence_level"], upper["level_evidence_level"], lower["level_evidence_level"], "方案暂估"]
            if upper["level"] is None or lower["level"] is None:
                levels.append("未决")
            level = combine(levels)
            sid_ = f"VSCN-{len(scenarios) + 1:03d}"
            scenarios.append({"scenario_id": sid_, "kind": "inter_floor_riser_scenario", "floor_id": entry["floor_id"], "fitting_ids": ids,
                              "segment_ids": [sa, sb], "system": entry["system"], "spec": spec_of(net.segments[sb]),
                              "length": abs((upper["level"] + eu["value"]) - (lower["level"] + el["value"])) if level != "未决" else None,
                              "evidence_level": level, "position_basis": bases,
                              "basis": f"反向件按立管投影定位配对，只作方案：不出平面长度，不并入规则推算与 metrics；"
                                       f"{upper['floor_id']} {sb} 中心 {eu['value']} + 层标高 {upper['level']} / {lower['floor_id']} {sa} 中心 {el['value']} + 层标高 {lower['level']}；平面错位 {d:.0f}"})
            scenario_used |= set(ids)
            entry["scenario_id"] = sid_
        pending.append(entry)
    for item in pending:
        if item.get("fitting_id") in partners and item["kind"] in {"single_end_plane_unresolved", "single_end_turn_plane_unknown"}:
            item["possible_riser_partners"] = partners[item["fitting_id"]]
            item["reason"] += f"；相邻层可能对应的单端件：{'、'.join(partners[item['fitting_id']])}（见 inter_floor_riser_blocked_by_single_end_geometry）"
    result["vertical_scenarios"] = scenarios
    # 4. single ends with unknown destination are pending, never estimated
    for fitting in singles:
        if fitting.get("vertical_id") or fitting.get("vertical_identity_pending"):
            continue
        sid, which = fitting["ends"][0]
        segment = net.segments[sid]
        near_blocked = partners.get(fitting["fitting_id"], [])
        reason = ("单端管件去向不明：无同层主管带宽、无相邻层对应弯头；竖向长度待查系统图/剖面" if not near_blocked else
                  f"单端管件去向不明：相邻层有候选单端件 {'、'.join(near_blocked)}，因其单端几何未决未配对"
                  "（见 inter_floor_riser_blocked_by_single_end_geometry）；无同层主管带宽；竖向长度待补齐该件几何后重跑或查系统图/剖面")
        pending.append({"kind": "single_end_destination_unknown", "fitting_id": fitting["fitting_id"], "segment_id": sid, "end": which,
                        "floor_id": fitting["floor_id"], "system": segment["system"], "spec": spec_of(segment),
                        "center_elevation": elevations[sid]["value"], "plane_extension": fitting.get("plane_extension"),
                        **({"blocked_riser_candidates": near_blocked} if near_blocked else {}), "reason": reason})
    for item in items:
        if item["evidence_level"] == "未决":
            pending.append({"kind": f"vertical_{item['kind']}_unresolved", "vertical_id": item["vertical_id"], "fitting_ids": item["fitting_ids"],
                            "segment_ids": item["segment_ids"], "floor_id": item["floor_id"], "reason": item["basis"]})
    result["vertical_items"] = items


def append_annotation_verticals(net: Network, data: dict[str, Any], result: dict[str, Any]) -> None:
    """Explicit height is a length fact, never an absolute elevation or leader length.

    Re-read bound source evidence at every calculation. Existing verticals win only
    as existing quantities: ambiguous physical overlap leaves the new note pending.
    """
    config = data.get('vertical_annotation_input')
    if config is None:
        return
    from bind_duct_vertical_annotations import bind_annotations
    receipt = bind_annotations(data, config)
    result['vertical_annotation_binding'] = receipt
    notes = receipt['annotations']
    existing = list(result['vertical_items']) + list(result['vertical_scenarios'])

    def source_port(note):
        target = note.get('target')
        if not target:
            return None
        part = target['part']
        length = math.dist(part['start'], part['end'])
        fraction = target['fraction']
        reach = receipt['parameters']['target_tolerance']
        # Use the actual finite source curve and its endpoint neighbourhoods.
        # A graph snap tolerance says nothing about separate physical risers.
        near_start = fraction * length <= reach
        near_end = (1. - fraction) * length <= reach
        if near_start == near_end:
            return None
        end = 'start' if near_start else 'end'
        return {'source_sha256': note['source_sha256'], 'source_id': target['source_id'],
                'part_index': target['part_index'], 'end': end, 'point': list(part[end])}

    for note in notes:
        note['source_connection_port'] = source_port(note)
    # Only different source-derived endpoints prove different ports here. Two
    # arbitrary connection_id strings or separated arrows do not prove identity.
    for index, note in enumerate(notes):
        if note['status'] != 'bound':
            continue
        collisions = []
        for other in notes:
            if other is note or other.get('segment_id') != note.get('segment_id') or not other.get('target'):
                continue
            a, b = note['source_connection_port'], other['source_connection_port']
            distinct = a is not None and b is not None and a['source_sha256'] == b['source_sha256'] and a['source_id'] == b['source_id'] and a['part_index'] == b['part_index'] and a['end'] != b['end']
            if not distinct:
                collisions.append(other['annotation_id'])
        overlap = [item.get('vertical_id') or item.get('scenario_id') for item in existing if note['segment_id'] in item.get('segment_ids', [])]
        if collisions or overlap:
            note.update(status='unresolved', evidence_level='未决', length=None)
            note['reasons'].append('可能与其他引注或既有竖向段为同一实物；缺少不同连接身份，不重复累计')
            note['duplicate_candidates'] = collisions + overlap
    for note in notes:
        if note['status'] != 'bound':
            result['pending'].append({'kind': 'vertical_annotation_unresolved', 'annotation_id': note['annotation_id'],
                                      'segment_ids': note['candidate_segment_ids'], 'floor_id': note.get('floor_id'),
                                      'length': None, 'height_fact': note['height_fact'], 'source_id': note['source_id'],
                                      'text': note['text'], 'reason': '；'.join(note['reasons'])})
            continue
        segment = net.segments[note['segment_id']]
        result['vertical_items'].append({'vertical_id': f"VERT-{len(result['vertical_items']) + 1:03d}",
            'kind': 'annotation_explicit_height', 'annotation_id': note['annotation_id'], 'origin': '源引线与明确高度',
            'floor_id': note['floor_id'], 'system': note['system'], 'segment_ids': [note['segment_id']], 'fitting_ids': [],
            'spec': spec_of(segment), 'length': note['length'], 'evidence_level': '规则推算',
            'height_fact': note['height_fact'], 'pointer': note['pointer'], 'source_id': note['source_id'],
            'source_ids': [note['source_id'], note['target']['source_id']], 'text': note['text'],
            'source_evidence': receipt['evidence'], 'binding_evidence': note,
            'surface_calculation_allowed': receipt['evidence']['coordinate_unit'] is not None,
            'connection_ends': [], 'business_adoption_allowed': False, 'human_reviewed': False,
            'basis': f"源引注 {note['source_id']} 原文“{note['text']}”，完整引线指向唯一风管 {note['segment_id']}；"
                     '长度直接取明示上接/下接高度，不是绝对标高，不取引线长或符号外框；规则推算待专业复核'})
        if note.get('dependency_pending'):
            result['pending'].append({'kind': 'annotation_surface_unit_unresolved', 'annotation_id': note['annotation_id'],
                'source_id': note['source_id'], 'length': note['length'], 'surface_area': None,
                'reason': '同原图坐标的唯一指向与明示高度已形成规则竖向长度；平面坐标单位未核，不能换算截面周长或该竖段面积；'
                          '既有水平量仍只按冻结输入声明口径计算，本绑定不证明该口径：' + str(note['dependency_pending']['xy_unit'])})


def summarize(net: Network, elevations: dict[str, dict[str, Any]], result: dict[str, Any]) -> None:
    f = net.factor
    summary: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    surface: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    part_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    plane_totals: dict[str, float] = defaultdict(float)

    def add(part: str, segment: dict[str, Any], level: str, length_units: float) -> None:
        metres = length_units * f
        summary[segment["system"]][level][spec_of(segment)] += metres
        part_totals[part][level] += metres
        per = perimeter_m(segment, f)
        if per is not None:
            surface[segment["system"]][level][spec_of(segment)] += per * metres

    straight = []
    for segment in net.segments.values():
        add("straight", segment, segment["length_evidence_level"], segment["length"])
        straight.append({"segment_id": segment["segment_id"], "floor_id": segment["floor_id"], "system": segment["system"], "spec": spec_of(segment),
                         "length": segment["length"] * f, "evidence_level": segment["length_evidence_level"], "size_evidence_level": segment["size_evidence_level"],
                         "center_elevation": elevations[segment["segment_id"]]["value"], "elevation_evidence_level": elevations[segment["segment_id"]]["evidence_level"],
                         "elevation_basis": elevations[segment["segment_id"]]["basis"], "source_ids": segment["source_ids"]})
    result["straight_segments"] = straight
    fittings_out = []
    for fitting in net.fittings:
        arms = []
        for arm in fitting["arms"]:
            segment = net.segments[arm["segment_id"]]
            add("fitting", segment, arm["evidence_level"], arm["length"])
            converted = {**arm, "length": arm["length"] * f, "system": segment["system"], "spec": spec_of(segment)}
            if "plane_scenarios" in arm:
                converted["plane_scenarios"] = {k: (v * f if v is not None else None) for k, v in arm["plane_scenarios"].items()}
                converted["half_width"] = arm["half_width"] * f if arm["half_width"] is not None else None
                plane_totals['geometry_evidence'] += converted['length']
            arms.append(converted)
        if 'plane_scenarios' in fitting:
            plane_totals['fittings'] += 1
            minus = fitting['plane_scenarios']['extension_minus_half_width']
            plane_totals['extension_full'] += fitting['plane_scenarios']['extension_full'] * f
            if minus is None:
                plane_totals['without_width'] += 1
            else:
                plane_totals['extension_minus_half_width'] += minus * f
                plane_totals['minus_half_width_zero'] += int(minus == 0)
            plane_totals['unresolved'] += int(bool(fitting.get('plane_length_unresolved')))
            plane_totals['rotated_bbox'] += int(fitting.get('plane_extension_basis') == 'rotated_bbox_upper_bound')
        fittings_out.append({"fitting_id": fitting["fitting_id"], "kind": fitting["kind"], "floor_id": fitting["floor_id"], "status": fitting["status"],
                             "block_name": fitting.get("block_name"), "source_id": fitting.get("source_id"),
                             "ends": [{"segment_id": s, "end": w} for s, w in fitting["ends"]], "junction": fitting.get("junction"),
                             "main_segment_id": fitting.get("main_segment_id"), "arms": arms,
                             'system': fitting.get('system'),
                              'physical_identity': fitting.get('physical_identity'),
                             'single_end_geometry': fitting.get('single_end_geometry'),
                             'plane_center': fitting.get('plane_center'),
                             'plane_extension_basis': fitting.get('plane_extension_basis'),
                             'plane_extension_error_bound': (fitting['plane_extension_error_bound']*f
                                                             if fitting.get('plane_extension_error_bound') is not None else None),
                             'plane_scenarios': {k: v*f if v is not None else None for k,v in fitting.get('plane_scenarios',{}).items()},
                             "centerline_length": None if fitting.get('plane_length_unresolved') else sum(a["length"] for a in arms),
                             "vertical_id": fitting.get("vertical_id"), "notes": fitting["notes"]})
        if fitting["status"] == "unconnected":
            result["pending"].append({"kind": "fitting_unconnected", "fitting_id": fitting["fitting_id"], "floor_id": fitting["floor_id"],
                                      "reason": "管件块范围内没有中心线端点；核对块位置、图层或中心线是否缺绘"})
    result["fittings"] = fittings_out
    # Preserve both legacy readings for diagnosis; only evidenced geometry supplies quantity.
    requested = net.params["single_end_plane_policy"]
    result["single_end_plane_scenarios"] = {
        "policy": "geometry_evidence",
        # a legacy reading asked for in parameters is echoed here but never applied
        "legacy_policy_requested": requested if requested != "geometry_evidence" else None,
        "legacy_policy_applied": False,
        "rotated_bbox_count": int(plane_totals["rotated_bbox"]),
        "fitting_count": int(plane_totals["fittings"]),
        "extension_minus_half_width": plane_totals["extension_minus_half_width"],
        "extension_full": plane_totals["extension_full"],
        "difference_full_minus_default": plane_totals["extension_full"] - plane_totals["extension_minus_half_width"],
        "minus_half_width_zero_count": int(plane_totals["minus_half_width_zero"]),
        "without_width_count": int(plane_totals["without_width"]),
        'unresolved_count': int(plane_totals['unresolved']),
        'geometry_evidence': None if plane_totals['unresolved'] else plane_totals['geometry_evidence'],
        'known_geometry_subtotal': plane_totals['geometry_evidence'],
        'legacy_readings_diagnostic_only': True,
        "meaning": '按single_end_geometry的中心/投影/半管高含法兰/在线本体证据计量；旧整段及减半宽只作诊断，不能靠切换参数采用。未决为null，已知小计另列。'
                   '斜向管段（rotated_bbox_count）的外包框伸出只是上限，旧读法诊断值偏大。',
    }
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
        if item.get('surface_calculation_allowed', True) and per is not None:
            surface[segment["system"]][item["evidence_level"]][spec_of(segment)] += per * metres
        if item.get('kind') == 'annotation_explicit_height':
            item['theoretical_surface_area'] = per * metres if item['surface_calculation_allowed'] and per is not None else None
    result["length_summary"] = {system: {level: dict(sorted(specs.items())) for level, specs in levels.items()} for system, levels in summary.items()}
    result["theoretical_surface_summary"] = {system: {level: dict(sorted(specs.items())) for level, specs in levels.items()} for system, levels in surface.items()}
    result["part_totals"] = {part: dict(levels) for part, levels in part_totals.items()}
    # Totals below add only the known parts; these pending kinds leave a length out of them.
    length_gaps = sum(1 for item in result["pending"] if item["kind"] in LENGTH_PENDING_KINDS
                      or (item["kind"].startswith("vertical_") and item["kind"].endswith("_unresolved")))
    result["metrics"] = {
        "totals_complete": length_gaps == 0,
        "length_unresolved_items": length_gaps,
        "straight_length": part_totals["straight"]["确认"],
        **({"straight_length_rule": part_totals["straight"]["规则推算"]} if part_totals["straight"].get("规则推算") else {}),
        "fitting_length_rule": part_totals["fitting"]["规则推算"],
        "fitting_length_scenario": part_totals["fitting"]["方案暂估"],
        "vertical_length_rule": part_totals["vertical"]["规则推算"],
        "vertical_length_scenario": part_totals["vertical"]["方案暂估"],
        "accessory_gap_count": len(result["accessory_gaps"]),
        "accessory_gap_length_excluded": sum(g["length"] for g in result["accessory_gaps"] if g["quantity_action"] == "exclude"),
        "pending_count": len(result["pending"]),
        "single_end_plane_default": result["single_end_plane_scenarios"]['geometry_evidence'],
        'fitting_plane_unresolved_count': sum(bool(f.get('plane_length_unresolved')) for f in net.fittings),
        'legacy_single_end_policy_diagnostic_only': True,
        "single_end_plane_full": result["single_end_plane_scenarios"]["extension_full"],
        "host_vertical_links": sum(1 for item in result["vertical_items"] if item["kind"] == "host_declared_link"),
        "measured_length_rule": part_totals["straight"]["确认"] + part_totals["straight"].get("规则推算",0) + part_totals["fitting"]["规则推算"] + part_totals["vertical"]["规则推算"] + part_totals["accessory_gap"]["规则推算"],
    }
    if 'vertical_annotation_binding' in result:
        missing_surface = sum(i.get('kind') == 'annotation_explicit_height' and i.get('theoretical_surface_area') is None for i in result['vertical_items'])
        result['metrics'].update(length_totals_complete=length_gaps == 0, surface_unresolved_items=missing_surface,
                                 surface_totals_complete=missing_surface == 0,
                                 source_xy_unit_verified=result['vertical_annotation_binding']['evidence']['coordinate_unit'] is not None)
        if missing_surface:
            result['metrics']['totals_complete'] = False


def apply_annotation_unit_dependencies(net: Network, result: dict[str, Any]) -> None:
    """Do not let a legacy declared mm scale certify an unknown source XY scale.

    Keep its calculations explicitly as conditional references. Only independent
    explicit-height verticals remain in the current known quantity subset.
    """
    receipt = result.get('vertical_annotation_binding')
    if receipt is None or receipt['evidence']['coordinate_unit'] is not None:
        return
    conditional = {k: copy.deepcopy(result[k]) for k in ('metrics', 'length_summary', 'theoretical_surface_summary', 'part_totals', 'single_end_plane_scenarios')}
    conditional.update(input_unit=net.unit, output_unit=net.output_unit,
        status='CONDITIONAL_REFERENCE_ONLY',
        basis='仅按冻结输入声明的坐标单位计算；当前源XY比例未核或冲突，本次绑定不认证该声明，不是已确认的米数、面积或完整总量')
    result['conditional_on_input_unit'] = conditional
    for segment in result['straight_segments']:
        segment['length_declared_unit_reference'] = segment['length']
        segment['length'] = None
        segment['evidence_level'] = '未决'
    for fitting in result['fittings']:
        fitting['centerline_length_declared_unit_reference'] = fitting['centerline_length']
        fitting['centerline_length'] = None
        for arm in fitting['arms']:
            arm['length_declared_unit_reference'] = arm['length']
            arm['length'] = None
            arm['evidence_level'] = '未决'
    for pool in ('open_taps', 'accessory_gaps'):
        for item in result[pool]:
            item['length_declared_unit_reference'] = item['length']
            item['length'] = None
            item['evidence_level'] = '未决'
    known = []
    for item in result['vertical_items']:
        if item.get('kind') == 'annotation_explicit_height':
            known.append(item)
        else:
            item['length_declared_unit_reference'] = item['length']
            item['length'] = None
            item['evidence_level'] = '未决'
    summary = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for item in known:
        summary[item['system']]['规则推算'][item['spec']] += item['length']
    result['length_summary'] = {system: {level: dict(specs) for level,specs in levels.items()} for system,levels in summary.items()}
    result['theoretical_surface_summary'] = {}
    subtotal = sum(item['length'] for item in known)
    result['part_totals'] = {'vertical': {'规则推算': subtotal}}
    for key in ('geometry_evidence', 'known_geometry_subtotal', 'extension_minus_half_width', 'extension_full', 'difference_full_minus_default'):
        result['single_end_plane_scenarios'][key] = None
    result['pending'].append({'kind': 'source_xy_unit_unresolved', 'length': None, 'surface_area': None,
        'reason': '原图XY比例未核或与冻结输入冲突；平面米数、截面换算、面积及完整总量未决，原单位声明下的数值只保留为conditional_on_input_unit条件参考；独立明示高度另计已知规则子集'})
    metrics = result['metrics']
    for key in ('straight_length', 'fitting_length_rule', 'fitting_length_scenario', 'vertical_length_scenario',
                'accessory_gap_length_excluded', 'single_end_plane_default', 'single_end_plane_full', 'measured_length_rule'):
        metrics[key] = None
    metrics.update(vertical_length_rule=subtotal, known_vertical_annotation_length_rule=subtotal,
                   totals_complete=False, length_totals_complete=False, surface_totals_complete=False,
                   length_unresolved_items=metrics['length_unresolved_items'] + 1, pending_count=len(result['pending']))


def infer(data: dict[str, Any], *, input_base: Path | None = None) -> dict[str, Any]:
    if data.get("schema_version") != INPUT_SCHEMA:
        fail(f"schema_version 必须为 {INPUT_SCHEMA}")
    net = Network(data)
    result: dict[str, Any] = {"schema_version": RESULT_SCHEMA, "engine_version": VERSION, "status": "REFERENCE_ONLY",
                              "package_id": data.get("package_id"), "unit": net.output_unit, "pending": []}
    net.result = result
    if data.get("drawn_curve_segments"):
        from duct_network_extensions import source_curve_network
        curve_graph, net.curve_endpoint_contacts = source_curve_network(data)
        net.curve_branch_pairs={frozenset(pair) for pair in curve_graph.get('branch_boundary_pairs',[])}
        net.curve_blocked_endpoint_pairs={frozenset((tuple(pair['a']),tuple(pair['b']))) for pair in curve_graph.get('blocked_legacy_endpoint_pairs',[])}
    build_connections(net, result)
    fitting_arms(net, result)
    _, adjacency = build_runs(net)
    elevations = propagate_elevation(net, adjacency)
    if data.get('drawn_curve_segments'):
        from duct_source_recovery import propagate_source_curve_elevations
        curve_elevations,curve_pending=propagate_source_curve_elevations(data,net,adjacency,elevations)
        net.curve_elevations=curve_elevations
        result['pending'].extend(curve_pending)
    vertical_items(net, elevations, result)
    append_annotation_verticals(net, data, result)
    summarize(net, elevations, result)
    apply_annotation_unit_dependencies(net, result)
    result["parameters"] = {**net.params, "parameter_basis": data["parameters"]["parameter_basis"]}
    result["counts"] = {"segments": len(net.segments), "fittings": len(net.fittings), "duplicate_fittings": len(result["duplicate_fittings"]),
                        "joints": len(result["joints"]), "accessory_gaps": len(result["accessory_gaps"]), "open_taps": len(result["open_taps"]),
                        "vertical_items": len(result["vertical_items"]), "vertical_scenarios": len(result["vertical_scenarios"]),
                        "pending": len(result["pending"]),
                        "cross_system_contacts": len(result["cross_system_contacts"]),
                        "fitting_status": dict(sorted(Counter(f["status"] for f in result["fittings"]).items()))}
    result["business_adoption_allowed"] = False
    if hasattr(net,'curve_elevations'):result['source_curve_elevations']=net.curve_elevations
    result["meaning"] = ("直管为已绘中心线确认量；管件中心线与竖向为按块几何、端口和标高标注的规则推算量，含假定者为方案暂估；"
                         "单端去向不明和缺标高的部分列待办，不以零或经验比例代替；vertical_scenarios 是被单端几何未决阻断的层间立管方案，"
                         "不计入任何合计。metrics 各合计只含已知部分，"
                         "totals_complete 为 false 时不得当作完整量。参考值须经专业复核后才能进入成果。")
    if 'conditional_on_input_unit' in result:
        result['meaning'] += ('当前源XY单位未核或冲突：上述直管等量仅在conditional_on_input_unit中按输入声明作条件参考，'
                              '主结果依赖XY换算的量与完整总量为null；明示高度形成的独立竖向规则量作为已知子集保留，不证明平面单位。')
    if any(key in data for key in ("drawn_curve_segments", "coverage", "valves", "riser_projection_candidates")) or net.unit == "raw_DU":
        from duct_network_extensions import append_drawn_and_inline
        append_drawn_and_inline({**data, "_input_base": str(input_base or Path.cwd())}, result, spec_of)
    from duct_color_material import append_material_quantities
    append_material_quantities(data, result)
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
        result = infer(data, input_base=args.input.resolve().parent)
        from bind_duct_vertical_annotations import write_protected_json
        protected = [args.input]
        if 'vertical_annotation_binding' in result:
            ev = result['vertical_annotation_binding']['evidence']
            refs = [ev[k] for k in ('neutral_manifest', 'source', 'exchange_dxf', 'layout_entities', 'block_definitions')]
            refs.append(data['vertical_annotation_input']['duct_input'])
            protected.extend(Path(ref['path']) for ref in refs)
        if any(key in data for key in ("neutral_binding", "valves")):
            from receive_duct_inline_components import protected_sources
            records = protected_sources(args.input.resolve(), args.input.resolve(), Path(__file__).resolve())
            protected.extend(Path(row["path"]) for row in records.values())
        write_protected_json(args.output, result, protected, allow_existing=True)
        print(json.dumps({"status": result["status"], **result["metrics"], "output": str(args.output)}, ensure_ascii=False))
        return 0
    except (OSError, InputError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "stop": True, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
