"""Source-bound rectangular substrate partitions and dimensional output scope."""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('partition_frame_base',HERE/'test_surface_frame_binding_20261011.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
c,v=base.c,base.v


def make_document(folder):
    doc,facts=base.make_frame_document(folder)
    frame={'coordinate_system_id':'local-test','origin_m':[0,0,0],'x_axis':[1,0,0],'up_axis':[0,0,1],
           'source_edge_id':'a','source_edge_direction':'same','source_edge_interval_m':[0,10],
           'vertical_range_m':[0,3],'vertical_origin_m':0}
    facts['edges']['a']={'edge_id':'a','start':[0,0,0],'end':[10,0,0],'unit':'m','coordinate_system_id':'local-test'}
    def record(key,rect,material=None,opening=False,reason=None):
        result={'opening_id' if opening else 'region_id':key,'physical_object_id':'OBJ-'+key,
                'rectangle':rect,'unit':'m','source_evidence_ref':'EV-'+key}
        if material:result['material_id']=material
        if reason:result['reason']=reason
        return result
    plan={'partition_id':'PART-1','parent_surface_id':'face','layer_scope_id':'substrate',
          'scenario':{'scenario_id':'S-1','status':'determined','condition':'Synthetic source geometry only'},
          'local_coordinate_frame':frame,'face':record('FACE',[0,0,10,3]),
          'actual_openings':[record('O1',[2,0,4,2],opening=True),record('O2',[3,0,5,1],opening=True)],
          'material_regions':[record('A1',[0,0,4,3],'A'),record('A2',[1,1,3,3],'A'),record('B1',[6,0,8,3],'B')],
          'unknown_regions':[record('U1',[8,0,9,1],reason='Unspecified local substrate')],
          'base_material':{'material_id':'BASE','physical_object_id':'OBJ-BASE','basis':'Explicit source declaration outside named zones','source_evidence_ref':'EV-BASE'}}
    facts['partitions']={'p':plan}
    doc['evidence']=[e for e in doc['evidence'] if e['evidence_id'] not in {'EV-O1','EV-O2'}]
    for record in [plan['face'],*plan['actual_openings'],*plan['material_regions'],*plan['unknown_regions'],plan['base_material']]:
        e=copy.deepcopy(doc['evidence'][1]);e.update(evidence_id=record['source_evidence_ref'],
            evidence_class='opening_union' if 'opening_id' in record else 'surface_geometry',
            object_keys=['face',record['physical_object_id']],locator='independent-synthetic:'+record['physical_object_id'])
        doc['evidence'].append(e)
    doc['unresolved']=[{'unresolved_id':'U-1','type':'范围未定','description':'Local unknown material',
        'impact':'Affected material remains unassigned','required_input':'Independent material evidence','action':'保留已证实部分'}]
    original=doc['items'][0];doc['items']=[]
    for output in ['A','B','BASE','unknown']:
        i=copy.deepcopy(original);i.update(item_id='QTY-'+output,name='材料区域 '+output,physical_surface_id='face',method='material_partition_net',
            local_coordinate_frame=copy.deepcopy(frame),scope_status='partial',unresolved_refs=['U-1'],
            evidence_refs=[e['evidence_id'] for e in doc['evidence']],
            frame_binding={'frame_evidence_ref':'EV-GA','source_pointer':'/partitions/p/local_coordinate_frame','edge_evidence_ref':'EV-GA','edge_source_pointer':'/edges/a'},
            inputs={'partition_evidence_ref':'EV-GA','source_pointer':'/partitions/p','output_kind':'unknown' if output=='unknown' else 'material'})
        if output!='unknown':i['inputs']['material_id']=output
        doc['items'].append(i)
    base.base.update_facts(doc,facts)
    return doc,facts


@pytest.fixture
def setup(tmp_path):
    doc,facts=make_document(tmp_path)
    return doc,facts,tmp_path


def run(setup):
    doc,facts,folder=setup;base.base.update_facts(doc,facts)
    return base.base.run(doc,folder)


def plan(setup):return setup[1]['partitions']['p']


def partitions(out):return out['工程量结果'][0]['专业检查']['输入审计']['material_partition']


def quantities(out):return {r['成果项编号']:r['结果']['数值'] for r in out['工程量结果']}


def test_full_source_chain_same_material_union_opening_union_and_unknown(setup):
    out=run(setup);p=partitions(out)
    assert quantities(out)=={'QTY-A':8,'QTY-B':6,'QTY-BASE':10,'QTY-unknown':1}
    assert p['actual_opening_union']['area_m2']==5 and p['actual_opening_overlap_removed_m2']==1
    assert p['material_regions'][0]['same_material_overlap_removed_m2']==3
    assert p['net_face_area_m2']==25 and p['known_material_total_area_m2']==24
    assert p['unknown_region']['area_m2']==1 and p['business_correctness_proven'] is False
    assert len(out['工程量小计'])==4 and len({r['专业检查']['对象身份签名'] for r in out['工程量结果']})==4
    assert {s['材料编号'] for s in out['工程量小计']}=={'A','B','BASE',''}
    assert all(r['source_binding']['source_sha256']==setup[0]['sources'][0]['sha256'] for r in p['records'])
    assert v.validate_document(out)==[]


def test_cell_decomposition_preserves_holes_disjoint_pieces_and_no_overlap(setup):
    p=partitions(run(setup))
    geometries=[r['effective_known_geometry'] for r in p['material_regions']]+[p['unknown_region']]
    all_cells=[]
    for geometry in geometries:
        cells=geometry['rectangles_m']
        assert sum((c[2]-c[0])*(c[3]-c[1]) for c in cells)==pytest.approx(geometry['area_m2'])
        all_cells+=cells
    for i,a in enumerate(all_cells):
        for b in all_cells[i+1:]:
            assert max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))==0


