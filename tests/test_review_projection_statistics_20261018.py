"""Invented, non-project projection/coverage counterexamples and controls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
import qto_review_contract as contract
from qto_review_delivery import canonical_delivery_data, normalize_items
from qto_review_statistics import build_coverage_check, build_comparison_statistics
from convert_qto_review_delivery import project_delivery, expr
import review_delivery_fixture as fixture


def item(iid, title, unit, quantity, original, main, reference=(), history=(), partial=False):
    scopes = ['WL1', 'WL2'] if unit == 'm' else ['泵房']
    return {'id': iid, 'title': title, 'unit': unit, 'quantity': quantity, 'original_quantity': original,
            'dimensions': {'building': '海岚仓库'}, 'scope': fixture.scope(*scopes),
            'coverage': {'state': 'partial' if partial else 'complete', 'computed_scope': scopes[:1] if partial else scopes,
                         'uncomputed_scope': scopes[1:] if partial else []},
            'current_row_ids': list(main), 'reference_row_ids': list(reference), 'historical_row_ids': list(history)}


def view(with_scene=True):
    v = fixture.view(with_scene)
    v['delivery']['items'] = [item('PIPE-ITEM', '一号支路', 'm', 12, 15, ['M1'], ['R1', 'R2'], ['H1'], True),
                              item('PUMP-ITEM', '泵组', '台', 2, None, ['M2'])]
    return v


def baseline(v, **extra):
    v['delivery']['coverage_baseline'] = {'state': 'explicit', 'source_refs': fixture.refs(), 'objects': [
        {'id': 'expected-pipe', 'title': '已证管段', 'item_ids': ['PIPE-ITEM'], 'member_keys': ['PIPE'],
         'row_ids': ['M1'], 'scope': ['WL1'], **extra},
        {'id': 'expected-pump', 'title': '泵组', 'item_ids': ['PUMP-ITEM'], 'member_keys': ['PUMP'],
         'row_ids': ['M2'], 'scope': ['泵房']}]}
    return v


def projection_case(with_scene=True):
    v = view(with_scene)
    d = v.pop('delivery')
    staged_items = []
    for i in d['items']:
        staged_items.append({**i, 'coverage_state': i['coverage']['state'], 'scope_hint': i['coverage']['computed_scope'][0],
                             'pending_scope': (i['coverage']['uncomputed_scope'] or [None])[0]})
    owner = {rid: i['id'] for i in d['items'] for name in ('current_row_ids', 'reference_row_ids', 'historical_row_ids') for rid in i[name]}
    staged_rows = []
    for r in d['quantity_rows']:
        staged_rows.append({**r, 'item_id': owner[r['id']], 'scope_hint': (r['coverage']['computed_scope'] or r['scope']['included'])[0],
                            'coverage_state': r['coverage']['state'], 'pending_scope': (r['coverage']['uncomputed_scope'] or [None])[0],
                            'reference_relationship': (r.get('reference') or {}).get('relationship'),
                            'reference_current_row_ids': (r.get('reference') or {}).get('current_row_ids'),
                            'reference_prerequisites': (r.get('reference') or {}).get('prerequisites')})
    v['prepared'] = {'items': staged_items, 'rows': staged_rows, 'missing': d['missing_groups'], 'retired': d['retired_member_keys']}
    path = lambda field: {'$path': 'value.' + field}
    mapping = {'schema': 'qto-review-delivery-projection/1.0', 'assistant_work_complete': True,
               'items': {'path': 'prepared.items', 'fields': {k: path(k) for k in ('id','title','unit','quantity','original_quantity','dimensions','coverage_state','scope_hint','pending_scope')}},
               'row_sources': [{'path': 'prepared.rows', 'fields': {k: path(k) for k in ('id','item_id','title','unit','quantity','state','basis','member_keys','evidence_member_keys','evidence','scope','coverage','scope_hint','coverage_state','pending_scope','reference_relationship','reference_current_row_ids','reference_prerequisites','history_reason')}}],
               'missing_groups': {'path': 'prepared.missing', 'fields': {k: path(k) for k in ('id','title','scope','verified_facts','minimum_required','children')}},
               'retired_member_keys': {'$path':'root.prepared.retired'}}
    # Optional fields on heterogeneous records remain explicit, rather than
    # failing projection or converting a missing value into zero.
    for spec in mapping['row_sources'][0]['fields'].values(): spec['default'] = None
    return v, mapping


def test_items_counts_units_and_unknown_coverage_are_persistent():
    v = contract.normalize_view(view())
    d = v['delivery']
    assert d['comparison_statistics']['total_items'] == 2
    assert [x['unit'] for x in d['comparison_statistics']['by_unit']] == ['m', '台']
    assert d['comparison_statistics']['counts']['has_reference'] == 1
    assert d['comparison_statistics']['counts']['partial'] == 1
    assert d['coverage_check']['status'] == 'unknown'
    assert contract.normalize_view(v) == v
    assert 'comparison_statistics' not in canonical_delivery_data(v)


def test_no_drawing_no_original_list_uses_same_generic_mapping(tmp_path):
    raw, mapping = projection_case(False)
    out = project_delivery(raw, mapping)
    d = contract.normalize_view(out)['delivery']
    assert d['items'][1]['original_quantity'] is None
    assert d['quantity_rows'][3]['quantity'] is None
    assert d['current_member_keys'] == ['PIPE', 'PUMP']
    assert d['retired_member_keys'] == ['OLD']
    assert out['prepared'] == raw['prepared']
    assert out['sources'] == raw['sources']


def test_formal_projection_cli_writes_shared_standard_input(tmp_path):
    raw, mapping = projection_case()
    source, mp, output = [tmp_path / n for n in ('source.json', 'mapping.json', 'delivery.json')]
    source.write_text(json.dumps(raw, ensure_ascii=False), 'utf-8')
    mp.write_text(json.dumps(mapping, ensure_ascii=False), 'utf-8')
    result = subprocess.run([sys.executable, '-B', str(SCRIPTS/'convert_qto_review_delivery.py'), '--input', str(source), '--mapping', str(mp), '--output', str(output)], capture_output=True, text=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    loaded = json.loads(output.read_text('utf-8'))
    assert loaded['delivery_projection']['input_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(loaded['delivery']['items']) == 2
    # Real shared HTML generator consumes the output; no project JS or patches.
    html = tmp_path / 'shared.html'
    generated = subprocess.run([sys.executable, '-B', str(SCRIPTS/'build_qto_review_html.py'), '--input', str(output), '--output', str(html), '--local-only'], capture_output=True, text=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert generated.returncode == 0, generated.stderr
    assert contract.extract_json_file(html)['delivery_snapshot']['delivery'] == loaded['delivery']


@pytest.mark.parametrize('change,match', [
    (lambda d: d.update(items=[]), '全部主量'),
    (lambda d: d['items'][0].update(quantity=0), '当前量'),
    (lambda d: d['items'][0].update(quantity=None), '当前量'),
    (lambda d: d['items'][0].update(unit='台'), '单位'),
    (lambda d: d['items'][0].update(current_row_ids=['M2']), '单位'),
])
def test_items_reject_missing_rows_and_unknown_zero_substitution(change, match):
    v = view(); change(v['delivery'])
    with pytest.raises(ValueError, match=match): contract.normalize_view(v)


def test_coverage_agreement_does_not_prove_no_omission():
    v = baseline(view())
    v['delivery']['coverage_baseline']['objects'].append({'id':'not-drawn-in-result','item_ids':['PUMP-ITEM'], 'member_keys':['UNADOPTED'], 'row_ids':[], 'scope':['泵房']})
    d = contract.normalize_view(v)['delivery']
    assert d['coverage_check']['status'] == 'attention'
    codes = {f['code'] for f in d['coverage_check']['findings']}
    assert {'uncovered_object','no_evidence','scope_uncovered'} <= codes


def test_coverage_many_to_one_requires_explicit_aggregation_basis():
    v = baseline(view())
    b = v['delivery']['coverage_baseline']['objects']
    b.append({**b[1], 'id': 'another-physical-pump'})
    result = contract.normalize_view(v)['delivery']['coverage_check']
    assert 'many_to_one' in result['counts']
    for e in (b[1], b[2]): e.update(relationship='aggregate', basis='两对象在同一设备组明细，按实例逐项列明')
    result = contract.normalize_view(v)['delivery']['coverage_check']
    assert 'many_to_one' not in result['counts']


def test_scope_uncovered_uses_baseline_scope_and_not_delta():
    v = baseline(view())
    v['delivery']['coverage_baseline']['objects'][1]['scope'].append('屋面设备区')
    result = contract.normalize_view(v)['delivery']['coverage_check']
    assert 'scope_uncovered' in result['counts']


def test_coverage_cannot_use_another_items_rows_even_at_the_same_scope():
    v = baseline(view()); d = v['delivery']
    d['quantity_rows'] = [r for r in d['quantity_rows'] if r['id'] not in ('R1','R2')]
    d['items'][0]['reference_row_ids']=[]
    for row in d['quantity_rows']:
        if row['state']=='current':
            row['scope']=fixture.scope('共同区域')
            row['coverage']={'state':'complete','computed_scope':['共同区域'],'uncomputed_scope':[]}
    for i in d['items']:
        i['scope']=fixture.scope('共同区域')
        i['coverage']={'state':'complete','computed_scope':['共同区域'],'uncomputed_scope':[]}
    expected=d['coverage_baseline']['objects']
    for e in expected: e['scope']=['共同区域']
    expected[0].update(row_ids=['M2'],member_keys=['PUMP'])
    expected[1].update(row_ids=['M1'],member_keys=['PIPE'])
    out=contract.normalize_view(v)
    coverage=out['delivery']['coverage_check']
    assert coverage['status']=='attention'
    assert coverage['counts']['wrong_item_ownership']==2
    assert coverage['counts']['uncovered_object']==2
    assert all(i['status']=='attention' for i in coverage['items'])
    assert out['delivery']['items'][0]['quantity']==12 and out['delivery']['items'][1]['quantity']==2
    expected[0].update(row_ids=['M1'],member_keys=['PIPE'])
    expected[1].update(row_ids=['M2'],member_keys=['PUMP'])
    assert contract.normalize_view(v)['delivery']['coverage_check']['status']=='pass'


def test_excluded_object_requires_its_own_evidence():
    v = baseline(view(), excluded=True)
    with pytest.raises(ValueError, match='排除须有'): contract.normalize_view(v)
    obj = v['delivery']['coverage_baseline']['objects'][0]
    obj.update(basis='已证为本期范围外独立对象', source_refs=fixture.refs())
    assert 'explicit_exclusion' in contract.normalize_view(v)['delivery']['coverage_check']['counts']


def test_numeric_comparison_statistics_slices_are_qualified_and_overlap():
    v = view()
    v['comparison'] = {'items': [
        {'id':'PIPE-ITEM', 'kind':'third_party', 'third_parties':[{'source_id':'LIST','unit':'m','comparison_state':'not_comparable', 'comparability':{'reason':'只有水平局部，原量为全项'}, 'difference':{'value':999,'rate':999}}]},
        {'id':'PUMP-ITEM', 'kind':'third_party', 'third_parties':[{'source_id':'LIST','unit':'台','comparison_state':'comparable', 'comparability':{'reason':'同对象同范围同规格完整'}, 'difference':{'value':0,'rate':0},'conclusion':'一致'}]}]}
    result = build_comparison_statistics(v, v['delivery'])
    assert result['items'][0]['delta'] is None
    assert result['items'][1]['delta'] == 0
    assert result['counts']['consistent'] == 1
    assert result['counts']['has_reference'] == 1
    assert result['overlapping_slices'] is True
    assert all('quantity_sum' not in u for u in result['by_unit'])


@pytest.mark.parametrize('presence,state,reason', [(True,'original_unknown','原量未填'),(False,'no_original_row','无原清单对应行'),(None,'unknown','存在性未声明')])
def test_original_row_presence_is_not_guessed_from_null(presence,state,reason):
    v=view(); i=v['delivery']['items'][1]
    assert i['original_quantity'] is None
    if presence is not None: i['original_row_present']=presence
    s=contract.normalize_view(v)['delivery']['comparison_statistics']['items'][1]
    assert s['comparison_state']==state and reason in s['reason']
    assert s['delta'] is None and s['rate'] is None
    assert ('extra' in s['tags']) == (presence is False)


def test_formal_comparison_cannot_override_delivery_partial_scope():
    v = view(); d = v['delivery']
    v['comparison'] = {'items':[{'id':'PIPE-ITEM','ours':{'unit':'m','total':12}, 'third_parties':[
        {'source_id':'LIST','unit':'m','quantity':15,'comparison_state':'comparable','difference':{'value':-3,'rate':-.2}}]}]}
    with pytest.raises(ValueError, match='局部/未知覆盖冲突'):
        normalize_items(v,d,{r['id']:r for r in d['quantity_rows']})


def test_scope_only_claim_cannot_bypass_resolved_reference_validation():
    v = view(); r = v['delivery']['quantity_rows'][2]
    r['quantity'] = 1
    with pytest.raises(ValueError, match='小于主量'): contract.normalize_view(v)
    r['reference'].update(association_state='scope_only', current_row_ids=[], scope_statement='源方案局部范围', association_reason='原记录关系声明；逐明细包含范围未证，不得相加')
    with pytest.raises(ValueError, match='仅范围包含'): contract.normalize_view(v)
    r['reference'].update(relationship='unknown', source_claimed_relationship='includes_current')
    assert contract.normalize_view(v)['delivery']['quantity_rows'][2]['reference']['relationship'] == 'unknown'


def containment_view(with_items=True, overlapping=False, repeated_member=False):
    v = view(); d = v['delivery']; main = d['quantity_rows'][0]
    main['scope'] = fixture.scope('WL1')
    main['coverage'] = {'state':'complete','computed_scope':['WL1'],'uncomputed_scope':[]}
    other = copy.deepcopy(main); other['id'] = 'M3'
    token = 'WL1' if overlapping else 'WL2'
    other['scope'] = fixture.scope(token)
    other['coverage'] = {'state':'complete','computed_scope':[token],'uncomputed_scope':[]}
    if not repeated_member:
        obj = copy.deepcopy(v['objects'][0]); obj.update(id='PIPE2',catalog_key='PIPE2')
        v['objects'].append(obj); d['current_member_keys'].append('PIPE2'); other['member_keys']=['PIPE2']
    d['quantity_rows'].append(other)
    d['quantity_rows'][2]['reference']['current_row_ids']=['M1','M3']
    d['items'][0].update(quantity=24,current_row_ids=['M1','M3'])
    d['items'][0]['coverage']={'state':'complete','computed_scope':['WL1','WL2'],'uncomputed_scope':[]}
    if not with_items: d.pop('items')
    return v


def test_reference_including_distinct_adopted_rows_checks_the_combined_quantity():
    for with_items in (False, True):
        v = containment_view(with_items)
        with pytest.raises(ValueError, match='合计下限'): contract.normalize_view(v)
        v['delivery']['quantity_rows'][2]['quantity']=24
        assert contract.normalize_view(v)['delivery']['quantity_rows'][2]['quantity']==24


def test_reference_known_single_quantity_outlet_is_checked_even_if_scopes_overlap():
    v = containment_view(True, overlapping=True)
    with pytest.raises(ValueError, match='合计下限'): contract.normalize_view(v)
    v['delivery']['quantity_rows'][2]['quantity']=24
    assert contract.normalize_view(v)['delivery']['items'][0]['quantity']==24


def test_reference_overlapping_or_repeated_members_without_outlet_are_not_blindly_added():
    for overlapping, repeated in ((True,False),(False,True),(True,True)):
        v = containment_view(False, overlapping, repeated)
        assert contract.normalize_view(v)['delivery']['quantity_rows'][2]['quantity']==20
    v = containment_view(False)
    ref = v['delivery']['quantity_rows'][2]['reference']
    ref.update(relationship='unknown',association_state='scope_only',source_claimed_relationship='includes_current',
               current_row_ids=[],scope_statement='仅有方案范围声明',association_reason='逐明细包含范围未证，不得相加')
    assert contract.normalize_view(v)['delivery']['quantity_rows'][2]['quantity']==20


def test_coverage_result_fingerprint_is_stable_across_hash_seeds(tmp_path):
    v=containment_view();v['delivery']['quantity_rows'][2]['quantity']=24
    baseline(v)
    e=v['delivery']['coverage_baseline']['objects'][0]
    e.update(row_ids=['M3','M1'],member_keys=['PIPE2','PIPE'],scope=['WL1','WL2'])
    source=tmp_path/'coverage.json';source.write_text(json.dumps(v,ensure_ascii=False),'utf-8')
    code='import sys,json;sys.path.insert(0,'+repr(str(SCRIPTS))+');from qto_review_contract import normalize_view;from pathlib import Path;print(normalize_view(json.loads(Path(sys.argv[1]).read_text("utf-8")))["result_fingerprint"])'
    fingerprints=[]
    for seed in ('1','2'):
        r=subprocess.run([sys.executable,'-B','-c',code,str(source)],env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,encoding='utf-8',stdin=subprocess.DEVNULL)
        assert r.returncode==0,r.stderr
        fingerprints.append(r.stdout.strip())
    assert fingerprints[0]==fingerprints[1]


def test_recorded_adoption_requires_original_explicit_role_and_preserves_identity():
    raw, mapping = projection_case()
    mapping['recorded_adoption'] = {'role_path':'adoption_state','current_roles':['adopted']}
    mapping['row_sources'][0]['fields'].pop('evidence')
    with pytest.raises(ValueError, match='未明确声明当前'): project_delivery(raw, mapping)
    for r in raw['prepared']['rows']:
        if r['state'] == 'current': r['adoption_state'] = 'adopted'
    out = contract.normalize_view(project_delivery(raw, mapping))
    assert out['sources'] == raw['sources']
    original = copy.deepcopy(raw); original['delivery'] = fixture.view()['delivery']
    # Identity is sources/project/review only; delivery changes result identity.
    assert out['source_sha256'] == contract.identity(raw)
    e = out['delivery']['quantity_rows'][0]['evidence']
    assert e['method'] == 'adopted_record' and e['input_facts'] == []
    assert e['verification_scope'] == 'frozen_record_identity_only'
    assert '本次未重新量算' in e['notice']
    out['delivery']['projection_provenance']['notice'] = '本次已正式核准'
    e['notice'] = '本次已核实全部几何'
    rebound = contract.normalize_view(out)
    assert '本次未重新量算' in rebound['delivery']['quantity_rows'][0]['evidence']['notice']
    assert '本次未重新量算' in rebound['delivery']['projection_provenance']['notice']
    assert e['provenance']['record_locator'].startswith('/prepared/rows/')
    e['provenance']['input_sha256'] = '0'*64
    with pytest.raises(ValueError, match='输入身份'): contract.normalize_view(out)


def test_rule_method_approval_does_not_prove_input_facts():
    raw, mapping = projection_case()
    main = raw['prepared']['rows'][0]
    main['evidence']['input_facts'][0]['state'] = 'pending'
    with pytest.raises(ValueError, match='输入尚未证实'): contract.normalize_view(project_delivery(raw, mapping))


def test_empty_items_only_valid_with_empty_quantity_rows():
    v = view(); d = v['delivery']; d['items'] = []; d['quantity_rows'] = []; d['current_member_keys'] = []
    d['source_evidence_member_keys'] = ['PIPE','PUMP','SOURCE']
    assert contract.normalize_view(v)['delivery']['comparison_statistics']['total_items'] == 0


def test_expression_lookup_preserves_null_and_does_not_execute_code():
    c = {'root': {'rows':{'I':{'quantity':None,'name':'中文'}}}, 'value':{}, 'parent':{}, 'key':'I'}
    spec = {'$lookup':{'collection':{'$path':'root.rows'},'key':{'$path':'key'},'path':'quantity','default':0}}
    assert expr(spec,c) is None
    assert expr({'$literal':'$(never executes)'},c) == '$(never executes)'


def historical_exit_view(period='historical', current=None):
    v = view(); v['schema'] = 'qto-review-view/1.1'
    detail = {'id':'HX','level':'确认量','quantity':5,'unit':'m','basis':'旧方案量完整留存','member_keys':[],
              'excluded':{'rule_ref':'R-HISTORY','note':'原方案已退出，本期尚无依据','period':period}}
    v['comparison'] = {'dimensions':[], 'sources':[], 'issues':[], 'items':[
        {'id':'HIST','kind':'extra','name':'历史退出项','features':'合成','dimensions':{},
         'ours':{'unit':'m','levels':{'确认量':current,'规则推算量':None,'方案暂估量':None,'未决量':None}}, 'detail_rows':[detail], 'third_parties':[]}]}
    h = {'id':'HX','title':'旧方案完整量','unit':'m','quantity':5,'state':'historical','basis':'旧量保留',
         'scope':fixture.scope('旧方案范围'),'coverage':{'state':'complete','computed_scope':['旧方案范围'],'uncomputed_scope':[]},
         'member_keys':[],'evidence_member_keys':[],'history_reason':'退出不代表本期已证排除为零','source_records':[copy.deepcopy(detail)]}
    v['delivery']['quantity_rows'].append(h)
    i = {'id':'HIST','title':'历史退出项','unit':'m','quantity':current,'original_quantity':None,'dimensions':{},
         'scope':fixture.scope('本期范围'),'coverage':{'state':'unknown','computed_scope':[],'uncomputed_scope':['本期范围']},
         'current_row_ids':[],'reference_row_ids':[],'historical_row_ids':['HX']}
    if current == 0:
        i['current_zero_evidence'] = {'source_refs':fixture.refs(),'reason':'独立本期依据已明确排除此范围，非历史退出推零'}
        i['coverage'] = {'state':'complete','computed_scope':['本期范围'],'uncomputed_scope':[]}
    v['delivery']['items'].append(i)
    return v


def test_historical_exit_preserves_null_and_all_original_fields():
    v = historical_exit_view(); old = copy.deepcopy(v['comparison']['items'][0]['detail_rows'])
    out = contract.normalize_view(v)
    assert out['comparison']['items'][0]['ours']['total'] is None
    assert out['delivery']['items'][-1]['quantity'] is None
    assert out['delivery']['quantity_rows'][-1]['source_records'][0] == old[0]


def test_current_period_explicit_exclusion_still_requires_and_accepts_zero():
    with pytest.raises(ValueError, match='确认量须写 0'): contract.normalize_view(historical_exit_view('current'))
    out = contract.normalize_view(historical_exit_view('current', 0))
    assert out['comparison']['items'][0]['ours']['total'] == 0
    assert out['delivery']['items'][-1]['quantity'] == 0


def test_historical_period_requires_formal_history_and_legacy_rules_are_unchanged():
    v = historical_exit_view(); v['delivery']['quantity_rows'][-1]['state'] = 'reference'
    with pytest.raises(ValueError, match='须完整保留'): contract.normalize_view(v)
    v = historical_exit_view(); v.pop('delivery')
    with pytest.raises(ValueError, match='旧格式不改变'): contract.normalize_view(v)


def test_frozen_adoption_migration_keeps_old_feedback_as_history():
    old = contract.normalize_view(view())
    feedback = contract.empty_feedback(old)
    feedback['custom_markers'] = [{'id':'old-marker','catalog_key':'旧标记|1','scene_id':'removed-page','points':[[1,2]],'professional':'qto-pipe-networks'}]
    feedback['annotations']['old-group'] = {'group_id':'removed-group', 'member_snapshot':[{'catalog_key':'OLD','object_id':'OLD'}],
        'selected_object_keys':['OLD'],'excluded_object_keys':[],'object_ids':['OLD'],'confirmed_result':'旧量888 m','reason':'历史方案', 'result_fingerprint':'f'*64}
    raw, mapping = projection_case()
    mapping['recorded_adoption'] = {'role_path':'adoption_state','current_roles':['adopted']}
    mapping['row_sources'][0]['fields'].pop('evidence')
    for r in raw['prepared']['rows']:
        if r['state'] == 'current': r['adoption_state'] = 'adopted'
    now = contract.normalize_view(project_delivery(raw,mapping))
    assert now['source_sha256'] == old['source_sha256']
    checked, unmatched = contract.validate_feedback(feedback,now)
    assert checked['annotations']['old-group']['applicability_state'] == 'unmatched'
    assert checked['annotations']['old-group']['confirmed_result'] == '旧量888 m'
    assert checked['custom_markers'] == feedback['custom_markers']
