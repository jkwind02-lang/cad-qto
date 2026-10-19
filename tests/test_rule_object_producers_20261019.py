"""Anonymous actual point/binding CLI fixtures; no real human or quantity acceptance."""
import copy
import json
import shutil
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent))
import test_rule_candidates_20261019 as common
import test_point_colocation_20261015 as points
import test_electrical_object_binding_20261018 as bound
from qto_rule_object_adapter import modules, SCHEMA, replay
from qto_rule_candidates import export_candidate, ref, read_ref, review_candidate
from qto_review_contract import normalize_view, empty_feedback
from qto_review_identity import fingerprint, snapshot

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'


def producer(directory, kind, project, unknown=False, placements=None):
    directory.mkdir(parents=True)
    if kind == 'point':
        ids, result, out = points.build(directory/'原源', placements=placements or [('LAMP',1000,1000)])
        assert result.returncode == 0, result.stdout+result.stderr
        data=json.loads((out/'点位唯一实例计数输入.json').read_text(encoding='utf-8'))
        target=data['reconciled_instances'][0]
        spec={'schema':SCHEMA,'producer':'reconciled_points/1.0',
              'manifest':ref(directory/'原源/neutral/CAD中性取证清单.json'),
              'profile':ref(directory/'原源/profile.json'),'recognition_ledger':ref(directory/'原源/ledger.json'),
              'count_input':ref(out/'点位唯一实例计数输入.json'),'count_result':ref(out/'点位数量汇总.json')}
        obj={'id':'P1','catalog_key':'点位|实例甲','professional':'qto-components-points','kind':'point',
             'scene_id':'PLAN','title':'匿名物理点位','category':'灯具','status':'规则推算','detail':'匿名实际生产夹具',
             'points':[target['point'][:2]],'source':'原图实例','quantities':[
                 {'name':'唯一实例','value':1,'unit':target['unit'],'level':target['quantity_level']}]}
        unit=target['coordinate_unit'];target_id=target['physical_id'];qname='唯一实例'
    else:
        manifest,request,*_=bound.fixture(directory/'原源')
        request['project_id']=project
        if unknown:
            request['objects'][0].pop('component_evidence')
        rp=common.write(directory/'绑定请求.json',request)
        out=directory/'绑定产物'
        common.cli(ROOT/'skills/qto-electrical-routes/scripts/bind_electrical_objects.py',
                   ['--cad-manifest',manifest,'--request',rp,'--output-dir',out],directory,expected=3 if unknown else 0)
        package=json.loads((out/'电气对象包.json').read_text(encoding='utf-8'))
        target=package['objects'][0]
        q=bound.binding.quantity_projection(target, package['quantity_result']['items'][0],package['ledger']['circuits'][0])['quantities'][1 if unknown else 0]
        obj={'id':'B1','catalog_key':target['catalog_key'],'professional':'qto-electrical-routes','kind':'line',
             'scene_id':'PLAN','title':'匿名绑定路径','category':'电气线路','status':'规则推算','detail':'匿名实际生产夹具',
             'points':[[0,0],[10,0]],'source':'原图路径','quantities':[q]}
        spec={'schema':SCHEMA,'producer':'bound_electrical_objects/1.0','manifest':ref(manifest),
              'request':ref(rp),'package':ref(out/'电气对象包.json'),'ledger':ref(out/'路径台账.json'),
              'calculation_run':ref(out/'数量复算.json')}
        unit='m';target_id=target['id'];qname=q['name']
    spec.update(producer_id='PRODUCER',decision_id='METHOD',decision_revision='1')
    spec['producer_modules']={identity:ref(ROOT/identity) for identity in modules(spec['producer'])}
    source_path=Path(json.loads(Path(spec['manifest']['path']).read_text(encoding='utf-8'))['authoritative_source']['path'])
    source=ref(source_path,id='SRC')
    return spec,obj,target,target_id,qname,unit,source


