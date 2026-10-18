"""T45/T46/T47: independent synthetic physical connections and evidence boundaries."""
import copy
import itertools
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/qto-duct-air-systems/scripts'))
import infer_duct_fittings_vertical as duct


def seg(sid, start, end, z=None, floor='L1', system='SYS'):
    row = dict(segment_id=sid, floor_id=floor, system=system, start=start, end=end,
               width=400, height=200, size_evidence_level='确认', source_ids=['SYN-'+sid])
    if z is not None:
        row['elevation'] = dict(value=z, reference='center', source_id='SYN-TAG-'+sid, evidence_level='确认')
    return row


def data(segments, fittings=()):
    return dict(schema_version=duct.INPUT_SCHEMA, unit='mm', output_unit='m',
                parameters=dict(parameter_basis='独立合成：几何mm，层标高及中心标高m'),
                floors=[dict(floor_id='L1', level=0., level_source_id='SYN-L1')],
                segments=segments, fittings=list(fittings))


def elbow(fid, sid, port, center, bbox, floor='L1'):
    return dict(fitting_id=fid, floor_id=floor, system='SYS', kind='elbow', bbox=bbox,
                source_id='SYN-'+fid, ends=[dict(segment_id=sid, end=port)],
                single_end_geometry=dict(source_id='SYN-GEO-'+fid, basis='独立给定中心止点与连接方向',
                    endpoint_relation='at_connection', turn_plane='vertical', centerline_point=center))


def link(a, b, **extra):
    return dict(link_id='HL', kind='segment_to_segment', segment_id=a, to_segment_id=b,
                source_id='SYN-SECTION', basis='独立合成明确连接', evidence_level='规则推算', **extra)


def inter(two_ends=False):
    d = data([seg('A', [0, 0], [1000, 0], 2.), seg('B', [0, 10000], [1000, 10000], 2., floor='L2')],
             [elbow('AE', 'A', 'end', [1200, 0], [900, -200, 1400, 200]),
              elbow('BE', 'B', 'end', [1200, 10000], [900, 9800, 1400, 10200], floor='L2')])
    d['floors'].append(dict(floor_id='L2', level=4.5, level_source_id='SYN-L2', plan_offset=[0, 10000]))
    if two_ends:
        d['fittings'] += [elbow('AS', 'A', 'start', [-200, 0], [-400, -200, 100, 200]),
                          elbow('BS', 'B', 'start', [-200, 10000], [-400, 9800, 100, 10200], floor='L2')]
    return d


def branch():
    return data([seg('A', [0, 300], [0, 1500], 2.), seg('B', [-3000, 0], [3000, 0], 4.)],
                [elbow('AE', 'A', 'start', [0, 100], [-200, 0, 200, 400])])


@pytest.mark.parametrize('factory,expected,kind', [(inter, 4.5, 'inter_floor_riser'), (branch, 2., 'branch_to_main')])
def test_host_and_automatic_have_one_physical_quantity_outlet(factory, expected, kind):
    d = factory()
    automatic = duct.infer(copy.deepcopy(d))
    assert len(automatic['vertical_items']) == 1 and automatic['vertical_items'][0]['kind'] == kind
    d['vertical_links'] = [link('A', 'B')]
    r = duct.infer(d)
    assert len(r['vertical_items']) == 1 and r['vertical_items'][0]['kind'] == 'host_declared_link'
    assert r['metrics']['vertical_length_rule'] == expected
    assert all(f['vertical_id'] == r['vertical_items'][0]['vertical_id'] for f in r['fittings'])


def test_same_segment_pair_distinct_ports_keep_other_real_riser():
    d = inter(two_ends=True)
    d['vertical_links'] = [link('A', 'B', connection_ends=[dict(segment_id='A', end='end')])]
    r = duct.infer(d)
    assert r['metrics']['vertical_length_rule'] == 9.
    assert [v['kind'] for v in r['vertical_items']] == ['host_declared_link', 'inter_floor_riser']
    automatic = r['vertical_items'][1]
    assert {f['end'] for f in automatic['connection_ends']} == {'start'}
    assert not any(p['kind'] == 'vertical_connection_identity_unresolved' for p in r['pending'])


def test_unanchored_host_does_not_swallow_different_ports_of_same_pair():
    d = inter(two_ends=True)
    d['vertical_links'] = [link('A', 'B')]
    r = duct.infer(d)
    assert r['metrics']['vertical_length_rule'] == 4.5
    pending = [p for p in r['pending'] if p['kind'] == 'vertical_connection_identity_unresolved']
    assert len(pending) == 2 and {tuple(p['fitting_ids']) for p in pending} == {('AE', 'BE'), ('AS', 'BS')}
    assert r['metrics']['totals_complete'] is False
    assert all(f['vertical_id'] is None for f in r['fittings'])


