#!/usr/bin/env python3
"""Partition source-backed mixed electrical linework through the existing L1 tracer.

The model identifies objects and supply/control relationships from the source. This
adapter finds and splits their drawn paths; it never manufactures a circuit number,
conductor count, or equipment classification from proximity or a block name.
"""
from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import gzip
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from collections import defaultdict

import bind_electrical_objects as binding
import trace_drawn_routes as trace
import qto_review_contract as review_contract
from calculate_electrical_paths import calculate as calculate_ledger

REQUEST_SCHEMA = 'qto-electrical-route-network-request/1.0'
RESULT_SCHEMA = 'qto-electrical-route-network-partition/1.0'
CATEGORIES = {'lighting', 'socket', 'power', 'control', 'source', 'junction', 'unknown'}
LOAD_CATEGORIES = {'lighting', 'socket', 'power', 'control'}
RELATIONS = {'supply', 'control', 'interconnect'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def rows(value, label, key='id'):
    return binding.identity_rows(value, label, key)


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if not k.startswith('_')}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def segment_source(segment_id):
    match = re.fullmatch(r'SEG-(.+):(\d+)', segment_id)
    if not match:
        raise ValueError('原段身份不能回取: ' + segment_id)
    return match[1], int(match[2])


def connection_category(connection, objects, evidence):
    """No priority rules: opposing load classes remain a conflict."""
    declared = connection.get('category')
    ends = {objects[connection[k]]['category'] for k in ('from_object', 'to_object')}
    load = ends & LOAD_CATEGORIES
    unknown = 'unknown' in ends
    if connection['relation'] == 'control':
        inferred = 'control'
    elif len(load) == 1 and not unknown:
        inferred = next(iter(load))
    else:
        inferred = None
    if declared is not None:
        if declared not in LOAD_CATEGORIES:
            raise ValueError('connection.category须为lighting/socket/power/control或null')
        evidence.refs(connection.get('classification_source_ids'), 'classification_source_ids')
        if inferred is not None and declared != inferred:
            return None, '连接声明类别与实际端点/控制关系冲突'
        if len(load) > 1 and connection['relation'] != 'control':
            return None, '不同负荷类别混连，不能按声明或优先级覆盖端点冲突'
        return declared, None
    return inferred, None if inferred else '端点类别不足或冲突；已保留几何分段'


