"""Native calculation and binding checks for counts of structural members."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/qto-structural-steel/scripts'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calc = load('member_count_native_calculator', 'calculate_structural_steel_items.py')
validator = load('member_count_native_validator', 'validate_structural_steel_result.py')


def manifest(tmp_path, unit='根', count=1, summary=False, mass=False):
    source = tmp_path / 'synthetic-source.txt'
    source.write_text('Synthetic fixture only; no project drawings or project quantities.\n', encoding='utf-8')
    evidence = []
    items = []
    for n in range(count):
        eid = f'EV-C{n}'
        evidence.append({'evidence_id': eid, 'source_id': 'SRC-TEST', 'evidence_class': 'object_geometry',
                         'fact_status': 'test_only', 'object_keys': [f'synthetic-instance:{n}'],
                         'source_kind': 'synthetic', 'fact': 'one synthetic physical instance', 'locator': f'instance {n}',
                         'method': 'fixture', 'quality': 'test_only', 'limitation': 'not project evidence'})
        items.append({'item_id': f'QTY-C{n}', 'name': f'Synthetic member {n} count', 'object_type': 'main_member',
                      'physical_object_id': f'OBJ-{n}', 'specification': 'synthetic', 'output_dimension': 'instance count',
                      'measurement_basis': 'explicit instance list', 'method': 'count', 'unit': unit,
                      'quantity_derivation': {'evidence_ids': [eid], 'groups': [{'group_id': f'g{n}',
                                               'instance_ids': [f'synthetic-instance:{n}'], 'units_per_instance': 1}]},
                      'formula_note': 'enumerated physical instances', 'scope_status': 'complete',
                      'mass_inclusion': 'not_mass', 'adoption_role': 'reference', 'evidence_ids': [eid],
                      'identity_evidence_ids': [eid], 'rule_ids': ['R-COUNT'], 'unresolved_ids': []})
    rules = [{'rule_id': 'R-COUNT', 'name': 'instance count', 'source': 'synthetic fixture', 'decision': '采用',
              'maturity': '试运行', 'execution': 'fixture only', 'evidence_ids': [e['evidence_id'] for e in evidence]}]
    if mass:
        evidence.extend([
            {'evidence_id': 'EV-MASS', 'source_id': 'SRC-TEST', 'evidence_class': 'object_geometry',
             'fact_status': 'test_only', 'object_keys': ['synthetic-mass-object'], 'source_kind': 'synthetic',
             'fact': 'synthetic mass object', 'locator': 'mass object', 'method': 'fixture', 'quality': 'test_only',
             'limitation': 'not project evidence'},
            {'evidence_id': 'EV-RHO', 'source_id': 'SRC-TEST', 'evidence_class': 'material_property',
             'fact_status': 'test_only', 'source_kind': 'synthetic', 'fact': 'synthetic density input',
             'locator': 'density', 'method': 'fixture', 'quality': 'test_only', 'limitation': 'test parameter'}])
        rules.append({'rule_id': 'R-MASS', 'name': 'section mass', 'source': 'synthetic fixture', 'decision': '采用',
                      'maturity': '试运行', 'execution': 'fixture only', 'evidence_ids': ['EV-MASS', 'EV-RHO']})
        items.append({'item_id': 'QTY-MASS', 'name': 'Synthetic member mass', 'object_type': 'main_member',
                      'physical_object_id': 'OBJ-MASS', 'specification': 'synthetic', 'output_dimension': 'mass',
                      'measurement_basis': 'synthetic section mass', 'method': 'built_up_section_length', 'unit': 't',
                      'cross_section_area_mm2': 1250, 'length_total_m': 1, 'density_kg_m3': 7850,
                      'density_basis': {'evidence_id': 'EV-RHO', 'basis': 'synthetic test parameter'},
                      'scope_status': 'complete', 'mass_inclusion': 'separate_reference', 'adoption_role': 'reference',
                      'evidence_ids': ['EV-MASS', 'EV-RHO'], 'identity_evidence_ids': ['EV-MASS'],
                      'rule_ids': ['R-MASS'], 'unresolved_ids': []})
    summaries = []
    if summary:
        summaries.append({'summary_id': 'QTY-COUNT-SUMMARY', 'item_ids': [f'QTY-C{n}' for n in range(count)],
                          'name': 'Synthetic member count summary', 'unit': unit, 'object_type': 'main_member',
                          'scope_status': 'complete', 'scope_coverage_closed': True, 'mass_inclusion': 'not_mass',
                          'adoption_role': 'reference', 'specification': 'synthetic', 'output_dimension': 'instance count',
                          'unresolved_ids': []})
    return {'manifest_version': calc.MANIFEST_VERSION, 'case_id': 'synthetic-member-count', 'test_mode': True,
            'scope': {'project': 'fixture', 'building': 'fixture', 'area': 'fixture', 'drawing_type': 'synthetic',
                      'drawing_version': 'test-v1', 'output_dimension': 'unit contract'},
            'sources': [{'source_id': 'SRC-TEST', 'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                         'source_type': 'synthetic fixture', 'file_role': 'text', 'source_role': 'test_fixture',
                         'identity_note': 'test-only fixture', 'reason': 'unit contract test', 'neutral_facts': 'synthetic instances',
                         'unused_impact': 'no project data'}],
            'evidence': evidence, 'rules': rules, 'items': items, 'unresolved': [], 'summary_requests': summaries,
            'note': 'Synthetic contract regression; not an engineering quantity.'}


@pytest.mark.parametrize('unit', ['根', '件', '组'])
def test_native_member_count_passes_full_binding(tmp_path, unit):
    m = manifest(tmp_path, unit=unit)
    evidence, _, result = calc.calculate(m)
    assert result['工程量结果'][0]['结果']['数值'] == 1
    assert validator.validate_binding_payloads(result, evidence, m, calc) == []


def test_count_summary_and_mass_keep_separate_units(tmp_path):
    m = manifest(tmp_path, count=2, summary=True, mass=True)
    evidence, _, result = calc.calculate(m)
    assert validator.validate_binding_payloads(result, evidence, m, calc) == []
    rows = {r['成果项编号']: r for r in result['工程量结果']}
    assert rows['QTY-COUNT-SUMMARY']['单位'] == '根'
    assert rows['QTY-COUNT-SUMMARY']['结果']['数值'] == 2
    assert rows['QTY-MASS']['单位'] == 't'


@pytest.mark.parametrize('mutation,fragment', [('unit', '计数'), ('mass', '计数'), ('fraction', '整数')])
def test_count_cannot_be_relabelled_as_mass_or_fractional(tmp_path, mutation, fragment):
    _, _, result = calc.calculate(manifest(tmp_path))
    row = result['工程量结果'][0]
    if mutation == 'unit':
        row['单位'] = 't'
    elif mutation == 'mass':
        row['专业检查']['质量归属'] = 'permanent'
    else:
        row['结果']['数值'] = 1.5
    assert any(fragment in error for error in validator.validate_document(result))


def test_mass_method_still_requires_tonnes(tmp_path):
    _, _, result = calc.calculate(manifest(tmp_path, mass=True))
    row = next(r for r in result['工程量结果'] if r['成果项编号'] == 'QTY-MASS')
    row['单位'] = '根'
    assert any('钢材质量必须使用t' in error for error in validator.validate_document(result))


def test_count_summary_cannot_be_relabelled_as_tonnes(tmp_path):
    _, _, result = calc.calculate(manifest(tmp_path, count=2, summary=True))
    row = next(r for r in result['工程量结果'] if r['成果项编号'] == 'QTY-COUNT-SUMMARY')
    row['单位'] = 't'
    assert any('单位' in error for error in validator.validate_document(result))
