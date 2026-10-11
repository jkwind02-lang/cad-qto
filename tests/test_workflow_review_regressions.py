"""Actual feedback, versioned preparation and review-output regressions."""
import copy
from pathlib import Path

import pytest

import test_workflow_integration as integration


def revise_feedback(root, document, state, feedback, suffix):
    """Keep prior feedback/handoff files and run the real feedback validator."""
    feedback_path = "feedback-" + suffix + ".json"
    handoff_path = "handoff-" + suffix + ".json"
    for row in document["artifacts"]:
        if row["id"] == "feedback":
            row["path"] = feedback_path
        elif row["id"] == "handoff":
            row["path"] = handoff_path
    integration.put(root, feedback_path, feedback)
    state, _ = integration.workflow.run_one(document, state, "read-feedback", root)
    return state


def shared_input_jobs(root):
    document, state, view = integration.reviewed_wall_batch(root, count=4)
    document["artifacts"].append(integration.artifact("result-b", "result"))
    second = copy.deepcopy(document["tasks"][0])
    second.update(id="calculate-b", output_ids=["result-b"])
    second["action"]["arguments"]["output"] = "result-b"
    document["tasks"].append(second)
    document["quantity_claims"][1]["artifact_id"] = "result-b"
    document["tasks"][1]["input_ids"].append("result-b")
    next(a for a in document["artifacts"] if a["id"] == "view")["path"] = "view-two-jobs.json"
    state, _ = integration.workflow.run_one(document, state, "calculate-b", root)
    execution, quantities, _ = integration.workflow.inspect(document, state, root)
    view = integration.bind_view(document, execution, quantities, view)
    integration.put(root, "view-two-jobs.json", view)
    state = integration.state_engine.checkpoint(
        document, state, "build-view", root, "model", "Current outputs from two program jobs",
        integration.workflow.tool_versions(document))
    feedback = integration.workflow.read(root / "feedback.json")
    feedback["result_fingerprint"] = view["result_fingerprint"]
    feedback["annotations"]["G-note"]["result_fingerprint"] = view["groups"][0]["result_fingerprint"]
    feedback["annotations"]["G-note"]["member_snapshot"] = copy.deepcopy(view["groups"][0]["member_snapshot"])
    return document, revise_feedback(root, document, state, feedback, "two-jobs"), view


def test_shared_editable_input_creates_distinct_preparations_and_completes_both_jobs(tmp_path):
    document, state, _ = shared_input_jobs(tmp_path)
    execution, _, _ = integration.workflow.inspect(document, state, tmp_path)
    patch = integration.feedback_patch(document, execution, "handoff", "a" * 64)
    for rows in patch["upsert"].values():
        assert len({r["id"] for r in rows}) == len(rows)
    amended = integration.workflow.amend(document, patch, "a" * 64)
    integration.state_engine.initial_state(amended)  # No duplicate producers or cycles.
    jobs = patch["upsert"]["tasks"]
    preparations = [t for t in jobs if t["stage"] == "recognize"]
    programs = [t for t in jobs if t["actor"] == "program"]
    assert len(preparations) == len(programs) == 2
    assert len({a for t in preparations for a in t["output_ids"]}) == 2
    assert len({t["action"]["arguments"]["input"] for t in programs}) == 2
    artifacts = {a["id"]: a for a in amended["artifacts"]}
    interpret = next(t for t in jobs if t["stage"] == "review")
    versions = integration.workflow.tool_versions(amended)
    integration.put(tmp_path, artifacts[interpret["output_ids"][0]]["path"], {
        "selected": ["W00", "W01"], "exceptions": ["W02", "W03"],
        "opening_union_area_m2": 3, "adoption": "working interpretation only"})
    state = integration.state_engine.checkpoint(
        amended, state, interpret["id"], tmp_path, "model", "Retained selected objects and exceptions", versions)
    for task in preparations:
        data = integration.workflow.read(tmp_path / "input.json")
        for row in data["wall_segments"]:
            if row["id"] in {"W00", "W01"}:
                row["opening_union_area_m2"] = 3
        integration.put(tmp_path, artifacts[task["output_ids"][0]]["path"], data)
        state = integration.state_engine.checkpoint(
            amended, state, task["id"], tmp_path, "model", "Prepared distinct version for this job", versions)
    for task in programs:
        state, evidence = integration.workflow.run_one(amended, state, task["id"], tmp_path)
        assert evidence["returncode"] == 0
    execution, quantities, _ = integration.workflow.inspect(amended, state, tmp_path)
    actual = {row["physical_id"]: row["quantity"] for row in quantities["result_index"]}
    assert actual == {"W00": 2.4, "W01": 2.4, "W02": 2.6, "W03": 2.6}
    assert sum(actual.values()) == pytest.approx(10)
    for name in ("result.json", "result-b.json"):
        assert integration.workflow.read(tmp_path / name)["items"][0]["net_volume_m3"] == 2.6
    assert all(execution["task_statuses"][t["id"]] == "completed" for t in programs)
    assert not any(integration.feedback_patch(amended, execution, "handoff", "b" * 64)["upsert"].values())