def test_different_material_conflict_becomes_explicit_unknown_not_priority(setup):
    plan(setup)['material_regions'][2]['rectangle']=[3,0,8,3]
    out=run(setup);p=partitions(out)
    assert quantities(out)=={'QTY-A':7,'QTY-B':11,'QTY-BASE':5,'QTY-unknown':2}
    assert p['material_conflict_union']['area_m2']==1
    assert p['conflict_excluding_explicit_unknown']['area_m2']==1
    assert p['area_conservation'] and p['status']=='partial'


def test_conflict_only_inside_opening_leaves_no_effective_material_conflict(setup):
    p=plan(setup);p['face']['rectangle']=[0,0,10,3]
    p['actual_openings']=p['actual_openings'][:1];p['actual_openings'][0]['rectangle']=[4,0,6,3]
    p['material_regions']=p['material_regions'][::2]
    p['material_regions'][0]['rectangle']=[0,0,6,3];p['material_regions'][1]['rectangle']=[4,0,10,3]
    p['unknown_regions']=[];p['base_material']=None
    setup[0]['items']=setup[0]['items'][:2]
    for i in setup[0]['items']:i['scope_status']='complete';i['unresolved_refs']=[]
    out=run(setup);p=partitions(out)
    assert quantities(out)=={'QTY-A':12,'QTY-B':12}
    assert p['unknown_region']['area_m2']==0 and p['material_conflicts']==[]


def test_no_base_declaration_does_not_invent_fill_material(setup):
    plan(setup)['base_material']=None
    setup[0]['items']=[i for i in setup[0]['items'] if i['item_id']!='QTY-BASE']
    out=run(setup)
    assert quantities(out)['QTY-unknown']==11
    assert partitions(out)['unassigned_without_base']['area_m2']==10


