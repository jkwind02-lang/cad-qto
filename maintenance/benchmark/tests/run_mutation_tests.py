#!/usr/bin/env python3
"""Synthetic regression protection for the pure-script scorer."""

from __future__ import annotations

import copy
import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from score_benchmark import score  # noqa: E402


IDENTITY = {
    "standard_answer_version": "SYNTHETIC-GOLD-1",
    "standard_answer_sha256": "gold-hash-1",
    "u0_version": "U0-1",
    "u0_sha256": "u0-hash-1",
    "up_version": "UP-1",
    "up_sha256": "up-hash-1",
    "policy_version": "P-1",
    "policy_sha256": "p-hash-1",
    "rule_version": "RULES-1",
    "ledger_version": "LEDGER-1",
    "result_schema_version": "qto-benchmark-result/1.0",
}


def gold_row(
    object_id: str,
    disposition: str,
    object_class: str = "",
    owner: str = "",
    system: str = "",
    space: str = "8F",
    requires_handoff: str = "false",
) -> dict[str, str]:
    return {
        "object_id": object_id,
        "sample_set": "PROBABILITY",
        "human_disposition": disposition,
        "human_object_class": object_class,
        "human_professional_owner": owner,
        "human_system": system,
        "human_space": space,
        "requires_handoff": requires_handoff,
        "adjudication_basis": "synthetic frozen fixture",
        "annotator": "fixture",
        "label_status": "FROZEN",
    }


