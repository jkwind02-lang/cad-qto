"""Source-coordinate span and end-condition transfer tests, not project answers."""
import copy
import importlib.util
from pathlib import Path
import pytest
from test_steel_project_feedback import manifest, c

P=Path(__file__).resolve().parents[1]/'skills/qto-structural-steel/scripts/steel_dimension_derivation.py'
spec=importlib.util.spec_from_file_location('steel_bay_dimensions',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
E={k:{'evidence_class':'object_geometry'} for k in ['plan','ends']}

def make(points=((0,0,0),(3,4,0)),unit='m',basis='support_centres'):
    return {'item_id':'beam','method':'built_up_section_length','length_derivation':{
        'basis':'source model coordinates and measured distances along member axis',
        'evidence_ids':['plan'],'span_basis':basis,
        'axis_geometry':{'start':list(points[0]),'end':list(points[1]),'unit':unit,'evidence_ids':['plan']},
        'end_conditions':[{'end_id':'start','axis_to_face_m':.15,'face_to_cut_m':.35,'evidence_ids':['ends']},
                          {'end_id':'end','axis_to_face_m':.2,'face_to_cut_m':.4,'evidence_ids':['ends']}]}}

@pytest.mark.parametrize('unit,scale',[('mm',1000),('cm',100),('m',1),('in',1/.0254),('ft',1/.3048)])
def test_unit_rotation_translation_and_slope_invariant(unit,scale):
    # 3-4-12 triangle, rotated and translated: true spatial length 13 m.
    points=[(1234567*scale,-47*scale,10*scale),((1234567-4)*scale,-44*scale,22*scale)]
    result,audit,used=m.derive_dimensions(make(points,unit),E)
    assert result['length_total_m']==pytest.approx(11.9)
    assert used=={'plan','ends'}
    assert audit['length_total_m']['basis']['axis_geometry']['unit']==unit

def test_end_order_does_not_change_quantity():
    x=make();x['length_derivation']['quantity']=3
    x['length_derivation']['end_conditions'].reverse()
    assert m.derive_dimensions(x,E)[0]['length_total_m']==pytest.approx(11.7)

@pytest.mark.parametrize('basis',['support_faces','cut_faces'])
def test_no_repeat_centre_deduction(basis):
    with pytest.raises(ValueError,match='重复扣'):m.derive_dimensions(make(basis=basis),E)

def test_cut_face_span_cannot_deduct_cut_again():
    x=make(basis='cut_faces')
    for end in x['length_derivation']['end_conditions']:end['axis_to_face_m']=0
    with pytest.raises(ValueError,match='再次扣'):m.derive_dimensions(x,E)

def test_equivalent_three_starting_bases():
    center=make()
    face=make(((0,0,0),(4.65,0,0)),basis='support_faces')
    for e in face['length_derivation']['end_conditions']:e['axis_to_face_m']=0
    cut=make(((0,0,0),(3.9,0,0)),basis='cut_faces')
    for e in cut['length_derivation']['end_conditions']:e.update(axis_to_face_m=0,face_to_cut_m=0)
    assert [m.derive_dimensions(x,E)[0]['length_total_m'] for x in [center,face,cut]]==pytest.approx([3.9]*3)

@pytest.mark.parametrize('fault',['repeated_end','missing_end','free_deduction','negative_cut','bool_coordinate','nan_coordinate','two_coordinates','zero_span','unknown_unit','conflicting_span','missing_evidence'])
def test_invalid_or_ambiguous_input_rejected(fault):
    x=make();s=x['length_derivation'];g=s['axis_geometry'];ends=s['end_conditions']
    if fault=='repeated_end':ends[1]['end_id']='start'
    elif fault=='missing_end':ends.pop()
    elif fault=='free_deduction':s['deductions']=[{'label':'again','value_m':.1}]
    elif fault=='negative_cut':ends[0]['face_to_cut_m']=-.2
    elif fault=='bool_coordinate':g['start'][0]=True
    elif fault=='nan_coordinate':g['end'][0]=float('nan')
    elif fault=='two_coordinates':g['start']=[0,0]
    elif fault=='zero_span':g['end']=g['start'][:]
    elif fault=='unknown_unit':g['unit']='drawing'
    elif fault=='conflicting_span':s['span_m']=4
    elif fault=='missing_evidence':ends[0]['evidence_ids']=[]
    with pytest.raises(ValueError):m.derive_dimensions(x,E)

def test_legacy_span_input_compatible_without_new_semantic_claim():
    x={'method':'profile_length','length_derivation':{'span_m':8,'basis':'already established clear span','deductions':[],'evidence_ids':['plan']}}
    assert m.derive_dimensions(x,E)[0]['length_total_m']==8

def test_matched_declared_and_derived_length():
    x=make();x['length_derivation']['span_m']=5;x['length_total_m']=3.9
    assert m.derive_dimensions(x,E)[0]['length_total_m']==pytest.approx(3.9)
    x['length_total_m']=4
    with pytest.raises(ValueError,match='声明值'):m.derive_dimensions(x,E)

def test_full_calculator_binds_indirect_end_evidence(manifest):
    item=manifest['items'][0];item.pop('length_total_m')
    s=make()['length_derivation'];s['evidence_ids']=['EV-OBJ'];s['axis_geometry']['evidence_ids']=['EV-OBJ']
    for e in s['end_conditions']:e['evidence_ids']=['EV-CONTEXT']
    item['length_derivation']=s
    audit,_,result=c.calculate(manifest)
    assert audit['items'][0]['value']==pytest.approx(9344*3.9*7850/1e9)
    assert 'EV-CONTEXT' in result['工程量结果'][0]['来源']
    next(e for e in manifest['evidence'] if e['evidence_id']=='EV-CONTEXT')['fact_status']='candidate'
    with pytest.raises(c.CalculationError,match='候选或测试证据'):c.calculate(manifest)
