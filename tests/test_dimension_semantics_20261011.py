"""Original DXF endpoint binding, semantic counterexamples and steel integration."""
import copy
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_dimension_semantics as m


def fixture(tmp_path, scale=1, units=4, insert=None, line_offset=0, dimlfac=1):
    doc = ezdxf.new(); doc.units = units
    space = doc.blocks.new('DETAIL', base_point=(13, 19)) if insert else doc.modelspace()
    positions = [0, 200, 500, 2500, 2800, 3000]
    targets = []
    handle = {}
    for i, p in enumerate(positions):
        e = space.add_line(((p + line_offset)*scale, -100*scale), ((p + line_offset)*scale, 100*scale))
        handle[str(i)] = e.dxf.handle
        targets.append(dict(target_id=str(i), source_id='S', view_id='V',
            physical_object_key='beam', reference_id='ref'+str(i), role=['axis', 'surface', 'cut', 'cut', 'surface', 'axis'][i],
            basis='direct source reference', selector={'kind':'on_line'}))
    dimension_handles = {}; dimensions = []
    for did, a, b in [('SPAN',0,5), ('A',0,1), ('B',1,2), ('C',3,4), ('D',4,5), ('NET',2,3)]:
        dim = space.add_aligned_dim((positions[a]*scale, 0), (positions[b]*scale, 0), 150*scale,
                                    override={'dimlfac':dimlfac}); dim.render()
        dim.dimension.dxf.dimtype = (dim.dimension.dxf.dimtype & ~15) | 1
        dimension_handles[did] = dim.dimension.dxf.handle
        dimensions.append(dict(dimension_id=did, source_id='S', view_id='V', basis='source dimension extension point review',
            candidates=[dict(candidate_id='ONE', measurement_kind='aligned_length', basis='selected extension point targets',
                endpoints=[{'target_id':str(a)},{'target_id':str(b)}])], selected_candidate_id='ONE', selection_basis='independently inspected targets'))
    prefix = 'CADINST-Model/'
    if insert:
        ref = doc.modelspace().add_blockref('DETAIL', insert.get('position',(10000,20000)), dxfattribs={k:v for k,v in insert.items() if k!='position'})
        prefix += ref.dxf.handle + '/'
    for target in targets: target['object_id'] = prefix + handle[target['target_id']]
    for dim in dimensions: dim['object_id'] = prefix + dimension_handles[dim['dimension_id']]
    path = tmp_path/'source.dxf'; doc.saveas(path)
    document = dict(schema_version=m.SCHEMA, sources=[dict(source_id='S',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),view_basis='source plan')],
        targets=targets, dimensions=dimensions, constraints=[])
    return document, doc, path


def steel_mapping():
    return dict(span_dimension_id='SPAN', basis='center span to source-bound cut faces',
                ends=[dict(end_id='start',dimension_ids=['A','B']),dict(end_id='end',dimension_ids=['D','C'])])


def one(result, did='SPAN'):
    return next(f for f in result['dimensions'] if f['dimension_id']==did)


def save(doc, path, document):
    doc.saveas(path)
    document['sources'][0]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()


def test_native_targets_and_existing_steel_arithmetic(tmp_path):
    data,_,_=fixture(tmp_path)
    output=m.steel_length_derivation(data,steel_mapping())
    assert output['review']['status']=='WORKING_CONSISTENT'
    assert output['length_derivation']['span_m']==pytest.approx(3)
    spec=importlib.util.spec_from_file_location('dimensions_existing',ROOT/'skills/qto-structural-steel/scripts/steel_dimension_derivation.py')
    steel=importlib.util.module_from_spec(spec);spec.loader.exec_module(steel)
    result,audit,used=steel.derive_dimensions(dict(item_id='beam',method='profile_length',length_derivation=output['length_derivation']),output['evidence'])
    assert result['length_total_m']==pytest.approx(2)
    assert output['formal_adoption_allowed'] is False
    assert all(e['fact_status']=='candidate' for e in output['evidence'].values())


