"""B15-INTERFACE-01: actual ATTRIB owner/cell placement through the SI adapter."""
import copy
import json

import ezdxf
import pytest

from test_geometric_units_t27_20261019 import case, save_source, credential, put, command, SHARED, PIPE, sha
from test_geometric_units_selected_arrays_batch15 import selected_array
from test_pipe_physical_network_t29_batch13 import fixture
from measure_pipe_diagram_lengths import Drawing
from pipe_diagram_source_parts import coordinate_unit_annotation


def carrier(case, *, zero=False, negative=True, attribute=True, outside_scope=False):
    if 'PIPE' not in case['doc'].layers:
        case['doc'].layers.new('PIPE')
    selected = selected_array(case, negative=negative, zero=zero, attribute='si' if attribute else None)
    # Put the actual native SI carrier beside the route, inside the already
    # valid scope. A rotated frame's outer bbox is not its true interior.
    si = case['doc'].entitydb[selected['refs']['si']['handle']]
    if not outside_scope:
        si.dxf.insert = selected['child'].matrix44().transform((12000, 30000, 0)) if attribute else (12000, 30000, 0)
    for block in case['doc'].blocks:
        for solid in list(block.query('SOLID')): block.delete_entity(solid)
    save_source(case)
    # Resolve a real reloaded DXF: ezdxf finalizes attached ATTRIB owner handles
    # on serialization; an unsaved in-memory add_attrib is not the source file.
    case['doc'] = ezdxf.readfile(case['path'])
    for key in ('root', 'child', 'route'):
        selected[key] = case['doc'].entitydb[selected[key].dxf.handle]
    selected['leaf'] = case['doc'].blocks[selected['leaf'].name]
    selected['native_root'] = list(selected['root'].multi_insert())[0 if zero else 3]
    selected['native_child'] = list(selected['child'].multi_insert())[0 if zero else 3]
    ref = selected['refs']['si']; path = [*ref['insert_handles'], ref['handle']]
    bounds = [case['scope']['bounds'][k] for k in ('xmin', 'ymin', 'xmax_exclusive', 'ymax_exclusive')]
    return selected, path, bounds


@pytest.mark.parametrize('zero,negative', [(False, False), (False, True), (True, True)])
def test_attached_coordinate_unit_matches_native_multi_insert_wcs_once(case, zero, negative):
    selected, path, bounds = carrier(case, zero=zero, negative=negative)
    native_attr = next(a for a in selected['native_child'].attribs if a.dxf.tag == 'SI')
    expected = selected['native_root'].matrix44().transform(native_attr.dxf.insert)
    note = coordinate_unit_annotation(Drawing(case['doc']), path, bounds)
    proof = credential(case)['coordinate_unit_evidence'][0]
    assert note['location'] == pytest.approx(tuple(expected), abs=1e-8, rel=0)
    assert note['location'] == pytest.approx(proof['source_context']['placement_points_wcs'][0], abs=1e-8, rel=0)
    assert note['source_owner_handle'] == selected['child'].dxf.handle
    assert note['path'] == path and note['text'] == '坐标单位：毫米'
    assert note['placement_rule'] == 'ATTRIB_parent_coordinates_plus_own_cell_offset_once_then_ancestors'
    # ATTRIB is only an SI carrier at this narrow adapter, never a new bare
    # length/height evidence channel in the ordinary diagram annotation API.
    with pytest.raises(ValueError, match='属性须另走'):
        Drawing(case['doc']).annotation(path, bounds)


@pytest.mark.parametrize('mutation', ['missing_parent', 'missing_cell', 'out_of_range', 'cell_alias',
    'wrong_owner', 'detached', 'hidden', 'out_of_scope'])
def test_coordinate_unit_attribute_rejects_invalid_owner_chain_cell_and_scope(case, mutation):
    selected, path, bounds = carrier(case)
    attr = case['doc'].entitydb[path[-1]]
    if mutation == 'missing_parent': path.pop(0)
    elif mutation == 'missing_cell': path[1] = selected['child'].dxf.handle
    elif mutation == 'out_of_range': path[1] = selected['child'].dxf.handle+'[2,0]'
    elif mutation == 'cell_alias': path[1] = selected['child'].dxf.handle+'[01,1]'
    elif mutation == 'wrong_owner': attr.dxf.owner = selected['leaf'].block_record_handle
    elif mutation == 'detached': selected['child'].attribs.remove(attr)
    elif mutation == 'hidden': attr.dxf.flags = attr.dxf.get('flags', 0)|1
    else: bounds = [0, 0, 1, 1]
    with pytest.raises(ValueError): coordinate_unit_annotation(Drawing(case['doc']), path, bounds)


