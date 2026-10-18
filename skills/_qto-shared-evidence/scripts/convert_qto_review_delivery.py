"""Declarative JSON -> shared delivery contract; never a project page adapter.

Run with --input source.json --mapping mapping.json --output delivery.json.
The input and its extensions remain intact. A mapping declares business roles;
the converter does not infer adoption, evidence, scope or zero from prose.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path

from qto_review_contract import normalize_view

SCHEMA = 'qto-review-delivery-projection/1.0'
MISSING = object()


def lookup(value, path, default=MISSING):
    if not path: return value
    parts = path if isinstance(path, list) else path.split('.')
    for part in parts:
        if isinstance(value, dict) and part in value: value = value[part]
        elif isinstance(value, list) and str(part).isdigit() and int(part) < len(value): value = value[int(part)]
        else:
            if default is MISSING: raise ValueError('projection: 缺少字段 ' + '.'.join(map(str, parts)))
            return default
    return value


def expr(spec, context):
    if isinstance(spec, list): return [expr(x, context) for x in spec]
    if not isinstance(spec, dict): return copy.deepcopy(spec)
    if '$literal' in spec: return copy.deepcopy(spec['$literal'])
    if '$path' in spec:
        path = spec['$path']
        head, _, tail = path.partition('.')
        return copy.deepcopy(lookup(context[head], tail, spec.get('default', MISSING)))
    if '$first' in spec:
        for val in spec['$first']:
            result = expr(val, context)
            if result is not None and result != '' and result != []: return result
        return copy.deepcopy(spec.get('default'))
    if '$enum' in spec:
        value = expr(spec['$enum'], context)
        key = 'null' if value is None else str(value)
        if key in spec['values']: return copy.deepcopy(spec['values'][key])
        if 'default' not in spec: raise ValueError('projection: 未声明值的角色/状态 ' + key)
        return copy.deepcopy(spec['default'])
    if '$join' in spec:
        values = expr(spec['$join'], context)
        return spec.get('separator', '；').join(str(v) for v in values if v is not None and v != '')
    if '$in' in spec:
        return expr(spec['$in'][0], context) in expr(spec['$in'][1], context)
    if '$each' in spec:
        values = expr(spec['$each'], context)
        if not isinstance(values, list): raise ValueError('projection: $each输入须为列表')
        return [expr(spec['fields'], {**context, 'owner': context['value'], 'value': v, 'index': str(n)}) for n, v in enumerate(values)]
    if '$as_list' in spec:
        value = expr(spec['$as_list'], context)
        return value if isinstance(value, list) else [] if value is None else [value]
    if '$lookup' in spec:
        item = spec['$lookup']
        cs = item['collection']
        if isinstance(cs, dict) and '$path' in cs:
            head, _, tail = cs['$path'].partition('.')
            collection = lookup(context[head], tail, cs.get('default', MISSING))
        else: collection = expr(cs, context)
        key = expr(item['key'], context)
        return copy.deepcopy(lookup(collection.get(key, {}), item.get('path', []), item.get('default')))
    return {k: expr(v, context) for k, v in spec.items()}


def records(raw, config):
    collection = lookup(raw, config['path'])
    pairs = collection.items() if isinstance(collection, dict) else enumerate(collection)
    for key, parent in pairs:
        children = lookup(parent, config['children'], []) if 'children' in config else None
        values = enumerate(children) if children is not None else [(key, parent)]
        for subkey, value in values:
            path = config['path'] if isinstance(config['path'], list) else config['path'].split('.')
            path = [*path, str(key), *([*config['children'].split('.'), str(subkey)] if 'children' in config and isinstance(config['children'], str) else [*config['children'], str(subkey)] if 'children' in config else [])]
            locator = '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in path)
            context = {'root': raw, 'parent': parent, 'value': value, 'key': str(subkey), 'record_locator': locator}
            if 'where' in config and not expr(config['where'], context): continue
            yield expr(config['fields'], context), copy.deepcopy(value), context


def coverage(state, hint, pending=None):
    if state not in ('complete', 'partial', 'unknown') or not isinstance(hint, str) or not hint.strip():
        raise ValueError('projection: 须从明确字段映射覆盖状态和范围说明')
    computed = [hint] if state != 'unknown' else []
    unknown = [pending or ('未形成有据量：' + hint)] if state != 'complete' else []
    if set(computed) & set(unknown): raise ValueError('projection: 已算和未算范围相同')
    return {'included': computed + unknown, 'excluded': []}, {'state': state, 'computed_scope': computed, 'uncomputed_scope': unknown}


def project_delivery(raw, mapping, input_sha256=None):
    if mapping.get('schema') != SCHEMA: raise ValueError('projection: mapping schema无效')
    if mapping.get('assistant_work_complete') is not True: raise ValueError('projection: 须先完成助手可执行工作')
    view = copy.deepcopy(raw)
    recorded = mapping.get('recorded_adoption')
    input_sha256 = input_sha256 or hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    if recorded:
        if not isinstance(recorded.get('current_roles'), list) or not recorded['current_roles'] or not recorded.get('role_path'):
            raise ValueError('projection: 冻结采用记录迁移须声明原角色字段与明确当前角色值')
    itemdata = []
    items = {}
    for fields, record, context in records(raw, mapping['items']):
        item = {k: fields[k] for k in ('id', 'title', 'unit', 'quantity', 'original_quantity', 'dimensions')}
        if item['id'] in items: raise ValueError('projection: 条目重复')
        state = fields['coverage_state']
        item['scope'], item['coverage'] = coverage(state, fields['scope_hint'], fields.get('pending_scope'))
        item.update(current_row_ids=[], reference_row_ids=[], historical_row_ids=[])
        for field in ('display', 'comparison_item_id', 'original_row_present', 'current_zero_evidence', 'original_unit'):
            if field in fields: item[field] = fields[field]
        items[item['id']] = item
        itemdata.append(item)
    rowdata = {}
    rowrecords = {}; rowlocators = {}
    for config in mapping['row_sources']:
        for fields, record, context in records(raw, config):
            rid = fields['id']
            if rid in rowdata and config.get('duplicates') != 'replace': raise ValueError('projection: 明细编号重复 ' + rid)
            rowdata[rid] = fields
            rowrecords.setdefault(rid, []).append(record)
            rowlocators.setdefault(rid, context['record_locator'])
    objects = {o['catalog_key']: o for o in view['objects']}
    scenes = {s['id']: s for s in view['scenes']}
    sources = {s['id']: s for s in view['sources']}
    retired = set(expr(mapping.get('retired_member_keys', []), {'root': raw}))
    rows, adopted = [], set()
    for rid, fields in rowdata.items():
        iid = fields['item_id']
        if iid not in items: raise ValueError('projection: 明细未找到条目 ' + iid)
        item = items[iid]
        state = fields['state']
        if state not in ('current', 'reference', 'historical', 'retired'): raise ValueError('projection: 未声明明细采用角色')
        hint = fields.get('scope_hint') or item['scope']['included'][0]
        covstate = fields.get('coverage_state', 'complete')
        # Historical records retain their scope, not an assertion of this-period coverage.
        if covstate == 'unknown': covstate = 'partial'
        if fields.get('scope') is not None and fields.get('coverage') is not None:
            sc, cv = copy.deepcopy(fields['scope']), copy.deepcopy(fields['coverage'])
        else: sc, cv = coverage(covstate, hint, fields.get('pending_scope'))
        row = {'id': rid, 'item_id': iid, 'title': fields.get('title') or item['title'],
               'unit': fields.get('unit') or item['unit'], 'quantity': fields['quantity'], 'state': state,
               'basis': fields['basis'], 'scope': sc, 'coverage': cv,
               'member_keys': fields.get('member_keys', []), 'evidence_member_keys': fields.get('evidence_member_keys', []),
               'source_records': rowrecords[rid]}
        if state in ('current', 'reference'):
            if state == 'current' and set(row['member_keys']) & retired:
                raise ValueError('projection: 原记录明确当前成员与退役声明冲突，不自动取舍 ' + rid)
            row['member_keys'] = [k for k in row['member_keys'] if k not in retired]
            row['evidence_member_keys'] = [k for k in row['evidence_member_keys'] if k not in retired]
            if state == 'current': adopted.update(row['member_keys'])
            if fields.get('evidence') is not None:
                row['evidence'] = fields['evidence']
            else:
                refs = []
                scan = ' '.join(str(fields.get(k) or '') for k in mapping.get('evidence_source_fields', ['drawing', 'locator', 'basis']))
                for sid in sources:
                    # Token matching prevents D01 from silently binding D010.
                    import re
                    if re.search(r'(?<![A-Za-z0-9])' + re.escape(sid) + r'(?![A-Za-z0-9])', scan):
                        refs.append({'source_id': sid, 'locator': fields.get('locator') or fields.get('drawing') or fields['basis']})
                for key in row['member_keys'] + row['evidence_member_keys']:
                    scene = scenes.get(objects.get(key, {}).get('scene_id'), {})
                    if scene.get('source_id') in sources:
                        ref = {'source_id': scene['source_id'], 'locator': objects[key]['source']}
                        if ref not in refs: refs.append(ref)
                if state == 'current' and recorded:
                    original_record = rowrecords[rid][0]
                    role = lookup(original_record, recorded['role_path'], None)
                    if role not in recorded['current_roles']:
                        raise ValueError('projection: 原始记录未明确声明当前采用角色，不能迁移为主量 ' + rid)
                    row['evidence'] = {'state': 'verified', 'method': 'adopted_record', 'source_refs': refs, 'input_facts': [],
                                       'new_measurement_performed': False,
                                       'provenance': {'input_sha256': input_sha256, 'record_id': rid,
                                                      'record_locator': rowlocators[rid], 'verification_scope': 'frozen_record_identity_only',
                                                      'role_path': recorded['role_path'], 'role_value': role}}
                    method = 'adopted_record'
                elif state == 'current' and not refs:
                    raise ValueError('projection: 当前明细缺可绑定的来源证据，须在映射中显式提供evidence: ' + rid)
                else: method = fields.get('method', 'measured')
                if method != 'adopted_record':
                    row['evidence'] = {'state': 'verified' if state == 'current' else 'pending', 'method': method, 'source_refs': refs,
                                       'input_facts': fields.get('input_facts', [])}
                if state == 'current' and method == 'rule' and not row['evidence']['input_facts']:
                    raise ValueError('projection: 规则方法已批不能替代输入已证，须映射input_facts: ' + rid)
        if state == 'reference':
            mains = [x['id'] for x in rowdata.values() if x['item_id'] == iid and x['state'] == 'current']
            relationship = fields['reference_relationship']
            # The original source's containment statement is retained even when
            # no main quantity exists. It never creates a main value or addition.
            links = fields.get('reference_current_row_ids', [])
            row['reference'] = {'relationship': relationship, 'current_row_ids': links,
                                'prerequisites': fields['reference_prerequisites'], 'addition': 'do_not_add'}
            if relationship == 'includes_current' and not links:
                if not mains: row['reference']['current_quantity_absent'] = True
                row['reference'].update(relationship='unknown', source_claimed_relationship=relationship,
                                        association_state='scope_only', scope_statement=hint,
                                        association_reason='原记录关系声明；逐明细包含范围未证，不得相加。不把同清单全部主量自动挂接')
        elif state in ('historical', 'retired'):
            row['history_reason'] = fields['history_reason']
        item['current_row_ids' if state == 'current' else 'reference_row_ids' if state == 'reference' else 'historical_row_ids'].append(rid)
        rows.append(row)
    if adopted & retired: raise ValueError('projection: 明确采用与退役成员冲突，不得自动裁决')
    missing = []
    for fields, record, context in records(raw, mapping.get('missing_groups', {'path': [], 'where': False, 'fields': {}})):
        if not fields: continue
        fields['source_record'] = record
        if mapping.get('recorded_missing_facts'):
            for n, fact in enumerate(fields.get('verified_facts', [])):
                fact_path = mapping['recorded_missing_facts']['facts_path'].replace('.', '/')
                suffix = '/' + str(n) if isinstance(lookup(record, mapping['recorded_missing_facts']['facts_path']), list) else ''
                fact['provenance'] = {'input_sha256': input_sha256, 'record_locator': context['record_locator'] + '/' + fact_path + suffix,
                                      'verification_scope': 'frozen_record_identity_only'}
        missing.append(fields)
    grouping = mapping.get('missing_grouping')
    if grouping:
        groups = {}
        guidance = lookup(raw, grouping['guidance_path'], {})
        for child in missing:
            key = child.pop(grouping['key_field'])
            if key not in groups:
                groups[key] = {'id': 'missing-group-' + str(len(groups) + 1), 'title': key,
                               'scope': {'included': [], 'excluded': []}, 'verified_facts': [],
                               'minimum_required': [], 'children': [], 'guidance_record': copy.deepcopy(guidance.get(key))}
            groups[key]['children'].append(child)
            groups[key]['scope']['included'] = list(dict.fromkeys(groups[key]['scope']['included'] + child['scope']['included']))
        missing = list(groups.values())
    d = {'schema': 'qto-review-delivery/1.0', 'mode': mapping.get('mode', 'read_only'), 'assistant_work_complete': True,
         'current_member_keys': sorted(adopted), 'retired_member_keys': sorted(retired),
         'source_evidence_member_keys': sorted(set(objects) - adopted - retired),
         'items': itemdata, 'quantity_rows': rows, 'missing_groups': missing,
         'decision_targets': expr(mapping.get('decision_targets', []), {'root': raw}),
         'coverage_baseline': expr(mapping.get('coverage_baseline', {'state': 'unavailable', 'reason': '迁移输入未提供完整原图可算对象覆盖基准；不以数值相同代替查漏'}), {'root': raw})}
    if recorded or mapping.get('recorded_missing_facts'):
        d['projection_provenance'] = {'schema': 'qto-review-projection-provenance/1.0', 'input_sha256': input_sha256,
                                      'original_source_sha256': raw.get('source_sha256'), 'original_result_fingerprint': raw.get('result_fingerprint'),
                                      'verification_scope': 'frozen_record_identity_only', 'source_identity_preserved': True,
                                      'notice': '来源已采用记录迁移，本次未重算；未重新证实几何或规则输入，不代表正式核准。'}
    if not retired <= set(objects): raise ValueError('projection: 退役对象不存在')
    view['delivery'] = d
    # Synchronize only explicit declared business values, preserving originals,
    # third-party rows, all archived details, opinions and source evidence.
    if mapping.get('synchronize_comparison'):
        for original in (view.get('comparison') or {}).get('items', []):
            item = items.get(original['id'])
            if item is None: continue
            if original['ours'].get('total') != item['quantity']:
                active = [r for r in original.get('detail_rows', []) if r['id'] in item['current_row_ids']]
                if active: raise ValueError('projection: 正式比较采用量与本轮主明细冲突，不自动改量 ' + original['id'])
                if item['quantity'] is not None: raise ValueError('projection: 无主明细的非空采用量须显式零量证据，不自动写入 ' + original['id'])
                original.setdefault('historical_quantity_snapshots', []).append(copy.deepcopy(original['ours']))
                original['ours']['levels'] = {k: None for k in original['ours']['levels']}
                original['ours']['total'] = None
            for r in original.get('detail_rows', []):
                if r.get('excluded') and r['id'] in item['historical_row_ids']: r['excluded']['period'] = 'historical'
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--mapping', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report')
    args = parser.parse_args(argv)
    source, mp, output = Path(args.input), Path(args.mapping), Path(args.output)
    if output.exists(): raise ValueError('projection: 不覆盖现有成果，请使用新输出路径')
    raw_bytes, map_bytes = source.read_bytes(), mp.read_bytes()
    raw, mapping = json.loads(raw_bytes.decode('utf-8-sig')), json.loads(map_bytes.decode('utf-8-sig'))
    projected = project_delivery(raw, mapping, hashlib.sha256(raw_bytes).hexdigest())
    projected['delivery_projection'] = {'schema': SCHEMA, 'input_sha256': hashlib.sha256(raw_bytes).hexdigest(),
                                       'mapping_sha256': hashlib.sha256(map_bytes).hexdigest(),
                                       'quantity_policy': '只投影显式采用角色与冻结业务量；未重新识图或量算；原数据及历史完整保留'}
    normalized = normalize_view(projected, source.parent)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(normalized, ensure_ascii=False, separators=(',', ':'), allow_nan=False), 'utf-8')
    report = {'status': 'PASS', 'input': str(source.resolve()), 'output': str(output.resolve()),
              'input_sha256': hashlib.sha256(raw_bytes).hexdigest(), 'mapping_sha256': hashlib.sha256(map_bytes).hexdigest(),
              'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'result_fingerprint': normalized['result_fingerprint'],
              'items': len(normalized['delivery']['items']), 'quantity_rows': len(normalized['delivery']['quantity_rows']),
              'statistics': normalized['delivery']['comparison_statistics']['counts'],
              'coverage_status': normalized['delivery']['coverage_check']['status'], 'engineering_quantity_acceptance': False}
    if args.report: Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())
