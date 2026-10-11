"""Real-file workflow continuity, isolation and parallel branch regressions."""
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/_qto-shared-evidence/scripts"))
from qto_workflow_state import checkpoint, initial_state, inspect_workflow, merge_states


TOOLS = {"measure_pipe": "1" * 64, "measure_masonry": "2" * 64}


def native_quantity(doc, state, root, run_name):
    """A native result really references a retained, hashed detail file."""
    detail = root / ".qto-runs" / run_name / "detail.csv"
    detail.parent.mkdir(parents=True, exist_ok=True)
    detail.write_bytes(b"object,quantity\npipe-1,12\n")
    sha = hashlib.sha256(detail.read_bytes()).hexdigest()
    (root / "pipe-quantity.json").write_text(json.dumps({
        "quantity": 12, "detail_csv": {"path": str(detail), "sha256": sha}}), encoding="utf-8")
    state = checkpoint(doc, state, "quantity", root, "program", "Native calculation and detail saved", TOOLS,
                       expected_task_fingerprint=inspect(doc, state, root)["tasks"]["quantity"]["fingerprint"],
                       execution_files={str(detail): sha})
    return state, detail


@pytest.fixture
def project(tmp_path):
    for name, text in (("pipe.dxf", "drawing revision A"),
                       ("masonry.dxf", "independent masonry drawing"),
                       ("rule.txt", "length follows the documented route; revision 1")):
        (tmp_path / name).write_text(text, encoding="utf-8")
    doc = {
        "schema_version": "qto-project-workflow/1.0",
        "project_id": "building-A", "task_id": "takeoff-01",
        "revision_id": "revision-1", "task_mode": "production",
        "artifacts": [
            {"id": "S", "kind": "source", "path": "pipe.dxf"},
            {"id": "M", "kind": "source", "path": "masonry.dxf"},
            {"id": "R", "kind": "rule", "path": "rule.txt"},
            {"id": "I", "kind": "index", "path": "pipe-index.json"},
            {"id": "Q", "kind": "output", "path": "pipe-quantity.json"},
            {"id": "V", "kind": "review", "path": "pipe-review.json"},
            {"id": "MQ", "kind": "output", "path": "masonry-quantity.json"},
        ],
        "work_packages": [
            {"id": "pipe", "owner_skill": "qto-pipe-networks", "scope_ids": ["floor-1"]},
            {"id": "masonry", "owner_skill": "qto-masonry-secondary", "scope_ids": ["wall-1"]},
        ],
        "scopes": [
            {"id": "floor-1", "title": "First floor piping", "basis_refs": ["S"],
             "inventory_artifact_ids": ["I"], "required_task_ids": ["read", "quantity", "review"],
             "bounds": [0, 0, 100, 100]},
            {"id": "wall-1", "title": "Independent wall", "basis_refs": ["M"],
             "inventory_artifact_ids": [], "required_task_ids": ["masonry"]},
        ],
        "tasks": [
            {"id": "read", "work_package_id": "pipe", "title": "Read source objects",
             "stage": "read", "actor": "model", "depends_on": [], "input_ids": ["S"], "output_ids": ["I"]},
            {"id": "quantity", "work_package_id": "pipe", "title": "Measure pipe",
             "stage": "calculate", "actor": "program", "depends_on": [], "input_ids": ["I", "R"], "output_ids": ["Q"],
             "action": {"tool": "measure_pipe", "arguments": {"input": "I", "rule": "R", "output": "Q"}}},
            {"id": "review", "work_package_id": "pipe", "title": "Review quantities",
             "stage": "review", "actor": "user", "depends_on": [], "input_ids": ["Q"], "output_ids": ["V"]},
            {"id": "masonry", "work_package_id": "masonry", "title": "Measure wall",
             "stage": "calculate", "actor": "program", "depends_on": [], "input_ids": ["M"], "output_ids": ["MQ"],
             "action": {"tool": "measure_masonry", "arguments": {"input": "M", "output": "MQ"}}},
        ],
    }
    return doc, tmp_path


