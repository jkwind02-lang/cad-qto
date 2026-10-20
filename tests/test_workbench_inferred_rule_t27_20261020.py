"""Explicit lower-evidence workbench pipe production; no SI fallback."""
import copy
import json
from pathlib import Path
import shutil
import sys

import ezdxf
import pytest

from test_component_geometric_units_t27_20261020 import component_case, case
from test_geometric_units_t27_20261019 import sha, put, command, save_source, credential, SHARED, PIPE, units, extract
from test_recognition_workbench_20261011 import ledger
import qto_recognition_workbench as wb


def refresh(c):
    save_source(c);doc=c['doc'];h=sha(c['path'])
    rows=dict(layout_entities=[extract.convert(e,layout=l.name,parent_block=None,source_hash=h,raw_unit='Unitless',sequence=i)
        for l in doc.layouts for i,e in enumerate(l,1)],
        block_definitions=[extract.convert(e,layout='Model',parent_block=b.name,source_hash=h,raw_unit='Unitless',sequence=i)
        for b in doc.blocks if not b.is_any_layout for i,e in enumerate(b,1)])
    for key,values in rows.items():
        artifact=c['manifest']['artifacts'][key];path=Path(artifact['path'])
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in values),encoding='utf-8')
        artifact['sha256']=sha(path)
    put(c['manifest_path'],c['manifest'])


