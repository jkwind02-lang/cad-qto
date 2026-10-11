#!/usr/bin/env python3
"""Professional invariants and self-tests for qto-concrete-formwork outputs."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


RESULT_VERSION = "qto-result-minimum/1.0"
sys.dont_write_bytecode = True
VALID_RESULT_STATES = {"已计算", "部分计算", "待确认", "证据不足", "不适用"}
VALID_VALUE_STATES = {"已计算", "未确定", "不适用"}
VALID_RULE_DECISIONS = {"采用", "不适用", "证据不足"}
VALID_RULE_MATURITY = {"候选", "试运行", "正式", "停用"}
VALID_UNRESOLVED_TYPES = {"缺资料", "范围未定", "来源冲突", "规则冲突", "质量问题", "需用户确认"}
VALID_UNRESOLVED_ACTIONS = {"停止受影响计算", "保留已证实部分", "等待裁决"}



def _load_calculator():
    path = Path(__file__).with_name("calculate_concrete_formwork_pairs.py")
    spec = importlib.util.spec_from_file_location("qto_concrete_formwork_calculator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载计算模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _close(a: Any, b: Any, tolerance: float = 1e-9) -> bool:
    return isinstance(a, (int, float)) and not isinstance(a, bool) and math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tolerance)


def run_self_tests() -> list[dict[str, Any]]:
    calc = _load_calculator()
    tests: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        tests.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})

    check("normal_foundation_volume", _close(calc.rect_prism_volume(10, 5, 0.5), 25.0), "矩形基础10×5×0.5=25m³")
    column_faces = calc.contact_surface_inventory([
        {"key": f"COLUMN-C1-SIDE-{idx}", "state": "included", "area_m2": 1.2} for idx in range(1, 5)
    ])
    check("column_pair", _close(calc.rect_prism_volume(0.4, 0.4, 3.0), 0.48) and _close(column_faces["included_area_m2"], 4.8), "柱C1同一0.4×0.4×3.0m几何生成0.480m³体积和4.800m²四侧接触面")
    beam_faces = calc.contact_surface_inventory([
        {"key": "BEAM-B1-SIDE-L", "state": "included", "area_m2": 2.0},
        {"key": "BEAM-B1-SIDE-R", "state": "included", "area_m2": 2.0},
        {"key": "BEAM-B1-BOTTOM", "state": "included", "area_m2": 1.2},
        {"key": "BEAM-B1-TOP", "state": "excluded"},
    ])
    check("beam_pair", _close(calc.rect_prism_volume(4.0, 0.3, 0.5), 0.6) and _close(beam_faces["included_area_m2"], 5.2) and beam_faces["excluded"] == ["BEAM-B1-TOP"], "梁B1同一几何生成0.600m³及两侧+底5.200m²，顶面明确排除")
    slab_faces = calc.contact_surface_inventory([
        {"key": "SLAB-S1-BOTTOM", "state": "included", "area_m2": 20.0},
        {"key": "SLAB-S1-TOP", "state": "excluded"},
        {"key": "SLAB-S1-EDGE", "state": "unresolved"},
    ])
    check("slab_pair_and_unresolved_edge", _close(calc.rect_prism_volume(5.0, 4.0, 0.15), 3.0) and _close(slab_faces["included_area_m2"], 20.0) and slab_faces["unresolved"] == ["SLAB-S1-EDGE"], "板S1底模20m²可算，边模条件未决不补入")
    check("wall_opening_physical", _close(calc.wall_physical_with_opening(5, 0.2, 3, 1, 2), 2.6), "墙体物理量扣真实洞口")
    small = calc.template_opening_adjustment(0.5, 0.5, 0.2)
    equal = calc.template_opening_adjustment(0.5, 0.6, 0.2)
    large = calc.template_opening_adjustment(0.5, 0.61, 0.2)
    check("opening_below_threshold", small["deduct"] is False and _close(small["main_faces_deduct_m2"], 0), "单洞0.25m²不触发>0.3m²模板扣减")
    check("opening_equal_threshold", equal["deduct"] is False and _close(equal["single_opening_area_m2"], 0.3), "单洞等于0.3m²仍不属于>0.3m²")
    check("opening_above_threshold", large["deduct"] is True and _close(large["main_faces_deduct_m2"], 0.61), "单洞0.305m²扣两主面并另计洞侧")
    union = calc.union_area_rectangles([(0, 0, 2, 1), (1, 0, 3, 1)])
    check("intersection_union", _close(union, 3.0), "重叠支承脚印先求并集，不能标量相加为4m²")
    stair = calc.stair_composite_volume([0.24, 0.18, 0.36, 0.12])
    stair_faces = calc.contact_surface_inventory([
        {"key": "STAIR-ST1-FLIGHT-BOTTOM", "state": "included", "area_m2": 3.2},
        {"key": "STAIR-ST1-RISERS", "state": "included", "area_m2": 1.8},
        {"key": "STAIR-ST1-LANDING-BOTTOM", "state": "included", "area_m2": 2.4},
        {"key": "STAIR-ST1-BEAM-SIDES-BOTTOM", "state": "included", "area_m2": 1.0},
        {"key": "STAIR-ST1-WALL-JOINT", "state": "excluded"},
    ])
    check("stair_composite", _close(stair, 0.9) and _close(stair_faces["included_area_m2"], 8.4), "楼梯ST1子实体0.900m³与五类面台账8.400m²分开复算，不用水平投影替代")
    area, perimeter = calc.orthogonal_offset(438.9, 126.4, 0.1)
    check("orthogonal_blinding_offset", _close(area, 451.58) and _close(perimeter, 127.2), "正交带缺口轮廓外扩面积和周长")
    try:
        calc.require_number(None, "missing")
        missing_rejected = False
    except calc.CalculationError:
        missing_rejected = True
    check("missing_decisive_input", missing_rejected, "决定性尺寸缺失必须停止，不能补零")
    try:
        calc.union_area_rectangles([(0, 0, 0, 1)])
        conflict_rejected = False
    except calc.CalculationError:
        conflict_rejected = True
    check("invalid_intersection_geometry", conflict_rejected, "零宽相交对象必须拒绝")
    try:
        calc.contact_surface_inventory([
            {"key": "DUPLICATE-FACE", "state": "included", "area_m2": 1.0},
            {"key": "DUPLICATE-FACE", "state": "included", "area_m2": 1.0},
        ])
        duplicate_face_rejected = False
    except calc.CalculationError:
        duplicate_face_rejected = True
    check("duplicate_contact_face", duplicate_face_rejected, "同一接触面防重复键出现两次必须拒绝")

    generic_fixture = {
        "契约版本": RESULT_VERSION,
        "任务范围": {"业务专业": "建筑结构"},
        "资料选择": [{"资料编号": "SRC-FIXTURE", "选择结论": "已选择"}],
        "证据目录": [{"证据编号": "EV-FIXTURE", "资料编号": "SRC-FIXTURE"}],
        "本次规则清单": [{"规则编号": "R-CF-FIXTURE", "本次判定": "采用", "成熟状态": "试运行"}],
        "工程量结果": [{
            "成果项编号": "QTY-CF-COLUMN-FIXTURE",
            "结果": {"名称": "柱C1混凝土体积", "数值": 0.48, "数值状态": "已计算", "计算式或说明": "0.4×0.4×3.0"},
            "单位": "m³",
            "来源": ["EV-FIXTURE"],
            "规则": ["R-CF-FIXTURE"],
            "状态": "已计算",
            "未决事项": [],
            "试运行结果": True,
            "零值复核": None,
            "专业检查": {"几何签名": "a" * 64, "构件几何编号": "GEO-COLUMN-C1", "计量口径": "物理量"},
        }],
        "整体未决事项": [],
    }
    generic_errors, _ = validate_result(generic_fixture, None, None, None)
    check("generic_validator_accepts_column", not generic_errors, f"通用校验器接受合法柱成果；errors={generic_errors}")
    missing_source = copy.deepcopy(generic_fixture)
    missing_source["工程量结果"][0]["来源"] = []
    missing_source_errors, _ = validate_result(missing_source, None, None, None)
    check("generic_validator_rejects_missing_source", any("缺少来源证据" in error for error in missing_source_errors), "有数成果缺少来源证据必须失败")
    missing_formula = copy.deepcopy(generic_fixture)
    missing_formula["工程量结果"][0]["结果"].pop("计算式或说明")
    missing_formula_errors, _ = validate_result(missing_formula, None, None, None)
    check("generic_validator_rejects_missing_formula", any("缺少计算式或说明" in error for error in missing_formula_errors), "有数成果缺少公式或计算说明必须失败")
    evidence_free_number = copy.deepcopy(generic_fixture)
    evidence_free_number["资料选择"] = []
    evidence_free_number["证据目录"] = []
    evidence_free_number["工程量结果"][0]["来源"] = []
    evidence_free_number["工程量结果"][0]["结果"].pop("计算式或说明")
    evidence_free_errors, _ = validate_result(evidence_free_number, None, None, None)
    check(
        "generic_validator_rejects_evidence_free_number",
        any("缺少来源证据" in error for error in evidence_free_errors) and any("缺少计算式或说明" in error for error in evidence_free_errors),
        "任意有数成果都不能在资料、证据、来源和计算说明全空时通过",
    )
    unknown_status = copy.deepcopy(generic_fixture)
    unknown_status["工程量结果"][0]["状态"] = "已完成"
    unknown_status["工程量结果"][0]["结果"]["数值状态"] = "已完成"
    unknown_status_errors, _ = validate_result(unknown_status, None, None, None)
    check(
        "generic_validator_rejects_unknown_status",
        any("结果状态不在允许范围" in error for error in unknown_status_errors) and any("数值状态不在允许范围" in error for error in unknown_status_errors),
        "自造状态名不能绕过有数结果门",
    )
    mismatched_value_status = copy.deepcopy(generic_fixture)
    mismatched_value_status["工程量结果"][0]["结果"]["数值状态"] = "未确定"
    mismatched_value_status_errors, _ = validate_result(mismatched_value_status, None, None, None)
    check("generic_validator_rejects_mismatched_value_status", any("有数状态的数值状态必须为已计算" in error for error in mismatched_value_status_errors), "外层已计算与内层未确定不能并存")
    unresolved_without_basis = copy.deepcopy(generic_fixture)
    unresolved_item = unresolved_without_basis["工程量结果"][0]
    unresolved_item["状态"] = "证据不足"
    unresolved_item["结果"]["数值"] = None
    unresolved_item["结果"]["数值状态"] = "未确定"
    unresolved_item["规则"] = []
    unresolved_item["未决事项"] = []
    unresolved_basis_errors, _ = validate_result(unresolved_without_basis, None, None, None)
    check(
        "generic_validator_rejects_unresolved_without_basis",
        any("缺少规则依据" in error for error in unresolved_basis_errors) and any("未决状态缺少未决事项" in error for error in unresolved_basis_errors),
        "证据不足/null 也必须绑定规则和未决事项",
    )
    partial_without_issue = copy.deepcopy(generic_fixture)
    partial_without_issue["工程量结果"][0]["状态"] = "部分计算"
    partial_issue_errors, _ = validate_result(partial_without_issue, None, None, None)
    check("generic_validator_rejects_partial_without_issue", any("部分计算缺少未决事项" in error for error in partial_issue_errors), "部分计算必须关联影响完整性的未决事项")
    partial_overclaim = copy.deepcopy(generic_fixture)
    partial_overclaim["整体未决事项"] = [{"事项编号": "U-PARTIAL", "类型": "缺资料", "当前处理": "保留已证实部分", "关联成果项": ["QTY-CF-COLUMN-FIXTURE"]}]
    partial_overclaim["工程量结果"][0]["状态"] = "部分计算"
    partial_overclaim["工程量结果"][0]["未决事项"] = ["U-PARTIAL"]
    partial_overclaim["工程量结果"][0]["结果"]["计算式或说明"] = "完整总量=0.48m³"
    partial_overclaim_errors, _ = validate_result(partial_overclaim, None, None, None)
    check("generic_validator_rejects_partial_formula_overclaim", any("部分结果说明夸大完整性" in error for error in partial_overclaim_errors), "部分计算的说明不得宣称完整总量")
    negative_value = copy.deepcopy(generic_fixture)
    negative_value["工程量结果"][0]["结果"]["数值"] = -10.0
    negative_value_errors, _ = validate_result(negative_value, None, None, None)
    check("generic_validator_rejects_negative_quantity", any("数值不得为负数" in error for error in negative_value_errors), "工程量不得为负数")
    nan_value = copy.deepcopy(generic_fixture)
    nan_value["工程量结果"][0]["结果"]["数值"] = math.nan
    nan_value_errors, _ = validate_result(nan_value, None, None, None)
    check("generic_validator_rejects_nan_quantity", any("数值必须为有限数" in error for error in nan_value_errors), "NaN不得作为工程量")
    infinite_value = copy.deepcopy(generic_fixture)
    infinite_value["工程量结果"][0]["结果"]["数值"] = math.inf
    infinite_value_errors, _ = validate_result(infinite_value, None, None, None)
    check("generic_validator_rejects_infinite_quantity", any("数值必须为有限数" in error for error in infinite_value_errors), "Infinity不得作为工程量")
    unknown_rule_state = copy.deepcopy(generic_fixture)
    unknown_rule_state["本次规则清单"][0]["本次判定"] = "自动采用"
    unknown_rule_state["本次规则清单"][0]["成熟状态"] = "已完成"
    unknown_rule_errors, _ = validate_result(unknown_rule_state, None, None, None)
    check(
        "generic_validator_rejects_unknown_rule_state",
        any("本次判定不在允许范围" in error for error in unknown_rule_errors) and any("成熟状态不在允许范围" in error for error in unknown_rule_errors),
        "规则判定和成熟状态不能使用自造值",
    )
    conditional_fixture = copy.deepcopy(generic_fixture)
    conditional_fixture["整体未决事项"] = [{"事项编号": "U-FIXTURE", "类型": "缺资料", "当前处理": "停止受影响计算", "关联成果项": ["QTY-CF-COLUMN-FIXTURE"]}]
    conditional_item = conditional_fixture["工程量结果"][0]
    conditional_item["状态"] = "证据不足"
    conditional_item["结果"]["数值"] = None
    conditional_item["结果"]["数值状态"] = "未确定"
    conditional_item["未决事项"] = ["U-FIXTURE"]
    conditional_item["专业检查"].update({"条件候选值": 0.48, "条件候选单位": "m³", "进入采用量": False})
    negative_conditional = copy.deepcopy(conditional_fixture)
    negative_conditional["工程量结果"][0]["专业检查"]["条件候选值"] = -5.0
    negative_conditional_errors, _ = validate_result(negative_conditional, None, None, None)
    check("generic_validator_rejects_negative_conditional_candidate", any("条件候选值不得为负数" in error for error in negative_conditional_errors), "条件候选几何不得为负数")
    nan_conditional = copy.deepcopy(conditional_fixture)
    nan_conditional["工程量结果"][0]["专业检查"]["条件候选值"] = math.nan
    nan_conditional_errors, _ = validate_result(nan_conditional, None, None, None)
    infinite_conditional = copy.deepcopy(conditional_fixture)
    infinite_conditional["工程量结果"][0]["专业检查"]["条件候选值"] = math.inf
    infinite_conditional_errors, _ = validate_result(infinite_conditional, None, None, None)
    check(
        "generic_validator_rejects_nonfinite_conditional_candidate",
        any("条件候选值必须为有限数" in error for error in nan_conditional_errors) and any("条件候选值必须为有限数" in error for error in infinite_conditional_errors),
        "NaN和Infinity不得作为条件候选值",
    )
    mismatched_conditional_unit = copy.deepcopy(conditional_fixture)
    mismatched_conditional_unit["工程量结果"][0]["专业检查"]["条件候选单位"] = "m²"
    mismatched_conditional_unit_errors, _ = validate_result(mismatched_conditional_unit, None, None, None)
    check("generic_validator_rejects_conditional_unit_mismatch", any("条件候选单位必须与成果单位一致" in error for error in mismatched_conditional_unit_errors), "条件候选单位必须与成果单位一致")
    unmarked_conditional = copy.deepcopy(conditional_fixture)
    unmarked_conditional["工程量结果"][0]["专业检查"].pop("进入采用量")
    unmarked_conditional_errors, _ = validate_result(unmarked_conditional, None, None, None)
    check("generic_validator_requires_conditional_nonadoption", any("条件候选值必须明确不进入采用量" in error for error in unmarked_conditional_errors), "条件候选值必须显式标记进入采用量=false")
    orphan_unresolved = copy.deepcopy(conditional_fixture)
    orphan_unresolved["整体未决事项"][0]["关联成果项"] = ["QTY-NOT-FOUND"]
    orphan_unresolved_errors, _ = validate_result(orphan_unresolved, None, None, None)
    check("generic_validator_rejects_orphan_unresolved_link", any("关联不存在成果项" in error for error in orphan_unresolved_errors), "未决事项必须与现有成果双向关联")
    invalid_conditional = copy.deepcopy(generic_fixture)
    invalid_conditional["工程量结果"][0]["专业检查"]["进入采用量"] = False
    conditional_errors, _ = validate_result(invalid_conditional, None, None, None)
    check("generic_validator_rejects_conditional_number", any("不进入采用量" in error for error in conditional_errors), "条件候选值误写为正式数值必须失败")
    zero_fixture = copy.deepcopy(generic_fixture)
    zero_item = zero_fixture["工程量结果"][0]
    zero_item["成果项编号"] = "QTY-CF-FOUNDATION-BOTTOM-FORM-FIXTURE"
    zero_item["结果"] = {"名称": "完整已确认基础底面普通模板", "数值": 0.0, "数值状态": "已计算", "计算式或说明": "完整底面均贴已确认垫层，无普通模板接触面"}
    zero_item["单位"] = "m²"
    zero_item["零值复核"] = {"范围覆盖": "基础F1完整底面", "来源": ["EV-FIXTURE"], "规则": ["R-CF-FIXTURE"], "复核结论": "已确认零值", "说明": "全部底面贴已确认垫层"}
    zero_errors, _ = validate_result(zero_fixture, None, None, None)
    check("generic_validator_accepts_proven_zero", not zero_errors, f"完整范围和结构化证据支持真零；errors={zero_errors}")
    incomplete_zero = copy.deepcopy(zero_fixture)
    incomplete_zero["工程量结果"][0]["零值复核"].pop("来源")
    incomplete_zero["工程量结果"][0]["零值复核"].pop("规则")
    incomplete_zero_errors, _ = validate_result(incomplete_zero, None, None, None)
    check("generic_validator_rejects_incomplete_zero_review", any("零值复核缺少来源" in error for error in incomplete_zero_errors) and any("零值复核缺少规则" in error for error in incomplete_zero_errors), "真零复核必须绑定本成果来源和规则")
    zero_item["零值复核"] = None
    false_zero_errors, _ = validate_result(zero_fixture, None, None, None)
    check("generic_validator_rejects_unproven_zero", any("零值缺少结构化复核" in error for error in false_zero_errors), "未知或未覆盖零值必须失败")

    if any(item["status"] == "FAIL" for item in tests):
        raise AssertionError("self-test failed")
    return tests


def run_source_negative_tests(manifest_path: Path) -> list[dict[str, Any]]:
    calc = _load_calculator()
    base = _load_json(manifest_path)
    tests: list[dict[str, Any]] = []

    def expect_rejected(name: str, mutate, expected_fragment: str) -> None:
        candidate = copy.deepcopy(base)
        mutate(candidate)
        try:
            calc.calculate(candidate)
            tests.append({"name": name, "status": "FAIL", "detail": "错误输入未被拒绝"})
        except calc.CalculationError as exc:
            message = str(exc)
            tests.append({"name": name, "status": "PASS" if expected_fragment in message else "FAIL", "detail": message})

    expect_rejected("wrong_dwg_hash", lambda item: item["source"].__setitem__("dwg_sha256", "0" * 64), "冻结DWG哈希不符")
    expect_rejected("wrong_stream_hash", lambda item: item["source"].__setitem__("entity_stream_sha256", "0" * 64), "原生实体流哈希不符")
    expect_rejected("wrong_cache_adapter", lambda item: item["source"].__setitem__("expected_adapter", "unverified-adapter"), "缓存元数据adapter")
    expect_rejected("missing_decisive_dimension", lambda item: item["evidence_entities"].__setitem__(slice(None), [row for row in item["evidence_entities"] if row.get("role") != "notch_width"]), "尺寸role不完整")
    expect_rejected("silent_template_condition", lambda item: item["calculation"]["template_conditions"].__setitem__("blinding_side", "confirmed_formwork"), "模板面状态")

    def legacy_path(item: dict[str, Any]) -> None:
        qto_root = manifest_path.parent / ".qto"
        item["source"]["dwg_path"] = str(qto_root / "methods" / "update_concrete_formwork_artifacts.py")

    expect_rejected("legacy_runtime_dependency", legacy_path, "不得引用旧运行时")

    def disabled_archive_path(item: dict[str, Any]) -> None:
        item["source"]["dwg_path"] = str(Path.home() / ".agents" / "skills-disabled" / "construction-qto-legacy-probe" / "SKILL.md")

    expect_rejected("disabled_archive_dependency", disabled_archive_path, "不得引用旧运行时")

    def stage1_archive_path(item: dict[str, Any]) -> None:
        item["source"]["dwg_path"] = str(manifest_path.parent.parent / "阶段1_归档与停用" / "construction-qto-legacy-probe" / "SKILL.md")

    expect_rejected("stage1_archive_dependency", stage1_archive_path, "不得引用旧运行时")
    if any(item["status"] == "FAIL" for item in tests):
        raise AssertionError("source negative test failed")
    return tests


def _unique_index(items: Any, key: str, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        errors.append(f"{label}必须是数组")
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{position}]必须是对象")
            continue
        identifier = item.get(key)
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{label}[{position}]缺少{key}")
        elif identifier in indexed:
            errors.append(f"{label}的{key}重复: {identifier}")
        else:
            indexed[identifier] = item
    return indexed


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_check(checks: list[dict[str, Any]], errors: list[str], name: str, condition: bool, detail: str) -> None:
    checks.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})
    if not condition:
        errors.append(f"{name}: {detail}")


def validate_result(
    data: dict[str, Any],
    evidence: dict[str, Any] | None,
    detail_rows: list[dict[str, str]] | None,
    manifest_path: Path | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    top_before = len(errors)
    if data.get("契约版本") != RESULT_VERSION:
        errors.append(f"契约版本必须为{RESULT_VERSION}")
    for key in ("任务范围", "资料选择", "证据目录", "本次规则清单", "工程量结果", "整体未决事项"):
        if key not in data:
            errors.append(f"缺少顶层字段: {key}")
    checks.append({"name": "generic_top_level", "status": "PASS" if len(errors) == top_before else "FAIL", "detail": "检查契约版本和七类顶层业务字段"})

    ref_before = len(errors)
    sources = _unique_index(data.get("资料选择"), "资料编号", "资料选择", errors)
    evidence_catalog = _unique_index(data.get("证据目录"), "证据编号", "证据目录", errors)
    rules = _unique_index(data.get("本次规则清单"), "规则编号", "本次规则清单", errors)
    unresolved = _unique_index(data.get("整体未决事项"), "事项编号", "整体未决事项", errors)
    quantities = _unique_index(data.get("工程量结果"), "成果项编号", "工程量结果", errors)
    if not quantities:
        errors.append("工程量结果不能为空")
    for ev_id, item in evidence_catalog.items():
        source = sources.get(item.get("资料编号"))
        if source is None:
            errors.append(f"{ev_id}引用不存在资料")
        elif source.get("选择结论") != "已选择":
            errors.append(f"{ev_id}引用未选择资料")
    for rule_id, item in rules.items():
        decision = item.get("本次判定")
        maturity = item.get("成熟状态")
        if decision not in VALID_RULE_DECISIONS:
            errors.append(f"{rule_id}本次判定不在允许范围")
        if maturity not in VALID_RULE_MATURITY:
            errors.append(f"{rule_id}成熟状态不在允许范围")
        if decision == "采用" and maturity in {"候选", "停用"}:
            errors.append(f"{rule_id}为候选或停用规则，不得标为采用")
    for issue_id, item in unresolved.items():
        if item.get("类型") not in VALID_UNRESOLVED_TYPES:
            errors.append(f"{issue_id}未决类型不在允许范围")
        if item.get("当前处理") not in VALID_UNRESOLVED_ACTIONS:
            errors.append(f"{issue_id}当前处理不在允许范围")
        linked_results = item.get("关联成果项")
        if not isinstance(linked_results, list) or not linked_results:
            errors.append(f"{issue_id}缺少关联成果项")
        else:
            for linked_id in linked_results:
                if linked_id not in quantities:
                    errors.append(f"{issue_id}关联不存在成果项{linked_id}")
                elif issue_id not in quantities[linked_id].get("未决事项", []):
                    errors.append(f"{issue_id}与成果项{linked_id}缺少双向关联")
    checks.append({"name": "generic_reference_integrity", "status": "PASS" if len(errors) == ref_before else "FAIL", "detail": "检查资料、证据、规则、未决和成果的唯一引用关系"})

    quantity_before = len(errors)
    geometry_groups: dict[str, set[str]] = {}
    for qty_id, item in quantities.items():
        result = item.get("结果") if isinstance(item.get("结果"), dict) else {}
        status = item.get("状态")
        value = result.get("数值")
        value_status = result.get("数值状态")
        unit = item.get("单位")
        if status not in VALID_RESULT_STATES:
            errors.append(f"{qty_id}结果状态不在允许范围")
        if value_status not in VALID_VALUE_STATES:
            errors.append(f"{qty_id}数值状态不在允许范围")
        if unit not in {"m³", "m²"}:
            errors.append(f"{qty_id}单位必须为m³或m²")
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if status in {"已计算", "部分计算"} and not is_number:
            errors.append(f"{qty_id}有数状态必须是数字")
        if is_number and not math.isfinite(float(value)):
            errors.append(f"{qty_id}数值必须为有限数")
        elif is_number and float(value) < 0:
            errors.append(f"{qty_id}数值不得为负数")
        if status in {"已计算", "部分计算"} and value_status != "已计算":
            errors.append(f"{qty_id}有数状态的数值状态必须为已计算")
        if status in {"待确认", "证据不足"} and value is not None:
            errors.append(f"{qty_id}未决状态必须为null")
        if status in {"待确认", "证据不足"} and value_status != "未确定":
            errors.append(f"{qty_id}未决状态的数值状态必须为未确定")
        if status == "不适用" and (value is not None or result.get("数值状态") != "不适用"):
            errors.append(f"{qty_id}不适用状态必须为null/不适用")
        if status == "部分计算" and re.search(r"总量|合计|完整|全部|全量", str(result.get("名称", ""))):
            errors.append(f"{qty_id}部分结果名称夸大完整性")
        if status == "部分计算" and re.search(r"总量|合计|完整|全部|全量", str(result.get("计算式或说明", ""))):
            errors.append(f"{qty_id}部分结果说明夸大完整性")
        source_refs = item.get("来源")
        if not isinstance(source_refs, list) or not source_refs or any(not isinstance(ev_id, str) or not ev_id for ev_id in source_refs):
            errors.append(f"{qty_id}缺少来源证据")
            source_refs = []
        if not isinstance(result.get("计算式或说明"), str) or not result.get("计算式或说明", "").strip():
            errors.append(f"{qty_id}缺少计算式或说明")
        for ev_id in source_refs:
            if ev_id not in evidence_catalog:
                errors.append(f"{qty_id}引用不存在证据{ev_id}")
        rule_refs = item.get("规则")
        if not isinstance(rule_refs, list) or not rule_refs or any(not isinstance(rule_id, str) or not rule_id for rule_id in rule_refs):
            errors.append(f"{qty_id}缺少规则依据")
            rule_refs = []
        for rule_id in rule_refs:
            if rule_id not in rules:
                errors.append(f"{qty_id}引用不存在规则{rule_id}")
        issue_refs = item.get("未决事项")
        if not isinstance(issue_refs, list):
            errors.append(f"{qty_id}未决事项必须为数组")
            issue_refs = []
        for issue_id in issue_refs:
            if issue_id not in unresolved:
                errors.append(f"{qty_id}引用不存在未决项{issue_id}")
        adopted = [rules[rule_id] for rule_id in rule_refs if rule_id in rules and rules[rule_id].get("本次判定") == "采用"]
        if status in {"已计算", "部分计算"} and not adopted:
            errors.append(f"{qty_id}有数结果没有采用规则")
        if status in {"已计算", "部分计算"}:
            non_adopted = [rule_id for rule_id in rule_refs if rule_id in rules and rules[rule_id].get("本次判定") != "采用"]
            if non_adopted:
                errors.append(f"{qty_id}有数结果引用未采用规则: {', '.join(non_adopted)}")
        if status == "已计算" and issue_refs:
            errors.append(f"{qty_id}已计算状态不得关联未决事项")
        if status == "部分计算" and not issue_refs:
            errors.append(f"{qty_id}部分计算缺少未决事项")
        if status in {"待确认", "证据不足"} and not issue_refs:
            errors.append(f"{qty_id}未决状态缺少未决事项")
        if not isinstance(item.get("试运行结果"), bool):
            errors.append(f"{qty_id}试运行结果必须为布尔值")
        if any(rule.get("成熟状态") == "试运行" for rule in adopted) and item.get("试运行结果") is not True:
            errors.append(f"{qty_id}采用试运行规则但未标识试运行结果")
        professional = item.get("专业检查") if isinstance(item.get("专业检查"), dict) else {}
        signature = professional.get("几何签名")
        geometry_id = professional.get("构件几何编号")
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            errors.append(f"{qty_id}缺少合法几何签名")
        if not isinstance(geometry_id, str) or not geometry_id:
            errors.append(f"{qty_id}缺少构件几何编号")
        elif isinstance(signature, str):
            geometry_groups.setdefault(geometry_id, set()).add(signature)
        if "进入采用量" in professional and not isinstance(professional.get("进入采用量"), bool):
            errors.append(f"{qty_id}进入采用量必须为布尔值")
        if professional.get("进入采用量") is False and value is not None:
            errors.append(f"{qty_id}标为不进入采用量但仍有正式数值")
        if "条件候选值" in professional:
            conditional_value = professional.get("条件候选值")
            conditional_is_number = isinstance(conditional_value, (int, float)) and not isinstance(conditional_value, bool)
            if not conditional_is_number:
                errors.append(f"{qty_id}条件候选值必须为数字")
            elif not math.isfinite(float(conditional_value)):
                errors.append(f"{qty_id}条件候选值必须为有限数")
            elif float(conditional_value) < 0:
                errors.append(f"{qty_id}条件候选值不得为负数")
            if professional.get("条件候选单位") != unit:
                errors.append(f"{qty_id}条件候选单位必须与成果单位一致")
            if professional.get("进入采用量") is not False:
                errors.append(f"{qty_id}条件候选值必须明确不进入采用量")
            if status not in {"待确认", "证据不足"}:
                errors.append(f"{qty_id}条件候选值只能用于待确认或证据不足状态")
            if value is not None:
                errors.append(f"{qty_id}存在条件候选值时正式数值必须为null")
        zero_review = item.get("零值复核")
        if value == 0:
            if not isinstance(zero_review, dict) or zero_review.get("复核结论") != "已确认零值":
                errors.append(f"{qty_id}零值缺少结构化复核")
            else:
                review_sources = zero_review.get("来源")
                review_rules = zero_review.get("规则")
                if not isinstance(review_sources, list) or not review_sources:
                    errors.append(f"{qty_id}零值复核缺少来源")
                elif any(source_id not in source_refs for source_id in review_sources):
                    errors.append(f"{qty_id}零值复核引用成果项外来源")
                if not isinstance(review_rules, list) or not review_rules:
                    errors.append(f"{qty_id}零值复核缺少规则")
                elif any(rule_id not in rule_refs for rule_id in review_rules):
                    errors.append(f"{qty_id}零值复核引用成果项外规则")
                if not isinstance(zero_review.get("范围覆盖"), str) or not zero_review.get("范围覆盖", "").strip():
                    errors.append(f"{qty_id}零值复核缺少范围覆盖")
                if not isinstance(zero_review.get("说明"), str) or not zero_review.get("说明", "").strip():
                    errors.append(f"{qty_id}零值复核缺少说明")
        elif zero_review is not None:
            errors.append(f"{qty_id}非零或未决结果不应携带零值复核")
    for geometry_id, signatures in geometry_groups.items():
        if len(signatures) != 1:
            errors.append(f"同一构件几何编号{geometry_id}出现多个几何签名")
    checks.append({"name": "generic_quantity_invariants", "status": "PASS" if len(errors) == quantity_before else "FAIL", "detail": "检查数值/null、单位、规则状态、条件量、真零和同一几何不变量"})

    if evidence is not None or detail_rows is not None or manifest_path is not None:
        if evidence is None or manifest_path is None:
            errors.append("源链复算须同时提供evidence和manifest")
        else:
            try:
                expected_evidence, expected_details, expected_result = _load_calculator().calculate(_load_json(manifest_path))
                _case_check(checks, errors, 'manifest_recalculation', data == expected_result,
                            '成果须与当前源清单重算一致，不以项目固定答案校验')
                _case_check(checks, errors, 'evidence_recalculation',
                            all(evidence.get(k) == v for k, v in expected_evidence.items()),
                            '来源、几何、面条件和结果证据须与重算一致')
                _case_check(checks, errors, 'manifest_link',
                            evidence.get('manifest_sha256') == _file_hash(manifest_path),
                            '证据须绑定当前清单哈希')
                if detail_rows is not None:
                    normalize = lambda rows: [{k: '' if v is None else str(v) for k, v in row.items()} for row in rows]
                    _case_check(checks, errors, 'detail_recalculation', normalize(detail_rows) == normalize(expected_details),
                                '明细须与当前源清单重算一致')
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(f'源链复算失败: {exc}')
    return errors, checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证混凝土与模板专业结果及边界不变量")
    parser.add_argument("result", nargs="?", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--detail-csv", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source-negative-tests", type=Path)
    args = parser.parse_args(argv)
    validator_path = Path(__file__).resolve()
    report: dict[str, Any] = {
        "validator": "qto-concrete-formwork/1.2",
        "validator_file": {"path": str(validator_path), "sha256": _file_hash(validator_path)},
        "status": "PASS",
        "inputs": [],
        "tests": [],
        "errors": [],
    }
    try:
        if args.self_test:
            report["tests"] = run_self_tests()
        if args.source_negative_tests:
            negative_manifest_path = args.source_negative_tests.resolve()
            report["inputs"].append({"role": "source_negative_manifest", "path": str(negative_manifest_path), "sha256": _file_hash(negative_manifest_path)})
            report["tests"].extend(run_source_negative_tests(negative_manifest_path))
        if args.result:
            result_path = args.result.resolve()
            result = _load_json(result_path)
            report["inputs"].append({"role": "result", "path": str(result_path), "sha256": _file_hash(result_path)})
            evidence_path = args.evidence.resolve() if args.evidence else None
            evidence = _load_json(evidence_path) if evidence_path else None
            if evidence_path:
                report["inputs"].append({"role": "evidence", "path": str(evidence_path), "sha256": _file_hash(evidence_path)})
            detail_rows = None
            if args.detail_csv:
                detail_path = args.detail_csv.resolve()
                with detail_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    detail_rows = list(csv.DictReader(handle))
                report["inputs"].append({"role": "detail_csv", "path": str(detail_path), "sha256": _file_hash(detail_path)})
            manifest_path = args.manifest.resolve() if args.manifest else None
            if manifest_path:
                report["inputs"].append({"role": "manifest", "path": str(manifest_path), "sha256": _file_hash(manifest_path)})
            validation_errors, validation_checks = validate_result(result, evidence, detail_rows, manifest_path)
            report["errors"].extend(validation_errors)
            report["tests"].extend(validation_checks)
            if report["errors"]:
                report["status"] = "FAIL"
        elif not args.self_test and not args.source_negative_tests:
            parser.error("必须提供result、--self-test或--source-negative-tests")
    except (OSError, json.JSONDecodeError, RuntimeError, AssertionError, ValueError) as exc:
        report["status"] = "FAIL"
        report["errors"].append(str(exc))
    if args.report_out:
        args.report_out.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.report_out.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
