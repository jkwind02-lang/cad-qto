import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import ezdxf
import pytest
from test_pipe_detail_routes import fixture,changed,m

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/qto-pipe-networks/scripts'))
import partition_pipe_detail_specs as p
from bind_pipe_callouts import bind_callouts


def prepared(tmp_path):
    detail,doc=fixture(tmp_path)
    cap=doc.modelspace().add_line((-.2,7),(.2,7))
    labels=[doc.modelspace().add_text(t,dxfattribs={'insert':(1,y),'height':.2}) for t,y in [('DN80',8),('DN50',6)]]
    changed(detail,doc)
    f=tmp_path/'paths.json';f.write_text(json.dumps(detail),encoding='utf8')
    specs=[dict(kind='constant_dn',dn=n,annotations=[dict(path=[e.dxf.handle],text=e.dxf.text)],review_note='direct label') for n,e in zip([80,50],labels)]
    cut=dict(segment=0,point=dict(path=[cap.dxf.handle],fraction=.5))
    return dict(schema_version=p.VERSION,detail_input=dict(path=str(f),sha256=hashlib.sha256(f.read_bytes()).hexdigest()),partitions=[dict(physical_group_id='A',physical_route_id='supply',zones=[
        dict(**{'from':dict(segment=0,end='start'),'to':cut},specification=specs[0]),
        dict(**{'from':cut,'to':dict(segment=4,end='end')},specification=specs[1])])]),detail,doc


def test_mixed_dn_conserves_curve_and_component_axis(tmp_path):
    data,detail,_=prepared(tmp_path);r=p.partition(data)['routes'][0]
    assert r['axis_length_by_spec_m']['DN80']==pytest.approx(3)
    assert sum(r['axis_length_by_spec_m'].values())==pytest.approx(16+math.pi/2)
    assert r['billable_pipe_length_m'] is None
    assert r['specification_coverage']=='complete'


@pytest.mark.parametrize('mutation,match',[
    (lambda d:d.update(partitions=[]),'为空'),
    (lambda d:d['partitions'].append(copy.deepcopy(d['partitions'][0])),'重复'),
    (lambda d:d['partitions'][0]['zones'].pop(),'终点'),
    (lambda d:d['partitions'][0]['zones'][1].update(**{'from':dict(segment=1,end='start')}),'遗漏'),
    (lambda d:d['partitions'][0]['zones'][1].update(**{'from':dict(segment=0,end='start')}),'重叠'),
    (lambda d:d['partitions'][0]['zones'][0]['specification'].update(dn=100),'DN不在'),
    (lambda d:d['partitions'][0]['zones'][0]['specification'].update(dn=True),'正整数'),
    (lambda d:d['partitions'][0]['zones'][0]['specification']['annotations'][0].update(text='DN100'),'原文'),
    (lambda d:d['partitions'][0]['zones'][0]['specification'].update(annotations=[]),'文字证据'),
    (lambda d:d['partitions'][0]['zones'][0]['specification'].update(review_note=''),'判读'),
    (lambda d:d['partitions'][0]['zones'][0]['to'].update(segment=True),'索引'),
])
def test_bad_partitions_fail(tmp_path,mutation,match):
    data,_,_=prepared(tmp_path);mutation(data)
    with pytest.raises(ValueError,match=match):p.partition(data)


def test_explicit_unknown_keeps_quantity_but_not_full_spec_coverage(tmp_path):
    data,_,_=prepared(tmp_path)
    data['partitions'][0]['zones'][1]['specification']=dict(kind='unresolved',review_note='two labels disagree',issue='DN50/65 conflict')
    r=p.partition(data)['routes'][0]
    assert r['specification_coverage']=='partial' and r['axis_length_by_spec_m']['规格未决']>0


