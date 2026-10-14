"""Finite standard-DXF viewport evidence, with independent geometric expectations.

All drawings are synthetic.  The tested support boundary is a positive-Z top
view, zero twist, a rectangular viewport and no depth clipping.  These checks
do not certify rendered visibility, drawing semantics or engineering quantities.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys

import ezdxf
from ezdxf.math import Matrix44
import pytest


SHARED = Path(__file__).resolve().parents[1] / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_recognition_workbench as workbench
import qto_viewport_evidence as evidence


def drawing(unit=4):
    doc = ezdxf.new('R2018')
    doc.units = unit
    return doc


def viewport(doc, layout='Layout1', **changes):
    if layout not in doc.layouts:
        doc.layouts.new(layout)
    vp = doc.layouts.get(layout).add_viewport(
        center=(50, 40), size=(100, 50), view_center_point=(5, -2),
        view_height=200)
    vp.dxf.view_target_point = (1000, -2000, 37)
    vp.dxf.view_direction_vector = (0, 0, 1)
    for name, value in changes.items():
        setattr(vp.dxf, name, value)
    return vp


def build_index(tmp_path, doc):
    path = tmp_path / 'synthetic.dxf'
    doc.saveas(path)
    source = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return workbench.build_index(source)


def simple_row(**changes):
    row = dict(object_id='object', parent_object_id=None, entity_type='LINE',
               geometry_status='measurable', bbox=[900, -2050, 1100, -1950],
               anchor=None, issues=[], effective_layer='0', visible_in_entity=True)
    row.update(changes)
    return row


def assert_points(actual, expected):
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected):
        assert a == pytest.approx(e, abs=1e-8)


def test_target_xy_translation_and_finite_target_z_have_closed_form_bounds():
    ctx = evidence.viewport_context(viewport(drawing()))
    assert ctx['supported'] is True
    assert ctx['unsupported_reasons'] == []
    assert ctx['scale'] == pytest.approx(.25)
    # View center is target + view-center offset; half-width/height = 200/100.
    assert ctx['model_bounds'] == pytest.approx([805, -2102, 1205, -1902])
    assert ctx['paper_bounds'] == pytest.approx([0, 15, 100, 65])
    assert ctx['legacy_without_target_bounds'] == pytest.approx([-195, -102, 205, 98])
    assert ctx['inverse_projection_plane']['paper_z'] == 0
    assert ctx['inverse_projection_plane']['model_z'] == 37
    assert_points(ctx['inverse_projected_model_corners'], [
        [805, -2102, 37], [1205, -2102, 37],
        [1205, -1902, 37], [805, -1902, 37]])
    # Deliberately use literal off-plane points, independently of the module's
    # analytic proof.  Their XY projection is constant while Z is preserved.
    matrix = Matrix44(ctx['model_to_paper_matrix'])
    assert_points([list(matrix.transform(p)) for p in [
        (1005, -2002, 37), (805, -2102, -3), (1205, -1902, 117)]],
        [[50, 40, 0], [0, 15, -10], [100, 65, 20]])
    inverse = Matrix44(ctx['paper_to_model_matrix'])
    assert list(inverse.transform((50, 40, 7.5))) == pytest.approx([1005, -2002, 67])
    proof = evidence.analytic_check(ctx, extra_z=[-3, 37, 117])
    assert proof['closed_form_bounds'] == pytest.approx([805, -2102, 1205, -1902])
    assert proof['bounds_max_error'] <= 1e-8


@pytest.mark.parametrize('target_z', [-1e4, -2.5, 0, 37, 1e4])
def test_target_z_changes_depth_plane_without_moving_xy_window(target_z):
    ctx = evidence.viewport_context(viewport(drawing(), view_target_point=(1000, -2000, target_z)))
    assert ctx['supported']
    assert ctx['model_bounds'] == pytest.approx([805, -2102, 1205, -1902])
    assert all(p[2] == pytest.approx(target_z) for p in ctx['inverse_projected_model_corners'])
    assert ctx['inverse_projection_plane']['model_z'] == target_z


@pytest.mark.parametrize('direction', [(0, 0, 1e-300), (0, 0, 1e-12), (0, 0, .25), (0, 0, 1), (0, 0, 4)])
def test_positive_z_view_direction_magnitude_does_not_change_the_window(direction):
    ctx = evidence.viewport_context(viewport(drawing(), view_direction_vector=direction))
    assert ctx['supported']
    assert ctx['model_bounds'] == pytest.approx([805, -2102, 1205, -1902])


@pytest.mark.parametrize('changes,reason', [
    ({'flags': 1}, 'perspective'),
    ({'flags': 2}, 'front_clip'),
    ({'flags': 4}, 'back_clip'),
    ({'flags': 16}, 'front_clip_not_at_eye'),
    ({'flags': 65536}, 'non_rectangular_clip'),
    ({'clipping_boundary_handle': 'ABCD'}, 'clip_boundary_handle_present'),
    ({'view_direction_vector': (0, 0, -1)}, 'non_top_view'),
    ({'view_direction_vector': (1, 0, 1)}, 'non_top_view'),
    ({'view_direction_vector': (1e-11, 0, 1e-12)}, 'non_top_view'),
    ({'view_direction_vector': (0, 0, 0)}, 'non_top_view'),
    ({'view_twist_angle': .01}, 'nonzero_twist'),
    ({'width': 0}, 'invalid_width'),
    ({'height': -1}, 'invalid_height'),
    ({'view_height': 0}, 'invalid_view_height'),
    ({'width': float('nan')}, 'invalid_width'),
    ({'view_target_point': (0, float('inf'), 0)}, 'nonfinite_or_missing_view_target_point'),
    ({'view_center_point': (0, 0, 2)}, 'nonzero_z_view_center_point'),
])
def test_unsupported_or_invalid_viewport_has_no_fabricated_window(changes, reason):
    ctx = evidence.viewport_context(viewport(drawing(), **changes))
    assert not ctx['supported']
    assert reason in ctx['unsupported_reasons']
    assert 'model_bounds' not in ctx
    assert 'paper_to_model_matrix' not in ctx


@pytest.mark.parametrize('changes', [
    {'view_twist_angle': float('nan')},
    {'view_direction_vector': (0, 0, float('inf'))},
    {'center': (float('nan'), 40, 0)},
    {'view_center_point': (5, float('inf'), 0)},
])
def test_nonfinite_view_parameters_fail_closed(changes):
    ctx = evidence.viewport_context(viewport(drawing(), **changes))
    assert not ctx['supported']
    assert ctx['unsupported_reasons']
    assert 'model_bounds' not in ctx


@pytest.mark.parametrize('changes', [{'id': 1}, {'status': 0}, {'flags': 131072}])
def test_overall_or_inactive_viewport_is_retained_as_unsupported(changes):
    ctx = evidence.viewport_context(viewport(drawing(), **changes))
    assert not ctx['supported']
    assert 'inactive_or_paper_overall_viewport' in ctx['unsupported_reasons']
    assert 'model_bounds' not in ctx


@pytest.mark.parametrize('box,expected', [
    ([1, 1, 9, 9], 'fully_inside'),
    ([0, 0, 10, 10], 'fully_inside'),
    ([10, 5, 12, 6], 'boundary_overlap_candidate'),
    ([-1, 5, 0, 6], 'boundary_overlap_candidate'),
    ([10.001, 5, 12, 6], 'outside'),
    ([-2, 5, -.001, 6], 'outside'),
    ([-1, -1, 11, 11], 'boundary_overlap_candidate'),
])
def test_only_complete_known_bounds_prove_geometric_relation(box, expected):
    assert evidence.geometric_relation(simple_row(bbox=box), [0, 0, 10, 10]) == expected


@pytest.mark.parametrize('changes', [
    {'geometry_status': 'unresolved'},
    {'geometry_status': 'unsupported_measurement'},
    {'issues': ['unverified_extent']},
    {'geometry_status': 'container', 'bounds_complete': False},
    {'bbox': None},
    {'bbox': [float('nan'), 1e6, 2e6, 2e6]},
    {'bbox': [2e6, 2e6, 1e6, 1e6]},
])
def test_unknown_or_broken_far_extent_is_never_classified_outside(changes):
    row = simple_row(bbox=[1e6, 1e6, 2e6, 2e6], **changes) if 'bbox' not in changes else simple_row(**changes)
    assert evidence.geometric_relation(row, [0, 0, 10, 10]) == 'unknown'


def test_complete_container_can_be_proven_outside():
    row = simple_row(geometry_status='container', bounds_complete=True, bbox=[11, 1, 12, 2])
    assert evidence.geometric_relation(row, [0, 0, 10, 10]) == 'outside'


@pytest.mark.parametrize('anchor,expected', [
    ([5, 5, 200], 'anchor_inside'),
    ([0, 10, -300], 'anchor_inside'),
    ([11, 5, 0], 'anchor_outside'),
    ([float('nan'), 5, 0], 'unknown'),
])
def test_text_relation_uses_anchor_without_claiming_glyph_extent(anchor, expected):
    row = simple_row(entity_type='TEXT', geometry_status='location_only', anchor=anchor,
                     bbox=[-1e6, -1e6, 1e6, 1e6], text='synthetic label')
    assert evidence.geometric_relation(row, [0, 0, 10, 10]) == expected


@pytest.mark.parametrize('state', ['off', 'frozen', 'no_plot', 'viewport_frozen', 'entity_invisible'])
def test_recorded_visibility_checks_ancestor_states_separately_from_geometry(state):
    doc = drawing()
    doc.layers.new('parent-layer')
    ctx = evidence.viewport_context(viewport(doc))
    parent = simple_row(object_id='parent', effective_layer='parent-layer',
                        geometry_status='container', bounds_complete=True)
    child = simple_row(object_id='child', parent_object_id='parent')
    if state == 'off':
        doc.layers.get('parent-layer').off()
    elif state == 'frozen':
        doc.layers.get('parent-layer').freeze()
    elif state == 'no_plot':
        doc.layers.get('parent-layer').dxf.plot = 0
    elif state == 'viewport_frozen':
        ctx['raw']['frozen_layers'] = ['PARENT-LAYER']
    else:
        parent['visible_in_entity'] = False
    result = evidence.evaluate(child, {'child': child, 'parent': parent}, doc, ctx)
    assert result['geometric_relation'] == 'fully_inside'
    assert not result['visibility']['eligible_by_recorded_states']
    assert result['visibility']['reasons']
    assert [r['object_id'] for r in result['visibility']['ancestor_chain']] == ['child', 'parent']


def test_normal_layer_chain_is_display_eligible_without_quantity_adoption():
    doc = drawing()
    ctx = evidence.viewport_context(viewport(doc))
    row = simple_row()
    result = evidence.evaluate(row, {'object': row}, doc, ctx)
    assert result['visibility']['eligible_by_recorded_states']
    assert result['visibility']['reasons'] == []
    assert result['applicability'] == 'model_interpretation_required_no_material_or_quantity_adoption'


@pytest.mark.parametrize('failure', ['missing_parent', 'cycle', 'missing_layer', 'missing_entity_visibility'])
def test_incomplete_visibility_chain_remains_unknown(failure):
    doc = drawing()
    ctx = evidence.viewport_context(viewport(doc))
    row = simple_row()
    byid = {'object': row}
    if failure == 'missing_parent':
        row['parent_object_id'] = 'absent'
    elif failure == 'cycle':
        row['parent_object_id'] = 'object'
    elif failure == 'missing_layer':
        row['effective_layer'] = 'absent-layer'
    else:
        row.pop('visible_in_entity')
    result = evidence.visibility(row, byid, doc, ctx)
    assert result['recorded_state_status'] == 'unknown'
    assert result['eligible_by_recorded_states'] is False
    assert result['reasons']


def test_native_line_z_is_not_replaced_by_the_inverse_projection_plane(tmp_path):
    doc = drawing()
    line = doc.modelspace().add_line((1000, -2000, -4), (1010, -1990, 9))
    vp = viewport(doc)
    index = build_index(tmp_path, doc)
    row = next(r for r in index['objects'] if r['source_handle'] == line.dxf.handle)
    proof = evidence.line_source_proof(row, {r['object_id']: r for r in index['objects']},
                                       doc, evidence.viewport_context(vp))
    assert proof['supported']
    assert_points(proof['WCS_endpoints'], [[1000, -2000, -4], [1010, -1990, 9]])
    assert proof['actual_WCS_Z'] == [-4, 9]
    assert_points(proof['paper_XY_closed_form'], [[48.75, 40.5], [51.25, 43]])
    assert proof['relation'] == 'fully_inside'


def test_nested_insert_line_proof_preserves_column_vector_convention(tmp_path):
    doc = drawing()
    leaf = doc.blocks.new('leaf', base_point=(1, 2, 3))
    line = leaf.add_line((2, 3, 5), (4, 3, 5))
    outer = doc.blocks.new('outer', base_point=(-2, 1, 4))
    inner = outer.add_blockref('leaf', (10, 20, 30),
                               dxfattribs={'xscale': 2, 'yscale': 3, 'zscale': 4, 'rotation': 90})
    root = doc.modelspace().add_blockref('outer', (1000, -2000, 100),
                                        dxfattribs={'xscale': 1, 'yscale': 2, 'zscale': .5, 'rotation': -90})
    ctx = evidence.viewport_context(viewport(doc))
    index = build_index(tmp_path, doc)
    byid = {r['object_id']: r for r in index['objects']}
    row = next(r for r in index['objects'] if r['source_handle'] == line.dxf.handle)
    # Independently hand-composed transform: (4*x+1034, 3*y-2018, 2*z+107).
    assert row['instance_transform'] == pytest.approx([
        4, 0, 0, 1034, 0, 3, 0, -2018, 0, 0, 2, 107, 0, 0, 0, 1])
    assert row['source_path'] == [root.dxf.handle, inner.dxf.handle, line.dxf.handle]
    proof = evidence.line_source_proof(row, byid, doc, ctx)
    assert proof['supported']
    assert_points(proof['raw_LINE_start_end'], [[2, 3, 5], [4, 3, 5]])
    assert_points(proof['WCS_endpoints'], [[1042, -2009, 117], [1050, -2009, 117]])
    assert_points(proof['paper_XY_closed_form'], [[59.25, 38.25], [61.25, 38.25]])
    assert proof['actual_WCS_Z'] == [117, 117]
    assert proof['endpoints_inside'] == [True, True]
    assert proof['source_to_index_endpoint_max_error'] < 1e-8
    assert proof['via_parent_chain_endpoint_max_error'] < 1e-8
    assert proof['transform_max_coefficient_error'] < 1e-8


def partial_parent_fixture(tmp_path, *, unresolved_parent_state=False):
    doc = drawing()
    block = doc.blocks.new('partially-readable')
    line = block.add_line((0, 0, 7), (20, 10, 7))
    sibling = block.add_spline(fit_points=[(1e6, 1e6), (1e6 + 10, 1e6 + 20), (1e6 + 20, 1e6)])
    parent = doc.modelspace().add_blockref('partially-readable', (1000, -2000, 10),
                                          dxfattribs={'rotation': 90})
    if unresolved_parent_state:
        parent.new_extension_dict()
    ctx = evidence.viewport_context(viewport(doc))
    index = build_index(tmp_path, doc)
    byid = {r['object_id']: r for r in index['objects']}
    rows = {r['source_handle']: r for r in index['objects']}
    return doc, ctx, byid, rows[parent.dxf.handle], rows[line.dxf.handle], rows[sibling.dxf.handle]


def test_unsupported_sibling_does_not_invalidate_proven_child_line(tmp_path):
    doc, ctx, byid, parent, line, sibling = partial_parent_fixture(tmp_path)
    assert parent['bounds_complete'] is False
    assert parent['issues'] == ['块范围仅覆盖已解析内容，范围外仍可能存在未解析子对象']
    assert evidence.geometric_relation(parent, ctx['model_bounds']) == 'unknown'
    assert evidence.geometric_relation(sibling, ctx['model_bounds']) == 'unknown'
    assert line['geometry_status'] == 'measurable' and line['issues'] == []
    proof = evidence.line_source_proof(line, byid, doc, ctx)
    assert proof['supported']
    # Rotation 90 degrees maps (x,y,z) to (-y,x,z), then adds the INSERT.
    assert_points(proof['WCS_endpoints'], [[1000, -2000, 17], [990, -1980, 17]])
    assert proof['actual_WCS_Z'] == [17, 17]
    assert proof['source_to_index_endpoint_max_error'] < 1e-8
    assert proof['via_parent_chain_endpoint_max_error'] < 1e-8
    assert proof['parent_coverage_warnings'] == [{
        'object_id': parent['object_id'], 'source_handle': parent['source_handle'],
        'issues': parent['issues']}]
    assert proof['parent_INSERT_chain'][0]['coverage_only_issues'] == parent['issues']
    # A successful child proof does not promote the parent's unknown extent.
    assert evidence.geometric_relation(parent, ctx['model_bounds']) == 'unknown'
    assert parent['bounds_complete'] is False


def test_partial_parent_with_additional_native_state_warning_still_rejects_child_proof(tmp_path):
    doc, ctx, byid, parent, line, _ = partial_parent_fixture(tmp_path, unresolved_parent_state=True)
    assert parent['bounds_complete'] is False
    assert '块范围仅覆盖已解析内容，范围外仍可能存在未解析子对象' in parent['issues']
    assert '可能含动态块或扩展数据；可见状态需原生CAD或模型另核' in parent['issues']
    proof = evidence.line_source_proof(line, byid, doc, ctx)
    assert proof['supported'] is False
    assert proof['reasons']


@pytest.mark.parametrize('mutation', ['parent_identity', 'child_transform', 'child_geometry_issue'])
def test_coverage_warning_exception_does_not_relax_identity_transform_or_geometry(tmp_path, mutation):
    doc, ctx, byid, parent, line, _ = partial_parent_fixture(tmp_path)
    if mutation == 'parent_identity':
        parent['parent_object_id'] = 'not-the-native-parent'
    elif mutation == 'child_transform':
        line['instance_transform'][3] += 10
    else:
        line['issues'] = ['unresolved_LINE_geometry']
    proof = evidence.line_source_proof(line, byid, doc, ctx)
    assert proof['supported'] is False
    assert proof['reasons']


def test_broken_source_line_proof_cannot_claim_success(tmp_path):
    doc = drawing()
    doc.modelspace().add_line((1000, -2000, 0), (1010, -1990, 0))
    ctx = evidence.viewport_context(viewport(doc))
    index = build_index(tmp_path, doc)
    row = copy.deepcopy(index['objects'][0])
    row['geometry']['parts'][0]['start'][0] += 10
    proof = evidence.line_source_proof(row, {row['object_id']: row}, doc, ctx)
    assert not proof['supported']
    assert proof['reasons']


def test_all_layouts_keep_empty_and_unsupported_windows_and_unique_unknown_ids(tmp_path):
    doc = drawing(unit=0)
    model = doc.modelspace()
    inside = model.add_line((900, -2050, -5), (950, -2000, -5))
    outside = model.add_line((1500, -2050, 7), (1550, -2000, 7))
    text = model.add_text('synthetic label', dxfattribs={'insert': (1000, -2000)})
    spline = model.add_spline(fit_points=[(1e6, 1e6), (1e6 + 10, 1e6 + 20), (1e6 + 20, 1e6)])
    first = viewport(doc)
    second = viewport(doc, layout='Second')
    empty = viewport(doc, layout='Empty', view_target_point=(-10000, -10000, 37))
    unsupported = viewport(doc, layout='Unsupported', flags=1)
    doc.layouts.new('NoWindow')
    index = build_index(tmp_path, doc)
    result = evidence.inspect_viewports(doc, index)
    layouts = {r['layout']: r for r in result['layouts']}
    assert set(layouts) == set(doc.layouts.names()) - {'Model'}
    assert layouts['NoWindow']['viewports'] == []
    records = {r['handle']: r for layout in layouts.values() for r in layout['viewports']}
    assert {first.dxf.handle, second.dxf.handle, empty.dxf.handle, unsupported.dxf.handle} <= set(records)
    uid = next(r['object_id'] for r in index['objects'] if r['source_handle'] == spline.dxf.handle)
    assert records[first.dxf.handle]['unknown_object_ids'] == [uid]
    assert records[second.dxf.handle]['unknown_object_ids'] == [uid]
    assert records[empty.dxf.handle]['unknown_object_ids'] == [uid]
    assert result['unique_unknown_object_ids'] == [uid]
    assert result['unique_unknown_count'] == 1
    assert result['raw_unit'] == 'Unitless'
    assert result['unit_conversion'] is None
    assert result['quantity_calculation'] is False
    assert result['formal_adoption'] is False
    assert result['source'] == index['source']
    assert result['index_sha256'] == index['index_sha256']
    unsupported_record = records[unsupported.dxf.handle]
    assert not unsupported_record['context']['supported']
    assert 'model_bounds' not in unsupported_record['context']
    assert unsupported_record['selections'] == []
    assert unsupported_record['status'] != 'empty'
    assert unsupported_record['relation_counts'] is None
    assert unsupported_record['unknown_object_ids'] is None
    assert unsupported_record['missing_line_relations'] is None
    assert records[empty.dxf.handle]['missing_line_relations'] == ['fully_inside']
    assert records[empty.dxf.handle]['status'] != 'empty'
    assert not records[empty.dxf.handle]['empty_viewport_proven']
    assert not records[empty.dxf.handle]['known_geometry_intersects']
    picks = {p['purpose']: p['evaluation'] for p in records[first.dxf.handle]['selections']}
    assert records[first.dxf.handle]['line_pair_proven'] is True
    assert picks['first_standard_LINE_inside']['object']['source_handle'] == inside.dxf.handle
    assert picks['first_standard_LINE_outside']['object']['source_handle'] == outside.dxf.handle
    assert picks['first_text_anchor_inside']['object']['source_handle'] == text.dxf.handle
    assert picks['first_unknown_retained']['object']['source_handle'] == spline.dxf.handle


def test_unverified_array_line_candidates_cannot_form_a_proven_line_pair(tmp_path):
    doc = drawing()
    block = doc.blocks.new('repeated-source')
    block.add_line((0, 0, 10), (20, 10, 10))
    doc.modelspace().add_blockref('repeated-source', (900, -2050, 0),
                                 dxfattribs={'column_count': 2, 'column_spacing': 30})
    doc.modelspace().add_line((1500, -2050, 7), (1550, -2000, 7))
    vp = viewport(doc)
    index = build_index(tmp_path, doc)
    result = evidence.inspect_viewports(doc, index)
    record = next(r for l in result['layouts'] for r in l['viewports'] if r['handle'] == vp.dxf.handle)
    picks = {p['purpose']: p['evaluation'] for p in record['selections']}
    assert set(picks) >= {'first_standard_LINE_inside', 'first_standard_LINE_outside'}
    assert picks['first_standard_LINE_inside']['geometric_relation'] == 'fully_inside'
    assert not picks['first_standard_LINE_inside']['LINE_source_proof']['supported']
    assert picks['first_standard_LINE_inside']['LINE_source_proof']['reasons']
    assert picks['first_standard_LINE_outside']['LINE_source_proof']['supported']
    assert record['line_pair_proven'] is False
    assert record['status'] != 'SUPPORTED_LINE_PAIR_RETAINED'


def test_truly_empty_model_is_recorded_without_omitting_viewport(tmp_path):
    doc = drawing()
    vp = viewport(doc)
    index = build_index(tmp_path, doc)
    result = evidence.inspect_viewports(doc, index)
    record = next(r for l in result['layouts'] for r in l['viewports'] if r['handle'] == vp.dxf.handle)
    assert record['context']['supported']
    assert record['status'] == 'empty'
    assert record['empty_viewport_proven'] is True
    assert record['unknown_object_ids'] == []
    assert record['missing_line_relations'] == ['fully_inside', 'outside']
    assert record['selections'] == []


@pytest.mark.parametrize('unit_code,unit_name', [(0, 'Unitless'), (123, 'Unknown')])
def test_original_insunits_code_is_retained_without_guessing_millimeters(tmp_path, unit_code, unit_name):
    doc = drawing()
    doc.header['$INSUNITS'] = unit_code
    viewport(doc)
    index = build_index(tmp_path, doc)
    result = evidence.inspect_viewports(doc, index)
    assert result['raw_insunits'] == unit_code
    assert result['raw_unit'] == unit_name
    assert result['unit_conversion'] is None
    assert result['quantity_calculation'] is False
    assert result['formal_adoption'] is False


@pytest.mark.parametrize('mutation', ['scope', 'scope_kind_only', 'layout', 'schema', 'digest'])
def test_inspection_rejects_partial_or_stale_index_without_treating_it_as_empty(tmp_path, mutation):
    doc = drawing()
    doc.modelspace().add_line((1000, -2000, 0), (1010, -1990, 0))
    viewport(doc)
    index = build_index(tmp_path, doc)
    if mutation == 'scope':
        index['index_scope'].update(kind='local', bounds=[900, -2050, 1100, -1950])
    elif mutation == 'scope_kind_only':
        index['index_scope']['kind'] = 'local'
    elif mutation == 'layout':
        index['layout'] = 'Layout1'
    elif mutation == 'schema':
        index['schema_version'] = 'legacy'
    else:
        index['objects'][0]['bbox'] = [1e6, 1e6, 2e6, 2e6]
    if mutation != 'digest':
        index['index_sha256'] = workbench.index_digest(index)
    with pytest.raises(ValueError):
        evidence.inspect_viewports(doc, index)
