"""Third batch T21/T22: anonymized chains, endpoint evidence, namespaces and route-local bridges.

These are deterministic synthetic DXF checks; they do not accept any real DWG or project quantity.
"""
import json

import pytest

import test_side_text_leader_l2_20261017 as points
import test_trace_drawn_routes_l1_20261017 as routes
import test_layer_length_l3_20261017 as layer


def test_remote_multiline_label_is_consumed_as_instance_key_endpoint(tmp_path):
    def draw(doc, msp):
        ids = {"A": points.box(msp, 0, 0), "D": points.box(msp, 5000, 0)}
        chain = [(50, 0), (500, 500), (1000, 500), (1500, 1000), (2000, 1000)]
        for a, b in zip(chain, chain[1:]):
            msp.add_line(a, b, dxfattribs=points.LEAD)
        points.note(msp, "AL1", 2000, 1000)
        msp.add_line((5050, 0), (6000, 1000), dxfattribs=points.LEAD)
        points.note(msp, "AL2", 6000, 1000)
        doc.layers.add("WIRE-动力")
        msp.add_line((50, 0), (4950, 0), dxfattribs=routes.WIRE)
        return ids
    cond = points.key_cond(leader={**points.LEADER, "text_tolerance": 0.05, "max_segments": 8})
    ids, done, out = points.build(tmp_path, draw, cond)
    _, disp, adopted = points.outcome(done, out)
    assert set(adopted) == set(ids.values())
    evidence = adopted[ids["A"]]["side_text"]
    assert len(evidence["leader_handles"]) == 4
    assert evidence["leader_terminal_evidence"][0]["mode"] == "terminal_inside_outline"
    manifest = tmp_path / "neutral/CAD中性取证清单.json"
    source = {"source_id": "points", "path": str(out / "点位中性实例处置清单.jsonl"), "basis": "图上引线绑定箱号"}
    code, result, _, _, _ = routes.trace(tmp_path, manifest,
        [{"route_id": "KEY", "from": {"instance_key": "AL1"}, "to": {"instance_key": "AL2"}}], instance_sources=[source],
        endpoint_tolerance=0.01)
    item = result["routes"][0]
    assert code == 0 and item["status"] == "已追踪"
    assert item["drawn_length_m"] == pytest.approx(4.9)
    assert item["from"]["instance_provenance"]["physical_id"] == ids["A"]
    assert item["from"]["instance_provenance"]["side_text"]["leader_handles"] == evidence["leader_handles"]


@pytest.mark.parametrize("kind", ["actual", "forward", "ambiguous", "overlapping"])
def test_terminal_direction_and_tightly_adjacent_boxes(tmp_path, kind):
    def draw(doc, msp):
        ids = {"A": points.box(msp, 0, 0), "B": points.box(msp, 180 if kind != "overlapping" else 40, 0)}
        tip = (50, 0) if kind == "actual" else (100, 0) if kind != "overlapping" else (30, 0)
        bend = (100, 500) if kind == "ambiguous" else (1000, 0)
        msp.add_line(tip, bend, dxfattribs=points.LEAD)
        points.note(msp, "AL1", *bend)
        return ids
    ids, done, out = points.build(tmp_path, draw, points.key_cond(leader={**points.LEADER, "text_tolerance": 0.05}))
    _, disp, adopted = points.outcome(done, out)
    if kind in ("actual", "forward"):
        assert set(adopted) == {ids["A"]}
        proof = adopted[ids["A"]]["side_text"]["leader_terminal_evidence"][0]
        assert proof["mode"] == ("terminal_inside_outline" if kind == "actual" else "terminal_forward_direction")
        assert disp[ids["B"]]["disposition"] == "unresolved"
    else:
        assert not adopted
        assert all(disp[pid]["side_text"]["status"] == "引线未决" for pid in ids.values())


@pytest.mark.parametrize("shared", [False, True])
def test_range_note_one_continuous_leader_group_preserves_text_and_members(tmp_path, shared):
    def draw(doc, msp):
        ids = {"A": points.box(msp, 0, 0), "B": points.box(msp, 1000, 0)}
        for a, b in [((0, 50), (500, 1000)), ((1000, 50), (500, 1000)), ((500, 1000), (500, 2000))]:
            msp.add_line(a, b, dxfattribs=points.LEAD)
        points.note(msp, "FAN1~2", 500, 2000)
        return ids
    cond = {"pattern": "^FAN1~2$", "layer_pattern": "^NOTE$", "role": "classify", "basis": "范围编号共用连续引线组",
            "leader": {**points.LEADER, "allow_shared_text": shared, "text_tolerance": 0.05}}
    ids, done, out = points.build(tmp_path, draw, cond)
    _, disp, adopted = points.outcome(done, out)
    if shared:
        assert set(adopted) == set(ids.values())
        for row in adopted.values():
            side = row["side_text"]
            assert side["text"] == "FAN1~2" and side["leader_group"]["physical_ids"] == sorted(ids.values())
            assert len(side["leader_handles"]) == 3 and side["leader_terminal_evidence"]
            assert "instance_key" not in row
    else:
        assert not adopted and all(disp[pid]["side_text"]["status"] == "引线未决" for pid in ids.values())


