"""Synthetic source DXF -> neutral -> object binding -> reference -> existing scene/HTML.

Geometry consistency only; this does not score semantic recognition or real project quantities.
"""
import copy
import base64
import gzip
import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'skills/qto-electrical-routes/scripts'
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SCRIPTS))
import bind_electrical_objects as binding


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args):
    return subprocess.run([sys.executable,'-B',*map(str,args)],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=60)


def extract(folder,doc):
    folder.mkdir(parents=True)
    source=folder/'匿名几何.dxf';doc.saveas(source)
    done=run([SHARED/'extract_cad_neutral.py','--source-dxf',source,'--output-dir',folder/'中性事实'])
    assert done.returncode==0,done.stdout+done.stderr
    return folder/'中性事实/CAD中性取证清单.json'


def fixture(folder,unit=6,curve='line'):
    d=ezdxf.new('R2013');d.units=unit
    d.layers.add('ROUTE');d.layers.add('NOTE')
    m=d.modelspace();block=d.blocks.new('DEVICE');port=block.add_point((0,0,0)).dxf.handle
    basis='CAD-'+m.add_text('匿名设计依据：已画连接口与物理通道',dxfattribs=dict(insert=(0,3),layer='NOTE',height=.2)).dxf.handle
    scale=1000 if unit==4 else 1
    if curve=='line':
        entity=m.add_line((0,0,0),(10*scale,0,0),dxfattribs=dict(layer='ROUTE'));a=(0,0,0);b=(10*scale,0,0)
    elif curve=='arc':
        entity=m.add_arc((0,0),10*scale,0,180,dxfattribs=dict(layer='ROUTE'));a=(10*scale,0,0);b=(-10*scale,0,0)
    elif curve=='ellipse':
        entity=m.add_ellipse((0,0),(10*scale,0),.5,0,math.pi,dxfattribs=dict(layer='ROUTE'));a=(10*scale,0,0);b=(-10*scale,0,0)
    elif curve=='circle':
        entity=m.add_circle((0,0),10*scale,dxfattribs=dict(layer='ROUTE'));a=b=(10*scale,0,0)
    ah=m.add_blockref('DEVICE',a).dxf.handle;bh=m.add_blockref('DEVICE',b).dxf.handle
    manifest=extract(folder,d);mf=json.loads(manifest.read_text(encoding='utf-8'))
    sid='CAD-'+entity.dxf.handle
    sh=dict(sheet_id='S1',floor='F1',bounds=[-20*scale,-20*scale,20*scale,20*scale])
    def endpoint(key,h):
        return dict(id=key,kind='connection_port',manifest_sha256=digest(manifest),physical_source_id='CAD-'+h,
                    physical_id='CAD-'+h,port_source_id='CAD-'+h+'/DEVICE/CAD-'+port,instance_path='CAD-'+h,
                    locator='point',basis='原实例内部连接口POINT',basis_source_ids=[basis])
    def step(lo=0.,hi=1.,direction=1):
        return dict(source_id=sid,part_index=0,instance_path=sid,fraction_range=[lo,hi],direction=direction,
                    sheet_id='S1',floor='F1',role='horizontal',basis_source_ids=[basis])
    def obj(key,kind='cable',start='A',end='B',steps=None):
        return dict(id=key,physical_id='物理-'+key,kind=kind,system='动力',spec='匿名规格',parallel_count=1,
                    start_endpoint=start,end_endpoint=end,steps=steps or [step()],basis_source_ids=[basis],
                    component_evidence={c:dict(status='not_applicable',basis='匿名样本明确不包含此分量',source_ids=[basis])for c in binding.COMPONENTS})
    request=dict(schema=binding.SCHEMA,project_id='匿名电气',drawing_id='DRAW',review_id='REVIEW',title='匿名原图对象复核',
                 cad_manifest_sha256=digest(manifest),source_sha256=mf['authoritative_source']['sha256'],
                 layout='Model',unit=dict(factor_to_m=1/scale,basis='匿名DXF单位'),expand_blocks=['DEVICE'],
                 measurement_mode='plan',measurement_basis_source_ids=[basis],connection_tolerance_m=.000001,
                 route_layers='^ROUTE$',sheets=[sh],endpoints=[endpoint('A',ah),endpoint('B',bh)],objects=[obj('C1')])
    return manifest,request,obj,step,d,entity,basis


def calculate(manifest,request):
    return binding.calculate(binding.Evidence(manifest,request),request)


