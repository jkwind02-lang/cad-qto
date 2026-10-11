import hashlib
import importlib.util
from pathlib import Path
import sys

import ezdxf
import pytest

ROOT = Path(__file__).parents[1]
MODULE = ROOT / "skills/qto-surfaces-envelope/scripts/select_surface_regions.py"
spec = importlib.util.spec_from_file_location("select_surface_regions", MODULE)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

select_hatches = mod.select_hatches
SurfaceSelectionError = mod.SurfaceSelectionError


def _make_fixture(path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "A"})
    hatch.paths.add_polyline_path([(0,0),(10,0),(10,10),(0,10)], is_closed=True)
    doc.saveas(path)


def test_selector_rejects_unverified_local_ids(tmp_path):
    dxf = tmp_path / "one_hatch.dxf"
    _make_fixture(dxf)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(
            dxf,
            {"source_sha256": hashlib.sha256(dxf.read_bytes()).hexdigest(), "layout": "Model", "cadinst_ids": ["CADINST-invalid/path"]},
            index={"schema_version": "invalid", "kind": "index"},
            unit="m",
            unit_evidence="explicit_m",
        )
