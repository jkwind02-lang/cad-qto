#!/usr/bin/env python3
"""Contract helpers for future CADINST-aware surface selection.

This module does not extract geometry. It validates model-provided selection
intent before extractor integration. Existing --layers regex input remains a
separate concern and is never parsed as a list.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


class SurfaceSelectionError(ValueError):
    pass


_CADINST_ID = re.compile(r"^CADINST-[^/\s]+/.+$")


@dataclass(frozen=True)
class SelectionScope:
    source_sha256: str
    layout: str
    cadinst_ids: tuple[str, ...] = ()
    layer_names: tuple[str, ...] = ()


def normalize_layer_names(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Normalize explicit layer-name selection only.

    This is intentionally not used for --layers. A comma, pipe, or other regex
    expression passed to --layers must keep its original regex meaning.
    """
    if values is None:
        return ()
    if isinstance(values, str):
        raise SurfaceSelectionError("explicit layer names require a list; regex belongs to --layers")
    result=[]
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise SurfaceSelectionError("图层名称不能为空")
        result.append(value.strip())
    return tuple(dict.fromkeys(result))


def validate_cadinst_selection(selection: dict, *, source_sha256: str, layout: str) -> SelectionScope:
    if not isinstance(selection, dict):
        raise SurfaceSelectionError("选择清单必须为对象")
    if not source_sha256 or selection.get("source_sha256") != source_sha256:
        raise SurfaceSelectionError("选择清单来源与当前CAD来源不一致")
    if not layout or selection.get("layout") != layout:
        raise SurfaceSelectionError("选择清单layout与当前布局不一致")
    ids=selection.get("cadinst_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x.strip() for x in ids):
        raise SurfaceSelectionError("CADINST选择项必须为非空字符串列表")
    ids=tuple(dict.fromkeys(x.strip() for x in ids))
    if any(not _CADINST_ID.match(x) for x in ids):
        raise SurfaceSelectionError("CADINST ID格式不符合工作台实例契约")
    layer_names=normalize_layer_names(selection.get("layer_names"))
    if not ids and not layer_names:
        raise SurfaceSelectionError("空选择条件；不得解释为零工程量")
    return SelectionScope(source_sha256, layout, ids, layer_names)


def diagnose_empty_selection(*, requested: int, matched: int, failures: int) -> str:
    if requested and matched == 0:
        return "cadinst_selection_no_match"
    if not requested and matched == 0 and failures == 0:
        return "no_selection_candidates"
    return "selection_checked"
