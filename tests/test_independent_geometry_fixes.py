"""Independent DXF regressions for fitted POLYLINE roles and full-turn ARCs.

The spline fixtures use ezdxf's public R12Spline writer, including its display
VERTEX records and control frame. Expected dimensions come from straight lines,
circles and a separately sampled ellipse integral, not the extractor's formulas.
These tests verify geometry and evidence handling, not DWG recognition coverage.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import ezdxf
from ezdxf.math import UCS
from ezdxf.render import R12Spline
import pytest


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_cad_geometry as geometry


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract = load_module('independent_geometry_extract',
                      'skills/_qto-shared-evidence/scripts/extract_cad_neutral.py')
detail = load_module('independent_geometry_detail',
                     'skills/qto-pipe-networks/scripts/measure_pipe_detail_routes.py')
pipe = load_module('independent_geometry_pipe',
                   'skills/qto-pipe-networks/scripts/build_pipe_inference_input.py')
component = load_module('independent_geometry_component',
                        'skills/qto-components-points/scripts/build_component_instances_from_neutral.py')


def drawing():
    doc = ezdxf.new('R2018')
    doc.units = 4
    return doc


def row(entity):
    return extract.convert(entity, layout='Model', parent_block=None,
                           source_hash='a' * 64, raw_unit='Millimeters', sequence=1)


def extract_package(tmp_path, doc, expected_status):
    # Valid fixtures must not rely on ezdxf repairing the input.
    audit = doc.audit()
    assert not audit.errors and not audit.fixes
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / 'neutral'
    process = subprocess.run(
        [sys.executable, '-B', str(SHARED / 'extract_cad_neutral.py'),
         '--source-dxf', str(source), '--output-dir', str(output)],
        capture_output=True, text=True, encoding='utf-8')
    assert process.returncode == (0 if expected_status == 'PASS' else 3), process.stdout + process.stderr
    manifest = json.loads((output / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    records = [json.loads(line) for line in
               (output / '中性布局图元.ndjson').read_text(encoding='utf-8').splitlines()]
    coverage = json.loads((output / 'CAD读取覆盖与待补读.json').read_text(encoding='utf-8'))
    assert manifest['status'] == coverage['status'] == expected_status
    assert manifest['geometry_schema_version'] == geometry.GEOMETRY_SCHEMA
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    return records, coverage


@pytest.mark.parametrize('degree', [2, 3])
@pytest.mark.parametrize('segments', [8, 11])
def test_r12_spline_frame_does_not_triple_straight_length(tmp_path, degree, segments):
    doc = drawing()
    controls = [(i * 10000, 0) for i in range(degree + 1)]
    entity = R12Spline(controls, degree=degree, closed=False).render(doc.modelspace(), segments=segments)
    source_vertex_count = len(entity.vertices)

    records, coverage = extract_package(tmp_path, doc, 'PASS')
    result = records[0]
    assert result['entity_handle'] == entity.dxf.handle
    assert result['raw_length'] == pytest.approx(degree * 10000, rel=1e-12)
    assert result['geometry_status'] == 'supported'
    assert result['geometry_capability'] == 'measurable_curve'
    assert result['bbox'] == pytest.approx([0, 0, degree * 10000, 0])
    assert coverage['pending_object_count'] == 0

    raw = result['raw_geometry']
    assert raw['polyline_flags'] == 4
    assert raw['smooth_type'] == {2: 5, 3: 6}[degree]
    assert raw['vertex_flags'] == [8] * (segments + 1) + [16] * (degree + 1)
    assert raw['vertex_handles'] == [v.dxf.handle for v in entity.vertices]
    assert len(raw['vertices']) == source_vertex_count == segments + degree + 2
    assert raw['measurement_vertex_indices'] == list(range(segments + 1))
    assert raw['excluded_control_vertex_indices'] == list(range(segments + 1, source_vertex_count))
    assert raw['measurement_basis'] == 'proven_monotone_collinear_spline_fit'


def test_straight_spline_fit_keeps_ocs_and_instance_transform():
    doc = drawing()
    ucs = UCS(origin=(13, -7, 19), ux=(0.6, 0, 0.8), uy=(0, 1, 0))
    entity = R12Spline([(0, 0), (10000, 0), (20000, 0)], degree=2, closed=False).render(
        doc.modelspace(), segments=9, ucs=ucs)
    result = row(entity)
    a, b = [list(ucs.to_wcs(p)) for p in [(0, 0, 0), (20000, 0, 0)]]
    assert result['raw_length'] == pytest.approx(20000, rel=1e-12)
    assert result['start'] == pytest.approx(a)
    assert result['end'] == pytest.approx(b)

    matrix = (-2, 0, 0, 100, 0, 3, 0, -50, 0, 0, 4, 70, 0, 0, 0, 1)
    transformed = geometry.transform_row(result, matrix, 'outer/inner', 'Model')
    expected_start = [-2 * a[0] + 100, 3 * a[1] - 50, 4 * a[2] + 70]
    expected_end = [-2 * b[0] + 100, 3 * b[1] - 50, 4 * b[2] + 70]
    assert transformed['raw_length'] == pytest.approx(math.dist(expected_start, expected_end), rel=1e-12)
    assert transformed['source_definition_record_id'] == result['record_id']
    assert transformed['raw_geometry'] == result['raw_geometry']


@pytest.mark.parametrize('controls,degree,closed', [
    ([(0, 0), (10000, 8000), (20000, 0)], 2, False),
    ([(0, 0), (5000, 8000), (15000, -8000), (20000, 0)], 3, False),
    ([(0, 0), (10000, 0), (20000, 0)], 2, True),
    ([(0, 0), (12000, 0), (-2000, 0), (10000, 0)], 3, False),
], ids=['curved_quadratic', 'curved_cubic', 'closed_fit', 'backtracking_frame'])
def test_unproven_spline_fit_remains_explicitly_partial(tmp_path, controls, degree, closed):
    doc = drawing()
    entity = R12Spline(controls, degree=degree, closed=closed).render(doc.modelspace(), segments=8)
    records, coverage = extract_package(tmp_path, doc, 'PARTIAL')
    result = records[0]
    assert result['entity_handle'] == entity.dxf.handle
    assert result['geometry'] is None and result['raw_length'] is None
    assert result['bbox'] is None  # Unknown fitted extent must not silently prune the object.
    assert result['geometry_status'] == 'unsupported'
    assert 'spline_fit_polyline_measurement_unverified' in result['geometry_issue_codes']
    assert result['raw_geometry']['measurement_vertex_indices'] == []
    assert result['raw_geometry']['excluded_control_vertex_indices']
    assert len(result['raw_geometry']['vertices']) == len(entity.vertices)
    assert coverage['pending_object_count'] == 1
    assert coverage['pending_objects'][0]['entity_handle'] == entity.dxf.handle


def test_curve_vertex_role_in_spline_fit_is_separate_from_frame():
    # Autodesk also permits generated curve-fit vertices (flag 1) in a spline
    # fit, for example with a negative SPLINESEGS setting. Frame points stay 16.
    doc = drawing()
    entity = R12Spline([(0, 0), (10000, 0), (20000, 0)], degree=2, closed=False).render(doc.modelspace(), segments=5)
    for vertex in entity.vertices:
        if vertex.dxf.flags == 8:
            vertex.dxf.flags = 1
    result = row(entity)
    assert result['raw_length'] == pytest.approx(20000)
    assert result['raw_geometry']['vertex_flags'] == [1] * 6 + [16] * 3
    assert result['raw_geometry']['excluded_control_vertex_indices'] == [6, 7, 8]


@pytest.mark.parametrize('mutation,issue', [
    ('missing_frame', 'spline_fit_polyline_data_incomplete'),
    ('mixed_roles', 'polyline_vertex_roles_unverified'),
    ('unmarked_vertex', 'polyline_vertex_roles_unverified'),
    ('unknown_fit_type', 'spline_fit_polyline_data_incomplete'),
])
def test_incomplete_or_ambiguous_fit_metadata_cannot_publish_length(mutation, issue):
    # These intentionally malformed records check fail-closed handling; they are
    # not evidence of a legal CAD drawing being misread.
    entity = R12Spline([(0, 0), (10000, 0), (20000, 0)], degree=2, closed=False).render(drawing().modelspace(), segments=4)
    if mutation == 'missing_frame':
        del entity.vertices[-3:]
    elif mutation == 'mixed_roles':
        entity.vertices[-1].dxf.flags = 16 | 8
    elif mutation == 'unmarked_vertex':
        entity.vertices[1].dxf.flags = 0
    else:
        entity.dxf.smooth_type = 0
    result = row(entity)
    assert result['raw_length'] is None and result['geometry'] is None
    assert result['geometry_issue_codes'] == [issue]
    assert result['raw_geometry']['measurement_vertex_indices'] == []
    assert len(result['raw_geometry']['vertex_flags']) == len(entity.vertices)


@pytest.mark.parametrize('curve_fit', [False, True])
def test_drawn_polyline_bulge_chain_keeps_curve_fit_extra_vertices(curve_fit):
    bulge = math.tan(math.pi / 8)
    entity = drawing().modelspace().add_polyline2d(
        [(0, 0, bulge), (5, -5, bulge), (10, 0, 0)], format='xyb')
    if curve_fit:
        entity.dxf.flags = 2
        entity.vertices[0].dxf.flags = 2
        entity.vertices[0].dxf.tangent = 270
        entity.vertices[1].dxf.flags = 1
    result = row(entity)
    assert result['raw_length'] == pytest.approx(5 * math.pi, rel=1e-12)
    assert result['bbox'] == pytest.approx([0, -5, 10, 0], abs=1e-12)
    assert len(result['geometry']['parts']) == 2
    assert result['raw_geometry']['measurement_vertex_indices'] == [0, 1, 2]
    if curve_fit:
        assert result['raw_geometry']['vertex_flags'] == [2, 1, 0]
        assert result['raw_geometry']['vertex_tangents'][0] == 270


def test_ordinary_3d_polyline_vertex_flags_are_not_spline_roles():
    entity = drawing().modelspace().add_polyline3d([(0, 0, 2), (3, 4, 14), (3, 4, 26)])
    result = row(entity)
    assert result['raw_length'] == pytest.approx(25)
    assert result['bbox3d'] == pytest.approx([0, 0, 2, 3, 4, 26])
    assert result['raw_geometry']['vertex_flags'] == [32, 32, 32]


def signed_sampled_area(part, count=1024):
    points = [detail.part_point(part, i / count) for i in range(count + 1)]
    return math.fsum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:])) / 2


@pytest.mark.parametrize('start,end', [(0, 360), (90, 450), (-180, 180), (0, -360), (180, -180),
                                     (90, 810), (90, -630)])
def test_full_turn_arc_has_consistent_length_bounds_samples_and_area(start, end):
    doc = drawing()
    entity = doc.modelspace().add_arc((5, 7, 11), 3, start, end)
    result = row(entity)
    part = detail.Drawing(doc).part({'path': [entity.dxf.handle]})
    assert result['raw_length'] == pytest.approx(6 * math.pi, rel=1e-12)
    assert result['geometry_status'] == 'supported'
    assert result['closed'] is result['geometry']['closed'] is True
    assert result['geometry_signature']['closed'] is True
    assert result['bbox3d'] == pytest.approx([2, 4, 11, 8, 10, 11])
    assert result['raw_geometry']['start_angle_degrees'] == start
    assert result['raw_geometry']['end_angle_degrees'] == end
    assert result['start'] == pytest.approx(result['end'], abs=1e-12)
    for fraction in [0, .25, .5, .75, 1]:
        angle = math.radians(start) + fraction * math.tau
        expected = [5 + 3 * math.cos(angle), 7 + 3 * math.sin(angle), 11]
        assert detail.part_point(part, fraction) == pytest.approx(expected, abs=1e-12)
    # The 1024-edge inscribed polygon differs from the exact disc by < 0.001%.
    assert signed_sampled_area(part) == pytest.approx(9 * math.pi, rel=1e-5)


@pytest.mark.parametrize('start,end,span', [
    (15, 70, 55), (317, 42, 85), (-10, 40, 50),
    (0, 1e-10, 1e-10), (0, 360 - 1e-10, 360 - 1e-10),
])
def test_nonzero_partial_arcs_keep_their_actual_sweep(start, end, span):
    entity = drawing().modelspace().add_arc((0, 0), 3, start, end)
    result = row(entity)
    assert 0 < result['raw_length'] < 6 * math.pi
    assert result['raw_length'] == pytest.approx(3 * math.radians(span), rel=1e-12, abs=0)
    assert result['closed'] is result['geometry']['closed'] is False
    assert result['start'] == pytest.approx(entity.start_point)
    assert result['end'] == pytest.approx(entity.end_point)


@pytest.mark.parametrize('start,end,span', [
    (-1e-14, 0., 1e-14),
    (-1e-15, 1e-15, 2e-15),
    (-1e-14, 360., 1e-14),
    (180., -180. + math.ulp(180.), math.ulp(180.)),
    (0., -1e-14, 360.),
    (-180. + math.ulp(180.), 180., 360.),
], ids=['tiny_to_zero', 'tiny_cross_zero', 'tiny_after_full_turn',
        'tiny_at_signed_remainder_seam', 'almost_full_turn', 'almost_full_at_seam'])
def test_arc_normalization_does_not_promote_roundoff_to_a_full_turn(start, end, span):
    doc = drawing()
    entity = doc.modelspace().add_arc((0, 0), 3, start, end)
    audit = doc.audit()
    assert not audit.errors and not audit.fixes
    result = row(entity)
    # A near-full sweep may round to 360 in a float, but its source endpoints
    # still differ. Such an arc is open; a tiny crossing must remain tiny.
    assert result['raw_length'] == pytest.approx(3 * math.radians(span), rel=1e-12, abs=0)
    assert result['closed'] is result['geometry']['closed'] is False
    assert result['geometry_signature']['closed'] is False
    assert result['geometry_status'] == 'supported'


def test_closed_flags_survive_neutral_cli_for_circle_full_arc_and_partial_arc(tmp_path):
    doc = drawing()
    circle = doc.modelspace().add_circle((0, 0), 3)
    full_arc = doc.modelspace().add_arc((20, 0), 3, 90, 450)
    partial_arc = doc.modelspace().add_arc((40, 0), 3, 10, 100)
    records, _ = extract_package(tmp_path, doc, 'PASS')
    expected = {circle.dxf.handle: True, full_arc.dxf.handle: True, partial_arc.dxf.handle: False}
    for result in records:
        assert result['closed'] is result['geometry']['closed'] is expected[result['entity_handle']]
        assert result['geometry_signature']['closed'] is expected[result['entity_handle']]


def test_equal_angle_arcs_are_pending_and_never_zero_quantity(tmp_path):
    doc = drawing()
    for angle in [0, 360]:
        doc.modelspace().add_arc((0, 0), 3, angle, angle)
    records, coverage = extract_package(tmp_path, doc, 'PARTIAL')
    assert coverage['pending_object_count'] == 2
    for result in records:
        assert result['raw_length'] is None and result['geometry'] is None
        assert result['geometry_issue_codes'] == ['arc_zero_sweep']


def test_full_turn_arc_under_mirrored_nonuniform_block_transform():
    doc = drawing()
    block = doc.blocks.new('ellipse_from_full_arc')
    arc = block.add_arc((0, 0, 2), 3, 90, 450)
    insert = doc.modelspace().add_blockref('ellipse_from_full_arc', (100, 200, 7),
                                        dxfattribs={'xscale': -2, 'yscale': 3})
    transformed = geometry.transform_row(row(arc), row(insert)['insert_matrix_wcs'], 'block', 'Model')
    part = detail.Drawing(doc).part({'path': [insert.dxf.handle, arc.dxf.handle]})
    # Independent periodic trapezoid integration of the known ellipse speed.
    count = 8192
    expected_length = math.tau / count * math.fsum(
        math.hypot(6 * math.sin(i * math.tau / count), 9 * math.cos(i * math.tau / count))
        for i in range(count))
    assert transformed['raw_length'] == pytest.approx(expected_length, rel=1e-10)
    assert transformed['geometry']['closed'] is True
    assert transformed['bbox3d'] == pytest.approx([94, 191, 9, 106, 209, 9])
    assert signed_sampled_area(part) == pytest.approx(-54 * math.pi, rel=1e-5)
    assert detail.part_point(part, 0) == pytest.approx(detail.part_point(part, 1), abs=1e-12)


@pytest.mark.parametrize('operation', ['summarize', 'transform_row'])
def test_old_1_1_geometry_with_control_frame_length_is_rejected(operation):
    legacy = {'schema': 'qto-cad-geometry/1.1', 'coordinate_system': 'WCS', 'closed': False,
              'parts': [{'kind': 'line', 'start': a, 'end': b} for a, b in [
                  ([0, 0, 0], [20000, 0, 0]), ([20000, 0, 0], [0, 0, 0]),
                  ([0, 0, 0], [20000, 0, 0])]]}
    original = copy.deepcopy(legacy)
    with pytest.raises(ValueError, match='重新提取'):
        if operation == 'summarize':
            geometry.summarize(legacy)
        else:
            geometry.transform_row({'record_id': 'old', 'geometry': legacy}, geometry.IDENTITY, 'old', 'Model')
    assert legacy == original  # Rejection never relabels the old cache as current.


@pytest.mark.parametrize('adapter', [pipe, component], ids=['pipe', 'component'])
def test_old_1_1_manifest_requires_real_reextraction(tmp_path, adapter):
    path = tmp_path / 'old_manifest.json'
    path.write_text(json.dumps({'schema_version': 'qto-cad-neutral-evidence/1.0',
                                'geometry_schema_version': 'qto-cad-geometry/1.1'}), encoding='utf-8')
    with pytest.raises(ValueError, match='旧缓存.*重新生成'):
        adapter.source_from_manifest(path)
    assert json.loads(path.read_text(encoding='utf-8'))['geometry_schema_version'] == 'qto-cad-geometry/1.1'