@pytest.mark.parametrize('unit',[4,6])
@pytest.mark.parametrize('curve',['line','arc'])
def test_source_package_reference_scene_and_html_end_to_end(tmp_path,unit,curve):
    manifest,req,obj,step,*_=fixture(tmp_path/'源',unit=unit,curve=curve)
    req['objects']=[obj('TRAY','tray'),dict(obj('C1'),channel_ids=['TRAY']),dict(obj('C2'),channel_ids=['TRAY'],parallel_count=2,conductor_instance_ids=['C2-1','C2-2'])]
    input_path=tmp_path/'对象请求.json';input_path.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8')
    output=tmp_path/'端到端结果'
    done=run([SCRIPTS/'bind_electrical_objects.py','--cad-manifest',manifest,'--request',input_path,'--output-dir',output,'--build-review'])
    assert done.returncode==0,done.stdout+done.stderr
    package=json.loads((output/'电气对象包.json').read_text(encoding='utf-8'))
    assert package['coverage']['closed'] and package['checks']['split_length_conserved']
    expected=10 if curve=='line'else 10*math.pi
    assert [o['complete_length_m']for o in package['objects']]==pytest.approx([expected,expected,2*expected])
    assert len(package['ledger']['paths'])==1
    assert all(r['exit_code']==0 for r in json.loads((output/'调用回执.json').read_text(encoding='utf-8')))
    view=json.loads((output/'复核视图.json').read_text(encoding='utf-8'))
    assert {o['catalog_key']for o in view['objects']}=={o['catalog_key']for o in package['objects']}
    for o in view['objects']:
        scale=1000 if unit==4 else 1
        assert o['points'][0]==pytest.approx([0,0]if curve=='line'else[10*scale,0])
        assert o['points'][-1]==pytest.approx([10*scale,0]if curve=='line'else[-10*scale,0],abs=.000001)
    assert view['scenes'][0]['coordinate_unit']==('mm'if unit==4 else'm')
    assert view['scenes'][0]['restoration']
    assert view.get('background_sets') or view['scenes'][0].get('background')
    html=(output/'电气对象复核.html').read_text(encoding='utf-8')
    assert '__PAYLOAD__'not in html and '原图对象复核'in html
    assert len(html.encode('utf-8'))<2_000_000 and max(map(len,html.splitlines()))<10000


@pytest.mark.parametrize('curve',['arc','ellipse'])
def test_partial_curve_reverse_and_arclength_conservation(tmp_path,curve):
    manifest,req,obj,step,*_=fixture(tmp_path/'源',curve=curve)
    # Start/end are actual instance ports; reverse the original curve, no text-length arithmetic.
    req['objects']=[obj('TRAY','tray',start='B',end='A',steps=[step(.5,1,-1),step(0,.5,-1)])]
    result=calculate(manifest,req)
    assert result['objects'][0]['errors']==[] and len(result['ledger']['paths'])==2
    original=binding.graph.row_parts(binding.Evidence(manifest,req).byid[step()['source_id']])[0][0]
    expected=binding.geometry.measure_part(original)[0]
    assert result['objects'][0]['complete_length_m']==pytest.approx(expected)
    assert result['objects'][0]['complete_length_m']>20
    assert result['objects'][0]['source_steps'][0]['direction']==-1


def test_common_minimal_intervals_partial_sharing_and_unprocessed_difference(tmp_path):
    manifest,req,obj,step,*_=fixture(tmp_path/'源')
    sid=step()['source_id'];basis=req['measurement_basis_source_ids']
    def end(key,f):return dict(id=key,kind='curve_node',source_id=sid,instance_path=sid,part_index=0,fraction=f,basis_source_ids=basis)
    req['endpoints'] += [end('MID',.5)]
    req['objects']=[obj('LEFT','tray',end='MID',steps=[step(0,.5)]),obj('RIGHT','tray',start='MID',steps=[step(.5,1)]),dict(obj('C1'),channel_ids=['LEFT','RIGHT'])]
    result=calculate(manifest,req)
    assert len(result['ledger']['paths'])==2
    assert [o['complete_length_m']for o in result['objects']]==[5,5,10]
    assert result['coverage']['closed']
    req['objects']=req['objects'][:1]
    result=calculate(manifest,req)
    assert result['coverage']['unprocessed'][0]['fraction_ranges']==[[.5,1]]
    assert result['status']=='REVIEW_REQUIRED'
    req['exclusions']=[dict(step(.5,1),reason='匿名旧线作废')]
    result=calculate(manifest,req)
    assert result['coverage']['closed']


