"""T36: synthetic sourced H, weakest extras, unresolved and alternative scopes."""
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/qto-electrical-routes/scripts/calculate_electrical_paths.py'
PYTHON = shutil.which('python') or sys.executable
spec = importlib.util.spec_from_file_location('electrical_t36', SCRIPT)
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def ledger():
    context = {'project_id': 'SYNTHETIC', 'scope_id': 'BUILDING-1-FLOORS-1-2', 'system': 'POWER'}
    refs = {'source_ids': ['DRAW-1'], 'basis': '合成剖面与本实例接线端口证据', 'level': 'inferred'}
    endpoints = {
        side: dict(refs, fact_id='Z-' + node, value=value, unit='m', datum_id='D0',
                   reference_id=node, measurement_kind='equipment_port_center')
        for side, node, value in [('from', 'A', 1.), ('to', 'B', 5.)]
    }
    bindings = {
        side: dict(refs, fact_id='PORT-' + node, reference_id=node, entity_id='DEVICE-' + node, port_id='P1')
        for side, node in [('from', 'A'), ('to', 'B')]
    }
    facts = []
    for row in endpoints.values():
        facts.append({**context, **{k: v for k, v in row.items() if k not in refs},
                      'id': row['fact_id'], 'level': 'inferred'})
    for row in bindings.values():
        facts.append(dict(context, id=row['fact_id'], measurement_kind='electrical_endpoint',
                          reference_id=row['reference_id'], entity_id=row['entity_id'], port_id=row['port_id'], level='inferred'))
    facts.append(dict(context, id='ROUTE-A-B', measurement_kind='route_shape', route_kind='vertical',
                      from_reference_id='A', to_reference_id='B', level='inferred'))
    link = dict(context, schema='qto-elevation-link/1.0', **endpoints, endpoint_bindings=bindings,
                route=dict(refs, kind='vertical', route_id='ROUTE-A-B'),
                sources=[{'id': 'DRAW-1', 'sha256': '1' * 64,
                          'locator': '合成剖面A-A／端口详图1（非真实工程）', 'facts': facts}])
    path = dict(id='H', from_node='A', to_node='B', length_m=4., role='vertical',
                length_basis='elevation_difference', basis_ids=[f['id'] for f in facts],
                source_ids=['DRAW-1'], scope_id=context['scope_id'], system=context['system'], elevation_link=link)
    item = dict(id='C1', physical_id='CABLE-1', kind='cable', system=context['system'], spec='SYN-5x4',
                start_node='A', end_node='B', path_ids=['H'], parallel_count=1, source_ids=['DRAW-1'],
                scope_id=context['scope_id'], path_scheme_id='sourced-H')
    return dict(schema=e.SCHEMA, project_id=context['project_id'], unit='m', paths=[path], channels=[], circuits=[item])


def fact(d, fid):
    return next(f for f in d['paths'][0]['elevation_link']['sources'][0]['facts'] if f['id'] == fid)


def extra(basis='project_rule'):
    row = dict(id='RESERVE', node_id='B', length_each_m=.8, basis='合成项目端接长度条款', source_ids=['DRAW-1'])
    if basis is not None:
        row.update(length_basis=basis, basis_ids=['RULE-1'])
    return row


def test_sourced_H_is_rule_and_never_formal_adoption():
    d = ledger(); out = e.calculate(d); item = out['items'][0]
    assert item['length_m'] == 4. and item['quantity_level'] == '规则推算量'
    assert item['length_basis'] == 'elevation_difference'
    assert item['length_by_basis_each_m'] == {'elevation_difference': 4.}
    assert item['basis_classification_complete'] and item['complete_quantity']
    assert not item['official_adoption'] and not out['business_adoption_allowed']
    check = out['elevation_checks']['H']
    assert check['vertical_difference'] == 4. and check['electrical_binding_check']['endpoint_binding_verified']
    assert d == ledger()  # calculation does not mutate the evidence input


