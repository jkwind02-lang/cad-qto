"""Independent quantity-path regressions, including real producer/consumer joins.

The finite geometry and pipe/count expectations are synthetic engineering
oracles. They do not assert recognition accuracy on unseen construction drawings.
"""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/_qto-shared-evidence/scripts"))
sys.path.insert(0, str(ROOT / "skills/qto-pipe-networks/scripts"))
from qto_pipe_quantity_contract import valve_quantity_level, validate_valve_summary
from merge_pipe_elevation_segments import merge as merge_elevations, network_items
from merge_pipe_instance_requirements import merge as merge_requirements


def seed(dn, level="confirmed"):
    return {"dn": dn, "evidence_level": level, "source_id": "drawing-label:" + dn}


def pipe(sid, a, b, dn=None, **extra):
    return {"segment_id": sid, "system": "water", "space_id": "plan", "start": list(a), "end": list(b),
            "length": math.dist(a, b), "source_ids": ["geometry:" + sid],
            "dn_seeds": [seed(dn)] if dn else [], **extra}


def network(rows, terminals=None):
    terminals = terminals or []
    return {"schema_version": "qto-pipe-network-inference-input/1.2", "network_id": "audit-regression", "unit": "m",
            "segments": rows, "terminals": terminals, "parameters": {"snap_tolerance": .001, "gap_max": .2},
            "object_selection": {"audit_id": "scope", "status": "READY", "candidate_count": len(rows),
                "included_count": len(rows), "excluded_count": 0, "unresolved_count": 0,
                "included_segment_ids": [r["segment_id"] for r in rows], "source_ids": ["scope-review"]},
            "endpoint_handoff": {"handoff_id": "ports", "required": bool(terminals), "source_instance_count": len(terminals),
                "delivered_count": len(terminals), "not_applicable_count": 0, "unresolved_count": 0,
                "source_id": "port-review", "reason": "finite synthetic system"}}


def valve(**extra):
    return {"valve_id": "V", "system": "water", "space_id": "plan", "point": [4, 0, 0],
            "valve_type": "isolation valve", "direct_dn": "DN32", "dn_evidence_level": "confirmed",
            "instance_level": "confirmed", "source_id": "drawing-A", "source_object_id": "CAD-AB12", **extra}


def run(tmp_path, data, reject=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    src, dest = tmp_path / "input.json", tmp_path / "output.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "skills/qto-pipe-networks/scripts/infer_pipe_network.py"),
                           "--input", str(src), "--output", str(dest)], capture_output=True, text=True, encoding="utf-8")
    if reject:
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert reject in proc.stderr
        assert not dest.exists()
        return None
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(dest.read_text(encoding="utf-8"))
    assert out["business_adoption_allowed"] is False
    assert validate_valve_summary(out) == []
    return out


@pytest.mark.parametrize("level,expected", [("confirmed", "确认量"), ("inferred", "规则推算量"), ("scenario", "方案暂估量")])
def test_valve_count_cannot_be_stronger_than_instance_existence(tmp_path, level, expected):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(instance_level=level)]
    out = run(tmp_path, data)
    assert out["valves"][0]["dn_quantity_level"] == "确认量"
    assert out["valves"][0]["quantity_level"] == expected
    assert out["valve_summary"][0]["quantity_level"] == expected
    assert out["valve_summary"][0]["instance_count"] == 1


@pytest.mark.parametrize("field", ["scope_evidence_level", "type_evidence_level"])
@pytest.mark.parametrize("value,expected", [("scenario", "方案暂估量"), ("inferred", "规则推算量"), (None, "未决量")])
def test_explicit_scope_or_attribute_uncertainty_limits_count(tmp_path, field, value, expected):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(**{field: value})]
    out = run(tmp_path, data)
    assert out["valve_summary"][0]["quantity_level"] == expected


def test_valve_dn_uncertainty_is_not_lost_in_combined_level(tmp_path):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(dn_evidence_level="scenario")]
    out = run(tmp_path, data)
    assert out["valves"][0]["instance_level"] == "confirmed"
    assert out["valve_summary"][0]["quantity_level"] == "方案暂估量"


def test_unknown_type_preserves_candidate_count_without_confirming_classification(tmp_path):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(valve_type=None)]
    out = run(tmp_path, data)
    assert out["valves"][0]["instance_level"] == "confirmed"
    assert out["valve_summary"][0]["quantity_level"] == "未决量"
    assert out["valve_summary"][0]["instance_count"] == 1


