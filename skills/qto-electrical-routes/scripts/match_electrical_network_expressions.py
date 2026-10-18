#!/usr/bin/env python3
"""Compare network expressions and export explicitly selected working quantities.

Similar device sets are only candidate constraints. Original source quantities,
all edge identities and model decisions survive; source outputs are never changed.
"""
from __future__ import annotations
import argparse
import copy
from collections import defaultdict
import json
from pathlib import Path
import subprocess

import infer_electrical_network_roles as roles
import partition_electrical_route_network as net
from calculate_electrical_paths import calculate

REQUEST_SCHEMA = 'qto-electrical-network-expression-request/1.0'
RESULT_SCHEMA = 'qto-electrical-network-expressions/1.0'
# Sheet identity belongs to an expression's own source view. Distinct source
# sheets may depict the same engineering circuit and are not a context conflict.
ENGINEERING_CONTEXT = tuple(k for k in roles.CONTEXT if k != 'sheet_id')


def current_result(raw, dependencies):
    value = roles.bound(raw, dependencies)
    if value.get('schema') != roles.RESULT_SCHEMA or not value.get('provenance', {}).get('stable'):
        raise ValueError('表达须绑定本轮系统角色结果')
    provenance = value['provenance']
    for field in ('fingerprints', 'dependency_fingerprints_before'):
        dependencies.update(provenance.get(field, {}))
    roles.stable(dependencies)
    return value


def context_key(expression):
    return [expression.get(k) for k in (*ENGINEERING_CONTEXT, 'system', 'circuit_id')]


def source_sheet(result, context, edge_ids):
    """Check each expression against its own declared, source-bound sheet."""
    nr = result['generated_network_request']
    sheets = net.rows(nr.get('sheets', []), 'expression source sheets', 'sheet_id')
    sheet = sheets.get(context.get('sheet_id'))
    if sheet is None or sheet.get('floor') != context.get('floor'):
        raise ValueError('表达图页与自身原图sheet_id/楼层不匹配，不能伪造相同图页')
    bounds = sheet.get('bounds')
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError('表达原图图页缺真实bounds')
    values = [net.trace.finite(v, 'source sheet.bounds') for v in bounds]
    if values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError('表达原图图页bounds无效')
    for edge in result['edges']:
        if edge['id'] not in edge_ids:
            continue
        points = edge.get('source_points')
        if not isinstance(points, list) or not points or any(
                not isinstance(p, list) or len(p) < 2 or
                not (values[0] <= p[0] <= values[2] and values[1] <= p[1] <= values[3])
                for p in points):
            raise ValueError('表达原边原坐标不在自身图页边界内')
    return copy.deepcopy(sheet)


def atom_parameters(expression, atom_id):
    if any(a['id'] == atom_id and a.get('code_status') == 'CONFLICT'
           for a in expression['_result']['edges']):
        return []
    rows = expression['_result']['network_result']['ledger']['circuits']
    return sorted({tuple(r.get(k) for k in ('kind', 'system', 'spec', 'parallel_count'))
                   for r in rows if atom_id in r['path_ids']}, key=repr)


