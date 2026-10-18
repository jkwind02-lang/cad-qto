"""Anonymous real-file/CLI acceptance for T39. These do not approve engineering quantities."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'qto-coordinator' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import coordination_contract as c
from qto_coordination import initialize


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'file': str(path), 'sha256': c.digest(path)}


def cli(tmp, command, request, *args):
    source = tmp / ('input_' + command + '.json')
    write(source, request)
    result = subprocess.run([sys.executable, str(SCRIPTS / 'qto_coordination.py'), command,
                             '--input', str(source), *map(str, args)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding='utf-8',
                            env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    assert not result.stderr, result.stderr
    return result.returncode, json.loads(result.stdout)


def identity(tmp):
    source = tmp / '匿名设计来源.txt'
    source.write_text('匿名设计基准R1：线路X，实际段S1；测试用，不是工程图。', encoding='utf-8')
    return {'physical_id': 'LINE-X', 'quantity_type': 'cable_plan_length', 'unit': 'm',
            'basis': '设计路径平面长度，不含竖向', 'actual_scope': ['匿名楼/一层/实际段S1'],
            'source_revision': 'R1', 'source_fingerprint': c.digest(source)}


def handoff_fixture(tmp):
    i = identity(tmp)
    result_refs = []
    for pid, included in [('P-A', False), ('P-B', True)]:
        row = dict(i, row_id='r1', quantity=12.5, included=included, source_ids=['原编号X'], accounting_class='清单内')
        row.pop('source_revision')
        row.pop('source_fingerprint')
        result = {'schema': 'qto-package-quantity-rows/1.0', 'package_id': pid,
                  'source_revision': i['source_revision'], 'source_fingerprint': i['source_fingerprint'], 'rows': [row]}
        ref = write(tmp / (pid + '.json'), result)
        result_refs.append({'package_id': pid, 'skill': 'qto-electrical-routes',
                            'result_file': ref['file'], 'result_sha256': ref['sha256']})
    source = tmp / '匿名设计来源.txt'
    baseline = {'schema': 'qto-quantity-universe/1.0', 'complete': True,
                'source_documents': [{'file': str(source), 'sha256': c.digest(source)}],
                'items': [dict(i, source_id='原编号X')]}
    bref = write(tmp / '完整编号基准.json', baseline)
    handoff = dict(i, handoff_id='H1', proposer_package='P-A', receiver_package='P-B', quantity=12.5,
                   status='accepted', receiver_row_id='r1', receiver_result_sha256=result_refs[1]['result_sha256'],
                   proposal_evidence='本包实际段交接；接收记录绑定真实成果文件及r1')
    return {'schema': 'qto-package-reconciliation/1.0', 'project_id': '匿名演练',
            'packages': result_refs, 'baseline': bref, 'handoffs': [handoff]}


def mutate_package(request, index, mutate):
    p = request['packages'][index]
    path = Path(p['result_file'])
    obj = c.load_json(path)
    mutate(obj)
    p['result_sha256'] = write(path, obj)['sha256']
    for h in request['handoffs']:
        if h['receiver_package'] == p['package_id']:
            h['receiver_result_sha256'] = p['result_sha256']


def codes(report):
    return {x['code'] for x in report.get('findings', [])}


def delivery_fixture(tmp, pending=False):
    for name, text in [('成果汇总.md', '# 匿名成果汇总\n校验器已运行；测试量。'),
                       ('复核.html', '<!doctype html><html lang="zh-CN"><body>匿名复核页</body></html>')]:
        (tmp / name).write_text(text, encoding='utf-8')
    release = write(tmp / '发布来源.json', {'schema': 'cad-qto-release-source/1.0',
                    'business_version': '0.3.16', 'source_commit': '8' * 40})
    row = {'row_id': 'u1', '环节': '接口交接', '涉及Skill与插件脚本': 'qto-coordinator',
           '卡点': '接收成果行没有接口事实' if pending else '无',
           '改用方法与位置': '人工列入项目未决表' if pending else '无',
           '影响数量': '不确定' if pending else '无', '登记': '待协调登记' if pending else '无'}
    return {'schema': 'qto-delivery-manifest/1.0', 'package_id': 'P-A', 'has_numeric_results': True,
            'source_mode': 'released', 'release_source': release,
            'artifacts': [{'role': role, 'file': str(tmp / name), 'sha256': c.digest(tmp / name)}
                          for role, name in [('summary', '成果汇总.md'), ('html_review', '复核.html')]],
            'plugin_usage': {'插件版本': '0.3.16', '提交': '8' * 40, '记录': [row]}}


def intake_fixture(tmp):
    manifest = delivery_fixture(tmp, pending=True)
    a = write(tmp / 'P-A使用.json', manifest)
    b_manifest = copy.deepcopy(manifest)
    b_manifest['package_id'] = 'P-B'
    b = write(tmp / 'P-B使用.json', b_manifest)
    return {'schema': 'qto-plugin-issue-intake/1.0', 'project_id': '匿名收件', 'host': 'Codex',
            'packages': [{'package_id': 'P-A', 'usage_file': a['file'], 'usage_sha256': a['sha256']},
                         {'package_id': 'P-B', 'usage_file': b['file'], 'usage_sha256': b['sha256']}]}


def test_real_cli_handoff_pass(tmp_path):
    request = handoff_fixture(tmp_path)
    code, report = cli(tmp_path, 'handoffs', request, '--out', tmp_path / '回执.json')
    assert code == 0 and report['status'] == 'PASS', report
    assert report['baseline_complete_with_sources'] and report['coverage_check']
    assert not report['engineering_acceptance']


@pytest.mark.parametrize('field,value', [('physical_id', 'OTHER'), ('unit', 'km'),
    ('quantity_type', 'box_count'), ('basis', '另一口径'), ('actual_scope', ['另一楼/实际段S1']),
    ('source_revision', 'R2'), ('source_fingerprint', '9' * 64)])
def test_receiver_identity_mismatch(tmp_path, field, value):
    request = handoff_fixture(tmp_path)
    request['handoffs'][0][field] = value
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and 'handoff_identity_mismatch' in codes(report)


def test_declaration_only_not_received(tmp_path):
    request = handoff_fixture(tmp_path)
    request['handoffs'][0]['receiver_row_id'] = '不存在'
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and 'receiver_row_missing' in codes(report)


@pytest.mark.parametrize('state', ['pending', 'rejected', 'arbitration'])
def test_unaccepted_is_unresolved(tmp_path, state):
    request = handoff_fixture(tmp_path)
    request['handoffs'][0]['status'] = state
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 3 and 'handoff_unaccepted' in codes(report)


def test_stale_receiver_fingerprint(tmp_path):
    request = handoff_fixture(tmp_path)
    request['handoffs'][0]['receiver_result_sha256'] = '0' * 64
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and 'receiver_result_fingerprint' in codes(report)


def test_zero_outlet_and_conservation(tmp_path):
    request = handoff_fixture(tmp_path)
    request['handoffs'] = []
    mutate_package(request, 1, lambda result: result['rows'][0].update(included=False))
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and 'zero_outlet' in codes(report)
    mutate_package(request, 1, lambda result: result['rows'][0].update(included=True, source_ids=['编造编号']))
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and {'source_id_not_conserved', 'source_id_outside_baseline'} <= codes(report)


def test_multiple_outlets_not_hidden_by_boq_classification(tmp_path):
    request = handoff_fixture(tmp_path)
    mutate_package(request, 0, lambda result: result['rows'][0].update(included=True, accounting_class='清单外新增'))
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and {'multiple_outlets', 'proposer_still_counts'} <= codes(report)


@pytest.mark.parametrize('field,value', [('basis', '按清单外新增计量'), ('unit', 'km')])
def test_basis_or_unit_does_not_hide_same_object(tmp_path, field, value):
    request = handoff_fixture(tmp_path)
    mutate_package(request, 0, lambda result: result['rows'][0].update(included=True, **{field: value}))
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and 'quantity_basis_conflict' in codes(report)


def test_missing_or_incomplete_baseline_unverified(tmp_path):
    request = handoff_fixture(tmp_path)
    request['baseline'] = None
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 3 and not report['baseline_complete_with_sources']
    request = handoff_fixture(tmp_path)
    obj = c.load_json(request['baseline']['file'])
    obj['complete'] = False
    request['baseline'] = write(Path(request['baseline']['file']), obj)
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 3 and 'baseline_incomplete' in codes(report)


def test_legal_split_same_original_id_is_not_duplicate(tmp_path):
    request = handoff_fixture(tmp_path)
    request['handoffs'] = []
    mutate_package(request, 0, lambda result: result['rows'][0].update(included=True, actual_scope=['匿名楼/一层/实际段S2']))
    obj = c.load_json(request['baseline']['file'])
    original = obj['items'][0]
    original.update(relationship='split', split_basis='原编号按设计两段S1与S2互斥分包')
    obj['items'].append(dict(original, actual_scope=['匿名楼/一层/实际段S2']))
    request['baseline'] = write(Path(request['baseline']['file']), obj)
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 0 and not {'multiple_outlets', 'overlapping_outlets'} & codes(report), report


def test_overlap_in_baseline_cannot_legitimize_duplicate(tmp_path):
    request = handoff_fixture(tmp_path)
    obj = c.load_json(request['baseline']['file'])
    obj['items'].append(dict(obj['items'][0], source_id='又一个编号', basis='另一自由文本'))
    request['baseline'] = write(Path(request['baseline']['file']), obj)
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 2 and 'baseline_overlap_ambiguous' in codes(report)


def test_other_quantity_type_does_not_become_duplicate(tmp_path):
    request = handoff_fixture(tmp_path)
    request['handoffs'] = []
    mutate_package(request, 0, lambda result: result['rows'][0].update(included=True, quantity_type='box_count', unit='台', source_ids=['箱体编号']))
    obj = c.load_json(request['baseline']['file'])
    obj['items'].append(dict(obj['items'][0], source_id='箱体编号', quantity_type='box_count', unit='台'))
    request['baseline'] = write(Path(request['baseline']['file']), obj)
    code, report = cli(tmp_path, 'handoffs', request)
    assert code == 0 and not {'multiple_outlets', 'quantity_basis_conflict'} & codes(report), report


@pytest.mark.parametrize('role', ['summary', 'html_review'])
def test_delivery_missing_then_explicit_user_waiver(tmp_path, role):
    request = delivery_fixture(tmp_path)
    request['artifacts'] = [a for a in request['artifacts'] if a['role'] != role]
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 2 and 'required_artifact_missing' in codes(report)
    quote = '本匿名包P-A本次明确免交' + role
    source = tmp_path / '用户授权.txt'
    source.write_text(quote, encoding='utf-8')
    request['waivers'] = {role: {'actor': 'user', 'at': '2026-10-19T09:00:00+08:00',
                               'quote': quote, 'scope': {'package_id': 'P-A', 'roles': [role]},
                               'source': {'file': str(source), 'sha256': c.digest(source)}}}
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 0 and 'user_waiver' in codes(report), report
    request['waivers'][role]['scope']['package_id'] = 'OTHER'
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 2 and 'waiver_invalid' in codes(report)


def test_diagnostic_has_no_quantity_delivery_requirement(tmp_path):
    request = delivery_fixture(tmp_path)
    request.update(has_numeric_results=False, artifacts=[])
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 0


def test_release_usage_mismatch(tmp_path):
    request = delivery_fixture(tmp_path)
    request['plugin_usage']['提交'] = '9' * 40
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 2 and 'release_usage_mismatch' in codes(report)


def test_development_truthful_not_release(tmp_path):
    request = delivery_fixture(tmp_path)
    src = tmp_path / '受测源码.py'
    src.write_text('print("匿名开发源")', encoding='utf-8')
    files = [{'file': str(src), 'sha256': c.digest(src)}]
    fp = __import__('hashlib').sha256(c.canonical(files).encode('utf-8')).hexdigest()
    request.update(source_mode='development', development_source={'baseline_commit': '8' * 40,
                   'business_version': '0.3.16', 'files': files, 'source_fingerprint': fp})
    request['plugin_usage']['development_source_fingerprint'] = fp
    request.pop('release_source')
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 0 and report['source_verification'] == 'DEVELOPMENT_VERIFIED_NOT_RELEASED'
    src.write_text('changed', encoding='utf-8')
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 2 and 'file_fingerprint_mismatch' in codes(report)


def test_method_replacement_reverse_check_and_registration_file(tmp_path):
    request = delivery_fixture(tmp_path)
    request['method_replacements'] = [{'usage_row_id': 'u1', 'fact': '用项目脚本临时替代'}]
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 2 and 'replacement_undeclared' in codes(report)
    row = request['plugin_usage']['记录'][0]
    row.update(卡点='接收接口计算没有实现', 改用方法与位置='项目临时脚本.py', 登记='20261019-0900_codex_协调_接口.md')
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 3 and 'file_reference_missing' in codes(report)


def test_missing_usage_preserves_minimum_compatibility_but_unverified(tmp_path):
    request = delivery_fixture(tmp_path)
    request.pop('plugin_usage')
    code, report = cli(tmp_path, 'delivery', request)
    assert code == 3 and 'plugin_usage_missing' in codes(report)


def test_collect_real_registration_idempotent_and_backflow(tmp_path):
    request = intake_fixture(tmp_path)
    state = tmp_path / '收件演练'
    code, report = cli(tmp_path, 'collect', request, '--state-dir', state)
    assert code == 0 and report['issue_count'] == 1 and report['rehearsal']
    ledger = c.load_json(state / '插件问题收件.json')
    issue = next(iter(ledger['issues'].values()))
    original = Path(issue['registration_file'])
    assert original.is_file() and '## 收件来源' in original.read_text(encoding='utf-8')
    assert issue['affected_packages'] == ['P-A', 'P-B'] and len(issue['sources']) == 2
    code, report = cli(tmp_path, 'collect', request, '--state-dir', state)
    assert code == 0 and len(list((state / '隔离维护演练').rglob('*.md'))) == 1
    with original.open('a', encoding='utf-8') as f:
        f.write('\n## 维护回写 匿名测试\n- 处理版本: 0.3.17 / 未发布\n- 处理状态: 代码已修复\n')
    code, report = cli(tmp_path, 'collect', request, '--state-dir', state)
    assert code == 0
    flow = c.load_json(state / '插件回流.json')
    assert flow['issues'][0]['processing_version'] == '0.3.17 / 未发布'
    assert flow['issues'][0]['actual_rerun'] is False
    code, report = cli(tmp_path, 'backflow', flow)
    assert code == 3 and 'published_not_rerun' in codes(report)
    flow['issues'][0]['actual_rerun'] = True
    code, report = cli(tmp_path, 'backflow', flow)
    assert code == 2 and 'rerun_without_evidence' in codes(report)


def test_collect_interruption_after_real_register_recovers(tmp_path, monkeypatch):
    request = intake_fixture(tmp_path)
    state = tmp_path / '中断演练'
    real = c.atomic_json
    def interrupt(path, value):
        if Path(path).name == '插件问题收件.json':
            raise OSError('匿名注入：登记已落盘，回执写盘前中断')
        return real(path, value)
    monkeypatch.setattr(c, 'atomic_json', interrupt)
    with pytest.raises(OSError, match='中断'):
        c.collect_issues(request, tmp_path, state)
    assert len(list((state / '隔离维护演练').rglob('*.md'))) == 1
    monkeypatch.setattr(c, 'atomic_json', real)
    report = c.collect_issues(request, tmp_path, state)
    assert report['status'] == 'PASS' and len(list((state / '隔离维护演练').rglob('*.md'))) == 1
    ledger = c.load_json(state / '插件问题收件.json')
    Path(next(iter(ledger['issues'].values()))['registration_file']).unlink()
    with pytest.raises(ValueError, match='原始登记'):
        c.collect_issues(request, tmp_path, state)


def test_os_lock_released_after_process_termination(tmp_path):
    state = tmp_path / '进程退出锁演练'
    state.mkdir()
    script = ('import sys,time; sys.path.insert(0,' + repr(str(SCRIPTS)) + '); '
              'from coordination_contract import IntakeLock; '
              'lock=IntakeLock(' + repr(str(state / '收件.lock')) + '); '
              'lock.__enter__(); print("ready",flush=True); time.sleep(30)')
    proc = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    try:
        assert proc.stdout.readline().strip() == 'ready'
        with pytest.raises(ValueError, match='占用'):
            with c.IntakeLock(state / '收件.lock'):
                pass
    finally:
        proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
    request = intake_fixture(tmp_path)
    result = c.collect_issues(request, tmp_path, state)
    assert result['status'] == 'PASS' and result['issue_count'] == 1


def test_collect_explicit_existing_original_uses_real_registration(tmp_path):
    request = intake_fixture(tmp_path)
    maint = tmp_path / '显式维护演练'
    (maint / '问题登记' / '待处理').mkdir(parents=True)
    body = tmp_path / '真实模板已填.md'
    body.write_text('''---
登记时间: 2026-10-19 09:00
发现宿主: Codex 匿名测试
项目与任务: 匿名收件
插件版本: 0.3.16 / 提交 8888888
涉及Skill: qto-coordinator
类型: 能力缺失
影响识图或数量: 不确定
阻断程度: 已临时处理
---
## 现象
接收成果行没有接口事实
## 复现材料
匿名来源原文
## 期望行为
维护分拣
## 本次临时处理及其位置
项目未决表
''', encoding='utf-8')
    proc = subprocess.run([sys.executable, str(c.SHARED / 'register_plugin_issue.py'), '--maint-dir', str(maint),
                           '--host', 'Codex', '--topic', '协调', '--title', '接口事实', '--body-file', str(body)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
                          env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    assert proc.returncode == 0
    path = Path(json.loads(proc.stdout)['path'])
    row = c.load_json(request['packages'][0]['usage_file'])['plugin_usage']['记录'][0]
    sig = c.intake_signature(request['project_id'], row)
    request['existing_registrations'] = [{'signature': sig, 'file': str(path), 'sha256': c.digest(path),
                                          'match_evidence': '接收成果行没有接口事实'}]
    result = c.collect_issues(request, tmp_path, tmp_path / '关联收件', maint)
    assert result['status'] == 'PASS' and len(list((maint / '问题登记').rglob('*.md'))) == 1
    request.pop('existing_registrations')
    result = c.collect_issues(request, tmp_path, tmp_path / '关联收件', maint)
    assert result['status'] == 'PASS' and len(list((maint / '问题登记').rglob('*.md'))) == 1


def test_collect_source_hash_invalid_writes_no_issue(tmp_path):
    request = intake_fixture(tmp_path)
    request['packages'][0]['usage_sha256'] = '0' * 64
    state = tmp_path / '坏来源演练'
    result = c.collect_issues(request, tmp_path, state)
    assert result['status'] == 'FAIL' and not list((state / '隔离维护演练').rglob('*.md'))


def test_backflow_requires_matching_actual_rerun_outputs(tmp_path):
    request = intake_fixture(tmp_path)
    state = tmp_path / '复跑演练'
    c.collect_issues(request, tmp_path, state)
    flow = c.load_json(state / '插件回流.json')
    issue = flow['issues'][0]
    issue.update(actual_rerun=True, processing_version='开发受测源码集')
    result_file = tmp_path / '真实复跑输出.txt'
    command = [sys.executable, '-c', 'print("anonymous actual subprocess output")']
    executed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert executed.returncode == 0
    result_file.write_text(executed.stdout, encoding='utf-8')
    receipt = {'schema': 'qto-project-rerun-receipt/1.0', 'status': 'PASS', 'project_id': request['project_id'],
               'processing_version': issue['processing_version'], 'packages': ['P-A', 'P-B'],
               'executions': [{'command': command, 'exit_code': executed.returncode}],
               'output_artifacts': [{'file': str(result_file), 'sha256': c.digest(result_file)}]}
    issue['rerun_receipt'] = write(tmp_path / '复跑凭据.json', receipt)
    assert c.backflow(flow, tmp_path)['status'] == 'PASS'
    receipt['packages'] = ['P-A']
    issue['rerun_receipt'] = write(tmp_path / '复跑凭据.json', receipt)
    assert 'rerun_scope_mismatch' in codes(c.backflow(flow, tmp_path))


def test_initialize_parameterized_no_agents_no_overwrite(tmp_path):
    req = {'schema': 'qto-coordination-init/1.0', 'project_id': '另一匿名项目',
           'capabilities': ['qto-pipe-networks'], 'packages': [{'package_id': '区域甲',
            'skill': 'qto-pipe-networks', 'actual_scope': ['楼A/一层'], 'responsibility': '本区域管道主量'}]}
    out = tmp_path / '新协调工作区'
    code, report = cli(tmp_path, 'init', req, '--out-dir', out)
    assert code == 0 and report['package_count'] == 1 and report['automatic_agent_creation'] is False
    assert len(c.load_json(out / '协调配置.json')['capabilities']) == 1
    code, report = cli(tmp_path, 'init', req, '--out-dir', out)
    assert code == 2
    req.update(capabilities=[], packages=[])
    initialize(req, tmp_path / '未决范围')
    assert c.load_json(tmp_path / '未决范围' / '协调配置.json')['readiness'] == 'INPUT_REQUIRED'


@pytest.mark.parametrize('mode', ['off', 'test', 'on'])
def test_project_hook_plan_never_installs(tmp_path, mode):
    request = {'schema': 'qto-project-hook-plan/1.0', 'mode': mode, 'project_id': '匿名',
               'project_root': str(tmp_path), 'on_since': '2026-10-19T09:00:00+08:00',
               'eligible_session_ids': ['new-session-only']}
    code, report = cli(tmp_path, 'hook-plan', request)
    assert code == 0 and not report['applied'] and not report['global_config_changed'] and not report['host_loading_verified']


def valid_pdf_bytes():
    """One real text page, with correct xref offsets; no optional authoring dependency."""
    stream = b'BT /F1 12 Tf 20 100 Td (Anonymous summary: 12.5 m) Tj ET\n'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] '
               b'/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    data = b'%PDF-1.4\n'
    offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(n).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    startxref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return data + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(startxref).encode() + b'\n%%EOF\n'


@pytest.mark.parametrize('form', ['valid', 'fake', 'truncated', 'header_only'])
def test_pdf_summary_requires_actual_readable_page(tmp_path, form):
    request = delivery_fixture(tmp_path)
    raw = valid_pdf_bytes()
    if form == 'fake':
        raw = b'This is not a PDF.\x00'
    elif form == 'truncated':
        raw = raw[:len(raw) // 2]
    elif form == 'header_only':
        raw = b'%PDF-1.4\n%%EOF\n'
    path = tmp_path / ('summary_' + form + '.pdf')
    path.write_bytes(raw)
    request['artifacts'][0] = dict(role='summary', file=str(path), sha256=c.digest(path))
    out = tmp_path / 'PDF检查回执.json'
    code, report = cli(tmp_path, 'delivery', request, '--out', out)
    if form == 'valid':
        from pypdf import PdfReader
        reader = PdfReader(path, strict=True)
        assert '12.5 m' in reader.pages[0].extract_text()
        assert code == 0 and report['status'] == 'PASS'
    else:
        assert code == 2 and report['status'] == 'FAIL' and 'artifact_unreadable' in codes(report)
    assert c.load_json(out)['status'] == report['status']
    assert report['engineering_acceptance'] is False


def test_pdf_parser_unavailable_is_unverified(tmp_path, monkeypatch):
    import builtins
    request = delivery_fixture(tmp_path)
    path = tmp_path / 'valid.pdf'
    path.write_bytes(valid_pdf_bytes())
    request['artifacts'][0] = dict(role='summary', file=str(path), sha256=c.digest(path))
    real_import = builtins.__import__
    def missing_parser(name, *args, **kwargs):
        if name == 'pypdf':
            raise ImportError('匿名测试：解析依赖不可用')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing_parser)
    report = c.validate_delivery(request, tmp_path)
    assert report['status'] == 'UNVERIFIED' and 'artifact_parser_unavailable' in codes(report)


@pytest.mark.parametrize('valid', [False, True])
def test_xlsx_invalid_is_business_failure_and_valid_control_passes(tmp_path, valid):
    request = delivery_fixture(tmp_path)
    path = tmp_path / 'summary.xlsx'
    if valid:
        from openpyxl import Workbook
        book = Workbook()
        book.active['A1'] = 'Anonymous summary'
        book.active['B1'] = 12.5
        book.save(path)
        book.close()
    else:
        path.write_bytes(b'not-an-xlsx')
    request['artifacts'][0] = dict(role='summary', file=str(path), sha256=c.digest(path))
    out = tmp_path / 'XLSX检查回执.json'
    code, report = cli(tmp_path, 'delivery', request, '--out', out)
    assert code == (0 if valid else 2)
    assert report['status'] == ('PASS' if valid else 'FAIL')
    assert c.load_json(out)['status'] == report['status']
    if not valid:
        assert 'artifact_unreadable' in codes(report)


@pytest.mark.parametrize('valid', [False, True])
def test_additional_release_source_schema_uses_same_contract(tmp_path, valid):
    request = delivery_fixture(tmp_path)
    request['plugin_usage']['换版'] = [{'插件版本': '0.3.15', '提交': 'a' * 7, '换版点': '匿名后段'}]
    source = {'schema': 'cad-qto-release-source/1.0' if valid else 'unrelated-local-json/1.0',
              'business_version': '0.3.15', 'source_commit': 'a' * 40}
    request['additional_release_sources'] = [write(tmp_path / '附加来源.json', source)]
    code, report = cli(tmp_path, 'delivery', request)
    assert code == (0 if valid else 2)
    if not valid:
        assert 'release_source_schema' in codes(report)


def test_xlsx_corrupt_deflate_returns_business_failure(tmp_path):
    import zipfile
    import zlib
    request = delivery_fixture(tmp_path)
    path = tmp_path / 'compressed_but_damaged.xlsx'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', '<workbook>anonymous summary</workbook>')
        info = archive.getinfo('xl/workbook.xml')
    raw = bytearray(path.read_bytes())
    start = info.header_offset + 30 + len(info.filename.encode('utf-8')) + len(info.extra)
    raw[start] = 0xff  # DEFLATE block type 3 is invalid; the ZIP directory remains valid.
    path.write_bytes(raw)
    with pytest.raises(zlib.error):
        with zipfile.ZipFile(path) as archive:
            archive.testzip()
    request['artifacts'][0] = dict(role='summary', file=str(path), sha256=c.digest(path))
    code, report = cli(tmp_path, 'delivery', request, '--out', tmp_path / '压缩损坏回执.json')
    assert code == 2 and 'artifact_unreadable' in codes(report)


def test_same_package_row_sha_from_different_source_files_is_conserved(tmp_path):
    request = intake_fixture(tmp_path)
    first = request['packages'][0]
    second_file = tmp_path / '同内容第二真实来源.json'
    second_file.write_bytes(Path(first['usage_file']).read_bytes())
    second = dict(first, usage_file=str(second_file))
    assert first['usage_sha256'] == c.digest(second_file)
    state = tmp_path / '多来源收件'
    request['packages'] = [first]
    code, _ = cli(tmp_path, 'collect', request, '--state-dir', state)
    assert code == 0
    request['packages'] = [second]
    code, _ = cli(tmp_path, 'collect', request, '--state-dir', state)
    assert code == 0
    ledger = c.load_json(state / '插件问题收件.json')
    entry = next(iter(ledger['issues'].values()))
    assert len(ledger['issues']) == 1 and len(entry['sources']) == 2
    assert {Path(s['source_file']).resolve() for s in entry['sources']} == {Path(first['usage_file']).resolve(), second_file.resolve()}
    flow = c.load_json(state / '插件回流.json')
    assert len(flow['issues'][0]['sources']) == 2
    # The identical real file, including a path with dot components, stays idempotent.
    dot_dir = tmp_path / '点路径'
    dot_dir.mkdir()
    second['usage_file'] = str(dot_dir / '..' / second_file.name)
    request['packages'] = [second]
    code, _ = cli(tmp_path, 'collect', request, '--state-dir', state)
    assert code == 0
    ledger = c.load_json(state / '插件问题收件.json')
    assert len(next(iter(ledger['issues'].values()))['sources']) == 2
    assert len(list((state / '隔离维护演练' / '问题登记').rglob('*.md'))) == 1