@pytest.mark.parametrize('scale,unit',[(1,4),(.001,6),(.1,5),(1/25.4,1),(1/304.8,2)])
def test_length_units_migrate_without_quantity_change(tmp_path,scale,unit):
    data,_,_=fixture(tmp_path,scale=scale,units=unit)
    assert m.steel_length_derivation(data,steel_mapping())['length_derivation']['span_m']==pytest.approx(3)


@pytest.mark.parametrize('insert',[
    {'rotation':37}, {'rotation':-42,'xscale':-1}, {'rotation':32,'xscale':2,'yscale':.4},
    {'position':(1e10,-1e10),'rotation':-17},
])
def test_block_basepoint_rotation_mirror_scale_bind_full_path(tmp_path,insert):
    data,_,_=fixture(tmp_path,insert=insert)
    out=m.steel_length_derivation(data,steel_mapping())
    assert out['length_derivation']['span_m']==pytest.approx(3)
    f=one(out['review'])
    assert f['measurement_facts']['measurement_frame']=='source_entity_definition_wcs'
    assert f['definition_points_wcs']!=list(f['measurement_facts']['definition_points_wcs'].values())


def test_dimlfac_view_depiction_and_design_value_separate(tmp_path):
    data,_,_=fixture(tmp_path,scale=.5,insert={'xscale':4,'yscale':4},dimlfac=2)
    for d in data['dimensions']:
        d.update(design_unit='mm',design_unit_basis='dimension display in millimetres',annotation_scale_basis='detail drawn at half design dimensions')
    f=one(m.review_dimension_semantics(data))
    assert f['value_m']==pytest.approx(3)
    assert f['depiction_span_m']==pytest.approx(6)


def test_scaled_annotation_requires_unit_and_view_basis(tmp_path):
    data,_,_=fixture(tmp_path,dimlfac=2)
    assert 'scaled_annotation_unit_and_view_basis_required' in one(m.review_dimension_semantics(data))['issues']


@pytest.mark.parametrize('fault', ['wrong_target','other_z','infinite_extension','raw_unit','stale_source','missing_instance','not_selected','role_override','hand_coordinates','wrong_kind','duplicate_target','wrong_view','claimed_formal'])
def test_identity_and_geometry_counterexamples(tmp_path,fault):
    data,doc,path=fixture(tmp_path)
    dim=data['dimensions'][0]
    if fault=='wrong_target': dim['candidates'][0]['endpoints'][0]['target_id']='1'
    elif fault=='other_z':
        e=doc.entitydb[data['targets'][0]['object_id'].split('/')[-1]];e.dxf.start=(0,-100,100);e.dxf.end=(0,100,100);save(doc,path,data)
    elif fault=='infinite_extension':
        e=doc.entitydb[data['targets'][0]['object_id'].split('/')[-1]];e.dxf.start=(0,100,0);e.dxf.end=(0,200,0);save(doc,path,data)
    elif fault=='raw_unit': data['sources'][0]['raw_unit']='m'
    elif fault=='stale_source': path.write_bytes(path.read_bytes()+b'\n')
    elif fault=='missing_instance': dim['object_id']='CADINST-Model/MISSING/'+dim['object_id'].split('/')[-1]
    elif fault=='not_selected': dim.pop('selected_candidate_id')
    elif fault=='role_override': dim['candidates'][0]['endpoints'][0]['role']='surface'
    elif fault=='hand_coordinates': data['targets'][0]['selector']['point']=[0,0,0]
    elif fault=='wrong_kind': dim['candidates'][0]['measurement_kind']='projected_length'
    elif fault=='duplicate_target':
        alias=copy.deepcopy(data['targets'][0]);alias['target_id']='alias';data['targets'].append(alias)
    elif fault=='wrong_view': data['targets'][0]['view_id']='other'
    elif fault=='claimed_formal': data['adoption_scope']='formal'
    try:
        out=m.review_dimension_semantics(data)
    except ValueError:
        return
    assert one(out)['usable_for_working_calculation'] is False


