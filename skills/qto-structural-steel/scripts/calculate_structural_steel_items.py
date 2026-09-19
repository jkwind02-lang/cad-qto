#!/usr/bin/env python3
"""Deterministic structural-steel calculator for evidence-locked manifests."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "qto-structural-steel-manifest/1.0"
RESULT_VERSION = "qto-result-minimum/1.0"
ALLOWED_METHODS = {
    "profile_length",
    "built_up_section_length",
    "plate",
    "count",
    "area",
    "surface",
    "deferred",
}
ALLOWED_OBJECT_TYPES = {
    "main_member",
    "node_plate",
    "connection_hardware",
    "profiled_sheet",
    "coating",
    "measure",
    "other_permanent_steel",
}
ALLOWED_SCOPE_STATUS = {"complete", "partial", "unresolved", "not_applicable"}
ALLOWED_MASS_INCLUSION = {"permanent", "separate_reference", "not_mass"}
ALLOWED_ADOPTION_ROLES = {"adopted", "reference"}
ALLOWED_FILE_ROLES = {"dwg", "dxf", "pdf", "json", "gzip_ndjson", "spreadsheet", "document", "csv", "text", "image"}
ALLOWED_SOURCE_ROLES = {
    "project_drawing",
    "cad_entity_stream",
    "cad_metadata",
    "project_document",
    "approved_plan",
    "coverage_report",
    "standard_reference",
    "test_fixture",
}
ALLOWED_EVIDENCE_CLASSES = {
    "source_identity",
    "object_geometry",
    "object_mapping",
    "material_property",
    "rule_clause",
    "plan_approved",
    "coverage_complete",
    "requirement",
    "third_party_reference",
    "visual_geometry",
}
ALLOWED_FACT_STATUS = {"verified", "reference", "candidate", "test_only"}
SOURCE_ROLE_FILE_ROLES = {
    "project_drawing": {"dwg", "dxf"},
    "cad_entity_stream": {"gzip_ndjson"},
    "cad_metadata": {"json"},
    "project_document": {"pdf", "spreadsheet", "document", "csv", "text", "image"},
    "approved_plan": {"pdf", "spreadsheet", "document", "text", "image"},
    "coverage_report": {"pdf", "spreadsheet", "document", "csv", "text", "image"},
    "standard_reference": {"pdf", "spreadsheet", "document", "csv", "text", "image"},
    "test_fixture": {"text"},
}
SOURCE_ROLE_EVIDENCE_CLASSES = {
    "project_drawing": {"source_identity", "object_geometry", "object_mapping", "material_property", "requirement", "visual_geometry"},
    "cad_entity_stream": {"source_identity", "object_geometry", "object_mapping", "material_property", "requirement", "visual_geometry"},
    "cad_metadata": {"source_identity", "object_mapping"},
    "project_document": {"source_identity", "object_geometry", "object_mapping", "material_property", "rule_clause", "requirement", "third_party_reference", "visual_geometry"},
    "approved_plan": {"plan_approved"},
    "coverage_report": {"coverage_complete"},
    "standard_reference": {"material_property", "rule_clause", "requirement", "third_party_reference"},
    "test_fixture": ALLOWED_EVIDENCE_CLASSES,
}
CAD_METADATA_REQUIRED_ASSERTIONS = {
    "metadata.source_sha256",
    "entity_stream_sha256",
    "metadata.adapter",
    "metadata.schema_version",
    "source_unit",
    "integrity.conservation_ok",
}
COUNT_UNITS = {"套", "个", "根", "块", "件", "组", "批"}
PARTIAL_FORBIDDEN_WORDS = {"总量", "完整", "全部", "合计"}
FORBIDDEN_ESTIMATE_WORDS = {"经验", "估算", "比例", "补差", "反推"}


_GZIP_AUDIT_CACHE: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}


class CalculationError(RuntimeError):
    """Raised when decisive input or evidence is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalculationError(f"{label}必须是非空文本")
    return value.strip()


def require_number(value: Any, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalculationError(f"{label}必须是有限数值")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        suffix = "非负" if allow_zero else "大于0"
        raise CalculationError(f"{label}必须{suffix}")
    return number


def require_integer(value: Any, label: str, *, allow_zero: bool = False) -> int:
    number = require_number(value, label, allow_zero=allow_zero)
    if not number.is_integer():
        raise CalculationError(f"{label}必须是整数")
    return int(number)


def require_sha256(value: Any, label: str) -> str:
    text = require_text(value, label).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise CalculationError(f"{label}不是有效SHA-256")
    return text


def require_contract_id(value: str, prefix: str, label: str) -> str:
    if not re.fullmatch(rf"{re.escape(prefix)}[A-Za-z0-9._-]+", value):
        raise CalculationError(f"{label}必须以{prefix}开头且只含ASCII字母、数字、点、下划线或连字符")
    return value


def dotted_value(data: Any, dotted_path: str, label: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise CalculationError(f"{label}不存在JSON字段: {dotted_path}")
        current = current[part]
    return current


def reject_legacy_runtime(path: Path, label: str) -> None:
    lowered = str(path).replace("/", "\\").lower()
    forbidden = (
        "\\skills-disabled\\",
        "\\旧skill只读归档\\",
        "\\阶段1_归档与停用\\",
        "construction-qto-legacy",
        "construction-qto-preexisting-backup",
        "construction-qto-retired-source",
        "\\.qto\\methods\\",
    )
    if any(fragment in lowered for fragment in forbidden):
        raise CalculationError(f"{label}不得引用旧Skill、旧归档或旧项目方法: {path}")


def resolve_file(value: Any, label: str) -> Path:
    path = Path(require_text(value, label)).expanduser().resolve()
    reject_legacy_runtime(path, label)
    if not path.is_file():
        raise CalculationError(f"{label}不存在: {path}")
    return path


def validate_file_role(path: Path, role: Any, source_type: str, label: str) -> str:
    file_role = require_text(role, f"{label}.file_role")
    if file_role not in ALLOWED_FILE_ROLES:
        raise CalculationError(f"{label}.file_role非法: {file_role}")
    suffix = path.suffix.casefold()
    with path.open("rb") as handle:
        head = handle.read(16)
    valid = True
    if file_role == "dwg":
        valid = suffix == ".dwg" and bool(re.fullmatch(rb"AC\d{4}", head[:6]))
    elif file_role == "dxf":
        with path.open("rb") as handle:
            dxf_head = handle.read(4096).upper()
        valid = suffix == ".dxf" and b"SECTION" in dxf_head and (b"HEADER" in dxf_head or b"ENTITIES" in dxf_head)
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
        valid = suffix in {".txt", ".md", ".yaml", ".yml", ".py"}
    elif file_role == "image":
        valid = suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"} and (
            head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"\xff\xd8\xff")
            or head.startswith((b"II*\x00", b"MM\x00*", b"BM"))
        )
    if not valid:
        raise CalculationError(f"{label}文件角色、扩展名或文件签名不一致: role={file_role} path={path}")

    lowered_type = source_type.casefold()
    keyword_roles = {
        "dwg": "dwg",
        "dxf": "dxf",
        "pdf": "pdf",
        "元数据": "json",
        "实体流": "gzip_ndjson",
    }
    for keyword, expected_role in keyword_roles.items():
        if keyword in lowered_type and file_role != expected_role:
            raise CalculationError(f"{label}.source_type与file_role不一致: {source_type} / {file_role}")
    return file_role


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalculationError(f"无法读取JSON {path}: {exc}") from exc


