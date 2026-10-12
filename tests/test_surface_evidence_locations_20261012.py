"""Generic regressions for wrong-file and wrong-object evidence locations.

No project drawings, project dimensions, or previously reported project answers
are embedded in this fixture.  The CLI tests use a synthetic six-square-metre
surface solely to exercise the native calculator/validator integration.
"""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
S = ROOT / 'skills/qto-surfaces-envelope/scripts'
spec = importlib.util.spec_from_file_location('location_audit_test', SHARED / 'qto_evidence_locations.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document(folder):
    frame = dict(coordinate_system_id='xy', origin_m=[0, 0, 0], x_axis=[1, 0, 0], up_axis=[0, 1, 0],
                 source_edge_id='a', source_edge_direction='same', source_edge_interval_m=[0, 3],
                 vertical_range_m=[0, 2], vertical_origin_m=0)
    facts = {'frames': {'face': frame},
             'edges': {'a': dict(edge_id='a', coordinate_system_id='xy', unit='m', start=[0, 0, 0], end=[3, 0, 0]),
                       'b': dict(edge_id='b', coordinate_system_id='xy', unit='m', start=[0, 2, 0], end=[3, 2, 0])},
             'finish_relation': {'room': 'sample', 'finish': 'finish'}, 'units': {'unit': 'm'}}
    source = dict(source_id='SRC-F', path='facts.json', sha256=write_json(folder/'facts.json', facts),
                  file_role='json', source_role='project_document', source_type='json', selection='selected',
                  reason='synthetic contract fixture', neutral_facts='synthetic source facts', not_used_impact='none')
    def ev(eid, cls, locator, keys):
        return dict(evidence_id=eid, source_id='SRC-F', evidence_class=cls, fact_status='test_only',
                    locator=locator, object_keys=keys, fact='synthetic test fact', extraction='fixture', quality='test_only')
    manifest = {'manifest_version': 'qto-surfaces-envelope-manifest/1.0',
                'task': dict(project_id='fixture', business_discipline='surfaces', main_objects='ceiling', system='finish',
                             building='sample', zone='sample', drawing_type='synthetic', drawing_version='v1',
                             output_dimension='area', applicable_rule_status='unresolved'),
                'sources': [source],
                'evidence': [ev('EV-I', 'source_identity', '#/units', ['face']),
                             ev('EV-F', 'surface_geometry', '#/frames/face', ['face', 'a']),
                             ev('EV-E', 'surface_geometry', '/edges/a', ['face', 'a']),
                             ev('EV-L', 'layer_assignment', '#/finish_relation', ['face', 'finish'])],
                'rules': [dict(rule_id='R-1', name='fixture geometry', source='fixture', decision='试用', maturity='候选',
                               execution='reference', evidence_locator='EV-F')],
                'unresolved': [dict(unresolved_id='U-1', type='范围未定', description='synthetic partial scope', impact='local',
                                    required_input='project review', action='保留已证实部分')],
                'items': [dict(item_id='QTY-1', name='synthetic surface', object_type='ceiling_finish', physical_surface_id='face',
                               layer_id='finish', side_scope='inner', measurement_basis='horizontal_projection', method='rectangle_net',
                               unit='m²', scope_status='partial', adoption_role='reference', quantity_role='physical_area', result_use='reference',
                               evidence_refs=['EV-I', 'EV-F', 'EV-E', 'EV-L'], rule_refs=['R-1'], unresolved_refs=['U-1'],
                               inputs={'length_m': 3, 'height_m': 2}, local_coordinate_frame=copy.deepcopy(frame),
                               frame_binding=dict(frame_evidence_ref='EV-F', source_pointer='/frames/face',
                                                  edge_evidence_ref='EV-E', edge_source_pointer='/edges/a'))]}
    return manifest, facts


def evidence(manifest, eid):
    return next(e for e in manifest['evidence'] if e['evidence_id'] == eid)


def run_cli(folder, manifest):
    path = folder/'manifest.json'; write_json(path, manifest)
    result = folder/'result.json'
    calc = subprocess.run([sys.executable, str(S/'calculate_surfaces_envelope.py'), '--manifest', str(path), '--output', str(result)],
                          capture_output=True, text=True, encoding='utf8', timeout=30)
    assert calc.returncode == 0, calc.stderr
    validation = subprocess.run([sys.executable, str(S/'validate_surfaces_envelope_result.py'), '--calculator', str(S/'calculate_surfaces_envelope.py'),
                                 '--manifest', str(path), '--binding', str(result)], capture_output=True, text=True, encoding='utf8', timeout=30)
    assert validation.stdout, validation.stderr
    return json.loads(result.read_text()), validation, json.loads(validation.stdout)


def test_valid_native_result_and_frame_remain_unchanged(tmp_path):
    m, _ = document(tmp_path)
    result, v, report = run_cli(tmp_path, m)
    assert v.returncode == 0 and report['status'] == 'PASS'
    assert report['evidence_location_audit']['checked_json_locator_count'] == 4
    item = result['工程量结果'][0]
    assert item['结果']['数值'] == 6
    frame = item['专业检查']['输入审计']['frame_validation']
    assert frame['status'] == 'bound_consistent'
    assert frame['rectangle_corners_m'] == [[0, 0, 0], [3, 0, 0], [3, 2, 0], [0, 2, 0]]
    assert report['business_correctness_proven'] is False


@pytest.mark.parametrize('change', ['wrong_file', 'missing_unit', 'wrong_existing_edge'])
def test_native_replay_no_longer_passes_invalid_evidence_locations(tmp_path, change):
    m, _ = document(tmp_path)
    if change == 'wrong_file':
        other = copy.deepcopy(m['sources'][0]); other.update(source_id='SRC-OTHER', path='other.json', sha256=write_json(tmp_path/'other.json', {'table': []}))
        m['sources'].append(other); evidence(m, 'EV-L')['source_id'] = 'SRC-OTHER'
    elif change == 'missing_unit':
        evidence(m, 'EV-I')['locator'] = '#/units/not_present'
    else:
        evidence(m, 'EV-E')['locator'] = '/edges/b'
    result, v, report = run_cli(tmp_path, m)
    # Arithmetic and declared frame still pass; publication validation must not.
    assert result['工程量结果'][0]['结果']['数值'] == 6
    assert v.returncode == 2 and report['status'] == 'FAIL'
    assert report['error_count'] >= 1
    assert report['evidence_location_audit']['errors']


@pytest.mark.parametrize('locator', ['#', '#/frames', '/frames', '#%2Fframes%2Fface'])
def test_parent_locator_or_fragment_can_support_a_more_specific_frame(tmp_path, locator):
    m, _ = document(tmp_path); evidence(m, 'EV-F')['locator'] = locator
    assert not a.audit_evidence_locations(m, tmp_path)['errors']


def test_free_text_legacy_locator_is_disclosed_not_claimed_as_checked(tmp_path):
    m, _ = document(tmp_path); evidence(m, 'EV-F')['locator'] = 'floor 1: drawn face'
    report = a.audit_evidence_locations(m, tmp_path)
    assert report['errors'] == [] and report['checked_json_locator_count'] == 3
    assert report['unchecked_text_locators'][0]['evidence_id'] == 'EV-F'


def test_changed_source_sha_is_not_accepted(tmp_path):
    m, facts = document(tmp_path); facts['units']['unit'] = 'mm'; write_json(tmp_path/'facts.json', facts)
    report = a.audit_evidence_locations(m, tmp_path)
    assert any('SHA256' in error for error in report['errors'])


def test_non_json_source_cannot_claim_a_json_fragment(tmp_path):
    m, _ = document(tmp_path); m['sources'][0]['file_role'] = 'dxf'
    assert any('非JSON' in e for e in a.audit_evidence_locations(m, tmp_path)['errors'])


@pytest.mark.parametrize('locator', ['#', '/a~1b/~0name/0', '#/a~1b/~0name/0', '#%2Fa~1b%2F~0name%2F0'])
def test_pointer_escaping_and_root(locator):
    value = {'a/b': {'~name': [False]}}
    tokens = a.pointer_tokens(locator)
    assert a.resolve_tokens(value, tokens) == (value if locator == '#' else False)


@pytest.mark.parametrize('locator', ['#bad', '#/%GG', '#/%FF', '/bad~2', '/bad~'])
def test_invalid_pointer_syntax_is_rejected(locator):
    with pytest.raises((ValueError, UnicodeError)):
        a.pointer_tokens(locator)


@pytest.mark.parametrize('index', ['-1', '01', '-', '2', '9'*10000])
def test_array_indexes_are_strict_and_bounded(index):
    with pytest.raises(ValueError):
        a.resolve_tokens([None, False], (index,))


@pytest.mark.parametrize('value', [None, False, 0, [], {}])
def test_existing_empty_or_false_fact_is_a_real_location(value):
    assert a.resolve_tokens({'fact': value}, ('fact',)) == value


def test_same_prefix_is_not_an_ancestor(tmp_path):
    m, facts = document(tmp_path)
    facts['edges']['a-extra'] = facts['edges']['a']
    m['sources'][0]['sha256'] = write_json(tmp_path/'facts.json', facts)
    evidence(m, 'EV-E')['locator'] = '/edges/a-extra'
    assert a.audit_evidence_locations(m, tmp_path)['errors']


@pytest.mark.parametrize('locator', ['#page=1', '#section-2', '/sheet/room', 'Model:LINE[A]'])
def test_non_json_document_locators_are_not_misread_as_json(tmp_path, locator):
    m, _ = document(tmp_path)
    m['sources'][0]['file_role'] = 'pdf'
    for e in m['evidence']:
        e['locator'] = locator
    report = a.audit_evidence_locations(m, tmp_path)
    assert not report['errors'] and report['checked_json_locator_count'] == 0
    assert len(report['unchecked_text_locators']) == 4
