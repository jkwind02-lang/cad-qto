"""T13/T08 synthetic counterexamples and legal controls. No real drawings or quantities.

Expected bounds come from the deliberately drawn vertices and ezdxf's independent Matrix44, never the new helpers.
These checks establish extraction/ledger behavior, not engineering-quantity or real-DWG acceptance.
"""
import hashlib
import copy
import csv
import json
import math
from pathlib import Path
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(ROOT / 'skills/qto-components-points/scripts'))
sys.path.insert(0, str(ROOT / 'skills/qto-pipe-networks/scripts'))
import qto_cad_geometry as geo
import extract_cad_neutral as extract
import build_point_coverage_ledger as coverage
import build_pipe_inference_inputs_batch as pipe_batch


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_box(points):
    points = [tuple(p) for p in points]
    return [min(p[i] for p in points) for i in range(3)] + [max(p[i] for p in points) for i in range(3)]


def neutral(tmp, doc):
    doc.units = 4
    source = tmp / 'synthetic.dxf'; doc.saveas(source)
    out = tmp / 'neutral'
    code = extract.main(['--source-dxf', str(source), '--output-dir', str(out)])
    assert code in (0, 3)
    manifest = json.loads((out / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    rows = [json.loads(s) for s in (out / '中性布局图元.ndjson').read_text(encoding='utf-8').splitlines()]
    return out, manifest, {r['entity_handle']: r for r in rows}


def triangle(space, **attrs):
    hatch = space.add_hatch(dxfattribs=attrs)
    hatch.paths.add_polyline_path([(0, 0), (8, 0), (0, 4)], is_closed=True)
    return hatch


def input_data(out, **extra):
    return {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'neutral_dir': str(out)}], **extra}


def sheet(kind='平面', **extra):
    return {'rows': [{'drawing': 'SYN', 'sheet_id': 'P', 'sheet_kind': kind,
                      'x1': -500, 'y1': -500, 'x2': 500, 'y2': 500, **extra}]}


def ledger(tmp, data):
    obj = coverage.Ledger(materialize(tmp, data), tmp)
    result = obj.run()
    return obj, result


def cli_report(tmp, data):
    path = save_json(tmp / 'input.json', materialize(tmp, data))
    assert coverage.main(['--input', str(path), '--output-dir', str(tmp / 'ledger')]) == 0
    return json.loads((tmp / 'ledger/点位覆盖台账.json').read_text(encoding='utf-8'))


def materialize(tmp, data):
    data = copy.deepcopy(data)
    for table in ('count_tables', 'handle_exclusions', 'sheets'):
        for i, spec in enumerate(data.get(table) or []):
            if 'rows' not in spec:
                continue
            rows = spec['rows']; path = tmp / f'{table}_{i}.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            data[table][i] = {'path': str(path)}
    return data


def adapter_report(tmp, out, rows, *, source=True):
    """Minimal adapter artifact contract, with real neutral artifact hashes; not a fabricated quantity approval."""
    disp = tmp / 'dispositions.jsonl'
    disp.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    profile = save_json(tmp / 'profile.json', {'unit': {'factor_to_output': 0.001}})
    counted = save_json(tmp / 'count.json', {'reconciled_instances': []})
    report = {'profile': {'path': str(profile), 'sha256': sha(profile)}, 'artifacts': {
        'neutral_instance_dispositions': str(disp), 'neutral_instance_dispositions_sha256': sha(disp),
        'count_input': str(counted)}}
    if source:
        report['neutral_source'] = {'manifest': str(out / 'CAD中性取证清单.json')}
    return save_json(tmp / 'adapter.json', report)


def disposition(insert, status='excluded'):
    return {'physical_id': f'PT-CAD-{insert.dxf.handle}', 'disposition': status,
            'block_name': insert.dxf.name, 'layer': insert.dxf.layer, 'point': [insert.dxf.insert.x*.001, insert.dxf.insert.y*.001],
            'basis': 'synthetic original exclusion', 'rule_ids': ['EX-SYN']}


