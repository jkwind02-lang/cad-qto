#!/usr/bin/env python3
"""Deterministic acceptance checks for a phase-one benchmark package."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def csv_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    benchmark = args.benchmark
    difference = read_json(benchmark / "对象全集差异.json")
    closure = read_json(benchmark / "块定义闭包.json")
    status = read_json(benchmark / "阶段一状态.json")
    mutations = read_json(benchmark / "变异测试结果.json")
    runtime = read_json(benchmark / "运行时文字预算.json")
    manifest = read_json(benchmark / "阶段一基准清单.json")
    validator = Draft202012Validator(read_json(args.schemas / "universe-record.schema.json"))

    checks = []

    def check(name: str, passed: bool, details=None):
        checks.append({"name": name, "passed": bool(passed), "details": details})

    up = list(rows(benchmark / "UP处理对象全集.jsonl"))
    check("UP_COUNT", len(up) == int(difference["up"]["count"]), len(up))
    up_errors = [(index, list(validator.iter_errors(row))[0].message) for index, row in enumerate(up, 1) if not validator.is_valid(row)]
    check("UP_SCHEMA_ALL", not up_errors, up_errors[:10])

    u0_count = 0
    u0_sample_errors = []
    for index, row in enumerate(rows(benchmark / "U0原始对象全集.jsonl.gz"), 1):
        u0_count = index
        if index <= 100 or index % 1000 == 0:
            errors = list(validator.iter_errors(row))
            if errors:
                u0_sample_errors.append((index, errors[0].message))
    check("U0_COUNT", u0_count == int(difference["u0"]["count"]), u0_count)
    check("U0_SCHEMA_STRATIFIED_SAMPLE", not u0_sample_errors, u0_sample_errors[:10])

    source_closure = closure.get("source_closure_metadata") or {}
    check(
        "BLOCK_CLOSURE_PASS",
        source_closure.get("status") == "PASS"
        and source_closure.get("block_set_match") is True
        and int(source_closure.get("skipped_record_count") or 0) == 0
        and int(source_closure.get("origin_mismatch_count") or 0) == 0,
        source_closure,
    )

    t1 = csv_rows(benchmark / "T1对象标签.csv")
    pending = all(row.get("label_status") == "PENDING_HUMAN" for row in t1)
    forbidden_filled = [
        row.get("object_id") for row in t1
        if any((row.get(field) or "").strip() for field in ("human_disposition", "human_object_class", "human_professional_owner", "human_system"))
    ]
    check("T1_PILOT_20_PENDING_HUMAN", len(t1) == 20 and pending and not forbidden_filled, forbidden_filled)
    check("T1_5_EMPTY", len(csv_rows(benchmark / "T1.5规则绑定.csv")) == 0)
    check("T2_EMPTY", len(csv_rows(benchmark / "T2几何标准答案.csv")) == 0)
    check("T3_EMPTY", len(csv_rows(benchmark / "T3规格抽样.csv")) == 0)
    check("MUTATIONS_8_OF_8", mutations.get("passed") is True and mutations.get("detected_count") == 8 and mutations.get("deterministic_count") == 8)
    check("RUNTIME_TEXT_BUDGET", runtime.get("within_total_budget") is True and runtime.get("delta_characters") == 0 and runtime.get("current_characters") == 182726)
    check("STATUS_TRUTHFUL", status.get("status") == "PILOT_READY_PENDING_HUMAN_T1" and status.get("global_skill_modified") is False and status.get("capability_or_maturity_upgraded") is False)
    artifacts = manifest.get("artifacts") or {}
    check("MANIFEST_INCLUDES_MUTATION", any(str(key).endswith("变异测试结果.json") for key in artifacts))
    check("MANIFEST_INCLUDES_RUNTIME_AUDIT", any(str(key).endswith("运行时文字预算.json") for key in artifacts))

    passed = all(item["passed"] for item in checks)
    payload = {
        "schema_version": "qto-phase1-artifact-validation/1.0",
        "status": "PASS" if passed else "FAIL",
        "passed": sum(item["passed"] for item in checks),
        "failed": sum(not item["passed"] for item in checks),
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "passed": payload["passed"], "failed": payload["failed"]}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
