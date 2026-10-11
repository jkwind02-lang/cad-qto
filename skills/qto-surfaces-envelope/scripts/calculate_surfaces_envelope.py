#!/usr/bin/env python3
"""面层与建筑围护确定性复算器。

脚本只复算已经由项目证据锁定的表面、构造层和洞口事实；不识别图纸语义，
不决定材料适用范围，也不把轴线参考面积自动提升为完整工程量。
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'))
from qto_cad_source_contract import canonical_scale
sys.path.insert(0, str(Path(__file__).resolve().parent))
from material_rectangle_partition import MaterialPartitionError, partition_material_regions, rectangle as material_rectangle


MANIFEST_VERSION = "qto-surfaces-envelope-manifest/1.0"
RESULT_VERSION = "qto-result-minimum/1.0"
ALLOWED_METHODS = {
    "area_direct",
    "horizontal_net",
    "rectangle_net",
    "rectangle_openings_net",
    "sloped_from_projection",
    "boundary_intervals_net",
    "material_partition_net",
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
    "skirting_finish",
}
ALLOWED_SCOPE_STATUS = {"complete", "partial", "unresolved", "not_applicable"}
ALLOWED_ADOPTION_ROLES = {"adopted", "reference"}
ALLOWED_QUANTITY_ROLES = {"physical_area", "physical_length", "bill_quantity_candidate"}
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
ALLOWED_RULE_DECISIONS = {"采用", "试用", "不适用", "证据不足"}
ALLOWED_RULE_MATURITY = {"候选", "试运行", "正式", "停用"}
ALLOWED_UNRESOLVED_TYPES = {"缺资料", "范围未定", "来源冲突", "规则冲突", "质量问题", "需用户确认"}
ALLOWED_UNRESOLVED_ACTIONS = {"停止受影响计算", "保留已证实部分", "等待裁决"}
SOURCE_ROLE_FILE_ROLES = {
    "project_drawing": {"dwg", "dxf", "pdf"},
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
        if decision == "试用" and maturity == "停用":
            raise CalculationError(f"{rule_id}为停用规则，不得试用")
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


def rectangle_opening_union(inputs: dict[str, Any], item_id: str) -> tuple[float, list[dict[str, Any]]]:
    """Clip explicit openings to one planar face and union by an exact sweep.

    Coordinates are metres in the face's local XY frame; this does not infer
    openings, assign a wall, or convert PDF page coordinates into metres.
    """
    length = numeric_input(inputs, "length_m", item_id)
    height = numeric_input(inputs, "height_m", item_id)
    if length <= 0 or height <= 0:
        raise CalculationError(f"{item_id}矩形表面尺寸必须大于0")
    if inputs.get("side_factor", 1) != 1:
        raise CalculationError(f"{item_id}几何洞口按单侧绑定；两侧须分别建表面")
    openings = inputs.get("openings")
    if not isinstance(openings, list):
        raise CalculationError(f"{item_id}.inputs.openings须为显式清单，无洞口时使用空数组")
    details = []; clipped = []; seen = set()
    for o in openings:
        if not isinstance(o, dict):
            raise CalculationError(f"{item_id}洞口记录必须是对象")
        key = require_text(o.get("opening_id"), f"{item_id}.opening_id")
        if key in seen:
            raise CalculationError(f"{item_id}洞口编号重复: {key}")
        seen.add(key)
        rect = o.get("rectangle_m")
        if (not isinstance(rect, list) or len(rect) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in rect)):
            raise CalculationError(f"{item_id}洞口矩形须为4个有限米坐标")
        x0,y0,x1,y1 = map(float, rect)
        if x1 <= x0 or y1 <= y0:
            raise CalculationError(f"{item_id}洞口矩形顺序或面积非法")
        part = [max(0.,x0),max(0.,y0),min(length,x1),min(height,y1)]
        intersects = part[2] > part[0] and part[3] > part[1]
        if intersects: clipped.append(part)
        details.append(dict(opening_id=key, rectangle_m=rect,
            clipped_rectangle_m=part if intersects else None,
            clipped_area_m2=(part[2]-part[0])*(part[3]-part[1]) if intersects else 0.))
    xs = sorted({r[i] for r in clipped for i in (0,2)})
    area = 0.
    for left,right in zip(xs,xs[1:]):
        ys = sorted((r[1],r[3]) for r in clipped if r[0] < right and r[2] > left)
        merged = 0.; start = end = None
        for low,high in ys:
            if start is None: start,end = low,high
            elif low <= end: end = max(end,high)
            else: merged += end-start; start,end = low,high
        if start is not None: merged += end-start
        area += (right-left)*merged
    if "opening_union_area_m2" in inputs and not math.isclose(
            numeric_input(inputs,"opening_union_area_m2",item_id),area,rel_tol=0,abs_tol=1e-8):
        raise CalculationError(f"{item_id}声明洞口面积与裁切并集复算不一致")
    return area,details


def json_pointer(value: Any, pointer: Any, label: str) -> Any:
    """Read an explicit JSON source location without evaluating expressions."""
    pointer = require_text(pointer, label)
    if not pointer.startswith('/'):
        raise CalculationError(f'{label}须为以/开头的JSON Pointer')
    for token in pointer[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise CalculationError(f'{label}包含非法JSON Pointer转义')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', token) and int(token) < len(value):
            value = value[int(token)]
        else:
            raise CalculationError(f'{label}源字段不存在: {pointer}')
    return value


def resolve_rectangle_frame(item_id, row, evidence, sources):
    """Check an explicitly bound rectangle frame without interpreting labels.

    A source edge, a finite edge interval and an explicit vertical range define
    the rectangle in one declared Cartesian coordinate system. Source facts are
    checked for internal consistency, not certified as a design interpretation.
    """
    binding = row.get('frame_binding')
    if 'frame_binding' not in row:
        return {'status': 'unbound' if 'local_coordinate_frame' in row else 'not_provided',
                'note': '未声明源边/坐标绑定；既有坐标描述未作定位一致性核验'}
    if row.get('method') not in {'rectangle_net', 'rectangle_openings_net', 'material_partition_net'}:
        raise CalculationError(f'{item_id}局部坐标绑定仅适用于矩形面积方法')
    keys = {'frame_evidence_ref', 'source_pointer', 'edge_evidence_ref', 'edge_source_pointer'}
    if not isinstance(binding, dict) or set(binding) != keys:
        raise CalculationError(f'{item_id}.frame_binding须明确框架及源边的证据引用和JSON Pointer')

    def read(ref_key, pointer_key):
        ref = require_text(binding.get(ref_key), f'{item_id}.{ref_key}')
        if ref not in row['evidence_refs'] or ref not in evidence or evidence[ref]['evidence_class'] != 'surface_geometry':
            raise CalculationError(f'{item_id}.{ref_key}须引用本项表面几何证据')
        e = evidence[ref]
        if row['physical_surface_id'] not in e['object_keys']:
            raise CalculationError(f'{item_id}框架或源边证据未绑定本物理表面')
        source = sources[e['source_id']]
        if source['file_role'] != 'json' or source['parsed'] is None:
            raise CalculationError(f'{item_id}框架与源边须来自已核验哈希的JSON事实源')
        value = json_pointer(source['parsed'], binding.get(pointer_key), f'{item_id}.{pointer_key}')
        if not isinstance(value, dict):
            raise CalculationError(f'{item_id}框架和源边Pointer须定位到对象')
        return value, {'evidence_ref': ref, 'source_id': e['source_id'],
                       'source_sha256': source['sha256'], 'source_pointer': binding[pointer_key]}

    def vector(value, size, label):
        if not isinstance(value, list) or len(value) != size or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
            raise CalculationError(f'{item_id}.{label}须为{size}个有限数值')
        return [float(v) for v in value]

    def close(a, b):
        # Absolute metre tolerance with a small IEEE-754 allowance at large
        # coordinates; never use relative tolerance on translated origins.
        return math.isfinite(a) and math.isfinite(b) and abs(a-b) <= max(1e-8, 8*math.ulp(a), 8*math.ulp(b))

    def same_vector(a, b):
        return all(close(x, y) for x, y in zip(a, b))

    def frame(value, label):
        if not isinstance(value, dict):
            raise CalculationError(f'{item_id}.{label}须为规范局部坐标对象')
        result = {k: require_text(value.get(k), f'{item_id}.{label}.{k}') for k in
                  ('coordinate_system_id', 'source_edge_id', 'source_edge_direction')}
        if result['source_edge_direction'] not in {'same', 'reverse'}:
            raise CalculationError(f'{item_id}.{label}源边方向须为same或reverse')
        for key in ('origin_m', 'x_axis', 'up_axis'):
            result[key] = vector(value.get(key), 3, f'{label}.{key}')
        for key in ('source_edge_interval_m', 'vertical_range_m'):
            result[key] = vector(value.get(key), 2, f'{label}.{key}')
            if result[key][0] >= result[key][1]:
                raise CalculationError(f'{item_id}.{label}.{key}须为单个有正长度的正向区间')
        result['vertical_origin_m'] = vector([value.get('vertical_origin_m')], 1, f'{label}.vertical_origin_m')[0]
        for axis in ('x_axis', 'up_axis'):
            if not math.isclose(math.hypot(*result[axis]), 1., rel_tol=0., abs_tol=1e-9):
                raise CalculationError(f'{item_id}.{label}.{axis}须为单位向量')
        if abs(math.fsum(x*y for x,y in zip(result['x_axis'], result['up_axis']))) > 1e-9:
            raise CalculationError(f'{item_id}.{label}水平轴与竖向轴必须正交')
        if not close(result['vertical_origin_m'], result['vertical_range_m'][0]):
            raise CalculationError(f'{item_id}.{label}竖向原点与明确竖向范围不一致')
        return result

    raw_source_frame, frame_source = read('frame_evidence_ref', 'source_pointer')
    raw_edge, edge_source = read('edge_evidence_ref', 'edge_source_pointer')
    source_frame = frame(raw_source_frame, 'source_frame')
    declared_frame = frame(row.get('local_coordinate_frame'), 'local_coordinate_frame')
    for key, value in source_frame.items():
        candidate = declared_frame[key]
        consistent = same_vector(value, candidate) if isinstance(value, list) else (
            close(value, candidate) if isinstance(value, float) else value == candidate)
        if not consistent:
            raise CalculationError(f'{item_id}局部坐标声明与绑定源框架不一致: {key}')
    edge_id = require_text(raw_edge.get('edge_id'), f'{item_id}.source_edge.edge_id')
    for ref_key in ('frame_evidence_ref', 'edge_evidence_ref'):
        if edge_id not in evidence[binding[ref_key]]['object_keys']:
            raise CalculationError(f'{item_id}框架或源边证据未绑定指定源边')
    if edge_id != source_frame['source_edge_id']:
        raise CalculationError(f'{item_id}框架与源边身份不一致')
    coordinate_system = require_text(raw_edge.get('coordinate_system_id'), f'{item_id}.source_edge.coordinate_system_id')
    if coordinate_system != source_frame['coordinate_system_id']:
        raise CalculationError(f'{item_id}框架与源边未声明同一坐标系')
    try:
        factor = canonical_scale(raw_edge.get('unit'))
    except ValueError as exc:
        raise CalculationError(f'{item_id}源边必须声明物理长度单位: {exc}') from exc
    endpoints = []
    for key in ('start', 'end'):
        value = raw_edge.get(key)
        if not isinstance(value, list) or len(value) not in (2, 3):
            raise CalculationError(f'{item_id}源边起终点须为二维或三维坐标')
        point = vector(value, len(value), f'source_edge.{key}')
        point = [x*factor for x in point] + [0.]*(3-len(point))
        if not all(math.isfinite(x) for x in point):
            raise CalculationError(f'{item_id}源边单位换算溢出')
        endpoints.append(point)
    start, end = endpoints
    edge_length = math.dist(start, end)
    if not math.isfinite(edge_length) or edge_length <= 0:
        raise CalculationError(f'{item_id}源边必须有有限正长度')
    lo, hi = source_frame['source_edge_interval_m']
    if lo < 0 or (hi > edge_length and not close(hi, edge_length)):
        raise CalculationError(f'{item_id}局部框架区间超出源边')
    hi = min(hi, edge_length)
    if not 0 <= lo < hi <= edge_length:
        raise CalculationError(f'{item_id}局部框架区间裁切后须位于源边内且有正长度')
    direction = [(y-x)/edge_length for x,y in zip(start, end)]
    reverse = source_frame['source_edge_direction'] == 'reverse'
    expected_x = [-v for v in direction] if reverse else direction
    station = hi if reverse else lo
    expected_origin = [x+station*v for x,v in zip(start, direction)]
    if not same_vector(source_frame['origin_m'], expected_origin):
        raise CalculationError(f'{item_id}源框架原点与源边有限区间及方向不一致')
    if not same_vector(source_frame['x_axis'], expected_x):
        raise CalculationError(f'{item_id}源框架水平轴与源边方向不一致')
    length = hi-lo
    zlo, zhi = source_frame['vertical_range_m']
    height = zhi-zlo
    inputs = row.get('inputs')
    declared_length = numeric_input(inputs, 'length_m', item_id) if isinstance(inputs, dict) else 0.
    declared_height = numeric_input(inputs, 'height_m', item_id) if isinstance(inputs, dict) else 0.
    if declared_length <= 0 or not close(declared_length, length):
        raise CalculationError(f'{item_id}矩形长度与源边有限区间不一致')
    if declared_height <= 0 or not close(declared_height, height):
        raise CalculationError(f'{item_id}矩形高度与明确竖向范围不一致')
    origin = [p+u*zlo for p,u in zip(expected_origin, source_frame['up_axis'])]
    corners = [[p+x*dx+u*dz for p,x,u in zip(origin, expected_x, source_frame['up_axis'])]
               for dx,dz in ((0.,0.),(length,0.),(length,height),(0.,height))]
    if not all(math.isfinite(v) for p in corners for v in p):
        raise CalculationError(f'{item_id}矩形定位坐标溢出')
    return {'status': 'bound_consistent', 'frame_source_binding': frame_source,
            'edge_source_binding': edge_source, 'local_coordinate_frame': source_frame,
            'source_edge_start_m': start, 'source_edge_end_m': end,
            'source_edge_length_m': edge_length, 'surface_origin_m': origin,
            'rectangle_corners_m': corners, 'bound_length_m': length, 'bound_height_m': height,
            'unchecked_annotation_fields': sorted(set(row['local_coordinate_frame'])-set(source_frame)),
            'note': '仅核已声明事实的坐标、区间及矩形尺寸一致性；不证明设计适用性、洞口三维定位或完整工程范围'}


def calculate_material_partition(item_id, row, evidence, sources):
    inputs = row.get('inputs')
    common = {'partition_evidence_ref', 'source_pointer', 'output_kind'}
    if not isinstance(inputs, dict) or inputs.get('output_kind') not in {'material','unknown'}:
        raise CalculationError(f'{item_id}材料分区须选择明确材料或未知区域出口')
    expected = common | ({'material_id'} if inputs['output_kind'] == 'material' else set())
    if set(inputs) != expected:
        raise CalculationError(f'{item_id}材料分区输入只接受有源分区Pointer和输出选择，不接受预计算面积')
    ref = require_text(inputs.get('partition_evidence_ref'), f'{item_id}.partition_evidence_ref')
    if ref not in row['evidence_refs'] or ref not in evidence or evidence[ref]['evidence_class'] != 'surface_geometry':
        raise CalculationError(f'{item_id}分区须引用本项surface_geometry证据')
    source = sources[evidence[ref]['source_id']]
    if source['file_role'] != 'json' or source['parsed'] is None:
        raise CalculationError(f'{item_id}分区坐标须来自已核验哈希的JSON事实')
    pointer = require_text(inputs.get('source_pointer'), f'{item_id}.source_pointer')
    raw = json_pointer(source['parsed'], pointer, f'{item_id}.source_pointer')
    if not isinstance(raw,dict):
        raise CalculationError(f'{item_id}分区Pointer须定位到对象')
    plan = copy.deepcopy(raw)
    parent = require_text(plan.get('parent_surface_id'), f'{item_id}.parent_surface_id')
    if not {parent,row['physical_surface_id']} <= set(evidence[ref]['object_keys']):
        raise CalculationError(f'{item_id}分区证据未绑定母面及本数量出口')
    plan['source_binding'] = {'evidence_ref':ref,'source_id':evidence[ref]['source_id'],
                              'source_sha256':source['sha256'],'source_pointer':pointer}

    def bind(record, suffix, expected_class):
        if not isinstance(record,dict):
            raise CalculationError(f'{item_id}{suffix}须为有物理身份的源事实对象')
        e_ref=require_text(record.get('source_evidence_ref'), f'{item_id}{suffix}.source_evidence_ref')
        physical=require_text(record.get('physical_object_id'),f'{item_id}{suffix}.physical_object_id')
        if e_ref not in row['evidence_refs'] or e_ref not in evidence or evidence[e_ref]['evidence_class'] != expected_class:
            raise CalculationError(f'{item_id}{suffix}未绑定本项对应类别原图证据')
        e=evidence[e_ref]
        if not {parent,physical,row['physical_surface_id']} <= set(e['object_keys']):
            raise CalculationError(f'{item_id}{suffix}证据未绑定母面、物理对象及本数量出口')
        evidence_source=sources[e['source_id']]
        record['source_binding']={'source_id':evidence[ref]['source_id'],'source_sha256':source['sha256'],
             'source_pointer':pointer+suffix,'evidence_ref':e_ref,'evidence_source_id':e['source_id'],
             'evidence_source_sha256':evidence_source['sha256'],'evidence_locator':e['locator']}

    bind(plan.get('face'),'/face','surface_geometry')
    for key,cls in [('actual_openings','opening_union'),('material_regions','surface_geometry'),('unknown_regions','surface_geometry')]:
        if not isinstance(plan.get(key),list):
            raise CalculationError(f'{item_id}.{key}须显式声明数组')
        for index,record in enumerate(plan[key]):
            bind(record,f'/{key}/{index}',cls)
    if 'base_material' not in plan:
        raise CalculationError(f'{item_id}base_material须显式声明有源补集或null')
    if plan['base_material'] is not None:
        bind(plan['base_material'],'/base_material','surface_geometry')
    if 'coverage' in plan:
        bind(plan['coverage'],'/coverage','surface_geometry')
    frame_binding=row.get('frame_binding')
    if not isinstance(frame_binding,dict):
        raise CalculationError(f'{item_id}材料分区必须绑定母面源边与局部框架')
    frame_ref=frame_binding.get('frame_evidence_ref')
    if frame_ref not in evidence or evidence[frame_ref]['source_id'] != evidence[ref]['source_id'] or frame_binding.get('source_pointer') != pointer+'/local_coordinate_frame':
        raise CalculationError(f'{item_id}材料分区框架必须显式绑定本分区的local_coordinate_frame')
    try:
        face=material_rectangle(plan['face'],f'{item_id}.face')
    except MaterialPartitionError as exc:
        raise CalculationError(str(exc)) from exc
    frame_row=dict(row,inputs={'length_m':face[2]-face[0],'height_m':face[3]-face[1]})
    frame_audit=resolve_rectangle_frame(item_id,frame_row,evidence,sources)
    frame=frame_audit['local_coordinate_frame']
    target=[0.,frame['vertical_range_m'][0],frame_audit['bound_length_m'],frame['vertical_range_m'][1]]
    if any(not math.isclose(a,b,rel_tol=0,abs_tol=1e-8) for a,b in zip(face,target)):
        raise CalculationError(f'{item_id}母面矩形位置与绑定源边区间及竖向范围不一致')
    try:
        partition=partition_material_regions(plan)
    except MaterialPartitionError as exc:
        raise CalculationError(str(exc)) from exc
    if partition['scenario']['status']=='conditional' and row['adoption_role']!='reference':
        raise CalculationError(f'{item_id}条件材料分区只能作为参考方案，不得进入采用量')
    if partition['unknown_region']['area_m2']>0 and row['scope_status']!='partial':
        raise CalculationError(f'{item_id}分区含未定材料或冲突，所有数量出口须保留partial范围及未决')
    if inputs['output_kind']=='unknown':
        if row['adoption_role']!='reference':
            raise CalculationError(f'{item_id}未知材料区域面积只能作为参考范围量')
        value=partition['unknown_region']['area_m2']
        selection={'output_kind':'unknown'}
    else:
        material=require_text(inputs.get('material_id'),f'{item_id}.material_id')
        matching=[v for v in partition['material_regions'] if v['material_id']==material]
        if not matching:
            raise CalculationError(f'{item_id}选择了分区中不存在的材料')
        value=matching[0]['effective_known_geometry']['area_m2']
        selection={'output_kind':'material','material_id':material}
    other=partition['net_face_area_m2']-value
    audit={'gross_area_m2':partition['gross_face_area_m2'],
           'opening_union_area_m2':partition['actual_opening_union']['area_m2'],
           'other_deductions_area_m2':other,'additions_area_m2':0.,
           'opening_union_computed':True,'material_partition':partition,'partition_selection':selection,
           'frame_validation':frame_audit,
           'partition_coordinate_basis':'矩形/多边形为沿x_axis与up_axis、相对基准origin_m的米值；不是偏移后的surface_origin_m'}
    formula=f"母面{partition['gross_face_area_m2']:.6f}-真实洞口并集{partition['actual_opening_union']['area_m2']:.6f}-本出口之外区域{other:.6f}={value:.6f} m²"
    return value,formula,audit


def validate_material_partition_outputs(result_items):
    """Require complete per-scope output without conflating different layers."""
    groups={}
    for item in result_items:
        check=item['专业检查'];audit=check['输入审计']
        partition=audit.get('material_partition')
        if partition is None:
            continue
        key=(partition['parent_surface_id'],partition['layer_scope_id'],partition['scenario']['scenario_id'])
        source=partition['partition_source_binding']
        fingerprint=(partition['partition_id'],source['source_sha256'],source['source_pointer'])
        group=groups.setdefault(key,{'fingerprint':fingerprint,'expected':set(),'actual':set()})
        if fingerprint != group['fingerprint']:
            raise CalculationError('同一母面、层范围和场景引用了不同分区方案，须明确拆分场景')
        expected={('material',v['material_id']) for v in partition['material_regions'] if v['effective_known_geometry']['area_m2']>0}
        if partition['unknown_region']['area_m2']>0:
            expected.add(('unknown',None))
        group['expected']=expected
        selection=audit['partition_selection'];selected=(selection['output_kind'],selection.get('material_id'))
        if selected in group['actual']:
            raise CalculationError('同一母面、层范围和场景的材料/未知区域有重复数量出口')
        group['actual'].add(selected)
    for key,group in groups.items():
        missing=group['expected']-group['actual']
        if missing:
            raise CalculationError(f'材料分区{key}缺少正面积材料或未知区域单列出口: {sorted(missing,key=str)}')


def resolve_boundary_inputs(item_id, row, evidence, sources):
    """Read edge coordinates and opening intervals from hashed source facts.

    The manifest carries identity, selection and interpretation; no precomputed
    perimeter or quantity can override the bound source coordinates.
    """
    inputs = row.get('inputs')
    if not isinstance(inputs, dict) or set(inputs) != {'boundary_edges', 'openings'}:
        raise CalculationError(f'{item_id}长度输入只接受boundary_edges和显式openings，不接受预计算总长')
    if not isinstance(inputs['boundary_edges'], list) or not inputs['boundary_edges'] or not isinstance(inputs['openings'], list):
        raise CalculationError(f'{item_id}源边须为非空数组，openings须显式给数组')
    resolved = {'boundary_edges': [], 'openings': []}
    surface = row['physical_surface_id']
    def read(entry, ref_key, expected_class, object_keys):
        ref = require_text(entry.get(ref_key), f'{item_id}.{ref_key}')
        if ref not in row['evidence_refs'] or ref not in evidence or evidence[ref]['evidence_class'] != expected_class:
            raise CalculationError(f'{item_id}.{ref_key}须引用本项对应类别证据')
        e = evidence[ref]
        if not set([surface, *object_keys]) <= set(e['object_keys']):
            raise CalculationError(f'{item_id}边/洞口证据{ref}未绑定本物理表面及指定边或洞口')
        source = sources[e['source_id']]
        if source['file_role'] != 'json' or source['parsed'] is None:
            raise CalculationError(f'{item_id}边线及区间数值须来自已核验哈希的JSON事实源')
        value = json_pointer(source['parsed'], entry.get('source_pointer'), f'{item_id}.source_pointer')
        if not isinstance(value, dict):
            raise CalculationError(f'{item_id}源边线或洞口区间须定位到对象')
        binding = {'evidence_ref':ref, 'source_id':e['source_id'], 'source_sha256':source['sha256'], 'source_pointer':entry['source_pointer']}
        return value, binding
    for entry in inputs['boundary_edges']:
        if not isinstance(entry, dict) or set(entry) != {'edge_id', 'geometry_evidence_ref', 'source_pointer', 'effective_scope'}:
            raise CalculationError(f'{item_id}源边只接受edge_id/geometry_evidence_ref/source_pointer/effective_scope')
        edge_id = require_text(entry.get('edge_id'), f'{item_id}.edge_id')
        value, binding = read(entry, 'geometry_evidence_ref', 'surface_geometry', [edge_id])
        if value.get('edge_id') != edge_id:
            raise CalculationError(f'{item_id}源边身份与选择不一致')
        if entry['effective_scope'] not in ('whole_edge', 'source_intervals'):
            raise CalculationError(f'{item_id}.effective_scope须为whole_edge或source_intervals')
        edge = {k:value.get(k) for k in ('edge_id','start','end','unit')}
        edge.update(effective_scope=entry['effective_scope'], source_binding=binding)
        if entry['effective_scope'] == 'source_intervals':
            edge['effective_intervals'] = value.get('effective_intervals')
        resolved['boundary_edges'].append(edge)
    for entry in inputs['openings']:
        if not isinstance(entry, dict) or set(entry) != {'opening_id','edge_id','opening_evidence_ref','source_pointer'}:
            raise CalculationError(f'{item_id}洞口只接受opening_id/edge_id/opening_evidence_ref/source_pointer')
        opening_id = require_text(entry.get('opening_id'), f'{item_id}.opening_id')
        edge_id = require_text(entry.get('edge_id'), f'{item_id}.edge_id')
        value, binding = read(entry, 'opening_evidence_ref', 'opening_union', [edge_id, opening_id])
        if value.get('opening_id') != opening_id or value.get('edge_id') != edge_id:
            raise CalculationError(f'{item_id}源洞口或所属边身份不一致，不能跨邻边套区间')
        opening = {k:value.get(k) for k in ('opening_id','edge_id','interval','unit')}
        opening['source_binding'] = binding
        resolved['openings'].append(opening)
    return resolved


def boundary_interval_length(inputs, item_id):
    """Straight edge lengths minus the union of eligible opening intervals."""
    def scale(unit):
        try:
            return canonical_scale(unit)
        except ValueError as exc:
            raise CalculationError(f'{item_id}边线/区间必须声明物理长度单位: {exc}') from exc
    def point(value, factor):
        if not isinstance(value, list) or len(value) not in (2,3) or any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in value):
            raise CalculationError(f'{item_id}边线起终点须为有限二维或三维坐标')
        output = [float(v)*factor for v in value]
        if any(not math.isfinite(v) for v in output):
            raise CalculationError(f'{item_id}边线单位换算溢出')
        return output + [0.]*(3-len(output))
    def interval(value, factor, label):
        if not isinstance(value,list) or len(value)!=2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in value):
            raise CalculationError(f'{item_id}.{label}须为两个有限沿边距离')
        a,b = [float(x)*factor for x in value]
        if not math.isfinite(a) or not math.isfinite(b) or a>=b:
            raise CalculationError(f'{item_id}.{label}区间须正向且有正长度')
        return [a,b]
    def union(values):
        output=[]
        for a,b in sorted(values):
            if output and a<=output[-1][1]:
                output[-1][1]=max(output[-1][1],b)
            else:
                output.append([a,b])
        return output
    def intersect(a,b):
        return [[max(x,u),min(y,v)] for x,y in a for u,v in b if max(x,u)<min(y,v)]
    def length(values):
        return math.fsum(b-a for a,b in values)
    def overlap(a,b,c,d):
        """Positive overlap of two 3D collinear spans, including reversed edges."""
        u=[y-x for x,y in zip(a,b)]; n=math.dist(a,b)
        if not n:
            return False
        direction=[x/n for x in u]
        along=lambda p:math.fsum((y-x)*v for x,y,v in zip(a,p,direction))
        tc,td=along(c),along(d)
        distance=lambda p,t:math.sqrt(math.fsum((y-x-t*v)**2 for x,y,v in zip(a,p,direction)))
        epsilon=max(1e-9,n*1e-12,math.dist(c,d)*1e-12)
        return distance(c,tc)<=epsilon and distance(d,td)<=epsilon and min(n,max(tc,td))-max(0,min(tc,td))>epsilon
    edges = inputs.get('boundary_edges')
    openings = inputs.get('openings')
    if not isinstance(edges,list) or not edges or not isinstance(openings,list):
        raise CalculationError(f'{item_id}缺少明确源边或洞口数组')
    by_id={}; physical_spans=[]
    for edge in edges:
        key=require_text(edge.get('edge_id'),f'{item_id}.edge_id')
        if key in by_id:
            raise CalculationError(f'{item_id}源边编号重复: {key}')
        factor=scale(edge.get('unit')); a,b=point(edge.get('start'),factor),point(edge.get('end'),factor)
        edge_length=math.dist(a,b)
        if not math.isfinite(edge_length) or edge_length<=0:
            raise CalculationError(f'{item_id}源边必须有有限正长度')
        if edge.get('effective_scope')=='whole_edge':
            effective=[[0.,edge_length]]
        elif edge.get('effective_scope')=='source_intervals' and isinstance(edge.get('effective_intervals'),list):
            effective=[interval(v,factor,'effective_intervals') for v in edge['effective_intervals']]
            if any(a<0 or b>edge_length+max(1e-9,edge_length*1e-12) for a,b in effective):
                raise CalculationError(f'{item_id}有效区间越出对应源边')
            effective=[[a,min(b,edge_length)] for a,b in effective]
        else:
            raise CalculationError(f'{item_id}有效范围缺失或非法')
        merged=union(effective)
        for lo,hi in merged:
            start=[x+(y-x)*lo/edge_length for x,y in zip(a,b)]
            end=[x+(y-x)*hi/edge_length for x,y in zip(a,b)]
            if any(overlap(start,end,p,q) for p,q,_ in physical_spans):
                raise CalculationError(f'{item_id}不同边别名覆盖同一物理有效线段，不能重复计量')
            physical_spans.append((start,end,key))
        by_id[key]={'edge_id':key,'start_m':a,'end_m':b,'source_length_m':edge_length,
                    'effective_intervals_m':merged,'effective_interval_overlap_removed_m':length(effective)-length(merged),
                    'gross_length_m':length(merged),'opening_intervals':[],'source_binding':edge.get('source_binding')}
    seen=set()
    for opening in openings:
        key=require_text(opening.get('opening_id'),f'{item_id}.opening_id')
        edge_id=require_text(opening.get('edge_id'),f'{item_id}.opening.edge_id')
        if (key,edge_id) in seen:
            raise CalculationError(f'{item_id}同一边的洞口编号重复: {key}/{edge_id}')
        seen.add((key,edge_id))
        if edge_id not in by_id:
            raise CalculationError(f'{item_id}洞口引用未选择的边: {edge_id}')
        edge=by_id[edge_id]
        raw=interval(opening.get('interval'),scale(opening.get('unit')),'opening.interval')
        clipped=intersect([raw],edge['effective_intervals_m'])
        edge['opening_intervals'].append({'opening_id':key,'raw_interval_m':raw,'clipped_intervals_m':clipped,
                                         'clipped_length_m':length(clipped),'source_binding':opening.get('source_binding')})
    for edge in by_id.values():
        pieces=[v for opening in edge['opening_intervals'] for v in opening['clipped_intervals_m']]
        merged=union(pieces)
        edge.update(opening_union_intervals_m=merged,opening_union_length_m=length(merged),
                    opening_overlap_removed_m=length(pieces)-length(merged),net_length_m=edge['gross_length_m']-length(merged))
    gross=math.fsum(e['gross_length_m'] for e in by_id.values())
    deduction=math.fsum(e['opening_union_length_m'] for e in by_id.values())
    net=gross-deduction
    if not all(math.isfinite(v) for v in (gross,deduction,net)):
        raise CalculationError(f'{item_id}长度合计溢出')
    audit={'gross_length_m':gross,'opening_union_length_m':deduction,'net_length_m':net,'boundary_edges':list(by_id.values()),'opening_union_computed':True}
    return net,f'逐源边有效区间并集{gross:.6f}-逐边洞口交集并集{deduction:.6f}={net:.6f} m',audit


def calculate_item(item_id: str, row: dict[str, Any]) -> tuple[float | None, str, dict[str, Any]]:
    method = require_text(row.get("method"), f"{item_id}.method")
    if method not in ALLOWED_METHODS:
        raise CalculationError(f"{item_id}.method非法")
    inputs = row.get("inputs", {})
    if not isinstance(inputs, dict):
        raise CalculationError(f"{item_id}.inputs必须是对象")
    if method == 'boundary_intervals_net':
        return boundary_interval_length(inputs, item_id)
    if method == "deferred":
        if inputs:
            raise CalculationError(f"{item_id}待定项不得携带数值输入")
        return None, require_text(row.get("deferred_reason"), f"{item_id}.deferred_reason"), {}
    if method == "area_direct":
        if set(inputs) != {"area_m2"}:
            raise CalculationError(f"{item_id}直接面积只接受area_m2，不得静默忽略洞口或其他扣增")
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
    if method in {"rectangle_net", "rectangle_openings_net"}:
        length = numeric_input(inputs, "length_m", item_id)
        height = numeric_input(inputs, "height_m", item_id)
        side_factor = numeric_input(inputs, "side_factor", item_id, 1.0)
        if side_factor not in {1.0, 2.0}:
            raise CalculationError(f"{item_id}.inputs.side_factor只允许1或2")
        opening_details = None
        if method == "rectangle_openings_net":
            opening, opening_details = rectangle_opening_union(inputs, item_id)
        else:
            opening = numeric_input(inputs, "opening_union_area_m2", item_id, 0.0)
        other = numeric_input(inputs, "other_deductions_area_m2", item_id, 0.0)
        additions = numeric_input(inputs, "reveal_or_return_area_m2", item_id, 0.0)
        gross = length * height * side_factor
        value = gross - opening - other + additions
        formula = f"{length:.6f}×{height:.6f}×{side_factor:.0f}-{opening:.6f}-{other:.6f}+{additions:.6f}"
        audit = {"gross_area_m2": gross, "opening_union_area_m2": opening, "other_deductions_area_m2": other, "additions_area_m2": additions}
        if opening_details is not None:
            audit["opening_intersections"] = opening_details
            audit["opening_union_computed"] = True
        return value, formula, audit
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
    sources: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    name = require_text(row.get("name"), f"{item_id}.name")
    object_type = require_text(row.get("object_type"), f"{item_id}.object_type")
    if object_type not in ALLOWED_OBJECT_TYPES:
        raise CalculationError(f"{item_id}.object_type非法")
    unit = require_text(row.get("unit"), f"{item_id}.unit")
    if unit not in {'m²', 'm'}:
        raise CalculationError(f"{item_id}仅接受明确的面积m²或踢脚长度m")
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
    if unit == 'm':
        if object_type != 'skirting_finish' or method not in {'boundary_intervals_net', 'deferred'} or quantity_role not in {'physical_length','bill_quantity_candidate'}:
            raise CalculationError(f'{item_id}米制仅用于源边区间踢脚长度或其未决项，数量角色须为physical_length或清单候选')
    elif method == 'boundary_intervals_net' or quantity_role == 'physical_length':
        raise CalculationError(f'{item_id}长度方法/角色不能输出平方米')
    if method == 'material_partition_net' and quantity_role != 'physical_area':
        raise CalculationError(f'{item_id}材料几何分区只输出physical_area，合同计量转换须另有明确依据')
    identity = (physical_surface_id, layer_id, side_scope, measurement_basis, adoption_role, quantity_role, unit)

    evidence_refs = require_refs(row.get("evidence_refs"), evidence, f"{item_id}.evidence_refs", allow_empty=method == "deferred")
    rule_refs = require_refs(row.get("rule_refs"), rules, f"{item_id}.rule_refs")
    allowed_decisions = {"采用", "试用"} if adoption_role == "reference" else {"采用"}
    inactive_rules = [ref for ref in rule_refs if rules[ref].get("decision") not in allowed_decisions]
    if inactive_rules:
        raise CalculationError(f"{item_id}引用的规则未判定为采用: {', '.join(inactive_rules)}")
    unresolved_refs = require_refs(row.get("unresolved_refs", []), unresolved, f"{item_id}.unresolved_refs", allow_empty=True)
    if scope_status in {"partial", "unresolved"} and not unresolved_refs:
        raise CalculationError(f"{item_id}范围非闭合时必须引用未决事项")
    if scope_status == "unresolved" and method != "deferred":
        raise CalculationError(f"{item_id}未决范围必须使用deferred")
    if scope_status == "not_applicable" and method != "deferred":
        raise CalculationError(f"{item_id}不适用项必须使用deferred，不得携带数值")
    if method == "deferred" and scope_status not in {"unresolved", "not_applicable"}:
        raise CalculationError(f"{item_id}deferred只允许未决或不适用")
    if method != "deferred":
        classes = {evidence[ref]["evidence_class"] for ref in evidence_refs}
        if not {"source_identity", "surface_geometry", "layer_assignment"} <= classes:
            raise CalculationError(f"{item_id}数值项必须具备来源身份、表面几何和构造层适用证据；参考量同样需要")
        for ref in evidence_refs:
            e = evidence[ref]
            if e["evidence_class"] in {"surface_geometry", "opening_union", "coverage_complete", "layer_assignment", "slope_geometry"}:
                if physical_surface_id not in e["object_keys"]:
                    raise CalculationError(f"{item_id}证据{ref}未绑定本物理表面，不能沿用其他房间或立面")
            if e["evidence_class"] == "layer_assignment" and layer_id not in e["object_keys"]:
                raise CalculationError(f"{item_id}做法证据{ref}未绑定本构造层")
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

    calculation_row = row
    if method == 'boundary_intervals_net':
        calculation_row = dict(row, inputs=resolve_boundary_inputs(item_id,row,evidence,sources))
    if method == 'material_partition_net':
        value,formula,audit=calculate_material_partition(item_id,row,evidence,sources)
    else:
        value, formula, audit = calculate_item(item_id, calculation_row)
    if method != 'material_partition_net' and (method in {'rectangle_net', 'rectangle_openings_net'} or 'local_coordinate_frame' in row or 'frame_binding' in row):
        audit['frame_validation'] = resolve_rectangle_frame(item_id, row, evidence, sources)
    partition_dimensions={}
    if method=='material_partition_net':
        p=audit['material_partition'];s=audit['partition_selection']
        partition_dimensions={'材料分区母面':p['parent_surface_id'],'材料分区层范围':p['layer_scope_id'],
                              '材料分区场景':p['scenario']['scenario_id'],'材料分区输出':s['output_kind'],
                              '材料编号':s.get('material_id','')}
        identity+=tuple(partition_dimensions.values())
    if identity in seen_identities:
        raise CalculationError(f"{item_id}与已有项形成同一表面、同一层、同一侧和同一口径的重复数量")
    seen_identities.add(identity)
    if value is not None:
        suffix = 'length_m' if unit == 'm' else 'area_m2'
        gross_key = f'gross_{suffix}'
        opening_key = f'opening_union_{suffix}'
        other_key = f'other_deductions_{suffix}'
        if not math.isfinite(value) or any(not math.isfinite(audit[k]) for k in
                (gross_key, opening_key, other_key, f'additions_{suffix}') if k in audit):
            raise CalculationError(f"{item_id}计算结果溢出或不是有限数量")
        if audit.get(opening_key,0.) + audit.get(other_key,0.) > audit[gross_key] + 1e-9:
            raise CalculationError(f"{item_id}扣减超过毛量；增加量不能掩盖越界或重复扣减")
        if value < -1e-9:
            raise CalculationError(f"{item_id}扣减后出现负数量")
        if abs(value) < 1e-12:
            value = 0.0
        opening = audit.get(opening_key, 0.0)
        if opening > 0:
            opening_ref = require_text(row.get("opening_union_evidence_ref"), f"{item_id}.opening_union_evidence_ref")
            if opening_ref not in evidence_refs or evidence[opening_ref]["evidence_class"] != "opening_union":
                raise CalculationError(f"{item_id}洞口扣减必须绑定洞口并集证据")
        if scope_status == "complete":
            coverage_ref = require_text(row.get("coverage_evidence_ref"), f"{item_id}.coverage_evidence_ref")
            if coverage_ref not in evidence_refs or evidence[coverage_ref]["evidence_class"] != "coverage_complete":
                raise CalculationError(f"{item_id}声明范围闭合但缺少完整覆盖证据")
        if value == 0:
            if scope_status != "complete":
                raise CalculationError(f"{item_id}零值必须在完整检查范围内成立")
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
    precision = require_text(row.get("precision", f"0.001 {unit}"), f"{item_id}.precision")
    identity_signature = canonical_hash({
        "physical_surface_id": physical_surface_id,
        "layer_id": layer_id,
        "side_scope": side_scope,
        "measurement_basis": measurement_basis,
        "adoption_role": adoption_role,
        "quantity_role": quantity_role,
        "unit": unit,
        **partition_dimensions,
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
        "单位": unit,
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
            "规则成熟状态": sorted({rules[ref]["maturity"] for ref in rule_refs}),
            "结果用途": result_use,
            "对象身份签名": identity_signature,
            "输入审计": audit,
            "进入采用小计": adoption_role == "adopted" and value is not None,
            **partition_dimensions,
        },
    }
    subtotal_row = None
    if value is not None:
        subtotal_row = {
            "对象类别": object_type,
            "构造层编号": layer_id,
            "计量基准": measurement_basis,
            "采用角色": adoption_role,
            "数量角色": quantity_role,
            "单位": unit,
            "数值": value,
            "来源成果项": item_id,
            "范围完整性": scope_status,
            **partition_dimensions,
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
        result, subtotal = build_result_item(item_id, row, task, evidence, rule_rows, unresolved_rows, seen_identities, sources)
        result_items.append(result)
        if subtotal is not None:
            subtotal_inputs.append(subtotal)

    validate_material_partition_outputs(result_items)

    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in subtotal_inputs:
        key = (
            row["对象类别"], row["构造层编号"], row["计量基准"], row["采用角色"],
            row["单位"], row["范围完整性"], row["数量角色"],
        ) + tuple(row.get(field,'') for field in ('材料分区母面','材料分区层范围','材料分区场景','材料分区输出','材料编号'))
        group = grouped.setdefault(key, {
            "对象类别": key[0], "构造层编号": key[1], "计量基准": key[2],
            "采用角色": key[3], "单位": key[4], "范围完整性": key[5],
            "数量角色": key[6],
            "数值": 0.0, "来源成果项": [],
        })
        if any(key[7:]):
            group.update(zip(('材料分区母面','材料分区层范围','材料分区场景','材料分区输出','材料编号'),key[7:]))
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
    parser = argparse.ArgumentParser(description="按证据锁定清单复算面层面积与踢脚长度")
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
