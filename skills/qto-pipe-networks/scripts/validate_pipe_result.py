#!/usr/bin/env python3
"""Validate pipe-specific invariants in a normalized QTO result sample."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


# Reuse the shared partial-result wording check so the two gates cannot drift.
_minimum_path = Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts/validate_minimum_result.py"
_minimum_spec = importlib.util.spec_from_file_location("qto_minimum_naming", _minimum_path)
_minimum = importlib.util.module_from_spec(_minimum_spec)
_minimum_spec.loader.exec_module(_minimum)
FORBIDDEN_COMMERCIAL_WORDS = ("单价", "合价", "报价", "成本")
FORBIDDEN_DN_METHODS = ("最近文字", "最近管线", "经验传播", "颜色推断", "图层经验")
VALID_VERTICAL_METHODS = ("三维几何", "起止标高", "系统图唯一绑定", "用户确认高度")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="检查管道工程量成果的专业边界")
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--calculation", type=Path)
    parser.add_argument("--inference", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    try:
        result = load_json(args.result.resolve())
    except ValueError as exc:
        print(json.dumps({"status": "FAIL", "errors": [str(exc)]}, ensure_ascii=False))
        return 2

    if result.get("契约版本") != "qto-result-minimum/1.0":
        errors.append("契约版本必须为 qto-result-minimum/1.0")

    rules = result.get("本次规则清单", [])
    quantities = result.get("工程量结果", [])
    unresolved = result.get("整体未决事项", [])
    if not isinstance(rules, list) or not isinstance(quantities, list) or not isinstance(unresolved, list):
        errors.append("本次规则清单、工程量结果和整体未决事项必须是数组")
        rules, quantities, unresolved = [], [], []

    rule_map: dict[str, dict[str, Any]] = {}
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"本次规则清单[{index}] 不是对象")
            continue
        rule_id = rule.get("规则编号")
        if isinstance(rule_id, str):
            if rule_id in rule_map:
                errors.append(f"规则编号重复: {rule_id}")
            rule_map[rule_id] = rule
        if rule.get("成熟状态") == "停用" and rule.get("本次判定") in ("采用", "试用"):
            errors.append(f"{rule_id or index} 为停用规则，不能采用或试用")
        if (
            rule.get("成熟状态") == "候选"
            and rule.get("本次判定") == "采用"
            and not rule.get("项目采用依据")
        ):
            errors.append(f"{rule_id or index} 为候选规则，项目采用时缺少项目采用依据")

    unresolved_ids = {
        item.get("事项编号") for item in unresolved if isinstance(item, dict) and isinstance(item.get("事项编号"), str)
    }
    calculation_links: dict[str, dict[str, Any]] = {}

    for index, item in enumerate(quantities):
        if not isinstance(item, dict):
            errors.append(f"工程量结果[{index}] 不是对象")
            continue
        item_id = item.get("成果项编号", f"index-{index}")
        result_value = item.get("结果", {})
        scope = item.get("范围", {})
        checks = item.get("专业检查", {})
        if not isinstance(result_value, dict) or not isinstance(scope, dict):
            errors.append(f"{item_id} 缺少结果或范围对象")
            continue
        if not isinstance(checks, dict):
            errors.append(f"{item_id}.专业检查 必须是对象")
            checks = {}

        name = str(result_value.get("名称", ""))
        numeric = as_number(result_value.get("数值"))
        status = item.get("状态")
        linked_unresolved = item.get("未决事项", [])
        if not isinstance(linked_unresolved, list):
            errors.append(f"{item_id}.未决事项 必须是数组")
            linked_unresolved = []
        missing_ids = [value for value in linked_unresolved if value not in unresolved_ids]
        if missing_ids:
            errors.append(f"{item_id} 引用了不存在的未决事项: {missing_ids}")

        for word in FORBIDDEN_COMMERCIAL_WORDS:
            if word in name:
                errors.append(f"{item_id} 含越界商业结果“{word}”")
        if status == "部分计算":
            if _minimum.unqualified_completeness_words(name):
                errors.append(f"{item_id} 为部分计算但名称暗示完整性")
            if not checks.get("完整性边界"):
                errors.append(f"{item_id} 为部分计算但缺少专业检查.完整性边界")

        for rule_id in item.get("规则", []) if isinstance(item.get("规则"), list) else []:
            rule = rule_map.get(rule_id)
            if rule is None:
                errors.append(f"{item_id} 引用了不存在的规则 {rule_id}")
            elif status in ("已计算", "部分计算") and rule.get("本次判定") not in ("采用", "试用"):
                errors.append(f"{item_id} 引用的规则 {rule_id} 本次既未采用也未试用")

        dn_method = str(checks.get("规格赋值方式", ""))
        if any(word in dn_method for word in FORBIDDEN_DN_METHODS):
            errors.append(f"{item_id} 使用了禁止的 DN/规格赋值方式: {dn_method}")
        if checks.get("二维符号作为竖向长度") is True:
            errors.append(f"{item_id} 将二维符号作为竖向长度")
        if checks.get("候选断口计入") is True:
            errors.append(f"{item_id} 将候选断口计入工程量")

        role = str(checks.get("计量对象", ""))
        # An explicit exclusion in a label is not a measured vertical quantity.
        # A structured vertical role or another affirmative mention still needs evidence.
        measured_name = re.sub(r"(?:不含|不包括|未计入|不计入)\s*竖向", "", name)
        if numeric is not None and ("竖向" in measured_name or role == "竖向管段"):
            vertical_method = checks.get("竖向证据方式")
            if vertical_method not in VALID_VERTICAL_METHODS:
                errors.append(f"{item_id} 的竖向数值缺少有效高度证据方式")
        if numeric is not None and role in ("管件", "在线阀门及附件"):
            if checks.get("对象身份闭合") is not True:
                errors.append(f"{item_id} 的计数对象身份未闭合")
        if numeric is not None and role == "管道支吊架" and not checks.get("支吊架规则来源"):
            errors.append(f"{item_id} 的支吊架数值缺少规则来源")
        if numeric is not None and role == "管道保温":
            required = ("计量口径", "外径来源", "厚度来源", "长度来源")
            missing = [key for key in required if not checks.get(key)]
            if missing:
                errors.append(f"{item_id} 的保温数值缺少: {missing}")

        calculation_id = checks.get("计算证据编号")
        if isinstance(calculation_id, str):
            system = scope.get("系统")
            if not isinstance(system, str) or not system:
                errors.append(f"{item_id} 有计算证据编号但缺少系统")
            elif numeric is None:
                errors.append(f"{item_id} 有计算证据编号但没有数值")
            elif system in calculation_links:
                errors.append(f"计算证据中的系统结果重复: {system}")
            else:
                calculation_links[system] = {
                    "calculation_id": calculation_id,
                    "value": numeric,
                    "record_count": checks.get("对象数"),
                    "unit": item.get("单位"),
                    "item_id": item_id,
                }

    if args.calculation:
        try:
            calculation = load_json(args.calculation.resolve())
        except ValueError as exc:
            errors.append(str(exc))
            calculation = {}
        if calculation.get("status") != "CALCULATED":
            errors.append("中心线计算证据状态不是 CALCULATED")
        calc_id = calculation.get("calculation_id")
        calc_quantity = calculation.get("quantity", {})
        by_system = calc_quantity.get("by_system", {}) if isinstance(calc_quantity, dict) else {}
        if not isinstance(by_system, dict):
            errors.append("中心线计算证据缺少 quantity.by_system")
            by_system = {}
        if set(calculation_links) != set(by_system):
            errors.append(
                f"成果系统与计算证据不一致: 成果={sorted(calculation_links)}, 计算={sorted(by_system)}"
            )
        for system, expected in by_system.items():
            linked = calculation_links.get(system)
            if linked is None or not isinstance(expected, dict):
                continue
            if linked["calculation_id"] != calc_id:
                errors.append(f"{linked['item_id']} 的计算证据编号与计算文件不一致")
            expected_value = as_number(expected.get("value"))
            if expected_value is None or not math.isclose(linked["value"], expected_value, rel_tol=0, abs_tol=1e-9):
                errors.append(f"{system} 的成果值与计算证据不一致")
            if linked["record_count"] != expected.get("record_count"):
                errors.append(f"{system} 的对象数与计算证据不一致")
            if linked["unit"] != calc_quantity.get("unit"):
                errors.append(f"{system} 的单位与计算证据不一致")

    inference_path = None
    if args.inference:
        inference_path = args.inference.resolve()
        try:
            inference = load_json(inference_path)
        except ValueError as exc:
            errors.append(str(exc))
            inference = {}
        inference_version = inference.get("evidence_version")
        if inference_version not in (
            "qto-pipe-network-inference-evidence/1.0",
            "qto-pipe-network-inference-evidence/1.1",
            "qto-pipe-network-inference-evidence/1.2",
        ):
            errors.append("管网推算证据版本无效")
        elif inference_version != "qto-pipe-network-inference-evidence/1.2":
            errors.append("管网推算证据仍为旧版，缺少对象审计与跨工作包交接门")
        metrics = inference.get("metrics", {})
        if not isinstance(metrics, dict):
            errors.append("管网推算证据缺少 metrics")
            metrics = {}
        delta = as_number(metrics.get("length_conservation_delta"))
        if delta is None or not math.isclose(delta, 0, rel_tol=0, abs_tol=1e-9):
            errors.append("管网推算证据分层长度不守恒")
        if metrics.get("virtual_bridge_quantity_included") != 0:
            errors.append("虚拟拓扑桥被计入工程量")
        for bridge in inference.get("topology_bridges", []):
            if not isinstance(bridge, dict) or bridge.get("quantity_included") is not False:
                errors.append("至少一个虚拟拓扑桥未明确排除工程量")
        for segment in inference.get("segments", []):
            if not isinstance(segment, dict):
                errors.append("管网推算 segments 含非对象项")
                continue
            if segment.get("quantity_level") in ("规则推算量", "方案暂估量") and not segment.get("method"):
                errors.append(f"{segment.get('segment_id')} 推算规格缺少方法")
        if inference_version in (
            "qto-pipe-network-inference-evidence/1.1",
            "qto-pipe-network-inference-evidence/1.2",
        ):
            bridges = inference.get("topology_bridges", [])
            bridge_scenarios = inference.get("bridge_scenarios", [])
            if metrics.get("topology_bridge_count", 0) and not bridge_scenarios:
                errors.append("存在虚拟拓扑桥但缺少断口数量方案")
            all_bridge_scenario = next(
                (
                    item
                    for item in bridge_scenarios
                    if isinstance(item, dict) and item.get("scenario_id") == "SC-GAP-ALL-UNIQUE"
                ),
                None,
            )
            if bridges and all_bridge_scenario is None:
                errors.append("缺少全部唯一虚拟桥补齐上界方案")
            elif all_bridge_scenario is not None:
                expected_increment = sum(
                    as_number(item.get("virtual_length")) or 0
                    for item in bridges
                    if isinstance(item, dict)
                )
                actual_increment = as_number(all_bridge_scenario.get("incremental_length"))
                if actual_increment is None or not math.isclose(
                    actual_increment, expected_increment, rel_tol=0, abs_tol=1e-9
                ):
                    errors.append("全部唯一虚拟桥方案与桥明细不守恒")
            valves = inference.get("valves", [])
            valve_summary = inference.get("valve_summary", [])
            if metrics.get("valve_count", 0) and not valve_summary:
                errors.append("存在阀门或在线附件实例但缺少用户可汇总分组")
            if len(valves) != metrics.get("valve_count"):
                errors.append("阀门明细数与 metrics.valve_count 不一致")
            if any(
                isinstance(item, dict)
                and item.get("valve_type") == "未分类"
                and not item.get("candidate_types")
                for item in valves
            ):
                warnings.append("仍有阀门实例既未分类也没有形成常规候选类型")
            fitting_candidates = inference.get("fitting_candidates", [])
            if len(fitting_candidates) != metrics.get("fitting_candidate_count"):
                errors.append("管件候选明细数与 metrics.fitting_candidate_count 不一致")

        if inference_version == "qto-pipe-network-inference-evidence/1.2":
            object_selection = inference.get("object_selection")
            if not isinstance(object_selection, dict):
                errors.append("缺少 object_selection 对象审计结果")
            else:
                selection_counts = [
                    object_selection.get("candidate_count"),
                    object_selection.get("included_count"),
                    object_selection.get("excluded_count"),
                    object_selection.get("unresolved_count"),
                ]
                if any(isinstance(value, bool) or not isinstance(value, int) for value in selection_counts):
                    errors.append("object_selection 数量字段无效")
                elif selection_counts[0] != sum(selection_counts[1:]):
                    errors.append("object_selection 数量不守恒")
                if object_selection.get("included_count") != metrics.get("segment_count"):
                    errors.append("对象审计纳入数与推算管段数不一致")
                if object_selection.get("status") not in ("READY", "REVIEW_REQUIRED"):
                    errors.append("对象审计状态无效或未提供")
                if object_selection.get("unresolved_count", 0):
                    warnings.append("对象审计仍有未决候选，只能采用已审计子集")

            endpoint_handoff = inference.get("endpoint_handoff")
            terminals = inference.get("terminals", [])
            if not isinstance(endpoint_handoff, dict):
                errors.append("缺少 endpoint_handoff 跨工作包对账")
            else:
                handoff_counts = [
                    endpoint_handoff.get("source_endpoint_port_count", endpoint_handoff.get("source_instance_count")),
                    endpoint_handoff.get("delivered_count"),
                    endpoint_handoff.get("not_applicable_count"),
                    endpoint_handoff.get("unresolved_count"),
                ]
                if any(isinstance(value, bool) or not isinstance(value, int) for value in handoff_counts):
                    errors.append("endpoint_handoff 数量字段无效")
                elif handoff_counts[0] != sum(handoff_counts[1:]):
                    errors.append("endpoint_handoff 来源、交付、不适用和未决数量不守恒")
                if endpoint_handoff.get("delivered_count") != len(terminals):
                    errors.append("endpoint_handoff 交付数与 terminals 明细数不一致")
                if endpoint_handoff.get("unresolved_count", 0):
                    warnings.append("点位到管网仍有未决端点，主支管与末端反推只能部分采用")

            bridges = inference.get("topology_bridges", [])
            classified_count = sum(
                1
                for bridge in bridges
                if isinstance(bridge, dict) and bridge.get("classification") != "未分类断口候选"
            )
            if classified_count != metrics.get("classified_gap_count"):
                errors.append("断口分类覆盖数与 metrics.classified_gap_count 不一致")
            if bridges and classified_count != len(bridges):
                errors.append("仍有唯一断口候选未形成项目分类或自动候选分类")

            dn_candidates = inference.get("dn_match_candidates", [])
            if not isinstance(dn_candidates, list):
                errors.append("dn_match_candidates 必须是数组")
                dn_candidates = []
            dispositions = {"seed": 0, "scenario": 0, "excluded": 0}
            for item in dn_candidates:
                if not isinstance(item, dict) or item.get("disposition") not in dispositions:
                    errors.append("DN 匹配候选存在无效处置")
                    continue
                dispositions[item["disposition"]] += 1
            if len(dn_candidates) != metrics.get("dn_match_candidate_count"):
                errors.append("DN 匹配候选明细数与 metrics 不一致")
            for disposition, metric_name in (
                ("seed", "dn_match_seed_count"),
                ("scenario", "dn_match_scenario_count"),
                ("excluded", "dn_match_excluded_count"),
            ):
                if dispositions[disposition] != metrics.get(metric_name):
                    errors.append(f"DN 匹配候选 {disposition} 数量与 metrics 不一致")
            if dn_candidates and not isinstance(inference.get("dn_match_policy"), dict):
                errors.append("存在 DN 匹配候选但缺少匹配策略与阈值证据")

            roots = inference.get("roots", [])
            if terminals and not roots:
                warnings.append("已有末端但没有根节点候选，主支管角色尚不能形成")
            if any(isinstance(item, dict) and item.get("port_dn") is None for item in terminals):
                warnings.append("仍有末端端口未形成直接或常规规则 DN 候选")
            if terminals and roots:
                decided_roles = [
                    item
                    for item in inference.get("segments", [])
                    if isinstance(item, dict) and item.get("role") not in (None, "未定", "环网")
                ]
                if not decided_roles:
                    warnings.append("已有根节点和末端，但没有形成任何主支管或末端连接角色")

            if inference.get("business_adoption_allowed") is not False:
                errors.append("推算证据必须明确 business_adoption_allowed=false")
            if inference.get("business_result_status") not in {
                "CANDIDATE_READY_FOR_REVIEW",
                "PARTIAL_BLOCKED",
                "PARTIAL_CALCULATED",
            }:
                errors.append("缺少有效 business_result_status")
            else:
                warnings.append("当前成果是候选工作量，不是已核准正式量")

            readiness = inference.get("workflow_readiness")
            if not isinstance(readiness, dict):
                errors.append("缺少 workflow_readiness")
            elif readiness.get("business_correctness_proven") is not False:
                errors.append("workflow_readiness 必须明确不以协议验证替代业务正确性")
            elif readiness.get("status") != "READY":
                warnings.append("推算链仍有未闭合审计或交接，只能形成部分工作量")

    if args.inference and isinstance(inference.get("route_preparation"), dict):
        prep = inference["route_preparation"]
        if not math.isclose(as_number(prep.get("length_delta")) or 0, 0, abs_tol=1e-7):
            errors.append("原工程路线分段前后长度不守恒")
        parent_map = prep.get("parent_map", {})
        children = [sid for values in parent_map.values() for sid in values]
        actual = [r.get("segment_id") for r in inference.get("segments", [])]
        if len(children) != len(set(children)) or set(children) != set(actual):
            errors.append("原对象与派生管段映射不完整或重复")
        source_lengths = prep.get("source_lengths", {})
        edge_lengths = {r["segment_id"]: r["length"] for r in inference.get("segments", [])}
        for parent, keys in parent_map.items():
            if parent not in source_lengths or any(k not in edge_lengths for k in keys) or not math.isclose(sum(edge_lengths.get(k, 0) for k in keys), source_lengths.get(parent, -1), abs_tol=1e-7, rel_tol=1e-10):
                errors.append("派生管段与原路线长度不符: " + parent)
        if prep.get("issues"):
            warnings.append("路径识别或跨图关系尚有未决，详见自动生成复核摘要")

    status = "FAIL" if errors else ("PASS_WITH_LIMITATIONS" if warnings else "PASS")
    output = {
        "status": status,
        "result": str(args.result.resolve()),
        "calculation": str(args.calculation.resolve()) if args.calculation else None,
        "inference": str(inference_path) if inference_path else None,
        "quantity_count": len(quantities),
        "rule_count": len(rules),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
