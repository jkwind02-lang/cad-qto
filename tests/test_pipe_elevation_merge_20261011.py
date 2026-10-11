"""Synthetic arithmetic/ownership oracles, not project recognition accuracy."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/qto-pipe-networks/scripts"))
sys.path.insert(0, str(Path(__file__).parent))
from merge_pipe_elevation_segments import merge, network_items, file_sha, markdown, SCHEMA, BASIS
from test_typed_elevations_20261011 import base, register


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def setup(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("synthetic independent section 3m to 7.2m", encoding="utf-8")
    facts = base()
    facts["sources"][0].update(path=str(source), sha256=file_sha(source))
    network = {"evidence_version": "qto-pipe-network-inference-evidence/1.2", "network_id": "N", "unit": "m",
        "segments": [{"segment_id": "A", "system": "supply", "dn": "DN20", "length": 10., "unit": "m", "quantity_level": "确认量", "evidence_level": "confirmed"}],
        "valves": [{"valve_id": "V", "system": "supply", "dn": "DN20", "dn_evidence_level": "confirmed"}],
        "bridge_quantity_items": [], "vertical_quantities": []}
    path = tmp_path / "network.json"
    write(path, network)
    ev = {"source_ids": ["section"], "basis": "synthetic sourced interpretation", "level": "confirmed"}
    row = {"record_id": "NEW", "physical_id": "physical-1", "extent_id": "extent-1", "system": "supply",
        "specification": {"dn": "DN20"}, "unit": "m", "measurement_basis": BASIS, "elevation_facts": facts, **ev,
        "endpoint_bindings": [{"port_id": "port:bottom", "network_record_id": "A", **ev}, {"port_id": "port:top", "network_record_id": "V", **ev}],
        "network_coverage": {"disjoint_record_ids": ["segment:A"], "same_extent_record_ids": [], "partial_overlap_record_ids": [], "unknown_record_ids": [], **ev}}
    data = {"schema": SCHEMA, "scope_id": "test", "scope_basis": "synthetic finite branch", "network_id": "N", "network_result": {"path": str(path), "sha256": file_sha(path)}, "segments": [row]}
    return data, network


def save_network(data, network):
    p = Path(data["network_result"]["path"])
    write(p, network)
    data["network_result"]["sha256"] = file_sha(p)


def rebind_source(row):
    source = row["elevation_facts"]["sources"][0]
    path, sha = source["path"], source["sha256"]
    register(row["elevation_facts"])
    row["elevation_facts"]["sources"][0].update(path=path, sha256=sha)


def test_independent_quantity_preserves_network_and_layers(tmp_path):
    p, n = setup(tmp_path)
    original = copy.deepcopy(p)
    out = merge(p)
    assert out["elevation_segments"][0]["quantity"] == pytest.approx(4.2)
    assert out["length_summary"][0]["quantity"] == pytest.approx(14.2)
    assert out["network_result"] == n and p == original
    assert out["scope_complete"] is False and out["formal_adoption_allowed"] is False
    assert "原已计管网小计" in markdown(out)


@pytest.mark.parametrize("place", ["from", "to", "route"])
def test_scenario_propagates_never_promoted(tmp_path, place):
    p, _ = setup(tmp_path)
    r = p["segments"][0]
    r["elevation_facts"][place]["level"] = "scenario"
    rebind_source(r)
    out = merge(p)
    assert out["elevation_segments"][0]["quantity_level"] == "方案暂估量"
    assert len(out["length_summary"]) == 2


@pytest.mark.parametrize("mode", ["missing_height", "missing_conversion", "different_datum", "missing_route"])
def test_missing_facts_are_null_not_zero(tmp_path, mode):
    p, _ = setup(tmp_path)
    r = p["segments"][0]
    facts = r["elevation_facts"]
    if mode == "missing_height":
        facts["to"].update(value=None, status="unresolved", issue="missing height")
    elif mode == "missing_conversion":
        facts["to"]["measurement_kind"] = "other_reference"
    elif mode == "different_datum":
        facts["to"]["datum_id"] = "different-floor-zero"
    else:
        facts["route"] = {"kind": "unresolved"}
    rebind_source(r)
    out = merge(p)
    assert out["elevation_segments"][0]["quantity"] is None
    assert out["added_length_summary"][0]["quantity"] is None
    assert out["unresolved_record_ids"] == ["NEW"]


@pytest.mark.parametrize("group", ["unknown_record_ids", "partial_overlap_record_ids"])
def test_unknown_or_partial_overlap_never_added(tmp_path, group):
    p, _ = setup(tmp_path)
    c = p["segments"][0]["network_coverage"]
    c["disjoint_record_ids"] = []
    c[group] = ["segment:A"]
    r = merge(p)["elevation_segments"][0]
    assert r["derived_length"] == pytest.approx(4.2) and r["quantity"] is None
    assert r["status"] == "coverage_unresolved"


def test_already_counted_extent_zero_remainder_without_double_count(tmp_path):
    p, n = setup(tmp_path)
    n["segments"][0].update(length=4200, unit="mm", physical_id="physical-1", extent_id="extent-1")
    save_network(p, n)
    p["segments"][0]["network_coverage"].update(disjoint_record_ids=[], same_extent_record_ids=["segment:A"])
    out = merge(p)
    r = out["elevation_segments"][0]
    assert r["status"] == "already_counted" and r["quantity"] == 0
    assert out["length_summary"][0]["quantity"] == pytest.approx(4.2)


def test_same_extent_conflict_does_not_fill_difference(tmp_path):
    p, n = setup(tmp_path)
    p["segments"][0]["network_coverage"].update(disjoint_record_ids=[], same_extent_record_ids=["segment:A"])
    r = merge(p)["elevation_segments"][0]
    assert r["quantity"] is None and r["status"] == "existing_quantity_conflict"
    assert r["issues"][-1]["existing_quantity_m"] == 10


@pytest.mark.parametrize("key", ["physical_id", "extent_id"])
def test_disjoint_cannot_hide_existing_identity(tmp_path, key):
    p, n = setup(tmp_path)
    n["segments"][0][key] = p["segments"][0][key]
    save_network(p, n)
    with pytest.raises(ValueError, match="已计"):
        merge(p)


@pytest.mark.parametrize("key,value", [("count", 4), ("quantity", 4.2), ("unit", "个"), ("measurement_basis", "net_pipe_length"), ("quantity_kind", "count")])
def test_wrong_quantity_field_unit_or_basis_rejected(tmp_path, key, value):
    p, _ = setup(tmp_path)
    p["segments"][0][key] = value
    with pytest.raises(ValueError):
        merge(p)


@pytest.mark.parametrize("mode", ["missing", "repeated", "nonexistent"])
def test_full_existing_quantity_audit_required(tmp_path, mode):
    p, _ = setup(tmp_path)
    c = p["segments"][0]["network_coverage"]
    c["disjoint_record_ids"] = {"missing": [], "repeated": ["segment:A", "segment:A"], "nonexistent": ["other"]}[mode]
    with pytest.raises(ValueError, match="覆盖审计"):
        merge(p)


def test_duplicate_extent_and_renamed_ports_rejected(tmp_path):
    p, _ = setup(tmp_path)
    new = copy.deepcopy(p["segments"][0])
    new.update(record_id="NEW2", physical_id="physical-2", extent_id="extent-2")
    p["segments"].append(new)
    with pytest.raises(ValueError, match="端口对重复"):
        merge(p)
    for side, binding in zip(("from", "to"), new["endpoint_bindings"]):
        new["elevation_facts"][side]["reference_id"] += "-renamed"
        binding["port_id"] += "-renamed"
    rebind_source(new)
    with pytest.raises(ValueError, match="原管网对象被重复关联"):
        merge(p)


def test_same_dn_different_material_never_coalesces(tmp_path):
    p, _ = setup(tmp_path)
    p["segments"][0]["specification"]["material"] = "copper"
    assert len(merge(p)["length_summary"]) == 2
    p["segments"][0]["network_coverage"].update(disjoint_record_ids=[], same_extent_record_ids=["segment:A"])
    with pytest.raises(ValueError, match="完整规格"):
        merge(p)


def test_existing_restoration_and_vertical_single_owner(tmp_path):
    p, n = setup(tmp_path)
    n["bridge_quantity_items"] = [{"bridge_id": "B", "system": "supply", "dn": "DN20", "length": .5,
        "included_in_reconstructed": True, "action": "restore_omission", "quantity_level": "确认量"},
        {"bridge_id": "excluded", "length": 99, "included_in_reconstructed": False, "action": "exclude"}]
    n["vertical_quantities"] = [{"link_id": "old", "system": "supply", "dn": "DN20", "value": 2,
        "unit": "m", "quantity_level": "确认量", "counted_elsewhere": False},
        {"link_id": "duplicate-view", "value": 2, "counted_elsewhere": True}]
    save_network(p, n)
    p["segments"][0]["network_coverage"]["disjoint_record_ids"] = [r["record_id"] for r in network_items(n)]
    out = merge(p)
    assert out["length_summary"][0]["quantity"] == pytest.approx(16.7)
    assert len(out["base_length_items"]) == 3


def test_stale_source_and_network_rejected(tmp_path):
    p, _ = setup(tmp_path)
    Path(p["segments"][0]["elevation_facts"]["sources"][0]["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希发生变化"):
        merge(p)
    Path(p["network_result"]["path"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA变化"):
        merge(p)


def test_same_reference_different_numeric_facts_rejected(tmp_path):
    p, n = setup(tmp_path)
    new = copy.deepcopy(p["segments"][0])
    new.update(record_id="NEW2", physical_id="physical-2", extent_id="extent-2")
    new["elevation_facts"]["from"].update(fact_id="different-fact-id", value=4)
    rebind_source(new)
    p["segments"].append(new)
    with pytest.raises(ValueError, match="互相矛盾"):
        merge(p)


def test_endpoint_wrong_system_and_dn_rejected(tmp_path):
    p, n = setup(tmp_path)
    n["valves"][0]["system"] = "return"
    save_network(p, n)
    with pytest.raises(ValueError, match="同系统"):
        merge(p)
    n["valves"][0].update(system="supply", dn="DN25")
    save_network(p, n)
    with pytest.raises(ValueError, match="端口DN"):
        merge(p)


def second_branch(p, n):
    new = copy.deepcopy(p["segments"][0])
    new.update(record_id="NEW2", physical_id="physical-2", extent_id="extent-2")
    new["elevation_facts"]["to"].update(reference_id="port:other-top", fact_id="other-top", value=1)
    new["endpoint_bindings"][1].update(port_id="port:other-top", network_record_id="V2")
    n["valves"].append({**n["valves"][0], "valve_id": "V2"})
    save_network(p, n)
    p["segments"].append(new)
    return new


def test_same_normalized_port_via_different_measurement_kind_conflict(tmp_path):
    p, n = setup(tmp_path)
    new = second_branch(p, n)
    ev = {"source_ids": ["section"], "basis": "synthetic floor offset", "level": "confirmed"}
    new["elevation_facts"]["from"].update(reference_id="floor", fact_id="floor", value=0, measurement_kind="finished_floor",
        centerline_conversion={"conversion_id": "floor-center", "method": "signed_offset", "target_reference_id": "port:bottom", **ev,
            "offset": {"fact_id": "offset", "value": 4, "unit": "m", **ev}})
    rebind_source(new)
    with pytest.raises(ValueError, match="归一.*矛盾"):
        merge(p)


@pytest.mark.parametrize("offset,expected", [(-100, "match"), (-99, "conflict"), (None, "unresolved")])
def test_shared_port_across_target_datums(tmp_path, offset, expected):
    p, n = setup(tmp_path)
    new = second_branch(p, n)
    for side in ("from", "to"):
        new["elevation_facts"][side]["datum_id"] = "survey"
        new["elevation_facts"][side]["value"] += 100
        new["elevation_facts"][side]["fact_id"] += "-survey"
    if offset is not None:
        new["elevation_facts"]["datum_transforms"] = [{"transform_id": "survey-building", "from_datum": "survey", "to_datum": "building-zero", "value": offset, "unit": "m", "source_ids": ["section"], "basis": "synthetic datum tie", "level": "confirmed"}]
    rebind_source(new)
    if expected == "conflict":
        with pytest.raises(ValueError, match="归一.*矛盾"):
            merge(p)
    else:
        rows = merge(p)["elevation_segments"]
        if expected == "match":
            assert [r["quantity"] for r in rows] == pytest.approx([4.2, 2.0])
        else:
            assert [r["quantity"] for r in rows] == [None, None]


def test_same_extent_different_identity_needs_sourced_alias(tmp_path):
    p, n = setup(tmp_path)
    n["segments"][0].update(length=4.2, physical_id="other-physical", extent_id="other-extent")
    save_network(p, n)
    r = p["segments"][0]
    c = r["network_coverage"]
    c.update(disjoint_record_ids=[], same_extent_record_ids=["segment:A"])
    with pytest.raises(ValueError, match="不能冒领"):
        merge(p)
    ev = {"source_ids": ["section"], "basis": "synthetic proved alias", "level": "inferred"}
    c["identity_aliases"] = [{"existing_record_id": "segment:A", "alias_fact_id": "alias", **ev}]
    fact = {"id": "alias", "measurement_kind": "physical_extent_identity", "system": "supply", "existing_record_id": "segment:A",
        "existing_physical_id": "other-physical", "existing_extent_id": "other-extent", "new_physical_id": r["physical_id"],
        "new_extent_id": r["extent_id"], "from_port_id": "port:bottom", "to_port_id": "port:top", "level": "inferred"}
    r["elevation_facts"]["sources"][0]["facts"].append(fact)
    assert merge(p)["elevation_segments"][0]["quantity"] == 0
    fact["new_extent_id"] = "wrong"
    with pytest.raises(ValueError, match="来源事实身份不一致"):
        merge(p)


def test_new_vertical_overlap_requires_shared_run_partition(tmp_path):
    p, n = setup(tmp_path)
    new = second_branch(p, n)
    new["elevation_facts"]["to"]["value"] = 5
    rebind_source(new)
    with pytest.raises(ValueError, match="同向部分重叠"):
        merge(p)


def test_old_vertical_same_port_pair_cannot_rename_and_add(tmp_path):
    from qto_elevation_facts import evaluate_elevation_link
    p, n = setup(tmp_path)
    n["vertical_quantities"] = [{"link_id": "old", "physical_id": "old-name", "system": "supply", "dn": "DN20",
        "value": 4.2, "unit": "m", "quantity_level": "确认量", "counted_elsewhere": False,
        "elevation_calculation": evaluate_elevation_link(p["segments"][0]["elevation_facts"])}]
    save_network(p, n)
    p["segments"][0]["network_coverage"]["disjoint_record_ids"].append("vertical:old")
    with pytest.raises(ValueError, match="原量已有同一物理端口对"):
        merge(p)


def test_same_network_object_distinct_ports_need_binding_facts(tmp_path):
    p, _ = setup(tmp_path)
    r = p["segments"][0]
    r["endpoint_bindings"][1]["network_record_id"] = "A"
    result = merge(p)
    assert result["elevation_segments"][0]["quantity"] is None
    assert result["elevation_segments"][0]["status"] == "endpoint_unresolved"
    for index, b in enumerate(r["endpoint_bindings"]):
        b.update(port_binding_fact_id="binding" + str(index), object_port_key=["start", "end"][index])
        r["elevation_facts"]["sources"][0]["facts"].append({"id": b["port_binding_fact_id"], "measurement_kind": "network_port_binding",
            "system": "supply", "network_record_id": "A", "port_id": b["port_id"], "object_port_key": b["object_port_key"], "level": "confirmed"})
    assert merge(p)["elevation_segments"][0]["quantity"] == pytest.approx(4.2)
    r["endpoint_bindings"][0]["object_port_key"] = "changed-without-source"
    with pytest.raises(ValueError, match="来源事实身份不一致"):
        merge(p)


def test_same_extent_equal_length_but_shifted_elevations_conflicts(tmp_path):
    from qto_elevation_facts import evaluate_elevation_link
    p, n = setup(tmp_path)
    original = copy.deepcopy(p["segments"][0]["elevation_facts"])
    n["vertical_quantities"] = [{"link_id": "old", "physical_id": "physical-1", "extent_id": "extent-1", "system": "supply", "dn": "DN20",
        "value": 4.2, "unit": "m", "quantity_level": "确认量", "counted_elsewhere": False, "elevation_calculation": evaluate_elevation_link(original)}]
    save_network(p, n)
    r = p["segments"][0]
    r["network_coverage"]["same_extent_record_ids"] = ["vertical:old"]
    for side in ("from", "to"):
        r["elevation_facts"][side]["value"] += 1
    rebind_source(r)
    with pytest.raises(ValueError, match="归一.*矛盾"):
        merge(p)
