"""R1 复核页审核视图（登记 20261015-1842 公共复核页 三条 + 0825 电气 E5）。

契约：docs/维护/复核页审核视图契约.md。每条双向校验配一个合成反例，合法夹具作对照；
1.0 视图和 2.0 批注的行为、指纹不变。数据全部是合成的，不含项目工程量。
"""
import copy, json, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / '_qto-shared-evidence' / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qto_review_contract as c  # noqa: E402
from qto_review_comparison import ComparisonError  # noqa: E402
from qto_review_revision import fingerprint  # noqa: E402
import review_comparison_fixture as fx  # noqa: E402


def norm(v):
    return c.normalize_view(v)


def problems(v):
    with pytest.raises(ComparisonError) as e:
        norm(v)
    return e.value.problems


def checks_of(v):
    return {p['check'] for p in problems(v)}


def seg(v, iid, n=0):
    return fx.item(v, iid)['third_parties'][n]


# ---------- legal fixture ----------

def test_valid_view_normalizes_and_fills_only_rule_based_fields():
    v = fx.view(); before = copy.deepcopy(v)
    n = norm(v)
    assert v == before                      # input not mutated
    items = {i['id']: i for i in n['comparison']['items']}
    assert items['I1']['third_parties'][0]['conclusion'] == '一致'      # 1/34 = 2.9% within 5% band
    assert items['I1']['third_parties'][1]['conclusion'] == '多算'      # bid 16.7%
    assert items['I2']['third_parties'][0]['conclusion'] == '少算'      # count unit: must be equal
    assert items['I1']['ours']['total'] == 35.0 and items['I3']['ours']['total'] is None
    assert items['E1']['estimate_dominant'] is True and items['I1']['estimate_dominant'] is False
    assert items['I1']['issue_ids'] == ['Q1'] and items['I3']['issue_ids'] == ['Q2']
    for i in n['comparison']['items']:     # quantities are carried, never recomputed
        assert i['ours']['levels'] == fx.item(before, i['id'])['ours']['levels']
    gc = n['comparison']['generation_check']
    expect = {'items': 6, 'extra_items': 1, 'detail_rows': 5, 'issues': 2, 'open_issues': 1, 'sources': 2, 'source_rows': 8,
              'member_objects': 3, 'uncertain_items': 2, 'uncertain_without_issue': 2, 'search_incomplete': 0}
    assert {k: gc['counts'][k] for k in expect} == expect   # fifth-modification counts: test_review_fifth_20261015.py
    assert {e['kind'] for e in gc['exceptions']} >= {'第三方条目有误已修正'}
    assert n['comparison']['handler_labels']['confirm'] == '需设计或业主确认' and n['comparison']['handler_labels']['decide'] == '需要你定口径'


def test_no_scene_view_with_unlocated_members_is_allowed():
    n = norm(fx.view(with_scene=False))
    assert n['scenes'] == [] and all(o['kind'] == 'unlocated' for o in n['objects'])


def test_comparison_view_may_have_no_objects_when_rows_are_leaf():
    v = fx.view(with_scene=False); v['objects'] = []
    fx.item(v, 'I1')['detail_rows'][0].update(aggregation='leaf', member_keys=[], member_table=None)
    assert norm(v)['objects'] == []


def test_located_object_without_scenes_is_rejected():
    v = fx.view(with_scene=False); v['objects'][0].update(kind='line', scene_id='P1', points=[[0, 0], [1, 1]])
    with pytest.raises(ValueError, match='unlocated'):
        norm(v)


def test_comparison_requires_view_1_1_and_1_0_still_requires_a_scene():
    v = fx.view(); v['schema'] = c.VIEW_SCHEMA
    with pytest.raises(ValueError, match='1.1'):
        norm(v)
    v = fx.view(with_scene=False); v['schema'] = c.VIEW_SCHEMA; v.pop('comparison')
    with pytest.raises(ValueError, match='至少提供一张图页'):
        norm(v)


