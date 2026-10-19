"""Replay named point/bound-object producers; explicit mappings never infer identity or approval."""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from qto_review_identity import fingerprint, require

ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
POINT = ROOT / 'skills/qto-components-points/scripts'
ELECTRICAL = ROOT / 'skills/qto-electrical-routes/scripts'
SCHEMA = 'qto-rule-object-producer-evidence/1.0'
PRODUCERS = {
    'reconciled_points/1.0': ('skills/qto-components-points/scripts/build_component_instances_from_neutral.py',
                              'skills/qto-components-points/scripts/count_reconciled_instances.py'),
    'bound_electrical_objects/1.0': ('skills/qto-electrical-routes/scripts/bind_electrical_objects.py',),
}


# Stable producer-owned outlets; point display labels never create additional counts.
ELECTRICAL_OUTLETS = {
    '已知参考长度（已含根数）': 'known_reference_length_with_parallel_count',
    '完整参考长度（已含根数）': 'complete_reference_length_with_parallel_count',
    '每根实画路径长度': 'drawn_path_length_each',
    '每根附加长度': 'extra_length_each',
    '正式采用长度': 'official_adopted_length',
}


def quantity_outlet(spec, target, context, quantity_name):
    authority = context['physical_authority']['sha256']
    if spec['producer'] == 'reconciled_points/1.0':
        physical_id = target.get('physical_id')
        require(isinstance(physical_id, str) and physical_id.startswith('PT-'), '点位生产缺少完整物理实例身份')
        instance_path = target.get('instance_path') or physical_id[3:]
        require(isinstance(instance_path, str) and instance_path and physical_id == 'PT-' + instance_path and
                target.get('source_sha256') == authority, '点位完整实例或源版本未对应实际authority')
        physical_instance = {'instance_path': instance_path}
        outlet = 'point.unique_physical_instance_count'
    else:
        physical_id = target.get('physical_id')
        require(isinstance(physical_id, str) and physical_id.strip() and quantity_name in ELECTRICAL_OUTLETS,
                '电气生产缺少实际物理身份或具名正式投影出口')
        physical_instance = {'physical_id': physical_id}
        outlet = 'electrical.quantity_projection.' + ELECTRICAL_OUTLETS[quantity_name]
    return {'schema': 'qto-rule-quantity-outlet/1.0', 'authority_sha256': authority,
            'source_version_sha256': authority, 'physical_instance': physical_instance, 'outlet': outlet}


