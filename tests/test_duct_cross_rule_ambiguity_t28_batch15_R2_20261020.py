"""R2: actual competing outlines cannot be hidden by rule dictionaries."""
import copy
import json
import math
from pathlib import Path
import pytest
from test_duct_eccentric_circles_t28_batch15_20261020 import ring_fixture,build,consume,write,sha
from test_duct_source_recovery_t28_batch9_20261019 import refresh
from duct_curved_outline import circle_scope_competes


def freeze_rule(c,rule):
    evidence={'schema_version':'qto-duct-recognition-evidence/1.0','source_sha256':c['profile']['expected_source_sha256'],
        'layout':c['profile']['layout'],'space_id':c['profile']['space_id'],'crop':c['profile'].get('crop'),
        'basis':'anonymous explicit source relationship for this exact rule scope','source_locator':'Model:anonymous exact instances',
        **{key:rule.get(key) for key in ('rule_id','selector','representation','system','section')}}
    if 'equidistant_midline' in rule:evidence['equidistant_midline']=rule['equidistant_midline']
    path=write(c['source'].parent/(rule['rule_id']+'原源识别证据.json'),evidence)
    rule['evidence']={'path':str(path),'sha256':sha(path)}


def cross_rule_fixture(tmp_path,case):
    nested=case=='nested_insert'
    c,_,keys=ring_fixture(tmp_path,extra=None if nested else 'duplicate' if case=='full' else 'partial')
    if nested:
        def edit(model,doc):
            inner=doc.blocks.new('ANON-THIRD-LEAF')
            inner.add_arc((0,0),1000,20,80,dxfattribs={'layer':'0'})
            outer=doc.blocks.new('ANON-THIRD-OUTER')
            outer.add_blockref('ANON-THIRD-LEAF',(200,0),dxfattribs={'layer':'0'})
            model.add_blockref('ANON-THIRD-OUTER',(0,0),dxfattribs={'layer':'DUCT-O'})
        refresh(c,edit)
        data=build(c)
        keys=[r['source_key'] for r in data['coverage']['rows'] if r['layer']=='DUCT-O' and r.get('geometry')]
    if case in {'different_plane','disjoint_support'}:
        handle=keys[2].split('#')[0].removeprefix('CAD-')
        def edit(model,doc):
            doc.entitydb[handle].dxf.center=(200,0,100) if case=='different_plane' else (10000,0,0)
        refresh(c,edit)
    pair=next(r for r in c['profile']['rules'] if r['rule_id']=='OUTLINE')
    pair['selector']['source_keys']=[k.rsplit('#',1)[0] for k in keys[:2]]
    third=copy.deepcopy(pair);third['rule_id']='THIRD'
    third['selector']['source_keys']=[keys[2].rsplit('#',1)[0]]
    third['equidistant_midline']['source_pairings']=[]
    if case=='description_only':
        third['rule_id']='THIRD_RENAMED';third['description']='Different descriptive words must not separate actual geometry'
        third['equidistant_midline']['basis']='another descriptive source narrative, same actual applicability'
        third['equidistant_midline']['source_locator']='Model:another locator description, same actual third curve'
    if case=='legacy_method':third.pop('equidistant_midline')
    if case=='different_system':third['system']='SEPARATE-SYSTEM'
    rules=[pair,third]
    if case=='multiple_matches':
        another=copy.deepcopy(third);another['rule_id']='THIRD_SECOND_MATCH';rules.append(another)
    for rule in rules:freeze_rule(c,rule)
    c['profile']['rules'].extend(rules[1:])
    return c,keys


@pytest.mark.parametrize('case',['partial','full','description_only','legacy_method','multiple_matches','nested_insert'])
def test_cross_rule_actual_competitors_block_claimed_pair_and_stay_null_in_actual_consumer(tmp_path,case):
    c,keys=cross_rule_fixture(tmp_path,case);data=build(c)
    assert not [r for r in data['drawn_curve_segments'] if r['representation']=='outline']
    coverage={r['source_key']:r for r in data['coverage']['rows']}
    for key in keys:
        row=coverage[key]
        assert row['disposition']=='unrecognized' and row['quantity'] is None
        assert row['geometry'] and row['raw_geometry_length']>0
        assert row.get('pairing_candidates')
    assert set(coverage[keys[0]]['pairing_candidates'])==set(keys[1:])
    if case=='nested_insert':
        assert '/ANON-THIRD-OUTER/' in keys[2] and '/ANON-THIRD-LEAF/' in keys[2]
        assert coverage[keys[2]]['layout']=='Model' and coverage[keys[2]]['space_id']==c['profile']['space_id']
        assert coverage[keys[2]]['source_instance_path']!=coverage[keys[0]]['source_instance_path']
    result=consume(tmp_path,data)
    assert not [r for r in result['drawn_curve_segments'] if r['representation']=='outline']
    assert data['coverage']['quantity_complete'] is False and result['metrics']['totals_complete'] is False


@pytest.mark.parametrize('case',['different_system','different_plane','disjoint_support'])
def test_noncompeting_production_scopes_preserve_legal_pair_and_actual_length(tmp_path,case):
    c,keys=cross_rule_fixture(tmp_path,case);data=build(c)
    rows=[r for r in data['drawn_curve_segments'] if r['representation']=='outline']
    assert len(rows)==1 and set(rows[0]['source_ids'])==set(keys[:2])
    result=consume(tmp_path,data)
    actual=next(r for r in result['drawn_curve_segments'] if r['representation']=='outline')
    assert actual['length']==pytest.approx(4.3757035276666585,abs=actual['length_error_estimate'])
    assert actual['theoretical_surface_area'] is None
    third=next(r for r in data['coverage']['rows'] if r['source_key']==keys[2])
    assert third['disposition']=='unrecognized' and third['quantity'] is None


@pytest.mark.parametrize('scope_key',['source_sha256','layout','space_id','floor_id'])
def test_authoritative_placement_scope_is_not_rule_or_definition_metadata(scope_key):
    a={'entry':{'source_key':'A','source_sha256':'S','layout':'Model','space_id':'SPACE','floor_id':'F'}}
    b=copy.deepcopy(a);b['entry']['source_key']='B'
    rules={'A':[{'representation':'outline','system':'SYS','rule_id':'FIRST','basis':'first words'}],
           'B':[{'representation':'outline','system':'SYS','rule_id':'DIFFERENT','basis':'different words'}]}
    assert circle_scope_competes(a,b,rules)
    b['entry'][scope_key]='OTHER'
    assert not circle_scope_competes(a,b,rules)
