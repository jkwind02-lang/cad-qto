"""Anonymous real-DXF controls; quantities are working geometric scope only."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SHARED))
import qto_penetrations as pen
import qto_penetration_sources as sources


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return path


def ref(path):
    return dict(path=str(path),sha256=sha(path))


def run(script,*args):
    return subprocess.run([sys.executable,'-B',str(script),*map(str,args)],stdin=subprocess.DEVNULL,
                          env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf-8',timeout=90)


def fixture(folder,shape='line',unit=6,route_z=1.,wall_shape='rectangle'):
    folder.mkdir(parents=True)
    d=ezdxf.new('R2013');d.units=unit;m=d.modelspace();scale=1000 if unit==4 else 1
    for layer in ('PIPE','DUCT','TRAY','WALL','NOTE'):
        d.layers.add(layer)
    if shape=='line':
        entity=m.add_line((-2*scale,0,route_z*scale),(2*scale,0,route_z*scale),dxfattribs=dict(layer='PIPE'))
    elif shape=='arc':
        entity=m.add_arc((0,0,route_z*scale),2*scale,0,180,dxfattribs=dict(layer='PIPE'))
    elif shape=='ellipse':
        entity=m.add_ellipse((0,0,route_z*scale),(2*scale,0,0),.5,0,math.pi,dxfattribs=dict(layer='PIPE'))
    elif shape=='twice':
        entity=m.add_lwpolyline([(-2*scale,0), (2*scale,0),(2*scale,.2*scale),(-2*scale,.2*scale)],dxfattribs=dict(layer='PIPE',elevation=route_z*scale))
    elif shape=='tangent':
        entity=m.add_line((-2*scale,scale,route_z*scale),(2*scale,scale,route_z*scale),dxfattribs=dict(layer='PIPE'))
    elif shape=='vertical':
        entity=m.add_line((0,0,-scale),(0,0,4*scale),dxfattribs=dict(layer='PIPE'))
    elif shape=='circle':
        entity=m.add_circle((0,0,route_z*scale),2*scale,dxfattribs=dict(layer='PIPE'))
    elif shape=='spline':
        entity=m.add_spline([(-2,0,1),(-1,1,1),(1,-1,1),(2,0,1)],dxfattribs=dict(layer='PIPE'))
    if wall_shape=='rectangle':
        wall=m.add_lwpolyline([(-.1*scale,-scale),(.1*scale,-scale),(.1*scale,scale),(-.1*scale,scale)],close=True,dxfattribs=dict(layer='WALL'))
    else:
        wall=m.add_circle((0,0),scale,dxfattribs=dict(layer='WALL'))
    note=m.add_text('ANON DATUM WCS source Z and F1',dxfattribs=dict(insert=(0,5),height=.1,layer='NOTE'))
    lo=m.add_text('LOW=0m',dxfattribs=dict(insert=(0,6),height=.1,layer='NOTE'))
    hi=m.add_text('HIGH=3m',dxfattribs=dict(insert=(0,7),height=.1,layer='NOTE'))
    opening=m.add_lwpolyline([(-.2*scale,-.5*scale),(.2*scale,-.5*scale),(.2*scale,.5*scale),(-.2*scale,.5*scale)],close=True,dxfattribs=dict(layer='WALL'))
    duct=m.add_line((-2*scale,.3*scale,route_z*scale),(2*scale,.3*scale,route_z*scale),dxfattribs=dict(layer='DUCT'))
    tray=m.add_line((-2*scale,-.3*scale,route_z*scale),(2*scale,-.3*scale,route_z*scale),dxfattribs=dict(layer='TRAY'))
    device=d.blocks.new('DEVICE');port=device.add_point((0,0,0))
    ah=m.add_blockref('DEVICE',(-2*scale,-.3*scale,route_z*scale));bh=m.add_blockref('DEVICE',(2*scale,-.3*scale,route_z*scale))
    path=folder/'anonymous.dxf';d.saveas(path)
    done=run(SHARED/'extract_cad_neutral.py','--source-dxf',path,'--output-dir',folder/'neutral')
    assert done.returncode in (0,3),done.stdout+done.stderr
    manifest=folder/'neutral/CAD中性取证清单.json'
    ids={k:'CAD-'+e.dxf.handle for k,e in locals().copy().items()if isinstance(e,ezdxf.entities.DXFEntity)}
    basis=[ids['note']]
    def scalar(sid,s):
        return dict(source_id=sid,expected_text=s,pattern=r'(?:LOW|HIGH)=(?P<value>[-\d.]+)(?P<unit>m)',unit='m')
    bounds=dict(datum_id='DATUM',datum_basis_source_ids=basis,lower=scalar(ids['lo'],'LOW=0m'),upper=scalar(ids['hi'],'HIGH=3m'))
    context=dict(space_id='S',floor_id='F1',basis='anonymous source object binding',basis_source_ids=basis)
    route=dict(id='R',physical_id='PR',discipline='pipe',system='W',bindings=[dict(source_id=ids['entity'])],
               z_mode='source_wcs',datum_id='DATUM',z_basis_source_ids=basis,**context)
    barrier=dict(id='B',physical_id='PB',kind='wall',form='closed_footprint',boundary=[dict(source_id=ids['wall'])],z_bounds=bounds,**context)
    request=dict(schema=pen.SCHEMA,source_sha256=sha(path),cad_manifest_sha256=sha(manifest),layout='Model',
                 scope='anonymous bounded source objects',routes=[route],barriers=[barrier])
    return dict(manifest=manifest,request=request,ids=ids,folder=folder,context=context,bounds=bounds,source=path,scale=scale,doc=d)


def calc(c):
    return pen.calculate(c['manifest'],c['request'],c['folder'])[0]


def refresh(c):
    path=c['folder']/'changed.dxf';c['doc'].saveas(path)
    done=run(SHARED/'extract_cad_neutral.py','--source-dxf',path,'--output-dir',c['folder']/'changed-neutral')
    assert done.returncode in (0,3),done.stdout+done.stderr
    c['source']=path;c['manifest']=c['folder']/'changed-neutral/CAD中性取证清单.json'
    c['request'].update(source_sha256=sha(path),cad_manifest_sha256=sha(c['manifest']))


@pytest.mark.parametrize('unit',[4,6])
def test_three_direct_disciplines_and_real_cli(tmp_path,unit):
    c=fixture(tmp_path/'source',unit=unit)
    for key,discipline in [('duct','duct'),('tray','tray')]:
        route=copy.deepcopy(c['request']['routes'][0]);route.update(id=key,physical_id='P-'+key,discipline=discipline,bindings=[dict(source_id=c['ids'][key])])
        c['request']['routes'].append(route)
    req=put(tmp_path/'request.json',c['request']);output=tmp_path/'result'
    done=run(SHARED/'calculate_penetrations.py','--cad-manifest',c['manifest'],'--request',req,'--output-dir',output)
    assert done.returncode==0,done.stdout+done.stderr
    result=json.loads((output/'穿越计算结果.json').read_text(encoding='utf-8'))
    assert result['quantities']['spatial_crossing_count']==3
    assert {e['discipline']for e in result['events']}=={'pipe','duct','tray'}
    assert all(e['sleeve_quantity']is None and e['firestop_volume']is None for e in result['events'])
    assert not result['business_adoption_allowed']
    assert all(e['source_geometry'][0]['source']['part_index']==0 for e in result['events'])


def test_same_wall_two_faces_and_two_passes_are_two_not_450mm_merge(tmp_path):
    c=fixture(tmp_path/'source',shape='twice');result=calc(c)
    assert result['quantities']['spatial_crossing_count']==2
    assert math.dist(result['events'][0]['point'],result['events'][1]['point'])==pytest.approx(.2)
    # Explicit repeated drawing representation has the same physical identities.
    c['request']['routes'].append(dict(c['request']['routes'][0],id='RA'))
    c['request']['barriers'].append(dict(c['request']['barriers'][0],id='BA'))
    result=calc(c)
    assert result['quantities']['spatial_crossing_count']==2 and len(result['duplicate_representations'])==2


def test_same_point_two_physical_pipes_and_existing_shared_opening(tmp_path):
    c=fixture(tmp_path/'source')
    other=c['doc'].modelspace().add_line((-2,0,1),(2,0,1),dxfattribs=dict(layer='PIPE'));refresh(c)
    c['request']['routes'].append(dict(c['request']['routes'][0],id='R2',physical_id='P2',bindings=[dict(source_id='CAD-'+other.dxf.handle)]))
    c['request']['openings']=[dict(id='O',physical_id='PO',barrier_physical_id='PB',boundary=[dict(source_id=c['ids']['opening'])],z_bounds=c['bounds'],**c['context'])]
    result=calc(c)
    assert result['quantities']['spatial_crossing_count']==2
    assert all(e['opening_refs']and e['new_opening_required']is None for e in result['events'])
    assert len(result['shared_openings'])==1 and len(result['shared_openings'][0]['event_ids'])==2


@pytest.mark.parametrize('shape',['arc','ellipse','circle'])
def test_complete_conic_not_endpoint_chord(tmp_path,shape):
    c=fixture(tmp_path/'source',shape=shape)
    c['request']['barriers'][0]['boundary']=[dict(source_id=c['ids']['wall'])]
    # At x=0 the upper arc lies above this wall; a chord would create a false crossing.
    result=calc(c)
    if shape in ('arc','circle'):
        assert result['quantities']['spatial_crossing_count']==0
    else:
        # The ellipse reaches y=1 only at its apex; its neighbouring portions
        # lie inside the wall footprint and genuinely traverse both side faces.
        assert result['quantities']['spatial_crossing_count']==1
        assert result['events'][0]['point'][1]>.99


def test_conic_wall_and_conic_route_intersection(tmp_path):
    c=fixture(tmp_path/'source',wall_shape='circle');result=calc(c)
    assert result['quantities']['spatial_crossing_count']==1
    assert result['events'][0]['entry'][0]==pytest.approx(-1)
    assert result['events'][0]['exit'][0]==pytest.approx(1)


def test_true_z_slab_vertical_and_different_elevation(tmp_path):
    c=fixture(tmp_path/'vertical',shape='vertical');c['request']['barriers'][0]['kind']='slab'
    result=calc(c);assert result['quantities']['spatial_crossing_count']==1
    assert result['events'][0]['entry'][2]==pytest.approx(0)
    assert result['events'][0]['exit'][2]==pytest.approx(3)
    high=fixture(tmp_path/'high',route_z=5);result=calc(high)
    assert result['quantities']['spatial_crossing_count']==0
    assert result['noncrossings'][0]['kind']=='different_elevation'


def test_along_wall_and_corner_touch_not_crossing(tmp_path):
    c=fixture(tmp_path/'source',shape='tangent');result=calc(c)
    assert result['quantities']['spatial_crossing_count']==0
    assert any(r['kind']=='along_boundary'for r in result['noncrossings'])


@pytest.mark.parametrize('missing',['wall','height','unit','z','floor'])
def test_missing_facts_never_complete_zero(tmp_path,missing):
    c=fixture(tmp_path/'source',unit=0 if missing=='unit'else 6)
    if missing=='wall':c['request']['barriers']=[]
    elif missing=='height':del c['request']['barriers'][0]['z_bounds']
    elif missing=='z':c['request']['routes'][0]['z_mode']='plan_only'
    elif missing=='floor':c['request']['barriers'][0]['floor_id']='F2'
    result=calc(c)
    if missing=='floor':
        assert result['pair_coverage'][0]['status']=='different_space_or_floor'
        assert result['quantities']['complete_spatial_count']is None
    else:
        assert result['quantities']['complete_spatial_count']is None
    if missing in ('height','unit','z'):
        assert result['quantities']['plan_crossing_candidate_count']==1


def test_request_geometry_and_source_stream_self_producer_cannot_forge_geometry(tmp_path):
    c=fixture(tmp_path/'source');c['request']['routes'][0]['bindings'][0]['geometry']={'parts':[]}
    with pytest.raises(ValueError,match='手填几何'):
        calc(c)
    del c['request']['routes'][0]['bindings'][0]['geometry']
    # Arbitrary extra producer declaration has no ability to modify real DXF geometry.
    c['request']['producer']='custom.py'
    result=calc(c);assert result['events'][0]['entry'][0]==pytest.approx(-.1)


def test_scalar_unit_mismatch_is_blocked(tmp_path):
    c=fixture(tmp_path/'source');c['request']['barriers'][0]['z_bounds']['upper']['unit']='mm'
    with pytest.raises(ValueError,match='单位'):
        calc(c)


def test_unsupported_spline_preserves_full_source_and_unknown(tmp_path):
    c=fixture(tmp_path/'source',shape='spline');result=calc(c)
    assert result['quantities']['complete_spatial_count']is None
    assert any(t.get('source_row',{}).get('entity_type')=='SPLINE'for t in result['todo'])


def test_cli_preflights_hardlinks_reparse_and_all_outputs(tmp_path):
    c=fixture(tmp_path/'source');req=put(tmp_path/'request.json',c['request'])
    target=tmp_path/'output';target.mkdir();os.link(c['source'],target/'穿越计算结果.json')
    before=sha(c['source'])
    done=run(SHARED/'calculate_penetrations.py','--cad-manifest',c['manifest'],'--request',req,'--output-dir',target)
    assert done.returncode==2 and sha(c['source'])==before
    assert not (target/'穿越位置明细.csv').exists()
    link=tmp_path/'link'
    try:link.symlink_to(c['folder'],target_is_directory=True)
    except OSError: return
    with pytest.raises(ValueError,match='reparse'):
        sources.reject_alias(link/'out')


def test_real_pipe_consumer_then_public_import_rebuilds_actual_source(tmp_path):
    c=fixture(tmp_path/'source');sid='SEG-'+c['ids']['entity']
    segment=dict(segment_id=sid,system='W',start=[-2,0,1],end=[2,0,1],length=4,
                 source_ids=[c['ids']['entity']],dn_seeds=[dict(dn='DN80',evidence_level='confirmed',source_id=c['ids']['note'])])
    data=dict(schema_version='qto-pipe-network-inference-input/1.2',network_id='ANON',unit='m',parameters=dict(snap_tolerance=.001,gap_max=.3),segments=[segment],
              object_selection=dict(audit_id='A',status='READY',candidate_count=1,included_count=1,excluded_count=0,unresolved_count=0,included_segment_ids=[sid],source_ids=[c['ids']['note']]),
              endpoint_handoff=dict(handoff_id='H',required=False,source_instance_count=0,delivered_count=0,not_applicable_count=0,unresolved_count=0,source_id=c['ids']['note'],reason='anonymous no points'))
    path=put(tmp_path/'pipe.json',data)
    done=run(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py','--input',path,'--output',tmp_path/'pipe-result.json')
    assert done.returncode==0,done.stdout+done.stderr
    route=c['request']['routes'][0];del route['bindings'];route['import']=dict(kind='pipe_segments',input=ref(path),segment_ids=[sid])
    result=calc(c);assert result['quantities']['spatial_crossing_count']==1
    data['segments'][0]['end']=[20,0,1];put(path,data);route['import']['input']=ref(path)
    with pytest.raises(ValueError,match='原part'):
        calc(c)


def test_real_duct_fixed_profile_and_public_import(tmp_path):
    c=fixture(tmp_path/'source');import build_duct_network_from_neutral as duct
    section=dict(shape='rectangle',width=300,height=200,unit='mm')
    rule=dict(rule_id='C',selector=dict(layer_pattern='^DUCT$'),representation='centerline',system='D',section=section)
    evidence=dict(schema_version='qto-duct-recognition-evidence/1.0',source_sha256=sha(c['source']),layout='Model',space_id='S',crop=None,
                  basis='anonymous source classification',source_locator='Model DUCT',**{k:rule[k]for k in ('rule_id','selector','representation','system','section')})
    ep=put(tmp_path/'duct-evidence.json',evidence);rule['evidence']=ref(ep)
    profile=dict(schema_version=duct.SCHEMA,expected_source_sha256=sha(c['source']),layout='Model',space_id='S',floor_id='F1',package_id='ANON',
                 target_layer_pattern='^DUCT$',parameters=dict(parameter_basis='anonymous fixed inputs'),rules=[rule])
    pp=put(tmp_path/'profile.json',profile)
    done=run(ROOT/'skills/qto-duct-air-systems/scripts/build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',pp,'--output-dir',tmp_path/'duct-output')
    assert done.returncode==0,done.stdout+done.stderr
    route=c['request']['routes'][0];del route['bindings'];route.update(discipline='duct',**{'import':dict(kind='duct_profile',profile=ref(pp),segment_id='DSEG-000001')})
    result=calc(c);assert result['quantities']['spatial_crossing_count']==1
    assert result['provenance']['producer_runs'][0]['status']=='REBUILT'


def test_real_electrical_object_request_to_public_tray(tmp_path):
    c=fixture(tmp_path/'source');import bind_electrical_objects as electrical
    ids=c['ids'];basis=[ids['note']]
    def endpoint(key,h):
        return dict(id=key,kind='connection_port',manifest_sha256=sha(c['manifest']),physical_source_id=ids[h],physical_id=ids[h],
                    port_source_id=ids[h]+'/DEVICE/'+ids['port'],instance_path=ids[h],locator='point',basis='anonymous native port',basis_source_ids=basis)
    step=dict(source_id=ids['tray'],part_index=0,instance_path=ids['tray'],fraction_range=[0,1],direction=1,sheet_id='S1',floor='F1',role='horizontal',basis_source_ids=basis)
    obj=dict(id='TRAY',physical_id='P-TRAY',kind='tray',system='POWER',spec='anonymous',parallel_count=1,start_endpoint='A',end_endpoint='B',steps=[step],basis_source_ids=basis,
             component_evidence={k:dict(status='not_applicable',basis='anonymous scope',source_ids=basis)for k in electrical.COMPONENTS})
    req=dict(schema=electrical.SCHEMA,project_id='ANON',drawing_id='D',review_id='RV',cad_manifest_sha256=sha(c['manifest']),source_sha256=sha(c['source']),layout='Model',
             unit=dict(factor_to_m=1,basis='DXF m'),expand_blocks=['DEVICE'],measurement_mode='spatial',measurement_basis_source_ids=basis,connection_tolerance_m=1e-6,
             route_layers='^TRAY$',sheets=[dict(sheet_id='S1',floor='F1',bounds=[-10,-10,10,10])],endpoints=[endpoint('A','ah'),endpoint('B','bh')],objects=[obj])
    pp=put(tmp_path/'electrical.json',req)
    actual=electrical.calculate(electrical.Evidence(c['manifest'],req),req)
    assert actual['objects'][0]['source_steps']
    route=c['request']['routes'][0];del route['bindings'];route.update(discipline='tray',**{'import':dict(kind='electrical_request',request=ref(pp),object_id='TRAY')})
    result=calc(c);assert result['quantities']['spatial_crossing_count']==1


def test_paired_wall_faces_once_with_source_identity(tmp_path):
    c=fixture(tmp_path/'source');m=c['doc'].modelspace()
    a=m.add_line((-.1,-2),(-.1,2));b=m.add_line((.1,-2),(.1,2));refresh(c)
    wall=c['request']['barriers'][0];wall.update(form='paired_faces',boundary=[dict(source_id='CAD-'+e.dxf.handle)for e in(a,b)])
    result=calc(c);assert result['quantities']['spatial_crossing_count']==1
    assert len(result['events'])==1


def test_paired_faces_same_face_return_is_not_full_wall_crossing(tmp_path):
    c=fixture(tmp_path/'source');m=c['doc'].modelspace()
    a=m.add_line((-.1,-2),(-.1,2));b=m.add_line((.1,-2),(.1,2))
    route=m.add_polyline3d([(-2,0,1),(0,0,1),(-2,.2,1)]);refresh(c)
    c['request']['routes'][0]['bindings']=[dict(source_id='CAD-'+route.dxf.handle)]
    c['request']['barriers'][0].update(form='paired_faces',boundary=[dict(source_id='CAD-'+e.dxf.handle)for e in(a,b)])
    result=calc(c)
    assert result['quantities']['known_spatial_crossing_subtotal']==0
    assert result['quantities']['complete_spatial_count']is None
    assert any(e['kind']=='partial_in_wall'for e in result['noncrossings'])


def test_gap_is_scenario_and_never_confirmed_drawn_crossing(tmp_path):
    c=fixture(tmp_path/'source');m=c['doc'].modelspace()
    a=m.add_line((-2,0,1),(-.5,0,1));b=m.add_line((.5,0,1),(2,0,1));refresh(c)
    route=c['request']['routes'][0];del route['bindings']
    route['scenario_gaps']=[dict(hypothesis_id='HYP',basis_source_ids=[c['ids']['note']],
                                **{'from':dict(source_id='CAD-'+a.dxf.handle,part_index=0,endpoint='end'),
                                   'to':dict(source_id='CAD-'+b.dxf.handle,part_index=0,endpoint='start')})]
    result=calc(c)
    assert result['quantities']['spatial_crossing_count']is None
    assert result['quantities']['known_spatial_crossing_subtotal']==0
    assert result['quantities']['scenario_crossing_candidate_count']==1
    assert result['quantities']['complete_spatial_count']is None


def test_complete_nested_instance_raw_transform_and_reversed_duplicate(tmp_path):
    c=fixture(tmp_path/'source');d=c['doc'];m=d.modelspace();block=d.blocks.new('NESTED')
    line=block.add_line((-2,0,1),(2,0,1));insert=m.add_blockref('NESTED',(0,0,0),dxfattribs=dict(xscale=1,yscale=2))
    refresh(c)
    route=c['request']['routes'][0];route['bindings']=[dict(source_id='CAD-'+insert.dxf.handle+'/NESTED/CAD-'+line.dxf.handle)]
    duplicate=copy.deepcopy(route);duplicate['id']='ALIAS';duplicate['bindings'][0]['direction']=-1
    c['request']['routes'].append(duplicate)
    result=calc(c);assert result['quantities']['spatial_crossing_count']==1
    assert '/NESTED/'in result['events'][0]['source_geometry'][0]['source']['instance_path']


def test_single_boundary_curve_tangency_is_separate_candidate_type(tmp_path):
    c=fixture(tmp_path/'source',shape='arc');m=c['doc'].modelspace();line=m.add_line((-3,2),(3,2));refresh(c)
    c['request']['barriers'][0].update(form='single_boundary',boundary=[dict(source_id='CAD-'+line.dxf.handle)])
    result=calc(c)
    assert not result['events']
    assert any(r['kind']=='touch'for r in result['noncrossings'])


def test_curved_wall_exact_conic_pair_and_closed_parameter_wrap(tmp_path):
    c=fixture(tmp_path/'source',wall_shape='circle');m=c['doc'].modelspace()
    ellipse=m.add_ellipse((1.5,0,1),(2,0,0),1,start_param=math.pi,end_param=3*math.pi)
    refresh(c);c['request']['routes'][0]['bindings']=[dict(source_id='CAD-'+ellipse.dxf.handle)]
    result=calc(c)
    assert result['quantities']['spatial_crossing_count']==1
    assert result['events'][0].get('wraps_closed_route')


def test_unrelated_malformed_curve_retains_source_without_erasing_valid_scope(tmp_path):
    c=fixture(tmp_path/'source');c['doc'].modelspace().add_lwpolyline([(10,10,1),(10,10,0)],format='xyb');refresh(c)
    result=calc(c)
    assert result['quantities']['complete_spatial_count']==1
    assert result['provenance']['source_reconstruction_issues']
    assert any('弧段弦长为零'in row.get('error','')for row in result['provenance']['source_reconstruction_issues'])


def test_one_source_claimed_as_two_pipes_needs_sourced_multiplicity(tmp_path):
    c=fixture(tmp_path/'source');note=c['doc'].modelspace().add_text('PIPE COUNT=2');refresh(c)
    first=c['request']['routes'][0];c['request']['routes'].append(dict(first,id='R2',physical_id='P2'))
    result=calc(c)
    assert result['quantities']['spatial_crossing_count']is None
    assert result['quantities']['physical_identity_candidate_count']==2
    for index,route in enumerate(c['request']['routes'],1):
        route['multiplicity_evidence']=dict(source_id='CAD-'+note.dxf.handle,expected_text='PIPE COUNT=2',pattern=r'PIPE COUNT=(?P<count>\d+)',instance_index=index)
    result=calc(c);assert result['quantities']['spatial_crossing_count']==2


def test_duplicate_opening_identity_does_not_make_one_event_shared(tmp_path):
    c=fixture(tmp_path/'source')
    opening=dict(id='O1',physical_id='PO',barrier_physical_id='PB',boundary=[dict(source_id=c['ids']['opening'])],z_bounds=c['bounds'],**c['context'])
    c['request']['openings']=[opening,dict(opening,id='O2')]
    result=calc(c)
    assert len(result['events'])==1
    assert len(result['events'][0]['opening_refs'])==1
    assert not result['shared_openings']
    assert any(a.get('kind')=='opening'for a in result['duplicate_representations'])


def test_conflicting_opening_height_stays_unresolved(tmp_path):
    c=fixture(tmp_path/'source');high=c['doc'].modelspace().add_text('HIGH=4m');refresh(c)
    opening=dict(id='O1',physical_id='PO',barrier_physical_id='PB',boundary=[dict(source_id=c['ids']['opening'])],z_bounds=c['bounds'],**c['context'])
    other=copy.deepcopy(opening);other['id']='O2'
    other['z_bounds']['upper'].update(source_id='CAD-'+high.dxf.handle,expected_text='HIGH=4m')
    c['request']['openings']=[opening,other];result=calc(c)
    assert result['quantities']['complete_spatial_count']is None
    assert not result['shared_openings']
    assert all(ref['relation_status']=='opening_identity_conflict'for ref in result['events'][0]['opening_refs'])


def midline_fixture(folder):
    """Actual two-outline affine source + crossing/near/tangent architectural facts."""
    c=fixture(folder,unit=4);d=c['doc'];m=d.modelspace();d.layers.add('MID')
    block=d.blocks.new('MIDLINE-OUTLINES')
    block.add_arc((0,0),1000,0,90);block.add_arc((0,0),1100,0,90)
    m.add_blockref('MIDLINE-OUTLINES',(3000,0),dxfattribs=dict(xscale=2,yscale=1,layer='MID'))
    crossing=m.add_lwpolyline([(3995,-100),(4005,-100),(4005,1600),(3995,1600)],close=True)
    near=m.add_line((2800,1050.1),(5200,1050.1));tangent=m.add_line((2800,1050),(5200,1050))
    refresh(c);import build_duct_network_from_neutral as duct
    section=dict(shape='circle',diameter=150,unit='mm')
    rule=dict(rule_id='MID',selector=dict(layer_pattern='^MID$'),representation='outline',system='D',section=section,pair_tolerance=.1,
              equidistant_midline=dict(method='euclidean_nested_ellipse',distance_tolerance=1e-8,chord_tolerance=.5,length_tolerance=.01,basis='anonymous implicit Euclidean equal-distance source'))
    evidence=dict(schema_version='qto-duct-recognition-evidence/1.0',source_sha256=sha(c['source']),layout='Model',space_id='S',crop=None,
                  basis='anonymous complete source outlines length-only',source_locator='Model MID',**{k:rule[k]for k in('rule_id','selector','representation','system','section','equidistant_midline')})
    ep=put(folder/'midline-evidence.json',evidence);rule['evidence']=ref(ep)
    pp=put(folder/'midline-profile.json',dict(schema_version=duct.SCHEMA,expected_source_sha256=sha(c['source']),layout='Model',space_id='S',floor_id='F1',package_id='MID',target_layer_pattern='^MID$',
                                            parameters=dict(parameter_basis='anonymous bounded numerical settings'),rules=[rule]))
    route=c['request']['routes'][0];del route['bindings'];route.update(discipline='duct',z_mode='plan_only',**{'import':dict(kind='duct_profile',profile=ref(pp),segment_id='DSEG-000001')})
    c['request']['barriers']=[dict(id=key,physical_id='P-'+key,kind='wall',form=form,boundary=[dict(source_id='CAD-'+entity.dxf.handle)],**c['context'])
                              for key,form,entity in [('CROSS','closed_footprint',crossing),('NEAR','single_boundary',near),('TANGENT','single_boundary',tangent)]]
    return c


def test_fixed_implicit_midline_preserves_integral_and_near_boundary_uncertainty(tmp_path):
    c=midline_fixture(tmp_path/'source');rp=put(tmp_path/'request.json',c['request']);out=tmp_path/'output'
    done=run(SHARED/'calculate_penetrations.py','--cad-manifest',c['manifest'],'--request',rp,'--output-dir',out)
    assert done.returncode==0,done.stdout+done.stderr
    result=json.loads((out/'穿越计算结果.json').read_text(encoding='utf-8'))
    assert result['quantities']['spatial_crossing_count']is None
    assert result['quantities']['derived_crossing_candidate_count']==1
    source=result['events'][0]['source_geometry'][0]['source']
    assert source['actual_curve_length']*.001==pytest.approx(2.542396993526945,abs=2e-9)
    assert not source['midline_evidence']['length_is_polyline_chord_sum']
    assert source['length_error_estimate']>0
    assert {p['barrier_id']for p in result['uncertain_candidates']}=={'CROSS','NEAR','TANGENT'}
    assert all(p['position_error_bound']>0 for p in result['uncertain_candidates'])
    assert not any(e['barrier_id']=='NEAR'for e in result['events'])
