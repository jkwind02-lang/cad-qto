"""T21 follow-up: a range leader's physical endpoint-on-straight-interior contacts are continuous.

The segmented trunk is an independent legal control. Interior crossings, gaps beyond the configured snap and
ambiguous hosts remain disconnected. Neither a graph junction nor a range note creates a per-device key.
"""
import pytest

import test_side_text_leader_l2_20261017 as points


def draw_range(topology, junction_box=False):
    def draw(doc, msp):
        ids = {name: points.box(msp, x, 0) for name, x in [("A", 0), ("B", 1000), ("C", 2000)]}
        if junction_box:
            ids["JUNCTION"] = points.box(msp, 1000, 1000)
        for x in (0, 1000, 2000):
            top = 1000
            if x and topology == "cross":
                top = 1500  # trunk crosses the middle of this branch, not a branch endpoint
            elif x and topology == "near":
                top = 990   # 10 mm gap > the unchanged 1 mm snap tolerance
            elif x and topology == "within_snap":
                top = 999.5
            msp.add_line((x, 50), (x, top), dxfattribs=points.LEAD)
        if topology == "split":
            for x1, x2 in [(0, 1000), (1000, 2000), (2000, 3000)]:
                msp.add_line((x1, 1000), (x2, 1000), dxfattribs=points.LEAD)
        else:
            msp.add_line((0, 1000), (3000, 1000), dxfattribs=points.LEAD)
        if topology == "ambiguous":
            # Both branches see two different host segments within 1 mm. They must not choose one by order.
            msp.add_line((500, 1000.5), (2500, 1000.5), dxfattribs=points.LEAD)
        points.note(msp, "FAN1-3", 3000, 1000)
        return ids
    return draw


def range_condition(shared=True):
    return {"pattern": "^FAN1-3$", "layer_pattern": "^NOTE$", "role": "classify", "basis": "范围共用连续引线的合成对照",
            "leader": {"layer_pattern": "^LEAD$", "touch_tolerance": .15, "text_tolerance": .05,
                       "snap_tolerance": .001, "max_segments": 20, "allow_shared_text": shared}}


@pytest.mark.parametrize("topology", ["T", "split", "within_snap", "cross", "near", "ambiguous"])
@pytest.mark.parametrize("shared", [False, True])
def test_range_leader_t_contacts_and_non_connections(tmp_path, topology, shared):
    ids, done, out = points.build(tmp_path, draw_range(topology), range_condition(shared))
    report, disp, adopted = points.outcome(done, out)
    continuous = topology in ("T", "split", "within_snap")
    expected = 3 if continuous and shared else 0 if continuous else 1
    assert len(adopted) == expected
    graph = report["side_text_binding"]["leader_graphs"][0]
    if continuous and shared:
        assert set(adopted) == set(ids.values())
        assert graph["continuous_component_count"] == 1
        for row in adopted.values():
            side = row["side_text"]
            assert side["text"] == "FAN1-3" and side["leader_group"]["physical_ids"] == sorted(ids.values())
            assert side["leader_terminal_evidence"] and "instance_key" not in row
            if topology != "split":
                assert len(side["leader_t_contacts"]) == 2
                assert all(c["distance"] <= .001 for c in side["leader_t_contacts"])
                assert all(c["basis"] == "free_endpoint_on_straight_segment_interior" for c in side["leader_t_contacts"])
    if topology == "split":
        assert not graph["t_contacts_used"]
    if topology in ("cross", "near"):
        assert not graph["t_contacts_used"]
        assert set(adopted) == {ids["A"]}
        assert all(disp[ids[name]]["side_text"]["status"] == "无文字" for name in ("B", "C"))
    if topology == "ambiguous":
        assert any(c["reason"] == "multiple_host_segments" for c in graph["t_contacts_unresolved"])
        assert set(adopted) == {ids["A"]}


def test_a_t_junction_inside_a_box_is_not_an_arrow_terminal(tmp_path):
    ids, done, out = points.build(tmp_path, draw_range("T", junction_box=True), range_condition())
    report, disp, adopted = points.outcome(done, out)
    assert set(adopted) == {ids[name] for name in ("A", "B", "C")}
    assert disp[ids["JUNCTION"]]["disposition"] == "unresolved"
    for row in adopted.values():
        assert ids["JUNCTION"] not in row["side_text"]["leader_group"]["physical_ids"]


def test_endpoint_on_arc_interior_remains_unsupported(tmp_path):
    def draw(doc, msp):
        ids = {"A": points.box(msp, 0, 0), "B": points.box(msp, 1000, 0)}
        msp.add_line((0, 50), (0, 1000), dxfattribs=points.LEAD)
        msp.add_arc((1000, 1000), 1000, 0, 180, dxfattribs=points.LEAD)
        msp.add_line((1000, 50), (1000, 2000), dxfattribs=points.LEAD)
        points.note(msp, "FAN1-3", 2000, 1000)
        return ids
    ids, done, out = points.build(tmp_path, draw, range_condition())
    report, disp, adopted = points.outcome(done, out)
    assert set(adopted) == {ids["A"]}
    assert any(c["reason"] == "host_not_straight" for c in report["side_text_binding"]["leader_graphs"][0]["t_contacts_unresolved"])
