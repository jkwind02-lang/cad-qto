"""Current workbench arc/array source adaptation, anonymous actual CLI controls."""
import copy
import json
import math
from pathlib import Path
import shutil
import sys

import ezdxf
import pytest

from test_pipe_physical_network_t29_batch13 import (ROOT, PIPE, si_cli, write,
    fixture, candidate, chain, PRODUCTION_REQUEST, CONSUME_REQUEST)
from measure_pipe_diagram_lengths import Drawing, sha, measure
from pipe_diagram_source_parts import unique_workbench_interval, scaled_part
from qto_cad_geometry import measure_part

def curve_workbench(tmp_path, kind='arc', array_issue=None, array_grid=None, duplicate_insert=False):
    """Use the existing public SI, index, apply and infer producers unchanged."""
    tmp_path.mkdir(exist_ok=True)
    shared = ROOT/'skills/_qto-shared-evidence/scripts'
    d = ezdxf.new('R2010'); d.units = 0; d.layers.new('ROUTE'); m = d.modelspace()
    if kind == 'arc':
        route = m.add_arc((0, 0), 4000, 0, 120, dxfattribs={'layer': 'ROUTE'})
    else:
        inner = d.blocks.new('INNER')
        if kind == 'native_ellipse':
            route = inner.add_ellipse((0, 0), major_axis=(5000, 0, 0), ratio=.2, start_param=0., end_param=2.2)
        elif kind in ('bulge_split', 'negative_bulge_split'):
            route = inner.add_lwpolyline([(0, 0, -1 if kind == 'negative_bulge_split' else 1), (4000, 0, 0), (4000, 6000, 0)], format='xyb')
        elif kind == 'array':
            route = inner.add_line((0, 0), (10000, 0))
        else:
            route = inner.add_arc((0, 0), 5000, 0., math.degrees(2.2))
        outer = d.blocks.new('OUTER')
        nested = outer.add_blockref('INNER', (1000, 0), dxfattribs=({'xscale': 1.7, 'yscale': .16} if kind == 'nonuniform' else {'yscale': .2} if kind == 'ellipse_split' else {}))
        instance = m.add_blockref('OUTER', (2000, 0), dxfattribs={'rotation': 30} if kind == 'array' else {})
        if kind == 'array':
            instance.dxf.row_count = 2
            instance.dxf.row_spacing = -10000
            instance.dxf.column_count = 2
            instance.dxf.column_spacing = 12000
            if array_grid is not None:
                instance.dxf.row_spacing, instance.dxf.column_spacing = array_grid
            if duplicate_insert:
                m.add_entity(instance.copy())
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
    if array_issue == 'opaque':
        inner.new_entity('TOLERANCE', {})
    elif array_issue == 'conflict':
        inner.add_text('坐标单位：米', dxfattribs={'insert': (1000, 1000), 'height': 100})
    source = tmp_path/'anonymous-physical-source.dxf'; d.saveas(source)
    neutral = tmp_path/'neutral'
    done = si_cli(tmp_path, shared/'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', neutral)
    assert done.returncode in (0, 3), done.stderr
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
    if array_issue:
        assert done.returncode == 2 and cp.exists(), (done.stdout, done.stderr)
        rejected = json.loads(cp.read_text(encoding='utf-8-sig'))
        assert rejected['status'] == 'UNIT_UNRESOLVED' and rejected['allows_length_measurement'] is False
        assert rejected['raw_unit'] is None
        search = rejected['unit_text_search']
        assert search['status'] == ('UNRESOLVED' if array_issue == 'opaque' else 'CONFLICT')
        assert len(search['checked_array_cells']) == 4
        assert len(search['uninspected_active_text_carriers'] if array_issue == 'opaque' else search['unit_text_matches']) == 4
        assert sha(source) == mf['exchange_dxf']['sha256']
        return dict(unit_rejection=done, source=source, source_sha256=sha(source))
    assert done.returncode == 0, done.stderr
    credential = json.loads(cp.read_text(encoding='utf-8'))
    sr = tmp_path/'source.json'; write(sr, mf['exchange_dxf'])
    ix = tmp_path/'index.json'
    done = si_cli(tmp_path, shared/'qto_recognition_workbench.py', 'index', '--source', sr,
        '--bounds', *bounds, '--output', ix)
    assert done.returncode == 0, done.stderr
    index = json.loads(ix.read_text(encoding='utf-8'))
    route_objects = [o for o in index['objects'] if o.get('source_handle') == route.dxf.handle]
    obj = route_objects[0]
    note = next(o for o in index['objects'] if o.get('source_handle') == dn.dxf.handle)
    objects = [o['object_id'] for o in route_objects]+[note['object_id']]
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
        assignments=[dict(adjudication_id='PIPE', object_ids=[o['object_id'] for o in route_objects], role='pipe_centerline', system='water', observation='source centerline')],
        relations=[dict(relation_id='DN', kind='dn_annotation', target_object_id=oid, annotation_object_id=nid,
            annotation_text='DN25', dn='DN25', adjudication_id='PIPE', observation='actual DN text'),
            dict(relation_id='ROOT', kind='root', target=dict(object_id=oid, end='start'), adjudication_id='PIPE', observation='source start'),
            dict(relation_id='END', kind='terminal', target=dict(object_id=oid, end='end'), terminal_type='source physical port',
                port_dn='DN25', port_dn_annotation_object_ids=[nid], adjudication_id='PIPE', observation='source end')],
        endpoint_review=dict(complete=False, basis='only selected branch; other objects retained'))
    for extra_object in route_objects[1:]:
        for relation in copy.deepcopy(ops['relations'][:3]):
            relation['relation_id'] += ':'+extra_object['object_id']
            if 'target_object_id' in relation:
                relation['target_object_id'] = extra_object['object_id']
            else:
                relation['target']['object_id'] = extra_object['object_id']
            ops['relations'].append(relation)
    if kind in ('ellipse_split', 'bulge_split', 'negative_bulge_split'):
        ops['relations'].append(dict(relation_id='CUT', kind='scope_boundary',
            target=dict(object_id=oid, part_index=0, parameter=.25), adjudication_id='PIPE', observation='source explicit internal boundary'))
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
    ranges = ([(0, 0., .25), (0, .25, 1.)] + ([(1, 0., 1.)] if kind in ('bulge_split', 'negative_bulge_split') else [])) if kind in ('ellipse_split', 'bulge_split', 'negative_bulge_split') else [(0, 0., 1.)]*len(route_objects)
    assert len(result['segments']) == len(ranges)
    for i, (item, (part, first, last)) in enumerate(zip(result['segments'], ranges)):
        parent = item.get('parent_segment_id', item['segment_id'])
        raw = next(r for r in prepared['segments'] if r['segment_id'] == parent)
        source_object = next(o for o in route_objects if o['object_id'] == raw['workbench_object_ids'][0])
        mappings.append(dict(segment_id=item['segment_id'], physical_id='base-physical-'+str(i) if kind == 'array' else 'base-physical', extent_id='base-extent-'+str(i),
            from_port_id='B'+str(i)+'-0' if kind == 'array' else 'B'+str(i), to_port_id='B'+str(i)+'-1' if kind == 'array' else 'B'+str(i+1), drawing=mf['exchange_dxf'], bounds=bounds,
            source_span=dict(path=source_object['source_path'], part=part, from_fraction=first, to_fraction=last),
            coordinate_unit_annotation_path=[clause.dxf.handle], specification=dict(dn=item['dn'], material=None),
            recognition_basis=dict(kind='model_working_interpretation', recognized_by='anonymous model control', review_status='unreviewed'),
            basis='actual index path, unchanged producer contract and source part boundary'))
    extra = dict(network_input=dict(path=str(ip), sha256=sha(ip)),
        network_result=dict(path=str(result_path), sha256=sha(result_path)), base_extent_bindings=mappings)
    return dict(extra=extra, input=prepared, result=result, index=index, object=obj, route_objects=route_objects, input_path=ip,
        source=source, credential=credential, clause=clause.dxf.handle, bare=bare.dxf.handle, scope=scope)


