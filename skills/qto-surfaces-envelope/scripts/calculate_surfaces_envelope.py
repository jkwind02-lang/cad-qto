#!/usr/bin/env python3
"""面层与建筑围护确定性复算器。

脚本只复算已经由项目证据锁定的表面、构造层和洞口事实；不识别图纸语义，
不决定材料适用范围，也不把轴线参考面积自动提升为完整工程量。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "qto-surfaces-envelope-manifest/1.0"
RESULT_VERSION = "qto-result-minimum/1.0"
ALLOWED_METHODS = {
    "area_direct",
    "horizontal_net",
    "rectangle_net",
    "sloped_from_projection",
    "deferred",
}
ALLOWED_OBJECT_TYPES = {
    "floor_finish",
    "wall_finish",
    "ceiling_finish",
    "roof_covering",
    "waterproofing",
    "insulation",
    "facade_panel",
    "curtain_wall_panel",
    "other_surface",
}
ALLOWED_SCOPE_STATUS = {"complete", "partial", "unresolved", "not_applicable"}
ALLOWED_ADOPTION_ROLES = {"adopted", "reference"}
ALLOWED_QUANTITY_ROLES = {"physical_area", "bill_quantity_candidate"}
ALLOWED_FACT_STATUS = {"verified", "reference", "candidate", "test_only"}
ALLOWED_SOURCE_ROLES = {
    "project_drawing",
    "cad_entity_stream",
    "cad_metadata",
    "project_document",
    "approved_plan",
    "coverage_report",
    "calculation_evidence",
    "standard_reference",
    "test_fixture",
}
ALLOWED_FILE_ROLES = {
    "dwg", "dxf", "pdf", "json", "gzip_ndjson", "spreadsheet",
    "document", "csv", "text", "image",
}
ALLOWED_EVIDENCE_CLASSES = {
    "source_identity",
    "surface_geometry",
    "opening_union",
    "layer_assignment",
    "material_property",
    "slope_geometry",
    "rule_clause",
    "plan_approved",
    "coverage_complete",
    "requirement",
    "visual_geometry",
}
ALLOWED_RULE_DECISIONS = {"采用", "不适用", "证据不足"}
ALLOWED_RULE_MATURITY = {"候选", "试运行", "正式", "停用"}
ALLOWED_UNRESOLVED_TYPES = {"缺资料", "范围未定", "来源冲突", "规则冲突", "质量问题", "需用户确认"}
ALLOWED_UNRESOLVED_ACTIONS = {"停止受影响计算", "保留已证实部分", "等待裁决"}
SOURCE_ROLE_FILE_ROLES = {
    "project_drawing": {"dwg", "dxf"},
    "cad_entity_stream": {"gzip_ndjson"},
    "cad_metadata": {"json"},
    "project_document": {"pdf", "spreadsheet", "document", "csv", "text", "image", "json"},
    "approved_plan": {"pdf", "spreadsheet", "document", "text", "image"},
    "coverage_report": {"pdf", "spreadsheet", "document", "csv", "text", "image", "json"},
    "calculation_evidence": {"json", "csv", "text"},
    "standard_reference": {"pdf", "spreadsheet", "document", "csv", "text", "image"},
    "test_fixture": {"text"},
}
SOURCE_ROLE_EVIDENCE_CLASSES = {
    "project_drawing": {"source_identity", "surface_geometry", "opening_union", "layer_assignment", "material_property", "slope_geometry", "requirement", "visual_geometry"},
    "cad_entity_stream": {"source_identity", "surface_geometry", "opening_union", "layer_assignment", "material_property", "slope_geometry", "requirement", "visual_geometry"},
    "cad_metadata": {"source_identity"},
    "project_document": ALLOWED_EVIDENCE_CLASSES,
    "approved_plan": {"plan_approved", "requirement", "layer_assignment"},
    "coverage_report": {"coverage_complete", "source_identity"},
    "calculation_evidence": {"source_identity", "surface_geometry", "opening_union", "slope_geometry", "coverage_complete"},
    "standard_reference": {"rule_clause", "requirement", "material_property"},
    "test_fixture": ALLOWED_EVIDENCE_CLASSES,
}
CAD_METADATA_ASSERTIONS = {
    "metadata.source_sha256",
    "entity_stream_sha256",
    "metadata.adapter",
    "metadata.schema_version",
    "source_unit",
    "integrity.conservation_ok",
}
PARTIAL_FORBIDDEN_WORDS = {'全部', '全量', '完整', '总量'}
LEGACY_FRAGMENTS = (
    "\\skills-disabled\\",
    "\\旧skill只读归档\\",
    "\\阶段1_归档与停用\\",
    "construction-qto-legacy",
    "construction-qto-preexisting-backup",
    "construction-qto-retired-source",
    "\\.qto\\methods\\",
)


class CalculationError(RuntimeError):
    """输入事实、证据绑定或计算约束不成立。"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalculationError(f"{label}必须是非空文本")
    return value.strip()


