"""Independent engineering cases for path identities, interfaces and units."""
import copy,json,math,subprocess,sys,importlib.util
from pathlib import Path
import pytest
from test_manual_routes import ROOT,base,line,junction,run_engine,graph,conic
import qto_pipe_topology as t
from recompute_qto_dependencies import recalculate,check_result_dependencies
spec=importlib.util.spec_from_file_location('electrical_paths',ROOT/'skills/qto-electrical-routes/scripts/calculate_electrical_paths.py')
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
def poly(sid,points):
    parts=[{'kind':'line','start':t.xyz(a),'end':t.xyz(b)} for a,b in zip(points,points[1:])]
    r=line(sid,points[0],points[-1]);r['geometry']=t.route_geometry(parts);r['length']=sum(math.dist(p['start'],p['end']) for p in parts)
    return r
def test_alternative_vertex_references_preserve_single_junction(tmp_path):
    d=base([poly('P',[(0,0),(3,0),(3,4)]),line('B',(3,0),(5,0))])
    d['junctions']=[junction({'segment_id':'P','part_index':0,'parameter':1},{'segment_id':'B','end':'start'})]
    o=run_engine(tmp_path,d);assert o['metrics']['raw_length']==9
    assert o['metrics']['node_count']==4 and len(o['fitting_candidates'])==1
def test_conflicting_aliases_of_same_vertex_rejected():
    d=base([poly('P',[(0,0),(3,0),(3,4)]),line('B',(3,0),(5,0)),line('C',(3,0),(3,-2))])
    d['junctions']=[junction({'segment_id':'P','part_index':0,'parameter':1},{'segment_id':'B','end':'start'},jid='J1'),junction({'segment_id':'P','part_index':1,'parameter':0},{'segment_id':'C','end':'start'},decision='separate',jid='J2')]
    with pytest.raises(ValueError,match='覆盖'):t.prepare_network(d)
def test_closed_curve_seam_can_join_without_breaking_loop(tmp_path):
    part=conic(0,math.tau,(10,0,0),(0,10,0));p=line('C',(10,0),(10,0));p.update(geometry=t.route_geometry([part]),length=20*math.pi)
    d=base([p,line('B',(10,0),(12,0))]);d['junctions']=[junction({'segment_id':'C','end':'start'},{'segment_id':'B','end':'start'})]
    o=run_engine(tmp_path,d);assert o['metrics']['raw_length']==pytest.approx(20*math.pi+2)
    assert o['metrics']['node_count']==3
def test_one_known_contact_does_not_hide_second_contact_same_routes():
    d=base([line('M',(0,0),(10,0)),poly('U',[(2,0),(2,2),(8,2),(8,0)])])
    d['junctions']=[junction({'segment_id':'M','point':[2,0]},{'segment_id':'U','end':'start'})]
    _,report=t.prepare_network(d)
    assert any(i.get('point')==[8,0,0] and '他管中部' in i['type'] for i in report['issues'])
def test_interior_crossing_candidate_is_not_automatic_connection(tmp_path):
    d=base([line('A',(-4,0),(4,0)),line('B',(0,-3),(0,3))]);o=run_engine(tmp_path,d)
    assert o['metrics']['node_count']==4 and not o['fitting_candidates']
    assert any('图内交叉' in i['type'] for i in o['route_preparation']['issues'])
def test_different_height_interior_crossing_is_not_contact():
    d=base([line('A',(-4,0,0),(4,0,0)),line('B',(0,-3,2),(0,3,2))]);_,r=t.prepare_network(d)
    assert not any('交叉' in i['type'] for i in r['issues'])
def test_collinear_overlap_exposes_possible_duplicate():
    _,r=t.prepare_network(base([line('A',(0,0),(10,0)),line('B',(3,0),(8,0))]))
    assert any('重叠' in i['type'] for i in r['issues'])
def test_long_dependency_chain_is_not_limited_by_python_recursion():
    d=graph();d['nodes']=d['nodes'][:1]
    for i in range(1500):d['nodes'].append({'id':f'n{i}','unit':'m','variables':{'x':'low' if i==0 else f'n{i-1}'},'expression':'x*2/2','rule_ids':['geometry']})
    o=recalculate(d);assert o['nodes'][-1]['value']==3 and o['computed_node_count']==1501
