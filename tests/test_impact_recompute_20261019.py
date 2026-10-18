"""Anonymous executable decision -> input revision -> real recalculation cases."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(Path(__file__).parent))
import review_comparison_fixture as fixture
from review_evidence_helpers import write_review
from qto_review_contract import normalize_view, empty_feedback
from qto_review_identity import snapshot
from recompute_qto_dependencies import recalculate
from qto_impact_recompute import (REQUEST_SCHEMA, execute, state_expectation, queue_markdown,
                                  NETWORK_REQUEST)

SCRIPT = ROOT / 'skills/_qto-shared-evidence/scripts/qto_impact_recompute.py'


def example_request(with_exception=True):
    view = fixture.view()
    keys = [o['catalog_key'] for o in view['objects']]
    def decision(ident, members, **extra):
        return {'id': ident, 'revision': 'v1', 'title': '共同导线根数假定', 'question': '是否采用有据的5根方案？',
            'kind': 'business_choice', 'scope': {'member_keys': members, 'excluded_member_keys': [],
                'basis': '匿名水平路径的导线根数；不含竖向和端头'},
            'refs': [{'kind': 'object', 'id': k} for k in members], 'facts': ['已有水平路径'],
            'gaps': ['根数待方法确认'], 'impacts': [{'unit': 'm', 'value': None, 'min': 20, 'max': 44,
                'level': '规则推算量', 'scope': '上述回路水平导线'}],
            'business_options': [{'id': 'apply', 'title': '按有据的5根方案'}, {'id': 'hold', 'title': '保留原3根并列未决'}],
            'evidence': [{'id': 'E1', 'source': '匿名导线详图D1', 'detail': '指定范围内5根', 'state': 'verified'}],
            'recommendation': {'id': 'REC-' + ident, 'version': '1', 'text': '按有据的5根方案',
                'reason': '匿名详图明确根数', 'assistant_source': '专业建议', 'evidence_ids': ['E1'],
                'business_option_id': 'apply', 'scope_member_keys': members}, **extra}
    ds = [decision('M1', keys)]
    if with_exception:
        ds.append(decision('X1', keys[2:], exception_of='M1'))
    view['review_workflow'] = {'schema': 'qto-review-workflow/1.0', 'enabled': True,
                               'decisions': ds}
    graph = {'schema': 'qto-calculation-graph/1.0', 'project_id': view['project_id'],
        'sources': [{'id': 'S' + str(i), 'sha256': str(i) * 64} for i in range(1, 4)],
        'rules': [{'id': 'R', 'sha256': 'a' * 64}], 'nodes': []}
    for i, length in enumerate([10., 12., 8.], 1):
        graph['nodes'] += [{'id': f'L{i}', 'unit': 'm', 'value': length, 'source_ids': [f'S{i}']},
            {'id': f'N{i}', 'unit': '1', 'value': 3, 'source_ids': [f'S{i}']},
            {'id': f'Q{i}', 'unit': 'm', 'expression': 'l*n', 'variables': {'l': f'L{i}', 'n': f'N{i}'},
             'rule_ids': ['R']}]
    req = {'schema': REQUEST_SCHEMA, 'view': view, 'state': {'version': 'input-v1', 'calculation_graph': graph},
        'bindings': [{'member_key': k, 'node_ids': [f'L{i}', f'N{i}', f'Q{i}'],
                     'system_ids': ['照明'], 'circuit_ids': [f'C{i}']} for i, k in enumerate(keys, 1)],
        'impact_context': [{'decision_id': 'M1', 'internal_working_basis': '首轮保留3根方案并列未决',
                            'first_pass_state': '水平路径已完成，待根数方案答复', 'shared_source_id': 'D1'}],
        'decision_changes': []}
    bind_feedback(req, ['M1'])
    return req


def bind_feedback(req, idents):
    v = normalize_view(req['view'])
    fb = empty_feedback(v)
    for d in v['review_workflow']['decisions']:
        if d['id'] in idents:
            fb['annotations']['审核事项|' + d['id']] = {'decision_snapshot': snapshot(d), 'action': '调整处理',
                'business_option_id': 'apply', 'confirmed_result': '采用5根方案', 'reason': '对照匿名详图',
                'user_note': '仅指定范围', 'exception_of': d.get('exception_of'), 'excluded_member_keys': [],
                'updated_at': '2026-10-19T00:00:00Z', 'history': []}
    req['feedback'] = fb
    return v


def parameter_change(req, ident='M1', numbers=(1, 2), value=5):
    v = normalize_view(req['view'])
    nodes = {n['id']: n for n in req['state']['calculation_graph']['nodes']}
    mutations = []
    for i in numbers:
        before = copy.deepcopy(nodes[f'N{i}'])
        after = {**before, 'value': value}
        mutations.append({'kind': 'parameter', 'collection': 'node', 'target_id': f'N{i}',
            'member_keys': [req['bindings'][i - 1]['member_key']], 'before': before, 'after': after})
    proposal = {'decision_id': ident, 'business_option_id': 'apply',
        'expected': state_expectation(req['state'], v), 'mutations': mutations}
    req['decision_changes'].append(proposal)
    return proposal


def result_rows(result):
    return {r['id']: r for r in result['calculation_run']['nodes']}


def test_common_multi_circuit_exception_and_real_recalculation():
    req = example_request()
    parameter_change(req)
    result = execute(req)
    rows = result_rows(result)
    assert [rows[f'Q{i}']['value'] for i in (1, 2, 3)] == [50, 60, 24]
    receipt = result['input_changes'][0]
    assert receipt['decision_id'] == 'M1' and receipt['scope']['held_by_exception'] == [req['bindings'][2]['member_key']]
    assert receipt['before']['input_fingerprint'] != receipt['after']['input_fingerprint']
    assert receipt['before']['result_fingerprint'] != receipt['after']['result_fingerprint']
    assert result['calculation_result_fingerprints']['before'] != result['calculation_result_fingerprints']['after']
    assert receipt['after']['review_view_requires_regeneration'] is True
    assert receipt['input_diff'][0]['before']['value'] == 3 and receipt['input_diff'][0]['after']['value'] == 5
    assert not receipt['quantity_approval_created'] and result['business_adoption_allowed'] is False
    assert req['state']['calculation_graph']['nodes'][1]['value'] == 3


def test_exception_has_own_decision_and_does_not_inherit_common():
    req = example_request()
    bind_feedback(req, ['M1', 'X1'])
    parameter_change(req)
    parameter_change(req, 'X1', (3,), 7)
    result = execute(req)
    assert result_rows(result)['Q3']['value'] == 56
    assert {r['decision_id'] for r in result['input_changes']} == {'M1', 'X1'}


def test_common_cannot_edit_member_held_by_exception():
    req = example_request()
    parameter_change(req, numbers=(3,))
    result = execute(req)
    assert not result['input_changes'] and '例外门' in result['held_changes'][0]['reason']


def test_same_value_different_basis_invalidates_numeric_confirmation(tmp_path):
    req = example_request()
    graph = req['state']['calculation_graph']
    old = recalculate(graph)
    approvals = write_review(tmp_path, graph['project_id'], old['nodes'])
    for node in graph['nodes']:
        node['approval'] = approvals[node['id']]
    old_source = copy.deepcopy(graph['sources'][0])
    req['decision_changes'] = [{'decision_id': 'M1', 'business_option_id': 'apply',
        'expected': state_expectation(req['state'], normalize_view(req['view'])),
        'mutations': [{'kind': 'parameter', 'collection': 'source', 'target_id': 'S1',
            'member_keys': [req['bindings'][0]['member_key']], 'before': old_source,
            'after': {**old_source, 'sha256': 'f' * 64}}]}]
    result = execute(req, tmp_path)
    rows = result_rows(result)
    assert rows['Q1']['value'] == 30 and rows['Q1']['review_state'] == 'stale'
    assert rows['Q2']['review_state'] == 'current' and rows['Q3']['review_state'] == 'current'
    changed = {r['node_id']: r for r in result['quantity_changes']}
    assert changed['Q1']['old_quantity_confirmation_invalidated']
    reused = {r['node_id']: r for r in result['reused_evidence']}
    assert reused['Q2']['review_evidence'] and reused['Q2']['fingerprint'] == rows['Q2']['fingerprint']


@pytest.mark.parametrize('field', ['state_version', 'source_sha256', 'result_fingerprint', 'input_fingerprint'])
def test_expired_change_does_not_enter_calculator(field, monkeypatch):
    req = example_request()
    proposal = parameter_change(req)
    proposal['expected'][field] = 'old'
    def forbidden(*args, **kw):
        raise AssertionError('stale input reached calculator')
    monkeypatch.setattr('qto_impact_recompute.recalculate', forbidden)
    result = execute(req)
    assert not result['input_changes'] and '过期' in result['held_changes'][0]['reason']


def test_feedback_new_member_or_changed_revision_does_not_inherit():
    req = example_request()
    req['view']['review_workflow']['decisions'][0]['revision'] = 'v2'
    parameter_change(req)
    result = execute(req)
    assert not result['input_changes'] and result['unmatched']
    req = example_request(False)
    req['view']['review_workflow']['decisions'][0]['scope']['excluded_member_keys'] = [req['bindings'][2]['member_key']]
    bind_feedback(req, ['M1'])
    req['view']['review_workflow']['decisions'][0]['scope']['excluded_member_keys'] = []
    parameter_change(req)
    assert not execute(req)['input_changes']


def test_new_physical_member_does_not_inherit_common_answer():
    req = example_request(False)
    new = fixture.circuit(4, 6.)
    req['view']['objects'].append(new)
    req['view']['review_workflow']['decisions'][0]['scope']['member_keys'].append(new['catalog_key'])
    req['view']['review_workflow']['decisions'][0]['refs'].append({'kind': 'object', 'id': new['catalog_key']})
    parameter_change(req, numbers=(1,))
    result = execute(req)
    assert not result['input_changes'] and result['unmatched']
    assert new['catalog_key'] in result['queue']['items'][0]['unbound_member_keys']


def test_same_title_new_identity_is_not_an_answer():
    req = example_request()
    parameter_change(req)
    req['decision_changes'][0]['decision_id'] = '同名新事项'
    assert not execute(req)['input_changes']


def test_true_conflict_held_by_existing_review_gate():
    req = example_request()
    parameter_change(req)
    record = req['feedback']['annotations']['审核事项|M1']
    other = copy.deepcopy(record)
    other['confirmed_result'] = '不采用5根方案'
    record['decision_conflict'] = {'candidates': [copy.deepcopy(record), other]}
    result = execute(req)
    assert not result['input_changes'] and result['feedback']['annotations']['审核事项|M1']['applicability_state'] == 'conflict'


def test_overlapping_decision_mutations_hold_both_without_time_selection():
    req = example_request()
    duplicate = copy.deepcopy(req['view']['review_workflow']['decisions'][0])
    duplicate['id'] = 'M2'
    req['view']['review_workflow']['decisions'].append(duplicate)
    bind_feedback(req, ['M1', 'M2'])
    parameter_change(req)
    parameter_change(req, 'M2', value=8)
    result = execute(req)
    assert not result['input_changes'] and {r['decision_id'] for r in result['held_changes']} == {'M1', 'M2'}


def test_unknown_impact_is_priority_and_units_are_never_added():
    req = example_request()
    ds = req['view']['review_workflow']['decisions']
    ds[0]['impacts'] = [{'unit': 'm', 'value': None, 'level': '未决量', 'scope': '多个回路'}]
    ds[1]['impacts'] = [{'unit': '个', 'value': 1000, 'level': '规则推算量', 'scope': '接头'}]
    req['impact_context'].append({'decision_id': 'X1', 'shared_source_id': 'D1'})
    result = execute(req, queue_only=True)
    queue = result['queue']
    assert queue['items'][0]['decision_id'] == 'M1' and queue['items'][0]['unknown_quantity_impact'] is True
    assert queue['items'][0]['unknown_impact_units'] == ['m'] and queue['items'][0]['quantity_rank_within_unit']['m'] is None
    assert queue['mixed_unit_total'] is None and not queue['unknown_is_zero']
    assert {r['decision_id'] for r in queue['same_source_groups'][0]['members']} == {'M1', 'X1'}
    assert queue['same_source_groups'][0]['members'][1]['exception_of'] == 'M1'
    text = queue_markdown(result)
    assert '未知（不得按0）' in text and '首轮保留3根方案并列未决' in text and '1000 个' in text


def test_unknown_numeric_value_remains_unknown_and_unaffected_is_reusable():
    req = example_request()
    parameter_change(req, numbers=(1,), value=None)
    result = execute(req)
    assert result_rows(result)['Q1']['value'] is None
    assert result_rows(result)['Q1']['quantity_level'] == '未决量'
    assert {r['node_id'] for r in result['reused_evidence']} >= {'Q2', 'Q3'}


def test_graph_relationship_change_cannot_claim_semantic_reidentification():
    req = example_request()
    proposal = parameter_change(req, numbers=(1,))
    proposal['mutations'][0]['after']['variables'] = {'x': 'L2'}
    result = execute(req)
    assert not result['input_changes'] and '不得冒充' in result['held_changes'][0]['reason']


def test_shared_basis_cannot_leak_into_excluded_member():
    req = example_request()
    graph = req['state']['calculation_graph']
    before = copy.deepcopy(graph['rules'][0])
    req['decision_changes'] = [{'decision_id': 'M1', 'business_option_id': 'apply',
        'expected': state_expectation(req['state'], normalize_view(req['view'])),
        'mutations': [{'kind': 'parameter', 'collection': 'rule', 'target_id': 'R',
            'member_keys': [r['member_key'] for r in req['bindings'][:2]], 'before': before,
            'after': {**before, 'sha256': 'c' * 64}}]}]
    result = execute(req)
    assert not result['input_changes'] and '传播越出' in result['held_changes'][0]['reason']


def test_cli_persists_input_revision_receipt_and_readable_queue(tmp_path):
    req = example_request()
    parameter_change(req)
    source, output, readable = tmp_path / '匿名请求.json', tmp_path / '输入变更与重算回执.json', tmp_path / '高影响队列.md'
    source.write_text(json.dumps(req, ensure_ascii=False), encoding='utf-8')
    cmd = [sys.executable, '-s', str(SCRIPT), str(source), '--output', str(output), '--queue-markdown', str(readable)]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result_rows(result)['Q1']['value'] == 50 and readable.read_text(encoding='utf-8').startswith('# 高影响')
    again = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert again.returncode == 2 and '已存在' in again.stdout


def test_real_reader_handoff_is_consumed_with_original_stable_identity(tmp_path):
    req = example_request()
    parameter_change(req)
    view_path, feedback_path, handoff_path = [tmp_path / name for name in ['视图.json', '已保存答复.json', 'reader交接.json']]
    view_path.write_text(json.dumps(req['view'], ensure_ascii=False), encoding='utf-8')
    feedback_path.write_text(json.dumps(req['feedback'], ensure_ascii=False), encoding='utf-8')
    process = subprocess.run([sys.executable, '-s', str(SCRIPT.parent / 'read_qto_review_feedback.py'),
        '--view', str(view_path), '--feedback', str(feedback_path), '--output', str(handoff_path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert process.returncode == 0, process.stdout + process.stderr
    req['handoff'] = json.loads(handoff_path.read_text(encoding='utf-8'))
    req.pop('feedback')
    result = execute(req, tmp_path)
    assert result['reader_handoff_consumed'] and result_rows(result)['Q1']['value'] == 50
    req['handoff']['decision_actions'][0]['scope']['member_keys'].append('不存在的同名成员')
    with pytest.raises(ValueError, match='reader动作'):
        execute(req, tmp_path)


def test_unavailable_semantic_branch_is_not_reported_as_success_and_parameters_continue(monkeypatch):
    req = example_request()
    parameter_change(req)
    bind_feedback(req, ['M1', 'X1'])
    network = {'id': 'scope-X', 'manifest_path': 'unavailable.json', 'request': {'schema': NETWORK_REQUEST,
        'scope_id': 'scope-X', 'project_id': req['view']['project_id'],
        'source_sha256': req['view']['sources'][0]['sha256'], 'connections': [{'id': 'K1', 'circuit_id': None}]}}
    req['state']['networks'] = [network]
    req['bindings'][2]['network_ids'] = ['scope-X']
    req['bindings'][2]['network_targets'] = [{'network_id': 'scope-X', 'collection': 'connections', 'target_id': 'K1'}]
    expected = state_expectation(req['state'], normalize_view(req['view']))
    req['decision_changes'][0]['expected'] = expected
    req['decision_changes'].append({'decision_id': 'X1', 'business_option_id': 'apply', 'expected': expected,
        'mutations': [{'kind': 'relationship', 'network_id': 'scope-X', 'collection': 'connections',
            'target_id': 'K1', 'member_keys': [req['bindings'][2]['member_key']],
            'before': network['request']['connections'][0], 'after': {'id': 'K1', 'circuit_id': 'C3'}}]})
    def unavailable(*args, **kw):
        raise ImportError('真实T35适配器不可用')
    monkeypatch.setattr('qto_impact_recompute.electrical_partition', unavailable)
    result = execute(req)
    assert [r['decision_id'] for r in result['input_changes']] == ['M1']
    assert result_rows(result)['Q1']['value'] == 50
    assert result['held_changes'][0]['recalculation_succeeded'] is False
    assert not result['input_changes'][0]['semantic_reidentification_executed']


def source_network_request(folder):
    """Independent oracle: 12 m trunk and 5 m branch at x=6; union=17 m.

    Initial C1 uses 3 conductors on union: 51 m. Move the 11 m branch route
    to C2 with 2 conductors: C1=36 m, C2=22 m, total=58 m. The 6 m shared
    geometry is physical once; conductors belong to each proven circuit.
    """
    import ezdxf
    folder.mkdir(parents=True, exist_ok=True)
    drawing = ezdxf.new('R2013')
    drawing.units = 6
    drawing.layers.add('WIRE_ROUTE')
    drawing.layers.add('BASIS')
    model = drawing.modelspace()
    device = drawing.blocks.new('PORT_DEVICE')
    port = device.add_point((0, 0, 0)).dxf.handle
    basis = 'CAD-' + model.add_text('匿名系统图: 支路可由C1拆分给C2; C1三根,C2两根',
        dxfattribs={'insert': (0, -2), 'height': .2, 'layer': 'BASIS'}).dxf.handle
    handles = {key: model.add_blockref('PORT_DEVICE', point).dxf.handle
               for key, point in [('S', (0, 0)), ('A', (12, 0)), ('B', (6, 5))]}
    model.add_line((0, 0), (12, 0), dxfattribs={'layer': 'WIRE_ROUTE'})
    model.add_line((6, 0), (6, 5), dxfattribs={'layer': 'WIRE_ROUTE'})
    path = folder / '匿名十二米主干与五米支路.dxf'
    drawing.saveas(path)
    command = [sys.executable, '-s', str(ROOT / 'skills/_qto-shared-evidence/scripts/extract_cad_neutral.py'),
               '--source-dxf', str(path), '--output-dir', str(folder / '中性原图')]
    process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert process.returncode == 0, process.stdout + process.stderr
    manifest = folder / '中性原图/CAD中性取证清单.json'
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    content = json.loads(manifest.read_text(encoding='utf-8'))
    objects = []
    for key in ('S', 'A', 'B'):
        sid = 'CAD-' + handles[key]
        objects.append({'object_key': key, 'category': 'source' if key == 'S' else 'lighting',
            'source_ids': [basis, sid], 'endpoint_binding': {'kind': 'connection_port',
                'manifest_sha256': manifest_hash, 'physical_source_id': sid, 'physical_id': sid,
                'port_source_id': sid + '/PORT_DEVICE/CAD-' + port, 'instance_path': sid,
                'locator': 'point', 'basis': '原实例内实际接线端口', 'basis_source_ids': [basis]}})
    request = {'schema': NETWORK_REQUEST, 'project_id': '匿名相关范围重算', 'scope_id': 'F1-LIGHT',
        'cad_manifest_sha256': manifest_hash, 'source_sha256': content['authoritative_source']['sha256'],
        'trace_request': {'drawing_id': 'SYN', 'layout': 'Model', 'route_layers': '^WIRE_ROUTE$',
            'unit': {'factor_to_output': 1, 'output_coordinate_unit': 'm', 'source_id': '匿名DXF米单位'},
            'expand_blocks': ['PORT_DEVICE'], 'snap_tolerance': .000001, 'gap_tolerance': 0,
            'endpoint_tolerance': .000001}, 'endpoint_objects': objects,
        'connections': [{'id': 'SA', 'from_object': 'S', 'to_object': 'A', 'relation': 'supply',
                         'source_ids': [basis], 'circuit_id': 'C1', 'circuit_source_ids': [basis]},
                        {'id': 'SB', 'from_object': 'S', 'to_object': 'B', 'relation': 'supply',
                         'source_ids': [basis], 'circuit_id': 'C1', 'circuit_source_ids': [basis]}],
        'circuit_specs': [{'circuit_id': 'C1', 'kind': 'wire', 'system': '照明', 'spec': '匿名导线',
                           'parallel_count': 3, 'extras': [], 'pending_components': [], 'source_ids': [basis]}]}
    return manifest, request, {'command': command, 'exit_code': process.returncode,
        'stdout': process.stdout, 'stderr': process.stderr}


def configure_semantic_review(req, network):
    """Bind displayed facts and the saved method answer to the source network."""
    view = req['view']
    view.pop('comparison', None)
    view['schema'] = 'qto-review-view/1.0'
    view['objects'] = view['objects'][:2]
    view['project_id'] = network['project_id']
    view['sources'] = [{'id': 'DWG', 'title': '匿名十二米主干与五米支路DXF', 'sha256': network['source_sha256']}]
    view['scenes'][0].update(coordinate_unit='m', bounds=[-2, -3, 14, 7])
    for obj, title, points, length in zip(view['objects'], ['支路A原图路径', '支路B原图路径'],
            [[[0, 0], [12, 0]], [[0, 0], [6, 0], [6, 5]]], [12, 11]):
        obj.update(title=title, points=points, detail='源端口到负荷端口的已画路径；共享原段不能重复计算物理通道',
                   source='匿名原图实际线路与设备连接口', quantities=[
                       {'name': '已画路径', 'value': length, 'unit': 'm', 'level': '规则推算量'}])
    for obj, connection, endpoint in zip(view['objects'], ['SA', 'SB'], ['A', 'B']):
        obj['attributes'] = {'连接关系': connection, '当前回路': 'C1', '源端口': 'S', '负荷端口': endpoint}
    view['title'] = '匿名回路归属拆合方法复核'
    decision = view['review_workflow']['decisions'][0]
    members = [o['catalog_key'] for o in view['objects']]
    decision['scope']['member_keys'] = members
    decision['refs'] = [{'kind': 'object', 'id': k} for k in members]
    decision['recommendation']['scope_member_keys'] = members
    decision.update(title='支路B回路归属', question='是否按有据的C2归属重建网络？')
    decision['business_options'] = [{'id': 'apply', 'title': 'B归C2'}, {'id': 'merge', 'title': 'B与A同归C1'}]
    decision['scope']['basis'] = '仅匿名12米主干及5米支路，不含竖向和端头'
    decision['facts'] = ['原图主干12米，x=6米处分支5米；端口与线路均有原图身份', '系统图给出C1三根、C2两根规格']
    decision['gaps'] = ['等待B支路归属方法答复；不将方法答复当数量核准']
    decision['impacts'] = [{'unit': 'm', 'value': 7, 'level': '规则推算量', 'scope': 'B从同归C1转为独立C2后的导线合计变化'}]
    decision['evidence'] = [{'id': 'E-NET', 'source': '匿名原图文字与实际端口/线路',
        'detail': 'CAD来源号:' + '、'.join(network['connections'][1]['source_ids']) + '；C1三根/C2两根', 'state': 'verified'}]
    decision['recommendation'].update(text='B支路归C2两根，A仍归C1三根', reason='按已提供的系统图和实际线路进行归属重建',
                                      evidence_ids=['E-NET'])
    req['impact_context'] = [{'decision_id': 'M1',
        'internal_working_basis': '首轮A、B共同按C1三根；调整归属后重新识别网络，不含竖向和端头',
        'first_pass_state': '已提交原图水平线、支路和实际端口；待归属裁决后重算', 'shared_source_id': '匿名DXF系统图'}]
    return view


def semantic_case(folder):
    manifest, network, extraction = source_network_request(folder)
    req = example_request(False)
    view = configure_semantic_review(req, network)
    req['state'] = {'version': 'network-v1', 'networks': [{'id': 'N1', 'manifest_path': str(manifest), 'request': network}]}
    req['bindings'] = []
    for o, connection, endpoint in zip(view['objects'], ['SA', 'SB'], ['A', 'B']):
        targets = [('endpoint_objects', 'S'), ('endpoint_objects', endpoint),
                   ('connections', connection), ('circuit_specs', 'C1')]
        if connection == 'SB':
            targets.append(('circuit_specs', 'C2'))
        req['bindings'].append({'member_key': o['catalog_key'], 'network_ids': ['N1'],
            'system_ids': ['照明'], 'circuit_ids': ['C1'], 'network_targets': [
                {'network_id': 'N1', 'collection': c, 'target_id': ident} for c, ident in targets]})
    bind_feedback(req, ['M1'])
    req['feedback']['annotations']['审核事项|M1'].update(confirmed_result='B支路归C2两根，A仍归C1三根',
                                                        reason='按匿名系统图及原图线路归属调整', user_note='仅此次原图范围')
    before = copy.deepcopy(network['connections'][1])
    new_spec = {**network['circuit_specs'][0], 'circuit_id': 'C2', 'parallel_count': 2}
    req['decision_changes'] = [{'decision_id': 'M1', 'business_option_id': 'apply',
        'expected': state_expectation(req['state'], normalize_view(view)), 'mutations': [
            {'kind': 'relationship', 'network_id': 'N1', 'collection': 'connections', 'target_id': 'SB',
             'member_keys': [req['bindings'][1]['member_key']], 'before': before,
             'after': {**before, 'circuit_id': 'C2'}},
            {'kind': 'relationship', 'network_id': 'N1', 'collection': 'circuit_specs', 'target_id': 'C2',
             'member_keys': [req['bindings'][1]['member_key']], 'before': None, 'after': new_spec}]}]
    return req, extraction


def test_real_semantic_split_rebuilds_objects_and_existing_calculator(tmp_path):
    req, _ = semantic_case(tmp_path / '源图')
    result = execute(req, tmp_path)
    assert len(result['input_changes']) == 1, result['held_changes']
    before = result['before_network_runs']['N1']
    after = result['network_runs']['N1']
    assert sum(r['length_m'] for r in before['calculation']['items']) == pytest.approx(51)
    quantities = {q['circuit_id']: q['complete_length_m'] for q in after['partition']['circuit_quantities']}
    assert quantities == {'C1': 36, 'C2': 22}
    assert sum(r['length_m'] for r in after['calculation']['items']) == pytest.approx(58)
    assert sum(s['length_m'] for s in after['partition']['physical_segments']) == pytest.approx(17)
    assert before['partition']['network_fingerprint'] != after['partition']['network_fingerprint']
    assert result['input_changes'][0]['semantic_reidentification_executed'] is True
    diff = result['quantity_changes'][0]
    assert diff['old_result_confirmation_may_not_be_inherited'] and diff['new_item_ids']
    assert after['calculator_executed'] and after['semantic_reidentification_executed']


def test_real_semantic_merge_retires_old_objects_instead_of_inheriting(tmp_path):
    req, _ = semantic_case(tmp_path / '源图')
    first = execute(req, tmp_path)
    assert first['input_changes'], first['held_changes']
    req['state'] = copy.deepcopy(first['state'])
    req['view']['revision'] = 'v2拆分后'
    req['view']['review_workflow']['decisions'][0]['revision'] = 'v2'
    bind_feedback(req, ['M1'])
    req['feedback']['annotations']['审核事项|M1'].update(business_option_id='merge', confirmed_result='支路B与A共同归C1')
    network = req['state']['networks'][0]['request']
    before_connection = copy.deepcopy(network['connections'][1])
    old_spec = copy.deepcopy(network['circuit_specs'][1])
    req['decision_changes'] = [{'decision_id': 'M1', 'business_option_id': 'merge',
        'expected': state_expectation(req['state'], normalize_view(req['view'])), 'mutations': [
            {'kind': 'relationship', 'network_id': 'N1', 'collection': 'connections', 'target_id': 'SB',
             'member_keys': [req['bindings'][1]['member_key']], 'before': before_connection,
             'after': {**before_connection, 'circuit_id': 'C1'}},
            {'kind': 'relationship', 'network_id': 'N1', 'collection': 'circuit_specs', 'target_id': 'C2',
             'member_keys': [req['bindings'][1]['member_key']], 'before': old_spec, 'after': None}]}]
    result = execute(req, tmp_path)
    assert result['input_changes'], result['held_changes']
    assert sum(i['length_m'] for i in result['network_runs']['N1']['calculation']['items']) == pytest.approx(51)
    assert result['quantity_changes'][0]['retired_item_ids']
    assert all(not i['official_adoption'] for i in result['network_runs']['N1']['calculation']['items'])


def test_expired_semantic_input_never_enters_professional_recognizer(tmp_path, monkeypatch):
    req, _ = semantic_case(tmp_path / '源图')
    req['decision_changes'][0]['expected']['state_version'] = '旧网络版本'
    def forbidden(*args, **kw):
        raise AssertionError('stale input reached semantic recognizer')
    monkeypatch.setattr('qto_impact_recompute.electrical_partition', forbidden)
    result = execute(req, tmp_path)
    assert not result['input_changes'] and '过期' in result['held_changes'][0]['reason']


def test_network_target_in_same_scope_cannot_bypass_member_exception(tmp_path, monkeypatch):
    req, _ = semantic_case(tmp_path / '源图')
    main = req['view']['review_workflow']['decisions'][0]
    exception = copy.deepcopy(main)
    member = req['bindings'][1]['member_key']
    exception.update(id='X-NET', exception_of='M1')
    exception['scope']['member_keys'] = [member]
    exception['refs'] = [{'kind': 'object', 'id': member}]
    exception['recommendation']['scope_member_keys'] = [member]
    req['view']['review_workflow']['decisions'].append(exception)
    bind_feedback(req, ['M1'])
    req['decision_changes'][0]['expected'] = state_expectation(req['state'], normalize_view(req['view']))
    def forbidden(*args, **kw):
        raise AssertionError('member exception reached semantic recognizer')
    monkeypatch.setattr('qto_impact_recompute.electrical_partition', forbidden)
    result = execute(req, tmp_path)
    assert not result['input_changes'] and '例外门' in result['held_changes'][0]['reason']
