"""Anonymous real DXF files through the shipped neutral/point/network CLIs.

These deterministic development cases do not certify a real project's quantities.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/qto-electrical-routes/scripts'
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
POINTS = ROOT / 'skills/qto-components-points/scripts'
sys.path.insert(0, str(SCRIPTS))
import infer_electrical_network_roles as role
import match_electrical_network_expressions as match


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True,
                          text=True, encoding='utf-8', stdin=subprocess.DEVNULL, timeout=240)


def fixture(folder, branch=False, code_conflict=False, near_port=False, second_shape=False):
    folder.mkdir(parents=True)
    doc = ezdxf.new('R2013'); doc.units = 6
    for layer in ('WRONG-GAS-LAYER', 'NOTE'):
        doc.layers.add(layer)
    m = doc.modelspace(); block = doc.blocks.new('DEVICE')
    block.add_lwpolyline([(-.1,-.1),(.1,-.1),(.1,.1),(-.1,.1)],close=True)
    port = block.add_point((0,0)).dxf.handle
    basis = 'CAD-' + m.add_text('本图图例：两端设备实际接口属于项目报警角色；回路X-01为两端连接，所有尺寸单位为米',
        dxfattribs=dict(insert=(0,-3),layer='NOTE',height=.2)).dxf.handle
    design = 'CAD-' + m.add_text('2K：报警系统，匿名双绞线，每段两根。3K：报警系统，同线三根。',
        dxfattribs=dict(insert=(0,-2),layer='NOTE',height=.2)).dxf.handle
    h = {}
    h['A'] = m.add_blockref('DEVICE',(0,0)).dxf.handle
    h['B'] = m.add_blockref('DEVICE',(10,0 if not near_port else .02)).dxf.handle
    points = [(0,0),(5,0),(10,0)] if not second_shape else [(0,0),(0,2),(10,2),(10,0)]
    lines=[]
    for a,b in zip(points,points[1:]):
        lines.append('CAD-'+m.add_line(a,b,dxfattribs=dict(layer='WRONG-GAS-LAYER')).dxf.handle)
    if branch:
        h['C'] = m.add_blockref('DEVICE',(5,4)).dxf.handle
        lines.append('CAD-'+m.add_line((5,0),(5,4),dxfattribs=dict(layer='WRONG-GAS-LAYER')).dxf.handle)
    text = 'CAD-' + m.add_text('2K',dxfattribs=dict(insert=(2,.3),layer='NOTE',height=.2)).dxf.handle
    other = 'CAD-' + m.add_text('3K',dxfattribs=dict(insert=(8,.3),layer='NOTE',height=.2)).dxf.handle
    source = folder/'匿名火警.dxf'; doc.saveas(source)
    neutral = folder/'中性事实'
    result = run([SHARED/'extract_cad_neutral.py','--source-dxf',source,'--output-dir',neutral])
    assert result.returncode == 0, result.stdout+result.stderr
    manifest = neutral/'CAD中性取证清单.json'; mf = role.read(manifest); mh = sha(manifest); src = sha(source)
    physicals=['PT-CAD-'+v for v in h.values()]
    ledger = dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='L',ledger_version='1',
        source_sha256=src,adoption_scope='working',review_status='unreviewed',
        evidence=[dict(evidence_id='G',path=str(source),sha256=src,source_sha256=src,kind='cad_property',locator='Model',observation='匿名图例端口')],
        entries=[dict(adjudication_id='M',subject_id='DEV',professional_owner='qto-components-points',decision='include',status='active',
            decision_origin='model',recognized_by='匿名开发图源',recognized_at='2026-10-19T00:00:00+00:00',source_id='SYN',evidence_ids=['G'],
            object_ids=physicals,basis='实际INSERT图符与图例',scope_basis='本图',identity_basis='完整实例路径',cross_check='原图接口',uncertainties=['开发样本'])])
    lp=folder/'识别台账.json';role.write(lp,ledger)
    profile=dict(schema_version='qto-component-recognition-profile/1.0',profile_id='P',profile_version='1',expected_source_sha256=src,
        adjudication_ledger=dict(ledger_id='L',ledger_version='1',sha256=sha(lp)),scope=dict(layout='Model',drawing_id='SYN',floor='F1',scope_id='AREA',version='V1',source_id='SYN'),
        unit=dict(factor_to_output=1,output_coordinate_unit='m',source_id='INSUNITS=6'),rules=[dict(rule_id='DEV',adjudication_id='M',block_pattern='^DEVICE$',
            decision='include',category='报警设备',system='报警',spec='匿名设备',unit='台',quantity_level='规则推算量',
            network_ports=[dict(port_id_suffix='P',network_disposition='not_applicable',reason='本测试实际电气端口由原图绑定另核')])])
    pp=folder/'点位参数.json';role.write(pp,profile); out=folder/'点位适配'
    result=run([POINTS/'build_component_instances_from_neutral.py','--cad-manifest',manifest,'--profile',pp,'--adjudication-ledger',lp,'--output-dir',out])
    assert result.returncode == 0,result.stdout+result.stderr
    pointfile=out/'点位唯一实例计数输入.json'
    objs=[]
    for key,handle in h.items():
        sid='CAD-'+handle
        objs.append(dict(object_key=key,category='control',source_ids=[basis,sid],endpoint_binding=dict(kind='connection_port',
            manifest_sha256=mh,physical_source_id=sid,physical_id=sid,instance_path=sid,port_source_id=sid+'/DEVICE/CAD-'+port,
            locator='point',basis='实际块内端口',basis_source_ids=[basis])))
    cons=[dict(id='LINK',from_object='A',to_object='B',relation='interconnect',source_ids=[basis],circuit_id='X-01',circuit_source_ids=[basis])]
    if branch:
        cons.append(dict(cons[0],id='BRANCH',to_object='C'))
    network=dict(schema=role.net.REQUEST_SCHEMA,project_id='匿名火警',scope_id='AREA',cad_manifest_sha256=mh,source_sha256=src,
        trace_request=dict(drawing_id='SYN',layout='Model',route_layers='^WRONG-GAS-LAYER$',unit=dict(factor_to_output=1,output_coordinate_unit='m',source_id='INSUNITS=6'),
            expand_blocks=['DEVICE'],snap_tolerance=.000001,gap_tolerance=0.,endpoint_tolerance=.000001),
        endpoint_objects=objs,connections=cons,circuit_specs=[dict(circuit_id='X-01',kind=None,system=None,spec=None,parallel_count=None,
            extras=[],pending_components=['vertical','terminal','reserve'],source_ids=[basis])],sheets=[dict(sheet_id='F1-PLAN',floor='F1',bounds=[-2,-4,13,6])])
    req=dict(schema=role.REQUEST_SCHEMA,network_request=network,point_instances=dict(path=str(pointfile),sha256=sha(pointfile)),
        endpoint_role_bindings=[dict(object_key=k,point_physical_id='PT-CAD-'+v,roles=['项目报警角色'],source_ids=[basis]) for k,v in h.items()],
        project_role_rules=[dict(id='ROLE',roles=['项目报警角色'],systems=['报警'],source_ids=[basis])],
        connection_contexts=[dict(connection_id=c['id'],building='匿名楼',floor='F1',revision='V1',scope_id='AREA',sheet_id='F1-PLAN',source_ids=[basis])for c in cons],
        connection_system_bindings=[],design_codes=[dict(id='K2',expression='2K',text_source_ids=[design],components=[dict(system='报警',kind='wire',spec='匿名双绞线',parallel_count=2)]),
            dict(id='K3',expression='3K',text_source_ids=[design],components=[dict(system='报警',kind='wire',spec='匿名双绞线',parallel_count=3)])],
        text_edge_bindings=[dict(id='BIND',text_source_id=text,expression_id='K2',source_ranges=[dict(source_id=lines[0],part_index=0,fraction_range=[0.,1.])],basis_source_ids=[basis])])
    if code_conflict:
        req['text_edge_bindings'].append(dict(id='CONFLICT',text_source_id=other,expression_id='K3',source_ranges=[dict(source_id=lines[-1],part_index=0,fraction_range=[0.,1.])],basis_source_ids=[basis]))
    return manifest,req,basis,lines


def save_roles(folder, manifest, req):
    result=role.infer_roles(manifest,req); path=folder/'角色结果.json';role.write(path,result)
    return result,path


def expression(eid,result,path,basis):
    return dict(id=eid,role_result=dict(path=str(path),sha256=sha(path)),connection_ids=['LINK'],
        applicability=dict(current=True,basis='原图当前适用平面',basis_source_ids=[basis]),
        endpoint_identities=[dict(object_key=k,identity_key='已识别物理设备-'+k,basis='原图本轮端点同物工作解释',basis_source_ids=[basis])for k in ('A','B')])


def test_real_dxf_points_roles_codes_existing_reference_chain(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源')
    result=role.infer_roles(mf,req)
    assert all(c['system']=='报警' for c in result['connections'])
    assert len(result['edges'])==2 and all(a['expression_ids']==['K2']for a in result['edges'])
    assert result['network_result']['circuit_quantities'][0]['known_length_m']==pytest.approx(20)
    assert result['network_result']['circuit_quantities'][0]['complete_length_m'] is None
    assert result['point_review_check']['status']=='matched'
    assert all(o['point_physical_id'].startswith('PT-CAD-')for o in result['endpoints'])
    assert result['official_adoption'] is False


def test_actual_cli_builds_shared_html_and_records_real_child_codes(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源'); rp=tmp_path/'请求.json';role.write(rp,req)
    done=run([SCRIPTS/'infer_electrical_network_roles.py','--cad-manifest',mf,'--request',rp,'--output-dir',tmp_path/'结果','--build-review'])
    assert done.returncode==3,done.stdout+done.stderr
    receipts=role.read(tmp_path/'结果/实际调用回执.json')
    assert receipts['calls'][0]['returncode']==3
    downstream=tmp_path/'结果/既有网络计算'
    assert list(downstream.glob('*.html'))
    assert (downstream/'调用回执.json').exists()


def test_degree_two_propagation_stops_at_branch(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源',branch=True)
    result=role.infer_roles(mf,req)
    assert sum(a['expression_ids']==['K2']for a in result['edges'])==1
    assert sum(not a['expression_ids']for a in result['edges'])==2


def test_contradicting_codes_are_not_resolved_by_distance(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源',code_conflict=True)
    result=role.infer_roles(mf,req)
    assert all(a['code_status']=='CONFLICT'for a in result['edges'])
    assert result['network_result']['circuit_quantities'][0]['known_length_m'] is None


def test_near_endpoint_does_not_become_actual_connection(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源',near_port=True)
    result=role.infer_roles(mf,req)
    assert result['connections'][0]['status']=='REVIEW_REQUIRED'
    assert result['connections'][0]['path_ids']==[]


def test_wrong_layer_never_overrides_role_and_conflicts_remain(tmp_path):
    mf,req,basis,_=fixture(tmp_path/'源')
    req['project_role_rules'].append(dict(id='GAS',roles=['气体角色'],systems=['可燃气体'],source_ids=[basis]))
    req['endpoint_role_bindings'][1]['roles']=['气体角色']
    req['connection_system_bindings']=[dict(connection_id='LINK',system='报警',source_ids=[basis])]
    result=role.infer_roles(mf,req)
    assert result['connections'][0]['system'] is None
    assert any('冲突'in p for p in result['connections'][0]['problems'])


def test_point_receipt_tamper_is_rejected(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源')
    path=Path(req['point_instances']['path']); data=role.read(path)
    data['reconciled_instances'][0]['point'][0]+=1;role.write(path,data);req['point_instances']['sha256']=sha(path)
    with pytest.raises(ValueError,match='geometry_review'):
        role.infer_roles(mf,req)


def test_original_source_changes_invalidate_old_requests(tmp_path):
    mf,req,_,_=fixture(tmp_path/'源'); source=Path(role.read(mf)['authoritative_source']['path'])
    source.write_bytes(source.read_bytes()+b'\n')
    with pytest.raises(ValueError):
        role.infer_roles(mf,req)


def pair_fixture(tmp_path):
    ma,ra,ba,_=fixture(tmp_path/'主源');mb,rb,bb,_=fixture(tmp_path/'次源',second_shape=True)
    a,ap=save_roles(tmp_path/'主源',ma,ra);b,bp=save_roles(tmp_path/'次源',mb,rb)
    req=dict(schema=match.REQUEST_SCHEMA,expressions=[expression('主表达',a,ap,ba),expression('次表达',b,bp,bb)],decisions=[])
    return req,a,b


def decision(req,a,b,partial=False):
    pairs=[dict(primary_edge_id=a['edges'][0]['id'],secondary_edge_id=edge['id'],basis='本轮核图逐边对应主图表达，仅工作层使用')for edge in b['edges']]
    if partial:pairs=pairs[:1]
    req['decisions']=[dict(id='SELECT',primary_expression='主表达',secondary_expression='次表达',basis='源平面本轮判图主表达',
        role_result_sha256={e['id']:e['role_result']['sha256'] for e in req['expressions']},
        source_refs=[dict(expression_id=e['id'],source_ids=e['applicability']['basis_source_ids'])for e in req['expressions']],edge_pairs=pairs)]


def test_same_devices_different_geometry_never_automatically_merge(tmp_path):
    req,a,b=pair_fixture(tmp_path)
    result=match.match_expressions(req)
    assert result['candidates'][0]['status']=='CANDIDATE_REQUIRES_EDGE_DECISION'
    assert result['decisions']==[]
    assert len(result['edge_dispositions'])==5
    assert sum(i['known_length_m']for i in result['reference_calculation']['items'])==pytest.approx(48)


def test_explicit_source_decision_selects_primary_without_equating_lengths(tmp_path):
    req,a,b=pair_fixture(tmp_path);decision(req,a,b)
    result=match.match_expressions(req)
    assert sum(i['known_length_m']for i in result['reference_calculation']['items'])==pytest.approx(20)
    assert result['physical_length_equivalence_proven'] is False
    assert len(result['edge_dispositions'])==5 and all(c['no_edge_lost']for c in result['coverage'])
    assert len(result['expressions'][1]['original_edges'])==3
    assert result['source_quantity_mutation'] is False


@pytest.mark.parametrize('change',['floor','revision','circuit','unknown_identity','invalid_primary','count'])
def test_conflicting_context_identity_and_parallel_conductors_are_not_suppressed(tmp_path,change):
    req,a,b=pair_fixture(tmp_path)
    path=Path(req['expressions'][1]['role_result']['path'])
    if change in ('floor','revision'):
        b['connections'][0]['context'][change]='OTHER'
        if change=='floor':
            own_sheet=next(s for s in b['generated_network_request']['sheets']
                           if s['sheet_id']==b['connections'][0]['context']['sheet_id'])
            own_sheet['floor']='OTHER'
    elif change=='circuit':b['connections'][0]['circuit_id']='SECOND'
    elif change=='unknown_identity':req['expressions'][1]['endpoint_identities'].pop()
    elif change=='invalid_primary':req['expressions'][0]['applicability']['current']=False
    elif change=='count':
        for item in b['network_result']['ledger']['circuits']:item['parallel_count']=3
    role.write(path,b);req['expressions'][1]['role_result']['sha256']=sha(path)
    decision(req,a,b)
    result=match.match_expressions(req)
    assert result['decisions']==[]
    if change=='floor':
        assert result['candidates'][0]['status']=='NOT_CLOSED'
        assert 'floor' in result['candidates'][0]['conflicts']
    assert all(d['destination']=='retained_independent_working_expression'for d in result['edge_dispositions'])


def test_partial_coverage_keeps_unmapped_edges_and_pending_quantities(tmp_path):
    req,a,b=pair_fixture(tmp_path);decision(req,a,b,partial=True)
    result=match.match_expressions(req)
    secondary=next(c for c in result['coverage']if c['expression_id']=='次表达')
    assert len(secondary['retained_edge_ids'])==2
    assert len(result['edge_dispositions'])==5
    assert any('未对应' in r['problem']for r in result['review_items'])
    assert all('expression_correspondence_unresolved'in c['pending_components']for c in result['selected_working_ledger']['circuits']if c['expression_id']=='次表达')


def test_stale_decision_refuses_reuse(tmp_path):
    req,a,b=pair_fixture(tmp_path);decision(req,a,b)
    req['decisions'][0]['role_result_sha256']['主表达']='0'*64
    with pytest.raises(ValueError,match='SHA'):
        match.match_expressions(req)


def test_match_cli_recalculates_working_ledger_with_existing_cli(tmp_path):
    req,a,b=pair_fixture(tmp_path);decision(req,a,b)
    path=tmp_path/'多图请求.json';role.write(path,req)
    done=run([SCRIPTS/'match_electrical_network_expressions.py','--request',path,'--output-dir',tmp_path/'选择结果'])
    assert done.returncode==3,done.stdout+done.stderr
    assert role.read(tmp_path/'选择结果/实际调用回执.json')['calls'][0]['returncode']==0
    assert role.read(tmp_path/'选择结果/选定工作数量复算.json')['business_adoption_allowed'] is False
    assert all(i['official_adoption'] is False for i in role.read(tmp_path/'选择结果/选定工作数量复算.json')['items'])
    assert role.read(tmp_path/'选择结果/实际调用回执.json')['calls'][1]['returncode']==3
