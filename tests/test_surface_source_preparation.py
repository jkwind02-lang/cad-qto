"""Synthetic CAD only: source-to-manifest handoff and explicit condition guards."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/_qto-shared-evidence/scripts'),
               str(ROOT / 'skills/qto-surfaces-envelope/scripts')]
import prepare_source_specialists as adapter
import qto_recognition_workbench as workbench
import read_cad_scope as reader
from extract_surface_linework import extract
from calculate_surfaces_envelope import calculate_from_path


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def bound(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def case(tmp_path):
    d = ezdxf.new(); d.units = 4; m = d.modelspace()
    edges = [m.add_line(a,b) for a,b in [((0,0),(3000,0)),((3000,0),(3000,2000)),
                                       ((3000,2000),(0,2000)),((0,2000),(0,0))]]
    label = m.add_text('Room A', dxfattribs={'insert':(500,500)})
    entries = {'floor':('1F',(19500,10200)), 'row':('Room A, Room B',(20200,10600)),
               'code1':('F',(22200,10600)), 'code2':('1',(22600,10600)),
               'column':('Floor finish',(22200,16000)), 'other':('1',(24200,10600)),
               'definition':('F1',(26500,16000))}
    entities = {k:m.add_text(text,dxfattribs={'insert':xy}) for k,(text,xy) in entries.items()}
    d.layers.new('auxiliary', dxfattribs={'plot':0})
    entities['nonplot'] = m.add_text('10 tile', dxfattribs={'layer':'auxiliary','insert':(26500,18000)})
    clauses = m.add_mtext('10 tile\n35 screed; stairs 15\n230/380 fill subject to slab levels\nC20 slope minimum 30\nRC slab',dxfattribs={'insert':(26500,14000)})
    file = tmp_path/'synthetic.dxf'; d.saveas(file); idx=workbench.build_index(bound(file)); ip=put(tmp_path/'index.json',idx)
    def oid(e):return 'CADINST-Model/'+e.dxf.handle
    def ref(key,excerpt=None):
        e=entities[key] if key in entities else clauses
        return dict(index=str(ip),index_sha256=idx['index_sha256'],object_id=oid(e),
                    applicability='declared table and room',interpretation='model-selected source clause',
                    **({'excerpt':excerpt} if excerpt else {}))
    def group(*parts):return dict(fragments=list(parts),interpretation='model-selected ordered fragments')
    q=dict(question='read selected row, column heading and merged floor context',context_regions=[
        dict(id='floor',role='merged_cell',bounds=[19000,10000,20000,11500]),
        dict(id='row',role='room_row',bounds=[20000,10400,25000,11000]),
        dict(id='cell',role='floor_code',bounds=[22000,10400,23000,11000]),
        dict(id='column',role='column_heading',bounds=[22000,15500,23000,16500])])
    response=reader.read_scope(idx,q,tmp_path/'context',max_bytes=15000)
    context=response['context_regions_ref']
    req=dict(schema_version='qto-surface-linework/1.0',drawing=bound(file),unit='mm',unit_evidence='synthetic millimetre drawing',
        scope=dict(view_id='room-plan',interpretation='model-selected inner boundary',bounds_raw=[-10,-10,3010,2010],plane_z_raw=0,plane_tolerance_m=0),
        segments=[dict(path=[e.dxf.handle]) for e in edges],label_paths=[[label.dxf.handle]])
    gp=put(tmp_path/'geometry-request.json',req);geometry=extract(req);rp=put(tmp_path/'geometry-result.json',geometry)
    chosen=[oid(label),*[oid(e) for e in edges]]
    ledger=dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='working',ledger_version='1',source_sha256=idx['authoritative_source']['sha256'],adoption_scope='working',review_status='unreviewed',
        evidence=[dict(evidence_id='EV',**bound(ip),source_sha256=idx['authoritative_source']['sha256'],kind='cad_property',locator='Model',observation='synthetic boundary and label')],
        entries=[dict(adjudication_id='ROOM',status='active',decision_origin='model',subject_id='surface',professional_owner='qto-surfaces-envelope',source_id='drawing',recognized_by='model',recognized_at='2026-01-01',basis='selected sources',scope_basis='one room',identity_basis='CAD instance',cross_check='source view',decision='include',object_ids=chosen,evidence_ids=['EV'],uncertainties=['reference surface'])])
    lp=put(tmp_path/'ledger.json',ledger)
    def parameter(status,value,quote):return dict(name='thickness',unit='mm',status=status,value=value,condition='source clause, no volume conversion',evidence_refs=[ref('clause',quote)])
    def condition(op):return [dict(field='room_name',operator=op,values=['stairs'],evidence_refs=[ref('clause','35 screed; stairs 15')])]
    layers=[dict(layer_id='tile',role='finish',object_type='floor_finish',clauses=group(ref('clause','10 tile')),parameters=[parameter('fixed',10,'10 tile')],coverage_basis='selected room footprint only',measurement='horizontal_projection'),
        dict(layer_id='screed',role='finish',object_type='other_surface',coverage_basis='selected room footprint',measurement='horizontal_projection',variants=[
            dict(conditions=condition('not_in'),clauses=group(ref('clause','35 screed; stairs 15')),parameters=[parameter('fixed',35,'35 screed')]),
            dict(conditions=condition('in'),clauses=group(ref('clause','35 screed; stairs 15')),parameters=[parameter('fixed',15,'stairs 15')])]),
        dict(layer_id='fill',role='finish',object_type='other_surface',clauses=group(ref('clause','230/380 fill subject to slab levels')),parameters=[dict(parameter('conditional',None,'230/380 fill subject to slab levels'),alternatives=[230,380])],coverage_basis='horizontal extent only; slab level unresolved',measurement='horizontal_projection'),
        dict(layer_id='slope',role='finish',object_type='other_surface',clauses=group(ref('clause','C20 slope minimum 30')),parameters=[parameter('minimum',30,'minimum 30')],coverage_basis='projected extent; average thickness unresolved',measurement='horizontal_projection'),
        dict(layer_id='slab',role='substrate',clauses=group(ref('clause','RC slab')),parameters=[],coverage_basis='structural substrate belongs to another quantity chain')]
    operations=dict(target='surfaces',index_sha256=idx['index_sha256'],scope_object_ids=chosen,scope_basis='one reviewed room',
        task=dict(project_id='synthetic',business_discipline='surfaces',main_objects='selected room',system='finishes',building='B',zone='1F',drawing_type='synthetic',drawing_version='1',output_dimension='reference area',applicable_rule_status='unresolved'),
        rules=[dict(rule_id='R-1',name='reference projected area',source='synthetic',decision='试用',maturity='候选',execution='selected source face only',evidence_locator='Model')],rule_refs=['R-1'],
        unresolved=[dict(unresolved_id='U-1',type='范围未定',description='finish boundary unknown',impact='reference only',required_input='nodes and slab levels',action='保留已证实部分')],
        definitions=[dict(id='finish',code=group(ref('definition')),column_role='floor',conditions=[],layers=layers)],
        surfaces=[dict(surface_id='surface-A',object_id=oid(label),adjudication_id='ROOM',name='Room A',scope_fields={'floor':'1F'},definition_id='finish',
            assignment=dict(code=group(ref('code1'),ref('code2')),row=group(ref('row')),column=group(ref('column')),floor=group(ref('floor')),column_role='floor',context=context,regions={k:k for k in ['floor','row','column','cell']}),
            source_face=dict(request=bound(gp),result=bound(rp),face_id=geometry['faces'][0]['face_id']),item_ids={l['layer_id']:'QTY-'+l['layer_id'] for l in layers},side_scope='top',measurement_basis='source_inner_projection',result_use='local reference only',unresolved_refs=['U-1'])])
    op=tmp_path/'operations.json'
    return dict(index=idx,operations=operations,ledger=lp,path=op,root=tmp_path,ref=ref,geometry=rp)


def prepare(case):
    put(case['path'],case['operations'])
    return adapter.prepare(case['index'],case['operations'],case['ledger'],case['root'],operations_path=case['path'])


def test_source_face_and_conditions_reach_existing_calculator(case):
    manifest=prepare(case);p=put(case['root']/'manifest.json',manifest);result=calculate_from_path(p)
    assert [x['结果']['数值'] for x in result['工程量结果']]==[6]*4
    assert len(manifest['source_preparation']['excluded_layers'])==1
    params={x['layer_id']:x['source_bound_layer']['parameters'][0] for x in manifest['items']}
    assert params['screed']['value']==35
    assert params['fill']['value'] is None and params['fill']['alternatives']==[230,380]
    assert params['slope']['status']=='minimum' and params['slope']['value']==30
    assert all(x['adoption_role']=='reference' and x['scope_status']=='partial' for x in manifest['items'])
    assert all('volume' not in x['inputs'] for x in manifest['items'])


@pytest.mark.parametrize('change,match', [('area','覆盖'),('neighbor','邻列'),('floor','楼层'),('stairs','源标签'),('unknown','未决或条件'),('volume','不得自动转体积'),('code','编号'),('geometry','复算不一致'),('context','行列上下文')])
def test_false_handoffs_stop_instead_of_defaulting(case,change,match):
    op=case['operations'];s=op['surfaces'][0];layers=op['definitions'][0]['layers']
    if change=='area':s['area_m2']=999
    if change=='neighbor':s['assignment']['column_role']='skirting'
    if change=='floor':s['scope_fields']['floor']='2F'
    if change=='stairs':s['scope_fields']['room_name']='stairs'
    if change=='unknown':layers[2]['parameters'][0]['value']=230
    if change=='volume':layers[3]['measurement']='volume_from_minimum'
    if change=='code':op['definitions'][0]['code']['fragments']=[case['ref']('other')]
    if change=='geometry':
        g=json.loads(case['geometry'].read_text());g['faces'][0]['area_m2']=999;put(case['geometry'],g);s['source_face']['result']=bound(case['geometry'])
    if change=='context':s['assignment']['code']['fragments'][1]=case['ref']('other')
    with pytest.raises(ValueError,match=match):prepare(case)


def test_no_default_layer_or_thickness(case):
    del case['operations']['definitions'][0]['layers'][0]['parameters']
    with pytest.raises(ValueError,match='参数须为数组'):prepare(case)


def test_source_snapshot_change_is_rejected(case):
    case['operations']['surfaces'][0]['assignment']['code']['fragments'][0]['index_sha256']='0'*64
    with pytest.raises(ValueError,match='快照'):prepare(case)


def test_nonplot_clause_cannot_drive_a_layer(case):
    case['operations']['definitions'][0]['layers'][0]['clauses']['fragments'] = [case['ref']('nonplot')]
    with pytest.raises(ValueError, match='不出图'):prepare(case)


def test_condition_without_scope_evidence_cannot_choose_default(case):
    variants = case['operations']['definitions'][0]['layers'][1]['variants']
    for v in variants:v['conditions'][0]['field']='unobserved_condition'
    with pytest.raises(ValueError, match='条件字段缺失'):prepare(case)


def test_context_display_box_cannot_be_swapped(case):
    s=case['operations']['surfaces'][0]
    p=Path(s['assignment']['context']['path']);c=json.loads(p.read_text())
    c['regions'][0]['bounds']=[0,0,999999,999999]
    changed=put(case['root']/'changed-context.json',c)
    s['assignment']['context']=bound(changed)
    with pytest.raises(ValueError, match='显示范围'):prepare(case)


def test_layer_scope_correction_leaves_other_native_rows_unchanged(case):
    before=prepare(case);first=calculate_from_path(put(case['root']/'first.json',before))
    case['operations']['definitions'][0]['layers'][2]['result_use']='reference footprint; selected thickness awaits slab levels'
    after=prepare(case);second=calculate_from_path(put(case['root']/'second.json',after))
    for a,b in zip(first['工程量结果'],second['工程量结果']):
        assert a['结果']==b['结果']
        if a['成果项编号']=='QTY-fill':
            assert a['专业检查']['结果用途']!=b['专业检查']['结果用途']
        else:assert a==b


def test_surface_workflow_registers_context_geometry_and_all_source_refs(case):
    from qto_workflow_dependencies import validate_dependencies
    put(case['path'],case['operations'])
    files=[p for p in case['root'].rglob('*') if p.is_file()]
    artifacts={str(n):dict(path=str(p)) for n,p in enumerate(files)}
    ids={Path(r['path']):k for k,r in artifacts.items()}
    task=dict(id='prepare',input_ids=list(artifacts),action=dict(tool='source_specialist_prepare',arguments={
        'index':ids[case['root']/'index.json'],'operations':ids[case['path']],'ledger':ids[case['ledger']]}))
    assert validate_dependencies(task,artifacts,case['root'])['errors']==[]
    surface=case['operations']['surfaces'][0]
    for path in [case['geometry'],Path(surface['source_face']['request']['path']),
                 Path(surface['assignment']['context']['path']),Path(case['index']['source']['path'])]:
        changed=copy.deepcopy(task);changed['input_ids'].remove(ids[path])
        assert validate_dependencies(changed,artifacts,case['root'])['errors']