def test_zero_spacing_attribute_cell_alias_is_not_a_second_instance(case):
    selected, path, bounds = carrier(case, zero=True)
    path[1] = selected['child'].dxf.handle+'[0,1]'
    with pytest.raises(ValueError, match='零间距cell'):
        coordinate_unit_annotation(Drawing(case['doc']), path, bounds)


def test_aligned_attribute_uses_native_placement_not_unused_insert_point(case):
    selected, path, bounds = carrier(case)
    attr = case['doc'].entitydb[path[-1]]
    attr.dxf.halign = 1
    attr.dxf.align_point = attr.dxf.insert
    attr.dxf.insert = (0, 0, 0)  # unused for this alignment, intentionally elsewhere
    save_source(case)
    doc = ezdxf.readfile(case['path'])
    note = coordinate_unit_annotation(Drawing(doc), path, bounds)
    proof = credential(case)['coordinate_unit_evidence'][0]['source_context']
    assert len(note['placement_points_wcs']) == len(proof['placement_points_wcs'])
    for actual, expected in zip(note['placement_points_wcs'], proof['placement_points_wcs']):
        assert actual == pytest.approx(expected, abs=1e-8, rel=0)


@pytest.mark.parametrize('mode', ['attrib_negative_nested', 'attrib_zero_nested', 'plain_text',
    'attrib_outside_route_scope', 'text_outside_route_scope'])
