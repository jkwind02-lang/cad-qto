"""Transfer tests for object operations, independent of project handles/layers."""
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'skills'/'_qto-shared-evidence'/'scripts'
sys.path.insert(0,str(SHARED))
import qto_recognition_workbench as w


def put(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    return path


def source(doc,tmp_path):
    path=tmp_path/'真实图纸.dxf';doc.saveas(path)
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def ledger(index,tmp_path,object_ids):
    src=index['authoritative_source']
    value=dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='MODEL',ledger_version='1',
        source_sha256=src['sha256'],adoption_scope='working',review_status='unreviewed',
        evidence=[dict(evidence_id='EV-source',path=src['path'],sha256=src['sha256'],source_sha256=src['sha256'],
            kind='cad_property',locator='source objects',observation='WCS source paths, DN text and selected endpoints')],
        entries=[dict(adjudication_id='PIPE',subject_id='branch',professional_owner='qto-pipe-networks',source_id='SRC',
            recognized_by='model',recognized_at='2026-10-11T10:00:00+08:00',basis='管段与管径文字逐对象观察',scope_basis='边界至两个末端',
            identity_basis='原图哈希和实例路径',cross_check='支路接口及无闭环',decision='include',decision_origin='model',status='active',
            object_ids=object_ids,evidence_ids=['EV-source'],uncertainties=[])])
    return put(tmp_path/'模型识别.json',value)


@pytest.fixture
def branch(tmp_path):
    d=ezdxf.new();d.units=4;m=d.modelspace()
    m.add_line((0,0),(2000,0),dxfattribs={'layer':'A'})
    m.add_line((2000,0),(2000,1000),dxfattribs={'layer':'A'})
    m.add_text('给水 DN32',dxfattribs={'insert':(500,60)})
    i=w.build_index(source(d,tmp_path));rows=i['objects']
    lines=[r['object_id'] for r in rows if r['entity_type']=='LINE']
    label=next(r['object_id'] for r in rows if r['text'])
    lp=ledger(i,tmp_path,[r['object_id'] for r in rows])
    o=dict(index_sha256=i['index_sha256'],network_id='portable',scope_object_ids=[r['object_id'] for r in rows],
        scope_basis='独立支路从入口到端口',unit='m',parameters=dict(snap_tolerance=.0001,gap_max=.1),
        parameter_basis='图面端点完整，接触容差0.1mm，缺口仅作为候选',
        assignments=[dict(adjudication_id='PIPE',object_ids=lines,role='pipe_centerline',system='W',observation='两条中心线在共同端点连接')],
        relations=[dict(relation_id='DN',kind='dn_annotation',target_object_id=lines[0],annotation_object_id=label,
            annotation_text='给水 DN32',dn='DN32',adjudication_id='PIPE',observation='DN32标于该连续支路，未跨变径'),
            dict(relation_id='ROOT',kind='root',target=dict(object_id=lines[0],end='start'),adjudication_id='PIPE',observation='入口端'),
            dict(relation_id='END',kind='terminal',target=dict(object_id=lines[1],end='end'),terminal_type='已识别末端接口',
                adjudication_id='PIPE',observation='末端位于该源线终点',port_dn='DN32',port_dn_annotation_object_ids=[label])],
        endpoint_review=dict(complete=True,basis='本测试图只有一条完整支路及一个末端'))
    return i,o,lp,tmp_path


def test_existing_pipe_inference_chain_consumes_operations(branch):
    i,o,lp,tmp=branch
    result=w.prepare_pipe(i,o,lp)
    assert sum(s['length'] for s in result['segments'])==pytest.approx(3)
    assert result['workbench']['unprocessed_objects']==[]
    assert result['recognition_basis']['formal_adoption_allowed'] is False
    assert result['terminals'][0]['port_dn']=='DN32' and result['terminals'][0]['port_evidence_level']=='inferred'
    ip=put(tmp/'input.json',result);op=tmp/'result.json'
    p=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'),
        '--input',str(ip),'--output',str(op)],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stdout+p.stderr
    output=json.loads(op.read_text(encoding='utf-8'))
    assert output['metrics']['raw_length']==pytest.approx(3)
    assert output['length_summary']['W']['规则推算量']['DN32']==pytest.approx(3)


