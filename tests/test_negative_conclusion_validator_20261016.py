"""成果里的否定结论要引用检索记录（登记 20261015-2019、20261015-1535 否定结论条）。

检索记录由 search_negative_evidence.py 在合成中性取证包上真跑得到；成果用合成混凝土算例。不含项目工程量。
"""
import copy, importlib.util, json
from pathlib import Path
import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


gct = load('gct_for_negative', ROOT / 'tests' / 'test_general_capability_transfer.py')
ns = load('ns_for_negative', ROOT / 'tests' / 'test_negative_search_20261016.py')
validate_document = gct.validate_document
SCHEMA = gct.RESULT_SCHEMA


@pytest.fixture(scope='module')
def records(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('neg')
    _, _, full, _ = ns.run(tmp / 'full')                                  # NS-A hit, NS-D 全覆盖未命中
    item = {'item_id': 'P', 'conclusion': '图上无 消防广播', 'kind': 'text', 'term': '消防广播'}
    _, _, part, _ = ns.run(tmp / 'part', items=[item], drawing_set=['D1', 'D9'])   # NS-P 待确认（漏图）
    recs = {r['record_id']: r for r in full['records'] + part['records']}
    assert recs['NS-A']['status'] == 'hit' and recs['NS-D']['status'] == 'no_hit_full' and recs['NS-P']['status'] == 'no_hit_partial'
    assert '表与说明' in recs['NS-D']['sheet_kinds_searched']
    return recs


@pytest.fixture(scope='module')
def base(tmp_path_factory):
    _, _, result = gct.cf.calculate(gct.concrete_manifest(tmp_path_factory.mktemp('cf')))
    assert validate_document(result) == []
    return result


def doc(base, records, ids=('NS-A', 'NS-D', 'NS-P')):
    d = copy.deepcopy(base)
    d['否定结论检索记录'] = [copy.deepcopy(records[i]) for i in ids]
    return d


def zero_with_negative(d, refs, bill=3):
    """Item 0 becomes a confirmed zero backed by the given search records."""
    q = d['工程量结果'][0]
    q['结果']['数值'] = 0
    q['零值复核'] = {'复核结论': '已确认零值', '来源': list(q['来源']), '规则': list(q['规则']),
                  '范围覆盖': '应覆盖图纸 D1 全部检索', '说明': '检索记录 ' + '、'.join(refs) + ' 全覆盖未命中'}
    q['否定结论'] = {'结论': '图上无 消防广播', '检索记录': list(refs), '清单量': bill}
    return q['成果项编号']


# ---------- legal controls ----------

def test_full_coverage_record_supports_confirmed_zero(base, records):
    d = doc(base, records)
    zero_with_negative(d, ['NS-D'])
    assert validate_document(d) == []
    jsonschema.Draft202012Validator(SCHEMA).validate(d)


def test_items_without_negative_conclusion_are_not_affected(base, records):
    assert validate_document(doc(base, records)) == []       # hit and pending records alone are not an error
    assert validate_document(copy.deepcopy(base)) == []      # no records at all


def test_pending_record_with_insufficient_evidence_state_passes(base, records):
    d = doc(base, records)
    d['工程量结果'][2]['否定结论'] = {'结论': '图上无 消防广播', '检索记录': ['NS-P'], '清单量': None}
    assert d['工程量结果'][2]['状态'] == '证据不足'
    assert validate_document(d) == []


# ---------- counter-examples ----------

def test_hit_record_refutes_the_negative_conclusion(base, records):
    d = doc(base, records)
    zero_with_negative(d, ['NS-D', 'NS-A'])
    errors = validate_document(d)
    assert len(errors) == 1 and '有命中，否定结论不成立' in errors[0]


def test_pending_record_cannot_be_a_confirmed_zero(base, records):
    d = doc(base, records)
    item = zero_with_negative(d, ['NS-P'], bill=None)
    errors = validate_document(d)
    assert errors == [f'{item}的检索记录为待确认，不能写已确认零值', f'{item}的检索记录为待确认，成果状态只能是待确认或证据不足']


def test_negative_conclusion_needs_existing_records(base, records):
    d = doc(base, records)
    item = zero_with_negative(d, ['NS-D'])
    d['工程量结果'][0]['否定结论']['检索记录'] = []
    assert validate_document(d) == [f'{item}的否定结论没有检索记录，只能列为待确认的未决事项']
    d['工程量结果'][0]['否定结论'].update(检索记录=['NS-D', 'NS-X'], 结论='')
    assert validate_document(d) == [f'{item}的否定结论缺少结论原文', f'{item}引用的否定结论检索记录不存在: NS-X']
    d['工程量结果'][0]['否定结论'] = '图上无'
    assert validate_document(d) == [f'{item}.否定结论必须为对象或null']


def test_record_list_shape_and_duplicates(base, records):
    d = doc(base, records)
    d['否定结论检索记录'] = {'NS-D': records['NS-D']}
    assert validate_document(d) == ['否定结论检索记录必须是数组']
    d = doc(base, records, ids=('NS-D', 'NS-D'))
    d['否定结论检索记录'].append({'status': 'hit'})
    assert validate_document(d) == ['否定结论检索记录编号重复: NS-D', '否定结论检索记录[2]缺少 record_id']


@pytest.mark.parametrize('field,tamper,label', [
    ('结论', lambda r: r.update(conclusion=''), '结论'),
    ('检索图纸', lambda r: r.update(drawings_searched=[]), '检索图纸'),
    ('检索对象', lambda r: r.update(targets={'layers': ['布局文字']}), '检索对象'),
    ('方法', lambda r: r.pop('method'), '方法'),
    ('结果', lambda r: r['result'].update(hits=None), '结果'),
    ('结果-未读', lambda r: r['result'].pop('unread'), '结果'),
    ('未覆盖', lambda r: r.update(uncovered=None), '未覆盖'),
])
def test_record_must_carry_the_six_parts(base, records, field, tamper, label):
    d = doc(base, records)
    tamper(d['否定结论检索记录'][1])
    assert validate_document(d) == [f'否定结论检索记录NS-D缺少: {label}']


@pytest.mark.parametrize('tamper,fragment', [
    (lambda r: r.update(status='none'), '检索状态非法'),
    (lambda r: r.update(status='hit'), '检索状态与命中数不符'),
    (lambda r: r['result'].update(hits=2), '检索状态与命中数不符'),
    (lambda r: r.update(uncovered=[{'drawing': 'D9', 'reason': '没给中性取证包'}]), '检索状态只能是待确认'),
    (lambda r: r['result']['unread'].append({'drawing': 'D1', 'kind': '嵌入对象', 'layer': 'E-PLAN', 'class': 'may_contain', 'count': 1}), '检索状态只能是待确认'),
])
def test_record_status_must_match_what_was_found(base, records, tamper, fragment):
    d = doc(base, records)
    tamper(d['否定结论检索记录'][1])
    errors = validate_document(d)
    assert len(errors) == 1 and errors[0].startswith('否定结论检索记录NS-D') and fragment in errors[0]


def test_bill_quantity_needs_design_notes_searched(base, records):
    d = doc(base, records)
    item = zero_with_negative(d, ['NS-D'], bill=3)
    d['否定结论检索记录'][1]['sheet_kinds_searched'] = ['平面', '系统图']
    assert validate_document(d) == [f'{item}清单有量而判图上无，检索记录须检索过表与说明类图页（设计说明）']
    for bill in (0, None):
        d['工程量结果'][0]['否定结论']['清单量'] = bill
        assert validate_document(d) == []
    d['工程量结果'][0]['否定结论']['清单量'] = '3'
    assert validate_document(d) == [f'{item}的否定结论清单量必须是数或null']