def test_unknown_or_invalid_explicit_quantity_evidence_is_not_default_confirmed():
    old = {"valve_type": "isolation valve", "dn": "DN32", "dn_quantity_level": "确认量"}
    assert valve_quantity_level(old) == "确认量"  # compatibility with the existing omitted-instance default
    assert valve_quantity_level(dict(old, instance_level=None)) == "未决量"
    assert valve_quantity_level(dict(old, dn_evidence_level=None)) == "未决量"
    with pytest.raises(ValueError, match="层级"):
        valve_quantity_level(dict(old, scope_evidence_level="unverified-but-confirmed"))


@pytest.mark.parametrize("same_hash", [False, True])
def test_duplicate_source_valve_cannot_be_renamed(tmp_path, same_hash):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    a, b = valve(valve_id="V1"), valve(valve_id="V2")
    if same_hash:
        a["source_sha256"] = b["source_sha256"] = "a" * 64
        b["source_id"] = "second-evidence-label-for-the-same-drawing"
    data["valves"] = [a, b]
    run(tmp_path, data, reject="同一来源实例")


@pytest.mark.parametrize("first_identity,second_identity", [
    ({"source_sha256": "a" * 64}, {"source_sha256": "A" * 64}),
    ({"source_sha256": "a" * 64}, {"source_sha256": "  " + "a" * 64 + "  "}),
    ({}, {"source_id": " drawing-A "}),
    ({}, {"source_object_id": " CAD-AB12 "}),
    ({"instance_path": "INSERT-I1"}, {"instance_path": " INSERT-I1 "}),
    ({"source_object_id": "CADINST-I1/CAD-AB12", "instance_path": "I1"},
     {"source_object_id": " CADINST-I1/CAD-AB12 ", "instance_path": "I2"}),
], ids=["hash_case", "hash_whitespace", "source_whitespace", "object_whitespace", "path_whitespace", "expanded_id_whitespace"])
def test_source_identity_normalization_precedes_inference_deduplication(tmp_path, first_identity, second_identity):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(valve_id="V1", **first_identity), valve(valve_id="V2", **second_identity)]
    run(tmp_path, data, reject="同一来源实例")


@pytest.mark.parametrize("field,first_value,second_value", [
    ("source_id", "drawing-A", "drawing-a"),
    ("source_object_id", "valve-A", "valve-a"),
    ("instance_path", "INSERT-A", "insert-a"),
])
def test_nonhash_source_and_instance_identifiers_keep_case(tmp_path, field, first_value, second_value):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    a, b = valve(valve_id="V1", point=[2,0,0]), valve(valve_id="V2", point=[6,0,0])
    a[field], b[field] = first_value, second_value
    data["valves"] = [a, b]
    out = run(tmp_path, data)
    assert out["metrics"]["valve_count"] == 2
    assert sum(r["instance_count"] for r in out["valve_summary"]) == 2


def test_duplicate_expanded_instance_cannot_be_renamed_by_a_redundant_path(tmp_path):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(valve_id="V1", source_object_id="CADINST-I1/CAD-AB12", instance_path="I1"),
                      valve(valve_id="V2", source_object_id="CADINST-I1/CAD-AB12", instance_path="I2")]
    run(tmp_path, data, reject="同一来源实例")


def test_stored_summary_rejects_duplicate_expanded_instance_despite_redundant_paths(tmp_path):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    data["valves"] = [valve(valve_id="V1", source_object_id="CADINST-I1/CAD-AB12", instance_path="I1"),
                      valve(valve_id="V2", source_object_id="CADINST-I2/CAD-AB12", instance_path="I2")]
    out = run(tmp_path, data)
    out["valves"][1]["source_object_id"] = out["valves"][0]["source_object_id"]
    assert any("源实例重复计数" in error for error in validate_valve_summary(out))


@pytest.mark.parametrize("identity_mode", ["cadinst", "explicit_instance_path", "different_drawings"])
def test_distinct_insertions_or_drawings_are_not_deduplicated(tmp_path, identity_mode):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")])
    a, b = valve(valve_id="V1", point=[2,0,0]), valve(valve_id="V2", point=[6,0,0])
    a["block_family"] = b["block_family"] = "same-valve-definition"
    if identity_mode == "cadinst":
        a["source_object_id"], b["source_object_id"] = "CADINST-I1/CAD-AB12", "CADINST-I2/CAD-AB12"
    elif identity_mode == "explicit_instance_path":
        a["instance_path"], b["instance_path"] = " INSERT-I1 ", " INSERT-I2 "
    else:
        a["source_sha256"], b["source_sha256"] = "a" * 64, " " + "B" * 64 + " "
    data["valves"] = [a, b]
    out = run(tmp_path, data)
    assert out["metrics"]["valve_count"] == 2
    assert sum(r["instance_count"] for r in out["valve_summary"]) == 2