def test_off_axis_cut_rejected(tmp_path):
    data,detail,doc=prepared(tmp_path)
    h=data['partitions'][0]['zones'][0]['to']['point']['path'][0]
    doc.entitydb[h].translate(1,0,0);changed(detail,doc)
    f=Path(data['detail_input']['path']);f.write_text(json.dumps(detail),encoding='utf8');data['detail_input']['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='不在当前'):p.partition(data)


def test_zero_dimension_not_a_scale_proof(tmp_path):
    data,doc=fixture(tmp_path);dim=doc.entitydb[data['routes'][0]['dimension_paths'][0][0]]
    dim.dxf.defpoint3=dim.dxf.defpoint2;changed(data,doc)
    with pytest.raises(ValueError,match='退化'):m.measure(data)


@pytest.mark.parametrize('angles,overlap',[((45,135),True),((90,180),False),((270,315),False),((350,45),True)])
def test_partial_arc_overlap_not_just_equal_whole_curves(angles,overlap):
    doc=ezdxf.new();a=doc.modelspace().add_arc((0,0),5,0,90);b=doc.modelspace().add_arc((0,0),5,*angles)
    draw=m.Drawing(doc);parts=[draw.part({'path':[e.dxf.handle]}) for e in (a,b)]
    assert m.overlapping_conics(*parts,.00001)==overlap


def test_mirrored_ellipse_partial_overlap():
    a=dict(kind='ellipse_arc',center=[0,0,0],u=[3,0,0],v=[1,2,0],start_param=0,sweep_param=math.pi/2)
    b=dict(a,u=[-3,0,0],start_param=math.pi/2,sweep_param=math.pi/4)
    assert m.overlapping_conics(a,b,1e-6)
    b['center']=[0,0,1]
    assert not m.overlapping_conics(a,b,1e-6)


def callout_fixture(gap=0,nested=False):
    doc=ezdxf.new();body=doc.blocks.new('valve');body.add_circle((0,0),1)
    space=doc.blocks.new('parent') if nested else doc.modelspace()
    instance=space.add_blockref('valve',(0,0));leader=space.add_line((0,0),(10-gap,0))
    box=space.add_lwpolyline([(10,-2),(30,-2),(30,2),(10,2)],close=True)
    text=space.add_text('DN80 VALVE',dxfattribs={'insert':(11,-1),'height':2})
    parent=doc.modelspace().add_blockref('parent',(100,20),dxfattribs={'rotation':90,'xscale':2,'yscale':2}) if nested else None
    path=[parent.dxf.handle] if parent else []
    return doc,space,instance,leader,box,text,path


@pytest.mark.parametrize('gap,state',[(0,'bound'),(.08,'bound'),(.11,'unmatched')])
def test_bounded_box_gap_is_recorded(gap,state):
    doc,_,ins,_,_,_,parent=callout_fixture(gap)
    row=bind_callouts(doc,parent,[parent+[ins.dxf.handle]],r'DN\d+ VALVE',text_height_fraction=.05,tolerance_basis='synthetic gap boundary test')[0]
    assert row['state']==state
    if state=='bound':assert row['candidates'][0]['leader_box_gap_raw']==pytest.approx(gap)


def test_no_nearest_text_fallback():
    doc,space,ins,line,*_=callout_fixture();space.delete_entity(line)
    assert bind_callouts(doc,[],[[ins.dxf.handle]],r'DN\d+ VALVE')[0]['state']=='unmatched'


def test_ambiguous_caption_does_not_seed_dn():
    doc,space,ins,*_=callout_fixture();space.add_text('DN100 VALVE',dxfattribs={'insert':(11,.5),'height':2})
    assert bind_callouts(doc,[],[[ins.dxf.handle]],r'DN\d+ VALVE')[0]['state']=='ambiguous'


def test_nested_identity_and_world_location():
    doc,_,ins,_,_,_,path=callout_fixture(nested=True)
    row=bind_callouts(doc,path,[path+[ins.dxf.handle]],r'DN\d+ VALVE')[0]
    assert row['state']=='bound';assert row['location_wcs']==pytest.approx([100,20,0])


def test_one_caption_cannot_double_count_two_instances():
    doc,space,ins,*_=callout_fixture();second=space.add_blockref('valve',(0,5));space.add_line((0,5),(10,0))
    rows=bind_callouts(doc,[],[[ins.dxf.handle],[second.dxf.handle]],r'DN\d+ VALVE')
    assert all(r['state']=='shared_caption_requires_review' for r in rows)
