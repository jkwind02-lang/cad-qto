"""T37 anonymous controls; these do not establish actual project support quantities."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/qto-electrical-routes/scripts'
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
import calculate_electrical_supports as supports


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=60)


def measure(value, unit='m', level='方案暂估量'):
    return dict(value=value, unit=unit, quantity_level=level, basis='匿名参数选定方案', source_ids=['BASIS'])


def fixture(tmp_path):
    upstream = dict(schema='qto-electrical-object-package/1.0', objects=[
        dict(id='T1', physical_id='PHY-T1', kind='tray', system='动力', spec='300x100', complete_length_m=10.2),
        dict(id='T2', physical_id='PHY-T2', kind='tray', system='动力', spec='300x100', complete_length_m=10.2),
        dict(id='T3', physical_id='PHY-T3', kind='tray', system='控制', spec='200x100', complete_length_m=4.5)])
    up = write(tmp_path/'上游对象包.json', upstream)
    basis = write(tmp_path/'匿名方案依据.json', dict(selected=True, reason='显式匿名构造和表面处理参数；不代表设计'))
    sources = [dict(id='UP', path=str(up), sha256=sha(up)), dict(id='BASIS', path=str(basis), sha256=sha(basis))]
    segments = [dict(id='S'+str(i+1), physical_id=o['physical_id'], upstream_id=o['id'], system=o['system'], spec=o['spec'],
                     orientation='horizontal', length=dict(value=o['complete_length_m'], unit='m', quantity_level='确认量', basis='上游已算物理桥架', source_ids=['UP']),
                     width=measure(.3 if i<2 else .2),
                     upstream=dict(source_id='UP', object_pointer='/objects/'+str(i), length_field='complete_length_m'))
                for i,o in enumerate(upstream['objects'])]
    def component(cid, kind, surface, count, length, weight):
        return dict(id=cid, kind=kind, surface=surface, basis='匿名构造', source_ids=['BASIS'],
                    count_each=measure(count,'1'), length_each=measure(length), unit_weight=measure(weight,'kg/m'),
                    exposed_perimeter=measure(.18), paint_required=True if surface=='non_galvanized' else False,
                    paint_basis=dict(basis='仅非镀锌部件刷油；镀锌者排除', source_ids=['BASIS']),
                    scope=dict(supply='included', installation='included'))
    construction = dict(id='DESIGN-A', quantity_level='方案暂估量', basis='已选匿名工作方案', source_ids=['BASIS'],
                        selection_status='selected', applicable_orientations=['horizontal'],
                        components=[component('ARM','crossarm','non_galvanized',1,.5,3), component('ROD','rod','galvanized',2,1.2,.7)])
    def group(gid, system, members):
        return dict(id=gid, system=system, basis='匿名专用支架范围', source_ids=['BASIS'], working_count_basis='equivalent',
                    runs=[dict(id='RUN-'+gid, segment_ids=members, length_segment_id=members[0], shared_support_identity='SHARED-'+gid,
                               basis='同一副显式支承多层桥架；仅取主段一次', source_ids=['BASIS'])],
                    spacing=measure(1.8), rounding=dict(scope='per_run', method='ceil', basis='逐独立通道段向上取整，不是实际布点', source_ids=['BASIS']),
                    events=[], event_scope=dict(status='complete',basis='匿名样本已核端部转角分支无额外净增',source_ids=['BASIS']),
                    placements=None, construction=copy.deepcopy(construction))
    document = dict(schema=supports.SCHEMA, project_id='匿名支架参考', sources=sources, segments=segments,
                    support_groups=[group('A','动力',['S1','S2']), group('B','控制',['S3'])])
    return document


def calculate(document, tmp_path, previous=None):
    return supports.calculate(document, tmp_path, previous)


def test_shared_support_layers_and_material_units(tmp_path):
    result, graph, calc = calculate(fixture(tmp_path), tmp_path)
    a = result['groups'][0]
    assert a['known_support_run_length']['value'] == 10.2
    assert a['counts']['equivalent']['value'] == pytest.approx(10.2/1.8)
    assert a['counts']['rule_integer']['value'] == 6
    assert a['counts']['actual_placements']['value'] is None
    arm, rod = a['components']
    assert arm['bases']['equivalent']['length_m']['value'] == pytest.approx(10.2/1.8*.5)
    assert rod['bases']['equivalent']['length_m']['value'] == pytest.approx(10.2/1.8*2*1.2)
    assert arm['bases']['equivalent']['paint_mass_kg']['value'] == pytest.approx(8.5)
    assert arm['bases']['equivalent']['paint_area_m2']['value'] == pytest.approx(.51)
    assert rod['bases']['equivalent']['paint_mass_kg'] is None
    assert rod['bases']['equivalent']['paint_status'] == 'not_applicable'
    assert all(not x[k] for x in [result] for k in ['business_adoption_allowed','official_adoption','human_approval'])
    assert graph['schema']=='qto-calculation-graph/1.0' and not calc['errors']


def test_unknown_component_only_blocks_its_dependants(tmp_path):
    doc = fixture(tmp_path)
    doc['support_groups'][0]['construction']['components'][0]['length_each'] = None
    result,_,_=calculate(doc,tmp_path)
    a=result['groups'][0]
    assert a['counts']['rule_integer']['value']==6
    assert a['known_support_run_length']['value']==10.2
    assert a['components'][0]['bases']['equivalent']['steel_mass_kg']['value'] is None
    assert a['components'][1]['bases']['equivalent']['length_m']['value'] is not None


def test_vertical_does_not_consume_horizontal_construction(tmp_path):
    doc=fixture(tmp_path)
    for seg in doc['segments'][:2]:seg['orientation']='vertical'
    result,_,_=calculate(doc,tmp_path)
    a=result['groups'][0]
    assert a['known_support_run_length']['value']==10.2
    assert a['counts']['equivalent']['value'] is not None
    assert a['components'][0]['bases']['equivalent']['length_m']['value'] is None
    assert a['pending']


def test_missing_construction_preserves_length_and_counts(tmp_path):
    doc=fixture(tmp_path);doc['support_groups'][0]['construction']=None
    result,_,_=calculate(doc,tmp_path)
    a=result['groups'][0]
    assert a['known_support_run_length']['value']==10.2 and a['counts']['rule_integer']['value']==6
    assert a['components']==[] and a['working_count'] is None


def test_events_unique_net_addition(tmp_path):
    doc=fixture(tmp_path)
    event=dict(id='CORNER', physical_support_id='SUP-CORNER', kind='corner', net_addition=1,
               run_ids=['RUN-A'], basis='端部和转角为同一物理支架，只记录一次', source_ids=['BASIS'])
    doc['support_groups'][0]['events']=[event]
    result,_,_=calculate(doc,tmp_path)
    assert result['groups'][0]['counts']['rule_integer']['value']==7
    doc['support_groups'][0]['events'].append(dict(event,id='END',kind='end'))
    with pytest.raises(ValueError,match='重复端部'):calculate(doc,tmp_path)


def test_actual_placements_are_bound_not_ceil(tmp_path):
    doc=fixture(tmp_path);group=doc['support_groups'][0]
    records=[dict(physical_support_id='SUP-'+str(i),run_ids=['RUN-A'],chainage_m={'RUN-A':v}) for i,v in enumerate([0,1.7,3.4,5.1,6.8,8.5,10.2])]
    path=write(tmp_path/'实际布点源.json',dict(supports=records))
    doc['sources'].append(dict(id='POINTS',path=str(path),sha256=sha(path)))
    group['placements']=[dict(r,id=r['physical_support_id'],basis='源记录实物布点',source_ids=['POINTS'],source_binding=dict(source_id='POINTS',pointer='/supports/'+str(i)))for i,r in enumerate(records)]
    group['placement_scope']=dict(status='complete',basis='该组布点源记录范围完整',source_ids=['POINTS'])
    result,_,_=calculate(doc,tmp_path)
    assert result['groups'][0]['counts']['actual_placements']['value']==7
    assert result['groups'][0]['counts']['rule_integer']['value']==6
    group['placements'][0]['chainage_m']={'RUN-A':1}
    with pytest.raises(ValueError,match='源记录不符'):calculate(doc,tmp_path)


def test_explicit_rounding_scope_changes_only_declared_group(tmp_path):
    doc=fixture(tmp_path)
    group=doc['support_groups'][0]
    group['spacing']['value']=2
    group['runs']=[dict(id='R1',segment_ids=['S1'],length_segment_id='S1'),dict(id='R2',segment_ids=['S2'],length_segment_id='S2')]
    first,_,old=calculate(doc,tmp_path)
    group['rounding'].update(scope='continuous_group',continuous_channel_id='CH-A',run_sequence=['R1','R2'])
    second,_,new=calculate(doc,tmp_path,old)
    assert first['groups'][0]['counts']['rule_integer']['value']==12
    assert second['groups'][0]['counts']['rule_integer']['value']==11
    assert 'GROUP:A:rule_integer'in new['affected_nodes']
    assert not any(n.startswith('GROUP:B:')for n in new['affected_nodes'])


@pytest.mark.parametrize('change',['spacing','construction'])
def test_existing_engine_related_recompute_and_no_review_inheritance(tmp_path,change):
    doc=fixture(tmp_path);before,graph,old=calculate(doc,tmp_path)
    # Fabricated legacy review state does not count as a real human receipt.
    for n in old['nodes']:
        if n['id']=='GROUP:A:equivalent':n['review_state']='current'
    if change=='spacing':doc['support_groups'][0]['spacing']['value']=1.5
    else:doc['support_groups'][0]['construction']['components'][0]['length_each']['value']=.6
    after,newgraph,new=calculate(doc,tmp_path,old)
    assert not any(n.startswith('GROUP:B:')for n in new['affected_nodes'])
    if change=='construction':assert 'GROUP:A:equivalent'not in new['affected_nodes']
    assert next(n for n in new['nodes']if n['id']=='GROUP:A:equivalent')['review_state']!='current'
    assert new['approval_issues']
    write(tmp_path/'图1.json',graph);write(tmp_path/'图2.json',newgraph)
    cli1=run([SHARED/'recompute_qto_dependencies.py','--input',tmp_path/'图1.json','--output',tmp_path/'旧计算.json'])
    cli2=run([SHARED/'recompute_qto_dependencies.py','--input',tmp_path/'图2.json','--previous',tmp_path/'旧计算.json','--output',tmp_path/'新计算.json'])
    assert cli1.returncode==cli2.returncode==0,cli1.stdout+cli2.stdout
    actual=json.loads((tmp_path/'新计算.json').read_text(encoding='utf-8'))
    assert not any(n.startswith('GROUP:B:')for n in actual['affected_nodes'])


@pytest.mark.parametrize('mutation,match',[
    ('unit','单位'),('outdated','指纹'),('duplicate_segment','重复'),('duplicate_physical','physical_id'),
    ('same_width_hidden_merge','重复分配'),('zero_spacing','范围'),('paint_from_kg','单位'),('galvanized_paint','镀锌'),
    ('mixed_orientation','须分组'),('missing_rounding','取整'),('unbound_actual','源记录'),
])
def test_controlled_rejection(tmp_path,mutation,match):
    doc=fixture(tmp_path)
    if mutation=='unit':doc['segments'][0]['length']['unit']='mm'
    if mutation=='outdated':doc['sources'][0]['sha256']='0'*64
    if mutation=='duplicate_segment':doc['segments'].append(copy.deepcopy(doc['segments'][0]))
    if mutation=='duplicate_physical':doc['segments'][1]['physical_id']=doc['segments'][0]['physical_id']
    if mutation=='same_width_hidden_merge':doc['support_groups'][1]['runs'][0]['segment_ids']=['S1']
    if mutation=='zero_spacing':doc['support_groups'][0]['spacing']['value']=0
    if mutation=='paint_from_kg':doc['support_groups'][0]['construction']['components'][0]['exposed_perimeter']['unit']='kg'
    if mutation=='galvanized_paint':doc['support_groups'][0]['construction']['components'][1]['paint_required']=True
    if mutation=='mixed_orientation':doc['segments'][1]['orientation']='vertical'
    if mutation=='missing_rounding':doc['support_groups'][0]['rounding']=None
    if mutation=='unbound_actual':
        doc['support_groups'][0]['placements']=[dict(id='P',physical_support_id='P',basis='false',source_ids=['BASIS'])]
        doc['support_groups'][0]['placement_scope']=dict(status='complete',basis='false',source_ids=['BASIS'])
    with pytest.raises((ValueError,KeyError),match=match):calculate(doc,tmp_path)


def test_unknown_surface_never_all_paint_and_connection_unit(tmp_path):
    doc=fixture(tmp_path);construction=doc['support_groups'][0]['construction']
    construction['components'][0]['surface']='unknown'
    construction['components'][0]['paint_required']=None
    construction['components'].append(dict(id='BOLT',kind='connection',count_unit='个',count_each=measure(4,'1'),mass_each=measure(.05,'kg'),area_each=None,
        basis='匿名连接件',source_ids=['BASIS'],surface='non_galvanized',paint_required=True,
        paint_basis=dict(basis='仅该件非镀锌刷油',source_ids=['BASIS']),scope=dict(supply='unknown',installation='included')))
    result,_,_=calculate(doc,tmp_path)
    arm,_,bolt=result['groups'][0]['components']
    assert arm['bases']['equivalent']['paint_mass_kg'] is None and arm['bases']['equivalent']['paint_status']=='unknown'
    assert bolt['bases']['rule_integer']['member_count']['unit']=='个'
    assert bolt['bases']['rule_integer']['member_count']['value']==24
    assert bolt['bases']['rule_integer']['paint_mass_kg']['value']==pytest.approx(1.2)
    assert bolt['bases']['rule_integer']['paint_area_m2']['value'] is None


def test_cli_saves_new_outputs_and_refuses_overwrite(tmp_path):
    doc=fixture(tmp_path);path=write(tmp_path/'输入.json',doc);output=tmp_path/'复算'
    first=run([SCRIPTS/'calculate_electrical_supports.py','--input',path,'--output-dir',output])
    second=run([SCRIPTS/'calculate_electrical_supports.py','--input',path,'--output-dir',output])
    assert first.returncode==0,first.stdout+first.stderr
    assert second.returncode==2 and '已存在'in second.stdout
    result=json.loads((output/'支架参考复算.json').read_text(encoding='utf-8'))
    assert result['input_raw_sha256']==sha(path) and result['script_raw_sha256']==sha(SCRIPTS/'calculate_electrical_supports.py')


def test_pending_events_do_not_turn_into_zero_and_proposed_not_selected(tmp_path):
    doc=fixture(tmp_path)
    doc['support_groups'][0]['event_scope']['status']='pending'
    doc['support_groups'][0]['construction']['selection_status']='proposed'
    result,_,_=calculate(doc,tmp_path)
    group=result['groups'][0]
    assert group['known_support_run_length']['value']==10.2
    assert group['counts']['equivalent']['value'] is None
    assert group['counts']['rule_integer']['value'] is None
    assert group['working_count'] is None


def test_source_schema_and_upper_quantity_level_rejected(tmp_path):
    doc=fixture(tmp_path)
    up=Path(doc['sources'][0]['path']);data=json.loads(up.read_text(encoding='utf-8'))
    data['objects'][0]['quantity_level']='方案暂估量'
    write(up,data);doc['sources'][0]['sha256']=sha(up)
    with pytest.raises(ValueError,match='提升上游'):calculate(doc,tmp_path)
    data['schema']='unrelated/1.0';write(up,data);doc['sources'][0]['sha256']=sha(up)
    with pytest.raises(ValueError,match='schema'):calculate(doc,tmp_path)
