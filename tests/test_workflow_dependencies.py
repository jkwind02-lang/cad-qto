"""Registered CLI file reads remain in the resumable workflow dependency set."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/_qto-shared-evidence/scripts"))
from qto_workflow_dependencies import NO_INDIRECT_TOOLS, SUPPORTED_TOOLS, validate_dependencies
from qto_workflow_state import checkpoint, initial_state, inspect_workflow
from qto_workflow_tools import TOOLS


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


def setup(root, tool_id, documents):
    task = {"id": "calculate", "input_ids": [], "output_ids": [],
            "action": {"tool": tool_id, "arguments": {}}}
    artifacts = {}
    for field, (name, data) in documents.items():
        path = root / name
        if data is not None:
            write(path, data)
        artifacts[field] = {"path": str(path)}
        task["input_ids"].append(field)
        task["action"]["arguments"][field] = field
    for field in TOOLS[tool_id]["outputs"]:
        aid = "out-" + field
        artifacts[aid] = {"path": str(root / "outputs" / (field + ".json"))}
        task["action"]["arguments"][field] = aid
        task["output_ids"].append(aid)
    return task, artifacts


def include(task, artifacts, aid, path, data=b"source evidence"):
    if data is not None:
        write(path, data)
    artifacts[aid] = {"path": str(path)}
    task["input_ids"].append(aid)


def assert_inputs(report, *ids):
    assert report["errors"] == []
    assert report["pending_artifact_ids"] == []
    assert set(report["required_artifact_ids"]) == set(ids)


def test_every_registered_cli_has_an_audited_read_contract():
    assert SUPPORTED_TOOLS == set(TOOLS)


def test_concrete_cwd_references_and_excluded_drift_files_are_all_required(tmp_path):
    data = {"source": {"dwg_path": "plan.dwg", "entity_stream_path": "cache/entities.gz",
                       "metadata_path": "cache/meta.json",
                       "excluded_drifted_files": [{"path": "old.dwg"}]}}
    task, artifacts = setup(tmp_path, "concrete_formwork", {"manifest": ("inputs/run.json", data)})
    first = validate_dependencies(task, artifacts, tmp_path)
    assert len(first["errors"]) == 4
    assert {r["path"] for r in first["references"]} == {
        str(tmp_path / name) for name in ("plan.dwg", "cache/entities.gz", "cache/meta.json", "old.dwg")}
    for aid, name in (("drawing", "plan.dwg"), ("entities", "cache/entities.gz"),
                      ("metadata", "cache/meta.json"), ("excluded", "old.dwg")):
        include(task, artifacts, aid, tmp_path / name)
    assert_inputs(validate_dependencies(task, artifacts, tmp_path),
                  "manifest", "drawing", "entities", "metadata", "excluded")


@pytest.mark.parametrize("tool_id,base", [
    ("structural_steel", "."), ("surfaces_envelope", "inputs"),
    ("earthwork_terrain", "inputs"), ("correspondence", "inputs"),
])
def test_source_list_uses_each_cli_actual_relative_base(tmp_path, tool_id, base):
    field = "input" if tool_id == "correspondence" else "manifest"
    payload = {"sources": [{"path": " drawing.dwg ", "selection": "selected"},
                           {"path": "excluded.pdf", "selection": "excluded"}]}
    task, artifacts = setup(tmp_path, tool_id, {field: ("inputs/run.json", payload)})
    include(task, artifacts, "drawing", tmp_path / base / "drawing.dwg")
    include(task, artifacts, "excluded", tmp_path / base / "excluded.pdf")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), field, "drawing", "excluded")


def test_rebar_requires_absolute_sources_but_not_readonly_directories_or_json_selectors(tmp_path):
    source = tmp_path / "cache/source.json"
    payload = {"sources": [{"path": str(source), "json_assertions": {"source.path": "old.json"}}],
               "readonly_roots": [str(tmp_path / "do-not-write")],
               "evidence": [{"path": "entity.attributes.width"}], "result_out": "future.json"}
    task, artifacts = setup(tmp_path, "rebar", {"manifest": ("inputs/run.json", payload)})
    include(task, artifacts, "source", source)
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "manifest", "source")
    payload["sources"][0]["path"] = "cache/source.json"
    write(tmp_path / "inputs/run.json", payload)
    report = validate_dependencies(task, artifacts, tmp_path)
    assert len(report["errors"]) == 1
    assert "绝对文件路径" in report["errors"][0]


def test_concrete_does_not_strip_path_whitespace_that_its_cli_preserves(tmp_path):
    payload = {"source": {k: " file with spaces " for k in
                           ("dwg_path", "entity_stream_path", "metadata_path")}}
    task, artifacts = setup(tmp_path, "concrete_formwork", {"manifest": ("manifest.json", payload)})
    include(task, artifacts, "source", tmp_path / " file with spaces ")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "manifest", "source")


@pytest.mark.parametrize("tool_id", ["cad_index", "cad_view"])
def test_raw_cad_tools_track_optional_export_record_when_it_appears(tmp_path, tool_id):
    documents = {"source": ("drawing.dxf", b"DXF")}
    if tool_id == "cad_view":
        documents["query"] = ("query.json", {"question": "查看当前图样"})
    task, artifacts = setup(tmp_path, tool_id, documents)
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), *documents)
    receipt = write(tmp_path / "drawing.dxf.export.json", {"output_sha256": "new receipt"})
    report = validate_dependencies(task, artifacts, tmp_path)
    assert len(report["errors"]) == 1 and str(receipt) in report["errors"][0]
    include(task, artifacts, "export", receipt, data=None)
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), *documents, "export")


@pytest.mark.parametrize("tool_id", ["cad_index", "cad_view"])
def test_cad_descriptor_reads_cwd_drawing_authoritative_source_and_sidecar(tmp_path, tool_id):
    payload = {"path": "drawing.dxf", "authoritative_source": {"path": "drawing.dwg"},
               "export_evidence": {"path": "historical-descriptive-field.json"}}
    documents = {"source": ("descriptors/source.json", payload)}
    if tool_id == "cad_view":
        documents["query"] = ("query.json", {"question": "查看当前图样"})
    task, artifacts = setup(tmp_path, tool_id, documents)
    include(task, artifacts, "dxf", tmp_path / "drawing.dxf")
    include(task, artifacts, "dwg", tmp_path / "drawing.dwg")
    include(task, artifacts, "export", tmp_path / "drawing.dxf.export.json")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), *documents, "dxf", "dwg", "export")


def test_descriptor_sidecar_is_relative_to_symlink_name_used_by_actual_cli(tmp_path):
    target = write(tmp_path / "actual/plan.dxf", b"drawing")
    alias = tmp_path / "plan.dxf"
    try:
        alias.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    task, artifacts = setup(tmp_path, "cad_index", {"source": ("descriptor.json", {"path": str(alias)})})
    include(task, artifacts, "drawing", target, data=None)
    include(task, artifacts, "export", tmp_path / "plan.dxf.export.json")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "source", "drawing", "export")


@pytest.mark.parametrize("tool_id", ["cad_scope", "pipe_prepare"])
def test_cad_consumers_require_current_source_files_and_do_not_scan_query_locators(tmp_path, tool_id):
    index = {"source": {"path": "drawing.dxf"}, "authoritative_source": {"path": "drawing.dwg"}}
    documents = {"index": ("cache/index.json", index)}
    if tool_id == "cad_scope":
        documents["query"] = ("query.json", {"path": "locator-only", "artifact_dir": "future-evidence"})
    else:
        documents["operations"] = ("operations.json", {"path": "CADINST/handle"})
        documents["ledger"] = ("evidence/ledger.json", {"evidence": [{"path": "local.json"},
                                                                 {"path": "../specification.pdf"}]})
    task, artifacts = setup(tmp_path, tool_id, documents)
    include(task, artifacts, "drawing", tmp_path / "drawing.dxf")
    include(task, artifacts, "authoritative", tmp_path / "drawing.dwg")
    expected = [*documents, "drawing", "authoritative"]
    if tool_id == "pipe_prepare":
        include(task, artifacts, "local", tmp_path / "evidence/local.json")
        include(task, artifacts, "specification", tmp_path / "specification.pdf")
        expected += ["local", "specification"]
    # Only index generation reads the adjacent conversion record. Subsequent
    # scope/apply operations protect that path from overwrite but do not read it.
    write(tmp_path / "drawing.dxf.export.json", {"ignored_in_this_cli": True})
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), *expected)


def test_instance_ledgers_require_registration_before_nested_evidence_is_opened(tmp_path, monkeypatch):
    ledger = write(tmp_path / "ledgers/ledger.json", {"evidence": [
        {"path": "pages/one.png"}, {"path": "../documents/unused-note.pdf"}]})
    payload = {"sources": [{"path": "../drawing.dxf"}],
               "recognition_ledgers": [{"path": "../ledgers/ledger.json"}]}
    task, artifacts = setup(tmp_path, "instance_requirements", {"input": ("inputs/run.json", payload)})
    include(task, artifacts, "drawing", tmp_path / "drawing.dxf")
    original = Path.read_bytes
    reads = []

    def observe(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", observe)
    report = validate_dependencies(task, artifacts, tmp_path)
    assert str(ledger) in report["errors"][0]
    assert ledger not in reads
    artifacts["ledger"] = {"path": str(ledger)}
    report = validate_dependencies(task, artifacts, tmp_path)
    assert "input_ids" in report["errors"][0] and "ledger" in report["required_artifact_ids"]
    assert ledger not in reads
    task["input_ids"].append("ledger")
    report = validate_dependencies(task, artifacts, tmp_path)
    assert ledger in reads
    assert len(report["errors"]) == 2
    include(task, artifacts, "page", tmp_path / "ledgers/pages/one.png")
    include(task, artifacts, "note", tmp_path / "documents/unused-note.pdf")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "input", "drawing", "ledger", "page", "note")


def test_previous_instance_result_is_historical_data_not_a_recursive_file_request(tmp_path):
    task, artifacts = setup(tmp_path, "instance_requirements", {
        "input": ("input.json", {"sources": [], "recognition_ledgers": []}),
        "previous": ("previous.json", {"sources": {"old": {"path": "/unavailable/old.dwg"}},
                                          "evidence_catalog": {"old": {"path": "/old/snapshot.png"}}}),
    })
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "input", "previous")


@pytest.mark.parametrize("tool_id,field", [("review_html", "input"), ("review_feedback", "view")])
def test_review_raster_is_relative_to_view_but_source_metadata_is_not_read(tmp_path, tool_id, field):
    payload = {"sources": [{"path": "identity-only.dwg"}], "scenes": [
        {"raster_path": "images/page.png"}, {"raster_data_uri": "data:image/png;base64,aGVsbG8="}],
        "objects": [{"path": "geometry/locator"}]}
    documents = {field: ("review/view.json", payload),
                 "feedback": ("review/feedback.html", b"<html>direct bytes only</html>")}
    task, artifacts = setup(tmp_path, tool_id, documents)
    include(task, artifacts, "raster", tmp_path / "review/images/page.png")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), field, "feedback", "raster")


@pytest.mark.parametrize("tool_id", sorted(NO_INDIRECT_TOOLS))
def test_no_indirect_cli_does_not_treat_arbitrary_paths_or_ledger_metadata_as_file_reads(tmp_path, tool_id):
    payload = {"sources": [{"path": "/unavailable/drawing.dwg"}],
               "rules": [{"path": "/unavailable/rules.pdf"}],
               "recognition_basis": {"ledger_path": "/unavailable/ledger.json"},
               "path": "JSON field selector", "output": "future.json"}
    task, artifacts = setup(tmp_path, tool_id, {"input": ("input.json", payload)})
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "input")


def test_missing_future_json_and_future_ledger_are_pending_without_global_failure(tmp_path):
    task, artifacts = setup(tmp_path, "concrete_formwork", {"manifest": ("future.json", None)})
    report = validate_dependencies(task, artifacts, tmp_path)
    assert report["errors"] == [] and report["pending_artifact_ids"] == ["manifest"]
    payload = {"sources": [], "recognition_ledgers": [{"path": "future-ledger.json"}]}
    task, artifacts = setup(tmp_path, "instance_requirements", {"input": ("input.json", payload)})
    include(task, artifacts, "ledger", tmp_path / "future-ledger.json", data=None)
    report = validate_dependencies(task, artifacts, tmp_path)
    assert report["errors"] == [] and report["pending_artifact_ids"] == ["ledger"]
    assert set(report["required_artifact_ids"]) == {"input", "ledger"}


def test_existing_stale_input_only_discovers_dependencies_and_preserves_state(tmp_path):
    task, artifacts = setup(tmp_path, "surfaces_envelope", {
        "manifest": ("input.json", {"sources": [{"path": "drawing.dwg"}]})})
    artifacts["manifest"]["status"] = "stale"
    include(task, artifacts, "drawing", tmp_path / "drawing.dwg")
    before = copy.deepcopy(artifacts)
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "manifest", "drawing")
    assert artifacts == before and artifacts["manifest"]["status"] == "stale"


def test_json_change_after_state_snapshot_requires_a_new_inspection(tmp_path):
    task, artifacts = setup(tmp_path, "surfaces_envelope", {
        "manifest": ("input.json", {"sources": []})})
    artifacts["manifest"]["sha256"] = hashlib.sha256((tmp_path / "input.json").read_bytes()).hexdigest()
    write(tmp_path / "input.json", {"sources": [{"path": "another.dwg"}]})
    report = validate_dependencies(task, artifacts, tmp_path)
    assert len(report["errors"]) == 1 and "依赖检查期间已改变" in report["errors"][0]
    assert report["references"] == []


def test_known_malformed_container_is_local_error_without_generic_path_scanning(tmp_path):
    task, artifacts = setup(tmp_path, "structural_steel", {
        "manifest": ("input.json", {"sources": {"path": "not-an-array"}})})
    report = validate_dependencies(task, artifacts, tmp_path)
    assert len(report["errors"]) == 1 and "sources 必须为对象数组" in report["errors"][0]
    assert report["references"] == []


def test_new_registered_tool_without_read_contract_is_rejected_locally(tmp_path, monkeypatch):
    monkeypatch.setitem(TOOLS, "new_tool", TOOLS["pipe_infer"])
    task, artifacts = setup(tmp_path, "new_tool", {"input": ("input.json", {})})
    report = validate_dependencies(task, artifacts, tmp_path)
    assert len(report["errors"]) == 1 and "尚未登记文件读取契约" in report["errors"][0]


def test_adding_discovered_input_makes_real_evidence_changes_invalidate_only_its_chain(tmp_path):
    task, artifacts = setup(tmp_path, "surfaces_envelope", {
        "manifest": ("inputs/run.json", {"sources": [{"path": "../drawing.dwg"}]})})
    include(task, artifacts, "drawing", tmp_path / "drawing.dwg")
    task["input_ids"].remove("drawing")
    report = validate_dependencies(task, artifacts, tmp_path)
    assert "drawing" in report["required_artifact_ids"] and report["errors"]
    task["input_ids"].append("drawing")
    assert_inputs(validate_dependencies(task, artifacts, tmp_path), "manifest", "drawing")
    task.update(work_package_id="surface", title="Calculate surfaces", actor="program",
                stage="calculate", depends_on=[])
    include(task, artifacts, "unrelated", tmp_path / "unrelated.txt")
    task["input_ids"].remove("unrelated")
    artifacts["review"] = {"path": str(tmp_path / "review.json")}
    artifacts["other-output"] = {"path": str(tmp_path / "other-output.json")}
    doc = {"schema_version": "qto-project-workflow/1.0", "project_id": "P", "task_id": "T",
           "revision_id": "R1", "task_mode": "production",
           "artifacts": [{"id": aid, "kind": "input", **row} for aid, row in artifacts.items()],
           "scopes": [{"id": "scope", "title": "Explicit area", "basis_refs": [],
                       "inventory_artifact_ids": [], "required_task_ids": []}],
           "work_packages": [{"id": "surface", "owner_skill": "qto-surfaces-envelope", "scope_ids": ["scope"]},
                             {"id": "other", "owner_skill": "qto-masonry-secondary", "scope_ids": []}],
           "tasks": [task, {"id": "review", "work_package_id": "surface", "title": "Review",
                            "stage": "review", "actor": "user", "depends_on": [],
                            "input_ids": ["out-output"], "output_ids": ["review"]},
                     {"id": "other", "work_package_id": "other", "title": "Independent work",
                      "stage": "recognize", "actor": "model", "depends_on": [],
                      "input_ids": ["unrelated"], "output_ids": ["other-output"]}]}
    versions = {"surfaces_envelope": "a" * 64}
    state = initial_state(doc)
    for row in doc["tasks"]:
        for aid in row["output_ids"]:
            write(Path(artifacts[aid]["path"]), {"quantity": 12})
        state = checkpoint(doc, state, row["id"], tmp_path, row["actor"], "Actual output", versions)
    before = inspect_workflow(doc, state, tmp_path, versions)
    assert set(before["task_statuses"].values()) == {"completed"}
    write(tmp_path / "drawing.dwg", b"Revised evidence, quantity could still be 12")
    after = inspect_workflow(doc, state, tmp_path, versions)
    assert after["task_statuses"] == {"calculate": "stale", "review": "stale", "other": "completed"}
