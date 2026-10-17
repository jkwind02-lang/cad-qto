"""第五批 L3：按图层量长与两端归类（登记 20261016-1824「室外接地」「插座箱归箱」，1426 照明支路的归类部分，0825 室外串接）。

合成图（毫米，输出米），走线画在“WIRE-照明”层。图页表照覆盖台账的写法（图面单位）：平面 P1、P2，P1 里的图例框
（sheet_kind 图例的小图页），图例页 LG。实例取点位处置清单（只用 adopted），外框用行里主遍历的 scope_geometry_bbox；
类别映射与半径由请求写，半径没有默认值。边界清单（备课第二节 L3）：
- 图例页、图例框里的样线剔除并单列；没进图页表的段归“图页未定”并提醒；
- 段按中点定页、按项目给的区域多边形分组；两端不在同一图页的段和分量标“跨图页”；
- 分量 = 剔除后的段 + 缺口桥接（首尾不相连的线按端点串接）+ T 接；悬挂端在半径内找实例：
  一类 → 归这一类；端点半径内两类 → 未决；有未映射的实例 → 未决；什么都没有 → 未连；楼栋示意圆 → 待办；
  半径是到外框距离的硬边界（圆，不是方框：对角 0.354 m 在 0.3 m 外）；
- 同层混入别的回路（一个分量的负荷端两类）→ 未决，不按长度分摊；一个分量连到两只箱 → 复核串箱；
- 插座箱归箱：同一分量里只一只箱 → 对应表并给 L1 请求行；两只 → 未决；挂不上走线 → 待办；
- 分类合计 = 图页合计（守恒）；图纸单位逐图不同（米图与毫米图量出同样长度）；
- 平面走线按平面量：等标高的多段线（D003 的 elevation 7.71、15.42）压到平面再连；两端标高不同的斜线（D021 52DF）按平面
  投影计长并警告，路线追踪同样，经过斜线的路线标复核。
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
WIRE = {'layer': 'WIRE-照明'}
SHAPES = {'BOX': (300, 200), 'LAMP': (100, 100), 'SOCK': (100, 100), 'MOTOR': (100, 100), 'BTN': (100, 100), 'SBOX': (100, 100)}
RULE = {'BOX': 'BOX', 'LAMP': 'LAMP', 'SOCK': 'SOCK', 'MOTOR': 'MOTOR', 'BTN': 'BTN', 'SBOX': 'SBOXR'}
CLASSES = [{'class': '箱', 'rule_ids': ['BOX'], 'role': 'source'}, {'class': '照明', 'rule_ids': ['LAMP']},
           {'class': '插座', 'rule_ids': ['SOCK']}, {'class': '动力', 'rule_ids': ['MOTOR']},
           {'class': '插座箱', 'block_pattern': '^SBOX$'}]
SHEETS = [('P1', '一层平面', '平面', 0, 0, 60000, 40000), ('P1-LG', '图例框', '图例', 50000, 30000, 60000, 40000),
          ('P2', '二层平面', '平面', 60000, 0, 100000, 40000), ('LG', '图例', '图例', 100000, 0, 120000, 20000)]
REGIONS = [{'region_id': 'A区', 'polygon': [[0, 0], [30, 0], [30, 40], [0, 40]]},
           {'region_id': 'B区', 'polygon': [[30, 0], [60, 0], [60, 40], [30, 40]]}]


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def new_doc(units=4):
    doc = ezdxf.new('R2013'); doc.units = units
    for name in ('WIRE-照明', 'WIRE-动力', '楼号'):
        doc.layers.add(name)
    for name, (hx, hy) in SHAPES.items():
        doc.blocks.new(name).add_lwpolyline([(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)], close=True)
    return doc


class Plan:
    def __init__(self, msp, factor=0.001):
        self.msp, self.factor, self.rows, self.h = msp, factor, [], {}

    def ins(self, key, name, x, y, disposition='adopted'):
        handle = self.msp.add_blockref(name, (x, y)).dxf.handle
        hx, hy = SHAPES[name]
        self.rows.append({'physical_id': f'PT-CAD-{handle}', 'object_id': f'PT-CAD-{handle}', 'block_name': name,
                          'rule_ids': [RULE[name]], 'disposition': disposition, 'coordinate_unit': 'm',
                          'point': [x * self.factor, y * self.factor, 0.0], 'scope_geometry_bbox': [x - hx, y - hy, x + hx, y + hy]})
        self.h[key] = f'PT-CAD-{handle}'

    def line(self, key, *points, layer=WIRE):
        e = self.msp.add_lwpolyline(points, dxfattribs=layer) if len(points) > 2 else self.msp.add_line(*points, dxfattribs=layer)
        self.h[key] = e.dxf.handle


def draw(plan):
    p = plan
    p.ins('AL1', 'BOX', 1000, 2000); p.ins('LP1', 'LAMP', 5100, 2000)                       # C1 lighting, a 0.15 m gap
    p.line('C1a', (1300, 2000), (3000, 2000)); p.line('C1b', (3150, 2000), (5000, 2000))
    p.ins('AL2', 'BOX', 1000, 6000); p.ins('MT2', 'MOTOR', 8100, 6000); p.ins('LP2', 'LAMP', 4000, 8100)   # C2 mixed
    p.line('C2', (1300, 6000), (8000, 6000)); p.line('C2b', (4000, 6000), (4000, 8000))
    p.ins('AL3', 'BOX', 1000, 10000); p.ins('LP3', 'LAMP', 5100, 10000); p.ins('SK3', 'SOCK', 5100, 10350)   # C3 end: two classes
    p.line('C3', (1300, 10000), (5000, 10000))
    p.ins('AL4', 'BOX', 1000, 14000); p.ins('LPX', 'LAMP', 4100, 14000, disposition='unresolved')   # C4 open: not adopted
    p.line('C4', (1300, 14000), (4000, 14000))
    p.ins('AL5', 'BOX', 1000, 18000); p.ins('AL6', 'BOX', 6100, 18000); p.ins('LP5', 'LAMP', 3000, 16900)   # C5 two boxes
    p.line('C5', (1300, 18000), (5800, 18000)); p.line('C5b', (3000, 18000), (3000, 17000))
    p.ins('AL7', 'BOX', 55000, 2000); p.ins('LP7', 'LAMP', 65100, 2000)                       # C6 over two sheets
    p.line('C6', (55300, 2000), (65000, 2000))
    p.ins('AL8', 'BOX', 1000, 22000); p.ins('BT8', 'BTN', 5100, 22000)                         # C7 unmapped block
    p.line('C7', (1300, 22000), (5000, 22000))
    p.ins('AL9', 'BOX', 34700, 26000)                                                          # C8 to a building circle
    p.line('C8', (35000, 26000), (45000, 26000)); p.msp.add_circle((46000, 26000), 1000, dxfattribs={'layer': '楼号'})
    p.ins('AL10', 'BOX', 1000, 30000); p.ins('SB1', 'SBOX', 4100, 30000)                       # C9 a socket box fed by AL10
    p.line('C9', (1300, 30000), (4000, 30000))
    p.ins('AL11', 'BOX', 10000, 30000); p.ins('AL12', 'BOX', 20000, 30000); p.ins('SB2', 'SBOX', 15000, 31100)   # C10
    p.line('C10', (10300, 30000), (19700, 30000)); p.line('C10b', (15000, 30000), (15000, 31000))
    p.ins('SB3', 'SBOX', 25000, 34000)                                                         # a socket box off any line
    p.line('LB', (51000, 35000), (55000, 35000)); p.line('LG', (101000, 5000), (105000, 5000))   # legend samples
    p.line('OUT', (200000, 0), (201000, 0))                                                    # on no sheet
    p.line('POWER', (1300, 2500), (5000, 2500), layer={'layer': 'WIRE-动力'})                  # another layer


def write_inputs(tmp, rows):
    with (tmp / 'sheets.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['图页编号', '图名', '类型', 'x1', 'y1', 'x2', 'y2']); w.writerows(SHEETS)
    (tmp / 'dispositions.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def extract(tmp, doc):
    tmp.mkdir(parents=True, exist_ok=True)
    dxf = tmp / 'layer.dxf'; doc.saveas(dxf)
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp / 'neutral')
    assert done.returncode == 0, done.stdout + done.stderr
    return tmp / 'neutral' / 'CAD中性取证清单.json'


def request(**extra):
    req = {'schema': 'qto-layer-length-request/1.0', 'drawing_id': 'SYN', 'layout': 'Model',
           'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
           'route_layers': '^WIRE-照明$', 'snap_tolerance': 0.01, 'gap_tolerance': 0.3,
           'sheets': [{'path': 'sheets.csv', 'constants': {'drawing': 'SYN'},
                       'columns': {'sheet_id': '图页编号', 'title': '图名', 'sheet_kind': '类型', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}],
           'regions': REGIONS,
           'endpoints': {'instances': {'path': 'dispositions.jsonl'}, 'classes': CLASSES, 'radius': 0.3,
                         'building_markers': {'layer_pattern': '^楼号$'}},
           'feeders': {'device_classes': ['插座箱']}}
    req.update(extra)
    return req


def measure(tmp, manifest, req):
    rp = tmp / 'request.json'; rp.write_text(json.dumps(req, ensure_ascii=False), encoding='utf-8')
    out = tmp / 'out'
    done = run(ROUTES / 'trace_drawn_routes.py', '--cad-manifest', manifest, '--request', rp, '--output-dir', out)
    return done, out


@pytest.fixture(scope='module')
def layer(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('l3')
    doc = new_doc(); plan = Plan(doc.modelspace()); draw(plan)
    manifest = extract(tmp, doc); write_inputs(tmp, plan.rows)
    done, out = measure(tmp, manifest, request())
    assert done.returncode in (0, 3), done.stdout + done.stderr
    result = json.loads((out / '按图层量长结果.json').read_text(encoding='utf-8'))
    comps = {}
    for comp in result['components']:
        for handle in comp['handles']:
            comps[handle] = comp
    with (out / '按图层量长待办.csv').open(encoding='utf-8-sig', newline='') as f:
        todo = list(csv.DictReader(f))
    assert [(t['component_id'], t['kind'], t['problem']) for t in result['todo']] == [(t['component_id'], t['kind'], t['problem']) for t in todo]
    return plan.h, done, result, comps, todo, out, tmp, manifest


def test_legend_samples_are_dropped_and_lines_are_totalled_by_sheet_and_region(layer):
    h, done, result, comps, todo, out, tmp, manifest = layer
    excluded = {row['handle']: row for row in result['excluded_segments']}
    assert set(excluded) == {h['LB'], h['LG']}
    assert {excluded[h['LB']]['sheet_id'], excluded[h['LG']]['sheet_id']} == {'P1-LG', 'LG'}   # the smallest sheet wins
    assert result['totals']['excluded_m'] == pytest.approx(8.0)
    by_sheet = {row['sheet_id']: row['length_m'] for row in result['totals']['by_sheet']}
    assert by_sheet == pytest.approx({'P1': 50.95, 'P2': 9.7, '图页未定': 1.0})
    by_region = {row['region_id']: row['length_m'] for row in result['totals']['by_region']}
    assert by_region == pytest.approx({'A区': 40.95, 'B区': 10.0, '区域未定': 10.7})
    assert [w['warning'] for w in result['warnings']] == ['segments_on_no_sheet']
    assert h['POWER'] not in comps                                              # another layer is not read
    assert result['parameters']['exclude_sheet_kinds'] == ['图例']


def test_components_join_gaps_and_t_junctions_and_end_classes_decide(layer):
    h, done, result, comps, todo, out, tmp, manifest = layer
    c1 = comps[h['C1a']]
    assert c1 is comps[h['C1b']] and c1['length_m'] == pytest.approx(3.55) and c1['bridged_gap_length_m'] == pytest.approx(0.15)
    assert (c1['status'], c1['class']) == ('已归类', '照明')
    assert c1['sources'] == [h['AL1']]
    c2 = comps[h['C2']]                                     # motor and lamp on one network: unresolved, not shared out
    assert c2 is comps[h['C2b']] and c2['length_m'] == pytest.approx(8.7)
    assert c2['status'] == '未决' and c2['load_classes'] == ['动力', '照明'] and c2['class'] is None
    c3 = comps[h['C3']]
    assert c3['status'] == '未决' and any(e['status'] == '未决' and e['classes'] == ['插座', '照明'] for e in c3['ends'])
    assert comps[h['C4']]['status'] == '未连'                # the lamp next to it is not adopted
    c5 = comps[h['C5']]
    assert (c5['status'], c5['class']) == ('已归类', '照明') and c5['sources'] == sorted([h['AL5'], h['AL6']])
    assert '连到两只以上箱' in [f['flag'] for f in c5['review_flags']]
    c7 = comps[h['C7']]
    assert c7['status'] == '未决' and any(e.get('unmapped_instances') == [h['BT8']] for e in c7['ends'])
    c8 = comps[h['C8']]
    assert c8['status'] == '未连' and any(e['status'] == '楼栋示意圆' for e in c8['ends'])
    assert ('到楼栋示意圆，没有进户点' in {row['problem'] for row in todo if row['component_id'] == c8['component_id']})


def test_sheet_crossing_and_conservation(layer):
    h, done, result, comps, todo, out, tmp, manifest = layer
    c6 = comps[h['C6']]
    assert c6['sheets'] == ['P2'] and '跨图页' in [f['flag'] for f in c6['review_flags']]
    assert [row['handle'] for row in result['segments_crossing_sheets']] == [h['C6']]
    by_class = {row['class']: row['length_m'] for row in result['totals']['by_class']}
    assert by_class == pytest.approx({'照明': 18.75, '插座箱': 13.1, '未决': 16.1, '未连': 13.7})
    assert math.fsum(by_class.values()) == pytest.approx(math.fsum(r['length_m'] for r in result['totals']['by_sheet']))
    assert result['checks']['class_total_equals_sheet_total'] is True
    assert done.returncode == 3 and result['status'] == 'REVIEW_REQUIRED'
    rows = list(csv.DictReader((out / '按图层量长分量.csv').open(encoding='utf-8-sig', newline='')))
    assert len(rows) == len(result['components'])
    lines = list(csv.DictReader((out / '按图层量长映射线.csv').open(encoding='utf-8-sig', newline='')))
    assert {row['handle'] for row in lines} >= {h['C1a'], h['LB']} and {row['kind'] for row in lines} >= {'segment', 'gap', 'excluded'}


def test_socket_boxes_are_traced_to_their_feeding_box(layer):
    h, done, result, comps, todo, out, tmp, manifest = layer
    feeds = {row['device']: row for row in result['feeders']}
    assert (feeds[h['SB1']]['status'], feeds[h['SB1']]['box']) == ('对应', h['AL10'])
    assert feeds[h['SB2']]['status'] == '未决' and feeds[h['SB2']]['box_candidates'] == sorted([h['AL11'], h['AL12']])
    assert feeds[h['SB3']]['status'] == '待办' and feeds[h['SB3']]['problem'] == '离走线超出半径'
    assert result['l1_routes'] == [{'route_id': f"FEED-{h['SB1']}", 'from': {'instance_id': h['AL10']}, 'to': {'instance_id': h['SB1']}}]


def test_units_differ_per_drawing(tmp_path):
    for units, factor, scale in ((6, 1.0, 1.0), (4, 0.001, 1000.0)):   # a metre drawing and a millimetre drawing
        doc = new_doc(units); msp = doc.modelspace()
        msp.add_line((0, 0), (3 * scale, 0), dxfattribs=WIRE); msp.add_line((3 * scale, 0), (3 * scale, 4 * scale), dxfattribs=WIRE)
        here = tmp_path / str(units)
        manifest = extract(here, doc)
        req = request(unit={'factor_to_output': factor, 'output_coordinate_unit': 'm', 'source_id': f'INSUNITS={units}'})
        for key in ('sheets', 'regions', 'endpoints', 'feeders'):
            req.pop(key)
        done, out = measure(here, manifest, req)
        assert done.returncode == 3, done.stdout                       # no sheet table: every line is on no sheet
        result = json.loads((out / '按图层量长结果.json').read_text(encoding='utf-8'))
        (comp,) = result['components']
        assert comp['length_m'] == pytest.approx(7.0) and comp['status'] == '未归类' and len(comp['dangling_points']) == 2


def test_end_radius_is_a_hard_edge(tmp_path):
    doc = new_doc(); plan = Plan(doc.modelspace())
    plan.ins('ALA', 'BOX', 1000, 2000); plan.ins('LPA', 'LAMP', 5350, 2000)    # lamp outline 0.25 m off the line end
    plan.line('IN', (1300, 2000), (5000, 2000))
    plan.ins('ALB', 'BOX', 1000, 6000); plan.ins('LPB', 'LAMP', 5500, 6000)    # 0.4 m off: outside the 0.3 m radius
    plan.line('OFF', (1300, 6000), (5000, 6000))
    plan.ins('ALC', 'BOX', 1000, 10000); plan.ins('LPC', 'LAMP', 5350, 10350)  # outline corner 0.354 m off: in the square, not the circle
    plan.line('DIAG', (1300, 10000), (5000, 10000))
    manifest = extract(tmp_path, doc); write_inputs(tmp_path, plan.rows)
    req = request(); req.pop('feeders'); req.pop('regions')
    done, out = measure(tmp_path, manifest, req)
    assert done.returncode in (0, 3), done.stdout + done.stderr
    result = json.loads((out / '按图层量长结果.json').read_text(encoding='utf-8'))
    comps = {comp['handles'][0]: comp for comp in result['components']}
    inside = comps[plan.h['IN']]
    assert (inside['status'], inside['class']) == ('已归类', '照明')
    assert [e['instances'][0]['distance_m'] for e in inside['ends'] if e['classes'] == ['照明']] == [pytest.approx(0.25)]
    for key in ('OFF', 'DIAG'):
        outside = comps[plan.h[key]]
        assert outside['status'] == '未连' and sorted(e['status'] for e in outside['ends']) == ['已归类', '未连'], key
        assert [e['instances'] for e in outside['ends'] if e['status'] == '未连'] == [[]], key


def test_plan_lines_at_an_elevation_join_in_plan(tmp_path):
    doc = new_doc(); msp = doc.modelspace()                       # D003: LWPOLYLINE elevations 0, 7.71, 15.42 m
    msp.add_lwpolyline([(0, 0), (3000, 0)], dxfattribs=WIRE)
    msp.add_lwpolyline([(3000, 0), (6000, 0)], dxfattribs={**WIRE, 'elevation': 7710})
    sloped = msp.add_line((0, 5000, 1000), (3000, 5000, 3000), dxfattribs=WIRE).dxf.handle   # D021 52DF: z 8000 to 0
    msp.add_line((3000, 5000, 0), (6000, 5000, 0), dxfattribs=WIRE)
    msp.add_line((0, 9000, 0), (3000, 9000, 0.004), dxfattribs=WIRE)          # z noise under the 0.01 m snap: not a slope
    manifest = extract(tmp_path, doc)
    req = request()
    for key in ('sheets', 'regions', 'endpoints', 'feeders'):
        req.pop(key)
    done, out = measure(tmp_path, manifest, req)
    result = json.loads((out / '按图层量长结果.json').read_text(encoding='utf-8'))
    assert result['graph']['elevations_flattened'] == {'7710': 1, '0.004': 1}
    (slope,) = result['graph']['sloped_lines_in_plan']
    assert slope['handle'] == sloped and (slope['length_3d_m'], slope['length_plan_m']) == (pytest.approx(3.606, abs=1e-3), pytest.approx(3.0))
    assert sorted(round(c['length_m'], 3) for c in result['components']) == [3.0, 6.0, 6.0]   # joined and counted in plan
    assert result['totals']['counted_m'] == pytest.approx(15.0)
    assert 'sloped_lines_measured_in_plan' in [w['warning'] for w in result['warnings']]
    routes = {'schema': 'qto-drawn-route-request/1.0', 'drawing_id': 'SYN', 'layout': 'Model', 'unit': req['unit'],
              'route_layers': '^WIRE-照明$', 'endpoint_tolerance': 0.01,
              'routes': [{'route_id': 'E1', 'from': {'point': [0.0, 0.0]}, 'to': {'point': [6.0, 0.0]}},
                         {'route_id': 'E2', 'from': {'point': [0.0, 5.0]}, 'to': {'point': [6.0, 5.0]}}]}
    rp = tmp_path / 'route.json'; rp.write_text(json.dumps(routes, ensure_ascii=False), encoding='utf-8')
    done = run(ROUTES / 'trace_drawn_routes.py', '--cad-manifest', manifest, '--request', rp, '--output-dir', tmp_path / 'route')
    traced = json.loads((tmp_path / 'route' / '走线追踪结果.json').read_text(encoding='utf-8'))
    e1, e2 = traced['routes']
    assert e1['status'] == '已追踪' and e1['length_m'] == pytest.approx(6.0) and not e1['review_flags'], done.stdout
    assert e2['status'] == '已追踪' and e2['length_m'] == pytest.approx(6.0)
    assert [f['flag'] for f in e2['review_flags']] == ['斜线按平面投影计长'] and e2['review_flags'][0]['handles'] == [sloped]
    assert traced['graph']['elevations_flattened'] == {'7710': 1, '0.004': 1}
    assert [w['warning'] for w in traced['warnings']] == ['sloped_lines_measured_in_plan']


@pytest.mark.parametrize('bad, message', [
    (dict(exclude_sheet_kinds=['平面']), 'exclude_sheet_kinds'),
    (dict(endpoints={'instances': {'path': 'dispositions.jsonl'}, 'classes': CLASSES}), 'endpoints.radius'),
    (dict(endpoints={'instances': {'path': 'dispositions.jsonl'}, 'classes': [{'rule_ids': ['X']}], 'radius': 1}), 'class'),
    (dict(endpoints={'instances': {'path': 'missing.jsonl'}, 'classes': CLASSES, 'radius': 1}), 'missing.jsonl'),
    (dict(regions=[{'region_id': 'X', 'polygon': [[0, 0], [1, 1]]}]), 'polygon'),
    (dict(feeders={'device_classes': ['插座箱']}, endpoints=None), 'feeders'),
])
def test_bad_layer_requests_are_rejected(layer, bad, message):
    h, done, result, comps, todo, out, tmp, manifest = layer
    req = request(**bad)
    if req.get('endpoints') is None:
        req.pop('endpoints')
    sub = tmp / f'bad_{abs(hash(message)) % 10000}'
    sub.mkdir(exist_ok=True)
    for name in ('sheets.csv', 'dispositions.jsonl'):
        (sub / name).write_bytes((tmp / name).read_bytes())
    done, _ = measure(sub, manifest, req)
    assert done.returncode == 2 and message in done.stdout, done.stdout