def ellipse_integral(a, b, start, end, n=20000):
    # Independent composite Simpson check; a parameter fraction is not an arc-length fraction.
    step = (end-start)/n
    speed = lambda t: math.hypot(a*math.sin(t), b*math.cos(t))
    return step/3*(speed(start)+speed(end)+sum((4 if i % 2 else 2)*speed(start+i*step) for i in range(1, n)))


@pytest.mark.parametrize('kind', ['arc', 'native_ellipse', 'ellipse_split', 'bulge_split', 'negative_bulge_split', 'nonuniform', 'array'])
def test_current_workbench_fresh_cli_curve_and_array_reach_unique_length_outlet(tmp_path, kind):
    c = curve_workbench(tmp_path/'source', kind)
    p, _ = fixture(tmp_path, 'separate', physical='separate', ports=('Q0', 'Q1'))
    out, request = chain(tmp_path, [candidate(tmp_path, p, 'separate')], c['extra'])
    produced = json.loads(Path(request['network_result']['path']).read_text(encoding='utf-8'))
    base = [r for r in produced['physical_edges'] if r['origin'] == 'base']
    actual = sum(r['measurements'][0]['replayed_edge_length_m'] for r in base)
    expected = {'arc': 4*2*math.pi/3, 'ellipse_split': ellipse_integral(5, 1, 0, 2.2),
        'native_ellipse': ellipse_integral(5, 1, 0, 2.2), 'bulge_split': 2*math.pi+6, 'negative_bulge_split': 2*math.pi+6, 'nonuniform': ellipse_integral(8.5, .8, 0, 2.2), 'array': 40}[kind]
    assert actual == pytest.approx(expected, rel=1e-8)
    assert out['preserved_existing_network'] == c['result']
    assert out['formal_total_quantity'] is None and out['scope_complete'] is False
    for row in base:
        span = row['source_span']; resolved = row['measurements'][0]['source_resolution']
        assert resolved['whole_workbench_input_replayed'] is True
        assert resolved['full_instance_path'] == span['path']
        assert resolved['part_index'] == span['part']
        assert row['specification']['dn'] == next(r['dn'] for r in c['result']['segments'] if r['segment_id'] == row['record_id'].removeprefix('base:'))
        assert span['physical_length_from_coordinates'] is False
    if kind == 'array':
        assert len(base) == 4 and [r['quantity'] for r in base] == pytest.approx([10]*4)
        assert len({tuple(r['source_span']['path']) for r in base}) == 4
        assert {r['source_span']['path'][0].split('[')[1] for r in base} == {'0,0]', '0,1]', '1,0]', '1,1]'}
    if kind == 'ellipse_split':
        assert [(r['source_span']['from_fraction'], r['source_span']['to_fraction']) for r in base] == [(0, .25), (.25, 1)]
        assert base[0]['measurements'][0]['replayed_edge_length_m'] == pytest.approx(ellipse_integral(5, 1, 0, .55), rel=1e-8)
        assert not math.isclose(base[0]['measurements'][0]['replayed_edge_length_m'], expected*.25, rel_tol=.01)
        # The old DN assignment's unresolved derived intervals stay unresolved.
        assert any(r['quantity'] is None for r in base)


