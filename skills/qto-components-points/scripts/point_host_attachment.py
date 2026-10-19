#!/usr/bin/env python3
"""Opt-in source-geometry placement and conservative host-segment candidates.

A geometry extent centre locates an instance; it never proves a connection port.
Network reception is checked against frozen quantity-result rows, not profile flags.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import qto_cad_geometry as cg

SCHEMA = "qto-point-host-attachment/1.0"
ANNOTATIONS = {"TEXT", "MTEXT", "ATTDEF", "ATTRIB", "POINT", "LEADER", "MLEADER", "DIMENSION"}
IDENTITY = (1., 0., 0., 1., 0., 0., 1., 0.)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reject_reparse_chain(path):
    """Check the original output spelling before resolve() can hide an alias."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"输出路径含symlink/reparse别名，拒绝写入:{part}")


def guard_new_attachment_report(output):
    requested = Path(output)
    requested = requested if requested.is_absolute() else Path.cwd()/requested
    reject_reparse_chain(requested)
    output = Path(os.path.abspath(requested))
    target = output/"点位位置与管段挂接.json"
    reject_reparse_chain(target)
    if os.path.lexists(target):
        raise ValueError("点位位置与管段挂接.json已存在（含链接或原输入），请另存新目录")
    return output


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def known_classification(value):
    return nonempty(value) and value.strip().lower() not in {"unknown", "unresolved", "未决", "待定", "未定", "未知", "?"}


def number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数值")
    if value < 0 or (positive and value <= 0):
        raise ValueError(f"{label} 必须{'大于' if positive else '不小于'}0")
    return float(value)


def frozen_file(ref, base, label):
    if not isinstance(ref, dict) or not nonempty(ref.get("path")):
        raise ValueError(f"{label} 必须声明冻结文件path和sha256")
    path = Path(ref["path"])
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file() or digest(path) != str(ref.get("sha256") or "").lower():
        raise ValueError(f"{label} 文件缺失或SHA256不一致")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} 顶层必须是对象")
    return value, {"path": str(path), "sha256": digest(path)}


def union(boxes):
    return [min(b[i] for b in boxes) for i in range(3)] + [max(b[i+3] for b in boxes) for i in range(3)]


def request_digest(row):
    request = {key: row[key] for key in ("physical_id", "quantity_outlet", "instance_identity", "placement", "candidate_segments")}
    return hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def pipe_producer():
    return Path(__file__).resolve().parents[2] / "qto-pipe-networks/scripts/infer_pipe_network.py"