def partition_network(manifest_path, request, base_path=None):
    """Rebuild source geometry, paths, shared atoms and reference ledger every call.

    Mutable semantic inputs are endpoint_objects, connections, circuit_specs and
    trace_request constraints. Output identifiers derive from scope/source identity,
    not array order or names. An absent circuit_id never blocks a proven local path.
    """
    manifest_path = Path(manifest_path).resolve()
    base = Path(base_path or manifest_path.parent).resolve()
    if request.get('schema') != REQUEST_SCHEMA:
        raise ValueError('request.schema须为' + REQUEST_SCHEMA)
    scope = trace.text(request.get('scope_id'), 'scope_id')
    project = trace.text(request.get('project_id'), 'project_id')
    if request.get('cad_manifest_sha256') != trace.sha256(manifest_path):
        raise ValueError('cad_manifest_sha256不匹配，须重新取证')
    raw_trace = copy.deepcopy(request.get('trace_request'))
    if not isinstance(raw_trace, dict) or 'routes' in raw_trace:
        raise ValueError('trace_request须为现有L1公共请求参数；routes由实际关系重建')
    factor = trace.finite(raw_trace.get('unit', {}).get('factor_to_output'), 'unit.factor_to_output')
    e_req = dict(schema=REQUEST_SCHEMA, layout=raw_trace.get('layout', 'Model'),
                 unit=dict(factor_to_m=factor, basis=trace.text(raw_trace.get('unit', {}).get('source_id'), 'unit.source_id')),
                 expand_blocks=raw_trace.get('expand_blocks', []), opaque_blocks=request.get('opaque_blocks', []),
                 max_depth=raw_trace.get('max_depth', 16))
    evidence = binding.Evidence(manifest_path, e_req)
    source_hash = (evidence.manifest.get('authoritative_source') or {}).get('sha256')
    if not source_hash or request.get('source_sha256') != source_hash:
        raise ValueError('source_sha256不匹配当前权威原图')
    endpoints = rows(request.get('endpoint_objects'), 'endpoint_objects', 'object_key')
    connections = rows(request.get('connections'), 'connections')
    if not endpoints or not connections:
        raise ValueError('endpoint_objects和connections须非空')
    objects, resolved, reviews = {}, {}, []
    for key, obj in endpoints.items():
        category = obj.get('category')
        if category not in CATEGORIES:
            raise ValueError('endpoint.category非法: ' + key)
        evidence.refs(obj.get('source_ids'), key + '.source_ids')
        out = dict(object_key=key, category=category, source_ids=obj['source_ids'],
                   endpoint_binding=copy.deepcopy(obj.get('endpoint_binding')),
                   endpoint=copy.deepcopy(obj.get('endpoint')), point_m=None,
                   status='SOURCE_BOUND', official_adoption=False)
        try:
            # Actual instance port or source curve node: a plain point/insert centre
            # cannot silently replace a missing electrical connection endpoint.
            raw = obj.get('endpoint_binding')
            if not isinstance(raw, dict):
                raise ValueError('缺实际connection_port/curve_node绑定；模型继续从源图识别端点')
            point = binding.endpoint(evidence, raw, 'plan', raw_trace.get('endpoint_tolerance', 0))
            resolved[key] = {'point': point[:2]}
            out.update(point_m=point, physical_id=raw.get('physical_id'),
                       instance_path=raw.get('instance_path'), endpoint_kind=raw['kind'])
        except (ValueError, TypeError, KeyError) as exc:
            out.update(status='UNRESOLVED', reason=str(exc))
            reviews.append(dict(object_key=key, problem=str(exc)))
        objects[key] = out
    routes, parsed, connection_out = [], {}, {}
    alternatives = defaultdict(list)
    for cid, con in connections.items():
        if con.get('alternative_group_id') is not None:
            alternatives[trace.text(con['alternative_group_id'], 'alternative_group_id')].append(cid)
    for cid, con in connections.items():
        for key in ('from_object', 'to_object'):
            if con.get(key) not in objects:
                raise ValueError(cid + '.' + key + '引用不存在的对象')
        if con['from_object'] == con['to_object']:
            raise ValueError('关系起终对象相同: ' + cid)
        if con.get('relation') not in RELATIONS:
            raise ValueError('relation须为supply/control/interconnect: ' + cid)
        evidence.refs(con.get('source_ids'), cid + '.source_ids')
        circuit = con.get('circuit_id')
        if circuit is not None:
            circuit = trace.text(circuit, cid + '.circuit_id')
            evidence.refs(con.get('circuit_source_ids'), cid + '.circuit_source_ids')
        candidate_rows = rows(con.get('circuit_candidates', []), cid + '.circuit_candidates', 'circuit_id')
        for candidate in candidate_rows.values():
            evidence.refs(candidate.get('source_ids'), cid + '.circuit_candidates.source_ids')
        assignment_problem = None
        if candidate_rows and (circuit not in candidate_rows or len(candidate_rows) > 1):
            if circuit is not None and circuit in candidate_rows and con.get('circuit_selection_source_ids'):
                evidence.refs(con['circuit_selection_source_ids'], cid + '.circuit_selection_source_ids')
            else:
                assignment_problem = '回路候选尚未由源图唯一消解，不能按名称或顺序选择'
                circuit = None
        category, problem = connection_category(con, objects, evidence)
        branch = 'BRANCH-' + fingerprint([scope, con['from_object'], con['relation'], category])[:20]
        out = dict(id=cid, from_object=con['from_object'], to_object=con['to_object'],
                   relation=con['relation'], category=category, category_source_ids=con['source_ids'],
                   circuit_id=circuit, circuit_source_ids=con.get('circuit_source_ids'),
                   circuit_assignment_status='AMBIGUOUS' if assignment_problem else ('SOURCE_ASSIGNED' if circuit else 'UNLABELED'),
                   circuit_candidates=copy.deepcopy(con.get('circuit_candidates', [])),
                   alternative_group_id=con.get('alternative_group_id'),
                   branch_group_id=branch, is_electrical_circuit=False,
                   source_ids=con['source_ids'], status='UNRESOLVED', path_ids=[],
                   traced_path_length_m=None, conductor_quantity_m=None, problems=[], official_adoption=False)
        if problem:
            out['problems'].append(problem)
        if assignment_problem:
            out['problems'].append(assignment_problem)
        if len(alternatives.get(con.get('alternative_group_id'), [])) > 1:
            out['problems'].append('互斥连接解释并存，须从源图继续核定，不能合计为同时存在')
        if con['from_object'] not in resolved or con['to_object'] not in resolved:
            out['problems'].append('实际连接端点未闭合')
        else:
            route = dict(route_id=cid, **{'from': resolved[con['from_object']], 'to': resolved[con['to_object']]})
            for key in ('via_handles', 'via_basis', 'bridge_pairs'):
                if key in con:
                    route[key] = copy.deepcopy(con[key])
            if con.get('via_handles'):
                evidence.refs(con.get('via_source_ids'), cid + '.via_source_ids')
            if con.get('bridge_pairs'):
                evidence.refs(con.get('bridge_source_ids'), cid + '.bridge_source_ids')
            routes.append(route)
        parsed[cid] = con
        connection_out[cid] = out
    # L1 needs at least one route; a component query still supplies the full source
    # universe when all semantic endpoints remain unresolved.
    if not routes:
        routes = [dict(route_id='__source_universe__', **{'from': {'point': [0., 0.]}})]
    tr_request = dict(raw_trace, schema=trace.REQUEST_SCHEMA, routes=routes)
    req = trace.parse_request(tr_request, base)
    drawing = trace.Drawing(manifest_path, req)
    traced = trace.trace_routes(drawing, req)
    route_map = {r['route_id']: r for r in traced['routes'] if r['route_id'] in connections}
    acceptable = {}
    for cid, out in connection_out.items():
        r = route_map.get(cid)
        if r is None:
            continue
        out['trace'] = clean(r)
        if r['status'] != '已追踪':
            out['problems'].extend(x['problem'] for x in r.get('problems', []))
        else:
            blocking = []
            for flag in r.get('review_flags', []):
                name = flag['flag']
                if (name == '走线网络有环，取最短' and not parsed[cid].get('via_handles')) or name in ('T接两侧回路标注不同', '经过别的箱', '经过未处置的共线重合'):
                    blocking.append(name)
            if r.get('bridged_gaps'):
                blocking.append('未画连接单列，不能冒充已画公共段')
            if r.get('overlap_deducted_m'):
                blocking.append('重合扣减需重新明确原段归属，不能继承扣减量代替原段')
            out['problems'].extend(blocking)
            out['traced_path_length_m'] = r['drawn_length_m']
            # Unknown class does not erase the real measured path. Its owners stay
            # explicitly unclassified and cannot obtain conductor quantities.
            out['status'] = 'PARTITIONED_REFERENCE' if not out['problems'] else 'PARTITIONED_REVIEW'
            acceptable[cid] = not out['problems']
        if out['problems']:
            reviews.append(dict(connection_id=cid, problem='；'.join(out['problems'])))
    # Split each original source part at every walked boundary. Owner changes,
    # branches and partial common runs thus become actual parameter intervals.
    original = {s['segment_id']: s for s in traced['collected']['segments']}
    flat, _ = trace.flatten_elevations(list(original.values()), req['factor'], req['snap'] / req['factor'])
    universe = {s['segment_id']: s for s in flat}
    cuts = {sid: {0., 1.} for sid in universe}
    protected_ranges = defaultdict(list)
    parameter_bindings = []
    # Source-tagged parameter changes also create physical split boundaries.
    # Overlapping contradictory declarations are retained later as unresolved;
    # their order never determines which specification wins.
    for spec in request.get('circuit_specs', []):
        for section in rows(spec.get('segment_parameters', []), 'segment_parameters').values():
            evidence.refs(section.get('source_ids'), 'segment_parameters.source_ids')
            ranges = section.get('source_ranges', [])
            members = section.get('connection_ids', [])
            if not isinstance(ranges, list) or not isinstance(members, list) or not (ranges or members):
                raise ValueError('segment_parameters须有source_ranges或connection_ids')
            if any(cid not in connections or connections[cid].get('circuit_id') != spec.get('circuit_id') for cid in members):
                raise ValueError('segment_parameters.connection_ids须属于本回路')
            for selected in ranges:
                source_id = trace.text(selected.get('source_id'), 'source_ranges.source_id')
                evidence.refs([source_id], 'source_ranges.source_id')
                index = selected.get('part_index')
                if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                    raise ValueError('source_ranges.part_index须为非负整数')
                lo, hi = binding.span(selected.get('fraction_range'))
                sid = 'SEG-' + source_id + ':' + str(index)
                if sid not in cuts:
                    raise ValueError('参数分段原曲线不在本次子网: ' + sid)
                cuts[sid].update((lo, hi))
                protected_ranges[sid].append((lo, hi))
                parameter_bindings.append(dict(circuit_id=spec['circuit_id'],section_id=section['id'],
                    source_segment_id=sid,source_id=source_id,part_index=index,original_fraction_range=[lo,hi]))
    claims = defaultdict(list)
    for cid, r in route_map.items():
        for s in r.get('segments', []):
            sid = s['segment_id']; lo, hi = s.get('fraction_range', [0., 1.])
            cuts[sid].update((lo, hi)); claims[sid].append((cid, lo, hi))
            protected_ranges[sid].append((lo, hi))
    # Reuse the existing source-binding representation rule: both <=2 ULP and
    # <=1 nm of the original curve, with every declared positive interval protected.
    # Apply the SAME aliases to cuts, route claims and parameter applicability.
    # Raw request/source_ranges stay untouched and remain separately auditable.
    cut_aliases, normalization = {}, []
    for sid in cuts:
        aliases, changes = binding.normalize_cuts(original[sid]['part'], cuts[sid], protected_ranges[sid], req['factor'])
        cut_aliases[sid] = aliases
        cuts[sid] = {aliases[v] for v in cuts[sid]}
        claims[sid] = [(cid, aliases[lo], aliases[hi]) for cid, lo, hi in claims[sid]]
        normalization.extend(dict(source_segment_id=sid, **change) for change in changes)
    for item in parameter_bindings:
        aliases = cut_aliases[item['source_segment_id']]
        item['normalized_fraction_range'] = [aliases[v] for v in item['original_fraction_range']]
    atoms, by_segment = [], defaultdict(list)
    for sid, s in sorted(universe.items()):
        source_id, index = segment_source(sid)
        evidence.refs([source_id], '线路原段')
        for lo, hi in zip(sorted(cuts[sid]), sorted(cuts[sid])[1:]):
            if hi <= lo:
                continue
            owners = sorted({cid for cid, a, b in claims[sid] if a <= lo and b >= hi})
            categories = sorted({connection_out[x]['category'] for x in owners if connection_out[x]['category']})
            circuits = sorted({connection_out[x]['circuit_id'] for x in owners if connection_out[x]['circuit_id']})
            part = binding.slice_part(s['part'], lo, hi)
            length = binding.interval_length(s['part'], lo, hi, req['factor'])
            atom_id = 'ATOM-' + fingerprint([scope, source_id, index, lo, hi])[:24]
            original_part = original[sid]['part']
            steps = 1 if original_part['kind'] == 'line' else max(8, math.ceil(abs(original_part['sweep_param'] * (hi - lo)) / (math.pi / 64)))
            source_points = [binding.point(original_part, lo + (hi - lo) * k / steps) for k in range(steps + 1)]
            out = dict(id=atom_id, scope_id=scope, source_segment_id=sid, source_id=source_id,
                       part_index=index, instance_path=evidence.byid[source_id].get('instance_path') or source_id,
                       fraction_range=[lo, hi], length_m=length, measurement_mode='plan',
                       source_spatial_length_m=binding.interval_length(original_part, lo, hi, req['factor']),
                       connection_ids=owners, circuit_ids=circuits, categories=categories,
                       shared_drawn_segment=len(owners) > 1, physical_channel_id=None,
                       physical_channel_quantity_m=None, conductor_quantity_m=None, known_conductor_quantity_m=None,
                       from_point_m=[v * req['factor'] for v in binding.point(s['part'], lo)],
                       to_point_m=[v * req['factor'] for v in binding.point(s['part'], hi)],
                       source_points=source_points, official_adoption=False,
                       status='PARTITIONED_REFERENCE' if owners and all(acceptable.get(x, False) for x in owners) else 'UNRESOLVED')
            atoms.append(out); by_segment[sid].append(out)
    for cid, r in route_map.items():
        ordered = []
        for s in r.get('segments', []):
            lo, hi = [cut_aliases[s['segment_id']][v] for v in s.get('fraction_range', [0., 1.])]
            selected = [a for a in by_segment[s['segment_id']] if lo <= a['fraction_range'][0] and a['fraction_range'][1] <= hi]
            sid = s['segment_id']; part = universe[sid]['part']
            forward = math.dist([v * req['factor'] for v in binding.point(part, lo)][:2], s['from_point'])
            reverse = math.dist([v * req['factor'] for v in binding.point(part, hi)][:2], s['from_point'])
            if reverse < forward:
                selected.reverse()
            ordered.extend(a['id'] for a in selected)
        connection_out[cid]['path_ids'] = ordered
        if r.get('status') == '已追踪' and not r.get('overlap_deducted_m'):
            measured = math.fsum(a['length_m'] for a in atoms if a['id'] in ordered)
            if not math.isclose(measured, r['drawn_length_m'], abs_tol=1e-8, rel_tol=1e-9):
                raise ValueError('原段拆分前后长度不守恒: ' + cid)
    ledger, calculation, quantities = build_ledger(request, evidence, scope, atoms, connection_out, cut_aliases)
    project_atom_parameters(atoms, connection_out, quantities)
    unclaimed = [a['id'] for a in atoms if not a['connection_ids']]
    if unclaimed:
        reviews.append(dict(problem='源图线路尚未被有证关系认领', atom_ids=unclaimed))
    for item in quantities:
        if item['pending_fields'] or item['pending_components']:
            reviews.append(dict(circuit_id=item['circuit_id'], problem='回路数量分量未闭合', pending_fields=item['pending_fields'], pending_components=item['pending_components']))
    graph_issues = {k: traced['collected'][k] for k in ('unsupported', 'geometry_problems', 'expansion_problems', 'degenerate_parts') if traced['collected'].get(k)}
    if graph_issues or traced['overlaps']['conflicts'] or evidence.todo:
        reviews.append(dict(problem='源图覆盖/几何仍需处理', graph_issues=graph_issues,
                            overlap_conflicts=traced['overlaps']['conflicts'], evidence_todo=evidence.todo))
    groups = defaultdict(list)
    for c in connection_out.values():
        groups[c['branch_group_id']].append(c['id'])
    provenance = evidence.provenance()
    network_hash = fingerprint(dict(scope_id=scope, source_sha256=source_hash, request=request))
    return dict(schema=RESULT_SCHEMA, scope_id=scope, project_id=project,
                status='REVIEW_REQUIRED' if reviews else 'PARTITIONED_REFERENCE',
                official_adoption=False, business_adoption_allowed=False,
                source_fingerprint=source_hash, cad_manifest_sha256=trace.sha256(manifest_path),
                network_fingerprint=network_hash, provenance=provenance,
                objects=list(objects.values()), connections=list(connection_out.values()),
                branch_groups=[dict(id=k, connection_ids=sorted(v), is_electrical_circuit=False,
                                    meaning='同源端点和有证关系的类别分支组；不是推定回路号') for k, v in sorted(groups.items())],
                physical_segments=atoms, ledger=ledger, reference_calculation=calculation,
                interval_normalization=dict(max_parameter_ulps=binding.MAX_PARAMETER_ULPS,
                    max_physical_boundary_shift_m=binding.MAX_BOUNDARY_SHIFT_M, changes=normalization,
                    parameter_ranges=parameter_bindings, engineering_connection_tolerance_used=False),
                circuit_quantities=quantities, review_items=reviews, source_coverage=dict(unclaimed_atom_ids=unclaimed),
                trace_diagnostics=dict(overlaps=clean(traced['overlaps']), graph_counts=traced['graph']['counts'],
                                       graph_issues=graph_issues),
                affected_scope=dict(scope_id=scope, object_keys=sorted(objects), connection_ids=sorted(connections),
                                    source_segment_ids=sorted(universe), circuit_ids=sorted({c['circuit_id'] for c in connection_out.values() if c['circuit_id']})),
                checks=dict(rebuilt_from_current_source=True, split_length_conserved=True,
                            distance_does_not_assign_category_or_circuit=True, terminal_point_quantity_owner='qto-components-points'))


