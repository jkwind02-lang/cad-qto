"""De-identified protocol examples: exact controls and independent harmful mutations."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ambiguity_eval", ROOT / "maintenance/benchmark/scripts/evaluate_ambiguities.py")
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)


def fixture():
    data = {"schema_version": "qto-ambiguity-input/1.0", "benchmark_id": "anonymous-control",
            "sources": [{"source_id": "S", "sha256": "a" * 64}], "assets": [],
            "groups": [{"group_id": "G", "source_id": "S", "split": "SEALED",
                        "isolation_keys": {"floor": ["F"], "frame": ["P"], "block_family": []},
                        "targets": [{"target_id": "A", "kind": "OBJECT"},
                                    {"target_id": "B", "kind": "RELATION"},
                                    {"target_id": "C", "kind": "JUDGMENT"}]}],
            "coverage_regions": [{"region_id": "R", "source_id": "S", "split": "SEALED"}]}
    ref = {"schema_version": "qto-ambiguity-reference/1.0", "input_sha256": "h",
           "authority": {"kind": "INDEPENDENT_MODEL_FROM_SOURCE", "decision_subject": "independent reader",
                         "human_approved": False},
           "targets": [{"target_id": "A", "decision_status": "DECIDABLE", "acceptable_conclusions": ["route"],
                        "evidence_refs": ["S:1"], "basis": "original curve", "quantity":
                        {"value": 2, "unit": "raw", "basis": "entity geometry", "tolerance": 1e-9}},
                       {"target_id": "B", "decision_status": "DECIDABLE", "acceptable_conclusions": [False],
                        "evidence_refs": ["S:2"], "basis": "separate terminal", "quantity": None},
                       {"target_id": "C", "decision_status": "INSUFFICIENT", "acceptable_conclusions": [],
                        "evidence_refs": ["S:3"], "basis": "remote sheet absent", "quantity": None}],
           "coverage_regions": [{"region_id": "R", "expected_objects": ["1", "2"],
                                 "expected_relations": ["1->2"], "census_basis": "complete local diagram"}]}
    run = {"schema_version": "qto-ambiguity-run/1.0", "input_sha256": "h", "run_id": "r1",
           "version_label": "baseline", "pair_id": "p1", "replicate": 1,
           "model_config": {"model": "fixed", "effort": "fixed"}, "budget": {"tool_calls": 10},
           "human_review_minutes": None,
           "claims": [{"target_id": "A", "status": "DETERMINED", "conclusion": "route", "evidence_refs": ["S:1"],
                       "quantity": {"value": 2, "unit": "raw", "basis": "entity geometry"}},
                      {"target_id": "B", "status": "DETERMINED", "conclusion": False, "evidence_refs": ["S:2"], "quantity": None},
                      {"target_id": "C", "status": "UNRESOLVED", "conclusion": None, "evidence_refs": ["S:3"],
                       "reason": "Remote sheet is unavailable", "quantity": None}],
           "coverage_regions": [{"region_id": "R", "scanned_all_scope": True,
                                 "object_ids": ["1", "2"], "relation_ids": ["1->2"]}]}
    return data, ref, run


def test_exact_control_retains_authority_and_null_time():
    d, r, a = fixture()
    result = ev.score(d, r, a, "h")
    assert result["status"] == "PASS"
    assert result["counts"]["correct_determined"] == 2
    assert result["counts"]["reasonable_unresolved"] == 1
    assert result["human_review_minutes"] is None
    assert result["authority"]["human_approved"] is False
    assert result["formal_engineering_acceptance"] is False


@pytest.mark.parametrize("target,category", [("A", "omitted_objects"), ("B", "omitted_relations"), ("C", "omitted_judgments")])
def test_missing_target_keeps_full_denominator(target, category):
    d, r, a = fixture()
    a["claims"] = [c for c in a["claims"] if c["target_id"] != target]
    result = ev.score(d, r, a, "h")
    assert result["status"] == "FAIL"
    assert result["denominators"]["target_universe"] == 3
    assert result["accepted_over_full_universe"] == 2 / 3
    assert result["counts"][category] == 1


def test_extra_output_is_explicit_failure():
    d, r, a = fixture()
    a["claims"].append({"target_id": "X", "status": "DETERMINED", "conclusion": "invented"})
    result = ev.score(d, r, a, "h")
    assert result["status"] == "FAIL" and result["counts"]["extra_outputs"] == 1
    assert result["denominators"]["target_universe"] == 3


def test_unsupported_determination_and_unjustified_abstention():
    d, r, a = fixture()
    a["claims"][2].update(status="DETERMINED", conclusion="invented")
    assert ev.score(d, r, a, "h")["counts"]["wrong_determined"] == 1
    a["claims"][0].update(status="UNRESOLVED", conclusion=None, quantity=None, reason="Unsure")
    assert ev.score(d, r, a, "h")["counts"]["deferred_decidable"] == 1


def test_invalid_evidence_never_counts_as_correct():
    d, r, a = fixture()
    a["claims"][0]["evidence_refs"] = ["UNKNOWN:1"]
    assert ev.score(d, r, a, "h")["counts"]["wrong_determined"] == 1


@pytest.mark.parametrize("quantity", [0, {"value": 0, "unit": "m", "basis": "unknown"}])
def test_unresolved_cannot_be_written_as_zero(quantity):
    d, r, a = fixture()
    a["claims"][2]["quantity"] = quantity
    with pytest.raises(ValueError, match="must be null"):
        ev.score(d, r, a, "h")


@pytest.mark.parametrize("field,value", [("unit", "m"), ("basis", "installed length")])
def test_equal_numbers_with_different_basis_are_not_comparable(field, value):
    d, r, a = fixture()
    a["claims"][0]["quantity"][field] = value
    result = ev.score(d, r, a, "h")
    assert result["quantities"][0]["status"] == "INCOMPARABLE"
    assert result["quantities"][0]["absolute_error"] is None


def test_coverage_omissions_and_extras_are_separate_from_target_success():
    d, r, a = fixture()
    a["coverage_regions"][0].update(object_ids=["1", "X"], relation_ids=[])
    result = ev.score(d, r, a, "h")
    assert result["accepted_over_full_universe"] == 1
    assert result["status"] == "FAIL"
    assert result["coverage_regions"][0]["objects"]["omitted"] == ["2"]
    assert result["coverage_regions"][0]["objects"]["extra"] == ["X"]
    assert result["coverage_regions"][0]["relations"]["omitted"] == ["1->2"]
    assert result["coverage_totals"]["objects"]["reference_total"] == 2
    assert result["coverage_totals"]["objects"]["omitted_count"] == 1


@pytest.mark.parametrize("field", ["input_sha256", "model_config", "budget"])
def test_compare_rejects_unpaired_configuration(field):
    d, r, a = fixture()
    b = copy.deepcopy(a)
    b.update(version_label="candidate", run_id="b1")
    b[field] = "different"
    with pytest.raises(ValueError, match="mismatch"):
        ev.compare(d, r, [a], [b], "h")


def test_one_repeat_has_unknown_variation_and_three_do_not_triple_samples():
    d, r, a = fixture()
    b = copy.deepcopy(a)
    b.update(version_label="candidate", run_id="b1")
    result = ev.compare(d, r, [a], [b], "h")
    assert result["summary"]["baseline"]["accepted_rate_range"] is None
    assert result["summary"]["baseline"]["human_review_minutes_mean"] is None
    aa, bb = [], []
    for i in range(1, 4):
        aa.append({**a, "replicate": i, "run_id": "a" + str(i)})
        bb.append({**b, "replicate": i, "run_id": "b" + str(i)})
    result = ev.compare(d, r, aa, bb, "h")
    assert result["summary"]["candidate"]["ambiguity_group_count"] == 1
    assert result["summary"]["candidate"]["run_count"] == 3


def test_duplicate_ids_bad_hash_and_fake_human_authority_rejected():
    d, r, a = fixture()
    a["claims"].append(copy.deepcopy(a["claims"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        ev.score(d, r, a, "h")
    a = fixture()[2]
    r["input_sha256"] = "other"
    with pytest.raises(ValueError, match="hash mismatch"):
        ev.score(d, r, a, "h")
    r = fixture()[1]
    r["authority"]["human_approved"] = True
    with pytest.raises(ValueError, match="contradicts"):
        ev.score(d, r, a, "h")


def test_cross_split_source_and_block_family_leakage_rejected():
    d, _, _ = fixture()
    g = copy.deepcopy(d["groups"][0])
    g.update(group_id="debug", split="DEBUG")
    g["targets"] = [{"target_id": "D", "kind": "JUDGMENT"}]
    d["groups"].append(g)
    with pytest.raises(ValueError, match="source crosses"):
        ev.validate_input(d)
    d["sources"].append({"source_id": "T", "sha256": "b" * 64})
    g["source_id"] = "T"
    d["groups"][0]["isolation_keys"]["block_family"] = ["same-family"]
    g["isolation_keys"] = {"floor": ["different"], "frame": ["different"], "block_family": ["same-family"]}
    with pytest.raises(ValueError, match="split leakage"):
        ev.validate_input(d)


def test_audit_rejects_answer_assets_unlisted_files_path_traversal_and_reference_in_runtime(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    p = root / "input.json"
    d, r, _ = fixture()
    p.write_text(json.dumps(d), encoding="utf-8")
    assert ev.audit_bundle(p)["status"] == "PASS"
    hidden = root / "notes.json"
    hidden.write_text('{"correct_answer":1}', encoding="utf-8")
    assert "unlisted" in ev.audit_bundle(p)["errors"][0]
    d["assets"] = [{"path": "notes.json", "sha256": ev.digest(hidden)}]
    p.write_text(json.dumps(d), encoding="utf-8")
    assert "answer fields" in ev.audit_bundle(p)["errors"][0]
    d["assets"] = [{"path": "../outside.json", "sha256": "x"}]
    p.write_text(json.dumps(d), encoding="utf-8")
    assert "escapes" in ev.audit_bundle(p)["errors"][0]
    hidden.unlink()
    d["assets"] = []
    p.write_text(json.dumps(d), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    r["input_sha256"] = ev.digest(p)
    ref = runtime / "private.json"
    ref.write_text(json.dumps(r), encoding="utf-8")
    assert "inside runtime" in ev.audit_bundle(p, ref, runtime)["errors"][0]


def test_two_public_roots_audit_hash_alias_leakage_and_legal_control():
    d, _, _ = fixture()
    peer = copy.deepcopy(d)
    peer["sources"][0].update(source_id="T", sha256="b" * 64)
    peer["groups"][0].update(group_id="H", source_id="T", split="DEBUG",
                              isolation_keys={"floor": ["T-F"], "frame": ["T-P"], "block_family": []},
                              targets=[{"target_id": "D", "kind": "JUDGMENT"}])
    peer["coverage_regions"] = [{"region_id": "Q", "source_id": "T", "split": "DEBUG"}]
    ev.validate_split_manifests([d, peer])
    peer["sources"][0]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="source crosses"):
        ev.validate_split_manifests([d, peer])


def test_run_budget_version_and_unreviewed_authority_cannot_be_faked():
    d, r, a = fixture()
    a["budget"] = {"tool_calls": 0}
    with pytest.raises(ValueError, match="budget limits"):
        ev.score(d, r, a, "h")
    a = fixture()[2]
    a["model_config"]["effort"] = ""
    with pytest.raises(ValueError, match="model_config"):
        ev.score(d, r, a, "h")
    a = fixture()[2]
    b = copy.deepcopy(a)
    b["run_id"] = "b1"
    with pytest.raises(ValueError, match="version identities"):
        ev.compare(d, r, [a], [b], "h")
    r["authority"] = {"kind": "HUMAN_REVIEWED_FROM_SOURCE", "human_approved": True, "decision_subject": "reviewer"}
    with pytest.raises(ValueError, match="not supported"):
        ev.score(d, r, a, "h")


def test_quantity_error_and_repetition_spread_are_in_same_basis():
    d, r, a = fixture()
    b = copy.deepcopy(a)
    b.update(version_label="candidate", run_id="b1")
    b["claims"][0]["quantity"]["value"] = 3
    second = copy.deepcopy(b)
    second.update(run_id="b2", replicate=2)
    second["claims"][0]["quantity"]["value"] = 4
    aa = [a, {**a, "run_id": "a2", "replicate": 2}]
    result = ev.compare(d, r, aa, [b, second], "h")
    assert result["runs"]["candidate"][0]["quantities"][0]["absolute_error"] == 1
    assert result["summary"]["candidate"]["quantity_repeat_spread"][0]["range"] == 1


def test_repeat_configuration_drift_cannot_be_reported_as_random_variation():
    d, r, a = fixture()
    b = copy.deepcopy(a)
    b.update(version_label="candidate", run_id="b1")
    aa = [a, {**a, "run_id": "a2", "replicate": 2, "budget": {"tool_calls": 20}}]
    bb = [b, {**b, "run_id": "b2", "replicate": 2, "budget": {"tool_calls": 20}}]
    with pytest.raises(ValueError, match="configuration mismatch"):
        ev.compare(d, r, aa, bb, "h")


@pytest.mark.parametrize("evidence", ["model says approved", {"receipt": "unverified"}, [], None])
def test_human_authority_is_conservatively_unsupported(evidence):
    d, r, a = fixture()
    r["authority"].update(kind="HUMAN_REVIEWED_FROM_SOURCE", human_approved=True, approval_evidence=evidence)
    with pytest.raises(ValueError, match="not supported"):
        ev.score(d, r, a, "h")


def test_model_authority_cannot_smuggle_human_approval_evidence():
    d, r, a = fixture()
    r["authority"]["approval_evidence"] = "unverified human receipt"
    with pytest.raises(ValueError, match="cannot carry"):
        ev.score(d, r, a, "h")


def _asset_bundle(tmp_path, name, payload):
    public = tmp_path / "public"
    public.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    asset = public / name
    asset.write_bytes(payload)
    d, r, _ = fixture()
    d["assets"] = [{"path": name, "sha256": ev.digest(asset)}]
    d["sources"][0].update(exchange_file=name, exchange_sha256=ev.digest(asset))
    ip = public / "input.json"
    ip.write_text(json.dumps(d), encoding="utf-8")
    r["input_sha256"] = ev.digest(ip)
    rp = tmp_path / "private.json"
    rp.write_text(json.dumps(r), encoding="utf-8")
    return ip, rp, runtime, asset


@pytest.mark.parametrize("name,payload", [("source.dxf", b"source geometry bytes"),
                                           ("source.png", b"source picture bytes"),
                                           ("neutral.jsonl", b'{"entity":"LINE"}\n')])
@pytest.mark.parametrize("with_reference", [False, True])
def test_known_public_assets_cannot_enter_runtime_after_rename(tmp_path, name, payload, with_reference):
    ip, rp, runtime, asset = _asset_bundle(tmp_path, name, payload)
    assert ev.audit_bundle(ip, rp if with_reference else None, runtime)["status"] == "PASS"
    nested = runtime / "changed-folder"
    nested.mkdir()
    (nested / "changed-name.bin").write_bytes(asset.read_bytes())
    report = ev.audit_bundle(ip, rp if with_reference else None, runtime)
    assert report["status"] == "FAIL" and "public benchmark input/asset" in report["errors"][0]


@pytest.mark.parametrize("suffix", [".json", ".geojson", ".jsonl", ".ndjson", ".JSONL"])
def test_structured_assets_have_identical_answer_field_policy(tmp_path, suffix):
    payload = b'{"entity":"LINE","nested":[{"correct_answer":42}]}\n'
    ip, rp, runtime, _ = _asset_bundle(tmp_path, "neutral" + suffix, payload)
    report = ev.audit_bundle(ip, rp, runtime)
    assert report["status"] == "FAIL" and "answer fields" in report["errors"][0]


def test_jsonl_later_line_and_malformed_record_do_not_bypass_audit(tmp_path):
    ip, rp, runtime, asset = _asset_bundle(tmp_path, "neutral.jsonl", b'{"entity":"LINE"}\n')
    for payload, message in [(b'{"entity":"LINE"}\n\n{"nested":{"correct_answer":42}}\n', "answer fields"),
                             (b'{"entity":"LINE"}\n{broken}\n', "invalid structured asset")]:
        asset.write_bytes(payload)
        d = ev.read(ip)
        d["assets"][0]["sha256"] = d["sources"][0]["exchange_sha256"] = ev.digest(asset)
        ip.write_text(json.dumps(d), encoding="utf-8")
        r = ev.read(rp)
        r["input_sha256"] = ev.digest(ip)
        rp.write_text(json.dumps(r), encoding="utf-8")
        report = ev.audit_bundle(ip, rp, runtime)
        assert report["status"] == "FAIL" and message in report["errors"][0]


def test_runtime_jsonl_checks_private_fields_without_reference_argument(tmp_path):
    ip, _, runtime, _ = _asset_bundle(tmp_path, "source.dxf", b"source geometry")
    (runtime / "extra.ndjson").write_text('{"ordinary":1}\n{"correct_answer":42}\n', encoding="utf-8")
    report = ev.audit_bundle(ip, runtime_root=runtime)
    assert report["status"] == "FAIL" and "private reference fields" in report["errors"][0]