def test_hatch_triangle_is_located_not_measured_after_real_dxf_reextraction(tmp_path):
    doc = ezdxf.new(); h = triangle(doc.modelspace())
    out, manifest, rows = neutral(tmp_path, doc)
    row = rows[h.dxf.handle]
    assert manifest['location_schema_version'] == geo.LOCATION_SCHEMA
    assert row['bbox3d'] == [0, 0, 0, 8, 4, 0]
    assert [row[k] for k in ('x', 'y', 'z')] == [4, 2, 0]
    assert row['location_basis'] == 'hatch_boundary_control_hull'
    assert row['location_status'] == 'bounded' and row['geometry_status'] == 'requires_adapter'
    assert row['geometry'] is row['raw_length'] is None and row['measurement_geometry_available'] is False
    obj, result = ledger(tmp_path, input_data(out, count_tables=[{'rows': [{'drawing': 'SYN', 'handle': h.dxf.handle,
        'category': 'ground terminal (explicit project evidence)'}]}], sheets=[sheet()]))
    assert result['by_destination']['已计'] == 1 and obj.candidates[0]['sheet'] == 'P'


@pytest.mark.parametrize('extrusion', [(0, 0, -1), (0, 1, 0), (1, 2, 3)])
def test_hatch_ocs_uses_wcs_not_source_xy(extrusion):
    doc = ezdxf.new(); h = triangle(doc.modelspace(), extrusion=extrusion, elevation=(0, 0, 7))
    facts = geo.entity_geometry(h)
    expected = expected_box([h.ocs().to_wcs((x, y, 7)) for x, y in [(0, 0), (8, 0), (0, 4)]])
    assert facts['location_bbox3d'] == pytest.approx(expected)


@pytest.mark.parametrize('kind', ['bulge', 'arc', 'ellipse'])
def test_hatch_conic_outer_hull_does_not_use_only_endpoints(kind):
    doc = ezdxf.new(); h = doc.modelspace().add_hatch()
    if kind == 'bulge':
        h.paths.add_polyline_path([(-2, 0, 1), (2, 0, 0), (0, -1, 0)], is_closed=True)
    else:
        p = h.paths.add_edge_path()
        if kind == 'arc': p.add_arc((0, 0), 2, 0, 360)
        else: p.add_ellipse((0, 0), (4, 0), .5, 0, 360)
    facts = geo.entity_geometry(h)
    assert facts['location_status'] == 'bounded'
    assert facts['bbox'][1] <= -2 and facts['bbox'][3] >= 2
    assert facts['geometry'] is facts['length'] is None


def test_unknown_hatch_path_stays_local_and_does_not_suppress_other_records(tmp_path):
    doc = ezdxf.new(); h = doc.modelspace().add_hatch()
    h.paths.add_edge_path().add_line((0, 0), (9, 0))  # invalid open boundary
    valid = triangle(doc.modelspace())
    line = doc.modelspace().add_line((100, 0), (103, 4))
    _, manifest, rows = neutral(tmp_path, doc)
    assert rows[h.dxf.handle]['location_status'] == 'unknown' and rows[h.dxf.handle]['bbox'] is None
    assert rows[valid.dxf.handle]['location_status'] == 'bounded'
    assert rows[line.dxf.handle]['raw_length'] == 5
    assert manifest['neutral_extraction_status'] == 'PASS'


@pytest.mark.parametrize('weights,bounded', [([], True), ([1, 2, 1, 1], True), ([1, -1, 1, 1], False), ([1, 0, 1, 1], False)])
def test_spline_control_hull_requires_valid_positive_weights(weights, bounded):
    doc = ezdxf.new(); s = doc.modelspace().add_open_spline([(0, 0), (2, 8), (8, -2), (10, 0)], degree=3)
    if weights: s.weights = weights
    facts = geo.entity_geometry(s)
    assert (facts['location_status'] == 'bounded') is bounded
    assert facts['geometry'] is facts['length'] is None
    assert 'spline_not_measured' in facts['geometry_issue_codes']
    if bounded: assert facts['bbox'] == [0, -2, 10, 8]


