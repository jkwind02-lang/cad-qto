import importlib.util
from pathlib import Path

import pytest

from test_surface_evidence_locations_20261012 import document, evidence, run_cli, write_json

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'skills/_qto-shared-evidence/scripts/qto_evidence_locations.py'
spec = importlib.util.spec_from_file_location('loc', SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_legacy_annotation_is_accepted_after_pointer_resolution():
    assert mod.pointer_tokens('/surface_frames/CL-11; raw Model handles 9A,9B') == ('surface_frames', 'CL-11')


def test_real_json_key_with_semicolon_is_not_truncated():
    value = {'objects': {'a;b': {'ok': True}}}
    assert mod.resolve_tokens(value, mod.pointer_tokens('/objects/a;b')) == {'ok': True}


def test_invalid_legacy_path_is_still_rejected_by_resolution():
    try:
        mod.resolve_tokens({'objects': {}}, mod.pointer_tokens('/objects/not-found; explanation'))
    except ValueError:
        return
    raise AssertionError('missing JSON location must remain invalid')


def test_legacy_note_is_resolved_without_claiming_annotation_was_checked(tmp_path):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = '/edges/a; raw Model handles 9A,9B'
    result, validation, report = run_cli(tmp_path, manifest)
    assert validation.returncode == 0 and report['status'] == 'PASS'
    audit = report['evidence_location_audit']
    assert audit['checked_json_locator_count'] == 4
    checked = next(row for row in audit['checked'] if row['evidence_id'] == 'EV-E')
    assert checked['verified_locator'] == '/edges/a'
    assert audit['unchecked_annotations'] == [
        {'evidence_id': 'EV-E', 'text': '; raw Model handles 9A,9B',
         'reason': '历史路径附注未作机器定位核验'}]
    assert result['工程量结果'][0]['结果']['数值'] == 6
    assert report['business_correctness_proven'] is False


@pytest.mark.parametrize('key', ['a;b', 'a; note', 'a; raw Model handles 9A,9B'])
def test_literal_semicolon_key_wins_over_existing_prefix_in_production_audit(tmp_path, key):
    manifest, facts = document(tmp_path)
    facts['edges'][key] = dict(facts['edges']['a'], test_marker='literal key')
    manifest['sources'][0]['sha256'] = write_json(tmp_path/'facts.json', facts)
    evidence(manifest, 'EV-E')['locator'] = '/edges/' + key
    manifest['items'][0]['frame_binding']['edge_source_pointer'] = '/edges/' + key
    report = mod.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] == [] and report['checked_json_locator_count'] == 4
    assert report['unchecked_annotations'] == []
    manifest['items'][0]['frame_binding']['edge_source_pointer'] = '/edges/a'
    assert mod.audit_evidence_locations(manifest, tmp_path)['errors']


@pytest.mark.parametrize('locator', ['/edges/missing; note', '/edges/missing;note'])
def test_annotations_cannot_rescue_missing_prefix(tmp_path, locator):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = locator
    report = mod.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] and report['checked_json_locator_count'] == 3


def test_missing_literal_semicolon_key_is_not_silently_truncated(tmp_path):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = '/edges/a;missing'
    assert mod.audit_evidence_locations(manifest, tmp_path)['errors']


def test_legacy_note_cannot_hide_wrong_existing_edge(tmp_path):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = '/edges/b; raw Model handles 9A,9B'
    _, validation, report = run_cli(tmp_path, manifest)
    assert validation.returncode == 2 and report['status'] == 'FAIL'


def test_legacy_note_does_not_bypass_source_sha(tmp_path):
    manifest, facts = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = '/edges/a; raw Model handles 9A,9B'
    facts['units']['unit'] = 'mm'
    write_json(tmp_path/'facts.json', facts)
    report = mod.audit_evidence_locations(manifest, tmp_path)
    assert any('SHA256' in error for error in report['errors'])


def test_free_text_with_note_is_not_claimed_as_checked(tmp_path):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = 'Model line; raw handles 9A,9B'
    report = mod.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] == [] and report['checked_json_locator_count'] == 3
    assert report['unchecked_text_locators'][0]['evidence_id'] == 'EV-E'
