#!/usr/bin/env python3
"""Replay and bind a qto-rebar result to its manifest, evidence and detail."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class ValidationError(ValueError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 JSON {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"JSON 顶层必须是对象: {path}")
    return data


def run_checked(command: list[str], label: str) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        fail(f"{label}失败\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
    return completed.stdout


def exact_file_match(expected: Path, actual: Path, label: str) -> None:
    if expected.read_bytes() != actual.read_bytes():
        fail(f"{label}与独立回放结果不一致: {expected}")


def result_index(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = result.get("工程量结果")
    if not isinstance(rows, list):
        fail("成果缺少工程量结果数组")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("成果项编号"), str):
            fail("工程量结果存在非法成果项")
        item_id = row["成果项编号"]
        if item_id in index:
            fail(f"成果项编号重复: {item_id}")
        index[item_id] = row
    return index


def close(actual: float, expected: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calculator", required=True, type=Path)
    parser.add_argument("--minimum-validator", required=True, type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("detail", type=Path)
    args = parser.parse_args()

    try:
        for path in (args.calculator, args.minimum_validator, args.manifest, args.result, args.evidence, args.detail):
            if not path.resolve().is_file():
                fail(f"文件不存在: {path}")

        manifest = read_json(args.manifest.resolve())
        result = read_json(args.result.resolve())
        evidence = read_json(args.evidence.resolve())
        if evidence.get("schema_version") != "qto-rebar-calculation-evidence/1.0":
            fail("计算证据版本不正确")
        if result.get("契约版本") != "qto-result-minimum/1.0":
            fail("成果契约版本不正确")

        manifest_sha = sha256_file(args.manifest.resolve())
        calculator_sha = sha256_file(args.calculator.resolve())
        if evidence.get("manifest_sha256") != manifest_sha:
            fail("计算证据中的运行清单 SHA 不匹配")
        if evidence.get("calculator_sha256") != calculator_sha:
            fail("计算证据中的计算器 SHA 不匹配")
        generator = result.get("生成器身份")
        if not isinstance(generator, dict) or generator.get("运行清单SHA256") != manifest_sha or generator.get("计算器SHA256") != calculator_sha:
            fail("成果中的生成器身份不匹配")
        if evidence.get("case_id") != manifest.get("case_id") or result.get("案例编号") != manifest.get("case_id"):
            fail("案例编号未在清单、证据和成果之间闭合")

        with tempfile.TemporaryDirectory(prefix="qto-rebar-replay-") as temp_name:
            temp = Path(temp_name)
            replay_result = temp / "result.json"
            replay_evidence = temp / "evidence.json"
            replay_detail = temp / "detail.csv"
            run_checked([
                sys.executable, str(args.calculator.resolve()), str(args.manifest.resolve()),
                "--result-out", str(replay_result),
                "--evidence-out", str(replay_evidence),
                "--detail-out", str(replay_detail),
            ], "计算器独立回放")
            exact_file_match(args.result.resolve(), replay_result, "成果")
            exact_file_match(args.evidence.resolve(), replay_evidence, "计算证据")
            exact_file_match(args.detail.resolve(), replay_detail, "构件明细")

        minimum_output = run_checked([
            sys.executable, str(args.minimum_validator.resolve()), str(args.result.resolve())
        ], "公共最小成果验证")

        results = result_index(result)
        calculations = evidence.get("bar_group_calculations")
        if not isinstance(calculations, list) or not calculations:
            fail("计算证据缺少钢筋组计算")
        group_results: dict[str, list[dict[str, Any]]] = {}
        for item in results.values():
            group_id = item.get("钢筋组编号")
            if not isinstance(group_id, str):
                fail("成果项缺少钢筋组编号")
            group_results.setdefault(group_id, []).append(item)

        for calc in calculations:
            if not isinstance(calc, dict) or not isinstance(calc.get("group_id"), str):
                fail("钢筋组计算记录非法")
            group_id = calc["group_id"]
            related = group_results.get(group_id, [])
            if not related:
                fail(f"钢筋组 {group_id} 没有成果项")
            deferred_dimensions = calc.get("deferred_dimensions", []) if calc.get("method") == "count_only" else []
            if (not isinstance(deferred_dimensions, list)
                    or any(not isinstance(dimension, str) or dimension not in {"length", "weight"}
                           for dimension in deferred_dimensions)
                    or len(set(deferred_dimensions)) != len(deferred_dimensions)):
                fail(f"count_only 钢筋组 {group_id} 的未决维度不合法")
            if deferred_dimensions:
                expected_levels = {"count": calc.get("quantity_level"),
                                   **{dimension: "unresolved" for dimension in deferred_dimensions}}
                if (calc.get("status") != "partial" or calc.get("count_unit") != "根"
                        or not isinstance(calc.get("deferred_reason"), str) or not calc["deferred_reason"].strip()
                        or calc.get("dimension_quantity_levels") != expected_levels):
                    fail(f"count_only 钢筋组 {group_id} 的分维度未决证据不完整")
            if any(item.get("quantity_level") != ("unresolved" if item.get("范围", {}).get("输出维度") in deferred_dimensions
                                                   else calc.get("quantity_level")) for item in related):
                fail(f"钢筋组 {group_id} 的原生数量层级与计算证据不一致")
            if calc.get("adoption_role") == "reference":
                if calc.get("quantity_level") not in {"inferred", "scenario", "unresolved", "not_applicable"}:
                    fail(f"参考钢筋组 {group_id} 不得作为确认量")
                for item in related:
                    layer = item.get("成果分层", {})
                    if (item.get("采用角色") != "reference" or item.get("结果用途") != "reference_only"
                            or item.get("formal_adoption") is not False
                            or layer.get("进入正式量") is True or layer.get("发布状态") == "正式"):
                        fail(f"参考钢筋组 {group_id} 被提升为正式采用量")
            if calc.get("method") == "deferred":
                for item in related:
                    if item.get("结果", {}).get("数值") is not None:
                        fail(f"deferred 钢筋组 {group_id} 仍有数值")
                continue
            if calc.get("method") == "count_only":
                by_dimension = {item.get("范围", {}).get("输出维度"): item for item in related}
                if len(related) != len(by_dimension) or set(by_dimension) != {"count", *deferred_dimensions}:
                    fail(f"count_only 钢筋组 {group_id} 的输出不合法")
                count_item = by_dimension["count"]
                count = calc.get("count")
                if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                        or count_item.get("单位") not in {"个", "套", "根", "片", "处"}
                        or count_item.get("单位") != calc.get("count_unit")
                        or count_item.get("结果", {}).get("数值") != count):
                    fail(f"count_only 钢筋组 {group_id} 的计数成果与证据不一致")
                for dimension in deferred_dimensions:
                    item = by_dimension[dimension]
                    if (item.get("结果", {}).get("数值") is not None
                            or item.get("单位") != {"length": "m", "weight": "kg"}[dimension]
                            or item.get("结果", {}).get("数值状态") != "未确定"
                            or item.get("状态") != "证据不足"):
                        fail(f"count_only 钢筋组 {group_id} 的未决 {dimension} 被填数或错标")
                continue

            count = calc.get("count")
            length_each = calc.get("developed_length_each_m")
            total_length = calc.get("total_length_m")
            unit_mass = calc.get("unit_mass_kg_m")
            weight = calc.get("weight_kg")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (count, length_each, total_length, unit_mass, weight)):
                fail(f"钢筋组 {group_id} 的数值链不完整")
            if not close(count * length_each, total_length):
                fail(f"钢筋组 {group_id} 的根数×单根长度不守恒")
            if not close(total_length * unit_mass, weight):
                fail(f"钢筋组 {group_id} 的长度×单位质量不守恒")
            units = {item.get("单位") for item in related}
            if units != {"根", "m", "kg"}:
                fail(f"钢筋组 {group_id} 没有按 根/m/kg 三链输出: {units}")
            actual_by_unit = {item["单位"]: item["结果"]["数值"] for item in related}
            if actual_by_unit["根"] != count:
                fail(f"钢筋组 {group_id} 的根数成果与证据不一致")
            if not close(actual_by_unit["m"], round(total_length, 6), 1e-7):
                fail(f"钢筋组 {group_id} 的长度成果与证据不一致")
            if not close(actual_by_unit["kg"], round(weight, 6), 1e-7):
                fail(f"钢筋组 {group_id} 的重量成果与证据不一致")

        manifest_rules = manifest.get("rules", [])
        for rule in manifest_rules:
            if isinstance(rule, dict) and rule.get("decision") == "adopted" and rule.get("maturity") in {"candidate", "disabled"}:
                fail(f"候选或停用规则被采用: {rule.get('rule_id')}")
        test_mode = manifest.get("test_mode") is True
        if not test_mode:
            for item in result.get("证据目录", []):
                if isinstance(item, dict) and item.get("事实状态") == "test_only":
                    fail("生产模式成果包含 test_only 证据")

        print(json.dumps({
            "status": "PASS",
            "case_id": manifest.get("case_id"),
            "bound_files": 4,
            "result_items": len(results),
            "bar_groups": len(calculations),
            "minimum_validator": minimum_output.strip(),
            "checks": [
                "来源与生成器 SHA 闭合",
                "独立回放逐字节一致",
                "公共最小成果验证通过",
                "根数-长度-单位质量-重量守恒",
                "工作推算与采用量分离、候选规则和测试证据门通过",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
