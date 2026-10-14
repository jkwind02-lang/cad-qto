#!/usr/bin/env python3
"""Pure-script scorer for frozen QTO benchmark labels and results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCORER_VERSION = "qto-pure-scorer/1.0.0"
REPORT_VERSION = "qto-score-report/1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normal(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def truthy(value: Any) -> bool:
    return normal(value).lower() in {"1", "true", "yes", "y", "是"}


def as_number(value: Any) -> float | None:
    if value is None or normal(value) == "":
        return None
    return float(value)


def add_failure(failures: list[dict[str, Any]], code: str, **details: Any) -> None:
    failures.append({"code": code, **details})


def check_identity(
    benchmark: dict[str, Any], result_manifest: dict[str, Any], failures: list[dict[str, Any]]
) -> None:
    required = [
        "standard_answer_version",
        "standard_answer_sha256",
        "u0_version",
        "u0_sha256",
        "up_version",
        "up_sha256",
        "policy_version",
        "policy_sha256",
        "rule_version",
        "ledger_version",
        "result_schema_version",
    ]
    for key in required:
        if normal(benchmark.get(key)) == "" or normal(result_manifest.get(key)) == "":
            add_failure(failures, "VERSION_INFO_MISSING", field=key)
            continue
        if normal(benchmark.get(key)) != normal(result_manifest.get(key)):
            code = "UNIVERSE_DRIFT" if key.startswith(("u0_", "up_", "policy_")) else "VERSION_IDENTITY_MISMATCH"
            add_failure(
                failures,
                code,
                field=key,
                expected=benchmark.get(key),
                actual=result_manifest.get(key),
            )


def class_metrics(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    true_positive = len(gold & predicted)
    false_positive = len(predicted - gold)
    false_negative = len(gold - predicted)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
    }


def score(
    benchmark_manifest: dict[str, Any],
    result_manifest: dict[str, Any],
    t1_rows: list[dict[str, str]],
    rule_rows: list[dict[str, str]],
    geometry_rows: list[dict[str, str]],
    spec_rows: list[dict[str, str]],
    result_rows: list[dict[str, Any]],
    small_class_threshold: int,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    differences: dict[str, Any] = defaultdict(list)
    check_identity(benchmark_manifest, result_manifest, failures)

    gold_rows = [row for row in t1_rows if normal(row.get("label_status")).upper() == "FROZEN"]
    if not gold_rows:
        return {
            "schema_version": REPORT_VERSION,
            "scorer_version": SCORER_VERSION,
            "status": "NOT_SCORED",
            "inputs": {},
            "hard_failures": [{"code": "NO_FROZEN_T1_LABELS"}],
            "metrics": {},
            "differences": {},
        }
    gold_by_id = {normal(row.get("object_id")): row for row in gold_rows}
    if "" in gold_by_id or len(gold_by_id) != len(gold_rows):
        add_failure(failures, "INVALID_OR_DUPLICATE_GOLD_OBJECT_ID")

    result_by_id: dict[str, dict[str, Any]] = {}
    for row in result_rows:
        object_id = normal(row.get("object_id"))
        if not object_id or object_id in result_by_id:
            add_failure(failures, "INVALID_OR_DUPLICATE_RESULT_OBJECT_ID", object_id=object_id)
        else:
            result_by_id[object_id] = row

    gold_ids, result_ids = set(gold_by_id), set(result_by_id)
    missing = sorted(gold_ids - result_ids)
    extra = sorted(result_ids - gold_ids)
    for object_id in missing:
        add_failure(failures, "SILENT_GOLD_OBJECT_MISSING", object_id=object_id)
    if extra:
        differences["extra_result_objects"] = extra

    adopted_gold: set[str] = set()
    adopted_result: set[str] = set()
    classification_matches = 0
    classification_total = 0
    confusion = Counter()
    per_class: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"gold": set(), "predicted": set()})

    for object_id in sorted(gold_ids & result_ids):
        gold, result = gold_by_id[object_id], result_by_id[object_id]
        expected_disposition = normal(gold.get("human_disposition")).upper()
        actual_disposition = normal(result.get("disposition")).upper()
        confusion[(expected_disposition, actual_disposition)] += 1
        if expected_disposition == "ADOPT":
            adopted_gold.add(object_id)
        if actual_disposition == "ADOPT":
            adopted_result.add(object_id)
        if expected_disposition != actual_disposition:
            add_failure(
                failures,
                "DISPOSITION_MISMATCH",
                object_id=object_id,
                expected=expected_disposition,
                actual=actual_disposition,
            )
            differences["disposition_mismatches"].append(object_id)

        expected_class = normal(gold.get("human_object_class"))
        actual_class = normal(result.get("object_class"))
        if expected_class:
            per_class[expected_class]["gold"].add(object_id)
        if actual_class and actual_disposition == "ADOPT":
            per_class[actual_class]["predicted"].add(object_id)

        fields = [
            ("object_class", "human_object_class"),
            ("professional_owner", "human_professional_owner"),
            ("system", "human_system"),
            ("space", "human_space"),
        ]
        if expected_disposition == "ADOPT":
            classification_total += 1
            mismatches = []
            for actual_key, expected_key in fields:
                expected = normal(gold.get(expected_key))
                actual = normal(result.get(actual_key))
                if expected and expected != actual:
                    mismatches.append({"field": actual_key, "expected": expected, "actual": actual})
            if mismatches:
                add_failure(failures, "CLASSIFICATION_MISMATCH", object_id=object_id, mismatches=mismatches)
                differences["classification_mismatches"].append({"object_id": object_id, "mismatches": mismatches})
            else:
                classification_matches += 1

        if expected_disposition == "UNRESOLVED":
            quantity = as_number(result.get("quantity_value"))
            if quantity == 0.0:
                add_failure(failures, "UNRESOLVED_WRITTEN_AS_ZERO", object_id=object_id)

        if expected_disposition == "EXCLUDE" and truthy(gold.get("requires_handoff")):
            if normal(result.get("handoff_status")).upper() != "DELIVERED":
                add_failure(
                    failures,
                    "EXCLUSION_HANDOFF_INCOMPLETE",
                    object_id=object_id,
                    actual=result.get("handoff_status"),
                )

    output_to_objects: dict[str, set[str]] = defaultdict(set)
    for object_id, row in result_by_id.items():
        unique_output_id = normal(row.get("unique_output_id"))
        physical_id = normal(row.get("physical_object_id")) or object_id
        if unique_output_id and normal(row.get("disposition")).upper() == "ADOPT":
            output_to_objects[physical_id].add(unique_output_id)
    for physical_id, output_ids in sorted(output_to_objects.items()):
        if len(output_ids) > 1:
            add_failure(
                failures,
                "DUPLICATE_UNIQUE_OUTPUT",
                physical_object_id=physical_id,
                unique_output_ids=sorted(output_ids),
            )

    frozen_rules = [row for row in rule_rows if normal(row.get("label_status")).upper() == "FROZEN"]
    for binding in frozen_rules:
        object_id = normal(binding.get("object_or_group_id"))
        result = result_by_id.get(object_id)
        if result is None:
            continue
        expected = (normal(binding.get("human_rule_id")), normal(binding.get("human_rule_version")), normal(binding.get("human_node")))
        actual = (normal(result.get("rule_id")), normal(result.get("rule_version")), normal(result.get("rule_node")))
        if expected != actual:
            add_failure(failures, "RULE_BINDING_MISMATCH", object_id=object_id, expected=expected, actual=actual)

    frozen_specs = [row for row in spec_rows if normal(row.get("label_status")).upper() == "FROZEN"]
    for spec in frozen_specs:
        object_id = normal(spec.get("object_id"))
        result = result_by_id.get(object_id)
        if result is None:
            continue
        expected = normal(spec.get("human_value"))
        actual = normal(result.get("specification"))
        if expected != actual:
            add_failure(failures, "SPECIFICATION_MISMATCH", object_id=object_id, expected=expected, actual=actual)

    tolerances = benchmark_manifest.get("numeric_tolerance_by_unit") or {}
    quantity_errors: list[dict[str, Any]] = []
    frozen_geometry = [row for row in geometry_rows if normal(row.get("gold_status")).upper() == "FROZEN"]
    for quantity in frozen_geometry:
        object_id = normal(quantity.get("object_id"))
        result = result_by_id.get(object_id)
        if result is None:
            continue
        expected_unit = normal(quantity.get("quantity_unit"))
        actual_unit = normal(result.get("quantity_unit"))
        expected_value = as_number(quantity.get("quantity_value"))
        actual_value = as_number(result.get("quantity_value"))
        tolerance = float(tolerances.get(expected_unit, tolerances.get("default", 0.0)))
        error = None if expected_value is None or actual_value is None else abs(expected_value - actual_value)
        if expected_unit != actual_unit or error is None or error > tolerance:
            detail = {
                "object_id": object_id,
                "expected_value": expected_value,
                "actual_value": actual_value,
                "expected_unit": expected_unit,
                "actual_unit": actual_unit,
                "absolute_error": error,
                "tolerance": tolerance,
            }
            quantity_errors.append(detail)
            add_failure(failures, "QUANTITY_ERROR_EXCEEDS_TOLERANCE", **detail)

    class_report: dict[str, Any] = {}
    small_classes: list[dict[str, Any]] = []
    aggregate_small_gold: set[str] = set()
    aggregate_small_predicted: set[str] = set()
    for class_name in sorted(per_class):
        values = per_class[class_name]
        if len(values["gold"]) < small_class_threshold:
            aggregate_small_gold.update(values["gold"])
            aggregate_small_predicted.update(values["predicted"])
            small_classes.append(
                {
                    "class": class_name,
                    "gold_count": len(values["gold"]),
                    "predicted_count": len(values["predicted"]),
                    "missing_object_ids": sorted(values["gold"] - values["predicted"]),
                    "extra_object_ids": sorted(values["predicted"] - values["gold"]),
                }
            )
        else:
            class_report[class_name] = class_metrics(values["gold"], values["predicted"])
    if small_classes:
        class_report["SMALL_CLASSES_COMBINED"] = {
            **class_metrics(aggregate_small_gold, aggregate_small_predicted),
            "individual_classes": small_classes,
            "rule": f"classes with fewer than {small_class_threshold} gold instances report absolute differences",
        }

    hard_failure_codes = sorted({failure["code"] for failure in failures})
    result = {
        "schema_version": REPORT_VERSION,
        "scorer_version": SCORER_VERSION,
        "status": "FAIL" if failures else "PASS",
        "inputs": {
            "standard_answer_version": benchmark_manifest.get("standard_answer_version"),
            "standard_answer_sha256": benchmark_manifest.get("standard_answer_sha256"),
            "result_schema_version": result_manifest.get("result_schema_version"),
            "u0_version": benchmark_manifest.get("u0_version"),
            "up_version": benchmark_manifest.get("up_version"),
            "policy_version": benchmark_manifest.get("policy_version"),
            "rule_version": benchmark_manifest.get("rule_version"),
            "ledger_version": benchmark_manifest.get("ledger_version"),
        },
        "hard_failure_codes": hard_failure_codes,
        "hard_failures": sorted(failures, key=lambda item: (item["code"], normal(item.get("object_id")), json.dumps(item, ensure_ascii=False, sort_keys=True))),
        "metrics": {
            "adopted_object_set": class_metrics(adopted_gold, adopted_result),
            "classification_accuracy": classification_matches / classification_total if classification_total else 1.0,
            "classification_total": classification_total,
            "disposition_confusion": {
                f"{expected}->{actual}": count for (expected, actual), count in sorted(confusion.items())
            },
            "per_class": class_report,
            "quantity_error_count": len(quantity_errors),
            "silent_gold_object_missing_count": len(missing),
            "unresolved_written_as_zero_count": sum(1 for item in failures if item["code"] == "UNRESOLVED_WRITTEN_AS_ZERO"),
        },
        "differences": dict(sorted(differences.items())),
    }
    return result


def markdown(report: dict[str, Any]) -> str:
    lines = ["# 基准评分报告", "", f"状态：**{report['status']}**", ""]
    if report["status"] == "NOT_SCORED":
        lines.append("未发现已冻结的 T1 标准答案，未执行评分。")
        return "\n".join(lines) + "\n"
    metrics = report["metrics"]
    adopted = metrics["adopted_object_set"]
    lines.extend(
        [
            f"- 硬失败：{len(report['hard_failures'])} 项；代码：{', '.join(report['hard_failure_codes']) or '无'}",
            f"- 采用对象精确率：{adopted['precision']:.4f}",
            f"- 采用对象召回率：{adopted['recall']:.4f}",
            f"- 分类准确率：{metrics['classification_accuracy']:.4f}",
            f"- 标准答案静默漏项：{metrics['silent_gold_object_missing_count']}",
            f"- 未决写成 0：{metrics['unresolved_written_as_zero_count']}",
            "",
        ]
    )
    if report["hard_failures"]:
        lines.extend(["## 硬失败明细", ""])
        for item in report["hard_failures"]:
            object_suffix = f"（{item['object_id']}）" if item.get("object_id") else ""
            lines.append(f"- `{item['code']}`{object_suffix}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-manifest", type=Path, required=True)
    parser.add_argument("--result-manifest", type=Path, required=True)
    parser.add_argument("--t1", type=Path, required=True)
    parser.add_argument("--t1-5", type=Path)
    parser.add_argument("--t2", type=Path)
    parser.add_argument("--t3", type=Path)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--small-class-threshold", type=int, default=8)
    args = parser.parse_args()

    report = score(
        load_json(args.benchmark_manifest),
        load_json(args.result_manifest),
        read_csv(args.t1),
        read_csv(args.t1_5),
        read_csv(args.t2),
        read_csv(args.t3),
        read_jsonl(args.result),
        args.small_class_threshold,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(markdown(report), encoding="utf-8")
    return 0 if report["status"] in {"PASS", "NOT_SCORED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