def test_negative_delta_is_a_positive_length():
    d = ledger(); link = d['paths'][0]['elevation_link']; link['from']['value'] = 5.; link['to']['value'] = 1.
    fact(d, 'Z-A')['value'] = 5.; fact(d, 'Z-B')['value'] = 1.
    out = e.calculate(d)
    assert out['items'][0]['length_m'] == 4.
    assert out['elevation_checks']['H']['vertical_difference'] == -4.


@pytest.mark.parametrize('fault,fragment', [
    ('missing_link', 'elevation_link'), ('two_numbers', 'project_id'), ('missing_source', 'sources'),
    ('unknown_source', '未登记来源'), ('bad_hash', '哈希'), ('missing_scope', 'scope_id'),
    ('wrong_project', 'project_id'), ('source_scope', '适用范围'), ('source_system', 'system'),
    ('unknown_measurement', 'measurement_kind'), ('endpoint_missing', 'endpoint_bindings'),
    ('entity_missing', 'entity_id'), ('port_identity', '端点实体'), ('node_mismatch', 'reference_id'),
    ('measurement_identity', 'from_reference_id'), ('basis_only', 'basis_ids'), ('length_mismatch', '复算'),
    ('not_vertical', '纯竖向'), ('source_value', '数值'), ('item_scope', 'system/scope_id'),
])
def test_false_promotion_is_rejected(fault, fragment):
    d = ledger(); p = d['paths'][0]; link = p['elevation_link']
    if fault == 'missing_link': p.pop('elevation_link')
    if fault == 'two_numbers': p['elevation_link'] = {'from': 1., 'to': 5.}
    if fault == 'missing_source': link.pop('sources')
    if fault == 'unknown_source': link['from']['source_ids'] = ['OTHER']
    if fault == 'bad_hash': link['sources'][0]['sha256'] = 'bad'
    if fault == 'missing_scope': p.pop('scope_id')
    if fault == 'wrong_project': link['project_id'] = 'OTHER'
    if fault == 'source_scope': fact(d, 'Z-A').pop('scope_id')
    if fault == 'source_system': fact(d, 'Z-A')['system'] = 'OTHER'
    if fault == 'unknown_measurement': link['from']['measurement_kind'] = fact(d, 'Z-A')['measurement_kind'] = 'BOP'
    if fault == 'endpoint_missing': link.pop('endpoint_bindings')
    if fault == 'entity_missing': link['endpoint_bindings']['from'].pop('entity_id')
    if fault == 'port_identity': fact(d, 'PORT-A')['port_id'] = 'OTHER'
    if fault == 'node_mismatch': link['endpoint_bindings']['from']['reference_id'] = 'OTHER'
    if fault == 'measurement_identity': link['from']['reference_id'] = fact(d, 'Z-A')['reference_id'] = 'FLOOR'
    if fault == 'basis_only': p['basis_ids'] = ['nonempty-is-not-evidence']
    if fault == 'length_mismatch': p['length_m'] = 9.
    if fault == 'not_vertical': p['role'] = 'offset'
    if fault == 'source_value': fact(d, 'Z-A')['value'] = 0.
    if fault == 'item_scope': d['circuits'][0]['scope_id'] = 'OTHER'
    with pytest.raises(ValueError, match=fragment): e.calculate(d)


def different_datum(d):
    link = d['paths'][0]['elevation_link']; link['to']['datum_id'] = 'D1'; fact(d, 'Z-B')['datum_id'] = 'D1'


def test_different_datum_needs_sourced_transform_or_remains_null():
    d = ledger(); different_datum(d); d['paths'][0]['length_m'] = None
    out = e.calculate(d); item = out['items'][0]
    assert item['length_m'] is None and item['known_length_m'] == 0.
    assert item['length_components_each_m']['vertical'] is None
    assert item['quantity_level'] is None and not item['complete_quantity']
    assert item['pending_components'] == ['path:H']
    assert out['summary'][0]['length_m'] is None
    with pytest.raises(ValueError, match='须为null'):
        d['paths'][0]['length_m'] = 4.; e.calculate(d)


