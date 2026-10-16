"""符号对账报出块内符号（登记 20261016-1403，第四批乙 d3）：楼层整层画在块里时，块里的计数符号至少要报个数，不再显示全 0。

DXF 和表格全部是合成的。只验证块内计数、逐级坐标、已匹配符号不重复数、隐藏层、无块内符号时输出不变、契约校验。
"""
import copy, hashlib, json, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_review_scene_mapping_20261016 as sm  # noqa: E402

c = sm.c


def make_floor_dxf(path, top_lamp=False):
    """FLOOR (on WALL) holds four LAMPs on E-LIGHT, one LAMP on a switched-off layer and a GROUP (on WALL) holding one more LAMP.
    Each LAMP holds a DOT on layer 0, which inherits E-LIGHT. FLOOR is inserted on P1 as is, on P2 turned 90 degrees, on P3 on a hidden layer."""
    import ezdxf
    doc = ezdxf.new('R2010', setup=True); msp = doc.modelspace()
    doc.layers.add('E-LIGHT', color=3); doc.layers.add('WALL', color=7); doc.layers.add('HIDDEN').off()
    dot = doc.blocks.new('DOT'); dot.add_circle((0, 0), 20)
    lamp = doc.blocks.new('LAMP'); lamp.add_circle((0, 0), 200); lamp.add_blockref('DOT', (0, 0))
    group = doc.blocks.new('GROUP'); group.add_line((0, 0), (300, 0), dxfattribs={'layer': 'WALL'})
    inner = group.add_blockref('LAMP', (100, 200), dxfattribs={'layer': 'E-LIGHT'})
    floor = doc.blocks.new('FLOOR'); floor.add_line((0, 0), (9000, 0), dxfattribs={'layer': 'WALL'})
    nested = [floor.add_blockref('LAMP', (x, 1000), dxfattribs={'layer': 'E-LIGHT'}).dxf.handle for x in (1000, 2000, 3000, 4000)]
    floor.add_blockref('LAMP', (4500, 1000), dxfattribs={'layer': 'HIDDEN'})
    floor.add_blockref('GROUP', (5000, 1000), dxfattribs={'layer': 'WALL'})
    msp.add_blockref('FLOOR', (0, 0), dxfattribs={'layer': 'WALL'})
    msp.add_blockref('FLOOR', (25000, 1000), dxfattribs={'layer': 'WALL', 'rotation': 90})
    msp.add_blockref('FLOOR', (0, 20000), dxfattribs={'layer': 'HIDDEN'})
    handles = {'N1': nested[0], 'N2': nested[1], 'G1': inner.dxf.handle}
    if top_lamp: handles['T1'] = msp.add_blockref('LAMP', (8000, 7000), dxfattribs={'layer': 'E-LIGHT'}).dxf.handle
    doc.saveas(path)
    return handles


XY = {'N1': ('1000', '1000'), 'N2': ('2000', '1000'), 'G1': ('5100', '1200'), 'T1': ('8000', '7000')}
P1_POINTS = [[1000, 1000], [2000, 1000], [3000, 1000], [4000, 1000], [5100, 1200]]
# FLOOR turned 90 degrees at (25000, 1000): (x, y) -> (25000 - y, 1000 + x).
P2_WORLD = [[24000, 2000], [24000, 3000], [24000, 4000], [24000, 5000], [23800, 6100]]


