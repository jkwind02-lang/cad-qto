"""Interior surfaces: source identity, trial inputs and real geometric deductions."""
import copy,hashlib,importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'skills/qto-surfaces-envelope/scripts'
def module(name):
    spec=importlib.util.spec_from_file_location(name,S/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
c=module('calculate_surfaces_envelope');validator=module('validate_surfaces_envelope_result')

@pytest.fixture
def document(tmp_path):
    p=tmp_path/'synthetic.pdf';p.write_bytes(b'%PDF-1.4\nSynthetic source identity test; not a project drawing.\n')
    source=dict(source_id='SRC-1',path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),file_role='pdf',source_role='project_drawing',source_type='synthetic',selection='selected',reason='test',neutral_facts='synthetic',not_used_impact='none')
    es=[]
    for eid,cls in [('EV-I','source_identity'),('EV-G','surface_geometry'),('EV-L','layer_assignment'),('EV-O','opening_union'),('EV-C','coverage_complete')]:
        es.append(dict(evidence_id=eid,source_id='SRC-1',evidence_class=cls,fact_status='reference',locator='fixture:'+eid,fact='synthetic',extraction='fixture',quality='test',object_keys=['face','layer']))
    es[-1]['source_id']='SRC-COV'
    item=dict(item_id='QTY-1',name='local candidate face',object_type='wall_finish',physical_surface_id='face',layer_id='layer',side_scope='inner',measurement_basis='design_surface',method='rectangle_openings_net',unit='m²',scope_status='partial',adoption_role='reference',quantity_role='physical_area',result_use='test',evidence_refs=['EV-I','EV-G','EV-L','EV-O'],opening_union_evidence_ref='EV-O',rule_refs=['R-1'],unresolved_refs=['U-1'],inputs=dict(length_m=4,height_m=3,openings=[]))
    return dict(manifest_version=c.MANIFEST_VERSION,task=dict(project_id='synthetic',business_discipline='surfaces',main_objects='walls',system='finish',building='B',zone='Z',drawing_type='synthetic',drawing_version='1',output_dimension='area',applicable_rule_status='unresolved'),sources=[source,dict(source,source_id='SRC-COV',source_role='coverage_report')],evidence=es,rules=[dict(rule_id='R-1',name='geometry',source='synthetic',decision='试用',maturity='候选',execution='reference',evidence_locator='fixture')],unresolved=[dict(unresolved_id='U-1',type='范围未定',description='local',impact='local',required_input='coverage',action='保留已证实部分')],items=[item])

def run(d,tmp_path):
    p=tmp_path/'manifest.json';p.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8');return c.calculate_from_path(p)

def opening(key,rect):return dict(opening_id=key,rectangle_m=rect)

def test_pdf_and_candidate_trial_can_produce_reference(document,tmp_path):
    out=run(document,tmp_path);r=out['工程量结果'][0]
    assert r['结果']['数值']==12 and r['专业检查']['进入采用小计'] is False
    assert r['专业检查']['规则成熟状态']==['候选'] and validator.validate_document(out)==[]

@pytest.mark.parametrize('rects,area',[
    ([],0),([[-1,-1,1,1]],1),([[1,1,3,4]],4),([[5,0,6,1]],0),
    ([[0,0,2,2],[1,1,3,3]],7),([[0,0,2,2],[0,0,2,2]],4),
    ([[0,0,4,3],[1,1,2,2]],12),([[0,0,1,1],[1,0,2,1]],2),
])
def test_openings_are_clipped_and_unioned(document,tmp_path,rects,area):
    i=document['items'][0];i['inputs']['openings']=[opening(str(n),r) for n,r in enumerate(rects)]
    if area==12:i.update(scope_status='complete',coverage_evidence_ref='EV-C',zero_meaning='face entirely open');i['evidence_refs'].append('EV-C')
    out=run(document,tmp_path)['工程量结果'][0]
    assert out['结果']['数值']==pytest.approx(12-area)
    assert out['专业检查']['输入审计']['opening_union_area_m2']==pytest.approx(area)

