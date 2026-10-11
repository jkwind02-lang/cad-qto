"""Reader coverage: measurements, evidence, delegated geometry and real gaps.

These fixtures deliberately use no project names, handles or expected BOQs.
"""
from __future__ import annotations

import importlib.util
import json
import math
import logging
from pathlib import Path
import subprocess
import sys
import re

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_cad_geometry as geometry

spec = importlib.util.spec_from_file_location('coverage_extract', SHARED/'extract_cad_neutral.py')
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


def row(entity):
    return extract.convert(entity, layout='Model', parent_block=None,
                           source_hash='a'*64, raw_unit='Millimeters', sequence=1)


@pytest.mark.parametrize('kind', ['ACAD_PROXY_ENTITY', '3DSOLID', 'MESH', 'BODY', 'REGION'])
def test_unhandled_entities_never_claim_supported_or_zero_length(kind):
    entity = ezdxf.new().modelspace().new_entity(kind, {})
    result = row(entity)
    assert result['geometry_status'] == 'unsupported'
    assert result['geometry_capability'] == 'unsupported'
    assert result['geometry_issue_codes'] == ['entity_type_not_supported']
    assert result['raw_length'] is None and result['geometry'] is None
    assert result['recommended_actions'] == ['native_geometry_read_required']
    assert result['entity_handle'] == entity.dxf.handle


@pytest.mark.parametrize('kind', ['TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF', 'DIMENSION', 'LEADER', 'WIPEOUT', 'POINT'])
def test_evidence_without_measurable_curves_is_not_a_read_failure(kind):
    space = ezdxf.new().modelspace()
    if kind == 'TEXT': entity = space.add_text('DN75', dxfattribs={'insert': (10, 20)})
    elif kind == 'MTEXT': entity = space.add_mtext('Room A', dxfattribs={'insert': (10, 20)})
    elif kind == 'ATTRIB': entity = space.new_entity('ATTRIB', {'text': '75', 'tag': 'SIZE', 'insert': (10, 20)})
    elif kind == 'ATTDEF': entity = space.add_attdef('SIZE', (10, 20), text='75')
    elif kind == 'DIMENSION': entity = space.add_linear_dim(base=(0, 2), p1=(0, 0), p2=(5, 0)).dimension
    elif kind == 'LEADER': entity = space.add_leader([(1, 2), (3, 4)])
    elif kind == 'WIPEOUT': entity = space.add_wipeout([(1, 2), (5, 2), (5, 6), (1, 6)])
    else: entity = space.add_point((10, 20))
    result = row(entity)
    assert result['geometry_status'] == 'supported'
    assert result['geometry_capability'] == ('point_evidence' if kind == 'POINT' else 'annotation_evidence')
    assert result['geometry_issues'] == []
    assert result['geometry'] is None and result['raw_length'] is None
    assert result['x'] is not None and result['y'] is not None


def test_hatch_is_delegated_to_real_existing_adapter():
    hatch = ezdxf.new().modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (4, 0), (4, 3), (0, 3)], is_closed=True)
    result = row(hatch)
    assert result['geometry_status'] == 'requires_adapter'
    assert result['geometry_issues'] == []
    assert result['raw_geometry']['boundary_path_count'] == 1
    assert (ROOT/'skills'/result['recommended_actions'][0]).is_file()
    assert result['raw_length'] is None


@pytest.mark.parametrize('factory', [lambda s: s.add_line((1, 2), (4, 6)),
                                     lambda s: s.add_circle((0, 0), 4),
                                     lambda s: s.add_lwpolyline([(0, 0), (3, 0), (3, 4)])])
def test_measurable_geometry_remains_available(factory):
    result = row(factory(ezdxf.new().modelspace()))
    assert result['geometry_capability'] == 'measurable_curve'
    assert result['geometry_status'] == 'supported'
    assert result['raw_length'] > 0


def test_empty_polyline_is_not_a_zero_length_takeoff():
    result = row(ezdxf.new().modelspace().add_lwpolyline([]))
    assert result['geometry_status'] == 'unsupported'
    assert result['geometry_issue_codes'] == ['empty_curve_geometry']
    assert result['raw_length'] is None


def test_missing_definition_keeps_reference_identity_and_gap():
    result = row(ezdxf.new().modelspace().add_blockref('not supplied', (100, 50)))
    assert result['geometry_capability'] == 'block_reference'
    assert 'block_definition_missing' in result['geometry_issue_codes']
    assert result['raw_geometry']['block_reference']['definition_state'] == 'missing'
    assert result['x'] == 100 and result['insert_matrix_wcs'] is not None