def test_fit_points_alone_and_legacy_json_do_not_gain_a_guessed_envelope():
    doc = ezdxf.new(); s = doc.modelspace().add_spline([(0, 0), (5, 9), (10, 0)])
    assert geo.entity_geometry(s)['location_status'] == 'unknown'
    legacy = {'entity_type': 'HATCH', 'geometry_status': 'requires_adapter', 'bbox': None, 'bbox3d': None,
              'raw_geometry': {'boundary_path_count': 1}, 'record_id': 'CAD-1', 'x': None, 'y': None}
    moved = geo.transform_row(legacy, geo.matrix16((1, 0, 0, 1, 3, 4, 1, 0)), 'A', 'Model')
    assert moved['bbox'] is None and 'location_schema_version' not in moved


@pytest.mark.parametrize('vertices,frames,owners,basis', [
    ([(10, 10), (20, 20), (30, 20), (40, 10)], [(0, 0, 100, 100)], ['F0'], 'spline_control_hull'),
    ([(200, 10), (220, 20), (230, 20), (240, 10)], [(0, 0, 100, 100)], [], 'spline_control_hull_outside_selected_frames'),
    ([(80, 10), (90, 20), (110, 20), (120, 10)], [(0, 0, 100, 100), (100, 0, 200, 100)], None, 'ambiguous_spline_control_hull'),
    ([(80, 10), (90, 20), (95, 20), (100, 10)], [(0, 0, 100, 100)], None, 'ambiguous_spline_control_hull'),
    ([(10, 10), (20, 20), (30, 20), (40, 10)], [(0, 0, 100, 100), (0, 0, 50, 50)], None, 'ambiguous_spline_control_hull'),
])
def test_new_spline_hull_batch_ownership_is_unique_or_drawing_level(vertices, frames, owners, basis, tmp_path):
    doc = ezdxf.new(); s = doc.modelspace().add_open_spline(vertices, degree=3)
    row = extract.convert(s, layout='Model', parent_block=None, source_hash='x', raw_unit='Unitless', sequence=1)
    selected = [{'frame_id': f'F{i}', 'bounds': list(box)} for i, box in enumerate(frames)]
    assert pipe_batch.located_frame_ids(row, selected, {}) == (owners, basis)
    layout = tmp_path / 'layout.jsonl'; blocks = tmp_path / 'blocks.jsonl'
    layout.write_text(json.dumps(row) + '\n', encoding='utf-8'); blocks.write_text('', encoding='utf-8')
    result = pipe_batch.scan_frames(layout, blocks, {}, {'layout': 'Model', 'families': [],
        'block_expansion': {'max_depth': 5}}, selected, [], tmp_path)
    assert len(result['drawing_level_pending']) == (1 if owners is None else 0)
    if owners is None or not owners:
        assert all(state['scoped_rows'] == 0 and not state['pending'] for state in result['frames'].values())
    assert row['raw_length'] is None and row['geometry_issue_codes'] == ['spline_not_measured']
    legacy = dict(row); legacy.pop('location_schema_version')
    assert pipe_batch.located_frame_ids(legacy, selected, {})[1] == 'own_geometry'


@pytest.mark.parametrize('scale,angle', [((2, 3, 1), 90), ((-2, 3, 1), 37), ((-1, -2, 4), 270)])
def test_insert_and_nested_hatch_preserve_world_transforms_and_identity(scale, angle):
    doc = ezdxf.new(); b = doc.blocks.new('LEAF', base_point=(2, 3, 0)); hatch = triangle(b)
    middle = doc.blocks.new('MIDDLE')
    inner = middle.add_blockref('LEAF', (4, 6), dxfattribs={'rotation': 23, 'xscale': -1.5, 'yscale': .5})
    outer = doc.modelspace().add_blockref('MIDDLE', (100, -200), dxfattribs={
        'rotation': angle, 'xscale': scale[0], 'yscale': scale[1], 'zscale': scale[2]})
    facts = geo.entity_geometry(outer)
    # The implementation transforms nested axis-aligned hulls, which may grow, but must contain all actual vertices.
    vertices = [outer.matrix44().transform(inner.matrix44().transform(p)) for p in [(0, 0, 0), (8, 0, 0), (0, 4, 0)]]
    bounds = facts['location_bbox3d']
    assert bounds is not None and facts['location_basis'] == 'insert_definition_control_envelope'
    for p in vertices:
        assert all(bounds[i]-1e-9 <= p[i] <= bounds[i+3]+1e-9 for i in range(3))
    row = extract.convert(hatch, layout='BlockDefinition', parent_block='LEAF', source_hash='x', raw_unit='mm', sequence=1)
    for insert, path in [(inner, 'inner'), (outer, 'outer/inner')]:
        matrix = geo.matrix_for_insert(extract.convert(insert, layout='Model', parent_block=None,
            source_hash='x', raw_unit='mm', sequence=1), [0, 0, 0])
        row = geo.transform_row(row, matrix, path, 'Model')
    assert row['bbox3d'] == row['location_bbox3d']
    assert row['instance_path'] == 'outer/inner' and row['entity_handle'] == hatch.dxf.handle
    assert row['source_definition_record_id'] == f'CAD-{hatch.dxf.handle}'
    assert row['raw_length'] is None and row['measurement_geometry_available'] is False


