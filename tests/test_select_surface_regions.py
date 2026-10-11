import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
for name, path in {
    "surface_selection_contract": ROOT / "skills/qto-surfaces-envelope/scripts/surface_selection_contract.py",
    "select_surface_regions": ROOT / "skills/qto-surfaces-envelope/scripts/select_surface_regions.py",
}.items():
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)

from select_surface_regions import build_selection_request


def test_adapter_keeps_validated_selection_only():
    result = build_selection_request(
        {"source_sha256": "a" * 64, "layout": "Model Space A"},
        {"source_sha256": "a" * 64, "layout": "Model Space A", "cadinst_ids": ["CADINST-Model Space A/path/record"]},
    )
    assert result["integration_status"] == "contract_validated_only"


def test_adapter_does_not_treat_layer_names_as_regex():
    result = build_selection_request(
        {"source_sha256": "a" * 64, "layout": "Model"},
        {"source_sha256": "a" * 64, "layout": "Model", "layer_names": ["A+B"]},
    )
    assert result["layer_names"] == ["A+B"]
