"""Prove riser order with actual frozen system-diagram paths and source labels.

The diagram is a topology witness. Its symbolic geometry never supplies a
physical length, elevation, section, or omitted floor.
"""
from __future__ import annotations
from collections import defaultdict
import math
from pathlib import Path
import re

from duct_source_recovery import annotation_network_scope, distance_to_line, source_curve_contact_possible, source_curve_point


def source_identity(raw, rows):
    if not isinstance(raw, dict) or not raw.get('basis') or not raw.get('source_locator'):
        raise ValueError('系统图端口身份缺冻结文字、语义依据或定位')
    row = rows.get(raw.get('source_key'))
    if row is None or row.get('entity_type') not in {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}:
        return None, '系统图端口身份文字源实例缺失'
    if row.get('text') != raw.get('expected_text'):
        raise ValueError('系统图端口身份文字与冻结源不同')
    found = re.fullmatch(raw.get('pattern', ''), row['text'])
    fields = ('scope_id', 'system', 'physical_id', 'floor_id')
    if not found or any(not found.groupdict().get(k) for k in fields):
        return None, '系统图实际端口文字缺系统/物理身份/楼层，不以配置值补齐'
    values = found.groupdict()
    return {**values, 'source_key': raw['source_key'], 'source_text': row['text'],
            'basis': raw['basis'], 'source_locator': raw['source_locator']}, None


def route_graph(route_keys, rows, tolerance):
    if not isinstance(route_keys, list) or not route_keys or len(route_keys) != len(set(route_keys)):
        raise ValueError('系统图连续路径须声明不重复的完整源实例')
    if isinstance(tolerance, bool) or not isinstance(tolerance, (float, int)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('系统图节点容差须为有限非负数')
    parts = []
    for key in route_keys:
        row = rows.get(key)
        if row is None: raise ValueError('系统图连续路径源实例不存在')
        geo = row.get('geometry') or {}
        if row.get('geometry_issues') or not geo.get('parts') or any(p.get('kind') != 'line' for p in geo['parts']):
            return None, '系统图路径须为已完整恢复的LINE/直线多段线；曲线路径节点化尚未实现'
        for index, part in enumerate(geo['parts']):
            if math.dist(part['start'], part['end']) <= 0:
                return None, '系统图连续路径含零长part'
            parts.append((key + '#' + str(index), part))
    layers = {rows[key].get('layer') for key in route_keys}
    omitted = []
    for key, row in rows.items():
        if key in route_keys or row.get('layer') not in layers: continue
        if row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF','INSERT'} and (
                row.get('geometry_issues') or not (row.get('geometry') or {}).get('parts')):
            return None, '同源路径图层存在几何未完整恢复的实例，不能证明没有遗漏连接分支：' + key
        for candidate in (row.get('geometry') or {}).get('parts', []):
            if candidate.get('kind') not in {'line','ellipse_arc'}:
                return None, '同源路径图层存在尚未实现连接判定的源曲线，不能裁掉：' + key
            if any(any(source_curve_contact_possible(end, part, tolerance) for end in (source_curve_point(candidate,0.),source_curve_point(candidate,1.)))
                   or any(source_curve_contact_possible(end, candidate, tolerance) for end in (part['start'], part['end']))
                   for _, part in parts):
                omitted.append(key); break
    if omitted:
        return None, '同源路径图层有连接但未纳入的实例，不能裁掉支路或重复路径：' + ','.join(sorted(omitted))
    ends = [p[end] for _, p in parts for end in ('start', 'end')]
    nodes = []; original_points = []; edges = []; adjacency = defaultdict(set)
    def node(point):
        hits = [index for index, value in enumerate(nodes) if math.dist(value, point) <= tolerance]
        if len(hits) > 1: raise ValueError('系统图路径端点容差导致多个节点候选')
        if hits:
            index = hits[0]
            if any(math.dist(value, point) > tolerance for value in original_points[index]):
                raise ValueError('系统图路径端点存在传递吸附歧义')
            original_points[index].append(point); return index
        nodes.append(list(point)); original_points.append([point]); return len(nodes) - 1
    for key, part in parts:
        direction = [b - a for a, b in zip(part['start'], part['end'])]
        square = sum(v * v for v in direction); cuts = {0., 1.}
        for endpoint in ends:
            fraction = sum((v - a) * d for v, a, d in zip(endpoint, part['start'], direction)) / square
            if 0 < fraction < 1 and distance_to_line(endpoint, part) <= tolerance: cuts.add(fraction)
        cuts = sorted(cuts)
        for lo, hi in zip(cuts, cuts[1:]):
            a, b = [node([v + t * d for v, d in zip(part['start'], direction)]) for t in (lo, hi)]
            if a == b: return None, '系统图路径节点容差吞掉非零part'
            edges.append({'a': a, 'b': b, 'source_part_key': key, 'parameter_interval': [lo, hi]})
            adjacency[a].add(b); adjacency[b].add(a)
    seen = set(); todo = [0]
    while todo:
        current = todo.pop()
        if current in seen: continue
        seen.add(current); todo.extend(adjacency[current] - seen)
    terminals = [index for index in adjacency if len(adjacency[index]) == 1]
    if len(seen) != len(nodes) or len(edges) != len(nodes) - 1 or len(terminals) != 2 or any(len(v) > 2 for v in adjacency.values()):
        return None, '系统图源路径不是完整单一无环无分叉链；中间层连续性未证'
    ordered = []; previous = None; current = min(terminals)
    while current is not None:
        ordered.append(current); following = adjacency[current] - ({previous} if previous is not None else set())
        previous, current = current, next(iter(following)) if following else None
    return {'nodes': nodes, 'edges': edges, 'ordered_nodes': ordered, 'terminals': terminals}, None


