"""Anonymous multi-callout and separately frozen source riser controls."""
import copy
import json
import math
import os
from pathlib import Path
import pytest
from test_duct_source_recovery_t28_batch9_20261019 import refresh, leader_case
from test_duct_batch_network_t28_t20_20261019 import fixture, build, consume, write, sha, run, DUCT
import build_duct_riser_bundle_from_neutral as bundle


def multi_case(tmp_path):
    c = leader_case(tmp_path)
    def edit(m, doc):
        # Independent actual text and complete source leader tree for the third
        # single-line part. No list-order correspondence to target parts.
        text = m.add_text('500x250 h=4.1', dxfattribs={'insert': (3000, 6500), 'height': 100, 'layer': 'DUCT-NOTE'})
        keys = []
        for a, b in (((3000,6500),(3200,6500)), ((3200,6500),(3500,6000))):
            keys.append('CAD-' + m.add_line(a,b,dxfattribs={'layer':'DUCT-LEADER'}).dxf.handle)
        return text, keys
    text, keys = refresh(c, edit)
    rule = c['profile']['rules'][-1]
    original = rule.pop('annotation_binding')
    other = copy.deepcopy(original)
    other.update(source_key='CAD-'+text.dxf.handle, expected_text='500x250 h=4.1')
    other['leader_network'].update(source_keys=keys, shoulder_source_key=keys[0])
    rule['annotation_bindings'] = [original, other]
    proof_path = Path(rule['evidence']['path'])
    evidence = json.loads(proof_path.read_text(encoding='utf-8'))
    evidence.pop('annotation_network', None)
    evidence['annotation_groups'] = rule['annotation_bindings']
    write(proof_path, evidence); rule['evidence']['sha256'] = sha(proof_path)
    return c


def test_multi_source_callouts_actual_cli_sizes_and_elevations(tmp_path):
    c = multi_case(tmp_path); data = build(c)
    lines = [s for s in data['segments'] if s['representation']=='single_line']
    assert sorted((s['width'],s['height']) for s in lines)==[(300,200),(300,200),(500,250)]
    result = consume(tmp_path,data)
    by_id = {r['segment_id']:r for r in result['straight_segments']}
    for line in lines:
        assert by_id[line['segment_id']]['center_elevation']==pytest.approx(3.3 if line['width']==300 else 4.225)
    write(c['pp'],c['profile'])
    out = tmp_path/'producer-cli'
    proc = run(DUCT/'build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',c['pp'],'--output-dir',out)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert json.loads((out/'实际风管消费日志.json').read_text(encoding='utf-8'))['returncode']==0


def test_multi_callout_tampered_text_and_omitted_branch(tmp_path):
    c=multi_case(tmp_path); rule=c['profile']['rules'][-1]
    rule['annotation_bindings'][1]['expected_text']='700x700'
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text());ev['annotation_groups']=rule['annotation_bindings'];write(ep,ev);rule['evidence']['sha256']=sha(ep)
    with pytest.raises(ValueError,match='引注内容'):build(c)


def test_multi_callout_conflicting_target_never_array_priority(tmp_path):
    c=multi_case(tmp_path);rule=c['profile']['rules'][-1]
    # A new independent leader reaches the already labelled first target.
    def edit(m,doc):
        doc.layers.new('OTHER-LEADER')
        for key in rule['annotation_bindings'][1]['leader_network']['source_keys']:
            doc.entitydb[key[4:]].dxf.layer='OTHER-LEADER'
        raw=rule['annotation_bindings'][1]['leader_network']['source_keys'][-1]
        doc.entitydb[raw[4:]].dxf.end=(500,6000,0)
    refresh(c,edit)
    data=build(c)
    conflict=next(s for s in data['segments'] if s['representation']=='single_line' and s['start'][0]==0)
    assert conflict.get('width') is None
    assert any(s['status']=='unresolved' and s.get('conflicting_part_keys') for s in data['annotation_scope_candidates'])


PATTERN=(r'scope=(?P<scope_id>\S+) id=(?P<physical_id>\S+) sys=(?P<system>\S+) '
         r'floor=(?P<floor_id>\S+) datum=(?P<datum_id>\S+) kind=(?P<datum_kind>\S+) '
         r'300x200(?P<size_unit>mm) (?P<reference>center|top|bottom)=(?P<value>[-\d.]+)(?P<unit>m)')


