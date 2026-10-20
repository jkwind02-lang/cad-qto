"""Physical unit receipts reach real electrical/workbench consumers, using saved anonymous DXF."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import ezdxf
import pytest

from test_electrical_object_binding_20261018 import fixture, run, SHARED, SCRIPTS, binding
from test_recognition_workbench_20261011 import ledger
import qto_geometric_unit_evidence as units
import qto_recognition_workbench as workbench
import partition_electrical_route_network as network
import trace_drawn_routes as trace


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


@pytest.fixture
def physical(tmp_path):
    _, req, obj, step, doc, line, basis = fixture(tmp_path/'source', unit=4)
    doc.units = 0
    m = doc.modelspace()
    frame=m.add_lwpolyline([(-20000,-20000),(64100,-20000),(64100,39400),(-20000,39400)],close=True)
    scale=m.add_text('1:100',dxfattribs={'insert':(50000,-18000),'height':100})
    title=m.add_text('匿名电气平面图',dxfattribs={'insert':(-18000,-18000),'height':100})
    clause=m.add_text('坐标单位：毫米',dxfattribs={'insert':(1000,6000),'height':100})
    dn=m.add_text('DN25',dxfattribs={'insert':(1000,2000),'height':100})
    dim=m.add_aligned_dim((1000,1000),(7000,1000),distance=500,override={'dimlfac':1.})
    dim.render()
    # A stroke-only anonymous dimension avoids unsupported filled arrow solids.
    for block in doc.blocks:
        for solid in list(block.query('SOLID')):block.delete_entity(solid)
    path=tmp_path/'source'/'unitless.dxf';doc.saveas(path)
    neutral=tmp_path/'neutral'
    done=run([SHARED/'extract_cad_neutral.py','--source-dxf',path,'--output-dir',neutral])
    assert done.returncode==0,done.stdout+done.stderr
    mp=neutral/'CAD中性取证清单.json';mf=json.loads(mp.read_text(encoding='utf-8'))
    scope=dict(layout='Model',boundary_policy='contained',bounds=dict(xmin=-20000.,ymin=-20000.,xmax_exclusive=64100.,ymax_exclusive=39400.))
    ur=dict(schema_version=units.REQUEST_SCHEMA,raw_unit='Millimeters',measurement_semantics='plan_to_scale',
        frame=dict(entity_refs=[dict(handle=frame.dxf.handle)]),scale_text=dict(handle=scale.dxf.handle),
        view_text_refs=[dict(handle=title.dxf.handle)],dimension_refs=[dict(handle=dim.dimension.dxf.handle)],
        coordinate_unit_refs=[dict(handle=clause.dxf.handle)],basis='原坐标单位、图框、比例、图名及独立几何尺寸')
    cred=units.verify_geometric_unit_evidence(ur,mf,scope)
    cred['cad_manifest']=dict(path=str(mp),sha256=sha(mp))
    req.update(cad_manifest_sha256=sha(mp),source_sha256=mf['authoritative_source']['sha256'],measurement_scope=scope)
    req['unit']['geometric_unit_evidence']=cred
    for ep in req['endpoints']:ep['manifest_sha256']=sha(mp)
    return dict(tmp=tmp_path,path=path,manifest=mp,mf=mf,request=req,credential=cred,scope=scope,doc=doc,
                obj=obj,step=step,line=line.dxf.handle,basis=basis,dn=dn.dxf.handle,unit_request=ur,clause=clause.dxf.handle)


def test_public_receipt_replays_original_si_and_rejects_geometry_only(physical):
    c=physical
    receipt=units.verify_geometric_unit_consumption(c['credential'],c['mf'],c['scope'],.001)
    assert receipt['checked_evidence']['coordinate_unit_evidence'][0]['source_text']=='坐标单位：毫米'
    assert receipt['formal_adoption'] is False
    # Removing the original coordinate SI clause cannot promote numeric geometry.
    request=copy.deepcopy(c['unit_request']);request['coordinate_unit_refs']=[]
    with pytest.raises(ValueError,match='单位文字'):
        units.verify_geometric_unit_evidence(request,c['mf'],c['scope'])
    fake=copy.deepcopy(c['credential']);fake['request']['coordinate_unit_refs']=[]
    with pytest.raises(ValueError):units.verify_geometric_unit_consumption(fake,c['mf'],c['scope'],.001)
    for factor in (1.,0.,float('nan')):
        with pytest.raises(ValueError,match='倍率'):units.verify_geometric_unit_consumption(c['credential'],c['mf'],c['scope'],factor)
    other=copy.deepcopy(c['scope']);other['bounds']['xmin']+=1
    with pytest.raises(ValueError,match='scope'):units.verify_geometric_unit_consumption(c['credential'],c['mf'],other,.001)


def test_electrical_evidence_to_bound_object_and_existing_reference(physical):
    c=physical;req=copy.deepcopy(c['request'])
    req['objects']=[dict(c['obj']('C1'),parallel_count=2,conductor_instance_ids=['A','B'])]
    result=binding.calculate(binding.Evidence(c['manifest'],req),req)
    out=result['objects'][0]
    assert out['known_path_length_each_m']==pytest.approx(10)
    assert out['complete_length_m']==pytest.approx(20)
    assert result['provenance']['geometric_unit_consumption']['factor_to_output']==.001
    dependencies=result['provenance']['dependency_fingerprints_before']
    assert any(p.endswith('qto_geometric_unit_evidence.py') for p in dependencies)
    assert any(p.endswith('bind_electrical_objects.py') for p in dependencies)
    no=copy.deepcopy(req);no['unit'].pop('geometric_unit_evidence')
    with pytest.raises(ValueError):binding.Evidence(c['manifest'],no)
    wrong=copy.deepcopy(req);wrong['measurement_scope']=copy.deepcopy(c['scope']);wrong['measurement_scope']['bounds']['xmax_exclusive']-=1
    with pytest.raises(ValueError):binding.Evidence(c['manifest'],wrong)


def trace_request(c):
    return dict(schema=trace.REQUEST_SCHEMA,drawing_id='ANON',layout='Model',
        unit=dict(factor_to_output=.001,output_coordinate_unit='m',source_id='原坐标SI及几何凭据',geometric_unit_evidence=c['credential']),
        scope=dict(bounds=c['scope']['bounds']),measurement_scope=c['scope'],route_layers='^ROUTE$',expand_blocks=['DEVICE'],
        snap_tolerance=.000001,gap_tolerance=0.,endpoint_tolerance=.000001,
        routes=[dict(route_id='R',**{'from':dict(point=[0.,0.]),'to':dict(point=[10.,0.])})])


def test_trace_actual_cli_and_consumer_replay_cannot_trust_lengths_or_drop_dependencies(physical):
    c=physical;tr=trace_request(c);rp=put(c['tmp']/'trace-request.json',tr);out=c['tmp']/'trace'
    done=run([SCRIPTS/'trace_drawn_routes.py','--cad-manifest',c['manifest'],'--request',rp,'--output-dir',out])
    assert done.returncode==0,done.stdout+done.stderr
    tp=out/'走线追踪结果.json';result=json.loads(tp.read_text(encoding='utf-8'))
    assert result['routes'][0]['drawn_length_m']==pytest.approx(10)
    assert trace.verify_trace_geometric_consumption(result,c['manifest'])
    req=copy.deepcopy(c['request']);req['trace_result']=dict(path=str(tp),sha256=sha(tp))
    req['objects'][0].update(trace_route_id='R',trace_context=dict(sheet_id='S1',floor='F1',basis_source_ids=[c['basis']]));req['objects'][0].pop('steps')
    bound=binding.calculate(binding.Evidence(c['manifest'],req),req)
    assert bound['objects'][0]['known_path_length_each_m']==pytest.approx(10)
    bad=copy.deepcopy(result);bad['routes'][0]['drawn_length_m']=999.
    with pytest.raises(ValueError,match='重放'):trace.verify_trace_geometric_consumption(bad,c['manifest'])
    bad=copy.deepcopy(result);bad['geometric_unit_consumption'].pop('producer_dependencies')
    with pytest.raises(ValueError,match='依赖'):trace.verify_trace_geometric_consumption(bad,c['manifest'])
    bad=copy.deepcopy(tr);bad.pop('measurement_scope');bad['unit'].pop('geometric_unit_evidence')
    with pytest.raises(ValueError):trace.Drawing(c['manifest'],trace.parse_request(bad))


def test_partition_uses_public_receipt_and_keeps_single_length_outlet(physical):
    c=physical;obj=c['request']['endpoints']
    tr=trace_request(c);tr.pop('routes')
    req=dict(schema=network.REQUEST_SCHEMA,scope_id='LOCAL',project_id='ANON',cad_manifest_sha256=sha(c['manifest']),
        source_sha256=c['mf']['authoritative_source']['sha256'],trace_request=tr,
        endpoint_objects=[dict(object_key=k,category=cat,source_ids=[c['basis']],endpoint_binding=ep)
                          for k,cat,ep in zip(('A','B'),('source','lighting'),obj)],
        connections=[dict(id='SUPPLY',from_object='A',to_object='B',relation='supply',source_ids=[c['basis']])],
        circuit_specs=[],sheets=c['request']['sheets'])
    result=network.partition_network(c['manifest'],req)
    assert result['connections'][0]['traced_path_length_m']==pytest.approx(10)
    assert sum(a['length_m'] for a in result['physical_segments'])==pytest.approx(10)
    assert result['provenance']['geometric_unit_consumption']['output_unit']=='m'
    assert result['official_adoption'] is False


def test_workbench_actual_local_index_to_pipe_consumer(physical):
    c=physical;scope=c['scope'];b=scope['bounds']
    index=workbench.build_index(c['mf']['exchange_dxf'],authoritative_source=c['mf']['authoritative_source'],
        scope_bounds=[b[k] for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')])
    selected=[r for r in index['objects'] if r.get('source_handle') in (c['line'],c['dn']) or r['object_id'] in ('CAD-'+c['line'],'CAD-'+c['dn'])]
    # Preserve index-native identities rather than manufacture CAD paths.
    line=next(r['object_id'] for r in selected if r['entity_type']=='LINE')
    note=next(r['object_id'] for r in selected if r.get('text')=='DN25')
    lp=ledger(index,c['tmp'],[line,note])
    ops=dict(index_sha256=index['index_sha256'],network_id='LOCAL',scope_object_ids=[line,note],scope_basis='同源局部支路',unit='m',
        geometric_unit_evidence=c['credential'],measurement_scope=scope,factor_to_output=.001,
        parameters=dict(snap_tolerance=.0001,gap_max=.1),parameter_basis='原图接触与候选断口参数',
        assignments=[dict(adjudication_id='PIPE',object_ids=[line],role='pipe_centerline',system='W',observation='原线中心线')],
        relations=[dict(relation_id='DN',kind='dn_annotation',target_object_id=line,annotation_object_id=note,annotation_text='DN25',dn='DN25',adjudication_id='PIPE',observation='原管径标注'),
            dict(relation_id='ROOT',kind='root',target=dict(object_id=line,end='start'),adjudication_id='PIPE',observation='入口'),
            dict(relation_id='END',kind='terminal',target=dict(object_id=line,end='end'),terminal_type='已识别末端接口',adjudication_id='PIPE',observation='末端',port_dn='DN25',port_dn_annotation_object_ids=[note])],
        endpoint_review=dict(complete=False,basis='仅闭合本次选定局部支路，未处理原源其它对象保留'))
    prepared=workbench.prepare_pipe(index,ops,lp)
    assert sum(s['length'] for s in prepared['segments'])==pytest.approx(10)
    ip=put(c['tmp']/'pipe-input.json',prepared);op=c['tmp']/'pipe-result.json'
    done=run([SHARED.parent.parent/'qto-pipe-networks/scripts/infer_pipe_network.py','--input',ip,'--output',op])
    assert done.returncode==0,done.stdout+done.stderr
    result=json.loads(op.read_text(encoding='utf-8'));assert result['metrics']['raw_length']==pytest.approx(10)
    assert workbench.verify_workbench_unit_consumption(prepared)
    # The source-replayed SI inlet cannot be selected by removable metadata.
    for mutation in ('strip','strip_and_change_geometry','strip_label_only','strip_receipt_only'):
        bad=copy.deepcopy(prepared)
        if mutation!='strip_receipt_only':bad.pop('workbench')
        if mutation!='strip_label_only':bad.pop('geometric_unit_consumption')
        if mutation=='strip_and_change_geometry':
            bad['segments'][0]['length']=12.
            bad['segments'][0]['end'][0]=12.
            bad['segments'][0]['geometry']['parts'][0]['end'][0]=12.
        bp=put(c['tmp']/('stripped-'+mutation+'.json'),bad)
        done=run([SHARED.parent.parent/'qto-pipe-networks/scripts/infer_pipe_network.py','--input',bp,'--output',c['tmp']/('stripped-'+mutation+'-result.json')])
        assert done.returncode==2,done.stdout+done.stderr
    moved=c['tmp']/'relocated';moved.mkdir()
    source=moved/'same-source.dxf';shutil.copyfile(c['path'],source)
    moved_index=workbench.build_index(dict(path=str(source),sha256=sha(source)),authoritative_source=c['mf']['authoritative_source'],
        scope_bounds=[b[k] for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')])
    moved_ops=copy.deepcopy(ops);moved_ops['index_sha256']=moved_index['index_sha256']
    relocated=workbench.prepare_pipe(moved_index,moved_ops,lp)
    moved_input=put(c['tmp']/'relocated-input.json',relocated)
    done=run([SHARED.parent.parent/'qto-pipe-networks/scripts/infer_pipe_network.py','--input',moved_input,'--output',c['tmp']/'relocated-result.json'])
    assert done.returncode==0,done.stdout+done.stderr
    assert json.loads((c['tmp']/'relocated-result.json').read_text(encoding='utf-8'))['metrics']['raw_length']==pytest.approx(10.)
    changed_source=moved/'changed-source.dxf';changed_doc=ezdxf.readfile(source)
    changed_doc.entitydb[c['line']].dxf.end=(12000,0,0);changed_doc.saveas(changed_source)
    changed_index=workbench.build_index(dict(path=str(changed_source),sha256=sha(changed_source)),authoritative_source=c['mf']['authoritative_source'],
        scope_bounds=[b[k] for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')])
    changed_ops=copy.deepcopy(ops);changed_ops['index_sha256']=changed_index['index_sha256']
    with pytest.raises(ValueError,match='来源内容不一致'):workbench.prepare_pipe(changed_index,changed_ops,lp)
    for mutation in ('length','missing_recipe','missing_dependency'):
        bad=copy.deepcopy(prepared)
        if mutation=='length':bad['segments'][0]['length']=999.
        elif mutation=='missing_recipe':bad.pop('geometric_unit_consumption')
        else:bad['geometric_unit_consumption']['workbench_production']['producer_dependencies'].popitem()
        with pytest.raises(ValueError):workbench.verify_workbench_unit_consumption(bad)
    isolated=c['tmp']/'same-byte-checkout'
    shutil.copytree(SHARED.parent.parent.parent,isolated,ignore=shutil.ignore_patterns('.git','.codex_work','__pycache__','.pytest_cache','*.pyc'))
    consumer=isolated/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'
    done=run([consumer,'--input',ip,'--output',c['tmp']/'same-byte-result.json'])
    assert done.returncode==0,done.stdout+done.stderr
    changed_module=isolated/'skills/_qto-shared-evidence/scripts/qto_recognition_workbench.py'
    original=changed_module.read_bytes();changed_module.write_bytes(original+b'\n# changed producer dependency\n')
    done=run([consumer,'--input',ip,'--output',c['tmp']/'stale-result.json'])
    assert done.returncode!=0 and '字节变化' in done.stderr
    stripped=copy.deepcopy(prepared);stripped.pop('workbench');stripped.pop('geometric_unit_consumption')
    stripped_input=put(c['tmp']/'drift-without-tags.json',stripped)
    done=run([consumer,'--input',stripped_input,'--output',c['tmp']/'drift-without-tags-result.json'])
    assert done.returncode==2,done.stdout+done.stderr
    changed_module.write_bytes(original)
    done=run([consumer,'--input',ip,'--output',c['tmp']/'restored-result.json'])
    assert done.returncode==0,done.stdout+done.stderr
    changed=copy.deepcopy(ops);changed['factor_to_output']=1.
    with pytest.raises(ValueError,match='倍率'):workbench.prepare_pipe(index,changed,lp)


def test_runtime_dependency_bytes_and_source_changes_invalidate_consumers(physical,tmp_path):
    c=physical;req=copy.deepcopy(c['request']);e=binding.Evidence(c['manifest'],req)
    fake=copy.deepcopy(c['credential']);fake['runtime_dependencies'].pop()
    with pytest.raises(ValueError,match='依赖'):units.verify_geometric_unit_consumption(fake,c['mf'],c['scope'],.001)
    copied=tmp_path/'copied-source';shutil.copytree(SHARED.parent.parent.parent,copied,ignore=shutil.ignore_patterns('.git','.codex_work','__pycache__','.pytest_cache','*.pyc'))
    module=copied/'skills/_qto-shared-evidence/scripts/qto_cad_source_contract.py'
    module.write_bytes(module.read_bytes()+b'\n# changed consumer dependency\n')
    checker=put(tmp_path/'credential.json',c['credential']);scope=put(tmp_path/'scope.json',c['scope'])
    script=tmp_path/'check.py';script.write_text('import json,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom qto_geometric_unit_evidence import verify_geometric_unit_credential\nverify_geometric_unit_credential(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")),json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")))\n',encoding='utf-8')
    done=run([script,copied/'skills/_qto-shared-evidence/scripts',checker,scope]);assert done.returncode!=0
    c['path'].write_bytes(c['path'].read_bytes()+b'\n')
    with pytest.raises(ValueError):e.provenance()


def test_geometry_only_saved_source_never_opens_new_professional_measurement(physical):
    c=physical;doc=c['doc'];doc.modelspace().delete_entity(doc.entitydb[c['clause']]);doc.saveas(c['path'])
    neutral=c['tmp']/'no-si-neutral'
    done=run([SHARED/'extract_cad_neutral.py','--source-dxf',c['path'],'--output-dir',neutral]);assert done.returncode==0
    mp=neutral/'CAD中性取证清单.json';mf=json.loads(mp.read_text(encoding='utf-8'))
    ur=copy.deepcopy(c['unit_request']);ur.pop('coordinate_unit_refs')
    cred=units.verify_geometric_unit_evidence(ur,mf,c['scope']);cred['cad_manifest']=dict(path=str(mp),sha256=sha(mp))
    assert cred['physical_unit_basis'] is None and cred['allows_length_measurement'] is False
    with pytest.raises(ValueError,match='独立坐标 SI'):units.verify_geometric_unit_consumption(cred,mf,c['scope'],.001)
    req=copy.deepcopy(c['request']);req['unit']['geometric_unit_evidence']=cred
    with pytest.raises(ValueError,match='独立坐标 SI'):binding.Evidence(mp,req)
