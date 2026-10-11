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
    "other_surface", "skirting_finish",
}
METHODS = {"area_direct", "horizontal_net", "rectangle_net", "rectangle_openings_net", "sloped_from_projection", "boundary_intervals_net", "material_partition_net", "deferred"}
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
    partition_groups = {}
    for item_id, item in results.items():
        unit = item.get('单位')
        if unit not in {'m²','m'}:
            errors.append(f"{item_id}单位必须为m²或踢脚长度m")
        value_block = item.get("结果")
        check = item.get("专业检查")
        if not isinstance(value_block, dict) or not isinstance(check, dict):
            errors.append(f"{item_id}缺少结果或专业检查")
            continue
        value = value_block.get("数值")
        value_state = value_block.get("数值状态")
        method = check.get("计算方法")
        object_type = check.get("对象类别")
        quantity_role = check.get('数量角色')
        if unit == 'm' and (object_type!='skirting_finish' or method not in {'boundary_intervals_net','deferred'} or quantity_role not in {'physical_length','bill_quantity_candidate'}):
            errors.append(f'{item_id}长度单位、对象、方法或数量角色不一致')
        if unit == 'm²' and (method=='boundary_intervals_net' or quantity_role=='physical_length'):
            errors.append(f'{item_id}长度不能作为面积输出')
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
        audit = check.get('输入审计', {})
        frame = audit.get('frame_validation') if isinstance(audit, dict) else None
        if method in {'rectangle_net', 'rectangle_openings_net', 'material_partition_net'} or frame is not None:
            if not isinstance(frame, dict) or frame.get('status') not in {'bound_consistent', 'unbound', 'not_provided'}:
                errors.append(f'{item_id}缺少明确的局部坐标绑定核验状态')
            elif frame['status'] == 'bound_consistent':
                if method not in {'rectangle_net', 'rectangle_openings_net', 'material_partition_net'}:
                    errors.append(f'{item_id}非矩形方法不得声称矩形定位绑定通过')
                for key in ('frame_source_binding', 'edge_source_binding'):
                    bound = frame.get(key)
                    if not isinstance(bound, dict) or bound.get('evidence_ref') not in refs or any(
                            not nonempty(bound.get(k)) for k in ('source_id', 'source_sha256', 'source_pointer')):
                        errors.append(f'{item_id}缺少完整的框架或源边来源绑定: {key}')
                for key in ('bound_length_m', 'bound_height_m', 'source_edge_length_m'):
                    if not numeric(frame.get(key)) or frame[key] <= 0:
                        errors.append(f'{item_id}绑定矩形尺寸或源边长度非法: {key}')
                for key in ('surface_origin_m', 'source_edge_start_m', 'source_edge_end_m'):
                    point = frame.get(key)
                    if not isinstance(point, list) or len(point) != 3 or not all(numeric(v) for v in point):
                        errors.append(f'{item_id}绑定定位坐标非法: {key}')
                corners = frame.get('rectangle_corners_m')
                if not isinstance(corners, list) or len(corners) != 4 or any(
                        not isinstance(p, list) or len(p) != 3 or not all(numeric(v) for v in p) for p in corners):
                    errors.append(f'{item_id}绑定矩形须导出四个有限三维顶点')
                if not isinstance(frame.get('local_coordinate_frame'), dict):
                    errors.append(f'{item_id}绑定矩形缺少规范源框架')
            elif any(key in frame for key in ('frame_source_binding', 'edge_source_binding', 'rectangle_corners_m')):
                errors.append(f'{item_id}未绑定定位不得带已核绑定的审计字段')
        if method == 'material_partition_net':
            partition = audit.get('material_partition') if isinstance(audit,dict) else None
            selection = audit.get('partition_selection') if isinstance(audit,dict) else None
            if not isinstance(frame,dict) or frame.get('status') != 'bound_consistent':
                errors.append(f'{item_id}材料分区必须绑定母面框架')
            if unit != 'm²' or quantity_role != 'physical_area':
                errors.append(f'{item_id}材料分区只允许物理面积')
            if not isinstance(partition,dict) or not isinstance(selection,dict):
                errors.append(f'{item_id}缺少材料分区及输出选择审计')
            else:
                scenario=partition.get('scenario',{})
                mats=partition.get('material_regions')
                unknown=partition.get('unknown_region',{}).get('area_m2')
                outside=partition.get('outside_selected_scope',{}).get('area_m2',0.)
                net=partition.get('net_face_area_m2')
                coverage=partition.get('coverage',{'mode':'full_surface'})
                source=partition.get('partition_source_binding',{})
                if not isinstance(scenario,dict) or scenario.get('status') not in {'determined','conditional'} or not nonempty(scenario.get('scenario_id')):
                    errors.append(f'{item_id}材料分区缺少明确方案身份和状态')
                    scenario={}
                if scenario.get('status')=='conditional' and adoption!='reference':
                    errors.append(f'{item_id}条件材料分区不得作为采用量')
                if not isinstance(coverage,dict) or coverage.get('mode') not in {'full_surface','selected_regions'}:
                    errors.append(f'{item_id}分区计量范围模式非法')
                    coverage={}
                if coverage.get('mode')=='selected_regions':
                    if partition.get('full_surface_coverage') is not False or partition.get('base_material_complement') is not None:
                        errors.append(f'{item_id}仅选定区域不能补满母面或声称全母面已覆盖')
                    if not isinstance(coverage.get('source_binding'),dict) or not nonempty(coverage.get('basis')):
                        errors.append(f'{item_id}仅选定区域范围缺少来源绑定')
                elif outside!=0:
                    errors.append(f'{item_id}完整母面分区不得把未分配面积排除范围')
                if not numeric(unknown) or unknown<0 or not numeric(net) or net<0 or not numeric(outside) or outside<0 or not isinstance(mats,list):
                    errors.append(f'{item_id}材料与未知分区面积非法')
                else:
                    quantities={}; expected=set()
                    for mat in mats:
                        mid=mat.get('material_id') if isinstance(mat,dict) else None
                        amount=mat.get('effective_known_geometry',{}).get('area_m2') if isinstance(mat,dict) else None
                        if not nonempty(mid) or mid in quantities or not numeric(amount) or amount<0:
                            errors.append(f'{item_id}材料身份或面积非法')
                            continue
                        quantities[mid]=amount
                        if amount>0:expected.add(('material',mid))
                    if unknown>0:
                        expected.add(('unknown',None))
                        if scope!='partial' or not item.get('未决事项'):
                            errors.append(f'{item_id}未知或冲突区域必须保留partial及未决')
                    if not math.isclose(math.fsum(quantities.values())+unknown+outside,net,rel_tol=1e-12,abs_tol=1e-8):
                        errors.append(f'{item_id}材料及未知区域与净母面不守恒')
                    kind=selection.get('output_kind');mid=selection.get('material_id')
                    selected=unknown if kind=='unknown' else quantities.get(mid)
                    if kind not in {'material','unknown'} or not numeric(selected) or not numeric(value) or not math.isclose(selected,value,rel_tol=1e-12,abs_tol=1e-8):
                        errors.append(f'{item_id}分项数值与选定材料/未知面积不符')
                    if kind=='unknown' and adoption!='reference':
                        errors.append(f'{item_id}未知材料区域必须作为参考范围')
                    key=(partition.get('parent_surface_id'),partition.get('layer_scope_id'),scenario.get('scenario_id'))
                    fingerprint=(partition.get('partition_id'),source.get('source_sha256'),source.get('source_pointer'))
                    if any(not nonempty(k) for k in key+fingerprint):
                        errors.append(f'{item_id}材料分区缺少母面、层范围或来源定位')
                    else:
                        group=partition_groups.setdefault(key,{'fingerprint':fingerprint,'expected':expected,'actual':set()})
                        if group['fingerprint']!=fingerprint or group['expected']!=expected:
                            errors.append(f'{item_id}同一母面层范围场景混入不同分区')
                        selection_key=(kind,mid)
                        if selection_key in group['actual']:
                            errors.append(f'{item_id}材料分区有重复数量出口')
                        group['actual'].add(selection_key)
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
            errors.append(f"{item_id}数量不得为负")
        if numeric(value) and value == 0 and not isinstance(item.get("零值复核"), dict):
            errors.append(f"{item_id}零值缺少覆盖复核")
        if method == "deferred" and value is not None:
            errors.append(f"{item_id}deferred项不得有数值")
        if adoption == "reference" and check.get("进入采用小计") is not False:
            errors.append(f"{item_id}参考量不得进入采用小计")

    for key,group in partition_groups.items():
        if group['expected']-group['actual']:
            errors.append(f'材料分区{key}缺少正面积材料或未知区域单列出口')

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
        if len(refs) != len(set(refs)):
            errors.append(f"{subtotal.get('小计编号')}重复引用来源成果项")
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
            if item.get('单位') != subtotal.get('单位'):
                errors.append(f"{subtotal.get('小计编号')}混合长度与面积或不同单位")
            for field in ("对象类别", "计量基准", "范围完整性", "数量角色"):
                if check.get(field) != subtotal.get(field):
                    errors.append(f"{subtotal.get('小计编号')}混合{field}")
            for field in ('材料分区母面','材料分区层范围','材料分区场景','材料分区输出','材料编号'):
                if check.get(field,'') != subtotal.get(field,''):
                    errors.append(f"{subtotal.get('小计编号')}混合{field}")
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
