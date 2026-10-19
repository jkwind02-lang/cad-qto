"""Replay current CAD source instance paths and parametric source intervals.

No SI or diagram length is inferred here. Array identity uses the existing
workbench HANDLE[row,column] contract and never supplies a quantity multiplier.
"""
from __future__ import annotations

import copy
import math
import re

from qto_cad_geometry import (IDENTITY, compose_matrix, entity_geometry,
    transform_geometry, measure_part, part_extrema, transform_point)
from qto_pipe_topology import part_point, part_slice


def require(condition, message):
    if not condition:
        raise ValueError(message)


def resolve_instance(doc, path):
    require(isinstance(path, list) and path and all(isinstance(v, str) for v in path),
        '必须提供从Model到叶实体的完整INSERT实例路径')
    owner, matrix = doc.modelspace().block_record_handle, IDENTITY
    last_insert, parent_matrix, cell_offset = None, IDENTITY, (0., 0., 0.)
    for index, token in enumerate(path):
        match = re.fullmatch(r'([0-9A-Fa-f]+)(?:\[(\d+),(\d+)\])?', token)
        require(match is not None, '源实例路径句柄或阵列单元格式无效')
        entity = doc.entitydb.get(match[1].upper())
        attribute = (entity is not None and entity.dxftype() == 'ATTRIB'
            and index == len(path)-1 and last_insert is not None
            and entity.dxf.owner == last_insert.dxf.handle
            and any(item is entity for item in last_insert.attribs))
        require(entity is not None and (attribute if entity.dxftype() == 'ATTRIB'
            else entity.dxf.owner == owner), '源实例路径与父块/Model不符')
        require(not entity.dxf.get('invisible', 0), '隐藏源实体不能作当前图面证据')
        require(not (attribute and int(entity.dxf.get('flags', 0)) & 1), '隐藏属性不能作当前图面证据')
        layer = doc.layers.get(entity.dxf.get('layer', '0'))
        require(layer is not None and not layer.is_off() and not layer.is_frozen(),
            '关闭或冻结图层不属于当前图面证据')
        if index < len(path)-1:
            require(entity.dxftype() == 'INSERT', '路径中间节点必须是INSERT')
            array = entity.dxf.row_count != 1 or entity.dxf.column_count != 1
            require(array == (match[2] is not None), '阵列INSERT须有逐单元身份；普通INSERT不能虚构阵列单元')
            instance = entity
            offset = (0., 0., 0.)
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
            last_insert, parent_matrix, cell_offset = entity, matrix, tuple(offset)
            matrix = compose_matrix(matrix, entity_geometry(instance)['insert_matrix_wcs'])
            owner = doc.blocks[entity.dxf.name].block_record_handle
        else:
            require(match[2] is None, '叶实体不能把阵列容器当物理曲线')
            if attribute:
                # Attached ATTRIB coordinates already include its own INSERT
                # placement. Apply only the chosen cell offset once, followed
                # by the complete ancestor chain (including ancestor cells).
                translation = list(IDENTITY)
                translation[3], translation[7], translation[11] = cell_offset
                matrix = compose_matrix(parent_matrix, translation)
    return entity, matrix


def coordinate_unit_annotation(drawing, path, bounds):
    """Read a public-credential-selected SI carrier, including attached ATTRIB.

    This narrow adapter does not add ATTRIB to the general dimension/length
    annotation contract. The producer first matches the exact public proof.
    """
    entity, matrix = drawing.resolve(path)
    if entity.dxftype() not in {'ATTRIB', 'TEXT'}:
        return drawing.annotation(path, bounds)
    _, first, second = entity.get_placement()
    points = (first,) if second is None else (first, second)
    locations = [transform_point(list(entity.ocs().to_wcs(point)), matrix) for point in points]
    drawing.inside(locations, bounds)
    return {'path': copy.deepcopy(path), 'text': entity.dxf.text,
        'location': locations[0], 'placement_points_wcs': locations,
        'entity_type': entity.dxftype(), 'layer': entity.dxf.layer,
        'source_owner_handle': entity.dxf.owner, 'placement_matrix_wcs': list(matrix),
        'placement_rule': 'ATTRIB_parent_coordinates_plus_own_cell_offset_once_then_ancestors'
            if entity.dxftype() == 'ATTRIB' else 'source_coordinates_through_complete_INSERT_chain'}


def source_interval(drawing, spec, bounds):
    require(isinstance(spec, dict), 'source_span须为源区段对象')
    if 'route_interval' in spec:
        return source_route_interval(drawing, spec, bounds)
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


def span_parts(span):
    """One parameter coverage representation for old parts and native routes."""
    return span.get('source_parts', [span])


