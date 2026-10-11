"""Single-object feedback must retain the clicked view, not every physical alias."""
import copy

import pytest

import test_workflow_integration as integration
from qto_workflow_review import _selected_catalog_keys


def multi_quantity_wall(root, cross_professional=False):
    document, state, template = integration.reviewed_wall_batch(root, count=4)
    owner = "faces" if cross_professional else "walls"
    professional = "qto-surfaces-envelope" if cross_professional else "qto-masonry-secondary"
    if cross_professional:
        document["work_packages"].append(dict(id=owner, owner_skill=professional, scope_ids=["walls"]))
    target = copy.deepcopy(document["quantity_targets"][0])
    target.update(id="W00-area", work_package_id=owner, quantity_kind="net_wall_area", unit="m2",
                  reason="同一墙的实际净面积几何事实另列量项，供相应专业复核，不与体积合并")
    document["quantity_targets"].append(target)
    claim = copy.deepcopy(document["quantity_claims"][0])
    claim.update(id="W00-area-claim", target_id="W00-area", result_pointer="/items/0/net_area_m2",
                 owner_work_package_id=owner)
    document["quantity_claims"].append(claim)
    document["review_bindings"].append(dict(id="W00-area-binding", catalog_key="W00-area-view", target_ids=["W00-area"]))
    obj = copy.deepcopy(template["objects"][0])
    obj.update(catalog_key="W00-area-view", title="W00 墙净面积复核", professional=professional)
    template["objects"].append(obj)
    next(a for a in document["artifacts"] if a["id"] == "view")["path"] = "two-quantity-view.json"
    execution, quantities, _ = integration.workflow.inspect(document, state, root)
    assert {r["target_id"]: r["quantity"] for r in quantities["result_index"]}["W00-area"] == 13
    view = integration.bind_view(document, execution, quantities, template)
    integration.put(root, "two-quantity-view.json", view)
    state = integration.state_engine.checkpoint(document, state, "build-view", root, "model",
        "同一墙体的体积和净面积分别对应实际复核对象", integration.workflow.tool_versions(document))
    return document, state, view


def read_single_feedback(root, document, state, view, catalog_key):
    selected = next(obj for obj in view["objects"] if obj["catalog_key"] == catalog_key)
    feedback = integration.empty_feedback(view)
    feedback["annotations"]["single-note"] = dict(
        object_id=selected["id"], object_ids=[selected["id"]],
        confirmed_result="仅复核当前点击量项的方法，不更改此实物的其他量项",
        reason="同一实物的其他视图和专业不在本条意见范围内", instruction="review_method",
        scope="仅此对象", updated_at="2026-10-11T18:00:00+00:00", result_fingerprint=selected["result_fingerprint"],
        object_snapshot=dict(catalog_key=selected["catalog_key"], object_id=selected["id"],
            professional=selected["professional"], scene_id=selected["scene_id"],
            quantities=copy.deepcopy(selected["quantities"]), attributes=copy.deepcopy(selected["attributes"]),
            result_fingerprint=selected["result_fingerprint"]))
    for artifact in document["artifacts"]:
        if artifact["id"] == "feedback":
            artifact["path"] = "single-feedback.json"
        elif artifact["id"] == "handoff":
            artifact["path"] = "single-handoff.json"
    integration.put(root, "single-feedback.json", feedback)
    state, evidence = integration.workflow.run_one(document, state, "read-feedback", root)
    assert evidence["returncode"] == 0  # Actual registered feedback CLI.
    return state, integration.workflow.read(root / "single-handoff.json")


@pytest.mark.parametrize("cross_professional", [False, True])
@pytest.mark.parametrize("catalog_key,target_id", [("W00-view", "W00-volume"), ("W00-area-view", "W00-area")])
def test_real_single_feedback_only_revises_clicked_view_quantity(tmp_path, cross_professional, catalog_key, target_id):
    document, state, view = multi_quantity_wall(tmp_path, cross_professional)
    state, handoff = read_single_feedback(tmp_path, document, state, view, catalog_key)
    assert len(handoff["actions"]) == 1
    assert handoff["actions"][0]["object_catalog_key"] == catalog_key
    execution, _, _ = integration.workflow.inspect(document, state, tmp_path)
    patch = integration.feedback_patch(document, execution, "handoff", "a" * 64)
    assert [target["id"] for target in patch["upsert"]["quantity_targets"]] == [target_id]
    assert {tid for row in patch["upsert"]["review_imports"] for tid in row["target_ids"]} == {target_id}
    interpreted = [task for task in patch["upsert"]["tasks"] if task["stage"] == "review"]
    assert len(interpreted) == 1 and interpreted[0]["selected_target_ids"] == [target_id]
    amended = integration.workflow.amend(document, patch, "a" * 64)
    _, quantities, _ = integration.workflow.inspect(amended, state, tmp_path)
    expected = {"W00-volume": 2.6, "W00-area": 13, "W01-volume": 2.6, "W02-volume": 2.6, "W03-volume": 2.6}
    expected.pop(target_id)
    assert {row["target_id"]: row["quantity"] for row in quantities["result_index"]} == expected


def test_older_handoff_recovers_exact_single_key_from_embedded_actual_snapshot(tmp_path):
    document, state, view = multi_quantity_wall(tmp_path, cross_professional=True)
    _, handoff = read_single_feedback(tmp_path, document, state, view, "W00-view")
    action = copy.deepcopy(handoff["actions"][0])
    del action["object_catalog_key"]  # The prior reader omitted only this field.
    objects = {obj["catalog_key"]: obj for obj in view["objects"]}
    selected, unresolved = _selected_catalog_keys(action, handoff, objects)
    assert selected == {"W00-view"} and unresolved == []


@pytest.mark.parametrize("objects", [
    {"volume-view": dict(id="W1", professional="qto-masonry-secondary", attributes=dict(workflow_target_ids=["volume"])),
     "finish-view": dict(id="W1", professional="qto-surfaces-envelope", attributes=dict(workflow_target_ids=["finish-area"]))},
    {"one-view": dict(id="W1", attributes=dict(workflow_target_ids=["volume", "area"]))},
])
def test_no_snapshot_never_expands_ambiguous_physical_id(objects):
    selected, unresolved = _selected_catalog_keys(dict(object_ids=["W1"]), {}, objects)
    assert selected == set()
    assert unresolved == [dict(object_id="W1", candidate_catalog_keys=sorted(objects))]


def test_legacy_single_view_single_quantity_mapping_remains_compatible():
    objects = {"W1-view": dict(id="W1", attributes=dict(workflow_target_ids=["W1-volume"]))}
    selected, unresolved = _selected_catalog_keys(dict(object_ids=["W1"]), {}, objects)
    assert selected == {"W1-view"} and unresolved == []


def test_explicit_all_excluded_group_does_not_fall_back_to_physical_id():
    objects = {"W1-view": dict(id="W1", attributes=dict(workflow_target_ids=["W1-volume"]))}
    selected, unresolved = _selected_catalog_keys(
        dict(object_ids=["W1"], selected_object_keys=[], excluded_object_keys=["W1-view"]), {}, objects)
    assert selected == set() and unresolved == []
