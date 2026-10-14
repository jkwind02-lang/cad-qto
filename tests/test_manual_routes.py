"""Synthetic engineering cases through production preparation and engine CLI.
Expected lengths/counts are independently hand-calculated, not prior-run totals.
These tests do not claim real-drawing recognition accuracy.
"""
from __future__ import annotations
import copy, importlib.util, json, math, random, subprocess, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
import qto_cad_geometry as g
import qto_pipe_topology as t
from qto_spatial import PointIndex,BoundsIndex,nearby_pairs
from recompute_qto_dependencies import recalculate,check_result_dependencies,digest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from review_evidence_helpers import write_review


def line(sid,a,b,space='plan',system='W',**extra):
    a=t.xyz(a);b=t.xyz(b)
    return dict(segment_id=sid,start=a,end=b,length=math.dist(a,b),system=system,space_id=space,source_ids=['drawing:'+sid],dn_seeds=[],**extra)


def base(rows):
    n=len(rows)
    return {'schema_version':'qto-pipe-network-inference-input/1.2','network_id':'hand-calculated-case','unit':'m','segments':rows,
        'object_selection':{'audit_id':'selection','status':'READY','candidate_count':n,'included_count':n,'excluded_count':0,'unresolved_count':0,
            'included_segment_ids':[r['segment_id'] for r in rows],'source_ids':['object-review']},
        'endpoint_handoff':{'handoff_id':'ports','required':False,'source_instance_count':0,'delivered_count':0,'not_applicable_count':0,'unresolved_count':0,
            'source_id':'port-review','reason':'本样本没有设备端口'},'parameters':{'snap_tolerance':.001,'gap_max':.1}}


def junction(*members,decision='connect',jid='J'):
    return {'junction_id':jid,'decision':decision,'members':list(members),'basis':'人工基准：图例及连接详图已对应','source_ids':['detail:J']}