def build_ledger(request, evidence, scope, atoms, connections, cut_aliases=None):
    cut_aliases = cut_aliases or {}
    specs = rows(request.get('circuit_specs', []), 'circuit_specs', 'circuit_id')
    ledger = dict(schema='qto-electrical-path-ledger/1.0', project_id=request['project_id'], unit='m', paths=[], channels=[], circuits=[])
    by_atom = {a['id']: a for a in atoms}
    circuit_atoms = defaultdict(set)
    for c in connections.values():
        if c['circuit_id']:
            circuit_atoms[c['circuit_id']].update(c['path_ids'])
    quantities = []
    for circuit in sorted(set(specs) - set(circuit_atoms)):
        spec = specs[circuit]
        evidence.refs(spec.get('source_ids'), circuit + '.source_ids')
        quantities.append(dict(circuit_id=circuit,source_ids=spec['source_ids'],path_ids=[],
            known_drawn_path_length_m=None,parallel_count=spec.get('parallel_count'),extra_each_m=None,
            known_length_m=None,complete_length_m=None,unit='m',pending_fields=['unassigned_circuit_source_relations'],
            pending_components=spec.get('pending_components'),segment_parameters=[],
            quantity_extent='declared_drawn_plan_scope',official_adoption=False))
    parameters = ('kind', 'system', 'spec', 'parallel_count')
    for circuit, atom_ids in sorted(circuit_atoms.items()):
        spec = specs.get(circuit, {})
        if spec:
            evidence.refs(spec.get('source_ids'), circuit + '.source_ids')
        unknown = [key for key in ('extras', 'pending_components') if spec.get(key) is None]
        if isinstance(spec.get('extras'), list):
            for xid, extra in rows(spec['extras'], circuit + '.extras').items():
                for field in ('length_each_m', 'length_basis', 'basis', 'basis_ids', 'source_ids'):
                    if extra.get(field) is None:
                        unknown.append('extras:' + xid + ':' + field)
        used_connections = [c for c in connections.values() if c['circuit_id'] == circuit]
        if any(c['status'] != 'PARTITIONED_REFERENCE' for c in used_connections):
            unknown.append('proven_path_and_classification')
        if len({c['category'] for c in used_connections}) > 1:
            unknown.append('conflicting_circuit_categories')
        sections = rows(spec.get('segment_parameters', []), circuit + '.segment_parameters')
        atom_params, param_rows = {}, []
        for ident in sorted(atom_ids):
            atom = by_atom[ident]
            applicable = []
            for section in sections.values():
                members = section.get('connection_ids', [])
                ranges = []
                for raw in section.get('source_ranges', []):
                    aliases = cut_aliases.get('SEG-' + raw['source_id'] + ':' + str(raw['part_index']), {})
                    ranges.append(dict(raw, fraction_range=[aliases.get(v,v) for v in raw['fraction_range']]))
                if members and not set(atom['connection_ids']) & set(members):
                    continue
                if ranges and not any(r['source_id'] == atom['source_id'] and r['part_index'] == atom['part_index']
                    and r['fraction_range'][0] <= atom['fraction_range'][0]
                    and r['fraction_range'][1] >= atom['fraction_range'][1] for r in ranges):
                    continue
                applicable.append(section)
            value, missing = {}, []
            for key in parameters:
                explicit = [section[key] for section in applicable if key in section]
                unique = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in explicit}
                if len(unique) > 1:
                    value[key] = None; missing.append(key + ':conflicting_segment_parameters')
                else:
                    value[key] = explicit[0] if explicit else spec.get(key)
                    if value[key] is None:
                        missing.append(key)
            if not missing:
                count = value['parallel_count']
                if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                    raise ValueError('parallel_count须为正整数或null')
                if value['kind'] not in ('wire', 'cable'):
                    raise ValueError('circuit_specs.kind须为wire/cable')
                for key in ('system', 'spec'):
                    trace.text(value[key], circuit + '.' + key)
                atom_params[ident] = value
            param_rows.append(dict(path_id=ident, **value, source_ids=sorted(set(spec.get('source_ids', [])) |
                {x for section in applicable for x in section['source_ids']}),
                section_ids=[section['id'] for section in applicable], pending_fields=missing))
            unknown.extend(ident + ':' + field for field in missing)
        out = dict(circuit_id=circuit, source_ids=spec.get('source_ids'), path_ids=sorted(atom_ids),
                   known_drawn_path_length_m=math.fsum(by_atom[a]['length_m'] for a in atom_ids) if atom_ids else None,
                   parallel_count=spec.get('parallel_count'), extra_each_m=None, known_length_m=None,
                   complete_length_m=None, unit='m', pending_fields=unknown,
                   pending_components=spec.get('pending_components'), segment_parameters=param_rows,
                   quantity_extent='declared_drawn_plan_scope', official_adoption=False)
        quantities.append(out)
        # A geometry/classification or whole-object extra ambiguity is not a
        # licence to calculate only a convenient part. Parameter-local unknowns
        # can retain their known counterpart while the complete total stays null.
        global_unknown = [x for x in unknown if not x.startswith('ATOM-')]
        if global_unknown:
            continue
        pending = spec['pending_components']
        if not isinstance(pending, list) or any(not isinstance(x, str) or not x for x in pending):
            raise ValueError('pending_components须显式数组或null')
        extras = rows(spec['extras'], circuit + '.extras')
        per_atom = defaultdict(list)
        for xid, extra in extras.items():
            extra = copy.deepcopy(extra)
            con = connections.get(extra.pop('connection_id', None))
            at = extra.pop('at', None)
            if con is None or con['circuit_id'] != circuit or at not in ('from', 'to') or not con['path_ids']:
                raise ValueError('extras须定位本回路的connection_id与at=from/to')
            evidence.refs(extra.get('source_ids'), 'extras.source_ids')
            evidence.refs(extra.get('basis_ids'), 'extras.basis_ids')
            ident = con['path_ids'][0 if at == 'from' else -1]
            a = by_atom[ident]
            point = next(o for o in request['endpoint_objects'] if o['object_key'] == con[at + '_object'])
            ep = binding.endpoint(evidence, point['endpoint_binding'], 'plan', 0)
            end = 'from' if math.dist(ep, a['from_point_m']) <= math.dist(ep, a['to_point_m']) else 'to'
            extra['node_id'] = ident + ':' + end
            per_atom[ident].append(extra)
        for ident, parameter in atom_params.items():
            a = by_atom[ident]
            if not any(p['id'] == ident for p in ledger['paths']):
                ledger['paths'].append(dict(id=ident, from_node=ident + ':from', to_node=ident + ':to',
                    length_m=a['length_m'], role='horizontal', length_basis='actual_drawn_path',
                    basis_ids=[a['source_id']], source_ids=[a['source_id']],
                    source_binding={k:a[k] for k in ('source_id', 'part_index', 'instance_path', 'fraction_range')}))
            source_ids = next(v['source_ids'] for v in param_rows if v['path_id'] == ident)
            ledger['circuits'].append(dict(id=circuit + '@' + ident, physical_id=circuit + '@' + ident,
                circuit_id=circuit, scope_id=scope, **parameter,
                start_node=ident + ':from', end_node=ident + ':to', path_ids=[ident],
                source_ids=source_ids, extras=per_atom[ident], pending_components=pending))
    result = calculate_ledger(ledger)
    parent_circuit = {r['id']: r['circuit_id'] for r in ledger['circuits']}
    for out in quantities:
        items = [r for r in result['items'] if parent_circuit[r['id']] == out['circuit_id']]
        if not items:
            continue
        out['known_length_m'] = math.fsum(r['known_length_m'] for r in items)
        out['extra_each_m'] = math.fsum(r['extra_each_m'] for r in items)
        counts = {r['parallel_count'] for r in items}
        out['parallel_count'] = next(iter(counts)) if len(counts) == 1 and len(items) == len(out['path_ids']) else None
        out['parameter_varies'] = len({(r['kind'], r['spec'], r['parallel_count']) for r in items}) > 1
        if not out['pending_fields'] and not out['pending_components']:
            out['complete_length_m'] = out['known_length_m']
        for item in items:
            a = by_atom[item['path_ids'][0]]
            old = a['known_conductor_quantity_m']
            addition = item['parallel_count'] * a['length_m']
            a['known_conductor_quantity_m'] = addition if old is None else old + addition
    computed = defaultdict(set)
    for item in ledger['circuits']:
        computed[item['circuit_id']].update(item['path_ids'])
    for a in atoms:
        owners = [connections[c] for c in a['connection_ids']]
        if owners and all(c['circuit_id'] and a['id'] in computed[c['circuit_id']] for c in owners):
            a['conductor_quantity_m'] = a['known_conductor_quantity_m']
    return ledger, result, quantities


