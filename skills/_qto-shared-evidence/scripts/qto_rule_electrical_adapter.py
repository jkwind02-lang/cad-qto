"""Explicit T35 source/partition/ledger/calculator adapter for rule candidates.

No topology or object identity is guessed. All selected targets are explicitly
mapped and the resulting context is bound by the separately saved human method record.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
from qto_review_identity import fingerprint, require

ELECTRICAL = Path(__file__).resolve().parents[2] / 'qto-electrical-routes/scripts'
SCHEMA = 'qto-rule-electrical-network-evidence/1.0'
FIELDS = {
    'physical_segment': {'length_m', 'conductor_quantity_m', 'known_conductor_quantity_m',
                         'physical_channel_quantity_m'},
    'calculation_item': {'length_m', 'known_length_m'},
    'circuit_quantity': {'complete_length_m', 'known_length_m'},
    'connection': {'traced_path_length_m'},
}


def replay_document(value):
    """Canonicalize only verified execution-module locations, never CAD data paths.

    T27's public contract allows byte-identical checkouts. Its mixed dependency
    maps can contain both the frozen producer and executing consumer aliases of
    one module. Every alias is read and hash-verified before using its fixed
    skills-relative identity; the complete executing module set stays required.
    """
    from qto_cad_source_contract import verify_source_file
    from qto_geometric_unit_evidence import verify_geometric_unit_runtime_dependencies
    from trace_drawn_routes import electrical_unit_runtime_dependencies
    document = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    provenance = document.get('provenance', {})
    receipt = provenance.get('geometric_unit_consumption')
    if not isinstance(receipt, dict):
        return document
    current = electrical_unit_runtime_dependencies()
    def identity(path):
        normalized = Path(path).as_posix()
        return 'skills/' + normalized.split('/skills/', 1)[1] if '/skills/' in normalized else None
    modules = {identity(path): digest for path, digest in current.items()}
    require(None not in modules and len(modules) == len(current), '当前执行模块身份缺失或重复')
    for path, digest in current.items():
        verify_source_file({'path': path, 'sha256': digest}, '当前规则重放执行模块')
    def dependencies(values, *, complete):
        require(isinstance(values, dict), '生产代码/原源依赖映射缺失')
        normalized, found = {}, set()
        for path, digest in values.items():
            module = identity(path)
            key = path
            if module in modules:
                verify_source_file({'path': path, 'sha256': digest}, '已保存生产执行模块')
                require(digest == modules[module], '当前执行模块字节与已保存生产依赖不同')
                key = 'module:' + module
                found.add(module)
            require(key not in normalized or normalized[key] == digest, '同模块执行路径别名字节冲突')
            normalized[key] = digest
        require(not complete or found == set(modules), '专业生产代码依赖模块集合不完整或变化')
        return normalized
    for key in ('dependency_fingerprints_before', 'dependency_fingerprints_after'):
        provenance[key] = dependencies(provenance.get(key), complete=True)
    receipt['dependency_fingerprints'] = dependencies(receipt.get('dependency_fingerprints'), complete=False)
    for owner in (receipt.get('credential'), receipt.get('checked_evidence')):
        require(isinstance(owner, dict), '几何单位凭据/实际检查内容缺失')
        rows = owner.get('runtime_dependencies')
        verify_geometric_unit_runtime_dependencies(rows)
        for row in rows:
            row['path'] = 'module:skills/' + row['module']
    if 'producer_dependencies' in receipt:
        receipt['producer_dependencies'] = dependencies(receipt['producer_dependencies'], complete=True)
    return document


def consume(spec, *, base, view, decision, active, read_ref, ref):
    require(isinstance(spec, dict) and spec.get('schema') == SCHEMA, '网络候选 adapter 版本无效')
    require(isinstance(spec.get('network_id'), str) and spec['network_id'].strip(), '须显式声明 network_id')
    require(spec.get('decision_id') == decision['id'] and spec.get('decision_revision') == decision['revision'],
            '网络证据未绑定当前事项和版本')
    paths, docs = {}, {}
    for key in ('manifest', 'request', 'partition', 'ledger', 'calculation_run'):
        paths[key], docs[key] = read_ref(spec.get(key), base)
    request = docs['request']
    require(request.get('schema') == 'qto-electrical-route-network-request/1.0' and
            request.get('project_id') == view['project_id'] and request.get('scope_id') == spec.get('scope_id'),
            '网络请求项目或 scope 不一致')
    require(request.get('source_sha256') in {s['sha256'] for s in view['sources']}, '网络原源不属于当前视图')
    if str(ELECTRICAL) not in sys.path:
        sys.path.insert(0, str(ELECTRICAL))
    from partition_electrical_route_network import partition_network, clean
    from calculate_electrical_paths import calculate
    fresh = partition_network(paths['manifest'], copy.deepcopy(request), base_path=paths['request'].parent)
    # The official writer persists clean(value) as JSON. Compare that complete
    # document, never a subset or a cleaned version of the supplied evidence.
    # Keep the full raw replay fingerprint below, including real underscore
    # block names omitted by the producer's existing serialization contract.
    raw_replay = replay_document(fresh)
    persisted = replay_document(clean(fresh))
    require(persisted == replay_document(docs['partition']) and
            fresh['network_fingerprint'] == spec.get('network_fingerprint'),
            '网络 partition 或版本已变化，须重新运行 producer')
    require(docs['ledger'] == persisted['ledger'], '网络路径台账与实际 partition 不一致')
    calculated = calculate(copy.deepcopy(docs['ledger']))
    calculated = json.loads(json.dumps(calculated, ensure_ascii=False, allow_nan=False))
    raw_reference = json.loads(json.dumps(fresh['reference_calculation'], ensure_ascii=False, allow_nan=False))
    require(calculated == raw_reference == docs['calculation_run'] and
            json.loads(json.dumps(clean(calculated), ensure_ascii=False, allow_nan=False)) ==
            persisted['reference_calculation'],
            '网络计算 producer 结果与实际台账重算不一致')
    require(calculated.get('business_adoption_allowed') is False and fresh.get('business_adoption_allowed') is False,
            '网络业务量采用门禁不得开启')
    pools = {'physical_segment': {r['id']: r for r in persisted['physical_segments']},
             'calculation_item': {r['id']: r for r in calculated['items']},
             'circuit_quantity': {r['circuit_id']: r for r in persisted['circuit_quantities']},
             'connection': {r['id']: r for r in persisted['connections']}}
    objects = {o['catalog_key']: o for o in view['objects']}
    mappings = spec.get('bindings')
    require(isinstance(mappings, list) and mappings and all(isinstance(m, dict) for m in mappings),
            '网络须显式声明对象到实际目标的 bindings')
    bindings, identities = [], set()
    for mapping in mappings:
        key, kind, ident = (mapping.get(k) for k in ('catalog_key', 'target_kind', 'target_id'))
        require(key in active and key in objects and kind in pools and ident in pools[kind],
                '网络绑定越出当前事项有效对象或实际目标不存在')
        target = pools[kind][ident]
        require(mapping.get('target_fingerprint') == fingerprint(target), '网络绑定目标内容指纹不一致')
        require(mapping.get('object_fingerprint') == objects[key]['result_fingerprint'],
                '网络绑定对象成果指纹不一致')
        field = mapping.get('quantity_field')
        require(field is None or field in FIELDS[kind] and field in target, '网络数量字段不属于已支持的实际目标')
        value = None if field is None else target[field]
        source_level = target.get('quantity_level')
        level = ('未决量' if value is None else
                 source_level if source_level is not None else
                 '未分类参考量' if kind == 'calculation_item' else '规则推算量')
        obj = objects[key]
        require(obj['professional'] == 'qto-electrical-routes', '电气网络不可绑定其他专业对象')
        source = mapping.get('value_source')
        if field is None:
            require(source == 'none', '无数量字段的关系绑定须保留未知量')
        elif source == 'attribute':
            name = mapping.get('value_name')
            require(name in obj['attributes'] and obj['attributes'][name] == value,
                    '当前对象属性与网络实际数量不一致')
        elif source == 'quantity':
            matches = [q for q in obj['quantities'] if q.get('name') == mapping.get('value_name')]
            require(len(matches) == 1 and matches[0].get('value') == value and matches[0].get('unit') == 'm' and
                    matches[0].get('level') == level, '当前对象数量与网络实际目标不一致')
        else:
            raise ValueError('网络绑定须明确 quantity / attribute / none 数量来源')
        identity = (key, kind, ident, field)
        require(identity not in identities, '网络对象目标绑定重复')
        identities.add(identity)
        bindings.append({'catalog_key': key, 'adapter_schema': SCHEMA, 'network_id': spec['network_id'],
                         'scope_id': spec['scope_id'], 'network_fingerprint': fresh['network_fingerprint'],
                         'target_kind': kind, 'target_id': ident, 'target_fingerprint': fingerprint(target),
                         'quantity_field': field, 'value': value, 'unit': 'm', 'quantity_level': level,
                         'source_quantity_level': source_level, 'professional': obj['professional'],
                         'object_kind': obj['kind'], 'object_type': obj['category'],
                         'review_state': 'method_evidence_required_not_quantity_approval',
                         'pending_fields': copy.deepcopy(target.get('pending_fields')),
                         'pending_components': copy.deepcopy(target.get('pending_components')),
                         'network_status': fresh['status'], 'calculation_status': calculated['status']})
    dependencies = {}
    provenance = raw_replay['provenance']
    for key in ('fingerprints', 'dependency_fingerprints_before'):
        dependencies.update(provenance.get(key, {}))
    # partition_network already re-read these files; carry their exact identity for later replay/independence.
    context = {'adapter_schema': SCHEMA, 'network_id': spec['network_id'], 'scope_id': spec['scope_id'],
               'request_fingerprint': fingerprint(request), 'network_fingerprint': fresh['network_fingerprint'],
               'partition_fingerprint': fingerprint(raw_replay), 'calculation_fingerprint': fingerprint(calculated),
               'serialized_partition_fingerprint': fingerprint(persisted),
               'serialization_contract': 'partition_electrical_route_network.write_json(clean(value)); full document',
               'source_fingerprint': fresh['source_fingerprint'], 'evidence': {k: ref(p) for k, p in paths.items()},
               'source_dependencies': dependencies, 'pending_review_items': copy.deepcopy(fresh['review_items']),
               'business_adoption_allowed': False}
    return bindings, context
