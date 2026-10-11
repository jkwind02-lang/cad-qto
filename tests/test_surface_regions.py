"""Boundary extraction failures found by independent drawing takeoff.

Synthetic mechanics only; project quantities remain in the real-project task.
"""
import importlib.util,math
from pathlib import Path
import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('surface_regions',ROOT/'skills/qto-surfaces-envelope/scripts/extract_surface_regions.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def rectangle(x,y,w,h):return [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
def hatch(paths,style=0):
    doc=ezdxf.new();h=doc.modelspace().add_hatch();h.dxf.hatch_style=style
    for points,flags in paths:h.paths.add_polyline_path(points,is_closed=True,flags=flags)
    return h

@pytest.mark.parametrize('style,expected',[(0,68),(1,64),(2,100)])
def test_hatch_islands_and_path_flags(style,expected):
    h=hatch([(rectangle(0,0,10,10),1),(rectangle(2,2,6,6),16),(rectangle(4,4,2,2),0)],style)
    assert m.hatch_geometry(h,1).area==pytest.approx(expected)

def test_overlapping_patterns_count_one_physical_area():
    h=hatch([(rectangle(0,0,10,5),1)]);g=m.hatch_geometry(h,1)
    rs=[dict(region_id=key,polygons=m.geometry_record(g),status='geometry_extracted') for key in ['NET','dots']]
    r=m.measure_region_group(rs)
    assert r['area_m2']==50 and r['sum_before_union_m2']==100 and r['overlap_removed_m2']==50
    with pytest.raises(m.RegionError):m.measure_region_group([rs[0],rs[0]])

def test_do_not_fill_open_boundary_without_explicit_candidate_flag():
    doc=ezdxf.new();h=doc.modelspace().add_hatch();p=h.paths.add_edge_path()
    p.add_line((0,0),(4,0));p.add_line((4,0),(4,3));p.add_line((4,3),(0,3))
    with pytest.raises(m.RegionError,match='open_boundary'):m.hatch_geometry(h,1)
    changes=[];g=m.hatch_geometry(h,1,allow_implicit=True,implicit_segments=changes,max_implicit_gap_m=3)
    assert g.area==12 and changes[0]['length_m']==3 and changes[0]['kind']=='closing_edge'

def test_bowtie_repair_is_explicit_and_traced():
    h=hatch([([(0,0),(2,2),(0,2),(2,0)],1)])
    with pytest.raises(m.RegionError):m.hatch_geometry(h,1)
    repairs=[];g=m.hatch_geometry(h,1,allow_repair=True,repairs=repairs)
    assert g.area==2 and repairs[0]['kind']=='make_valid' and repairs[0]['parts']==2

def test_crossing_paths_remain_candidate_even_when_graphics_are_measurable():
    h=hatch([(rectangle(0,0,3,3),1),(rectangle(2,2,3,3),16)])
    with pytest.raises(m.RegionError,match='crossing_boundary'):m.hatch_geometry(h,1)
    repairs=[];g=m.hatch_geometry(h,1,allow_repair=True,repairs=repairs)
    assert g.area==16 and repairs[0]['kind']=='crossing_path_parity'

def test_curves_are_not_replaced_by_chords():
    doc=ezdxf.new();h=doc.modelspace().add_hatch();p=h.paths.add_edge_path();p.add_arc((0,0),3,0,360)
    with pytest.raises(m.RegionError,match='curved_boundary'):m.hatch_geometry(h,1,allow_repair=True)

def test_nested_insert_transform_and_array_instances(tmp_path):
    d=ezdxf.new();d.header['$INSUNITS']=4;d.layers.new('finish')
    block=d.blocks.new('panel');h=block.add_hatch(dxfattribs={'layer':'finish'});h.paths.add_polyline_path(rectangle(0,0,1000,2000),is_closed=True)
    d.modelspace().add_blockref('panel',(10000,20000),dxfattribs={'xscale':2,'yscale':3,'rotation':90})
    insert=d.modelspace().add_blockref('panel',(0,0));insert.grid((2,3),(3000,4000))
    p=tmp_path/'array.dxf';d.saveas(p);before=m.sha256(p)
    out=m.extract(p,'^finish$','mm','known 1000mm block dimension')
    assert len(out['regions'])==7 and sum(r['area_m2'] for r in out['regions'])==pytest.approx(24)
    assert len({r['region_id'] for r in out['regions']})==7 and m.sha256(p)==before

def test_wrong_declared_unit_is_rejected(tmp_path):
    d=ezdxf.new();d.header['$INSUNITS']=6;p=tmp_path/'meters.dxf';d.saveas(p)
    with pytest.raises(m.RegionError,match='unit_conflicts'):m.extract(p,'.*','mm','wrong')

def test_candidate_status_and_failure_locations_survive_export(tmp_path):
    d=ezdxf.new();d.header['$INSUNITS']=4;h=d.modelspace().add_hatch();p=h.paths.add_edge_path()
    for a,b in [((0,0),(2000,0)),((2000,0),(2000,1000)),((2000,1000),(0,1000))]:p.add_line(a,b)
    file=tmp_path/'open.dxf';d.saveas(file)
    strict=m.extract(file,'.*','mm','dimensions')
    assert not strict['regions'] and strict['failures'][0]['locator_bounds_m']==[0,0,2,1]
    candidate=m.extract(file,'.*','mm','dimensions',allow_implicit=True,max_implicit_gap_m=1)
    assert candidate['regions'][0]['status']=='candidate_implicit_segments'
    assert candidate['regions'][0]['implicit_segments'][0]['length_m']==1


def test_disconnected_cycles_reassemble_without_diagonal_bridges():
    doc=ezdxf.new();h=doc.modelspace().add_hatch();p=h.paths.add_edge_path()
    # Two independent 4x1 rectangles in one DXF EdgePath, with reversed/random edges.
    for a,b in [((4,1),(0,1)),((0,0),(4,0)),((4,0),(4,1)),((0,1),(0.0002,0)),
                ((9,0),(5,0)),((5,1),(9,1)),((5,0),(5,1)),((9,1),(9,0))]:p.add_line(a,b)
    with pytest.raises(m.RegionError,match='implicit_gap_exceeds'):m.hatch_geometry(h,1,allow_implicit=True,allow_repair=True)
    repairs=[];g=m.hatch_geometry(h,1,allow_repair=True,stitch_tolerance_m=.001,repairs=repairs)
    assert g.area==pytest.approx(8) and g.geom_type=='MultiPolygon'
    assert repairs[0]['kind']=='edge_cycles_reassembled' and repairs[0]['cycles']==2
    assert max(j['distance_m'] for j in repairs[0]['endpoint_joints'])==pytest.approx(.0002)


@pytest.mark.parametrize('segments',[
    [((0,0),(4,0)),((4,0),(4,1)),((4,1),(0,1))], # missing whole edge
    [((0,0),(4,0)),((4,0),(4,1)),((4,1),(0,1)),((0,1),(0,.1))], # gap exceeds tolerance
    [((0,0),(4,0)),((4,0),(4,1)),((4,1),(0,1)),((0,1),(0,0)),((0,0),(4,1))], # branch
])
def test_stitch_never_invents_missing_edges_or_resolves_branches(segments):
    with pytest.raises(m.RegionError,match='edge_graph_not_closed'):m.reassemble_edge_rings(segments,.001,[])


def test_excessive_implicit_gap_rejected_by_default():
    doc=ezdxf.new();h=doc.modelspace().add_hatch();p=h.paths.add_edge_path()
    for a,b in [((0,0),(2,0)),((2,0),(2,1)),((2,1),(0,1))]:p.add_line(a,b)
    with pytest.raises(m.RegionError,match='implicit_gap_exceeds'):m.hatch_geometry(h,1,allow_implicit=True)
