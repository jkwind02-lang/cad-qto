"""Actual curved leader parameters, never display chords or nearby tags."""
import json
from pathlib import Path
import math
import pytest
from test_duct_source_recovery_t28_batch9_20261019 import leader_case, refresh
from test_duct_batch_network_t28_t20_20261019 import build, consume, write, sha


def curved_case(tmp_path, *, omitted=False):
    c = leader_case(tmp_path); rule = c['profile']['rules'][-1]; cfg = rule['annotation_binding']['leader_network']
    old = cfg['source_keys'][-1]
    def edit(m, doc):
        m.delete_entity(doc.entitydb[old[4:]])
        curve = m.add_lwpolyline([(300, 6400, .41421356237309503), (1500, 6000, 0)], format='xyb',
                                 dxfattribs={'layer': 'DUCT-LEADER'})
        if omitted:
            arc = next(curve.virtual_entities()); angle = math.radians((arc.dxf.start_angle + arc.dxf.end_angle) / 2)
            middle = ((900, 6200) if omitted == 'chord' else
                      (arc.dxf.center.x + arc.dxf.radius * math.cos(angle), arc.dxf.center.y + arc.dxf.radius * math.sin(angle)))
            m.add_line(middle, (2500, 5500), dxfattribs={'layer': 'DUCT-LEADER'})
        return 'CAD-' + curve.dxf.handle
    key = refresh(c, edit); cfg['source_keys'][-1] = key
    ep = Path(rule['evidence']['path']); evidence = json.loads(ep.read_text(encoding='utf-8'))
    evidence['annotation_network'] = cfg; write(ep, evidence); rule['evidence']['sha256'] = sha(ep)
    return c


def test_source_bulge_arc_leader_binds_actual_complete_tree(tmp_path):
    c = curved_case(tmp_path); data = build(c)
    scope = data['annotation_scope_candidates'][0]
    assert scope['status'] == 'unique_source_leader_tree'
    assert len(scope['target_part_keys']) == 2
    assert any(edge['geometry']['kind'] == 'ellipse_arc' for path in scope['target_branch_paths'] for edge in path['leader_path'])
    lines = [s for s in data['segments'] if s['representation'] == 'single_line']
    assert sum(s.get('width') == 300 for s in lines) == 2
    result = consume(tmp_path, data)
    assert any(s['center_elevation'] == pytest.approx(3.3) for s in result['straight_segments'] if s.get('center_elevation') is not None)


def test_omitted_actual_branch_touching_arc_interior_not_hidden_by_chord(tmp_path):
    c = curved_case(tmp_path, omitted=True); data = build(c)
    assert data['annotation_scope_candidates'][0]['status'] == 'unresolved'
    assert data['annotation_scope_candidates'][0]['omitted_connected_source_keys']
    assert all(s.get('width') is None for s in data['segments'] if s['representation'] == 'single_line')


def test_disconnected_line_at_display_chord_does_not_invent_arc_contact(tmp_path):
    c = curved_case(tmp_path, omitted='chord'); data = build(c)
    assert data['annotation_scope_candidates'][0]['status'] == 'unique_source_leader_tree'


def test_ellipse_parameter_projection_residual_cannot_prove_omitted_branch_disconnected():
    from duct_source_recovery import source_curve_contact_possible, source_curve_incidence
    part = {'kind':'ellipse_arc','center':[0.,0.,0.],'u':[1000.,0.,0.],'v':[0.,1.,0.],
            'start_param':0.,'sweep_param':math.pi/2}
    angle=.7; exact=[1000*math.cos(angle),math.sin(angle),0.]
    normal=[math.cos(angle)/1000,math.sin(angle),0.];scale=math.hypot(*normal)
    point=[p+.0001*n/scale for p,n in zip(exact,normal)]
    assert math.dist(point,exact)<.001
    assert source_curve_incidence(point,part)[1]>.001
    assert source_curve_contact_possible(point,part,.001) is True
    assert source_curve_contact_possible([2000.,2000.,0.],part,.001) is False
