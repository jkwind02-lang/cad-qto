"""Delivery statistics and independent, explicitly based coverage checks.

Counts overlap. No function in this module adds engineering quantities across
items, units, references or historical records. A numerical agreement is never
used as evidence of coverage.
"""
from collections import Counter, defaultdict

SLICE_TITLES = {
    'all': '全部', 'current_unknown_original_known': '原量有值、本次未形成量',
    'positive': '可比正差', 'negative': '可比负差', 'consistent': '可比一致或容差内',
    'comparable': '正式资格可比', 'zero': '可比差额为零',
    'not_comparable': '不可比或资格未证', 'extra': '清单外有据量',
    'has_reference': '含独立参考', 'complete': '当前范围完整',
    'partial': '当前范围局部', 'coverage_unknown': '当前覆盖未形成',
}


def build_comparison_statistics(view, delivery):
    comparison = view.get('comparison') or {}
    comp = {i['id']: i for i in comparison.get('items', [])}
    records = []
    for item in delivery['items']:
        original = item['original_quantity']
        current = item['quantity']
        source = comp.get(item.get('comparison_item_id', item['id']))
        segments = []
        for seg in (source or {}).get('third_parties', []):
            state = seg.get('comparison_state', 'unknown')
            reason = {
                'original_unknown': '原量未填，不能按零作差', 'current_unknown': '本次未形成有据量，不能与原量作差',
                'unit_mismatch': '单位不同', 'unknown': '对象、范围、规格或完整性资格未证',
            }.get(state) or (seg.get('comparability') or {}).get('reason') or ('同对象、同范围、同规格、同单位且覆盖完整' if state == 'comparable' else '不可比')
            eligible = state == 'comparable'
            diff = seg.get('difference') or {}
            segments.append({'source_id': seg['source_id'], 'unit': seg['unit'], 'state': state,
                             'reason': reason, 'delta': diff.get('value') if eligible else None,
                             'rate': diff.get('rate') if eligible else None,
                             'within_tolerance': eligible and seg.get('conclusion') == '一致'})
        if len(segments) == 1:
            s = segments[0]
            state, reason, delta, rate = s['state'], s['reason'], s['delta'], s['rate']
        elif segments:
            state, reason, delta, rate = 'multiple_sources', '多来源分别比较，不拼成一个差额', None, None
        else:
            present = item.get('original_row_present')
            if present is True:
                state = 'original_unknown' if original is None else 'unknown'
                reason = '原清单行存在但原量未填，不能按零作差' if original is None else '原行及原量已给，尚无正式比较资格数据'
            elif present is False or (source or {}).get('kind') == 'extra':
                state, reason = 'no_original_row', '无原清单对应行'
            else:
                state, reason = 'unknown', '原行存在性未声明，不能从原量未知推定无原行'
            delta, rate = None, None
        tags = ['all']
        if current is None and original is not None:
            tags.append('current_unknown_original_known')
        eligible = [s for s in segments if s['state'] == 'comparable']
        if eligible: tags.append('comparable')
        if any(s['delta'] is not None and s['delta'] > 0 for s in eligible): tags.append('positive')
        if any(s['delta'] is not None and s['delta'] < 0 for s in eligible): tags.append('negative')
        if any(s['delta'] == 0 for s in eligible): tags.append('zero')
        if any(s['delta'] == 0 or s['within_tolerance'] for s in eligible): tags.append('consistent')
        if not segments or any(s['state'] != 'comparable' for s in segments): tags.append('not_comparable')
        if current is not None and ((source or {}).get('kind') == 'extra' or item.get('original_row_present') is False): tags.append('extra')
        if item['reference_row_ids']: tags.append('has_reference')
        cov = item['coverage']['state']
        tags.append(cov if cov in ('complete', 'partial') else 'coverage_unknown')
        records.append({'item_id': item['id'], 'unit': item['unit'], 'original_unit': item.get('original_unit', item['unit']), 'current_quantity': current,
                        'original_quantity': original, 'coverage_state': cov, 'comparison_state': state,
                        'reason': reason, 'delta': delta, 'rate': rate, 'source_segments': segments, 'tags': tags})
    counts = {tag: sum(tag in r['tags'] for r in records) for tag in SLICE_TITLES}
    units = sorted({r['unit'] for r in records})
    return {'schema': 'qto-review-statistics/1.0', 'total_items': len(records), 'overlapping_slices': True,
            'comparable_items': sum(any(s['state'] == 'comparable' for s in r['source_segments']) for r in records),
            'comparable_segments': sum(s['state'] == 'comparable' for r in records for s in r['source_segments']),
            'note': '筛选分类允许交叉，不可相加；数量不跨项或跨单位求总；容差内不证明没有遗漏。',
            'items': records, 'counts': counts,
            'by_unit': [{'unit': u, 'total_items': sum(r['unit'] == u for r in records),
                         'counts': {tag: sum(r['unit'] == u and tag in r['tags'] for r in records) for tag in SLICE_TITLES}} for u in units],
            'slices': [{'id': tag, 'title': title, 'item_ids': [r['item_id'] for r in records if tag in r['tags']], 'count': counts[tag]}
                       for tag, title in SLICE_TITLES.items()]}


