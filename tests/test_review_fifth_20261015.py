"""R1 第五处修改：问题性质、共性问题、偏差归因、已定不计、答复继承与例外、表格输入新表。

契约：docs/维护/复核页审核视图契约.md 第 5.1、5.5、6、9.1、10、12 节（提交 e3812c4、d1ff558）。
每条规则配合成反例和合法对照；数据全部是合成的，不含项目工程量。
"""
import copy, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / '_qto-shared-evidence' / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qto_review_contract as c  # noqa: E402
from qto_review_comparison import ComparisonError, LIST_PREFIX  # noqa: E402
import review_comparison_fixture as fx  # noqa: E402


def norm(v):
    return c.normalize_view(v)


def problems(v):
    with pytest.raises(ComparisonError) as e:
        norm(v)
    return e.value.problems


def checks_of(v):
    return {p['check'] for p in problems(v)}


def items_of(n):
    return {i['id']: i for i in n['comparison']['items']}


def commons_of(n):
    return {g['id']: g for g in n['comparison']['common_issues']}


def kinds(n):
    return [e['kind'] for e in n['comparison']['generation_check']['exceptions']]


def with_common(v, **extra):
    g = {'id': 'G1', 'nature': 'multi_method', 'question': '同类配管是否计出桥架段', 'text': '合成情况',
         'options': [{'id': 'O1', 'text': '计出桥架段', 'impact': None},
                     {'id': 'O2', 'text': '不计出桥架段', 'impact': [{'value': -5.0, 'unit': 'm', 'note': '合成影响'}]}],
         'current_option': 'O1', 'handler': 'decide', 'item_ids': [], 'rule_ref': 'K8', 'status': '待定'}
    g.update(extra)
    v['comparison']['common_issues'] = [g]
    return g


def q1(v):
    return v['comparison']['issues'][0]


# ---------- 5.5 deviation attribution and implied list-issue groups ----------

def test_attribution_follows_basis_and_implies_list_groups():
    n = norm(fx.view()); its = items_of(n)
    i1 = its['I1']['third_parties']
    assert i1[0]['attribution'] is None                                           # 一致 is not attributed
    assert i1[1]['attribution'] == 'ours' and i1[1]['excess'] is None             # 5 m more, within 5 m estimate
    assert its['I2']['third_parties'][0]['attribution'] == 'list'                 # sufficient basis: the list's problem
    assert its['I2']['third_parties'][0]['list_issue_types'] == ['清单多计', '清单规格写错']
    assert its['I3']['third_parties'][0]['list_issue_types'] == ['清单列了图上没有的']   # linked only to a closed issue
    assert its['I6']['third_parties'][0]['list_issue_types'] == ['清单漏量']
    assert its['I4']['third_parties'][0]['attribution'] is None and its['I5']['third_parties'][0]['attribution'] is None
    g = commons_of(n)
    implied = [k for k in g if k.startswith(LIST_PREFIX)]
    assert implied == [LIST_PREFIX + t for t in ('清单多计', '清单漏量', '清单列了图上没有的', '清单规格写错')]
    assert g[LIST_PREFIX + '清单多计']['scope'] == {'issue_ids': [], 'item_ids': ['I2']}
    assert g[LIST_PREFIX + '清单多计']['nature'] == 'list_issue' and g[LIST_PREFIX + '清单多计']['current_option'] == 'O1'
    assert its['I2']['list_issue_ids'] == [LIST_PREFIX + '清单多计', LIST_PREFIX + '清单规格写错']
    counts = n['comparison']['generation_check']['counts']
    assert counts['attribution'] == {'list': 3, 'ours': 1, 'both': 0, 'none': 0} and counts['implied_common_issues'] == 4


def test_both_when_deviation_exceeds_estimate_and_excess_is_the_rest():
    v = fx.view(); bid = fx.item(v, 'I1')['third_parties'][1]
    bid['rows'] = [fx.row('T1', 10.0), fx.row('T2', 10.0)]; bid['quantity'] = 20.0; bid['difference'] = {'value': 15.0, 'rate': 0.75}
    s = items_of(norm(v))['I1']['third_parties'][1]
    assert s['attribution'] == 'both' and s['excess'] == pytest.approx(10.0) and s['list_issue_types'] == ['清单少计']


def test_supplied_attribution_must_match_recompute():
    v = fx.view(); fx.item(v, 'I2')['third_parties'][0]['attribution'] = 'ours'
    assert 'C3-11' in checks_of(v)
    v = fx.view(); fx.item(v, 'I2')['third_parties'][0].update(attribution='list', excess=None)
    assert items_of(norm(v))['I2']['third_parties'][0]['attribution'] == 'list'


def test_weak_deviation_without_open_issue_is_reminded():
    v = fx.view(); q1(v)['item_ids'] = []
    n = norm(v)
    assert '依据不足的偏差未挂问题' in kinds(n) and n['comparison']['generation_check']['counts']['weak_deviation_without_issue'] == 1


