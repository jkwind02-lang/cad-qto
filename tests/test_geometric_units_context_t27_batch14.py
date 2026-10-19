"""Source paper declarations and compensated DIM contexts, never an SI shortcut."""
import copy
import json
import pytest

from test_geometric_units_t27_20261019 import (
    case, check, credential, save_source, units, sha, put, command, SHARED, PIPE,
)
from test_geometric_units_block_t27_batch11 import wrap
from test_geometric_units_clone_context_t27_batch11 import clone_picture
from test_geometric_unit_professional_consumption_batch13 import (
    physical, trace_request, trace, SCRIPTS,
)


def paper(case, width=1200., height=900., text=None):
    doc = case['doc']
    doc.entitydb[case['handles']['frame']].set_points(
        [(0, 0), (width*100, 0), (width*100, height*100), (0, height*100)])
    label = doc.modelspace().add_text(text or f'图纸幅面：{width:g}×{height:g}mm',
                                    dxfattribs={'insert': (1000, 4000), 'height': 100})
    case['request']['paper_size_text_refs'] = [dict(handle=label.dxf.handle)]
    case['scope']['bounds'].update(xmax_exclusive=width*100, ymax_exclusive=height*100)
    return label


def compensated(case, scale=2., factor=None):
    dim = case['doc'].entitydb[case['handles']['dim']]
    factor = scale if factor is None else factor
    style = dim.override(); style.update({'dimlfac': factor}); style.commit()
    case['doc'].blocks[dim.dxf.geometry].query('MTEXT')[0].text = f'{6000*factor:g}'
    parent, _ = wrap(case, ['dim'], base=(13000, 10000, 0), insert=(13000, 10000, 0),
                     sx=scale, sy=scale)
    return dim, parent


@pytest.mark.parametrize('width,height', [(1200., 900.), (1682., 594.), (900., 900.)])
def test_non_a_extended_and_square_source_paper_both_axes_are_replayed(case, width, height):
    label = paper(case, width, height); save_source(case)
    before = sha(case['path']); result = credential(case)
    assert result['frame']['standard'] == 'source_explicit_paper_size'
    assert result['frame']['paper_size_mm'] == [width, height]
    assert result['frame']['paper_size_source_evidence'][0]['source_ref']['handle'] == label.dxf.handle
    assert result['frame']['checked_axes'] == 2
    assert result['frame']['implied_metres_per_unit_by_axis'] == pytest.approx([.001, .001])
    assert result['physical_unit_basis'] is None and result['allows_length_measurement'] is False
    assert result['evidence_level'] == 'inferred' and result['quantity_level'] == '规则推算量'
    assert units.verify_geometric_unit_credential(result, case['scope'])['status'] == 'INFERRED'
    with pytest.raises(units.GeometricUnitUnresolved):
        units.verify_geometric_unit_consumption(result, case['manifest'], case['scope'], .001)
    assert sha(case['path']) == before


@pytest.mark.parametrize('mutation', ['foreign_frame', 'component', 'self_values', 'one_axis',
                                    'hidden', 'annotative', 'render_override', 'conflicting_label'])
def test_paper_size_requires_real_current_frame_semantics_and_context(case, mutation):
    label = paper(case)
    if mutation == 'foreign_frame':
        # Same source bytes and a second real frame are not the selected frame.
        case['doc'].modelspace().add_lwpolyline(
            [(200000, 0), (320000, 0), (320000, 90000), (200000, 90000)], close=True)
        label.dxf.insert = (201000, 4000)
    elif mutation == 'component': label.dxf.text = '桥架500×150mm'
    elif mutation == 'self_values':
        case['request']['paper_size_text_refs'] = [dict(paper_size_mm=[1200, 900])]
    elif mutation == 'one_axis':
        case['doc'].entitydb[case['handles']['frame']].set_points(
            [(0, 0), (120000, 0), (120000, 92000), (0, 92000)])
    elif mutation == 'hidden': label.dxf.invisible = 1
    elif mutation == 'annotative':
        case['doc'].appids.new('AcadAnnotative')
        label.set_xdata('AcadAnnotative', [(1000, 'AnnotativeData')])
    elif mutation == 'render_override':
        case['request']['paper_size_text_refs'] = case['request']['dimension_refs']
    elif mutation == 'conflicting_label':
        other = case['doc'].modelspace().add_text('图纸幅面：900×1300mm', dxfattribs={'insert': (1000, 5000)})
        case['request']['paper_size_text_refs'].append(dict(handle=other.dxf.handle))
    save_source(case)
    with pytest.raises(ValueError): check(case)


