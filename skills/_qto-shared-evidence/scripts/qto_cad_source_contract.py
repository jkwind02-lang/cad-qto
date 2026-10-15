"""Source freshness and dimensional unit checks, never drawing multiplicity."""
import hashlib
import math
import re
from pathlib import Path

_METRES = {
    'mm': .001, 'millimeter': .001, 'millimeters': .001, '毫米': .001,
    'cm': .01, 'centimeter': .01, 'centimeters': .01, '厘米': .01,
    'm': 1., 'meter': 1., 'meters': 1., 'metre': 1., 'metres': 1., '米': 1.,
    'km': 1000., 'kilometers': 1000.,
    'in': .0254, 'inch': .0254, 'inches': .0254,
    'ft': .3048, 'foot': .3048, 'feet': .3048,
}


def canonical_scale(unit):
    if not isinstance(unit, str) or unit.strip().lower() not in _METRES:
        raise ValueError(f'单位未知或不受支持: {unit!r}；须提供可核验的物理长度单位')
    return _METRES[unit.strip().lower()]


def unit_known(unit):
    """True only for a physical length unit; Unitless/Unknown/INSUNITS-0 are not."""
    return isinstance(unit, str) and unit.strip().lower() in _METRES


_UNIT_ALIASES = {"毫米": .001, "厘米": .01, "米": 1., "千米": 1000., "公里": 1000., "英寸": .0254, "英尺": .3048,
    "mm": .001, "millimeter": .001, "millimeters": .001, "millimetre": .001, "millimetres": .001,
    "cm": .01, "centimeter": .01, "centimeters": .01, "centimetre": .01, "centimetres": .01,
    "m": 1., "meter": 1., "meters": 1., "metre": 1., "metres": 1., "km": 1000.,
    "in": .0254, "inch": .0254, "inches": .0254, "ft": .3048, "foot": .3048, "feet": .3048}
# Longest complete units first: 毫米 is one token, never also 米.
_UNIT_PATTERN = re.compile(r"毫米|厘米|千米|公里|英寸|英尺|(?<![毫厘分千公])米|(?<![A-Za-z])(?:millimet(?:er|re)s?|centimet(?:er|re)s?|met(?:er|re)s?|inches|inch|feet|foot|mm|cm|km|ft|in|m)(?![A-Za-z])", re.I)


def unit_tokens(text, with_spans=False):
    """Length-unit words actually written in a source text, as metre scales (or spans)."""
    matches = [(m.start(), m.end(), _UNIT_ALIASES[m[0].lower()]) for m in _UNIT_PATTERN.finditer(text)]
    return matches if with_spans else {value for _, _, value in matches}


def verify_unit_declaration_texts(source_texts, declared_raw_unit, source_excerpt=None):
    """A declared raw unit must be the only unit the cited source texts state.

    source_excerpt, when given, must be a verbatim excerpt of one cited text that
    does not cut through a unit word (a quoted 米 inside 毫米 is not a metre).
    """
    scale = canonical_scale(declared_raw_unit)
    if source_excerpt is not None:
        if not isinstance(source_excerpt, str) or not source_excerpt.strip():
            raise ValueError("单位适用条款原文 source_excerpt 必须是非空文本")
        valid_excerpt = False
        for text in source_texts:
            start = text.find(source_excerpt)
            while start >= 0:
                end = start + len(source_excerpt)
                if not any(a < start < b or a < end < b for a, b, _ in unit_tokens(text, with_spans=True)):
                    valid_excerpt = True
                start = text.find(source_excerpt, start + 1)
        if not valid_excerpt:
            raise ValueError("单位条款不是完整来源原文，或截断了单位词")
        inspected = source_excerpt
    else:
        inspected = "\n".join(source_texts)
    if unit_tokens(inspected) != {scale}:
        raise ValueError("单位原文与声明不一致或含多种单位；须引用适用于当前量测的唯一单位条款，不能按子串猜测")
    return scale


UNIT_APPLIES_TO = {
    'coordinates': '图纸坐标/绘图单位',
    'annotation_values': '图面标注的尺寸数值',
    'pipe_length_elevation': '管长、标高等数值',
}
_RATIO_TOLERANCE = .02     # implied metres per drawing unit vs declared unit
_MEASURE_TOLERANCE = .01   # stated measured span vs the cited objects' extent


def _positive(value, label):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f'{label} 必须是正数')
    return float(value)


