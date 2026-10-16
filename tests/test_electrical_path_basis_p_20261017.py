"""第五批 P：路径依据分级与同范围冲突（登记 20261016-1044 实际路径与本楼标高 R5；20261016-1056 期望 2）。

合成台账，米。边界清单（备课第二节 P，取自 1044 R5 验收 A15～A17）：
- 路径行写 length_basis（actual_drawn_path / network_model / device_distance / assumption）和 basis_ids；
- 一项的数量层级按最弱的一段定：含 device_distance 或 assumption → 方案暂估量；其余 → 规则推算量；只改标签，不改数；
- 不写 length_basis 的旧输入层级不变，只提醒 length_basis_missing；
- scope_claims 声明“对象 X 在范围 S、路径方案 P 下的已证子量”：
  - 同对象、同范围、同方案，本项量小于已证子量 → scope_conflict（已证水平 104.984 m、同路径全长 89.53 m）；
  - 范围或方案不同 → not_comparable，不套下界；
  - 竖向未知不补 0：本项没有竖向路径、或竖向列为未决时，竖向和全长都不比；水平已证照常比。
"""
import copy, importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('electrical_paths_p', ROOT / 'skills/qto-electrical-routes/scripts/calculate_electrical_paths.py')
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
PROVEN_H = 53.52014880448812 + 51.46410626210878      # 1044 R5: two drawn runs, 104.984 m


def path(pid, a, b, m, role, basis=None):
    row = {'id': pid, 'from_node': a, 'to_node': b, 'length_m': m, 'role': role, 'source_ids': ['plan']}
    if basis:
        row.update(length_basis=basis, basis_ids=[f'basis-{pid}'])
    return row


def ledger():
    common = {'system': '动力', 'source_ids': ['plan'], 'parallel_count': 1}
    return {'schema': e.SCHEMA, 'unit': 'm', 'project_id': 'synthetic',
            'paths': [path('HB1', 'A', 'B', 32.97, 'horizontal', 'device_distance'), path('VB1', 'B', 'C', 30.53, 'vertical', 'assumption'),
                      path('HB2', 'C', 'D', 26.03, 'horizontal', 'device_distance'),
                      path('HD', 'E', 'F', 23.849, 'horizontal', 'actual_drawn_path'), path('HN', 'F', 'G', 7.5, 'horizontal', 'network_model'),
                      path('VA', 'G', 'H', 3.0, 'vertical', 'assumption'), path('HX', 'J', 'K', 12.0, 'horizontal', 'actual_drawn_path')],
            'channels': [dict(common, id='BUS-1', physical_id='bus-3#-1', kind='busbar', spec='1250A', start_node='A', end_node='D',
                              path_ids=['HB1', 'VB1', 'HB2'], scope_id='3#动力中心', path_scheme_id='v1')],
            'circuits': [dict(common, id='C-DRAWN', physical_id='c-drawn', kind='cable', spec='YJV-5x4', start_node='E', end_node='F', path_ids=['HD']),
                         dict(common, id='C-NET', physical_id='c-net', kind='cable', spec='YJV-5x4', start_node='E', end_node='G',
                              path_ids=['HD', 'HN'], parallel_count=2),
                         dict(common, id='C-ASSUME', physical_id='c-assume', kind='cable', spec='YJV-5x4', start_node='E', end_node='H',
                              path_ids=['HD', 'HN', 'VA']),
                         dict(common, id='C-HONLY', physical_id='c-honly', kind='cable', spec='YJV-5x4', start_node='J', end_node='K',
                              path_ids=['HX'], scope_id='S', path_scheme_id='v1', pending_components=['vertical', 'terminal'])]}


def claim(cid, obj, role, proven, scope='3#动力中心', scheme='v1'):
    return {'id': cid, 'physical_id': obj, 'scope_id': scope, 'path_scheme_id': scheme, 'role': role, 'proven_length_m': proven,
            'source_ids': ['codex 动力中心1250A母线_v1']}


def test_quantity_level_follows_the_weakest_path_basis():
    plain = copy.deepcopy(ledger())
    for p in plain['paths']:
        p.pop('length_basis', None); p.pop('basis_ids', None)
    o, old = e.calculate(ledger()), e.calculate(plain)
    byid, oldid = {r['id']: r for r in o['items']}, {r['id']: r for r in old['items']}
    assert {k: (r['quantity_level'], r['length_basis']) for k, r in byid.items()} == {
        'BUS-1': ('方案暂估量', 'assumption'), 'C-DRAWN': ('规则推算量', 'actual_drawn_path'),
        'C-NET': ('规则推算量', 'network_model'), 'C-ASSUME': ('方案暂估量', 'assumption'), 'C-HONLY': ('规则推算量', 'actual_drawn_path')}
    assert {k: r['length_m'] for k, r in byid.items()} == {k: r['length_m'] for k, r in oldid.items()}   # labels only, numbers unchanged
    assert byid['C-NET']['length_by_basis_each_m'] == pytest.approx({'actual_drawn_path': 23.849, 'network_model': 7.5})
    assert byid['C-ASSUME']['length_by_basis_each_m'] == pytest.approx({'actual_drawn_path': 23.849, 'network_model': 7.5, 'assumption': 3.0})
    assert o['quantity_level_counts'] == {'方案暂估量': 2, '规则推算量': 3} and o['warnings'] == []


def test_old_input_without_basis_keeps_its_level_and_is_flagged():
    d = ledger()
    for p in d['paths']:
        if p['id'] in ('HD', 'HB2'):
            p.pop('length_basis'); p.pop('basis_ids')
    o = e.calculate(d)
    byid = {r['id']: r for r in o['items']}
    assert byid['C-DRAWN']['quantity_level'] == '规则推算量' and byid['C-DRAWN']['length_basis'] is None
    assert byid['C-DRAWN']['length_basis_missing_path_ids'] == ['HD']
    assert byid['BUS-1']['quantity_level'] == '方案暂估量'                   # the known weak parts still count
    assert o['warnings'] == [{'warning': 'length_basis_missing', 'path_ids': ['HB2', 'HD']}]


