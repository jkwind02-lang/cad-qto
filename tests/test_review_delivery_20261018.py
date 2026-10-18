"""Delivery contract, actual shared builder and offline feedback round-trip (synthetic only)."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).parent))
import qto_review_contract as contract
import build_qto_review_html as builder
from qto_review_revision import assess_record
from qto_review_delivery import delivery_allows_target
import review_delivery_fixture as fixture


def norm(v=None):
    return contract.normalize_view(v or fixture.view())


def test_current_reference_units_null_and_roles():
    v = norm(); d = v['delivery']
    assert d['generation_check']['current_rows'] == 2
    assert d['generation_check']['business_choices'] == 0
    assert [r['quantity'] for r in d['quantity_rows']] == [12, 2, 20, None, 888]
    assert d['quantity_rows'][0]['coverage']['state'] == 'partial'
    assert d['quantity_rows'][2]['reference']['addition'] == 'do_not_add'
    assert v['summary'] == []  # No mixed-unit or reference-inclusive total is invented.
    assert contract.normalize_view(v) == v


@pytest.mark.parametrize('change,match', [
    (lambda d: d.update(assistant_work_complete=False), '助手仍可继续'),
    (lambda d: d['current_member_keys'].append('SOURCE'), '不得重叠'),
    (lambda d: d['current_member_keys'].remove('PIPE'), '全部对象'),
    (lambda d: d['quantity_rows'][0].update(quantity=None), '未知不能改成零'),
    (lambda d: d['quantity_rows'][3].pop('quantity'), '显式写null'),
    (lambda d: d['quantity_rows'][0].update(quantity=True), '数量须为'),
    (lambda d: d['quantity_rows'][0]['evidence'].update(state='pending'), '主量须有'),
    (lambda d: d['quantity_rows'][0]['evidence'].update(input_facts=[]), '规则已批'),
    (lambda d: d['quantity_rows'][0]['evidence']['input_facts'][0].update(state='pending'), '规则输入尚未'),
    (lambda d: d['quantity_rows'][0]['evidence']['source_refs'][0].update(source_id='不存在'), 'source_id'),
    (lambda d: d['quantity_rows'][0].update(member_keys=['SOURCE']), '仅来源证据定位'),
    (lambda d: d['quantity_rows'][0].update(evidence_member_keys=['OLD']), '退役成员'),
    (lambda d: d['quantity_rows'][0]['coverage'].update(state='complete'), '完整/局部'),
    (lambda d: d['quantity_rows'][0]['coverage'].update(computed_scope=['WL1', 'WL2']), '互斥'),
    (lambda d: d['quantity_rows'][2]['reference'].update(addition='add'), '不得默认'),
    (lambda d: d['quantity_rows'][2]['reference'].update(relationship=''), '包含同范围'),
    (lambda d: d['quantity_rows'][2]['reference'].update(current_row_ids=['M2']), '不同单位'),
    (lambda d: d['quantity_rows'][2].update(quantity=1), '小于主量'),
    (lambda d: d['missing_groups'][0]['children'][0].pop('scope'), '显式填写'),
    (lambda d: d['missing_groups'][0]['children'][0]['scope'].update(included=['室外管网']), '超出父项'),
    (lambda d: d['missing_groups'][0]['children'][0]['minimum_required'][0].update(provider=''), '提供方'),
    (lambda d: d['missing_groups'][0]['children'][0]['minimum_required'][0].update(recompute_action=''), '回算动作'),
    (lambda d: d.update(mode='decisions'), '真实业务取舍'),
    (lambda d: d.update(mode='decisions', decision_targets=[{'kind':'issue','id':'不存在','reason':'合成'}]), 'handler=decide'),
])
def test_reject_unproved_or_misleading_delivery(change, match):
    v = fixture.view(); change(v['delivery'])
    with pytest.raises(ValueError, match=match):
        norm(v)


def test_explicit_zero_is_allowed_but_never_inferred():
    v = fixture.view(); v['delivery']['quantity_rows'][1]['quantity'] = 0
    assert norm(v)['delivery']['quantity_rows'][1]['quantity'] == 0
    assert norm(v)['delivery']['quantity_rows'][3]['quantity'] is None


def test_no_drawing_no_list_or_object_shell_required():
    v = fixture.view(False); v['objects'] = []
    for name in ('current_member_keys', 'source_evidence_member_keys', 'retired_member_keys'): v['delivery'][name] = []
    for row in v['delivery']['quantity_rows']:
        row['member_keys'] = []; row['evidence_member_keys'] = []
    normalized = norm(v)
    assert normalized['scenes'] == [] and normalized['objects'] == []
    assert 'comparison' not in normalized


def test_legacy_inputs_keep_identical_fingerprints():
    # Compute the old view fingerprint with the exact pre-extension algorithm via a checked fixture.
    for schema in ('qto-review-view/1.0', 'qto-review-view/1.1'):
        v = fixture.view(); v.pop('delivery'); v['schema'] = schema
        if schema.endswith('1.1'): v['comparison'] = {}
        a = norm(v)
        assert 'delivery' not in a and 'delivery_snapshot' not in contract.empty_feedback(a)
        assert norm(a)['result_fingerprint'] == a['result_fingerprint']
        expected = {'qto-review-view/1.0': '68ff09e4d000535595ac29826ca4fc7c8e992b58d86e770736e147cfb468ad79',
                    'qto-review-view/1.1': '28f6a18c9d476a3dcb040f9d6ff56b0e871a08a38847f9bc21fb5e5b73392590'}
        # Pinned by the e9e5f57 pre-delivery normalizer; all normalized fields were also compared.
        assert a['result_fingerprint'] == expected[schema]


def test_delivery_semantics_bind_fingerprints_without_changing_source_identity():
    a = norm(fixture.view(decisions=True)); b = copy.deepcopy(a)
    b['delivery']['missing_groups'][0]['children'][0]['minimum_required'][0]['impact'] += '（修订）'
    b = norm(b)
    assert a['source_sha256'] == b['source_sha256']
    assert a['result_fingerprint'] != b['result_fingerprint']
    assert a['objects'][0]['result_fingerprint'] != b['objects'][0]['result_fingerprint']
    assert a['comparison']['issues'][0]['result_fingerprint'] != b['comparison']['issues'][0]['result_fingerprint']


def issue_record(v):
    issue = v['comparison']['issues'][0]
    return {'annotation_id': '问题|D1', 'issue_snapshot': {'issue_id': 'D1', 'result_fingerprint': issue['result_fingerprint']},
            'result_fingerprint': issue['result_fingerprint'], 'verdict': '同意', 'confirmed_result': '', 'reason': '业务明确选择方案甲',
            'excluded_detail_row_ids': [], 'updated_at': '2026-10-18T10:00:00Z', 'history': []}


def test_old_group_range_and_removed_scene_marker_remain_readable_history():
    v = fixture.view(False)
    v['groups'] = [{'id':'G','title':'当前组','category':'合成','proposal':'保持已证量','reason':'已证','status':'已核', 'member_keys':['PIPE','PUMP']}]
    v = norm(v); fb = contract.empty_feedback(v)
    fb['custom_markers'] = [{'id':'旧标记','catalog_key':'旧标记|1','scene_id':'旧图页','points':[[1,2]],'professional':'qto-pipe-networks'}]
    r = {'group_id':'G','member_snapshot':[{'catalog_key':'OLD','object_id':'OLD'}], 'selected_object_keys':['OLD'],
         'excluded_object_keys':[], 'object_ids':['OLD'], 'confirmed_result':'旧量888 m','reason':'历史方案，已退役','result_fingerprint':'f'*64}
    fb['annotations']['旧组|G'] = r
    checked, unmatched = contract.validate_feedback(fb,v)
    assert checked['annotations']['旧组|G']['applicability_state'] == 'unmatched'
    assert checked['custom_markers'] == fb['custom_markers']
    assert unmatched and checked['delivery_snapshot']['delivery']['current_member_keys'] == ['PIPE','PUMP']


def test_declared_common_choice_reuses_scope_and_exception_mechanism():
    v = fixture.view(decisions=True); issue = v['comparison']['issues'][0]
    v['comparison']['common_issues'] = [{'id':'C1','nature':'multi_method','question':'同类支路选择何种工艺','text':'两种工艺均可，需要业务选择',
        'handler':'decide','options':copy.deepcopy(issue['options']),'current_option':'A','item_ids':[]}]
    issue['common_issue_id'] = 'C1'
    v['delivery']['decision_targets'] = [{'kind':'common','id':'C1','reason':'同类支路工艺选择，可保留个别特例'}]
    v = norm(v)
    assert delivery_allows_target(v,'common','C1')
    assert delivery_allows_target(v,'issue','D1')
    assert not delivery_allows_target(v,'issue','不存在')
    record = issue_record(v); record['exception_of'] = 'C1'
    fb = contract.empty_feedback(v); fb['annotations']['问题|D1'] = record
    checked, unmatched = contract.validate_feedback(fb,v)
    assert not unmatched and checked['annotations']['问题|D1']['applicability_state'] == 'current'


def test_adoption_boundary_cannot_be_overridden_by_free_text():
    v=fixture.view();v['delivery'].update(adoption_note='当前采用工程量已正式核准。',adoption_boundary='覆盖说明')
    d=norm(v)['delivery']
    assert d['adoption_boundary']=='当前采用仅指本轮工作采用，不代表工程正式核准。'
    assert d['generation_check']['current_is_formal_approval'] is False


def test_undeclared_common_dependency_is_rejected_before_generating_blocked_form():
    v=fixture.view(decisions=True);issue=v['comparison']['issues'][0]
    issue['common_issue_id']='C1'
    v['comparison']['common_issues']=[{'id':'C1','nature':'multi_method','question':'选择工艺','text':'两种均可',
        'handler':'decide','options':copy.deepcopy(issue['options']),'current_option':'A','item_ids':[]}]
    with pytest.raises(ValueError,match='须显式声明该共性取舍'):norm(v)
    v['delivery']['decision_targets'].append({'kind':'common','id':'C1','reason':'同类支路需统一选择工艺'})
    assert delivery_allows_target(norm(v),'issue','D1')


def test_true_business_decision_feedback_and_retired_history(tmp_path):
    v = norm(fixture.view(decisions=True)); r = issue_record(v)
    fb = contract.empty_feedback(v); fb['annotations']['问题|D1'] = r
    validated, unmatched = contract.validate_feedback(fb, v)
    assert not unmatched and validated['annotations']['问题|D1']['applicability_state'] == 'current'
    changed = copy.deepcopy(v); changed['delivery']['quantity_rows'][0]['basis'] += '修订'; changed = norm(changed)
    stale, _ = contract.validate_feedback(fb, changed)
    assert stale['annotations']['问题|D1']['applicability_state'] == 'stale'
    assert len(stale['historical_delivery_snapshots']) == 1
    assert stale['delivery_snapshot']['delivery'] == changed['delivery']
    old = v['objects'][-1]
    old_record = {'object_snapshot': {'catalog_key': old['catalog_key'], 'object_id': old['id'], 'result_fingerprint': old['result_fingerprint']}, 'result_fingerprint': old['result_fingerprint']}
    assert assess_record(old_record, v)[0] == 'unmatched'
    assert assess_record(r, norm())[0] == 'stale'


@pytest.mark.parametrize('with_scene,decisions', [(True, False), (False, False), (False, True)])
def test_real_shared_generator_offline_and_feedback_reader(tmp_path, with_scene, decisions):
    raw = fixture.view(with_scene, decisions)
    source = tmp_path / '合成输入.json'; source.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    output = tmp_path / '通用交付.html'
    report = builder.build(source, output, feedback_url='')
    assert report['delivery']['business_choices'] == int(decisions)
    html = output.read_text(encoding='utf-8')
    assert '__REVIEW_DELIVERY__' not in html and 'QtoReviewDelivery.attach' in html
    assert '<script src=' not in html and '<link rel="stylesheet" href=' not in html
    v = norm(raw); exported = contract.extract_json_file(output)
    assert exported['delivery_snapshot']['delivery'] == v['delivery']
    assert max(map(len, html.splitlines())) < 10000
    feedback = tmp_path / '保存意见.json'
    if decisions: exported['annotations']['问题|D1'] = issue_record(v)
    feedback.write_text(json.dumps(exported, ensure_ascii=False), encoding='utf-8')
    handoff = tmp_path / '交接.json'
    p = subprocess.run([sys.executable, str(SCRIPTS/'read_qto_review_feedback.py'), '--view', str(source), '--feedback', str(feedback), '--output', str(handoff)], capture_output=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    consumed = json.loads(handoff.read_text(encoding='utf-8'))
    assert consumed['delivery']['quantity_rows'][3]['quantity'] is None
    assert len(consumed['issue_actions']) == int(decisions) if decisions else consumed['actions'] == []
    result = subprocess.run(['node', str(ROOT/'tests/run_review_delivery_ui_tests.mjs'), '--html', str(output), '--view', str(source)], capture_output=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stdout + result.stderr


def test_large_delivery_keeps_all_data_and_paginates(tmp_path):
    raw = fixture.view(False)
    for n in range(125):
        r = copy.deepcopy(raw['delivery']['quantity_rows'][2]); r['id'] = 'MORE-'+str(n); r['title'] = '批量参考'+str(n)
        raw['delivery']['quantity_rows'].append(r)
    source = tmp_path/'大样本.json'; source.write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
    output = tmp_path/'大样本.html'; builder.build(source,output,feedback_url='')
    restored = contract.extract_json_file(output)['delivery_snapshot']['delivery']
    assert len(restored['quantity_rows']) == 130
    result = subprocess.run(['node',str(ROOT/'tests/run_review_delivery_ui_tests.mjs'),'--html',str(output),'--view',str(source)],capture_output=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert result.returncode == 0,result.stdout+result.stderr


def test_typed_business_route_avoids_cross_pool_id_collision(tmp_path):
    raw = fixture.view(False, True)
    raw['comparison']['items'] = [{'id':'D1','kind':'extra','name':'与业务问题同名的条目',
        'ours':{'unit':'m','levels':{'确认量':12,'规则推算量':None,'方案暂估量':None,'未决量':None}}, 'third_parties':[],
        'detail_rows':[{'id':'D1#1','level':'确认量','quantity':12,'unit':'m','basis':'合成已核记录'}]}]
    source=tmp_path/'同名输入.json';source.write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
    output=tmp_path/'同名交付.html';builder.build(source,output,feedback_url='')
    result=subprocess.run(['node',str(ROOT/'tests/run_review_delivery_ui_tests.mjs'),'--html',str(output),'--view',str(source)],capture_output=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert result.returncode==0,result.stdout+result.stderr
