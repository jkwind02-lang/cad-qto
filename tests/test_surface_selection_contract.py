from skills.qto_surfaces_envelope.scripts.surface_selection_contract import (
    SurfaceSelectionError,
    diagnose_empty_selection,
    normalize_layers,
    validate_cadinst_selection,
)


def test_layers_keep_regex_semantics():
    assert normalize_layers(["FILL-A", "FILL-B"]) == ("FILL-A", "FILL-B")
    try:
        normalize_layers("FILL-A,FILL-B")
    except SurfaceSelectionError:
        pass
    else:
        raise AssertionError("must not silently convert regex input")


def test_cadinst_selection_requires_source_and_layout():
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


def test_empty_selection_is_not_zero_quantity():
    assert diagnose_empty_selection(requested=2, matched=0, failures=0) == "cadinst_selection_no_match"
    assert diagnose_empty_selection(requested=0, matched=0, failures=0) == "no_selection_candidates"
