"""Anonymous source-faithful dimensions/elevations, not a project quantity oracle."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / 'skills/qto-pipe-networks/scripts'
sys.path.insert(0, str(PIPE))
from measure_pipe_diagram_lengths import measure, sha, REQUEST, BASIS, Drawing
from receive_pipe_diagram_lengths import receive, REQUEST as RECEIVE_REQUEST


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')


def setup(tmp_path):
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 0
    m = doc.modelspace()
    title = m.add_text('Synthetic system diagram', dxfattribs={'insert': (0, 9)})
    line = m.add_line((0, 0), (10, 0))
    dim = m.add_aligned_dim((0, 0), (10, 0), distance=1, text='2.5m'); dim.render()
    dimension = dim.dimension
    lower = m.add_text('0.000m', dxfattribs={'insert': (0, 2)})
    upper = m.add_text('5.500m', dxfattribs={'insert': (0, 3)})
    other = m.add_text('3.0m', dxfattribs={'insert': (3, 3)})
    unit = m.add_text('标高、管长单位m', dxfattribs={'insert': (0, 4)})
    bare = m.add_text('5.500', dxfattribs={'insert': (0, 5)})
    coordinate = m.add_text('绘图坐标单位mm', dxfattribs={'insert': (0, 6)})
    datum = m.add_text('统一建筑标高基准 zero-A', dxfattribs={'insert': (0, 7)})
    path = tmp_path / 'anonymous-diagram.dxf'
    doc.saveas(path)
    h = lambda entity: [entity.dxf.handle]
    row = {'record_id': 'D-1', 'physical_id': 'pipe-1', 'extent_id': 'extent-1',
        'system': 'supply', 'physical_instance_status': 'actual', 'specification': {'dn': 'DN25', 'material': 'test material'},
        'view_id': 'V', 'from_port_id': 'p0', 'to_port_id': 'p1',
        'source_span': {'path': h(line)}, 'basis': 'one physical extent and its literal dimension',
        'recognition_basis': {'kind': 'model_working_interpretation', 'recognized_by': 'synthetic source author', 'review_status': 'unreviewed'},
        'level': 'confirmed', 'length_facts': [{'method': 'dimension', 'annotation_path': h(dimension),
        'basis': 'source dimension applies to this entire extent', 'level': 'confirmed'}]}
    payload = {'schema_version': REQUEST, 'drawing': {'path': str(path), 'sha256': sha(path)},
        'scope_id': 'finite-scope', 'scope_basis': 'explicit selected physical extents',
        'views': [{'view_id': 'V', 'kind': 'isometric', 'title_paths': [h(title)],
                   'bounds': [-2, -2, 40, 15], 'basis': 'source system title and finite window', 'level': 'confirmed'}],
        'segments': [row]}
    return payload, {'title': h(title), 'line': h(line), 'dimension': h(dimension),
                     'lower': h(lower), 'upper': h(upper), 'other': h(other), 'unit': h(unit),
                     'bare': h(bare), 'coordinate': h(coordinate), 'datum': h(datum)}


def elevation(handles, route='vertical', datum='zero-A'):
    return {'method': 'elevation', 'level': 'confirmed', 'basis': 'source centerline ports and vertical route',
        'route_kind': route,
        'from': {'annotation_path': handles['lower'], 'datum_annotation_path': handles['datum'],
                 'datum_id': 'zero-A', 'measurement_kind': 'pipe_center'},
        'to': {'annotation_path': handles['upper'], 'datum_annotation_path': handles['datum'],
               'datum_id': datum, 'measurement_kind': 'pipe_center'}}


def test_real_dimension_not_raw_page_length_and_unitless_allowed(tmp_path):
    p, _ = setup(tmp_path)
    out = measure(p)
    r = out['segments'][0]
    assert r['quantity'] == 2.5 and r['source_span']['page_coordinate_length'] == 10
    assert r['source_span']['physical_length_from_coordinates'] is False
    assert out['network_quantity_produced'] is False and out['business_adoption_allowed'] is False
    assert r['quantity_level'] == '确认量'


def test_same_geometry_different_source_annotation_changes_length(tmp_path):
    p, h = setup(tmp_path)
    path = Path(p['drawing']['path'])
    before = measure(p)
    doc = ezdxf.readfile(path)
    doc.entitydb[h['dimension'][0]].dxf.text = '7.25m'
    doc.saveas(path)
    with pytest.raises(ValueError, match='SHA|哈希|改变'):
        measure(p)
    p['drawing']['sha256'] = sha(path)
    after = measure(p)
    assert before['segments'][0]['source_span'] == after['segments'][0]['source_span']
    assert after['segments'][0]['quantity'] == 7.25


def test_compressed_vertical_uses_source_elevations_and_existing_typed_engine(tmp_path):
    p, h = setup(tmp_path)
    p['segments'][0]['length_facts'] = [elevation(h)]
    result = measure(p)['segments'][0]
    assert result['quantity'] == 5.5
    assert result['quantity_level'] == '规则推算量'
    calc = result['measurements'][0]['elevation_calculation']
    assert calc['business_adoption_allowed'] is False and calc['calculation_graph']


@pytest.mark.parametrize('case', ['unknown', 'schematic', 'different_datum', 'wrong_measurement'])
def test_missing_schematic_and_datum_ambiguity_remain_null(tmp_path, case):
    p, h = setup(tmp_path)
    if case == 'unknown':
        p['segments'][0]['length_facts'] = []
    else:
        f = elevation(h, route='schematic' if case == 'schematic' else 'vertical',
                      datum='zero-B' if case == 'different_datum' else 'zero-A')
        if case == 'wrong_measurement':
            f['from']['measurement_kind'] = 'finished_floor'
        p['segments'][0]['length_facts'] = [f]
    out = measure(p)
    assert out['segments'][0]['quantity'] is None
    assert out['unprocessed_record_ids'] == ['D-1']


def test_unresolved_is_not_zero_and_source_conflict_is_not_overwritten(tmp_path):
    p, h = setup(tmp_path)
    p['segments'][0]['length_facts'].append({'method': 'dimension', 'annotation_path': h['other'],
        'level': 'confirmed', 'basis': 'second explicit competing dimension'})
    r = measure(p)['segments'][0]
    assert r['status'] == 'conflict' and r['quantity'] is None
    assert [x['value'] for x in r['measurements']] == [2.5, 3.]


def test_unit_note_is_for_annotation_values_not_coordinates(tmp_path):
    p, h = setup(tmp_path)
    f = p['segments'][0]['length_facts'][0]
    f['annotation_path'] = h['bare']
    assert measure(p)['segments'][0]['quantity'] is None
    f.update(unit_annotation_path=h['unit'], unit_applies_to='pipe_length_elevation')
    assert measure(p)['segments'][0]['measured_extent_length'] == 5.5
    assert measure(p)['segments'][0]['quantity'] is None
    f['unit_annotation_path'] = h['coordinate']
    with pytest.raises(ValueError, match='坐标单位'):
        measure(p)


@pytest.mark.parametrize('change', ['physical', 'ports', 'same_source', 'cross_source', 'multiplier'])
def test_duplicate_identity_and_cross_source_injection_rejected(tmp_path, change):
    p, _ = setup(tmp_path)
    if change in {'cross_source', 'multiplier'}:
        p['segments'][0]['drawing_id' if change == 'cross_source' else 'multiplier'] = 'other' if change == 'cross_source' else 5
    else:
        row = copy.deepcopy(p['segments'][0]); row['record_id'] = 'D-2'
        if change != 'physical':
            row.update(physical_id='pipe-2', extent_id='extent-2')
        if change == 'same_source':
            row.update(from_port_id='p2', to_port_id='p3')
        p['segments'].append(row)
    with pytest.raises(ValueError):
        measure(p)


def test_same_handle_two_insert_instances_are_distinct_without_scaling_dimensions(tmp_path):
    p, _ = setup(tmp_path)
    path = Path(p['drawing']['path'])
    d = ezdxf.readfile(path); block = d.blocks.new('TYPICAL')
    axis = block.add_line((0, 0), (1, 0))
    bd = block.add_aligned_dim((0, 0), (1, 0), distance=1, text='1.5m'); bd.render()
    annotation = bd.dimension
    instances = [d.modelspace().add_blockref('TYPICAL', (x, 0), dxfattribs={'xscale': 2}) for x in (15, 25)]
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    p['segments'] = []
    for i, inst in enumerate(instances):
        row = {'record_id': str(i), 'physical_id': 'pipe' + str(i), 'extent_id': 'extent' + str(i),
            'from_port_id': 'a' + str(i), 'to_port_id': 'b' + str(i), 'system': 'supply',
            'specification': {'dn': 'DN25'}, 'physical_instance_status': 'actual', 'view_id': 'V', 'level': 'confirmed', 'basis': 'actual INSERT instance',
            'recognition_basis': {'kind': 'model_working_interpretation', 'recognized_by': 'synthetic source author', 'review_status': 'unreviewed'},
            'source_span': {'path': [inst.dxf.handle, axis.dxf.handle]},
            'length_facts': [{'method': 'dimension', 'annotation_path': [inst.dxf.handle, annotation.dxf.handle],
                              'level': 'confirmed', 'basis': 'dimension value is not multiplied by block scale'}]}
        p['segments'].append(row)
    out = measure(p)
    assert [r['quantity'] for r in out['segments']] == [1.5, 1.5]
    assert out['segments'][0]['source_span']['path'][-1] == out['segments'][1]['source_span']['path'][-1]
    assert out['length_summary'][0]['quantity'] == 3.


def candidate_receipt(tmp_path):
    p, _ = setup(tmp_path)
    request = tmp_path / 'request.json'; write(request, p)
    result = measure(p); result['source_request'] = {'path': str(request), 'sha256': sha(request)}
    candidate = tmp_path / 'candidate.json'; write(candidate, result)
    network = {'evidence_version': 'qto-pipe-network-inference-evidence/1.2', 'network_id': 'N', 'unit': 'm',
        'segments': [{'segment_id': 'A', 'system': 'supply', 'dn': 'DN25', 'length': 10.,
                      'specification': p['segments'][0]['specification']},
                     {'segment_id': 'B', 'system': 'supply', 'dn': 'DN25', 'length': 3.}]}
    netpath = tmp_path / 'network.json'; write(netpath, network)
    relation = {'record_id': 'D-1', 'from_network_record_id': 'segment:A', 'to_network_record_id': 'segment:B',
        'basis': 'explicit physical extent relationships; network reference is not quantity authority', 'level': 'inferred',
        'evidence': [{**p['drawing'], 'locator': 'source view and physical interfaces'}],
        'coverage': {'disjoint_record_ids': ['segment:A', 'segment:B'], 'same_extent_record_ids': [],
                     'partial_overlap_record_ids': [], 'unknown_record_ids': []}}
    return {'schema_version': RECEIVE_REQUEST, 'scope_id': p['scope_id'], 'scope_basis': 'candidate relationship only',
        'network_id': 'N', 'candidate_result': {'path': str(candidate), 'sha256': sha(candidate)},
        'network_result': {'path': str(netpath), 'sha256': sha(netpath)}, 'relationships': [relation]}, network


def test_actual_producer_consumer_replay_and_layering(tmp_path):
    p, _ = candidate_receipt(tmp_path)
    out = receive(p)
    r = out['candidate_length_items'][0]
    assert r['candidate_addition_quantity'] == 2.5 and r['quantity_level'] == '规则推算量'
    assert out['producer_replay']['status'] == 'REPLAYED_FROM_SOURCE'
    assert out['network_quantity_produced'] is False and r['network_quantity_owner'] is None
    assert out['network_source_replayed'] is False and out['business_adoption_allowed'] is False


def test_same_physical_extent_has_one_candidate_outlet(tmp_path):
    p, network = candidate_receipt(tmp_path)
    network['segments'][0].update(physical_id='pipe-1', extent_id='extent-1', length=2.5)
    write(Path(p['network_result']['path']), network); p['network_result']['sha256'] = sha(p['network_result']['path'])
    coverage = p['relationships'][0]['coverage']
    coverage.update(disjoint_record_ids=['segment:B'], same_extent_record_ids=['segment:A'])
    r = receive(p)['candidate_length_items'][0]
    assert r['candidate_addition_quantity'] == 0 and r['status'] == 'already_counted_candidate'


def test_equal_length_without_physical_identity_remains_null(tmp_path):
    p, network = candidate_receipt(tmp_path)
    network['segments'][0]['length'] = 2.5
    write(Path(p['network_result']['path']), network); p['network_result']['sha256'] = sha(p['network_result']['path'])
    p['relationships'][0]['coverage'].update(disjoint_record_ids=['segment:B'], same_extent_record_ids=['segment:A'])
    assert receive(p)['candidate_length_items'][0]['candidate_addition_quantity'] is None


def test_unknown_coverage_and_incomplete_difference_not_silently_added(tmp_path):
    p, _ = candidate_receipt(tmp_path)
    coverage = p['relationships'][0]['coverage']
    coverage.update(disjoint_record_ids=['segment:B'], unknown_record_ids=['segment:A'])
    assert receive(p)['unprocessed_record_ids'] == ['D-1']
    coverage['unknown_record_ids'] = []
    with pytest.raises(ValueError, match='差集'):
        receive(p)


def test_tampered_candidate_fails_even_with_new_container_hash(tmp_path):
    p, _ = candidate_receipt(tmp_path)
    path = Path(p['candidate_result']['path']); data = json.loads(path.read_text(encoding='utf-8'))
    data['segments'][0]['quantity'] = 99.; write(path, data)
    p['candidate_result']['sha256'] = sha(path)
    with pytest.raises(ValueError, match='重放'):
        receive(p)


@pytest.mark.parametrize('changed_name', ['measure_pipe_diagram_lengths.py', 'qto_cad_geometry.py',
    'qto_cad_source_contract.py', 'qto_elevation_facts.py', 'recompute_qto_dependencies.py',
    'qto_human_review_evidence.py', 'qto_actor_identity.py'])
def test_source_and_dependency_byte_change_invalidates_real_cli_consumption(tmp_path, changed_name):
    p, _ = candidate_receipt(tmp_path)
    clone = tmp_path / 'clone'
    shared = clone / 'skills/_qto-shared-evidence/scripts'; shared.mkdir(parents=True)
    pipe = clone / 'skills/qto-pipe-networks/scripts'; pipe.mkdir(parents=True)
    for name in ('qto_cad_geometry.py', 'qto_cad_source_contract.py', 'qto_elevation_facts.py',
                 'recompute_qto_dependencies.py', 'qto_human_review_evidence.py', 'qto_actor_identity.py'):
        shutil.copyfile(ROOT / 'skills/_qto-shared-evidence/scripts' / name, shared / name)
    for name in ('measure_pipe_diagram_lengths.py', 'receive_pipe_diagram_lengths.py'):
        shutil.copyfile(PIPE / name, pipe / name)
    request = json.loads(Path(p['candidate_result']['path']).read_text(encoding='utf-8'))['source_request']['path']
    produced = tmp_path / 'cli-candidate.json'
    def run(script, input_path, output_path):
        return subprocess.run([sys.executable, '-B', str(pipe / script), '--input', str(input_path), '--output', str(output_path)],
                              stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8')
    result = run('measure_pipe_diagram_lengths.py', request, produced)
    assert result.returncode == 0, result.stderr
    p['candidate_result'] = {'path': str(produced), 'sha256': sha(produced)}
    receipt_request = tmp_path / 'receipt-request.json'; write(receipt_request, p)
    result = run('receive_pipe_diagram_lengths.py', receipt_request, tmp_path / 'first-receipt.json')
    assert result.returncode == 0, result.stderr
    changed = (pipe if changed_name == 'measure_pipe_diagram_lengths.py' else shared) / changed_name
    changed.write_text(changed.read_text(encoding='utf-8') + '\n# deliberate byte-change probe\n', encoding='utf-8')
    result = run('receive_pipe_diagram_lengths.py', receipt_request, tmp_path / 'changed-receipt.json')
    assert result.returncode != 0 and not (tmp_path / 'changed-receipt.json').exists()
    assert '字节' in result.stderr


def test_source_scope_and_output_overwrite_protected(tmp_path):
    p, _ = setup(tmp_path)
    p['views'][0]['bounds'] = [-2, -2, 6, 15]
    with pytest.raises(ValueError, match='范围'):
        measure(p)


def test_uninstantiated_typical_dimension_preserved_but_quantity_null(tmp_path):
    p, _ = setup(tmp_path)
    p['segments'][0]['physical_instance_status'] = 'typical_uninstantiated'
    r = measure(p)['segments'][0]
    assert r['measured_extent_length'] == 2.5 and r['quantity'] is None
    assert r['quantity_level'] == '未决量'


def test_valid_dimension_misbound_to_wrong_source_segment_is_null(tmp_path):
    p, _ = setup(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    wrong = d.modelspace().add_line((15, 0), (25, 0)); d.saveas(path)
    p['drawing']['sha256'] = sha(path)
    p['segments'][0]['source_span']['path'] = [wrong.dxf.handle]
    r = measure(p)['segments'][0]
    assert r['quantity'] is None and any('误绑' in issue for issue in r['issues'])


def test_free_text_binding_is_only_source_literal_not_quantity_proof(tmp_path):
    p, h = setup(tmp_path)
    p['segments'][0]['length_facts'][0]['annotation_path'] = h['other']
    r = measure(p)['segments'][0]
    assert r['measured_extent_length'] == 3. and r['quantity'] is None
    assert r['measurements'][0]['binding_verification'] == 'source_text_only_model_interpretation'


def test_same_datum_string_cannot_replace_actual_source_datum_binding(tmp_path):
    p, h = setup(tmp_path)
    f = elevation(h); f['to']['datum_annotation_path'] = h['title']
    p['segments'][0]['length_facts'] = [f]
    r = measure(p)['segments'][0]
    assert r['quantity'] is None and any('基准' in issue for issue in r['issues'])


def test_automatic_raw_dimension_has_no_implicit_physical_unit(tmp_path):
    p, h = setup(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    d.entitydb[h['dimension'][0]].dxf.text = '<>'
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    assert measure(p)['segments'][0]['quantity'] is None


def test_source_height_change_recomputes_with_same_page_geometry(tmp_path):
    p, h = setup(tmp_path); p['segments'][0]['length_facts'] = [elevation(h)]
    before = measure(p)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    d.entitydb[h['upper'][0]].dxf.text = '11.000m'
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    after = measure(p)
    assert before['segments'][0]['source_span'] == after['segments'][0]['source_span']
    assert before['segments'][0]['quantity'] == 5.5 and after['segments'][0]['quantity'] == 11.


def test_projected_dimension_not_pipe_route_length(tmp_path):
    p, h = setup(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    line = d.entitydb[h['line'][0]]; line.dxf.end = (10, 10, 0)
    dim = d.entitydb[h['dimension'][0]]; dim.dxf.defpoint3 = (10, 10, 0); dim.dxf.angle = 0
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    assert measure(p)['segments'][0]['quantity'] is None


def affine_dimension_request(tmp_path, *, projected=False, nested=False, rotation=45,
                             xscale=1, yscale=1, insert_extrusion=(0, 0, 1),
                             dimension_extrusion=(0, 0, 1), dimension_type=0):
    """Source dimensions use OCS angles; defpoints and axes use owner-block WCS."""
    d = ezdxf.new('R2010'); m = d.modelspace(); b = d.blocks.new('AXIS')
    title = m.add_text('Anonymous affine dimension control', dxfattribs={'insert': (0, 500)})
    from ezdxf.math import OCS
    ocs = OCS(dimension_extrusion)
    p0 = (0, 0, 0)
    p1 = tuple(ocs.to_wcs((120, 120 if projected else 0, 0)))
    axis = b.add_line(p0, p1)
    dimension = b.new_entity('DIMENSION', dxfattribs={'dimtype': 32+dimension_type, 'text': '8m',
        'defpoint': p0, 'defpoint2': p0, 'defpoint3': p1, 'angle': 0,
        'extrusion': dimension_extrusion})
    inner = []
    if nested:
        parent = d.blocks.new('PARENT')
        inst = parent.add_blockref('AXIS', (25, 5, 3), dxfattribs={'rotation': 31,
            'xscale': -1.5, 'yscale': .6, 'zscale': 2})
        inner = [inst.dxf.handle]; name = 'PARENT'
    else:
        name = 'AXIS'
    outer = m.add_blockref(name, (50, 80, 10), dxfattribs={'rotation': rotation,
        'xscale': xscale, 'yscale': yscale, 'zscale': 1.25, 'extrusion': insert_extrusion})
    path = tmp_path / 'affine-dimension.dxf'; d.saveas(path)
    chain = [outer.dxf.handle] + inner
    row = {'record_id': 'A', 'physical_id': 'physical-A', 'extent_id': 'extent-A', 'system': 'water',
        'specification': {'dn': 'DN32'}, 'physical_instance_status': 'actual',
        'from_port_id': 'P0', 'to_port_id': 'P1', 'view_id': 'V', 'level': 'confirmed',
        'basis': 'anonymous explicit source endpoints and literal length',
        'recognition_basis': {'kind': 'model_working_interpretation', 'recognized_by': 'anonymous fixture author', 'review_status': 'unreviewed'},
        'source_span': {'path': chain + [axis.dxf.handle]},
        'length_facts': [{'method': 'dimension', 'annotation_path': chain + [dimension.dxf.handle],
                         'basis': 'literal source dimension bound to full extent', 'level': 'confirmed'}]}
    return {'schema_version': REQUEST, 'drawing': {'path': str(path), 'sha256': sha(path)},
        'scope_id': 'affine-source-control', 'scope_basis': 'finite anonymous source',
        'views': [{'view_id': 'V', 'kind': 'principle', 'title_paths': [[title.dxf.handle]],
            'bounds': [-2000, -2000, 2000, 2000], 'basis': 'source title and finite window', 'level': 'confirmed'}],
        'segments': [row]}


@pytest.mark.parametrize('options', [
    {'rotation': 45}, {'rotation': -45}, {'rotation': 61, 'nested': True},
    {'rotation': 45, 'xscale': -2, 'yscale': 1},
    {'rotation': -45, 'xscale': 2, 'yscale': .5},
    {'rotation': 23, 'nested': True, 'xscale': -2, 'yscale': .5},
    {'rotation': 45, 'insert_extrusion': (0, 1, 1)},
    {'rotation': -45, 'dimension_extrusion': (0, 1, 0)},
    {'rotation': 33, 'nested': True, 'insert_extrusion': (1, 2, 3), 'dimension_extrusion': (0, 1, 0)},
])
@pytest.mark.parametrize('projected', [False, True])
def test_dimension_projection_relation_survives_complete_affine_chain(tmp_path, options, projected):
    p = affine_dimension_request(tmp_path, projected=projected, **options)
    r = measure(p)['segments'][0]
    if projected:
        assert r['quantity'] is None and any('投影尺寸' in issue for issue in r['issues'])
    else:
        assert r['quantity'] == 8.0
        proof = r['measurements'][0]['source_annotation']['axis_verification']
        assert proof['coordinate_basis'] == 'dimension_OCS_to_owner_WCS_then_complete_INSERT_chain'
        assert proof['parent_matrix'] and len(r['source_span']['path']) == (3 if options.get('nested') else 2)


def test_aligned_non_xy_dimension_and_out_of_plane_rejection(tmp_path):
    p = affine_dimension_request(tmp_path, projected=True, nested=True,
        dimension_type=1, dimension_extrusion=(0, 1, 0), insert_extrusion=(1, 2, 3))
    assert measure(p)['segments'][0]['quantity'] == 8.
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    dimension = d.entitydb[p['segments'][0]['length_facts'][0]['annotation_path'][-1]]
    dimension.dxf.extrusion = (0, 0, 1)
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    assert measure(p)['segments'][0]['quantity'] is None


@pytest.mark.parametrize('changed_name', ['qto_human_review_evidence.py', 'qto_actor_identity.py'])
def test_elevation_transitive_dependency_drift_and_restore_reject_stale_candidates(tmp_path, changed_name):
    p, _ = candidate_receipt(tmp_path)
    saved = json.loads(Path(p['candidate_result']['path']).read_text(encoding='utf-8'))
    request_path = Path(saved['source_request']['path'])
    source_request = json.loads(request_path.read_text(encoding='utf-8'))
    d = ezdxf.readfile(source_request['drawing']['path'])
    texts = {e.dxf.text: [e.dxf.handle] for e in d.modelspace().query('TEXT')}
    h = {'lower': texts['0.000m'], 'upper': texts['5.500m'], 'datum': texts['统一建筑标高基准 zero-A']}
    source_request['segments'][0]['length_facts'] = [elevation(h)]
    write(request_path, source_request)
    clone = tmp_path / 'elevation-clone'
    shared = clone / 'skills/_qto-shared-evidence/scripts'; shared.mkdir(parents=True)
    pipe = clone / 'skills/qto-pipe-networks/scripts'; pipe.mkdir(parents=True)
    expected = {'qto_cad_geometry.py', 'qto_cad_source_contract.py', 'qto_elevation_facts.py',
        'recompute_qto_dependencies.py', 'qto_human_review_evidence.py', 'qto_actor_identity.py'}
    for name in expected:
        shutil.copyfile(ROOT / 'skills/_qto-shared-evidence/scripts' / name, shared / name)
    for name in ('measure_pipe_diagram_lengths.py', 'receive_pipe_diagram_lengths.py'):
        shutil.copyfile(PIPE / name, pipe / name)
    def run(name, input_path, output_path):
        return subprocess.run([sys.executable, '-B', str(pipe/name), '--input', str(input_path), '--output', str(output_path)],
            stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8')
    original_candidate = tmp_path / 'original-elevation-candidate.json'
    assert run('measure_pipe_diagram_lengths.py', request_path, original_candidate).returncode == 0
    result = json.loads(original_candidate.read_text(encoding='utf-8'))
    assert result['segments'][0]['quantity'] == 5.5
    assert {Path(row['path']).name for row in result['runtime_dependencies']} == expected | {'measure_pipe_diagram_lengths.py'}
    p['candidate_result'] = {'path': str(original_candidate), 'sha256': sha(original_candidate)}
    original_receipt_request = tmp_path / 'original-elevation-receipt-request.json'; write(original_receipt_request, p)
    assert run('receive_pipe_diagram_lengths.py', original_receipt_request, tmp_path/'original-receipt.json').returncode == 0
    original_hashes = [sha(path) for path in (original_candidate, original_receipt_request, request_path, Path(source_request['drawing']['path']))]
    changed = shared / changed_name; original_bytes = changed.read_bytes()
    changed.write_bytes(original_bytes + b'\n# transitive byte drift control\n')
    rejected = tmp_path/'stale-under-drift.json'
    assert run('receive_pipe_diagram_lengths.py', original_receipt_request, rejected).returncode == 2
    assert not rejected.exists()
    drift_candidate = tmp_path/'fresh-under-drift.json'
    assert run('measure_pipe_diagram_lengths.py', request_path, drift_candidate).returncode == 0
    p['candidate_result'] = {'path': str(drift_candidate), 'sha256': sha(drift_candidate)}
    drift_request = tmp_path/'drift-request.json'; write(drift_request, p)
    assert run('receive_pipe_diagram_lengths.py', drift_request, tmp_path/'drift-receipt.json').returncode == 0
    changed.write_bytes(original_bytes)
    restored_reject = tmp_path/'drift-candidate-after-restore.json'
    assert run('receive_pipe_diagram_lengths.py', drift_request, restored_reject).returncode == 2
    assert not restored_reject.exists()
    assert run('receive_pipe_diagram_lengths.py', original_receipt_request, tmp_path/'original-after-restore.json').returncode == 0
    assert original_hashes == [sha(path) for path in (original_candidate, original_receipt_request, request_path, Path(source_request['drawing']['path']))]