def match_expressions(request):
    if request.get('schema') != REQUEST_SCHEMA:
        raise ValueError('request.schema须为' + REQUEST_SCHEMA)
    dependencies = {}; expressions = {}; todo = []
    for raw in net.rows(request.get('expressions'), 'expressions').values():
        result = current_result(raw.get('role_result'), dependencies)
        ids = raw.get('connection_ids')
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise ValueError('表达connection_ids须非空且唯一')
        connections = {c['id']: c for c in result['connections']}
        if any(cid not in connections for cid in ids):
            raise ValueError('表达引用未知连接')
        selected = [connections[cid] for cid in ids]
        contexts = {net.fingerprint([c['context'], c['system'], c['circuit_id']]) for c in selected}
        if len(contexts) != 1:
            raise ValueError('单表达须为同一上下文/系统/回路；不同线路请分别声明')
        ctx = selected[0]['context']; system = selected[0]['system']; circuit = selected[0]['circuit_id']
        edge_ids = sorted({p for c in selected for p in c['path_ids']})
        own_sheet = source_sheet(result, ctx, edge_ids)
        used_objects = {c[k] for c in selected for k in ('from_object', 'to_object')}
        identity_rows = net.rows(raw.get('endpoint_identities', []), 'endpoint_identities', 'object_key')
        endpoints = {o['object_key']: o for o in result['endpoints']}
        ev = roles.evidence_for(Path(result['provenance']['manifest']), result['generated_network_request'])
        identities = []
        for key, identity in identity_rows.items():
            if key not in used_objects:
                raise ValueError('endpoint_identities引用表达范围外端点')
            ev.refs(identity.get('basis_source_ids'), 'endpoint identity source')
            net.trace.text(identity.get('basis'), 'endpoint identity basis')
            net.trace.text(identity.get('identity_key'), 'endpoint identity_key')
            ep = endpoints[key]
            if not ep.get('point_physical_id') or not ep.get('instance_path'):
                raise ValueError('未知物理实例不能建立端点同物工作解释')
            item = dict(identity, point_physical_id=ep['point_physical_id'], source_physical_id=ep['source_physical_id'],
                        instance_path=ep['instance_path'], source_sha256=result['source_sha256'],
                        state='working/unreviewed', same_physical_proven=False)
            if identity.get('t16_result'):
                t16 = roles.bound(identity['t16_result'], dependencies)
                if t16.get('schema') != 'qto-cross-drawing-registration/1.0' or not t16.get('provenance', {}).get('stable'):
                    raise ValueError('T16结果契约/来源状态无效')
                dependencies.update(t16['provenance'].get('fingerprints', {}))
                for field in ('sources',):
                    for source in t16.get(field, []):
                        if source.get('path') and source.get('sha256'):
                            dependencies[source['path']] = source['sha256']
                pair = [p for p in t16.get('pairings', []) if p.get('interpretation_id', p.get('pairing_id')) == identity.get('pairing_id')]
                if len(pair) != 1:
                    raise ValueError('T16配对未唯一找到')
                if not any(r.get('instance_path') == ep['instance_path'] for r in pair[0].get('source_refs', [])):
                    raise ValueError('T16配对未包含当前完整端点实例')
                item['t16_pairing'] = pair[0]
            identities.append(item)
        identity_complete = set(identity_rows) == used_objects and len({i['identity_key'] for i in identities}) == len(identities)
        applicability = raw.get('applicability')
        if not isinstance(applicability, dict):
            raise ValueError('表达须显式applicability适用声明')
        ev.refs(applicability.get('basis_source_ids'), 'applicability.source_ids')
        net.trace.text(applicability.get('basis'), 'applicability.basis')
        if applicability.get('current') not in (True, False, None):
            raise ValueError('applicability.current须为true/false/null')
        expr = dict(id=raw['id'], role_result=raw['role_result'], connection_ids=ids, edge_ids=edge_ids,
            **ctx, system=system, circuit_id=circuit, endpoint_identities=identities,
            source_sheet=own_sheet,
            identity_complete=identity_complete, applicability=applicability,
            point_geometry_review=result['point_geometry_review'], source_sha256=result['source_sha256'],
            original_edges=[a for a in result['edges'] if a['id'] in edge_ids],
            original_quantity=result['network_result']['circuit_quantities'], **roles.FALSES,
            _result=result, _evidence=ev)
        if not identity_complete:
            todo.append(dict(expression_id=raw['id'], problem='表达物理端点身份未闭合'))
        if any(c['status'] != 'WORKING_CANDIDATE' for c in selected):
            todo.append(dict(expression_id=raw['id'], problem='表达实际连接/系统角色仍有未决'))
        expressions[raw['id']] = expr
    if not expressions:
        raise ValueError('expressions须非空')
    projects = {e['_result']['project_id'] for e in expressions.values()}
    if len(projects) != 1:
        raise ValueError('多表达project_id不符')
    candidates = []
    ids = sorted(expressions)
    for i, left_id in enumerate(ids):
        for right_id in ids[i + 1:]:
            a, b = expressions[left_id], expressions[right_id]
            conflicts, missing = [], []
            for key in (*ENGINEERING_CONTEXT, 'system', 'circuit_id'):
                if a.get(key) is None or b.get(key) is None:
                    missing.append(key)
                elif a[key] != b[key]:
                    conflicts.append(key)
            if not a['identity_complete'] or not b['identity_complete']:
                missing.append('physical_endpoint_identities')
            ai = {p['identity_key'] for p in a['endpoint_identities']}
            bi = {p['identity_key'] for p in b['endpoint_identities']}
            if ai != bi:
                conflicts.append('endpoint_identity_set')
            ap = {p for eid in a['edge_ids'] for p in atom_parameters(a, eid)}
            bp = {p for eid in b['edge_ids'] for p in atom_parameters(b, eid)}
            if not ap or not bp:
                missing.append('specification_and_multiplicity')
            elif ap != bp:
                conflicts.append('specification_and_multiplicity')
            if a['applicability'].get('current') is not True or b['applicability'].get('current') is not True:
                missing.append('current_applicability')
            candidates.append(dict(left_expression=left_id, right_expression=right_id,
                status='CANDIDATE_REQUIRES_EDGE_DECISION' if not conflicts and not missing else 'NOT_CLOSED',
                conflicts=conflicts, missing=missing, common_endpoint_identity_count=len(ai & bi),
                left_endpoint_identity_count=len(ai), right_endpoint_identity_count=len(bi),
                source_sheet_ids={left_id:a['sheet_id'], right_id:b['sheet_id']},
                endpoint_set_is_not_route_identity_proof=True, **roles.FALSES))
    dispositions = {(e['id'], edge): dict(expression_id=e['id'], edge_id=edge,
                    destination='retained_independent_working_expression', primary_expression=None,
                    primary_edge_id=None, reason='没有明确有源主次逐边决定，原表达保留', **roles.FALSES)
                    for e in expressions.values() for edge in e['edge_ids']}
    accepted = []; primary_targets = {}; secondary_sources = set()
    for decision in net.rows(request.get('decisions', []), 'decisions').values():
        primary = expressions.get(decision.get('primary_expression')); secondary = expressions.get(decision.get('secondary_expression'))
        if primary is None or secondary is None or primary is secondary:
            raise ValueError('主次表达无效')
        # Decisions are frozen against the exact interpreted outputs, not names.
        hashes = decision.get('role_result_sha256', {})
        if any(hashes.get(e['id']) != e['role_result']['sha256'] for e in (primary, secondary)):
            raise ValueError('主次决定来源SHA过期/缺失；拒绝继承旧决定')
        candidate = next(c for c in candidates if {c['left_expression'], c['right_expression']} == {primary['id'], secondary['id']})
        if candidate['status'] != 'CANDIDATE_REQUIRES_EDGE_DECISION':
            todo.append(dict(decision_id=decision['id'], problem='主次候选约束未闭合，决定未执行', candidate=candidate))
            continue
        net.trace.text(decision.get('basis'), 'decision.basis')
        refs = net.rows(decision.get('source_refs'), 'decision.source_refs', 'expression_id')
        if set(refs) != {primary['id'], secondary['id']}:
            raise ValueError('主次决定须分别引用两侧原图来源')
        for e in (primary, secondary):
            e['_evidence'].refs(refs[e['id']].get('source_ids'), 'decision source refs')
        pairs = decision.get('edge_pairs')
        if not isinstance(pairs, list) or not pairs:
            raise ValueError('主次决定须非空逐边edge_pairs')
        for pair in pairs:
            pkey = (primary['id'], pair.get('primary_edge_id')); skey = (secondary['id'], pair.get('secondary_edge_id'))
            if pkey not in dispositions or skey not in dispositions:
                raise ValueError('主次决定引用不存在/范围外原边')
            net.trace.text(pair.get('basis'), 'edge pair basis')
            if skey in secondary_sources or pkey in secondary_sources or skey in primary_targets:
                raise ValueError('逐边主次关系重复或成链/环，不能多出口')
            if atom_parameters(primary, pkey[1]) != atom_parameters(secondary, skey[1]) or not atom_parameters(primary, pkey[1]):
                todo.append(dict(decision_id=decision['id'], secondary_edge_id=skey[1], problem='逐边规格/根数不符或未知，保留独立原边'))
                continue
            primary_targets[pkey] = True; secondary_sources.add(skey)
            dispositions[skey].update(destination='represented_by_selected_primary', primary_expression=pkey[0], primary_edge_id=pkey[1],
                reason=pair['basis'], decision_id=decision['id'], decision_origin='model', state='working/unreviewed',
                source_quantities_unchanged=True)
            dispositions[pkey].update(destination='selected_primary_working_expression', reason=pair['basis'], decision_id=decision['id'])
        accepted.append(dict(id=decision['id'], primary_expression=primary['id'], secondary_expression=secondary['id'],
                             basis=decision['basis'], source_refs=decision['source_refs'], **roles.FALSES))
    # Export one fresh working ledger. Original geometries/quantities are retained
    # above. A secondary edge is represented once ONLY after the explicit decision.
    # Unmapped edges are kept in the ledger, and incomplete correspondence remains
    # an explicit pending component. No zero length is manufactured.
    ledger = dict(schema='qto-electrical-path-ledger/1.0', project_id=next(iter(projects)), unit='m', paths=[], channels=[], circuits=[])
    coverage = []; working_exports = []
    for eid, expression in expressions.items():
        original = expression['_result']['network_result']['ledger']
        active_edges = {edge for edge in expression['edge_ids'] if (eid, edge) not in secondary_sources}
        mapped = [d for (x, _), d in dispositions.items() if x == eid and d['destination'] == 'represented_by_selected_primary']
        secondary_decisions = [d for d in accepted if d['secondary_expression'] == eid]
        primary_decisions = [d for d in accepted if d['primary_expression'] == eid]
        unmatched = sorted(active_edges) if secondary_decisions else []
        if unmatched:
            todo.append(dict(expression_id=eid, problem='次表达存在未对应原边，保留已知参考并标未决', edge_ids=unmatched))
        if not secondary_decisions and not primary_decisions and len(expressions) > 1:
            todo.append(dict(expression_id=eid, problem='独立/未裁定表达保留原参考，未自动扣量'))
        namespace = eid + '::'
        def rename(row):
            v = copy.deepcopy(row)
            for key in ('id', 'physical_id', 'from_node', 'to_node', 'start_node', 'end_node', 'node_id'):
                if isinstance(v.get(key), str):
                    v[key] = namespace + v[key]
            if 'path_ids' in v:
                v['path_ids'] = [namespace + p for p in v['path_ids']]
            if 'extras' in v:
                v['extras'] = [rename(x) for x in v['extras']]
            v['expression_id'] = eid
            return v
        consumed = set()
        code_conflict_edges = {a['id'] for a in expression['original_edges'] if a.get('code_status') == 'CONFLICT'}
        for item in original['circuits']:
            if not set(item['path_ids']).issubset(active_edges):
                continue
            if set(item['path_ids']) & code_conflict_edges:
                # An older role result may still contain its pre-conflict T35
                # rows. Preserve that snapshot, but never re-export it as valid.
                continue
            v = rename(item)
            v['original_physical_id'] = item['physical_id']
            v['source_expression_role_result_sha256'] = expression['role_result']['sha256']
            if unmatched:
                v['pending_components'] = sorted(set(v['pending_components']) | {'expression_correspondence_unresolved'})
            ledger['circuits'].append(v); consumed.update(item['path_ids'])
            working_exports.append(dict(expression_id=eid, original_item_id=item['id'], working_item_id=v['id'],
                physical_id=v['physical_id'], quantity_owner='qto-electrical-routes', unit='m',
                system=item['system'], circuit_id=item.get('circuit_id'), scope_id=item.get('scope_id'),
                role='selected_working_expression', **roles.FALSES))
        for path in original['paths']:
            if path['id'] in consumed:
                ledger['paths'].append(rename(path))
        coverage.append(dict(expression_id=eid, all_edge_ids=expression['edge_ids'],
            represented_secondary_edge_ids=[d['edge_id'] for d in mapped], retained_edge_ids=sorted(active_edges),
            exported_calculable_edge_ids=sorted(consumed), unresolved_calculation_edge_ids=sorted(active_edges - consumed),
            no_edge_lost=len(mapped) + len(active_edges) == len(expression['edge_ids'])))
        if active_edges - consumed:
            todo.append(dict(expression_id=eid, problem='保留原边缺可计算规格/实际路径；数量继续null', edge_ids=sorted(active_edges - consumed)))
    calculation = calculate(ledger)
    roles.stable(dependencies)
    return dict(schema=RESULT_SCHEMA, status='REVIEW_REQUIRED' if todo else 'WORKING_SELECTION', **roles.FALSES,
        source_quantity_mutation=False, quantity_mutation_allowed=False, automatic_physical_merge=False, physical_length_equivalence_proven=False,
        expressions=[{k:v for k,v in e.items() if not k.startswith('_')} for e in expressions.values()],
        candidates=candidates, decisions=accepted, edge_dispositions=list(dispositions.values()), coverage=coverage,
        selected_working_ledger=ledger, working_exports=working_exports, reference_calculation=calculation,
        review_items=todo, provenance=dict(stable=True, dependency_fingerprints_before=dependencies, dependency_fingerprints_after=dependencies),
        request_fingerprint=net.fingerprint(request))


