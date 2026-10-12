"""Resolve explicit JSON evidence locations; never infer drawing semantics.

Text locators remain usable but are disclosed as not machine-checked.  A JSON
Pointer proves a location exists, not that the model chose the correct room,
finish, unit calibration, or measurement boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote


def pointer_tokens(locator: str) -> tuple[str, ...] | None:
    """Return decoded JSON Pointer tokens, or None for a free-text locator.

    Historical manifests may store a JSON path followed by a human note:
    ``/objects/CL-11; raw Model handles ...``.  The path part remains verified
    as JSON Pointer; the suffix is metadata only and never bypasses validation.
    """
    if not isinstance(locator, str):
        raise ValueError('证据定位必须是文本')
    if locator.startswith('#'):
        fragment = locator[1:]
        if re.search(r'%(?![0-9a-fA-F]{2})', fragment):
            raise ValueError('JSON Pointer片段包含非法百分号转义')
        locator = unquote(fragment, encoding='utf-8', errors='strict')
        if locator == '':
            return ()
        if not locator.startswith('/'):
            raise ValueError('JSON Pointer片段必须为#或#/开头')
    elif not locator.startswith('/'):
        return None
    if ';' in locator:
        locator = locator.split(';', 1)[0].rstrip()
    tokens = []
    for token in locator[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('JSON Pointer包含非法~转义')
        tokens.append(token.replace('~1', '/').replace('~0', '~'))
    return tuple(tokens)


def resolve_tokens(value: Any, tokens: tuple[str, ...]) -> Any:
    for token in tokens:
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif (isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', token)
              and len(token) <= len(str(len(value))) and int(token) < len(value)):
            value = value[int(token)]
        else:
            raise ValueError('JSON证据定位在所引用来源中不存在')
    return value


def audit_evidence_locations(manifest: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Audit locations after the native calculator has validated the manifest.

    Explicit #/fragment or /pointer locators on JSON sources are resolved against
    their own registered, SHA-verified bytes.  A frame binding may narrow an
    evidence locator to a descendant, but cannot silently use a sibling object.
    This does not require free-text CAD/PDF locators to be JSON Pointers.
    """
    sources = {row['source_id']: row for row in manifest['sources']}
    checked = []
    unchecked = []
    errors = []
    parsed = {}
    resolved = {}
    for row in manifest['evidence']:
        eid = row['evidence_id']
        sid = row['source_id']
        locator = row['locator'].strip()
        source = sources[sid]
        if source['file_role'] != 'json':
            if locator.startswith('#/') or locator.lower().startswith('#%2f'):
                errors.append(f'{eid}来源定位核验失败({sid}, {locator}): JSON Pointer片段引用了非JSON来源')
            else:
                unchecked.append({'evidence_id': eid, 'reason': '非JSON来源的文字定位未作机器定位核验'})
            continue
        try:
            tokens = pointer_tokens(locator)
            if tokens is None:
                unchecked.append({'evidence_id': eid, 'reason': '自由文本定位未作机器定位核验'})
                continue
            if sid not in parsed:
                path = Path(source['path']).expanduser()
                path = (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != source['sha256'].lower():
                    raise ValueError('证据来源SHA256不一致')
                parsed[sid] = json.loads(raw.decode('utf-8-sig'))
            value = resolve_tokens(parsed[sid], tokens)
            resolved[eid] = tokens
            checked.append({'evidence_id': eid, 'source_id': sid, 'locator': locator,
                            'resolved_type': type(value).__name__})
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append(f'{eid}来源定位核验失败({sid}, {locator}): {exc}')
    for item in manifest['items']:
        binding = item.get('frame_binding')
        if not isinstance(binding, dict):
            continue
        for ref_key, pointer_key in (('frame_evidence_ref', 'source_pointer'),
                                     ('edge_evidence_ref', 'edge_source_pointer')):
            eid = binding.get(ref_key)
            if eid not in resolved:
                continue
            try:
                selected = pointer_tokens(binding[pointer_key])
                parent = resolved[eid]
                if selected is None or selected[:len(parent)] != parent:
                    raise ValueError('实际框架/源边不在该证据定位范围内，不能用另一对象的定位支撑')
            except (KeyError, ValueError, UnicodeError) as exc:
                errors.append(f"{item['item_id']}的{eid}与{pointer_key}不一致: {exc}")
    return {'checked_json_locator_count': len(checked), 'checked': checked,
            'unchecked_text_locators': unchecked, 'errors': errors,
            'business_correctness_proven': False,
            'note': '仅检查显式来源定位及框架引用范围，不证明房间归属、单位推定或完整工程量。'}
