"""Second electrical/general batch: invented labels/coordinates only, no project quantities."""
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import ezdxf
import pytest
import test_negative_search_20261016 as fx
import test_point_handoff_source_gaps_20261015 as points

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
import search_negative_evidence as search
import extract_cad_neutral as neutral


def text(h,t,x=100,y=500,angle=0,height=50):
    r=fx.rec(h,'TEXT','SYN-TEXT',x,y,text=t,raw={'height':height,'width_factor':1})
    r['rotation']=angle
    return r


def parts(angle=0):
    return [text('L'+str(i),s,100+i*80*math.cos(angle),500+i*80*math.sin(angle),angle)
            for i,s in enumerate(['联箱','1350','×','1450','×','750','梁底'])]


def run_text(tmp_path,layout,term='1350*1450*750',**extra):
    d=fx.package(tmp_path,'SYN',layout=layout,blocks=[])
    return fx.run(tmp_path,items=[dict(item_id='S',conclusion='图上无 '+term,kind='text',term=term,**extra)],drawings=[d])


@pytest.mark.parametrize('source',[('感烟探测器',{}),(None,{'default':'感烟探测器'})])
def test_top_level_attdef_is_real_text_hit_with_type_provenance(tmp_path,source):
    r=fx.rec('DEF','ATTDEF','0',100,500,text=source[0],raw=source[1])
    code,_,d,_=run_text(tmp_path,[r],term='感烟探测器')
    s=d['records'][0]
    assert code==0 and s['result']['hits']==1 and s['status']=='hit'
    assert s['result']['samples'][0]['entity_type']=='ATTDEF'
    assert 'ATTDEF' in s['result']['samples'][0]['via']


@pytest.mark.parametrize('term,field',[('感烟探测器','tag'),('默认名称','default'),('显示内容','text')])
def test_attdef_reads_distinct_text_default_and_tag(tmp_path,term,field):
    r=fx.rec('DEF','ATTDEF','0',100,500,text='显示内容',raw={'default':'默认名称','tag':'感烟探测器'})
    _,_,d,_=run_text(tmp_path,[r],term=term)
    record=d['records'][0]
    assert record['status']=='hit' and record['result']['hits']==1
    assert 'ATTDEF:'+field in record['result']['samples'][0]['via']


@pytest.mark.parametrize('angle',[0,math.pi/2,math.pi,-math.pi/4])
def test_joined_text_is_secondary_candidate_with_all_handles(tmp_path,angle):
    rows=parts(angle)
    # Keep rotated labels in the same actual sheet.
    for r in rows:r['x']+=400
    code,_,d,out=run_text(tmp_path,rows)
    r=d['records'][0];hit=r['result']['secondary_samples'][0]
    assert code==0 and r['status']=='no_hit_partial' and r['result']['hits']==0
    assert r['result']['secondary_hits']==1 and hit['member_handles']==['L'+str(i) for i in range(7)]
    assert '拼接候选' in hit['via'] and search.record_problems(r)==[]
    detail=list(csv.DictReader((out/search.HIT_DETAIL).open(encoding='utf-8-sig')))
    assert detail[0]['参与句柄']=='；'.join(hit['member_handles'])


@pytest.mark.parametrize('change',['far','angle','layer','page'])
def test_unrelated_text_never_forms_joined_hit(tmp_path,change):
    rows=[text('A','1350',100,500),text('B','×1450×750',230,500)]
    if change=='far':rows[1]['x']=900
    elif change=='angle':rows[1]['rotation']=math.pi/2
    elif change=='layer':rows[1]['layer']='OTHER'
    else:rows[1]['x']=2100
    _,_,d,_=run_text(tmp_path,rows)
    assert d['records'][0]['result']['hits']==0 and d['records'][0]['result']['secondary_hits']==0


def test_old_text_without_join_geometry_cannot_claim_full_coverage(tmp_path):
    r=text('OLD','片段');r['raw_geometry']={}
    _,_,d,_=run_text(tmp_path,[r])
    record=d['records'][0]
    assert record['status']=='no_hit_partial' and record['result']['text_join_geometry_missing']
    record['status']='no_hit_full'
    assert any('拆分文字覆盖' in p for p in search.record_problems(record))


def test_excluded_hits_are_counted_and_input_survives_source_overwrite(tmp_path):
    code,_,d,out=run_text(tmp_path,[text('E','消防 输入模块')],term='输入模块',exclude=['消防'])
    assert code==0
    r=d['records'][0]
    assert r['result']['hits']==0 and r['result']['excluded_hits']==1 and r['status']=='no_hit_full'
    assert r['targets']['exclude']==['消防'] and r['result']['excluded_samples'][0]['exclusion_patterns']==['消防']
    detail=list(csv.DictReader((out/search.HIT_DETAIL).open(encoding='utf-8-sig')))
    assert detail[0]['命中类别']=='已排除' and detail[0]['排除词']=='消防'
    original=(tmp_path/'input.json').read_bytes()
    (tmp_path/'input.json').write_text('{}',encoding='utf-8')
    assert (out/d['input']['frozen_path']).read_bytes()==original
    assert hashlib.sha256(original).hexdigest()==d['input']['frozen_sha256']==d['input']['sha256']
    assert d['input']['snapshot']['items'][0]['exclude']==['消防']
    replay=points.run(search.__file__,'--replay-record',out/'否定结论检索记录.json','--output-dir',tmp_path/'replay')
    assert replay.returncode==0,replay.stdout+replay.stderr
    restored=json.loads((tmp_path/'replay/否定结论检索记录.json').read_text(encoding='utf-8'))
    assert restored['records']==d['records'] and restored['input']['sha256']==d['input']['sha256']
    (tmp_path/'SYN_图页.csv').write_text('changed',encoding='utf-8')
    refused=points.run(search.__file__,'--replay-record',out/'否定结论检索记录.json','--output-dir',tmp_path/'changed')
    assert refused.returncode==2 and '已变更' in refused.stdout


