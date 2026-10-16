"""覆盖台账核对零实例图例行的检索记录（第四批乙 c，备课“覆盖台账校验 search_record_id 在检索记录文件里存在，且字段齐全”）。

检索记录由 search_negative_evidence.py 在合成中性取证包上真跑得到：NS-A 有命中、NS-D 全覆盖未命中、NS-P 待确认（漏图）。
"""
import copy, importlib.util, json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


cov = load('cov_for_search', ROOT / 'tests' / 'test_point_coverage_ledger_20261016.py')
ns = load('ns_for_ledger', ROOT / 'tests' / 'test_negative_search_20261016.py')


@pytest.fixture(scope='module')
def search_files(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('search')
    _, _, _, full = ns.run(tmp / 'full')
    item = {'item_id': 'P', 'conclusion': '图上无 消防广播', 'kind': 'text', 'term': '消防广播'}
    _, _, _, part = ns.run(tmp / 'part', items=[item], drawing_set=['D1', 'D9'])
    full, part = full / '否定结论检索记录.json', part / '否定结论检索记录.json'
    status = {r['record_id']: r['status'] for p in (full, part) for r in json.loads(p.read_text(encoding='utf-8'))['records']}
    assert status['NS-A'] == 'hit' and status['NS-D'] == 'no_hit_full' and status['NS-P'] == 'no_hit_partial'
    return [str(full), str(part)]


def zero_row(tmp, search_id, **extra):
    cov.write(tmp / 'legend.csv', cov.FIELDS, [['D0', 'Z1', '', '测试图符', '<HATCH>', '非块', '0', '', '', '不计', '合成：本图未见', '', search_id]])
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'drawings': [{'drawing': 'D0'}], 'legend_coverage': 'legend.csv', **extra}
    return cov.ledger(tmp, data)


def pending(report):
    return {r['legend_row']: r['reason'] for r in report['legend_rows_without_destination']}


# ---------- legal controls ----------

def test_full_coverage_record_confirms_the_zero_row(tmp_path, search_files):
    done, report, _ = zero_row(tmp_path, 'NS-D', search_records=search_files)
    assert done.returncode == 0, done.stderr
    assert pending(report) == {} and report['status'] == 'COMPLETE'
    assert report['search_records'] == {'given': True, 'records': 5} and 'search_record_ids_not_checked' not in report['warnings']
    assert [f['role'] for f in report['files']].count('search_records') == 2


def test_without_search_records_the_id_is_only_checked_for_presence_and_flagged(tmp_path):
    done, report, _ = zero_row(tmp_path, 'NS-ANY')
    assert done.returncode == 0 and report['status'] == 'COMPLETE' and report['search_records'] == {'given': False, 'records': 0}
    assert report['warnings']['search_record_ids_not_checked'] == [{'drawing': 'D0', 'legend_row': 'Z1', 'search_record_id': 'NS-ANY'}]


# ---------- counter-examples ----------

@pytest.mark.parametrize('search_id,fragment', [
    ('NS-A', '检索记录 NS-A 有命中'),
    ('NS-P', '检索记录 NS-P 为待确认'),
    ('NS-X', '检索记录 NS-X 不在 search_records 里'),
    ('NS-D；NS-A', '检索记录 NS-A 有命中'),          # every listed record must confirm the zero
])
def test_zero_row_record_must_exist_and_confirm_the_zero(tmp_path, search_files, search_id, fragment):
    done, report, _ = zero_row(tmp_path, search_id, search_records=search_files)
    assert done.returncode == 0, done.stderr
    assert fragment in pending(report)['Z1'] and report['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('tamper,fragment', [
    (lambda r: r.pop('method'), '缺少: 方法'),
    (lambda r: r.update(drawings_searched=[]), '缺少: 检索图纸'),
    (lambda r: r.update(status='hit'), '检索状态与命中数不符'),
    (lambda r: r.update(uncovered=[{'drawing': 'D9', 'reason': '没给中性取证包'}]), '检索状态只能是待确认'),
])
def test_zero_row_record_must_be_complete(tmp_path, search_files, tamper, fragment):
    data = json.loads(Path(search_files[0]).read_text(encoding='utf-8'))
    tamper(next(r for r in data['records'] if r['record_id'] == 'NS-D'))
    bad = tmp_path / 'bad.json'; bad.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    done, report, _ = zero_row(tmp_path, 'NS-D', search_records=str(bad))
    assert done.returncode == 0, done.stderr
    assert fragment in pending(report)['Z1'] and report['status'] == 'INCOMPLETE'


def test_bad_search_record_files_are_input_problems(tmp_path, search_files):
    data = json.loads(Path(search_files[0]).read_text(encoding='utf-8'))
    wrong = copy.deepcopy(data); wrong['schema'] = 'qto-negative-search/0.9'
    (tmp_path / 'wrong.json').write_text(json.dumps(wrong, ensure_ascii=False), encoding='utf-8')
    dup = copy.deepcopy(data); dup['records'].append(copy.deepcopy(dup['records'][0])); dup['records'].append({'status': 'hit'})
    (tmp_path / 'dup.json').write_text(json.dumps(dup, ensure_ascii=False), encoding='utf-8')
    done, report, _ = zero_row(tmp_path, 'NS-D', search_records=['missing.json', 'wrong.json', 'dup.json'])
    assert done.returncode == 2 and report is None
    problems = json.loads(done.stderr)['problems']
    assert [p['check'] for p in problems] == ['C-7'] * 4
    messages = ' | '.join(p['message'] for p in problems)
    for fragment in ('检索记录文件不存在', '须是 qto-negative-search/1.0', '检索记录编号重复：NS-A', '缺少 record_id'):
        assert fragment in messages