def prepare(c,kind='component'):
    if kind=='dim':
        c['request']['component_dimension_refs']=[]
        c['request']['dimension_refs']=[dict(handle=c['handles']['dim'])]
    if kind=='si':
        text=c['doc'].modelspace().add_text('本图坐标单位：mm',dxfattribs={'insert':(1000,6000)})
        c['request']['coordinate_unit_refs']=[dict(handle=text.dxf.handle)];refresh(c)
    cred=credential(c);bounds=[c['scope']['bounds'][k] for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')]
    index=wb.build_index(c['manifest']['exchange_dxf'],scope_bounds=bounds)
    line=next(r['object_id'] for r in index['objects'] if r['source_handle']==c['handles']['pipe'])
    note=next(r['object_id'] for r in index['objects'] if r.get('text')=='DN25')
    lp=ledger(index,c['tmp'],[line,note])
    ops=dict(index_sha256=index['index_sha256'],network_id='T27-WORKBENCH',scope_object_ids=[line,note],
        scope_basis='同源当前图框内原生管段与DN标注',unit='m',geometric_unit_evidence=cred,
        measurement_scope=c['scope'],factor_to_output=.001,parameters=dict(snap_tolerance=.0001,gap_max=.1),
        parameter_basis='匿名图原生端点相接；不自动补量',
        assignments=[dict(adjudication_id='PIPE',object_ids=[line],role='pipe_centerline',system='W',observation='原管段中心线')],
        relations=[dict(relation_id='DN',kind='dn_annotation',target_object_id=line,annotation_object_id=note,
            annotation_text='DN25',dn='DN25',adjudication_id='PIPE',observation='真实DN25对应本管段'),
            dict(relation_id='ROOT',kind='root',target=dict(object_id=line,end='start'),adjudication_id='PIPE',observation='原入口'),
            dict(relation_id='END',kind='terminal',target=dict(object_id=line,end='end'),terminal_type='已识别末端接口',
                adjudication_id='PIPE',observation='原末端',port_dn='DN25',port_dn_annotation_object_ids=[note])],
        endpoint_review=dict(complete=False,basis='仅本次局部，不声称全图端口完整'))
    if kind!='si':ops['unit_consumption_mode']='inferred_rule'
    files=None
    if kind!='si':
        inputs=[('index',put(c['tmp']/'producer-index.json',index)),('operations',put(c['tmp']/'producer-ops.json',ops))]
        files={k:dict(path=str(p),sha256=sha(p)) for k,p in inputs}
    result=wb.prepare_pipe(index,ops,lp,input_files=files)
    c.update(cred=cred,index=index,ops=ops,ledger=lp,prepared=result,input_files=files)
    return c


@pytest.fixture
def pipeline(component_case):
    return prepare(component_case)


def infer(c,data,name='infer'):
    ip=put(c['tmp']/(name+'-input.json'),data);op=c['tmp']/(name+'-output.json')
    done=command(PIPE/'infer_pipe_network.py','--input',ip,'--output',op)
    return done,op


@pytest.mark.parametrize('kind',['component','dim','si'])
def test_legal_source_workbench_and_old_dim_si_controls(component_case,kind):
    c=prepare(component_case,kind);data=c['prepared']
    assert wb.verify_workbench_unit_consumption(data)
    assert data['unit_evidence_level']=='inferred'
    if kind!='si':
        receipt=data['geometric_unit_consumption']
        assert receipt['schema_version']=='qto-geometric-inferred-rule-consumption/1.0'
        assert receipt['quantity_level']=='规则推算量' and receipt['allows_physical_si_measurement'] is False
        assert receipt['checked_evidence']['physical_unit_basis'] is None
        with pytest.raises(units.GeometricUnitUnresolved):
            units.verify_geometric_unit_consumption(c['cred'],c['manifest'],c['scope'],.001)
        ops=copy.deepcopy(c['ops']);ops.pop('unit_consumption_mode')
        with pytest.raises(units.GeometricUnitUnresolved):wb.prepare_pipe(c['index'],ops,c['ledger'])
    else:
        bad=copy.deepcopy(c['ops']);bad['unit_consumption_mode']='inferred_rule'
        with pytest.raises(ValueError,match='降格'):wb.prepare_pipe(c['index'],bad,c['ledger'])
    done,output=infer(c,data)
    assert done.returncode==0,(done.stdout,done.stderr)
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['metrics']['raw_length']==10.
    assert result['segments'][0]['quantity_level']=='规则推算量'


@pytest.mark.parametrize('mutation',['receipt','all_tags','all_units','geometry','parent','scope','ledger','recipe_ops','recipe_index','dependency','source_refs','missing_inputs','operations_file','index_file','axis','ledger_file','stripped_geometry'])
def test_receipt_and_current_source_replay_cannot_be_downgraded(pipeline,mutation):
    c=pipeline;bad=copy.deepcopy(c['prepared']);receipt=bad['geometric_unit_consumption']
    if mutation=='receipt':bad.pop('geometric_unit_consumption')
    elif mutation in ('all_tags','all_units'):
        bad.pop('workbench');bad.pop('geometric_unit_consumption')
        for seg in bad['segments']:
            seg.pop('workbench_object_ids',None);seg.pop('physical_id',None)
        if mutation=='all_units':bad.pop('geometric_unit_evidence');bad.pop('unit_evidence_level');bad.pop('measurement_scope')
    elif mutation=='geometry':
        seg=bad['segments'][0];seg['length']=11.;seg['end'][0]=21.;seg['geometry']['parts'][0]['end'][0]=21.
    elif mutation=='parent':bad['segments'][0]['source_instance_path']='CAD-DEAD'
    elif mutation=='scope':bad['measurement_scope']['bounds']['xmax_exclusive']-=1.
    elif mutation=='ledger':bad['recognition_basis']['ledger_sha256']='0'*64
    elif mutation=='recipe_ops':receipt['workbench_production']['operations']['relations']=[]
    elif mutation=='recipe_index':receipt['workbench_production']['index_sha256']='0'*64
    elif mutation=='dependency':receipt['workbench_production']['producer_dependencies'].popitem()
    elif mutation=='missing_inputs':receipt['workbench_production'].pop('input_files')
    elif mutation=='operations_file':Path(c['input_files']['operations']['path']).write_text('{}',encoding='utf-8')
    elif mutation=='index_file':Path(c['input_files']['index']['path']).write_text('{}',encoding='utf-8')
    elif mutation=='axis':bad['geometric_unit_evidence']['request']['component_dimension_refs'][0]['measurement_axis']='Y'
    elif mutation=='ledger_file':c['ledger'].write_text('{}',encoding='utf-8')
    elif mutation=='stripped_geometry':
        bad.pop('workbench');bad.pop('geometric_unit_consumption')
        seg=bad['segments'][0];seg['length']=11.;seg['end'][0]=21.;seg['geometry']['parts'][0]['end'][0]=21.
    elif mutation=='source_refs':
        bad.pop('geometric_unit_consumption');bad.pop('workbench')
        for seg in bad['segments']:
            for key in ('source_ids','source_record_id','source_instance_path','workbench_object_ids'):seg.pop(key,None)
    done,_=infer(c,bad,'bad-'+mutation)
    assert done.returncode==2,(done.stdout,done.stderr)


def test_source_and_ledger_drift_rejected(pipeline):
    c=pipeline;c['path'].write_bytes(c['path'].read_bytes()+b'\n')
    done,_=infer(c,c['prepared'],'source-drift');assert done.returncode==2


def test_actual_producer_dependency_bytes_rechecked_after_tag_stripping(pipeline):
    c=pipeline;copy_root=c['tmp']/'isolated-plugin'
    shutil.copytree(SHARED.parents[2],copy_root,ignore=shutil.ignore_patterns('.git','.codex_work','__pycache__','.pytest_cache','*.pyc'))
    consumer=copy_root/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'
    ip=put(c['tmp']/'copied-consumer-input.json',c['prepared'])
    done=command(consumer,'--input',ip,'--output',c['tmp']/'same-byte.json')
    assert done.returncode==0,(done.stdout,done.stderr)
    module=copy_root/'skills/_qto-shared-evidence/scripts/qto_recognition_workbench.py'
    module.write_bytes(module.read_bytes()+b'\n# actual producer dependency drift\n')
    done=command(consumer,'--input',ip,'--output',c['tmp']/'drift.json')
    assert done.returncode==2 and '字节变化' in done.stderr
    stripped=copy.deepcopy(c['prepared']);stripped.pop('workbench');stripped.pop('geometric_unit_consumption')
    ip=put(c['tmp']/'stripped-drift-input.json',stripped)
    done=command(consumer,'--input',ip,'--output',c['tmp']/'stripped-drift.json')
    assert done.returncode==2,(done.stdout,done.stderr)


def test_t29_physical_adapter_explicitly_refuses_inferred_rule(pipeline):
    sys.path.insert(0,str(PIPE))
    from produce_pipe_diagram_network import workbench_si_context
    with pytest.raises(ValueError,match='SI|坐标'):
        workbench_si_context(pipeline['prepared'])


@pytest.mark.parametrize('target',['index','operations','authority'])
def test_infer_output_cannot_overwrite_frozen_producer_inputs(pipeline,target):
    c=pipeline;source=c['authority'] if target=='authority' else Path(c['input_files'][target]['path'])
    original=source.read_bytes();ip=put(c['tmp']/'overwrite-input.json',c['prepared'])
    done=command(PIPE/'infer_pipe_network.py','--input',ip,'--output',source)
    assert done.returncode==2 and source.read_bytes()==original


def legacy_declaration(tmp_path,mixed=False):
    doc=ezdxf.new();doc.units=0;model=doc.modelspace()
    line=model.add_line((0,0),(1000,0))
    unit_text='标高以米，其余尺寸为毫米' if mixed else '本图坐标单位：毫米'
    model.add_text(unit_text,dxfattribs={'insert':(0,100)})
    model.add_text('DN25',dxfattribs={'insert':(500,0)})
    source=tmp_path/'legacy-source.dxf';doc.saveas(source)
    index=wb.build_index(dict(path=str(source),sha256=sha(source)))
    oids=[r['object_id'] for r in index['objects']]
    pipe=next(r['object_id'] for r in index['objects'] if r['source_handle']==line.dxf.handle)
    note=next(r['object_id'] for r in index['objects'] if r.get('text')==unit_text)
    dn=next(r['object_id'] for r in index['objects'] if r.get('text')=='DN25')
    lp=ledger(index,tmp_path,oids)
    ops=dict(index_sha256=index['index_sha256'],network_id='OLD-DECLARATION',scope_object_ids=oids,
        scope_basis='旧明确单位说明与原管段',unit='m',parameters=dict(snap_tolerance=.001,gap_max=.1),
        parameter_basis='原图端点与毫米坐标',unit_declaration=dict(raw_unit='Millimeters',adjudication_id='PIPE',
            annotation_object_ids=[note],observation='真实明确坐标单位',applies_to='coordinates'),
        assignments=[dict(adjudication_id='PIPE',object_ids=[pipe],role='pipe_centerline',system='W',observation='原生线')],
        relations=[dict(relation_id='DN',kind='dn_annotation',target_object_id=pipe,annotation_object_id=dn,
            annotation_text='DN25',dn='DN25',adjudication_id='PIPE',observation='原标注对应原线')],
        endpoint_review=dict(complete=False,basis='旧局部端口未决'))
    if mixed:
        ops['unit_declaration'].update(source_excerpt='其余尺寸为毫米',geometric_corroboration=[dict(
            kind='known_component_size',real_length_m=1.,measured_drawing_units=1000.,object_ids=[pipe],basis='匿名既有单位佐证原定尺短管1米')])
    return dict(tmp=tmp_path,path=source,index=index,ops=ops,ledger=lp,prepared=wb.prepare_pipe(index,ops,lp))


@pytest.mark.parametrize('mixed',[False,True])
def test_legacy_unitless_explicit_declaration_new_and_historical_inputs_remain_usable(tmp_path,mixed):
    c=legacy_declaration(tmp_path,mixed)
    assert 'geometric_unit_evidence' not in c['prepared'] and 'geometric_unit_consumption' not in c['prepared']
    done,output=infer(c,c['prepared'],'legacy-declaration')
    assert done.returncode==0,(done.stdout,done.stderr)
    assert json.loads(output.read_text(encoding='utf-8'))['segments'][0]['length']==1.


@pytest.mark.parametrize('mutation',['declaration','annotation','applies','geometry','parent','ledger','source'])
def test_legacy_declaration_replays_real_source_and_rejects_forgery(tmp_path,mutation):
    c=legacy_declaration(tmp_path);bad=copy.deepcopy(c['prepared'])
    if mutation=='declaration':bad['workbench']['unit_declaration']['raw_unit']='Meters'
    elif mutation=='annotation':bad['workbench']['unit_declaration']['annotation_object_ids']=[bad['workbench']['bindings'][0]['object_id']]
    elif mutation=='applies':bad['workbench']['unit_declaration']['applies_to']='annotation_values'
    elif mutation=='geometry':bad['segments'][0]['length']=2.;bad['segments'][0]['end'][0]=2.;bad['segments'][0]['geometry']['parts'][0]['end'][0]=2.
    elif mutation=='parent':bad['workbench']['bindings'][0]['object_id']='CADINST-Model/DEAD'
    elif mutation=='ledger':c['ledger'].write_text('{}',encoding='utf-8')
    elif mutation=='source':c['path'].write_bytes(c['path'].read_bytes()+b'\n')
    done,_=infer(c,bad,'legacy-bad-'+mutation)
    assert done.returncode==2,(done.stdout,done.stderr)


def test_public_unit_index_apply_and_infer_actual_cli(component_case):
    c=prepare(component_case)
    request=put(c['tmp']/'unit-request.json',c['request']);scope=put(c['tmp']/'unit-scope.json',c['scope'])
    credential_path=c['tmp']/'unit-credential.json'
    done=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',c['manifest_path'],
        '--request',request,'--scope',scope,'--output',credential_path)
    assert done.returncode==0,(done.stdout,done.stderr)
    index_path=c['tmp']/'native-index.json';source=put(c['tmp']/'native-source.json',c['manifest']['exchange_dxf'])
    done=command(SHARED/'qto_recognition_workbench.py','index','--source',source,'--bounds',0,0,84100,59400,'--output',index_path)
    assert done.returncode==0,(done.stdout,done.stderr)
    index=json.loads(index_path.read_text(encoding='utf-8'));ops=copy.deepcopy(c['ops'])
    ops['index_sha256']=index['index_sha256'];ops['geometric_unit_evidence']=json.loads(credential_path.read_text(encoding='utf-8'))
    operations=put(c['tmp']/'native-operations.json',ops);built=c['tmp']/'workbench-pipe-input.json'
    done=command(SHARED/'qto_recognition_workbench.py','apply','--index',index_path,'--operations',operations,'--ledger',c['ledger'],'--output',built)
    assert done.returncode==0,(done.stdout,done.stderr)
    output=c['tmp']/'workbench-pipe-result.json'
    done=command(PIPE/'infer_pipe_network.py','--input',built,'--output',output)
    assert done.returncode==0,(done.stdout,done.stderr)
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['segments'][0]['length']==10. and result['segments'][0]['quantity_level']=='规则推算量'


