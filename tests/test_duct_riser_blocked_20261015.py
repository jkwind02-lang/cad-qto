"""D1（登记 20261015-1115 风管_层间立管上端件未决时下端待办写无相邻层对应弯头）。

合成图（mm，输出 m）：1F 弯头 E4 在 S10 末端，中心已知；2F 正上方 S11 末端是上端弯头 E5。
宁波 P-SMK-A 3F 的上端块方向与管段相反（endpoint_relation: reversed），中心未决。
另有 2F 的 MA 系统反向弯头 E7 紧挨着，与 E4 不同系统，不得互列为候选；1F 的 E3 附近确实没有候选。
期望值按图面写出：立管长 = (5.5 + 2.0 - 0.2) - (0 + 3.0 + 0.25) = 4.05 m。
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'qto-duct-air-systems' / 'scripts'))
import infer_duct_fittings_vertical as duct


def seg(sid, floor, start, end, w=1000, h=500, el=None, ref='bottom', system='SME'):
    item = dict(segment_id=sid, floor_id=floor, system=system, start=start, end=end, width=w, height=h,
                size_evidence_level='确认', source_ids=[f'EV-{sid}'])
    if el is not None:
        item['elevation'] = dict(value=el, reference=ref, evidence_level='确认', source_id=f'TAG-{sid}')
    return item


def at_connection(fid, centre):
    return dict(centerline_point=centre, turn_plane='vertical', endpoint_relation='at_connection',
                source_id='CENTER-' + fid, basis='合成图中独立给定的中心线止点')


REVERSED = dict(endpoint_relation='reversed', source_id='BLK-DIR', basis='块方向与管段相反：接口法兰在远端')

PLAN = dict(
    schema_version='qto-duct-fitting-vertical-input/1.0', package_id='SYN', unit='mm', output_unit='m',
    floors=[dict(floor_id='1F', level=0.0, level_source_id='D035'),
            dict(floor_id='2F', level=5.5, level_source_id='D035', plan_offset=[0, 100000])],
    parameters=dict(parameter_basis='合成图：端点吻合 5mm，块范围容差 15mm'),
    segments=[seg('S10', '1F', [40000, 0], [45000, 0], el=3.0),
              seg('S11', '2F', [40000, 100000], [45000, 100000], w=800, h=400, el=2.0, ref='top'),
              seg('S21', '2F', [40000, 101500], [45000, 101500], w=800, h=400, el=2.0, ref='top', system='MA'),
              seg('S8', '1F', [0, 20000], [5000, 20000], w=500, h=300, el=2.0)],
    fittings=[dict(fitting_id='E4', floor_id='1F', kind='elbow', bbox=[44800, -600, 46000, 600], source_id='BLK-E4',
                   single_end_geometry=at_connection('E4', [45750, 0])),
              dict(fitting_id='E5', floor_id='2F', kind='elbow', bbox=[44800, 99400, 46000, 100600], source_id='BLK-E5',
                   single_end_geometry=dict(REVERSED)),
              dict(fitting_id='E7', floor_id='2F', kind='elbow', bbox=[44800, 100900, 46000, 102100], source_id='BLK-E7',
                   single_end_geometry=dict(REVERSED)),
              dict(fitting_id='E3', floor_id='1F', kind='elbow', bbox=[4800, 19400, 6000, 20600], source_id='BLK-E3',
                   single_end_geometry=at_connection('E3', [5750, 20000]))])


def infer(e5_geometry):
    data = copy.deepcopy(PLAN)
    data['fittings'][1]['single_end_geometry'] = e5_geometry
    return duct.infer(data)


def pendings(result, kind, fitting_id=None):
    return [p for p in result['pending'] if p['kind'] == kind and (fitting_id is None or p.get('fitting_id') == fitting_id)]


def test_lower_end_names_the_blocked_upper_candidate():
    result = infer(dict(REVERSED))
    assert not [item for item in result['vertical_items'] if item['kind'] == 'inter_floor_riser']
    lower = pendings(result, 'single_end_destination_unknown', 'E4')[0]
    assert lower['blocked_riser_candidates'] == ['E5']
    assert 'E5' in lower['reason'] and '单端几何未决' in lower['reason'] and '无相邻层对应弯头' not in lower['reason']
    # 附近确实没有候选的单端件，原因照旧
    assert '无相邻层对应弯头' in pendings(result, 'single_end_destination_unknown', 'E3')[0]['reason']


def test_upper_end_lists_possible_lower_and_one_combined_item():
    result = infer(dict(REVERSED))
    for kind in ('single_end_plane_unresolved', 'single_end_turn_plane_unknown'):
        item = pendings(result, kind, 'E5')[0]
        assert item['possible_riser_partners'] == ['E4'] and 'E4' in item['reason']
    combined = pendings(result, 'inter_floor_riser_blocked_by_single_end_geometry')
    assert len(combined) == 1
    assert combined[0]['fitting_ids'] == ['E4', 'E5'] and combined[0]['blocked_fitting_ids'] == ['E5']
    assert combined[0]['position_basis']['E5'] == 'segment_end_approximation' and 'scenario_id' not in combined[0]
    # 不同系统的 E7 不列为 E4 的候选
    assert all('possible_riser_partners' not in item for item in result['pending'] if item.get('fitting_id') == 'E7')
    assert result['vertical_scenarios'] == [] and result['metrics']['totals_complete'] is False


def test_reversed_block_with_riser_projection_gives_scenario_outside_totals():
    result = infer(dict(REVERSED, turn_plane='vertical', riser_projection=[30, 530]))
    [scenario] = result['vertical_scenarios']
    assert scenario['fitting_ids'] == ['E4', 'E5'] and scenario['evidence_level'] == '方案暂估'
    assert abs(scenario['length'] - 4.05) < 1e-9
    assert scenario['position_basis']['E5'] == 'reversed_riser_projection'
    # 方案不并入规则推算，也不并入方案暂估合计；平面段仍未决
    assert result['metrics']['vertical_length_rule'] == 0 and result['metrics']['vertical_length_scenario'] == 0
    assert pendings(result, 'single_end_plane_unresolved', 'E5')
    combined = pendings(result, 'inter_floor_riser_blocked_by_single_end_geometry')
    assert combined[0]['scenario_id'] == scenario['scenario_id']


def test_reversed_projection_out_of_block_range_is_not_used():
    result = infer(dict(REVERSED, turn_plane='vertical', riser_projection=[30, 5000]))
    assert result['vertical_scenarios'] == []
    assert pendings(result, 'inter_floor_riser_blocked_by_single_end_geometry')[0]['position_basis']['E5'] == 'segment_end_approximation'


def test_resolved_upper_end_still_pairs_as_rule_inference():
    result = infer(at_connection('E5', [45750, 100000]))
    [riser] = [item for item in result['vertical_items'] if item['kind'] == 'inter_floor_riser']
    assert riser['fitting_ids'] == ['E4', 'E5'] and riser['evidence_level'] == '规则推算' and abs(riser['length'] - 4.05) < 1e-9
    assert not pendings(result, 'inter_floor_riser_blocked_by_single_end_geometry')
    assert not pendings(result, 'single_end_destination_unknown', 'E4')
