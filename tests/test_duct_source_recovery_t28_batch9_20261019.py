"""Anonymous T28 source recovery controls; no project adoption or DWG claim."""
import copy
import json
import math
from pathlib import Path
import ezdxf
import pytest
from test_duct_batch_network_t28_t20_20261019 import fixture,build,consume,write,sha,run,DUCT,SHARED,builder


def refresh(c, edit):
    doc=ezdxf.readfile(c['source']);value=edit(doc.modelspace(),doc);doc.saveas(c['source'])
    out=c['source'].parent/'neutral-refreshed'
    p=run(SHARED/'extract_cad_neutral.py','--source-dxf',c['source'],'--output-dir',out)
    assert p.returncode in (0,3),p.stderr
    c['manifest']=out/'CAD中性取证清单.json';c['profile']['expected_source_sha256']=sha(c['source'])
    for rule in c['profile']['rules']:
        ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'))
        ev['source_sha256']=sha(c['source']);write(ep,ev);rule['evidence']['sha256']=sha(ep)
    return value


@pytest.mark.parametrize('duplicate',[False,True])
def test_staggered_multiple_outlines_partition_only_ambiguous_interval(tmp_path,duplicate):
    c=fixture(tmp_path)
    def edit(m,doc):
        for key in ('oa','ob'):m.delete_entity(doc.entitydb[c['entities'][key].dxf.handle])
        for a,b in (((0,0),(1000,0)),((0,150),(600,150)),((600,150),(1000,150))):
            m.add_line(a,b,dxfattribs={'layer':'DUCT-O'})
        if duplicate:m.add_line((200,150),(400,150),dxfattribs={'layer':'DUCT-O'})
    refresh(c,edit);data=build(c)
    outline=[s for s in data['segments'] if s['representation']=='outline']
    total=sum(math.dist(s['start'],s['end']) for s in outline)
    assert total==pytest.approx(800 if duplicate else 1000)
    rows=[r for r in data['coverage']['rows'] if r.get('layer')=='DUCT-O']
    by_key={r['source_key']:r for r in rows}
    for parent in rows:
        if parent.get('partition_children'):
            assert sum(by_key[k]['raw_geometry_length'] for k in parent['partition_children'])==pytest.approx(parent['raw_geometry_length'])
    if duplicate:
        assert any(r.get('pairing_candidates') and r['disposition']=='unrecognized' for r in rows)
    result=consume(tmp_path,data)
    assert result['metrics']['straight_length_rule']==pytest.approx(total/1000)


def leader_case(tmp_path, *, ambiguous=False):
    c=fixture(tmp_path)
    def edit(m,doc):
        note=doc.entitydb[c['entities']['note'].dxf.handle];note.dxf.insert=(0,6500,0)
        m.add_line((1200,6000),(2000,6000),dxfattribs={'layer':'DUCT-SINGLE'})
        m.add_line((3000,6000),(4000,6000),dxfattribs={'layer':'DUCT-SINGLE'})
        if ambiguous:m.add_line((1200,6000.05),(2000,6000.05),dxfattribs={'layer':'DUCT-SINGLE'})
        doc.layers.new('DUCT-LEADER');keys=[]
        for a,b in (((0,6500),(200,6500)),((200,6500),(300,6400)),((300,6400),(500,6000)),((300,6400),(1500,6000))):
            keys.append('CAD-'+m.add_line(a,b,dxfattribs={'layer':'DUCT-LEADER'}).dxf.handle)
        return keys
    keys=refresh(c,edit);rule=c['profile']['rules'][-1]
    cfg={'source_keys':keys,'shoulder_source_key':keys[0],'shoulder_part_index':0,
         'snap_tolerance':.01,'text_tolerance':.01,'target_tolerance':.1,
         'basis':'complete anonymous shoulder and two explicit target branches','source_locator':'Model:anonymous leader tree'}
    rule['annotation_binding']['leader_network']=cfg
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'));ev.pop('annotation_relationships')
    ev['annotation_network']=cfg;write(ep,ev);rule['evidence']['sha256']=sha(ep)
    return c


