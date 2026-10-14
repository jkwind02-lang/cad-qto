"""Independent Codex C4 cases: actual recompute CLI, synthetic oracle, no Claude fixture helper."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/_qto-shared-evidence/scripts/recompute_qto_dependencies.py'
STAMP = '2026-10-14T21:00:00+08:00'


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def graph():
    return {'schema': 'qto-calculation-graph/1.0', 'project_id': 'codex-independent-C4',
            'sources': [{'id': 'drawing', 'sha256': '1'*64}],
            'rules': [{'id': 'addition', 'sha256': '2'*64}],
            'nodes': [{'id': 'A', 'unit': 'm', 'value': 12, 'source_ids': ['drawing']},
                      {'id': 'B', 'unit': 'm', 'value': 4, 'source_ids': ['drawing']},
                      {'id': 'SUM', 'unit': 'm', 'expression': 'a+b',
                       'variables': {'a': 'A', 'b': 'B'}, 'rule_ids': ['addition']}]}


def run(tmp, data, tag, previous=None):
    source, output = tmp/f'{tag}_input.json', tmp/f'{tag}_output.json'
    put(source, data);before = source.read_bytes()
    args = [sys.executable, '-B', str(SCRIPT), '--input', str(source), '--output', str(output)]
    if previous:
        args += ['--previous', str(previous)]
    result = subprocess.run(args, cwd=tmp.parent, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout+result.stderr
    assert source.read_bytes() == before
    value = json.loads(output.read_text(encoding='utf-8'))
    rows = {n['id']: n for n in value['nodes']}
    assert rows['SUM']['value'] == data['nodes'][0]['value']+4
    assert value['business_adoption_allowed'] is False
    assert all(n['official_adoption'] is False for n in value['nodes'])
    return value, rows, output


def approve(tmp, data, rows):
    confirmations = [{'node_id': n['id'], 'calculation_fingerprint': n['fingerprint'],
                      'review_object_key': 'drawing-object-'+n['id'], 'result_fingerprint': '3'*64,
                      'quantity': n['value'], 'unit': 'm'} for n in rows.values()]
    annotation = {'decision_type': 'quantity_confirmation', 'reviewer': 'Wang Kai',
                  'reviewed_at': STAMP, 'updated_at': STAMP, 'quantity_confirmations': confirmations}
    fb = tmp/'saved_feedback.json'
    fbhash = put(fb, {'schema': 'qto-review-feedback/2.0', 'project_id': data['project_id'],
                      'annotations': {'selected': annotation}})
    records = [dict(c, record_id=c['node_id'], reviewer='Wang Kai', reviewer_type='human',
                    reviewed_at=STAMP, decision='confirm_quantity',
                    source_feedback={'path': fb.name, 'sha256': fbhash, 'annotation_key': 'selected',
                                     'saved_at': STAMP}) for c in confirmations]
    evidence = tmp/'human_evidence.json'
    digest = put(evidence, {'schema': 'qto-human-review-evidence/1.0', 'project_id': data['project_id'],
                            'records': records})
    for node in data['nodes']:
        node['approval'] = {'fingerprint': rows[node['id']]['fingerprint'], 'reviewer': 'Wang Kai',
                            'reviewer_type': 'human', 'reviewed_at': STAMP,
                            'evidence': {'path': evidence.name, 'sha256': digest, 'record_id': node['id']}}
    return fb, evidence


def test_independent_cli_valid_inherited_and_transitive_stale(tmp_path):
    data = graph();_, rows, _ = run(tmp_path, data, 'unreviewed')
    approve(tmp_path, data, rows)
    first, rows, previous = run(tmp_path, data, 'confirmed')
    assert {n['review_state'] for n in rows.values()} == {'current'}
    for node in data['nodes']:
        node.pop('approval')
    _, rows, inherited = run(tmp_path, data, 'inherited', previous)
    assert {n['review_state'] for n in rows.values()} == {'current'}
    data['nodes'][0]['value'] = 13
    result, rows, _ = run(tmp_path, data, 'changed', inherited)
    assert result['status'] == 'REVIEW_REQUIRED' and set(result['stale_reviews']) == {'A', 'SUM'}
    assert rows['B']['review_state'] == 'current' and rows['SUM']['value'] == 17


@pytest.mark.parametrize('fault', ['records-type', 'annotations-type', 'confirmations-type',
                                 'review-time', 'saved-time', 'duplicate', 'bad-result-fingerprint',
                                 'overflow-quantity'])
def test_independent_cli_invalid_evidence_keeps_computation(tmp_path, fault):
    data = graph();_, rows, _ = run(tmp_path, data, 'base')
    fb, ev = approve(tmp_path, data, rows)
    feedback = json.loads(fb.read_text(encoding='utf-8'))
    evidence = json.loads(ev.read_text(encoding='utf-8'))
    annotation = feedback['annotations']['selected']
    if fault == 'records-type':evidence['records'] = 7
    elif fault == 'annotations-type':feedback['annotations'] = ['invalid']
    elif fault == 'confirmations-type':annotation['quantity_confirmations'] = 7
    elif fault == 'review-time':annotation['reviewed_at'] = '2026-10-14T20:00:00+08:00'
    elif fault == 'saved-time':annotation['updated_at'] = '2026-10-14T20:00:00+08:00'
    elif fault == 'duplicate':annotation['quantity_confirmations'] *= 2
    elif fault == 'overflow-quantity':
        for row in evidence['records']:row['quantity'] = 10**1000
        for row in annotation['quantity_confirmations']:row['quantity'] = 10**1000
    else:
        for row in evidence['records']:row['result_fingerprint'] = 'not-a-fingerprint'
        for row in annotation['quantity_confirmations']:row['result_fingerprint'] = 'not-a-fingerprint'
    fbhash = put(fb, feedback)
    if isinstance(evidence['records'], list):
        for row in evidence['records']:row['source_feedback']['sha256'] = fbhash
    evhash = put(ev, evidence)
    for node in data['nodes']:node['approval']['evidence']['sha256'] = evhash
    result, rows, _ = run(tmp_path, data, 'invalid')
    assert result['status'] == 'REVIEW_REQUIRED' and len(result['approval_issues']) == 3
    assert all(n['review_state'] == 'unreviewed' for n in rows.values())


def test_independent_cli_invalid_inherited_reference_and_model_actor(tmp_path):
    data = graph();previous, rows, output = run(tmp_path, data, 'base')
    previous['nodes'][0].update(review_state='current', review_evidence='not-an-object')
    put(output, previous)
    result, rows, _ = run(tmp_path, data, 'forged_previous', output)
    assert rows['A']['review_state'] == 'unreviewed' and result['approval_issues']
    data['nodes'][0]['approval'] = {'fingerprint': rows['A']['fingerprint'], 'actor': 'Claude'}
    result, rows, _ = run(tmp_path, data, 'legacy_actor')
    assert rows['A']['review_state'] == 'unreviewed' and result['approval_issues']


def test_unreadable_evidence_is_reported_instead_of_raising(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPT.parent))
    import qto_human_review_evidence as evidence
    path=tmp_path/'evidence.json';put(path, {})
    def denied(self):raise PermissionError('synthetic inaccessible evidence')
    monkeypatch.setattr(Path, 'read_bytes', denied)
    issues=[]
    assert evidence._load_hashed({'path':path.name,'sha256':'0'*64},tmp_path,'凭据',issues)==(None,None)
    assert '无法读取' in issues[0]