def test_different_datum_with_transform_is_recomputed():
    d = ledger(); different_datum(d); link = d['paths'][0]['elevation_link']
    transform = dict(transform_id='D1-D0', from_datum='D1', to_datum='D0', value=2., unit='m',
                     source_ids=['DRAW-1'], basis='合成基准换算关系', level='inferred')
    link['datum_transforms'] = [transform]
    fact(d, 'Z-B')['value'] = link['to']['value'] = 3.
    link['sources'][0]['facts'].append(dict(id='D1-D0', measurement_kind='datum_offset',
        project_id=d['project_id'], scope_id=link['scope_id'], system=link['system'],
        from_datum='D1', to_datum='D0', value=2., unit='m', level='inferred'))
    d['paths'][0]['basis_ids'].append('D1-D0')
    assert e.calculate(d)['items'][0]['length_m'] == 4.


def test_floor_is_not_a_port_without_sourced_offset():
    d = ledger(); link = d['paths'][0]['elevation_link']
    link['from']['measurement_kind'] = fact(d, 'Z-A')['measurement_kind'] = 'finished_floor'
    d['paths'][0]['length_m'] = None
    assert e.calculate(d)['items'][0]['length_m'] is None
    offset = dict(fact_id='OFFSET-A', measurement_kind='vertical_offset', value=0., unit='m',
                  source_ids=['DRAW-1'], basis='合成楼面至实际端口带符号高度', level='inferred')
    conversion = dict(conversion_id='CONVERT-A', method='signed_offset', target_reference_id='A', offset=offset,
                      source_ids=['DRAW-1'], basis='合成实际端口详图', level='inferred')
    link['from']['centerline_conversion'] = conversion
    context = {k: link[k] for k in ['project_id', 'scope_id', 'system']}
    link['sources'][0]['facts'] += [
        dict(context, id='OFFSET-A', measurement_kind='vertical_offset', value=0., unit='m', level='inferred'),
        dict(context, id='CONVERT-A', measurement_kind='centerline_conversion', method='signed_offset',
             from_fact_id='Z-A', from_reference_id='A', target_reference_id='A', datum_id='D0', operand_fact_id='OFFSET-A', level='inferred')]
    d['paths'][0]['basis_ids'] += ['OFFSET-A', 'CONVERT-A']; d['paths'][0]['length_m'] = 4.
    assert e.calculate(d)['items'][0]['quantity_level'] == '规则推算量'


@pytest.mark.parametrize('basis,expected', [('project_rule', '规则推算量'), ('actual_drawn_path', '规则推算量'),
    ('network_model', '规则推算量'), ('device_distance', '方案暂估量'), ('assumption', '方案暂估量'), (None, None)])
def test_extras_take_part_in_the_weakest_basis(basis, expected):
    d = ledger(); d['circuits'][0]['extras'] = [extra(basis)]
    out = e.calculate(d); item = out['items'][0]
    assert item['length_m'] == 4.8 and item['quantity_level'] == expected
    assert item['basis_classification_complete'] == (basis is not None)
    if basis is None:
        assert out['warnings'] == [{'warning': 'extra_length_basis_missing', 'item_id': 'C1', 'extra_ids': ['RESERVE']}]
    else: assert item['extra_by_basis_each_m'] == {basis: .8}


@pytest.mark.parametrize('field,value', [('length_basis', 'guess'), ('basis_ids', []), ('node_id', 'OTHER')])
def test_bad_extra_is_rejected(field, value):
    d = ledger(); x = extra(); x[field] = value; d['circuits'][0]['extras'] = [x]
    with pytest.raises(ValueError): e.calculate(d)


