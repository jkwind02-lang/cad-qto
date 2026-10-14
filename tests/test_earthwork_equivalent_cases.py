"""Earthwork calculator: equivalent geometry cases and failure guards; synthetic only.

Migrated from the legacy development source `_qto-shared-evidence/tests/qto-earthwork-terrain/run_equivalent_cases.py`.
Equivalent cases prove the encoded mechanism and guards only; they do not raise any branch maturity.
"""
import copy,hashlib,importlib.util,json,math,re
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=Path(__file__).resolve().parent/'fixtures/earthwork_equivalent/等效案例事实夹具.txt'
spec=importlib.util.spec_from_file_location('qto_earthwork_calculator_cases',ROOT/'skills/qto-earthwork-terrain/scripts/calculate_earthwork_terrain.py')
calculator=importlib.util.module_from_spec(spec);spec.loader.exec_module(calculator)

def base_manifest():
    return {
        "manifest_version": "qto-earthwork-terrain-manifest/1.0",
        "task": {
            "business_discipline": "土方与场地标高", "main_objects": "等效机制对象",
            "system": "测试", "building": "等效夹具", "zone": "局部测试区",
            "drawing_type": "文本夹具", "drawing_version": "当前哈希",
            "output_dimension": "分测试项",
        },
        "sources": [{
            "source_id": "SRC-TEST", "path": str(FIXTURE), "sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "selection": "selected", "source_type": "等效测试夹具", "file_role": "text",
            "source_role": "test_fixture", "reason": "只用于验证确定性机制",
            "neutral_facts": "显式输入尺寸", "not_used_impact": "无法运行等效测试",
        }],
        "evidence": [{
            "evidence_id": "EV-TEST", "source_id": "SRC-TEST", "fact": "测试输入由清单显式给出",
            "locator": "等效案例事实夹具全文", "extraction": "测试清单读取",
            "quality": "test_only/A", "limitation": "不代表真实项目", "evidence_class": "test_fixture",
        }],
        "rules": [{
            "rule_id": "R-TEST-001", "name": "测试几何不变量", "source": "等效案例",
            "decision": "采用", "maturity": "正式", "execution": "仅在等效夹具内执行",
            "evidence_locator": "test_earthwork_equivalent_cases.py",
        }],
        "unresolved": [{
            "unresolved_id": "U-TEST-001", "type": "缺资料", "description": "测试未决项",
            "impact": "数值保持未确定", "required_input": "决定性输入", "action": "停止受影响计算",
        }],
        "items": [],
    }

def item(item_id,method,inputs,*,identity=None,basis="physical_geometry",adoption="adopted",
         earth_state="natural_dense",scope="complete",rule_refs=None,unresolved_refs=None,
         balance_signature="test|physical_geometry|natural_dense"):
    row = {
        "item_id": item_id, "name": f"等效测试{item_id}", "main_object": "显式测试几何",
        "specification": "等效夹具", "object_type": "pit_excavation",
        "quantity_kind": "excavation_volume", "measurement_basis": basis,
        "earth_state": earth_state, "adoption_role": adoption, "scope_status": scope,
        "method": method, "unit": "m³", "identity_signature": identity or item_id,
        "balance_signature": balance_signature, "evidence_refs": ["EV-TEST"],
        "rule_refs": ["R-TEST-001"] if rule_refs is None else rule_refs,
        "unresolved_refs": [] if unresolved_refs is None else unresolved_refs,
        "trial": False, "result_use": "仅验证机制，不提升成熟度", "inputs": inputs,
    }
    if method == "deferred":
        row.update({
            "scope_status": "unresolved", "deferred_status": "证据不足",
            "deferred_reason": "测试输入未提供", "unresolved_refs": ["U-TEST-001"],
            "rule_refs": [] if rule_refs is None else rule_refs,
        })
    return row

def run(manifest,tmp_path):
    p=tmp_path/'manifest.json';p.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return calculator.calculate_from_path(p)

def add(*rows):
    def build(m):m['items'].extend(copy.deepcopy(rows))
    return build

