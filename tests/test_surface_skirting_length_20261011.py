"""Source-bound skirting lengths, interval ownership and dimensional separation."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'skills/qto-surfaces-envelope/scripts'

def module(name):
    spec=importlib.util.spec_from_file_location('skirting_'+name,S/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

c=module('calculate_surfaces_envelope')
v=module('validate_surfaces_envelope_result')


def make_document(folder):
    folder.mkdir(parents=True,exist_ok=True)
    facts={'edges':{'a':{'edge_id':'a','start':[0,0],'end':[10,0],'unit':'m','effective_intervals':[[1,9]]},
                    'b':{'edge_id':'b','start':[10,0],'end':[10,4],'unit':'m'}},
           'openings':{'o1':{'opening_id':'o1','edge_id':'a','interval':[2,4],'unit':'m'},
                       'o2':{'opening_id':'o2','edge_id':'a','interval':[3,5],'unit':'m'}}}
    path=folder/'source-facts.json';path.write_text(json.dumps(facts),encoding='utf8')
    pdf=folder/'synthetic.pdf';pdf.write_bytes(b'%PDF-1.4\nSynthetic test source, not a real drawing.\n')
    def source(identifier,p,role,file_role):
        return dict(source_id=identifier,path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),file_role=file_role,source_role=role,source_type='synthetic',selection='selected',reason='test',neutral_facts='explicit synthetic geometry',not_used_impact='none')
    evidence=[]
    for eid,cls,src,keys in [('EV-I','source_identity','SRC-F',['face']),('EV-GA','surface_geometry','SRC-F',['face','a']),('EV-GB','surface_geometry','SRC-F',['face','b']),
       ('EV-O1','opening_union','SRC-F',['face','a','o1']),('EV-O2','opening_union','SRC-F',['face','a','o2']),('EV-O','opening_union','SRC-F',['face']),
       ('EV-L','layer_assignment','SRC-P',['face','layer']),('EV-C','coverage_complete','SRC-C',['face'])]:
        evidence.append(dict(evidence_id=eid,source_id=src,evidence_class=cls,fact_status='test_only',locator='synthetic:'+eid,fact='Synthetic source-bound facts; no project baseline',extraction='test',quality='synthetic',object_keys=keys))
    item=dict(item_id='QTY-S',name='踢脚有效边长',object_type='skirting_finish',physical_surface_id='face',layer_id='layer',side_scope='inner',measurement_basis='design_boundary',
       method='boundary_intervals_net',unit='m',scope_status='complete',adoption_role='reference',quantity_role='physical_length',result_use='test',evidence_refs=[e['evidence_id'] for e in evidence],
       opening_union_evidence_ref='EV-O',coverage_evidence_ref='EV-C',rule_refs=['R-1'],unresolved_refs=[],
       inputs={'boundary_edges':[{'edge_id':'a','geometry_evidence_ref':'EV-GA','source_pointer':'/edges/a','effective_scope':'source_intervals'}],
               'openings':[{'opening_id':key,'edge_id':'a','opening_evidence_ref':ref,'source_pointer':'/openings/'+key} for key,ref in [('o1','EV-O1'),('o2','EV-O2')]]})
    document=dict(manifest_version=c.MANIFEST_VERSION,task=dict(project_id='synthetic',business_discipline='surfaces',main_objects='skirting',system='finish',building='B',zone='Z',drawing_type='synthetic',drawing_version='1',output_dimension='length_and_area_separately',applicable_rule_status='unresolved'),
       sources=[source('SRC-F',path,'calculation_evidence','json'),source('SRC-P',pdf,'project_drawing','pdf'),source('SRC-C',pdf,'coverage_report','pdf')],evidence=evidence,
       rules=[dict(rule_id='R-1',name='source interval union',source='synthetic geometry',decision='试用',maturity='候选',execution='reference',evidence_locator='synthetic')],unresolved=[],items=[item])
    return document,facts


def update_facts(document,facts):
    source=document['sources'][0];p=Path(source['path'])
    p.write_text(json.dumps(facts),encoding='utf8');source['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()


def run(document,folder):
    path=folder/'manifest.json';path.write_text(json.dumps(document,ensure_ascii=False),encoding='utf8')
    return c.calculate_from_path(path)


@pytest.fixture
def setup(tmp_path):
    document,facts=make_document(tmp_path)
    return document,facts,tmp_path


def test_source_edge_effective_intervals_and_opening_union_full_contract(setup):
    doc,_,folder=setup;result=run(doc,folder)
    item=result['工程量结果'][0];audit=item['专业检查']['输入审计']
    assert item['结果']['数值']==5 and item['单位']=='m'
    assert audit['gross_length_m']==8 and audit['opening_union_length_m']==3
    edge=audit['boundary_edges'][0]
    assert edge['opening_union_intervals_m']==[[2,5]] and edge['opening_overlap_removed_m']==1
    assert edge['source_binding']['source_sha256']==doc['sources'][0]['sha256']
    assert v.validate_document(result)==[]


@pytest.mark.parametrize('source_unit,factor',[('m',1),('cm',100),('mm',1000),('ft',1/.3048)])
def test_physical_units_migrate_without_quantity_factor(setup,source_unit,factor):
    doc,facts,folder=setup
    for edge in facts['edges'].values():
        edge['start']=[p*factor for p in edge['start']];edge['end']=[p*factor for p in edge['end']];edge['unit']=source_unit
        if 'effective_intervals' in edge:edge['effective_intervals']=[[p*factor for p in pair] for pair in edge['effective_intervals']]
    for op in facts['openings'].values():op['interval']=[p*factor for p in op['interval']];op['unit']=source_unit
    update_facts(doc,facts)
    assert run(doc,folder)['工程量结果'][0]['结果']['数值']==pytest.approx(5)


def test_reversed_edge_with_mirrored_intervals_same_quantity(setup):
    doc,facts,folder=setup;e=facts['edges']['a'];e['start'],e['end']=e['end'],e['start']
    e['effective_intervals']=[[10-b,10-a] for a,b in e['effective_intervals']]
    for op in facts['openings'].values():a,b=op['interval'];op['interval']=[10-b,10-a]
    update_facts(doc,facts)
    assert run(doc,folder)['工程量结果'][0]['结果']['数值']==5


def test_openings_clip_to_disjoint_effective_intervals(setup):
    doc,facts,folder=setup;facts['edges']['a']['effective_intervals']=[[0,2],[4,6],[5,6]]
    facts['openings']['o1']['interval']=[-1,1];facts['openings']['o2']['interval']=[1,5]
    update_facts(doc,facts);item=run(doc,folder)['工程量结果'][0]
    assert item['结果']['数值']==1
    audit=item['专业检查']['输入审计']['boundary_edges'][0]
    assert audit['effective_intervals_m']==[[0,2],[4,6]]
    assert audit['effective_interval_overlap_removed_m']==1
    assert audit['opening_union_intervals_m']==[[0,2],[4,5]]


def test_adjacent_edges_do_not_share_opening_chainage(setup):
    doc,facts,folder=setup;i=doc['items'][0]
    i['inputs']['boundary_edges'].append({'edge_id':'b','geometry_evidence_ref':'EV-GB','source_pointer':'/edges/b','effective_scope':'whole_edge'})
    result=run(doc,folder)['工程量结果'][0]
    assert result['结果']['数值']==9
    assert result['专业检查']['输入审计']['boundary_edges'][1]['opening_union_length_m']==0


def test_distinct_opening_ids_same_interval_not_deducted_twice(setup):
    doc,facts,folder=setup;facts['openings']['o2']['interval']=[2,4];update_facts(doc,facts)
    assert run(doc,folder)['工程量结果'][0]['结果']['数值']==6


@pytest.mark.parametrize('change',['row_unit','area_role','wrong_object','area_method','source_unit','nan','boolean','zero_edge','reversed_interval','effective_outside',
    'duplicate_edge_id','overlap_edge_alias','duplicate_opening_id','opening_wrong_edge','opening_unselected_edge','missing_openings','missing_coverage','missing_union',
    'missing_geometry_evidence','wrong_geometry_object','wrong_opening_object','wrong_pointer','unbound_numeric_total','edge_payload_override','changed_source','evidence_class'])
def test_bad_geometry_units_identity_and_sources_rejected(setup,change):
    doc,facts,folder=setup;i=doc['items'][0];inputs=i['inputs']
    if change=='row_unit':i['unit']='m²'
    elif change=='area_role':i['quantity_role']='physical_area'
    elif change=='wrong_object':i['object_type']='floor_finish'
    elif change=='area_method':i['method']='area_direct';i['inputs']={'area_m2':5}
    elif change=='source_unit':facts['edges']['a']['unit']='m²'
    elif change=='nan':facts['edges']['a']['end'][0]=float('nan')
    elif change=='boolean':facts['edges']['a']['end'][0]=True
    elif change=='zero_edge':facts['edges']['a']['end']=[0,0]
    elif change=='reversed_interval':facts['openings']['o1']['interval']=[4,2]
    elif change=='effective_outside':facts['edges']['a']['effective_intervals']=[[1,11]]
    elif change=='duplicate_edge_id':inputs['boundary_edges']*=2
    elif change=='overlap_edge_alias':
        facts['edges']['b']['start']=[5,0];facts['edges']['b']['end']=[10,0]
        inputs['boundary_edges'].append({'edge_id':'b','geometry_evidence_ref':'EV-GB','source_pointer':'/edges/b','effective_scope':'whole_edge'})
    elif change=='duplicate_opening_id':inputs['openings']*=2
    elif change=='opening_wrong_edge':inputs['openings'][0]['edge_id']='b'
    elif change=='opening_unselected_edge':
        facts['openings']['o1']['edge_id']='b';inputs['openings'][0]['edge_id']='b';doc['evidence'][3]['object_keys']=['face','b','o1']
    elif change=='missing_openings':inputs.pop('openings')
    elif change=='missing_coverage':i.pop('coverage_evidence_ref')
    elif change=='missing_union':i.pop('opening_union_evidence_ref')
    elif change=='missing_geometry_evidence':i['evidence_refs'].remove('EV-GA')
    elif change=='wrong_geometry_object':doc['evidence'][1]['object_keys']=['face','b']
    elif change=='wrong_opening_object':doc['evidence'][3]['object_keys']=['face','a','wrong-opening']
    elif change=='wrong_pointer':inputs['boundary_edges'][0]['source_pointer']='/edges/missing'
    elif change=='unbound_numeric_total':inputs['net_length_m']=5
    elif change=='edge_payload_override':inputs['boundary_edges'][0]['end']=[99,0]
    elif change=='evidence_class':doc['evidence'][1]['evidence_class']='opening_union'
    update_facts(doc,facts)
    if change=='changed_source':Path(doc['sources'][0]['path']).write_text('{}',encoding='utf8')
    with pytest.raises(c.CalculationError):run(doc,folder)


def test_zero_length_requires_complete_coverage_and_declared_meaning(setup):
    doc,facts,folder=setup;facts['openings']['o1']['interval']=[-1,11];update_facts(doc,facts)
    with pytest.raises(c.CalculationError,match='zero_meaning'):run(doc,folder)
    doc['items'][0]['zero_meaning']='all selected eligible boundary covered by validated openings'
    assert run(doc,folder)['工程量结果'][0]['结果']['数值']==0


def test_length_and_area_same_finish_are_distinct_results_and_subtotals(setup):
    doc,_,folder=setup
    area=copy.deepcopy(doc['items'][0]);area.update(item_id='QTY-A',method='area_direct',unit='m²',quantity_role='physical_area',inputs={'area_m2':2.5})
    doc['items'].append(area)
    out=run(doc,folder)
    assert {s['单位'] for s in out['工程量小计']}=={'m','m²'} and len(out['工程量小计'])==2
    assert len({r['专业检查']['对象身份签名'] for r in out['工程量结果']})==2
    assert v.validate_document(out)==[]
    out['工程量小计'][0]['来源成果项'].append('QTY-S' if out['工程量小计'][0]['单位']=='m²' else 'QTY-A')
    assert any('不同单位' in message for message in v.validate_document(out))


def test_deferred_skirting_length_is_null_not_zero(setup):
    doc,_,folder=setup;i=doc['items'][0]
    doc['unresolved']=[dict(unresolved_id='U-H',type='缺资料',description='opening extent unavailable',impact='skirting length',required_input='source opening extent',action='保留已证实部分')]
    i.update(method='deferred',scope_status='unresolved',unresolved_refs=['U-H'],inputs={},deferred_reason='source opening range unavailable')
    out=run(doc,folder)
    assert out['工程量结果'][0]['结果']['数值'] is None
    assert v.validate_document(out)==[]


def test_one_physical_opening_can_cross_two_nonoverlapping_collinear_edges(setup):
    doc,facts,folder=setup;i=doc['items'][0]
    facts['edges']['a']={'edge_id':'a','start':[0,0],'end':[5,0],'unit':'m'}
    facts['edges']['b']={'edge_id':'b','start':[5,0],'end':[10,0],'unit':'m'}
    facts['openings']={'o1':{'opening_id':'door','edge_id':'a','interval':[4,6],'unit':'m'},
                       'o2':{'opening_id':'door','edge_id':'b','interval':[-1,1],'unit':'m'}}
    i['inputs']['boundary_edges'][0]['effective_scope']='whole_edge'
    i['inputs']['boundary_edges'].append({'edge_id':'b','geometry_evidence_ref':'EV-GB','source_pointer':'/edges/b','effective_scope':'whole_edge'})
    for item,edge in zip(i['inputs']['openings'],['a','b']):item.update(opening_id='door',edge_id=edge)
    doc['evidence'][3]['object_keys']=['face','a','door'];doc['evidence'][4]['object_keys']=['face','b','door']
    update_facts(doc,facts)
    result=run(doc,folder)['工程量结果'][0]
    assert result['结果']['数值']==8
    assert [x['opening_union_length_m'] for x in result['专业检查']['输入审计']['boundary_edges']]==[1,1]
