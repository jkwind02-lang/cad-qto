"""Execute actual drawing -> working interpretation -> calculator -> review.

The model interpretation is deliberately authored test data, not a claimed AI
recognition benchmark. Geometric and arithmetic expectations are hand-derived.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills/_qto-shared-evidence/scripts"
COORDINATOR = ROOT / "skills/qto-coordinator/scripts"
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(COORDINATOR))
import qto_workflow as workflow
import qto_workflow_state as state_engine
import qto_workflow_tools as tools
from qto_workflow_review import bind_view, feedback_patch, markdown_report
from qto_review_contract import empty_feedback


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def drawing(path, horizontal=2000):
    doc = ezdxf.new()
    doc.units = 4
    model = doc.modelspace()
    model.add_line((0, 0), (horizontal, 0))
    model.add_line((horizontal, 0), (horizontal, 1000))
    model.add_text("给水 DN32", dxfattribs={"insert": (500, 60)})
    doc.saveas(path)


def artifact(aid, kind, name=None):
    return {"id": aid, "kind": kind, "path": name or aid + ".json"}


def job(tid, wp, actor, stage, inputs, outputs, tool=None, arguments=None):
    row = {"id": tid, "title": tid, "work_package_id": wp, "actor": actor,
           "stage": stage, "input_ids": inputs, "output_ids": outputs, "depends_on": []}
    if tool:
        row["action"] = {"tool": tool, "arguments": arguments}
    return row


@pytest.fixture
def project(tmp_path):
    drawing(tmp_path / "pipe.dxf")
    (tmp_path / "basis.txt").write_text("本范围为已画中心线；竖向另查系统图，不能借层高补量。", encoding="utf-8")
    put(tmp_path, "query.json", {"text_pattern": "DN32", "context_padding_raw": 2500, "question": "定位本支路管径标注并查看局部上下文"})
    put(tmp_path, "masonry-source.json", {"wall": {"length_m": 5, "height_m": 3, "thickness_m": .2, "opening_union_area_m2": 2}})
    put(tmp_path, "masonry-input.json", {"wall_segments": [{"id": "wall-1", "length_m": 5, "height_m": 3, "thickness_m": .2,
                                                              "opening_union_area_m2": 2, "embedded_volume_m3": 0,
                                                              "evidence": "独立已闭合尺寸：5×3m，洞口并集2m²，厚0.2m"}]})
    artifacts = [artifact("pipe-source", "source", "pipe.dxf"), artifact("rule", "rule", "basis.txt"),
                 artifact("query", "input"), artifact("index", "index"), artifact("context", "evidence"),
                 artifact("ledger", "input"), artifact("operations", "input"), artifact("pipe-input", "input"),
                 artifact("pipe-result", "result"), artifact("pipe-check", "evidence"),
                 artifact("masonry-source", "source"), artifact("masonry-input", "input"), artifact("masonry-result", "result")]
    wp = [{"id": "pipe", "owner_skill": "qto-pipe-networks", "scope_ids": ["pipe-scope"]},
          {"id": "masonry", "owner_skill": "qto-masonry-secondary", "scope_ids": ["wall-scope"]}]
    tasks = [job("index", "pipe", "program", "read", ["pipe-source"], ["index"], "cad_index", {"source": "pipe-source", "output": "index"}),
             job("read-local", "pipe", "program", "read", ["index", "query", "pipe-source"], ["context"], "cad_scope", {"index": "index", "query": "query", "output": "context", "max-bytes": 12000}),
             job("interpret", "pipe", "model", "recognize", ["index", "context", "rule"], ["ledger", "operations"]),
             job("prepare", "pipe", "program", "recognize", ["index", "operations", "ledger", "pipe-source"], ["pipe-input"], "pipe_prepare", {"index": "index", "operations": "operations", "ledger": "ledger", "output": "pipe-input"}),
             job("infer", "pipe", "program", "calculate", ["pipe-input", "rule"], ["pipe-result"], "pipe_infer", {"input": "pipe-input", "output": "pipe-result"}),
             job("backcheck", "pipe", "model", "reconcile", ["pipe-source", "pipe-result"], ["pipe-check"]),
             job("masonry", "masonry", "program", "calculate", ["masonry-source", "masonry-input"], ["masonry-result"], "masonry_reference", {"input": "masonry-input", "output": "masonry-result"})]
    document = {"schema_version": workflow.SCHEMA, "project_id": "synthetic-engineering", "task_id": "takeoff-1", "revision_id": "1", "task_mode": "production",
                "title": "两条独立工程链", "artifacts": artifacts, "work_packages": wp, "tasks": tasks,
                "scopes": [{"id": "pipe-scope", "title": "已画给水支路", "basis_refs": ["pipe-source"], "reason": "明确的图面工作范围", "inventory_artifact_ids": ["index"], "required_task_ids": ["backcheck"]},
                           {"id": "wall-scope", "title": "给定闭合墙段尺寸复核", "basis_refs": ["masonry-source"], "inventory_disposition": "not_applicable", "inventory_basis_refs": ["masonry-source"], "reason": "此独立包只复核给定尺寸，未授权独立识图", "inventory_artifact_ids": [], "required_task_ids": ["masonry"]}],
                "objects": [{"id": "wall-1", "scope_id": "wall-scope", "observations": [], "basis_refs": ["masonry-source"], "reason": "用户给定闭合墙段"}],
                "quantity_targets": [{"id": "wall-volume", "scope_id": "wall-scope", "work_package_id": "masonry", "physical_id": "wall-1", "extent_id": "whole", "quantity_kind": "net_wall_volume", "measurement_basis_id": "net_dimension", "scenario_id": "working", "unit": "m3", "quantity_role": "geometric", "disposition": "required", "basis_refs": ["masonry-source"], "reason": "(5×3−2)×0.2"}],
                "quantity_claims": [{"id": "wall-claim", "target_id": "wall-volume", "artifact_id": "masonry-result", "result_pointer": "/items/0/net_volume_m3", "identity_pointer": "/items/0/id", "expected_identity": "wall-1", "quantity_level": "inferred", "owner_work_package_id": "masonry"}],
                "observation_dispositions": [], "issues": [], "review_bindings": []}
    return document, state_engine.initial_state(document), tmp_path


def working_interpretation(document, state, root):
    index = workflow.read(root / "index.json")
    lines = [o["object_id"] for o in index["objects"] if o["entity_type"] == "LINE"]
    label = next(o["object_id"] for o in index["objects"] if o["text"])
    source = index["authoritative_source"]
    ledger = {"schema_version": "qto-model-recognition-ledger/1.0", "ledger_id": "model-1", "ledger_version": "1",
              "source_sha256": source["sha256"], "adoption_scope": "working", "review_status": "unreviewed",
              "evidence": [{"evidence_id": "E", "path": source["path"], "sha256": source["sha256"], "source_sha256": source["sha256"],
                            "kind": "cad_property", "locator": "两条支路线及DN32文字", "observation": "相接线段、支路管径及两个端头"}],
              "entries": [{"adjudication_id": "PIPE", "subject_id": "branch", "professional_owner": "qto-pipe-networks", "source_id": "drawing",
                           "recognized_by": "model", "recognized_at": "2026-10-11T13:00:00+00:00", "basis": "相接两条中心线及邻近DN32文字", "scope_basis": "从入口至末端",
                           "identity_basis": "本图源哈希及实际实例路径", "cross_check": "一条连续支路，没有另一个平行系统", "decision": "include", "decision_origin": "model", "status": "active",
                           "object_ids": [o["object_id"] for o in index["objects"]], "evidence_ids": ["E"], "uncertainties": []}]}
    operations = {"index_sha256": index["index_sha256"], "network_id": "branch", "scope_object_ids": [o["object_id"] for o in index["objects"]],
                  "scope_basis": "图示入口至末端", "unit": "m", "parameters": {"snap_tolerance": .0001, "gap_max": .1}, "parameter_basis": "0.1mm接触容差，缺口仅作候选",
                  "assignments": [{"adjudication_id": "PIPE", "object_ids": lines, "role": "pipe_centerline", "system": "W", "observation": "两条线段相接"}],
                  "relations": [{"relation_id": "DN", "kind": "dn_annotation", "target_object_id": lines[0], "annotation_object_id": label, "annotation_text": "给水 DN32", "dn": "DN32", "adjudication_id": "PIPE", "observation": "文字指向本支路，无变径"},
                                {"relation_id": "root", "kind": "root", "target": {"object_id": lines[0], "end": "start"}, "adjudication_id": "PIPE", "observation": "入口"},
                                {"relation_id": "end", "kind": "terminal", "target": {"object_id": lines[1], "end": "end"}, "terminal_type": "已识别末端接口", "adjudication_id": "PIPE", "observation": "末端位于源线终点", "port_dn": "DN32", "port_dn_annotation_object_ids": [label]}],
                  "endpoint_review": {"complete": True, "basis": "本合成范围只有这条完整支路"}}
    put(root, "ledger.json", ledger)
    put(root, "operations.json", operations)
    state = state_engine.checkpoint(document, state, "interpret", root, "model", "已完成当前源版本的工作解释", workflow.tool_versions(document))
    # Replace only this current source's observation mappings after a revision.
    document["objects"] = [o for o in document["objects"] if o["id"] != "branch"] + [
        {"id": "branch", "scope_id": "pipe-scope", "observations": [{"artifact_id": "index", "object_id": oid} for oid in lines], "basis_refs": ["ledger"], "reason": "两段线组成一条图示支路"}]
    document["observation_dispositions"] = [{"scope_id": "pipe-scope", "artifact_id": "index", "object_id": o["object_id"],
                                             "disposition": "included" if o["entity_type"] == "LINE" else "evidence", "physical_id": "branch" if o["entity_type"] == "LINE" else None,
                                             "basis_refs": ["ledger"], "reason": "按本轮原图解释处置"} for o in index["objects"]]
    if not any(t["id"] == "pipe-length" for t in document["quantity_targets"]):
        document["quantity_targets"].append({"id": "pipe-length", "scope_id": "pipe-scope", "work_package_id": "pipe", "physical_id": "branch", "extent_id": "whole", "quantity_kind": "pipe_centerline_length", "measurement_basis_id": "drawn_centerline", "scenario_id": "working", "unit": "m", "quantity_role": "geometric", "disposition": "required", "basis_refs": ["ledger", "rule"], "reason": "本范围图示中心线，不补无来源竖向"})
        document["quantity_claims"].append({"id": "pipe-claim", "target_id": "pipe-length", "artifact_id": "pipe-result", "result_pointer": "/length_summary/W/规则推算量/DN32", "quantity_level": "inferred", "owner_work_package_id": "pipe"})
    return document, state


def finish(project):
    document, state, root = project
    for tid in ("index", "read-local", "masonry"):
        state, _ = workflow.run_one(document, state, tid, root)
    document, state = working_interpretation(document, state, root)
    for tid in ("prepare", "infer"):
        state, _ = workflow.run_one(document, state, tid, root)
    put(root, "pipe-check.json", {"observed": "一条支路，图面三项对象均处置", "engineering_complete": False})
    state = state_engine.checkpoint(document, state, "backcheck", root, "model", "按入口至末端反向回查", workflow.tool_versions(document))
    return document, state, root


def test_real_cad_pipeline_reports_quantities_and_resumes_without_recalculation(project):
    document, state, root = finish(project)
    report = workflow.snapshot(document, json.loads(json.dumps(state)), root)
    actual = {r["target_id"]: r["quantity"] for r in report["quantities"]["result_index"]}
    assert actual == pytest.approx({"pipe-length": 3, "wall-volume": 2.6})
    assert all(s == "completed" for s in report["execution"]["task_statuses"].values())
    assert not report["next"]["actions"]
    assert report["formal_adoption_allowed"] is False
    assert all(s["engineering_completeness_proven"] is False for s in report["quantities"]["scope_status"])
    assert "2.6" in markdown_report(document, report)


def test_source_revision_rereads_and_recalculates_only_dependent_package(project):
    document, state, root = finish(project)
    masonry_receipt = state["task_receipts"]["masonry"]
    drawing(root / "pipe.dxf", horizontal=5000)
    inspection, quantities, _ = workflow.inspect(document, state, root)
    assert inspection["task_statuses"]["masonry"] == "completed"
    assert inspection["task_statuses"]["index"] == "stale"
    assert {r["target_id"] for r in quantities["result_index"]} == {"wall-volume"}
    for tid in ("index", "read-local"):
        state, _ = workflow.run_one(document, state, tid, root)
    document, state = working_interpretation(document, state, root)
    for tid in ("prepare", "infer"):
        state, _ = workflow.run_one(document, state, tid, root)
    report = workflow.snapshot(document, state, root)
    actual = {r["target_id"]: r["quantity"] for r in report["quantities"]["result_index"]}
    assert actual == pytest.approx({"pipe-length": 6, "wall-volume": 2.6})
    assert state["task_receipts"]["masonry"] == masonry_receipt
    assert report["execution"]["task_statuses"]["backcheck"] == "stale"


def test_unread_system_view_stays_pending_without_hiding_computed_plan_quantity(project):
    document, state, root = finish(project)
    document["artifacts"].append(artifact("system-view-reading", "evidence"))
    document["tasks"].append(job("read-system", "pipe", "model", "read", ["pipe-source"], ["system-view-reading"]))
    document["scopes"][0]["required_task_ids"].append("read-system")
    report = workflow.snapshot(document, state, root)
    assert report["execution"]["task_statuses"]["infer"] == "completed"
    assert next(s for s in report["quantities"]["scope_status"] if s["scope_id"] == "pipe-scope")["status"] == "pending"
    assert {r["target_id"]: r["quantity"] for r in report["quantities"]["result_index"]}["pipe-length"] == 3
    assert any(a["task_id"] == "read-system" and a["actor"] == "model" for a in report["next"]["actions"])


def test_issue_queue_asks_project_only_after_actual_reading_attempts(project):
    document, state, root = finish(project)
    issue = {"id": "height", "kind": "missing_source", "question": "补本系统竖向所需剖面和标高依据", "target_ids": ["pipe-length"], "attempt_refs": []}
    document["issues"] = [issue]
    report = workflow.snapshot(document, state, root)
    assert report["next"]["evidence_questions"] and not report["next"]["project_questions"]
    issue["attempt_refs"] = ["pipe-check"]
    report = workflow.snapshot(document, state, root)
    assert report["next"]["project_questions"][0]["affected_quantities"][0]["unit"] == "m"


def test_registered_runner_rejects_shell_and_overwriting_input(project):
    document, state, root = project
    bad = copy.deepcopy(document["tasks"][-1])
    bad["action"]["tool"] = "bash"
    with pytest.raises(ValueError, match="未登记"):
        tools.bindings(bad)
    bad = copy.deepcopy(document["tasks"][-1])
    bad["action"]["arguments"]["output"] = "masonry-input"
    with pytest.raises(ValueError, match="output_ids"):
        tools.bindings(bad)


def test_cli_init_run_report_and_record_role(project, tmp_path):
    root = tmp_path / "cli"
    source = put(tmp_path, "source.json", {"wall": "明确给定尺寸"})
    assert workflow.main(["init", "--workflow", str(root / "workflow.json"), "--state", str(root / "state.json"),
                          "--project-id", "example", "--task-id", "run-1", "--scope", "闭合墙段尺寸复核",
                          "--source", str(source), "--skill", "qto-masonry-secondary"]) == 0
    document = workflow.read(root / "workflow.json")
    calculate = next(t for t in document["tasks"] if t["actor"] == "program")
    assert workflow.main(["record", "--workflow", str(root / "workflow.json"), "--state", str(root / "state.json"),
                          "--task", calculate["id"], "--actor", "model", "--note", "伪装程序完成"]) == 2
    assert workflow.main(["resume", "--workflow", str(root / "workflow.json"), "--state", str(root / "state.json")]) == 0
    assert workflow.main(["report", "--workflow", str(root / "workflow.json"), "--state", str(root / "state.json"), "--output", str(root / "progress.json")]) == 0
    assert (root / "progress.md").is_file()


def reviewed_wall_batch(root, count=20):
    rows = [{"id": f"W{i:02d}", "length_m": 5, "height_m": 3, "thickness_m": .2,
             "opening_union_area_m2": 2, "embedded_volume_m3": 0, "evidence": "给定闭合尺寸及洞口并集"} for i in range(count)]
    put(root, "source.json", {"wall_segments": rows})
    put(root, "input.json", {"wall_segments": rows})
    document = {"schema_version": workflow.SCHEMA, "project_id": "review-demo", "task_id": "takeoff", "revision_id": "1", "task_mode": "production",
                "artifacts": [artifact("source", "source"), artifact("input", "input"), artifact("result", "result"), artifact("view", "review_view"),
                              artifact("feedback", "feedback"), artifact("handoff", "result")],
                "scopes": [{"id": "walls", "title": "明细中的20面墙", "basis_refs": ["source"], "inventory_disposition": "not_applicable",
                             "inventory_basis_refs": ["source"], "reason": "只复核明确闭合尺寸", "required_task_ids": ["calculate"]}],
                "work_packages": [{"id": "walls", "owner_skill": "qto-masonry-secondary", "scope_ids": ["walls"]}],
                "tasks": [job("calculate", "walls", "program", "calculate", ["input", "source"], ["result"], "masonry_reference", {"input": "input", "output": "result"}),
                          job("build-view", "walls", "model", "review", ["result", "source"], ["view"]),
                          job("read-feedback", "walls", "program", "review", ["view", "feedback", "source"], ["handoff"], "review_feedback", {"view": "view", "feedback": "feedback", "output": "handoff"})],
                "objects": [], "quantity_targets": [], "quantity_claims": [], "observation_dispositions": [], "review_bindings": [], "issues": []}
    for n, row in enumerate(rows):
        pid = row["id"]
        document["objects"].append({"id": pid, "scope_id": "walls", "observations": [], "basis_refs": ["source"], "reason": "明确的独立墙段"})
        document["quantity_targets"].append({"id": pid + "-volume", "scope_id": "walls", "work_package_id": "walls", "physical_id": pid,
                                               "extent_id": "whole", "quantity_kind": "net_wall_volume", "measurement_basis_id": "net", "scenario_id": "working",
                                               "unit": "m3", "quantity_role": "geometric", "disposition": "required", "basis_refs": ["source"], "reason": "按闭合尺寸扣洞口并集"})
        document["quantity_claims"].append({"id": pid + "-claim", "target_id": pid + "-volume", "artifact_id": "result", "result_pointer": f"/items/{n}/net_volume_m3",
                                              "identity_pointer": f"/items/{n}/id", "expected_identity": pid, "quantity_level": "inferred", "owner_work_package_id": "walls"})
        document["review_bindings"].append({"id": pid + "-binding", "catalog_key": pid + "-view", "target_ids": [pid + "-volume"]})
    state = state_engine.initial_state(document)
    state, _ = workflow.run_one(document, state, "calculate", root)
    execution, quantities, _ = workflow.inspect(document, state, root)
    template = {"schema": "qto-review-view/1.0", "project_id": document["project_id"], "review_id": "walls-review", "title": "墙段数量复核", "revision": "1",
                "sources": [{"id": "source", "title": "明确墙段尺寸", "sha256": execution["artifacts"]["source"]["sha256"]}],
                "scenes": [{"id": "schedule", "title": "明细复核，无原图定位", "source_id": "source", "coordinate_unit": "m", "bounds": [0, 0, 1, 1]}],
                "objects": [{"id": r["id"], "catalog_key": r["id"] + "-view", "title": r["id"], "professional": "qto-masonry-secondary",
                             "category": "墙段", "detail": "明确尺寸复核", "source": "source.json", "status": "工作量", "kind": "unlocated", "scene_id": None} for r in rows],
                "groups": [{"id": "G", "title": "洞口并集修订", "category": "洞口", "proposal": "按实际尺寸复算", "reason": "保留组内例外", "status": "待复核",
                            "member_keys": [r["id"] + "-view" for r in rows]}]}
    view = bind_view(document, execution, quantities, template)
    put(root, "view.json", view)
    state = state_engine.checkpoint(document, state, "build-view", root, "model", "按实际量项生成复核视图", workflow.tool_versions(document))
    group = view["groups"][0]
    selected, excluded = group["member_keys"][:-2], group["member_keys"][-2:]
    feedback = empty_feedback(view)
    feedback["annotations"]["G-note"] = {"group_id": "G", "confirmed_result": "选中墙段洞口并集应为3m2，两个例外仍为2m2", "reason": "合成案例的新尺寸意见",
                                            "instruction": "review_method", "updated_at": "2026-10-11T14:00:00+00:00", "scope": "selected_members",
                                            "selected_object_keys": selected, "excluded_object_keys": excluded,
                                            "object_ids": [r["id"] for r in rows[:-2]], "member_snapshot": copy.deepcopy(group["member_snapshot"]),
                                            "result_fingerprint": group["result_fingerprint"]}
    put(root, "feedback.json", feedback)
    state, _ = workflow.run_one(document, state, "read-feedback", root)
    return document, state, view


def test_feedback_runs_selected_18_preserves_two_exceptions_and_is_idempotent(tmp_path):
    document, state, view = reviewed_wall_batch(tmp_path)
    execution, quantities, _ = workflow.inspect(document, state, tmp_path)
    patch = feedback_patch(document, execution, "handoff", "a" * 64)
    assert len(patch["upsert"]["quantity_claims"]) == 18
    amended = workflow.amend(document, patch, "a" * 64)
    execution, quantities, _ = workflow.inspect(amended, state, tmp_path)
    assert {r["physical_id"] for r in quantities["result_index"]} == {"W18", "W19"}
    again = feedback_patch(amended, execution, "handoff", "b" * 64)
    assert not any(again["upsert"].values())
    added = {t["id"]: t for t in patch["upsert"]["tasks"]}
    artifacts = {a["id"]: a for a in amended["artifacts"]}
    interpret = next(t for t in added.values() if t["stage"] == "review")
    put(tmp_path, artifacts[interpret["output_ids"][0]]["path"], {"selected": list(range(18)), "excluded": [18, 19], "opening_union_area_m2": 3, "origin": "synthetic user method feedback"})
    state = state_engine.checkpoint(amended, state, interpret["id"], tmp_path, "model", "范围、例外及修订事实已明确", workflow.tool_versions(amended))
    prepare = next(t for t in added.values() if t["stage"] == "recognize")
    revised = workflow.read(tmp_path / "input.json")
    for row in revised["wall_segments"][:-2]:
        row["opening_union_area_m2"] = 3
    put(tmp_path, artifacts[prepare["output_ids"][0]]["path"], revised)
    state = state_engine.checkpoint(amended, state, prepare["id"], tmp_path, "model", "只改选中对象并保持稳定ID", workflow.tool_versions(amended))
    calculate = next(t for t in added.values() if t["actor"] == "program")
    state, evidence = workflow.run_one(amended, state, calculate["id"], tmp_path)
    report = workflow.snapshot(amended, state, tmp_path)
    actual = {r["physical_id"]: r["quantity"] for r in report["quantities"]["result_index"]}
    assert len(actual) == 20
    assert actual["W00"] == 2.4 and actual["W18"] == actual["W19"] == 2.6
    assert sum(actual.values()) == pytest.approx(48.4)
    assert workflow.read(tmp_path / "result.json")["items"][0]["net_volume_m3"] == 2.6
    assert not report["formal_adoption_allowed"]
    assert next(t for t in added.values() if t["stage"] == "reconcile")["id"] in report["execution"]["ready_task_ids"]


def test_review_binding_cannot_redirect_numbers_or_old_feedback_to_other_object(tmp_path):
    document, state, view = reviewed_wall_batch(tmp_path, count=4)
    execution, quantities, _ = workflow.inspect(document, state, tmp_path)
    bad = copy.deepcopy(document)
    bad["review_bindings"][0]["target_ids"] = ["W01-volume"]
    with pytest.raises(ValueError, match="实物不符"):
        bind_view(bad, execution, quantities, view)
    with pytest.raises(ValueError, match="绑定不同|身份与量项实物不符"):
        feedback_patch(bad, execution, "handoff", "c" * 64)


def test_rule_only_change_with_same_value_invalidates_bound_review_fingerprint(tmp_path):
    document, state, view = reviewed_wall_batch(tmp_path, count=4)
    # A revised task interpretation is part of the calculation contract even
    # when this case's actual dimensions and resulting numbers stay the same.
    document["tasks"][0]["rule_interpretation"] = "本次已查明：该条件仍适用，但规则版本不同"
    state, _ = workflow.run_one(document, state, "calculate", tmp_path)
    execution, quantities, _ = workflow.inspect(document, state, tmp_path)
    revised = bind_view(document, execution, quantities, view)
    assert revised["objects"][0]["quantities"] == view["objects"][0]["quantities"]
    assert revised["objects"][0]["result_fingerprint"] != view["objects"][0]["result_fingerprint"]
    assert execution["task_statuses"]["build-view"] == "stale"
    assert execution["task_statuses"]["read-feedback"] == "stale"


def test_runner_keeps_immutable_native_output_references(tmp_path):
    document, state, view = reviewed_wall_batch(tmp_path, count=4)
    attempt = next(a for a in state["attempts"] if a["tool"] == "masonry_reference")
    folder = Path(attempt["run_directory"])
    staged = next(folder.glob("*-result.json"))
    assert staged.read_bytes() == (tmp_path / "result.json").read_bytes()
    assert (folder / "stdout.log").is_file()