@pytest.mark.parametrize('units,factor',[(4,1),(5,.1),(6,.001)])
@pytest.mark.parametrize('rotation',[0,31,90])
def test_nested_array_units_rotation_translation(units,factor,rotation,tmp_path):
    doc=ezdxf.new();doc.units=units
    b=doc.blocks.new('arbitrary name',base_point=(3*factor,4*factor))
    b.add_line((3*factor,4*factor),(103*factor,4*factor))
    ins=doc.modelspace().add_blockref('arbitrary name',(6000*factor,7000*factor),dxfattribs={
        'rotation':rotation,'xscale':2,'yscale':3,'row_count':2,'column_count':2,
        'row_spacing':300*factor,'column_spacing':400*factor})
    index=w.build_index(source(doc,tmp_path))
    rows=[r for r in index['objects'] if r['entity_type']=='LINE']
    assert len(rows)==4 and len({r['object_id'] for r in rows})==4
    assert all(r['raw_length']==pytest.approx(200*factor) for r in rows)
    for r in rows:assert r['anchor']==pytest.approx(r['geometry']['parts'][0]['start'])
    candidates=w.family_candidates(index,rows[0]['object_id'])
    assert len(candidates['candidates'])==3
    assert candidates['semantic_equivalence_confirmed'] is False


def test_three_dimensional_parent_transform(tmp_path):
    doc=ezdxf.new();doc.units=4
    b=doc.blocks.new('tilted');line=b.add_line((2,3,4),(7,11,13))
    child=doc.blocks.new('parent');ins=child.add_blockref('tilted',(17,23,29),dxfattribs={'xscale':2,'yscale':3,'zscale':4,'extrusion':(0,1,0),'rotation':19})
    outer=doc.modelspace().add_blockref('parent',(1000,2000,3000),dxfattribs={'rotation':37,'xscale':1.5,'yscale':.75})
    index=w.build_index(source(doc,tmp_path));row=next(r for r in index['objects'] if r['entity_type']=='LINE')
    for key in ('start','end'):
        expected=outer.matrix44().transform(ins.matrix44().transform(getattr(line.dxf,key)))
        assert row['geometry']['parts'][0][key]==pytest.approx(list(expected))


def test_coincident_array_cells_are_separate_identities(tmp_path):
    d=ezdxf.new();b=d.blocks.new('A');b.add_line((0,0),(10,0))
    d.modelspace().add_blockref('A',(0,0),dxfattribs={'row_count':2,'column_count':2,'row_spacing':0,'column_spacing':0})
    i=w.build_index(source(d,tmp_path));rows=[r for r in i['objects'] if r['entity_type']=='LINE']
    assert len(rows)==4 and len({r['object_id'] for r in rows})==4
    assert len({tuple(r['anchor']) for r in rows})==1


def test_unknown_geometry_never_disappears_outside_window(tmp_path):
    d=ezdxf.new();d.modelspace().add_spline(fit_points=[(0,0),(1,3),(2,0)]);d.modelspace().add_line((0,0),(10,0))
    i=w.build_index(source(d,tmp_path));c=w.context(i,dict(bounds=[1000,1000,2000,2000]))
    assert len(c['objects'])==0 and len(c['unlocated_objects'])==1
    assert c['unlocated_objects'][0]['geometry_status']=='unresolved'


def test_context_no_implicit_nearest_text_binding(branch):
    i,*_=branch
    c=w.context(i,dict(text_pattern='DN32',context_padding_raw=1000,neighbor_distance_raw=1))
    assert c['matched_count']>=2
    assert all(n['status']=='candidate_only' for n in c['neighbor_candidates'])
    svg=w.render_svg(c)
    assert i['index_sha256'] in svg and '<script' not in svg and 'http://' in svg


def test_pagination_discloses_omitted_objects(branch):
    i,*_=branch;c=w.context(i,dict(max_objects=1))
    assert c['matched_count']==3 and len(c['omitted_object_ids'])==2


@pytest.mark.parametrize('field',['source','index','operation_hash'])
def test_changed_evidence_or_context_rejected(branch,field):
    i,o,lp,_=branch
    if field=='source':Path(i['source']['path']).write_bytes(b'changed')
    elif field=='index':i['objects'][0]['geometry']['parts'][0]['start'][0]+=1
    else:o['index_sha256']='0'*64
    with pytest.raises(ValueError):w.prepare_pipe(i,o,lp)


def test_declared_dn_must_match_current_source_text(branch):
    i,o,lp,_=branch;o['relations'][0]['dn']='DN100'
    with pytest.raises(ValueError,match='DN文字'):w.prepare_pipe(i,o,lp)