def fixture(directory, kind='point', project='匿名生产甲', unknown=False, placements=None):
    spec,obj,target,ident,qname,unit,source=producer(directory,kind,project,unknown=unknown,placements=placements)
    decision={'id':'METHOD','revision':'1','title':'生产方法','question':'是否保留逐物理对象方法与未决边界？',
              'kind':'business_choice','scope':{'member_keys':[obj['catalog_key']], 'excluded_member_keys':[],
              'basis':'仅该实际生产对象，不代表完整工程量'},'refs':[{'kind':'object','id':obj['catalog_key']}],
              'business_options':[{'id':'keep','title':'保留方法'}]}
    raw={'schema':'qto-review-view/1.0','project_id':project,'review_id':'匿名生产审核','title':'匿名生产审核',
         'revision':'1','sources':[{'id':'SRC','title':'匿名原图','sha256':source['sha256']}],
         'scenes':[{'id':'PLAN','title':'匿名平面','source_id':'SRC','coordinate_unit':unit,'bounds':[-1,-1,12,12]}],
         'objects':[obj], 'review_workflow':{'schema':'qto-review-workflow/1.0','enabled':True,'migrate_legacy':False,'decisions':[decision]}}
    vp=common.write(directory/'视图.json',raw);view=normalize_view(raw)
    d=view['review_workflow']['decisions'][0]
    spec['bindings']=[{'catalog_key':obj['catalog_key'],'target_id':ident,'target_fingerprint':fingerprint(target),
                       'object_fingerprint':view['objects'][0]['result_fingerprint'],'quantity_name':qname}]
    annotation={'decision_snapshot':snapshot(d),'action':'调整处理','business_option_id':'keep',
                'confirmed_result':'保留方法，数量继续未批准','reason':'匿名合成实际生产结果',
                'user_note':'匿名测试非真实凭据','exception_of':None,'excluded_member_keys':[],'history':[],
                'updated_at':common.TIME,'reviewer':common.REVIEWER,'reviewer_type':'human','reviewed_at':common.TIME,
                'decision_type':'method_confirmation','fixture_kind':'anonymous_synthetic_not_real_human'}
    feedback=empty_feedback(view);feedback['annotations'][common.KEY]=annotation
    fp=common.write(directory/'批注.json',feedback);hp=directory/'交接.json'
    def handoff():
        nonlocal hp
        hp=directory/('交接'+str(len(list(directory.glob('交接*.json'))))+'.json')
        common.cli(SHARED/'read_qto_review_feedback.py',['--view',vp,'--feedback',fp,'--output',hp],directory)
    handoff()
    request={'schema':'qto-rule-candidate-request/1.0','view':ref(vp),'feedback':ref(fp),'handoff':ref(hp),
             'source_files':[source],'annotation_key':common.KEY,'object_producer_evidence':[spec], 'draft':common.draft()}
    request['draft']['applicability'].update(professional=obj['professional'],object_kind=obj['kind'],object_type=obj['category'],
                                           quantity_layer=obj['quantities'][0]['level'],unit=obj['quantities'][0]['unit'])
    if kind=='point':
        request['draft']['rule_family']='逐物理点位';request['draft']['proposed_rule']='仅按原源唯一物理实例计数，未决覆盖另列'
        request['draft']['suggested_reference']='skills/qto-components-points/references/点位方法候选.md'
    rp=common.write(directory/'候选请求.json',request)
    preview=export_candidate(rp,preview=True)
    annotation['method_context_fingerprint']=preview['method_context_fingerprint'];common.write(fp,feedback);handoff()
    evidence={'schema':'qto-rule-decision-evidence/1.0','project_id':project,'records':[{
        'record_id':'METHOD','reviewer':common.REVIEWER,'reviewer_type':'human','reviewed_at':common.TIME,
        'decision':'confirm_method_decision','annotation_key':common.KEY,'annotation_fingerprint':fingerprint(annotation),
        'decision_id':d['id'],'decision_revision':d['revision'],'source_fingerprint':view['source_sha256'],
        'method_context_fingerprint':preview['method_context_fingerprint'],'result_fingerprint':view['result_fingerprint'],
        'decision_result_fingerprint':d['result_fingerprint'],'source_feedback':ref(fp,annotation_key=common.KEY,saved_at=common.TIME)}]}
    ep=common.write(directory/'匿名方法凭据.json',evidence)
    request.update(feedback=ref(fp),handoff=ref(hp),human_decision_evidence=ref(ep,record_id='METHOD'))
    return common.write(rp,request)


