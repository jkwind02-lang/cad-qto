"""Anonymous DXF regression: logical selection and source-bound T16 endpoints.

Every source is extracted with the shipped neutral/point path. Malformed T16
variants deliberately exercise the consumer boundary, never human approval.
"""
import copy
from pathlib import Path

import pytest

import test_electrical_network_roles_t34_20261019 as h
import qto_cross_drawing_registration as cross


def quantity(result):
    return sum(i['known_length_m'] for i in result['reference_calculation']['items'])


@pytest.fixture(scope='module')
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('t34-source-scope')
    ma, ra, ba, la = h.fixture(root / 'current')
    mb, rb, bb, lb = h.fixture(root / 'other', second_shape=True)
    mc, rc, bc, lc = h.fixture(root / 'unrelated', near_port=True)
    full = copy.deepcopy(ra)
    other = dict(full['network_request']['connections'][0], id='OTHER', circuit_id='X-02')
    full['network_request']['connections'].append(other)
    full['connection_contexts'].append(dict(full['connection_contexts'][0], connection_id='OTHER'))
    full['network_request']['circuit_specs'].append(dict(full['network_request']['circuit_specs'][0], circuit_id='X-02'))
    for spec in full['network_request']['circuit_specs']:
        spec.update(kind='wire', system='报警', spec='匿名双绞线', parallel_count=2)
    result, path = h.save_roles(root / 'current', ma, full)
    request = dict(schema=h.match.REQUEST_SCHEMA,
                   expressions=[h.expression('X01', result, path, ba)], decisions=[])

    def source(key, mf):
        return dict(source_key=key, drawing_id=key, version='V1', floor='F1', scope_id='AREA',
                    neutral_manifest=str(mf), manifest_sha256=h.sha(mf),
                    unit=dict(factor_to_m=1, basis='INSUNITS=6'), layout='Model', expand_blocks=['DEVICE'])

    def make_t16(left, right, label):
        mf1, req1, basis1, lines1 = left
        mf2, req2, basis2, lines2 = right

        def endpoint(key, req):
            ep = req['network_request']['endpoint_objects'][0]['endpoint_binding']
            return dict(source_key=key, source_id=ep['physical_source_id'], instance_path=ep['instance_path'])

        refs = [endpoint('left', req1), endpoint('right', req2)]
        controls = []
        for n in (0, -1):
            controls.append(dict(from_ref=dict(source_key='left', source_id=lines1[n], instance_path=lines1[n], locator='start' if n == 0 else 'end'),
                                 to_ref=dict(source_key='right', source_id=lines2[n], instance_path=lines2[n], locator='start' if n == 0 else 'end'),
                                 basis='原图两端控制点', from_basis_source_ids=[basis1], to_basis_source_ids=[basis2]))
        doc = dict(schema=cross.SCHEMA, sources=[source('left', mf1), source('right', mf2)],
                   registration=dict(from_source_key='left', to_source_key='right', model='translation', max_residual_m=1e-6, control_pairs=controls),
                   candidate_tolerance_m=.1,
                   objects=[dict(ref, observation_id=str(n), object_id='device-A', object_type='device', kind='point') for n, ref in enumerate(refs)],
                   interpretations=[dict(interpretation_id='PAIR', observation_ids=['0', '1'], decision_origin='model', basis='两个源视图的工作同物解释',
                                         source_refs=[dict(ref, observation_id=str(n), basis_source_ids=[basis]) for n, (ref, basis) in enumerate(zip(refs, (basis1, basis2)))])])
        value = cross.resolve(doc, root)
        output = root / (label + '.json')
        h.role.write(output, value)
        return value, output

    correct, correct_path = make_t16((ma, ra, ba, la), (mb, rb, bb, lb), 'correct')
    wrong, wrong_path = make_t16((mb, rb, bb, lb), (mc, rc, bc, lc), 'unrelated')
    return dict(root=root, manifest=ma, full=full, result=result, path=path, request=request, basis=ba,
                correct=correct, correct_path=correct_path, wrong=wrong, wrong_path=wrong_path)


