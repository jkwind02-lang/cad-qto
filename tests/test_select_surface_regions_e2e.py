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


def _select(path, index, oid, unit="m", evidence="explicit_m"):
    return select_hatches(path, {"source_sha256": _sha(path), "layout": index["layout"], "cadinst_ids": [oid]}, index=index, unit=unit, unit_evidence=evidence)


def test_exact_selection_uses_verified_index(tmp_path):
    dxf = tmp_path / "two_hatch.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    _hatch(doc.modelspace())
    _hatch(doc.modelspace(), 20)
    doc.saveas(dxf)
    index = _index(dxf)
    ids = [k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH"]
    assert _select(dxf, index, ids[0])["regions"][0]["area_m2"] == pytest.approx(100)


def test_nested_insert_transform_changes_area(tmp_path):
    dxf = tmp_path / "nested.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("SURFACE")
    _hatch(block)
    doc.modelspace().add_blockref("SURFACE", (0, 0), dxfattribs={"xscale": 2, "yscale": 3, "rotation": 37})
    doc.saveas(dxf)
    index = _index(dxf)
    oid = next(k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH")
    assert _select(dxf, index, oid)["regions"][0]["area_m2"] == pytest.approx(600)


def test_minsert_keeps_array_cells_and_selects_one(tmp_path):
    dxf = tmp_path / "array.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("ARRAY_SURFACE")
    _hatch(block)
    insert = doc.modelspace().add_blockref("ARRAY_SURFACE", (0, 0))
    insert.grid(size=(2, 3), spacing=(20, 20))
    doc.saveas(dxf)
    index = _index(dxf)
    ids = [k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH"]
    assert len(ids) == 6
    assert _select(dxf, index, ids[0])["regions"][0]["area_m2"] == pytest.approx(100)


def test_paperspace_layout_is_bound_to_selection(tmp_path):
    dxf = tmp_path / "paper.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    _hatch(doc.layout("Layout1"))
    doc.saveas(dxf)
    index = _index(dxf, "Layout1")
    oid = next(k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH")
    assert _select(dxf, index, oid)["layout"] == "Layout1"


def test_mm_conversion_and_unit_conflicts(tmp_path):
    dxf = tmp_path / "mm.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    _hatch(doc.modelspace(), 1000)
    doc.saveas(dxf)
    index = _index(dxf)
    oid = next(k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH")
    assert _select(dxf, index, oid, "mm", "explicit_mm")["regions"][0]["area_m2"] == pytest.approx(1)
    with pytest.raises(SurfaceSelectionError):
        _select(dxf, index, oid, "m", "explicit_m")


def test_unknown_unit_requires_evidence(tmp_path):
    dxf = tmp_path / "unknown.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 0
    _hatch(doc.modelspace())
    doc.saveas(dxf)
    index = _index(dxf)
    oid = next(k for k, v in workbench._verified_index(index).items() if v["entity_type"] == "HATCH")
    with pytest.raises(SurfaceSelectionError):
        _select(dxf, index, oid, "m", "")
    assert _select(dxf, index, oid, "m", "drawing_note_m")