def test_range_note_never_becomes_shared_unique_instance_key(tmp_path):
    ids, done, out = points.build(tmp_path, lambda d, m: {"A": points.box(m, 0, 0)},
                                 points.key_cond(leader={**points.LEADER, "allow_shared_text": True}))
    assert done.returncode == 2 and "allow_shared_text" in done.stderr


def test_connected_chain_bend_inside_another_box_is_not_a_second_arrow_tip(tmp_path):
    def draw(doc, msp):
        ids = {"A": points.box(msp, 0, 0), "PASS": points.box(msp, 1000, 500)}
        msp.add_line((50, 0), (1000, 500), dxfattribs=points.LEAD)
        msp.add_line((1000, 500), (2000, 1000), dxfattribs=points.LEAD)
        points.note(msp, "AL1", 2000, 1000)
        return ids
    ids, done, out = points.build(tmp_path, draw, points.key_cond(leader={**points.LEADER, "text_tolerance": .05}))
    _, disp, adopted = points.outcome(done, out)
    assert set(adopted) == {ids["A"]}
    assert disp[ids["PASS"]]["disposition"] == "unresolved"


def test_identical_two_labels_to_one_physical_block_do_not_count_twice(tmp_path):
    def draw(doc, msp):
        ids = {"A": points.box(msp, 0, 0)}
        for end, label in [((1000, 1000), "AL1"), ((-1000, 1000), "AL1")]:
            msp.add_line((0, 50), end, dxfattribs=points.LEAD)
            points.note(msp, label, *end)
        return ids
    ids, done, out = points.build(tmp_path, draw, points.key_cond())
    _, disp, adopted = points.outcome(done, out)
    assert list(adopted) == [ids["A"]]
    assert disp[ids["A"]]["instance_key"] == "AL1"
    assert len(adopted[ids["A"]]["side_text"]["leader_handles"]) == 2


def gap_case(tmp_path):
    doc = routes.new_doc(); msp = doc.modelspace()
    a = msp.add_line((0, 0), (2000, 0), dxfattribs=routes.WIRE).dxf.handle
    b = msp.add_line((4000, 1000), (8000, 1000), dxfattribs=routes.WIRE).dxf.handle
    # Another equidistant but physically different line makes indiscriminate widening unsafe.
    c = msp.add_line((4000, -1000), (8000, -1000), dxfattribs=routes.WIRE).dxf.handle
    return routes.extract(tmp_path, doc), a, b, c


def test_route_local_specified_bridge_and_via_chain_do_not_connect_unspecified_route(tmp_path):
    manifest, a, b, c = gap_case(tmp_path)
    common = {"from": {"point": [0, 0]}, "to": {"point": [8, 1]}}
    requests = [{"route_id": "UNSPECIFIED", **common},
                {"route_id": "PAIR", **common, "bridge_pairs": [[a, b, "井内通道已核，连接指定两端"]]},
                {"route_id": "VIA", **common, "via_handles": [a, b], "via_basis": "本回路经由两段已画线"}]
    code, result, paths, lines, todo = routes.trace(tmp_path, manifest, requests, gap_tolerance=0.0, endpoint_tolerance=0.01)
    got = {r["route_id"]: r for r in result["routes"]}
    assert code == 3 and got["UNSPECIFIED"]["status"] == "待办"
    for rid in ("PAIR", "VIA"):
        row = got[rid]
        assert row["status"] == "已追踪" and row["drawn_length_m"] == pytest.approx(6)
        assert row["project_bridged_gap_length_m"] == pytest.approx(5 ** 0.5)
        assert row["length_m"] == pytest.approx(6 + 5 ** 0.5)
        assert row["bridged_gaps"][0]["project_basis"]
        assert {s["handle"] for s in row["segments"]} == {a, b}
    assert all(p["project_bridges"][0]["project_basis"] for p in paths["paths"])


def test_via_order_is_enforced_and_missing_handles_stay_todo(tmp_path):
    manifest, a, b, c = gap_case(tmp_path)
    base = {"from": {"point": [0, 0]}, "to": {"point": [8, 1]}, "via_basis": "指定顺序"}
    _, result, _, _, _ = routes.trace(tmp_path, manifest,
        [{"route_id": "REVERSED", **base, "via_handles": [b, a]},
         {"route_id": "MISSING", **base, "via_handles": [a, "UNKNOWN"]}], endpoint_tolerance=0.01)
    assert all(r["status"] == "待办" for r in result["routes"])
    assert result["routes"][1]["problems"][0]["problem"] == "指定句柄未唯一"


