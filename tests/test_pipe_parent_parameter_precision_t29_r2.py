"""Exact parent-parameter coverage precedes any native affine mapping."""
import copy
import json
import math
from pathlib import Path

import ezdxf
import pytest

from test_pipe_native_routes_batch15 import route_fixture
from test_pipe_physical_network_t29_batch13 import (PIPE, write, candidate, chain,
    si_cli, CONSUME_REQUEST, PRODUCTION_REQUEST)
from test_pipe_legacy_directed_groups_t29_closure import record_actual_cli_commands
from measure_pipe_diagram_lengths import Drawing, sha, digest
from produce_pipe_diagram_network import exact_parent_ranges
from qto_pipe_topology import route_geometry


def precision_fixture(tmp_path, cuts, reverse_parent=False, reverse_group=False):
    p, meta = route_fixture(tmp_path, points=[(0, 0), (4000, 0), (4000, 3000), (7000, 3000)],
        values=['3m', '3m', '2.25m'])
    path = Path(p['drawing']['path']); doc = ezdxf.readfile(path)
    doc.entitydb[meta['dimensions'][0]].dxf.defpoint2 = (1000, 0, 0)
    doc.entitydb[meta['dimensions'][2]].dxf.defpoint3 = (6250, 3000, 0)
    note = doc.modelspace().add_text('绘图坐标单位mm', dxfattribs={'insert': (-100, -100)})
    doc.saveas(path); p['drawing']['sha256'] = sha(path)
    p['views'][0]['bounds'] = [-2000, -2000, 10000, 6000]
    route = dict(from_part=0, from_fraction=.25, to_part=2, to_fraction=.75)
    if reverse_parent:
        route = dict(from_part=2, from_fraction=.75, to_part=0, to_fraction=.25)
    p['segments'][0]['source_span']['route_interval'] = route
    selected = Drawing(doc).span(p['segments'][0]['source_span'], p['views'][0]['bounds'])
    parts = [dict(kind='line', start=[v*.001 for v in leg['page_endpoints'][0]],
        end=[v*.001 for v in leg['page_endpoints'][1]]) for leg in selected['source_parts']]
    raw = dict(segment_id='parent', system='water', start=parts[0]['start'], end=parts[-1]['end'],
        geometry=route_geometry(parts), source_ids=['anonymous-source'], source_drawing=p['drawing'],
        source_instance_path=p['segments'][0]['source_span']['path'], source_route_interval=route,
        dn_seeds=[dict(dn='DN25', evidence_level='confirmed', source_id='source-dn',
            applies_to_entire_object=True, scope_source_id='whole-native-parent-dn')])
    original = dict(schema_version='qto-pipe-network-inference-input/1.0', network_id='network-a', unit='m',
        segments=[raw], parameters=dict(snap_tolerance=.000001, gap_max=.1), scope_boundary_nodes=[])
    for i, (part, parameter) in enumerate(cuts):
        original['scope_boundary_nodes'].append(dict(boundary_id='CUT'+str(i), segment_id='parent',
            part_index=part, parameter=parameter, system='water', source_id='source-cut',
            evidence_level='confirmed', basis='explicit original parent parameter'))
    ip = tmp_path/'parent-input.json'; write(ip, original)
    op = tmp_path/'parent-result.json'
    done = si_cli(tmp_path, PIPE/'infer_pipe_network.py', '--input', ip, '--output', op)
    assert done.returncode == 0, done.stderr
    saved = json.loads(op.read_text(encoding='utf-8'))
    ids = saved['route_preparation']['parent_map']['parent']
    mapping = dict(segment_ids=ids[::-1] if reverse_group else ids,
        **{k: copy.deepcopy(p['segments'][0][k]) for k in ('physical_id', 'extent_id', 'from_port_id',
            'to_port_id', 'specification', 'recognition_basis')}, drawing=p['drawing'], bounds=p['views'][0]['bounds'],
        source_span=copy.deepcopy(p['segments'][0]['source_span']), coordinate_unit_annotation_path=[note.dxf.handle],
        basis='actual native partial parent and exact original cuts')
    if reverse_group:
        r = mapping['source_span']['route_interval']
        mapping['source_span']['route_interval'] = dict(from_part=r['to_part'], from_fraction=r['to_fraction'],
            to_part=r['from_part'], to_fraction=r['from_fraction'])
        mapping['from_port_id'], mapping['to_port_id'] = mapping['to_port_id'], mapping['from_port_id']
    extra = dict(network_input=dict(path=str(ip), sha256=sha(ip)),
        network_result=dict(path=str(op), sha256=sha(op)), base_extent_bindings=[mapping])
    return p, saved, extra


def production_request(tmp_path, p, extra):
    return dict(schema_version=PRODUCTION_REQUEST, network_id='network-a', scope_id='finite',
        scope_basis='parent parameter exactness control', candidate_results=[candidate(tmp_path, p, 'diagram')], **extra)


@pytest.mark.parametrize('reverse_parent,reverse_group', [(False, False), (False, True), (True, False), (True, True)])
def test_exact_contiguous_partial_parent_actual_cli_keeps_825_and_one_owner(tmp_path, reverse_parent, reverse_group):
    p, saved, extra = precision_fixture(tmp_path, [(0, .1), (0, .5)], reverse_parent, reverse_group)
    out, _ = chain(tmp_path, [candidate(tmp_path, p, 'diagram')], extra)
    assert out['preserved_existing_network'] == saved
    assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] == 8.25
    assert out['quantity_items'][0]['deduplicated_representation_count'] == 1