def source_case(path, floor, elevation, *, datum='ABS', physical='R1', reference='center', units=4, kind='absolute'):
    path.mkdir();c=fixture(path,units=units)
    actual=f'scope=A id={physical} sys=SYS floor={floor} datum={datum} kind={kind} 300x200mm {reference}={elevation}m'
    def edit(m,doc):
        text=m.add_text(actual,dxfattribs={'insert':(8000,3500),'height':100,'layer':'DUCT-NOTE'})
        doc.layers.new('RISER-LEADER');keys=[]
        for a,b in (((8000,3500),(8200,3500)),((8200,3500),(7500,3000))):
            keys.append('CAD-'+m.add_line(a,b,dxfattribs={'layer':'RISER-LEADER'}).dxf.handle)
        return text,keys
    text,keys=refresh(c,edit);c['profile']['floor_id']=floor;write(c['pp'],c['profile'])
    observation={'identity_annotation':{'source_key':'CAD-'+text.dxf.handle,'expected_text':actual,'pattern':PATTERN,
                  'basis':'anonymous explicit physical/system/floor/common absolute datum source label','source_locator':'Model source identity label'},
                 'leader_network':{'source_keys':keys,'shoulder_source_key':keys[0],'snap_tolerance':.001,'text_tolerance':.001,
                  'target_tolerance':.001,'target_contact':'source_curve_endpoint','basis':'actual shoulder to source projection endpoint',
                  'source_locator':'Model source leader'}}
    return c,observation


def bundle_case(tmp_path, *, upper=7.4, lower=3.2, datum='ABS', units=4, reference='center', upper_kind='absolute'):
    a,ao=source_case(tmp_path/'a','F1',lower,units=units)
    b,bo=source_case(tmp_path/'b','F2',upper,datum=datum,reference=reference,kind=upper_kind)
    cfg={'schema_version':bundle.SCHEMA,'package_id':'ANON-RISER-BUNDLE','sources':[
        {'source_id':name,'manifest':{'path':str(c['manifest']),'sha256':sha(c['manifest'])},
         'profile':{'path':str(c['pp']),'sha256':sha(c['pp'])},'observations':[obs]}
        for name,c,obs in [('LOWER',a,ao),('UPPER',b,bo)]]}
    bp=write(tmp_path/'bundle.json',cfg)
    return bp,cfg,a,b


def test_cross_manifest_actual_source_id_endpoint_height_one_outlet(tmp_path):
    bp,cfg,a,b=bundle_case(tmp_path,reference='top');data,_=bundle.build(bp)
    assert len(data['riser_vertical_items'])==1
    item=data['riser_vertical_items'][0]
    assert item['length']==pytest.approx(4.1)
    assert item['theoretical_surface_area']==pytest.approx(4.1)
    assert len(item['source_ids'])==2 and len(set(item['source_ids']))==2
    assert all(r['height']==pytest.approx(4.1) for r in data['riser_projection_candidates'])
    assert len(data['coverage']['rows'])==2 and all(r['disposition']=='counted' for r in data['coverage']['rows'])
    out=tmp_path/'bundle-cli';proc=run(DUCT/'build_duct_riser_bundle_from_neutral.py','--bundle',bp,'--output-dir',out)
    assert proc.returncode==0,proc.stdout+proc.stderr
    result=json.loads((out/'风管实际消费结果.json').read_text(encoding='utf-8'))
    assert len(result['vertical_items'])==1 and result['metrics']['known_source_riser_length_rule']==pytest.approx(4.1)
    assert result['metrics']['totals_complete'] is False
    log=json.loads((out/'实际风管消费日志.json').read_text(encoding='utf-8'));assert log['returncode']==0
    # Projection raw arc length is >1m and is neither the height nor its basis.
    assert data['coverage']['rows'][0]['raw_geometry_length']==pytest.approx(math.pi*500)