def test_mixed_actual_network_H_and_assumption_does_not_promote():
    d = ledger(); p = d['paths'][0]
    for pid, a, b, basis in [('D', 'X', 'Y', 'actual_drawn_path'), ('N', 'Y', 'A', 'network_model'), ('V', 'B', 'C', 'assumption')]:
        d['paths'].append(dict(id=pid, from_node=a, to_node=b, length_m=2., role='horizontal' if pid != 'V' else 'vertical',
                              source_ids=['DRAW-1'], length_basis=basis, basis_ids=[pid]))
    d['circuits'][0].update(start_node='X', end_node='C', path_ids=['D', 'N', 'H', 'V'])
    d['circuits'][0]['extras'] = [dict(extra(), node_id='C')]
    item = e.calculate(d)['items'][0]
    assert item['length_m'] == 10.8 and item['quantity_level'] == '方案暂估量'
    assert item['length_by_basis_each_m'] == {'actual_drawn_path': 2., 'network_model': 2., 'elevation_difference': 4., 'assumption': 2.}


def test_scenario_elevation_or_endpoint_is_never_rule():
    for fault in ['Z-A', 'PORT-A']:
        d = ledger(); link = d['paths'][0]['elevation_link']; fact(d, fault)['level'] = 'scenario'
        (link['from'] if fault == 'Z-A' else link['endpoint_bindings']['from'])['level'] = 'scenario'
        item = e.calculate(d)['items'][0]
        assert item['quantity_level'] == '方案暂估量' and item['length_basis'] == 'assumption'


def confirmed_ledger():
    d = ledger(); link = d['paths'][0]['elevation_link']
    for row in [link['from'], link['to'], link['route'], *link['endpoint_bindings'].values(), *link['sources'][0]['facts']]:
        row['level'] = 'confirmed'
    return d


def test_endpoint_adoption_level_propagates_to_nested_route_and_fingerprint():
    fingerprints = []
    for level, expected, item_level in [('confirmed', '规则推算量', '规则推算量'),
        ('inferred', '规则推算量', '规则推算量'), ('scenario', '方案暂估量', '方案暂估量')]:
        d = confirmed_ledger(); d['paths'][0]['elevation_link']['endpoint_bindings']['from']['level'] = level
        out = e.calculate(d); check = out['elevation_checks']['H']
        route = next(r for r in check['calculation_run']['nodes'] if r['id'] == 'route_length')
        node = next(r for r in check['calculation_graph']['nodes'] if r['id'] == 'route_length')
        assert out['items'][0]['length_m'] == check['value'] == route['value'] == 4.
        assert out['items'][0]['quantity_level'] == item_level
        assert check['evidence_level'] == level
        assert check['quantity_level'] == route['quantity_level'] == node['quantity_level'] == expected
        assert node['electrical_endpoint_bindings']['from']['level'] == level
        raw = {r['id']: r for r in check['calculation_run']['nodes']}
        assert all(raw[nid]['quantity_level'] == '确认量' for nid in ['fact:Z-A', 'fact:Z-B', 'vertical_difference', 'vertical_projection'])
        assert not out['business_adoption_allowed'] and not check['business_adoption_allowed']
        fingerprints.append((check['calculation_run']['input_fingerprint'], route['own_fingerprint'], route['fingerprint']))
    assert len(set(fingerprints)) == 3  # the adopted endpoint is a semantic dependency


def test_unresolved_electrical_route_stays_unknown_with_confirmed_source_evidence():
    d = confirmed_ledger(); link = d['paths'][0]['elevation_link']
    link['to'].update(value=None, status='unresolved', issue='端口高程未知')
    fact(d, 'Z-B').update(value=None, status='unresolved'); d['paths'][0]['length_m'] = None
    out = e.calculate(d); check = out['elevation_checks']['H']
    route = next(r for r in check['calculation_run']['nodes'] if r['id'] == 'route_length')
    graph = next(r for r in check['calculation_graph']['nodes'] if r['id'] == 'route_length')
    assert check['evidence_level'] == 'confirmed'
    assert out['items'][0]['length_m'] is check['value'] is route['value'] is None
    assert check['quantity_level'] == route['quantity_level'] == graph['quantity_level'] == '未决量'
    assert not out['items'][0]['complete_quantity'] and not out['business_adoption_allowed']


