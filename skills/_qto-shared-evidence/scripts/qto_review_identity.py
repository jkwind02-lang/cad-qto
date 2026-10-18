"""Optional stable review matters, explicit legacy migration and suggestion receipts.

Only consumes professional-owned facts and mappings. Never approves or changes quantities.
Legacy views without an enabled workflow retain their existing behavior.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone

SCHEMA = 'qto-review-workflow/1.0'
ACTIONS = ('采用建议', '调整处理', '退回核查', '待资料确认', '暂不处理')
PREFIX = '审核事项|'
POOLS = {'object': 'objects', 'group': 'groups', 'item': 'items', 'issue': 'issues', 'common': 'common_issues', 'delivery_item': 'items'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def enabled(view):
    return (view.get('review_workflow') or {}).get('enabled') is True


def decisions(view):
    return (view.get('review_workflow') or {}).get('decisions', []) if enabled(view) else []


def target_pools(view):
    c = view.get('comparison') or {}
    pools = {k: view.get(pool, []) if k in ('object', 'group') else c.get(pool, []) for k, pool in POOLS.items()}
    d = view.get('delivery') or {}
    pools['delivery_item'] = d.get('items', [r for r in d.get('quantity_rows', []) if r.get('state') in ('current', 'reference')])
    return pools


def normalize_workflow(view):
    w = view.get('review_workflow')
    if w is None:
        return view
    require(isinstance(w, dict) and w.get('schema') == SCHEMA and isinstance(w.get('enabled'), bool), '审核事项工作流配置无效')
    if not w['enabled']:
        return view
    require(isinstance(w.get('migrate_legacy', False), bool), 'migrate_legacy 须显式为布尔值')
    require(w.get('default_tab', 'quantity') in ('quantity', 'decisions'), '默认入口须为工程量或审核事项')
    ds = w.get('decisions')
    require(isinstance(ds, list), '审核事项须为列表；分类不得冒充整组决定')
    keys = {o['catalog_key'] for o in view['objects']}
    pools = target_pools(view)
    ids = set()
    for d in ds:
        require(isinstance(d, dict), '审核事项无效')
        for k in ('id', 'revision', 'title', 'question'):
            require(isinstance(d.get(k), str) and d[k].strip(), '审核事项缺少 ' + k)
        require(d['id'] not in ids and d['id'] not in ('__proto__', 'constructor', 'prototype'), '审核事项身份重复或无效')
        ids.add(d['id'])
        require(d.get('kind') in ('business_choice', 'data_gap', 'category'), '审核事项须区分业务取舍、资料缺口和分类')
        scope = d.get('scope')
        require(isinstance(scope, dict), '审核事项须明确适用和排除范围')
        for field in ('member_keys', 'excluded_member_keys'):
            vals = scope.get(field)
            require(isinstance(vals, list) and len(set(vals)) == len(vals) and all(x in keys for x in vals), '事项范围成员不存在或重复')
        require(set(scope['excluded_member_keys']) <= set(scope['member_keys']), '排除成员须属于事项适用范围')
        require(isinstance(scope.get('basis'), str) and scope['basis'].strip(), '事项范围须写明口径和边界')
        refs = d.setdefault('refs', [])
        require(isinstance(refs, list), '事项引用须为列表')
        for r in refs:
            require(isinstance(r, dict) and r.get('kind') in pools and any((t.get('catalog_key') if r['kind'] == 'object' else t.get('id')) == r.get('id') for t in pools[r['kind']]), '事项引用须显式绑定当前对象或条目')
        for field in ('facts', 'gaps'):
            d.setdefault(field, [])
            require(isinstance(d[field], list) and all(isinstance(s, str) and s.strip() for s in d[field]), '事项事实或资料缺口须为文字列表')
        d.setdefault('impacts', [])
        require(isinstance(d['impacts'], list), '事项影响须按单位列出')
        for impact in d['impacts']:
            require(isinstance(impact, dict) and all(isinstance(impact.get(k), str) and impact[k].strip() for k in ('unit', 'level', 'scope')) and (impact.get('value') is None or (isinstance(impact['value'], (int, float)) and not isinstance(impact['value'], bool))), '影响须保留单位、层级、范围；未知量用null')
        d.setdefault('business_options', [])
        require(isinstance(d['business_options'], list) and all(isinstance(o, dict) and isinstance(o.get('id'), str) and o['id'] and isinstance(o.get('title'), str) and o['title'] for o in d['business_options']), '业务做法由项目数据提供')
        require(len({o['id'] for o in d['business_options']}) == len(d['business_options']), '业务做法编号重复')
        d.setdefault('evidence', [])
        require(isinstance(d['evidence'], list) and all(isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k].strip() for k in ('id', 'source', 'detail')) for e in d['evidence']), '事项证据须含编号、来源和内容')
        require(len({e['id'] for e in d['evidence']}) == len(d['evidence']), '事项证据编号重复')
        require(d.get('exception_of') is None or isinstance(d['exception_of'], str), '特例须显式指向原事项')
        # Invalid/incomplete recommendations may be displayed but never acquire the quick-adopt path.
        require(d.get('recommendation') is None or isinstance(d['recommendation'], dict), '参考建议须为对象')
    by_id = {d['id']: d for d in ds}
    for d in ds:
        if d.get('exception_of'):
            parent = by_id.get(d['exception_of'])
            require(parent is not None and parent is not d and not parent.get('exception_of'), '特例原事项不存在或特例关系循环/嵌套')
            require(set(d['scope']['member_keys']) <= set(parent['scope']['member_keys']), '特例范围须在原事项范围内')
        if d['kind'] == 'business_choice' and 'delivery' in view:
            from qto_review_delivery import delivery_allows_target
            refs = [*d['refs'], *(by_id[d['exception_of']]['refs'] if d.get('exception_of') else [])]
            require(any(delivery_allows_target(view, r['kind'], r['id']) for r in refs), '事项业务取舍须复用交付已声明的审核目标，不得绕过只读门控')
            require(set(d['scope']['member_keys']) - set(d['scope']['excluded_member_keys']) <= set(view['delivery']['current_member_keys']), '事项不得采用退役或非当前成员')
        semantic = {k: v for k, v in d.items() if k not in ('result_fingerprint', 'snapshot')}
        semantic['member_results'] = [(o['catalog_key'], o['result_fingerprint']) for o in view['objects'] if o['catalog_key'] in d['scope']['member_keys']]
        d['result_fingerprint'] = fingerprint(semantic)
    view['result_fingerprint'] = fingerprint({'result': view['result_fingerprint'], 'review_decisions': [(d['id'], d['result_fingerprint']) for d in ds]})
    return view


def snapshot(d):
    return {k: copy.deepcopy(d.get(k)) for k in ('id', 'revision', 'title', 'result_fingerprint', 'scope', 'exception_of')}


def is_record(r):
    return isinstance(r, dict) and r.get('decision_snapshot') is not None


def reconcile_duplicate_conflict(record):
    """Recover an earlier metadata-only conflict; do not select between different business instructions."""
    mc = record.get('decision_conflict')
    if not isinstance(mc, dict) or not isinstance(mc.get('candidates'), list) or len(mc['candidates']) < 2 or not all(isinstance(r, dict) for r in mc['candidates']):
        return record
    def flatten(r):
        conflict = r.get('decision_conflict') or {}
        candidates = conflict.get('candidates')
        return [x for c in candidates for x in flatten(c)] if isinstance(candidates, list) and len(candidates) >= 2 and all(isinstance(c, dict) for c in candidates) else [r]
    def bare(r):
        return {k: copy.deepcopy(v) for k, v in r.items() if k not in ('history', 'decision_conflict')}
    def semantics(r):
        q = bare(r)
        for k in ('annotation_id', 'updated_at', 'entry_origin', 'applicability_state', 'applicability_reason', 'identity_hold', 'legacy_sources'):
            q.pop(k, None)
        if isinstance(q.get('recommendation_receipt'), dict):
            q['recommendation_receipt'].pop('chosen_at', None)
        if isinstance(q.get('excluded_member_keys'), list):
            q['excluded_member_keys'].sort()
        return canonical(q)
    candidates = flatten(record)
    if not all(isinstance(r.get('history', []), list) for r in [record, *candidates]):
        return record
    if len({semantics(r) for r in candidates}) != 1:
        return record
    out = bare(candidates[0])  # local order, never newest time
    history = [*record.get('history', []), *(h for r in candidates for h in r.get('history', [])), *(bare(r) for r in candidates[1:] if bare(r) != out)]
    out['history'] = [copy.deepcopy(r) for i, r in enumerate(history) if r not in history[:i]]
    sources = [*record.get('legacy_sources', []), *(s for r in candidates for s in r.get('legacy_sources', []))]
    if sources:
        out['legacy_sources'] = [copy.deepcopy(r) for i, r in enumerate(sources) if r not in sources[:i]]
    return out


def clean_record(r, key, complete=True):
    if isinstance(r, dict):
        r = reconcile_duplicate_conflict(r)
    require(is_record(r) and isinstance(r['decision_snapshot'], dict), '审核事项记录缺少身份快照')
    s = r['decision_snapshot']
    require(isinstance(s.get('id'), str) and key == PREFIX + s['id'], '审核事项键与稳定身份不一致')
    require(not any(r.get(k) is not None for k in ('group_id', 'object_snapshot', 'item_snapshot', 'issue_snapshot', 'common_issue_snapshot')), '统一事项不可兼带另一通道身份')
    require(isinstance(s.get('scope'), dict) and all(isinstance(s['scope'].get(k), list) and all(isinstance(x, str) for x in s['scope'][k]) for k in ('member_keys', 'excluded_member_keys')), '审核事项须保留答复时范围快照')
    require(isinstance(r.get('confirmed_result', ''), str) and isinstance(r.get('reason', ''), str), '结论及依据须为文字')
    require(isinstance(r.get('history', []), list), '事项历史须为列表')
    ex = r.get('excluded_member_keys', [])
    require(isinstance(ex, list) and len(set(ex)) == len(ex) and set(ex) <= set(s['scope']['member_keys']), '用户排除范围不得越出答复时成员')
    mc = r.get('decision_conflict')
    require(mc is None or isinstance(mc, dict) and isinstance(mc.get('candidates'), list) and len(mc['candidates']) >= 2 and all(isinstance(x, dict) for x in mc['candidates']), '事项冲突须完整保留候选')
    if complete and not r.get('migration_state'):
        require(r.get('action') in ACTIONS, '请选择审核动作')
        if r['action'] in ('调整处理', '退回核查'):
            require(r.get('user_note', '').strip(), '调整或退回须补充一项必要说明')
        if r['action'] == '采用建议':
            receipt = r.get('recommendation_receipt')
            require(isinstance(receipt, dict) and isinstance(receipt.get('snapshot'), dict) and receipt.get('user_choice') == '采用建议' and receipt.get('reason_origin') == '助手建议引用', '采用建议须保存建议快照、助手来源及用户选择')
    q = copy.deepcopy(r)
    q['annotation_id'] = key
    q.setdefault('history', [])
    return q


def recommendation_problem(d, prior=None, now=None):
    if d.get('kind') != 'business_choice':
        return '资料缺口和分类只读，不接受整组认可'
    r = d.get('recommendation') or {}
    if not all(isinstance(r.get(k), str) and r[k].strip() for k in ('id', 'version', 'text', 'reason', 'assistant_source')):
        return '建议版本、内容、依据或助手来源不足'
    eids = r.get('evidence_ids')
    if not isinstance(eids, list) or not eids or not set(eids) <= {e['id'] for e in d['evidence']}:
        return '建议缺少可追溯依据'
    if any(e.get('state') != 'verified' for e in d['evidence'] if e['id'] in eids):
        return '建议所引依据尚未核实，不可快捷采用'
    if r.get('business_option_id') not in {o['id'] for o in d['business_options']}:
        return '建议缺少本事项的具体业务做法'
    if r.get('status', 'current') != 'current':
        return '参考建议已过期或待核'
    if r.get('expires_at'):
        try:
            expiry = datetime.fromisoformat(r['expires_at'].replace('Z', '+00:00'))
            require(expiry.tzinfo is not None, '建议有效期须包含时区')
            if expiry <= (now or datetime.now(timezone.utc)):
                return '参考建议已过期'
        except (ValueError, TypeError):
            return '建议有效期无效'
    scope = d['scope']
    active = set(scope['member_keys']) - set(scope['excluded_member_keys'])
    if not isinstance(r.get('scope_member_keys'), list) or set(r['scope_member_keys']) != active:
        return '建议适用范围与当前事项不一致；新增范围须另核'
    if prior:
        old = (prior.get('decision_snapshot') or {}).get('scope') or {}
        if set(old.get('member_keys', [])) != set(scope['member_keys']) or set(old.get('excluded_member_keys', [])) != set(scope['excluded_member_keys']):
            return '答复后适用或排除范围变化，须调整处理并核对新增成员'
        if prior.get('migration_state') or prior.get('decision_conflict') or prior.get('identity_hold'):
            return '旧意见迁移或冲突须先人工核对，不可快捷覆盖'
    return None


def assess_record(r, view):
    d = next((d for d in decisions(view) if d['id'] == r['decision_snapshot'].get('id')), None)
    if d is None:
        return 'unmatched', '审核事项身份不存在或工作流未启用'
    if r.get('decision_conflict'):
        return 'conflict', '同一事项意见冲突，完整候选保留；不按时间择一'
    if r.get('migration_state'):
        return 'migration_required', '旧意见已对应事项，尚未重新审核；原话、草稿、历史和来源保留'
    if r.get('identity_hold'):
        return 'migration_required', r['identity_hold']
    if r['decision_snapshot'] != snapshot(d):
        return 'stale', '事项事实、建议、成果或适用排除范围变化，须重新核对'
    if d['kind'] != 'business_choice':
        return 'unmatched', '资料缺口或分类只读，不是业务取舍事项'
    if r.get('exception_of') != d.get('exception_of'):
        return 'unmatched', '特例关系与当前事项不一致'
    if r.get('business_option_id') and r['business_option_id'] not in {o['id'] for o in d['business_options']}:
        return 'unmatched', '业务做法不属于当前事项'
    if r.get('action') == '采用建议':
        reason = recommendation_problem(d)
        receipt = r.get('recommendation_receipt') or {}
        expected = {'snapshot': d.get('recommendation'), 'evidence': [e for e in d['evidence'] if e['id'] in (d.get('recommendation') or {}).get('evidence_ids', [])], 'user_choice': '采用建议', 'reason_origin': '助手建议引用'}
        if reason or any(receipt.get(k) != v for k, v in expected.items()) or r.get('business_option_id') != d['recommendation']['business_option_id'] or r.get('confirmed_result') != d['recommendation']['text'] or r.get('reason') != d['recommendation']['reason']:
            return 'stale', reason or '建议快照、来源或依据与当前建议不一致'
    return 'current', '已审核，待所属专业复算；方法选择不等于数量核准'


def record_ref(r):
    for field, ident, kind in (('item_snapshot', 'item_id', 'item'), ('issue_snapshot', 'issue_id', 'issue'), ('common_issue_snapshot', 'common_issue_id', 'common')):
        if isinstance(r.get(field), dict):
            return kind, r[field].get(ident)
    if r.get('group_id'):
        return 'group', r['group_id']
    return 'object', (r.get('object_snapshot') or {}).get('catalog_key')


def resolve_ref(view, kind, ident):
    pools = target_pools(view)
    target = next((t for t in pools.get(kind, []) if (t.get('catalog_key') if kind == 'object' else t.get('id')) == ident), {})
    return [d['id'] for d in decisions(view) if d['id'] == target.get('decision_id') or {'kind': kind, 'id': ident} in d.get('refs', [])]


def migrate_package(p, view):
    if not enabled(view):
        return p
    archive = p.setdefault('legacy_review_records', [])
    require(isinstance(archive, list) and all(isinstance(x, dict) for x in archive), '旧意见迁移档案无效')
    pending = []
    ds = {d['id']: d for d in decisions(view)}
    for field in ('annotations', 'drafts'):
        for r in p.get(field, {}).values():
            if is_record(r):
                r.pop('identity_hold', None)
    def same_origin(a, b):
        return all(a.get(k) == b.get(k) for k in ('field', 'annotation_key', 'record'))
    # A newly clarified explicit mapping can migrate an archived pending record without losing its original mapping history.
    if view['review_workflow'].get('migrate_legacy', False):
        for a in list(archive):
            if len(a.get('candidate_decision_ids', [])) == 1 or not isinstance(a.get('record'), dict):
                continue
            matches = resolve_ref(view, *record_ref(a['record']))
            linked = any(same_origin(a, b) and b.get('candidate_decision_ids') == matches for b in archive)
            field, key = a.get('field'), a.get('annotation_key')
            if len(matches) == 1 and not linked and field in ('annotations', 'drafts') and key not in p[field]:
                p[field][key] = copy.deepcopy(a['record'])
    for field in ('annotations', 'drafts'):
        for key, r in list(p.get(field, {}).items()):
            if is_record(r):
                continue
            require(isinstance(r, dict), '旧意见须为对象')
            matches = resolve_ref(view, *record_ref(r))
            if not view['review_workflow'].get('migrate_legacy', False):
                pending.append({'field': field, 'annotation_key': key, 'candidate_decision_ids': matches, 'reason': '未启用旧意见迁移'})
                continue
            a = {'field': field, 'annotation_key': key, 'record': copy.deepcopy(r), 'source_schema': p.get('legacy_schema') or p.get('schema'), 'candidate_decision_ids': matches}
            if a not in archive:
                archive.append(a)
            del p[field][key]
            if len(matches) != 1:
                pending.append({'field': field, 'annotation_key': key, 'candidate_decision_ids': matches, 'reason': '显式事项映射缺失、歧义或旧映射变化，须迁移核对，不按同号或成员重叠合并'})
                continue
            d = ds[matches[0]]
            dest = PREFIX + d['id']
            q = {'annotation_id': dest, 'decision_snapshot': snapshot(d), 'confirmed_result': r.get('confirmed_result', ''), 'reason': r.get('reason', ''), 'history': [], 'migration_state': 'review_required', 'entry_origin': '旧意见显式迁移，未重审', 'updated_at': r.get('updated_at'), 'legacy_sources': [copy.deepcopy(a)], 'exception_of': d.get('exception_of')}
            old = p[field].get(dest)
            if old:
                sources = old.setdefault('legacy_sources', [])
                if a not in sources:
                    sources.append(copy.deepcopy(a))
                # Every distinct saved legacy opinion survives; a current canonical answer is also held for explicit review.
                if field == 'annotations':
                    candidates = old.get('decision_conflict', {}).get('candidates', [copy.deepcopy(old)])
                    candidates.append(q)
                    old['decision_conflict'] = {'candidates': candidates}
                else:
                    old.setdefault('history', []).append(q)
            else:
                p[field][dest] = q
    for a in archive:
        matches = resolve_ref(view, *record_ref(a.get('record') or {}))
        linked = len(matches) == 1 and any(same_origin(a, b) and b.get('candidate_decision_ids') == matches for b in archive)
        if not linked:
            item = {'field': a.get('field'), 'annotation_key': a.get('annotation_key'), 'candidate_decision_ids': matches, 'reason': '显式事项映射缺失、歧义或旧映射变化，须迁移核对，不按同号或成员重叠合并'}
            if item not in pending:
                pending.append(item)
    p['decision_migration_pending'] = pending
    return p


def handoff_actions(view, feedback):
    out = []
    ds = {d['id']: d for d in decisions(view)}
    for key, r in feedback['annotations'].items():
        if not is_record(r) or r.get('applicability_state') != 'current' or r.get('action') in ('待资料确认', '暂不处理'):
            continue
        d = ds[r['decision_snapshot']['id']]
        held = sorted({k for x in ds.values() if x.get('exception_of') == d['id'] for k in x['scope']['member_keys']})
        excluded = sorted(set(d['scope']['excluded_member_keys']) | set(r.get('excluded_member_keys', [])))
        out.append({'annotation_key': key, 'decision_id': d['id'], 'action': r['action'], 'business_option_id': r.get('business_option_id'), 'user_note': r.get('user_note', ''), 'user_result': r['confirmed_result'], 'reason': r['reason'], 'recommendation_receipt': r.get('recommendation_receipt'), 'scope': {'member_keys': [k for k in d['scope']['member_keys'] if k not in excluded and k not in held], 'excluded_member_keys': excluded, 'held_by_exception': held, 'basis': d['scope']['basis']}, 'exception_of': d.get('exception_of'), 'processing': '供所属专业复核并重算；用户方法选择不等于数量核准，未自动改量'})
    return out
