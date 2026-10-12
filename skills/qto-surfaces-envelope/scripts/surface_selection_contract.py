#!/usr/bin/env python3
"""Contract helpers for CADINST-aware surface selection."""
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
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise SurfaceSelectionError("explicit layer names require list/tuple")
    if any(not isinstance(v, str) or v == "" for v in values):
        raise SurfaceSelectionError("图层名称必须是非空字符串")
    return tuple(dict.fromkeys(values))


def validate_cadinst_selection(selection: dict, *, source_sha256: str, layout: str) -> SelectionScope:
    if not isinstance(selection, dict):
        raise SurfaceSelectionError("选择清单必须为对象")
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise SurfaceSelectionError("调用者必须提供有效DXF SHA256")
    if not isinstance(layout, str) or layout == "":
        raise SurfaceSelectionError("调用者必须提供非空layout")
    if selection.get("source_sha256") != source_sha256 or selection.get("layout") != layout:
        raise SurfaceSelectionError("选择清单来源或布局不一致")
    ids = selection.get("cadinst_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) or x == "" for x in ids):
        raise SurfaceSelectionError("CADINST选择项必须为字符串列表")
    ids = tuple(dict.fromkeys(ids))
    if any(not _CADINST_ID.fullmatch(x) for x in ids):
        raise SurfaceSelectionError("CADINST ID格式不符合工作台实例契约")
    names = normalize_layer_names(selection.get("layer_names"))
    if not ids and not names:
        raise SurfaceSelectionError("空选择条件；不得解释为零工程量")
    return SelectionScope(source_sha256, layout, ids, names)


def diagnose_empty_selection(*, requested: int, matched: int, failures: int) -> str:
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (requested, matched, failures)):
        raise SurfaceSelectionError("选择诊断计数必须为非负整数")
    if failures > 0:
        return "selection_partial_or_failed" if matched > 0 else "selection_failed_with_unresolved_objects"
    if requested > 0 and matched == 0:
        return "selection_requested_but_no_match"
    if requested > matched:
        return "selection_partial_match"
    if matched == 0:
        return "no_selection_candidates"
    return "selection_successful"
