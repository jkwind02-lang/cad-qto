"""Pipe-only gap candidates. Geometry, DN continuity and quantity stay separate.

An extended accessory gap needs a continuous union of evidenced, transformed
instance extents. A long search radius alone can never create that bridge.
"""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path


def same_file(a, b):
    a, b = Path(a), Path(b)
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


def protect_outputs(outputs, sources):
    """Check real paths and file identities, including hardlinks and junctions."""
    outputs, sources = list(map(Path, outputs)), list(map(Path, sources))
    if any(same_file(a, b) for a in outputs for b in sources):
        raise ValueError('输出路径与输入/来源文件别名冲突')
    if any(same_file(a, b) for i, a in enumerate(outputs) for b in outputs[i+1:]):
        raise ValueError('输出文件之间路径别名冲突')


def protect_tree(directory, sources):
    """Batch output names are dynamic; reject any input inside the output tree."""
    root = Path(directory).resolve()
    sources = [Path(p) for p in sources]
    if any(p.resolve().is_relative_to(root) for p in sources):
        raise ValueError('输出目录包含输入/来源文件，不能在该目录生成批量结果')
    # Existing output aliases may point to an input located elsewhere.
    if root.exists():
        if any(same_file(p, src) for p in root.rglob('*') if p.is_file() for src in sources):
            raise ValueError('输出路径与输入/来源文件别名冲突')


def source_paths(data, input_path):
    values = data.get('protected_source_paths', [])
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError('protected_source_paths须为路径文本数组')
    parent = Path(input_path).resolve().parent
    basis = data.get('recognition_basis')
    basis_path = basis.get('ledger_path') if isinstance(basis, dict) else None
    if basis_path:
        values = values + [basis_path]
    return [Path(input_path)] + [(parent / v).resolve() for v in values]


def xyz(p):
    if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
        raise ValueError('断口覆盖坐标须为二维或三维')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p):
        raise ValueError('断口覆盖坐标须为有限数值')
    return tuple(p) if len(p) == 3 else (*p, 0.)


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def axial(p, origin, axis):
    v = sub(xyz(p), origin)
    along = dot(v, axis)
    return along, math.sqrt(max(0., dot(v, v)-along*along))


def aligned(outward, axis, tolerance):
    size = math.sqrt(dot(outward, outward))
    return size > 0 and dot(outward, axis)/size >= math.cos(math.radians(tolerance))


def box_interval(bounds, origin, axis, gap, tolerance):
    """Slab intersection with the pipe axis, not bbox diagonal projection."""
    if not isinstance(bounds, list) or len(bounds) != 6:
        raise ValueError('coverage_bounds须为输出单位的六值WCS包围框')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds):
        raise ValueError('coverage_bounds须为有限数值')
    if any(bounds[i] > bounds[i+3] for i in range(3)):
        raise ValueError('coverage_bounds最小最大值倒置')
    lo, hi = 0., gap
    for i in range(3):
        if abs(axis[i]) < 1e-12:
            if not bounds[i]-tolerance <= origin[i] <= bounds[i+3]+tolerance:
                return None
        else:
            a, b = sorted(((bounds[i]-origin[i])/axis[i], (bounds[i+3]-origin[i])/axis[i]))
            lo, hi = max(lo, a), min(hi, b)
    return (lo, hi) if hi > lo else None