def project_atom_parameters(atoms, connections, quantities):
    """Make local parameter/quantity gaps visible on each actual source atom."""
    by_circuit = {q['circuit_id']: q for q in quantities}
    for atom in atoms:
        parameters, fields, components, details = [], [], [], []
        owner_circuits = sorted({connections[c]['circuit_id'] for c in atom['connection_ids'] if connections[c]['circuit_id']})
        for circuit in owner_circuits:
            quantity = by_circuit[circuit]
            local = next((row for row in quantity['segment_parameters'] if row['path_id'] == atom['id']), None)
            if local is not None:
                parameters.append(dict(circuit_id=circuit, **copy.deepcopy(local)))
                for field in local['pending_fields']:
                    fields.append(circuit + ':' + field)
                    reason = '同一原段区间存在互相冲突的参数声明' if 'conflicting_segment_parameters' in field else '该原段区间缺少已证参数值'
                    details.append(dict(circuit_id=circuit, field=field, reason=reason))
            for field in quantity['pending_fields']:
                if field.startswith('ATOM-'):
                    continue
                fields.append(circuit + ':' + field)
                details.append(dict(circuit_id=circuit, field=field, reason='本回路该字段或归属依据未闭合；完整量保持未知'))
            if quantity['pending_components'] is None:
                components.append('unknown_components')
                details.append(dict(circuit_id=circuit, field='pending_components', reason='完整量分量范围尚未明确'))
            else:
                for name in quantity['pending_components']:
                    components.append(name)
                    details.append(dict(circuit_id=circuit, field=name, reason='该完整量分量仍未决；已知平面量单列'))
        for cid in atom['connection_ids']:
            connection = connections[cid]
            if connection['circuit_id'] is None:
                fields.append(cid + ':circuit_assignment')
                details.append(dict(connection_id=cid, field='circuit_assignment',
                    reason='源图关系尚无唯一回路归属；规格及根数不补默认值'))
            if connection['status'] != 'PARTITIONED_REFERENCE':
                fields.append(cid + ':source_relationship')
                details.append(dict(connection_id=cid, field='source_relationship', reason='；'.join(connection['problems'])))
        if not atom['connection_ids']:
            fields.append('source_relationship')
            details.append(dict(field='source_relationship', reason='原段尚无已证连接归属'))
        atom.update(segment_parameters=parameters, pending_fields=sorted(set(fields)),
                    pending_components=sorted(set(components)), pending_details=details,
                    geometry_status=atom['status'],
                    quantity_status='REVIEW_REQUIRED' if fields or components or atom['status'] != 'PARTITIONED_REFERENCE' else 'PARTITIONED_REFERENCE')