def require_number(value: Any, label: str, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalculationError(f"{label}必须是有限数值")
    number = float(value)
    if number < 0 or (not allow_zero and number == 0):
        raise CalculationError(f"{label}必须{'非负' if allow_zero else '大于0'}")
    return number


def require_sha256(value: Any, label: str) -> str:
    text = require_text(value, label).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise CalculationError(f"{label}不是有效SHA-256")
    return text


def require_id(value: Any, prefix: str, label: str) -> str:
    text = require_text(value, label)
    if not re.fullmatch(rf"{re.escape(prefix)}[A-Za-z0-9._-]+", text):
        raise CalculationError(f"{label}必须以{prefix}开头并仅含ASCII字母、数字、点、下划线或连字符")
    return text


def unique_map(items: Any, key: str, prefix: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise CalculationError(f"{label}必须是数组")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CalculationError(f"{label}[{index}]必须是对象")
        identifier = require_id(item.get(key), prefix, f"{label}[{index}].{key}")
        if identifier in result:
            raise CalculationError(f"{label}存在重复编号: {identifier}")
        result[identifier] = item
    return result


def require_refs(value: Any, known: dict[str, Any], label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise CalculationError(f"{label}必须是数组")
    refs = [require_text(item, label) for item in value]
    if not allow_empty and not refs:
        raise CalculationError(f"{label}不能为空")
    if len(refs) != len(set(refs)):
        raise CalculationError(f"{label}存在重复引用")
    missing = sorted(set(refs) - set(known))
    if missing:
        raise CalculationError(f"{label}引用不存在的编号: {', '.join(missing)}")
    return refs


def dotted_value(data: Any, dotted_path: str, label: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise CalculationError(f"{label}不存在字段: {dotted_path}")
        current = current[part]
    return current


def load_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"非标准JSON数值: {value}")

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CalculationError(f"无法读取JSON {path}: {exc}") from exc


def reject_legacy_runtime(path: Path, label: str) -> None:
    lowered = str(path).replace("/", "\\").lower()
    if any(fragment in lowered for fragment in LEGACY_FRAGMENTS):
        raise CalculationError(f"{label}不得引用旧Skill、旧归档或旧项目方法: {path}")


def resolve_source_path(value: Any, base_dir: Path, label: str) -> Path:
    raw = Path(require_text(value, label)).expanduser()
    path = (base_dir / raw).resolve() if not raw.is_absolute() else raw.resolve()
    reject_legacy_runtime(path, label)
    if not path.is_file():
        raise CalculationError(f"{label}不存在: {path}")
    return path


def validate_file_role(path: Path, file_role: str, source_role: str, label: str) -> None:
    if file_role not in ALLOWED_FILE_ROLES:
        raise CalculationError(f"{label}.file_role非法: {file_role}")
    if source_role not in ALLOWED_SOURCE_ROLES:
        raise CalculationError(f"{label}.source_role非法: {source_role}")
    if file_role not in SOURCE_ROLE_FILE_ROLES[source_role]:
        raise CalculationError(f"{label}物理文件角色与语义角色不匹配: {file_role}/{source_role}")
    with path.open("rb") as handle:
        head = handle.read(16)
    suffix = path.suffix.casefold()
    valid = True
    if file_role == "dwg":
        valid = suffix == ".dwg" and bool(re.fullmatch(rb"AC\d{4}", head[:6]))
    elif file_role == "dxf":
        with path.open("rb") as handle:
            valid = suffix == ".dxf" and b"SECTION" in handle.read(4096).upper()
    elif file_role == "pdf":
        valid = suffix == ".pdf" and head.startswith(b"%PDF-")
    elif file_role == "json":
        valid = suffix == ".json"
    elif file_role == "gzip_ndjson":
        valid = suffix == ".gz" and head.startswith(b"\x1f\x8b")
    elif file_role == "spreadsheet":
        valid = suffix in {".xlsx", ".xls", ".xlsm"} and (
            head.startswith(b"PK\x03\x04") or head.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
        )
    elif file_role == "document":
        valid = suffix == ".docx" and head.startswith(b"PK\x03\x04")
    elif file_role == "csv":
        valid = suffix in {".csv", ".tsv"}
    elif file_role == "text":
        valid = suffix in {".txt", ".md", ".yaml", ".yml", ".json"}
    elif file_role == "image":
        valid = suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
    if not valid:
        raise CalculationError(f"{label}扩展名或文件签名与file_role不一致: {path}")


def load_sources(source_rows: dict[str, dict[str, Any]], base_dir: Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for source_id, row in source_rows.items():
        selection = require_text(row.get("selection"), f"{source_id}.selection")
        if selection not in {"selected", "excluded", "uncertain"}:
            raise CalculationError(f"{source_id}.selection非法")
        source_type = require_text(row.get("source_type"), f"{source_id}.source_type")
        reason = require_text(row.get("reason"), f"{source_id}.reason")
        neutral_facts = require_text(row.get("neutral_facts"), f"{source_id}.neutral_facts")
        not_used_impact = require_text(row.get("not_used_impact"), f"{source_id}.not_used_impact")
        file_role = require_text(row.get("file_role"), f"{source_id}.file_role")
        source_role = require_text(row.get("source_role"), f"{source_id}.source_role")
        path = resolve_source_path(row.get("path"), base_dir, f"{source_id}.path")
        validate_file_role(path, file_role, source_role, source_id)
        actual_sha = sha256_file(path)
        expected_sha = require_sha256(row.get("sha256"), f"{source_id}.sha256")
        if actual_sha != expected_sha:
            raise CalculationError(f"{source_id}文件哈希不一致")
        parsed = load_json(path) if file_role == "json" else None
        if source_role == "cad_metadata":
            assertions = row.get("assertions")
            if not isinstance(assertions, dict) or set(assertions) != CAD_METADATA_ASSERTIONS:
                raise CalculationError(f"{source_id}必须完整声明CAD元数据身份断言")
            for dotted, expected in assertions.items():
                if dotted_value(parsed, dotted, source_id) != expected:
                    raise CalculationError(f"{source_id}身份断言失败: {dotted}")
        elif file_role == "json" and isinstance(row.get("assertions"), dict):
            for dotted, expected in row["assertions"].items():
                if dotted_value(parsed, require_text(dotted, f"{source_id}.assertions"), source_id) != expected:
                    raise CalculationError(f"{source_id}JSON断言失败: {dotted}")
        loaded[source_id] = {
            "row": row,
            "path": path,
            "sha256": actual_sha,
            "selection": selection,
            "source_type": source_type,
            "reason": reason,
            "neutral_facts": neutral_facts,
            "not_used_impact": not_used_impact,
            "file_role": file_role,
            "source_role": source_role,
            "parsed": parsed,
            "entity_handles": set(),
        }

    for source_id, source in loaded.items():
        if source["source_role"] != "cad_entity_stream":
            continue
        binding = source["row"].get("binding")
        if not isinstance(binding, dict):
            raise CalculationError(f"{source_id}.binding必须是对象")
        drawing_id = require_text(binding.get("drawing_source_id"), f"{source_id}.binding.drawing_source_id")
        metadata_id = require_text(binding.get("metadata_source_id"), f"{source_id}.binding.metadata_source_id")
        expected_rows = int(require_number(binding.get("expected_row_count"), f"{source_id}.binding.expected_row_count", allow_zero=False))
        if drawing_id not in loaded or loaded[drawing_id]["source_role"] != "project_drawing":
            raise CalculationError(f"{source_id}未绑定合法项目图纸")
        if metadata_id not in loaded or loaded[metadata_id]["source_role"] != "cad_metadata":
            raise CalculationError(f"{source_id}未绑定合法CAD元数据")
        metadata = loaded[metadata_id]["parsed"]
        drawing_sha = loaded[drawing_id]["sha256"]
        if dotted_value(metadata, "metadata.source_sha256", metadata_id).lower() != drawing_sha:
            raise CalculationError(f"{metadata_id}的图纸哈希与{drawing_id}不一致")
        if dotted_value(metadata, "entity_stream_sha256", metadata_id).lower() != source["sha256"]:
            raise CalculationError(f"{metadata_id}的实体流哈希与{source_id}不一致")
        schema_version = require_text(dotted_value(metadata, "metadata.schema_version", metadata_id), f"{metadata_id}.schema")
        handles: set[str] = set()
        row_count = 0
        try:
            with gzip.open(source["path"], "rt", encoding="utf-8-sig") as stream:
                for line_number, raw in enumerate(stream, 1):
                    if not raw.strip():
                        continue
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        raise CalculationError(f"{source_id}第{line_number}行不是对象")
                    if require_sha256(row.get("source_sha256"), f"{source_id}第{line_number}行.source_sha256") != drawing_sha:
                        raise CalculationError(f"{source_id}第{line_number}行图纸身份不一致")
                    if require_text(row.get("schema_version"), f"{source_id}第{line_number}行.schema_version") != schema_version:
                        raise CalculationError(f"{source_id}第{line_number}行schema不一致")
                    handle = row.get("entity_handle")
                    if isinstance(handle, str) and handle.strip():
                        handles.add(handle.strip().upper())
                    row_count += 1
        except (OSError, EOFError, json.JSONDecodeError) as exc:
            raise CalculationError(f"无法审计中性实体流{source_id}: {exc}") from exc
        if row_count != expected_rows:
            raise CalculationError(f"{source_id}实体流行数不守恒: actual={row_count} expected={expected_rows}")
        source["entity_handles"] = handles
        source["audited_row_count"] = row_count
    return loaded


def validate_task(task: Any) -> dict[str, str]:
    if not isinstance(task, dict):
        raise CalculationError("task必须是对象")
    fields = (
        "project_id", "business_discipline", "main_objects", "system", "building",
        "zone", "drawing_type", "drawing_version", "output_dimension",
    )
    result = {field: require_text(task.get(field), f"task.{field}") for field in fields}
    rule_status = require_text(task.get("applicable_rule_status"), "task.applicable_rule_status")
    if rule_status not in {"confirmed", "unresolved"}:
        raise CalculationError("task.applicable_rule_status非法")
    result["applicable_rule_status"] = rule_status
    return result


def validate_evidence(
    evidence_rows: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    validated: dict[str, dict[str, Any]] = {}
    for evidence_id, row in evidence_rows.items():
        source_id = require_text(row.get("source_id"), f"{evidence_id}.source_id")
        if source_id not in sources or sources[source_id]["selection"] != "selected":
            raise CalculationError(f"{evidence_id}必须引用已选择资料")
        evidence_class = require_text(row.get("evidence_class"), f"{evidence_id}.evidence_class")
        fact_status = require_text(row.get("fact_status"), f"{evidence_id}.fact_status")
        if evidence_class not in ALLOWED_EVIDENCE_CLASSES:
            raise CalculationError(f"{evidence_id}.evidence_class非法")
        if fact_status not in ALLOWED_FACT_STATUS:
            raise CalculationError(f"{evidence_id}.fact_status非法")
        if evidence_class not in SOURCE_ROLE_EVIDENCE_CLASSES[sources[source_id]["source_role"]]:
            raise CalculationError(f"{evidence_id}证据类别与资料语义角色不匹配")
        locator = require_text(row.get("locator"), f"{evidence_id}.locator")
        fact = require_text(row.get("fact"), f"{evidence_id}.fact")
        extraction = require_text(row.get("extraction"), f"{evidence_id}.extraction")
        quality = require_text(row.get("quality"), f"{evidence_id}.quality")
        object_keys = row.get("object_keys")
        if not isinstance(object_keys, list) or not object_keys:
            raise CalculationError(f"{evidence_id}.object_keys必须是非空数组")
        object_keys = [require_text(value, f"{evidence_id}.object_keys") for value in object_keys]
        handles_raw = row.get("entity_handles", [])
        if not isinstance(handles_raw, list):
            raise CalculationError(f"{evidence_id}.entity_handles必须是数组")
        handles = [require_text(value, f"{evidence_id}.entity_handles").upper() for value in handles_raw]
        if handles:
            if sources[source_id]["source_role"] != "cad_entity_stream":
                raise CalculationError(f"{evidence_id}只有中性实体流证据可声明entity_handles")
            missing = sorted(set(handles) - sources[source_id]["entity_handles"])
            if missing:
                raise CalculationError(f"{evidence_id}引用不存在的实体句柄: {', '.join(missing)}")
        limitation = require_text(
            row.get("limitation", "仅证明所述中性事实，不自动证明专业适用范围或成果完整性"),
            f"{evidence_id}.limitation",
        )
        validated[evidence_id] = {
            "row": row,
            "source_id": source_id,
            "evidence_class": evidence_class,
            "fact_status": fact_status,
            "locator": locator,
            "fact": fact,
            "extraction": extraction,
            "quality": quality,
            "limitation": limitation,
            "object_keys": object_keys,
            "entity_handles": handles,
        }
    return validated


def validate_rules(rule_rows: dict[str, dict[str, Any]]) -> None:
    for rule_id, row in rule_rows.items():
        decision = require_text(row.get("decision"), f"{rule_id}.decision")
        maturity = require_text(row.get("maturity"), f"{rule_id}.maturity")
        if decision not in ALLOWED_RULE_DECISIONS or maturity not in ALLOWED_RULE_MATURITY:
            raise CalculationError(f"{rule_id}规则判定或成熟状态非法")
        if decision == "采用" and maturity in {"候选", "停用"}:
            raise CalculationError(f"{rule_id}为{maturity}规则，不得采用")
        for field in ("name", "source", "execution", "evidence_locator"):
            require_text(row.get(field), f"{rule_id}.{field}")


def validate_unresolved(rows: dict[str, dict[str, Any]]) -> None:
    for unresolved_id, row in rows.items():
        if require_text(row.get("type"), f"{unresolved_id}.type") not in ALLOWED_UNRESOLVED_TYPES:
            raise CalculationError(f"{unresolved_id}.type非法")
        if require_text(row.get("action"), f"{unresolved_id}.action") not in ALLOWED_UNRESOLVED_ACTIONS:
            raise CalculationError(f"{unresolved_id}.action非法")
        for field in ("description", "impact", "required_input"):
            require_text(row.get(field), f"{unresolved_id}.{field}")


def numeric_input(inputs: dict[str, Any], key: str, item_id: str, default: float | None = None) -> float:
    if key not in inputs:
        if default is None:
            raise CalculationError(f"{item_id}.inputs缺少{key}")
        return default
    return require_number(inputs[key], f"{item_id}.inputs.{key}")


def calculate_item(item_id: str, row: dict[str, Any]) -> tuple[float | None, str, dict[str, float]]:
    method = require_text(row.get("method"), f"{item_id}.method")
    if method not in ALLOWED_METHODS:
        raise CalculationError(f"{item_id}.method非法")
    inputs = row.get("inputs", {})
    if not isinstance(inputs, dict):
        raise CalculationError(f"{item_id}.inputs必须是对象")
    if method == "deferred":
        if inputs:
            raise CalculationError(f"{item_id}待定项不得携带数值输入")
        return None, require_text(row.get("deferred_reason"), f"{item_id}.deferred_reason"), {}
    if method == "area_direct":
        area = numeric_input(inputs, "area_m2", item_id)
        return area, f"直接设计表面积={area:.6f}", {"gross_area_m2": area, "opening_union_area_m2": 0.0}
    if method == "horizontal_net":
        gross = numeric_input(inputs, "gross_area_m2", item_id)
        opening = numeric_input(inputs, "opening_union_area_m2", item_id, 0.0)
        other = numeric_input(inputs, "other_deductions_area_m2", item_id, 0.0)
        additions = numeric_input(inputs, "additions_area_m2", item_id, 0.0)
        value = gross - opening - other + additions
        formula = f"{gross:.6f}-{opening:.6f}-{other:.6f}+{additions:.6f}"
        return value, formula, {"gross_area_m2": gross, "opening_union_area_m2": opening, "other_deductions_area_m2": other, "additions_area_m2": additions}
    if method == "rectangle_net":
        length = numeric_input(inputs, "length_m", item_id)
        height = numeric_input(inputs, "height_m", item_id)
        side_factor = numeric_input(inputs, "side_factor", item_id, 1.0)
        if side_factor not in {1.0, 2.0}:
            raise CalculationError(f"{item_id}.inputs.side_factor只允许1或2")
        opening = numeric_input(inputs, "opening_union_area_m2", item_id, 0.0)
        other = numeric_input(inputs, "other_deductions_area_m2", item_id, 0.0)
        additions = numeric_input(inputs, "reveal_or_return_area_m2", item_id, 0.0)
        gross = length * height * side_factor
        value = gross - opening - other + additions
        formula = f"{length:.6f}×{height:.6f}×{side_factor:.0f}-{opening:.6f}-{other:.6f}+{additions:.6f}"
        return value, formula, {"gross_area_m2": gross, "opening_union_area_m2": opening, "other_deductions_area_m2": other, "additions_area_m2": additions}
    projected = numeric_input(inputs, "projected_area_m2", item_id)
    slope = numeric_input(inputs, "slope_ratio", item_id)
    opening = numeric_input(inputs, "opening_union_area_m2", item_id, 0.0)
    other = numeric_input(inputs, "other_deductions_area_m2", item_id, 0.0)
    additions = numeric_input(inputs, "additions_area_m2", item_id, 0.0)
    gross = projected * math.sqrt(1.0 + slope * slope)
    value = gross - opening - other + additions
    formula = f"{projected:.6f}×√(1+{slope:.6f}²)-{opening:.6f}-{other:.6f}+{additions:.6f}"
    return value, formula, {"gross_area_m2": gross, "opening_union_area_m2": opening, "other_deductions_area_m2": other, "additions_area_m2": additions, "slope_ratio": slope}


def build_result_item(
    item_id: str,
    row: dict[str, Any],
    task: dict[str, str],
    evidence: dict[str, dict[str, Any]],
    rules: dict[str, dict[str, Any]],
    unresolved: dict[str, dict[str, Any]],
    seen_identities: set[tuple[str, ...]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    name = require_text(row.get("name"), f"{item_id}.name")
    object_type = require_text(row.get("object_type"), f"{item_id}.object_type")
    if object_type not in ALLOWED_OBJECT_TYPES:
        raise CalculationError(f"{item_id}.object_type非法")
    if require_text(row.get("unit"), f"{item_id}.unit") != "m²":
        raise CalculationError(f"{item_id}当前版本只接受明确的面积单位m²")
    scope_status = require_text(row.get("scope_status"), f"{item_id}.scope_status")
    adoption_role = require_text(row.get("adoption_role"), f"{item_id}.adoption_role")
    quantity_role = require_text(row.get("quantity_role"), f"{item_id}.quantity_role")
    if scope_status not in ALLOWED_SCOPE_STATUS:
        raise CalculationError(f"{item_id}.scope_status非法")
    if adoption_role not in ALLOWED_ADOPTION_ROLES:
        raise CalculationError(f"{item_id}.adoption_role非法")
    if quantity_role not in ALLOWED_QUANTITY_ROLES:
        raise CalculationError(f"{item_id}.quantity_role非法")
    if quantity_role == "bill_quantity_candidate" and task["applicable_rule_status"] != "confirmed":
        raise CalculationError(f"{item_id}清单候选量要求适用计量口径已确认")
    physical_surface_id = require_text(row.get("physical_surface_id"), f"{item_id}.physical_surface_id")
    layer_id = require_text(row.get("layer_id"), f"{item_id}.layer_id")
    side_scope = require_text(row.get("side_scope"), f"{item_id}.side_scope")
    measurement_basis = require_text(row.get("measurement_basis"), f"{item_id}.measurement_basis")
    result_use = require_text(row.get("result_use"), f"{item_id}.result_use")
    method = require_text(row.get("method"), f"{item_id}.method")
    identity = (physical_surface_id, layer_id, side_scope, measurement_basis, adoption_role)
    if identity in seen_identities:
        raise CalculationError(f"{item_id}与已有项形成同一表面、同一层、同一侧和同一口径的重复数量")
    seen_identities.add(identity)

    evidence_refs = require_refs(row.get("evidence_refs"), evidence, f"{item_id}.evidence_refs", allow_empty=method == "deferred")
    rule_refs = require_refs(row.get("rule_refs"), rules, f"{item_id}.rule_refs")
    inactive_rules = [ref for ref in rule_refs if rules[ref].get("decision") != "采用"]
    if inactive_rules:
        raise CalculationError(f"{item_id}引用的规则未判定为采用: {', '.join(inactive_rules)}")
    unresolved_refs = require_refs(row.get("unresolved_refs", []), unresolved, f"{item_id}.unresolved_refs", allow_empty=True)
    if scope_status in {"partial", "unresolved"} and not unresolved_refs:
        raise CalculationError(f"{item_id}范围非闭合时必须引用未决事项")
    if scope_status == "unresolved" and method != "deferred":
        raise CalculationError(f"{item_id}未决范围必须使用deferred")
    if method == "deferred" and scope_status not in {"unresolved", "not_applicable"}:
        raise CalculationError(f"{item_id}deferred只允许未决或不适用")
    if adoption_role == "adopted":
        invalid = [ref for ref in evidence_refs if evidence[ref]["fact_status"] not in {"verified", "test_only"}]
        if invalid:
            raise CalculationError(f"{item_id}采用量引用了非已核实证据: {', '.join(invalid)}")
        classes = {evidence[ref]["evidence_class"] for ref in evidence_refs}
        if method != "deferred" and not ({"source_identity", "surface_geometry"} <= classes):
            raise CalculationError(f"{item_id}采用数值必须同时具备来源身份和表面几何证据")
    if measurement_basis == "axis_projection" and adoption_role == "adopted":
        approval_ref = require_text(row.get("basis_approval_evidence_ref"), f"{item_id}.basis_approval_evidence_ref")
        if approval_ref not in evidence_refs or evidence[approval_ref]["evidence_class"] not in {"rule_clause", "plan_approved", "requirement"}:
            raise CalculationError(f"{item_id}轴线口径采用量缺少明确批准证据")

    value, formula, audit = calculate_item(item_id, row)
    if value is not None:
        if value < -1e-9:
            raise CalculationError(f"{item_id}扣减后出现负面积")
        if abs(value) < 1e-12:
            value = 0.0
        opening = audit.get("opening_union_area_m2", 0.0)
        if opening > 0:
            opening_ref = require_text(row.get("opening_union_evidence_ref"), f"{item_id}.opening_union_evidence_ref")
            if opening_ref not in evidence_refs or evidence[opening_ref]["evidence_class"] != "opening_union":
                raise CalculationError(f"{item_id}洞口扣减必须绑定洞口并集证据")
        if scope_status == "complete":
            coverage_ref = require_text(row.get("coverage_evidence_ref"), f"{item_id}.coverage_evidence_ref")
            if coverage_ref not in evidence_refs or evidence[coverage_ref]["evidence_class"] != "coverage_complete":
                raise CalculationError(f"{item_id}声明范围闭合但缺少完整覆盖证据")
        if value == 0:
            require_text(row.get("zero_meaning"), f"{item_id}.zero_meaning")
            coverage_ref = require_text(row.get("coverage_evidence_ref"), f"{item_id}.coverage_evidence_ref")
            if coverage_ref not in evidence_refs or evidence[coverage_ref]["evidence_class"] != "coverage_complete":
                raise CalculationError(f"{item_id}零值必须由完整覆盖证据支持")
    if scope_status == "partial" and any(word in name for word in PARTIAL_FORBIDDEN_WORDS):
        raise CalculationError(f"{item_id}部分结果名称不得使用完整性词语")

    value_state = "未确定" if value is None else "已计算"
    status = "待确认" if value is None else ("部分计算" if scope_status == "partial" else "已计算")
    if scope_status == "not_applicable":
        value_state, status = "不适用", "不适用"
    precision = require_text(row.get("precision", "0.001 m²"), f"{item_id}.precision")
    identity_signature = canonical_hash({
        "physical_surface_id": physical_surface_id,
        "layer_id": layer_id,
        "side_scope": side_scope,
        "measurement_basis": measurement_basis,
        "adoption_role": adoption_role,
    })
    result = {
        "成果项编号": item_id,
        "范围": {
            "业务专业": task["business_discipline"],
            "主要对象": name,
            "系统": task["system"],
            "楼栋": task["building"],
            "楼层或区域": task["zone"],
            "规格": layer_id,
            "输出维度": task["output_dimension"],
        },
        "结果": {
            "名称": name,
            "数值": None if value is None else round(value, 9),
            "数值状态": value_state,
            "计算式或说明": formula,
            "精度": precision,
        },
        "单位": "m²",
        "来源": evidence_refs,
        "规则": rule_refs,
        "状态": status,
        "未决事项": unresolved_refs,
        "试运行结果": True,
        "零值复核": (
            {
                "范围覆盖": evidence[require_text(row.get("coverage_evidence_ref"), f"{item_id}.coverage_evidence_ref")]["locator"],
                "来源": evidence_refs,
                "规则": rule_refs,
                "复核结论": "已确认零值",
                "说明": require_text(row.get("zero_meaning"), f"{item_id}.zero_meaning"),
            }
            if value == 0
            else None
        ),
        "专业检查": {
            "对象类别": object_type,
            "物理表面编号": physical_surface_id,
            "构造层编号": layer_id,
            "侧别范围": side_scope,
            "计量基准": measurement_basis,
            "计算方法": method,
            "范围完整性": scope_status,
            "采用角色": adoption_role,
            "数量角色": quantity_role,
            "结果用途": result_use,
            "对象身份签名": identity_signature,
            "输入审计": audit,
            "进入采用小计": adoption_role == "adopted" and value is not None,
        },
    }
    subtotal_row = None
    if value is not None:
        subtotal_row = {
            "对象类别": object_type,
            "构造层编号": layer_id,
            "计量基准": measurement_basis,
            "采用角色": adoption_role,
            "单位": "m²",
            "数值": value,
            "来源成果项": item_id,
            "范围完整性": scope_status,
        }
    return result, subtotal_row


def calculate_from_path(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    reject_legacy_runtime(manifest_path, "manifest")
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CalculationError(f"manifest_version必须为{MANIFEST_VERSION}")
    task = validate_task(manifest.get("task"))
    source_rows = unique_map(manifest.get("sources"), "source_id", "SRC-", "sources")
    evidence_rows = unique_map(manifest.get("evidence"), "evidence_id", "EV-", "evidence")
    rule_rows = unique_map(manifest.get("rules"), "rule_id", "R-", "rules")
    unresolved_rows = unique_map(manifest.get("unresolved", []), "unresolved_id", "U-", "unresolved")
    item_rows = unique_map(manifest.get("items"), "item_id", "QTY-", "items")
    if not item_rows:
        raise CalculationError("items至少需要一项")
    sources = load_sources(source_rows, manifest_path.parent)
    evidence = validate_evidence(evidence_rows, sources)
    validate_rules(rule_rows)
    validate_unresolved(unresolved_rows)

    result_items: list[dict[str, Any]] = []
    subtotal_inputs: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, ...]] = set()
    for item_id, row in item_rows.items():
        result, subtotal = build_result_item(item_id, row, task, evidence, rule_rows, unresolved_rows, seen_identities)
        result_items.append(result)
        if subtotal is not None:
            subtotal_inputs.append(subtotal)

    grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for row in subtotal_inputs:
        key = (
            row["对象类别"], row["构造层编号"], row["计量基准"], row["采用角色"],
            row["单位"], row["范围完整性"],
        )
        group = grouped.setdefault(key, {
            "对象类别": key[0], "构造层编号": key[1], "计量基准": key[2],
            "采用角色": key[3], "单位": key[4], "范围完整性": key[5],
            "数值": 0.0, "来源成果项": [],
        })
        group["数值"] += row["数值"]
        group["来源成果项"].append(row["来源成果项"])
    subtotals = []
    for index, key in enumerate(sorted(grouped), 1):
        group = grouped[key]
        group["小计编号"] = f"ST-{index:03d}"
        group["数值"] = round(group["数值"], 9)
        subtotals.append(group)

    source_output = []
    for source_id, source in sources.items():
        source_output.append({
            "资料编号": source_id,
            "路径": str(source["path"]),
            "资料类型": source["source_type"],
            "文件角色": source["file_role"],
            "语义角色": source["source_role"],
            "版本身份": f"SHA256:{source['sha256']}",
            "选择结论": {"selected": "已选择", "excluded": "已排除", "uncertain": "暂不确定"}[source["selection"]],
            "原因": source["reason"],
            "拟提取中性事实": source["neutral_facts"],
            "未使用影响": source["not_used_impact"],
        })
    evidence_output = []
    for evidence_id, row in evidence.items():
        source = sources[row["source_id"]]
        locator = row["locator"]
        if row["entity_handles"]:
            locator = f"{locator}; handles={','.join(row['entity_handles'])}"
        evidence_output.append({
            "证据编号": evidence_id,
            "资料编号": row["source_id"],
            "来源类型": source["source_type"],
            "中性事实": row["fact"],
            "定位": locator,
            "来源身份": f"SHA256:{source['sha256']}; object_keys={','.join(row['object_keys'])}",
            "提取方式": row["extraction"],
            "质量状态": f"{row['fact_status']}/{row['quality']}",
            "限制": row["limitation"],
            "证据类别": row["evidence_class"],
            "事实状态": row["fact_status"],
        })
    rules_output = [{
        "规则编号": rule_id,
        "规则名称": row["name"],
        "规则来源": row["source"],
        "本次判定": row["decision"],
        "成熟状态": row["maturity"],
        "执行结果": row["execution"],
        "证据位置": row["evidence_locator"],
    } for rule_id, row in rule_rows.items()]
    unresolved_links = {
        unresolved_id: [
            item_id for item_id, row in item_rows.items()
            if unresolved_id in row.get("unresolved_refs", [])
        ]
        for unresolved_id in unresolved_rows
    }
    unresolved_output = [{
        "事项编号": unresolved_id,
        "类型": row["type"],
        "关联成果项": unresolved_links[unresolved_id],
        "说明": row["description"],
        "影响": row["impact"],
        "所需输入": row["required_input"],
        "当前处理": row["action"],
    } for unresolved_id, row in unresolved_rows.items()]

    result = {
        "契约版本": RESULT_VERSION,
        "任务范围": {
            "业务专业": task["business_discipline"],
            "主要物理对象": task["main_objects"],
            "系统": task["system"],
            "楼栋": task["building"],
            "楼层或区域": task["zone"],
            "图纸类型": task["drawing_type"],
            "图纸版本": task["drawing_version"],
            "输出维度": task["output_dimension"],
        },
        "资料选择": source_output,
        "证据目录": evidence_output,
        "本次规则清单": rules_output,
        "工程量结果": result_items,
        "工程量小计": subtotals,
        "整体未决事项": unresolved_output,
        "专业验算": {
            "来源身份通过": True,
            "中性实体流逐行守恒": all(source.get("audited_row_count", 1) > 0 for source in sources.values()),
            "构造层分账": True,
            "洞口采用并集证据": True,
            "轴线与设计表面口径分离": True,
            "参考量未进入采用小计": True,
            "旧运行时依赖": False,
        },
        "绑定验证": {
            "运行清单SHA256": sha256_file(manifest_path),
            "生成器SHA256": sha256_file(Path(__file__).resolve()),
            "清单语义SHA256": canonical_hash(manifest),
            "生成器": str(Path(__file__).resolve()),
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="按证据锁定清单复算面层与建筑围护面积")
    parser.add_argument("--manifest", required=True, type=Path, help="运行清单JSON")
    parser.add_argument("--output", type=Path, help="成果JSON；省略则输出到标准输出")
    args = parser.parse_args()
    try:
        result = calculate_from_path(args.manifest)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            print(json.dumps({"status": "PASS", "output": str(output), "item_count": len(result["工程量结果"])}, ensure_ascii=False))
        else:
            print(payload)
        return 0
    except CalculationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
