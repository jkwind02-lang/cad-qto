"""Portable evidence-led instance count tests; no project answers or paths."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import subprocess

import pytest

SHARED = Path(__file__).resolve().parents[1] / "skills/_qto-shared-evidence/scripts"
sys.path.insert(0, str(SHARED))
from qto_instance_requirements import SCHEMA, evaluate, verify_result
sys.path.insert(0, str(SHARED.parents[1] / "qto-pipe-networks/scripts"))
from merge_pipe_instance_requirements import merge, markdown, validate_summary, requirement_table_row, accessory_table_row


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    source = tmp_path / "synthetic_source.txt"
    source.write_text("SYNTHETIC TEST ONLY: two ports, DN20; one count item each. No engineering default.", encoding="utf-8")
    evidence = [{"evidence_id": "view", "path": str(source), "sha256": sha(source), "source_sha256": sha(source),
                 "kind": "drawing_view", "locator": "synthetic named ports and clause", "observation": "test fixture"}]
    objects = ["obs-a", "obs-b", "cad-a", "cad-b", "rule-a", "rule-b", "existing-a", "cad-valve-a", "position-b"]
    ledger = {"schema_version": "qto-model-recognition-ledger/1.0", "ledger_id": "recognition",
              "ledger_version": "1", "source_sha256": sha(source), "adoption_scope": "working", "review_status": "unreviewed",
              "evidence": evidence, "entries": [{"adjudication_id": "read", "status": "active", "decision_origin": "model",
              "subject_id": "synthetic fixture", "professional_owner": "qto-pipe-networks", "source_id": "source",
              "recognized_by": "synthetic test generator", "recognized_at": "2026-10-11T00:00:00Z", "basis": "synthetic",
              "scope_basis": "synthetic", "identity_basis": "named source objects", "cross_check": "independent fixture expectations",
              "decision": "include", "object_ids": objects, "evidence_ids": ["view"], "uncertainties": []}]}
    ledger_path = tmp_path / "recognition.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    refs = ["recognition:view"]
    recognition = {"ledger_id": "recognition", "entry_id": "read"}
    subjects = [{"subject_id": "obs-" + key, "physical_id": "port-" + key, "parent_physical_id": "equipment-a",
        "port_key": key, "subject_kind": "port", "system": "supply", "scope_id": "scope",
        "identity_anchor": {"source_id": "source", "object_id": "cad-" + key},
        "recognition": recognition, "evidence_refs": refs,
        "facts": {"kind": {"value": "connection", "evidence_refs": refs}, "dn": {"value": "DN20", "evidence_refs": refs},
                  "tag": {"value": key, "evidence_refs": refs}}} for key in ("a", "b")]
    rule = {"requirement_id": "rule-a", "requirement_key": "isolation-requirement", "version": "1",
            "system": "supply", "scope_id": "scope", "subject_kind": "port", "accessory_type": "test_valve",
            "professional_owner": "qto-pipe-networks", "basis": "synthetic clause says one at each named port",
            "accessory_role": "isolation", "installation_position": {"relation": "subject_interface"},
            "recognition": recognition, "evidence_refs": refs,
            "when": [{"fact": "kind", "op": "eq", "value": "connection"}],
            "quantity": {"mode": "count", "per_subject": 1, "unit": "个"},
            "specification": {"dn": {"from_fact": "dn"}}, "exceptions": []}
    return {"schema": SCHEMA, "run_id": "synthetic", "scope_id": "scope", "scope_complete": True,
            "sources": [{"source_id": "source", "document_id": "drawing", "revision": "A", "path": str(source), "sha256": sha(source), "status": "selected"}],
            "recognition_ledgers": [{"source_id": "source", "path": str(ledger_path), "sha256": sha(ledger_path)}],
            "subjects": subjects, "requirements": [rule], "existing_accessories": []}


def existing(data):
    obj = {"observation_id": "existing-a", "physical_id": "valve-a", "subject_physical_id": "port-a",
           "requirement_key": "isolation-requirement", "system": "supply", "scope_id": "scope",
           "identity_anchor": {"source_id": "source", "object_id": "cad-valve-a"},
           "result_record_id": "network-valve-a", "accessory_type": "test_valve", "specification": {"dn": "DN20"}, "unit": "个",
           "recognition": {"ledger_id": "recognition", "entry_id": "read"}, "evidence_refs": ["recognition:view"]}
    data["existing_accessories"].append(obj)
    return obj


def test_two_ports_of_one_equipment_count_separately(tmp_path):
    result = evaluate(fixture(tmp_path))
    assert len(result["records"]) == 2
    assert sum(r["additional_quantity"] for r in result["records"]) == 2
    assert result["scope_complete"] and not result["formal_adoption_allowed"]
    verify_result(result)


def test_same_view_as_new_physical_alias_rejected(tmp_path):
    data = fixture(tmp_path)
    data["subjects"][1]["identity_anchor"] = data["subjects"][0]["identity_anchor"]
    data["subjects"][1]["port_key"] = "a"
    with pytest.raises(ValueError, match="改名"):
        evaluate(data)


def test_shared_equipment_anchor_multiple_ports_need_frozen_port_bindings(tmp_path):
    data = fixture(tmp_path)
    data["subjects"][1]["identity_anchor"] = dict(data["subjects"][0]["identity_anchor"])
    with pytest.raises(ValueError, match="port_bindings"):
        evaluate(data)
    entry_path = Path(data["recognition_ledgers"][0]["path"])
    ledger = json.loads(entry_path.read_text())
    ledger["entries"][0]["port_bindings"] = [{"subject_id": s["subject_id"], "anchor_object_id": s["identity_anchor"]["object_id"],
        "port_key": s["port_key"], "parent_physical_id": s["parent_physical_id"]} for s in data["subjects"]]
    entry_path.write_text(json.dumps(ledger), encoding="utf-8")
    data["recognition_ledgers"][0]["sha256"] = sha(entry_path)
    assert sum(r["additional_quantity"] for r in evaluate(data)["records"]) == 2
    data["subjects"][1]["port_key"] = "renamed-port"
    with pytest.raises(ValueError, match="port_bindings"):
        evaluate(data)


def test_multi_view_same_physical_identity_count_once(tmp_path):
    data = fixture(tmp_path)
    data["subjects"][1].update(physical_id="port-a", port_key="a")
    assert len(evaluate(data)["records"]) == 1


def test_duplicate_clause_alias_does_not_multiply(tmp_path):
    data = fixture(tmp_path)
    rule = copy.deepcopy(data["requirements"][0])
    rule.update(requirement_id="rule-b", requirement_key="renamed-clause")
    data["requirements"].append(rule)
    out = evaluate(data)
    assert len(out["records"]) == 2
    assert sum(r["additional_quantity"] for r in out["records"]) == 2


def test_conflicting_duplicate_clause_is_not_first_wins(tmp_path):
    data = fixture(tmp_path)
    rule = copy.deepcopy(data["requirements"][0])
    rule.update(requirement_id="rule-b", requirement_key="alias")
    rule["quantity"]["per_subject"] = 2
    data["requirements"].append(rule)
    assert all(r["status"] == "unresolved" and r["additional_quantity"] is None for r in evaluate(data)["records"])


def test_two_serial_accessories_need_source_installation_positions(tmp_path):
    data = fixture(tmp_path)
    rule = copy.deepcopy(data["requirements"][0])
    rule.update(requirement_id="rule-b", requirement_key="second-serial-position")
    rule["installation_position"] = {"relation": "source_object", "source_id": "source", "object_id": "position-b", "evidence_refs": ["recognition:view"]}
    rule["when"] = [{"fact": "tag", "op": "eq", "value": "a"}]
    data["requirements"].append(rule)
    assert sum(r["additional_quantity"] or 0 for r in evaluate(data)["records"]) == 3


def test_missing_condition_not_false_or_zero(tmp_path):
    data = fixture(tmp_path)
    del data["subjects"][0]["facts"]["kind"]
    row = evaluate(data)["records"][0]
    assert row["status"] == "unresolved" and row["additional_quantity"] is None


def test_missing_dn_preserves_count_but_not_specification(tmp_path):
    data = fixture(tmp_path)
    del data["subjects"][0]["facts"]["dn"]
    row = evaluate(data)["records"][0]
    assert row["required_quantity"] == 1 and row["specification"] == {"dn": None}
    assert not row["specification_complete"]


@pytest.mark.parametrize("value,expected", [(True, "excluded"), (False, "determined"), (None, "unresolved")])
def test_exceptions_three_valued_logic(tmp_path, value, expected):
    data = fixture(tmp_path)
    data["requirements"][0]["exceptions"] = [{"when": [{"fact": "built_in", "op": "eq", "value": True}], "reason": "integral accessory", "evidence_refs": ["recognition:view"]}]
    data["subjects"][0]["facts"]["built_in"] = {"value": value, "evidence_refs": ["recognition:view"]}
    assert evaluate(data)["records"][0]["status"] == expected


def test_existing_count_deducted_exactly_once(tmp_path):
    data = fixture(tmp_path)
    existing(data)
    out = evaluate(data)
    assert out["records"][0]["required_quantity"] == 1
    assert out["records"][0]["additional_quantity"] == 0
    assert out["records"][1]["additional_quantity"] == 1


@pytest.mark.parametrize("field,value", [("system", "return"), ("scope_id", "other"), ("subject_physical_id", "missing")])
def test_wrong_existing_attachment_rejected(tmp_path, field, value):
    data = fixture(tmp_path)
    existing(data)[field] = value
    with pytest.raises(ValueError, match="错系统|错范围"):
        evaluate(data)


def test_wrong_existing_spec_is_not_silently_added(tmp_path):
    data = fixture(tmp_path)
    existing(data)["specification"] = {"dn": "DN25"}
    assert evaluate(data)["records"][0]["additional_quantity"] is None


def test_source_drift_rejects_and_cannot_reuse_result(tmp_path):
    data = fixture(tmp_path)
    result = evaluate(data)
    Path(data["sources"][0]["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        evaluate(data)
    with pytest.raises(ValueError, match="来源已变化"):
        verify_result(result)


def test_conflicting_selected_versions_rejected(tmp_path):
    data = fixture(tmp_path)
    source = copy.deepcopy(data["sources"][0])
    source.update(source_id="duplicate", revision="B")
    data["sources"].append(source)
    with pytest.raises(ValueError, match="冲突版本"):
        evaluate(data)


def test_rule_change_recomputes_affected_results(tmp_path):
    data = fixture(tmp_path)
    before = evaluate(data)
    data["requirements"][0]["quantity"]["per_subject"] = 3
    after = evaluate(data, previous=before)
    assert sum(r["additional_quantity"] for r in after["records"]) == 6
    assert len(after["recompute"]["changed_or_added"]) == 2


@pytest.mark.parametrize("unit", ["m", "m2", "kg", "mm"])
def test_no_count_shortcut_for_geometry(tmp_path, unit):
    data = fixture(tmp_path)
    data["requirements"][0]["quantity"]["unit"] = unit
    with pytest.raises(ValueError, match="件数单位"):
        evaluate(data)


def test_geometry_remains_explicit_specialist_request(tmp_path):
    data = fixture(tmp_path)
    data["requirements"][0]["quantity"] = {"mode": "specialist_measurement", "unit": "m", "required_facts": ["route", "start", "end"]}
    assert all(r["additional_quantity"] is None and "specialist_geometry_required" in r["issues"] for r in evaluate(data)["records"])


def test_multiview_dn_conflict_preserved(tmp_path):
    data = fixture(tmp_path)
    data["subjects"][1].update(physical_id="port-a", port_key="a")
    data["subjects"][1]["facts"]["dn"]["value"] = "DN25"
    row = evaluate(data)["records"][0]
    assert row["specification"]["dn"] is None


def merge_fixture(tmp_path):
    data = fixture(tmp_path)
    existing(data)
    req = evaluate(data)
    req_path = tmp_path / "requirements-result.json"
    req_path.write_text(json.dumps(req), encoding="utf-8")
    ledger = data["recognition_ledgers"][0]
    network = {"evidence_version": "qto-pipe-inference/1.2", "network_id": "network-a", "unit": "m",
               "recognition_basis": {"schema_version": "qto-model-recognition-ledger/1.0", "ledger_path": ledger["path"],
                 "ledger_sha256": ledger["sha256"], "decision_origin": "model", "adoption_scope": "working",
                 "review_status": "unreviewed", "formal_adoption_allowed": False},
               "valves": [{"valve_id": "network-valve-a", "source_id": "cad-valve-a", "system": "supply", "valve_type": "test_valve", "dn": "DN20", "dn_quantity_level": "确认量"}],
               "length_summary": {"supply": {"确认量": {"DN20": 4.5}}}, "decision_queue": [],
               "route_preparation": {"length_delta": 0, "issues": []}}
    network_path = tmp_path / "network.json"
    network_path.write_text(json.dumps(network), encoding="utf-8")
    payload = {"schema": "qto-pipe-instance-merge/1.0", "scope_id": "scope", "scope_basis": "synthetic same two ports",
               "network_id": "network-a", "network_result": {"path": str(network_path), "sha256": sha(network_path)},
               "instance_requirements_result": {"path": str(req_path), "sha256": sha(req_path)}}
    return payload, data


def test_pipe_merge_existing_plus_remainder_equals_required(tmp_path):
    payload, _ = merge_fixture(tmp_path)
    result = merge(payload)
    assert sum(i["quantity"] for i in result["accessory_items"]) == 2
    assert len(result["network_result"]["valves"]) == 1
    assert validate_summary(result, markdown(result))["status"] == "PASS"


def test_user_summary_missing_unresolved_record_cannot_pass(tmp_path):
    payload, data = merge_fixture(tmp_path)
    data["subjects"][1]["facts"].pop("kind")
    path = Path(payload["instance_requirements_result"]["path"])
    path.write_text(json.dumps(evaluate(data)), encoding="utf-8")
    payload["instance_requirements_result"]["sha256"] = sha(path)
    result = merge(payload)
    text = markdown(result)
    record_id = result["instance_requirements"]["records"][1]["record_id"]
    text = "\n".join(line for line in text.splitlines() if record_id not in line)
    with pytest.raises(ValueError, match="遗漏"):
        validate_summary(result, text)


@pytest.mark.parametrize("change", ["dn", "system", "valve_type"])
def test_merge_checks_existing_against_actual_network_record(tmp_path, change):
    payload, _ = merge_fixture(tmp_path)
    path = Path(payload["network_result"]["path"])
    network = json.loads(path.read_text())
    network["valves"][0][change] = "wrong"
    path.write_text(json.dumps(network), encoding="utf-8")
    payload["network_result"]["sha256"] = sha(path)
    with pytest.raises(ValueError, match="系统/类型/规格"):
        merge(payload)


def test_merge_source_change_rejected(tmp_path):
    payload, data = merge_fixture(tmp_path)
    Path(data["sources"][0]["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="来源已变化"):
        merge(payload)


def test_merge_does_not_accept_result_modification(tmp_path):
    payload, _ = merge_fixture(tmp_path)
    path = Path(payload["instance_requirements_result"]["path"])
    result = json.loads(path.read_text())
    result["records"][0]["additional_quantity"] = 20
    path.write_text(json.dumps(result), encoding="utf-8")
    payload["instance_requirements_result"]["sha256"] = sha(path)
    with pytest.raises(ValueError, match="内容签名"):
        merge(payload)


def test_excluded_quantities_display_not_applicable_without_zero(tmp_path):
    payload, data = merge_fixture(tmp_path)
    data["existing_accessories"] = []
    data["requirements"][0]["when"] = [{"fact": "kind", "op": "eq", "value": "other"}]
    path = Path(payload["instance_requirements_result"]["path"])
    path.write_text(json.dumps(evaluate(data)), encoding="utf-8")
    payload["instance_requirements_result"]["sha256"] = sha(path)
    result = merge(payload)
    for row in result["instance_requirements"]["records"]:
        assert row["required_quantity"] is None and row["additional_quantity"] is None
        columns = [c.strip() for c in requirement_table_row(row).split("|")]
        assert columns[6] == columns[8] == "不适用"
    for row in result["accessory_summary"]:
        if row["origin"] == "instance_requirement_remainder":
            assert row["quantity"] is None
            assert [c.strip() for c in accessory_table_row(row).split("|")][7] == "不适用"


def test_mutually_exclusive_rules_do_not_conflict(tmp_path):
    data = fixture(tmp_path)
    alternate = copy.deepcopy(data["requirements"][0])
    alternate.update(requirement_id="rule-b")
    alternate["when"] = [{"fact": "dn", "op": "eq", "value": "DN25"}]
    alternate["quantity"]["per_subject"] = 3
    data["requirements"].append(alternate)
    result = evaluate(data)
    assert sum(r["additional_quantity"] for r in result["records"]) == 2
    assert all(len(r["clause_evaluations"]) == 2 for r in result["records"])


def test_source_alias_installation_position_deduplicated(tmp_path):
    data = fixture(tmp_path)
    data["subjects"] = data["subjects"][:1]
    alias = copy.deepcopy(data["sources"][0])
    alias["source_id"] = "alias"
    data["sources"].append(alias)
    data["requirements"][0]["installation_position"] = {"relation": "source_object", "source_id": "source", "object_id": "position-b", "evidence_refs": ["recognition:view"]}
    alternate = copy.deepcopy(data["requirements"][0])
    alternate.update(requirement_id="rule-b")
    alternate["installation_position"]["source_id"] = "alias"
    data["requirements"].append(alternate)
    assert len(evaluate(data)["records"]) == 1


def test_specialist_requests_keep_all_required_facts(tmp_path):
    data = fixture(tmp_path)
    data["requirements"][0]["quantity"] = {"mode": "specialist_measurement", "unit": "m2", "required_facts": ["length", "diameter"]}
    alternate = copy.deepcopy(data["requirements"][0])
    alternate.update(requirement_id="rule-b")
    alternate["quantity"]["required_facts"] = ["thickness"]
    data["requirements"].append(alternate)
    assert evaluate(data)["records"][0]["required_facts"] == ["diameter", "length", "thickness"]


def test_physical_position_cannot_be_repeated_across_subjects(tmp_path):
    data = fixture(tmp_path)
    data["requirements"][0]["installation_position"] = {"relation": "source_object", "source_id": "source", "object_id": "position-b", "evidence_refs": ["recognition:view"]}
    with pytest.raises(ValueError, match="多个主体"):
        evaluate(data)


def test_declaring_accessory_as_port_does_not_split_identity(tmp_path):
    data = fixture(tmp_path)
    existing(data).update(subject_kind="port", port_key="invented")
    with pytest.raises(ValueError, match="端口字段"):
        evaluate(data)


def test_excluded_rule_does_not_need_irrelevant_specification(tmp_path):
    data = fixture(tmp_path)
    data["requirements"][0]["when"] = [{"fact": "kind", "op": "eq", "value": "other"}]
    data["subjects"][0]["facts"].pop("dn")
    result = evaluate(data)
    assert result["scope_complete"]
    assert result["records"][0]["quantity_level"] == "不适用"


def test_existing_count_unit_does_not_cover_set(tmp_path):
    data = fixture(tmp_path)
    existing(data)["unit"] = "套"
    assert evaluate(data)["records"][0]["additional_quantity"] is None


def test_builder_infer_merge_standard_source_identity_and_wrong_branch(tmp_path):
    from test_pipe_symbol_ownership_20261003 import case
    drawing_dir = tmp_path / "drawing"
    drawing_dir.mkdir()
    inferred_input = case(drawing_dir, "include", False, "m", "fixtureValve")
    valve = inferred_input["valves"][0]
    assert valve["source_id"].startswith("EV-CAD-")
    original_id = valve["source_object_id"]
    assert original_id and original_id != valve["source_id"]
    valve.update(direct_dn="DN20", dn_evidence_level="inferred")
    input_path = drawing_dir / "inference-input.json"
    input_path.write_text(json.dumps(inferred_input), encoding="utf-8")
    network_path = drawing_dir / "network.json"
    script = SHARED.parents[1] / "qto-pipe-networks/scripts/infer_pipe_network.py"
    run = subprocess.run([sys.executable, "-B", str(script), "--input", str(input_path), "--output", str(network_path)], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stdout + run.stderr
    network = json.loads(network_path.read_text(encoding="utf-8"))
    assert network["valves"][0]["source_object_id"] == original_id
    data = fixture(tmp_path)
    data["subjects"] = data["subjects"][:1]
    data["subjects"][0]["system"] = "W"
    data["requirements"][0].update(system="W", accessory_type="test accessory")
    source_path = drawing_dir / "source.dxf"
    ledger_path = drawing_dir / "ledger.json"
    data["sources"].append({"source_id": "drawing", "document_id": "other-source", "revision": "A", "path": str(source_path), "sha256": sha(source_path), "status": "selected"})
    data["recognition_ledgers"].append({"source_id": "drawing", "path": str(ledger_path), "sha256": sha(ledger_path)})
    old = existing(data)
    old.update(observation_id=original_id, identity_anchor={"source_id": "drawing", "object_id": original_id},
               result_record_id=valve["valve_id"], accessory_type="test accessory", system="W",
               recognition={"ledger_id": "L", "entry_id": "SYMBOL"}, evidence_refs=["L:RAW"])
    req_path = tmp_path / "requirements.json"
    req_path.write_text(json.dumps(evaluate(data)), encoding="utf-8")
    payload = {"schema": "qto-pipe-instance-merge/1.0", "scope_id": "scope", "scope_basis": "generated symbol source", "network_id": network["network_id"],
               "network_result": {"path": str(network_path), "sha256": sha(network_path)},
               "instance_requirements_result": {"path": str(req_path), "sha256": sha(req_path)}}
    merged = merge(payload)
    assert sum(i["quantity"] or 0 for i in merged["accessory_items"]) == 1
    assert merged["instance_requirements"]["records"][0]["additional_quantity"] == 0
    network["valves"][0]["source_object_id"] = "other-physical-valve"
    network_path.write_text(json.dumps(network), encoding="utf-8")
    payload["network_result"]["sha256"] = sha(network_path)
    with pytest.raises(ValueError, match="物理对象身份"):
        merge(payload)
