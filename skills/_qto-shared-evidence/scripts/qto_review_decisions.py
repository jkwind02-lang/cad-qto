"""Cross-entry decision identity: explicit IDs unify intent; overlap only raises a review hold."""
from itertools import combinations


def decision_conflicts(package, view):
    c = view.get('comparison') or {}
    objects = {x['catalog_key']: x for x in view['objects']}
    groups = {x['id']: x for x in view['groups']}
    items = {x['id']: x for x in c.get('items', [])}
    issues = {x['id']: x for x in c.get('issues', [])}
    commons = {x['id']: x for x in c.get('common_issues', [])}
    def item_keys(ids):
        return {k for iid in ids for r in items.get(iid, {}).get('detail_rows', []) for k in r.get('member_keys', [])}
    def target(r):
        if r.get('common_issue_snapshot') is not None:
            snap = r['common_issue_snapshot']; t = commons.get(snap['common_issue_id'], {})
            sc = snap.get('scope') or {}; ids = set(sc.get('item_ids', []))
            ids.update(i for q in sc.get('issue_ids', []) for i in issues.get(q, {}).get('item_ids', []))
            return 'audit', t.get('decision_id'), item_keys(ids)
        if r.get('issue_snapshot') is not None:
            t = issues.get(r['issue_snapshot']['issue_id'], {})
            return 'audit', t.get('decision_id'), item_keys(t.get('item_ids', []))
        if r.get('item_snapshot') is not None:
            iid = r['item_snapshot']['item_id']; t = items.get(iid, {})
            return 'audit', t.get('decision_id'), item_keys([iid])
        if r.get('group_id'):
            t = groups.get(r['group_id'], {})
            return 'drawing', t.get('decision_id'), set(r.get('selected_object_keys', []))
        key = (r.get('object_snapshot') or {}).get('catalog_key'); t = objects.get(key, {})
        return 'drawing', t.get('decision_id'), {key} if key else set()
    def opinion(r):
        return r.get('verdict') or {'确认现结果': '同意', '修改结果': '要改', '排除对象': '要改', '暂缓计算': '要改'}.get(r.get('instruction'))
    live = [(k, r, target(r)) for k, r in package.get('annotations', {}).items()
            if r.get('applicability_state') == 'current' and not r.get('merge_conflict')]
    found = []
    for (ka, a, ta), (kb, b, tb) in combinations(live, 2):
        if ta[0] == tb[0]: continue
        explicit = bool(ta[1] and tb[1])
        if explicit and ta[1] != tb[1]: continue
        overlap = ta[2] & tb[2]
        if not (explicit or overlap): continue
        if {opinion(a), opinion(b)} != {'同意', '要改'}: continue
        found.append({'annotation_keys': [ka, kb], 'decision_id': ta[1] if explicit else None,
                      'kind': 'decision_conflict' if explicit else 'decision_identity_required',
                      'overlap_member_keys': sorted(overlap),
                      'processing': '跨入口意见相反，动作暂挂；由所属专业核对是否同一决策事项，不按同号或重叠自动合并'})
    return found


def hold_decision_conflicts(package, view, unmatched):
    found = decision_conflicts(package, view)
    package['decision_conflicts'] = found
    seen = {x['annotation_key'] for x in unmatched}
    for conflict in found:
        for key in conflict['annotation_keys']:
            record = package['annotations'][key]
            record['applicability_state'] = 'conflict'
            record['applicability_reason'] = conflict['processing']
            if key not in seen:
                unmatched.append({'annotation_key': key, 'state': 'conflict', 'reason': conflict['processing']})
                seen.add(key)
    return package, unmatched
