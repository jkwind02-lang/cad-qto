"""Anonymous T15 examples: source B-spline + independently accumulated original INSERT matrices.

The oracle never explodes an INSERT or calls the walker's transform/flatten helpers.
MTEXT checks the documented approximate line layout, not native CAD glyph placement.
"""
import math
from pathlib import Path
import sys

import ezdxf
from ezdxf.math import Matrix44, Vec3
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
import qto_dxf_background as bg

CASES = {
    'single': [dict(xscale=2.1, yscale=.65, rotation=33)],
    'no_inner_rotation': [dict(rotation=0), dict(xscale=2.1, yscale=.65, rotation=33)],
    'nested_shear': [dict(rotation=27), dict(xscale=2.1, yscale=.65, rotation=33)],
    'nested_right_angle': [dict(rotation=90), dict(xscale=2.1, yscale=.65, rotation=33)],
    'mirror': [dict(xscale=-1, yscale=1, rotation=19), dict(xscale=2.1, yscale=.65, rotation=33)],
    'tilted_ocs': [dict(rotation=21, extrusion=(.1, .3, 1)), dict(xscale=2.1, yscale=.65, zscale=1.3, rotation=33, extrusion=(-.2, .4, 1))],
    'top_array': [dict(xscale=2.1, yscale=.65, rotation=33, row_count=2, column_count=3, row_spacing=170, column_spacing=310)],
    'nested_array': [dict(rotation=0, row_count=2, column_count=2, row_spacing=170, column_spacing=310), dict(xscale=-2.1, yscale=.65, rotation=33)],
    'double_array_shear': [dict(rotation=27, row_count=2, column_count=2, row_spacing=170, column_spacing=310), dict(xscale=2.1, yscale=.65, rotation=33, row_count=2, row_spacing=700)],
    'array_ocs': [dict(rotation=21, extrusion=(.1, .3, 1), row_count=2, column_count=2, row_spacing=170, column_spacing=310), dict(xscale=2.1, yscale=.65, rotation=33)],
    'array_zero_spacing': [dict(xscale=2.1, yscale=.65, rotation=33, row_count=3, column_count=2, row_spacing=0, column_spacing=310)],
    'uniform_control': [dict(rotation=0), dict(xscale=2, yscale=2, rotation=33)],
    'mirror_control': [dict(xscale=-2, yscale=2, rotation=33)],
    'cancelled_scale_control': [dict(xscale=.5, yscale=2), dict(xscale=2, yscale=.5, rotation=33)],
}
CONTROLS = {'uniform_control', 'mirror_control', 'cancelled_scale_control'}


def fixture(tmp_path, name, kind='both', attachment=1, tilted_text=False, spatial_spline=False):
    doc = ezdxf.new('R2010')
    leaf = doc.blocks.new('*U201', base_point=(17, -9, 3))
    spline = text = None
    if kind in ('both', 'spline'):
        fp = [(0, 30, 0), (70, 120, 20 if spatial_spline else 0), (190, -40, -10 if spatial_spline else 0), (260, 30, 0)]
        spline = leaf.add_spline(fit_points=fp, dxfattribs={'layer': 'SPLINE'})
    if kind in ('both', 'text'):
        text = leaf.add_mtext('ALPHA\\PBETA\\P\\PGAMMA', dxfattribs={'insert': (10, 150, 8), 'char_height': 20, 'attachment_point': attachment, 'line_spacing_factor': 1.3, 'rotation': 68, 'layer': 'MTEXT'})
        # A present direction has precedence over the retained angle.
        text.dxf.text_direction = (2, 1, .3 if tilted_text else 0)
        if tilted_text: text.dxf.extrusion = (-.15, 0, 1)
    leaf.add_line((0, 0, 0), (260, 0, 0), dxfattribs={'layer': 'CONTROL_LINE'})
    refs = []
    block = leaf
    for i, opts in enumerate(CASES[name]):
        layout = doc.modelspace() if i == len(CASES[name]) - 1 else doc.blocks.new('LEVEL' + str(i), base_point=(31 + i, 12, -2))
        ref = layout.add_blockref(block.name, (300 + i * 1000, 400 + i * 700, 12), dxfattribs=opts)
        refs.append(ref)
        block = layout
    path = tmp_path / 'anonymous.dxf'; doc.saveas(path)
    return doc, path, refs, spline, text