def test_insert_unknown_child_missing_definition_and_array_are_not_complete_envelopes():
    doc = ezdxf.new(); b = doc.blocks.new('UNKNOWN'); b.add_line((0, 0), (10, 0)); b.add_region()
    ins = doc.modelspace().add_blockref('UNKNOWN', (0, 0))
    assert geo.entity_geometry(ins)['location_status'] == 'unknown'
    missing = doc.modelspace().add_blockref('NOT_DEFINED', (2, 2))
    assert geo.entity_geometry(missing)['location_status'] == 'unknown'
    ins.dxf.row_count = 2
    assert geo.entity_geometry(ins)['location_status'] == 'unknown'


def test_reports_frame_envelope_layer_rule_and_disposed_no_table_warning(tmp_path):
    doc = ezdxf.new(); b = doc.blocks.new('FRAME'); b.add_lwpolyline([(-10, 0), (0, 0), (0, 8), (-10, 8)], close=True)
    ins = doc.modelspace().add_blockref('FRAME', (1000, 1000), dxfattribs={'rotation': 270, 'xscale': 10, 'yscale': 20, 'layer': 'SHEET'})
    out, _, _ = neutral(tmp_path, doc)
    report = adapter_report(tmp_path, out, [disposition(ins)])
    data = {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}],
            'frame_blocks': [{'layer_pattern': '^SHEET$'}],
            'sheets': [sheet(x1=1001, y1=1001, x2=1159, y2=1099)]}
    obj, result = ledger(tmp_path, data)
    assert result['frame_todo'] == [] and result['frame_coverage']['frame_count'] == 1
    assert obj.candidates[0]['placement_basis'] == coverage.GEOMETRY_BASIS
    assert obj.candidates[0]['box'] == pytest.approx([1000, 1000, 1160, 1100])
    data.pop('sheets')
    data['frame_dispositions'] = [{'drawing': 'SYN', 'handle': ins.dxf.handle, 'disposition': '其他', 'basis': 'reviewed cover'}]
    obj, result = ledger(tmp_path, data)
    assert result['frame_todo'] == [] and not obj.warnings['frames_without_sheet_table']


def test_missing_report_neutral_source_cannot_claim_full_coverage(tmp_path):
    doc = ezdxf.new(); b = doc.blocks.new('BOX'); b.add_line((0, 0), (1, 1))
    ins = doc.modelspace().add_blockref('BOX', (0, 0))
    out, _, _ = neutral(tmp_path, doc)
    report = adapter_report(tmp_path, out, [disposition(ins)], source=False)
    result = cli_report(tmp_path, {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}]})
    assert result['status'] == 'INCOMPLETE' and result['source_coverage_gaps']


