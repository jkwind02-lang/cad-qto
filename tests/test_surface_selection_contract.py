import importlib.util
from pathlib import Path
import sys
import pytest


MODULE = Path(__file__).parents[1] / "skills" / "qto-surfaces-envelope" / "scripts" / "surface_selection_contract.py"
spec = importlib.util.spec_from_file_location("surface_selection_contract", MODULE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

SurfaceSelectionError = module.SurfaceSelectionError
diagnose_empty_selection = module.diagnose_empty_selection
normalize_layer_names = module.normalize_layer_names
validate_cadinst_selection = module.validate_cadinst_selection


VALID_SHA = "a" * 64


def test_explicit_layer_names_are_separate_from_regex():
    assert normalize_layer_names(["FILL-A", "FILL,B"]) == ("FILL-A", "FILL,B")
    for value in ("FILL-A,FILL-B", {"FILL-A"}):
        with pytest.raises(SurfaceSelectionError):
            normalize_layer_names(value)


def test_cadinst_selection_requires_real_source_and_layout_binding():
    result = validate_cadinst_selection(
        {
            "source_sha256": VALID_SHA,
            "layout": "Model Space A",
            "cadinst_ids": ["CADINST-Model Space A/path/record"],
        },
        source_sha256=VALID_SHA,
        layout="Model Space A",
    )
    assert result.layout == "Model Space A"


@pytest.mark.parametrize("selection", [
    {"source_sha256": "abc", "layout": "", "cadinst_ids": ["CADINST-x/y"]},
    {"source_sha256": "abc", "layout": "Model", "cadinst_ids": ["CADINST-x/y"]},
])
def test_invalid_source_is_not_strong_binding(selection):
    with pytest.raises(SurfaceSelectionError):
        validate_cadinst_selection(selection, source_sha256=VALID_SHA, layout="Model")


def test_empty_and_partial_selection_diagnostics():
    assert diagnose_empty_selection(requested=2, matched=0, failures=0) == "selection_requested_but_no_match"
    assert diagnose_empty_selection(requested=3, matched=1, failures=0) == "selection_partial_match"
    assert diagnose_empty_selection(requested=0, matched=0, failures=1) == "selection_failed_with_unresolved_objects"


def test_invalid_counters_fail():
    with pytest.raises(SurfaceSelectionError):
        diagnose_empty_selection(requested=-1, matched=0, failures=0)
