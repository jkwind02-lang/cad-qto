"""Source freshness and dimensional unit checks, never drawing multiplicity."""
import hashlib
import math
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
