"""Drawing context is distinct from quantity scope; omitted lines are explicit."""
import copy, json, subprocess, sys
import pytest
from test_manual_routes import ROOT, base, line, run_engine
from test_project_feedback_fixes import cropped_network, boundary


def full_boundary(owner='in_scope'):
    return dict(boundary(), connections_complete=True, fitting_quantity_owner=owner,
                external_ports=[dict(port_id='third-port', segment_id='OUTSIDE', point=[0,0,0],
                    outside_point=[5,0,0], dn='DN40', source_id='outside-drawing', evidence_level='inferred')])


def test_complete_context_restores_tee_without_adding_pipe_length(tmp_path):
    d=cropped_network();d['scope_boundary_nodes']=[full_boundary()];o=run_engine(tmp_path,d)
    f=o['fitting_candidates'][0]
    assert f['type']=='三通候选' and f['degree']==3 and f['measured_degree']==2
    assert f['dn_candidates']==['DN15','DN40']
    assert o['metrics']['raw_length']==7 and o['metrics']['segment_count']==2
    assert f['external_port_ids']==['third-port'] and o['metrics']['dn_assigned_segment_count']==2


def test_context_fitting_outside_scope_not_in_quantity_summary(tmp_path):
    d=cropped_network();d['scope_boundary_nodes']=[full_boundary('outside_scope')]
    o=run_engine(tmp_path,d)
    assert not o['fitting_summary'] and o['metrics']['outside_scope_fitting_candidate_count']==1
    assert o['metrics']['raw_length']==7


def test_context_scenario_cannot_be_promoted_to_inferred(tmp_path):
    d=cropped_network();d['scope_boundary_nodes']=[full_boundary()]
    d['scope_boundary_nodes'][0]['external_ports'][0]['evidence_level']='scenario'
    o=run_engine(tmp_path,d);assert o['fitting_candidates'][0]['quantity_level']=='方案暂估量'


@pytest.mark.parametrize('bad',['duplicate','included','position','direction','unit','source','owner'])
def test_invalid_full_boundary_rejected(tmp_path,bad):
    b=full_boundary();p=b['external_ports'][0]
    if bad=='duplicate':b['external_ports'].append(copy.deepcopy(p))
    if bad=='included':p['segment_id']='A'
    if bad=='position':p['point']=[1,0,0]
    if bad=='direction':p['outside_point']=[-5,0,0]
    if bad=='unit':p['coordinate_unit']='mm'
    if bad=='source':p.pop('source_id')
    if bad=='owner':b.pop('fitting_quantity_owner')
    d=cropped_network();d['scope_boundary_nodes']=[b]
    f=tmp_path/'input.json';f.write_text(json.dumps(d),encoding='utf-8');out=tmp_path/'out.json'
    r=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'),'--input',str(f),'--output',str(out)],capture_output=True)
    assert r.returncode!=0 and not out.exists()


def omitted_line():
    a=line('A',(0,0),(2,0));b=line('B',(2.8,0),(5,0))
    for s in [a,b]:s['dn_seeds']=[dict(dn='DN25',source_id='pipe-label',evidence_level='inferred')]
    d=base([a,b]);d['parameters']['gap_max']=1
    d['gap_classifications']=[dict(a_segment='A',a_end='end',b_segment='B',b_end='start',
        classification='图中文字省略线',quantity_decision='依据图面恢复',quantity_action='restore_omission',
        quantity_evidence_level='inferred',source_id='plan:label-gap',scenario_include=True)]
    return d


def test_explicit_omission_restored_by_dn_without_changing_baseline(tmp_path):
    o=run_engine(tmp_path,omitted_line())
    assert o['metrics']['raw_length']==pytest.approx(4.2)
    assert o['restored_omission_length']==pytest.approx(.8)
    assert o['reconstructed_length']==pytest.approx(5)
    assert o['reconstructed_length_summary']['W']['规则推算量']['DN25']==pytest.approx(5)
    assert o['bridge_scenarios'][0]['quantity_level']=='规则推算量'
    assert not o['topology_bridges'][0]['quantity_included']


def test_unclassified_short_gap_not_silently_restored(tmp_path):
    d=omitted_line();d.pop('gap_classifications');o=run_engine(tmp_path,d)
    assert o['restored_omission_length']==0
    assert o['bridge_quantity_items'][0]['status']=='SCENARIO_ONLY'


def test_valve_occupancy_cannot_masquerade_as_omission(tmp_path):
    d=omitted_line();d['valves']=[dict(valve_id='V',system='W',space_id='plan',point=[2.4,0,0],
        source_id='valve-symbol',instance_level='inferred',valve_type='截止阀')]
    o=run_engine(tmp_path,d)
    assert o['restored_omission_length']==0
    assert o['bridge_quantity_items'][0]['status']=='BLOCKED_OCCUPANCY'
    assert any('占位冲突' in r['type'] for r in o['route_preparation']['issues'])


def test_unmatched_omission_declaration_is_reported(tmp_path):
    d=omitted_line();d['parameters']['gap_max']=.1;o=run_engine(tmp_path,d)
    assert o['restored_omission_length']==0
    assert any('未匹配' in r['type'] for r in o['route_preparation']['issues'])


def test_conflicting_dn_omission_length_does_not_guess_split(tmp_path):
    d=omitted_line();d['segments'][1]['dn_seeds'][0]['dn']='DN20'
    o=run_engine(tmp_path,d)
    assert o['bridge_quantity_items'][0]['dn'] is None
    assert 'DN待定' in o['reconstructed_length_summary']['W']['未决量']


def test_scenario_seed_keeps_restored_length_scenario(tmp_path):
    d=omitted_line()
    for s in d['segments']:s['dn_seeds'][0]['evidence_level']='scenario'
    o=run_engine(tmp_path,d)
    assert o['reconstructed_length_summary']['W']['方案暂估量']['DN25']==pytest.approx(5)
    assert o['bridge_scenarios'][0]['quantity_level']=='方案暂估量'


def test_tee_sizes_keep_repeated_port_dn_and_do_not_merge_distinct_sizes(tmp_path):
    rows=[]
    for prefix,x,dns in [('P',0,['DN75','DN100','DN75']),('Q',20,['DN100','DN100','DN75'])]:
        for label,a,b,dn in [('left',(x-3,0),(x,0),dns[0]),('right',(x,0),(x+3,0),dns[1]),('branch',(x-2,2),(x,0),dns[2])]:
            r=line(prefix+label,a,b);r['dn_seeds']=[dict(dn=dn,source_id='drawing:'+prefix+label,evidence_level='inferred')];rows.append(r)
    o=run_engine(tmp_path,base(rows))
    assert {f['dn_combination'] for f in o['fitting_candidates']}=={'DN75×DN75×DN100','DN75×DN100×DN100'}
    assert len(o['fitting_summary'])==2 and all(s['candidate_count']==1 for s in o['fitting_summary'])
    assert all(f['branch_angle_deg']==pytest.approx(45) and len(f['ports'])==3 for f in o['fitting_candidates'])