def coverage_key(span):
    # Directed traversal changes the port orientation, never the measured set.
    return tuple(sorted((tuple(p['path']), p['part'], p['from_fraction'], p['to_fraction'])
                        for p in span_parts(span)))


def coverage_overlap(left, right):
    return any(a['path'] == b['path'] and a['part'] == b['part'] and
        min(a['to_fraction'], b['to_fraction']) > max(a['from_fraction'], b['from_fraction'])+1e-12
        for a in span_parts(left) for b in span_parts(right))


def line_fraction(point, start, end):
    vector = [b-a for a, b in zip(start, end)]
    square = sum(v*v for v in vector)
    if not square:
        return None, math.inf
    t = sum((p-a)*v for p, a, v in zip(point, start, vector))/square
    return t, math.dist(point, [a+t*v for a, v in zip(start, vector)])


def line_overlap(a, b, tolerance):
    ts = [line_fraction(p, a['start'], a['end']) for p in (b['start'], b['end'])]
    return all(error <= tolerance for _, error in ts) and (
        min(1., max(t for t, _ in ts))-max(0., min(t for t, _ in ts))) > 1e-9


def visible_source_parts(drawing):
    """Enumerate actual cells of complete INSERT chains, without multipliers."""
    emitted = 0
    def walk(entities, prefix, active):
        nonlocal emitted
        for entity in entities:
            handle = entity.dxf.handle
            path = prefix+[handle]
            try:
                drawing.resolve(path)
            except ValueError:
                continue
            if entity.dxftype() == 'INSERT':
                name = entity.dxf.name
                require(name not in active and len(prefix) < 64, '循环或过深块不能排除原生路由歧义')
                rows, columns = entity.dxf.row_count, entity.dxf.column_count
                require(rows*columns <= 100000, '阵列过大，当前有界路由不能完整排除实例歧义')
                for row in range(rows):
                    for col in range(columns):
                        # Coincident cells have only the canonical zero cell identity.
                        if (row and not entity.dxf.row_spacing) or (col and not entity.dxf.column_spacing):
                            continue
                        token = handle+f'[{row},{col}]' if rows != 1 or columns != 1 else handle
                        yield from walk(drawing.doc.blocks[name], prefix+[token], active|{name})
            elif entity.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'ELLIPSE', 'CIRCLE', 'SPLINE'}:
                _, matrix = drawing.resolve(path)
                geometry = entity_geometry(entity).get('geometry')
                require(geometry is not None, '可见曲线没有原生几何，不能排除路由歧义')
                emitted += 1
                require(emitted <= 100000, '源实例过多，当前有界路由不能完整排除歧义')
                yield [v.upper() for v in path], transform_geometry(geometry, matrix)['parts']
    yield from walk(drawing.doc.modelspace(), [], set())


