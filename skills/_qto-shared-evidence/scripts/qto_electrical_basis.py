"""Bind electrical elevation arithmetic to sourced, scoped physical endpoints.

This validates an explicit interpretation. It does not recognize a connection
port from CAD or grant engineering adoption to a hash-checked source record.
"""
from __future__ import annotations

import copy
import math

from qto_elevation_facts import (LEVELS, evidence, evaluate_elevation_link,
                                 record, text, weakest)


def evaluate_electrical_elevation(path, document):
    """Return a typed elevation result, enforcing the electrical application.

    Source facts carry project/scope as well as the existing datum, measurement
    and system semantics. Endpoint facts independently bind node to entity/port;
    an installation bottom or a floor level is not itself a connection port.
    """
    pid = text(path.get("id"), "path.id")
    scope = text(path.get("scope_id"), pid + ".scope_id")
    system = text(path.get("system"), pid + ".system")
    if path.get("role") != "vertical":
        raise ValueError("elevation_difference只用于有据纯竖向路径:" + pid)
    spec = record(path.get("elevation_link"), pid + ".elevation_link")
    context = {"project_id": document["project_id"], "scope_id": scope, "system": system}
    for key, expected in context.items():
        if spec.get(key) != expected:
            raise ValueError("标高事实适用范围与路径的" + key + "不一致:" + pid)
    # Existing validator checks values, typed measurement conversion, datum
    # transforms, source hashes and every sourced arithmetic/route fact.
    result = evaluate_elevation_link(spec, expected_system=system)
    if result["route_kind"] != "vertical":
        raise ValueError("标高差不证明非竖直路径的全长:" + pid)
    catalog = {s["id"]: s for s in spec["sources"]}
    used_facts = []
    used_sources = set(result["source_ids"])
    levels = [result["evidence_level"]]

    def bind_context(row, id_key):
        fid = text(row.get(id_key), pid + "." + id_key)
        refs, level = evidence(row, fid)
        used_sources.update(refs)
        used_facts.append(fid)
        found = [f for sid in refs for f in catalog[sid].get("facts", []) if f["id"] == fid]
        if not found:
            raise ValueError("电气事实未登记在所引来源:" + fid)
        for fact in found:
            if any(fact.get(k) != v for k, v in context.items()):
                raise ValueError("来源事实缺少或不符合项目/系统/适用范围:" + fid)
        return found, level

    def walk(row):
        if isinstance(row, dict):
            for id_key in ("fact_id", "conversion_id", "transform_id", "route_id"):
                if id_key in row:
                    bind_context(row, id_key)
            for key, child in row.items():
                if key not in ("sources", "endpoint_bindings"):
                    walk(child)
        elif isinstance(row, list):
            for child in row:
                walk(child)
    walk(spec)
    bindings = record(spec.get("endpoint_bindings"), "endpoint_bindings")
    endpoint_identities = []
    for side, node_key, endpoint in zip(("from", "to"), ("from_node", "to_node"), result["endpoints"]):
        binding = record(bindings.get(side), "endpoint_bindings." + side)
        node = text(path.get(node_key), node_key)
        expected = {"measurement_kind": "electrical_endpoint", "reference_id": node,
                    "entity_id": text(binding.get("entity_id"), side + ".entity_id"),
                    "port_id": text(binding.get("port_id"), side + ".port_id")}
        endpoint_identities.append((expected["entity_id"], expected["port_id"]))
        if binding.get("reference_id") != node:
            raise ValueError("端点绑定reference_id与路径节点不一致:" + pid)
        facts, level = bind_context(binding, "fact_id")
        for fact in facts:
            if any(fact.get(k) != v for k, v in expected.items()):
                raise ValueError("来源端点实体/端口/节点身份不一致:" + pid)
            if fact.get("level") not in LEVELS or LEVELS[level] < LEVELS[fact["level"]]:
                raise ValueError("端点绑定不能提升来源的方案/推算层级:" + pid)
        levels.append(level)
        # Original reference levels are allowed only through the typed
        # conversion to this sourced endpoint (e.g. floor + signed offset).
        effective_reference = endpoint.get("centerline_reference_id", endpoint["reference_id"])
        if effective_reference != node:
            raise ValueError("标高测点未闭合到路径真实端点:" + pid)
        if endpoint["measurement_kind"] in {"pipe_center", "pipe_outer_bottom", "pipe_outer_top"}:
            raise ValueError("电气端点不能以管道中心测点替代:" + pid)
    if len(set(endpoint_identities)) != 2:
        raise ValueError("非零竖向路径两端不能复用同一真实实体端口:" + pid)
    if not used_facts or not set(used_facts) <= set(path.get("basis_ids", [])):
        raise ValueError("basis_ids未覆盖实际标高/转换/路线/端点事实:" + pid)
    if not used_sources <= set(path.get("source_ids", [])):
        raise ValueError("路径source_ids未覆盖实际标高及端点来源:" + pid)
    if result["value"] is None:
        if path.get("length_m") is not None:
            raise ValueError("未决标高路径length_m须为null，不能填入猜测长度:" + pid)
    elif path.get("length_m") is None or isinstance(path["length_m"], bool) or not isinstance(path["length_m"], (int, float)) or not math.isclose(path["length_m"], result["value"], rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError("路径length_m与有据标高复算不一致:" + pid)
    result["evidence_level"] = weakest(levels)
    # Endpoint applicability is a real dependency of the electrical route.
    # Keep the original arithmetic facts intact, but grade and fingerprint the
    # consumable route node with the complete sourced binding interpretation.
    # Recompute through the existing engine, rather than patching display labels.
    result["source_ids"] = sorted(used_sources)
    result["dependency_source_ids"] = list(result["source_ids"])
    route_node = next(row for row in result["calculation_graph"]["nodes"] if row["id"] == "route_length")
    # Confirmed source facts do not confirm an electrically interpreted route.
    # Retain their real evidence_level; cap only the consumable route result.
    route_level = "方案暂估量" if result["evidence_level"] == "scenario" else "规则推算量"
    route_node["quantity_level"] = route_level if result["value"] is not None else "未决量"
    route_node["source_ids"] = list(result["source_ids"])
    route_node["electrical_endpoint_bindings"] = copy.deepcopy(bindings)
    route_node["electrical_context"] = dict(context)
    from recompute_qto_dependencies import recalculate
    result["calculation_run"] = recalculate(result["calculation_graph"])
    calculated = next(row for row in result["calculation_run"]["nodes"] if row["id"] == "route_length")
    if (calculated["value"] is None) != (result["value"] is None) or (calculated["value"] is not None and not math.isclose(calculated["value"], result["value"], rel_tol=1e-12, abs_tol=1e-10)):
        raise ValueError("电气端口适用依据传播后与标高复算结果不一致:" + pid)
    result["quantity_level"] = calculated["quantity_level"]
    result["effective_length_basis"] = "assumption" if result["evidence_level"] == "scenario" else "elevation_difference"
    result["electrical_binding_check"] = {**context, "bound_fact_ids": sorted(set(used_facts)),
                                          "endpoint_binding_verified": True,
                                          "meaning": "sourced_interpretation_not_original_drawing_acceptance"}
    return result
