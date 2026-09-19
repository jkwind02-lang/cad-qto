#!/usr/bin/env python3
"""Validate qto-structural-steel result invariants after the shared validator."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


COUNT_UNITS = {"套", "个", "根", "块", "件", "组", "批"}
MASS_TYPES = {"main_member", "node_plate", "other_permanent_steel"}
SOURCE_ROLES = {
    "project_drawing", "cad_entity_stream", "cad_metadata", "project_document",
    "approved_plan", "coverage_report", "standard_reference", "test_fixture",
}
PARTIAL_WORDS = {"总量", "完整", "全部", "全量", "合计"}
FORBIDDEN_FORMULA_WORDS = {"经验", "估算", "比例", "补差", "反推"}
LEGACY_FRAGMENTS = (
    "\\skills-disabled\\",
    "\\旧skill只读归档\\",
    "\\阶段1_归档与停用\\",
    "construction-qto-legacy",
    "construction-qto-preexisting-backup",
    "construction-qto-retired-source",
    "\\.qto\\methods\\",
)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def unique_map(items: Any, key: str, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
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

    professional = data.get("专业验算")
    if not isinstance(professional, dict):
        errors.append("缺少专业验算")
    else:
        if professional.get("来源身份通过") is not True:
            errors.append("来源身份未通过")
        if professional.get("不同单位分链") is not True:
            errors.append("不同单位未分链")
        if professional.get("旧运行时依赖") is not False:
            errors.append("不得依赖旧运行时")

    for source in data.get("资料选择", []):
        if not isinstance(source, dict):
            continue
        path = str(source.get("路径", "")).replace("/", "\\").lower()
        if any(fragment in path for fragment in LEGACY_FRAGMENTS):
            errors.append(f"资料路径引用旧Skill、旧归档或旧项目方法: {source.get('资料编号')}")
        if source.get("文件角色") not in {"dwg", "dxf", "pdf", "json", "gzip_ndjson", "spreadsheet", "document", "csv", "text", "image"}:
            errors.append(f"{source.get('资料编号')}缺少合法文件角色")
        if source.get("语义角色") not in SOURCE_ROLES:
            errors.append(f"{source.get('资料编号')}缺少合法语义角色")

    evidence_map = unique_map(data.get("证据目录"), "证据编号", "证据目录", errors)

    results = unique_map(data.get("工程量结果"), "成果项编号", "工程量结果", errors)
    for identifier, item in results.items():
        value_block = item.get("结果")
        check = item.get("专业检查")
        if not isinstance(value_block, dict) or not isinstance(check, dict):
            errors.append(f"{identifier}缺少结果或专业检查对象")
            continue
        value = value_block.get("数值")
        name = value_block.get("名称", "")
        formula = value_block.get("计算式或说明", "")
        unit = item.get("单位")
        status = item.get("状态")
        object_type = check.get("对象类别")
        method = check.get("计算方法")
        mass_inclusion = check.get("质量归属")
        adoption_role = check.get("采用角色")
        physical_object_id = check.get("物理对象编号")
        identity_signature = check.get("对象身份签名")
        measurement_basis = check.get("计量基准")
        result_use = check.get("结果用途")
        enters_permanent_mass = check.get("进入永久钢质量小计")
        completeness = check.get("范围完整性")
        inputs = check.get("输入字段", {})

        if object_type not in {
            "main_member", "node_plate", "connection_hardware", "profiled_sheet",
            "coating", "measure", "other_permanent_steel",
        }:
            errors.append(f"{identifier}对象类别非法")
            continue
        if mass_inclusion not in {"permanent", "separate_reference", "not_mass"}:
            errors.append(f"{identifier}质量归属非法")
        if adoption_role not in {"adopted", "reference"}:
            errors.append(f"{identifier}采用角色非法")
        if not nonempty(physical_object_id):
            errors.append(f"{identifier}缺少物理对象编号")
        if not isinstance(identity_signature, str) or not re.fullmatch(r"[0-9a-f]{64}", identity_signature):
            errors.append(f"{identifier}缺少有效对象身份签名")
        if not nonempty(measurement_basis):
            errors.append(f"{identifier}缺少计量基准")
        if not isinstance(enters_permanent_mass, bool):
            errors.append(f"{identifier}进入永久钢质量小计必须是布尔值")
        expected_use = "unresolved" if value is None else (
            "reference_only" if adoption_role == "reference" or mass_inclusion == "separate_reference" else (
                "adopted_physical_quantity" if mass_inclusion == "permanent" else "independent_non_mass_quantity"
            )
        )
        if result_use != expected_use:
            errors.append(f"{identifier}结果用途与数值/质量归属不一致")
        expected_mass_entry = bool(
            numeric(value) and unit == "t" and object_type in MASS_TYPES
            and mass_inclusion == "permanent" and adoption_role == "adopted"
        )
        if enters_permanent_mass != expected_mass_entry:
            errors.append(f"{identifier}永久钢质量小计标记不一致")
        if numeric(value) and value < 0:
            errors.append(f"{identifier}数值不得为负数")
        if status == "部分计算" and any(word in str(name) or word in str(formula) for word in PARTIAL_WORDS):
            errors.append(f"{identifier}部分结果夸大范围完整性")
        if numeric(value) and object_type in MASS_TYPES and unit != "t":
            errors.append(f"{identifier}钢材质量必须使用t")
        if numeric(value) and object_type == "connection_hardware":
            if unit not in COUNT_UNITS:
                errors.append(f"{identifier}连接件必须按套/个/根等数量单位单列")
            if mass_inclusion != "not_mass":
                errors.append(f"{identifier}连接件数量不得作为钢材质量")
        if numeric(value) and object_type in {"profiled_sheet", "coating"} and unit != "m²":
            errors.append(f"{identifier}{object_type}必须使用m²")
        if numeric(value) and object_type in {"profiled_sheet", "coating", "measure"} and mass_inclusion != "not_mass":
            errors.append(f"{identifier}面积或措施数量不得并入永久钢材质量")

        if object_type == "node_plate" and method not in {"plate", "deferred", "explicit_summary"}:
            errors.append(f"{identifier}节点板计算方法非法")
        if object_type == "connection_hardware" and method not in {"count", "deferred", "explicit_summary"}:
            errors.append(f"{identifier}连接件计算方法非法")
        if object_type == "profiled_sheet" and method not in {"area", "deferred", "explicit_summary"}:
            errors.append(f"{identifier}压型板计算方法非法")
        if object_type == "coating" and method not in {"surface", "deferred", "explicit_summary"}:
            errors.append(f"{identifier}涂装计算方法非法")

        if numeric(value) and method in {"profile_length", "built_up_section_length", "plate"}:
            if any(word in str(formula) for word in FORBIDDEN_FORMULA_WORDS):
                errors.append(f"{identifier}质量公式含经验比例或补差")
        if numeric(value) and method == "plate":
            if not isinstance(inputs, dict) or not nonempty(inputs.get("hole_treatment")):
                errors.append(f"{identifier}板件缺少孔洞处理口径")
        if numeric(value) and object_type == "profiled_sheet":
            if not isinstance(inputs, dict) or any(not nonempty(inputs.get(field)) for field in ("area_basis", "opening_rule", "overlap_rule")):
                errors.append(f"{identifier}压型板缺少面积、洞口或搭接口径")
        if numeric(value) and object_type == "coating":
            if not isinstance(inputs, dict) or any(not nonempty(inputs.get(field)) for field in ("area_basis", "treatment_scope")):
                errors.append(f"{identifier}涂装缺少暴露面或处理范围")
        if numeric(value) and object_type == "measure":
            plan_refs = check.get("方案证据")
            if not isinstance(plan_refs, list) or not plan_refs:
                errors.append(f"{identifier}方案依赖措施量缺少方案证据")
            elif any(evidence_map.get(ref, {}).get("证据类别") != "plan_approved" for ref in plan_refs):
                errors.append(f"{identifier}方案证据类别必须为plan_approved")

        if object_type == "node_plate" and method == "explicit_summary" and completeness == "complete":
            if check.get("节点覆盖闭合") is not True:
                errors.append(f"{identifier}节点板完整汇总缺少节点覆盖闭合证明")
        if method == "explicit_summary":
            members = check.get("汇总成员")
            if not isinstance(members, list) or not members:
                errors.append(f"{identifier}显式汇总缺少成员")
            else:
                member_items = [results.get(member_id) for member_id in members]
                if any(member is None for member in member_items):
                    errors.append(f"{identifier}显式汇总引用不存在成员")
                else:
                    member_units = {member.get("单位") for member in member_items if member}
                    member_types = {member.get("专业检查", {}).get("对象类别") for member in member_items if member}
                    member_objects = [member.get("专业检查", {}).get("物理对象编号") for member in member_items if member]
                    member_signatures = [member.get("专业检查", {}).get("对象身份签名") for member in member_items if member]
                    if member_units != {unit}:
                        errors.append(f"{identifier}显式汇总混合不同单位")
                    if member_types != {object_type}:
                        errors.append(f"{identifier}显式汇总混合不同对象类别")
                    if len(member_objects) != len(set(member_objects)):
                        errors.append(f"{identifier}显式汇总重复同一物理对象的多个口径")
                    if len(member_signatures) != len(set(member_signatures)):
                        errors.append(f"{identifier}显式汇总重复同一对象证据身份的多个口径")

        candidate = check.get("条件候选值")
        if candidate is not None:
            if not numeric(candidate) or candidate < 0:
                errors.append(f"{identifier}条件候选值必须是非负有限数")
            if value is not None or check.get("条件候选值进入采用量") is not False:
                errors.append(f"{identifier}条件候选值必须与采用量隔离")

    return errors


def _fixture() -> dict[str, Any]:
    return {
        "专业验算": {"来源身份通过": True, "不同单位分链": True, "旧运行时依赖": False},
        "资料选择": [{"资料编号": "SRC", "路径": "D:\\fixture\\source.dwg", "文件角色": "dwg", "语义角色": "project_drawing"}],
        "证据目录": [],
        "工程量结果": [
            {
                "成果项编号": "M1", "结果": {"名称": "梁M1", "数值": 1.0, "计算式或说明": "A×L×ρ"},
                "单位": "t", "状态": "已计算",
                "专业检查": {"对象类别": "main_member", "物理对象编号": "OBJ-M1", "对象身份签名": "1" * 64, "计量基准": "theoretical_mass", "计算方法": "built_up_section_length", "质量归属": "permanent", "采用角色": "adopted", "结果用途": "adopted_physical_quantity", "进入永久钢质量小计": True, "范围完整性": "complete", "输入字段": {}},
            },
            {
                "成果项编号": "B1", "结果": {"名称": "M20连接副", "数值": 8, "计算式或说明": "2×4"},
                "单位": "套", "状态": "已计算",
                "专业检查": {"对象类别": "connection_hardware", "物理对象编号": "OBJ-B1", "对象身份签名": "2" * 64, "计量基准": "physical_count", "计算方法": "count", "质量归属": "not_mass", "采用角色": "adopted", "结果用途": "independent_non_mass_quantity", "进入永久钢质量小计": False, "范围完整性": "complete", "输入字段": {"quantity": 8}},
            },
        ],
    }


def run_self_tests() -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []

    def expect(name: str, candidate: dict[str, Any], fragment: str | None) -> None:
        errors = validate_document(candidate)
        passed = not errors if fragment is None else any(fragment in error for error in errors)
        tests.append({"name": name, "status": "PASS" if passed else "FAIL", "errors": errors})

    base = _fixture()
    expect("valid_member_and_connector", base, None)
    mixed = copy.deepcopy(base)
    mixed["工程量结果"].append({
        "成果项编号": "S1", "结果": {"名称": "错误混合汇总", "数值": 9, "计算式或说明": "M1+B1"}, "单位": "t", "状态": "已计算",
        "专业检查": {"对象类别": "main_member", "物理对象编号": "SUM-S1", "对象身份签名": "3" * 64, "计量基准": "explicit_summary", "计算方法": "explicit_summary", "质量归属": "permanent", "采用角色": "adopted", "结果用途": "adopted_physical_quantity", "进入永久钢质量小计": True, "范围完整性": "complete", "汇总成员": ["M1", "B1"]},
    })
    expect("reject_mixed_summary", mixed, "显式汇总混合不同")
    connector_mass = copy.deepcopy(base)
    connector_mass["工程量结果"][1]["单位"] = "t"
    expect("reject_connector_mass", connector_mass, "连接件必须按")
    legacy = copy.deepcopy(base)
    legacy["资料选择"][0]["路径"] = "C:\\Users\\x\\.agents\\skills-disabled\\construction-qto-legacy\\old.json"
    expect("reject_legacy_runtime", legacy, "资料路径引用旧")
    measure = copy.deepcopy(base)
    measure["工程量结果"][0]["专业检查"].update({
        "对象类别": "measure", "计算方法": "count", "质量归属": "not_mass",
        "结果用途": "independent_non_mass_quantity", "进入永久钢质量小计": False,
    })
    measure["工程量结果"][0]["单位"] = "件"
    expect("reject_measure_without_plan", measure, "缺少方案证据")
    partial = copy.deepcopy(base)
    partial["工程量结果"][0]["状态"] = "部分计算"
    partial["工程量结果"][0]["结果"]["名称"] = "完整钢梁总量"
    expect("reject_partial_overclaim", partial, "夸大范围完整性")
    if any(test["status"] == "FAIL" for test in tests):
        raise AssertionError("structural-steel validator self-test failed")
    return tests


def load_json_strict(path: Path) -> dict[str, Any]:
    data = json.loads(
        path.read_text(encoding="utf-8-sig"),
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    if not isinstance(data, dict):
        raise ValueError("顶层必须是JSON对象")
    return data


def load_calculator(path: Path):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("qto_structural_steel_bound_calculator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载指定钢结构计算器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("calculate", "canonical_hash", "sha256_file"):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(f"指定计算器缺少{name}")
    return module


def validate_binding_payloads(
    result: dict[str, Any],
    evidence: dict[str, Any],
    manifest: dict[str, Any],
    calculator: Any,
) -> list[str]:
    """Bind a result to evidence, manifest and a deterministic recalculation."""
    errors = validate_document(result)
    if evidence.get("schema_version") != "qto-structural-steel-calculation-evidence/1.0":
        errors.append("计算证据schema_version非法")
    result_hash = calculator.canonical_hash(result)
    if evidence.get("result_canonical_sha256") != result_hash:
        errors.append("成果规范哈希与计算证据不一致")
    manifest_hash = calculator.canonical_hash(manifest)
    if evidence.get("manifest_canonical_sha256") != manifest_hash:
        errors.append("运行清单规范哈希与计算证据不一致")
    if result.get("专业验算", {}).get("运行清单规范哈希") != manifest_hash:
        errors.append("成果中的运行清单规范哈希不一致")
    try:
        recalculated_evidence, recalculated_details, recalculated_result = calculator.calculate(copy.deepcopy(manifest))
    except Exception as exc:  # deterministic binding must report calculator failures
        errors.append(f"绑定复算失败: {type(exc).__name__}: {exc}")
        return errors
    if calculator.canonical_hash(recalculated_result) != result_hash:
        errors.append("绑定复算成果与提交成果不一致")
    if calculator.canonical_hash(recalculated_details) != evidence.get("detail_rows_canonical_sha256"):
        errors.append("绑定复算明细与计算证据不一致")
    if recalculated_evidence.get("items") != evidence.get("items"):
        errors.append("绑定复算逐项证据与提交证据不一致")
    return errors


def validate_binding(
    result_path: Path,
    evidence_path: Path,
    manifest_path: Path,
    calculator_path: Path,
    calculator: Any,
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    errors: list[str] = []
    try:
        result = load_json_strict(result_path)
        evidence = load_json_strict(evidence_path)
        manifest = load_json_strict(manifest_path)
    except (OSError, ValueError) as exc:
        return [f"绑定文件不可读或不是有效JSON: {exc}"], None, None, None

    errors.extend(validate_binding_payloads(result, evidence, manifest, calculator))
    manifest_declared = evidence.get("manifest_path")
    if not nonempty(manifest_declared) or Path(manifest_declared).resolve() != manifest_path:
        errors.append("计算证据声明的运行清单路径与绑定输入不一致")
    elif evidence.get("manifest_sha256") != calculator.sha256_file(manifest_path):
        errors.append("运行清单文件哈希与计算证据不一致")

    generator = evidence.get("generator")
    if not isinstance(generator, dict):
        errors.append("计算证据缺少生成器身份")
    else:
        declared_path = generator.get("path")
        if not nonempty(declared_path) or Path(declared_path).resolve() != calculator_path:
            errors.append("计算证据声明的生成器路径与绑定计算器不一致")
        if generator.get("sha256") != calculator.sha256_file(calculator_path):
            errors.append("生成器文件哈希与计算证据不一致")

    result_file = evidence.get("result_file")
    if not isinstance(result_file, dict):
        errors.append("计算证据缺少成果文件身份")
    else:
        declared_path = result_file.get("path")
        if not nonempty(declared_path) or Path(declared_path).resolve() != result_path:
            errors.append("计算证据声明的成果路径与绑定成果不一致")
        if result_file.get("sha256") != calculator.sha256_file(result_path):
            errors.append("成果文件哈希与计算证据不一致")

    detail_csv = evidence.get("detail_csv")
    if not isinstance(detail_csv, dict) or not nonempty(detail_csv.get("path")):
        errors.append("计算证据缺少明细CSV身份")
    else:
        detail_path = Path(detail_csv["path"]).resolve()
        if not detail_path.is_file():
            errors.append("计算证据声明的明细CSV不存在")
        elif detail_csv.get("sha256") != calculator.sha256_file(detail_path):
            errors.append("明细CSV文件哈希与计算证据不一致")
    return errors, result, evidence, manifest


def binding_tamper_self_test(
    result: dict[str, Any], evidence: dict[str, Any], manifest: dict[str, Any], calculator: Any
) -> dict[str, Any]:
    tampered = copy.deepcopy(result)
    changed = False
    for item in tampered.get("工程量结果", []):
        value_block = item.get("结果", {}) if isinstance(item, dict) else {}
        if numeric(value_block.get("数值")):
            value_block["数值"] = 999.0
            changed = True
            break
    errors = validate_binding_payloads(tampered, evidence, manifest, calculator) if changed else ["无可篡改数值"]
    passed = changed and any("成果规范哈希" in error or "绑定复算成果" in error for error in errors)
    return {
        "name": "reject_tampered_numeric_result",
        "status": "PASS" if passed else "FAIL",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验钢结构成果的专业不变量及可重放绑定")
    parser.add_argument("files", nargs="*", type=Path, help="仅在--schema-only下执行结构校验")
    parser.add_argument("--schema-only", action="store_true", help="仅检查成果结构，不证明成果与清单绑定")
    parser.add_argument("--calculator", type=Path, help="绑定复算使用的计算器")
    parser.add_argument(
        "--binding",
        nargs=3,
        action="append",
        metavar=("RESULT", "EVIDENCE", "MANIFEST"),
        type=Path,
        help="绑定校验一组成果、计算证据和运行清单；可重复",
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out", type=Path, help="可选：把验证报告写入JSON文件")
    args = parser.parse_args()
    payload: dict[str, Any] = {"self_tests": [], "binding_self_tests": [], "reports": [], "bindings": []}
    failed = False
    if args.self_test:
        try:
            payload["self_tests"] = run_self_tests()
        except AssertionError as exc:
            failed = True
            payload["self_test_error"] = str(exc)
    if args.files and not args.schema_only:
        failed = True
        payload["input_error"] = "位置成果文件只能与--schema-only一起使用；业务验收必须使用--binding"
    elif args.schema_only:
        for path in args.files:
            path = path.resolve()
            try:
                data = load_json_strict(path)
                errors = validate_document(data)
            except (OSError, ValueError) as exc:
                errors = [f"文件不可读或不是有效JSON: {exc}"]
            failed = failed or bool(errors)
            payload["reports"].append({"file": str(path), "status": "PASS" if not errors else "FAIL", "errors": errors})

    bindings = args.binding or []
    calculator = None
    calculator_path = args.calculator.resolve() if args.calculator else None
    if bindings and calculator_path is None:
        failed = True
        payload["binding_error"] = "使用--binding时必须提供--calculator"
    elif calculator_path is not None:
        try:
            calculator = load_calculator(calculator_path)
        except (OSError, RuntimeError) as exc:
            failed = True
            payload["calculator_error"] = str(exc)

    if calculator is not None and calculator_path is not None:
        for result_raw, evidence_raw, manifest_raw in bindings:
            result_path, evidence_path, manifest_path = (
                result_raw.resolve(), evidence_raw.resolve(), manifest_raw.resolve()
            )
            errors, result, evidence, manifest = validate_binding(
                result_path, evidence_path, manifest_path, calculator_path, calculator
            )
            failed = failed or bool(errors)
            payload["bindings"].append({
                "result": str(result_path),
                "evidence": str(evidence_path),
                "manifest": str(manifest_path),
                "status": "PASS" if not errors else "FAIL",
                "errors": errors,
            })
            if args.self_test and result is not None and evidence is not None and manifest is not None:
                tamper_test = binding_tamper_self_test(result, evidence, manifest, calculator)
                tamper_test["result"] = str(result_path)
                payload["binding_self_tests"].append(tamper_test)
                failed = failed or tamper_test["status"] != "PASS"
    payload.update({"status": "FAIL" if failed else "PASS", "business_correctness_proven": False})
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        out_path = args.out.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