def test_bridge_equal_endpoint_candidates_require_explicit_entity_ends(tmp_path):
    doc = routes.new_doc(); msp = doc.modelspace()
    a = msp.add_line((0, 0), (0, 2000), dxfattribs=routes.WIRE).dxf.handle
    b = msp.add_line((2000, 0), (2000, 2000), dxfattribs=routes.WIRE).dxf.handle
    manifest = routes.extract(tmp_path, doc)
    common = {"from": {"point": [0, 2]}, "to": {"point": [2, 2]}}
    _, result, _, _, _ = routes.trace(tmp_path, manifest,
        [{"route_id": "AMB", **common, "bridge_pairs": [[a, b, "井内穿越"]]},
         {"route_id": "EXPLICIT", **common, "bridge_pairs": [{"from_handle": a, "to_handle": b,
             "from_end": "start", "to_end": "start", "basis": "核定下端井口"}]}], endpoint_tolerance=0.01, gap_tolerance=0)
    assert result["routes"][0]["problems"][0]["problem"] == "桥接端对等距未决"
    assert result["routes"][1]["status"] == "已追踪" and result["routes"][1]["length_m"] == pytest.approx(6)


def test_terminal_branch_labels_are_reported_without_a_t_contact(tmp_path):
    doc = routes.new_doc(); msp = doc.modelspace()
    msp.add_line((0, 0), (5000, 0), dxfattribs=routes.WIRE)
    msp.add_text("WP9", dxfattribs={"insert": (4000, 100), "height": 100})
    manifest = routes.extract(tmp_path, doc)
    _, result, _, _, _ = routes.trace(tmp_path, manifest, [{"route_id": "R", "from": {"point": [0, 0]}, "to": {"point": [5, 0]}}],
                                    circuit_label_pattern="WP[0-9]+", circuit_label_radius=0.2)
    row = result["routes"][0]
    assert row["status"] == "已追踪" and not row["t_junctions"]
    assert [t["label"] for t in row["terminal_branch_labels"]] == ["WP9"]


def test_source_namespaces_duplicate_ids_keys_and_missing_coordinates(tmp_path):
    manifest, a, b, c = gap_case(tmp_path)
    tables = [{"source_id": sid, "basis": "人工回图映射", "coordinate_unit": "m", "instances": [
        {"physical_id": "CUSTOM", "instance_key": "AL9", "point": point}]} for sid, point in [("L3", [0, 0]), ("L1", [8, 1])]]
    tables.append({"source_id": "UNKNOWN", "basis": "缺值反例", "coordinate_unit": "m", "instances": [{"physical_id": "NOXY"}]})
    rows = [{"route_id": "DUP", "from": {"instance_id": "CUSTOM"}, "to": {"point": [2, 0]}},
            {"route_id": "KEYDUP", "from": {"instance_key": "AL9"}, "to": {"point": [2, 0]}},
            {"route_id": "QUALIFIED", "from": {"instance_id": "CUSTOM", "instance_source": "L3"}, "to": {"point": [2, 0]}},
            {"route_id": "UNKNOWN", "from": {"instance_id": "NOXY", "instance_source": "UNKNOWN"}, "to": {"point": [2, 0]}},
            {"route_id": "MISS", "from": {"instance_key": "NOT-DRAWN"}, "to": {"point": [2, 0]}}]
    _, result, _, _, _ = routes.trace(tmp_path, manifest, rows, instance_sources=tables, endpoint_tolerance=0.01)
    got = {r["route_id"]: r for r in result["routes"]}
    assert all(got[r]["status"] == "待办" for r in ("DUP", "KEYDUP", "UNKNOWN", "MISS"))
    assert len(got["DUP"]["problems"][0]["candidates"]) == 2
    assert got["QUALIFIED"]["status"] == "已追踪"
    assert got["QUALIFIED"]["from"]["instance_provenance"]["source_id"] == "L3"