def inspect(doc, state, root, tools=TOOLS):
    return inspect_workflow(doc, state, root, tool_fingerprints=tools)


def complete(doc, state, root, task_id, note="Current objects were processed", tools=TOOLS):
    """Write a real deterministic output, as an external task runner would."""
    task = next(t for t in doc["tasks"] if t["id"] == task_id)
    before = inspect(doc, state, root, tools)["tasks"][task_id]["fingerprint"]
    for aid in task["output_ids"]:
        artifact = next(a for a in doc["artifacts"] if a["id"] == aid)
        path = root / artifact["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"artifact_id": aid, "quantity": 12,
                                    "official_adoption": False}), encoding="utf-8")
    return checkpoint(doc, state, task_id, root, actor=task["actor"], note=note,
                      tool_fingerprints=tools, expected_task_fingerprint=before)


def completed_project(doc, root):
    state = initial_state(doc)
    for task_id in ("read", "quantity", "review", "masonry"):
        state = complete(doc, state, root, task_id)
    return state


def test_existing_files_and_completed_flags_do_not_replace_receipts(project):
    doc, root = project
    doc["tasks"][0]["completed"] = True
    (root / "pipe-index.json").write_text("old index", encoding="utf-8")
    state = initial_state(doc)
    result = inspect(doc, state, root)
    assert result["task_statuses"] == {"read": "ready", "masonry": "ready", "quantity": "pending", "review": "pending"}
    assert result["artifacts"]["I"]["status"] == "stale"
    assert result["artifacts"]["S"]["status"] == "current"
    with pytest.raises(ValueError, match="cannot checkpoint"):
        checkpoint(doc, state, "quantity", root, "program", "Cannot skip the read task", TOOLS)


def test_actual_sha_and_self_produced_inventory_keep_checkpoint_stable(project):
    doc, root = project
    doc["artifacts"][0]["sha256"] = "a" * 64  # A claim is not the file identity.
    before = initial_state(doc)
    result = inspect(doc, before, root)
    assert result["artifacts"]["S"]["sha256"] == hashlib.sha256((root / "pipe.dxf").read_bytes()).hexdigest()
    fingerprint = result["tasks"]["read"]["fingerprint"]
    state = complete(doc, before, root, "read")
    after = inspect(doc, state, root)
    assert after["task_statuses"]["read"] == "completed"
    assert after["tasks"]["read"]["fingerprint"] == fingerprint
    assert after["task_statuses"]["quantity"] == "ready"
    assert before["receipts"] == []
    assert state["business_adoption_allowed"] is False
    assert all(r["business_adoption_allowed"] is False for r in state["receipts"])


def test_source_change_invalidates_only_its_chain(project):
    doc, root = project
    state = completed_project(doc, root)
    (root / "pipe.dxf").write_text("drawing revision B", encoding="utf-8")
    result = inspect(doc, state, root)
    assert set(result["stale_task_ids"]) == {"read", "quantity", "review"}
    assert result["task_statuses"]["masonry"] == "completed"
    assert result["runnable_task_ids"] == ["read"]
    assert result["artifacts"]["S"]["status"] == "current"
    assert all(result["artifacts"][a]["status"] == "stale" for a in ("I", "Q", "V"))
    assert len(state["receipts"]) == 4


def test_same_number_with_changed_rule_invalidates_review_after_recalculation(project):
    doc, root = project
    state = completed_project(doc, root)
    previous_quantity = (root / "pipe-quantity.json").read_bytes()
    (root / "rule.txt").write_text("same route quantity, changed applicability and revision", encoding="utf-8")
    result = inspect(doc, state, root)
    assert set(result["stale_task_ids"]) == {"quantity", "review"}
    state = complete(doc, state, root, "quantity")
    after = inspect(doc, state, root)
    assert (root / "pipe-quantity.json").read_bytes() == previous_quantity
    assert after["task_statuses"]["quantity"] == "completed"
    assert after["task_statuses"]["review"] == "stale"
    assert after["tasks"]["review"]["can_run"] is True
    assert len(state["receipts"]) == 5


