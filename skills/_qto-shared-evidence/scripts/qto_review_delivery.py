"""Optional, evidence-bound delivery contract. No recognition, estimation or approval."""
from __future__ import annotations

import copy
import math

SCHEMA = 'qto-review-delivery/1.0'
ADOPTION_BOUNDARY = '当前采用仅指本轮工作采用，不代表工程正式核准。'
MEMBER_FIELDS = ('current_member_keys', 'source_evidence_member_keys', 'retired_member_keys')


def require(ok, text):
    if not ok:
        raise ValueError('delivery: ' + text)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value, label, nonempty=False):
    require(isinstance(value, list) and all(text(x) for x in value), label + '须为文字列表')
    require(len(value) == len(set(value)) and (bool(value) or not nonempty), label + '为空或重复')
    return set(value)


def scope(value, label, parent=None):
    require(isinstance(value, dict), label + '须显式填写适用和排除范围')
    included = strings(value.get('included'), label + '.included', True)
    excluded = strings(value.get('excluded'), label + '.excluded')
    require(included.isdisjoint(excluded), label + '适用与排除范围冲突')
    if parent is not None:
        require(included <= set(parent['included']) and not included.intersection(parent['excluded']),
                label + '子项适用范围超出父项；不得自动继承父卡全部范围')
    return included


def source_refs(value, sources, label, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty), label + '缺来源证据定位')
    for ref in value:
        require(isinstance(ref, dict) and ref.get('source_id') in sources and text(ref.get('locator')),
                label + '须含当前来源source_id和具体locator')


def evidence(value, sources, label, verified=False):
    require(isinstance(value, dict) and value.get('state') in ('verified', 'pending'), label + '证据状态无效')
    if verified:
        require(value['state'] == 'verified', label + '主量须有已核实证据；可改列参考待核，不得造零')
    require(value.get('method') in ('measured', 'rule'), label + '须声明实测或规则推算')
    source_refs(value.get('source_refs'), sources, label, value['state'] == 'verified')
    facts = value.get('input_facts', [])
    require(isinstance(facts, list), label + '输入事实须为列表')
    if verified and value['method'] == 'rule':
        require(bool(facts), label + '规则已批不等于数量输入已证；须逐项提供input_facts')
    fact_ids = set()
    for fact in facts:
        require(isinstance(fact, dict) and text(fact.get('id')) and fact['id'] not in fact_ids,
                label + '输入事实编号缺失或重复')
        fact_ids.add(fact['id'])
        require(text(fact.get('statement')) and fact.get('state') in ('verified', 'pending'), label + '输入事实无效')
        if verified:
            require(fact['state'] == 'verified', label + '规则输入尚未证实，不能进入主量')
        source_refs(fact.get('source_refs'), sources, label + '.input_facts', fact['state'] == 'verified')


