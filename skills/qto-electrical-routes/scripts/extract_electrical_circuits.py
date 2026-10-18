#!/usr/bin/env python3
"""Candidate system-diagram circuit tables from neutral CAD text and table lines."""
import math
import re
from collections import defaultdict
from qto_electrical_evidence import bound_result, bounds, cli, contains, finite, finish, identity_rows, required

SCHEMA = 'qto-electrical-circuit-request/1.0'
DEFAULT_ROWS = dict(spec=r'电缆|导线|型号规格|线缆规格', destination=r'设备名称|用电设备|去向',
                    upstream=r'^引自$|上级', state=r'状态|备注', count=r'根数|并行根数', method=r'敷设方式',
                    control_code=r'控制编号|控制回路', control_spec=r'控制电缆|控制电缆规格', control_count=r'控制根数')


def calculate(e, request):
    if request.get('schema') != SCHEMA:
        raise ValueError('request.schema必须为' + SCHEMA)
    area = bounds(request.get('bounds_m'), 'bounds_m')
    text_layer = re.compile(required(request.get('text_layers'), 'text_layers'))
    texts = [x for x in e.texts if contains(x['point_m'], area) and text_layer.search(str(x['layer']))]
    row_tol = finite(request.get('row_tolerance_m'), 'row_tolerance_m', positive=True)
    ext = request.get('table_extent_m', {})
    left, right, up, down = [finite(ext.get(k), 'table_extent_m.' + k, positive=True) for k in ('left', 'right', 'up', 'down')]
    head_re = re.compile(request.get('header_pattern', r'^回路编号$'))
    code_re = re.compile(required(request.get('circuit_pattern'), 'circuit_pattern'))
    spec_re = re.compile(required(request.get('spec_pattern'), 'spec_pattern'))
    title_re = re.compile(required(request.get('box_title_pattern'), 'box_title_pattern'))
    row_rules = request.get('row_patterns', DEFAULT_ROWS)
    if any(k not in ('spec', 'destination', 'upstream', 'state', 'count', 'method', 'control_code', 'control_spec', 'control_count') for k in row_rules):
        raise ValueError('未知row_patterns字段')
    headers = sorted([x for x in texts if head_re.search(x['text'].strip())], key=lambda x: (-x['point_m'][1], x['point_m'][0]))
    lines = e.lines(required(request.get('table_line_layers'), 'table_line_layers'))
    leader_chains=e.leader_chains(request.get('leader_layers',r'(?!)'),finite(request.get('leader_snap_tolerance_m',0.0),'leader_snap_tolerance_m'))
    leader_links = {}
    if leader_chains:
        leader_text_tol = finite(request.get('leader_text_tolerance_m'), 'leader_text_tolerance_m', positive=True)
        for t in texts:
            links = [(chain,chain['target']) for chain in leader_chains if math.dist(chain['text_end'][:2],t['point_m'])<=leader_text_tol]
            if links: leader_links[t['source_id']] = links
    todo, tables, ledger, assigned = [], [], [], set()
    applicable = []
    if request.get('design_clauses'):
        design = e.bind(request['design_clauses'], 'qto-design-clause-candidates/1.0')
        scope = request.get('effective_scope')
        if not isinstance(scope, dict) or any(k not in scope for k in ('building', 'system', 'phase', 'revision')):
            raise ValueError('消费说明条款须明确effective_scope的楼栋/系统/分期/版本')
        e.refs(request.get('scope_basis_source_ids'), 'scope_basis_source_ids')
        for clause in design['clauses']:
            targets = clause['applicability']['targets']
            if any(v is None for t in targets for v in t.values()):
                todo.append(dict(problem='上游说明适用范围未闭合', clause_id=clause['id']))
            elif any(all(t[k] == '*' or t[k] == scope[k] for k in scope) for t in targets):
                applicable.append(clause['id'])
    else:
        todo.append(dict(problem='未绑定设计说明候选，回路规则适用性未闭合'))
    global_cells = {}
    for h in headers:
        hx, hy = h['point_m']
        next_x = min([other['point_m'][0] for other in headers if abs(other['point_m'][1] - hy) <= row_tol and other['point_m'][0] > hx] or [hx + right])
        box = [max(area[0], hx - left), max(area[1], hy - down), min(area[2], hx + right, next_x), min(area[3], hy + up)]
        table_text = [x for x in texts if contains(x['point_m'], box)]
        box_notes = [t for t in table_text if t['point_m'][1] > hy + row_tol and re.search(r'仅预留总电量|待后期|设备自带控制柜|取消|预留二期|二期', t['text'])]
        tid = 'TABLE-' + h['source_id']
        titles = [x for x in table_text if hy < x['point_m'][1] < box[3] and title_re.fullmatch(x['text'].strip())]
        if len(titles) == 1:
            title = titles[0]['text'].strip()
            title_sources = [titles[0]['source_id']]
        else:
            title, title_sources = None, [x['source_id'] for x in titles]
            todo.append(dict(problem='箱标题缺失/多候选', table_id=tid, source_ids=title_sources))
        headings = {}
        for field, pattern in row_rules.items():
            headings[field] = [x for x in table_text if re.fullmatch(pattern, x['text'].strip())]
            if len(headings[field]) > 1:
                todo.append(dict(problem='表格行标签多候选', table_id=tid, field=field, source_ids=[x['source_id'] for x in headings[field]]))
        columns = sorted([x for x in table_text if abs(x['point_m'][1] - hy) <= row_tol and x['source_id'] != h['source_id'] and
                          (code_re.fullmatch(x['text'].strip()) or re.fullmatch(r'[~～…]+', x['text'].strip()))], key=lambda x: x['point_m'][0])
        vertical = sorted({v['start'][0] for v in lines if abs(v['start'][0] - v['end'][0]) <= row_tol and
                           min(v['start'][1], v['end'][1]) - row_tol <= hy <= max(v['start'][1], v['end'][1]) + row_tol and
                           box[0] <= v['start'][0] <= box[2]})
        table_rows = []
        for ci, column in enumerate(columns):
            code, x = column['text'].strip(), column['point_m'][0]
            if re.fullmatch(r'[~～…]+', code):
                todo.append(dict(problem='省略列不自动展开', table_id=tid, source_id=column['source_id'], text=code))
                continue
            xmin = max([v for v in vertical if v <= x] or [((columns[ci - 1]['point_m'][0] + x) / 2) if ci else hx + row_tol])
            xmax = min([v for v in vertical if v > x] or [((columns[ci + 1]['point_m'][0] + x) / 2) if ci + 1 < len(columns) else box[2]])
            cid = tid + ':' + column['source_id']
            fields, field_sources, field_bindings, problems = {}, {}, {}, []
            for field, labels in headings.items():
                if len(labels) != 1:
                    fields[field], field_sources[field] = None, []
                    continue
                y = labels[0]['point_m'][1]
                def belongs(t):
                    links = leader_links.get(t['source_id'])
                    if links:
                        return len(links) == 1 and xmin <= links[0][1][0] < xmax and abs(links[0][1][1] - y) <= row_tol
                    return xmin <= t['point_m'][0] < xmax and abs(t['point_m'][1] - y) <= row_tol
                candidates = [t for t in table_text if belongs(t) and t['source_id'] not in {l['source_id'] for values in headings.values() for l in values}]
                # Multiple text fragments remain visible; never select a nearby specification across column boundaries.
                candidates.sort(key=lambda t: t['point_m'][0])
                ids = [t['source_id'] for t in candidates]
                for sid in ids:
                    owner = global_cells.get(sid)
                    if owner is not None and owner != cid:
                        problems.append('源文字被多回路列认领')
                        todo.append(dict(problem='列边界/并列表格认领冲突', source_id=sid, circuit_ids=[owner, cid]))
                    global_cells[sid] = cid
                value = '\n'.join(t['text'] for t in candidates) or None
                fields[field], field_sources[field] = value, ids
                field_bindings[field] = [dict(text_source_id=sid, via=leader_links[sid][0][0]['via'] if sid in leader_links else 'table_cell_candidate',
                    leader_source_ids=sorted({s for x in leader_links.get(sid, [])for s in x[0]['source_ids']})) for sid in ids]
                if field == 'spec' and (not value or len([t for t in candidates if spec_re.search(t['text'])]) != 1):
                    problems.append('规格缺失/多候选')
            raw_state = '\n'.join([*(v for v in fields.values() if isinstance(v, str)), *(t['text'] for t in box_notes)])
            flags = [name for name, pattern in [('cancelled', '取消'), ('phase2', '二期'), ('reserved', '预留'), ('spare', '备用')] if re.search(pattern, raw_state)]
            state = flags or ['normal_candidate']
            dest = fields.get('destination')
            if dest and re.fullmatch(r'(?:预留|备用|二期|预留二期|取消)[\s\d]*', dest):
                dest = None
                problems.append('预留/备用无实体终点')
            count_text = fields.get('count')
            count = int(count_text) if isinstance(count_text, str) and re.fullmatch(r'[1-9]\d*', count_text) else None
            if count is None:
                problems.append('并行根数未明确；不默认为1')
            source_ids = sorted({column['source_id'], h['source_id'], *title_sources, *(s for ids in field_sources.values() for s in ids)})
            assigned.update(s for ids in field_sources.values() for s in ids)
            row = dict(id=cid, table_id=tid, source_box=title, circuit_code=code, fields=fields,
                       field_sources=field_sources, field_bindings=field_bindings, source_ids=source_ids, column_bounds_m=[xmin, xmax],
                       field_binding_status='table_cell_candidate_requires_review',
                       state=state, endpoint_label=dest, parallel_count=count,
                       endpoint_candidates=[], chosen_endpoint=None, upstream_conflicts=[], problems=problems,
                       applicable_clause_ids=applicable,
                       status='CANDIDATE', official_adoption=False)
            ledger.append(row)
            table_rows.append(cid)
            for problem in problems:
                todo.append(dict(problem=problem, circuit_id=cid))
        tables.append(dict(id=tid, header_source_id=h['source_id'], box=title, title_source_ids=title_sources,
                           box_notes=box_notes,
                           bounds_m=box, circuit_ids=table_rows, table_line_source_ids=sorted({x['source_id'] for x in lines if contains(x['start'], box) or contains(x['end'], box)})))
        if not table_rows:
            todo.append(dict(problem='找到表头但未解析回路列', table_id=tid))
    endpoints = identity_rows(request.get('endpoint_candidates', []), 'endpoint_candidates')
    for pid, endpoint in endpoints.items():
        sid = required(endpoint.get('physical_source_id'), pid + '.physical_source_id')
        e.refs([sid], pid)
        if e.byid[sid].get('entity_type') != 'INSERT':
            raise ValueError('终点物理来源须为本图INSERT，不把文字当设备')
        label = required(endpoint.get('label'), pid + '.label')
        refs = e.refs(endpoint.get('label_source_ids'), pid + '.label_source_ids')
        if not any(label == str(e.byid[s].get('text') or '').strip() for s in refs):
            raise ValueError('终点标签须可回读到本图文字；名称变更需显式专业对账')
        required(endpoint.get('scope_id'), pid + '.scope_id')
        required(endpoint.get('binding_basis'), pid + '.binding_basis')
    endpoint_references=defaultdict(list)
    selected_endpoints=set()
    def match_endpoints(row,label,role,field_source_ids):
        eligible=[];diagnostics=[]
        if not label:return eligible,diagnostics
        for pid,p in endpoints.items():
            if p['label']!=label:continue
            reasons=[]
            if request.get('scope_id')is None:reasons.append('circuit_scope_missing')
            elif p['scope_id']!=request['scope_id']:reasons.append('scope_mismatch')
            if p.get('source_box')is not None and p['source_box']!=row['source_box']:reasons.append('source_box_mismatch')
            detail=dict(endpoint_id=pid,role=role,label=label,circuit_id=row['id'],
                circuit_scope_id=request.get('scope_id'),circuit_source_box=row['source_box'],
                circuit_source_ids=sorted(set(row['source_ids']+field_source_ids)),
                endpoint_scope_id=p['scope_id'],endpoint_source_box=p.get('source_box'),
                endpoint_physical_source_id=p['physical_source_id'],endpoint_physical_id=p.get('physical_id',pid),
                endpoint_label_source_ids=p['label_source_ids'],endpoint_binding_basis=p['binding_basis'],
                eligibility='excluded'if reasons else'candidate',exclusion_reasons=reasons,
                circuit_state=row['state'],selected=False)
            diagnostics.append(detail);endpoint_references[pid].append(detail)
            if reasons:
                todo.append(dict(problem='终点同号候选被范围/供电箱排除',**detail))
            else:eligible.append(pid)
        return eligible,diagnostics
    bindings = identity_rows(request.get('confirmed_bindings', []), 'confirmed_bindings', key='circuit_id')
    feeder_rows = request.get('feeder_rows', [])
    circuit_ids = {r['id'] for r in ledger}
    if any(cid not in circuit_ids for cid in bindings):
        raise ValueError('绑定引用不存在的回路')
    for row in ledger:
        names = row['endpoint_label']
        candidates,diagnostics=match_endpoints(row,names,'destination',row['field_sources'].get('destination',[]))
        row['endpoint_candidates'] = candidates
        row['endpoint_candidate_checks']=diagnostics
        binding = bindings.get(row['id'])
        if binding and binding.get('endpoint_id') is not None:
            pid = binding.get('endpoint_id')
            if pid not in candidates:
                raise ValueError('确认绑定不属于同范围同标签物理候选')
            e.refs(binding.get('basis_source_ids'), 'confirmed_binding.basis_source_ids')
            if row['state'] != ['normal_candidate']:
                todo.append(dict(problem='取消/分期/预留回路不得自动定位为正常设备', circuit_id=row['id']))
            else:
                row['chosen_endpoint'] = pid
                selected_endpoints.add(pid)
                for detail in diagnostics:
                    if detail['endpoint_id']==pid:detail['selected']=True
        elif len(candidates) > 1:
            todo.append(dict(problem='同名多物理终点，未自动选第一处', circuit_id=row['id'], candidates=candidates))
        elif names:
            todo.append(dict(problem='终点关系待确认' if candidates else '平面未对到终点', circuit_id=row['id'], candidates=candidates))
        control_pattern = re.compile(request.get('control_code_pattern', r'n\d+a'))
        control_codes = sorted(set(control_pattern.findall(row['fields'].get('control_code') or '')))
        if any(not isinstance(code,str)for code in control_codes):
            raise ValueError('control_code_pattern不得用多组findall制造非文本代码')
        row['control_links'] = []
        for code in control_codes:
            candidates,diagnostics=match_endpoints(row,code,'control:'+code,row['field_sources'].get('control_code',[]))
            count_raw=row['fields'].get('control_count')
            count=int(count_raw)if isinstance(count_raw,str)and re.fullmatch(r'[1-9]\d*',count_raw)else None
            link=dict(control_code=code,parent_circuit_id=row['id'],source_box=row['source_box'],
                      endpoint_candidates=candidates,chosen_endpoint=None,spec=row['fields'].get('control_spec'),parallel_count=count,
                      endpoint_candidate_checks=diagnostics,
                      source_ids=sorted(set(row['field_sources'].get('control_code',[])+row['field_sources'].get('control_spec',[])+row['field_sources'].get('control_count',[]))))
            requested=(binding.get('control_endpoint_ids')or{}).get(code)if binding else None
            if binding and binding.get('control_endpoint_id')is not None:
                if len(control_codes)!=1:raise ValueError('多控制编号须用control_endpoint_ids逐代码绑定')
                requested=binding['control_endpoint_id']
            if requested is not None:
                if requested not in candidates:
                    raise ValueError('控制终点绑定不属于本箱/范围/控制编号候选')
                e.refs(binding.get('basis_source_ids'),'control_binding.basis_source_ids')
                if row['state']!=['normal_candidate']:
                    todo.append(dict(problem='取消/分期/预留控制回路不能自动正常定位',circuit_id=row['id'],control_code=code))
                else:
                    link['chosen_endpoint']=requested;selected_endpoints.add(requested)
                    for detail in diagnostics:
                        if detail['endpoint_id']==requested:detail['selected']=True
            elif candidates:
                todo.append(dict(problem='控制编号同号多实例'if len(candidates)>1 else'控制编号终点关系待核',circuit_id=row['id'],control_code=code,candidates=candidates))
            else:todo.append(dict(problem='控制编号未对到平面物理终点',circuit_id=row['id'],control_code=code))
            if count is None or link['spec']is None:
                todo.append(dict(problem='控制线缆规格/根数未闭合，不由动力回路复制',circuit_id=row['id'],control_code=code))
            row['control_links'].append(link)
        if binding and binding.get('control_endpoint_id')is not None and not control_codes:
            raise ValueError('无源控制编号不能绑定控制终点')
        matching_feeders = [f for f in feeder_rows if f.get('box') == row['source_box'] and f.get('circuit_code') == row['circuit_code']]
        for f in matching_feeders:
            e.refs(f.get('source_ids'), 'feeder_rows.source_ids')
            for field, actual in [('upstream', row['fields'].get('upstream')), ('destination', row['endpoint_label'])]:
                if f.get(field) is not None and actual is not None and f[field] != actual:
                    conflict = dict(field=field, table=actual, feeder=f[field], source_ids=f['source_ids'])
                    row['upstream_conflicts'].append(conflict)
                    todo.append(dict(problem='系统图/干线表引自去向冲突', circuit_id=row['id'], conflict=conflict))
    endpoint_audit=[]
    for pid,p in endpoints.items():
        references=endpoint_references[pid]
        eligible=[x for x in references if x['eligibility']=='candidate']
        disposition='selected'if pid in selected_endpoints else'candidate_unselected'if eligible else'excluded'if references else'unreferenced'
        audit=dict(endpoint_id=pid,physical_id=p.get('physical_id',pid),physical_source_id=p['physical_source_id'],
            label=p['label'],label_source_ids=p['label_source_ids'],scope_id=p['scope_id'],source_box=p.get('source_box'),
            binding_basis=p['binding_basis'],disposition=disposition,references=references,
            consumed=pid in selected_endpoints)
        endpoint_audit.append(audit)
        if disposition in ('unreferenced','excluded'):
            todo.append(dict(problem='平面物理终点未入回路台账',endpoint=audit))
    # Reconcile the relationships already extracted above; do not require the caller to retype them as feeder_rows.
    box_tables=defaultdict(list)
    for table in tables:
        if table['box']is not None:box_tables[table['box']].append(table)
    known_boxes=set(box_tables)|set(request.get('expected_boxes',[]))
    parsed_outgoing=[]
    for row in ledger:
        destination=row['fields'].get('destination')
        if destination in known_boxes and row['source_box']is not None:
            parsed_outgoing.append(dict(from_box=row['source_box'],to_box=destination,circuit_id=row['id'],
                state=row['state'],source_ids=sorted(set(row['source_ids']+row['field_sources'].get('destination',[]))),
                destination_source_ids=row['field_sources'].get('destination',[]),
                relationship='parsed_box_to_box_destination_candidate'))
    box_upstream_checks=[]
    for name,table_group in sorted(box_tables.items()):
        own_rows=[r for r in ledger if r['source_box']==name]
        declarations=[dict(upstream=r['fields']['upstream'],circuit_id=r['id'],state=r['state'],
            source_ids=r['field_sources'].get('upstream',[]),row_source_ids=r['source_ids'])
            for r in own_rows if r['fields'].get('upstream')not in(None,'') and r['fields']['upstream']!=name]
        self_references=[dict(circuit_id=r['id'],source_ids=r['field_sources'].get('upstream',[]))
            for r in own_rows if r['fields'].get('upstream')==name]
        incoming=[r for r in parsed_outgoing if r['to_box']==name]
        declared_names=sorted({d['upstream']for d in declarations})
        incoming_names=sorted({d['from_box']for d in incoming})
        missing_declarations=[d for d in declarations if d['upstream']not in incoming_names]
        conflicts=[link for link in incoming if declared_names and link['from_box']not in declared_names]
        parent_claim_in_scope=bool(declarations or incoming)
        check=dict(box=name,table_source_ids=sorted({s for t in table_group for s in [t['header_source_id'],*t['title_source_ids']]}),
            declared_upstream=declarations,self_referenced_rows=self_references,parsed_incoming=incoming,
            declared_upstream_names=declared_names,incoming_box_names=incoming_names,
            declarations_without_incoming=missing_declarations,conflicting_incoming=conflicts,
            status='no_parent_claim_in_scope'if not parent_claim_in_scope else'candidate_conflict'if conflicts else'uncovered'if not incoming or not declarations or missing_declarations else'consistent_candidate',
            meaning='仅已提取字段之间的来源互证；状态/分期保留，不据此补根设备或形成采用量')
        box_upstream_checks.append(check)
        if conflicts:
            todo.append(dict(problem='已解析出线与本箱引自声明不一致',box_upstream_check=check))
        if parent_claim_in_scope and (not incoming or not declarations or missing_declarations):
            todo.append(dict(problem='箱上级关系未被已解析台账完整覆盖',box_upstream_check=check))
        if len(table_group)>1:
            todo.append(dict(problem='上级对账同名箱存在多个图面表头，物理身份待核',box_upstream_check=check))
    specs = [t for t in texts if spec_re.search(t['text'])]
    unassigned = [t for t in specs if t['source_id'] not in assigned]
    missing_boxes = [b for b in request.get('expected_boxes', []) if b not in {r['source_box'] for r in ledger}]
    feeder_unmatched = [f for f in feeder_rows if not any(f.get('box') == r['source_box'] and f.get('circuit_code') == r['circuit_code'] for r in ledger)]
    for t in unassigned:
        todo.append(dict(problem='规格文字未入回路台账', source_id=t['source_id'], text=t['text']))
    for box in missing_boxes:
        todo.append(dict(problem='预期箱未被回路表覆盖', box=box))
    for row in feeder_unmatched:
        e.refs(row.get('source_ids'), 'feeder_rows.source_ids')
        todo.append(dict(problem='干线出线未入回路台账', row=row))
    identities = [(r['source_box'], r['circuit_code']) for r in ledger]
    for identity in sorted(set(identities), key=str):
        if identities.count(identity) > 1:
            todo.append(dict(problem='箱/回路身份重复候选，不自动合并', box=identity[0], circuit_code=identity[1]))
    if not headers:
        todo.append(dict(problem='范围内未找到回路表头，不是确认零回路'))
    return finish(e, dict(schema='qto-electrical-circuit-candidates/1.0', tables=tables, circuits=ledger,
        design_clause_source=request.get('design_clauses'), applicable_clause_ids=applicable, endpoint_instances=list(endpoints.values()),
        coverage=dict(found_headers=len(headers), parsed_headers=sum(bool(t['circuit_ids']) for t in tables),
                      unassigned_spec_texts=unassigned, missing_boxes=missing_boxes, unmatched_feeders=feeder_unmatched,
                      upstream_uncovered_boxes=[x['box']for x in box_upstream_checks if x['status']in('uncovered','candidate_conflict')],
                      boxes_without_parent_claim_in_scope=[x['box']for x in box_upstream_checks if x['status']=='no_parent_claim_in_scope'],
                      endpoint_instance_count=len(endpoints),selected_endpoint_ids=sorted(selected_endpoints),
                      consumed_endpoint_ids=sorted(selected_endpoints),
                      referenced_candidate_endpoint_ids=sorted(a['endpoint_id']for a in endpoint_audit if any(r['eligibility']=='candidate'for r in a['references'])),
                      unconsumed_endpoint_ids=sorted(set(endpoints)-selected_endpoints),
                      unreferenced_endpoint_ids=[a['endpoint_id']for a in endpoint_audit if a['disposition']=='unreferenced'],
                      excluded_endpoint_ids=[a['endpoint_id']for a in endpoint_audit if a['disposition']=='excluded'],
                      endpoint_instances_not_in_ledger=[a for a in endpoint_audit if a['disposition']in('excluded','unreferenced')]),
        endpoint_reconciliation=endpoint_audit,
        parsed_box_relationships=parsed_outgoing,box_upstream_checks=box_upstream_checks,
        meaning='回路表文字/表线候选与覆盖差集；不推断省略回路、未知根数或正式终点'), todo)


if __name__ == '__main__':
    raise SystemExit(cli(calculate, __doc__))
