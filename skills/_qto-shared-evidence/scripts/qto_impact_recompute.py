#!/usr/bin/env python3
"""Evidence-bound impact queue and method-decision to professional recomputation.

Uses the existing review identity/applicability/conflict gates and calculators.
This creates a new local input revision and receipt, never project backfill or
human quantity approval. Relationship edits require the versioned electrical
network partitioner; the numeric dependency graph is never a substitute.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path

from qto_review_contract import normalize_view, validate_feedback
from qto_review_identity import decisions, fingerprint, handoff_actions
from recompute_qto_dependencies import recalculate

REQUEST_SCHEMA = 'qto-impact-recompute-request/1.0'
RESULT_SCHEMA = 'qto-impact-recompute-result/1.0'
NETWORK_REQUEST = 'qto-electrical-route-network-request/1.0'
NETWORK_RESULT = 'qto-electrical-route-network-partition/1.0'
NETWORK_ADAPTER = 'qto-impact-electrical-network-adapter/1.0'
GRAPH_COLLECTIONS = {'node': 'nodes', 'source': 'sources', 'rule': 'rules'}
NODE_PARAMETER_FIELDS = {'value', 'source_ids', 'rule_ids', 'quantity_level', 'basis',
                         'basis_ids', 'evidence', 'semantic_context'}
NETWORK_COLLECTIONS = {'endpoint_objects': 'object_key', 'connections': 'id', 'circuit_specs': 'circuit_id'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def index(rows, field='id', label='记录'):
    require(isinstance(rows, list), label + '须为数组')
    out = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get(field), str) and row[field].strip()
                and row[field] not in out, label + '身份缺失或重复')
        out[row[field]] = row
    return out


def strings(value, label):
    require(isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)
            and len(value) == len(set(value)), label + '须为不重复文字数组')
    return value


def transitive_nodes(graph, seeds):
    nodes = index(graph.get('nodes', []), label='计算节点')
    children = defaultdict(set)
    for key, row in nodes.items():
        for dep in row.get('variables', {}).values():
            children[dep].add(key)
    seen = set(seeds)
    queue = deque(seen)
    while queue:
        for key in children[queue.popleft()] - seen:
            seen.add(key)
            queue.append(key)
    return sorted(seen)


def bindings_for(request, view):
    bindings = index(request.get('bindings', []), 'member_key', '专业输入成员绑定')
    keys = {o['catalog_key'] for o in view['objects']}
    graph = request['state'].get('calculation_graph') or {}
    node_ids = set(index(graph.get('nodes', []), label='计算节点'))
    network_ids = set(index(request['state'].get('networks', []), label='专业网络'))
    for key, row in bindings.items():
        require(key in keys, '绑定不属于当前复核对象:' + key)
        for field in ('node_ids', 'network_ids', 'system_ids', 'circuit_ids'):
            strings(row.get(field, []), key + '.' + field)
        require(set(row.get('node_ids', [])) <= node_ids, '绑定引用不存在的计算节点:' + key)
        require(set(row.get('network_ids', [])) <= network_ids, '绑定引用不存在的专业网络:' + key)
        targets = row.get('network_targets', [])
        require(isinstance(targets, list), '专业网络目标绑定须为数组')
        for target in targets:
            require(isinstance(target, dict) and target.get('network_id') in row.get('network_ids', [])
                    and target.get('collection') in NETWORK_COLLECTIONS
                    and isinstance(target.get('target_id'), str) and target['target_id'].strip(),
                    '网络目标须显式绑定本成员的网络/集合/稳定身份')
        require(len({fingerprint(t) for t in targets}) == len(targets), '网络目标绑定重复')
    return bindings


def build_queue(request, view, bindings):
    """Sort with explicit dimensions; never add metres to counts or unknown to zero."""
    contexts = index(request.get('impact_context', []), 'decision_id', '工程影响上下文')
    ds = {d['id']: d for d in decisions(view)}
    require(set(contexts) <= set(ds), '影响上下文不得按同名事项或重叠范围另造身份')
    graph = request['state'].get('calculation_graph') or {}
    result = []
    for ident, d in ds.items():
        if d['kind'] == 'category':
            continue
        context = contexts.get(ident, {})
        members = sorted(set(d['scope']['member_keys']) - set(d['scope']['excluded_member_keys']))
        held = {k for child in ds.values() if child.get('exception_of') == ident
                for k in child['scope']['member_keys']}
        applicable = sorted(set(members) - held)
        active = [bindings[k] for k in applicable if k in bindings]
        seeds = {n for row in active for n in row.get('node_ids', [])}
        affected = transitive_nodes(graph, seeds)
        impacts = copy.deepcopy(d.get('impacts', []))
        for imp in impacts:
            for field in ('value', 'min', 'max'):
                value = imp.get(field)
                require(value is None or isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value), ident + '影响数量须为有限数值或null')
            if imp.get('min') is not None and imp.get('max') is not None:
                require(imp['min'] <= imp['max'], ident + '影响区间上下限颠倒')
        unknown = not impacts or any(i.get('value') is None and
                    (i.get('min') is None or i.get('max') is None) for i in impacts)
        blocking = strings(context.get('blocking_member_keys', []), ident + '.blocking_member_keys')
        require(set(blocking) <= set(members), '决定性资料缺口不能越出事项范围')
        mutex = strings(context.get('exclusive_with_decision_ids', []), ident + '.互斥事项')
        require(set(mutex) <= set(ds) and ident not in mutex, '互斥事项必须显式引用当前稳定身份')
        systems = sorted({s for b in active for s in b.get('system_ids', [])})
        circuits = sorted({s for b in active for s in b.get('circuit_ids', [])})
        result.append({'decision_id': ident, 'revision': d['revision'],
            'result_fingerprint': d['result_fingerprint'], 'title': d['title'],
            'scope': copy.deepcopy(d['scope']), 'exception_of': d.get('exception_of'),
            'applies_to_member_keys': applicable, 'held_by_exception_member_keys': sorted(held),
            'recommendation': copy.deepcopy(d.get('recommendation')),
            'alternatives': copy.deepcopy(d.get('business_options', [])),
            'facts': copy.deepcopy(d.get('facts', [])), 'gaps': copy.deepcopy(d.get('gaps', [])),
            'sources': copy.deepcopy(d.get('evidence', [])), 'quantity_impacts': impacts,
            'unknown_quantity_impact': unknown, 'system_ids': systems, 'circuit_ids': circuits,
            'unknown_impact_units': sorted({i['unit'] for i in impacts if i.get('value') is None
                and (i.get('min') is None or i.get('max') is None)}),
            'affected_node_ids': affected, 'exclusive_with_decision_ids': mutex,
            'unbound_member_keys': sorted(set(applicable) - set(bindings)),
            'internal_working_basis': context.get('internal_working_basis'),
            'first_pass_state': context.get('first_pass_state', '未提供首轮完成证据'),
            'continue_member_keys': sorted(set(applicable) - set(blocking)),
            'blocked_member_keys': blocking, 'shared_source_id': context.get('shared_source_id'),
            'processing': '可继续分支先完成；决定性缺口保留多方案或未决；普通识图仍由专业执行'})
    # Quantity ranks compare only peers with the same unit. No cross-unit sum or
    # artificial conversion is used; ranks merely break otherwise equal scope ties.
    magnitudes = defaultdict(set)
    for row in result:
        for imp in row['quantity_impacts']:
            if imp.get('value') is None and (imp.get('min') is None or imp.get('max') is None):
                continue
            values = [abs(imp[k]) for k in ('value', 'min', 'max') if imp.get(k) is not None]
            if values:
                magnitudes[imp['unit']].add(max(values))
    for row in result:
        unit_ranks = {unit: None for unit in row['unknown_impact_units']}
        for imp in row['quantity_impacts']:
            if imp.get('value') is None and (imp.get('min') is None or imp.get('max') is None):
                continue
            values = [abs(imp[k]) for k in ('value', 'min', 'max') if imp.get(k) is not None]
            if values:
                unit_ranks[imp['unit']] = max(unit_ranks.get(imp['unit']) or 0,
                    sorted(magnitudes[imp['unit']]).index(max(values)) + 1)
        row['quantity_rank_within_unit'] = unit_ranks
        row['priority_vector'] = [int(bool(row['blocked_member_keys'])), int(row['unknown_quantity_impact']),
            len(row['system_ids']), len(row['circuit_ids']), len(row['affected_node_ids']),
            len(row['exclusive_with_decision_ids']), max((v for v in unit_ranks.values() if v is not None), default=0)]
    result.sort(key=lambda r: (tuple(-n for n in r['priority_vector']), r['decision_id']))
    # Grouping is a presentation aid. Every member identity, exception, quantity
    # unit and rank remains in the full queue and in the group member snapshots.
    groups = defaultdict(list)
    for row in result:
        if row['shared_source_id']:
            groups[row['shared_source_id']].append(copy.deepcopy(row))
    return {'schema': 'qto-high-impact-queue/1.0', 'items': result,
            'same_source_groups': [{'source_id': k, 'members': v} for k, v in sorted(groups.items())],
            'priority_dimensions': ['决定性缺口', '未知数量影响', '系统数', '回路数', '依赖传播节点数', '互斥事项数', '各单位内数量位次'],
            'unknown_is_zero': False, 'mixed_unit_total': None}


def electrical_partition(network, base_dir):
    """Versioned real T35 adapter; no pending/stub semantic success fallback."""
    require(network.get('request', {}).get('schema') == NETWORK_REQUEST, '专业重识别请求版本无效')
    scripts = Path(__file__).resolve().parents[2] / 'qto-electrical-routes' / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from partition_electrical_route_network import partition_network
    from calculate_electrical_paths import calculate
    manifest = Path(network['manifest_path'])
    if not manifest.is_absolute():
        require(base_dir is not None, '相对专业来源路径须有请求文件目录')
        manifest = Path(base_dir) / manifest
    result = partition_network(manifest, copy.deepcopy(network['request']), base_path=base_dir)
    require(result.get('schema') == NETWORK_RESULT, '专业重识别返回版本不兼容')
    require(result.get('source_fingerprint') == network['request'].get('source_sha256'), '专业重识别来源指纹未绑定原输入')
    require(isinstance(result.get('ledger'), dict), '专业重识别没有返回实际重建的计算台账')
    calculated = calculate(copy.deepcopy(result['ledger']))
    require(calculated == result.get('reference_calculation'), '重建台账复算与专业返回数量不一致')
    return {'adapter_schema': NETWORK_ADAPTER, 'partition': result, 'calculation': calculated,
            'request_fingerprint': fingerprint(network['request']),
            'semantic_reidentification_executed': True, 'calculator_executed': True}


def replace_row(rows, ident, before, after, field='id'):
    by_id = index(rows, field)
    require(by_id.get(ident) == before, '原输入已变化或新增/删除目标不一致:' + ident)
    require(before is not None or after is not None, '空变更不构成输入变化')
    if after is not None:
        require(isinstance(after, dict) and after.get(field) == ident, '变更不得改写稳定对象身份:' + ident)
    require(before != after, '变更前后完全相同:' + ident)
    if before is None:
        rows.append(copy.deepcopy(after))
    else:
        pos = next(i for i, r in enumerate(rows) if r[field] == ident)
        if after is None:
            rows.pop(pos)
        else:
            rows[pos] = copy.deepcopy(after)


def mutation_target(mutation):
    if mutation.get('kind') == 'parameter':
        return ('graph', mutation.get('collection'), mutation.get('target_id'))
    return ('network', mutation.get('network_id'), mutation.get('collection'), mutation.get('target_id'))


def apply_mutation(state, mutation, allowed, bindings):
    members = strings(mutation.get('member_keys'), '变更成员')
    require(members and set(members) <= set(allowed), '变更成员未通过适用范围/例外门')
    require(all(k in bindings for k in members), '变更成员缺少专业输入绑定')
    ident = mutation.get('target_id')
    require(isinstance(ident, str) and ident.strip(), '变更缺少对象/节点身份')
    before, after = mutation.get('before'), mutation.get('after')
    if mutation.get('kind') == 'parameter':
        collection = mutation.get('collection')
        require(collection in GRAPH_COLLECTIONS, '参数变更目标集合无效')
        graph = state.get('calculation_graph')
        require(isinstance(graph, dict), '没有数值计算输入')
        allowed_nodes = {n for k in members for n in bindings[k].get('node_ids', [])}
        if collection == 'node':
            require(ident in allowed_nodes and before is not None and after is not None, '参数必须绑定已有节点；对象拆合应走专业重识别')
            changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
            require(changed <= NODE_PARAMETER_FIELDS, '关系、公式或归属变化不得冒充数值参数重算')
        else:
            key = 'source_ids' if collection == 'source' else 'rule_ids'
            require(any(ident in n.get(key, []) for n in graph['nodes'] if n['id'] in allowed_nodes),
                    '依据变更未绑定实际使用该依据的节点')
        seeds = {ident} if collection == 'node' else {n['id'] for n in graph['nodes']
                    if ident in n.get('source_ids' if collection == 'source' else 'rule_ids', [])}
        propagated = set(transitive_nodes(graph, seeds))
        impacted_members = {k for k, b in bindings.items() if propagated & set(b.get('node_ids', []))}
        require(impacted_members <= set(members), '共享参数/依据传播越出声明成员，须补全范围或专业拆分；不得影响成员例外')
        replace_row(graph[GRAPH_COLLECTIONS[collection]], ident, before, after)
    elif mutation.get('kind') == 'relationship':
        network_id, collection = mutation.get('network_id'), mutation.get('collection')
        require(collection in NETWORK_COLLECTIONS, '专业语义变更目标集合无效')
        require(all(network_id in bindings[k].get('network_ids', []) for k in members), '语义变更未绑定相应专业范围')
        target = {'network_id': network_id, 'collection': collection, 'target_id': ident}
        target_members = {k for k, row in bindings.items() if target in row.get('network_targets', [])}
        require(target_members and target_members == set(members),
                '语义目标须显式绑定全部实际成员；不得按同网/同名向例外或新成员继承')
        network = index(state.get('networks', []), label='专业网络').get(network_id)
        require(network is not None, '专业网络范围不存在')
        request = network['request']
        require(request.get('schema') == NETWORK_REQUEST, '专业语义请求版本无效')
        replace_row(request.setdefault(collection, []), ident, before, after, NETWORK_COLLECTIONS[collection])
    else:
        raise ValueError('输入变更须明确parameter或relationship')


def state_expectation(state, view):
    return {'state_version': state['version'], 'source_sha256': view['source_sha256'],
            'result_fingerprint': view['result_fingerprint'], 'input_fingerprint': fingerprint(state)}


def node_diff(before, after):
    old = index(before.get('nodes', [])) if before else {}
    new = index(after.get('nodes', [])) if after else {}
    changed, reused = [], []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a and b and a['fingerprint'] == b['fingerprint']:
            reused.append({'node_id': key, 'fingerprint': b['fingerprint'], 'value': b['value'],
                           'unit': b['unit'], 'review_state': b['review_state'],
                           'review_evidence': copy.deepcopy(b.get('review_evidence'))})
        else:
            changed.append({'node_id': key, 'before': a, 'after': b,
                'old_quantity_confirmation_invalidated': bool(a and a.get('review_state') == 'current'),
                'old_result_confirmation_may_not_be_inherited': bool(a),
                'reason': '输入语义/依据/依赖指纹变化或对象新增删除，数字相同也须重新核对'})
    return changed, reused


def network_diff(before, after):
    changed, reused = [], []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        # The supplied network is the evidence-bound reidentification scope.
        # Conservatively invalidate all its old item confirmations on semantic
        # input changes, including equal resulting numbers; unrelated networks
        # retain byte-verifiable inputs and results.
        if a and b and a == b:
            reused.append({'network_id': key, 'result_fingerprint': fingerprint(b),
                           'evidence': copy.deepcopy(b['partition'].get('affected_scope'))})
        else:
            old_items = a['calculation'].get('items', []) if a else []
            new_items = b['calculation'].get('items', []) if b else []
            changed.append({'network_id': key, 'before_result_fingerprint': fingerprint(a) if a else None,
                'after_result_fingerprint': fingerprint(b) if b else None,
                'before_items': old_items, 'after_items': new_items,
                'retired_item_ids': sorted(set(index(old_items)) - set(index(new_items))),
                'new_item_ids': sorted(set(index(new_items)) - set(index(old_items))),
                'old_result_confirmation_may_not_be_inherited': bool(a),
                'quantity_confirmation_presence': '专业网络计算器不核验人工凭据；不继承任何旧数量确认',
                'reason': '专业网络相关范围输入语义变化；重建对象后重新计算，旧确认不按同名/同值继承'})
    return changed, reused


def execute(request, base_dir=None, queue_only=False):
    require(request.get('schema') == REQUEST_SCHEMA, '裁决输入变更请求版本无效')
    original = copy.deepcopy(request['state'])
    require(isinstance(original.get('version'), str) and original['version'].strip(), '缺少专业输入版本')
    view = normalize_view(copy.deepcopy(request['view']), base_dir)
    if original.get('calculation_graph'):
        require(original['calculation_graph'].get('project_id') == view['project_id'], '计算输入属于其他项目')
    require(view.get('review_workflow', {}).get('enabled') is True, '须使用已有审核事项稳定身份')
    bindings = bindings_for(request, view)
    queue = build_queue(request, view, bindings)
    expected = state_expectation(original, view)
    out = {'schema': RESULT_SCHEMA, 'project_id': view['project_id'], 'review_id': view['review_id'],
        'before': expected, 'queue': queue, 'business_adoption_allowed': False,
        'method_answer_is_quantity_approval': False, 'project_backfill_performed': False,
        'input_changes': [], 'held_changes': [], 'quantity_changes': [], 'reused_evidence': [],
        'status': 'QUEUE_READY', 'state': original}
    if queue_only:
        return out
    handoff = request.get('handoff')
    if handoff is not None:
        require(isinstance(handoff, dict) and handoff.get('schema') == 'qto-review-handoff/1.0', 'reader交接契约无效')
        require(all(handoff.get(k) == view[k] for k in ('project_id', 'review_id', 'source_sha256', 'result_fingerprint')),
                'reader交接属于其他项目、源版本或结果版本')
        require(handoff.get('quantity_changes') == 0 and handoff.get('drafts_are_not_decisions') is True,
                'reader交接不得伪称已完成数量变更或将草稿当裁决')
    raw_feedback = request.get('feedback') if request.get('feedback') is not None else (handoff or {}).get('feedback')
    require(isinstance(raw_feedback, dict), '缺少保存答复或reader交接原批注')
    feedback, unmatched = validate_feedback(copy.deepcopy(raw_feedback), view)
    if handoff is not None and request.get('feedback') is not None:
        h_feedback, h_unmatched = validate_feedback(copy.deepcopy(handoff.get('feedback')), view)
        require(feedback == h_feedback and unmatched == h_unmatched, '另给的答复与reader已核验交接不一致')
    actions = {a['decision_id']: a for a in handoff_actions(view, feedback)}
    if handoff is not None:
        require(index(handoff.get('decision_actions'), 'decision_id', 'reader动作') == actions,
                'reader动作与当前适用/例外/冲突门结果不一致')
    out['reader_handoff_consumed'] = handoff is not None
    out.update(feedback=feedback, unmatched=unmatched, decision_actions=list(actions.values()))
    proposals = index(request.get('decision_changes', []), 'decision_id', '裁决输入变更')
    staged = {}
    for ident, proposal in proposals.items():
        try:
            require(ident in actions, '事项未通过既有适用/冲突/迁移门，或尚未答复')
            action = actions[ident]
            require(action['action'] in ('采用建议', '调整处理'), '退回核查不是采用某项专业输入')
            require(proposal.get('expected') == expected, '源版本、结果或输入指纹已过期')
            require(proposal.get('business_option_id') == action.get('business_option_id')
                    and proposal.get('business_option_id'), '输入方案未绑定当前明确选择')
            mutations = proposal.get('mutations')
            require(isinstance(mutations, list) and mutations, '缺少可追溯原输入和新输入')
            candidate = copy.deepcopy(original)
            for mutation in mutations:
                apply_mutation(candidate, mutation, action['scope']['member_keys'], bindings)
            staged[ident] = (proposal, action)
        except (ValueError, KeyError, TypeError) as exc:
            out['held_changes'].append({'decision_id': ident, 'reason': str(exc), 'proposal': proposal})
    # Two applicable decisions that edit the same target differently hold both;
    # overlapping member names alone never manufacture or inherit a decision.
    ownership = defaultdict(list)
    for ident, (proposal, _) in staged.items():
        for mutation in proposal['mutations']:
            ownership[mutation_target(mutation)].append((ident, mutation))
    conflicts = set()
    for target, edits in ownership.items():
        if len(edits) > 1:
            conflicts.update(i for i, _ in edits)
            for ident, _ in edits:
                out['held_changes'].append({'decision_id': ident, 'reason': '多个输入变更重叠同一目标，须显式合并核对；不按时间采用',
                    'target': list(target), 'candidate_decision_ids': sorted({i for i, _ in edits})})
    state = copy.deepcopy(original)
    applied = []
    baseline_networks = {}
    rebuilt_networks = {}
    for ident, (proposal, action) in staged.items():
        if ident in conflicts:
            continue
        candidate = copy.deepcopy(state)
        try:
            for mutation in proposal['mutations']:
                apply_mutation(candidate, mutation, action['scope']['member_keys'], bindings)
            if any(m['kind'] == 'parameter' for m in proposal['mutations']):
                recalculate(candidate['calculation_graph'], base_dir=base_dir)
            for network_id in {m['network_id'] for m in proposal['mutations'] if m['kind'] == 'relationship'}:
                before_network = index(original.get('networks', []))[network_id]
                require(before_network['request'].get('project_id') == view['project_id'], '专业网络属于其他项目')
                require(str(before_network['request'].get('source_sha256', '')).lower() in {s['sha256'].lower() for s in view['sources']},
                        '专业网络原图不属于当前复核来源版本')
                if network_id not in baseline_networks:
                    baseline_networks[network_id] = electrical_partition(before_network, base_dir)
                changed_network = index(candidate.get('networks', []))[network_id]
                rebuilt = electrical_partition(changed_network, base_dir)
                rebuilt_networks[(network_id, fingerprint(changed_network))] = rebuilt
                require(rebuilt['calculation'].get('project_id') == view['project_id'], '专业重识别输入属于其他项目')
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            out['held_changes'].append({'decision_id': ident, 'reason': '专业重算未接通或输入无效:' + str(exc),
                                       'proposal': proposal, 'recalculation_succeeded': False})
            continue
        state = candidate
        applied.append((ident, proposal, action))
    if not applied:
        out['status'] = 'REVIEW_REQUIRED' if out['held_changes'] or unmatched else 'NO_INPUT_CHANGE'
        return out
    # Calculate actual baseline and changed numerical values. Verified unchanged
    # human evidence is preserved by the existing calculator, never synthesized.
    before_run = after_run = None
    if original.get('calculation_graph'):
        before_run = recalculate(original['calculation_graph'], request.get('previous_calculation_run'),
                                 base_dir=base_dir, previous_base_dir=base_dir)
        after_run = recalculate(state['calculation_graph'], before_run, base_dir=base_dir,
                                previous_base_dir=base_dir)
        changed, reused = node_diff(before_run, after_run)
        out['quantity_changes'].extend(changed)
        out['reused_evidence'].extend(reused)
        out.update(before_calculation_run=before_run, calculation_run=after_run)
    old_networks, new_networks = {}, {}
    changed_network_ids = {m['network_id'] for _, p, _ in applied for m in p['mutations']
                           if m['kind'] == 'relationship'}
    old_by_id, new_by_id = index(original.get('networks', [])), index(state.get('networks', []))
    for ident in changed_network_ids:
        old_networks[ident] = baseline_networks.get(ident) or electrical_partition(old_by_id[ident], base_dir)
        new_networks[ident] = rebuilt_networks.get((ident, fingerprint(new_by_id[ident]))) or electrical_partition(new_by_id[ident], base_dir)
    for ident in sorted(set(old_by_id) - changed_network_ids):
        out['reused_evidence'].append({'network_id': ident, 'input_fingerprint': fingerprint(old_by_id[ident]),
            'reuse_scope': '专业网络输入原字节语义未变；本次未重读原图或核准数量',
            'quantity_approval_created': False})
    changed, reused = network_diff(old_networks, new_networks)
    out['quantity_changes'].extend(changed)
    out['reused_evidence'].extend(reused)
    out.update(before_network_runs=old_networks, network_runs=new_networks)
    state['version'] = original['version'] + '+change-' + fingerprint([p for _, p, _ in applied])[:12]
    before_results = fingerprint({'calculation_run': before_run, 'network_runs': old_networks})
    after_results = fingerprint({'calculation_run': after_run, 'network_runs': new_networks})
    after = state_expectation(state, view)
    # The changed inputs have not yet regenerated the review HTML. Never label
    # the old displayed result fingerprint as the newly computed result.
    after.update(result_fingerprint=after_results,
                 review_view_result_fingerprint=view['result_fingerprint'],
                 review_view_requires_regeneration=True)
    out.update(state=state, after=after)
    out['calculation_result_fingerprints'] = {'before': before_results, 'after': after_results}
    for ident, proposal, action in applied:
        out['input_changes'].append({'decision_id': ident, 'decision_revision': next(d['revision'] for d in decisions(view) if d['id'] == ident),
            'annotation_key': action['annotation_key'], 'business_option_id': proposal['business_option_id'],
            'before': expected, 'after': after, 'scope': action['scope'], 'exception_of': action.get('exception_of'),
            'input_diff': copy.deepcopy(proposal['mutations']), 'user_method_answer': action,
            'quantity_approval_created': False, 'professional_recalculation_executed': True,
            'semantic_reidentification_executed': any(m['kind'] == 'relationship' for m in proposal['mutations'])})
    blocked = after_run and after_run['status'] == 'REVIEW_REQUIRED'
    unresolved = any(r['partition'].get('review_items') for r in new_networks.values())
    out['status'] = 'REVIEW_REQUIRED' if out['held_changes'] or unmatched or blocked or unresolved else 'RECALCULATED'
    return out


def queue_markdown(result):
    def esc(value):
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    lines = ['# 高影响假定集中处理队列', '',
        '首轮可继续工作先完成；下表为方法事项，答复后仍须专业复算，不代表数量核准。', '',
        '| 顺序 / 稳定身份 | 事项 / 范围与例外 | 推荐 / 替代 | 来源 | 数量影响 | 未确认内部口径 / 可继续与暂挂 |',
        '|---|---|---|---|---|---|']
    for pos, row in enumerate(result['queue']['items'], 1):
        recommendation = row.get('recommendation') or {}
        impacts = []
        for impact in row['quantity_impacts']:
            value = impact.get('value')
            if value is None:
                lo, hi = impact.get('min'), impact.get('max')
                value = f'{lo}～{hi}' if lo is not None and hi is not None else '未知（不得按0）'
            impacts.append(f"{value} {impact['unit']}；{impact['level']}；{impact['scope']}")
        columns = [f"{pos} / {row['decision_id']}（{row['revision']}）",
            row['title'] + '；对象：' + '、'.join(row['scope']['member_keys']) +
            '；排除：' + ('、'.join(row['scope']['excluded_member_keys']) or '无') +
            '；独立例外：' + ('、'.join(row['held_by_exception_member_keys']) or '无') +
            '；例外指向：' + str(row.get('exception_of') or '无'),
            str(recommendation.get('text') or '待专业形成建议') + '；替代：' +
            '、'.join(o['id'] + '：' + o['title'] for o in row['alternatives']),
            '；'.join(e['id'] + '：' + e['source'] + '—' + e['detail'] for e in row['sources']) or '来源待补',
            '；'.join(impacts) or '未知（不得按0）',
            str(row.get('internal_working_basis') or '未提供内部采用口径，保留未决') +
            '；可继续：' + ('、'.join(row['continue_member_keys']) or '无') +
            '；决定性缺口暂挂：' + ('、'.join(row['blocked_member_keys']) or '无')]
        lines.append('| ' + ' | '.join(esc(x) for x in columns) + ' |')
    lines += ['', '排序依次参考：' + '、'.join(result['queue']['priority_dimensions']) +
              '。不同单位分别保留，数量位次只在同单位内比较。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--queue-only', action='store_true')
    parser.add_argument('--queue-markdown', type=Path, help='可选的可读队列表；完整数据仍在JSON回执')
    args = parser.parse_args()
    try:
        require(not args.output.exists(), '输出已存在，请使用新的成果版本名')
        require(not args.queue_markdown or not args.queue_markdown.exists(), '可读队列已存在，请使用新版本名')
        require(not args.queue_markdown or args.queue_markdown.resolve() != args.output.resolve(), 'JSON回执与可读队列表不得使用同一路径')
        request = json.loads(args.input.read_text(encoding='utf-8-sig'))
        result = execute(request, args.input.resolve().parent, args.queue_only)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        if args.queue_markdown:
            args.queue_markdown.parent.mkdir(parents=True, exist_ok=True)
            args.queue_markdown.write_text(queue_markdown(result), encoding='utf-8')
        print(json.dumps({'status': result['status'], 'output': str(args.output.resolve()),
            'applied': len(result['input_changes']), 'held': len(result['held_changes']),
            'quantity_change_count': len(result['quantity_changes']), 'business_adoption_allowed': False}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(json.dumps({'status': 'INPUT_REQUIRED', 'error': str(exc), 'input_revision_created': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