@pytest.mark.parametrize('has_children', [False, True])
def test_xref_not_mistaken_for_loaded_verified_local_block(has_children):
    doc = ezdxf.new()
    block = doc.blocks.new('referenced', dxfattribs={'flags': 4, 'xref_path': 'relative/model.dwg'})
    if has_children:
        block.add_line((0, 0), (10, 0))
    result = row(doc.modelspace().add_blockref('referenced', (0, 0)))
    expected = 'xref_identity_not_verified' if has_children else 'xref_content_unavailable'
    assert result['geometry_issue_codes'] == [expected]
    assert result['raw_geometry']['block_reference']['xref_path'] == 'relative/model.dwg'
    assert 'native_load_and_verify_xref' in result['recommended_actions']


def test_explicit_dynamic_marker_requires_native_state_evaluation():
    doc = ezdxf.new()
    block = doc.blocks.new('adjustable-symbol')
    block.add_line((0, 0), (10, 0))
    block.block_record.new_extension_dict().add_dictionary('ACAD_ENHANCEDBLOCK')
    result = row(doc.modelspace().add_blockref(block.name, (0, 0)))
    assert result['geometry_issue_codes'] == ['dynamic_state_not_evaluated']
    assert result['raw_geometry']['block_reference']['dynamic_evidence']


def test_anonymous_name_is_a_candidate_not_a_dynamic_classification():
    doc = ezdxf.new()
    block = doc.blocks.new('*U42')
    block.add_line((0, 0), (10, 0))
    result = row(doc.modelspace().add_blockref(block.name, (0, 0)))
    assert result['geometry_issue_codes'] == ['anonymous_block_state_unverified']
    assert not result['raw_geometry']['block_reference']['dynamic_evidence']


def test_array_does_not_silently_degrade_to_first_cell():
    doc = ezdxf.new()
    block = doc.blocks.new('repeat'); block.add_line((0, 0), (10, 0))
    result = row(doc.modelspace().add_blockref(block.name, (0, 0), dxfattribs={'column_count': 3, 'column_spacing': 20}))
    assert result['geometry_issue_codes'] == ['array_instances_not_expanded']
    assert result['raw_geometry']['column_count'] == 3


def test_read_failure_retains_stream_row_and_is_counted(tmp_path):
    doc = ezdxf.new()
    good = doc.modelspace().add_line((0, 0), (3, 4))
    malformed = doc.modelspace().add_line((0, 0), (float('nan'), 4))
    output = tmp_path/'rows.jsonl'
    coverage = extract.new_coverage()
    count, kinds, failures = extract.write_stream(output, [('Model', None, doc.modelspace())], 'a'*64, 'mm', coverage=coverage)
    records = [json.loads(text) for text in output.read_text(encoding='utf-8').splitlines()]
    assert count == 2 and kinds['LINE'] == 2 and len(records) == 2
    assert len(failures) == 1 and failures[0]['record_retained'] is True
    assert coverage['entity_count'] == 2 and coverage['read_failed_count'] == 1
    failed = next(r for r in records if r['entity_handle'] == malformed.dxf.handle)
    assert failed['geometry_status'] == 'read_failed' and failed['raw_length'] is None
    assert next(r for r in records if r['entity_handle'] == good.dxf.handle)['raw_length'] == 5