def test_terminal_dn_cannot_come_from_type_default(branch):
    i,o,lp,_=branch;o['relations'][-1]['port_dn']='DN50'
    with pytest.raises(ValueError,match='DN文字'):w.prepare_pipe(i,o,lp)
    o['relations'][-1]['port_dn_annotation_object_ids']=[]
    with pytest.raises(ValueError,match='真实标注'):w.prepare_pipe(i,o,lp)


def test_unknown_objects_prevent_complete_claim(branch):
    i,o,lp,_=branch;o['assignments'][0]['object_ids']=o['assignments'][0]['object_ids'][:1];o['relations']=o['relations'][:2]
    with pytest.raises(ValueError,match='完整'):w.prepare_pipe(i,o,lp)
    o['endpoint_review']['complete']=False
    result=w.prepare_pipe(i,o,lp)
    assert len(result['workbench']['unprocessed_objects'])==1


def test_nonuniform_same_named_block_is_exception(tmp_path):
    d=ezdxf.new();b=d.blocks.new('same');b.add_line((0,0),(20,0))
    d.modelspace().add_blockref('same',(0,0));d.modelspace().add_blockref('same',(100,0),dxfattribs={'xscale':2})
    i=w.build_index(source(d,tmp_path));seed=next(r['object_id'] for r in i['objects'] if r['entity_type']=='INSERT')
    result=w.family_candidates(i,seed)
    assert result['candidates']==[] and len(result['exceptions'])==1


def test_curve_not_replaced_by_chord(tmp_path):
    d=ezdxf.new();d.units=4;d.modelspace().add_arc((0,0),1000,0,90)
    i=w.build_index(source(d,tmp_path));oid=i['objects'][0]['object_id'];lp=ledger(i,tmp_path,[oid])
    o=dict(index_sha256=i['index_sha256'],network_id='curve',scope_object_ids=[oid],scope_basis='explicit arc',unit='m',
        parameters=dict(snap_tolerance=.001,gap_max=.1),parameter_basis='test source precision',
        assignments=[dict(adjudication_id='PIPE',object_ids=[oid],role='pipe_centerline',system='W',observation='弧为已识别管道中心线')],
        endpoint_review=dict(complete=False,basis='端口尚未处置'))
    result=w.prepare_pipe(i,o,lp)
    assert result['segments'][0]['length']==pytest.approx(math.pi/2)
    assert result['segments'][0]['geometry']['parts'][0]['kind']=='ellipse_arc'


def test_unitless_needs_source_unit_text(tmp_path):
    d=ezdxf.new();d.units=0;d.modelspace().add_line((0,0),(1000,0));d.modelspace().add_text('标高以米，其余尺寸为毫米')
    i=w.build_index(source(d,tmp_path));oids=[r['object_id'] for r in i['objects']];lp=ledger(i,tmp_path,oids)
    line=next(r['object_id'] for r in i['objects'] if r['geometry']);note=next(r['object_id'] for r in i['objects'] if r['text'])
    o=dict(index_sha256=i['index_sha256'],network_id='unit',scope_object_ids=oids,scope_basis='unit note and route',unit='m',
        parameters=dict(snap_tolerance=.001,gap_max=.1),parameter_basis='毫米原图',
        assignments=[dict(adjudication_id='PIPE',object_ids=[line],role='pipe_centerline',system='W',observation='中心线')],
        endpoint_review=dict(complete=False,basis='未决端口'))
    with pytest.raises(ValueError,match='单位未设'):w.prepare_pipe(i,o,lp)
    o['unit_declaration']=dict(raw_unit='Millimeters',adjudication_id='PIPE',annotation_object_ids=[note],observation='图面平面尺寸为毫米，标高独立为米',source_excerpt='其余尺寸为毫米')
    with pytest.raises(ValueError,match='applies_to'):w.prepare_pipe(i,o,lp)
    # registration 20261015-0155: half of a mixed m/mm sentence cannot fix the coordinate unit by itself
    o['unit_declaration']['applies_to']='annotation_values'
    with pytest.raises(ValueError,match='geometric_corroboration'):w.prepare_pipe(i,o,lp)
    o['unit_declaration']['applies_to']='coordinates'
    with pytest.raises(ValueError,match='geometric_corroboration'):w.prepare_pipe(i,o,lp)
    known=dict(kind='known_component_size',real_length_m=1.0,measured_drawing_units=1000,object_ids=[line],basis='设备表：该段为 1.0 m 定尺短管')
    o['unit_declaration']['geometric_corroboration']=[dict(known,real_length_m=2.0)]
    with pytest.raises(ValueError,match='不符'):w.prepare_pipe(i,o,lp)
    o['unit_declaration']['geometric_corroboration']=[known]
    result=w.prepare_pipe(i,o,lp)
    assert result['segments'][0]['length']==1
    assert result['workbench']['source_declared_unit']=='Unitless'
    assert result['workbench']['unit_declaration']['geometric_corroboration'][0]['implied_metres_per_unit']==pytest.approx(.001)