@pytest.mark.parametrize('change',['datum','datum_kind','missing_label','manual_id','broken_leader','profile_sha','same_source','same_source_new_space','duplicate_part'])
def test_cross_source_missing_or_forged_identity_never_quantity(tmp_path,change):
    bp,cfg,a,b=bundle_case(tmp_path,datum='OTHER' if change=='datum' else 'ABS',upper_kind='project_common' if change=='datum_kind' else 'absolute')
    if change=='missing_label':cfg['sources'][1]['observations'][0].pop('identity_annotation')
    if change=='manual_id':cfg['sources'][1]['observations'][0]['physical_riser_id']='FORGED'
    if change=='broken_leader':cfg['sources'][1]['observations'][0]['leader_network']['source_keys'].pop()
    if change=='profile_sha':cfg['sources'][1]['profile']['sha256']='0'*64
    if change=='same_source':cfg['sources'][1]={**copy.deepcopy(cfg['sources'][0]),'source_id':'UPPER'}
    if change=='same_source_new_space':
        cfg['sources'][1]={**copy.deepcopy(cfg['sources'][0]),'source_id':'UPPER'}
        shifted=copy.deepcopy(a['profile']);shifted['space_id']='OTHER-SPACE'
        for index,rule in enumerate(shifted['rules']):
            evidence=json.loads(Path(rule['evidence']['path']).read_text(encoding='utf-8'));evidence['space_id']='OTHER-SPACE'
            ep=write(tmp_path/('shifted-evidence-'+str(index)+'.json'),evidence)
            rule['evidence']={'path':str(ep),'sha256':sha(ep)}
        pp=write(tmp_path/'shifted-profile.json',shifted);cfg['sources'][1]['profile']={'path':str(pp),'sha256':sha(pp)}
    if change=='duplicate_part':cfg['sources'][1]['observations']*=2
    write(bp,cfg)
    if change in {'manual_id','profile_sha','same_source','same_source_new_space','duplicate_part'}:
        with pytest.raises(ValueError):bundle.build(bp)
        if change=='same_source_new_space':
            rejected=tmp_path/'duplicate-source-cli'
            proc=run(DUCT/'build_duct_riser_bundle_from_neutral.py','--bundle',bp,'--output-dir',rejected)
            assert proc.returncode==2 and not rejected.exists()
    else:
        data,_=bundle.build(bp);assert not data['riser_vertical_items']
        assert all(r['height'] is None for r in data['riser_projection_candidates'])
        assert all(r['disposition']=='unrecognized' for r in data['coverage']['rows'])
        result=consume(tmp_path,data)
        assert not result['vertical_items'] and any(p['kind']=='cross_source_riser_identity_unresolved' for p in result['pending'])


def test_cross_source_tamper_replay_and_path_protection(tmp_path):
    bp,cfg,a,b=bundle_case(tmp_path);data,_=bundle.build(bp)
    data['riser_vertical_items'][0]['length']=999
    ip=write(tmp_path/'tamper.json',data);op=tmp_path/'tamper-out.json'
    proc=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',op)
    assert proc.returncode==2 and not op.exists()
    before=bp.read_bytes();out=tmp_path/'occupied';out.mkdir();os.link(bp,out/'风管实际消费输入.json')
    proc=run(DUCT/'build_duct_riser_bundle_from_neutral.py','--bundle',bp,'--output-dir',out)
    assert proc.returncode==2 and bp.read_bytes()==before


def test_cross_source_plane_units_do_not_control_independent_source_height(tmp_path):
    bp,cfg,a,b=bundle_case(tmp_path,units=0);data,_=bundle.build(bp)
    assert data['neutral_binding']['sources'][0]['coordinate_unit']=='raw_DU'
    assert data['riser_vertical_items'][0]['length']==pytest.approx(4.2)
    result=consume(tmp_path,data)
    assert result['metrics']['known_source_riser_length_rule']==pytest.approx(4.2)
    assert result['metrics']['totals_complete'] is False
    assert result['source_xy_unit_verified'] is False and result['quantity_scope']=='vertical_risers_only'


def test_new_modules_are_receipt_dependencies():
    from duct_inline_reception import duct_dependencies
    names={Path(v['path']).name for v in duct_dependencies()}
    assert {'duct_callout_groups.py','build_duct_riser_bundle_from_neutral.py'}<=names
