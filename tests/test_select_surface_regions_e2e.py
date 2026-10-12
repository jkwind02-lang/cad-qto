import copy
import json
from test_surface_project_feedback import document, run as run_professional, validator, c
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
    result = _select(dxf, index, ids[0])
    assert result["formal_adoption_allowed"] is False
    assert result["adoption_scope"] == "working"
    assert result["index_sha256"] == index["index_sha256"] and result["conversion_warnings"] == index.get("conversion_warnings", [])


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


def test_source_change_after_geometry_is_rejected(tmp_path, monkeypatch):
    dxf = tmp_path / "source_change.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("A")
    _hatch(block)
    doc.modelspace().add_blockref("A", (0, 0))
    doc.saveas(dxf)


    index = _index(dxf)
    oid = next(
        k for k, v in module.WORKBENCH._verified_index(index).items()
        if v["entity_type"] == "HATCH"
    )


    original = module.EXTRACT.hatch_geometry


    def wrapped(*args, **kwargs):
        geometry = original(*args, **kwargs)
        with open(dxf, "ab") as fp:
            fp.write(b"changed")
        return geometry


    monkeypatch.setattr(module.EXTRACT, "hatch_geometry", wrapped)


    with pytest.raises(module.SurfaceSelectionError):
        _select(dxf, index, oid)

def test_xref_parent_insert_is_rejected(tmp_path):
    dxf = tmp_path / "xref_parent.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("XREF_BLOCK")
    block.block.dxf.flags = 4
    _hatch(block)
    doc.modelspace().add_blockref("XREF_BLOCK", (0, 0))
    doc.saveas(dxf)


    index = _index(dxf)
    oid = next(
        k for k, v in module.WORKBENCH._verified_index(index).items()
        if v["entity_type"] == "HATCH"
    )


    with pytest.raises(module.SurfaceSelectionError):
        _select(dxf, index, oid)

@pytest.mark.parametrize("insunits", [1, 2, 5])
@pytest.mark.parametrize("unit", ["mm", "m"])
def test_invalid_insunits_rejected(insunits, unit):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    with pytest.raises(module.SurfaceSelectionError):
        module._unit_scale_from_evidence(doc, unit, "explicit")




def test_insunits_zero_evidence_not_keyword_parsed():
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 0
    assert module._unit_scale_from_evidence(
        doc, "mm", "毫米及米换算见总说明"
    ) == pytest.approx(0.001)


def test_resolve_selection_ids_exact_layer_and_validation():
    objects = {
        "h1": {"entity_type": "HATCH", "effective_layer": "A.[1]"},
        "h2": {"entity_type": "HATCH", "effective_layer": "Axx1"},
    }
    assert module._resolve_selection_ids(objects, {"layer_names": ["A.[1]"]}) == ["h1"]
    assert module._resolve_selection_ids(objects, {"cadinst_ids": ["h1", "h2"], "layer_names": ["A.[1]"]}) == ["h1"]
    for selection in (
        {"layer_names": ["A[1]"]},
        {"layer_names": ["A.[1]", "不存在层"]},
        {"cadinst_ids": []},
        {"cadinst_ids": None},
        {"layer_names": None},
        {"cadinst_ids": ["bad"]},
        {"layer_names": ["NONE"]},
        {"cadinst_ids": ["h1"], "layer_names": ["Axx1"]},
    ):
        with pytest.raises(module.SurfaceSelectionError):
            module._resolve_selection_ids(objects, selection)

