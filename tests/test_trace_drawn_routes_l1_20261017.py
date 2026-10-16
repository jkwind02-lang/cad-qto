"""第五批 L1：已画走线追踪（登记 20261016-1056 期望 1、3）。

合成图（毫米，输出米）。箱块 BOX 为 600×400 方框，设备块 DEV 为 200×200 方框，走线画在“WIRE-动力”层。
请求的容差按米：snap 0.01、gap 0.3、端点挂接 0.3、等长判定 0.001。边界清单（备课第二节 L1）：
- 起点按箱外框而不是插入点：一条多段线从箱边到设备边，长度只算画出来的线；
- 两回路共用一段走线（同一条线）→ 两条都追到，共用段列出并标复核；同层两条平行线不算共用；
- 走线经过别的箱（串箱）→ 照追，标“经过别的箱”；
- 交叉不连：只能靠交叉过去的设备 → 网络断开（待办）；
- 图符遮断的小缺口（≤ gap，且共线）桥接，缺口长度单列并计入；超出容差的缺口 → 网络断开，不退回直角距离；
- 端点离走线在挂接容差内照追并记距离，超出 → 待办；实例找不到 → 待办；
- 画进挂接容差带、画进设备图符的线照计（从带里最近的线端算起）；设备只挂最近的一组线，邻近设备的线落进带里不用；
  箱出几路，带里几组线都可挂；挂接距离报实际用到的那个点；
- 走线和箱在楼层块内（实例号 PT-CAD-<顶层>/FLOOR/CAD-<块内>）；单位换算（输出米）；
- 范围边上的段 → 待办（不能整段计入）；两条等长路径 → 待办；只给起点 → 输出连通分量；
- 输出的 paths 行可直接交给 calculate_electrical_paths；映射线表按原图坐标列每段和每个缺口。
"""
import csv
import json
import math
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
ROUTES = ROOT / 'skills/qto-electrical-routes/scripts'
WIRE = {'layer': 'WIRE-动力'}
BOX = [(-300, -200), (300, -200), (300, 200), (-300, 200)]
DEV = [(-100, -100), (100, -100), (100, 100), (-100, 100)]


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def new_doc():
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.layers.add('WIRE-动力'); doc.layers.add('WIRE-照明')
    doc.blocks.new('BOX').add_lwpolyline(BOX, close=True)
    doc.blocks.new('DEV').add_lwpolyline(DEV, close=True)
    return doc


def ins(space, name, x, y):
    return space.add_blockref(name, (x, y)).dxf.handle


def extract(tmp_path, doc):
    tmp_path.mkdir(parents=True, exist_ok=True)
    dxf = tmp_path / 'routes.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert done.returncode == 0, done.stdout + done.stderr
    return neutral / 'CAD中性取证清单.json'


def trace(tmp_path, manifest, routes, **extra):
    request = {'schema': 'qto-drawn-route-request/1.0', 'drawing_id': 'SYN', 'layout': 'Model',
               'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'route_layers': '^WIRE-动力$', 'box_block_pattern': '^BOX$', 'snap_tolerance': 0.01, 'gap_tolerance': 0.3,
               'endpoint_tolerance': 0.3, 'equal_length_tolerance': 0.001, 'routes': routes, **extra}
    rp = tmp_path / 'request.json'; rp.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    done = run(ROUTES / 'trace_drawn_routes.py', '--cad-manifest', manifest, '--request', rp, '--output-dir', out)
    assert done.returncode in (0, 3), done.stdout + done.stderr
    result = json.loads((out / '走线追踪结果.json').read_text(encoding='utf-8'))
    paths = json.loads((out / '走线追踪路径.json').read_text(encoding='utf-8'))
    with (out / '走线追踪映射线.csv').open(encoding='utf-8-sig', newline='') as f:
        lines = list(csv.DictReader(f))
    with (out / '走线追踪待办.csv').open(encoding='utf-8-sig', newline='') as f:
        todo = list(csv.DictReader(f))
    return done.returncode, result, paths, lines, todo


