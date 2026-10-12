"""Resolve explicit evidence locations against registered source bytes.

A location check proves existence and frame-reference containment, not drawing
semantics or professional acceptance. CAD/PDF text and legacy annotations are
disclosed separately from the JSON locations that have actually been checked.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote


def _decode_pointer(locator: str) -> str | None:
    if not isinstance(locator, str):
        raise ValueError('证据定位必须是文本')
    if locator.startswith('#'):
        fragment = locator[1:]
        if re.search(r'%(?![0-9a-fA-F]{2})', fragment):
            raise ValueError('JSON Pointer片段包含非法百分号转义')
        pointer = unquote(fragment, encoding='utf-8', errors='strict')
        if pointer and not pointer.startswith('/'):
            raise ValueError('JSON Pointer片段必须为#或#/开头')
        return pointer
    return locator if locator.startswith('/') else None


def _pointer_tokens_strict(pointer: str) -> tuple[str, ...]:
    if pointer == '':
        return ()
    if not pointer.startswith('/'):
        raise ValueError('JSON Pointer必须以/开头')
    tokens = []
    for token in pointer[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('JSON Pointer包含非法~转义')
        tokens.append(token.replace('~1', '/').replace('~0', '~'))
    return tuple(tokens)


def _legacy_prefix(pointer: str) -> str | None:
    delimiter = re.search(r';[ \t]+\S', pointer)
    return pointer[:delimiter.start()].rstrip() if delimiter else None


def pointer_tokens(locator: str) -> tuple[str, ...] | None:
    """Decode a pointer; raw historical paths may have a ``; note`` suffix.

    URI fragments are always strict. Production auditing resolves the entire
    raw key first, so a real key containing ``; note`` is not truncated.
    """
    pointer = _decode_pointer(locator)
    if pointer is None:
        return None
    legacy = None if locator.startswith('#') else _legacy_prefix(pointer)
    return _pointer_tokens_strict(legacy if legacy is not None else pointer)


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


def _resolve_locator(value: Any, locator: str, *, allow_legacy: bool = True
                     ) -> tuple[tuple[str, ...], Any, str | None]:
    pointer = _decode_pointer(locator)
    if pointer is None:
        raise ValueError('所选位置必须是JSON Pointer')
    # Strict syntax is checked first. Compatibility cannot hide malformed
    # escapes, and an existing literal key always takes precedence.
    tokens = _pointer_tokens_strict(pointer)
    try:
        return tokens, resolve_tokens(value, tokens), None
    except ValueError:
        legacy = None if not allow_legacy or locator.startswith('#') else _legacy_prefix(pointer)
        if legacy is None:
            raise
        tokens = _pointer_tokens_strict(legacy)
        return tokens, resolve_tokens(value, tokens), legacy


def _reject_constant(value: str) -> None:
    raise ValueError(f'非标准JSON数值: {value}')


def audit_evidence_locations(manifest: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Audit a manifest already structurally checked by the native calculator."""
    sources = {row['source_id']: row for row in manifest['sources']}
    evidence_sources = {row['evidence_id']: row['source_id'] for row in manifest['evidence']}
    errors = []
    checked = []
    unchecked = []
    annotations = []
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
            pointer = _decode_pointer(locator)
            if pointer is None:
                unchecked.append({'evidence_id': eid, 'reason': '自由文本定位未作机器定位核验'})
                continue
            if sid not in parsed:
                path = Path(source['path']).expanduser()
                path = (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != source['sha256'].lower():
                    raise ValueError('证据来源SHA256不一致')
                parsed[sid] = json.loads(raw.decode('utf-8-sig'), parse_constant=_reject_constant)
            tokens, value, legacy = _resolve_locator(parsed[sid], locator)
            resolved[eid] = tokens
            checked.append({'evidence_id': eid, 'source_id': sid, 'locator': locator,
                            'verified_locator': legacy if legacy is not None else locator,
                            'resolved_type': type(value).__name__})
            if legacy is not None:
                annotations.append({'evidence_id': eid, 'text': locator[len(legacy):],
                                    'reason': '历史路径附注未作机器定位核验'})
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
                sid = evidence_sources[eid]
                selected, _, _ = _resolve_locator(parsed[sid], binding[pointer_key], allow_legacy=False)
                parent = resolved[eid]
                if selected[:len(parent)] != parent:
                    raise ValueError('实际框架/源边不在该证据定位范围内，不能用另一对象的定位支撑')
            except (KeyError, ValueError, UnicodeError) as exc:
                errors.append(f"{item['item_id']}的{eid}与{pointer_key}不一致: {exc}")
    return {'checked_json_locator_count': len(checked), 'checked': checked,
            'unchecked_text_locators': unchecked, 'unchecked_annotations': annotations,
            'errors': errors, 'business_correctness_proven': False,
            'note': '仅检查显式来源定位及框架引用范围，不证明房间归属、单位推定或完整工程量。'}