T1 = [
    gold_row("O1", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    gold_row("O2", "EXCLUDE", "AUXILIARY_SYMBOL", "qto-pipe-networks", "WS"),
    gold_row("O3", "ADOPT", "BASIN", "qto-components-points", "WS"),
    gold_row("O4", "UNRESOLVED", "UNKNOWN_INSTANCE", "", ""),
    gold_row("O5", "EXCLUDE", "DEVICE_INSTANCE", "qto-components-points", "WS", requires_handoff="true"),
    gold_row("O6", "ADOPT", "VALVE", "qto-pipe-networks", "WS"),
    gold_row("O7", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    gold_row("O8", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    gold_row("O9", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    gold_row("O10", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
]

RULES = [
    {
        "object_or_group_id": "O1",
        "human_rule_id": "RULE-DN-SEED",
        "human_rule_version": "1",
        "human_node": "N1",
        "label_status": "FROZEN",
    }
]

GEOMETRY = [
    {"object_id": "O1", "quantity_value": "2.0", "quantity_unit": "m", "gold_status": "FROZEN"},
    {"object_id": "O3", "quantity_value": "1", "quantity_unit": "个", "gold_status": "FROZEN"},
]

SPECS = [
    {"object_id": "O1", "human_value": "DN25", "label_status": "FROZEN"}
]


def result_row(
    object_id: str,
    disposition: str,
    object_class: str,
    owner: str,
    system: str,
    *,
    quantity_value=None,
    quantity_unit=None,
    quantity_level="NOT_APPLICABLE",
    handoff_status="NOT_REQUIRED",
) -> dict:
    return {
        "object_id": object_id,
        "physical_object_id": "PHY-" + object_id,
        "disposition": disposition,
        "object_class": object_class,
        "professional_owner": owner,
        "system": system,
        "space": "8F",
        "quantity_value": quantity_value,
        "quantity_unit": quantity_unit,
        "quantity_level": quantity_level,
        "handoff_status": handoff_status,
        "unique_output_id": "OUT-" + object_id,
        "rule_id": None,
        "rule_version": None,
        "rule_node": None,
        "specification": None,
    }


BASE = [
    result_row("O1", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS", quantity_value=2.0, quantity_unit="m", quantity_level="CONFIRMED"),
    result_row("O2", "EXCLUDE", "AUXILIARY_SYMBOL", "qto-pipe-networks", "WS"),
    result_row("O3", "ADOPT", "BASIN", "qto-components-points", "WS", quantity_value=1, quantity_unit="个", quantity_level="CONFIRMED"),
    result_row("O4", "UNRESOLVED", "UNKNOWN_INSTANCE", "", "", quantity_level="UNRESOLVED"),
    result_row("O5", "EXCLUDE", "DEVICE_INSTANCE", "qto-components-points", "WS", handoff_status="DELIVERED"),
    result_row("O6", "ADOPT", "VALVE", "qto-pipe-networks", "WS", quantity_value=1, quantity_unit="个", quantity_level="CONFIRMED"),
    result_row("O7", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    result_row("O8", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    result_row("O9", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
    result_row("O10", "ADOPT", "PIPE_SEGMENT", "qto-pipe-networks", "WS"),
]
BASE[0].update({"rule_id": "RULE-DN-SEED", "rule_version": "1", "rule_node": "N1", "specification": "DN25"})


def run(rows: list[dict]) -> dict:
    benchmark = {**IDENTITY, "numeric_tolerance_by_unit": {"m": 1e-9, "个": 0, "default": 0}}
    return score(benchmark, dict(IDENTITY), T1, RULES, GEOMETRY, SPECS, rows, 8)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "tests" / "变异测试结果.json")
    args = parser.parse_args()
    baseline = run(copy.deepcopy(BASE))
    if baseline["status"] != "PASS":
        raise SystemExit("baseline must pass: " + json.dumps(baseline, ensure_ascii=False))

    cases = []
    rows = copy.deepcopy(BASE)
    rows = [row for row in rows if row["object_id"] != "O1"]
    cases.append(("M01", "SILENT_GOLD_OBJECT_MISSING", rows))

    rows = copy.deepcopy(BASE)
    row = next(item for item in rows if item["object_id"] == "O2")
    row.update({"disposition": "ADOPT", "object_class": "PIPE_SEGMENT", "quantity_value": 1, "quantity_unit": "m", "quantity_level": "CONFIRMED"})
    cases.append(("M02", "DISPOSITION_MISMATCH", rows))

    rows = copy.deepcopy(BASE)
    next(item for item in rows if item["object_id"] == "O3")["professional_owner"] = "qto-pipe-networks"
    cases.append(("M03", "CLASSIFICATION_MISMATCH", rows))

    rows = copy.deepcopy(BASE)
    next(item for item in rows if item["object_id"] == "O1")["rule_version"] = "2"
    cases.append(("M04", "RULE_BINDING_MISMATCH", rows))

    rows = copy.deepcopy(BASE)
    next(item for item in rows if item["object_id"] == "O1")["specification"] = "DN32"
    cases.append(("M05", "SPECIFICATION_MISMATCH", rows))

    rows = copy.deepcopy(BASE)
    next(item for item in rows if item["object_id"] == "O5")["handoff_status"] = "NOT_APPLICABLE"
    cases.append(("M06", "EXCLUSION_HANDOFF_INCOMPLETE", rows))

    rows = copy.deepcopy(BASE)
    next(item for item in rows if item["object_id"] == "O3")["physical_object_id"] = "PHY-DUP"
    next(item for item in rows if item["object_id"] == "O6")["physical_object_id"] = "PHY-DUP"
    cases.append(("M07", "DUPLICATE_UNIQUE_OUTPUT", rows))

    rows = copy.deepcopy(BASE)
    next(item for item in rows if item["object_id"] == "O4")["quantity_value"] = 0
    cases.append(("M08", "UNRESOLVED_WRITTEN_AS_ZERO", rows))

    results = []
    for mutation_id, expected, rows in cases:
        first = run(rows)
        second = run(copy.deepcopy(rows))
        deterministic = json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)
        detected = expected in first.get("hard_failure_codes", [])
        results.append(
            {
                "mutation_id": mutation_id,
                "expected_failure": expected,
                "detected": detected,
                "deterministic_repeat": deterministic,
                "actual_failure_codes": first.get("hard_failure_codes", []),
            }
        )

    summary = {
        "schema_version": "qto-mutation-test-report/1.0",
        "baseline_status": baseline["status"],
        "mutation_count": len(results),
        "detected_count": sum(1 for item in results if item["detected"]),
        "deterministic_count": sum(1 for item in results if item["deterministic_repeat"]),
        "passed": all(item["detected"] and item["deterministic_repeat"] for item in results),
        "results": results,
    }
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