def test_unselected_paper_claim_and_other_unit_claim_are_not_silently_excluded(case):
    paper(case); case['request'].pop('paper_size_text_refs'); save_source(case)
    with pytest.raises(ValueError): check(case)
    case['request']['paper_size_text_refs'] = [dict(handle=next(
        e.dxf.handle for e in case['doc'].modelspace().query('TEXT') if '图纸幅面' in e.dxf.text))]
    case['doc'].modelspace().add_text('坐标单位：米', dxfattribs={'insert': (1000, 6000)})
    save_source(case)
    with pytest.raises(units.GeometricUnitUnresolved) as caught: check(case)
    assert caught.value.partial_evidence['raw_unit'] is None
    assert caught.value.partial_evidence['unit_text_search']['unit_text_matches'][0]['text'] == '坐标单位：米'


def test_paper_source_reuses_true_mirrored_insert_scope_and_rejects_other_scope(case):
    label = paper(case)
    parent, block = wrap(case, ['frame', 'scale', 'title', 'dim'], rotation=180., sx=-1., sy=1.,
                         insert=(150000, 200000, 0))
    case['doc'].modelspace().unlink_entity(label); block.add_entity(label)
    case['request']['paper_size_text_refs'][0]['insert_handles'] = [parent.dxf.handle]
    center = parent.matrix44().transform((60000, 45000, 0))
    case['scope']['bounds'] = dict(xmin=center.x-1000, ymin=center.y-1000,
                                 xmax_exclusive=center.x+1000, ymax_exclusive=center.y+1000)
    save_source(case); result = credential(case)
    assert result['frame']['paper_size_mm'] == [1200., 900.]
    other = copy.deepcopy(case['scope']); other['bounds']['xmin'] += 1
    with pytest.raises(ValueError, match='scope'):
        units.verify_geometric_unit_credential(result, other)


@pytest.mark.parametrize('scale', [.5, 2., 3.])
def test_positive_dimlfac_compensates_real_insert_geometry_once(case, scale):
    compensated(case, scale); save_source(case); result = credential(case)['dimensions'][0]
    assert result['source_measurement'] == pytest.approx(6000.)
    assert result['source_expected_display_number'] == result['source_display_number'] == 6000.*scale
    assert result['dimlfac'] == result['insert_world_scale'] == scale
    assert result['measurement'] == pytest.approx(6000.*scale)
    assert result['physical_unit_proven_independently'] is False
    assert result['dimscale_role'].startswith('annotation_display_only')


@pytest.mark.parametrize('mutation', ['display_unscaled', 'display_twice', 'divided_again',
                                    'wrong_lfac', 'negative_lfac', 'override', 'annotative',
                                    'net_scale_cancelled', 'cache_conflict'])
def test_dimlfac_display_local_and_world_geometry_cannot_be_multiplied_twice(case, mutation):
    dim, parent = compensated(case, 2.)
    picture = case['doc'].blocks[dim.dxf.geometry].query('MTEXT')[0]
    if mutation == 'display_unscaled': picture.text = '6000'
    elif mutation == 'display_twice': picture.text = '24000'
    elif mutation == 'divided_again': picture.text = '3000'
    elif mutation == 'wrong_lfac':
        style = dim.override(); style.update({'dimlfac': 3.}); style.commit(); picture.text = '18000'
    elif mutation == 'negative_lfac':
        style = dim.override(); style.update({'dimlfac': -2.}); style.commit()
    elif mutation == 'override': dim.dxf.text = '12000'
    elif mutation == 'annotative':
        case['doc'].appids.new('AcadAnnotative'); dim.set_xdata('AcadAnnotative', [(1000, 'AnnotativeData')])
    elif mutation == 'net_scale_cancelled':
        outer = case['doc'].blocks.new('OUTER')
        case['doc'].modelspace().unlink_entity(parent); outer.add_entity(parent)
        root = case['doc'].modelspace().add_blockref('OUTER', (0, 0), dxfattribs={'xscale': .5, 'yscale': .5})
        case['request']['dimension_refs'][0]['insert_handles'] = [root.dxf.handle, parent.dxf.handle]
    elif mutation == 'cache_conflict': dim.dxf.actual_measurement = 6000.
    save_source(case)
    with pytest.raises(ValueError): check(case)


@pytest.mark.parametrize('offset', [(650000., 155000., 37.), (0., 0., -100.), (10., -20., 15.)])
def test_finite_default_ocs_z_picture_translation_restores_the_source_plane(case, offset):
    dim = case['doc'].entitydb[case['handles']['dim']]
    clone_picture(case['doc'], dim, offset); save_source(case)
    before = sha(case['path']); result = credential(case)['dimensions'][0]
    expected = next(e for e in dim.virtual_entities() if e.dxftype() == 'MTEXT')
    assert result['rendered_text_point_wcs'] == pytest.approx(tuple(expected.dxf.insert))
    assert result['rendered_text_point_wcs'][2] == pytest.approx(0.)
    assert result['dimension_render_offset_ocs'] == offset
    assert result['source_measurement'] == result['measurement'] == 6000.
    assert sha(case['path']) == before