def verify_unit_corroboration(declared_raw_unit, items, object_bounds):
    """Geometry that independently fixes the drawing unit, for INSUNITS-0 drawings.

    A text statement about annotation values or pipe lengths says nothing about the
    coordinate unit by itself. Each item ties a measured span of cited objects to a
    known real size: a sheet frame at a stated plot scale, or a component of known
    size. object_bounds maps each cited object id to its [xmin, ymin, xmax, ymax]
    in raw drawing units. Returns the checked items with their implied scale.
    """
    scale = canonical_scale(declared_raw_unit)
    if not isinstance(items, list) or not items:
        raise ValueError('geometric_corroboration 必须是非空数组')
    checked = []
    for index, item in enumerate(items):
        label = f'geometric_corroboration[{index}]'
        if not isinstance(item, dict):
            raise ValueError(f'{label} 必须是对象')
        kind = item.get('kind')
        if kind == 'frame_size_scale':
            paper, measured = item.get('paper_size_mm'), item.get('measured_drawing_units')
            if not all(isinstance(v, (list, tuple)) and len(v) == 2 for v in (paper, measured)):
                raise ValueError(f'{label} 图框佐证必须同时提供两边：paper_size_mm=[纸宽,纸高]、measured_drawing_units=[实测X跨度,实测Y跨度]；旧单边声明须补齐，不能猜幅面')
            paper = [_positive(v, f'{label}.paper_size_mm[{i}]') for i, v in enumerate(paper)]
            measured = [_positive(v, f'{label}.measured_drawing_units[{i}]') for i, v in enumerate(measured)]
            denominator = _positive(item.get('scale_denominator'), f'{label}.scale_denominator')
        elif kind == 'known_component_size':
            measured = _positive(item.get('measured_drawing_units'), f'{label}.measured_drawing_units')
            real_m = _positive(item.get('real_length_m'), f'{label}.real_length_m')
        else:
            raise ValueError(f'{label}.kind 必须是 frame_size_scale 或 known_component_size')
        if not str(item.get('basis') or '').strip():
            raise ValueError(f'{label}.basis 必须写明图框幅面/比例或构件尺寸的依据')
        ids = item.get('object_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or not v.strip() for v in ids):
            raise ValueError(f'{label}.object_ids 必须是非空对象ID数组')
        boxes = []
        for oid in ids:
            box = object_bounds.get(oid)
            if (not isinstance(box, (list, tuple)) or len(box) < 4
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box[:4])
                or box[2] < box[0] or box[3] < box[1]):
                raise ValueError(f'{label} 引用的对象 {oid} 不在当前布局或没有可量的范围')
            boxes.append(box)
        width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
        height = max(b[3] for b in boxes) - min(b[1] for b in boxes)
        if kind == 'frame_size_scale':
            if any(abs(actual - claimed) > _MEASURE_TOLERANCE * claimed for actual, claimed in zip((width, height), measured)):
                raise ValueError(f'{label} 图框两条实测边与所引对象的实际宽 {width:.6g} / 高 {height:.6g} 不符')
            orientations = [paper, paper[::-1]]  # portrait and landscape, never select just one matching edge
            options = [[side * denominator / 1000. / span for side, span in zip(sides, measured)] for sides in orientations]
            accepted = next(((sides, ratios) for sides, ratios in zip(orientations, options)
                             if all(abs(r / scale - 1.) <= _RATIO_TOLERANCE for r in ratios)
                             and abs(ratios[0] / ratios[1] - 1.) <= _RATIO_TOLERANCE), None)
            if accepted is None:
                raise ValueError(f'{label} 图框幅面两边/比例与声明单位 {declared_raw_unit} 不符，或两边推得的坐标比例不一致')
            sides, ratios = accepted
            checked.append({**item, 'object_width': width, 'object_height': height,
                            'checked_paper_size_mm': sides, 'implied_metres_per_unit_by_axis': ratios,
                            'implied_metres_per_unit': sum(ratios) / 2., 'checked_axes': 2})
            continue
        if min(abs(width - measured), abs(height - measured)) > _MEASURE_TOLERANCE * measured:
            raise ValueError(f'{label}.measured_drawing_units={measured} 与所引对象的实际宽 {width:.6g} / 高 {height:.6g} 都不符')
        implied = real_m / measured
        if abs(implied / scale - 1.) > _RATIO_TOLERANCE:
            raise ValueError(f'{label} 推得每绘图单位 {implied:.6g} m，与声明单位 {declared_raw_unit}（{scale} m）不符')
        checked.append({**item, 'object_width': width, 'object_height': height, 'implied_metres_per_unit': implied})
    return checked


def check_unit_factor(raw_unit, output_unit, factor, declared_raw_unit=None):
    raw = canonical_scale(raw_unit)
    if declared_raw_unit is not None and canonical_scale(declared_raw_unit) != raw:
        raise ValueError('识别配置原始单位与CAD中性事实单位不一致')
    expected = raw / canonical_scale(output_unit)
    if isinstance(factor, bool) or not isinstance(factor, (float, int)) or not math.isfinite(factor):
        raise ValueError('单位换算系数须为有限数值')
    if not math.isclose(factor, expected, rel_tol=1e-12, abs_tol=0):
        raise ValueError(f'单位换算应为 {raw_unit}→{output_unit}: {expected}，不能混入单双线倍率、根数或损耗系数')
    return expected


def verify_source_file(value, label):
    if not isinstance(value, dict) or not value.get('path') or not value.get('sha256'):
        raise ValueError(f'{label}缺少文件路径或SHA256')
    path = Path(value['path'])
    if not path.is_file():
        raise ValueError(f'{label}不可用，不能以旧缓存替代当前来源: {path}')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    if h.hexdigest() != str(value['sha256']).lower():
        raise ValueError(f'{label}已改变，须重新取证和识别；旧缓存不得沿用')