def test_complete_multibranch_leader_binds_only_pointed_parts_and_actual_metres(tmp_path):
    c=leader_case(tmp_path);data=build(c)
    scope=data['annotation_scope_candidates'][0]
    assert scope['status']=='unique_source_leader_tree' and len(scope['target_part_keys'])==2
    lines=[s for s in data['segments'] if s['representation']=='single_line']
    assert sum(s.get('width')==300 for s in lines)==2
    assert sum(s.get('width') is None for s in lines)==1
    result=consume(tmp_path,data)
    pointed={s['segment_id'] for s in lines if s.get('width')==300}
    assert all(s['center_elevation']==pytest.approx(3.3) for s in result['straight_segments'] if s['segment_id'] in pointed)
    assert all(s['elevation']['unit']=='mm' for s in lines if s.get('elevation'))


def test_one_ambiguous_leader_branch_keeps_whole_annotation_unbound(tmp_path):
    c=leader_case(tmp_path,ambiguous=True);data=build(c)
    assert data['annotation_scope_candidates'][0]['status']=='unresolved'
    assert all(s.get('width') is None for s in data['segments'] if s['representation']=='single_line')
    result=consume(tmp_path,data)
    assert any(p['kind']=='annotation_scope_unresolved' for p in result['pending'])


def test_disconnected_or_cyclic_leader_is_not_nearest_text_scope(tmp_path):
    c=leader_case(tmp_path);rule=c['profile']['rules'][-1]
    cfg=rule['annotation_binding']['leader_network'];cfg['source_keys']=cfg['source_keys'][:-1]
    # Omitting the last branch leaves a connected tree: it legitimately binds one
    # target only. An explicit unrelated source line makes it disconnected.
    cfg['source_keys'].append('CAD-'+c['entities']['unknown'].dxf.handle)
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text());ev['annotation_network']=cfg;write(ep,ev);rule['evidence']['sha256']=sha(ep)
    data=build(c);assert data['annotation_scope_candidates'][0]['status']=='unresolved'
    assert not data['annotation_scope_candidates'][0]['target_part_keys']


def riser_case(tmp_path, *, units=4, endpoint=False):
    c=fixture(tmp_path,units=units)
    def edit(m,doc):
        m.add_arc((8000,3000),500,0,180,dxfattribs={'layer':'DUCT-RISER'})
        texts=['底标高3.000','顶标高8.000'] if endpoint else ['风管上接高度6m']
        return [('CAD-'+m.add_text(t,dxfattribs={'insert':(9000+i*1000,3000),'height':100,'layer':'DUCT-NOTE'}).dxf.handle,t) for i,t in enumerate(texts)]
    texts=refresh(c,edit);data=build(c);keys=[k for r in data['riser_projection_candidates'] for k in r['source_ids']]
    section=next(r['section'] for r in data['riser_projection_candidates'])
    link={'physical_riser_id':'ANON-RISER','projection_source_keys':keys,'system':'SYS','section':section,
          'projection_floor_bindings':[{'source_key':key,'floor_id':'F'+str(i+1),'source_locator':'Model:projection frame '+str(i+1)} for i,key in enumerate(keys)]}
    ev={'schema_version':'qto-duct-riser-height-evidence/1.0','source_sha256':sha(c['source']),'layout':'Model','space_id':'ANON-SPACE',
        **link,'basis':'anonymous multi-floor projection aliases for one physical riser','source_locator':'Model:anonymous riser pair'}
    if endpoint:
        ev['endpoint_annotations']=[{'source_key':key,'expected_text':t,'floor_id':'F'+str(i+1),'datum_id':'ANON-ABSOLUTE',
            'value_regex':r'标高(?P<value>\d+(?:\.\d+)?)','unit':'m','reference':'bottom' if i==0 else 'top',
            'basis':'explicit source endpoint level and corresponding floor','source_locator':'Model:'+key} for i,(key,t) in enumerate(texts)]
    else:
        ev['height_annotation']={'source_key':texts[0][0],'expected_text':texts[0][1],'basis':'explicit full vertical height','source_locator':'Model:height note'}
    ep=write(tmp_path/'riser-height-evidence.json',ev);link['evidence']={'path':str(ep),'sha256':sha(ep)}
    c['profile']['riser_links']=[link];return c,ep


