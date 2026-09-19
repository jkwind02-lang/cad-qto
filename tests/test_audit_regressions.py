"""Regression tests against the installed source modules (not copied old functions)."""
from __future__ import annotations

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

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SHARED))
import qto_cad_geometry as geometry
import qto_review_contract as review


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

extract=load_module('audit_extract','skills/_qto-shared-evidence/scripts/extract_cad_neutral.py')
pipe=load_module('audit_pipe','skills/qto-pipe-networks/scripts/build_pipe_inference_input.py')
component=load_module('audit_component','skills/qto-components-points/scripts/build_component_instances_from_neutral.py')


def row(entity):
    return extract.convert(entity,layout='Model',parent_block=None,source_hash='a'*64,raw_unit='Millimeters',sequence=1)


def drawing():
    return ezdxf.new('R2018')


@pytest.mark.parametrize('matrix,multiplier',[
    (geometry.IDENTITY,1),
    ((1,0,0,1,120,-41,1,9),1),
    ((0,1,-1,0,0,0,1,0),1),
    ((-1,0,0,1,0,0,1,0),1),
    ((2,0,0,2,0,0,2,0),2),
])
@pytest.mark.parametrize('kind',['line','polyline','arc'])
def test_curve_length_invariance_through_actual_adapter(matrix,multiplier,kind):
    m=drawing().modelspace()
    if kind=='line':entity=m.add_line((0,0),(3,4));expected=5
    elif kind=='polyline':entity=m.add_lwpolyline([(0,0),(3,0),(3,4)]);expected=7
    else:entity=m.add_arc((0,0),10,0,90);expected=5*math.pi
    result=pipe.transform_row(row(entity),matrix,'outer/inner','Model')
    assert result['raw_length']==pytest.approx(expected*multiplier,rel=1e-10)
    assert result['instance_path']=='outer/inner'
    assert result['geometry']['schema']==geometry.GEOMETRY_SCHEMA
    assert result['source_definition_record_id']==row(entity)['record_id']


def test_ellipse_arc_not_full_perimeter():
    entity=drawing().modelspace().add_ellipse((0,0),(10,0),.5,start_param=0,end_param=math.pi/2)
    result=extract.entity_geometry(entity)
    assert result['length']==pytest.approx(12.11056027568459,abs=2e-9)
    assert result['start']==pytest.approx([10,0,0])
    assert result['end']==pytest.approx([0,5,0],abs=1e-12)


def test_wrapped_ellipse_parameters():
    entity=drawing().modelspace().add_ellipse((0,0),(10,0),.5,start_param=3*math.pi/2,end_param=math.pi/2)
    assert extract.entity_geometry(entity)['length']==pytest.approx(24.22112055136918,abs=3e-9)


@pytest.mark.parametrize('bulge,expected_y',[(1,-5),(-1,5)])
def test_bulge_extents_include_arc_interior(bulge,expected_y):
    entity=drawing().modelspace().add_lwpolyline([(0,0,bulge),(10,0,0)],format='xyb')
    result=row(entity)
    assert result['raw_length']==pytest.approx(5*math.pi)
    assert result['bbox'][1 if bulge>0 else 3]==pytest.approx(expected_y)
    scope={'xmin':4,'xmax_exclusive':6,'ymin':expected_y-.1,'ymax_exclusive':expected_y+.1}
    assert pipe.bbox_intersects(result,scope)
    assert result['raw_geometry']['bulges']==[bulge,0]


def test_lwpolyline_ocs_to_wcs():
    entity=drawing().modelspace().add_lwpolyline([(1,2),(3,4)],dxfattribs={'elevation':5,'extrusion':(0,1,0)})
    result=row(entity)
    assert result['start']==pytest.approx([-1,5,2])
    assert result['start']==pytest.approx(list(entity.vertices_in_wcs())[0])
    assert result['bbox3d']==pytest.approx([-3,5,2,-1,5,4])


def test_arc_ocs_to_wcs():
    entity=drawing().modelspace().add_arc((1,2,5),3,0,90,dxfattribs={'extrusion':(0,1,0)})
    result=row(entity)
    assert result['start']==pytest.approx(entity.start_point)
    assert result['end']==pytest.approx(entity.end_point)


def test_closed_polyline_retains_closing_segment():
    entity=drawing().modelspace().add_lwpolyline([(0,0),(3,0),(3,4)],close=True)
    result=row(entity)
    assert result['raw_length']==pytest.approx(12)
    assert len(result['geometry']['parts'])==3
    assert result['geometry']['closed']
    roundtrip=json.loads(json.dumps(result['geometry']))
    assert geometry.summarize(roundtrip)['length']==pytest.approx(12)