def test_multiple_real_opinions_for_one_job_produce_one_preparation_and_one_rerun(tmp_path):
    document, state, _ = integration.reviewed_wall_batch(tmp_path, count=4)
    feedback = integration.workflow.read(tmp_path / "feedback.json")
    second = copy.deepcopy(feedback["annotations"]["G-note"])
    second.update(confirmed_result="对选中墙段按洞口并集3m2复核净体积", reason="补充说明同一作业的计量方法",
                  updated_at="2026-10-11T15:00:00+00:00")
    feedback["annotations"]["G-method"] = second
    state = revise_feedback(tmp_path, document, state, feedback, "two-opinions")
    execution, _, _ = integration.workflow.inspect(document, state, tmp_path)
    patch = integration.feedback_patch(document, execution, "handoff", "a" * 64)
    jobs = patch["upsert"]["tasks"]
    assert len([t for t in jobs if t["stage"] == "recognize"]) == 1
    assert len([t for t in jobs if t["actor"] == "program"]) == 1
    interpret = next(t for t in jobs if t["stage"] == "review")
    assert len(interpret["review_actions"]) == 2
    assert interpret["selected_target_ids"] == ["W00-volume", "W01-volume"]
    amended = integration.workflow.amend(document, patch, "a" * 64)
    execution, quantities, _ = integration.workflow.inspect(amended, state, tmp_path)
    assert {row["physical_id"] for row in quantities["result_index"]} == {"W02", "W03"}
    assert not any(integration.feedback_patch(amended, execution, "handoff", "b" * 64)["upsert"].values())


def test_cli_bind_review_writes_a_declared_new_model_review_output(tmp_path):
    document, state, _ = integration.reviewed_wall_batch(tmp_path, count=4)
    document["artifacts"].append({"id": "next-view", "kind": "review_view", "path": "next-view.json"})
    # The former view is the actual geometry template read by bind-review. It
    # and all result artifacts belong in this new model task's dependency set.
    document["tasks"].append(integration.job(
        "next-view", "walls", "model", "review", ["view", "result", "source"], ["next-view"]))
    integration.put(tmp_path, "workflow.json", document)
    integration.put(tmp_path, "state.json", state)
    code = integration.workflow.main([
        "bind-review", "--workflow", str(tmp_path / "workflow.json"),
        "--state", str(tmp_path / "state.json"), "--view", str(tmp_path / "view.json"),
        "--output", str(tmp_path / "next-view.json")])
    assert code == 0
    saved = integration.workflow.read(tmp_path / "next-view.json")
    assert saved["workflow_binding"]["task_id"] == document["task_id"]
    assert saved["objects"][0]["quantities"][0]["value"] == 2.6
    execution, _, _ = integration.workflow.inspect(document, state, tmp_path)
    assert execution["task_statuses"]["next-view"] == "ready"  # Generation alone is not a checkpoint.
    assert (tmp_path / "view.json").is_file() and (tmp_path / "feedback.json").is_file()


def test_unconsumed_target_rule_withdraws_number_and_still_versions_pending_review(tmp_path):
    document, state, template = integration.reviewed_wall_batch(tmp_path, count=4)
    document["artifacts"].append({"id": "quantity-rule", "kind": "rule", "path": "quantity-rule.txt"})
    rule = tmp_path / "quantity-rule.txt"
    rule.write_text("Edition 1: use the net area for this condition.", encoding="utf-8")
    document["quantity_targets"][0]["basis_refs"].append("quantity-rule")
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    assert "W00-volume" not in {r["target_id"] for r in quantities["result_index"]}
    first = integration.bind_view(document, execution, quantities, template)
    assert first["objects"][0]["quantities"][0]["value"] is None
    rule.write_text("Edition 2: the same formula has a revised applicability condition.", encoding="utf-8")
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    second = integration.bind_view(document, execution, quantities, template)
    assert second["objects"][0]["quantities"][0]["value"] is None
    assert first["objects"][0]["result_fingerprint"] != second["objects"][0]["result_fingerprint"]
    assert first["objects"][1]["result_fingerprint"] == second["objects"][1]["result_fingerprint"]
    # Closing the real workflow input chain permits an actual recomputation,
    # without treating mere file registration as engineering interpretation.
    document["tasks"][0]["input_ids"].append("quantity-rule")
    state, _ = integration.workflow.run_one(document, state, "calculate", tmp_path)
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    third = integration.bind_view(document, execution, quantities, template)
    assert third["objects"][0]["quantities"][0]["value"] == 2.6