@pytest.mark.parametrize('mutation,match',[
    ('gap','几何不连续'),('wrong_end','终端'),('missing_geometry','回取'),('wrong_instance','instance_path'),
    ('reverse_range','fraction_range'),('bad_direction','direction'),('duplicate_interval','重复认领'),('wrong_floor','楼层'),
    ('missing_spec','spec'),('wrong_route_layer','route_layers'),('false_port','连接口'),
])
def test_invalid_claims_remain_null_and_visible(tmp_path,mutation,match):
    manifest,req,obj,step,*_=fixture(tmp_path/'源')
    current=req['objects'][0]
    if mutation=='gap':current['steps']=[step(0,.4),step(.6,1)]
    if mutation=='wrong_end':current['steps']=[step(0,.5)]
    if mutation=='missing_geometry':current['steps'][0]['part_index']=4
    if mutation=='wrong_instance':current['steps'][0]['instance_path']='CAD-OTHER/DEVICE/CAD-1'
    if mutation=='reverse_range':current['steps'][0]['fraction_range']=[1,0]
    if mutation=='bad_direction':current['steps'][0]['direction']=True
    if mutation=='duplicate_interval':current['steps']=[step(),step(0,1,-1),step()]
    if mutation=='wrong_floor':current['steps'][0]['floor']='F2'
    if mutation=='missing_spec':current['spec']=None
    if mutation=='wrong_route_layer':req['route_layers']='^OTHER$'
    if mutation=='false_port':req['endpoints'][0]['locator']='insert_anchor'
    result=calculate(manifest,req);out=result['objects'][0]
    assert out['status']=='UNRESOLVED' and out['complete_length_m']is None and out['known_path_length_each_m']is None
    assert match in ''.join(out['errors'])
    assert result['quantity_result']['items']==[] and result['todo']


@pytest.mark.parametrize('mutation', ['source_revision','manifest_revision','unit'])
def test_wrong_source_version_and_unit_are_rejected(tmp_path,mutation):
    manifest,req,*_=fixture(tmp_path/'源')
    if mutation=='source_revision':req['source_sha256']='0'*64
    if mutation=='manifest_revision':req['cad_manifest_sha256']='0'*64
    if mutation=='unit':req['unit']['factor_to_m']=.001
    with pytest.raises(ValueError):calculate(manifest,req)


def test_mm_conversion_missing_components_and_declaration_length_cannot_override_source(tmp_path):
    manifest,req,*_=fixture(tmp_path/'源',unit=4)
    req['objects'][0]['path_length_m']=999999
    req['objects'][0].pop('component_evidence')
    result=calculate(manifest,req);obj=result['objects'][0]
    assert obj['known_length_m']==10 and obj['complete_length_m']is None
    assert obj['pending_components']==sorted(binding.COMPONENTS)
    assert result['quantity_result']['items'][0]['pending_components']


@pytest.mark.parametrize('kind',['physical_id','channel_overlap','unapproved_share','parallel_channel','conductor_identity'])
def test_duplicate_identity_and_illegal_sharing_rejected(tmp_path,kind):
    manifest,req,obj,*_=fixture(tmp_path/'源')
    if kind=='physical_id':req['objects'] += [dict(req['objects'][0],id='C2')]
    if kind=='channel_overlap':req['objects']=[obj('TR1','tray'),obj('TR2','tray')]
    if kind=='unapproved_share':req['objects'] += [obj('C2')]
    if kind=='parallel_channel':req['objects']=[dict(obj('TR1','tray'),parallel_count=2)]
    if kind=='conductor_identity':req['objects'][0]['parallel_count']=2
    if kind in ('parallel_channel','conductor_identity'):
        result=calculate(manifest,req)
        assert result['objects'][0]['complete_length_m']is None and result['objects'][0]['errors']
    else:
        with pytest.raises(ValueError):calculate(manifest,req)


def test_closed_ring_is_split_not_replaced_with_zero_chord(tmp_path):
    manifest,req,obj,*_=fixture(tmp_path/'源',curve='circle')
    req['objects']=[obj('RING','tray')]
    result=calculate(manifest,req)
    assert len(result['ledger']['paths'])==2
    assert result['objects'][0]['complete_length_m']==pytest.approx(20*math.pi)


