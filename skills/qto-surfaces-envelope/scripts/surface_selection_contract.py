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


_CADINST_ID = re.compile(r"^CADINST-(.+)/([^/\s]+)$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class SelectionScope:
    source_sha256: str
    layout: str
    cadinst_ids: tuple[str, ...] = ()
    layer_names: tuple[str, ...] = ()


def normalize_layer_names(values):
    """Normalize explicit layer-name selection only.

    This is not --layers. Explicit names must be supplied as list/tuple only;
    strings keep their regex meaning in the future extractor interface.
    """
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise SurfaceSelectionError("explicit layer names require list/tuple")
    result=[]
    for value in values:
        if not isinstance(value, str) or value == "":
            raise SurfaceSelectionError("图层名称必须是非空字符串")
        result.append(value)
    return tuple(dict.fromkeys(result))


def validate_cadinst_selection(selection: dict, *, source_sha256: str, layout: str) -> SelectionScope:
    if not isinstance(selection, dict):
        raise SurfaceSelectionError("选择清单必须为对象")
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise SurfaceSelectionError("调用者必须提供有效DXF SHA256")
    if not isinstance(layout, str) or layout == "":
        raise SurfaceSelectionError("调用者必须提供非空layout")
    if selection.get("source_sha256") != source_sha256:
        raise SurfaceSelectionError("选择清单来源与当前CAD来源不一致")
    if selection.get("layout") != layout:
        raise SurfaceSelectionError("选择清单layout与当前布局不一致")
    ids=selection.get("cadinst_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) or x == "" for x in ids):
        raise SurfaceSelectionError("CADINST选择项必须为字符串列表")
    ids=tuple(dict.fromkeys(ids))
    if any(not _CADINST_ID.fullmatch(x) for x in ids):
        raise SurfaceSelectionError("CADINST ID格式不符合工作台实例契约")
    layer_names=normalize_layer_names(selection.get("layer_names"))
    if not ids and not layer_names:
        raise SurfaceSelectionError("空选择条件；不得解释为零工程量")
    return SelectionScope(source_sha256, layout, ids, layer_names)


def diagnose_empty_selection(*, requested: int, matched: int, failures: int) -> str:
    if not all(isinstance(v, int) and v >= 0 for v in (requested, matched, failures)):
        raise SurfaceSelectionError("选择诊断计数必须为非负整数")
    if requested > 0 and matched == 0:
        return "selection_requested_but_no_match"
    if requested > matched:
        return "selection_partial_match"
    if matched == 0 and failures > 0:
        return "selection_failed_with_unresolved_objects"
    if matched == 0:
        return "no_selection_candidates"
    return "selection_checked"