def test_two_anchored_host_connections_of_one_pair_are_allowed():
    d = inter(two_ends=True)
    d['vertical_links'] = [link('A', 'B', connection_ends=[dict(segment_id='A', end='start')]),
         dict(link('A', 'B', connection_ends=[dict(segment_id='A', end='end')]), link_id='HL2')]
    r = duct.infer(d)
    assert len(r['vertical_items']) == 2 and r['metrics']['vertical_length_rule'] == 9.
    assert all(v['kind'] == 'host_declared_link' for v in r['vertical_items'])


def test_conflicting_partner_port_does_not_double_count_an_occupied_port():
    d = inter(two_ends=True)
    d['vertical_links'] = [link('A', 'B', connection_ends=[dict(segment_id='A', end='end'), dict(segment_id='B', end='start')])]
    r = duct.infer(d)
    assert r['metrics']['vertical_length_rule'] == 4.5
    assert len([p for p in r['pending'] if p['kind'] == 'vertical_connection_identity_unresolved']) == 2
    assert r['metrics']['totals_complete'] is False


@pytest.mark.parametrize('blocked_ids', [(), ('AS',), ('AS', 'BS'), ('AS', 'BS', 'AE', 'BE')])
def test_one_host_checks_resolved_and_blocked_candidates_together(blocked_ids):
    d = inter(two_ends=True)
    d['parameters']['riser_match_distance'] = 500.
    for f in d['fittings']:
        if f['fitting_id'] in blocked_ids:
            f['single_end_geometry'] = dict(endpoint_relation='unknown', source_id='SYN-UNKNOWN', basis='端口方向未决')
    d['vertical_links'] = [link('A', 'B')]
    r = duct.infer(d)
    pending = [p for p in r['pending'] if p['kind'] == 'vertical_connection_identity_unresolved']
    assert len(pending) == 2
    assert {tuple(p['fitting_ids']) for p in pending} == {('AE', 'BE'), ('AS', 'BS')}
    assert r['metrics']['vertical_length_rule'] == 4.5 and len(r['vertical_items']) == 1
    assert r['metrics']['totals_complete'] is False


def test_anchored_host_does_not_hide_other_blocked_connection():
    d = inter(two_ends=True)
    d['parameters']['riser_match_distance'] = 500.
    d['fittings'][2]['single_end_geometry'] = dict(endpoint_relation='unknown', source_id='SYN-UNKNOWN', basis='端口方向未决')
    d['vertical_links'] = [link('A', 'B', connection_ends=[dict(segment_id='A', end='end')])]
    r = duct.infer(d)
    blocked = [p for p in r['pending'] if p['kind'] == 'inter_floor_riser_blocked_by_single_end_geometry']
    assert len(blocked) == 1 and blocked[0]['fitting_ids'] == ['AS', 'BS']
    assert not any(p['kind'] == 'vertical_connection_identity_unresolved' for p in r['pending'])
    assert r['metrics']['vertical_length_rule'] == 4.5


@pytest.mark.parametrize('anchors', [None, [dict(segment_id='A', end='end')]])
def test_repeated_host_declarations_still_reject(anchors):
    d = inter()
    first = link('A', 'B', **({'connection_ends': anchors} if anchors else {}))
    d['vertical_links'] = [first, dict(first, link_id='HL2')]
    with pytest.raises(duct.InputError, match='重复声明'):
        duct.infer(d)


def test_bad_anchor_cannot_forge_an_unrelated_endpoint():
    d = inter()
    d['vertical_links'] = [link('A', 'B', connection_ends=[dict(segment_id='OUTSIDE', end='end')])]
    with pytest.raises(duct.InputError, match='connection_ends'):
        duct.infer(d)