def continuous_coverage(a, b, gap, accessories, system, space, tolerance):
    from pipe_accessory_geometry import polygon_axis_intervals
    origin = xyz(a)
    axis = tuple(v/gap for v in sub(xyz(b), origin))
    intervals = []
    seen = set()
    for item in accessories:
        if item.get('system') != system or item.get('space_id', 'single-view') != space:
            continue
        if item.get('coverage_bounds') is None:
            continue
        oid, source = item.get('source_object_id'), item.get('source_id')
        if not isinstance(oid, str) or not oid.strip() or not isinstance(source, str) or not source.strip():
            raise ValueError('串联附件覆盖须有逐实例source_object_id和source_id')
        if not item.get('coverage_basis'):
            raise ValueError('串联附件覆盖须记录coverage_basis')
        if oid in seen:
            raise ValueError('串联附件物理实例重复:'+oid)
        seen.add(oid)
        along, offset = axial(item['point'], origin, axis)
        if offset > tolerance or not -tolerance <= along <= gap+tolerance:
            continue
        interval = box_interval(item['coverage_bounds'], origin, axis, gap, tolerance)
        if interval:
            source_geometry=item.get('coverage_geometry') or {}
            actual=[]
            for shape in source_geometry.get('shapes',[]):
                if shape.get('kind')=='closed_polygon':
                    actual.extend(polygon_axis_intervals(shape.get('vertices'),origin,axis,gap))
            ports=source_geometry.get('point_candidates',[])
            port_offsets=[axial(p,origin,axis)[1] for p in ports]
            on_axis=not ports or max(port_offsets)<=max(tolerance,1e-8)
            proven=bool(actual) and source_geometry.get('complete_source_outline') is True and on_axis
            intervals.append({'valve_id': item.get('valve_id'), 'source_object_id': oid,
                              'source_id': source, 'coverage_basis': item['coverage_basis'],
                              'coverage_bounds': item['coverage_bounds'],
                              'axis_interval': [min(r[0] for r in actual),max(r[1] for r in actual)] if actual else list(interval),
                              'envelope_axis_interval':list(interval),'actual_axis_intervals':actual,
                              'source_geometry':source_geometry,'geometry_proven':proven,
                              'source_point_lateral_offsets':port_offsets,'source_points_on_pipe_axis':on_axis,
                              'anchor_axis_position': along,'anchor_lateral_offset': offset})
    intervals.sort(key=lambda r: (r['axis_interval'][0], r['axis_interval'][1], r['source_object_id']))
    if len(intervals) < 2:
        return None
    # Two IDs inside one large symbol cannot masquerade as a serial chain.
    effective = []
    for i, row in enumerate(intervals):
        lo, hi = row['axis_interval']
        contained = any(j != i and other['axis_interval'][0] <= lo+tolerance and other['axis_interval'][1] >= hi-tolerance
                        and (other['axis_interval'][1]-other['axis_interval'][0] > hi-lo+tolerance or j < i)
                        for j, other in enumerate(intervals))
        if not contained:
            effective.append(row)
    if len(effective) < 2:
        return None
    end, gaps, uncovered = 0., [], []
    coverage_rows=[span for row in intervals for span in row['actual_axis_intervals']]
    for lo,hi in sorted(coverage_rows):
        if lo > end+tolerance:
            uncovered.append([end, lo])
        elif lo > end:
            gaps.append([end, lo])
        end = max(end, hi)
    if end < gap-tolerance:
        uncovered.append([end, gap])
    # AABBs locate the group only. They never establish its continuous coverage.
    if min(r['envelope_axis_interval'][0] for r in intervals)>tolerance or max(r['envelope_axis_interval'][1] for r in intervals)<gap-tolerance:
        return None
    geometry_complete=all(r['geometry_proven'] for r in intervals)
    envelope_end,envelope_gaps=0.,[]
    for lo,hi in sorted(r['envelope_axis_interval'] for r in intervals):
        if lo>envelope_end+tolerance:envelope_gaps.append([envelope_end,lo])
        envelope_end=max(envelope_end,hi)
    if envelope_end<gap-tolerance:envelope_gaps.append([envelope_end,gap])
    return {'method': 'source_outline_axis_intersection_check', 'origin': list(origin),
            'axis_unit_vector': list(axis), 'gap_length': gap, 'join_tolerance': tolerance,
            'instances': intervals, 'covered_axis_end': end, 'tolerance_gaps': gaps,
            'effective_coverage_instance_ids': [r['source_object_id'] for r in effective],
            'complete': not uncovered and geometry_complete, 'uncovered_axis_intervals': uncovered,
            'uncovered_axis_length': sum(hi-lo for lo, hi in uncovered) if coverage_rows else None,
            'missing_geometry_or_port_evidence_ids':[r['source_object_id'] for r in intervals if not r['geometry_proven']],
            'envelope_uncovered_axis_intervals':envelope_gaps,
            'envelope_uncovered_axis_length':sum(hi-lo for lo,hi in envelope_gaps),
            'evidence_level': 'inferred',
            'source_object_ids': [r['source_object_id'] for r in intervals]}