def test_implied_groups_may_not_be_written_by_data_but_renormalizing_is_stable():
    v = fx.view(); v['comparison']['common_issues'] = [{'id': LIST_PREFIX + '清单多计', 'nature': 'list_issue', 'question': '?', 'handler': 'confirm'}]
    assert 'C3-10' in checks_of(v)
    n = norm(fx.view()); again = norm(n)
    assert [g['id'] for g in again['comparison']['common_issues']] == [g['id'] for g in n['comparison']['common_issues']]
    assert again['result_fingerprint'] == n['result_fingerprint']


# ---------- 6 natures and common issues ----------

def test_common_issue_scope_and_inherited_nature():
    v = fx.view(); g = with_common(v, item_ids=['E1']); q1(v)['common_issue_id'] = 'G1'
    n = norm(v); cs = commons_of(n); its = items_of(n)
    assert cs['G1']['scope'] == {'issue_ids': ['Q1'], 'item_ids': ['I1', 'E1']} and cs['G1']['implied'] is False
    assert n['comparison']['issues'][0]['nature'] == 'multi_method'          # taken from the common issue
    assert its['I1']['common_issue_ids'] == ['G1'] and its['E1']['common_issue_ids'] == ['G1']
    assert its['E1']['uncertain'] == ['以暂估为主'] and its['E1']['uncertain_unlinked'] is False   # an open common issue links it
    assert g['options'][0]['impact'] is None


@pytest.mark.parametrize('change', [
    {'nature': 'unknown'}, {'nature': None}, {'question': ''}, {'handler': 'later'},
    {'options': [{'id': 'O1', 'text': '只有一种'}], 'current_option': 'O1'},
    {'current_option': 'O9'}, {'options': [], 'current_option': 'O1'},
    {'options': [{'id': 'O1', 'text': 'a'}, {'id': 'O1', 'text': 'b'}]},
    {'options': [{'id': 'O1', 'text': 'a', 'impact': {'value': 1.0}}, {'id': 'O2', 'text': 'b'}]},
    {'item_ids': ['NOPE']}, {'id': LIST_PREFIX + '自写'}, {'id': 'Q1'},
])
def test_common_issue_checks(change):
    v = fx.view(); with_common(v, **change)
    assert 'C3-10' in checks_of(v)


def test_issue_nature_must_match_its_common_issue():
    v = fx.view(); with_common(v); q1(v).update(common_issue_id='G1', nature='scope')
    assert 'C3-6' in checks_of(v)
    v = fx.view(); q1(v)['common_issue_id'] = 'G9'
    assert 'C3-6' in checks_of(v)


def test_standalone_multi_method_issue_needs_two_options():
    v = fx.view(); q1(v)['nature'] = 'multi_method'
    assert 'C3-6' in checks_of(v)
    q1(v).update(options=[{'id': 'A', 'text': '甲'}, {'id': 'B', 'text': '乙'}], current_option='A')
    assert norm(v)['comparison']['issues'][0]['current_option'] == 'A'


def test_unnatured_open_issue_is_reminded_but_closed_one_is_not():
    n = norm(fx.view())
    assert kinds(n).count('问题未分性质') == 1 and n['comparison']['generation_check']['counts']['issues_without_nature'] == 1
    v = fx.view(); q1(v)['nature'] = 'rule_gap'
    assert '问题未分性质' not in kinds(norm(v))
    v = fx.view(); q1(v)['nature'] = 'bad'
    assert 'C3-6' in checks_of(v)


def test_nature_labels_and_new_dimension_roles():
    v = fx.view(); v['comparison']['nature_labels'] = {'scope': '范围分期'}
    dims = v['comparison']['dimensions']; dims[2]['role'] = 'technical'
    dims.append({'id': 'category', 'title': '品类', 'role': 'category', 'values': [{'id': '配管', 'title': '配管'}]})
    fx.item(v, 'I1')['dimensions']['category'] = '配管'
    n = norm(v)
    assert n['comparison']['nature_labels']['scope'] == '范围分期' and n['comparison']['nature_labels']['rule_gap'] == '现行规则无法判断'
    v['comparison']['nature_labels'] = {'nope': 'x'}
    assert 'C3-0' in checks_of(v)
    v = fx.view(); v['comparison']['dimensions'] += [{'id': f'c{n}', 'title': 'x', 'role': 'category', 'values': []} for n in (1, 2)]
    assert 'C3-0' in checks_of(v)


# ---------- 5.1 decided not counted ----------

def excluded_row(**ex):
    return {'id': 'I1#3', 'level': '方案暂估量', 'quantity': 3.0, 'unit': 'm', 'basis': '二期段估',
            'excluded': {'rule_ref': 'K5', 'issue_id': 'Q2', 'note': '图标二期，本期不计', **ex}}


