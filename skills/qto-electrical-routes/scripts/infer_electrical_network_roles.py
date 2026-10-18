#!/usr/bin/env python3
"""Source-bound network role/code candidates; routing and quantities stay in T35.

Project role and design-code dictionaries are model interpretations of cited source
text, never a built-in device/layer dictionary. No point quantity is produced.
"""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import json
from pathlib import Path
import subprocess
import sys
import re
from collections import defaultdict

import partition_electrical_route_network as net
from qto_electrical_evidence import Evidence, text_value, graph

POINTS = Path(__file__).resolve().parents[2] / 'qto-components-points' / 'scripts'
sys.path.insert(0, str(POINTS))
import point_geometry_review

REQUEST_SCHEMA = 'qto-electrical-network-role-request/1.0'
RESULT_SCHEMA = 'qto-electrical-network-roles/1.0'
FALSES = dict(official_adoption=False, business_adoption_allowed=False, human_approved=False)
CONTEXT = ('building', 'floor', 'revision', 'scope_id', 'sheet_id')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def bound(raw, dependencies):
    if not isinstance(raw, dict):
        raise ValueError('依赖须为{path,sha256}')
    path = Path(net.trace.text(raw.get('path'), '依赖.path'))
    if not path.is_absolute() or net.trace.sha256(path) != raw.get('sha256'):
        raise ValueError('依赖路径或SHA不符，来源变更须重新识别')
    dependencies[str(path)] = raw['sha256']
    return read(path)


def stable(dependencies):
    if any(net.trace.sha256(Path(p)) != h for p, h in dependencies.items()):
        raise ValueError('本轮依赖来源已变更；拒绝旧工作决定')


def text_refs(evidence, ids, label):
    evidence.refs(ids, label)
    if any(not text_value(evidence.byid[i]) or evidence.byid[i].get('entity_type') not in ('TEXT', 'MTEXT', 'ATTRIB') for i in ids):
        raise ValueError(label + '须为当前源图可回读文字')
    return [text_value(evidence.byid[i]) for i in ids]


def evidence_for(manifest, request):
    tr = request['trace_request']
    return Evidence(manifest, dict(schema=REQUEST_SCHEMA, layout=tr.get('layout', 'Model'),
        unit=dict(factor_to_m=tr['unit']['factor_to_output'], basis=tr['unit']['source_id'],
                  declaration=copy.deepcopy(tr['unit'].get('declaration', {}))),
        expand_blocks=tr.get('expand_blocks', []), opaque_blocks=request.get('opaque_blocks', [])))


def source_range(atom):
    return {k: copy.deepcopy(atom[k]) for k in ('source_id', 'part_index', 'fraction_range')}


def selected(atom, ranges):
    return any(r.get('source_id') == atom['source_id'] and r.get('part_index') == atom['part_index'] and
               r['fraction_range'][0] <= atom['fraction_range'][0] and
               r['fraction_range'][1] >= atom['fraction_range'][1] for r in ranges)


