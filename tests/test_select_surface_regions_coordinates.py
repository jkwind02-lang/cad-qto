import hashlib
import importlib.util
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).parents[1]
SELECTOR = ROOT / "skills" / "qto-surfaces-envelope" / "scripts" / "select_surface_regions.py"
WORKBENCH = ROOT / "skills" / "_qto-shared-evidence" / "scripts" / "qto_recognition_workbench.py"

spec = importlib.util.spec_from_file_location("selector", SELECTOR)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)

spec = importlib.util.spec_from_file_location("workbench", WORKBENCH)
workbench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workbench)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hatch(container, size=10):
    h = container.add_hatch()
    h.paths.add_polyline_path([(0, 0), (size, 0), (size, size), (0, size)], is_closed=True)


def select(path, index, oid):
    return selector.select_hatches(path, {"source_sha256": sha(path), "layout": index["layout"], "cadinst_ids": [oid]}, index=index, unit="m", unit_evidence="explicit_m")


def test_instance_transform_origin_and_nonzero_bounds(tmp_path):
    dxf = tmp_path / "transform.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("S")
    hatch(block)
    doc.modelspace().add_blockref("S", (50, 20), dxfattribs={"xscale": 2, "yscale": 3, "rotation": 0})
    doc.saveas(dxf)
    index = workbench.build_index({"path": str(dxf), "sha256": sha(dxf)}, layout="Model")
    objects = workbench._verified_index(index)
    oid = next(k for k, v in objects.items() if v["entity_type"] == "HATCH")
    row = objects[oid]
    assert row["instance_transform"][3] == pytest.approx(50)
    assert row["instance_transform"][7] == pytest.approx(20)
    result = select(dxf, index, oid)
    assert result["regions"][0]["area_m2"] == pytest.approx(600)
    assert result["regions"][0]["bounds_m"][0] >= 50


def test_minsert_non_first_cell_has_position(tmp_path):
    dxf = tmp_path / "array_position.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("A")
    hatch(block)
    insert = doc.modelspace().add_blockref("A", (0, 0))
    insert.grid(size=(2, 3), spacing=(20, 30))
    doc.saveas(dxf)
    index = workbench.build_index({"path": str(dxf), "sha256": sha(dxf)}, layout="Model")
    objects = workbench._verified_index(index)
    ids = [k for k, v in objects.items() if v["entity_type"] == "HATCH"]
    assert len(ids) == 6
    non_first = next(k for k in ids if "[1,2]" in k)
    result = select(dxf, index, non_first)
    assert result["regions"][0]["bounds_m"][0] == pytest.approx(40)
    assert result["regions"][0]["bounds_m"][1] == pytest.approx(20)


def test_two_level_insert_combines_transform(tmp_path):
    dxf = tmp_path / "nested_two_level.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    inner = doc.blocks.new("INNER")
    hatch(inner)
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (10, 0), dxfattribs={"xscale": 2, "yscale": 1})
    doc.modelspace().add_blockref("OUTER", (5, 0), dxfattribs={"xscale": 3, "yscale": 1})
    doc.saveas(dxf)
    index = workbench.build_index({"path": str(dxf), "sha256": sha(dxf)}, layout="Model")
    objects = workbench._verified_index(index)
    oid = next(k for k, v in objects.items() if v["entity_type"] == "HATCH")
    result = select(dxf, index, oid)
    assert result["regions"][0]["area_m2"] == pytest.approx(600)