def test_excluded_rows_stay_outside_levels_and_uncertainty():
    v = fx.view(); fx.item(v, 'I1')['detail_rows'].append(excluded_row())
    pending = excluded_row(); pending.update(id='I1#4', level='未决量', quantity=None); fx.item(v, 'I1')['detail_rows'].append(pending)
    n = norm(v); i1 = items_of(n)['I1']
    assert i1['ours']['levels'] == fx.item(fx.view(), 'I1')['ours']['levels'] and i1['ours']['total'] == 35.0
    assert i1['ours']['excluded_quantity'] == 3.0 and i1['ours']['excluded_rows'] == 2
    assert '有未决量' not in i1['uncertain']
    assert '已定不计' in kinds(n) and n['comparison']['generation_check']['counts']['excluded_rows'] == 2
    assert 'excluded_rows' not in items_of(n)['I2']['ours']


def test_excluded_quantity_in_levels_breaks_c3_1():
    v = fx.view(); i1 = fx.item(v, 'I1'); i1['detail_rows'].append(excluded_row()); i1['ours']['levels']['方案暂估量'] = 8.0
    assert 'C3-1' in checks_of(v)


@pytest.mark.parametrize('ex', [{'note': ''}, {'rule_ref': None, 'issue_id': None}, {'issue_id': 'Q1'}, {'issue_id': 'NOPE'}, {'extra': 'x'}])
def test_excluded_needs_note_and_a_closed_basis(ex):
    v = fx.view(); fx.item(v, 'I1')['detail_rows'].append(excluded_row(**ex))
    assert 'C3-12' in checks_of(v)


def test_excluded_may_rest_on_rule_ref_only_or_a_closed_common_issue():
    v = fx.view(); fx.item(v, 'I1')['detail_rows'].append(excluded_row(issue_id=None))
    assert items_of(norm(v))['I1']['ours']['excluded_rows'] == 1
    v = fx.view(); with_common(v, handler='closed'); fx.item(v, 'I1')['detail_rows'].append(excluded_row(rule_ref=None, issue_id='G1'))
    assert items_of(norm(v))['I1']['ours']['excluded_rows'] == 1


def all_excluded_item(levels):
    return {'id': 'E2', 'kind': 'extra', 'name': '二期干线', 'dimensions': {'building': 'B1', 'division': 'D1'},
            'ours': {'unit': 'm', 'levels': levels}, 'third_parties': [],
            'detail_rows': [{'id': 'E2#1', 'level': '方案暂估量', 'quantity': 12.0, 'unit': 'm', 'basis': '二期估',
                             'excluded': {'rule_ref': 'K5', 'note': '本期不计'}}]}


def test_fully_excluded_item_is_zero_with_confirmed_zero():
    v = fx.view(); v['comparison']['items'].append(all_excluded_item({'确认量': 0, '规则推算量': None, '方案暂估量': None, '未决量': None}))
    e2 = items_of(norm(v))['E2']
    assert e2['ours']['total'] == 0 and e2['ours']['excluded_quantity'] == 12.0 and e2['uncertain'] == []
    v = fx.view(); v['comparison']['items'].append(all_excluded_item({'确认量': None}))
    assert 'C3-12' in checks_of(v)


def test_supplied_excluded_totals_must_match():
    v = fx.view(); i1 = fx.item(v, 'I1'); i1['detail_rows'].append(excluded_row()); i1['ours']['excluded_quantity'] = 4.0
    assert 'C3-12' in checks_of(v)


def test_excluded_shortfall_is_attributed_to_the_list():
    v = fx.view(); i6 = fx.item(v, 'I6')
    i6['third_parties'] = [fx.segment('AUDIT', [fx.row('A6', 6.0, unit='台', name='配电箱', seq='6')], '台', 6.0, 4.0)]
    i6['detail_rows'].append({'id': 'I6#2', 'level': '确认量', 'quantity': 2.0, 'unit': '台', 'basis': '二期箱', 'excluded': {'rule_ref': 'K5', 'note': '二期'}})
    s = items_of(norm(v))['I6']['third_parties'][0]
    assert s['conclusion'] == '少算' and s['attribution'] == 'list' and s['list_issue_types'] == ['清单多计']


# ---------- 9 / 9.1 fingerprints, common answers, inheritance and exceptions ----------

import json, subprocess  # noqa: E402
import read_qto_review_feedback as reader  # noqa: E402

CORE_JS = ROOT / 'skills' / '_qto-shared-evidence' / 'assets' / 'review' / 'review-core.js'


def answered_view():
    """G1 covers Q1 (and through it I1) plus E1 directly; Q2 stays closed."""
    v = fx.view(); with_common(v, item_ids=['E1']); q1(v)['common_issue_id'] = 'G1'
    return v


