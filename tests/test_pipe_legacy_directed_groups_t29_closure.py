"""Anonymous, actual CLI closure of directed legacy native-part groups."""
import copy
import json
import math
from pathlib import Path
import sys

import ezdxf
import pytest

from test_pipe_native_routes_batch15 import route_fixture, edit_source
import test_pipe_physical_network_t29_batch13 as controls
from test_pipe_physical_network_t29_batch13 import (PIPE, si_workbench, si_cli,
    fixture, write, run, candidate, chain, PRODUCTION_REQUEST)
from measure_pipe_diagram_lengths import Drawing, sha
from qto_cad_geometry import entity_geometry, transform_geometry
from qto_pipe_topology import route_geometry


@pytest.fixture(autouse=True)
def record_actual_cli_commands(monkeypatch, tmp_path):
    original = controls.run
    def logged(name, source, target, root=PIPE):
        result = original(name, source, target, root)
        with (tmp_path/'si-cli.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(command=[sys.executable, '-B', str(root/name),
                '--input', str(source), '--output', str(target)], actual_exit_code=result.returncode,
                stdout=result.stdout, stderr=result.stderr), ensure_ascii=False)+'\n')
        return result
    monkeypatch.setattr(controls, 'run', logged)


def legacy(tmp_path, reverse=False, nested=False, partial=False, parent_reverse=False):
    p, meta = route_fixture(tmp_path, nested=nested, values=['.01m', '.008m'])
    path = Path(p['drawing']['path']); doc = ezdxf.readfile(path)
    unit = doc.modelspace().add_text('绘图坐标单位mm', dxfattribs={'insert': (-100, -100)})
    doc.saveas(path); p['drawing']['sha256'] = sha(path)
    drawing = Drawing(doc); span = p['segments'][0]['source_span']
    entity, matrix = drawing.resolve(span['path'])
    parts = transform_geometry(entity_geometry(entity)['geometry'], matrix)['parts']
    raw_parts = [dict(kind='line', start=[v*.001 for v in x['start']],
        end=[v*.001 for v in x['end']]) for x in parts]
    if parent_reverse:
        raw_parts = [dict(kind='line', start=x['end'], end=x['start']) for x in raw_parts[::-1]]
    original = dict(schema_version='qto-pipe-network-inference-input/1.0', network_id='network-a', unit='m',
        parameters=dict(snap_tolerance=.0000001, gap_max=.0001),
        segments=[dict(segment_id='parent', system='water', start=raw_parts[0]['start'], end=raw_parts[-1]['end'],
            geometry=route_geometry(raw_parts), source_ids=['anonymous-source'], source_instance_path=span['path'],
            source_drawing=copy.deepcopy(p['drawing']),
            source_route_interval=copy.deepcopy(span['route_interval']), dn_seeds=[dict(dn='DN25',
                evidence_level='confirmed', source_id='source-dn', applies_to_entire_object=True,
                scope_source_id='anonymous-entire-parent-dn-proof')])])
    if parent_reverse:
        original['segments'][0]['source_route_interval'] = dict(
            from_part=1, from_fraction=1., to_part=0, to_fraction=0.)
    if partial:
        original['scope_boundary_nodes'] = [dict(boundary_id='CUT', segment_id='parent', part_index=0,
            parameter=.5, source_ids=['source-cut'], source_id='source-cut', system='water', evidence_level='confirmed',
            basis='actual explicit source parameter boundary')]
    ip = tmp_path/'legacy-input.json'; write(ip, original)
    op = tmp_path/'legacy-result.json'; done = si_cli(tmp_path, PIPE/'infer_pipe_network.py', '--input', ip, '--output', op)
    assert done.returncode == 0, done.stderr
    saved = json.loads(op.read_text(encoding='utf-8'))
    ids = [r['segment_id'] for r in saved['segments']]
    group = dict(segment_ids=ids[::-1] if reverse else ids, **{k: copy.deepcopy(p['segments'][0][k]) for k in
        ('physical_id', 'extent_id', 'from_port_id', 'to_port_id', 'specification', 'recognition_basis')},
        drawing=p['drawing'], bounds=p['views'][0]['bounds'], source_span=copy.deepcopy(span),
        coordinate_unit_annotation_path=[unit.dxf.handle], basis='same native directed parent chain')
    group['source_span']['route_interval'] = copy.deepcopy(original['segments'][0]['source_route_interval'])
    if parent_reverse:
        group['from_port_id'], group['to_port_id'] = group['to_port_id'], group['from_port_id']
    if reverse:
        r = group['source_span']['route_interval']
        group['source_span']['route_interval'] = dict(from_part=r['to_part'], from_fraction=r['to_fraction'],
            to_part=r['from_part'], to_fraction=r['from_fraction'])
        group['from_port_id'], group['to_port_id'] = group['to_port_id'], group['from_port_id']
    extra = dict(network_input=dict(path=str(ip), sha256=sha(ip)), network_result=dict(path=str(op), sha256=sha(op)),
        base_extent_bindings=[group])
    return p, meta, original, saved, extra


