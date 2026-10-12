"""Window performance guard without dropping unproven drawing content."""
import hashlib
import random
from pathlib import Path
import sys
from types import SimpleNamespace

import ezdxf
import numpy as np
from PIL import Image
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SCRIPTS))
import render_cad_view as view
from qto_render_window import known_display_envelope,proven_far_outside

PROPERTIES=SimpleNamespace(lineweight=.25)
B=[0,0,100,100]

def test_far_line_is_culled_but_crossing_line_and_view_margin_stay():
    m=ezdxf.new().modelspace()
    assert proven_far_outside(m.add_line((1000,1000),(1100,1000)),B,PROPERTIES)
    assert not proven_far_outside(m.add_line((-1000,50),(1000,50)),B,PROPERTIES)
    assert not proven_far_outside(m.add_line((199,20),(199,40)),B,PROPERTIES)

@pytest.mark.parametrize('weight',[None,True,float('inf'),-1.0,3.0])
def test_unusual_display_weight_stays(weight):
    e=ezdxf.new().modelspace().add_line((1000,1000),(1100,1000))
    assert not proven_far_outside(e,B,SimpleNamespace(lineweight=weight))

def test_tilted_circle_and_arc_envelopes_cover_wcs_geometry():
    m=ezdxf.new().modelspace()
    e=m.add_circle((10,20,0),50,dxfattribs={'extrusion':(1,0,1)})
    box=known_display_envelope(e)
    for p in e.vertices([i for i in range(0,360,5)]):
        assert box[0]<=p.x<=box[2] and box[1]<=p.y<=box[3]
    arc=m.add_arc((50,50),60,0,5)
    assert not proven_far_outside(arc,B,PROPERTIES)

def test_transformed_child_not_block_insert_anchor_controls_visibility():
    d=ezdxf.new();block=d.blocks.new('wide-block')
    block.add_line((-1000,50),(-990,50))
    ins=d.modelspace().add_blockref('wide-block',(1000,0))
    assert known_display_envelope(ins) is None
    child=next(ins.virtual_entities())
    assert not proven_far_outside(child,B,PROPERTIES)

def test_polyline_widths_stay_and_curves_use_full_native_envelope():
    m=ezdxf.new().modelspace()
    bulge=m.add_lwpolyline([(1000,1000,1),(1100,1000,0)],format='xyb')
    wide=m.add_lwpolyline([(1000,1000),(1100,1000)],dxfattribs={'const_width':1000})
    tapered=m.add_lwpolyline([(1000,1000,200,300),(1100,1000,0,0)],format='xyse')
    assert known_display_envelope(bulge) is not None
    for e in (wide,tapered):assert known_display_envelope(e) is None
    reaches_window=m.add_lwpolyline([(1000,-1000,-1),(1000,1000,0)],format='xyb')
    assert known_display_envelope(reaches_window)[0] == pytest.approx(0)
    assert not proven_far_outside(reaches_window,B,PROPERTIES)

def test_hatch_envelope_includes_all_paths_and_ocs():
    m=ezdxf.new().modelspace();e=m.add_hatch()
    e.paths.add_polyline_path([(1000,1000),(1100,1000),(1100,1100),(1000,1100)],is_closed=True)
    assert proven_far_outside(e,B,PROPERTIES)
    e.paths.add_polyline_path([(50,50),(60,50),(60,60),(50,60)],is_closed=True)
    assert not proven_far_outside(e,B,PROPERTIES)

def test_hatch_edge_curve_and_open_path_are_retained():
    m=ezdxf.new().modelspace();a=m.add_hatch();a.paths.add_edge_path().add_arc((1000,1000),100,0,360)
    b=m.add_hatch();b.paths.add_polyline_path([(1000,1000),(1100,1000),(1000,1100)],is_closed=False)
    assert known_display_envelope(a) is None and known_display_envelope(b) is None

def test_text_spline_and_infinite_line_are_not_culled_by_anchor():
    m=ezdxf.new().modelspace()
    rows=[m.add_text('long rotated text',dxfattribs={'insert':(1000,1000),'height':2000}),
          m.add_spline([(1000,1000),(500,-500),(-500,1000)]),m.add_xline((1000,1000),(1,0))]
    for e in rows:assert known_display_envelope(e) is None

def test_no_explicit_window_keeps_old_full_render():
    e=ezdxf.new().modelspace().add_line((1000,1000),(1100,1000))
    assert not proven_far_outside(e,None,PROPERTIES)

def test_thick_primitive_with_unproven_extruded_display_is_retained():
    e=ezdxf.new().modelspace().add_line((1000,1000),(1100,1000),dxfattribs={'thickness':1000,'extrusion':(-1,0,1)})
    assert known_display_envelope(e) is None

def test_point_anchor_alone_is_unknown_and_large_marker_must_stay():
    e=ezdxf.new().modelspace().add_point((1000,50))
    assert known_display_envelope(e) is None
    assert proven_far_outside(e,B,PROPERTIES,1)
    assert not proven_far_outside(e,B,PROPERTIES,1000)