@pytest.mark.parametrize("artifact,affected", [("pipe-index.json", {"read", "quantity", "review"}),
                                                ("pipe-quantity.json", {"quantity", "review"})])
def test_output_tamper_invalidates_producer_and_consumers(project, artifact, affected):
    doc, root = project
    state = completed_project(doc, root)
    (root / artifact).write_text('{"quantity":999}', encoding="utf-8")
    assert set(inspect(doc, state, root)["stale_task_ids"]) == affected


def test_deleted_output_does_not_remain_current(project):
    doc, root = project
    state = completed_project(doc, root)
    (root / "pipe-index.json").unlink()
    result = inspect(doc, state, root)
    assert result["artifacts"]["I"]["status"] == "missing"
    assert set(result["stale_task_ids"]) == {"read", "quantity", "review"}


def test_changed_professional_input_and_tool_version_are_real_dependencies(project):
    doc, root = project
    (root / "semantic-input.json").write_text('{"DN":"50"}', encoding="utf-8")
    doc["artifacts"].append({"id": "semantic", "kind": "input", "path": "semantic-input.json"})
    doc["tasks"][1]["input_ids"].append("semantic")
    state = completed_project(doc, root)
    (root / "semantic-input.json").write_text('{"DN":"65"}', encoding="utf-8")
    assert set(inspect(doc, state, root)["stale_task_ids"]) == {"quantity", "review"}
    (root / "semantic-input.json").write_text('{"DN":"50"}', encoding="utf-8")
    changed_tools = {**TOOLS, "measure_pipe": "f" * 64, "unrelated_tool": "a" * 64}
    assert set(inspect(doc, state, root, changed_tools)["stale_task_ids"]) == {"quantity", "review"}
    unchanged_tools = {**TOOLS, "unrelated_tool": "b" * 64}
    assert all(v == "completed" for v in inspect(doc, state, root, unchanged_tools)["task_statuses"].values())


def test_expected_fingerprint_rejects_input_change_during_execution(project):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    expected = inspect(doc, state, root)["tasks"]["quantity"]["fingerprint"]
    (root / "pipe-quantity.json").write_text('{"quantity":12}', encoding="utf-8")
    (root / "rule.txt").write_text("updated while program was running", encoding="utf-8")
    with pytest.raises(ValueError, match="during execution"):
        checkpoint(doc, state, "quantity", root, "program", "finished", TOOLS, expected)
    assert len(state["receipts"]) == 1


@pytest.mark.parametrize("change", ["task", "owner", "physical_scope"])
def test_task_owner_and_physical_scope_changes_invalidate_local_results(project, change):
    doc, root = project
    state = completed_project(doc, root)
    if change == "task":
        doc["tasks"][1]["action"]["arguments"]["method"] = "revised-method"
        affected = {"quantity", "review"}
    elif change == "owner":
        doc["work_packages"][0]["owner_skill"] = "qto-duct-air-systems"
        affected = {"read", "quantity", "review"}
    else:
        doc["scopes"][0]["bounds"][2] = 120
        affected = {"read", "quantity", "review"}
    assert set(inspect(doc, state, root)["stale_task_ids"]) == affected


def test_revision_label_new_inventory_and_required_task_do_not_repeat_old_reads(project):
    doc, root = project
    state = completed_project(doc, root)
    doc["revision_id"] = "revision-2"
    doc["artifacts"].append({"id": "I2", "kind": "index", "path": "additional-index.json"})
    doc["scopes"][0]["inventory_artifact_ids"].append("I2")
    doc["scopes"][0]["required_task_ids"].append("extra-read")
    doc["tasks"].append({"id": "extra-read", "work_package_id": "pipe", "title": "Additional inspected family",
                          "stage": "read", "actor": "model", "input_ids": ["S"], "output_ids": ["I2"]})
    result = inspect(doc, state, root)
    assert result["task_statuses"]["extra-read"] == "ready"
    assert all(result["task_statuses"][key] == "completed" for key in ("read", "quantity", "review", "masonry"))