def test_output_cannot_overwrite_original(branch):
    i,o,lp,tmp=branch;ip=put(tmp/'index.json',i);op=put(tmp/'operations.json',o)
    before=Path(i['source']['path']).read_bytes()
    p=subprocess.run([sys.executable,'-B',str(SHARED/'qto_recognition_workbench.py'),'apply','--index',str(ip),
        '--operations',str(op),'--ledger',str(lp),'--output',i['source']['path']],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode!=0
    assert Path(i['source']['path']).read_bytes()==before


@pytest.mark.parametrize('text,expected',[('尺寸单位：毫米',{.001}),('dimensions mm',{.001}),('单位厘米',{.01}),
    ('metres and centimeters',{1.,.01}),('标高以米，其余尺寸为毫米',{1.,.001}),('room',set()),('毫米与厘米冲突',{.001,.01})])
def test_unit_tokens_never_match_inside_other_unit(text,expected):
    assert w._unit_tokens(text)==expected


def test_local_index_preserves_unknown_container_and_prunes_known_geometry(tmp_path):
    d=ezdxf.new();known=d.blocks.new('known');known.add_line((0,0),(20,0))
    partial=d.blocks.new('partial');partial.add_line((0,0),(20,0));partial.add_text('说明')
    d.modelspace().add_blockref('known',(100000,100000))
    d.modelspace().add_blockref('partial',(100000,100000))
    d.modelspace().add_line((0,0),(100,0))
    i=w.build_index(source(d,tmp_path),scope_bounds=[-1,-1,101,101])
    assert len(i['objects'])==2
    assert len([r for r in i['objects'] if r.get('expansion_state')=='pending_scope_uncertain'])==1
    c=w.context(i,dict(bounds=[0,0,100,100]))
    assert len(c['unlocated_objects'])==1
    with pytest.raises(ValueError,match='超出'):w.context(i,dict(bounds=[0,0,100000,100000]))


def test_located_unmeasured_hatch_and_spline_stay_unmeasured(tmp_path):
    d=ezdxf.new();d.units=4
    hatch=d.modelspace().add_hatch();hatch.paths.add_polyline_path([(0,0),(100,0),(100,100),(0,100)],is_closed=True)
    d.modelspace().add_open_spline([(10000,0),(10100,100),(10200,0),(10300,0)],degree=3)
    i=w.build_index(source(d,tmp_path),scope_bounds=[-1,-1,101,101])
    assert len(i['objects'])==1
    row=i['objects'][0]
    assert row['entity_type']=='HATCH' and row['geometry_status']=='unsupported_measurement'
    assert row['bbox']==pytest.approx([0,0,100,100]) and row['geometry'] is None and row['raw_length'] is None
    assert row['measurement_geometry_available'] is False and row['location_status']=='bounded'
    assert row['location_bbox3d']==pytest.approx([0,0,0,100,100,0])


@pytest.mark.parametrize('mode,kept', [('positive',False),('negative',True),('zero',True),('fit_only',True)])
def test_workbench_uses_only_proven_spline_location_hulls_for_scope(tmp_path,mode,kept):
    d=ezdxf.new();d.units=4
    points=[(10000,0),(10100,100),(10200,0),(10300,0)]
    if mode=='fit_only':
        d.modelspace().add_spline(points,degree=3)
    else:
        spline=d.modelspace().add_open_spline(points,degree=3)
        spline.weights={'positive':[1,2,1,1],'negative':[1,-1,1,1],'zero':[1,0,1,1]}[mode]
    i=w.build_index(source(d,tmp_path),scope_bounds=[-1,-1,101,101])
    assert bool(i['objects']) is kept
    assert i['inventory']['total']==int(kept)
    if kept:
        row=i['objects'][0]
        assert row['geometry'] is row['raw_length'] is row['bbox'] is None
        assert row['issues'] and row['geometry_status']=='unresolved'
    else:
        assert i['index_scope']['proven_geometry_or_text_anchor_outside_count']==1


def test_workbench_unknown_open_hatch_is_retained_without_a_guessed_envelope(tmp_path):
    d=ezdxf.new();h=d.modelspace().add_hatch()
    h.paths.add_edge_path().add_line((10000,0),(10100,0))
    i=w.build_index(source(d,tmp_path),scope_bounds=[-1,-1,101,101])
    assert len(i['objects'])==1
    row=i['objects'][0]
    assert row['bbox'] is row['geometry'] is row['raw_length'] is None and row['issues']


def test_workbench_nested_hatch_envelope_preserves_affine_location_and_crossing_scope(tmp_path):
    d=ezdxf.new();d.units=4
    leaf=d.blocks.new('LEAF',base_point=(2,3,0));h=leaf.add_hatch()
    h.paths.add_polyline_path([(0,0),(8,0),(0,4)],is_closed=True)
    middle=d.blocks.new('MID')
    inner=middle.add_blockref('LEAF',(5,7),dxfattribs={'rotation':23,'xscale':-2,'yscale':.5})
    outer=d.modelspace().add_blockref('MID',(1000,2000),dxfattribs={'rotation':37,'xscale':3,'yscale':-.5})
    src=source(d,tmp_path)
    # An independent Matrix44 composition bounds the local hull's corners, including the block base point.
    corners=[outer.matrix44().transform(inner.matrix44().transform((x,y,0))) for x in (0,8) for y in (0,4)]
    box=[min(p[k] for p in corners) for k in range(3)]+[max(p[k] for p in corners) for k in range(3)]
    i=w.build_index(src)
    row=next(r for r in i['objects'] if r['entity_type']=='HATCH')
    assert row['bbox3d']==pytest.approx(box) and row['location_bbox3d']==pytest.approx(box)
    assert row['geometry'] is row['raw_length'] is None and row['measurement_geometry_available'] is False
    assert row['geometry_status']=='unsupported_measurement' and row['source_handle']==h.dxf.handle
    crossing=[box[0]-1,box[1]-1,box[0]+1,box[4]+1]
    local=w.build_index(src,scope_bounds=crossing)
    assert any(r['entity_type']=='HATCH' for r in local['objects'])  # a crossing envelope cannot be pruned by its centre
    far=w.build_index(src,scope_bounds=[box[3]+10000,box[4]+10000,box[3]+10100,box[4]+10100])
    assert far['objects']==[]


def test_source_excerpt_cannot_cut_mm_into_m(tmp_path):
    d=ezdxf.new();d.units=0;d.modelspace().add_line((0,0),(1000,0));d.modelspace().add_text('尺寸单位：毫米')
    i=w.build_index(source(d,tmp_path));ids=[r['object_id'] for r in i['objects']];lp=ledger(i,tmp_path,ids)
    line=next(r['object_id'] for r in i['objects'] if r['geometry']);note=next(r['object_id'] for r in i['objects'] if r['text'])
    o=dict(index_sha256=i['index_sha256'],network_id='unit',scope_object_ids=ids,scope_basis='unit note and route',unit='m',
        parameters=dict(snap_tolerance=.001,gap_max=.1),parameter_basis='毫米原图',
        assignments=[dict(adjudication_id='PIPE',object_ids=[line],role='pipe_centerline',system='W',observation='中心线')],
        endpoint_review=dict(complete=False,basis='未决端口'),unit_declaration=dict(raw_unit='Meters',adjudication_id='PIPE',
            annotation_object_ids=[note],source_excerpt='米',observation='incorrect extraction',applies_to='coordinates'))
    with pytest.raises(ValueError,match='截断'):w.prepare_pipe(i,o,lp)
    o['unit_declaration'].pop('source_excerpt')
    with pytest.raises(ValueError,match='单位原文'):w.prepare_pipe(i,o,lp)


def test_valve_gap_and_change_relations_use_source_geometry(tmp_path):
    d=ezdxf.new();d.units=4;m=d.modelspace()
    m.add_line((0,0),(1000,0));m.add_line((1100,0),(2000,0))
    b=d.blocks.new('any-valve-name');b.add_circle((0,0),20)
    m.add_blockref('any-valve-name',(1050,0));m.add_text('DN25',dxfattribs={'insert':(500,50)})
    i=w.build_index(source(d,tmp_path));rows=i['objects'];ids=[r['object_id'] for r in rows]
    lp=ledger(i,tmp_path,ids);lines=[r['object_id'] for r in rows if r['entity_type']=='LINE']
    valve=next(r['object_id'] for r in rows if r['entity_type']=='INSERT');label=next(r['object_id'] for r in rows if r['text'])
    common=dict(adjudication_id='PIPE',observation='原图接口和在线占位逐个核对')
    o=dict(index_sha256=i['index_sha256'],network_id='gap',scope_object_ids=ids,scope_basis='含在线附件的支路',unit='m',
        parameters=dict(snap_tolerance=.001,gap_max=.2),parameter_basis='0.1m源断口',
        assignments=[dict(common,object_ids=lines,role='pipe_centerline',system='W')],
        relations=[dict(common,relation_id='dn',kind='dn_annotation',target_object_id=lines[0],annotation_object_id=label,annotation_text='DN25',dn='DN25'),
            dict(common,relation_id='valve',kind='valve',object_id=valve,connected_object_ids=lines,valve_type='source-reviewed valve'),
            dict(common,relation_id='gap',kind='gap_classification',a=dict(object_id=lines[0],end='end'),b=dict(object_id=lines[1],end='start'),
                classification='在线附件占位',quantity_action='exclude',scenario_include=False),
            dict(common,relation_id='change',kind='change_node',target=dict(object_id=lines[1],end='end'))],
        endpoint_review=dict(complete=False,basis='端部未完成识别'))
    result=w.prepare_pipe(i,o,lp)
    assert result['valves'][0]['point']==pytest.approx([1.05,0,0])
    assert result['change_nodes'][0]['point']==pytest.approx([2,0,0])
    assert result['gap_classifications'][0]['quantity_action']=='exclude'
    assert sum(s['length'] for s in result['segments'])==pytest.approx(1.9)
    assert result['valves'][0]['instance_level']=='inferred'
    assert result['valves'][0]['block_family']=='any-valve-name'
    ip=put(tmp_path/'with-valve.json',result);op=tmp_path/'valve-result.json'
    process=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'),
        '--input',str(ip),'--output',str(op)],capture_output=True,text=True,encoding='utf-8')
    assert process.returncode==0,process.stdout+process.stderr
    actual=json.loads(op.read_text(encoding='utf-8'))['valves'][0]
    assert actual['instance_level']=='inferred' and actual['candidate_type_source_id']=='EV-source'
    assert actual['type_source_ids']==['EV-source'] and actual['block_family']=='any-valve-name'