@pytest.mark.parametrize('spacing',[0,20])
def test_same_inner_handle_different_insert_instances_do_not_collide(tmp_path,spacing):
    d=ezdxf.new('R2013');d.units=6;d.layers.add('ROUTE');d.layers.add('NOTE')
    b=d.blocks.new('CONTAINER');line=b.add_line((0,0),(10,0),dxfattribs=dict(layer='ROUTE'))
    first=d.modelspace().add_blockref('CONTAINER',(0,0));second=d.modelspace().add_blockref('CONTAINER',(0,spacing))
    note=d.modelspace().add_text('匿名依据',dxfattribs=dict(insert=(0,40),layer='NOTE'))
    manifest=extract(tmp_path/'源',d);mf=json.loads(manifest.read_text(encoding='utf-8'));basis=['CAD-'+note.dxf.handle]
    req=dict(schema=binding.SCHEMA,project_id='匿名实例',cad_manifest_sha256=digest(manifest),source_sha256=mf['authoritative_source']['sha256'],
             layout='Model',unit=dict(factor_to_m=1,basis='m'),expand_blocks=['CONTAINER'],measurement_mode='plan',measurement_basis_source_ids=basis,
             connection_tolerance_m=.000001,route_layers='^ROUTE$',sheets=[dict(sheet_id='S1',floor='F1',bounds=[-1,-1,20,30])],endpoints=[],objects=[])
    for n,insert in enumerate((first,second)):
        sid='CAD-'+insert.dxf.handle+'/CONTAINER/CAD-'+line.dxf.handle
        for end,f in [('A',0),('B',1)]:req['endpoints'].append(dict(id=end+str(n),kind='curve_node',source_id=sid,instance_path=sid,part_index=0,fraction=f,basis_source_ids=basis))
        req['objects'].append(dict(id='TR'+str(n),physical_id='TR'+str(n),kind='tray',system='动力',spec='TR',parallel_count=1,start_endpoint='A'+str(n),end_endpoint='B'+str(n),
             basis_source_ids=basis,steps=[dict(source_id=sid,instance_path=sid,part_index=0,fraction_range=[0,1],direction=1,sheet_id='S1',floor='F1',role='horizontal',basis_source_ids=basis)]))
    result=calculate(manifest,req)
    assert [o['known_length_m']for o in result['objects']]==[10,10]
    assert len(result['ledger']['paths'])==2
    assert result['objects'][0]['source_steps'][0]['source_id']!=result['objects'][1]['source_steps'][0]['source_id']
    if spacing==0:
        candidate=result['overlap_review']['candidates'][0]
        assert candidate['status']=='pending' and candidate['overlap_m']==10
        req['overlap_resolutions']=[dict(segment_ids=candidate['segment_ids'],action='keep_both',basis='匿名两根独立桥架，已核实物',basis_source_ids=basis)]
        kept=calculate(manifest,req)
        assert kept['overlap_review']['candidates'][0]['status']=='kept_both'
        assert [o['known_length_m']for o in kept['objects']]==[10,10]


def test_real_trace_import_recomputes_source_and_rejects_network_and_gaps(tmp_path):
    manifest,req,obj,step,*_=fixture(tmp_path/'源')
    tr=dict(schema='qto-drawn-route-request/1.0',drawing_id='DRAW',unit=dict(factor_to_output=1,source_id=req['measurement_basis_source_ids'][0]),
            route_layers='^ROUTE$',endpoint_tolerance=.000001,snap_tolerance=.000001,gap_tolerance=0,
            routes=[dict(route_id='R1',**{'from':dict(point=[0,0]),'to':dict(point=[10,0])})])
    rp=tmp_path/'追踪请求.json';rp.write_text(json.dumps(tr),encoding='utf-8')
    td=tmp_path/'追踪结果'
    done=run([SCRIPTS/'trace_drawn_routes.py','--cad-manifest',manifest,'--request',rp,'--output-dir',td])
    assert done.returncode==0,done.stdout+done.stderr
    trace_path=td/'走线追踪结果.json'
    req['trace_result']=dict(path=str(trace_path),sha256=digest(trace_path))
    req['objects'][0].pop('steps');req['objects'][0].update(trace_route_id='R1',trace_context=dict(sheet_id='S1',floor='F1',basis_source_ids=req['measurement_basis_source_ids']))
    trace=json.loads(trace_path.read_text(encoding='utf-8'))
    # These are declarations only. Recomputed source curve remains authoritative.
    trace['routes'][0]['segments'][0]['length_m']=123456
    trace_path.write_text(json.dumps(trace),encoding='utf-8');req['trace_result']['sha256']=digest(trace_path)
    result=calculate(manifest,req)
    assert result['objects'][0]['complete_length_m']==10
    trace['schema']='qto-circuit-on-tray-result/1.0'
    trace_path.write_text(json.dumps(trace),encoding='utf-8');req['trace_result']['sha256']=digest(trace_path)
    with pytest.raises(ValueError,match='network_model'):calculate(manifest,req)
    trace['schema']=binding.routes.RESULT_SCHEMA
    trace['routes'][0]['bridged_gaps']=[dict(length_m=1)]
    trace_path.write_text(json.dumps(trace),encoding='utf-8');req['trace_result']['sha256']=digest(trace_path)
    result=calculate(manifest,req)
    assert result['objects'][0]['complete_length_m']is None