def independent_simpson(f,a,b,n=20000):
    h=(b-a)/n
    return h*(f(a)+f(b)+4*math.fsum(f(a+i*h) for i in range(1,n,2))+2*math.fsum(f(a+i*h) for i in range(2,n,2)))/3


def test_nonuniform_arc_scaling_is_ellipse_not_chord():
    entity=drawing().modelspace().add_arc((0,0),10,0,90)
    result=pipe.transform_row(row(entity),(2,0,0,3,0,0,1,0),'scaled','Model')
    expected=independent_simpson(lambda t:math.hypot(20*math.sin(t),30*math.cos(t)),0,math.pi/2)
    assert result['raw_length']==pytest.approx(expected,rel=1e-10)
    assert result['bbox']==pytest.approx([0,0,20,30],abs=1e-10)


def test_legacy_curve_cache_does_not_produce_wrong_length():
    legacy={'record_id':'legacy','entity_type':'LWPOLYLINE','x':0,'y':0,'z':0,'start':[0,0,0],'end':[3,4,0],'raw_length':7,'bbox':[0,0,3,4]}
    result=pipe.transform_row(legacy,geometry.IDENTITY,'old','Model')
    assert result['raw_length'] is None
    assert result['geometry_status']=='requires_reextraction'
    assert pipe.bbox_intersects(result,{'xmin':0,'xmax_exclusive':1,'ymin':0,'ymax_exclusive':1})


def test_spline_retains_controls_without_fake_length():
    entity=drawing().modelspace().add_spline([(0,0),(1,2),(3,0)])
    result=row(entity)
    assert result['raw_length'] is None
    assert result['geometry_status']=='unsupported'
    assert result['raw_geometry']['fit_points']
    assert result['geometry_issues']


def test_pipe_and_component_share_full_insert_matrix():
    doc=drawing();block=doc.blocks.new('offset',base_point=(2,3,4));block.add_line((2,3,4),(3,3,4))
    entity=doc.modelspace().add_blockref('offset',(11,7,5),dxfattribs={'extrusion':(0,1,0),'rotation':30,'xscale':-2,'yscale':3})
    data=row(entity);point=[4,8,6]
    expected=list(entity.matrix44().transform(point))
    assert pipe.transform_point(point,pipe.matrix_for_insert(data,[2,3,4]))==pytest.approx(expected)
    assert component.transform_point(point,component.matrix_for_insert(data,[2,3,4]))==pytest.approx(expected)


def extract_package(tmp_path,doc):
    source=tmp_path/'drawing.dxf';doc.saveas(source)
    output=tmp_path/'evidence'
    process=subprocess.run([sys.executable,str(SHARED/'extract_cad_neutral.py'),'--source-dxf',str(source),'--output-dir',str(output)],capture_output=True,text=True)
    assert process.returncode==0,process.stdout+process.stderr
    manifest=json.loads((output/'CAD中性取证清单.json').read_text(encoding='utf-8'))
    return output,manifest


def test_real_extractor_nested_expansion_and_scope(tmp_path):
    doc=drawing();inner=doc.blocks.new('inner');inner.add_lwpolyline([(0,0),(3,0),(3,4)])
    outer=doc.blocks.new('outer');outer.add_blockref('inner',(2,0))
    doc.modelspace().add_blockref('outer',(10,0))
    doc.modelspace().add_blockref('outer',(10000,0))
    doc.layout().add_blockref('outer',(10,0))
    output,manifest=extract_package(tmp_path,doc)
    failures=[]
    rows=list(pipe.expanded_manifest_rows(output/'中性布局图元.ndjson',output/'中性块定义图元.ndjson',manifest,16,failures,target_layout='Model',scope_bounds={'xmin':0,'xmax_exclusive':30,'ymin':-10,'ymax_exclusive':10}))
    curves=[r for r in rows if r['entity_type']=='LWPOLYLINE']
    assert not failures
    assert len(curves)==1
    assert curves[0]['raw_length']==pytest.approx(7)
    assert curves[0]['start']==pytest.approx([12,0,0])
    assert manifest['geometry_schema_version']==geometry.GEOMETRY_SCHEMA
    pipe.source_from_manifest(output/'CAD中性取证清单.json')
    component.source_from_manifest(output/'CAD中性取证清单.json')


@pytest.mark.parametrize('adapter',[pipe,component])
def test_old_manifest_requires_reextraction(tmp_path,adapter):
    path=tmp_path/'old.json';path.write_text(json.dumps({'schema_version':'qto-cad-neutral-evidence/1.0'}),encoding='utf-8')
    with pytest.raises(ValueError,match='旧缓存'):
        adapter.source_from_manifest(path)