def audit_gzip_ndjson(
    path: Path,
    stream_sha256: str,
    drawing_sha256: str,
    schema_version: str,
    expected_rows: int,
) -> dict[str, Any]:
    """Parse the bound neutral stream once and verify row-level identity fields."""
    cache_key = (str(path), stream_sha256, drawing_sha256, schema_version, expected_rows)
    cached = _GZIP_AUDIT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    row_count = 0
    handles: set[str] = set()
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise CalculationError(f"{path}第{line_number}行不是有效NDJSON: {exc}") from exc
                if not isinstance(row, dict):
                    raise CalculationError(f"{path}第{line_number}行必须是JSON对象")
                if not require_text(row.get("row_type"), f"{path}第{line_number}行.row_type"):
                    raise CalculationError(f"{path}第{line_number}行缺少row_type")
                row_source = require_sha256(row.get("source_sha256"), f"{path}第{line_number}行.source_sha256")
                if row_source != drawing_sha256:
                    raise CalculationError(f"{path}第{line_number}行来源哈希与绑定图纸不一致")
                if require_text(row.get("schema_version"), f"{path}第{line_number}行.schema_version") != schema_version:
                    raise CalculationError(f"{path}第{line_number}行schema_version与元数据不一致")
                entity_handle = row.get("entity_handle")
                if isinstance(entity_handle, str) and entity_handle.strip():
                    handles.add(entity_handle.strip().upper())
                row_count += 1
    except (OSError, EOFError) as exc:
        raise CalculationError(f"无法解压或读取中性实体流 {path}: {exc}") from exc
    if row_count <= 0:
        raise CalculationError(f"{path}中性实体流为空")
    if row_count != expected_rows:
        raise CalculationError(f"{path}实体流行数不守恒: actual={row_count} expected={expected_rows}")
    result = {"row_count": row_count, "handles": handles}
    _GZIP_AUDIT_CACHE[cache_key] = result
    return result


