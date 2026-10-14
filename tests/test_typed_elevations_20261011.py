"""Portable engineering arithmetic and negative evidence/route cases.

Synthetic source records below have independently specified numeric expectations;
they test computation, not recognition accuracy on construction drawings.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills/_qto-shared-evidence/scripts"
sys.path.insert(0, str(SHARED))
from qto_elevation_facts import SCHEMA, evaluate_elevation_link
from qto_pipe_topology import prepare_network
sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_evidence_helpers import inherited_refs, write_review


def ev(label="section"):
    return {"source_ids": [label], "basis": "synthetic dimensioned section", "level": "confirmed"}


def value(fid, number, unit="m", **extra):
    return {"fact_id": fid, "value": number, "unit": unit, **ev(), **extra}


def fact(fid, number, unit="m", kind="pipe_center", datum="building-zero"):
    return {**value(fid, number, unit), "datum_id": datum, "reference_id": "port:" + fid,
            "measurement_kind": kind, "system": "supply"}


def register(spec):
    """Materialize a synthetic source fixture; production code never does this."""
    observations = []
    for key in ("from", "to"):
        row = spec[key]
        observations.append({"id": row["fact_id"], "value": row["value"], "unit": row["unit"], "measurement_kind": row["measurement_kind"],
            "datum_id": row["datum_id"], "reference_id": row["reference_id"], "system": spec["system"],
            "level": row["level"], "status": row.get("status")})
        c = row.get("centerline_conversion", {})
        for key, kind in (("outer_diameter", "outer_diameter"), ("offset", "vertical_offset")):
            if key in c:
                q = c[key]
                observations.append({"id": q["fact_id"], "value": q["value"], "unit": q["unit"], "measurement_kind": kind, "level": q["level"]})
                observations.append({"id": c["conversion_id"], "measurement_kind": "centerline_conversion", "level": c["level"],
                    "method": c["method"], "from_fact_id": row["fact_id"], "from_reference_id": row["reference_id"],
                    "target_reference_id": c["target_reference_id"], "datum_id": row["datum_id"], "system": spec["system"],
                    "operand_fact_id": q["fact_id"]})
    for q in spec.get("datum_transforms", []):
        observations.append({"id": q["transform_id"], "value": q["value"], "unit": q["unit"], "measurement_kind": "datum_offset",
                             "from_datum": q["from_datum"], "to_datum": q["to_datum"], "level": q["level"]})
    endpoints = {side: spec[side].get("centerline_conversion", {}).get("target_reference_id", spec[side]["reference_id"]) for side in ("from", "to")}
    refs = {"from_reference_id": endpoints["from"], "to_reference_id": endpoints["to"], "system": spec["system"]}
    route = spec.get("route", {})
    if route.get("kind", "unresolved") != "unresolved":
        route.setdefault("route_id", "route-shape")
        observations.append({"id": route["route_id"], "measurement_kind": "route_shape", "route_kind": route["kind"], "level": route["level"], **refs})
    q = spec.get("route", {}).get("horizontal_displacement")
    if q is not None:
        observations.append({"id": q["fact_id"], "value": q["value"], "unit": q["unit"], "measurement_kind": "horizontal_displacement", "level": q["level"], **refs})
    spec["sources"] = [{"id": "section", "sha256": hashlib.sha256(json.dumps(observations, sort_keys=True).encode()).hexdigest(),
                        "locator": "synthetic section and dimensions", "facts": observations}]
    return spec


def base():
    return register({"schema": SCHEMA, "system": "supply", "from": fact("bottom", 3), "to": fact("top", 7.2),
                     "route": {"kind": "vertical", **ev()}})


def outer(spec, where="from", kind="pipe_outer_bottom", diameter=114.3):
    spec[where]["measurement_kind"] = kind
    spec[where]["centerline_conversion"] = {
        "conversion_id": "center-" + where, "method": "outer_radius", "target_reference_id": "pipe-center-" + where, **ev(),
        "outer_diameter": value("OD-" + where, diameter, "mm", measurement_kind="outer_diameter")}
    return register(spec)


@pytest.mark.parametrize("unit,multiplier", [("mm", 1000), ("cm", 100), ("m", 1), ("ft", 1/.3048), ("in", 1/.0254)])
def test_unit_migration_preserves_physical_length(unit, multiplier):
    spec = base()
    spec["from"].update(value=3*multiplier, unit=unit)
    spec["to"].update(value=7.2*multiplier, unit=unit)
    out = evaluate_elevation_link(register(spec))
    assert out["value"] == pytest.approx(4.2)
    assert out["vertical_difference"] == pytest.approx(4.2)
    assert out["status"] == "RESOLVED"


def test_different_input_and_output_units_and_direction():
    spec = base()
    spec["from"].update(value=7200, unit="mm")
    spec["to"].update(value=300, unit="cm")
    out = evaluate_elevation_link(register(spec), "mm")
    assert out["value"] == pytest.approx(4200)
    assert out["vertical_difference"] == pytest.approx(-4200)
    assert out["calculation_run"]["errors"] == []


def test_real_outer_diameter_changes_centerline_and_length():
    small = outer(base(), diameter=114.3)
    large = outer(base(), diameter=168.3)
    a, b = evaluate_elevation_link(small), evaluate_elevation_link(large)
    assert a["value"] == pytest.approx(4.14285)
    assert b["value"] == pytest.approx(4.11585)
    assert a["value"] - b["value"] == pytest.approx(.027)
    assert a["input_sha256"] != b["input_sha256"]
    assert "OD-from" in a["source_reference_check"]["bound_fact_ids"]


def test_pipe_outer_top_subtracts_radius():
    assert evaluate_elevation_link(outer(base(), kind="pipe_outer_top"))["value"] == pytest.approx(4.25715)


def test_floor_to_center_requires_signed_offset():
    spec = base()
    spec["from"]["measurement_kind"] = "finished_floor"
    incomplete = evaluate_elevation_link(register(spec))
    assert incomplete["value"] is None
    assert incomplete["status"] == "UNRESOLVED"
    spec["from"]["centerline_conversion"] = {"conversion_id": "floor-port", "method": "signed_offset",
        "target_reference_id": "pipe-center", "offset": value("mounting-height", 1200, "mm"), **ev()}
    out = evaluate_elevation_link(register(spec))
    assert out["value"] == pytest.approx(3.0)


def test_different_datum_without_transform_is_unresolved():
    spec = base()
    spec["to"]["datum_id"] = "national-datum"
    out = evaluate_elevation_link(register(spec))
    assert out["value"] is None and out["vertical_projection"] is None
    assert any(i["type"] == "不同基准缺少换算依据" for i in out["issues"])


def transform(tid, a, b, number, unit="m"):
    return {"transform_id": tid, "from_datum": a, "to_datum": b, "value": number, "unit": unit, **ev()}


@pytest.mark.parametrize("reverse", [False, True])
def test_explicit_datum_shift_recomputes_absolute_levels(reverse):
    spec = base()
    spec["to"].update(value=107200, unit="mm", datum_id="national-datum")
    spec["datum_transforms"] = [transform("building-to-national", "building-zero", "national-datum", 100)]
    if reverse:
        spec["datum_transforms"] = [transform("national-to-building", "national-datum", "building-zero", -100000, "mm")]
    out = evaluate_elevation_link(register(spec))
    assert out["value"] == pytest.approx(4.2)


def test_multi_datum_chain_and_contradictory_cycle():
    spec = base()
    spec["to"].update(value=107.2, datum_id="national-datum")
    spec["datum_transforms"] = [transform("a", "building-zero", "site-zero", 20), transform("b", "site-zero", "national-datum", 80)]
    assert evaluate_elevation_link(register(spec))["value"] == pytest.approx(4.2)
    spec["datum_transforms"].append(transform("conflict", "building-zero", "national-datum", 101))
    with pytest.raises(ValueError, match="矛盾"):
        evaluate_elevation_link(register(spec))


@pytest.mark.parametrize("kind,expected", [("straight", 5), ("orthogonal", 7)])
def test_known_offset_route_is_not_vertical_difference(kind, expected):
    spec = base()
    spec["from"]["value"] = 0
    spec["to"]["value"] = 4
    spec["route"] = {"kind": kind, "horizontal_displacement": value("offset-plan", 3000, "mm"), **ev()}
    out = evaluate_elevation_link(register(spec))
    assert out["vertical_projection"] == 4 and out["value"] == expected


def test_horizontal_offset_conflicts_with_vertical_claim():
    spec = base()
    spec["route"]["horizontal_displacement"] = value("offset-plan", .3)
    with pytest.raises(ValueError, match="非零水平位移"):
        evaluate_elevation_link(register(spec))


@pytest.mark.parametrize("kind", ["straight", "orthogonal", "unresolved"])
def test_height_difference_is_not_an_unproved_route_length(kind):
    spec = base()
    spec["route"]["kind"] = kind
    out = evaluate_elevation_link(register(spec))
    assert out["value"] is None
    assert out["vertical_projection"] == pytest.approx(4.2)


def test_missing_route_has_known_projection_only():
    spec = base()
    spec.pop("route")
    out = evaluate_elevation_link(spec)
    assert out["value"] is None and out["vertical_projection"] == pytest.approx(4.2)


def test_scenario_conversion_cannot_become_confirmed_quantity():
    spec = outer(base())
    spec["from"]["centerline_conversion"]["outer_diameter"]["level"] = "scenario"
    assert evaluate_elevation_link(spec)["quantity_level"] == "方案暂估量"


@pytest.mark.parametrize("mutation", ["source-missing", "unregistered", "missing-fact", "different-value", "nominal-diameter", "dn-key"])
def test_outer_diameter_evidence_cannot_be_replaced_by_nominal_text(mutation):
    spec = outer(base())
    diameter = spec["from"]["centerline_conversion"]["outer_diameter"]
    if mutation == "source-missing":
        spec.pop("sources")
    elif mutation == "unregistered":
        diameter["source_ids"] = ["not-a-source"]
    elif mutation == "missing-fact":
        diameter["fact_id"] = "not-in-source"
    elif mutation == "different-value":
        diameter["value"] = 100
    elif mutation == "nominal-diameter":
        spec["sources"][0]["facts"][1]["measurement_kind"] = "nominal_diameter"
        diameter["basis"] = "DN equals outer diameter"
    else:
        diameter["dn"] = 100
    with pytest.raises(ValueError):
        evaluate_elevation_link(spec)


@pytest.mark.parametrize("mutation", ["system", "source_ids", "basis", "level", "kind", "nan"])
def test_invalid_fact_is_rejected(mutation):
    spec = base()
    changes = {"system": "return", "source_ids": [], "basis": "", "level": "approved", "kind": "BOP", "nan": float("nan")}
    key = {"kind": "measurement_kind", "nan": "value"}.get(mutation, mutation)
    spec["from"][key] = changes[mutation]
    with pytest.raises(ValueError):
        evaluate_elevation_link(spec)


def test_path_hash_binds_source_bytes(tmp_path):
    spec = base()
    source = tmp_path / "原始标高.txt"
    source.write_text("Original drawing observations", encoding="utf-8")
    spec["sources"][0].update(path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    assert evaluate_elevation_link(spec)["source_reference_check"]["file_hashes_checked"] == ["section"]
    source.write_text("Changed drawing observations", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        evaluate_elevation_link(spec)


def test_existing_dependency_engine_invalidates_only_related_reviews(tmp_path):
    spec = outer(base())
    observations = spec["sources"][0]["facts"]
    spec["sources"] = []
    for observation in observations:
        sid = "source:" + observation["id"]
        spec["sources"].append({"id": sid, "sha256": hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest(),
                                "locator": "independent synthetic observation", "facts": [observation]})
    spec["from"]["source_ids"] = ["source:bottom"]
    spec["to"]["source_ids"] = ["source:top"]
    conversion = spec["from"]["centerline_conversion"]
    conversion["outer_diameter"]["source_ids"] = ["source:OD-from"]
    conversion["source_ids"] = ["source:" + conversion["conversion_id"]]
    spec["route"]["source_ids"] = ["source:route-shape"]
    first = evaluate_elevation_link(spec)
    previous = copy.deepcopy(first["calculation_run"])
    # A previous 'current' is inherited only with verifiable human evidence (see test_approval_evidence.py).
    reviewed = [node for node in previous["nodes"] if node["value"] is not None]
    refs = inherited_refs(tmp_path, write_review(tmp_path, previous["project_id"], reviewed))
    for node in reviewed:
        node["review_state"] = "current"
        node["review_evidence"] = refs[node["id"]]
    conversion["outer_diameter"]["value"] = 168.3
    od_source = next(s for s in spec["sources"] if s["id"] == "source:OD-from")
    od_source["facts"][0]["value"] = 168.3
    od_source["sha256"] = hashlib.sha256(json.dumps(od_source["facts"], sort_keys=True).encode()).hexdigest()
    second = evaluate_elevation_link(spec, previous_calculation=previous)
    run = second["calculation_run"]
    assert "route_length" in run["stale_reviews"]
    assert "fact:OD-from" in run["affected_nodes"]
    assert "fact:top" not in run["affected_nodes"]
    assert "fact:bottom" not in run["affected_nodes"]
    assert second["value"] == pytest.approx(4.11585)
    assert run["business_adoption_allowed"] is False


def test_negative_absolute_elevations_are_valid_signed_intermediates():
    spec = base()
    spec["from"]["value"] = -5
    spec["to"]["value"] = -2
    out = evaluate_elevation_link(register(spec))
    assert out["value"] == 3
    assert out["calculation_run"]["errors"] == []


@pytest.mark.parametrize("field,changed", [("datum_id", "wrong-zero"), ("reference_id", "wrong-port"), ("system", "return")])
def test_source_endpoint_identity_cannot_be_rewritten(field, changed):
    spec = base()
    spec["to"][field] = changed
    with pytest.raises(ValueError):
        evaluate_elevation_link(spec)


def test_datum_mismatch_cannot_be_hidden_by_changing_reference():
    spec = base()
    spec["to"]["datum_id"] = "other-building-zero"
    register(spec)
    assert evaluate_elevation_link(spec)["value"] is None
    spec["to"]["datum_id"] = "building-zero"
    spec["to"]["reference_id"] = "made-up-port"
    with pytest.raises(ValueError, match="来源事实"):
        evaluate_elevation_link(spec)


@pytest.mark.parametrize("mutation", ["conversion-target", "conversion-operand", "datum-direction", "route-kind", "level-upgrade"])
def test_bound_semantics_cannot_change_without_source_change(mutation):
    spec = outer(base())
    if mutation == "conversion-target":
        spec["from"]["centerline_conversion"]["target_reference_id"] = "wrong-pipe"
    elif mutation == "conversion-operand":
        spec["from"]["centerline_conversion"]["outer_diameter"]["fact_id"] = "top"
    elif mutation == "datum-direction":
        spec["datum_transforms"] = [transform("datum", "building-zero", "site-zero", 2)]
        register(spec)
        spec["datum_transforms"][0].update(from_datum="site-zero", to_datum="building-zero")
    elif mutation == "route-kind":
        spec["route"]["kind"] = "straight"
    else:
        spec["from"]["level"] = "scenario"
        register(spec)
        spec["from"]["level"] = "confirmed"
    with pytest.raises(ValueError):
        evaluate_elevation_link(spec)


def test_missing_port_elevation_is_null_not_fabricated_or_zero():
    spec = base()
    spec["from"].update(value=3.2, level="scenario")
    spec["to"].update(value=None, status="unresolved", issue="Actual device port height is absent", measurement_kind="equipment_port_center")
    spec["route"] = {"kind": "unresolved"}
    out = evaluate_elevation_link(register(spec))
    assert out["value"] is None and out["vertical_projection"] is None
    assert out["endpoints"][0]["normalized_value_m"] == 3.2
    assert any(i["type"] == "接口高程未给出" for i in out["issues"])
    assert out["calculation_run"]["status"] == "REVIEW_REQUIRED"
    assert out["calculation_run"]["errors"] == []


def network(spec=None):
    rows = [{"segment_id": s, "system": "supply", "space_id": s, "start": [x, 0, 0], "end": [x+2, 0, 0],
             "source_ids": ["drawing:"+s], "dn_seeds": []} for s, x in [("floor-a", 0), ("floor-b", 10000)]]
    link = {"link_id": "across-floors", "physical_id": "riser", "a": {"segment_id": "floor-a", "end": "end"},
            "b": {"segment_id": "floor-b", "end": "start"}, "source_ids": ["system-diagram"], "basis": "same physical riser"}
    if spec is not None:
        link["elevation_facts"] = spec
    return {"unit": "m", "segments": rows, "cross_view_links": [link]}


def test_topology_uses_typed_facts_not_page_coordinate_gap():
    _, report = prepare_network(network(base()))
    assert report["source_length"] == 4
    assert report["vertical_quantities"][0]["value"] == pytest.approx(4.2)
    assert report["vertical_quantities"][0]["elevation_calculation"]["status"] == "RESOLVED"


def test_typed_unknown_does_not_fall_back_to_legacy_number():
    spec = base()
    spec.pop("route")
    data = network(spec)
    data["cross_view_links"][0].update(route_is_vertical=True, from_elevation=0, to_elevation=99,
        elevation_unit="m", height_source_ids=["manual-total"], height_level="confirmed")
    _, report = prepare_network(data)
    assert report["vertical_quantities"][0]["value"] is None
    assert report["issues"]


def test_topology_rejects_typed_system_mismatch():
    spec = base()
    spec["system"] = "return"
    with pytest.raises(ValueError, match="系统"):
        prepare_network(network(spec))


def test_existing_physical_owner_remains_unique():
    data = network(base())
    data["segments"].append({"segment_id": "owner", "physical_id": "riser", "space_id": "section", "system": "supply",
                             "start": [0, 0, 3], "end": [0, 0, 7.2], "source_ids": ["section:riser"], "dn_seeds": []})
    data["cross_view_links"][0]["quantity_owner_segment_id"] = "owner"
    _, report = prepare_network(data)
    q = report["vertical_quantities"][0]
    assert q["value"] is None and q["counted_elsewhere"] is True
    assert report["source_length"] == pytest.approx(8.2)


def test_reverse_alias_link_cannot_duplicate_same_physical_ports():
    data = network(base())
    duplicate = copy.deepcopy(data["cross_view_links"][0])
    duplicate.update(link_id="another-link-name", physical_id="another-physical-name")
    duplicate["a"], duplicate["b"] = duplicate["b"], duplicate["a"]
    data["cross_view_links"].append(duplicate)
    with pytest.raises(ValueError, match="端口对被重复"):
        prepare_network(data)


def test_declared_cross_view_system_must_match_ports():
    data = network(base())
    data["cross_view_links"][0]["system"] = "return"
    with pytest.raises(ValueError, match="声明系统"):
        prepare_network(data)


def test_same_physical_name_cannot_borrow_quantity_from_other_system():
    data = network(base())
    data["segments"].append({"segment_id": "other-owner", "physical_id": "riser", "space_id": "section", "system": "return",
                             "start": [0, 0, 3], "end": [0, 0, 7.2], "source_ids": ["section:riser"], "dn_seeds": []})
    data["cross_view_links"][0]["quantity_owner_segment_id"] = "other-owner"
    with pytest.raises(ValueError, match="不同系统"):
        prepare_network(data)


def test_legacy_elevation_input_stays_compatible():
    data = network()
    data["cross_view_links"][0].update(route_is_vertical=True, from_elevation=3, to_elevation=7.2,
        elevation_unit="m", height_source_ids=["section:height"], height_level="confirmed")
    assert prepare_network(data)[1]["vertical_quantities"][0]["value"] == pytest.approx(4.2)


def test_cli_recomputes_and_refuses_to_overwrite(tmp_path):
    source, output = tmp_path / "标高输入.json", tmp_path / "标高计算.json"
    source.write_text(json.dumps(base()), encoding="utf-8")
    command = [sys.executable, "-B", str(SHARED / "qto_elevation_facts.py"), "--input", str(source), "--output", str(output)]
    p = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["value"] == pytest.approx(4.2)
    old = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert output.read_bytes() == old
    command[-1] = str(source)
    before = source.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert source.read_bytes() == before


def test_full_engine_cli_consumes_typed_result(tmp_path):
    data = network(base())
    data.update(schema_version="qto-pipe-network-inference-input/1.2", network_id="typed-elevation-test",
        object_selection={"audit_id": "select", "status": "READY", "candidate_count": 2, "included_count": 2,
                          "excluded_count": 0, "unresolved_count": 0, "included_segment_ids": ["floor-a", "floor-b"], "source_ids": ["scope"]},
        endpoint_handoff={"handoff_id": "ports", "required": False, "source_instance_count": 0, "delivered_count": 0,
                          "not_applicable_count": 0, "unresolved_count": 0, "source_id": "scope", "reason": "synthetic riser"})
    source, output = tmp_path / "网络输入.json", tmp_path / "网络结果.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(ROOT / "skills/qto-pipe-networks/scripts/infer_pipe_network.py"),
                        "--input", str(source), "--output", str(output)], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    out = json.loads(output.read_text(encoding="utf-8"))
    assert out["vertical_quantities"][0]["value"] == pytest.approx(4.2)
    assert out["business_adoption_allowed"] is False