@pytest.mark.parametrize('endpoint,expected',[(False,6),(True,4.8)])
def test_grouped_riser_projection_has_one_physical_height_outlet(tmp_path,endpoint,expected):
    c,_=riser_case(tmp_path,endpoint=endpoint);data=build(c)
    assert len(data['riser_vertical_items'])==1
    assert len(data['riser_vertical_items'][0]['source_ids'])==2
    assert data['riser_vertical_items'][0]['length']==pytest.approx(expected)
    result=consume(tmp_path,data)
    values=[r for r in result['vertical_items'] if r['kind']=='source_riser_link']
    assert len(values)==1 and values[0]['length']==pytest.approx(expected)
    assert result['metrics']['known_source_riser_length_rule']==pytest.approx(expected)
    assert not any(p['kind']=='vertical_projection_height_unresolved' for p in result['pending'])


def test_independent_riser_height_survives_unknown_plan_units(tmp_path):
    c,_=riser_case(tmp_path,units=0);result=consume(tmp_path,build(c))
    item=next(r for r in result['vertical_items'] if r['kind']=='source_riser_link')
    assert item['length']==6 and item['theoretical_surface_area']==6
    assert result['metrics']['known_source_riser_length_rule']==6
    assert result['metrics']['measured_length_rule'] is None and result['length_summary'] is None


@pytest.mark.parametrize('failure',['datum','text','duplicate'])
def test_riser_height_rejects_mixed_datums_text_tamper_and_duplicate_source(tmp_path,failure):
    c,ep=riser_case(tmp_path,endpoint=True);ev=json.loads(ep.read_text(encoding='utf-8'))
    if failure=='datum':ev['endpoint_annotations'][1]['datum_id']='FLOOR-RELATIVE'
    elif failure=='text':ev['endpoint_annotations'][1]['expected_text']='顶标高80.000'
    else:c['profile']['riser_links'].append(copy.deepcopy(c['profile']['riser_links'][0]))
    write(ep,ev);c['profile']['riser_links'][0]['evidence']['sha256']=sha(ep)
    with pytest.raises(ValueError):build(c)


def curve_data():
    arc={'kind':'ellipse_arc','center':[0,0,0],'u':[1000,0,0],'v':[0,1000,0],'start_param':0,'sweep_param':math.pi/2}
    common={'system':'SYS','space_id':'S','floor_id':'F','width':300,'height':200,'size_evidence_level':'规则推算'}
    return {'schema_version':'qto-duct-fitting-vertical-input/1.0','unit':'mm','package_id':'CURVED','space_id':'S',
        'parameters':{'parameter_basis':'anonymous true curve elevation chain','end_snap_tolerance':.01},'floors':[{'floor_id':'F'}],
        'segments':[{**common,'segment_id':'A','start':[1000,-1000],'end':[1000,0],'source_ids':['ANON-A']},
                    {**common,'segment_id':'B','start':[0,1000],'end':[-1000,1000],'source_ids':['ANON-B']}],
        'drawn_curve_segments':[{**common,'segment_id':'ARC','geometry':builder.geometry(arc),'source_ids':['ANON-ARC']}]}


def test_curve_and_legacy_line_same_run_elevation_propagates_with_explicit_units(tmp_path):
    data=curve_data();data['segments'][0]['elevation']={'value':3200,'unit':'mm','reference':'bottom','source_id':'NOTE-A','origin':'tag'}
    result=consume(tmp_path,data)
    assert all(s['center_elevation']==pytest.approx(3.3) for s in result['straight_segments'])
    assert result['drawn_curve_segments'][0]['center_elevation']==pytest.approx(3.3)
    assert result['source_curve_elevations']['ARC']['evidence_level']=='规则推算'
    assert result['metrics']['measured_length_rule']==pytest.approx(2+math.pi/2)