def common_note(n, gid='G1', **extra):
    g = commons_of(n)[gid]
    r = {'common_issue_snapshot': {'common_issue_id': gid, 'result_fingerprint': g['result_fingerprint'], 'scope': copy.deepcopy(g['scope'])},
         'result_fingerprint': g['result_fingerprint'], 'verdict': '同意', 'option_id': g['current_option'], 'confirmed_result': '', 'reason': '合成：按现做法',
         'updated_at': '2026-10-15T21:00:00Z', 'history': []}
    r.update(extra)
    return r


def item_note(n, iid, **extra):
    it = items_of(n)[iid]
    r = {'item_snapshot': {'item_id': iid, 'result_fingerprint': it['result_fingerprint']}, 'result_fingerprint': it['result_fingerprint'],
         'verdict': '要改', 'confirmed_result': '', 'reason': '合成：这一处不同', 'excluded_detail_row_ids': [], 'updated_at': '2026-10-15T21:00:00Z', 'history': []}
    r.update(extra)
    return r


def feedback(n, records):
    fb = c.empty_feedback(n); fb['annotations'] = records
    return fb


def js(fn, payload):
    code = ("require(process.argv[1]);const p=JSON.parse(require('fs').readFileSync(0,'utf8'));try{console.log(JSON.stringify(" + fn + "));}"
            "catch(e){console.log(JSON.stringify({error:e.message}));}")
    p = subprocess.run(['node', '-e', code, str(CORE_JS)], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def js_states(fb, view):
    return js("(r=>Object.fromEntries(Object.entries(r.annotations).map(([k,v])=>[k,v.applicability_state])))(QtoReviewCore.parse(p.fb,p.view))", {'fb': fb, 'view': view})


def test_common_fingerprint_ignores_scope_but_tracks_question_and_options():
    n = norm(answered_view())
    v = answered_view(); v['comparison']['common_issues'][0]['item_ids'] = []           # scope shrinks
    assert commons_of(norm(v))['G1']['result_fingerprint'] == commons_of(n)['G1']['result_fingerprint']
    v = answered_view(); v['comparison']['common_issues'][0]['question'] = '改了问法'
    assert commons_of(norm(v))['G1']['result_fingerprint'] != commons_of(n)['G1']['result_fingerprint']
    v = answered_view(); v['comparison']['common_issues'][0]['options'][1]['impact'][0]['value'] = -6.0
    assert commons_of(norm(v))['G1']['result_fingerprint'] != commons_of(n)['G1']['result_fingerprint']
    v = answered_view(); fx.item(v, 'I1')['detail_rows'].append(excluded_row())
    assert items_of(norm(v))['I1']['result_fingerprint'] != items_of(n)['I1']['result_fingerprint']   # decided-not-counted rows are in the item fingerprint


@pytest.mark.parametrize('case', ['current', 'stale', 'unmatched'])
def test_common_answer_states_match_in_python_and_javascript(case):
    n = norm(answered_view())
    fb = feedback(n, {'共性问题|G1': common_note(n), '对照条目|E1': item_note(n, 'E1', exception_of='G1')})
    v = answered_view()
    if case == 'stale':
        v['comparison']['common_issues'][0]['text'] = '改了情况'; fx.item(v, 'E1')['ours']['note'] = '改了说明'
    elif case == 'unmatched':
        v['comparison']['common_issues'] = []; q1(v)['common_issue_id'] = None
    target = norm(v)
    py, _ = c.validate_feedback(fb, target)
    states = {k: r['applicability_state'] for k, r in py['annotations'].items()}
    assert states == {'共性问题|G1': case, '对照条目|E1': case} and js_states(fb, target) == states


def test_exception_outside_the_common_scope_no_longer_applies():
    n = norm(answered_view())
    fb = feedback(n, {'对照条目|E1': item_note(n, 'E1', exception_of='G1')})
    v = answered_view(); v['comparison']['common_issues'][0]['item_ids'] = []
    target = norm(v)
    py, _ = c.validate_feedback(fb, target)
    assert py['annotations']['对照条目|E1']['applicability_state'] == 'unmatched' and js_states(fb, target) == {'对照条目|E1': 'unmatched'}


@pytest.mark.parametrize('key, make, message', [
    ('共性问题|G1', lambda n: common_note(n, option_id='O9'), '所选做法'),
    ('共性问题|G1', lambda n: common_note(n, exception_of='G1'), 'exception_of'),
    ('共性问题|G1', lambda n: {**common_note(n), 'common_issue_snapshot': {'common_issue_id': 'G1', 'result_fingerprint': 'x'}}, 'scope'),
    ('共性问题|G2', lambda n: common_note(n), '键与快照不一致'),
    ('对照条目|E1', lambda n: item_note(n, 'E1', option_id='O1'), 'option_id'),
    ('对照条目|E1', lambda n: {**item_note(n, 'E1'), 'issue_snapshot': {'issue_id': 'Q1', 'result_fingerprint': 'x'}}, '只能指向一个'),
    ('对照条目|E1', lambda n: item_note(n, 'E1', merge_conflict={'candidates': [{}]}), 'merge_conflict'),
])
def test_bad_common_answers_and_exceptions_are_rejected_on_both_sides(key, make, message):
    n = norm(answered_view()); fb = feedback(n, {key: make(n)})
    with pytest.raises(ValueError, match=message):
        c.validate_feedback(fb, n)
    assert message in js_states(fb, n).get('error', '')


def handoff(n, records):
    out, _ = c.validate_feedback(feedback(n, records), n)
    return reader.comparison_actions(n, {k: r for k, r in out['annotations'].items() if r['applicability_state'] == 'current'})


def test_common_answer_is_inherited_with_via_and_exceptions_are_kept_apart():
    n = norm(answered_view())
    h = handoff(n, {'共性问题|G1': common_note(n), '对照条目|E1': item_note(n, 'E1', exception_of='G1')})
    ca = h['common_issue_actions'][0]
    assert ca['common_issue_id'] == 'G1' and ca['nature'] == 'multi_method' and ca['option_id'] == 'O1'
    assert ca['scope'] == {'issue_ids': ['Q1'], 'item_ids': ['I1']} and ca['added_since_answer'] == {'issue_ids': [], 'item_ids': []}
    assert [(a['issue_id'], a.get('via')) for a in h['issue_actions']] == [('Q1', 'G1')]
    assert sorted((a['item_id'], a.get('via') or '', a.get('exception_of') or '') for a in h['item_actions']) == [('E1', '', 'G1'), ('I1', 'G1', '')]
    assert h['conflicts'] == [] and h['overlaps'] == []


def test_added_since_answer_when_scope_grows_without_going_stale():
    record = common_note(norm(answered_view()))
    v = answered_view(); v['comparison']['common_issues'][0]['item_ids'] = ['E1', 'I6']
    h = handoff(norm(v), {'共性问题|G1': record})
    assert h['common_issue_actions'][0]['added_since_answer'] == {'issue_ids': [], 'item_ids': ['I6']}


def test_overlapping_common_answers_that_disagree_are_flagged():
    v = answered_view()
    v['comparison']['common_issues'].append({'id': 'G2', 'nature': 'assumption', 'question': '估算方法是否接受', 'handler': 'assumed', 'item_ids': ['E1']})
    n = norm(v)
    h = handoff(n, {'共性问题|G1': common_note(n), '共性问题|G2': common_note(n, 'G2', verdict='要改', option_id=None, reason='合成：不接受')})
    assert [o['item_id'] for o in h['overlaps']] == ['E1']


def test_implied_list_group_takes_one_answer_for_the_whole_group():
    n = norm(fx.view()); gid = LIST_PREFIX + '清单多计'
    h = handoff(n, {'共性问题|' + gid: common_note(n, gid)})
    assert h['common_issue_actions'][0]['scope']['item_ids'] == ['I2'] and [a['item_id'] for a in h['item_actions']] == ['I2']


def test_merge_keeps_both_opinions_until_the_user_resaves_one():
    n = norm(answered_view())
    a = common_note(n, updated_at='2026-10-15T21:00:00Z'); b = common_note(n, verdict='要改', option_id='O2', reason='合成：改选', updated_at='2026-10-15T21:05:00Z')
    local, incoming = feedback(n, {'共性问题|G1': a}), feedback(n, {'共性问题|G1': b})
    merged = js('QtoReviewCore.mergePackages(p.local,p.incoming)', {'local': local, 'incoming': incoming})
    rec = merged['annotations']['共性问题|G1']
    assert merged['merge_conflicts'] == 1 and len(rec['merge_conflict']['candidates']) == 2
    same = js('QtoReviewCore.mergePackages(p.local,p.incoming)', {'local': local, 'incoming': feedback(n, {'共性问题|G1': {**a, 'reason': '只改了措辞', 'updated_at': '2026-10-15T21:06:00Z'}})})
    assert 'merge_conflict' not in same['annotations']['共性问题|G1']        # same opinion: latest wins as before
    h = handoff(n, {'共性问题|G1': rec})
    assert h['common_issue_actions'] == [] and h['conflicts'][0]['annotation_key'] == '共性问题|G1'
    saved = js("QtoReviewCore.saveRecord(p.old,p.next,'共性问题|G1','2026-10-15T21:10:00Z')", {'old': rec, 'next': {**rec, 'reason': '合成：最终按 O2'}})
    assert 'merge_conflict' not in saved and saved['history']


def test_reader_cli_reports_common_actions_and_conflicts(tmp_path):
    v = answered_view(); n = norm(v)
    view_file = tmp_path / '视图.json'; view_file.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    fb_file = tmp_path / '批注.json'; fb_file.write_text(json.dumps(feedback(n, {'共性问题|G1': common_note(n)}), ensure_ascii=False), encoding='utf-8')
    out = tmp_path / '交接.json'
    p = subprocess.run([sys.executable, str(ROOT / 'skills/_qto-shared-evidence/scripts/read_qto_review_feedback.py'), '--view', str(view_file),
                        '--feedback', str(fb_file), '--output', str(out)], capture_output=True, text=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    summary = json.loads(p.stdout); h = json.loads(out.read_text(encoding='utf-8'))
    assert summary['status'] == 'PASS' and summary['conflicts'] == 0 and summary['actionable'] == 4   # one common + Q1 + I1 + E1
    assert len(h['common_issue_actions']) == 1 and {a['item_id'] for a in h['item_actions']} == {'I1', 'E1'}


# ---------- 12 table input: new tables, ledger links, several base views, row_ids_table ----------

from test_review_comparison_tables_20261015 import tables, run, write_csv  # noqa: E402


def build(tmp_path, mapping, *extra):
    m = tmp_path / 'mapping.json'; m.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
    out, rep = tmp_path / 'view.json', tmp_path / 'report.json'
    for p in (out, rep):
        if p.exists(): p.unlink()
    code, summary, _ = run('build_comparison_view_from_tables.py', '--mapping', m, '--output', out, '--report', rep, *extra)
    return code, json.loads(rep.read_text(encoding='utf-8')), out


DETAIL_HEADER = ['明细', '条目', '层级', '数量', '单位', '依据', '区域', '聚合', '成员数量名', '成员表', '已定不计', '不计口径', '不计问题', '不计说明']
DETAILS = [['I1#1', 'I1', '规则推算', '30', 'm', '3 回路合计', '1F', 'sum_members', '水平长度', 'circuit', '', '', '', ''],
           ['I1#2', 'I1', '方案暂估', '5', 'm', '出桥架估', '1F', '', '', '', '', '', '', ''],
           ['I1#3', 'I1', '方案暂估', '3', 'm', '二期段估', '室外', '', '', '', '是', 'K5', 'Q2', '图标二期，本期不计'],
           ['I2#1', 'I2', '确认', '10', '个', '逐个计数', '', '', '', '', '', '', '', ''],
           ['I6#1', 'I6', '确认', '4', '台', '系统图箱号', '', '', '', '', '', '', '', ''],
           ['E1#1', 'E1', '方案暂估', '7', 'm', '终点估算', '', '', '', '', '', '', '', '']]


def fifth_tables(tmp_path, details=DETAILS):
    m, mapping = tables(tmp_path); d = tmp_path / 'tables'
    write_csv(d / 'details.csv', DETAIL_HEADER, details)
    mapping['tables']['detail_rows'][0]['columns'].update(excluded='已定不计', excluded_rule_ref='不计口径', excluded_issue_id='不计问题', excluded_note='不计说明')
    write_csv(d / 'issues.csv', ['编号', '类别', '性质', '共性问题', '标题', '正文', '影响', '做法', '楼栋', '原条目'],
              [['Q1', '需要定口径', '', 'G1', '配管是否含出桥架段', '合成问题', '约 5 m', '现含', 'B1', ''],
               ['Q2', '已处理', '范围、分期或专业界面不清', '', '已处理事项', '仅留记录', '', '', 'B1、B2', 'E1-Q02']])
    issues = mapping['tables']['issues'][0]
    issues['columns'].update(nature='性质', common_issue_id='共性问题')
    natures = {'有多种判断方式': 'multi_method', '范围、分期或专业界面不清': 'scope', '按假设或估算计入': 'assumption'}
    issues['value_maps']['nature'] = natures
    write_csv(d / 'common.csv', ['编号', '性质', '问法', '情况', '处理方', '现做法', '口径', '楼栋'],
              [['G1', '有多种判断方式', '同类配管是否计出桥架段', '合成情况', '需要定口径', 'O1', 'K8', 'B1']])
    write_csv(d / 'options.csv', ['问题编号', '做法编号', '做法', '影响数值', '单位', '说明'],
              [['G1', 'O1', '计出桥架段', '', '', '现做法'], ['G1', 'O2', '不计出桥架段', '-5', 'm', '一号楼'], ['G1', 'O2', '不计出桥架段', '-2', 'm', '二号楼']])
    write_csv(d / 'common_links.csv', ['common_issue_id', 'item_id'], [['G1', 'E1']])
    mapping['tables']['common_issues'] = [{'path': 'tables/common.csv', 'value_maps': {'nature': natures, 'handler': {'需要定口径': 'decide'}},
                                           'columns': {'id': '编号', 'nature': '性质', 'question': '问法', 'text': '情况', 'handler': '处理方', 'current_option': '现做法',
                                                       'rule_ref': '口径', 'dim.building': '楼栋'}}]
    mapping['tables']['issue_options'] = [{'path': 'tables/options.csv', 'columns': {'issue_id': '问题编号', 'option_id': '做法编号', 'text': '做法', 'value': '影响数值', 'unit': '单位', 'note': '说明'}}]
    mapping['tables']['common_issue_links'] = [{'path': 'tables/common_links.csv'}]
    return mapping


def test_tables_common_issues_options_and_excluded_rows(tmp_path):
    code, report, out = build(tmp_path, fifth_tables(tmp_path))
    assert code == 0, report
    n = norm(json.loads(out.read_text(encoding='utf-8')))
    g = commons_of(n)['G1']
    assert g['nature'] == 'multi_method' and g['current_option'] == 'O1' and g['scope'] == {'issue_ids': ['Q1'], 'item_ids': ['I1', 'E1']}
    assert g['options'][0]['impact'] == [{'value': None, 'note': '现做法'}]
    assert g['options'][1]['impact'] == [{'value': -5.0, 'unit': 'm', 'note': '一号楼'}, {'value': -2.0, 'unit': 'm', 'note': '二号楼'}]
    q = {x['id']: x for x in n['comparison']['issues']}
    assert q['Q1']['nature'] == 'multi_method' and q['Q2']['nature'] == 'scope'
    i1 = items_of(n)['I1']
    assert i1['ours']['excluded_quantity'] == 3.0 and next(r for r in i1['detail_rows'] if r['id'] == 'I1#3')['excluded'] == {'rule_ref': 'K5', 'issue_id': 'Q2', 'note': '图标二期，本期不计'}
    assert report['comparison_counts']['excluded_rows'] == 1 and report['comparison_counts']['common_issues'] == 1


def test_tables_excluded_basis_without_flag_and_option_text_mismatch_are_reported(tmp_path):
    rows = copy.deepcopy(DETAILS); rows[2][10] = ''
    mapping = fifth_tables(tmp_path, rows)
    write_csv(tmp_path / 'tables' / 'options.csv', ['问题编号', '做法编号', '做法', '影响数值', '单位', '说明'],
              [['G1', 'O1', '计出桥架段', '', '', ''], ['G1', 'O2', '不计', '-5', 'm', ''], ['G1', 'O2', '不计出桥架段', '-2', 'm', ''], ['G9', 'O1', 'x', '', '', '']])
    code, report, out = build(tmp_path, mapping)
    msgs = ' '.join(p['message'] for p in report['problems'])
    assert code == 2 and not out.exists()
    assert 'excluded 为假或空' in msgs and '各行的 text 须相同' in msgs and '已存在的问题编号' in msgs


def ledger_mapping(tmp_path, links):
    """Circuits only build objects; quantities and levels come from the link table, aggregation from its own table."""
    rows = [r[:7] + [''] * 3 + r[10:] if r[0] == 'I1#1' else r for r in DETAILS]
    mapping = fifth_tables(tmp_path, rows)
    mapping['tables']['members'][0].pop('quantities'); mapping['tables']['members'][0]['columns'].pop('detail_id')
    write_csv(tmp_path / 'tables' / 'links.csv', ['回路键', '明细', '数量名', '层级', '数量'], links)
    write_csv(tmp_path / 'tables' / 'agg.csv', ['明细', '汇总', '成员数量名'], [['I1#1', 'sum_members', '水平长度']])
    mapping['tables']['member_links'] = [{'path': 'tables/links.csv', 'value_maps': {'level': {'规则推算': '规则推算量'}},
                                          'columns': {'catalog_key': '回路键', 'detail_id': '明细', 'quantity_name': '数量名', 'level': '层级', 'quantity': '数量'}}]
    mapping['tables']['detail_aggregation'] = [{'path': 'tables/agg.csv', 'constants': {'member_table': 'circuit'}, 'columns': {'detail_id': '明细', 'aggregation': '汇总', 'member_quantity_name': '成员数量名'}}]
    return mapping


LINKS = [['成员|WL1', 'I1#1', '水平长度', '规则推算', '10'], ['成员|WL2', 'I1#1', '水平长度', '规则推算', '12'], ['成员|WL3', 'I1#1', '水平长度', '规则推算', '8']]


def test_tables_ledger_by_link_and_aggregation_tables(tmp_path):
    code, report, out = build(tmp_path, ledger_mapping(tmp_path, LINKS))
    assert code == 0, report
    n = norm(json.loads(out.read_text(encoding='utf-8')))
    row = next(r for r in items_of(n)['I1']['detail_rows'] if r['id'] == 'I1#1')
    assert row['aggregation'] == 'sum_members' and row['member_table'] == 'circuit' and sorted(row['member_keys']) == ['成员|WL1', '成员|WL2', '成员|WL3']
    wl1 = next(o for o in n['objects'] if o['catalog_key'] == '成员|WL1')
    assert wl1['quantities'] == [{'name': '水平长度', 'value': 10.0, 'unit': 'm', 'level': '规则推算量'}]


def test_tables_ledger_links_that_break_the_sum_or_disagree_are_stopped(tmp_path):
    bad = copy.deepcopy(LINKS); bad[2][4] = '9'
    code, report, _ = build(tmp_path, ledger_mapping(tmp_path, bad))
    assert code == 2 and {p['check'] for p in report['problems']} == {'C3-2'}
    clash = LINKS + [['成员|WL1', 'I1#1', '水平长度', '规则推算', '11']]
    code, report, _ = build(tmp_path, ledger_mapping(tmp_path, clash))
    assert code == 2 and any('不猜' in p['message'] for p in report['problems'])
    code, report, _ = build(tmp_path, ledger_mapping(tmp_path, LINKS + [['成员|WL9', 'I1#1', '水平长度', '规则推算', '1']]))
    assert code == 2 and any('WL9' in p['message'] for p in report['problems'])


def base_view_file(tmp_path, name, scene, keys):
    base = fx.view(); del base['comparison']; base['schema'] = 'qto-review-view/1.0'
    base['scenes'][0]['id'] = scene
    base['objects'] = [o for o in base['objects'] if o['id'] in keys]
    for o in base['objects']: o.update(catalog_key='成员|' + o['id'], scene_id=scene)
    p = tmp_path / name; p.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8')
    return p


def test_tables_merge_several_base_views_and_stop_on_clashes(tmp_path):
    a = base_view_file(tmp_path, 'a.json', 'P1', {'WL1', 'WL2'}); b = base_view_file(tmp_path, 'b.json', 'P2', {'WL3'})
    mapping = tables(tmp_path)[1]
    code, report, out = build(tmp_path, mapping, '--base-view', a, '--base-view', b)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8'))
    assert [s['id'] for s in v['scenes']] == ['P1', 'P2'] and {o['scene_id'] for o in v['objects']} == {'P1', 'P2'} and len(v['objects']) == 3
    assert report['base_views'] == [{'view': 'a.json', 'scenes': 1, 'objects': 2, 'groups': len(fx.view()['groups'])}, {'view': 'b.json', 'scenes': 1, 'objects': 1, 'groups': len(fx.view()['groups'])}]
    clash = base_view_file(tmp_path, 'c.json', 'P1', {'WL3'})
    code, report, _ = build(tmp_path, mapping, '--base-view', a, '--base-view', clash)
    assert code == 2 and '图页编号相同' in report['error']
    # Problem groups are numbered per package: a clashing group of the later view is renamed "<file stem>|<id>" and reported.
    for f, key in ((a, '成员|WL1'), (b, '成员|WL3')):
        v = json.loads(f.read_text(encoding='utf-8')); v['groups'] = [{'id': 'G01', 'title': '合成组', 'category': '合成', 'proposal': '合成', 'reason': '合成', 'status': '待复核', 'member_keys': [key]}]
        f.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    code, report, out = build(tmp_path, mapping, '--base-view', a, '--base-view', b)
    assert code == 0, report
    assert [g['id'] for g in json.loads(out.read_text(encoding='utf-8'))['groups']] == ['G01', 'b|G01'] and report['renamed_groups'] == [{'view': 'b.json', 'from': 'G01', 'to': 'b|G01'}]


@pytest.mark.parametrize('spec, ok', [('tables/audit_rows.csv', True), ({'path': 'tables/audit_rows.csv', 'column': 'row_id'}, True), (5, False), ({'column': 'row_id'}, False)])
def test_tables_row_ids_table_accepts_object_or_path_string(tmp_path, spec, ok):
    mapping = tables(tmp_path)[1]; mapping['comparison']['sources'][0]['row_ids_table'] = spec
    code, report, _ = build(tmp_path, mapping)
    assert (code == 0) is ok, report
    if not ok:
        assert any('row_ids_table 写成' in p['message'] for p in report['problems'])


# ---------- 5.5: an open list issue says the list is wrong; it does not make our basis weak ----------

@pytest.mark.parametrize('nature, att', [('list_issue', 'list'), ('rule_gap', 'both')])
def test_open_list_issue_does_not_weaken_our_basis(nature, att):
    v = fx.view(); q = v['comparison']['issues'][0]; q['nature'] = nature; q['item_ids'] = ['I1', 'I2']
    n = norm(v); s = items_of(n)['I2']['third_parties'][0]
    assert s['attribution'] == att and (s['excess'] is None) == (att == 'list')
    assert n['comparison']['generation_check']['counts']['weak_deviation_without_issue'] == 0   # linked either way, so no reminder
