"""Anonymous CLI fixtures only; never records a real person, project or M2 approval."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
from qto_review_contract import normalize_view, empty_feedback
from qto_review_identity import fingerprint, snapshot
from qto_rule_candidates import export_candidate, review_candidate, sha, ref, graph_bindings, context_fingerprint
from recompute_qto_dependencies import recalculate

TIME = '2026-10-19T00:00:00+08:00'
KEY = '审核事项|METHOD'
REVIEWER = '匿名测试复核员甲'


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def cli(script, args, directory, expected=0):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-B', str(script), *map(str, args)]
    run = subprocess.run(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, encoding='utf-8')
    (directory / (script.stem + '-' + str(len(list(directory.glob('*.log')))) + '.log')).write_text(
        json.dumps(command, ensure_ascii=False) + '\n' + run.stdout + run.stderr +
        '\nexit=' + str(run.returncode), encoding='utf-8')
    print(script.name, 'exit=', run.returncode, run.stdout.strip())
    assert run.returncode == expected, run.stdout + run.stderr
    return run


def draft():
    return {'schema': 'qto-rule-candidate-draft/1.0', 'rule_family': '水平段处理', 'draft_version': '1',
            'proposed_rule': '仅在明确水平路径和单位依据均已核实的分支采用水平量，竖向另核',
            'applicability': {'professional': 'qto-electrical-routes', 'object_kind': 'line',
                              'object_type': '电气线路', 'quantity_layer': '规则推算量', 'unit': 'm',
                              'measurement_basis': '明确路径中心线水平投影',
                              'region': '按适用地区计量依据另核', 'contract_scope': '仅水平段；供装费用不在本规则内'},
            'trigger_conditions': ['路径与单位均有明确依据'],
            'boundaries': ['不推定竖向及附属量'], 'exceptions': ['路径缺失则暂挂'],
            'counterexamples': ['无路径时不能按图框比例生成当前长度'], 'pending_branches': ['地区合同差异待维护判断'],
            'suggested_reference': 'skills/qto-electrical-routes/references/水平段候选.md'}


def fixture(directory, project='匿名项目甲', *, producer=True, source_content=None, object_type='电气线路',
            quantity_confirmed=False, unknown=False):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / '匿名原源.txt'
    source.write_text(source_content or project + ' 合成水平路径，非真实图纸', encoding='utf-8')
    graph = {'schema': 'qto-calculation-graph/1.0', 'project_id': project,
             'sources': [{'id': 'S1', 'sha256': sha(source)}], 'rules': [],
             'nodes': [{'id': 'N1', 'source_ids': ['S1'], 'rule_ids': [],
                        'value': None if unknown else 10, 'unit': 'm', 'quantity_level': '规则推算量'}]}
    run = recalculate(graph)
    if quantity_confirmed:
        sys.path.insert(0, str(Path(__file__).parent))
        from review_evidence_helpers import write_review
        approvals = write_review(directory, project, run['nodes'], reviewer='匿名数量测试员', name='匿名数量夹具')
        graph['nodes'][0]['approval'] = approvals['N1']
        run = recalculate(graph, base_dir=directory)
    graph_path = write(directory / '计算输入.json', graph)
    run_path = directory / '重算结果.json'
    if producer:
        cli(SCRIPTS / 'recompute_qto_dependencies.py', ['--input', graph_path, '--output', run_path], directory)
    else:
        write(run_path, run)
    obj = {'id': 'O1', 'catalog_key': '电气|对象甲', 'professional': 'qto-electrical-routes',
           'kind': 'line', 'scene_id': 'P1', 'points': [[0, 0], [10, 0]],
           'title': '匿名水平路径', 'category': object_type, 'detail': '测试夹具', 'source': '匿名原源',
           'status': '规则推算', 'quantities': [{'name': '水平长度', 'value': run['nodes'][0]['value'],
                                                'unit': 'm', 'level': run['nodes'][0]['quantity_level']}],
           'calculation_bindings': [{'node_id': 'N1', 'calculation_fingerprint': run['nodes'][0]['fingerprint']}]}
    decision = {'id': 'METHOD', 'revision': '1', 'title': '水平段方法', 'question': '是否仅采用水平段并保留竖向待核？',
                'kind': 'business_choice', 'scope': {'member_keys': ['电气|对象甲'], 'excluded_member_keys': [],
                                                   'basis': '仅水平路径，不含竖向及附属量'},
                'refs': [{'kind': 'object', 'id': '电气|对象甲'}],
                'business_options': [{'id': 'horizontal', 'title': '水平段暂采用'}]}
    raw_view = {'schema': 'qto-review-view/1.0', 'project_id': project, 'review_id': '匿名方法复核',
                'title': '匿名测试复核', 'revision': 'R1', 'sources': [{'id': 'S1', 'title': '匿名源', 'sha256': sha(source)}],
                'scenes': [{'id': 'P1', 'title': '匿名平面', 'source_id': 'S1', 'coordinate_unit': 'm', 'bounds': [-1, -1, 11, 1]}],
                'objects': [obj], 'review_workflow': {'schema': 'qto-review-workflow/1.0', 'enabled': True,
                                                   'migrate_legacy': False, 'decisions': [decision]}}
    view_path = write(directory / '复核视图.json', raw_view)
    view = normalize_view(raw_view)
    d = view['review_workflow']['decisions'][0]
    method_context = context_fingerprint(run, graph_bindings(view, d['scope']['member_keys'], run), [])
    annotation = {'decision_snapshot': snapshot(d), 'action': '调整处理', 'business_option_id': 'horizontal',
                  'confirmed_result': '仅水平段，竖向另核', 'reason': '合成路径有明确来源',
                  'user_note': '测试夹具，非真实人工审批', 'exception_of': None, 'excluded_member_keys': [],
                  'history': [], 'updated_at': TIME, 'reviewer': REVIEWER, 'reviewer_type': 'human',
                  'reviewed_at': TIME, 'decision_type': 'method_confirmation',
                  'fixture_kind': 'anonymous_synthetic_not_real_human'}
    annotation['method_context_fingerprint'] = method_context
    feedback = empty_feedback(view)
    feedback['annotations'][KEY] = annotation
    feedback_path = write(directory / '原保存批注.json', feedback)
    evidence = {'schema': 'qto-rule-decision-evidence/1.0', 'project_id': project, 'records': [{
        'record_id': 'E1', 'reviewer': REVIEWER, 'reviewer_type': 'human', 'reviewed_at': TIME,
        'decision': 'confirm_method_decision', 'annotation_key': KEY, 'annotation_fingerprint': fingerprint(annotation),
        'decision_id': d['id'], 'decision_revision': d['revision'], 'source_fingerprint': view['source_sha256'],
        'method_context_fingerprint': method_context,
        'result_fingerprint': view['result_fingerprint'], 'decision_result_fingerprint': d['result_fingerprint'],
        'source_feedback': ref(feedback_path, annotation_key=KEY, saved_at=TIME)}]}
    evidence_path = write(directory / '匿名方法凭据.json', evidence)
    handoff_path = directory / '反馈交接.json'
    cli(SCRIPTS / 'read_qto_review_feedback.py',
        ['--view', view_path, '--feedback', feedback_path, '--output', handoff_path], directory)
    request = {'schema': 'qto-rule-candidate-request/1.0', 'view': ref(view_path), 'feedback': ref(feedback_path),
               'handoff': ref(handoff_path), 'calculation_input': ref(graph_path), 'calculation_run': ref(run_path),
               'source_files': [ref(source, id='S1')], 'annotation_key': KEY,
               'human_decision_evidence': ref(evidence_path, record_id='E1'), 'draft': draft()}
    request['draft']['applicability']['object_type'] = object_type
    if unknown:
        request['draft']['applicability']['quantity_layer'] = '未决量'
        request['draft']['proposed_rule'] = '缺少路径证据时保留未知量null，不以零代替'
    request_path = write(directory / '导出请求.json', request)
    return request_path


def candidate(request_path, maintenance):
    cli(SCRIPTS / 'qto_rule_candidates.py', ['export', '--request', request_path,
        '--maint-dir', maintenance, '--name', '匿名候选.json'], request_path.parent)
    return maintenance / '规则候选/匿名候选.json'


def review_request(directory, candidate_path, branch, cases=()):
    c = json.loads(candidate_path.read_text(encoding='utf-8'))
    review = {'schema': 'qto-rule-maintenance-review/1.0', 'candidate_sha256': sha(candidate_path),
              'reviewer': '匿名测试维护员乙', 'reviewer_type': 'human', 'reviewed_at': TIME,
              'disposition': branch, 'reason': '匿名测试分流；不是实际维护批准',
              'applicability_review': {'project_specific': '已逐项核对项目限定',
                  'region_contract_limits': '保留地区和合同差异待核，不扩大适用',
                  'exception_review': '无路径时拒收', 'covered_branch': '只覆盖明确水平路径分支',
                  'rule_fingerprint': c['rule_fingerprint']}, 'independent_cases': [ref(p) for p in cases]}
    review_path = write(directory / '匿名原保存维护意见.json', review)
    evidence = {'schema': 'qto-rule-maintenance-evidence/1.0', 'project_id': c['project_id'], 'records': [{
        'record_id': 'M1', 'reviewer': review['reviewer'], 'reviewer_type': 'human', 'reviewed_at': TIME,
        'decision': branch, 'candidate_sha256': sha(candidate_path), 'review_fingerprint': fingerprint(review),
        'source_review': ref(review_path, saved_at=TIME)}]}
    evidence_path = write(directory / '匿名维护凭据.json', evidence)
    return write(directory / '维护请求.json', {'schema': 'qto-rule-maintenance-request/1.0',
        'candidate': ref(candidate_path), 'review': ref(review_path),
        'human_review_evidence': ref(evidence_path, record_id='M1')})


@pytest.mark.parametrize('branch', ['reject', 'project_only', 'general_candidate'])
def test_actual_producers_export_review_all_three_branches(tmp_path, branch):
    request = fixture(tmp_path / '原项目')
    before = {p: sha(p) for p in request.parent.iterdir() if p.is_file()}
    cpath = candidate(request, tmp_path / '维护甲')
    other = candidate(fixture(tmp_path / '独立项目', '匿名项目乙'), tmp_path / '维护乙') if branch == 'general_candidate' else None
    rpath = review_request(tmp_path / '维护输入', cpath, branch, [other] if other else [])
    cli(ROOT / 'maintenance/review_rule_candidates.py', ['review', '--request', rpath,
        '--maint-dir', tmp_path / '维护输出', '--name', branch + '.json'], rpath.parent)
    receipt = json.loads((tmp_path / '维护输出/规则候选' / (branch + '.json')).read_text(encoding='utf-8'))
    assert receipt['disposition'] == branch
    assert ('maintenance_proposal' in receipt) == (branch == 'general_candidate')
    assert receipt['business_adoption_allowed'] is receipt['runtime_changed'] is receipt['maturity_changed'] is False
    assert all(sha(p) == h for p, h in before.items())
    c = json.loads(cpath.read_text(encoding='utf-8'))
    assert c['origin']['original_question'] and c['origin']['original_answer']['fixture_kind'].startswith('anonymous')
    assert 'skills/_qto-shared-evidence/scripts/qto_rule_candidates.py' in c['runtime_dependencies']
    assert 'maintenance/review_rule_candidates.py' in receipt['maintenance_dependencies']
    assert 'maintenance/review_rule_candidates.py' not in c['runtime_dependencies']
    assert 'skills/_qto-shared-evidence/scripts/qto_human_review_evidence.py' in c['runtime_dependencies']


def mutate_reference(request_path, field, mutate):
    request = json.loads(request_path.read_text(encoding='utf-8'))
    path = Path(request[field]['path'])
    doc = json.loads(path.read_text(encoding='utf-8'))
    mutate(doc)
    write(path, doc)
    request[field]['sha256'] = sha(path)
    write(request_path, request)


@pytest.mark.parametrize('kind', ['no_evidence', 'model', 'no_saved_confirmation', 'old_source',
    'old_result', 'changed_input', 'old_binding', 'conflict', 'migration', 'draft_only', 'no_explicit_answer',
    'forged_handoff', 'wrong_unit', 'bad_reference', 'source_bytes', 'wrong_evidence_schema',
    'duplicate_evidence', 'evidence_wrong_feedback', 'evidence_wrong_revision', 'expired_recommendation'])
def test_export_rejects_invalid_or_stale_sources_with_cli_receipt(tmp_path, kind):
    request_path = fixture(tmp_path / '输入')
    request = json.loads(request_path.read_text(encoding='utf-8'))
    if kind == 'no_evidence':
        request.pop('human_decision_evidence')
    elif kind == 'model':
        mutate_reference(request_path, 'human_decision_evidence', lambda d: d['records'][0].update(reviewer='GPT-6'))
    elif kind == 'no_saved_confirmation':
        mutate_reference(request_path, 'feedback', lambda d: d['annotations'][KEY].pop('decision_type'))
    elif kind == 'old_source':
        mutate_reference(request_path, 'feedback', lambda d: d.update(source_sha256='f' * 64))
    elif kind == 'old_result':
        mutate_reference(request_path, 'feedback', lambda d: d['annotations'][KEY]['decision_snapshot'].update(result_fingerprint='a' * 64))
    elif kind == 'changed_input':
        mutate_reference(request_path, 'calculation_input', lambda d: d['nodes'][0].update(value=11))
    elif kind == 'old_binding':
        mutate_reference(request_path, 'view', lambda d: d['objects'][0]['calculation_bindings'][0].update(calculation_fingerprint='e' * 64))
    elif kind == 'conflict':
        def conflict(d):
            a = d['annotations'][KEY]
            b = copy.deepcopy(a); b['confirmed_result'] = '相反结论'
            a['decision_conflict'] = {'candidates': [copy.deepcopy(a), b]}
        mutate_reference(request_path, 'feedback', conflict)
    elif kind == 'migration':
        mutate_reference(request_path, 'feedback', lambda d: d['annotations'][KEY].update(migration_state='pending'))
    elif kind == 'draft_only':
        mutate_reference(request_path, 'feedback', lambda d: d['drafts'].update({KEY: d['annotations'].pop(KEY)}))
    elif kind == 'no_explicit_answer':
        mutate_reference(request_path, 'feedback', lambda d: d['annotations'][KEY].update(action='暂不处理'))
    elif kind == 'forged_handoff':
        mutate_reference(request_path, 'handoff', lambda d: d.update(decision_actions=[]))
    elif kind == 'wrong_unit':
        request['draft']['applicability']['unit'] = 'm2'
    elif kind == 'bad_reference':
        request['draft']['suggested_reference'] = '../../runtime.md'
    elif kind == 'source_bytes':
        Path(request['source_files'][0]['path']).write_text('变更后原源', encoding='utf-8')
    elif kind == 'wrong_evidence_schema':
        mutate_reference(request_path, 'human_decision_evidence', lambda d: d.update(schema='qto-human-review-evidence/1.0'))
    elif kind == 'duplicate_evidence':
        mutate_reference(request_path, 'human_decision_evidence', lambda d: d['records'].append(copy.deepcopy(d['records'][0])))
    elif kind == 'evidence_wrong_feedback':
        mutate_reference(request_path, 'human_decision_evidence', lambda d: d['records'][0]['source_feedback'].update(sha256='b' * 64))
    elif kind == 'evidence_wrong_revision':
        mutate_reference(request_path, 'human_decision_evidence', lambda d: d['records'][0].update(decision_revision='0'))
    elif kind == 'expired_recommendation':
        def expired(d):
            a = d['annotations'][KEY]
            a.update(action='采用建议', recommendation_receipt={'snapshot': {}, 'user_choice': '采用建议', 'reason_origin': '助手建议引用'})
        mutate_reference(request_path, 'feedback', expired)
    if kind in ('no_evidence', 'wrong_unit', 'bad_reference'):
        write(request_path, request)
    cli(SCRIPTS / 'qto_rule_candidates.py', ['export', '--request', request_path,
        '--maint-dir', tmp_path / '维护拒收', '--name', '拒收.json'], request_path.parent, expected=2)
    rejected = json.loads((tmp_path / '维护拒收/规则候选/拒收.json').read_text(encoding='utf-8'))
    assert rejected['status'] == 'INPUT_REQUIRED'
    assert rejected['runtime_changed'] is False


@pytest.mark.parametrize('kind', ['no_case', 'same_project', 'same_source', 'rule_version', 'object_type',
                                  'no_review_evidence', 'model_reviewer', 'review_source_changed',
                                  'candidate_changed', 'dependency_changed'])
def test_maintenance_rejects_single_project_unrelated_or_invalid_case(tmp_path, kind, monkeypatch):
    c = candidate(fixture(tmp_path / '原项目'), tmp_path / '维护甲')
    cases = []
    if kind not in ('no_case', 'no_review_evidence', 'model_reviewer', 'review_source_changed', 'candidate_changed', 'dependency_changed'):
        source_content = (tmp_path / '原项目/匿名原源.txt').read_text(encoding='utf-8') if kind == 'same_source' else None
        other_request = fixture(tmp_path / '独立项目', '匿名项目甲' if kind == 'same_project' else '匿名项目乙',
                                source_content=source_content,
                                object_type='不同构件类型' if kind == 'object_type' else '电气线路')
        doc = json.loads(other_request.read_text(encoding='utf-8'))
        if kind == 'rule_version':
            doc['draft']['draft_version'] = '2'
            write(other_request, doc)
        cases = [candidate(other_request, tmp_path / '维护乙')]
    r = review_request(tmp_path / '审核输入', c, 'general_candidate', cases)
    if kind == 'no_review_evidence':
        doc = json.loads(r.read_text(encoding='utf-8')); doc.pop('human_review_evidence'); write(r, doc)
    elif kind == 'model_reviewer':
        mutate_reference(r, 'human_review_evidence', lambda d: d['records'][0].update(reviewer='Codex'))
    elif kind == 'review_source_changed':
        mutate_reference(r, 'review', lambda d: d.update(reason='事后改写'))
    elif kind == 'candidate_changed':
        Path(json.loads(c.read_text(encoding='utf-8'))['request']['path']).write_text('{}', encoding='utf-8')
    elif kind == 'dependency_changed':
        import qto_rule_candidates as module
        original = module.dependency_bytes
        monkeypatch.setattr(module, 'dependency_bytes', lambda: {**original(), 'changed.py': 'c' * 64})
    with pytest.raises((ValueError, KeyError)):
        review_candidate(r)


def test_runtime_output_and_overwrite_rejected(tmp_path):
    request = fixture(tmp_path / '输入')
    cli(SCRIPTS / 'qto_rule_candidates.py', ['export', '--request', request,
        '--maint-dir', ROOT / 'skills', '--name', '禁止.json'], request.parent, expected=2)
    path = candidate(request, tmp_path / '维护')
    before = sha(path)
    cli(SCRIPTS / 'qto_rule_candidates.py', ['export', '--request', request,
        '--maint-dir', tmp_path / '维护', '--name', '匿名候选.json'], request.parent, expected=2)
    assert sha(path) == before


def test_quantity_evidence_reused_without_method_becoming_quantity_approval(tmp_path):
    request = fixture(tmp_path / '输入', quantity_confirmed=True)
    c = export_candidate(request)
    assert c['origin']['bindings'][0]['review_state'] == 'current'
    assert c['origin']['bindings'][0]['review_evidence']['record_id']
    assert c['business_adoption_allowed'] is False
    # Alter the separately saved quantity source: the real calculator must downgrade it,
    # and the existing producer run is no longer a current replay.
    quantity_source = request.parent / '匿名数量夹具_feedback.json'
    quantity_source.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='计算输入'):
        export_candidate(request)


def test_installed_export_runs_without_repository_maintenance_files(tmp_path):
    import shutil
    request = fixture(tmp_path / '输入')
    installed = tmp_path / '隔离运行包'
    shutil.copytree(SCRIPTS, installed / 'skills/_qto-shared-evidence/scripts')
    shutil.copytree(ROOT / 'skills/qto-electrical-routes/scripts', installed / 'skills/qto-electrical-routes/scripts')
    assert not (installed / 'maintenance').exists()
    script = installed / 'skills/_qto-shared-evidence/scripts/qto_rule_candidates.py'
    cli(script, ['export', '--request', request, '--maint-dir', tmp_path / '独立维护',
                 '--name', '已安装入口验证.json'], request.parent)
    cpath = tmp_path / '独立维护/规则候选/已安装入口验证.json'
    c = json.loads(cpath.read_text(encoding='utf-8'))
    assert c['schema'] == 'qto-rule-candidate/1.0'
    # Same bytes and relative dependency paths permit the maintenance repository consumer.
    r = review_request(tmp_path / '维护输入', cpath, 'project_only')
    assert review_candidate(r)['disposition'] == 'project_only'


def test_explicit_method_candidate_can_preserve_unknown_quantity(tmp_path):
    request = fixture(tmp_path / '输入', unknown=True)
    c = export_candidate(request)
    binding = c['origin']['bindings'][0]
    assert binding['value'] is None and binding['quantity_level'] == '未决量'
    assert binding['calculation_status'] == 'BLOCKED'
    assert c['business_adoption_allowed'] is False


def test_calculation_source_must_be_actual_bound_physical_evidence(tmp_path):
    request = fixture(tmp_path / '输入')
    mutate_reference(request, 'calculation_input', lambda d: d['sources'][0].update(sha256='a' * 64))
    with pytest.raises(ValueError, match='计算来源物理文件'):
        export_candidate(request)


def test_rule_physical_provenance_is_consumed_and_changed_rule_rejected(tmp_path):
    request = fixture(tmp_path / '输入')
    doc = json.loads(request.read_text(encoding='utf-8'))
    graph_path = Path(doc['calculation_input']['path'])
    graph = json.loads(graph_path.read_text(encoding='utf-8'))
    rule_path = request.parent / '匿名计算规则.txt'
    rule_path.write_text('匿名规则依据：P1水平段按L计算', encoding='utf-8')
    graph['rules'] = [{'id': 'R1', 'sha256': sha(rule_path)}]
    graph['nodes'][0]['id'] = 'P1'
    graph['nodes'].append({'id': 'N1', 'expression': 'L', 'variables': {'L': 'P1'},
                           'source_ids': [], 'rule_ids': ['R1'], 'unit': 'm', 'quantity_level': '规则推算量'})
    write(graph_path, graph)
    new_run = request.parent / '新重算结果.json'
    cli(SCRIPTS / 'recompute_qto_dependencies.py', ['--input', graph_path, '--output', new_run], request.parent)
    run = json.loads(new_run.read_text(encoding='utf-8'))
    row = next(n for n in run['nodes'] if n['id'] == 'N1')
    # Regenerate the current view and saved method evidence after the calculation change.
    view_path = Path(doc['view']['path'])
    view_raw = json.loads(view_path.read_text(encoding='utf-8'))
    view_raw['objects'][0]['calculation_bindings'][0]['calculation_fingerprint'] = row['fingerprint']
    write(view_path, view_raw)
    view = normalize_view(view_raw)
    feedback_path = Path(doc['feedback']['path'])
    feedback = json.loads(feedback_path.read_text(encoding='utf-8'))
    feedback['annotations'][KEY]['decision_snapshot'] = snapshot(view['review_workflow']['decisions'][0])
    feedback['result_fingerprint'] = view['result_fingerprint']
    write(feedback_path, feedback)
    evidence_path = Path(doc['human_decision_evidence']['path'])
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    record = evidence['records'][0]
    record.update(annotation_fingerprint=fingerprint(feedback['annotations'][KEY]),
                  result_fingerprint=view['result_fingerprint'],
                  decision_result_fingerprint=view['review_workflow']['decisions'][0]['result_fingerprint'],
                  source_feedback=ref(feedback_path, annotation_key=KEY, saved_at=TIME))
    method_context = context_fingerprint(run, graph_bindings(view, ['电气|对象甲'], run), [])
    feedback['annotations'][KEY]['method_context_fingerprint'] = method_context
    write(feedback_path, feedback)
    record.update(method_context_fingerprint=method_context,
                  annotation_fingerprint=fingerprint(feedback['annotations'][KEY]),
                  source_feedback=ref(feedback_path, annotation_key=KEY, saved_at=TIME))
    write(evidence_path, evidence)
    new_handoff = request.parent / '新反馈交接.json'
    cli(SCRIPTS / 'read_qto_review_feedback.py', ['--view', view_path,
        '--feedback', feedback_path, '--output', new_handoff], request.parent)
    doc.update(view=ref(view_path), feedback=ref(feedback_path), handoff=ref(new_handoff),
               calculation_input=ref(graph_path), calculation_run=ref(new_run),
               calculation_rules=[ref(rule_path, id='R1')],
               human_decision_evidence=ref(evidence_path, record_id='E1'))
    write(request, doc)
    c = export_candidate(request)
    assert c['calculation_rule_files'][0]['sha256'] == sha(rule_path)
    rule_path.write_text('事后修改规则依据', encoding='utf-8')
    with pytest.raises(ValueError, match='字节变化'):
        export_candidate(request)


def test_unwritable_output_reports_original_error_without_recursive_write(tmp_path, monkeypatch, capsys):
    from qto_rule_candidates import main
    request = fixture(tmp_path / '输入')
    write_text = Path.write_text
    def fail_target(path, *args, **kwargs):
        if path.name == '不可写.json':
            raise PermissionError('synthetic unwritable output')
        return write_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_target)
    result = main(['export', '--request', str(request), '--maint-dir', str(tmp_path / '维护'),
                   '--name', '不可写.json'])
    assert result == 2
    assert 'rejection_receipt_write_error' in capsys.readouterr().out


def refresh_synthetic_method_context(request_path):
    """Only anonymous tests fabricate records; runtime provides no approval writer."""
    doc = json.loads(request_path.read_text(encoding='utf-8'))
    view_path = Path(doc['view']['path'])
    view = normalize_view(json.loads(view_path.read_text(encoding='utf-8')))
    d = view['review_workflow']['decisions'][0]
    feedback_path = Path(doc['feedback']['path'])
    feedback = empty_feedback(view)
    annotation = {'decision_snapshot': snapshot(d), 'action': '调整处理', 'business_option_id': 'horizontal',
                  'confirmed_result': '仅采用本次已证水平段方法，其他分量保留待核', 'reason': '匿名原源测试',
                  'user_note': '匿名测试夹具，不是真实M2或人工批准', 'exception_of': None, 'excluded_member_keys': [],
                  'history': [], 'updated_at': TIME, 'reviewer': REVIEWER, 'reviewer_type': 'human',
                  'reviewed_at': TIME, 'decision_type': 'method_confirmation',
                  'fixture_kind': 'anonymous_synthetic_not_real_human'}
    feedback['annotations'][KEY] = annotation
    write(feedback_path, feedback)
    doc.update(view=ref(view_path), feedback=ref(feedback_path))
    handoff_path = request_path.parent / ('重建反馈交接' + str(len(list(request_path.parent.glob('重建反馈交接*.json')))) + '.json')
    cli(SCRIPTS / 'read_qto_review_feedback.py', ['--view', view_path, '--feedback', feedback_path,
        '--output', handoff_path], request_path.parent)
    doc['handoff'] = ref(handoff_path)
    write(request_path, doc)
    context = export_candidate(request_path, preview=True)
    annotation['method_context_fingerprint'] = context['method_context_fingerprint']
    write(feedback_path, feedback)
    evidence_path = Path(doc['human_decision_evidence']['path'])
    write(evidence_path, {'schema': 'qto-rule-decision-evidence/1.0', 'project_id': view['project_id'], 'records': [{
        'record_id': 'E1', 'reviewer': REVIEWER, 'reviewer_type': 'human', 'reviewed_at': TIME,
        'decision': 'confirm_method_decision', 'annotation_key': KEY, 'annotation_fingerprint': fingerprint(annotation),
        'decision_id': d['id'], 'decision_revision': d['revision'],
        'method_context_fingerprint': context['method_context_fingerprint'],
        'source_fingerprint': view['source_sha256'], 'result_fingerprint': view['result_fingerprint'],
        'decision_result_fingerprint': d['result_fingerprint'],
        'source_feedback': ref(feedback_path, annotation_key=KEY, saved_at=TIME)}]})
    doc.update(feedback=ref(feedback_path), human_decision_evidence=ref(evidence_path, record_id='E1'))
    final_handoff = request_path.parent / ('重建反馈交接' + str(len(list(request_path.parent.glob('重建反馈交接*.json')))) + '.json')
    cli(SCRIPTS / 'read_qto_review_feedback.py', ['--view', view_path, '--feedback', feedback_path,
        '--output', final_handoff], request_path.parent)
    doc['handoff'] = ref(final_handoff)
    write(request_path, doc)
    return context


def network_fixture(directory, *, mixed=False, unknown=False):
    """Real anonymous DXF -> neutral source -> T35 partition -> reference calculator."""
    sys.path.insert(0, str(Path(__file__).parent))
    from test_impact_recompute_20261019 import source_network_request
    request_path = fixture(directory / '候选输入', '匿名相关范围重算')
    doc = json.loads(request_path.read_text(encoding='utf-8'))
    manifest_path, network_request, extraction = source_network_request(directory / '网络原源')
    if unknown:
        network_request['circuit_specs'][0].pop('parallel_count')
    write(directory / '网络原源/实际取证CLI回执.json', extraction)
    network_request_path = write(directory / '网络原源/网络请求.json', network_request)
    electrical = ROOT / 'skills/qto-electrical-routes/scripts'
    producer_output = directory / '网络原源/实际网络输出'
    cli(electrical / 'partition_electrical_route_network.py',
        ['--cad-manifest', manifest_path, '--request', network_request_path, '--output-dir', producer_output],
        request_path.parent, expected=3 if unknown else 0)
    partition_path = producer_output / '混连网络拆分结果.json'
    ledger_path = producer_output / '路径台账.json'
    calculation_path = directory / '网络原源/实际路径复算.json'
    cli(electrical / 'calculate_electrical_paths.py', [ledger_path, '--output', calculation_path], request_path.parent)
    partition = json.loads(partition_path.read_text(encoding='utf-8'))
    atom = (partition['physical_segments'][0] if unknown else
            next(a for a in partition['physical_segments'] if a['conductor_quantity_m'] is not None))
    view_path = Path(doc['view']['path'])
    view_raw = json.loads(view_path.read_text(encoding='utf-8'))
    network_object = {'id': atom['id'], 'catalog_key': '电气混连原段|' + atom['id'],
        'professional': 'qto-electrical-routes', 'kind': 'line', 'scene_id': 'P1',
        'points': [p[:2] for p in atom['source_points']], 'title': '匿名网络原段',
        'category': '电气线路', 'detail': '实际T35原段显式映射', 'source': '匿名DXF实际线路及端口',
        'status': atom['quantity_status'], 'quantities': [],
        'attributes': {'conductor_quantity_m': atom['conductor_quantity_m']}}
    source_doc = json.loads(manifest_path.read_text(encoding='utf-8'))['authoritative_source']
    source_path = Path(source_doc['path'])
    if not source_path.is_absolute():
        source_path = manifest_path.parent / source_path
    source = ref(source_path, id='DWG')
    view_raw['sources'] = ([*view_raw['sources']] if mixed else []) + [
        {'id': 'DWG', 'title': '匿名T35原源', 'sha256': source['sha256']}]
    view_raw['objects'] = ([*view_raw['objects']] if mixed else []) + [network_object]
    view_raw['scenes'][0].update(source_id='DWG', bounds=[-2, -3, 14, 7])
    d = view_raw['review_workflow']['decisions'][0]
    keys = [o['catalog_key'] for o in view_raw['objects']]
    d['scope']['member_keys'] = keys
    d['refs'] = [{'kind': 'object', 'id': k} for k in keys]
    write(view_path, view_raw)
    view = normalize_view(view_raw)
    obj = next(o for o in view['objects'] if o['catalog_key'] == network_object['catalog_key'])
    spec = {'schema': 'qto-rule-electrical-network-evidence/1.0', 'network_id': '显式网络甲',
        'decision_id': 'METHOD', 'decision_revision': '1', 'scope_id': network_request['scope_id'],
        'network_fingerprint': partition['network_fingerprint'], 'manifest': ref(manifest_path),
        'request': ref(network_request_path), 'partition': ref(partition_path), 'ledger': ref(ledger_path),
        'calculation_run': ref(calculation_path), 'bindings': [{
            'catalog_key': obj['catalog_key'], 'object_fingerprint': obj['result_fingerprint'],
            'target_kind': 'physical_segment', 'target_id': atom['id'], 'target_fingerprint': fingerprint(atom),
            'quantity_field': 'conductor_quantity_m', 'value_source': 'attribute', 'value_name': 'conductor_quantity_m'}]}
    doc['source_files'] = ([*doc['source_files']] if mixed else []) + [source]
    doc['network_evidence'] = [spec]
    if unknown:
        doc['draft']['applicability']['quantity_layer'] = '未决量'
    doc['view'] = ref(view_path)
    if not mixed:
        doc.pop('calculation_input')
        doc.pop('calculation_run')
    write(request_path, doc)
    refresh_synthetic_method_context(request_path)
    return request_path


@pytest.mark.parametrize('mixed', [False, True])
def test_actual_t35_partition_producers_export_and_maintenance_consume(tmp_path, mixed):
    request = network_fixture(tmp_path, mixed=mixed)
    cpath = candidate(request, tmp_path / '维护候选')
    c = json.loads(cpath.read_text(encoding='utf-8'))
    assert len(c['origin']['bindings']) == (2 if mixed else 1)
    assert c['network_evidence'][0]['adapter_schema'] == 'qto-rule-electrical-network-evidence/1.0'
    assert 'skills/qto-electrical-routes/scripts/partition_electrical_route_network.py' in c['runtime_dependencies']
    assert 'skills/qto-electrical-routes/scripts/calculate_electrical_paths.py' in c['runtime_dependencies']
    r = review_request(tmp_path / '审核输入', cpath, 'project_only')
    cli(ROOT / 'maintenance/review_rule_candidates.py', ['review', '--request', r, '--maint-dir',
        tmp_path / '维护回执', '--name', '网络仅项目回执.json'], r.parent)
    assert review_candidate(r)['business_adoption_allowed'] is False


@pytest.mark.parametrize('kind', ['request_scope', 'partition_bytes', 'calculation_bytes', 'target_identity',
                                  'target_content', 'object_content', 'decision_revision',
                                  'method_context', 'neutral_stream', 'source_bytes'])
def test_network_explicit_adapter_rejects_stale_or_unbound_evidence(tmp_path, kind):
    request = network_fixture(tmp_path)
    doc = json.loads(request.read_text(encoding='utf-8'))
    spec = doc['network_evidence'][0]
    if kind in ('request_scope', 'partition_bytes', 'calculation_bytes'):
        field = {'request_scope': 'request', 'partition_bytes': 'partition', 'calculation_bytes': 'calculation_run'}[kind]
        path = Path(spec[field]['path'])
        content = json.loads(path.read_text(encoding='utf-8'))
        if kind == 'request_scope':
            content['scope_id'] = '其他范围'
        elif kind == 'partition_bytes':
            content['network_fingerprint'] = 'f' * 64
        else:
            content['items'][0]['length_m'] += 1
        write(path, content)
        spec[field] = ref(path)
    elif kind == 'target_identity':
        spec['bindings'][0]['target_id'] = '没有该目标'
    elif kind == 'target_content':
        spec['bindings'][0]['target_fingerprint'] = 'e' * 64
    elif kind == 'object_content':
        spec['bindings'][0]['object_fingerprint'] = 'c' * 64
    elif kind == 'decision_revision':
        spec['decision_revision'] = '旧版'
    elif kind == 'method_context':
        mutate_reference(request, 'feedback', lambda d: d['annotations'][KEY].update(method_context_fingerprint='a' * 64))
        with pytest.raises(ValueError):
            export_candidate(request)
        return
    elif kind == 'neutral_stream':
        part = json.loads(Path(spec['partition']['path']).read_text(encoding='utf-8'))
        paths = part['provenance']['fingerprints']
        stream = next(Path(p) for p in paths if p.endswith('.ndjson'))
        stream.write_bytes(stream.read_bytes() + b'\n')
    elif kind == 'source_bytes':
        source = Path(doc['source_files'][0]['path'])
        source.write_bytes(source.read_bytes() + b'\n')
    write(request, doc)
    with pytest.raises((ValueError, KeyError)):
        export_candidate(request)


def test_context_inspection_does_not_fabricate_human_confirmation(tmp_path):
    request = fixture(tmp_path / '输入')
    doc = json.loads(request.read_text(encoding='utf-8'))
    doc.pop('human_decision_evidence')
    mutate_reference(request, 'feedback', lambda d: d['annotations'][KEY].pop('decision_type'))
    doc = json.loads(request.read_text(encoding='utf-8'))
    doc.pop('human_decision_evidence')
    fb = Path(doc['feedback']['path'])
    handoff = request.parent / '无批准的当前交接.json'
    cli(SCRIPTS / 'read_qto_review_feedback.py', ['--view', doc['view']['path'], '--feedback', fb, '--output', handoff], request.parent)
    doc['handoff'] = ref(handoff)
    write(request, doc)
    cli(SCRIPTS / 'qto_rule_candidates.py', ['inspect', '--request', request, '--maint-dir',
        tmp_path / '维护预览', '--name', '上下文预览.json'], request.parent)
    preview = json.loads((tmp_path / '维护预览/规则候选/上下文预览.json').read_text(encoding='utf-8'))
    assert preview['status'] == 'NOT_HUMAN_APPROVAL'
    assert 'human_decision_evidence' not in preview
    assert preview['method_context_fingerprint']
    with pytest.raises(ValueError, match='未明确确认'):
        export_candidate(request)


@pytest.mark.parametrize('kind,field', [('calculation_item', 'length_m'),
                                      ('circuit_quantity', 'complete_length_m'),
                                      ('connection', 'traced_path_length_m')])
def test_network_existing_stable_target_types_have_explicit_source_and_scope(tmp_path, kind, field):
    request = network_fixture(tmp_path)
    doc = json.loads(request.read_text(encoding='utf-8'))
    spec = doc['network_evidence'][0]
    partition = json.loads(Path(spec['partition']['path']).read_text(encoding='utf-8'))
    calc = json.loads(Path(spec['calculation_run']['path']).read_text(encoding='utf-8'))
    pool = {'calculation_item': calc['items'], 'circuit_quantity': partition['circuit_quantities'],
            'connection': partition['connections']}[kind]
    target = next(r for r in pool if r.get(field) is not None)
    identity = target['circuit_id'] if kind == 'circuit_quantity' else target['id']
    paths = set(target['path_ids'])
    atoms = [a for a in partition['physical_segments'] if a['id'] in paths]
    assert atoms
    view_path = Path(doc['view']['path'])
    raw = json.loads(view_path.read_text(encoding='utf-8'))
    obj = raw['objects'][0]
    obj.update(id=identity, catalog_key='网络显式目标|' + kind + '|' + identity,
               points=[p[:2] for p in atoms[0]['source_points']],
               parts=[[p[:2] for p in a['source_points']] for a in atoms],
               attributes={'被核数量': target[field]}, quantities=[])
    d = raw['review_workflow']['decisions'][0]
    d['scope']['member_keys'] = [obj['catalog_key']]
    d['refs'] = [{'kind': 'object', 'id': obj['catalog_key']}]
    write(view_path, raw)
    view = normalize_view(raw)
    spec['bindings'] = [{'catalog_key': obj['catalog_key'], 'object_fingerprint': view['objects'][0]['result_fingerprint'],
                         'target_kind': kind, 'target_id': identity, 'target_fingerprint': fingerprint(target),
                         'quantity_field': field, 'value_source': 'attribute', 'value_name': '被核数量'}]
    doc['view'] = ref(view_path)
    write(request, doc)
    refresh_synthetic_method_context(request)
    c = export_candidate(request)
    assert c['origin']['bindings'][0]['target_kind'] == kind
    assert c['origin']['bindings'][0]['value'] == target[field]


def test_network_unknown_complete_quantity_remains_null_with_pending_evidence(tmp_path):
    request = network_fixture(tmp_path, unknown=True)
    c = export_candidate(request)
    binding = c['origin']['bindings'][0]
    assert binding['value'] is None and binding['quantity_level'] == '未决量'
    assert binding['source_quantity_level'] is None
    assert binding['pending_fields']
    assert c['network_evidence'][0]['pending_review_items']
    assert c['business_adoption_allowed'] is False


def test_same_numeric_result_with_changed_input_still_requires_new_method_context(tmp_path):
    request = fixture(tmp_path / '输入')
    doc = json.loads(request.read_text(encoding='utf-8'))
    graph_path = Path(doc['calculation_input']['path'])
    graph = json.loads(graph_path.read_text(encoding='utf-8'))
    graph['nodes'][0]['basis'] = '不同输入依据；数量仍为10'
    write(graph_path, graph)
    new_run = request.parent / '等数值新结果.json'
    cli(SCRIPTS / 'recompute_qto_dependencies.py', ['--input', graph_path, '--output', new_run], request.parent)
    run = json.loads(new_run.read_text(encoding='utf-8'))
    view_path = Path(doc['view']['path'])
    view_raw = json.loads(view_path.read_text(encoding='utf-8'))
    old_view_fp = normalize_view(view_raw)['result_fingerprint']
    view_raw['objects'][0]['calculation_bindings'][0]['calculation_fingerprint'] = run['nodes'][0]['fingerprint']
    write(view_path, view_raw)
    assert normalize_view(view_raw)['result_fingerprint'] == old_view_fp
    doc.update(view=ref(view_path), calculation_input=ref(graph_path), calculation_run=ref(new_run))
    write(request, doc)
    with pytest.raises(ValueError, match='原方法确认未绑定当前输入'):
        export_candidate(request)


def test_human_saved_at_requires_aware_time_even_when_hashes_and_names_match(tmp_path):
    request = fixture(tmp_path / '输入')
    doc = json.loads(request.read_text(encoding='utf-8'))
    feedback_path = Path(doc['feedback']['path'])
    feedback = json.loads(feedback_path.read_text(encoding='utf-8'))
    feedback['annotations'][KEY]['updated_at'] = 'invalid-time'
    write(feedback_path, feedback)
    evidence_path = Path(doc['human_decision_evidence']['path'])
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    evidence['records'][0].update(annotation_fingerprint=fingerprint(feedback['annotations'][KEY]),
        source_feedback=ref(feedback_path, annotation_key=KEY, saved_at='invalid-time'))
    write(evidence_path, evidence)
    handoff = request.parent / '坏保存时间交接.json'
    cli(SCRIPTS / 'read_qto_review_feedback.py', ['--view', doc['view']['path'], '--feedback', feedback_path,
        '--output', handoff], request.parent)
    doc.update(feedback=ref(feedback_path), handoff=ref(handoff),
               human_decision_evidence=ref(evidence_path, record_id='E1'))
    write(request, doc)
    with pytest.raises(ValueError, match='saved_at 须为带时区时间'):
        export_candidate(request)


def si_production_fixture(directory):
    """Current official SI trace -> Evidence binding -> partition -> calculator.

    Anonymous source and local contracts only; no real human approval is claimed.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from test_electrical_object_binding_20261018 import fixture as geometry_fixture
    import qto_geometric_unit_evidence as units
    electrical = ROOT / 'skills/qto-electrical-routes/scripts'
    sys.path.insert(0, str(electrical))
    import partition_electrical_route_network as producer
    source_dir = directory / 'SI原源'
    _, bound_request, _, _, drawing, _, basis = geometry_fixture(source_dir, unit=4)
    drawing.units = 0
    model = drawing.modelspace()
    frame = model.add_lwpolyline([(-20000, -20000), (64100, -20000), (64100, 39400), (-20000, 39400)], close=True)
    scale = model.add_text('1:100', dxfattribs={'insert': (50000, -18000), 'height': 100})
    title = model.add_text('匿名电气平面图', dxfattribs={'insert': (-18000, -18000), 'height': 100})
    si = model.add_text('坐标单位：mm', dxfattribs={'insert': (1000, 6000), 'height': 100})
    dimension = model.add_aligned_dim((1000, 1000), (7000, 1000), distance=500, override={'dimlfac': 1.})
    dimension.render()
    for block in drawing.blocks:
        for solid in list(block.query('SOLID')):
            block.delete_entity(solid)
    source = source_dir / '匿名有效SI图.dxf'
    drawing.saveas(source)
    neutral = directory / 'SI中性原源'
    directory.mkdir(parents=True, exist_ok=True)
    cli(SCRIPTS / 'extract_cad_neutral.py', ['--source-dxf', source, '--output-dir', neutral], directory)
    manifest = neutral / 'CAD中性取证清单.json'
    mf = json.loads(manifest.read_text(encoding='utf-8'))
    scope = {'layout': 'Model', 'boundary_policy': 'contained',
             'bounds': {'xmin': -20000., 'ymin': -20000., 'xmax_exclusive': 64100., 'ymax_exclusive': 39400.}}
    unit_request = {'schema_version': units.REQUEST_SCHEMA, 'raw_unit': 'Millimeters',
        'measurement_semantics': 'plan_to_scale', 'frame': {'entity_refs': [{'handle': frame.dxf.handle}]},
        'scale_text': {'handle': scale.dxf.handle}, 'view_text_refs': [{'handle': title.dxf.handle}],
        'dimension_refs': [{'handle': dimension.dimension.dxf.handle}],
        'coordinate_unit_refs': [{'handle': si.dxf.handle}], 'basis': '真实SI文字、图框、比例和独立尺寸；匿名夹具'}
    credential = units.verify_geometric_unit_evidence(unit_request, mf, scope)
    credential['cad_manifest'] = ref(manifest)
    # Save first, as the real producers receive JSON rather than Python tuples.
    credential = json.loads(json.dumps(credential, ensure_ascii=False))
    trace_request = {'schema': 'qto-drawn-route-request/1.0', 'drawing_id': 'ANON', 'layout': 'Model',
        'unit': {'factor_to_output': .001, 'output_coordinate_unit': 'm', 'source_id': '真实SI及几何原源',
                 'geometric_unit_evidence': credential}, 'scope': {'bounds': scope['bounds']},
        'measurement_scope': scope, 'route_layers': '^ROUTE$', 'expand_blocks': ['DEVICE'],
        'snap_tolerance': .000001, 'gap_tolerance': 0., 'endpoint_tolerance': .000001,
        'routes': [{'route_id': 'R', 'from': {'point': [0., 0.]}, 'to': {'point': [10., 0.]}}]}
    import trace_drawn_routes
    trace_request['schema'] = trace_drawn_routes.REQUEST_SCHEMA
    trace_path = write(directory / '实际SI走线请求.json', trace_request)
    trace_out = directory / '实际SI走线输出'
    cli(electrical / 'trace_drawn_routes.py', ['--cad-manifest', manifest, '--request', trace_path,
        '--output-dir', trace_out], directory)
    bound_request.update(cad_manifest_sha256=sha(manifest), source_sha256=mf['authoritative_source']['sha256'],
                         measurement_scope=scope, trace_result=ref(trace_out / '走线追踪结果.json'))
    bound_request['unit']['geometric_unit_evidence'] = credential
    for endpoint in bound_request['endpoints']:
        endpoint['manifest_sha256'] = sha(manifest)
    for obj in bound_request['objects']:
        obj.update(trace_route_id='R', trace_context={'sheet_id': 'S1', 'floor': 'F1', 'basis_source_ids': [basis]})
        obj.pop('steps')
    bound_path = write(directory / '实际SI对象请求.json', bound_request)
    cli(electrical / 'bind_electrical_objects.py', ['--cad-manifest', manifest, '--request', bound_path,
        '--output-dir', directory / '实际SI对象输出'], directory)
    network_request = {'schema': producer.REQUEST_SCHEMA, 'scope_id': 'LOCAL', 'project_id': '匿名电气',
        'cad_manifest_sha256': sha(manifest), 'source_sha256': mf['authoritative_source']['sha256'],
        'trace_request': {k: v for k, v in trace_request.items() if k != 'routes'},
        'endpoint_objects': [{'object_key': key, 'category': category, 'source_ids': [basis], 'endpoint_binding': endpoint}
            for key, category, endpoint in zip(('A', 'B'), ('source', 'lighting'), bound_request['endpoints'])],
        'connections': [{'id': 'SUPPLY', 'from_object': 'A', 'to_object': 'B', 'relation': 'supply', 'source_ids': [basis]}],
        'circuit_specs': [], 'sheets': bound_request['sheets']}
    network_path = write(directory / '实际SI混连请求.json', network_request)
    network_out = directory / '实际SI混连输出'
    cli(electrical / 'partition_electrical_route_network.py', ['--cad-manifest', manifest,
        '--request', network_path, '--output-dir', network_out], directory)
    partition_path, ledger_path = network_out / '混连网络拆分结果.json', network_out / '路径台账.json'
    calculation_path = directory / '实际SI路径复算.json'
    cli(electrical / 'calculate_electrical_paths.py', [ledger_path, '--output', calculation_path], directory)
    partition = json.loads(partition_path.read_text(encoding='utf-8'))
    fresh = producer.partition_network(manifest, network_request, base_path=directory)
    receipt = fresh['provenance']['geometric_unit_consumption']
    assert any(isinstance(p, tuple) for p in receipt['checked_evidence']['frame']['polygon_wcs'])
    raw_counts = receipt['checked_evidence']['unit_text_search']['by_block_count']
    assert '_CLOSEDFILLED' in raw_counts
    assert '_CLOSEDFILLED' not in partition['provenance']['geometric_unit_consumption']['checked_evidence']['unit_text_search']['by_block_count']
    assert sum(a['length_m'] for a in partition['physical_segments']) == pytest.approx(10.)
    assert partition['physical_segments'][0]['conductor_quantity_m'] is None
    request_path = fixture(directory / '规则输入', '匿名电气')
    doc = json.loads(request_path.read_text(encoding='utf-8'))
    atom = partition['physical_segments'][0]
    view_path = Path(doc['view']['path'])
    raw_view = json.loads(view_path.read_text(encoding='utf-8'))
    key = '电气混连原段|' + atom['id']
    raw_view['sources'] = [{'id': 'DWG', 'title': '匿名有效SI原源', 'sha256': sha(source)}]
    raw_view['scenes'][0].update(source_id='DWG', coordinate_unit='mm', bounds=[-20000, -20000, 64100, 39400])
    raw_view['objects'] = [{'id': atom['id'], 'catalog_key': key, 'professional': 'qto-electrical-routes',
        'kind': 'line', 'scene_id': 'P1', 'points': [p[:2] for p in atom['source_points']],
        'title': '匿名10m原段', 'category': '电气线路', 'detail': '仅路径有据，导线量未知',
        'source': '真实SI图纸及原端口', 'status': atom['quantity_status'], 'quantities': [],
        'attributes': {'conductor_quantity_m': None}}]
    d = raw_view['review_workflow']['decisions'][0]
    d['scope']['member_keys'] = [key]
    d['refs'] = [{'kind': 'object', 'id': key}]
    write(view_path, raw_view)
    view = normalize_view(raw_view)
    spec = {'schema': 'qto-rule-electrical-network-evidence/1.0', 'network_id': '显式SI网络',
        'decision_id': 'METHOD', 'decision_revision': '1', 'scope_id': 'LOCAL',
        'network_fingerprint': partition['network_fingerprint'], 'manifest': ref(manifest), 'request': ref(network_path),
        'partition': ref(partition_path), 'ledger': ref(ledger_path), 'calculation_run': ref(calculation_path),
        'bindings': [{'catalog_key': key, 'object_fingerprint': view['objects'][0]['result_fingerprint'],
                      'target_kind': 'physical_segment', 'target_id': atom['id'], 'target_fingerprint': fingerprint(atom),
                      'quantity_field': 'conductor_quantity_m', 'value_source': 'attribute', 'value_name': 'conductor_quantity_m'}]}
    doc.update(view=ref(view_path), source_files=[ref(source, id='DWG')], network_evidence=[spec])
    doc.pop('calculation_input'); doc.pop('calculation_run')
    doc['draft']['applicability']['quantity_layer'] = '未决量'
    write(request_path, doc)
    refresh_synthetic_method_context(request_path)
    return request_path