@pytest.mark.parametrize('projection', [False, True])
def test_host_supersedes_blocked_riser_but_keeps_plane_geometry_pending(projection):
    d = inter()
    g = dict(source_id='SYN-REVERSED', basis='合成反向接口，平面几何仍未决', endpoint_relation='reversed', turn_plane='vertical')
    if projection:
        g['riser_projection'] = [20, 380]
    d['fittings'][1]['single_end_geometry'] = g
    no_host = duct.infer(copy.deepcopy(d))
    assert any(p['kind'] == 'inter_floor_riser_blocked_by_single_end_geometry' for p in no_host['pending'])
    assert bool(no_host['vertical_scenarios']) is projection
    assert no_host['metrics']['vertical_length_scenario'] == 0
    d['vertical_links'] = [link('A', 'B')]
    r = duct.infer(d)
    assert r['metrics']['vertical_length_rule'] == 4.5 and len(r['vertical_items']) == 1
    assert not r['vertical_scenarios']
    assert not any(p['kind'] in {'inter_floor_riser_blocked_by_single_end_geometry', 'single_end_destination_unknown'} for p in r['pending'])
    assert any(p['kind'] == 'single_end_plane_unresolved' for p in r['pending'])
    assert r['metrics']['totals_complete'] is False


def ports():
    d = data([seg('A', [0, 0], [1000, 0], 2.), seg('B', [1200, 0], [1400, 0], 2.5), seg('C', [1600, 0], [2600, 0], 3.)])
    d['fittings'] = [dict(fitting_id='F1', floor_id='L1', kind='elbow', system='SYS', bbox=[900, -300, 1700, 300],
             ends=[dict(segment_id='A', end='end'), dict(segment_id='B', end='start')]),
        dict(fitting_id='F2', floor_id='L1', kind='elbow', system='SYS', bbox=[1000, -300, 1800, 300],
             ends=[dict(segment_id='B', end='end'), dict(segment_id='C', end='start')])]
    return d


def test_high_bbox_overlap_does_not_merge_different_explicit_ports():
    r = duct.infer(ports())
    assert len(r['fittings']) == 2 and r['duplicate_fittings'] == []


def test_evidenced_equal_ports_still_collapse_duplicate_representation():
    d = ports()
    d['fittings'][1]['ends'] = copy.deepcopy(d['fittings'][0]['ends'])
    r = duct.infer(d)
    assert len(r['fittings']) == 1 and r['duplicate_fittings'][0]['duplicate_of'] == 'F1'


def test_physical_identity_namespaces_and_ports_cannot_be_overridden():
    d = ports()
    identity = dict(instance_id='ONE', source_id='DRAWING-A', basis='独立确认同物实例')
    for f in d['fittings']:
        f['physical_identity'] = copy.deepcopy(identity)
    r = duct.infer(copy.deepcopy(d))
    assert len(r['fittings']) == 2 and not r['duplicate_fittings']  # different explicit ports override same-id claim
    assert any('冲突' in p['reason'] for p in r['pending'])
    d['fittings'][1]['ends'] = copy.deepcopy(d['fittings'][0]['ends'])
    d['fittings'][1]['physical_identity']['source_id'] = 'DRAWING-B'
    r = duct.infer(d)
    assert len(r['fittings']) == 2 and not r['duplicate_fittings']  # equal id in a different source is different
    assert any('冲突' in p['reason'] for p in r['pending'])


def test_evidenced_same_physical_identity_deduplicates_and_roundtrips_receipt():
    d = ports()
    identity = dict(instance_id='ONE', source_id='DRAWING-A', basis='独立确认同物实例')
    for f in d['fittings']:
        f.pop('ends')
        f['physical_identity'] = copy.deepcopy(identity)
    r = duct.infer(d)
    assert len(r['fittings']) == 1 and r['duplicate_fittings'][0]['physical_identity'] == identity
    assert r['fittings'][0]['physical_identity'] == identity


def test_equal_instance_names_in_different_sources_are_not_same_object():
    d = ports()
    for index, f in enumerate(d['fittings']):
        f.pop('ends')
        f['physical_identity'] = dict(instance_id='SAME-NAME', source_id='SOURCE-'+str(index), basis='不同来源内实例')
    r = duct.infer(d)
    assert len(r['fittings']) == 2 and not r['duplicate_fittings']


def test_identical_bbox_without_identity_or_ports_retains_two_candidates():
    d = ports()
    for f in d['fittings']:
        f.pop('ends')
        f['bbox'] = [900, -300, 1800, 300]
    r = duct.infer(d)
    assert len(r['fittings']) == 2 and not r['duplicate_fittings']
    assert any(p['kind'] == 'fitting_duplicate_identity_unresolved' for p in r['pending'])
    assert r['metrics']['totals_complete'] is False