def test_endpoint_only_source_and_identity_are_route_semantic_dependencies():
    d = confirmed_ledger(); p = d['paths'][0]; link = p['elevation_link']; original = link['sources'][0]
    endpoint_facts = [r for r in original['facts'] if r['measurement_kind'] == 'electrical_endpoint']
    original['facts'] = [r for r in original['facts'] if r not in endpoint_facts]
    link['sources'].append(dict(id='PORT-SOURCE', sha256='2' * 64, locator='合成端口详图', facts=endpoint_facts))
    for binding in link['endpoint_bindings'].values(): binding['source_ids'] = ['PORT-SOURCE']
    p['source_ids'].append('PORT-SOURCE')
    first = e.calculate(d)['elevation_checks']['H']
    assert first['source_ids'] == first['dependency_source_ids'] == ['DRAW-1', 'PORT-SOURCE']
    route_before = next(r for r in first['calculation_run']['nodes'] if r['id'] == 'route_length')
    assert route_before['source_ids'] == ['DRAW-1', 'PORT-SOURCE']
    # A legal, explicitly changed entity interpretation with identical numeric
    # heights still invalidates the electrical route's semantic fingerprint.
    link['endpoint_bindings']['from']['entity_id'] = endpoint_facts[0]['entity_id'] = 'REINTERPRETED-DEVICE-A'
    second = e.calculate(d)['elevation_checks']['H']
    route_after = next(r for r in second['calculation_run']['nodes'] if r['id'] == 'route_length')
    assert route_before['value'] == route_after['value'] == 4.
    assert route_before['fingerprint'] != route_after['fingerprint']


def test_cannot_promote_scenario_endpoint_source_by_claiming_inferred():
    d = ledger(); fact(d, 'PORT-A')['level'] = 'scenario'
    with pytest.raises(ValueError, match='不能提升'): e.calculate(d)


def test_cannot_reuse_one_real_port_as_both_ends_of_nonzero_H():
    d = ledger(); link = d['paths'][0]['elevation_link']
    link['endpoint_bindings']['to']['entity_id'] = fact(d, 'PORT-B')['entity_id'] = 'DEVICE-A'
    with pytest.raises(ValueError, match='同一真实实体端口'): e.calculate(d)