def test_si_official_json_serialization_complete_inspect_export_review(tmp_path):
    from qto_rule_electrical_adapter import replay_document
    request = si_production_fixture(tmp_path / '合法SI链')
    doc = json.loads(request.read_text(encoding='utf-8'))
    n = doc['network_evidence'][0]
    import partition_electrical_route_network as producer
    nr = json.loads(Path(n['request']['path']).read_text(encoding='utf-8'))
    fresh = producer.partition_network(Path(n['manifest']['path']), nr, base_path=Path(n['request']['path']).parent)
    persisted = json.loads(Path(n['partition']['path']).read_text(encoding='utf-8'))
    assert replay_document(producer.clean(fresh)) == replay_document(persisted)
    preview = export_candidate(request, preview=True)
    assert preview == json.loads(json.dumps(preview, ensure_ascii=False))
    context = preview['network_evidence'][0]
    assert context['partition_fingerprint'] == fingerprint(replay_document(fresh))
    assert context['serialized_partition_fingerprint'] == fingerprint(replay_document(persisted))
    assert context['partition_fingerprint'] != context['serialized_partition_fingerprint']
    cli(SCRIPTS / 'qto_rule_candidates.py', ['inspect', '--request', request, '--maint-dir', tmp_path / '维护',
        '--name', '当前SI上下文.json'], request.parent)
    c = candidate(request, tmp_path / '维护')
    exported = export_candidate(request)
    assert exported == json.loads(c.read_text(encoding='utf-8'))
    assert exported['origin']['bindings'][0]['value'] is None
    r = review_request(tmp_path / '审核输入', c, 'project_only')
    cli(ROOT / 'maintenance/review_rule_candidates.py', ['review', '--request', r, '--maint-dir',
        tmp_path / '维护', '--name', '当前SI项目审核.json'], r.parent)
    assert review_candidate(r)['disposition'] == 'project_only'