def test_1_0_view_fingerprint_formula_unchanged():
    v = fx.view(); v['schema'] = c.VIEW_SCHEMA; v.pop('comparison')
    for o in v['objects']:
        o.pop('dimensions')
    n = norm(v)
    expected = fingerprint({'objects': sorted((o['catalog_key'], o['result_fingerprint']) for o in n['objects']),
                            'groups': [], 'summary': []})
    assert n['result_fingerprint'] == expected and 'comparison' not in n


# ---------- C3 negatives, one per check ----------

def test_c3_1_level_must_equal_detail_rows_and_units_match():
    v = fx.view(); fx.item(v, 'I1')['ours']['levels']['方案暂估量'] = 6.0
    assert 'C3-1' in checks_of(v)
    v = fx.view(); fx.item(v, 'I2')['detail_rows'][0]['unit'] = '套'
    assert any('明细单位' in p['message'] for p in problems(v))
    v = fx.view(); fx.item(v, 'I6')['ours']['levels']['未决量'] = 2.0
    assert any('没有该层级的明细行' in p['message'] for p in problems(v))


def test_c3_2_member_sum_declared_reason_and_leaf_without_members():
    v = fx.view(); v['objects'][0]['quantities'][0]['value'] = 11.0
    assert any('成员合计' in p['message'] for p in problems(v))
    v = fx.view(); fx.item(v, 'I1')['detail_rows'][0]['aggregation'] = 'declared'
    assert any('aggregation_reason' in p['message'] for p in problems(v))
    v = fx.view(); fx.item(v, 'I1')['detail_rows'][0]['aggregation'] = 'declared'; fx.item(v, 'I1')['detail_rows'][0]['aggregation_reason'] = '按系统图回路表'
    assert any(e['kind'] == '明细数量不由成员合计' for e in norm(v)['comparison']['generation_check']['exceptions'])
    v = fx.view(); fx.item(v, 'I1')['detail_rows'][0]['aggregation'] = 'leaf'
    assert 'C3-2' in checks_of(v)
    v = fx.view(); fx.item(v, 'I1')['detail_rows'][0].pop('aggregation')
    assert any('须声明 aggregation' in p['message'] for p in problems(v))