def test_changed_original_source_bytes_are_rejected(tmp_path):
    d = ledger(); source = tmp_path / 'source.txt'; source.write_text('original synthetic evidence', encoding='utf-8')
    record = d['paths'][0]['elevation_link']['sources'][0]
    record['path'] = str(source); record['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    assert e.calculate(d)['items'][0]['length_m'] == 4.
    source.write_text('changed synthetic evidence', encoding='utf-8')
    with pytest.raises(ValueError, match='哈希发生变化'): e.calculate(d)


def test_unknown_height_does_not_hide_the_known_horizontal_component():
    d = ledger(); t = d['paths'][0]['elevation_link']['to']; t.update(value=None, status='unresolved', issue='终端标高待查')
    fact(d, 'Z-B').update(value=None, status='unresolved'); d['paths'][0]['length_m'] = None
    d['paths'].append(dict(id='PLAN', from_node='X', to_node='A', length_m=8., role='horizontal',
        source_ids=['DRAW-1'], length_basis='actual_drawn_path', basis_ids=['DRAWN-PLAN']))
    d['circuits'][0].update(start_node='X', path_ids=['PLAN', 'H'])
    item = e.calculate(d)['items'][0]
    assert item['length_m'] is None and item['known_length_m'] == 8.
    assert item['length_components_each_m']['horizontal'] == 8. and item['length_components_each_m']['vertical'] is None


def test_missing_extra_does_not_hide_a_known_weak_extra():
    d = ledger(); d['circuits'][0]['extras'] = [extra(None), dict(extra('assumption'), id='ASSUMED')]
    item = e.calculate(d)['items'][0]
    assert item['quantity_level'] == '方案暂估量' and item['quantity_basis'] == 'assumption'
    assert item['length_basis_missing_extra_ids'] == ['RESERVE'] and not item['basis_classification_complete']


def test_pending_is_not_overridden_by_sourced_H():
    d = ledger(); d['circuits'][0]['pending_components'] = ['terminal', 'reserve']
    item = e.calculate(d)['items'][0]
    assert item['length_m'] == 4. and item['quantity_level'] == '规则推算量'
    assert not item['complete_quantity'] and not item['official_adoption']


def alternatives():
    d = ledger(); a = d['circuits'][0]; a.update(alternative_group_id='ALT', pending_components=['terminal'])
    b = dict(a, id='C2', path_scheme_id='full-assumed', pending_components=[], extras=[extra('assumption')])
    d['circuits'].append(b)
    return d


def test_sourced_partial_H_and_assumed_complete_scheme_are_separate_and_exclusive():
    out = e.calculate(alternatives())
    assert [r['quantity_level'] for r in out['items']] == ['规则推算量', '方案暂估量']
    assert [r['complete_quantity'] for r in out['items']] == [False, True]
    assert len(out['summary']) == 2 and sorted(r['length_m'] for r in out['summary']) == [4., 4.8]
    assert all(r['combination_policy'] == 'mutually_exclusive' for r in out['summary'])
    assert out['alternative_groups'][0]['item_ids'] == ['C1', 'C2']
    d = alternatives(); d['scope_claims'] = [dict(id='KNOWN', physical_id='CABLE-1', scope_id=d['circuits'][0]['scope_id'],
        path_scheme_id='sourced-H', role='total', proven_length_m=20., source_ids=['DRAW-1'])]
    assert e.calculate(d)['scope_checks'][0]['status'] == 'not_comparable'  # pending remains pending


@pytest.mark.parametrize('fault', ['unmarked', 'single', 'same_scheme', 'different_scope', 'different_physical'])
def test_bad_alternatives_are_rejected(fault):
    d = alternatives()
    if fault == 'unmarked': d['circuits'][1].pop('alternative_group_id')
    if fault == 'single': d['circuits'].pop()
    if fault == 'same_scheme': d['circuits'][1]['path_scheme_id'] = 'sourced-H'
    if fault == 'different_scope': d['circuits'][1]['scope_id'] = 'OTHER'
    if fault == 'different_physical': d['circuits'][1]['physical_id'] = 'OTHER'
    with pytest.raises(ValueError): e.calculate(d)


@pytest.mark.parametrize('channels,fragment', [(['C1', 'C2'], '同时采用'), (['C2'], 'path_scheme_id')])
def test_circuit_cannot_mix_mutually_exclusive_channel_schemes(channels, fragment):
    d = alternatives(); d['channels'] = d.pop('circuits')
    for channel in d['channels']: channel['kind'] = 'conduit'
    circuit = dict(d['channels'][0], id='USE', physical_id='SEPARATE-CABLE', kind='cable', channel_ids=channels)
    circuit.pop('alternative_group_id'); d['circuits'] = [circuit]
    with pytest.raises(ValueError, match=fragment): e.calculate(d)


def test_old_four_bases_and_old_route_items_keep_their_numbers(tmp_path):
    old = {'route_items': [dict(id='OLD', kind='cable', system='S', spec='X', path_length_m=10., parallel_count=2, extra_each_m=1., evidence=['D'])]}
    source = tmp_path / 'old.json'; source.write_text(json.dumps(old), encoding='utf-8')
    run = subprocess.run([PYTHON, str(SCRIPT.with_name('calculate_electrical_routes_reference.py')), str(source)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert run.returncode == 0 and json.loads(run.stdout)['items'][0]['physical_or_conductor_length_m'] == 22.


@pytest.mark.parametrize('valid', [True, False])
def test_actual_CLI_accepts_sourced_H_and_rejects_basis_ids_only(tmp_path, valid):
    d = ledger()
    if not valid: d['paths'][0].pop('elevation_link')
    source = tmp_path / 'input.json'; source.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    run = subprocess.run([PYTHON, str(SCRIPT), str(source)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert run.returncode == (0 if valid else 2)
    assert json.loads(run.stdout)['status'] == ('REFERENCE_ONLY' if valid else 'INPUT_REQUIRED')