def request_with_t16(sources, tmp_path, variant):
    request = copy.deepcopy(sources['request'])
    value = copy.deepcopy(sources['correct'])
    if variant == 'unrelated':
        value = copy.deepcopy(sources['wrong'])
    elif variant == 'current_listed_but_not_pair_side':
        value = copy.deepcopy(sources['wrong'])
        current = copy.deepcopy(sources['correct']['sources'][0])
        current['source_key'] = 'unused-current'
        value['sources'].append(current)
    elif variant == 'missing_source':
        value['sources'] = value['sources'][1:]
    elif variant == 'missing_other_source':
        value['sources'] = value['sources'][:1]
    elif variant == 'missing_authority':
        value['sources'][0]['verified_source_files'].pop('authoritative_source')
    elif variant == 'exchange_sha_only':
        value['sources'][0]['verified_source_files']['authoritative_source'] = copy.deepcopy(value['sources'][1]['verified_source_files']['authoritative_source'])
    elif variant == 'stale_version':
        value['sources'][0]['version'] = 'V0'
    elif variant == 'stale_manifest':
        value['sources'][0]['manifest_sha256'] = '0' * 64
    elif variant == 'other_parent_same_handle':
        value['pairings'][0]['source_refs'][0].update(source_id='CAD-OTHER/PARENT/CAD-38', instance_path='CAD-OTHER/PARENT/CAD-38')
    elif variant == 'other_source_id_same_instance':
        value['pairings'][0]['source_refs'][0]['source_id'] = 'CAD-OTHER'
    elif variant == 'wrong_pair_side':
        value['pairings'][0]['source_refs'][0]['source_key'] = 'right'
    elif variant == 'correct_on_right':
        value['pairings'][0]['source_refs'].reverse()
    elif variant != 'correct':
        raise AssertionError(variant)
    path = tmp_path / ('t16-' + variant + '.json')
    h.role.write(path, value)
    request['expressions'][0]['endpoint_identities'][0].update(
        t16_result=dict(path=str(path), sha256=h.sha(path)), pairing_id='PAIR')
    return request


def test_only_selected_logical_circuit_exports_twenty_metres(sources):
    original = copy.deepcopy(sources['result'])
    result = h.match.match_expressions(sources['request'])
    assert {r['circuit_id'] for r in result['selected_working_ledger']['circuits']} == {'X-01'}
    assert quantity(result) == pytest.approx(20)
    assert len(result['working_exports']) == 2
    assert sources['result'] == original
    assert result['expressions'][0]['original_quantity'] == original['network_result']['circuit_quantities']


def test_unknown_selected_part_is_not_covered_by_other_known_circuit(sources, tmp_path):
    request = copy.deepcopy(sources['full'])
    request['network_request']['circuit_specs'][0].update(kind=None, system=None, spec=None, parallel_count=None)
    value = h.role.infer_roles(sources['manifest'], request)
    path = tmp_path / 'partially-known.json'
    h.role.write(path, value)
    expression = h.expression('X01', value, path, sources['basis'])
    result = h.match.match_expressions(dict(schema=h.match.REQUEST_SCHEMA, expressions=[expression], decisions=[]))
    assert {r['circuit_id'] for r in result['selected_working_ledger']['circuits']} == {'X-01'}
    assert quantity(result) == pytest.approx(10)
    assert len(result['coverage'][0]['unresolved_calculation_edge_ids']) == 1
    assert result['status'] == 'REVIEW_REQUIRED'
    assert any('数量继续null' in r['problem'] for r in result['review_items'])


def test_atom_parameter_comparison_uses_only_selected_circuit(sources):
    request = copy.deepcopy(sources['request'])
    expression = request['expressions'][0]
    result = h.match.match_expressions(request)
    expr = dict(result['expressions'][0], _result=copy.deepcopy(sources['result']))
    for row in expr['_result']['network_result']['ledger']['circuits']:
        if row['circuit_id'] == 'X-02':
            row.update(parallel_count=7, spec='另回路异规格')
    assert h.match.atom_parameters(expr, expr['edge_ids'][0]) == [('wire', '报警', '匿名双绞线', 2)]