@pytest.mark.parametrize('change',[ 'missing_geometry','wrong_face','wrong_layer','deduction_masked','trial_adopted','disabled_trial',
    'unknown_openings','duplicate_id','nan','boolean','zero_width','backward','two_sides','wrong_union','overflow'])
def test_bad_inputs_rejected(document,tmp_path,change):
    d=document;i=d['items'][0];n=i['inputs']
    if change=='missing_geometry':i['evidence_refs']=['EV-I']
    if change=='wrong_face':d['evidence'][1]['object_keys']=['other']
    if change=='wrong_layer':d['evidence'][2]['object_keys']=['face','other']
    if change=='deduction_masked':n.update(other_deductions_area_m2=13,reveal_or_return_area_m2=20)
    if change=='trial_adopted':i['adoption_role']='adopted'
    if change=='disabled_trial':d['rules'][0]['maturity']='停用'
    if change=='unknown_openings':n.pop('openings')
    if change=='duplicate_id':n['openings']=[opening('a',[0,0,1,1]),opening('a',[1,0,2,1])]
    if change=='nan':n['openings']=[opening('a',[0,0,float('nan'),1])]
    if change=='boolean':n['openings']=[opening('a',[0,0,True,1])]
    if change=='zero_width':n['openings']=[opening('a',[0,0,0,1])]
    if change=='backward':n['openings']=[opening('a',[2,0,1,1])]
    if change=='two_sides':n['side_factor']=2
    if change=='wrong_union':n['opening_union_area_m2']=1
    if change=='overflow':n.update(length_m=1e308,height_m=1e308)
    with pytest.raises(c.CalculationError):run(d,tmp_path)

def test_quantity_roles_kept_separate_for_same_surface(document,tmp_path):
    d=document;d['task']['applicable_rule_status']='confirmed';other=copy.deepcopy(d['items'][0]);other.update(item_id='QTY-2',quantity_role='bill_quantity_candidate');d['items'].append(other)
    out=run(d,tmp_path)
    assert len(out['工程量小计'])==2
    assert {s['数量角色'] for s in out['工程量小计']}=={'physical_area','bill_quantity_candidate'}
    assert validator.validate_document(out)==[]

def test_same_surface_same_quantity_role_duplicate_rejected(document,tmp_path):
    d=document;d['items'].append(dict(d['items'][0],item_id='QTY-2'))
    with pytest.raises(c.CalculationError,match='重复数量'):run(d,tmp_path)

def test_height_variation_clips_window_and_door(document,tmp_path):
    n=document['items'][0]['inputs'];n.update(length_m=3,height_m=2.1,openings=[opening('door',[0,0,.8,2.2]),opening('window',[1,1.8,1.6,2.4])])
    r=run(document,tmp_path)['工程量结果'][0]
    assert r['结果']['数值']==pytest.approx(6.3-1.68-.18)

def test_changed_source_is_rejected(document,tmp_path):
    Path(document['sources'][0]['path']).write_bytes(b'%PDF-1.4 changed')
    with pytest.raises(c.CalculationError,match='哈希'):run(document,tmp_path)

def test_validator_detects_quantity_role_and_duplicate_subtotal_members(document,tmp_path):
    out=run(document,tmp_path);out['工程量小计'][0]['数量角色']='bill_quantity_candidate'
    assert any('数量角色' in s for s in validator.validate_document(out))
    out=run(document,tmp_path);out['工程量小计'][0]['来源成果项']*=2
    assert any('重复引用' in s for s in validator.validate_document(out))


@pytest.mark.parametrize('case',['inapplicable_numeric','partial_zero','direct_ignored_opening'])
def test_numeric_status_and_unused_deductions_not_silently_accepted(document,tmp_path,case):
    i=document['items'][0]
    if case=='inapplicable_numeric':i['scope_status']='not_applicable'
    if case=='partial_zero':
        i['inputs']['openings']=[opening('all',[0,0,4,3])]
        i.update(coverage_evidence_ref='EV-C',zero_meaning='synthetic');i['evidence_refs'].append('EV-C')
    if case=='direct_ignored_opening':i.update(method='area_direct',inputs=dict(area_m2=12,opening_union_area_m2=2))
    with pytest.raises(c.CalculationError):run(document,tmp_path)
