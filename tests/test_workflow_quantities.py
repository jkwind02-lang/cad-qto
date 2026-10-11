"""Project ledger cases use real source-bound CAD indexes and native results.

These exercise accounting meaning and change propagation, not another geometry
or quantity calculator: the wall's 1.8 m3 and each 9 m2 face are known outputs.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "_qto-shared-evidence" / "scripts"))
import qto_recognition_workbench as workbench
from qto_workflow_quantities import reconcile


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def observed(path, status="current"):
    return dict(path=str(path.resolve()), exists=path.is_file(),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None, status=status)


def update_result(case, value):
    write_json(case["result_path"], value)
    case["artifacts"]["RESULT"] = observed(case["result_path"])


def run(case):
    answer = reconcile(case["document"], case["artifacts"], case["task_statuses"])
    # Reports must be ordinary finite JSON even when a native result is not.
    json.dumps(answer, ensure_ascii=False, allow_nan=False)
    return answer


@pytest.fixture
def wall(tmp_path):
    doc = ezdxf.new()
    doc.units = 4
    model = doc.modelspace()
    model.add_line((0, 0), (3000, 0))
    model.add_line((0, 5000), (3000, 5000))
    source_path = tmp_path / "wall.dxf"
    doc.saveas(source_path)
    source = {key: value for key, value in observed(source_path).items() if key in {"path", "sha256"}}
    index = workbench.build_index(source)
    index_path = write_json(tmp_path / "index.json", index)
    rule_path = tmp_path / "rule.txt"
    rule_path.write_text("试验墙长3m、高3m、厚0.2m，体积1.8m3；两面各9m2，按层和侧分别计量。", encoding="utf-8")
    native_result = {"wall": {
        "volume": {"quantity": 1.8, "unit": "m3", "quantity_level": "confirmed"},
        "face": {"quantity": 9.0, "unit": "m2", "quantity_level": "confirmed"}}}
    result_path = write_json(tmp_path / "result.json", native_result)
    artifact_paths = {"SRC": ("source", source_path), "RULE": ("rule", rule_path),
                      "INDEX": ("index", index_path), "RESULT": ("result", result_path)}
    object_ids = [row["object_id"] for row in index["objects"]]
    document = dict(schema_version="qto-project-workflow/1.0",
        artifacts=[dict(id=aid, kind=kind, path=str(path)) for aid, (kind, path) in artifact_paths.items()],
        scopes=[dict(id="WALL-SCOPE", title="试验墙平面、立面与做法范围", basis_refs=["SRC", "RULE"],
            inventory_artifact_ids=["INDEX"], required_task_ids=["read-plan", "read-elevation"])],
        work_packages=[dict(id="CONCRETE", owner_skill="qto-concrete-formwork", scope_ids=["WALL-SCOPE"]),
                       dict(id="SURFACES", owner_skill="qto-surfaces-envelope", scope_ids=["WALL-SCOPE"])],
        objects=[dict(id="WALL-1", scope_id="WALL-SCOPE", observations=[dict(artifact_id="INDEX", object_id=oid) for oid in object_ids],
            basis_refs=["SRC", "RULE"], reason="两条源图观察分别为该同一面墙的平面与立面，按轴线、尺寸和做法对应")],
        observation_dispositions=[dict(scope_id="WALL-SCOPE", artifact_id="INDEX", object_id=oid, disposition="included",
            physical_id="WALL-1", basis_refs=["SRC", "RULE"], reason="作为同一面墙的一个视图证据，不另算一面墙") for oid in object_ids],
        quantity_targets=[dict(id="VOLUME", scope_id="WALL-SCOPE", work_package_id="CONCRETE", physical_id="WALL-1", extent_id="whole-wall",
            quantity_kind="solid_volume", measurement_basis_id="wall-net-rule", scenario_id="base", unit="m3", quantity_role="project_rule",
            disposition="required", basis_refs=["RULE"], reason="墙体按净混凝土体积计量，与面层面积属于不同量项")],
        quantity_claims=[dict(id="CLAIM-VOLUME", target_id="VOLUME", artifact_id="RESULT", result_pointer="/wall/volume/quantity",
            quantity_level="confirmed", owner_work_package_id="CONCRETE")])
    return dict(document=document, artifacts={aid: observed(path) for aid, (_, path) in artifact_paths.items()},
                task_statuses={"read-plan": "completed", "read-elevation": "completed"}, native_result=native_result,
                source_path=source_path, index_path=index_path, index=index, result_path=result_path, object_ids=object_ids)


def add_face_targets(case):
    document = case["document"]
    for side in ("front", "back"):
        document["quantity_targets"].append(dict(id=f"FINISH-{side}", scope_id="WALL-SCOPE", work_package_id="SURFACES",
            physical_id="WALL-1", extent_id="whole-wall", quantity_kind="wall_finish_area", measurement_basis_id="wall-net-rule",
            scenario_id="base", unit="m2", quantity_role="project_rule", layer_id="plaster", side=side,
            disposition="required", basis_refs=["RULE"], reason=f"墙体{side}面抹灰，独立于混凝土体积和另一侧面层"))
        document["quantity_claims"].append(dict(id=f"CLAIM-{side}", target_id=f"FINISH-{side}", artifact_id="RESULT",
            result_pointer="/wall/face/quantity", quantity_level="confirmed", owner_work_package_id="SURFACES"))


def test_two_source_views_reconcile_to_one_physical_quantity(wall):
    # Caller-authored totals are not accepted as arithmetic inputs.
    wall["document"]["quantity_claims"][0]["quantity"] = 999
    wall["document"]["quantity_targets"][0]["quantity"] = 999
    answer = run(wall)
    assert len(answer["result_index"]) == 1
    entry = answer["result_index"][0]
    assert entry["quantity"] == pytest.approx(1.8)
    assert len(entry["observations"]) == 2
    assert entry["path"] == str(wall["result_path"])
    assert entry["sha256"] == observed(wall["result_path"])["sha256"]
    assert entry["result_pointer"] == "/wall/volume/quantity"
    assert entry["status"] == "working" and entry["review_state"] == "unreviewed"
    assert entry["formal_adoption"] is False
    assert answer["totals"][0]["quantity"] == pytest.approx(1.8)
    scope = answer["scope_status"][0]
    assert scope["inventory_count"] == scope["processed_observation_count"] == 2
    assert scope["status"] == "reconciled_declared_scope"
    assert scope["engineering_completeness_proven"] is False
    assert answer["unaccounted_targets"] == answer["unprocessed_observations"] == answer["ownership_conflicts"] == []


def test_unknown_unlocated_source_object_is_in_denominator(wall):
    doc = ezdxf.readfile(wall["source_path"])
    doc.modelspace().add_spline(fit_points=[(1e6, 1e6), (1e6 + 1, 1e6 + 3), (1e6 + 2, 1e6)])
    doc.saveas(wall["source_path"])
    source = observed(wall["source_path"])
    index = workbench.build_index({key: source[key] for key in ("path", "sha256")})
    spline = next(row for row in index["objects"] if row["entity_type"] == "SPLINE")
    assert spline["bbox"] is None  # A located-only denominator would omit it.
    write_json(wall["index_path"], index)
    wall["artifacts"]["INDEX"] = observed(wall["index_path"])
    wall["artifacts"]["SRC"] = observed(wall["source_path"])
    answer = run(wall)
    assert answer["scope_status"][0]["inventory_count"] == 3
    assert answer["scope_status"][0]["processed_observation_count"] == 2
    assert answer["unprocessed_observations"][0]["object_id"] == spline["object_id"]
    assert answer["scope_status"][0]["status"] == "pending"
    assert answer["totals"][0]["quantity"] == pytest.approx(1.8)  # Known subtotal survives.
    assert any(action["actor"] == "model" and action["code"] == "process_observation" for action in answer["next_actions"])


def test_wall_volume_and_two_distinct_finish_sides_coexist(wall):
    add_face_targets(wall)
    answer = run(wall)
    assert len(answer["result_index"]) == 3
    assert answer["ownership_conflicts"] == []
    quantities = {(row["quantity_kind"], row["side"], row["unit"]): row["quantity"] for row in answer["totals"]}
    assert quantities == {("solid_volume", None, "m3"): 1.8,
                          ("wall_finish_area", "front", "m2"): 9.0,
                          ("wall_finish_area", "back", "m2"): 9.0}
    assert {row["owner_skill"] for row in answer["totals"]} == {"qto-concrete-formwork", "qto-surfaces-envelope"}


def test_cross_package_duplicate_withholds_both_claims(wall):
    duplicate = copy.deepcopy(wall["document"]["quantity_targets"][0])
    duplicate.update(id="VOLUME-OTHER", work_package_id="SURFACES")
    wall["document"]["quantity_targets"].append(duplicate)
    wall["document"]["quantity_claims"].append(dict(id="CLAIM-OTHER", target_id=duplicate["id"], artifact_id="RESULT",
        result_pointer="/wall/volume/quantity", quantity_level="confirmed", owner_work_package_id="SURFACES"))
    answer = run(wall)
    assert answer["result_index"] == answer["totals"] == []
    conflict = next(row for row in answer["ownership_conflicts"] if row["code"] == "duplicate_quantity_target")
    assert set(conflict["target_ids"]) == {"VOLUME", "VOLUME-OTHER"}
    assert len(answer["unaccounted_targets"]) == 2


def test_different_unit_cannot_evade_same_actual_quantity_conflict(wall):
    duplicate = copy.deepcopy(wall["document"]["quantity_targets"][0])
    duplicate.update(id="VOLUME-MM3", unit="mm3")
    wall["document"]["quantity_targets"].append(duplicate)
    wall["native_result"]["wall"]["volume_mm"] = dict(quantity=1_800_000_000, unit="mm3", quantity_level="confirmed")
    update_result(wall, wall["native_result"])
    wall["document"]["quantity_claims"].append(dict(id="CLAIM-MM3", target_id=duplicate["id"], artifact_id="RESULT",
        result_pointer="/wall/volume_mm/quantity", quantity_level="confirmed", owner_work_package_id="CONCRETE"))
    answer = run(wall)
    assert answer["result_index"] == answer["totals"] == []
    assert any(row["code"] == "duplicate_quantity_target" and set(row["units"]) == {"m3", "mm3"}
               for row in answer["ownership_conflicts"])


@pytest.mark.parametrize("change", ["changed", "stale", "missing"])
def test_changed_or_unavailable_result_never_enters_total(wall, change):
    if change == "changed":
        wall["native_result"]["wall"]["volume"]["quantity"] = 88.0
        write_json(wall["result_path"], wall["native_result"])  # Keep executor's old hash.
    elif change == "stale":
        wall["artifacts"]["RESULT"]["status"] = "stale"
    else:
        wall["result_path"].unlink()
    answer = run(wall)
    assert answer["result_index"] == answer["totals"] == []
    assert len(answer["unaccounted_targets"]) == 1
    assert answer["scope_status"][0]["status"] == "pending"


def test_updated_actual_result_is_read_and_hash_provenance_changes(wall):
    previous = run(wall)["result_index"][0]
    wall["native_result"]["wall"]["volume"]["quantity"] = 2.4
    update_result(wall, wall["native_result"])
    current = run(wall)["result_index"][0]
    assert previous["quantity"] == 1.8 and current["quantity"] == 2.4
    assert previous["sha256"] != current["sha256"]


def test_required_drawing_task_cannot_be_replaced_by_existing_subtotal(wall):
    wall["task_statuses"]["read-elevation"] = "pending"
    answer = run(wall)
    assert answer["totals"][0]["quantity"] == pytest.approx(1.8)
    scope = answer["scope_status"][0]
    assert scope["status"] == "pending"
    assert scope["required_task_statuses"]["read-elevation"] == "pending"
    assert any(action["code"] == "complete_required_task" and action["task_id"] == "read-elevation" for action in answer["next_actions"])


@pytest.mark.parametrize("value", [None, True, False, float("nan"), float("inf"), float("-inf"), "1.8", []])
def test_null_bool_and_nonfinite_native_results_are_not_zero(wall, value):
    wall["native_result"]["wall"]["volume"]["quantity"] = value
    update_result(wall, wall["native_result"])
    answer = run(wall)
    assert answer["result_index"] == answer["totals"] == []
    assert answer["unaccounted_targets"][0]["target_id"] == "VOLUME"
    assert answer["scope_status"][0]["accounted_target_count"] == 0


def test_actual_zero_is_retained_without_claiming_engineering_completeness(wall):
    wall["native_result"]["wall"]["volume"]["quantity"] = 0
    update_result(wall, wall["native_result"])
    answer = run(wall)
    assert answer["result_index"][0]["quantity"] == answer["totals"][0]["quantity"] == 0
    assert answer["engineering_completeness_proven"] is False


@pytest.mark.parametrize("role,allow_negative,accepted", [
    ("project_rule", False, False), ("geometric", True, False),
    ("procurement_reference", True, False), ("project_rule", True, True)])
def test_negative_is_only_explicit_signed_project_rule_quantity(wall, role, allow_negative, accepted):
    target = wall["document"]["quantity_targets"][0]
    target.update(quantity_role=role, allow_negative=allow_negative)
    target["reason"] = "本规则将已确认拆除的净工程量作为有符号调整项，负号表示扣回"
    wall["native_result"]["wall"]["volume"]["quantity"] = -1.8
    update_result(wall, wall["native_result"])
    answer = run(wall)
    assert bool(answer["result_index"]) is accepted
    if accepted:
        assert answer["totals"][0]["quantity"] == -1.8
    else:
        assert answer["unaccounted_targets"]


def test_native_scenario_level_cannot_be_upgraded_by_claim(wall):
    wall["native_result"]["wall"]["volume"]["quantity_level"] = "方案暂估量"
    update_result(wall, wall["native_result"])
    answer = run(wall)
    entry = answer["result_index"][0]
    assert entry["declared_quantity_level"] == "confirmed"
    assert entry["quantity_level"] == answer["totals"][0]["quantity_level"] == "scenario"
    assert entry["formal_adoption"] is False


def test_unresolved_native_level_cannot_be_counted(wall):
    wall["native_result"]["wall"]["volume"]["quantity_level"] = "未决量"
    update_result(wall, wall["native_result"])
    answer = run(wall)
    assert answer["totals"] == [] and answer["unaccounted_targets"]


def test_level_pointer_cannot_bypass_native_scenario_bucket(wall):
    update_result(wall, {"length_summary": {"W": {"方案暂估量": {"DN32": 5.5}}}, "other_level": "confirmed", "unit": "m"})
    target = wall["document"]["quantity_targets"][0]
    target.update(unit="m", quantity_kind="pipe_length")
    wall["document"]["quantity_claims"][0].update(result_pointer="/length_summary/W/方案暂估量/DN32", level_pointer="/other_level")
    answer = run(wall)
    assert answer["result_index"][0]["quantity_level"] == "scenario"


def test_native_unit_mismatch_requires_real_conversion(wall):
    wall["document"]["quantity_targets"][0]["unit"] = "mm3"
    answer = run(wall)
    assert answer["totals"] == []
    assert "单位" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_same_observation_under_an_index_alias_cannot_be_two_objects(wall):
    document = wall["document"]
    document["artifacts"].append(dict(id="INDEX-ALIAS", kind="index", path=str(wall["index_path"])))
    wall["artifacts"]["INDEX-ALIAS"] = observed(wall["index_path"])
    document["scopes"][0]["inventory_artifact_ids"].append("INDEX-ALIAS")
    document["objects"].append(dict(id="WALL-2", scope_id="WALL-SCOPE",
        observations=[dict(artifact_id="INDEX-ALIAS", object_id=wall["object_ids"][0])],
        basis_refs=["SRC"], reason="调用方暂将同一源线解释为另一实物，尚未给出部位拆分"))
    answer = run(wall)
    assert answer["scope_status"][0]["inventory_count"] == 2  # Aliased files do not double the denominator.
    assert answer["totals"] == []
    assert any(row["code"] == "source_observation_multiple_objects" and set(row["physical_ids"]) == {"WALL-1", "WALL-2"}
               for row in answer["ownership_conflicts"])


def test_source_hash_spelling_cannot_turn_one_observation_into_two_objects(wall):
    document = wall["document"]
    alternate_index = copy.deepcopy(wall["index"])
    alternate_index["source"]["sha256"] = alternate_index["source"]["sha256"].upper()
    alternate_index["index_sha256"] = workbench.index_digest(alternate_index)
    # Uppercase source SHA is accepted by the existing source verifier.  It
    # describes the same bytes, not a separate drawing or physical observation.
    workbench._verified_index(alternate_index)
    alternate_path = write_json(wall["index_path"].with_name("uppercase-index.json"), alternate_index)
    document["artifacts"].append(dict(id="UPPER-INDEX", kind="index", path=str(alternate_path)))
    wall["artifacts"]["UPPER-INDEX"] = observed(alternate_path)
    document["scopes"][0]["inventory_artifact_ids"].append("UPPER-INDEX")
    document["objects"].append(dict(id="WALL-2", scope_id="WALL-SCOPE",
        observations=[dict(artifact_id="UPPER-INDEX", object_id=oid) for oid in wall["object_ids"]],
        basis_refs=["SRC", "RULE"], reason="误将相同源图哈希的另一拼写解释为另一面墙"))
    for oid in wall["object_ids"]:
        document["observation_dispositions"].append(dict(scope_id="WALL-SCOPE", artifact_id="UPPER-INDEX", object_id=oid,
            disposition="included", physical_id="WALL-2", basis_refs=["SRC", "RULE"], reason="第二登记行，与源观察身份需要核对"))
    target = copy.deepcopy(document["quantity_targets"][0])
    target.update(id="VOLUME-2", physical_id="WALL-2")
    document["quantity_targets"].append(target)
    wall["native_result"]["wall2"] = copy.deepcopy(wall["native_result"]["wall"])
    update_result(wall, wall["native_result"])
    document["quantity_claims"].append(dict(id="CLAIM-2", target_id="VOLUME-2", artifact_id="RESULT",
        result_pointer="/wall2/volume/quantity", quantity_level="confirmed", owner_work_package_id="CONCRETE"))
    answer = run(wall)
    assert answer["totals"] == []
    assert answer["scope_status"][0]["inventory_count"] == 2
    assert any(row["code"] == "source_observation_multiple_objects" for row in answer["ownership_conflicts"])


def test_explicit_source_extent_split_retains_working_explanation(wall):
    document = wall["document"]
    document["objects"][0].update(extent_id="left-half", reason="按同一条总墙线中点拆分左半墙；部位互斥为模型工作判断")
    document["objects"].append(dict(id="WALL-2", scope_id="WALL-SCOPE", extent_id="right-half",
        observations=[dict(artifact_id="INDEX", object_id=wall["object_ids"][0])],
        basis_refs=["SRC", "RULE"], reason="按同一条总墙线中点拆分右半墙；独立分项程序给出右半体积"))
    document["observation_dispositions"].append(dict(scope_id="WALL-SCOPE", artifact_id="INDEX", object_id=wall["object_ids"][0],
        disposition="included", physical_id="WALL-2", basis_refs=["SRC", "RULE"], reason="源线右半归属 WALL-2，与左半分别说明"))
    document["quantity_targets"][0]["extent_id"] = "left-half"
    second_target = copy.deepcopy(document["quantity_targets"][0])
    second_target.update(id="VOLUME-2", physical_id="WALL-2", extent_id="right-half")
    document["quantity_targets"].append(second_target)
    update_result(wall, {"left": {"quantity": .9, "unit": "m3", "quantity_level": "inferred"},
                         "right": {"quantity": .9, "unit": "m3", "quantity_level": "inferred"}})
    document["quantity_claims"][0]["result_pointer"] = "/left/quantity"
    document["quantity_claims"].append(dict(id="CLAIM-2", target_id="VOLUME-2", artifact_id="RESULT",
        result_pointer="/right/quantity", quantity_level="confirmed", owner_work_package_id="CONCRETE"))
    answer = run(wall)
    assert len(answer["result_index"]) == 2
    assert answer["totals"][0]["quantity"] == pytest.approx(1.8)
    split = answer["source_observation_splits"][0]
    assert split["disjointness_proven"] is False and split["formal_adoption"] is False
    assert len(split["interpretations"]) == 2
    assert all(item["basis_refs"] and item["reason"] for item in split["interpretations"])


def test_two_views_claimed_as_two_results_for_same_target_are_not_added(wall):
    claim = copy.deepcopy(wall["document"]["quantity_claims"][0])
    claim["id"] = "CLAIM-FROM-ELEVATION"
    wall["document"]["quantity_claims"].append(claim)
    answer = run(wall)
    assert answer["result_index"] == answer["totals"] == []
    assert any(row["code"] == "multiple_claims_for_target" for row in answer["ownership_conflicts"])


def test_no_scope_inventory_or_target_declaration_has_no_completeness_claim(wall):
    document = wall["document"]
    document["scopes"][0]["inventory_artifact_ids"] = []
    document["objects"] = document["observation_dispositions"] = document["quantity_targets"] = document["quantity_claims"] = []
    answer = run(wall)
    scope = answer["scope_status"][0]
    assert scope["status"] == "pending" and scope["inventory_state"] == "missing"
    assert scope["inventory_count"] is None
    assert scope["engineering_completeness_proven"] is False
    assert any(action["code"] == "declare_scope_quantity_targets" for action in answer["next_actions"])
    document["scopes"] = []
    answer = run(wall)
    assert any(issue["code"] == "scope_not_established" for issue in answer["issues"])


def test_explicit_non_cad_review_scope_does_not_require_fabricated_index(wall):
    document = wall["document"]
    document["scopes"][0].update(inventory_artifact_ids=[], inventory_disposition="not_applicable",
        inventory_basis_refs=["RULE"], reason="本范围仅复核给定墙体明细的原生计算，不承诺已独立识读全部原图")
    document["objects"][0]["observations"] = []
    document["observation_dispositions"] = []
    answer = run(wall)
    assert answer["totals"][0]["quantity"] == 1.8
    scope = answer["scope_status"][0]
    assert scope["inventory_state"] == "not_applicable" and scope["inventory_count"] is None
    assert scope["engineering_completeness_proven"] is False
    assert scope["inventory_disposition_reason"]


def test_source_change_invalidates_index_even_if_index_file_unchanged(wall):
    doc = ezdxf.readfile(wall["source_path"])
    doc.modelspace().add_line((0, 0), (4000, 0))
    doc.saveas(wall["source_path"])
    wall["artifacts"]["SRC"] = observed(wall["source_path"])
    answer = run(wall)
    assert answer["totals"] == []
    assert answer["scope_status"][0]["inventory_state"] == "partial"
    assert answer["unaccounted_targets"]


def array_result(case):
    update_result(case, {"items": [
        {"id": "native-wall", "quantity": 1.8, "unit": "m3", "quantity_level": "confirmed"},
        {"id": "other-wall", "quantity": 7.2, "unit": "m3", "quantity_level": "confirmed"}]})
    claim = case["document"]["quantity_claims"][0]
    claim.update(result_pointer="/items/0/quantity", identity_pointer="/items/0/id", expected_identity="native-wall")
    return claim


def test_array_item_identity_is_verified_and_exported(wall):
    array_result(wall)
    entry = run(wall)["result_index"][0]
    assert entry["quantity"] == 1.8
    assert entry["expected_identity"] == entry["native_identity"] == "native-wall"
    assert entry["identity_pointer"] == "/items/0/id"


def test_array_reordering_cannot_silently_rebind_quantity(wall):
    array_result(wall)
    native = json.loads(wall["result_path"].read_text())
    native["items"].reverse()
    update_result(wall, native)  # This is a newly current result, not merely stale.
    answer = run(wall)
    assert answer["totals"] == answer["result_index"] == []
    assert "身份" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_array_pointer_requires_identity_when_native_row_has_id(wall):
    claim = array_result(wall)
    del claim["identity_pointer"]
    del claim["expected_identity"]
    answer = run(wall)
    assert answer["totals"] == []
    assert "identity_pointer" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_identity_pointer_cannot_borrow_a_different_array_rows_id(wall):
    claim = array_result(wall)
    claim.update(identity_pointer="/items/1/id", expected_identity="other-wall")
    answer = run(wall)
    assert answer["totals"] == []
    assert "自己的身份" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def nested_array_result(case):
    native = {"floors": [
        {"id": "F1", "items": [{"id": "local-wall", "quantity": 1.8, "unit": "m3", "quantity_level": "confirmed"}]},
        {"id": "F2", "items": [{"id": "local-wall", "quantity": 7.2, "unit": "m3", "quantity_level": "confirmed"}]}]}
    update_result(case, native)
    claim = case["document"]["quantity_claims"][0]
    claim.update(result_pointer="/floors/0/items/0/quantity", identity_pointer="/floors/0/items/0/id", expected_identity="local-wall")
    return native, claim


def test_nested_local_identity_requires_each_identified_array_ancestor(wall):
    _, claim = nested_array_result(wall)
    answer = run(wall)
    assert answer["totals"] == []
    assert "数组祖先" in " ".join(answer["unaccounted_targets"][0]["reasons"])
    claim["identity_bindings"] = [dict(identity_pointer="/floors/0/id", expected_identity="F1")]
    entry = run(wall)["result_index"][0]
    assert entry["quantity"] == 1.8
    assert {binding["expected_identity"] for binding in entry["identity_bindings"]} == {"F1", "local-wall"}


def test_parent_array_reorder_is_rejected_even_when_local_item_id_matches(wall):
    native, claim = nested_array_result(wall)
    claim["identity_bindings"] = [dict(identity_pointer="/floors/0/id", expected_identity="F1")]
    native["floors"].reverse()
    update_result(wall, native)
    answer = run(wall)
    assert answer["totals"] == []
    assert "身份" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_all_array_identity_bindings_can_use_the_list_form(wall):
    _, claim = nested_array_result(wall)
    claim.pop("identity_pointer")
    claim.pop("expected_identity")
    claim["identity_bindings"] = [dict(identity_pointer="/floors/0/id", expected_identity="F1"),
                                  dict(identity_pointer="/floors/0/items/0/id", expected_identity="local-wall")]
    entry = run(wall)["result_index"][0]
    assert entry["quantity"] == 1.8 and len(entry["identity_bindings"]) == 2


def test_json_pointer_escaping_works_and_invalid_array_index_does_not(wall):
    update_result(wall, {"wall/part~A": {"quantity": 1.8, "unit": "m3", "quantity_level": "confirmed"}})
    wall["document"]["quantity_claims"][0]["result_pointer"] = "/wall~1part~0A/quantity"
    assert run(wall)["totals"][0]["quantity"] == 1.8
    array_result(wall)
    wall["document"]["quantity_claims"][0]["result_pointer"] = "/items/00/quantity"
    assert run(wall)["totals"] == []


def test_duplicate_ids_raise_instead_of_silently_overwriting(wall):
    wall["document"]["quantity_targets"].append(copy.deepcopy(wall["document"]["quantity_targets"][0]))
    with pytest.raises(ValueError, match="重复 id"):
        run(wall)


def execute_reference(case, input_data):
    """Refresh real calculator input/output, not a hand-authored result fixture."""
    write_json(case["input_path"], input_data)
    completed = subprocess.run([sys.executable, "-B", str(case["script_path"]), str(case["input_path"])],
                               capture_output=True, check=False)
    assert completed.returncode == 0, (completed.stdout + completed.stderr).decode("utf-8")
    case["result_path"].write_bytes(completed.stdout)
    case["artifacts"].update(INPUT=observed(case["input_path"]), RESULT=observed(case["result_path"]))
    case["native_result"] = json.loads(completed.stdout)


def reference_case(tmp_path, skill, script, input_data, unit, pointer, kind):
    input_path, result_path = tmp_path / "reference-input.json", tmp_path / "reference-output.json"
    case = dict(input_path=input_path, result_path=result_path,
                script_path=ROOT / "skills" / skill / "scripts" / script, artifacts={}, task_statuses={})
    execute_reference(case, input_data)
    case["document"] = dict(schema_version="qto-project-workflow/1.0",
        artifacts=[dict(id="INPUT", kind="input", path=str(input_path)), dict(id="RESULT", kind="result", path=str(result_path))],
        scopes=[dict(id="SCOPE", title="给定明细的实际参考计算", basis_refs=["INPUT"], inventory_artifact_ids=[],
            required_task_ids=[], inventory_disposition="not_applicable", inventory_basis_refs=["INPUT"],
            reason="仅复核给定明细的算术与成果对应，不声称已独立识读工程原图")],
        work_packages=[dict(id="PACKAGE", owner_skill=skill, scope_ids=["SCOPE"])],
        objects=[dict(id="SUBJECT", scope_id="SCOPE", observations=[], basis_refs=["INPUT"], reason="由输入明细说明本组实物及部位")],
        observation_dispositions=[],
        quantity_targets=[dict(id="QUANTITY", scope_id="SCOPE", work_package_id="PACKAGE", physical_id="SUBJECT", extent_id="stated-group",
            quantity_kind=kind, measurement_basis_id="explicit-input-reference", scenario_id="base", unit=unit,
            quantity_role="project_rule", disposition="required", basis_refs=["INPUT"], reason="按实际程序的明示分组或结构层读取参考工程量")],
        quantity_claims=[dict(id="CLAIM", target_id="QUANTITY", artifact_id="RESULT", result_pointer=pointer,
            quantity_level="inferred", owner_work_package_id="PACKAGE")])
    return case


def component_input():
    return dict(recognition_basis=None, reconciled_instances=[
        dict(physical_id=pid, category="灯具", system="照明", spec=spec, unit="个", quantity_level="规则推算量",
             evidence="输入逐项给定实物编号；此测试不声称已完成工程识别")
        for pid, spec in [("B1", "B"), ("B2", "B"), ("C1", "C")]])


def component_record_binding():
    return dict(record_pointer="/groups/0", expected_fields=dict(category="灯具", system="照明", spec="B", unit="个", quantity_level="规则推算量"))


@pytest.fixture
def components(tmp_path):
    return reference_case(tmp_path, "qto-components-points", "count_reconciled_instances.py", component_input(), "个", "/groups/0/count", "instance_count")


def test_actual_component_group_requires_full_native_semantics(components):
    assert components["native_result"]["groups"][0]["count"] == 2
    answer = run(components)
    assert answer["totals"] == [] and answer["unaccounted_targets"]
    assert any(action["actor"] == "model" and action["code"] == "rebind_native_result" for action in answer["next_actions"])
    components["document"]["quantity_claims"][0]["record_bindings"] = [component_record_binding()]
    entry = run(components)["result_index"][0]
    assert entry["quantity"] == 2 and entry["quantity_level"] == "inferred"
    binding = entry["record_bindings"][0]
    assert binding["contract"] == "component_count.groups"
    assert set(binding["required_fields"]) == {"category", "system", "spec", "unit", "quantity_level"}
    assert binding["native_fields"] == binding["expected_fields"]


@pytest.mark.parametrize("missing", ["category", "system", "spec", "unit", "quantity_level"])
def test_actual_component_group_cannot_omit_any_grouping_dimension(components, missing):
    binding = component_record_binding()
    del binding["expected_fields"][missing]
    components["document"]["quantity_claims"][0]["record_bindings"] = [binding]
    answer = run(components)
    assert answer["totals"] == []
    assert missing in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_actual_component_new_group_sorting_cannot_rebind_existing_claim(components):
    claim = components["document"]["quantity_claims"][0]
    claim["record_bindings"] = [component_record_binding()]
    assert run(components)["totals"][0]["quantity"] == 2
    changed = component_input()
    changed["reconciled_instances"].extend(dict(changed["reconciled_instances"][0], physical_id=f"A{i}", spec="A") for i in range(7))
    execute_reference(components, changed)
    assert components["native_result"]["groups"][0]["spec"] == "A"
    assert components["native_result"]["groups"][0]["count"] == 7
    answer = run(components)
    assert answer["totals"] == [] and answer["unaccounted_targets"]
    claim["result_pointer"] = "/groups/1/count"
    claim["record_bindings"][0]["record_pointer"] = "/groups/1"
    assert run(components)["totals"][0]["quantity"] == 2


def test_semantic_binding_does_not_override_native_unit_or_level(components):
    claim = components["document"]["quantity_claims"][0]
    claim.update(record_bindings=[component_record_binding()], quantity_level="confirmed")
    assert run(components)["result_index"][0]["quantity_level"] == "inferred"
    components["document"]["quantity_targets"][0]["unit"] = "m2"
    answer = run(components)
    assert answer["totals"] == []
    assert "单位" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def component_area_claim(case):
    case["document"]["quantity_targets"][0].update(unit="m2", quantity_kind="derived_instance_area")
    case["document"]["quantity_claims"][0].update(result_pointer="/groups/0/derived_area_m2", record_bindings=[component_record_binding()])


def test_actual_component_derived_area_uses_native_metric_unit_and_allows_recalculation(components):
    payload = component_input()
    for item in payload["reconciled_instances"]:
        item.update(width_m=1, height_m=2)
    execute_reference(components, payload)
    assert components["native_result"]["groups"][0]["unit"] == "个"  # This is count's unit.
    component_area_claim(components)
    entry = run(components)["result_index"][0]
    assert entry["quantity"] == 4 and entry["unit"] == "m2" and entry["unit_basis"] == "native_result"
    for item in payload["reconciled_instances"]:
        item["width_m"] = 1.5
    execute_reference(components, payload)
    # Stable group semantics do not freeze its recalculated quantity value.
    assert run(components)["result_index"][0]["quantity"] == 6


def test_explicit_unit_pointer_cannot_turn_native_component_area_into_count(components):
    payload = component_input()
    for item in payload["reconciled_instances"]:
        item.update(width_m=1, height_m=2)
    execute_reference(components, payload)
    component_area_claim(components)
    components["document"]["quantity_targets"][0]["unit"] = "个"
    components["document"]["quantity_claims"][0]["unit_pointer"] = "/groups/0/unit"
    answer = run(components)
    assert answer["totals"] == []
    assert "单位" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_actual_component_missing_dimensions_keep_derived_area_unaccounted(components):
    assert components["native_result"]["groups"][0]["derived_area_m2"] is None
    component_area_claim(components)
    answer = run(components)
    assert answer["totals"] == [] and answer["unaccounted_targets"]
    assert "null" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def road_input():
    return {"segments": [{"id": "R1", "length_m": 10, "width_m": 5,
        "layers": [{"name": "基层", "thickness_m": .2}, {"name": "面层", "thickness_m": .05}],
        "evidence": "输入明示该段定宽道路与各层厚度，未包含变宽或交叉口"}]}


@pytest.fixture
def road(tmp_path):
    case = reference_case(tmp_path, "qto-roads-siteworks", "calculate_road_layers_reference.py", road_input(), "m3",
                          "/items/0/layers/0/volume_m3", "layer_volume")
    case["document"]["quantity_claims"][0].update(identity_pointer="/items/0/id", expected_identity="R1")
    return case


def road_record_binding():
    return dict(record_pointer="/items/0/layers/0", expected_fields=dict(name="基层", thickness_m=.2))


def test_actual_road_layer_requires_context_in_addition_to_segment_identity(road):
    assert road["native_result"]["items"][0]["layers"][0]["volume_m3"] == 10
    answer = run(road)
    assert answer["totals"] == [] and answer["unaccounted_targets"]
    road["document"]["quantity_claims"][0]["record_bindings"] = [road_record_binding()]
    entry = run(road)["result_index"][0]
    assert entry["quantity"] == 10 and entry["native_identity"] == "R1"
    assert entry["record_bindings"][0]["contract"] == "roads_reference.layers"
    assert set(entry["record_bindings"][0]["required_fields"]) == {"name", "thickness_m"}


@pytest.mark.parametrize("missing", ["name", "thickness_m"])
def test_actual_road_layer_requires_both_native_layer_fields(road, missing):
    binding = road_record_binding()
    del binding["expected_fields"][missing]
    road["document"]["quantity_claims"][0]["record_bindings"] = [binding]
    answer = run(road)
    assert answer["totals"] == []
    assert missing in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_actual_road_layers_reorder_cannot_rebind_volume(road):
    claim = road["document"]["quantity_claims"][0]
    claim["record_bindings"] = [road_record_binding()]
    assert run(road)["totals"][0]["quantity"] == 10
    changed = road_input()
    changed["segments"][0]["layers"].reverse()
    execute_reference(road, changed)
    assert road["native_result"]["items"][0]["layers"][0]["volume_m3"] == 2.5
    assert run(road)["totals"] == []
    claim["result_pointer"] = "/items/0/layers/1/volume_m3"
    claim["record_bindings"][0]["record_pointer"] = "/items/0/layers/1"
    assert run(road)["totals"][0]["quantity"] == 10


def test_actual_road_repeated_layer_semantics_cannot_be_disambiguated_by_quantity(road):
    changed = road_input()
    changed["segments"][0]["layers"].append(copy.deepcopy(changed["segments"][0]["layers"][0]))
    execute_reference(road, changed)
    road["document"]["quantity_claims"][0]["record_bindings"] = [road_record_binding()]
    answer = run(road)
    assert answer["totals"] == []
    assert "不唯一" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_actual_road_duplicate_segment_id_is_not_a_stable_result_binding(road):
    changed = road_input()
    changed["segments"].append(dict(changed["segments"][0], length_m=30))
    execute_reference(road, changed)  # Existing reference calculator permits this input.
    assert [row["id"] for row in road["native_result"]["items"]] == ["R1", "R1"]
    road["document"]["quantity_claims"][0]["record_bindings"] = [road_record_binding()]
    answer = run(road)
    assert answer["totals"] == []
    assert "身份在同层不唯一" in " ".join(answer["unaccounted_targets"][0]["reasons"])


def test_unknown_array_requires_full_explicit_context_and_rejects_reorder(wall):
    native = {"items": [{"zone": "A", "material": "C30", "quantity": 1.8}, {"zone": "B", "material": "C30", "quantity": 7.2}]}
    update_result(wall, native)
    claim = wall["document"]["quantity_claims"][0]
    claim["result_pointer"] = "/items/0/quantity"
    assert run(wall)["totals"] == []
    claim["record_bindings"] = [dict(record_pointer="/items/0", expected_fields=dict(zone="A"))]
    assert run(wall)["totals"] == []
    claim["record_bindings"][0]["expected_fields"]["material"] = "C30"
    entry = run(wall)["result_index"][0]
    assert entry["quantity"] == 1.8
    assert entry["record_bindings"][0]["contract"] == "explicit_row_context"
    native["items"].reverse()
    update_result(wall, native)
    assert run(wall)["totals"] == []


def test_record_context_cannot_borrow_a_different_array_row(wall):
    update_result(wall, {"items": [{"zone": "A", "quantity": 1.8}, {"zone": "B", "quantity": 7.2}]})
    wall["document"]["quantity_claims"][0].update(result_pointer="/items/0/quantity",
        record_bindings=[dict(record_pointer="/items/1", expected_fields=dict(zone="B"))])
    answer = run(wall)
    assert answer["totals"] == []
    assert "不能借用其他行" in " ".join(answer["unaccounted_targets"][0]["reasons"])


@pytest.mark.parametrize("native,pointer,binding", [
    ({"items": [1.8, 7.2]}, "/items/0", []),
    ({"items": [{"quantity": 1.8}]}, "/items/0/quantity", [dict(record_pointer="/items/0", expected_fields=dict(quantity=1.8))]),
    ({"items": [{"zone": "A", "quantity": 1.8}, {"zone": "A", "quantity": 7.2}]}, "/items/0/quantity",
     [dict(record_pointer="/items/0", expected_fields=dict(zone="A"))]),
])
def test_position_or_quantity_value_cannot_supply_missing_row_identity(wall, native, pointer, binding):
    update_result(wall, native)
    wall["document"]["quantity_claims"][0].update(result_pointer=pointer, record_bindings=binding)
    answer = run(wall)
    assert answer["totals"] == [] and answer["unaccounted_targets"]
    assert any(action["actor"] == "model" and action["code"] == "rebind_native_result" for action in answer["next_actions"])


def test_repeated_local_id_requires_unique_bound_native_identity_fields(wall):
    update_result(wall, {"items": [
        {"id": "W1", "physical_id": "F1-W1", "quantity": 1.8},
        {"id": "W1", "physical_id": "F2-W1", "quantity": 7.2}]})
    claim = wall["document"]["quantity_claims"][0]
    claim.update(result_pointer="/items/0/quantity", identity_pointer="/items/0/id", expected_identity="W1")
    assert run(wall)["totals"] == []
    claim["identity_bindings"] = [dict(identity_pointer="/items/0/physical_id", expected_identity="F1-W1")]
    assert run(wall)["totals"][0]["quantity"] == 1.8


def tracked_result(case, consumed):
    # The workflow executor supplies this observed closure; reconcile does not
    # accept caller-authored task claims as a substitute for execution evidence.
    case["artifacts"]["RESULT"].update(workflow_tracked=True, producer_input_ids=consumed)


def test_tracked_quantity_accepts_actual_transitive_basis_inputs(wall):
    tracked_result(wall, ["SRC", "RULE", "INDEX"])
    answer = run(wall)
    assert answer["totals"][0]["quantity"] == 1.8
    assert answer["unaccounted_targets"] == []


@pytest.mark.parametrize("owner", ["quantity_targets", "objects", "scopes"])
def test_registered_basis_not_consumed_by_result_never_validates_old_quantity(wall, owner):
    rule_path = wall["result_path"].parent / "unconsumed-project-rule.txt"
    rule_path.write_text("本项目规则初版：保留设计实体体积。", encoding="utf-8")
    wall["document"]["artifacts"].append(dict(id="EXTRA-RULE", kind="rule", path=str(rule_path)))
    wall["artifacts"]["EXTRA-RULE"] = observed(rule_path)
    wall["document"][owner][0]["basis_refs"].append("EXTRA-RULE")
    tracked_result(wall, ["SRC", "RULE", "INDEX"])
    original_result_hash = wall["artifacts"]["RESULT"]["sha256"]
    answer = run(wall)
    assert answer["totals"] == answer["result_index"] == []
    gap = next(row for row in answer["next_actions"] if row["code"] == "declare_quantity_dependencies")
    assert gap["actor"] == "model" and gap["missing_basis_refs"] == ["EXTRA-RULE"]
    # Refreshing the rule's observed hash alone still does not put the rule in
    # the producing task's real dependency graph or establish a rerun.
    rule_path.write_text("本项目规则修订：该墙体有明确新增扣除项。", encoding="utf-8")
    wall["artifacts"]["EXTRA-RULE"] = observed(rule_path)
    assert wall["artifacts"]["RESULT"]["status"] == "current"
    assert wall["artifacts"]["RESULT"]["sha256"] == original_result_hash
    after_edit = run(wall)
    assert after_edit["totals"] == [] and after_edit["unaccounted_targets"]


def test_tracked_result_must_consume_the_actual_source_observation_index(wall):
    tracked_result(wall, ["SRC", "RULE"])
    answer = run(wall)
    assert answer["totals"] == []
    gap = next(row for row in answer["next_actions"] if row["code"] == "declare_quantity_dependencies")
    assert gap["missing_basis_refs"] == ["INDEX"]


def test_tracked_result_without_a_producer_cannot_prove_basis_consumption(wall):
    tracked_result(wall, [])
    answer = run(wall)
    assert answer["totals"] == [] and answer["unaccounted_targets"]
    gap = next(row for row in answer["next_actions"] if row["code"] == "declare_quantity_dependencies")
    assert set(gap["missing_basis_refs"]) == {"SRC", "RULE", "INDEX"}