def test_literal_override_is_candidate_not_adopted(tmp_path):
    data,doc,path=fixture(tmp_path)
    e=doc.entitydb[data['dimensions'][0]['object_id'].split('/')[-1]];e.dxf.text='4500';save(doc,path,data)
    f=one(m.review_dimension_semantics(data))
    assert not f['usable_for_working_calculation']
    assert f['measurement_facts']['text_override']=='4500'


def test_literal_observation_is_source_located_but_not_geometric_quantity(tmp_path):
    data,doc,path=fixture(tmp_path)
    e=doc.entitydb[data['dimensions'][0]['object_id'].split('/')[-1]];e.dxf.text='4500';save(doc,path,data)
    data['dimensions'][0]['model_design_observation']=dict(value=4500,unit='mm',raw_text_excerpt='4500',locator='native DIMENSION text',basis='model read the literal dimension')
    f=one(m.review_dimension_semantics(data))
    assert f['observed_candidate_m']==4.5
    assert f['design_candidate_m']==3
    assert f['usable_for_working_calculation'] is False
    data['dimensions'][0]['model_design_observation']['raw_text_excerpt']='5500'
    with pytest.raises(ValueError,match='原文'):m.review_dimension_semantics(data)


def test_reused_line_reference_cannot_move_between_dimensions(tmp_path):
    data,doc,path=fixture(tmp_path)
    e=doc.entitydb[data['dimensions'][1]['object_id'].split('/')[-1]]
    e.dxf.defpoint2=(0,50,0);e.dxf.defpoint3=(200,50,0)
    save(doc,path,data)
    out=m.review_dimension_semantics(data)
    assert 'same_reference_point_drift:0' in one(out)['issues']
    assert not one(out,'A')['usable_for_working_calculation']


def test_multiple_candidates_need_explicit_selection(tmp_path):
    data,_,_=fixture(tmp_path)
    d=data['dimensions'][0]
    other=copy.deepcopy(d['candidates'][0]);other['candidate_id']='TWO';other['endpoints'][0]['target_id']='1'
    d['candidates'].append(other);d.pop('selected_candidate_id')
    r=one(m.review_dimension_semantics(data))
    assert not r['usable_for_working_calculation'] and len(r['candidates'])==2


def chain(data):
    data['constraints']=[dict(constraint_id='CLOSE',kind='directed_chain',total='SPAN',basis='same source directed station chain',parts=[
        {'dimension_id':x,'direction':1} for x in ['A','B','NET','C','D']])]


def test_dimension_chain_closes_by_identity_and_value(tmp_path):
    data,_,_=fixture(tmp_path);chain(data)
    out=m.review_dimension_semantics(data)
    assert out['constraints'][0]['status']=='consistent'
    assert out['status']=='WORKING_CONSISTENT'


def test_tautological_dimension_chain_does_not_count_as_independent_check(tmp_path):
    data,_,_=fixture(tmp_path);chain(data)
    data['constraints'][0]['parts']=[dict(dimension_id='SPAN',direction=1)]
    assert m.review_dimension_semantics(data)['status']=='PARTIAL_BLOCKED'


@pytest.mark.parametrize('fault',['reverse','duplicate','wrong_order','different_value','other_view'])
def test_numeric_chain_cannot_hide_topology_or_design_conflict(tmp_path,fault):
    data,doc,path=fixture(tmp_path);chain(data)
    parts=data['constraints'][0]['parts']
    if fault=='reverse':parts[1]['direction']=-1
    elif fault=='duplicate':parts.insert(1,copy.deepcopy(parts[0]))
    elif fault=='wrong_order':parts[1],parts[2]=parts[2],parts[1]
    elif fault=='different_value':
        e=doc.entitydb[data['dimensions'][1]['object_id'].split('/')[-1]];override=e.override();override['dimlfac']=2;override.commit();save(doc,path,data)
    elif fault=='other_view':data['dimensions'][1]['view_id']='other'
    out=m.review_dimension_semantics(data)
    assert out['status']=='PARTIAL_BLOCKED'
    assert out['constraints'][0]['status']=='unresolved_or_conflict'