def normalize_delivery(view):
    """Validate opt-in data in place; never change quantities or legacy defaults."""
    if 'delivery' not in view:
        return None
    d = view['delivery']
    require(isinstance(d, dict) and d.get('schema') == SCHEMA, 'schema无效')
    require(d.get('mode') in ('read_only', 'decisions'), 'mode须为read_only或decisions')
    require(d.get('assistant_work_complete') is True,
            '助手仍可继续的识图、匹配、计算或纠错应先完成；只读交付不能代替执行')
    d['adoption_boundary'] = ADOPTION_BOUNDARY
    d.setdefault('adoption_note', ADOPTION_BOUNDARY)
    require(text(d['adoption_note']), '当前采用说明缺失')
    sources = {s['id'] for s in view['sources']}
    keys = {o['catalog_key'] for o in view['objects']}
    membership = [strings(d.get(k), k) for k in MEMBER_FIELDS]
    current, source, retired = membership
    require(not (current & source or current & retired or source & retired), '采用、仅证据与退役成员不得重叠')
    require(set.union(*membership) == keys, '全部对象须且只能分属当前采用、仅证据定位或退役集合')
    rows = d.get('quantity_rows')
    require(isinstance(rows, list), 'quantity_rows须为列表')
    byrow = {}
    for row in rows:
        require(isinstance(row, dict) and text(row.get('id')) and row['id'] not in byrow, '明细编号为空或重复')
        byrow[row['id']] = row
        require(all(text(row.get(k)) for k in ('title', 'unit', 'basis')), '明细须有名称、单位和依据')
        require(row.get('state') in ('current', 'reference', 'historical', 'retired'), '明细状态无效')
        value = row.get('quantity')
        require('quantity' in row, '数量未知须显式写null，不得省略字段或造零')
        require(value is None or (isinstance(value, (float, int)) and not isinstance(value, bool)
                                 and math.isfinite(value) and value >= 0), '数量须为非负有限数字或null')
        scope(row.get('scope'), '明细' + row['id'])
        cov = row.get('coverage')
        require(isinstance(cov, dict) and cov.get('state') in ('complete', 'partial'), '须声明完整/局部覆盖')
        computed = strings(cov.get('computed_scope'), 'computed_scope')
        pending = strings(cov.get('uncomputed_scope'), 'uncomputed_scope')
        require(not computed.intersection(pending) and computed | pending == set(row['scope']['included']),
                '已算与未算范围须互斥并完整覆盖本条适用范围')
        require((cov['state'] == 'complete' and not pending) or (cov['state'] == 'partial' and bool(pending)),
                '完整/局部标志与未算范围不一致')
        members = strings(row.get('member_keys'), 'member_keys')
        locators = strings(row.get('evidence_member_keys'), 'evidence_member_keys')
        require(members <= keys and locators <= keys, '明细稳定成员引用不存在')
        if row['state'] in ('current', 'reference'):
            require(not (members | locators) & retired, '退役成员不得回流当前量或参考定位')
            require(locators <= current | source, '证据定位角色无效')
        if row['state'] == 'current':
            require(value is not None and bool(computed), '当前主量须有已算数值及范围；未知不能改成零')
            require(members <= current, '仅来源证据定位对象不得作为主量成员')
            evidence(row.get('evidence'), sources, row['id'], True)
        elif row['state'] == 'reference':
            require(members <= current | source, '参考成员无效')
            evidence(row.get('evidence'), sources, row['id'])
            ref = row.get('reference')
            require(isinstance(ref, dict) and ref.get('relationship') in ('includes_current', 'excludes_current', 'unknown'),
                    '参考须声明是否包含同范围主量')
            strings(ref.get('prerequisites'), '参考前提', True)
            strings(ref.get('current_row_ids'), '参考关联主量')
            require(ref.get('addition') == 'do_not_add', '参考值不得默认与主量相加')
        else:
            require(text(row.get('history_reason')), '历史/退役明细须说明保留原因')
    adopted = {k for row in rows if row['state'] == 'current' for k in row['member_keys']}
    require(adopted == current, '当前采用成员集合须与主量明细成员一致；不可挂入未采用的旧对象')
    for row in rows:
        if row['state'] != 'reference':
            continue
        ref = row['reference']
        require(all(r in byrow and byrow[r]['state'] == 'current' for r in ref['current_row_ids']), '参考关联主量不存在')
        if ref['relationship'] == 'includes_current':
            require(bool(ref['current_row_ids']), '包含主量的参考须列明主量编号')
        for rid in ref['current_row_ids']:
            main = byrow[rid]
            require(main['unit'] == row['unit'], '不同单位的参考与主量不能建立数值包含关系')
            if ref['relationship'] == 'includes_current':
                require(set(main['scope']['included']) <= set(row['scope']['included']), '参考范围未包含所指主量范围')
                require(row['quantity'] is None or row['quantity'] >= main['quantity'], '参考包含主量却小于主量')
    groups = d.get('missing_groups')
    require(isinstance(groups, list), 'missing_groups须为列表')
    ids = set()

    def missing(node, parent=None):
        require(isinstance(node, dict) and text(node.get('id')) and node['id'] not in ids, '缺件编号缺失或重复')
        ids.add(node['id'])
        require(text(node.get('title')), '缺件共性/子项标题缺失')
        scope(node.get('scope'), '缺件' + node['id'], parent)
        facts = node.get('verified_facts')
        require(isinstance(facts, list), '已核事实须显式列出；无事实用空列表')
        for fact in facts:
            require(isinstance(fact, dict) and text(fact.get('statement')), '已核事实无效')
            source_refs(fact.get('source_refs'), sources, '已核事实')
        needs = node.get('minimum_required')
        children = node.get('children', [])
        require(isinstance(needs, list) and isinstance(children, list) and (needs or children), '须提供最少具体缺件或独立子项')
        need_ids = set()
        for need in needs:
            require(isinstance(need, dict) and all(text(need.get(k)) for k in
                    ('id', 'description', 'provider', 'impact', 'recompute_action')), '缺件须有编号、具体内容、提供方、影响与助手回算动作')
            require(need['id'] not in need_ids, '缺件编号重复')
            need_ids.add(need['id'])
            scope(need.get('scope'), '最少缺件' + need['id'], node['scope'])
        for child in children:
            missing(child, node['scope'])
    for group in groups:
        missing(group)
    targets = d.get('decision_targets')
    require(isinstance(targets, list), 'decision_targets须显式列出；没有真实取舍用空列表')
    require(bool(targets) == (d['mode'] == 'decisions'), '无真实业务取舍须用只读模式；不能自动生成审核表单')
    c = view.get('comparison') or {}
    seen = set()
    for target in targets:
        require(isinstance(target, dict) and target.get('kind') in ('issue', 'common')
                and text(target.get('id')) and text(target.get('reason')), '真实取舍须关联既有问题/共性审核并说明取舍内容')
        key = target['kind'], target['id']
        require(key not in seen, '真实取舍重复')
        seen.add(key)
        pool = c.get('issues' if target['kind'] == 'issue' else 'common_issues', [])
        obj = next((x for x in pool if x['id'] == target['id']), None)
        require(obj is not None and obj.get('handler') == 'decide', '真实取舍须绑定现有handler=decide的业务审核对象')
    for target in targets:
        if target['kind'] != 'issue':
            continue
        issue = next(x for x in c.get('issues', []) if x['id'] == target['id'])
        common = next((x for x in c.get('common_issues', []) if x['id'] == issue.get('common_issue_id')), None)
        require(common is None or common.get('handler') == 'closed' or ('common', common['id']) in seen,
                '单项取舍依赖尚未答复的共性问题，须显式声明该共性取舍后再开放单项；不得形成无法保存的表单')
    d['generation_check'] = {'current_rows': sum(r['state'] == 'current' for r in rows),
                             'reference_rows': sum(r['state'] == 'reference' for r in rows),
                             'missing_groups': len(groups), 'business_choices': len(targets),
                             'current_is_formal_approval': False, 'automatic_quantity_addition': False}
    return d