@pytest.mark.parametrize('reverse_parent,reverse_group', [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize('defect', ['interior_alias', 'cross_part_end', 'cross_part_start', 'outer_start_alias'])
def test_native_parent_gap_refused_and_existing_vertex_canonicalization_preserved(tmp_path,
        reverse_parent, reverse_group, defect):
    cuts = {'interior_alias': [(0, .1), (0, math.nextafter(.1, 1.)), (0, .5)],
        'cross_part_end': [(0, math.nextafter(1., 0.))],
        'cross_part_start': [(1, math.nextafter(0., 1.))],
        'outer_start_alias': [(0, math.nextafter(0., 1.))]}[defect]
    p, saved, extra = precision_fixture(tmp_path, cuts, reverse_parent, reverse_group)
    if defect == 'interior_alias':
        locations = [r['parent_geometry_interval'] for r in saved['segments'] if r['parent_geometry_interval']['part'] == 0]
        assert locations[0]['to_fraction'] == .1 and locations[1]['from_fraction'] == math.nextafter(.1, 1.)
    else:
        # The unchanged legacy locator canonicalizes near-vertex references
        # before subdivision. These outputs really cover every parent part;
        # they are not the skipped interior interval from the P2 defect.
        locations = [r['parent_geometry_interval'] for r in saved['segments']]
        assert locations == [dict(part=i, from_fraction=0., to_fraction=1.) for i in range(3)]
        out, _ = chain(tmp_path, [candidate(tmp_path, p, 'diagram')], extra)
        assert len(out['quantity_items']) == 1 and out['quantity_items'][0]['quantity'] == 8.25
        return
    request = production_request(tmp_path, p, extra)
    ip = tmp_path/'bad-production.json'; write(ip, request)
    op = tmp_path/'must-not-exist.json'
    done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', ip, '--output', op)
    assert done.returncode == 2 and not op.exists(), done.stderr
    assert '原父part' in done.stderr or '精确映射' in done.stderr


@pytest.mark.parametrize('cut', [.1, math.nextafter(.1, 1.)])
def test_outer_partial_group_boundary_cannot_alias_after_affine_rounding(tmp_path, cut):
    p, _, extra = precision_fixture(tmp_path, [(0, cut)])
    full = extra['base_extent_bindings'][0]
    left = copy.deepcopy(full); right = copy.deepcopy(full)
    left.update(segment_ids=full['segment_ids'][:1], extent_id='left', to_port_id='MID')
    right.update(segment_ids=full['segment_ids'][1:], extent_id='right', from_port_id='MID')
    # Both distinct original cuts round to this same source float. Neither
    # exact rational boundary equals that literal float, so no owner is made.
    left['source_span']['route_interval'].update(to_part=0, to_fraction=.325)
    right['source_span']['route_interval'].update(from_part=0, from_fraction=.325)
    extra['base_extent_bindings'] = [left, right]
    request = production_request(tmp_path, p, extra)
    ip = tmp_path/'bad-production.json'; write(ip, request)
    op = tmp_path/'must-not-exist.json'
    done = si_cli(tmp_path, PIPE/'produce_pipe_diagram_network.py', '--input', ip, '--output', op)
    assert done.returncode == 2 and not op.exists() and '精确映射' in done.stderr


def test_consumer_current_envelope_replays_bad_parent_and_refuses_old_valid_quantity(tmp_path):
    valid = tmp_path/'valid'; valid.mkdir()
    p, _, extra = precision_fixture(valid, [(0, .1), (0, .5)])
    _, consume_request = chain(valid, [candidate(valid, p, 'diagram')], extra)
    bad = tmp_path/'bad'; bad.mkdir()
    bp, _, bextra = precision_fixture(bad, [(0, .1), (0, math.nextafter(.1, 1.)), (0, .5)])
    bad_request = production_request(bad, bp, bextra)
    request_path = bad/'bad-production.json'; request_binding = write(request_path, bad_request)
    saved = json.loads(Path(consume_request['network_result']['path']).read_text(encoding='utf-8'))
    saved['source_request'] = request_binding
    saved['input_fingerprint'] = digest(bad_request)
    forged = bad/'rebound-old-positive.json'; binding = write(forged, saved)
    consumer = dict(schema_version=CONSUME_REQUEST, network_id='network-a', scope_id='finite',
        scope_basis='fresh code envelope cannot bypass parent replay', network_result=binding)
    cp = bad/'consume-request.json'; write(cp, consumer)
    op = bad/'must-not-exist.json'
    done = si_cli(bad, PIPE/'consume_pipe_physical_network.py', '--input', cp, '--output', op)
    assert done.returncode == 2 and not op.exists() and '原父part参数' in done.stderr


@pytest.mark.parametrize('ranges', [
    [(0, 0., .1), (0, math.nextafter(.1, 1.), 1.)],
    [(0, 0., .5), (0, math.nextafter(.5, 0.), 1.)],
    [(0, 0., .5), (0, 0., .5)], [(0, .5, 1.), (0, 0., .5)],
    [(0, 0., math.nextafter(1., 0.)), (1, 0., 1.)],
    [(0, 0., 1.), (1, math.nextafter(0., 1.), 1.)],
    [(0, 0., 1.), (2, 0., 1.)], [(1, 0., 1.), (0, 0., 1.)]])
def test_exact_parent_space_guard_rejects_gap_overlap_order_and_part_alias(ranges):
    rows = [dict(parent_geometry_interval=dict(part=i, from_fraction=a, to_fraction=b)) for i, a, b in ranges]
    with pytest.raises(ValueError, match='原父part'):
        exact_parent_ranges(rows, 3)
