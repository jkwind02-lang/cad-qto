"""Synthetic reproductions of structural-steel project feedback; no project files."""
import copy
import hashlib
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'skills/qto-structural-steel/scripts'
sys.path.insert(0,str(S))
spec=importlib.util.spec_from_file_location('steel_feedback_calculator',S/'calculate_structural_steel_items.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

@pytest.fixture
def manifest(tmp_path):
    p=tmp_path/'synthetic.txt';p.write_text('Synthetic drawing, properties and geometry for regression only.',encoding='utf-8')
    source=dict(source_id='SRC-1',path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),file_role='text',source_role='project_document',source_type='合成回归文本',identity_note='synthetic',reason='regression',neutral_facts='synthetic dimensions',unused_impact='none')
    evidence=[]
    for eid,klass in [('EV-OBJ','object_geometry'),('EV-CONTEXT','object_geometry'),('EV-MAT','material_property'),('EV-RULE','rule_clause')]:
        e=dict(evidence_id=eid,source_id='SRC-1',evidence_class=klass,fact_status='verified',source_kind='合成回归',fact='synthetic',locator=eid,method='fixture',quality='test',limitation='not a real project')
        if klass=='object_geometry':e['object_keys']=[eid]
        evidence.append(e)
    item=dict(item_id='QTY-A',name='合成构件',method='built_up_section_length',object_type='main_member',unit='t',scope_status='complete',mass_inclusion='permanent',adoption_role='adopted',physical_object_id='A',measurement_basis='net section',specification='synthetic H',output_dimension='mass',evidence_ids=['EV-OBJ','EV-MAT','EV-RULE'],identity_evidence_ids=['EV-OBJ'],rule_ids=['R-1'],unresolved_ids=[],cross_section_area_mm2=9600,length_total_m=2,density_kg_m3=7850,density_basis={'evidence_id':'EV-MAT','basis':'fixture parameter'},section_geometry={'kind':'welded_h','height_mm':400,'width_mm':200,'web_mm':8,'flange_mm':16,'evidence_ids':['EV-OBJ']})
    # 2*200*16+(400-32)*8 = 9344, not 9600.
    item['cross_section_area_mm2']=9344
    return dict(manifest_version=c.MANIFEST_VERSION,case_id='synthetic',scope=dict(project='synthetic',building='B',area='A',drawing_type='test',drawing_version='1',output_dimension='mass'),sources=[source],evidence=evidence,rules=[dict(rule_id='R-1',name='arithmetic',source='fixture',maturity='试运行',decision='采用',execution='calculated',evidence_ids=['EV-RULE'])],items=[item],unresolved=[],summary_requests=[],note='synthetic regression only')

def add_summary(d,**kw):
    s=dict(summary_id='QTY-S',name='构件小计',item_ids=[x['item_id'] for x in d['items']],unit='t',object_type='main_member',scope_status='complete',unresolved_ids=[],scope_coverage_closed=True,mass_inclusion='permanent',adoption_role='adopted',specification='synthetic',output_dimension='mass');s.update(kw);d['summary_requests']=[s]

def make_partial(d):
    d['items'][0].update(scope_status='partial',unresolved_ids=['U-DEPTH'])
    d['unresolved']=[dict(unresolved_id='U-DEPTH',type='dimension',description='minimum only',impact='unknown addition',required_input='actual dimension',handling='partial')]

def test_section_arithmetic_and_derivation_preserved(manifest):
    e,_,_=c.calculate(manifest)
    assert e['items'][0]['value']==pytest.approx(9344*2*7850/1e9)
    assert e['items'][0]['professional_check']['输入字段']['dimension_derivations']['cross_section_area_mm2']['value']==9344

def test_can_derive_missing_numeric_area(manifest):
    del manifest['items'][0]['cross_section_area_mm2']
    assert c.calculate(manifest)[0]['items'][0]['value']==pytest.approx(.1467008)

@pytest.mark.parametrize('kind,height,width,web,flange',[
    ('welded_h',400,200,201,16),('welded_box',400,200,100,16),('welded_box',400,200,8,200),('rolled_h',400,200,8,16),('welded_h',float('nan'),200,8,16),('welded_h',True,200,8,16)])
def test_invalid_section_is_rejected(manifest,kind,height,width,web,flange):
    manifest['items'][0]['section_geometry'].update(kind=kind,height_mm=height,width_mm=width,web_mm=web,flange_mm=flange)
    with pytest.raises(c.CalculationError):c.calculate(manifest)

def test_declared_area_conflict(manifest):
    manifest['items'][0]['cross_section_area_mm2']=9600
    with pytest.raises(c.CalculationError,match='声明值'):c.calculate(manifest)

def test_center_span_deductions_recomputed(manifest):
    i=manifest['items'][0];del i['length_total_m'];i['length_derivation']=dict(basis='support center to center',span_m=4,deductions=[dict(label='left center to cut',value_m=.4),dict(label='right center to cut',value_m=.4)],evidence_ids=['EV-OBJ'])
    assert c.calculate(manifest)[0]['items'][0]['value']==pytest.approx(9344*3.2*7850/1e9)
    i['length_total_m']=2.8
    with pytest.raises(c.CalculationError,match='length_total_m声明值'):c.calculate(manifest)

def test_partial_member_cannot_become_complete_summary(manifest):
    make_partial(manifest);add_summary(manifest)
    with pytest.raises(c.CalculationError,match='完整汇总不得包含部分量'):c.calculate(manifest)