def delivery_allows_target(view, kind, ident):
    d = view.get('delivery')
    if d is None:
        return True
    for target in d['decision_targets']:
        if target['kind'] == kind and target['id'] == ident:
            return True
        if target['kind'] == 'common' and kind in ('issue', 'item'):
            common = next((g for g in (view.get('comparison') or {}).get('common_issues', []) if g['id'] == target['id']), {})
            if ident in common.get('scope', {}).get('issue_ids' if kind == 'issue' else 'item_ids', []):
                return True
    return False


def delivery_record_state(record, view):
    """Delivery is read-only except explicitly declared real business choices."""
    d = view.get('delivery')
    if d is None:
        return None
    snaps = record.get('member_snapshot', []) if record.get('group_id') else [record.get('object_snapshot') or {}]
    retired = set(d['retired_member_keys'])
    if any(s.get('catalog_key') in retired for s in snaps):
        return 'unmatched', '退役对象意见仅保留历史，不得回流当前图面或采用量'
    for kind, field, ident in (('issue', 'issue_snapshot', 'issue_id'), ('common', 'common_issue_snapshot', 'common_issue_id'), ('item', 'item_snapshot', 'item_id')):
        if record.get(field) is not None and delivery_allows_target(view, kind, record[field].get(ident)):
            return None
    return 'stale', '当前交付无对应真实业务取舍；意见保留为历史，不下发审核或改量动作'


def delivery_snapshot(view):
    if 'delivery' not in view:
        return None
    return {'result_fingerprint': view['result_fingerprint'], 'delivery': copy.deepcopy(view['delivery'])}


def bind_delivery_feedback(package, view):
    """Incoming snapshots are history, never a source for current adoption."""
    if 'delivery' not in view:
        return package
    snapshot = delivery_snapshot(view)
    old = package.get('delivery_snapshot')
    history = package.setdefault('historical_delivery_snapshots', [])
    require(isinstance(history, list), '历史交付快照须为列表')
    if old is not None and old != snapshot and old not in history:
        history.append(copy.deepcopy(old))
    package['delivery_snapshot'] = snapshot
    return package