def infer_roles(manifest_path, request):
    if request.get('schema') != REQUEST_SCHEMA:
        raise ValueError('request.schema须为' + REQUEST_SCHEMA)
    manifest_path = Path(manifest_path).resolve()
    nr = copy.deepcopy(request.get('network_request'))
    if not isinstance(nr, dict):
        raise ValueError('须提供既有T35 network_request')
    # Geometry, ports, source version, routing constraints and atomization are
    # exactly the existing engine's responsibility, including unresolved paths.
    initial = net.partition_network(manifest_path, nr)
    evidence = evidence_for(manifest_path, nr)
    dependencies = {}
    points = bound(request.get('point_instances'), dependencies)
    if points.get('schema_version') != 'qto-reconciled-instance-input/1.0':
        raise ValueError('point_instances须为点位适配器唯一实例输入')
    instances = net.rows(points.get('reconciled_instances'), 'point_instances', 'physical_id')
    receipt = points.get('geometry_review')
    review = point_geometry_review.evaluate(list(instances.values()), receipt)
    if review['status'] != 'matched' or receipt.get('source_sha256') != nr['source_sha256']:
        raise ValueError('点位geometry_review/源图指纹不匹配')
    objects = {o['object_key']: o for o in initial['objects']}
    roles = net.rows(request.get('endpoint_role_bindings', []), 'endpoint_role_bindings', 'object_key')
    role_rules = net.rows(request.get('project_role_rules', []), 'project_role_rules')
    for rule in role_rules.values():
        text_refs(evidence, rule.get('source_ids'), 'project_role_rules.source_ids')
        if not isinstance(rule.get('roles'), list) or not rule['roles'] or not isinstance(rule.get('systems'), list) or not rule['systems']:
            raise ValueError('项目角色规则须显式roles及systems数组')
        for s in (*rule['roles'], *rule['systems']):
            net.trace.text(s, '角色/系统')
    todo, endpoints = [], {}
    for key, obj in objects.items():
        row = roles.get(key)
        value = dict(object_key=key, point_physical_id=None, source_physical_id=obj.get('physical_id'),
                     instance_path=obj.get('instance_path'), point_m=obj.get('point_m'),
                     roles=[], candidate_systems=[], source_ids=[], **FALSES)
        if row:
            evidence.refs(row.get('source_ids'), 'endpoint_role_bindings.source_ids')
            inst = instances.get(row.get('point_physical_id'))
            if inst is None:
                raise ValueError('角色端点不在点位geometry_review实例集中: ' + key)
            binding = obj.get('endpoint_binding') or {}
            expected = binding.get('physical_source_id')
            if inst.get('instance_path') != expected or inst.get('source_sha256') != nr['source_sha256']:
                raise ValueError('点位实例完整路径/来源与实际端口不一致: ' + key)
            if not isinstance(row.get('roles'), list) or any(not isinstance(x, str) or not x for x in row['roles']):
                raise ValueError('roles须为显式文字数组')
            candidates = set()
            used = []
            for rule in role_rules.values():
                if set(rule['roles']).issubset(row['roles']):
                    candidates.update(rule['systems']); used.append(rule['id'])
            value.update(point_physical_id=row['point_physical_id'], instance_provenance=copy.deepcopy(inst),
                         roles=row['roles'], candidate_systems=sorted(candidates), source_ids=row['source_ids'], rule_ids=used)
        if not value['candidate_systems'] or obj['status'] == 'UNRESOLVED':
            todo.append(dict(object_key=key, problem='实际端口或有据项目系统角色未闭合'))
        endpoints[key] = value
    unknown_role_keys = set(roles) - set(objects)
    if unknown_role_keys:
        raise ValueError('角色绑定引用不存在的端点: ' + ','.join(sorted(unknown_role_keys)))
    contexts = net.rows(request.get('connection_contexts', []), 'connection_contexts', 'connection_id')
    systems = net.rows(request.get('connection_system_bindings', []), 'connection_system_bindings', 'connection_id')
    connections = {}
    for con in initial['connections']:
        cid = con['id']; ctx = contexts.get(cid, {})
        if ctx:
            evidence.refs(ctx.get('source_ids'), 'connection_contexts.source_ids')
        missing = [k for k in CONTEXT if not isinstance(ctx.get(k), str) or not ctx[k]]
        if ctx.get('scope_id') not in (None, nr['scope_id']):
            raise ValueError('connection context.scope_id不符')
        ends = [set(endpoints[con[k]]['candidate_systems']) for k in ('from_object', 'to_object')]
        candidates = ends[0] & ends[1] if all(ends) else set()
        reasons = []
        if all(ends) and not candidates:
            reasons.append('端点系统角色冲突；图层或显式声明不能覆盖')
        elif not all(ends):
            reasons.append('端点系统角色未知')
        chosen = next(iter(candidates)) if len(candidates) == 1 else None
        explicit = systems.get(cid)
        if explicit:
            evidence.refs(explicit.get('source_ids'), 'connection_system_bindings.source_ids')
            if explicit.get('system') not in candidates:
                reasons.append('显式系统与有据端点角色不相容')
                chosen = None
            else:
                chosen = explicit['system']
        if chosen is None and not reasons:
            reasons.append('多个系统候选/共用模块尚未分清')
        if missing:
            reasons.append('上下文未知:' + ','.join(missing))
        if con['status'] != 'PARTITIONED_REFERENCE':
            reasons.append('实际连接或路径仍有未决')
        out = dict(id=cid, system=chosen, candidate_systems=sorted(candidates),
                   context={k: ctx.get(k) for k in CONTEXT}, context_source_ids=ctx.get('source_ids', []),
                   circuit_id=con.get('circuit_id'), path_ids=con['path_ids'],
                   from_object=con['from_object'], to_object=con['to_object'],
                   problems=reasons, status='WORKING_CANDIDATE' if not reasons else 'REVIEW_REQUIRED', **FALSES)
        connections[cid] = out
        todo.extend(dict(connection_id=cid, problem=p) for p in reasons)
    if set(contexts) - set(connections) or set(systems) - set(connections):
        raise ValueError('连接上下文/系统绑定引用未知连接')
    codes = net.rows(request.get('design_codes', []), 'design_codes')
    for code in codes.values():
        strings = text_refs(evidence, code.get('text_source_ids'), 'design_codes.text_source_ids')
        expression = net.trace.text(code.get('expression'), 'expression')
        if not any(expression in s for s in strings):
            raise ValueError('代码表原文不含本表达式')
        if not isinstance(code.get('components'), list) or not code['components']:
            raise ValueError('代码表达式须显式components，不默认根数')
        for component in code['components']:
            net.trace.text(component.get('system'), 'component.system')
            if component.get('kind') not in ('wire', 'cable'):
                raise ValueError('component.kind须为wire/cable')
            count = component.get('parallel_count')
            if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count <= 0):
                raise ValueError('代码根数须为正整数或null')
    atoms = copy.deepcopy(initial['physical_segments'])
    byid = {a['id']: a for a in atoms}
    seeds = defaultdict(set)
    seed_evidence = defaultdict(list)
    for item in net.rows(request.get('text_edge_bindings', []), 'text_edge_bindings').values():
        evidence.refs(item.get('basis_source_ids'), 'text_edge_bindings.basis_source_ids')
        texts = text_refs(evidence, [item.get('text_source_id')], 'text_edge_bindings.text_source_id')
        code = codes.get(item.get('expression_id'))
        if not code or texts[0].strip() != code['expression']:
            raise ValueError('原边文字与指定代码表达式不符，不能按最近文字绑定')
        ranges = item.get('source_ranges')
        if not isinstance(ranges, list) or not ranges:
            raise ValueError('文字绑定须精确source_ranges')
        for r in ranges:
            evidence.refs([r.get('source_id')], 'text_edge_bindings.source_range')
            net.binding.span(r.get('fraction_range'))
        hits = [a for a in atoms if selected(a, ranges)]
        if not hits:
            raise ValueError('文字绑定未命中本轮完整原子区间')
        # A partial overlap must not silently affect an entire unsplit atom.
        if any(not any(r['source_id'] == a['source_id'] and r['part_index'] == a['part_index'] and
                       r['fraction_range'] == a['fraction_range'] for a in hits) for r in ranges):
            raise ValueError('文字绑定范围须与本轮原子边界一致；先由T35分段参数显式切分')
        for atom in hits:
            seeds[atom['id']].add(code['id']); seed_evidence[atom['id']].append(copy.deepcopy(item))
    # Reuse the shared endpoint graph. Zero snapping is intentionally conservative:
    # an unproven gap is a stop. Chords below are only endpoint carriers; neither
    # T contacts nor their lengths are consumed, and original curve lengths survive.
    carriers = []
    for a in atoms:
        p, q = a['from_point_m'], a['to_point_m']
        carriers.append(dict(segment_id=a['id'], start=p, end=q, layer='', length=a['length_m'],
            part=dict(kind='line', start=p, end=q), bbox3d=[min(p[i], q[i]) for i in range(3)] + [max(p[i], q[i]) for i in range(3)]))
    topology = graph.build_graph(carriers, snap_tolerance=0., gap_tolerance=0., t_tolerance=0.)
    node_edges = {n['node_id']: [e['segment_id'] for e in n['ends']] for n in topology['nodes']}
    adjacent = defaultdict(set)
    for ids in node_edges.values():
        if len(ids) == 2 and ids[0] != ids[1]:
            adjacent[ids[0]].add(ids[1]); adjacent[ids[1]].add(ids[0])
    def signature(a):
        owners = [connections[c] for c in a['connection_ids']]
        if not owners or any(c['status'] != 'WORKING_CANDIDATE' or c['circuit_id'] is None for c in owners):
            return None
        keys = {net.fingerprint([c['system'], c['circuit_id'], c['context']]) for c in owners}
        return next(iter(keys)) if len(keys) == 1 else None
    remaining = set(byid)
    while remaining:
        start = min(remaining); group = {start}; stack = [start]; sig = signature(byid[start])
        while stack:
            cur = stack.pop()
            for other in adjacent[cur]:
                if sig is not None and signature(byid[other]) == sig and other not in group:
                    group.add(other); stack.append(other)
        remaining -= group
        found = set().union(*(seeds[x] for x in group))
        for ident in group:
            byid[ident]['expression_ids'] = sorted(found)
            byid[ident]['expression_seed_ids'] = sorted(x for x in group if seeds[x])
            byid[ident]['code_status'] = 'BOUND_WORKING' if len(found) == 1 else ('CONFLICT' if found else 'UNRESOLVED')
            if len(found) > 1:
                todo.append(dict(edge_id=ident, problem='同度2链代码矛盾，停止采用规格并保留全部种子'))
    specs = {s['circuit_id']: s for s in nr.get('circuit_specs', [])}
    for atom in atoms:
        atom['network_roles'] = [connections[c] for c in atom['connection_ids']]
        atom['text_bindings'] = seed_evidence[atom['id']]
        atom.update(FALSES)
        if atom['code_status'] != 'BOUND_WORKING':
            todo.append(dict(edge_id=atom['id'], problem='原边代码未知或冲突'))
            if atom['code_status'] == 'CONFLICT':
                # An old global spec is not a fallback after contradictory text
                # was explicitly bound to this chain. Invalidate only its atoms
                # through the existing T35 parameter interface before recalculating.
                conflict_sources = sorted({sid for code_id in atom['expression_ids']
                    for sid in codes[code_id]['text_source_ids']} |
                    {b['text_source_id'] for seed in atom['expression_seed_ids'] for b in seed_evidence[seed]})
                for cid in atom['connection_ids']:
                    con = connections[cid]
                    if not con['circuit_id']:
                        continue
                    circuit = con['circuit_id']
                    spec = specs.setdefault(circuit, dict(circuit_id=circuit, kind=None, system=None, spec=None,
                        parallel_count=None, extras=None, pending_components=None, source_ids=[]))
                    spec['source_ids'] = sorted(set(spec.get('source_ids', [])) | set(conflict_sources))
                    spec.setdefault('segment_parameters', []).append(dict(id='T34-CONFLICT-' + atom['id'] + '-' + cid,
                        source_ranges=[source_range(atom)], connection_ids=[cid], source_ids=conflict_sources,
                        kind=None, system=con['system'], spec=None, parallel_count=None))
            continue
        code = codes[atom['expression_ids'][0]]
        for cid in atom['connection_ids']:
            con = connections[cid]
            if con['status'] != 'WORKING_CANDIDATE' or not con['circuit_id']:
                continue
            components = [c for c in code['components'] if c['system'] == con['system']]
            if len(components) != 1:
                todo.append(dict(edge_id=atom['id'], connection_id=cid, problem='代码系统成分不能唯一对应实际回路'))
                continue
            circuit = con['circuit_id']
            spec = specs.setdefault(circuit, dict(circuit_id=circuit, kind=None, system=None, spec=None,
                parallel_count=None, extras=None, pending_components=None, source_ids=[]))
            evidence_ids = sorted(set(code['text_source_ids']) | {b['text_source_id'] for s in atom['expression_seed_ids'] for b in seed_evidence[s]})
            spec['source_ids'] = sorted(set(spec.get('source_ids', [])) | set(evidence_ids))
            values = copy.deepcopy(components[0])
            for field in ('kind', 'system', 'spec', 'parallel_count'):
                if spec.get(field) is not None and spec[field] != values.get(field):
                    values[field] = None
                    todo.append(dict(edge_id=atom['id'], connection_id=cid, problem='代码与已有回路参数冲突:' + field))
            spec.setdefault('segment_parameters', []).append(dict(id='T34-' + atom['id'] + '-' + cid,
                source_ranges=[source_range(atom)], connection_ids=[cid], source_ids=evidence_ids, **values))
    nr['circuit_specs'] = list(specs.values())
    # No circuit numbers or conductor identities are inferred from roles/codes.
    final = net.partition_network(manifest_path, nr)
    if review.get('work_quantity_ready') is not True:
        todo.append(dict(problem='点位几何复核有局部未决，来源完整性不升级', geometry_review=review))
    stable(dependencies)
    provenance = evidence.provenance()
    provenance['dependency_fingerprints_before'].update(dependencies)
    provenance['dependency_fingerprints_after'].update(dependencies)
    region_complete = request.get('is_region_complete', nr.get('is_region_complete'))
    if region_complete is not None and not isinstance(region_complete, bool):
        raise ValueError('is_region_complete须为布尔或null，不默认声明局部完整')
    unit_request = nr['trace_request']['unit']
    unit_disclosure = dict(original_drawing_unit=copy.deepcopy(evidence.manifest.get('drawing_unit')),
        unit_basis=provenance['unit_basis'], checked_unit_declaration=copy.deepcopy(provenance.get('checked_unit_declaration')),
        unit_basis_type='source_annotation_and_geometry_rule' if provenance.get('checked_unit_declaration') is not None else 'drawing_header_unit',
        unit_declaration=copy.deepcopy(unit_request.get('declaration')), unit_factor_to_m=evidence.factor)
    scope_disclosure = dict(scope_id=nr['scope_id'], layout=nr['trace_request'].get('layout', 'Model'),
        declared_sheets=copy.deepcopy(nr.get('sheets', [])), scope_statement=nr.get('scope_statement'),
        is_region_complete=region_complete, completeness_is_model_declaration=True)
    return dict(schema=RESULT_SCHEMA, project_id=nr['project_id'], scope_id=nr['scope_id'],
        source_sha256=nr['source_sha256'], cad_manifest_sha256=nr['cad_manifest_sha256'],
        status='REVIEW_REQUIRED' if todo or final['review_items'] else 'WORKING_CANDIDATE', **FALSES,
        point_geometry_review=copy.deepcopy(receipt), point_review_check=review,
        endpoints=list(endpoints.values()), connections=list(connections.values()), edges=atoms,
        generated_network_request=nr, network_result=final, code_topology=topology,
        review_items=todo, provenance=provenance, request_fingerprint=net.fingerprint(request),
        unit_disclosure=unit_disclosure, scope_disclosure=scope_disclosure,
        quantity_owner='既有T35/reference/路径台账；点位数量仍由qto-components-points输出')


