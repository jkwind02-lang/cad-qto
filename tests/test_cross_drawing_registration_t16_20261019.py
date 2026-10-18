"""Anonymous DXF -> neutral -> registration/working correspondence -> existing review HTML.

These are deterministic evidence/quantity boundary tests, not project adoption or DWG validation.
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
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SHARED))
import qto_cross_drawing_registration as reg


def run(args):
    return subprocess.run([sys.executable,'-B',*map(str,args)],capture_output=True,text=True,encoding='utf-8',timeout=60)


def fixture(folder, units=(6,4), offset=100., rotation=0.):
    folder.mkdir(parents=True,exist_ok=True); sources=[]; controls=[]; observations=[]
    for i,unit in enumerate(units):
        key='A' if i==0 else 'B';factor=1 if unit==6 else .001;scale=1/factor
        d=ezdxf.new('R2013');d.units=unit;m=d.modelspace()
        import math
        def raw(p):
            x,y=p
            if i: x,y=math.cos(rotation)*x-math.sin(rotation)*y+offset,math.sin(rotation)*x+math.cos(rotation)*y
            return (x*scale,y*scale,0.)
        pts=[m.add_point(raw(p)) for p in ((0,0),(10,0),(0,10))]
        basis='CAD-'+m.add_text('同层同范围跨视图同物工程编号对照；单根且标高3m',dxfattribs=dict(insert=raw((0,12)),height=scale*.2)).dxf.handle
        block=d.blocks.new('DEVICE');inner=block.add_point((0,0,0));insert=m.add_blockref('DEVICE',raw((3,2)))
        nested='CAD-'+insert.dxf.handle+'/DEVICE/CAD-'+inner.dxf.handle
        line=m.add_line(raw((1,0)),raw((11 if i==0 else 16,0)))
        path=folder/(key+'.dxf');d.saveas(path);out=folder/(key+'中性')
        proc=run([SHARED/'extract_cad_neutral.py','--source-dxf',path,'--output-dir',out]);assert proc.returncode==0,proc.stdout+proc.stderr
        manifest=out/'CAD中性取证清单.json'
        sources.append(dict(source_key=key,drawing_id='匿名图'+key,version='R1',floor='F1',scope_id='S1',neutral_manifest=str(manifest),manifest_sha256=reg.sha(manifest),
                            layout='Model',unit=dict(factor_to_m=factor,basis='匿名DXF显式单位'),expand_blocks=['DEVICE']))
        controls.append([dict(source_id='CAD-'+p.dxf.handle,instance_path='CAD-'+p.dxf.handle,locator='point')for p in pts])
        observations.extend([dict(observation_id=key+'-P',source_key=key,source_id=nested,instance_path=nested,kind='point',object_id='旧编号' if i==0 else '新编号',object_type='设备',physical_id='PT-'+nested),
                             dict(observation_id=key+'-L',source_key=key,source_id='CAD-'+line.dxf.handle,instance_path='CAD-'+line.dxf.handle,kind='line',object_id='线'+key,object_type='挡烟垂壁',
                                  parallel_count=1,elevation_m=3.,multiplicity_elevation_basis_source_ids=[basis])])
        sources[-1]['_basis']=basis
    pairs=[dict(from_ref=a,to_ref=b,basis='匿名轴号/柱位控制点明确对应',from_basis_source_ids=[sources[0]['_basis']],to_basis_source_ids=[sources[1]['_basis']]) for a,b in zip(*controls)]
    request=dict(schema=reg.SCHEMA,project_id='匿名配准',review_id='T16',title='匿名跨图配准复核',revision='R1',sources=sources,objects=observations,
                 registration=dict(from_source_key='A',to_source_key='B',model='rigid_2d' if rotation else 'translation',control_pairs=pairs,max_residual_m=1e-6),candidate_tolerance_m=.02)
    return request


def interpretation(req, kind='P'):
    obs=[next(o for o in req['objects']if o['observation_id']==k+'-'+kind) for k in ('A','B')]
    refs=[dict(observation_id=o['observation_id'],source_key=o['source_key'],source_id=o['source_id'],instance_path=o['instance_path'],basis_source_ids=[req['sources'][i]['_basis']])for i,o in enumerate(obs)]
    return dict(interpretation_id='I-'+kind,observation_ids=[o['observation_id']for o in obs],basis='两侧编号对照文字及同层平面工程语义由模型显式解释',decision_origin='model',source_refs=refs)


@pytest.mark.parametrize('units,rotation',[((6,4),0),((4,6),.3),((4,4),0)])
def test_cli_dxf_neutral_registration_scene_html(tmp_path,units,rotation):
    req=fixture(tmp_path/'源',units,rotation=rotation); req['interpretations']=[interpretation(req),interpretation(req,'L')]
    req['line_union_requests']=[dict(interpretation_id='I-L',collinear_tolerance_m=1e-6)]
    path=tmp_path/'请求.json';path.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8');out=tmp_path/'结果'
    proc=run([SHARED/'qto_cross_drawing_registration.py','--input',path,'--output-dir',out,'--build-review']);assert proc.returncode==0,proc.stdout+proc.stderr
    result=reg.load(out/'跨图配准与候选.json');view=reg.load(out/'跨图复核视图.json')
    assert result['registration']['max_residual_m']<1e-8
    assert result['registration']['rotation_rad']==pytest.approx(rotation)
    assert len(result['candidates'])==2 and len(result['pairings'])==2
    assert result['line_unions'][0]['comparison_union_length_m']==pytest.approx(15.)
    assert result['line_unions'][0]['original_lengths_m']==pytest.approx([10.,15.])
    assert all(not o['official'] and not o['business_adoption']for o in result['pairings'])
    assert all(not o['quantities']for o in view['objects'])
    assert len(view['scenes'])==3 and len(view['objects'])==8
    nested=result['objects'][0];assert '/DEVICE/'in nested['source_id'] and nested['instance_path']==nested['source_id']
    assert nested['physical_id']=='PT-'+nested['source_id']
    html=(out/'跨图复核.html').read_text(encoding='utf-8');assert 'number-drift'in html and '未合并、未扣量'in html


def test_nearby_equal_and_partial_overlap_remain_candidates(tmp_path):
    req=fixture(tmp_path/'源');r=reg.resolve(req)
    assert len(r['candidates'])==2 and not r['pairings'] and not r['line_unions']
    assert all(not c['automatic_merge_allowed'] and not c['same_physical_proven'] for c in r['candidates'])
    assert next(c for c in r['candidates']if c['observation_ids']==['A-L','B-L'])['comparison_overlap_m']==pytest.approx(10.)
    req['objects'][2]['object_type']='另一类设备'
    assert all(c['observation_ids']!=['A-P','B-P']for c in reg.resolve(req)['candidates'])
    req['interpretations']=[interpretation(req)]
    with pytest.raises(ValueError,match='类别/版本/楼层/范围'):reg.resolve(req)


def test_unit_mismatch_and_unknown_dxf_unit_are_refused(tmp_path):
    req=fixture(tmp_path/'源');req['sources'][0]['unit']['factor_to_m']=.001
    with pytest.raises(ValueError):reg.resolve(req)
    req=fixture(tmp_path/'未知单位',units=(0,6))
    with pytest.raises(ValueError,match='未知图纸单位'):reg.resolve(req)


@pytest.mark.parametrize('field,value',[('version','R2'),('floor','F2'),('scope_id','S2'),('floor',None)])
def test_context_differences_visible_but_cannot_claim_same_physical(tmp_path,field,value):
    req=fixture(tmp_path/'源')
    if value is not None:req['sources'][1][field]=value
    else:req['objects'][2][field]=value
    r=reg.resolve(req);candidate=next(c for c in r['candidates']if c['observation_ids']==['A-P','B-P'])
    assert field in candidate['missing_context'if value is None else 'context_conflicts']
    req['interpretations']=[interpretation(req)]
    with pytest.raises(ValueError,match='类别/版本/楼层/范围'):reg.resolve(req)


@pytest.mark.parametrize('field,value',[('parallel_count',2),('parallel_count',None),('parallel_count',True),('elevation_m',None),('elevation_m',4)])
def test_line_union_blocks_multiple_or_unknown_or_different_elevation(tmp_path,field,value):
    req=fixture(tmp_path/'源');req['objects'][3][field]=value;req['interpretations']=[interpretation(req,'L')]
    req['line_union_requests']=[dict(interpretation_id='I-L',collinear_tolerance_m=.001)]
    with pytest.raises(ValueError,match='单根|标高'):reg.resolve(req)


def test_residual_rejects_transformation_instead_of_suppressing_warning(tmp_path):
    req=fixture(tmp_path/'源');req['registration']['control_pairs'][2]['to_ref']=copy.deepcopy(req['registration']['control_pairs'][0]['to_ref'])
    with pytest.raises(ValueError,match='重复控制点'):reg.resolve(req)
    # Wrong but distinct control correspondences leave large residuals and zero pairing/search.
    req=fixture(tmp_path/'另一源'); pairs=req['registration']['control_pairs'];pairs[1]['to_ref'],pairs[2]['to_ref']=pairs[2]['to_ref'],pairs[1]['to_ref']
    result=reg.resolve(req);assert result['status']=='REVIEW_REQUIRED'and not result['candidates']
    assert result['registration']['max_residual_m']>1


def test_source_hash_and_nested_identity_refusal(tmp_path):
    req=fixture(tmp_path/'源');req['objects'][0]['instance_path']='CAD-'+req['objects'][0]['source_id'].split('CAD-')[-1]
    with pytest.raises(ValueError,match='instance_path'):reg.resolve(req)
    req=fixture(tmp_path/'另一源');p=Path(req['sources'][0]['neutral_manifest']);p.write_bytes(p.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='SHA'):reg.resolve(req)


def test_interpretation_requires_both_original_binding_and_source_basis(tmp_path):
    req=fixture(tmp_path/'源');req['interpretations']=[interpretation(req)]
    req['interpretations'][0]['source_refs'][0]['source_id']='unknown'
    with pytest.raises(ValueError,match='原始观察'):reg.resolve(req)
    req['interpretations']=[];req['objects'][0]['version']='伪造统一版本'
    with pytest.raises(ValueError,match='图源版本'):reg.resolve(req)
    req['objects'][0].pop('version');duplicate=copy.deepcopy(req['objects'][0]);duplicate['observation_id']='另起观察编号';req['objects'].append(duplicate)
    with pytest.raises(ValueError,match='重复观察'):reg.resolve(req)


def test_existing_comparison_drift_has_scope_and_does_not_change_quantity():
    def ob(key,x,version='R1'):return dict(id=key,catalog_key=key,kind='point',points=[[x,0]],scene_id='S',category='灯具',quantities=[dict(name='个数',value=1,unit='个',level='working')],attributes=dict(floor='F1',scope_id='S1',version=version))
    view=dict(scenes=[dict(id='S',coordinate_unit='m')],objects=[ob('旧',0),ob('新',.0002,'R2'),ob('远',.1)])
    before=[copy.deepcopy(o['quantities'])for o in view['objects']];drifts=reg.annotate_number_drift(view)
    assert len(drifts)==1 and drifts[0]['context_conflicts']==['version']
    assert [o['quantities']for o in view['objects']]==before
    assert len(view['objects'])==3 and [o['id']for o in view['objects']]==['旧','新','远']
    import build_comparison_view_from_tables as comparison
    view['objects'][1]['id']='旧'
    assert comparison.shared_object_stats(view)['multi_quantity']==0
    view['objects'][1]['attributes']['version']='R1'
    assert comparison.shared_object_stats(view)['multi_quantity']==1


def test_existing_comparison_cli_carries_number_drift_into_scene(tmp_path):
    import test_shared_sheets_20261016 as ss
    a,_=ss.package(tmp_path,'包甲');va=reg.load(a);vb=copy.deepcopy(va)
    for ob in va['objects']:
        ob.setdefault('attributes',{}).update(floor='F1',scope_id='S1',version='R1')
    for ob in vb['objects']:
        ob['catalog_key']='B|'+ob['catalog_key'];ob.setdefault('attributes',{}).update(floor='F1',scope_id='S1',version='R2')
        if ob['id']=='S1':ob['id']='新编号'
    for group in vb['groups']:group['member_keys']=['B|'+v for v in group['member_keys']]
    a.write_text(json.dumps(va,ensure_ascii=False),encoding='utf-8');b=tmp_path/'包乙.json';b.write_text(json.dumps(vb,ensure_ascii=False),encoding='utf-8')
    before=[copy.deepcopy(o['quantities']) for v in (va,vb)for o in v['objects']]
    code,report,out=ss.merge(tmp_path,a,b);assert code==0,report
    assert report['suspected_number_drifts']['count']>=1
    drift=next(c for c in report['suspected_number_drifts']['rows']if set(c['object_ids'])=={'S1','新编号'})
    assert drift['context_conflicts']==['version']
    merged=reg.load(out);assert any(o['attributes'].get('suspected_number_drifts')for o in merged['objects'])
    bykey={o['catalog_key']:o for o in merged['objects']}
    assert all(bykey[o['catalog_key']]['quantities']==o['quantities'] for v in (va,vb)for o in v['objects'])


def structured_comparison(tmp_path,case='same'):
    """Real comparison CLI with legal structured review attributes, including reordered objects."""
    import test_shared_sheets_20261016 as ss
    tmp_path.mkdir(parents=True,exist_ok=True)
    a,_=ss.package(tmp_path,'结构包甲');va=reg.load(a);vb=copy.deepcopy(va)
    attrs=dict(object_type={'family':['设备',{'a':1,'b':False}]},floor=['1F','2F'],
               version={'revision':1,'nested':{'a':[1,None,True],'b':'R'}},scope_id={'zone':{'a':1,'b':'Z'},'building':['A']})
    reordered=dict(scope_id={'building':['A'],'zone':{'b':'Z','a':1.0}},version={'nested':{'b':'R','a':[1.0,None,True]},'revision':1.0},
                   floor=['1F','2F'],object_type={'family':['设备',{'b':False,'a':1.0}]})
    for ob in va['objects']:
        if ob['id']=='S1':ob['attributes']=copy.deepcopy(attrs)
    for ob in vb['objects']:
        ob['catalog_key']='B|'+ob['catalog_key']
        if ob['id']=='S1':ob['attributes']=copy.deepcopy(reordered)
    sa=next(o for o in va['objects']if o['id']=='S1');sb=next(o for o in vb['objects']if o['id']=='S1')
    if case=='array_order':sb['attributes']['floor']=['2F','1F']
    elif case=='bool_number':sa['attributes']['version']=True;sb['attributes']['version']=1
    elif case=='null_missing':sa['attributes']['version']=None;sb['attributes'].pop('version')
    elif case=='structure':sb['attributes']['scope_id']=[sb['attributes']['scope_id']]
    for group in vb['groups']:group['member_keys']=['B|'+v for v in group['member_keys']]
    a.write_text(json.dumps(va,ensure_ascii=False),encoding='utf-8');b=tmp_path/'结构包乙.json';b.write_text(json.dumps(vb,ensure_ascii=False),encoding='utf-8')
    code,report,out=ss.merge(tmp_path,a,b)
    return code,report,out,va,vb


@pytest.mark.parametrize('case,shared',[('same',True),('array_order',False),('bool_number',False),('null_missing',True),('structure',False)])
def test_structured_context_actual_comparison_cli_preserves_values(tmp_path,case,shared):
    code,report,out,va,vb=structured_comparison(tmp_path,case);assert code==0,report
    assert report['shared_objects']['objects']==len(va['objects'])-(0 if shared else 1)
    merged=reg.load(out);bykey={o['catalog_key']:o for o in merged['objects']}
    for view in (va,vb):
        for original in view['objects']:
            actual=bykey[original['catalog_key']]
            assert actual['quantities']==original['quantities']
            for key,value in original.get('attributes',{}).items():
                assert json.dumps(actual['attributes'][key],sort_keys=True,ensure_ascii=False)==json.dumps(value,sort_keys=True,ensure_ascii=False)


def test_context_keys_match_browser_json_number_type_and_structure_semantics():
    values=[None,True,False,1,1.0,0,-0.0,'1',[],{},['1F','2F'],['2F','1F'],
            {'a':[1,None,True],'b':{'x':'设备','y':False}}, {'b':{'y':False,'x':'设备'},'a':[1.0,None,True]},
            {'a':True},{'a':1},{'😀':1,'\ue000':2},{'\ue000':2.,'😀':1.}]
    asset=(ROOT/'skills/_qto-shared-evidence/assets/review/review-app.js').read_text(encoding='utf-8')
    source=asset[asset.index(' function contextValue(v)'):asset.index(' const objectContext=')]
    script=source+"\nconst fs=require('fs');const v=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(v.map(x=>JSON.stringify(contextValue(x)))));"
    proc=subprocess.run(['node','-e',script],input=json.dumps(values,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert proc.returncode==0,proc.stderr
    assert json.loads(proc.stdout)==[reg.canonical_context_key(v)for v in values]
    assert reg.canonical_context_key(True)!=reg.canonical_context_key(1)
    assert reg.canonical_context_key(1)==reg.canonical_context_key(1.0)
    assert reg.canonical_context_key(0)==reg.canonical_context_key(-0.0)
    assert reg.canonical_context_key([])!=reg.canonical_context_key({})


def test_structured_drift_compares_json_types_without_mutating_original_attributes():
    def ob(key,version,typ):return dict(id=key,catalog_key=key,kind='point',points=[[0,0]],scene_id='S',category='设备',quantities=[],attributes=dict(floor=['1F','2F'],scope_id={'a':1,'b':[None]},version=version,object_type=typ))
    a=ob('旧',{'a':1,'b':False},{'a':['设备'],'b':1});b=ob('新',{'b':False,'a':1.0},{'b':1.0,'a':['设备']})
    view=dict(scenes=[dict(id='S',coordinate_unit='mm')],objects=[a,b]);before=copy.deepcopy(view)
    rows=reg.annotate_number_drift(view);assert len(rows)==1 and not rows[0]['context_conflicts'] and not rows[0]['missing_context']
    a['attributes']['version']=True;b['attributes']['version']=1
    assert reg.annotate_number_drift(view)[0]['context_conflicts']==['version']
    a['attributes']['object_type']=True;b['attributes']['object_type']=1
    assert not reg.annotate_number_drift(view)
    for current,original in zip(view['objects'],before['objects']):
        assert current['attributes']['floor']==original['attributes']['floor']and current['attributes']['scope_id']==original['attributes']['scope_id']
        assert current['quantities']==original['quantities']


def test_exchange_dxf_actual_cli_checks_fingerprint_and_legacy_missing_field(tmp_path):
    req=fixture(tmp_path/'源');source=req['sources'][0];manifest=reg.load(source['neutral_manifest'])
    authority=Path(manifest['authoritative_source']['path']);authority_before=reg.sha(authority)
    exchange=tmp_path/'匿名交换副本.dxf';exchange.write_bytes(authority.read_bytes())
    manifest['exchange_dxf']=dict(path=str(exchange),sha256=reg.sha(exchange),bytes=exchange.stat().st_size)
    path=tmp_path/'当前中性清单.json';path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8');source['neutral_manifest']=str(path);source['manifest_sha256']=reg.sha(path)
    request=tmp_path/'请求.json';request.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8')
    proc=run([SHARED/'qto_cross_drawing_registration.py','--input',request,'--output-dir',tmp_path/'合法结果']);assert proc.returncode==0,proc.stdout+proc.stderr
    result=reg.load(tmp_path/'合法结果/跨图配准与候选.json')
    assert result['provenance']['fingerprints'][str(exchange.resolve())]==reg.sha(exchange)
    assert result['sources'][0]['verified_source_files']['exchange_dxf']['sha256']==reg.sha(exchange)
    snapshot=reg.Source(source,tmp_path)
    exchange.write_bytes(exchange.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='上游来源发生变化'):snapshot.stable()
    proc=run([SHARED/'qto_cross_drawing_registration.py','--input',request,'--output-dir',tmp_path/'过期结果']);assert proc.returncode==2,proc.stdout+proc.stderr
    assert 'exchange_dxf'in proc.stderr and not (tmp_path/'过期结果').exists()
    assert reg.sha(authority)==authority_before
    manifest.pop('exchange_dxf');path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8');source['manifest_sha256']=reg.sha(path);request.write_text(json.dumps(req,ensure_ascii=False),encoding='utf-8')
    proc=run([SHARED/'qto_cross_drawing_registration.py','--input',request,'--output-dir',tmp_path/'旧合法无交换字段结果']);assert proc.returncode==0,proc.stdout+proc.stderr
    assert reg.load(tmp_path/'旧合法无交换字段结果/跨图配准与候选.json')['sources'][0]['verified_source_files']['exchange_dxf']is None
