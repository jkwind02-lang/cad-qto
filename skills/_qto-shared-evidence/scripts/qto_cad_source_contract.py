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
