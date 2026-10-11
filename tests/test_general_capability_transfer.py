"""Transfer tests use synthetic sources, never a project's desired totals."""
import copy
import gzip
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
import ezdxf
from ezdxf.math import Matrix44
import pytest
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/qto-pipe-networks/scripts'))
from bind_pipe_callouts import bind_callouts

def module(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/'skills/qto-concrete-formwork/scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
cf=module('calculate_concrete_formwork_pairs')
cv=module('validate_concrete_formwork_result')
sys.path.insert(0, str(ROOT/'skills/_qto-shared-evidence/scripts'))
from validate_minimum_result import validate_document
RESULT_SCHEMA=json.loads((ROOT/'skills/_qto-shared-evidence/references/工程量成果最小字段.schema.json').read_text(encoding='utf8'))

def callout(scale=1., angle=0., gap=0., endpoint_gap=0., mtext=False, bent=False, nested=False):
    doc=ezdxf.new();body=doc.blocks.new('arbitrary equipment family');body.add_circle((0,0),scale)
    space=doc.blocks.new('arbitrary container') if nested else doc.modelspace()
    instance=space.add_blockref(body.name,(0,0))
    if bent:
        space.add_lwpolyline([(endpoint_gap*scale,0),(6*scale,3*scale),((10-gap)*scale,0)])
    else:space.add_line((endpoint_gap*scale,0),((10-gap)*scale,0))
    space.add_lwpolyline([(10*scale,-2*scale),(30*scale,-2*scale),(30*scale,2*scale),(10*scale,2*scale)],close=True,dxfattribs={'layer':'NOT-0'})
    if mtext:space.add_mtext('AHU-X DN125',dxfattribs={'insert':(11*scale,scale),'char_height':2*scale})
    else:space.add_text('AHU-X DN125',dxfattribs={'insert':(11*scale,-scale),'height':2*scale})
    transform=Matrix44.chain(Matrix44.z_rotate(angle),Matrix44.translate(871*scale,-543*scale,0))
    for e in list(space):e.transform(transform)
    root=doc.modelspace().add_blockref(space.name,(9876,-8765),dxfattribs={'rotation':41,'xscale':-2,'yscale':3}) if nested else None
    parent=[root.dxf.handle] if root else []
    return doc,parent,parent+[instance.dxf.handle]

@pytest.mark.parametrize('scale',[.001,1.,1000.])
@pytest.mark.parametrize('angle',[0.,.37,math.pi/2,2.3])
@pytest.mark.parametrize('nested',[False,True])
def test_units_rotation_translation_layer_and_mirror_do_not_change_identity(scale,angle,nested):
    doc,parent,path=callout(scale,angle,nested=nested)
    row=bind_callouts(doc,parent,[path],r'AHU-X DN\d+')[0]
    assert row['state']=='bound'
    assert row['candidates'][0]['text']=='AHU-X DN125'

@pytest.mark.parametrize('scale',[.001,1.,1000.])
def test_nearby_disconnected_instance_never_binds_by_default(scale):
    doc,parent,path=callout(scale,endpoint_gap=.04)
    assert bind_callouts(doc,parent,[path],r'AHU-X DN\d+')[0]['state']=='unmatched'

@pytest.mark.parametrize('scale',[.001,1.,1000.])
def test_gap_requires_explicit_basis_and_scales_with_input(scale):
    doc,parent,path=callout(scale,angle=.45,gap=.08,endpoint_gap=.03)
    assert bind_callouts(doc,parent,[path],r'AHU-X DN\d+')[0]['state']=='unmatched'
    with pytest.raises(ValueError,match='依据'):
        bind_callouts(doc,parent,[path],r'AHU-X DN\d+',text_height_fraction=.05)
    row=bind_callouts(doc,parent,[path],r'AHU-X DN\d+',endpoint_tolerance=.04*scale,
        text_height_fraction=.05,tolerance_basis='synthetic source gap')[0]
    assert row['state']=='bound'
    assert row['candidates'][0]['leader_box_gap_raw']/scale==pytest.approx(.08)
    assert bind_callouts(doc,parent,[path],r'AHU-X DN\d+',endpoint_tolerance=.04*scale,
        text_height_fraction=.05,gap_limit_raw=.07*scale,tolerance_basis='stricter source limit')[0]['state']=='unmatched'

@pytest.mark.parametrize('mtext,bent',[(True,False),(False,True),(True,True)])
def test_mtext_and_single_bent_polyline_leader(mtext,bent):
    doc,parent,path=callout(angle=.7,mtext=mtext,bent=bent)
    assert bind_callouts(doc,parent,[path],r'AHU-X DN\d+')[0]['state']=='bound'

def test_tip_inside_box_is_not_boundary_contact():
    doc,parent,path=callout(gap=-3)
    assert bind_callouts(doc,parent,[path],r'AHU-X DN\d+')[0]['state']=='unmatched'

def test_parent_array_does_not_collapse_physical_instances():
    doc,parent,path=callout(nested=True)
    doc.entitydb[parent[0]].dxf.row_count=2
    with pytest.raises(ValueError,match='阵列父块'):bind_callouts(doc,parent,[path],r'AHU-X DN\d+')

def concrete_manifest(tmp_path, scale=1., prefix='VARIABLE'):
    tmp_path.mkdir(parents=True,exist_ok=True)
    dwg=tmp_path/'synthetic.dwg';dwg.write_bytes(b'Synthetic source identity fixture, not a real DWG.')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    dims=dict(core_length=7200,core_width=3800,extension_left=180,extension_right=240,
              extension_top=120,extension_bottom=160,notch_length=640,notch_width=460)
    dims={k:v*scale for k,v in dims.items()}
    length=dims['core_length']+dims['extension_left']+dims['extension_right']
    width=dims['core_width']+dims['extension_top']+dims['extension_bottom']
    nl,nw=dims['notch_length'],dims['notch_width']
    pts=[(0,0),(length,0),(length,width-nw),(length-nl,width-nw),(length-nl,width),(0,width)]
    rows=[];specs=[]
    def add(handle,kind,**kw):
        rows.append(dict(row_type='entity',entity_handle=handle,source_sha256=sha(dwg),entity_type=kind,**kw))
        specs.append(dict(handle=handle,entity_type=kind))
    add(prefix+'-OUT','LWPOLYLINE',closed=True,geometry=json.dumps(dict(measurement_points=pts)))
    for i,(role,value) in enumerate(dims.items()):
        add(prefix+f'-D{i}','DIMENSION',measurement=value)
        specs[-1].update(role=role,adopted_mm=value,expected_measurement_mm=value)
    tv=dict(raft_thickness=220*scale,blinding_thickness=70*scale,blinding_offset=120*scale)
    text=' '.join(f'{k}={v}' for k,v in tv.items())+'; face conditions individually reviewed'
    note=prefix+'-NOTE';add(note,'TEXT',text=text)
    stream=tmp_path/'source.jsonl.gz'
    with gzip.open(stream,'wt',encoding='utf8') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    metadata=tmp_path/'metadata.json'
    metadata.write_text(json.dumps(dict(metadata=dict(source_sha256=sha(dwg),adapter='synthetic',schema_version='synthetic/1'),
        entity_stream_sha256=sha(stream),source_unit='Millimeters',unit_status='synthetic',unit_basis='fixture')),encoding='utf8')
    condition=dict(state='unresolved',evidence_handles=[note],reason='source does not state this face construction')
    return dict(manifest_version=cf.MANIFEST_VERSION,case_id=prefix,object=dict(id=prefix+'-BASE',name=prefix+'基础',
        blinding_id=prefix+'-PAD',blinding_name=prefix+'垫层'),scope=dict(project=prefix,building='test',area='test'),
        source=dict(dwg_path=str(dwg),dwg_sha256=sha(dwg),entity_stream_path=str(stream),entity_stream_sha256=sha(stream),
            metadata_path=str(metadata),metadata_sha256=sha(metadata),source_unit='Millimeters',expected_adapter='synthetic',
            expected_schema_version='synthetic/1',expected_unit_status='synthetic',expected_unit_basis='fixture',
            expected_row_count=len(rows),expected_entity_count=len(rows)),evidence_entities=specs,
        calculation=dict(type=cf.SUPPORTED_CALCULATION,outline_handle=prefix+'-OUT',
            text_values={k:dict(handle=note,pattern=k+r'=([0-9.]+)',expected_mm=v) for k,v in tv.items()},
            template_conditions={k:copy.deepcopy(condition) for k in ('raft_exterior_side','raft_bottom','raft_top','blinding_side')}))

@pytest.mark.parametrize('scale,prefix',[(1,'A-unique'),(2.5,'B-renamed'),(.1,'C-small')])
def test_concrete_actual_inputs_replace_case_answer(tmp_path,scale,prefix):
    d=concrete_manifest(tmp_path,scale,prefix);ev,details,result=cf.calculate(d)
    area=7.62*4.08-.64*.46
    assert ev['calculations']['results']['raft_gross_m3']==pytest.approx(area*.22*scale**3)
    assert ev['calculations']['results']['blinding_gross_m3']==pytest.approx((area+2*(7.62+4.08)*.12+4*.12**2)*.07*scale**3)
    assert all(x['专业检查']['对象编号'].startswith(prefix) for x in result['工程量结果'])
    assert cv.validate_result(result,None,None)[0]==[]
    assert validate_document(result)==[]
    jsonschema.Draft202012Validator(RESULT_SCHEMA).validate(result)
    rendered=json.dumps([ev,details,result],ensure_ascii=False)
    assert not any(x in rendered for x in ('青海','消防泵房','219.45','SRC-QH','569D','819D'))

@pytest.mark.parametrize('state,value', [('included',True),('excluded',False),('unresolved',None)])
def test_each_face_changes_with_evidenced_condition(tmp_path,state,value):
    d=concrete_manifest(tmp_path)
    for c in d['calculation']['template_conditions'].values():c.update(state=state,coverage='complete',reason='source face condition fixture')
    ev,details,result=cf.calculate(d)
    faces=result['工程量结果'][2:]
    assert len(faces)==4
    for q in faces:
        n=q['结果']['数值']
        assert n>0 if value is True else n==0 if value is False else n is None
    assert cv.validate_result(result,None,None)[0]==[]
    assert validate_document(result)==[]
    jsonschema.Draft202012Validator(RESULT_SCHEMA).validate(result)

@pytest.mark.parametrize('change,fragment', [('missing_basis','证据'),('missing_coverage','完整覆盖'),('old_schema','迁移'),('bad_state','状态')])
def test_concrete_conditions_do_not_silently_default(tmp_path,change,fragment):
    d=concrete_manifest(tmp_path);f=d['calculation']['template_conditions']['raft_bottom']
    if change=='missing_basis':f['evidence_handles']=['invented']
    if change=='missing_coverage':f['state']='included'
    if change=='old_schema':d['manifest_version']='qto-concrete-formwork-manifest/1.0'
    if change=='bad_state':f['state']='whatever'
    with pytest.raises(cf.CalculationError,match=fragment):cf.calculate(d)

def test_concrete_recalculation_rejects_equal_evidence_and_result_tampering(tmp_path):
    d=concrete_manifest(tmp_path);ev,details,result=cf.calculate(d)
    p=tmp_path/'manifest.json';p.write_text(json.dumps(d),encoding='utf8')
    ev['manifest_sha256']=cf.sha256_file(p)
    assert cv.validate_result(result,ev,details,p)[0]==[]
    ev['calculations']['results']['raft_gross_m3']=999.
    result['工程量结果'][0]['结果']['数值']=999.
    errors,_=cv.validate_result(result,ev,details,p)
    assert any('重算一致' in e for e in errors)

def test_concrete_generic_self_tests_still_pass():
    assert all(t['status']=='PASS' for t in cv.run_self_tests())

def test_polygon_area_does_not_change_with_large_coordinate_origin():
    points=[(0,0),(7000,0),(7000,4000),(0,4000)]
    far=[(x+1e12,y-1e12) for x,y in points]
    assert cf._polygon_metrics(points)==cf._polygon_metrics(far)

def test_concrete_source_negative_tests_use_current_input(tmp_path):
    d=concrete_manifest(tmp_path)
    p=tmp_path/'manifest.json';p.write_text(json.dumps(d),encoding='utf8')
    assert all(t['status']=='PASS' for t in cv.run_source_negative_tests(p))

@pytest.mark.parametrize('height,width,web,flange,length,count',[
    (260,145,6,11,1.7,3),(730,315,13,23,5.25,7),(190,90,4,7,2.37,2)])
def test_steel_mass_follows_new_dimensions_and_count(tmp_path,height,width,web,flange,length,count):
    import test_steel_project_feedback as fixture
    d=fixture.manifest.__wrapped__(tmp_path);item=d['items'][0]
    item.pop('cross_section_area_mm2')
    item.update(name='another member',length_total_m=length*count,physical_object_id='unrelated-physical-id')
    item['section_geometry'].update(height_mm=height,width_mm=width,web_mm=web,flange_mm=flange)
    evidence,_,_=fixture.c.calculate(d)
    expected=(2*width*flange+(height-2*flange)*web)*length*count*7850/1e9
    assert evidence['items'][0]['value']==pytest.approx(expected)

@pytest.mark.parametrize('width,height',[(3.7,2.6),(6.45,4.2),(9.8,3.55)])
def test_surface_net_area_follows_different_dimensions_and_opening_overlap(tmp_path,width,height):
    import test_surface_project_feedback as fixture
    d=fixture.document.__wrapped__(tmp_path)
    item=d['items'][0];item['name']='another finish area'
    item['inputs'].update(length_m=width,height_m=height,openings=[
        fixture.opening('left',[0,0,width*.6,height*.5]),
        fixture.opening('right',[width*.4,0,width,height*.5])])
    result=fixture.run(d,tmp_path)['工程量结果'][0]
    assert result['结果']['数值']==pytest.approx(width*height/2)