def test_direct_dn_survives_a_lower_strength_terminal_candidate(tmp_path):
    terminal = {"terminal_id": "basin", "system": "water", "space_id": "plan", "point": [8,0,0],
                "terminal_type": "washbasin", "evidence_level": "confirmed", "source_id": "fixture-symbol",
                "port_dn": "DN15", "port_evidence_level": "scenario", "port_source_id": "conventional-minimum",
                "assumption_id": "A-minimum-port"}
    out = run(tmp_path, network([pipe("P", (0,0,0), (8,0,0), "DN32")], [terminal]))
    assert out["length_summary"]["water"] == {"确认量": {"DN32": 8}}
    rejected = out["dn_seed_rejections"]
    assert len(rejected) == 1 and rejected[0]["candidate"]["dn"] == "DN15"
    assert rejected[0]["candidate"]["source_id"] == "conventional-minimum"
    assert out["terminals"][0]["port_dn"] == "DN15"


def test_equal_direct_conflicts_stay_unresolved(tmp_path):
    row = pipe("P", (0,0,0), (8,0,0), dn_seeds=[seed("DN32"), seed("DN15")])
    out = run(tmp_path, network([row]))
    assert out["segments"][0]["dn"] is None
    assert out["segments"][0]["alternatives"] == ["DN15", "DN32"]
    assert out["dn_seed_rejections"] == []


def test_weaker_conflict_on_one_segment_does_not_hide_two_real_direct_dns(tmp_path):
    rows = [pipe("A", (0,0,0), (3,0,0), dn_seeds=[seed("DN50"), seed("DN15", "scenario")]),
            pipe("B", (3,0,0), (8,0,0), "DN25")]
    out = run(tmp_path, network(rows))
    assert {r["segment_id"]: r["dn"] for r in out["segments"]} == {"A": "DN50", "B": "DN25"}
    assert out["runs"][0]["status"] == "conflict"
    assert len(out["dn_seed_rejections"]) == 1


def test_direct_seed_priority_never_crosses_a_declared_change_boundary(tmp_path):
    data = network([pipe("A", (0,0,0), (3,0,0), "DN32"),
                    pipe("B", (3,0,0), (8,0,0), dn_seeds=[seed("DN15", "scenario")])])
    data["change_nodes"] = [{"change_id": "reducer", "system": "water", "point": [3,0,0],
                              "source_id": "reducer-detail", "evidence_level": "confirmed"}]
    out = run(tmp_path, data)
    assert out["length_summary"]["water"] == {"确认量": {"DN32": 3}, "方案暂估量": {"DN15": 5}}
    assert out["dn_seed_rejections"] == []


@pytest.mark.parametrize("downstream_dn,expected", [(None, None), ("DN50", "DN50"), ("DN25", None)])
def test_valve_inheritance_requires_compatible_known_connections(tmp_path, downstream_dn, expected):
    data = network([pipe("UP", (0,0,0), (3,0,0), "DN50"), pipe("DOWN", (3,0,0), (8,0,0), downstream_dn)])
    data["change_nodes"] = [{"change_id": "assembly-boundary", "system": "water", "point": [3,0,0],
                              "source_id": "valve-reducer-detail", "evidence_level": "confirmed"}]
    item = valve(point=[3,0,0], connected_segment_ids=["UP", "DOWN"], connection_source_id="assembly-detail")
    item.pop("direct_dn")
    data["valves"] = [item]
    out = run(tmp_path, data)
    assert out["valves"][0]["dn"] == expected
    if expected is None:
        assert out["valves"][0]["quantity_level"] == "未决量"


def produced_elevation_fixture(tmp_path):
    from test_pipe_elevation_merge_20261011 import setup, save_network
    data, _ = setup(tmp_path)
    rows = [pipe("A", (0,0,3), (1,0,3), "DN20", system="supply"),
            pipe("RISER", (1,0,3), (1,0,7.2), "DN20", system="supply", physical_id="physical-1", extent_id="extent-1"),
            pipe("HIGH", (1,0,7.2), (2,0,7.2), "DN20", system="supply")]
    raw = network(rows)
    raw["valves"] = [valve(system="supply", point=[1,0,7.2], direct_dn="DN20")]
    out = run(tmp_path / "producer", raw)
    data["network_id"] = out["network_id"]
    save_network(data, out)
    data["segments"][0]["network_coverage"]["disjoint_record_ids"] = [r["record_id"] for r in network_items(out)]
    return data, out