@pytest.mark.parametrize('reverse,partial,parent_reverse', [(False, False, False), (True, False, False),
    (False, True, False), (True, True, False), (False, False, True), (True, True, True)])
def test_legacy_group_real_cli_conserves_full_network_and_unique_owner(tmp_path, reverse, partial, parent_reverse):
    p, _, _, saved, extra = legacy(tmp_path, reverse=reverse, partial=partial, parent_reverse=parent_reverse)
    out, request = chain(tmp_path, [candidate(tmp_path, p, 'diagram')], extra)
    assert out['preserved_existing_network'] == saved
    assert len(out['quantity_items']) == 1
    row = out['quantity_items'][0]
    assert row['quantity'] == pytest.approx(.018) and row['quantity_level'] == '规则推算量'
    assert row['deduplicated_representation_count'] == 1
    base = next(r for r in row['representations'] if r['origin'] == 'base')
    proof = base['measurements'][0]['source_resolution']
    assert proof['replayed_segment_ids'] == extra['base_extent_bindings'][0]['segment_ids']
    assert proof['whole_parent_source_geometry_replayed'] is True
    assert len(proof['parent_geometry_intervals']) == (3 if partial else 2)
    assert out['formal_total_quantity'] is None and out['scope_complete'] is False
    assert out['business_adoption_allowed'] is False


@pytest.mark.parametrize('reverse', [False, True])
def test_workbench_split_native_group_real_cli_retains_unresolved_dn(tmp_path, reverse):
    c = si_workbench(tmp_path/'workbench', 'split')
    rows = c['extra']['base_extent_bindings']; group = copy.deepcopy(rows[0])
    group.pop('segment_id'); group['segment_ids'] = [r['segment_id'] for r in rows]
    group['to_port_id'] = rows[-1]['to_port_id']
    group['specification']['dn'] = None
    group['source_span'] = dict(path=c['object']['source_path'], route_interval=dict(
        from_part=0, from_fraction=0., to_part=1, to_fraction=1.))
    if reverse:
        group['segment_ids'].reverse()
        group['source_span']['route_interval'] = dict(from_part=1, from_fraction=1., to_part=0, to_fraction=0.)
        group['from_port_id'], group['to_port_id'] = group['to_port_id'], group['from_port_id']
    p, _ = fixture(tmp_path, 'independent', physical='other', ports=('Q0', 'Q1'))
    extra = {**c['extra'], 'base_extent_bindings': [group]}
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'other')], extra)
    assert out['preserved_existing_network'] == c['result']
    base = next(r for r in out['quantity_items'] if r['physical_id'] == 'base-physical')
    assert base['quantity'] is None  # legacy DN assignment did not cover all derived extents
    representation = base['representations'][0]
    assert representation['measurements'][0]['replayed_edge_length_m'] == 10
    assert representation['measurements'][0]['source_resolution']['whole_workbench_input_replayed'] is True


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'reordered', 'wrong_direction', 'fraction_ulp',
    'part', 'parent_path', 'parent_extent', 'parent_geometry', 'parent_endpoint', 'unit', 'no_source_extent',
    'forged_parent', 'saved_geometry', 'single_mapping_route', 'both_mapping_keys', 'no_source_drawing',
    'wrong_source_drawing'])