@pytest.mark.parametrize('unit,factor',[('m',1),('mm',1000),('cm',100),('ft',1/.3048)])
def test_rectangle_units_are_independent_of_metre_frame(setup,unit,factor):
    p=plan(setup)
    for rec in [p['face'],*p['actual_openings'],*p['material_regions'],*p['unknown_regions']]:
        rec['rectangle']=[x*factor for x in rec['rectangle']];rec['unit']=unit
    assert quantities(run(setup))=={'QTY-A':8,'QTY-B':6,'QTY-BASE':10,'QTY-unknown':1}


def test_local_material_locations_survive_large_world_translation(setup):
    doc,facts,_=setup;shift=[1e9,-1e9,1e8]
    for k in ('start','end'):facts['edges']['a'][k]=[a+b for a,b in zip(facts['edges']['a'][k],shift)]
    plan(setup)['local_coordinate_frame']['origin_m']=shift
    for i in doc['items']:i['local_coordinate_frame']['origin_m']=shift[:]
    assert quantities(run(setup))=={'QTY-A':8,'QTY-B':6,'QTY-BASE':10,'QTY-unknown':1}


@pytest.mark.parametrize('change',['missing_unknown_output','missing_material_output','duplicate_output','wrong_unit','wrong_parent_evidence','wrong_physical_evidence',
    'missing_record_evidence','wrong_opening_class','wrong_plan_class','frame_other_source','frame_other_pointer','row_frame_moved','face_equal_area_shift',
    'precomputed_area','missing_actual_openings','missing_unknown_array','missing_base','bad_base_basis','wrong_material','wrong_layer_context',
    'wrong_frame_context','wrong_scenario_context','duplicate_region','duplicate_opening','nonfinite','bool_coord','zero_rectangle','unknown_complete','unknown_adopted','conditional_adopted'])
def test_source_geometry_scope_and_output_contract_errors_rejected(setup,change):
    doc,facts,_=setup;p=plan(setup);i=doc['items'][0]
    if change=='missing_unknown_output':doc['items']=doc['items'][:-1]
    elif change=='missing_material_output':doc['items']=doc['items'][1:]
    elif change=='duplicate_output':x=copy.deepcopy(i);x['item_id']='QTY-COPY';doc['items'].append(x)
    elif change=='wrong_unit':p['material_regions'][0]['unit']='pixels'
    elif change=='wrong_parent_evidence':p['parent_surface_id']='OTHER'
    elif change=='wrong_physical_evidence':p['material_regions'][0]['physical_object_id']='OTHER'
    elif change=='missing_record_evidence':i['evidence_refs'].remove('EV-A1')
    elif change=='wrong_opening_class':next(e for e in doc['evidence'] if e['evidence_id']=='EV-O1')['evidence_class']='surface_geometry'
    elif change=='wrong_plan_class':doc['evidence'][1]['evidence_class']='opening_union'
    elif change=='frame_other_source':i['frame_binding']['frame_evidence_ref']='EV-L'
    elif change=='frame_other_pointer':i['frame_binding']['source_pointer']='/frames/sample'
    elif change=='row_frame_moved':i['local_coordinate_frame']['origin_m'][0]=.1
    elif change=='face_equal_area_shift':p['face']['rectangle']=[.1,0,10.1,3]
    elif change=='precomputed_area':i['inputs']['area_m2']=8
    elif change=='missing_actual_openings':p.pop('actual_openings')
    elif change=='missing_unknown_array':p.pop('unknown_regions')
    elif change=='missing_base':p.pop('base_material')
    elif change=='bad_base_basis':p['base_material']['basis']=''
    elif change=='wrong_material':i['inputs']['material_id']='OTHER'
    elif change=='wrong_layer_context':p['material_regions'][0]['layer_scope_id']='OTHER'
    elif change=='wrong_frame_context':p['material_regions'][0]['coordinate_system_id']='OTHER'
    elif change=='wrong_scenario_context':p['material_regions'][0]['scenario_id']='OTHER'
    elif change=='duplicate_region':p['material_regions'][1]['region_id']=p['material_regions'][0]['region_id']
    elif change=='duplicate_opening':p['actual_openings'][1]['opening_id']=p['actual_openings'][0]['opening_id']
    elif change=='nonfinite':p['material_regions'][0]['rectangle'][0]=float('inf')
    elif change=='bool_coord':p['material_regions'][0]['rectangle'][0]=False
    elif change=='zero_rectangle':p['material_regions'][0]['rectangle']=[0,0,0,1]
    elif change=='unknown_complete':i['scope_status']='complete'
    elif change=='unknown_adopted':doc['items'][-1]['adoption_role']='adopted'
    elif change=='conditional_adopted':p['scenario']['status']='conditional';i['adoption_role']='adopted'
    with pytest.raises((c.CalculationError,ValueError)):run(setup)