def test_curve_run_conflicting_tags_dont_create_constant_elevation(tmp_path):
    data=curve_data()
    for row,val in zip(data['segments'],(3,4)):
        row['elevation']={'value':val,'reference':'center','source_id':'NOTE-'+row['segment_id'],'origin':'tag'}
    result=consume(tmp_path,data)
    assert result['source_curve_elevations']['ARC']['value'] is None
    assert any(p['kind']=='curve_run_elevation_conflict' for p in result['pending'])
    assert {s['center_elevation'] for s in result['straight_segments']}=={3,4}


def test_old_without_unit_elevation_semantics_and_system_boundary_remain(tmp_path):
    data=curve_data();data['segments'][0]['elevation']={'value':3.2,'reference':'center','source_id':'NOTE','origin':'tag'}
    data['segments'][1]['system']='OTHER'
    result=consume(tmp_path,data)
    assert result['straight_segments'][0]['center_elevation']==3.2
    assert result['straight_segments'][1]['center_elevation'] is None
    assert result['source_curve_elevations']['ARC']['value']==3.2


def test_curve_branch_preserves_unique_main_and_branch_elevation_boundary(tmp_path):
    data=curve_data();common={k:data['segments'][0][k] for k in ('system','space_id','floor_id','width','height','size_evidence_level')}
    data['segments'].append({**common,'segment_id':'BRANCH','start':[1000,0],'end':[1500,0],'source_ids':['ANON-BRANCH']})
    data['segments'][0]['elevation']={'value':3.2,'reference':'center','source_id':'MAIN-NOTE','origin':'tag'}
    result=consume(tmp_path,data)
    assert len(result['source_curve_network']['branch_nodes'])==1
    assert len(result['source_curve_network']['joins'])==2
    branch=next(s for s in result['straight_segments'] if s['segment_id']=='BRANCH')
    assert branch['elevation_evidence_level']=='方案暂估'
    assert result['drawn_curve_segments'][0]['center_elevation']==3.2
    main=next(s for s in result['straight_segments'] if s['segment_id']=='B')
    assert main['center_elevation']==3.2 and main['elevation_evidence_level']=='规则推算'


def test_omitted_connected_leader_branch_is_not_silently_narrowed(tmp_path):
    c=leader_case(tmp_path);rule=c['profile']['rules'][-1];cfg=rule['annotation_binding']['leader_network']
    omitted=cfg['source_keys'].pop()
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text());ev['annotation_network']=cfg;write(ep,ev);rule['evidence']['sha256']=sha(ep)
    data=build(c);scope=data['annotation_scope_candidates'][0]
    assert scope['status']=='unresolved' and scope['omitted_connected_source_keys']==[omitted]
    assert all(s.get('width') is None for s in data['segments'] if s['representation']=='single_line')


def test_leader_branch_can_end_inside_unsplit_long_trunk(tmp_path):
    c=leader_case(tmp_path);rule=c['profile']['rules'][-1]
    key=rule['annotation_binding']['leader_network']['source_keys'][-1]
    def edit(m,doc):doc.entitydb[key.removeprefix('CAD-')].dxf.start=(250,6450,0)
    refresh(c,edit);data=build(c)
    assert data['annotation_scope_candidates'][0]['status']=='unique_source_leader_tree'
    assert len(data['annotation_scope_candidates'][0]['target_part_keys'])==2


def test_multiple_specifications_in_one_source_tag_are_not_first_match(tmp_path):
    c=fixture(tmp_path);rule=c['profile']['rules'][-1]
    def edit(m,doc):doc.entitydb[c['entities']['note'].dxf.handle].dxf.text='300x200 400x300 h=3.2'
    refresh(c,edit);rule['annotation_binding']['expected_text']='300x200 400x300 h=3.2'
    with pytest.raises(ValueError,match='多个尺寸'):build(c)