def test_conflicting_physical_group_is_order_invariant_with_an_unported_member():
    d = ports()
    identity = dict(instance_id='ONE', source_id='SYN-SOURCE', basis='同物声明须与全组端口交叉核对')
    no_port = copy.deepcopy(d['fittings'][0])
    no_port.update(fitting_id='NO_PORT', bbox=[900, -300, 1800, 300])
    no_port.pop('ends')
    d['fittings'][0]['fitting_id'], d['fittings'][1]['fitting_id'] = 'LEFT', 'RIGHT'
    all_fittings = [no_port] + d['fittings']
    for f in all_fittings:
        f['physical_identity'] = copy.deepcopy(identity)
    for ordering in itertools.permutations(all_fittings):
        current = copy.deepcopy(d)
        current['fittings'] = list(copy.deepcopy(ordering))
        r = duct.infer(current)
        assert {f['fitting_id'] for f in r['fittings']} == {'NO_PORT', 'LEFT', 'RIGHT'}
        assert not r['duplicate_fittings']
        conflicts = [p for p in r['pending'] if p['kind'] == 'fitting_duplicate_identity_unresolved']
        assert len(conflicts) == 1 and set(conflicts[0]['fitting_ids']) == {'NO_PORT', 'LEFT', 'RIGHT'}
        assert len(conflicts[0]['explicit_port_sets']) == 2
        assert r['metrics']['totals_complete'] is False


def test_nonconflicting_identity_group_keeps_explicit_ports_as_representative():
    d = ports()
    explicit = copy.deepcopy(d['fittings'][0])
    explicit['physical_identity'] = dict(instance_id='ONE', source_id='SYN-SOURCE', basis='同物且唯一显式端口')
    unported = copy.deepcopy(explicit)
    unported['fitting_id'] = 'NO_PORT'
    unported.pop('ends')
    for ordering in itertools.permutations([explicit, unported]):
        current = copy.deepcopy(d)
        current['fittings'] = list(copy.deepcopy(ordering))
        r = duct.infer(current)
        assert len(r['fittings']) == 1 and r['fittings'][0]['fitting_id'] == explicit['fitting_id']
        assert r['fittings'][0]['ends'] == explicit['ends']


@pytest.mark.parametrize('identity', [{}, dict(instance_id='', source_id='S', basis='B'),
    dict(instance_id='A', source_id='', basis='B'), dict(instance_id='A', source_id='S', basis=''), 'A'])
def test_incomplete_physical_identity_is_not_evidence(identity):
    d = ports()
    d['fittings'][0]['physical_identity'] = identity
    with pytest.raises(duct.InputError, match='physical_identity'):
        duct.infer(d)


@pytest.mark.parametrize('remove_both', [False, True])
def test_bbox_without_two_explicit_port_sets_stays_unresolved(remove_both):
    d = ports()
    d['fittings'][1].pop('ends')
    if remove_both:
        d['fittings'][0].pop('ends')
    r = duct.infer(d)
    assert len(r['fittings']) == 2 and not r['duplicate_fittings']
    assert any(p['kind'] == 'fitting_duplicate_identity_unresolved' for p in r['pending'])
    assert r['metrics']['totals_complete'] is False


def elevation_graph(tap=False):
    start, end = ([0, 700], [0, 1700]) if tap else ([0, 200], [0, 1200])
    up_start, up_end = ([0, 1900], [0, 2900]) if tap else ([0, 1400], [0, 2400])
    d = data([seg('BR', start, end), seg('UP', up_start, up_end, 4.6), seg('MAIN', [-3000, 0], [3000, 0], 6.35)])
    if tap:
        d['fittings'] = [dict(fitting_id='T', floor_id='L1', kind='tap', system='SYS', bbox=[-250, -200, 250, 900],
                             ends=[dict(segment_id='BR', end='start')])]
    return d


def elevation(r, sid='BR'):
    return next(s for s in r['straight_segments'] if s['segment_id'] == sid)


@pytest.mark.parametrize('tap', [False, True])
def test_same_branch_gap_source_wins_over_main_at_same_hop(tap):
    r = duct.infer(elevation_graph(tap))
    e = elevation(r)
    assert e['center_elevation'] == 4.6 and e['elevation_evidence_level'] == '规则推算'
    assert 'UP' in e['elevation_basis'] and '不跨主支管' in e['elevation_basis']


def test_more_hops_on_same_branch_still_beat_near_main():
    d = elevation_graph()
    d['segments'][1].pop('elevation')
    d['segments'].append(seg('FAR', [0, 2600], [0, 3600], 4.8))
    assert elevation(duct.infer(d))['center_elevation'] == 4.8


