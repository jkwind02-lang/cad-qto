"""Source-bound rebar working calculations, not a claim of autonomous drawing recognition."""
import hashlib
import importlib.util
import json
import copy
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/qto-rebar/scripts"
SHARED = ROOT / "skills/_qto-shared-evidence/scripts"
spec = importlib.util.spec_from_file_location("rebar_working_calculator", SCRIPTS / "calculate_rebar_items.py")
calculator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calculator)
sys.path.insert(0, str(SHARED))
from qto_workflow_quantities import reconcile
from validate_minimum_result import validate_document


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def observed(path):
    return dict(path=str(path.resolve()), exists=True, status="current",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def case(tmp_path):
    # A small synthetic project document exercises the production source path;
    # facts and model interpretation remain separate.  No standard node value
    # or end-offset rule is implied by these deliberately specified numbers.
    source = write_json(tmp_path / "project/detail.json", dict(
        member="B1", layout=dict(first_centre_mm=100, last_centre_mm=1300, spacing_mm=200),
        centreline=dict(base_mm=2400, left_addition_mm=300, right_addition_mm=300),
        unit_mass_kg_m=0.395))
    manifest = dict(manifest_version="qto-rebar-manifest/1.0", case_id="REBAR-WORKING", test_mode=False,
        readonly_roots=[str(source.parent)],
        scope=dict(project="人工经验推理试验", building="A", area="B1 局部", drawing_type="局部配筋及节点",
                   drawing_version="fixture-v1", output_dimension="根数、长度、理论重量"),
        sources=[dict(source_id="SRC-PROJECT", path=str(source), sha256=observed(source)["sha256"], file_role="json",
            source_role="project_document", version_identity="fixture-v1", reason="明确本例的排布及中心线尺寸",
            neutral_facts="本例首末钢筋中心、间距及节点增量", unused_impact="不能求解本例的排布与长度",
            json_assertions={"member": "B1", "layout.first_centre_mm": 100, "layout.last_centre_mm": 1300,
                             "layout.spacing_mm": 200, "centreline.base_mm": 2400,
                             "centreline.left_addition_mm": 300, "centreline.right_addition_mm": 300})],
        evidence=[dict(evidence_id="EV-RB-1", source_id="SRC-PROJECT", evidence_class="object_mapping", fact_status="candidate",
            neutral_fact="B1 标注给定本组首末中心、间距和节点中心线尺寸", locator="layout / centreline",
            source_identity="S / fixture-v1", extraction_method="模型读图后明确关联的测试解释",
            quality_status="数字可读，专业归属为模型工作判断", limitation="该测试不证明未知原图自动识别正确")],
        rules=[dict(rule_id="R-RB-1", name="本组明确等距中心与显式中心线增量", decision="trial", maturity="candidate",
            source="本例局部配筋与节点", execution="按模型解释的实际端点排布计数，按节点显式增量计算长度",
            locator="S / layout / centreline")], unresolved=[],
        bar_groups=[dict(group_id="B1-LOWER-X", physical_object_id="B1-LOWER-X", member_id="B1",
            component_type="板", bar_category="底筋", position="底层 X 向同一排布组", grade="HRB400", diameter_mm=8,
            measurement_basis="本例明确中心线排布及节点", quantity_basis="physical_design", adoption_role="reference",
            status="complete", method="straight_with_explicit_additions", evidence_ids=["EV-RB-1"], rule_ids=["R-RB-1"], unresolved_ids=[],
            working_basis=dict(quantity_level="inferred", reason="本例标注及节点按构件 B1 与底层 X 向作用范围对应；首末位置为钢筋中心。",
                               evidence_ids=["EV-RB-1"], rule_ids=["R-RB-1"]),
            count_derivation=dict(method="uniform_centres", unit="m", first_centre=0.1, last_centre=1.3,
                                  spacing=0.2, basis="本例明确首末中心及间距，已从 mm 换为 m", evidence_ids=["EV-RB-1"]),
            base_length_each_m=2.4,
            additions_each_m=[dict(name="左端中心线段", length_m=0.3, basis="本例节点明确中心线增量"),
                              dict(name="右端中心线段", length_m=0.3, basis="本例节点明确中心线增量")],
            unit_mass_method="explicit_unit_mass", unit_mass_kg_m=0.395, unit_mass_basis="本例来源表列理论单位质量",
            outputs=dict(count="QTY-B1-N", length="QTY-B1-L", weight="QTY-B1-W"))])
    paths = dict(manifest=tmp_path / "manifest.json", result=tmp_path / "result.json",
                 evidence=tmp_path / "evidence.json", detail=tmp_path / "detail.csv")
    return dict(manifest=manifest, source=source, paths=paths)


def run_calculator(case):
    paths = case["paths"]
    write_json(paths["manifest"], case["manifest"])
    return subprocess.run([sys.executable, str(SCRIPTS / "calculate_rebar_items.py"), str(paths["manifest"]),
        "--result-out", str(paths["result"]), "--evidence-out", str(paths["evidence"]),
        "--detail-out", str(paths["detail"])], capture_output=True, text=True)


def successful(case):
    run = run_calculator(case)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(case["paths"]["result"].read_text())
    evidence = json.loads(case["paths"]["evidence"].read_text())
    assert validate_document(result) == []
    return result, evidence


def validate_bound(case):
    paths = case["paths"]
    return subprocess.run([sys.executable, str(SCRIPTS / "validate_rebar_result.py"),
        "--calculator", str(SCRIPTS / "calculate_rebar_items.py"),
        "--minimum-validator", str(SHARED / "validate_minimum_result.py"),
        *[str(paths[name]) for name in ("manifest", "result", "evidence", "detail")]], capture_output=True, text=True)


def test_sourced_candidate_working_calculation_passes_both_real_validators(case):
    result, evidence = successful(case)
    assert [item["结果"]["数值"] for item in result["工程量结果"]] == [7, 21.0, 8.295]
    assert result["本次规则清单"][0]["成熟状态"] == "候选"
    assert result["本次规则清单"][0]["本次判定"] == "试用"
    assert result["证据目录"][0]["事实状态"] == "candidate"
    assert result["推算协议版本"] == "qto-inference/1.0"
    for row in result["工程量结果"]:
        assert row["quantity_level"] == "inferred"
        assert row["采用角色"] == "reference" and row["结果用途"] == "reference_only"
        assert row["试运行结果"] is True
        assert row["成果分层"]["进入工作量"] is True
        assert row["成果分层"]["进入正式量"] is False
        assert row["review_status"] == "unreviewed" and row["formal_adoption"] is False
    calc = evidence["bar_group_calculations"][0]
    assert calc["count_derivation"]["interval_count"] == 6
    assert calc["developed_length_each_m"] == pytest.approx(3.0)
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("role", ["reference", "adopted"])
def test_old_verified_adopted_inputs_need_no_working_basis(case, role):
    doc = case["manifest"]
    doc["evidence"][0]["fact_status"] = "verified"
    doc["rules"][0].update(decision="adopted", maturity="trial")
    group = doc["bar_groups"][0]
    group.update(adoption_role=role, count=7)
    group.pop("working_basis")
    group.pop("count_derivation")
    result, evidence = successful(case)
    assert evidence["bar_group_calculations"][0]["weight_kg"] == pytest.approx(8.295)
    assert {row["quantity_level"] for row in result["工程量结果"]} == ({"inferred"} if role == "reference" else {"confirmed"})
    assert "假定与裁决清单" not in result
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_legacy_test_fixture_reference_remains_isolated_and_usable(case):
    doc = case["manifest"]
    doc["test_mode"] = True
    doc["sources"][0]["source_role"] = "test_fixture"
    doc["evidence"][0]["fact_status"] = "test_only"
    doc["rules"][0].update(decision="adopted", maturity="trial")
    doc["bar_groups"][0].pop("working_basis")
    result, _ = successful(case)
    assert all(row["quantity_level"] == "inferred" and row["formal_adoption"] is False for row in result["工程量结果"])
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("fact,decision,maturity", [("reference", "adopted", "formal"), ("candidate", "adopted", "trial"),
                                                    ("verified", "trial", "formal")])
def test_each_supported_working_interpretation_retains_actual_maturity(case, fact, decision, maturity):
    doc = case["manifest"]
    doc["evidence"][0]["fact_status"] = fact
    doc["rules"][0].update(decision=decision, maturity=maturity)
    result, _ = successful(case)
    assert result["证据目录"][0]["事实状态"] == fact
    assert result["本次规则清单"][0]["成熟状态"] == calculator.RULE_MATURITY_CN[maturity]


@pytest.mark.parametrize("change,message", [
    ("adopted_candidate", "非采用证据"), ("adopted_trial", "不可用规则"),
    ("candidate_rule_adopted", "不得 adopted"), ("disabled_rule", "不得 trial"),
    ("insufficient_rule", "不可用规则"), ("not_applicable_rule", "不可用规则"),
    ("missing_basis", "working_basis"), ("empty_basis", "reason"),
    ("wrong_basis_evidence", "引用本组证据"), ("wrong_basis_rule", "引用本组规则"),
    ("confirmed_basis", "不得升级"), ("production_test_evidence", "test_only"),
    ("source_changed", "来源哈希不匹配"),
])
def test_working_path_cannot_bypass_adoption_source_or_rule_gates(case, change, message):
    doc = case["manifest"]
    group = doc["bar_groups"][0]
    if change == "adopted_candidate":
        group["adoption_role"] = "adopted"
    elif change == "adopted_trial":
        group["adoption_role"] = "adopted"
        doc["evidence"][0]["fact_status"] = "verified"
    elif change == "candidate_rule_adopted":
        doc["rules"][0]["decision"] = "adopted"
    elif change == "disabled_rule":
        doc["rules"][0]["maturity"] = "disabled"
    elif change in {"insufficient_rule", "not_applicable_rule"}:
        doc["rules"][0]["decision"] = change.removesuffix("_rule")
    elif change == "missing_basis":
        group.pop("working_basis")
    elif change == "empty_basis":
        group["working_basis"] = {}
        group["working_basis"]["quantity_level"] = "inferred"
    elif change == "wrong_basis_evidence":
        group["working_basis"]["evidence_ids"] = ["not-in-group"]
    elif change == "wrong_basis_rule":
        group["working_basis"]["rule_ids"] = ["not-in-group"]
    elif change == "confirmed_basis":
        group["working_basis"]["quantity_level"] = "confirmed"
    elif change == "production_test_evidence":
        doc["evidence"][0]["fact_status"] = "test_only"
    elif change == "source_changed":
        case["source"].write_text('{"revised":true}')
    run = run_calculator(case)
    assert run.returncode == 2
    assert message in run.stderr
    assert not case["paths"]["result"].exists()


def test_missing_node_still_preserves_null_and_unresolved_dimension(case):
    doc = case["manifest"]
    group = doc["bar_groups"][0]
    group.update(status="unresolved", method="deferred", deferred_reason="节点缺页，当前资料无可核对替代方案", unresolved_ids=["U-NODE"])
    for key in ("count_derivation", "base_length_each_m", "additions_each_m"):
        group.pop(key)
    doc["unresolved"] = [dict(unresolved_id="U-NODE", type="missing_source", description="缺本组适用节点页",
        impact="无法确定单根展开长度", required_input="本例节点图", handling="stop",
        related_result_ids=list(group["outputs"].values()))]
    result, evidence = successful(case)
    assert all(row["结果"]["数值"] is None for row in result["工程量结果"])
    assert all(row["quantity_level"] == "unresolved" for row in result["工程量结果"])
    assert evidence["bar_group_calculations"][0]["quantity_level"] == "unresolved"
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("level", ["inferred", "scenario"])
def test_actual_native_working_result_cannot_be_promoted_by_workflow_claim(case, level):
    case["manifest"]["bar_groups"][0]["working_basis"]["quantity_level"] = level
    successful(case)
    source, result = case["source"], case["paths"]["result"]
    document = dict(schema_version="qto-project-workflow/1.0",
        artifacts=[dict(id="SRC-PROJECT", kind="source", path=str(source)), dict(id="R-RB-1", kind="result", path=str(result))],
        scopes=[dict(id="B1", title="B1 局部原生钢筋成果对接", basis_refs=["SRC-PROJECT"],
                     inventory_disposition="not_applicable", inventory_basis_refs=["SRC-PROJECT"],
                     reason="本测试核验专业原生成果数量分层，不声称工程图形覆盖完整", inventory_artifact_ids=[], required_task_ids=[])],
        work_packages=[dict(id="RB", owner_skill="qto-rebar", scope_ids=["B1"])],
        objects=[dict(id="B1-LOWER-X", scope_id="B1", observations=[], basis_refs=["SRC-PROJECT"], reason="本例明确钢筋组")],
        observation_dispositions=[],
        quantity_targets=[dict(id="L", scope_id="B1", work_package_id="RB", physical_id="B1-LOWER-X", extent_id="group",
            quantity_kind="rebar_length", measurement_basis_id="physical_design", scenario_id="base", unit="m",
            quantity_role="geometric", disposition="required", basis_refs=["SRC-PROJECT"], reason="根数乘单根中心线长度")],
        quantity_claims=[dict(id="C", target_id="L", artifact_id="R-RB-1", result_pointer="/工程量结果/1/结果/数值",
            unit_pointer="/工程量结果/1/单位", identity_pointer="/工程量结果/1/成果项编号", expected_identity="QTY-B1-L",
            quantity_level="confirmed", owner_work_package_id="RB")])
    report = reconcile(document, {"SRC-PROJECT": observed(source), "R-RB-1": observed(result)})
    assert len(report["result_index"]) == 1, json.dumps(report, ensure_ascii=False, indent=2)
    assert report["result_index"][0]["quantity"] == 21.0
    assert report["result_index"][0]["quantity_level"] == level
    assert report["totals"][0]["quantity_level"] == level
    assert report["result_index"][0]["formal_adoption"] is False


def test_tampering_result_layer_cannot_pass_bound_replay(case):
    result, _ = successful(case)
    result["工程量结果"][0]["quantity_level"] = "confirmed"
    write_json(case["paths"]["result"], result)
    run = validate_bound(case)
    assert run.returncode == 2 and "独立回放结果不一致" in run.stderr


@pytest.mark.parametrize("first,last,spacing,expected", [(0.1, 0.7, 0.2, 4), (-0.3, 0.3, 0.1, 7), (1.3, 1.3, 0.2, 1)])
def test_explicit_centres_have_exact_endpoint_count(case, first, last, spacing, expected):
    group = case["manifest"]["bar_groups"][0]
    group["count_derivation"].update(first_centre=first, last_centre=last, spacing=spacing)
    count, trace = calculator.derive_count(group)
    assert count == expected and trace["interval_count"] == expected - 1


@pytest.mark.parametrize("change,message", [
    ({"last_centre": 1.25}, "不得 round/ceil"), ({"last_centre": 0.05}, "末根中心"),
    ({"spacing": 0}, "必须大于"), ({"spacing": -0.2}, "必须大于"),
    ({"spacing": True}, "有限数字"), ({"first_centre": None}, "有限数字"),
    ({"last_centre": float("inf")}, "有限数字"), ({"unit": "mm"}, "必须为 m"),
])
def test_inconsistent_or_unbounded_layout_is_not_rounded_into_bars(case, change, message):
    group = case["manifest"]["bar_groups"][0]
    group["count_derivation"].update(change)
    with pytest.raises(calculator.ManifestError, match=message):
        calculator.derive_count(group)


def test_model_can_supply_irregular_positions_without_inventing_spacing_rule(case):
    group = case["manifest"]["bar_groups"][0]
    group["count_derivation"] = dict(method="explicit_centres", unit="m", centres=[0.1, 0.3, 0.5, 0.62],
        basis="本例末段实际单列，位置由模型解释且分层一致", evidence_ids=["EV-RB-1"])
    count, trace = calculator.derive_count(group)
    assert count == 4 and trace["centres"] == [0.1, 0.3, 0.5, 0.62]
    group["count_derivation"]["centres"].append(0.5)
    with pytest.raises(calculator.ManifestError, match="重复位置"):
        calculator.derive_count(group)


def test_caller_cannot_override_derived_count(case):
    group = case["manifest"]["bar_groups"][0]
    group["count"] = 8
    with pytest.raises(calculator.ManifestError, match="count 与 count_derivation 不一致"):
        calculator.derive_count(group)


def known_count_missing_node(case):
    """The real source retains a layout, but does not contain a length node."""
    doc = case["manifest"]
    source = json.loads(case["source"].read_text())
    source.pop("centreline")
    write_json(case["source"], source)
    doc["sources"][0].update(sha256=observed(case["source"])["sha256"],
        reason="本组排布可确定根数，节点页尚缺", neutral_facts="本组首末钢筋中心与间距",
        unused_impact="不能确定本组已知根数")
    doc["sources"][0]["json_assertions"] = {key: value for key, value in doc["sources"][0]["json_assertions"].items()
                                           if not key.startswith("centreline.")}
    doc["evidence"][0].update(neutral_fact="本组首末钢筋中心及间距已给定，未取得适用节点", locator="layout")
    doc["rules"][0].update(name="本组明确首末中心与等间距计数", execution="按明确首末中心与等间距计算根数", locator="S / layout")
    group = doc["bar_groups"][0]
    group.update(method="count_only", count_unit="根", status="partial", unresolved_ids=["U-NODE"],
                 deferred_dimensions=["length", "weight"], deferred_reason="缺适用节点页，展开长度及理论重量未确定")
    group["working_basis"]["reason"] = "平面排布首末中心归于本组，间距与两端一致；不以缺失节点补长度"
    for key in ("base_length_each_m", "additions_each_m", "unit_mass_method", "unit_mass_kg_m", "unit_mass_basis"):
        group.pop(key, None)
    doc["unresolved"] = [dict(unresolved_id="U-NODE", type="missing_source", description="缺本组适用节点页",
        impact="仅能计算根数，不能形成展开长度及理论重量", required_input="本组适用节点图", handling="keep_proven",
        related_result_ids=list(group["outputs"].values()))]
    return group


@pytest.mark.parametrize("role,level", [("reference", "inferred"), ("reference", "scenario"), ("adopted", "confirmed")])
def test_one_physical_group_preserves_known_count_and_null_length_weight(case, role, level):
    group = known_count_missing_node(case)
    if role == "adopted":
        group.update(adoption_role="adopted")
        group.pop("working_basis")
        case["manifest"]["evidence"][0]["fact_status"] = "verified"
        case["manifest"]["rules"][0].update(decision="adopted", maturity="trial")
    else:
        group["working_basis"]["quantity_level"] = level
    result, evidence = successful(case)
    rows = result["工程量结果"]
    assert [row["结果"]["数值"] for row in rows] == [7, None, None]
    assert [row["单位"] for row in rows] == ["根", "m", "kg"]
    assert [row["quantity_level"] for row in rows] == [level, "unresolved", "unresolved"]
    assert [row["状态"] for row in rows] == ["部分计算", "证据不足", "证据不足"]
    assert {row["钢筋组编号"] for row in rows} == {group["group_id"]}
    calc = evidence["bar_group_calculations"][0]
    assert calc["count"] == 7 and calc["count_derivation"]["interval_count"] == 6
    assert calc["dimension_quantity_levels"] == {"count": level, "length": "unresolved", "weight": "unresolved"}
    assert "total_length_m" not in calc and "weight_kg" not in calc
    if role == "reference":
        assert [row["成果分层"]["进入工作量"] for row in rows] == [True, False, False]
        assert all(row["成果分层"]["进入正式量"] is False and row["formal_adoption"] is False for row in rows)
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_legacy_count_only_still_outputs_one_count_with_bound_replay(case):
    group = case["manifest"]["bar_groups"][0]
    group.update(method="count_only", count_unit="根", outputs={"count": "QTY-B1-N"})
    result, _ = successful(case)
    assert len(result["工程量结果"]) == 1 and result["工程量结果"][0]["结果"]["数值"] == 7
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("change,message", [
    ({"deferred_dimensions": ["count", "length", "weight"]}, "不重复的 length/weight"),
    ({"deferred_dimensions": ["length", "length"]}, "不重复的 length/weight"),
    ({"deferred_dimensions": [{}]}, "不重复的 length/weight"),
    ({"deferred_reason": ""}, "deferred_reason"),
    ({"status": "complete"}, "不得关联未决事项"),
    ({"unresolved_ids": []}, "必须关联未决事项"),
    ({"count_unit": "个"}, "count_unit=根"),
    ({"outputs": {"count": "QTY-B1-N"}}, "明确列出的未决维度"),
    ({"developed_length_each_m": 3.0}, "不得同时填写"),
    ({"weight_kg": 0}, "不得同时填写"),
])
def test_partial_count_cannot_hide_missing_or_contradictory_dimensions(case, change, message):
    group = known_count_missing_node(case)
    group.update(change)
    run = run_calculator(case)
    assert run.returncode == 2 and message in run.stderr, run.stdout + run.stderr
    assert not case["paths"]["result"].exists()


@pytest.mark.parametrize("duplicate_dimension", ["count", "length"])
def test_partial_count_does_not_relax_same_physical_group_dedup(case, duplicate_dimension):
    group = known_count_missing_node(case)
    duplicate = copy.deepcopy(group)
    duplicate.update(group_id="B1-DUPLICATE")
    duplicate["outputs"] = {duplicate_dimension: "QTY-B1-DUPLICATE"}
    if duplicate_dimension == "count":
        duplicate.pop("deferred_dimensions")
    else:
        duplicate.update(status="unresolved", method="deferred", deferred_dimensions=["length"])
    case["manifest"]["bar_groups"].append(duplicate)
    run = run_calculator(case)
    assert run.returncode == 2 and "钢筋组物理身份重复" in run.stderr


@pytest.mark.parametrize("diameter,grade", [(8.0, "HRB400"), (8, " HRB400 ")])
def test_equivalent_diameter_or_trimmed_grade_cannot_duplicate_a_physical_group(case, diameter, grade):
    group = case["manifest"]["bar_groups"][0]
    duplicate = copy.deepcopy(group)
    duplicate.update(group_id="B1-DUPLICATE", diameter_mm=diameter, grade=grade)
    duplicate["outputs"] = {key: value + "-DUPLICATE" for key, value in group["outputs"].items()}
    case["manifest"]["bar_groups"].append(duplicate)
    run = run_calculator(case)
    assert run.returncode == 2 and "钢筋组物理身份重复" in run.stderr


def test_distinct_real_diameter_is_not_merged_by_a_numeric_tolerance(case):
    group = case["manifest"]["bar_groups"][0]
    source = json.loads(case["source"].read_text())
    source["other_spec"] = dict(diameter_mm=10, unit_mass_kg_m=0.617)
    write_json(case["source"], source)
    case["manifest"]["sources"][0]["sha256"] = observed(case["source"])["sha256"]
    case["manifest"]["sources"][0]["json_assertions"].update({"other_spec.diameter_mm": 10, "other_spec.unit_mass_kg_m": 0.617})
    other = copy.deepcopy(group)
    other.update(group_id="B1-OTHER-SPEC", diameter_mm=10, unit_mass_kg_m=0.617,
                 unit_mass_basis="独立规格的本例明确理论单位质量")
    other["outputs"] = {key: value + "-OTHER" for key, value in group["outputs"].items()}
    case["manifest"]["bar_groups"].append(other)
    result, evidence = successful(case)
    assert len(result["工程量结果"]) == 6
    assert [calc["diameter_mm"] for calc in evidence["bar_group_calculations"]] == [8.0, 10.0]
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_partial_unresolved_dimension_cannot_be_changed_to_zero_and_replayed(case):
    known_count_missing_node(case)
    result, _ = successful(case)
    result["工程量结果"][1]["结果"]["数值"] = 0
    write_json(case["paths"]["result"], result)
    checked = validate_bound(case)
    assert checked.returncode == 2 and "独立回放结果不一致" in checked.stderr


def test_workflow_keeps_partial_count_working_and_null_dimensions_unaccounted(case):
    known_count_missing_node(case)
    successful(case)
    source, result = case["source"], case["paths"]["result"]
    document = dict(schema_version="qto-project-workflow/1.0",
        artifacts=[dict(id="SRC-PROJECT", kind="source", path=str(source)), dict(id="RESULT", kind="result", path=str(result))],
        scopes=[dict(id="B1", title="已知根数与未决节点分别交接", basis_refs=["SRC-PROJECT"],
            inventory_disposition="not_applicable", inventory_basis_refs=["SRC-PROJECT"],
            reason="专业原生成果分维度测试，不声称工程图形覆盖完整", inventory_artifact_ids=[], required_task_ids=[])],
        work_packages=[dict(id="RB", owner_skill="qto-rebar", scope_ids=["B1"])],
        objects=[dict(id="B1-LOWER-X", scope_id="B1", observations=[], basis_refs=["SRC-PROJECT"], reason="同一个实际钢筋组")],
        observation_dispositions=[], quantity_targets=[], quantity_claims=[])
    for index, (dimension, unit, native_id) in enumerate([("count", "根", "QTY-B1-N"), ("length", "m", "QTY-B1-L"),
                                                        ("weight", "kg", "QTY-B1-W")]):
        document["quantity_targets"].append(dict(id=dimension, scope_id="B1", work_package_id="RB", physical_id="B1-LOWER-X",
            extent_id="group", quantity_kind="rebar_" + dimension, measurement_basis_id="physical_design", scenario_id="base",
            unit=unit, quantity_role="geometric", disposition="required", basis_refs=["SRC-PROJECT"], reason="本组独立计量维度"))
        document["quantity_claims"].append(dict(id="C-" + dimension, target_id=dimension, artifact_id="RESULT",
            result_pointer=f"/工程量结果/{index}/结果/数值", unit_pointer=f"/工程量结果/{index}/单位",
            identity_pointer=f"/工程量结果/{index}/成果项编号", expected_identity=native_id,
            quantity_level="confirmed", owner_work_package_id="RB"))
    report = reconcile(document, {"SRC-PROJECT": observed(source), "RESULT": observed(result)})
    assert [(row["target_id"], row["quantity"], row["quantity_level"]) for row in report["result_index"]] == [("count", 7, "inferred")]
    assert len(report["totals"]) == 1 and report["totals"][0]["quantity"] == 7 and report["totals"][0]["unit"] == "根"
    assert {item["target_id"] for item in report["unaccounted_targets"]} == {"length", "weight"}
    assert all("null" in " ".join(item["reasons"]) for item in report["unaccounted_targets"])
    assert report["result_index"][0]["formal_adoption"] is False


def test_receiving_actual_node_recalculates_same_group_and_result_ids(case):
    complete_manifest = copy.deepcopy(case["manifest"])
    complete_source = case["source"].read_bytes()
    known_count_missing_node(case)
    partial, _ = successful(case)
    assert [row["结果"]["数值"] for row in partial["工程量结果"]] == [7, None, None]
    # The newly obtained source supplies the actual 2.4 + 0.3 + 0.3 m node.
    # The model changes the supported method, retaining physical/result IDs.
    case["source"].write_bytes(complete_source)
    case["manifest"] = complete_manifest
    complete, _ = successful(case)
    assert [row["成果项编号"] for row in complete["工程量结果"]] == [row["成果项编号"] for row in partial["工程量结果"]]
    assert [row["结果"]["数值"] for row in complete["工程量结果"]] == [7, 21.0, 8.295]
    assert all(row["quantity_level"] == "inferred" and row["formal_adoption"] is False for row in complete["工程量结果"])
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("specification,message", [
    ({"diameter_mm": "8.0"}, "有限数字"), ({"diameter_mm": True}, "有限数字"),
    ({"diameter_mm": 0}, "必须大于 0"), ({"diameter_mm": -8}, "必须大于 0"),
    ({"diameter_mm": float("inf")}, "有限数字"), ({"grade": 400}, "非空文本"), ({"grade": " "}, "非空文本"),
])
def test_count_only_supplied_specification_must_use_existing_numeric_and_text_types(case, specification, message):
    group = case["manifest"]["bar_groups"][0]
    group.update(method="count_only", count_unit="根", outputs={"count": "QTY-B1-N"}, **specification)
    run = run_calculator(case)
    assert run.returncode == 2 and message in run.stderr
    assert not case["paths"]["result"].exists()


def test_count_only_string_diameter_cannot_create_a_second_numeric_specification(case):
    group = case["manifest"]["bar_groups"][0]
    group.update(method="count_only", count_unit="根", outputs={"count": "QTY-B1-N"})
    duplicate = copy.deepcopy(group)
    duplicate.update(group_id="B1-DUPLICATE", diameter_mm="8.0", outputs={"count": "QTY-B1-DUPLICATE"})
    case["manifest"]["bar_groups"].append(duplicate)
    run = run_calculator(case)
    assert run.returncode == 2 and "diameter_mm 必须是有限数字" in run.stderr
    assert not case["paths"]["result"].exists()


def test_legacy_connector_count_without_bar_specification_remains_usable(case):
    group = case["manifest"]["bar_groups"][0]
    group.update(method="count_only", component_type="连接件", bar_category="套筒", count_unit="个", count=7,
                 outputs={"count": "QTY-B1-N"})
    for key in ("grade", "diameter_mm", "count_derivation"):
        group.pop(key)
    source = json.loads(case["source"].read_text())
    source["connector_count"] = 7
    write_json(case["source"], source)
    case["manifest"]["sources"][0]["sha256"] = observed(case["source"])["sha256"]
    case["manifest"]["sources"][0]["json_assertions"]["connector_count"] = 7
    result, _ = successful(case)
    assert len(result["工程量结果"]) == 1
    assert result["工程量结果"][0]["结果"]["数值"] == 7 and result["工程量结果"][0]["单位"] == "个"
    checked = validate_bound(case)
    assert checked.returncode == 0, checked.stdout + checked.stderr