def run_engine(tmp_path,data):
    tmp_path.mkdir(parents=True,exist_ok=True)
    source=tmp_path/'input.json';dest=tmp_path/'output.json';source.write_text(json.dumps(data),encoding='utf-8')
    p=subprocess.run([sys.executable,str(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'),'--input',str(source),'--output',str(dest)],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stdout+p.stderr
    out=json.loads(dest.read_text(encoding='utf-8'))
    assert out['business_adoption_allowed'] is False
    assert out['route_preparation']['length_delta']==pytest.approx(0,abs=1e-7)
    return out


def tee():
    d=base([line('main',(0,0),(10,0)),line('branch',(5,0),(5,3))])
    d['junctions']=[junction({'segment_id':'main','point':[5,0]},{'segment_id':'branch','end':'start'})]
    return d


def test_tee_splits_main_without_losing_or_adding_length(tmp_path):
    o=run_engine(tmp_path,tee())
    assert o['metrics']['raw_length']==13
    assert o['metrics']['source_route_count']==2 and o['metrics']['segment_count']==3
    assert len(o['route_preparation']['parent_map']['main'])==2
    assert [r['type'] for r in o['fitting_candidates']]==['三通候选']
    assert o['route_preparation']['source_object_selection']['included_count']==2
    assert list(tmp_path.glob('*复核摘要.md'))


def test_unknown_tee_is_exposed_not_silently_connected(tmp_path):
    d=tee();d.pop('junctions');o=run_engine(tmp_path,d)
    assert o['metrics']['segment_count']==2 and not o['fitting_candidates']
    assert any('他管中部' in r['type'] for r in o['route_preparation']['issues'])
    assert o['metrics']['raw_length']==13


@pytest.mark.parametrize('decision',['separate','unresolved'])
def test_separate_or_undecided_crossing_has_no_tee(tmp_path,decision):
    d=tee();d['junctions'][0]['decision']=decision;o=run_engine(tmp_path,d)
    assert not o['fitting_candidates']
    assert o['metrics']['raw_length']==13


@pytest.mark.parametrize('decision,count',[('connect',1),('separate',0)])
def test_crossing_four_way_requires_connection_evidence(tmp_path,decision,count):
    d=base([line('A',(-4,0),(4,0)),line('B',(0,-3),(0,3))]);d['junctions']=[junction({'segment_id':'A','point':[0,0]},{'segment_id':'B','point':[0,0]},decision=decision)]
    o=run_engine(tmp_path,d);assert o['metrics']['raw_length']==14
    assert len(o['fitting_candidates'])==count


@pytest.mark.parametrize('kind',['space','height','system'])
def test_coincident_plan_positions_do_not_merge_distinct_contexts(tmp_path,kind):
    rows=[line('A',(0,0,0),(4,0,0)),line('B',(0,0,0),(0,3,0))]
    if kind=='space':rows[1]['space_id']='level-2'
    elif kind=='height':rows[1]=line('B',(0,0,3),(0,3,3))
    else:rows[1]['system']='return-water'
    o=run_engine(tmp_path,base(rows));assert o['metrics']['node_count']==4
    assert not o['fitting_candidates']


def cross_view():
    d=base([line('floor1',(0,0),(10,0),space='F1'),line('floor2',(1000,500),(1010,500),space='F2')])
    d['cross_view_links']=[{'link_id':'riser:F1-F2','physical_id':'R1:F1-F2','a':{'segment_id':'floor1','end':'end'},'b':{'segment_id':'floor2','end':'start'},
        'basis':'立管编号R1及系统图上下层接口对应','source_ids':['system:R1'],'route_is_vertical':True,'from_elevation':3.,'to_elevation':7.2,
        'elevation_unit':'m','height_source_ids':['section:levels'],'height_level':'confirmed','dn_continuity':False}]
    return d


def test_cross_view_riser_uses_height_not_page_distance(tmp_path):
    o=run_engine(tmp_path,cross_view());assert o['metrics']['raw_length']==20
    assert o['vertical_quantities'][0]['value']==pytest.approx(4.2)
    assert o['metrics']['cross_view_link_count']==1
    assert o['metrics']['virtual_bridge_length']==0
    assert not o['fitting_candidates']


def test_cross_view_no_height_keeps_other_work(tmp_path):
    d=cross_view();d['cross_view_links'][0].pop('from_elevation');o=run_engine(tmp_path,d)
    assert o['vertical_quantities'][0]['value'] is None
    assert o['metrics']['raw_length']==20
    assert o['metrics']['vertical_unresolved_count']==1


def test_cross_view_existing_quantity_owner_not_counted_twice(tmp_path):
    d=cross_view();r=line('vertical',(20,0,3),(20,0,7.2),space='section',physical_id='R1:F1-F2');d['segments'].append(r)
    d['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['floor1','floor2','vertical'])
    d['cross_view_links'][0]['quantity_owner_segment_id']='vertical'
    o=run_engine(tmp_path,d);assert o['metrics']['raw_length']==pytest.approx(24.2)
    assert o['vertical_quantities'][0]['counted_elsewhere'] is True


@pytest.mark.parametrize('mutation',['duplicate','different-system','unproved-dn','unit'])
def test_cross_view_rejects_invalid_engineering_contract(mutation):
    d=cross_view()
    if mutation=='duplicate':d['cross_view_links'].append(copy.deepcopy(d['cross_view_links'][0]))
    if mutation=='different-system':d['segments'][1]['system']='other'
    if mutation=='unproved-dn':d['cross_view_links'][0]['dn_continuity']=True
    if mutation=='unit':d['cross_view_links'][0]['elevation_unit']='mm'
    with pytest.raises(ValueError):t.prepare_network(d)


def test_polyline_turns_are_not_shortcut_chords(tmp_path):
    parts=[{'kind':'line','start':[0,0,0],'end':[3,0,0]},{'kind':'line','start':[3,0,0],'end':[3,4,0]}]
    d=base([line('L',(0,0),(3,4))]);d['segments'][0].update(geometry=t.route_geometry(parts),length=7)
    o=run_engine(tmp_path,d);assert o['metrics']['raw_length']==7 and o['metrics']['segment_count']==2
    assert len(o['fitting_candidates'])==1


def conic(start=0,sweep=math.pi/2,u=(10,0,0),v=(0,10,0)):
    return {'kind':'ellipse_arc','center':[0,0,0],'u':list(u),'v':list(v),'start_param':start,'sweep_param':sweep}


def curved(sid,part):
    return {'segment_id':sid,'system':'W','space_id':'plan','start':t.part_point(part,0),'end':t.part_point(part,1),
            'geometry':t.route_geometry([part]),'length':g.measure_part(part)[0],'source_ids':['curve:'+sid],'dn_seeds':[]}


def test_arc_midpoint_branch_uses_exact_curve_tangents(tmp_path):
    p=conic();pt=t.part_point(p,.5);b=line('branch',pt,[pt[0]+2,pt[1]+2,0]);d=base([curved('arc',p),b]);d['junctions']=[junction({'segment_id':'arc','parameter':.5},{'segment_id':'branch','end':'start'})]
    o=run_engine(tmp_path,d);assert o['metrics']['raw_length']==pytest.approx(5*math.pi+math.sqrt(8))
    assert [r['type'] for r in o['fitting_candidates']]==['三通候选']


def test_closed_circle_can_be_network_without_fake_elbows(tmp_path):
    d=base([curved('ring',conic(sweep=math.tau))]);o=run_engine(tmp_path,d)
    assert o['metrics']['raw_length']==pytest.approx(20*math.pi)
    assert not o['fitting_candidates']
    assert all(r['role']=='环网' for r in o['segments'])


def test_reducer_splits_and_stops_dn_propagation(tmp_path):
    r=line('P',(0,0),(10,0));r['dn_seeds']=[{'dn':'DN50','point':[2,0],'source_id':'label1','evidence_level':'confirmed'},
        {'dn':'DN25','point':[8,0],'source_id':'label2','evidence_level':'confirmed'}]
    d=base([r]);d['change_nodes']=[{'change_id':'reducer','system':'W','point':[5,0],'segment_id':'P','source_id':'detail:reducer','evidence_level':'confirmed'}]
    o=run_engine(tmp_path,d);assert sorted(r['dn'] for r in o['segments'])==['DN25','DN50']
    assert all(r['length']==5 for r in o['segments'])


def test_unlocated_seed_after_split_does_not_spread_everywhere(tmp_path):
    d=tee();d['segments'][0]['dn_seeds']=[{'dn':'DN50','source_id':'label:unknown-scope','evidence_level':'confirmed'}]
    o=run_engine(tmp_path,d);assert all(r['dn'] is None for r in o['segments'])
    assert any('规格适用' in i['type'] for i in o['route_preparation']['issues'])


def test_evidence_scoped_whole_object_seed_survives_split(tmp_path):
    d=tee();d['segments'][0]['dn_seeds']=[{'dn':'DN50','source_id':'label:whole-main','evidence_level':'confirmed','applies_to_entire_object':True,'scope_source_id':'system:main-constant-size'}]
    o=run_engine(tmp_path,d);main=[r for r in o['segments'] if r['parent_segment_id']=='main'];assert all(r['dn']=='DN50' for r in main)


def test_unbound_terminal_does_not_abort_unrelated_lengths(tmp_path):
    d=base([line('main',(0,0),(10,0))]);d['terminals']=[{'terminal_id':k,'system':'W','point':p,'terminal_type':'tap','evidence_level':'confirmed','source_id':'symbol:'+k} for k,p in [('ok',[10,0,0]),('unbound',[100,100,0])]]
    d['endpoint_handoff'].update(required=True,source_instance_count=2,source_endpoint_port_count=2,source_physical_instance_count=2,delivered_count=2)
    o=run_engine(tmp_path,d);assert len(o['terminals'])==1 and o['endpoint_handoff']['unresolved_count']==1
    assert o['metrics']['raw_length']==10


def test_valve_near_pipe_is_only_candidate_not_connection(tmp_path):
    r=line('main',(0,0),(10,0));r['dn_seeds']=[{'dn':'DN40','source_id':'DN40-label','evidence_level':'confirmed'}]
    d=base([r]);d['parameters']['valve_attach_max']=.5
    d['valves']=[{'valve_id':'V','system':'W','point':[5,.2,0],'instance_level':'confirmed','valve_type':'闸阀','source_id':'legend:V'}]
    o=run_engine(tmp_path,d);assert o['valves'][0]['dn'] is None and o['valves'][0]['nearby_dn_candidates']==['DN40']


def test_valve_on_arc_does_not_use_chord_distance(tmp_path):
    p=conic();r=curved('arc',p);r['dn_seeds']=[{'dn':'DN32','source_id':'label','evidence_level':'confirmed'}]
    d=base([r]);d['valves']=[{'valve_id':'V','system':'W','point':t.part_point(p,.5),'source_id':'valve-symbol','valve_type':'闸阀'}]
    o=run_engine(tmp_path,d);assert o['valves'][0]['dn']=='DN32'


@pytest.mark.parametrize('seed',[1,7,19])
@pytest.mark.parametrize('radius',[0.,.1,1.])
def test_spatial_candidates_equal_brute_force(seed,radius):
    rng=random.Random(seed);pts=[(rng.uniform(-2,2),rng.uniform(-2,2)) for _ in range(100)]+[(0.,0.),(0.,0.)];groups=[i%2 for i in range(len(pts))]
    expected={(i,j) for i in range(len(pts)) for j in range(i+1,len(pts)) if groups[i]==groups[j] and math.dist(pts[i],pts[j])<=radius}
    assert set(nearby_pairs(pts,radius,groups))==expected


def test_sparse_large_endpoint_set_does_not_do_all_pairs():
    pts=[(i*2.,0.,0.) for i in range(20000)];index=PointIndex(pts,.01)
    assert list(index.pairs(.01))==[]
    assert index.distance_checks<60000


def test_snap_does_not_merge_unbounded_transitive_chain():
    rows={str(i):line(str(i),(x,0),(x,2)) for i,x in enumerate([0.,.009,.018])}
    es=[{'segment_id':k,'end_name':'start','point':r['start'],'system':'W'} for k,r in rows.items()]
    nodes,mapping,partitions,stats=t.make_endpoint_nodes(es,rows,.01)
    assert len(nodes)==2 and stats['rejected_transitive_pairs']


@pytest.mark.parametrize('start,sweep',[(0,math.pi/2),(3*math.pi/2,math.pi),(.2,-2.),(0,math.tau)])
def test_parametric_projection_matches_independent_dense_samples(start,sweep):
    part=conic(start,sweep,(10,2,0),(3,5,1));p=[7,4,2];dist,frac=t.project_part(p,part)
    dense=min(math.dist(p,t.part_point(part,i/20000)) for i in range(20001))
    assert dist<=dense+1e-7 and dense-dist<.002 and 0<=frac<=1


def graph():
    return {'schema':'qto-calculation-graph/1.0','project_id':'same-project','sources':[{'id':'drawing','sha256':'a'*64}],
        'rules':[{'id':'geometry','sha256':'b'*64,'status':'active'}],
        'nodes':[{'id':'low','unit':'m','value':3.,'source_ids':['drawing']},
                 {'id':'high','unit':'m','value':7.2,'source_ids':['drawing']},
                 {'id':'horizontal','unit':'m','value':20.,'source_ids':['drawing']},
                 {'id':'vertical','unit':'m','variables':{'a':'low','b':'high'},'expression':'abs(b-a)','rule_ids':['geometry']},
                 {'id':'total','unit':'m','variables':{'h':'horizontal','v':'vertical'},'expression':'h+v','rule_ids':['geometry']},
                 {'id':'radius','unit':'mm','value':50.,'source_ids':['drawing']},
                 {'id':'surface','unit':'m²','variables':{'r':'radius','l':'total'},'expression':'2*3.141592653589793*r*l','rule_ids':['geometry']}]}


def test_height_change_recomputes_transitive_quantities_and_invalidates_review(tmp_path):
    d=graph();first=recalculate(d);values={r['id']:r for r in first['nodes']};assert values['total']['value']==pytest.approx(24.2)
    # Reviews are current only with human evidence (see test_approval_evidence.py); a bare 'current' is not inherited.
    approvals=write_review(tmp_path,d['project_id'],first['nodes'])
    for node in d['nodes']:node['approval']=approvals[node['id']]
    first=recalculate(d,None,tmp_path);assert all(r['review_state']=='current' for r in first['nodes'])
    for node in d['nodes']:del node['approval']
    d['nodes'][1]['value']=8.2;second=recalculate(d,first,tmp_path,tmp_path);values={r['id']:r for r in second['nodes']}
    assert values['total']['value']==pytest.approx(25.2) and values['surface']['value']==pytest.approx(2*math.pi*.05*25.2)
    assert set(second['affected_nodes'])=={'high','vertical','total','surface'}
    assert set(second['stale_reviews'])=={'high','vertical','total','surface'}
    assert values['horizontal']['review_state']=='current'


def test_missing_height_blocks_only_dependents_not_horizontal():
    d=graph();d['nodes'][1]['value']=None;o=recalculate(d);v={r['id']:r for r in o['nodes']}
    assert v['total']['value'] is None and v['surface']['value'] is None and v['horizontal']['value']==20


def test_rule_revision_recomputes_all_dependents_even_same_values():
    d=graph();first=recalculate(d);d['rules'][0]['sha256']='c'*64;o=recalculate(d,first)
    assert set(o['affected_nodes'])=={'vertical','total','surface'}


@pytest.mark.parametrize('bad',['cycle','missing','duplicate','source'])
def test_invalid_dependency_graph_is_rejected(bad):
    d=graph()
    if bad=='cycle':d['nodes'][3]['variables']['b']='total'
    if bad=='missing':d['nodes'][3]['variables']['b']='missing'
    if bad=='duplicate':d['nodes'].append(copy.deepcopy(d['nodes'][0]))
    if bad=='source':d['nodes'][0]['source_ids']=['not-present']
    with pytest.raises(ValueError):recalculate(d)


@pytest.mark.parametrize('expr',['h+1',"__import__('os').system('echo unsafe')",'h.__class__','h[0]','h/0','h**99'])
def test_invalid_units_or_executable_expressions_never_produce_quantities(expr):
    d=graph();d['nodes'][4].update(expression=expr,variables={'h':'horizontal'});o=recalculate(d);v={r['id']:r for r in o['nodes']}
    assert v['total']['value'] is None and v['surface']['value'] is None
    assert o['errors']


def test_unreviewed_dependency_cannot_feed_formal_result():
    doc={'工程量结果':[{'成果项编号':'A','结果':{'数值':2},'成果分层':{'进入正式量':False,'依赖成果项':[]}},
                          {'成果项编号':'B','结果':{'数值':4},'成果分层':{'进入正式量':True,'依赖成果项':['A']}}]}
    assert any('未闭合' in e for e in check_result_dependencies(doc))
    doc['工程量结果'][0]['成果分层']['依赖成果项']=['B']
    assert any('成环' in e for e in check_result_dependencies(doc))


def test_recompute_cli_preserves_previous_and_writes_readable_output(tmp_path):
    src=tmp_path/'graph.json';out=tmp_path/'new.json';src.write_text(json.dumps(graph()),encoding='utf-8')
    p=subprocess.run([sys.executable,str(ROOT/'skills/_qto-shared-evidence/scripts/recompute_qto_dependencies.py'),'--input',str(src),'--output',str(out)],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stdout+p.stderr
    assert out.with_suffix('.md').exists() and json.loads(src.read_text())==graph()


def test_multiport_handoff_uses_port_count_not_equipment_count(tmp_path):
    d=base([line('P',(0,0),(10,0))]);d['terminals']=[{'terminal_id':k,'system':'W','point':p,'terminal_type':'device-port','evidence_level':'confirmed','source_id':'equipment:'+k} for k,p in [('in',[0,0,0]),('out',[10,0,0])]]
    d['endpoint_handoff'].update(required=True,source_instance_count=2,source_physical_instance_count=1,source_endpoint_port_count=2,delivered_count=2)
    o=run_engine(tmp_path,d);assert len(o['terminals'])==2
    result=tmp_path/'result.json';result.write_text(json.dumps({'契约版本':'qto-result-minimum/1.0','本次规则清单':[],'工程量结果':[],'整体未决事项':[]}),encoding='utf-8')
    p=subprocess.run([sys.executable,str(ROOT/'skills/qto-pipe-networks/scripts/validate_pipe_result.py'),'--result',str(result),'--inference',str(tmp_path/'output.json')],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stdout+p.stderr