def build_continuity(config, base, groups, protected, duct):
    """Return source-backed ordered observations per physical identity."""
    specs = config.get('continuity_diagrams', [])
    if not isinstance(specs, list): raise ValueError('continuity_diagrams须为数组')
    proofs = {}; pending = []; bindings = []; attempted = set()
    for spec in specs:
        manifest, manifest_ref = duct.read_ref(spec.get('manifest'), base)
        manifest_path = Path(manifest_ref['path']); protected.append(manifest_path)
        layout = duct.text(spec.get('layout'), 'continuity.layout')
        if not spec.get('basis') or not spec.get('source_locator'):
            raise ValueError('系统图连续路径缺语义依据或源定位')
        stream, blocks, manifest = duct.adapter.source_from_manifest(manifest_path)
        protected.extend([stream, blocks, Path(manifest['authoritative_source']['path']), Path(manifest['exchange_dxf']['path'])])
        failures = []
        rows = list(duct.adapter.expanded_manifest_rows(stream, blocks, manifest, 32, failures, target_layout=layout))
        by_key = {duct.source_key(row): row for row in rows}
        if len(by_key) != len(rows): raise ValueError('系统图完整源实例身份重复')
        binding = {'manifest': manifest_ref, 'authoritative_source': manifest['authoritative_source'],
                   'exchange_dxf': manifest['exchange_dxf'], 'layout': layout, 'expansion_diagnostics': failures}
        bindings.append(binding)
        declared_annotations = spec.get('port_annotations', [])
        if isinstance(declared_annotations, list):
            for annotation in declared_annotations:
                value, _ = source_identity(annotation.get('identity_annotation'), by_key)
                if value: attempted.add((value['scope_id'], value['system'], value['physical_id']))
        graph, reason = route_graph(spec.get('route_source_keys'), by_key, spec.get('snap_tolerance'))
        if reason:
            pending.append({'continuity_manifest': manifest_ref, 'reason': reason, 'length': None}); continue
        annotations = spec.get('port_annotations')
        if not isinstance(annotations, list) or len(annotations) < 2:
            pending.append({'continuity_manifest': manifest_ref, 'reason': '系统图实际路径缺至少两个有源端口身份', 'length': None}); continue
        ports = []; used = set(); scope_error = None
        for annotation in annotations:
            identity, reason = source_identity(annotation.get('identity_annotation'), by_key)
            if reason: scope_error = reason; break
            if identity['source_key'] in used: raise ValueError('系统图同一端口身份文字重复输入')
            used.add(identity['source_key'])
            leader = annotation.get('leader_network')
            if not isinstance(leader, dict) or leader.get('target_contact') != 'source_curve_node':
                scope_error = '系统图端口须有完整实际引线至真实路径节点'; break
            scope = annotation_network_scope({'annotation_binding': {'source_key': identity['source_key'], 'leader_network': leader},
                        'selector': {'source_keys': spec['route_source_keys']}}, {'annotation_network': leader}, by_key, duct.match, duct.source_key)
            locations = scope.get('target_node_bindings', [])
            if scope['status'] != 'unique_source_leader_tree' or len(locations) != 1:
                scope_error = '系统图端口引线未唯一对应一个完整路径节点'; break
            hits = [index for index, value in enumerate(graph['nodes']) if math.dist(value, locations[0]['point']) <= spec['snap_tolerance']]
            if len(hits) != 1:
                scope_error = '系统图端口节点与实际路径节点身份不唯一'; break
            ports.append({**identity, 'node': hits[0], 'leader_scope': scope})
        if scope_error:
            pending.append({'continuity_manifest': manifest_ref, 'reason': scope_error, 'length': None}); continue
        identities = {(p['scope_id'], p['system'], p['physical_id']) for p in ports}
        if len(identities) != 1:
            pending.append({'continuity_manifest': manifest_ref, 'reason': '单一源路径连接了不同系统或不同物理立管身份，不能合并', 'length': None}); continue
        identity = next(iter(identities)); observations = groups.get(identity, [])
        # All source labels with this identity must be present, including labels
        # whose leaders were not selected. Selecting a subset cannot close it.
        patterns = {a['identity_annotation'].get('pattern') for a in annotations}
        omitted_labels = []
        for key, row in by_key.items():
            if key in used or row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}: continue
            for pattern in patterns:
                hit = re.fullmatch(pattern or '', str(row.get('text', '')))
                if hit and tuple(hit.groupdict().get(k) for k in ('scope_id','system','physical_id')) == identity:
                    omitted_labels.append(key); break
        if omitted_labels:
            pending.append({'physical_identity': list(identity), 'reason': '系统图还有未纳入的同物身份文字，不能裁掉楼层或端口', 'length': None}); continue
        floors = [p['floor_id'] for p in ports]
        if len(floors) != len(set(floors)) or len({p['node'] for p in ports}) != len(ports):
            pending.append({'physical_identity': list(identity), 'reason': '系统图同楼层多端口或多身份共节点尚未证明，不能合并', 'length': None}); continue
        if sorted(floors) != sorted(o['floor_id'] for o in observations):
            pending.append({'physical_identity': list(identity), 'reason': '系统图实际端口与平面源投影楼层未逐项完整对应', 'length': None}); continue
        node_positions = {node: index for index, node in enumerate(graph['ordered_nodes'])}
        ports.sort(key=lambda port: node_positions[port['node']])
        if {ports[0]['node'], ports[-1]['node']} != set(graph['terminals']):
            pending.append({'physical_identity': list(identity), 'reason': '系统图实际路径端点无对应源投影，不能忽略开放尾段', 'length': None}); continue
        if identity in proofs:
            raise ValueError('同一物理立管有多个连续性来源，须先裁定冲突/重复来源')
        by_floor = {o['floor_id']: o for o in observations}
        proof = {'source_sha256': manifest['authoritative_source']['sha256'], **binding,
                 'route_source_keys': spec['route_source_keys'], 'route_parts': graph['edges'],
                 'route_nodes': graph['nodes'], 'ordered_route_nodes': graph['ordered_nodes'],
                 'ordered_ports': ports, 'identity': list(identity), 'basis': spec['basis'], 'source_locator': spec['source_locator'],
                 'route_length_used_for_quantity': False, 'connection_method': 'actual_complete_unbranched_source_path_and_labelled_ports'}
        proofs[identity] = ([by_floor[p['floor_id']] for p in ports], proof)
    return proofs, pending, bindings, attempted - set(proofs)