def test_select_surface_cli_layer_selection(tmp_path):
    import json
    import subprocess
    import sys


    dxf = tmp_path / "cli.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    h1 = _hatch(doc.modelspace())
    h1.dxf.layer = "A"
    h2 = _hatch(doc.modelspace())
    h2.dxf.layer = "B"
    doc.saveas(dxf)


    index = _index(dxf)
    index_file = tmp_path / "index.json"
    selection_file = tmp_path / "selection.json"
    index_file.write_text(json.dumps(index), encoding="utf-8")
    payload = {"source_sha256": _sha(dxf), "layout": "Model", "layer_names": ["A"]}
    selection_file.write_text(json.dumps(payload), encoding="utf-8")


    cmd = [sys.executable, str(SELECTOR), "--dxf", str(dxf),
           "--index", str(index_file), "--selection-file", str(selection_file),
           "--unit", "m"]
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 0
    result = json.loads(run.stdout)
    assert len(result["regions"]) == 1
    assert result["regions"][0]["area_m2"] == pytest.approx(100)


    selection_file.write_text(json.dumps({**payload, "layer_names": ["NONE"]}), encoding="utf-8")
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 2
    assert run.stdout.strip() == ""


def test_selected_hatch_regions_feed_measure_union(tmp_path, document):
    dxf = tmp_path / "union.dxf"
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    h1 = _hatch(doc.modelspace(), 10)
    h2 = _hatch(doc.modelspace(), 10)
    h2.transform(ezdxf.math.Matrix44.translate(5, 0, 0))
    h3 = _hatch(doc.modelspace(), 10)
    h3.transform(ezdxf.math.Matrix44.translate(100, 0, 0))
    doc.saveas(dxf)


    index = _index(dxf)
    objects = workbench._verified_index(index)
    ids = [
        oid for oid, row in objects.items()
        if row["entity_type"] == "HATCH"
        and row["source_handle"] in {h1.dxf.handle, h2.dxf.handle}
    ]
    result = module.select_hatches(
        dxf, {"source_sha256": _sha(dxf), "layout": "Model",
              "cadinst_ids": ids},
        index=index, unit="m", unit_evidence="explicit_m"
    )
    measured = module.EXTRACT.measure_region_group(result["regions"])
    assert measured["sum_before_union_m2"] == pytest.approx(200)
    assert measured["overlap_removed_m2"] == pytest.approx(50)
    assert measured["area_m2"] == pytest.approx(150)
    assert measured["candidate_region_ids"] == []
    assert measured["region_ids"] == ids
    assert measured["polygons"]
    assert all("area_m2" in r and "bounds_m" in r for r in result["regions"])

    union_path = tmp_path / "union.json"
    union_path.write_text(json.dumps(measured, ensure_ascii=False), encoding="utf-8")
    union_sha = hashlib.sha256(union_path.read_bytes()).hexdigest()
    d = copy.deepcopy(document)
    src = copy.deepcopy(d["sources"][0])
    src.update(path=str(dxf), sha256=hashlib.sha256(dxf.read_bytes()).hexdigest(),
               file_role="dxf", source_role="project_drawing")
    d["sources"][0] = src
    for e in d["evidence"]:
        if e["evidence_id"] == "EV-I":
            e["source_id"] = src["source_id"]
    union_src = copy.deepcopy(src)
    union_src.update(source_id="SRC-UNION", path=str(union_path),
                     sha256=union_sha, file_role="json",
                     source_role="calculation_evidence")
    d["sources"].append(union_src)
    for e in d["evidence"]:
        if e["evidence_id"] == "EV-G":
            e["source_id"] = "SRC-UNION"
    item = d["items"][0]
    item.update(object_type="ceiling_finish", method="area_direct",
                evidence_refs=["EV-I", "EV-G", "EV-L"],
                inputs={"area_m2": measured["area_m2"]})
    item.pop("opening_union_evidence_ref", None)
    item["evidence_refs"] = [x for x in item["evidence_refs"] if x != "EV-O"]
    out = run_professional(d, tmp_path)
    r = out["工程量结果"][0]
    assert r["结果"]["数值"] == pytest.approx(150)
    assert r["专业检查"]["进入采用小计"] is False
    assert r["专业检查"]["采用角色"] == "reference"
    assert r["专业检查"]["规则成熟状态"] == ["候选"]
    assert validator.validate_document(out) == []
    union_path.write_bytes(union_path.read_bytes() + b"x")
    with pytest.raises(c.CalculationError, match="哈希"):
        run_professional(d, tmp_path)