@pytest.mark.parametrize('size',[None,-1,float('nan'),True])
def test_unknown_point_marker_size_is_not_culled(size):
    e=ezdxf.new().modelspace().add_point((1000,1000))
    assert not proven_far_outside(e,B,PROPERTIES,size)

def test_invalid_point_angle_is_retained_instead_of_masking_display_error():
    e=ezdxf.new().modelspace().add_point((1000,1000));e.dxf.angle=float('nan')
    assert not proven_far_outside(e,B,PROPERTIES,1)

def test_actual_native_point_markers_match_full_then_crop(tmp_path,monkeypatch):
    d=ezdxf.new();d.header['$PDMODE']=34;d.header['$PDSIZE']=600
    d.modelspace().add_point((350,50),dxfattribs={'color':1})
    d.modelspace().add_point((10000,10000),dxfattribs={'color':5})
    path=tmp_path/'points.dxf';d.saveas(path);src={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    query={'question':'synthetic point marker extent parity','bounds':B,'background':'white'}
    def render(name):
        r=view.render_view(src,query,svg_path=tmp_path/f'{name}.svg',png_path=tmp_path/f'{name}.png',output_path=tmp_path/f'{name}.json',pixels=600)
        return r,np.asarray(Image.open(tmp_path/f'{name}.png').convert('RGB'))
    filtered,p=render('filtered')
    assert filtered['window_render_filter']['culled_by_type']=={'POINT':1}
    assert filtered['window_render_filter']['resolved_point_marker_size_raw']==600
    monkeypatch.setattr(view,'proven_far_outside',lambda *args:False)
    _,q=render('full')
    assert np.array_equal(p,q) and (p[:,:,0]>p[:,:,1]).any()

def test_window_pixels_equal_full_drawing_then_crop(tmp_path,monkeypatch):
    d=ezdxf.new();m=d.modelspace()
    m.add_line((-1000,20),(1000,20),dxfattribs={'color':1})
    m.add_circle((60,60),30,dxfattribs={'color':5})
    m.add_arc((1000,1000),100,30,100)
    m.add_text('BOUND',dxfattribs={'insert':(10,90),'height':10})
    block=d.blocks.new('long-block');block.add_line((-1000,50),(-900,50),dxfattribs={'color':3});m.add_blockref('long-block',(1000,0))
    h=m.add_hatch();h.paths.add_polyline_path([(1000,1000),(1100,1000),(1100,1100),(1000,1100)],is_closed=True)
    m.add_lwpolyline([(1000,0,3),(1000,200,0)],format='xyb')
    path=tmp_path/'source.dxf';d.saveas(path);sha=hashlib.sha256(path.read_bytes()).hexdigest();src={'path':str(path),'sha256':sha}
    query={'question':'synthetic clipping parity','bounds':B,'background':'white'}
    def render(name):
        r=view.render_view(src,query,svg_path=tmp_path/f'{name}.svg',png_path=tmp_path/f'{name}.png',output_path=tmp_path/f'{name}.json',pixels=1200)
        return r,np.asarray(Image.open(tmp_path/f'{name}.png').convert('RGB'))
    filtered,p=render('filtered')
    assert filtered['window_render_filter']['culled_count']>=2
    monkeypatch.setattr(view,'proven_far_outside',lambda *args:False)
    unfiltered,q=render('unfiltered')
    assert unfiltered['window_render_filter']['culled_count']==0 and np.array_equal(p,q)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha

def test_patterned_hatch_is_repeatable_and_unchanged_by_far_hatch_culling(tmp_path,monkeypatch):
    d=ezdxf.new();m=d.modelspace()
    for points in [[(1000,1000),(1100,1000),(1100,1100),(1000,1100)],[(0,0),(100,0),(100,100),(0,100)]]:
        h=m.add_hatch();h.set_pattern_definition([[30,(0,0),(-1,2),[1,-1]]])
        h.dxf.solid_fill=0;h.dxf.pattern_name='SYNTHETIC';h.paths.add_polyline_path(points,is_closed=True)
    path=tmp_path/'pattern.dxf';d.saveas(path);sha=hashlib.sha256(path.read_bytes()).hexdigest();src={'path':str(path),'sha256':sha}
    query={'question':'synthetic patterned hatch repeatability','bounds':B,'background':'white'}
    def render(name):
        r=view.render_view(src,query,svg_path=tmp_path/f'{name}.svg',png_path=tmp_path/f'{name}.png',output_path=tmp_path/f'{name}.json',pixels=1200)
        return r,np.asarray(Image.open(tmp_path/f'{name}.png').convert('RGB'))
    state=random.getstate();random.seed(13);before=random.getstate()
    filtered,p=render('filtered')
    assert random.getstate()==before
    random.seed(987);_,q=render('repeat')
    assert np.array_equal(p,q)
    monkeypatch.setattr(view,'proven_far_outside',lambda *args:False)
    _,full=render('full')
    assert np.array_equal(p,full)
    assert filtered['window_render_filter']['culled_by_type']=={'HATCH':1}
    assert 'source_bound' in filtered['renderer']['hatch_pattern_origin_jitter']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
    random.setstate(state)
