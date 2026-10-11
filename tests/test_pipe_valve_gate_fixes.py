"""Evidence-to-summary checks for the independently reproduced Q12 mismatch.

The source drawing is synthetic. These checks protect declared certainty and
count reconciliation; they do not validate real drawing recognition.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from test_manual_routes import ROOT, base, line, run_engine
from qto_pipe_quantity_contract import validate_valve_summary


@pytest.fixture
def candidate_inference(tmp_path):
    pipe = line("P", (0, 0), (8, 0))
    pipe["dn_seeds"] = [{"dn": "DN32", "evidence_level": "confirmed", "source_id": "label:P"}]
    data = base([pipe])
    data["valves"] = [{
        "valve_id": "candidate-V", "system": "W", "space_id": "plan",
        "point": [4, 0, 0], "source_id": "drawing", "source_object_id": "handle-V",
        "valve_type": "isolation valve", "direct_dn": "DN32",
        "dn_evidence_level": "confirmed", "instance_level": "scenario",
        "assumption_id": "A-symbol-identity",
    }]
    inference = run_engine(tmp_path / "engine", data)
    assert inference["valve_summary"][0]["quantity_level"] == "方案暂估量"
    return inference


def run_gate(tmp_path, gate, inference):
    source = tmp_path / "inference.json"
    source.write_text(json.dumps(inference, ensure_ascii=False), encoding="utf-8")
    if gate == "validate_pipe_result.py":
        result = {
            "契约版本": "qto-result-minimum/1.0", "本次规则清单": [],
            "工程量结果": [{
                "成果项编号": "Q-P", "范围": {"系统": "W"},
                "结果": {"名称": "已绘中心线工作量", "数值": 8, "单位": "m"},
                "状态": "部分计算", "规则": [], "未决事项": ["U-V"],
                "专业检查": {"计量对象": "平面管段", "规格赋值方式": "直接标注DN32",
                           "完整性边界": "仅已绘中心线；候选阀门的实例存在性待核"},
            }],
            "整体未决事项": [{"事项编号": "U-V", "说明": "阀门实例仍是scenario"}],
        }
        target = tmp_path / "result.json"
        target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        arguments = ["--result", str(target)]
    else:
        level = inference["valve_summary"][0]["quantity_level"]
        target = tmp_path / "summary.md"
        target.write_text(
            "# 管道长度\n\n| 系统 | DN | 数量层级 | 长度 |\n"
            "| --- | --- | --- | --- |\n| W | DN32 | 确认量 | 8 m |\n\n"
            "# 阀门\n\n| 系统 | 阀门类型 | 块族 | 候选类型 | DN | 实例数 | 数量层级 | 实例状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            f"| W | isolation valve | 无 | isolation valve | DN32 | 1 | {level} | scenario |\n",
            encoding="utf-8",
        )
        arguments = ["--summary", str(target)]
    process = subprocess.run(
        [sys.executable, str(ROOT / "skills/qto-pipe-networks/scripts" / gate),
         "--inference", str(source), *arguments],
        capture_output=True, text=True, encoding="utf-8",
    )
    return process.returncode, json.loads(process.stdout)


@pytest.mark.parametrize("gate", ["validate_pipe_result.py", "validate_pipe_user_summary.py"])
def test_both_gates_reject_historical_q12_confirmed_count(tmp_path, candidate_inference, gate):
    # Recreate the old engine output: no overall per-valve level, and its DN
    # certainty incorrectly used as the count certainty. The displayed table
    # faithfully copies that output, so column-presence checks cannot catch it.
    candidate_inference["valves"][0].pop("quantity_level", None)
    candidate_inference["valve_summary"][0]["quantity_level"] = "确认量"
    code, report = run_gate(tmp_path, gate, candidate_inference)
    assert code == 2
    assert any("阀门汇总" in error and "逐实例证据" in error for error in report["errors"])


@pytest.mark.parametrize("gate", ["validate_pipe_result.py", "validate_pipe_user_summary.py"])
def test_both_gates_accept_explicitly_partial_candidate_counts(tmp_path, candidate_inference, gate):
    code, report = run_gate(tmp_path, gate, candidate_inference)
    assert code == 0, report
    assert report["errors"] == []


def test_consistent_legacy_output_without_new_quantity_field_is_accepted(candidate_inference):
    candidate_inference["valves"][0].pop("quantity_level")
    assert validate_valve_summary(candidate_inference) == []


@pytest.mark.parametrize("fault", ["count", "dn", "system", "missing", "duplicate_group", "promoted_detail"])
def test_group_or_count_tampering_is_rejected(candidate_inference, fault):
    row = candidate_inference["valve_summary"][0]
    if fault == "count":
        row["instance_count"] = 2
    elif fault == "dn":
        row["dn"] = "DN50"
    elif fault == "system":
        row["system"] = "other-system"
    elif fault == "missing":
        candidate_inference["valve_summary"] = []
    elif fault == "duplicate_group":
        candidate_inference["valve_summary"].append(copy.deepcopy(row))
    else:
        candidate_inference["valves"][0]["quantity_level"] = "确认量"
    assert validate_valve_summary(candidate_inference)


def test_balanced_count_does_not_hide_duplicate_source_instance(candidate_inference):
    duplicate = copy.deepcopy(candidate_inference["valves"][0])
    duplicate["valve_id"] = "different-logical-id"
    candidate_inference["valves"].append(duplicate)
    candidate_inference["valve_summary"][0]["instance_count"] = 2
    candidate_inference["metrics"]["valve_count"] = 2
    assert any("源实例重复" in error for error in validate_valve_summary(candidate_inference))


@pytest.mark.parametrize("gate", ["validate_pipe_result.py", "validate_pipe_user_summary.py"])
@pytest.mark.parametrize("variant", ["hash_case", "object_whitespace", "expanded_id_whitespace"])
def test_shared_contract_and_both_gates_reject_normalized_duplicate_identity(tmp_path, candidate_inference, gate, variant):
    first = candidate_inference["valves"][0]
    first.update(source_sha256="a" * 64)
    if variant == "expanded_id_whitespace":
        first.update(source_object_id="CADINST-I1/CAD-AB12", instance_path="I1")
    duplicate = copy.deepcopy(first)
    duplicate["valve_id"] = "same-valve-renamed"
    if variant == "hash_case":
        duplicate["source_sha256"] = "A" * 64
    else:
        duplicate["source_object_id"] = " " + first["source_object_id"] + " "
        if variant == "expanded_id_whitespace":
            duplicate["instance_path"] = "I2"
    candidate_inference["valves"].append(duplicate)
    candidate_inference["valve_summary"][0]["instance_count"] = 2
    candidate_inference["metrics"]["valve_count"] = 2
    # Counts and certainty still reconcile. Only canonical source identity
    # reveals that these two records refer to the same physical valve.
    assert any("源实例重复" in error for error in validate_valve_summary(candidate_inference))
    code, report = run_gate(tmp_path, gate, candidate_inference)
    assert code == 2
    assert any("源实例重复" in error for error in report["errors"])


def test_distinct_insertions_of_same_definition_remain_two_instances(candidate_inference):
    first = candidate_inference["valves"][0]
    first["instance_path"] = "INSERT-A"
    second = copy.deepcopy(first)
    second.update(valve_id="other-insertion", instance_path="INSERT-B")
    candidate_inference["valves"].append(second)
    candidate_inference["valve_summary"][0]["instance_count"] = 2
    candidate_inference["metrics"]["valve_count"] = 2
    assert validate_valve_summary(candidate_inference) == []


@pytest.mark.parametrize("field", ["scope_evidence_level", "type_evidence_level"])
def test_explicitly_unresolved_evidence_cannot_be_overridden_by_dn(candidate_inference, field):
    candidate_inference["valves"][0][field] = None
    assert validate_valve_summary(candidate_inference)
    candidate_inference["valves"][0]["quantity_level"] = "未决量"
    candidate_inference["valve_summary"][0]["quantity_level"] = "未决量"
    assert validate_valve_summary(candidate_inference) == []
