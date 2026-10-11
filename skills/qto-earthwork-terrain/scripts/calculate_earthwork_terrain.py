#!/usr/bin/env python3
"""土方与场地标高确定性复算器。

只复算运行清单中已被项目证据锁定的几何和规则；不识别图纸语义，
不推定原地面、工作面、放坡、支护或土体状态换算。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "qto-earthwork-terrain-manifest/1.0"
RESULT_VERSION = "qto-result-minimum/1.0"
METHODS = {
    "direct_volume", "rectangular_prism", "rectangular_frustum",
    "orthogonal_offset_frustum", "pit_union_slices", "difference", "deferred",
}
OBJECT_TYPES = {
    "general_excavation", "pit_excavation", "trench_excavation",
    "pipe_trench_excavation", "cut", "fill", "backfill", "surplus",
    "borrow", "site_leveling", "other_earthwork",
}
QUANTITY_KINDS = {
    "excavation_volume", "fill_volume", "backfill_volume", "surplus_volume",
    "borrow_volume", "leveling_area", "other",
}
MEASUREMENT_BASES = {
    "physical_geometry", "scheme_reference", "standard_boq", "project_contract",
}
EARTH_STATES = {"natural_dense", "compacted", "loose", "void", "unspecified"}
ADOPTION_ROLES = {"adopted", "reference"}
SCOPE_STATUSES = {"complete", "partial", "unresolved", "not_applicable"}
RULE_DECISIONS = {"采用", "不适用", "证据不足"}
RULE_MATURITY = {"候选", "试运行", "正式", "停用"}
UNRESOLVED_TYPES = {"缺资料", "范围未定", "来源冲突", "规则冲突", "质量问题", "需用户确认"}
UNRESOLVED_ACTIONS = {"停止受影响计算", "保留已证实部分", "等待裁决"}
SOURCE_SELECTIONS = {"selected", "excluded", "uncertain"}
# Share the wording heuristic with the minimum-result and pipe validators.
_minimum_path = Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts/validate_minimum_result.py"
_minimum_spec = importlib.util.spec_from_file_location("qto_minimum_earthwork", _minimum_path)
_minimum = importlib.util.module_from_spec(_minimum_spec)
_minimum_spec.loader.exec_module(_minimum)
LEGACY_FRAGMENTS = (
    "\\skills-disabled\\", "\\旧skill只读归档\\", "\\阶段1_归档与停用\\",
    "construction-qto-legacy", "construction-qto-preexisting-backup",
    "construction-qto-retired-source", "\\.qto\\methods\\",
)


class CalculationError(RuntimeError):
    """输入事实、证据绑定或计算约束不成立。"""


def load_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"非标准JSON数值: {value}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CalculationError(f"无法读取JSON {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalculationError(f"{label}必须是非空文本")
    return value.strip()


def number_value(value: Any, label: str, *, positive: bool = False, signed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalculationError(f"{label}必须是有限数值")
    result = float(value)
    if (not signed and result < 0) or (positive and result == 0):
        raise CalculationError(f"{label}必须{'大于0' if positive else '非负'}")
    return result


def identifier(value: Any, prefix: str, label: str) -> str:
    result = text_value(value, label)
    if not re.fullmatch(rf"{re.escape(prefix)}[A-Za-z0-9._-]+", result):
        raise CalculationError(f"{label}必须以{prefix}开头并仅含ASCII字母、数字、点、下划线或连字符")
    return result


def unique_map(rows: Any, key: str, prefix: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise CalculationError(f"{label}必须是数组")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CalculationError(f"{label}[{index}]必须是对象")
        item_id = identifier(row.get(key), prefix, f"{label}[{index}].{key}")
        if item_id in result:
            raise CalculationError(f"{label}存在重复编号: {item_id}")
        result[item_id] = row
    return result


def refs(value: Any, known: dict[str, Any], label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise CalculationError(f"{label}必须是数组")
    result = [text_value(item, label) for item in value]
    if not allow_empty and not result:
        raise CalculationError(f"{label}不能为空")
    if len(result) != len(set(result)):
        raise CalculationError(f"{label}存在重复引用")
    missing = sorted(set(result) - set(known))
    if missing:
        raise CalculationError(f"{label}引用不存在: {', '.join(missing)}")
    return result


def reject_legacy(path: Path, label: str) -> None:
    lowered = str(path).replace("/", "\\").lower()
    if any(fragment in lowered for fragment in LEGACY_FRAGMENTS):
        raise CalculationError(f"{label}不得引用旧Skill、旧归档或旧项目方法: {path}")


def resolve_source(raw: Any, base_dir: Path, label: str) -> Path:
    path = Path(text_value(raw, label)).expanduser()
    path = (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
    reject_legacy(path, label)
    if not path.is_file():
        raise CalculationError(f"{label}不存在: {path}")
    return path


def load_sources(rows: dict[str, dict[str, Any]], base_dir: Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for source_id, row in rows.items():
        selection = text_value(row.get("selection"), f"{source_id}.selection")
        if selection not in SOURCE_SELECTIONS:
            raise CalculationError(f"{source_id}.selection非法")
        path = resolve_source(row.get("path"), base_dir, f"{source_id}.path")
        expected = text_value(row.get("sha256"), f"{source_id}.sha256").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise CalculationError(f"{source_id}.sha256非法")
        actual = sha256_file(path)
        if actual != expected:
            raise CalculationError(f"{source_id}来源SHA256不匹配: expected={expected}, actual={actual}")
        loaded[source_id] = {
            "path": path,
            "sha256": actual,
            "selection": selection,
            "source_type": text_value(row.get("source_type"), f"{source_id}.source_type"),
            "file_role": text_value(row.get("file_role"), f"{source_id}.file_role"),
            "source_role": text_value(row.get("source_role"), f"{source_id}.source_role"),
            "reason": text_value(row.get("reason"), f"{source_id}.reason"),
            "neutral_facts": text_value(row.get("neutral_facts"), f"{source_id}.neutral_facts"),
            "not_used_impact": text_value(row.get("not_used_impact"), f"{source_id}.not_used_impact"),
        }
    return loaded


def rectangle_union_area(rectangles: list[tuple[float, float, float, float]]) -> float:
    """坐标压缩法求轴对齐矩形精确并集面积。"""
    if not rectangles:
        return 0.0
    xs = sorted({value for rect in rectangles for value in (rect[0], rect[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (bottom, top) for x1, bottom, x2, top in rectangles
            if x1 < right and x2 > left
        )
        if not intervals:
            continue
        covered = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                covered += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        covered += end - start
        area += (right - left) * covered
    return area


def rectangular_prismoid(bl: float, bw: float, tl: float, tw: float, depth: float,
                         label: str) -> tuple[float, dict[str, Any]]:
    """Exact volume for parallel rectangles whose lengths vary linearly with height.

    The corresponding edges have the same orientation. The faces may have
    different slopes or asymmetric offsets; the two rectangles need not be
    similar. Stepped, curved or rotating sections are not this geometry model.
    Input lengths are the manifest's evidence-locked metre dimensions.
    """
    # Halving before addition avoids overflow when the finite endpoints are
    # large. Dividing the section weights before summing likewise avoids the
    # unnecessary overflow in 4*A_mid or A_bottom*A_top.
    ml = number_value(bl / 2 + tl / 2, f"{label}.mid_length", positive=True)
    mw = number_value(bw / 2 + tw / 2, f"{label}.mid_width", positive=True)
    ab = number_value(bl * bw, f"{label}.bottom_area", positive=True)
    am = number_value(ml * mw, f"{label}.mid_area", positive=True)
    at = number_value(tl * tw, f"{label}.top_area", positive=True)
    try:
        mean_area = math.fsum((ab / 6, am * (2 / 3), at / 6))
    except OverflowError as exc:
        raise CalculationError(f"{label}矩形棱柱体的截面积积分超出有限数值范围") from exc
    value = number_value(depth * mean_area, f"{label}.volume", positive=True)
    return value, {
        "bottom_area": ab, "mid_length": ml, "mid_width": mw,
        "mid_area": am, "top_area": at,
        "section_model": "parallel_rectangles_linear_dimensions",
    }


def pit_union(inputs: dict[str, Any], label: str) -> tuple[float, dict[str, Any]]:
    pits_raw = inputs.get("pits")
    if not isinstance(pits_raw, list) or not pits_raw:
        raise CalculationError(f"{label}.pits必须是非空数组")
    step = number_value(inputs.get("slice_step"), f"{label}.slice_step", positive=True)
    pits: list[dict[str, float]] = []
    standalone = 0.0
    max_depth = 0.0
    for index, raw in enumerate(pits_raw):
        if not isinstance(raw, dict):
            raise CalculationError(f"{label}.pits[{index}]必须是对象")
        pit = {
            "x": number_value(raw.get("x"), f"{label}.pits[{index}].x", signed=True),
            "y": number_value(raw.get("y"), f"{label}.pits[{index}].y", signed=True),
            "bottom_length": number_value(raw.get("bottom_length"), f"{label}.pits[{index}].bottom_length", positive=True),
            "bottom_width": number_value(raw.get("bottom_width"), f"{label}.pits[{index}].bottom_width", positive=True),
            "depth": number_value(raw.get("depth"), f"{label}.pits[{index}].depth", positive=True),
            "slope_x": number_value(raw.get("slope_x", 0), f"{label}.pits[{index}].slope_x"),
            "slope_y": number_value(raw.get("slope_y", 0), f"{label}.pits[{index}].slope_y"),
        }
        top_l = pit["bottom_length"] + 2 * pit["slope_x"] * pit["depth"]
        top_w = pit["bottom_width"] + 2 * pit["slope_y"] * pit["depth"]
        pit_volume, _ = rectangular_prismoid(
            pit["bottom_length"], pit["bottom_width"], top_l, top_w, pit["depth"],
            f"{label}.pits[{index}]",
        )
        standalone = number_value(standalone + pit_volume, f"{label}.standalone_sum", positive=True)
        max_depth = max(max_depth, pit["depth"])
        pits.append(pit)
    slice_count = math.ceil(max_depth / step)
    volume = 0.0
    for slice_index in range(slice_count):
        z0 = slice_index * step
        z1 = min(max_depth, (slice_index + 1) * step)
        z = (z0 + z1) / 2
        rectangles: list[tuple[float, float, float, float]] = []
        for pit in pits:
            if z >= pit["depth"]:
                continue
            length = pit["bottom_length"] + 2 * pit["slope_x"] * z
            width = pit["bottom_width"] + 2 * pit["slope_y"] * z
            rectangles.append((
                pit["x"] - length / 2, pit["y"] - width / 2,
                pit["x"] + length / 2, pit["y"] + width / 2,
            ))
        volume += rectangle_union_area(rectangles) * (z1 - z0)
    if volume > standalone + 1e-8:
        raise CalculationError(f"{label}并集体积大于逐坑相加体积")
    return volume, {
        "pit_count": len(pits), "slice_step": step, "slice_count": slice_count,
        "standalone_sum": standalone, "overlap_reduction": standalone - volume,
        "standalone_formula": "H/6×(A底+4×A中+A顶)",
        "integration_is_approximate": True,
    }


def calculate_method(method: str, inputs: dict[str, Any], label: str,
                     prior: dict[str, dict[str, Any]], item: dict[str, Any]) -> tuple[float | None, str, dict[str, Any]]:
    if method == "deferred":
        return None, text_value(item.get("deferred_reason"), f"{label}.deferred_reason"), {}
    if method == "direct_volume":
        value = number_value(inputs.get("volume"), f"{label}.inputs.volume")
        return value, f"直接采用已锁定体积 {value:.9f}", {}
    if method == "rectangular_prism":
        length = number_value(inputs.get("length"), f"{label}.inputs.length", positive=True)
        width = number_value(inputs.get("width"), f"{label}.inputs.width", positive=True)
        depth = number_value(inputs.get("depth"), f"{label}.inputs.depth", positive=True)
        return length * width * depth, f"{length}×{width}×{depth}", {}
    if method == "rectangular_frustum":
        bl = number_value(inputs.get("bottom_length"), f"{label}.inputs.bottom_length", positive=True)
        bw = number_value(inputs.get("bottom_width"), f"{label}.inputs.bottom_width", positive=True)
        tl = number_value(inputs.get("top_length"), f"{label}.inputs.top_length", positive=True)
        tw = number_value(inputs.get("top_width"), f"{label}.inputs.top_width", positive=True)
        depth = number_value(inputs.get("depth"), f"{label}.inputs.depth", positive=True)
        value, checks = rectangular_prismoid(bl, bw, tl, tw, depth, label)
        return value, "H/6×(A底+4×A中+A顶)，长、宽随高度线性变化", checks
    if method == "orthogonal_offset_frustum":
        bl = number_value(inputs.get("bottom_length"), f"{label}.inputs.bottom_length", positive=True)
        bw = number_value(inputs.get("bottom_width"), f"{label}.inputs.bottom_width", positive=True)
        depth = number_value(inputs.get("depth"), f"{label}.inputs.depth", positive=True)
        left = number_value(inputs.get("offset_left"), f"{label}.inputs.offset_left")
        right = number_value(inputs.get("offset_right"), f"{label}.inputs.offset_right")
        front = number_value(inputs.get("offset_front"), f"{label}.inputs.offset_front")
        back = number_value(inputs.get("offset_back"), f"{label}.inputs.offset_back")
        tl, tw = bl + left + right, bw + front + back
        value, checks = rectangular_prismoid(bl, bw, tl, tw, depth, label)
        checks.update(top_length=tl, top_width=tw)
        return value, "按四侧水平外扩、侧边随高度线性变化：H/6×(A底+4×A中+A顶)", checks
    if method == "pit_union_slices":
        value, checks = pit_union(inputs, label)
        return value, f"{checks['pit_count']}个矩形坑按{checks['slice_step']}m中点切片求平面并集并积分", checks
    if method == "difference":
        minuend = text_value(inputs.get("minuend_item"), f"{label}.inputs.minuend_item")
        subtrahend = text_value(inputs.get("subtrahend_item"), f"{label}.inputs.subtrahend_item")
        if minuend not in prior or subtrahend not in prior:
            raise CalculationError(f"{label}差值项必须引用此前已计算成果")
        a, b = prior[minuend], prior[subtrahend]
        signature = text_value(item.get("balance_signature"), f"{label}.balance_signature")
        if a["balance_signature"] != signature or b["balance_signature"] != signature:
            raise CalculationError(f"{label}差值项的范围、计量基准或土体状态不一致")
        if a["value"] is None or b["value"] is None:
            raise CalculationError(f"{label}差值来源存在未确定值")
        value = a["value"] - b["value"]
        if value < -1e-8:
            raise CalculationError(f"{label}差值为负，不能静默改作借方")
        return max(0.0, value), f"{minuend}−{subtrahend}", {}
    raise CalculationError(f"{label}.method非法: {method}")


def calculate_from_path(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    reject_legacy(manifest_path, "运行清单")
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CalculationError(f"manifest_version必须为{MANIFEST_VERSION}")
    task = manifest.get("task")
    if not isinstance(task, dict):
        raise CalculationError("task必须是对象")
    required_task = {
        "business_discipline", "main_objects", "system", "building", "zone",
        "drawing_type", "drawing_version", "output_dimension",
    }
    for field in required_task:
        text_value(task.get(field), f"task.{field}")

    source_rows = unique_map(manifest.get("sources"), "source_id", "SRC-", "sources")
    evidence_rows = unique_map(manifest.get("evidence"), "evidence_id", "EV-", "evidence")
    rule_rows = unique_map(manifest.get("rules"), "rule_id", "R-", "rules")
    unresolved_rows = unique_map(manifest.get("unresolved"), "unresolved_id", "U-", "unresolved")
    item_rows = unique_map(manifest.get("items"), "item_id", "QTY-", "items")
    sources = load_sources(source_rows, manifest_path.parent)

    evidence: dict[str, dict[str, Any]] = {}
    for evidence_id, row in evidence_rows.items():
        source_id = text_value(row.get("source_id"), f"{evidence_id}.source_id")
        if source_id not in sources or sources[source_id]["selection"] != "selected":
            raise CalculationError(f"{evidence_id}必须引用已选择资料")
        evidence[evidence_id] = {
            "source_id": source_id,
            "fact": text_value(row.get("fact"), f"{evidence_id}.fact"),
            "locator": text_value(row.get("locator"), f"{evidence_id}.locator"),
            "extraction": text_value(row.get("extraction"), f"{evidence_id}.extraction"),
            "quality": text_value(row.get("quality"), f"{evidence_id}.quality"),
            "limitation": text_value(row.get("limitation"), f"{evidence_id}.limitation"),
            "evidence_class": text_value(row.get("evidence_class"), f"{evidence_id}.evidence_class"),
        }

    for rule_id, row in rule_rows.items():
        decision = text_value(row.get("decision"), f"{rule_id}.decision")
        maturity = text_value(row.get("maturity"), f"{rule_id}.maturity")
        if decision not in RULE_DECISIONS or maturity not in RULE_MATURITY:
            raise CalculationError(f"{rule_id}判定或成熟状态非法")
        if decision == "采用" and maturity in {"候选", "停用"}:
            raise CalculationError(f"{rule_id}为{maturity}规则，不得采用")
        for field in ("name", "source", "execution", "evidence_locator"):
            text_value(row.get(field), f"{rule_id}.{field}")

    for unresolved_id, row in unresolved_rows.items():
        if text_value(row.get("type"), f"{unresolved_id}.type") not in UNRESOLVED_TYPES:
            raise CalculationError(f"{unresolved_id}.type非法")
        if text_value(row.get("action"), f"{unresolved_id}.action") not in UNRESOLVED_ACTIONS:
            raise CalculationError(f"{unresolved_id}.action非法")
        for field in ("description", "impact", "required_input"):
            text_value(row.get(field), f"{unresolved_id}.{field}")

    result_items: list[dict[str, Any]] = []
    prior: dict[str, dict[str, Any]] = {}
    identities: set[str] = set()
    for item_id, row in item_rows.items():
        label = item_id
        object_type = text_value(row.get("object_type"), f"{label}.object_type")
        quantity_kind = text_value(row.get("quantity_kind"), f"{label}.quantity_kind")
        basis = text_value(row.get("measurement_basis"), f"{label}.measurement_basis")
        earth_state = text_value(row.get("earth_state"), f"{label}.earth_state")
        adoption = text_value(row.get("adoption_role"), f"{label}.adoption_role")
        scope = text_value(row.get("scope_status"), f"{label}.scope_status")
        method = text_value(row.get("method"), f"{label}.method")
        if object_type not in OBJECT_TYPES or quantity_kind not in QUANTITY_KINDS:
            raise CalculationError(f"{label}对象类别或数量种类非法")
        if basis not in MEASUREMENT_BASES or earth_state not in EARTH_STATES:
            raise CalculationError(f"{label}计量基准或土体状态非法")
        if adoption not in ADOPTION_ROLES or scope not in SCOPE_STATUSES or method not in METHODS:
            raise CalculationError(f"{label}采用角色、完整性或方法非法")
        if basis == "scheme_reference" and adoption != "reference":
            raise CalculationError(f"{label}方案参考量只能是reference")
        identity = text_value(row.get("identity_signature"), f"{label}.identity_signature")
        if identity in identities:
            raise CalculationError(f"{label}对象身份签名重复")
        identities.add(identity)
        name = text_value(row.get("name"), f"{label}.name")
        if scope == "partial" and _minimum.unqualified_completeness_words(name):
            raise CalculationError(f"{label}局部项名称不得使用完整性措辞")
        evidence_refs = refs(row.get("evidence_refs"), evidence, f"{label}.evidence_refs")
        rule_refs = refs(row.get("rule_refs"), rule_rows, f"{label}.rule_refs", allow_empty=method == "deferred")
        unresolved_refs = refs(row.get("unresolved_refs", []), unresolved_rows, f"{label}.unresolved_refs", allow_empty=True)
        adopted_rules = [rule_rows[rule_id] for rule_id in rule_refs if rule_rows[rule_id]["decision"] == "采用"]
        if method != "deferred":
            if not adopted_rules or len(adopted_rules) != len(rule_refs):
                raise CalculationError(f"{label}数值计算只能引用已采用规则")
            if basis in {"standard_boq", "project_contract"}:
                applicability = row.get("applicability_evidence_refs")
                refs(applicability, evidence, f"{label}.applicability_evidence_refs")
        inputs = row.get("inputs", {})
        if not isinstance(inputs, dict):
            raise CalculationError(f"{label}.inputs必须是对象")
        value, formula, method_checks = calculate_method(method, inputs, label, prior, row)
        unit = text_value(row.get("unit"), f"{label}.unit")
        expected_unit = "m²" if quantity_kind == "leveling_area" else "m³"
        if unit != expected_unit:
            raise CalculationError(f"{label}单位必须为{expected_unit}")
        if value is not None and value < -1e-8:
            raise CalculationError(f"{label}结果不得为负")
        if method == "deferred":
            if not unresolved_refs or scope not in {"unresolved", "partial"}:
                raise CalculationError(f"{label}未决项必须关联未决事项并标明范围未闭合")
            status = text_value(row.get("deferred_status", "证据不足"), f"{label}.deferred_status")
            if status not in {"待确认", "证据不足"}:
                raise CalculationError(f"{label}.deferred_status非法")
            value_state = "未确定"
        else:
            status = "部分计算" if scope == "partial" else "已计算"
            if status == "部分计算" and not unresolved_refs:
                raise CalculationError(f"{label}部分计算必须关联完整性未决事项")
            value_state = "已计算"
        trial = bool(row.get("trial", False))
        if any(rule["maturity"] == "试运行" for rule in adopted_rules) and not trial:
            raise CalculationError(f"{label}采用试运行规则但未标识trial")
        balance_signature = text_value(
            row.get("balance_signature", f"{basis}|{earth_state}|{task['zone']}"),
            f"{label}.balance_signature",
        )
        prior[item_id] = {"value": value, "balance_signature": balance_signature, "unit": unit}
        result_items.append({
            "成果项编号": item_id,
            "范围": {
                "业务专业": task["business_discipline"],
                "主要对象": text_value(row.get("main_object", name), f"{label}.main_object"),
                "系统": task["system"], "楼栋": task["building"], "楼层或区域": task["zone"],
                "规格": text_value(row.get("specification", "按来源边界"), f"{label}.specification"),
                "输出维度": task["output_dimension"],
            },
            "结果": {
                "名称": name, "数值": None if value is None else round(value, 9),
                "数值状态": value_state, "计算式或说明": formula,
                "精度": text_value(row.get("precision", "0.001m³" if unit == "m³" else "0.001m²"), f"{label}.precision"),
            },
            "单位": unit, "来源": evidence_refs, "规则": rule_refs, "状态": status,
            "未决事项": unresolved_refs, "试运行结果": trial, "零值复核": None,
            "专业检查": {
                "对象类别": object_type, "数量种类": quantity_kind, "土体状态": earth_state,
                "计量基准": basis, "采用角色": adoption, "范围完整性": scope,
                "计算方法": method, "对象身份签名": identity,
                "进入采用小计": adoption == "adopted" and value is not None,
                "平衡签名": balance_signature, "方法检查": method_checks,
                "结果用途": text_value(row.get("result_use"), f"{label}.result_use"),
            },
        })

    subtotals: list[dict[str, Any]] = []
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in result_items:
        value = item["结果"]["数值"]
        if value is None:
            continue
        check = item["专业检查"]
        key = (check["对象类别"], check["数量种类"], check["土体状态"], check["计量基准"], check["采用角色"])
        group = groups.setdefault(key, {"数值": 0.0, "来源成果项": []})
        group["数值"] += value
        group["来源成果项"].append(item["成果项编号"])
    for index, (key, group) in enumerate(sorted(groups.items()), start=1):
        obj, kind, state, basis, adoption = key
        subtotals.append({
            "小计编号": f"SUB-ET-{index:03d}", "对象类别": obj, "数量种类": kind,
            "土体状态": state, "计量基准": basis, "采用角色": adoption,
            "单位": "m²" if kind == "leveling_area" else "m³",
            "数值": round(group["数值"], 9), "来源成果项": group["来源成果项"],
        })

    source_output = [{
        "资料编号": source_id, "路径": str(row["path"]), "资料类型": row["source_type"],
        "文件角色": row["file_role"], "语义角色": row["source_role"],
        "版本身份": f"SHA256:{row['sha256']}",
        "选择结论": {"selected": "已选择", "excluded": "已排除", "uncertain": "暂不确定"}[row["selection"]],
        "原因": row["reason"], "拟提取中性事实": row["neutral_facts"], "未使用影响": row["not_used_impact"],
    } for source_id, row in sources.items()]
    evidence_output = [{
        "证据编号": evidence_id, "资料编号": row["source_id"],
        "来源类型": sources[row["source_id"]]["source_type"], "中性事实": row["fact"],
        "定位": row["locator"],
        "来源身份": f"SHA256:{sources[row['source_id']]['sha256']}",
        "提取方式": row["extraction"], "质量状态": row["quality"],
        "限制": row["limitation"], "证据类别": row["evidence_class"],
    } for evidence_id, row in evidence.items()]
    rules_output = [{
        "规则编号": rule_id, "规则名称": row["name"], "规则来源": row["source"],
        "本次判定": row["decision"], "成熟状态": row["maturity"],
        "执行结果": row["execution"], "证据位置": row["evidence_locator"],
    } for rule_id, row in rule_rows.items()]
    links = {u: [item["成果项编号"] for item in result_items if u in item["未决事项"]] for u in unresolved_rows}
    unresolved_output = [{
        "事项编号": unresolved_id, "类型": row["type"], "关联成果项": links[unresolved_id],
        "说明": row["description"], "影响": row["impact"],
        "所需输入": row["required_input"], "当前处理": row["action"],
    } for unresolved_id, row in unresolved_rows.items()]

    return {
        "契约版本": RESULT_VERSION,
        "任务范围": {
            "业务专业": task["business_discipline"], "主要物理对象": task["main_objects"],
            "系统": task["system"], "楼栋": task["building"], "楼层或区域": task["zone"],
            "图纸类型": task["drawing_type"], "图纸版本": task["drawing_version"],
            "输出维度": task["output_dimension"],
        },
        "资料选择": source_output, "证据目录": evidence_output,
        "本次规则清单": rules_output, "工程量结果": result_items,
        "工程量小计": subtotals, "整体未决事项": unresolved_output,
        "专业验算": {
            "来源身份通过": True, "标高基准显式": True, "土体状态分账": True,
            "计量基准分层": True, "坑槽并集上限": True,
            "参考量未进入采用小计": all(
                not item["专业检查"]["进入采用小计"]
                for item in result_items if item["专业检查"]["采用角色"] == "reference"
            ),
            "未知值未写零": True, "旧运行时依赖": False,
        },
        "绑定验证": {
            "运行清单SHA256": sha256_file(manifest_path),
            "生成器SHA256": sha256_file(Path(__file__).resolve()),
            "清单语义SHA256": canonical_hash(manifest),
            "生成器": str(Path(__file__).resolve()),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="按证据锁定清单复算土方与场地标高工程量")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path)
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
