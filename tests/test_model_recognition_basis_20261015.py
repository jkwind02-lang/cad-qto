"""Direct contract tests for model_recognition_basis (working-recognition ledger); synthetic only.

Registered 20261014-2154: the module was only covered indirectly through the point/pipe CLI chains.
Covers: a legal ledger loads; source-hash, evidence-hash, human-field, decision and evidence-kind
guards reject; recognition_basis output passes validate_working_basis; escalation is refused.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
from model_recognition_basis import (SCHEMA, applies_to, file_sha, load_working_entries,
                                     recognition_basis, validate_working_basis)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def ledger(tmp_path):
    source = tmp_path / '合成中性图元.jsonl'
    source.write_text('{"record_id":"P1"}\n{"record_id":"P2"}\n', encoding='utf-8')
    source_sha = file_sha(source)
    evidence = tmp_path / '证据' / '截图说明.txt'
    evidence.parent.mkdir()
    evidence.write_text('合成证据：P1 与 P2 相连', encoding='utf-8')
    data = {
        'schema_version': SCHEMA, 'ledger_id': 'MR-TEST', 'ledger_version': '1', 'source_sha256': source_sha,
        'adoption_scope': 'working', 'review_status': 'unreviewed',
        'evidence': [{'evidence_id': 'EV-1', 'path': '证据/截图说明.txt', 'sha256': file_sha(evidence),
                      'source_sha256': source_sha, 'kind': 'cad_property', 'locator': 'Model / P1 P2',
                      'observation': '合成单元测试对象属性；非真实项目识图证据'}],
        'entries': [{'adjudication_id': 'MR-R1', 'subject_id': 'R1', 'decision': 'include',
                     'professional_owner': 'qto-pipe-networks', 'status': 'active', 'decision_origin': 'model',
                     'recognized_by': 'synthetic-test-agent', 'recognized_at': '2026-10-15', 'source_id': 'SYN-1',
                     'evidence_ids': ['EV-1'], 'object_ids': ['P1', 'P2'], 'basis': '合成测试：两个相连已识别段',
                     'scope_basis': '本合成图 Model 范围', 'identity_basis': '稳定 ID', 'cross_check': '连接关系检查',
                     'uncertainties': []}],
    }
    path = tmp_path / '模型识别记录.json'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path, data, source_sha


def test_legal_ledger_loads_and_stays_working(ledger):
    path, data, source_sha = ledger
    loaded, entries = load_working_entries(data, path, source_sha.upper())
    assert set(entries) == {'MR-R1'}
    assert entries['MR-R1']['adoption_scope'] == 'working' and entries['MR-R1']['review_status'] == 'unreviewed'
    assert applies_to({'authority': {'decision_origin': 'model', 'object_ids': ['P1']}}, 'P1')
    assert not applies_to({'authority': {'decision_origin': 'model', 'object_ids': ['P1']}}, 'U1')
    assert applies_to({'authority': {'decision_origin': 'human'}}, 'U1')


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d.update(source_sha256='0' * 64), '来源哈希不一致'),
    (lambda d: d['evidence'][0].update(sha256='0' * 64), '证据文件哈希不一致'),
    (lambda d: d['evidence'][0].update(source_sha256='0' * 64), '未绑定本轮来源'),
    (lambda d: d['evidence'][0].update(kind='hearsay'), 'kind 无效'),
    (lambda d: d.update(review_status='human_approved'), '不是人工核定'),
    (lambda d: d['entries'][0].update(confirmed_by='某人'), '不得填写人工核定字段'),
    (lambda d: d['entries'][0].update(decision_origin='human'), '实际 model 识别来源'),
    (lambda d: d['entries'][0].update(decision='maybe'), 'decision 无效'),
    (lambda d: d['entries'][0].update(evidence_ids=['EV-9']), '未登记证据'),
    (lambda d: d['entries'][0].update(object_ids=['P1', 'P1']), '有重复值'),
    (lambda d: d['entries'].append(copy.deepcopy(d['entries'][0])), '识别记录编号重复'),
])
def test_guards_reject_tampered_or_escalated_ledgers(ledger, mutate, message):
    path, data, source_sha = ledger
    broken = copy.deepcopy(data)
    mutate(broken)
    with pytest.raises(ValueError, match=message):
        load_working_entries(broken, path, source_sha)


def test_project_document_only_evidence_is_not_recognition(ledger):
    path, data, source_sha = ledger
    broken = copy.deepcopy(data)
    broken['evidence'][0]['kind'] = 'project_document'
    with pytest.raises(ValueError, match='缺图面或对象属性识别证据'):
        load_working_entries(broken, path, source_sha)


def test_recognition_basis_round_trips_and_cannot_escalate(ledger):
    path, data, _ = ledger
    basis = recognition_basis(data, path)
    assert basis['ledger_sha256'] == file_sha(path) and basis['recognized_by'] == ['synthetic-test-agent']
    assert basis['formal_adoption_allowed'] is False
    validate_working_basis(basis)
    validate_working_basis(None)
    for key, value in (('review_status', 'human_approved'), ('adoption_scope', 'formal'),
                       ('formal_adoption_allowed', True), ('decision_origin', 'human')):
        escalated = dict(basis, **{key: value})
        with pytest.raises(ValueError, match='不得升级'):
            validate_working_basis(escalated)
    with pytest.raises(ValueError, match='64位小写'):
        validate_working_basis(dict(basis, ledger_sha256=basis['ledger_sha256'].upper()))
    assert recognition_basis({'schema_version': 'other'}, path) is None