@pytest.mark.parametrize('kind', ['extra_underscore', 'missing_field', 'quantity_drift',
                                  'cad_path_alias', 'credential_drift', 'scope_drift', 'human_evidence_drift'])
def test_si_serialization_repair_keeps_complete_document_and_guards(tmp_path, kind):
    request = si_production_fixture(tmp_path / '当前SI')
    doc = json.loads(request.read_text(encoding='utf-8'))
    n = doc['network_evidence'][0]
    if kind in ('extra_underscore', 'missing_field', 'quantity_drift', 'cad_path_alias'):
        path = Path(n['partition']['path'])
        partition = json.loads(path.read_text(encoding='utf-8'))
        if kind == 'extra_underscore':
            partition['provenance']['geometric_unit_consumption']['checked_evidence']['unit_text_search']['by_block_count']['_UNAUTHORIZED_EXTRA'] = 1
        elif kind == 'missing_field':
            partition['provenance']['geometric_unit_consumption'].pop('dependency_fingerprints')
        elif kind == 'quantity_drift':
            partition['physical_segments'][0]['length_m'] = 11.
        else:
            # Byte-identical CAD copy is still another business source path.
            original = Path(doc['source_files'][0]['path'])
            alias = request.parent / '另路径同字节CAD.dxf'
            alias.write_bytes(original.read_bytes())
            assert sha(alias) == sha(original)
            paths = partition['provenance']['fingerprints']
            paths[str(alias)] = paths.pop(str(original.resolve()))
        write(path, partition)
        n['partition'] = ref(path)
    elif kind in ('credential_drift', 'scope_drift'):
        path = Path(n['request']['path'])
        network_request = json.loads(path.read_text(encoding='utf-8'))
        if kind == 'credential_drift':
            network_request['trace_request']['unit']['geometric_unit_evidence']['runtime_dependencies'].pop()
        else:
            network_request['trace_request']['measurement_scope']['bounds']['xmin'] += 1.
        write(path, network_request)
        n['request'] = ref(path)
    else:
        mutate_reference(request, 'human_decision_evidence', lambda d: d['records'][0].update(reviewer='GPT-6'))
        with pytest.raises(ValueError):
            export_candidate(request)
        return
    write(request, doc)
    with pytest.raises(ValueError):
        export_candidate(request, preview=True)


