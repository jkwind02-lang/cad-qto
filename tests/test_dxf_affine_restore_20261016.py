"""Original block geometry follows the full cumulative matrix; independent 2-D formula oracle."""
import copy
import hashlib
import math
from collections import Counter
from pathlib import Path
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/_qto-shared-evidence/scripts'), str(ROOT / 'tests')]
import qto_dxf_background as bg
import qto_review_contract as contract
import review_comparison_fixture as fixture

# Innermost first: insert, x/y scales, rotation, child's definition base point.
CHAINS = {
    'shear': [((300, 400), 1, 1, 25, (0, 0)), ((2000, 3000), 2, .75, 33, (0, 0))],
    'uniform_control': [((300, 400), 1, 1, 25, (0, 0)), ((2000, 3000), 2, 2, 33, (0, 0))],
    'no_rotation_control': [((300, 400), 1, 1, 0, (0, 0)), ((2000, 3000), 2, .75, 33, (0, 0))],
    'mirror_uniform_control': [((300, 400), 1, 1, 25, (0, 0)), ((2000, 3000), -2, 2, 33, (0, 0))],
    'right_angle_control': [((300, 400), 1, 1, 90, (0, 0)), ((2000, 3000), 2, .75, 33, (0, 0))],
    'mirror_shear': [((300, 400), -1, .8, 25, (0, 0)), ((2000, 3000), 2, -.75, 33, (0, 0))],
    'three_bases': [((300, 400), 1, 1, 25, (17, -9)), ((100, -50), 1.2, .8, 17, (31, 12)), ((2000, 3000), 2, .75, 33, (-21, 33))],
}


def move(p, chain):
    x, y = p[:2]
    for (ix, iy), sx, sy, rot, (bx, by) in chain:
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        x, y = sx * (x - bx), sy * (y - by)
        x, y = ix + c * x - s * y, iy + s * x + c * y
    return x, y


def unmove(p, chain):
    x, y = p[:2]
    for (ix, iy), sx, sy, rot, (bx, by) in reversed(chain):
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        x, y = x - ix, y - iy
        x, y = (c * x + s * y) / sx + bx, (-s * x + c * y) / sy + by
    return x, y


def vector(v, chain):
    a, b = move((0, 0), chain), move(v, chain)
    return [b[0] - a[0], b[1] - a[1]]


def drawing(chain, anonymous=False, attached=False):
    doc = ezdxf.new('R2010')
    leaf = doc.blocks.new('*U40' if anonymous else 'LEAF', base_point=(*chain[0][4], 0))
    inner = leaf.name
    for i, (p, sx, sy, rot, _) in enumerate(chain[:-1]):
        parent = doc.blocks.new('LEVEL' + str(i), base_point=(*chain[i + 1][4], 0))
        ref = parent.add_blockref(inner, p, dxfattribs={'xscale': sx, 'yscale': sy, 'rotation': rot})
        if attached and i == 0:
            ref.add_attrib('TAG', 'ATTR', (10, 150), dxfattribs={'height': 20, 'layer': 'ATTR'})
        inner = parent.name
    p, sx, sy, rot, _ = chain[-1]
    root = doc.modelspace().add_blockref(inner, p, dxfattribs={'xscale': sx, 'yscale': sy, 'rotation': rot})
    return doc, leaf, root


def output(tmp_path, doc):
    p = tmp_path / 'affine.dxf'
    doc.saveas(p)
    dw = bg._Drawing(p)
    w = bg._Walker(dw, {**bg.DEFAULTS, 'large_block_layers': bg.LARGE_BLOCK_LAYERS, 'flatten_mm': .01}, set(), 1)
    _, items, skipped, _ = w.run(next(iter(dw.msp)))
    return items, skipped