def run_child(args, receipts, label):
    process = subprocess.run([sys.executable, '-B', *map(str, args)], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding='utf-8', timeout=240)
    receipts.append(dict(id=label, argv=[sys.executable, '-B', *map(str, args)], returncode=process.returncode,
                         stdout=process.stdout, stderr=process.stderr))
    if process.returncode not in (0, 3):
        raise ValueError(label + '子CLI失败:' + str(process.returncode))


def export_role_review(result, output, receipts):
    """Project roles into the shared view and close its NEW identity after projection."""
    old = output / '既有网络计算'
    scene = read(old / '复核视图.json')
    original_report = read(old / '场景检查.json')
    before = net.review_contract.normalize_view(scene, old)['result_fingerprint']
    if original_report.get('result_fingerprint') != before or original_report.get('status') != 'PASS':
        raise ValueError('T35最终视图与原检查身份不闭合')
    (output / 'T35原场景检查.json').write_bytes((old / '场景检查.json').read_bytes())
    edges = {'电气混连原段|' + a['id']: a for a in result['edges']}
    endpoints = {e['object_key']: e for e in result['endpoints']}
    projected = 0
    for obj in scene.get('objects', []):
        atom = edges.get(obj.get('catalog_key'))
        if atom is None:
            continue
        attrs = obj.setdefault('attributes', {})
        attrs.update({k: copy.deepcopy(atom[k]) for k in ('network_roles', 'expression_ids', 'expression_seed_ids', 'code_status', 'text_bindings')})
        actual_roles = []
        for connection in atom['network_roles']:
            for side in ('from_object', 'to_object'):
                endpoint = endpoints[connection[side]]
                actual_roles.append(dict(connection_id=connection['id'], side=side,
                    **{k: copy.deepcopy(endpoint.get(k)) for k in ('object_key', 'point_physical_id', 'source_physical_id',
                        'instance_path', 'roles', 'candidate_systems', 'source_ids', 'rule_ids')}))
        attrs.update(point_geometry_review=copy.deepcopy(result['point_geometry_review']),
                     endpoint_roles=actual_roles,
                     unit_disclosure=copy.deepcopy(result['unit_disclosure']),
                     scope_disclosure=copy.deepcopy(result['scope_disclosure']),
                     network_circuit_quantities=copy.deepcopy(result['network_result']['circuit_quantities']),
                     quantity_extent='已画平面原段；完整安装量请查同包complete_length_m，未决不是0',
                     role_quantity_owner=result['quantity_owner'], **FALSES)
        obj['detail'] = str(obj.get('detail') or '') + '\n双方实际设备角色及来源：' + json.dumps(actual_roles, ensure_ascii=False) + \
            '\n原INSUNITS、单位依据类型与局部范围（显示坐标单位不代替原图明文单位）：' + json.dumps(
                dict(unit_disclosure=result['unit_disclosure'], scope_disclosure=result['scope_disclosure']), ensure_ascii=False) + \
            '\n数量范围：原段导线量只表示已画平面分量，不是完整安装量；完整量及未决：' + json.dumps(
            result['network_result']['circuit_quantities'], ensure_ascii=False) + '\n系统/回路与代号传播：' + json.dumps(
            dict(network_roles=atom['network_roles'], code_status=atom['code_status'], expression_ids=atom['expression_ids'],
                 expression_seed_ids=atom['expression_seed_ids'], text_bindings=atom['text_bindings']), ensure_ascii=False)
        projected += 1
    normalized = net.review_contract.normalize_view(scene, output)
    view = output / '火警网络复核视图.json'; write(view, normalized)
    shared = Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'
    html = output / '火警网络系统复核.html'
    run_child([shared / 'build_qto_review_html.py', '--input', view, '--output', html, '--local-only'], receipts, 'T34角色投影共享HTML')
    payload = re.search(r'<script\s+id="payload"[^>]*>([\s\S]*?)</script>', html.read_text(encoding='utf-8'))
    if payload is None:
        raise ValueError('最终HTML缺公开载荷')
    embedded = net.review_contract.unpack_payload(gzip.decompress(base64.b64decode(payload[1])))
    html_normalized = net.review_contract.normalize_view(embedded, output)
    closed = normalized['result_fingerprint'] == embedded.get('result_fingerprint') == html_normalized['result_fingerprint']
    report = dict(status='PASS' if closed else 'FAIL', result_fingerprint=normalized['result_fingerprint'],
        scope='T34有据角色/代号投影及最终共享视图身份；不证明识图/工程量正确',
        before_result_fingerprint=before, after_result_fingerprint=normalized['result_fingerprint'],
        original_report=dict(path='T35原场景检查.json', sha256=net.trace.sha256(output / 'T35原场景检查.json')),
        normalization='qto_review_contract.normalize_view', projected_atom_count=projected,
        html_result_fingerprint=embedded.get('result_fingerprint'), html_normalized_result_fingerprint=html_normalized['result_fingerprint'],
        html_sha256=net.trace.sha256(html), final_view_sha256=net.trace.sha256(view), identity_closed=closed, **FALSES)
    write(output / '火警网络场景检查.json', report)
    if not closed:
        raise ValueError('角色投影后view/HTML/check身份未闭合')