@pytest.mark.parametrize('kind',['point','bound'])
@pytest.mark.parametrize('branch',['reject','project_only','general_candidate'])
def test_actual_named_producers_export_maintenance(tmp_path,kind,branch):
    rp=fixture(tmp_path/'项目甲',kind)
    cp=common.candidate(rp,tmp_path/'维护甲')
    other=common.candidate(fixture(tmp_path/'项目乙',kind,'匿名生产乙'),tmp_path/'维护乙') if branch=='general_candidate' else None
    rr=common.review_request(tmp_path/'审核输入',cp,branch,[other]if other else [])
    common.cli(ROOT/'maintenance/review_rule_candidates.py',['review','--request',rr,'--maint-dir',tmp_path/'审核输出','--name','结果.json'],rr.parent)
    result=json.loads((tmp_path/'审核输出/规则候选/结果.json').read_text(encoding='utf-8'))
    assert result['disposition']==branch and result['business_adoption_allowed'] is False
    assert ('maintenance_proposal'in result)==(branch=='general_candidate')
    c=json.loads(cp.read_text(encoding='utf-8'))
    assert c['producer_source_files'] and c['origin']['bindings'][0]['source_target']
    assert c['origin']['bindings'][0]['review_state']=='method_evidence_required_not_quantity_approval'


@pytest.mark.parametrize('kind',['point','bound'])
@pytest.mark.parametrize('change',['target','product','module_set','module_bytes','source','method_context','quantity','scope'])
def test_reject_drift_and_forged_mapping(tmp_path,kind,change):
    rp=fixture(tmp_path/'项目',kind);r=json.loads(rp.read_text(encoding='utf-8'));s=r['object_producer_evidence'][0]
    if change=='target':s['bindings'][0]['target_fingerprint']='0'*64
    elif change=='module_set':s['producer_modules'].pop(next(iter(s['producer_modules'])))
    elif change=='module_bytes':
        key=next(iter(s['producer_modules']));p=tmp_path/'复制模块'/key;p.parent.mkdir(parents=True);p.write_bytes(Path(s['producer_modules'][key]['path']).read_bytes()+b'\n# drift\n');s['producer_modules'][key]=ref(p)
    elif change=='source':Path(r['source_files'][0]['path']).write_text('原源已变化',encoding='utf-8')
    elif change=='product':
        key='count_input'if kind=='point'else'package';p=Path(s[key]['path']);v=json.loads(p.read_text(encoding='utf-8'));v['unrecognized_extra']='must not be stripped';common.write(p,v);s[key]=ref(p)
    elif change=='method_context':
        f=Path(r['feedback']['path']);v=json.loads(f.read_text(encoding='utf-8'));v['annotations'][common.KEY]['method_context_fingerprint']='0'*64;common.write(f,v);r['feedback']=ref(f)
    elif change=='quantity':
        p=Path(r['view']['path']);v=json.loads(p.read_text(encoding='utf-8'));v['objects'][0]['quantities'][0]['value']=0;common.write(p,v);r['view']=ref(p)
    else:s['decision_revision']='old'
    common.write(rp,r)
    with pytest.raises(ValueError):export_candidate(rp)


