"""Independent linear-section volumes; these tests do not certify CAD recognition."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/qto-earthwork-terrain/scripts/calculate_earthwork_terrain.py"
spec = importlib.util.spec_from_file_location("earthwork_prismoid_calculator", SCRIPT)
calc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calc)
validator_spec = importlib.util.spec_from_file_location(
    "earthwork_prismoid_validator", SCRIPT.with_name("validate_earthwork_result.py"))
validator = importlib.util.module_from_spec(validator_spec)
validator_spec.loader.exec_module(validator)

METHODS = ("rectangular_frustum", "orthogonal_offset_frustum")


def slope_inputs(method, *, scale=1, depth=1):
    dimensions = dict(bottom_length=2, bottom_width=1, depth=depth)
    if method == "rectangular_frustum":
        dimensions.update(top_length=4, top_width=3)
    else:
        dimensions.update(offset_left=1, offset_right=1, offset_front=1, offset_back=1)
    return {key: value * scale for key, value in dimensions.items()}


def compute(method, dimensions):
    return calc.calculate_method(method, dimensions, "QTY-KNOWN", {}, {})


@pytest.mark.parametrize("method", METHODS)
def test_non_similar_rectangular_pit_has_exact_hand_integral(method):
    # At fractional height t: A(t)=(2+2t)(1+2t)=2+6t+4t².
    # Integral 0..1 is 2+3+4/3 = 19/3, not the old 6.2996598285.
    value, formula, checks = compute(method, slope_inputs(method))
    assert value == pytest.approx(19 / 3, rel=1e-14)
    assert checks["bottom_area"] == 2
    assert checks["mid_area"] == 6
    assert checks["top_area"] == 12
    assert checks["section_model"] == "parallel_rectangles_linear_dimensions"
    assert "A中" in formula
    json.dumps(checks, allow_nan=False)


@pytest.mark.parametrize("dims,expected", [
    ((3, 2, 3, 2, 5), 30),       # Equal section.
    ((3, 2, 6, 4, 5), 70),       # Similar rectangles, old formula also valid.
    ((6, 4, 3, 2, 5), 70),       # Reversing end sections preserves volume.
    ((10, 1, 12, 11, 3), 203),   # Independent length/width growth rates.
    ((1, 9, 9, 1, 3), 59),       # Both end areas 9; middle area is 25.
])
def test_rectangular_section_controls(dims, expected):
    inputs = dict(zip(("bottom_length", "bottom_width", "top_length", "top_width", "depth"), dims))
    value, _, _ = compute("rectangular_frustum", inputs)
    assert value == pytest.approx(expected, rel=1e-14)


@pytest.mark.parametrize("offsets", [(0, 4, 1, .5), (3.5, .5, .2, 1.3)])
def test_asymmetric_side_offsets_keep_exact_volume(offsets):
    inputs = dict(bottom_length=2, bottom_width=3, depth=2.5)
    inputs.update(zip(("offset_left", "offset_right", "offset_front", "offset_back"), offsets))
    # A(t)=(2+4t)(3+1.5t)=6+15t+6t²; integral times 2.5 is 38.75.
    value, _, checks = compute("orthogonal_offset_frustum", inputs)
    assert value == pytest.approx(38.75, rel=1e-14)
    assert checks["top_length"] == 6
    assert checks["top_width"] == 4.5
    assert checks["mid_length"] == 4
    assert checks["mid_width"] == 3.75


def test_zero_offsets_are_a_prism_and_similar_offsets_retain_classic_answer():
    inputs = dict(bottom_length=3, bottom_width=2, depth=5,
                  offset_left=0, offset_right=0, offset_front=0, offset_back=0)
    assert compute("orthogonal_offset_frustum", inputs)[0] == pytest.approx(30)
    inputs.update(offset_left=1, offset_right=2, offset_front=.5, offset_back=1.5)
    assert compute("orthogonal_offset_frustum", inputs)[0] == pytest.approx(70)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("scale,depth", [(.001, 1), (.25, 3), (1000, 1), (1, .25), (1, 7)])
def test_dimension_scaling_and_height_use_volume_dimension(method, scale, depth):
    value, _, _ = compute(method, slope_inputs(method, scale=scale, depth=depth))
    assert value == pytest.approx((19 / 3) * depth * scale ** 3, rel=1e-13, abs=0)


@pytest.mark.parametrize("dims,expected", [
    ((1e100, 1e100, 2e100, 2e100, .5), 7e200 / 6),  # Old A_bottom*A_top overflows.
    ((1e154, 1e154, 1e154, 1e154, .1), 1e307),      # 4*A_mid would overflow.
    ((1e308, 1e-308, 1e308, 1e-308, 1), 1),        # L_bottom+L_top would overflow.
])
def test_representable_large_geometry_avoids_intermediate_overflow(dims, expected):
    inputs = dict(zip(("bottom_length", "bottom_width", "top_length", "top_width", "depth"), dims))
    value, _, checks = compute("rectangular_frustum", inputs)
    assert math.isfinite(value)
    assert value == pytest.approx(expected, rel=1e-13)
    json.dumps(checks, allow_nan=False)


@pytest.mark.parametrize("method,changes", [
    ("rectangular_frustum", dict(bottom_length=1e308, bottom_width=1e308)),
    ("rectangular_frustum", dict(depth=1e308)),
    ("orthogonal_offset_frustum", dict(offset_left=1e308, offset_right=1e308)),
    ("orthogonal_offset_frustum", dict(depth=1e308)),
])
def test_finite_inputs_that_overflow_geometry_are_rejected(method, changes):
    inputs = slope_inputs(method)
    inputs.update(changes)
    with pytest.raises(calc.CalculationError, match="有限"):
        compute(method, inputs)


@pytest.mark.parametrize("method,changes", [
    ("rectangular_frustum", dict(top_width=0)),
    ("rectangular_frustum", dict(bottom_length=-1)),
    ("rectangular_frustum", dict(top_length=None)),
    ("rectangular_frustum", dict(depth=float("nan"))),
    ("orthogonal_offset_frustum", dict(offset_front=-1)),
    ("orthogonal_offset_frustum", dict(offset_back=float("inf"))),
    ("orthogonal_offset_frustum", dict(depth=0)),
])
def test_unsupported_dimensions_do_not_become_quantities(method, changes):
    inputs = slope_inputs(method)
    inputs.update(changes)
    with pytest.raises(calc.CalculationError):
        compute(method, inputs)


def test_unrepresented_geometry_must_use_deferred_or_fail():
    with pytest.raises(calc.CalculationError, match="method非法"):
        compute("rotated_or_stepped_rectangle", slope_inputs("rectangular_frustum"))
    value, reason, checks = calc.calculate_method(
        "deferred", {}, "QTY-STEP", {}, {"deferred_reason": "变坡台阶的分段边界尚未锁定"})
    assert value is None and "分段" in reason and checks == {}


@pytest.mark.parametrize("centers,expected", [([0], 19 / 3), ([0, 1], 25 / 3)])
def test_pit_slice_upper_bound_uses_exact_non_similar_standalone_volume(centers, expected):
    pits = [dict(x=x, y=0, bottom_length=2, bottom_width=1, depth=1, slope_x=1, slope_y=1)
            for x in centers]
    inputs = dict(pits=pits, slice_step=.001)
    value, formula, checks = compute("pit_union_slices", inputs)
    # Both cases have A(z) with quadratic coefficient 4. Midpoint error is 1/(3*n²).
    assert value == pytest.approx(expected - 1 / 3_000_000, rel=1e-12)
    assert checks["standalone_sum"] == pytest.approx(len(pits) * 19 / 3, rel=1e-14)
    assert value <= checks["standalone_sum"]
    assert checks["integration_is_approximate"] is True
    assert "中点切片" in formula


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return path


def manifest_fixture(tmp_path, method):
    dimensions = slope_inputs(method)
    source = write_json(tmp_path / "known-geometry-facts.json", {
        "units": "m", "pit": dimensions, "backfill_natural_dense_m3": 1,
        "geometry_model": "parallel rectangles with linear side movement"})
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "manifest_version": calc.MANIFEST_VERSION,
        "task": dict(business_discipline="土方", main_objects="独立试验坑",
                     system="基坑", building="试验建筑", zone="Z1", drawing_type="已知几何表",
                     drawing_version="test-1", output_dimension="数量种类、土体状态、采用角色"),
        "sources": [dict(source_id="SRC-GEOMETRY", path=str(source), sha256=source_sha,
                         selection="selected", source_type="json", file_role="已知几何测试依据",
                         source_role="几何与土体状态", reason="独立解析解对照", neutral_facts="上下矩形尺寸和深度",
                         not_used_impact="不可复算")],
        "evidence": [dict(evidence_id="EV-GEOMETRY", source_id="SRC-GEOMETRY", fact="米制尺寸和明确线性侧边",
                          locator="/pit", extraction="已知数字", quality="来源身份已绑定",
                          limitation="不验证原CAD语义识别", evidence_class="明确几何")],
        "rules": [dict(rule_id="R-GEOMETRY", decision="采用", maturity="正式", name="本试验显式几何模型",
                       source="已知解析几何", execution="米制，线性矩形截面，保持自然密实土体状态",
                       evidence_locator="SRC-GEOMETRY /geometry_model")],
        "unresolved": [],
        "items": [dict(item_id="QTY-PIT", name="Z1试验坑几何体积", object_type="pit_excavation",
                       quantity_kind="excavation_volume", measurement_basis="physical_geometry",
                       earth_state="natural_dense", adoption_role="adopted", scope_status="complete",
                       method=method, identity_signature="Z1-pit-physical", inputs=dimensions, unit="m³",
                       evidence_refs=["EV-GEOMETRY"], rule_refs=["R-GEOMETRY"], result_use="独立公式回归")],
    }
    return manifest, source, source_sha


@pytest.mark.parametrize("method", METHODS)
def test_manifest_result_preserves_sources_soil_state_and_explicit_unit(tmp_path, method):
    manifest, source, source_sha = manifest_fixture(tmp_path, method)
    # A millimetre drawing must have dimensions explicitly converted before this
    # metre-only calculator is invoked; it never infers conversion from magnitude.
    in_mm = {key: value * 1000 for key, value in manifest["items"][0]["inputs"].items()}
    manifest["items"][0]["inputs"] = {key: value / 1000 for key, value in in_mm.items()}
    original = copy.deepcopy(manifest)
    path = write_json(tmp_path / "manifest.json", manifest)
    result = calc.calculate_from_path(path)
    assert manifest == original
    row = result["工程量结果"][0]
    assert row["结果"]["数值"] == pytest.approx(6.333333333, rel=1e-12)
    assert row["单位"] == "m³" and row["来源"] == ["EV-GEOMETRY"] and row["规则"] == ["R-GEOMETRY"]
    assert row["专业检查"]["土体状态"] == "natural_dense"
    assert row["专业检查"]["计量基准"] == "physical_geometry"
    assert row["专业检查"]["计算方法"] == method
    assert result["资料选择"][0]["路径"] == str(source)
    assert result["证据目录"][0]["来源身份"] == "SHA256:" + source_sha
    assert result["绑定验证"]["运行清单SHA256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert validator.validate_document(result) == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("unit", ["mm³", "m²", "m3"])
def test_manifest_does_not_silently_change_output_units(tmp_path, unit):
    manifest, _, _ = manifest_fixture(tmp_path, "rectangular_frustum")
    manifest["items"][0]["unit"] = unit
    with pytest.raises(calc.CalculationError, match="单位必须为m³"):
        calc.calculate_from_path(write_json(tmp_path / "manifest.json", manifest))


def add_balance(manifest):
    fill = copy.deepcopy(manifest["items"][0])
    fill.update(item_id="QTY-FILL", name="Z1回填的自然密实体积", object_type="backfill",
                quantity_kind="backfill_volume", identity_signature="Z1-backfill-natural",
                method="direct_volume", inputs=dict(volume=1))
    surplus = copy.deepcopy(fill)
    surplus.update(item_id="QTY-SURPLUS", name="Z1自然密实余方", object_type="surplus",
                   quantity_kind="surplus_volume", identity_signature="Z1-surplus-natural",
                   method="difference", balance_signature="physical_geometry|natural_dense|Z1",
                   inputs=dict(minuend_item="QTY-PIT", subtrahend_item="QTY-FILL"))
    manifest["items"].extend([fill, surplus])


def test_same_basis_balance_uses_corrected_quantity_and_keeps_roles_separate(tmp_path):
    manifest, _, _ = manifest_fixture(tmp_path, "orthogonal_offset_frustum")
    add_balance(manifest)
    reference = copy.deepcopy(manifest["items"][0])
    reference.update(item_id="QTY-REF", identity_signature="Z1-reference-scenario", adoption_role="reference",
                     measurement_basis="scheme_reference", earth_state="loose")
    manifest["items"].append(reference)
    result = calc.calculate_from_path(write_json(tmp_path / "manifest.json", manifest))
    assert result["工程量结果"][2]["结果"]["数值"] == pytest.approx(5.333333333, rel=1e-12)
    assert result["工程量结果"][3]["专业检查"]["进入采用小计"] is False
    assert len(result["工程量小计"]) == 4
    assert validator.validate_document(result) == []


@pytest.mark.parametrize("change", ["soil", "basis", "scope", "negative"])
def test_difference_boundaries_still_reject_mixed_or_negative_balance(tmp_path, change):
    manifest, _, _ = manifest_fixture(tmp_path, "rectangular_frustum")
    add_balance(manifest)
    fill = manifest["items"][1]
    if change == "soil":
        fill["earth_state"] = "compacted"
    elif change == "basis":
        fill.update(measurement_basis="scheme_reference", adoption_role="reference")
    elif change == "scope":
        fill["balance_signature"] = "physical_geometry|natural_dense|Z2"
    else:
        fill["inputs"]["volume"] = 10
    with pytest.raises(calc.CalculationError, match="不一致|差值为负"):
        calc.calculate_from_path(write_json(tmp_path / "manifest.json", manifest))


def test_changed_source_still_blocks_recalculation(tmp_path):
    manifest, source, _ = manifest_fixture(tmp_path, "rectangular_frustum")
    path = write_json(tmp_path / "manifest.json", manifest)
    source.write_text("changed geometry", encoding="utf-8")
    with pytest.raises(calc.CalculationError, match="来源SHA256不匹配"):
        calc.calculate_from_path(path)
