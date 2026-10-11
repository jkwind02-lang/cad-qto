"""Detail routes must retain source curves and separate symbol gaps from pipe bills."""
import copy
import hashlib
import importlib.util
import math
from pathlib import Path
import pytest
import ezdxf

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('detail_routes', ROOT/'skills/qto-pipe-networks/scripts/measure_pipe_detail_routes.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture(tmp_path):
    doc = ezdxf.new(); doc.units = 6
    model = doc.modelspace()
    upper = model.add_line((0,5),(0,10))
    lower = model.add_line((0,1),(0,3))
    block = doc.blocks.new('elbow'); curve = block.add_arc((1,1),1,180,270)
    elbow = model.add_blockref('elbow',(0,0))
    bottom = model.add_line((1,0),(8,0))
    valve_block = doc.blocks.new('valve'); valve_block.add_circle((0,0),.2)
    valve = model.add_blockref('valve',(0,4))
    dim = model.add_linear_dim(base=(-1,0),p1=(0,0),p2=(0,10),angle=90)
    dim.render()
    f=tmp_path/'source.dxf'; doc.saveas(f)
    def ref(e, **kwargs):return dict(path=[e.dxf.handle],**kwargs)
    segments=[dict(kind='drawn',source=ref(upper),reverse=True),
              dict(kind='restored_axis',**{'from':ref(upper,fraction=0),'to':ref(lower,fraction=1)},component_paths=[[valve.dxf.handle]],reason='inline valve'),
              dict(kind='drawn',source=ref(lower),reverse=True),
              dict(kind='drawn',source=dict(path=[elbow.dxf.handle,curve.dxf.handle])),
              dict(kind='drawn',source=ref(bottom))]
    data=dict(schema_version=m.VERSION,drawing=dict(path=str(f),sha256=hashlib.sha256(f.read_bytes()).hexdigest()),raw_unit='m',join_tolerance_raw=.00001,
              views=[dict(view_id='front',role='primary',bounds3d=[-3,-3,-1,15,15,1])],
              routes=[dict(physical_group_id='A',physical_route_id='supply',view_id='front',review_note='source reviewed',dimension_paths=[[dim.dimension.dxf.handle]],segments=segments)])
    return data,doc


def changed(data,doc):
    doc.saveas(data['drawing']['path'])
    data['drawing']['sha256']=hashlib.sha256(Path(data['drawing']['path']).read_bytes()).hexdigest()


def test_source_arc_and_restored_span_are_separate(tmp_path):
    data,_=fixture(tmp_path); r=m.measure(data)['routes'][0]
    assert r['drawn_axis_m']==pytest.approx(14+math.pi/2)
    assert r['restored_axis_m']==2
    assert r['axis_including_components_m']==pytest.approx(16+math.pi/2)
    assert r['billable_pipe_length_m'] is None and r['installed_group_count'] is None


@pytest.mark.parametrize('mutation,match',[
 (lambda d:d.update(routes=[]),'没有路径'),
 (lambda d:d.update(views=[]),'没有可定位'),
 (lambda d:d['routes'][0].update(physical_group_id=''),'编号不能为空'),
 (lambda d:d.update(raw_unit='mm'),'单位'),
 (lambda d:d.update(join_tolerance_raw=1),'容差'),
 (lambda d:d['views'][0].update(role='secondary'),'辅助视图'),
 (lambda d:d['views'][0].update(bounds3d=[1,1,-1,5,5,1]),'范围'),
 (lambda d:d['routes'].append(copy.deepcopy(d['routes'][0])),'同一物理管路'),
 (lambda d:d['routes'][0].update(dimension_paths=[]),'尺寸标定'),
 (lambda d:d['routes'][0]['segments'][0].update(reverse=False),'不连续'),
 (lambda d:d['routes'][0]['segments'][1].update(component_paths=[]),'占位构件'),
 (lambda d:d['routes'][0]['segments'][1]['from'].update(fraction=.5),'端点'),
 (lambda d:d['routes'][0]['segments'][3]['source'].update(path=[d['routes'][0]['segments'][3]['source']['path'][-1]]),'父块'),
 (lambda d:d['routes'][0]['segments'].append(copy.deepcopy(d['routes'][0]['segments'][-1])),'重复或重叠'),
])
def test_invalid_inputs_do_not_produce_quantities(tmp_path,mutation,match):
    data,_=fixture(tmp_path);mutation(data)
    with pytest.raises(ValueError,match=match):m.measure(data)


def test_stale_source_is_rejected(tmp_path):
    data,_=fixture(tmp_path);Path(data['drawing']['path']).write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='已改变'):m.measure(data)


def test_wrong_component_cannot_close_gap(tmp_path):
    data,doc=fixture(tmp_path)
    path=data['routes'][0]['segments'][1]['component_paths'][0]
    doc.entitydb[path[0]].dxf.insert=(2,4,0);changed(data,doc)
    with pytest.raises(ValueError,match='插入点'):m.measure(data)