@pytest.mark.parametrize('mutation', ['wrong_array_cell', 'bare_array_handle', 'out_of_range', 'wrong_fraction', 'reverse_fraction', 'wrong_part', 'missing_si'])
def test_current_curve_array_false_equal_length_or_interval_mapping_is_refused_actual_cli(tmp_path, mutation):
    kind = 'array' if 'array' in mutation or mutation == 'out_of_range' else 'ellipse_split'
    c = curve_workbench(tmp_path/'source', kind)
    p, _ = fixture(tmp_path, 'separate', physical='separate', ports=('Q0', 'Q1'))
    request = dict(schema_version=PRODUCTION_REQUEST, network_id='network-a', scope_id='finite',
        scope_basis='source interval counterexample', candidate_results=[candidate(tmp_path, p, 'separate')], **copy.deepcopy(c['extra']))
    span = request['base_extent_bindings'][0]['source_span']
    if mutation == 'wrong_array_cell':
        span['path'][0] = span['path'][0].replace('[0,0]', '[1,0]')
    elif mutation == 'bare_array_handle':
        span['path'][0] = span['path'][0].split('[')[0]
    elif mutation == 'out_of_range':
        span['path'][0] = span['path'][0].replace('[0,0]', '[2,0]')
    elif mutation == 'wrong_fraction':
        span['to_fraction'] = .5
    elif mutation == 'reverse_fraction':
        span['from_fraction'], span['to_fraction'] = span['to_fraction'], span['from_fraction']
    elif mutation == 'missing_si':
        changed = copy.deepcopy(c['input']); changed.pop('geometric_unit_evidence')
        request['network_input'] = write(tmp_path/'without-si-original-input.json', changed)
    else:
        span['part'] = 1
    ip = tmp_path/'bad-request.json'; write(ip, request)
    target = tmp_path/'must-not-exist.json'
    done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', ip, '--output', target)
    assert done.returncode == 2 and not target.exists(), done.stderr


