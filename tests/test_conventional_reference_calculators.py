"""Reference arithmetic and stop guards of seven conventional skills; synthetic fixtures only.

Migrated from the legacy development source `_qto-shared-evidence/tests/run_conventional_candidate_tests.py`
(stage 4/5 candidate checks); fixtures are copied unchanged into fixtures/conventional_reference.
"""
import json,subprocess,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
F=Path(__file__).resolve().parent/'fixtures/conventional_reference'
SPECS=[
    ('qto-masonry-secondary','calculate_masonry_reference.py','等效墙段输入.json','洞口超限负例.json',
     [('items',0,'gross_area_m2',30),('items',0,'net_area_m2',26),('items',0,'net_volume_m3',4.8)]),
    ('qto-framing-supports','calculate_framing_reference.py','等效构件表.json','间距缺范围负例.json',
     [('items',0,'count',12),('items',0,'total_length_m',36),('items',0,'theoretical_mass_kg',30.6)]),
    ('qto-roads-siteworks','calculate_road_layers_reference.py','等效直段.json','缺层厚负例.json',
     [('items',0,'area_m2',600),('items',0,'layers',0,'volume_m3',120),('items',0,'layers',1,'volume_m3',30)]),
    ('qto-components-points','count_reconciled_instances.py','等效实例账.json','重复实例负例.json',
     [('unique_instance_count',3)]),
    ('qto-piles-groundwork','calculate_pile_groups_reference.py','等效桩组.json','缺桩径负例.json',
     [('items',0,'total_length_m',200),('items',0,'theoretical_volume_m3',100.530965)]),
    ('qto-duct-air-systems','calculate_straight_duct_reference.py','等效直管.json','缺截面负例.json',
     [('items',0,'theoretical_surface_m2',24),('items',1,'theoretical_surface_m2',15.707963)]),
    ('qto-electrical-routes','calculate_electrical_routes_reference.py','等效路径.json','缺竖向负例.json',
     [('items',0,'physical_or_conductor_length_m',50),('items',1,'physical_or_conductor_length_m',156)]),
]
IDS=[s[0] for s in SPECS]

def call(skill,script,name):
    path=F/skill/name
    assert path.is_file(),path  # a missing fixture must not pass as a stopped input
    p=subprocess.run([sys.executable,str(ROOT/'skills'/skill/'scripts'/script),str(path)],capture_output=True,text=True,encoding='utf-8')
    try:data=json.loads(p.stdout)
    except json.JSONDecodeError:data={}
    return p.returncode,data

def dig(data,path):
    for key in path:data=data[key]
    return data

@pytest.mark.parametrize('skill,script,positive,negative,expects',SPECS,ids=IDS)
def test_equivalent_input_stays_reference_only(skill,script,positive,negative,expects):
    code,data=call(skill,script,positive)
    assert code==0 and data.get('status')=='REFERENCE_ONLY' and data.get('business_adoption_allowed') is False
    for *path,expected in expects:assert dig(data,path)==pytest.approx(expected,abs=1e-6)

@pytest.mark.parametrize('skill,script,positive,negative,expects',SPECS,ids=IDS)
def test_missing_or_invalid_decisive_input_stops(skill,script,positive,negative,expects):
    code,data=call(skill,script,negative)
    assert code!=0 and data.get('status')=='INPUT_REQUIRED' and data.get('stop') is True and data.get('error')