def cumulative_instances(refs):
    """Independent grid formula in original INSERT OCS; spacing precedes scale.

    Each raw original matrix already includes its own definition base point.
    Unlike virtual_entities/multi_insert this explicitly builds each raw matrix.
    """
    products = [Matrix44()]
    for ref in refs:
        d = ref.dxf; c, s = math.cos(math.radians(d.rotation)), math.sin(math.radians(d.rotation))
        offsets = {(col * d.column_spacing, row * d.row_spacing)
                   for row in range(d.row_count) for col in range(d.column_count)}
        next_products = []
        for x, y in sorted(offsets):
            clone = ref.copy()
            clone.dxf.insert = d.insert + Vec3(c * x - s * y, s * x + c * y, 0)
            raw = clone.matrix44()
            next_products.extend(Matrix44.chain(m, raw) for m in products)
        products = next_products
    return products


def render(path):
    dw = bg._Drawing(path); root = next(iter(dw.msp))
    walker = bg._Walker(dw, {**bg.DEFAULTS, 'flatten_mm': .002, 'large_block_layers': bg.LARGE_BLOCK_LAYERS}, set(), 1)
    before = tuple(root.matrix44())
    _, rows, skipped, _ = walker.run(root)
    assert before == tuple(root.matrix44())
    assert walker.run(root)[1] == rows
    return dw, root, rows, skipped


def segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    dd = dx * dx + dy * dy
    k = 0 if dd == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd))
    return math.hypot(p[0] - a[0] - k * dx, p[1] - a[1] - k * dy)


@pytest.mark.parametrize('name', CASES)
def test_fit_only_interior_curve_and_mtext_all_line_anchors(tmp_path, name):
    _, path, refs, spline, text = fixture(tmp_path, name)
    dw, root, rows, skipped = render(path)
    assert not skipped
    matrices = cumulative_instances(refs)
    curves = [item['points'] for layer, item in rows if layer == 'SPLINE']
    lines = [item['points'] for layer, item in rows if layer == 'CONTROL_LINE']
    labels = [item for layer, item in rows if layer == 'MTEXT']
    assert len(curves) == len(lines) == len(matrices) and len(labels) == 3 * len(matrices)
    tool = spline.construction_tool()
    original = [tool.point(tool.max_t * k / 512) for k in range(513)]
    for matrix in matrices:
        samples = [matrix.transform(p) for p in original]
        curve = min(curves, key=lambda points: math.dist(points[0], tuple(samples[0])[:2]))
        assert max(min(segment_distance(p, a, b) for a, b in zip(curve, curve[1:])) for p in samples) < .004
        assert math.dist(curve[0], tuple(samples[0])[:2]) < 1e-8 and math.dist(curve[-1], tuple(samples[-1])[:2]) < 1e-8
        p, q = [matrix.transform(v) for v in [(0, 0, 0), (260, 0, 0)]]
        line = min(lines, key=lambda points: math.dist(points[0], tuple(p)[:2]))
        assert math.dist(line[0], tuple(p)[:2]) < 1e-8 and math.dist(line[-1], tuple(q)[:2]) < 1e-8
        ux = Vec3(text.dxf.text_direction).normalize()
        uy = Vec3(text.dxf.extrusion).cross(ux).normalize()
        for k, value in [(0, 'ALPHA'), (1, 'BETA'), (3, 'GAMMA')]:
            want = matrix.transform(text.dxf.insert - uy * (20 * 1.667 * 1.3 * k))
            label = min((x for x in labels if x['text'] == value), key=lambda x: math.dist(x['point'], tuple(want)[:2]))
            assert math.dist(label['point'], tuple(want)[:2]) < 1e-8
            if name not in CONTROLS:
                axes = [matrix.transform_direction(v) for v in (ux, uy)]
                assert all(math.dist(got, tuple(expected)[:2]) < 1e-10 for got, expected in zip(label['text_axes'], axes))
                assert label['height'] == 20
    if name in CONTROLS:
        assert not dw.affine_loss(root) and not getattr(dw, 'definition_display', lambda _: False)(root)
        if name != 'mirror_control': assert all('text_axes' not in x for x in labels)