def test_group_source_and_chain_drift_actual_producer_refuses(tmp_path, problem):
    p, _, original, saved, extra = legacy(tmp_path, partial=True)
    g = extra['base_extent_bindings'][0]
    if problem == 'missing':
        g['segment_ids'].pop(1)
    elif problem == 'duplicate':
        g['segment_ids'].append(g['segment_ids'][0])
    elif problem == 'reordered':
        g['segment_ids'][0], g['segment_ids'][1] = g['segment_ids'][1], g['segment_ids'][0]
    elif problem == 'wrong_direction':
        g['segment_ids'].reverse()
    elif problem == 'fraction_ulp':
        g['source_span']['route_interval']['to_fraction'] = math.nextafter(1., 0.)
    elif problem == 'part':
        g['source_span']['route_interval']['to_part'] = 0
    elif problem in {'parent_path', 'parent_extent', 'parent_geometry', 'parent_endpoint', 'unit', 'no_source_extent',
                     'no_source_drawing', 'wrong_source_drawing'}:
        parent = original['segments'][0]
        if problem == 'parent_path':
            parent['source_instance_path'] = g['coordinate_unit_annotation_path']
        elif problem == 'parent_extent':
            parent['source_route_interval']['from_fraction'] = .1
        elif problem == 'parent_geometry':
            parent['geometry']['parts'][0]['end'][0] += .001
            parent['geometry']['parts'][1]['start'][0] += .001
        elif problem == 'parent_endpoint':
            parent['start'][0] += .001
        elif problem == 'unit':
            original['unit'] = 'mm'
        elif problem == 'no_source_drawing':
            parent.pop('source_drawing')
        elif problem == 'wrong_source_drawing':
            parent['source_drawing']['sha256'] = '0'*64
        else:
            parent.pop('source_route_interval')
        extra['network_input'] = write(Path(extra['network_input']['path']), original)
        result_path = tmp_path/'changed-original-result.json'
        done = si_cli(tmp_path, PIPE/'infer_pipe_network.py', '--input', extra['network_input']['path'], '--output', result_path)
        assert done.returncode == 0, done.stderr
        extra['network_result'] = dict(path=str(result_path), sha256=sha(result_path))
    elif problem in {'forged_parent', 'saved_geometry'}:
        if problem == 'forged_parent':
            saved['segments'][0]['parent_segment_id'] = 'other-parent'
        else:
            saved['segments'][0]['geometry']['parts'][0]['end'][0] += .001
        extra['network_result'] = write(Path(extra['network_result']['path']), saved)
    elif problem == 'single_mapping_route':
        extra['base_extent_bindings'] = []
        for ident in g['segment_ids']:
            one = copy.deepcopy(g); one.pop('segment_ids'); one['segment_id'] = ident
            extra['base_extent_bindings'].append(one)
    else:
        g['segment_id'] = g['segment_ids'][0]
    other, _ = fixture(tmp_path, 'other', physical='other', ports=('Q0', 'Q1'))
    request = dict(schema_version=PRODUCTION_REQUEST, network_id='network-a', scope_id='finite',
        scope_basis='anonymous source and chain counterexample', candidate_results=[candidate(tmp_path, other, 'other')], **extra)
    ip = tmp_path/'bad-production.json'; write(ip, request)
    output = tmp_path/'must-not-exist.json'
    done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', ip, '--output', output)
    assert done.returncode == 2 and not output.exists(), done.stderr