def test_unrelated_missing_package_does_not_invalidate_completed_work(project):
    doc, root = project
    state = completed_project(doc, root)
    doc["artifacts"] += [{"id": "X", "kind": "source", "path": "not-supplied.dxf"},
                          {"id": "XO", "kind": "index", "path": "not-yet-produced.json"}]
    doc["scopes"].append({"id": "new-scope", "title": "Additional building", "basis_refs": ["X"]})
    doc["work_packages"].append({"id": "new-package", "owner_skill": "qto-components-points", "scope_ids": ["new-scope"]})
    doc["tasks"].append({"id": "new-read", "title": "Read additional building", "work_package_id": "new-package",
                          "stage": "read", "actor": "model", "input_ids": ["X"], "output_ids": ["XO"]})
    result = inspect(doc, state, root)
    assert result["blocked_task_ids"] == ["new-read"]
    assert all(result["task_statuses"][key] == "completed" for key in ("read", "quantity", "review", "masonry"))


@pytest.mark.parametrize("field,value", [("project_id", "another-building"), ("task_id", "blind-assessment-02"),
                                         ("task_mode", "independent_assessment")])
def test_state_isolation_by_project_task_and_mode(project, field, value):
    doc, root = project
    state = completed_project(doc, root)
    other = copy.deepcopy(doc)
    other[field] = value
    with pytest.raises(ValueError, match="state " + field):
        inspect(other, state, root)
    # Rewriting only the state header cannot transfer previous receipts either.
    state[field] = value
    with pytest.raises(ValueError, match="another project/task/mode"):
        inspect(other, state, root)


def test_independent_assessment_can_resume_same_task_without_adopting_results(project):
    doc, root = project
    doc["task_mode"] = "independent_assessment"
    doc["task_id"] = "independent-original-drawings-01"
    state = completed_project(doc, root)
    restored = json.loads(json.dumps(state))
    assert all(v == "completed" for v in inspect(doc, restored, root)["task_statuses"].values())
    assert restored["business_adoption_allowed"] is False


@pytest.mark.parametrize("collection,index,field", [
    ("tasks", 0, "input_ids"), ("tasks", 0, "output_ids"), ("tasks", 0, "depends_on"),
    ("work_packages", 0, "scope_ids"), ("scopes", 0, "basis_refs"),
    ("scopes", 0, "inventory_artifact_ids"), ("scopes", 0, "required_task_ids"),
])
def test_unknown_references_are_rejected(project, collection, index, field):
    doc, root = project
    doc[collection][index][field] = ["unknown"]
    with pytest.raises(ValueError, match="unknown"):
        initial_state(doc)


@pytest.mark.parametrize("mode", ["explicit-cycle", "inferred-cycle", "self-input", "duplicate-producer"])
def test_invalid_task_graphs_are_rejected(project, mode):
    doc, root = project
    if mode == "explicit-cycle":
        doc["tasks"][0]["depends_on"] = ["review"]
    elif mode == "inferred-cycle":
        doc["tasks"][0]["input_ids"].append("Q")
    elif mode == "self-input":
        doc["tasks"][0]["input_ids"].append("I")
    else:
        duplicate = copy.deepcopy(doc["tasks"][0])
        duplicate["id"] = "duplicate"
        doc["tasks"].append(duplicate)
    with pytest.raises(ValueError, match="cycle|duplicate producer"):
        initial_state(doc)


@pytest.mark.parametrize("kind", ["source", "rule", "source_input"])
def test_tasks_cannot_write_readonly_artifacts(project, kind):
    doc, root = project
    doc["artifacts"][3]["kind"] = kind
    with pytest.raises(ValueError, match="cannot write"):
        initial_state(doc)


