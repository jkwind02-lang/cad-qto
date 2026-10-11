#!/usr/bin/env python3
"""Adapter for future exact HATCH selection.

This module deliberately reuses existing extraction contracts. It does not
implement a second geometry engine. A future extractor integration should pass
validated CADINST selections into the existing HATCH geometry extraction path.
"""
from __future__ import annotations

from surface_selection_contract import SurfaceSelectionError, validate_cadinst_selection


def build_selection_request(index: dict, selection: dict) -> dict:
    """Validate model selection against an existing workbench index.

    This validates structure only. Entity existence and geometry extraction stay
    with the existing CAD traversal/extractor integration.
    """
    scope = validate_cadinst_selection(
        selection,
        source_sha256=index.get("source_sha256", ""),
        layout=index.get("layout", ""),
    )
    return {
        "source_sha256": scope.source_sha256,
        "layout": scope.layout,
        "cadinst_ids": list(scope.cadinst_ids),
        "layer_names": list(scope.layer_names),
        "integration_status": "contract_validated_only",
    }