def test_nested_nonuniform_block_transform_keeps_source_arc_and_background(tmp_path):
    d=ezdxf.new('R2013');d.units=4;d.layers.add('ROUTE');d.layers.add('NOTE')
    inner=d.blocks.new('INNER');arc=inner.add_arc((0,0),1000,0,180,dxfattribs=dict(layer='ROUTE'))
    outer=d.blocks.new('OUTER');nested=outer.add_blockref('INNER',(2000,1000),dxfattribs=dict(xscale=2,yscale=1))
    top=d.modelspace().add_blockref('OUTER',(10000,20000),dxfattribs=dict(rotation=90))
    note=d.modelspace().add_text('匿名嵌套块原图依据',dxfattribs=dict(insert=(10000,25000),height=100,layer='NOTE'))
    manifest=extract(tmp_path/'源',d);mf=json.loads(manifest.read_text(encoding='utf-8'));basis=['CAD-'+note.dxf.handle]
    sid='CAD-'+top.dxf.handle+'/OUTER/CAD-'+nested.dxf.handle+'/INNER/CAD-'+arc.dxf.handle
    endpoints=[dict(id=label,kind='curve_node',source_id=sid,instance_path=sid,part_index=0,fraction=f,basis_source_ids=basis)for label,f in [('A',0),('B',1)]]
    req=dict(schema=binding.SCHEMA,project_id='匿名嵌套',drawing_id='NESTED',review_id='NESTED',title='匿名嵌套块复核',
        cad_manifest_sha256=digest(manifest),source_sha256=mf['authoritative_source']['sha256'],layout='Model',
        unit=dict(factor_to_m=.001,basis='mm'),expand_blocks=['OUTER','INNER'],measurement_mode='plan',measurement_basis_source_ids=basis,
        connection_tolerance_m=.000001,route_layers='^ROUTE$',sheets=[dict(sheet_id='S1',floor='F1',bounds=[5000,15000,15000,30000])],endpoints=endpoints,
        objects=[dict(id='TR',physical_id='嵌套桥架',kind='tray',system='动力',spec='匿名桥架',parallel_count=1,start_endpoint='A',end_endpoint='B',basis_source_ids=basis,
            steps=[dict(source_id=sid,instance_path=sid,part_index=0,fraction_range=[0,1],direction=1,sheet_id='S1',floor='F1',role='horizontal',basis_source_ids=basis)],
            component_evidence={c:dict(status='not_applicable',basis='匿名明确范围',source_ids=basis)for c in binding.COMPONENTS})])
    rp=tmp_path/'嵌套请求.json';rp.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8');out=tmp_path/'嵌套结果'
    done=run([SCRIPTS/'bind_electrical_objects.py','--cad-manifest',manifest,'--request',rp,'--output-dir',out,'--build-review'])
    assert done.returncode==0,done.stdout+done.stderr
    package=json.loads((out/'电气对象包.json').read_text(encoding='utf-8'))
    expected=binding.geometry.measure_part(dict(kind='ellipse_arc',center=[0,0,0],u=[2,0,0],v=[0,1,0],start_param=0,sweep_param=math.pi))[0]
    assert package['objects'][0]['complete_length_m']==pytest.approx(expected)
    view=json.loads((out/'复核视图.json').read_text(encoding='utf-8'))
    assert view['objects'][0]['points'][0]==pytest.approx([9000,24000])
    assert view['objects'][0]['points'][-1]==pytest.approx([9000,20000])
    assert view['scenes'][0]['coordinate_unit']=='mm' and view['background_sets']


def test_all_unresolved_objects_are_preserved_in_existing_html(tmp_path):
    manifest,req,*_=fixture(tmp_path/'源');req['objects'][0]['spec']=None
    path=tmp_path/'请求.json';path.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8');out=tmp_path/'未决回图'
    done=run([SCRIPTS/'bind_electrical_objects.py','--cad-manifest',manifest,'--request',path,'--output-dir',out,'--build-review'])
    assert done.returncode==3,done.stdout+done.stderr
    package=json.loads((out/'电气对象包.json').read_text(encoding='utf-8'))
    view=json.loads((out/'复核视图.json').read_text(encoding='utf-8'))
    assert package['objects'][0]['complete_length_m']is None
    assert view['objects'][0]['kind']=='unlocated' and view['objects'][0]['catalog_key']==package['objects'][0]['catalog_key']


def test_crossing_lines_are_not_used_as_implicit_connection(tmp_path):
    manifest,req,obj,step,d,entity,basis=fixture(tmp_path/'初始')
    cross=d.modelspace().add_line((5,-5),(5,5),dxfattribs=dict(layer='ROUTE'))
    manifest=extract(tmp_path/'交叉',d);mf=json.loads(manifest.read_text(encoding='utf-8'))
    req['cad_manifest_sha256']=digest(manifest);req['source_sha256']=mf['authoritative_source']['sha256']
    for end in req['endpoints']:end['manifest_sha256']=digest(manifest)
    other=dict(step(),source_id='CAD-'+cross.dxf.handle,instance_path='CAD-'+cross.dxf.handle)
    req['objects'][0]['steps']=[step(),other]
    result=calculate(manifest,req)
    assert result['objects'][0]['status']=='UNRESOLVED'
    assert '几何不连续'in ''.join(result['objects'][0]['errors'])


