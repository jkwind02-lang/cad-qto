"""R2 anonymous physical-source/outlet identity regressions; no real human acceptance."""
import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parent))
import test_rule_object_producers_20261019 as objtests
import test_rule_candidates_20261019 as common
from qto_rule_candidates import export_candidate, review_candidate, ref
from qto_review_contract import normalize_view, empty_feedback
from qto_review_identity import fingerprint, snapshot

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def refresh(rp,r,raw,*,confirm):
    """Refresh synthetic current view and original feedback; only legal scopes get a new test confirmation."""
    directory=rp.parent
    keys=[o['catalog_key']for o in raw['objects']]
    d=raw['review_workflow']['decisions'][0]
    d['scope']['member_keys']=keys;d['refs']=[{'kind':'object','id':k}for k in keys]
    vp=common.write(directory/'R2当前视图.json',raw);view=normalize_view(raw);d=view['review_workflow']['decisions'][0]
    for spec in r['object_producer_evidence']:
        for m in spec['bindings']:
            m['object_fingerprint']=next(o['result_fingerprint']for o in view['objects']if o['catalog_key']==m['catalog_key'])
    annotation=copy.deepcopy(load(r['feedback']['path'])['annotations'][common.KEY])
    annotation['decision_snapshot']=snapshot(d)
    feedback=empty_feedback(view);feedback['annotations'][common.KEY]=annotation
    fp=common.write(directory/'R2原保存匿名反馈.json',feedback)
    def handoff(index):
        hp=directory/('R2反馈交接'+str(index)+'.json')
        common.cli(SHARED/'read_qto_review_feedback.py',['--view',vp,'--feedback',fp,'--output',hp],directory)
        r.update(view=ref(vp),feedback=ref(fp),handoff=ref(hp));common.write(rp,r)
    handoff(1)
    if confirm:
        preview=export_candidate(rp,preview=True)
        annotation['method_context_fingerprint']=preview['method_context_fingerprint'];common.write(fp,feedback);handoff(2)
        evidence=load(r['human_decision_evidence']['path']);record=evidence['records'][0]
        record.update(annotation_fingerprint=fingerprint(annotation),decision_id=d['id'],decision_revision=d['revision'],
                      source_fingerprint=view['source_sha256'],method_context_fingerprint=preview['method_context_fingerprint'],
                      result_fingerprint=view['result_fingerprint'],decision_result_fingerprint=d['result_fingerprint'],
                      source_feedback=ref(fp,annotation_key=common.KEY,saved_at=common.TIME))
        ep=common.write(directory/'R2匿名方法凭据.json',evidence);r['human_decision_evidence']=ref(ep,record_id=record['record_id'])
    return common.write(rp,r)


def alternate_exchange(directory,spec):
    manifest=load(spec['manifest']['path']);old=Path(manifest.get('exchange_dxf',manifest['authoritative_source'])['path'])
    import ezdxf
    alternate=directory/'仅额外交换字节不同.dxf'
    document=ezdxf.readfile(old);document.header['$LASTSAVEDBY']='R2 anonymous exchange metadata';document.saveas(alternate)
    assert ref(alternate)['sha256']!=ref(old)['sha256']
    manifest['exchange_dxf']=ref(alternate)
    mp=common.write(directory/'同authority另一交换声明.json',manifest)
    changed=copy.deepcopy(spec);changed.update(producer_id='PRODUCER-SECOND',manifest=ref(mp))
    return changed


