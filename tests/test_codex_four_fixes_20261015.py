"""Independent synthetic oracles for the four 0.3.4 findings; no project answers."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for relative in ('skills/_qto-shared-evidence/scripts', 'skills/qto-pipe-networks/scripts', 'skills/qto-duct-air-systems/scripts'):
    sys.path.insert(0, str(ROOT / relative))
import qto_cad_source_contract as units
import infer_duct_fittings_vertical as duct
import build_pipe_inference_inputs_batch as batch


def segment(sid='A', system='SYS-A', start=None, end=None, elevation=None, floor='F1', width=1000, height=320):
    row = dict(segment_id=sid, system=system, start=start or [0,0], end=end or [1000,0], floor_id=floor,
               width=width, height=height, size_evidence_level='确认', source_ids=['DRAW-'+sid])
    if elevation is not None:
        row['elevation'] = dict(value=elevation, reference='center', origin='tag', evidence_level='确认', source_id='TAG-'+sid)
    return row


def data(segments=None, fittings=None, **extra):
    return dict(schema_version=duct.INPUT_SCHEMA, unit='mm', output_unit='m',
                parameters={'parameter_basis':'independent synthetic geometry'},
                segments=segments or [segment()], fittings=fittings or [], **extra)


def geometry(**extra):
    return dict(source_id='BLOCK-CENTERLINE', basis='synthetic turn projection 35..355; centre 195; outer face 385',
                endpoint_relation='at_connection', turn_plane='vertical', **extra)


def fitting(g=None, kind='elbow', bbox=None, **extra):
    row = dict(fitting_id='E', kind=kind, floor_id='F1', bbox=bbox or [1000,-500,1385,500],
               ends=[{'segment_id':'A','end':'end'}], **extra)
    if g is not None:row['single_end_geometry'] = g
    return row


def inferred(value):
    original=copy.deepcopy(value);result=duct.infer(value)
    assert value==original and result['business_adoption_allowed'] is False
    return result


@pytest.mark.parametrize('gap', [0,200])
@pytest.mark.parametrize('system,expected', [('SYS-A',3.),('SYS-B',None)])
def test_system_boundary_for_direct_joints_and_accessory_gaps(gap, system, expected):
    r=inferred(data([segment(elevation=3.),segment('B',system,[1000+gap,0],[2000+gap,0])]))
    assert r['straight_segments'][1]['center_elevation']==expected
    assert len(r['joints'])==int(system=='SYS-A' and gap==0)
    assert len(r['accessory_gaps'])==int(system=='SYS-A' and gap==200)


@pytest.mark.parametrize('system,expected', [('SYS-A',1),('SYS-B',0)])
def test_open_tap_does_not_find_main_in_another_system(system, expected):
    r=inferred(data([segment(end=[2000,0],elevation=3.),segment('B',system,[1000,600],[1000,200])]))
    assert len(r['open_taps'])==expected
    assert r['straight_segments'][1]['center_elevation']==(3. if expected else None)


def test_ambiguous_fitting_scope_does_not_link_or_deduplicate_different_systems():
    parts=[segment(elevation=3.),segment('B','SYS-B',[1200,0],[2200,0])]
    f=dict(fitting_id='E',kind='reducer',floor_id='F1',bbox=[990,-200,1210,200])
    r=inferred(data(parts,[f]))
    assert r['fittings'][0]['status']=='system_ambiguous'
    assert r['fittings'][0]['centerline_length'] is None
    assert r['straight_segments'][1]['center_elevation'] is None
    assert any(p['kind']=='fitting_system_ambiguous' for p in r['pending'])
    fa=dict(f,system='SYS-A');fb=dict(f,fitting_id='OTHER',system='SYS-B')
    r=inferred(data(parts,[fa,fb]))
    assert len(r['fittings'])==2 and not r['duplicate_fittings']


def test_explicit_cross_system_fitting_and_host_vertical_link_are_rejected():
    parts=[segment(elevation=3.),segment('B','SYS-B',[1200,0],[2200,0],4.)]
    f=fitting();f['ends'].append({'segment_id':'B','end':'start'})
    with pytest.raises(duct.InputError,match='跨风系统'):inferred(data(parts,[f]))
    link=dict(link_id='L',kind='segment_to_segment',segment_id='A',to_segment_id='B',source_id='SEC',basis='synthetic')
    with pytest.raises(duct.InputError,match='跨风系统'):inferred(data(parts,vertical_links=[link]))


@pytest.mark.parametrize('system,expected', [('SYS-A',1),('SYS-B',0)])
def test_automatic_interfloor_riser_stays_within_system(system, expected):
    parts=[segment(elevation=3.),segment('B',system,[0,10000],[1000,10000],2.,floor='F2')]
    f=fitting(geometry(centerline_point=[1195,0]))
    other=dict(f,fitting_id='UP',floor_id='F2',bbox=[1000,9500,1385,10500],ends=[{'segment_id':'B','end':'end'}],
               single_end_geometry=geometry(centerline_point=[1195,10000]))
    r=inferred(data(parts,[f,other],floors=[{'floor_id':'F1','level':0},{'floor_id':'F2','level':5,'plan_offset':[0,10000]}]))
    assert len([v for v in r['vertical_items'] if v['kind']=='inter_floor_riser'])==expected


@pytest.mark.parametrize('g', [geometry(centerline_point=[1195,0]),geometry(riser_projection=[35,355]),
                              geometry(flange_allowance=30),
                              geometry(centerline_point=[1195,0],riser_projection=[35,355],flange_allowance=30)])
def test_known_vertical_elbow_centre_195mm_is_not_clipped_to_zero(g):
    r=inferred(data(fittings=[fitting(g)]))
    assert r['fittings'][0]['centerline_length']==pytest.approx(.195)
    assert r['metrics']['fitting_length_rule']==pytest.approx(.195)
    assert r['single_end_plane_scenarios']['geometry_evidence']==pytest.approx(.195)


def test_outer_face_uses_height_or_explicit_riser_depth_and_far_flange():
    for width in (500,1000,2000):
        r=inferred(data([segment(width=width)], [fitting(geometry(flange_allowance=30))]))
        assert r['fittings'][0]['centerline_length']==pytest.approx(.195)
    r=inferred(data([segment(height=999)], [fitting(geometry(flange_allowance=30,riser_depth=320))]))
    assert r['fittings'][0]['centerline_length']==pytest.approx(.195)


def test_missing_height_is_unresolved_and_horizontal_turn_never_creates_riser():
    r=inferred(data([segment(height=None)], [fitting(geometry(flange_allowance=30))]))
    assert r['fittings'][0]['centerline_length'] is None
    g=geometry(centerline_point=[1195,0]);g['turn_plane']='horizontal'
    r=inferred(data([segment(elevation=3),segment('B',start=[1100,-1000],end=[1100,1000],elevation=4)], [fitting(g)]))
    assert r['fittings'][0]['centerline_length']==pytest.approx(.195) and not r['vertical_items']
    assert any(p['kind']=='single_end_horizontal_destination_unknown' for p in r['pending'])


@pytest.mark.parametrize('system,expected', [('SYS-A',1),('SYS-B',0)])
def test_vertical_branch_main_matches_only_same_system(system,expected):
    parts=[segment(elevation=3),segment('B',system,[1195,-2000],[1195,2000],4)]
    r=inferred(data(parts,[fitting(geometry(centerline_point=[1195,0]))]))
    assert len([v for v in r['vertical_items'] if v['kind']=='branch_to_main'])==expected


@pytest.mark.parametrize('kind', ['reducer','rect_to_round'])
def test_single_inline_body_is_counted_without_half_width_or_vertical_inference(kind):
    g=dict(source_id='BODY',basis='synthetic body 200mm',endpoint_relation='at_connection',body_length=200)
    r=inferred(data(fittings=[fitting(g,kind,[1000,-500,1200,500])]))
    assert r['fittings'][0]['centerline_length']==pytest.approx(.2)
    assert r['fittings'][0]['status']=='single_end_inline' and not r['vertical_items']
    assert not any(p['kind']=='single_end_destination_unknown' for p in r['pending'])


@pytest.mark.parametrize('g', [None,geometry(riser_depth=320),geometry(riser_projection=[355,35])])
def test_bbox_or_depth_alone_is_unresolved_and_keeps_known_straight_quantity(g):
    r=inferred(data(fittings=[fitting(g)]))
    assert r['fittings'][0]['centerline_length'] is None and r['fittings'][0]['arms']==[]
    assert r['metrics']['straight_length']==1. and r['metrics']['fitting_plane_unresolved_count']==1
    assert r['metrics']['single_end_plane_default'] is None and r['pending']


@pytest.mark.parametrize('relation', ['reversed','unknown'])
def test_reversed_or_unknown_connection_relation_does_not_guess_length(relation):
    g=geometry(centerline_point=[1195,0]);g['endpoint_relation']=relation
    r=inferred(data(fittings=[fitting(g)]))
    assert r['fittings'][0]['centerline_length'] is None


def test_already_at_center_requires_geometric_coincidence():
    g=geometry(centerline_point=[1000,0]);g['endpoint_relation']='already_at_center'
    assert inferred(data(fittings=[fitting(g)]))['fittings'][0]['centerline_length']==0.
    g['centerline_point']=[1195,0]
    assert inferred(data(fittings=[fitting(g)]))['fittings'][0]['centerline_length'] is None


def test_geometry_conflict_negative_deduction_and_legacy_policy_are_not_promoted():
    cases=[geometry(centerline_point=[1195,0],riser_projection=[100,400]),
           geometry(flange_allowance=300),geometry(centerline_point=[950,0])]
    for g in cases:
        assert inferred(data(fittings=[fitting(g)]))['fittings'][0]['centerline_length'] is None
    d=data(fittings=[fitting()]);d['parameters']['single_end_plane_policy']='extension_full'
    assert inferred(d)['fittings'][0]['centerline_length'] is None


def sheet(paper=(841,1189),measured=(126150,178350),scale=150):
    return dict(kind='frame_size_scale',paper_size_mm=list(paper),measured_drawing_units=list(measured),
                scale_denominator=scale,object_ids=['FRAME'],basis='synthetic frozen frame width and height')


@pytest.mark.parametrize('paper', [(841,1189),(1189,841)])
def test_frame_corroboration_checks_both_edges_and_supports_orientation(paper):
    r=units.verify_unit_corroboration('mm',[sheet(paper)],{'FRAME':[0,0,126150,178350]})[0]
    assert r['checked_axes']==2 and r['implied_metres_per_unit_by_axis']==pytest.approx([.001,.001])


@pytest.mark.parametrize('change', [{'paper_size_mm':[594,841]}, {'measured_drawing_units':[126150,126150]},
                                  {'scale_denominator':100}, {'paper_size_mm':841,'measured_drawing_units':126150},
                                  {'paper_size_mm':[841,0]}])
def test_matching_one_frame_edge_never_validates_the_wrong_other_edge(change):
    proof=sheet();proof.update(change)
    with pytest.raises(ValueError):units.verify_unit_corroboration('mm',[proof],{'FRAME':[0,0,126150,178350]})


def test_known_component_size_still_supports_a_one_dimensional_measurement():
    proof=dict(kind='known_component_size',real_length_m=1.,measured_drawing_units=1000,object_ids=['LINE'],basis='synthetic one metre')
    assert units.verify_unit_corroboration('mm',[proof],{'LINE':[0,0,1000,0]})[0]['implied_metres_per_unit']==.001


def scan(tmp_path, rows, blocks=None, manifest=None):
    layout=tmp_path/'layout.ndjson';defs=tmp_path/'blocks.ndjson'
    layout.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    defs.write_text(''.join(json.dumps(r)+'\n' for r in (blocks or [])),encoding='utf-8')
    frames=[{'frame_id':'A','bounds':[0,0,100,100]},{'frame_id':'B','bounds':[200,0,300,100]}]
    return batch.scan_frames(layout,defs,manifest or {},{'layout':'Model','families':[], 'block_expansion':{'max_depth':5}},frames,[],tmp_path)


def entity(oid='UNLOCATED', **extra):
    return dict(row_type='entity',layout='Model',entity_type='SPLINE',record_id=oid,layer='UNKNOWN',bbox=None,**extra)


def test_unlocated_object_is_drawing_level_once_not_repeated_in_every_frame(tmp_path):
    r=scan(tmp_path,[entity()])
    assert len(r['drawing_level_pending'])==1
    assert all(not s['pending'] and s['scoped_rows']==0 for s in r['frames'].values())


@pytest.mark.parametrize('x,owner', [(50,'A'),(250,'B'),(400,None)])
def test_unlocated_child_follows_nearest_ancestor_insertion_point(tmp_path,monkeypatch,x,owner):
    # Actual scanner and ownership logic; isolate expansion from these missing-geometry facts.
    parent=dict(entity('CAD-ROOT',x=x,y=50),entity_type='INSERT',block_name='BODY')
    child=entity('CADINST-ROOT/BODY/CHILD',instance_path='CAD-ROOT/BODY')
    monkeypatch.setattr(batch.adapter,'expanded_manifest_rows',lambda *a,**k:iter([parent,child]))
    r=scan(tmp_path,[])
    assert not r['drawing_level_pending']
    for fid,s in r['frames'].items():
        ids=[p['object_id'] for p in s['pending']]
        assert ('CADINST-ROOT/BODY/CHILD' in ids)==(fid==owner)


def test_nearest_nested_anchor_wins_over_top_level_and_frame_edge_is_unique(tmp_path,monkeypatch):
    root=dict(entity('ROOT',x=50,y=50),entity_type='INSERT',block_name='PARENT')
    nested=dict(entity('CADINST-ROOT/PARENT/NEST',x=200,y=50),entity_type='INSERT',block_name='INNER',instance_path='ROOT/PARENT')
    child=entity('CHILD',instance_path='ROOT/PARENT/NEST/INNER')
    monkeypatch.setattr(batch.adapter,'expanded_manifest_rows',lambda *a,**k:iter([root,nested,child]))
    r=scan(tmp_path,[])
    assert not any(p['object_id']=='CHILD' for p in r['frames']['A']['pending'])
    assert [p['object_id'] for p in r['frames']['B']['pending']].count('CHILD')==1


def test_own_locatable_child_geometry_takes_precedence_over_ancestor(tmp_path,monkeypatch):
    parent=dict(entity('ROOT',x=50,y=50),entity_type='INSERT',block_name='BODY')
    child=entity('CHILD',instance_path='ROOT/BODY');child['bbox']=[210,20,220,30]
    monkeypatch.setattr(batch.adapter,'expanded_manifest_rows',lambda *a,**k:iter([parent,child]))
    r=scan(tmp_path,[])
    assert not any(p['object_id']=='CHILD' for p in r['frames']['A']['pending'])
    assert any(p['object_id']=='CHILD' for p in r['frames']['B']['pending'])


@pytest.mark.parametrize('bad', [[50,50,10,10],[False,0,10,10]])
def test_invalid_bbox_is_not_a_locating_fact(bad):
    row=entity();row['bbox']=bad
    frames=[{'frame_id':'A','bounds':[0,0,100,100]}]
    assert batch.located_frame_ids(row,frames,{})[0] is None


@pytest.mark.parametrize('proof,accepted', [(sheet(),True),(sheet(paper=(841,841)),False),
                                          (dict(sheet(),paper_size_mm=841,measured_drawing_units=126150),False)])
def test_object_workbench_applies_two_axis_frame_contract(tmp_path,proof,accepted):
    import ezdxf
    import qto_recognition_workbench as workbench
    doc=ezdxf.new();doc.units=0;m=doc.modelspace()
    line=m.add_line((0,0),(1000,0))
    frame=m.add_lwpolyline([(0,0),(126150,0),(126150,178350),(0,178350)],close=True)
    note=m.add_text('管长和标高以米计，其余尺寸为毫米')
    src=tmp_path/'synthetic.dxf';doc.saveas(src)
    index=workbench.build_index(dict(path=str(src),sha256=hashlib.sha256(src.read_bytes()).hexdigest()))
    ids=[r['object_id'] for r in index['objects']]
    def oid(handle):return next(r['object_id'] for r in index['objects'] if r['source_handle']==handle)
    source=index['authoritative_source'];lp=tmp_path/'ledger.json'
    lp.write_text(json.dumps(dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='SYN',ledger_version='1',
        source_sha256=source['sha256'],adoption_scope='working',review_status='unreviewed',
        evidence=[dict(evidence_id='E',path=source['path'],sha256=source['sha256'],source_sha256=source['sha256'],
                       kind='cad_property',locator='synthetic source',observation='frozen synthetic geometry')],
        entries=[dict(adjudication_id='PIPE',subject_id='SYN',professional_owner='qto-pipe-networks',source_id='SYN',
                      recognized_by='Codex synthetic recognition',recognized_at='2026-10-15T08:00:00+08:00',basis='synthetic',
                      scope_basis='synthetic all objects',identity_basis='source hash',cross_check='two-axis independent frame',
                      decision='include',decision_origin='model',status='active',object_ids=ids,evidence_ids=['E'],uncertainties=[])])),encoding='utf-8')
    proof=copy.deepcopy(proof);proof['object_ids']=[oid(frame.dxf.handle)]
    operations=dict(index_sha256=index['index_sha256'],network_id='SYN',scope_object_ids=ids,scope_basis='synthetic full scope',unit='m',
        parameters=dict(snap_tolerance=.001,gap_max=.1),parameter_basis='synthetic drawing precision',
        assignments=[dict(adjudication_id='PIPE',object_ids=[oid(line.dxf.handle)],role='pipe_centerline',system='W',observation='synthetic pipe')],
        endpoint_review=dict(complete=False,basis='unreviewed synthetic endpoints'),
        unit_declaration=dict(raw_unit='Millimeters',adjudication_id='PIPE',annotation_object_ids=[oid(note.dxf.handle)],
            observation='synthetic mixed unit note',source_excerpt='其余尺寸为毫米',applies_to='annotation_values',geometric_corroboration=[proof]))
    before=src.read_bytes()
    if accepted:
        result=workbench.prepare_pipe(index,operations,lp)
        assert result['segments'][0]['length']==1
        assert result['workbench']['unit_declaration']['geometric_corroboration'][0]['checked_axes']==2
    else:
        with pytest.raises(ValueError,match='两边'):workbench.prepare_pipe(index,operations,lp)
    assert src.read_bytes()==before


@pytest.mark.parametrize('g,expected', [(geometry(centerline_point=[1195,0]),.195),(None,None)])
def test_actual_duct_cli_serializes_known_or_null_and_protects_input(tmp_path,g,expected):
    source=tmp_path/'input.json';target=tmp_path/'output.json'
    source.write_text(json.dumps(data(fittings=[fitting(g)])),encoding='utf-8');before=source.read_bytes()
    run=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-duct-air-systems/scripts/infer_duct_fittings_vertical.py'),
                        '--input',str(source),'--output',str(target)],stdin=subprocess.DEVNULL,capture_output=True,text=True)
    assert run.returncode==0,run.stdout+run.stderr
    assert source.read_bytes()==before
    result=json.loads(target.read_text(encoding='utf-8'))
    actual=result['fittings'][0]['centerline_length']
    assert actual==pytest.approx(expected) if expected is not None else actual is None
    assert result['business_adoption_allowed'] is False


def test_batch_cli_keeps_unlocated_rows_out_of_frame_counts_and_retains_ancestor_owned_children(tmp_path):
    import ezdxf
    import extract_cad_neutral as extract
    def put(path,value):path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');return path
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    source=tmp_path/'source.dwg';source.write_bytes(b'independent synthetic source')
    doc=ezdxf.new();doc.units=4;m=doc.modelspace()
    m.add_line((10,20),(90,20),dxfattribs={'layer':'PIPE'})
    global_spline=m.add_spline([(0,0),(10,30),(40,0)],dxfattribs={'layer':'UNKNOWN'})
    b=doc.blocks.new('SHAPE');child=b.add_spline([(0,0),(10,30),(40,0)])
    m.add_blockref('SHAPE',(250,50),dxfattribs={'layer':'UNKNOWN'})
    exchange=tmp_path/'source.dxf';doc.saveas(exchange)
    rows=[extract.convert(e,layout='Model',parent_block=None,source_hash=digest(source),raw_unit='Millimeters',sequence=i) for i,e in enumerate(m)]
    blocks=[extract.convert(e,layout='Model',parent_block='SHAPE',source_hash=digest(source),raw_unit='Millimeters',sequence=i) for i,e in enumerate(b)]
    layout=tmp_path/'entities.ndjson';blockfile=tmp_path/'blocks.ndjson'
    layout.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    blockfile.write_text(''.join(json.dumps(r)+'\n' for r in blocks),encoding='utf-8')
    manifest=put(tmp_path/'manifest.json',dict(schema_version='qto-cad-neutral-evidence/1.0',geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source={'path':str(source),'sha256':digest(source)},exchange_dxf={'path':str(exchange),'sha256':digest(exchange)},
        drawing_unit={'insunits_code':4,'raw_unit':'Millimeters'},blocks=[{'block_name':'SHAPE','origin':[0,0,0]}],
        artifacts={'layout_entities':{'path':str(layout),'sha256':digest(layout)},'block_definitions':{'path':str(blockfile),'sha256':digest(blockfile)}}))
    frames=put(tmp_path/'frames.json',dict(schema_version=batch.FRAMES_SCHEMA,frames=[{'frame_id':'A','bounds':[0,0,100,100]},{'frame_id':'B','bounds':[200,0,300,100]}]))
    mapping=put(tmp_path/'mapping.json',dict(schema_version=batch.MAPPING_SCHEMA,mapping_id='SYN',mapping_version='1',expected_source_sha256=digest(source),
        recognized_by='Codex synthetic working recognition',recognized_at='2026-10-15T08:00:00+08:00',source_id='legend',network_id_prefix='W',
        evidence=[dict(evidence_id='E',path=str(layout),sha256=digest(layout),kind='cad_property',locator='Model',observation='synthetic PIPE centreline')],
        unit={'raw_unit':'Millimeters','output_unit':'m','factor_to_output':.001,'source_id':'E'},
        families=[dict(family_id='P',kind='layer',layer_pattern='^PIPE$',system='W',decision='include',evidence_ids=['E'],basis='synthetic drawing')],
        use_conventional_terminal_type_candidates=False,use_conventional_valve_type_candidates=False))
    before={p:p.read_bytes() for p in (source,exchange,layout,blockfile,manifest,mapping,frames)}
    output=tmp_path/'output'
    run=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/build_pipe_inference_inputs_batch.py'),
                        '--cad-manifest',str(manifest),'--frames',str(frames),'--mapping',str(mapping),'--output-dir',str(output)],
                       stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8')
    assert run.returncode==0,run.stdout+run.stderr
    assert all(p.read_bytes()==old for p,old in before.items())
    summary=json.loads((output/'批量转换汇总.json').read_text(encoding='utf-8'))
    assert summary['status']=='REVIEW_REQUIRED' and summary['business_adoption_allowed'] is False
    unlocated=summary['drawing_level_unlocated_objects']
    assert unlocated['count']==1 and summary['counts']['unlocated_objects']==1
    global_rows=[json.loads(line) for line in Path(unlocated['pending_detail']).read_text(encoding='utf-8').splitlines()]
    assert global_rows[0]['object_id']=='CAD-'+global_spline.dxf.handle and global_rows[0]['frame_id'] is None
    details=[json.loads(line) for line in (output/'待办明细.jsonl').read_text(encoding='utf-8').splitlines()]
    assert all(r['object_id']!='CAD-'+global_spline.dxf.handle for r in details)
    nested=[r for r in details if '/'+child.dxf.handle in r['object_id'] or '/CAD-'+child.dxf.handle in r['object_id']]
    assert len(nested)==1 and nested[0]['frame_id']=='B' and nested[0]['location_basis']=='ancestor_insertion_point'
    inference=json.loads((output/'A/管网推算输入.json').read_text(encoding='utf-8'))
    assert sum(s['length'] for s in inference['segments'])==pytest.approx(.08)
    bframe=next(f for f in summary['frames'] if f['frame_id']=='B')
    assert bframe['status']=='NO_PIPES' and bframe['pending_line_objects']==1