@pytest.mark.parametrize('name', sorted(CHAINS))
def test_original_geometry_follows_cumulative_affine(tmp_path, name):
    chain = CHAINS[name]
    doc, leaf, _ = drawing(chain, anonymous=name == 'mirror_shear')
    for layer in ('LINE', 'PLINE', 'CIRCLE', 'ARC', 'SPLINE', 'SOLID'):
        doc.layers.add(layer)
    leaf.add_line((0, 0), (300, 0), dxfattribs={'layer': 'LINE'})
    leaf.add_lwpolyline([(0, 200), (100, 150), (300, 200)], dxfattribs={'layer': 'PLINE'})
    leaf.add_circle((150, 100), 50, dxfattribs={'layer': 'CIRCLE'})
    leaf.add_arc((150, 100), 80, 0, 90, dxfattribs={'layer': 'ARC'})
    leaf.add_spline(fit_points=[(0, 30), (100, 80), (300, 30)], dxfattribs={'layer': 'SPLINE'})
    leaf.add_solid([(0, 0), (20, 0), (0, 20)], dxfattribs={'layer': 'SOLID'})
    items, skipped = output(tmp_path, doc)
    assert not skipped
    by_layer = {layer: item for layer, item in items}
    assert by_layer.keys() == {'LINE', 'PLINE', 'CIRCLE', 'ARC', 'SPLINE', 'SOLID'}
    assert max(math.dist(p, move(q, chain)) for p, q in zip(by_layer['LINE']['points'], [(0, 0), (300, 0)])) < 1e-7
    assert max(math.dist(p, move(q, chain)) for p, q in zip(by_layer['PLINE']['points'], [(0, 200), (100, 150), (300, 200)])) < 1e-7
    for layer, radius in [('CIRCLE', 50), ('ARC', 80)]:
        pts = [unmove(p, chain) for p in by_layer[layer]['points']]
        assert max(abs(math.hypot(p[0] - 150, p[1] - 100) - radius) for p in pts) < .05
    arc = by_layer['ARC']['points']
    assert math.dist(arc[0], move((230, 100), chain)) < 1e-7
    assert math.dist(arc[-1], move((150, 180), chain)) < 1e-7
    spline = by_layer['SPLINE']['points']
    assert math.dist(spline[0], move((0, 30), chain)) < 1e-7
    assert math.dist(spline[-1], move((300, 30), chain)) < 1e-7
    assert by_layer['SOLID']['kind'] == 'polygon'
    assert all(min(math.dist(p, move(q, chain)) for p in by_layer['SOLID']['points']) < 1e-7 for q in [(0, 0), (20, 0), (0, 20)])


@pytest.mark.parametrize('kind', ['TEXT', 'MTEXT'])
def test_sheared_text_has_original_anchor_and_full_axes(tmp_path, kind):
    chain = CHAINS['shear']; doc, leaf, _ = drawing(chain)
    doc.layers.add('TEXT')
    if kind == 'TEXT':
        leaf.add_text('AB', dxfattribs={'insert': (10, 150), 'height': 20, 'rotation': 17, 'width': 1.5, 'oblique': 12, 'layer': 'TEXT'})
    else:
        leaf.add_mtext('AB\\PCD', dxfattribs={'insert': (10, 150), 'char_height': 20, 'rotation': 17, 'layer': 'TEXT'})
    items, skipped = output(tmp_path, doc)
    assert not skipped
    item = items[0][1]
    assert math.dist(item['point'], move((10, 150), chain)) < 1e-7
    a = math.radians(17); c, s = math.cos(a), math.sin(a)
    width, skew = (1.5, math.tan(math.radians(12))) if kind == 'TEXT' else (1, 0)
    want = [vector((width * c, width * s), chain), vector((skew * c - s, skew * s + c), chain)]
    assert all(math.dist(g, w) < 1e-10 for g, w in zip(item['text_axes'], want))
    assert item['height'] == 20
    if kind == 'MTEXT':
        assert len(items) == 2
        second = (10 + math.sin(a) * 20 * 1.667, 150 - math.cos(a) * 20 * 1.667)
        assert math.dist(items[1][1]['point'], move(second, chain)) < 1e-7


def test_attached_attribute_uses_original_parent_coordinates(tmp_path):
    chain = CHAINS['three_bases']; doc, _, _ = drawing(chain, attached=True)
    doc.layers.add('ATTR')
    items, skipped = output(tmp_path, doc)
    assert not skipped
    item = next(it for layer, it in items if layer == 'ATTR')
    assert math.dist(item['point'], move((10, 150), chain[1:])) < 1e-7
    assert all(math.dist(g, w) < 1e-10 for g, w in zip(item['text_axes'], [vector((1, 0), chain[1:]), vector((0, 1), chain[1:])]))