@pytest.mark.parametrize('field,value', [('circuit_id', 'X-02'), ('system', '其他系统'), ('scope_id', 'OTHER')])
def test_selected_geometry_does_not_accept_wrong_logical_item(sources, tmp_path, field, value):
    role_result = copy.deepcopy(sources['result'])
    for row in role_result['network_result']['ledger']['circuits']:
        if row['circuit_id'] == 'X-01':
            row[field] = value
    path = tmp_path / 'invalid-logical-ledger.json'
    h.role.write(path, role_result)
    request = copy.deepcopy(sources['request'])
    request['expressions'][0]['role_result'] = dict(path=str(path), sha256=h.sha(path))
    result = h.match.match_expressions(request)
    assert result['selected_working_ledger']['circuits'] == []
    assert set(result['coverage'][0]['unresolved_calculation_edge_ids']) == set(result['expressions'][0]['edge_ids'])


@pytest.mark.parametrize('variant', ['correct', 'correct_on_right'])
def test_current_source_full_instance_t16_is_retained_as_working(sources, tmp_path, variant):
    request = request_with_t16(sources, tmp_path, variant)
    result = h.match.match_expressions(request)
    pairing = result['expressions'][0]['endpoint_identities'][0]['t16_pairing']
    assert pairing['state'] == 'working/unreviewed'
    assert pairing['same_physical_proven'] is False
    assert pairing['official'] is False
    assert result['human_approved'] is False
    assert quantity(result) == pytest.approx(20)


BAD_T16 = ['unrelated', 'current_listed_but_not_pair_side', 'missing_source', 'missing_other_source', 'missing_authority',
           'exchange_sha_only', 'stale_version', 'stale_manifest', 'other_parent_same_handle',
           'other_source_id_same_instance', 'wrong_pair_side']


@pytest.mark.parametrize('variant', BAD_T16)
def test_t16_pairing_must_bind_actual_current_source_side(sources, tmp_path, variant):
    request = request_with_t16(sources, tmp_path, variant)
    with pytest.raises(ValueError, match='T16'):
        h.match.match_expressions(request)


@pytest.mark.parametrize('variant', ['correct', 'unrelated', 'current_listed_but_not_pair_side', 'missing_source',
                                    'stale_version', 'stale_manifest', 'other_parent_same_handle', 'wrong_pair_side'])
def test_actual_match_cli_and_t39_enforce_source_and_workset(sources, tmp_path, variant):
    request = request_with_t16(sources, tmp_path, variant)
    path = tmp_path / 'request.json'
    h.role.write(path, request)
    output = tmp_path / 'result'
    done = h.run([h.SCRIPTS / 'match_electrical_network_expressions.py', '--request', path, '--output-dir', output])
    if variant != 'correct':
        assert done.returncode == 2, done.stdout + done.stderr
        assert 'T16' in done.stdout
        assert not output.exists()
        return
    assert done.returncode == 3, done.stdout + done.stderr
    result = h.role.read(output / '多平面网络表达结果.json')
    package = h.role.read(output / '选定工作出口成果行.json')
    rows = package['rows']
    assert len(rows) == 2 and sum(r['quantity'] for r in rows) == pytest.approx(20)
    assert len({r['row_id'] for r in rows}) == 2
    assert all(r['human_approved'] is False and r['official_adoption'] is False for r in rows)
    assert all(r['complete_length_m'] is None and r['pending_components'] for r in rows)
    assert result['actual_cli_returncodes'] == [dict(id='既有路径reference实际复算', returncode=0), dict(id='T39选定工作出口实际对账', returncode=3)]
    assert h.role.read(output / '选定工作出口基准.json')['complete'] is False
    assert quantity(result) == pytest.approx(20)