def test_c3_3_every_source_row_exactly_once():
    v = fx.view(); v['comparison']['sources'][0]['row_ids'].append('A7')
    assert any('没有出现在任何条目里' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I2')['rows'].append(fx.row('A1', 0.0, unit='个'))
    seg(v, 'I2')['quantity'] = 11.0
    assert any('出现多次' in p['message'] for p in problems(v))
    v = fx.view(); v['comparison']['sources'][0]['row_ids'].remove('A6')
    assert any('不在 row_ids 里' in p['message'] for p in problems(v))


def test_c3_4_member_in_two_rows_needs_split_summing_to_one():
    v = fx.view(); rows = fx.item(v, 'I1')['detail_rows']
    rows[1].update(aggregation='declared', aggregation_reason='合成', member_keys=[v['objects'][0]['catalog_key']], member_quantity_name='水平长度')
    assert 'C3-4' in checks_of(v)
    key = v['objects'][0]['catalog_key']
    rows[0]['shared_member_split'] = [{'member_key': key, 'share': 0.5, 'basis': '合成拆分'}]
    rows[1]['shared_member_split'] = [{'member_key': key, 'share': 0.5, 'basis': '合成拆分'}]
    rows[0]['quantity'] = 25.0; fx.item(v, 'I1')['ours']['levels']['规则推算量'] = 25.0
    for s in fx.item(v, 'I1')['third_parties']:
        s['difference'] = {'value': 30.0 - s['quantity'], 'rate': round((30.0 - s['quantity']) / s['quantity'], 4)}
    seg(v, 'I1', 1)['conclusion'] = '一致'
    n = norm(v)
    assert any(e['kind'] == '成员拆分' for e in n['comparison']['generation_check']['exceptions'])
    rows[1]['shared_member_split'][0]['share'] = 0.4
    assert any('份额合计' in p['message'] for p in problems(v))


def test_c3_4_one_object_may_feed_rows_with_different_quantity_names():
    v = fx.view(); key = v['objects'][0]['catalog_key']; row = fx.item(v, 'I2')['detail_rows'][0]
    row.update(aggregation='declared', aggregation_reason='合成：电缆头按回路计数', member_keys=[key], member_quantity_name='电缆头')
    norm(v)                                                    # same circuit, another quantity: not a duplicate
    row['member_quantity_name'] = '水平长度'
    assert 'C3-4' in checks_of(v)                              # same quantity of the same object in two rows


@pytest.mark.parametrize('impact, ok', [
    (None, True), ({'value': 5.0, 'unit': 'm'}, True), ({'value': None, 'note': '定性：影响计价口径'}, True),
    ([{'value': 5.0, 'unit': 'm'}, {'value': 2, 'unit': '个', 'note': '电缆头'}], True),
    ({'value': 5.0}, False), ({'value': None}, False), ([{'value': 'x', 'unit': 'm'}], False), ('5 m', False)])
def test_c3_6_issue_impact_may_be_several_segments(impact, ok):
    v = fx.view(); v['comparison']['issues'][0]['impact'] = impact
    if ok:
        norm(v)
    else:
        assert any('impact' in p['message'] for p in problems(v))


def test_c3_5_uniform_band_overrides_per_item_judgement():
    v = fx.view(); seg(v, 'I1')['conclusion'] = '多算'           # 2.9% is within the 5% measure band
    assert any('默认带宽判定“一致”' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I2')['conclusion'] = '一致'           # count unit: 10 vs 11 must be 少算
    assert any('默认带宽判定“少算”' in p['message'] for p in problems(v))
    v = fx.view(); v['comparison']['thresholds'] = {'consistent_band': 0.1}   # one number for every unit
    assert norm(v)['comparison']['items'][1]['third_parties'][0]['conclusion'] == '一致'   # 9.1% within 10%
    v = fx.view(); v['comparison']['thresholds'].pop('consistent_band')
    assert any('consistent_band' in p['message'] for p in problems(v))


@pytest.mark.parametrize('thresholds, message', [
    ({'consistent_band': {'计量': 0.05, '计数': 0}}, '须给 count_units'),
    ({'consistent_band': {'计量': 0.05}, 'count_units': ['个']}, '"计量": 非负数字'),
    ({'consistent_band': -0.1}, '非负数字'),
    ({'consistent_band': 0.05, 'consistent_band_by_unit': {'个': 0}}, '已删除'),
    ({'consistent_band': 0.05, 'count_units': ['个', '个']}, '不重复'),
])
def test_c3_0_default_band_split_by_unit_class(thresholds, message):
    v = fx.view(); v['comparison']['thresholds'] = thresholds
    assert any(message in p['message'] for p in problems(v))


def test_band_check_only_on_band_judged_segments():
    segs = {i['id']: i['third_parties'][0] for i in norm(fx.view())['comparison']['items'] if i['third_parties']}
    assert segs['I1']['band_check']['equal'] is False and abs(segs['I1']['band_check']['rate'] - 1 / 34) < 1e-12
    assert segs['I2']['band_check'] == {'rate': -1 / 11, 'equal': False}
    assert all('band_check' not in segs[k] for k in ('I3', 'I4', 'I6'))   # 图上无、归入他项、第三方无量 are shown as written
    v = fx.view(); seg(v, 'I3')['band_check'] = {'rate': 0, 'equal': True}
    assert 'band_check' not in fx.item(norm(v), 'I3')['third_parties'][0]   # never trusted from the input


def test_legacy_search_evidence_marks_incomplete_instead_of_failing():
    v = fx.view(); seg(v, 'I3')['search_evidence'] = {'note': '查过 P1、P2，未逐条记关键词'}
    assert any('检索依据缺' in p['message'] for p in problems(v))           # rule in force: incomplete records fail
    v['comparison']['legacy_search_evidence'] = {'rule': 'e63fe30', 'note': '规则生效前交付，不回头补'}
    n = norm(v); s = fx.item(n, 'I3')['third_parties'][0]; check = n['comparison']['generation_check']
    assert s['search_incomplete'] is True and s['conclusion'] == '图上无' and check['counts']['search_incomplete'] == 1
    assert any(e['kind'] == '检索记录不全' and '查过 P1' in e['detail'] for e in check['exceptions'])
    assert not fx.item(n, 'I3')['uncertain']                                # not turned into an open item
    v = fx.view(); seg(v, 'I3')['search_evidence'] = None; v['comparison']['legacy_search_evidence'] = {'rule': 'e63fe30', 'note': '合成'}
    assert fx.item(norm(v), 'I3')['third_parties'][0]['search_incomplete'] is True
    v = fx.view(); v['comparison']['legacy_search_evidence'] = {'rule': 'e63fe30', 'note': '合成'}
    assert 'search_incomplete' not in fx.item(norm(v), 'I3')['third_parties'][0]   # complete records stay complete
    v = fx.view(); v['comparison']['legacy_search_evidence'] = {'rule': 'e63fe30'}
    assert any(p['where'] == 'legacy_search_evidence' for p in problems(v))


def test_c3_5_difference_rate_and_conclusion_fields():
    v = fx.view(); seg(v, 'I1')['difference']['value'] = 2.0
    assert any('不等于本次合计' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I1')['difference']['rate'] = 0.0294 + 0.0004     # within 0.1% rounding
    norm(v)
    seg(v, 'I1')['difference']['rate'] = 0.0294 + 0.0008
    assert any('差额率' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I1')['difference'] = None
    assert any('须有差额' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I3')['search_evidence'].pop('hits')
    assert any('检索依据缺：hits' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I3')['search_evidence'] = None
    assert any('须附检索依据' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I4')['redirect_to'] = []
    assert any('须写 redirect_to' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I4')['redirect_to'] = ['不存在']
    assert any('去向条目不存在' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I4')['redirect_to'] = ['I4']
    assert any('去向不能是自己' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I5').pop('missing_drawing_note')
    assert any('missing_drawing_note' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I2').pop('correction_note')
    assert any('correction_note' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I6')['conclusion'] = '多算'
    assert any('只能用于同单位' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I6').pop('conclusion')
    assert any('须写结论' in p['message'] for p in problems(v))
    v = fx.view(); fx.item(v, 'I1')['ours']['total'] = 36.0
    assert any('本次合计' in p['message'] for p in problems(v))
    v = fx.view(); fx.item(v, 'I1')['estimate_dominant'] = True
    assert any('以暂估为主' in p['message'] for p in problems(v))


def test_c3_5_unit_mismatch_needs_note_and_no_difference():
    v = fx.view(); s = seg(v, 'I6'); s['unit'] = '套'; s['rows'][0]['unit'] = '套'
    assert any('unit_mismatch_note' in p['message'] for p in problems(v))
    s['unit_mismatch_note'] = '合成：清单按套计，本次按台'
    assert any(e['kind'] == '单位不同' for e in norm(v)['comparison']['generation_check']['exceptions'])


def test_c3_5_rate_rounding_tolerance_includes_the_boundary():
    # -5/40 = -0.125 written as -0.1245 (rounded to 0.1%): float difference is 0.0005000000000000004.
    v = fx.view(); s = seg(v, 'I1'); s['rows'][0]['quantity'] = 40.0; s['quantity'] = 40.0; s['difference'] = {'value': -5.0, 'rate': -0.1245}
    assert seg(norm(v), 'I1')['conclusion'] == '少算'
    s['difference']['rate'] = -0.1239
    assert any('差额率' in p['message'] for p in problems(v))


def test_c3_5_no_third_party_quantity_difference_counts_from_zero():
    v = fx.view(); seg(v, 'I6')['difference'] = {'value': 4.0, 'rate': None}     # electrical writes 差额 = 本次合计
    assert seg(norm(v), 'I6')['conclusion'] == '第三方无量'
    seg(v, 'I6')['difference'] = {'value': 3.0, 'rate': None}
    assert any('不等于本次合计' in p['message'] for p in problems(v))
    seg(v, 'I6')['difference'] = {'value': 4.0, 'rate': 1.0}
    assert any('差额率须为 null' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I5')['difference'] = {'value': 1.0, 'rate': None}     # only 第三方无量 may count from 0
    assert any('都须有值' in p['message'] for p in problems(v))


def test_c3_9_uncertain_items_should_reach_an_open_issue():
    n = norm(fx.view()); items = {i['id']: i for i in n['comparison']['items']}
    assert items['E1']['uncertain'] == ['以暂估为主'] and items['E1']['uncertain_unlinked'] is True
    assert items['I5']['uncertain'] == ['缺图'] and items['I5']['uncertain_unlinked'] is True
    assert items['I1']['uncertain'] == [] and items['I1']['uncertain_unlinked'] is False
    unl = [e for e in n['comparison']['generation_check']['exceptions'] if e['kind'] == '不确定条目未挂问题']
    assert sorted(e['where'] for e in unl) == ['条目 E1', '条目 I5']
    v = fx.view(); q = v['comparison']['issues']
    q[0]['item_ids'] += ['I5']; q[1]['item_ids'] += ['E1']           # Q1 open, Q2 closed
    fx.item(v, 'I1')['detail_rows'].append({'id': 'I1#3', 'level': '未决量', 'quantity': None, 'unit': 'm', 'basis': '待设计确认'})
    n = norm(v); items = {i['id']: i for i in n['comparison']['items']}
    assert items['I5']['uncertain_unlinked'] is False                  # linked to open Q1
    assert items['E1']['uncertain_unlinked'] is True                   # closed issue does not count
    assert items['I1']['uncertain'] == ['有未决量'] and items['I1']['uncertain_unlinked'] is False
    assert n['comparison']['generation_check']['counts']['uncertain_without_issue'] == 1


def test_comparison_may_hold_only_issues():
    v = fx.view(with_scene=False); v['objects'] = []
    c_ = v['comparison']; c_['sources'] = []; c_['items'] = []; c_['member_tables'] = []
    for q in c_['issues']: q['item_ids'] = []
    n = norm(v)
    counts = n['comparison']['generation_check']['counts']
    assert counts['items'] == 0 and counts['issues'] == 2 and counts['open_issues'] == 1 and n['scenes'] == [] and n['objects'] == []


def test_c3_6_issues_reference_existing_items_and_valid_handler():
    v = fx.view(); v['comparison']['issues'][0]['item_ids'] = ['不存在']
    assert 'C3-6' in checks_of(v)
    v = fx.view(); v['comparison']['issues'][0]['handler'] = '需要你定口径'
    assert any('handler' in p['message'] for p in problems(v))
    v = fx.view(); v['comparison']['issues'][1]['dimensions'] = {'building': ['B1', 'B9']}
    assert any('不在值域内' in p['message'] for p in problems(v))


def test_c3_7_object_dimension_must_match_item_or_be_declared():
    v = fx.view(); v['objects'][0]['dimensions'] = {'building': 'B2'}
    assert 'C3-7' in checks_of(v)
    v['objects'][0]['dimension_mismatch_note'] = '合成：跨楼回路'
    assert any(e['kind'] == '对象维度与条目不同' for e in norm(v)['comparison']['generation_check']['exceptions'])


def test_c3_8_segments_one_per_source_and_rows_sum():
    v = fx.view(); fx.item(v, 'I2')['third_parties'].append(copy.deepcopy(seg(v, 'I2')))
    assert any('同一来源只能有一段' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I1', 1)['quantity'] = 31.0
    assert any('各行数量之和' in p['message'] for p in problems(v))
    v = fx.view(); fx.item(v, 'E1')['third_parties'] = [copy.deepcopy(seg(v, 'I2'))]
    assert any('清单外条目不能有来源段' in p['message'] for p in problems(v))
    v = fx.view(); seg(v, 'I1', 1)['rows'][1]['quantity'] = None; seg(v, 'I1', 1)['quantity'] = 20.0
    seg(v, 'I1', 1)['difference'] = {'value': 15.0, 'rate': 0.75}
    assert any('quantity_note' in p['message'] for p in problems(v))
    v = fx.view(); segs = fx.item(v, 'I1')['third_parties']; segs.reverse()
    assert any('按 sources 的顺序' in p['message'] for p in problems(v))


def test_c3_0_closed_dimension_domains_and_roles():
    v = fx.view(); fx.item(v, 'I1')['dimensions']['building'] = 'B9'
    assert any('不在值域内' in p['message'] for p in problems(v))
    v = fx.view(); v['comparison']['dimensions'][1]['role'] = 'overview'
    assert any('overview 维度须恰好一个' in p['message'] for p in problems(v))
    v = fx.view(); v['comparison']['items'].append(copy.deepcopy(fx.item(v, 'I2')))
    assert any('条目编号缺失或重复' in p['message'] for p in problems(v))


def test_all_problems_are_reported_together():
    v = fx.view()
    fx.item(v, 'I1')['ours']['levels']['方案暂估量'] = 6.0
    seg(v, 'I3')['search_evidence'] = None
    v['comparison']['issues'][0]['item_ids'] = ['不存在']
    found = checks_of(v)
    assert {'C3-1', 'C3-5', 'C3-6'} <= found


# ---------- fingerprints and stale comments ----------

def test_item_and_issue_fingerprints_track_results_not_presentation():
    base = norm(fx.view())
    fp = lambda n, iid: fx.item(n, iid)['result_fingerprint']
    v = fx.view(); seg(v, 'I1')['rows'][0]['quantity'] = 34.5; seg(v, 'I1')['quantity'] = 34.5
    seg(v, 'I1')['difference'] = {'value': 0.5, 'rate': round(0.5 / 34.5, 4)}
    changed = norm(v)
    assert fp(changed, 'I1') != fp(base, 'I1') and fp(changed, 'I2') == fp(base, 'I2')
    assert changed['result_fingerprint'] != base['result_fingerprint']
    v = fx.view(); v['comparison']['display_terms']['third_party'] = '审计'; v['comparison']['thresholds']['difference_rate_alert'] = 0.1; v['title'] = '改名'
    same = norm(v)
    assert all(fp(same, i) == fp(base, i) for i in ('I1', 'I2', 'E1')) and same['result_fingerprint'] == base['result_fingerprint']
    v = fx.view(); v['comparison']['sources'][0]['files'][0]['sha256'] = fx.sha('audit-v2')
    assert fp(norm(v), 'I1') != fp(base, 'I1')
    v = fx.view(); v['objects'][0]['attributes']['起点箱'] = 'AL2'
    assert fp(norm(v), 'I1') != fp(base, 'I1')
    v = fx.view(); v['comparison']['issues'][0]['text'] = '改了正文'
    n = norm(v)
    assert n['comparison']['issues'][0]['result_fingerprint'] != base['comparison']['issues'][0]['result_fingerprint']


def item_note(n, iid, **extra):
    it = fx.item(n, iid)
    record = {'item_snapshot': {'item_id': iid, 'result_fingerprint': it['result_fingerprint']}, 'result_fingerprint': it['result_fingerprint'],
              'object_id': iid, 'object_ids': [], 'verdict': '要改', 'confirmed_result': '', 'reason': '合成：出桥架段应另列',
              'excluded_detail_row_ids': ['I1#2'], 'updated_at': '2026-10-15T20:00:00Z', 'history': []}
    record.update(extra)
    return record


def feedback(n, records):
    fb = c.empty_feedback(n); fb['annotations'] = records
    return fb


def test_feedback_2_1_item_and_issue_comments_validate_and_go_stale():
    n = norm(fx.view())
    q = n['comparison']['issues'][0]
    issue_record = {'issue_snapshot': {'issue_id': 'Q1', 'result_fingerprint': q['result_fingerprint']}, 'result_fingerprint': q['result_fingerprint'],
                    'object_id': 'Q1', 'object_ids': [], 'verdict': '同意', 'confirmed_result': '', 'reason': '按现做法', 'updated_at': '2026-10-15T20:00:00Z', 'history': []}
    fb = feedback(n, {'对照条目|I1': item_note(n, 'I1'), '问题|Q1': issue_record})
    assert fb['schema'] == c.FEEDBACK_SCHEMA_COMPARISON
    out, unmatched = c.validate_feedback(fb, n)
    assert not unmatched and {r['applicability_state'] for r in out['annotations'].values()} == {'current'}
    v = fx.view(); fx.item(v, 'I1')['detail_rows'][1]['basis'] = '改了依据'
    changed = norm(v)
    out, unmatched = c.validate_feedback(fb, changed)
    assert out['annotations']['对照条目|I1']['applicability_state'] == 'stale' and out['annotations']['问题|Q1']['applicability_state'] == 'current'
    v = fx.view(); v['comparison']['items'] = [i for i in v['comparison']['items'] if i['id'] != 'I4']
    v['comparison']['sources'][0]['row_ids'].remove('A4')
    out, unmatched = c.validate_feedback(feedback(n, {'对照条目|I4': item_note(n, 'I4', excluded_detail_row_ids=[])}), norm(v))
    assert out['annotations']['对照条目|I4']['applicability_state'] == 'unmatched' and unmatched


@pytest.mark.parametrize('change, message', [
    ({'reason': ''}, '须有原因'),
    ({'verdict': '', 'confirmed_result': ''}, '快捷结论或结论文字'),
    ({'verdict': '通过'}, '快捷结论只能是'),
    ({'excluded_detail_row_ids': ['I2#1']}, '不属于该条目'),
    ({'item_snapshot': {'item_id': 'I2', 'result_fingerprint': 'x'}}, '键与快照不一致'),
])
def test_feedback_2_1_rejects_bad_item_comments(change, message):
    n = norm(fx.view())
    with pytest.raises(ValueError, match=message):
        c.validate_feedback(feedback(n, {'对照条目|I1': item_note(n, 'I1', **change)}), n)


CORE_JS = ROOT / 'skills' / '_qto-shared-evidence' / 'assets' / 'review' / 'review-core.js'


def js_parse(fb, view):
    code = ("require(process.argv[1]);const p=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "try{const r=QtoReviewCore.parse(p.fb,p.view);console.log(JSON.stringify({schema:r.schema,states:Object.fromEntries(Object.entries(r.annotations).map(([k,v])=>[k,v.applicability_state]))}));}"
            "catch(e){console.log(JSON.stringify({error:e.message}));}")
    p = subprocess.run(['node', '-e', code, str(CORE_JS)], input=json.dumps({'fb': fb, 'view': view}, ensure_ascii=False),
                       capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize('case', ['current', 'stale', 'unmatched'])
def test_python_javascript_parity_for_item_and_issue_comments(case):
    n = norm(fx.view()); q = n['comparison']['issues'][0]
    issue_record = {'issue_snapshot': {'issue_id': 'Q1', 'result_fingerprint': q['result_fingerprint']}, 'result_fingerprint': q['result_fingerprint'],
                    'verdict': '要改', 'confirmed_result': '', 'reason': '出桥架段单列', 'updated_at': '2026-10-15T20:00:00Z', 'history': []}
    fb = feedback(n, {'对照条目|I4': item_note(n, 'I4', excluded_detail_row_ids=[]), '问题|Q1': issue_record})
    v = fx.view()
    if case == 'stale':
        seg(v, 'I4')['rows'][0]['features'] = '改了特征'; v['comparison']['issues'][0]['current_practice'] = '改了做法'
    elif case == 'unmatched':
        v['comparison']['items'] = [i for i in v['comparison']['items'] if i['id'] != 'I4']; v['comparison']['sources'][0]['row_ids'].remove('A4')
        v['comparison']['issues'] = [x for x in v['comparison']['issues'] if x['id'] != 'Q1']
    target = norm(v)
    py, _ = c.validate_feedback(fb, target)
    js = js_parse(fb, target)
    assert js['schema'] == py['schema'] == 'qto-review-feedback/2.1'
    assert js['states'] == {k: r['applicability_state'] for k, r in py['annotations'].items()} == {'对照条目|I4': case, '问题|Q1': case}


@pytest.mark.parametrize('change', [{'reason': ''}, {'verdict': '通过'}, {'excluded_detail_row_ids': ['I2#1']}, {'item_snapshot': {'item_id': 'I2', 'result_fingerprint': 'x'}}])
def test_python_javascript_both_reject_bad_item_comments(change):
    n = norm(fx.view())
    fb = feedback(n, {'对照条目|I1': item_note(n, 'I1', **change)})
    with pytest.raises(ValueError):
        c.validate_feedback(fb, n)
    assert 'error' in js_parse(fb, n)


PAGE_VIEW = {'consistent_band': {'计量': 0.1, '计数': 0}, 'conclusions': {'AUDIT': '一致'}}


def test_page_view_is_kept_but_not_used_for_staleness():
    n = norm(fx.view())
    fb = feedback(n, {'对照条目|I1': item_note(n, 'I1', page_view=PAGE_VIEW)})
    out, _ = c.validate_feedback(fb, n)
    assert out['annotations']['对照条目|I1']['applicability_state'] == 'current' and out['annotations']['对照条目|I1']['page_view'] == PAGE_VIEW
    assert js_parse(fb, n)['states'] == {'对照条目|I1': 'current'}


@pytest.mark.parametrize('pv', [{'consistent_band': -1, 'conclusions': {}}, {'consistent_band': {'计量': 0.05}, 'conclusions': {}},
                                {'consistent_band': 0.05, 'conclusions': {'AUDIT': '差不多'}}, {'consistent_band': 0.05}, 'x'])
def test_python_javascript_both_reject_bad_page_view(pv):
    n = norm(fx.view()); fb = feedback(n, {'对照条目|I1': item_note(n, 'I1', page_view=pv)})
    with pytest.raises(ValueError, match='page_view'):
        c.validate_feedback(fb, n)
    assert 'page_view' in js_parse(fb, n).get('error', '')


def test_1_0_views_keep_writing_feedback_2_0():
    v = fx.view(); v['schema'] = c.VIEW_SCHEMA; v.pop('comparison')
    assert c.empty_feedback(norm(v))['schema'] == c.FEEDBACK_SCHEMA


def test_reader_lists_item_and_issue_actions(tmp_path):
    v = fx.view(); n = norm(v)
    view_file = tmp_path / '视图.json'; view_file.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    fb_file = tmp_path / '批注.json'; fb_file.write_text(json.dumps(feedback(n, {'对照条目|I1': item_note(n, 'I1')}), ensure_ascii=False), encoding='utf-8')
    out = tmp_path / '交接.json'
    p = subprocess.run([sys.executable, str(ROOT / 'skills/_qto-shared-evidence/scripts/read_qto_review_feedback.py'), '--view', str(view_file),
                        '--feedback', str(fb_file), '--output', str(out)], capture_output=True, text=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    handoff = json.loads(out.read_text(encoding='utf-8'))
    assert handoff['actions'] == [] and handoff['issue_actions'] == [] and handoff['quantity_changes'] == 0
    action = handoff['item_actions'][0]
    assert action['item_id'] == 'I1' and action['verdict'] == '要改' and action['user_result'] == '要改' and action['excluded_detail_row_ids'] == ['I1#2']


def test_human_review_evidence_accepts_feedback_2_1(tmp_path):
    import qto_human_review_evidence as h
    assert 'qto-review-feedback/2.1' in h.FEEDBACK_SCHEMAS and h.FEEDBACK_SCHEMA in h.FEEDBACK_SCHEMAS