def build_coverage_check(delivery):
    """Inspect expected object -> adopted detail relations, independent of deltas.

    Optional coverage_baseline requires explicit source_refs and expected
    objects. row_ids identifies expected current details; member_keys identifies
    physical objects. Neither a blank list row nor an original quantity creates
    an expected drawing object. Aggregate/split relations need a written basis.
    """
    baseline = delivery.get('coverage_baseline')
    rows = {r['id']: r for r in delivery['quantity_rows'] if r['state'] == 'current'}
    items = delivery['items']
    findings = []
    byitem = defaultdict(list)
    known_items = {i['id'] for i in items}
    item_rows = {i['id']: set(i['current_row_ids']) for i in items}
    row_owners = defaultdict(list)
    for iid, owned in item_rows.items():
        for rid in owned: row_owners[rid].append(iid)
    usage = defaultdict(list)

    def finding(code, reason, entry=None, row_ids=(), status='uncovered'):
        entry = entry or {}
        ids = entry.get('item_ids', [])
        f = {'id': 'COV-' + str(len(findings) + 1), 'code': code, 'status': status,
             'item_ids': ids, 'row_ids': sorted(row_ids), 'object_ids': [entry['id']] if entry.get('id') else [],
             'scope': entry.get('scope', []), 'reason': reason}
        if len(ids) == 1: f['item_id'] = ids[0]
        findings.append(f)
        for iid in ids: byitem[iid].append(f)

    explicit = isinstance(baseline, dict) and baseline.get('state') == 'explicit'
    if explicit:
        if not baseline.get('source_refs') or not isinstance(baseline.get('objects'), list):
            raise ValueError('delivery: coverage_baseline须有明确来源和完整objects列表；无基准请写unavailable')
        seen = set()
        for entry in baseline['objects']:
            if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id'] or entry['id'] in seen:
                raise ValueError('delivery: 覆盖基准对象编号缺失或重复')
            seen.add(entry['id'])
            for name in ('item_ids', 'member_keys', 'row_ids', 'scope'):
                val = entry.get(name)
                if not isinstance(val, list) or not all(isinstance(x, str) and x for x in val) or len(val) != len(set(val)):
                    raise ValueError('delivery: 覆盖基准' + name + '须为不重复文字列表')
            if not entry['scope'] or not set(entry['item_ids']) <= known_items or not (entry['member_keys'] or entry['row_ids']):
                raise ValueError('delivery: 覆盖基准须明确范围、有效条目及预期对象或明细关系')
            rel = entry.get('relationship', 'one_to_one')
            if rel not in ('one_to_one', 'aggregate', 'split') or (rel != 'one_to_one' and not entry.get('basis')):
                raise ValueError('delivery: 多对一或拆分覆盖关系须声明relationship及具体basis')
            if entry.get('excluded'):
                if not entry.get('basis') or not entry.get('source_refs'):
                    raise ValueError('delivery: 基准排除须有明确依据及来源，不得凭数量差额排除')
                finding('explicit_exclusion', entry['basis'], entry, status='accepted')
                continue
            matches = set(entry['row_ids']) & set(rows)
            for rid, row in rows.items():
                if set(entry['member_keys']) & set(row['member_keys']): matches.add(rid)
            allowed = {rid for iid in entry['item_ids'] for rid in item_rows[iid]}
            wrong = matches - allowed
            if wrong:
                actual = sorted({iid for rid in wrong for iid in row_owners[rid]})
                finding('wrong_item_ownership', '基准声明条目' + '、'.join(entry['item_ids']) + '，实际承接行属于' + '、'.join(actual)
                        + '；跨条目关联不得作为本条目覆盖', entry, sorted(wrong), 'invalid')
                findings[-1]['actual_item_ids'] = actual
            if not entry['item_ids']:
                finding('item_unassigned', '明确基准对象尚未指定所属条目，不以其他条目有同位置明细代替承接', entry, sorted(matches))
            matches &= allowed
            if entry['row_ids'] and not set(entry['row_ids']) <= matches:
                finding('missing_detail', '基准预期的当前明细未由所属条目采用集合承接', entry, sorted(set(entry['row_ids']) - matches))
            covered_members = {k for rid in matches for k in rows[rid]['member_keys']}
            if set(entry['member_keys']) - covered_members:
                finding('uncovered_object', '明确基准中的对象未由当前采用明细覆盖', entry, matches)
            if not matches:
                finding('no_evidence', '预期对象或明细无当前有据量承接，不能由原量或差额补证', entry)
            if len(matches) > 1 and rel != 'split':
                finding('one_to_many', '同一预期对象进入多条当前明细，缺少已声明拆分依据', entry, matches, 'invalid')
            for rid in sorted(matches):
                usage[rid].append(entry)
                e = rows[rid].get('evidence') or {}
                if e.get('state') != 'verified' or not e.get('source_refs'):
                    finding('no_evidence', '当前明细缺少有效来源证据', entry, [rid], 'invalid')
            computed_scope = {s for rid in matches for s in rows[rid]['coverage']['computed_scope']}
            if set(entry['scope']) - computed_scope:
                finding('scope_uncovered', '明确基准的适用范围未全部进入当前明细已算范围', entry, matches)
        for rid, entries in usage.items():
            if len(entries) > 1 and any(e.get('relationship', 'one_to_one') != 'aggregate' for e in entries):
                ids = sorted({i for e in entries for i in e['item_ids']})
                finding('many_to_one', '多个基准对象共用一条明细，须明确聚合范围与依据',
                        {'item_ids': ids, 'scope': sorted({s for e in entries for s in e['scope']})}, [rid], 'invalid')
    else:
        if baseline is not None and (not isinstance(baseline, dict) or baseline.get('state') != 'unavailable' or not baseline.get('reason')):
            raise ValueError('delivery: 无覆盖基准时须写state=unavailable和reason')
    bad = any(f['status'] in ('uncovered', 'invalid') for f in findings)
    results = []
    for item in items:
        linked = [e for e in (baseline or {}).get('objects', []) if item['id'] in e.get('item_ids', [])] if explicit else []
        fs = byitem[item['id']]
        pending = item['coverage']['uncomputed_scope']
        state = 'unknown' if not explicit or not linked else 'attention' if pending or any(f['status'] in ('uncovered', 'invalid') for f in fs) else 'pass'
        expected = {s for e in linked if not e.get('excluded') for s in e['scope']}
        actual = {s for rid in item['current_row_ids'] for s in rows[rid]['coverage']['computed_scope']}
        results.append({'item_id': item['id'], 'status': state,
                        'expected_scope': sorted(expected), 'excluded_scope': sorted({s for e in linked if e.get('excluded') for s in e['scope']}),
                        'covered_scope': item['coverage']['computed_scope'], 'missing_scope': sorted(set(pending) | (expected - actual)),
                        'row_ids': item['current_row_ids'], 'findings': fs})
    status = 'unknown' if not explicit else 'attention' if bad or any(r['status'] == 'attention' for r in results) else 'unknown' if any(r['status'] == 'unknown' for r in results) else 'pass'
    return {'schema': 'qto-review-coverage/1.0', 'status': status,
            'baseline_state': 'explicit' if explicit else 'unavailable',
            'reason': (baseline or {}).get('reason') or ('依明确对象、范围与明细关系核查；数值差额不参与判定' if explicit else '无明确原图可算对象覆盖基准，尚不能判断是否遗漏'),
            'items': results, 'findings': findings, 'counts': dict(Counter(f['code'] for f in findings))}
