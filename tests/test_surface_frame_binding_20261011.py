"""Explicit rectangle frame binding: source spans, transforms and legacy scope."""
import copy
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('frame_base', TESTS/'test_surface_skirting_length_20261011.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
c, v = base.c, base.v


def make_frame_document(folder):
    doc, facts = base.make_document(folder)
    facts['edges']['a'] = {'edge_id':'a', 'start':[0,4.1,0], 'end':[0,0,0], 'unit':'m', 'coordinate_system_id':'explicit-local'}
    frame = {'coordinate_system_id':'explicit-local', 'origin_m':[0,0,0], 'x_axis':[0,1,0], 'up_axis':[0,0,1],
             'source_edge_id':'a', 'source_edge_direction':'reverse', 'source_edge_interval_m':[3.65,4.1],
             'vertical_range_m':[0,.1], 'vertical_origin_m':0}
    facts['frames'] = {'sample':frame}
    item = doc['items'][0]
    item.update(name='有限子面面积',object_type='wall_finish',method='rectangle_openings_net',unit='m²',quantity_role='physical_area',
                inputs={'length_m':.45,'height_m':.1,'openings':[]},local_coordinate_frame=copy.deepcopy(frame),
                frame_binding={'frame_evidence_ref':'EV-GA','source_pointer':'/frames/sample','edge_evidence_ref':'EV-GA','edge_source_pointer':'/edges/a'})
    base.update_facts(doc,facts)
    return doc,facts


@pytest.fixture
def setup(tmp_path):
    doc,facts=make_frame_document(tmp_path)
    return doc,facts,tmp_path


def run(setup):
    doc,facts,folder=setup
    base.update_facts(doc,facts)
    return base.run(doc,folder)


def audit(out):
    return out['工程量结果'][0]['专业检查']['输入审计']['frame_validation']


def set_both(setup,key,value):
    doc,facts,_=setup
    facts['frames']['sample'][key]=copy.deepcopy(value)
    doc['items'][0]['local_coordinate_frame'][key]=copy.deepcopy(value)


def test_finite_part_of_longer_edge_binds_and_exports_actual_surface(setup):
    out=run(setup);a=audit(out)
    assert out['工程量结果'][0]['结果']['数值']==pytest.approx(.045)
    assert a['status']=='bound_consistent' and a['source_edge_length_m']==4.1
    assert a['bound_length_m']==pytest.approx(.45)
    assert a['rectangle_corners_m'][0]==[0,0,0]
    assert a['rectangle_corners_m'][2]==pytest.approx([0,.45,.1])
    assert a['frame_source_binding']['source_sha256']==setup[0]['sources'][0]['sha256']
    assert v.validate_document(out)==[]


def test_source_reversal_with_mirrored_station_range_preserves_geometry(setup):
    expected=audit(run(setup))['rectangle_corners_m']
    edge=setup[1]['edges']['a'];edge['start'],edge['end']=edge['end'],edge['start']
    set_both(setup,'source_edge_interval_m',[0,.45]);set_both(setup,'source_edge_direction','same')
    actual=audit(run(setup))['rectangle_corners_m']
    for a,b in zip(actual,expected): assert a==pytest.approx(b)


@pytest.mark.parametrize('unit,factor',[('m',1.),('mm',1000.),('cm',100.),('ft',1/.3048)])
def test_source_edge_unit_conversion_preserves_metre_frame(setup,unit,factor):
    e=setup[1]['edges']['a'];e['unit']=unit
    for k in ('start','end'):e[k]=[x*factor for x in e[k]]
    assert run(setup)['工程量结果'][0]['结果']['数值']==pytest.approx(.045)


def test_explicit_rotated_3d_frame_and_vertical_origin_export(setup):
    doc,facts,_=setup
    facts['edges']['a'].update(start=[3,4,5],end=[7,4,8])
    for k,val in {'origin_m':[3.8,4,5.6],'x_axis':[.8,0,.6],'up_axis':[0,1,0],
                  'source_edge_direction':'same','source_edge_interval_m':[1,1.45],
                  'vertical_range_m':[-.2,.8],'vertical_origin_m':-.2}.items():set_both(setup,k,val)
    doc['items'][0]['inputs']['height_m']=1
    a=audit(run(setup))
    assert a['surface_origin_m']==pytest.approx([3.8,3.8,5.6])
    assert a['rectangle_corners_m'][2]==pytest.approx([4.16,4.8,5.87])


def test_large_translation_keeps_small_subspan_and_detects_centimetre_residue(setup):
    doc,facts,_=setup
    shift=[1e9,-1e9,1e8]
    for k in ('start','end'):facts['edges']['a'][k]=[a+b for a,b in zip(facts['edges']['a'][k],shift)]
    # Explicit source positions, not a relative tolerance against 1e9 coordinates.
    set_both(setup,'origin_m',shift)
    assert run(setup)['工程量结果'][0]['结果']['数值']==pytest.approx(.045)
    doc['items'][0]['local_coordinate_frame']['origin_m'][0]+=.01
    with pytest.raises(c.CalculationError,match='框架不一致'):run(setup)


def test_tiny_fully_outside_interval_cannot_become_negative_bound_length(setup):
    set_both(setup,'source_edge_interval_m',[4.1+1e-10,4.1+2e-10])
    set_both(setup,'origin_m',[0,0,0])
    setup[0]['items'][0]['inputs']['length_m']=1e-10
    with pytest.raises(c.CalculationError,match='裁切后'):run(setup)


def test_tiny_valid_source_interval_cannot_license_zero_rectangle(setup):
    set_both(setup,'source_edge_interval_m',[4.1-1e-10,4.1])
    setup[0]['items'][0]['method']='rectangle_net'
    setup[0]['items'][0]['inputs']['length_m']=0
    with pytest.raises(c.CalculationError,match='矩形长度'):run(setup)


@pytest.mark.parametrize('change',[ 'row_origin','both_origin','both_x_axis','both_z','row_coordinate_system','edge_coordinate_system',
    'edge_id','missing_edge_object_key','missing_surface_object_key','wrong_evidence_class','missing_evidence_ref','wrong_pointer','extra_binding_key',
    'missing_binding_key','null_binding','missing_frame','missing_origin','short_origin','boolean_origin','nonunit_x','nonunit_up','nonorthogonal_up',
    'out_of_edge','negative_station','reversed_interval','multiple_intervals','zero_interval','wrong_length','wrong_height',
    'vertical_origin','reversed_height','boolean_height','edge_wrong_unit','edge_missing_system','zero_edge','overflow_edge','wrong_method'])
def test_explicit_binding_rejects_inconsistent_geometry_and_ownership(setup,change):
    doc,facts,_=setup;i=doc['items'][0];f=i['local_coordinate_frame'];binding=i['frame_binding'];edge=facts['edges']['a']
    if change=='row_origin': f['origin_m'][0]=.1
    elif change=='both_origin':set_both(setup,'origin_m',[.1,0,0])
    elif change=='both_x_axis':set_both(setup,'x_axis',[1,0,0])
    elif change=='both_z':set_both(setup,'origin_m',[0,0,.1])
    elif change=='row_coordinate_system':f['coordinate_system_id']='different'
    elif change=='edge_coordinate_system':edge['coordinate_system_id']='different'
    elif change=='edge_id':edge['edge_id']='different'
    elif change=='missing_edge_object_key':doc['evidence'][1]['object_keys']=['face']
    elif change=='missing_surface_object_key':doc['evidence'][1]['object_keys']=['a']
    elif change=='wrong_evidence_class':doc['evidence'][1]['evidence_class']='opening_union'
    elif change=='missing_evidence_ref':i['evidence_refs'].remove('EV-GA')
    elif change=='wrong_pointer':binding['source_pointer']='/frames/missing'
    elif change=='extra_binding_key':binding['origin_m']=[0,0,0]
    elif change=='missing_binding_key':binding.pop('source_pointer')
    elif change=='null_binding':i['frame_binding']=None
    elif change=='missing_frame':i.pop('local_coordinate_frame')
    elif change=='missing_origin':f.pop('origin_m')
    elif change=='short_origin':f['origin_m']=[0,0]
    elif change=='boolean_origin':f['origin_m']=[False,0,0]
    elif change=='nonunit_x':set_both(setup,'x_axis',[0,2,0])
    elif change=='nonunit_up':set_both(setup,'up_axis',[0,0,2])
    elif change=='nonorthogonal_up':set_both(setup,'up_axis',[0,1,0])
    elif change=='out_of_edge':set_both(setup,'source_edge_interval_m',[3.8,4.2])
    elif change=='negative_station':set_both(setup,'source_edge_interval_m',[-1,.45])
    elif change=='reversed_interval':set_both(setup,'source_edge_interval_m',[4.1,3.65])
    elif change=='multiple_intervals':set_both(setup,'source_edge_interval_m',[[0,.2],[.3,.55]])
    elif change=='zero_interval':set_both(setup,'source_edge_interval_m',[0,0])
    elif change=='wrong_length':i['inputs']['length_m']=4.1
    elif change=='wrong_height':i['inputs']['height_m']=.2
    elif change=='vertical_origin':set_both(setup,'vertical_origin_m',.1)
    elif change=='reversed_height':set_both(setup,'vertical_range_m',[.1,0])
    elif change=='boolean_height':set_both(setup,'vertical_range_m',[False,.1])
    elif change=='edge_wrong_unit':edge['unit']='m²'
    elif change=='edge_missing_system':edge.pop('coordinate_system_id')
    elif change=='zero_edge':edge['end']=edge['start'][:]
    elif change=='overflow_edge':edge['start']=[-1e308,0];edge['end']=[1e308,0]
    elif change=='wrong_method':i['method']='area_direct';i['inputs']={'area_m2':.045}
    with pytest.raises(c.CalculationError):run(setup)


def test_unbound_legacy_frame_is_disclosed_without_inventing_validation(setup):
    i=setup[0]['items'][0];i.pop('frame_binding');i['local_coordinate_frame']={'origin_m':[99,99,99],'positive_x':'anything'}
    out=run(setup)
    assert out['工程量结果'][0]['结果']['数值']==pytest.approx(.045)
    assert audit(out)['status']=='unbound' and v.validate_document(out)==[]
    i.pop('local_coordinate_frame')
    assert audit(run(setup))['status']=='not_provided'


def test_cardinal_labels_are_not_interpreted_as_numeric_axes(setup):
    setup[0]['items'][0]['local_coordinate_frame']['positive_x']='intentionally-non-directional-label'
    a=audit(run(setup))
    assert a['status']=='bound_consistent' and a['unchecked_annotation_fields']==['positive_x']


def test_bound_rectangle_with_local_opening_uses_existing_union_area(setup):
    i=setup[0]['items'][0];i['inputs']['openings']=[{'opening_id':'part','rectangle_m':[.1,0,.3,.1]}]
    out=run(setup)
    assert out['工程量结果'][0]['结果']['数值']==pytest.approx(.025)
    assert audit(out)['note'].find('洞口三维定位')>=0


def test_full_binding_cli_replay_detects_frame_output_tampering(setup):
    out=run(setup);folder=setup[2]
    path=folder/'result.json';path.write_text(json.dumps(out,ensure_ascii=False),encoding='utf8')
    command=[sys.executable,'-X','utf8','-B',str(base.S/'validate_surfaces_envelope_result.py'),'--calculator',str(base.S/'calculate_surfaces_envelope.py'),
             '--manifest',str(folder/'manifest.json'),'--binding',str(path)]
    assert subprocess.run(command,capture_output=True,stdin=subprocess.DEVNULL).returncode==0
    audit(out)['rectangle_corners_m'][0][0]=9
    path.write_text(json.dumps(out,ensure_ascii=False),encoding='utf8')
    rejected=subprocess.run(command,capture_output=True,stdin=subprocess.DEVNULL)
    assert rejected.returncode==2
    assert '绑定复算结果不一致' in rejected.stdout.decode('utf8',errors='replace')


def test_result_validator_rejects_missing_or_false_bound_claim(setup):
    out=run(setup);a=audit(out);a['status']='unbound'
    assert any('未绑定定位' in e for e in v.validate_document(out))
    out['工程量结果'][0]['专业检查']['输入审计'].pop('frame_validation')
    assert any('核验状态' in e for e in v.validate_document(out))
