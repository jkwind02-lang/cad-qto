#!/usr/bin/env python3
"""Maintenance-only ambiguity evaluation. References must stay outside blind inputs.

This is a new explicit-denominator protocol; the frozen phase-1 scorer is unchanged.
All reference decisions retain their declared authority, including model-only ones.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any

VERSION = "qto-ambiguity-evaluation/1.0"
PRIVATE_KEYS = {"acceptable_conclusions", "accepted_values", "human_disposition",
                "adjudication_basis", "reference_decision", "correct_answer",
                "expected_objects", "expected_relations", "gold_status"}
PRIVATE_NAMES = ("答案", "批注", "reference", "answer", "gold", "label")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _invalid_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_invalid_json_constant)


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _private_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return (set(value) & PRIVATE_KEYS) | set().union(*(_private_keys(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_private_keys(v) for v in value), set())
    return set()


def _structured_private_keys(path: Path) -> set[str]:
    """Apply the same answer-field policy to every supported JSON container."""
    suffix = path.suffix.lower()
    if suffix in {".json", ".geojson"}:
        return _private_keys(read(path))
    if suffix in {".jsonl", ".ndjson"}:
        keys = set()
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        value = json.loads(line, parse_constant=_invalid_json_constant)
                    except ValueError as exc:
                        raise ValueError(f"invalid structured asset {path.name}:{line_number}: {exc}") from exc
                    keys.update(_private_keys(value))
        return keys
    return set()


def _index(rows: list[dict], key: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident.strip() or ident in result:
            raise ValueError(f"invalid or duplicate {key}: {ident!r}")
        result[ident] = row
    return result


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_input(data: dict) -> dict[str, dict]:
    if data.get("schema_version") != "qto-ambiguity-input/1.0":
        raise ValueError("unsupported input schema")
    if not data.get("benchmark_id"):
        raise ValueError("benchmark_id missing")
    sources = _index(data.get("sources", []), "source_id")
    if not sources:
        raise ValueError("source universe empty")
    if any(not isinstance(s.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", s["sha256"]) for s in sources.values()):
        raise ValueError("source SHA256 missing or invalid")
    groups = _index(data.get("groups", []), "group_id")
    if not groups:
        raise ValueError("group universe empty")
    seen = {}
    source_splits = {}
    targets = []
    for group in groups.values():
        split = group.get("split")
        if split not in {"DEBUG", "SEALED"}:
            raise ValueError("invalid split")
        if group.get("source_id") not in sources:
            raise ValueError("unknown group source")
        source_id = group["source_id"]
        source_identity = sources[source_id]["sha256"]
        if source_identity in source_splits and source_splits[source_identity] != split:
            raise ValueError("source crosses DEBUG/SEALED")
        source_splits[source_identity] = split
        keys = group.get("isolation_keys", {})
        if not all(k in keys for k in ("floor", "frame", "block_family")):
            raise ValueError("floor/frame/block_family isolation must be explicit")
        for key, values in keys.items():
            if not isinstance(values, list):
                raise ValueError("isolation keys must be lists")
            for value in values:
                if not isinstance(value, str) or not value:
                    raise ValueError("empty isolation key")
                previous = seen.setdefault((key, value), split)
                if previous != split:
                    raise ValueError(f"split leakage: {key} {value}")
        for target in group.get("targets", []):
            if target.get("kind") not in {"OBJECT", "RELATION", "JUDGMENT"}:
                raise ValueError("invalid target kind")
            targets.append({**target, "group_id": group["group_id"], "source_id": source_id})
    indexed = _index(targets, "target_id")
    if not indexed:
        raise ValueError("target universe empty")
    regions = _index(data.get("coverage_regions", []), "region_id")
    for region in regions.values():
        if region.get("source_id") not in sources or region.get("split") != source_splits.get(sources[region["source_id"]]["sha256"]):
            raise ValueError("coverage region source/split mismatch")
    if _private_keys(data):
        raise ValueError("private answer fields in public manifest")
    return indexed


def validate_split_manifests(manifests: list[dict]) -> None:
    """Audit DEBUG and SEALED roots together without moving their assets or answers."""
    combined = {"schema_version": "qto-ambiguity-input/1.0", "benchmark_id": "split-audit",
                "sources": [], "groups": [], "coverage_regions": []}
    sources = {}
    for data in manifests:
        validate_input(data)
        for source in data["sources"]:
            previous = sources.setdefault(source["source_id"], source)
            if previous != source:
                raise ValueError("split source identity conflict")
        combined["groups"].extend(data["groups"])
        combined["coverage_regions"].extend(data.get("coverage_regions", []))
    combined["sources"] = list(sources.values())
    validate_input(combined)


def audit_bundle(input_path: Path, reference_path: Path | None = None,
                 runtime_root: Path | None = None) -> dict:
    errors = []
    root = input_path.parent.resolve()
    try:
        data = read(input_path)
        targets = validate_input(data)
        assets = _index(data.get("assets", []), "path")
        allowed = {input_path.resolve()}
        for name, asset in assets.items():
            win = PureWindowsPath(name)
            p = root / name
            if win.is_absolute() or win.drive or ".." in win.parts or not _inside(p, root):
                raise ValueError(f"asset escapes input root: {name}")
            if p.is_symlink() or not p.is_file():
                raise ValueError(f"asset missing or symlink: {name}")
            if any(word in p.name.lower() for word in PRIVATE_NAMES):
                raise ValueError(f"private-looking asset filename: {name}")
            if digest(p) != asset.get("sha256"):
                raise ValueError(f"asset hash mismatch: {name}")
            if _structured_private_keys(p):
                raise ValueError(f"answer fields in public asset: {name}")
            allowed.add(p.resolve())
        for source in data["sources"]:
            file = source.get("exchange_file")
            if file and (file not in assets or assets[file].get("sha256") != source.get("exchange_sha256")):
                raise ValueError("source exchange identity not in asset manifest")
        for p in root.rglob("*"):
            if p.is_symlink():
                raise ValueError(f"symlink in input root: {p.name}")
            if p.is_file() and p.resolve() not in allowed:
                raise ValueError(f"unlisted input asset: {p.relative_to(root)}")
        if reference_path:
            if _inside(reference_path, root):
                raise ValueError("reference is inside blind input root")
            refhash = digest(reference_path)
            for p in allowed:
                if digest(p) == refhash:
                    raise ValueError("reference content copied into blind input")
            validate_reference(data, read(reference_path), digest(input_path))
        if runtime_root:
            if not runtime_root.is_dir():
                raise ValueError("runtime package root missing or not a directory")
            if reference_path and _inside(reference_path, runtime_root):
                raise ValueError("reference is inside runtime package")
            forbidden = {(p.stat().st_size, digest(p)): "public benchmark input/asset" for p in allowed}
            if reference_path:
                forbidden[(reference_path.stat().st_size, digest(reference_path))] = "private reference"
            sizes = {size for size, _ in forbidden}
            for p in runtime_root.rglob("*"):
                if not p.is_file():
                    continue
                if p.stat().st_size in sizes:
                    label = forbidden.get((p.stat().st_size, digest(p)))
                    if label:
                        raise ValueError(f"{label} content copied into runtime package")
                if _structured_private_keys(p):
                    raise ValueError("private reference fields in runtime package")
        return {"schema_version": VERSION, "status": "PASS", "input_sha256": digest(input_path),
                "asset_count": len(assets), "target_count": len(targets), "errors": []}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return {"schema_version": VERSION, "status": "FAIL", "errors": errors}


def validate_reference(data: dict, reference: dict, input_hash: str) -> dict[str, dict]:
    if reference.get("schema_version") != "qto-ambiguity-reference/1.0":
        raise ValueError("unsupported reference schema")
    if reference.get("input_sha256") != input_hash:
        raise ValueError("reference/input hash mismatch")
    authority = reference.get("authority", {})
    if authority.get("kind") == "HUMAN_REVIEWED_FROM_SOURCE":
        raise ValueError("human-reviewed reference authority is not supported by this protocol version")
    if authority.get("kind") != "INDEPENDENT_MODEL_FROM_SOURCE":
        raise ValueError("reference authority must be explicit")
    if not authority.get("decision_subject") or not isinstance(authority.get("human_approved"), bool):
        raise ValueError("reference authority subject/approval missing")
    if authority["human_approved"] is not False:
        raise ValueError("authority contradicts human approval")
    if authority.get("approval_evidence") is not None:
        raise ValueError("model reference cannot carry human approval evidence")
    targets = validate_input(data)
    refs = _index(reference.get("targets", []), "target_id")
    if set(refs) != set(targets):
        raise ValueError("reference target universe differs from public target universe")
    for ref in refs.values():
        state = ref.get("decision_status")
        if state not in {"DECIDABLE", "INSUFFICIENT", "ADJUDICATION_REQUIRED"}:
            raise ValueError("invalid reference decision status")
        if not ref.get("evidence_refs") or not ref.get("basis"):
            raise ValueError("reference evidence/basis missing")
        if not isinstance(ref["evidence_refs"], list) or any(not isinstance(e, str) or ":" not in e or
                e.split(":", 1)[0] not in {s["source_id"] for s in data["sources"]} for e in ref["evidence_refs"]):
            raise ValueError("reference cites unknown source")
        accepted = ref.get("acceptable_conclusions", [])
        if state == "DECIDABLE" and (not isinstance(accepted, list) or not accepted or None in accepted):
            raise ValueError("decidable reference lacks acceptable conclusions")
        q = ref.get("quantity")
        if q is not None:
            if state != "DECIDABLE" or not _number(q.get("value")) or not q.get("unit") or not q.get("basis"):
                raise ValueError("invalid reference quantity, unknown must be null")
            if not _number(q.get("tolerance")) or q["tolerance"] < 0:
                raise ValueError("invalid numeric tolerance")
    regions = _index(reference.get("coverage_regions", []), "region_id")
    if set(regions) != {r["region_id"] for r in data.get("coverage_regions", [])}:
        raise ValueError("reference coverage universe differs")
    for region in regions.values():
        for field in ("expected_objects", "expected_relations"):
            ids = region.get(field)
            if ids is not None and (not isinstance(ids, list) or len(set(ids)) != len(ids)):
                raise ValueError("invalid coverage census")
        if not region.get("census_basis"):
            raise ValueError("coverage census basis missing")
    return refs


def score(data: dict, reference: dict, run: dict, input_hash: str) -> dict:
    targets = validate_input(data)
    refs = validate_reference(data, reference, input_hash)
    if run.get("schema_version") != "qto-ambiguity-run/1.0" or run.get("input_sha256") != input_hash:
        raise ValueError("run schema/input hash mismatch")
    for field in ("run_id", "version_label", "pair_id"):
        if not isinstance(run.get(field), str) or not run[field].strip():
            raise ValueError(f"run configuration missing: {field}")
    if not isinstance(run.get("model_config"), dict) or not all(
            isinstance(run["model_config"].get(k), str) and run["model_config"][k].strip() for k in ("model", "effort")):
        raise ValueError("model_config model/effort must be explicit")
    if not isinstance(run.get("budget"), dict) or not run["budget"] or any(
            not _number(v) or v <= 0 for v in run["budget"].values()):
        raise ValueError("budget limits must be finite positive numbers")
    if not isinstance(run.get("replicate"), int) or isinstance(run["replicate"], bool) or run["replicate"] < 1:
        raise ValueError("replicate must be a positive integer")
    minutes = run.get("human_review_minutes")
    if minutes is not None and (not _number(minutes) or minutes < 0):
        raise ValueError("human review time must be null or nonnegative")
    claims = _index(run.get("claims", []), "target_id")
    counts = Counter({k: 0 for k in ("correct_determined", "wrong_determined", "reasonable_unresolved",
                     "deferred_decidable", "unsubstantiated_unresolved", "omitted_objects",
                     "omitted_relations", "omitted_judgments", "extra_outputs")})
    details, quantity = [], []
    sources = {s["source_id"] for s in data["sources"]}
    for tid, target in targets.items():
        ref, actual = refs[tid], claims.get(tid)
        category = None
        if actual is None:
            category = {"OBJECT": "omitted_objects", "RELATION": "omitted_relations", "JUDGMENT": "omitted_judgments"}[target["kind"]]
        else:
            evidence = actual.get("evidence_refs", [])
            valid_evidence = isinstance(evidence, list) and bool(evidence) and all(
                isinstance(e, str) and ":" in e and e.split(":", 1)[0] in sources for e in evidence)
            grounded = valid_evidence and bool(set(evidence) & set(ref["evidence_refs"]))
            status = actual.get("status")
            if status not in {"DETERMINED", "UNRESOLVED"}:
                raise ValueError(f"invalid claim status: {tid}")
            if status == "UNRESOLVED":
                if actual.get("conclusion") is not None or actual.get("quantity") is not None:
                    raise ValueError(f"unresolved conclusion/quantity must be null: {tid}")
                if not grounded or not isinstance(actual.get("reason"), str) or not actual["reason"].strip():
                    category = "unsubstantiated_unresolved"
                elif ref["decision_status"] == "DECIDABLE":
                    category = "deferred_decidable"
                else:
                    category = "reasonable_unresolved"
            else:
                if actual.get("conclusion") is None:
                    raise ValueError(f"determined conclusion missing: {tid}")
                accepted = any(json.dumps(actual["conclusion"], sort_keys=True, ensure_ascii=False) ==
                               json.dumps(v, sort_keys=True, ensure_ascii=False)
                               for v in ref.get("acceptable_conclusions", []))
                category = "correct_determined" if ref["decision_status"] == "DECIDABLE" and accepted and grounded else "wrong_determined"
        counts[category] += 1
        details.append({"target_id": tid, "group_id": target["group_id"], "category": category})
        expected, actual_q = ref.get("quantity"), actual.get("quantity") if actual else None
        if actual_q is not None and (not isinstance(actual_q, dict) or not _number(actual_q.get("value")) or
                                     not actual_q.get("unit") or not actual_q.get("basis")):
            raise ValueError(f"invalid run quantity: {tid}")
        q_status, error = "NOT_REFERENCED", None
        if expected:
            if actual_q is None:
                q_status = "MISSING_OR_UNRESOLVED"
            elif not isinstance(actual_q, dict) or not _number(actual_q.get("value")):
                raise ValueError(f"invalid run quantity: {tid}")
            elif (actual_q.get("unit"), actual_q.get("basis")) != (expected["unit"], expected["basis"]):
                q_status = "INCOMPARABLE"
            else:
                error = abs(actual_q["value"] - expected["value"])
                q_status = "WITHIN_TOLERANCE" if error <= expected["tolerance"] else "EXCEEDS_TOLERANCE"
        elif actual_q is not None:
            q_status = "UNSUPPORTED_QUANTITY"
        quantity.append({"target_id": tid, "status": q_status, "absolute_error": error,
                         "unit": expected.get("unit") if expected else None,
                         "basis": expected.get("basis") if expected else None})
    extras = sorted(set(claims) - set(targets))
    counts["extra_outputs"] = len(extras)
    reference_regions = _index(reference.get("coverage_regions", []), "region_id")
    run_regions = _index(run.get("coverage_regions", []), "region_id")
    coverage = []
    for rid, ref in reference_regions.items():
        actual = run_regions.get(rid, {})
        row = {"region_id": rid, "scanned_all_scope": actual.get("scanned_all_scope") is True}
        for expected_key, actual_key, label in (("expected_objects", "object_ids", "objects"),
                                               ("expected_relations", "relation_ids", "relations")):
            expected = ref.get(expected_key)
            provided = actual.get(actual_key, [])
            if not isinstance(provided, list) or len(provided) != len(set(provided)):
                raise ValueError("duplicate/invalid coverage outputs")
            row[label] = ({"reference_total": len(expected), "reported_total": len(provided),
                           "omitted": sorted(set(expected) - set(provided)),
                           "extra": sorted(set(provided) - set(expected))} if expected is not None else
                          {"reference_total": None, "reported_total": len(provided), "omitted": None, "extra": None})
        coverage.append(row)
    extra_regions = sorted(set(run_regions) - set(reference_regions))
    coverage_totals = {}
    for label in ("objects", "relations"):
        known = all(row[label]["reference_total"] is not None for row in coverage)
        coverage_totals[label] = {
            "reference_total": sum(row[label]["reference_total"] for row in coverage) if known else None,
            "reported_total": sum(row[label]["reported_total"] for row in coverage),
            "omitted_count": sum(len(row[label]["omitted"]) for row in coverage) if known else None,
            "extra_count": sum(len(row[label]["extra"]) for row in coverage) if known else None}
    total = len(targets)
    accepted_count = counts["correct_determined"] + counts["reasonable_unresolved"]
    failure = any(counts[k] for k in counts if k not in {"correct_determined", "reasonable_unresolved"})
    failure |= any(q["status"] not in {"WITHIN_TOLERANCE", "NOT_REFERENCED"} for q in quantity)
    failure |= bool(extra_regions) or any(not r["scanned_all_scope"] or any(
        r[k]["reference_total"] is None or r[k]["omitted"] or r[k]["extra"] for k in ("objects", "relations")) for r in coverage)
    return {"schema_version": VERSION, "status": "FAIL" if failure else "PASS",
            "authority": reference["authority"], "formal_engineering_acceptance": False,
            "run_id": run["run_id"], "version_label": run["version_label"],
            "denominators": {"target_universe": total, "result_target_count": len(claims),
                             "decidable": sum(r["decision_status"] == "DECIDABLE" for r in refs.values()),
                             "groups": len(data["groups"]), "source_contexts": len({s["sha256"] for s in data["sources"]})},
            "counts": dict(counts), "accepted_over_full_universe": accepted_count / total,
            "correct_determined_over_full_universe": counts["correct_determined"] / total,
            "details": details, "extra_target_ids": extras, "quantities": quantity,
            "coverage_regions": coverage, "coverage_totals": coverage_totals, "extra_region_ids": extra_regions,
            "human_review_minutes": minutes}


def compare(data: dict, reference: dict, baseline: list[dict], candidate: list[dict], input_hash: str) -> dict:
    if not baseline or not candidate:
        raise ValueError("both versions require at least one run")
    def indexed(runs):
        result = {}
        for run in runs:
            key = (run.get("pair_id"), run.get("replicate"))
            if key in result:
                raise ValueError("duplicate paired replicate")
            result[key] = run
        return result
    a, b = indexed(baseline), indexed(candidate)
    if set(a) != set(b):
        raise ValueError("unpaired runs")
    versions_a = {r.get("version_label") for r in baseline}
    versions_b = {r.get("version_label") for r in candidate}
    if len(versions_a) != 1 or len(versions_b) != 1 or versions_a == versions_b:
        raise ValueError("baseline/candidate version identities must be stable and distinct")
    run_ids = [r.get("run_id") for r in baseline + candidate]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("duplicate run_id across versions/repeats")
    configurations = {json.dumps({k: r.get(k) for k in ("input_sha256", "model_config", "budget")}, sort_keys=True)
                      for r in baseline + candidate}
    if len(configurations) != 1:
        raise ValueError("pair or repeat configuration mismatch")
    for key in a:
        for field in ("input_sha256", "model_config", "budget"):
            if a[key].get(field) != b[key].get(field):
                raise ValueError(f"pair configuration mismatch: {field}")
    reports = {"baseline": [score(data, reference, a[k], input_hash) for k in sorted(a)],
               "candidate": [score(data, reference, b[k], input_hash) for k in sorted(b)]}
    summary = {}
    for label, rows in reports.items():
        rates = [r["accepted_over_full_universe"] for r in rows]
        times = [r["human_review_minutes"] for r in rows if r["human_review_minutes"] is not None]
        summary[label] = {"run_count": len(rows), "ambiguity_group_count": len(data["groups"]),
                          "source_context_count": len({s["sha256"] for s in data["sources"]}), "accepted_rate_mean": statistics.mean(rates),
                          "accepted_rate_range": max(rates) - min(rates) if len(rates) > 1 else None,
                          "accepted_rate_population_stddev": statistics.pstdev(rates) if len(rates) > 1 else None,
                          "human_review_minutes_mean": statistics.mean(times) if times else None,
                          "human_review_measured_runs": len(times),
                          "repeat_observation": "AVAILABLE" if len(rows) >= 3 else "FEWER_THAN_THREE_RUNS"}
        summary[label]["count_ranges"] = {key: {"min": min(r["counts"][key] for r in rows),
                                                    "max": max(r["counts"][key] for r in rows)} for key in rows[0]["counts"]}
        source_runs = baseline if label == "baseline" else candidate
        values = {}
        for run in source_runs:
            for claim in run.get("claims", []):
                q = claim.get("quantity")
                if isinstance(q, dict) and _number(q.get("value")):
                    key = (claim["target_id"], q.get("unit"), q.get("basis"))
                    values.setdefault(key, []).append(q["value"])
        summary[label]["quantity_repeat_spread"] = [
            {"target_id": k[0], "unit": k[1], "basis": k[2], "measured_runs": len(v),
             "range": max(v) - min(v) if len(v) > 1 else None,
             "population_stddev": statistics.pstdev(v) if len(v) > 1 else None}
            for k, v in sorted(values.items())]
    # A repetition never increases the source or ambiguity-group denominators.
    return {"schema_version": VERSION, "status": "COMPARISON_COMPLETE", "formal_engineering_acceptance": False,
            "summary": summary, "runs": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "score", "compare"))
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--split-peer", type=Path, nargs="+", help="Other public manifests for combined DEBUG/SEALED leakage audit")
    parser.add_argument("--run", type=Path)
    parser.add_argument("--baseline", type=Path, nargs="+")
    parser.add_argument("--candidate", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        audit = audit_bundle(args.input_manifest, args.reference, args.runtime_root)
        if audit["status"] == "PASS" and args.split_peer:
            peers = [audit_bundle(p, runtime_root=args.runtime_root) for p in args.split_peer]
            if any(p["status"] != "PASS" for p in peers):
                raise ValueError("split peer asset audit failed")
            validate_split_manifests([read(args.input_manifest)] + [read(p) for p in args.split_peer])
            audit["split_peer_sha256"] = [digest(p) for p in args.split_peer]
        if audit["status"] == "FAIL" or args.mode == "audit":
            report = audit
        elif not args.reference:
            raise ValueError("reference required for scoring")
        else:
            data, ref, h = read(args.input_manifest), read(args.reference), digest(args.input_manifest)
            if args.mode == "score":
                if not args.run:
                    raise ValueError("--run required")
                report = score(data, ref, read(args.run), h)
            else:
                report = compare(data, ref, [read(p) for p in args.baseline or []],
                                 [read(p) for p in args.candidate or []], h)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report = {"schema_version": VERSION, "status": "INVALID", "errors": [str(exc)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] in {"PASS", "COMPARISON_COMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