@pytest.mark.parametrize("kind", ["input", "shared_fact"])
def test_explicit_readonly_protects_external_inputs_and_shared_facts(project, kind):
    doc, root = project
    doc["artifacts"][3].update(kind=kind, read_only=True)
    with pytest.raises(ValueError, match="cannot write"):
        initial_state(doc)


def test_prepared_input_and_produced_shared_fact_feed_the_next_work_package(project):
    doc, root = project
    doc["artifacts"][3]["kind"] = "input"  # AI prepares the professional input.
    doc["artifacts"][4]["kind"] = "shared_fact"  # Its program publishes a shared fact.
    doc["tasks"][3]["input_ids"].append("Q")  # Another package consumes that fact.
    state = initial_state(doc)
    assert inspect(doc, state, root)["task_statuses"]["masonry"] == "pending"
    state = complete(doc, state, root, "read")
    result = inspect(doc, state, root)
    assert result["artifacts"]["I"]["status"] == "current"
    assert result["task_statuses"]["quantity"] == "ready"
    assert result["task_statuses"]["masonry"] == "pending"
    state = complete(doc, state, root, "quantity")
    result = inspect(doc, state, root)
    assert result["artifacts"]["Q"]["status"] == "current"
    assert result["tasks"]["masonry"]["depends_on"] == ["quantity"]
    assert result["task_statuses"]["masonry"] == "ready"
    state = complete(doc, state, root, "masonry")
    (root / "pipe-index.json").write_text('{"DN":"changed after preparation"}', encoding="utf-8")
    result = inspect(doc, state, root)
    assert all(result["task_statuses"][key] == "stale" for key in ("read", "quantity", "masonry"))


def test_duplicate_resolved_output_and_source_paths_are_rejected(project):
    doc, root = project
    doc["artifacts"][3]["path"] = "unused/../pipe.dxf"
    with pytest.raises(ValueError, match="duplicate resolved"):
        inspect(doc, initial_state(doc), root)


def test_hardlink_output_alias_cannot_overwrite_source(project):
    doc, root = project
    try:
        os.link(root / "pipe.dxf", root / "pipe-index.json")
    except OSError as exc:
        pytest.skip("hard links unavailable: " + str(exc))
    with pytest.raises(ValueError, match="alias the same file"):
        inspect(doc, initial_state(doc), root)


def test_artifact_action_reference_must_be_declared_as_input_or_output(project):
    doc, root = project
    doc["tasks"][1]["action"]["arguments"]["extra"] = "M"
    with pytest.raises(ValueError, match="undeclared input/output"):
        initial_state(doc)


def test_real_output_and_matching_actor_required_for_checkpoint(project):
    doc, root = project
    state = initial_state(doc)
    with pytest.raises(ValueError, match="actual output"):
        checkpoint(doc, state, "read", root, "model", "Nothing written", TOOLS)
    with pytest.raises(ValueError, match="actor"):
        checkpoint(doc, state, "read", root, "user", "Wrong task role", TOOLS)
    state = complete(doc, state, root, "read")
    with pytest.raises(ValueError, match="actor"):
        checkpoint(doc, state, "quantity", root, "model", "Cannot claim program ran", TOOLS)
    doc["tasks"][0]["output_ids"] = []
    assert inspect(doc, initial_state(doc), root)["task_statuses"]["read"] == "blocked"
    with pytest.raises(ValueError, match="no real output"):
        checkpoint(doc, initial_state(doc), "read", root, "model", "Text flag is insufficient", TOOLS)


def test_missing_registered_tool_version_blocks_only_that_program(project):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    result = inspect(doc, state, root, {"measure_masonry": TOOLS["measure_masonry"]})
    assert result["task_statuses"]["quantity"] == "blocked"
    assert result["task_statuses"]["masonry"] == "ready"