def inst(handle):
    return {'instance_id': f'PT-CAD-{handle}'}


def draw_main(doc, msp):
    h = {}
    h['A1'], h['D1'] = ins(msp, 'BOX', 0, 0), ins(msp, 'DEV', 5000, 0)            # box edge to device edge
    h['R1'] = msp.add_lwpolyline([(300, 0), (2000, 0), (2000, 1000), (4000, 1000), (4000, 0), (4900, 0)], dxfattribs=WIRE).dxf.handle
    h['A2'], h['D2'], h['D3'] = ins(msp, 'BOX', 0, 10000), ins(msp, 'DEV', 6000, 10000), ins(msp, 'DEV', 6000, 12000)
    h['S'] = msp.add_line((300, 10000), (3000, 10000), dxfattribs=WIRE).dxf.handle   # trunk shared by two circuits
    msp.add_line((3000, 10000), (5900, 10000), dxfattribs=WIRE)
    msp.add_lwpolyline([(3000, 10000), (3000, 12000), (5900, 12000)], dxfattribs=WIRE)
    h['A3'], h['D4'], h['D5'] = ins(msp, 'BOX', 0, 20000), ins(msp, 'DEV', 6000, 20000), ins(msp, 'DEV', 6000, 20600)
    msp.add_line((300, 20000), (5900, 20000), dxfattribs=WIRE)                        # two parallel lines, 0.1 m apart
    msp.add_lwpolyline([(300, 20100), (5000, 20100), (5000, 20600), (5900, 20600)], dxfattribs=WIRE)
    h['A4'], h['D6'], h['D7'] = ins(msp, 'BOX', 0, 30000), ins(msp, 'DEV', 6000, 30000), ins(msp, 'DEV', 3000, 32100)
    msp.add_line((300, 30000), (5900, 30000), dxfattribs=WIRE)
    msp.add_line((3000, 28000), (3000, 32000), dxfattribs=WIRE)                       # crosses the line above, not joined
    h['A5'], h['D8'] = ins(msp, 'BOX', 0, 40000), ins(msp, 'DEV', 6000, 40000)        # 150 mm gap behind a symbol
    msp.add_line((300, 40000), (2900, 40000), dxfattribs=WIRE); msp.add_line((3050, 40000), (5900, 40000), dxfattribs=WIRE)
    h['A6'], h['D9'] = ins(msp, 'BOX', 0, 50000), ins(msp, 'DEV', 6000, 50000)        # 1 m gap: a break
    msp.add_line((300, 50000), (2500, 50000), dxfattribs=WIRE); msp.add_line((3500, 50000), (5900, 50000), dxfattribs=WIRE)
    h['A7'], h['D10'] = ins(msp, 'BOX', 0, 60000), ins(msp, 'DEV', 6000, 60000)       # starts 0.2 m off the box edge
    msp.add_line((500, 60000), (5900, 60000), dxfattribs=WIRE)
    h['A8'], h['D11'] = ins(msp, 'BOX', 0, 70000), ins(msp, 'DEV', 6000, 70000)       # starts 0.4 m off the box edge
    msp.add_line((700, 70000), (5900, 70000), dxfattribs=WIRE)
    h['A9'], h['B9'], h['D12'] = ins(msp, 'BOX', 0, 80000), ins(msp, 'BOX', 3000, 80000), ins(msp, 'DEV', 6000, 80000)
    msp.add_line((300, 80000), (5900, 80000), dxfattribs=WIRE)                        # straight through box B9
    h['A11'], h['D14'] = ins(msp, 'BOX', 0, 100000), ins(msp, 'DEV', 6000, 100000)    # two equal ways round
    msp.add_line((300, 100000), (1000, 100000), dxfattribs=WIRE)
    msp.add_lwpolyline([(1000, 100000), (1000, 101000), (5000, 101000), (5000, 100000)], dxfattribs=WIRE)
    msp.add_lwpolyline([(1000, 100000), (1000, 99000), (5000, 99000), (5000, 100000)], dxfattribs=WIRE)
    msp.add_line((5000, 100000), (5900, 100000), dxfattribs=WIRE)
    h['W'] = msp.add_line((300, 110000), (5900, 110000), dxfattribs={'layer': 'WIRE-照明'}).dxf.handle   # other layer
    h['A12'], h['D15'] = ins(msp, 'BOX', 0, 110000), ins(msp, 'DEV', 6000, 110000)
    msp.add_lwpolyline([(0, 120000), (2000, 120000), (2000, 122000)], dxfattribs=WIRE)   # traced between two points
    h['A13'] = ins(msp, 'BOX', 0, 130000)                                              # a point route leaving a box edge
    msp.add_line((300, 130000), (3000, 130000), dxfattribs=WIRE)
    h['A14'], h['D16'], h['D17'] = ins(msp, 'BOX', 0, 140000), ins(msp, 'DEV', 8100, 140000), ins(msp, 'DEV', 6000, 142000)
    h['TR'] = msp.add_line((300, 140000), (8000, 140000), dxfattribs=WIRE).dxf.handle    # one trunk line
    msp.add_lwpolyline([(4000, 140000), (4000, 142000), (5900, 142000)], dxfattribs=WIRE)   # a branch ending on its middle
    h['A15'], h['B15'] = ins(msp, 'BOX', 0, 150000), ins(msp, 'BOX', 10000, 150000)   # one line, two boxes, each route
    h['DX'], h['DY'] = ins(msp, 'DEV', 5000, 151000), ins(msp, 'DEV', 8000, 149000)   # running its own part of it
    h['L15'] = msp.add_line((300, 150000), (9700, 150000), dxfattribs=WIRE).dxf.handle
    msp.add_lwpolyline([(3000, 150000), (3000, 151000), (4900, 151000)], dxfattribs=WIRE)
    msp.add_lwpolyline([(7000, 150000), (7000, 149000), (7900, 149000)], dxfattribs=WIRE)
    h['A16'], h['D18'] = ins(msp, 'BOX', 0, 160000), ins(msp, 'DEV', 5100, 160400)   # a branch from 0.1 m off the box
    h['BR'] = msp.add_line((400, 160000), (400, 160400), dxfattribs=WIRE).dxf.handle   # up to a trunk 0.2 m over the box
    h['TK'] = msp.add_line((-2000, 160400), (5000, 160400), dxfattribs=WIRE).dxf.handle
    h['A17'], h['D19'] = ins(msp, 'BOX', 0, 170000), ins(msp, 'DEV', 5300, 170000)   # a line drawn to the device centre,
    h['E1'] = msp.add_line((500, 170000), (5000, 170000), dxfattribs=WIRE).dxf.handle  # its last joint 0.2 m off the device
    h['E2'] = msp.add_line((5000, 170000), (5250, 170000), dxfattribs=WIRE).dxf.handle  # a joint inside the device too
    h['E2b'] = msp.add_line((5250, 170000), (5300, 170000), dxfattribs=WIRE).dxf.handle
    msp.add_line((0, 170200), (0, 172000), dxfattribs=WIRE)                            # another circuit from the box edge
    h['A18'], h['D20'] = ins(msp, 'BOX', 0, 180000), ins(msp, 'DEV', 800, 175000)    # a trunk down from the box to a device;
    h['DN'] = ins(msp, 'DEV', 800, 175350)                                             # a neighbour device 0.35 m over it,
    h['T1'] = msp.add_line((0, 179800), (0, 175000), dxfattribs=WIRE).dxf.handle       # whose line branches off the trunk
    h['E3'] = msp.add_line((0, 175000), (800, 175000), dxfattribs=WIRE).dxf.handle     # and ends 0.25 m off this device
    msp.add_line((0, 175350), (800, 175350), dxfattribs=WIRE)
    floor = doc.blocks.new('FLOOR')                                                    # box, device and line in a floor block
    inner_box, inner_dev = ins(floor, 'BOX', 0, 0), ins(floor, 'DEV', 6000, 0)
    floor.add_line((300, 0), (5900, 0), dxfattribs=WIRE)
    top = ins(msp, 'FLOOR', 0, 90000)
    h['FA'], h['FD'] = f'{top}/FLOOR/CAD-{inner_box}', f'{top}/FLOOR/CAD-{inner_dev}'
    return h