def test_overlapping_same_curve_parts_ambiguous_and_chord_or_arc_length_ratio_are_not_source_intervals():
    part = dict(kind='ellipse_arc', center=[0, 0, 0], u=[10, 0, 0], v=[0, 1, 0], start_param=0., sweep_param=2.)
    child = dict(part, start_param=.5, sweep_param=1.)
    with pytest.raises(ValueError, match='唯一'):
        unique_workbench_interval([part, copy.deepcopy(part)], child, 1., 1.)
    (index, start, end), _ = unique_workbench_interval([part], child, 1., 1.)
    assert (index, start, end) == (0, .25, .75)
    wrong = dict(child, center=[0, 2, 0])
    with pytest.raises(ValueError, match='唯一'):
        unique_workbench_interval([part], wrong, 1., 1.)


def test_curve_extrema_outside_scope_and_linear_dimension_chord_do_not_create_length(tmp_path):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path)
    arc = d.modelspace().add_arc((5, 0), 5, 0, 180)
    dim = d.modelspace().add_aligned_dim((10, 0), (0, 0), distance=-1, text='10m'); dim.render()
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    row = p['segments'][0]; row['source_span'] = dict(path=[arc.dxf.handle])
    row['length_facts'] = [dict(method='dimension', annotation_path=[dim.dimension.dxf.handle], level='confirmed', basis='native chord counterexample')]
    result = measure(p)['segments'][0]
    assert result['quantity'] is None and '弦线' in result['issues'][0]
    with pytest.raises(ValueError, match='范围'):
        Drawing(d).span(dict(path=[arc.dxf.handle]), [-1, -1, 11, 1])


@pytest.mark.parametrize('case', ['legal', 'crossing_curve', 'chord_arrow', 'partial', 'unattached'])
def test_native_attached_curved_part_literal_route_length_actual_cli(tmp_path, case):
    p, _ = fixture(tmp_path)
    path = Path(p['drawing']['path']); d = ezdxf.readfile(path); m = d.modelspace()
    arc = m.add_arc((5, 0), 5, 0, 180)
    text = m.add_text('L=7.25m', dxfattribs={'insert': (7, 8)})
    arrow = (5, 0) if case == 'chord_arrow' else (5, 5)
    leader = m.add_leader([arrow, (5, 8), (7, 8)], dxfattribs={'annotation_handle': text.dxf.handle})
    if case == 'unattached':
        leader.dxf.annotation_handle = '0'
    elif case == 'crossing_curve':
        m.add_arc((10, 5), 5, 90, 270)
    d.saveas(path); p['drawing']['sha256'] = sha(path)
    row = p['segments'][0]; row['source_span'] = dict(path=[arc.dxf.handle])
    if case == 'partial':
        row['source_span'].update(from_fraction=.1, to_fraction=.9)
    row['length_facts'] = [dict(method='dimension', annotation_path=[text.dxf.handle], leader_path=[leader.dxf.handle],
        level='confirmed', basis='native attached curve length label, source is schematic and raw coordinates are not SI')]
    result = measure(p)['segments'][0]
    if case != 'legal':
        assert result['quantity'] is None
    else:
        assert result['quantity'] == 7.25
        native = result['measurements'][0]['source_annotation']['native_leader_binding']
        assert native['source_part_fraction'] == pytest.approx(.5)
        assert native['semantic_scope'] == 'complete_source_curved_part'
        out, _ = chain(tmp_path, [candidate(tmp_path, p, 'curved-native-leader')])
        assert out['quantity_items'][0]['quantity'] == 7.25


def test_array_cell_alias_and_fake_cell_on_regular_insert_are_rejected():
    d = ezdxf.new('R2010'); block = d.blocks.new('CELL'); line = block.add_line((0, 0), (10, 0))
    instance = d.modelspace().add_blockref('CELL', (0, 0)); path = [instance.dxf.handle, line.dxf.handle]
    with pytest.raises(ValueError, match='普通INSERT'):
        Drawing(d).resolve([path[0]+'[0,0]', path[1]])
    instance.dxf.row_count = 2
    with pytest.raises(ValueError, match='规范'):
        Drawing(d).resolve([path[0]+'[01,0]', path[1]])
    with pytest.raises(ValueError, match='格式'):
        Drawing(d).resolve([path[0]+'[-1,0]', path[1]])
    with pytest.raises(ValueError, match='格式'):
        Drawing(d).resolve([path[0]+'[0,no]', path[1]])


@pytest.mark.parametrize('issue', ['opaque', 'conflict'])
def test_array_si_carriers_must_be_fully_inspected_and_consistent_actual_cli(tmp_path, issue):
    case = curve_workbench(tmp_path/'source', 'array', array_issue=issue)
    assert case['unit_rejection'].returncode == 2
    assert '单位' in case['unit_rejection'].stderr
