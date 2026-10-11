"""Independent hand-specified counterexamples, not totals copied from a takeoff."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[1] / "maintenance/evaluate_takeoff.py"
_spec = importlib.util.spec_from_file_location("takeoff_evaluation_20261011", _path)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def row(key="pipe-A", quantity=4., **overrides):
    result = dict(record_id=key, physical_object_key=key, quantity_kind="length", classification="pipe",
                  specification="DN32", unit="m", measurement_basis="centerline_including_valve",
                  status="determined", quantity=quantity, evidence_refs=["drawing:entity/ABC"],
                  relations={"system": "supply"})
    result.update(overrides)
    return result


def datasets(tmp_path, actual=None, expected=None, complete=True):
    actual = [row()] if actual is None else actual
    expected = [row()] if expected is None else expected
    drawing = tmp_path / "source-drawing.txt"
    drawing.write_text("Synthetic independent geometry: A=4m, B=6m. Not an actual project.", encoding="utf8")
    oracle = tmp_path / "hand-oracle.txt"
    oracle.write_text("Hand specified oracle: A=4m, B=6m. Independent of output.", encoding="utf8")
    data = {"schema_version": m.INPUT_VERSION, "tolerances": {"absolute_by_unit": {"m": 1e-9}, "relative": 1e-9}}
    for role, records, artifact_id, source in (("frozen_takeoff", actual, "calculated", drawing), ("independent_reference", expected, "oracle", oracle)):
        metadata = dict(artifact_id=artifact_id, role=role, scope_id="finite_branch", scope_complete=complete,
                        basis="drawing_takeoff" if role == "frozen_takeoff" else "synthetic_oracle",
                        historical_exposure=False, sources=[m.source_ref(source, "synthetic_fixture")])
        if role == "independent_reference":
            metadata.update(reference_authority="synthetic_oracle", independence={"independent_of": ["calculated"], "method": "hand arithmetic from fixture dimensions"})
        frozen = m.freeze_dataset(dict(metadata=metadata, records=records), tmp_path / (artifact_id + ".json"))
        data["result" if role == "frozen_takeoff" else "reference"] = frozen
    return data


def rehash(data, role="result"):
    data[role]["metadata"]["records_sha256"] = m.records_sha256(data[role]["records"])


def test_equal_totals_do_not_hide_missing_and_extra(tmp_path):
    data = datasets(tmp_path, [row("A", 4), row("C", 6)], [row("A", 4), row("B", 6)])
    report = m.evaluate(data)
    assert report["status"] == "DIFFERENCES_FOUND"
    assert report["differences"]["missing"] == [{"physical_object_key": "B", "quantity_kind": "length"}]
    assert report["differences"]["extra"] == [{"physical_object_key": "C", "quantity_kind": "length"}]
    assert report["metrics"]["reference_object_coverage"] == .5
    assert report["metrics"]["quantity_comparison_coverage"] == .5


def test_opposite_errors_do_not_cancel(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row("A", 6), row("B", 4)], [row("A", 4), row("B", 6)]))
    totals = report["metrics"]["quantity_error_by_unit"]["m"]
    assert totals["absolute_error_sum"] == 4
    assert totals["out_of_tolerance_count"] == 2
    assert totals["relative_absolute_error_sum"] == .4


def test_unresolved_is_present_but_not_quantity_compared(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row(status="unresolved", quantity=None, unresolved_reason="height absent")]))
    assert report["metrics"]["reference_object_coverage"] == 1
    assert report["metrics"]["quantity_comparison_coverage"] == 0
    assert not report["quantity_comparisons"]
    assert len(report["differences"]["unresolved"]) == 1
    assert report["all_accurate"] is None


def test_unresolved_zero_is_rejected(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row(status="unresolved", quantity=0)]))
    assert report["status"] == "INVALID_INPUT"
    assert "UNRESOLVED_HAS_NUMERIC_QUANTITY" in {x["code"] for x in report["input_issues"]}


def test_duplicate_physical_output_is_not_added_or_overwritten(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row(record_id="first", quantity=2), row(record_id="second", quantity=2)]))
    assert len(report["differences"]["duplicates"]) == 1
    assert not report["quantity_comparisons"]
    assert report["metrics"]["quantity_comparison_coverage"] == 0


def test_relation_error_survives_equal_quantity(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row(relations={"system": "return"})]))
    assert len(report["differences"]["relation_mismatches"]) == 1
    assert report["quantity_comparisons"][0]["absolute_error"] == 0
    assert report["status"] == "DIFFERENCES_FOUND"


@pytest.mark.parametrize("change,reason", [
    ({"measurement_basis": "net_pipe_excluding_valve"}, "measurement_basis_mismatch"),
    ({"specification": "DN40"}, "specification_mismatch"),
    ({"classification": "conduit"}, "classification_mismatch"),
    ({"unit": "m2"}, "unit_dimension_mismatch"),
])
def test_incompatible_engineering_quantities_are_not_scored(tmp_path, change, reason):
    report = m.evaluate(datasets(tmp_path, [row(**change)]))
    assert not report["quantity_comparisons"]
    assert reason in report["differences"]["incomparable"][0]["reasons"]


@pytest.mark.parametrize("actual,expected,unit", [
    (row(quantity=4000, unit="mm"), row(quantity=4, unit="m"), "m"),
    (row(quantity=2_000_000, unit="mm²"), row(quantity=2, unit="m2"), "m2"),
    (row(quantity=.2, unit="t"), row(quantity=200, unit="kg"), "kg"),
])
def test_only_same_dimension_units_are_normalized(tmp_path, actual, expected, unit):
    report = m.evaluate(datasets(tmp_path, [actual], [expected]))
    assert report["quantity_comparisons"][0]["unit"] == unit
    assert report["quantity_comparisons"][0]["absolute_error"] == 0
    assert report["status"] == "NO_DIFFERENCES_WITHIN_DECLARED_REFERENCE"


def test_determined_without_evidence_is_visible_even_without_reference(tmp_path):
    data = datasets(tmp_path, [row(evidence_refs=[])])
    data["reference"] = None
    report = m.evaluate(data)
    assert report["status"] == "NOT_ASSESSED"
    assert len(report["differences"]["unsupported_determined"]) == 1
    assert report["metrics"]["reference_object_coverage"] is None


def test_empty_reference_does_not_produce_perfect_accuracy(tmp_path):
    report = m.evaluate(datasets(tmp_path, [], []))
    assert report["status"] == "NOT_ASSESSED"
    assert report["metrics"]["reference_object_coverage"] is None
    assert report["all_accurate"] is None


def test_partial_reference_does_not_call_unknown_outputs_wrong(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row(), row("unknown", 12)], complete=False))
    assert not report["differences"]["extra"]
    assert len(report["differences"]["unreferenced_output"]) == 1


def test_partial_reference_cannot_pass_as_full_scope(tmp_path):
    report = m.evaluate(datasets(tmp_path, complete=False))
    assert report["status"] == "NO_DIFFERENCES_IN_PARTIAL_REFERENCE"
    assert report["all_accurate"] is None


def test_reference_dispute_is_not_treated_as_truth(tmp_path):
    report = m.evaluate(datasets(tmp_path, expected=[row(quantity=900, adjudication_status="disputed")]))
    assert not report["quantity_comparisons"]
    assert len(report["differences"]["reference_unresolved"]) == 1
    assert report["metrics"]["quantity_comparison_coverage"] is None


def test_reference_without_evidence_is_not_quantity_scored(tmp_path):
    report = m.evaluate(datasets(tmp_path, expected=[row(evidence_refs=[])]))
    assert not report["quantity_comparisons"]
    assert report["differences"]["incomparable"][0]["reasons"] == ["reference_evidence_missing"]


@pytest.mark.parametrize("metadata,code", [
    ({"artifact_id": "calculated"}, "REFERENCE_IS_RESULT_ARTIFACT"),
    ({"derived_from_artifact_ids": ["calculated"]}, "REFERENCE_DERIVED_FROM_RESULT"),
    ({"independence": {}}, "REFERENCE_INDEPENDENCE_UNDECLARED"),
])
def test_result_reused_as_reference_is_rejected(tmp_path, metadata, code):
    data = datasets(tmp_path)
    data["reference"]["metadata"].update(metadata)
    report = m.evaluate(data)
    assert report["status"] == "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"
    assert code in {r["code"] for r in report["independence_issues"]}
    assert not report["quantity_comparisons"]


def test_worksheet_total_does_not_become_a_drawing_result_input(tmp_path):
    data = datasets(tmp_path)
    data["result"]["metadata"]["derived_from_artifact_ids"] = ["oracle"]
    assert m.evaluate(data)["status"] == "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"


def test_original_file_mutation_and_frozen_record_mutation_detected(tmp_path):
    data = datasets(tmp_path)
    data["result"]["records"][0]["quantity"] = 100
    (tmp_path / "source-drawing.txt").write_text("changed", encoding="utf8")
    report = m.evaluate(data)
    assert {"SOURCE_HASH_MISMATCH", "RECORDS_FREEZE_HASH_MISMATCH"} <= {r["code"] for r in report["input_issues"]}


def test_freeze_never_rewrites_or_backdates(tmp_path):
    data = datasets(tmp_path)
    with pytest.raises(ValueError, match="unfrozen"):
        m.freeze_dataset(data["result"], tmp_path / "forged.json")
    clean = deepcopy(data["result"])
    clean["metadata"].pop("frozen_at")
    clean["metadata"].pop("records_sha256")
    before = (tmp_path / "calculated.json").read_bytes()
    with pytest.raises(FileExistsError):
        m.freeze_dataset(clean, tmp_path / "calculated.json")
    assert before == (tmp_path / "calculated.json").read_bytes()


def test_excluded_objects_are_not_missing_but_inclusion_is_error(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row()], [row(status="excluded", quantity=None)]))
    assert not report["differences"]["missing"]
    assert len(report["differences"]["extra"]) == 1


def test_no_cross_dimension_error_grand_total(tmp_path):
    a = [row("pipe", 6), row("wall", 25, unit="m2", quantity_kind="area")]
    b = [row("pipe", 4), row("wall", 20, unit="m2", quantity_kind="area")]
    report = m.evaluate(datasets(tmp_path, a, b))
    assert report["metrics"]["quantity_error_by_unit"]["m"]["absolute_error_sum"] == 2
    assert report["metrics"]["quantity_error_by_unit"]["m2"]["absolute_error_sum"] == 5
    assert "absolute_error_sum" not in report["metrics"]


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf")])
def test_nonfinite_or_boolean_quantity_cannot_be_frozen(tmp_path, invalid):
    if isinstance(invalid, bool):
        assert m.evaluate(datasets(tmp_path, [row(quantity=invalid)]))["status"] == "INVALID_INPUT"
    else:
        with pytest.raises(ValueError):
            datasets(tmp_path, [row(quantity=invalid)])


def test_reference_identity_is_not_assumed_from_matching_totals(tmp_path):
    data = datasets(tmp_path)
    data["reference"]["metadata"]["scope_id"] = "other_level"
    assert m.evaluate(data)["status"] == "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"


def test_provenance_never_claims_blindness_or_certainty(tmp_path):
    data = datasets(tmp_path)
    data["result"]["metadata"]["historical_exposure"] = True
    report = m.evaluate(data)
    assert report["provenance"]["result_metadata"]["historical_exposure"] is True
    assert report["provenance"]["independence_verification"] == "declarations_and_source_identity_only"
    assert report["all_accurate"] is None
    json.dumps(report, allow_nan=False)


def test_unsupported_duplicate_is_reported_per_record(tmp_path):
    report = m.evaluate(datasets(tmp_path, [row(record_id="no-evidence", evidence_refs=[]), row(record_id="has-evidence")]))
    assert [r["record_id"] for r in report["differences"]["unsupported_determined"]] == ["no-evidence"]


@pytest.mark.parametrize("field,value", [("record_id", []), ("status", []), ("evidence_refs", [None])])
def test_malformed_record_returns_diagnostic_instead_of_crashing(tmp_path, field, value):
    data = datasets(tmp_path)
    data["result"]["records"][0][field] = value
    rehash(data)
    assert m.evaluate(data)["status"] == "INVALID_INPUT"


def test_reference_duplicates_are_ambiguous_not_overwritten(tmp_path):
    report = m.evaluate(datasets(tmp_path, expected=[row(record_id="ref-1"), row(record_id="ref-2", quantity=8)]))
    assert len(report["differences"]["reference_duplicates"]) == 1
    assert not report["quantity_comparisons"]


def test_reference_cannot_consume_result_source_with_renamed_id(tmp_path):
    data = datasets(tmp_path)
    data["reference"]["metadata"]["sources"][0]["role"] = "frozen_result"
    report = m.evaluate(data)
    assert report["status"] == "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"
    assert "REFERENCE_USES_RESULT_OUTPUT" in {r["code"] for r in report["independence_issues"]}


@pytest.mark.parametrize("status", ["pending", "unsupported", "absent", "unknown", "draft", None])
def test_reference_declared_without_supported_evidence_is_not_truth(tmp_path, status):
    report = m.evaluate(datasets(tmp_path, expected=[row(evidence_status=status)]))
    assert report["status"] == "DIFFERENCES_FOUND"
    assert len(report["differences"]["reference_unsupported"]) == 1
    assert not report["quantity_comparisons"]
    assert report["metrics"]["reference_quantity_eligible_count"] == 0
    assert report["metrics"]["quantity_comparison_coverage"] is None


def test_explicitly_supported_reference_remains_comparable(tmp_path):
    report = m.evaluate(datasets(tmp_path, expected=[row(evidence_status="supported")]))
    assert report["status"] == "NO_DIFFERENCES_WITHIN_DECLARED_REFERENCE"
    assert report["metrics"]["quantity_comparison_coverage"] == 1


@pytest.mark.parametrize("copy_file", [False, True])
def test_result_artifact_source_cannot_be_disguised_as_worksheet(tmp_path, copy_file):
    data = datasets(tmp_path)
    artifact = tmp_path / "calculated.json"
    data["result"]["metadata"].update(artifact_path=str(artifact), artifact_sha256=m.file_sha256(artifact).upper())
    reference_source = artifact
    if copy_file:
        reference_source = tmp_path / "renamed-manual-worksheet.json"
        reference_source.write_bytes(artifact.read_bytes())
    data["reference"]["metadata"]["sources"] = [m.source_ref(reference_source, "manual_worksheet")]
    report = m.evaluate(data)
    assert report["status"] == "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"
    assert "REFERENCE_SOURCE_IS_RESULT_ARTIFACT" in {r["code"] for r in report["independence_issues"]}
    assert not report["quantity_comparisons"]


def test_independent_review_can_share_original_drawing_sources(tmp_path):
    data = datasets(tmp_path)
    data["result"]["metadata"].update(artifact_path=str(tmp_path / "calculated.json"), artifact_sha256=m.file_sha256(tmp_path / "calculated.json"))
    data["reference"]["metadata"]["sources"] = deepcopy(data["result"]["metadata"]["sources"])
    report = m.evaluate(data)
    assert not report["independence_issues"]
    assert report["status"] == "NO_DIFFERENCES_WITHIN_DECLARED_REFERENCE"


def test_result_artifact_hash_identity_is_case_insensitive(tmp_path):
    data = datasets(tmp_path)
    digest = m.file_sha256(tmp_path / "calculated.json")
    data["result"]["metadata"]["artifact_sha256"] = digest.upper()
    data["reference"]["metadata"]["artifact_sha256"] = digest.lower()
    report = m.evaluate(data)
    assert report["status"] == "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"
    assert "REFERENCE_IS_RESULT_FILE_HASH" in {r["code"] for r in report["independence_issues"]}


def test_declared_result_artifact_hash_is_verified_when_file_available(tmp_path):
    data = datasets(tmp_path)
    data["result"]["metadata"].update(artifact_path=str(tmp_path / "calculated.json"), artifact_sha256="0" * 64)
    report = m.evaluate(data)
    assert report["status"] == "INVALID_INPUT"
    assert "ARTIFACT_HASH_MISMATCH" in {r["code"] for r in report["input_issues"]}
