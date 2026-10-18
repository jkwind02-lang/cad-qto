"""T03/T41: synthetic identity, conservative migration and genuine browser workflow."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(Path(__file__).parent))
import review_comparison_fixture as fixture
from qto_review_contract import normalize_view, empty_feedback, validate_feedback
from qto_review_identity import snapshot, assess_record, recommendation_problem, handoff_actions
import build_qto_review_html as builder


def workflow_view():
    v = fixture.view()
    keys = [o['catalog_key'] for o in v['objects']]
    v['groups'] = [{'id': 'G01', 'title': '同一方法的图面组', 'category': '方法', 'proposal': '保留方案暂估', 'reason': '合成图面依据', 'status': '待核', 'member_keys': keys}]
    def decision(ident, refs, scope=None, **extra):
        members = scope or keys
        return {'id': ident, 'revision': '1', 'title': '合成事项 ' + ident, 'question': '是否保留方案暂估，待图纸补齐后复算？', 'kind': 'business_choice', 'is_common': True,
                'scope': {'member_keys': members, 'excluded_member_keys': [], 'basis': '仅合成平面水平长度，不含竖向及附属量'},
                'refs': refs, 'facts': ['仅三条合成回路'], 'gaps': ['缺少终点布置资料'],
                'impacts': [{'unit': 'm', 'value': None, 'level': '方案暂估量', 'scope': '水平段'}],
                'business_options': [{'id': 'estimate', 'title': '保留方案暂估'}, {'id': 'hold', 'title': '补资料后计算'}],
                'evidence': [{'id': 'E1', 'source': '合成平面 P1', 'detail': '合成对照依据', 'state': 'verified'}],
                'recommendation': {'id': 'R-' + ident, 'version': '1', 'text': '保留方案暂估，补资料后复算', 'reason': '已核水平段，终点位置缺资料', 'assistant_source': '合成助手建议；不是用户原话', 'evidence_ids': ['E1'], 'business_option_id': 'estimate', 'scope_member_keys': members}, **extra}
    main = decision('M1', [{'kind': 'group', 'id': 'G01'}, {'kind': 'item', 'id': 'I1'}, {'kind': 'issue', 'id': 'Q1'}])
    exception = decision('X1', [{'kind': 'object', 'id': keys[2]}], [keys[2]], exception_of='M1', is_common=False)
    gap = decision('GAP', [{'kind': 'item', 'id': 'I5'}], [keys[0]], kind='data_gap')
    no_evidence = decision('NOE', [{'kind': 'item', 'id': 'I2'}])
    no_evidence['recommendation']['evidence_ids'] = ['不存在的依据']
    expired = decision('OLD', [{'kind': 'item', 'id': 'I3'}])
    expired['recommendation']['expires_at'] = '2000-01-01T00:00:00Z'
    added = decision('NEW', [{'kind': 'item', 'id': 'I4'}])
    added['recommendation']['scope_member_keys'] = keys[:2]
    v['review_workflow'] = {'schema': 'qto-review-workflow/1.0', 'enabled': True, 'migrate_legacy': True, 'default_tab': 'decisions', 'decisions': [main, exception, gap, no_evidence, expired, added]}
    return v


def matter(v, ident='M1'):
    return next(d for d in v['review_workflow']['decisions'] if d['id'] == ident)


def manual(v, ident='M1', action='调整处理'):
    d = matter(v, ident)
    return {'decision_snapshot': snapshot(d), 'action': action, 'business_option_id': 'hold', 'confirmed_result': '补图后计算', 'reason': '需核实终点', 'user_note': '需核实终点', 'exception_of': d.get('exception_of'), 'excluded_member_keys': [], 'updated_at': '2026-10-18T00:00:00Z', 'history': []}


def js_merge(local, incoming, tmp_path):
    source, output = tmp_path / '合并输入.json', tmp_path / '合并输出.json'
    source.write_text(json.dumps({'local': local, 'incoming': incoming}, ensure_ascii=False), encoding='utf-8')
    script = "const fs=require('fs'),vm=require('vm'),path=require('path');for(const n of ['review-identity.js','review-core.js'])vm.runInThisContext(fs.readFileSync(path.join(process.argv[1],n),'utf8'));const d=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));fs.writeFileSync(process.argv[3],JSON.stringify(QtoReviewCore.mergePackages(d.local,d.incoming)));"
    p = subprocess.run(['node', '-e', script, str(ROOT / 'skills/_qto-shared-evidence/assets/review'), str(source), str(output)], capture_output=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(output.read_text(encoding='utf-8'))


@pytest.mark.parametrize('field', ['updated_at', 'entry_origin', 'history', 'recommendation_chosen_at'])
def test_same_business_opinion_metadata_deduplicates_and_retains_each_source(field, tmp_path):
    v = normalize_view(workflow_view())
    local = empty_feedback(v)
    r = manual(v)
    r.update(entry_origin='来源甲', recommendation_receipt={'chosen_at': '2020-01-01T00:00:00Z'})
    local['annotations']['审核事项|M1'] = r
    incoming = copy.deepcopy(local)
    changed = incoming['annotations']['审核事项|M1']
    if field == 'recommendation_chosen_at':
        changed['recommendation_receipt']['chosen_at'] = '2099-01-01T00:00:00Z'
    elif field == 'history':
        changed['history'] = [{**copy.deepcopy(r), 'entry_origin': '更早来源', 'updated_at': '2010-01-01T00:00:00Z'}]
    else:
        changed[field] = '2099-01-01T00:00:00Z' if field == 'updated_at' else '来源乙'
    merged = js_merge(local, incoming, tmp_path)
    out = merged['annotations']['审核事项|M1']
    assert not out.get('decision_conflict') and merged['merge_conflicts'] == 0
    assert out['updated_at'] == r['updated_at']  # local representative, not latest time
    assert out['history']
    verified, unmatched = validate_feedback(merged, v)
    assert not unmatched and len(handoff_actions(v, verified)) == 1
    repeated = js_merge(merged, incoming, tmp_path)
    assert repeated['annotations']['审核事项|M1']['history'] == out['history']
    assert not repeated['merge_conflicts']


@pytest.mark.parametrize('field,value', [('action', '退回核查'), ('business_option_id', 'estimate'), ('confirmed_result', '保留暂估'), ('reason', '新的不同依据'), ('user_note', '另一个业务要求')])
def test_genuinely_different_business_instructions_still_conflict(field, value, tmp_path):
    v = normalize_view(workflow_view())
    local = empty_feedback(v)
    local['annotations']['审核事项|M1'] = manual(v)
    incoming = copy.deepcopy(local)
    incoming['annotations']['审核事项|M1'][field] = value
    incoming['annotations']['审核事项|M1']['updated_at'] = '2099-01-01T00:00:00Z'
    merged = js_merge(local, incoming, tmp_path)
    r = merged['annotations']['审核事项|M1']
    assert merged['merge_conflicts'] == 1 and len(r['decision_conflict']['candidates']) == 2
    verified, _ = validate_feedback(merged, v)
    assert verified['annotations']['审核事项|M1']['applicability_state'] == 'conflict'
    assert not handoff_actions(v, verified)


def test_previously_saved_metadata_only_conflict_recovers_without_losing_history():
    v = normalize_view(workflow_view())
    fb = empty_feedback(v)
    original, later = manual(v), manual(v)
    later.update(updated_at='2099-01-01T00:00:00Z', entry_origin='另一个导入来源')
    fb['annotations']['审核事项|M1'] = {**copy.deepcopy(original), 'decision_conflict': {'candidates': [original, later]}}
    checked, unmatched = validate_feedback(fb, v)
    r = checked['annotations']['审核事项|M1']
    assert r['applicability_state'] == 'current' and not unmatched and not r.get('decision_conflict')
    assert r['updated_at'] == original['updated_at']
    assert any(h.get('updated_at') == later['updated_at'] and h.get('entry_origin') == later['entry_origin'] for h in r['history'])
    assert len(handoff_actions(v, checked)) == 1


def legacy_pair(v):
    fb = empty_feedback(v)
    group = v['groups'][0]
    fb['annotations']['问题组|G01'] = {'group_id': 'G01', 'member_snapshot': copy.deepcopy(group['member_snapshot']), 'selected_object_keys': group['member_keys'], 'excluded_object_keys': [], 'object_ids': group['object_ids'], 'result_fingerprint': group['result_fingerprint'], 'instruction': '修改结果', 'scope': '本组勾选对象', 'confirmed_result': '反对暂估', 'reason': '补图后计算', 'original_message': '原话保留', 'history': [{'confirmed_result': '更早的意见', 'reason': '历史'}], 'updated_at': '2020-01-01T00:00:00Z'}
    issue = v['comparison']['issues'][0]
    fb['annotations']['问题|Q1'] = {'issue_snapshot': {'issue_id': 'Q1', 'result_fingerprint': issue['result_fingerprint']}, 'result_fingerprint': issue['result_fingerprint'], 'verdict': '同意', 'confirmed_result': '同意暂估', 'reason': '采用方案暂估', 'updated_at': '2030-01-01T00:00:00Z', 'history': []}
    fb['drafts']['问题|Q1'] = {**copy.deepcopy(fb['annotations']['问题|Q1']), 'confirmed_result': '未保存草稿'}
    fb['annotations']['外部未知身份'] = {'confirmed_result': '不得丢弃的未知旧意见', 'reason': '无法对应', 'history': []}
    return fb


def test_legacy_conflict_migration_preserves_all_raw_text_drafts_and_sources():
    v = normalize_view(workflow_view())
    raw = legacy_pair(v)
    fb, unmatched = validate_feedback(raw, v)
    assert list(fb['annotations']) == ['审核事项|M1']
    r = fb['annotations']['审核事项|M1']
    assert r['applicability_state'] == 'conflict'
    assert len(r['decision_conflict']['candidates']) == 2
    assert len(fb['legacy_review_records']) == 4
    assert fb['drafts']['审核事项|M1']['confirmed_result'] == '未保存草稿'
    assert fb['legacy_review_records'][0]['record'] == raw['annotations']['问题组|G01']
    assert fb['decision_migration_pending'][0]['annotation_key'] == '外部未知身份'
    assert handoff_actions(v, fb) == []
    assert validate_feedback(fb, v)[0] == fb
    assert any(x['state'] == 'conflict' for x in unmatched)


def test_same_bare_id_and_members_do_not_merge_or_invent_identity():
    raw = workflow_view()
    d = matter(raw)
    d['refs'] = [{'kind': 'group', 'id': 'G01'}]
    matter(raw, 'X1')['refs'] = []
    v = normalize_view(raw)
    fb, _ = validate_feedback(legacy_pair(v), v)
    assert len(fb['annotations']) == 1
    assert not fb['annotations']['审核事项|M1'].get('decision_conflict')
    assert len(fb['decision_migration_pending']) == 3
    assert fb['annotations']['审核事项|M1']['applicability_state'] == 'migration_required'


def test_recommendation_gates_scope_and_migration_without_relaxing_old_reason_rules():
    v = normalize_view(workflow_view())
    assert recommendation_problem(matter(v)) is None
    for ident in ('GAP', 'NOE', 'OLD', 'NEW'):
        assert recommendation_problem(matter(v, ident))
    candidate = copy.deepcopy(matter(v))
    candidate['evidence'][0]['state'] = 'candidate'
    assert '尚未核实' in recommendation_problem(candidate)
    prior = manual(v)
    prior['decision_snapshot']['scope']['member_keys'].pop()
    assert '范围' in recommendation_problem(matter(v), prior)
    assert recommendation_problem(matter(v), {'migration_state': 'review_required', 'decision_snapshot': snapshot(matter(v))})
    old = normalize_view(fixture.view())
    fb = empty_feedback(old)
    fb['annotations']['问题|Q1'] = {'issue_snapshot': {'issue_id': 'Q1'}, 'verdict': '同意', 'reason': '', 'confirmed_result': ''}
    with pytest.raises(ValueError, match='原因'):
        validate_feedback(fb, old)


def test_explicit_exception_and_waiting_are_not_quantity_approval():
    v = normalize_view(workflow_view())
    fb = empty_feedback(v)
    fb['annotations'] = {'审核事项|M1': manual(v), '审核事项|X1': manual(v, 'X1', '待资料确认')}
    fb, unmatched = validate_feedback(fb, v)
    assert not unmatched
    actions = handoff_actions(v, fb)
    assert len(actions) == 1
    assert matter(v, 'X1')['scope']['member_keys'][0] not in actions[0]['scope']['member_keys']
    assert actions[0]['scope']['held_by_exception'] == matter(v, 'X1')['scope']['member_keys']
    changed = workflow_view()
    matter(changed)['facts'].append('新增需确认事实')
    newer = normalize_view(changed)
    assert assess_record(fb['annotations']['审核事项|M1'], newer)[0] == 'stale'


def test_ambiguous_explicit_mapping_is_pending_and_bad_exception_is_rejected():
    v = workflow_view()
    matter(v, 'X1')['refs'].append({'kind': 'issue', 'id': 'Q1'})
    n = normalize_view(v)
    fb, _ = validate_feedback(legacy_pair(n), n)
    assert any(len(x['candidate_decision_ids']) == 2 for x in fb['decision_migration_pending'])
    matter(v, 'X1')['exception_of'] = '不存在'
    with pytest.raises(ValueError, match='原事项'):
        normalize_view(v)


def test_327_group_230_member_overlap_is_explicit_migration_not_member_merging():
    raw = workflow_view()
    raw['objects'] += [fixture.circuit(n, 0) for n in range(4, 328)]
    keys = [o['catalog_key'] for o in raw['objects']]
    raw['groups'][0]['member_keys'] = keys
    fixture.item(raw, 'I1')['detail_rows'][0]['member_keys'] = keys[:230]
    main = matter(raw)
    main['scope']['member_keys'] = keys
    main['recommendation']['scope_member_keys'] = keys
    v = normalize_view(raw)
    fb, _ = validate_feedback(legacy_pair(v), v)
    assert len(v['groups'][0]['member_keys']) == 327
    assert len(set(v['groups'][0]['member_keys']) & set(fixture.item(v, 'I1')['detail_rows'][0]['member_keys'])) == 230
    assert len(fb['annotations']['审核事项|M1']['decision_conflict']['candidates']) == 2
    assert not handoff_actions(v, fb)


def test_workflow_cannot_bypass_delivery_business_or_current_member_gates():
    import review_delivery_fixture as delivery
    v = delivery.view(decisions=False)
    d = copy.deepcopy(matter(workflow_view()))
    key = v['objects'][0]['catalog_key']
    d['scope']['member_keys'] = [key]
    d['recommendation']['scope_member_keys'] = [key]
    d['refs'] = [{'kind': 'object', 'id': key}]
    v['review_workflow'] = {'schema': 'qto-review-workflow/1.0', 'enabled': True, 'decisions': [d]}
    with pytest.raises(ValueError, match='只读门控'):
        normalize_view(v)


def test_related_unmigrated_or_ambiguous_records_hold_canonical_actions():
    raw = workflow_view()
    raw['review_workflow']['migrate_legacy'] = False
    v = normalize_view(raw)
    fb = legacy_pair(v)
    fb['annotations']['审核事项|M1'] = manual(v)
    checked, _ = validate_feedback(fb, v)
    assert checked['annotations']['审核事项|M1']['applicability_state'] == 'migration_required'
    assert not handoff_actions(v, checked)
    assert recommendation_problem(matter(v), checked['annotations']['审核事项|M1'])
    raw['review_workflow']['migrate_legacy'] = True
    matter(raw, 'X1')['refs'].append({'kind': 'issue', 'id': 'Q1'})
    v = normalize_view(raw)
    fb = legacy_pair(v)
    fb['annotations'] = {'问题|Q1': fb['annotations']['问题|Q1'], '审核事项|X1': manual(v, 'X1')}
    fb['drafts'] = {}
    checked, _ = validate_feedback(fb, v)
    assert checked['annotations']['审核事项|X1']['applicability_state'] == 'migration_required'
    assert not handoff_actions(v, checked)


def test_clarified_pending_mapping_migrates_archive_without_losing_original_mapping_history():
    raw = workflow_view()
    matter(raw)['refs'] = [{'kind': 'group', 'id': 'G01'}]
    v = normalize_view(raw)
    old, _ = validate_feedback(legacy_pair(v), v)
    assert any(x['annotation_key'] == '问题|Q1' for x in old['decision_migration_pending'])
    matter(raw)['refs'].append({'kind': 'issue', 'id': 'Q1'})
    updated = normalize_view(raw)
    migrated, _ = validate_feedback(old, updated)
    assert all(x['annotation_key'] != '问题|Q1' for x in migrated['decision_migration_pending'])
    assert migrated['drafts']['审核事项|M1']['confirmed_result'] == '未保存草稿'
    assert migrated['annotations']['审核事项|M1']['decision_conflict']
    assert any(a['annotation_key'] == '问题|Q1' and not a['candidate_decision_ids'] for a in migrated['legacy_review_records'])
    assert validate_feedback(migrated, updated)[0] == migrated


def pipeline_page(tmp_path):
    """Professional CSV -> neutral scene builder -> comparison builder -> offline HTML, no hand edited view."""
    import test_review_scene_mapping_20261016 as scene
    import test_review_comparison_tables_20261015 as comparison
    nroot = scene.neutral(tmp_path)
    mapping_path, m = scene.mapping(tmp_path)
    m['objects'][0]['constants']['catalog_key'] = '电气对象|{编号}'
    m['objects'][0]['attributes']['decision_id'] = '回路'
    d = copy.deepcopy(matter(workflow_view()))
    d.update(id='WL1', refs=[{'kind': 'object', 'id': '电气对象|F1'}])
    d['scope']['member_keys'] = ['电气对象|F1']
    d['recommendation']['scope_member_keys'] = ['电气对象|F1']
    w = {'schema': 'qto-review-workflow/1.0', 'enabled': True, 'migrate_legacy': True, 'default_tab': 'decisions', 'decisions': [d]}
    m['view']['review_workflow'] = w
    mapping_path.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    sceneout = tmp_path / 'CSV生成图面.json'
    code, _, err = scene.run('build_review_scenes_from_tables.py', '--mapping', mapping_path, '--neutral-root', nroot, '--output', sceneout)
    assert code == 0, err
    generated = json.loads(sceneout.read_text(encoding='utf-8'))
    assert next(o for o in generated['objects'] if o['id'] == 'F1')['decision_id'] == 'WL1'
    assert generated['review_workflow'] == w
    cmapping, cm = comparison.tables(tmp_path)
    cm['view']['review_workflow'] = copy.deepcopy(w)
    cm['view']['review_workflow']['decisions'][0]['refs'].append({'kind': 'item', 'id': 'I1'})
    cmapping.write_text(json.dumps(cm, ensure_ascii=False), encoding='utf-8')
    final = tmp_path / 'CSV生成工程量与事项.json'
    code, _, err = comparison.run('build_comparison_view_from_tables.py', '--mapping', cmapping, '--base-view', sceneout, '--output', final)
    assert code == 0, err
    html = tmp_path / 'CSV完整链路离线页.html'
    builder.build(final, html, feedback_url='')
    return html


def test_real_scene_and_comparison_builders_preserve_workflow_and_catalog_identity(tmp_path):
    assert pipeline_page(tmp_path).is_file()


def test_real_browser_identity_workflow(tmp_path):
    module = os.environ.get('CAD_QTO_PLAYWRIGHT_MODULE', 'playwright')
    probe = subprocess.run(['node', '-e', 'require(process.argv[1])', module], capture_output=True, stdin=subprocess.DEVNULL)
    if probe.returncode:
        pytest.skip('设置现有CAD_QTO_PLAYWRIGHT_MODULE后运行真实Edge验证')
    raw = workflow_view()
    v = normalize_view(raw)
    source, html = tmp_path / '合成审核事项输入.json', tmp_path / '合成审核事项页.html'
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    builder.build(source, html, feedback_url='')
    legacy = tmp_path / '合成旧冲突及草稿.json'
    legacy.write_text(json.dumps(legacy_pair(v), ensure_ascii=False), encoding='utf-8')
    oldsource, oldhtml = tmp_path / '旧模式输入.json', tmp_path / '旧模式页.html'
    oldsource.write_text(json.dumps(fixture.view(), ensure_ascii=False), encoding='utf-8')
    builder.build(oldsource, oldhtml, feedback_url='')
    pipedir = tmp_path / '完整生成链路'
    pipedir.mkdir()
    pipeline = pipeline_page(pipedir)
    import review_delivery_fixture as delivery
    dv = delivery.view(decisions=True)
    dd = copy.deepcopy(matter(workflow_view()))
    dd.update(id='DELIVER', refs=[{'kind': 'issue', 'id': 'D1'}, {'kind': 'delivery_item', 'id': 'M1'}, {'kind': 'object', 'id': 'PIPE'}])
    dd['scope']['member_keys'] = ['PIPE']
    dd['recommendation']['scope_member_keys'] = ['PIPE']
    dv['review_workflow'] = {'schema': 'qto-review-workflow/1.0', 'enabled': True, 'default_tab': 'decisions', 'decisions': [dd]}
    dsource, dhtml = tmp_path / '交付事项输入.json', tmp_path / '交付事项页.html'
    dsource.write_text(json.dumps(dv, ensure_ascii=False), encoding='utf-8')
    builder.build(dsource, dhtml, feedback_url='')
    qraw = copy.deepcopy(raw)
    qraw['review_workflow']['default_tab'] = 'quantity'
    qsource, qhtml = tmp_path / '默认工程量入口输入.json', tmp_path / '默认工程量入口页.html'
    qsource.write_text(json.dumps(qraw, ensure_ascii=False), encoding='utf-8')
    builder.build(qsource, qhtml, feedback_url='')
    praw = copy.deepcopy(raw)
    praw['review_workflow']['migrate_legacy'] = False
    psource, phtml = tmp_path / '未启用旧迁移输入.json', tmp_path / '未启用旧迁移页.html'
    psource.write_text(json.dumps(praw, ensure_ascii=False), encoding='utf-8')
    builder.build(psource, phtml, feedback_path=legacy, feedback_url='')
    result = subprocess.run(['node', str(ROOT / 'tests/run_review_identity_browser_20261018.cjs'), '--html', str(html), '--legacy', str(legacy), '--legacy-html', str(oldhtml), '--pipeline-html', str(pipeline), '--delivery-html', str(dhtml), '--quantity-html', str(qhtml), '--pending-html', str(phtml), '--report', str(tmp_path / '浏览器回执.json')], capture_output=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / '浏览器回执.json').read_text(encoding='utf-8'))
    exported = json.loads(Path(report['exported_feedback']).read_text(encoding='utf-8'))
    verified, _ = validate_feedback(exported, v)
    assert verified['annotations']['审核事项|M1']['applicability_state'] == 'conflict'
    adopted = next(c['actual'] for c in report['checks'] if c['name'].startswith('一键采用'))
    saved = empty_feedback(v)
    saved['annotations']['审核事项|M1'] = adopted
    assert validate_feedback(saved, v)[0]['annotations']['审核事项|M1']['applicability_state'] == 'current'
    saved['annotations']['审核事项|M1']['recommendation_receipt']['snapshot']['version'] = '篡改版本'
    assert validate_feedback(saved, v)[0]['annotations']['审核事项|M1']['applicability_state'] == 'stale'
    same_opinion = json.loads(Path(report['same_opinion_export']).read_text(encoding='utf-8'))
    current, unmatched = validate_feedback(same_opinion, v)
    assert not unmatched and current['annotations']['审核事项|M1']['applicability_state'] == 'current'
    assert len(handoff_actions(v, current)) == 1
    actual_conflict = json.loads(Path(report['real_conflict_export']).read_text(encoding='utf-8'))
    held, _ = validate_feedback(actual_conflict, v)
    assert held['annotations']['审核事项|M1']['applicability_state'] == 'conflict'
    assert not handoff_actions(v, held)