def test_actual_extractor_retains_text_height_and_width_for_joining():
    doc=ezdxf.new();entity=doc.modelspace().add_text('合成',dxfattribs={'height':37,'width':.8})
    r=neutral.convert(entity,layout='Model',parent_block=None,source_hash='a'*64,raw_unit='mm',sequence=1)
    assert r['raw_geometry']['height']==37 and r['raw_geometry']['width_factor']==.8


def test_long_chain_keeps_spec_across_sixteenth_fragment(tmp_path):
    fragments=['标']*14+['1280','×1360','×720']
    rows=[text(str(i),part,100+i*35,500,height=20) for i,part in enumerate(fragments)]
    _,_,d,_=run_text(tmp_path,rows,term='1280*1360*720')
    r=d['records'][0]
    assert r['status']=='no_hit_partial' and r['result']['secondary_hits']==1
    assert r['result']['secondary_samples'][0]['member_handles']==[str(i) for i in range(17)]


@pytest.mark.parametrize('initial_present',[True,False])
def test_replay_binds_manifest_presence_as_well_as_content(tmp_path,initial_present):
    drawing=fx.package(tmp_path,'SYN',layout=[text('A','普通标注')],blocks=[])
    manifest=tmp_path/'neutral/SYN/CAD中性取证清单.json'
    if not initial_present:manifest.unlink()
    _,_,d,out=fx.run(tmp_path,items=[dict(item_id='S',conclusion='图上无目标',kind='text',term='目标')],drawings=[drawing])
    assert d['scope'][0]['neutral']['manifest_present'] is initial_present
    p=out/'否定结论检索记录.json'
    replay=points.run(search.__file__,'--replay-record',p,'--output-dir',tmp_path/'unchanged')
    assert replay.returncode==0,replay.stdout+replay.stderr
    if initial_present:manifest.unlink()
    else:manifest.write_text('{"blocks":[]}',encoding='utf-8')
    refused=points.run(search.__file__,'--replay-record',p,'--output-dir',tmp_path/'changed')
    assert refused.returncode==2 and '存在状态' in refused.stdout


@pytest.mark.parametrize('case',['excluded','orphan','anonymous','active'])
def test_only_scoped_insertions_can_block_legacy_text_coverage(tmp_path,case):
    old=text('OLD','普通说明',100,500);old['raw_geometry']={}
    name='*U321' if case=='anonymous' else 'INNER'
    old.update(parent_block=name,layout='BlockDefinition')
    blocks=[old];layout=[]
    if case=='orphan':blocks.append(fx.rec('NEST','INSERT','0',0,0,block=name,matrix=fx.m(0,0),parent='ORPHAN'))
    if case in ('excluded','active'):
        layout=[fx.rec('TOP','INSERT','0',0,0,block=name,matrix=fx.m(4000 if case=='excluded' else 0,0))]
    drawing=fx.package(tmp_path,'SYN',layout=layout,blocks=blocks)
    if case=='excluded':drawing['exclude_sheet_titles']='设备材料表'
    _,_,d,_=fx.run(tmp_path,items=[dict(item_id='S',conclusion='图上无目标',kind='text',term='目标')],drawings=[drawing])
    r=d['records'][0]
    assert r['status']==('no_hit_partial' if case=='active' else 'no_hit_full')
    assert bool(r['result']['text_join_geometry_missing']) is (case=='active')


@pytest.mark.parametrize('consumer',[None,'','qto-components-points','invalid-network'])
def test_count_only_does_not_require_network_handoff_and_invalid_name_still_fails(tmp_path,consumer):
    _,baseline,_,_=points.build_case(tmp_path,mixed=False,declare_all=True)
    p=tmp_path/'profile.json';profile=json.loads(p.read_text(encoding='utf-8'))
    if consumer is None:profile.pop('consumer_skill')
    else:profile['consumer_skill']=consumer
    p.write_text(json.dumps(profile,ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'count_only'
    r=points.run(points.POINTS/'build_component_instances_from_neutral.py','--cad-manifest',tmp_path/'neutral/CAD中性取证清单.json',
                 '--profile',p,'--adjudication-ledger',tmp_path/'ledger.json','--output-dir',out)
    if consumer=='invalid-network':
        assert r.returncode==2 and '只计数可以不填' in r.stderr;return
    assert r.returncode==0,r.stdout+r.stderr
    report=json.loads((out/'点位识别报告.json').read_text(encoding='utf-8'))
    assert report['counts']['adopted_physical_instance_count']==baseline['counts']['adopted_physical_instance_count']==2
    assert report['status']==baseline['status'] and report['endpoint_handoff_status']=='not_requested'
    assert report['endpoint_unresolved_count'] is None and 'endpoint_handoff' not in report['artifacts']
    assert not (out/'点位端点交接输入.json').exists() and not (out/'点位至网络端点交接.json').exists()