def test_changed_actual_rule_same_native_bytes_requires_new_review_version(tmp_path):
    document, state, template = integration.reviewed_wall_batch(tmp_path, count=4)
    document["artifacts"].append({"id": "quantity-rule", "kind": "rule", "path": "quantity-rule.txt"})
    document["quantity_targets"][0]["basis_refs"].append("quantity-rule")
    document["tasks"][0]["input_ids"].append("quantity-rule")
    rule = tmp_path / "quantity-rule.txt"
    rule.write_text("Edition 1: original applicability has been checked.", encoding="utf-8")
    state, _ = integration.workflow.run_one(document, state, "calculate", tmp_path)
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    first = integration.bind_view(document, execution, quantities, template)
    native_before = (tmp_path / "result.json").read_bytes()
    rule.write_text("Edition 2: a different condition now justifies the same quantity.", encoding="utf-8")
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    assert execution["task_statuses"]["calculate"] == "stale"
    assert not quantities["result_index"]
    state, _ = integration.workflow.run_one(document, state, "calculate", tmp_path)
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    second = integration.bind_view(document, execution, quantities, template)
    assert (tmp_path / "result.json").read_bytes() == native_before
    assert first["objects"][0]["quantities"] == second["objects"][0]["quantities"]
    assert first["objects"][0]["result_fingerprint"] != second["objects"][0]["result_fingerprint"]


@pytest.mark.parametrize("attachment", ["object", "scope", "observation_index"])
def test_pending_review_tracks_actual_object_scope_and_observation_evidence(tmp_path, attachment):
    document, state, template = integration.reviewed_wall_batch(tmp_path, count=4)
    document["artifacts"].append({"id": "additional-basis", "kind": "evidence", "path": "additional.json"})
    evidence = integration.put(tmp_path, "additional.json", {"revision": 1, "working_fact": "still unresolved"})
    if attachment == "object":
        document["objects"][0]["basis_refs"].append("additional-basis")
    elif attachment == "scope":
        document["scopes"][0]["basis_refs"].append("additional-basis")
    else:
        # An unresolved observation still has an actual file identity. Its
        # bytes changing must not leave old feedback current merely because
        # there is no accepted quantity or valid inventory mapping yet.
        document["objects"][0]["observations"].append({"artifact_id": "additional-basis", "object_id": "unresolved-1"})
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    first = integration.bind_view(document, execution, quantities, template)
    assert first["objects"][0]["quantities"][0]["value"] is None
    integration.put(tmp_path, evidence.name, {"revision": 2, "working_fact": "still unresolved"})
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    second = integration.bind_view(document, execution, quantities, template)
    assert second["objects"][0]["quantities"][0]["value"] is None
    assert first["objects"][0]["result_fingerprint"] != second["objects"][0]["result_fingerprint"]


@pytest.mark.parametrize("kind,readonly", [("source_input", None), ("input", True)])
def test_feedback_revises_a_separate_copy_of_protected_professional_input(tmp_path, monkeypatch, kind, readonly):
    original_artifact = integration.artifact

    def protected_input(aid, artifact_kind, name=None):
        row = original_artifact(aid, artifact_kind, name)
        if aid == "input":
            row["kind"] = kind
            if readonly is not None:
                row["read_only"] = readonly
        return row

    monkeypatch.setattr(integration, "artifact", protected_input)
    document, state, _ = integration.reviewed_wall_batch(tmp_path, count=4)
    original = copy.deepcopy(next(a for a in document["artifacts"] if a["id"] == "input"))
    original_bytes = (tmp_path / "input.json").read_bytes()
    execution, _, _ = integration.workflow.inspect(document, state, tmp_path)
    patch = integration.feedback_patch(document, execution, "handoff", "a" * 64)
    amended = integration.workflow.amend(document, patch, "a" * 64)
    integration.workflow.inspect(amended, state, tmp_path)  # The new model output is writable.
    assert next(a for a in amended["artifacts"] if a["id"] == "input") == original
    assert not {"source", "input"} & {a["id"] for a in patch["upsert"]["artifacts"]}
    jobs = patch["upsert"]["tasks"]
    artifacts = {a["id"]: a for a in amended["artifacts"]}
    interpret = next(t for t in jobs if t["stage"] == "review")
    prepare = next(t for t in jobs if t["stage"] == "recognize")
    revised = artifacts[prepare["output_ids"][0]]
    assert revised["kind"] == "input"
    if readonly is not None:
        assert revised["read_only"] is False
    assert revised["path"] != original["path"]
    integration.put(tmp_path, artifacts[interpret["output_ids"][0]]["path"], {
        "selected": ["W00", "W01"], "exceptions": ["W02", "W03"], "opening_union_area_m2": 3})
    versions = integration.workflow.tool_versions(amended)
    state = integration.state_engine.checkpoint(
        amended, state, interpret["id"], tmp_path, "model", "Interpret selected revision with retained exceptions", versions)
    data = integration.workflow.read(tmp_path / "input.json")
    for row in data["wall_segments"][:2]:
        row["opening_union_area_m2"] = 3
    integration.put(tmp_path, revised["path"], data)
    state = integration.state_engine.checkpoint(
        amended, state, prepare["id"], tmp_path, "model", "New professional input, original preserved", versions)
    program = next(t for t in jobs if t["actor"] == "program")
    state, _ = integration.workflow.run_one(amended, state, program["id"], tmp_path)
    _, quantities, _ = integration.workflow.inspect(amended, state, tmp_path)
    assert {r["physical_id"]: r["quantity"] for r in quantities["result_index"]} == {
        "W00": 2.4, "W01": 2.4, "W02": 2.6, "W03": 2.6}
    assert (tmp_path / "input.json").read_bytes() == original_bytes