def test_offset_endpoints_cannot_be_bridged(tmp_path):
    data,doc=fixture(tmp_path)
    path=data['routes'][0]['segments'][2]['source']['path']
    doc.entitydb[path[0]].dxf.start=(1,1,0);doc.entitydb[path[0]].dxf.end=(1,3,0);changed(data,doc)
    with pytest.raises(ValueError,match='不共线'):m.measure(data)


def test_display_override_cannot_calibrate(tmp_path):
    data,doc=fixture(tmp_path)
    doc.entitydb[data['routes'][0]['dimension_paths'][0][0]].dxf.text='20';changed(data,doc)
    with pytest.raises(ValueError,match='覆盖'):m.measure(data)


def test_dimension_scale_applies_exactly_once(tmp_path):
    data,doc=fixture(tmp_path)
    dim=doc.entitydb[data['routes'][0]['dimension_paths'][0][0]]
    override=dim.override();override['dimlfac']=.2;override.commit()
    changed(data,doc)
    r=m.measure(data)['routes'][0]
    assert r['axis_including_components_m']==pytest.approx((16+math.pi/2)*.2)


def test_mirrored_nonuniform_nested_arc_uses_affine_geometry(tmp_path):
    doc=ezdxf.new(); b=doc.blocks.new('B'); a=b.add_arc((0,0),1,0,90)
    parent=doc.blocks.new('P'); child=parent.add_blockref('B',(2,3),dxfattribs={'xscale':-2,'yscale':3,'rotation':30})
    root=doc.modelspace().add_blockref('P',(100,20),dxfattribs={'rotation':60})
    p=m.Drawing(doc).part({'path':[root.dxf.handle,child.dxf.handle,a.dxf.handle]})
    # Quarter ellipse computed by independent dense chord integration.
    expected=sum(math.hypot(2*(math.cos((i+1)*math.pi/20000)-math.cos(i*math.pi/20000)),3*(math.sin((i+1)*math.pi/20000)-math.sin(i*math.pi/20000))) for i in range(10000))
    value=m.summarize({'schema':m.GEOMETRY_SCHEMA,'coordinate_system':'WCS','parts':[p]})['length']
    assert value==pytest.approx(expected,rel=1e-8)
    assert value != pytest.approx(math.dist(m.part_point(p,0),m.part_point(p,1)))


def test_overlapping_partial_line_is_rejected(tmp_path):
    data,doc=fixture(tmp_path)
    e=doc.modelspace().add_line((3,0),(8,0));changed(data,doc)
    data['routes'][0]['segments'].append(dict(kind='drawn',source=dict(path=[e.dxf.handle]),reverse=True))
    with pytest.raises(ValueError,match='重叠'):m.measure(data)


@pytest.mark.parametrize('bad_cap',[False,True])
def test_source_end_cap_midpoint_extends_axis(tmp_path,bad_cap):
    data,doc=fixture(tmp_path)
    cap=doc.modelspace().add_line((-1,12),(1,13 if bad_cap else 12))
    changed(data,doc)
    axis=dict(data['routes'][0]['segments'][0]['source'],fraction=1)
    s=dict(kind='port_extension',**{'from':dict(path=[cap.dxf.handle],fraction=.5),'to':axis},reason='end cap')
    data['routes'][0]['segments'].insert(0,s)
    if bad_cap:
        with pytest.raises(ValueError,match='不垂直'):m.measure(data)
    else:
        r=m.measure(data)['routes'][0]
        assert r['restored_port_axis_m']==2
        assert r['axis_including_components_m']==pytest.approx(18+math.pi/2)


@pytest.mark.parametrize('mode',['valid','outside','skew','unrelated_axis'])
def test_source_tee_intersection_constraints(tmp_path,mode):
    data,doc=fixture(tmp_path)
    horizontal=doc.modelspace().add_line((2,6),(5,6))
    vertical=doc.modelspace().add_line((0,5,1 if mode=='skew' else 0),(0,10,1 if mode=='skew' else 0))
    boundary=doc.modelspace().add_lwpolyline([(1 if mode=='outside' else -1,5),(3,5),(3,7),(1 if mode=='outside' else -1,7)],close=True)
    other=doc.modelspace().add_line((2,6),(4,6))
    changed(data,doc)
    h=dict(path=[horizontal.dxf.handle]);v=dict(path=[vertical.dxf.handle])
    point=dict(intersection=[h,v],enclosure_path=[boundary.dxf.handle])
    route=dict(data['routes'][0],physical_route_id='bypass',segments=[dict(kind='junction_axis',**{'from':point,'to':dict(path=[other.dxf.handle] if mode=='unrelated_axis' else h['path'],fraction=0)},reason='tee outline'),dict(kind='drawn',source=h)])
    data['routes'].append(route)
    if mode=='valid':
        r=m.measure(data)['routes'][1]
        assert r['restored_junction_axis_m']==2
        assert r['axis_including_components_m']==5
    else:
        with pytest.raises(ValueError,match={'outside':'范围之外','skew':'异面','unrelated_axis':'参与求解'}[mode]):m.measure(data)