@pytest.mark.parametrize('cross_producer',[False,True])
@pytest.mark.parametrize('different_exchange',[False,True])
def test_point_same_outlet_different_display_names_never_duplicate(tmp_path,cross_producer,different_exchange):
    rp=objtests.fixture(tmp_path/'项目');r=load(rp);raw=load(r['view']['path']);first=raw['objects'][0]
    second=copy.deepcopy(first);second.update(id='P2',catalog_key='点位|同物另一显示对象')
    second['quantities'][0]['name']='换标签仍是同一实例数';raw['objects'].append(second)
    s=r['object_producer_evidence'][0];m=copy.deepcopy(s['bindings'][0]);m.update(catalog_key=second['catalog_key'],quantity_name=second['quantities'][0]['name'])
    if cross_producer:
        extra=alternate_exchange(rp.parent,s)if different_exchange else copy.deepcopy(s)
        extra['producer_id']='PRODUCER-SECOND';extra['bindings']=[m];r['object_producer_evidence'].append(extra)
    else:s['bindings'].append(m)
    refresh(rp,r,raw,confirm=False)
    for action in ('inspect','export'):
        common.cli(SHARED/'qto_rule_candidates.py',[action,'--request',rp,'--maint-dir',tmp_path/action,'--name','拒收.json'],rp.parent,expected=2)
        receipt=load(tmp_path/action/'规则候选/拒收.json')
        assert '重复'in receipt['error']


def test_point_same_name_same_authority_alternate_exchange_still_duplicate(tmp_path):
    rp=objtests.fixture(tmp_path/'项目');r=load(rp);raw=load(r['view']['path']);second=copy.deepcopy(raw['objects'][0]);second.update(id='P2',catalog_key='点位|同物再认领');raw['objects'].append(second)
    extra=alternate_exchange(rp.parent,r['object_producer_evidence'][0]);extra['bindings'][0]['catalog_key']=second['catalog_key'];r['object_producer_evidence'].append(extra)
    refresh(rp,r,raw,confirm=False)
    with pytest.raises(ValueError,match='重复映射'):export_candidate(rp,preview=True)


@pytest.mark.parametrize('kind',['point','bound'])
def test_one_target_display_identity_is_separate_from_producer_owned_outlet(tmp_path,kind):
    rp=objtests.fixture(tmp_path/'项目',kind);c=export_candidate(rp);outlet=c['origin']['bindings'][0]['quantity_outlet']
    assert outlet['authority_sha256']==outlet['source_version_sha256']
    assert outlet['outlet'].startswith('point.unique_'if kind=='point'else'electrical.quantity_projection.')
    assert 'quantity_name'not in outlet and 'producer_id'not in outlet and 'source_hashes'not in outlet
    assert c['producer_authority_sources'][0]['sha256']==outlet['authority_sha256']


def test_different_real_targets_same_authority_are_legal(tmp_path):
    rp=objtests.fixture(tmp_path/'项目',placements=[('LAMP',1000,1000),('LAMP',3000,1000)])
    r=load(rp);raw=load(r['view']['path']);s=r['object_producer_evidence'][0]
    targets=load(s['count_input']['path'])['reconciled_instances'];assert len(targets)==2
    second=copy.deepcopy(raw['objects'][0]);second.update(id='P2',catalog_key='点位|实际第二实例',points=[targets[1]['point'][:2]])
    raw['objects'].append(second)
    m=copy.deepcopy(s['bindings'][0]);m.update(catalog_key=second['catalog_key'],target_id=targets[1]['physical_id'],target_fingerprint=fingerprint(targets[1]));s['bindings'].append(m)
    refresh(rp,r,raw,confirm=True)
    c=export_candidate(rp);b=c['origin']['bindings']
    assert b[0]['quantity_outlet']['authority_sha256']==b[1]['quantity_outlet']['authority_sha256']
    assert b[0]['quantity_outlet_fingerprint']!=b[1]['quantity_outlet_fingerprint']
    common.candidate(rp,tmp_path/'合法导出')