def test_sheet_prefilter_does_not_use_lossy_nested_bbox(tmp_path):
    chain = CHAINS['shear']; doc, leaf, _ = drawing(chain)
    leaf.add_line((0, 200), (300, 200))
    p = tmp_path / 'crop.dxf'; doc.saveas(p)
    x, y = move((0, 200), chain)
    target = {'scene_id': 'S', 'sheet_bounds': [x - .1, y - .1, x + .1, y + .1], 'dx': 0, 'dy': 0, 'cfg': {**bg.DEFAULTS, 'large_block_layers': bg.LARGE_BLOCK_LAYERS}, 'include': [], 'exclude': [], 'symbol_layers': [], 'items': [], 'stats': Counter()}
    bg.build_dxf_backgrounds(p, hashlib.sha256(p.read_bytes()).hexdigest(), [target], set(), 1, 6)
    assert len(target['items']) == 1
    assert math.dist(target['items'][0]['points'][0], (x, y)) < 1e-6


def test_text_axes_are_checked_and_survive_payload_roundtrip():
    v = fixture.view(); s = v['scenes'][0]
    item = {'kind': 'text', 'point': [0, 0], 'text': '斜切字', 'height': 20, 'text_axes': [[1.2, .3], [.4, .8]]}
    s['background'].append(item)
    contract.normalize_view(copy.deepcopy(v))
    raw = contract.pack_payload(v)
    assert contract.unpack_payload(raw) == v
    decoded = contract.page_decode(raw, ROOT / 'skills/_qto-shared-evidence/assets/review/review-core.js')
    if decoded is not None:
        assert decoded == v
    for axes in ([[1, 0], [2, 0]], [[1, 0]], [[True, 0], [0, 1]], [[1, 0], [0, math.inf]]):
        bad = copy.deepcopy(v); bad['scenes'][0]['background'][-1]['text_axes'] = axes
        with pytest.raises(ValueError, match='文字变换'):
            contract.normalize_view(bad)


@pytest.mark.parametrize('flags', [2, 4, 6])
def test_text_generation_mirrors_keep_both_axes(tmp_path, flags):
    chain = CHAINS['mirror_shear']; doc, leaf, _ = drawing(chain)
    leaf.add_text('镜像', dxfattribs={'insert': (10, 150), 'height': 20, 'text_generation_flag': flags})
    items, skipped = output(tmp_path, doc)
    assert not skipped
    axes = items[0][1]['text_axes']
    want = [vector((-1 if flags & 2 else 1, 0), chain), vector((0, -1 if flags & 4 else 1), chain)]
    assert all(math.dist(g, w) < 1e-10 for g, w in zip(axes, want))


def test_affine_layer_inheritance_hidden_and_large_filter(tmp_path):
    chain = CHAINS['shear']; doc, leaf, root = drawing(chain)
    for name in ['WALL', 'PIPE', 'OFF']:
        doc.layers.add(name)
    doc.layers.get('OFF').off(); root.dxf.layer = 'WALL'
    leaf.add_line((0, 0), (300, 0))
    leaf.add_line((0, 10), (300, 10), dxfattribs={'layer': 'PIPE'})
    leaf.add_text('隐藏', dxfattribs={'insert': (0, 20), 'height': 20, 'layer': 'OFF'})
    p = tmp_path / 'layers.dxf'; doc.saveas(p)
    dw = bg._Drawing(p); e = next(iter(dw.msp))
    before = tuple(e.matrix44())
    w = bg._Walker(dw, {**bg.DEFAULTS, 'large_block_points': 1, 'large_block_layers': ['WALL']}, set(), 1)
    cls, items, skipped, dropped = w.run(e)
    assert cls == 'large' and len(items) == 1 and items[0][0] == 'WALL'
    assert dropped == {'PIPE': 1}
    assert skipped[0][0] == 'other_layers'
    assert math.dist(skipped[0][1], move((0, 10), chain)) < 1e-7
    assert before == tuple(e.matrix44()) and w.run(e)[1] == items
    # A counted symbol expands fully, but an off layer remains off.
    w.handles.add(str(e.dxf.handle).upper())
    cls, items, skipped, dropped = w.run(e)
    assert cls == 'object_symbol' and len(items) == 2 and not skipped and not dropped


def test_edge_on_text_is_counted_as_not_shown(tmp_path):
    doc, leaf, _ = drawing(CHAINS['shear'])
    leaf.add_text('投影退化', dxfattribs={'height': 20, 'extrusion': (0, 1, 0)})
    items, skipped = output(tmp_path, doc)
    assert not items and len(skipped) == 1 and skipped[0][0] == 'proxy'
