"""Traceable method decisions -> maintenance candidates; never changes rules or quantities.

Local hashes prove content consistency, not identity authentication. This tool never
creates human decisions or their evidence; anonymous fixtures are not real approvals.
"""
from __future__ import annotations
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from qto_human_review_evidence import approval_field_issues, _aware_time
from qto_review_contract import extract_json_file, normalize_view, validate_feedback
from qto_review_identity import decisions, fingerprint, handoff_actions, require
from recompute_qto_dependencies import recalculate
import read_qto_review_feedback

CANDIDATE = 'qto-rule-candidate/1.0'
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[2]
DISPOSITIONS = ('reject', 'project_only', 'general_candidate')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def json_document(value):
    """The public artifact contract is the exact JSON document emitted by main."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def strings(value, *, empty=False):
    return isinstance(value, list) and (empty or bool(value)) and all(text(x) for x in value)


def read_ref(reference, base, *, raw=False):
    require(isinstance(reference, dict) and text(reference.get('path')) and text(reference.get('sha256')),
            '引用须有 path、sha256')
    path = Path(reference['path'])
    path = (Path(base) / path).resolve() if not path.is_absolute() else path.resolve()
    require(path.is_file() and sha(path) == reference['sha256'], '引用缺失或字节变化：' + str(path))
    document = path.read_bytes() if raw else extract_json_file(path)
    require(raw or isinstance(document, dict), '引用 JSON 须为对象：' + str(path))
    return path, document


def ref(path, **extra):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path), **extra}


def verify_calculation_files(rows, supplied, base, label):
    require(isinstance(supplied, list) and all(isinstance(r, dict) for r in supplied) and
            len({r.get('id') for r in supplied}) == len(supplied), label + '文件映射须唯一')
    verified = []
    for row in rows:
        item = next((r for r in supplied if r.get('id') == row.get('id')), None)
        path, _ = read_ref(item, base, raw=True)
        require(item['sha256'] == row.get('sha256'), label + '物理文件与计算输入不一致')
        verified.append(ref(path, id=row['id']))
    return verified


def dependency_bytes():
    """Hash the runtime import closure; installed exports do not depend on maintenance files."""
    pending = [Path(__file__).resolve(), SCRIPT_DIR / 'read_qto_review_feedback.py']
    found = {}
    while pending:
        path = pending.pop()
        if path in found:
            continue
        found[path] = sha(path)
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            names = ([node.module] if isinstance(node, ast.ImportFrom) else
                     [x.name for x in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                for folder in (SCRIPT_DIR, ROOT / 'skills/qto-electrical-routes/scripts'):
                    local = folder / ((name or '').split('.')[0] + '.py')
                    if local.is_file():
                        pending.append(local)
    return {p.relative_to(ROOT).as_posix(): h for p, h in sorted(found.items())}


def human_record(evidence_ref, base, *, schema, project, expected, source_label,
                 source_path, source_document, source_fingerprint):
    path, document = read_ref(evidence_ref, base)
    require(document.get('schema') == schema and document.get('project_id') == project,
            '人工凭据版本或项目不一致')
    records = document.get('records')
    require(isinstance(records, list), '人工凭据 records 须为数组')
    matches = [r for r in records if isinstance(r, dict) and r.get('record_id') == evidence_ref.get('record_id')]
    require(len(matches) == 1, '人工凭据记录须唯一')
    record = matches[0]
    issues = approval_field_issues({'fingerprint': source_fingerprint, 'reviewer': record.get('reviewer'),
                                   'reviewer_type': record.get('reviewer_type'),
                                   'reviewed_at': record.get('reviewed_at'), 'evidence': evidence_ref})
    require(not issues, '；'.join(issues))
    require(all(record.get(k) == v for k, v in expected.items()), '人工凭据未绑定当前裁决/审核内容')
    for field in ('reviewer', 'reviewer_type', 'reviewed_at'):
        require(record.get(field) == source_document.get(field), '原保存意见与凭据的 ' + field + ' 不一致')
    source, _ = read_ref(record.get(source_label), path.parent, raw=True)
    require(source == source_path.resolve(), '人工凭据未指向本次原保存意见')
    require(_aware_time(record[source_label].get('saved_at')), '原保存意见 saved_at 须为带时区时间')
    if source_label == 'source_feedback':
        require(record[source_label].get('annotation_key') == expected['annotation_key'], '原批注键不一致')
    require(record[source_label].get('saved_at') ==
            source_document.get('updated_at', source_document.get('reviewed_at')), '原保存意见时间不一致')
    return ref(path, record_id=evidence_ref['record_id'])


def normalize_draft(draft):
    require(isinstance(draft, dict) and draft.get('schema') == 'qto-rule-candidate-draft/1.0', '候选草稿版本无效')
    for field in ('rule_family', 'draft_version', 'proposed_rule'):
        require(text(draft.get(field)), '候选草稿缺少 ' + field)
    scope = draft.get('applicability')
    require(isinstance(scope, dict), '候选须明确适用范围')
    for field in ('professional', 'object_kind', 'object_type', 'quantity_layer', 'unit',
                  'measurement_basis', 'region', 'contract_scope'):
        require(text(scope.get(field)), '适用范围缺少 ' + field)
    for field in ('trigger_conditions', 'boundaries', 'exceptions', 'counterexamples', 'pending_branches'):
        require(strings(draft.get(field), empty=field == 'pending_branches'), '候选须显式列明 ' + field)
    target = draft.get('suggested_reference')
    require(text(target), '须建议 reference 归属')
    posix = PurePosixPath(target)
    require(not posix.is_absolute() and '..' not in posix.parts and '\\' not in target and
            len(posix.parts) >= 4 and posix.parts[0] == 'skills' and 'references' in posix.parts and
            posix.suffix == '.md' and ':' not in target, 'reference 须为插件内相对 references/*.md 路径')
    fields = ('schema', 'rule_family', 'draft_version', 'proposed_rule', 'applicability', 'trigger_conditions',
              'boundaries', 'exceptions', 'counterexamples', 'pending_branches', 'suggested_reference')
    return {k: copy.deepcopy(draft[k]) for k in fields}


def graph_bindings(view, active, fresh):
    nodes = {r['id']: r for r in fresh['nodes']}
    bindings = []
    for obj in view['objects']:
        if obj['catalog_key'] not in active:
            continue
        linked = obj.get('calculation_bindings') or (
            [{'node_id': obj.get('calculation_node_id'), 'calculation_fingerprint': obj.get('calculation_fingerprint')}]
            if obj.get('calculation_node_id') else [])
        require(isinstance(linked, list) and all(isinstance(b, dict) for b in linked), '对象计算绑定须为数组')
        for binding in linked:
            node = nodes.get(binding.get('node_id'))
            require(node is not None and node['fingerprint'] == binding.get('calculation_fingerprint') and
                    node['status'] in ('CALCULATED', 'BLOCKED'), '对象绑定或计算结果已失效')
            quantities = [q for q in obj['quantities'] if
                          (binding.get('quantity_name') is None or q['name'] == binding['quantity_name']) and
                          q['value'] == node['value'] and q['unit'] == node['unit'] and q['level'] == node['quantity_level']]
            require(len(quantities) == 1, '对象展示数量未唯一对应当前计算绑定')
            bindings.append({'catalog_key': obj['catalog_key'], 'node_id': node['id'], 'fingerprint': node['fingerprint'],
                             'quantity_name': quantities[0]['name'],
                             'value': node['value'], 'unit': node['unit'], 'quantity_level': node['quantity_level'],
                             'review_state': node['review_state'], 'review_evidence': node['review_evidence'],
                             'calculation_status': node['status'], 'calculation_reason': node['reason'],
                             'professional': obj['professional'], 'object_kind': obj['kind'],
                             'object_type': obj['category']})
    return bindings


def context_fingerprint(fresh, bindings, networks):
    return fingerprint({'calculation_input_fingerprint': fresh['input_fingerprint'] if fresh else None,
                        'calculation_run_fingerprint': fingerprint(fresh) if fresh else None,
                        'bindings': bindings, 'networks': networks})


def export_candidate(request_path, *, preview=False):
    """Reconsume existing producers; deterministic replay rejects stale downstream artifacts."""
    request_path = Path(request_path).resolve()
    request = extract_json_file(request_path)
    require(isinstance(request, dict) and request.get('schema') == 'qto-rule-candidate-request/1.0', '导出请求版本无效')
    base = request_path.parent
    paths, docs = {}, {}
    fields = ['view', 'feedback', 'handoff']
    if any(request.get(k) is not None for k in ('calculation_input', 'calculation_run')):
        fields.extend(('calculation_input', 'calculation_run'))
    for field in fields:
        paths[field], docs[field] = read_ref(request.get(field), base)
    view = normalize_view(docs['view'], paths['view'].parent)
    feedback, unmatched = validate_feedback(docs['feedback'], view)
    require(not feedback.get('decision_migration_pending'), '旧意见迁移尚未完成')
    key = request.get('annotation_key')
    raw = docs['feedback'].get('annotations', {}).get(key)
    record = feedback['annotations'].get(key)
    require(isinstance(raw, dict) and isinstance(record, dict) and record.get('decision_snapshot'),
            '缺少已保存的明确事项裁决；草稿或普通意见不可提炼')
    require(record.get('applicability_state') == 'current' and
            not any(record.get(k) for k in ('decision_conflict', 'merge_conflict', 'migration_state', 'identity_hold')),
            '裁决不是 current 或存在冲突/迁移挂起')
    actions = handoff_actions(view, feedback)
    action = next((a for a in actions if a['annotation_key'] == key), None)
    require(action is not None and record.get('action') in ('采用建议', '调整处理') and
            text(record.get('confirmed_result')) and text(record.get('reason')), '须明确采用或调整结论')
    if not preview:
        require(raw.get('decision_type') == 'method_confirmation', '原批注未明确确认方法裁决')
    handoff = docs['handoff']
    require(handoff.get('schema') == 'qto-review-handoff/1.0' and
            all(handoff.get(k) == view.get(k) for k in ('project_id', 'review_id', 'source_sha256', 'result_fingerprint')) and
            handoff.get('feedback') == feedback and handoff.get('decision_actions') == actions and
            handoff.get('unmatched') == unmatched and handoff.get('quantity_changes') == 0,
            'feedback producer 交接与当前消费不一致')
    sources = request.get('source_files')
    require(isinstance(sources, list) and len(sources) == len(view['sources']) and
            all(isinstance(s, dict) for s in sources) and
            len({s.get('id') for s in sources}) == len(sources), '来源文件映射须完整且唯一')
    actual_sources = []
    for source in view['sources']:
        item = next((s for s in sources if s.get('id') == source['id']), None)
        source_path, _ = read_ref(item, base, raw=True)
        require(item['sha256'] == source['sha256'], '物理源与当前视图来源字节不一致')
        actual_sources.append(ref(source_path, id=source['id']))
    calculation_sources, calculation_rules, fresh = [], [], None
    if 'calculation_input' in docs:
        graph = docs['calculation_input']
        calculation_sources = verify_calculation_files(
            graph.get('sources', []), request.get('calculation_sources', sources), base, '计算来源')
        calculation_rules = verify_calculation_files(
            graph.get('rules', []), request.get('calculation_rules', []), base, '计算规则')
        previous = previous_path = None
        if request.get('previous_run') is not None:
            previous_path, previous = read_ref(request['previous_run'], base)
        fresh = recalculate(graph, previous, paths['calculation_input'].parent,
                            previous_path.parent if previous_path else None)
        require(fresh == docs['calculation_run'] and fresh['project_id'] == view['project_id'],
                '计算输入、当前结果或继承凭据已变化，须重新运行 producer')
        require(fresh.get('business_adoption_allowed') is False, '业务量采用门禁不得开启')
    decision = next(d for d in decisions(view) if d['id'] == action['decision_id'])
    require(decision.get('kind') == 'business_choice', '只有明确业务取舍可提炼')
    active = action['scope']['member_keys']
    require(active, '适用对象为空或全部被例外挂起')
    bindings = graph_bindings(view, active, fresh) if fresh else []
    network_specs = request.get('network_evidence', [])
    require(isinstance(network_specs, list) and all(isinstance(n, dict) for n in network_specs),
            'network_evidence 须为显式版本网络证据数组')
    require(len({n.get('network_id') for n in network_specs}) == len(network_specs), '网络身份重复')
    networks = []
    for spec in network_specs:
        from qto_rule_electrical_adapter import consume
        mapped, context = consume(spec, base=base, view=view, decision=decision, active=active,
                                  read_ref=read_ref, ref=ref)
        bindings.extend(mapped)
        networks.append(context)
    require(set(active) == {b['catalog_key'] for b in bindings}, '当前有效对象缺少显式计算节点或网络目标绑定')
    context_fp = context_fingerprint(fresh, bindings, networks)
    if preview:
        return json_document({'schema': 'qto-rule-context-preview/1.0', 'status': 'NOT_HUMAN_APPROVAL',
                'method_context_fingerprint': context_fp, 'bindings': bindings, 'network_evidence': networks,
                'business_adoption_allowed': False, 'runtime_changed': False, 'reference_changed': False,
                'maturity_changed': False})
    require(raw.get('method_context_fingerprint') == context_fp, '原方法确认未绑定当前输入/计算/网络对象上下文')
    evidence = human_record(
        request.get('human_decision_evidence'), base, schema='qto-rule-decision-evidence/1.0',
        project=view['project_id'], expected={
            'decision': 'confirm_method_decision', 'annotation_key': key, 'annotation_fingerprint': fingerprint(raw),
            'decision_id': decision['id'], 'decision_revision': decision['revision'],
            'method_context_fingerprint': context_fp,
            'source_fingerprint': view['source_sha256'], 'result_fingerprint': view['result_fingerprint'],
            'decision_result_fingerprint': decision['result_fingerprint']},
        source_label='source_feedback', source_path=paths['feedback'], source_document=raw,
        source_fingerprint=decision['result_fingerprint'])
    draft = normalize_draft(request.get('draft'))
    require(all(b['unit'] == draft['applicability']['unit'] and
                b['quantity_level'] == draft['applicability']['quantity_layer'] and
                all(b[k] == draft['applicability'][k] for k in ('professional', 'object_kind', 'object_type'))
                for b in bindings), '草稿专业、对象、数量层级或单位与实际对象不一致')
    out = {'schema': CANDIDATE, 'status': 'DRAFT_FOR_MAINTENANCE', 'request': ref(request_path),
           'project_id': view['project_id'], 'source_fingerprint': view['source_sha256'],
           'result_fingerprint': view['result_fingerprint'], 'source_files': actual_sources,
           'calculation_source_files': calculation_sources, 'calculation_rule_files': calculation_rules,
           'view_revision': view['revision'], 'input_fingerprint': context_fp,
           'calculation_input_fingerprint': fresh['input_fingerprint'] if fresh else None,
           'network_evidence': networks,
           'origin': {'annotation_key': key, 'original_question': decision['question'],
                      'original_answer': copy.deepcopy(raw), 'decision': copy.deepcopy(decision),
                      'effective_scope': copy.deepcopy(action['scope']), 'bindings': bindings,
                      'human_decision_evidence': evidence},
           'draft': draft, 'rule_fingerprint': fingerprint(draft),
           'draft_authority': 'model_organized_candidate_not_human_rule_approval',
           'runtime_dependencies': dependency_bytes(), 'business_adoption_allowed': False,
           'runtime_changed': False, 'reference_changed': False, 'maturity_changed': False,
           'identity_authenticated': False}
    out['candidate_fingerprint'] = fingerprint(out)
    return json_document(out)


def verify_candidate(reference, base):
    path, candidate = read_ref(reference, base)
    require(candidate.get('schema') == CANDIDATE, '规则候选版本无效')
    request_path, _ = read_ref(candidate.get('request'), path.parent)
    require(export_candidate(request_path) == candidate,
            '候选、来源、计算、人工凭据或运行依赖已变化，须重导出')
    return path, candidate


def review_candidate(request_path):
    request_path = Path(request_path).resolve()
    request = extract_json_file(request_path)
    require(isinstance(request, dict) and request.get('schema') == 'qto-rule-maintenance-request/1.0', '维护审核请求版本无效')
    base = request_path.parent
    candidate_path, candidate = verify_candidate(request.get('candidate'), base)
    review_path, review = read_ref(request.get('review'), base)
    require(review.get('schema') == 'qto-rule-maintenance-review/1.0' and
            review.get('candidate_sha256') == sha(candidate_path), '审核未绑定当前候选字节')
    disposition = review.get('disposition')
    require(disposition in DISPOSITIONS and text(review.get('reason')), '审核须明确分支与理由')
    scope = review.get('applicability_review')
    require(isinstance(scope, dict) and all(text(scope.get(k)) for k in
            ('project_specific', 'region_contract_limits', 'exception_review', 'covered_branch')),
            '审核须逐项判断项目特例、地区合同、例外和覆盖分支')
    require(scope.get('rule_fingerprint') == candidate['rule_fingerprint'], '审核覆盖的规则版本或适用边界不同')
    evidence = human_record(
        request.get('human_review_evidence'), base, schema='qto-rule-maintenance-evidence/1.0',
        project=candidate['project_id'], expected={'decision': disposition, 'candidate_sha256': sha(candidate_path),
        'review_fingerprint': fingerprint(review)}, source_label='source_review', source_path=review_path,
        source_document=review, source_fingerprint=fingerprint(review))
    cases = review.get('independent_cases', [])
    require(isinstance(cases, list), '独立案例须为数组')
    accepted, seen = [], set()
    if disposition == 'general_candidate':
        require(cases, '单项目认可不能升级通用候选；须独立案例')
        origin_sources = {s['sha256'] for s in
                          [*candidate['source_files'], *candidate['calculation_source_files']]}
        for item in cases:
            path, other = verify_candidate(item, review_path.parent)
            identity = (other['project_id'], other['source_fingerprint'])
            require(identity not in seen and other['project_id'] != candidate['project_id'] and
                    other['source_fingerprint'] != candidate['source_fingerprint'] and
                    origin_sources.isdisjoint({s['sha256'] for s in
                                               [*other['source_files'], *other['calculation_source_files']]}),
                    '独立案例须另项目、另物理源且不重复')
            require(other['rule_fingerprint'] == candidate['rule_fingerprint'],
                    '独立案例规则版本、对象、单位、口径或触发边界不一致')
            seen.add(identity)
            accepted.append(ref(path, project_id=other['project_id'], source_fingerprint=other['source_fingerprint'],
                                rule_fingerprint=other['rule_fingerprint']))
    out = {'schema': 'qto-rule-maintenance-receipt/1.0', 'status': 'EVIDENCE_BOUND_REVIEW',
           'candidate': ref(candidate_path), 'review': ref(review_path), 'human_review_evidence': evidence,
           'disposition': disposition, 'reason': review['reason'], 'applicability_review': copy.deepcopy(scope),
           'independent_cases': accepted, 'business_adoption_allowed': False,
           'runtime_changed': False, 'reference_changed': False, 'maturity_changed': False,
           'identity_authenticated': False, 'request': ref(request_path),
           'maintenance_dependencies': {**dependency_bytes(),
                                       'maintenance/review_rule_candidates.py':
                                       sha(ROOT / 'maintenance/review_rule_candidates.py')}}
    if disposition == 'project_only':
        out['project_adoption_scope'] = {'project_id': candidate['project_id'],
                                         'scope': candidate['origin']['effective_scope'],
                                         'formal_quantity_adoption': False}
    if disposition == 'general_candidate':
        out['maintenance_proposal'] = {'schema': 'qto-rule-maintenance-proposal/1.0',
            'status': 'AWAIT_MAINTENANCE_CHANGE', 'rule_fingerprint': candidate['rule_fingerprint'],
            'draft': candidate['draft'], 'project_evidence_must_not_enter_runtime': True,
            'required_next_steps': ['维护会话审查并改目标 reference', '按覆盖分支编制反例与合法对照',
                                    '非作者复核与适用门禁', '按证据决定成熟度；另行授权发布安装']}
    return json_document(out)


def safe_output(maint_dir, name):
    maint = Path(maint_dir).resolve()
    require(maint != ROOT and ROOT not in maint.parents, '维护输出不得位于插件源码/运行包内')
    require(text(name) and Path(name).name == name and name.endswith('.json'), '输出须为维护区内单个 JSON 文件名')
    target = maint / '规则候选' / name
    require(not target.exists(), '输出已存在，请使用新版本名称')
    require(maint in target.resolve().parents, '输出路径越出维护区')
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def main(argv=None, *, maintenance=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('review',) if maintenance else ('inspect', 'export'))
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--maint-dir', required=True, type=Path)
    parser.add_argument('--name', required=True)
    args = parser.parse_args(argv)
    target = None
    try:
        target = safe_output(args.maint_dir, args.name)
        out = review_candidate(args.request) if maintenance else export_candidate(args.request, preview=args.action == 'inspect')
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'status': out['status'], 'output': str(target), 'sha256': sha(target)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, StopIteration, UnicodeError, AttributeError) as exc:
        receipt = {'schema': 'qto-rule-candidate-rejection/1.0', 'status': 'INPUT_REQUIRED',
                   'action': args.action, 'error': str(exc), 'business_adoption_allowed': False,
                   'runtime_changed': False, 'reference_changed': False, 'maturity_changed': False}
        if target is not None:
            try:
                target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            except OSError as write_error:
                receipt['rejection_receipt_write_error'] = str(write_error)
        print(json.dumps(receipt, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
