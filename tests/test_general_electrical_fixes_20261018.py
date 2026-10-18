"""第六批甲：去标识化反例与合法对照，不包含项目图纸或数量。"""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SCRIPTS))
import extract_cad_neutral as neutral
import search_negative_evidence as search
import qto_review_contract as contract
import review_comparison_fixture as fixture
from test_review_fifth_20261015 import answered_view, common_note, feedback, handoff


def occurrence(text):
    return dict(text=text,flat=search.flat(text),flat_sep=search.flat_sep(text))


@pytest.mark.parametrize('token',['%%C','Φ','φ','Ø','∅'])
def test_diameter_symbol_retained_without_matching_every_text(token):
    v=search.variants(dict(item_id='DIA',kind='text',term=token))
    assert search.match(occurrence('风管 Φ125'),v['main'],[],v)
    assert search.match(occurrence('风管 %%C125'),v['main'],[],v)
    assert not search.match(occurrence('配电箱 AL8'),v['main'],[],v)


def test_empty_normalization_refused_and_normal_text_preserved():
    with pytest.raises(ValueError,match='规范化后为空'):
        search.variants(dict(item_id='EMPTY',kind='text',term='---'))
    v=search.variants(dict(item_id='NORMAL',kind='text',term='送风机'))
    assert search.match(occurrence('设备 送风机 SF-1'),v['main'],[],v)


def test_box_short_alias_with_conflicting_building_prefix_is_secondary():
    v=search.variants(dict(item_id='BOX',kind='box',term='12-R2-04-ZPB-K-7',aliases=['04-ZPB-K-7']))
    bad=occurrence('合成乙楼 99-R2-04-ZPB-K-7')
    assert not search.match(bad,v['main'],[],v)
    assert search.match(bad,v['second'],[],v,'second')
    for text in ['12-R2-04-ZPB-K-7','04-ZPB-K-7','合成甲楼 12-R2-04-ZPB-K-7']:
        assert search.match(occurrence(text),v['main'],[],v)


def test_bound_xref_block_matches_same_base_without_changing_full_name():
    v=search.variants(dict(item_id='BLOCK',kind='block',block_names=[r'^\$equip\$009$']))
    assert search.match(dict(text=None,block='X-05$0$$equip$009'),[],[],v)
    assert search.match(dict(text=None,block='$equip$009'),[],[],v)
    assert not search.match(dict(text=None,block='X-05$0$$equip$0099'),[],[],v)


@pytest.mark.parametrize('field',['patterns','exclude','block_names'])
def test_empty_user_regex_cannot_match_or_exclude_all(field):
    with pytest.raises(ValueError,match='正则不能为空'):
        search.variants(dict(item_id='EMPTY_REGEX',kind='text',term='风管',**{field:['']}))


def test_partial_system_prefix_conflict_and_nondigit_alias():
    v=search.variants(dict(item_id='BOX',kind='box',term='12-R2-04-ZPB-K-7',aliases=['04-ZPB-K-7']))
    o=occurrence('R9-04-ZPB-K-7')
    assert not search.match(o,v['main'],[],v) and search.match(o,v['second'],[],v,'second')
    v=search.variants(dict(item_id='BOX',kind='box',term='DD-ATB-NA',aliases=['ATB-NA']))
    o=occurrence('CC-ATB-NA')
    assert not search.match(o,v['main'],[],v) and search.match(o,v['second'],[],v,'second')


@pytest.mark.parametrize('direction,expected',[((0,1,0),math.pi/2),((-1,0,0),math.pi),((1,-1,0),-math.pi/4)])
def test_mtext_direction_overrides_rotation(direction,expected):
    doc=ezdxf.new();e=doc.modelspace().add_mtext('合成文字',dxfattribs={'rotation':17})
    e.dxf.text_direction=direction
    r=neutral.convert(e,layout='Model',parent_block=None,source_hash='a'*64,raw_unit='mm',sequence=1)
    assert r['rotation']==pytest.approx(expected)
    assert r['raw_geometry']['text_direction']==list(direction)
    assert r['geometry_status']=='supported'


def test_mtext_angle_only_and_invalid_direction():
    doc=ezdxf.new();e=doc.modelspace().add_mtext('角度',dxfattribs={'rotation':30})
    kw=dict(layout='Model',parent_block=None,source_hash='a'*64,raw_unit='mm',sequence=1)
    assert neutral.convert(e,**kw)['rotation']==pytest.approx(math.pi/6)
    e.dxf.text_direction=(0,0,1);r=neutral.convert(e,**kw)
    assert r['rotation'] is None and r['geometry_status']=='unsupported'


@pytest.mark.parametrize('key',['object_match','scope_match','specification_match','coverage_complete'])
def test_noncomparable_quantity_kept_without_business_difference(key):
    v=fixture.view();s=fixture.item(v,'I2')['third_parties'][0]
    s['comparability'][key]=False;s['comparability']['reason']='合成：此项范围或对象尚未闭合'
    n=contract.normalize_view(v);s=fixture.item(n,'I2')['third_parties'][0]
    assert s['quantity']==11 and fixture.item(n,'I2')['ours']['total']==10
    assert s['conclusion']=='不可比' and s['difference'] is None
    assert s['attribution'] is None and s['list_issue_types']==[]
    contract.normalize_view(n)  # 正规化输出可再次回读，原归因不使新状态报错


def test_legacy_qualification_unknown_not_implicitly_complete():
    v=fixture.view();s=fixture.item(v,'I2')['third_parties'][0];s.pop('comparability')
    n=contract.normalize_view(v);s=fixture.item(n,'I2')['third_parties'][0]
    assert s['conclusion']=='待核可比性' and s['difference'] is None
    assert s['quantity']==11 and s['attribution'] is None


