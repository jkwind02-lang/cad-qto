#!/usr/bin/env python3
"""File-backed coordination checks. Never calculate, allocate or approve quantities."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import zipfile
import zlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SHARED = Path(__file__).resolve().parents[2] / '_qto-shared-evidence' / 'scripts'
IDENTITY = ('physical_id', 'quantity_type', 'unit', 'basis', 'actual_scope',
            'source_revision', 'source_fingerprint')


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('非有限数字: ' + x)))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def atom(value):
    return isinstance(value, str) and bool(value.strip())


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def module(name):
    if str(SHARED) not in sys.path:
        sys.path.insert(0, str(SHARED))
    spec = importlib.util.spec_from_file_location(name, SHARED / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


class Report:
    def __init__(self, operation):
        self.data = {'schema': 'qto-coordination-check/1.0', 'operation': operation,
                     'status': 'PASS', 'findings': [], 'engineering_acceptance': False}

    def add(self, code, message, state='FAIL', **fields):
        self.data['findings'].append(dict(code=code, message=message, state=state, **fields))

    def finish(self):
        states = {x['state'] for x in self.data['findings']}
        self.data['status'] = 'FAIL' if 'FAIL' in states else 'UNVERIFIED' if 'UNVERIFIED' in states else 'PASS'
        return self.data


def ref_file(ref, base, report, label):
    if not isinstance(ref, dict) or not atom(ref.get('file')) or not re.fullmatch(r'[0-9a-f]{64}', str(ref.get('sha256', ''))):
        report.add('file_reference_missing', label + '须有file与完整sha256', 'UNVERIFIED')
        return None
    path = (Path(base) / ref['file']).resolve()
    if not path.is_file():
        report.add('file_missing', label + '文件不存在', file=str(path))
        return None
    if digest(path) != ref['sha256']:
        report.add('file_fingerprint_mismatch', label + '实际文件与冻结指纹不一致', file=str(path))
        return None
    return path


def checked_json(ref, base, report, label):
    path = ref_file(ref, base, report, label)
    if path is None:
        return None, None
    try:
        return load_json(path), path
    except (ValueError, UnicodeError) as exc:
        report.add('json_unreadable', label + ': ' + str(exc))
        return None, path


def explicit_waiver(waiver, base, report, role, package_id):
    if not isinstance(waiver, dict):
        return False
    path = ref_file(waiver.get('source'), base, report, '用户豁免来源')
    quote = waiver.get('quote')
    scope = waiver.get('scope')
    if (waiver.get('actor') != 'user' or not atom(waiver.get('at')) or not atom(quote)
            or not isinstance(scope, dict) or scope.get('package_id') != package_id
            or role not in scope.get('roles', [])):
        report.add('waiver_invalid', '豁免须为用户原话、时间和本包具体交付角色范围')
        return False
    try:
        if path is None or quote not in path.read_text(encoding='utf-8-sig'):
            report.add('waiver_quote_missing', '豁免原话未见于来源文件')
            return False
    except UnicodeError:
        report.add('waiver_source_unreadable', '豁免来源须为可核验文本')
        return False
    report.add('user_waiver', '用户明示豁免已留痕', 'INFO', role=role, package_id=package_id)
    return True


def registration_valid(text):
    header = text.split('---', 2)[1] if text.startswith('---') and text.count('---') >= 2 else ''
    return bool(header) and all(re.search(r'^' + re.escape(f) + r'\s*:', header, re.M)
                               for f in module('register_plugin_issue').REQUIRED_FIELDS) and '## 现象' in text


def check_pdf_summary(path, report):
    """Read page content with an optional parser; this does not inspect quantity or layout accuracy."""
    try:
        from pypdf import PdfReader
    except ImportError:
        report.add('artifact_parser_unavailable', 'PDF解析能力不可用，汇总内容可读性未核', 'UNVERIFIED', role='summary')
        return
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted and not reader.decrypt(''):
            report.add('artifact_encrypted', 'PDF需要密码，汇总内容可读性未核', 'UNVERIFIED', role='summary')
            return
        if not reader.pages:
            raise ValueError('PDF没有可读页面')
        has_content = False
        for page in reader.pages:
            if page.mediabox.width <= 0 or page.mediabox.height <= 0:
                raise ValueError('PDF页面尺寸无效')
            content = page.get_contents()
            if content is not None:
                # Accessing operations parses/decompresses the actual content stream.
                has_content = bool(content.operations) or has_content
                page.extract_text()
        if not has_content:
            report.add('artifact_content_missing', 'PDF没有可读取的页面内容，不能确认成果汇总可用', 'UNVERIFIED', role='summary')
    except Exception as exc:
        # Parser format errors have several exception types; keep all of them inside this format boundary.
        report.add('artifact_unreadable', 'PDF页面内容无法解析: ' + str(exc), role='summary')


def release_source_pair(source, report, label):
    if not isinstance(source, dict) or source.get('schema') != 'cad-qto-release-source/1.0':
        report.add('release_source_schema', label + '须为cad-qto-release-source/1.0')
        return None
    if (not re.fullmatch(r'\d+\.\d+\.\d+', str(source.get('business_version', '')))
            or not re.fullmatch(r'[0-9a-f]{40}', str(source.get('source_commit', '')))):
        report.add('release_source_fields', label + '须有业务版本和完整提交号')
        return None
    return source['business_version'], source['source_commit']


def validate_delivery(data, base_dir):
    r = Report('delivery')
    if not isinstance(data, dict) or data.get('schema') != 'qto-delivery-manifest/1.0':
        r.add('schema', '交付清单schema应为qto-delivery-manifest/1.0')
        return r.finish()
    pid = data.get('package_id')
    if not atom(pid) or not isinstance(data.get('has_numeric_results'), bool):
        r.add('package_inputs', '须声明package_id和布尔has_numeric_results')
    roles = defaultdict(list)
    for a in data.get('artifacts', []):
        if not isinstance(a, dict) or not atom(a.get('role')):
            r.add('artifact_role', '每个交付文件须有role')
            continue
        roles[a['role']].append(a)
        path = ref_file(a, base_dir, r, a['role'])
        if path and (path.stat().st_size == 0 or (a['role'] == 'html_review' and path.suffix.lower() not in ('.html', '.htm'))):
            r.add('artifact_unreadable', '交付文件为空或HTML扩展名不符', role=a['role'])
        if path and a['role'] in ('summary', 'html_review'):
            try:
                if path.suffix.lower() in ('.md', '.txt', '.html', '.htm', '.csv'):
                    text = path.read_text(encoding='utf-8-sig')
                    if a['role'] == 'html_review' and '<html' not in text.lower():
                        raise ValueError('HTML复核文件缺html文档结构')
                elif path.suffix.lower() == '.xlsx':
                    with zipfile.ZipFile(path) as z:
                        if z.testzip() or 'xl/workbook.xml' not in z.namelist():
                            raise ValueError('不是可读xlsx')
                elif path.suffix.lower() == '.pdf':
                    check_pdf_summary(path, r)
                elif path.suffix.lower() != '.pdf':
                    raise ValueError('不支持的用户可读汇总格式')
            except (OSError, UnicodeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile,
                    RuntimeError, EOFError, zlib.error) as exc:
                r.add('artifact_unreadable', str(exc), role=a['role'])
    if data.get('has_numeric_results') is True:
        for role in ('summary', 'html_review'):
            if not roles[role] and not explicit_waiver(data.get('waivers', {}).get(role), base_dir, r, role, pid):
                r.add('required_artifact_missing', '数字成果缺少' + role, role=role)
    usage = data.get('plugin_usage')
    # Reuse the existing conditional validator; do not change minimum-result compatibility.
    if usage is None:
        r.add('plugin_usage_missing', '未提供插件使用与临时处理节，沿用旧版仅提醒', 'INFO')
        if data.get('method_replacements'):
            r.add('replacement_undeclared', '存在方法替代事实但缺插件使用记录')
        r.add('plugin_provenance_unverified', '没有使用记录，登记和发布来源未核', 'UNVERIFIED')
        return r.finish()
    errors = module('validate_minimum_result').check_plugin_usage({'插件使用与临时处理': usage})
    for e in errors:
        r.add('plugin_usage_fields', e)
    if not isinstance(usage, dict):
        return r.finish()
    versions = [{'插件版本': usage.get('插件版本'), '提交': usage.get('提交')}] + (usage.get('换版') or [])
    release_pairs = []
    development = data.get('source_mode') == 'development'
    source = None
    if development:
        dev = data.get('development_source', {})
        files = dev.get('files') or []
        valid = bool(files) and bool(re.fullmatch(r'[0-9a-f]{40}', str(dev.get('baseline_commit', ''))))
        for f in files:
            if ref_file(f, base_dir, r, '开发源码') is None:
                valid = False
        expected = hashlib.sha256(canonical(sorted(files, key=lambda f: f.get('file', ''))).encode('utf-8')).hexdigest()
        if dev.get('source_fingerprint') != expected or usage.get('development_source_fingerprint') != expected:
            r.add('development_fingerprint_mismatch', '开发使用记录与受测源码文件集指纹不同')
            valid = False
        if not valid:
            r.add('development_source_unverified', '缺有效基线提交和受测源码文件集，开发来源未核', 'UNVERIFIED')
        else:
            release_pairs.append((dev.get('business_version'), dev.get('baseline_commit')))
            r.data['source_verification'] = 'DEVELOPMENT_VERIFIED_NOT_RELEASED'
            r.add('development_not_released', '仅核对未发布开发源码；不表示已发布或已安装', 'INFO')
    elif data.get('source_mode', 'released') != 'released':
        r.add('source_mode', 'source_mode只能是released或development')
    else:
        source, _ = checked_json(data.get('release_source'), base_dir, r, '发布来源')
    if isinstance(source, dict):
        pair = release_source_pair(source, r, '正式发布来源')
        if pair:
            release_pairs.append(pair)
    for extra in data.get('additional_release_sources', []):
        src, _ = checked_json(extra, base_dir, r, '换版发布来源')
        if isinstance(src, dict):
            pair = release_source_pair(src, r, '换版发布来源')
            if pair:
                release_pairs.append(pair)
    for v in versions:
        if not isinstance(v, dict):
            continue
        if not any(v.get('插件版本') == ver and atom(commit) and len(commit) == 40
                   and atom(v.get('提交')) and commit.startswith(v['提交']) for ver, commit in release_pairs):
            r.add('release_usage_mismatch', '使用版本/提交不对应所附发布或开发来源', version=v)
    records = usage.get('记录') if isinstance(usage.get('记录'), list) else []
    row_ids = set()
    for row in records:
        if not isinstance(row, dict):
            continue
        rid = row.get('row_id')
        if not atom(rid) or rid in row_ids:
            r.add('usage_row_identity', '新增清单的使用记录须有唯一row_id')
        row_ids.add(rid)
        reg = str(row.get('登记', ''))
        if reg == '无' and (row.get('卡点') != '无' or row.get('改用方法与位置') != '无'):
            r.add('issue_undeclared', '已声明卡点或替代方法却将登记写无', row_id=rid)
        if reg != '无' and (str(row.get('卡点', '')).strip() in ('有问题', '报错', '失败', '缺功能', '不好用', '不支持', '待补', '未知')
                            or len(str(row.get('卡点', '')).strip()) < 4):
            r.add('issue_description_vague', '卡点须保留报错原文或具体缺失能力', row_id=rid)
        if reg == '待协调登记':
            r.add('issue_waiting_collection', '待协调登记尚未完成收件回流', 'UNVERIFIED', row_id=rid)
        elif reg.endswith('.md'):
            reference = data.get('registrations', {}).get(reg)
            path = ref_file(reference, base_dir, r, '原始登记')
            if path:
                text = path.read_text(encoding='utf-8-sig')
                if path.name != reg or not registration_valid(text):
                    r.add('registration_source_invalid', '原始登记文件名或模板来源不符', row_id=rid)
    for fact in data.get('method_replacements', []):
        if not isinstance(fact, dict) or not atom(fact.get('usage_row_id')):
            r.add('replacement_source_missing', '方法替代事实须关联使用记录行')
        elif not any(x.get('row_id') == fact['usage_row_id'] and x.get('登记') != '无'
                     and x.get('改用方法与位置') not in ('无', '') for x in records if isinstance(x, dict)):
            r.add('replacement_undeclared', '方法替代事实与插件使用表写无或缺行冲突')
    if data.get('local_scripts_present'):
        r.add('local_script_notice', '发现自写脚本仅作提示，不能据此认定绕过插件', 'INFO')
    return r.finish()


def key(row):
    return tuple(canonical(sorted(row.get(k, [])) if k == 'actual_scope' else row.get(k)) for k in IDENTITY)


def valid_identity(row):
    if not isinstance(row, dict) or not all(atom(row.get(k)) for k in IDENTITY if k != 'actual_scope'):
        return False
    scope = row.get('actual_scope')
    return (isinstance(scope, list) and bool(scope) and all(atom(x) for x in scope)
            and len(scope) == len(set(scope)) and bool(re.fullmatch(r'[0-9a-f]{64}', row['source_fingerprint'])))


def validate_handoffs(data, base_dir):
    r = Report('handoffs')
    if not isinstance(data, dict) or data.get('schema') != 'qto-package-reconciliation/1.0':
        r.add('schema', '对账schema应为qto-package-reconciliation/1.0')
        return r.finish()
    rows, packages, outputs = {}, {}, defaultdict(list)
    for package in data.get('packages', []):
        if not isinstance(package, dict) or not atom(package.get('package_id')) or package['package_id'] in packages:
            r.add('package_identity', '工作包编号缺失或重复')
            continue
        pid = package['package_id']
        result, path = checked_json({'file': package.get('result_file'), 'sha256': package.get('result_sha256')}, base_dir, r, '工作包实际成果')
        packages[pid] = package
        if not isinstance(result, dict) or result.get('schema') != 'qto-package-quantity-rows/1.0' or result.get('package_id') != pid:
            r.add('package_result_identity', '实际成果schema或工作包身份不符', package_id=pid)
            continue
        for row in result.get('rows', []):
            if not isinstance(row, dict):
                r.add('result_row', '成果行须为对象', package_id=pid)
                continue
            row = dict(row, source_revision=result.get('source_revision'), source_fingerprint=result.get('source_fingerprint'))
            rid = row.get('row_id')
            if not atom(rid) or (pid, rid) in rows or not valid_identity(row):
                r.add('result_row_identity', '实际成果行缺唯一行ID或完整物理/范围/计量/版本身份', package_id=pid, row_id=rid)
                continue
            if not isinstance(row.get('included'), bool) or not (row.get('quantity') is None or number(row['quantity'])):
                r.add('result_row_quantity', '计入状态须为布尔，未知数量用null', package_id=pid, row_id=rid)
                continue
            if not isinstance(row.get('source_ids'), list) or not row['source_ids'] or not all(atom(s) for s in row['source_ids']):
                r.add('result_row_sources', '每行须有原编号source_ids', package_id=pid, row_id=rid)
                continue
            if len(row['source_ids']) != len(set(row['source_ids'])):
                r.add('result_row_sources', '每行原编号source_ids不能重复', package_id=pid, row_id=rid)
                continue
            rows[pid, rid] = row
            if row['included']:
                outputs[key(row)].append((pid, rid))
                if row['quantity'] is None:
                    r.add('quantity_unknown', '已计入行数量仍未知', 'UNVERIFIED', package_id=pid, row_id=rid)
    handoff_ids = set()
    for h in data.get('handoffs', []):
        if not isinstance(h, dict):
            r.add('handoff_identity', '交接须为对象')
            continue
        hid = h.get('handoff_id')
        if not atom(hid) or hid in handoff_ids or not valid_identity(h):
            r.add('handoff_identity', '交接编号或复合身份缺失/重复', handoff_id=hid)
            continue
        handoff_ids.add(hid)
        proposer, receiver = h.get('proposer_package'), h.get('receiver_package')
        if proposer not in packages or receiver not in packages or proposer == receiver:
            r.add('handoff_parties', '提出方和接收方须为两个已登记工作包', handoff_id=hid)
        if not atom(h.get('proposal_evidence')):
            r.add('proposal_evidence_missing', '交接缺提出依据', handoff_id=hid)
        if h.get('status') != 'accepted':
            r.add('handoff_unaccepted', '交接待收/拒收/转裁决均未闭合', 'UNVERIFIED', handoff_id=hid, actual_status=h.get('status'))
            continue
        target = rows.get((receiver, h.get('receiver_row_id')))
        if target is None or not target.get('included'):
            r.add('receiver_row_missing', '已接收须指向接收方实际计入成果行', handoff_id=hid)
            continue
        if h.get('receiver_result_sha256') != packages.get(receiver, {}).get('result_sha256'):
            r.add('receiver_result_fingerprint', '接收确认未绑定当前实际成果指纹', handoff_id=hid)
        different = [name for n, name in enumerate(IDENTITY) if key(h)[n] != key(target)[n]]
        if different:
            r.add('handoff_identity_mismatch', '接收行物理身份、数量类型、单位、口径、实际范围或资料版本不同', handoff_id=hid, fields=different)
        if not (h.get('quantity') is None or number(h.get('quantity'))):
            r.add('handoff_quantity_invalid', '交接数量须为有限数字或null', handoff_id=hid)
        elif h.get('quantity') is not None and h['quantity'] != target.get('quantity'):
            r.add('handoff_quantity_mismatch', '交接数量与实际接收行不同', handoff_id=hid)
        if any(pid == proposer for pid, _ in outputs.get(key(h), [])):
            r.add('proposer_still_counts', '已移交的相同实际范围提出方仍计入', handoff_id=hid)
    for identity, owners in outputs.items():
        if len(owners) > 1:
            r.add('multiple_outlets', '同物理对象/数量类型/单位/口径/实际范围有多个数量出口', outlets=owners)
    included_rows = [(location, row) for location, row in rows.items() if row['included']]
    for n, (location, row) in enumerate(included_rows):
        for other_location, other in included_rows[n + 1:]:
            same = all(row[k] == other[k] for k in IDENTITY if k != 'actual_scope')
            overlap = sorted(set(row['actual_scope']) & set(other['actual_scope']))
            if same and key(row) != key(other) and overlap:
                r.add('overlapping_outlets', '拆分范围仍有相同实际段，不能按不同包名避开重复', outlets=[location, other_location], overlap=overlap)
            elif (row['physical_id'] == other['physical_id'] and row['quantity_type'] == other['quantity_type']
                  and overlap and not same):
                r.add('quantity_basis_conflict', '同物同数量类型的重叠范围单位/口径/资料版本不同，须明确互斥对照或核对，不是两个独立出口',
                      outlets=[location, other_location], overlap=overlap)
    baseline, baseline_path = checked_json(data.get('baseline'), base_dir, r, '完整编号基准')
    complete = False
    if isinstance(baseline, dict):
        if baseline.get('schema') != 'qto-quantity-universe/1.0':
            r.add('baseline_schema', '编号基准schema不符')
        else:
            docs = baseline.get('source_documents')
            docs_valid = isinstance(docs, list) and bool(docs)
            for doc in docs or []:
                if ref_file(doc, baseline_path.parent, r, '基准原始资料') is None:
                    docs_valid = False
            complete = baseline.get('complete') is True and docs_valid
            if not baseline.get('items'):
                complete = False
                r.add('baseline_empty', '空基准不足以证明完整设计/清单没有应计对象', 'UNVERIFIED')
            if not complete:
                r.add('baseline_incomplete', '缺有来源的完整设计/清单基准，全量无漏未核', 'UNVERIFIED')
            expected, by_source = [], defaultdict(list)
            for item in baseline.get('items', []):
                if not valid_identity(item) or not atom(item.get('source_id')):
                    r.add('baseline_identity', '基准编号缺失/重复或复合身份不完整')
                    complete = False
                    continue
                previous = by_source[item['source_id']]
                for other in expected:
                    if (item['physical_id'] == other['physical_id'] and item['quantity_type'] == other['quantity_type']
                            and set(item['actual_scope']) & set(other['actual_scope'])):
                        r.add('baseline_overlap_ambiguous', '完整基准同物同数量类型实际范围重叠，不能作为两份合法出口',
                              source_id=item['source_id'], other_source_id=other['source_id'])
                        complete = False
                if previous and (item.get('relationship') != 'split' or not atom(item.get('split_basis'))
                        or any(x.get('relationship') != 'split' or not atom(x.get('split_basis'))
                               or any(x[k] != item[k] for k in IDENTITY if k != 'actual_scope')
                               or set(x['actual_scope']) & set(item['actual_scope']) for x in previous)):
                    r.add('baseline_split_invalid', '同原编号拆分须有明确依据且实际范围互斥', source_id=item['source_id'])
                    complete = False
                by_source[item['source_id']].append(item)
                expected.append(item)
                owners = outputs.get(key(item), [])
                if not owners:
                    r.add('zero_outlet', '基准原编号没有数量出口', source_id=item['source_id'])
                if len(owners) > 1:
                    r.add('source_multiple_outlets', '基准原编号有多个数量出口', source_id=item['source_id'], outlets=owners)
                for pid, rid in owners:
                    if item['source_id'] not in rows[pid, rid].get('source_ids', []):
                        r.add('source_id_not_conserved', '出口行未保留原编号', source_id=item['source_id'], package_id=pid, row_id=rid)
            for (pid, rid), row in rows.items():
                if not row['included']:
                    continue
                for sid in row.get('source_ids', []):
                    if sid not in by_source:
                        r.add('source_id_outside_baseline', '成果行原编号不在本次基准', 'FAIL' if complete else 'UNVERIFIED', source_id=sid, package_id=pid, row_id=rid)
                    elif not any(key(x) == key(row) for x in by_source[sid]):
                        r.add('source_identity_mismatch', '原编号对应物理/范围/计量/资料身份不符', source_id=sid, package_id=pid, row_id=rid)
            # Reuse existing independent coverage inspection rather than invent another completeness engine.
            if complete and expected:
                coverage = module('qto_review_statistics')
                items, qrows, objects = [], [], []
                grouped = defaultdict(list)
                for item in expected:
                    grouped[key(item)].append(item['source_id'] + '@' + canonical(sorted(item['actual_scope'])))
                for n, (identity, ids) in enumerate(grouped.items()):
                    iid = 'scope-' + str(n)
                    owners = outputs.get(identity, [])
                    rids = [pid + '::' + rid for pid, rid in owners]
                    scope = [identity[4]]
                    items.append({'id': iid, 'current_row_ids': rids,
                                  'coverage': {'computed_scope': scope if rids else [], 'uncomputed_scope': []}})
                    for pid, rid in owners:
                        row = rows[pid, rid]
                        qrows.append({'id': pid + '::' + rid, 'state': 'current',
                                      'member_keys': [s + '@' + canonical(sorted(row['actual_scope'])) for s in row['source_ids']],
                                      'evidence': {'state': 'verified', 'source_refs': [packages[pid]['result_file']]},
                                      'coverage': {'computed_scope': scope}})
                    objects.append({'id': iid, 'item_ids': [iid], 'member_keys': ids, 'row_ids': [], 'scope': scope,
                                    'relationship': 'aggregate' if len(ids) > 1 else 'one_to_one',
                                    'basis': '按显式编号与实际范围对账，不计算工程量'})
                result = coverage.build_coverage_check({'items': items, 'quantity_rows': qrows,
                          'coverage_baseline': {'state': 'explicit', 'source_refs': baseline['source_documents'], 'objects': objects}})
                r.data['coverage_check'] = result
                if result['status'] != 'pass':
                    r.add('shared_coverage_attention', '既有覆盖检查发现缺漏或归属关系冲突；见coverage_check')
    r.data['baseline_complete_with_sources'] = complete
    r.data['completeness_claim'] = '显式基准编号对账；不等于原设计提取已无遗漏' if complete else '未核：不能宣称全量无漏'
    r.data['package_count'] = len(packages)
    r.data['actual_row_count'] = len(rows)
    return r.finish()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


class IntakeLock:
    """OS-owned advisory lock: a killed process releases it without unsafe age-based deletion."""
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.handle = self.path.open('a+b')
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise ValueError('同一收件工作区仍被运行中的进程占用') from exc
        return self

    def __exit__(self, *args):
        self.handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def intake_signature(project, row):
    return hashlib.sha256(canonical([project, row['涉及Skill与插件脚本'].strip(), row['卡点'].strip()]).encode('utf-8')).hexdigest()


def collect_issues(data, base_dir, state_dir, maint_dir=None):
    """Recover after register-before-ledger interruption by checking the real original issue marker."""
    if data.get('schema') != 'qto-plugin-issue-intake/1.0' or not atom(data.get('project_id')):
        raise ValueError('须用qto-plugin-issue-intake/1.0并声明project_id')
    state = Path(state_dir).resolve()
    state.mkdir(parents=True, exist_ok=True)
    # No inferred official queue. Default is an explicitly created task-isolated rehearsal queue.
    rehearsal = maint_dir is None
    maint = Path(maint_dir).resolve() if maint_dir else state / '隔离维护演练'
    queue = maint / '问题登记' / '待处理'
    if rehearsal:
        queue.mkdir(parents=True, exist_ok=True)
    elif not queue.is_dir():
        raise ValueError('指定维护队列不存在，不自动创建正式位置')
    lock = IntakeLock(state / '收件.lock')
    lock.__enter__()
    try:
        report = Report('collect')
        groups = defaultdict(list)
        for source in data.get('packages', []):
            manifest, path = checked_json({'file': source.get('usage_file'), 'sha256': source.get('usage_sha256')}, base_dir, report, '使用记录来源')
            if not isinstance(manifest, dict) or manifest.get('schema') != 'qto-delivery-manifest/1.0' or manifest.get('package_id') != source.get('package_id'):
                report.add('intake_manifest_identity', '收件来源工作包或schema不符')
                continue
            usage = manifest.get('plugin_usage', {})
            errors = module('validate_minimum_result').check_plugin_usage({'插件使用与临时处理': usage})
            if errors:
                report.add('intake_usage_fields', '；'.join(errors))
                continue
            seen = set()
            for row in usage.get('记录', []):
                if not atom(row.get('row_id')) or row['row_id'] in seen:
                    report.add('intake_row_identity', '收件来源须有唯一row_id')
                    continue
                seen.add(row['row_id'])
                if row.get('登记') != '待协调登记':
                    continue
                sig = intake_signature(data['project_id'], row)
                groups[sig].append({'package_id': source['package_id'], 'row_id': row['row_id'],
                                    'source_file': str(path), 'source_sha256': digest(path),
                                    'version': usage['插件版本'], 'commit': usage['提交'], 'row': row})
        if report.finish()['status'] == 'FAIL':
            return report.data
        ledger_file = state / '插件问题收件.json'
        ledger = load_json(ledger_file) if ledger_file.is_file() else {'schema': 'qto-plugin-issue-receipts/1.0', 'project_id': data['project_id'], 'issues': {}}
        if ledger.get('project_id') != data['project_id']:
            raise ValueError('同一收件工作区不得混用项目')
        for sig, sources in groups.items():
            marker = '<!-- qto-intake-signature:' + sig + ' -->'
            matches = []
            # Include moved original registrations; receipts never substitute a real issue file.
            for file in (maint / '问题登记').rglob('*.md'):
                text = file.read_text(encoding='utf-8-sig')
                if marker in text:
                    if not registration_valid(text) or '## 收件来源' not in text:
                        raise ValueError('恢复登记模板或来源损坏: ' + str(file))
                    matches.append(file)
            entry = ledger['issues'].get(sig)
            for candidate in data.get('existing_registrations', []):
                if candidate.get('signature') != sig:
                    continue
                original = ref_file(candidate, base_dir, report, '显式合并原单')
                if original is None:
                    raise ValueError('显式合并原单缺失或指纹不同，不代登另一份')
                try:
                    original.relative_to(maint / '问题登记')
                except ValueError as exc:
                    raise ValueError('显式合并原单须在指定维护目录的问题登记内') from exc
                if original:
                    text = original.read_text(encoding='utf-8-sig')
                    first_row = sources[0]['row']
                    if (original.suffix.lower() != '.md' or not registration_valid(text) or first_row['涉及Skill与插件脚本'] not in text
                            or first_row['卡点'] not in text or not atom(candidate.get('match_evidence'))
                            or candidate['match_evidence'] not in text):
                        raise ValueError('显式合并原单缺少同一卡点及可定位原文依据')
                    if original not in matches:
                        matches.append(original)
            if entry and entry.get('provenance') == 'explicit_existing_original':
                original = Path(entry['registration_file'])
                if original.is_file() and original not in matches:
                    matches.append(original)
            if len(matches) > 1:
                raise ValueError('同签名存在多个原始登记，须人工核对，不继续代登')
            if entry:
                raw = matches[0].read_bytes() if matches else b''
                count = entry.get('registration_original_bytes', len(raw))
                if not matches or hashlib.sha256(raw[:count]).hexdigest() != entry.get('registration_original_sha256', entry['registration_sha256']):
                    raise ValueError('原始登记不存在或原文已变更，不伪造收件成功；维护追加允许')
            if matches:
                issue = matches[0]
            else:
                first = sources[0]
                row = first['row']
                body = f'''---
登记时间: {datetime.now():%Y-%m-%d %H:%M}
发现宿主: {data.get('host', 'Codex')}（协调收件代登；隔离演练={rehearsal}）
项目与任务: {data['project_id']}
插件版本: {first['version']} / 提交 {first['commit']}
涉及Skill: {row['涉及Skill与插件脚本']}
类型: 其他
影响识图或数量: {row['影响数量']}
阻断程度: 已临时处理
---
## 现象
{row['卡点']}

## 复现材料（路径和哈希，不复制项目原件）
见收件来源的原始使用记录、SHA256及行ID。

## 期望行为
由维护会话核对原始来源后分拣，不自动认定为插件缺陷或修复完成。

## 本次临时处理及其位置
{row['改用方法与位置']}（临时处理，非插件能力）

## 收件来源
{canonical(sources)}
{marker}
'''
                body_file = state / ('待登记_' + sig[:12] + '.md')
                body_file.write_text(body, encoding='utf-8')
                cmd = [sys.executable, str(SHARED / 'register_plugin_issue.py'), '--maint-dir', str(maint),
                       '--host', data.get('host', 'Codex'), '--topic', '协调收件', '--title', row['卡点'][:50], '--body-file', str(body_file)]
                env = dict(os.environ, PYTHONIOENCODING='utf-8')
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='strict', env=env)
                if proc.returncode != 0:
                    raise ValueError('真实登记CLI失败: ' + proc.stdout + proc.stderr)
                receipt = json.loads(proc.stdout)
                issue = Path(receipt['path'])
                if receipt.get('status') != 'REGISTERED' or not issue.is_file() or marker not in issue.read_text(encoding='utf-8'):
                    raise ValueError('真实登记回执与原单不一致')
            previous = (entry or {}).get('sources', [])
            source_map = {canonical([s['package_id'], s['row_id'],
                                     os.path.normcase(str(Path(s['source_file']).resolve())),
                                     s['source_sha256']]): s for s in previous + sources}
            raw = issue.read_bytes()
            text = raw.decode('utf-8-sig')
            maintenance = re.split(r'^##\s+维护回写[^\n]*', text, flags=re.M)
            processing = None
            if len(maintenance) > 1:
                found = re.search(r'^-\s*处理版本\s*[:：]\s*(.+)$', maintenance[-1], re.M)
                processing = found.group(1).strip() if found else None
            ledger['issues'][sig] = {'registration_file': str(issue), 'registration_sha256': digest(issue),
                                     'registration_original_bytes': (entry or {}).get('registration_original_bytes', len(raw)),
                                     'registration_original_sha256': (entry or {}).get('registration_original_sha256', digest(issue)),
                                     'provenance': 'collector_registered' if marker in text else 'explicit_existing_original',
                                     'sources': list(source_map.values()), 'rehearsal': rehearsal,
                                     'processing_version': processing, 'actual_rerun': False, 'rerun_receipt': None,
                                     'affected_packages': sorted({s['package_id'] for s in source_map.values()}),
                                     'notify_skills': sorted({s['row']['涉及Skill与插件脚本'] for s in source_map.values()}),
                                     'required_action': '待维护分拣；没有项目真实复跑凭据'}
            atomic_json(ledger_file, ledger)
        atomic_json(state / '插件回流.json', {'schema': 'qto-plugin-issue-backflow/1.0', 'project_id': data['project_id'], 'issues': list(ledger['issues'].values())})
        report.data.update(receipt_file=str(ledger_file), issue_count=len(ledger['issues']), rehearsal=rehearsal,
                           original_registrations_verified=True)
        return report.finish()
    finally:
        lock.__exit__()


def backflow(data, base_dir):
    r = Report('backflow')
    if data.get('schema') != 'qto-plugin-issue-backflow/1.0':
        r.add('schema', '回流schema不符')
        return r.finish()
    for issue in data.get('issues', []):
        ref_file({'file': issue.get('registration_file'), 'sha256': issue.get('registration_sha256')}, base_dir, r, '回流原单')
        if issue.get('actual_rerun') is True:
            receipt, receipt_path = checked_json(issue.get('rerun_receipt'), base_dir, r, '项目真实复跑凭据')
            if receipt is None:
                r.add('rerun_without_evidence', '没有真实复跑凭据不能标已复跑')
            elif (receipt.get('schema') != 'qto-project-rerun-receipt/1.0' or receipt.get('status') != 'PASS'
                  or receipt.get('project_id') != data.get('project_id')
                  or receipt.get('processing_version') != issue.get('processing_version')
                  or not set(issue.get('affected_packages', [])) <= set(receipt.get('packages', []))
                  or not receipt.get('executions') or not receipt.get('output_artifacts')):
                r.add('rerun_scope_mismatch', '复跑凭据须匹配项目、处理版本、全部受影响包及实际执行/输出')
            else:
                for execution in receipt['executions']:
                    if not execution.get('command') or execution.get('exit_code') != 0:
                        r.add('rerun_execution_failed', '复跑凭据中存在未通过或无命令的执行')
                for artifact in receipt['output_artifacts']:
                    ref_file(artifact, receipt_path.parent, r, '项目真实复跑输出')
        elif issue.get('processing_version'):
            r.add('published_not_rerun', '维护版本不等于受影响项目已复跑', 'UNVERIFIED')
    return r.finish()
