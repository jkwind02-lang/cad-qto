"""Turn sourced elevation links into unique physical pipe extents and merge lengths.

This consumes explicit model interpretation. It does not recognize fixtures,
assume installation-height measurement points, or measure schematic CAD lines.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts"))
from qto_elevation_facts import (evaluate_elevation_link, evidence, factor, number,
                                 text, weakest, datum_path, QUANTITY_LEVELS)
from model_recognition_basis import file_sha, validate_working_basis

SCHEMA = "qto-pipe-elevation-merge/1.0"
RESULT_SCHEMA = "qto-pipe-elevation-merged-result/1.0"
BASIS = "pipe_centerline_route_length"
LEVEL_BY_ZH = {v: k for k, v in QUANTITY_LEVELS.items()}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_bound(binding, base):
    if not isinstance(binding, dict):
        raise ValueError("来源须有path/sha256")
    path = (Path(base) / text(binding.get("path"), "path")).resolve()
    if not path.is_file() or file_sha(path) != binding.get("sha256"):
        raise ValueError("来源文件不存在或SHA变化: " + str(path))
    return json.loads(path.read_text(encoding="utf-8-sig")), path


def unique(rows, key):
    result = {}
    for row in rows:
        ident = text(row.get(key), key)
        if ident in result:
            raise ValueError(key + "重复: " + ident)
        result[ident] = row
    return result


def network_items(network):
    """Enumerate only length fields with their original ownership semantics.

    Counts, candidate gap lengths and counted-elsewhere interfaces never become
    added pipe length. The three fields below are existing runtime contracts.
    """
    if network.get("evidence_version") != "qto-pipe-network-inference-evidence/1.2":
        raise ValueError("仅支持1.2原始管网结果；不可将已合并结果当原始量再合并")
    unit = network.get("unit")
    factor(unit)
    items = []

    def add(row, prefix, key, value_field, origin):
        ident = prefix + text(row.get(key), key)
        value = row.get(value_field)
        row_unit = row.get("unit", unit)
        value = None if value is None else number(value, ident) * factor(row_unit)
        if value is not None and value < 0:
            raise ValueError("原管网长度不能为负")
        basis = row.get("measurement_basis", BASIS)
        if basis != BASIS or row.get("quantity_kind", "length") != "length":
            raise ValueError("原数量不是管中心线路径长度，不可混并净管/个数等口径")
        spec = copy.deepcopy(row.get("specification", {"dn": row.get("dn")}))
        if not isinstance(spec, dict) or spec.get("dn") != row.get("dn"):
            raise ValueError("原管网规格字段与DN冲突")
        level = row.get("quantity_level", "未决量")
        if level not in {*LEVEL_BY_ZH, "未决量"}:
            raise ValueError("原管网数量层级无效")
        items.append({"record_id": ident, "origin": origin,
            "system": text(row.get("system"), "system"), "specification": spec,
            "quantity_kind": "length", "measurement_basis": basis, "unit": "m",
            "quantity": value, "quantity_level": level,
            "physical_id": row.get("physical_id"), "extent_id": row.get("extent_id"),
            "status": "unresolved" if value is None else "determined", "issues": [],
            "source_record_id": row[key], "elevation_calculation": copy.deepcopy(row.get("elevation_calculation"))})

    for row in network.get("segments", []):
        add(row, "segment:", "segment_id", "length", "drawn_centerline")
    for row in network.get("bridge_quantity_items", []):
        if row.get("included_in_reconstructed") is True:
            if row.get("action") != "restore_omission":
                raise ValueError("断口数量出口状态冲突")
            add(row, "bridge:", "bridge_id", "length", "restored_omission")
    for row in network.get("vertical_quantities", []):
        if row.get("counted_elsewhere") is not True:
            add(row, "vertical:", "link_id", "value", "existing_cross_view")
    unique(items, "record_id")
    return items


def source_evidence(row, catalog, name):
    refs, level = evidence(row, name)
    if any(sid not in catalog for sid in refs):
        raise ValueError(name + "引用未登记的来源")
    return refs, level


def semantic_fact(row, id_field, kind, semantics, catalog):
    refs, level = source_evidence(row, catalog, id_field)
    fid = text(row.get(id_field), id_field)
    matches = [(catalog[sid], fact) for sid in refs for fact in catalog[sid].get("facts", []) if fact.get("id") == fid]
    if not matches or not any(source.get("path") for source, _ in matches):
        raise ValueError(id_field + "缺少带实际来源SHA的绑定事实")
    for _, fact in matches:
        for key, expected in {"measurement_kind": kind, **semantics}.items():
            if key not in fact or fact[key] != expected:
                raise ValueError(id_field + "与来源事实身份不一致: " + key)
        if fact.get("level") not in QUANTITY_LEVELS or weakest([level, fact["level"]]) != level:
            raise ValueError(id_field + "不能提升来源解释层级")
    return level


def endpoint_features(calculation):
    if not calculation:
        return []
    return [{"port_id": e.get("centerline_reference_id", e.get("reference_id")),
             "value": e.get("normalized_value_m")} for e in calculation.get("endpoints", [])]


def same_direction_overlap(left, right):
    """Two pure vertical extents with a shared true port overlap if same-sided."""
    if not left or not right or left.get("route_kind") != "vertical" or right.get("route_kind") != "vertical":
        return False
    a, b = endpoint_features(left), endpoint_features(right)
    if len(a) != 2 or len(b) != 2 or any(v["value"] is None for v in a + b):
        return False
    for i in range(2):
        for j in range(2):
            if a[i]["port_id"] == b[j]["port_id"]:
                if (a[1-i]["value"] - a[i]["value"]) * (b[1-j]["value"] - b[j]["value"]) > 1e-12:
                    return True
    return False


def check_shared_facts(spec, registry):
    # Distinct fact IDs cannot disguise conflicting values at one physical
    # reference, datum and measurement point. Alternative scenarios need runs.
    for side in ("from", "to"):
        row = spec[side]
        key = (spec["system"], row["datum_id"], row["reference_id"], row["measurement_kind"])
        val = None if row["value"] is None else row["value"] * factor(row["unit"])
        if key in registry:
            old = registry[key]
            if ((old is None) != (val is None) or
                    (val is not None and not math.isclose(old, val, rel_tol=1e-12, abs_tol=1e-9))):
                raise ValueError("同一基准/物理测点的高程事实互相矛盾")
        registry[key] = val


def summarize(items):
    groups = {}
    for item in items:
        key = (item["system"], json.dumps(item["specification"], sort_keys=True, ensure_ascii=False),
               item["measurement_basis"], item["unit"], item["quantity_level"])
        group = groups.setdefault(key, {"system": key[0], "specification": item["specification"],
            "measurement_basis": key[2], "unit": key[3], "quantity_level": key[4],
            "known_subtotal": 0., "quantity": None, "record_ids": [], "unresolved_record_ids": []})
        group["record_ids"].append(item["record_id"])
        if item["quantity"] is None:
            group["unresolved_record_ids"].append(item["record_id"])
        else:
            group["known_subtotal"] += item["quantity"]
    for group in groups.values():
        group["quantity"] = None if group["unresolved_record_ids"] else group["known_subtotal"]
    return [groups[key] for key in sorted(groups)]


def check_normalized_ports(records, inputs):
    """Compare final centre points even if raw types, datums or IDs differ."""
    transforms = []
    for row in inputs:
        for transform in row["elevation_facts"].get("datum_transforms", []):
            transform = copy.deepcopy(transform)
            transform["transform_id"] = row["record_id"] + ":" + transform["transform_id"]
            transforms.append(transform)
    ports, checks = {}, []
    for row in records:
        calc = row["elevation_calculation"]
        if not calc:
            continue
        for endpoint in calc["endpoints"]:
            key = (row["system"], endpoint.get("centerline_reference_id", endpoint["reference_id"]))
            value = endpoint.get("normalized_value_m")
            for previous, old_value, old_datum in ports.get(key, []):
                if value is None or old_value is None:
                    continue
                conversion = datum_path(transforms, calc["target_datum"], old_datum)
                check = {"port_id": key[1], "system": key[0], "record_ids": [previous["record_id"], row["record_id"]],
                         "from_datum": calc["target_datum"], "to_datum": old_datum}
                if conversion is None:
                    check["status"] = "unresolved_datum"
                    for affected in (previous, row):
                        affected["issues"].append({"type": "同一归一中心端口跨基准缺少换算，不能认定一致", **check})
                        affected.update(quantity=None, quantity_level="未决量", status="port_datum_unresolved")
                else:
                    offset, tids, refs, levels = conversion
                    if not math.isclose(value + offset, old_value, rel_tol=1e-12, abs_tol=1e-9):
                        raise ValueError("归一后同一物理管中心端口高程互相矛盾: " + key[1])
                    check.update(status="consistent", compared_value_m=value + offset, datum_transform_ids=tids)
                    for affected in (previous, row):
                        if affected["quantity_level"] in LEVEL_BY_ZH:
                            affected["quantity_level"] = QUANTITY_LEVELS[weakest([LEVEL_BY_ZH[affected["quantity_level"]]] + levels)]
                checks.append(check)
            ports.setdefault(key, []).append((row, value, calc["target_datum"]))
    return checks


def scenario_conditions(value, path=""):
    conditions = []
    if isinstance(value, dict):
        if value.get("level") == "scenario" and value.get("basis"):
            conditions.append({"field": path, "basis": value["basis"]})
        for key, child in value.items():
            if key != "sources":
                conditions.extend(scenario_conditions(child, path + "." + key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            conditions.extend(scenario_conditions(child, path + "." + str(index)))
    return conditions


def merge(payload, base_dir="."):
    if payload.get("schema") != SCHEMA:
        raise ValueError("竖向管段合并schema无效")
    text(payload.get("scope_id"), "scope_id")
    text(payload.get("scope_basis"), "scope_basis")
    network, network_path = load_bound(payload.get("network_result"), base_dir)
    if payload.get("network_id") != network.get("network_id"):
        raise ValueError("合并network_id与原管网不同")
    validate_working_basis(network.get("recognition_basis"))
    recognition = network.get("recognition_basis")
    source_sha = None
    if recognition:
        ledger, _ = load_bound({"path": recognition["ledger_path"], "sha256": recognition["ledger_sha256"]}, base_dir)
        source_sha = ledger.get("source_sha256")
    base_items = network_items(network)
    existing = unique(base_items, "record_id")
    endpoint_objects = unique(network.get("segments", []), "segment_id")
    for field, key in (("valves", "valve_id"), ("terminals", "terminal_id")):
        for ident, row in unique(network.get(field, []), key).items():
            if ident in endpoint_objects:
                raise ValueError("不同原管网对象的编号冲突")
            endpoint_objects[ident] = row
    rows = payload.get("segments")
    if not isinstance(rows, list) or not rows:
        raise ValueError("segments须有逐个物理管段")
    unique(rows, "record_id")
    unique(rows, "physical_id")
    unique(rows, "extent_id")
    seen_pairs, seen_object_pairs, shared_facts, records = set(), set(), {}, []
    for row in rows:
        rid = row["record_id"]
        if rid in existing:
            raise ValueError("新旧数量出口编号冲突")
        if row.get("unit") != "m" or row.get("measurement_basis") != BASIS or row.get("quantity_kind", "length") != "length":
            raise ValueError("派生竖段须按米及管中心线路径长度；不得输入数量/净管或任意单位")
        if any(k in row for k in ("quantity", "length", "count", "runs", "multiplier")):
            raise ValueError("每个物理竖段独立计算，不接受外部数量、乘数或底稿值")
        spec = row.get("specification")
        if not isinstance(spec, dict) or not isinstance(spec.get("dn"), str) or not spec["dn"].strip():
            raise ValueError("竖段须给明确规格，其他规格不得按同DN归并")
        system = text(row.get("system"), "system")
        facts = row.get("elevation_facts")
        calc = evaluate_elevation_link(facts, "m", expected_system=system)
        if facts.get("route", {}).get("kind") not in {"vertical", "unresolved"}:
            raise ValueError("本入口只接竖直或尚未证明的竖段；斜管/折线应显式分段")
        catalog = {s["id"]: s for s in facts["sources"]}
        if not any(s.get("path") for s in catalog.values()):
            raise ValueError("竖段须有可实际复核SHA的来源文件")
        if source_sha and source_sha not in {s["sha256"] for s in catalog.values()}:
            raise ValueError("竖段与原管网未绑定同一图源SHA")
        refs, level = source_evidence(row, catalog, rid)
        levels = [level, calc["evidence_level"]]
        check_shared_facts(facts, shared_facts)
        ports = [facts[s].get("centerline_conversion", {}).get("target_reference_id", facts[s]["reference_id"])
                 for s in ("from", "to")]
        if ports[0] == ports[1]:
            raise ValueError("竖段两端不能是同一物理端口")
        pair = (system, tuple(sorted(ports)))
        if pair in seen_pairs:
            raise ValueError("同一物理端口对重复，不能改extent或physical编号再次计量")
        seen_pairs.add(pair)
        bindings = row.get("endpoint_bindings", [])
        by_port = unique(bindings, "port_id")
        if set(by_port) != set(ports):
            raise ValueError("端口绑定必须恰好覆盖高程换算后的两个物理端口")
        object_pair = (system, tuple(sorted(b.get("network_record_id", "") for b in bindings)))
        if object_pair in seen_object_pairs:
            raise ValueError("同一对原管网对象被重复关联，改端口名称不能生成第二条实物竖段")
        seen_object_pairs.add(object_pair)
        binding_issues = []
        bound_keys = []
        for port in ports:
            binding = by_port[port]
            binding_refs, lv = source_evidence(binding, catalog, "endpoint_binding")
            refs.extend(binding_refs)
            levels.append(lv)
            obj = endpoint_objects.get(binding.get("network_record_id"))
            if obj is None or obj.get("system") != system:
                raise ValueError("竖段端口未绑定同系统实际管网对象")
            if obj.get("dn") != spec["dn"]:
                raise ValueError("竖段规格与端口DN不一致或端口DN未定，须先划明变径")
            if obj.get("specification") is not None and obj["specification"] != spec:
                raise ValueError("同DN但完整规格不同，不得并成同一竖段")
            if binding.get("port_binding_fact_id") is not None:
                port_key = text(binding.get("object_port_key"), "object_port_key")
                levels.append(semantic_fact(binding, "port_binding_fact_id", "network_port_binding", {
                    "system": system, "network_record_id": binding["network_record_id"],
                    "port_id": port, "object_port_key": port_key}, catalog))
                bound_keys.append(port_key)
            endpoint_level = obj.get("evidence_level", obj.get("dn_evidence_level"))
            if endpoint_level in QUANTITY_LEVELS:
                levels.append(endpoint_level)
            else:
                levels.append(LEVEL_BY_ZH.get(obj.get("quantity_level", obj.get("dn_quantity_level")), "scenario"))
        if bindings[0]["network_record_id"] == bindings[1]["network_record_id"] and (len(bound_keys) != 2 or len(set(bound_keys)) != 2):
            binding_issues.append({"type": "两端绑定同一原管网对象但未证明是不同实体端口"})
        coverage = row.get("network_coverage", {})
        coverage_refs, lv = source_evidence(coverage, catalog, "network_coverage")
        refs.extend(coverage_refs)
        levels.append(lv)
        keys = ("disjoint_record_ids", "same_extent_record_ids", "partial_overlap_record_ids", "unknown_record_ids")
        all_ids = []
        for key in keys:
            members = coverage.get(key, [])
            if not isinstance(members, list) or any(not isinstance(x, str) for x in members):
                raise ValueError("数量覆盖审计成员须为编号数组")
            all_ids.extend(members)
        if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(existing):
            raise ValueError("既有数量覆盖审计必须完整且不重复，不能静默跳过已计竖段")
        same = coverage.get("same_extent_record_ids", [])
        if len(same) > 1:
            raise ValueError("同一完整extent存在多个既有出口，须先解决原量重复或分段范围")
        for eid, item in existing.items():
            if ((item.get("physical_id") == row["physical_id"] or item.get("extent_id") == row["extent_id"])
                    and eid not in same):
                raise ValueError("原竖段物理/extent身份已计，不能声明互不重叠")
            old_calc = item.get("elevation_calculation")
            old_ports = [v["port_id"] for v in endpoint_features(old_calc)]
            if item["system"] == system and len(old_ports) == 2 and set(old_ports) == set(ports) and eid not in same:
                raise ValueError("原量已有同一物理端口对，不能改physical名称再追加")
            if item["system"] == system and eid not in same and same_direction_overlap(old_calc, calc):
                raise ValueError("新旧纯竖段共享端口且同向重叠，须先拆分共享区段")
        for old in records:
            if old["system"] == system and same_direction_overlap(old["elevation_calculation"], calc):
                raise ValueError("新增纯竖段共享端口且同向部分重叠，须先拆分共享区段")
        issues = copy.deepcopy(calc["issues"]) + binding_issues
        quantity = calc["value"]
        status, owner = "added", rid
        if binding_issues:
            quantity, status = None, "endpoint_unresolved"
        elif coverage.get("partial_overlap_record_ids") or coverage.get("unknown_record_ids"):
            issues.append({"type": "既有数量范围重叠或未判清，不能追加或当作零", "coverage": copy.deepcopy(coverage)})
            quantity, status = None, "coverage_unresolved"
        elif same:
            old = existing[same[0]]
            if old["system"] != system or old["specification"] != spec or old["measurement_basis"] != row["measurement_basis"]:
                raise ValueError("同一extent新旧系统/完整规格/计量口径冲突")
            identity_conflict = any(old.get(k) is not None and old[k] != row[k] for k in ("physical_id", "extent_id"))
            if identity_conflict:
                aliases = [a for a in coverage.get("identity_aliases", []) if a.get("existing_record_id") == old["record_id"]]
                if len(aliases) != 1:
                    raise ValueError("不同物理/extent身份不能冒领已计数量，须有来源别名合并事实")
                alias = aliases[0]
                levels.append(semantic_fact(alias, "alias_fact_id", "physical_extent_identity", {
                    "system": system, "existing_record_id": old["record_id"],
                    "existing_physical_id": old.get("physical_id"), "existing_extent_id": old.get("extent_id"),
                    "new_physical_id": row["physical_id"], "new_extent_id": row["extent_id"],
                    "from_port_id": ports[0], "to_port_id": ports[1]}, catalog))
                refs.extend(alias["source_ids"])
            if quantity is None or old["quantity"] is None:
                quantity, status = None, "existing_quantity_unresolved"
            elif not math.isclose(quantity, old["quantity"], rel_tol=1e-9, abs_tol=1e-8):
                issues.append({"type": "同一extent新旧长度不一致，禁止补差", "existing_quantity_m": old["quantity"], "derived_quantity_m": quantity})
                quantity, status = None, "existing_quantity_conflict"
            else:
                quantity, status, owner = 0., "already_counted", old["record_id"]
        elif quantity is None:
            status = "elevation_unresolved"
        records.append({"record_id": rid, "name": row.get("name", row["extent_id"]), "physical_id": row["physical_id"], "extent_id": row["extent_id"],
            "system": system, "specification": copy.deepcopy(spec), "quantity_kind": "length", "unit": "m",
            "measurement_basis": row["measurement_basis"], "origin": "elevation_segment_remainder",
            "derived_length": calc["value"], "quantity": quantity, "quantity_level": "未决量" if quantity is None else QUANTITY_LEVELS[weakest(levels)],
            "status": status, "quantity_owner": owner, "ports": ports, "endpoint_bindings": copy.deepcopy(bindings),
            "source_ids": list(dict.fromkeys(refs + calc["source_ids"])), "basis": row["basis"], "issues": issues,
            "scenario_conditions": scenario_conditions(row),
            "network_coverage": copy.deepcopy(coverage), "elevation_calculation": calc})
    port_checks = check_normalized_ports(base_items + records, rows)
    items = base_items + records
    result = {"schema": RESULT_SCHEMA, "network_id": network["network_id"], "scope_id": payload["scope_id"],
        "scope_basis": payload["scope_basis"], "input_sha256": digest(payload),
        "network_file": {"path": str(network_path), "sha256": file_sha(network_path)},
        "network_result": copy.deepcopy(network), "base_length_items": base_items, "elevation_segments": records,
        "normalized_port_checks": port_checks,
        "length_summary": summarize(items), "base_length_summary": summarize(base_items), "added_length_summary": summarize(records),
        "unresolved_record_ids": [r["record_id"] for r in items if r["quantity"] is None],
        "adoption_scope": "working", "review_status": "unreviewed", "formal_adoption_allowed": False,
        "scope_complete": False, "meaning": "原管网数量出口保持；新增竖段按实物区段去重，分层汇总，未决不补零；非净管或完整施工量。"}
    result["result_sha256"] = digest(result)
    return result


def markdown(result):
    def cell(value):
        if isinstance(value, float):
            value = format(value, ".6f").rstrip("0").rstrip(".")
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return ("未决" if value is None else str(value)).replace("|", "\\|").replace("\n", " ")
    lines = ["# 高程推导竖向管段与原管网合并", "", "范围：" + cell(result["scope_id"]), "", result["meaning"],
             "模型工作识别，未人工复核或正式采用。下表各数量层级分别保留，方案暂估不并入确认量。", "",
             "| 实物区段 | 系统 | 规格 | 高程推导长 m | 新增长 m | 层级 | 状态 | 数量出口 |", "| --- | --- | --- | ---: | ---: | --- | --- | --- |"]
    for row in result["elevation_segments"]:
        lines.append("| " + " | ".join(cell(row[k]) for k in ("name", "system", "specification", "derived_length", "quantity", "quantity_level", "status", "quantity_owner")) + " |")
    for label, field in (("原已计管网小计（含已采用补断）", "base_length_summary"), ("本轮竖向追加量", "added_length_summary"), ("原量与新增量分层合计", "length_summary")):
        lines += ["", "## " + label, "", "| 系统 | 规格 | 数量层级 | 已算部分 m | 本组无未决时的小计 m | 未决对象 |", "| --- | --- | --- | ---: | ---: | --- |"]
        for row in result[field]:
            lines.append("| " + " | ".join(cell(row[k]) for k in ("system", "specification", "quantity_level", "known_subtotal", "quantity", "unresolved_record_ids")) + " |")
    for row in result["elevation_segments"]:
        lines += ["", "### " + row["name"], "", row["basis"], "", "实物区段：" + row["extent_id"], "端口：" + cell(row["ports"]),
                  "来源：" + cell(row["source_ids"]), "问题：" + cell(row["issues"])]
        for end in row["elevation_calculation"]["endpoints"]:
            lines.append("- 原测点 " + cell(end["measurement_kind"]) + "：" + cell(end["original_value"]) + " " + end["original_unit"] +
                         "；基准 " + end["datum_id"] + "；归一中心标高 m：" + cell(end.get("normalized_value_m")))
        if row["scenario_conditions"]:
            lines += ["", "方案前提（不代表实际安装已确认）：", ""]
            lines.extend("- " + condition["field"] + "：" + cell(condition["basis"]) for condition in row["scenario_conditions"])
    sources = {}
    for row in result["elevation_segments"]:
        for source in row["elevation_calculation"]["calculation_graph"]["sources"]:
            key = (source.get("path"), source["sha256"])
            sources.setdefault(key, set()).add(source["locator"])
    lines += ["", "## 可复核来源", ""]
    for (path, sha), locators in sources.items():
        lines.append("- " + cell(path) + "；SHA256 " + sha + "；" + "；".join(sorted(locators)))
    lines += ["", "原管网未决与构造范围仍保留在 network_result；这里的合并不补齐支吊架、套管、保温或阀后软管。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    report = target.with_suffix(".md")
    if target.exists() or report.exists() or target == args.input.resolve():
        parser.error("须使用未存在的新输出，不能覆盖来源或旧成果")
    result = merge(json.loads(args.input.read_text(encoding="utf-8-sig")), args.input.resolve().parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report.write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": "PASS", "unresolved": result["unresolved_record_ids"], "output": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
