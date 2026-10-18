#!/usr/bin/env python3
"""Extract candidate clauses from hashed neutral CAD TEXT/MTEXT, with applicability evidence."""
import re
from qto_electrical_evidence import bounds, cli, contains, finite, finish, identity_rows, required

SCHEMA = 'qto-design-clause-request/1.0'


def calculate(e, request):
    if request.get('schema') != SCHEMA:
        raise ValueError('request.schema必须为' + SCHEMA)
    regions = identity_rows(request.get('regions'), 'regions')
    start_re = re.compile(request.get('clause_start_pattern', r'^(?:第[一二三四五六七八九十百\d]+条|[（(][一二三四五六七八九十百\d]+[）)]|\d+[、．]|\d+\.(?!\d))'))
    todo, clauses, consumed = [], [], set()
    for rid, region in regions.items():
        box = bounds(region.get('bounds_m'), rid + '.bounds_m')
        pattern = re.compile(required(region.get('text_layers'), rid + '.text_layers'))
        selected = [x for x in e.texts if contains(x['point_m'], box) and pattern.search(str(x['layer']))]
        if not selected:
            todo.append(dict(problem='说明范围未取得文字', region_id=rid))
        columns = region.get('columns_m')
        if columns is None:
            gap = finite(region.get('column_gap_m'), rid + '.column_gap_m', positive=True)
            anchors = sorted({x['point_m'][0] for x in selected if start_re.search(x['text'].strip())})
            starts = []
            for x in anchors:
                if not starts or x - starts[-1] > gap:
                    starts.append(x)
            cuts = [(a + b) / 2 for a, b in zip(starts, starts[1:])]
            edges = [box[0], *cuts, box[2]]
            columns = list(zip(edges, edges[1:]))
        if not isinstance(columns, list) or not columns:
            raise ValueError(rid + '.columns_m须为非空列区间')
        normalized = []
        for col in columns:
            if len(col) != 2 or col[0] >= col[1] or col[0] < box[0] or col[1] > box[2]:
                raise ValueError(rid + '列边界无效')
            if any(max(col[0], old[0]) < min(col[1], old[1]) for old in normalized):
                raise ValueError(rid + '列边界重叠')
            normalized.append(col)
        applicability = region.get('applicability')
        if not isinstance(applicability, dict):
            raise ValueError(rid + '缺明确applicability及其源句柄')
        e.refs(applicability.get('basis_handles'), rid + '.applicability.basis_handles')
        if any(e.byid[s].get('entity_type') not in ('TEXT', 'MTEXT', 'ATTRIB') for s in applicability['basis_handles']):
            raise ValueError('说明适用范围必须可回读到原始文字，不能仅挂几何句柄')
        targets = applicability.get('targets')
        if not isinstance(targets, list) or not targets:
            raise ValueError(rid + '缺适用范围targets；未知用null并列待办，不猜全项目')
        fields = ('building', 'system', 'phase', 'revision')
        for target in targets:
            if not isinstance(target, dict) or any(k not in target for k in fields):
                raise ValueError('每个范围须有building/system/phase/revision，明确全域写*，未知写null')
            if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in target.values()):
                raise ValueError('适用范围值须为非空文本或null')
            if any(target[k] is None for k in fields):
                todo.append(dict(problem='说明适用范围未闭合', region_id=rid, target=target))
        for ci, col in enumerate(normalized):
            current = []
            def emit():
                if not current:
                    return
                ids = [x['source_id'] for x in current]
                cid = rid + ':' + ids[0]
                if consumed.intersection(ids):
                    raise ValueError('说明区域重复消费同一文字，需划清区域: ' + cid)
                consumed.update(ids)
                full = '\n'.join(x['text'] for x in current)
                clauses.append(dict(id=cid, region_id=rid, column=ci, text=full,
                    source_ids=ids, source_texts=current[:],
                    clause_number=start_re.match(full.strip()).group(0) if start_re.match(full.strip()) else None,
                    contains_exception=bool(re.search(r'除|例外|另有|但|仅|二期|预留|取消', full)),
                    applicability=applicability, status='CANDIDATE',
                    scope_source_texts=[dict(source_id=x, text=str(e.byid[x].get('text') or '')) for x in applicability['basis_handles']]))
                if not start_re.match(full.strip()):
                    todo.append(dict(problem='说明续页/前导段缺条号，保留全文不并入相邻条', clause_id=cid))
            for text in sorted((x for x in selected if col[0] <= x['point_m'][0] < col[1]), key=lambda x: (-x['point_m'][1], x['point_m'][0], x['source_id'])):
                if abs(float(text['rotation'])) > 1e-6:
                    todo.append(dict(problem='非水平说明文字未重排', source_id=text['source_id']))
                    continue
                if start_re.match(text['text'].strip()) and current:
                    emit()
                    current = []
                current.append(text)
            emit()
    selected_ids = {x['source_id'] for x in e.texts if any(contains(x['point_m'], bounds(r['bounds_m'], rid)) and re.search(r['text_layers'], str(x['layer'])) for rid, r in regions.items())}
    uncovered = sorted(selected_ids - consumed)
    for sid in uncovered:
        todo.append(dict(problem='说明文字未进入条款', source_id=sid))
    packages = identity_rows(request.get('packages', []), 'packages')
    for pid, package in packages.items():
        if not isinstance(package.get('scope'), dict) or any(not package['scope'].get(k) for k in ('building', 'system', 'phase', 'revision')):
            raise ValueError(pid + '缺本包完整楼栋/系统/分期/版本范围，不能推全域适用')
    executions = request.get('executions', [])
    index = {}
    clause_ids = {x['id'] for x in clauses}
    for row in executions:
        key = (row.get('package_id'), row.get('clause_id'))
        if key in index or key[0] not in packages or key[1] not in clause_ids:
            raise ValueError('条款执行重复或引用不存在的包/条款')
        if row.get('disposition') not in ('applied', 'not_applicable', 'pending'):
            raise ValueError('条款执行disposition非法')
        if row['disposition'] != 'pending':
            required(row.get('basis'), '执行依据')
        index[key] = row
    checks = []
    for clause in clauses:
        matching = []
        for pid, package in packages.items():
            scope = package.get('scope', {})
            targets = clause['applicability']['targets']
            unknown = any(v is None for target in targets for v in target.values())
            applies = any(all(target[k] == '*' or target[k] == scope.get(k) for k in ('building', 'system', 'phase', 'revision')) for target in targets)
            execution = index.get((pid, clause['id']))
            if applies or execution is not None:
                applicability = 'UNRESOLVED' if unknown else 'APPLIES' if applies else 'OUT_OF_SCOPE'
                row = dict(package_id=pid, clause_id=clause['id'], execution=execution,
                           status='MISSING' if execution is None else execution['disposition'],
                           applicability=applicability, package_scope=scope, clause_targets=targets,
                           scope_source_texts=clause['scope_source_texts'])
                checks.append(row)
                if unknown:
                    todo.append(dict(problem='执行声明的条款适用范围未闭合', **row))
                elif not applies:
                    if execution['disposition'] == 'applied':
                        todo.append(dict(problem='范围外条款被执行', **row))
                    elif execution['disposition'] == 'pending':
                        todo.append(dict(problem='范围外执行声明仍待解释', **row))
                else:
                    matching.append(row)
                if applies and not unknown and (execution is None or execution['disposition'] != 'applied'):
                    todo.append(dict(problem='适用条款未一致落实/待解释', **row))
        dispositions = {x['status'] for x in matching}
        if len(dispositions) > 1:
            todo.append(dict(problem='共用条款多包执行差异', clause_id=clause['id'], packages=matching))
    return finish(e, dict(schema='qto-design-clause-candidates/1.0', clauses=clauses,
        coverage=dict(selected_texts=len(selected_ids), consumed_texts=len(consumed), uncovered_source_ids=uncovered),
        package_checks=checks, meaning='条款候选及显式范围对账；不自动裁定规则优先或执行为工程量'), todo)


if __name__ == '__main__':
    raise SystemExit(cli(calculate, __doc__))