def test_true_same_branch_equal_distance_conflict_stays_null():
    d = elevation_graph()
    d['segments'] = d['segments'][:2] + [seg('DOWN', [0, -1000], [0, 0], 6.35)]
    e = elevation(duct.infer(d))
    assert e['center_elevation'] is None and e['elevation_evidence_level'] == '未决'


def test_main_only_fallback_is_explicit_scenario_not_rule_elevation():
    d = elevation_graph()
    d['segments'][1].pop('elevation')
    e = elevation(duct.infer(d))
    assert e['center_elevation'] == 6.35 and e['elevation_evidence_level'] == '方案暂估'
    assert '同高假定' in e['elevation_basis']


def test_own_tag_wins_and_cross_system_never_propagates():
    d = elevation_graph()
    d['segments'][0]['elevation'] = dict(value=4.8, reference='center', source_id='SYN-OWN')
    assert elevation(duct.infer(copy.deepcopy(d)))['center_elevation'] == 4.8
    d['segments'][0].pop('elevation')
    d['segments'][2]['system'] = 'OTHER'
    assert elevation(duct.infer(d))['center_elevation'] == 4.6


def test_normal_tee_keeps_main_continuity_and_branch_scenario():
    d = data([seg('ML', [-2000, 0], [-200, 0], 3.), seg('MR', [200, 0], [2000, 0]), seg('BR', [0, 200], [0, 2000])],
          [dict(fitting_id='TEE', floor_id='L1', kind='tee', system='SYS', bbox=[-300, -100, 300, 300],
                ends=[dict(segment_id='ML', end='end'), dict(segment_id='MR', end='start'), dict(segment_id='BR', end='start')])])
    r = duct.infer(d)
    main = elevation(r, 'MR')
    branch = elevation(r, 'BR')
    assert main['center_elevation'] == 3. and main['elevation_evidence_level'] == '规则推算'
    assert branch['center_elevation'] == 3. and branch['elevation_evidence_level'] == '方案暂估'
    assert r['fittings'][0]['status'] == 'junction' and len(r['fittings'][0]['arms']) == 3


def test_coincident_tee_ports_do_not_bypass_main_branch_boundary():
    d = data([seg('ML', [-2000, 0], [0, 0], 3.), seg('MR', [0, 0], [2000, 0]), seg('BR', [0, 0], [0, 2000], 4.6)],
          [dict(fitting_id='TEE', floor_id='L1', kind='tee', system='SYS', bbox=[-100, -100, 100, 100],
                ends=[dict(segment_id='ML', end='end'), dict(segment_id='MR', end='start'), dict(segment_id='BR', end='start')])])
    r = duct.infer(d)
    assert len(r['joints']) == 3
    main = elevation(r, 'MR')
    assert main['center_elevation'] == 3. and main['elevation_evidence_level'] == '规则推算'
    assert elevation(r, 'BR')['center_elevation'] == 4.6


@pytest.mark.parametrize('degrees', [0., 37., 90., 143.])
def test_bare_endpoint_tee_preserves_each_run_without_a_fitting_block(degrees):
    angle = math.radians(degrees)
    def point(x, y):
        return [13000 + x * math.cos(angle) - y * math.sin(angle), -7000 + x * math.sin(angle) + y * math.cos(angle)]
    d = data([seg('UP', point(0, -6000), point(0, -3000), 4.8),
              seg('BR', point(0, -3000), point(0, 0)),
              seg('ML', point(-5000, 0), point(0, 0), 6.9),
              seg('MR', point(0, 0), point(5000, 0))])
    r = duct.infer(d)
    assert not r['fittings']
    branch, main = elevation(r, 'BR'), elevation(r, 'MR')
    assert branch['center_elevation'] == 4.8 and branch['elevation_evidence_level'] == '规则推算'
    assert main['center_elevation'] == 6.9 and main['elevation_evidence_level'] == '规则推算'
    assert len([j for j in r['joints'] if j.get('elevation_relation') == 'branch_boundary']) == 2


def test_bare_endpoint_tee_does_not_propagate_another_systems_tag():
    d = data([seg('UP', [0, -6000], [0, -3000], 4.8, system='OTHER'),
              seg('BR', [0, -3000], [0, 0]), seg('ML', [-5000, 0], [0, 0], 6.9), seg('MR', [0, 0], [5000, 0])])
    r = duct.infer(d)
    branch = elevation(r, 'BR')
    assert branch['center_elevation'] == 6.9 and branch['elevation_evidence_level'] == '方案暂估'
    assert r['cross_system_contacts']
    assert elevation(r, 'MR')['center_elevation'] == 6.9