def test_out_of_period_keeps_basis_without_false_no_hit():
    v=fixture.view();s=fixture.item(v,'I2')['third_parties'][0]
    s.update(conclusion='本期不计',scope_basis='合成：本期工程边界不含该系统',difference=None,
             search_evidence={'drawings':['合成图A'],'targets':['合成预留回路'],'method':'合成图已定位对象','hits':1})
    n=contract.normalize_view(v);actual=fixture.item(n,'I2')['third_parties'][0]
    assert actual['conclusion']=='本期不计' and actual['attribution'] is None
    assert actual['search_evidence']['hits']==1
    missing=copy.deepcopy(v);fixture.item(missing,'I2')['third_parties'][0].pop('search_evidence')
    with pytest.raises(ValueError,match='检索依据'):contract.normalize_view(missing)
    s.pop('scope_basis')
    with pytest.raises(ValueError,match='scope_basis'):contract.normalize_view(v)


@pytest.mark.parametrize('label',['原量未填','原量明确零','不可比','待核可比性','本次量未形成'])
def test_derived_label_cannot_bypass_real_positive_quantity_comparison(label):
    v=fixture.view();s=fixture.item(v,'I2')['third_parties'][0]
    s['conclusion']=label
    with pytest.raises(ValueError,match='派生结论'):contract.normalize_view(v)


@pytest.mark.parametrize('current',[0,4])
def test_proven_zero_retains_legal_audit_only_for_positive_current(current):
    v=fixture.view();it=fixture.item(v,'I6');s=it['third_parties'][0]
    s['quantity']=s['rows'][0]['quantity']=0
    it['ours']['levels']['确认量']=it['detail_rows'][0]['quantity']=current
    n=contract.normalize_view(v);s=fixture.item(n,'I6')['third_parties'][0]
    assert s['conclusion']=='原量明确零' and s['difference']=={'value':current,'rate':None}
    assert s['attribution']==('list' if current else None)
    assert s['list_issue_types']==(['清单漏量'] if current else [])
    assert ('清单的问题|清单漏量' in fixture.item(n,'I6')['list_issue_ids']) is bool(current)
    assert fixture.item(contract.normalize_view(n),'I6')['third_parties'][0]==s


def test_blocked_legacy_diagnostics_survive_second_normalization():
    v=fixture.view();s=fixture.item(v,'I2')['third_parties'][0]
    s.pop('comparability');s.update(attribution='list',excess=None,list_issue_types=['清单多计'])
    n=contract.normalize_view(v);first=copy.deepcopy(fixture.item(n,'I2')['third_parties'][0])
    assert first['diagnostic_legacy_derivation']=={'attribution':'list','list_issue_types':['清单多计']}
    again=fixture.item(contract.normalize_view(n),'I2')['third_parties'][0]
    assert again==first


def test_zero_original_does_not_replace_not_on_drawing_or_bypass_evidence():
    v=fixture.view();it=fixture.item(v,'I3');s=it['third_parties'][0]
    s['quantity']=s['rows'][0]['quantity']=0
    n=contract.normalize_view(v);actual=fixture.item(n,'I3')['third_parties'][0]
    assert actual['conclusion']=='图上无' and actual['difference'] is None
    assert actual['attribution'] is None and actual['list_issue_types']==[]
    s.pop('search_evidence')
    with pytest.raises(ValueError,match='检索依据'):contract.normalize_view(v)


def drawing_note(n):
    g=n['groups'][0];keys=g['member_keys']
    return dict(annotation_id='问题组|DG',group_id='DG',object_id='DG',object_ids=g['object_ids'],
                selected_object_keys=keys,excluded_object_keys=[],member_snapshot=g['member_snapshot'],
                result_fingerprint=g['result_fingerprint'],confirmed_result='合成：暂停原估算',reason='实际对象依据不足',
                instruction='暂缓计算',scope='本组勾选对象',updated_at='2026-10-18T08:00:00Z')


@pytest.mark.parametrize('ids,expected',[(None,True),(('D1','D1'),True),(('D1','D2'),False)])
def test_cross_entry_identity_conflict_python_js_and_handoff(ids,expected):
    v=answered_view();v['groups']=[dict(id='DG',title='合成待核',category='设备',proposal='估算方案',reason='缺位置',status='待核',member_keys=[o['catalog_key'] for o in v['objects']])]
    if ids:
        v['groups'][0]['decision_id']=ids[0];v['comparison']['common_issues'][0]['decision_id']=ids[1]
    n=contract.normalize_view(v)
    package=feedback(n,{'问题组|DG':drawing_note(n),'共性问题|G1':common_note(n)})
    fb,unmatched=contract.validate_feedback(copy.deepcopy(package),n)
    assert bool(fb['decision_conflicts']) is expected
    if expected:
        assert all(r['applicability_state']=='conflict' for r in fb['annotations'].values())
        assert unmatched
    node="const fs=require('fs');eval(fs.readFileSync(process.argv[1],'utf8'));let a=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(QtoReviewCore.parse(a.p,a.v)));"
    r=subprocess.run(['node','-e',node,str(ROOT/'skills/_qto-shared-evidence/assets/review/review-core.js')],input=json.dumps({'p':package,'v':n},ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',check=True)
    js=json.loads(r.stdout)
    assert js['decision_conflicts']==fb['decision_conflicts']
    h=handoff(n,package['annotations'])
    if expected:assert h['item_actions']==[] and h['issue_actions']==[]
    else:assert fb['annotations']['问题组|DG']['applicability_state']=='current' and h['item_actions']