def test_parallel_branches_merge_distinct_completed_tasks(project):
    doc, root = project
    base = initial_state(doc)
    left = complete(doc, base, root, "read")
    left = complete(doc, left, root, "quantity")
    right = complete(doc, base, root, "masonry")
    left_before, right_before = copy.deepcopy(left), copy.deepcopy(right)
    merged = merge_states(doc, left, right, root, TOOLS)
    result = inspect(doc, merged, root)
    assert result["task_statuses"] == {"read": "completed", "masonry": "completed", "quantity": "completed", "review": "ready"}
    assert len(merged["receipts"]) == 3
    assert left == left_before and right == right_before
    assert merged == merge_states(doc, right, left, root, TOOLS)


def test_parallel_same_task_receipts_conflict_even_when_output_bytes_match(project):
    doc, root = project
    base = initial_state(doc)
    left = complete(doc, base, root, "read", note="Branch A reviewed the source")
    right = complete(doc, base, root, "read", note="Branch B reviewed differently")
    with pytest.raises(ValueError, match="divergent receipts"):
        merge_states(doc, left, right, root, TOOLS)


def test_parallel_same_task_conflict_not_hidden_by_overwritten_output(project):
    doc, root = project
    base = initial_state(doc)
    left = complete(doc, base, root, "read", note="First branch")
    (root / "pipe-index.json").write_text("second branch index", encoding="utf-8")
    right = checkpoint(doc, base, "read", root, "model", "Second branch", TOOLS)
    assert inspect(doc, left, root)["task_statuses"]["read"] == "stale"
    with pytest.raises(ValueError, match="divergent receipts"):
        merge_states(doc, left, right, root, TOOLS)


def test_fast_forward_merge_accepts_descendant_receipt(project):
    doc, root = project
    older = complete(doc, initial_state(doc), root, "read")
    newer = complete(doc, older, root, "read", note="Repeated current read")
    merged = merge_states(doc, older, newer, root, TOOLS)
    assert merged["task_receipts"]["read"] == newer["task_receipts"]["read"]
    assert len(merged["receipts"]) == 2


def test_merge_keeps_stale_history_without_accepting_it_as_progress(project):
    doc, root = project
    state = completed_project(doc, root)
    (root / "pipe.dxf").write_text("changed drawing", encoding="utf-8")
    merged = merge_states(doc, state, initial_state(doc), root, TOOLS)
    assert len(merged["receipts"]) == 4
    assert set(merged["task_receipts"]) == {"masonry"}
    assert set(inspect(doc, merged, root)["stale_task_ids"]) == {"read", "quantity", "review"}


def test_superseded_receipt_is_not_resurrected_when_input_is_reverted(project):
    doc, root = project
    original = (root / "pipe.dxf").read_bytes()
    state = complete(doc, initial_state(doc), root, "read")
    (root / "pipe.dxf").write_text("revised drawing", encoding="utf-8")
    state = complete(doc, state, root, "read")
    (root / "pipe.dxf").write_bytes(original)
    assert inspect(doc, state, root)["task_statuses"]["read"] == "stale"
    assert len(state["receipts"]) == 2


def test_attempts_and_extension_data_survive_checkpoint_and_merge(project):
    doc, root = project
    base = initial_state(doc)
    left = complete(doc, base, root, "read")
    fp = inspect(doc, left, root)["tasks"]["quantity"]["fingerprint"]
    attempt = {"task_id": "quantity", "task_fingerprint": fp, "tool": "measure_pipe",
               "success": False, "error": "Fix the missing route scope", "recorded_at": datetime.now(timezone.utc).isoformat(),
               "run_directory": "runs/failed-attempt"}
    left["attempts"].append(attempt)
    left["review_context"] = {"pending_instruction": "Keep user review history"}
    assert inspect(doc, left, root)["tasks"]["quantity"]["last_error"] == attempt["error"]
    right = complete(doc, base, root, "masonry")
    merged = merge_states(doc, left, right, root, TOOLS)
    assert merged["attempts"] == [attempt]
    assert merged["review_context"] == left["review_context"]
    finished = complete(doc, merged, root, "quantity")
    assert finished["attempts"] == [attempt]
    assert finished["review_context"] == left["review_context"]
    assert "last_error" not in inspect(doc, finished, root)["tasks"]["quantity"]