@pytest.mark.parametrize('fault',['skip_face','repeat_deduction','wrong_end','unselected','projected_as_true_length'])
def test_steel_adapter_cannot_sneak_in_free_or_repeat_deductions(tmp_path,fault):
    data,doc,path=fixture(tmp_path);mapping=steel_mapping()
    if fault=='skip_face':mapping['ends'][0]['dimension_ids']=['B']
    elif fault=='repeat_deduction':mapping['ends'][0]['dimension_ids']=['A','A','B']
    elif fault=='wrong_end':mapping['ends'][0]['dimension_ids']=['D','C']
    elif fault=='unselected':data['dimensions'][1].pop('selected_candidate_id')
    elif fault=='projected_as_true_length':
        e=doc.entitydb[data['dimensions'][0]['object_id'].split('/')[-1]];e.dxf.dimtype=0;e.dxf.angle=10
        data['dimensions'][0]['candidates'][0]['measurement_kind']='projected_length';save(doc,path,data)
    with pytest.raises(ValueError):m.steel_length_derivation(data,mapping)


def test_document_observation_can_detect_overlong_component_but_never_geometry_pass(tmp_path):
    path=tmp_path/'drawing.txt';path.write_text('source drawing observation',encoding='utf-8')
    d=dict(schema_version=m.SCHEMA,sources=[dict(source_id='S',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),view_basis='source document')],
        dimensions=[dict(dimension_id=i,source_id='S',view_id='V',basis='model observation',evidence_kind='document_observation',locator='page1 '+i,observed_value=v,unit='mm') for i,v in [('WHOLE',275),('PART',300)]],
        constraints=[dict(constraint_id='C',kind='contained_length',whole='WHOLE',part='PART',basis='same object longitudinal containment')])
    out=m.review_dimension_semantics(d)
    assert out['status']=='PARTIAL_BLOCKED'
    assert 'component_design_length_exceeds_whole' in out['constraints'][0]['issues']
    assert one(out,'WHOLE')['verification_mode']=='non_geometric_review'


def test_containment_uses_actual_extent_not_only_smaller_number(tmp_path):
    data,_,_=fixture(tmp_path)
    data['constraints']=[dict(constraint_id='C',kind='contained_length',whole='B',part='A',basis='intentional bad containment')]
    out=m.review_dimension_semantics(data)
    assert 'component_source_extent_outside_whole' in out['constraints'][0]['issues']


def projected_fixture(tmp_path, points, specs, insert=None):
    doc=ezdxf.new();doc.units=6;space=doc.blocks.new('PROJECTED') if insert else doc.modelspace();targets=[];dimensions=[]
    for i, point in enumerate(points):
        e=space.add_line(point,(point[0],point[1]+.05))
        targets.append(dict(target_id=str(i),source_id='S',view_id='V',physical_object_key='object',reference_id='r'+str(i),
            role='object_point',basis='source vertex',object_id='CADINST-Model/'+e.dxf.handle,
            selector={'kind':'endpoint','end':'start'}))
    for did,a,b,angle in specs:
        dm=space.add_linear_dim(base=(0,-.5),p1=points[a],p2=points[b],angle=angle);dm.render()
        dimensions.append(dict(dimension_id=did,source_id='S',view_id='V',basis='projected source dimension',
            object_id='CADINST-Model/'+dm.dimension.dxf.handle,selected_candidate_id='C',selection_basis='source witness points',
            candidates=[dict(candidate_id='C',measurement_kind='projected_length',basis='dimension projection axis',
            endpoints=[{'target_id':str(a)},{'target_id':str(b)}])]))
    if insert:
        ref=doc.modelspace().add_blockref('PROJECTED',(50,70),dxfattribs=insert)
        for record in targets+dimensions:
            record['object_id']=record['object_id'].replace('CADINST-Model/','CADINST-Model/'+ref.dxf.handle+'/')
    path=tmp_path/'projection.dxf';doc.saveas(path)
    return dict(schema_version=m.SCHEMA,sources=[dict(source_id='S',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),view_basis='dimension projections')],
                targets=targets,dimensions=dimensions,constraints=[])