def test_container_legend_first_level_rows_names_and_zero_instances_are_visible(tmp_path):
    doc = ezdxf.new()
    for name in ('SYMBOL_A', 'SYMBOL_B'):
        b = doc.blocks.new(name); b.add_line((0, 0), (3, 3))
    container = doc.blocks.new('LEGEND_TABLE')
    a = container.add_blockref('SYMBOL_A', (0, 0)); a.add_attrib('A', 'symbol one', (4, 0))
    b = container.add_blockref('SYMBOL_B', (0, 10)); container.add_text('symbol two', dxfattribs={'insert': (4, 10), 'height': 1})
    top = doc.modelspace().add_blockref('LEGEND_TABLE', (0, 0))
    out, _, _ = neutral(tmp_path, doc)
    obj, result = ledger(tmp_path, input_data(out, sheets=[sheet('图例')]))
    rows = result['legend_rows']
    assert len(rows) == 2 and {r['legend_text'] for r in rows} == {'symbol one', 'symbol two'}
    assert all(r['legend_handle'].startswith(f'PT-CAD-{top.dxf.handle}/LEGEND_TABLE/CAD-') for r in rows)
    assert {r['symbol'] for r in rows} == {'SYMBOL_A', 'SYMBOL_B'}
    assert len(result['legend_rows_without_destination']) == 2
    report = adapter_report(tmp_path, out, [disposition(top)])
    data = input_data(out, sheets=[sheet('图例')]); data['drawings'] = [{'drawing': 'SYN', 'reports': [str(report)]}]
    assert len(ledger(tmp_path, data)[1]['legend_rows']) == 2


def test_empty_legend_sheet_stays_incomplete_even_when_all_candidates_excluded(tmp_path):
    doc = ezdxf.new(); doc.modelspace().add_text('unparsed legend')
    out, _, _ = neutral(tmp_path, doc)
    result = cli_report(tmp_path, input_data(out, sheets=[sheet('图例')]))
    assert result['candidate_total'] == 0 and result['legend_inventory_gaps']
    assert result['status'] == 'INCOMPLETE' and result['warnings']['legend_sheets_without_rows']


def test_container_center_outside_legend_does_not_hide_inside_rows(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('SYMBOL').add_line((0, 0), (1, 1))
    table = doc.blocks.new('TABLE')
    table.add_blockref('SYMBOL', (5, 5)); table.add_blockref('SYMBOL', (5, 15))
    table.add_line((1000, 1000), (1001, 1001))  # shifts the parent's extent centre far outside the legend sheet
    doc.modelspace().add_blockref('TABLE', (0, 0))
    doc.modelspace().add_blockref('SYMBOL', (10, 20))  # nonzero existing row must not hide container omissions
    out, _, _ = neutral(tmp_path, doc)
    _, result = ledger(tmp_path, input_data(out, sheets=[sheet('图例', x1=0, y1=0, x2=100, y2=100)]))
    assert len(result['legend_rows']) == 3 and len(result['legend_rows_without_destination']) == 3


def test_raw_frame_missing_from_scoped_report_is_still_checked(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('FRAME').add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    ins = doc.modelspace().add_blockref('FRAME', (1000, 1000))
    out, _, _ = neutral(tmp_path, doc)
    report = adapter_report(tmp_path, out, [])  # adapter work package did not include this page at all
    data = {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}],
            'frame_blocks': [{'block_pattern': '^FRAME$'}], 'sheets': [sheet()]}
    result = cli_report(tmp_path, data)
    assert result['frame_todo'][0]['handle'] == ins.dxf.handle
    assert result['status'] == 'INCOMPLETE' and result['candidate_total'] == 0


