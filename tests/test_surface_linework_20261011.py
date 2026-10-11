"""Transfer tests for source-bound ordinary linework; no project answer fixtures."""
import copy
import hashlib
import importlib.util
import math
from pathlib import Path
import re

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('surface_linework_20261011', ROOT/'skills/qto-surfaces-envelope/scripts/extract_surface_linework.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fixture(tmp_path, lines, unit='m', insunits=6, bounds=None):
    doc = ezdxf.new()
    doc.units = insunits
    entities = [doc.modelspace().add_line(a,b) for a,b in lines]
    path = tmp_path/'source.dxf'
    doc.saveas(path)
    data = {'schema_version':m.VERSION, 'drawing':{'path':str(path),'sha256':m.digest(path)}, 'unit':unit, 'unit_evidence':'explicit test geometry units',
            'scope':{'view_id':'TEST-VIEW', 'interpretation':'selected boundary lines', 'bounds_raw':bounds or [-1,-1,20,20], 'plane_z_raw':0, 'plane_tolerance_m':1e-8},
            'segments':[{'path':[e.dxf.handle]} for e in entities]}
    return doc, entities, data


def rectangle(x,y,w,h):
    p=[(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
    return list(zip(p,p[1:]))


@pytest.mark.parametrize('factor,origin,unit,insunits', [(1,(0,0),'m',6),(1000,(0,0),'mm',4),(100,(10**9,-10**9),'cm',5)])
def test_unit_translation_and_actual_source_provenance(tmp_path,factor,origin,unit,insunits):
    x,y=origin
    lines=rectangle(x,y,4*factor,3*factor)
    _,_,data=fixture(tmp_path,lines,unit,insunits,[x-factor,y-factor,x+5*factor,y+4*factor])
    result=m.extract(data)
    assert len(result['faces']) == 1
    assert result['faces'][0]['area_m2'] == pytest.approx(12)
    assert result['faces'][0]['perimeter_m'] == pytest.approx(14)
    assert all(edge['sources'] for edge in result['faces'][0]['boundary'])
    assert result['source_immutable_verified']


def test_door_gap_is_not_closed(tmp_path):
    lines=rectangle(0,0,4,3)[:3]+[((0,3),(0,2)),((0,1),(0,0))]
    _,_,data=fixture(tmp_path,lines)
    result=m.extract(data)
    assert result['faces'] == []
    assert result['dangles'] and result['interpreted_length_m'] == 0


def linked_fixture(tmp_path):
    lines=rectangle(0,0,4,3)[:3]+[((0,3),(0,2)),((0,1),(0,0))]
    doc,es,data=fixture(tmp_path,lines)
    data['interpreted_links']=[{'id':'DOOR-BOUNDARY','from':{'path':[es[3].dxf.handle],'end':'end'},'to':{'path':[es[4].dxf.handle],'end':'start'},
         'purpose':'room floor measurement limit at opening','basis':'source door jamb ends interpreted in this view','surface_scope':['floor'], 'max_length_m':1.01}]
    return doc,es,data


def test_explicit_door_measurement_boundary_stays_separate(tmp_path):
    _,_,data=linked_fixture(tmp_path)
    result=m.extract(data)
    assert result['faces'][0]['area_m2'] == 12
    assert result['interpreted_length_m'] == 1
    assert any(s['origin']=='interpreted' for e in result['faces'][0]['boundary'] for s in e['sources'])


@pytest.mark.parametrize('mutation', ['duplicate','outside_endpoint','wrong_plane','excess_length','arbitrary_point'])
def test_bad_interpreted_links_rejected(tmp_path,mutation):
    doc,es,data=linked_fixture(tmp_path)
    link=data['interpreted_links'][0]
    if mutation=='duplicate':
        extra=copy.deepcopy(link);extra['id']='second';data['interpreted_links'].append(extra)
    elif mutation=='outside_endpoint':
        e=doc.modelspace().add_line((10,10),(11,10));doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
        link['from']={'path':[e.dxf.handle],'end':'start'};link['max_length_m']=20
    elif mutation=='wrong_plane':
        es[3].dxf.end=(0,2,1);doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
    elif mutation=='excess_length':link['max_length_m']=.5
    else:link['from']={'point':[0,2,0]}
    with pytest.raises((ValueError,KeyError)):
        m.extract(data)


def test_nested_rings_are_candidates_not_room_total(tmp_path):
    _,_,data=fixture(tmp_path,rectangle(0,0,10,10)+rectangle(3,3,2,2))
    result=m.extract(data)
    assert sorted(f['area_m2'] for f in result['faces']) == [4,96]
    assert sum(bool(f['holes_m']) for f in result['faces']) == 1
    assert 'total_area_m2' not in result
    assert all(f['status']=='candidate_face_requires_surface_interpretation' for f in result['faces'])


def test_adjacent_faces_and_spatial_label_do_not_assign_finish(tmp_path):
    doc,_,data=fixture(tmp_path,rectangle(0,0,8,3)+[((4,0),(4,3))])
    text=doc.modelspace().add_text('Unrelated generic name',dxfattribs={'insert':(1,1)})
    edge=doc.modelspace().add_text('Shared edge',dxfattribs={'insert':(4,1)})
    doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
    data['label_paths']=[[text.dxf.handle],[edge.dxf.handle]]
    result=m.extract(data)
    assert sorted(f['area_m2'] for f in result['faces']) == [12,12]
    assert len(result['label_candidates'][0]['faces']) == 1
    assert len(result['label_candidates'][1]['faces']) == 2
    assert all(x['status']=='spatial_candidate_only' for x in result['label_candidates'])


def test_cuts_dangles_and_duplicate_provenance(tmp_path):
    lines=rectangle(0,0,3,3)+rectangle(6,0,3,3)+[((3,1),(6,1)),((1,3),(1,4)),((0,0),(3,0))]
    _,_,data=fixture(tmp_path,lines)
    result=m.extract(data)
    assert result['cut_edges'] and result['dangles']
    assert result['coincident_length_removed_m'] == pytest.approx(3)
    assert any(len(e['sources'])==2 for f in result['faces'] for e in f['boundary'])


def test_self_crossing_source_diagnostic_retained_before_noding(tmp_path):
    _,_,data=fixture(tmp_path,[((0,0),(3,3)),((3,3),(0,3)),((0,3),(3,0)),((3,0),(0,0))])
    result=m.extract(data)
    assert result['pre_noding_invalid_rings']
    assert len(result['faces'])==2


def test_outside_scope_and_z_not_silently_projected(tmp_path):
    _,_,data=fixture(tmp_path,rectangle(0,0,4,3)+[((-2,1),(2,1)),((0,0,1),(4,0,1))])
    result=m.extract(data)
    assert len(result['excluded'])==1 and len(result['failures'])==1
    assert result['status']=='partial_selected_linework'
    assert result['faces'][0]['area_m2']==12


def test_curved_polyline_is_not_chord_filled(tmp_path):
    doc,_,data=fixture(tmp_path,rectangle(0,0,4,3))
    e=doc.modelspace().add_lwpolyline([(0,0,0),(4,0,1)],format='xyb')
    # A bulge belongs to its start vertex, ensure the sole segment is curved.
    e.set_points([(0,0,1),(4,0,0)],format='xyb')
    doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
    data['segments'].append({'path':[e.dxf.handle]})
    assert m.extract(data)['failures']


def test_nested_nonuniform_rotated_array_transform(tmp_path):
    doc=ezdxf.new();doc.units=6
    child=doc.blocks.new('different-generic-block-name')
    p=child.add_lwpolyline([(0,0),(4,0),(4,3),(0,3)],close=True)
    mid=doc.blocks.new('outer-generic-name')
    arr=mid.add_blockref(child.name,(2,3),dxfattribs={'rotation':30,'xscale':2,'yscale':.5,'row_count':2,'row_spacing':10,'column_count':2,'column_spacing':11})
    outer=doc.modelspace().add_blockref(mid.name,(100,200),dxfattribs={'rotation':-14})
    path=tmp_path/'array.dxf';doc.saveas(path)
    data={'schema_version':m.VERSION,'drawing':{'path':str(path),'sha256':m.digest(path)},'unit':'m','unit_evidence':'test',
          'scope':{'view_id':'array-view','interpretation':'one explicit cell','bounds_raw':[50,150,170,290],'plane_z_raw':0,'plane_tolerance_m':1e-8},
          'segments':[{'path':[outer.dxf.handle,{'handle':arr.dxf.handle,'row':1,'column':1},p.dxf.handle]}]}
    result=m.extract(data)
    assert result['faces'][0]['area_m2']==pytest.approx(12)
    data['segments'][0]['path'][1]=arr.dxf.handle
    with pytest.raises(ValueError,match='没有可分析'):
        m.extract(data)


def test_stale_source_and_unit_conflict_rejected(tmp_path):
    _,_,data=fixture(tmp_path,rectangle(0,0,4,3))
    bad=copy.deepcopy(data);bad['unit']='mm'
    with pytest.raises(ValueError,match='INSUNITS'):m.extract(bad)
    data['drawing']['sha256']='0'*64
    with pytest.raises(ValueError,match='已改变'):m.extract(data)


def test_precision_is_explicit_and_changes_not_hidden(tmp_path):
    lines=rectangle(0,0,4,3)[:3]+[((0,3),(0,.0001))]
    _,_,data=fixture(tmp_path,lines)
    assert not m.extract(data)['faces']
    data['precision_grid_m']=.001
    with pytest.raises(ValueError,match='依据'):m.extract(data)
    data['precision_evidence']='synthetic drafting endpoint deviation of 0.1 mm'
    result=m.extract(data)
    assert result['faces'][0]['area_m2']==12 and result['precision_changes']


def test_zero_length_polyline_part_does_not_swallow_later_valid_edges(tmp_path):
    doc,_,data=fixture(tmp_path,rectangle(10,10,1,1))
    entity=doc.modelspace().add_lwpolyline([(0,0),(2,0),(2,0),(2,2),(0,2)],close=True)
    doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
    data['segments']=[{'path':[entity.dxf.handle]}]
    result=m.extract(data)
    assert result['faces'][0]['area_m2']==4
    assert result['network_length_m']==8
    assert len(result['failures'])==1 and result['failures'][0]['reference']['part']==1


def test_repeated_first_part_does_not_swallow_unseen_later_parts(tmp_path):
    doc,_,data=fixture(tmp_path,rectangle(10,10,1,1))
    entity=doc.modelspace().add_lwpolyline([(0,0),(2,0),(2,2),(0,2)],close=True)
    doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
    data['segments']=[{'path':[entity.dxf.handle],'part':0},{'path':[entity.dxf.handle]}]
    result=m.extract(data)
    assert result['faces'][0]['area_m2']==4 and result['network_length_m']==8
    assert result['failures'][0]['reference']['part']==0


def test_unverified_dynamic_parent_preserved_and_scope_degraded(tmp_path):
    doc=ezdxf.new();doc.units=6
    block=doc.blocks.new('*U1')
    entity=block.add_lwpolyline([(0,0),(2,0),(2,2),(0,2)],close=True)
    ins=doc.modelspace().add_blockref(block.name,(0,0))
    path=tmp_path/'unverified.dxf';doc.saveas(path)
    data={'schema_version':m.VERSION,'drawing':{'path':str(path),'sha256':m.digest(path)},'unit':'m','unit_evidence':'test',
          'scope':{'view_id':'view','interpretation':'unknown parent state test','bounds_raw':[-1,-1,3,3],'plane_z_raw':0,'plane_tolerance_m':0},
          'segments':[{'path':[ins.dxf.handle,entity.dxf.handle]}]}
    result=m.extract(data)
    assert result['faces'][0]['area_m2']==4
    assert result['status']=='partial_selected_linework'
    assert result['source_context_issues'][0]['parent_path']==[ins.dxf.handle]
    assert 'anonymous_block_state_unverified' in result['source_context_issues'][0]['issue_codes']
    assert result['faces'][0]['boundary'][0]['sources'][0]['context_issues']


def test_coincident_but_unselected_source_endpoint_cannot_close_room(tmp_path):
    doc,_,data=linked_fixture(tmp_path)
    other=doc.modelspace().add_line((0,2),(1,2))
    doc.saveas(data['drawing']['path']);data['drawing']['sha256']=m.digest(data['drawing']['path'])
    data['interpreted_links'][0]['from']={'path':[other.dxf.handle],'end':'start'}
    with pytest.raises(ValueError,match='源路径'):
        m.extract(data)


def duplicate_unused_block_handles(doc, path):
    block=doc.blocks.new('unreferenced_source_definition')
    first=block.add_line((100,100),(101,100))
    second=block.add_line((100,101),(101,101))
    doc.saveas(path)
    text=path.read_text(encoding='utf8')
    changed,count=re.subn(rf'(?m)^(\s*5\n){second.dxf.handle}$',lambda match:match[1]+first.dxf.handle,text)
    assert count==1
    path.write_text(changed,encoding='utf8')
    return first.dxf.handle


def test_duplicate_source_identity_outside_selected_view_stays_visible(tmp_path):
    doc,_,data=fixture(tmp_path,rectangle(0,0,2,2))
    source=Path(data['drawing']['path'])
    bad_handle=duplicate_unused_block_handles(doc,source)
    data['drawing']['sha256']=m.digest(source)
    result=m.extract(data)
    assert result['faces'][0]['area_m2']==4
    assert result['source_immutable_verified']
    assert result['source_identity_status']=='conflict'
    assert result['status']=='partial_selected_linework'
    assert result['source_identity_issues'][0]['entity_handle']==bad_handle
    assert result['source_read_warnings']
    assert m.digest(source)==data['drawing']['sha256']


def test_hatch_entry_also_carries_duplicate_identity_and_candidate_group(tmp_path):
    spec=importlib.util.spec_from_file_location('surface_hatch_identity_review', ROOT/'skills/qto-surfaces-envelope/scripts/extract_surface_regions.py')
    hatchmod=importlib.util.module_from_spec(spec);spec.loader.exec_module(hatchmod)
    doc=ezdxf.new();doc.units=6
    h=doc.modelspace().add_hatch()
    h.paths.add_polyline_path([(0,0),(2,0),(2,2),(0,2)],is_closed=True)
    source=tmp_path/'hatch-duplicate.dxf'
    bad_handle=duplicate_unused_block_handles(doc,source)
    before=m.digest(source)
    result=hatchmod.extract(source,'.*','m','known synthetic units')
    assert result['status']=='partial_source_identity_conflict'
    assert result['source_identity_issues'][0]['entity_handle']==bad_handle
    assert result['regions'][0]['area_m2']==4
    assert result['regions'][0]['status']=='candidate_source_identity_conflict'
    group=hatchmod.measure_region_group(result['regions'])
    assert group['candidate_region_ids']==[result['regions'][0]['region_id']]
    assert m.digest(source)==before