def export_coordination(result, output, receipts):
    """Actual T39 rows: the workset is explicit; full project baseline stays unknown."""
    by_item = {i['id']: i for i in result['reference_calculation']['items']}
    by_expression = {e['id']: e for e in result['expressions']}
    rows, universe = [], []
    for item in result['selected_working_ledger']['circuits']:
        expression = by_expression[item['expression_id']]
        actual_scope = [item['expression_id'] + '|' + '|'.join(item['path_ids'])]
        basis = '本轮显式选定工作表达的已画平面导线参考；竖向端接预留未闭合另列，不是完整安装量'
        ident = dict(physical_id=item['physical_id'], quantity_type='working_drawn_conductor_length', unit='m',
            basis=basis, actual_scope=actual_scope, source_revision='本轮各表达源版本分别保留',
            source_fingerprint=result['request_fingerprint'])
        # This is a WORKING output row identity, visibly marked as internal. Raw
        # drawing IDs stay in the explicit provenance, never relabelled as numbers.
        sid = '内部工作出口|' + item['id']
        calc = by_item[item['id']]
        original_edges = [edge for edge in expression['original_edges'] if edge['id'] in
                          [p.split('::', 1)[1] for p in item['path_ids']]]
        row = dict(ident, row_id=item['id'], quantity=calc['known_length_m'], included=True, source_ids=[sid],
            internal_working_identity=True, physical_identity_status='model_working_unreviewed',
            quantity_level=calc.get('quantity_level'), complete_length_m=calc.get('length_m') if calc.get('complete_quantity') else None,
            pending_components=calc.get('pending_components'), original_edge_provenance=original_edges,
            original_source_revision=expression['revision'], original_source_fingerprint=expression['role_result']['sha256'],
            **roles.FALSES)
        row.pop('source_revision'); row.pop('source_fingerprint')
        rows.append(row); universe.append(dict(ident, source_id=sid))
    pid = 'T34选定工作表达'
    package = output / '选定工作出口成果行.json'
    roles.write(package, dict(schema='qto-package-quantity-rows/1.0', package_id=pid,
        source_revision='本轮各表达源版本分别保留', source_fingerprint=result['request_fingerprint'], rows=rows,
        **roles.FALSES))
    documents = [dict(file=e['role_result']['path'], sha256=e['role_result']['sha256']) for e in result['expressions']]
    baseline = output / '选定工作出口基准.json'
    roles.write(baseline, dict(schema='qto-quantity-universe/1.0', complete=False,
        completeness_reason='仅本轮显式可计算工作出口；未提供完整设计/清单基准，原边守恒另见coverage',
        source_documents=documents, items=universe))
    reconciliation = dict(schema='qto-package-reconciliation/1.0', project_id=result['selected_working_ledger']['project_id'],
        packages=[dict(package_id=pid, skill='qto-electrical-routes', result_file=str(package.resolve()), result_sha256=net.trace.sha256(package))],
        baseline=dict(file=str(baseline.resolve()), sha256=net.trace.sha256(baseline)), handoffs=[])
    request = output / '工作出口对账输入.json'; roles.write(request, reconciliation)
    coordinator = Path(__file__).resolve().parents[2] / 'qto-coordinator/scripts/qto_coordination.py'
    roles.run_child([coordinator, 'handoffs', '--input', request, '--out', output / '工作出口实际对账.json'], receipts, 'T39选定工作出口实际对账')