def test_l3_custom_instance_table_and_generated_l1_routes_roundtrip(tmp_path):
    doc = layer.new_doc(); plan = layer.Plan(doc.modelspace())
    plan.ins("A", "BOX", 1000, 2000); plan.ins("B", "SBOX", 5100, 2000)
    plan.line("LINE", (1300, 2000), (5000, 2000))
    for row, custom in zip(plan.rows, ["CUSTOM-BOX", "CUSTOM-SBOX"]):
        row["physical_id"] = row["object_id"] = custom
    manifest = layer.extract(tmp_path, doc); layer.write_inputs(tmp_path, plan.rows)
    done, out = layer.measure(tmp_path, manifest, layer.request())
    assert done.returncode in (0, 3), done.stdout + done.stderr
    produced = out / "按图层量长结果.json"
    data = json.loads(produced.read_text(encoding="utf-8"))
    assert len(data["l1_routes"]) == 1 and len(data["endpoint_instances"]) == 2
    code, result, _, _, _ = routes.trace(tmp_path, manifest, data["l1_routes"], route_layers="^WIRE-照明$",
        endpoint_tolerance=0.01, instance_sources=[{"source_id": "L3", "path": str(produced), "basis": "L3已画分量归箱实例"}])
    row = result["routes"][0]
    assert code == 0 and row["status"] == "已追踪" and row["length_m"] == pytest.approx(3.7)
    assert row["from"]["instance_provenance"]["source_id"] == "L3"
    assert row["to"]["instance_provenance"]["physical_id"] == "CUSTOM-SBOX"


def test_same_handle_in_two_insertions_requires_complete_source_id(tmp_path):
    doc = routes.new_doc(); block = doc.blocks.new("FLOOR")
    h = block.add_line((0, 0), (2000, 0), dxfattribs=routes.WIRE).dxf.handle
    msp = doc.modelspace(); top = msp.add_blockref("FLOOR", (0, 0)).dxf.handle
    msp.add_blockref("FLOOR", (0, 10000))
    b = msp.add_line((4000, 0), (6000, 0), dxfattribs=routes.WIRE).dxf.handle
    manifest = routes.extract(tmp_path, doc)
    common = {"from": {"point": [0, 0]}, "to": {"point": [6, 0]}}
    _, result, _, _, _ = routes.trace(tmp_path, manifest,
        [{"route_id": "RAW", **common, "bridge_pairs": [[h, b, "指定井口"]]},
         {"route_id": "QUAL", **common, "bridge_pairs": [[f"CAD-{top}/FLOOR/CAD-{h}", b, "完整插入路径限定"]]}],
        expand_blocks=["FLOOR"], gap_tolerance=0, endpoint_tolerance=0.01)
    assert result["routes"][0]["problems"][0]["problem"] == "指定句柄未唯一"
    assert result["routes"][1]["status"] == "已追踪"


def test_location_only_source_cannot_turn_a_hatch_center_into_a_route_port(tmp_path):
    manifest, a, b, c = gap_case(tmp_path)
    _, result, _, _, _ = routes.trace(tmp_path, manifest,
        [{"route_id": "HATCH", "from": {"instance_id": "HATCH"}, "to": {"point": [2, 0]}}],
        instance_sources=[{"source_id": "H", "basis": "定位证据", "coordinate_unit": "m", "instances": [
            {"physical_id": "HATCH", "point": [0, 0], "measurement_geometry_available": False}]}])
    assert result["routes"][0]["status"] == "待办"
    assert "只有定位边界" in result["routes"][0]["problems"][0]["problem"]


def test_another_drawing_with_the_same_native_handle_is_not_the_same_instance(tmp_path):
    doc = routes.new_doc(); msp = doc.modelspace()
    box = routes.ins(msp, "BOX", 0, 0)
    msp.add_line((300, 0), (2000, 0), dxfattribs=routes.WIRE)
    manifest = routes.extract(tmp_path, doc)
    _, result, _, _, _ = routes.trace(tmp_path, manifest,
        [{"route_id": "R", "from": {"instance_key": "AL1"}, "to": {"point": [2, 0]}}],
        instance_sources=[{"source_id": "OTHER", "basis": "另一份图纸的箱号", "coordinate_unit": "m",
                           "source_sha256": "0" * 64, "instances": [{"physical_id": f"PT-CAD-{box}", "instance_key": "AL1", "point": [0, 0]}]}])
    assert result["routes"][0]["status"] == "待办"
    assert "指纹不符" in result["routes"][0]["problems"][0]["problem"]


@pytest.mark.parametrize("constraint", [{"via_handles": ["A", "B"]}, {"bridge_pairs": [["A", "B", ""]]}])
def test_bridge_or_via_without_basis_is_rejected(tmp_path, constraint):
    manifest, a, b, c = gap_case(tmp_path)
    request = {"schema": "qto-drawn-route-request/1.0", "unit": {"factor_to_output": .001, "source_id": "SYN"},
               "route_layers": ".*", "endpoint_tolerance": .01,
               "routes": [{"route_id": "R", "from": {"point": [0, 0]}, "to": {"point": [8, 1]}, **constraint}]}
    path = tmp_path / "invalid.json"; path.write_text(json.dumps(request), encoding="utf-8")
    done = routes.run(routes.ROUTES / "trace_drawn_routes.py", "--cad-manifest", manifest, "--request", path, "--output-dir", tmp_path / "invalid")
    assert done.returncode == 2 and "basis" in done.stdout + done.stderr