def test_si_equal_quantity_changed_input_invalidates_method_context(tmp_path):
    request = si_production_fixture(tmp_path / '当前SI')
    doc = json.loads(request.read_text(encoding='utf-8'))
    n = doc['network_evidence'][0]
    before = export_candidate(request, preview=True)
    path = Path(n['request']['path'])
    network_request = json.loads(path.read_text(encoding='utf-8'))
    network_request['title'] = '同数量但输入语义版本改变'
    new_request = write(request.parent / '新SI网络请求.json', network_request)
    electrical = ROOT / 'skills/qto-electrical-routes/scripts'
    output = request.parent / '同数量新SI网络'
    cli(electrical / 'partition_electrical_route_network.py', ['--cad-manifest', n['manifest']['path'],
        '--request', new_request, '--output-dir', output], request.parent)
    calculation = request.parent / '同数量新SI复算.json'
    cli(electrical / 'calculate_electrical_paths.py', [output / '路径台账.json', '--output', calculation], request.parent)
    partition = json.loads((output / '混连网络拆分结果.json').read_text(encoding='utf-8'))
    n.update(request=ref(new_request), partition=ref(output / '混连网络拆分结果.json'),
             ledger=ref(output / '路径台账.json'), calculation_run=ref(calculation),
             network_fingerprint=partition['network_fingerprint'])
    write(request, doc)
    after = export_candidate(request, preview=True)
    assert before['bindings'][0]['value'] is after['bindings'][0]['value'] is None
    assert before['method_context_fingerprint'] != after['method_context_fingerprint']
    with pytest.raises(ValueError, match='未绑定当前输入'):
        export_candidate(request)