def test_render_uses_query_window_not_parent_extent(branch):
    i,*_=branch;c=w.context(i,dict(bounds=[0,-100,2100,1100]))
    c['objects'].append(dict(c['objects'][0],object_id='parent',entity_type='INSERT',geometry=None,
        bbox=[-1e9,-1e9,1e9,1e9],anchor=[0,0,0],text=None))
    svg=w.render_svg(c)
    assert 'drawing-window' in svg and '1106.667,736.667' in svg


def test_other_profession_exclusion_does_not_hide_pipe_unresolved(branch):
    i,o,lp,_=branch
    leftover=o['assignments'][0]['object_ids'].pop()
    o['relations']=o['relations'][:2];o['endpoint_review']['complete']=False
    l=json.loads(lp.read_text(encoding='utf-8'));extra=copy.deepcopy(l['entries'][0])
    extra.update(adjudication_id='SURFACE',professional_owner='qto-surfaces-envelope',decision='exclude',object_ids=[leftover])
    l['entries'].append(extra);put(lp,l)
    out=w.prepare_pipe(i,o,lp)
    assert leftover in {r['object_id'] for r in out['workbench']['unprocessed_objects']}


def test_duplicate_source_identity_blocks_apply(tmp_path):
    d=ezdxf.new();d.units=4;a=d.modelspace().add_line((0,0),(1000,0));b=d.modelspace().add_line((1000,0),(2000,0))
    src=source(d,tmp_path);p=Path(src['path']);text=p.read_text(encoding='utf-8')
    text=text.replace('\n  5\n'+b.dxf.handle+'\n','\n  5\n'+a.dxf.handle+'\n')
    p.write_text(text,encoding='utf-8');src['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='身份碰撞'):w.build_index(src)
