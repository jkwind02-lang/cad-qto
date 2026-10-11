import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
for name, path in {
    "select_surface_regions": ROOT / "skills/qto-surfaces-envelope/scripts/select_surface_regions.py",
}.items():
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)

from select_surface_regions import SurfaceSelectionError


def test_selector_requires_workbench_object_id_contract():
    assert SurfaceSelectionError