def test_only_some_no_table_frames_disposed_keeps_remaining_warning(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('FRAME').add_line((0, 0), (10, 10))
    a = doc.modelspace().add_blockref('FRAME', (0, 0)); doc.modelspace().add_blockref('FRAME', (100, 0))
    out, _, _ = neutral(tmp_path, doc)
    data = input_data(out, frame_blocks=[{'layer_pattern': '^0$'}], frame_dispositions=[{
        'drawing': 'SYN', 'handle': a.dxf.handle, 'disposition': '其他', 'basis': 'reviewed cover'}])
    obj, result = ledger(tmp_path, data)
    assert len(result['frame_todo']) == 1 and obj.warnings['frames_without_sheet_table'][0]['frame_count'] == 1


def test_legend_array_child_marks_an_omitted_row_gap(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('SYMBOL').add_line((0, 0), (1, 1))
    table = doc.blocks.new('TABLE')
    table.add_blockref('SYMBOL', (0, 0), dxfattribs={'row_count': 2, 'row_spacing': 10})
    table.add_blockref('SYMBOL', (0, 20)); doc.modelspace().add_blockref('TABLE', (0, 0))
    out, _, _ = neutral(tmp_path, doc)
    result = cli_report(tmp_path, input_data(out, sheets=[sheet('图例')]))
    assert result['legend_inventory_gaps'] and result['status'] == 'INCOMPLETE'


def test_deeper_legend_wrapper_retains_an_explicit_inventory_gap(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('SYMBOL').add_line((0, 0), (1, 1))
    table = doc.blocks.new('TABLE'); table.add_blockref('SYMBOL', (0, 0)); table.add_blockref('SYMBOL', (0, 20))
    doc.blocks.new('WRAPPER').add_blockref('TABLE', (0, 0)); doc.modelspace().add_blockref('WRAPPER', (0, 0))
    out, _, _ = neutral(tmp_path, doc)
    result = cli_report(tmp_path, input_data(out, sheets=[sheet('图例')]))
    assert any('更深容器' in gap['reason'] for gap in result['legend_inventory_gaps'])
    assert result['status'] == 'INCOMPLETE'


def test_project_recollection_and_exclusion_preserve_original_adapter_evidence(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('BOX').add_line((0, 0), (1, 1))
    a = doc.modelspace().add_blockref('BOX', (0, 0)); b = doc.modelspace().add_blockref('BOX', (10, 0))
    out, _, _ = neutral(tmp_path, doc)
    report = adapter_report(tmp_path, out, [disposition(a), disposition(b, 'unresolved')])
    data = {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}],
        'count_overrides': [{'drawing': 'SYN', 'physical_id': f'PT-CAD-{a.dxf.handle}', 'category': 'confirmed device',
                             'basis': 'project supplemental instance ledger and reviewed symbol'}],
        'handle_exclusions': [{'rows': [{'drawing': 'SYN', 'handle': b.dxf.handle, 'basis': 'individually reviewed empty symbol'}]}]}
    obj, result = ledger(tmp_path, data)
    assert result['by_destination']['已计'] == 1 and result['by_destination']['有据排除'] == 1
    assert result['adapter_excluded_project_counted'] == 1
    original = result['project_corrections'][0]['original_adapter']
    assert original['rule_ids'] == ['EX-SYN'] and original['basis'] == 'synthetic original exclusion'
    assert result['project_corrections'][0]['quantity_validation_performed'] is False
    assert obj.candidates[0]['category'] == 'confirmed device'
    data['count_overrides'][0]['physical_id'] = 'PT-CAD-FFFFFF'
    with pytest.raises(coverage.InputProblems): ledger(tmp_path, data)


def test_report_hatch_disposition_uses_nonblock_kind_and_retains_location(tmp_path):
    doc = ezdxf.new(); h = triangle(doc.modelspace()); out, _, _ = neutral(tmp_path, doc)
    row = {'physical_id': f'PT-CAD-{h.dxf.handle}', 'entity_type': 'HATCH', 'block_name': '', 'layer': 'SYN',
           'disposition': 'unresolved', 'basis': 'pending explicit recognition', 'point': [.004, .002],
           'scope_geometry_bbox': [0, 0, 8, 4], 'measurement_geometry_available': False}
    report = adapter_report(tmp_path, out, [row])
    obj, result = ledger(tmp_path, {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}],
        'sheets': [sheet()]})
    assert obj.candidates[0]['kind'] == '非块' and obj.candidates[0]['symbol'] == '<HATCH>'
    assert obj.candidates[0]['sheet'] == 'P' and result['by_destination']['待识别'] == 1


