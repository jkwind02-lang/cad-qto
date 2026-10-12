"""Join a frozen pipe result and instance requirements without counting twice.

The original network remains intact. Drawn valves own their counts; requirements
own only an explicitly matched remainder. This tool also emits and checks the
human-readable per-instance summary, including excluded and unresolved records.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts"))
from model_recognition_basis import file_sha, validate_working_basis
from qto_instance_requirements import digest, identity_map, verify_result
from qto_pipe_topology import review_markdown
from qto_pipe_quantity_contract import valve_quantity_level, valve_source_identity

SCHEMA = "qto-pipe-instance-merge/1.0"
RESULT_SCHEMA = "qto-pipe-instance-merged-result/1.0"


def load_bound(item, base):
    if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
        raise ValueError("合并输入必须包含 path / sha256")
    path = (base / item["path"]).resolve()
    if file_sha(path) != item["sha256"]:
        raise ValueError("合并输入文件内容已变化")
    return json.loads(path.read_text(encoding="utf-8-sig")), path


def merge(payload, base_dir="."):
    if payload.get("schema") != SCHEMA:
        raise ValueError("管网附属量合并版本无效")
    base = Path(base_dir).resolve()
    network, network_path = load_bound(payload.get("network_result"), base)
    requirements, requirements_path = load_bound(payload.get("instance_requirements_result"), base)
    verify_result(requirements, requirements_path.parent)
    if payload.get("scope_id") != requirements["scope_id"] or not payload.get("scope_basis"):
        raise ValueError("管网与附属量需声明同一 scope_id 及 scope_basis")
    if payload.get("network_id") != network.get("network_id"):
        raise ValueError("管网编号与合并范围绑定不一致")
    if not str(network.get("evidence_version", "")).startswith("qto-pipe-"):
        raise ValueError("仅支持现有管网计算结果")
    basis = network.get("recognition_basis")
    validate_working_basis(basis)
    if not basis:
        raise ValueError("管网结果须保留可核验的模型工作识别来源")
    ledger_path = Path(basis["ledger_path"])
    if file_sha(ledger_path) != basis["ledger_sha256"]:
        raise ValueError("管网识别台账已经变化")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    if ledger.get("source_sha256") not in {r["sha256"] for r in requirements["sources"].values()}:
        raise ValueError("管网与逐实例附属量未绑定同一来源版本")
    valves = identity_map(network.get("valves", []), "valve_id", "network.valves")
    claim_owners = {}
    items = []
    source_instances = set()
    for valve_id, valve in valves.items():
        if not valve.get("system"):
            raise ValueError("既有阀件缺系统")
        source_identity = valve_source_identity(valve)
        if source_identity is not None:
            if source_identity in source_instances:
                raise ValueError("原管网阀门存在重复来源实例，须先解决重复数量出口")
            source_instances.add(source_identity)
        items.append({"item_id": valve_id, "origin": "drawn_network_accessory", "system": valve["system"],
            "type": valve.get("valve_type", "未分类"), "specification": {"dn": valve.get("dn")}, "unit": "个",
            "quantity": 1, "quantity_level": valve_quantity_level(valve),
            "block_family": valve.get("block_family"), "candidate_types": valve.get("candidate_types", []),
            "status": "determined" if valve.get("dn") else "specification_unresolved",
            "status_meaning": "仅表示规格是否已有可计算值；实物存在性、范围及属性证据由quantity_level限定，不因determined升级为确认量",
            "requirement_records": []})
    item_by_id = {i["item_id"]: i for i in items}
    for row in requirements["records"]:
        if row["professional_owner"] != "qto-pipe-networks":
            raise ValueError("管网适配器不得消费其他专业附属量")
        for matched in row["matched_existing"]:
            vid = matched["result_record_id"]
            valve = valves.get(vid)
            if valve is None:
                raise ValueError("已绘附件数量出口未在原管网结果找到: " + vid)
            if vid in claim_owners and claim_owners[vid] != row["record_id"]:
                raise ValueError("同一原管网附件被重复匹配")
            claim_owners[vid] = row["record_id"]
            anchor = matched["identity_anchor"]
            anchor_source = requirements["sources"][anchor["source_id"]]
            network_source_object = valve.get("source_object_id") or valve.get("source_id")
            recognized_objects = {o for entry in ledger.get("entries", []) if entry.get("decision") == "include"
                                  for o in entry.get("object_ids", [])}
            if (network_source_object != anchor["object_id"] or network_source_object not in recognized_objects
                    or anchor_source["sha256"] != ledger["source_sha256"]):
                raise ValueError("图示附件数量出口与原始物理对象身份不一致: " + vid)
            if (valve["system"] != matched["system"] or valve.get("valve_type") != matched["accessory_type"]
                    or matched.get("specification") != {"dn": valve.get("dn")} or matched.get("unit") != "个"):
                raise ValueError("图示附件系统/类型/规格与原管网数量出口不一致: " + vid)
            item_by_id[vid]["requirement_records"].append(row["record_id"])
        if row["record_id"] in item_by_id:
            raise ValueError("派生附属量编号与原管网编号冲突")
        # Include a row even for zero remainder, exclusion or unresolved quantity.
        item = {"item_id": row["record_id"], "origin": "instance_requirement_remainder", "system": row["system"],
                "type": row["accessory_type"], "specification": row["specification"], "unit": row["unit"],
                "quantity": row["additional_quantity"], "quantity_level": row["quantity_level"], "status": row["status"],
                "requirement_records": [row["record_id"]], "subject_physical_id": row["subject_physical_id"],
                "issues": row["issues"], "specification_complete": row["specification_complete"]}
        item.update(block_family=None, candidate_types=[])
        item.update(quantity_meaning="unmatched_declared_requirement; not procurement or installed remainder",
                    procurement_additional_quantity=None)
        items.append(item)
        item_by_id[item["item_id"]] = item
    groups = defaultdict(lambda: {"quantity": 0, "unresolved_items": [], "excluded_items": [], "item_ids": []})
    for item in items:
        key = (item["origin"], item["system"], item["type"], json.dumps(item["specification"], sort_keys=True, ensure_ascii=False),
               item["unit"], item["quantity_level"], str(item["block_family"] or ""), json.dumps(item["candidate_types"], ensure_ascii=False, sort_keys=True))
        group = groups[key]
        group["item_ids"].append(item["item_id"])
        if item["status"] == "excluded":
            group["excluded_items"].append(item["item_id"])
        elif item["quantity"] is None:
            group["unresolved_items"].append(item["item_id"])
        else:
            group["quantity"] += item["quantity"]
            if item.get("specification_complete") is False or item["status"] == "specification_unresolved":
                group["unresolved_items"].append(item["item_id"])
    summaries = [{"origin": k[0], "system": k[1], "type": k[2], "specification": json.loads(k[3]),
                  "unit": k[4], "quantity_level": k[5], "block_family": k[6] or None, "candidate_types": json.loads(k[7]), **v,
                  "quantity": v["quantity"] if any(item_by_id[i]["quantity"] is not None for i in v["item_ids"]) else None}
                 for k, v in sorted(groups.items())]
    result = {"schema": RESULT_SCHEMA, "network_id": network["network_id"], "scope_id": payload["scope_id"],
              "scope_basis": payload["scope_basis"], "input_files": {"network": {"path": str(network_path), "sha256": file_sha(network_path)},
              "requirements": {"path": str(requirements_path), "sha256": file_sha(requirements_path)}},
              "network_result": copy.deepcopy(network), "instance_requirements": copy.deepcopy(requirements),
              "accessory_items": items, "accessory_summary": summaries,
              "adoption_scope": "working", "review_status": "unreviewed", "formal_adoption_allowed": False,
              "procurement_additional_quantity": None,
              "meaning": "原管网阀附件计数保持；需求总量已含匹配现件。未匹配需求仅相对于本次提交的现件对应，不证明采购缺口或安装余量；采购新增量未定。完整施工范围仍须核对原管网未决及专业计量待办。"}
    result["result_sha256"] = digest(result)
    return result


def cell(value):
    if value is None:
        return "未决"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def requirement_table_row(row):
    required = "不适用" if row["status"] == "excluded" else row["required_quantity"]
    remainder = "不适用" if row["status"] == "excluded" else row["additional_quantity"]
    values = (row["record_id"], row["subject_physical_id"], row["requirement_ids"], row["accessory_type"], row["specification"],
              required, row["existing_count"], remainder, row["unit"], row["quantity_level"],
              row["status"], row["excluded_by"], row["issues"], row["required_facts"], row["clause_evaluations"], row["evidence_refs"])
    return "| " + " | ".join(cell(v) for v in values) + " |"


def accessory_table_row(row):
    quantity = "不适用" if (row["quantity"] is None and not row["unresolved_items"]
                               and len(row["excluded_items"]) == len(row["item_ids"])) else row["quantity"]
    family = "不适用（条款推导）" if row["origin"] == "instance_requirement_remainder" else row["block_family"]
    values = (row["origin"], row["system"], row["type"], family, row["candidate_types"], row["specification"], quantity, row["unit"], row["quantity_level"], row["unresolved_items"], row["excluded_items"])
    return "| " + " | ".join(cell(v) for v in values) + " |"


def markdown(result):
    network = result["network_result"]
    output = ["# 管网与逐实例附属量工作计算", "", "范围：" + cell(result["scope_id"]), "",
              result["meaning"], "模型工作识别，尚未人工复核，不进入正式量。", "",
              "以下原管网逐路摘要完整保留，不与附属需求互相替代：", "", original_network_markdown(network)]
    output += ["", "原管网未决记录：" + cell(network.get("decision_queue", [])), "",
               "## 阀门与在线附件汇总", "", "图示数量与未匹配需求分列；需求总量已含图示部分，不能再次相加。未提交现件匹配不等于没有现件，未匹配需求不作新增采购量。", "",
               "| 数量来源 | 系统 | 阀门/附件类型 | 块族 | 候选类型 | DN/规格 | 已算数量 | 单位 | 数量层级 | 未决记录 | 排除记录 |",
               "| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |"]
    output += [accessory_table_row(r) for r in result["accessory_summary"]]
    output += ["", "## 逐实例要求与唯一数量出口", "",
               "| 记录 | 物理实例/端口 | 条款 | 附件 | 规格 | 应设 | 已匹配现件 | 未匹配需求 | 单位 | 层级 | 状态 | 例外 | 未决/冲突 | 专业待补事实 | 各条款适用 | 证据 |",
               "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |"]
    output += [requirement_table_row(r) for r in result["instance_requirements"]["records"]]
    output += ["", "未分配构造要求的实例：" + cell(result["instance_requirements"]["unassigned_subject_physical_ids"]), "",
               "声明范围内附属量完整性：" + str(result["instance_requirements"]["scope_complete"]), "",
               "## 来源定位", ""]
    for key, ev in result["instance_requirements"]["evidence_catalog"].items():
        output.append("- " + cell(key) + "：" + cell(ev["path"]) + "；" + cell(ev["locator"]) + "；SHA256 " + ev["sha256"])
    return "\n".join(output) + "\n"


def original_network_markdown(network):
    original = review_markdown(network)
    original = original.replace("| 系统 | 类型 | 管口规格组合 | 层级 | 候选数 |", "| 系统 | 管件类型 | DN组合 | 数量层级 | 候选数 |")
    lines = [original, "", "## 断口分类与数量影响", "", "断口虚拟桥方案及增量逐项列示；是否计入按原结果执行。", "",
             "| 断口/虚拟桥 | 分类 | 方案及是否计入 | 虚拟长度/增量 | 原始处理记录 |", "| --- | --- | --- | ---: | --- |"]
    for bridge in network.get("topology_bridges", []):
        values = (bridge.get("bridge_id"), bridge.get("classification"), bridge.get("quantity_decision"),
                  bridge.get("length", bridge.get("distance")), bridge)
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    return "\n".join(lines)


def validate_summary(result, text):
    """Check every exact business row, not merely the presence of category words."""
    errors = []
    if original_network_markdown(result["network_result"]) not in text:
        errors.append("原管网管长、恢复、管件或断口摘要未完整保留")
    for row in result["instance_requirements"]["records"]:
        if text.splitlines().count(requirement_table_row(row)) != 1:
            errors.append("逐实例行遗漏、重复或数值被改写: " + row["record_id"])
    for row in result["accessory_summary"]:
        if text.splitlines().count(accessory_table_row(row)) != 1:
            errors.append("附属件汇总遗漏或改写: " + row["type"])
    if errors:
        raise ValueError("; ".join(errors))
    return {"status": "PASS", "instance_rows": len(result["instance_requirements"]["records"]),
            "summary_rows": len(result["accessory_summary"]), "meaning": "逐行计算字段一致，不证明识图语义正确"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary_path = args.output.with_suffix(".md")
        if args.output.exists() or summary_path.exists():
            raise ValueError("输出已经存在，请使用新版本路径")
        result = merge(json.loads(args.input.read_text(encoding="utf-8-sig")), args.input.parent)
        summary = markdown(result)
        checked = validate_summary(result, summary)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary_path.write_text(summary, encoding="utf-8")
        print(json.dumps({**checked, "output": str(args.output), "summary": str(summary_path)}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