def main():
    p = argparse.ArgumentParser(description='从原图网络、点位身份及项目角色/代码表形成火警等系统工作候选')
    p.add_argument('--cad-manifest', type=Path, required=True)
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--build-review', action='store_true')
    args = p.parse_args(); receipts = []; output = args.output_dir
    created = False
    try:
        if output.exists():
            raise ValueError('输出目录已存在，拒绝覆盖')
        request = read(args.request)
        result = infer_roles(args.cad_manifest, request)
        output.mkdir(parents=True); created = True
        write(output / '网络系统角色结果.json', result)
        generated = output / '既有网络计算请求.json'; write(generated, result['generated_network_request'])
        cli = Path(__file__).with_name('partition_electrical_route_network.py')
        command = [cli, '--cad-manifest', args.cad_manifest.resolve(), '--request', generated.resolve(), '--output-dir', output / '既有网络计算']
        if args.build_review:
            command.append('--build-review')
        run_child(command, receipts, 'T35网络与既有reference及共享HTML')
        actual = read(output / '既有网络计算' / '混连网络拆分结果.json')
        if actual != result['network_result']:
            raise ValueError('实际T35 CLI与本轮候选计算不一致')
        if args.build_review:
            export_role_review(result, output, receipts)
        dependencies = {}
        for d in net.rows(request.get('downstream_requests', []), 'downstream_requests').values():
            if any(c in d['id'] for c in '/\\<>:"|?*') or d['id'] in ('.', '..'):
                raise ValueError('downstream.id须为安全文件名，不能包含路径')
            value = bound(d.get('request'), dependencies)
            kind = d.get('kind'); target = output / ('接续-' + d['id'])
            if kind == 'paths':
                if value.get('project_id') != result['project_id']:
                    raise ValueError('下游路径台账project_id不符')
                command = [Path(__file__).with_name('calculate_electrical_paths.py'), d['request']['path'], '--output', target.with_suffix('.json')]
            elif kind in ('objects', 'trays'):
                if value.get('source_sha256') != result['source_sha256'] or value.get('cad_manifest_sha256') != result['cad_manifest_sha256']:
                    raise ValueError('下游请求与本轮原图/清单指纹不符')
                script = 'bind_electrical_objects.py' if kind == 'objects' else 'route_circuits_on_trays.py'
                command = [Path(__file__).with_name(script), '--cad-manifest', args.cad_manifest.resolve(), '--request', d['request']['path'],
                           '--output-dir' if kind == 'objects' else '--output', target if kind == 'objects' else target.with_suffix('.json')]
                if kind == 'objects' and args.build_review:
                    command.append('--build-review')
            else:
                raise ValueError('downstream.kind须为paths/objects/trays，复用既有契约')
            run_child(command, receipts, d['id'])
        stable(dependencies)
        stable(result['provenance']['fingerprints'])
        stable(result['provenance']['dependency_fingerprints_before'])
        write(output / '实际调用回执.json', dict(status='COMPLETED', calls=receipts, **FALSES))
        print(json.dumps(dict(status=result['status'], output_dir=str(output), calls=[dict(id=r['id'], returncode=r['returncode']) for r in receipts]), ensure_ascii=False))
        return 3 if result['status'] == 'REVIEW_REQUIRED' or any(r['returncode'] == 3 for r in receipts) else 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.TimeoutExpired) as exc:
        if created:
            write(output / '实际调用回执.json', dict(status='FAILED', error=str(exc), calls=receipts, **FALSES))
        print(json.dumps(dict(status='INPUT_REQUIRED', error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