def main():
    p = argparse.ArgumentParser(description='按有源逐边决定选择火警多平面工作表达；保留原始全部边')
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args(); created = False; receipts = []
    try:
        if args.output_dir.exists():
            raise ValueError('输出目录已存在，拒绝覆盖')
        result = match_expressions(roles.read(args.request))
        args.output_dir.mkdir(parents=True); created = True
        roles.write(args.output_dir / '多平面网络表达结果.json', result)
        ledger = args.output_dir / '选定工作路径台账.json'; roles.write(ledger, result['selected_working_ledger'])
        roles.run_child([Path(__file__).with_name('calculate_electrical_paths.py'), ledger, '--output', args.output_dir / '选定工作数量复算.json'], receipts, '既有路径reference实际复算')
        if roles.read(args.output_dir / '选定工作数量复算.json') != result['reference_calculation']:
            raise ValueError('选定工作台账的实际CLI与内嵌结果不符')
        export_coordination(result, args.output_dir, receipts)
        roles.stable(result['provenance']['dependency_fingerprints_before'])
        result['actual_cli_returncodes'] = [dict(id=r['id'], returncode=r['returncode']) for r in receipts]
        if any(r['returncode'] == 3 for r in receipts):
            result['status'] = 'REVIEW_REQUIRED'
            result['review_items'].append(dict(problem='实际下游对账仍有未核，子进程0不覆盖父层未决',
                calls=result['actual_cli_returncodes'], t39_report='工作出口实际对账.json'))
        roles.write(args.output_dir / '多平面网络表达结果.json', result)
        roles.write(args.output_dir / '实际调用回执.json', dict(status='COMPLETED', calls=receipts, **roles.FALSES))
        print(json.dumps(dict(status=result['status'], output_dir=str(args.output_dir), **roles.FALSES), ensure_ascii=False))
        return 3 if result['review_items'] or any(r['returncode'] == 3 for r in receipts) else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, subprocess.TimeoutExpired) as exc:
        if created:
            roles.write(args.output_dir / '实际调用回执.json', dict(status='FAILED', calls=receipts, error=str(exc), **roles.FALSES))
        print(json.dumps(dict(status='INPUT_REQUIRED', error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