@pytest.mark.parametrize('kind', ['vertical', 'restored_gap'])
def test_legacy_other_length_outlets_remain_visible_and_not_recounted(tmp_path, kind):
    p, _, original, _, extra = legacy(tmp_path)
    path = Path(p['drawing']['path']); doc = ezdxf.readfile(path)
    pts = [(100, 100), (110, 100)] if kind == 'vertical' else [(10, 10), (10, 20)]
    second_source = doc.modelspace().add_lwpolyline(pts)
    doc.saveas(path); p['drawing']['sha256'] = sha(path)
    first = original['segments'][0]
    first['source_drawing'] = copy.deepcopy(p['drawing'])
    first['space_id'] = 'F1'
    second = copy.deepcopy(first)
    second.update(segment_id='second', space_id='F2' if kind == 'vertical' else 'F1',
        source_instance_path=[second_source.dxf.handle], source_route_interval=dict(
            from_part=0, from_fraction=0., to_part=0, to_fraction=1.))
    a, b = [[v*.001 for v in [*point, 0.]] for point in pts]
    second.update(start=a, end=b, geometry=route_geometry([dict(kind='line', start=a, end=b)]))
    original['segments'].append(second)
    if kind == 'vertical':
        original['cross_view_links'] = [dict(link_id='riser', physical_id='actual-riser',
            a=dict(segment_id='parent', end='end'), b=dict(segment_id='second', end='start'),
            basis='anonymous cross-page port and elevation correspondence', source_ids=['source-riser'],
            route_is_vertical=True, from_elevation=3., to_elevation=7.2, elevation_unit='m',
            height_source_ids=['source-elevations'], height_level='confirmed', dn_continuity=False)]
    else:
        original['parameters']['gap_max'] = .003
        original['gap_classifications'] = [dict(a_segment='parent', a_end='end', b_segment='second', b_end='start',
            classification='图中文字省略线', quantity_decision='依据图面恢复', quantity_action='restore_omission',
            quantity_evidence_level='inferred', source_id='source-explicit-omission', scenario_include=True)]
    extra['network_input'] = write(Path(extra['network_input']['path']), original)
    result_path = tmp_path/'with-other-outlet.json'
    done = si_cli(tmp_path, PIPE/'infer_pipe_network.py', '--input', extra['network_input']['path'], '--output', result_path)
    assert done.returncode == 0, done.stderr
    saved = json.loads(result_path.read_text(encoding='utf-8'))
    extra['network_result'] = dict(path=str(result_path), sha256=sha(result_path))
    group = extra['base_extent_bindings'][0]
    group.update(drawing=copy.deepcopy(p['drawing']))
    second_binding = copy.deepcopy(group)
    second_binding.update(segment_ids=saved['route_preparation']['parent_map']['second'], physical_id='second',
        extent_id='second', from_port_id='S0', to_port_id='S1',
        source_span=dict(path=[second_source.dxf.handle], route_interval=second['source_route_interval']))
    extra['base_extent_bindings'].append(second_binding)
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'diagram')], extra)
    assert out['preserved_existing_network'] == saved
    leftovers = out['unadapted_existing_length_records']
    expected = ([r for r in saved['vertical_quantities'] if not r['counted_elsewhere']] if kind == 'vertical'
        else [r for r in saved['bridge_quantity_items'] if r['included_in_reconstructed']])
    assert leftovers == expected and len(leftovers) == 1
    assert sum(r['quantity'] for r in out['quantity_items']) == pytest.approx(.028)
    assert len(out['quantity_items']) == 2 and out['formal_total_quantity'] is None


def test_nested_legacy_native_actual_source_chain_and_unit_reach_quantity(tmp_path):
    p, _, _, saved, extra = legacy(tmp_path, nested=True, reverse=True)
    other, _ = fixture(tmp_path, 'other', physical='other', ports=('Q0', 'Q1'))
    out, _ = chain(tmp_path, [candidate(tmp_path, other, 'other')], extra)
    assert out['preserved_existing_network'] == saved
    base = next(r for r in out['quantity_items'] if r['physical_id'] == 'physical-a')
    assert base['quantity'] == pytest.approx(.0256)
    proof = base['representations'][0]['measurements'][0]['source_resolution']
    assert len(proof['full_instance_path']) == 3 and '[0,0]' in proof['full_instance_path'][0]


