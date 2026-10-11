import hashlib
import importlib.util
from pathlib import Path
import sys

import ezdxf
import pytest

ROOT = Path(__file__).parents[1]
for name, path in {
    "extract_surface_regions": ROOT / "skills/qto-surfaces-envelope/scripts/extract_surface_regions.py",
    "select_surface_regions": ROOT / "skills/qto-surfaces-envelope/scripts/select_surface_regions.py",
}.items():
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)

from extract_surface_regions import walk
from select_surface_regions import select_hatches, SurfaceSelectionError


def _make_hatch_doc(path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for x in (0, 20):
        hatch = msp.add_hatch("SOLID")
        hatch.paths.add_polyline_path([(x, 0), (x + 10, 0), (x + 10, 10), (x, 10)], is_closed=True)
    doc.saveas(path)
    return path


def _workbench_style_ids(path):
    doc = ezdxf.readfile(path)
    ids = []
    for identity, entity, failure, _ in walk(doc.modelspace()):
        if failure or entity.dxftype() != "HATCH":
            continue
        ids.append("CADINST-" + "/".join(identity))
    return ids


def test_select_one_hatch_from_real_dxf(tmp_path):
    dxf = _make_hatch_doc(tmp_path / "two_hatch.dxf")
    ids = _workbench_style_ids(dxf)
    result = select_hatches(
        dxf,
        {"source_sha256": hashlib.sha256(dxf.read_bytes()).hexdigest(), "layout": "Model", "cadinst_ids": [ids[0]]},
        layout="Model",
        unit_scale=1.0,
    )
    assert len(result["regions"]) == 1
    assert result["regions"][0]["area_m2"] == pytest.approx(100)


def test_wrong_source_and_layout_rejected(tmp_path):
    dxf = _make_hatch_doc(tmp_path / "two_hatch.dxf")
    ids = _workbench_style_ids(dxf)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"source_sha256": "0" * 64, "layout": "Model", "cadinst_ids": [ids[0]]}, layout="Model", unit_scale=1)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"source_sha256": hashlib.sha256(dxf.read_bytes()).hexdigest(), "layout": "Paper", "cadinst_ids": [ids[0]]}, layout="Model", unit_scale=1)
