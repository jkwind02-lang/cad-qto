import hashlib
import importlib.util
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).parents[1]
SELECTOR = ROOT / "skills" / "qto-surfaces-envelope" / "scripts" / "select_surface_regions.py"
WORKBENCH = ROOT / "skills" / "_qto-shared-evidence" / "scripts" / "qto_recognition_workbench.py"

spec = importlib.util.spec_from_file_location("select_surface_regions", SELECTOR)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
select_hatches = module.select_hatches
SurfaceSelectionError = module.SurfaceSelectionError

spec = importlib.util.spec_from_file_location("qto_recognition_workbench", WORKBENCH)
workbench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workbench)


def _make_fixture(path):
    doc = ezdxf.new()
    msp = doc.modelspace()
    for x in (0, 20):
        hatch = msp.add_hatch(dxfattribs={"layer": "A"})
        hatch.paths.add_polyline_path([(x, 0), (x + 10, 0), (x + 10, 10), (x, 10)], is_closed=True)
    doc.saveas(path)


def _index(path):
    source = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return workbench.build_index(source, layout="Model")


def test_exact_selection_uses_verified_index(tmp_path):
    dxf = tmp_path / "two_hatch.dxf"
    _make_fixture(dxf)
    index = _index(dxf)
    objects = workbench._verified_index(index)
    ids = [k for k, v in objects.items() if v["entity_type"] == "HATCH"]
    assert len(ids) == 2
    result = select_hatches(dxf, {"layout": "Model", "cadinst_ids": [ids[0]]}, index=index, unit_scale=1.0)
    assert len(result["regions"]) == 1
    assert result["regions"][0]["area_m2"] == pytest.approx(100)


def test_unverified_or_wrong_layout_rejected(tmp_path):
    dxf = tmp_path / "two_hatch.dxf"
    _make_fixture(dxf)
    index = _index(dxf)
    objects = workbench._verified_index(index)
    hid = next(k for k, v in objects.items() if v["entity_type"] == "HATCH")
    bad = dict(index)
    bad["index_sha256"] = "0" * 64
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"layout": "Model", "cadinst_ids": [hid]}, index=bad, unit_scale=1.0)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"layout": "Other", "cadinst_ids": [hid]}, index=index, unit_scale=1.0)
