"""Explicit project metering of serial accessory axes; never raw pipe length."""
from collections import defaultdict


def pipe_bridge_quantities(bridges, assignments, valves, points, tolerance, point_distance, restore):
    ordinary = [b for b in bridges if b.get('quantity_action') != 'include_occupied_axis']
    items, grouped, issues = restore(ordinary, assignments, valves, points, tolerance, point_distance)
    grouped = defaultdict(float)
    for bridge in bridges:
        if bridge.get('quantity_action') != 'include_occupied_axis':
            continue
        sides = [assignments[bridge[k]['segment_id']] for k in ('a', 'b')]
        dn = sides[0]['dn'] if sides[0]['dn'] and sides[0]['dn'] == sides[1]['dn'] else None
        item = {'bridge_id': bridge['bridge_id'], 'system': bridge['system'], 'space_id': bridge['space_id'],
                'classification': bridge['classification'], 'length': bridge['virtual_length'], 'dn': dn,
                'action': 'include_occupied_axis', 'included_in_reconstructed': False,
                'source_id': bridge['source_id'], 'quantity_basis': bridge.get('quantity_basis'),
                'occupying_source_object_ids': bridge.get('occupying_source_object_ids', [])}
        if not bridge.get('coverage_evidence') or not bridge.get('quantity_basis'):
            item['status'] = 'BLOCKED_OCCUPANCY_BASIS'
        elif not dn:
            item['status'] = 'BLOCKED_DN'
        else:
            level = '方案暂估量'
            item.update(status='PROJECT_INCLUSIVE_AXIS', quantity_level=level, included_in_reconstructed=True,
                        measurement_basis='项目有据阀件轴长不扣口径；含附件轴长，不代表净管切割长度')
        if not item['included_in_reconstructed']:
            issues.append({'type': '串联附件含轴长计量条件未闭合', 'bridge_id': bridge['bridge_id'], 'status': item['status']})
        items.append(item)
    by_id={b['bridge_id']:b for b in bridges}
    for item in items:
        bridge=by_id[item['bridge_id']]
        sides=[assignments[bridge[side]['segment_id']] for side in ('a','b')]
        if item['action']=='restore_omission' and not bridge.get('omission_within_gap_max',True) and item['status']=='RESTORED':
            item.update(status='UNMATCHED_OMISSION_THRESHOLD',included_in_reconstructed=False)
            issues.append({'type':'省略恢复未匹配原gap_max，不能借用T25搜索阈值','bridge_id':bridge['bridge_id']})
        explicit_restoration=item['action']=='restore_omission' and item['status']=='RESTORED'
        depends=any(s.get('requires_topology_assumption') for s in sides) if explicit_restoration else True
        item['assumption_ids']=sorted(({bridge['assumption_id']} if depends else set())|
            {a for side in sides for a in side.get('assumption_ids',[])})
        item['depends_on_candidate_topology']=depends
        if explicit_restoration:
            # Drawing-backed omission length is an independent measurement fact.
            # Keep the original restorer's DN/evidence layering; it already caps
            # borrowed/scenario DN and retains unknown DN without guessing.
            item['measurement_basis']='explicit_drawing_omission'
            item['dn_candidates']=sorted({s['dn'] for s in sides if s['dn']})
        else:
            item['quantity_level']='不适用' if item['action']=='exclude' else '方案暂估量'
        if item['included_in_reconstructed']:
            if not explicit_restoration:
                item['quantity_level']='方案暂估量' if item['dn'] else '未决量'
            grouped[(item['system'],item['quantity_level'],item['dn'] or 'DN待定')]+=item['length']
    order = {b['bridge_id']: i for i, b in enumerate(bridges)}
    items.sort(key=lambda row: order[row['bridge_id']])
    return items, grouped, issues