def test_multiport_producer_preserves_space_and_segment_binding(tmp_path):
    ports=[dict(port_id=k,endpoint_id='EP-'+k,network_disposition='deliver',point=p,space_id=space,segment_id=segment,coordinate_unit='m',port_role='terminal',propagation_scope='terminal_to_first_junction',evidence_level='confirmed') for k,p,space,segment in [('in',[0,0,0],'floor-1','A'),('out',[0,0,0],'floor-2','B')]]
    data={'schema_version':'qto-network-endpoint-handoff-input/1.1','handoff_id':'H','source_id':'D','consumer_skill':'qto-pipe-networks','instances':[{'physical_id':'equipment-1','category':'设备','system':'W','evidence':'图纸设备编号和端口详图','network_ports':ports}]}
    source=tmp_path/'ports.json';out=tmp_path/'handoff.json';source.write_text(json.dumps(data),encoding='utf-8')
    p=subprocess.run([sys.executable,str(ROOT/'skills/qto-components-points/scripts/build_network_endpoint_handoff.py'),'--input',str(source),'--output',str(out)],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stdout+p.stderr
    h=json.loads(out.read_text(encoding='utf-8'))
    assert h['source_physical_instance_count']==1 and h['source_instance_count']==h['source_endpoint_port_count']==2
    assert [x['space_id'] for x in h['terminals']]==['floor-1','floor-2']
    assert [x['segment_id'] for x in h['terminals']]==['A','B']
    d=base([line('A',(0,0),(4,0),'floor-1'),line('B',(0,0),(4,0),'floor-2')]);d['terminals']=h['terminals'];d['endpoint_handoff']=h
    result=run_engine(tmp_path/'network',d);assert len(result['terminals'])==2
    assert len({x['node_id'] for x in result['terminals']})==2
def electrical():
    p=[{'id':'H','from_node':'A','to_node':'B','length_m':10,'role':'horizontal','source_ids':['plan']},{'id':'V','from_node':'B','to_node':'C','length_m':3,'role':'vertical','source_ids':['section']},{'id':'O','from_node':'C','to_node':'D','length_m':2,'role':'offset','source_ids':['terminal-detail']}]
    common={'system':'配电','spec':'本样本明确规格','source_ids':['plan-and-circuit-schedule'],'start_node':'A','parallel_count':1}
    channels=[dict(common,id='TRAY',physical_id='tray-1',kind='tray',end_node='B',path_ids=['H'])]
    circuits=[dict(common,id='CB',physical_id='cable-group',kind='cable',end_node='D',path_ids=['H','V','O'],parallel_count=2,cable_core_count=3,extras=[{'id':'EX','node_id':'D','basis':'本样本端头明确预留1m','length_each_m':1,'source_ids':['detail']}]),dict(common,id='WIRE',physical_id='wire-group',kind='wire',end_node='B',path_ids=['H'],parallel_count=5,channel_ids=['TRAY'])]
    return {'schema':e.SCHEMA,'unit':'m','project_id':'electrical-synthetic','paths':p,'channels':channels,'circuits':circuits}
def test_shared_channel_not_multiplied_by_circuit_or_core_count():
    o=e.calculate(electrical());byid={r['id']:r for r in o['items']}
    assert byid['TRAY']['length_m']==10 and byid['CB']['length_m']==32 and byid['WIRE']['length_m']==50
    assert byid['CB']['length_components_each_m']=={'horizontal':10,'vertical':3,'offset':2}
    assert byid['CB']['cable_core_count']==3 and o['business_adoption_allowed'] is False
    assert o['shared_path_usage']['H']['channel_ids']==['TRAY']
    assert set(o['shared_path_usage']['H']['circuit_ids'])=={'CB','WIRE'}
@pytest.mark.parametrize('fault',['order','end','repeat','missing','physical','extra','count','unit','source','coverage','core'])
def test_electrical_invalid_ledger_rejected(fault):
    d=electrical();r=d['circuits'][0]
    if fault=='order':r['path_ids']=['V','H','O']
    if fault=='end':r['end_node']='B'
    if fault=='repeat':r['path_ids']=['H','H','V','O']
    if fault=='missing':r['path_ids']=['X']
    if fault=='physical':r['physical_id']='tray-1'
    if fault=='extra':r['extras'][0]['node_id']='unrelated'
    if fault=='count':r['parallel_count']=True
    if fault=='unit':d['unit']='mm'
    if fault=='source':d['paths'][0]['source_ids']=[]
    if fault=='coverage':r['channel_ids']=['TRAY']
    if fault=='core':r['cable_core_count']=0
    with pytest.raises(ValueError):e.calculate(d)
def test_electrical_unused_path_remains_visible():
    d=electrical();d['paths'].append({'id':'spare','from_node':'X','to_node':'Y','length_m':5,'role':'horizontal','source_ids':['plan']})
    o=e.calculate(d);assert o['unused_path_ids']==['spare'] and o['status']=='REVIEW_REQUIRED'
def test_existing_electrical_entrypoint_accepts_new_ledger(tmp_path):
    source=tmp_path/'electric.json';source.write_text(json.dumps(electrical()),encoding='utf-8')
    p=subprocess.run([sys.executable,str(ROOT/'skills/qto-electrical-routes/scripts/calculate_electrical_routes_reference.py'),str(source)],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stdout+p.stderr
    assert json.loads(p.stdout)['schema']=='qto-electrical-path-result/1.0'
def test_mismatched_endpoint_coordinate_units_rejected():
    d=base([line('A',(0,0),(10,0))]);d['terminals']=[{'coordinate_unit':'mm'}]
    with pytest.raises(ValueError,match='单位'):t.prepare_network(d)
def test_long_result_dependency_chain_does_not_overflow():
    rows=[{'成果项编号':f'R{i}','结果':{'数值':1},'成果分层':{'依赖成果项':[] if i==0 else [f'R{i-1}'],'进入正式量':False}} for i in range(1500)]
    assert check_result_dependencies({'工程量结果':rows})==[]
    rows[0]['成果分层']['依赖成果项']=['R1499']
    assert any('成环' in s for s in check_result_dependencies({'工程量结果':rows}))