def test_multi_connection_expression_keeps_scope_and_unknowns(tmp_path):
    mf, request, basis, _ = h.fixture(tmp_path / 'branch', branch=True)
    # All branches are the same explicitly selected circuit; geometry alone does
    # not close their missing code/specification evidence.
    value, path = h.save_roles(tmp_path / 'branch', mf, request)
    expr = h.expression('branch', value, path, basis)
    expr['connection_ids'] = ['LINK', 'BRANCH']
    expr['endpoint_identities'].append(dict(object_key='C', identity_key='device-C', basis='原图端点C', basis_source_ids=[basis]))
    expression_request = dict(schema=h.match.REQUEST_SCHEMA, expressions=[expr], decisions=[])
    result = h.match.match_expressions(expression_request)
    assert quantity(result) == pytest.approx(10)
    assert len(result['coverage'][0]['unresolved_calculation_edge_ids']) == 2
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['official_adoption'] is False and result['human_approved'] is False
    request_path = tmp_path / 'multi-connection.json'
    h.role.write(request_path, expression_request)
    done = h.run([h.SCRIPTS / 'match_electrical_network_expressions.py', '--request', request_path, '--output-dir', tmp_path / 'multi-result'])
    assert done.returncode == 3, done.stdout + done.stderr
    actual = h.role.read(tmp_path / 'multi-result/多平面网络表达结果.json')
    assert quantity(actual) == pytest.approx(10)
    assert len(actual['coverage'][0]['unresolved_calculation_edge_ids']) == 2


@pytest.mark.parametrize('selected,expected', [(['LINK'], 20), (['OTHER'], 26), (['LINK', 'OTHER'], 26)])
def test_same_circuit_extras_follow_selected_logical_connection(sources, tmp_path, selected, expected):
    request = copy.deepcopy(sources['full'])
    network = request['network_request']
    network['connections'][1]['circuit_id'] = 'X-01'
    network['circuit_specs'] = network['circuit_specs'][:1]
    network['circuit_specs'][0]['extras'] = [dict(id='OTHER-END', connection_id='OTHER', at='to', length_each_m=3,
                                                length_basis='network_model', basis='仅OTHER连接端头有据附属长度',
                                                basis_ids=[sources['basis']], source_ids=[sources['basis']])]
    role_result = h.role.infer_roles(sources['manifest'], request)
    path = tmp_path / 'same-circuit-extra.json'
    h.role.write(path, role_result)
    expr = h.expression('X01', role_result, path, sources['basis'])
    expr['connection_ids'] = selected
    expression_request = dict(schema=h.match.REQUEST_SCHEMA, expressions=[expr], decisions=[])
    request_path = tmp_path / 'request.json'
    h.role.write(request_path, expression_request)
    original = copy.deepcopy(role_result)
    result = h.match.match_expressions(expression_request)
    assert quantity(result) == pytest.approx(expected)
    assert role_result == original
    assert result['expressions'][0]['original_quantity'] == original['network_result']['circuit_quantities']
    done = h.run([h.SCRIPTS / 'match_electrical_network_expressions.py', '--request', request_path, '--output-dir', tmp_path / 'actual'])
    assert done.returncode == 3, done.stdout + done.stderr
    package = h.role.read(tmp_path / 'actual/选定工作出口成果行.json')
    assert sum(r['quantity'] for r in package['rows']) == pytest.approx(expected)


def test_multi_connection_must_share_exact_context(sources, tmp_path):
    role_result = copy.deepcopy(sources['result'])
    role_result['connections'][1]['circuit_id'] = 'X-01'
    role_result['connections'][1]['context']['revision'] = 'V0'
    path = tmp_path / 'mismatched-context.json'
    h.role.write(path, role_result)
    request = copy.deepcopy(sources['request'])
    request['expressions'][0].update(role_result=dict(path=str(path), sha256=h.sha(path)), connection_ids=['LINK', 'OTHER'])
    with pytest.raises(ValueError, match='上下文/系统/回路'):
        h.match.match_expressions(request)