def unique_records(items: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise CalculationError(f"{label}必须是数组")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CalculationError(f"{label}[{index}]必须是对象")
        identifier = require_text(item.get(key), f"{label}[{index}].{key}")
        if identifier in result:
            raise CalculationError(f"{label}存在重复{key}: {identifier}")
        result[identifier] = item
    return result


def require_refs(value: Any, known: dict[str, Any], label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise CalculationError(f"{label}必须是数组")
    refs = [require_text(item, label) for item in value]
    if not allow_empty and not refs:
        raise CalculationError(f"{label}不能为空")
    missing = sorted(set(refs) - set(known))
    if missing:
        raise CalculationError(f"{label}引用不存在的编号: {', '.join(missing)}")
    if len(refs) != len(set(refs)):
        raise CalculationError(f"{label}存在重复引用")
    return refs


def output_scope(scope: dict[str, str], item: dict[str, Any]) -> dict[str, str]:
    object_names = {
        "main_member": "钢结构主构件",
        "node_plate": "钢结构节点板件",
        "connection_hardware": "钢结构连接件",
        "profiled_sheet": "结构压型钢板",
        "coating": "钢构件涂装或防火表面",
        "measure": "钢结构措施量",
        "other_permanent_steel": "其他永久钢构件",
    }
    return {
        "业务专业": "建筑结构",
        "主要对象": object_names[item["object_type"]],
        "系统": "钢结构",
        "楼栋": scope["building"],
        "楼层或区域": require_text(item.get("area", scope["area"]), "item.area"),
        "规格": require_text(item.get("specification"), "item.specification"),
        "输出维度": require_text(item.get("output_dimension"), "item.output_dimension"),
    }


def check_basis(
    value: Any,
    label: str,
    evidence: dict[str, Any],
    required_fields: tuple[str, ...],
    allowed_evidence_classes: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CalculationError(f"{label}必须是对象")
    for field in required_fields:
        require_text(value.get(field), f"{label}.{field}")
    evidence_id = require_text(value.get("evidence_id"), f"{label}.evidence_id")
    if evidence_id not in evidence:
        raise CalculationError(f"{label}引用不存在的证据: {evidence_id}")
    evidence_class = evidence[evidence_id].get("evidence_class")
    if evidence_class not in allowed_evidence_classes:
        raise CalculationError(
            f"{label}证据类别必须为{sorted(allowed_evidence_classes)}，实际为{evidence_class!r}"
        )
    return value


def zero_review(
    item: dict[str, Any],
    evidence: dict[str, Any],
    rules: dict[str, Any],
    physical_object_id: str,
) -> dict[str, Any]:
    spec = item.get("zero_evidence")
    if not isinstance(spec, dict):
        raise CalculationError(f"{item['item_id']}为零值但缺少zero_evidence")
    evidence_ids = require_refs(spec.get("evidence_ids"), evidence, f"{item['item_id']}.zero_evidence.evidence_ids")
    coverage_ids = require_refs(
        spec.get("coverage_evidence_ids"),
        evidence,
        f"{item['item_id']}.zero_evidence.coverage_evidence_ids",
    )
    if any(evidence[evidence_id].get("evidence_class") != "coverage_complete" for evidence_id in coverage_ids):
        raise CalculationError(f"{item['item_id']}.zero_evidence覆盖证据必须为coverage_complete")
    if any(
        physical_object_id not in evidence[evidence_id].get("covered_object_ids", [])
        for evidence_id in coverage_ids
    ):
        raise CalculationError(f"{item['item_id']}.zero_evidence覆盖证据未包含当前物理对象编号")
    if not set(coverage_ids).issubset(evidence_ids):
        raise CalculationError(f"{item['item_id']}.zero_evidence覆盖证据必须同时列入evidence_ids")
    if not set(evidence_ids).issubset(set(item.get("evidence_ids", []))):
        raise CalculationError(f"{item['item_id']}.zero_evidence不得引用成果项外证据")
    rule_ids = require_refs(spec.get("rule_ids"), rules, f"{item['item_id']}.zero_evidence.rule_ids")
    return {
        "复核结论": "已确认零值",
        "范围覆盖": require_text(spec.get("coverage"), f"{item['item_id']}.zero_evidence.coverage"),
        "来源": evidence_ids,
        "覆盖证据": coverage_ids,
        "规则": rule_ids,
        "说明": require_text(spec.get("explanation"), f"{item['item_id']}.zero_evidence.explanation"),
    }


def compute_item(item: dict[str, Any], evidence: dict[str, Any]) -> tuple[float | None, str, dict[str, Any]]:
    identifier = item["item_id"]
    method = require_text(item.get("method"), f"{identifier}.method")
    if method not in ALLOWED_METHODS:
        raise CalculationError(f"{identifier}.method非法: {method}")
    object_type = item["object_type"]
    unit = require_text(item.get("unit"), f"{identifier}.unit")
    inputs: dict[str, Any] = {}

    if method == "profile_length":
        length = require_number(item.get("length_total_m"), f"{identifier}.length_total_m", allow_zero=True)
        unit_mass = require_number(item.get("unit_mass_kg_m"), f"{identifier}.unit_mass_kg_m")
        basis = check_basis(
            item.get("unit_mass_basis"),
            f"{identifier}.unit_mass_basis",
            evidence,
            ("evidence_id", "standard_or_project_basis", "version"),
            {"material_property", "rule_clause"},
        )
        value = length * unit_mass / 1000.0
        formula = f"{length:.12g}m×{unit_mass:.12g}kg/m÷1000"
        inputs = {"length_total_m": length, "unit_mass_kg_m": unit_mass, "unit_mass_basis": basis}
    elif method == "built_up_section_length":
        area = require_number(item.get("cross_section_area_mm2"), f"{identifier}.cross_section_area_mm2")
        length = require_number(item.get("length_total_m"), f"{identifier}.length_total_m", allow_zero=True)
        density = require_number(item.get("density_kg_m3"), f"{identifier}.density_kg_m3")
        basis = check_basis(
            item.get("density_basis"),
            f"{identifier}.density_basis",
            evidence,
            ("evidence_id", "basis"),
            {"material_property", "rule_clause"},
        )
        value = area * length * density / 1_000_000_000.0
        formula = f"{area:.12g}mm²×{length:.12g}m×{density:.12g}kg/m³÷10^9"
        inputs = {"cross_section_area_mm2": area, "length_total_m": length, "density_kg_m3": density, "density_basis": basis}
    elif method == "plate":
        quantity = require_integer(item.get("quantity"), f"{identifier}.quantity", allow_zero=True)
        area = require_number(item.get("net_area_mm2"), f"{identifier}.net_area_mm2")
        thickness = require_number(item.get("thickness_mm"), f"{identifier}.thickness_mm")
        density = require_number(item.get("density_kg_m3"), f"{identifier}.density_kg_m3")
        hole_treatment = require_text(item.get("hole_treatment"), f"{identifier}.hole_treatment")
        basis = check_basis(
            item.get("density_basis"),
            f"{identifier}.density_basis",
            evidence,
            ("evidence_id", "basis"),
            {"material_property", "rule_clause"},
        )
        value = quantity * area * thickness * density / 1_000_000_000_000.0
        formula = f"{quantity}×{area:.12g}mm²×{thickness:.12g}mm×{density:.12g}kg/m³÷10^12"
        inputs = {"quantity": quantity, "net_area_mm2": area, "thickness_mm": thickness, "density_kg_m3": density, "hole_treatment": hole_treatment, "density_basis": basis}
    elif method == "count":
        quantity = require_integer(item.get("quantity"), f"{identifier}.quantity", allow_zero=True)
        if unit not in COUNT_UNITS:
            raise CalculationError(f"{identifier}计数单位非法: {unit}")
        value = float(quantity)
        formula = require_text(item.get("formula_note"), f"{identifier}.formula_note")
        inputs = {"quantity": quantity}
    elif method == "area":
        value = require_number(item.get("area_m2"), f"{identifier}.area_m2", allow_zero=True)
        for field in ("area_basis", "opening_rule", "overlap_rule"):
            inputs[field] = require_text(item.get(field), f"{identifier}.{field}")
        formula = require_text(item.get("formula_note"), f"{identifier}.formula_note")
    elif method == "surface":
        value = require_number(item.get("surface_area_m2"), f"{identifier}.surface_area_m2", allow_zero=True)
        inputs["area_basis"] = require_text(item.get("area_basis"), f"{identifier}.area_basis")
        inputs["treatment_scope"] = require_text(item.get("treatment_scope"), f"{identifier}.treatment_scope")
        formula = require_text(item.get("formula_note"), f"{identifier}.formula_note")
    else:
        value = None
        formula = require_text(item.get("formula_note"), f"{identifier}.formula_note")

    mass_method = method in {"profile_length", "built_up_section_length", "plate"}
    if mass_method and unit != "t":
        raise CalculationError(f"{identifier}质量计算单位必须为t")
    if method == "area" and unit != "m²":
        raise CalculationError(f"{identifier}面积计算单位必须为m²")
    if method == "surface" and unit != "m²":
        raise CalculationError(f"{identifier}表面计算单位必须为m²")

    if object_type == "connection_hardware" and method not in {"count", "deferred"}:
        raise CalculationError(f"{identifier}连接件必须按数量单列")
    if object_type == "profiled_sheet" and method not in {"area", "deferred"}:
        raise CalculationError(f"{identifier}压型板主结果必须为面积")
    if object_type == "coating" and method not in {"surface", "deferred"}:
        raise CalculationError(f"{identifier}涂装主结果必须为表面面积")
    if object_type == "node_plate" and method not in {"plate", "deferred"}:
        raise CalculationError(f"{identifier}节点板必须按板件或未决表达")
    if object_type == "measure" and method != "deferred":
        plan_refs = item.get("plan_evidence_ids")
        plan_refs = require_refs(plan_refs, evidence, f"{identifier}.plan_evidence_ids")
        if any(evidence[evidence_id].get("evidence_class") != "plan_approved" for evidence_id in plan_refs):
            raise CalculationError(f"{identifier}.plan_evidence_ids必须全部为plan_approved证据")
        if not set(plan_refs).issubset(set(item.get("evidence_ids", []))):
            raise CalculationError(f"{identifier}.plan_evidence_ids必须同时列入成果项evidence_ids")
        physical_object_id = require_text(item.get("physical_object_id"), f"{identifier}.physical_object_id")
        if any(
            physical_object_id not in evidence[evidence_id].get("applicable_object_ids", [])
            for evidence_id in plan_refs
        ):
            raise CalculationError(f"{identifier}.plan_evidence_ids未覆盖当前物理对象编号")

    calculation_basis_text = json.dumps(
        {"formula": formula, "inputs": inputs}, ensure_ascii=False, sort_keys=True
    )
    if method != "deferred" and any(
        word in calculation_basis_text for word in FORBIDDEN_ESTIMATE_WORDS
    ):
        raise CalculationError(f"{identifier}计算依据含经验、比例、补差或反推措辞")
    return value, formula, inputs


def calculate(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CalculationError(f"manifest_version必须为{MANIFEST_VERSION}")
    case_id = require_text(manifest.get("case_id"), "case_id")
    scope_raw = manifest.get("scope")
    if not isinstance(scope_raw, dict):
        raise CalculationError("scope必须是对象")
    scope = {field: require_text(scope_raw.get(field), f"scope.{field}") for field in (
        "project", "building", "area", "drawing_type", "drawing_version", "output_dimension"
    )}

    source_records = unique_records(manifest.get("sources"), "source_id", "sources")
    selected_sources: dict[str, dict[str, Any]] = {}
    output_sources: list[dict[str, Any]] = []
    source_audit: list[dict[str, Any]] = []
    source_paths: dict[str, Path] = {}
    source_roles: dict[str, str] = {}
    source_semantic_roles: dict[str, str] = {}
    source_identity_groups: dict[str, str | None] = {}
    test_mode = manifest.get("test_mode", False)
    if not isinstance(test_mode, bool):
        raise CalculationError("test_mode必须是布尔值")
    for source_id, source in source_records.items():
        require_contract_id(source_id, "SRC-", f"{source_id}.source_id")
        path = resolve_file(source.get("path"), f"{source_id}.path")
        source_paths[source_id] = path
        expected = require_sha256(source.get("sha256"), f"{source_id}.sha256")
        actual = sha256_file(path)
        if actual != expected:
            raise CalculationError(f"{source_id}哈希不符: expected={expected} actual={actual}")
        source_type = require_text(source.get("source_type"), f"{source_id}.source_type")
        file_role = validate_file_role(path, source.get("file_role"), source_type, source_id)
        source_roles[source_id] = file_role
        source_role = require_text(source.get("source_role"), f"{source_id}.source_role")
        if source_role not in ALLOWED_SOURCE_ROLES:
            raise CalculationError(f"{source_id}.source_role非法: {source_role}")
        if file_role not in SOURCE_ROLE_FILE_ROLES[source_role]:
            raise CalculationError(f"{source_id}.source_role与file_role不兼容: {source_role} / {file_role}")
        if source_role == "test_fixture" and not test_mode:
            raise CalculationError(f"{source_id}测试夹具只能在test_mode=true时使用")
        source_semantic_roles[source_id] = source_role
        identity_group_raw = source.get("identity_group")
        identity_group = None if identity_group_raw is None else require_text(
            identity_group_raw, f"{source_id}.identity_group"
        )
        source_identity_groups[source_id] = identity_group
        selected = source.get("selected", True)
        if not isinstance(selected, bool):
            raise CalculationError(f"{source_id}.selected必须是布尔值")
        if selected:
            selected_sources[source_id] = source
        output_sources.append({
            "资料编号": source_id,
            "路径": str(path),
            "资料类型": source_type,
            "文件角色": file_role,
            "语义角色": source_role,
            "身份组": identity_group,
            "版本身份": f"SHA-256 {actual.upper()}；{require_text(source.get('identity_note'), f'{source_id}.identity_note')}",
            "选择结论": "已选择" if selected else "已排除",
            "原因": require_text(source.get("reason"), f"{source_id}.reason"),
            "拟提取中性事实": require_text(source.get("neutral_facts"), f"{source_id}.neutral_facts"),
            "未使用影响": require_text(source.get("unused_impact"), f"{source_id}.unused_impact"),
        })
        source_audit.append({"source_id": source_id, "path": str(path), "sha256": actual, "selected": selected, "file_role": file_role, "source_role": source_role, "identity_group": identity_group})
    if not selected_sources:
        raise CalculationError("至少需要一个已选择来源")

    source_hashes = {row["source_id"]: row["sha256"] for row in source_audit}
    for source_id, source in source_records.items():
        assertions = source.get("json_assertions", [])
        if assertions is None:
            assertions = []
        if not isinstance(assertions, list):
            raise CalculationError(f"{source_id}.json_assertions必须是数组")
        assertion_fields = {
            assertion.get("path") for assertion in assertions if isinstance(assertion, dict)
        }
        if source_semantic_roles[source_id] == "cad_metadata":
            missing_assertions = sorted(CAD_METADATA_REQUIRED_ASSERTIONS - assertion_fields)
            if missing_assertions:
                raise CalculationError(
                    f"{source_id} CAD元数据缺少强制身份断言: {', '.join(missing_assertions)}"
                )
        if not assertions:
            continue
        if source_roles[source_id] != "json":
            raise CalculationError(f"{source_id}.json_assertions只能用于json文件角色")
        data = load_json(source_paths[source_id])
        for index, assertion in enumerate(assertions):
            label = f"{source_id}.json_assertions[{index}]"
            if not isinstance(assertion, dict):
                raise CalculationError(f"{label}必须是对象")
            field = require_text(assertion.get("path"), f"{label}.path")
            actual_value = dotted_value(data, field, label)
            if "equals_source_sha256" in assertion:
                target_id = require_text(assertion.get("equals_source_sha256"), f"{label}.equals_source_sha256")
                if target_id not in source_hashes:
                    raise CalculationError(f"{label}引用不存在的来源: {target_id}")
                expected_value: Any = source_hashes[target_id]
            elif "equals" in assertion:
                expected_value = assertion["equals"]
            else:
                raise CalculationError(f"{label}必须提供equals或equals_source_sha256")
            if isinstance(actual_value, str) and isinstance(expected_value, str):
                matched = actual_value.casefold() == expected_value.casefold()
            else:
                matched = actual_value == expected_value
            if not matched:
                raise CalculationError(
                    f"{label}身份断言失败: {field} actual={actual_value!r} expected={expected_value!r}"
                )
        next(row for row in source_audit if row["source_id"] == source_id)["json_assertion_count"] = len(assertions)

    selected_ids = set(selected_sources)
    stream_identity_audits: dict[str, dict[str, Any]] = {}
    for stream_id in sorted(selected_ids):
        if source_semantic_roles[stream_id] != "cad_entity_stream":
            continue
        identity_group = source_identity_groups[stream_id]
        if identity_group is None:
            raise CalculationError(f"{stream_id}中性实体流必须声明identity_group")
        stream_hash = source_hashes[stream_id]
        metadata_matches: list[tuple[str, dict[str, Any]]] = []
        for metadata_id in sorted(selected_ids):
            if source_semantic_roles[metadata_id] != "cad_metadata" or source_identity_groups[metadata_id] != identity_group:
                continue
            metadata_data = load_json(source_paths[metadata_id])
            if isinstance(metadata_data, dict) and str(metadata_data.get("entity_stream_sha256", "")).casefold() == stream_hash.casefold():
                metadata_matches.append((metadata_id, metadata_data))
        if len(metadata_matches) != 1:
            raise CalculationError(
                f"{stream_id}必须在同一identity_group中唯一匹配声明entity_stream_sha256的元数据，实际{len(metadata_matches)}份"
            )
        metadata_id, metadata_data = metadata_matches[0]
        declared_source_hash = str(dotted_value(metadata_data, "metadata.source_sha256", metadata_id)).casefold()
        drawing_matches = [
            source_id for source_id in sorted(selected_ids)
            if source_identity_groups[source_id] == identity_group
            and source_semantic_roles[source_id] == "project_drawing"
            and source_hashes[source_id].casefold() == declared_source_hash
        ]
        if len(drawing_matches) != 1:
            raise CalculationError(
                f"{metadata_id}必须在同一identity_group中唯一绑定源DWG/DXF，实际{len(drawing_matches)}份"
            )
        if dotted_value(metadata_data, "integrity.conservation_ok", metadata_id) is not True:
            raise CalculationError(f"{metadata_id}实体守恒未通过")
        for field in ("metadata.adapter", "metadata.schema_version", "source_unit"):
            require_text(dotted_value(metadata_data, field, metadata_id), f"{metadata_id}.{field}")
        row_counts = metadata_data.get("row_counts")
        if not isinstance(row_counts, dict) or not row_counts:
            raise CalculationError(f"{metadata_id}.row_counts必须是非空对象")
        expected_rows = 0
        for row_kind, value in row_counts.items():
            expected_rows += require_integer(value, f"{metadata_id}.row_counts.{row_kind}", allow_zero=True)
        stream_audit = audit_gzip_ndjson(
            source_paths[stream_id],
            stream_hash,
            declared_source_hash,
            require_text(dotted_value(metadata_data, "metadata.schema_version", metadata_id), f"{metadata_id}.metadata.schema_version"),
            expected_rows,
        )
        stream_identity_audits[stream_id] = stream_audit
        audit_row = next(row for row in source_audit if row["source_id"] == stream_id)
        audit_row["identity_metadata_source_id"] = metadata_id
        audit_row["identity_drawing_source_id"] = drawing_matches[0]
        audit_row["ndjson_row_count"] = stream_audit["row_count"]

    evidence_records = unique_records(manifest.get("evidence"), "evidence_id", "evidence")
    output_evidence: list[dict[str, Any]] = []
    for evidence_id, item in evidence_records.items():
        require_contract_id(evidence_id, "EV-", f"{evidence_id}.evidence_id")
        source_id = require_text(item.get("source_id"), f"{evidence_id}.source_id")
        if source_id not in selected_sources:
            raise CalculationError(f"{evidence_id}必须引用已选择来源")
        source_hash = next(row["sha256"] for row in source_audit if row["source_id"] == source_id)
        evidence_class = require_text(item.get("evidence_class"), f"{evidence_id}.evidence_class")
        if evidence_class not in ALLOWED_EVIDENCE_CLASSES:
            raise CalculationError(f"{evidence_id}.evidence_class非法: {evidence_class}")
        source_role = source_semantic_roles[source_id]
        if evidence_class not in SOURCE_ROLE_EVIDENCE_CLASSES[source_role]:
            raise CalculationError(
                f"{evidence_id}.evidence_class与来源语义角色不兼容: {evidence_class} / {source_role}"
            )
        fact_status = require_text(item.get("fact_status"), f"{evidence_id}.fact_status")
        if fact_status not in ALLOWED_FACT_STATUS:
            raise CalculationError(f"{evidence_id}.fact_status非法: {fact_status}")
        if source_role == "test_fixture" and fact_status != "test_only":
            raise CalculationError(f"{evidence_id}测试夹具证据必须标记fact_status=test_only")
        source_type = require_text(source_records[source_id].get("source_type"), f"{source_id}.source_type")
        if evidence_class == "plan_approved":
            if item.get("approval_status") != "approved":
                raise CalculationError(f"{evidence_id}批准方案证据的approval_status必须为approved")
            for field in ("approval_locator", "approval_authority", "plan_scope", "plan_version"):
                require_text(item.get(field), f"{evidence_id}.{field}")
            if source_role not in {"approved_plan", "test_fixture"}:
                raise CalculationError(f"{evidence_id}批准方案证据来源语义角色非法: {source_role}")
            applicable = item.get("applicable_object_ids")
            if not isinstance(applicable, list) or not applicable or any(
                not isinstance(value, str) or not value.strip() for value in applicable
            ):
                raise CalculationError(f"{evidence_id}.applicable_object_ids必须是非空对象编号数组")
        if evidence_class == "coverage_complete":
            if item.get("coverage_closed") is not True:
                raise CalculationError(f"{evidence_id}完整覆盖证据必须coverage_closed=true")
            for field in ("coverage_scope", "coverage_method"):
                require_text(item.get(field), f"{evidence_id}.{field}")
            covered = item.get("covered_object_ids")
            if not isinstance(covered, list) or not covered or any(not isinstance(value, str) or not value.strip() for value in covered):
                raise CalculationError(f"{evidence_id}.covered_object_ids必须是非空对象编号数组")
            if source_role not in {"coverage_report", "test_fixture"}:
                raise CalculationError(f"{evidence_id}完整覆盖证据来源语义角色非法: {source_role}")
        object_keys: list[str] = []
        if evidence_class in {"object_geometry", "object_mapping"}:
            raw_keys = item.get("object_keys")
            if not isinstance(raw_keys, list) or not raw_keys:
                raise CalculationError(f"{evidence_id}.object_keys必须是非空数组")
            object_keys = [require_text(value, f"{evidence_id}.object_keys") for value in raw_keys]
            if len(object_keys) != len(set(object_keys)):
                raise CalculationError(f"{evidence_id}.object_keys存在重复值")
            if source_role == "cad_entity_stream":
                available_handles = stream_identity_audits[source_id]["handles"]
                handle_keys = [value.split(":", 1)[1].upper() for value in object_keys if value.casefold().startswith("handle:")]
                if len(handle_keys) != len(object_keys):
                    raise CalculationError(f"{evidence_id}中性实体流对象键必须全部使用handle:<句柄>")
                missing_handles = sorted(set(handle_keys) - available_handles)
                if missing_handles:
                    raise CalculationError(f"{evidence_id}.object_keys引用实体流不存在句柄: {', '.join(missing_handles)}")
        cache_id = item.get("cache_id")
        if source_role in {"cad_entity_stream", "cad_metadata"}:
            require_text(cache_id, f"{evidence_id}.cache_id")
        output_evidence.append({
            "证据编号": evidence_id,
            "证据类别": evidence_class,
            "事实状态": fact_status,
            "来源类型": require_text(item.get("source_kind"), f"{evidence_id}.source_kind"),
            "资料编号": source_id,
            "中性事实": require_text(item.get("fact"), f"{evidence_id}.fact"),
            "定位": require_text(item.get("locator"), f"{evidence_id}.locator"),
            "来源身份": f"{source_id} SHA-256 {source_hash.upper()}",
            "提取方式": require_text(item.get("method"), f"{evidence_id}.method"),
            "质量状态": require_text(item.get("quality"), f"{evidence_id}.quality"),
            "限制": require_text(item.get("limitation"), f"{evidence_id}.limitation"),
            "缓存编号": cache_id,
            "对象键": object_keys,
        })

    rule_records = unique_records(manifest.get("rules"), "rule_id", "rules")
    output_rules: list[dict[str, Any]] = []
    for rule_id, rule in rule_records.items():
        require_contract_id(rule_id, "R-", f"{rule_id}.rule_id")
        maturity = require_text(rule.get("maturity"), f"{rule_id}.maturity")
        decision = require_text(rule.get("decision"), f"{rule_id}.decision")
        if maturity not in {"候选", "试运行", "正式", "停用"}:
            raise CalculationError(f"{rule_id}.maturity非法")
        if decision not in {"采用", "不适用", "证据不足"}:
            raise CalculationError(f"{rule_id}.decision非法")
        if decision == "采用" and maturity in {"候选", "停用"}:
            raise CalculationError(f"{rule_id}为{maturity}规则，不得采用")
        evidence_ids = require_refs(rule.get("evidence_ids"), evidence_records, f"{rule_id}.evidence_ids")
        output_rules.append({
            "规则编号": rule_id,
            "规则名称": require_text(rule.get("name"), f"{rule_id}.name"),
            "规则来源": require_text(rule.get("source"), f"{rule_id}.source"),
            "本次判定": decision,
            "成熟状态": maturity,
            "执行结果": require_text(rule.get("execution"), f"{rule_id}.execution"),
            "证据位置": "、".join(evidence_ids),
        })

    unresolved_records = unique_records(manifest.get("unresolved", []), "unresolved_id", "unresolved")
    item_records = unique_records(manifest.get("items"), "item_id", "items")
    if not item_records:
        raise CalculationError("items不能为空")

    results: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    item_runtime: dict[str, dict[str, Any]] = {}
    for item_id, item in item_records.items():
        require_contract_id(item_id, "QTY-", f"{item_id}.item_id")
        object_type = require_text(item.get("object_type"), f"{item_id}.object_type")
        if object_type not in ALLOWED_OBJECT_TYPES:
            raise CalculationError(f"{item_id}.object_type非法")
        status = require_text(item.get("scope_status"), f"{item_id}.scope_status")
        if status not in ALLOWED_SCOPE_STATUS:
            raise CalculationError(f"{item_id}.scope_status非法")
        mass_inclusion = require_text(item.get("mass_inclusion"), f"{item_id}.mass_inclusion")
        if mass_inclusion not in ALLOWED_MASS_INCLUSION:
            raise CalculationError(f"{item_id}.mass_inclusion非法")
        adoption_role = require_text(item.get("adoption_role"), f"{item_id}.adoption_role")
        if adoption_role not in ALLOWED_ADOPTION_ROLES:
            raise CalculationError(f"{item_id}.adoption_role非法")
        physical_object_id = require_text(item.get("physical_object_id"), f"{item_id}.physical_object_id")
        measurement_basis = require_text(item.get("measurement_basis"), f"{item_id}.measurement_basis")
        alternative_group_raw = item.get("alternative_group")
        alternative_group = None if alternative_group_raw is None else require_text(
            alternative_group_raw, f"{item_id}.alternative_group"
        )
        item["item_id"] = item_id
        item["object_type"] = object_type
        source_refs = require_refs(item.get("evidence_ids"), evidence_records, f"{item_id}.evidence_ids")
        identity_refs = require_refs(
            item.get("identity_evidence_ids"),
            evidence_records,
            f"{item_id}.identity_evidence_ids",
        )
        if not set(identity_refs).issubset(source_refs):
            raise CalculationError(f"{item_id}.identity_evidence_ids必须同时列入成果项evidence_ids")
        if any(evidence_records[ref].get("evidence_class") not in {"object_geometry", "object_mapping"} for ref in identity_refs):
            raise CalculationError(f"{item_id}.identity_evidence_ids只能引用object_geometry或object_mapping证据")
        identity_components = []
        for ref in identity_refs:
            identity_evidence = evidence_records[ref]
            identity_source_id = identity_evidence["source_id"]
            identity_components.append({
                "source_sha256": source_hashes[identity_source_id],
                "object_keys": sorted(identity_evidence["object_keys"]),
            })
        identity_signature = canonical_hash(sorted(
            identity_components,
            key=lambda value: (value["source_sha256"], tuple(value["object_keys"])),
        ))
        rule_refs = require_refs(item.get("rule_ids"), rule_records, f"{item_id}.rule_ids")
        unresolved_refs = require_refs(item.get("unresolved_ids", []), unresolved_records, f"{item_id}.unresolved_ids", allow_empty=True)
        value, formula, inputs = compute_item(item, evidence_records)
        method = item["method"]
        if status == "complete" and (value is None or unresolved_refs):
            raise CalculationError(f"{item_id}完整项必须有数值且无未决事项")
        if status == "partial" and (value is None or not unresolved_refs):
            raise CalculationError(f"{item_id}部分项必须有数值并关联未决事项")
        if status in {"unresolved", "not_applicable"} and value is not None:
            raise CalculationError(f"{item_id}未决或不适用时必须为null")
        if status == "unresolved" and not unresolved_refs:
            raise CalculationError(f"{item_id}未决时必须关联未决事项")
        if method == "deferred" and status not in {"unresolved", "not_applicable"}:
            raise CalculationError(f"{item_id}deferred只能用于未决或不适用")
        name = require_text(item.get("name"), f"{item_id}.name")
        if status == "partial" and any(word in name for word in PARTIAL_FORBIDDEN_WORDS):
            raise CalculationError(f"{item_id}部分结果名称含完整性措辞")
        if value == 0:
            zreview = zero_review(item, evidence_records, rule_records, physical_object_id)
        else:
            zreview = None
        adopted_rules = [rule_records[ref] for ref in rule_refs if rule_records[ref]["decision"] == "采用"]
        if value is not None and not adopted_rules:
            raise CalculationError(f"{item_id}有数值但没有已采用规则")
        if value is not None and len(adopted_rules) != len(rule_refs):
            raise CalculationError(f"{item_id}有数值却引用未采用规则")
        if value is not None and adoption_role == "adopted":
            candidate_evidence = sorted(
                ref for ref in source_refs
                if evidence_records[ref].get("fact_status") == "candidate"
                or (evidence_records[ref].get("fact_status") == "test_only" and not test_mode)
            )
            if candidate_evidence:
                raise CalculationError(
                    f"{item_id}采用量不得由候选或测试证据支撑: {', '.join(candidate_evidence)}"
                )
        trial = any(rule["maturity"] == "试运行" for rule in adopted_rules)
        state_map = {"complete": "已计算", "partial": "部分计算", "unresolved": "证据不足", "not_applicable": "不适用"}
        value_state = "已计算" if value is not None else ("不适用" if status == "not_applicable" else "未确定")
        if value is None:
            result_use = "unresolved"
        elif adoption_role == "reference":
            result_use = "reference_only"
        elif mass_inclusion == "separate_reference":
            result_use = "reference_only"
        elif mass_inclusion == "permanent":
            result_use = "adopted_physical_quantity"
        else:
            result_use = "independent_non_mass_quantity"
        enters_permanent_mass = bool(
            value is not None
            and item["unit"] == "t"
            and object_type in {"main_member", "node_plate", "other_permanent_steel"}
            and mass_inclusion == "permanent"
            and adoption_role == "adopted"
        )
        result_item = {
            "成果项编号": item_id,
            "范围": output_scope(scope, item),
            "结果": {
                "名称": name,
                "数值": value,
                "数值状态": value_state,
                "计算式或说明": formula,
                "精度": "使用未舍入输入计算；展示精度不替代中间值",
            },
            "单位": item["unit"],
            "来源": source_refs,
            "规则": rule_refs,
            "状态": state_map[status],
            "未决事项": unresolved_refs,
            "试运行结果": trial,
            "零值复核": zreview,
            "专业检查": {
                "对象类别": object_type,
                "物理对象编号": physical_object_id,
                "对象身份签名": identity_signature,
                "计量基准": measurement_basis,
                "计算方法": method,
                "质量归属": mass_inclusion,
                "采用角色": adoption_role,
                "结果用途": result_use,
                "进入永久钢质量小计": enters_permanent_mass,
                "范围完整性": status,
                "输入字段": inputs,
                "节点覆盖闭合": bool(item.get("node_coverage_closed", False)),
                "方案证据": item.get("plan_evidence_ids", []),
                "互斥口径组": alternative_group,
            },
        }
        results.append(result_item)
        item_runtime[item_id] = {
            "result": result_item,
            "value": value,
            "unit": item["unit"],
            "object_type": object_type,
            "mass_inclusion": mass_inclusion,
            "adoption_role": adoption_role,
            "alternative_group": alternative_group,
            "physical_object_id": physical_object_id,
            "identity_signature": identity_signature,
        }
        detail_rows.append({
            "成果项编号": item_id,
            "名称": name,
            "对象类别": object_type,
            "计算方法": method,
            "数值": "" if value is None else f"{value:.12g}",
            "单位": item["unit"],
            "状态": state_map[status],
            "计算式或说明": formula,
            "证据": "|".join(source_refs),
            "未决事项": "|".join(unresolved_refs),
        })

    summary_records = unique_records(manifest.get("summary_requests", []), "summary_id", "summary_requests")
    for summary_id, summary in summary_records.items():
        require_contract_id(summary_id, "QTY-", f"{summary_id}.summary_id")
        member_ids = require_refs(summary.get("item_ids"), item_runtime, f"{summary_id}.item_ids")
        members = [item_runtime[item_id] for item_id in member_ids]
        if any(member["value"] is None for member in members):
            raise CalculationError(f"{summary_id}不能汇总null成员")
        unit = require_text(summary.get("unit"), f"{summary_id}.unit")
        if any(member["unit"] != unit for member in members):
            raise CalculationError(f"{summary_id}混合了不同单位")
        object_type = require_text(summary.get("object_type"), f"{summary_id}.object_type")
        if object_type not in ALLOWED_OBJECT_TYPES:
            raise CalculationError(f"{summary_id}.object_type非法")
        member_types = {member["object_type"] for member in members}
        if member_types != {object_type}:
            raise CalculationError(f"{summary_id}混合了不同对象类别或汇总类别与成员不一致")
        alternative_groups = [
            member["alternative_group"]
            for member in members
            if member.get("alternative_group") is not None
        ]
        duplicate_alternatives = sorted({
            group for group in alternative_groups if alternative_groups.count(group) > 1
        })
        if duplicate_alternatives:
            raise CalculationError(
                f"{summary_id}把互斥计量口径相加: {', '.join(duplicate_alternatives)}"
            )
        physical_object_ids = [member["physical_object_id"] for member in members]
        duplicate_objects = sorted({
            object_id for object_id in physical_object_ids if physical_object_ids.count(object_id) > 1
        })
        if duplicate_objects:
            raise CalculationError(
                f"{summary_id}重复汇总同一物理对象的多个计量口径: {', '.join(duplicate_objects)}"
            )
        identity_signatures = [member["identity_signature"] for member in members]
        duplicate_signatures = sorted({
            signature for signature in identity_signatures if identity_signatures.count(signature) > 1
        })
        if duplicate_signatures:
            raise CalculationError(f"{summary_id}重复汇总同一对象证据身份的多个计量口径")
        status = require_text(summary.get("scope_status"), f"{summary_id}.scope_status")
        if status not in {"complete", "partial"}:
            raise CalculationError(f"{summary_id}汇总状态只能是complete或partial")
        unresolved_refs = require_refs(summary.get("unresolved_ids", []), unresolved_records, f"{summary_id}.unresolved_ids", allow_empty=True)
        if status == "complete" and unresolved_refs:
            raise CalculationError(f"{summary_id}完整汇总不得关联未决事项")
        if status == "partial" and not unresolved_refs:
            raise CalculationError(f"{summary_id}部分汇总必须关联未决事项")
        name = require_text(summary.get("name"), f"{summary_id}.name")
        if status == "partial" and any(word in name for word in PARTIAL_FORBIDDEN_WORDS):
            raise CalculationError(f"{summary_id}部分汇总名称含完整性措辞")
        if object_type == "node_plate" and status == "complete" and summary.get("node_coverage_closed") is not True:
            raise CalculationError(f"{summary_id}节点板完整汇总必须证明节点覆盖闭合")
        if status == "complete" and summary.get("scope_coverage_closed") is not True:
            raise CalculationError(f"{summary_id}完整汇总必须证明范围覆盖闭合")
        mass_inclusion = require_text(summary.get("mass_inclusion"), f"{summary_id}.mass_inclusion")
        if mass_inclusion not in ALLOWED_MASS_INCLUSION:
            raise CalculationError(f"{summary_id}.mass_inclusion非法")
        adoption_role = require_text(summary.get("adoption_role"), f"{summary_id}.adoption_role")
        if adoption_role not in ALLOWED_ADOPTION_ROLES:
            raise CalculationError(f"{summary_id}.adoption_role非法")
        if mass_inclusion == "permanent" and any(member["mass_inclusion"] != "permanent" for member in members):
            raise CalculationError(f"{summary_id}永久量汇总混入参考或非质量项")
        if adoption_role == "adopted" and any(member["adoption_role"] != "adopted" for member in members):
            raise CalculationError(f"{summary_id}采用量汇总混入参考项")
        value = math.fsum(float(member["value"]) for member in members)
        sources = sorted({ref for member in members for ref in member["result"]["来源"]})
        rules = sorted({ref for member in members for ref in member["result"]["规则"]})
        trial = any(member["result"]["试运行结果"] for member in members)
        summary_item = {
            "object_type": object_type,
            "area": summary.get("area", scope["area"]),
            "specification": require_text(summary.get("specification"), f"{summary_id}.specification"),
            "output_dimension": require_text(summary.get("output_dimension"), f"{summary_id}.output_dimension"),
        }
        result_item = {
            "成果项编号": summary_id,
            "范围": output_scope(scope, summary_item),
            "结果": {"名称": name, "数值": value, "数值状态": "已计算", "计算式或说明": " + ".join(member_ids), "精度": "未舍入成员使用高精度求和"},
            "单位": unit,
            "来源": sources,
            "规则": rules,
            "状态": "已计算" if status == "complete" else "部分计算",
            "未决事项": unresolved_refs,
            "试运行结果": trial,
            "零值复核": None,
            "专业检查": {
                "对象类别": object_type,
                "物理对象编号": summary_id,
                "对象身份签名": canonical_hash(sorted(identity_signatures)),
                "计量基准": "explicit_summary",
                "计算方法": "explicit_summary",
                "质量归属": mass_inclusion,
                "采用角色": adoption_role,
                "结果用途": "reference_only" if adoption_role == "reference" else (
                    "adopted_physical_quantity" if mass_inclusion == "permanent" else "independent_non_mass_quantity"
                ),
                "进入永久钢质量小计": bool(
                    unit == "t"
                    and object_type in {"main_member", "node_plate", "other_permanent_steel"}
                    and mass_inclusion == "permanent"
                    and adoption_role == "adopted"
                ),
                "范围完整性": status,
                "汇总成员": member_ids,
                "节点覆盖闭合": bool(summary.get("node_coverage_closed", False)),
                "范围覆盖闭合": bool(summary.get("scope_coverage_closed", False)),
                "互斥口径组": None,
            },
        }
        results.append(result_item)
        detail_rows.append({"成果项编号": summary_id, "名称": name, "对象类别": object_type, "计算方法": "explicit_summary", "数值": f"{value:.12g}", "单位": unit, "状态": result_item["状态"], "计算式或说明": result_item["结果"]["计算式或说明"], "证据": "|".join(sources), "未决事项": "|".join(unresolved_refs)})

    result_ids = {item["成果项编号"] for item in results}
    overall_unresolved: list[dict[str, Any]] = []
    for unresolved_id, item in unresolved_records.items():
        require_contract_id(unresolved_id, "U-", f"{unresolved_id}.unresolved_id")
        linked = [result["成果项编号"] for result in results if unresolved_id in result["未决事项"]]
        if not linked:
            raise CalculationError(f"{unresolved_id}没有关联成果项")
        if any(identifier not in result_ids for identifier in linked):
            raise CalculationError(f"{unresolved_id}关联未知成果项")
        overall_unresolved.append({
            "事项编号": unresolved_id,
            "类型": require_text(item.get("type"), f"{unresolved_id}.type"),
            "关联成果项": linked,
            "说明": require_text(item.get("description"), f"{unresolved_id}.description"),
            "影响": require_text(item.get("impact"), f"{unresolved_id}.impact"),
            "所需输入": require_text(item.get("required_input"), f"{unresolved_id}.required_input"),
            "当前处理": require_text(item.get("handling"), f"{unresolved_id}.handling"),
        })

    result = {
        "契约版本": RESULT_VERSION,
        "任务范围": {
            "业务专业": "建筑结构",
            "主要物理对象": "钢结构主构件、节点钢件、连接件、结构压型板及随构件派生量",
            "系统": "钢结构",
            "楼栋": scope["building"],
            "楼层或区域": scope["area"],
            "图纸类型": scope["drawing_type"],
            "图纸版本": scope["drawing_version"],
            "输出维度": scope["output_dimension"],
            "纳入项": manifest.get("included", []),
            "排除项": manifest.get("excluded", []),
        },
        "资料选择": output_sources,
        "证据目录": output_evidence,
        "本次规则清单": output_rules,
        "工程量结果": results,
        "整体未决事项": overall_unresolved,
        "专业验算": {
            "案例编号": case_id,
            "来源身份通过": True,
            "不同单位分链": True,
            "旧运行时依赖": False,
            "运行清单规范哈希": canonical_hash(manifest),
        },
        "说明": require_text(manifest.get("note"), "note"),
    }
    evidence_output = {
        "schema_version": "qto-structural-steel-calculation-evidence/1.0",
        "case_id": case_id,
        "source_audit": source_audit,
        "manifest_canonical_sha256": canonical_hash(manifest),
        "items": [
            {"item_id": row["成果项编号"], "value": row["结果"]["数值"], "unit": row["单位"], "status": row["状态"], "formula": row["结果"]["计算式或说明"], "professional_check": row["专业检查"]}
            for row in results
        ],
        "result_canonical_sha256": canonical_hash(result),
        "detail_rows_canonical_sha256": canonical_hash(detail_rows),
        "business_correctness_proven": False,
    }
    return evidence_output, detail_rows, result


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["成果项编号", "名称", "对象类别", "计算方法", "数值", "单位", "状态", "计算式或说明", "证据", "未决事项"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按证据锁定清单复算钢结构对象工程量")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence-out", required=True, type=Path)
    parser.add_argument("--detail-csv", required=True, type=Path)
    parser.add_argument("--result-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest_path = args.manifest.resolve()
        reject_legacy_runtime(manifest_path, "运行清单")
        manifest = load_json(manifest_path)
        evidence, details, result = calculate(manifest)
        evidence["manifest_path"] = str(manifest_path)
        evidence["manifest_sha256"] = sha256_file(manifest_path)
        generator = Path(__file__).resolve()
        evidence["generator"] = {"path": str(generator), "sha256": sha256_file(generator)}
        detail_path = args.detail_csv.resolve()
        result_path = args.result_out.resolve()
        evidence_path = args.evidence_out.resolve()
        write_csv(detail_path, details)
        write_json(result_path, result)
        evidence["detail_csv"] = {"path": str(detail_path), "sha256": sha256_file(detail_path)}
        evidence["result_file"] = {"path": str(result_path), "sha256": sha256_file(result_path)}
        write_json(evidence_path, evidence)
        print(json.dumps({"status": "PASS", "case_id": manifest["case_id"], "result_count": len(result["工程量结果"]), "evidence_out": str(args.evidence_out.resolve()), "detail_csv": str(args.detail_csv.resolve()), "result_out": str(args.result_out.resolve())}, ensure_ascii=False))
        return 0
    except (CalculationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