def source_route_interval(drawing, spec, bounds):
    require(set(spec) <= {'path', 'route_interval'}, '原生路由不能混用手选part或单段区间')
    entity, matrix = drawing.resolve(spec.get('path'))
    require(entity.dxftype() in {'LWPOLYLINE', 'POLYLINE'}, '原生路由只接同一有序原生多段线，不拼近邻散线')
    geometry = entity_geometry(entity).get('geometry')
    require(geometry is not None, '原生路由缺少曲线几何')
    parts = transform_geometry(geometry, matrix)['parts']
    interval = spec['route_interval']
    required = {'from_part', 'from_fraction', 'to_part', 'to_fraction'}
    require(isinstance(interval, dict) and set(interval) == required, '路由须明确有向起终part及参数')
    for key in ('from_part', 'to_part'):
        require(isinstance(interval[key], int) and not isinstance(interval[key], bool)
            and 0 <= interval[key] < len(parts), '路由起终part索引无效')
    for key in ('from_fraction', 'to_fraction'):
        require(isinstance(interval[key], (int, float)) and not isinstance(interval[key], bool)
            and math.isfinite(interval[key]) and 0 <= interval[key] <= 1, '路由起终参数须为0到1有限数值')
    def endpoint(prefix):
        index, fraction = interval[prefix+'_part'], float(interval[prefix+'_fraction'])
        # Adjacent end/start references name one native vertex. Keep every
        # nonvertex fraction literal: part+fraction arithmetic loses ULPs and
        # would make the same legacy interval look partially overlapping.
        return (index+1, 0.) if fraction == 1. and index < len(parts)-1 else (index, fraction)
    first, last = endpoint('from'), endpoint('to')
    require(first != last, '原生路由须为非零有向范围')
    direction = 1 if first < last else -1
    lo, hi = sorted((first, last))
    selected = []
    for index, part in enumerate(parts):
        if not lo[0] <= index <= hi[0]:
            continue
        a, b = lo[1] if index == lo[0] else 0., hi[1] if index == hi[0] else 1.
        if b <= a:
            continue
        require(part['kind'] == 'line', '有向原生路由当前仅接多段直线；曲线路由须保留未决')
        sliced = part_slice(part, a, b)
        length, _ = measure_part(sliced)
        require(length > 0, '原生路由含零长腿')
        endpoints = [part_point(sliced, t) for t in (0, 1)]
        drawing.inside(endpoints, bounds)
        selected.append({'path': [v.upper() for v in spec['path']], 'part': index,
            'from_fraction': float(a), 'to_fraction': float(b), 'direction': direction,
            'page_endpoints': endpoints if direction == 1 else endpoints[::-1],
            'layer': entity.dxf.layer, 'page_coordinate_length': length,
            'page_coordinate_unit': 'raw_location_only', 'physical_length_from_coordinates': False})
    if direction == -1:
        selected.reverse()
    require(selected, '原生路由没有非零源区段')
    tolerance = max(max(p['page_coordinate_length'] for p in selected)*1e-9,
        32*max(math.ulp(v) for p in selected for point in p['page_endpoints'] for v in point), 1e-10)
    require(all(math.dist(a['page_endpoints'][1], b['page_endpoints'][0]) <= tolerance
                for a, b in zip(selected, selected[1:])), '原生路由区段有缺口，不能直连端点补齐')
    ends = [selected[0]['page_endpoints'][0], selected[-1]['page_endpoints'][1]]
    require(math.dist(*ends) > tolerance, '闭合或端点重合路由不能作为当前唯一实物区段')
    target = tuple(selected[0]['path'])
    chosen = {p['part'] for p in selected}
    # Inspect the entire current source scene: selecting one part/cell cannot hide
    # a duplicate, overlapping source part or an internal branch.
    for path, other_parts in visible_source_parts(drawing):
        for index, other in enumerate(other_parts):
            for item in selected:
                if tuple(path) == target and index == item['part']:
                    continue
                leg = dict(kind='line', start=item['page_endpoints'][0], end=item['page_endpoints'][1])
                if other['kind'] != 'line':
                    # Curves are outside this bounded route binding; if their
                    # bbox reaches the selected leg, fail closed.
                    extrema = part_extrema(other)
                    require(any(max(p[k] for p in extrema) < min(leg['start'][k], leg['end'][k])-tolerance
                        or min(p[k] for p in extrema) > max(leg['start'][k], leg['end'][k])+tolerance
                        for k in range(3)), '曲线与原生直线路由范围相交，当前不能排除分支或重合歧义')
                    continue
                require(not line_overlap(leg, other, tolerance), '原生路由有重复/重合源part或实例，不能手选part消除歧义')
                for point in (other['start'], other['end']):
                    fraction, error = line_fraction(point, leg['start'], leg['end'])
                    if fraction is not None and -1e-9 <= fraction <= 1+1e-9 and error <= tolerance:
                        at_route_end = min(math.dist(point, p) for p in ends) <= tolerance
                        own_neighbor = tuple(path) == target and index in chosen and abs(index-item['part']) == 1
                        require(at_route_end or own_neighbor, '原生路由内部命中分支或重复顶点，须先划清实际端口范围')
                a, b = leg['start'], other['start']
                u, v = [y-x for x, y in zip(a, leg['end'])], [y-x for x, y in zip(b, other['end'])]
                w = [x-y for x, y in zip(a, b)]
                aa, ab, bb = sum(x*x for x in u), sum(x*y for x, y in zip(u, v)), sum(x*x for x in v)
                aw, bw = sum(x*y for x, y in zip(u, w)), sum(x*y for x, y in zip(v, w))
                determinant = aa*bb-ab*ab
                if aa and bb and determinant > aa*bb*1e-18:
                    t, q = (ab*bw-bb*aw)/determinant, (aa*bw-ab*aw)/determinant
                    if -1e-9 <= t <= 1+1e-9 and -1e-9 <= q <= 1+1e-9:
                        point, hit = [x+t*y for x, y in zip(a, u)], [x+q*y for x, y in zip(b, v)]
                        if math.dist(point, hit) <= tolerance:
                            own_neighbor = tuple(path) == target and index in chosen and abs(index-item['part']) == 1
                            require(min(math.dist(point, p) for p in ends) <= tolerance or own_neighbor,
                                '原生路由内部有交叉或分支，不能从页面走线猜选路')
    return {'path': list(target), 'route_interval': copy.deepcopy(interval),
        'source_parts': selected, 'direction': direction, 'page_endpoints': ends,
        'layer': entity.dxf.layer, 'page_coordinate_length': sum(p['page_coordinate_length'] for p in selected),
        'page_coordinate_unit': 'raw_location_only', 'physical_length_from_coordinates': False,
        'source_route_verification': 'ordered_native_parts_full_instance_replay_no_internal_branch_or_overlap'}


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
