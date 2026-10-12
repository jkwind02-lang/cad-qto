"""Stable identity aliases differ from genuinely distinct repeated instances."""
import json,subprocess,sys
from pathlib import Path
import pytest
SCRIPT=Path(__file__).resolve().parents[1]/'skills/qto-duct-air-systems/scripts/calculate_straight_duct_reference.py'
def row(ident):return dict(id=ident,system='synthetic ventilation',shape='rectangle',count=1,length_each_m=2,width_m=.2,height_m=.1,evidence='synthetic distinct physical identity')
def run(tmp_path,rows):
 p=tmp_path/'input.json';p.write_text(json.dumps({'straight_sections':rows}),encoding='utf8')
 r=subprocess.run([sys.executable,str(SCRIPT),str(p)],capture_output=True,text=True,encoding='utf8');return r.returncode,json.loads(r.stdout)
@pytest.mark.parametrize('ids',[('A',' A '),(' A','A '),('\tA','A\n')])
def test_whitespace_alias_is_same_identity(tmp_path,ids):
 code,out=run(tmp_path,[row(i) for i in ids]);assert code==2 and '重复' in out['error']
def test_distinct_instances_with_identical_geometry_remain_valid(tmp_path):
 code,out=run(tmp_path,[row('A'),row('B')]);assert code==0 and len(out['items'])==2
 assert [r['total_centerline_length_m'] for r in out['items']]==[2,2]
def test_explicit_group_count_remains_valid(tmp_path):
 a=row('A');a['count']=3;code,out=run(tmp_path,[a]);assert code==0 and out['items'][0]['total_centerline_length_m']==6