def test_source_replacement_and_mutation_during_calculation_invalidates_package(tmp_path):
    manifest,req,*_=fixture(tmp_path/'源')
    e=binding.Evidence(manifest,req)
    path=Path(e.manifest['authoritative_source']['path'])
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError):binding.calculate(e,req)
    with pytest.raises(ValueError):binding.Evidence(manifest,req)


def split_at_nearly_equal_boundaries(req,obj,step,left=.3,right=math.nextafter(.3,1),reverse=False,shared=True):
    sid=step()['source_id'];basis=req['measurement_basis_source_ids']
    req['endpoints'] += [dict(id=name,kind='curve_node',source_id=sid,instance_path=sid,
        part_index=0,fraction=f,basis_source_ids=basis)for name,f in [('ZERO',0),('LEFTCUT',left),('RIGHTCUT',right),('ONE',1)]]
    direction=-1 if reverse else 1
    first=obj('LEFT','tray',start='LEFTCUT'if reverse else'ZERO',end='ZERO'if reverse else'LEFTCUT',steps=[step(0,left,direction)])
    second=obj('RIGHT','tray',start='ONE'if reverse else'RIGHTCUT',end='RIGHTCUT'if reverse else'ONE',steps=[step(right,1,direction)])
    req['objects']=[first,second]
    if shared:req['objects'] += [dict(obj('SHARED'),channel_ids=['LEFT','RIGHT'])]
    return req


@pytest.mark.parametrize('curve',['line','arc','ellipse'])
@pytest.mark.parametrize('reverse',[False,True])
def test_ulp_boundary_normalizes_only_numeric_representation_and_conserves_curve(tmp_path,curve,reverse):
    manifest,req,obj,step,*_=fixture(tmp_path/'源',curve=curve)
    req=split_at_nearly_equal_boundaries(req,obj,step,reverse=reverse)
    result=calculate(manifest,req)
    assert result['status']=='BOUND_REFERENCE' and result['coverage']['closed']
    assert len(result['ledger']['paths'])==2
    original=binding.graph.row_parts(binding.Evidence(manifest,req).byid[step()['source_id']])[0][0]
    expected=binding.geometry.measure_part(binding.slice_part(original,0,.3))[0]
    items={o['id']:o for o in result['objects']}
    assert items['LEFT']['complete_length_m']==pytest.approx(expected)
    assert items['LEFT']['complete_length_m']+items['RIGHT']['complete_length_m']==pytest.approx(items['SHARED']['complete_length_m'])
    if curve=='line':assert [items[k]['complete_length_m']for k in ('LEFT','RIGHT','SHARED')]==pytest.approx([3,7,10])
    assert items['RIGHT']['source_steps'][0]['fraction_range'][0]==math.nextafter(.3,1)
    assert items['RIGHT']['source_steps'][0]['normalized_fraction_range'][0]==.3
    normalization=result['interval_normalization']
    assert normalization['engineering_connection_tolerance_used']is False
    changes=[x for group in normalization['changes']for x in group['boundaries']]
    assert changes and all(x['physical_boundary_shift_m']<=1e-9 for x in changes)


@pytest.mark.parametrize('right',[.30001,.3+1e-13])
def test_real_gap_is_not_swallowed_by_connection_tolerance_or_fixed_fraction_epsilon(tmp_path,right):
    manifest,req,obj,step,*_=fixture(tmp_path/'源')
    req=split_at_nearly_equal_boundaries(req,obj,step,right=right,shared=False)
    result=calculate(manifest,req)
    assert result['status']=='REVIEW_REQUIRED' and not result['coverage']['closed']
    assert result['coverage']['unprocessed'][0]['fraction_ranges']==[[.3,right]]
    assert result['interval_normalization']['changes']==[]
    assert len(result['ledger']['paths'])==2
    assert sum(o['known_length_m']for o in result['objects'])==pytest.approx(10-10*(right-.3),abs=1e-14)