def test_cli_coverage_counts_inventory_without_claiming_quantity_coverage(tmp_path):
    doc = ezdxf.new(); doc.units = 4
    doc.modelspace().add_line((0, 0), (3, 4))
    doc.modelspace().add_text('annotation')
    mesh = doc.modelspace().add_mesh()
    with mesh.edit_data() as data:
        data.vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        data.faces = [(0, 1, 2)]
    h = doc.modelspace().add_hatch()
    h.paths.add_polyline_path([(0, 0), (4, 0), (4, 3), (0, 3)], is_closed=True)
    source = tmp_path/'input.dxf'; doc.saveas(source)
    before = extract.sha256(source)
    output = tmp_path/'neutral'
    process = subprocess.run([sys.executable, '-B', str(SHARED/'extract_cad_neutral.py'),
        '--source-dxf', str(source), '--output-dir', str(output)], capture_output=True, text=True, encoding='utf-8')
    assert process.returncode == 3, process.stdout+process.stderr
    manifest = json.loads((output/'CAD中性取证清单.json').read_text(encoding='utf-8'))
    coverage = json.loads((output/'CAD读取覆盖与待补读.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PARTIAL'
    assert coverage['entity_count'] == 4 and coverage['pending_object_count'] == 2
    assert coverage['capability_counts'] == {'measurable_curve': 1, 'annotation_evidence': 1, 'unsupported': 1, 'specialized_adapter_required': 1}
    assert coverage['quantity_coverage_proven'] is False
    assert manifest['artifacts']['failures']['count'] == 1  # HATCH is delegated, not failed.
    assert manifest['artifacts']['read_coverage']['sha256'] == extract.sha256(output/'CAD读取覆盖与待补读.json')
    assert extract.sha256(source) == before


def test_text_only_cli_does_not_raise_false_geometry_failure(tmp_path):
    doc = ezdxf.new(); doc.modelspace().add_text('specification note')
    source = tmp_path/'input.dxf'; doc.saveas(source)
    output = tmp_path/'neutral'
    process = subprocess.run([sys.executable, '-B', str(SHARED/'extract_cad_neutral.py'), '--source-dxf', str(source), '--output-dir', str(output)], capture_output=True)
    assert process.returncode == 0
    manifest = json.loads((output/'CAD中性取证清单.json').read_text(encoding='utf-8'))
    assert manifest['read_coverage']['capability_counts'] == {'annotation_evidence': 1}
    assert manifest['read_coverage']['quantity_coverage_proven'] is False


def dimension(dimtype=1, p1=(0, 0, 0), p2=(0, 2600, 0), **attributes):
    return ezdxf.new('R2018').modelspace().new_entity('DIMENSION',
        dict(dimtype=dimtype, defpoint=(0, 0, 0), defpoint2=p1, defpoint3=p2, **attributes))


@pytest.mark.parametrize('angle', [0, 35, 90, 145, 270])
@pytest.mark.parametrize('scale,origin', [(1, (0, 0)), (.001, (30, -50)), (1000, (1e9, -1e9))])
def test_aligned_dimensions_without_angle_are_rotation_unit_and_translation_invariant(angle, scale, origin):
    # A 3-4-5 triangle rotated before translation is always length 5.
    r = math.radians(angle)
    dx, dy = (3*math.cos(r)-4*math.sin(r))*scale, (3*math.sin(r)+4*math.cos(r))*scale
    entity = dimension(dimtype=129, p1=(*origin, 0), p2=(origin[0]+dx, origin[1]+dy, 0))
    assert not entity.dxf.hasattr('angle')
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] == pytest.approx(5*scale, rel=1e-10)
    assert facts['linear_measurement_usable'] is True
    assert facts['design_value_adopted'] is False
    assert row(entity)['raw_length'] is None  # Not a pipe centerline.


@pytest.mark.parametrize('angle,expected', [(0, 3), (90, 4)])
def test_rotated_dimension_is_projection_not_euclidean_distance(angle, expected):
    entity = dimension(dimtype=0, p2=(3, 4, 0), angle=angle)
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] == pytest.approx(expected)
    assert facts['method'] == 'rotated_wcs_projection_on_ocs_direction'


def test_rotated_dimension_tilted_ocs_does_not_transform_wcs_points_twice():
    entity = dimension(dimtype=0, p2=(3, 0, 4), angle=90, extrusion=(0, 1, 0))
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] == pytest.approx(4)
    assert facts['linear_measurement_usable'] is True


def test_aligned_dimension_tilted_ocs_uses_true_aligned_length():
    entity = dimension(p2=(3, 0, 4), extrusion=(0, 1, 0))
    assert geometry.dimension_measurement_facts(entity)['raw_measurement'] == 5


def test_stored_dimension_geometry_conflict_keeps_candidates_not_design_value():
    facts = geometry.dimension_measurement_facts(dimension(actual_measurement=2300))
    assert facts['geometric_measurement_candidate'] == 2600
    assert facts['stored_actual_measurement'] == 2300
    assert facts['raw_measurement'] is None
    assert facts['issue_codes'] == ['dimension_stored_geometry_conflict']


def test_aligned_interface_zero_is_corrected_by_type_not_accepted_as_design_zero():
    entity = dimension(actual_measurement=2600)
    facts = row(entity)['raw_geometry']['dimension_measurement']
    assert facts['api_measurement'] == 0
    assert facts['raw_measurement'] == 2600
    assert 'library_measurement_disagrees_with_type_aware_geometry' in facts['notes']


