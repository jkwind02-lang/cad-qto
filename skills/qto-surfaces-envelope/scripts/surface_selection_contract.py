#!/usr/bin/env python3
"""Small contract helpers for future CADINST-aware surface selection.

This module does not perform extraction. It validates model-provided selection
intent before a future extractor integration. Existing --layers regex behaviour
remains unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass


class SurfaceSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class SelectionScope:
    source_sha256: str
    layout: str
    cadinst_ids: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()


def normalize_layers(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raise SurfaceSelectionError("layers必须保持原有正则字符串语义，不接受隐式列表转换")
    result=[]
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise SurfaceSelectionError("图层名称不能为空")
        result.append(value.strip())
    return tuple(dict.fromkeys(result))


def validate_cadinst_selection(selection: dict, *, source_sha256: str, layout: str) -> SelectionScope:
    if not isinstance(selection, dict):
        raise SurfaceSelectionError("选择清单必须为对象")
    if selection.get("source_sha256") != source_sha256:
        raise SurfaceSelectionError("选择清单来源与当前CAD来源不一致")
    if selection.get("layout") != layout:
        raise SurfaceSelectionError("选择清单layout与当前布局不一致")
    ids=selection.get("cadinst_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x.strip() for x in ids):
        raise SurfaceSelectionError("CADINST选择项必须为非空字符串列表")
    ids=tuple(dict.fromkeys(x.strip() for x in ids))
    layers=normalize_layers(selection.get("layers"))
    if not ids and not layers:
        raise SurfaceSelectionError("空选择条件；不得解释为零工程量")
    return SelectionScope(source_sha256, layout, ids, layers)


def diagnose_empty_selection(*, requested: int, matched: int, failures: int) -> str:
    if requested and matched == 0:
        return "cadinst_selection_no_match"
    if not requested and matched == 0 and failures == 0:
        return "no_selection_candidates"
    return "selection_checked"