def test_large_scale_one_ulp_gap_is_preserved_when_physically_distinguishable(tmp_path):
    manifest,req,obj,step,d,line,*_=fixture(tmp_path/'初始')
    line.dxf.end=(1e16,0,0)
    manifest=extract(tmp_path/'大尺度源',d)
    req['cad_manifest_sha256']=digest(manifest);req['source_sha256']=json.loads(manifest.read_text(encoding='utf-8'))['authoritative_source']['sha256']
    req['sheets'][0]['bounds']=[-20,-20,1e16+20,20]
    req=split_at_nearly_equal_boundaries(req,obj,step,shared=False)
    # Only channel nodes are used here; stale device-port declarations are intentionally not consumed.
    req['endpoints']=[p for p in req['endpoints']if p['kind']=='curve_node']
    result=calculate(manifest,req)
    assert result['interval_normalization']['changes']==[]
    assert result['coverage']['unprocessed'][0]['fraction_ranges']==[[.3,math.nextafter(.3,1)]]
    part=binding.graph.row_parts(binding.Evidence(manifest,req).byid[step()['source_id']])[0][0]
    assert binding.interval_length(part,.3,math.nextafter(.3,1),1)>.5


def test_numeric_normalization_cannot_erase_declared_positive_short_interval_or_chain_unboundedly():
    part=dict(kind='line',start=[0,0,0],end=[10,0,0]);a=.3;b=math.nextafter(a,1)
    aliases,changes=binding.normalize_cuts(part,[a,b],[[a,b]],1)
    assert aliases[a]==a and aliases[b]==b and changes==[]
    values=[a]
    for i in range(10):values.append(math.nextafter(values[-1],1))
    aliases,changes=binding.normalize_cuts(part,values,[],1)
    assert len(set(aliases.values()))>1
    assert all(abs(k-v)<=2*max(math.ulp(k),math.ulp(v))for k,v in aliases.items())


def test_plan_projection_cannot_hide_physically_distinguishable_source_boundary_shift(tmp_path):
    manifest,req,obj,step,d,line,*_=fixture(tmp_path/'初始')
    line.dxf.end=(10,0,1e16)
    manifest=extract(tmp_path/'大竖差源',d)
    req['cad_manifest_sha256']=digest(manifest);req['source_sha256']=json.loads(manifest.read_text(encoding='utf-8'))['authoritative_source']['sha256']
    req=split_at_nearly_equal_boundaries(req,obj,step,shared=False)
    req['endpoints']=[p for p in req['endpoints']if p['kind']=='curve_node']
    result=calculate(manifest,req)
    assert result['measurement_mode']=='plan' and result['interval_normalization']['changes']==[]
    assert result['coverage']['unprocessed'][0]['fraction_ranges']==[[.3,math.nextafter(.3,1)]]


@pytest.mark.parametrize('has_item',[False,True])
def test_quantity_projection_preserves_unknowns_and_never_promotes_official_quantity(has_item):
    item=dict(id='C1',known_length_m=10,length_m=None,complete_quantity=False,quantity_level=None,
              quantity_basis=None,extra_each_m=None,parallel_count=1,pending_components=['reserve'],
              length_by_basis_each_m={'actual_drawn_path':10})if has_item else None
    projection=binding.quantity_projection({},item,dict(extras=[]))
    metadata=projection['metadata'];quantities={q['name']:q for q in projection['quantities']}
    assert metadata['quantity_level']is None and metadata['quantity_basis']is None
    assert metadata['complete_length_m']is None and metadata['extra_each_m']is None
    assert metadata['official_length_m']is None and metadata['official_adoption']is False
    assert quantities['完整参考长度（已含根数）']['value']is None
    assert quantities['每根附加长度']['value']is None
    assert quantities['正式采用长度']['value']is None
    assert quantities['已知参考长度（已含根数）']['value']==(10 if has_item else None)
    assert quantities['已知参考长度（已含根数）']['level']=='数量层级未决'