@pytest.mark.parametrize('dimension',['layer_scope_id','scenario_id'])
def test_same_surface_and_layer_can_hold_separate_scope_or_scenario(setup,dimension):
    doc,facts,_=setup;p2=copy.deepcopy(plan(setup));p2['partition_id']='PART-2'
    if dimension=='scenario_id':p2['scenario']['scenario_id']='S-2'
    else:p2['layer_scope_id']='OTHER-LAYER-SCOPE'
    facts['partitions']['p2']=p2
    for original in doc['items'][:]:
        item=copy.deepcopy(original);item['item_id']+='-SECOND';item['inputs']['source_pointer']='/partitions/p2'
        item['frame_binding']['source_pointer']='/partitions/p2/local_coordinate_frame';doc['items'].append(item)
    out=run(setup)
    assert len(out['工程量结果'])==8 and len(out['工程量小计'])==8
    assert v.validate_document(out)==[]


def test_same_scope_different_plan_cannot_hide_duplicate_or_conflicting_quantities(setup):
    doc,facts,_=setup;facts['partitions']['p2']=copy.deepcopy(plan(setup));facts['partitions']['p2']['partition_id']='PART-2'
    doc['items'][1]['inputs']['source_pointer']='/partitions/p2'
    doc['items'][1]['frame_binding']['source_pointer']='/partitions/p2/local_coordinate_frame'
    with pytest.raises(c.CalculationError,match='不同分区方案'):run(setup)


def test_subtotal_cannot_mix_material_or_scenario(setup):
    out=run(setup);s=out['工程量小计'][0];s['来源成果项'].append(out['工程量小计'][1]['来源成果项'][0])
    assert any('混合材料编号' in e for e in v.validate_document(out))


def test_complete_cli_contract_and_equal_area_geometry_tampering(setup):
    out=run(setup);folder=setup[2];path=folder/'result.json';path.write_text(json.dumps(out,ensure_ascii=False),encoding='utf8')
    scripts=base.base.S
    commands=[ [sys.executable,'-X','utf8','-B',str(scripts.parents[1]/'_qto-shared-evidence/scripts/validate_minimum_result.py'),str(path)],
       [sys.executable,'-X','utf8','-B',str(scripts/'validate_surfaces_envelope_result.py'),'--calculator',str(scripts/'calculate_surfaces_envelope.py'),
        '--manifest',str(folder/'manifest.json'),'--binding',str(path)]]
    for cmd in commands:
        r=subprocess.run(cmd,capture_output=True,stdin=subprocess.DEVNULL)
        assert r.returncode==0,r.stdout.decode('utf8',errors='replace')+r.stderr.decode('utf8',errors='replace')
    p=partitions(out);cell=p['material_regions'][0]['effective_known_geometry']['rectangles_m'][0]
    cell[0]+=.1;cell[2]+=.1
    path.write_text(json.dumps(out,ensure_ascii=False),encoding='utf8')
    rejected=subprocess.run(commands[-1],capture_output=True,stdin=subprocess.DEVNULL)
    assert rejected.returncode==2 and '绑定复算结果不一致' in rejected.stdout.decode('utf8',errors='replace')