def native_unit_separate_authority(tmp_path,unit_code=4,geometry='line',bounds=None):
    doc=ezdxf.new();doc.units=unit_code;model=doc.modelspace()
    length={4:1000.,5:100.,6:1.}[unit_code]
    line=(model.add_arc((0,0),length,0,90) if geometry=='arc' else
          model.add_lwpolyline([(0,0),(length/2,0),(length/2,length/2)]) if geometry=='polyline' else
          model.add_line((0,0),(length,0)))
    note=model.add_text('DN25',dxfattribs={'insert':(length/2,0)})
    exchange=tmp_path/'native-exchange.dxf';doc.saveas(exchange)
    # A frozen anonymous authority identity payload; no DWG geometry is decoded.
    authority=tmp_path/'independent-authority.dwg'
    authority.write_bytes(b'AC1027 anonymous authority identity fixture only')
    authority_ref=dict(path=str(authority),sha256=sha(authority))
    index=wb.build_index(dict(path=str(exchange),sha256=sha(exchange)),authoritative_source=authority_ref,scope_bounds=bounds)
    oids=[r['object_id'] for r in index['objects']]
    pipe=next(r['object_id'] for r in index['objects'] if r['source_handle']==line.dxf.handle)
    dn=next(r['object_id'] for r in index['objects'] if r['source_handle']==note.dxf.handle)
    lp=ledger(index,tmp_path,oids)
    ops=dict(index_sha256=index['index_sha256'],network_id='NATIVE-SEPARATE-AUTHORITY',scope_object_ids=oids,
        scope_basis='原生明确单位交换与独立权威来源',unit='m',parameters=dict(snap_tolerance=.001,gap_max=.1),
        parameter_basis='有限原生坐标',assignments=[dict(adjudication_id='PIPE',object_ids=[pipe],
            role='pipe_centerline',system='W',observation='原线及原单位')],
        relations=[dict(relation_id='DN',kind='dn_annotation',target_object_id=pipe,annotation_object_id=dn,
            annotation_text='DN25',dn='DN25',adjudication_id='PIPE',observation='原DN25对应原线')],
        endpoint_review=dict(complete=False,basis='局部未决端口保留'))
    return dict(tmp=tmp_path,path=exchange,authority=authority,index=index,ops=ops,ledger=lp,
        prepared=wb.prepare_pipe(index,ops,lp))


