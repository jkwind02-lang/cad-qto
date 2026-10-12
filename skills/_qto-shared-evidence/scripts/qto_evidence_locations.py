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


def _pointer_tokens_strict(locator: str) -> tuple[str, ...]:
    tokens=[]
    for token in locator[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('JSON Pointer包含非法~转义')
        tokens.append(token.replace('~1','/').replace('~0','~'))
    return tuple(tokens)


def pointer_tokens(locator: str) -> tuple[str, ...] | None:
    if not isinstance(locator, str):
        raise ValueError('证据定位必须是文本')
    if locator.startswith('#'):
        fragment=locator[1:]
        if re.search(r'%(?![0-9a-fA-F]{2})', fragment):
            raise ValueError('JSON Pointer片段包含非法百分号转义')
        locator=unquote(fragment, encoding='utf-8', errors='strict')
        if locator and not locator.startswith('/'):
            raise ValueError('JSON Pointer片段必须为#或#/开头')
        if locator=='':
            return ()
    elif not locator.startswith('/'):
        return None
    return _pointer_tokens_strict(locator)


def resolve_tokens(value: Any, tokens: tuple[str, ...]) -> Any:
    for token in tokens:
        if isinstance(value,dict) and token in value:
            value=value[token]
        elif isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]*',token) and int(token)<len(value):
            value=value[int(token)]
        else:
            raise ValueError('JSON证据定位在所引用来源中不存在')
    return value


def audit_evidence_locations(manifest: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    sources={row['source_id']:row for row in manifest['sources']}
    errors=[]; checked=[]; unchecked=[]; parsed={}; resolved={}
    for row in manifest['evidence']:
        eid=row['evidence_id']; sid=row['source_id']; locator=row['locator'].strip(); source=sources[sid]
        try:
            if source['file_role']!='json':
                if locator.startswith('#'): raise ValueError('非JSON来源不能使用JSON Pointer片段')
                unchecked.append({'evidence_id':eid,'reason':'非JSON文字定位未机器核验'}); continue
            tokens=pointer_tokens(locator)
            if tokens is None and ';' in locator:
                legacy=locator.split(';',1)[0].rstrip()
                tokens=pointer_tokens(legacy)
            if tokens is None:
                unchecked.append({'evidence_id':eid,'reason':'自由文本定位未作机器定位核验'}); continue
            if sid not in parsed:
                p=(base_dir/Path(source['path'])).resolve(); raw=p.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=source['sha256'].lower(): raise ValueError('证据来源SHA256不一致')
                parsed[sid]=json.loads(raw.decode('utf-8-sig'))
            resolve_tokens(parsed[sid],tokens); resolved[eid]=tokens; checked.append(eid)
        except Exception as exc:
            errors.append(f'{eid}: {exc}')
    return {'checked_json_locator_count':len(checked),'checked':checked,'unchecked_text_locators':unchecked,'errors':errors,'business_correctness_proven':False}