def validate_pipe_request(report, data, network_base, report_base):
    """The single preflight used by both the consumer and receipt replay.

An actual pipe result may contain other packages. Their stable identities remain
outside this request's uniqueness guarantee, and must be explicitly scoped later.
"""
    if report.get("schema_version") != SCHEMA:
        raise ValueError("点位挂接报告契约无效")
    for key in ("source_sha256", "coordinate_system", "coordinate_unit", "layout", "space_id"):
        if data.get(key) != report.get(key) or report.get(key) is None:
            raise ValueError(f"真实管网输入{key}必须绑定同一中性来源和点位范围")
    if data.get("unit") != report["coordinate_unit"]:
        raise ValueError("真实管网输入unit与点位坐标单位不一致")
    profile, _ = frozen_file(report.get("profile_ref"), report_base, "attachment.profile_ref")
    if profile.get("point_host_attachment", {}).get("receptions"):
        raise ValueError("必须使用尚未引用接收结果的原始挂接请求，禁止循环接收")
    if profile.get("expected_source_sha256") != report["source_sha256"]:
        raise ValueError("原始请求profile与报告原图SHA不一致")
    segments, _ = frozen_file(report.get("segments_ref"), report_base, "attachment.segments_ref")
    if segments.get("source_sha256") != report["source_sha256"]:
        raise ValueError("挂接管段来源已变化")
    rows, valves, source_segments = report.get("rows"), data.get("valves"), data.get("segments")
    if not all(isinstance(v, list) for v in (rows, valves, source_segments)):
        raise ValueError("挂接请求和真实管网输入必须保留完整rows/valves/segments")
    def unique(values, key, label):
        result = {}
        for row in values:
            if not isinstance(row, dict) or not nonempty(row.get(key)) or row[key] in result:
                raise ValueError(f"{label}.{key}必须非空且全集唯一")
            result[row[key]] = row
        return result
    by_pid = unique(rows, "physical_id", "原始请求")
    raw_by_pid = unique(valves, "source_object_id", "真实管网输入")
    unique(valves, "valve_id", "真实管网输入")
    raw_segments = unique(source_segments, "segment_id", "真实管网输入")
    outlets = {}
    for row in profile.get("point_host_attachment", {}).get("outlets", []):
        outlets.setdefault(row.get("physical_id"), []).append(row.get("quantity_outlet"))
    requested = []
    for pid, row in by_pid.items():
        if outlets.get(pid) != [row.get("quantity_outlet")]:
            raise ValueError("原始请求与冻结profile的物理对象数量出口不一致")
        raw = raw_by_pid.get(pid)
        if row.get("quantity_outlet") != "inline_network":
            if raw is not None:
                raise ValueError("真实管网valves全集含本报告独立设备或未定出口，禁止漏报造成双计")
            continue
        if raw is None:
            continue  # legal partial receiver; the absent inline stays pending in the point adapter
        if row.get("host_status") != "unique_spatial_candidate" or row.get("request_sha256") != request_digest(row) or len(row.get("candidate_segments", [])) != 1:
            raise ValueError("inline挂接仍未决或请求指纹已变化，不能生成数量接收")
        host, identity = row["candidate_segments"][0], row["instance_identity"]
        if identity.get("unit") not in {"个", "只", "台", "套"}:
            raise ValueError("inline实际消费只支持离散实例计数单位")
        for key, expected in (("source_sha256", report["source_sha256"]), ("source_instance_path", identity["instance_path"]),
                              ("source_record_id", identity["source_record_id"]), ("quantity_unit", identity["unit"]),
                              ("attachment_request_sha256", row["request_sha256"]), ("system", host["system"]),
                              ("space_id", report["space_id"]), ("point", row["placement"]["geometry_point"]),
                              ("connected_segment_ids", [host["segment_id"]])):
            if raw.get(key) != expected:
                raise ValueError(f"真实管网阀件输入{key}与源实例和挂接请求不一致")
        segment = raw_segments.get(host["segment_id"], {})
        for key, expected in (("source_sha256", report["source_sha256"]), ("source_instance_path", host["instance_path"]),
                              ("source_record_id", host["source_record_id"]), ("start", host["start"]), ("end", host["end"]),
                              ("system", host["system"]), ("space_id", report["space_id"])):
            if segment.get(key) != expected:
                raise ValueError(f"真实管网管段{key}与完整路径源几何不一致")
        evidence, evidence_ref = frozen_file(raw.get("connection_evidence"), network_base, "connection_evidence")
        expected = {"schema_version": "qto-inline-connection-evidence/1.0", "source_sha256": report["source_sha256"],
                    "physical_id": pid, "segment_id": host["segment_id"], "layout": report["layout"],
                    "space_id": report["space_id"], "relationship": "inline_connection"}
        if (any(evidence.get(k) != v for k, v in expected.items()) or not nonempty(evidence.get("evidence_id"))
                or evidence["evidence_id"] != raw.get("connection_source_id")
                or not nonempty(evidence.get("basis")) or not nonempty(evidence.get("source_locator"))):
            raise ValueError("显式在线连接依据契约/来源/对象/范围/定位不一致")
        requested.append((row, raw, host, evidence_ref))
    if not requested:
        raise ValueError("本报告没有被真实管网输入承接的inline实例")
    return requested


