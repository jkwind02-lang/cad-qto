#!/usr/bin/env python3
"""Deterministically recalculate already-resolved reinforcement groups.

This calculator validates source identities and explicit inputs.  It does not
interpret drawings, infer bar counts from spacing, or select anchorage/detailing
rules.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "qto-rebar-manifest/1.0"
EVIDENCE_VERSION = "qto-rebar-calculation-evidence/1.0"
RESULT_VERSION = "qto-result-minimum/1.0"

FILE_ROLES = {
    "dwg", "dxf", "pdf", "json", "gzip_ndjson", "spreadsheet",
    "document", "csv", "text", "image",
}
SOURCE_ROLES = {
    "project_drawing", "cad_metadata", "cad_entity_stream",
    "project_document", "approved_schedule", "standard_reference",
    "test_fixture",
}
EVIDENCE_CLASSES = {
    "source_identity", "object_geometry", "object_mapping", "bar_schedule",
    "material_property", "rule_clause", "approved_detail",
    "coverage_complete", "requirement", "third_party_reference",
}
FACT_STATUSES = {"verified", "reference", "candidate", "test_only"}
RULE_DECISIONS = {"adopted", "not_applicable", "insufficient"}
RULE_MATURITY = {"candidate", "trial", "formal", "disabled"}
GROUP_STATUSES = {"complete", "partial", "unresolved", "not_applicable"}
ADOPTION_ROLES = {"adopted", "reference"}
QUANTITY_BASES = {
    "physical_design", "cutting_schedule", "standard_boq",
    "project_contract", "procurement_reference", "test_reference",
}
METHODS = {
    "explicit_developed_length", "straight_with_explicit_additions",
    "count_only", "deferred",
}
OLD_PATH_MARKERS = (
    "skills-disabled", "construction-qto-retired-source",
    "阶段1_归档与停用", "\\archived\\", "/archived/",
)
STATUS_CN = {
    "complete": "已计算", "partial": "部分计算",
    "unresolved": "证据不足", "not_applicable": "不适用",
}
VALUE_STATUS_CN = {
    "complete": "已计算", "partial": "已计算",
    "unresolved": "未确定", "not_applicable": "不适用",
}
RULE_DECISION_CN = {
    "adopted": "采用", "not_applicable": "不适用", "insufficient": "证据不足",
}
RULE_MATURITY_CN = {
    "candidate": "候选", "trial": "试运行",
    "formal": "正式", "disabled": "停用",
}


class ManifestError(ValueError):
    pass


def fail(message: str) -> None:
    raise ManifestError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 JSON {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"JSON 顶层必须是对象: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_text(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"{where} 缺少非空文本字段 {key}")
    return value.strip()


def require_list(obj: dict[str, Any], key: str, where: str) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        fail(f"{where}.{key} 必须是数组")
    return value


def require_number(obj: dict[str, Any], key: str, where: str, *, positive: bool = False) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(f"{where}.{key} 必须是有限数字")
    result = float(value)
    if positive and result <= 0:
        fail(f"{where}.{key} 必须大于 0")
    return result


def require_count(obj: dict[str, Any], key: str, where: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{where}.{key} 必须是非负整数")
    return value


def unique_index(items: Any, key: str, where: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        fail(f"{where} 必须是数组")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            fail(f"{where}[{index}] 必须是对象")
        identifier = require_text(item, key, f"{where}[{index}]")
        if identifier in result:
            fail(f"{where} 存在重复编号 {identifier}")
        result[identifier] = item
    return result


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def reject_old_path(path: Path, where: str) -> None:
    text = str(path.resolve()).replace("/", "\\").casefold()
    for marker in OLD_PATH_MARKERS:
        if marker.casefold() in text:
            fail(f"{where} 引用了旧 Skill、旧归档或旧结果路径: {path}")


def dotted_get(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            fail(f"JSON 断言字段不存在: {dotted}")
    return current


def comparable(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def close_enough(actual: Any, expected: Any, tolerance: float = 1e-9) -> bool:
    actual = comparable(actual)
    expected = comparable(expected)
    if isinstance(actual, (int, float)) and not isinstance(actual, bool) and isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(actual, list) and isinstance(expected, list) and len(actual) == len(expected):
        return all(close_enough(a, b, tolerance) for a, b in zip(actual, expected))
    return actual == expected


def validate_source(
    source: dict[str, Any],
    test_mode: bool,
) -> tuple[Path, dict[str, Any]]:
    source_id = require_text(source, "source_id", "source")
    path = Path(require_text(source, "path", source_id))
    if not path.is_absolute() or not path.is_file():
        fail(f"{source_id} 的来源路径必须是现存绝对文件: {path}")
    reject_old_path(path, source_id)
    expected_hash = require_text(source, "sha256", source_id).casefold()
    if len(expected_hash) != 64:
        fail(f"{source_id}.sha256 必须是 64 位")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        fail(f"{source_id} 来源哈希不匹配: expected={expected_hash}, actual={actual_hash}")
    file_role = require_text(source, "file_role", source_id)
    source_role = require_text(source, "source_role", source_id)
    if file_role not in FILE_ROLES:
        fail(f"{source_id}.file_role 非法: {file_role}")
    if source_role not in SOURCE_ROLES:
        fail(f"{source_id}.source_role 非法: {source_role}")
    if source_role == "test_fixture" and not test_mode:
        fail(f"{source_id} 是 test_fixture，但 test_mode=false")
    for key in ("version_identity", "reason", "neutral_facts", "unused_impact"):
        require_text(source, key, source_id)

    assertion_results: list[dict[str, Any]] = []
    assertions = source.get("json_assertions", {})
    if assertions:
        if file_role != "json" or not isinstance(assertions, dict):
            fail(f"{source_id}.json_assertions 只适用于 JSON 来源且必须是对象")
        data = read_json(path)
        for dotted, expected in assertions.items():
            actual = dotted_get(data, dotted)
            if not close_enough(actual, expected):
                fail(f"{source_id} JSON 断言失败 {dotted}: expected={expected!r}, actual={actual!r}")
            assertion_results.append({"field": dotted, "expected": expected, "actual": actual, "pass": True})

    return path, {
        "source_id": source_id,
        "path": str(path.resolve()),
        "expected_sha256": expected_hash,
        "actual_sha256": actual_hash,
        "file_role": file_role,
        "source_role": source_role,
        "json_assertions": assertion_results,
    }


def load_stream(source: dict[str, Any], path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if source.get("file_role") != "gzip_ndjson" or source.get("source_role") != "cad_entity_stream":
        fail(f"{source.get('source_id')} 的实体流角色或文件角色不匹配")
    rows: dict[str, dict[str, Any]] = {}
    line_count = 0
    expected_source = source.get("expected_source_sha256")
    expected_schema = source.get("expected_schema_version")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_count, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    fail(f"实体流第 {line_count} 行不是合法 JSON: {exc}")
                if not isinstance(row, dict):
                    fail(f"实体流第 {line_count} 行顶层不是对象")
                if expected_source and row.get("source_sha256") != expected_source:
                    fail(f"实体流第 {line_count} 行 source_sha256 不匹配")
                if expected_schema and row.get("schema_version") != expected_schema:
                    fail(f"实体流第 {line_count} 行 schema_version 不匹配")
                entity_handle = row.get("entity_handle")
                if isinstance(entity_handle, str):
                    if entity_handle in rows:
                        fail(f"实体流存在重复 handle: {entity_handle}")
                    rows[entity_handle] = row
    except OSError as exc:
        fail(f"无法解压实体流 {path}: {exc}")
    expected_count = source.get("expected_line_count")
    if expected_count is not None and line_count != expected_count:
        fail(f"实体流行数不匹配: expected={expected_count}, actual={line_count}")
    return rows, {"line_count": line_count, "unique_handle_count": len(rows)}


def validate_entity_assertion(evidence_id: str, assertion: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    handle = require_text(assertion, "entity_handle", f"{evidence_id}.entity_assertion")
    row = rows.get(handle)
    if row is None:
        fail(f"{evidence_id} 引用的实体 handle 不存在: {handle}")
    checks: dict[str, Any] = {"entity_handle": handle}
    for key in ("layer", "entity_type", "geometry_type", "closed"):
        if key in assertion:
            actual = row.get(key)
            expected = assertion[key]
            if not close_enough(actual, expected):
                fail(f"{evidence_id} 实体断言失败 {key}: expected={expected!r}, actual={actual!r}")
            checks[key] = actual
    if "text_contains" in assertion:
        needle = require_text(assertion, "text_contains", f"{evidence_id}.entity_assertion")
        actual_text = row.get("text")
        if not isinstance(actual_text, str) or needle not in actual_text:
            fail(f"{evidence_id} 实体文字不包含 {needle!r}")
        checks["text"] = actual_text
    if "bbox" in assertion:
        tolerance = float(assertion.get("bbox_tolerance", 1e-6))
        if not close_enough(row.get("bbox"), assertion["bbox"], tolerance):
            fail(f"{evidence_id} 实体 bbox 断言失败")
        checks["bbox"] = comparable(row.get("bbox"))
    return checks


def result_scope(scope: dict[str, Any], group: dict[str, Any], dimension: str) -> dict[str, Any]:
    grade = group.get("grade") or "不适用"
    diameter = group.get("diameter_mm")
    dia_text = f"Φ{diameter:g}" if isinstance(diameter, (int, float)) else "无直径"
    specification = group.get("specification") or f"{grade} {dia_text} / {group.get('position', '位置未列')}"
    return {
        "业务专业": "钢筋工程",
        "主要对象": require_text(group, "member_id", group["group_id"]),
        "系统": require_text(group, "component_type", group["group_id"]),
        "楼栋": require_text(scope, "building", "scope"),
        "楼层或区域": require_text(scope, "area", "scope"),
        "规格": specification,
        "输出维度": dimension,
    }


def zero_review(group: dict[str, Any]) -> dict[str, Any] | None:
    block = group.get("zero_evidence")
    if not isinstance(block, dict):
        return None
    return {
        "范围覆盖": require_text(block, "coverage_scope", f"{group['group_id']}.zero_evidence"),
        "来源": require_list(block, "evidence_ids", f"{group['group_id']}.zero_evidence"),
        "规则": require_list(block, "rule_ids", f"{group['group_id']}.zero_evidence"),
        "复核结论": "已确认零值",
        "说明": require_text(block, "note", f"{group['group_id']}.zero_evidence"),
    }


def make_result_item(
    scope: dict[str, Any],
    group: dict[str, Any],
    item_id: str,
    dimension: str,
    name: str,
    value: float | int | None,
    unit: str,
    formula: str,
    precision: str,
) -> dict[str, Any]:
    status = group["status"]
    return {
        "成果项编号": item_id,
        "范围": result_scope(scope, group, dimension),
        "结果": {
            "名称": name,
            "数值": value,
            "数值状态": VALUE_STATUS_CN[status],
            "计算式或说明": formula,
            "精度": precision,
        },
        "单位": unit,
        "来源": list(group["evidence_ids"]),
        "规则": list(group["rule_ids"]),
        "状态": STATUS_CN[status],
        "未决事项": list(group["unresolved_ids"]),
        "试运行结果": bool(group.get("trial_result", False)),
        "零值复核": zero_review(group) if value == 0 else None,
        "钢筋组编号": group["group_id"],
        "构件编号": group["member_id"],
        "钢筋类别": group["bar_category"],
        "位置": group["position"],
        "计量基准": group["quantity_basis"],
        "采用角色": group["adoption_role"],
        "结果用途": "reference_only" if group["adoption_role"] == "reference" else "adopted",
    }


def calculate_group(
    group: dict[str, Any],
    scope: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]],
    rule_map: dict[str, dict[str, Any]],
    unresolved_map: dict[str, dict[str, Any]],
    test_mode: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    group_id = require_text(group, "group_id", "bar_group")
    for key in (
        "physical_object_id", "component_type", "bar_category", "member_id",
        "position", "measurement_basis", "quantity_basis", "adoption_role",
        "status", "method",
    ):
        require_text(group, key, group_id)
    if group["quantity_basis"] not in QUANTITY_BASES:
        fail(f"{group_id}.quantity_basis 非法")
    if group["adoption_role"] not in ADOPTION_ROLES:
        fail(f"{group_id}.adoption_role 非法")
    if group["status"] not in GROUP_STATUSES:
        fail(f"{group_id}.status 非法")
    if group["method"] not in METHODS:
        fail(f"{group_id}.method 非法")

    evidence_ids = require_list(group, "evidence_ids", group_id)
    rule_ids = require_list(group, "rule_ids", group_id)
    unresolved_ids = require_list(group, "unresolved_ids", group_id)
    outputs = group.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        fail(f"{group_id}.outputs 必须是非空对象")
    for evidence_id in evidence_ids:
        if evidence_id not in evidence_map:
            fail(f"{group_id} 引用不存在的证据 {evidence_id}")
    for rule_id in rule_ids:
        if rule_id not in rule_map:
            fail(f"{group_id} 引用不存在的规则 {rule_id}")
    for unresolved_id in unresolved_ids:
        if unresolved_id not in unresolved_map:
            fail(f"{group_id} 引用不存在的未决事项 {unresolved_id}")

    status = group["status"]
    method = group["method"]
    if status == "complete" and unresolved_ids:
        fail(f"{group_id} 为 complete 时不得关联未决事项")
    if status == "partial" and not unresolved_ids:
        fail(f"{group_id} 为 partial 时必须关联未决事项")
    if status in {"unresolved", "not_applicable"} and method != "deferred":
        fail(f"{group_id} 为 {status} 时必须使用 deferred")
    if status in {"complete", "partial"} and method == "deferred":
        fail(f"{group_id} 有数值状态时不得使用 deferred")
    if status == "unresolved" and not unresolved_ids:
        fail(f"{group_id} 为 unresolved 时必须关联未决事项")

    numeric = status in {"complete", "partial"}
    if numeric:
        if not evidence_ids or not rule_ids:
            fail(f"{group_id} 数值结果必须有证据和规则")
        for evidence_id in evidence_ids:
            fact_status = evidence_map[evidence_id].get("fact_status")
            if fact_status not in {"verified", "test_only"}:
                fail(f"{group_id} 数值结果引用了非采用证据 {evidence_id}: {fact_status}")
            if fact_status == "test_only" and (not test_mode or group["adoption_role"] != "reference"):
                fail(f"{group_id} 仅在 test_mode + reference 下可使用测试证据")
        for rule_id in rule_ids:
            rule = rule_map[rule_id]
            if rule.get("decision") != "adopted" or rule.get("maturity") not in {"trial", "formal"}:
                fail(f"{group_id} 数值结果引用了未采用或候选规则 {rule_id}")
        if group["quantity_basis"] == "procurement_reference" and group["adoption_role"] != "reference":
            fail(f"{group_id} 采购参考量不得作为 adopted")

    group_calc: dict[str, Any] = {
        "group_id": group_id,
        "method": method,
        "status": status,
        "adoption_role": group["adoption_role"],
        "quantity_basis": group["quantity_basis"],
    }
    results: list[dict[str, Any]] = []

    if method == "deferred":
        reason = require_text(group, "deferred_reason", group_id)
        dimensions = group.get("deferred_dimensions", ["count", "length", "weight"])
        if not isinstance(dimensions, list) or not dimensions:
            fail(f"{group_id}.deferred_dimensions 必须是非空数组")
        unit_map = {"count": group.get("count_unit", "根"), "length": "m", "weight": "kg"}
        name_map = {"count": "采用根数", "length": "采用中心线展开长度", "weight": "采用理论重量"}
        for dimension in dimensions:
            if dimension not in unit_map or dimension not in outputs:
                fail(f"{group_id} deferred 输出维度或编号缺失: {dimension}")
            results.append(make_result_item(
                scope, group, require_text(outputs, dimension, f"{group_id}.outputs"),
                dimension, name_map[dimension], None, unit_map[dimension], reason,
                "未计算；待决定性输入闭合",
            ))
        group_calc["deferred_reason"] = reason
        return results, group_calc

    count = require_count(group, "count", group_id)
    if count == 0 and not isinstance(group.get("zero_evidence"), dict):
        fail(f"{group_id} 根数为 0 时必须提供 zero_evidence")

    if method == "count_only":
        if set(outputs) != {"count"}:
            fail(f"{group_id} count_only 只能提供 count 输出")
        unit = require_text(group, "count_unit", group_id)
        results.append(make_result_item(
            scope, group, require_text(outputs, "count", f"{group_id}.outputs"),
            "count", "连接或成品数量", count, unit, f"count = {count}", "整数",
        ))
        group_calc.update({"count": count, "count_unit": unit})
        return results, group_calc

    required_outputs = {"count", "length", "weight"}
    if set(outputs) != required_outputs:
        fail(f"{group_id} 普通钢筋组必须同时提供 count/length/weight 输出")
    grade = require_text(group, "grade", group_id)
    diameter = require_number(group, "diameter_mm", group_id, positive=True)

    if method == "explicit_developed_length":
        length_each = require_number(group, "developed_length_each_m", group_id, positive=True)
        length_basis = require_text(group, "developed_length_basis", group_id)
        length_formula = f"L = {count} × {length_each:.9g}（单根中心线展开长度；{length_basis}）"
        additions: list[dict[str, Any]] = []
    else:
        base = require_number(group, "base_length_each_m", group_id, positive=True)
        additions_raw = require_list(group, "additions_each_m", group_id)
        additions = []
        addition_total = 0.0
        for index, addition in enumerate(additions_raw):
            if not isinstance(addition, dict):
                fail(f"{group_id}.additions_each_m[{index}] 必须是对象")
            name = require_text(addition, "name", f"{group_id}.additions_each_m[{index}]")
            value = require_number(addition, "length_m", f"{group_id}.additions_each_m[{index}]")
            if value < 0:
                fail(f"{group_id} 的附加长度不得为负")
            require_text(addition, "basis", f"{group_id}.additions_each_m[{index}]")
            addition_total += value
            additions.append({"name": name, "length_m": value, "basis": addition["basis"]})
        length_each = base + addition_total
        length_formula = f"L = {count} × ({base:.9g} + {addition_total:.9g})（直段与显式附加长度）"

    unit_mass_method = require_text(group, "unit_mass_method", group_id)
    if unit_mass_method == "geometric_density":
        density = require_number(group, "density_kg_m3", group_id, positive=True)
        basis = require_text(group, "density_basis", group_id)
        unit_mass = math.pi / 4.0 * (diameter / 1000.0) ** 2 * density
        unit_formula = f"π/4 × ({diameter:g}/1000)² × {density:g}"
    elif unit_mass_method == "explicit_unit_mass":
        unit_mass = require_number(group, "unit_mass_kg_m", group_id, positive=True)
        basis = require_text(group, "unit_mass_basis", group_id)
        unit_formula = f"显式理论单位质量 {unit_mass:.12g} kg/m"
    else:
        fail(f"{group_id}.unit_mass_method 非法")

    total_length = count * length_each
    weight = total_length * unit_mass
    count_value: int | float = count
    length_value = round(total_length, 6)
    weight_value = round(weight, 6)
    results.extend([
        make_result_item(
            scope, group, require_text(outputs, "count", f"{group_id}.outputs"),
            "count", "钢筋根数", count_value, "根", f"count = {count}", "整数",
        ),
        make_result_item(
            scope, group, require_text(outputs, "length", f"{group_id}.outputs"),
            "length", "钢筋中心线展开长度", length_value, "m", length_formula,
            "计算保留 0.000001 m；展示精度另行约定",
        ),
        make_result_item(
            scope, group, require_text(outputs, "weight", f"{group_id}.outputs"),
            "weight", "钢筋理论重量", weight_value, "kg",
            f"W = {total_length:.12g} × {unit_mass:.12g}；单位质量={unit_formula}",
            "计算保留 0.000001 kg；展示精度另行约定",
        ),
    ])
    group_calc.update({
        "grade": grade,
        "diameter_mm": diameter,
        "count": count,
        "developed_length_each_m": length_each,
        "additions_each_m": additions,
        "total_length_m": total_length,
        "unit_mass_method": unit_mass_method,
        "unit_mass_kg_m": unit_mass,
        "unit_mass_basis": basis,
        "weight_kg": weight,
    })
    return results, group_calc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--result-out", required=True, type=Path)
    parser.add_argument("--evidence-out", required=True, type=Path)
    parser.add_argument("--detail-out", required=True, type=Path)
    args = parser.parse_args()

    try:
        manifest_path = args.manifest.resolve()
        manifest = read_json(manifest_path)
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            fail(f"manifest_version 必须为 {MANIFEST_VERSION}")
        case_id = require_text(manifest, "case_id", "manifest")
        test_mode = manifest.get("test_mode")
        if not isinstance(test_mode, bool):
            fail("test_mode 必须是布尔值")
        scope = manifest.get("scope")
        if not isinstance(scope, dict):
            fail("scope 必须是对象")
        for key in ("project", "building", "area", "drawing_type", "drawing_version", "output_dimension"):
            require_text(scope, key, "scope")

        readonly_roots_raw = require_list(manifest, "readonly_roots", "manifest")
        readonly_roots: list[Path] = []
        for value in readonly_roots_raw:
            root = Path(value) if isinstance(value, str) else Path("")
            if not isinstance(value, str) or not root.is_absolute() or not root.is_dir():
                fail(f"readonly_roots 必须列出现存绝对目录: {value!r}")
            readonly_roots.append(root.resolve())
        for output in (args.result_out, args.evidence_out, args.detail_out):
            output_abs = output.resolve()
            for root in readonly_roots:
                if is_within(output_abs, root):
                    fail(f"输出路径位于只读项目根内: {output_abs}")

        sources = unique_index(manifest.get("sources"), "source_id", "sources")
        evidence_map = unique_index(manifest.get("evidence"), "evidence_id", "evidence")
        rule_map = unique_index(manifest.get("rules"), "rule_id", "rules")
        unresolved_map = unique_index(manifest.get("unresolved"), "unresolved_id", "unresolved")
        groups = unique_index(manifest.get("bar_groups"), "group_id", "bar_groups")
        if not groups:
            fail("bar_groups 至少需要一项")

        source_paths: dict[str, Path] = {}
        source_checks: list[dict[str, Any]] = []
        stream_rows: dict[str, dict[str, dict[str, Any]]] = {}
        stream_stats: dict[str, dict[str, Any]] = {}
        for source_id, source in sources.items():
            path, check = validate_source(source, test_mode)
            source_paths[source_id] = path
            source_checks.append(check)
            if source.get("source_role") == "cad_entity_stream":
                rows, stats = load_stream(source, path)
                stream_rows[source_id] = rows
                stream_stats[source_id] = stats

        entity_checks: list[dict[str, Any]] = []
        for evidence_id, evidence in evidence_map.items():
            source_id = require_text(evidence, "source_id", evidence_id)
            if source_id not in sources:
                fail(f"{evidence_id} 引用不存在的来源 {source_id}")
            evidence_class = require_text(evidence, "evidence_class", evidence_id)
            fact_status = require_text(evidence, "fact_status", evidence_id)
            if evidence_class not in EVIDENCE_CLASSES:
                fail(f"{evidence_id}.evidence_class 非法")
            if fact_status not in FACT_STATUSES:
                fail(f"{evidence_id}.fact_status 非法")
            if fact_status == "test_only" and (not test_mode or sources[source_id].get("source_role") != "test_fixture"):
                fail(f"{evidence_id} 的 test_only 状态与来源或 test_mode 不兼容")
            for key in ("neutral_fact", "locator", "source_identity", "extraction_method", "quality_status", "limitation"):
                require_text(evidence, key, evidence_id)
            object_keys = evidence.get("object_keys", [])
            if not isinstance(object_keys, list) or any(not isinstance(v, str) or not v for v in object_keys):
                fail(f"{evidence_id}.object_keys 必须是文本数组")
            assertion = evidence.get("entity_assertion")
            if assertion is not None:
                if not isinstance(assertion, dict) or source_id not in stream_rows:
                    fail(f"{evidence_id}.entity_assertion 必须绑定 cad_entity_stream")
                handle = require_text(assertion, "entity_handle", f"{evidence_id}.entity_assertion")
                if f"handle:{handle}" not in object_keys:
                    fail(f"{evidence_id}.object_keys 未包含 handle:{handle}")
                checked = validate_entity_assertion(evidence_id, assertion, stream_rows[source_id])
                checked.update({"evidence_id": evidence_id, "source_id": source_id})
                entity_checks.append(checked)

        for rule_id, rule in rule_map.items():
            decision = require_text(rule, "decision", rule_id)
            maturity = require_text(rule, "maturity", rule_id)
            if decision not in RULE_DECISIONS:
                fail(f"{rule_id}.decision 非法")
            if maturity not in RULE_MATURITY:
                fail(f"{rule_id}.maturity 非法")
            if decision == "adopted" and maturity in {"candidate", "disabled"}:
                fail(f"{rule_id} 为 {maturity} 规则，不得 adopted")
            for key in ("name", "source", "execution", "locator"):
                require_text(rule, key, rule_id)

        for unresolved_id, item in unresolved_map.items():
            for key in ("type", "description", "impact", "required_input", "handling"):
                require_text(item, key, unresolved_id)
            related = require_list(item, "related_result_ids", unresolved_id)
            if any(not isinstance(v, str) or not v for v in related):
                fail(f"{unresolved_id}.related_result_ids 必须是文本数组")

        results: list[dict[str, Any]] = []
        calculations: list[dict[str, Any]] = []
        detail_rows: list[dict[str, Any]] = []
        output_ids: set[str] = set()
        physical_signatures: set[tuple[str, str, str, str, str]] = set()
        for group_id, group in groups.items():
            signature = (
                require_text(group, "physical_object_id", group_id),
                require_text(group, "member_id", group_id),
                require_text(group, "position", group_id),
                str(group.get("grade")),
                str(group.get("diameter_mm")),
            )
            if signature in physical_signatures:
                fail(f"钢筋组物理身份重复，不能靠改 group_id 重复计算: {group_id}")
            physical_signatures.add(signature)
            group_results, calc = calculate_group(group, scope, evidence_map, rule_map, unresolved_map, test_mode)
            for item in group_results:
                item_id = item["成果项编号"]
                if item_id in output_ids:
                    fail(f"成果项编号重复: {item_id}")
                output_ids.add(item_id)
                results.append(item)
            calculations.append(calc)
            detail_rows.append({
                "group_id": group_id,
                "member_id": group["member_id"],
                "component_type": group["component_type"],
                "bar_category": group["bar_category"],
                "position": group["position"],
                "grade": calc.get("grade", group.get("grade", "")),
                "diameter_mm": calc.get("diameter_mm", group.get("diameter_mm", "")),
                "method": calc["method"],
                "status": calc["status"],
                "adoption_role": calc["adoption_role"],
                "quantity_basis": calc["quantity_basis"],
                "count": calc.get("count", ""),
                "developed_length_each_m": calc.get("developed_length_each_m", ""),
                "total_length_m": calc.get("total_length_m", ""),
                "unit_mass_kg_m": calc.get("unit_mass_kg_m", ""),
                "weight_kg": calc.get("weight_kg", ""),
                "unresolved_ids": "|".join(group["unresolved_ids"]),
            })

        for unresolved_id, item in unresolved_map.items():
            for result_id in item["related_result_ids"]:
                if result_id not in output_ids:
                    fail(f"{unresolved_id} 关联不存在的成果项 {result_id}")

        manifest_sha = sha256_file(manifest_path)
        calculator_sha = sha256_file(Path(__file__).resolve())
        source_selections = []
        for source_id, source in sources.items():
            source_selections.append({
                "资料编号": source_id,
                "路径": str(source_paths[source_id].resolve()),
                "资料类型": source["file_role"],
                "版本身份": source["version_identity"],
                "选择结论": "已选择",
                "原因": source["reason"],
                "拟提取中性事实": source["neutral_facts"],
                "未使用影响": source["unused_impact"],
                "来源角色": source["source_role"],
                "SHA256": source["sha256"],
            })
        evidence_catalog = []
        for evidence_id, evidence in evidence_map.items():
            evidence_catalog.append({
                "证据编号": evidence_id,
                "来源类型": evidence["evidence_class"],
                "资料编号": evidence["source_id"],
                "中性事实": evidence["neutral_fact"],
                "定位": evidence["locator"],
                "来源身份": evidence["source_identity"],
                "提取方式": evidence["extraction_method"],
                "质量状态": evidence["quality_status"],
                "限制": evidence["limitation"],
                "事实状态": evidence["fact_status"],
                "对象键": evidence.get("object_keys", []),
                "缓存编号": evidence.get("cache_id"),
            })
        rule_catalog = []
        for rule_id, rule in rule_map.items():
            rule_catalog.append({
                "规则编号": rule_id,
                "规则名称": rule["name"],
                "规则来源": rule["source"],
                "本次判定": RULE_DECISION_CN[rule["decision"]],
                "成熟状态": RULE_MATURITY_CN[rule["maturity"]],
                "执行结果": rule["execution"],
                "证据位置": rule["locator"],
            })
        unresolved_catalog = []
        type_map = {
            "missing_source": "缺资料", "scope": "范围未定", "source_conflict": "来源冲突",
            "rule_conflict": "规则冲突", "quality": "质量问题", "user_decision": "需用户确认",
        }
        handling_map = {
            "stop": "停止受影响计算", "keep_proven": "保留已证实部分", "await_decision": "等待裁决",
        }
        for unresolved_id, item in unresolved_map.items():
            if item["type"] not in type_map or item["handling"] not in handling_map:
                fail(f"{unresolved_id} 的类型或处理方式非法")
            unresolved_catalog.append({
                "事项编号": unresolved_id,
                "类型": type_map[item["type"]],
                "关联成果项": item["related_result_ids"],
                "说明": item["description"],
                "影响": item["impact"],
                "所需输入": item["required_input"],
                "当前处理": handling_map[item["handling"]],
            })

        result_doc = {
            "契约版本": RESULT_VERSION,
            "任务范围": {
                "业务专业": "钢筋工程",
                "主要物理对象": scope.get("main_object", "构件钢筋组"),
                "系统": scope.get("system", "混凝土结构配筋"),
                "楼栋": scope["building"],
                "楼层或区域": scope["area"],
                "图纸类型": scope["drawing_type"],
                "图纸版本": scope["drawing_version"],
                "输出维度": scope["output_dimension"],
                "纳入项": scope.get("included", []),
                "排除项": scope.get("excluded", []),
            },
            "资料选择": source_selections,
            "证据目录": evidence_catalog,
            "本次规则清单": rule_catalog,
            "工程量结果": results,
            "整体未决事项": unresolved_catalog,
            "说明": manifest.get("note", "钢筋候选确定性复算；不解释平法语义。"),
            "案例编号": case_id,
            "生成器身份": {
                "运行清单SHA256": manifest_sha,
                "计算器SHA256": calculator_sha,
                "计算器": str(Path(__file__).resolve()),
            },
        }
        evidence_doc = {
            "schema_version": EVIDENCE_VERSION,
            "case_id": case_id,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha,
            "calculator_path": str(Path(__file__).resolve()),
            "calculator_sha256": calculator_sha,
            "test_mode": test_mode,
            "readonly_roots": [str(path) for path in readonly_roots],
            "source_checks": source_checks,
            "stream_stats": stream_stats,
            "entity_checks": entity_checks,
            "bar_group_calculations": calculations,
            "result_item_ids": sorted(output_ids),
        }

        write_json(args.result_out.resolve(), result_doc)
        write_json(args.evidence_out.resolve(), evidence_doc)
        args.detail_out.resolve().parent.mkdir(parents=True, exist_ok=True)
        with args.detail_out.resolve().open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(detail_rows[0]))
            writer.writeheader()
            writer.writerows(detail_rows)

        print(json.dumps({
            "status": "PASS",
            "case_id": case_id,
            "result_items": len(results),
            "bar_groups": len(groups),
            "result_out": str(args.result_out.resolve()),
            "evidence_out": str(args.evidence_out.resolve()),
            "detail_out": str(args.detail_out.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