def test_actual_zero_projection_stays_unresolved_not_replaced_by_point_distance():
    facts = geometry.dimension_measurement_facts(dimension(dimtype=0, angle=0))
    assert facts['geometric_measurement_candidate'] == 0
    assert facts['raw_measurement'] is None
    assert 'dimension_zero_or_degenerate_measurement' in facts['issue_codes']


@pytest.mark.parametrize('text', ['2300', '0', 'R250', '按现场'])
def test_dimension_literal_text_is_not_used_as_geometry_or_ignored(text):
    facts = geometry.dimension_measurement_facts(dimension(text=text))
    assert facts['geometric_measurement_candidate'] == 2600
    assert facts['raw_measurement'] is None
    assert facts['text_override'] == text
    assert 'dimension_literal_text_requires_review' in facts['issue_codes']


def test_dimension_scale_kept_separate_from_raw_geometry():
    entity = dimension(p2=(0, 2.6, 0), actual_measurement=2600)
    override = entity.override(); override['dimlfac'] = 1000; override.commit()
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] == 2.6
    assert facts['scaled_measurement_candidate'] == 2600
    assert facts['linear_factor'] == 1000


@pytest.mark.parametrize('factor', [.1, .1333333333333333, .2, .5, 2., 1000.])
def test_cached_linear_measurement_includes_dimlfac(factor):
    entity = dimension(p2=(3, 4, 0), actual_measurement=5*factor)
    override = entity.override(); override['dimlfac'] = factor; override.commit()
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] == 5
    assert facts['scaled_measurement_candidate'] == 5*factor
    assert facts['issue_codes'] == []


def test_stale_cache_not_excused_by_accepting_either_raw_or_scaled():
    entity = dimension(p2=(3, 4, 0), actual_measurement=5)
    override = entity.override(); override['dimlfac'] = .5; override.commit()
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] is None
    assert facts['scaled_measurement_candidate'] == 2.5
    assert 'dimension_stored_geometry_conflict' in facts['issue_codes']


def test_dimension_negative_factor_is_not_silently_applied_without_space_context():
    entity = dimension()
    override = entity.override(); override['dimlfac'] = -2; override.commit()
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['raw_measurement'] is None and facts['scaled_measurement_candidate'] is None
    assert 'dimension_scale_requires_context' in facts['issue_codes']


def test_dimension_out_of_plane_endpoints_require_review():
    facts = geometry.dimension_measurement_facts(dimension(p2=(3, 4, 5)))
    assert facts['raw_measurement'] is None
    assert 'dimension_points_outside_ocs_plane' in facts['issue_codes']


@pytest.mark.parametrize('dimtype,kind', [(2, 'angle_degrees'), (3, 'diameter'), (4, 'radius'), (5, 'angle_degrees'), (6, 'ordinate_vector')])
def test_other_dimension_families_do_not_become_linear_calibration(dimtype, kind):
    entity = dimension(dimtype=dimtype, p1=(2, 0, 0), p2=(0, 2, 0), defpoint4=(0, 0, 0))
    facts = geometry.dimension_measurement_facts(entity)
    assert facts['measurement_kind'] == kind
    assert facts['raw_measurement'] is None
    assert facts['linear_measurement_usable'] is False


@pytest.mark.parametrize('scales', [(1.1, 1.1, 1.), (-1.2, .7, 2.), (2., -3., .5)])
@pytest.mark.parametrize('extrusion', [(0, 0, 1), (0, 1, 0)])
def test_large_translation_does_not_corrupt_insert_rotation_scale_or_ocs(scales, extrusion):
    doc = ezdxf.new()
    block = doc.blocks.new('translated-local-frame', base_point=(1e6, -2e6, 0))
    block.add_line((1e6, -2e6, 0), (1e6+1000, -2e6, 0))
    entity = doc.modelspace().add_blockref(block.name, (1e9, -1e9, 1e8),
        dxfattribs={'rotation': 31, 'xscale': scales[0], 'yscale': scales[1],
                    'zscale': scales[2], 'extrusion': extrusion})
    matrix = row(entity)['insert_matrix_wcs']
    c, s = math.cos(math.radians(31)), math.sin(math.radians(31))
    # Independent OCS bases for these two extrusion normals.
    if extrusion == (0, 0, 1):
        expected = [[c*scales[0], -s*scales[1], 0],
                    [s*scales[0], c*scales[1], 0], [0, 0, scales[2]]]
    else:
        expected = [[-c*scales[0], s*scales[1], 0],
                    [0, 0, scales[2]], [s*scales[0], c*scales[1], 0]]
    for i in range(3):
        assert matrix[4*i:4*i+3] == pytest.approx(expected[i], abs=2e-15)
    # The short member vector must remain independent of the billion-unit
    # translation and million-unit block base point.
    vector = geometry.transform_vector([1000, 0, 0], matrix)
    assert math.sqrt(sum(x*x for x in vector)) == pytest.approx(1000*abs(scales[0]), abs=1e-10)


