"""Legacy route identities must not silently duplicate physical quantities."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/qto-electrical-routes/scripts/calculate_electrical_routes_reference.py'

def row(ident='A'):
    return dict(id=ident,kind='wire',system='grounding',spec='specified conductor',
                path_length_m=3.25,parallel_count=1,extra_each_m=.5,evidence='synthetic explicit path')

def run(tmp_path, data):
    source=tmp_path/'input.json'
    source.write_text(json.dumps(data),encoding='utf-8')
    p=subprocess.run([sys.executable,str(SCRIPT),str(source)],capture_output=True,text=True,encoding='utf-8')
    return p.returncode,json.loads(p.stdout)

@pytest.mark.parametrize('ident', ['', '   ', None, 3, True, [], {}])
def test_route_identity_must_be_nonempty_text(tmp_path,ident):
    code,out=run(tmp_path,{'route_items':[row(ident)]})
    assert code==2 and out['status']=='INPUT_REQUIRED'
    assert 'id' in out['error']

@pytest.mark.parametrize('ids', [('A','A'),('A',' A '),(' A','A ')])
def test_duplicate_stable_route_rejected(tmp_path,ids):
    code,out=run(tmp_path,{'route_items':[row(ids[0]),row(ids[1])]})
    assert code==2 and out['status']=='INPUT_REQUIRED'
    assert '重复' in out['error']

def test_distinct_parallel_conductors_remain_valid(tmp_path):
    a=row('wire-1');b=row('wire-2');b['parallel_count']=3
    code,out=run(tmp_path,{'route_items':[a,b]})
    assert code==0
    assert [x['physical_or_conductor_length_m'] for x in out['items']]==[3.75,11.25]
    assert [x['id'] for x in out['items']]==['wire-1','wire-2']

def test_valid_output_contract_is_unchanged(tmp_path):
    a=row();code,out=run(tmp_path,{'route_items':[a]})
    expected={k:a[k] for k in ['id','kind','system','spec','parallel_count','evidence']}
    expected['physical_or_conductor_length_m']=3.75
    assert code==0 and out==dict(status='REFERENCE_ONLY',business_adoption_allowed=False,explicit_paths_only=True,items=[expected])

def test_path_ledger_forwarding_retains_identity_check(tmp_path):
    path=dict(id='p',from_node='a',to_node='b',length_m=2,role='horizontal',source_ids=['synthetic'])
    channel=dict(id='tray',physical_id='physical-tray',kind='tray',system='distribution',spec='specified tray',
                 path_ids=['p'],start_node='a',end_node='b',parallel_count=1,source_ids=['synthetic'])
    data=dict(schema='qto-electrical-path-ledger/1.0',unit='m',project_id='synthetic',paths=[path],channels=[channel],circuits=[])
    code,out=run(tmp_path,data)
    assert code==0 and out['items'][0]['length_m']==2
    data['channels'].append(copy.deepcopy(channel))
    code,out=run(tmp_path,data)
    assert code==2 and out['status']=='INPUT_REQUIRED'
