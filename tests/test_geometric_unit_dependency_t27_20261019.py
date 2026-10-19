"""R2: actual source version and executing unit-verifier bytes bind quantity receipts."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import ezdxf
import pytest

from test_geometric_units_t27_20261019 import (ROOT, SHARED, PIPE, case, sha, put, command,
    credential, bind_actual_recognition, batch_input, extract, units)
from model_recognition_basis import recognition_basis


def input_for(case):
    data=dict(schema_version='qto-pipe-network-inference-input/1.0',network_id='ACTUAL',unit='m',
        geometric_unit_evidence=credential(case),measurement_scope=case['scope'],
        segments=[dict(segment_id='P',system='W',start=[10,30],end=[20,30],length=10.,
            source_ids=['CAD-'+case['handles']['pipe']],
            dn_seeds=[dict(dn='25',evidence_level='confirmed',source_id='ANON-DN',basis='明确标注')])])
    return bind_actual_recognition(case,data)


def other_source(case, *, same_authority):
    folder=case['tmp']/'other';folder.mkdir()
    path=folder/'other.dxf';doc=ezdxf.readfile(case['path'])
    doc.modelspace().add_text('另一个实际交换版本',dxfattribs={'insert':(1000,5000)})
    doc.saveas(path)
    primary=case['manifest']['authoritative_source'] if same_authority else dict(path=str(path),sha256=sha(path))
    layout=folder/'layout.ndjson';blocks=folder/'blocks.ndjson'
    rows=[extract.convert(e,layout=l.name,parent_block=None,source_hash=primary['sha256'],raw_unit='Unitless',sequence=i)
          for l in doc.layouts for i,e in enumerate(l,1)]
    definitions=[extract.convert(e,layout='Model',parent_block=b.name,source_hash=primary['sha256'],raw_unit='Unitless',sequence=i)
          for b in doc.blocks if not b.is_any_layout for i,e in enumerate(b,1)]
    for target,values in ((layout,rows),(blocks,definitions)):
        target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in values),encoding='utf-8')
    manifest=copy.deepcopy(case['manifest']);manifest.update(authoritative_source=primary,exchange_dxf=dict(path=str(path),sha256=sha(path)))
    manifest['artifacts']={'layout_entities':dict(path=str(layout),sha256=sha(layout)),
                           'block_definitions':dict(path=str(blocks),sha256=sha(blocks))}
    mp=put(folder/'manifest.json',manifest)
    checked=units.verify_geometric_unit_evidence(case['request'],manifest,case['scope'])
    checked['cad_manifest']=dict(path=str(mp),sha256=sha(mp))
    return checked


@pytest.mark.parametrize('same_authority',[False,True])
def test_actual_infer_rejects_other_source_or_other_exchange_with_identical_scope(case,same_authority):
    data=input_for(case)
    valid=put(case['tmp']/'valid.json',data);out=case['tmp']/'valid-result.json'
    good=command(PIPE/'infer_pipe_network.py','--input',valid,'--output',out)
    assert good.returncode==0,(good.stdout,good.stderr)
    before=copy.deepcopy(data)
    data['geometric_unit_evidence']=other_source(case,same_authority=same_authority)
    assert data['measurement_scope']==before['measurement_scope']
    assert data['recognition_basis']==before['recognition_basis'] and data['segments']==before['segments']
    if same_authority:
        assert data['geometric_unit_evidence']['source']['authoritative_source']==before['geometric_unit_evidence']['source']['authoritative_source']
        assert data['geometric_unit_evidence']['source']['exchange_dxf']['sha256']!=before['geometric_unit_evidence']['source']['exchange_dxf']['sha256']
    bad=put(case['tmp']/'foreign.json',data);rejected=case['tmp']/'foreign-result.json'
    wrong=command(PIPE/'infer_pipe_network.py','--input',bad,'--output',rejected)
    assert wrong.returncode==2 and not rejected.exists(),(wrong.stdout,wrong.stderr)
    assert ('交换DXF版本' in wrong.stderr) if same_authority else ('原图身份' in wrong.stderr)
    restored=command(PIPE/'infer_pipe_network.py','--input',valid,'--output',case['tmp']/'restored.json')
    assert restored.returncode==0,(restored.stdout,restored.stderr)
    put(case['tmp']/'实际跨源与还原对照.json',dict(same_authority=same_authority,legal_exit=good.returncode,
        wrong_source_exit=wrong.returncode,wrong_source_stderr=wrong.stderr,restored_exit=restored.returncode,
        source_before=before['geometric_unit_evidence']['source'],source_after=data['geometric_unit_evidence']['source']))


@pytest.mark.parametrize('missing',['recognition','version_evidence','artifact_only','source_object','runtime_list','runtime_item','credential_schema'])
def test_geometry_mode_cannot_drop_provenance_or_dependency_items(case,missing):
    data=input_for(case)
    if missing=='recognition':data.pop('recognition_basis')
    elif missing=='version_evidence':
        path=Path(data['recognition_basis']['ledger_path']);ledger=json.loads(path.read_text(encoding='utf-8'))
        note=case['tmp']/'only-original-hash.txt';note.write_text('原DWG来源相同，未固定交换副本版本',encoding='utf-8')
        ledger['evidence'][0].update(path=str(note),sha256=sha(note));put(path,ledger)
        data['recognition_basis']=recognition_basis(ledger,path)
    elif missing=='artifact_only':
        path=Path(data['recognition_basis']['ledger_path']);ledger=json.loads(path.read_text(encoding='utf-8'))
        ledger['evidence']=[dict(evidence_id=k,**ref) for k,ref in case['manifest']['artifacts'].items()]
        ledger['entries'][0]['evidence_ids']=list(case['manifest']['artifacts'])
        put(path,ledger);data['recognition_basis']=recognition_basis(ledger,path)
    elif missing=='source_object':data['segments'][0]['source_ids']=['UNBOUND']
    elif missing=='runtime_list':data['geometric_unit_evidence'].pop('runtime_dependencies')
    elif missing=='runtime_item':data['geometric_unit_evidence']['runtime_dependencies'].pop()
    else:data['geometric_unit_evidence']['schema_version']='old-self-declared/1.0'
    source=put(case['tmp']/'invalid.json',data)
    p=command(PIPE/'infer_pipe_network.py','--input',source,'--output',case['tmp']/'invalid-result.json')
    assert p.returncode==2,(p.stdout,p.stderr)
    assert not (case['tmp']/'invalid-result.json').exists()


def test_actual_batch_records_version_and_resolves_real_nested_source_chain(case):
    doc=case['doc'];model=doc.modelspace();model.delete_entity(doc.entitydb[case['handles']['pipe']])
    block=doc.blocks.new('ROUTES');leaf=block.add_line((10000,30000),(20000,30000),dxfattribs={'layer':'PIPE'})
    root=model.add_blockref('ROUTES',(0,0));doc.saveas(case['path'])
    neutral=case['tmp']/'fresh-neutral'
    p=command(SHARED/'extract_cad_neutral.py','--source-dxf',case['path'],'--all-blocks','--output-dir',neutral)
    assert p.returncode in (0,3),(p.stdout,p.stderr)
    case['manifest_path']=neutral/'CAD中性取证清单.json';case['manifest']=json.loads(case['manifest_path'].read_text(encoding='utf-8'))
    case['stream']=Path(case['manifest']['artifacts']['layout_entities']['path'])
    mapping,frames=batch_input(case);out=case['tmp']/'actual-batch'
    p=command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',case['manifest_path'],
        '--mapping',mapping,'--frames',frames,'--output-dir',out,'--run-inference')
    assert p.returncode==0,(p.stdout,p.stderr)
    actual=json.loads(next(out.rglob('管网推算证据.json')).read_text(encoding='utf-8'))
    binding=actual['geometric_unit_source_binding']
    assert binding['cad_manifest']['sha256']==sha(case['manifest_path'])
    assert binding['verified_source_object_refs']==['CADINST-CAD-'+root.dxf.handle+'/ROUTES/CAD-'+leaf.dxf.handle]


def t20_case(tmp_path):
    spec=importlib.util.spec_from_file_location('t20_mechanical_fixture',ROOT/'tests/test_point_host_attachment_t20_20261019.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    c=old.case(tmp_path)
    path=tmp_path/'anonymous.dxf';doc=ezdxf.readfile(path);doc.units=0;m=doc.modelspace()
    frame=m.add_lwpolyline([(0,0),(84100,0),(84100,59400),(0,59400)],close=True)
    scale=m.add_text('1:100',dxfattribs={'insert':(70000,1000),'height':200})
    title=m.add_text('首层给水平面图',dxfattribs={'insert':(40000,1000),'height':200})
    dim=m.add_linear_dim(base=(20000,22000),p1=(20000,20000),p2=(26000,20000),angle=0,text='<>',
        dimstyle='EZDXF',override={'dimlfac':1,'dimtxt':120,'dimasz':80});dim.render();doc.saveas(path)
    oldsha=c['source'];c['source']=sha(path)
    for key,filekey in (('ledger','lp'),('segments','sp')):
        c[key]=json.loads(json.dumps(c[key]).replace(oldsha,c['source']));put(c[filekey],c[key])
    c['profile']['expected_source_sha256']=c['source'];c['profile']['adjudication_ledger']['sha256']=sha(c['lp'])
    c['profile']['point_host_attachment']['segments']['sha256']=sha(c['sp'])
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet']='inline_network';put(c['pp'],c['profile'])
    p=command(SHARED/'extract_cad_neutral.py','--source-dxf',path,'--all-blocks','--output-dir',tmp_path/'neutral')
    assert p.returncode in (0,3),(p.stdout,p.stderr)
    request=dict(schema_version=units.REQUEST_SCHEMA,raw_unit='Millimeters',measurement_semantics='plan_to_scale',
        frame=dict(entity_refs=[dict(handle=frame.dxf.handle)]),scale_text=dict(handle=scale.dxf.handle),
        view_text_refs=[dict(handle=title.dxf.handle)],dimension_refs=[dict(handle=dim.dimension.dxf.handle)],basis='同原源外框/图签/尺寸')
    scope=dict(layout='Model',space_id='S',boundary_policy='contained',bounds=dict(xmin=0,ymin=0,xmax_exclusive=84100,ymax_exclusive=59400))
    mp=tmp_path/'neutral/CAD中性取证清单.json'
    req=put(tmp_path/'unit-request.json',request);sp=put(tmp_path/'unit-scope.json',scope);ep=tmp_path/'unit-evidence.json'
    p=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',mp,'--request',req,'--scope',sp,'--output',ep)
    assert p.returncode==0,(p.stdout,p.stderr)
    evidence=json.loads(ep.read_text(encoding='utf-8'))
    point=ROOT/'skills/qto-components-points/scripts';out=tmp_path/'points'
    p=command(point/'build_component_instances_from_neutral.py','--cad-manifest',mp,'--profile',c['pp'],
        '--adjudication-ledger',c['lp'],'--output-dir',out)
    assert p.returncode==0,(p.stdout,p.stderr)
    attachment=out/'点位位置与管段挂接.json';ip,data=old.pipe_input(c,attachment)
    data.update(geometric_unit_evidence=evidence,measurement_scope=scope,unit_evidence_level='inferred',
                recognition_basis=recognition_basis(c['ledger'],c['lp']))
    put(ip,data);received=tmp_path/'actual-reception'
    p=command(point/'receive_point_inline_components.py','--attachment-report',attachment,'--network-input',ip,'--output-dir',received)
    assert p.returncode==0,(p.stdout,p.stderr)
    return received/'管网在线附件数量接收.json'


def test_actual_receipt_binds_new_validator_bytes_with_replay_on_and_off_and_restoration(tmp_path):
    receipt=t20_case(tmp_path)
    frozen_receipt=json.loads(receipt.read_text(encoding='utf-8'))
    assert len(frozen_receipt['producer_dependencies'])==3
    isolated=tmp_path/'isolated-source'
    shutil.copytree(ROOT,isolated,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','*.pyc','*.pyo'))
    module=isolated/'skills/_qto-shared-evidence/scripts/qto_geometric_unit_evidence.py'
    producer=isolated/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'
    original_bytes=module.read_bytes();producer_before=sha(producer);receipt_before=sha(receipt)
    checker=tmp_path/'check.py'
    checker.write_text('''import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'skills/qto-components-points/scripts'));sys.path.insert(0,str(root/'skills/_qto-shared-evidence/scripts'))
from point_host_attachment import verify_pipe_consumption
p=Path(sys.argv[2]);receipt=json.loads(p.read_text(encoding='utf-8'))
if sys.argv[4]=='drop':receipt.pop('producer_dependencies',None)
if sys.argv[4]=='item':receipt['producer_dependencies'].pop()
if sys.argv[4]=='strip':
 for key in ('network_input','network_result'):
  value=json.loads(Path(receipt[key]['path']).read_text(encoding='utf-8'))
  for field in ('geometric_unit_evidence','geometric_unit_source_binding','unit_evidence_level','measurement_scope'):value.pop(field,None)
  target=p.parent/('stripped-'+key+'.json');target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\\n',encoding='utf-8')
  receipt[key]={'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
 receipt.pop('producer_dependencies',None)
try: verify_pipe_consumption(receipt,p.parent,replay=sys.argv[3]=='true')
except ValueError as exc: print(str(exc));sys.exit(2)
print('accepted')
''',encoding='utf-8')
    outcomes=[]
    for replay in ('true','false'):
        valid=command(checker,isolated,receipt,replay,'keep');assert valid.returncode==0,(valid.stdout,valid.stderr)
        for kind in ('drop','item'):
            missing=command(checker,isolated,receipt,replay,kind);assert missing.returncode==2
    text=original_bytes.decode('utf-8-sig').replace('def _scope(scope, box):\n','def _scope(scope, box):\n    return None  # isolated byte-identity counterexample\n',1)
    module.write_text(text,encoding='utf-8');assert sha(module)!=sha(SHARED/'qto_geometric_unit_evidence.py')
    for replay in ('true','false'):
        changed=command(checker,isolated,receipt,replay,'keep')
        assert changed.returncode==2 and '字节' in changed.stdout,(changed.stdout,changed.stderr)
        stripped=command(checker,isolated,receipt,replay,'strip')
        assert stripped.returncode==2 and '不一致' in stripped.stdout,(stripped.stdout,stripped.stderr)
        outcomes.append(dict(replay=replay,changed_exit=changed.returncode,changed_stdout=changed.stdout,
            all_geometry_markers_stripped_exit=stripped.returncode,all_geometry_markers_stripped_stdout=stripped.stdout))
    assert sha(producer)==producer_before and sha(receipt)==receipt_before
    module.write_bytes(original_bytes)
    for replay in ('true','false'):
        restored=command(checker,isolated,receipt,replay,'keep');assert restored.returncode==0,(restored.stdout,restored.stderr)
    put(tmp_path/'实际旧凭据变字节与还原对照.json',dict(receipt_sha256=receipt_before,producer_sha256=producer_before,
        actual_receipt=str(receipt),outcomes=outcomes,restored_accepted=True))


def test_legacy_explicit_header_unit_actual_private_stage_and_both_replay_values(tmp_path):
    spec=importlib.util.spec_from_file_location('t20_legacy_fixture',ROOT/'tests/test_point_host_attachment_t20_20261019.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    c=old.case(tmp_path)
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet']='inline_network';put(c['pp'],c['profile'])
    out,_=old.adapt(c)
    ip,data=old.pipe_input(c,out/'点位位置与管段挂接.json')
    assert data.get('geometric_unit_evidence') is None
    point=ROOT/'skills/qto-components-points/scripts';received=tmp_path/'legacy-reception'
    actual=command(point/'receive_point_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json',
        '--network-input',ip,'--output-dir',received)
    assert actual.returncode==0,(actual.stdout,actual.stderr)
    receipt=received/'管网在线附件数量接收.json'
    checker=tmp_path/'legacy-check.py'
    checker.write_text('''import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'skills/qto-components-points/scripts'));sys.path.insert(0,str(root/'skills/_qto-shared-evidence/scripts'))
from point_host_attachment import verify_pipe_consumption
p=Path(sys.argv[2]);r=json.loads(p.read_text(encoding='utf-8'));verify_pipe_consumption(r,p.parent,replay=sys.argv[3]=='true');print('accepted')
''',encoding='utf-8')
    outcomes=[]
    for replay in ('true','false'):
        valid=command(checker,ROOT,receipt,replay)
        assert valid.returncode==0,(valid.stdout,valid.stderr)
        outcomes.append(dict(replay=replay,exit_code=valid.returncode,stdout=valid.stdout))
    put(tmp_path/'旧显式INSUNITS4接收开关对照.json',dict(private_stage_exit=actual.returncode,actual_receipt=str(receipt),outcomes=outcomes))