@pytest.mark.parametrize('unit_code',[4,5,6])
def test_f01_native_insunits_with_separate_authority_dwg_stays_usable(tmp_path,unit_code):
    c=native_unit_separate_authority(tmp_path,unit_code)
    assert c['prepared']['workbench']['unit_declaration'] is None
    assert wb.workbench_unit_consumption_required(c['prepared']) is True
    done,output=infer(c,c['prepared'],'f01-native')
    assert done.returncode==0,(done.stdout,done.stderr)
    assert json.loads(output.read_text(encoding='utf-8'))['segments'][0]['length']==1.


@pytest.mark.parametrize('mutation',['exchange','authority','ledger','used_authority','source_missing','native_unit_claim'])
def test_f01_separate_authority_native_path_rechecks_source_bindings(tmp_path,mutation):
    c=native_unit_separate_authority(tmp_path);bad=copy.deepcopy(c['prepared'])
    if mutation=='exchange':c['path'].write_bytes(c['path'].read_bytes()+b'\n')
    elif mutation=='authority':c['authority'].write_bytes(c['authority'].read_bytes()+b'changed')
    elif mutation=='ledger':c['ledger'].write_text('{}',encoding='utf-8')
    elif mutation=='used_authority':
        value=json.loads(c['ledger'].read_text(encoding='utf-8'));value['entries'][0]['evidence_ids']=[]
        put(c['ledger'],value);bad['recognition_basis']['ledger_sha256']=sha(c['ledger'])
    elif mutation=='source_missing':bad['workbench'].pop('source')
    elif mutation=='native_unit_claim':bad['workbench']['raw_unit_used']='Meters'
    done,_=infer(c,bad,'f01-bad-'+mutation)
    assert done.returncode==2,(done.stdout,done.stderr)