def test_unknown_child_extent_prevents_unsafe_pruning():
    definitions={'A':[{'entity_type':'SPLINE','bbox3d':None}]}
    assert geometry.definition_bounds('A',definitions,{}) is None
    assert geometry.bounds_intersect(None,{'xmin':100,'xmax_exclusive':101,'ymin':100,'ymax_exclusive':101})


def view_raw():
    objects=[]
    for n in range(2):
        objects.append({'id':f'O{n}','catalog_key':f'K{n}','professional':'qto-pipe-networks','scene_id':'plan','kind':'line','points':[[0,n],[10,n]],'title':f'对象{n}','category':'管道','detail':'图示中心线','source':'图A对象'+str(n),'status':'规则推算','quantities':[{'name':'管长','value':10,'unit':'m','level':'规则推算量'}],'attributes':{'DN':'DN25'}})
    return {'schema':review.VIEW_SCHEMA,'project_id':'P','review_id':'R','title':'复核','revision':'v1','sources':[{'id':'S','title':'图A','sha256':'a'*64}],
            'scenes':[{'id':'plan','title':'平面','source_id':'S','coordinate_unit':'m','y_axis':'up','bounds':[0,0,20,20],'background':[]}],
            'objects':objects,'groups':[{'id':'G','title':'组','category':'复核','proposal':'按图计量','reason':'相同画法','status':'待核','member_keys':['K0']}],'summary':[]}


def package(view,single=False):
    data=review.empty_feedback(view);obj=view['objects'][0];group=view['groups'][0]
    snap=copy.deepcopy(group['member_snapshot'][0])
    record={'annotation_id':'A','confirmed_result':'确认10m','reason':'按图核对','instruction':'确认现结果','scope':'仅所选对象' if single else '本组勾选对象','updated_at':'2026-09-20T01:00:00Z','history':[],
            'object_id':obj['id'] if single else group['id'],'object_ids':[obj['id']],'result_fingerprint':obj['result_fingerprint'] if single else group['result_fingerprint']}
    if single:record['object_snapshot']=snap
    else:record.update(group_id=group['id'],member_snapshot=copy.deepcopy(group['member_snapshot']),selected_object_keys=['K0'],excluded_object_keys=[])
    data['annotations']['A']=record
    return data


@pytest.mark.parametrize('single',[False,True])
def test_current_review_snapshot_accepted(single):
    view=review.normalize_view(view_raw());result,unmatched=review.validate_feedback(package(view,single),view)
    assert not unmatched
    assert result['annotations']['A']['applicability_state']=='current'


@pytest.mark.parametrize('field',['quantity','spec','geometry','status','basis'])
def test_changed_engineering_result_marks_old_opinion_stale(field):
    raw=view_raw();old=review.normalize_view(raw);fb=package(old)
    if field=='quantity':raw['objects'][0]['quantities'][0]['value']=20
    elif field=='spec':raw['objects'][0]['attributes']['DN']='DN32'
    elif field=='geometry':raw['objects'][0]['points'][1]=[11,0]
    elif field=='status':raw['objects'][0]['status']='方案暂估'
    else:raw['objects'][0]['detail']='依据已变更'
    new=review.normalize_view(raw);result,unmatched=review.validate_feedback(fb,new)
    assert result['annotations']['A']['applicability_state']=='stale'
    assert unmatched
    assert result['annotations']['A']['confirmed_result']=='确认10m'
    assert result['annotations']['A']['result_fingerprint']==fb['annotations']['A']['result_fingerprint']


@pytest.mark.parametrize('single',[False,True])
def test_same_catalog_key_cannot_change_physical_identity(single):
    raw=view_raw();old=review.normalize_view(raw);fb=package(old,single)
    raw['objects'][0]['id']='REPLACEMENT'
    with pytest.raises(ValueError,match='身份不一致'):
        review.validate_feedback(fb,review.normalize_view(raw))


def test_cosmetic_titles_and_revision_do_not_expire_approval():
    raw=view_raw();old=review.normalize_view(raw);fb=package(old)
    raw['title']='新版标题';raw['revision']='v2';raw['objects'][0]['title']='修改显示名称';raw['groups'][0]['title']='新组名'
    result,unmatched=review.validate_feedback(fb,review.normalize_view(raw))
    assert not unmatched
    assert result['annotations']['A']['applicability_state']=='current'


def test_unrelated_object_change_does_not_expire_other_target():
    raw=view_raw();old=review.normalize_view(raw);fb=package(old)
    raw['objects'][1]['quantities'][0]['value']=100
    new=review.normalize_view(raw)
    assert old['result_fingerprint']!=new['result_fingerprint']
    assert not review.validate_feedback(fb,new)[1]


