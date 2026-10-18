"""Anonymous source-drawing mixed network tests; no project quantities or answers."""
import copy
import base64
import gzip
import re
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/qto-electrical-routes/scripts'
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
import partition_electrical_route_network as network


def run(args):
    return subprocess.run([sys.executable, '-B', *map(str,args)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding='utf-8', timeout=180)


def fixture(folder, loop=False, mm=False, curve=False, nested=False):
    folder.mkdir(parents=True)
    doc = ezdxf.new('R2013'); doc.units = 4 if mm else 6
    scale = 1000 if mm else 1
    doc.layers.add('ROUTE'); doc.layers.add('NOTE')
    m = doc.modelspace()
    dev = doc.blocks.new('DEVICE'); port = dev.add_point((0,0,0)).dxf.handle
    basis = 'CAD-' + m.add_text('匿名图例与系统图：源端口供电照明及控制按钮', dxfattribs=dict(insert=(0,-2*scale),layer='NOTE',height=.2*scale)).dxf.handle
    h = {}
    space = m
    if nested:
        space = doc.blocks.new('FLOOR')
    for key, point in [('SRC',(0,0)),('LAMP',(10,0)),('BTN',(5,4))]:
        h[key] = space.add_blockref('DEVICE', (point[0]*scale,point[1]*scale)).dxf.handle
    h['TRUNK'] = space.add_line((0,0),(10*scale,0),dxfattribs=dict(layer='ROUTE')).dxf.handle
    h['BRANCH'] = space.add_line((5*scale,0),(5*scale,4*scale),dxfattribs=dict(layer='ROUTE')).dxf.handle
    if loop:
        h['LOOP'] = space.add_lwpolyline([(0,0),(0,-scale),(10*scale,-scale),(10*scale,0)],dxfattribs=dict(layer='ROUTE')).dxf.handle
    if curve:
        # Replace straight load branch with an actual half circle of radius 2.
        space.delete_entity(next(e for e in space if e.dxf.handle == h['BRANCH']))
        h['BRANCH'] = space.add_arc((5*scale,2*scale),2*scale,270,90,dxfattribs=dict(layer='ROUTE')).dxf.handle
    prefix = ''
    if nested:
        top = m.add_blockref('FLOOR',(20*scale,30*scale)).dxf.handle
        prefix = 'CAD-'+top+'/FLOOR/'
    source = folder/'匿名混连.dxf';doc.saveas(source)
    done = run([SHARED/'extract_cad_neutral.py','--source-dxf',source,'--output-dir',folder/'中性事实'])
    assert done.returncode == 0, done.stdout+done.stderr
    manifest = folder/'中性事实/CAD中性取证清单.json'
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    mf = json.loads(manifest.read_text(encoding='utf-8'))
    objs=[]
    for key,category in [('SRC','source'),('LAMP','lighting'),('BTN','control')]:
        sid=prefix+'CAD-'+h[key]
        objs.append(dict(object_key=key,category=category,source_ids=[basis,sid],endpoint_binding=dict(
            kind='connection_port',manifest_sha256=digest,physical_source_id=sid,physical_id=sid,
            port_source_id=sid+'/DEVICE/CAD-'+port,instance_path=sid,locator='point',basis='原实例内实际连接口',basis_source_ids=[basis])))
    offset=20*scale if nested else 0; offy=30*scale if nested else 0
    request=dict(schema=network.REQUEST_SCHEMA,project_id='匿名电气',scope_id='SYN-F1',
        cad_manifest_sha256=digest,source_sha256=mf['authoritative_source']['sha256'],
        trace_request=dict(drawing_id='SYN',layout='Model',route_layers='^ROUTE$',
            unit=dict(factor_to_output=1/scale,output_coordinate_unit='m',source_id='匿名DXF明确单位'),
            expand_blocks=['FLOOR','DEVICE'] if nested else ['DEVICE'],snap_tolerance=.000001,gap_tolerance=0.,endpoint_tolerance=.000001),
        endpoint_objects=objs,connections=[dict(id='SUPPLY',from_object='SRC',to_object='LAMP',relation='supply',source_ids=[basis]),
                                         dict(id='CONTROL',from_object='SRC',to_object='BTN',relation='control',source_ids=[basis])],
        circuit_specs=[],sheets=[dict(sheet_id='F1',floor='F1',bounds=[offset-3*scale,offy-3*scale,offset+13*scale,offy+6*scale])])
    return manifest,request,h,basis


def calculate(manifest,request):
    return network.partition_network(manifest,request)


@pytest.mark.parametrize('mm',[False,True])
def test_unlabeled_mixed_t_network_is_actually_partitioned(tmp_path,mm):
    manifest,req,h,_=fixture(tmp_path/'源',mm=mm)
    result=calculate(manifest,req)
    assert result['status']=='PARTITIONED_REFERENCE'
    cons={c['id']:c for c in result['connections']}
    assert cons['SUPPLY']['status']==cons['CONTROL']['status']=='PARTITIONED_REFERENCE'
    assert cons['SUPPLY']['category']=='lighting' and cons['CONTROL']['category']=='control'
    assert cons['SUPPLY']['traced_path_length_m']==pytest.approx(10)
    assert cons['CONTROL']['traced_path_length_m']==pytest.approx(9)
    trunk=[a for a in result['physical_segments'] if a['source_id']=='CAD-'+h['TRUNK']]
    assert [a['fraction_range']for a in trunk]==[[0,.5],[.5,1]]
    assert trunk[0]['connection_ids']==['CONTROL','SUPPLY'] and trunk[1]['connection_ids']==['SUPPLY']
    assert trunk[0]['categories']==['control','lighting']
    assert sum(a['length_m']for a in result['physical_segments'])==pytest.approx(14)
    assert all(c['circuit_id'] is None and c['circuit_assignment_status']=='UNLABELED'for c in cons.values())
    assert all(a['physical_channel_quantity_m'] is None and a['conductor_quantity_m'] is None for a in result['physical_segments'])
    assert all(not g['is_electrical_circuit']for g in result['branch_groups'])
    assert result['ledger']['circuits']==[] and result['checks']['split_length_conserved']
    assert result['official_adoption'] is False


def add_specs(req,basis,circuit='L-VERIFIED',count=3):
    req['endpoint_objects'][2]['category']='lighting'
    req['connections'][1]['relation']='supply'
    for c in req['connections']:
        c.update(circuit_id=circuit,circuit_source_ids=[basis])
    req['circuit_specs']=[dict(circuit_id=circuit,kind='wire',system='照明',spec='匿名导线',parallel_count=count,
                               extras=[],pending_components=[],source_ids=[basis])]


def test_one_circuit_branch_trunk_is_deduplicated_and_existing_calculator_runs(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    result=calculate(manifest,req)
    assert result['status']=='PARTITIONED_REFERENCE'
    q=result['circuit_quantities'][0]
    assert q['known_drawn_path_length_m']==pytest.approx(14)
    assert q['known_length_m']==q['complete_length_m']==pytest.approx(42)
    assert len(result['ledger']['circuits'])==3
    assert sum(i['length_m']for i in result['reference_calculation']['items'])==pytest.approx(42)
    assert all(i['official_adoption'] is False for i in result['reference_calculation']['items'])


def test_two_circuits_share_physical_geometry_but_conductors_separate(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    req['connections'][1]['circuit_id']='SECOND'
    req['circuit_specs'].append(dict(req['circuit_specs'][0],circuit_id='SECOND',parallel_count=2))
    result=calculate(manifest,req)
    q={q['circuit_id']:q for q in result['circuit_quantities']}
    assert q['L-VERIFIED']['complete_length_m']==pytest.approx(30)
    assert q['SECOND']['complete_length_m']==pytest.approx(18)
    shared=next(a for a in result['physical_segments']if a['shared_drawn_segment'])
    assert shared['length_m']==pytest.approx(5) and shared['conductor_quantity_m']==pytest.approx(25)
    assert shared['physical_channel_quantity_m'] is None


@pytest.mark.parametrize('field',['parallel_count','extras','spec'])
def test_unknown_parameter_never_becomes_zero_or_default(tmp_path,field):
    manifest,req,_,basis=fixture(tmp_path/'源');add_specs(req,basis)
    req['circuit_specs'][0][field]=None
    result=calculate(manifest,req);q=result['circuit_quantities'][0]
    assert q['known_length_m'] is None and q['complete_length_m'] is None and q['extra_each_m'] is None
    assert any(p==field or p.endswith(':'+field)for p in q['pending_fields']) and result['ledger']['circuits']==[]


def test_endpoint_class_conflict_is_not_overwritten_by_name_or_declared_priority(tmp_path):
    manifest,req,_,basis=fixture(tmp_path/'源')
    req['endpoint_objects'][0]['category']='power'
    req['connections'][0].update(category='lighting',classification_source_ids=[basis])
    result=calculate(manifest,req);c=result['connections'][0]
    assert c['category'] is None and c['status']=='PARTITIONED_REVIEW'
    assert c['traced_path_length_m']==pytest.approx(10) and c['path_ids']
    assert any('冲突'in p for p in c['problems'])


def test_unequal_loop_does_not_choose_shortest_without_source_constraint(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源',loop=True)
    result=calculate(manifest,req)
    assert result['connections'][0]['status']=='PARTITIONED_REVIEW'
    assert '走线网络有环，取最短'in result['connections'][0]['problems']
    req['connections'][0].update(via_handles=[h['TRUNK']],via_basis='匿名系统图实际去向明确',via_source_ids=[basis])
    result=calculate(manifest,req)
    assert result['connections'][0]['status']=='PARTITIONED_REFERENCE'


def test_missing_actual_port_remains_unresolved_not_insert_or_nearest_fallback(tmp_path):
    manifest,req,h,_=fixture(tmp_path/'源')
    req['endpoint_objects'][1].pop('endpoint_binding')
    req['endpoint_objects'][1]['endpoint']={'instance_id':'PT-CAD-'+h['LAMP']}
    result=calculate(manifest,req)
    assert result['objects'][1]['point_m'] is None
    assert result['connections'][0]['status']=='UNRESOLVED' and result['connections'][0]['path_ids']==[]
    assert result['source_coverage']['unclaimed_atom_ids']


def test_same_class_nearby_objects_do_not_produce_unproven_circuit(tmp_path):
    manifest,req,_,_=fixture(tmp_path/'源')
    req['endpoint_objects'][2]['category']='lighting';req['connections'][1]['relation']='supply'
    result=calculate(manifest,req)
    assert all(c['circuit_id'] is None for c in result['connections'])
    assert result['ledger']['circuits']==[]


def test_changed_relationship_rebuilds_paths_and_scope_not_only_pending_marker(tmp_path):
    manifest,req,_,_=fixture(tmp_path/'源')
    before=calculate(manifest,req)
    req2=copy.deepcopy(req);req2['connections'][1]['from_object']='LAMP'
    after=calculate(manifest,req2)
    assert before['network_fingerprint']!=after['network_fingerprint']
    b=before['connections'][1];a=after['connections'][1]
    assert a['status']=='PARTITIONED_REFERENCE' and a['path_ids']!=b['path_ids']
    shared_before=next(x for x in before['physical_segments']if x['shared_drawn_segment'])
    shared_after=next(x for x in after['physical_segments']if x['shared_drawn_segment'])
    assert shared_before['fraction_range']==[0,.5] and shared_after['fraction_range']==[.5,1]
    assert after['checks']['rebuilt_from_current_source']


def test_fingerprints_and_full_instance_paths_protect_namespace(tmp_path):
    manifest,req,_,_=fixture(tmp_path/'源',nested=True)
    result=calculate(manifest,req)
    assert all('/FLOOR/'in a['instance_path']for a in result['physical_segments'])
    assert all(c['status']=='PARTITIONED_REFERENCE'for c in result['connections'])
    req['source_sha256']='0'*64
    with pytest.raises(ValueError,match='source_sha256'):calculate(manifest,req)


def test_real_cli_reference_and_html_overlay_chain(tmp_path):
    manifest,req,_,basis=fixture(tmp_path/'源');add_specs(req,basis)
    rp=tmp_path/'匿名请求.json';rp.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'网络复核'
    done=run([SCRIPTS/'partition_electrical_route_network.py','--cad-manifest',manifest,'--request',rp,'--output-dir',out,'--build-review'])
    assert done.returncode==0,done.stdout+done.stderr
    result=json.loads((out/'混连网络拆分结果.json').read_text(encoding='utf-8'))
    receipts=json.loads((out/'调用回执.json').read_text(encoding='utf-8'))
    assert [r['exit_code']for r in receipts]==[0,0,0]
    assert result['circuit_quantities'][0]['complete_length_m']==pytest.approx(42)
    view=json.loads((out/'复核视图.json').read_text(encoding='utf-8'))
    assert len(view['objects'])==3 and (view.get('background_sets')or view['scenes'][0].get('background'))
    html=(out/'电气混连网络复核.html').read_text(encoding='utf-8')
    assert '__PAYLOAD__'not in html and '电气混连网络拆分复核'in html
    assert len(html.encode('utf-8'))<2_000_000 and max(map(len,html.splitlines()))<10000



def test_conflicting_circuit_candidates_are_not_selected_by_label(tmp_path):
    manifest,req,_,basis=fixture(tmp_path/'源')
    req['connections'][0]['circuit_candidates']=[dict(circuit_id='A',source_ids=[basis]),dict(circuit_id='B',source_ids=[basis])]
    result=calculate(manifest,req);c=result['connections'][0]
    assert c['circuit_id'] is None and c['circuit_assignment_status']=='AMBIGUOUS'
    assert c['path_ids'] and c['status']=='PARTITIONED_REVIEW'
    req['connections'][0].update(circuit_id='B',circuit_source_ids=[basis],circuit_selection_source_ids=[basis])
    c=calculate(manifest,req)['connections'][0]
    assert c['circuit_id']=='B' and c['status']=='PARTITIONED_REFERENCE'


def test_mutually_exclusive_interpretations_are_not_added_as_simultaneous_routes(tmp_path):
    manifest,req,_,basis=fixture(tmp_path/'源')
    for c in req['connections']:c['alternative_group_id']='CHOICE'
    result=calculate(manifest,req)
    assert all(c['status']=='PARTITIONED_REVIEW'for c in result['connections'])
    assert all(any('互斥'in p for p in c['problems'])for c in result['connections'])
    assert result['ledger']['circuits']==[]


def test_partial_known_conductors_do_not_fill_unknown_shared_total(tmp_path):
    manifest,req,_,basis=fixture(tmp_path/'源')
    req['connections'][0].update(circuit_id='L',circuit_source_ids=[basis])
    req['circuit_specs']=[dict(circuit_id='L',kind='wire',system='照明',spec='匿名导线',parallel_count=3,extras=[],pending_components=[],source_ids=[basis])]
    result=calculate(manifest,req)
    shared=next(a for a in result['physical_segments']if a['shared_drawn_segment'])
    assert shared['known_conductor_quantity_m']==pytest.approx(15)
    assert shared['conductor_quantity_m'] is None


def test_extras_are_counted_once_and_missing_vertical_stays_unknown(tmp_path):
    manifest,req,_,basis=fixture(tmp_path/'源');add_specs(req,basis)
    spec=req['circuit_specs'][0]
    spec['pending_components']=['vertical']
    spec['extras']=[dict(id='END',connection_id='SUPPLY',at='to',length_each_m=.5,length_basis='network_model',
                         basis='匿名实际端头留线依据',basis_ids=[basis],source_ids=[basis])]
    result=calculate(manifest,req);q=result['circuit_quantities'][0]
    assert q['known_length_m']==pytest.approx(43.5) and q['complete_length_m'] is None
    assert q['extra_each_m']==pytest.approx(.5) and q['pending_components']==['vertical']


def test_source_curve_is_remeasured_and_overlay_is_not_a_chord(tmp_path):
    manifest,req,_,_=fixture(tmp_path/'源',curve=True)
    result=calculate(manifest,req)
    assert result['connections'][1]['traced_path_length_m']==pytest.approx(5+2*3.141592653589793)
    arc=next(a for a in result['physical_segments']if len(a['source_points'])>2)
    assert arc['length_m']==pytest.approx(2*3.141592653589793)
    assert max(p[0]for p in arc['source_points'])>5



def test_source_tagged_parameter_boundary_really_splits_and_recalculates(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    req['circuit_specs'][0]['segment_parameters']=[dict(id='PARAM',source_ids=[basis],source_ranges=[dict(
        source_id='CAD-'+h['TRUNK'],part_index=0,fraction_range=[.25,.5])],parallel_count=2,spec='匿名变更规格')]
    result=calculate(manifest,req)
    q=result['circuit_quantities'][0]
    assert q['complete_length_m']==pytest.approx(39.5)
    assert q['parallel_count'] is None and q['parameter_varies']
    trunk=[a for a in result['physical_segments']if a['source_id']=='CAD-'+h['TRUNK']]
    assert [a['fraction_range']for a in trunk]==[[0,.25],[.25,.5],[.5,1]]
    changed=next(p for p in q['segment_parameters']if p['section_ids'])
    assert changed['parallel_count']==2 and changed['spec']=='匿名变更规格'
    assert result['checks']['split_length_conserved']


def test_conflicting_segment_parameters_keep_local_unknown_and_known_counterpart(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    a=dict(id='A',source_ids=[basis],source_ranges=[dict(source_id='CAD-'+h['BRANCH'],part_index=0,fraction_range=[0,1])],parallel_count=2)
    b=dict(a,id='B',parallel_count=4)
    req['circuit_specs'][0]['segment_parameters']=[a,b]
    result=calculate(manifest,req);q=result['circuit_quantities'][0]
    assert q['known_length_m']==pytest.approx(30) and q['complete_length_m'] is None
    assert any('conflicting_segment_parameters'in f for f in q['pending_fields'])
    branch=next(a for a in result['physical_segments']if a['source_id']=='CAD-'+h['BRANCH'])
    assert branch['conductor_quantity_m'] is None
    req['circuit_specs'][0]['segment_parameters'].reverse()
    assert calculate(manifest,req)['circuit_quantities'][0]['known_length_m']==pytest.approx(30)


def test_per_connection_parameters_cannot_silently_choose_a_shared_trunk_count(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    req['circuit_specs'][0]['segment_parameters']=[dict(id='A',connection_ids=['SUPPLY'],parallel_count=3,source_ids=[basis]),
                                                 dict(id='B',connection_ids=['CONTROL'],parallel_count=2,source_ids=[basis])]
    result=calculate(manifest,req);q=result['circuit_quantities'][0]
    assert q['complete_length_m'] is None and q['known_length_m']==pytest.approx(23)
    shared=next(a for a in result['physical_segments']if a['shared_drawn_segment'])
    assert shared['conductor_quantity_m'] is None



def test_unknown_extra_value_and_unassigned_schedule_circuit_stay_null(tmp_path):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    req['circuit_specs'][0]['extras']=[dict(id='E',connection_id='SUPPLY',at='to',length_each_m=None,
        length_basis='network_model',basis='尚待补实值',basis_ids=[basis],source_ids=[basis])]
    req['circuit_specs'].append(dict(req['circuit_specs'][0],circuit_id='UNASSIGNED'))
    result=calculate(manifest,req);q={q['circuit_id']:q for q in result['circuit_quantities']}
    assert q['L-VERIFIED']['known_length_m'] is None and q['L-VERIFIED']['extra_each_m'] is None
    assert 'extras:E:length_each_m'in q['L-VERIFIED']['pending_fields']
    assert q['UNASSIGNED']['known_drawn_path_length_m'] is None and q['UNASSIGNED']['complete_length_m'] is None
    assert all(c['status']=='PARTITIONED_REFERENCE'for c in result['connections'])
    assert result['status']=='REVIEW_REQUIRED'



def parameter_fixture(folder, boundaries):
    manifest,req,h,basis=fixture(folder)
    req['connections']=[dict(req['connections'][0],circuit_id='C',circuit_source_ids=[basis])]
    req['circuit_specs']=[dict(circuit_id='C',kind='wire',system='lighting',spec=None,parallel_count=None,
        extras=[],pending_components=[],source_ids=[basis],segment_parameters=[dict(id='P'+str(i),source_ids=[basis],
            source_ranges=[dict(source_id='CAD-'+h['TRUNK'],part_index=0,fraction_range=span)],spec=spec,parallel_count=count)
            for i,(span,spec,count) in enumerate(boundaries)])]
    return manifest,req,h,basis


@pytest.mark.parametrize('ulp_count',[1,2])
def test_r2_numeric_equivalent_boundaries_share_aliases_for_parameter_applicability(tmp_path,ulp_count):
    right=.5
    for _ in range(ulp_count):right=math.nextafter(right,math.inf)
    manifest,req,h,_=parameter_fixture(tmp_path/'源',[([0,.5],'BV-2.5',3),([right,1],'BV-4',2)])
    original=copy.deepcopy(req);result=calculate(manifest,req)
    trunk=[a for a in result['physical_segments']if a['source_id']=='CAD-'+h['TRUNK']]
    assert [a['fraction_range']for a in trunk]==[[0,.5],[.5,1]]
    q=result['circuit_quantities'][0]
    assert q['pending_fields']==[] and q['complete_length_m']==pytest.approx(25)
    assert [a['segment_parameters'][0]['parallel_count']for a in trunk]==[3,2]
    receipt=result['interval_normalization']
    assert receipt['max_parameter_ulps']==2 and receipt['max_physical_boundary_shift_m']==1e-9
    assert receipt['engineering_connection_tolerance_used'] is False
    assert any(p['original_fraction_range']==[right,1] and p['normalized_fraction_range']==[.5,1]for p in receipt['parameter_ranges'])
    assert req==original


def test_r2_explicit_positive_ten_nanometre_interval_is_preserved(tmp_path):
    lo=.4;hi=.400000001
    manifest,req,h,_=parameter_fixture(tmp_path/'源',[([0,lo],'BV-2.5',3),([lo,hi],'BV-MICRO',4),([hi,1],'BV-4',2)])
    result=calculate(manifest,req)
    trunk=[a for a in result['physical_segments']if a['source_id']=='CAD-'+h['TRUNK']]
    micro=next(a for a in trunk if a['fraction_range']==[lo,hi])
    assert micro['length_m']==pytest.approx(10*(hi-lo),rel=1e-12,abs=0)
    assert micro['length_m']>0 and micro['segment_parameters'][0]['parallel_count']==4
    assert result['circuit_quantities'][0]['pending_fields']==[]
    assert result['interval_normalization']['changes']==[]


def test_r2_explicit_positive_one_ulp_interval_must_not_collapse(tmp_path):
    lo=.5;hi=math.nextafter(lo,math.inf)
    manifest,req,h,_=parameter_fixture(tmp_path/'源',[([0,lo],'BV-2.5',3),([lo,hi],'BV-TINY',4),([hi,1],'BV-4',2)])
    result=calculate(manifest,req)
    micro=next(a for a in result['physical_segments']if a['source_id']=='CAD-'+h['TRUNK']and a['fraction_range']==[lo,hi])
    assert micro['length_m']==10*(hi-lo)>0
    assert micro['segment_parameters'][0]['parallel_count']==4
    assert result['interval_normalization']['changes']==[]


@pytest.mark.parametrize('right',[.500000000001,math.nextafter(math.nextafter(math.nextafter(.5,math.inf),math.inf),math.inf)])
def test_r2_multi_ulp_real_gap_remains_local_unknown(tmp_path,right):
    manifest,req,h,_=parameter_fixture(tmp_path/'源',[([0,.5],'BV-2.5',3),([right,1],'BV-4',2)])
    result=calculate(manifest,req)
    gap=next(a for a in result['physical_segments']if a['source_id']=='CAD-'+h['TRUNK']and a['fraction_range']==[.5,right])
    assert gap['length_m']==10*(right-.5)>0 and gap['conductor_quantity_m'] is None
    assert gap['quantity_status']=='REVIEW_REQUIRED'
    q=result['circuit_quantities'][0]
    assert q['complete_length_m'] is None and q['pending_fields']
    assert result['interval_normalization']['changes']==[]


def test_r2_original_curve_physical_shift_gate_prevents_large_one_ulp_merge():
    right=math.nextafter(.5,math.inf)
    part=dict(kind='line',start=[0,0,0],end=[1e17,0,0])
    aliases,changes=network.binding.normalize_cuts(part,[0,.5,right,1],[(0,.5),(right,1)],1)
    assert aliases[right]==right and changes==[]
    assert network.binding.interval_length(part,.5,right,1)>1e-9


@pytest.mark.parametrize('conflict',[False,True])
def test_r2_real_scene_projects_local_parameters_known_quantity_and_pending_reason(tmp_path,conflict):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    spec=req['circuit_specs'][0];spec.update(spec=None,parallel_count=None)
    spec['segment_parameters']=[dict(id='LEFT',source_ids=[basis],source_ranges=[dict(source_id='CAD-'+h['TRUNK'],part_index=0,fraction_range=[0,.5])],spec='BV-2.5',parallel_count=3),
        dict(id='RIGHT',source_ids=[basis],source_ranges=[dict(source_id='CAD-'+h['TRUNK'],part_index=0,fraction_range=[.5,1])],spec='BV-4',parallel_count=2),
        dict(id='BRANCH',source_ids=[basis],source_ranges=[dict(source_id='CAD-'+h['BRANCH'],part_index=0,fraction_range=[0,1])],spec='BV-2.5',parallel_count=3)]
    if conflict:
        spec['segment_parameters'].append(dict(spec['segment_parameters'][1],id='RIGHT-CONFLICT',spec='BV-6',parallel_count=4))
        spec['pending_components']=['vertical']
    rp=tmp_path/'请求.json';rp.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'结果'
    done=run([SCRIPTS/'partition_electrical_route_network.py','--cad-manifest',manifest,'--request',rp,'--output-dir',out,'--build-review'])
    assert done.returncode==(3 if conflict else 0),done.stdout+done.stderr
    result=json.loads((out/'混连网络拆分结果.json').read_text(encoding='utf-8'))
    view=json.loads((out/'复核视图.json').read_text(encoding='utf-8'))
    assert [r['exit_code']for r in json.loads((out/'调用回执.json').read_text(encoding='utf-8'))]==[0,0,0]
    right=next(a for a in result['physical_segments']if a['source_id']=='CAD-'+h['TRUNK']and a['fraction_range']==[.5,1])
    obj=next(o for o in view['objects']if o['catalog_key']=='电气混连原段|'+right['id'])
    assert 'segment_parameters'in obj['attributes'] and 'known_conductor_quantity_m'in obj['attributes']
    assert 'pending_fields'in obj['attributes'] and 'pending_components'in obj['attributes']
    detail=json.dumps(obj,ensure_ascii=False)
    if conflict:
        assert obj['status']=='REVIEW_REQUIRED' and '互相冲突'in detail and 'vertical'in detail
        assert right['segment_parameters'][0]['spec'] is None and right['segment_parameters'][0]['parallel_count'] is None
        assert right['conductor_quantity_m'] is None and obj['attributes']['conductor_quantity_m'] is None
        assert obj['attributes']['segment_parameters'][0]['spec'] is None
    else:
        assert obj['status']=='PARTITIONED_REFERENCE' and 'BV-4'in detail
        assert right['segment_parameters'][0]['parallel_count']==2
        assert right['known_conductor_quantity_m']==pytest.approx(10)
        assert obj['attributes']['segment_parameters'][0]['parallel_count']==2
    assert result['official_adoption'] is False



@pytest.mark.parametrize('local_unknown',[False,True])
def test_r3_final_view_html_and_check_report_share_official_fingerprint(tmp_path,local_unknown):
    manifest,req,h,basis=fixture(tmp_path/'源');add_specs(req,basis)
    if local_unknown:
        req['circuit_specs'][0]['segment_parameters']=[dict(id='UNKNOWN',source_ids=[basis],source_ranges=[dict(
            source_id='CAD-'+h['BRANCH'],part_index=0,fraction_range=[0,1])],spec=None,parallel_count=None)]
    request=tmp_path/'请求.json';request.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8')
    output=tmp_path/'结果'
    done=run([SCRIPTS/'partition_electrical_route_network.py','--cad-manifest',manifest,'--request',request,'--output-dir',output,'--build-review'])
    assert done.returncode==(3 if local_unknown else 0),done.stdout+done.stderr
    result=json.loads((output/'混连网络拆分结果.json').read_text(encoding='utf-8'))
    view=json.loads((output/'复核视图.json').read_text(encoding='utf-8'))
    report=json.loads((output/'场景检查.json').read_text(encoding='utf-8'))
    original=json.loads((output/'场景生成原检查.json').read_text(encoding='utf-8'))
    check=report['projection_check']
    official=network.review_contract.normalize_view(view,output)['result_fingerprint']
    assert report['status']==check['status']=='PASS' and check['identity_closed'] is True
    assert view['result_fingerprint']==report['result_fingerprint']==check['after_result_fingerprint']==official
    assert original['result_fingerprint']==check['before_result_fingerprint']!=official
    assert original['status']=='PASS' and 'projection_check'not in original
    assert hashlib.sha256((output/'场景生成原检查.json').read_bytes()).hexdigest()==check['original_report']['sha256']
    assert [s['stage']for s in check['steps']]==['scene_builder_checks','typed_atom_attributes_projection','shared_post_projection_normalization','html_shared_identity_check']
    html=(output/'电气混连网络复核.html').read_text(encoding='utf-8')
    payload=re.search(r'<script\s+id="payload"[^>]*>([\s\S]*?)</script>',html)[1]
    embedded=network.review_contract.unpack_payload(gzip.decompress(base64.b64decode(payload)))
    assert embedded['result_fingerprint']==check['html_result_fingerprint']==check['html_normalized_result_fingerprint']==official
    assert network.review_contract.normalize_view(embedded,output)['result_fingerprint']==official
    q=result['circuit_quantities'][0]
    assert q['known_length_m']==pytest.approx(30 if local_unknown else 42)
    assert q['complete_length_m'] is None if local_unknown else q['complete_length_m']==pytest.approx(42)
    assert result['official_adoption'] is False
    assert [r['exit_code']for r in json.loads((output/'调用回执.json').read_text(encoding='utf-8'))]==[0,0,0]