def interior_obstruction(a, b, segments, excluded, system, space, tolerance, indices):
    """Reject original lines/branches touching or crossing the proposed axis."""
    a, b = xyz(a), xyz(b)
    gap = math.dist(a, b)
    axis = tuple(v/gap for v in sub(b, a))
    index = indices.get((system, space))
    query = [min(x, y)-tolerance for x, y in zip(a, b)] + [max(x, y)+tolerance for x, y in zip(a, b)]
    for sid in index.query_box(query) if index else []:
        segment = segments[sid]
        if sid in excluded or segment['system'] != system or segment.get('space_id', 'single-view') != space:
            continue
        if any(p.get('kind') != 'line' for p in (segment.get('geometry') or {}).get('parts', [])):
            # Curved route crossing cannot be resolved from its endpoint chord.
            return sid
        p, q = xyz(segment['start']), xyz(segment['end'])
        # Closest pair between the infinite bridge axis and this straight edge.
        v, w = sub(q, p), sub(p, a)
        vdot, av = dot(v, v), dot(axis, v)
        denominator = vdot-av*av
        t = max(0., min(1., (av*dot(axis, w)-dot(v, w))/denominator)) if denominator > 1e-18 else 0.
        points = [p, q, tuple(x+t*y for x, y in zip(p, v))]
        for pt in points:
            along, offset = axial(pt, a, axis)
            if tolerance < along < gap-tolerance and offset <= tolerance:
                return sid
        # Collinear original segment spanning the entire gap is also duplicate.
        ap, op = axial(p, a, axis); aq, oq = axial(q, a, axis)
        if max(op, oq) <= tolerance and min(ap, aq) < gap-tolerance and max(ap, aq) > tolerance:
            return sid
    return None


