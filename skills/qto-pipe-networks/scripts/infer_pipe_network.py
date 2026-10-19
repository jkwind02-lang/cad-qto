#!/usr/bin/env python3
"""Build auditable pipe topology and layered DN/valve candidates.

The input contains evidence-identified pipe segments and evidence-bound seeds. This
tool does not discover pipe layers, parse arbitrary DN text, perform hydraulic
design, or add virtual gaps to measured length.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from pipe_gap_candidates import candidates as pipe_gap_candidates, gap_limits, protect_outputs, source_paths


INPUT_VERSIONS = {
    "qto-pipe-network-inference-input/1.0",
    "qto-pipe-network-inference-input/1.1",
    "qto-pipe-network-inference-input/1.2",
}
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from model_recognition_basis import validate_working_basis
from qto_pipe_scope_quantity import boundary_context, restore_omissions
from qto_pipe_topology import (prepare_network, make_endpoint_nodes, gap_pairs, add_interfaces,
    NodeLocator, RouteIndex, ambiguous_role_nodes, finalize_network, review_markdown)
OUTPUT_VERSION = "qto-pipe-network-inference-evidence/1.2"
LEVEL_RANK = {"scenario": 1, "inferred": 2, "confirmed": 3}
LEVEL_ZH = {"scenario": "方案暂估量", "inferred": "规则推算量", "confirmed": "确认量"}


class InputError(ValueError):
    pass


def fail(message: str) -> None:
    raise InputError(message)


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{label} 必须是有限数值")
    number = float(value)
    if not math.isfinite(number):
        fail(f"{label} 必须是有限数值")
    return number


def nonnegative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{label} 必须是非负整数")
    return value


def point(value: Any, label: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) not in (2, 3):
        fail(f"{label} 必须是二维或三维坐标")
    return tuple(finite(item, label) for item in value)


def distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        fail("同一网络坐标维度不一致")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def vector(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(y - x for x, y in zip(a, b))


def norm(v: tuple[float, ...]) -> float:
    return math.sqrt(sum(x * x for x in v))


def angle_deg(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    na, nb = norm(a), norm(b)
    if na == 0 or nb == 0:
        return 180.0
    cosine = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
    return math.degrees(math.acos(cosine))


def point_segment_distance(
    p: tuple[float, ...], a: tuple[float, ...], b: tuple[float, ...]
) -> float:
    ab = vector(a, b)
    ap = vector(a, p)
    denominator = sum(value * value for value in ab)
    if denominator == 0:
        return distance(p, a)
    ratio = max(0.0, min(1.0, sum(x * y for x, y in zip(ap, ab)) / denominator))
    projection = tuple(x + ratio * y for x, y in zip(a, ab))
    return distance(p, projection)


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取输入 JSON: {exc}")
    if not isinstance(value, dict):
        fail("输入 JSON 顶层必须是对象")
    return value


def seed_level(value: Any, label: str) -> str:
    level = text(value, label)
    if level not in LEVEL_RANK:
        fail(f"{label} 必须是 confirmed、inferred 或 scenario")
    return level


def stronger_level(levels: list[str]) -> str:
    return max(levels, key=lambda item: LEVEL_RANK[item])


def weaker_level(levels: list[str]) -> str:
    return min(levels, key=lambda item: LEVEL_RANK[item])


def main() -> int:
    parser = argparse.ArgumentParser(description="建立管网虚拟拓扑并形成分层 DN/阀门候选")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        data = load(args.input.resolve())
        protected_sources = source_paths(data, args.input)
        protect_outputs([args.output], protected_sources)
        data, route_preparation = prepare_network(data)
        uncertain_change_systems = set()
        validate_working_basis(data.get("recognition_basis"))
        input_version = data.get("schema_version")
        if input_version not in INPUT_VERSIONS:
            fail(f"schema_version 必须为 {sorted(INPUT_VERSIONS)} 之一")
        network_id = text(data.get("network_id"), "network_id")
        unit = text(data.get("unit"), "unit")
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            fail("parameters 必须是对象")
        snap = finite(parameters.get("snap_tolerance", 0.01), "snap_tolerance")
        gap_max = finite(parameters.get("gap_max", 0.5), "gap_max")
        direction_tolerance = finite(
            parameters.get("direction_tolerance_deg", 20.0), "direction_tolerance_deg"
        )
        terminal_max = finite(
            parameters.get("terminal_attach_max", gap_max), "terminal_attach_max"
        )
        valve_max = finite(parameters.get("valve_attach_max", gap_max), "valve_attach_max")
        fitting_angle_tolerance = finite(
            parameters.get("fitting_angle_tolerance_deg", 10.0),
            "fitting_angle_tolerance_deg",
        )
        main_min = int(parameters.get("main_min_downstream_terminals", 3))
        if (
            snap < 0
            or gap_max <= snap
            or not 0 < direction_tolerance < 90
            or not 0 < fitting_angle_tolerance < 45
            or main_min < 2
        ):
            fail("拓扑阈值无效")

        raw_selection = data.get("object_selection")
        if input_version == "qto-pipe-network-inference-input/1.2" and not isinstance(
            raw_selection, dict
        ):
            fail("1.2 输入必须提供 object_selection 对象审计结果")
        if isinstance(raw_selection, dict):
            selection_status = text(raw_selection.get("status"), "object_selection.status")
            if selection_status not in ("READY", "REVIEW_REQUIRED"):
                fail("object_selection.status 必须是 READY 或 REVIEW_REQUIRED")
            selection_candidate_count = nonnegative_integer(
                raw_selection.get("candidate_count"), "object_selection.candidate_count"
            )
            selection_included_count = nonnegative_integer(
                raw_selection.get("included_count"), "object_selection.included_count"
            )
            selection_excluded_count = nonnegative_integer(
                raw_selection.get("excluded_count"), "object_selection.excluded_count"
            )
            selection_unresolved_count = nonnegative_integer(
                raw_selection.get("unresolved_count"), "object_selection.unresolved_count"
            )
            if selection_candidate_count != (
                selection_included_count + selection_excluded_count + selection_unresolved_count
            ):
                fail("object_selection 候选数与纳入、排除、未决数不守恒")
            included_segment_ids = raw_selection.get("included_segment_ids")
            if not isinstance(included_segment_ids, list) or any(
                not isinstance(value, str) or not value.strip() for value in included_segment_ids
            ):
                fail("object_selection.included_segment_ids 必须是文本数组")
            selection_source_ids = raw_selection.get("source_ids")
            if not isinstance(selection_source_ids, list) or not selection_source_ids:
                fail("object_selection.source_ids 必须是非空数组")
            object_selection = {
                "audit_id": text(raw_selection.get("audit_id"), "object_selection.audit_id"),
                "status": selection_status,
                "candidate_count": selection_candidate_count,
                "included_count": selection_included_count,
                "excluded_count": selection_excluded_count,
                "unresolved_count": selection_unresolved_count,
                "included_segment_ids": sorted(set(included_segment_ids)),
                "source_ids": selection_source_ids,
                "anomaly_groups": raw_selection.get("anomaly_groups", []),
            }
        else:
            object_selection = {
                "audit_id": None,
                "status": "NOT_PROVIDED",
                "candidate_count": None,
                "included_count": None,
                "excluded_count": None,
                "unresolved_count": None,
                "included_segment_ids": [],
                "source_ids": [],
                "anomaly_groups": [],
            }

        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            fail("segments 必须是非空数组")
        segments: dict[str, dict[str, Any]] = {}
        endpoints: list[dict[str, Any]] = []
        coordinate_dimension: int | None = None
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict):
                fail(f"segments[{index}] 必须是对象")
            segment_id = text(raw.get("segment_id"), f"segments[{index}].segment_id")
            if segment_id in segments:
                fail(f"segment_id 重复: {segment_id}")
            system = text(raw.get("system"), f"{segment_id}.system")
            start = point(raw.get("start"), f"{segment_id}.start")
            end = point(raw.get("end"), f"{segment_id}.end")
            if coordinate_dimension is None:
                coordinate_dimension = len(start)
            if len(start) != coordinate_dimension or len(end) != coordinate_dimension:
                fail("同一输入中的坐标维度必须一致")
            geometric_length = distance(start, end)
            length = finite(raw.get("length", geometric_length), f"{segment_id}.length")
            if geometric_length <= 0 or length <= 0:
                fail(f"{segment_id} 长度必须大于 0")
            source_ids = raw.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids or any(
                not isinstance(item, str) or not item.strip() for item in source_ids
            ):
                fail(f"{segment_id}.source_ids 必须是非空文本数组")
            raw_seeds = raw.get("dn_seeds", [])
            if not isinstance(raw_seeds, list):
                fail(f"{segment_id}.dn_seeds 必须是数组")
            seeds = []
            for seed_index, raw_seed in enumerate(raw_seeds):
                if not isinstance(raw_seed, dict):
                    fail(f"{segment_id}.dn_seeds[{seed_index}] 必须是对象")
                seeds.append(
                    {
                        "dn": text(raw_seed.get("dn"), f"{segment_id}.dn_seeds[{seed_index}].dn"),
                        "evidence_level": seed_level(
                            raw_seed.get("evidence_level"),
                            f"{segment_id}.dn_seeds[{seed_index}].evidence_level",
                        ),
                        "source_id": text(
                            raw_seed.get("source_id"), f"{segment_id}.dn_seeds[{seed_index}].source_id"
                        ),
                        "assumption_id": raw_seed.get("assumption_id"),
                        "method": text(raw_seed.get("method", "direct"), "dn seed method"),
                    }
                )
            segments[segment_id] = {
                "segment_id": segment_id,
                "system": system,
                "start": start,
                "end": end,
                "length": length,
                "source_ids": source_ids,
                "seeds": seeds,
                **{k: raw.get(k) for k in ("geometry", "parent_segment_id", "space_id", "start_token", "end_token", "tangent_start", "tangent_end")},
            }
            endpoints.extend(
                [
                    {"segment_id": segment_id, "end_name": "start", "point": start, "system": system},
                    {"segment_id": segment_id, "end_name": "end", "point": end, "system": system},
                ]
            )

        if object_selection["status"] != "NOT_PROVIDED":
            if object_selection["included_count"] != len(segments):
                fail("object_selection.included_count 与 segments 数量不一致")
            if set(object_selection["included_segment_ids"]) != set(segments):
                fail("object_selection.included_segment_ids 与 segments 不一致")

        raw_dn_match_candidates = data.get("dn_match_candidates", [])
        if not isinstance(raw_dn_match_candidates, list):
            fail("dn_match_candidates 必须是数组")
        dn_match_candidates: list[dict[str, Any]] = []
        dn_match_candidate_ids: set[str] = set()
        for index, raw in enumerate(raw_dn_match_candidates):
            if not isinstance(raw, dict):
                fail(f"dn_match_candidates[{index}] 必须是对象")
            candidate_id = text(raw.get("candidate_id"), f"dn_match_candidates[{index}].candidate_id")
            if candidate_id in dn_match_candidate_ids:
                fail(f"DN 匹配候选编号重复: {candidate_id}")
            dn_match_candidate_ids.add(candidate_id)
            segment_id = text(raw.get("segment_id"), f"{candidate_id}.segment_id")
            if segment_id not in segments:
                fail(f"{candidate_id} 引用了不存在的 segment_id")
            disposition = text(raw.get("disposition"), f"{candidate_id}.disposition")
            if disposition not in ("seed", "scenario", "excluded"):
                fail(f"{candidate_id}.disposition 必须是 seed、scenario 或 excluded")
            threshold_sensitive = raw.get("threshold_sensitive", False)
            if not isinstance(threshold_sensitive, bool):
                fail(f"{candidate_id}.threshold_sensitive 必须是布尔值")
            reason = raw.get("reason")
            if disposition == "excluded" and (not isinstance(reason, str) or not reason.strip()):
                fail(f"{candidate_id} 被排除但缺少 reason")
            diagnostics = raw.get("diagnostics", {})
            if not isinstance(diagnostics, dict):
                fail(f"{candidate_id}.diagnostics 必须是对象")
            record = {
                "candidate_id": candidate_id,
                "segment_id": segment_id,
                "dn": text(raw.get("dn"), f"{candidate_id}.dn"),
                "source_id": text(raw.get("source_id"), f"{candidate_id}.source_id"),
                "disposition": disposition,
                "threshold_sensitive": threshold_sensitive,
                "reason": reason,
                "diagnostics": diagnostics,
                "assumption_id": raw.get("assumption_id"),
            }
            if disposition != "excluded":
                level = seed_level(raw.get("evidence_level"), f"{candidate_id}.evidence_level")
                if disposition == "scenario":
                    level = "scenario"
                record["evidence_level"] = level
                segments[segment_id]["seeds"].append(
                    {
                        "dn": record["dn"],
                        "evidence_level": level,
                        "source_id": record["source_id"],
                        "assumption_id": record["assumption_id"],
                        "method": text(raw.get("method", "calibrated_text_binding"), f"{candidate_id}.method"),
                    }
                )
            dn_match_candidates.append(record)

        raw_dn_policy = data.get("dn_match_policy")
        if raw_dn_match_candidates and not isinstance(raw_dn_policy, dict):
            fail("存在 DN 匹配候选时必须提供 dn_match_policy")
        if isinstance(raw_dn_policy, dict):
            detected_count = nonnegative_integer(
                raw_dn_policy.get("detected_candidate_count"),
                "dn_match_policy.detected_candidate_count",
            )
            if detected_count != len(dn_match_candidates):
                fail("dn_match_policy.detected_candidate_count 与候选明细数不一致")
            policy_sources = raw_dn_policy.get("source_ids")
            if not isinstance(policy_sources, list) or not policy_sources:
                fail("dn_match_policy.source_ids 必须是非空数组")
            thresholds = raw_dn_policy.get("thresholds")
            if not isinstance(thresholds, dict) or not thresholds:
                fail("dn_match_policy.thresholds 必须是非空对象")
            sensitivity_scenarios = raw_dn_policy.get("sensitivity_scenarios", [])
            if not isinstance(sensitivity_scenarios, list):
                fail("dn_match_policy.sensitivity_scenarios 必须是数组")
            if any(item["threshold_sensitive"] for item in dn_match_candidates) and len(
                sensitivity_scenarios
            ) < 2:
                fail("存在阈值敏感 DN 候选时至少需要两个敏感性方案")
            dn_match_policy = {
                "policy_id": text(raw_dn_policy.get("policy_id"), "dn_match_policy.policy_id"),
                "detected_candidate_count": detected_count,
                "source_ids": policy_sources,
                "thresholds": thresholds,
                "sensitivity_scenarios": sensitivity_scenarios,
            }
        else:
            dn_match_policy = None

        node_points, endpoint_node, node_partitions, snap_stats = make_endpoint_nodes(endpoints, segments, snap)

        segment_nodes: dict[str, tuple[str, str]] = {}
        node_segments: dict[str, set[str]] = defaultdict(set)
        endpoint_lookup: dict[tuple[str, str], int] = {}
        for index, endpoint in enumerate(endpoints):
            endpoint_lookup[(endpoint["segment_id"], endpoint["end_name"])] = index
        for segment_id in segments:
            start_node = endpoint_node[endpoint_lookup[(segment_id, "start")]]
            end_node = endpoint_node[endpoint_lookup[(segment_id, "end")]]
            segment_nodes[segment_id] = (start_node, end_node)
            node_segments[start_node].add(segment_id)
            node_segments[end_node].add(segment_id)

        exclusions: set[frozenset[tuple[str, str]]] = set()
        for raw in data.get("gap_exclusions", []):
            if not isinstance(raw, dict):
                fail("gap_exclusions 项必须是对象")
            exclusions.add(
                frozenset(
                    {
                        (text(raw.get("a_segment"), "a_segment"), text(raw.get("a_end"), "a_end")),
                        (text(raw.get("b_segment"), "b_segment"), text(raw.get("b_end"), "b_end")),
                    }
                )
            )

        gap_classifications: dict[frozenset[tuple[str, str]], dict[str, Any]] = {}
        for index, raw in enumerate(data.get("gap_classifications", [])):
            if not isinstance(raw, dict):
                fail(f"gap_classifications[{index}] 必须是对象")
            pair = frozenset(
                {
                    (text(raw.get("a_segment"), "a_segment"), text(raw.get("a_end"), "a_end")),
                    (text(raw.get("b_segment"), "b_segment"), text(raw.get("b_end"), "b_end")),
                }
            )
            if pair in gap_classifications:
                fail(f"gap_classifications[{index}] 端点对重复")
            scenario_include = raw.get("scenario_include", False)
            if not isinstance(scenario_include, bool):
                fail(f"gap_classifications[{index}].scenario_include 必须是布尔值")
            action = raw.get("quantity_action", "scenario")
            if action not in {"scenario", "exclude", "restore_omission", "include_occupied_axis"}:
                fail("quantity_action无效")
            if action in {"restore_omission", "include_occupied_axis"}:
                text(raw.get("source_id"), "omission source_id")
                seed_level(raw.get("quantity_evidence_level"), "omission evidence_level")
            if action == "include_occupied_axis":
                text(raw.get("quantity_basis"), "include_occupied_axis.quantity_basis")
            gap_classifications[pair] = {
                "classification": text(raw.get("classification", "未分类断口候选"), "classification"),
                "quantity_decision": text(raw.get("quantity_decision", "待确认"), "quantity_decision"),
                "scenario_include": scenario_include,
                "source_id": raw.get("source_id"),
                "quantity_action": action,
                "quantity_evidence_level": raw.get("quantity_evidence_level"),
                "quantity_basis": raw.get("quantity_basis"),
            }

        open_endpoints = [
            index
            for index, endpoint in enumerate(endpoints)
            if len(node_segments[endpoint_node[index]]) == 1
        ]
        gap_options, rejected_gaps = pipe_gap_candidates(
            endpoints, open_endpoints, segments, parameters, unit, snap, gap_max,
            direction_tolerance, exclusions, gap_pairs, data.get("valves", []), node_segments, node_points,
            data.get("change_nodes", []) + data.get("scope_boundary_nodes", []),
            {pair for pair,row in gap_classifications.items() if row["quantity_action"]=="restore_omission"})
        candidates: dict[int, list[tuple[float, int]]] = defaultdict(list)
        for (left, right), option in gap_options.items():
            candidates[left].append((option["distance"], right))
            candidates[right].append((option["distance"], left))

        best: dict[int, tuple[float, int]] = {}
        ambiguous: set[int] = {endpoint_lookup[tuple(e)] for item in rejected_gaps
                               if item["reason"] == "multiple_target_endpoints" for e in item["endpoints"]}
        for endpoint_index, options in candidates.items():
            options.sort()
            best[endpoint_index] = options[0]
            if len(options) > 1:
                ambiguous.add(endpoint_index)

        bridges: list[dict[str, Any]] = []
        bridged_endpoints: set[int] = set()
        for left, (gap, right) in sorted(best.items()):
            if left > right or left in ambiguous or right in ambiguous:
                continue
            if right not in best or best[right][1] != left:
                continue
            a, b = endpoints[left], endpoints[right]
            bridge_id = f"BR-{len(bridges) + 1:04d}"
            pair = frozenset(
                {(a["segment_id"], a["end_name"]), (b["segment_id"], b["end_name"])}
            )
            gap_classification = gap_classifications.get(pair, {})
            supplied_classification = bool(gap_classification)
            option = gap_options[(left, right)]
            serial = option["kind"] == "serial_accessory"
            bridges.append(
                {
                    "bridge_id": bridge_id,
                    "system": a["system"],
                    "space_id": segments[a["segment_id"]]["space_id"],
                    "a": {"segment_id": a["segment_id"], "end": a["end_name"]},
                    "b": {"segment_id": b["segment_id"], "end": b["end_name"]},
                    "node_a": endpoint_node[left],
                    "node_b": endpoint_node[right],
                    "virtual_length": gap,
                    "quantity_included": False,
                    "quantity_action": gap_classification.get("quantity_action", "scenario"),
                    "quantity_evidence_level": gap_classification.get("quantity_evidence_level"),
                    "quantity_basis": gap_classification.get("quantity_basis"),
                    "quantity_decision": gap_classification.get(
                        "quantity_decision", "只补拓扑，不补管长" if serial else "共线断口方案暂估补长"
                    ),
                    "classification": gap_classification.get(
                        "classification", "串联在线附件占位候选" if serial else "共线断口候选"
                    ),
                    "classification_method": (
                        "project_classification" if supplied_classification else "automatic_serial_accessory_coverage_candidate" if serial else "automatic_geometry_candidate"
                    ),
                    "scenario_include": gap_classification.get("scenario_include", not serial),
                    "source_id": gap_classification.get("source_id", "AUTO-GAP-GEOMETRY"),
                    "evidence_level": option["coverage"]["evidence_level"] if serial else "scenario",
                    "assumption_id": f"A-GAP-{bridge_id[3:]}",
                    "candidate_kind": option["kind"],
                    "coverage_evidence": option["coverage"],
                    "connectivity_included": option["connectivity_included"],
                    "omission_within_gap_max": option["omission_within_gap_max"],
                    "topology_status": "CANDIDATE_CONNECTED" if option["connectivity_included"] else "DN_CONFLICT_UNRESOLVED",
                    "occupying_source_object_ids": option["coverage"]["source_object_ids"] if serial else [],
                    "occupying_valve_ids": [r["valve_id"] for r in option["coverage"]["instances"]] if serial else [],
                }
            )
            if option["connectivity_included"]:
                bridged_endpoints.update({left, right})

        graph: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for segment_id, (start_node, end_node) in segment_nodes.items():
            graph[start_node].append((end_node, "segment", segment_id))
            graph[end_node].append((start_node, "segment", segment_id))
        for bridge in bridges:
            if not bridge["connectivity_included"]:
                continue
            graph[bridge["node_a"]].append((bridge["node_b"], "bridge", bridge["bridge_id"]))
            graph[bridge["node_b"]].append((bridge["node_a"], "bridge", bridge["bridge_id"]))

        interface_edges = add_interfaces(route_preparation, endpoint_lookup, endpoint_node, graph)
        node_locator = NodeLocator(node_points, node_partitions, segment_nodes, segments, max(terminal_max, snap, 1e-9))
        def nearest_node(raw_point, system, maximum, context=None):
            return node_locator.bind({**(context or {}), "point": list(raw_point), "system": system}, maximum)

        change_nodes: set[str] = set()
        change_records = []
        boundary_records = []
        boundary_nodes: set[str] = set()
        complete_boundary_nodes = set()
        context_by_node = {}
        raw_boundaries = data.get("scope_boundary_nodes", [])
        if not isinstance(raw_boundaries, list):
            fail("scope_boundary_nodes 必须是数组")
        # Existing scope-interface roots already declare an incomplete network.
        raw_boundaries = list(raw_boundaries) + [
            {**r, "boundary_id": "ROOT-" + str(r.get("root_id", ""))}
            for r in data.get("roots", []) if r.get("root_role") == "scope_interface"
        ]
        boundary_ids = set()
        for index, raw in enumerate(raw_boundaries):
            if not isinstance(raw, dict):
                fail(f"scope_boundary_nodes[{index}] 必须是对象")
            bid = text(raw.get("boundary_id"), "boundary_id")
            if bid in boundary_ids:
                fail("boundary_id 重复: " + bid)
            boundary_ids.add(bid)
            system = text(raw.get("system"), "boundary system")
            source_id = text(raw.get("source_id"), "boundary source_id")
            level = seed_level(raw.get("evidence_level"), "boundary evidence_level")
            node = nearest_node(point(raw.get("point"), "boundary point"), system, snap, raw)
            boundary_records.append({"boundary_id": bid, "node_id": node, "system": system,
                                     "source_id": source_id, "evidence_level": level,
                                     "status": "INCOMPLETE" if node else "UNBOUND"})
            if node is None:
                uncertain_change_systems.add(system)
                route_preparation["issues"].append({"type": "范围边界未绑定，受影响系统停止规格传播", "object": raw})
            else:
                boundary_nodes.add(node)
                change_nodes.add(node)
                context = boundary_context({"coordinate_unit": unit, **raw}, node_points[node], segments, snap)
                if node in context_by_node and context_by_node[node] != context:
                    fail("同一边界节点存在不一致外接声明")
                context_by_node[node] = context
                boundary_records[-1].update(context)
                if context["connections_complete"]:
                    complete_boundary_nodes.add(node)
                    boundary_records[-1]["status"] = "CONTEXT_RESOLVED"
                else:
                    route_preparation["issues"].append({"type": "范围边界外接关系未完整，管件类型待核实",
                                                        "boundary_id": bid, "node_id": node, "source_id": source_id})
        for index, raw in enumerate(data.get("change_nodes", [])):
            if not isinstance(raw, dict):
                fail(f"change_nodes[{index}] 必须是对象")
            system = text(raw.get("system"), f"change_nodes[{index}].system")
            raw_point = point(raw.get("point"), f"change_nodes[{index}].point")
            node = nearest_node(raw_point, system, terminal_max, raw)
            if node is None:
                uncertain_change_systems.add(system)
                route_preparation["issues"].append({"type": "变径边界未绑定，受影响系统停止规格传播", "object": raw})
                continue
            change_nodes.add(node)
            change_records.append(
                {
                    "change_id": text(raw.get("change_id"), "change_id"),
                    "node_id": node,
                    "system": system,
                    "evidence_level": seed_level(raw.get("evidence_level"), "change evidence_level"),
                    "source_id": text(raw.get("source_id"), "change source_id"),
                }
            )

        roots_by_system: dict[str, list[str]] = defaultdict(list)
        root_records: list[dict[str, Any]] = []
        for index, raw in enumerate(data.get("roots", [])):
            if not isinstance(raw, dict):
                fail(f"roots[{index}] 必须是对象")
            system = text(raw.get("system"), f"roots[{index}].system")
            node = nearest_node(point(raw.get("point"), f"roots[{index}].point"), system, terminal_max, raw)
            if node is None:
                route_preparation["issues"].append({"type": "水源或立管根节点未绑定", "object": raw})
                continue
            roots_by_system[system].append(node)
            root_records.append(
                {
                    "root_id": text(raw.get("root_id"), f"roots[{index}].root_id"),
                    "system": system,
                    "node_id": node,
                    "source_id": text(raw.get("source_id"), f"roots[{index}].source_id"),
                    "evidence_level": seed_level(
                        raw.get("evidence_level", "scenario"), f"roots[{index}].evidence_level"
                    ),
                    "root_role": text(
                        raw.get("root_role", "network_root"), f"roots[{index}].root_role"
                    ),
                    "assumption_id": raw.get("assumption_id"),
                }
            )

        terminal_type_rule_map: dict[tuple[str, str], dict[str, Any]] = {}
        for index, raw in enumerate(data.get("terminal_type_rules", [])):
            if not isinstance(raw, dict):
                fail(f"terminal_type_rules[{index}] 必须是对象")
            rule = {
                "rule_id": text(raw.get("rule_id"), f"terminal_type_rules[{index}].rule_id"),
                "system": text(raw.get("system"), f"terminal_type_rules[{index}].system"),
                "terminal_type": text(
                    raw.get("terminal_type"), f"terminal_type_rules[{index}].terminal_type"
                ),
                "port_dn": text(raw.get("port_dn"), f"terminal_type_rules[{index}].port_dn"),
                "evidence_level": seed_level(
                    raw.get("evidence_level", "scenario"),
                    f"terminal_type_rules[{index}].evidence_level",
                ),
                "source_id": text(raw.get("source_id"), f"terminal_type_rules[{index}].source_id"),
                "assumption_id": text(
                    raw.get("assumption_id"), f"terminal_type_rules[{index}].assumption_id"
                ),
            }
            key = (rule["system"], rule["terminal_type"])
            if key in terminal_type_rule_map:
                fail(f"terminal_type_rules[{index}] 的系统和末端类型重复")
            terminal_type_rule_map[key] = rule

        terminal_nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        terminal_records = []
        for index, raw in enumerate(data.get("terminals", [])):
            if not isinstance(raw, dict):
                fail(f"terminals[{index}] 必须是对象")
            terminal_id = text(raw.get("terminal_id"), f"terminals[{index}].terminal_id")
            system = text(raw.get("system"), f"{terminal_id}.system")
            terminal_point = point(raw.get("point"), f"{terminal_id}.point")
            node = nearest_node(terminal_point, system, terminal_max, raw)
            if node is None:
                route_preparation["issues"].append({"type": "末端端口未绑定", "object": raw})
                continue
            level = seed_level(raw.get("evidence_level"), f"{terminal_id}.evidence_level")
            terminal_type = text(raw.get("terminal_type"), f"{terminal_id}.terminal_type")
            port_dn = raw.get("port_dn")
            port_level = raw.get("port_evidence_level", level)
            port_source_id = raw.get("port_source_id", raw.get("source_id"))
            port_assumption_id = raw.get("assumption_id")
            port_method = "direct_terminal_port" if port_dn is not None else "unresolved"
            type_rule = terminal_type_rule_map.get((system, terminal_type))
            if port_dn is None and type_rule is not None:
                port_dn = type_rule["port_dn"]
                port_level = type_rule["evidence_level"]
                port_source_id = type_rule["source_id"]
                port_assumption_id = type_rule["assumption_id"]
                port_method = "terminal_type_rule"
            record = {
                "terminal_id": terminal_id,
                "system": system,
                "node_id": node,
                "terminal_type": terminal_type,
                "port_dn": port_dn,
                "evidence_level": level,
                "source_id": text(raw.get("source_id"), f"{terminal_id}.source_id"),
                "assumption_id": port_assumption_id,
                "port_evidence_level": None,
                "port_source_id": port_source_id,
                "port_method": port_method,
                "propagation_scope": raw.get("propagation_scope", "terminal_to_first_junction"),
            }
            if record["port_dn"] is not None:
                record["port_dn"] = text(record["port_dn"], f"{terminal_id}.port_dn")
                record["port_evidence_level"] = seed_level(
                    port_level, f"{terminal_id}.port_evidence_level"
                )
                record["port_source_id"] = text(
                    record["port_source_id"], f"{terminal_id}.port_source_id"
                )
            terminal_nodes[node].append(record)
            terminal_records.append(record)

        raw_endpoint_handoff = data.get("endpoint_handoff")
        if isinstance(raw_endpoint_handoff, dict):
            unbound = len(data.get("terminals", [])) - len(terminal_records)
            route_preparation["endpoint_receipt"] = {"provided": len(data.get("terminals", [])), "bound": len(terminal_records), "unbound": unbound}
            raw_endpoint_handoff = dict(raw_endpoint_handoff)
            raw_endpoint_handoff["delivered_count"] -= unbound
            raw_endpoint_handoff["unresolved_count"] += unbound
        if input_version == "qto-pipe-network-inference-input/1.2" and not isinstance(
            raw_endpoint_handoff, dict
        ):
            fail("1.2 输入必须提供 endpoint_handoff 交接对账")
        if isinstance(raw_endpoint_handoff, dict):
            handoff_required = raw_endpoint_handoff.get("required")
            if not isinstance(handoff_required, bool):
                fail("endpoint_handoff.required 必须是布尔值")
            handoff_source_count = nonnegative_integer(
                raw_endpoint_handoff.get("source_instance_count"),
                "endpoint_handoff.source_instance_count",
            )
            handoff_port_count = nonnegative_integer(
                raw_endpoint_handoff.get("source_endpoint_port_count", handoff_source_count),
                "endpoint_handoff.source_endpoint_port_count",
            )
            handoff_physical_count = nonnegative_integer(
                raw_endpoint_handoff.get("source_physical_instance_count", handoff_source_count),
                "endpoint_handoff.source_physical_instance_count",
            )
            if handoff_port_count != handoff_source_count:
                fail("endpoint_handoff.source_instance_count 必须与端口处置总数一致")
            handoff_delivered_count = nonnegative_integer(
                raw_endpoint_handoff.get("delivered_count"), "endpoint_handoff.delivered_count"
            )
            handoff_not_applicable_count = nonnegative_integer(
                raw_endpoint_handoff.get("not_applicable_count"),
                "endpoint_handoff.not_applicable_count",
            )
            handoff_unresolved_count = nonnegative_integer(
                raw_endpoint_handoff.get("unresolved_count"), "endpoint_handoff.unresolved_count"
            )
            if handoff_source_count != (
                handoff_delivered_count
                + handoff_not_applicable_count
                + handoff_unresolved_count
            ):
                fail("endpoint_handoff 来源端口数与已交付、不适用、未决数不守恒")
            if handoff_delivered_count != len(terminal_records):
                fail("endpoint_handoff.delivered_count 与 terminals 明细数不一致")
            handoff_reason = raw_endpoint_handoff.get("reason")
            source_complete = raw_endpoint_handoff.get("source_workpackage_complete", True)
            if not isinstance(source_complete, bool):
                fail("endpoint_handoff.source_workpackage_complete 必须是布尔值")
            validate_working_basis(raw_endpoint_handoff.get("recognition_basis"))
            if data.get("recognition_basis") is None and raw_endpoint_handoff.get("recognition_basis"):
                data["recognition_basis"] = raw_endpoint_handoff["recognition_basis"]
            if not handoff_required and (not isinstance(handoff_reason, str) or not handoff_reason.strip()):
                fail("endpoint_handoff.required=false 时必须说明 reason")
            endpoint_handoff = {
                "handoff_id": text(
                    raw_endpoint_handoff.get("handoff_id"), "endpoint_handoff.handoff_id"
                ),
                "required": handoff_required,
                "source_instance_count": handoff_source_count,
                "source_physical_instance_count": handoff_physical_count,
                "source_endpoint_port_count": handoff_port_count,
                "delivered_count": handoff_delivered_count,
                "not_applicable_count": handoff_not_applicable_count,
                "unresolved_count": handoff_unresolved_count,
                "complete": handoff_unresolved_count == 0 and source_complete,
                "source_workpackage_complete": source_complete,
                "recognition_basis": raw_endpoint_handoff.get("recognition_basis"),
                "source_id": text(
                    raw_endpoint_handoff.get("source_id"), "endpoint_handoff.source_id"
                ),
                "reason": handoff_reason,
            }
        else:
            endpoint_handoff = {
                "handoff_id": None,
                "required": False,
                "source_instance_count": 0,
                "source_physical_instance_count": 0,
                "source_endpoint_port_count": 0,
                "delivered_count": len(terminal_records),
                "not_applicable_count": 0,
                "unresolved_count": 0,
                "complete": False,
                "source_id": None,
                "reason": "旧版输入未提供跨工作包端点对账",
            }

        adjacency: dict[str, set[str]] = defaultdict(set)
        for node, incident in node_segments.items():
            if node in change_nodes or any(segments[sid]["system"] in uncertain_change_systems for sid in incident):
                continue
            if len(incident) == 2:
                left, right = sorted(incident)
                adjacency[left].add(right)
                adjacency[right].add(left)
        independent_adjacency = {sid: set(adjacency[sid]) for sid in segments}
        bridge_links = defaultdict(list)
        for bridge in bridges:
            if not bridge["connectivity_included"]:
                continue
            if bridge["node_a"] in change_nodes or bridge["node_b"] in change_nodes or bridge["system"] in uncertain_change_systems:
                continue
            left_segments = node_segments[bridge["node_a"]]
            right_segments = node_segments[bridge["node_b"]]
            if len(left_segments) == 1 and len(right_segments) == 1:
                left, right = next(iter(left_segments)), next(iter(right_segments))
                adjacency[left].add(right)
                adjacency[right].add(left)
                bridge_links[frozenset((left,right))].append(bridge)

        for link in interface_edges:
            if link.get("dn_continuity") is not True or link["system"] in uncertain_change_systems:
                continue
            if link["node_a"] in change_nodes or link["node_b"] in change_nodes:
                continue
            left_set, right_set = node_segments[link["node_a"]], node_segments[link["node_b"]]
            if len(left_set) == len(right_set) == 1:
                left, right = next(iter(left_set)), next(iter(right_set))
                adjacency[left].add(right); adjacency[right].add(left)
                independent_adjacency[left].add(right); independent_adjacency[right].add(left)

        terminal_seeds: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node, records in terminal_nodes.items():
            if len(node_segments[node]) != 1:
                continue
            segment_id = next(iter(node_segments[node]))
            for record in records:
                if record["port_dn"] is None or record["propagation_scope"] == "interface_only":
                    continue
                propagated_level = (
                    "scenario" if record["port_evidence_level"] == "scenario" else "inferred"
                )
                terminal_seeds[segment_id].append(
                    {
                        "dn": record["port_dn"],
                        "evidence_level": propagated_level,
                        "source_id": record["port_source_id"],
                        "assumption_id": record["assumption_id"],
                        "method": record["port_method"],
                    }
                )

        assignments: dict[str, dict[str, Any]] = {}
        runs: list[dict[str, Any]] = []
        seen_segments: set[str] = set()
        decision_queue: list[dict[str, Any]] = [
            {"type": "串联附件连接待核" if item.get("candidate_classification") else "断口候选被安全约束阻断",
             **item} for item in rejected_gaps]
        for segment_id in sorted(segments):
            if segment_id in seen_segments:
                continue
            component = []
            queue = [segment_id]
            seen_segments.add(segment_id)
            while queue:
                current = queue.pop()
                component.append(current)
                for neighbor in adjacency[current]:
                    if neighbor not in seen_segments:
                        seen_segments.add(neighbor)
                        queue.append(neighbor)
            seeds = []
            for member in component:
                seeds.extend((member, seed) for seed in segments[member]["seeds"])
                seeds.extend((member, seed) for seed in terminal_seeds[member])
            unique_dns = sorted({seed["dn"] for _, seed in seeds})
            run_id = f"RUN-{len(runs) + 1:04d}"
            if len(unique_dns) == 1:
                dn = unique_dns[0]
                seed_levels = [seed["evidence_level"] for _, seed in seeds]
                best_level = stronger_level(seed_levels)
                for member in component:
                    own = [seed for seed in segments[member]["seeds"] if seed["dn"] == dn]
                    if own:
                        member_level = stronger_level([seed["evidence_level"] for seed in own])
                        method = "direct_or_bound_seed"
                    else:
                        member_level = "scenario" if best_level == "scenario" else "inferred"
                        method = "constrained_through_run_propagation"
                    assignments[member] = {
                        "dn": dn,
                        "evidence_level": member_level,
                        "quantity_level": LEVEL_ZH[member_level],
                        "method": method,
                        "run_id": run_id,
                        "source_ids": sorted({seed["source_id"] for _, seed in seeds}),
                        "assumption_ids": sorted(
                            {
                                seed["assumption_id"]
                                for _, seed in seeds
                                if isinstance(seed.get("assumption_id"), str)
                            }
                        ),
                        "alternatives": [],
                    }
                run_status = "assigned"
            elif len(unique_dns) > 1:
                for member in component:
                    own = segments[member]["seeds"] + terminal_seeds[member]
                    own_dns = sorted({seed["dn"] for seed in own})
                    if len(own_dns) == 1:
                        own_level = stronger_level([seed["evidence_level"] for seed in own])
                        assignments[member] = {
                            "dn": own_dns[0],
                            "evidence_level": own_level,
                            "quantity_level": LEVEL_ZH[own_level],
                            "method": "local_seed_with_run_conflict",
                            "run_id": run_id,
                            "source_ids": sorted({seed["source_id"] for seed in own}),
                            "assumption_ids": sorted(
                                {
                                    seed["assumption_id"]
                                    for seed in own
                                    if isinstance(seed.get("assumption_id"), str)
                                }
                            ),
                            "alternatives": unique_dns,
                        }
                    else:
                        assignments[member] = {
                            "dn": None,
                            "evidence_level": "scenario",
                            "quantity_level": "方案暂估量",
                            "method": "multi_dn_run_requires_change_boundary",
                            "run_id": run_id,
                            "source_ids": sorted({seed["source_id"] for _, seed in seeds}),
                            "assumption_ids": [f"A-DN-{run_id[4:]}"],
                            "alternatives": unique_dns,
                        }
                decision_queue.append(
                    {
                        "decision_id": f"D-DN-{run_id[4:]}",
                        "type": "变径边界或标签冲突",
                        "run_id": run_id,
                        "segments": sorted(component),
                        "alternatives": unique_dns,
                        "recommendation": "核对显式变径节点、引线和设备接口后分区",
                        "formal_release_blocked": True,
                    }
                )
                run_status = "conflict"
            else:
                for member in component:
                    assignments[member] = {
                        "dn": None,
                        "evidence_level": None,
                        "quantity_level": "未决量",
                        "method": "no_dn_seed",
                        "run_id": run_id,
                        "source_ids": [],
                        "assumption_ids": [],
                        "alternatives": [],
                    }
                decision_queue.append(
                    {
                        "decision_id": f"D-DN-{run_id[4:]}",
                        "type": "无 DN 种子",
                        "run_id": run_id,
                        "segments": sorted(component),
                        "alternatives": [],
                        "recommendation": "补充直接标注、设备端口或当前项目设计复原规则",
                        "formal_release_blocked": True,
                    }
                )
                run_status = "unresolved"
            runs.append(
                {
                    "run_id": run_id,
                    "segments": sorted(component),
                    "seed_dns": unique_dns,
                    "status": run_status,
                }
            )

        node_depth: dict[str, int] = {}
        for system, roots in roots_by_system.items():
            queue: deque[str] = deque()
            for root in roots:
                node_depth[root] = 0
                queue.append(root)
            while queue:
                current = queue.popleft()
                for neighbor, edge_type, edge_id in graph[current]:
                    if edge_type == "segment" and segments[edge_id]["system"] != system:
                        continue
                    if neighbor not in node_depth or node_depth[neighbor] > node_depth[current] + 1:
                        node_depth[neighbor] = node_depth[current] + 1
                        queue.append(neighbor)

        terminal_count = {node: len(records) for node, records in terminal_nodes.items()}
        downstream_count = dict(terminal_count)
        for node in sorted(node_depth, key=node_depth.get, reverse=True):
            parents = [
                neighbor
                for neighbor, _, _ in graph[node]
                if neighbor in node_depth and node_depth[neighbor] < node_depth[node]
            ]
            if len(parents) == 1:
                downstream_count[parents[0]] = downstream_count.get(parents[0], 0) + downstream_count.get(node, 0)

        roles: dict[str, dict[str, Any]] = {}
        for segment_id, (left, right) in segment_nodes.items():
            dl, dr = node_depth.get(left), node_depth.get(right)
            if dl is None or dr is None:
                role = "未定"
                downstream = 0
            elif dl == dr:
                role = "环网"
                downstream = max(downstream_count.get(left, 0), downstream_count.get(right, 0))
            else:
                downstream_node = right if dl < dr else left
                downstream = downstream_count.get(downstream_node, 0)
                if downstream >= main_min:
                    role = "主干"
                elif downstream >= 2:
                    role = "支干"
                elif downstream == 1 and terminal_count.get(downstream_node, 0):
                    role = "末端连接"
                elif downstream == 1:
                    role = "支管"
                else:
                    role = "未定"
            roles[segment_id] = {"role": role, "downstream_terminal_count": downstream}

        ambiguous_nodes = ambiguous_role_nodes(graph, roots_by_system)
        for sid, (left, right) in segment_nodes.items():
            if left in ambiguous_nodes or right in ambiguous_nodes:
                roles[sid] = {"role": "环网", "downstream_terminal_count": 0, "role_basis": "环路或多源不套用树状下游数量"}
        if ambiguous_nodes:
            route_preparation["issues"].append({"type": "环网或多水源需按系统图复核主支管", "node_count": len(ambiguous_nodes)})
        role_seen = set()
        for first in graph:
            if first in role_seen:
                continue
            todo, members, used_gaps = [first], set(), set()
            while todo:
                current = todo.pop()
                if current in members:
                    continue
                members.add(current)
                for other, kind, edge_id in graph[current]:
                    if kind == "bridge":
                        used_gaps.add(next(b["assumption_id"] for b in bridges if b["bridge_id"] == edge_id))
                    if other not in members:
                        todo.append(other)
            role_seen.update(members)
            if used_gaps:
                for sid,(a,b) in segment_nodes.items():
                    if a in members:
                        roles[sid].update(role_evidence_level="scenario", role_assumption_ids=sorted(used_gaps))

        role_dn_rules = []
        for index, raw in enumerate(data.get("role_dn_rules", [])):
            if not isinstance(raw, dict):
                fail(f"role_dn_rules[{index}] 必须是对象")
            raw_roles = raw.get("roles")
            if isinstance(raw_roles, str):
                raw_roles = [raw_roles]
            if not isinstance(raw_roles, list) or not raw_roles:
                fail(f"role_dn_rules[{index}].roles 必须是非空数组")
            min_terminals = int(raw.get("min_downstream_terminals", 0))
            max_terminals = raw.get("max_downstream_terminals")
            if max_terminals is not None:
                max_terminals = int(max_terminals)
            role_dn_rules.append(
                {
                    "rule_id": text(raw.get("rule_id"), f"role_dn_rules[{index}].rule_id"),
                    "system": text(raw.get("system"), f"role_dn_rules[{index}].system"),
                    "roles": [text(value, f"role_dn_rules[{index}].roles") for value in raw_roles],
                    "min_downstream_terminals": min_terminals,
                    "max_downstream_terminals": max_terminals,
                    "dn": text(raw.get("dn"), f"role_dn_rules[{index}].dn"),
                    "evidence_level": seed_level(
                        raw.get("evidence_level", "scenario"),
                        f"role_dn_rules[{index}].evidence_level",
                    ),
                    "source_id": text(raw.get("source_id"), f"role_dn_rules[{index}].source_id"),
                    "assumption_id": text(
                        raw.get("assumption_id"), f"role_dn_rules[{index}].assumption_id"
                    ),
                }
            )

        role_rule_assignment_count = 0
        for segment_id, assignment in assignments.items():
            if assignment["dn"] is not None or assignment["alternatives"]:
                continue
            segment = segments[segment_id]
            role_record = roles[segment_id]
            matched = [
                rule
                for rule in role_dn_rules
                if rule["system"] == segment["system"]
                and role_record["role"] in rule["roles"]
                and role_record["downstream_terminal_count"] >= rule["min_downstream_terminals"]
                and (
                    rule["max_downstream_terminals"] is None
                    or role_record["downstream_terminal_count"] <= rule["max_downstream_terminals"]
                )
            ]
            candidate_dns = sorted({rule["dn"] for rule in matched})
            if len(candidate_dns) == 1:
                level = weaker_level([rule["evidence_level"] for rule in matched])
                assignment.update(
                    {
                        "dn": candidate_dns[0],
                        "evidence_level": level,
                        "quantity_level": LEVEL_ZH[level],
                        "method": "project_role_dn_rule",
                        "source_ids": sorted({rule["source_id"] for rule in matched}),
                        "assumption_ids": sorted({rule["assumption_id"] for rule in matched}),
                        "alternatives": [],
                    }
                )
                role_rule_assignment_count += 1
            elif len(candidate_dns) > 1:
                assignment.update(
                    {
                        "evidence_level": "scenario",
                        "quantity_level": "方案暂估量",
                        "method": "multiple_project_role_dn_rules",
                        "source_ids": sorted({rule["source_id"] for rule in matched}),
                        "assumption_ids": sorted({rule["assumption_id"] for rule in matched}),
                        "alternatives": candidate_dns,
                    }
                )

        from pipe_gap_dependencies import isolate_gap_dependencies, summarize_assignments
        independent_assignments = isolate_gap_dependencies(
            assignments, segments, terminal_seeds, adjacency, independent_adjacency, bridge_links)
        for sid, assignment in assignments.items():
            if assignment["method"] == "project_role_dn_rule" and roles[sid].get("role_assumption_ids"):
                deps=roles[sid]["role_assumption_ids"]
                assignment.update(evidence_level="scenario",quantity_level="方案暂估量",requires_topology_assumption=True,
                    gap_assumption_ids=deps,assumption_ids=sorted(set(assignment["assumption_ids"])|set(deps)))
                independent_assignments[sid].update(dn=None,evidence_level=None,quantity_level="未决量",method="no_independent_dn_path",
                    source_ids=[],assumption_ids=[],alternatives=[])

        for run in runs:
            assigned = [assignments[segment_id]["dn"] for segment_id in run["segments"]]
            if run["status"] == "unresolved" and any(assigned):
                run["status"] = "scenario_assigned" if all(assigned) else "partially_scenario_assigned"
        for decision in decision_queue:
            if decision["type"] != "无 DN 种子":
                continue
            assigned = [assignments[segment_id]["dn"] for segment_id in decision["segments"]]
            if any(assigned):
                decision["type"] = "虚拟桥条件DN方案" if any(assignments[sid].get("requires_topology_assumption") for sid in decision["segments"]) else "按项目主支管规则形成方案暂估"
                decision["alternatives"] = sorted({value for value in assigned if value})
                decision["recommendation"] = "保留方案暂估并由设计或用户确认项目规则"

        dn_scenarios = []
        for run in runs:
            unresolved_members = [
                member for member in run["segments"] if assignments[member]["dn"] is None
            ]
            alternatives = sorted(
                {
                    dn
                    for member in unresolved_members
                    for dn in assignments[member].get("alternatives", [])
                }
                | set(run.get("seed_dns", []))
            )
            if not unresolved_members or len(alternatives) < 2:
                continue
            unresolved_length = sum(segments[member]["length"] for member in unresolved_members)
            for option_index, dn in enumerate(alternatives, start=1):
                dn_scenarios.append(
                    {
                        "scenario_id": f"SC-DN-{run['run_id'][4:]}-{option_index}",
                        "run_id": run["run_id"],
                        "candidate_dn": dn,
                        "segments": sorted(unresolved_members),
                        "scenario_length": unresolved_length,
                        "unit": unit,
                        "quantity_level": "方案暂估量",
                        "selected": False,
                        "assumption_id": f"A-DN-{run['run_id'][4:]}",
                        "meaning": "把本直通链未分区管段全部归入该候选DN的边界方案；不自动采用",
                    }
                )

        fitting_records = []
        for node_id, incident_set in sorted(node_segments.items()):
            incident = sorted(incident_set)
            degree = len(incident)
            is_boundary = node_id in boundary_nodes
            resolved_context = node_id in complete_boundary_nodes
            if degree < 2 and not is_boundary:
                continue
            systems = {segments[segment_id]["system"] for segment_id in incident}
            if len(systems) != 1:
                continue
            directions = []
            fitting_ports = []
            for segment_id in incident:
                left, right = segment_nodes[segment_id]
                target = segments[segment_id]["end"] if left == node_id else segments[segment_id]["start"]
                directions.append(tuple(segments[segment_id]["tangent_start"]) if left == node_id else tuple(-x for x in segments[segment_id]["tangent_end"]))
                fitting_ports.append({"port_id": segment_id + ("@start" if left == node_id else "@end"),
                                      "segment_id": segment_id, "dn": assignments[segment_id]["dn"],
                                      "direction": list(directions[-1]), "quantity_scope": "measured"})
            dns = sorted({assignments[segment_id]["dn"] for segment_id in incident if assignments[segment_id]["dn"]})
            measured_degree = degree
            context = context_by_node.get(node_id, {})
            if resolved_context:
                directions.extend(tuple(p["direction"]) for p in context["external_ports"])
                fitting_ports.extend({"port_id": p["port_id"], "segment_id": p["segment_id"],
                                      "dn": p.get("dn"), "direction": p["direction"], "quantity_scope": "context"}
                                     for p in context["external_ports"])
                if any(angle_deg(a,b) < 1e-5 for i,a in enumerate(directions) for b in directions[i+1:]):
                    fail("范围外接方向与已有端口重叠，不能重复作为独立管口")
                degree = len(directions)
                dns = sorted(set(dns) | {p["dn"] for p in context["external_ports"] if p.get("dn")})
            fitting_type = None
            candidate_types = []
            angle = None
            if is_boundary and not resolved_context:
                fitting_type = "边界管件未决"
            elif degree == 2:
                angle = angle_deg(directions[0], directions[1])
                straight = abs(180.0 - angle) <= fitting_angle_tolerance
                if len(dns) > 1 and straight:
                    fitting_type = "变径候选"
                elif len(dns) > 1 and not straight:
                    fitting_type = "弯头或变径组合候选"
                    candidate_types = ["弯头", "变径"]
                elif not straight:
                    fitting_type = "弯头候选"
            elif degree == 3:
                fitting_type = "三通候选"
            elif degree == 4:
                fitting_type = "四通或平面交叉候选"
            else:
                fitting_type = "多通节点候选"
            if fitting_type is None:
                continue
            port_angles = [{"a": fitting_ports[i]["port_id"], "b": fitting_ports[j]["port_id"],
                            "angle_deg": angle_deg(directions[i], directions[j])}
                           for i in range(degree) for j in range(i + 1, degree)]
            branch_angle = None
            if degree == 3 and any(abs(p['angle_deg'] - 180) <= fitting_angle_tolerance for p in port_angles):
                branch_angle = min(p['angle_deg'] for p in port_angles)
            dn_combination = "×".join(sorted((p['dn'] or 'DN待定' for p in fitting_ports), key=lambda d:(len(d), d)))
            incident_levels = [
                assignments[segment_id]["evidence_level"]
                for segment_id in incident
                if assignments[segment_id]["evidence_level"]
            ]
            level = "scenario" if "scenario" in incident_levels else "inferred"
            if resolved_context and any(p['evidence_level'] == 'scenario' for p in context['external_ports']):
                level = 'scenario'
            fitting_records.append(
                {
                    "fitting_id": f"FIT-{len(fitting_records) + 1:04d}",
                    "node_id": node_id,
                    "system": next(iter(systems)),
                    "type": fitting_type,
                    "candidate_types": candidate_types,
                    "degree": degree,
                    "angle_deg": angle,
                    "segment_ids": incident,
                    "dn_candidates": dns,
                    "ports": fitting_ports,
                    "dn_combination": dn_combination,
                    "port_angles": port_angles,
                    "branch_angle_deg": branch_angle,
                    "quantity_level": "未决量" if is_boundary and not resolved_context else LEVEL_ZH[level],
                    "formal_release_blocked": True,
                    "method": "scope_context_endpoint_topology" if resolved_context else ("incomplete_scope_boundary" if is_boundary else "exact_endpoint_topology"),
                    "connection_complete": resolved_context if is_boundary else None,
                    "measured_degree": measured_degree,
                    "external_port_ids": [p["port_id"] for p in context.get("external_ports", [])],
                    "quantity_owner": context.get("fitting_quantity_owner", "in_scope"),
                    "boundary_ids": [r["boundary_id"] for r in boundary_records if r["node_id"] == node_id],
                    "assumption_ids": sorted({a for sid in incident for a in assignments[sid].get("assumption_ids", [])}),
                }
            )
            if is_boundary and not resolved_context:
                decision_queue.append({"type": "范围边界管件待核实", "node_id": node_id,
                                       "fitting_id": fitting_records[-1]["fitting_id"],
                                       "segment_ids": incident, "quantity": None,
                                       "reason": "范围外连接未纳入当前网络，不用裁剪后度数认定管件，也不增加管长"})

        valve_route_index = RouteIndex(list(segments.values()))
        valve_records = []
        valve_ids: set[str] = set()
        for index, raw in enumerate(data.get("valves", [])):
            if not isinstance(raw, dict):
                fail(f"valves[{index}] 必须是对象")
            valve_id = text(raw.get("valve_id"), f"valves[{index}].valve_id")
            if valve_id in valve_ids:
                fail(f"valve_id 重复: {valve_id}")
            valve_ids.add(valve_id)
            system = text(raw.get("system"), f"{valve_id}.system")
            valve_point = point(raw.get("point"), f"{valve_id}.point")
            if len(valve_point) == 2:
                # 节点坐标已按网络统一补成三维；二维阀门点（无 z 的图元或外包框中心）
                # 按同一口径补 z=0，否则落在唯一拓扑桥上的阀门会让整网以维度不一致中断。
                valve_point = (*valve_point, 0.0)
            spaces = {r["space_id"] for r in segments.values() if r["system"] == system}
            valve_space = raw.get("space_id") or (next(iter(spaces)) if len(spaces) == 1 else None)
            ranked = valve_route_index.within(valve_point, system, valve_max, valve_space)
            nearby_segments = [segment_id for gap, segment_id in ranked if gap <= valve_max]
            connection_method = "proximity_candidates"
            attached_segments = []  # proximity alone is not an actual online connection
            explicit_segments = raw.get("connected_segment_ids")
            if explicit_segments is not None:
                if (not isinstance(explicit_segments, list) or not explicit_segments
                        or any(not isinstance(sid, str) or sid not in segments
                               or segments[sid]["system"] != system for sid in explicit_segments)
                        or len(set(explicit_segments)) != len(explicit_segments)):
                    fail(f"{valve_id}.connected_segment_ids 必须是同系统唯一有效管段数组")
                text(raw.get("connection_source_id"), f"{valve_id}.connection_source_id")
                attached_segments = explicit_segments
                connection_method = "evidence_bound_connection"
            else:
                # 搜索半径只找候选；实际在线接触优先于邻近侧管。
                contact_segments = [sid for gap, sid in ranked if gap <= snap and valve_space is not None]
                if contact_segments:
                    attached_segments = contact_segments
                    connection_method = "exact_segment_contact"
                else:
                    bridge_pairs = []
                    for bridge in bridges:
                        if not bridge["connectivity_included"]:
                            continue
                        if bridge["system"] != system or bridge.get("space_id") != valve_space:
                            continue
                        pa, pb = node_points[bridge["node_a"]], node_points[bridge["node_b"]]
                        if (point_segment_distance(valve_point, pa, pb) <= snap
                                and distance(valve_point, pa) > snap
                                and distance(valve_point, pb) > snap):
                            bridge_pairs.append(sorted([bridge["a"]["segment_id"], bridge["b"]["segment_id"]]))
                    distinct_pairs = {tuple(pair) for pair in bridge_pairs}
                    if len(distinct_pairs) == 1:
                        attached_segments = list(next(iter(distinct_pairs)))
                        connection_method = "unique_topology_bridge"
            attached = attached_segments[0] if attached_segments else None
            connection_gap_ids = sorted({bridge["assumption_id"] for bridge in bridges
                if connection_method == "unique_topology_bridge"
                and {bridge["a"]["segment_id"], bridge["b"]["segment_id"]} == set(attached_segments)})
            direct_dn = raw.get("direct_dn")
            if direct_dn is not None:
                dn = text(direct_dn, f"{valve_id}.direct_dn")
                dn_level = seed_level(raw.get("dn_evidence_level", "confirmed"), "valve dn level")
                dn_method = "direct_valve_dn"
            elif attached_segments:
                adjacent_dns = sorted(
                    {
                        assignments[segment_id]["dn"]
                        for segment_id in attached_segments
                        if assignments[segment_id]["dn"]
                    }
                )
                if len(adjacent_dns) == 1:
                    dn = adjacent_dns[0]
                    adjacent_levels = [
                        assignments[segment_id]["evidence_level"]
                        for segment_id in attached_segments
                        if assignments[segment_id]["dn"]
                        and assignments[segment_id]["evidence_level"]
                    ]
                    dn_level = (
                        "scenario"
                        if not adjacent_levels or "scenario" in adjacent_levels
                        else "inferred"
                    )
                    dn_method = "inherit_compatible_adjacent_segments"
                else:
                    dn = None
                    dn_level = None
                    dn_method = "adjacent_dn_conflict" if adjacent_dns else "unresolved"
            else:
                dn = None
                dn_level = None
                dn_method = "unresolved"
            dn_assumptions = sorted({a for sid in attached_segments for a in assignments[sid].get("assumption_ids", [])}
                                    | set(connection_gap_ids)) if direct_dn is None else []
            if direct_dn is None and dn is not None and connection_gap_ids:
                dn_level = "scenario"
            valve_type = raw.get("valve_type")
            candidate_types = raw.get("candidate_types", [])
            if not isinstance(candidate_types, list) or any(
                not isinstance(value, str) or not value.strip() for value in candidate_types
            ):
                fail(f"{valve_id}.candidate_types 必须是文本数组")
            valve_records.append(
                {
                    "valve_id": valve_id,
                    "system": system,
                    "attached_segment_id": attached,
                    "attached_segment_ids": attached_segments,
                    "nearby_segment_ids": nearby_segments,
                    "nearby_dn_candidates": sorted({assignments[sid]["dn"] for sid in nearby_segments if assignments[sid]["dn"]}),
                    "space_id": valve_space,
                    "connection_method": connection_method,
                    "connection_source_id": raw.get("connection_source_id"),
                    "instance_level": seed_level(raw.get("instance_level", "confirmed"), "valve instance level"),
                    "valve_type": valve_type if isinstance(valve_type, str) and valve_type.strip() else "未分类",
                    "candidate_types": sorted(set(candidate_types)),
                    "candidate_type_source_id": raw.get("candidate_type_source_id"),
                    "type_source_ids": raw.get("type_source_ids", []),
                    "candidate_type_assumption_ids": raw.get("candidate_type_assumption_ids", []),
                    "block_family": raw.get("block_family"),
                    "dn": dn,
                    "dn_evidence_level": dn_level,
                    "dn_quantity_level": LEVEL_ZH[dn_level] if dn_level else "未决量",
                    "dn_method": dn_method,
                    "dn_assumption_ids": dn_assumptions,
                    "connection_assumption_ids": connection_gap_ids,
                    "assumption_id": raw.get("assumption_id"),
                    "source_id": text(raw.get("source_id"), f"{valve_id}.source_id"),
                    "source_object_id": (text(raw["source_object_id"], f"{valve_id}.source_object_id")
                                         if raw.get("source_object_id") is not None else None),
                    "point": list(valve_point),
                }
            )

        for bridge in bridges:
            if bridge["classification_method"] != "automatic_geometry_candidate":
                continue
            bridge_start = node_points[bridge["node_a"]]
            bridge_end = node_points[bridge["node_b"]]
            occupying_valves = [
                item
                for item in valve_records
                if item["system"] == bridge["system"]
                and item.get("space_id") == bridge.get("space_id")
                and point_segment_distance(tuple(item["point"]), bridge_start, bridge_end) <= snap
            ]
            if occupying_valves:
                bridge["classification"] = "在线附件占位候选"
                bridge["classification_method"] = "automatic_valve_occupancy_candidate"
                bridge["quantity_decision"] = "只补拓扑，不补管长"
                bridge["scenario_include"] = False
                bridge["source_id"] = occupying_valves[0]["source_id"]
                bridge["occupying_valve_ids"] = [item["valve_id"] for item in occupying_valves]

        valve_summary: dict[tuple[Any, ...], int] = defaultdict(int)
        valve_family_summary: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in valve_records:
            key = (
                item["system"],
                item["valve_type"],
                item.get("block_family"),
                item["dn"] or "DN待定",
                item["dn_quantity_level"],
            )
            valve_summary[key] += 1
            family_key = (item["system"], item.get("block_family") or "无块族")
            summary = valve_family_summary.setdefault(
                family_key,
                {
                    "system": item["system"],
                    "block_family": item.get("block_family") or "无块族",
                    "instance_count": 0,
                    "dn_assigned_count": 0,
                    "candidate_types": set(),
                },
            )
            summary["instance_count"] += 1
            summary["dn_assigned_count"] += 1 if item["dn"] else 0
            summary["candidate_types"].update(item["candidate_types"])

        length_summary: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        )
        segment_records = []
        for segment_id, segment in sorted(segments.items()):
            assignment = assignments[segment_id]
            dn_key = assignment["dn"] or "DN待定"
            length_summary[segment["system"]][assignment["quantity_level"]][dn_key] += segment["length"]
            segment_records.append(
                {
                    "segment_id": segment_id,
                    "system": segment["system"],
                    "length": segment["length"],
                    "unit": unit,
                    **roles[segment_id],
                    **assignment,
                }
            )

        virtual_length = sum(bridge["virtual_length"] for bridge in bridges)
        raw_length = sum(segment["length"] for segment in segments.values())
        layered_length = sum(
            sum(dn_values.values())
            for system_values in length_summary.values()
            for dn_values in system_values.values()
        )
        if not math.isclose(raw_length, layered_length, rel_tol=0, abs_tol=1e-9):
            fail("分层长度与原始长度不守恒")
        independent_length_summary = summarize_assignments(independent_assignments, segments)
        independent_length = sum(v for levels in independent_length_summary.values() for dns in levels.values() for v in dns.values())
        if not math.isclose(raw_length, independent_length, rel_tol=0, abs_tol=1e-9):
            fail("不依赖虚拟桥的已绘分层基线与原始长度不守恒")

        classified_bridges = [bridge for bridge in bridges if bridge["scenario_include"]]
        from pipe_gap_quantities import pipe_bridge_quantities
        bridge_items, restored, restoration_issues = pipe_bridge_quantities(
            bridges, assignments, valve_records, node_points, snap, point_segment_distance, restore_omissions)
        inclusive_axis_length = sum(item["length"] for item in bridge_items
                                    if item["action"] == "include_occupied_axis" and item["included_in_reconstructed"])
        route_preparation["issues"].extend(restoration_issues)
        restored_summary = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        for system, levels in length_summary.items():
            for level, values in levels.items():
                for dn, value in values.items():restored_summary[system][level][dn] += value
        for (system, level, dn), value in restored.items():restored_summary[system][level][dn] += value
        used_pairs = {frozenset({(b['a']['segment_id'], b['a']['end']), (b['b']['segment_id'], b['b']['end'])}) for b in bridges}
        for pair, classification in gap_classifications.items():
            if pair not in used_pairs or (classification["quantity_action"]=="restore_omission" and not any(
                frozenset({(b['a']['segment_id'],b['a']['end']),(b['b']['segment_id'],b['b']['end'])})==pair
                and b["omission_within_gap_max"] for b in bridges)):
                route_preparation["issues"].append({"type": "断口分类未匹配实际拓扑桥", "endpoints": sorted(pair), "classification": classification})
        base_levels = {a['quantity_level'] for a in assignments.values()}
        base_level = next((v for v in ('未决量','方案暂估量','规则推算量','确认量') if v in base_levels), '未决量')
        classified_length = sum(bridge["virtual_length"] for bridge in classified_bridges)
        bridge_scenarios = [
            {
                "scenario_id": "SC-GAP-BASE",
                "name": "平面已绘中心线基线",
                "bridge_ids": [],
                "incremental_length": 0.0,
                "scenario_total_length": raw_length,
                "unit": unit,
                "quantity_level": base_level,
                "selected": True,
                "meaning": "已绘中心线按实际最低证据层级汇总，不因作为基线而升级确认量",
            },
            {
                "scenario_id": "SC-GAP-ALL-UNIQUE",
                "name": "全部唯一虚拟桥补齐上界方案",
                "bridge_ids": [bridge["bridge_id"] for bridge in bridges],
                "incremental_length": virtual_length,
                "scenario_total_length": raw_length + virtual_length,
                "unit": unit,
                "quantity_level": "方案暂估量",
                "selected": False,
                "assumption_ids": [bridge["assumption_id"] for bridge in bridges],
                "meaning": "用于暴露全部唯一断口的数量影响，不自动进入确认量或正式量",
            },
        ]
        if classified_bridges:
            bridge_scenarios.append(
                {
                    "scenario_id": "SC-GAP-CLASSIFIED",
                    "name": "已分类并允许暂估补齐方案",
                    "bridge_ids": [bridge["bridge_id"] for bridge in classified_bridges],
                    "incremental_length": classified_length,
                    "scenario_total_length": raw_length + classified_length,
                    "unit": unit,
                    "quantity_level": "方案暂估量",
                    "selected": False,
                    "assumption_ids": [bridge["assumption_id"] for bridge in classified_bridges],
                    "meaning": "纳入项目分类或自动几何分类中允许暂估补长的断口，仍需项目裁决",
                }
            )

        fitting_summary: dict[tuple[str, str, str, str], int] = defaultdict(int)
        for item in fitting_records:
            if item.get("quantity_owner") == "outside_scope":
                continue
            fitting_summary[(item["system"], item["type"], item["quantity_level"], item["dn_combination"])] += 1

        readiness_checks = {
            "object_selection_audited": object_selection["status"] in ("READY", "REVIEW_REQUIRED"),
            "object_selection_resolved": object_selection.get("unresolved_count") == 0,
            "endpoint_handoff_reconciled": endpoint_handoff["complete"],
            "gap_candidates_classified": all(
                bridge["classification"] != "未分类断口候选" for bridge in bridges
            ),
            "extended_gap_candidates_resolved": not rejected_gaps,
            "dn_match_candidates_accounted": (
                not dn_match_candidates
                or (
                    dn_match_policy is not None
                    and dn_match_policy["detected_candidate_count"] == len(dn_match_candidates)
                )
            ),
            "root_candidates_available": bool(root_records) if terminal_records else True,
            "network_roles_materialized": (
                any(item["role"] not in {"未定", "环网"} for item in roles.values())
                if terminal_records and root_records else not terminal_records
            ),
            "terminal_port_dn_covered": (
                all(item.get("port_dn") is not None for item in terminal_records)
                if terminal_records else True
            ),
            "valve_type_candidates_covered": (
                all(
                    item.get("valve_type") != "未分类" or bool(item.get("candidate_types"))
                    for item in valve_records
                )
                if valve_records else True
            ),
        }
        workflow_ready = all(readiness_checks.values())
        output = {
            "evidence_version": OUTPUT_VERSION,
            "network_id": network_id,
            "status": "CALCULATED_WITH_DECISIONS" if decision_queue else "CALCULATED",
            "business_result_status": "CANDIDATE_READY_FOR_REVIEW" if workflow_ready else "PARTIAL_CALCULATED",
            "stop_calculation": False,
            "business_adoption_allowed": False,
            "recognition_basis": data.get("recognition_basis"),
            "meaning": "确认基线、规则推算和多方案暂估分列；虚拟桥方案不替代项目专业复核或正式量",
            "unit": unit,
            "parameters": {
                "snap_tolerance": snap,
                "gap_max": gap_max,
                "direction_tolerance_deg": direction_tolerance,
                "terminal_attach_max": terminal_max,
                "valve_attach_max": valve_max,
                "fitting_angle_tolerance_deg": fitting_angle_tolerance,
                "main_min_downstream_terminals": main_min,
                **dict(zip(("collinear_gap_max", "serial_accessory_gap_max", "accessory_coverage_tolerance"),
                           gap_limits(parameters, unit, gap_max, snap))),
            },
            "object_selection": object_selection,
            "endpoint_handoff": endpoint_handoff,
            "dn_match_policy": dn_match_policy,
            "dn_match_candidates": dn_match_candidates,
            "workflow_readiness": {
                "status": "READY" if workflow_ready else "REVIEW_REQUIRED_WITH_RESULTS",
                "checks": readiness_checks,
                "business_correctness_proven": False,
                "meaning": "只判断本次声明的整套推算依赖是否齐全；某端口规格缺失只影响相应派生量，不阻断已有直接标注管段的工作算量，也不代表人工核准",
            },
            "metrics": {
                "segment_count": len(segments),
                "raw_length": raw_length,
                "layered_length": layered_length,
                "length_conservation_delta": layered_length - raw_length,
                "independent_drawn_length": independent_length,
                "independent_length_conservation_delta": independent_length-raw_length,
                "node_count": len(node_points),
                "topology_bridge_count": len(bridges),
                "topology_connected_bridge_count": sum(1 for bridge in bridges if bridge["connectivity_included"]),
                "virtual_bridge_length": virtual_length,
                "virtual_bridge_quantity_included": 0,
                "classified_bridge_scenario_length": classified_length,
                "all_unique_bridge_scenario_total_length": raw_length + virtual_length,
                "ambiguous_gap_endpoint_count": len(ambiguous),
                "classified_gap_count": sum(
                    1 for bridge in bridges if bridge["classification"] != "未分类断口候选"
                ),
                "automatic_gap_classification_count": sum(
                    1 for bridge in bridges if bridge["classification_method"].startswith("automatic_")
                ),
                "object_selection_unresolved_count": object_selection.get("unresolved_count"),
                "terminal_handoff_delivered_count": endpoint_handoff["delivered_count"],
                "terminal_handoff_unresolved_count": endpoint_handoff["unresolved_count"],
                "dn_match_candidate_count": len(dn_match_candidates),
                "dn_match_seed_count": sum(
                    1 for item in dn_match_candidates if item["disposition"] == "seed"
                ),
                "dn_match_scenario_count": sum(
                    1 for item in dn_match_candidates if item["disposition"] == "scenario"
                ),
                "dn_match_excluded_count": sum(
                    1 for item in dn_match_candidates if item["disposition"] == "excluded"
                ),
                "run_count": len(runs),
                "dn_assigned_segment_count": sum(1 for item in assignments.values() if item["dn"]),
                "dn_conditionally_assigned_segment_count": sum(1 for item in assignments.values() if item["dn"] and item.get("requires_topology_assumption")),
                "dn_independently_assigned_segment_count": sum(1 for item in independent_assignments.values() if item["dn"]),
                "dn_unresolved_segment_count": sum(1 for item in assignments.values() if not item["dn"]),
                "role_rule_assignment_count": role_rule_assignment_count,
                "dn_scenario_count": len(dn_scenarios),
                "valve_count": len(valve_records),
                "unclassified_valve_count": sum(1 for item in valve_records if item["valve_type"] == "未分类"),
                "valve_dn_assigned_count": sum(1 for item in valve_records if item["dn"]),
                "fitting_candidate_count": len(fitting_records),
                "outside_scope_fitting_candidate_count": sum(f.get('quantity_owner') == 'outside_scope' for f in fitting_records),
                "decision_count": len(decision_queue),
            },
            "topology_bridges": bridges,
            "rejected_gap_candidates": rejected_gaps,
            "bridge_scenarios": bridge_scenarios,
            "bridge_quantity_items": bridge_items,
            "reconstructed_length_summary": restored_summary,
            "reconstructed_length": raw_length + sum(restored.values()),
            "restored_omission_length": sum(restored.values()) - inclusive_axis_length,
            "project_inclusive_accessory_axis_length": inclusive_axis_length,
            "change_nodes": change_records,
            "scope_boundary_nodes": boundary_records,
            "roots": root_records,
            "terminals": terminal_records,
            "runs": runs,
            "segments": segment_records,
            "dn_scenarios": dn_scenarios,
            "valves": valve_records,
            "valve_summary": [
                {
                    "system": key[0],
                    "valve_type": key[1],
                    "block_family": key[2],
                    "dn": key[3],
                    "quantity_level": key[4],
                    "instance_count": count,
                }
                for key, count in sorted(valve_summary.items(), key=lambda item: tuple(str(v) for v in item[0]))
            ],
            "valve_family_summary": [
                {
                    **{key: value for key, value in summary.items() if key != "candidate_types"},
                    "candidate_types": sorted(summary["candidate_types"]),
                }
                for _, summary in sorted(valve_family_summary.items())
            ],
            "fitting_candidates": fitting_records,
            "fitting_summary": [
                {
                    "system": key[0],
                    "type": key[1],
                    "quantity_level": key[2],
                    "dn_combination": key[3],
                    "candidate_count": count,
                }
                for key, count in sorted(fitting_summary.items())
            ],
            "length_summary": {
                system: {
                    level: dict(sorted(values.items()))
                    for level, values in sorted(levels.items())
                }
                for system, levels in sorted(length_summary.items())
            },
            "independent_drawn_length_summary": independent_length_summary,
            "independent_dn_assignments": independent_assignments,
            "conditional_drawn_segment_length": sum(segments[sid]["length"] for sid,a in assignments.items() if a.get("requires_topology_assumption")),
            "conditional_drawn_quantity_meaning": "原已绘几何长度不变；依赖虚拟桥的DN及对应分规格量只属条件方案。独立已绘基线与此分层方案互为替代，不可相加。",
            "decision_queue": decision_queue,
        }
        output = finalize_network(output, route_preparation, segments, snap_stats)
        output_path = args.output.resolve()
        import hashlib
        from pipe_gap_candidates import review_pending_gaps
        summary_view = {**output, "length_summary": independent_length_summary,
                        "restored_omission_length": 0 if inclusive_axis_length else output["restored_omission_length"]}
        summary = review_markdown(summary_view) + review_pending_gaps(output)
        summary_path = output_path.with_name(output_path.stem + "_" + hashlib.sha256(summary.encode()).hexdigest()[:10] + "_复核摘要.md")
        protect_outputs([output_path, summary_path], protected_sources)
        if summary_path.exists() and summary_path.read_text(encoding="utf-8") != summary:
            fail("复核摘要文件冲突，请使用新版本输出目录")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary_path.write_text(summary, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "review_summary": str(summary_path),
                    "network_id": network_id,
                    "segment_count": len(segments),
                    "topology_bridge_count": len(bridges),
                    "dn_assigned_segment_count": output["metrics"]["dn_assigned_segment_count"],
                    "valve_count": len(valve_records),
                    "decision_count": len(decision_queue),
                    "output": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except ValueError as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
