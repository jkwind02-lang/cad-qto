"""Independent runner checks with actual native calculator subprocesses."""
import copy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from test_workflow_integration import (
    artifact, finish, job, project, put, state_engine, tools, workflow,
)


def controls(document, state, root, state_name="state.json"):
    workflow_path = put(root, "workflow.json", document)
    state_path = put(root, state_name, state)
    return workflow_path, state_path


def cli(command, workflow_path, state_path, *args):
    return workflow.main([command, "--workflow", str(workflow_path), "--state", str(state_path), *args])


def two_native_branches(project):
    document, _, root = project
    put(root, "east-source.json", {"wall": "独立东区墙段：3×2m，厚0.2m，无洞口"})
    put(root, "east-input.json", {"wall_segments": [dict(id="wall-east", length_m=3, height_m=2,
        thickness_m=.2, opening_union_area_m2=0, embedded_volume_m3=0, evidence="东区明确尺寸")]})
    document["artifacts"].extend([
        artifact("east-source", "source"), artifact("east-input", "input"), artifact("east-result", "result")])
    document["work_packages"].append(dict(id="east", owner_skill="qto-masonry-secondary", scope_ids=["east-scope"]))
    document["scopes"].append(dict(id="east-scope", title="独立东区给定墙段", basis_refs=["east-source"],
        inventory_disposition="not_applicable", inventory_basis_refs=["east-source"], inventory_artifact_ids=[],
        reason="仅计算给定闭合尺寸", required_task_ids=["east-calculate"]))
    document["tasks"].append(job("east-calculate", "east", "program", "calculate", ["east-source", "east-input"],
                                 ["east-result"], "masonry_reference", {"input": "east-input", "output": "east-result"}))
    document["objects"].append(dict(id="wall-east", scope_id="east-scope", observations=[],
                                      basis_refs=["east-source"], reason="独立东区物理墙段"))
    target = copy.deepcopy(document["quantity_targets"][0])
    target.update(id="east-volume", scope_id="east-scope", work_package_id="east", physical_id="wall-east",
                  basis_refs=["east-source"], reason="3×2×0.2")
    document["quantity_targets"].append(target)
    claim = copy.deepcopy(document["quantity_claims"][0])
    claim.update(id="east-claim", target_id="east-volume", artifact_id="east-result",
                 expected_identity="wall-east", owner_work_package_id="east")
    document["quantity_claims"].append(claim)
    return document, state_engine.initial_state(document), root


def test_independent_state_branches_overlap_real_calculators_and_merge_progress(project, monkeypatch):
    document, state, root = two_native_branches(project)
    workflow_path, west_state = controls(document, state, root, "west-state.json")
    east_state = put(root, "east-state.json", state)
    first_entered = threading.Event()
    readers_seen = []
    readers_dir = workflow_path.with_name(workflow_path.name + ".readers")
    overlap = threading.Barrier(2, action=lambda: readers_seen.append(len(list(readers_dir.iterdir()))))
    native_run = tools.subprocess.run

    def concurrent_native(command, *args, **kwargs):
        assert str(command[2]).endswith("calculate_masonry_reference.py")
        first_entered.set()
        overlap.wait(timeout=15)
        return native_run(command, *args, **kwargs)  # Actual registered CLI, no fabricated result.

    monkeypatch.setattr(tools.subprocess, "run", concurrent_native)
    with ThreadPoolExecutor(max_workers=2) as pool:
        west = pool.submit(cli, "run", workflow_path, west_state, "--task", "masonry")
        assert first_entered.wait(15)
        east = pool.submit(cli, "run", workflow_path, east_state, "--task", "east-calculate")
        assert west.result(timeout=25) == 0
        assert east.result(timeout=25) == 0
    assert readers_seen == [2]
    assert not list(readers_dir.iterdir())
    assert workflow.read(root / "masonry-result.json")["items"][0]["net_volume_m3"] == pytest.approx(2.6)
    assert workflow.read(root / "east-result.json")["items"][0]["net_volume_m3"] == pytest.approx(1.2)
    assert set(workflow.read(west_state)["task_receipts"]) == {"masonry"}
    assert set(workflow.read(east_state)["task_receipts"]) == {"east-calculate"}
    assert cli("merge", workflow_path, west_state, "--incoming-state", str(east_state)) == 0
    merged = workflow.snapshot(document, workflow.read(west_state), root)
    assert merged["execution"]["task_statuses"]["masonry"] == "completed"
    assert merged["execution"]["task_statuses"]["east-calculate"] == "completed"
    assert {r["target_id"]: r["quantity"] for r in merged["quantities"]["result_index"]} == pytest.approx({
        "wall-volume": 2.6, "east-volume": 1.2})