def test_real_inference_output_preserves_identity_and_blocks_false_disjoint_merge(tmp_path):
    data, out = produced_elevation_fixture(tmp_path)
    riser = next(r for r in out["segments"] if r["segment_id"] == "RISER")
    assert (riser["physical_id"], riser["extent_id"]) == ("physical-1", "extent-1")
    assert out["metrics"]["raw_length"] == pytest.approx(6.2)
    with pytest.raises(ValueError, match="身份已计"):
        merge_elevations(data)


def test_real_inference_output_still_allows_proven_same_extent_zero_remainder(tmp_path):
    data, _ = produced_elevation_fixture(tmp_path)
    coverage = data["segments"][0]["network_coverage"]
    coverage["disjoint_record_ids"].remove("segment:RISER")
    coverage["same_extent_record_ids"] = ["segment:RISER"]
    result = merge_elevations(data)
    assert result["elevation_segments"][0]["quantity"] == 0
    assert sum(r["known_subtotal"] for r in result["length_summary"]) == pytest.approx(6.2)


def test_instance_requirement_merge_recomputes_existing_valve_quantity_level(tmp_path):
    from test_instance_requirements_20261011 import merge_fixture, sha
    data, _ = merge_fixture(tmp_path)
    path = Path(data["network_result"]["path"])
    net = json.loads(path.read_text(encoding="utf-8"))
    net["valves"][0].update(instance_level="scenario", quantity_level="确认量")
    path.write_text(json.dumps(net), encoding="utf-8")
    data["network_result"]["sha256"] = sha(path)
    result = merge_requirements(data)
    old = next(r for r in result["accessory_items"] if r["origin"] == "drawn_network_accessory")
    assert old["quantity"] == 1 and old["quantity_level"] == "方案暂估量"
    assert old["status"] == "determined"  # The DN is readable; this is not an adoption state.
    assert "不因determined升级为确认量" in old["status_meaning"]
    summary = [r for r in result["accessory_summary"] if r["origin"] == "drawn_network_accessory"]
    assert len(summary) == 1 and summary[0]["quantity_level"] == "方案暂估量"
    assert result["formal_adoption_allowed"] is False


@pytest.mark.parametrize("variant", ["hash_case", "object_whitespace"])
def test_instance_merge_rejects_equivalent_source_identity_spellings(tmp_path, variant):
    from test_instance_requirements_20261011 import merge_fixture, sha
    data, _ = merge_fixture(tmp_path)
    path = Path(data["network_result"]["path"])
    net = json.loads(path.read_text(encoding="utf-8"))
    ledger = json.loads(Path(net["recognition_basis"]["ledger_path"]).read_text(encoding="utf-8"))
    first = net["valves"][0]
    first.update(source_object_id=first["source_id"], source_sha256=ledger["source_sha256"].lower())
    duplicate = copy.deepcopy(first)
    duplicate["valve_id"] = "same-drawn-valve-renamed"
    if variant == "hash_case":
        duplicate["source_sha256"] = first["source_sha256"].upper()
        assert bytes.fromhex(duplicate["source_sha256"]) == bytes.fromhex(first["source_sha256"])
    else:
        duplicate["source_object_id"] = " " + first["source_object_id"] + " "
    net["valves"].append(duplicate)
    path.write_text(json.dumps(net), encoding="utf-8")
    data["network_result"]["sha256"] = sha(path)
    with pytest.raises(ValueError, match="重复来源实例"):
        merge_requirements(data)


@pytest.mark.parametrize("unit", ["m2", "kg", "Unitless"])
def test_direct_inference_rejects_nonlength_units(tmp_path, unit):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")]); data["unit"] = unit
    run(tmp_path, data, reject="物理长度单位")


@pytest.mark.parametrize("unit", ["m", "mm", "ft", "in", "Meters"])
def test_supported_length_units_keep_the_declared_coordinate_quantity(tmp_path, unit):
    data = network([pipe("P", (0,0,0), (8,0,0), "DN32")]); data["unit"] = unit
    out = run(tmp_path, data)
    assert out["unit"] == unit and out["metrics"]["raw_length"] == 8
