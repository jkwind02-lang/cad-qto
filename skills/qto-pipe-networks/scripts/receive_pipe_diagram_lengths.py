"""Replay source diagram candidates and issue a network candidate receipt.

This binds existing network records for review, without adding a formal length
or changing the network result. Equal lengths or proximity never prove identity.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys

from measure_pipe_diagram_lengths import (measure, sha, digest, require, text,
    verify_runtime_dependencies, runtime_dependencies, write_new, RESULT, BASIS,
    weakest, QUANTITY_LEVELS)
from qto_cad_source_contract import verify_source_file, canonical_scale

REQUEST = 'qto-pipe-diagram-network-receipt-request/1.0'
RECEIPT = 'qto-pipe-diagram-network-candidate-receipt/1.0'


def load_binding(binding, name):
    require(isinstance(binding, dict) and Path(binding.get('path', '')).is_absolute(),
            name + '须用绝对路径和SHA绑定')
    verify_source_file(binding, name)
    return json.loads(Path(binding['path']).read_text(encoding='utf-8-sig'))


def dependency_identity():
    return [{'module': 'qto-pipe-networks/scripts/' + Path(__file__).name,
             'path': str(Path(__file__).resolve()), 'sha256': sha(__file__)}] + runtime_dependencies()


def receive(data):
    require(data.get('schema_version') == REQUEST, '系统图管网候选接收契约不符')
    saved = load_binding(data.get('candidate_result'), '系统图候选')
    require(saved.get('schema_version') == RESULT and saved.get('network_quantity_produced') is False,
            '只消费当前系统图专项候选，不能将网络量或历史汇总冒充候选')
    verify_runtime_dependencies(saved.get('runtime_dependencies'))
    request_binding = saved.get('source_request')
    source_request = load_binding(request_binding, '系统图原请求')
    rebuilt = measure(source_request)
    rebuilt['source_request'] = copy.deepcopy(request_binding)
    require(digest(rebuilt) == digest(saved), '系统图候选与固定producer原源重放不符')
    network_binding = data.get('network_result')
    network = load_binding(network_binding, '既有管网只读参考')
    require(network.get('evidence_version') == 'qto-pipe-network-inference-evidence/1.2',
            '既有管网必须是1.2原始结果，不能再次接收合并量')
    require(network.get('network_id') == text(data.get('network_id'), 'network_id'), '管网身份不一致')
    require(data.get('scope_id') == rebuilt['scope_id'], '消费范围与生产范围不一致')
    text(data.get('scope_basis'), '消费范围依据')
    # Network result bytes are a record reference, not authority for new quantities.
    items, ports = {}, {}
    for row in network.get('segments', []):
        rid = 'segment:' + text(row.get('segment_id'), 'segment_id')
        require(rid not in items, '原管网数量记录重复')
        items[rid] = row
        ports[rid] = row
    for field, prefix, ident in (('valves', 'valve:', 'valve_id'),
                                 ('endpoints', 'endpoint:', 'endpoint_id')):
        for row in network.get(field, []):
            rid = prefix + text(row.get(ident), ident)
            require(rid not in ports, '原管网端口记录重复')
            ports[rid] = row
    for field, prefix, ident in (('bridge_quantity_items', 'bridge:', 'bridge_id'),
                                 ('vertical_quantities', 'vertical:', 'link_id')):
        for row in network.get(field, []):
            counted = row.get('included_in_reconstructed') is True if prefix == 'bridge:' else row.get('counted_elsewhere') is not True
            if counted:
                rid = prefix + text(row.get(ident), ident)
                require(rid not in items, '原管网数量记录重复')
                items[rid] = row
                ports[rid] = row
    relationships = data.get('relationships')
    require(isinstance(relationships, list), '须逐候选声明管网接收/未决去向')
    relation_map = {}
    for relation in relationships:
        rid = text(relation.get('record_id'), 'record_id')
        require(rid not in relation_map, '候选关系重复')
        relation_map[rid] = relation
    require(set(relation_map) == {r['record_id'] for r in rebuilt['segments']}, '候选接收差集不守恒')
    records = []
    for candidate in rebuilt['segments']:
        relation = relation_map[candidate['record_id']]
        text(relation.get('basis'), '物理对应解释')
        require(relation.get('level') in {'confirmed', 'inferred', 'scenario'}, '对应须声明采用层级')
        evidence = relation.get('evidence')
        require(isinstance(evidence, list) and evidence, '对应须绑定本轮证据文件，不接受孤立结论')
        for binding in evidence:
            require(Path(binding.get('path', '')).is_absolute(), '对应证据须为绝对路径')
            verify_source_file(binding, '对应证据')
            text(binding.get('locator'), '对应证据定位')
        coverage = relation.get('coverage')
        require(isinstance(coverage, dict), '须逐项核对既有数量覆盖')
        lists = [coverage.get(key) for key in ('disjoint_record_ids', 'same_extent_record_ids',
                  'partial_overlap_record_ids', 'unknown_record_ids')]
        require(all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in lists),
                '覆盖四分项须为记录编号数组')
        flattened = [rid for values in lists for rid in values]
        require(len(set(flattened)) == len(flattened) and set(flattened) == set(items),
                '既有长度出口覆盖差集不守恒，不能省略或重复')
        issues = list(candidate['issues'])
        endpoint_rows = []
        for key in ('from_network_record_id', 'to_network_record_id'):
            ref = relation.get(key)
            existing = ports.get(ref) if isinstance(ref, str) else None
            endpoint_rows.append(existing)
            if existing is None:
                issues.append('尚未绑定实际管网端口记录: ' + key)
            elif existing.get('system') != candidate['system']:
                issues.append('实际端口系统不符: ' + key)
            elif candidate['specification'].get('dn') is not None and existing.get('dn') != candidate['specification']['dn']:
                issues.append('实际端口DN不符或未知: ' + key)
        if relation.get('from_network_record_id') == relation.get('to_network_record_id'):
            issues.append('两端绑定同一记录但未证明实际不同端口；须先拆分原记录或使用既有端口事实入口')
        for rid in lists[0]:
            old = items[rid]
            if old.get('physical_id') == candidate['physical_id'] and old.get('extent_id') == candidate['extent_id']:
                issues.append('已声明相同实物或区段身份不能同时声明不相交: ' + rid)
        same = lists[1]
        addition = candidate['quantity']
        status = 'candidate_disjoint'
        if lists[2] or lists[3]:
            issues.append('既有实物部分重叠或对应未知；不靠近邻/等长强行扣减或追加')
        if same:
            require(len(same) == 1, '同一完整范围只能指向一个既有数量出口')
            old = items[same[0]]
            old_spec = old.get('specification', {'dn': old.get('dn')})
            old_value = old.get('value') if same[0].startswith('vertical:') else old.get('length')
            identity = (old.get('physical_id') == candidate['physical_id'] and
                        old.get('extent_id') == candidate['extent_id'])
            compatible = (old.get('system') == candidate['system'] and old_spec == candidate['specification']
                          and old.get('measurement_basis', BASIS) == BASIS)
            if old_value is not None:
                require(isinstance(old_value, (int, float)) and not isinstance(old_value, bool)
                        and math.isfinite(old_value) and old_value >= 0, '既有长度无效')
                old_value = old_value * canonical_scale(old.get('unit', network.get('unit')))
            if not identity or not compatible:
                issues.append('同范围缺少完整实物/区段/系统/规格身份，不以等长代替对应')
            elif old_value is None or addition is None or not math.isclose(old_value, addition, rel_tol=1e-9, abs_tol=1e-9):
                issues.append('同一实物范围长度冲突或未知，不能补差凑齐')
            else:
                addition = 0.
                status = 'already_counted_candidate'
        if issues or candidate['quantity'] is None:
            addition = None
            status = 'unresolved'
        records.append({'record_id': candidate['record_id'], 'physical_id': candidate['physical_id'],
            'extent_id': candidate['extent_id'], 'system': candidate['system'],
            'specification': candidate['specification'], 'derived_length': candidate['measured_extent_length'],
            'candidate_addition_quantity': addition, 'unit': 'm', 'measurement_basis': BASIS,
            'derived_length_level': candidate['measured_extent_level'],
            'quantity_level': QUANTITY_LEVELS[weakest([candidate['level'], relation['level']])]
                if candidate['level'] and addition is not None else '未决量', 'status': status,
            'candidate_owner': candidate['quantity_owner'], 'network_quantity_owner': None,
            'relationship': copy.deepcopy(relation), 'issues': issues})
    for binding in [data['candidate_result'], request_binding, network_binding]:
        verify_source_file(binding, '接收后字节复核')
    return {'schema_version': RECEIPT, 'scope_id': data['scope_id'], 'scope_basis': data['scope_basis'],
        'network_id': data['network_id'], 'source_candidate': copy.deepcopy(data['candidate_result']),
        'producer_replay': {'status': 'REPLAYED_FROM_SOURCE', 'request': request_binding,
                            'input_fingerprint': rebuilt['input_fingerprint']},
        'runtime_dependencies': dependency_identity(), 'network_reference': copy.deepcopy(network_binding),
        'network_source_replayed': False, 'network_reference_use': 'hash_bound_record_reference_only',
        'candidate_length_items': records,
        'unprocessed_record_ids': [r['record_id'] for r in records if r['candidate_addition_quantity'] is None],
        'network_quantity_mutation_performed': False, 'network_quantity_produced': False,
        'scope_complete': False, 'business_adoption_allowed': False,
        'limitations': ['候选接收回执不合并既有管网工程量；管网参考结果未在本入口重跑',
                        '标注与实物对应为本轮模型解释，未自动验证图纸语义或人工签认']}


def main():
    parser = argparse.ArgumentParser(description='固定原源重放系统图计长并生成管网候选接收回执')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        result = receive(data)
        protected = [args.input, data['candidate_result']['path'], data['network_result']['path'],
                     result['producer_replay']['request']['path']]
        protected += [r['path'] for r in result['runtime_dependencies']]
        protected += [b['path'] for r in data['relationships'] for b in r['evidence']]
        write_new(args.output, result, protected)
        print(json.dumps({'status': 'CANDIDATE_RECEIPT', 'records': len(result['candidate_length_items']),
                          'unprocessed': len(result['unprocessed_record_ids'])}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