def test_actual_public_si_workbench_infer_produce_consume_preserves_original_unknown_dn(case, mode):
    selected, unit_path, bounds = carrier(case, zero=mode == 'attrib_zero_nested',
        negative=mode != 'plain_text', attribute=mode not in {'plain_text', 'text_outside_route_scope'},
        outside_scope='outside' in mode)
    original_source = case['path'].read_bytes(); stages = []
    def run(name, script, *args):
        result = command(script, *args)
        stages.append(dict(stage=name, command=[str(script), *map(str, args)], actual_exit_code=result.returncode,
            stdout=result.stdout, stderr=result.stderr))
        put(case['tmp']/'actual-attrib-adapter-cli.json', stages)
        return result
    neutral = case['tmp']/'fresh-neutral'
    taken = run('extract', SHARED/'extract_cad_neutral.py', '--source-dxf', case['path'], '--all-blocks', '--output-dir', neutral)
    assert taken.returncode == 3  # Actual array container PARTIAL remains PARTIAL.
    manifest_path = neutral/'CAD中性取证清单.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    request_path = put(case['tmp']/'unit-request.json', case['request'])
    scope_path = put(case['tmp']/'scope.json', case['scope']); unit_path_out = case['tmp']/'unit-credential.json'
    produced = run('unit', SHARED/'qto_geometric_unit_evidence.py', '--cad-manifest', manifest_path,
        '--request', request_path, '--scope', scope_path, '--output', unit_path_out)
    assert produced.returncode == 0, produced.stdout+produced.stderr
    cred = json.loads(unit_path_out.read_text(encoding='utf-8'))
    source = put(case['tmp']/'source-binding.json', manifest['exchange_dxf']); index_path = case['tmp']/'index.json'
    indexed = run('index', SHARED/'qto_recognition_workbench.py', 'index', '--source', source, '--bounds', *bounds, '--output', index_path)
    assert indexed.returncode == 0, indexed.stdout+indexed.stderr
    index = json.loads(index_path.read_text(encoding='utf-8'))
    obj = next(o for o in index['objects'] if o.get('source_handle') == case['handles']['pipe'])
    oid = obj['object_id']; authority = index['authoritative_source']
    ledger = dict(schema_version='qto-model-recognition-ledger/1.0', ledger_id='ATTRIB', ledger_version='1',
        source_sha256=authority['sha256'], adoption_scope='working', review_status='unreviewed',
        evidence=[dict(evidence_id='SRC', path=authority['path'], sha256=authority['sha256'],
            source_sha256=authority['sha256'], kind='cad_property', locator='actual selected array source route', observation='source LINE; DN unknown')],
        entries=[dict(adjudication_id='PIPE', subject_id='source', professional_owner='qto-pipe-networks', source_id='SRC',
            recognized_by='anonymous model control', recognized_at='2026-10-20', basis='original native source',
            scope_basis='actual selected scope', identity_basis='complete instance path', cross_check='source endpoints',
            decision='include', decision_origin='model', status='active', object_ids=[oid], evidence_ids=['SRC'], uncertainties=['DN unknown'])])
    ledger_path = put(case['tmp']/'ledger.json', ledger)
    operations = dict(index_sha256=index['index_sha256'], network_id='network-a', scope_object_ids=[oid],
        scope_basis='actual selected native route', unit='m', geometric_unit_evidence=cred, measurement_scope=case['scope'],
        factor_to_output=.001, parameters=dict(snap_tolerance=.0001, gap_max=.1), parameter_basis='source finite geometry',
        assignments=[dict(adjudication_id='PIPE', object_ids=[oid], role='pipe_centerline', system='water', observation='source LINE')],
        relations=[], endpoint_review=dict(complete=False, basis='DN and actual port adoption unknown', unresolved_object_ids=[oid]))
    operations_path = put(case['tmp']/'operations.json', operations); prepared = case['tmp']/'network-input.json'
    applied = run('apply', SHARED/'qto_recognition_workbench.py', 'apply', '--index', index_path,
        '--operations', operations_path, '--ledger', ledger_path, '--output', prepared)
    assert applied.returncode == 0, applied.stdout+applied.stderr
    inferred = case['tmp']/'network-result.json'
    result = run('infer', PIPE/'infer_pipe_network.py', '--input', prepared, '--output', inferred)
    assert result.returncode == 0, result.stdout+result.stderr
    network = json.loads(inferred.read_text(encoding='utf-8')); base = network['segments'][0]
    assert len(network['segments']) == 1 and base['length'] == pytest.approx(10.) and base['dn'] is None
    mapping = dict(segment_id=base['segment_id'], physical_id='base', extent_id='full', from_port_id='B0', to_port_id='B1',
        specification=dict(dn=None, material=None), drawing=manifest['exchange_dxf'], bounds=bounds,
        source_span=dict(path=obj['source_path'], part=0, from_fraction=0., to_fraction=1.),
        coordinate_unit_annotation_path=unit_path,
        recognition_basis=dict(kind='model_working_interpretation', review_status='unreviewed', recognized_by='anonymous model control'),
        basis='public SI source, original instance path and unchanged source extent')
    diagram, _ = fixture(case['tmp'], name='separate-diagram', physical='diagram', ports=('D0', 'D1'))
    diagram_request = put(case['tmp']/'diagram-request.json', diagram)
    diagram_output = case['tmp']/'diagram-candidate.json'
    measured = run('measure', PIPE/'measure_pipe_diagram_lengths.py', '--input', diagram_request, '--output', diagram_output)
    assert measured.returncode == 0, measured.stdout+measured.stderr
    diagram_binding = dict(path=str(diagram_output), sha256=sha(diagram_output))
    extra = dict(network_input=dict(path=str(prepared), sha256=sha(prepared)),
        network_result=dict(path=str(inferred), sha256=sha(inferred)), base_extent_bindings=[mapping])
    production_request = put(case['tmp']/'production-request.json', dict(schema_version='qto-pipe-physical-network-production/1.0',
        network_id='network-a', scope_id='finite', scope_basis='source SI adapter interface', candidate_results=[diagram_binding], **extra))
    production_output = case['tmp']/'production-result.json'
    production = run('produce', PIPE/'produce_pipe_diagram_network.py', '--input', production_request, '--output', production_output)
    assert production.returncode == 0, production.stdout+production.stderr
    consume_request = put(case['tmp']/'consume-request.json', dict(schema_version='qto-pipe-physical-network-consumption/1.0',
        network_id='network-a', scope_id='finite', scope_basis='source SI adapter interface',
        network_result=dict(path=str(production_output), sha256=sha(production_output))))
    consume_output = case['tmp']/'consumed-result.json'
    consumed = run('consume', PIPE/'consume_pipe_physical_network.py', '--input', consume_request, '--output', consume_output)
    assert consumed.returncode == 0, consumed.stdout+consumed.stderr
    out = json.loads(consume_output.read_text(encoding='utf-8'))
    row = next(r for r in out['quantity_items'] if r['physical_id'] == 'base')
    assert out['preserved_existing_network'] == network
    assert row['quantity'] is None and row['specification'] == dict(dn=None, material=None)
    evidence = row['representations'][0]['measurements'][0]['source_unit_note']
    assert evidence['path'] == unit_path and evidence['entity_type'] == ('TEXT' if mode in {'plain_text', 'text_outside_route_scope'} else 'ATTRIB')
    source_context = cred['coordinate_unit_evidence'][0]['source_context']
    assert evidence['location'] == pytest.approx(source_context['placement_points_wcs'][0])
    if 'outside' in mode:
        x, y = evidence['location'][:2]
        assert not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])
    # Prove saved source bytes remain authoritative after successful consumption.
    case['path'].write_bytes(original_source+b'\n')
    stale = run('source_drift', PIPE/'consume_pipe_physical_network.py', '--input', case['tmp']/'consume-request.json',
        '--output', case['tmp']/'must-not-exist.json')
    assert stale.returncode == 2 and not (case['tmp']/'must-not-exist.json').exists()
    case['path'].write_bytes(original_source)
    assert out['business_adoption_allowed'] is False and out['formal_total_quantity'] is None
