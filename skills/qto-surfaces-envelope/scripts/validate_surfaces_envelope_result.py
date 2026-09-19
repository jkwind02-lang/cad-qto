#!/usr/bin/env python3
"""绑定复算并校验面层与建筑围护成果。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any


OBJECT_TYPES = {
    "floor_finish", "wall_finish", "ceiling_finish", "roof_covering",
    "waterproofing", "insulation", "facade_panel", "curtain_wall_panel",
    "other_surface",
}
METHODS = {"area_direct", "horizontal_net", "rectangle_net", "sloped_from_projection", "deferred"}
LEGACY_FRAGMENTS = (
    "\\skills-disabled\\", "\\旧skill只读归档\\", "\\阶段1_归档与停用\\",
    "construction-qto-legacy", "construction-qto-preexisting-backup",
    "construction-qto-retired-source", "\\.qto\\methods\\",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(value)

    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)


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
    required_true = {
        "来源身份通过", "中性实体流逐行守恒", "构造层分账", "洞口采用并集证据",
        "轴线与设计表面口径分离", "参考量未进入采用小计",
    }
    if not isinstance(professional, dict):
        errors.append("缺少专业验算")
    else:
        for field in required_true:
            if professional.get(field) is not True:
                errors.append(f"专业验算未通过: {field}")
        if professional.get("旧运行时依赖") is not False:
            errors.append("不得依赖旧运行时")

    for source in data.get("资料选择", []):
        if not isinstance(source, dict):
            continue
        lowered = str(source.get("路径", "")).replace("/", "\\").lower()
        if any(fragment in lowered for fragment in LEGACY_FRAGMENTS):
            errors.append(f"资料路径引用旧运行时: {source.get('资料编号')}")

    evidence = unique_map(data.get("证据目录"), "证据编号", "证据目录", errors)
    rules = unique_map(data.get("本次规则清单"), "规则编号", "本次规则清单", errors)
    unresolved = unique_map(data.get("整体未决事项"), "事项编号", "整体未决事项", errors)
    results = unique_map(data.get("工程量结果"), "成果项编号", "工程量结果", errors)
    identities: set[str] = set()
    for item_id, item in results.items():
        if item.get("单位") != "m²":
            errors.append(f"{item_id}单位必须为m²")
        value_block = item.get("结果")
        check = item.get("专业检查")
        if not isinstance(value_block, dict) or not isinstance(check, dict):
            errors.append(f"{item_id}缺少结果或专业检查")
            continue
        value = value_block.get("数值")
        value_state = value_block.get("数值状态")
        method = check.get("计算方法")
        object_type = check.get("对象类别")
        adoption = check.get("采用角色")
        scope = check.get("范围完整性")
        signature = check.get("对象身份签名")
        if object_type not in OBJECT_TYPES:
            errors.append(f"{item_id}对象类别非法")
        if method not in METHODS:
            errors.append(f"{item_id}计算方法非法")
        if adoption not in {"adopted", "reference"}:
            errors.append(f"{item_id}采用角色非法")
        if scope not in {"complete", "partial", "unresolved", "not_applicable"}:
            errors.append(f"{item_id}范围完整性非法")
        if not nonempty(signature):
            errors.append(f"{item_id}缺少对象身份签名")
        elif signature in identities:
            errors.append(f"{item_id}对象身份签名重复")
        else:
            identities.add(signature)
        refs = item.get("来源")
        if not isinstance(refs, list):
            errors.append(f"{item_id}.来源必须是数组")
            refs = []
        for ref in refs:
            if ref not in evidence:
                errors.append(f"{item_id}引用不存在的证据: {ref}")
        for ref in item.get("规则", []):
            if ref not in rules:
                errors.append(f"{item_id}引用不存在的规则: {ref}")
        for ref in item.get("未决事项", []):
            if ref not in unresolved:
                errors.append(f"{item_id}引用不存在的未决事项: {ref}")
        if value_state == "未确定" and value is not None:
            errors.append(f"{item_id}未确定值必须为null")
        if value_state == "已计算" and not numeric(value):
            errors.append(f"{item_id}已计算值必须为有限数值")
        if numeric(value) and value < 0:
            errors.append(f"{item_id}面积不得为负")
        if numeric(value) and value == 0 and not isinstance(item.get("零值复核"), dict):
            errors.append(f"{item_id}零值缺少覆盖复核")
        if method == "deferred" and value is not None:
            errors.append(f"{item_id}deferred项不得有数值")
        if adoption == "reference" and check.get("进入采用小计") is not False:
            errors.append(f"{item_id}参考量不得进入采用小计")

    subtotals = data.get("工程量小计")
    if not isinstance(subtotals, list):
        errors.append("工程量小计必须是数组")
        subtotals = []
    for subtotal in subtotals:
        if not isinstance(subtotal, dict):
            errors.append("工程量小计项必须是对象")
            continue
        refs = subtotal.get("来源成果项")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{subtotal.get('小计编号')}缺少来源成果项")
            continue
        expected = 0.0
        for ref in refs:
            if ref not in results:
                errors.append(f"{subtotal.get('小计编号')}引用不存在成果项: {ref}")
                continue
            item = results[ref]
            check = item.get("专业检查", {})
            value = item.get("结果", {}).get("数值")
            if check.get("采用角色") != subtotal.get("采用角色"):
                errors.append(f"{subtotal.get('小计编号')}混合采用与参考口径")
            if check.get("构造层编号") != subtotal.get("构造层编号"):
                errors.append(f"{subtotal.get('小计编号')}混合构造层")
            if numeric(value):
                expected += value
        if not numeric(subtotal.get("数值")) or not math.isclose(float(subtotal["数值"]), expected, abs_tol=1e-8):
            errors.append(f"{subtotal.get('小计编号')}数值与来源项不守恒")
    return errors


def import_calculator(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("qto_surfaces_envelope_calculator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法导入计算器: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "calculate_from_path"):
        raise RuntimeError("计算器缺少calculate_from_path")
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="校验面层与建筑围护成果并执行绑定复算")
    parser.add_argument("--calculator", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--binding", required=True, type=Path, help="待校验成果JSON")
    args = parser.parse_args()
    try:
        calculator = args.calculator.expanduser().resolve()
        manifest = args.manifest.expanduser().resolve()
        binding = args.binding.expanduser().resolve()
        data = load_json(binding)
        errors = validate_document(data)
        replay = import_calculator(calculator).calculate_from_path(manifest)
        if data != replay:
            errors.append("成果与当前计算器、运行清单绑定复算结果不一致")
        bound = data.get("绑定验证", {}) if isinstance(data, dict) else {}
        if bound.get("运行清单SHA256") != sha256_file(manifest):
            errors.append("运行清单SHA256绑定失败")
        if bound.get("生成器SHA256") != sha256_file(calculator):
            errors.append("生成器SHA256绑定失败")
        report = {
            "status": "PASS" if not errors else "FAIL",
            "binding": str(binding),
            "manifest": str(manifest),
            "calculator": str(calculator),
            "error_count": len(errors),
            "errors": errors,
            "business_correctness_proven": False,
            "note": "通过表示绑定复算与已编码不变量成立，不替代项目专业复核。",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not errors else 2
    except Exception as exc:  # CLI boundary
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