def test_modified_receipt_and_formal_progress_claim_are_rejected(project):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    modified = copy.deepcopy(state)
    modified["receipts"][0]["note"] = "Changed the historical conclusion"
    with pytest.raises(ValueError, match="modified receipt"):
        inspect(doc, modified, root)
    state["business_adoption_allowed"] = True
    with pytest.raises(ValueError, match="adoption"):
        inspect(doc, state, root)


def test_logical_dependency_propagates_changed_output_even_without_direct_input(project):
    doc, root = project
    doc["tasks"][2]["input_ids"] = []
    doc["tasks"][2]["depends_on"] = ["quantity"]
    state = completed_project(doc, root)
    (root / "pipe-quantity.json").write_text('{"quantity":13}', encoding="utf-8")
    state = checkpoint(doc, state, "quantity", root, "program", "Program produced a different result", TOOLS)
    assert inspect(doc, state, root)["task_statuses"]["review"] == "stale"


def test_removed_task_receipts_stay_in_history(project):
    doc, root = project
    state = completed_project(doc, root)
    doc["tasks"] = [t for t in doc["tasks"] if t["id"] != "review"]
    doc["scopes"][0]["required_task_ids"].remove("review")
    result = inspect(doc, state, root)
    assert result["removed_task_ids"] == ["review"]
    assert result["historical_receipt_count"] == 4


@pytest.mark.parametrize("change", ["remove", "tamper"])
def test_lost_native_run_evidence_invalidates_its_chain_and_restoring_bytes_recovers(project, change):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    state, detail = native_quantity(doc, state, root, "run-native-a")
    state = complete(doc, state, root, "review")
    state = complete(doc, state, root, "masonry")
    saved = detail.read_bytes()
    published = (root / "pipe-quantity.json").read_bytes()
    if change == "remove":
        detail.unlink()
    else:
        detail.write_bytes(b"object,quantity\npipe-1,99\n")
    result = inspect(doc, state, root)
    assert result["task_statuses"] == {"read": "completed", "masonry": "completed",
                                       "quantity": "stale", "review": "stale"}
    assert result["artifacts"]["Q"]["status"] == "stale"
    assert any("execution evidence" in text for text in result["tasks"]["quantity"]["reasons"])
    assert (root / "pipe-quantity.json").read_bytes() == published
    with pytest.raises(ValueError, match="execution evidence"):
        checkpoint(doc, state, "quantity", root, "program", "Cannot omit missing native evidence", TOOLS)
    detail.write_bytes(saved)
    assert set(inspect(doc, state, root)["task_statuses"].values()) == {"completed"}


def test_new_execution_receipt_preserves_old_evidence_history_without_requiring_old_files(project):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    state, old_detail = native_quantity(doc, state, root, "run-native-a")
    old_receipt = copy.deepcopy(state["receipts"][-1])
    (root / "rule.txt").write_text("Rule edition 2; the arithmetic is unchanged", encoding="utf-8")
    state, new_detail = native_quantity(doc, state, root, "run-native-b")
    old_detail.unlink()
    assert inspect(doc, state, root)["task_statuses"]["quantity"] == "completed"
    assert old_receipt in state["receipts"]
    assert state["receipts"][-1]["previous_receipt_id"] == old_receipt["receipt_id"]
    assert set(state["receipts"][-1]["execution_files"]) == {str(new_detail)}
    with pytest.raises(ValueError, match="cannot discard"):
        checkpoint(doc, state, "quantity", root, "program", "Cannot clear evidence binding", TOOLS,
                   execution_files={})


