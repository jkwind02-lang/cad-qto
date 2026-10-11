"""Source-bound cross-view candidates with no quantity or implicit adoption."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/_qto-shared-evidence/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("correspondence_20261011", SCRIPTS / "qto_object_correspondence.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def case(tmp_path):
    sources = []
    for name in ("平面", "系统", "详图"):
        p = tmp_path / (name + ".txt")
        p.write_text(name + " 独立合成源；非项目实例", encoding="utf-8")
        sources.append({"source_id": name, "kind": "drawing", "path": str(p),
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    left = {"observation_id": "A", "source_id": "平面", "locator": "实体12/端口", "view_id": "一层平面",
            "kind": "port", "building": "甲楼", "floor": "一层", "system": "空调水", "object_tag": "R-01",
            "connection_role": "供水", "route_ref": {"segment_id": "SEG-A", "end": "end"}}
    right = dict(left, observation_id="B", source_id="系统", locator="实体37/端口", view_id="二层平面",
                 floor="二层", route_ref={"segment_id": "SEG-B", "end": "start"})
    return {"schema_version": module.SCHEMA, "sources": sources, "observations": [left, right],
            "requests": [{"request_id": "Q", "relation": "floor_continuation", "from_id": "A",
                          "target_ids": ["B"], "match_fields": ["object_tag"]}]}


def select(case, **extra):
    case["interpretations"] = [{"interpretation_id": "I", "request_id": "Q", "target_id": "B",
                                "decision_origin": "model", "basis": "上下行索引与系统图相互核对；只声明接口对应",
                                "source_refs": [{"observation_id": o["observation_id"], "source_id": o["source_id"],
                                                 "locator": o["locator"]} for o in case["observations"][:2]], **extra}]
    return case


def result(case):
    return module.resolve_correspondence(case)["requests"][0]


def test_unique_is_candidate_not_adopted(case):
    out = module.resolve_correspondence(case)
    assert out["requests"][0]["status"] == "unique_candidate"
    assert out["requests"][0]["adoption_scope"] == "candidate"
    assert out["requests"][0]["formal_adoption_allowed"] is False
    assert out["pairings"] == out["cross_view_links"] == []
    assert out["quantity_created"] is False


@pytest.mark.parametrize("field,value", [("building", "乙楼"), ("system", "消防水"), ("connection_role", "回水")])
def test_same_number_wrong_scope_or_role_is_conflict(case, field, value):
    case["observations"][1][field] = value
    row = result(case)
    assert row["status"] == "no_candidate"
    assert any(c.get("field") == field for c in row["evaluations"][0]["conflicts"])


def test_floor_continuation_does_not_require_equal_floor(case):
    assert result(case)["status"] == "unique_candidate"
    case["observations"][1]["floor"] = "一层"
    assert result(case)["status"] == "no_candidate"


def test_same_physical_same_tag_different_floor_does_not_match(case):
    case["requests"][0]["relation"] = "same_physical"
    assert result(case)["status"] == "no_candidate"
    case["observations"][1]["floor"] = "一层"
    assert result(case)["status"] == "unique_candidate"


def test_same_object_can_have_multiple_views(case):
    case["requests"][0]["relation"] = "same_physical"
    case["observations"][1]["floor"] = "一层"
    case["observations"].append(dict(case["observations"][1], observation_id="C", view_id="剖面", locator="实体45"))
    case["requests"][0]["target_ids"].append("C")
    select(case)
    second = copy.deepcopy(case["interpretations"][0])
    second.update(interpretation_id="I2", target_id="C")
    second["source_refs"][1] = {"observation_id": "C", "source_id": "系统", "locator": "实体45"}
    case["interpretations"].append(second)
    out = module.resolve_correspondence(case)
    assert out["requests"][0]["status"] == "multiple_candidates"
    assert len(out["pairings"]) == 2
    assert out["cross_view_links"] == []


def test_one_typical_detail_can_serve_multiple_instances(case):
    a, b = case["observations"]
    a.update(kind="instance", detail_index="D-7")
    b.update(kind="detail", detail_index="D-7", source_id="详图", view_id="典型详图")
    for f in ("building", "floor", "object_tag", "route_ref"):
        b.pop(f)
    other = dict(a, observation_id="C", object_tag="R-02", locator="实体22")
    case["observations"].append(other)
    case["requests"] = [{"request_id": "Q", "relation": "detail_applicability", "from_id": "A",
                         "target_ids": ["B"], "match_fields": ["detail_index"]},
                        {"request_id": "Q2", "relation": "detail_applicability", "from_id": "C",
                         "target_ids": ["B"], "match_fields": ["detail_index"]}]
    select(case)
    second = copy.deepcopy(case["interpretations"][0])
    second.update(interpretation_id="I2", request_id="Q2")
    second["source_refs"][0] = {"observation_id": "C", "source_id": "平面", "locator": "实体22"}
    case["interpretations"].append(second)
    out = module.resolve_correspondence(case)
    assert [r["status"] for r in out["requests"]] == ["unique_candidate", "unique_candidate"]
    assert len(out["pairings"]) == 2
    assert out["cross_view_links"] == []


def test_duplicate_detail_indices_remain_multiple_candidates(case):
    a, b = case["observations"]
    a.update(kind="instance", detail_index="D-7")
    b.update(kind="detail", detail_index="D-7")
    case["observations"].append(dict(b, observation_id="C", source_id="详图", locator="另一版同号详图"))
    case["requests"][0].update(relation="detail_applicability", target_ids=["B", "C"], match_fields=["detail_index"])
    assert result(case)["status"] == "multiple_candidates"


@pytest.mark.parametrize("field", ["object_type", "specification", "variant"])
def test_known_detail_type_mismatch_cannot_be_omitted(case, field):
    a, b = case["observations"]
    a.update(kind="instance", detail_index="D-7")
    b.update(kind="detail", detail_index="D-7")
    a[field], b[field] = "类型甲", "类型乙"
    case["requests"][0].update(relation="detail_applicability", match_fields=["detail_index"])
    assert result(case)["status"] == "no_candidate"


@pytest.mark.parametrize("field", ["building", "system", "floor", "connection_role", "object_tag"])
def test_missing_context_never_becomes_unique(case, field):
    case["observations"][1].pop(field)
    row = result(case)
    assert row["status"] == "insufficient_constraints"
    assert row["candidate_ids"] == ["B"]
    select(case)
    with pytest.raises(ValueError, match="补齐观察"):
        module.resolve_correspondence(case)


def test_matching_only_system_not_enough_identity(case):
    case["requests"][0]["match_fields"] = ["system"]
    assert result(case)["status"] == "insufficient_constraints"


def test_missing_match_constraints_rejected(case):
    case["requests"][0]["match_fields"] = []
    with pytest.raises(ValueError, match="匹配约束"):
        module.resolve_correspondence(case)


def test_coordinates_never_choose_nearest_candidate(case):
    case["observations"][1]["point"] = [0, 0]
    case["observations"].append(dict(case["observations"][1], observation_id="C", locator="另一个端口", point=[100000, 200000]))
    case["requests"][0]["target_ids"].append("C")
    assert result(case)["status"] == "multiple_candidates"


def test_source_hash_change_rejected(case):
    Path(case["sources"][0]["path"]).write_text("已修订", encoding="utf-8")
    with pytest.raises(ValueError, match="已改变"):
        module.resolve_correspondence(case)


def test_manual_source_rejected(case):
    case["sources"][0]["kind"] = "manual"
    with pytest.raises(ValueError, match="底稿"):
        module.resolve_correspondence(case)


def test_export_requires_explicit_model_interpretation(case):
    select(case, export_cross_view_link=True, link_id="L", physical_id="RISER-A-L1-L2")
    out = module.resolve_correspondence(case)
    link = out["cross_view_links"][0]
    assert link["a"] == {"segment_id": "SEG-A", "end": "end"}
    assert link["b"] == {"segment_id": "SEG-B", "end": "start"}
    assert link["adoption_scope"] == "working" and link["review_status"] == "unreviewed"
    assert not any(k in link for k in ("length", "quantity", "dn_continuity", "from_elevation", "to_elevation"))
    assert out["quantity_created"] is False


def test_export_real_topology_keeps_vertical_length_unknown(case):
    from qto_pipe_topology import prepare_network
    select(case, export_cross_view_link=True, link_id="L", physical_id="RISER-A-L1-L2")
    link = module.resolve_correspondence(case)["cross_view_links"][0]
    data = {"unit": "m", "segments": [
        {"segment_id": "SEG-A", "start": [0, 0, 0], "end": [1, 0, 0], "length": 1,
         "system": "空调水", "space_id": "一层平面"},
        {"segment_id": "SEG-B", "start": [4, 0, 0], "end": [5, 0, 0], "length": 1,
         "system": "空调水", "space_id": "二层平面"}], "cross_view_links": [link]}
    _, report = prepare_network(data)
    assert report["vertical_quantities"][0]["value"] is None
    assert report["derived_length"] == 2


@pytest.mark.parametrize("mutation", ["missing_ref", "wrong_locator", "conflict", "confirmed"])
def test_interpretation_cannot_bypass_evidence_or_conflicts(case, mutation):
    select(case)
    row = case["interpretations"][0]
    if mutation == "missing_ref": row["source_refs"].pop()
    if mutation == "wrong_locator": row["source_refs"][1]["locator"] = "另一设备"
    if mutation == "conflict": case["observations"][1]["system"] = "给水"
    if mutation == "confirmed": row["confirmed_by"] = "虚构人工审核"
    with pytest.raises(ValueError):
        module.resolve_correspondence(case)


def test_duplicate_link_physical_identity_rejected(case):
    select(case, export_cross_view_link=True, link_id="L", physical_id="P")
    case["observations"].append(dict(case["observations"][1], observation_id="C", locator="系统图另视图",
                                     route_ref={"segment_id": "SEG-C", "end": "start"}))
    case["requests"][0]["target_ids"].append("C")
    row = copy.deepcopy(case["interpretations"][0])
    row.update(interpretation_id="I2", target_id="C", link_id="L2")
    row["source_refs"][1] = {"observation_id": "C", "source_id": "系统", "locator": "系统图另视图"}
    case["interpretations"].append(row)
    with pytest.raises(ValueError, match="重复导出"):
        module.resolve_correspondence(case)


def test_repeated_view_cannot_create_topological_connection(case):
    case["requests"][0]["relation"] = "same_physical"
    case["observations"][1]["floor"] = "一层"
    select(case, export_cross_view_link=True, link_id="L", physical_id="P")
    with pytest.raises(ValueError, match="不能造连接"):
        module.resolve_correspondence(case)


@pytest.mark.parametrize("reverse,observation_alias,route_alias,source_alias", [
    (True, False, False, False),
    (False, True, False, False),
    (True, True, False, False),
    (False, True, True, False),
    (True, True, True, True),
])
def test_same_undirected_source_port_pair_cannot_export_twice(
        case, reverse, observation_alias, route_alias, source_alias):
    select(case, export_cross_view_link=True, link_id="L", physical_id="P")
    ids = ["A", "B"]
    if observation_alias:
        aliases = [dict(o, observation_id=o["observation_id"] + "-alias")
                   for o in case["observations"][:2]]
        if route_alias:
            for observation in aliases:
                observation["route_ref"] = dict(observation["route_ref"],
                                               segment_id=observation["route_ref"]["segment_id"] + "-alias")
        if source_alias:
            for observation in aliases:
                source = next(s for s in case["sources"] if s["source_id"] == observation["source_id"])
                source = dict(source, source_id=source["source_id"] + "-alias")
                case["sources"].append(source)
                observation["source_id"] = source["source_id"]
        case["observations"].extend(aliases)
        ids = [o["observation_id"] for o in aliases]
    if reverse:
        ids.reverse()
    case["requests"].append(dict(case["requests"][0], request_id="Q2", from_id=ids[0], target_ids=[ids[1]]))
    selected = [next(o for o in case["observations"] if o["observation_id"] == oid) for oid in ids]
    case["interpretations"].append({
        "interpretation_id": "I2", "request_id": "Q2", "target_id": ids[1], "decision_origin": "model",
        "basis": "同一实物的反向或别名观察，不能再产生一个数量出口",
        "source_refs": [{"observation_id": o["observation_id"], "source_id": o["source_id"],
                         "locator": o["locator"]} for o in selected],
        "export_cross_view_link": True, "link_id": "L2", "physical_id": "P2"})
    with pytest.raises(ValueError, match="源端口配对重复"):
        module.resolve_correspondence(case)


def test_distinct_source_port_pairs_still_export_separately(case):
    select(case, export_cross_view_link=True, link_id="L", physical_id="P")
    other = dict(case["observations"][1], observation_id="C", locator="另一真实端口",
                 route_ref={"segment_id": "SEG-C", "end": "start"})
    case["observations"].append(other)
    case["requests"][0]["target_ids"].append("C")
    second = copy.deepcopy(case["interpretations"][0])
    second.update(interpretation_id="I2", target_id="C", link_id="L2", physical_id="P2")
    second["source_refs"][1] = {"observation_id": "C", "source_id": "系统", "locator": "另一真实端口"}
    case["interpretations"].append(second)
    assert len(module.resolve_correspondence(case)["cross_view_links"]) == 2


def test_same_typical_source_in_distinct_buildings_keeps_real_connections(case):
    select(case, export_cross_view_link=True, link_id="L", physical_id="P")
    aliases = [dict(o, observation_id=o["observation_id"] + "-乙楼", building="乙楼",
                    route_ref=dict(o["route_ref"], segment_id=o["route_ref"]["segment_id"] + "-乙楼"))
               for o in case["observations"][:2]]
    case["observations"].extend(aliases)
    case["requests"].append(dict(case["requests"][0], request_id="Q2", from_id=aliases[0]["observation_id"],
                                 target_ids=[aliases[1]["observation_id"]]))
    second = copy.deepcopy(case["interpretations"][0])
    second.update(interpretation_id="I2", request_id="Q2", target_id=aliases[1]["observation_id"],
                  link_id="L-乙楼", physical_id="P-乙楼")
    second["source_refs"] = [{"observation_id": o["observation_id"], "source_id": o["source_id"],
                              "locator": o["locator"]} for o in aliases]
    case["interpretations"].append(second)
    assert len(module.resolve_correspondence(case)["cross_view_links"]) == 2


def test_cli_relative_sources_and_no_original_overwrite(case, tmp_path):
    for source in case["sources"]:
        source["path"] = Path(source["path"]).name
    p = tmp_path / "输入.json"
    p.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "结果.json"
    cmd = [sys.executable, "-B", str(SCRIPTS / "qto_object_correspondence.py"), "--input", str(p), "--output"]
    run = subprocess.run(cmd + [str(output)], text=True, encoding="utf-8", capture_output=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["requests"][0]["status"] == "unique_candidate"
    before = p.read_bytes()
    assert subprocess.run(cmd + [str(p)], capture_output=True).returncode == 2
    assert p.read_bytes() == before
