"""Replay current CAD source instance paths and parametric source intervals.

No SI or diagram length is inferred here. Array identity uses the existing
workbench HANDLE[row,column] contract and never supplies a quantity multiplier.
"""
from __future__ import annotations

import copy
import math
import re

from qto_cad_geometry import (IDENTITY, compose_matrix, entity_geometry,
    transform_geometry, measure_part, part_extrema)
from qto_pipe_topology import part_point, part_slice


def require(condition, message):
    if not condition:
        raise ValueError(message)


def resolve_instance(doc, path):
    require(isinstance(path, list) and path and all(isinstance(v, str) for v in path),
        '必须提供从Model到叶实体的完整INSERT实例路径')
    owner, matrix = doc.modelspace().block_record_handle, IDENTITY
    for index, token in enumerate(path):
        match = re.fullmatch(r'([0-9A-Fa-f]+)(?:\[(\d+),(\d+)\])?', token)
        require(match is not None, '源实例路径句柄或阵列单元格式无效')
        entity = doc.entitydb.get(match[1].upper())
        require(entity is not None and entity.dxf.owner == owner, '源实例路径与父块/Model不符')
        require(not entity.dxf.get('invisible', 0), '隐藏源实体不能作当前图面证据')
        layer = doc.layers.get(entity.dxf.get('layer', '0'))
        require(layer is not None and not layer.is_off() and not layer.is_frozen(),
            '关闭或冻结图层不属于当前图面证据')
        if index < len(path)-1:
            require(entity.dxftype() == 'INSERT', '路径中间节点必须是INSERT')
            array = entity.dxf.row_count != 1 or entity.dxf.column_count != 1
            require(array == (match[2] is not None), '阵列INSERT须有逐单元身份；普通INSERT不能虚构阵列单元')
            instance = entity
            if array:
                r, c = int(match[2]), int(match[3])
                require(match[2] == str(r) and match[3] == str(c), '阵列单元身份必须使用规范行列整数，不能以别名重复计量')
                require(r < entity.dxf.row_count and c < entity.dxf.column_count, '阵列单元越出原源声明')
                require(not ((r != 0 and entity.dxf.row_spacing == 0) or (c != 0 and entity.dxf.column_spacing == 0)),
                    '阵列零间距cell不是独立原生实例，须使用规范0行/0列')
                from ezdxf.math import Vec3
                instance = entity.copy()
                offset = Vec3(c*entity.dxf.column_spacing, r*entity.dxf.row_spacing).rotate_deg(entity.dxf.rotation)
                instance.dxf.insert += offset
                instance.dxf.row_count = instance.dxf.column_count = 1
            matrix = compose_matrix(matrix, entity_geometry(instance)['insert_matrix_wcs'])
            owner = doc.blocks[entity.dxf.name].block_record_handle
        else:
            require(match[2] is None, '叶实体不能把阵列容器当物理曲线')
    return entity, matrix


def source_interval(drawing, spec, bounds):
    require(isinstance(spec, dict), 'source_span须为源区段对象')
    entity, matrix = drawing.resolve(spec.get('path'))
    require(entity.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'ELLIPSE'},
        '物理源段只接原生直线、多段线、ARC及ELLIPSE；代理构件须另走取证')
    geometry = entity_geometry(entity).get('geometry')
    require(geometry is not None, '源实例没有可定位曲线')
    parts = transform_geometry(geometry, matrix)['parts']
    index = spec.get('part', 0)
    require(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(parts), '源part索引无效')
    require('part' in spec or len(parts) == 1, '多part源须明确区段')
    a, b = spec.get('from_fraction', 0), spec.get('to_fraction', 1)
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (a, b))
        and 0 <= a < b <= 1, '源区段须为0到1内的正向非零区间')
    sliced = part_slice(parts[index], a, b)
    drawing.inside(part_extrema(sliced), bounds)
    length, error = measure_part(sliced)
    require(length > 0, '零长源段不能绑定实物范围')
    output = {'path': [h.upper() for h in spec['path']], 'part': index,
        'from_fraction': float(a), 'to_fraction': float(b),
        'page_endpoints': [part_point(sliced, t) for t in (0, 1)],
        'layer': entity.dxf.layer, 'page_coordinate_length': length,
        'page_coordinate_unit': 'raw_location_only', 'physical_length_from_coordinates': False}
    if sliced['kind'] != 'line':
        output['page_curve'] = copy.deepcopy(sliced)
        output['page_coordinate_length_error_estimate'] = error
    return output


def scaled_part(part, scale):
    output = copy.deepcopy(part)
    for key in ('start', 'end', 'center', 'u', 'v'):
        if key in output:
            output[key] = [v*scale for v in output[key]]
    return output


def unique_workbench_interval(parts, target, source_scale, target_scale):
    """Only the unchanged producer's actual geometric parameterization is read.

    Length equality and endpoint equality alone cannot identify a curved part.
    Coefficients and directed parameters must reproduce its complete interval.
    Identical overlapping parts remain ambiguous even when the caller picks one.
    """
    target = scaled_part(target, target_scale)
    endpoints = [part_point(target, t) for t in (0, 1)]
    matches = []
    for index, raw in enumerate(parts):
        part = scaled_part(raw, source_scale)
        if part['kind'] != target['kind']:
            continue
        if part['kind'] == 'line':
            a, b = part['start'], part['end']
            vector = [y-x for x, y in zip(a, b)]; square = sum(v*v for v in vector)
            if square <= 0:
                continue
            fractions = [sum((p[i]-a[i])*vector[i] for i in range(3))/square for p in endpoints]
        elif part['kind'] == 'ellipse_arc':
            tolerance = max(measure_part(part)[0]*1e-9, 1e-8)
            if not all(math.dist(part[k], target[k]) <= tolerance for k in ('center', 'u', 'v')):
                continue
            sweep = part['sweep_param']
            if not sweep:
                continue
            first = (target['start_param']-part['start_param'])/sweep
            fractions = [first, first+target['sweep_param']/sweep]
        else:
            continue
        if not (all(-1e-9 <= t <= 1+1e-9 for t in fractions) and fractions[0] < fractions[1]):
            continue
        fractions = [0. if abs(t) <= 1e-9 else 1. if abs(t-1) <= 1e-9 else t for t in fractions]
        sliced = part_slice(part, *fractions)
        tolerance = max(measure_part(part)[0]*1e-9, 1e-8)
        if all(math.dist(part_point(sliced, t), part_point(target, t)) <= tolerance for t in (0, .25, .5, .75, 1)):
            matches.append((index, *fractions))
    require(len(matches) == 1, '派生边不能唯一对应原索引part区间；不能按近邻/等长手填或消除重合歧义')
    return matches[0], endpoints