@pytest.mark.parametrize('kind',['point','bound'])
def test_same_byte_modules_other_directory_and_old_original_must_remain_readable(tmp_path,kind):
    rp=fixture(tmp_path/'项目',kind);r=json.loads(rp.read_text(encoding='utf-8'));s=r['object_producer_evidence'][0]
    expected=export_candidate(rp)
    for key,row in s['producer_modules'].items():
        p=tmp_path/'同字节运行副本'/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(Path(row['path']).read_bytes());row.update(ref(p))
    common.write(rp,r)
    actual=export_candidate(rp)
    assert actual['input_fingerprint']==expected['input_fingerprint']
    first=next(iter(s['producer_modules'].values()));Path(first['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):export_candidate(rp)


def test_unreviewed_inspection_cannot_export(tmp_path):
    spec,*_=producer(tmp_path/'项目','point','匿名生产甲')
    rp=common.write(tmp_path/'只读生产请求.json',{'schema':'qto-rule-producer-inspection-request/1.0','project_id':'匿名生产甲','producer_evidence':spec})
    common.cli(SHARED/'qto_rule_candidates.py',['inspect-production','--request',rp,'--maint-dir',tmp_path/'只读结果','--name','生产检查.json'],tmp_path)
    output=json.loads((tmp_path/'只读结果/规则候选/生产检查.json').read_text(encoding='utf-8'))
    assert output['status']=='NOT_HUMAN_APPROVAL' and output['targets']
    assert next(iter(output['targets'].values()))['recognition_record']['review_status']=='unreviewed'
    with pytest.raises(ValueError):export_candidate(rp)


def test_bound_null_and_method_not_quantity_confirmation(tmp_path):
    rp=fixture(tmp_path/'项目','bound',unknown=True)
    c=export_candidate(rp)
    b=c['origin']['bindings'][0]
    assert b['value'] is None and b['source_target']['pending_components']
    assert c['business_adoption_allowed'] is False and c['maturity_changed'] is False


@pytest.mark.parametrize('kind',['point','bound'])
def test_duplicate_target_across_producer_declarations_rejected(tmp_path,kind):
    rp=fixture(tmp_path/'项目',kind);r=json.loads(rp.read_text(encoding='utf-8'))
    extra=copy.deepcopy(r['object_producer_evidence'][0]);extra['producer_id']='SECOND'
    r['object_producer_evidence'].append(extra);common.write(rp,r)
    with pytest.raises(ValueError,match='重复映射'):export_candidate(rp,preview=True)


@pytest.mark.parametrize('kind',['point','bound'])
def test_actual_same_byte_checkout_export_current_maintenance_review(tmp_path,kind):
    rp=fixture(tmp_path/'项目',kind)
    other=tmp_path/'同字节安装运行子树'
    shutil.copytree(ROOT/'skills',other/'skills')
    alt=other/'skills/_qto-shared-evidence/scripts/qto_rule_candidates.py'
    common.cli(alt,['export','--request',rp,'--maint-dir',tmp_path/'副本导出','--name','候选.json'],rp.parent)
    cp=tmp_path/'副本导出/规则候选/候选.json'
    rr=common.review_request(tmp_path/'审核输入',cp,'project_only')
    common.cli(ROOT/'maintenance/review_rule_candidates.py',['review','--request',rr,'--maint-dir',tmp_path/'维护仓库消费','--name','审核.json'],rr.parent)


@pytest.mark.parametrize('kind',['point','bound'])
def test_full_json_document_distinguishes_false_from_zero(tmp_path,kind):
    rp=fixture(tmp_path/'项目',kind);r=json.loads(rp.read_text(encoding='utf-8'));s=r['object_producer_evidence'][0]
    key='count_result'if kind=='point'else'package'
    p=Path(s[key]['path']);doc=json.loads(p.read_text(encoding='utf-8'))
    assert doc['business_adoption_allowed'] is False
    doc['business_adoption_allowed']=0;common.write(p,doc);s[key]=ref(p);common.write(rp,r)
    with pytest.raises(ValueError):export_candidate(rp,preview=True)

@pytest.mark.parametrize('kind',['point','bound'])
def test_actual_reproduced_equal_quantity_changed_input_needs_new_method_context(tmp_path,kind):
    rp=fixture(tmp_path/'项目',kind);r=json.loads(rp.read_text(encoding='utf-8'));s=r['object_producer_evidence'][0]
    old=export_candidate(rp);out=tmp_path/'同量新上下文生产'
    if kind=='point':
        p=Path(s['profile']['path']);d=json.loads(p.read_text(encoding='utf-8'));d['profile_version']='2';common.write(p,d);s['profile']=ref(p)
        common.cli(ROOT/'skills/qto-components-points/scripts/build_component_instances_from_neutral.py',
                   ['--cad-manifest',s['manifest']['path'],'--profile',p,'--adjudication-ledger',s['recognition_ledger']['path'],'--output-dir',out],tmp_path)
        s['count_input']=ref(out/'点位唯一实例计数输入.json');s['count_result']=ref(out/'点位数量汇总.json')
    else:
        p=Path(s['request']['path']);d=json.loads(p.read_text(encoding='utf-8'));d['title']='已改变的方法输入上下文，数值仍相等';common.write(p,d);s['request']=ref(p)
        common.cli(ROOT/'skills/qto-electrical-routes/scripts/bind_electrical_objects.py',
                   ['--cad-manifest',s['manifest']['path'],'--request',p,'--output-dir',out],tmp_path)
        s.update(package=ref(out/'电气对象包.json'),ledger=ref(out/'路径台账.json'),calculation_run=ref(out/'数量复算.json'))
    common.write(rp,r)
    preview=export_candidate(rp,preview=True)
    assert preview['bindings'][0]['value']==old['origin']['bindings'][0]['value']
    assert preview['method_context_fingerprint']!=old['input_fingerprint']
    with pytest.raises(ValueError,match='原方法确认未绑定'):export_candidate(rp)