def test_partial_summary_cannot_drop_member_issue(manifest):
    make_partial(manifest);add_summary(manifest,scope_status='partial',unresolved_ids=[])
    with pytest.raises(c.CalculationError,match='成员未决'):c.calculate(manifest)

def test_partial_summary_retains_value_and_issue(manifest):
    make_partial(manifest);add_summary(manifest,scope_status='partial',unresolved_ids=['U-DEPTH'])
    result=c.calculate(manifest)[2]['工程量结果'][-1]
    assert result['状态']=='部分计算' and result['未决事项']==['U-DEPTH']

@pytest.mark.parametrize('eid',['EV-MAT','EV-RULE'])
def test_indirect_candidate_dependency_cannot_be_adopted(manifest,eid):
    manifest['items'][0]['evidence_ids'].remove(eid)
    next(e for e in manifest['evidence'] if e['evidence_id']==eid)['fact_status']='candidate'
    with pytest.raises(c.CalculationError,match='候选或测试证据'):c.calculate(manifest)

def test_used_indirect_evidence_is_in_output(manifest):
    manifest['items'][0]['evidence_ids']=['EV-OBJ']
    assert set(c.calculate(manifest)[2]['工程量结果'][0]['来源'])=={'EV-OBJ','EV-MAT','EV-RULE'}

def test_extra_context_cannot_hide_identity_overlap(manifest):
    other=copy.deepcopy(manifest['items'][0]);other.update(item_id='QTY-B',physical_object_id='alias',identity_evidence_ids=['EV-OBJ','EV-CONTEXT']);other['evidence_ids'].append('EV-CONTEXT');manifest['items'].append(other);add_summary(manifest)
    with pytest.raises(c.CalculationError,match='身份证据存在重叠'):c.calculate(manifest)

def test_distinct_objects_can_sum(manifest):
    other=copy.deepcopy(manifest['items'][0]);other.update(item_id='QTY-B',physical_object_id='B',identity_evidence_ids=['EV-CONTEXT']);other['evidence_ids'].append('EV-CONTEXT');manifest['items'].append(other);add_summary(manifest)
    assert c.calculate(manifest)[0]['items'][-1]['value']==pytest.approx(2*.1467008)

@pytest.mark.parametrize('fault',['mismatch','duplicate','fraction','missing'])
def test_node_quantity_derivation_rejects_invalid_input(manifest,fault):
    i=manifest['items'][0];i.pop('section_geometry');i.update(method='count',object_type='connection_hardware',unit='套',mass_inclusion='not_mass',quantity=12,formula_note='two ends of six',quantity_derivation=dict(evidence_ids=['EV-OBJ'],groups=[dict(group_id='G',instance_ids=['A','B'],units_per_instance=6)]))
    if fault=='mismatch':i['quantity']=24
    if fault=='duplicate':i['quantity_derivation']['groups'][0]['instance_ids']=['A','A']
    if fault=='fraction':i['quantity_derivation']['groups'][0]['units_per_instance']=2.5
    if fault=='missing':i['quantity_derivation']['evidence_ids']=[]
    with pytest.raises(c.CalculationError):c.calculate(manifest)

def test_same_code_is_scoped_by_floor(manifest):
    i=manifest['items'][0];geometry=i.pop('section_geometry');del i['cross_section_area_mm2'];i['section_reference']=dict(schedule_id='V1',building='B',level='2',code='B1')
    manifest['section_catalog']=[dict(schedule_id='V1',building='B',level='2',code='B1',section_geometry=geometry),dict(schedule_id='V1',building='B',level='3',code='B1',section_geometry={**geometry,'height_mm':600})]
    first=c.calculate(manifest)[0]['items'][0]['value'];i['section_reference']['level']='3';second=c.calculate(manifest)[0]['items'][0]['value'];assert second>first
    manifest['section_catalog'].append(copy.deepcopy(manifest['section_catalog'][1]))
    with pytest.raises(c.CalculationError,match='须唯一匹配'):c.calculate(manifest)

def test_calculation_overflow_is_rejected(manifest):
    manifest['items'][0]['length_total_m']=1e308
    with pytest.raises(c.CalculationError,match='溢出'):c.calculate(manifest)

@pytest.mark.parametrize('extra',[{'height_end_mm':600},{'taper_ratio':.2},{'radius_mm':12}])
def test_nonconstant_or_rounded_geometry_is_not_silently_ignored(manifest,extra):
    manifest['items'][0]['section_geometry'].update(extra)
    with pytest.raises(c.CalculationError,match='未支持尺寸字段'):c.calculate(manifest)

@pytest.mark.parametrize('alias',['handle:abc','HANDLE:AbC',' handle:ABC '])
def test_handle_spelling_cannot_bypass_identity_deduplication(manifest,alias):
    manifest['evidence'][0]['object_keys']=['handle:ABC']
    manifest['evidence'][1]['object_keys']=[alias]
    other=copy.deepcopy(manifest['items'][0]);other.update(item_id='QTY-B',physical_object_id='B',identity_evidence_ids=['EV-CONTEXT']);other['evidence_ids'].append('EV-CONTEXT');manifest['items'].append(other);add_summary(manifest)
    with pytest.raises(c.CalculationError,match='对象证据身份'):c.calculate(manifest)
