"""Synthetic sources only: no project identifiers or drawings."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/_qto-shared-evidence/scripts"))
import qto_recognition_workbench as workbench
import prepare_source_specialists as adapter


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def sample(tmp_path):
    doc = ezdxf.new()
    doc.units = 4
    m = doc.modelspace()
    m.add_line((0, 0), (1200, 0))
    m.add_line((1200, 0), (1200, 800))
    m.add_text("尺寸单位毫米，标高为米", dxfattribs={"insert": (0, 30)})
    m.add_text("P-1", dxfattribs={"insert": (0, 50)})
    dxf = tmp_path / "synthetic.dxf"
    doc.saveas(dxf)
    source = dict(path=str(dxf), sha256=hashlib.sha256(dxf.read_bytes()).hexdigest())
    idx = workbench.build_index(source)
    ip = put(tmp_path / "index.json", idx)
    ids = [r["object_id"] for r in idx["objects"] if r["entity_type"] == "LINE"]
    ledger = dict(schema_version="qto-model-recognition-ledger/1.0", ledger_id="working",
                  ledger_version="1", source_sha256=source["sha256"],
                  adoption_scope="working", review_status="unreviewed",
                  evidence=[dict(evidence_id="EV", path=str(ip), sha256=hashlib.sha256(ip.read_bytes()).hexdigest(),
                                 source_sha256=source["sha256"], kind="cad_property",
                                 locator="Model", observation="synthetic source geometry")],
                  entries=[dict(adjudication_id="I", status="active", decision_origin="model",
                                subject_id="selected", professional_owner="qto-electrical-routes",
                                source_id="drawing", recognized_by="model", recognized_at="2026-01-01",
                                basis="reviewed source", scope_basis="two paths", identity_basis="source instances",
                                cross_check="source geometry", decision="include", object_ids=ids,
                                evidence_ids=["EV"], uncertainties=[])])
    lp = put(tmp_path / "ledger.json", ledger)
    op = dict(target="electrical", index_sha256=idx["index_sha256"], scope_object_ids=ids,
              scope_basis="only two selected paths", project_id="synthetic",
              paths=[dict(id=f"path{i}", object_id=oid, adjudication_id="I",
                          observation="selected physical channel axis", from_node=f"N{i}",
                          to_node=f"N{i+1}", role="horizontal") for i, oid in enumerate(ids)],
              channels=[dict(id="tray", physical_id="tray", kind="tray", system="power",
                             spec="candidate", start_node="N0", end_node="N2", parallel_count=1,
                             path_ids=["path0", "path1"], source_ids=["EV"])], circuits=[])
    return idx, op, lp, ip


def test_source_geometry_enters_existing_calculator(sample):
    idx, op, lp, _ = sample
    out = adapter.prepare(idx, op, lp)
    assert [r["length_m"] for r in out["paths"]] == pytest.approx([1.2, .8])
    from calculate_electrical_paths import calculate
    result = calculate(out)
    assert result["items"][0]["length_m"] == pytest.approx(2)
    assert out["source_preparation"]["formal_adoption_allowed"] is False


@pytest.mark.parametrize("field,value", [("length_m", 200), ("physical_id", "fake"), ("position", [1, 2]), ("geometry", {})])
def test_no_coordinate_or_identity_injection(sample, field, value):
    idx, op, lp, _ = sample
    op["paths"][0][field] = value
    with pytest.raises(ValueError, match="覆盖"):
        adapter.prepare(idx, op, lp)


def test_wrong_snapshot_or_source_change(sample):
    idx, op, lp, _ = sample
    op["index_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="快照"):
        adapter.prepare(idx, op, lp)
    op["index_sha256"] = idx["index_sha256"]
    Path(idx["source"]["path"]).write_text("changed", encoding="utf8")
    with pytest.raises(ValueError):
        adapter.prepare(idx, op, lp)


def test_fake_node_connectivity_is_rejected(sample):
    idx, op, lp, _ = sample
    op["paths"][1].update(from_node="N2", to_node="N1")
    with pytest.raises(ValueError, match="源坐标不连续"):
        adapter.prepare(idx, op, lp)


def test_repeated_source_cannot_double_count(sample):
    idx, op, lp, _ = sample
    op["paths"][1]["object_id"] = op["paths"][0]["object_id"]
    with pytest.raises(ValueError, match="重复"):
        adapter.prepare(idx, op, lp)


def test_wrong_profession_does_not_authorize_selection(sample):
    idx, op, lp, _ = sample
    ledger = adapter.read(lp)
    ledger["entries"][0]["professional_owner"] = "qto-pipe-networks"
    put(lp, ledger)
    with pytest.raises(ValueError, match="本专业"):
        adapter.prepare(idx, op, lp)


def test_search_retains_scopes_ambiguity_and_not_found(sample):
    idx, _, _, ip = sample
    scope = dict(index=str(ip), index_sha256=idx["index_sha256"],
                 applicability="separate drawing interpretation", query={})
    request = dict(terms=["P-1"], scopes=[scope, dict(scope, applicability="different system")], max_hits=1)
    out = adapter.search(request)
    assert out["matched_count"] == 2 and len(out["omitted_hits"]) == 1
    assert out["automatic_identity_merge"] is False
    request["terms"] = ["ABSENT"]
    assert adapter.search(request)["matched_count"] == 0
    assert len(adapter.search(request)["searched"]) == 2


def test_evidence_excerpt_and_snapshot_validation(sample):
    idx, op, lp, ip = sample
    tag = next(r for r in idx["objects"] if r.get("text") == "P-1")
    ref = dict(index=str(ip), index_sha256=idx["index_sha256"], object_id=tag["object_id"],
               excerpt="P-1", applicability="only selected path", interpretation="candidate label")
    op["paths"][0]["evidence_refs"] = [ref]
    assert adapter.prepare(idx, op, lp)["paths"][0]["source_binding"]["evidence_refs"][0]["excerpt"] == "P-1"
    ref["excerpt"] = "P-99"
    with pytest.raises(ValueError, match="原文"):
        adapter.prepare(idx, op, lp)


def test_unit_words_are_not_substrings(sample):
    idx, op, lp, ip = sample
    text = next(r for r in idx["objects"] if r.get("text", "") and "毫米" in r["text"])
    ref = dict(index=str(ip), index_sha256=idx["index_sha256"], object_id=text["object_id"],
               excerpt="毫米", raw_unit="Millimeters", applicability="plan only", interpretation="exclude elevation units")
    op["unit_declaration"] = ref
    adapter.prepare(idx, op, lp)
    ref.update(excerpt="米", raw_unit="Meters")
    with pytest.raises(ValueError):
        adapter.prepare(idx, op, lp)


def test_components_identity_and_unaffected_rows(sample):
    idx, op, lp, _ = sample
    ledger = adapter.read(lp)
    ledger["entries"][0]["professional_owner"] = "qto-components-points"
    put(lp, ledger)
    op["target"] = "components"
    op["instances"] = [dict(object_id=r["object_id"], adjudication_id="I", observation="reviewed symbol",
                            category="equipment", system="water", spec="candidate", unit="台")
                       for r in op.pop("paths")]
    before = adapter.prepare(idx, op, lp)
    op["instances"][0]["spec"] = "reviewed different specification"
    after = adapter.prepare(idx, op, lp)
    assert before["reconciled_instances"][1] == after["reconciled_instances"][1]
    assert before["reconciled_instances"][0]["physical_id"] == after["reconciled_instances"][0]["physical_id"]
    assert before["source_preparation"]["operations_sha256"] != after["source_preparation"]["operations_sha256"]
    assert after["reconciled_instances"][0]["location_m"] == pytest.approx([.6, 0])

def test_grouped_ordinary_geometry_counts_once_and_keeps_ancestor_warnings(sample):
    idx, op, lp, _ = sample
    ledger = adapter.read(lp)
    ledger["entries"][0]["professional_owner"] = "qto-components-points"
    put(lp, ledger)
    ids = op["scope_object_ids"]
    op.update(target="components", instances=[dict(object_id=ids[0], member_object_ids=ids,
        adjudication_id="I", observation="one reviewed symbol with multiple source primitives",
        category="equipment", system="water", spec="candidate", unit="台")])
    out = adapter.prepare(idx, op, lp)
    assert len(out["reconciled_instances"]) == 1
    assert len(out["reconciled_instances"][0]["source_binding"]["member_sources"]) == 2
    assert out["source_preparation"]["unprocessed_object_ids"] == []


@pytest.mark.parametrize("tool_id", ["source_specialist_prepare", "source_evidence_search"])
def test_indirect_index_sources_cannot_escape_snapshot(sample, tool_id, tmp_path):
    from qto_workflow_dependencies import validate_dependencies
    idx, op, lp, ip = sample
    ref = dict(index=str(ip), index_sha256=idx["index_sha256"], object_id=op["scope_object_ids"][0],
               applicability="synthetic", interpretation="read")
    op["paths"][0]["evidence_refs"] = [ref]
    request = dict(terms=["P-1"], scopes=[ref])
    operation_path = put(tmp_path / "operations.json", op if tool_id.endswith("prepare") else request)
    artifacts = {"index": {"path": str(ip)}, "ledger": {"path": str(lp)},
                 "operations": {"path": str(operation_path)}, "source": {"path": idx["source"]["path"]}}
    args = dict(index="index", operations="operations", ledger="ledger") if tool_id.endswith("prepare") else dict(input="operations")
    task = dict(id="prepare", input_ids=list(artifacts), action=dict(tool=tool_id, arguments=args))
    report = validate_dependencies(task, artifacts, tmp_path)
    assert report["errors"] == []
    task["input_ids"].remove("source")
    assert validate_dependencies(task, artifacts, tmp_path)["errors"]