def convert(tmp_path, background, lamps=(), top_lamp=False, dxf=make_floor_dxf):
    nroot = sm.neutral(tmp_path); folder = nroot / 'D1'
    handles = dxf(folder / 'D1.dxf', top_lamp) if dxf is make_floor_dxf else dxf(folder / 'D1.dxf')
    mf = json.loads((folder / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    mf['exchange_dxf'] = {'path': 'D1.dxf', 'sha256': hashlib.sha256((folder / 'D1.dxf').read_bytes()).hexdigest()}
    (folder / 'CAD中性取证清单.json').write_text(json.dumps(mf, ensure_ascii=False), encoding='utf-8')
    rows = [[n, 'D1', 'P1', handles[n], *XY[n]] for n in lamps] or [['P-0', 'D1', 'P1', '', '3000', '3000']]
    rows += [['P-2', 'D1', 'P2', '', '21000', '1000'], ['P-3', 'D1', 'P3', '', '1000', '21000']]   # a scene exists only where an object is
    sm.write_csv(tmp_path / 'tables' / 'lamps.csv', ['编号', '图纸', '图页', '句柄', 'x', 'y'], rows)
    p, m = sm.mapping(tmp_path, background=background)
    m['objects'] = [{'key': 'lamp', 'path': 'tables/lamps.csv', 'columns': {'object_id': '编号', 'drawing': '图纸', 'sheet': '图页', 'x': 'x', 'y': 'y', 'handle': '句柄'},
                     'constants': {'title': '灯', 'category': '灯具', 'status': '待复核'}}]
    p.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    out, rep = tmp_path / 'view.json', tmp_path / 'report.json'
    code, _, err = sm.run(sm.SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', out, '--report', rep)
    assert rep.exists(), err
    report = json.loads(rep.read_text(encoding='utf-8'))
    assert code == 0, report.get('problems')
    view = json.loads(out.read_text(encoding='utf-8'))
    return report, view, out


def scene(view, sid): return next(s for s in view['scenes'] if s['id'] == sid)
def symbols(report, sid): return next(s for s in report['scenes'] if s['id'] == sid)['symbols']


BG_LAYER = {'large_block_points': 30, 'symbol_layers': ['^E-LIGHT$']}
BG = {'large_block_points': 30}


@pytest.mark.parametrize('how', ['symbol_layers', 'counted_handles'])
def test_floor_block_symbols_are_counted_not_zero(tmp_path, how):
    # Counter-example from the register: 5 counted symbols inside the floor block, none at top level -> in_block_not_reconciled = 5.
    report, view, _ = convert(tmp_path, BG_LAYER if how == 'symbol_layers' else BG, lamps=() if how == 'symbol_layers' else ('N1', 'N2', 'G1'))
    c.normalize_view(copy.deepcopy(view))
    s1 = scene(view, 'D1|P1')
    assert 'symbol_reconciliation' not in s1                      # FLOOR itself is neither counted nor on a symbol layer
    assert s1['symbol_in_block'] == {'total': 5, 'blocks': [{'block': 'LAMP', 'count': 5, 'points': P1_POINTS}]}
    assert symbols(report, 'D1|P1') == {'blocks': 0, 'on_sheet': 0, 'counted': 0, 'uncounted': 0, 'duplicates': 0, 'in_block_not_reconciled': 5}
    # The turned copy: every level of the matrix applies, so the points follow the rotation (scene shift is one constant vector).
    pts = scene(view, 'D1|P2')['symbol_in_block']['blocks'][0]['points']
    shift = [pts[0][0] - P2_WORLD[0][0], pts[0][1] - P2_WORLD[0][1]]
    assert pts == [[x + shift[0], y + shift[1]] for x, y in P2_WORLD]
    # FLOOR on a switched-off layer is not drawn: nothing inside it is counted either.
    assert 'symbol_in_block' not in scene(view, 'D1|P3') and 'in_block_not_reconciled' not in symbols(report, 'D1|P3')


def test_symbols_inside_a_matched_symbol_are_not_counted_again(tmp_path):
    # DOT sits inside every LAMP on layer 0 and so inherits E-LIGHT; it is part of the lamp, not a second symbol.
    report, view, _ = convert(tmp_path, BG_LAYER, top_lamp=True)
    s1 = scene(view, 'D1|P1')
    assert [b['block'] for b in s1['symbol_in_block']['blocks']] == ['LAMP'] and s1['symbol_in_block']['total'] == 5
    # The top-level lamp is reconciled as before; its DOT is not an in-block symbol.
    rows = {r['block']: r for r in s1['symbol_reconciliation']}
    assert set(rows) == {'LAMP'} and rows['LAMP']['on_sheet'] == 1 and rows['LAMP']['uncounted'] == 1
    assert symbols(report, 'D1|P1')['in_block_not_reconciled'] == 5


def test_non_matching_nested_blocks_are_not_counted(tmp_path):
    # No symbol layers and no counted handles: nothing can be a symbol, so no field is written.
    report, view, _ = convert(tmp_path, BG)
    assert all('symbol_in_block' not in s for s in view['scenes'])
    assert all('in_block_not_reconciled' not in s['symbols'] for s in report['scenes'])


def test_without_in_block_symbols_the_output_is_unchanged(tmp_path):
    # Legal control: the existing fixture has symbols only at top level; neither the view nor the report gets a new field.
    code, report, v, out = sm.dxf_convert(tmp_path / 'a', background={'large_block_points': 30, 'symbol_layers': ['^E-LIGHT$']})
    assert code == 0, report
    assert all('symbol_in_block' not in s for s in v['scenes']) and all('in_block_not_reconciled' not in s['symbols'] for s in report['scenes'])
    assert 'symbol_in_block' not in out.read_text(encoding='utf-8')


def test_page_shows_in_block_symbols():
    js = (sm.SCRIPTS.parent / 'assets' / 'review' / 'review-app.js').read_text(encoding='utf-8')
    assert "scene?.symbol_in_block" in js and "'块内 '+ib.total+' 个符号未参与对账" in js and '$(\'symbols\').hidden=!rows.length&&!ib' in js


def test_contract_checks_in_block_symbols():
    v = sm.fx.view(); del v['comparison']; v['schema'] = 'qto-review-view/1.0'
    v['scenes'][0]['symbol_in_block'] = {'total': 3, 'blocks': [{'block': 'LAMP', 'count': 2, 'points': [[1, 1], [2, 2]]},
                                                               {'block': 'TB', 'count': 1, 'points': [[3, 3]], 'sheet': 'P1'}]}
    c.normalize_view(copy.deepcopy(v))
    bad = [lambda ib: ib.update(total=4),
           lambda ib: ib.update(blocks=[]),
           lambda ib: ib['blocks'][0].update(count=3),
           lambda ib: ib['blocks'][0].update(count=0, points=[]),
           lambda ib: ib['blocks'][0].update(count=True),
           lambda ib: ib['blocks'][0].update(points=[[1, 1], [2]]),
           lambda ib: ib['blocks'][0].update(block=' '),
           lambda ib: ib['blocks'][1].update(sheet=''),
           lambda ib: ib.update(blocks='LAMP')]
    for change in bad:
        w = copy.deepcopy(v); change(w['scenes'][0]['symbol_in_block'])
        with pytest.raises(ValueError, match='块内未对账符号'): c.normalize_view(w)
    w = copy.deepcopy(v); w['scenes'][0]['symbol_in_block'] = [1]
    with pytest.raises(ValueError, match='块内未对账符号'): c.normalize_view(w)