def modules(producer):
    """Complete local import closure plus the point counter actually invoked by its producer."""
    require(producer in PRODUCERS, '未接入的具名生产格式')
    pending = [ROOT / p for p in PRODUCERS[producer]]
    found = {}
    while pending:
        path = pending.pop().resolve()
        if path in found:
            continue
        found[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            names = ([node.module] if isinstance(node, ast.ImportFrom) else
                     [n.name for n in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                for folder in (path.parent, SHARED, POINT, ELECTRICAL):
                    local = folder / ((name or '').split('.')[0] + '.py')
                    if local.is_file():
                        pending.append(local)
                        break
    return {p.relative_to(ROOT).as_posix(): h for p, h in sorted(found.items())}


def verify_modules(spec, base, read_ref):
    expected = modules(spec['producer'])
    supplied = spec.get('producer_modules')
    require(isinstance(supplied, dict) and set(supplied) == set(expected), '生产模块完整集合缺失或变化')
    for identity, digest in expected.items():
        path, _ = read_ref(supplied[identity], base, raw=True)
        require(path.as_posix().endswith('/' + identity) and supplied[identity]['sha256'] == digest,
                '生产模块身份或字节变化')
    return expected


def run(command, directory):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-B', *map(str, command)], cwd=directory,
                            stdin=subprocess.DEVNULL, env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=180)
    require(result.returncode in (0, 3), '实际生产入口拒收：' + result.stdout + result.stderr)
    return result


def replay(spec, *, base, project, read_ref, ref):
    require(isinstance(spec, dict) and spec.get('schema') == SCHEMA and
            spec.get('producer') in PRODUCERS, '对象生产 adapter 版本或具名格式无效')
    dependencies = verify_modules(spec, base, read_ref)
    paths, docs = {}, {}
    producer = spec['producer']
    fields = ('manifest', 'profile', 'recognition_ledger', 'count_input', 'count_result') if producer == 'reconciled_points/1.0' else (
        'manifest', 'request', 'package', 'ledger', 'calculation_run')
    for field in fields:
        paths[field], docs[field] = read_ref(spec.get(field), base)
    manifest = docs['manifest']
    # The point producer checks streams but older versions did not reread the physical DWG/DXF.
    # Verify both authority and exchange here; preserve each physical source path in the context.
    source_dependencies = []
    source_hashes = set()
    for key in ('authoritative_source', 'exchange_dxf'):
        if manifest.get(key) is not None:
            path, _ = read_ref(manifest[key], paths['manifest'].parent, raw=True)
            source_dependencies.append(ref(path, role=key))
            source_hashes.add(manifest[key]['sha256'])
    authority = next((r for r in source_dependencies if r['role'] == 'authoritative_source'), None)
    require(authority is not None, '生产清单缺少可核物理authority原源')
    artifacts = manifest.get('artifacts')
    require(isinstance(artifacts, dict), '生产清单缺少 artifacts')
    for key in ('layout_entities', 'block_definitions'):
        path, _ = read_ref(artifacts.get(key), paths['manifest'].parent, raw=True)
        source_dependencies.append(ref(path, role=key))
    with tempfile.TemporaryDirectory(prefix='qto-rule-replay-', dir=base) as temp:
        out = Path(temp) / '产物'
        if producer == 'reconciled_points/1.0':
            result = run([ROOT / PRODUCERS[producer][0], '--cad-manifest', paths['manifest'],
                          '--profile', paths['profile'], '--adjudication-ledger', paths['recognition_ledger'],
                          '--output-dir', out], base)
            fresh = json.loads((out / '点位唯一实例计数输入.json').read_text(encoding='utf-8'))
            require(fresh.get('schema_version') == 'qto-reconciled-instance-input/1.0' and
                    fingerprint(fresh) == fingerprint(docs['count_input']), '点位完整生产输入已变化，须重新运行 producer')
            count = run([ROOT / PRODUCERS[producer][1], out / '点位唯一实例计数输入.json'], base)
            calculated = json.loads(count.stdout)
            require(fingerprint(calculated) == fingerprint(docs['count_result']), '点位实际计数消费与保存结果不一致')
            rows = fresh['reconciled_instances']
            require(len({r['physical_id'] for r in rows}) == len(rows), '点位物理身份重复')
            targets = {r['physical_id']: r for r in rows}
            pending = fresh['geometry_review']
        else:
            request = docs['request']
            require(request.get('schema') == 'qto-electrical-object-request/1.0' and
                    request.get('project_id') == project and request.get('source_sha256') == authority['sha256'],
                    '直接绑定请求项目、authority源版本或格式不一致')
            result = run([ROOT / PRODUCERS[producer][0], '--cad-manifest', paths['manifest'],
                          '--request', paths['request'], '--output-dir', out], base)
            fresh = json.loads((out / '电气对象包.json').read_text(encoding='utf-8'))
            from qto_rule_electrical_adapter import replay_document
            for folder in (ELECTRICAL,):
                if str(folder) not in sys.path:
                    sys.path.insert(0, str(folder))
            require(fingerprint(replay_document(fresh)) == fingerprint(replay_document(docs['package'])),
                    '直接对象绑定完整产物已变化，须重新运行 producer')
            calculated = json.loads((out / '数量复算.json').read_text(encoding='utf-8'))
            require(fingerprint(docs['ledger']) == fingerprint(fresh['ledger']) and
                    fingerprint(docs['calculation_run']) == fingerprint(calculated) == fingerprint(fresh['quantity_result']),
                    '直接对象台账或数量结果不一致')
            rows = fresh['objects']
            require(len({r['id'] for r in rows}) == len(rows), '直接绑定对象身份重复')
            targets = {r['id']: r for r in rows}
            pending = {'coverage': fresh['coverage'], 'todo': fresh['todo']}
    verify_modules(spec, base, read_ref)
    for source in source_dependencies:
        read_ref(source, base, raw=True)
    require(calculated.get('business_adoption_allowed') is False, '对象生产不得开启数量采用')
    canonical = fresh
    if producer == 'bound_electrical_objects/1.0':
        canonical = replay_document(fresh)
    context = {'adapter_schema': SCHEMA, 'producer': producer, 'producer_id': spec.get('producer_id'),
               'producer_modules': dependencies, 'source_dependencies': source_dependencies,
               'source_hashes': sorted(source_hashes), 'physical_authority': authority, 'evidence': {k: ref(p) for k, p in paths.items()},
               'full_product_fingerprint': fingerprint(canonical), 'raw_replay_fingerprint': fingerprint(canonical),
               'full_serialized_product_fingerprint': fingerprint(canonical),
               'serialization_contract': 'official producer JSON document; all fields retained; canonical JSON types preserved', 'calculation_fingerprint': fingerprint(calculated),
               'actual_producer_exit': result.returncode, 'pending': copy.deepcopy(pending),
               'business_adoption_allowed': False}
    return targets, fresh, context


def consume(spec, *, base, view, decision, active, read_ref, ref):
    require(spec.get('decision_id') == decision['id'] and spec.get('decision_revision') == decision['revision'],
            '对象生产证据未绑定当前方法事项和版本')
    targets, product, context = replay(spec, base=base, project=view['project_id'], read_ref=read_ref, ref=ref)
    require(set(context['source_hashes']) & {s['sha256'] for s in view['sources']}, '对象原源不属于当前视图')
    mappings = spec.get('bindings')
    require(isinstance(mappings, list) and mappings and all(isinstance(m, dict) for m in mappings),
            '须显式声明当前对象到生产目标的 bindings')
    objects = {o['catalog_key']: o for o in view['objects']}
    bindings, identities, target_identities = [], set(), set()
    for mapping in mappings:
        key, ident = mapping.get('catalog_key'), mapping.get('target_id')
        require(key in active and key in objects and ident in targets, '对象映射越界或实际目标不存在')
        obj, target = objects[key], targets[ident]
        require(mapping.get('target_fingerprint') == fingerprint(target) and
                mapping.get('object_fingerprint') == obj['result_fingerprint'], '生产目标或当前对象指纹已变化')
        if spec['producer'] == 'reconciled_points/1.0':
            require(obj['professional'] == 'qto-components-points' and obj['kind'] == 'point', '点位生产仅映射点位对象')
            scene = next((s for s in view['scenes'] if s['id'] == obj['scene_id']), None)
            require(scene is not None and scene['coordinate_unit'] == target['coordinate_unit'] and
                    obj['points'] == [target['point'][:2]] and
                    next((s['sha256'] for s in view['sources'] if s['id'] == scene['source_id']), None) == target['source_sha256'],
                    '当前点位定位、原源或坐标单位不对应生产物理实例；不按近邻认领')
            quantity = {'name': mapping.get('quantity_name'), 'value': 1,
                        'unit': target['unit'], 'level': target['quantity_level']}
        else:
            require(obj['professional'] == 'qto-electrical-routes' and obj['catalog_key'] == target['catalog_key'],
                    '直接绑定不得替换生产catalog_key或跨专业认领')
            from bind_electrical_objects import quantity_projection
            qi = next((r for r in product['quantity_result'].get('items', []) if r['id'] == ident), None)
            li = next((r for kind in ('channels', 'circuits') for r in product['ledger'].get(kind, []) if r['id'] == ident), None)
            projected = quantity_projection(target, qi, li)['quantities']
            matches = [q for q in projected if q['name'] == mapping.get('quantity_name')]
            require(len(matches) == 1, '数量字段不是当前生产正式投影字段')
            quantity = matches[0]
        matches = [q for q in obj['quantities'] if q['name'] == quantity['name']]
        require(len(matches) == 1 and fingerprint(matches[0]) == fingerprint(quantity), '当前对象数量、单位或层级与生产投影不一致')
        outlet = quantity_outlet(spec, target, context, quantity['name'])
        outlet_fingerprint = fingerprint(outlet)
        identity = (key, quantity['name'])
        require(identity not in identities and outlet_fingerprint not in target_identities,
                '当前对象或同一生产目标数量映射重复')
        identities.add(identity)
        target_identities.add(outlet_fingerprint)
        bindings.append({'catalog_key': key, 'adapter_schema': SCHEMA, 'producer': spec['producer'],
                         'producer_id': spec.get('producer_id'), 'source_hashes': context['source_hashes'], 'target_id': ident,
                         'target_fingerprint': fingerprint(target), 'quantity_name': quantity['name'],
                         'quantity_outlet': outlet, 'quantity_outlet_fingerprint': outlet_fingerprint,
                         'value': quantity['value'], 'unit': quantity['unit'], 'quantity_level': quantity['level'],
                         'professional': obj['professional'], 'object_kind': obj['kind'], 'object_type': obj['category'],
                         'source_target': copy.deepcopy(target), 'review_state': 'method_evidence_required_not_quantity_approval'})
    return bindings, context


def inspect_production(path, *, read_ref, ref):
    path = Path(path).resolve()
    request = json.loads(path.read_text(encoding='utf-8-sig'))
    require(request.get('schema') == 'qto-rule-producer-inspection-request/1.0' and
            isinstance(request.get('project_id'), str) and request['project_id'].strip(), '生产检查请求版本或项目缺失')
    targets, _, context = replay(request.get('producer_evidence'), base=path.parent,
                                project=request['project_id'], read_ref=read_ref, ref=ref)
    return {'schema': 'qto-rule-producer-inspection/1.0', 'status': 'NOT_HUMAN_APPROVAL',
            'targets': targets, 'context': context, 'business_adoption_allowed': False,
            'runtime_changed': False, 'reference_changed': False, 'maturity_changed': False}
