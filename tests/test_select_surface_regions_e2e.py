import hashlib
import importlib.util
from pathlib import Path

import ezdxf
import pytest


MODULE = Path(__file__).parents[1] / "skills" / "qto-surfaces-envelope" / "scripts" / "select_surface_regions.py"
spec = importlib.util.spec_from_file_location("select_surface_regions", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
select_hatches = module.select_hatches
SurfaceSelectionError = module.SurfaceSelectionError


def _make_fixture(path):
    doc = ezdxf.new()
    msp = doc.modelspace()
    for layer, x in (("A", 0), ("B", 20)):
        hatch = msp.add_hatch(dxfattribs={"layer": layer})
        hatch.paths.add_polyline_path(
            [(x, 0), (x + 10, 0), (x + 10, 10), (x, 10)],
            is_closed=True,
        )
    doc.saveas(path)


def _ids_from_fixture(path):
    # Use the same extractor walk used by the adapter; the test does not invent IDs.
    spec = importlib.util.spec_from_file_location(
        "extract_surface_regions", Path(__file__).parents[1] / "skills" / "qto-surfaces-envelope" / "scripts" / "extract_surface_regions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    doc = module.read_dxf_with_diagnostics(path)[0]
    return ["CADINST-" + "/".join(identity) for identity, entity, failure, _ in module.walk(doc.modelspace()) if not failure and entity.dxftype() == "HATCH"]


def test_exact_selection_selects_one_hatch_area(tmp_path):
    dxf = tmp_path / "two_hatch.dxf"
    _make_fixture(dxf)
    ids = _ids_from_fixture(dxf)
    assert len(ids) == 2
    result = select_hatches(
        dxf,
        {"source_sha256": hashlib.sha256(dxf.read_bytes()).hexdigest(), "layout": "Model", "cadinst_ids": [ids[0]]},
        layout="Model",
        unit_scale=1.0,
    )
    assert len(result["regions"]) == 1
    assert result["regions"][0]["area_m2"] == pytest.approx(100)


def test_wrong_source_and_layout_rejected(tmp_path):
    dxf = tmp_path / "two_hatch.dxf"
    _make_fixture(dxf)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"source_sha256": "0" * 64, "layout": "Model", "cadinst_ids": []}, layout="Model", unit_scale=1.0)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"source_sha256": hashlib.sha256(dxf.read_bytes()).hexdigest(), "layout": "Other", "cadinst_ids": []}, layout="Model", unit_scale=1.0)
