"""Ordered DN callouts for the existing pipe adapter; no quantity or DN propagation engine.

Consumes the neutral extractor's full WCS geometry through qto_segment_graph.
The caller supplies the drawing-specific text order and complete leader path.
"""
from __future__ import annotations

import math

from qto_segment_graph import row_parts


def number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数值")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{label} 必须{'大于' if positive else '不小于'}0")
    return float(value)


def targets(value, label):
    values = [value] if isinstance(value, str) else value
    if (not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.startswith('SEG-') for v in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"{label} 必须为 SEG- 管段ID或不重复的非空管段ID数组")
    return list(values)


def ids(value, label):
    if (not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} 必须为不重复的非空ID数组")
    return value


def point(value, label):
    if not isinstance(value, (tuple, list)) or len(value) not in (2, 3):
        raise ValueError(f"{label} 须为二维/三维有限坐标")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(f"{label} 须为二维/三维有限坐标")
    return [float(v) for v in value] + ([0.] if len(value) == 2 else [])


def _line_parts(row, factor):
    # Neutral block expansion retains raw LEADER vertices in definition coordinates.
    # Other geometry has already been transformed by the shared expansion adapter.
    kind = row.get('entity_type')
    if kind == 'LEADER' and (row.get('raw_geometry') or {}).get('path_type', 0) != 0:
        raise ValueError('曲线LEADER不能以顶点折线冒充完整路径')
    matrix = row.get('instance_transform') if kind == 'LEADER' else None
    parts, problem = row_parts(row, matrix)
    if problem or not parts or any(p['kind'] != 'line' for p in parts):
        raise ValueError(f"引线需完整直线/多折点几何：{problem or '含曲线或无路径'}")
    return [dict(kind='line', start=[v * factor for v in point(p['start'], '引线起点')],
                 end=[v * factor for v in point(p['end'], '引线终点')]) for p in parts]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _intersection(a, b, c, d, eps):
    """Planar crossing with Z check. Collinear overlaps are deliberately ambiguous."""
    u, v, w = [b[i]-a[i] for i in range(3)], [d[i]-c[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
    den = _cross(u, v)
    lu, lv = math.hypot(*u[:2]), math.hypot(*v[:2])
    if min(lu, lv) <= eps:
        raise ValueError('引线/管段含零长或竖直投影，不能唯一排序')
    if abs(den) <= 1e-12 * lu * lv:
        if abs(_cross(w, u)) / lu <= eps:
            ts = [sum((p[i]-a[i])*u[i] for i in range(2))/(lu*lu) for p in (c, d)]
            if min(1., max(ts)) - max(0., min(ts)) > eps/lu:
                raise ValueError('引线与管段共线重叠，交点不唯一')
        return None
    t, s = _cross(w, v)/den, _cross(w, u)/den
    if not (-eps/lu <= t <= 1+eps/lu and -eps/lv <= s <= 1+eps/lv):
        return None
    t, s = max(0., min(1., t)), max(0., min(1., s))
    p, q = [a[i]+t*u[i] for i in range(3)], [c[i]+s*v[i] for i in range(3)]
    return (t, s, p) if math.dist(p, q) <= eps else None


def bind_stack(stack, rows, segments, *, factor, output_unit, source_sha256, system, space_id, applies, system_by_segment=None):
    if not isinstance(stack, dict) or not isinstance(stack.get('stack_id'), str) or not stack['stack_id'].strip():
        raise ValueError('callout_stacks 项须有 stack_id')
    sid = stack['stack_id']
    if stack.get('source_sha256') != source_sha256 or stack.get('unit') != output_unit:
        raise ValueError(f'{sid} 来源哈希/容差单位不匹配')
    eps = number(stack.get('intersection_tolerance'), f'{sid}.intersection_tolerance', positive=True)
    if not str(stack.get('tolerance_basis') or '').strip():
        raise ValueError(f'{sid} 缺图面容差依据')
    texts = ids(stack.get('text_ids'), f'{sid}.text_ids')
    pipe_ids = targets(stack.get('segment_ids'), f'{sid}.segment_ids')
    path = stack.get('leader_path')
    if not isinstance(path, list) or not path:
        raise ValueError(f'{sid}.leader_path 须为有向完整引线路径')
    leader_ids = ids([p.get('record_id') if isinstance(p, dict) else None for p in path], f'{sid}.leader_path')
    for oid in texts + leader_ids + [v[4:] for v in pipe_ids]:
        if oid not in rows or not applies(oid):
            raise ValueError(f'{sid} 对象不在来源/识别范围内: {oid}')
        row = rows[oid]
        if row.get('source_sha256') != source_sha256 or (row.get('space_id') is not None and row['space_id'] != space_id):
            raise ValueError(f'{sid} 对象跨来源/空间: {oid}')
    parts = []
    for step in path:
        row = rows[step['record_id']]
        if row.get('entity_type') not in {'LINE', 'LWPOLYLINE', 'POLYLINE', 'LEADER'}:
            raise ValueError(f'{sid} 不支持该引线类型')
        ps = _line_parts(row, factor)
        if step.get('direction') == 'reverse':
            ps = [dict(kind='line', start=p['end'], end=p['start']) for p in reversed(ps)]
        elif step.get('direction') != 'forward':
            raise ValueError(f'{sid} 须显式给引线路径方向 forward/reverse')
        parts.extend(ps)
    for i, p in enumerate(parts):
        if math.dist(p['start'], p['end']) <= eps:
            raise ValueError(f'{sid} 引线含退化段')
        if i and math.dist(parts[i-1]['end'], p['start']) > eps:
            raise ValueError(f'{sid} 引线路径不连续，不以端点弦代替')
    if any(abs(v[2]-parts[0]['start'][2]) > eps for p in parts for v in (p['start'], p['end'])):
        raise ValueError(f'{sid} 非图面引线路径须专项原生核对')
    axis = point(stack.get('text_order_axis'), f'{sid}.text_order_axis')
    if abs(axis[2]) > 1e-12 or math.hypot(*axis[:2]) == 0:
        raise ValueError(f'{sid} 文字排序轴须为有效图面方向')
    n = math.hypot(*axis[:2])
    if not math.isfinite(n):
        raise ValueError(f'{sid} 文字排序方向数值溢出')
    axis = [v/n for v in axis]
    ordered = []
    for oid in texts:
        row = rows[oid]
        if row.get('entity_type') not in {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}:
            raise ValueError(f'{sid} text_ids 含非文字')
        anchor = point([row.get('x'), row.get('y'), row.get('z', 0.)], f'{sid}文字锚点')
        ordered.append((sum(a*b for a, b in zip(anchor, axis))*factor, oid))
    ordered.sort()
    if any(b[0]-a[0] <= eps for a, b in zip(ordered, ordered[1:])):
        raise ValueError(f'{sid} 文字栈顺序不唯一')
    hits = []
    for pipe_id in pipe_ids:
        pipe = segments.get(pipe_id)
        if pipe is None or pipe['system'] != (system_by_segment or {}).get(pipe_id, system) or pipe.get('space_id') != space_id:
            raise ValueError(f'{sid} 管段不可用或跨系统/空间: {pipe_id}')
        pipe_parts = (pipe.get('geometry') or {}).get('parts') or [dict(kind='line', start=pipe['start'], end=pipe['end'])]
        found, distance = [], 0.
        for li, lp in enumerate(parts):
            for pi, pp in enumerate(pipe_parts):
                if pp['kind'] != 'line':
                    raise ValueError(f'{sid} 曲线管段交点尚不支持，保留原候选')
                hit = _intersection(lp['start'], lp['end'], point(pp['start'], '管段起点'), point(pp['end'], '管段终点'), eps)
                if hit:
                    t, parameter, p = hit
                    station = distance + math.dist(lp['start'], lp['end'])*t
                    if not any(abs(station-h['station']) <= eps and math.dist(p, h['point']) <= eps for h in found):
                        found.append(dict(segment_id=pipe_id, station=station, point=p, leader_part_index=li, part_index=pi, parameter=parameter))
            distance += math.dist(lp['start'], lp['end'])
        if len(found) != 1:
            raise ValueError(f'{sid} 管段与完整引线交点数应为1，实际{len(found)}: {pipe_id}')
        hits.extend(found)
    # The complete leader must not silently skip another recognized pipe of a covered system.
    covered_systems = {segments[pid]['system'] for pid in pipe_ids}
    for other_id, other in segments.items():
        if other_id in pipe_ids or other.get('space_id') != space_id or other.get('system') not in covered_systems:
            continue
        for pp in ((other.get('geometry') or {}).get('parts') or [dict(kind='line', start=other['start'], end=other['end'])]):
            if pp['kind'] != 'line':
                continue
            if any(_intersection(lp['start'], lp['end'], point(pp['start'], '管段起点'), point(pp['end'], '管段终点'), eps) for lp in parts):
                raise ValueError(f'{sid} 完整引线还穿过未列目标管段: {other_id}')
    hits.sort(key=lambda h: h['station'])
    if len(hits) != len(ordered) or any(b['station']-a['station'] <= eps for a, b in zip(hits, hits[1:])):
        raise ValueError(f'{sid} 标注数/交点数不符或逐管交点顺序不唯一')
    return {oid: [hit['segment_id']] for (_, oid), hit in zip(ordered, hits)}, {
        'stack_id': sid, 'method': 'complete_leader_crossing_order', 'text_order': [o for _, o in ordered],
        'leader_path': path, 'intersections': hits, 'unit': output_unit, 'tolerance': eps,
        'source_sha256': source_sha256, 'review_status': 'unreviewed', 'formal_adoption_allowed': False}
