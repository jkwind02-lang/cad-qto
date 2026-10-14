"""Rebar calculator: explicit-schedule equivalent case and failure guards; synthetic only.

Registered 20261014-2154: the repository had no rebar calculator test. Rewritten from the legacy
`_qto-shared-evidence/tests/qto-rebar/run_failure_cases.py` cases B01, C01, B02, S01, S02 to the
current contract; the legacy C02/C03 cases depended on a real project inventory and are not carried
over. The equivalent case proves count / developed length / theoretical weight conservation only.
"""
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALCULATOR = ROOT / 'skills/qto-rebar/scripts/calculate_rebar_items.py'
FIXTURES = Path(__file__).resolve().parent / 'fixtures/rebar_equivalent'


@pytest.fixture
def manifest(tmp_path):
    facts = tmp_path / '等效钢筋表事实.json'
    facts.write_bytes((FIXTURES / '等效钢筋表事实.json').read_bytes())
    digest = hashlib.sha256(facts.read_bytes()).hexdigest()
    text = (FIXTURES / '等效钢筋表清单.json').read_text(encoding='utf-8-sig')
    stale = json.loads(text)['sources'][0]['sha256']
    data = json.loads(text.replace(stale, digest))
    data['sources'][0]['path'] = str(facts)
    return data


def run(tmp_path, manifest, case='case', output_root=None):
    path = tmp_path / f'{case}.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    out = output_root or tmp_path / case
    completed = subprocess.run(
        [sys.executable, '-B', str(CALCULATOR), str(path), '--result-out', str(out / 'result.json'),
         '--evidence-out', str(out / 'evidence.json'), '--detail-out', str(out / 'detail.csv')],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    return completed, out


def test_explicit_schedule_conserves_count_length_and_weight(tmp_path, manifest):
    completed, out = run(tmp_path, manifest)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((out / 'result.json').read_text(encoding='utf-8'))
    items = {item['成果项编号']: item for item in result['工程量结果']}
    unit_mass = math.pi / 4 * 0.014 ** 2 * 7850  # kg/m, HRB400 d=14 geometric density
    for axis in ('X', 'Y'):
        assert items[f'QTY-RB-EQ-{axis}-COUNT']['结果']['数值'] == 8
        assert items[f'QTY-RB-EQ-{axis}-LENGTH']['结果']['数值'] == pytest.approx(8 * 1.22, rel=1e-6)
        assert items[f'QTY-RB-EQ-{axis}-WEIGHT']['结果']['数值'] == pytest.approx(8 * 1.22 * unit_mass, rel=1e-3)
        for kind in ('COUNT', 'LENGTH', 'WEIGHT'):
            item = items[f'QTY-RB-EQ-{axis}-{kind}']
            assert item['状态'] == '已计算' and item['试运行结果'] is True
            assert item['采用角色'] == 'reference' and item['结果用途'] == 'reference_only'
    assert (out / 'evidence.json').is_file() and (out / 'detail.csv').is_file()


def mutate_missing_length(data):
    data['bar_groups'][0].pop('developed_length_each_m')


def mutate_duplicate_identity(data):
    cloned = copy.deepcopy(data['bar_groups'][0])
    cloned['group_id'] = 'RB-EQ-DJ01-B-X-ALIAS'
    cloned['outputs'] = {'count': 'QTY-RB-EQ-X2-COUNT', 'length': 'QTY-RB-EQ-X2-LENGTH', 'weight': 'QTY-RB-EQ-X2-WEIGHT'}
    data['bar_groups'].append(cloned)


def mutate_zero_without_evidence(data):
    data['bar_groups'][0]['count'] = 0


def mutate_bad_hash(data):
    data['sources'][0]['sha256'] = '0' * 64


def mutate_candidate_rule_adopted(data):
    data['rules'][0]['maturity'] = 'candidate'
    data['rules'][0]['decision'] = 'adopted'


@pytest.mark.parametrize('case_id, mutate, expected', [
    ('B01', mutate_missing_length, 'developed_length_each_m 必须是有限数字'),
    ('C01', mutate_duplicate_identity, '物理身份重复'),
    ('B02', mutate_zero_without_evidence, '根数为 0 时必须提供 zero_evidence'),
    ('S01', mutate_bad_hash, '来源哈希不匹配'),
], ids=['missing_length', 'duplicate_identity', 'zero_without_evidence', 'bad_hash'])
def test_guards_reject_with_return_code_2(tmp_path, manifest, case_id, mutate, expected):
    broken = copy.deepcopy(manifest)
    mutate(broken)
    completed, out = run(tmp_path, broken, case_id)
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert expected in completed.stdout + completed.stderr
    assert not (out / 'result.json').exists(), 'a rejected manifest must not leave a result file'


def test_output_inside_readonly_root_is_refused(tmp_path, manifest):
    readonly_root = tmp_path / 'readonly-project'
    readonly_root.mkdir()
    guarded = copy.deepcopy(manifest)
    guarded['readonly_roots'] = [str(readonly_root.resolve())]
    completed, _ = run(tmp_path, guarded, 'S02', output_root=readonly_root)
    assert completed.returncode == 2
    assert '输出路径位于只读项目根内' in completed.stdout + completed.stderr
    assert not any(readonly_root.iterdir()), 'nothing may be written under a read-only project root'