def test_merge_accepts_only_receipts_whose_retained_run_evidence_still_exists(project):
    doc, root = project
    base = complete(doc, initial_state(doc), root, "read")
    left, detail = native_quantity(doc, base, root, "run-native-branch")
    right = complete(doc, base, root, "masonry")
    saved = detail.read_bytes()
    detail.unlink()
    merged = merge_states(doc, left, right, root, TOOLS)
    assert "quantity" not in merged["task_receipts"]
    assert "masonry" in merged["task_receipts"]
    assert left["receipts"][-1] in merged["receipts"]
    detail.write_bytes(saved)
    restored = merge_states(doc, left, right, root, TOOLS)
    assert restored["task_receipts"]["quantity"] == left["task_receipts"]["quantity"]
    assert inspect(doc, restored, root)["task_statuses"]["quantity"] == "completed"


def test_execution_file_hash_is_verified_and_receipt_binds_the_reference(project):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    state, detail = native_quantity(doc, state, root, "run-native-identity")
    with pytest.raises(ValueError, match="execution evidence was modified"):
        checkpoint(doc, state, "quantity", root, "program", "Wrong claimed detail hash", TOOLS,
                   execution_files={str(detail): "0" * 64})
    changed = copy.deepcopy(state)
    changed["receipts"][-1]["execution_files"][str(detail)] = "0" * 64
    with pytest.raises(ValueError, match="modified receipt"):
        inspect(doc, changed, root)


def test_execution_evidence_cannot_relabel_consumed_input_as_saved_output(project):
    doc, root = project
    input_file = root / ".qto-runs/run-older-source/source.txt"
    input_file.parent.mkdir(parents=True)
    input_file.write_bytes(b"actual older source evidence")
    doc["artifacts"].append({"id": "extra-source", "kind": "source", "path": str(input_file)})
    doc["tasks"][1]["input_ids"].append("extra-source")
    state = complete(doc, initial_state(doc), root, "read")
    (root / "pipe-quantity.json").write_text('{"quantity":12}', encoding="utf-8")
    with pytest.raises(ValueError, match="consumed input"):
        checkpoint(doc, state, "quantity", root, "program", "Not a new execution output", TOOLS,
                   execution_files={str(input_file): hashlib.sha256(input_file.read_bytes()).hexdigest()})


def test_execution_evidence_requires_program_and_local_runner_output_directory(project):
    doc, root = project
    state = complete(doc, initial_state(doc), root, "read")
    state, detail = native_quantity(doc, state, root, "run-native-local")
    binding = {str(detail): hashlib.sha256(detail.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="only a program"):
        checkpoint(doc, state, "read", root, "model", "Cannot manufacture program evidence", TOOLS,
                   execution_files=binding)
    source = root / "pipe.dxf"
    with pytest.raises(ValueError, match="invalid execution evidence"):
        checkpoint(doc, state, "quantity", root, "program", "Cannot use source as run evidence", TOOLS,
                   execution_files={str(source): hashlib.sha256(source.read_bytes()).hexdigest()})


def test_model_runtime_revision_invalidates_preparation_chain_but_not_independent_user_decision(project):
    doc, root = project
    doc["artifacts"].append({"id": "choice", "kind": "decision", "path": "choice.json"})
    doc["tasks"].append({"id": "choice", "work_package_id": "pipe", "title": "Existing project choice",
                         "stage": "review", "actor": "user", "depends_on": [],
                         "input_ids": ["R"], "output_ids": ["choice"]})
    versions = {**TOOLS, "model:qto-pipe-networks": "a" * 64}
    state = initial_state(doc)
    for task_id in ("read", "quantity", "review", "masonry", "choice"):
        state = complete(doc, state, root, task_id, tools=versions)
    changed_model = inspect(doc, state, root, {**versions, "model:qto-pipe-networks": "b" * 64})
    assert changed_model["task_statuses"] == {"choice": "completed", "read": "stale", "masonry": "completed",
                                              "quantity": "stale", "review": "stale"}
    changed_program = inspect(doc, state, root, {**versions, "measure_pipe": "c" * 64})
    assert changed_program["task_statuses"]["read"] == "completed"
    assert changed_program["task_statuses"]["choice"] == "completed"
    assert changed_program["task_statuses"]["quantity"] == "stale"
