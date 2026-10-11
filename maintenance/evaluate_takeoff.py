#!/usr/bin/env python3
"""Compare independently prepared takeoffs without treating totals as accuracy.

Maintenance only. This small adapter reuses the object-set, duplicate and unresolved
semantics of _qto-benchmark/scripts/score_benchmark.py, without its phase-specific
label manifests. Provenance checks detect declared reuse and source identity; they
cannot prove independent reasoning, historical blindness, or source correctness.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

INPUT_VERSION = "qto-independent-evaluation-input/1.0"
REPORT_VERSION = "qto-independent-evaluation-report/1.0"
# Only dimensional conversions; no DN, material, quantity-kind or basis inference.
UNITS = {
    "mm": ("m", .001), "cm": ("m", .01), "m": ("m", 1.),
    "mm2": ("m2", 1e-6), "cm2": ("m2", .0001), "m2": ("m2", 1.),
    "mm²": ("m2", 1e-6), "cm²": ("m2", .0001), "m²": ("m2", 1.),
    "mm3": ("m3", 1e-9), "cm3": ("m3", 1e-6), "m3": ("m3", 1.),
    "mm³": ("m3", 1e-9), "cm³": ("m3", 1e-6), "m³": ("m3", 1.),
    "g": ("kg", .001), "kg": ("kg", 1.), "t": ("kg", 1000.),
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def records_sha256(records: list[dict]) -> str:
    return hashlib.sha256(canonical(records).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_ref(path: str | Path, role: str) -> dict:
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": file_sha256(path), "role": role}


def freeze_dataset(dataset: dict, output: str | Path) -> dict:
    """Create a new receipt now; never backdate or replace an existing frozen file."""
    result = deepcopy(dataset)
    metadata = result.setdefault("metadata", {})
    if metadata.get("frozen_at") or metadata.get("records_sha256"):
        raise ValueError("freeze_dataset accepts an unfrozen dataset only; preserve existing receipts")
    metadata["frozen_at"] = datetime.now(timezone.utc).isoformat()
    metadata["records_sha256"] = records_sha256(result["records"])
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def identity(record: dict) -> tuple[str, str]:
    return record["physical_object_key"], record["quantity_kind"]


def item_key(key: tuple[str, str]) -> dict:
    return {"physical_object_key": key[0], "quantity_kind": key[1]}


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def evidence_issues(record: dict) -> list[str]:
    issues = []
    if not record.get("evidence_refs"):
        issues.append("evidence_missing")
    # An omitted status preserves legacy 'citations supplied' semantics only.
    # Once an explicit status is present, only supported evidence is eligible.
    if "evidence_status" in record and record["evidence_status"] != "supported":
        issues.append("evidence_status_not_supported")
    return issues


def validate_dataset(dataset: Any, role: str, verify_sources: bool) -> list[dict]:
    issues = []
    def add(code, **detail):
        issues.append({"code": code, "role": role, **detail})
    if not isinstance(dataset, dict) or not isinstance(dataset.get("metadata"), dict) or not isinstance(dataset.get("records"), list):
        return [{"code": "INVALID_DATASET", "role": role}]
    meta = dataset["metadata"]
    for field in ("artifact_id", "scope_id", "basis", "frozen_at"):
        if not isinstance(meta.get(field), str) or not meta[field].strip():
            add("MISSING_METADATA", field=field)
    if meta.get("role") != role:
        add("ROLE_MISMATCH", actual=meta.get("role"))
    if not isinstance(meta.get("scope_complete"), bool):
        add("SCOPE_COMPLETENESS_UNDECLARED")
    try:
        timestamp = datetime.fromisoformat(meta.get("frozen_at", "").replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            add("FREEZE_TIME_WITHOUT_TIMEZONE")
    except (ValueError, AttributeError):
        add("INVALID_FREEZE_TIME")
    try:
        if meta.get("records_sha256") != records_sha256(dataset["records"]):
            add("RECORDS_FREEZE_HASH_MISMATCH")
    except (TypeError, ValueError):
        add("NON_JSON_OR_NONFINITE_RECORDS")
    sources = meta.get("sources")
    if not isinstance(sources, list) or not sources:
        add("SOURCE_PROVENANCE_MISSING")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict) or not all(isinstance(source.get(f), str) and source[f].strip() for f in ("path", "sha256", "role")):
                add("INVALID_SOURCE_REFERENCE", index=index)
                continue
            digest = source["sha256"]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                add("INVALID_SOURCE_HASH", index=index)
            elif verify_sources:
                try:
                    if file_sha256(source["path"]) != digest.lower():
                        add("SOURCE_HASH_MISMATCH", path=source["path"])
                except OSError:
                    add("SOURCE_UNAVAILABLE", path=source["path"])
    artifact_hash = meta.get("artifact_sha256")
    if artifact_hash is not None:
        if not isinstance(artifact_hash, str) or len(artifact_hash) != 64 or any(c not in "0123456789abcdef" for c in artifact_hash.lower()):
            add("INVALID_ARTIFACT_HASH")
        elif meta.get("artifact_path") and verify_sources:
            try:
                if file_sha256(meta["artifact_path"]) != artifact_hash.lower():
                    add("ARTIFACT_HASH_MISMATCH", path=meta["artifact_path"])
            except (OSError, TypeError):
                add("ARTIFACT_UNAVAILABLE", path=meta["artifact_path"])
    record_ids = set()
    for index, row in enumerate(dataset["records"]):
        if not isinstance(row, dict):
            add("INVALID_RECORD", index=index)
            continue
        for field in ("record_id", "physical_object_key", "quantity_kind", "classification", "unit", "measurement_basis"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                add("MISSING_RECORD_FIELD", index=index, field=field)
        record_id = row.get("record_id")
        if isinstance(record_id, str):
            if record_id in record_ids:
                add("DUPLICATE_RECORD_ID", record_id=record_id)
            record_ids.add(record_id)
        if "specification" not in row:
            add("MISSING_RECORD_FIELD", index=index, field="specification")
        if row.get("status") not in ("determined", "unresolved", "excluded"):
            add("INVALID_RECORD_STATUS", index=index)
        if row.get("status") == "determined" and not finite_number(row.get("quantity")):
            add("DETERMINED_WITHOUT_FINITE_QUANTITY", index=index)
        if row.get("status") == "unresolved" and row.get("quantity") is not None:
            add("UNRESOLVED_HAS_NUMERIC_QUANTITY", index=index, quantity=row.get("quantity"))
        if row.get("status") == "excluded" and row.get("quantity") not in (None, 0):
            add("EXCLUDED_HAS_NONZERO_QUANTITY", index=index)
        evidence = row.get("evidence_refs", [])
        if not isinstance(evidence, list) or any(not isinstance(ref, str) or not ref.strip() for ref in evidence) or not isinstance(row.get("relations", {}), dict):
            add("INVALID_RECORD_EVIDENCE_OR_RELATIONS", index=index)
    return issues


def check_independence(result: dict, reference: dict) -> list[dict]:
    actual, expected = result["metadata"], reference["metadata"]
    issues = []
    def add(code):
        issues.append({"code": code})
    actual_id = actual["artifact_id"]
    if actual_id == expected["artifact_id"]:
        add("REFERENCE_IS_RESULT_ARTIFACT")
    if actual_id in expected.get("derived_from_artifact_ids", []):
        add("REFERENCE_DERIVED_FROM_RESULT")
    if expected["artifact_id"] in actual.get("derived_from_artifact_ids", []):
        add("RESULT_DERIVED_FROM_REFERENCE")
    declaration = expected.get("independence", {})
    if not isinstance(declaration, dict) or actual_id not in declaration.get("independent_of", []) or not declaration.get("method"):
        add("REFERENCE_INDEPENDENCE_UNDECLARED")
    if expected.get("reference_authority") not in {"manual_reference", "adjudicated_drawing", "synthetic_oracle"}:
        add("REFERENCE_AUTHORITY_UNDECLARED")
    if any(source.get("role") in {"frozen_result", "result_output", "frozen_takeoff"} for source in expected.get("sources", [])):
        add("REFERENCE_USES_RESULT_OUTPUT")
    a_path, b_path = actual.get("artifact_path"), expected.get("artifact_path")
    if a_path and b_path and Path(a_path).resolve() == Path(b_path).resolve():
        add("REFERENCE_IS_RESULT_FILE")
    actual_hash = (actual.get("artifact_sha256") or "").lower()
    expected_hash = (expected.get("artifact_sha256") or "").lower()
    if actual_hash and actual_hash == expected_hash:
        add("REFERENCE_IS_RESULT_FILE_HASH")
    # Compare the result *artifact* identity, never the common original drawing
    # sources. Independent drawing reviews are allowed to share those sources.
    for source in expected.get("sources", []):
        same_path = bool(a_path) and Path(a_path).resolve() == Path(source["path"]).resolve()
        same_hash = bool(actual_hash) and actual_hash == source["sha256"].lower()
        if same_path or same_hash:
            add("REFERENCE_SOURCE_IS_RESULT_ARTIFACT")
            break
    return issues


def evaluate(data: dict, *, verify_source_files: bool = True) -> dict:
    """Evaluate matching object keys; no expected-data generation or automatic matching."""
    report = {
        "schema_version": REPORT_VERSION, "status": "NOT_ASSESSED", "all_accurate": None,
        "limitations": [
            "Independence checks inspect declarations, derivation links and source identity only; they do not prove blind or independent reasoning.",
            "A manual worksheet is a reference, not an automatically authoritative ground truth. Disputed records are not quantity-scored.",
            "A record hash proves payload consistency with its receipt, not the historical time when its reasoning was performed.",
            "Evidence references and declared support status are checked; the tool does not prove that a citation supports the engineering conclusion.",
        ],
        "provenance": {"independence_verification": "declarations_and_source_identity_only", "source_files_verified": verify_source_files},
        "input_issues": [], "independence_issues": [], "differences": {}, "metrics": {}, "quantity_comparisons": [],
    }
    if not isinstance(data, dict) or data.get("schema_version") != INPUT_VERSION:
        report["input_issues"] = [{"code": "INPUT_SCHEMA_MISMATCH"}]
        report["status"] = "INVALID_INPUT"
        return report
    actual, reference = data.get("result"), data.get("reference")
    report["input_issues"] = validate_dataset(actual, "frozen_takeoff", verify_source_files)
    if reference is not None:
        report["input_issues"] += validate_dataset(reference, "independent_reference", verify_source_files)
    tolerance = data.get("tolerances", {})
    absolute = tolerance.get("absolute_by_unit", {}) if isinstance(tolerance, dict) else {}
    relative = tolerance.get("relative", 0.) if isinstance(tolerance, dict) else None
    if not isinstance(absolute, dict) or not finite_number(relative) or relative < 0 or any(not finite_number(v) or v < 0 for v in absolute.values()):
        report["input_issues"].append({"code": "INVALID_TOLERANCE"})
    if report["input_issues"]:
        report["status"] = "INVALID_INPUT"
        return report
    report["provenance"].update({"result_metadata": actual["metadata"], "reference_metadata": reference["metadata"] if reference else None})
    groups = defaultdict(list)
    for row in actual["records"]:
        groups[identity(row)].append(row)
    differences = report["differences"] = {name: [] for name in (
        "missing", "extra", "unreferenced_output", "duplicates", "relation_mismatches", "classification_mismatches",
        "specification_mismatches", "unresolved", "unsupported_determined", "reference_unresolved", "reference_unsupported", "reference_duplicates", "incomparable")}
    for key, rows in sorted(groups.items()):
        if len(rows) > 1:
            differences["duplicates"].append({**item_key(key), "record_ids": [r["record_id"] for r in rows]})
        for row in rows:
            if row["status"] == "unresolved":
                differences["unresolved"].append({**item_key(key), "record_id": row["record_id"], "reason": row.get("unresolved_reason")})
            if row["status"] == "determined" and evidence_issues(row):
                differences["unsupported_determined"].append({**item_key(key), "record_id": row["record_id"], "reasons": evidence_issues(row)})
    report["metrics"].update({"result_record_count": len(actual["records"]), "result_unique_item_count": len(groups), "reference_object_coverage": None, "quantity_comparison_coverage": None})
    if reference is None:
        report["limitations"].append("No independent reference supplied; object completeness and quantity accuracy are not assessed.")
        return report
    report["independence_issues"] = check_independence(actual, reference)
    if actual["metadata"]["scope_id"] != reference["metadata"]["scope_id"]:
        report["independence_issues"].append({"code": "SCOPE_ID_MISMATCH"})
    if report["independence_issues"]:
        report["status"] = "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"
        return report
    expected_groups = defaultdict(list)
    for row in reference["records"]:
        expected_groups[identity(row)].append(row)
    for key, rows in sorted(expected_groups.items()):
        if len(rows) > 1:
            differences["reference_duplicates"].append({**item_key(key), "record_ids": [r["record_id"] for r in rows]})
    required = {key for key, rows in expected_groups.items() if any(r["status"] != "excluded" for r in rows)}
    present = {key for key, rows in groups.items() if any(r["status"] != "excluded" for r in rows)}
    for key in sorted(required - present):
        differences["missing"].append(item_key(key))
    for key in sorted(present - required):
        category = "extra" if reference["metadata"]["scope_complete"] or key in expected_groups else "unreferenced_output"
        differences[category].append(item_key(key))
    comparable_reference = set()
    aggregate = defaultdict(lambda: {"compared_item_count": 0, "absolute_error_sum": 0., "reference_absolute_quantity_sum": 0., "out_of_tolerance_count": 0})
    for key, ref_rows in sorted(expected_groups.items()):
        if len(ref_rows) != 1:
            continue
        ref = ref_rows[0]
        disputed = ref.get("adjudication_status") in ("disputed", "unresolved")
        ref_evidence_issues = evidence_issues(ref)
        if ref["status"] == "determined" and ref_evidence_issues:
            differences["reference_unsupported"].append({**item_key(key), "record_id": ref["record_id"], "evidence_status": ref.get("evidence_status"), "reasons": ref_evidence_issues})
            differences["incomparable"].append({**item_key(key), "reasons": ["reference_" + reason for reason in ref_evidence_issues]})
            continue
        if ref["status"] == "unresolved" or disputed:
            differences["reference_unresolved"].append({**item_key(key), "record_id": ref["record_id"], "reason": ref.get("unresolved_reason")})
        elif ref["status"] == "determined" and ref.get("evidence_refs"):
            comparable_reference.add(key)
        else:
            if ref["status"] == "determined":
                differences["incomparable"].append({**item_key(key), "reasons": ["reference_evidence_missing"]})
            continue
        rows = groups.get(key, [])
        if not rows:
            continue
        reasons = []
        if len(rows) != 1:
            reasons.append("duplicate_result")
        if disputed or ref["status"] != "determined":
            reasons.append("reference_unresolved_or_disputed")
        if reasons:
            differences["incomparable"].append({**item_key(key), "reasons": reasons})
            continue
        row = rows[0]
        if row["status"] != "determined":
            differences["incomparable"].append({**item_key(key), "reasons": ["result_" + row["status"]]})
            continue
        for field, category in (("classification", "classification_mismatches"), ("specification", "specification_mismatches")):
            if canonical(row[field]) != canonical(ref[field]):
                differences[category].append({**item_key(key), "expected": ref[field], "actual": row[field]})
                reasons.append(field + "_mismatch")
        if row["measurement_basis"] != ref["measurement_basis"]:
            reasons.append("measurement_basis_mismatch")
        for relation, expected in sorted(ref.get("relations", {}).items()):
            found = row.get("relations", {}).get(relation)
            if canonical(found) != canonical(expected):
                differences["relation_mismatches"].append({**item_key(key), "relation": relation, "expected": expected, "actual": found})
        a_unit, a_factor = UNITS.get(row["unit"], (row["unit"], 1.))
        e_unit, e_factor = UNITS.get(ref["unit"], (ref["unit"], 1.))
        if a_unit != e_unit:
            reasons.append("unit_dimension_mismatch")
        if reasons:
            differences["incomparable"].append({**item_key(key), "reasons": reasons})
            continue
        a_value, e_value = row["quantity"] * a_factor, ref["quantity"] * e_factor
        if not math.isfinite(a_value) or not math.isfinite(e_value):
            differences["incomparable"].append({**item_key(key), "reasons": ["unit_conversion_numeric_overflow"]})
            continue
        error = abs(a_value - e_value)
        limit = max(absolute.get(e_unit, 0.), relative * abs(e_value))
        comparison = {**item_key(key), "result_record_id": row["record_id"], "reference_record_id": ref["record_id"],
                      "unit": e_unit, "actual": a_value, "reference": e_value, "absolute_error": error,
                      "relative_absolute_error": ratio(error, abs(e_value)), "tolerance": limit, "within_tolerance": error <= limit}
        report["quantity_comparisons"].append(comparison)
        totals = aggregate[e_unit]
        totals["compared_item_count"] += 1
        totals["absolute_error_sum"] += error
        totals["reference_absolute_quantity_sum"] += abs(e_value)
        totals["out_of_tolerance_count"] += int(error > limit)
    for totals in aggregate.values():
        totals["relative_absolute_error_sum"] = ratio(totals["absolute_error_sum"], totals["reference_absolute_quantity_sum"])
    report["metrics"].update({
        "reference_required_item_count": len(required), "reference_present_item_count": len(required & present),
        "reference_object_coverage": ratio(len(required & present), len(required)),
        "reference_quantity_eligible_count": len(comparable_reference),
        "quantity_compared_item_count": len(report["quantity_comparisons"]),
        "quantity_comparison_coverage": ratio(len(report["quantity_comparisons"]), len(comparable_reference)),
        "quantity_error_by_unit": dict(sorted(aggregate.items())),
        "difference_counts": {key: len(value) for key, value in differences.items()},
        "scope_completeness": "declared_complete" if actual["metadata"]["scope_complete"] and reference["metadata"]["scope_complete"] else "partial_or_unknown",
    })
    has_error = any(differences.values()) or any(not row["within_tolerance"] for row in report["quantity_comparisons"])
    if has_error:
        report["status"] = "DIFFERENCES_FOUND"
    elif not required or not report["quantity_comparisons"]:
        report["status"] = "NOT_ASSESSED"
    elif report["metrics"]["scope_completeness"] != "declared_complete":
        report["status"] = "NO_DIFFERENCES_IN_PARTIAL_REFERENCE"
    else:
        report["status"] = "NO_DIFFERENCES_WITHIN_DECLARED_REFERENCE"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-source-file-check", action="store_true", help="diagnostic only; report clearly records that source files were not verified")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    report = evaluate(data, verify_source_files=not args.skip_source_file_check)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=False))
    return 2 if report["status"] in {"INVALID_INPUT", "INDEPENDENCE_OR_SCOPE_NOT_ESTABLISHED"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
