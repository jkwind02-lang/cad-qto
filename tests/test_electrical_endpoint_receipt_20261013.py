"""Engineering receipt invariants; synthetic ports do not certify recognition."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skills/qto-electrical-routes/scripts/calculate_electrical_paths.py'
spec=importlib.util.spec_from_file_location('er_receipt_test',SCRIPT);calc=importlib.util.module_from_spec(spec);spec.loader.exec_module(calc)

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')

def rebind(data,source,path):
    write(path,source);data['source_handoff']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()

@pytest.fixture
def example(tmp_path):
    terminals=[dict(terminal_id='T'+str(i),physical_id='D'+str(i),port_id='P',system='WL1',point=p,
       coordinate_unit='m',terminal_type='fixture',evidence_level='inferred',source_id='synthetic drawing',
       port_role='synthetic installation point',propagation_scope='test branch') for i,p in [(1,[0.,0.]),(2,[4.,0.])]]
    source=dict(schema_version='qto-network-endpoint-handoff/1.1',handoff_id='TEST',provider_skill='qto-components-points',consumer_skill='qto-electrical-routes',source_workpackage_complete=True,required=True,source_instance_count=2,source_physical_instance_count=2,source_endpoint_port_count=2,delivered_count=2,not_applicable_count=0,unresolved_count=0,terminals=terminals,dispositions=[dict(physical_id=t['physical_id'],port_id='P',network_disposition='deliver') for t in terminals])
    path=tmp_path/'handoff.json'
    data=dict(schema=calc.SCHEMA,unit='m',project_id='synthetic',paths=[dict(id='P',from_node='T1',to_node='T2',length_m=4.,role='horizontal',source_ids=['drawing'],from_point_m=[0.,0.],to_point_m=[4.,0.])],channels=[dict(id='C',physical_id='conduit',kind='conduit',system='WL1',spec='JDG20',start_node='T1',end_node='T2',path_ids=['P'],parallel_count=1,source_ids=['drawing'])],circuits=[dict(id='W',physical_id='three-single-wires',kind='wire',system='WL1',spec='BYJ2.5',start_node='T1',end_node='T2',path_ids=['P'],parallel_count=3,channel_ids=['C'],source_ids=['drawing'])],terminals=copy.deepcopy(terminals),source_handoff=dict(path=str(path),delivered_count=2,received_count=2))
    rebind(data,source,path);return data,source,path

def test_valid_receipt_preserves_wire_and_channel_quantities(example):
    data,_,_=example;before=copy.deepcopy(data);out=calc.calculate(data)
    assert [x['length_m'] for x in out['items']]==[4.,12.]
    r=out['endpoint_receipt'];assert r['received_count']==2 and r['coordinate_receipt_complete']
    assert r['identity_receipt_complete'] and not r['business_adoption_allowed']
    assert data==before

@pytest.mark.parametrize('mode',['hash','missing','changed'])
def test_actual_provider_freshness_required(example,mode):
    data,_,path=example
    if mode=='hash':data['source_handoff']['sha256']='0'*64
    elif mode=='missing':path.unlink()
    else:path.write_text('{}',encoding='utf8')
    with pytest.raises(ValueError):calc.calculate(data)

@pytest.mark.parametrize('mode',['remove','physical','point','system','duplicate'])
def test_terminals_must_match_saved_provider_facts(example,mode):
    data,_,_=example
    if mode=='remove':data['terminals']=[]
    elif mode=='physical':data['terminals'][0]['physical_id']='another device'
    elif mode=='point':data['terminals'][0]['point']=[9.,0.]
    elif mode=='system':data['terminals'][0]['system']='unrelated network'
    else:data['terminals'].append(copy.deepcopy(data['terminals'][0]))
    with pytest.raises(ValueError):calc.calculate(data)

def test_claimed_received_count_cannot_replace_actual_graph_binding(example):
    data,_,_=example
    data['paths'][0].update(from_node='other-A',to_node='other-B')
    for r in data['channels']+data['circuits']:r.update(start_node='other-A',end_node='other-B')
    with pytest.raises(ValueError,match='graph-bound'):calc.calculate(data)
    del data['source_handoff']['received_count'];out=calc.calculate(data)
    assert out['status']=='REVIEW_REQUIRED' and out['endpoint_receipt']['received_count']==0
    assert out['endpoint_receipt']['unbound_terminal_ids']==['T1','T2']

def test_port_physical_unit_conversion_is_valid(example):
    data,_,_=example
    for t in data['terminals']:t['point']=[v*1000 for v in t['point']];t['coordinate_unit']='mm'
    assert calc.calculate(data)['endpoint_receipt']['coordinate_receipt_complete']

def test_graph_coordinates_cannot_be_unrelated_to_received_port(example):
    data,_,_=example;data['paths'][0]['to_point_m']=[40.,0.]
    with pytest.raises(ValueError,match='coordinate differs'):calc.calculate(data)

def test_missing_path_coordinates_not_claimed_as_verified(example):
    data,_,_=example
    for k in ['from_point_m','to_point_m']:del data['paths'][0][k]
    r=calc.calculate(data)['endpoint_receipt'];assert r['identity_receipt_complete']
    assert not r['coordinate_receipt_complete'] and len(r['coordinate_unchecked_terminal_ids'])==2

def test_two_ports_one_device_not_two_physical_instances(example):
    data,source,path=example
    data['terminals'][1].update(physical_id='D1',port_id='P2');source['terminals']=copy.deepcopy(data['terminals'])
    source['dispositions'][1].update(physical_id='D1',port_id='P2');source['source_physical_instance_count']=1
    rebind(data,source,path);r=calc.calculate(data)['endpoint_receipt']
    assert r['source_physical_instance_count']==1 and r['source_endpoint_port_count']==2

def test_partial_provider_retains_safe_quantities_and_review_state(example):
    data,source,path=example;source.update(source_instance_count=3,source_endpoint_port_count=3,source_physical_instance_count=3,unresolved_count=1)
    source['dispositions'].append(dict(physical_id='D3',port_id='P',network_disposition='unresolved'))
    rebind(data,source,path);out=calc.calculate(data)
    assert out['status']=='REVIEW_REQUIRED' and out['endpoint_receipt']['source_unresolved_count']==1
    assert [x['length_m'] for x in out['items']]==[4.,12.]

def test_other_consumer_handoff_not_reused(example):
    data,source,path=example;source['consumer_skill']='qto-pipe-networks';rebind(data,source,path)
    with pytest.raises(ValueError,match='consumer'):calc.calculate(data)

def test_clients_without_handoff_keep_original_output_contract(example):
    data,_,_=example;del data['source_handoff'];del data['terminals']
    out=calc.calculate(data);assert 'endpoint_receipt' not in out and out['status']=='REFERENCE_ONLY'

def test_declared_terminals_not_silently_ignored_without_provider(example):
    data,_,_=example;del data['source_handoff']
    with pytest.raises(ValueError,match='source_handoff'):calc.calculate(data)
