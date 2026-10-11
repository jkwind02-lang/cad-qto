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


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hatch(container, size=10):
    h = container.add_hatch()
    h.paths.add_polyline_path([(0, 0), (size, 0), (size, size), (0, size)], is_closed=True)
    return h


def _index(path, layout="Model"):
    return workbench.build_index({"path": str(path), "sha256": _sha(path)}, layout=layout)


def _select(path, index, oid, unit_scale=1.0):
    return select_hatches(path, {"source_sha256": _sha(path), "layout": index["layout"], "cadinst_ids": [oid]}, index=index, unit_scale=unit_scale)


def test_exact_selection_uses_verified_index(tmp_path):
    dxf = tmp_path / "two_hatch.dxf"
    doc = ezdxf.new()
    _hatch(doc.modelspace())
    _hatch(doc.modelspace(), 20)
    doc.saveas(dxf)
    index = _index(dxf)
    ids = [k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH"]
    assert len(ids) == 2
    assert _select(dxf, index, ids[0])["regions"][0]["area_m2"] == pytest.approx(100)


def test_nested_insert_transform_changes_area(tmp_path):
    dxf = tmp_path / "nested.dxf"
    doc = ezdxf.new()
    block = doc.blocks.new("SURFACE")
    _hatch(block)
    doc.modelspace().add_blockref("SURFACE", (0, 0), dxfattribs={"xscale": 2, "yscale": 3, "rotation": 37})
    doc.saveas(dxf)
    index = _index(dxf)
    ids = [k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH"]
    assert ids
    assert _select(dxf, index, ids[0])["regions"][0]["area_m2"] == pytest.approx(600, rel=0.05)


def test_minsert_keeps_array_cells_and_selects_one(tmp_path):
    dxf = tmp_path / "array.dxf"
    doc = ezdxf.new()
    block = doc.blocks.new("ARRAY_SURFACE")
    _hatch(block)
    insert = doc.modelspace().add_blockref("ARRAY_SURFACE", (0, 0))
    insert.grid(size=(2, 3), spacing=(20, 20))
    doc.saveas(dxf)
    index = _index(dxf)
    ids = [k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH"]
    assert len(ids) == 6
    result = _select(dxf, index, ids[0])
    assert result["regions"][0]["area_m2"] == pytest.approx(100)


def test_paperspace_layout_is_bound_to_selection(tmp_path):
    dxf = tmp_path / "paper.dxf"
    doc = ezdxf.new()
    _hatch(doc.layout("Layout1"))
    doc.saveas(dxf)
    index = _index(dxf, "Layout1")
    ids = [k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH"]
    assert ids
    assert _select(dxf, index, ids[0])["layout"] == "Layout1"


def test_mm_unit_conversion_and_unknown_unit_rejection(tmp_path):
    dxf = tmp_path / "mm.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    _hatch(doc.modelspace(), 1000)
    doc.saveas(dxf)
    index = _index(dxf)
    hid = next(k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH")
    assert _select(dxf, index, hid, unit_scale=0.001)["regions"][0]["area_m2"] == pytest.approx(1)
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"source_sha256": _sha(dxf), "layout": "Model", "cadinst_ids": [hid]}, index=index, unit_scale=None)


def test_duplicate_selection_ids_rejected(tmp_path):
    dxf = tmp_path / "dup.dxf"
    doc = ezdxf.new()
    _hatch(doc.modelspace())
    doc.saveas(dxf)
    index = _index(dxf)
    hid = next(k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH")
    with pytest.raises(SurfaceSelectionError):
        select_hatches(dxf, {"source_sha256": _sha(dxf), "layout": "Model", "cadinst_ids": [hid, hid]}, index=index, unit_scale=1.0)