@pytest.mark.parametrize('attachment', [1, 5, 9])
@pytest.mark.parametrize('tilted', [False, True])
def test_mtext_direction_attachment_and_ocs_use_original_line_offsets(tmp_path, attachment, tilted):
    _, path, refs, _, text = fixture(tmp_path, 'single', kind='text', attachment=attachment, tilted_text=tilted)
    _, _, rows, skipped = render(path); assert not skipped
    matrix = cumulative_instances(refs)[0]
    up = Vec3(text.dxf.extrusion).cross(Vec3(text.dxf.text_direction)).normalize()
    first_offset = {1: 0, 5: 1.5, 9: 3}[attachment] * 20 * 1.667 * 1.3
    for value, line in [('ALPHA', 0), ('BETA', 1), ('GAMMA', 3)]:
        item = next(item for layer, item in rows if layer == 'MTEXT' and item['text'] == value)
        p = matrix.transform(text.dxf.insert + up * (first_offset - line * 20 * 1.667 * 1.3))
        assert math.dist(item['point'], tuple(p)[:2]) < 1e-8


@pytest.mark.parametrize('kind', ['spline', 'text'])
def test_each_target_alone_triggers_definition_path(tmp_path, kind):
    _, path, _, _, _ = fixture(tmp_path, 'no_inner_rotation', kind=kind)
    dw, root, rows, skipped = render(path)
    assert not dw.affine_loss(root) and dw.definition_display(root)
    assert rows and not skipped


def test_nonplanar_fit_spline_uses_all_three_scale_axes(tmp_path):
    _, path, refs, spline, _ = fixture(tmp_path, 'uniform_control', kind='spline', spatial_spline=True)
    dw, root, rows, skipped = render(path)
    assert dw.definition_display(root) and not skipped
    tool = spline.construction_tool(); matrix = cumulative_instances(refs)[0]
    curve = next(item['points'] for layer, item in rows if layer == 'SPLINE')
    for k in range(257):
        p = matrix.transform(tool.point(tool.max_t * k / 256))
        assert min(segment_distance(p, a, b) for a, b in zip(curve, curve[1:])) < .004


@pytest.mark.parametrize('incomplete', [False, True])
def test_failed_or_incomplete_array_expansion_is_explicit_not_shown(tmp_path, monkeypatch, incomplete):
    _, path, _, _, _ = fixture(tmp_path, 'top_array')
    from ezdxf.entities import Insert
    def broken(self):
        if incomplete: return []
        raise ValueError('anonymous failed grid')
    monkeypatch.setattr(Insert, 'multi_insert', broken)
    _, _, rows, skipped = render(path)
    assert not rows and len(skipped) == 1 and skipped[0][0] == 'proxy'


@pytest.mark.parametrize('enabled', [True, False])
def test_definition_route_never_presents_active_xclip_as_unclipped_geometry(tmp_path, enabled):
    from ezdxf.xclip import XClip
    doc, path, refs, _, _ = fixture(tmp_path, 'single')
    clip = XClip(refs[-1]); clip.set_block_clipping_path([(0, 0), (20, 20)])
    if not enabled: clip.disable_clipping()
    doc.saveas(path)
    _, _, rows, skipped = render(path)
    if enabled: assert not rows and skipped == [('proxy', None)]
    else: assert rows and not skipped


def test_unreadable_clip_state_is_explicit_not_shown(tmp_path, monkeypatch):
    from ezdxf.xclip import XClip
    doc, path, refs, _, _ = fixture(tmp_path, 'single')
    XClip(refs[-1]).set_block_clipping_path([(0, 0), (20, 20)]); doc.saveas(path)
    def broken(self): raise ValueError('anonymous invalid filter')
    monkeypatch.setattr(XClip, 'is_clipping_enabled', property(broken))
    _, _, rows, skipped = render(path)
    assert not rows and skipped == [('proxy', None)]
