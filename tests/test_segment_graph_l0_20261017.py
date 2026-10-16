"""第五批 L0：公共线段图（qto_segment_graph），L1～L4 共用，只做几何。

边界清单（备课第二节 L0）：并点容差为 0、带小偏移；T 接；弧段长度；嵌套块里的线；单位 mm 和 m；自交多段线；闭合多段线；
范围边上的段。另加：交叉不连、缺口只在共线时桥接且一端两个候选不桥接、图层过滤、LEADER 顶点入图、MLEADER 计数不丢、
零长度段剔除。合成图（去标识），走真实中性提取器，期望值按图面坐标手算。
"""
import json
import math
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_segment_graph as sg  # noqa: E402

H = {}


@pytest.fixture(scope='module')
def package(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('l0')
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in ('E-LINE', 'E-OTHER'):
        doc.layers.add(name)
    sub = doc.blocks.new('SUB'); sub.add_line((0, 0), (0, 100), dxfattribs={'layer': '0'})
    floor = doc.blocks.new('FLOOR')
    floor.add_line((0, 0), (100, 0), dxfattribs={'layer': '0'})
    floor.add_blockref('SUB', (0, 0), dxfattribs={'layer': '0'})
    msp = doc.modelspace()
    e = {'layer': 'E-LINE'}
    add = lambda key, a, b: H.__setitem__(key, msp.add_line(a, b, dxfattribs=e).dxf.handle)
    add('L1', (0, 0), (1000, 0)); add('L2', (1000, 0), (1000, 1000))                 # exact join
    add('L3', (2000, 0), (3000, 0)); add('L4', (3000.4, 0), (4000, 0))              # 0.4 apart
    add('L5', (5000, 0), (6000, 0)); add('L6', (5500, 0), (5500, 500))              # T on L5's interior
    add('L7', (7000, -500), (7000, 500)); add('L8', (6500, 0), (7500, 0))           # crossing
    H['ARC'] = msp.add_arc((10000, 0), 1000, 0, 90, dxfattribs=e).dxf.handle      # quarter circle
    H['SQ'] = msp.add_lwpolyline([(12000, 0), (13000, 0), (13000, 1000), (12000, 1000)], close=True, dxfattribs=e).dxf.handle
    H['BOW'] = msp.add_lwpolyline([(15000, 0), (16000, 1000), (16000, 0), (15000, 1000)], dxfattribs=e).dxf.handle  # self-crossing
    H['FLOOR'] = msp.add_blockref('FLOOR', (20000, 0), dxfattribs=e).dxf.handle
    add('L9', (25000, 0), (26000, 0)); add('L10', (26050, 0), (27000, 0))          # collinear gap 50
    add('L11', (28000, 3000), (29000, 3000)); add('L12', (29050, 3000), (29050, 4000))  # perpendicular gap
    add('EDGE', (29500, 2000), (30500, 2000))                                        # crosses the scope edge
    add('OUT', (31000, 2000), (32000, 2000))                                         # outside the scope
    H['DUP'] = msp.add_lwpolyline([(0, 5000), (500, 5000), (500, 5000), (1000, 5000)], dxfattribs=e).dxf.handle  # repeated vertex
    H['OTHER'] = msp.add_line((0, 8000), (1000, 8000), dxfattribs={'layer': 'E-OTHER'}).dxf.handle
    H['LEADER'] = msp.add_leader([(40000, 0), (40500, 500), (41000, 500)], dxfattribs=e).dxf.handle
    dxf = tmp / 'l0.dxf'; doc.saveas(dxf)
    neutral = tmp / 'neutral'
    done = subprocess.run([sys.executable, '-B', str(SHARED / 'extract_cad_neutral.py'), '--source-dxf', str(dxf), '--output-dir', str(neutral)],
                          capture_output=True, text=True, encoding='utf-8', stdin=subprocess.DEVNULL, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    read = lambda name: [json.loads(x) for x in (neutral / name).read_text(encoding='utf-8').splitlines() if x.strip()]
    layout = [r for r in read('中性布局图元.ndjson') if r.get('layout') == 'Model']
    definitions = {}
    for r in read('中性块定义图元.ndjson'):
        definitions.setdefault(r['parent_block'], []).append(r)
    manifest = json.loads((neutral / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    origins = {b['block_name']: b.get('origin') or [0, 0, 0] for b in manifest['blocks']}
    return {'layout': layout, 'definitions': definitions, 'origins': origins}


def graph(pkg, snap=0.0, gap=0.0, scope=None, expand=('FLOOR', 'SUB'), layer='^E-LINE$', rows=None):
    got = sg.collect_segments(rows if rows is not None else pkg['layout'], pkg['definitions'], pkg['origins'],
                              layer_pattern=layer, expand_blocks=expand, scope=scope)
    return got, sg.build_graph(got['segments'], snap_tolerance=snap, gap_tolerance=gap)


def segs_of(g, key):
    return [s for s in g['segments'] if s['source_id'] == f'CAD-{H[key].upper()}' or s['source_id'] == f'CAD-{H[key]}']


def comp(g, key):
    return {s['component_id'] for s in segs_of(g, key)}


def test_ends_join_only_when_they_snap(package):
    _, g = graph(package)
    assert comp(g, 'L1') == comp(g, 'L2')                     # exact shared end
    assert comp(g, 'L3') != comp(g, 'L4')                     # 0.4 apart, tolerance 0
    _, g = graph(package, snap=0.5)
    assert comp(g, 'L3') == comp(g, 'L4')
    _, g = graph(package, snap=0.3)
    assert comp(g, 'L3') != comp(g, 'L4')


def test_t_contact_is_a_candidate_and_a_crossing_never_joins(package):
    _, g = graph(package)
    assert comp(g, 'L5') != comp(g, 'L6')
    t = [c for c in g['t_contacts'] if c['segment_id'] == segs_of(g, 'L6')[0]['segment_id']]
    assert len(t) == 1 and t[0]['on_segment_id'] == segs_of(g, 'L5')[0]['segment_id'] and math.isclose(t[0]['fraction'], 0.5)
    assert comp(g, 'L7') != comp(g, 'L8')
    assert not [c for c in g['t_contacts'] if c['segment_id'] in {segs_of(g, k)[0]['segment_id'] for k in ('L7', 'L8')}]
    # a wider T reach than the snap: an end near another segment's END is a near end, not a T on its interior
    wide = sg.build_graph(graph(package)[0]['segments'], snap_tolerance=0.0, t_tolerance=0.5)
    near = {s['segment_id'] for s in wide['segments'] if s['source_id'] in (f"CAD-{H['L3']}", f"CAD-{H['L4']}", f"CAD-{H['L3'].upper()}", f"CAD-{H['L4'].upper()}")}
    assert len(near) == 2 and not [c for c in wide['t_contacts'] if c['segment_id'] in near]


def test_arc_length_and_polylines(package):
    _, g = graph(package)
    arc = segs_of(g, 'ARC')
    assert len(arc) == 1 and math.isclose(arc[0]['length'], math.pi / 2 * 1000, rel_tol=1e-9)
    square = segs_of(g, 'SQ')                                 # closed: 4 sides, one loop, no free end
    assert len(square) == 4 and len(comp(g, 'SQ')) == 1
    c = next(x for x in g['components'] if x['component_id'] in comp(g, 'SQ'))
    assert c['dangling_node_ids'] == [] and math.isclose(c['length'], 4000)
    bow = segs_of(g, 'BOW')                                   # self-crossing: a chain, the crossing is no node
    c = next(x for x in g['components'] if x['component_id'] in comp(g, 'BOW'))
    assert len(bow) == 3 and len(c['node_ids']) == 4 and len(c['dangling_node_ids']) == 2


def test_nested_block_lines_take_the_d2_path_and_the_insert_layer(package):
    got, g = graph(package)
    floor = [s for s in g['segments'] if s['source_id'].startswith(f"CAD-{H['FLOOR'].upper()}/") or s['source_id'].startswith(f"CAD-{H['FLOOR']}/")]
    assert len(floor) == 2 and {s['layer'] for s in floor} == {'E-LINE'}           # layer 0 inside -> the insert's layer
    paths = sorted(s['source_id'].split('/', 1)[1] for s in floor)
    assert paths[0].startswith('FLOOR/CAD-') and paths[1].count('/') == 3 and '/SUB/CAD-' in paths[1]
    ends = sorted(tuple(round(v, 6) for v in (*s['start'][:2], *s['end'][:2])) for s in floor)
    assert ends == [(20000, 0, 20000, 100), (20000, 0, 20100, 0)]
    assert len({s['component_id'] for s in floor}) == 1
    got, _ = graph(package, expand=())                       # not declared: not walked
    assert not [s for s in got['segments'] if '/' in s['source_id']]
    got, _ = graph(package, expand=('FLOOR',))               # the outer block only: its own line, not SUB's
    assert [s['source_id'].count('/') for s in got['segments'] if '/' in s['source_id']] == [2]


def test_gap_bridged_only_when_both_ends_point_at_each_other(package):
    _, g = graph(package, gap=100)
    assert comp(g, 'L9') == comp(g, 'L10')
    bridge = [b for b in g['gap_bridged'] if {b['from_segment_id'], b['to_segment_id']} == {segs_of(g, 'L9')[0]['segment_id'], segs_of(g, 'L10')[0]['segment_id']}]
    assert len(bridge) == 1 and math.isclose(bridge[0]['length'], 50)
    assert comp(g, 'L11') != comp(g, 'L12')                   # perpendicular: not bridged
    _, g = graph(package)                                     # gap 0: nothing bridged
    assert g['gap_bridged'] == [] and comp(g, 'L9') != comp(g, 'L10')


def test_an_end_with_two_gap_partners_bridges_nothing():
    line =lambda h, a, b: {'entity_handle': h, 'entity_type': 'LINE', 'layer': 'E', 'start': a, 'end': b}
    rows = [line('A', [0, 0, 0], [100, 0, 0]), line('B', [110, 0.5, 0], [200, 0.5, 0]), line('C', [110, -0.5, 0], [200, -0.5, 0])]
    for order in (rows, rows[::-1]):                          # the ambiguous end first and last
        g = sg.build_graph(sg.collect_segments(order)['segments'], gap_tolerance=20)
        assert g['gap_bridged'] == [] and [a['segment_id'] for a in g['ambiguous_gaps']] == ['SEG-CAD-A:0']


def test_a_chain_of_near_ends_never_joins_ends_beyond_the_tolerance():
    line = lambda h, a, b: {'entity_handle': h, 'entity_type': 'LINE', 'layer': 'E', 'start': a, 'end': b}
    rows = [line('A', [-100, 0, 0], [0, 0, 0]), line('B', [0.4, 0, 0], [0.4, 100, 0]), line('C', [0.8, 0, 0], [100, 0, 0])]
    g = sg.build_graph(sg.collect_segments(rows)['segments'], snap_tolerance=0.5)
    node = {s['segment_id']: (s['start_node'], s['end_node']) for s in g['segments']}
    assert node['SEG-CAD-A:0'][1] != node['SEG-CAD-C:0'][0]          # 0.8 apart: never one node
    assert len(g['refused_snaps']) == 1 and g['counts']['components'] == 2


def test_rows_without_geometry_and_missing_blocks_are_reported_not_dropped():
    rows = [{'entity_handle': 'P', 'entity_type': 'LWPOLYLINE', 'layer': 'E'},
            {'entity_handle': 'I', 'entity_type': 'INSERT', 'layer': 'E', 'block_name': 'GONE', 'insert': [0, 0, 0]},
            {'entity_handle': 'L', 'entity_type': 'LEADER', 'layer': 'E', 'raw_geometry': {'vertices': [[0, 0, 0]]}}]
    got = sg.collect_segments(rows, expand_blocks=('GONE',))
    assert [p['source_id'] for p in got['geometry_problems']] == ['CAD-P', 'CAD-L']
    assert [p['source_id'] for p in got['expansion_problems']] == ['CAD-I'] and got['segments'] == []
    with pytest.raises(ValueError):
        sg.build_graph([], snap_tolerance=-1)


def test_scope_edge_and_units(package):
    scope = {'xmin': 0, 'xmax_exclusive': 30000, 'ymin': -1000, 'ymax_exclusive': 10000}
    got, g = graph(package, scope=scope)
    edge = segs_of(g, 'EDGE')
    assert len(edge) == 1 and edge[0]['crosses_scope_edge'] is True and not segs_of(g, 'OUT')
    assert segs_of(g, 'L1')[0]['crosses_scope_edge'] is False and got['out_of_scope_parts'] >= 2   # OUT and the leader
    # the same pair in metres with the tolerance converted: same topology
    line = lambda h, a, b: {'entity_handle': h, 'entity_type': 'LINE', 'layer': 'E', 'start': a, 'end': b}
    metres = sg.collect_segments([line('A', [2.0, 0, 0], [3.0, 0, 0]), line('B', [3.0004, 0, 0], [4.0, 0, 0])])
    assert sg.build_graph(metres['segments'], snap_tolerance=0.0005)['counts']['components'] == 1
    assert sg.build_graph(metres['segments'], snap_tolerance=0.0003)['counts']['components'] == 2


def test_layer_filter_leaders_and_degenerate_parts(package):
    got, g = graph(package)
    assert not segs_of(g, 'OTHER')
    got_all, _ = graph(package, layer=None)
    assert any(s['source_id'] in (f"CAD-{H['OTHER']}", f"CAD-{H['OTHER'].upper()}") for s in got_all['segments'])
    leader = segs_of(g, 'LEADER')
    assert len(leader) == 2 and leader[0]['entity_type'] == 'LEADER' and len(comp(g, 'LEADER')) == 1
    dup = segs_of(g, 'DUP')
    assert len(dup) == 2 and math.isclose(sum(s['length'] for s in dup), 1000)
    assert [d['part_index'] for d in got['degenerate_parts'] if d['source_id'] in (f"CAD-{H['DUP']}", f"CAD-{H['DUP'].upper()}")] == [1]
    assert len(comp(g, 'DUP')) == 1                           # the repeated vertex still joins the two halves
    mleader = sg.collect_segments([{'entity_handle': 'M1', 'entity_type': 'MULTILEADER', 'layer': 'E-LINE'}], layer_pattern='^E-')
    assert mleader['unsupported'] == {'MULTILEADER': 1} and mleader['segments'] == []
