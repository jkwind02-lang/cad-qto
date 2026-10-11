"""Evidence-bound context ports and explicitly restored drawing omissions.

Context ports never add measured pipe length. Restoration has a separate
quantity outlet and never changes the raw drawn-centreline baseline.
"""
import math
from collections import defaultdict

LEVELS = {'confirmed': '确认量', 'inferred': '规则推算量', 'scenario': '方案暂估量'}


def boundary_context(raw, anchor, segments, tolerance):
    complete = raw.get('connections_complete', False)
    if not isinstance(complete, bool):
        raise ValueError('connections_complete须为布尔值')
    ports = raw.get('external_ports', [])
    if not isinstance(ports, list):
        raise ValueError('external_ports须为数组')
    if complete and (not ports or raw.get('fitting_quantity_owner') not in ('in_scope', 'outside_scope')):
        raise ValueError('完整边界须提供外接口及明确管件数量归属')
    ids, source_ends, resolved = set(), set(), []
    in_scope_sources = set(segments) | {s.get('parent_segment_id') for s in segments.values()}
    for port in ports:
        if not isinstance(port, dict):
            raise ValueError('外接口须为对象')
        for key in ('port_id', 'segment_id', 'source_id'):
            if not isinstance(port.get(key), str) or not port[key].strip():
                raise ValueError('外接口缺少'+key)
        if port.get('evidence_level') not in LEVELS:
            raise ValueError('外接口缺少证据层级')
        if port['segment_id'] in in_scope_sources:
            raise ValueError('范围外接口已在计量管段中，须重新核对边界，不能重复增加端口')
        if port.get('system', raw['system']) != raw['system'] or port.get('space_id', raw.get('space_id')) != raw.get('space_id'):
            raise ValueError('外接口不可跨系统或坐标空间拼接')
        if port.get('coordinate_unit') is not None and port['coordinate_unit'] != raw.get('coordinate_unit'):
            raise ValueError('外接口坐标单位须与边界相同')
        pts = []
        for key in ('point', 'outside_point'):
            value = port.get(key)
            if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
                raise ValueError('外接口须提供有限三维坐标 '+key)
            pts.append(value)
        if math.dist(pts[0], anchor) > max(tolerance, 1e-9):
            raise ValueError('外接口未落在所声明边界节点')
        distance = math.dist(*pts)
        if distance <= max(tolerance, 1e-9):
            raise ValueError('外接口方向证据不足')
        source_end = (port['segment_id'], tuple(pts[0]))
        if port['port_id'] in ids or source_end in source_ends:
            raise ValueError('边界外接口重复')
        ids.add(port['port_id']);source_ends.add(source_end)
        dn = port.get('dn')
        if dn is not None and (not isinstance(dn, str) or not dn.strip()):
            raise ValueError('外接口DN须为文本或null')
        resolved.append({**port, 'direction': [(b-a)/distance for a,b in zip(*pts)], 'quantity_included': False})
    return {'connections_complete': complete, 'external_ports': resolved,
            'fitting_quantity_owner': raw.get('fitting_quantity_owner', 'unresolved')}


def restore_omissions(bridges, assignments, valves, node_points, tolerance, point_distance):
    items, grouped, issues = [], defaultdict(float), []
    for bridge in bridges:
        action = bridge.get('quantity_action', 'scenario')
        dns = [assignments[bridge[k]['segment_id']]['dn'] for k in ('a', 'b')]
        dn = dns[0] if dns[0] and dns[0] == dns[1] else None
        item = {'bridge_id': bridge['bridge_id'], 'system': bridge['system'], 'space_id': bridge['space_id'],
                'classification': bridge['classification'], 'length': bridge['virtual_length'], 'dn': dn,
                'action': action, 'included_in_reconstructed': False, 'source_id': bridge['source_id']}
        if action == 'restore_omission':
            occupied = any(v['system'] == bridge['system'] and v.get('space_id') == bridge['space_id']
                           and point_distance(tuple(v['point']), node_points[bridge['node_a']], node_points[bridge['node_b']]) <= max(tolerance, 1e-9)
                           for v in valves)
            level = bridge.get('quantity_evidence_level')
            if occupied:
                item['status'] = 'BLOCKED_OCCUPANCY'
                issues.append({'type': '已声明省略补长与在线附件占位冲突', 'bridge_id': bridge['bridge_id']})
            elif not bridge.get('source_id') or level not in LEVELS:
                raise ValueError('恢复省略段须有明确图面来源及证据层级')
            else:
                levels = [level] + [assignments[bridge[k]['segment_id']]['evidence_level'] for k in ('a','b')]
                qlevel = '未决量' if not dn else ('方案暂估量' if 'scenario' in levels else '规则推算量')
                # Restoration is derived geometry even when its drawing evidence is confirmed.
                item.update(status='RESTORED', quantity_level=qlevel, included_in_reconstructed=True)
                grouped[(bridge['system'], qlevel, dn or 'DN待定')] += bridge['virtual_length']
        else:
            item['status'] = 'EXCLUDED' if action == 'exclude' else 'SCENARIO_ONLY'
        items.append(item)
    return items, grouped, issues
