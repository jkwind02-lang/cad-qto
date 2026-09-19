"""Evidence/provenance contract for model working decisions, not professional rules.

Checks evidence identity and declared object scope; cannot verify visual semantics
or authenticate a person's sign-off. Legacy adjudications remain a separate format.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

SCHEMA = "qto-model-recognition-ledger/1.0"


def required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空文本")
    return value.strip()


def texts(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} 必须是非空文本数组")
    result = [required_text(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{label} 有重复值")
    return result


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_working_entries(ledger, path, expected_source):
    if ledger.get("schema_version") != SCHEMA:
        raise ValueError("模型识别记录版本无效")
    for key in ("ledger_id", "ledger_version"):
        required_text(ledger.get(key), key)
    if required_text(ledger.get("source_sha256"), "source_sha256").lower() != expected_source.lower():
        raise ValueError("模型工作识别记录来源哈希不一致")
    if ledger.get("adoption_scope") != "working" or ledger.get("review_status") != "unreviewed":
        raise ValueError("模型识别记录必须为 working / unreviewed，不是人工核定")
    evidence = {}
    raw_evidence = ledger.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ValueError("evidence 必须是数组")
    hashes = {}
    for item in raw_evidence:
        if not isinstance(item, dict):
            raise ValueError("evidence 项必须为对象")
        eid = required_text(item.get("evidence_id"), "evidence_id")
        if eid in evidence:
            raise ValueError(f"证据编号重复: {eid}")
        relative = Path(required_text(item.get("path"), f"{eid}.path"))
        source = (path.parent / relative).resolve()
        expected = required_text(item.get("sha256"), f"{eid}.sha256").lower()
        if source not in hashes:
            hashes[source] = file_sha(source)
        if hashes[source] != expected:
            raise ValueError(f"{eid} 证据文件哈希不一致")
        if required_text(item.get("source_sha256"), f"{eid}.source_sha256").lower() != expected_source.lower():
            raise ValueError(f"{eid} 未绑定本轮来源")
        if item.get("kind") not in {"drawing_view", "cad_property", "project_document", "cross_view"}:
            raise ValueError(f"{eid}.kind 无效")
        required_text(item.get("locator"), f"{eid}.locator")
        required_text(item.get("observation"), f"{eid}.observation")
        evidence[eid] = item
    raw_entries = ledger.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("entries 必须是数组")
    entries = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("entries 项必须为对象")
        entry = dict(raw)
        rid = required_text(entry.get("adjudication_id"), "adjudication_id")
        if rid in entries:
            raise ValueError(f"识别记录编号重复: {rid}")
        if entry.get("status") != "active" or entry.get("decision_origin") != "model":
            raise ValueError(f"{rid} 必须声明 active 和实际 model 识别来源")
        if any(entry.get(key) is not None for key in ("confirmed_by", "confirmed_at", "confirmation_source")):
            raise ValueError(f"{rid} 模型工作识别不得填写人工核定字段")
        for key in ("subject_id", "professional_owner", "source_id", "recognized_by", "recognized_at", "basis", "scope_basis", "identity_basis", "cross_check"):
            required_text(entry.get(key), f"{rid}.{key}")
        if entry.get("decision") not in {"include", "exclude", "container"}:
            raise ValueError(f"{rid}.decision 无效；不能判明的对象留在未决清单")
        entry["object_ids"] = texts(entry.get("object_ids"), f"{rid}.object_ids")
        for eid in texts(entry.get("evidence_ids"), f"{rid}.evidence_ids"):
            if eid not in evidence:
                raise ValueError(f"{rid} 引用了未登记证据 {eid}")
        if not any(evidence[eid]["kind"] in {"drawing_view", "cad_property", "cross_view"} for eid in entry["evidence_ids"]):
            raise ValueError(f"{rid} 缺图面或对象属性识别证据")
        if not isinstance(entry.get("uncertainties"), list) or any(not isinstance(item, str) for item in entry["uncertainties"]):
            raise ValueError(f"{rid}.uncertainties 必须为文本数组，可为空")
        entry.update(adoption_scope="working", review_status="unreviewed")
        entries[rid] = entry
    return ledger, entries


def applies_to(rule, object_id):
    authority = rule.get("authority", {})
    return authority.get("decision_origin") != "model" or object_id in authority["object_ids"]


def recognition_basis(ledger, path):
    if ledger.get("schema_version") != SCHEMA:
        return None
    return {
        "schema_version": SCHEMA,
        "ledger_path": str(path),
        "ledger_sha256": file_sha(path),
        "decision_origin": "model",
        "recognized_by": sorted({item["recognized_by"] for item in ledger["entries"]}),
        "adoption_scope": "working",
        "review_status": "unreviewed",
        "formal_adoption_allowed": False,
        "meaning": "模型依据本轮图纸作工作识别；未取得人工复核或正式采用资格",
    }


def validate_working_basis(value):
    if value is None:
        return
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        raise ValueError("recognition_basis 版本或结构无效")
    if (value.get("decision_origin") != "model" or value.get("adoption_scope") != "working"
            or value.get("review_status") != "unreviewed" or value.get("formal_adoption_allowed") is not False):
        raise ValueError("模型工作识别不得升级为人工复核或正式采用")
    required_text(value.get("ledger_path"), "recognition_basis.ledger_path")
    digest = required_text(value.get("ledger_sha256"), "recognition_basis.ledger_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("recognition_basis.ledger_sha256 必须为64位小写十六进制")
