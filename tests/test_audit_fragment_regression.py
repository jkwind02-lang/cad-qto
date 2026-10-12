"""Exercise the production audit and native validator, not only token helpers."""
import hashlib

import pytest

from test_surface_evidence_locations_20261012 import a, document, evidence, run_cli, write_json


@pytest.mark.parametrize('locator', ['#bad', '#/%GG', '#/%FF', '#/bad~2', '#/bad~'])
def test_malformed_json_fragment_is_an_audit_error(tmp_path, locator):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = locator
    report = a.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] and report['checked_json_locator_count'] == 3
    assert report['unchecked_text_locators'] == []
    assert report['business_correctness_proven'] is False


@pytest.mark.parametrize('locator', ['#bad', '#/%GG'])
def test_native_validator_rejects_malformed_fragment(tmp_path, locator):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-L')['locator'] = locator
    result, validation, report = run_cli(tmp_path, manifest)
    assert validation.returncode == 2 and report['status'] == 'FAIL'
    assert report['evidence_location_audit']['errors']
    assert result['工程量结果'][0]['结果']['数值'] == 6


@pytest.mark.parametrize('locator', ['#/edges/a; note', '#%2Fedges%2Fa%3B%20note'])
def test_missing_fragment_key_never_uses_legacy_prefix(tmp_path, locator):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = locator
    report = a.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] and report['checked_json_locator_count'] == 3
    assert report['unchecked_text_locators'] == []


@pytest.mark.parametrize('locator', ['/a;b', '#/a%3Bb', '#%2Fa%3Bb'])
def test_semicolon_key_and_encoded_fragment_select_literal_key_in_native_path(tmp_path, locator):
    manifest, facts = document(tmp_path)
    facts['a'] = dict(facts['edges']['b'])
    facts['a;b'] = dict(facts['edges']['a'])
    manifest['sources'][0]['sha256'] = write_json(tmp_path/'facts.json', facts)
    evidence(manifest, 'EV-E')['locator'] = locator
    binding = manifest['items'][0]['frame_binding']
    binding['edge_source_pointer'] = '/a;b'
    result, validation, report = run_cli(tmp_path, manifest)
    assert validation.returncode == 0 and report['status'] == 'PASS'
    assert result['工程量结果'][0]['结果']['数值'] == 6
    assert report['evidence_location_audit']['unchecked_annotations'] == []
    binding['edge_source_pointer'] = '/a'
    assert a.audit_evidence_locations(manifest, tmp_path)['errors']


@pytest.mark.parametrize('locator', ['#/edges/a; note', '#%2Fedges%2Fa%3B%20note'])
def test_existing_fragment_semicolon_key_is_preserved(tmp_path, locator):
    manifest, facts = document(tmp_path)
    facts['edges']['a; note'] = dict(facts['edges']['a'])
    manifest['sources'][0]['sha256'] = write_json(tmp_path/'facts.json', facts)
    evidence(manifest, 'EV-E')['locator'] = locator
    binding = manifest['items'][0]['frame_binding']
    binding['edge_source_pointer'] = '#/edges/a; note'
    assert a.audit_evidence_locations(manifest, tmp_path)['errors'] == []
    binding['edge_source_pointer'] = '/edges/a'
    assert a.audit_evidence_locations(manifest, tmp_path)['errors']


@pytest.mark.parametrize('pointer', ['/edges/missing', '#/edges/missing'])
def test_selected_descendant_must_exist_even_inside_parent_scope(tmp_path, pointer):
    manifest, _ = document(tmp_path)
    evidence(manifest, 'EV-E')['locator'] = '#/edges'
    manifest['items'][0]['frame_binding']['edge_source_pointer'] = pointer
    assert a.audit_evidence_locations(manifest, tmp_path)['errors']


@pytest.mark.parametrize('locator', ['#/edges', '#%2fedges'])
def test_json_fragment_on_pdf_is_rejected(tmp_path, locator):
    manifest, _ = document(tmp_path)
    manifest['sources'][0]['file_role'] = 'pdf'
    for row in manifest['evidence']:
        row['locator'] = locator
    report = a.audit_evidence_locations(manifest, tmp_path)
    assert len(report['errors']) == 4 and report['checked_json_locator_count'] == 0


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_hash_verified_source_still_rejects_nonstandard_json_numbers(tmp_path, constant):
    manifest, _ = document(tmp_path)
    raw = ('{"edges": {"a": ' + constant + '}}').encode('utf-8')
    (tmp_path/'facts.json').write_bytes(raw)
    manifest['sources'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
    report = a.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] and report['checked_json_locator_count'] == 0


def test_root_fragment_is_checked_and_allows_actual_descendants(tmp_path):
    manifest, _ = document(tmp_path)
    for row in manifest['evidence']:
        row['locator'] = '#'
    report = a.audit_evidence_locations(manifest, tmp_path)
    assert report['errors'] == [] and report['checked_json_locator_count'] == 4
    assert report['unchecked_text_locators'] == []


@pytest.mark.parametrize('kind', ['frame', 'edge'])
def test_native_validator_rejects_existing_sibling_for_either_binding(tmp_path, kind):
    manifest, facts = document(tmp_path)
    facts['frames']['other'] = dict(facts['frames']['face'])
    manifest['sources'][0]['sha256'] = write_json(tmp_path/'facts.json', facts)
    evidence(manifest, 'EV-F' if kind == 'frame' else 'EV-E')['locator'] = (
        '/frames/other' if kind == 'frame' else '/edges/b')
    result, validation, report = run_cli(tmp_path, manifest)
    assert validation.returncode == 2 and report['status'] == 'FAIL'
    assert report['evidence_location_audit']['errors']
    assert result['工程量结果'][0]['结果']['数值'] == 6


@pytest.mark.parametrize('key,pointer', [('source_pointer', '/frames/face; note'),
                                       ('edge_source_pointer', '/edges/a; note')])
def test_binding_pointer_is_strict_and_cannot_invent_a_missing_annotated_key(tmp_path, key, pointer):
    manifest, _ = document(tmp_path)
    manifest['items'][0]['frame_binding'][key] = pointer
    assert a.audit_evidence_locations(manifest, tmp_path)['errors']
