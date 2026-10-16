"""同一逻辑回路分段换型（登记 20261016-1056 导线换型的规则部分）：电缆到首灯、之后换电线，两行各自计量，用 logical_circuit_id 关联。

合成路径：A-B 水平 10 m、B-C 竖向 3 m、C-D 水平 6 m、A-D 水平 4 m。期望值按路径长度手算。
"""
import copy, importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('electrical_paths_lc', ROOT / 'skills/qto-electrical-routes/scripts/calculate_electrical_paths.py')
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)


def ledger():
    path = lambda pid, a, b, m, role: {'id': pid, 'from_node': a, 'to_node': b, 'length_m': m, 'role': role, 'source_ids': ['plan']}
    common = {'system': '照明', 'source_ids': ['plan', '设计说明 第 5 条'], 'parallel_count': 1, 'logical_circuit_id': 'AL1-WL1'}
    return {'schema': e.SCHEMA, 'unit': 'm', 'project_id': 'synthetic',
            'paths': [path('H1', 'A', 'B', 10, 'horizontal'), path('V1', 'B', 'C', 3, 'vertical'), path('H2', 'C', 'D', 6, 'horizontal'),
                      path('X', 'A', 'D', 4, 'horizontal')],
            'circuits': [dict(common, id='WL1-W', physical_id='wl1-wire', kind='wire', spec='BV-2.5', start_node='B', end_node='D',
                              path_ids=['V1', 'H2'], parallel_count=3),                         # listed first: order comes from the nodes
                         dict(common, id='WL1-CB', physical_id='wl1-cable', kind='cable', spec='YJV-3x2.5', start_node='A', end_node='B',
                              path_ids=['H1'])]}


def test_cable_then_wire_chain_into_one_logical_circuit():
    o = e.calculate(ledger())
    assert o['logical_circuits'] == [{'logical_circuit_id': 'AL1-WL1', 'segment_ids': ['WL1-CB', 'WL1-W'], 'start_node': 'A', 'end_node': 'D',
                                      'segments': [{'id': 'WL1-CB', 'kind': 'cable', 'spec': 'YJV-3x2.5', 'parallel_count': 1, 'length_m': 10},
                                                   {'id': 'WL1-W', 'kind': 'wire', 'spec': 'BV-2.5', 'parallel_count': 3, 'length_m': 27}]}]
    byid = {r['id']: r for r in o['items']}
    assert byid['WL1-W']['logical_circuit_id'] == byid['WL1-CB']['logical_circuit_id'] == 'AL1-WL1'
    assert {(s['kind'], s['spec']): s['length_m'] for s in o['summary']} == {('cable', 'YJV-3x2.5'): 10, ('wire', 'BV-2.5'): 27}


def test_without_logical_ids_nothing_changes():
    d = ledger()
    for r in d['circuits']: r.pop('logical_circuit_id')
    o = e.calculate(d)
    assert o['logical_circuits'] == [] and all(r['logical_circuit_id'] is None for r in o['items'])
    assert o['unused_path_ids'] == ['X']


@pytest.mark.parametrize('fault,fragment', [
    ('gap', '首尾相接'),        # wire starts at C, cable ends at B
    ('overlap', '路径重叠'),    # wire goes back over H1
    ('branch', '分叉'),         # two segments leave B
    ('cycle', '首尾相接'),      # no head: the chain closes on itself
    ('system', '系统不一致'),
    ('blank', '缺少非空文字'),
])
def test_bad_logical_circuits_are_rejected(fault, fragment):
    d = ledger(); w, cb = d['circuits']
    if fault == 'gap': w.update(start_node='C', path_ids=['H2'])
    if fault == 'overlap': w.update(path_ids=['H1', 'X'])
    if fault == 'branch': d['circuits'].append(dict(copy.deepcopy(w), id='WL1-W2', physical_id='wl1-wire-2', end_node='C', path_ids=['V1']))
    if fault == 'cycle': w.update(end_node='A', path_ids=['V1', 'H2', 'X'])
    if fault == 'system': w['system'] = '应急照明'
    if fault == 'blank': w['logical_circuit_id'] = ' '
    with pytest.raises(ValueError, match=fragment):
        e.calculate(d)