@pytest.mark.parametrize('count',[1,2])
def test_scene_and_html_receive_engine_quantity_layers_components_and_sources_once(tmp_path,monkeypatch,count):
    """Projection test: inject current calculator-result fields; only reference CLI is stubbed.

    Actual neutral source, geometry scene generation, view normalization and HTML generation run.
    This is explicitly not T36/04 arithmetic or classification acceptance.
    """
    manifest,req,obj,step,*_=fixture(tmp_path/'源')
    current=req['objects'][0]
    current['steps']=[step(0,.4),step(.4,1)]
    current['parallel_count']=count
    if count==2:current['conductor_instance_ids']=['电缆实例1','电缆实例2']
    basis=req['measurement_basis_source_ids']
    current['extras']=[dict(id='RESERVE',component='reserve',endpoint_id='B',length_each_m=1.5,
        length_basis='assumption',basis_ids=basis,source_ids=basis,basis='匿名方案假定：每根预留1.5m，尚未正式采用')]
    current['component_evidence']['reserve']=dict(status='provided',basis='匿名方案参数',source_ids=basis)
    evidence=binding.Evidence(manifest,req);package=binding.calculate(evidence,req)
    item=package['quantity_result']['items'][0]
    # Authoritative result fixture matches the fields emitted by integrated T36/04.
    # The adapter must not use the stale wrapper's old-engine quantity_level instead.
    item.update(known_length_m=item['length_m'],quantity_level='方案暂估量',quantity_basis='assumption',
                basis_classification_complete=True,extra_by_basis_each_m={'assumption':1.5},
                length_basis_missing_extra_ids=[],unresolved_path_ids=[])
    package['objects'][0]['quantity_level']='规则推算量'  # Explicit stale-wrapper fixture, independent of calculator version.
    out=tmp_path/'分层导出';out.mkdir()
    binding.write_json(out/'路径台账.json',package['ledger'])
    original_run=subprocess.run;stub_calls=[];geometry_fingerprints=[]
    def run_with_result_fixture(command,*args,**kwargs):
        if Path(command[2]).name=='calculate_electrical_routes_reference.py':
            stub_calls.append(list(command))
            return subprocess.CompletedProcess(command,0,json.dumps(package['quantity_result'],ensure_ascii=False),'')
        kwargs.setdefault('stdin',subprocess.DEVNULL)
        done=original_run(command,*args,**kwargs)
        if Path(command[2]).name=='build_review_scenes_from_tables.py'and done.returncode==0:
            raw_view=json.loads((out/'复核视图.json').read_text(encoding='utf-8'))
            canonical=binding.normalize_view(raw_view,out)
            geometry_fingerprints.append(canonical['result_fingerprint'])
            binding.write_json(out/'原几何规范指纹对照.json',dict(raw_declared_fingerprint=raw_view.get('result_fingerprint'),
                normalized_geometry_fingerprint=canonical['result_fingerprint']))
        return done
    monkeypatch.setattr(binding.subprocess,'run',run_with_result_fixture)
    binding.export_review(package,req,evidence,out)
    binding.write_json(out/'投影测试来源.json',dict(reference_calculation='injected quantity_result fixture; not an actual calculator CLI run',
        scene_and_html='actual existing shared generators',stubbed_commands=stub_calls,count=count,
        input_fixture='10m原图路径；每根1.5m假定预留；level/basis fields supplied as current calculator contract'))
    assert len(stub_calls)==1
    import csv
    with (out/'对象叠加.csv').open(encoding='utf-8-sig',newline='')as stream:rows=list(csv.DictReader(stream))
    assert len(rows)==2  # two source geometry rows, never two copies of the total quantity
    assert all(r['quantity_level']=='方案暂估量'and r['quantity_basis']=='assumption'for r in rows)
    assert all(float(r['extra_each_m'])==1.5 and float(r['known_length_m'])==item['length_m']for r in rows)
    assert '匿名方案假定'in rows[0]['extra_components']and 'assumption'in rows[0]['extra_components']
    view=json.loads((out/'复核视图.json').read_text(encoding='utf-8'));assert len(view['objects'])==1
    displayed=view['objects'][0];attrs=displayed['attributes'];quantities={q['name']:q for q in displayed['quantities']}
    assert attrs['quantity_level']=='方案暂估量'and attrs['quantity_basis']=='assumption'
    assert json.loads(attrs['extra_by_basis_each_m'])=={'assumption':1.5}
    assert quantities['已知参考长度（已含根数）']['value']==11.5*count
    assert quantities['完整参考长度（已含根数）']['value']==11.5*count
    assert quantities['每根实画路径长度']['value']==10 and quantities['每根附加长度']['value']==1.5
    assert quantities['已知参考长度（已含根数）']['level']=='方案暂估量'
    assert quantities['正式采用长度']['value']is None and attrs['official_adoption']=='false'
    html=(out/'电气对象复核.html').read_text(encoding='utf-8')
    from qto_review_contract import unpack_payload
    embedded=unpack_payload(gzip.decompress(base64.b64decode(re.search(r'<script id="payload"[^>]*>(.*?)</script>',html,re.S)[1])))
    assert embedded['objects'][0]['quantities']==displayed['quantities']
    assert embedded['objects'][0]['attributes']['quantity_basis']=='assumption'
    projection_report=json.loads((out/'同源数量投影检查.json').read_text(encoding='utf-8'))
    assert projection_report['quantity_changes']==0 and not projection_report['parallel_count_multiplied']
    scene_report=json.loads((out/'场景检查.json').read_text(encoding='utf-8'))
    assert len(geometry_fingerprints)==1 and re.fullmatch('[0-9a-f]{64}',geometry_fingerprints[0])
    assert projection_report['geometry_result_fingerprint']==geometry_fingerprints[0]
    assert scene_report['geometry_result_fingerprint']==geometry_fingerprints[0]
    assert projection_report['result_fingerprint']==view['result_fingerprint']
    assert projection_report['geometry_result_fingerprint']!=projection_report['result_fingerprint']
    assert scene_report['result_fingerprint']==view['result_fingerprint']