def test_amend_waits_for_running_branch_then_accepts_same_patch(project, monkeypatch):
    document, state, root = project
    workflow_path, state_path = controls(document, state, root)
    old_hash = workflow.file_hash(workflow_path)
    entered, release = threading.Event(), threading.Event()
    native_run = tools.subprocess.run

    def paused_native(command, *args, **kwargs):
        entered.set()
        assert release.wait(15)
        return native_run(command, *args, **kwargs)

    monkeypatch.setattr(tools.subprocess, "run", paused_native)
    patch = dict(expected_workflow_sha256=old_hash, project_id=document["project_id"], task_id=document["task_id"],
                 reason="明确新增待查事项，不更改已计算尺寸", upsert={"issues": [dict(id="review-east",
                     kind="read_more", question="查东区是否另有墙段", target_ids=["wall-volume"], attempt_refs=[])]})
    patch_path = put(root, "patch.json", patch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(cli, "run", workflow_path, state_path, "--task", "masonry")
        try:
            assert entered.wait(15)
            assert cli("amend", workflow_path, state_path, "--patch", str(patch_path)) == 2
            assert workflow.file_hash(workflow_path) == old_hash
        finally:
            release.set()
        assert running.result(timeout=25) == 0
    assert cli("amend", workflow_path, state_path, "--patch", str(patch_path)) == 0
    assert workflow.read(workflow_path)["revision_id"] == "2"


def test_external_workflow_edit_during_native_run_keeps_output_unpublished(project, monkeypatch):
    document, state, root = project
    workflow_path, state_path = controls(document, state, root)
    before_state = state_path.read_bytes()
    native_run = tools.subprocess.run
    produced = []

    def edit_after_native(command, *args, **kwargs):
        result = native_run(command, *args, **kwargs)
        assert result.returncode == 0
        output_path = root / command[command.index("--output") + 1]
        assert output_path.is_file()
        produced.append(output_path)
        edited = copy.deepcopy(document)
        edited["title"] = "运行期间外部编辑了工作范围说明"
        put(root, "workflow.json", edited)
        return result

    monkeypatch.setattr(tools.subprocess, "run", edit_after_native)
    assert cli("run", workflow_path, state_path, "--task", "masonry") == 2
    assert produced and produced[0].is_file()  # Retain actual staged evidence for diagnosis.
    assert not (root / "masonry-result.json").exists()
    assert state_path.read_bytes() == before_state
    assert not list(workflow_path.with_name(workflow_path.name + ".readers").iterdir())


def test_missing_indirect_input_blocks_only_its_package_and_descendants(project):
    document, state, root = project
    state, _ = workflow.run_one(document, state, "index", root)
    read_task = next(t for t in document["tasks"] if t["id"] == "read-local")
    read_task["input_ids"].remove("pipe-source")
    document["tasks"].reverse()  # Inspection must follow dependency order, not author order.
    report = workflow.snapshot(document, state, root)
    execution = report["execution"]
    assert execution["task_statuses"]["index"] == "completed"
    assert execution["task_statuses"]["read-local"] == "blocked"
    assert execution["tasks"]["read-local"]["dependency_errors"]
    assert all(not execution["tasks"][tid]["can_run"] for tid in ("interpret", "prepare", "infer", "backcheck"))
    assert execution["tasks"]["masonry"]["can_run"]
    workflow_path, state_path = controls(document, state, root)
    assert cli("advance", workflow_path, state_path, "--max-jobs", "5") == 0
    final = workflow.snapshot(document, workflow.read(state_path), root)
    assert final["execution"]["task_statuses"]["masonry"] == "completed"
    assert final["execution"]["task_statuses"]["read-local"] == "blocked"
    assert {r["target_id"]: r["quantity"] for r in final["quantities"]["result_index"]} == pytest.approx({"wall-volume": 2.6})
    repair = next(a for a in final["next"]["actions"] if a["task_id"] == "read-local")
    assert repair["actor"] == "model" and repair["dependency_errors"]


def test_record_checks_actual_dependencies_of_previously_completed_ancestors(project):
    document, state, root = finish(project)
    next(t for t in document["tasks"] if t["id"] == "read-local")["input_ids"].remove("pipe-source")
    versions = workflow.tool_versions(document)
    # Real native outputs above are retained. Create receipts with the generic
    # state kernel to model a checkpoint predating the CLI indirect-read gate.
    # Its automatic scope basis still sees the source, but the actual tool's
    # explicit input contract is incomplete and must block a new model record.
    contracts = {task["id"]: task for task in document["tasks"]}
    for tid in ("read-local", "interpret", "prepare", "infer", "backcheck"):
        state = state_engine.checkpoint(document, state, tid, root, contracts[tid]["actor"],
                                        "旧检查入口已有真实文件回执", versions)
    kernel = state_engine.inspect_workflow(document, state, root, versions)
    assert kernel["tasks"]["backcheck"]["can_checkpoint"] is True
    inspection, quantities, _ = workflow.inspect(document, state, root)
    assert inspection["tasks"]["read-local"]["status"] == "stale"
    assert inspection["tasks"]["backcheck"]["can_checkpoint"] is False
    assert inspection["artifacts"]["pipe-result"]["status"] == "stale"
    assert {r["target_id"] for r in quantities["result_index"]} == {"wall-volume"}
    workflow_path, state_path = controls(document, state, root)
    before = state_path.read_bytes()
    assert cli("record", workflow_path, state_path, "--task", "backcheck", "--actor", "model", "--note",
               "不能用旧程序回执掩盖尚未登记的实际文件依赖") == 2
    assert state_path.read_bytes() == before