def test_si_installed_runtime_produces_candidate_repository_reviews_same_bytes(tmp_path):
    import shutil
    installed = tmp_path / '隔离已安装运行包'
    shutil.copytree(ROOT / 'skills', installed / 'skills')
    # Test-only production driver; the actual runtime contains no maintenance CLI.
    shutil.copytree(ROOT / 'tests', installed / 'tests')
    assert not (installed / 'maintenance').exists()
    inputs, maintenance = tmp_path / '客户端生产输入', tmp_path / '客户端维护候选'
    driver = tmp_path / '客户端实际生产.py'
    driver.write_text(
        'import sys\nfrom pathlib import Path\n' +
        'sys.path.insert(0, ' + repr(str(installed / 'tests')) + ')\n' +
        'from test_rule_candidates_20261019 import si_production_fixture,candidate\n' +
        'request=si_production_fixture(Path(' + repr(str(inputs)) + '))\n' +
        'candidate(request,Path(' + repr(str(maintenance)) + '))\n', encoding='utf-8')
    cli(driver, [], tmp_path)
    request = inputs / '规则输入/导出请求.json'
    cpath = maintenance / '规则候选/匿名候选.json'
    client = json.loads(cpath.read_text(encoding='utf-8'))
    # Both producer module location and the installed export location differ.
    client_request = json.loads(Path(client['request']['path']).read_text(encoding='utf-8'))
    producer_document = json.loads(Path(client_request['network_evidence'][0]['partition']['path']).read_text(encoding='utf-8'))
    assert any(Path(p).is_relative_to(installed) for p in producer_document['provenance']['dependency_fingerprints_before'])
    r = review_request(tmp_path / '仓库审核输入', cpath, 'project_only')
    cli(ROOT / 'maintenance/review_rule_candidates.py', ['review', '--request', r,
        '--maint-dir', tmp_path / '仓库审核输出', '--name', '异目录同字节审核.json'], r.parent)
    assert review_candidate(r)['disposition'] == 'project_only'
    # A declared producer module changing later invalidates this exact receipt.
    module = installed / 'skills/_qto-shared-evidence/scripts/qto_cad_geometry.py'
    original = module.read_bytes()
    module.write_bytes(original + b'\n# isolated module byte drift\n')
    with pytest.raises(ValueError):
        review_candidate(r)
    module.write_bytes(original)
    assert review_candidate(r)['disposition'] == 'project_only'
