from skills.qto_surfaces_envelope.scripts.surface_selection_contract import (
    SurfaceSelectionError,
    diagnose_empty_selection,
    normalize_layer_names,
    validate_cadinst_selection,
)


def test_explicit_layer_names_do_not_replace_layers_regex():
    assert normalize_layer_names(["FILL-A", "FILL-B"]) == ("FILL-A", "FILL-B")
    try:
        normalize_layer_names("FILL-A,FILL-B")
    except SurfaceSelectionError:
        pass
    else:
        raise AssertionError("explicit layer names must not accept regex text")


def test_cadinst_selection_uses_source_layout_and_contract_shape():
    result = validate_cadinst_selection(
        {
            "source_sha256": "abc",
            "layout": "Model",
            "cadinst_ids": ["CADINST-Model/1:2"],
        },
        source_sha256="abc",
        layout="Model",
    )
    assert result.cadinst_ids == ("CADINST-Model/1:2",)


def test_invalid_cadinst_format_is_rejected():
    try:
        validate_cadinst_selection(
            {"source_sha256": "abc", "layout": "Model", "cadinst_ids": ["fake-id"]},
            source_sha256="abc",
            layout="Model",
        )
    except SurfaceSelectionError:
        pass
    else:
        raise AssertionError("invalid CADINST contract must fail")


def test_empty_selection_is_not_zero_quantity():
    assert diagnose_empty_selection(requested=2, matched=0, failures=0) == "cadinst_selection_no_match"
    assert diagnose_empty_selection(requested=0, matched=0, failures=0) == "no_selection_candidates"
