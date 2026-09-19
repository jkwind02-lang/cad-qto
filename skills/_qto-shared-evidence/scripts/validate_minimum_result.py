#!/usr/bin/env python3
"""Validate normalized samples of the minimum QTO result contract."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_recognition_basis import validate_working_basis
from recompute_qto_dependencies import check_result_dependencies


TOP_REQUIRED = (
    "契约版本",
    "任务范围",
    "资料选择",
    "证据目录",
    "本次规则清单",
    "工程量结果",
    "整体未决事项",
)
SCOPE_REQUIRED = (
    "业务专业",
    "主要物理对象",
    "系统",
    "楼栋",
    "楼层或区域",
    "图纸类型",
    "图纸版本",
    "输出维度",
)
ITEM_SCOPE_REQUIRED = (
    "业务专业",
    "主要对象",
    "系统",
    "楼栋",
    "楼层或区域",
    "规格",
    "输出维度",
)
RESULT_REQUIRED = (
    "名称",
    "数值",
    "数值状态",
    "计算式或说明",
    "精度",
)
ITEM_REQUIRED = (
    "成果项编号",
    "范围",
    "结果",
    "单位",
    "来源",
    "规则",
    "状态",
    "未决事项",
    "试运行结果",
    "零值复核",
)
LAYER_REQUIRED = (
    "数量层级",
    "计算完整性",
    "置信等级",
    "确认状态",
    "发布状态",
    "假定编号",
    "替代方案",
    "依赖成果项",
    "进入工作量",
    "进入正式量",
    "重算范围",
)
VALID_RESULT_STATES = {"已计算", "部分计算", "待确认", "证据不足", "不适用"}
VALID_VALUE_STATES = {"已计算", "未确定", "不适用"}
VALID_RULE_DECISIONS = {"采用", "试用", "不适用", "证据不足"}
VALID_RULE_MATURITY = {"候选", "试运行", "正式", "停用"}
VALID_UNRESOLVED_TYPES = {
    "缺资料",
    "范围未定",
    "来源冲突",
    "规则冲突",
    "质量问题",
    "需用户确认",
}
VALID_UNRESOLVED_ACTIONS = {
    "停止受影响计算",
    "保留已证实部分",
    "等待裁决",
    "形成规则推算",
    "形成方案暂估",
    "保留多方案",
}
VALID_QUANTITY_LEVELS = {"确认量", "规则推算量", "方案暂估量", "未决量", "不适用"}
VALID_COMPLETENESS = {"完整", "部分", "无法形成"}
VALID_CONFIDENCE = {"高", "中", "低", "不适用"}
VALID_CONFIRMATION = {"无需确认", "待确认", "已确认", "已否决", "不适用"}
VALID_RELEASE = {"内部工作", "复核", "正式"}
VALID_ASSUMPTION_TYPES = {"事实推定", "规则选择", "方案参数", "跨专业接口", "用户裁决"}
VALID_ASSUMPTION_STATES = {"自动接受", "待确认", "已确认", "已否决", "不适用"}
PLACEHOLDER_VALUES = {"待确认", "未知", "未确定", "证据不足", "无", "n/a", "-"}
SUSPICIOUS_UNITS = {
    "待确认",
    "未知",
    "未确定",
    "证据不足",
    "混合",
    "混合单位",
    "综合",
    "综合单位",
    "多单位",
    "-",
}
UNIT_JOINERS = {"+", "＋", "、", ",", "，", "|", ";", "；"}
# Summation does not assert full coverage. Scope and status are checked separately.
PARTIAL_COMPLETE_WORDS = {"总量", "完整", "全部", "全量"}
NEGATED_COMPLETENESS_PREFIXES = {
    "不代表",
    "不构成",
    "不等于",
    "不是",
    "并非",
    "不得称为",
    "不可称为",
    "尚非",
    "尚未形成",
    "未形成",
    "不含",
}


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def numeric(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"非标准 JSON 数值常量: {value}")


def placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() in PLACEHOLDER_VALUES


def schema_error_path(error: Any) -> str:
    parts = ["$"]
    for item in error.absolute_path:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}")
    return "".join(parts)


def unqualified_completeness_words(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    remaining = value
    for word in PARTIAL_COMPLETE_WORDS:
        for prefix in NEGATED_COMPLETENESS_PREFIXES:
            remaining = remaining.replace(f"{prefix}{word}", "")
        if word == "完整":
            remaining = remaining.replace("不完整", "")
    return sorted(word for word in PARTIAL_COMPLETE_WORDS if word in remaining)


def unique_map(
    items: Any,
    key: str,
    label: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        errors.append(f"{label}必须是数组")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}]必须是对象")
            continue
        identifier = item.get(key)
        if not nonempty(identifier):
            errors.append(f"{label}[{index}]缺少{key}")
            continue
        if identifier in result:
            errors.append(f"{label}存在重复编号: {identifier}")
        result[identifier] = item
    return result


def validate_document(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["顶层必须是对象"]
    try:
        if "recognition_basis" in data and data["recognition_basis"] is None:
            raise ValueError("recognition_basis 存在时必须是模型工作识别对象，不得为null")
        validate_working_basis(data.get("recognition_basis"))
    except ValueError as exc:
        errors.append(str(exc))

    for field in TOP_REQUIRED:
        if field not in data:
            errors.append(f"缺少顶层字段: {field}")
    if data.get("契约版本") != "qto-result-minimum/1.0":
        errors.append("契约版本必须为 qto-result-minimum/1.0")

    scope = data.get("任务范围")
    if not isinstance(scope, dict):
        errors.append("任务范围必须是对象")
    else:
        for field in SCOPE_REQUIRED:
            if not nonempty(scope.get(field)):
                errors.append(f"任务范围缺少非空字段: {field}")

    sources = unique_map(data.get("资料选择"), "资料编号", "资料选择", errors)
    evidence = unique_map(data.get("证据目录"), "证据编号", "证据目录", errors)
    rules = unique_map(data.get("本次规则清单"), "规则编号", "本次规则清单", errors)
    unresolved = unique_map(
        data.get("整体未决事项"), "事项编号", "整体未决事项", errors
    )
    results = unique_map(data.get("工程量结果"), "成果项编号", "工程量结果", errors)
    assumptions_raw = data.get("假定与裁决清单", [])
    assumptions = unique_map(
        assumptions_raw, "假定编号", "假定与裁决清单", errors
    )
    inference_enabled = "推算协议版本" in data or "假定与裁决清单" in data
    if inference_enabled and data.get("推算协议版本") != "qto-inference/1.0":
        errors.append("使用推算分层时推算协议版本必须为 qto-inference/1.0")
    if isinstance(data.get("工程量结果"), list) and not data.get("工程量结果"):
        errors.append("工程量结果至少需要一项")

    selected_sources = {
        identifier
        for identifier, item in sources.items()
        if item.get("选择结论") == "已选择"
    }
    valid_selection_states = {"已选择", "已排除", "暂不确定"}
    for identifier, item in sources.items():
        if item.get("选择结论") not in valid_selection_states:
            errors.append(f"{identifier}的选择结论非法")
        for field in (
            "路径",
            "资料类型",
            "版本身份",
            "原因",
            "拟提取中性事实",
            "未使用影响",
        ):
            if not nonempty(item.get(field)):
                errors.append(f"{identifier}缺少非空字段: {field}")

    for identifier, item in evidence.items():
        source_id = item.get("资料编号")
        if source_id not in sources:
            errors.append(f"{identifier}引用不存在的资料: {source_id}")
        for field in (
            "来源类型",
            "中性事实",
            "定位",
            "来源身份",
            "提取方式",
            "质量状态",
        ):
            if not nonempty(item.get(field)):
                errors.append(f"{identifier}缺少非空字段: {field}")
        for field in ("定位", "来源身份"):
            if placeholder(item.get(field)):
                errors.append(f"{identifier}.{field}使用占位值，不能作为可追溯证据")

    for identifier, item in rules.items():
        decision = item.get("本次判定")
        maturity = item.get("成熟状态")
        if decision not in VALID_RULE_DECISIONS:
            errors.append(f"{identifier}的本次判定非法")
        if maturity not in VALID_RULE_MATURITY:
            errors.append(f"{identifier}的成熟状态非法")
        if maturity == "停用" and decision in {"采用", "试用"}:
            errors.append(f"{identifier}为停用规则，不得采用或试用")
        if maturity == "候选" and decision == "采用" and not nonempty(
            item.get("项目采用依据")
        ):
            errors.append(f"{identifier}为候选规则，项目采用时必须记录项目采用依据")
        for field in ("规则名称", "规则来源", "执行结果", "证据位置"):
            if not nonempty(item.get(field)):
                errors.append(f"{identifier}缺少非空字段: {field}")

    for identifier, item in results.items():
        for field in ITEM_REQUIRED:
            if field not in item:
                errors.append(f"{identifier}缺少字段: {field}")

        item_scope = item.get("范围")
        if not isinstance(item_scope, dict):
            errors.append(f"{identifier}.范围必须是对象")
        else:
            for field in ITEM_SCOPE_REQUIRED:
                if not nonempty(item_scope.get(field)):
                    errors.append(f"{identifier}.范围缺少非空字段: {field}")

        value_block = item.get("结果")
        if not isinstance(value_block, dict):
            errors.append(f"{identifier}.结果必须是对象")
            continue
        for field in RESULT_REQUIRED:
            if field not in value_block:
                errors.append(f"{identifier}.结果缺少字段: {field}")
        for field in ("名称", "计算式或说明", "精度"):
            if not nonempty(value_block.get(field)):
                errors.append(f"{identifier}.结果缺少非空字段: {field}")

        value = value_block.get("数值")
        value_state = value_block.get("数值状态")
        status = item.get("状态")
        source_refs = item.get("来源")
        rule_refs = item.get("规则")
        unresolved_refs = item.get("未决事项")
        unit = item.get("单位")
        layer = item.get("成果分层")
        if data.get("recognition_basis") is not None:
            if not isinstance(layer, dict) or layer.get("进入正式量") is not False or layer.get("发布状态") == "正式":
                errors.append(f"{identifier} 模型工作识别成果必须分层且不得作为正式量")
        quantity_level = None
        if layer is not None:
            if not isinstance(layer, dict):
                errors.append(f"{identifier}.成果分层必须是对象")
                layer = {}
            if not inference_enabled:
                errors.append(f"{identifier}使用成果分层但缺少顶层推算协议")
            for field in LAYER_REQUIRED:
                if field not in layer:
                    errors.append(f"{identifier}.成果分层缺少字段: {field}")
            quantity_level = layer.get("数量层级")
            if quantity_level not in VALID_QUANTITY_LEVELS:
                errors.append(f"{identifier}.成果分层.数量层级非法")
            if layer.get("计算完整性") not in VALID_COMPLETENESS:
                errors.append(f"{identifier}.成果分层.计算完整性非法")
            if layer.get("置信等级") not in VALID_CONFIDENCE:
                errors.append(f"{identifier}.成果分层.置信等级非法")
            if layer.get("确认状态") not in VALID_CONFIRMATION:
                errors.append(f"{identifier}.成果分层.确认状态非法")
            if layer.get("发布状态") not in VALID_RELEASE:
                errors.append(f"{identifier}.成果分层.发布状态非法")
            for field in ("假定编号", "替代方案", "依赖成果项"):
                if not isinstance(layer.get(field), list):
                    errors.append(f"{identifier}.成果分层.{field}必须是数组")
            for field in ("进入工作量", "进入正式量"):
                if not isinstance(layer.get(field), bool):
                    errors.append(f"{identifier}.成果分层.{field}必须是布尔值")
            if not nonempty(layer.get("重算范围")):
                errors.append(f"{identifier}.成果分层.重算范围不能为空")
            for assumption_id in layer.get("假定编号", []) if isinstance(layer.get("假定编号"), list) else []:
                if assumption_id not in assumptions:
                    errors.append(f"{identifier}引用不存在的假定: {assumption_id}")
            for dependency_id in layer.get("依赖成果项", []) if isinstance(layer.get("依赖成果项"), list) else []:
                if dependency_id not in results:
                    errors.append(f"{identifier}依赖不存在的成果项: {dependency_id}")
            if quantity_level in {"规则推算量", "方案暂估量"} and not layer.get("假定编号"):
                errors.append(f"{identifier}{quantity_level}必须引用假定编号")
            if quantity_level in {"未决量", "不适用"} and (
                layer.get("进入工作量") is True or layer.get("进入正式量") is True
            ):
                errors.append(f"{identifier}{quantity_level}不得进入工作量或正式量")
            if layer.get("进入正式量") is True:
                if layer.get("发布状态") != "正式":
                    errors.append(f"{identifier}进入正式量但发布状态不是正式")
                if layer.get("确认状态") in {"待确认", "已否决", "不适用"}:
                    errors.append(f"{identifier}确认状态不允许进入正式量")
                if quantity_level not in {"确认量"}:
                    errors.append(f"{identifier}非确认量不得直接进入正式量")

        if status not in VALID_RESULT_STATES:
            errors.append(f"{identifier}的结果状态非法")
        if value_state not in VALID_VALUE_STATES:
            errors.append(f"{identifier}的数值状态非法")
        if not nonempty(unit):
            errors.append(f"{identifier}缺少明确单位")
        elif status in {"已计算", "部分计算"} and unit.strip().casefold() in SUSPICIOUS_UNITS:
            errors.append(f"{identifier}使用了不可核对的混合单位: {unit}")
        elif status in {"已计算", "部分计算"} and any(
            joiner in unit for joiner in UNIT_JOINERS
        ):
            errors.append(f"{identifier}把多个单位拼接为一个字段: {unit}")

        if not isinstance(source_refs, list):
            errors.append(f"{identifier}.来源必须是数组")
            source_refs = []
        if not isinstance(rule_refs, list):
            errors.append(f"{identifier}.规则必须是数组")
            rule_refs = []
        if not isinstance(unresolved_refs, list):
            errors.append(f"{identifier}.未决事项必须是数组")
            unresolved_refs = []
        if not isinstance(item.get("试运行结果"), bool):
            errors.append(f"{identifier}.试运行结果必须是布尔值")

        if value_state == "已计算" and not numeric(value):
            errors.append(f"{identifier}数值状态为已计算但数值不是数字")
        if value_state == "未确定" and value is not None:
            errors.append(f"{identifier}数值未确定时必须使用 null，禁止用0或经验值")
        if value_state == "不适用" and value is not None:
            errors.append(f"{identifier}不适用时数值必须使用 null")
        if numeric(value) and not source_refs:
            errors.append(f"{identifier}有数值但没有成果项级来源")

        for evidence_id in source_refs:
            if evidence_id not in evidence:
                errors.append(f"{identifier}引用不存在的证据: {evidence_id}")
            else:
                source_id = evidence[evidence_id].get("资料编号")
                if source_id not in selected_sources:
                    errors.append(
                        f"{identifier}引用的证据{evidence_id}不属于已选择资料"
                    )
        for rule_id in rule_refs:
            if rule_id not in rules:
                errors.append(f"{identifier}引用不存在的规则: {rule_id}")
        for unresolved_id in unresolved_refs:
            if unresolved_id not in unresolved:
                errors.append(f"{identifier}引用不存在的未决事项: {unresolved_id}")

        adopted_rules = [
            rules[rule_id]
            for rule_id in rule_refs
            if rule_id in rules and rules[rule_id].get("本次判定") == "采用"
        ]
        usable_rules = [
            rules[rule_id]
            for rule_id in rule_refs
            if rule_id in rules and rules[rule_id].get("本次判定") in {"采用", "试用"}
        ]
        if status in {"已计算", "部分计算"} and not usable_rules:
            errors.append(f"{identifier}有数值结果但没有已采用或试用规则")
        if status in {"已计算", "部分计算"}:
            unusable = [
                rule_id
                for rule_id in rule_refs
                if rule_id in rules
                and rules[rule_id].get("本次判定") not in {"采用", "试用"}
            ]
            if unusable:
                errors.append(
                    f"{identifier}的数值结果引用了不可用规则: {', '.join(unusable)}"
                )
        if quantity_level == "确认量":
            trial_rule_ids = [
                rule_id
                for rule_id in rule_refs
                if rule_id in rules and rules[rule_id].get("本次判定") == "试用"
            ]
            if trial_rule_ids:
                errors.append(f"{identifier}确认量不得引用试用规则: {', '.join(trial_rule_ids)}")

        if status == "已计算":
            if not numeric(value) or value_state != "已计算":
                errors.append(f"{identifier}已计算状态必须有数字")
            if not source_refs or not rule_refs:
                errors.append(f"{identifier}已计算状态必须有来源和规则")
            if unresolved_refs:
                errors.append(f"{identifier}已计算状态不得有关联未决事项")
        elif status == "部分计算":
            if layer is not None and layer.get("计算完整性") != "部分":
                errors.append(f"{identifier}部分计算与成果分层.计算完整性不一致，应为部分")
            if not numeric(value) or value_state != "已计算":
                errors.append(f"{identifier}部分计算必须保留已证实部分数字")
            if not unresolved_refs:
                errors.append(f"{identifier}部分计算必须关联完整性未决事项")
            name_words = unqualified_completeness_words(value_block.get("名称"))
            note_words = unqualified_completeness_words(
                value_block.get("计算式或说明")
            )
            if name_words:
                errors.append(
                    f"{identifier}部分计算名称不得使用完整性措辞: {', '.join(name_words)}"
                )
            if note_words:
                errors.append(
                    f"{identifier}部分计算说明不得宣称范围完整: {', '.join(note_words)}"
                )
        elif status in {"待确认", "证据不足"}:
            if value is not None or value_state != "未确定":
                errors.append(f"{identifier}{status}时结果必须为未确定/null")
            if not unresolved_refs:
                errors.append(f"{identifier}{status}时必须关联未决事项")
        elif status == "不适用":
            if value is not None or value_state != "不适用":
                errors.append(f"{identifier}不适用时数值必须为null")

        zero_review = item.get("零值复核")
        if numeric(value) and value == 0:
            if not isinstance(zero_review, dict):
                errors.append(f"{identifier}为零值但没有结构化零值复核记录")
            else:
                if zero_review.get("复核结论") != "已确认零值":
                    errors.append(f"{identifier}的零值复核结论不明确")
                review_sources = zero_review.get("来源")
                review_rules = zero_review.get("规则")
                if not isinstance(review_sources, list) or not review_sources:
                    errors.append(f"{identifier}的零值复核缺少来源")
                elif any(source_id not in source_refs for source_id in review_sources):
                    errors.append(f"{identifier}的零值复核引用了成果项外来源")
                if not isinstance(review_rules, list) or not review_rules:
                    errors.append(f"{identifier}的零值复核缺少规则")
                elif any(rule_id not in rule_refs for rule_id in review_rules):
                    errors.append(f"{identifier}的零值复核引用了成果项外规则")
                for field in ("范围覆盖", "说明"):
                    if not nonempty(zero_review.get(field)):
                        errors.append(f"{identifier}的零值复核缺少非空字段: {field}")
                    elif placeholder(zero_review.get(field)):
                        errors.append(f"{identifier}的零值复核字段{field}使用占位值")
        elif zero_review is not None and not isinstance(zero_review, dict):
            errors.append(f"{identifier}.零值复核必须为对象或null")

        adopted_trial = any(
            rule.get("成熟状态") in {"候选", "试运行"} for rule in usable_rules
        )
        if adopted_trial and item.get("试运行结果") is not True:
            errors.append(f"{identifier}采用试运行规则但未标识试运行结果")

    for identifier, item in assumptions.items():
        if item.get("类型") not in VALID_ASSUMPTION_TYPES:
            errors.append(f"{identifier}的假定类型非法")
        if item.get("置信等级") not in VALID_CONFIDENCE:
            errors.append(f"{identifier}的置信等级非法")
        if item.get("状态") not in VALID_ASSUMPTION_STATES:
            errors.append(f"{identifier}的假定状态非法")
        for field in ("假定内容", "推荐方案", "影响说明", "所需确认方", "重算范围"):
            if not nonempty(item.get(field)):
                errors.append(f"{identifier}缺少非空字段: {field}")
        for field in ("依据", "使用规则", "替代方案", "影响成果项"):
            if not isinstance(item.get(field), list):
                errors.append(f"{identifier}.{field}必须是数组")
        if isinstance(item.get("依据"), list) and not item.get("依据"):
            errors.append(f"{identifier}.依据不得为空")
        for rule_id in item.get("使用规则", []) if isinstance(item.get("使用规则"), list) else []:
            if rule_id not in rules:
                errors.append(f"{identifier}引用不存在的规则: {rule_id}")
        for result_id in item.get("影响成果项", []) if isinstance(item.get("影响成果项"), list) else []:
            if result_id not in results:
                errors.append(f"{identifier}影响不存在的成果项: {result_id}")
            else:
                result_layer = results[result_id].get("成果分层", {})
                if identifier not in result_layer.get("假定编号", []):
                    errors.append(f"{identifier}与{result_id}未建立双向假定引用")

    for identifier, item in results.items():
        layer = item.get("成果分层")
        if not isinstance(layer, dict) or layer.get("进入正式量") is not True:
            continue
        for rule_id in item.get("规则", []):
            if rule_id in rules and rules[rule_id].get("本次判定") != "采用":
                errors.append(f"{identifier}进入正式量但规则{rule_id}未正式采用")
        for assumption_id in layer.get("假定编号", []):
            if assumption_id in assumptions and assumptions[assumption_id].get("状态") not in {"自动接受", "已确认"}:
                errors.append(f"{identifier}进入正式量但假定{assumption_id}尚未确认")

    for identifier, item in unresolved.items():
        if item.get("类型") not in VALID_UNRESOLVED_TYPES:
            errors.append(f"{identifier}的未决事项类型非法")
        if item.get("当前处理") not in VALID_UNRESOLVED_ACTIONS:
            errors.append(f"{identifier}的当前处理非法")
        linked = item.get("关联成果项")
        if not isinstance(linked, list):
            errors.append(f"{identifier}.关联成果项必须是数组")
            continue
        for result_id in linked:
            if result_id not in results:
                errors.append(f"{identifier}关联不存在的成果项: {result_id}")
            elif identifier not in results[result_id].get("未决事项", []):
                errors.append(f"{identifier}与{result_id}未建立双向引用")
        for field in ("说明", "影响", "所需输入", "当前处理"):
            if not nonempty(item.get(field)):
                errors.append(f"{identifier}缺少非空字段: {field}")

    errors.extend(check_result_dependencies(data))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    schema_path = Path(__file__).resolve().parents[1] / "references" / (
        "工程量成果最小字段.schema.json"
    )
    try:
        import jsonschema
    except ImportError as exc:
        print(
            json.dumps(
                {
                    "status": "INPUT_REQUIRED",
                    "stop": True,
                    "missing_dependency": "jsonschema",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    try:
        schema = json.loads(
            schema_path.read_text(encoding="utf-8"),
            parse_constant=reject_nonstandard_constant,
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        schema_validator = jsonschema.Draft202012Validator(schema)
    except (OSError, ValueError, jsonschema.exceptions.SchemaError) as exc:
        print(
            json.dumps(
                {
                    "status": "INPUT_REQUIRED",
                    "stop": True,
                    "missing_or_invalid_contract": str(schema_path),
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    reports = []
    failed = False
    for path in args.files:
        try:
            data = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=reject_nonstandard_constant,
            )
            schema_errors = sorted(
                schema_validator.iter_errors(data),
                key=lambda error: tuple(str(item) for item in error.absolute_path),
            )
            errors = [
                f"结构约定 {schema_error_path(error)}: {error.message}"
                for error in schema_errors
            ]
            if not schema_errors:
                errors.extend(validate_document(data))
        except (OSError, ValueError) as exc:
            errors = [f"文件不可读或不是有效 JSON: {exc}"]
        status = "PASS" if not errors else "FAIL"
        failed = failed or bool(errors)
        reports.append(
            {
                "file": str(path),
                "status": status,
                "error_count": len(errors),
                "errors": errors,
            }
        )

    print(
        json.dumps(
            {
                "status": "FAIL" if failed else "PASS",
                "files_checked": len(reports),
                "reports": reports,
                "schema_validation": "JSON Schema Draft 2020-12",
                "business_correctness_proven": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
