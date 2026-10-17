"""第五批 L4：同层共线重合（登记 20261016-1044 R4、20261015-1535，给排水样例 20261017-0133）。

合成图（毫米，输出米）。重合检查对两种请求都做，只出候选，项目逐对写 overlap_resolutions 才扣。边界清单（备课第二节 L4）：
- 候选 = 同图层（或 overlaps.layer_groups 同组）、平面共线（夹角 ≤ angle_tolerance_deg 默认 0.5°，重合区间两端的垂距
  ≤ distance_tolerance 默认 = 并点容差）、投影重合 > min_overlap（默认 0.01 m）的直线对；局部重描（A，0.8 m）和整条重复
  （B、E1/E3）都算；首尾相接（重合 0）、重合 5 mm、平行相距 50 mm、夹角 0.86° 都不算；垂距 8 mm、夹角 0.29° 算；
- 不同图层不比（C），写进同一组才比；原始标高不同（D，z 0 与 3000）或有斜线（S）的对单列 z_differs，不合并、不许写处置；
- 处置一对只许一条；不是候选的对、标高不同的对写处置都报错；drop 须为这对里的一段，basis 必填；
- 按图层量长：drop_overlap 只扣被剔段的重合区间（段仍在图里连通），keep_both 两段都计，pending 与未处置一样列待办，
  checks.overlaps_resolved 为假；
- 路线追踪：被剔段整条落在另一段上 → 不进图（整条复制不再出"多条等长路径"，回头重描后 T 接接上）；局部的被剔段留在图里，
  路线两段都走过的部分扣一次；路线两段都走过而这对没处置 → 复核"经过未处置的共线重合"；keep_both 不扣。
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
LIGHT, POWER = {'layer': 'WIRE-照明'}, {'layer': 'WIRE-动力'}


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def draw():
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.layers.add('WIRE-照明'); doc.layers.add('WIRE-动力')
    doc.blocks.new('BOX').add_lwpolyline([(-300, -200), (300, -200), (300, 200), (-300, 200)], close=True)
    doc.blocks.new('DEV').add_lwpolyline([(-100, -100), (100, -100), (100, 100), (-100, 100)], close=True)
    msp, h = doc.modelspace(), {}

    def line(key, a, b, layer=LIGHT):
        h[key] = msp.add_line(a, b, dxfattribs=layer).dxf.handle
    line('A1', (0, 0), (10000, 0)); line('A2', (9200, 0), (20000, 0))                     # local redraw 0.8 m
    line('B1', (0, 8000), (6000, 8000)); line('B2', (6000, 8000), (0, 8000))              # whole copy, reversed
    h['BOX_B'] = msp.add_blockref('BOX', (-300, 8000)).dxf.handle; h['DEV_B'] = msp.add_blockref('DEV', (6100, 8000)).dxf.handle
    line('C1', (0, 12000), (5000, 12000)); line('C2', (0, 12000), (5000, 12000), POWER)   # another layer
    line('D1', (0, 16000, 0), (5000, 16000, 0)); line('D2', (0, 16000, 3000), (5000, 16000, 3000))   # another elevation
    line('E1', (0, 20000), (5000, 20000)); line('E2', (0, 20050), (5000, 20050))          # 50 mm apart: not one line
    line('E3', (0, 20008), (5000, 20008))                                                 # 8 mm: within tolerance
    line('F1', (0, 24000), (5000, 24000)); line('F2', (5000, 24000), (9000, 24000))       # end to end
    line('F3', (8995, 24000), (12000, 24000))                                             # 5 mm overlap: below min
    line('G1', (0, 28000), (10000, 28000)); line('G2', (0, 28000), (10000, 28150))        # 0.86 degrees: not one line
    line('G3', (0, 28000), (1000, 28005))                                                 # 0.29 degrees, 5 mm off
    line('S1', (0, 32000, 0), (5000, 32000, 0)); line('S2', (0, 32000, 0), (5000, 32000, 3000))   # a sloped line
    line('P1', (0, 40000), (10000, 40000))                                                # P2 runs back over P1's end
    h['P2'] = msp.add_lwpolyline([(10000, 40000), (9200, 40000), (9200, 45000)], dxfattribs=LIGHT).dxf.handle
    h['BOX_P'] = msp.add_blockref('BOX', (-300, 40000)).dxf.handle; h['DEV_P'] = msp.add_blockref('DEV', (9200, 45100)).dxf.handle
    return doc, h


@pytest.fixture(scope='module')
def drawing(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('l4')
    doc, h = draw()
    dxf = tmp / 'overlap.dxf'; doc.saveas(dxf)
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp / 'neutral')
    assert done.returncode == 0, done.stdout + done.stderr
    with (tmp / 'sheets.csv').open('w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f).writerows([['图页编号', '类型', 'x1', 'y1', 'x2', 'y2'], ['P1', '平面', -1000, -1000, 60000, 60000]])
    return tmp, tmp / 'neutral' / 'CAD中性取证清单.json', h


def seg(h, key, part=0):
    return f'SEG-CAD-{h[key]}:{part}'


def pair(h, a, b, action, drop=None, basis='核图：同一根线重描'):
    item = {'segment_ids': [seg(h, *a) if isinstance(a, tuple) else seg(h, a), seg(h, *b) if isinstance(b, tuple) else seg(h, b)],
            'action': action, 'basis': basis}
    if drop:
        item['drop'] = seg(h, *drop) if isinstance(drop, tuple) else seg(h, drop)
    return item


def measure(drawing, name, **extra):
    tmp, manifest, _ = drawing
    req = {'schema': 'qto-layer-length-request/1.0', 'drawing_id': 'SYN', 'layout': 'Model',
           'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
           'route_layers': '^WIRE-(照明|动力)$', 'snap_tolerance': 0.01, 'gap_tolerance': 0.0,
           'sheets': [{'path': str(tmp / 'sheets.csv'), 'constants': {'drawing': 'SYN'},
                       'columns': {'sheet_id': '图页编号', 'sheet_kind': '类型', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}], **extra}
    rp = tmp / f'{name}.json'; rp.write_text(json.dumps(req, ensure_ascii=False), encoding='utf-8')
    out = tmp / name
    done = run(ROUTES / 'trace_drawn_routes.py', '--cad-manifest', manifest, '--request', rp, '--output-dir', out)
    if done.returncode == 2:
        return done, None
    assert done.returncode in (0, 3), done.stdout + done.stderr
    result = json.loads((out / '按图层量长结果.json').read_text(encoding='utf-8'))
    with (out / '按图层量长共线重合.csv').open(encoding='utf-8-sig', newline='') as f:
        result['_csv'] = list(csv.DictReader(f))
    return done, result


def trace(drawing, name, **extra):
    tmp, manifest, h = drawing
    req = {'schema': 'qto-drawn-route-request/1.0', 'drawing_id': 'SYN', 'layout': 'Model',
           'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
           'route_layers': '^WIRE-照明$', 'box_block_pattern': '^BOX$', 'snap_tolerance': 0.01, 'gap_tolerance': 0.0,
           'endpoint_tolerance': 0.3, 'routes': [
               {'route_id': 'B', 'from': {'instance_id': f'PT-CAD-{h["BOX_B"]}'}, 'to': {'instance_id': f'PT-CAD-{h["DEV_B"]}'}},
               {'route_id': 'P', 'from': {'instance_id': f'PT-CAD-{h["BOX_P"]}'}, 'to': {'instance_id': f'PT-CAD-{h["DEV_P"]}'}}], **extra}
    rp = tmp / f'{name}.json'; rp.write_text(json.dumps(req, ensure_ascii=False), encoding='utf-8')
    out = tmp / name
    done = run(ROUTES / 'trace_drawn_routes.py', '--cad-manifest', manifest, '--request', rp, '--output-dir', out)
    assert done.returncode in (0, 3), done.stdout + done.stderr
    result = json.loads((out / '走线追踪结果.json').read_text(encoding='utf-8'))
    return result, {r['route_id']: r for r in result['routes']}


def keys(h, rows):
    back = {v: k for k, v in h.items()}
    return sorted(tuple(sorted(f'{back[x]}:{i}' if back[x] == 'P2' else back[x] for x, i in zip(r['handles'], r['part_indices']))) for r in rows)


def test_candidates_are_one_line_on_one_layer_sharing_more_than_the_minimum(drawing):
    _, _, h = drawing
    done, result = measure(drawing, 'open')
    ov = result['overlaps']
    assert keys(h, ov['candidates']) == [('A1', 'A2'), ('B1', 'B2'), ('E1', 'E3'), ('G1', 'G3'), ('P1', 'P2:0')]
    by = {tuple(sorted(r['handles'])): r for r in ov['candidates']}
    a = by[tuple(sorted((h['A1'], h['A2'])))]
    assert a['kind'] == '局部重复' and a['overlap_m'] == pytest.approx(0.8) and a['status'] == '未处置'
    assert sorted([a['from_point_m'][0], a['to_point_m'][0]]) == pytest.approx([9.2, 10.0])
    assert by[tuple(sorted((h['B1'], h['B2'])))]['kind'] == '整条重复'
    assert by[tuple(sorted((h['E1'], h['E3'])))]['overlap_m'] == pytest.approx(5.0)
    assert by[tuple(sorted((h['G1'], h['G3'])))]['overlap_m'] == pytest.approx(1.0)
    p = by[tuple(sorted((h['P1'], h['P2'])))]
    assert p['kind'] == '整条重复' and p['overlap_m'] == pytest.approx(0.8)
    assert [r['group_id'] for r in ov['candidates']] == [f'OV-{n:04d}' for n in range(1, 6)]
    # another elevation or a sloped line: listed apart, never a candidate
    assert keys(h, ov['z_differs']) == [('D1', 'D2'), ('S1', 'S2')]
    assert ov['open_count'] == 5 and ov['resolved'] is False and result['checks']['overlaps_resolved'] is False
    assert done.returncode == 3 and result['status'] == 'REVIEW_REQUIRED'
    warned = {w['warning']: w for w in result['warnings']}
    assert warned['collinear_overlaps_open']['count'] == 5 and warned['collinear_overlaps_at_different_z']['count'] == 2
    todo = [t for t in result['todo'] if t['problem'].startswith('共线重合')]
    assert sorted(t['problem'] for t in todo) == ['共线重合但标高不同'] * 2 + ['共线重合未处置'] * 5
    assert result['totals']['overlap_deducted_m'] == 0
    assert len(result['_csv']) == 7 and {r['status'] for r in result['_csv']} == {'未处置', '标高不同'}


def test_a_drop_takes_only_the_shared_interval_off_and_keep_both_counts_both(drawing):
    _, _, h = drawing
    _, before = measure(drawing, 'open2')
    _, after = measure(drawing, 'resolved', overlap_resolutions=[
        pair(h, 'A1', 'A2', 'drop_overlap', drop='A2'), pair(h, 'B1', 'B2', 'keep_both', basis='核图：两根线并行'),
        pair(h, 'E3', 'E1', 'drop_overlap', drop='E3'), pair(h, 'G1', 'G3', 'pending', basis='待设计确认'),
        pair(h, 'P1', ('P2', 0), 'drop_overlap', drop=('P2', 0))])
    assert before['totals']['counted_m'] - after['totals']['counted_m'] == pytest.approx(0.8 + 5.0 + 0.8)
    assert after['totals']['overlap_deducted_m'] == pytest.approx(6.6)
    status = {tuple(sorted(r['handles'])): r['status'] for r in after['overlaps']['candidates']}
    assert status[tuple(sorted((h['A1'], h['A2'])))] == '已扣重合' and status[tuple(sorted((h['B1'], h['B2'])))] == '两段都保留'
    assert status[tuple(sorted((h['G1'], h['G3'])))] == '暂缓'
    # the dropped segments stay in the graph: A is still one connected part 20 m long
    comps = [c for c in after['components'] if h['A1'] in c['handles']]
    assert len(comps) == 1 and h['A2'] in comps[0]['handles'] and comps[0]['length_m'] == pytest.approx(20.0)
    assert after['overlaps']['resolved'] is False and after['checks']['overlaps_resolved'] is False
    assert [t['problem'] for t in after['todo'] if t['problem'].startswith('共线重合') and '标高' not in t['problem']] == ['共线重合暂缓']
    assert after['checks']['class_total_equals_sheet_total']
    rows = {r['group_id']: r for r in after['_csv'] if r['group_id']}
    assert {r['action'] for r in rows.values()} == {'drop_overlap', 'keep_both', 'pending'}
    _, done = measure(drawing, 'all', overlap_resolutions=[
        pair(h, 'A1', 'A2', 'drop_overlap', drop='A2'), pair(h, 'B1', 'B2', 'keep_both'), pair(h, 'E1', 'E3', 'keep_both'),
        pair(h, 'G1', 'G3', 'keep_both'), pair(h, 'P1', ('P2', 0), 'drop_overlap', drop=('P2', 0))])
    assert done['overlaps']['resolved'] is True and done['checks']['overlaps_resolved'] is True


def test_layer_groups_compare_lines_on_the_layers_of_one_group(drawing):
    _, _, h = drawing
    _, result = measure(drawing, 'groups', overlaps={'layer_groups': ['^WIRE-(照明|动力)$']})
    assert ('C1', 'C2') in keys(h, result['overlaps']['candidates'])
    _, result = measure(drawing, 'tight', overlaps={'min_overlap': 0.9, 'distance_tolerance': 0.006, 'angle_tolerance_deg': 0.2})
    assert keys(h, result['overlaps']['candidates']) == [('B1', 'B2')]


@pytest.mark.parametrize('bad, message', [
    ('twice', '一组只许一条'), ('other_layer', '不是本次的共线重合候选'), ('other_z', '标高不同'), ('drop_outside', '.drop 须为'),
    ('no_basis', 'basis'), ('action', 'action 只能是'), ('one_id', 'segment_ids 须为两个不同的段号')])
def test_dispositions_are_checked(drawing, bad, message):
    _, _, h = drawing
    item = pair(h, 'A1', 'A2', 'drop_overlap', drop='A2')
    items = {'twice': [item, pair(h, 'A2', 'A1', 'keep_both')], 'other_layer': [pair(h, 'C1', 'C2', 'keep_both')],
             'other_z': [pair(h, 'D1', 'D2', 'drop_overlap', drop='D2')], 'drop_outside': [{**item, 'drop': seg(h, 'B1')}],
             'no_basis': [{k: v for k, v in item.items() if k != 'basis'}], 'action': [{**item, 'action': 'merge'}],
             'one_id': [{**item, 'segment_ids': [seg(h, 'A1')]}]}[bad]
    done, result = measure(drawing, f'bad_{bad}', overlap_resolutions=items)
    assert done.returncode == 2 and result is None
    assert message in json.loads(done.stdout)['error']


def test_routes_leave_a_covered_copy_out_and_take_a_walked_overlap_off_once(drawing):
    _, _, h = drawing
    result, routes = trace(drawing, 'route_open')
    assert routes['B']['status'] == '待办' and routes['B']['problems'][0]['problem'] == '多条等长路径'
    p = routes['P']
    assert p['status'] == '已追踪' and p['length_m'] == pytest.approx(15.8)
    flag = next(f for f in p['review_flags'] if f['flag'] == '经过未处置的共线重合')
    assert flag['walked_twice_m'] == pytest.approx(0.8) and p['overlap_deducted_m'] == 0
    assert result['overlaps']['open_count'] >= 2
    # the redraw that lies wholly on P1 is left out: P2's corner then sits on P1 and joins it as a T junction
    result, routes = trace(drawing, 'route_drop_copy', overlap_resolutions=[
        pair(h, 'B1', 'B2', 'drop_overlap', drop='B2'), pair(h, 'P1', ('P2', 0), 'drop_overlap', drop=('P2', 0))])
    assert routes['B']['status'] == '已追踪' and routes['B']['length_m'] == pytest.approx(6.0)
    assert routes['P']['length_m'] == pytest.approx(14.2) and len(routes['P']['t_junctions']) == 1
    assert result['overlaps']['left_out_of_graph'] == sorted([seg(h, 'B2'), seg(h, 'P2', 0)])
    assert not any(f['flag'] == '经过未处置的共线重合' for r in routes.values() for f in r['review_flags'])
    # dropping the overlap off the longer line keeps it in the graph; the part walked on both is taken off once
    _, routes = trace(drawing, 'route_drop_long', overlap_resolutions=[pair(h, 'P1', ('P2', 0), 'drop_overlap', drop='P1')])
    p = routes['P']
    assert p['length_m'] == pytest.approx(15.0) and p['overlap_deducted_m'] == pytest.approx(0.8)
    assert p['drawn_length_m'] == pytest.approx(math.fsum(s['length_m'] for s in p['segments']) - 0.8)
    assert p['overlaps_walked_twice'][0]['status'] == '已扣重合'
    _, routes = trace(drawing, 'route_keep', overlap_resolutions=[pair(h, 'P1', ('P2', 0), 'keep_both')])
    assert routes['P']['length_m'] == pytest.approx(15.8) and routes['P']['overlap_deducted_m'] == 0
    assert not any(f['flag'] == '经过未处置的共线重合' for f in routes['P']['review_flags'])
