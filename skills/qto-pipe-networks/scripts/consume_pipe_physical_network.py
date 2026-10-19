"""Replay physical pipe production and deliver one quantity per actual extent."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys

from produce_pipe_diagram_network import (produce, verify_dependencies, dependencies,
    RESULT as NETWORK)
from receive_pipe_diagram_lengths import load_binding
from measure_pipe_diagram_lengths import sha, digest, replay_digest, require, text, write_new, BASIS
from qto_cad_source_contract import verify_source_file

REQUEST = 'qto-pipe-physical-network-consumption/1.0'
RESULT = 'qto-pipe-physical-network-delivery/1.0'


def source_overlap(a, b):
    sa, sb = a['source_span'], b['source_span']
    if (a['drawing']['sha256'], sa['path'], sa['part']) != (b['drawing']['sha256'], sb['path'], sb['part']):
        return False
    return min(sa['to_fraction'], sb['to_fraction']) > max(sa['from_fraction'], sb['from_fraction']) + 1e-12


def same_source_extent(a, b):
    return (a['drawing']['sha256'], a['source_span']['path'], a['source_span']['part'],
            a['source_span']['from_fraction'], a['source_span']['to_fraction']) == (
            b['drawing']['sha256'], b['source_span']['path'], b['source_span']['part'],
            b['source_span']['from_fraction'], b['source_span']['to_fraction'])


def identity(a, b):
    return (a['physical_id'], a['extent_id']) == (b['physical_id'], b['extent_id'])


def port_pair(a):
    return frozenset((a['from_port_id'], a['to_port_id']))


def compatible(a, b):
    return (identity(a, b) and a['system'] == b['system'] and port_pair(a) == port_pair(b)
            and a['specification'] == b['specification']
            and a['measurement_basis'] == b['measurement_basis'] == BASIS
            and a['quantity'] is not None and b['quantity'] is not None
            and math.isclose(a['quantity'], b['quantity'], rel_tol=1e-9, abs_tol=1e-9))


def consume(data):
    require(data.get('schema_version') == REQUEST, '实物管网消费契约不符')
    runtime = dependencies()
    saved = load_binding(data.get('network_result'), '实物管网生产结果')
    require(saved.get('schema_version') == NETWORK, '只接实物管网生产结果')
    verify_dependencies(saved.get('runtime_dependencies'))
    original = load_binding(saved.get('source_request'), '实物管网原生产请求')
    rebuilt = produce(original)
    rebuilt['source_request'] = copy.deepcopy(saved['source_request'])
    require(replay_digest(saved) == replay_digest(rebuilt), '实物管网与固定producer原源重放不符')
    require(text(data.get('network_id'), 'network_id') == rebuilt['network_id'], '消费管网身份不符')
    require(data.get('scope_id') == rebuilt['scope_id'], '消费范围不符')
    text(data.get('scope_basis'), '消费范围依据')
    rows = rebuilt['physical_edges']
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    conflicts = []
    for i, a in enumerate(rows):
        for j in range(i):
            b = rows[j]
            same, ports, overlap = identity(a, b), port_pair(a) == port_pair(b), source_overlap(a, b)
            if same or ports or overlap:
                parent[find(i)] = find(j)
                # Overlapping source ranges with different interval boundaries
                # are never silently deduplicated as one complete extent.
                if not compatible(a, b) or (overlap and not same_source_extent(a, b)):
                    conflicts.append({'left': i, 'right': j, 'same_physical_extent': same,
                        'same_port_pair': ports, 'source_overlap': overlap,
                        'issue': '实物、端口、完整规格、长度或源part区间对应冲突，禁止叠加或补差'})
    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append((index, row))
    items = []
    for index, members in groups.items():
        selected = members[0][1]
        issues = [issue for _, row in members for issue in row['issues']]
        group_conflicts = [c for c in conflicts if find(c['left']) == index]
        issues += [c['issue'] for c in group_conflicts]
        quantity = None if issues or any(row['quantity'] is None for _, row in members) else selected['quantity']
        levels = {row['quantity_level'] for _, row in members}
        level = '未决量' if quantity is None else '方案暂估量' if '方案暂估量' in levels else '规则推算量'
        owner = 'physical_length:' + digest({'network': rebuilt['network_id'],
            'physical_id': selected['physical_id'], 'extent_id': selected['extent_id']})[:24]
        items.append({key: copy.deepcopy(selected[key]) for key in
            ('physical_id', 'extent_id', 'system', 'specification', 'from_port_id', 'to_port_id')} | {
            'quantity_owner': owner, 'quantity': quantity, 'unit': 'm', 'measurement_basis': BASIS,
            'quantity_level': level, 'status': 'unresolved' if quantity is None else
                'deduplicated_working_length' if len(members) > 1 else 'working_length',
            'source_record_ids': [r['record_id'] for _, r in members],
            'representations': [copy.deepcopy(r) for _, r in members], 'issues': issues,
            'deduplicated_representation_count': max(0, len(members)-1) if quantity is not None else 0,
            'business_adoption_allowed': False})
        systems = sorted({r['system'] for _, r in members})
        if len(systems) > 1:
            items[-1].update(system=None, conflicting_systems=systems)
    summaries = {}
    for item in items:
        key = (item['system'], digest(item['specification']), item['quantity_level'])
        group = summaries.setdefault(key, {'system': item['system'], 'specification': item['specification'],
            'quantity_level': item['quantity_level'], 'unit': 'm', 'known_subtotal': 0.,
            'quantity': None, 'quantity_owners': [], 'unresolved_quantity_owners': []})
        group['quantity_owners'].append(item['quantity_owner'])
        if item['quantity'] is None:
            group['unresolved_quantity_owners'].append(item['quantity_owner'])
        else:
            group['known_subtotal'] += item['quantity']
    for group in summaries.values():
        group['quantity'] = None if group['unresolved_quantity_owners'] else group['known_subtotal']
    legacy = rebuilt['legacy_replay']
    verify_source_file(data['network_result'], '消费后生产结果')
    verify_source_file(saved['source_request'], '消费后原生产请求')
    require(runtime == dependencies(), '消费期间运行依赖字节变化，须重新执行')
    return {'schema_version': RESULT, 'network_id': rebuilt['network_id'], 'scope_id': data['scope_id'],
        'scope_basis': data['scope_basis'], 'source_network': data['network_result'],
        'producer_replay': {'status': 'REPLAYED_FROM_SOURCE', 'request': saved['source_request'],
                            'input_fingerprint': rebuilt['input_fingerprint']},
        'runtime_dependencies': runtime, 'quantity_items': items,
        'length_summary': list(summaries.values()),
        'unresolved_quantity_owners': [i['quantity_owner'] for i in items if i['quantity'] is None],
        'unadapted_existing_length_records': legacy['unadapted_length_records'] if legacy else [],
        'preserved_existing_network': legacy['preserved_network'] if legacy else None,
        'quantity_outlet': 'quantity_items[].quantity', 'all_other_values': 'source_or_reference_do_not_add',
        'network_quantity_produced': True, 'scope_complete': False,
        'business_adoption_allowed': False, 'formal_total_quantity': None,
        'recognition_status': 'model_working_interpretation_unreviewed'}


def main():
    parser = argparse.ArgumentParser(description='回源消费并交付唯一实物管网长度出口')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        request_binding = {'path': str(args.input.resolve()), 'sha256': sha(args.input)}
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        result = consume(data)
        verify_source_file(request_binding, '消费后原请求')
        write_new(args.output, result, [args.input] + [r['path'] for r in result['runtime_dependencies']])
        print(json.dumps({'status': 'WORKING_QUANTITY_DELIVERED',
            'quantity_items': len(result['quantity_items']), 'unresolved': len(result['unresolved_quantity_owners']),
            'business_adoption_allowed': False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
