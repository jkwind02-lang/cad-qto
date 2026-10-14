"""Claude independent cross-review of Codex patch e11d9b4 (review evidence and inheritance).

Built from docs/维护/审批凭据契约.md and the review page's annotation timestamps
(review-core.js stores updated_at with toISOString), not from the Codex or batch-1
test helpers. Every rejection asserts its own reason, so a case cannot pass on an
unrelated failure, and every run checks the computed quantities stay intact.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/_qto-shared-evidence/scripts/recompute_qto_dependencies.py'
EXPORTER = ROOT / 'skills/_qto-shared-evidence/scripts/export_dwg_exchange_dxf.ps1'
PROJECT = 'claude-cross-review'
SAVED = '2026-10-14T13:05:09.123Z'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def graph():
    return {'schema': 'qto-calculation-graph/1.0', 'project_id': PROJECT,
            'sources': [{'id': 'S', 'sha256': 'a' * 64}], 'rules': [{'id': 'R', 'sha256': 'b' * 64}],
            'nodes': [{'id': 'L', 'unit': 'm', 'value': 12, 'source_ids': ['S']},
                      {'id': 'D', 'unit': 'm', 'expression': 'l*2', 'variables': {'l': 'L'}, 'rule_ids': ['R']}]}


def recompute(folder, data, name, previous=None):
    source, output = folder / f'{name}.json', folder / f'{name}_run.json'
    dump(source, data)
    command = [sys.executable, '-B', str(SCRIPT), '--input', str(source), '--output', str(output)]
    if previous:
        command += ['--previous', str(previous)]
    proc = subprocess.run(command, cwd=folder, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding='utf-8')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run = json.loads(output.read_text(encoding='utf-8'))
    rows = {row['id']: row for row in run['nodes']}
    assert rows['L']['value'] == 12 and rows['D']['value'] == 24
    assert run['business_adoption_allowed'] is False
    return run, rows, output


def issues_of(run, node='L'):
    return [text for item in run['approval_issues'] if item['node_id'] == node for text in item['issues']]


def approved(folder, mutate=None):
    """Approve node L with one saved annotation and one evidence record; mutate before hashing."""
    data = graph()
    _, rows, _ = recompute(folder, data, 'plain')
    fingerprint = rows['L']['fingerprint']
    confirmation = {'node_id': 'L', 'calculation_fingerprint': fingerprint, 'review_object_key': 'pipe-run-07',
                    'result_fingerprint': 'c' * 64, 'quantity': 12, 'unit': 'm'}
    annotation = {'decision_type': 'quantity_confirmation', 'reviewer': '李工', 'reviewed_at': SAVED,
                  'updated_at': SAVED, 'quantity_confirmations': [dict(confirmation)]}
    feedback = {'schema': 'qto-review-feedback/2.0', 'project_id': PROJECT, 'annotations': {'k1': annotation}}
    record = dict(confirmation, record_id='r1', reviewer='李工', reviewer_type='human', reviewed_at=SAVED,
                  decision='confirm_quantity',
                  source_feedback={'path': 'feedback.json', 'annotation_key': 'k1', 'saved_at': SAVED})
    evidence = {'schema': 'qto-human-review-evidence/1.0', 'project_id': PROJECT, 'records': [record]}
    approval = {'fingerprint': fingerprint, 'reviewer': '李工', 'reviewer_type': 'human', 'reviewed_at': SAVED,
                'evidence': {'path': 'evidence.json', 'record_id': 'r1'}}
    parts = {'feedback': feedback, 'annotation': annotation, 'record': record, 'evidence': evidence}
    if mutate:
        mutate(parts)
    record['source_feedback'].setdefault('sha256', dump(folder / 'feedback.json', parts['feedback']))
    approval['evidence']['sha256'] = dump(folder / 'evidence.json', parts['evidence'])
    data['nodes'][0]['approval'] = approval
    run, rows, output = recompute(folder, data, 'approved')
    return data, run, rows, output


def set_both(field, value):
    def mutate(parts):
        parts['record'][field] = value
        parts['annotation']['quantity_confirmations'][0][field] = value
    return mutate


POSITIVE = {
    'baseline': None,
    'no-updated-at-falls-back-to-reviewed-at': lambda p: p['annotation'].pop('updated_at'),
    'float-equal-quantity': set_both('quantity', 12.0),
    'unrelated-records-and-confirmations': lambda p: (
        p['evidence']['records'].extend(['junk', {'record_id': 'r2'}]),
        p['annotation']['quantity_confirmations'].append({'node_id': 'OTHER'})),
}


@pytest.mark.parametrize('case', list(POSITIVE))
def test_valid_evidence_is_still_accepted(tmp_path, case):
    _, run, rows, _ = approved(tmp_path, POSITIVE[case])
    assert issues_of(run) == []
    assert rows['L']['review_state'] == 'current' and run['status'] == 'CALCULATED'
    assert rows['L']['review_evidence']['record_id'] == 'r1'


NEGATIVE = {
    'annotations-list': (lambda p: p['feedback'].update(annotations=[p['annotation']]), 'annotations 必须是对象'),
    'annotations-missing': (lambda p: p['feedback'].pop('annotations'), 'annotations 必须是对象'),
    'confirmations-object': (lambda p: p['annotation'].update(
        quantity_confirmations=p['annotation']['quantity_confirmations'][0]), 'quantity_confirmations 必须是数组'),
    'annotation-reviewed-at-differs': (lambda p: p['annotation'].update(reviewed_at='2026-10-14T13:05:10.123Z'),
                                       '原批注的复核时间与凭据记录不一致'),
    'saved-at-differs-from-updated-at': (lambda p: p['annotation'].update(updated_at='2026-10-14T13:06:00.000Z'),
                                         '保存时间与 source_feedback.saved_at 不一致'),
    # Same instant, other notation: the binding is a verbatim copy, not a time comparison.
    'saved-at-same-instant-other-notation': (
        lambda p: p['record']['source_feedback'].update(saved_at='2026-10-14T21:05:09.123+08:00'),
        '保存时间与 source_feedback.saved_at 不一致'),
    'updated-at-null': (lambda p: p['annotation'].update(updated_at=None), '保存时间与 source_feedback.saved_at 不一致'),
    'saved-at-missing': (lambda p: p['record']['source_feedback'].pop('saved_at'), 'saved_at 必须是带时区'),
    'duplicate-confirmation': (lambda p: p['annotation']['quantity_confirmations'].append(
        dict(p['annotation']['quantity_confirmations'][0])), '同一数量确认重复'),
    'result-fingerprint-short': (set_both('result_fingerprint', 'c' * 63), '完整对象 result_fingerprint'),
    'result-fingerprint-not-hex': (set_both('result_fingerprint', 'g' * 64), '完整对象 result_fingerprint'),
    'records-object': (lambda p: p['evidence'].update(records={'r1': p['record']}), 'records 必须是数组'),
    'huge-int-quantity': (set_both('quantity', 10 ** 400), '数量与节点当前值不一致'),
    'nul-in-feedback-path': (lambda p: p['record']['source_feedback'].update(path='feed\x00back.json'), '原批注'),
    'feedback-path-is-directory': (lambda p: p['record']['source_feedback'].update(path='.', sha256='d' * 64),
                                   '原批注文件不存在'),
}


@pytest.mark.parametrize('case', list(NEGATIVE))
def test_invalid_evidence_is_reported_for_its_own_reason(tmp_path, case):
    mutate, reason = NEGATIVE[case]
    _, run, rows, _ = approved(tmp_path, mutate)
    assert rows['L']['review_state'] == 'unreviewed' and rows['L']['review_evidence'] is None
    assert run['status'] == 'REVIEW_REQUIRED'
    assert any(reason in text for text in issues_of(run)), issues_of(run)


def previous_with(tmp_path, forged):
    data, _, rows, output = approved(tmp_path)
    assert rows['L']['review_state'] == 'current'
    previous = json.loads(output.read_text(encoding='utf-8'))
    for row in previous['nodes']:
        if row['id'] == 'L' and forged is not KEEP:
            row['review_evidence'] = forged
    path = tmp_path / 'previous.json'
    dump(path, previous)
    data['nodes'][0].pop('approval')
    return data, path


KEEP = object()


def test_inherited_valid_evidence_stays_current(tmp_path):
    data, previous = previous_with(tmp_path, KEEP)
    run, rows, _ = recompute(tmp_path, data, 'inherit', previous)
    assert rows['L']['review_state'] == 'current' and issues_of(run) == []


def test_inherited_evidence_rechecks_the_saved_annotation(tmp_path):
    data, previous = previous_with(tmp_path, KEEP)
    feedback = tmp_path / 'feedback.json'
    feedback.write_bytes(feedback.read_bytes() + b' ')
    run, rows, _ = recompute(tmp_path, data, 'inherit', previous)
    assert rows['L']['review_state'] == 'unreviewed'
    assert any('原批注哈希与登记不一致' in text for text in issues_of(run)), issues_of(run)


@pytest.mark.parametrize('forged', [None, {}, [], ['evidence.json'], 'evidence.json', 7, True])
def test_inherited_current_needs_a_reference_object(tmp_path, forged):
    data, previous = previous_with(tmp_path, forged)
    run, rows, _ = recompute(tmp_path, data, 'inherit', previous)
    assert rows['L']['review_state'] == 'unreviewed'
    assert any('没有有效的人工复核凭据引用对象' in text for text in issues_of(run)), issues_of(run)


ODA_WRAPPER = r'''
param($Exporter,$Source,$Target)
$ErrorActionPreference='Stop'
function Get-Command { param($Name) [pscustomobject]@{Source='fake-oda.exe'} }
function Start-Process {
 param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError)
 [IO.File]::WriteAllText((Join-Path $ArgumentList[1].Trim('"') 'source.dxf'),"0`nSECTION`n2`nENTITIES`n0`nENDSEC`n0`nEOF`n")
 $p=[pscustomobject]@{ExitCode=0}
 $p | Add-Member ScriptMethod WaitForExit { param($Milliseconds) if($null -ne $Milliseconds){return $true} }
 return $p
}
& $Exporter -SourceDwg $Source -OutputDxf $Target -ExportBackend oda
'''


@pytest.mark.skipif(sys.platform != 'win32' or not (shutil.which('pwsh') and shutil.which('powershell')),
                    reason='needs pwsh and Windows PowerShell 5.1')
def test_exporter_hashes_on_51_under_a_pwsh_module_path(tmp_path):
    """A 5.1 child of a pwsh-launched caller inherits pwsh's PSModulePath, auto-loads pwsh's
    Utility manifest and cannot find Get-FileHash. The path is forced here, so the case fails
    without the explicit import even when the test runner was not started from pwsh."""
    def ask(command):
        return subprocess.run([shutil.which('pwsh'), '-NoProfile', '-Command', command], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL).stdout.strip()
    module_path, pwsh_home = ask('$env:PSModulePath'), ask('$PSHOME')
    assert pwsh_home and pwsh_home.lower() in module_path.lower()
    source, target, wrapper = tmp_path / 'original.dwg', tmp_path / 'converted.dxf', tmp_path / 'oda-probe.ps1'
    source.write_bytes(b'drawing')
    wrapper.write_text(ODA_WRAPPER, encoding='utf-8-sig')
    proc = subprocess.run([shutil.which('powershell'), '-NoProfile', '-File', str(wrapper), str(EXPORTER),
                           str(source), str(target)], env=dict(os.environ, PSModulePath=module_path),
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = json.loads(Path(str(target) + '.export.json').read_text(encoding='utf-8'))
    assert receipt['source_sha256'] == hashlib.sha256(b'drawing').hexdigest()
    assert receipt['output_sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