@pytest.mark.parametrize('mutation',['valid_other_source','claims_other_source','fake_geometry','fake_parent','fake_role','fake_index','fake_professional_output'])
def test_f02_native_path_always_replays_original_source_index_and_professional_output(tmp_path,mutation):
    c=native_unit_separate_authority(tmp_path);bad=copy.deepcopy(c['prepared'])
    if mutation in ('valid_other_source','claims_other_source'):
        doc=ezdxf.new();doc.units=6 if mutation=='claims_other_source' else 4
        doc.modelspace().add_line((0,0),(999,0));path=tmp_path/'other-valid.dxf';doc.saveas(path)
        bad['workbench']['source']=dict(path=str(path),sha256=sha(path))
        if mutation=='claims_other_source':bad['workbench'].update(source_declared_unit='Meters',raw_unit_used='Meters')
    elif mutation=='fake_geometry':
        seg=bad['segments'][0];seg['length']=30.;seg['end'][0]=30.;seg['geometry']['parts'][0]['end'][0]=30.
    elif mutation=='fake_parent':bad['workbench']['bindings'][0]['source_path']=['DEAD']
    elif mutation=='fake_role':bad['workbench']['production_operations']['assignments'][0]['role']='container'
    elif mutation=='fake_index':bad['workbench']['index_sha256']='0'*64
    elif mutation=='fake_professional_output':bad['object_selection']['included_count']=999
    done,_=infer(c,bad,'f02-'+mutation)
    assert done.returncode==2,(done.stdout,done.stderr)