@pytest.mark.parametrize('target', ['unrestored_picture', 'definition_plane', 'scale_plane',
                                  'paper_plane', 'nonxy_ocs'])
def test_same_xy_projection_on_another_plane_is_not_current_frame_evidence(case, target):
    dim = case['doc'].entitydb[case['handles']['dim']]
    if target == 'unrestored_picture': dim.dxf.insert = (0, 0, 5)
    elif target == 'definition_plane':
        dim.dxf.defpoint2 = (10000, 10000, 5); dim.dxf.defpoint3 = (16000, 10000, 5)
    elif target == 'scale_plane': case['doc'].entitydb[case['handles']['scale']].dxf.insert = (80000, 2000, 5)
    elif target == 'paper_plane': paper(case).dxf.insert = (1000, 4000, 5)
    else: dim.dxf.extrusion = (0, 1, 0)
    save_source(case)
    with pytest.raises(ValueError): check(case)


def test_new_context_actual_public_cli_pipe_producer_consumer_and_drift(case):
    from test_geometric_unit_dependency_t27_20261019 import input_for
    paper(case); dim, _ = compensated(case, 2.)
    clone_picture(case['doc'], dim, (650000, 155000, 15)); save_source(case)
    neutral = case['tmp']/'new-context-neutral'
    take = command(SHARED/'extract_cad_neutral.py', '--source-dxf', case['path'], '--all-blocks', '--output-dir', neutral)
    assert take.returncode in (0, 3), take.stdout+take.stderr
    case['manifest_path'] = neutral/'CAD中性取证清单.json'
    case['manifest'] = json.loads(case['manifest_path'].read_text(encoding='utf-8'))
    rp = put(case['tmp']/'unit-request.json', case['request']); sp = put(case['tmp']/'scope.json', case['scope'])
    ep = case['tmp']/'unit-evidence.json'
    produced = command(SHARED/'qto_geometric_unit_evidence.py', '--cad-manifest', case['manifest_path'],
                       '--request', rp, '--scope', sp, '--output', ep)
    assert produced.returncode == 0, produced.stdout+produced.stderr
    checked = json.loads(ep.read_text(encoding='utf-8'))
    assert checked['dimensions'][0]['measurement'] == 12000.
    assert checked['physical_unit_basis'] is None
    data = input_for(case); data['geometric_unit_evidence'] = checked
    ip = put(case['tmp']/'pipe-input.json', data); op = case['tmp']/'pipe-result.json'
    consumed = command(PIPE/'infer_pipe_network.py', '--input', ip, '--output', op)
    assert consumed.returncode == 0, consumed.stdout+consumed.stderr
    actual = json.loads(op.read_text(encoding='utf-8'))
    assert actual['geometric_unit_evidence']['dimensions'][0]['measurement'] == 12000.
    # Same source, original schema and modified source-selection context are replayed.
    wrong = copy.deepcopy(checked); wrong['request']['paper_size_text_refs'] = wrong['request']['dimension_refs']
    with pytest.raises(ValueError): units.verify_geometric_unit_credential(wrong, case['scope'])
    wrong = copy.deepcopy(checked); wrong['runtime_dependencies'][0]['sha256'] = '0'*64
    with pytest.raises(ValueError, match='依赖'): units.verify_geometric_unit_credential(wrong, case['scope'])
    source_bytes = case['path'].read_bytes()
    case['path'].write_bytes(source_bytes+b'\n')
    rejected = command(PIPE/'infer_pipe_network.py', '--input', ip, '--output', case['tmp']/'stale-result.json')
    assert rejected.returncode == 2 and not (case['tmp']/'stale-result.json').exists()
    case['path'].write_bytes(source_bytes)
    restored = command(PIPE/'infer_pipe_network.py', '--input', ip, '--output', case['tmp']/'restored-result.json')
    assert restored.returncode == 0, restored.stdout+restored.stderr
    put(case['tmp']/'actual-command-exits.json', dict(extract=take.returncode, public_unit=produced.returncode,
        pipe_consumption=consumed.returncode, stale_source=rejected.returncode, stale_stderr=rejected.stderr,
        restored_same_source=restored.returncode))


