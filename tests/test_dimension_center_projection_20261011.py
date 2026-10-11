"""Source rectangle center projection: this is not spatial point coincidence."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
import qto_dimension_semantics as m


def case(tmp_path, vertices=None, witness=((0,25),(75,25)), closed=True, insert=None, units=4, scale=1, nested=False):
    doc=ezdxf.new();doc.units=units
    space=doc.blocks.new('INNER') if insert else doc.modelspace()
    vertices=vertices or [(-225,-225),(225,-225),(225,225),(-225,225)]
    e=space.add_lwpolyline([(p[0]*scale,p[1]*scale) for p in vertices],close=closed)
    axis=space.add_line((75*scale,-1000*scale),(75*scale,1000*scale))
    dm=space.add_linear_dim(base=(0,700*scale),p1=tuple(x*scale for x in witness[0]),p2=tuple(x*scale for x in witness[1]),angle=0);dm.render()
    prefix='CADINST-Model/'
    if insert:
        if nested:
            outer=doc.blocks.new('OUTER');inner=outer.add_blockref('INNER',(300,400));parent=doc.modelspace().add_blockref('OUTER',(1e7,-2e7),dxfattribs=insert)
            prefix+=parent.dxf.handle+'/'+inner.dxf.handle+'/'
        else:
            parent=doc.modelspace().add_blockref('INNER',(1e7,-2e7),dxfattribs=insert);prefix+=parent.dxf.handle+'/'
    path=tmp_path/'source.dxf';doc.saveas(path)
    data=dict(schema_version=m.SCHEMA,sources=[dict(source_id='S',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),view_basis='native source projected dimension')],
      targets=[dict(target_id='CENTER',source_id='S',view_id='PLAN',physical_object_key='column',reference_id='center',role='axis',
                    object_id=prefix+e.dxf.handle,selector={'kind':'rectangle_center_projection'},basis='center of source closed rectangle'),
               dict(target_id='AXIS',source_id='S',view_id='PLAN',physical_object_key='grid',reference_id='station',role='axis',
                    object_id=prefix+axis.dxf.handle,selector={'kind':'on_line'},basis='source grid line')],
      dimensions=[dict(dimension_id='OFFSET',source_id='S',view_id='PLAN',object_id=prefix+dm.dimension.dxf.handle,basis='source projected offset',
        candidates=[dict(candidate_id='C',measurement_kind='projected_length',basis='source center station and grid',endpoints=[{'target_id':'CENTER'},{'target_id':'AXIS'}])],
        selected_candidate_id='C',selection_basis='model selected the column center projection')],constraints=[])
    return data,doc,path,e,dm.dimension


def save(doc,path,data):
    doc.saveas(path);data['sources'][0]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()


def fact(data):return m.review_dimension_semantics(data)['dimensions'][0]


@pytest.mark.parametrize('offset',[0,25,-100,5000])
def test_projection_offset_not_confused_with_source_point(tmp_path,offset):
    data,*_=case(tmp_path,witness=((0,offset),(75,offset)))
    # Keep the direct endpoint finite for the far projected-center case.
    if abs(offset)>1000:
        data,doc,path,_,_=case(tmp_path,witness=((0,offset),(75,offset)))
        line=doc.entitydb[data['targets'][1]['object_id'].split('/')[-1]];line.dxf.end=(75,offset+100);save(doc,path,data)
    f=fact(data);check=f['selected']['endpoint_checks'][0]
    assert f['usable_for_working_calculation']
    assert f['value_m']==pytest.approx(.075)
    assert check['match_mode']=='measurement_projection'
    assert check['source_center_wcs']==pytest.approx([0,0,0])
    assert check['point_coincident'] is (offset==0)
    assert check['projection_offset_distance_raw']==pytest.approx(abs(offset))
    assert check['projection_offset_vector_wcs']==pytest.approx([0,offset,0])
    assert check['measurement_station_error_raw']==pytest.approx(0)
    assert f['formal_adoption_allowed'] is False and f['semantic_truth_verified'] is False


@pytest.mark.parametrize('transform',[
    {'rotation':32},{'xscale':-1,'rotation':-21},{'xscale':2,'yscale':.4,'rotation':37},
])
@pytest.mark.parametrize('nested',[False,True])
def test_real_nested_affine_instance_keeps_design_offset(tmp_path,transform,nested):
    data,*_=case(tmp_path,insert=transform,nested=nested)
    f=fact(data)
    assert f['usable_for_working_calculation'],f
    assert f['value_m']==pytest.approx(.075)
    assert f['selected']['endpoint_checks'][0]['point_coincident'] is False


@pytest.mark.parametrize('vertices,closed',[
    ([(-225,-225),(225,-225),(225,225),(-225,225)],False),
    ([(-225,-225),(225,-225),(175,225),(-175,225)],True),
    ([(-225,-225),(225,225),(225,-225),(-225,225)],True),
    ([(-225,-225),(225,-225),(0,0),(-225,225)],True),
    ([(-225,-225),(225,-225),(225,225),(0,300),(-225,225)],True),
    ([(-225,-225),(225,-225),(225,-225),(-225,225)],True),
])
def test_bbox_center_does_not_admit_nonrectangles(tmp_path,vertices,closed):
    data,*_=case(tmp_path,vertices=vertices,closed=closed)
    assert not fact(data)['usable_for_working_calculation']


@pytest.mark.parametrize('fault',['along_axis_shift','out_of_plane','wrong_measurement_axis','aligned_semantics','manual_center','part_only','arc_edge','foreign_instance'])
def test_projection_specific_counterexamples(tmp_path,fault):
    data,doc,path,rect,dim=case(tmp_path)
    if fault=='along_axis_shift':dim.dxf.defpoint2=(1,25,0)
    elif fault=='out_of_plane':
        rect.dxf.elevation=100
    elif fault=='wrong_measurement_axis':dim.dxf.angle=90;dim.dxf.defpoint3=(75,100,0)
    elif fault=='aligned_semantics':
        dim.dxf.dimtype=(dim.dxf.dimtype&~15)|1;data['dimensions'][0]['candidates'][0]['measurement_kind']='aligned_length'
    elif fault=='manual_center':data['targets'][0]['selector']['center']=[0,0,0]
    elif fault=='part_only':data['targets'][0]['selector']['part_index']=0
    elif fault=='arc_edge':rect.set_points([(-225,-225,1),(225,-225,0),(225,225,0),(-225,225,0)],format='xyb')
    elif fault=='foreign_instance':data['targets'][0]['object_id']='CADINST-Model/not-real/'+rect.dxf.handle
    save(doc,path,data)
    try:r=fact(data)
    except ValueError:return
    assert not r['usable_for_working_calculation']


@pytest.mark.parametrize('unit,scale',[(4,1),(6,.001),(5,.1),(1,1/25.4)])
def test_projection_distance_uses_source_length_unit(tmp_path,unit,scale):
    data,*_=case(tmp_path,units=unit,scale=scale)
    f=fact(data)
    assert f['usable_for_working_calculation']
    assert f['value_m']==pytest.approx(.075)


def test_two_definitions_cannot_use_projection_to_fake_direct_coincidence(tmp_path):
    data,doc,path,rect,dim=case(tmp_path)
    data['targets'][0]['selector']={'kind':'on_line'}
    assert not fact(data)['usable_for_working_calculation']


def test_steel_adapter_uses_actual_centers_not_witness_line(tmp_path):
    data,doc,path,rect,dim=case(tmp_path)
    other=doc.modelspace().add_lwpolyline([(65,15),(85,15),(85,35),(65,35)],close=True)
    data['targets'][1].update(object_id='CADINST-Model/'+other.dxf.handle,selector={'kind':'rectangle_center_projection'},role='axis')
    save(doc,path,data)
    assert fact(data)['usable_for_working_calculation']
    # Witness line is horizontal, but true selected centers differ in both X/Y.
    with pytest.raises(ValueError,match='投影跨度'):
        m.steel_length_derivation(data,dict(span_dimension_id='OFFSET',basis='test true axis length',
            ends=[dict(end_id='start',dimension_ids=[]),dict(end_id='end',dimension_ids=[])]))