def test_partial_legacy_group_overlap_is_unresolved_without_difference_fill(tmp_path):
    p, _, _, saved, extra = legacy(tmp_path)
    whole = copy.deepcopy(extra['base_extent_bindings'][0])
    first = copy.deepcopy(whole); first['segment_ids'] = first['segment_ids'][:1]
    first['source_span']['route_interval'].update(to_part=0, to_fraction=1.)
    second = copy.deepcopy(whole); second['segment_ids'] = second['segment_ids'][1:]
    second['source_span']['route_interval'].update(from_part=1, from_fraction=0.)
    first.update(extent_id='part1', to_port_id='MID')
    second.update(extent_id='part2', from_port_id='MID')
    extra['base_extent_bindings'] = [first, second]
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'whole-diagram')], extra)
    assert out['preserved_existing_network'] == saved
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] is None
    assert out['quantity_items'][0]['deduplicated_representation_count'] == 0
    assert len(out['quantity_items'][0]['representations']) == 3


def test_single_derived_edge_reverse_and_remaining_native_group_have_disjoint_owners(tmp_path):
    p, _, _, saved, extra = legacy(tmp_path, partial=True)
    whole = extra['base_extent_bindings'][0]
    one = copy.deepcopy(whole); one['segment_ids'] = one['segment_ids'][:1]
    one['source_span']['route_interval'] = dict(from_part=0, from_fraction=.5, to_part=0, to_fraction=0.)
    one.update(extent_id='first-half', from_port_id='MID', to_port_id='P0')
    remaining = copy.deepcopy(whole); remaining['segment_ids'] = remaining['segment_ids'][1:]
    remaining['source_span']['route_interval']['from_fraction'] = .5
    remaining.update(extent_id='remaining', from_port_id='MID', to_port_id='P1')
    extra['base_extent_bindings'] = [one, remaining]
    other, _ = fixture(tmp_path, 'other', physical='other', ports=('Q0', 'Q1'))
    out, _ = chain(tmp_path, [candidate(tmp_path, other, 'other')], extra)
    quantities = {r['extent_id']: r['quantity'] for r in out['quantity_items']}
    assert quantities['first-half'] == pytest.approx(.005)
    assert quantities['remaining'] == pytest.approx(.013)
    assert len(out['quantity_items']) == 3 and out['preserved_existing_network'] == saved


@pytest.mark.parametrize('defect', ['branch', 'loop', 'duplicate_source'])
def test_legacy_groups_do_not_hide_current_source_route_ambiguity(tmp_path, defect):
    p, meta, original, _, extra = legacy(tmp_path)
    def mutate(doc):
        if defect == 'branch':
            doc.modelspace().add_line((5, 0), (5, 3))
        elif defect == 'loop':
            doc.entitydb[meta['route']].append_points([(0, 0)])
        else:
            doc.modelspace().add_entity(doc.entitydb[meta['route']].copy())
    edit_source(p, mutate)
    extra['base_extent_bindings'][0]['drawing'] = p['drawing']
    original['segments'][0]['source_drawing'] = copy.deepcopy(p['drawing'])
    extra['network_input'] = write(Path(extra['network_input']['path']), original)
    fresh = tmp_path/'current-ambiguous-source-result.json'
    done = si_cli(tmp_path, PIPE/'infer_pipe_network.py', '--input', extra['network_input']['path'], '--output', fresh)
    assert done.returncode == 0, done.stderr
    extra['network_result'] = dict(path=str(fresh), sha256=sha(fresh))
    other, _ = fixture(tmp_path, 'other', physical='other', ports=('Q0', 'Q1'))
    request = dict(schema_version=PRODUCTION_REQUEST, network_id='network-a', scope_id='finite',
        scope_basis='current source route ambiguity', candidate_results=[candidate(tmp_path, other, 'other')], **extra)
    ip = tmp_path/'bad-production.json'; write(ip, request)
    output = tmp_path/'must-not-exist.json'
    done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', ip, '--output', output)
    assert done.returncode == 2 and not output.exists(), done.stderr