def gap_limits(parameters, unit, gap_max, snap):
    physical = {'m': 1., 'mm': 1000., 'cm': 100., 'Millimeters': 1000., 'Meters': 1.}.get(unit)
    short = parameters.get('collinear_gap_max', physical if physical else gap_max)
    serial = parameters.get('serial_accessory_gap_max', 5*physical if physical else gap_max)
    join = parameters.get('accessory_coverage_tolerance', snap)
    for name, value in (('collinear_gap_max', short), ('serial_accessory_gap_max', serial), ('accessory_coverage_tolerance', join)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(name+'须为非负有限数值（网络输出单位）')
    if join > max(snap, 1e-8):
        raise ValueError('accessory_coverage_tolerance不可大于snap_tolerance，不能放大单阀容差')
    return float(short), float(serial), float(join)


def candidates(endpoints, open_ids, segments, parameters, unit, snap, gap_max, direction_tolerance, exclusions, gap_pairs, accessories, node_segments=None, node_points=None, boundaries=(), explicit_omissions=()):
    from qto_spatial import BoundsIndex
    from qto_cad_geometry import summarize
    short, serial, join = gap_limits(parameters, unit, gap_max, snap)
    if not isinstance(accessories, list) or any(not isinstance(v, dict) for v in accessories):
        raise ValueError('valves须为对象数组')
    groups = defaultdict(list)
    for sid, segment in segments.items():
        bounds = summarize(segment['geometry'])['bbox3d']
        groups[(segment['system'], segment['space_id'])].append((sid, bounds))
    indices = {key: BoundsIndex(rows) for key, rows in groups.items()}
    chain_dns = {sid: {seed['dn'] for seed in s['seeds'] if seed['evidence_level'] != 'scenario'} for sid, s in segments.items()}
    adjacency = defaultdict(set)
    for node, members in (node_segments or {}).items():
        if len(members) != 2:
            continue
        left, right = sorted(members)
        part = segments[left]
        blocked = any(b.get('system') == part['system'] and b.get('space_id', part['space_id']) == part['space_id']
                      and b.get('point') is not None and math.dist(xyz(b['point']), node_points[node]) <= max(snap, 1e-8) for b in boundaries)
        if not blocked:
            adjacency[left].add(right); adjacency[right].add(left)
    seen_segments = set()
    for sid in segments:
        if sid in seen_segments:
            continue
        stack, members, dns = [sid], set(), set()
        while stack:
            current = stack.pop()
            if current in members:
                continue
            members.add(current); dns.update(chain_dns[current]); stack.extend(adjacency[current]-members)
        seen_segments.update(members)
        for member in members:
            chain_dns[member] = dns
    normalized_accessories = []
    for item in accessories:
        spaces = {s['space_id'] for s in segments.values() if s['system'] == item.get('system')}
        normalized_accessories.append({**item, 'space_id': item.get('space_id') or (next(iter(spaces)) if len(spaces) == 1 else None)})
    options = gap_pairs(endpoints, open_ids, segments, max(gap_max, short, serial))
    pairs, rejected = {}, []
    potential = defaultdict(set)
    for left, right_ids in options.items():
        a = endpoints[left]; sa = segments[a['segment_id']]
        for right in right_ids:
            b = endpoints[right]; sb = segments[b['segment_id']]
            if a['segment_id'] == b['segment_id'] or sa['system'] != sb['system'] or sa['space_id'] != sb['space_id']:
                continue
            pair = frozenset(((a['segment_id'], a['end_name']), (b['segment_id'], b['end_name'])))
            if pair in exclusions:
                continue
            gap = math.dist(a['point'], b['point'])
            if gap <= snap:
                continue
            explicit_omission=pair in explicit_omissions
            eligible_omission=explicit_omission and gap<=gap_max
            axis = tuple(v/gap for v in sub(b['point'], a['point']))
            oa = tuple(-v for v in sa['tangent_start']) if a['end_name'] == 'start' else sa['tangent_end']
            ob = tuple(-v for v in sb['tangent_start']) if b['end_name'] == 'start' else sb['tangent_end']
            if not aligned(oa, axis, direction_tolerance) or not aligned(ob, tuple(-v for v in axis), direction_tolerance):
                continue
            # Parallel axes must coincide within snap, independent of distance.
            ua = tuple(v/math.sqrt(dot(oa, oa)) for v in oa)
            ub = tuple(v/math.sqrt(dot(ob, ob)) for v in ob)
            if axial(b['point'], a['point'], ua)[1] > max(snap, 1e-8) or axial(a['point'], b['point'], ub)[1] > max(snap, 1e-8):
                continue
            dns = [chain_dns[sid] for sid in (a['segment_id'], b['segment_id'])]
            reason = 'known_dn_conflict' if len(dns[0] | dns[1]) > 1 else None
            coverage = continuous_coverage(a['point'], b['point'], gap, normalized_accessories, sa['system'], sa['space_id'], join) if gap <= serial else None
            if explicit_omission and not eligible_omission:
                rejected.append({'endpoints':sorted(pair),'reason':'explicit_omission_outside_gap_max','distance':gap,'gap_max':gap_max})
                # A genuine serial-symbol candidate is a distinct topology
                # route. Keep its occupancy diagnostic, never restore it by
                # borrowing the longer T25 search radius.
                if not (coverage and coverage['complete']):
                    continue
            if coverage or gap <= max(short, gap_max):
                potential[left].add(right); potential[right].add(left)
            obstruction = interior_obstruction(a['point'], b['point'], segments, {a['segment_id'], b['segment_id']}, sa['system'], sa['space_id'], snap, indices)
            if obstruction:
                reason = 'existing_pipe_or_branch_on_gap'
            if coverage and not coverage['complete'] and not eligible_omission:
                rejected.append({'endpoints': sorted(pair), 'system': sa['system'], 'space_id': sa['space_id'],
                                 'reason': reason or 'incomplete_serial_axis_coverage', 'distance': gap,
                                 'candidate_classification': '串联附件连接待核候选', 'coverage_evidence': coverage,
                                 'quantity_included': False, 'conditional_incremental_length': None if reason else gap,
                                 'quantity_level': '方案暂估量',
                                 'condition': ('已知规格或支路/既有管线冲突，须先裁决；' if reason else '') + '须补足未绘连接关系及项目计量口径；全跨距含附件轴长，不能当作净管长度'})
                continue
            kind = 'explicit_omission' if eligible_omission else 'serial_accessory' if coverage else ('collinear_gap' if gap <= short else 'legacy_gap' if gap <= gap_max else None)
            if kind is None:
                continue
            if reason:
                rejected.append({'endpoints': sorted(pair), 'reason': 'explicit_omission_dn_conflict' if eligible_omission and reason=='known_dn_conflict' else reason,
                                 'obstructing_segment_id': obstruction, 'distance': gap})
                if not (eligible_omission and reason=='known_dn_conflict'):
                    continue
            pairs[(left, right)] = {'distance': gap, 'kind': kind, 'coverage': coverage,
                                   'omission_within_gap_max':gap<=gap_max,
                                   'connectivity_included':reason!='known_dn_conflict'}
    for (left, right), item in list(pairs.items()):
        if len(potential[left]) > 1 or len(potential[right]) > 1:
            rejected.append({'endpoints': sorted((endpoints[i]['segment_id'], endpoints[i]['end_name']) for i in (left, right)),
                             'reason': 'multiple_target_endpoints', 'distance': item['distance']})
            del pairs[(left, right)]
    return pairs, rejected


def review_pending_gaps(output):
    rows = [r for r in output.get('rejected_gap_candidates', []) if r.get('candidate_classification')]
    lines = []
    if output.get('conditional_drawn_segment_length'):
        lines += ['\n## 虚拟拓扑条件下的已绘分规格方案\n',
                  '本表与不依赖虚拟桥的已绘基线互为替代，几何总长相同；依赖候选桥的DN及对应量只为方案暂估，不增加管段、不得并入规则或确认主数。\n',
                  '| 系统 | 层级 | DN | 已绘长度 | 单位 |', '|---|---|---|---:|---|']
        for system,levels in output['length_summary'].items():
            for level,values in levels.items():
                for dn,value in values.items():lines.append(f"| {system} | {level} | {dn} | {value:.12g} | {output['unit']} |")
        lines += ['', '受虚拟桥假设影响的原已绘管段：'+format(output['conditional_drawn_segment_length'],'.12g')+' '+output['unit']+'；逐段A-GAP依赖见segments，桥接新增轴长另列，两个口径不可再次相加。']
    if output.get('topology_bridges'):
        lines += ['\n## 断口拓扑与数量方案\n', '虚拟桥只改变候选连通；各数量方案互为替代，不与基线或恢复量重复相加。\n',
                  '| 桥 | 分类 | 轴长 | DN | 数量处理 | 逐实例句柄 |', '|---|---|---:|---|---|---|']
        quantity_items = {r['bridge_id']: r for r in output['bridge_quantity_items']}
        for bridge in output['topology_bridges']:
            item = quantity_items[bridge['bridge_id']]
            lines.append(f"| {bridge['bridge_id']} | {bridge['classification']} | {bridge['virtual_length']:.12g} {output['unit']} | {item['dn'] or 'DN待定'} | {item['status']}：{bridge['quantity_decision']} | {', '.join(bridge.get('occupying_source_object_ids', []))} |")
        lines += ['', '| 方案 | 增量 | 该口径合计 | 层级 |', '|---|---:|---:|---|']
        for row in output['bridge_scenarios']:
            lines.append(f"| {row['name']} | {row['incremental_length']:.12g} {output['unit']} | {row['scenario_total_length']:.12g} {output['unit']} | {row['quantity_level']} |")
    if output.get('project_inclusive_accessory_axis_length'):
        lines += ['\n## 有据补长及项目含附件轴长后的工作量\n',
                  '本表包含已绘基线；不得与基线表再次相加。含附件轴长不代表净管切割长度。\n',
                  '| 系统 | 层级 | DN | 长度 | 单位 |', '|---|---|---|---:|---|']
        for system, levels in output['reconstructed_length_summary'].items():
            for level, values in levels.items():
                for dn, value in values.items():
                    lines.append(f"| {system} | {level} | {dn} | {value:.12g} | {output['unit']} |")
        lines += ['', f"项目含附件轴长增量：{output['project_inclusive_accessory_axis_length']:.12g} {output['unit']}；图示省略恢复增量：{output['restored_omission_length']:.12g} {output['unit']}。来源与计量条款见 bridge_quantity_items。"]
    if not rows:
        return '\n'.join(lines)+'\n' if lines else ''
    lines += ['\n## 串联附件连接待核候选\n',
             '以下未建立闭合拓扑桥；全跨距只作含附件轴长的条件方案，不并入已绘基线或恢复量。\n',
             '| 端点 | 全跨距 | 未覆盖轴长 | 实例句柄 | 条件 |', '|---|---:|---:|---|---|']
    for row in rows:
        evidence = row['coverage_evidence']
        ends = ' ↔ '.join(s+'@'+e for s, e in row['endpoints'])
        uncovered='缺源轮廓证据' if evidence['uncovered_axis_length'] is None else f"{evidence['uncovered_axis_length']:.9g} {output['unit']}"
        lines.append(f"| {ends} | {row['distance']:.9g} {output['unit']} | {uncovered} | {', '.join(evidence['source_object_ids'])} | {row['condition']} |")
    return '\n'.join(lines)+'\n'