def test_f02_rule_cannot_strip_receipt_and_replace_source_then_handfill_30m(pipeline):
    c=pipeline;bad=copy.deepcopy(c['prepared'])
    for key in ('geometric_unit_evidence','geometric_unit_consumption','unit_evidence_level','measurement_scope','geometric_unit_source_binding'):
        bad.pop(key,None)
    doc=ezdxf.new();doc.units=4;doc.modelspace().add_line((0,0),(30000,0))
    path=c['tmp']/'replacement-known-unit.dxf';doc.saveas(path)
    bad['workbench']['source']=dict(path=str(path),sha256=sha(path))
    bad['workbench'].update(source_declared_unit='Millimeters',raw_unit_used='Millimeters')
    seg=bad['segments'][0];seg['length']=30.;seg['end'][0]=40.;seg['geometry']['parts'][0]['end'][0]=40.
    done,_=infer(c,bad,'f02-stripped-30m')
    assert done.returncode==2,(done.stdout,done.stderr)


@pytest.mark.parametrize('geometry',['arc','polyline'])
def test_f02_public_native_units_separate_dwg_local_scope_complete_cli(tmp_path,geometry):
    c=native_unit_separate_authority(tmp_path,geometry=geometry,bounds=[-200.,-200.,2000.,2000.])
    src=put(tmp_path/'native-source.json',dict(c['index']['source'],authoritative_source=c['index']['authoritative_source']))
    ip=tmp_path/'native-index.json'
    done=command(SHARED/'qto_recognition_workbench.py','index','--source',src,'--bounds',-200,-200,2000,2000,'--output',ip)
    assert done.returncode==0,(done.stdout,done.stderr)
    ops=copy.deepcopy(c['ops']);ops['index_sha256']=json.loads(ip.read_text(encoding='utf-8'))['index_sha256']
    op=put(tmp_path/'native-ops.json',ops);prepared=tmp_path/'native-prepared.json'
    done=command(SHARED/'qto_recognition_workbench.py','apply','--index',ip,'--operations',op,'--ledger',c['ledger'],'--output',prepared)
    assert done.returncode==0,(done.stdout,done.stderr)
    delivered=json.loads(prepared.read_text(encoding='utf-8'))
    assert delivered['workbench']['index_context']['bounds']==[-200.,-200.,2000.,2000.]
    done,out=infer(c,delivered,'native-bounded-'+geometry)
    assert done.returncode==0,(done.stdout,done.stderr)
    expected=__import__('math').pi/2 if geometry=='arc' else 1.
    assert json.loads(out.read_text(encoding='utf-8'))['metrics']['raw_length']==pytest.approx(expected)


def test_f02_historical_full_index_native_prepared_without_new_recipe_is_replayed(tmp_path):
    c=native_unit_separate_authority(tmp_path);historical=copy.deepcopy(c['prepared'])
    historical['workbench'].pop('index_context');historical['workbench'].pop('production_operations')
    done,out=infer(c,historical,'historical-native')
    assert done.returncode==0,(done.stdout,done.stderr)
    assert json.loads(out.read_text(encoding='utf-8'))['metrics']['raw_length']==1.


def test_f02_historical_local_index_without_decisive_context_requires_original_apply(tmp_path):
    c=native_unit_separate_authority(tmp_path,bounds=[-200.,-200.,2000.,2000.]);historical=copy.deepcopy(c['prepared'])
    historical['workbench'].pop('index_context');historical['workbench'].pop('production_operations')
    done,_=infer(c,historical,'historical-missing-bounds')
    assert done.returncode==2 and '原index/operations重新apply' in done.stderr


@pytest.mark.parametrize('mixed',[False,True])
def test_f02_historical_unit_declaration_without_new_recipe_keeps_same_evidence(tmp_path,mixed):
    c=legacy_declaration(tmp_path,mixed);old=copy.deepcopy(c['prepared'])
    old['workbench'].pop('index_context');old['workbench'].pop('production_operations')
    done,out=infer(c,old,'historical-declaration-'+str(mixed))
    assert done.returncode==0,(done.stdout,done.stderr)
    assert json.loads(out.read_text(encoding='utf-8'))['metrics']['raw_length']==1.