@pytest.mark.parametrize('fault,fragment', [
    ('basis', '依据类别'), ('basis_ids', 'basis_ids'), ('pending', 'pending_components'),
    ('claim_role', 'role'), ('claim_length', 'proven_length_m'), ('claim_source', 'source_ids'), ('claim_dup', '编号重复'),
])
def test_bad_basis_and_claims_are_rejected(fault, fragment):
    d = ledger(); d['scope_claims'] = [claim('K1', 'bus-3#-1', 'horizontal', PROVEN_H)]
    k = d['scope_claims'][0]
    if fault == 'basis': d['paths'][0]['length_basis'] = 'guess'
    if fault == 'basis_ids': d['paths'][0]['basis_ids'] = []
    if fault == 'pending': d['circuits'][3]['pending_components'] = 'vertical'
    if fault == 'claim_role': k['role'] = 'diagonal'
    if fault == 'claim_length': k['proven_length_m'] = -1
    if fault == 'claim_source': k.pop('source_ids')
    if fault == 'claim_dup': d['scope_claims'].append(dict(k))
    with pytest.raises(ValueError, match=fragment):
        e.calculate(d)


def test_complete_quantity_below_a_proven_part_of_the_same_scope_is_a_conflict():
    d = ledger()
    d['scope_claims'] = [claim('K-H', 'bus-3#-1', 'horizontal', PROVEN_H), claim('K-T', 'bus-3#-1', 'total', PROVEN_H),
                         claim('K-OK', 'bus-3#-1', 'horizontal', 50.0),
                         claim('K-SCOPE', 'bus-3#-1', 'horizontal', PROVEN_H, scope='3#动力中心（变更）'),
                         claim('K-SCHEME', 'bus-3#-1', 'horizontal', PROVEN_H, scheme='v2'),
                         claim('K-NONE', 'bus-9#', 'horizontal', 1.0), claim('K-UNSCOPED', 'c-drawn', 'horizontal', 99.0)]
    o = e.calculate(d)
    checks = {c['claim_id']: c for c in o['scope_checks']}
    assert {k: c['status'] for k, c in checks.items()} == {'K-H': 'scope_conflict', 'K-T': 'scope_conflict', 'K-OK': 'consistent',
                                                            'K-SCOPE': 'not_comparable', 'K-SCHEME': 'not_comparable', 'K-NONE': 'not_comparable',
                                                            'K-UNSCOPED': 'not_comparable'}
    assert {k: c.get('reason') for k, c in checks.items() if c['status'] == 'not_comparable'} == {
        'K-SCOPE': '范围或路径方案不同，不套下界', 'K-SCHEME': '范围或路径方案不同，不套下界', 'K-NONE': '台账里没有这个对象',
        'K-UNSCOPED': '本项没有声明范围scope_id'}
    assert checks['K-H']['item_id'] == 'BUS-1' and checks['K-H']['item_length_m'] == pytest.approx(59.0)
    assert checks['K-T']['item_length_m'] == pytest.approx(89.53) and checks['K-T']['proven_length_m'] == pytest.approx(PROVEN_H)
    assert checks['K-SCOPE']['item_length_m'] is None                           # no lower bound is put on another scope
    assert o['status'] == 'REVIEW_REQUIRED' and o['scope_conflict_ids'] == ['K-H', 'K-T']
    assert {r['id']: r.get('scope_conflict_claim_ids') for r in o['items']}['BUS-1'] == ['K-H', 'K-T']


def test_unknown_vertical_is_not_taken_as_zero():
    d = ledger()
    d['scope_claims'] = [claim('K-H', 'c-honly', 'horizontal', 11.0, scope='S'), claim('K-H2', 'c-honly', 'horizontal', 12.5, scope='S'),
                         claim('K-EQ', 'c-honly', 'horizontal', 12.0, scope='S'),
                         claim('K-V', 'c-honly', 'vertical', 4.0, scope='S'), claim('K-T', 'c-honly', 'total', 20.0, scope='S')]
    o = e.calculate(d)
    checks = {c['claim_id']: c for c in o['scope_checks']}
    assert {k: c['status'] for k, c in checks.items()} == {'K-H': 'consistent', 'K-H2': 'scope_conflict', 'K-EQ': 'consistent',
                                                            'K-V': 'not_comparable', 'K-T': 'not_comparable'}
    item = {r['id']: r for r in o['items']}['C-HONLY']
    assert item['pending_components'] == ['vertical', 'terminal'] and item['complete_quantity'] is False
    assert item['length_m'] == pytest.approx(12.0)                             # horizontal part still given
    others = {r['id']: r for r in o['items']}
    assert others['C-DRAWN']['complete_quantity'] is True and others['C-DRAWN']['pending_components'] == []
    d['circuits'][3].pop('pending_components')                                 # no vertical path drawn: still not 0
    checks = {c['claim_id']: c for c in e.calculate(d)['scope_checks']}
    assert checks['K-V']['status'] == 'not_comparable' and checks['K-T']['status'] == 'scope_conflict'
    d = ledger()                                        # one riser drawn (3 m) but the vertical still listed pending
    d['circuits'][2].update(scope_id='S', path_scheme_id='v1', pending_components=['vertical'])
    d['scope_claims'] = [claim('K-V', 'c-assume', 'vertical', 5.0, scope='S')]
    assert e.calculate(d)['scope_checks'][0]['status'] == 'not_comparable'
