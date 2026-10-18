"""Generic capability enhancements, synthetic DXF -> neutral CAD -> candidate interfaces.

Eight original circuit-table counterexample classes remain separate assertions.
These checks do not accept real DWG quantities or project-specific rules.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'skills/qto-electrical-routes/scripts'
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'


def doc():
    d=ezdxf.new('R2013');d.units=6
    for layer in ('NOTE','TABLE','CENTER','EDGE','LEADER','DEVICE'):
        d.layers.add(layer)
    block=d.blocks.new('DEV');block.add_circle((0,0),.1);block.add_point((0,0,0))
    return d


def text(space,value,x,y,layer='TABLE'):
    return 'CAD-'+space.add_text(value,dxfattribs=dict(insert=(x,y),height=.2,layer=layer)).dxf.handle


def extract(tmp,d):
    tmp.mkdir(parents=True,exist_ok=True)
    source=tmp/'synthetic.dxf';d.saveas(source)
    p=subprocess.run([sys.executable,'-B',str(SHARED/'extract_cad_neutral.py'),'--source-dxf',str(source),'--output-dir',str(tmp/'neutral')],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert p.returncode==0,p.stdout+p.stderr
    return tmp/'neutral/CAD中性取证清单.json'


def base(schema):
    return dict(schema=schema,layout='Model',unit=dict(factor_to_m=1.0,basis='synthetic DXF INSUNITS=6, 1 unit=m'),opaque_blocks=['DEV'])


def run(tmp,script,manifest,request,name='result'):
    tmp.mkdir(parents=True,exist_ok=True)
    rp=tmp/(name+'_request.json');rp.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
    out=tmp/(name+'.json')
    p=subprocess.run([sys.executable,'-B',str(SCRIPTS/script),'--cad-manifest',str(manifest),'--request',str(rp),'--output',str(out)],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=30)
    (tmp/(name+'_cli_receipt.json')).write_text(json.dumps(dict(script=script,true_exit=p.returncode,stdout=p.stdout,stderr=p.stderr,
        request=str(rp),request_sha256=hashlib.sha256(rp.read_bytes()).hexdigest(),output=str(out)if out.exists()else None),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    result=json.loads(out.read_text(encoding='utf-8')) if out.exists()else json.loads(p.stdout)
    return p.returncode,result,out


def bound(path):
    return dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def problems(result):
    return [x['problem']for x in result['todo']]


@pytest.fixture
def clauses(tmp_path):
    d=doc();m=d.modelspace()
    scope=text(m,'本图适用于B1电气系统一期R1版本',0,60,'NOTE')
    ids=[text(m,'（一）首灯前采用电缆，',0,40,'NOTE'),text(m,'首灯后改换电线，',0,38,'NOTE'),text(m,'除注明外，二期预留另列。',0,36,'NOTE'),
         text(m,'（二）设备自带控制柜除外。',30,40,'NOTE')]
    manifest=extract(tmp_path/'clauses_cad',d)
    request=base('qto-design-clause-request/1.0')
    target=dict(building='B1',system='electric',phase='phase1',revision='R1')
    request['regions']=[dict(id='NOTES',bounds_m=[-1,0,50,50],text_layers='^NOTE$',columns_m=[[-1,20],[20,50]],
                            applicability=dict(basis_handles=[scope],targets=[target]))]
    request['packages']=[dict(id='A',scope=target),dict(id='B',scope=target)]
    code,result,path=run(tmp_path,'extract_design_clauses.py',manifest,request,'clauses')
    return manifest,request,result,path,ids,scope


def test_t38_reads_neutral_wrapped_columns_exceptions_and_package_difference(clauses,tmp_path):
    manifest,req,result,path,ids,scope=clauses
    assert len(result['clauses'])==2
    first=result['clauses'][0]
    assert first['source_ids']==ids[:3]
    assert '首灯后改换电线' in first['text'] and '除注明外，二期预留另列' in first['text']
    assert first['contains_exception'] and result['coverage']['uncovered_source_ids']==[]
    assert first['scope_source_texts'][0]['source_id']==scope
    assert '适用条款未一致落实/待解释' in problems(result)
    req['executions']=[dict(package_id=p,clause_id=c['id'],disposition='applied',basis='本轮同源条款已实施')for c in result['clauses']for p in ('A','B')]
    code,good,_=run(tmp_path,'extract_design_clauses.py',manifest,req,'clauses_applied')
    assert code==0 and good['status']=='CANDIDATE_ONLY' and good['provenance']['stable']
    req['executions'][1].update(disposition='not_applicable',basis='与显式范围矛盾，留核')
    code,bad,_=run(tmp_path,'extract_design_clauses.py',manifest,req,'clauses_diff')
    assert code==3 and '共用条款多包执行差异' in problems(bad)


def test_t38_scope_missing_source_and_overlapping_columns_rejected(clauses,tmp_path):
    manifest,req,*_=clauses
    req=copy.deepcopy(req);req['regions'][0]['applicability']['basis_handles']=['CAD-NOT-THERE']
    code,result,_=run(tmp_path,'extract_design_clauses.py',manifest,req,'bad_scope')
    assert code==2 and result['status']=='INPUT_REQUIRED'
    req=copy.deepcopy(clauses[1]);req['regions'][0]['columns_m']=[[-1,30],[20,50]]
    code,result,_=run(tmp_path,'extract_design_clauses.py',manifest,req,'bad_columns')
    assert code==2


@pytest.mark.parametrize('declaration,expected_exit',[('applied',3),('not_applicable',0),('pending',3)])
def test_t38_every_execution_is_reconciled_even_outside_scope(clauses,tmp_path,declaration,expected_exit):
    manifest,request,first,*_=clauses;request=copy.deepcopy(request)
    scope=dict(building='OTHER',system='electric',phase='phase1',revision='R1')
    request['packages'].append(dict(id='OUTSIDE',scope=scope))
    request['executions']=[dict(package_id=p,clause_id=c['id'],disposition='applied',basis='本包显式适用')for c in first['clauses']for p in ('A','B')]
    request['executions'].append(dict(package_id='OUTSIDE',clause_id=first['clauses'][1]['id'],disposition=declaration,basis='原句范围不包含OTHER楼栋'))
    code,result,_=run(tmp_path,'extract_design_clauses.py',manifest,request,'outside_'+declaration)
    assert code==expected_exit
    outside=[x for x in result['package_checks']if x['package_id']=='OUTSIDE']
    assert len(outside)==1 and outside[0]['applicability']=='OUT_OF_SCOPE'
    assert outside[0]['execution']['basis']=='原句范围不包含OTHER楼栋'
    assert outside[0]['scope_source_texts'] and outside[0]['package_scope']==scope
    if declaration=='applied':assert '范围外条款被执行'in problems(result)
    else:assert '范围外条款被执行'not in problems(result)


@pytest.fixture
def tables(tmp_path):
    d=doc();m=d.modelspace();handles={}
    def table(x,box,codes):
        handles[box+'_title']=text(m,box,x,24)
        handles[box+'_header']=text(m,'回路编号',x,20)
        for name,y in [('电缆',18),('设备名称',16),('根数',14),('引自',12),('状态',10),('敷设方式',8),('控制编号',6)]:text(m,name,x,y)
        for i,code in enumerate(codes):
            xx=x+5+5*i
            handles[box+'_'+code]=text(m,code,xx,20)
            handles[box+'_spec_'+code]=text(m,'YJV-3x2.5',xx,18)
            text(m,'MOTOR-1' if i==0 else '预留',xx,16)
            text(m,'2',xx,14);text(m,'TOP-A',xx,12)
            text(m,'正常' if i==0 else '二期',xx,10);text(m,'CT',xx,8)
            text(m,'n1a' if i==0 else 'n2a',xx,6)
        for xx in (x+3,x+8,x+13):m.add_line((xx,4),(xx,21),dxfattribs=dict(layer='TABLE'))
    table(0,'BOX-1',['WP1','WP2']);table(20,'BOX-2',['WP1'])
    handles['omission']=text(m,'~',15,20)
    handles['empty_header']=text(m,'回路编号',40,20)
    handles['outside_spec']=text(m,'KVV-7x1.5',50,5)
    for key,x in [('dev1',5),('dev2',6)]:
        handles[key]='CAD-'+m.add_blockref('DEV',(x,30),dxfattribs=dict(layer='DEVICE')).dxf.handle
    handles['dev_name1']=text(m,'MOTOR-1',5,30)
    handles['dev_name2']=text(m,'MOTOR-1',6,30)
    handles['control_name1']=text(m,'n1a',5,31)
    handles['control_name2']=text(m,'n1a',6,31)
    handles['orphan']='CAD-'+m.add_blockref('DEV',(7,30),dxfattribs=dict(layer='DEVICE')).dxf.handle
    handles['orphan_name']=text(m,'n9a',7,31)
    manifest=extract(tmp_path/'tables_cad',d)
    request=base('qto-electrical-circuit-request/1.0')
    request.update(bounds_m=[-1,0,60,35],scope_id='B1-F1',text_layers='^TABLE$',table_line_layers='^TABLE$',
        row_tolerance_m=.25,table_extent_m=dict(left=.5,right=25,up=5,down=17),circuit_pattern=r'WP\d+',
        spec_pattern=r'(?:YJV|KVV)-\S+',box_title_pattern=r'BOX-\d+',expected_boxes=['BOX-1','BOX-2','BOX-MISSING'])
    code,result,path=run(tmp_path,'extract_electrical_circuits.py',manifest,request,'circuits')
    return manifest,request,result,path,handles


def test_t32_original_1_header_coverage_and_2_omission_not_expanded(tables):
    result=tables[2]
    assert result['coverage']['found_headers']==3 and result['coverage']['parsed_headers']==2
    assert len(result['circuits'])==3
    assert '省略列不自动展开'in problems(result)
    assert '找到表头但未解析回路列'in problems(result)


def test_t32_original_3_parallel_tables_clipped_and_row_source(tables):
    result=tables[2];h=tables[4]
    a=next(t for t in result['tables']if t['box']=='BOX-1')
    assert a['bounds_m'][2]==20
    assert len(a['circuit_ids'])==2
    assert all(h['BOX-2_spec_WP1']not in r['source_ids']for r in result['circuits']if r['source_box']=='BOX-1')
    assert all(r['field_sources']['spec']and r['field_binding_status']=='table_cell_candidate_requires_review'for r in result['circuits'])
    assert all(r['fields']['control_code']in ('n1a','n2a')for r in result['circuits'])


def test_t32_original_4_state_and_6_reserve_never_becomes_endpoint(tables):
    r=next(r for r in tables[2]['circuits']if r['circuit_code']=='WP2')
    assert set(r['state'])=={'phase2','reserved'}
    assert r['endpoint_label']is None and r['chosen_endpoint']is None
    assert '预留/备用无实体终点'in r['problems']


def test_t32_original_5_feeder_conflict_7_uncovered_outgoing_8_parent_coverage(tables,tmp_path):
    manifest,req,result,_,h=tables
    req=copy.deepcopy(req)
    req['feeder_rows']=[dict(box='BOX-1',circuit_code='WP1',upstream='TOP-B',destination='OTHER',source_ids=[h['outside_spec']]),
                        dict(box='UPS-OUT',circuit_code='WP9',upstream='UPS',destination='WEAK',source_ids=[h['outside_spec']])]
    code,r,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'feeder_conflicts')
    assert code==3
    assert len(next(x for x in r['circuits']if x['source_box']=='BOX-1'and x['circuit_code']=='WP1')['upstream_conflicts'])==2
    assert '干线出线未入回路台账'in problems(r)
    assert r['coverage']['missing_boxes']==['BOX-MISSING']
    assert h['outside_spec']in {x['source_id']for x in r['coverage']['unassigned_spec_texts']}


def test_t32_duplicate_endpoint_labels_do_not_pick_first_and_na_binding(tables,tmp_path):
    manifest,req,result,_,h=tables;req=copy.deepcopy(req)
    req['endpoint_candidates']=[dict(id=x,label='MOTOR-1',physical_source_id=h[x],label_source_ids=[h['dev_name'+str(i)]],scope_id='B1-F1',binding_basis='明确实例旁注核对')for i,x in enumerate(('dev1','dev2'),1)]
    code,r,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'duplicate_ends')
    row=next(x for x in r['circuits']if x['source_box']=='BOX-1'and x['circuit_code']=='WP1')
    assert row['endpoint_candidates']==['dev1','dev2']and row['chosen_endpoint']is None
    req['confirmed_bindings']=[dict(circuit_id=row['id'],endpoint_id='dev2',basis_source_ids=[h['dev_name2']])]
    code,r,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'confirmed_end')
    assert next(x for x in r['circuits']if x['id']==row['id'])['chosen_endpoint']=='dev2'


def tray_request():
    r=base('qto-cable-tray-request/1.0')
    r.update(bounds_m=[-1,-1,30,30],center_layers='^CENTER$',edge_layers='^EDGE$',pair_tolerance_m=.01,
             parallel_angle_deg=.1,geometry_z_tolerance_m=.001,width_range_m=[.1,2.0],annotation_layers='^NOTE$',
             annotation_pattern=r'(?P<spec>TRAY-(?P<width>\d+)x\d+)',annotation_radius_m=5,
             annotation_width_factor_m=.001,leader_layers='^LEADER$',leader_text_tolerance_m=.05)
    return r


def add_tray(m,a,b,width=.4,label_width=400):
    sid='CAD-'+m.add_line(a,b,dxfattribs=dict(layer='CENTER')).dxf.handle
    v=[b[i]-a[i]for i in range(2)];length=math.hypot(*v);n=[-v[1]/length,v[0]/length]
    for sign in (-1,1):m.add_line(tuple(a[i]+sign*n[i]*width/2 for i in range(2))+(a[2]if len(a)>2 else 0,),tuple(b[i]+sign*n[i]*width/2 for i in range(2))+(b[2]if len(b)>2 else 0,),dxfattribs=dict(layer='EDGE'))
    mid=tuple((a[i]+b[i])/2 for i in range(2))+(((a[2]+b[2])/2)if len(a)>2 else 0,);note=(mid[0]+n[0],mid[1]+n[1],mid[2])
    label=text(m,f'TRAY-{label_width}x100',note[0],note[1],'NOTE')
    m.add_leader([mid,note],dxfattribs=dict(layer='LEADER'))
    return sid,label


import math


def test_t33_real_neutral_geometry_width_conflict_and_native_leader(tmp_path):
    d=doc();m=d.modelspace();add_tray(m,(0,0),(10,0),.4,600)
    manifest=extract(tmp_path/'cad',d);r=tray_request()
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,r,'tray')
    assert code==3 and len(result['segments'])==1
    s=result['segments'][0]
    assert s['width_geometry_m']==pytest.approx(.4)and s['width_annotation_m']==pytest.approx(.6)
    assert s['annotation_binding']['via']=='native_leader'
    assert '量得宽度与标注宽度冲突'in s['problems']and s['elevation_m']is None


def test_t33_edge_pair_blocks_and_multi_pair_not_arbitrarily_selected(tmp_path):
    d=doc();m=d.modelspace();b=d.blocks.new('TRAYS')
    for y in (0,.4,.8):b.add_line((0,y),(10,y),dxfattribs=dict(layer='EDGE'))
    m.add_blockref('TRAYS',(1,1));manifest=extract(tmp_path/'cad',d);r=tray_request();r.pop('center_layers');r['expand_blocks']=['TRAYS']
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,r,'edges_ambiguous')
    assert code==3 and result['segments']==[] and result['coverage']['unpaired_edge_parts']==3
    assert any('/TRAYS/'in t.get('source_id','')for t in result['todo'])


def test_t33_route_shared_physical_tray_once_each_cable_independent_and_ledger_roundtrip(tables,tmp_path):
    circuits_path=tables[3]
    d=doc();m=d.modelspace();s1,l1=add_tray(m,(0,0),(10,0));s2,l2=add_tray(m,(10,0),(20,0))
    zref=text(m,'桥架标高3m',0,29,'NOTE')
    instances=['CAD-'+m.add_blockref('DEV',p,dxfattribs=dict(layer='DEVICE')).dxf.handle for p in [(0,0,3),(20,0,3),(28,20,3)]]
    point_handle=list(d.blocks['DEV'].query('POINT'))[0].dxf.handle
    manifest=extract(tmp_path/'traycad',d);r=tray_request();r['expand_blocks']=['DEV']
    code,trays,tray_path=run(tmp_path,'recognize_cable_trays.py',manifest,r,'network_trays')
    confirmations=[dict(segment_id=s['id'],physical_id='PH'+str(i),width_m=.4,spec='TRAY-400x100',elevation_mode='drawing_elevations',start_elevation_m=3,end_elevation_m=3,
        start_elevation_source_ids=[zref],end_elevation_source_ids=[zref],datum='DATUM',basis='本图明确标高和物理段确认',basis_source_ids=s['source_ids'],resolved_problems={p:'本图标高依据补齐'for p in s['problems']})for i,s in enumerate(trays['segments'])]
    req=base('qto-circuit-on-tray-request/1.0');req['expand_blocks']=['DEV'];req.update(project_id='SYN',scope_id='B1-F1',tray_candidates=bound(tray_path),circuit_candidates=bound(circuits_path),
        snap_tolerance_m=.001,endpoint_tolerance_m=.01,equal_length_tolerance_m=.001,tray_confirmations=confirmations)
    normal=[c for c in tables[2]['circuits']if c['state']==['normal_candidate']]
    def port(i):
        source=instances[i]
        return dict(physical_id=source,physical_source_id=source,port_source_id=source+'/DEV/CAD-'+point_handle,
                    instance_path=source,manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),locator='point',basis='本实例内连接POINT',basis_source_ids=[source])
    req['circuit_bindings']=[dict(circuit_id=c['id'],system='power',basis='本轮原图端点和回路对应',basis_source_ids=[s1],
                                 **{'from':port(0),'to':port(1)})for i,c in enumerate(normal)]
    code,result,path=run(tmp_path,'route_circuits_on_trays.py',manifest,req,'network_routes')
    assert code==3 and len(result['circuit_items'])==2
    assert result['physical_tray_length_m']==pytest.approx(20)
    assert result['cable_traversal_length_m']==pytest.approx(80)
    assert all(len(v)==2 for v in result['shared_path_usage'].values())
    assert all(c['length_basis']=='network_model'and not c['complete_quantity']for c in result['circuit_items'])
    ledger_path=tmp_path/'roundtrip_ledger.json';ledger_path.write_text(json.dumps(result['path_ledger'],ensure_ascii=False),encoding='utf-8')
    p=subprocess.run([sys.executable,'-B',str(SCRIPTS/'calculate_electrical_paths.py'),str(ledger_path)],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert p.returncode==0,p.stdout+p.stderr
    assert json.loads(p.stdout)['business_adoption_allowed']is False
    req['circuit_bindings'][0]['to']=port(2)
    code,off,_=run(tmp_path,'route_circuits_on_trays.py',manifest,req,'off_network')
    assert code==3 and '端点离网，未退直角距离'in problems(off)


def test_neutral_hash_tamper_rejected(tables,tmp_path):
    manifest,req,*_=tables
    value=json.loads(manifest.read_text(encoding='utf-8'))
    artifact=value['artifacts']['layout_entities'];artifact['sha256']='0'*64
    changed=manifest.parent/'changed_manifest.json';changed.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    code,result,_=run(tmp_path,'extract_electrical_circuits.py',changed,req,'bad_neutral')
    assert code==2 and result['status']=='INPUT_REQUIRED'


def test_t32_shifted_control_spec_follows_native_leader_not_nearest_column(tmp_path):
    d=doc();m=d.modelspace()
    text(m,'BOX-1',0,24);text(m,'回路编号',0,20)
    text(m,'WP1',5,20);text(m,'WP2',10,20);text(m,'电缆',0,18);text(m,'根数',0,14)
    shifted=text(m,'KVV-7x1.5',10,18);normal=text(m,'YJV-3x2.5',11,18)
    text(m,'1',5,14);text(m,'1',10,14)
    m.add_leader([(5,18),(10,18)],dxfattribs=dict(layer='LEADER'))
    manifest=extract(tmp_path/'cad',d)
    req=base('qto-electrical-circuit-request/1.0');req.update(bounds_m=[-1,0,20,30],text_layers='^TABLE$',table_line_layers='^TABLE$',
        row_tolerance_m=.2,table_extent_m=dict(left=.5,right=20,up=5,down=15),circuit_pattern=r'WP\d+',
        spec_pattern=r'(?:YJV|KVV)-\S+',box_title_pattern=r'BOX-\d+',leader_layers='^LEADER$',leader_text_tolerance_m=.1)
    code,r,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'shifted')
    a,b=sorted(r['circuits'],key=lambda x:x['circuit_code'])
    assert a['fields']['spec']=='KVV-7x1.5'and b['fields']['spec']=='YJV-3x2.5'
    assert a['field_sources']['spec']==[shifted]and b['field_sources']['spec']==[normal]
    assert a['field_bindings']['spec'][0]['via']=='native_leader'


def test_text_unitless_can_read_but_tray_measurement_requires_unit(tmp_path):
    d=doc();d.units=0;m=d.modelspace();sid=text(m,'本图一期B1电气R1',0,10,'NOTE');text(m,'（一）首灯后换线，',0,5,'NOTE');text(m,'但设备自带者除外。',0,4,'NOTE')
    m.add_line((0,0),(10,0),dxfattribs=dict(layer='CENTER'))
    manifest=extract(tmp_path/'cad',d)
    r=dict(schema='qto-design-clause-request/1.0',layout='Model',unit=dict(mode='cad_coordinates',basis='INSUNITS=0，仅原始CAD文字坐标，不建立工程长度'),
           regions=[dict(id='N',bounds_cad=[-1,0,20,8],columns_cad=[[-1,20]],text_layers='^NOTE$',applicability=dict(basis_handles=[sid],targets=[dict(building='B1',system='electric',phase='phase1',revision='R1')]))])
    code,out,_=run(tmp_path,'extract_design_clauses.py',manifest,r,'unitless_text')
    assert code==0 and out['provenance']['coordinate_unit']=='cad_unitless'and not out['provenance']['measured_length_allowed']
    assert '但设备自带者除外'in out['clauses'][0]['text']and 'point_cad'in out['clauses'][0]['source_texts'][0]
    code,bad,_=run(tmp_path,'recognize_cable_trays.py',manifest,tray_request(),'unitless_tray')
    assert code==2 and bad['status']=='INPUT_REQUIRED'


def network_case(tmp,tables,lines,ports):
    d=doc();m=d.modelspace();sources=[]
    for a,b in lines:
        if math.dist(a[:2],b[:2])==0:
            sources.append('CAD-'+m.add_line(a,b,dxfattribs=dict(layer='CENTER')).dxf.handle)
        else:sources.append(add_tray(m,a,b)[0])
    instances=['CAD-'+m.add_blockref('DEV',p,dxfattribs=dict(layer='DEVICE')).dxf.handle for p in ports]
    point_handle=list(d.blocks['DEV'].query('POINT'))[0].dxf.handle
    manifest=extract(tmp/'networkcad',d);r=tray_request();r['expand_blocks']=['DEV']
    _,trays,tray_path=run(tmp,'recognize_cable_trays.py',manifest,r,'trays')
    req=base('qto-circuit-on-tray-request/1.0');req['expand_blocks']=['DEV']
    req.update(project_id='SYN',scope_id='B1-F1',tray_candidates=bound(tray_path),circuit_candidates=bound(tables[3]),snap_tolerance_m=.001,endpoint_tolerance_m=.01,equal_length_tolerance_m=.001)
    req['tray_confirmations']=[dict(segment_id=s['id'],physical_id='P'+str(i),width_m=.4,spec='TRAY-400x100',elevation_mode='wcs_geometry',datum='WCS-verified',basis='三维WCS及物理段已核',basis_source_ids=s['source_ids'],resolved_problems={p:'本轮三维路径、规格来源另行逐项核对'for p in s['problems']})for i,s in enumerate(trays['segments'])]
    def port(i):
        source=instances[i]
        return dict(physical_id=source,physical_source_id=source,port_source_id=source+'/DEV/CAD-'+point_handle,instance_path=source,
                    manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),locator='point',basis='实例内已识别连接点',basis_source_ids=[source])
    circuit=next(c for c in tables[2]['circuits']if c['state']==['normal_candidate'])
    req['circuit_bindings']=[dict(circuit_id=circuit['id'],system='power',basis='已核系统图-端点',basis_source_ids=[sources[0]],**{'from':port(0),'to':port(1)})]
    return manifest,req,port


@pytest.mark.parametrize('kind',['disconnected','equal','vertical'])
def test_t33_disconnect_equal_and_explicit_vertical_not_flattened(tables,tmp_path,kind):
    if kind=='equal':
        lines=[((0,0,3),(10,0,3)),((10,0,3),(10,10,3)),((0,0,3),(0,10,3)),((0,10,3),(10,10,3))];ports=[(0,0,3),(10,10,3)]
    elif kind=='disconnected':
        lines=[((0,0,3),(5,0,3)),((10,0,3),(20,0,3))];ports=[(0,0,3),(20,0,3)]
    else:
        lines=[((0,0,3),(10,0,3)),((10,0,3),(10,0,6))];ports=[(0,0,3),(10,0,6)]
    manifest,req,_=network_case(tmp_path,tables,lines,ports)
    code,result,_=run(tmp_path,'route_circuits_on_trays.py',manifest,req,'routed')
    assert code==3
    if kind=='equal':assert '多条等长路径，不默认挑选'in problems(result)and not result['circuit_items']
    elif kind=='disconnected':assert '桥架网络断开，未退直角距离'in problems(result)and not result['circuit_items']
    else:
        assert result['circuit_items'][0]['path_length_each_m']==pytest.approx(13)
        vertical=[p for p in result['path_ledger']['paths']if p['role']=='vertical']
        assert len(vertical)==1 and vertical[0]['length_m']==pytest.approx(3)


@pytest.mark.parametrize('mutation',['different_source','different_instance','invented_coordinate','wrong_identity','outline_instead_of_port'])
def test_t33_port_identity_coordinate_and_qualification(tables,tmp_path,mutation):
    manifest,req,port=network_case(tmp_path,tables,[((0,0,3),(10,0,3))],[(0,0,3),(10,0,3)])
    target=req['circuit_bindings'][0]['from']
    if mutation=='different_source':target['manifest_sha256']='0'*64
    elif mutation=='different_instance':target['port_source_id']=port(1)['port_source_id']
    elif mutation=='invented_coordinate':target['point_m']=[1,1,3]
    elif mutation=='wrong_identity':target['physical_id']='another-object'
    else:target['port_source_id']=target['physical_source_id'];target['locator']='point'
    code,result,_=run(tmp_path,'route_circuits_on_trays.py',manifest,req,'invalid_port')
    assert code==2 and result['status']=='INPUT_REQUIRED'


def test_t38_to_t32_real_cli_scope_binding(clauses,tables,tmp_path):
    manifest,req,*_=tables;req=copy.deepcopy(req)
    req['design_clauses']=bound(clauses[3]);req['effective_scope']=dict(building='B1',system='electric',phase='phase1',revision='R1')
    req['scope_basis_source_ids']=[tables[4]['BOX-1_header']]
    code,result,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'design_to_circuit')
    assert code==3 and len(result['applicable_clause_ids'])==2
    assert all(c['applicable_clause_ids']==result['applicable_clause_ids']for c in result['circuits'])


def test_t33_real_branch_is_split_and_trunk_fraction_is_replayable(tables,tmp_path):
    manifest,req,_=network_case(tmp_path,tables,[((0,0,3),(20,0,3)),((10,0,3),(10,10,3))],[(0,0,3),(10,10,3)])
    code,result,_=run(tmp_path,'route_circuits_on_trays.py',manifest,req,'branched')
    assert code==3 and result['physical_tray_length_m']==pytest.approx(30)
    assert result['circuit_items'][0]['path_length_each_m']==pytest.approx(20)
    assert len(result['path_ledger']['paths'])==3
    assert len(result['circuit_items'][0]['path_ids'])==2
    assert any(':0:0.5' in p for p in result['circuit_items'][0]['path_ids'])


def test_t33_instance_attributes_not_attdef_defaults(tmp_path):
    d=doc();b=d.blocks.new('TRAY_BLOCK')
    b.add_line((0,0),(10,0),dxfattribs=dict(layer='CENTER'))
    b.add_line((0,-.2),(10,-.2),dxfattribs=dict(layer='EDGE'));b.add_line((0,.2),(10,.2),dxfattribs=dict(layer='EDGE'))
    b.add_attdef('SPEC',(0,0),text='TRAY-400x100')
    ins=d.modelspace().add_blockref('TRAY_BLOCK',(0,0));ins.add_auto_attribs(dict(SPEC='TRAY-600x100'))
    manifest=extract(tmp_path/'cad',d);r=tray_request();r['expand_blocks']=['TRAY_BLOCK'];r['attribute_tags']={'spec':'SPEC'}
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,r,'attributes')
    assert code==3;segment=result['segments'][0]
    attr=segment['block_attribute_evidence'][0]
    assert attr['instance_values']['SPEC']=='TRAY-600x100'
    assert attr['definition_defaults'][0]['text']=='TRAY-400x100'
    assert segment['spec']=='TRAY-600x100' and '量得宽度与标注宽度冲突'in segment['problems']


def test_t33_line_leader_chain_has_source_and_direction_constraints(tmp_path):
    d=doc();m=d.modelspace()
    m.add_line((0,0),(10,0),dxfattribs=dict(layer='CENTER'))
    for y in (-.2,.2):m.add_line((0,y),(10,y),dxfattribs=dict(layer='EDGE'))
    label=text(m,'TRAY-400x100',5,2,'NOTE')
    leader_ids=['CAD-'+m.add_line(a,b,dxfattribs=dict(layer='LEADER')).dxf.handle for a,b in [((5,0),(6,1)),((6,1),(5,2))]]
    manifest=extract(tmp_path/'cad',d);r=tray_request()
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,r,'line_leader')
    segment=result['segments'][0]
    assert segment['annotation_source_id']==label and segment['annotation_binding']['via']=='line_chain_candidate'
    assert set(segment['annotation_binding']['basis_source_ids'])==set(leader_ids)


def test_t32_na_control_link_is_namespace_checked_and_separate_from_power_counts(tables,tmp_path):
    manifest,req,_,_,h=tables;req=copy.deepcopy(req)
    req['endpoint_candidates']=[dict(id=x,label='n1a',physical_source_id=h[x],label_source_ids=[h['control_name'+str(i)]],
        scope_id='B1-F1',source_box='BOX-1',binding_basis='本箱控制回路及按钮旁注已核')for i,x in enumerate(('dev1','dev2'),1)]
    code,result,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'na_candidates')
    row=next(r for r in result['circuits']if r['source_box']=='BOX-1'and r['circuit_code']=='WP1')
    control=row['control_links'][0]
    assert control['control_code']=='n1a'and control['endpoint_candidates']==['dev1','dev2']and control['chosen_endpoint']is None
    assert control['source_ids']==row['field_sources']['control_code']
    assert control['parallel_count']is None and control['spec']is None and row['parallel_count']==2
    other=next(r for r in result['circuits']if r['source_box']=='BOX-2')
    assert other['control_links'][0]['endpoint_candidates']==[]
    req['confirmed_bindings']=[dict(circuit_id=row['id'],control_endpoint_id='dev2',basis_source_ids=[h['control_name2']])]
    code,result,_=run(tmp_path,'extract_electrical_circuits.py',manifest,req,'na_confirmed')
    assert next(r for r in result['circuits']if r['id']==row['id'])['control_links'][0]['chosen_endpoint']=='dev2'


def test_t32_physical_endpoint_difference_separates_selected_candidate_excluded_and_unreferenced(tables,tmp_path):
    manifest,request,_,_,h=tables;request=copy.deepcopy(request)
    request['endpoint_candidates']=[dict(id=x,label='n1a',physical_source_id=h[x],label_source_ids=[h['control_name'+str(i)]],
        scope_id='B1-F1',source_box='BOX-1',binding_basis='同源控制实例')for i,x in enumerate(('dev1','dev2'),1)]
    request['endpoint_candidates'].append(dict(id='ORPHAN',label='n9a',physical_source_id=h['orphan'],label_source_ids=[h['orphan_name']],
        scope_id='B1-F1',source_box='BOX-1',binding_basis='同源额外物理实例，未在系统图控制列中出现'))
    _,first,_=run(tmp_path,'extract_electrical_circuits.py',manifest,request,'endpoint_difference_candidates')
    row=next(r for r in first['circuits']if r['source_box']=='BOX-1'and r['circuit_code']=='WP1')
    request['confirmed_bindings']=[dict(circuit_id=row['id'],control_endpoint_id='dev1',basis_source_ids=[h['control_name1']])]
    code,result,_=run(tmp_path,'extract_electrical_circuits.py',manifest,request,'endpoint_difference_selected')
    byid={r['endpoint_id']:r for r in result['endpoint_reconciliation']}
    assert code==3 and byid['dev1']['disposition']=='selected'and byid['dev1']['consumed']
    assert byid['dev2']['disposition']=='candidate_unselected'and not byid['dev2']['consumed']
    assert byid['ORPHAN']['disposition']=='unreferenced'
    assert result['coverage']['selected_endpoint_ids']==['dev1']
    assert set(result['coverage']['unconsumed_endpoint_ids'])=={'dev2','ORPHAN'}
    assert result['coverage']['unreferenced_endpoint_ids']==['ORPHAN']
    assert byid['ORPHAN']['physical_source_id']==h['orphan']and byid['ORPHAN']['label_source_ids']==[h['orphan_name']]
    request['endpoint_candidates'][1].update(source_box='OTHER-BOX',scope_id='OTHER-SCOPE')
    code,excluded,_=run(tmp_path,'extract_electrical_circuits.py',manifest,request,'endpoint_difference_excluded')
    audit=next(x for x in excluded['endpoint_reconciliation']if x['endpoint_id']=='dev2')
    assert audit['disposition']=='excluded'
    assert any(set(ref['exclusion_reasons'])=={'source_box_mismatch','scope_mismatch'}and ref['circuit_source_ids']and ref['endpoint_label_source_ids']for ref in audit['references'])


def test_t32_reconciles_already_parsed_upstream_and_outgoing_without_manual_feeders(tmp_path):
    d=doc();m=d.modelspace()
    for x,box,up,destination in [(0,'BOX-A','MAIN-Y','LOAD-A'),(20,'BOX-B','MAIN-Z','LOAD-B'),(40,'UPS-X','UPS-X','BOX-A')]:
        text(m,box,x,24);text(m,'回路编号',x,20);text(m,'W1',x+5,20)
        for label,value,y in [('电缆','YJV-3x2.5',18),('设备名称',destination,16),('根数','1',14),('引自',up,12),('状态','正常',10)]:
            text(m,label,x,y);text(m,value,x+5,y)
    manifest=extract(tmp_path/'upstream_cad',d)
    request=base('qto-electrical-circuit-request/1.0');request.update(bounds_m=[-1,0,65,30],text_layers='^TABLE$',table_line_layers='^TABLE$',
        row_tolerance_m=.2,table_extent_m=dict(left=.5,right=20,up=5,down=15),circuit_pattern=r'W\d+',
        spec_pattern=r'YJV-\S+',box_title_pattern=r'BOX-[AB]|UPS-X',expected_boxes=['BOX-A','BOX-B','UPS-X'])
    code,result,_=run(tmp_path,'extract_electrical_circuits.py',manifest,request,'parsed_upstream')
    checks={r['box']:r for r in result['box_upstream_checks']}
    assert code==3 and not request.get('feeder_rows')
    assert checks['BOX-A']['declared_upstream_names']==['MAIN-Y']and checks['BOX-A']['incoming_box_names']==['UPS-X']
    assert checks['BOX-A']['status']=='candidate_conflict'
    assert checks['BOX-A']['conflicting_incoming'][0]['source_ids']and checks['BOX-A']['declared_upstream'][0]['source_ids']
    assert checks['BOX-B']['status']=='uncovered'and 'BOX-B'in result['coverage']['upstream_uncovered_boxes']
    assert checks['UPS-X']['status']=='no_parent_claim_in_scope'
    assert '已解析出线与本箱引自声明不一致'in problems(result)


def test_t33_different_coincident_block_instances_are_not_cross_paired(tmp_path):
    d=doc();b=d.blocks.new('EDGE_TRAY')
    for y in (-.2,.2):b.add_line((0,y),(5,y),dxfattribs=dict(layer='EDGE'))
    for _ in range(2):d.modelspace().add_blockref('EDGE_TRAY',(1,1))
    manifest=extract(tmp_path/'coincident_cad',d);request=tray_request();request['expand_blocks']=['EDGE_TRAY']
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,request,'coincident_instances')
    assert code==3 and len(result['segments'])==2
    assert len({r['id']for r in result['segments']})==2
    assert len({tuple(r['physical_instance_paths'])for r in result['segments']})==2
    assert all(r['length_m']==pytest.approx(5)and r['width_geometry_m']==pytest.approx(.4)for r in result['segments'])


def test_t33_independent_width_attribute_is_structured_and_not_attdef_default(tmp_path):
    d=doc();b=d.blocks.new('ATTR_TRAY')
    b.add_line((0,0),(5,0),dxfattribs=dict(layer='CENTER'))
    for tag,value in [('TYPE','LADDER'),('WIDTH','400'),('ELEVATION','3.5')]:b.add_attdef(tag,(0,0),text=value)
    ins=d.modelspace().add_blockref('ATTR_TRAY',(1,1));ins.add_auto_attribs(dict(TYPE='LADDER',WIDTH='450',ELEVATION='3.5'))
    manifest=extract(tmp_path/'width_cad',d);request=tray_request();request.update(expand_blocks=['ATTR_TRAY'],
        annotation_pattern=r'(?P<spec>LADDER)',attribute_tags=dict(spec='TYPE',width='WIDTH',elevation='ELEVATION'),elevation_datum='DATUM')
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,request,'separate_width')
    row=result['segments'][0]
    assert code==0 and row['width_geometry_m']is None
    assert row['width_attribute_m']==pytest.approx(.45)and row['effective_width_m']==pytest.approx(.45)
    assert row['attribute_width_evidence'][0]['raw_value']=='450'and row['attribute_width_evidence'][0]['source_kind']=='instance_attribute'
    assert any(x['text']=='400'for x in row['block_attribute_evidence'][0]['definition_defaults'])


def test_t33_crossing_scope_is_recorded_but_fully_outside_is_legally_excluded(tmp_path):
    d=doc();m=d.modelspace();add_tray(m,(0,0),(10,0))
    for y in (2.8,3.2):m.add_line((0,y),(20,y),dxfattribs=dict(layer='EDGE'))
    outside=['CAD-'+m.add_line((30,y),(40,y),dxfattribs=dict(layer='EDGE')).dxf.handle for y in (4.8,5.2)]
    manifest=extract(tmp_path/'boundary_cad',d);request=tray_request();request['bounds_m']=[-1,-1,11,6]
    code,result,_=run(tmp_path,'recognize_cable_trays.py',manifest,request,'scope_crossing')
    assert code==3 and len(result['segments'])==1
    assert len(result['coverage']['crossing_source_ids'])==2 and set(result['coverage']['outside_source_ids'])==set(outside)
    assert sum(t['problem']=='桥架源线跨出当前范围，未当完整段计入'for t in result['todo'])==2
    assert all(not r['included_in_recognition']for r in result['coverage']['scope_records']if r['scope_status']in('outside','crosses_scope'))
