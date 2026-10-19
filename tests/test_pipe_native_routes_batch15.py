"""Native directed polyline route dimensions: source/CLI controls, no project oracle."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

from test_pipe_physical_network_t29_batch13 import (PIPE, fixture, write, candidate, chain, run)
from measure_pipe_diagram_lengths import measure, sha, Drawing
from produce_pipe_diagram_network import produce, REQUEST as PRODUCTION_REQUEST
from pipe_diagram_source_parts import coverage_key, coverage_overlap


def route_fixture(tmp_path, *, nested=False, poly3d=False, points=None, values=None, cell=(0, 0)):
    tmp_path.mkdir(exist_ok=True)
    request, _ = fixture(tmp_path)
    doc = ezdxf.new('R2010'); doc.units = 0
    model = doc.modelspace()
    title = model.add_text('Anonymous native route system', dxfattribs={'insert': (0, -20)})
    owner = doc.blocks.new('ROUTE') if nested else model
    points = points or [(0, 0), (10, 0), (10, 8)]
    values = values or ['7m', '3m']
    route = owner.add_polyline3d([(*p[:2], p[2] if len(p) == 3 else 0) for p in points]) if poly3d else owner.add_lwpolyline(points)
    dimensions = []
    for index, (a, b) in enumerate(zip(points, points[1:])):
        dim = owner.add_aligned_dim(a[:2], b[:2], distance=2,
            text=values[index] if index < len(values) else '1m')
        dim.render(); dimensions.append(dim.dimension.dxf.handle)
    prefix = []
    if nested:
        outer = doc.blocks.new('OUTER')
        inner = outer.add_blockref('ROUTE', (11, 13), dxfattribs={'rotation': 31, 'xscale': 2., 'yscale': .7})
        instance = model.add_blockref('OUTER', (50, 100), dxfattribs={'rotation': -23,
            'row_count': 2, 'column_count': 2, 'row_spacing': 150, 'column_spacing': 150})
        prefix = [instance.dxf.handle+f'[{cell[0]},{cell[1]}]', inner.dxf.handle]
    source = tmp_path/'native-route.dxf'; doc.saveas(source)
    request['drawing'] = {'path': str(source), 'sha256': sha(source)}
    request['views'][0].update(title_paths=[[title.dxf.handle]], bounds=[-1000, -1000, 1000, 1000])
    row = request['segments'][0]
    row['source_span'] = dict(path=prefix+[route.dxf.handle], route_interval=dict(
        from_part=0, from_fraction=0., to_part=len(points)-2, to_fraction=1.))
    row['length_facts'] = [dict(method='dimension_chain', dimension_paths=[prefix+[h] for h in dimensions],
        level='confirmed', basis='native DIMENSION endpoint and axis at each directed source leg')]
    return request, dict(route=route.dxf.handle, dimensions=dimensions, prefix=prefix)


def edit_source(request, mutation):
    path = Path(request['drawing']['path']); doc = ezdxf.readfile(path)
    mutation(doc); doc.saveas(path); request['drawing']['sha256'] = sha(path)


@pytest.mark.parametrize('nested,poly3d,reverse', [(False, False, False), (False, True, False),
    (False, False, True), (True, False, False), (True, False, True)])
def test_directed_native_route_actual_measure_produce_consume(tmp_path, nested, poly3d, reverse):
    p, _ = route_fixture(tmp_path, nested=nested, poly3d=poly3d, cell=(1, 1))
    if reverse:
        p['segments'][0]['source_span']['route_interval'] = dict(from_part=1, from_fraction=1., to_part=0, to_fraction=0.)
        p['segments'][0].update(from_port_id='P1', to_port_id='P0')
    measured = measure(p)['segments'][0]
    assert measured['quantity'] == 10 and measured['quantity_level'] == '确认量'
    span = measured['source_span']
    assert span['physical_length_from_coordinates'] is False and len(span['source_parts']) == 2
    assert [leg['direction'] for leg in span['source_parts']] == ([-1, -1] if reverse else [1, 1])
    assert measured['specification']['material'] is None
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'route')])
    row = out['quantity_items'][0]
    assert row['quantity'] == 10 and row['quantity_level'] == '规则推算量'
    assert row['specification']['material'] is None and out['formal_total_quantity'] is None
    assert out['business_adoption_allowed'] is False and out['scope_complete'] is False
    assert out['preserved_existing_network'] is None
    assert len({r['quantity_owner'] for r in out['quantity_items']}) == 1


def test_finite_partial_range_sums_source_values_without_page_proportion(tmp_path):
    p, meta = route_fixture(tmp_path)
    edit_source(p, lambda doc: doc.entitydb[meta['dimensions'][0]].dxf.set('defpoint2', (5, 0, 0)))
    interval = p['segments'][0]['source_span']['route_interval']; interval['from_fraction'] = .5
    measured = measure(p)['segments'][0]
    assert measured['quantity'] == 10  # the source says 7m for this partial leg, not 3.5m.
    assert measured['source_span']['page_coordinate_length'] == 13
    proof = measured['measurements'][0]['source_dimension_chain_segments']
    assert proof[0]['source_from_fraction'] == .5


@pytest.mark.parametrize('defect', ['missing_leg', 'overlap', 'gap', 'projected', 'text', 'raw', 'wrong_end', 'cross_leg'])
def test_each_native_leg_requires_complete_unique_axis_bound_dimensions(tmp_path, defect):
    p, meta = route_fixture(tmp_path)
    fact = p['segments'][0]['length_facts'][0]
    if defect == 'missing_leg':
        fact['dimension_paths'].pop()
    else:
        def mutate(doc):
            m = doc.modelspace(); first = doc.entitydb[meta['dimensions'][0]]
            if defect == 'overlap':
                dim = m.add_aligned_dim((2, 0), (8, 0), distance=3, text='4m'); dim.render()
                fact['dimension_paths'].append([dim.dimension.dxf.handle])
            elif defect == 'gap':
                first.dxf.defpoint3 = (8, 0, 0)
            elif defect == 'projected':
                first.dxf.dimtype = 0; first.dxf.angle = 90
            elif defect == 'text':
                note = m.add_text('7m', dxfattribs={'insert': (0, 4)})
                fact['dimension_paths'][0] = [note.dxf.handle]
            elif defect == 'raw':
                first.dxf.text = '<>'
            elif defect == 'wrong_end':
                first.dxf.defpoint3 = (10, 2, 0)
            elif defect == 'cross_leg':
                first.dxf.defpoint3 = (10, 8, 0)
        edit_source(p, mutate)
    row = measure(p)['segments'][0]
    assert row['quantity'] is None and row['quantity_level'] == '未决量'
    out, _ = chain(tmp_path, [candidate(tmp_path, p, defect)])
    assert out['quantity_items'][0]['quantity'] is None
    assert out['length_summary'][0]['quantity'] is None


@pytest.mark.parametrize('defect', ['duplicate_entity', 'internal_branch', 'crossing', 'duplicate_parts',
    'self_crossing', 'curve', 'array_alias', 'array_cell_missing', 'zero_interval'])
def test_ambiguous_source_paths_cannot_be_repaired_by_selected_parts(tmp_path, defect):
    points = [(0, 0), (10, 0), (0, 0), (0, 8)] if defect == 'duplicate_parts' else (
        [(0, 0), (10, 10), (0, 10), (10, 0)] if defect == 'self_crossing' else None)
    p, meta = route_fixture(tmp_path, points=points, nested=defect.startswith('array'))
    if defect == 'array_alias':
        p['segments'][0]['source_span']['path'][0] = p['segments'][0]['source_span']['path'][0].replace('[0,0]', '[01,0]')
    elif defect == 'array_cell_missing':
        p['segments'][0]['source_span']['path'][0] = p['segments'][0]['source_span']['path'][0].split('[')[0]
    elif defect == 'zero_interval':
        p['segments'][0]['source_span']['route_interval'].update(to_part=0, to_fraction=0.)
    elif defect not in {'duplicate_parts', 'self_crossing'}:
        def mutate(doc):
            m = doc.modelspace()
            if defect == 'duplicate_entity': m.add_lwpolyline([(0, 0), (10, 0), (10, 8)])
            elif defect == 'internal_branch': m.add_line((10, 4), (14, 4))
            elif defect == 'crossing': m.add_line((5, -4), (5, 4))
            elif defect == 'curve': doc.entitydb[meta['route']].set_points([(0, 0, 1), (10, 0, 0), (10, 8, 0)], format='xyb')
        edit_source(p, mutate)
    with pytest.raises(ValueError): measure(p)


def test_selected_part_cannot_hide_other_retraced_native_part(tmp_path):
    p, _ = route_fixture(tmp_path, points=[(0, 0), (10, 0), (0, 0), (0, 8)])
    p['segments'][0]['source_span']['route_interval'].update(to_part=0, to_fraction=1.)
    with pytest.raises(ValueError, match='重复/重合'): measure(p)


def test_positive_native_detour_uses_all_legs_not_endpoint_chord(tmp_path):
    p, _ = route_fixture(tmp_path, points=[(0, 0), (10, 0), (10, 8), (2, 8)], values=['7m', '3m', '6m'])
    row = measure(p)['segments'][0]
    assert row['quantity'] == 16 and row['source_span']['page_coordinate_length'] == 26
    assert len(row['measurements'][0]['source_dimension_chain_segments']) == 3


def test_forward_reverse_route_deduplicates_complete_source_coverage(tmp_path):
    p, _ = route_fixture(tmp_path)
    q = copy.deepcopy(p)
    q['segments'][0]['source_span']['route_interval'] = dict(from_part=1, from_fraction=1., to_part=0, to_fraction=0.)
    q['segments'][0].update(from_port_id='P1', to_port_id='P0')
    a, b = measure(p)['segments'][0]['source_span'], measure(q)['segments'][0]['source_span']
    assert coverage_key(a) == coverage_key(b) and coverage_overlap(a, b)
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'forward'), candidate(tmp_path, q, 'reverse')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] == 10
    assert out['quantity_items'][0]['deduplicated_representation_count'] == 1


@pytest.mark.parametrize('same_extent', [False, True])
def test_new_route_and_legacy_single_part_share_the_same_parameter_conservation(tmp_path, same_extent):
    p, meta = route_fixture(tmp_path)
    q = copy.deepcopy(p)
    q['segments'][0]['source_span'] = dict(path=p['segments'][0]['source_span']['path'], part=0)
    q['segments'][0]['length_facts'] = [dict(method='dimension', annotation_path=[meta['dimensions'][0]],
        level='confirmed', basis='legacy native single-part dimension')]
    if same_extent:
        p['segments'][0]['source_span']['route_interval'].update(to_part=0, to_fraction=1.)
        p['segments'][0]['length_facts'][0]['dimension_paths'].pop()
    else:
        q['segments'][0].update(physical_id='other-physical', extent_id='other-extent', from_port_id='Q0', to_port_id='Q1')
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'new'), candidate(tmp_path, q, 'old')])
    assert len(out['quantity_items']) == 1
    assert out['quantity_items'][0]['quantity'] == (7 if same_extent else None)
    assert len(out['quantity_items'][0]['representations']) == 2


def test_cross_record_mixed_route_and_single_part_overlap_rejects_entire_candidate(tmp_path):
    p, _ = route_fixture(tmp_path)
    other = copy.deepcopy(p['segments'][0]); other.update(record_id='B', physical_id='B', extent_id='B', from_port_id='B0', to_port_id='B1')
    other['source_span'] = dict(path=other['source_span']['path'], part=1, from_fraction=.5, to_fraction=1.)
    p['segments'].append(other)
    with pytest.raises(ValueError, match='重复绑定'): measure(p)


@pytest.mark.parametrize('reverse', [False, True])
def test_part_fraction_literal_survives_new_old_representation_without_scalar_roundoff(tmp_path, reverse):
    p, meta = route_fixture(tmp_path)
    def mutate(doc):
        dimension = doc.entitydb[meta['dimensions'][1]]
        dimension.dxf.defpoint2 = (10, 1.6, 0); dimension.dxf.defpoint3 = (10, 6.4, 0)
    edit_source(p, mutate)
    row = p['segments'][0]
    row['source_span']['route_interval'] = dict(from_part=1, from_fraction=.2,
        to_part=1, to_fraction=.8)
    row['length_facts'][0]['dimension_paths'] = [[meta['dimensions'][1]]]
    q = copy.deepcopy(p)
    q['segments'][0]['source_span'] = dict(path=row['source_span']['path'], part=1,
        from_fraction=.2, to_fraction=.8)
    q['segments'][0]['length_facts'] = [dict(method='dimension', annotation_path=[meta['dimensions'][1]],
        level='confirmed', basis='same native partial dimension and literal source fractions')]
    if reverse:
        row['source_span']['route_interval'].update(from_fraction=.8, to_fraction=.2)
        row.update(from_port_id='P1', to_port_id='P0')
    new = measure(p)['segments'][0]['source_span']; old = measure(q)['segments'][0]['source_span']
    assert coverage_key(new) == coverage_key(old)
    assert new['source_parts'][0]['from_fraction'] == .2
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'partial-new'), candidate(tmp_path, q, 'partial-old')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] == 3


def test_single_ulp_distinct_route_parameter_must_not_be_tolerance_deduplicated(tmp_path):
    p, meta = route_fixture(tmp_path)
    def mutate(doc):
        dimension = doc.entitydb[meta['dimensions'][1]]
        dimension.dxf.defpoint2 = (10, 1.6, 0); dimension.dxf.defpoint3 = (10, 6.4, 0)
    edit_source(p, mutate)
    p['segments'][0]['source_span']['route_interval'] = dict(from_part=1, from_fraction=.2, to_part=1, to_fraction=.8)
    p['segments'][0]['length_facts'][0]['dimension_paths'] = [[meta['dimensions'][1]]]
    q = copy.deepcopy(p)
    q['segments'][0]['source_span'] = dict(path=p['segments'][0]['source_span']['path'], part=1,
        from_fraction=math.nextafter(.2, 1.), to_fraction=.8)
    q['segments'][0]['length_facts'] = [dict(method='dimension', annotation_path=[meta['dimensions'][1]],
        level='confirmed', basis='same native dimension but a genuinely different source interval')]
    a = measure(p)['segments'][0]['source_span']; b = measure(q)['segments'][0]['source_span']
    assert coverage_key(a) != coverage_key(b) and coverage_overlap(a, b)
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'literal'), candidate(tmp_path, q, 'one-ulp-different')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] is None


def test_partially_overlapping_routes_in_separate_packages_stay_null(tmp_path):
    p, meta = route_fixture(tmp_path)
    q = copy.deepcopy(p); q['segments'][0]['source_span']['route_interval'].update(from_part=1, from_fraction=0.)
    q['segments'][0]['length_facts'][0]['dimension_paths'] = [[meta['dimensions'][1]]]
    q['segments'][0].update(physical_id='part-b', extent_id='part-b', from_port_id='B0', to_port_id='B1')
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'full'), candidate(tmp_path, q, 'partial')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] is None


def test_equal_length_equal_page_ports_but_distinct_native_routes_are_not_equivalent_views(tmp_path):
    p, _ = route_fixture(tmp_path)
    q = copy.deepcopy(p)
    def mutate(doc):
        m = doc.modelspace(); route = m.add_lwpolyline([(0, 0), (0, 8), (10, 8)])
        dims = []
        for a, b, value in [((0, 0), (0, 8), '3m'), ((0, 8), (10, 8), '7m')]:
            dim = m.add_aligned_dim(a, b, distance=2, text=value); dim.render(); dims.append([dim.dimension.dxf.handle])
        q['segments'][0]['source_span']['path'] = [route.dxf.handle]
        q['segments'][0]['length_facts'][0]['dimension_paths'] = dims
    edit_source(p, mutate); q['drawing'] = copy.deepcopy(p['drawing'])
    assert measure(p)['segments'][0]['quantity'] == measure(q)['segments'][0]['quantity'] == 10
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'east-route'), candidate(tmp_path, q, 'west-route')])
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] is None


def test_one_native_leg_can_have_multiple_ordered_dimensions(tmp_path):
    p, meta = route_fixture(tmp_path)
    def mutate(doc):
        first = doc.entitydb[meta['dimensions'][0]]; first.dxf.defpoint3 = (4, 0, 0); first.dxf.text = '2m'
        dim = doc.modelspace().add_aligned_dim((4, 0), (10, 0), distance=3, text='5m'); dim.render()
        # Intentionally unordered input; replay sorts by the original route leg parameters.
        p['segments'][0]['length_facts'][0]['dimension_paths'].insert(0, [dim.dimension.dxf.handle])
    edit_source(p, mutate)
    row = measure(p)['segments'][0]
    assert row['quantity'] == 10
    pieces = row['measurements'][0]['source_dimension_chain_segments']
    assert [(piece['route_leg'], piece['from_fraction'], piece['to_fraction']) for piece in pieces] == [(0, 0., .4), (0, .4, 1.), (1, 0., 1.)]


def test_route_cannot_skip_between_disconnected_native_entities(tmp_path):
    p, _ = route_fixture(tmp_path)
    def mutate(doc):
        disconnected = doc.modelspace().add_line((100, 0), (110, 0))
        p['segments'][0]['source_span']['route_interval']['source_paths'] = [[disconnected.dxf.handle]]
    edit_source(p, mutate)
    with pytest.raises(ValueError, match='明确有向起终'): measure(p)


def test_inapplicable_multileg_dimension_method_stays_null(tmp_path):
    p, meta = route_fixture(tmp_path)
    p['segments'][0]['length_facts'] = [dict(method='dimension', annotation_path=[meta['dimensions'][0]],
        level='confirmed', basis='one dimension does not cover a multi-part route')]
    row = measure(p)['segments'][0]
    assert row['quantity'] is None and '逐腿原生尺寸链' in row['issues'][0]


def test_distinct_nested_array_cells_are_individual_physical_owners(tmp_path):
    p, meta = route_fixture(tmp_path, nested=True)
    q = copy.deepcopy(p)
    q['segments'][0]['source_span']['path'][0] = meta['prefix'][0].replace('[0,0]', '[1,1]')
    q['segments'][0]['length_facts'][0]['dimension_paths'] = [[q['segments'][0]['source_span']['path'][0], *path[1:]] for path in q['segments'][0]['length_facts'][0]['dimension_paths']]
    q['segments'][0].update(physical_id='cell11', extent_id='cell11', from_port_id='C0', to_port_id='C1')
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'cell00'), candidate(tmp_path, q, 'cell11')])
    assert len(out['quantity_items']) == 2 and out['length_summary'][0]['quantity'] == 20
    assert len({r['quantity_owner'] for r in out['quantity_items']}) == 2


@pytest.mark.parametrize('kind', ['scope', 'source', 'dependency'])
def test_source_scope_or_dependency_drift_rejects_stale_native_route(tmp_path, kind):
    p, _ = route_fixture(tmp_path)
    binding = candidate(tmp_path, p, 'stale')
    saved = json.loads(Path(binding['path']).read_text(encoding='utf-8'))
    if kind == 'source':
        edit_source(p, lambda doc: doc.modelspace().add_text('changed source', dxfattribs={'insert': (0, 30)}))
    elif kind == 'scope':
        request_path = Path(saved['source_request']['path']); original = json.loads(request_path.read_text(encoding='utf-8'))
        original['views'][0]['bounds'][2] -= 1; write(request_path, original)
        saved['source_request']['sha256'] = sha(request_path); write(Path(binding['path']), saved); binding['sha256'] = sha(binding['path'])
    else:
        saved['runtime_dependencies'][1]['sha256'] = '0'*64
        write(Path(binding['path']), saved); binding['sha256'] = sha(binding['path'])
    data = dict(schema_version=PRODUCTION_REQUEST, scope_id='finite', scope_basis='source native route',
        network_id='network-a', candidate_results=[binding])
    with pytest.raises(ValueError): produce(data)


def test_native_route_all_cli_outputs_are_hashseed_deterministic(tmp_path):
    p, _ = route_fixture(tmp_path)
    binding = candidate(tmp_path, p, 'hashseed')
    chain(tmp_path, [binding])
    for stage, script, request_path in [('measure', 'measure_pipe_diagram_lengths.py', tmp_path/'hashseed-request.json'),
        ('produce', 'produce_pipe_diagram_network.py', tmp_path/'production-request.json'),
        ('consume', 'consume_pipe_physical_network.py', tmp_path/'consume-request.json')]:
        outputs = []
        for seed in ('1', '87', '999'):
            output = tmp_path/f'{stage}-seed-{seed}.json'
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
            command = [sys.executable, '-B', str(PIPE/script), '--input', str(request_path), '--output', str(output)]
            result = subprocess.run(command, capture_output=True, encoding='utf-8', env=env)
            assert result.returncode == 0, result.stderr
            outputs.append(output.read_bytes())
            with (tmp_path/'hashseed-cli.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(stage=stage, seed=seed, command=command, actual_exit_code=result.returncode,
                    stdout=result.stdout, stderr=result.stderr), ensure_ascii=False)+'\n')
        assert outputs[0] == outputs[1] == outputs[2]