def write_json(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def write_csv(path, values, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(values)


def export_review(manifest_path, request, result, output):
    """Real source geometry -> existing scene builder -> existing HTML, no UI fork."""
    sheets = request.get('sheets')
    if not isinstance(sheets, list) or not sheets:
        raise ValueError('--build-review需要原图坐标的sheets图页/楼层/范围')
    factor = request['trace_request']['unit']['factor_to_output']
    if factor not in (1., .001):
        raise ValueError('现有底图只支持m/mm，先同源转换视图')
    drawing = trace.text(request['trace_request'].get('drawing_id'), 'drawing_id')
    sheet_rows = []
    for sh in sheets:
        values = sh['bounds']
        if len(values) != 4 or values[0] >= values[2] or values[1] >= values[3]:
            raise ValueError('sheets.bounds无效')
        sheet_rows.append(dict(sheet_id=sh['sheet_id'], drawing=drawing, title=sh.get('title', sh['sheet_id']),
            floor=sh['floor'], sheet_kind='平面', **dict(zip(('x1','y1','x2','y2'),values)), length_factor=factor))
    rows_out = []
    for atom in result['physical_segments']:
        points = atom['source_points']
        sh = next((sh for sh in sheets if all(sh['bounds'][0] - 1e-8 <= p[0] <= sh['bounds'][2] + 1e-8 and sh['bounds'][1] - 1e-8 <= p[1] <= sh['bounds'][3] + 1e-8 for p in points)), None)
        row = dict(object_id=atom['id'], catalog_key='电气混连原段|' + atom['id'], drawing=drawing,
            title=atom['source_segment_id'], category='共用已画段' if atom['shared_drawn_segment'] else '已画分支段',
            status=atom['quantity_status'], sheet=sh['sheet_id'] if sh else '', kind='line' if sh else 'unlocated',
            points=json.dumps([p[:2] for p in points]) if sh else '',
            source=json.dumps({k:atom[k] for k in ('source_id','instance_path','part_index','fraction_range')}, ensure_ascii=False),
            detail=json.dumps(dict(连接归属=atom['connection_ids'],回路归属=atom['circuit_ids'],类别=atom['categories'],
                分段参数=atom['segment_parameters'],已知导线量_m=atom['known_conductor_quantity_m'],完整导线量_m=atom['conductor_quantity_m'],
                未决字段=atom['pending_fields'],未决分量=atom['pending_components'],未决原因=atom['pending_details']),ensure_ascii=False),
            segment_parameters=json.dumps(atom['segment_parameters'],ensure_ascii=False),
            known_conductor_quantity_m=json.dumps(atom['known_conductor_quantity_m']),
            conductor_quantity_m=json.dumps(atom['conductor_quantity_m']),
            pending_fields=json.dumps(atom['pending_fields'],ensure_ascii=False),
            pending_components=json.dumps(atom['pending_components'],ensure_ascii=False),
            pending_details=json.dumps(atom['pending_details'],ensure_ascii=False),
            geometry_status=atom['geometry_status'],quantity_status=atom['quantity_status'],
            unlocated_reason='' if sh else 'no_geometry')
        rows_out.append(row)
    for con in result['connections']:
        if con['path_ids']:
            continue
        rows_out.append(dict(object_id='关系|' + con['id'], catalog_key='电气混连关系|' + con['id'], drawing=drawing,
            title=con['id'], category='连接待识别', status='UNRESOLVED', sheet='', kind='unlocated', points='',
            source=json.dumps(con['source_ids'], ensure_ascii=False), detail=json.dumps(con, ensure_ascii=False), unlocated_reason='no_geometry'))
    if not rows_out:
        raise ValueError('没有可回图对象')
    fields = list(rows_out[0])
    standard = ('object_id','catalog_key','drawing','title','category','status','sheet','kind','points','source','detail','unlocated_reason')
    for row in rows_out:
        for field in fields:
            row.setdefault(field, '')
    write_csv(output / '网络叠加.csv', rows_out, fields)
    write_csv(output / '图页.csv', sheet_rows, list(sheet_rows[0]))
    mapping = dict(schema='qto-review-scene-mapping/1.0',
        view=dict(project_id=request['project_id'], review_id=request.get('review_id', '电气混连网络'),
                  title=request.get('title', '电气混连网络拆分复核'), revision=result['network_fingerprint'], professional='qto-electrical-routes'),
        drawings=[dict(id=drawing, neutral_dir=str(Path(manifest_path).resolve().parent), coordinate_unit='m' if factor == 1 else 'mm', precision=6)],
        sheets=[dict(path='图页.csv', columns={k:k for k in sheet_rows[0]})], background=dict(source='dxf'),
        objects=[dict(key='电气混连网络', path='网络叠加.csv', columns={k:k for k in standard}, attributes={k:k for k in fields if k not in standard}, geometry=dict(mode='points'),
                      transform=dict(scale=1,dx=0,dy=0), group_by=['catalog_key'])])
    write_json(output / '场景映射.json', mapping)
    shared = Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'
    commands = [
        [sys.executable, '-B', str(Path(__file__).with_name('calculate_electrical_routes_reference.py')), str(output / '路径台账.json')],
        [sys.executable, '-B', str(shared / 'build_review_scenes_from_tables.py'), '--mapping', str(output / '场景映射.json'), '--neutral-root', str(Path(manifest_path).parent), '--output', str(output / '复核视图.json'), '--report', str(output / '场景检查.json')],
        [sys.executable, '-B', str(shared / 'build_qto_review_html.py'), '--input', str(output / '复核视图.json'), '--output', str(output / '电气混连网络复核.html'), '--local-only']]
    receipts = []
    for command in commands:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=120)
        receipts.append(dict(command=command, exit_code=done.returncode, stdout=done.stdout, stderr=done.stderr))
        write_json(output / '调用回执.json', receipts)
        if done.returncode:
            raise ValueError('既有reference/scene/HTML调用失败，见调用回执.json')
        if Path(command[2]).name == 'build_review_scenes_from_tables.py':
            # CSV attributes are text. Restore the same trusted atom values as
            # typed scene attributes before the existing HTML builder reads them.
            # In particular, unknown numeric quantities remain JSON null.
            scene = trace.load_json(output / '复核视图.json')
            report_path = output / '场景检查.json'
            original_report = trace.load_json(report_path)
            initial = review_contract.normalize_view(scene, output)
            initial_fp = initial['result_fingerprint']
            if original_report.get('result_fingerprint') != initial_fp:
                raise ValueError('原始scene检查与投影前视图指纹不一致')
            original_report_path = output / '场景生成原检查.json'
            original_report_path.write_bytes(report_path.read_bytes())
            atoms_by_key = {'电气混连原段|' + a['id']: a for a in result['physical_segments']}
            for obj in scene.get('objects', []):
                atom = atoms_by_key.get(obj.get('catalog_key'))
                if atom is None:
                    continue
                obj.setdefault('attributes', {}).update({key: copy.deepcopy(atom[key]) for key in (
                    'segment_parameters', 'known_conductor_quantity_m', 'conductor_quantity_m',
                    'pending_fields', 'pending_components', 'pending_details', 'geometry_status', 'quantity_status')})
            normalized = review_contract.normalize_view(scene, output)
            write_json(output / '复核视图.json', normalized)
            # The original scene checks describe CSV-to-scene conversion before
            # typed projection. Preserve that report verbatim and bind the final
            # report explicitly to the post-projection shared normalization.
            original_report.update(status='VIEW_NORMALIZED_PENDING_HTML',
                result_fingerprint=normalized['result_fingerprint'],
                size_estimate=trace.sheet_tables.size_estimate(normalized),
                projection_check=dict(status='VIEW_NORMALIZED_PENDING_HTML',
                    normalization='qto_review_contract.normalize_view',
                    original_report=dict(path=original_report_path.name, sha256=trace.sha256(original_report_path)),
                    before_result_fingerprint=initial_fp,
                    after_result_fingerprint=normalized['result_fingerprint'],
                    final_view_sha256=trace.sha256(output / '复核视图.json'),
                    steps=[dict(stage='scene_builder_checks', status='PASS', result_fingerprint=initial_fp,
                                scope='原场景生成检查；完整原报告另存'),
                           dict(stage='typed_atom_attributes_projection', status='PASS',
                                scope='同一原子的类型化参数/未知量属性回填；不计算工程量'),
                           dict(stage='shared_post_projection_normalization', status='PASS',
                                result_fingerprint=normalized['result_fingerprint'])]))
            write_json(report_path, original_report)
        if Path(command[2]).name == 'build_qto_review_html.py':
            report_path = output / '场景检查.json'
            report = trace.load_json(report_path)
            html_path = output / '电气混连网络复核.html'
            payload = re.search(r'<script\s+id="payload"[^>]*>([\s\S]*?)</script>', html_path.read_text(encoding='utf-8'))
            if payload is None:
                raise ValueError('HTML缺少公开视图载荷，不能闭合最终身份')
            embedded = review_contract.unpack_payload(gzip.decompress(base64.b64decode(payload[1])))
            normalized_html = review_contract.normalize_view(embedded, output)
            final_fp = report['projection_check']['after_result_fingerprint']
            closed = final_fp == embedded.get('result_fingerprint') == normalized_html['result_fingerprint']
            check = report['projection_check']
            check.update(status='PASS' if closed else 'FAIL', html_result_fingerprint=embedded.get('result_fingerprint'),
                html_normalized_result_fingerprint=normalized_html['result_fingerprint'],
                html_sha256=trace.sha256(html_path), identity_closed=closed)
            check['steps'].append(dict(stage='html_shared_identity_check', status='PASS' if closed else 'FAIL',
                result_fingerprint=normalized_html['result_fingerprint'],
                scope='公共unpack_payload与normalize_view核对最终view/HTML/场景检查身份'))
            report['status'] = 'PASS' if closed else 'FAIL'
            write_json(report_path, report)
            if not closed:
                raise ValueError('最终视图、HTML规范身份与场景检查指纹不闭合')
    if json.loads(receipts[0]['stdout']) != result['reference_calculation']:
        raise ValueError('reference CLI与内存计算不一致')


def main():
    parser = argparse.ArgumentParser(description='按源图已识别对象及关系拆分电气混连网络')
    parser.add_argument('--cad-manifest', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--build-review', action='store_true')
    args = parser.parse_args()
    try:
        request = trace.load_json(args.request)
        result = partition_network(args.cad_manifest, request, args.request.parent)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / '混连网络拆分结果.json', result)
        write_json(args.output_dir / '路径台账.json', result['ledger'])
        if args.build_review:
            export_review(args.cad_manifest, request, result, args.output_dir)
        print(json.dumps(dict(status=result['status'], network_fingerprint=result['network_fingerprint'],
                              connections=len(result['connections']), physical_segments=len(result['physical_segments']),
                              review_items=len(result['review_items']), output_dir=str(args.output_dir)), ensure_ascii=False))
        return 3 if result['review_items'] else 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(json.dumps(dict(status='INPUT_REQUIRED', error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
