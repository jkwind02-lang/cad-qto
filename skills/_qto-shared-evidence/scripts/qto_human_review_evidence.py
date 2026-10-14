"""Verify local human quantity-review evidence (qto-human-review-evidence/1.0).

A review counts only when a user-made record binds the same project, calculation node,
calculation fingerprint, value and unit, and points back to the saved user annotation
that explicitly confirmed this quantity. Names alone never prove a human review, and
assistant receipts such as "已应用并复算" are not quantity confirmations.
File hashes prove content integrity only; this is a traceable local contract, not
identity authentication or official adoption.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from qto_actor_identity import is_model_identity

EVIDENCE_SCHEMA = 'qto-human-review-evidence/1.0'
FEEDBACK_SCHEMA = 'qto-review-feedback/2.0'
DECISION = 'confirm_quantity'
FEEDBACK_DECISION = 'quantity_confirmation'
_SKIP = object()


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _hex64(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value.lower())


def _aware_time(value) -> bool:
    if not _text(value):
        return False
    try:
        return datetime.fromisoformat(value.strip().replace('Z', '+00:00')).tzinfo is not None
    except ValueError:
        return False


def _same_number(left, right) -> bool:
    numeric = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return numeric(left) and numeric(right) and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)


def _resolve(path_text, base_dir):
    path = Path(path_text)
    if path.is_absolute():
        return path
    if base_dir is None:
        return None
    return Path(base_dir) / path


def _load_hashed(ref, base_dir, label, issues):
    """Resolve and hash-check a referenced file; returns (path, bytes) or (None, None)."""
    path = _resolve(ref['path'], base_dir)
    if path is None:
        issues.append(f'{label}路径为相对路径，但没有可用的所在目录，不按当前目录猜测')
        return None, None
    if not path.is_file():
        issues.append(f'{label}文件不存在：{path}')
        return None, None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref['sha256'].lower():
        issues.append(f'{label}哈希与登记不一致，文件可能已被修改：{path}')
        return None, None
    return path, data


def approval_field_issues(approval) -> list[str]:
    """Field checks for a node's approval block, before reading any evidence file."""
    if not isinstance(approval, dict):
        return ['approval 必须是对象']
    issues = []
    if 'actor' in approval and 'evidence' not in approval:
        issues.append('旧格式 approval.actor 不能作为人工数量确认；需 reviewer、reviewer_type、reviewed_at 及 evidence 凭据')
    if not _hex64(approval.get('fingerprint')):
        issues.append('approval.fingerprint 必须是完整计算指纹')
    reviewer = approval.get('reviewer')
    if not _text(reviewer):
        issues.append('approval.reviewer 必须是非空姓名')
    elif is_model_identity(reviewer):
        issues.append(f'approval.reviewer 是模型或助手名称：{reviewer}')
    if approval.get('reviewer_type') != 'human':
        issues.append('approval.reviewer_type 必须为 human')
    if not _aware_time(approval.get('reviewed_at')):
        issues.append('approval.reviewed_at 必须是带时区的 ISO 8601 时间')
    issues.extend(evidence_ref_issues(approval.get('evidence'), 'approval.evidence'))
    return issues


def evidence_ref_issues(ref, label='evidence') -> list[str]:
    if not isinstance(ref, dict):
        return [f'{label} 缺失；需人工复核凭据的 path、sha256、record_id']
    issues = []
    if not _text(ref.get('path')):
        issues.append(f'{label}.path 必须填写')
    if not _hex64(ref.get('sha256')):
        issues.append(f'{label}.sha256 必须是 64 位十六进制哈希')
    if not _text(ref.get('record_id')):
        issues.append(f'{label}.record_id 必须填写')
    return issues