def test_unversioned_opinion_is_preserved_but_not_current():
    view=review.normalize_view(view_raw());fb=package(view)
    del fb['annotations']['A']['result_fingerprint']
    for snap in fb['annotations']['A']['member_snapshot']:snap.pop('result_fingerprint',None)
    result,unmatched=review.validate_feedback(fb,view)
    assert unmatched and result['annotations']['A']['applicability_state']=='stale'
    assert result['annotations']['A']['reason']=='按图核对'


def test_client_cannot_relabel_stale_as_current():
    raw=view_raw();old=review.normalize_view(raw);fb=package(old)
    raw['objects'][0]['quantities'][0]['value']=20;new=review.normalize_view(raw)
    fb['result_fingerprint']=new['result_fingerprint'];fb['annotations']['A']['applicability_state']='current'
    result,_=review.validate_feedback(fb,new)
    assert result['annotations']['A']['applicability_state']=='stale'


def test_stale_handoff_has_no_actionable_quantity_confirmation(tmp_path):
    raw=view_raw();old=review.normalize_view(raw);fb=package(old)
    raw['objects'][0]['quantities'][0]['value']=20
    (tmp_path/'view.json').write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
    (tmp_path/'feedback.json').write_text(json.dumps(fb,ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'handoff.json'
    process=subprocess.run([sys.executable,str(SHARED/'read_qto_review_feedback.py'),'--view',str(tmp_path/'view.json'),'--feedback',str(tmp_path/'feedback.json'),'--output',str(out)],capture_output=True,text=True)
    assert process.returncode==0,process.stdout+process.stderr
    data=json.loads(out.read_text(encoding='utf-8'))
    assert data['actions']==[] and data['quantity_changes']==0
    assert data['historical_annotations']['A']['confirmed_result']=='确认10m'
    assert data['unmatched']


def node_call(payload):
    code="require(process.argv[1]);const p=JSON.parse(require('fs').readFileSync(0,'utf8'));try{const result=p.receipt?QtoReviewCore.receiptState(p.receipt,p.record):QtoReviewCore.parse(p.feedback,p.view);console.log(JSON.stringify({result}));}catch(e){console.log(JSON.stringify({error:e.message}));}"
    process=subprocess.run(['node','-e',code,str(ROOT/'skills/_qto-shared-evidence/assets/review/review-core.js')],input=json.dumps(payload,ensure_ascii=False),capture_output=True,text=True)
    assert process.returncode==0,process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize('case',['current','quantity','identity','unversioned'])
def test_python_javascript_contract_parity(case):
    raw=view_raw();old=review.normalize_view(raw);fb=package(old)
    if case=='quantity':raw['objects'][0]['quantities'][0]['value']=20
    elif case=='identity':raw['objects'][0]['id']='NEW'
    elif case=='unversioned':fb['annotations']['A'].pop('result_fingerprint')
    view=review.normalize_view(raw);js=node_call({'feedback':fb,'view':view})
    if case=='identity':
        assert 'error' in js
        with pytest.raises(ValueError):review.validate_feedback(fb,view)
    else:
        result,_=review.validate_feedback(fb,view)
        assert js['result']['annotations']['A']['applicability_state']==result['annotations']['A']['applicability_state']


@pytest.mark.parametrize('change',['none','time','result','stale','legacy'])
def test_receipt_requires_both_versions(change):
    view=review.normalize_view(view_raw());fb,_=review.validate_feedback(package(view),view);record=fb['annotations']['A']
    receipt={'source_annotation_updated_at':record['updated_at'],'source_result_fingerprint':record['result_fingerprint']}
    if change=='time':receipt['source_annotation_updated_at']='old'
    elif change=='result':receipt['source_result_fingerprint']='wrong'
    elif change=='stale':record['applicability_state']='stale'
    elif change=='legacy':receipt.pop('source_result_fingerprint')
    assert node_call({'receipt':receipt,'record':record})['result']==('current' if change=='none' else 'stale')


def test_partial_subtotal_does_not_imply_complete_scope():
    import ast
    found=False
    for path in (ROOT/'skills/qto-surfaces-envelope/scripts').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PARTIAL_FORBIDDEN_WORDS' for t in node.targets):
                words=ast.literal_eval(node.value);found=True
                assert '合计' not in words
                assert '总量' in words or '完整' in words
    assert found


def test_runtime_manifest_matches_actual_source_bytes():
    data=json.loads((ROOT/'运行文件清单.json').read_text(encoding='utf-8'))
    assert data['runtime_file_count']==len(data['files'])
    assert len({r['path'] for r in data['files']})==len(data['files'])
    for entry in data['files']:
        raw=(ROOT/'skills'/entry['path']).read_bytes()
        assert len(raw)==entry['bytes'],entry['path']
        assert hashlib.sha256(raw).hexdigest()==entry['sha256'],entry['path']
