"""1129（登记 20261016-1129）：普通路径镜像块内 TEXT/ATTRIB/MTEXT 的 OCS 锚点要换成 WCS 再画。

合成图：块 LEAF 内左对齐 TEXT、右对齐 TEXT、两行 MTEXT、一段直线；MID 嵌套 LEAF 并带一个挂属性的 SUB。
期望位置按插入参数自建矩阵（平移 × 旋转 × 缩放）算局部点，不用 ezdxf 炸开结果。
等比镜像不丢仿射，走普通路径（_walk）；非镜像对照的输出与修复前逐项相同。
"""
import math
import sys
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Matrix44

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
import qto_dxf_background as bg

TEXT_AT, RIGHT_AT, MTEXT_AT, LINE_AT = (100, 50), (400, 80), (100, -200), (100, 50)
SUB_AT, ATTRIB_OFFSET = (600, 300), (20, 10)
STEP = 10 * 1.667   # MTEXT line step used by the walker: height × 1.667 × line spacing


def matrix(sx, sy, rot, at):
    return Matrix44.chain(Matrix44.scale(sx, sy, 1), Matrix44.z_rotate(math.radians(rot)), Matrix44.translate(at[0], at[1], 0))


def build(tmp_path, top, mid=None, top_attrib=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2010'); doc.layers.add('T')
    leaf = doc.blocks.new('LEAF')
    leaf.add_text('左字', dxfattribs={'insert': TEXT_AT, 'height': 10, 'layer': 'T'})
    leaf.add_text('右字', dxfattribs={'height': 10, 'layer': 'T'}).set_placement(RIGHT_AT, align=ezdxf.enums.TextEntityAlignment.RIGHT)
    leaf.add_mtext('第一行\\P第二行', dxfattribs={'insert': MTEXT_AT, 'char_height': 10, 'attachment_point': 1, 'layer': 'T'})
    leaf.add_line(LINE_AT, (LINE_AT[0] + 50, LINE_AT[1]), dxfattribs={'layer': 'L'})
    sub = doc.blocks.new('SUB'); sub.add_attdef('BOX', (0, 0), dxfattribs={'height': 10})
    sub.add_line((0, 0), (5, 0))
    name = 'LEAF'
    if mid is not None:
        m = doc.blocks.new('MID')
        m.add_blockref('LEAF', (0, 0), dxfattribs={'xscale': mid[0], 'yscale': mid[1], 'rotation': mid[2]})
        ref = m.add_blockref('SUB', SUB_AT)
        ref.add_attrib('BOX', '01-F1-1A', (SUB_AT[0] + ATTRIB_OFFSET[0], SUB_AT[1] + ATTRIB_OFFSET[1]), dxfattribs={'height': 10})
        name = 'MID'
    if top_attrib:
        blk = doc.blocks.new('TAGGED'); blk.add_attdef('NO', (30, 40), dxfattribs={'height': 10}); blk.add_line((0, 0), (5, 0))
        name = 'TAGGED'
    ins = doc.modelspace().add_blockref(name, (10000, 20000), dxfattribs={'xscale': top[0], 'yscale': top[1], 'rotation': top[2]})
    if top_attrib:
        ins.add_auto_attribs({'NO': 'W-12'})
    p = tmp_path / 'mirror_text.dxf'; doc.saveas(p)
    return p


def walk(path):
    dw = bg._Drawing(path); e = next(iter(dw.msp))
    w = bg._Walker(dw, {**bg.DEFAULTS, 'flatten_mm': .01, 'large_block_layers': bg.LARGE_BLOCK_LAYERS},
                   {str(e.dxf.handle).upper()}, 1)
    assert not dw.affine_loss(e)   # uniform mirror keeps the ordinary path under test
    _, items, _, _ = w.run(e)
    return [it for _, it in items]


def texts(items):
    return {it['text']: it for it in items if it['kind'] == 'text'}


CASES = {'top_mirror_x': ((-1, 1, 0), None), 'top_mirror_y': ((1, -1, 0), None),
         'top_mirror_x_rot30': ((-1, 1, 30), None), 'nested_mirror_x': ((1, 1, 0), (-1, 1, 0)),
         'nested_mirror_uniform_rot40': ((-1.6, 1.6, 40), (1, 1, 25)), 'plain_rotated': ((2, 2, 35), None),
         'nested_plain': ((1.5, 1.5, -20), (1, 1, 60))}


@pytest.mark.parametrize('name', CASES)
def test_text_anchors_land_where_the_insert_puts_them(tmp_path, name):
    top, mid = CASES[name]
    m_top = matrix(top[0], top[1], top[2], (10000, 20000))
    m_leaf = Matrix44.chain(matrix(mid[0], mid[1], mid[2], (0, 0)), m_top) if mid else m_top
    got = texts(walk(build(tmp_path, top, mid)))
    want = {'左字': TEXT_AT, '右字': RIGHT_AT, '第一行': MTEXT_AT, '第二行': (MTEXT_AT[0], MTEXT_AT[1] - STEP)}
    for text, local in want.items():
        p = m_leaf.transform((*local, 0))
        assert math.dist(got[text]['point'], (p.x, p.y)) < 1e-6, (text, got[text]['point'], (p.x, p.y))
    if mid:
        p = m_top.transform((SUB_AT[0] + ATTRIB_OFFSET[0], SUB_AT[1] + ATTRIB_OFFSET[1], 0))
        assert math.dist(got['01-F1-1A']['point'], (p.x, p.y)) < 1e-6
    line = next(it for it in walk(build(tmp_path / 'again', top, mid)) if it['kind'] == 'line')
    p = m_leaf.transform((*LINE_AT, 0))
    assert math.dist(line['points'][0], (p.x, p.y)) < 1e-6


@pytest.mark.parametrize('name', ['top_mirror_x', 'nested_mirror_uniform_rot40'])
def test_mirrored_text_reading_direction_follows_the_mirror(tmp_path, name):
    top, mid = CASES[name]
    m_leaf = matrix(top[0], top[1], top[2], (10000, 20000))
    if mid:
        m_leaf = Matrix44.chain(matrix(mid[0], mid[1], mid[2], (0, 0)), m_leaf)
    t = texts(walk(build(tmp_path, top, mid)))['左字']
    base, up = m_leaf.transform_direction((1, 0, 0)), m_leaf.transform_direction((0, 1, 0))
    axes = t['text_axes']
    cos = lambda a, b: (a[0] * b.x + a[1] * b.y) / (math.hypot(*a) * math.hypot(b.x, b.y))
    assert cos(axes[0], base) > 0.999999 and cos(axes[1], up) > 0.999999


def test_attrib_on_mirrored_top_insert(tmp_path):
    top = (-1, 1, 0)
    path = build(tmp_path, top, top_attrib=True)
    doc = ezdxf.readfile(path); a = next(iter(next(iter(doc.modelspace())).attribs))
    p = matrix(*top, (10000, 20000)).transform((30, 40, 0))
    got = texts(walk(path))['W-12']
    assert math.dist(got['point'], (p.x, p.y)) < 1e-6, (list(a.dxf.extrusion), got['point'], (p.x, p.y))


def test_plain_text_output_is_unchanged_for_upright_inserts(tmp_path):
    """Legal contrast: +Z text keeps the old primitive exactly (no text_axes, original rotation)."""
    got = texts(walk(build(tmp_path, (2, 2, 35), None)))
    assert 'text_axes' not in got['左字'] and got['左字']['rotation'] == pytest.approx(35)
