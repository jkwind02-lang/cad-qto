#!/usr/bin/env python3
"""Audit explicit pipe parts, physical owners and manual comparison rows.

This is an evidence-ledger checker, not a drawing recognizer or an estimator.
It never fills missing lengths from a manual total or a residual difference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

VERSION = 'qto-pipe-correspondence/1.0'
ROLES = {'plan': 'route', 'vertical': 'route', 'valve_group': 'valve_group',
         'device_connection': 'device'}


def number(value, label, *, positive=False, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label}: 必须为有限数值')
    if not signed and (value < 0 or (positive and value == 0)):
        raise ValueError(f'{label}: 数值范围不合法')
    return value


def indexed(rows, key):
    result = {}
    for row in rows:
        name = row.get(key)
        if not isinstance(name, str) or not name.strip() or name in result:
            raise ValueError(f'{key}: 缺失或重复: {name}')
        result[name] = row
    return result


def audit(data):
    if data.get('schema_version') != VERSION:
        raise ValueError('不支持的对应台账版本')
    sources = indexed(data.get('sources', []), 'source_id')
    if not sources:
        raise ValueError('缺少来源')
    for source in sources.values():
        if source.get('kind') not in {'drawing', 'manual', 'other'}:
            raise ValueError('来源必须区分图纸、人工底稿或其他资料')
        p = Path(source['path'])
        expected = source.get('sha256')
        if not expected or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError(f"来源缺失或已变化: {source['source_id']}")

    def refs(rows):
        if not rows or not isinstance(rows, list):
            raise ValueError('缺少具体证据引用')
        for r in rows:
            if r.get('source_id') not in sources or not str(r.get('locator', '')).strip():
                raise ValueError('证据来源或定位无效')

    def drawing_refs(rows):
        refs(rows)
        if not any(sources[r['source_id']]['kind'] == 'drawing' for r in rows):
            raise ValueError('缺少图纸来源；人工底稿引用不能冒充几何或独立实例证明')

    members = indexed(data.get('members', []), 'member_id')
    for m in members.values():
        refs(m.get('source_refs'))
        n = number(m.get('count'), m['member_id'], positive=True)
        if int(n) != n or m.get('kind') not in set(ROLES.values()):
            raise ValueError('对象类型或整数数量无效')
        if not m.get('scope') or not m.get('system'):
            raise ValueError('对象缺少区域/楼层范围或系统')
        if m.get('identity') not in {'physical', 'aggregate', 'candidate'}:
            raise ValueError('对象必须说明是独立物理实例、汇总组还是候选')
        if m['identity'] == 'physical' and n != 1:
            raise ValueError('独立物理实例数量必须为1')
        if m.get('identity_basis') not in {'drawing', 'manual', 'candidate'}:
            raise ValueError('对象识别依据无效')
        if m['identity_basis'] == 'drawing':
            drawing_refs(m['source_refs'])

    parts = indexed(data.get('parts', []), 'part_id')
    calculated = {}
    used_extents = {}
    for p in parts.values():
        refs(p.get('source_refs'))
        m = members.get(p.get('member_id'))
        if m is None or ROLES.get(p.get('role')) != m['kind']:
            raise ValueError('部位与计数对象不一致；阀组共用管不得乘设备台数')
        if p.get('unit') not in {'m', '个', '套'} or not p.get('specification'):
            raise ValueError('缺少规格或单位无效')
        extent = p.get('extent_id')
        if not extent or extent in used_extents:
            raise ValueError(f'同一实物区段不能在平面/竖向/详图/多个视图重复计量: {extent}')
        used_extents[extent] = p['part_id']
        b = p.get('basis', {})
        kind = b.get('kind')
        refs(b.get('source_refs'))
        runs = number(b.get('runs', 1), '供回/并行根数', positive=True)
        if int(runs) != runs:
            raise ValueError('并行根数必须为正整数')
        if runs != 1:
            refs(b.get('runs_source_refs'))
        if kind in {'allowance', 'measured', 'count'}:
            value = number(b.get('value'), '每实例数值')
            expression = str(value)
            if kind == 'count' and (p['unit'] == 'm' or int(value) != value):
                raise ValueError('计件依据只支持整数个/套')
            if kind == 'measured' and p['unit'] != 'm':
                raise ValueError('测长依据单位必须为m')
        elif kind == 'dimension_chain':
            if p['unit'] != 'm' or not b.get('lengths_m'):
                raise ValueError('尺寸链须显式换算为米并提供各段')
            values = [number(v, '尺寸链') for v in b['lengths_m']]
            value = sum(values)
            expression = '+'.join(map(str, values))
        elif kind == 'elevation_pair':
            if p['unit'] != 'm' or not b.get('datum'):
                raise ValueError('竖向须提供统一标高基准和米单位')
            refs(b.get('from_source_refs')); refs(b.get('to_source_refs'))
            drawing_refs(b['from_source_refs']); drawing_refs(b['to_source_refs'])
            z1 = number(b.get('from_m'), '起点标高', signed=True)
            z2 = number(b.get('to_m'), '终点标高', signed=True)
            value = abs(z2 - z1)
            expression = f'abs({z2}-{z1})'
        else:
            raise ValueError('长度依据类型无效')
        if kind != 'allowance' and b.get('evidence_origin') != 'drawing':
            raise ValueError('图纸几何/计件依据不得由人工汇总数冒充')
        if kind != 'allowance':
            drawing_refs(b['source_refs'])
            if runs != 1:
                drawing_refs(b['runs_source_refs'])
        if kind == 'elevation_pair' and p['role'] != 'vertical':
            raise ValueError('标高差只能用于竖向段')
        quantity = number(value * runs * m['count'], '计算结果')
        calculated[p['part_id']] = dict(p, scope=m['scope'], system=m['system'],
            member_count=m['count'], quantity=quantity,
            expression=f'({expression}) × {runs} × {m["count"]}',
            drawing_supported=(kind != 'allowance' and m['identity'] == 'physical'
                               and m['identity_basis'] == 'drawing'))

    results = []
    for c in indexed(data.get('comparisons', []), 'comparison_id').values():
        refs(c.get('source_refs'))
        ids = c.get('part_ids', [])
        if not ids or len(set(ids)) != len(ids) or any(i not in calculated for i in ids):
            raise ValueError('对照行必须引用非空且不重复的已知区段')
        selected = [calculated[i] for i in ids]
        if any(p['unit'] != c.get('unit') or p['specification'] != c.get('specification')
               or p['system'] != c.get('system') for p in selected):
            raise ValueError('对照行不得混合单位、规格或系统')
        scopes = c.get('expected_scopes')
        if not isinstance(scopes, list) or not scopes or len(set(scopes)) != len(scopes):
            raise ValueError('缺少明确且不重复的对照范围')
        expected_members = c.get('expected_member_ids')
        if expected_members is not None and (len(set(expected_members)) != len(expected_members)
                or any(i not in members for i in expected_members)):
            raise ValueError('预期对象清单有重复或未知对象')
        value = number(c.get('manual_quantity'), '人工底稿数量')
        total = number(sum(p['quantity'] for p in selected), '区段合计')
        delta = total - value
        selected_members = set(p['member_id'] for p in selected)
        actual_scopes = set(p['scope'] for p in selected)
        outside = [p['part_id'] for p in selected if p['scope'] not in scopes]
        issues = []
        if outside or actual_scopes != set(scopes): issues.append('SCOPE_MISMATCH')
        if not math.isclose(total, value, abs_tol=1e-6, rel_tol=0): issues.append('QUANTITY_MISMATCH')
        if expected_members is None: issues.append('MEMBER_COVERAGE_UNKNOWN')
        elif selected_members != set(expected_members): issues.append('MEMBER_MISMATCH')
        if not all(p['drawing_supported'] for p in selected): issues.append('DRAWING_BASIS_INCOMPLETE')
        if c.get('extent_coverage') not in {'complete', 'partial', 'unknown'}:
            raise ValueError('须声明对照行区段覆盖是否完整')
        if c['extent_coverage'] != 'complete': issues.append('EXTENT_COVERAGE_INCOMPLETE')
        else: refs(c.get('coverage_source_refs'))
        count = sum(members[i]['count'] for i in selected_members)
        declared = c.get('manual_member_count')
        if declared is not None and count != number(declared, '底稿对象数量'):
            issues.append('COUNT_MISMATCH')
        results.append(dict(c, calculated_quantity=total, difference=delta,
            actual_member_count=count, actual_scopes=sorted(actual_scopes),
            outside_scope_part_ids=outside,
            missing_member_ids=sorted(set(expected_members or []) - selected_members),
            extra_member_ids=sorted(selected_members - set(expected_members or [])) if expected_members is not None else [],
            issues=issues, correspondence_complete=not issues,
            arithmetic_match=math.isclose(total, value, abs_tol=1e-6, rel_tol=0)))
    if not results:
        raise ValueError('缺少对照行')
    referenced = {i for c in results for i in c['part_ids']}
    return dict(schema_version=VERSION, sources=list(sources.values()),
        members=list(members.values()), parts=list(calculated.values()), comparisons=results,
        unreferenced_part_ids=sorted(set(parts) - referenced),
        note='仅核验输入台账的一致性；图纸识别、采用版本与区段完整性仍需独立取证。不得将对照差额补作接管或竖向量。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = audit(json.loads(args.input.read_text(encoding='utf-8-sig')))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'对应检查失败: {exc}\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                           encoding='utf-8', newline='\n')
    print(json.dumps({'comparison_count': len(result['comparisons']),
        'complete_count': sum(c['correspondence_complete'] for c in result['comparisons'])}))


if __name__ == '__main__':
    main()
