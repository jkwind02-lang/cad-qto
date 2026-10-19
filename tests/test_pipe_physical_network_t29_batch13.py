"""Anonymous source/CLI controls for real working pipe quantity production."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT/'skills/qto-pipe-networks/scripts'
sys.path.insert(0, str(PIPE))
from measure_pipe_diagram_lengths import measure, sha, REQUEST as MEASURE_REQUEST, Drawing
from produce_pipe_diagram_network import produce, REQUEST as PRODUCTION_REQUEST
from consume_pipe_physical_network import consume, REQUEST as CONSUME_REQUEST


def si_cli(directory, *arguments):
    """Keep actual subprocess exits; all source material is anonymous tmp_path."""
    command = [sys.executable, '-B', *map(str, arguments)]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8')
    with (directory/'si-cli.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(command=command, actual_exit_code=result.returncode,
            stdout=result.stdout, stderr=result.stderr), ensure_ascii=False)+'\n')
    return result


def si_workbench(tmp_path, kind='line'):
    """Use the existing public SI, index, apply and infer producers unchanged."""
    tmp_path.mkdir(exist_ok=True)
    shared = ROOT/'skills/_qto-shared-evidence/scripts'
    d = ezdxf.new('R2010'); d.units = 0; d.layers.new('ROUTE'); m = d.modelspace()
    if kind == 'line':
        route = m.add_line((0, 0), (10000, 0), dxfattribs={'layer': 'ROUTE'})
    else:
        inner = d.blocks.new('INNER')
        route = (inner.add_line((0, 0), (10000, 0)) if kind == 'nested'
                 else inner.add_lwpolyline([(0, 0), (4000, 0), (4000, 6000)]))
        outer = d.blocks.new('OUTER'); outer.add_blockref('INNER', (1000, 0))
        m.add_blockref('OUTER', (2000, 0))
    frame = m.add_lwpolyline([(-20000,-20000),(64100,-20000),(64100,39400),(-20000,39400)], close=True)
    scale = m.add_text('1:100', dxfattribs={'insert': (50000, -18000), 'height': 100})
    title = m.add_text('匿名管道平面图', dxfattribs={'insert': (-18000, -18000), 'height': 100})
    clause = m.add_text('坐标单位：毫米', dxfattribs={'insert': (1000, 6000), 'height': 100})
    dn = m.add_text('DN25', dxfattribs={'insert': (1000, 2000), 'height': 100})
    bare = m.add_text('mm', dxfattribs={'insert': (1000000, 2000), 'height': 100})
    dim = m.add_aligned_dim((1000, 1000), (7000, 1000), distance=500, override={'dimlfac': 1.})
    dim.render()
    for block in d.blocks:
        for solid in list(block.query('SOLID')):
            block.delete_entity(solid)
    source = tmp_path/'anonymous-physical-source.dxf'; d.saveas(source)
    neutral = tmp_path/'neutral'
    done = si_cli(tmp_path, shared/'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', neutral)
    assert done.returncode == 0, done.stderr
    manifest = neutral/'CAD中性取证清单.json'; mf = json.loads(manifest.read_text(encoding='utf-8'))
    bounds = [-20000., -20000., 64100., 39400.]
    scope = dict(layout='Model', boundary_policy='contained', bounds=dict(zip(
        ('xmin','ymin','xmax_exclusive','ymax_exclusive'), bounds)))
    ur = dict(schema_version='qto-geometric-unit-request/1.0', raw_unit='Millimeters',
        measurement_semantics='plan_to_scale', frame=dict(entity_refs=[dict(handle=frame.dxf.handle)]),
        scale_text=dict(handle=scale.dxf.handle), view_text_refs=[dict(handle=title.dxf.handle)],
        dimension_refs=[dict(handle=dim.dimension.dxf.handle)], coordinate_unit_refs=[dict(handle=clause.dxf.handle)],
        basis='source coordinate SI clause, finite frame and independent dimensions')
    urp = tmp_path/'unit-request.json'; write(urp, ur)
    sp = tmp_path/'scope.json'; write(sp, scope); cp = tmp_path/'credential.json'
    done = si_cli(tmp_path, shared/'qto_geometric_unit_evidence.py', '--cad-manifest', manifest,
        '--request', urp, '--scope', sp, '--output', cp)
    assert done.returncode == 0, done.stderr
    credential = json.loads(cp.read_text(encoding='utf-8'))
    sr = tmp_path/'source.json'; write(sr, mf['exchange_dxf'])
    ix = tmp_path/'index.json'
    done = si_cli(tmp_path, shared/'qto_recognition_workbench.py', 'index', '--source', sr,
        '--bounds', *bounds, '--output', ix)
    assert done.returncode == 0, done.stderr
    index = json.loads(ix.read_text(encoding='utf-8'))
    obj = next(o for o in index['objects'] if o.get('source_handle') == route.dxf.handle)
    note = next(o for o in index['objects'] if o.get('source_handle') == dn.dxf.handle)
    objects = [obj['object_id'], note['object_id']]
    authority = index['authoritative_source']
    ledger = dict(schema_version='qto-model-recognition-ledger/1.0', ledger_id='MODEL', ledger_version='1',
        source_sha256=authority['sha256'], adoption_scope='working', review_status='unreviewed',
        evidence=[dict(evidence_id='EV-source', path=authority['path'], sha256=authority['sha256'],
            source_sha256=authority['sha256'], kind='cad_property', locator='source instances', observation='actual paths and DN')],
        entries=[dict(adjudication_id='PIPE', subject_id='branch', professional_owner='qto-pipe-networks', source_id='SRC',
            recognized_by='model', recognized_at='2026-10-19T10:00:00+08:00', basis='source route and DN observations',
            scope_basis='selected source branch', identity_basis='source SHA and complete instance path',
            cross_check='source branch endpoints', decision='include', decision_origin='model', status='active',
            object_ids=objects, evidence_ids=['EV-source'], uncertainties=[])])
    lp = tmp_path/'ledger.json'; write(lp, ledger)
    oid = obj['object_id']; nid = note['object_id']
    ops = dict(index_sha256=index['index_sha256'], network_id='network-a', scope_object_ids=objects,
        scope_basis='source finite pipe branch', unit='m', geometric_unit_evidence=credential,
        measurement_scope=scope, factor_to_output=.001, parameters=dict(snap_tolerance=.0001, gap_max=.1),
        parameter_basis='source touching endpoints and bounded gap candidate',
        assignments=[dict(adjudication_id='PIPE', object_ids=[oid], role='pipe_centerline', system='water', observation='source centerline')],
        relations=[dict(relation_id='DN', kind='dn_annotation', target_object_id=oid, annotation_object_id=nid,
            annotation_text='DN25', dn='DN25', adjudication_id='PIPE', observation='actual DN text'),
            dict(relation_id='ROOT', kind='root', target=dict(object_id=oid, end='start'), adjudication_id='PIPE', observation='source start'),
            dict(relation_id='END', kind='terminal', target=dict(object_id=oid, end='end'), terminal_type='source physical port',
                port_dn='DN25', port_dn_annotation_object_ids=[nid], adjudication_id='PIPE', observation='source end')],
        endpoint_review=dict(complete=False, basis='only selected branch; other objects retained'))
    if kind == 'split':
        ops['relations'].append(dict(relation_id='CUT', kind='scope_boundary',
            target=dict(object_id=oid, part_index=0, parameter=.5), adjudication_id='PIPE', observation='source explicit internal boundary'))
    op = tmp_path/'operations.json'; write(op, ops); ip = tmp_path/'base-input.json'
    done = si_cli(tmp_path, shared/'qto_recognition_workbench.py', 'apply', '--index', ix,
        '--operations', op, '--ledger', lp, '--output', ip)
    assert done.returncode == 0, done.stderr
    result_path = tmp_path/'base-result.json'
    done = si_cli(tmp_path, PIPE/'infer_pipe_network.py', '--input', ip, '--output', result_path)
    assert done.returncode == 0, done.stderr
    prepared = json.loads(ip.read_text(encoding='utf-8')); result = json.loads(result_path.read_text(encoding='utf-8'))
    mappings = []
    # Expected ranges come from this explicit source and source boundary operation,
    # never by adding missing fields to a saved T27 input or output.
    ranges = [(0, 0., 1.)] if kind != 'split' else [(0, 0., .5), (0, .5, 1.), (1, 0., 1.)]
    assert len(result['segments']) == len(ranges)
    for i, (item, (part, first, last)) in enumerate(zip(result['segments'], ranges)):
        mappings.append(dict(segment_id=item['segment_id'], physical_id='base-physical', extent_id='base-extent-'+str(i),
            from_port_id='B'+str(i), to_port_id='B'+str(i+1), drawing=mf['exchange_dxf'], bounds=bounds,
            source_span=dict(path=obj['source_path'], part=part, from_fraction=first, to_fraction=last),
            coordinate_unit_annotation_path=[clause.dxf.handle], specification=dict(dn=item['dn'], material=None),
            recognition_basis=dict(kind='model_working_interpretation', recognized_by='anonymous model control', review_status='unreviewed'),
            basis='actual index path, unchanged producer contract and source part boundary'))
    extra = dict(network_input=dict(path=str(ip), sha256=sha(ip)),
        network_result=dict(path=str(result_path), sha256=sha(result_path)), base_extent_bindings=mappings)
    return dict(extra=extra, input=prepared, result=result, index=index, object=obj, input_path=ip,
        source=source, credential=credential, clause=clause.dxf.handle, bare=bare.dxf.handle, scope=scope)


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return {'path': str(path), 'sha256': sha(path)}


def run(name, source, target, root=PIPE):
    return subprocess.run([sys.executable, '-B', str(root/name), '--input', str(source), '--output', str(target)],
        stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8')


def fixture(tmp_path, name='a', physical='physical-a', ports=('P0', 'P1'), value='7m'):
    d = ezdxf.new('R2010'); m = d.modelspace()
    title = m.add_text('Anonymous system control', dxfattribs={'insert': (0, 10)})
    line = m.add_line((0, 0), (10, 0))
    dim = m.add_aligned_dim((0, 0), (10, 0), distance=1, text=value); dim.render()
    note = m.add_text('绘图坐标单位mm', dxfattribs={'insert': (0, 9)})
    path = tmp_path/(name+'.dxf'); d.saveas(path)
    p = {'schema_version': MEASURE_REQUEST, 'scope_id': 'finite', 'scope_basis': 'one finite control',
        'drawing': {'path': str(path), 'sha256': sha(path)},
        'views': [{'view_id': 'V', 'kind': 'system', 'title_paths': [[title.dxf.handle]],
            'bounds': [-5, -5, 30, 20], 'level': 'confirmed', 'basis': 'source finite view'}],
        'segments': [{'record_id': 'A', 'physical_id': physical, 'extent_id': 'extent-a',
            'from_port_id': ports[0], 'to_port_id': ports[1], 'system': 'water',
            'specification': {'dn': 'DN25', 'material': None}, 'physical_instance_status': 'actual',
            'source_span': {'path': [line.dxf.handle]}, 'view_id': 'V', 'level': 'confirmed',
            'basis': 'source actual straight part', 'recognition_basis': {
                'kind': 'model_working_interpretation', 'recognized_by': 'anonymous fixture author',
                'review_status': 'unreviewed'}, 'length_facts': [{'method': 'dimension',
                'annotation_path': [dim.dimension.dxf.handle], 'level': 'confirmed', 'basis': 'source along-axis dimension'}]}]}
    return p, {'line': line.dxf.handle, 'dimension': dim.dimension.dxf.handle, 'unit': note.dxf.handle}


def candidate(tmp_path, p, name):
    request = tmp_path/(name+'-request.json'); write(request, p)
    output = tmp_path/(name+'-candidate.json')
    execution = run('measure_pipe_diagram_lengths.py', request, output)
    assert execution.returncode == 0, execution.stderr
    return {'path': str(output), 'sha256': sha(output)}


def chain(tmp_path, candidates, extra=None):
    data = {'schema_version': PRODUCTION_REQUEST, 'scope_id': 'finite', 'network_id': 'network-a',
            'scope_basis': 'source physical pipe control', 'candidate_results': candidates, **(extra or {})}
    request = tmp_path/'production-request.json'; write(request, data)
    output = tmp_path/'produced-network.json'
    result = run('produce_pipe_diagram_network.py', request, output)
    assert result.returncode == 0, result.stderr
    consume_request = {'schema_version': CONSUME_REQUEST, 'network_id': 'network-a', 'scope_id': 'finite',
        'scope_basis': 'deliver finite physical extents', 'network_result': {'path': str(output), 'sha256': sha(output)}}
    input_path = tmp_path/'consume-request.json'; write(input_path, consume_request)
    delivered = tmp_path/'delivered-network.json'
    result = run('consume_pipe_physical_network.py', input_path, delivered)
    assert result.returncode == 0, result.stderr
    return json.loads(delivered.read_text(encoding='utf-8')), consume_request


def test_real_measure_produce_consume_cli_delivers_working_quantity(tmp_path):
    p, _ = fixture(tmp_path)
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'one')])
    row = out['quantity_items'][0]
    assert row['quantity'] == 7 and row['quantity_level'] == '规则推算量'
    assert out['quantity_outlet'] == 'quantity_items[].quantity'
    assert out['network_quantity_produced'] is True and out['business_adoption_allowed'] is False
    assert out['formal_total_quantity'] is None and out['scope_complete'] is False
    assert row['representations'][0]['source_span']['page_coordinate_length'] == 10


def test_same_physical_extent_two_views_has_one_owner_and_no_double_sum(tmp_path):
    a, _ = fixture(tmp_path, 'a'); b, _ = fixture(tmp_path, 'b')
    out, _ = chain(tmp_path, [candidate(tmp_path, a, 'one'), candidate(tmp_path, b, 'two')])
    assert len(out['quantity_items']) == 1
    assert out['quantity_items'][0]['quantity'] == 7
    assert out['quantity_items'][0]['deduplicated_representation_count'] == 1
    assert out['length_summary'][0]['quantity'] == 7


@pytest.mark.parametrize('mutation', ['length', 'specification', 'ports', 'renamed_source', 'partial_source'])
def test_conflicting_or_overlapping_representations_stay_null(tmp_path, mutation):
    a, h = fixture(tmp_path, 'a'); b = copy.deepcopy(a)
    if mutation == 'length':
        b, _ = fixture(tmp_path, 'b', value='8m')
    elif mutation == 'specification':
        b['segments'][0]['specification']['material'] = 'other'
    elif mutation == 'ports':
        b['segments'][0]['to_port_id'] = 'P2'
    elif mutation == 'renamed_source':
        b['segments'][0].update(physical_id='renamed', extent_id='renamed', from_port_id='Q0', to_port_id='Q1')
    else:
        b['segments'][0]['source_span'].update(from_fraction=.5, to_fraction=1.)
    out, _ = chain(tmp_path, [candidate(tmp_path, a, 'one'), candidate(tmp_path, b, 'two')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] is None
    assert out['unresolved_quantity_owners']


def test_equal_lengths_with_distinct_source_and_physical_identity_are_not_deduplicated(tmp_path):
    a, _ = fixture(tmp_path, 'a'); b, _ = fixture(tmp_path, 'b', physical='physical-b', ports=('Q0', 'Q1'))
    out, _ = chain(tmp_path, [candidate(tmp_path, a, 'one'), candidate(tmp_path, b, 'two')])
    assert len(out['quantity_items']) == 2 and out['length_summary'][0]['quantity'] == 14


def test_tamper_and_source_drift_rejected_by_actual_consumer_cli(tmp_path):
    p, h = fixture(tmp_path)
    _, request = chain(tmp_path, [candidate(tmp_path, p, 'one')])
    path = Path(request['network_result']['path']); saved = json.loads(path.read_text(encoding='utf-8'))
    saved['physical_edges'][0]['quantity'] = 99; write(path, saved)
    request['network_result']['sha256'] = sha(path)
    input_path = tmp_path/'tamper-request.json'; write(input_path, request)
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'tampered.json')
    assert result.returncode == 2 and not (tmp_path/'tampered.json').exists()
    assert '重放' in result.stderr
    d = ezdxf.readfile(p['drawing']['path']); d.entitydb[h['dimension']].dxf.text = '8m'; d.saveas(p['drawing']['path'])
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'stale.json')
    assert result.returncode == 2 and not (tmp_path/'stale.json').exists()


def test_dimension_chain_native_endpoints_partition_extent(tmp_path):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path); m = d.modelspace(); dims = []
    for start, end, value in [((0, 0), (4, 0), '2m'), ((4, 0), (10, 0), '5m')]:
        dim = m.add_aligned_dim(start, end, distance=2, text=value); dim.render(); dims.append([dim.dimension.dxf.handle])
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    p['segments'][0]['length_facts'] = [{'method': 'dimension_chain', 'dimension_paths': dims[::-1],
        'level': 'confirmed', 'basis': 'two native dimensions exactly cover full straight part'}]
    assert measure(p)['segments'][0]['quantity'] == 7
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'chain')])
    assert out['quantity_items'][0]['quantity'] == 7


@pytest.mark.parametrize('problem', ['gap', 'overlap', 'wrong_axis', 'wrong_source', 'unitless'])
def test_invalid_dimension_chains_do_not_produce_length(tmp_path, problem):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path); m = d.modelspace()
    first_end = (4, 0); second_start = (5, 0) if problem == 'gap' else (3, 0) if problem == 'overlap' else (4, 0)
    dims = []
    for i, (start, end) in enumerate([((0, 0), first_end), (second_start, (10, 1) if problem == 'wrong_source' else (10, 0))]):
        dim = m.add_aligned_dim(start, end, distance=2, text='2' if problem == 'unitless' else '2m'); dim.render()
        if problem == 'wrong_axis' and i == 1:
            dim.dimension.dxf.dimtype = 32; dim.dimension.dxf.angle = 90
        dims.append([dim.dimension.dxf.handle])
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    p['segments'][0]['length_facts'] = [{'method': 'dimension_chain', 'dimension_paths': dims,
        'level': 'confirmed', 'basis': 'deliberate bad source chain'}]
    assert measure(p)['segments'][0]['quantity'] is None


@pytest.mark.parametrize('problem', [None, 'unattached', 'crossing', 'crossing_block', 'endpoint', 'bare_text', 'partial'])
def test_native_leader_actual_annotation_handle_and_unique_target(tmp_path, problem):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path); m = d.modelspace()
    text_entity = m.add_text('7m' if problem == 'bare_text' else 'L=7m', dxfattribs={'insert': (6, 3)})
    arrow = (0, 0) if problem == 'endpoint' else (5, 0)
    leader = m.add_leader([arrow, (5, 3), (6, 3)], dxfattribs={'annotation_handle': text_entity.dxf.handle})
    if problem == 'unattached':
        leader.dxf.annotation_handle = '0'
    if problem == 'crossing':
        m.add_line((5, -2), (5, 2))
    if problem == 'crossing_block':
        block = d.blocks.new('CROSSING_SIBLING'); block.add_line((0, -2), (0, 2))
        m.add_blockref('CROSSING_SIBLING', (5, 0))
    if problem == 'partial':
        p['segments'][0]['source_span'].update(from_fraction=.1, to_fraction=.9)
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    p['segments'][0]['length_facts'] = [{'method': 'dimension', 'annotation_path': [text_entity.dxf.handle],
        'leader_path': [leader.dxf.handle], 'level': 'confirmed', 'basis': 'native L= leader complete straight part'}]
    row = measure(p)['segments'][0]
    if problem:
        assert row['quantity'] is None
    else:
        assert row['quantity'] == 7
        out, _ = chain(tmp_path, [candidate(tmp_path, p, 'leader')])
        assert out['quantity_items'][0]['quantity'] == 7


def test_legacy_real_infer_replay_and_source_geometry_adaptation(tmp_path):
    p, h = fixture(tmp_path, value='.01m')
    original = {'schema_version': 'qto-pipe-network-inference-input/1.0', 'network_id': 'network-a', 'unit': 'm',
        'segments': [{'segment_id': 'base-a', 'system': 'water', 'start': [0, 0], 'end': [.01, 0],
            'source_ids': ['anonymous-source'], 'source_instance_path': [h['line']],
            'source_part_index': 0, 'source_from_fraction': 0., 'source_to_fraction': 1., 'dn_seeds': [
                {'dn': 'DN25', 'evidence_level': 'confirmed', 'source_id': 'source-dn'}]}]}
    input_path = tmp_path/'base-input.json'; input_binding = write(input_path, original)
    output = tmp_path/'base-result.json'; result = run('infer_pipe_network.py', input_path, output)
    assert result.returncode == 0, result.stderr
    extra = {'network_input': input_binding, 'network_result': {'path': str(output), 'sha256': sha(output)},
        'base_extent_bindings': [{'segment_id': 'base-a', 'physical_id': 'physical-a', 'extent_id': 'extent-a',
            'from_port_id': 'P0', 'to_port_id': 'P1', 'drawing': p['drawing'], 'bounds': [-5, -5, 30, 20],
            'source_span': p['segments'][0]['source_span'], 'coordinate_unit_annotation_path': [h['unit']],
            'specification': p['segments'][0]['specification'], 'recognition_basis': p['segments'][0]['recognition_basis'],
            'basis': 'source direct drawn part and native system dimension identify same actual physical extent'}]}
    out, request = chain(tmp_path, [candidate(tmp_path, p, 'diagram')], extra)
    assert out['quantity_items'][0]['quantity'] == .01 and len(out['quantity_items']) == 1
    assert out['quantity_items'][0]['deduplicated_representation_count'] == 1
    assert out['preserved_existing_network']['network_id'] == 'network-a'
    raw = json.loads(output.read_text(encoding='utf-8')); raw['segments'][0]['length'] = 99; write(output, raw)
    # Re-hashing a forged original result cannot bypass original-input replay.
    production_path = Path(json.loads(Path(request['network_result']['path']).read_text(encoding='utf-8'))['source_request']['path'])
    production = json.loads(production_path.read_text(encoding='utf-8')); production['network_result']['sha256'] = sha(output)
    with pytest.raises(ValueError, match='重放'):
        produce(production)


def typed_fixture(tmp_path):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path); m = d.modelspace(); h = {}
    for index, (key, value) in enumerate({'bottom': '1m', 'upper': '5m', 'od': '108mm',
        'datum-a': '建筑高程基准 A', 'datum-b': '项目高程基准 B', 'relation': 'z(A)=z(B)+3m',
        'kind-bottom': '本段标高为管外底', 'kind-upper': '本段标高为管中心',
        'kind-od': '本管外径，非DN'}.items()):
        h[key] = [m.add_text(value, dxfattribs={'insert': (index*2, 5)}).dxf.handle]
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    fact = {'method': 'typed_elevation', 'route_kind': 'vertical', 'level': 'confirmed',
        'basis': 'source typed ports and directed datum equation for one actual vertical extent',
        'from': {'annotation_path': h['bottom'], 'measurement_kind': 'pipe_outer_bottom',
            'datum_id': 'A', 'datum_annotation_path': h['datum-a'], 'measurement_reference_id': 'bottom-point',
            'measurement_semantics_annotation_path': h['kind-bottom'],
            'centerline_conversion': {'method': 'outer_radius', 'operand': {'annotation_path': h['od']},
                'operand_semantics_annotation_path': h['kind-od']}},
        'to': {'annotation_path': h['upper'], 'measurement_kind': 'pipe_center', 'datum_id': 'B',
            'datum_annotation_path': h['datum-b'], 'measurement_semantics_annotation_path': h['kind-upper']},
        'datum_transform_annotation_paths': [h['relation']]}
    p['segments'][0]['length_facts'] = [fact]
    return p, h


def test_typed_source_outer_diameter_and_explicit_datum_equation_actual_quantity(tmp_path):
    p, _ = typed_fixture(tmp_path)
    row = measure(p)['segments'][0]
    assert row['quantity'] == pytest.approx(6.946)
    assert row['quantity_level'] == '规则推算量'
    calc = row['measurements'][0]['elevation_calculation']
    assert calc['review_state'] == 'model_working_unreviewed' and calc['business_adoption_allowed'] is False
    assert calc['endpoints'][0]['centerline_value_m'] == 1.054
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'typed')])
    assert out['quantity_items'][0]['quantity'] == pytest.approx(6.946)


def test_typed_missing_datum_path_keeps_unresolved_not_equal_datum_assumption(tmp_path):
    p, _ = typed_fixture(tmp_path)
    p['segments'][0]['length_facts'][0]['datum_transform_annotation_paths'] = []
    assert measure(p)['segments'][0]['quantity'] is None


@pytest.mark.parametrize('problem', ['unknown_relation', 'contradictory_relation', 'negative_od',
    'dn_substitution', 'forged_value', 'wrong_measurement_kind', 'missing_source_semantics'])
def test_typed_source_conversion_rejects_semantic_and_operand_forgery(tmp_path, problem):
    p, h = typed_fixture(tmp_path); fact = p['segments'][0]['length_facts'][0]
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    if problem == 'unknown_relation':
        d.entitydb[h['relation'][0]].dxf.text = 'A与B大概相同'
    elif problem == 'contradictory_relation':
        extra = d.modelspace().add_text('z(A)=z(B)+4m', dxfattribs={'insert': (20, 5)})
        fact['datum_transform_annotation_paths'].append([extra.dxf.handle])
    elif problem == 'negative_od':
        d.entitydb[h['od'][0]].dxf.text = '-108mm'
    elif problem == 'dn_substitution':
        fact['from']['centerline_conversion']['operand']['dn'] = 'DN100'
    elif problem == 'forged_value':
        fact['from']['value'] = 999
    elif problem == 'wrong_measurement_kind':
        fact['from']['measurement_kind'] = 'pipe_outer_top'
    else:
        fact['from']['centerline_conversion']['operand_semantics_annotation_path'] = h['kind-upper']
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    with pytest.raises(ValueError):
        measure(p)


@pytest.mark.parametrize('changed', ['produce_pipe_diagram_network.py', 'consume_pipe_physical_network.py',
    'infer_pipe_network.py', 'qto_elevation_facts.py'])
def test_actual_consumer_fixed_dependency_envelope_drift_and_restore(tmp_path, changed):
    p, _ = fixture(tmp_path)
    clone = tmp_path/'clone'; shutil.copytree(ROOT/'skills/qto-pipe-networks/scripts', clone/'skills/qto-pipe-networks/scripts')
    shutil.copytree(ROOT/'skills/_qto-shared-evidence/scripts', clone/'skills/_qto-shared-evidence/scripts')
    root = clone/'skills/qto-pipe-networks/scripts'
    source_request = tmp_path/'clone-source-request.json'; write(source_request, p)
    measured = tmp_path/'clone-measured.json'
    result = run('measure_pipe_diagram_lengths.py', source_request, measured, root)
    assert result.returncode == 0, result.stderr
    production = {'schema_version': PRODUCTION_REQUEST, 'scope_id': 'finite', 'network_id': 'network-a',
        'scope_basis': 'source control', 'candidate_results': [{'path': str(measured), 'sha256': sha(measured)}]}
    production_input = tmp_path/'clone-production-input.json'; write(production_input, production)
    network = tmp_path/'clone-network.json'
    result = run('produce_pipe_diagram_network.py', production_input, network, root)
    assert result.returncode == 0, result.stderr
    request = {'schema_version': CONSUME_REQUEST, 'network_id': 'network-a', 'scope_id': 'finite',
        'scope_basis': 'finite physical control', 'network_result': {'path': str(network), 'sha256': sha(network)}}
    input_path = tmp_path/'drift-consume-request.json'; write(input_path, request)
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'clone-valid.json', root)
    assert result.returncode == 0, result.stderr
    target = (clone/'skills/_qto-shared-evidence/scripts' if changed.startswith('qto_') else root)/changed
    original = target.read_bytes(); target.write_bytes(original+b'\n# deliberately altered runtime byte\n')
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'clone-drift.json', root)
    assert result.returncode == 2 and '字节' in result.stderr and not (tmp_path/'clone-drift.json').exists()
    target.write_bytes(original)
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'clone-restored.json', root)
    assert result.returncode == 0, result.stderr


def test_explicit_actual_insert_instances_consume_template_dimensions_individually(tmp_path):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    block = d.blocks.new('ANONYMOUS_TEMPLATE')
    axis = block.add_line((0, 0), (1, 0))
    dim = block.add_aligned_dim((0, 0), (1, 0), distance=1, text='4m'); dim.render()
    inserts = [d.modelspace().add_blockref('ANONYMOUS_TEMPLATE', (15+i*5, 0),
        dxfattribs={'xscale': 2}) for i in range(2)]
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    original = p['segments'][0]; p['segments'] = []
    for index, instance in enumerate(inserts):
        row = copy.deepcopy(original)
        row.update(record_id=str(index), physical_id='actual-'+str(index), extent_id='actual-extent-'+str(index),
            from_port_id='actual-port-'+str(index)+'-0', to_port_id='actual-port-'+str(index)+'-1',
            source_span={'path': [instance.dxf.handle, axis.dxf.handle]},
            length_facts=[{'method': 'dimension', 'annotation_path': [instance.dxf.handle, dim.dimension.dxf.handle],
                'level': 'confirmed', 'basis': 'native per-instance dimension; source literal is not multiplied by INSERT scale'}])
        p['segments'].append(row)
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'actual-instances')])
    assert len(out['quantity_items']) == 2 and out['length_summary'][0]['quantity'] == 8.
    assert all(r['quantity'] == 4. for r in out['quantity_items'])
    assert len({tuple(r['representations'][0]['source_span']['path']) for r in out['quantity_items']}) == 2


@pytest.mark.parametrize('nested', [False, True])
def test_text_ocs_anchor_then_complete_insert_chain_controls_view_scope(nested):
    d = ezdxf.new('R2010')
    if nested:
        block = d.blocks.new('TEXT_PLANE')
        text_entity = block.add_text('L=3m', dxfattribs={'insert': (0, 4, 0), 'extrusion': (0, 1, 0)})
        instance = d.modelspace().add_blockref('TEXT_PLANE', (10, 20, 30), dxfattribs={'rotation': 90})
        path = [instance.dxf.handle, text_entity.dxf.handle]
        expected = [10., 20., 34.]
    else:
        text_entity = d.modelspace().add_text('L=3m', dxfattribs={'insert': (0, 4, 0), 'extrusion': (0, 1, 0)})
        path = [text_entity.dxf.handle]
        expected = [0., 0., 4.]
    bounds = [expected[0]-.1, expected[1]-.1, expected[0]+.1, expected[1]+.1]
    assert Drawing(d).annotation(path, bounds)['location'] == pytest.approx(expected)
    # The raw OCS insert location is not a permissible substitute for world view scope.
    with pytest.raises(ValueError, match='范围'):
        Drawing(d).annotation(path, [-.1, 3.9, .1, 4.1])


def test_native_leader_inside_rotated_actual_insert_uses_world_arrow_and_full_path(tmp_path):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    block = d.blocks.new('ACTUAL_LEADER_BLOCK')
    axis = block.add_line((0, 0), (10, 0))
    label = block.add_text('L=7m', dxfattribs={'insert': (6, 3)})
    leader = block.add_leader([(5, 0), (5, 3), (6, 3)], dxfattribs={'annotation_handle': label.dxf.handle})
    instance = d.modelspace().add_blockref('ACTUAL_LEADER_BLOCK', (15, 0), dxfattribs={'rotation': 90})
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    row = p['segments'][0]; row['source_span'] = {'path': [instance.dxf.handle, axis.dxf.handle]}
    row['length_facts'] = [{'method': 'dimension', 'annotation_path': [instance.dxf.handle, label.dxf.handle],
        'leader_path': [instance.dxf.handle, leader.dxf.handle], 'level': 'confirmed', 'basis': 'native actual instance'}]
    result = measure(p)['segments'][0]
    assert result['quantity'] == 7
    assert result['measurements'][0]['source_annotation']['native_leader_binding']['arrow_page_point'] == pytest.approx([15, 5, 0])


@pytest.mark.parametrize('same_physical', [True, False])
def test_r2_same_physical_or_actual_port_pair_cannot_hide_conflict_in_different_systems(tmp_path, same_physical):
    a, _ = fixture(tmp_path, 'a'); b, _ = fixture(tmp_path, 'b')
    b['segments'][0]['system'] = 'drainage'
    if not same_physical:
        b['segments'][0].update(physical_id='different-name', extent_id='different-name')
    out, _ = chain(tmp_path, [candidate(tmp_path, a, 'one'), candidate(tmp_path, b, 'two')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] is None
    assert out['quantity_items'][0]['conflicting_systems'] == ['drainage', 'water']
    assert out['quantity_items'][0]['system'] is None


def test_r2_different_physical_pipes_and_actual_ports_in_different_systems_remain_separate(tmp_path):
    a, _ = fixture(tmp_path, 'a'); b, _ = fixture(tmp_path, 'b', physical='physical-b', ports=('Q0', 'Q1'))
    b['segments'][0]['system'] = 'drainage'
    out, _ = chain(tmp_path, [candidate(tmp_path, a, 'one'), candidate(tmp_path, b, 'two')])
    assert len(out['quantity_items']) == 2 and [r['quantity'] for r in out['quantity_items']] == [7., 7.]


@pytest.mark.parametrize('curve,blocked', [('arc', True), ('circle', True), ('ellipse', True),
    ('bulge', True), ('nested_nonuniform_arc', True), ('outside_sweep', False), ('other_z', False)])
def test_r2_native_leader_checks_curved_parts_exact_sweep_and_complete_world_frame(tmp_path, curve, blocked):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path); m = d.modelspace()
    label = m.add_text('L=7m', dxfattribs={'insert': (6, 3)})
    leader = m.add_leader([(5, 0), (5, 3), (6, 3)], dxfattribs={'annotation_handle': label.dxf.handle})
    if curve in {'arc', 'outside_sweep', 'other_z'}:
        m.add_arc((5, 5, 2 if curve == 'other_z' else 0), 5,
            start_angle=0 if curve == 'outside_sweep' else 180,
            end_angle=180 if curve == 'outside_sweep' else 360)
    elif curve == 'circle':
        m.add_circle((5, 5), 5)
    elif curve == 'ellipse':
        m.add_ellipse((5, 5), major_axis=(10, 0, 0), ratio=.5)
    elif curve == 'bulge':
        m.add_lwpolyline([(0, 5, 1), (10, 5, 0)], format='xyb')
    else:
        block = d.blocks.new('ANONYMOUS_CURVE'); block.add_arc((0, 5), 5, start_angle=180, end_angle=360)
        m.add_blockref('ANONYMOUS_CURVE', (5, 0), dxfattribs={'xscale': 2})
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    p['segments'][0]['length_facts'] = [{'method': 'dimension', 'annotation_path': [label.dxf.handle],
        'leader_path': [leader.dxf.handle], 'level': 'confirmed', 'basis': 'actual native attached leader'}]
    row = measure(p)['segments'][0]
    assert row['quantity'] is None if blocked else row['quantity'] == 7.


def test_r2_typed_same_named_distinct_source_declarations_are_not_common_datum(tmp_path):
    p, h = typed_fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    d.entitydb[h['datum-b'][0]].dxf.text = '设备独立高程基准 A'; d.saveas(path)
    p['drawing']['sha256'] = sha(path)
    fact = p['segments'][0]['length_facts'][0]
    fact['to']['datum_id'] = 'A'; fact['datum_transform_annotation_paths'] = []
    row = measure(p)['segments'][0]
    assert row['quantity'] is None and any('不同原源基准声明' in issue for issue in row['issues'])


def test_r2_typed_same_actual_source_declaration_remains_common_datum(tmp_path):
    p, _ = typed_fixture(tmp_path)
    fact = p['segments'][0]['length_facts'][0]
    fact['to'].update(datum_id='A', datum_annotation_path=fact['from']['datum_annotation_path'])
    fact['datum_transform_annotation_paths'] = []
    assert measure(p)['segments'][0]['quantity'] == pytest.approx(3.946)


def test_r2_same_byte_relocation_consumes_when_original_code_path_is_gone_and_still_rejects_drift(tmp_path):
    p, _ = fixture(tmp_path)
    first = tmp_path/'first-code'; second = tmp_path/'relocated-code'
    for root in (first, second):
        shutil.copytree(ROOT/'skills/qto-pipe-networks/scripts', root/'skills/qto-pipe-networks/scripts')
        shutil.copytree(ROOT/'skills/_qto-shared-evidence/scripts', root/'skills/_qto-shared-evidence/scripts')
    pipe = first/'skills/qto-pipe-networks/scripts'; relocated = second/'skills/qto-pipe-networks/scripts'
    source_request = tmp_path/'source-request.json'; write(source_request, p)
    measured = tmp_path/'measured.json'
    result = run('measure_pipe_diagram_lengths.py', source_request, measured, pipe)
    assert result.returncode == 0, result.stderr
    production = {'schema_version': PRODUCTION_REQUEST, 'scope_id': 'finite', 'network_id': 'N',
        'scope_basis': 'same byte relocation', 'candidate_results': [{'path': str(measured), 'sha256': sha(measured)}]}
    production_input = tmp_path/'production.json'; write(production_input, production)
    network = tmp_path/'network.json'
    result = run('produce_pipe_diagram_network.py', production_input, network, pipe)
    assert result.returncode == 0, result.stderr
    request = {'schema_version': CONSUME_REQUEST, 'scope_id': 'finite', 'network_id': 'N',
        'scope_basis': 'same source and same relative module bytes', 'network_result': {'path': str(network), 'sha256': sha(network)}}
    input_path = tmp_path/'consumer.json'; write(input_path, request)
    old_network = {'evidence_version': 'qto-pipe-network-inference-evidence/1.2', 'network_id': 'N', 'unit': 'm',
        'segments': [{'segment_id': key, 'system': 'water', 'dn': 'DN25', 'length': value}
            for key, value in [('left', 1.), ('right', 2.)]]}
    old_path = tmp_path/'old-network.json'; write(old_path, old_network)
    receipt_request = {'schema_version': 'qto-pipe-diagram-network-receipt-request/1.0', 'scope_id': 'finite',
        'scope_basis': 'hash-bound old reference only', 'network_id': 'N',
        'candidate_result': {'path': str(measured), 'sha256': sha(measured)},
        'network_result': {'path': str(old_path), 'sha256': sha(old_path)},
        'relationships': [{'record_id': 'A', 'from_network_record_id': 'segment:left', 'to_network_record_id': 'segment:right',
            'basis': 'source different physical extents', 'level': 'inferred',
            'evidence': [{**p['drawing'], 'locator': 'anonymous source physical interfaces'}],
            'coverage': {'disjoint_record_ids': ['segment:left', 'segment:right'], 'same_extent_record_ids': [],
                'partial_overlap_record_ids': [], 'unknown_record_ids': []}}]}
    receipt_input = tmp_path/'old-receipt-input.json'; write(receipt_input, receipt_request)
    first.rename(tmp_path/'retired-first-code')
    output = tmp_path/'relocated-quantity.json'
    result = run('consume_pipe_physical_network.py', input_path, output, relocated)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding='utf-8'))['quantity_items'][0]['quantity'] == 7.
    result = run('receive_pipe_diagram_lengths.py', receipt_input, tmp_path/'relocated-old-receipt.json', relocated)
    assert result.returncode == 0, result.stderr
    changed = relocated/'measure_pipe_diagram_lengths.py'; original = changed.read_bytes()
    changed.write_bytes(original+b'\n# deliberate relocated byte drift\n')
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'drift-must-not-exist.json', relocated)
    assert result.returncode == 2 and '字节' in result.stderr and not (tmp_path/'drift-must-not-exist.json').exists()
    changed.write_bytes(original)
    result = run('consume_pipe_physical_network.py', input_path, tmp_path/'restored.json', relocated)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('kind', ['line', 'nested', 'split'])
def test_si_workbench_complete_contract_reaches_t29_actual_cli(tmp_path, kind):
    c = si_workbench(tmp_path/'workbench', kind)
    p, _ = fixture(tmp_path, 'separate-seven', physical='separate-seven', ports=('Q0', 'Q1'))
    out, request = chain(tmp_path, [candidate(tmp_path, p, 'separate')], c['extra'])
    assert out['preserved_existing_network'] == c['result']
    assert out['business_adoption_allowed'] is False and out['formal_total_quantity'] is None
    produced = json.loads(Path(request['network_result']['path']).read_text(encoding='utf-8'))
    assert produced['legacy_replay']['adapter_mode'] == 'replayed_workbench_physical_SI'
    edges = [e for e in produced['physical_edges'] if e['origin'] == 'base']
    assert sum(e['measurements'][0]['replayed_edge_length_m'] for e in edges) == pytest.approx(10)
    for e in edges:
        fact = e['measurements'][0]; resolved = fact['source_resolution']
        assert resolved['whole_workbench_input_replayed'] is True
        assert resolved['full_instance_path'] == c['object']['source_path']
        assert resolved['public_unit_scope'] == c['scope']
        assert fact['source_unit_note']['text'] == '坐标单位：毫米'
        assert isinstance(fact['source_input_segment']['source_instance_path'], str)
        assert 'source_part_index' not in fact['source_input_segment']
    if kind == 'split':
        assert [(e['source_span']['part'], e['source_span']['from_fraction'], e['source_span']['to_fraction'])
            for e in edges] == [(0, 0., .5), (0, .5, 1.), (1, 0., 1.)]
        assert [e['measurements'][0]['replayed_edge_length_m'] for e in edges] == [2, 2, 6]
        # Existing producer cannot distribute the original single DN seed over
        # every derived part. Keep that existing uncertainty, including null.
        assert any(e['quantity'] is None for e in edges)
        assert out['unresolved_quantity_owners']
    else:
        assert sorted(r['quantity'] for r in out['quantity_items']) == [7, 10]
        assert sum(r['quantity'] for r in out['quantity_items']) == 17
    if kind != 'line':
        assert len(c['object']['source_path']) == 3
        bad = {'schema_version': PRODUCTION_REQUEST, 'network_id': 'network-a', 'scope_id': 'finite',
            'scope_basis': 'negative source range', 'candidate_results': [candidate(tmp_path, p, 'negative')], **copy.deepcopy(c['extra'])}
        bad['base_extent_bindings'][0]['source_span']['path'] = [c['object']['source_handle']]
        bp = tmp_path/'bare-leaf-request.json'; write(bp, bad)
        done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', bp, '--output', tmp_path/'bare-leaf-result.json')
        assert done.returncode == 2 and not (tmp_path/'bare-leaf-result.json').exists()
        if kind == 'split':
            bad['base_extent_bindings'] = copy.deepcopy(c['extra']['base_extent_bindings'])
            bad['base_extent_bindings'][0]['source_span']['to_fraction'] = .75
            write(bp, bad)
            done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', bp, '--output', tmp_path/'wrong-range-result.json')
            assert done.returncode == 2 and '分数' in done.stderr and not (tmp_path/'wrong-range-result.json').exists()


@pytest.mark.parametrize('conflict', [None, 'length', 'system', 'specification'])
def test_si_workbench_and_diagram_one_physical_outlet(tmp_path, conflict):
    c = si_workbench(tmp_path/'workbench')
    p, _ = fixture(tmp_path, 'same-object', physical='base-physical', ports=('B0', 'B1'),
        value='11m' if conflict == 'length' else '10m')
    p['segments'][0]['extent_id'] = 'base-extent-0'
    if conflict == 'system':
        p['segments'][0]['system'] = 'wastewater'
    if conflict == 'specification':
        p['segments'][0]['specification']['dn'] = 'DN32'
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'same')], c['extra'])
    assert len(out['quantity_items']) == 1
    row = out['quantity_items'][0]
    if conflict:
        assert row['quantity'] is None and out['unresolved_quantity_owners']
    else:
        assert row['quantity'] == 10 and row['deduplicated_representation_count'] == 1
        assert out['length_summary'][0]['quantity'] == 10


def test_si_workbench_fields_and_scope_cannot_be_stripped_or_changed(tmp_path):
    c = si_workbench(tmp_path/'workbench')
    p, _ = fixture(tmp_path, 'separate-seven', physical='separate-seven', ports=('Q0', 'Q1'))
    cand = candidate(tmp_path, p, 'separate')
    base = dict(schema_version=PRODUCTION_REQUEST, network_id='network-a', scope_id='finite',
        scope_basis='negative complete input replay', candidate_results=[cand], **c['extra'])
    mutations = ['workbench', 'geometric_unit_consumption', 'geometric_unit_evidence', 'dependency',
        'segment_ids', 'length', 'scope', 'credential_source', 'unit_note', 'outside_scope']
    for mutation in mutations:
        bad = copy.deepcopy(base); original = copy.deepcopy(c['input'])
        if mutation in ('workbench', 'geometric_unit_consumption', 'geometric_unit_evidence'):
            original.pop(mutation)
        elif mutation == 'dependency':
            original['geometric_unit_consumption']['workbench_production']['producer_dependencies'].popitem()
        elif mutation == 'segment_ids':
            original['segments'][0]['workbench_object_ids'] = []
            original['segments'][0]['source_instance_path'] = [c['object']['source_handle']]
            original['segments'][0].update(source_part_index=0, source_from_fraction=0., source_to_fraction=1.)
        elif mutation == 'length':
            original['segments'][0]['length'] = 12.
            original['segments'][0]['end'][0] = 12.
            original['segments'][0]['geometry']['parts'][0]['end'][0] = 12.
        elif mutation == 'scope':
            original['measurement_scope']['bounds']['xmin'] += 1
        elif mutation == 'credential_source':
            original['geometric_unit_evidence']['request']['coordinate_unit_refs'] = [dict(handle=c['bare'])]
        elif mutation == 'unit_note':
            bad['base_extent_bindings'][0]['coordinate_unit_annotation_path'] = [c['bare']]
        else:
            bad['base_extent_bindings'][0]['bounds'][0] -= 1
        if mutation not in ('unit_note', 'outside_scope'):
            ip = tmp_path/(mutation+'-input.json'); bad['network_input'] = write(ip, original)
        bp = tmp_path/(mutation+'-request.json'); write(bp, bad)
        output = tmp_path/(mutation+'-must-not-exist.json')
        done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', bp, '--output', output)
        assert done.returncode == 2 and not output.exists(), (mutation, done.stdout, done.stderr)
    # Original source bytes are authoritative even if a caller rehashes a
    # forged clause; the saved public credential and whole contract disagree.
    d = ezdxf.readfile(c['source']); d.entitydb[c['clause']].dxf.text = 'mm'; d.saveas(c['source'])
    bad = copy.deepcopy(base)
    bad['base_extent_bindings'][0]['drawing']['sha256'] = sha(c['source'])
    bp = tmp_path/'source-clause-request.json'; write(bp, bad)
    output = tmp_path/'source-clause-must-not-exist.json'
    done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', bp, '--output', output)
    assert done.returncode == 2 and not output.exists()


def test_si_workbench_byte_identical_code_relocation_and_nested_dependency_drift(tmp_path):
    c = si_workbench(tmp_path/'workbench')
    p, _ = fixture(tmp_path, 'separate-seven', physical='separate-seven', ports=('Q0', 'Q1'))
    out, request = chain(tmp_path, [candidate(tmp_path, p, 'separate')], c['extra'])
    assert sorted(r['quantity'] for r in out['quantity_items']) == [7, 10]
    moved = tmp_path/'relocated'; shutil.copytree(ROOT/'skills', moved/'skills',
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    script = moved/'skills/qto-pipe-networks/scripts/consume_pipe_physical_network.py'
    ip = tmp_path/'consume-same-code.json'; write(ip, request)
    done = si_cli(tmp_path, script, '--input', ip, '--output', tmp_path/'relocated-result.json')
    assert done.returncode == 0, done.stderr
    module = moved/'skills/_qto-shared-evidence/scripts/cad_native/reader.py'
    original = module.read_bytes(); module.write_bytes(original+b'\n# anonymous dependency byte drift\n')
    done = si_cli(tmp_path, script, '--input', ip, '--output', tmp_path/'drift-must-not-exist.json')
    assert done.returncode == 2 and not (tmp_path/'drift-must-not-exist.json').exists()
    module.write_bytes(original)
    done = si_cli(tmp_path, script, '--input', ip, '--output', tmp_path/'restored-result.json')
    assert done.returncode == 0, done.stderr