def _feedback_issues(source, record, project_id, base_dir) -> list[str]:
    issues = []
    if not isinstance(source, dict):
        return ['凭据记录缺少 source_feedback（原用户批注文件绑定）']
    for key in ('path', 'annotation_key'):
        if not _text(source.get(key)):
            issues.append(f'source_feedback.{key} 必须填写')
    if not _hex64(source.get('sha256')):
        issues.append('source_feedback.sha256 必须是 64 位十六进制哈希')
    if not _aware_time(source.get('saved_at')):
        issues.append('source_feedback.saved_at 必须是带时区的 ISO 8601 时间')
    if issues:
        return issues
    path, _ = _load_hashed(source, base_dir, '原批注', issues)
    if path is None:
        return issues
    try:
        from qto_review_contract import extract_json_file
        feedback = extract_json_file(path)
    except (ValueError, OSError) as exc:
        return [f'原批注文件无法读取：{exc}']
    if not isinstance(feedback, dict) or feedback.get('schema') != FEEDBACK_SCHEMA:
        return [f'原批注文件不是 {FEEDBACK_SCHEMA}']
    if feedback.get('project_id') != project_id:
        issues.append('原批注属于其他项目')
    annotation = (feedback.get('annotations') or {}).get(source['annotation_key'])
    if not isinstance(annotation, dict):
        return issues + [f"原批注中没有已保存批注 {source['annotation_key']}"]
    if annotation.get('decision_type') != FEEDBACK_DECISION:
        issues.append('原批注不是明确的数量确认操作；普通意见、方法意见或助手回执不能当作数量确认')
    if annotation.get('reviewer') != record.get('reviewer'):
        issues.append('原批注填写的复核者与凭据记录不一致')
    wanted = {k: record.get(k) for k in ('node_id', 'calculation_fingerprint', 'review_object_key', 'result_fingerprint', 'unit')}
    matches = [item for item in annotation.get('quantity_confirmations') or []
               if isinstance(item, dict) and all(item.get(k) == v for k, v in wanted.items())
               and _same_number(item.get('quantity'), record.get('quantity'))]
    if not matches:
        issues.append('原批注的 quantity_confirmations 中没有与本节点、指纹、对象、数值和单位一致的确认项')
    return issues


def verify_evidence(ref, *, base_dir, project_id, node_id, fingerprint, unit, value=_SKIP,
                    reviewer=None, reviewed_at=None):
    """Return (verified reference, issues). value=_SKIP skips the quantity comparison (stale fingerprint)."""
    issues = evidence_ref_issues(ref)
    if issues:
        return None, issues
    path, data = _load_hashed(ref, base_dir, '人工复核凭据', issues)
    if path is None:
        return None, issues
    try:
        document = json.loads(data.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f'人工复核凭据不是合法 JSON：{exc}']
    if not isinstance(document, dict) or document.get('schema') != EVIDENCE_SCHEMA:
        return None, [f'人工复核凭据不是 {EVIDENCE_SCHEMA}']
    if document.get('project_id') != project_id:
        issues.append('人工复核凭据属于其他项目')
    records = [r for r in document.get('records') or [] if isinstance(r, dict) and r.get('record_id') == ref['record_id']]
    if len(records) != 1:
        return None, issues + [f"人工复核凭据中 record_id={ref['record_id']} 应恰好一条，实际 {len(records)} 条"]
    record = records[0]
    if not _text(record.get('reviewer')):
        issues.append('凭据记录的 reviewer 必须是非空姓名')
    elif is_model_identity(record['reviewer']):
        issues.append(f"凭据记录的 reviewer 是模型或助手名称：{record['reviewer']}")
    if record.get('reviewer_type') != 'human':
        issues.append('凭据记录的 reviewer_type 必须为 human')
    if not _aware_time(record.get('reviewed_at')):
        issues.append('凭据记录的 reviewed_at 必须是带时区的 ISO 8601 时间')
    if record.get('decision') != DECISION:
        issues.append(f'凭据记录的 decision 必须为 {DECISION}')
    if record.get('node_id') != node_id:
        issues.append('凭据记录指向其他计算节点')
    if record.get('calculation_fingerprint') != fingerprint:
        issues.append('凭据记录的计算指纹与审批所述指纹不一致')
    if not _text(record.get('review_object_key')) or not _text(record.get('result_fingerprint')):
        issues.append('凭据记录缺少 review_object_key 或对象 result_fingerprint')
    if record.get('unit') != unit:
        issues.append('凭据记录的单位与节点单位不一致')
    if value is not _SKIP and not _same_number(record.get('quantity'), value):
        issues.append('凭据记录确认的数量与节点当前值不一致')
    if reviewer is not None and record.get('reviewer') != reviewer:
        issues.append('审批填写的复核者与凭据记录不一致')
    if reviewed_at is not None and record.get('reviewed_at') != reviewed_at:
        issues.append('审批填写的复核时间与凭据记录不一致')
    issues.extend(_feedback_issues(record.get('source_feedback'), record, project_id, path.parent))
    if issues:
        return None, issues
    return {'path': str(path.resolve()), 'sha256': ref['sha256'].lower(), 'record_id': ref['record_id'],
            'reviewer': record['reviewer'], 'reviewed_at': record['reviewed_at']}, []