def test_new_context_actual_electrical_cli_still_requires_independent_coordinate_si(physical):
    c = physical; doc = c['doc']; req = c['unit_request']
    frame = doc.entitydb[req['frame']['entity_refs'][0]['handle']]
    frame.set_points([(-20000, -20000), (100000, -20000), (100000, 70000), (-20000, 70000)])
    label = doc.modelspace().add_text('图纸幅面：1200×900mm', dxfattribs={'insert': (-18000, -16000), 'height': 100})
    req['paper_size_text_refs'] = [dict(handle=label.dxf.handle)]
    c['scope']['bounds'].update(xmax_exclusive=100000., ymax_exclusive=70000.)
    dim = doc.entitydb[req['dimension_refs'][0]['handle']]
    style = dim.override(); style.update({'dimlfac': 2.}); style.commit()
    doc.blocks[dim.dxf.geometry].query('MTEXT')[0].text = '12000'
    block = doc.blocks.new('SOURCE_DIM'); doc.modelspace().unlink_entity(dim); block.add_entity(dim)
    parent = doc.modelspace().add_blockref('SOURCE_DIM', (0, 0), dxfattribs={'xscale': 2., 'yscale': 2.})
    req['dimension_refs'][0]['insert_handles'] = [parent.dxf.handle]
    clone_picture(doc, dim, (650000, 155000, 15)); doc.saveas(c['path'])
    neutral = c['tmp']/'new-context-electrical-neutral'
    take = command(SHARED/'extract_cad_neutral.py', '--source-dxf', c['path'], '--output-dir', neutral)
    assert take.returncode == 0, take.stdout+take.stderr
    c['manifest'] = neutral/'CAD中性取证清单.json'; c['mf'] = json.loads(c['manifest'].read_text(encoding='utf-8'))
    rp = put(c['tmp']/'public-request.json', req); sp = put(c['tmp']/'public-scope.json', c['scope']); ep = c['tmp']/'public-result.json'
    produced = command(SHARED/'qto_geometric_unit_evidence.py', '--cad-manifest', c['manifest'], '--request', rp,
                       '--scope', sp, '--output', ep)
    assert produced.returncode == 0, produced.stdout+produced.stderr
    c['credential'] = json.loads(ep.read_text(encoding='utf-8'))
    assert c['credential']['physical_unit_basis'] == 'source_coordinate_declaration'
    tr = trace_request(c); tp = put(c['tmp']/'trace-input.json', tr); out = c['tmp']/'electrical-output'
    done = command(SCRIPTS/'trace_drawn_routes.py', '--cad-manifest', c['manifest'], '--request', tp, '--output-dir', out)
    assert done.returncode == 0, done.stdout+done.stderr
    result = json.loads((out/'走线追踪结果.json').read_text(encoding='utf-8'))
    assert result['routes'][0]['drawn_length_m'] == 10.
    assert trace.verify_trace_geometric_consumption(result, c['manifest'])
    wrong = copy.deepcopy(tr); wrong['unit']['factor_to_output'] *= 2
    with pytest.raises(ValueError): trace.Drawing(c['manifest'], trace.parse_request(wrong))
    # Remove the actual SI clause, retake the same drawing and prove geometry
    # remains useful but cannot become a new professional length credential.
    source_bytes = c['path'].read_bytes()
    doc.modelspace().delete_entity(doc.entitydb[c['clause']]); doc.saveas(c['path'])
    req.pop('coordinate_unit_refs'); no = c['tmp']/'no-coordinate-neutral'
    take_no = command(SHARED/'extract_cad_neutral.py', '--source-dxf', c['path'], '--output-dir', no)
    assert take_no.returncode == 0, take_no.stdout+take_no.stderr
    mp = no/'CAD中性取证清单.json'; mf = json.loads(mp.read_text(encoding='utf-8'))
    cred = units.verify_geometric_unit_evidence(req, mf, c['scope'])
    cred['cad_manifest'] = dict(path=str(mp), sha256=sha(mp))
    assert cred['allows_length_measurement'] is False and cred['physical_unit_basis'] is None
    wrong = copy.deepcopy(tr); wrong['unit']['geometric_unit_evidence'] = cred
    with pytest.raises(units.GeometricUnitUnresolved): trace.Drawing(mp, trace.parse_request(wrong))
    c['path'].write_bytes(source_bytes)
    assert trace.verify_trace_geometric_consumption(result, c['manifest'])
    restored = command(SCRIPTS/'trace_drawn_routes.py', '--cad-manifest', c['manifest'], '--request', tp,
                       '--output-dir', c['tmp']/'restored-electrical-output')
    assert restored.returncode == 0, restored.stdout+restored.stderr
    put(c['tmp']/'actual-electrical-command-exits.json', dict(extract=take.returncode, public_unit=produced.returncode,
        electrical_trace=done.returncode, extract_no_SI=take_no.returncode, restored_same_source=restored.returncode))