MAIN_ROUTES = [('RT', 'A14', 'D17'), ('RT2', 'A14', 'D16'), ('RA', 'A15', 'DX'), ('RB', 'B15', 'DY'),
               ('R1', 'A1', 'D1'), ('R2', 'A2', 'D2'), ('R3', 'A2', 'D3'), ('R4', 'A3', 'D4'), ('R5', 'A3', 'D5'),
               ('R6', 'A4', 'D6'), ('R7', 'A4', 'D7'), ('R8', 'A5', 'D8'), ('R9', 'A6', 'D9'), ('R10', 'A7', 'D10'),
               ('R11', 'A8', 'D11'), ('R12', 'A9', 'D12'), ('R14', 'A11', 'D14'), ('R15', 'A12', 'D15'), ('RF', 'FA', 'FD'),
               ('RS', 'A16', 'D18'), ('RE', 'A17', 'D19'), ('RN', 'A18', 'D20')]


@pytest.fixture(scope='module')
def main(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('l1main')
    doc = new_doc(); h = draw_main(doc, doc.modelspace())
    manifest = extract(tmp, doc)
    routes = [{'route_id': rid, 'from': inst(h[a]), 'to': inst(h[b]), 'circuit_label': {'text': rid, 'source': '合成'}}
              for rid, a, b in MAIN_ROUTES]
    routes += [{'route_id': 'P1', 'from': {'point': [0.0, 120.0]}, 'to': {'point': [2.1, 122.0]}},
               {'route_id': 'P2', 'from': {'point': [0.3, 130.0]}, 'to': {'point': [3.0, 130.0]}},
               {'route_id': 'ONLY', 'from': inst(h['A1'])},
               {'route_id': 'MISS', 'from': {'instance_id': 'PT-CAD-FFFFF'}, 'to': inst(h['D1'])}]
    code, result, paths, lines, todo = trace(tmp, manifest, routes)
    return h, code, {r['route_id']: r for r in result['routes']}, result, paths, lines, todo, tmp


def test_a_drawn_route_is_measured_from_the_box_outline(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    r1 = routes['R1']
    assert r1['status'] == '已追踪' and r1['length_m'] == pytest.approx(6.6) and r1['bridged_gap_length_m'] == 0
    assert r1['from']['basis'] == '外框' and r1['from']['attach_distance_m'] == pytest.approx(0.0)
    assert [s['handle'] for s in r1['segments']] == [h['R1']] * 5                 # five edges of one polyline, in order
    assert r1['segments'][0]['from_point'] == pytest.approx([0.3, 0.0]) and r1['segments'][-1]['to_point'] == pytest.approx([4.9, 0.0])
    assert r1['circuit_label'] == {'text': 'R1', 'source': '合成'} and not r1['review_flags']
    assert routes['RF']['status'] == '已追踪' and routes['RF']['length_m'] == pytest.approx(5.6)   # in a floor block
    assert '/FLOOR/CAD-' in routes['RF']['segments'][0]['segment_id']
    p1 = routes['P1']                                                              # end point 0.1 m off the line end
    assert p1['status'] == '已追踪' and p1['length_m'] == pytest.approx(4.0) and not p1['review_flags']
    assert p1['from']['basis'] == '坐标' and p1['to']['attach_distance_m'] == pytest.approx(0.1)


def test_shared_line_is_listed_parallel_lines_are_not(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    assert routes['R2']['length_m'] == pytest.approx(5.6) and routes['R3']['length_m'] == pytest.approx(7.6)
    shared = {row['handle']: row['route_ids'] for row in result['shared_segments']}
    assert shared == {h['S']: ['R2', 'R3'], h['TR']: ['RT', 'RT2']}
    for rid in ('R2', 'R3'):
        assert [f['flag'] for f in routes[rid]['review_flags']] == ['与别的回路共用走线段'], rid
    assert routes['R4']['length_m'] == pytest.approx(5.6) and routes['R5']['length_m'] == pytest.approx(6.1)
    assert not routes['R4']['review_flags'] and not routes['R5']['review_flags']


def test_a_branch_ending_on_a_trunk_takes_only_the_trunk_part_it_runs(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    rt = routes['RT']                                   # 3.7 m of the trunk, then the 3.9 m branch
    assert rt['status'] == '已追踪' and rt['length_m'] == pytest.approx(7.6) and len(rt['t_junctions']) == 1
    first = rt['segments'][0]
    assert first['handle'] == h['TR'] and first['length_m'] == pytest.approx(3.7)
    assert first['fraction_range'] == pytest.approx([0.0, 3700 / 7700])
    assert routes['RT2']['length_m'] == pytest.approx(7.7) and 'fraction_range' not in routes['RT2']['segments'][0]
    for rid, length in (('RA', 5.6), ('RB', 4.6)):      # two parts of one line that do not overlap are not shared
        assert routes[rid]['length_m'] == pytest.approx(length) and not routes[rid]['review_flags'], rid
    rt_lines = [row for row in lines if row['route_id'] == 'RT']
    assert [float(rt_lines[0][k]) for k in ('x1', 'y1', 'x2', 'y2')] == pytest.approx([300, 140000, 4000, 140000])


def test_t_junctions_can_be_switched_off(tmp_path):
    doc = new_doc(); msp = doc.modelspace()
    a, d = ins(msp, 'BOX', 0, 0), ins(msp, 'DEV', 6000, 2000)
    msp.add_line((300, 0), (8000, 0), dxfattribs=WIRE)
    msp.add_lwpolyline([(4000, 0), (4000, 2000), (5900, 2000)], dxfattribs=WIRE)
    manifest = extract(tmp_path, doc)
    code, result, paths, lines, todo = trace(tmp_path, manifest, [{'route_id': 'T', 'from': inst(a), 'to': inst(d)}],
                                             t_junctions=False)
    route = result['routes'][0]
    assert route['status'] == '待办' and route['problems'][0]['problem'] == '网络断开'
    assert len(route['problems'][0]['t_contact_hints']) == 1


def test_crossings_and_wide_gaps_break_and_small_gaps_bridge(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    assert routes['R6']['status'] == '已追踪' and routes['R6']['length_m'] == pytest.approx(5.6)
    for rid in ('R7', 'R9'):
        assert routes[rid]['status'] == '待办' and [p['problem'] for p in routes[rid]['problems']] == ['网络断开'], rid
        assert 'length_m' not in routes[rid] and rid not in {p['route_id'] for p in paths['paths']}, rid
    assert routes['R9']['problems'][0]['nearest_gap_m'] == pytest.approx(1.0)
    r8 = routes['R8']
    assert r8['status'] == '已追踪' and r8['drawn_length_m'] == pytest.approx(5.45)
    assert r8['bridged_gap_length_m'] == pytest.approx(0.15) and r8['length_m'] == pytest.approx(5.6)
    assert len(r8['bridged_gaps']) == 1
    assert routes['R15']['status'] == '待办'                                         # the line is on another layer
    assert [p['problem'] for p in routes['R15']['problems']] == ['起点离走线超出容差', '终点离走线超出容差']


def test_lines_drawn_into_the_attach_band_are_counted(main):
    """宁波众芯 5# WP7（7BB2）、A 区 WP2（46C9）：挂接容差带里画进去的线不能被最短路径跳过。"""
    h, code, routes, result, paths, lines, todo, tmp = main
    rs = routes['RS']                                   # branch 0.4 m + trunk 4.6 m, not just the trunk from the band
    assert rs['status'] == '已追踪' and rs['length_m'] == pytest.approx(5.0)
    assert [s['handle'] for s in rs['segments']] == [h['BR'], h['TK']]
    assert rs['segments'][1]['fraction_range'] == pytest.approx([2400 / 7000, 1.0])
    assert rs['from']['attach_distance_m'] == pytest.approx(0.1)
    re_ = routes['RE']                                  # to the line end inside the device, not the joint 0.2 m off it
    assert re_['status'] == '已追踪' and re_['length_m'] == pytest.approx(4.8)
    assert [s['handle'] for s in re_['segments']] == [h['E1'], h['E2'], h['E2b']]   # inside: to the free line end
    assert re_['to']['attach_distance_m'] == pytest.approx(0.0)
    assert re_['from']['attach_distance_m'] == pytest.approx(0.2)   # the node used, not the other circuit's on the edge
    rn = routes['RN']                                   # the neighbour's line is shorter but a device takes one line
    assert rn['status'] == '已追踪' and rn['length_m'] == pytest.approx(5.6)
    assert [s['handle'] for s in rn['segments']] == [h['T1'], h['E3']] and not rn['t_junctions']
    assert [round(x['distance_m'], 6) for x in rn['to']['other_line_ends_in_band']] == [0.25]
    assert 'other_line_ends_in_band' not in routes['RE']['from']    # a box feeds several circuits: all attach


def test_endpoint_tolerance_and_missing_instances(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    assert routes['R10']['status'] == '已追踪' and routes['R10']['from']['attach_distance_m'] == pytest.approx(0.2)
    assert routes['R10']['length_m'] == pytest.approx(5.4)
    r11 = routes['R11']
    assert r11['status'] == '待办' and r11['problems'][0]['problem'] == '起点离走线超出容差'
    assert r11['problems'][0]['nearest_m'] == pytest.approx(0.4)
    assert routes['MISS']['status'] == '待办' and routes['MISS']['problems'][0]['problem'] == '起点实例在图上找不到'


def test_through_another_box_and_equal_ways_and_only_a_start(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    r12 = routes['R12']
    assert r12['status'] == '已追踪' and r12['length_m'] == pytest.approx(5.6)
    assert [(f['flag'], f['instance_ids']) for f in r12['review_flags']] == [('经过别的箱', [f"PT-CAD-{h['B9']}"])]
    assert routes['P2']['status'] == '已追踪' and not routes['P2']['review_flags']   # the box at its start is its own
    r14 = routes['R14']
    assert r14['status'] == '待办' and r14['problems'][0]['problem'] == '多条等长路径' and r14['problems'][0]['count'] == 2
    only = routes['ONLY']
    assert only['status'] == '分量' and len(only['components']) == 1
    comp = only['components'][0]
    assert comp['length_m'] == pytest.approx(6.6) and len(comp['dangling_points']) == 2


def test_outputs_feed_the_path_ledger_and_the_scene_mapping(main):
    h, code, routes, result, paths, lines, todo, tmp = main
    assert code == 3 and result['status'] == 'REVIEW_REQUIRED'
    traced = {r for r, v in routes.items() if v['status'] == '已追踪'}
    assert {p['route_id'] for p in paths['paths']} == traced
    p1 = next(p for p in paths['paths'] if p['route_id'] == 'R1')
    assert p1['length_basis'] == 'actual_drawn_path' and p1['role'] == 'horizontal' and p1['source_handles'] == [h['R1']]
    assert p1['from_node'] == f"PT-CAD-{h['A1']}" and p1['to_node'] == f"PT-CAD-{h['D1']}"
    assert all(s.startswith('SYN:SEG-') for s in p1['source_ids'])
    ledger = {'schema': 'qto-electrical-path-ledger/1.0', 'unit': 'm', 'project_id': 'SYN', 'paths': [p1],
              'circuits': [{'id': 'WP1', 'kind': 'cable', 'system': '动力', 'spec': 'YJV-5x4', 'physical_id': 'WP1',
                            'start_node': p1['from_node'], 'end_node': p1['to_node'], 'parallel_count': 1,
                            'path_ids': [p1['id']], 'source_ids': ['合成']}]}
    lp = tmp / 'ledger.json'; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    done = run(ROUTES / 'calculate_electrical_paths.py', lp)
    assert done.returncode == 0, done.stdout
    assert json.loads(done.stdout)['items'][0]['length_m'] == pytest.approx(6.6)
    r1_lines = [row for row in lines if row['route_id'] == 'R1']
    assert [row['kind'] for row in r1_lines] == ['segment'] * 5 and {row['handle'] for row in r1_lines} == {h['R1']}
    assert [float(r1_lines[0][k]) for k in ('x1', 'y1', 'x2', 'y2')] == pytest.approx([300, 0, 2000, 0])   # drawing units
    assert [row['kind'] for row in lines if row['route_id'] == 'R8'] == ['segment', 'gap', 'segment']
    assert {row['route_id'] for row in todo} >= {'R7', 'R9', 'R11', 'R14', 'R15', 'MISS'}
    assert {row['kind'] for row in todo if row['route_id'] == 'R2'} == {'复核'}


def test_a_route_over_the_scope_edge_is_not_measured_whole(tmp_path):
    doc = new_doc(); msp = doc.modelspace()
    a, d = ins(msp, 'BOX', 0, 0), ins(msp, 'DEV', 6000, 0)
    msp.add_lwpolyline([(300, 0), (1000, 0), (3000, -4000), (5000, 0), (5900, 0)], dxfattribs=WIRE)
    a2, d2 = ins(msp, 'BOX', 0, 5000), ins(msp, 'DEV', 9000, 5000)                     # device beyond the scope
    msp.add_line((300, 5000), (8900, 5000), dxfattribs=WIRE)
    manifest = extract(tmp_path, doc)
    scope = {'bounds': {'xmin': -1000, 'xmax_exclusive': 7000, 'ymin': -3000, 'ymax_exclusive': 8000}}
    code, result, paths, lines, todo = trace(tmp_path, manifest, [{'route_id': 'E', 'from': inst(a), 'to': inst(d)},
                                                                  {'route_id': 'OUT', 'from': inst(a2), 'to': inst(d2)}], scope=scope)
    routes = {r['route_id']: r for r in result['routes']}
    assert routes['E']['status'] == '待办' and routes['E']['problems'][0]['problem'] == '路径经过范围边'
    assert routes['E']['problems'][0]['segment_ids'] and not paths['paths']
    assert routes['OUT']['status'] == '待办' and routes['OUT']['problems'][0]['problem'] == '终点不在范围内'


def test_request_is_checked(tmp_path):
    doc = new_doc(); doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs=WIRE); manifest = extract(tmp_path, doc)
    for bad, message in ((dict(route_layers=''), 'route_layers'), (dict(endpoint_tolerance=-1), 'endpoint_tolerance'),
                         (dict(routes=[{'route_id': 'X', 'from': {}}]), 'from')):
        request = {'schema': 'qto-drawn-route-request/1.0', 'unit': {'factor_to_output': 0.001, 'source_id': 'U'},
                   'route_layers': 'W', 'endpoint_tolerance': 0.3, 'routes': [{'route_id': 'X', 'from': {'point': [0, 0]}}]}
        request.update(bad)
        rp = tmp_path / 'bad.json'; rp.write_text(json.dumps(request), encoding='utf-8')
        done = run(ROUTES / 'trace_drawn_routes.py', '--cad-manifest', manifest, '--request', rp, '--output-dir', tmp_path / 'o')
        assert done.returncode == 2 and message in done.stdout, (bad, done.stdout)