def test_projected_chain_uses_annotation_axis_not_definition_chord(tmp_path):
    data=projected_fixture(tmp_path,[(0,0),(1,-1),(2,100)],[('TOTAL',0,2,0),('A',0,1,0),('B',1,2,0)])
    data['constraints']=[dict(constraint_id='C',kind='directed_chain',total='TOTAL',basis='projected stations',
                             parts=[{'dimension_id':d,'direction':1} for d in ['A','B']])]
    assert m.review_dimension_semantics(data)['status']=='WORKING_CONSISTENT'


@pytest.mark.parametrize('angle,expected',[(0,'WORKING_CONSISTENT'),(90,'PARTIAL_BLOCKED')])
def test_containment_cannot_compare_different_projection_axes(tmp_path,angle,expected):
    data=projected_fixture(tmp_path,[(0,0),(10,10),(2,2),(8,8)],[('WHOLE',0,1,0),('PART',2,3,angle)])
    data['constraints']=[dict(constraint_id='C',kind='contained_length',whole='WHOLE',part='PART',basis='same projected extent')]
    assert m.review_dimension_semantics(data)['status']==expected


@pytest.mark.parametrize('insert',[{'xscale':1,'yscale':1},{'xscale':2,'yscale':1},{'xscale':.4,'yscale':3,'rotation':27}])
def test_projection_chain_affine_invariant_requires_covector(tmp_path,insert):
    data=projected_fixture(tmp_path,[(0,0),(-1,2),(2,2)],[('TOTAL',0,2,45),('A',0,1,45),('B',1,2,45)],insert=insert)
    data['constraints']=[dict(constraint_id='C',kind='directed_chain',total='TOTAL',basis='native projected stations',
                             parts=[{'dimension_id':d,'direction':1} for d in ['A','B']])]
    out=m.review_dimension_semantics(data)
    assert out['status']=='WORKING_CONSISTENT'
    assert one(out,'TOTAL')['value_m']==pytest.approx(2*math.sqrt(2))


@pytest.mark.parametrize('insert',[{'xscale':1,'yscale':1},{'xscale':2,'yscale':1}])
def test_outside_projected_component_stays_outside_after_affine_transform(tmp_path,insert):
    data=projected_fixture(tmp_path,[(0,0),(2,2),(-1,6),(0,6)],[('WHOLE',0,1,45),('PART',2,3,45)],insert=insert)
    data['constraints']=[dict(constraint_id='C',kind='contained_length',whole='WHOLE',part='PART',basis='source projected extent')]
    out=m.review_dimension_semantics(data)
    assert 'component_source_extent_outside_whole' in out['constraints'][0]['issues']


def test_parent_dynamic_candidate_propagates(tmp_path):
    data,doc,path=fixture(tmp_path,insert={'rotation':15})
    block=doc.blocks.get('DETAIL');doc.blocks.rename_block('DETAIL','*U901')
    for e in doc.modelspace().query('INSERT'):e.dxf.name='*U901'
    save(doc,path,data)
    assert not one(m.review_dimension_semantics(data))['usable_for_working_calculation']


def test_cli_no_overwrite_and_utf8(tmp_path):
    data,_,_=fixture(tmp_path)
    ip=tmp_path/'input.json';op=tmp_path/'复核结果.json';ip.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    command=[sys.executable,'-B',str(SHARED/'qto_dimension_semantics.py'),'--input',str(ip),'--output',str(op)]
    r=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    assert r.returncode==0,r.stderr
    assert json.loads(op.read_text(encoding='utf-8'))['status']=='WORKING_CONSISTENT'
    r=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    assert r.returncode!=0