@pytest.mark.parametrize('claim,complete', [('@REC-H', True), ('', False)])
def test_hatch_physical_instance_and_nonblock_legend_do_not_duplicate_candidates(tmp_path, claim, complete):
    doc = ezdxf.new(); h = triangle(doc.modelspace()); out, _, _ = neutral(tmp_path, doc)
    pid = f'PT-CAD-{h.dxf.handle}'
    row = {'physical_id': pid, 'entity_type': 'HATCH', 'block_name': '', 'disposition': 'adopted', 'basis': 'explicit reviewed rule',
           'adjudication_id': 'REC-H', 'point': [.004, .002], 'scope_geometry_bbox': [0, 0, 8, 4]}
    report = adapter_report(tmp_path, out, [row])
    save_json(tmp_path / 'count.json', {'reconciled_instances': [{'physical_id': pid, 'category': 'terminal'}]})
    legend = {'drawing': 'SYN', 'legend_row': 'H1', 'legend_handle': '', 'legend_text': 'terminal', 'symbol': '<HATCH>',
        'symbol_kind': '非块', 'plan_count': '1', 'destination': '已计', 'target_or_reason': 'terminal', 'matched_symbols': claim}
    path = tmp_path / 'legend.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(legend)); writer.writeheader(); writer.writerow(legend)
    result = cli_report(tmp_path, {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}],
        'legend_coverage': str(path), 'sheets': [sheet(x1=-10, y1=-10, x2=20, y2=20),
                                               sheet('图例', sheet_id='LG', x1=100, y1=100, x2=200, y2=200)]})
    assert result['candidate_total'] == 1
    assert result['coverage_complete'] is complete


@pytest.mark.parametrize('case', ['duplicate', 'no_basis', 'double_destination', 'duplicate_excluded', 'version_conflict'])
def test_project_correction_counterexamples_are_rejected(tmp_path, case):
    doc = ezdxf.new(); doc.blocks.new('BOX').add_line((0, 0), (1, 1))
    a = doc.modelspace().add_blockref('BOX', (0, 0)); out, _, _ = neutral(tmp_path, doc)
    report = adapter_report(tmp_path, out, [disposition(a, 'duplicate_excluded' if case == 'duplicate_excluded' else 'excluded')])
    override = {'drawing': 'SYN', 'physical_id': f'PT-CAD-{a.dxf.handle}', 'category': 'device', 'basis': 'review evidence'}
    data = {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}], 'count_overrides': [override]}
    if case == 'duplicate': data['count_overrides'].append(dict(override))
    if case == 'no_basis': override.pop('basis')
    if case == 'double_destination': data['handle_exclusions'] = [{'rows': [{'drawing': 'SYN', 'handle': a.dxf.handle, 'basis': 'exclude'}]}]
    if case == 'version_conflict': data['version_conflicts'] = [{'conflict_id': 'VC', 'drawing': 'SYN',
        'object_ids': [f'PT-CAD-{a.dxf.handle}'], 'basis': 'two unresolved revisions'}]
    with pytest.raises(coverage.InputProblems): ledger(tmp_path, data)


def test_project_recollection_keeps_full_nested_instance_identity(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('DEVICE').add_line((0, 0), (1, 1))
    container = doc.blocks.new('CONTAINER'); child = container.add_blockref('DEVICE', (5, 5))
    top = doc.modelspace().add_blockref('CONTAINER', (10, 10))
    out, _, _ = neutral(tmp_path, doc)
    pid = f'PT-CAD-{top.dxf.handle}/CONTAINER/CAD-{child.dxf.handle}'
    row = {'physical_id': pid, 'disposition': 'excluded', 'block_name': 'DEVICE', 'parent_block': 'CONTAINER',
           'point': [.015, .015], 'basis': 'excluded with container', 'rule_ids': ['EX-CONTAINER']}
    report = adapter_report(tmp_path, out, [row])
    data = {'schema_version': coverage.SCHEMA, 'drawings': [{'drawing': 'SYN', 'reports': [str(report)]}],
        'count_overrides': [{'drawing': 'SYN', 'physical_id': pid, 'category': 'device', 'basis': 'reviewed physical child'}]}
    obj, result = ledger(tmp_path, data)
    assert result['project_corrections'][0]['physical_id'] == pid and obj.candidates[0]['id'] == pid
    assert result['adapter_excluded_project_counted'] == 1 and result['candidate_total'] == 1