def value(index):return lambda r:r['工程量结果'][index]['结果']['数值']

def candidate_rule(m):
    m['rules'][0]['maturity']='候选';m['items'].append(item('QTY-CANDIDATE','direct_volume',{'volume':1}))

def bad_hash(m):
    m['sources'][0]['sha256']='0'*64;m['items'].append(item('QTY-HASH','direct_volume',{'volume':1}))

def legacy_path(m):
    m['sources'][0]['path']=str(FIXTURE.parent/'.qto'/'methods'/'legacy.txt');m['items'].append(item('QTY-LEGACY','direct_volume',{'volume':1}))

PIT=dict(bottom_length=2,bottom_width=2,depth=1,slope_x=0,slope_y=0)
POSITIVE=[
    ('矩形直壁体',add(item('QTY-PRISM','rectangular_prism',{'length':2,'width':3,'depth':4})),value(0),24.0),
    ('矩形截头体',add(item('QTY-FRUSTUM','rectangular_frustum',{'bottom_length':2,'bottom_width':2,'top_length':4,'top_width':4,'depth':3})),value(0),28.0),
    ('四侧外扩截头体',add(item('QTY-OFFSET','orthogonal_offset_frustum',{'bottom_length':2,'bottom_width':2,'offset_left':1,'offset_right':1,'offset_front':1,'offset_back':1,'depth':3})),value(0),28.0),
    ('坑群重叠并集',add(item('QTY-UNION','pit_union_slices',{'slice_step':0.1,'pits':[dict(PIT,x=0,y=0),dict(PIT,x=0,y=1)]})),value(0),6.0),
    ('未决值保持null',add(item('QTY-DEFERRED','deferred',{})),value(0),None),
    ('同口径差值',add(item('QTY-MINUEND','direct_volume',{'volume':10},balance_signature='same'),
                    item('QTY-SUBTRAHEND','direct_volume',{'volume':4},balance_signature='same'),
                    item('QTY-DIFFERENCE','difference',{'minuend_item':'QTY-MINUEND','subtrahend_item':'QTY-SUBTRAHEND'},balance_signature='same')),value(2),6.0),
]
NEGATIVE=[
    ('负深度拦截',add(item('QTY-NEG','rectangular_prism',{'length':2,'width':2,'depth':-1})),'必须大于0'),
    ('候选规则不得采用',candidate_rule,'候选规则，不得采用'),
    ('方案参考不得采用',add(item('QTY-SCHEME','direct_volume',{'volume':1},basis='scheme_reference',adoption='adopted')),'方案参考量只能是reference'),
    ('不同状态不得平衡',add(item('QTY-A','direct_volume',{'volume':10},balance_signature='state-a'),
                       item('QTY-B','direct_volume',{'volume':4},earth_state='compacted',balance_signature='state-b'),
                       item('QTY-C','difference',{'minuend_item':'QTY-A','subtrahend_item':'QTY-B'},balance_signature='state-a')),'范围、计量基准或土体状态不一致'),
    ('重复对象身份拦截',add(item('QTY-DUP-A','direct_volume',{'volume':1},identity='same'),
                       item('QTY-DUP-B','direct_volume',{'volume':2},identity='same')),'对象身份签名重复'),
    ('来源哈希不匹配',bad_hash,'来源SHA256不匹配'),
    ('旧项目方法路径拦截',legacy_path,'不得引用旧Skill、旧归档或旧项目方法'),
]

@pytest.mark.parametrize('name,build,pick,expected',POSITIVE,ids=[c[0] for c in POSITIVE])
def test_equivalent_geometry(tmp_path,name,build,pick,expected):
    m=base_manifest();build(m);actual=pick(run(m,tmp_path))
    assert actual is None if expected is None else math.isclose(actual,expected)

@pytest.mark.parametrize('name,build,message',NEGATIVE,ids=[c[0] for c in NEGATIVE])
def test_failure_guard_blocks(tmp_path,name,build,message):
    m=base_manifest();build(m)
    with pytest.raises(Exception,match=re.escape(message)):run(m,tmp_path)