def duplicate_handle_file(path):
    doc = ezdxf.new('R2018')
    first = doc.modelspace().add_line((0, 0), (3, 4))
    second = doc.modelspace().add_line((10, 0), (13, 4))
    doc.saveas(path)
    content = path.read_text(encoding='utf-8')
    # Corrupt only the second entity's identity in the actually saved DXF.
    # Let the real loader discover the collision, without mocking its logger.
    pattern = rf'(?m)^(\s*5\n){second.dxf.handle}$'
    changed, count = re.subn(pattern, lambda m: m[1]+first.dxf.handle, content)
    assert count == 1
    path.write_text(changed, encoding='utf-8')
    return first.dxf.handle


def test_actual_duplicate_handle_load_retains_source_identity_conflict_and_source_bytes(tmp_path):
    source = tmp_path/'duplicate.dxf'
    handle = duplicate_handle_file(source)
    before = extract.sha256(source)
    document, diagnostics = geometry.read_dxf_with_diagnostics(source)
    assert document is not None  # Successful parsing is not identity validation.
    assert diagnostics['status'] == 'PARTIAL'
    assert diagnostics['source_identity_issues'][0]['entity_handle'] == handle
    assert diagnostics['source_identity_issues'][0]['kind'] == 'duplicate_entity_handle'
    assert diagnostics['parser_entity_preservation_proven'] is False
    assert diagnostics['source_rewrite_performed'] is False
    assert extract.sha256(source) == before


def test_duplicate_handle_cli_never_returns_pass_and_does_not_repair_source(tmp_path):
    source = tmp_path/'duplicate.dxf'; duplicate_handle_file(source)
    before = extract.sha256(source)
    output = tmp_path/'neutral'
    process = subprocess.run([sys.executable, '-B', str(SHARED/'extract_cad_neutral.py'),
        '--source-dxf', str(source), '--output-dir', str(output)], capture_output=True)
    assert process.returncode == 3
    manifest = json.loads((output/'CAD中性取证清单.json').read_text(encoding='utf-8'))
    coverage = json.loads((output/'CAD读取覆盖与待补读.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PARTIAL'
    assert manifest['source_identity_issues'] == coverage['source_identity_issues']
    assert coverage['source_identity_status'] == 'conflict'
    assert extract.sha256(source) == before


def test_loader_capture_is_not_defeated_by_silenced_logger_and_restores_settings(tmp_path):
    source = tmp_path/'duplicate.dxf'; duplicate_handle_file(source)
    logger = logging.getLogger('ezdxf')
    old_level, old_disabled, old_handlers = logger.level, logger.disabled, list(logger.handlers)
    old_global_disable = logger.manager.disable
    try:
        logger.setLevel(logging.CRITICAL); logger.disabled = True
        logging.disable(logging.CRITICAL)
        _, diagnostics = geometry.read_dxf_with_diagnostics(source)
        assert diagnostics['source_identity_issues']
        assert logger.level == logging.CRITICAL and logger.disabled is True
        assert logger.handlers == old_handlers
        assert logger.manager.disable == logging.CRITICAL
    finally:
        logger.setLevel(old_level); logger.disabled = old_disabled
        logging.disable(old_global_disable)


def test_normal_dxf_read_has_no_identity_conflict_but_does_not_claim_full_preservation(tmp_path):
    source = tmp_path/'clean.dxf'
    doc = ezdxf.new(); doc.modelspace().add_line((0, 0), (3, 4)); doc.saveas(source)
    before = extract.sha256(source)
    loaded, diagnostics = geometry.read_dxf_with_diagnostics(source)
    assert len(loaded.modelspace()) == 1
    assert diagnostics['status'] == 'PASS'
    assert diagnostics['source_identity_issues'] == []
    assert diagnostics['source_read_warnings'] == []
    assert diagnostics['parser_entity_preservation_proven'] is False
    assert extract.sha256(source) == before