def verify_pipe_consumption(receipt, base, *, replay=True, current_outlets=None):
    """Recheck actual producer output and its quantity rows. A profile cannot assert receipt."""
    inp, inp_ref = frozen_file(receipt.get("network_input"), base, "network_input")
    result, result_ref = frozen_file(receipt.get("network_result"), base, "network_result")
    request, _ = frozen_file(receipt.get("attachment_request"), base, "attachment_request")
    validated_request = validate_pipe_request(request, inp, Path(inp_ref["path"]).parent,
                                             Path(receipt["attachment_request"]["path"]).parent)
    original_profile, _ = frozen_file(request.get("profile_ref"), Path(receipt["attachment_request"]["path"]).parent, "attachment_request.profile_ref")
    if original_profile.get("point_host_attachment", {}).get("receptions"):
        raise ValueError("接收凭据必须从未引用接收结果的原始请求生成，禁止循环依赖")
    for key in ("source_sha256", "coordinate_system", "coordinate_unit", "layout", "space_id"):
        if inp.get(key) != receipt.get(key) or request.get(key) != receipt.get(key):
            raise ValueError("实际管网消费输入、原始请求及接收结果的来源/单位/范围不一致")
    if inp.get("unit") != receipt.get("coordinate_unit"):
        raise ValueError("实际管网消费单位与接收坐标单位不一致")
    producer = pipe_producer()
    if receipt.get("producer_sha256") != digest(producer):
        raise ValueError("接收结果生产者版本已变化，必须重跑实际管网消费")
    if replay:
        with tempfile.TemporaryDirectory(prefix="point-reception-") as directory:
            output = Path(directory) / "replay.json"
            try:
                run = subprocess.run([sys.executable, "-B", str(producer), "--input", inp_ref["path"], "--output", str(output)],
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=180)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("实际管网消费重放超时，数量接收未核验") from exc
            if run.returncode != 0 or not output.is_file():
                raise ValueError("接收结果实际管网消费重放失败:" + run.stderr[-500:])
            if json.loads(output.read_text(encoding="utf-8-sig")) != result:
                raise ValueError("接收结果与相同版本实际管网消费输出不一致")
    valves = result.get("valves")
    if not isinstance(valves, list) or result.get("metrics", {}).get("valve_count") != len(valves):
        raise ValueError("管网实际valves明细和数量不一致")
    if sum(r.get("instance_count", 0) for r in result.get("valve_summary", [])) != len(valves):
        raise ValueError("管网实际阀件汇总与明细不守恒")
    request_rows = request.get("rows")
    items = receipt.get("quantity_items")
    raw_valves = inp.get("valves")
    if not isinstance(request_rows, list) or not isinstance(items, list) or not isinstance(raw_valves, list):
        raise ValueError("接收对账缺少请求、数量行或实际输入全集")
    def unique(values, key, label):
        result = {}
        for row in values:
            if not isinstance(row, dict) or not nonempty(row.get(key)) or row[key] in result:
                raise ValueError(f"{label}.{key}必须非空且全集唯一")
            result[row[key]] = row
        return result
    request_by_pid = unique(request_rows, "physical_id", "原始请求")
    items_by_pid = unique(items, "physical_id", "接收数量行")
    unique(items, "network_valve_id", "接收数量行")
    indices = [row.get("network_valve_index") for row in items]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in indices) or len(set(indices)) != len(indices):
        raise ValueError("接收数量行的实际valves行定位重复")
    raw_by_id = unique(raw_valves, "valve_id", "真实管网输入")
    actual_by_id = unique(valves, "valve_id", "真实管网结果")
    raw_by_pid = unique(raw_valves, "source_object_id", "真实管网输入")
    actual_by_pid = unique(valves, "source_object_id", "真实管网结果")
    if set(raw_by_id) != set(actual_by_id) or set(raw_by_pid) != set(actual_by_pid):
        raise ValueError("真实管网输入与结果全集物理身份/阀件身份不守恒")
    for valve_id, raw in raw_by_id.items():
        if actual_by_id[valve_id]["source_object_id"] != raw["source_object_id"]:
            raise ValueError("真实管网输入与结果物理ID对应关系不一致")
    original_outlets = {}
    for row in original_profile.get("point_host_attachment", {}).get("outlets", []):
        original_outlets.setdefault(row.get("physical_id"), []).append(row.get("quantity_outlet"))
    for pid, bound in request_by_pid.items():
        if original_outlets.get(pid) != [bound.get("quantity_outlet")]:
            raise ValueError("原始请求与冻结profile的物理对象数量出口不一致")
    for pid in actual_by_pid:
        if pid in request_by_pid and request_by_pid[pid].get("quantity_outlet") != "inline_network":
            raise ValueError("真实管网valves全集含本报告独立设备或未定出口，禁止漏报造成双计")
        if current_outlets is not None and pid in current_outlets:
            declared = current_outlets[pid]
            if len(declared) != 1 or declared[0].get("quantity_outlet") != "inline_network":
                raise ValueError("真实管网valves全集命中本轮独立设备或多出口，禁止跨请求漏报双计")
    inside = set(actual_by_pid) & set(request_by_pid)
    if set(items_by_pid) != inside:
        raise ValueError("本报告inline实际明细与接收数量行必须逐对象一一守恒，禁止漏报或夹入")
    outside = set(actual_by_pid) - set(request_by_pid)
    coverage = receipt.get("quantity_scope")
    if coverage is None and outside:
        raise ValueError("真实管网结果含报告外对象，必须明确声明报告覆盖及旁路范围")
    if coverage is not None:
        expected_scope = {"request_physical_ids": set(request_by_pid), "received_inline_physical_ids": set(items_by_pid),
                          "outside_request_physical_ids": outside}
        if not isinstance(coverage, dict):
            raise ValueError("quantity_scope必须为对象")
        for key, expected in expected_scope.items():
            values = coverage.get(key)
            if not isinstance(values, list) or any(not nonempty(v) for v in values) or len(values) != len(set(values)) or set(values) != expected:
                raise ValueError(f"quantity_scope.{key}未与实际全集及报告范围逐对象核对")
    input_segments = {row.get("segment_id"): row for row in inp.get("segments", [])}
    rows = []
    for item in items:
        index = item.get("network_valve_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(valves):
            raise ValueError("管网数量接收行缺少实际valves原始行定位")
        actual = valves[index]
        if item.get("network_valve_id") != actual.get("valve_id"):
            raise ValueError("接收数量行阀件ID与实际原始行定位不一致")
        raw = raw_by_id.get(actual.get("valve_id"), {})
        matches = [row for row in request.get("rows", []) if row.get("physical_id") == item.get("physical_id")]
        if len(matches) != 1 or matches[0].get("request_sha256") != item.get("request_sha256") or request_digest(matches[0]) != item.get("request_sha256"):
            raise ValueError("接收行与冻结的原始挂接请求不一致")
        bound = matches[0]
        if bound.get("quantity_outlet") != "inline_network" or bound.get("host_status") != "unique_spatial_candidate" or len(bound.get("candidate_segments", [])) != 1:
            raise ValueError("原始挂接请求不是唯一可核inline候选")
        host = bound["candidate_segments"][0]
        source_segment = input_segments.get(item.get("segment_id"), {})
        segment_expected = {"source_sha256": receipt["source_sha256"], "source_instance_path": host["instance_path"],
                            "source_record_id": host["source_record_id"], "start": host["start"], "end": host["end"],
                            "system": host["system"], "space_id": receipt["space_id"]}
        if any(source_segment.get(k) != v for k, v in segment_expected.items()) or raw.get("point") != bound["placement"]["geometry_point"]:
            raise ValueError("实际管网输入源管段或点位坐标不匹配完整路径几何")
        evidence, _ = frozen_file(raw.get("connection_evidence"), Path(inp_ref["path"]).parent, "connection_evidence")
        if (evidence.get("source_sha256") != receipt["source_sha256"] or evidence.get("physical_id") != item["physical_id"]
                or evidence.get("segment_id") != item["segment_id"] or evidence.get("evidence_id") != raw.get("connection_source_id")
                or evidence.get("relationship") != "inline_connection" or evidence.get("layout") != receipt["layout"]
                or evidence.get("space_id") != receipt["space_id"] or not evidence.get("basis") or not evidence.get("source_locator")):
            raise ValueError("接收行显式在线连接依据与原始对象和范围不一致")
        if (actual.get("source_object_id") != item.get("physical_id")
                or actual.get("connection_method") != "evidence_bound_connection"
                or actual.get("attached_segment_ids") != [item.get("segment_id")]
                or actual.get("system") != item.get("system") or actual.get("dn") != item.get("spec")
                or actual.get("valve_type") in (None, "", "未分类")
                or raw.get("source_sha256") != item.get("source_sha256")
                or raw.get("source_instance_path") != item.get("source_instance_path")
                or raw.get("source_record_id") != item.get("source_record_id")
                or raw.get("quantity_unit") != item.get("unit")
                or raw.get("attachment_request_sha256") != item.get("request_sha256")):
            raise ValueError("管网接收行未与实际消费输入和结果逐对象匹配")
        identity = bound["instance_identity"]
        expected_item = {"source_sha256": request["source_sha256"], "source_instance_path": identity["instance_path"],
                         "source_record_id": identity["source_record_id"], "quantity_type": "inline_accessory", "unit": identity["unit"],
                         "quantity": 1, "status": "received", "layout": request["layout"], "space_id": request["space_id"]}
        levels = {"方案暂估量": 0, "规则推算量": 1, "确认量": 2}
        if (any(item.get(k) != v for k, v in expected_item.items()) or isinstance(item.get("quantity"), bool)
                or item.get("quantity_level") not in levels
                or levels[item["quantity_level"]] > levels.get(identity.get("quantity_level"), -1)):
            raise ValueError("接收数量类型/离散单位/层级与原始实例不一致")
        rows.append(item)
    return rows


class Attachment:
    def __init__(self, config, *, definitions, origins, layout_rows, factor, coordinate_unit,
                 source_sha256, layout, space_id, profile_path, neutral_refs, max_depth=16,
                 classification_evidence=None, classification_evidence_base=None, classification_evidence_ref=None):
        if not isinstance(config, dict) or config.get("schema_version") != SCHEMA:
            raise ValueError(f"point_host_attachment.schema_version 必须为 {SCHEMA}")
        self.config, self.definitions, self.origins = config, definitions, origins
        self.factor, self.unit, self.source = factor, coordinate_unit, source_sha256
        self.layout, self.space, self.max_depth = layout, space_id, max_depth
        self.neutral_refs = neutral_refs
        self.tolerance = number(config.get("tolerance"), "point_host_attachment.tolerance", True)
        self.large_offset = number(config.get("large_offset_warning", self.tolerance * 10), "large_offset_warning", True)
        self.segments = []
        self.received, self.reception_refs = [], []
        self.reception_scopes = []
        self.classification_records = {}
        self.classification_source_refs = []
        for row in classification_evidence or []:
            if isinstance(row, dict) and nonempty(row.get("evidence_id")):
                self.classification_records.setdefault(row["evidence_id"], []).append(
                    (row, Path(classification_evidence_base or profile_path.parent),
                     classification_evidence_ref or {"kind": "current_ledger_evidence"}))
        self.outlets = config.get("outlets")
        if not isinstance(self.outlets, list):
            raise ValueError("point_host_attachment.outlets 必须是数组，逐物理实例声明唯一数量出口")
        self.outlet_map = {}
        for row in self.outlets:
            if not isinstance(row, dict) or not nonempty(row.get("physical_id")):
                raise ValueError("outlets行缺少physical_id")
            self.outlet_map.setdefault(row["physical_id"], []).append(row)
        self.segment_ref = None
        if config.get("segments") is not None:
            data, self.segment_ref = frozen_file(config["segments"], profile_path.parent, "segments")
            for key, expected in (("schema_version", "qto-point-host-segments/1.0"), ("source_sha256", self.source),
                                  ("coordinate_system", "WCS"), ("coordinate_unit", self.unit), ("layout", self.layout)):
                if data.get(key) != expected:
                    raise ValueError(f"segments.{key} 与当前点位来源/单位/坐标系不一致")
            values = data.get("segments")
            if not isinstance(values, list):
                raise ValueError("segments.segments 必须是数组")
            sources = data.get("classification_sources", [])
            if not isinstance(sources, list):
                raise ValueError("classification_sources必须是冻结分类来源文件数组")
            for ref in sources:
                bundle, bundle_ref = frozen_file(ref, Path(self.segment_ref["path"]).parent, "classification_sources")
                self.classification_source_refs.append(bundle_ref)
                if (bundle.get("schema_version") != "qto-host-segment-classifications/1.0"
                        or bundle.get("source_sha256") != self.source or not isinstance(bundle.get("evidence"), list)):
                    raise ValueError("冻结分类来源契约/原图SHA/证据数组无效")
                for row in bundle["evidence"]:
                    if not isinstance(row, dict) or not nonempty(row.get("evidence_id")):
                        raise ValueError("冻结分类来源记录缺少evidence_id")
                    self.classification_records.setdefault(row["evidence_id"], []).append(
                        (row, Path(bundle_ref["path"]).parent, bundle_ref))
            wanted = {r.get("instance_path") for r in values if isinstance(r, dict)}
            source_rows = {}
            for row in layout_rows:
                if row.get("layout") != self.layout:
                    continue
                for path, leaf, matrix, inherited_layer in self.walk(row, str(row.get("record_id") or "NO-ID"), IDENTITY):
                    if path in wanted:
                        if path in source_rows:
                            raise ValueError("管段完整实例路径重复，来源身份不唯一")
                        source_rows[path] = (leaf, matrix, inherited_layer)
            ids = set()
            for raw in values:
                if not isinstance(raw, dict) or not nonempty(raw.get("segment_id")) or raw["segment_id"] in ids:
                    raise ValueError("segment_id 缺失或重复")
                ids.add(raw["segment_id"])
                leaf_data = source_rows.get(raw.get("instance_path"))
                if leaf_data is None:
                    raise ValueError("管段instance_path未在当前中性来源的完整实例路径中找到")
                leaf, matrix, layer = leaf_data
                if leaf.get("record_id") != raw.get("source_record_id") or leaf.get("geometry_issues"):
                    raise ValueError("管段源实体ID不符或几何不完备")
                geometry = leaf.get("geometry")
                if not isinstance(geometry, dict):
                    raise ValueError("管段缺少可核验源几何")
                part_index = raw.get("part_index", 0)
                if isinstance(part_index, bool) or not isinstance(part_index, int) or part_index < 0:
                    raise ValueError("管段part_index必须是非负整数")
                transformed = cg.transform_geometry(geometry, matrix)
                cg.summarize(transformed)  # validate schema, WCS and finite source geometry, not just endpoints
                parts = transformed.get("parts", [])
                if part_index >= len(parts) or parts[part_index].get("kind") != "line":
                    raise ValueError("当前挂接仅支持源几何的直线段，曲线须另建有依据的适配")
                part = parts[part_index]
                start, end = ([v*self.factor for v in part[k]] for k in ("start", "end"))
                if math.dist(start, end) <= 0:
                    raise ValueError("管段源几何长度必须大于0")
                # Coordinates are computed from source leaves; supplied coordinates cannot replace them.
                for key, actual in (("start", start), ("end", end)):
                    if key in raw and (not isinstance(raw[key], list) or raw[key] != actual):
                        raise ValueError(f"管段{key}与完整路径变换的源几何不一致")
                segment = {**raw, "start": start, "end": end, "source_layer": layer,
                           "source_sha256": self.source, "coordinate_system": "WCS", "coordinate_unit": self.unit}
                segment["classification_proofs"], segment["classification_problems"] = self.classification_for(segment)
                self.segments.append(segment)
        refs = config.get("receptions", [])
        if not isinstance(refs, list):
            raise ValueError("receptions 必须是冻结管网数量结果文件数组")
        for ref in refs:
            result, receipt_ref = frozen_file(ref, profile_path.parent, "receptions")
            if result.get("schema_version") != "qto-network-component-reception/1.0":
                raise ValueError("管网接收结果不支持当前数量接收契约；旧valves候选不等于数量接收")
            owner = result.get("professional_owner")
            if owner != "qto-pipe-networks":
                raise ValueError("当前实际数量消费适配仅支持管网infer_pipe_network，风管仍待独立接收凭据")
            for key, expected in (("source_sha256", self.source), ("coordinate_system", "WCS"),
                                  ("coordinate_unit", self.unit), ("layout", self.layout), ("space_id", self.space)):
                if result.get(key) != expected:
                    raise ValueError(f"管网接收结果{key}与当前来源及范围不符")
            if not nonempty(result.get("workpackage_id")) or not isinstance(result.get("quantity_items"), list):
                raise ValueError("管网接收结果缺少workpackage_id/quantity_items")
            self.reception_refs.append(receipt_ref)
            verified_rows = verify_pipe_consumption(result, Path(receipt_ref["path"]).parent, current_outlets=self.outlet_map)
            self.reception_scopes.append({"result_ref": receipt_ref, "quantity_scope": result.get("quantity_scope"),
                                          "guarantee": "only_current_report_instances_not_global_network_uniqueness"})
            for index, item in enumerate(verified_rows):
                if not isinstance(item, dict) or not nonempty(item.get("physical_id")):
                    raise ValueError("管网数量接收行缺少physical_id")
                self.received.append({**item, "professional_owner": owner,
                                      "result_ref": {**receipt_ref, "row_index": index}, "workpackage_id": result["workpackage_id"]})

    def classification_for(self, segment):
        ids = segment.get("classification_evidence_ids")
        if not isinstance(ids, list) or not ids or any(not nonempty(v) for v in ids) or len(ids) != len(set(ids)):
            return [], ["管段分类证据ID缺失、重复或无效"]
        proofs, problems = [], []
        expected_scope = {"kind": "source_instance_part", "layout": self.layout, "space_id": segment.get("space_id"),
                          "source_record_id": segment["source_record_id"], "instance_path": segment["instance_path"],
                          "part_index": segment.get("part_index", 0)}
        for eid in ids:
            records = self.classification_records.get(eid, [])
            if len(records) != 1:
                problems.append(f"分类证据{eid}不存在或来源不唯一")
                continue
            record, base, record_ref = records[0]
            try:
                if record.get("source_sha256") != self.source:
                    raise ValueError("分类记录绑定的原图SHA不一致")
                if not nonempty(record.get("locator")) or not nonempty(record.get("observation")):
                    raise ValueError("分类原始资料缺少来源定位/观察记录")
                source = record.get("path")
                if not nonempty(source):
                    raise ValueError("分类记录缺少原始资料路径和SHA")
                path = Path(source)
                path = (base/path).resolve() if not path.is_absolute() else path.resolve()
                if not path.is_file() or digest(path) != str(record.get("sha256") or "").lower():
                    raise ValueError("分类原始资料缺失或SHA已变化")
                classifications = record.get("host_segment_classifications")
                if not isinstance(classifications, list):
                    raise ValueError("证据ID虽存在但没有本段系统规格的作用范围和值")
                matched = [v for v in classifications if isinstance(v, dict) and v.get("segment_id") == segment["segment_id"]]
                if len(matched) != 1:
                    raise ValueError("分类记录没有唯一对应本段的分类事实")
                value = matched[0]
                if value.get("scope") != expected_scope:
                    raise ValueError("分类事实的layout/空间/完整路径/源实体/part作用范围不一致")
                if (not known_classification(value.get("system")) or not known_classification(value.get("spec"))
                        or value["system"] != segment.get("system") or value["spec"] != segment.get("spec")):
                    raise ValueError("分类事实的明确系统规格值与当前段声明不一致或未知")
                if not nonempty(value.get("basis")) or not nonempty(value.get("source_locator")):
                    raise ValueError("分类事实缺少专业判断依据及原始来源定位")
                proofs.append({"evidence_id": eid, "record_ref": record_ref,
                               "record_sha256": hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                               "source": {"path": str(path), "sha256": digest(path)},
                               "source_sha256": self.source, "classification": value,
                               "meaning": "source-bound working classification; not human approval or formal quantity"})
            except (ValueError, OSError) as exc:
                problems.append(f"分类证据{eid}未核实:{exc}")
        return proofs, problems

    def walk(self, row, path, parent, stack=(), inherited_layer=""):
        layer = row.get("layer") or "0"
        layer = inherited_layer if layer == "0" and inherited_layer else layer
        if row.get("entity_type") != "INSERT":
            yield path, row, parent, layer
            return
        name = str(row.get("block_name") or "")
        if row.get("geometry_issues") or name in stack or len(stack) >= self.max_depth or name not in self.definitions:
            yield path, {**row, "geometry_issues": ["missing/incomplete block definition or transform"]}, parent, layer
            return
        matrix = cg.compose_matrix(parent, cg.matrix_for_insert(row, self.origins.get(name, [0., 0., 0.])))
        for child in self.definitions[name]:
            yield from self.walk(child, f"{path}/{name}/{child.get('record_id') or 'NO-ID'}", matrix, (*stack, name), layer)

    def placement(self, insert, parent_matrix, path, insertion_point):
        boxes, members, problems, excluded = [], [], [], []
        for leaf_path, row, matrix, layer in self.walk(insert, path, parent_matrix):
            if row.get("entity_type") in ANNOTATIONS:
                excluded.append({"instance_path": leaf_path, "entity_type": row.get("entity_type")})
                continue
            try:
                if row.get("geometry_issues") or row.get("geometry_status") in {"unsupported", "requires_reextraction", "partial", "incomplete"} or not isinstance(row.get("geometry"), dict):
                    raise ValueError("叶图元缺少完整受支持的源几何")
                summary = cg.summarize(cg.transform_geometry(row["geometry"], matrix))
                box = summary.get("bbox3d")
                if box is None:
                    raise ValueError("叶图元没有几何外形")
                boxes.append([v*self.factor for v in box])
                members.append({"source_record_id": row.get("record_id"), "instance_path": leaf_path,
                                "instance_transform": list(cg.matrix16(matrix)), "source_layer": layer})
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                problems.append({"instance_path": leaf_path, "source_record_id": row.get("record_id"), "problem": str(exc)})
        box = union(boxes) if boxes else None
        point = [(box[i]+box[i+3])/2 for i in range(3)] if box else None
        anchor = [v*self.factor for v in insertion_point]
        displacement = [point[i]-anchor[i] for i in range(3)] if point else None
        distance = math.dist(anchor, point) if point else None
        return {"insertion_point": anchor, "geometry_point": point, "geometry_bbox3d": box,
                "displacement": displacement, "displacement_distance": distance,
                "large_displacement_warning": distance is not None and distance > self.large_offset,
                "position_status": "complete_geometry_extent" if box and not problems else "partial_geometry_extent" if box else "geometry_unavailable",
                "position_basis": "full_instance_path_source_geometry_extent_center" if box else "insertion_anchor_fallback_requires_review",
                "connection_port_verified": False, "coordinate_system": "WCS", "coordinate_unit": self.unit,
                "source_sha256": self.source, "instance_path": path, "source_members": members,
                "excluded_annotations": excluded, "geometry_problems": problems, "neutral_source_artifacts": self.neutral_refs}

    def candidates(self, placement, outlet):
        point = placement.get("geometry_point")
        candidates, problems = [], []
        if placement.get("position_status") != "complete_geometry_extent":
            return [], ["缺少完整源几何位置，不能按插入点替代挂接"]
        allowed_layers = outlet.get("host_layers")
        if not isinstance(allowed_layers, list) or not allowed_layers or any(not nonempty(x) for x in allowed_layers):
            problems.append("未声明允许的管段源图层，跨层近邻不自动挂接")
        for segment in self.segments:
            a, b = segment["start"], segment["end"]
            ab = [b[i]-a[i] for i in range(3)]
            t = max(0., min(1., sum((point[i]-a[i])*ab[i] for i in range(3))/sum(v*v for v in ab)))
            projection = [a[i]+t*ab[i] for i in range(3)]
            distance = math.dist(point, projection)
            # Preserve all nearby segments, including different systems/spaces/layers: never pre-filter ambiguity away.
            if distance > self.tolerance:
                continue
            issues = []
            if segment.get("space_id") != self.space:
                issues.append("管段空间与点位范围不一致")
            if not allowed_layers or segment["source_layer"] not in allowed_layers:
                issues.append("管段源图层未在本实例允许范围")
            evidence = segment.get("classification_evidence_ids")
            if (not known_classification(segment.get("system")) or not known_classification(segment.get("spec"))
                    or not isinstance(evidence, list) or not evidence or any(not nonempty(v) for v in evidence)):
                issues.append("管段系统/规格或分类依据缺失")
            issues.extend(segment.get("classification_problems") or [])
            candidates.append({**segment, "distance": distance, "projected_point": projection, "candidate_problems": issues})
        if not candidates:
            problems.append("容差内无可核管段")
        elif len(candidates) != 1:
            problems.append("容差内存在多段/同位/交叉候选，禁止最近段静默选择")
        else:
            problems.extend(candidates[0]["candidate_problems"])
        return candidates, problems

    def apply(self, dispositions, adopted, endpoints):
        by_id = {r["physical_id"]: r for r in dispositions}
        known = set(by_id)
        if set(self.outlet_map) - known:
            raise ValueError("outlets引用了本轮范围内不存在的物理ID")
        rows, keep = [], []
        inline_pending, outlet_pending = 0, 0
        for item in adopted:
            pid = item["physical_id"]
            outlets = self.outlet_map.get(pid, [])
            role = outlets[0].get("quantity_outlet") if len(outlets) == 1 else None
            problems = []
            if role not in {"independent_point", "inline_network"}:
                problems.append("未声明唯一数量出口或存在多出口")
                outlet_pending += 1
            outlet = outlets[0] if len(outlets) == 1 else {}
            position = item.get("placement_evidence", {})
            if item.get("merged_member_ids"):
                # Existing identity grouping does not by itself prove that the primary glyph is the entire assembly outline.
                position = {**position, "position_status": "grouped_primary_geometry_only",
                            "merged_member_ids": item["merged_member_ids"],
                            "geometry_problems": [*position.get("geometry_problems", []),
                                                  {"problem": "归并实例仅保留主角色几何，组合外形位置尚未复核"}]}
            candidates, host_problems = self.candidates(position, outlet)
            host = candidates[0] if len(candidates) == 1 and not host_problems else None
            receipts = [r for r in self.received if r["physical_id"] == pid]
            accepted = False
            if role == "inline_network":
                if host is None:
                    problems.extend(host_problems)
                if len(receipts) != 1:
                    problems.append("管网数量结果零条或多条接收，唯一数量出口未闭合")
                elif host is not None:
                    r = receipts[0]
                    expected = {"source_sha256": self.source, "physical_id": pid, "source_instance_path": item.get("instance_path"),
                                "source_record_id": item.get("source_record_id"), "quantity_type": "inline_accessory",
                                "unit": item["unit"], "quantity": 1, "status": "received", "segment_id": host["segment_id"],
                                "space_id": self.space, "layout": self.layout, "system": host["system"], "spec": host["spec"]}
                    wrong = [key for key, value in expected.items() if r.get(key) != value]
                    if wrong or isinstance(r.get("quantity"), bool) or r.get("quantity_level") not in {"规则推算量", "方案暂估量", "确认量"}:
                        problems.append("管网实际数量接收行的来源/物理ID/段/范围/数量类型单位不符:" + ",".join(wrong))
                    elif r["quantity_level"] == "确认量" and item.get("quantity_level") != "确认量":
                        problems.append("管网接收不得升级点位来源的数量层级")
                    else:
                        accepted = True
                if not accepted:
                    inline_pending += 1
            elif role == "independent_point":
                if receipts:
                    problems.append("独立设备同时进入管网数量结果，存在重复出口")
                    outlet_pending += 1
                else:
                    keep.append(item)
            attachment = {"physical_id": pid, "quantity_outlet": role, "placement": position,
                          "instance_identity": {key: item.get(key) for key in ("source_sha256", "source_record_id", "instance_path", "category", "unit", "quantity_level")},
                          "host_status": "unique_spatial_candidate" if host else "review_required",
                          "candidate_segments": candidates, "host_problems": host_problems,
                          "connection_proven": False, "connection_basis": "spatial_candidate_only_not_a_port",
                          "inherited_system": host["system"] if host else None, "inherited_spec": host["spec"] if host else None,
                          "quantity_outlet_status": "received_by_network" if accepted else "point_count_only" if role == "independent_point" and not problems else "blocked",
                          "quantity_problems": problems, "receiving_quantity_rows": receipts, "business_adoption_allowed": False}
            attachment["request_sha256"] = request_digest(attachment)
            if accepted and receipts[0].get("request_sha256") != attachment["request_sha256"]:
                accepted = False
                inline_pending += 1
                attachment["quantity_outlet_status"] = "blocked"
                attachment["quantity_problems"].append("接收凭据请求指纹过期，源几何/管段/实例出口已经改变")
            rows.append(attachment)
            by_id[pid]["host_attachment"] = attachment
            item["host_attachment"] = attachment
            if item not in keep:
                by_id[pid]["disposition"] = "network_received" if accepted else "quantity_outlet_pending"
        adopted[:] = keep
        # An inline valve is not a terminal endpoint. Pending cases stay in the attachment report, not duplicated as terminals.
        endpoint_ids = {r["physical_id"] for r in keep}
        endpoints[:] = [r for r in endpoints if r["physical_id"] in endpoint_ids]
        return {"schema_version": SCHEMA, "rows": rows, "inline_pending_count": inline_pending,
                "point_outlet_pending_count": outlet_pending, "quantity_closure_complete": not inline_pending and not outlet_pending,
                "host_pending_count": sum(r["host_status"] != "unique_spatial_candidate" for r in rows),
                "network_received_count": sum(r["quantity_outlet_status"] == "received_by_network" for r in rows),
                "independent_point_count": len(keep), "segments_ref": self.segment_ref, "reception_refs": self.reception_refs,
                "reception_scopes": self.reception_scopes, "classification_source_refs": self.classification_source_refs,
                "quantity_outlet_guarantee_scope": "current_report_instances_only",
                "business_adoption_allowed": False, "meaning": "几何中心仅为实例定位；空间候选不证明端口或连通，数量接收须按管网结果原始明细对账"}
