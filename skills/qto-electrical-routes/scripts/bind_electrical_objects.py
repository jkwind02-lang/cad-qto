#!/usr/bin/env python3
"""Bind model-selected electrical objects to immutable CAD geometry and existing calculators.

No circuit recognition, automatic routing, approval or project-specific rules. Curve fractions are
native part parameters, not fractions of arclength. All lengths are remeasured from WCS parts.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from collections import defaultdict

from qto_electrical_evidence import Evidence, finite, identity_rows, required, geometry, graph, routes
from calculate_electrical_paths import calculate as calculate_ledger
from qto_review_contract import normalize_view

SCHEMA = 'qto-electrical-object-request/1.0'
PACKAGE = 'qto-electrical-object-package/1.0'
COMPONENTS = ('vertical', 'terminal', 'reserve', 'undrawn_connection')
# Parameter representation tolerance, never the engineering connection/snap tolerance.
# Both gates must pass: at most two IEEE-754 ULPs and at most one nanometre of real curve.
MAX_PARAMETER_ULPS = 2
MAX_BOUNDARY_SHIFT_M = 1e-9


def point(part, fraction):
    if part['kind'] == 'line':
        return [a + (b-a)*fraction for a, b in zip(part['start'], part['end'])]
    return geometry.conic_point(part, part['start_param'] + part['sweep_param']*fraction)


def slice_part(part, lo, hi):
    if part['kind'] == 'line':
        return dict(kind='line', start=point(part, lo), end=point(part, hi))
    return dict(part, start_param=part['start_param'] + part['sweep_param']*lo,
                sweep_param=part['sweep_param']*(hi-lo))


def interval_length(part, lo, hi, factor):
    """Physical size of a parameter interval, without endpoint cancellation on long lines."""
    if part['kind'] == 'line':
        return geometry.measure_part(part)[0] * abs(hi-lo) * factor
    return geometry.measure_part(slice_part(part, min(lo,hi), max(lo,hi)))[0] * factor


def normalize_cuts(part, values, protected_ranges, factor):
    """Bounded numeric-equivalence clusters; preserve positive declared intervals and real gaps.

    A large WCS scale never makes a one-ULP parameter gap dispensable: its actual curve length
    must still be <=1 nm. The representative is an original value, not a rounded/fabricated cut.
    """
    groups=[]
    protected={tuple(sorted(pair))for pair in protected_ranges}
    for value in sorted(set(values)):
        group=groups[-1]if groups else None
        if group:
            proposed=[*group,value]
            close=all(abs(value-v)<=MAX_PARAMETER_ULPS*max(math.ulp(value),math.ulp(v)) and
                      interval_length(part,v,value,factor)<=MAX_BOUNDARY_SHIFT_M for v in group)
            collapses=any(a in proposed and b in proposed for a,b in protected)
            if close and not collapses:
                group.append(value);continue
        groups.append([value])
    aliases={v:group[0]for group in groups for v in group}
    changes=[dict(original_fraction=v,normalized_fraction=aliases[v],
                  physical_boundary_shift_m=interval_length(part,v,aliases[v],factor))
             for v in sorted(aliases)if v!=aliases[v]]
    return aliases,changes


def span(raw):
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError('fraction_range须为[lo,hi]，方向另列direction')
    lo, hi = [finite(v, 'fraction_range') for v in raw]
    if not 0 <= lo < hi <= 1:
        raise ValueError('fraction_range须满足0<=lo<hi<=1；反向使用direction=-1')
    return lo, hi


def merged(spans):
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def subtract(spans, cuts):
    result = []
    for lo, hi in spans:
        current = lo
        for a, b in merged(cuts):
            if b <= current or a >= hi:
                continue
            if a > current:
                result.append([current, min(a, hi)])
            current = max(current, b)
        if current < hi:
            result.append([current, hi])
    return result


def key_for(sid, index):
    return sid + ':' + str(index)


def binding(e, raw, sheets, mode):
    if not isinstance(raw,dict):
        raise ValueError('原段须为对象')
    sid = required(raw.get('source_id'), '原段.source_id')
    e.refs([sid], '原段.source_id')
    index = raw.get('part_index')
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError('part_index须为非负整数')
    row = e.byid[sid]
    parts, problem = graph.row_parts(row)
    if problem or index >= len(parts):
        raise ValueError('原曲线不能回取: ' + sid + ': ' + str(problem or 'part_index越界'))
    if row.get('entity_type') not in graph.CURVE_TYPES | {'ELLIPSE', 'CIRCLE'}:
        raise ValueError('原段不是受支持的线路曲线: ' + sid)
    part = copy.deepcopy(parts[index])
    if mode == 'plan':
        # Explicit projection only; true vertical/offset must be separate objects/components.
        if part['kind'] == 'line':
            part['start'][2] = part['end'][2] = 0.0
        else:
            if abs(part['u'][2]) + abs(part['v'][2]) > 1e-10:
                raise ValueError('非水平圆锥曲线不支持平面投影，须用spatial或单列待办')
            part['center'][2] = 0.0
    lo, hi = span(raw.get('fraction_range'))
    direction = raw.get('direction')
    if isinstance(direction, bool) or direction not in (-1, 1):
        raise ValueError('direction须明确为1或-1')
    sheet = required(raw.get('sheet_id'), '原段.sheet_id')
    if sheet not in sheets:
        raise ValueError('原段图页不存在: ' + sheet)
    floor = required(raw.get('floor'), '原段.floor')
    if floor != sheets[sheet]['floor']:
        raise ValueError('原段楼层与图页声明不一致')
    if raw.get('instance_path') != (row.get('instance_path') or sid):
        raise ValueError('原段完整instance_path不匹配，不能只引用块定义句柄')
    box = sheets[sheet]['bounds']
    if any(not (box[0]-1e-9 <= p[0] <= box[2]+1e-9 and box[1]-1e-9 <= p[1] <= box[3]+1e-9)
           for p in geometry.part_extrema(slice_part(part, lo, hi))):
        raise ValueError('所选区间越出声明图页，须按图页拆段')
    e.refs(raw.get('basis_source_ids'), '原段.basis_source_ids')
    role = raw.get('role')
    if role not in ('horizontal', 'vertical', 'offset'):
        raise ValueError('原段role须为horizontal/vertical/offset')
    if mode == 'plan' and role != 'horizontal':
        raise ValueError('平面投影区间不能声明为竖向或上下翻实长')
    piece = slice_part(part, lo, hi)
    length, error = geometry.measure_part(piece)
    length *= e.factor
    if not math.isfinite(length) or length <= 0:
        raise ValueError('回取原曲线长度须为有限正数')
    a, b = point(part, lo), point(part, hi)
    if direction == -1:
        a, b = b, a
    original_piece=slice_part(parts[index],lo,hi)
    raw_points=geometry.part_extrema(original_piece)
    if mode=='spatial':
        if role=='horizontal' and (max(p[2]for p in raw_points)-min(p[2]for p in raw_points))*e.factor>1e-8:
            raise ValueError('spatial水平段原几何Z变化，须按实际上下翻/斜段角色核定')
        if role=='vertical' and any((max(p[i]for p in raw_points)-min(p[i]for p in raw_points))*e.factor>1e-8 for i in (0,1)):
            raise ValueError('spatial竖段原几何有水平位移，不能把斜长称为纯竖向')
    return dict(source_id=sid, part_index=index, instance_path=raw['instance_path'],
                sheet_id=sheet, floor=floor, fraction_range=[lo, hi], direction=direction,
                role=role, basis_source_ids=raw['basis_source_ids'], source_part=part,
                part_key=key_for(sid, index), from_point_m=[v*e.factor for v in a],
                to_point_m=[v*e.factor for v in b], length_m=length,
                source_spatial_length_m=geometry.measure_part(original_piece)[0]*e.factor,
                source_z_range_m=[min(p[2]for p in raw_points)*e.factor,max(p[2]for p in raw_points)*e.factor],
                measurement_error_m=error*e.factor, original_part=parts[index])


def endpoint(e, raw, mode, tolerance):
    """Reuse actual instance-port validator; curve-node endpoints never become equipment ports."""
    kind = raw.get('kind')
    if kind == 'connection_port':
        result = e.port(raw, tolerance)
    elif kind == 'curve_node':
        sid = required(raw.get('source_id'), 'endpoint.source_id')
        e.refs([sid], 'endpoint.source_id')
        row = e.byid[sid]
        if raw.get('instance_path') != (row.get('instance_path') or sid):
            raise ValueError('节点instance_path与原图不符')
        parts, problem = graph.row_parts(row)
        index = raw.get('part_index')
        if problem or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(parts):
            raise ValueError('节点原曲线/part_index不可回取')
        fraction = finite(raw.get('fraction'), 'endpoint.fraction')
        if fraction > 1:
            raise ValueError('endpoint.fraction须在0..1')
        e.refs(raw.get('basis_source_ids'), 'endpoint.basis_source_ids')
        result = [v*e.factor for v in point(parts[index], fraction)]
    else:
        raise ValueError('endpoint.kind须为connection_port或curve_node')
    if mode == 'plan':
        result[2] = 0.0
    return result


def trace_steps(e, request, obj, trace):
    """Only explicitly selected, same-source actual-drawn traces; recompute, never trust declared lengths."""
    rid = obj.get('trace_route_id')
    found = [r for r in trace.get('routes', []) if r.get('route_id') == rid]
    if len(found) != 1 or found[0].get('status') != '已追踪':
        raise ValueError('trace_route_id未唯一已追踪')
    route = found[0]
    if route.get('bridged_gaps') or route.get('overlap_deducted_m', 0):
        raise ValueError('有补连或重合扣减的trace须逐段显式建对象，不能冒充actual_drawn_path')
    steps = []
    meta = obj.get('trace_segment_context', {})
    for segment in route.get('segments', []):
        match = re.fullmatch(r'SEG-(.+):(\d+)', str(segment.get('segment_id')))
        if not match:
            raise ValueError('trace原段身份不能解析')
        sid, index = match[1], int(match[2])
        e.refs([sid], 'trace原段')
        parts, problem = graph.row_parts(e.byid[sid])
        if problem or index >= len(parts):
            raise ValueError('trace原曲线不能回取')
        lo, hi = segment.get('fraction_range', [0.0, 1.0])
        start = [v*e.factor for v in point(parts[index], lo)]
        end = [v*e.factor for v in point(parts[index], hi)]
        if request['measurement_mode'] == 'plan':
            start[2] = end[2] = 0.0
        a, b = segment['from_point'], segment['to_point']
        # Existing L1 trace endpoint coordinates are 2-D metres.
        forward = math.dist(start[:2], a[:2]) + math.dist(end[:2], b[:2])
        reverse = math.dist(start[:2], b[:2]) + math.dist(end[:2], a[:2])
        if min(forward, reverse) > 2*request['connection_tolerance_m']:
            raise ValueError('trace坐标与原曲线不一致；不能继承旧结果')
        if abs(forward-reverse) <= 1e-12:
            raise ValueError('trace方向不能唯一恢复，须显式选择分段方向')
        context = meta.get(segment['segment_id']) or obj.get('trace_context') or {}
        steps.append(dict(context, source_id=sid, part_index=index,
                          instance_path=e.byid[sid].get('instance_path') or sid,
                          fraction_range=[lo, hi], direction=1 if forward < reverse else -1,
                          role='horizontal'))
    return steps, route.get('review_flags', [])


def calculate(e, request):
    if request.get('schema') != SCHEMA:
        raise ValueError('request.schema须为' + SCHEMA)
    if request.get('cad_manifest_sha256') != routes.sha256(e.path):
        raise ValueError('对象请求与原图中性清单版本不一致')
    source = e.manifest.get('authoritative_source') or {}
    if not source or request.get('source_sha256') != source.get('sha256'):
        raise ValueError('对象请求缺少同版原图source_sha256')
    mode = request.get('measurement_mode')
    if mode not in ('plan', 'spatial'):
        raise ValueError('measurement_mode须明确plan或spatial')
    e.refs(request.get('measurement_basis_source_ids'), 'measurement_basis_source_ids')
    tolerance = finite(request.get('connection_tolerance_m'), 'connection_tolerance_m')
    if tolerance > 0.01:
        raise ValueError('connection_tolerance_m不得超过0.01m；未画连接须独立列依据')
    sheets = identity_rows(request.get('sheets'), 'sheets', key='sheet_id')
    for sh in sheets.values():
        required(sh.get('floor'), 'sheet.floor')
        box = sh['bounds']
        if not isinstance(box, list) or len(box) != 4 or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError('sheet.bounds须为明确原图坐标[xmin,ymin,xmax,ymax]')
    endpoints = identity_rows(request.get('endpoints'), 'endpoints')
    endpoint_points, endpoint_errors = {}, {}
    for key, value in endpoints.items():
        try:
            endpoint_points[key] = endpoint(e, value, mode, tolerance)
        except ValueError as exc:
            endpoint_errors[key] = str(exc)
    trace = None
    if request.get('trace_result'):
        raw = request['trace_result']
        path = Path(required(raw.get('path'), 'trace_result.path'))
        if routes.sha256(path) != raw.get('sha256'):
            raise ValueError('trace_result文件指纹不一致')
        trace = routes.load_json(path)
        if trace.get('schema') != routes.RESULT_SCHEMA or trace.get('inputs', {}).get('cad_manifest_sha256') != routes.sha256(e.path) or trace.get('inputs', {}).get('source_sha256') != source['sha256']:
            raise ValueError('trace_result须为同版已画走线输出，network_model不能冒充实画')
        if mode != 'plan' or trace.get('parameters', {}).get('factor_to_output') != e.factor:
            raise ValueError('trace导入须使用相同单位的plan模式')
        if e.geometric_unit_consumption:
            if not trace.get('geometric_unit_consumption'):
                raise ValueError('几何单位对象导入缺走线实际消费凭据')
            e.dependencies.update(routes.verify_trace_geometric_consumption(trace,e.path))
        e.dependencies[str(path)] = raw['sha256']
    route_pattern = re.compile(required(request.get('route_layers'), 'route_layers'))
    objects = identity_rows(request.get('objects'), 'objects')
    if not objects:
        raise ValueError('objects须为非空数组')
    physical, good, outputs, todo = set(), {}, [], list(e.todo)
    for oid, obj in objects.items():
        pid = required(obj.get('physical_id'), oid+'.physical_id')
        if pid in physical:
            raise ValueError('同一物理对象重复认领: ' + pid)
        physical.add(pid)
        out = dict(id=oid, physical_id=pid, catalog_key='电气对象|'+pid, decision_id=obj.get('decision_id'),
                   known_path_length_each_m=None, complete_length_m=None, official_length_m=None,
                   source_steps=[], pending_components=[], status='UNRESOLVED', errors=[])
        outputs.append(out)
        try:
            e.refs(obj.get('basis_source_ids'), oid+'.basis_source_ids')
            for field in ('system', 'spec', 'start_endpoint', 'end_endpoint'):
                required(obj.get(field), oid+'.'+field)
            count = obj.get('parallel_count')
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ValueError('parallel_count须为明确正整数')
            if obj.get('kind') not in {'tray', 'trunking', 'conduit', 'busbar', 'cable', 'wire'}:
                raise ValueError('对象kind不支持')
            if count != 1 and obj['kind'] not in ('cable', 'wire'):
                raise ValueError('每个物理通道只能认领一次，parallel_count须为1')
            if count > 1:
                ids=obj.get('conductor_instance_ids')
                if not isinstance(ids,list) or len(ids)!=count or len(set(ids))!=count or any(not isinstance(i,str) or not i.strip() for i in ids):
                    raise ValueError('多根线缆须明确各根conductor_instance_ids')
                if physical & set(ids):
                    raise ValueError('多根线缆实例重复认领')
                physical.update(ids)
                out['conductor_instance_ids']=ids
            for end in ('start_endpoint', 'end_endpoint'):
                key = obj[end]
                if key not in endpoint_points:
                    raise ValueError('端点不能绑定: '+key+': '+endpoint_errors.get(key, '端点缺失'))
                if obj['kind'] in ('cable', 'wire') and endpoints[key]['kind'] != 'connection_port':
                    raise ValueError('电缆/电线起终端须引用实际实例连接口，曲线节点不能替代设备端口')
            raw_steps = obj.get('steps')
            if obj.get('trace_route_id'):
                if raw_steps is not None or trace is None:
                    raise ValueError('trace_route_id须有绑定trace_result且不同时声明steps')
                raw_steps, flags = trace_steps(e, request, obj, trace)
                out['trace_review_flags'] = flags
                if flags:
                    todo.append(dict(object_id=oid, problem='trace语义/画法复核标记保留', flags=flags))
            if not isinstance(raw_steps, list) or not raw_steps:
                raise ValueError('对象须有有序原段steps，声明长度不能替代原图')
            steps = [binding(e, row, sheets, mode) for row in raw_steps]
            if any(not route_pattern.search(str(e.byid[s['source_id']].get('layer') or '')) for s in steps):
                raise ValueError('所选原段不属于本次route_layers范围')
            previous = endpoint_points[obj['start_endpoint']]
            residuals=[]
            seen = defaultdict(list)
            for step in steps:
                residual=math.dist(previous, step['from_point_m'])
                if residual>1e-8: residuals.append(residual)
                if residual > tolerance:
                    raise ValueError('有序原段几何不连续；同名节点或图上交叉不自动相连')
                lo, hi = step['fraction_range']
                if any(min(hi, b)-max(lo, a) > 1e-12 for a, b in seen[step['part_key']]):
                    raise ValueError('同一对象重复认领原曲线区间；往返须分明物理对象')
                seen[step['part_key']].append((lo, hi))
                previous = step['to_point_m']
            residual=math.dist(previous, endpoint_points[obj['end_endpoint']])
            if residual>1e-8:residuals.append(residual)
            if residual > tolerance:
                raise ValueError('有序原段终端与实际连接口不相接')
            pending = list(obj.get('pending_components', []))
            if not isinstance(obj.get('pending_components', []), list) or any(not isinstance(v, str) or not v.strip() for v in pending):
                raise ValueError('pending_components须为文字数组')
            if residuals:
                pending.append('undrawn_connection')
                out['geometric_residuals_m']=residuals
            if not isinstance(obj.get('extras',[]),list) or any(not isinstance(x,dict)for x in obj.get('extras',[])):
                raise ValueError('extras须为对象数组')
            component_evidence = obj.get('component_evidence', {})
            if not isinstance(component_evidence,dict):
                raise ValueError('component_evidence须为对象')
            for name in COMPONENTS:
                declaration = component_evidence.get(name, {})
                if not isinstance(declaration,dict):
                    raise ValueError('component_evidence分量须为对象')
                status = declaration.get('status', 'pending')
                if status not in ('pending', 'provided', 'not_applicable'):
                    raise ValueError('component_evidence状态非法: '+name)
                if status == 'pending':
                    pending.append(name)
                else:
                    required(declaration.get('basis'), name+'.basis')
                    e.refs(declaration.get('source_ids'), name+'.source_ids')
                    if status == 'provided' and name == 'vertical' and not any(s['role'] == 'vertical' for s in steps):
                        raise ValueError('已提供vertical须有本对象实际竖段，标高推算另走T36契约')
                    if status == 'provided' and name != 'vertical' and not any(x.get('component') == name for x in obj.get('extras', [])):
                        raise ValueError('已提供分量缺少对应extras: '+name)
            for extra in obj.get('extras', []):
                e.refs(extra.get('source_ids'), 'extras.source_ids')
                if extra.get('component') not in COMPONENTS[1:]:
                    raise ValueError('附加长度须明示terminal/reserve/undrawn_connection分量')
                if extra['component'] == 'undrawn_connection':
                    raise ValueError('未画连接不能藏在端头extras中；本适配器保留待办并转路径台账分层建段')
                if extra.get('length_basis') not in ('network_model', 'assumption'):
                    raise ValueError('extras须明确network_model/assumption；实画长度只能通过原曲线steps回取')
                e.refs(extra.get('basis_ids'), 'extras.basis_ids')
            good[oid] = dict(obj=obj, steps=steps, pending=sorted(set(pending)))
            out.update(known_path_length_each_m=math.fsum(s['length_m'] for s in steps),
                       source_steps=[{k:v for k,v in s.items() if k not in ('source_part', 'original_part')} for s in steps],
                       pending_components=sorted(set(pending)), status='BOUND_REFERENCE')
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            out['errors'].append(str(exc))
            todo.append(dict(object_id=oid, problem=str(exc)))

    # Split the union of all selected parameter intervals into common atomic paths.
    cuts, parts, contexts, claims = defaultdict(set), {}, {}, defaultdict(list)
    normalization_parts={}
    protected=defaultdict(list)
    bound_exclusions=[]
    for raw in request.get('exclusions', []):
        s=binding(e,raw,sheets,mode)
        required(raw.get('reason'),'exclusions.reason')
        s['reason']=raw['reason'];bound_exclusions.append(s)
        key=s['part_key'];cuts[key].update(s['fraction_range']);parts[key]=s['source_part']
        normalization_parts[key]=s['original_part']
        protected[key].append(s['fraction_range'])
    for oid, value in good.items():
        for s in value['steps']:
            key = s['part_key']; cuts[key].update(s['fraction_range']); parts[key] = s['source_part']
            normalization_parts[key]=s['original_part']
            protected[key].append(s['fraction_range'])
            previous = contexts.get(key)
            context = (s['sheet_id'], s['floor'], s['role'])
            if previous is not None and previous != context:
                raise ValueError('同一原曲线被声明为不同图页/楼层/角色，须先核图: '+key)
            contexts[key] = context
            # A full closed part must be split before the existing ledger's node validation.
            if math.dist(point(parts[key], 0), point(parts[key], 1)) <= 1e-10:
                cuts[key].add(0.5)
    cut_maps={};normalization=[]
    for key in cuts:
        aliases,changes=normalize_cuts(normalization_parts[key],cuts[key]|{0.0,1.0},protected[key],e.factor)
        cut_maps[key]=aliases
        cuts[key]={aliases[v]for v in cuts[key]}
        if changes:
            normalization.append(dict(part_key=key,boundaries=changes))
    for oid,value in good.items():
        output=next(o for o in outputs if o['id']==oid)
        for index,s in enumerate(value['steps']):
            lo,hi=[cut_maps[s['part_key']][v]for v in s['fraction_range']]
            s['normalized_fraction_range']=[lo,hi]
            output['source_steps'][index]['normalized_fraction_range']=[lo,hi]
            claims[s['part_key']].append((oid,lo,hi))
    for key, values in claims.items():
        for i, (oid, lo, hi) in enumerate(values):
            for other, a, b in values[i+1:]:
                if oid == other or min(hi, b) <= max(lo, a):
                    continue
                one, two = good[oid]['obj'], good[other]['obj']
                if one['kind'] not in ('cable', 'wire') and two['kind'] not in ('cable', 'wire'):
                    raise ValueError('同一物理通道区间被多个通道重复认领: '+oid+'/'+other)
                if one['kind'] in ('cable', 'wire') and two['kind'] in ('cable', 'wire'):
                    common = set(one.get('channel_ids', [])) & set(two.get('channel_ids', []))
                    if not common:
                        raise ValueError('多回路共用原段须引用同一已绑定通道，不能默认合法共用: '+oid+'/'+other)

    nodes, paths, bypart = [], [], defaultdict(list)
    def node(p):
        # Bounded clusters: each accepted point must be within tolerance of every member.
        for i, group in enumerate(nodes):
            if all(math.dist(p, q) <= tolerance for q in group):
                group.append(p); return 'N-'+str(i+1)
        nodes.append([p]); return 'N-'+str(len(nodes))
    for key in sorted(cuts):
        values = sorted(cuts[key])
        for lo, hi in zip(values, values[1:]):
            if not any(a <= lo and b >= hi for _, a, b in claims[key]):
                continue
            part = slice_part(parts[key], lo, hi)
            length = geometry.measure_part(part)[0]*e.factor
            ident = 'PATH-'+key+'@'+format(lo,'.17g')+'..'+format(hi,'.17g')
            a, b = [v*e.factor for v in point(parts[key], lo)], [v*e.factor for v in point(parts[key], hi)]
            row = dict(id=ident, from_node=node(a), to_node=node(b), length_m=length,
                       role=contexts[key][2], length_basis='actual_drawn_path', basis_ids=[key], source_ids=[key],
                       source_binding=dict(source_id=key.rsplit(':',1)[0], part_index=int(key.rsplit(':',1)[1]),
                                           fraction_range=[lo,hi], sheet_id=contexts[key][0], floor=contexts[key][1]),
                       from_point_m=a, to_point_m=b)
            if row['from_node'] == row['to_node']:
                raise ValueError('曲线区间小于节点容差或环段未分开；请降低容差/显式拆段')
            paths.append(row); bypart[key].append(row)
    ledger = dict(schema='qto-electrical-path-ledger/1.0', project_id=required(request.get('project_id'),'project_id'),
                  unit='m', paths=paths, channels=[], circuits=[])
    for oid, value in good.items():
        obj, steps = value['obj'], value['steps']; refs = []; expected = 0.0
        for s in steps:
            lo, hi = s['normalized_fraction_range']
            atomic = [p for p in bypart[s['part_key']] if lo <= p['source_binding']['fraction_range'][0] and p['source_binding']['fraction_range'][1] <= hi]
            if s['direction'] == -1:
                atomic.reverse()
            refs.extend(p['id'] for p in atomic); expected += s['length_m']
        actual = math.fsum(next(p['length_m'] for p in paths if p['id']==pid) for pid in refs)
        if not math.isclose(expected, actual, abs_tol=1e-8, rel_tol=1e-9):
            raise ValueError('原曲线分段前后长度不守恒: '+oid)
        row = {k:copy.deepcopy(v) for k,v in obj.items() if k in ('id','physical_id','kind','system','spec','parallel_count','logical_circuit_id','cable_core_count','scope_id','path_scheme_id','channel_ids','extras')}
        first=next(p for p in paths if p['id']==refs[0]);last=next(p for p in paths if p['id']==refs[-1])
        start_node=first['from_node'if steps[0]['direction']==1 else'to_node']
        end_node=last['to_node'if steps[-1]['direction']==1 else'from_node']
        for x in row.get('extras', []):
            end = x.pop('endpoint_id', None)
            if end not in (obj['start_endpoint'], obj['end_endpoint']):
                raise ValueError('extras.endpoint_id须为本对象实际起终端')
            x['node_id'] = start_node if end==obj['start_endpoint']else end_node
        row.update(start_node=start_node, end_node=end_node,
                   path_ids=refs, source_ids=obj['basis_source_ids'], pending_components=value['pending'])
        ledger['circuits' if obj['kind'] in ('cable','wire') else 'channels'].append(row)
        next(o for o in outputs if o['id']==oid)['path_ids'] = refs
    quantity = calculate_ledger(ledger)
    byid = {o['id']:o for o in outputs}
    for item in quantity['items']:
        out = byid[item['id']]
        # Preserve known horizontal/3-D quantity and unknown complete total as different fields.
        out['known_length_m'] = item['length_m']
        out['complete_length_m'] = item['length_m'] if not out['pending_components'] else None
        out['quantity_level'] = item['quantity_level']
        if out['pending_components']:
            todo.append(dict(object_id=item['id'], problem='完整量缺少分量依据', pending_components=out['pending_components']))

    # Scope universe: all eligible source parts inside declared sheets, not just the selected list.
    layer = re.compile(required(request.get('route_layers'), 'route_layers'))
    universe, unsupported, unprocessed, excluded = {}, [], [], []
    source_lines=[]
    exclusions = defaultdict(list)
    for s in bound_exclusions:
        normalized=[cut_maps[s['part_key']][v]for v in s['fraction_range']]
        exclusions[s['part_key']].append(normalized)
        excluded.append(dict(part_key=s['part_key'], fraction_range=s['fraction_range'],normalized_fraction_range=normalized,
                             reason=s['reason'],basis_source_ids=s['basis_source_ids']))
    for sid, row in e.byid.items():
        if not layer.search(str(row.get('layer') or '')) or row.get('entity_type') in ('INSERT','TEXT','MTEXT','POINT','ATTRIB','ATTDEF'):
            continue
        original, problem = graph.row_parts(row)
        if problem:
            unsupported.append(dict(source_id=sid, entity_type=row.get('entity_type'), reason=problem, length_m=None))
            continue
        for index, part in enumerate(original):
            bb = graph._part_box(part)
            if not any(bb[0] <= sh['bounds'][2] and bb[3] >= sh['bounds'][0] and bb[1] <= sh['bounds'][3] and bb[4] >= sh['bounds'][1] for sh in sheets.values()):
                continue
            key = key_for(sid,index); covered = merged([(lo,hi)for _,lo,hi in claims.get(key,[])])
            if part['kind']=='line':
                source_lines.append(dict(segment_id='SEG-'+key,source_id=sid,handle=str(row.get('entity_handle')),
                    part_index=index,kind='line',layer=str(row.get('layer')or''),start=part['start'],end=part['end'],
                    length=geometry.measure_part(part)[0],part=part,bbox3d=graph._part_box(part)))
            if any(min(b,d)>max(a,c)for a,b in covered for c,d in exclusions[key]):
                raise ValueError('同一原图区间同时计量与排除: '+key)
            universe[key] = dict(used=covered, excluded=merged(exclusions[key]))
            remaining = subtract([[0,1]], covered+exclusions[key])
            if remaining:
                unprocessed.append(dict(part_key=key, fraction_ranges=remaining, reason='范围内尚未认领/排除的原段区间'))
    if set(exclusions)-set(universe):
        raise ValueError('排除项不属于本次route_layers/图页覆盖范围')
    # Reuse L4's collinear candidates; distinct source handles do not prove distinct physical channels.
    # No automatic deduction. Counted overlaps require an explicit, source-backed keep_both decision.
    flat,_=routes.flatten_elevations(source_lines,e.factor,max(tolerance,1e-9))
    cfg=routes.parse_overlaps(request,max(tolerance,1e-9))
    overlaps,apart=routes.find_overlaps(flat,dict(overlaps=cfg,factor=e.factor,snap=max(tolerance,1e-9)))
    declared={tuple(sorted(r['segment_ids'])):r for r in request.get('overlap_resolutions',[])}
    found_pairs={tuple(sorted(r['segment_ids']))for r in overlaps}
    if set(declared)-found_pairs:
        raise ValueError('overlap_resolutions引用本轮不存在的重合候选')
    for row in overlaps:
        active=[]
        for sid,(lo,hi)in row['_spans'].items():
            active.append(any(min(hi,b)>max(lo,a)for a,b in universe[sid[4:]]['used']))
        decision=declared.get(tuple(sorted(row['segment_ids'])))
        row['status']='not_both_counted'
        if all(active):
            if decision and decision.get('action')=='keep_both':
                required(decision.get('basis'),'overlap_resolutions.basis')
                e.refs(decision.get('basis_source_ids'),'overlap_resolutions.basis_source_ids')
                row.update(status='kept_both',basis=decision['basis'],basis_source_ids=decision['basis_source_ids'])
            else:
                row['status']='pending'
                todo.append(dict(problem='共线重合尚未核定为不同实物；不自动扣减',segment_ids=row['segment_ids']))
        row.pop('_spans',None)
    for row in apart:
        row.pop('_spans',None)
    if unprocessed or unsupported:
        todo.append(dict(problem='原图覆盖差集未闭合', unprocessed_count=len(unprocessed), unsupported_count=len(unsupported)))
    return dict(schema=PACKAGE, project_id=request['project_id'], status='REVIEW_REQUIRED' if todo else 'BOUND_REFERENCE',
                business_adoption_allowed=False, official_adoption=False, measurement_mode=mode,
                objects=outputs, ledger=ledger, quantity_result=quantity, todo=todo,
                overlap_review=dict(candidates=overlaps,different_elevation=apart,automatic_deduction=False),
                interval_normalization=dict(max_parameter_ulps=MAX_PARAMETER_ULPS,
                    max_physical_boundary_shift_m=MAX_BOUNDARY_SHIFT_M,changes=normalization,
                    engineering_connection_tolerance_used=False),
                coverage=dict(universe=universe, excluded=excluded, unprocessed=unprocessed, unsupported=unsupported,
                              closed=not unprocessed and not unsupported and not e.todo),
                checks=dict(geometry_remeasured=True, split_length_conserved=True, no_automatic_crossing_connection=True),
                provenance=e.provenance())


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def write_csv(path, rows, fields):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def quantity_projection(result, quantity_item, ledger_item):
    """Display the calculator's result once per physical object; never reclassify or multiply it.

    Basis/source detail comes from the same package's ledger extras. It is input evidence,
    while every numeric total, component and level is owned by quantity_result.
    """
    item=quantity_item or {}
    known=item.get('known_length_m')if 'known_length_m'in item else item.get('length_m')
    complete=item.get('length_m')if item.get('complete_quantity')is True else None
    by_basis=item.get('length_by_basis_each_m')
    drawn=by_basis.get('actual_drawn_path')if isinstance(by_basis,dict)else None
    level=item.get('quantity_level')
    metadata=dict(quantity_level=level,quantity_basis=item.get('quantity_basis'),
        basis_classification_complete=item.get('basis_classification_complete'),length_basis=item.get('length_basis'),
        known_length_m=known,complete_length_m=complete,official_length_m=None,official_adoption=False,
        parallel_count=item.get('parallel_count'),length_components_each_m=item.get('length_components_each_m'),
        length_by_basis_each_m=by_basis,drawn_path_each_m=drawn,extra_each_m=item.get('extra_each_m'),
        extra_by_basis_each_m=item.get('extra_by_basis_each_m'),
        extra_components=copy.deepcopy(ledger_item.get('extras',[]))if quantity_item and ledger_item else None,
        quantity_source_ids=item.get('source_ids'),quantity_pending_components=item.get('pending_components'),
        complete_quantity=item.get('complete_quantity'),calculation=item.get('calculation'),
        length_basis_missing_path_ids=item.get('length_basis_missing_path_ids'),
        length_basis_missing_extra_ids=item.get('length_basis_missing_extra_ids'),unresolved_path_ids=item.get('unresolved_path_ids'),
        quantity_projection_status='RESULT_PROJECTED'if quantity_item else'NO_CALCULATED_ITEM',
        quantity_scope_note='已知/完整参考长度已含根数；路径及附加组成按每根显示，不再乘根数；正式采用量未给出')
    display_level=level if isinstance(level,str)and level.strip()else'数量层级未决'
    quantities=[dict(name=name,value=value,unit='m',level=display_level)for name,value in (
        ('已知参考长度（已含根数）',known),('完整参考长度（已含根数）',complete),
        ('每根实画路径长度',drawn),('每根附加长度',item.get('extra_each_m')))]
    quantities.append(dict(name='正式采用长度',value=None,unit='m',level='未正式采用'))
    return dict(metadata=metadata,quantities=quantities)


def csv_quantity_value(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False)if value is None or isinstance(value,(dict,list,bool))else value


def project_scene_quantities(package, projections, output):
    """Geometry mapping groups several source rows; project result quantities after that grouping.

    Existing mapping forbids group_by+quantities to prevent row-wise double counting. Keep that
    protection and use the existing review-view quantities contract exactly once per catalog_key.
    """
    path=output/'复核视图.json';view=normalize_view(routes.load_json(path),output)
    before=view['result_fingerprint'];seen=set();projected=[]
    for obj in view.get('objects',[]):
        key=obj['catalog_key']
        if key not in projections:continue
        if key in seen:raise ValueError('数量投影catalog_key重复，拒绝重复计量: '+key)
        seen.add(key);projection=projections[key]
        obj['quantities']=copy.deepcopy(projection['quantities'])
        obj.setdefault('attributes',{}).update({k:csv_quantity_value(v)for k,v in projection['metadata'].items()})
        projected.append(key)
    if seen!=set(projections):raise ValueError('数量投影与对象包覆盖不一致，不能丢掉未定位对象')
    normalized=normalize_view(view,output)
    write_json(path,normalized)
    report=dict(status='PASS',quantity_changes=0,numeric_recalculation=False,parallel_count_multiplied=False,
        source='同包quantity_result；依据明细来自同包ledger.extras',projected_catalog_keys=projected,
        quantity_result_sha256=hashlib.sha256(json.dumps(package['quantity_result'],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest(),
        geometry_result_fingerprint=before,result_fingerprint=normalized['result_fingerprint'])
    write_json(output/'同源数量投影检查.json',report)
    scene_report=routes.load_json(output/'场景检查.json')
    scene_report.update(geometry_result_fingerprint=before,result_fingerprint=normalized['result_fingerprint'],quantity_projection=report)
    write_json(output/'场景检查.json',scene_report)


def export_review(package, request, e, output):
    """Feed existing scene generator: one stable catalog object, one or more geometry rows per item."""
    drawing = required(request.get('drawing_id'), 'drawing_id')
    sheets = request['sheets']; sheet_rows=[]
    for sh in sheets:
        sheet_rows.append(dict(sheet_id=sh['sheet_id'],drawing=drawing,title=sh.get('title',sh['sheet_id']),floor=sh['floor'],sheet_kind='平面',
                               **dict(zip(('x1','y1','x2','y2'),sh['bounds'])),length_factor=e.factor))
    write_csv(output/'图页.csv', sheet_rows, ['sheet_id','drawing','title','floor','sheet_kind','x1','y1','x2','y2','length_factor'])
    items = {o['id']:o for o in request['objects']}; rows=[];projections={}
    quantity_items=identity_rows(package['quantity_result'].get('items',[]),'quantity_result.items')
    ledger_items={r['id']:r for key in ('channels','circuits')for r in package['ledger'].get(key,[])}
    for result in package['objects']:
        obj=items[result['id']]; steps=next((v['source_steps'] for v in package['objects'] if v['id']==obj['id']),[])
        projection=quantity_projection(result,quantity_items.get(result['id']),ledger_items.get(result['id']))
        projections[result['catalog_key']]=projection
        for i,s in enumerate(steps or [None]):
            common=dict(object_id=obj['physical_id'], catalog_key=result['catalog_key'], drawing=drawing,
                        title=obj['id']+' '+str(obj.get('spec','')), category=obj.get('kind','未知'), status=result['status'],
                        detail=json.dumps(dict(errors=result['errors'], pending_components=result['pending_components']),ensure_ascii=False),
                        physical_id=obj['physical_id'], spec=obj.get('spec',''), parallel_count=obj.get('parallel_count',''),
                        known_length_m=result.get('known_length_m',''), complete_length_m=json.dumps(result.get('complete_length_m')),
                        path_ids=json.dumps(result.get('path_ids',[]),ensure_ascii=False), decision_id=obj.get('decision_id',''),
                        source_fingerprint=request['source_sha256'], measurement_mode=request['measurement_mode'],
                        shared_paths=json.dumps({pid:package['quantity_result']['shared_path_usage'][pid] for pid in result.get('path_ids',[])},ensure_ascii=False))
            common.update({k:csv_quantity_value(v)for k,v in projection['metadata'].items()})
            if s:
                original=graph.row_parts(e.byid[s['source_id']])[0][s['part_index']]
                lo,hi=s['fraction_range']; n=1 if original['kind']=='line' else max(8, math.ceil(abs(original['sweep_param']*(hi-lo))/(math.pi/64)))
                fractions=[lo+(hi-lo)*k/n for k in range(n+1)]
                if s['direction']==-1: fractions.reverse()
                common.update(sheet=s['sheet_id'],kind='line',points=json.dumps([point(original,t)[:2] for t in fractions]),
                              source=json.dumps(dict(source_id=s['source_id'],instance_path=s['instance_path'],part_index=s['part_index'],fraction_range=s['fraction_range'],direction=s['direction'],basis_source_ids=s['basis_source_ids']),ensure_ascii=False),
                              direction=s['direction'], source_interval=json.dumps(s['fraction_range']),
                              source_id=s['source_id'], role=s['role'], unlocated_reason='')
            else:
                common.update(sheet='',kind='unlocated',points='',source=json.dumps(obj,ensure_ascii=False),direction='',source_interval='',source_id='',role='',unlocated_reason='no_geometry')
            rows.append(common)
    if rows and not any(o['source_steps']for o in package['objects']):
        # The existing scene builder emits source identities only for used scenes. Retain the
        # declared source-page boundary as a non-quantity context object when every item is unknown.
        # This supplies a real source background, never an invented location for an unresolved item.
        for sh in sheets:
            x1,y1,x2,y2=sh['bounds'];context=dict(rows[0])
            context.update(object_id='原图范围参照|'+sh['sheet_id'],catalog_key='电气原图范围参照|'+drawing+'|'+sh['sheet_id'],
                title='原图范围参照（不计工程量）',category='原图范围参照',status='REFERENCE_ONLY',
                sheet=sh['sheet_id'],kind='line',points=json.dumps([[x1,y1],[x2,y1],[x2,y2],[x1,y2],[x1,y1]]),
                source='同版原图及请求明确的图页范围，仅供证据导航',detail='全部工程对象未定位；本外框不代表任何工程路径',
                physical_id='',spec='',parallel_count='',known_length_m='',complete_length_m='null',path_ids='[]',
                decision_id='',shared_paths='{}',direction='',source_interval='',source_id='',role='',unlocated_reason='')
            rows.append(context)
    fields=list(rows[0]) if rows else ['object_id','catalog_key','drawing','title','category','status','sheet','kind','points','source','unlocated_reason']
    write_csv(output/'对象叠加.csv',rows,fields)
    standard=('object_id','catalog_key','drawing','title','category','status','sheet','kind','points','source','detail','unlocated_reason')
    attrs={k:k for k in fields if k not in standard}
    # Existing scene mapping supports mm/m. Coordinates are converted to m for every source unit.
    transform=dict(scale=e.factor,dx=0,dy=0)
    for sh in sheet_rows:
        for axis in ('x1','y1','x2','y2'): sh[axis]*=e.factor
        sh['length_factor']=1.0
    write_csv(output/'图页.csv',sheet_rows,['sheet_id','drawing','title','floor','sheet_kind','x1','y1','x2','y2','length_factor'])
    # Neutral backgrounds use their original coordinates; keep mm/m directly to preserve background alignment.
    if math.isclose(e.factor,.001) or math.isclose(e.factor,1.):
        coordinate_unit='mm'if math.isclose(e.factor,.001)else'm'
        transform=dict(scale=1,dx=0,dy=0)
        for row,sh in zip(sheet_rows,sheets):
            row.update(dict(zip(('x1','y1','x2','y2'),sh['bounds'])));row['length_factor']=e.factor
        write_csv(output/'图页.csv',sheet_rows,['sheet_id','drawing','title','floor','sheet_kind','x1','y1','x2','y2','length_factor'])
    else:
        raise ValueError('现有scene底图接口只支持mm/m；其他单位对象包可复算，回图须先用已有中性单位转换工具转同源视图')
    mapping=dict(schema='qto-review-scene-mapping/1.0',
                 view=dict(project_id=request['project_id'],review_id=required(request.get('review_id'),'review_id'),
                           title=request.get('title','电气原图对象绑定复核'),revision=request['cad_manifest_sha256'],professional='qto-electrical-routes'),
                 drawings=[dict(id=drawing,neutral_dir=str(e.path.resolve().parent),coordinate_unit=coordinate_unit,precision=6)],
                 sheets=[dict(path='图页.csv',columns={k:k for k in sheet_rows[0]})],
                 background=dict(source=request.get('review_background','dxf')),
                 objects=[dict(key='电气原图对象',path='对象叠加.csv',columns={k:k for k in standard},
                               geometry=dict(mode='points'),transform=transform,group_by=['catalog_key'],attributes=attrs)])
    if request.get('review_workflow') is not None:
        mapping['view']['review_workflow']=copy.deepcopy(request['review_workflow'])
    write_json(output/'场景映射.json',mapping)
    shared=Path(__file__).resolve().parents[2]/'_qto-shared-evidence/scripts'
    commands=[
        [sys.executable,'-B',str(Path(__file__).with_name('calculate_electrical_routes_reference.py')),str(output/'路径台账.json')],
        [sys.executable,'-B',str(shared/'build_review_scenes_from_tables.py'),'--mapping',str(output/'场景映射.json'),'--neutral-root',str(e.path.parent),'--output',str(output/'复核视图.json'),'--report',str(output/'场景检查.json')],
        [sys.executable,'-B',str(shared/'build_qto_review_html.py'),'--input',str(output/'复核视图.json'),'--output',str(output/'电气对象复核.html'),'--local-only'],
    ]
    receipts=[]
    for command in commands:
        done=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=120)
        receipts.append(dict(command=command,exit_code=done.returncode,stdout=done.stdout,stderr=done.stderr))
        write_json(output/'调用回执.json',receipts)
        if done.returncode:
            raise ValueError('既有计算/场景/HTML入口失败，见调用回执.json')
        if Path(command[2]).name=='build_review_scenes_from_tables.py':
            project_scene_quantities(package,projections,output)
    cli_quantity=json.loads(receipts[0]['stdout'])
    if cli_quantity != package['quantity_result']:
        raise ValueError('reference CLI与对象包内计算结果不一致')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cad-manifest',required=True,type=Path)
    parser.add_argument('--request',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--build-review',action='store_true',help='调用已有scene/HTML及reference计算CLI')
    args=parser.parse_args()
    try:
        if args.output_dir.exists():
            raise ValueError('输出目录已存在，请使用新版本中文目录以保护已有批注')
        request=routes.load_json(args.request)
        e=Evidence(args.cad_manifest,request)
        result=calculate(e,request)
        result['request_provenance']=dict(path=str(args.request.resolve()),sha256=routes.sha256(args.request))
        args.output_dir.mkdir(parents=True)
        write_json(args.output_dir/'电气对象包.json',result)
        write_json(args.output_dir/'路径台账.json',result['ledger'])
        write_json(args.output_dir/'数量复算.json',result['quantity_result'])
        if args.build_review:
            export_review(result,request,e,args.output_dir)
            result['provenance']=e.provenance()
            write_json(args.output_dir/'电气对象包.json',result)
        print(json.dumps(dict(status=result['status'],objects=len(result['objects']),unresolved=sum(o['status']=='UNRESOLVED'for o in result['objects']),todo=len(result['todo']),output_dir=str(args.output_dir)),ensure_ascii=False))
        return 3 if result['todo'] else 0
    except (OSError,ValueError,KeyError,TypeError,OverflowError,subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='INPUT_REQUIRED',error=str(exc)),ensure_ascii=False));return 2


if __name__=='__main__':
    raise SystemExit(main())