def test_different_authorities_same_handle_are_legal(tmp_path):
    rp=objtests.fixture(tmp_path/'项目');r=load(rp);raw=load(r['view']['path']);s=r['object_producer_evidence'][0]
    extra,obj,target,ident,qname,unit,source=objtests.producer(tmp_path/'第二物理源','point','匿名生产甲')
    assert ident==s['bindings'][0]['target_id'] and source['sha256']!=r['source_files'][0]['sha256']
    source['id']='SRC2';r['source_files'].append(source);raw['sources'].append({'id':'SRC2','title':'另一实际authority','sha256':source['sha256']})
    raw['scenes'].append(dict(raw['scenes'][0],id='PLAN2',source_id='SRC2'))
    obj.update(id='P2',catalog_key='点位|另一authority同句柄',scene_id='PLAN2');raw['objects'].append(obj)
    extra.update(producer_id='SECOND-AUTHORITY',bindings=[{'catalog_key':obj['catalog_key'],'target_id':ident,'target_fingerprint':fingerprint(target),'quantity_name':qname}]);r['object_producer_evidence'].append(extra)
    refresh(rp,r,raw,confirm=True);c=export_candidate(rp)
    assert len({b['quantity_outlet_fingerprint']for b in c['origin']['bindings']})==2
    common.candidate(rp,tmp_path/'合法导出')


@pytest.mark.parametrize('cross_producer',[False,True])
def test_electrical_distinct_official_projection_outlets_remain_legal(tmp_path,cross_producer):
    rp=objtests.fixture(tmp_path/'项目','bound');r=load(rp);raw=load(r['view']['path']);s=r['object_producer_evidence'][0]
    p=load(s['package']['path']);t=p['objects'][0]
    q=objtests.bound.binding.quantity_projection(t,p['quantity_result']['items'][0],p['ledger']['circuits'][0])['quantities'][1]
    raw['objects'][0]['quantities'].append(q)
    m=copy.deepcopy(s['bindings'][0]);m['quantity_name']=q['name']
    if cross_producer:
        extra=copy.deepcopy(s);extra.update(producer_id='SECOND-PROJECTION',bindings=[m]);r['object_producer_evidence'].append(extra)
    else:s['bindings'].append(m)
    refresh(rp,r,raw,confirm=True);c=export_candidate(rp);b=c['origin']['bindings']
    assert b[0]['quantity_outlet']['physical_instance']==b[1]['quantity_outlet']['physical_instance']
    assert len({v['quantity_outlet']['outlet']for v in b})==2
    common.candidate(rp,tmp_path/'合法不同投影导出')


def test_same_authority_new_exchange_and_labels_do_not_make_independent_case(tmp_path):
    ap=objtests.fixture(tmp_path/'甲');a=load(ap)
    bp=objtests.fixture(tmp_path/'乙',project='匿名生产乙');b=load(bp);raw=load(b['view']['path'])
    extra=alternate_exchange(bp.parent,a['object_producer_evidence'][0]);b['object_producer_evidence']=[extra]
    # Deliberately change the aggregate view source fingerprint while preserving the actual count authority.
    aux=bp.parent/'另一视图附加说明.txt';aux.write_text('另标签和额外资料不改变实际物理authority',encoding='utf-8')
    b['source_files']=[copy.deepcopy(a['source_files'][0]),ref(aux,id='AUX')]
    raw['sources']=[{'id':'SRC','title':'同物不同标签','sha256':a['source_files'][0]['sha256']},{'id':'AUX','title':'额外说明','sha256':ref(aux)['sha256']}]
    raw['objects'][0]['catalog_key']='点位|换显示标签仍同物';extra['bindings'][0]['catalog_key']=raw['objects'][0]['catalog_key']
    refresh(bp,b,raw,confirm=True)
    ac=common.candidate(ap,tmp_path/'甲维护');bc=common.candidate(bp,tmp_path/'乙维护')
    assert load(ac)['source_fingerprint']!=load(bc)['source_fingerprint']
    assert load(ac)['producer_authority_sources'][0]['sha256']==load(bc)['producer_authority_sources'][0]['sha256']
    rr=common.review_request(tmp_path/'匿名维护',ac,'general_candidate',[bc])
    common.cli(ROOT/'maintenance/review_rule_candidates.py',['review','--request',rr,'--maint-dir',tmp_path/'维护拒收','--name','拒收.json'],rr.parent,expected=2)
    assert '另项目、另物理源'in load(tmp_path/'维护拒收/规则候选/拒收.json')['error']
