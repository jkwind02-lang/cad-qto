"""Codex 复测 R1：参照图（role: context）上的未读对象不改变本范围检索状态，检索器和两处记录消费者判据一致。

仿 Codex 独立输入“参照未读”：本范围图 D1 完整读取、没有命中、有“表与说明”图页；参照图 D2 上有一张图片（IMAGE，
图层 E-NOTE，可能含图面内容）。期望：检索器出全覆盖未命中，成果校验器和覆盖台账都接受这条记录；
合法对照：同一张图片画在本范围图上照旧待确认；旧记录（未读条目没有角色）和角色对不上的记录照旧阻断。
"""
import copy, importlib.util, json, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
import search_negative_evidence as sne  # noqa: E402
record_problems = sne.record_problems


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


ns = load('ns_for_context_unread', ROOT / 'tests' / 'test_negative_search_20261016.py')
gct = load('gct_for_context_unread', ROOT / 'tests' / 'test_general_capability_transfer.py')
cov = load('cov_for_context_unread', ROOT / 'tests' / 'test_point_coverage_ledger_20261016.py')
ITEM = {'item_id': 'Q', 'kind': 'text', 'term': '消防广播', 'conclusion': '本范围图上无 消防广播'}
PICTURE = ns.rec('IM1', 'IMAGE', 'E-NOTE', 700, 700)


def search(tmp, picture_on):
    d1 = ns.package(tmp, 'D1', layout=ns.LAYOUT + ([PICTURE] if picture_on == 'scope' else []))
    d2 = {**ns.package(tmp, 'D2', layout=ns.LAYOUT + ([PICTURE] if picture_on == 'context' else [])), 'group': '参照', 'role': 'context'}
    code, _, data, out = ns.run(tmp, items=[ITEM], drawings=[d1, d2])
    assert code == 0
    return data['records'][0], out / '否定结论检索记录.json'


@pytest.fixture(scope='module')
def context_record(tmp_path_factory):
    return search(tmp_path_factory.mktemp('ctx'), 'context')


def test_context_unread_object_is_listed_but_full_coverage_stands(context_record):
    rec, _ = context_record
    assert rec['status'] == 'no_hit_full' and rec['status_reasons'] == []
    assert {'drawing': 'D2', 'role': 'context', 'kind': '图片', 'layer': 'E-NOTE', 'class': 'may_contain', 'count': 1} in rec['result']['unread']
    assert rec['drawing_roles'] == {'D1': 'scope', 'D2': 'context'} and '表与说明' in rec['sheet_kinds_searched']
    assert record_problems(rec) == []


def test_scope_unread_object_still_blocks(tmp_path):
    rec, _ = search(tmp_path, 'scope')
    assert rec['status'] == 'no_hit_partial' and rec['status_reasons'] == ['可能含图面内容的未读对象 1 个']
    full = copy.deepcopy(rec); full['status'] = 'no_hit_full'
    assert record_problems(full) == ['有未覆盖的图或可能含图面内容的未读对象，检索状态只能是待确认']


def test_old_or_inconsistent_records_still_block(context_record):
    rec, _ = context_record
    old = copy.deepcopy(rec)
    old.pop('drawing_roles')
    for u in old['result']['unread']:
        u.pop('role')                      # written before 1.0.1: no role, counts as scope
    assert record_problems(old) == ['有未覆盖的图或可能含图面内容的未读对象，检索状态只能是待确认']
    forged = copy.deepcopy(rec)
    forged['drawing_roles']['D2'] = 'scope'   # the entry says context, the record says the drawing is in scope
    assert record_problems(forged) == ['有未覆盖的图或可能含图面内容的未读对象，检索状态只能是待确认']
    assert sne.blocking_unread([{'drawing': 'D2', 'role': 'context', 'class': 'may_contain'}]) == []
    assert sne.blocking_unread([{'drawing': 'D2', 'role': 'context', 'class': 'may_contain'}], {'D9': 'context'}) != []


def test_validator_cli_accepts_a_zero_backed_by_the_record(context_record, tmp_path):
    rec, _ = context_record
    _, _, result = gct.cf.calculate(gct.concrete_manifest(tmp_path / 'cf'))
    q = result['工程量结果'][0]
    q['结果']['数值'] = 0
    q['零值复核'] = {'复核结论': '已确认零值', '来源': list(q['来源']), '规则': list(q['规则']),
                  '范围覆盖': '本范围图 D1 全部检索', '说明': '检索记录 NS-Q 全覆盖未命中；参照图 D2 的图片只列出'}
    q['否定结论'] = {'结论': '本范围图上无 消防广播', '检索记录': ['NS-Q'], '清单量': 2}
    result['否定结论检索记录'] = [rec]
    assert gct.validate_document(result) == []
    p = tmp_path / 'result.json'; p.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    r = subprocess.run([sys.executable, str(ROOT / 'skills/_qto-shared-evidence/scripts/validate_minimum_result.py'), str(p)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert r.returncode == 0 and json.loads(r.stdout)['status'] == 'PASS', r.stdout


def test_coverage_ledger_gives_the_zero_row_a_destination(context_record, tmp_path):
    _, path = context_record
    cov.write(tmp_path / 'legend.csv', cov.FIELDS, [['D0', 'Z1', '', '消防广播', '<HATCH>', '非块', '0', '', '', '不计', '合成：本范围未见', '', 'NS-Q']])
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'drawings': [{'drawing': 'D0'}], 'legend_coverage': 'legend.csv',
            'search_records': str(path)}
    done, report, _ = cov.ledger(tmp_path, data)
    assert done.returncode == 0, done.stderr
    assert report['status'] == 'COMPLETE' and report['legend_rows_without_destination'] == []