def select_known_regions(setup):
    doc,_,_=setup;p=plan(setup)
    p['coverage']={'mode':'selected_regions','physical_object_id':'SCOPE-BANDS',
                   'basis':'Only explicit bands are included in this measurement scope','source_evidence_ref':'EV-SCOPE'}
    p['base_material']=None
    e=copy.deepcopy(doc['evidence'][1]);e.update(evidence_id='EV-SCOPE',object_keys=['face','SCOPE-BANDS'])
    doc['evidence'].append(e)
    doc['items']=[i for i in doc['items'] if i['item_id']!='QTY-BASE']
    for i in doc['items']:i['evidence_refs'].append('EV-SCOPE')


def test_selected_regions_excludes_background_without_calling_it_unknown(setup):
    select_known_regions(setup);out=run(setup);p=partitions(out)
    assert quantities(out)=={'QTY-A':8,'QTY-B':6,'QTY-unknown':1}
    assert p['outside_selected_scope']['area_m2']==10 and p['unassigned_without_base']['area_m2']==0
    assert p['full_surface_coverage'] is False
    assert p['coverage']['source_binding']['source_pointer']=='/partitions/p/coverage'
    assert v.validate_document(out)==[]


def test_selected_known_only_scope_does_not_claim_complete_parent_face(setup):
    select_known_regions(setup);plan(setup)['unknown_regions']=[]
    setup[0]['items']=setup[0]['items'][:-1]
    for i in setup[0]['items']:i['scope_status']='complete';i['unresolved_refs']=[]
    out=run(setup);p=partitions(out)
    assert p['status']=='selected_regions_measured' and p['full_surface_coverage'] is False
    assert p['outside_selected_scope']['area_m2']==11 and p['unknown_region']['area_m2']==0
    assert quantities(out)=={'QTY-A':8,'QTY-B':6} and v.validate_document(out)==[]


def test_selected_regions_does_not_hide_material_conflicts(setup):
    select_known_regions(setup);plan(setup)['material_regions'][2]['rectangle']=[3,0,8,3]
    out=run(setup)
    assert quantities(out)=={'QTY-A':7,'QTY-B':11,'QTY-unknown':2}
    assert partitions(out)['material_conflict_union']['area_m2']==1


@pytest.mark.parametrize('change',['with_base','no_scope_evidence','wrong_scope_owner','empty_basis','false_full_claim','omit_unknown',
    'wrong_scope_parent','wrong_scope_layer','wrong_scope_scenario','wrong_scope_frame'])
def test_selected_scope_requires_evidence_and_cannot_bypass_unknown_contract(setup,change):
    original=copy.deepcopy(plan(setup)['base_material']);select_known_regions(setup)
    if change=='with_base':plan(setup)['base_material']=original
    elif change=='no_scope_evidence':setup[0]['items'][0]['evidence_refs'].remove('EV-SCOPE')
    elif change=='wrong_scope_owner':plan(setup)['coverage']['physical_object_id']='WRONG'
    elif change=='empty_basis':plan(setup)['coverage']['basis']=''
    elif change=='wrong_scope_parent':plan(setup)['coverage']['parent_surface_id']='OTHER'
    elif change=='wrong_scope_layer':plan(setup)['coverage']['layer_scope_id']='OTHER'
    elif change=='wrong_scope_scenario':plan(setup)['coverage']['scenario_id']='OTHER'
    elif change=='wrong_scope_frame':plan(setup)['coverage']['coordinate_system_id']='OTHER'
    elif change=='omit_unknown':setup[0]['items']=setup[0]['items'][:-1]
    elif change=='false_full_claim':
        out=run(setup);partitions(out)['full_surface_coverage']=True
        assert any('全母面' in e for e in v.validate_document(out));return
    with pytest.raises(c.CalculationError):run(setup)
