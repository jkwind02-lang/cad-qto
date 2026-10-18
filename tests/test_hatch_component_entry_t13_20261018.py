"""Explicit HATCH counting consumes versioned location facts; it never creates area or port geometry.

CAD_QTO_TEST_NEUTRAL_EXTRACTOR lets the two isolated authors run their real extraction/consumer interface before
the main task merges patches. After merge the normal repository extractor is used.
"""
import json
import os
from pathlib import Path

import ezdxf
import pytest

import test_side_text_leader_l2_20261017 as common


def triangle(space, x=0, y=0, layer="GROUND"):
    hatch = space.add_hatch(color=7, dxfattribs={"layer": layer})
    hatch.paths.add_polyline_path([(x, y), (x + 100, y), (x + 50, y + 100)], is_closed=True)
    return hatch.dxf.handle


def make_case(tmp_path, enabled=True, unknown=False, bounded=False):
    doc = ezdxf.new("R2013"); doc.units = 4
    doc.layers.add("GROUND"); doc.layers.add("LEGEND")
    msp = doc.modelspace()
    real = "PT-CAD-" + triangle(msp, 1000, 1000)
    legend = "PT-CAD-" + triangle(msp, 3000, 1000, "LEGEND")
    floor = doc.blocks.new("FLOOR")
    child = triangle(floor)
    top1 = msp.add_blockref("FLOOR", (5000, 2000), dxfattribs={"rotation": 90}).dxf.handle
    top2 = msp.add_blockref("FLOOR", (8000, 2000), dxfattribs={"xscale": -2, "yscale": 2}).dxf.handle
    nested = [f"PT-CAD-{top}/FLOOR/CAD-{child}" for top in (top1, top2)]
    missing = []
    if unknown:
        missing = ["PT-CAD-" + msp.add_hatch(dxfattribs={"layer": "GROUND"}).dxf.handle]
    dxf = tmp_path / "匿名点位.dxf"; doc.saveas(dxf)
    extractor = Path(os.environ.get("CAD_QTO_TEST_NEUTRAL_EXTRACTOR", str(common.SHARED / "extract_cad_neutral.py")))
    done = common.run(extractor, "--source-dxf", dxf, "--output-dir", tmp_path / "neutral")
    # Extraction remains PARTIAL/3 because HATCH has location but no measurement adapter.
    assert done.returncode == 3 and json.loads(done.stdout)["status"] == "PARTIAL", done.stdout + done.stderr
    src = common.sha(dxf)
    entries = []
    for rid, decision, objects in [("GROUND", "include", [real, *nested, *missing]), ("LEGEND", "exclude", [legend]),
                                    ("FLOOR", "container", [f"PT-CAD-{top1}", f"PT-CAD-{top2}"])]:
        entries.append({"adjudication_id": rid, "subject_id": rid, "professional_owner": "qto-components-points",
            "decision": decision, "status": "active", "decision_origin": "model", "recognized_by": "合成图核对",
            "recognized_at": "2026-10-18T00:00:00+00:00", "source_id": "SYN", "evidence_ids": ["E"],
            "object_ids": objects, "basis": "显式按接地实体与图例身份识别", "scope_basis": "合成平面", "identity_basis": "句柄及插入路径",
            "cross_check": "逐实例核对", "uncertainties": ["合成测试"]})
    ledger = {"schema_version": "qto-model-recognition-ledger/1.0", "ledger_id": "L", "ledger_version": "1",
        "source_sha256": src, "adoption_scope": "working", "review_status": "unreviewed", "entries": entries,
        "evidence": [{"evidence_id": "E", "path": str(dxf), "sha256": src, "source_sha256": src,
                      "kind": "cad_property", "locator": "Model", "observation": "闭合HATCH三角与图例"}]}
    lp = tmp_path / "识别记录.json"; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    profile = {"schema_version": "qto-component-recognition-profile/1.0", "profile_id": "P", "profile_version": "1",
        "expected_source_sha256": src, "adjudication_ledger": {"ledger_id": "L", "ledger_version": "1", "sha256": common.sha(lp)},
        "scope": {"layout": "Model", **({"bounds": {"xmin": 1050, "xmax_exclusive": 9000, "ymin": 0, "ymax_exclusive": 5000}} if bounded else {})},
        "unit": {"factor_to_output": .001, "output_coordinate_unit": "m", "source_id": "INSUNITS=4"},
        "rules": [{"rule_id": "GROUND", "adjudication_id": "GROUND", "layer_pattern": "^GROUND$", "decision": "include",
                   "entity_types": ["HATCH"] if enabled else ["INSERT"], "category": "接地端子板", "system": "接地", "spec": "合成端子板", "unit": "块"},
                  {"rule_id": "LEGEND", "adjudication_id": "LEGEND", "entity_types": ["HATCH"], "layer_pattern": "^LEGEND$", "decision": "exclude"},
                  {"rule_id": "FLOOR", "adjudication_id": "FLOOR", "decision": "container", "block_pattern": "^FLOOR$"}]}
    pp = tmp_path / "识别规则.json"; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    done = common.run(common.POINTS / "build_component_instances_from_neutral.py", "--cad-manifest", tmp_path / "neutral/CAD中性取证清单.json",
                      "--profile", pp, "--adjudication-ledger", lp, "--output-dir", out)
    return common.outcome(done, out), real, nested, missing, legend


def test_real_dxf_hatch_counts_only_when_explicit_and_keeps_physical_paths(tmp_path):
    (report, disp, adopted), real, nested, missing, legend = make_case(tmp_path)
    assert set(adopted) == {real, *nested}
    assert disp[legend]["disposition"] == "excluded"
    assert adopted[nested[0]]["point"] == pytest.approx([4.95, 2.05, 0])
    assert adopted[nested[1]]["point"] == pytest.approx([7.9, 2.1, 0])
    for pid in adopted:
        assert adopted[pid]["instance_path"] == pid[3:]
        assert adopted[pid]["measurement_geometry_available"] is False
        assert disp[pid]["point_basis"] == "location_bbox_center_not_insertion_or_connection_port"
        assert disp[pid]["location_status"] == "bounded"
    assert report["counts"]["scoped_hatch_instance_count"] == 4
    assert report["point_workpackage_complete"] is True


def test_default_insert_rules_do_not_turn_all_fills_into_counted_components(tmp_path):
    (report, disp, adopted), real, nested, missing, legend = make_case(tmp_path, enabled=False)
    assert not adopted and real not in disp and all(pid not in disp for pid in nested)


def test_unknown_hatch_location_is_not_zero_and_does_not_complete_workpackage(tmp_path):
    (report, disp, adopted), real, nested, missing, legend = make_case(tmp_path, unknown=True)
    row = disp[missing[0]]
    assert row["disposition"] == "unresolved" and row["point"] is None
    assert row["scope_geometry_bbox"] is None and row["location_status"] == "unknown"
    assert report["status"] == "REVIEW_REQUIRED"


def test_hatch_hull_crossing_package_boundary_stays_unresolved(tmp_path):
    (report, disp, adopted), real, nested, missing, legend = make_case(tmp_path, bounded=True)
    assert real not in adopted and disp[real]["disposition"] == "unresolved"
    assert "跨工作范围" in disp[real]["basis"]
