"""Anonymous source/evidence controls for T17/T24; no project dictionary or answers."""
import copy
import base64
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills/_qto-shared-evidence/scripts"
PIPE = ROOT / "skills/qto-pipe-networks/scripts"
sys.path.insert(0, str(SHARED))
import extract_cad_neutral as extract
import qto_symbol_geometry as symbol


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def package(folder, variant=False, incomplete=False, include_stretch=True, nested_pipe=False):
    folder.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new(); doc.units = 6
    for name in ("ALPHA", "BETA", "UNPLACED"):
        b = doc.blocks.new(name)
        points = [(0, 0), (2, 0), (2, 1), (0, 1)]
        if variant and name == "ALPHA":
            points[2] = (3, 1)
        for start, end in zip(points, points[1:] + points[:1]):
            b.add_line(start, end)
        b.add_line(points[0], points[2])
    outer = doc.blocks.new("OUTER"); outer.add_blockref("ALPHA", (0, 0))
    m = doc.modelspace(); tags = {}
    for name, pos, attrs, tag in [
        ("ALPHA", (10, 10), {}, "a"), ("BETA", (20, 10), {}, "b"),
        ("ALPHA", (30, 10), {"rotation": 73, "xscale": 3, "yscale": 3}, "rotated"),
        ("ALPHA", (40, 10), {"xscale": -2, "yscale": 2}, "mirrored"),
        ("ALPHA", (50, 10), {"xscale": 2, "yscale": 1}, "stretched"),
        ("OUTER", (60, 10), {}, "nested"),
        ("BETA", (10, 50), {}, "legend1"), ("BETA", (10, 55), {}, "legend2")]:
        if tag == "stretched" and not include_stretch:
            continue
        tags[tag] = m.add_blockref(name, pos, dxfattribs={"layer": "PIPE" if nested_pipe and tag == "nested" else "SHARED", **attrs})
    m.add_text("TYPE A DN20", dxfattribs={"insert": (15, 50), "height": 0.2})
    m.add_text("TYPE B DN25", dxfattribs={"insert": (15, 55), "height": 0.2})
    tags["straight"] = m.add_line((0, 20), (9, 20), dxfattribs={"layer": "PIPE"})
    tags["bend"] = m.add_arc((0, 30), 2, 0, 270, dxfattribs={"layer": "PIPE"})
    tags["loose"] = []
    pts = [(100, 20), (102, 20), (102, 21), (100, 21)]
    for start, end in list(zip(pts, pts[1:] + pts[:1])) + [(pts[0], pts[2])]:
        tags["loose"].append(m.add_line(start, end, dxfattribs={"layer": "PIPE"}))
    if incomplete:
        missing = doc.blocks.new("MISSING"); missing.add_line((0, 0), (1, 0))
        tags["missing"] = m.add_blockref("MISSING", (120, 20))
        cyclic = doc.blocks.new("CYCLIC"); cyclic.add_blockref("CYCLIC", (0, 0))
        tags["cyclic"] = m.add_blockref("CYCLIC", (130, 20))
        tags["array"] = m.add_blockref("ALPHA", (140, 20), dxfattribs={"row_count": 2, "row_spacing": 5})
        tags["xref"] = m.add_blockref("BETA", (150, 20))
    source = folder / "anonymous.dxf"; doc.saveas(source)
    h = symbol.digest(source)
    layouts = [extract.convert(e, layout="Model", parent_block=None, source_hash=h, raw_unit="m", sequence=i) for i, e in enumerate(m)]
    if incomplete:
        next(r for r in layouts if r["record_id"] == "CAD-" + tags["xref"].dxf.handle)["raw_geometry"]["block_reference"]["xref"] = True
    block_rows, inventory = [], []
    for b in doc.blocks:
        if b.is_any_layout:
            continue
        serialized = b.name != "MISSING"
        inventory.append({"block_name": b.name, "origin": list(b.block.dxf.base_point), "serialized": serialized, "direct_entity_count": len(b)})
        if serialized:
            block_rows.extend(extract.convert(e, layout="BlockDefinition", parent_block=b.name, source_hash=h, raw_unit="m", sequence=i) for i, e in enumerate(b))
    lp = folder / "layout.ndjson"; bp = folder / "blocks.ndjson"
    for path, rows in ((lp, layouts), (bp, block_rows)):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {"schema_version": "qto-cad-neutral-evidence/1.0", "geometry_schema_version": "qto-cad-geometry/1.1", "status": "PASS", "layouts": ["Model"],
                "authoritative_source": {"path": str(source), "sha256": h}, "exchange_dxf": {"path": str(source), "sha256": h},
                "drawing_unit": {"raw_unit": "m"}, "blocks": inventory,
                "artifacts": {"layout_entities": {"path": str(lp), "sha256": symbol.digest(lp), "count": len(layouts)},
                              "block_definitions": {"path": str(bp), "sha256": symbol.digest(bp), "count": len(block_rows)}}}
    mp = put(folder / "manifest.json", manifest)
    ids = {key: ["CAD-" + e.dxf.handle for e in value] if isinstance(value, list) else "CAD-" + value.dxf.handle for key, value in tags.items()}
    return mp, ids


def request_for(path, sources):
    return put(path, {"schema_version": symbol.REQUEST_SCHEMA,
                      "sources": [{"source_id": sid, "manifest": {"path": str(mp), "sha256": symbol.digest(mp)}, "layouts": ["Model"]} for sid, mp in sources],
                      "legends": [{"source_id": sources[0][0], "layout": "Model", "bounds": [5, 45, 20, 60], "row_tolerance": 0.2, "max_left_distance": 10}],
                      "evidence": [], "object_dispositions": []})


def obj(report, oid, sid="A"):
    return next(o for o in report["objects"] if o["object_id"] == oid and o["source_id"] == sid)


def shape(report, oid, sid="A"):
    return next(o for o in report["shapes"] if o["shape_id"] == oid and o["source_id"] == sid)


def decide(request, report, ids, action="exclude"):
    value = json.loads(request.read_text(encoding="utf-8"))
    for i, oid in enumerate(ids):
        row = obj(report, oid)
        ev = {"evidence_id": f"EV-{i}", "source_id": "A", "object_id": oid, "row_sha256": row["row_sha256"],
              "kind": "cad_property", "basis": "synthetic source object explicitly checked against rectangle symbol"}
        value["evidence"].append(ev)
        value["object_dispositions"].append({"source_id": "A", "object_id": oid, "row_sha256": row["row_sha256"], "decision": action,
            "professional_owner": "qto-pipe-networks", "evidence_ids": [ev["evidence_id"]], "recognized_by": "test model", "recognized_at": "2026-10-19",
            "basis": "explicitly checked symbol outline", "scope_basis": "one generated source object", "identity_basis": "current source and instance snapshot",
            "cross_check": "generated source geometry and legend comparison"})
    put(request, value)


def test_names_transform_and_nested_identity(tmp_path):
    mp, ids = package(tmp_path / "a")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request)
    sig = shape(report, ids["a"])["signature"]
    assert sig and all(shape(report, ids[key])["signature"] == sig for key in ("b", "rotated", "mirrored", "nested"))
    assert shape(report, ids["stretched"])["signature"] is None
    nested = next(o for o in report["objects"] if o["block_name"] == "ALPHA" and o["owner_insert_id"] == ids["nested"])
    assert nested["source_definition_record_id"] and nested["instance_path"] and nested["object_id"].startswith("CADINST-")
    assert report["coverage"]["object_count"] == sum(report["coverage"]["object_disposition_counts"].values())
    assert not report["type_absence_proven"] and not report["quantity_acceptance_proven"]
    assert next(d for d in report["definition_inventory"] if d["block_name"] == "UNPLACED")["placed_insert_records"] == 0


def test_same_name_different_geometry_cross_source(tmp_path):
    a, aid = package(tmp_path / "a"); b, bid = package(tmp_path / "b", variant=True)
    report = symbol.build_census(request_for(tmp_path / "request.json", [("A", a), ("B", b)]))
    assert shape(report, aid["a"])["signature"] != shape(report, bid["a"], "B")["signature"]
    assert obj(report, aid["a"])["source_sha256"] != obj(report, bid["a"], "B")["source_sha256"]


def test_loose_geometry_and_conflicting_legend_keep_candidates(tmp_path):
    mp, ids = package(tmp_path / "a")
    report = symbol.build_census(request_for(tmp_path / "request.json", [("A", mp)]))
    group = next(s for s in report["shapes"] if set(s["object_ids"]) == set(ids["loose"]))
    assert group["signature"] == shape(report, ids["a"])["signature"]
    assert group["same_shape_candidates"] and group["legend_conflict"]
    assert group["legend_text_candidates"] == ["TYPE A DN20", "TYPE B DN25"]
    assert len(report["legend_pairs"]) == 2
    assert all(p["state"] == "candidate_pair" and p["symbols"][0]["row_sha256"] for p in report["legend_pairs"])
    assert all(obj(report, oid)["disposition"] == "pending" for oid in ids["loose"] + [ids["straight"], ids["bend"]])


def test_missing_cyclic_array_xref_are_partial_and_accounted(tmp_path):
    mp, ids = package(tmp_path / "a", incomplete=True)
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request)
    assert report["census_status"] == "PARTIAL"
    for key in ("missing", "cyclic", "array", "xref"):
        assert obj(report, ids[key])["issues"] and obj(report, ids[key])["disposition"] == "pending"
    assert not any(o["owner_insert_id"] == ids["array"] for o in report["objects"])
    assert any(o["reference_chain"] for o in report["objects"] if o["owner_insert_id"] == ids["xref"])
    decide(request, report, [ids["array"]])
    with pytest.raises(ValueError, match="incomplete"):
        symbol.build_census(request)


@pytest.mark.parametrize("bad", ["layout", "manifest", "neutral", "scope", "evidence", "decision_snapshot", "missing_evidence", "layer_decision", "wrong_object_evidence"])
def test_fail_closed_invalid_scope_identity_and_evidence(tmp_path, bad):
    mp, ids = package(tmp_path / "a")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request)
    decide(request, report, [ids["loose"][0]])
    value = json.loads(request.read_text(encoding="utf-8"))
    if bad == "layout": value["sources"][0]["layouts"] = ["*"]
    elif bad == "manifest": value["sources"][0]["manifest"]["sha256"] = "0" * 64
    elif bad == "neutral":
        manifest = json.loads(mp.read_text()); Path(manifest["artifacts"]["layout_entities"]["path"]).write_text("{}\n")
    elif bad == "scope": value["object_dispositions"][0]["object_id"] = "CAD-OUTSIDE"
    elif bad == "evidence": value["evidence"][0]["row_sha256"] = "0" * 64
    elif bad == "decision_snapshot": value["object_dispositions"][0]["row_sha256"] = "0" * 64
    elif bad == "missing_evidence": value["object_dispositions"][0]["evidence_ids"] = []
    elif bad == "layer_decision": value["object_dispositions"][0]["object_id"] = "PIPE:*"
    elif bad == "wrong_object_evidence":
        other = obj(report, ids["straight"])
        value["evidence"][0].update(object_id=other["object_id"], row_sha256=other["row_sha256"])
    put(request, value)
    with pytest.raises(ValueError): symbol.build_census(request)


def test_report_rebuild_rejects_self_rehashed_forgery_and_stale_source(tmp_path):
    mp, ids = package(tmp_path / "a")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request); decide(request, report, ids["loose"])
    output = put(tmp_path / "report.json", symbol.build_census(request))
    ref = {"path": str(output), "sha256": symbol.digest(output)}
    assert len(symbol.pipe_context(ref, mp, "Model")["exclusions"]) == 5
    fake = json.loads(output.read_text()); fake["explicit_dispositions"][0]["object_id"] = ids["straight"]
    put(output, fake); ref["sha256"] = symbol.digest(output)
    with pytest.raises(ValueError, match="recomputed"): symbol.verify_report(ref, mp)
    put(output, symbol.build_census(request)); ref["sha256"] = symbol.digest(output)
    Path(json.loads(mp.read_text())["exchange_dxf"]["path"]).write_bytes(b"changed source")
    with pytest.raises(ValueError, match="stale"): symbol.verify_report(ref, mp)


def pipe_files(folder, mp, report_path):
    manifest = json.loads(mp.read_text()); rows = symbol.load_rows(Path(manifest["artifacts"]["layout_entities"]["path"]), manifest["authoritative_source"]["sha256"])
    _, expanded, _, _ = symbol.expand_source(symbol.load_source("A", mp, symbol.digest(mp), ["Model"]))
    ids = [r["record_id"] for r in expanded.values() if r["layer"] == "PIPE"]
    entry = {"adjudication_id": "P", "subject_id": "P", "professional_owner": "qto-pipe-networks", "source_id": "anonymous", "recognized_by": "test model",
             "recognized_at": "2026-10-19", "basis": "synthetic pipe layer working recognition", "scope_basis": "generated layout", "identity_basis": "source objects",
             "cross_check": "straight and true arc plus loose outlines", "decision": "include", "decision_origin": "model", "status": "active", "object_ids": ids,
             "evidence_ids": ["RAW"], "uncertainties": []}
    source = manifest["authoritative_source"]
    ledger = {"schema_version": "qto-model-recognition-ledger/1.0", "ledger_id": "L", "ledger_version": "1", "source_sha256": source["sha256"],
              "adoption_scope": "working", "review_status": "unreviewed", "entries": [entry], "evidence": [{"evidence_id": "RAW", **source,
              "source_sha256": source["sha256"], "kind": "cad_property", "locator": "generated handles", "observation": "synthetic curve inputs"}]}
    lp = put(folder / "ledger.json", ledger)
    profile = {"schema_version": "qto-pipe-recognition-profile/1.1", "profile_id": "P", "profile_version": "1", "expected_source_sha256": source["sha256"],
               "adjudication_ledger": {"ledger_id": "L", "ledger_version": "1", "sha256": symbol.digest(lp)}, "scope": {"layout": "Model"},
               "unit": {"output_unit": "m", "factor_to_output": 1, "source_id": "drawing-unit"},
               "layer_rules": [{"rule_id": "P", "adjudication_id": "P", "layer_pattern": "^PIPE$", "system": "W", "decision": "include", "allowed_entity_types": ["LINE", "ARC"]}],
               "block_rules": [], "text_rules": [], "use_conventional_terminal_type_candidates": False, "use_conventional_valve_type_candidates": False}
    if report_path:
        profile["symbol_census_report"] = {"path": str(report_path), "sha256": symbol.digest(report_path)}
    return put(folder / "profile.json", profile), lp


def test_pipe_cli_explicit_exclusion_preserves_same_layer_straight_and_true_arc(tmp_path):
    mp, ids = package(tmp_path / "a")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request); decide(request, report, ids["loose"])
    output = put(tmp_path / "report.json", symbol.build_census(request))
    pp, lp = pipe_files(tmp_path, mp, output)
    process = subprocess.run([sys.executable, "-B", str(PIPE / "build_pipe_inference_input.py"), "--cad-manifest", str(mp), "--profile", str(pp),
                              "--adjudication-ledger", str(lp), "--output-dir", str(tmp_path / "pipe")], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "pipe/管网推算输入.json").read_text(encoding="utf-8"))
    assert {s["segment_id"].removeprefix("SEG-") for s in result["segments"]} == {ids["straight"], ids["bend"]}
    assert sum(s["length"] for s in result["segments"]) == pytest.approx(9 + 3 * math.pi)
    recognition = json.loads((tmp_path / "pipe/管网识别报告.json").read_text(encoding="utf-8"))
    assert recognition["counts"]["symbol_explicitly_excluded_objects"] == 5
    assert recognition["disposition_closure_complete"]


def test_candidates_do_not_remove_any_curve_from_pipe_cli(tmp_path):
    mp, ids = package(tmp_path / "a")
    output = put(tmp_path / "report.json", symbol.build_census(request_for(tmp_path / "request.json", [("A", mp)])))
    pp, lp = pipe_files(tmp_path, mp, output)
    process = subprocess.run([sys.executable, "-B", str(PIPE / "build_pipe_inference_input.py"), "--cad-manifest", str(mp), "--profile", str(pp),
                              "--adjudication-ledger", str(lp), "--output-dir", str(tmp_path / "pipe")], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "pipe/管网推算输入.json").read_text(encoding="utf-8"))
    assert {s["segment_id"].removeprefix("SEG-") for s in result["segments"]} == set(ids["loose"] + [ids["straight"], ids["bend"]])


def test_census_cli_partial_exit_and_output(tmp_path):
    mp, _ = package(tmp_path / "a", incomplete=True)
    request = request_for(tmp_path / "request.json", [("A", mp)])
    output = tmp_path / "普查.json"
    process = subprocess.run([sys.executable, "-B", str(SHARED / "census_cad_symbols.py"), "--request", str(request), "--output", str(output)],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 3, process.stdout + process.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["census_status"] == "PARTIAL"


def test_pipe_batch_cli_uses_same_explicit_object_exclusion(tmp_path):
    mp, ids = package(tmp_path / "a")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request); decide(request, report, ids["loose"])
    output = put(tmp_path / "report.json", symbol.build_census(request))
    manifest = json.loads(mp.read_text()); source = manifest["authoritative_source"]
    mapping = {"schema_version": "qto-pipe-batch-family-mapping/1.0", "mapping_id": "MAP", "mapping_version": "1",
               "expected_source_sha256": source["sha256"], "recognized_by": "test model", "recognized_at": "2026-10-19", "source_id": "anonymous",
               "evidence": [{"evidence_id": "RAW", **source, "kind": "cad_property", "locator": "generated source objects", "observation": "generated pipe layout"}],
               "unit": {"factor_to_output": 1, "output_unit": "m", "source_id": "RAW", "raw_unit": "m"},
               "families": [{"family_id": "PIPE", "kind": "layer", "layer_pattern": "^PIPE$", "system": "W", "decision": "include",
                             "evidence_ids": ["RAW"], "basis": "working layer recognition", "allowed_entity_types": ["LINE", "ARC"]}],
               "symbol_census_report": {"path": str(output), "sha256": symbol.digest(output)},
               "use_conventional_terminal_type_candidates": False, "use_conventional_valve_type_candidates": False}
    mapping_path = put(tmp_path / "mapping.json", mapping)
    frames = put(tmp_path / "frames.json", {"schema_version": "qto-pipe-batch-frames/1.0", "frames": [{"frame_id": "F1", "name": "Generated", "bounds": [-10, -10, 200, 100]}]})
    process = subprocess.run([sys.executable, "-B", str(PIPE / "build_pipe_inference_inputs_batch.py"), "--cad-manifest", str(mp), "--mapping", str(mapping_path),
                              "--frames", str(frames), "--output-dir", str(tmp_path / "batch")], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "batch/F1/管网推算输入.json").read_text(encoding="utf-8"))
    assert {s["segment_id"].removeprefix("SEG-") for s in result["segments"]} == {ids["straight"], ids["bend"]}


def test_output_protection_and_complete_positive_cli(tmp_path):
    mp, ids = package(tmp_path / "a", include_stretch=False)
    manifest = json.loads(mp.read_text()); lp = Path(manifest["artifacts"]["layout_entities"]["path"])
    request = request_for(tmp_path / "request.json", [("A", mp)])
    command = [sys.executable, "-B", str(SHARED / "census_cad_symbols.py"), "--request", str(request), "--output"]
    output = tmp_path / "complete.json"
    process = subprocess.run(command + [str(output)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["census_status"] == "SCOPED_CENSUS_COMPLETE"
    before = symbol.digest(lp)
    process = subprocess.run(command + [str(lp)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 2 and symbol.digest(lp) == before


def test_ambiguous_legend_keeps_all_same_row_symbols(tmp_path):
    mp, _ = package(tmp_path / "a", include_stretch=False)
    request = request_for(tmp_path / "request.json", [("A", mp)])
    value = json.loads(request.read_text()); value["legends"] = [{"source_id": "A", "layout": "Model", "bounds": [0, 0, 100, 60], "row_tolerance": 50, "max_left_distance": 100}]
    put(request, value)
    report = symbol.build_census(request)
    assert all(p["state"] == "ambiguous" and len(p["symbols"]) > 1 for p in report["legend_pairs"])
    assert all(not s["legend_text_candidates"] for s in report["shapes"])


def test_invalid_request_collections_and_cross_stream_duplicate_identity(tmp_path):
    mp, _ = package(tmp_path / "a")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    value = json.loads(request.read_text()); value["evidence"] = {"whole_layer": "PIPE"}; put(request, value)
    with pytest.raises(ValueError, match="list of objects"): symbol.build_census(request)
    manifest = json.loads(mp.read_text())
    lp = Path(manifest["artifacts"]["layout_entities"]["path"]); bp = Path(manifest["artifacts"]["block_definitions"]["path"])
    rows = symbol.load_rows(bp, manifest["authoritative_source"]["sha256"])
    rows[0]["record_id"] = symbol.load_rows(lp, manifest["authoritative_source"]["sha256"])[0]["record_id"]
    bp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    manifest["artifacts"]["block_definitions"]["sha256"] = symbol.digest(bp); put(mp, manifest)
    request_for(request, [("A", mp)])
    with pytest.raises(ValueError, match="repeated across"): symbol.build_census(request)


@pytest.mark.parametrize("field", ["geometry", "raw_length", "layer", "instance_transform"])
def test_rehashed_prescoped_index_cannot_reuse_exclusion_for_changed_curve(tmp_path, field):
    # Reuse the complete batch CLI to obtain a genuine legal index and header.
    test_pipe_batch_cli_uses_same_explicit_object_exclusion(tmp_path)
    frame = tmp_path / "batch/F1"
    profile = json.loads((frame / "识别配置.json").read_text(encoding="utf-8"))
    source_report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    target = source_report["explicit_dispositions"][0]["object_id"]
    index_path = Path(profile["prescoped_rows"]["path"])
    if not index_path.is_absolute(): index_path = frame / index_path
    records = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line]
    row = next(r for r in records if r.get("record_id") == target)
    if field == "geometry": row["geometry"]["parts"][0]["end"] = [999, 998, 0]
    elif field == "raw_length": row["raw_length"] = 999
    elif field == "layer": row["layer"] = "OTHER"
    else: row["instance_transform"] = list(symbol.geo.IDENTITY)
    edited = tmp_path / "edited-index.ndjson"
    edited.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    profile["prescoped_rows"] = {"path": str(edited), "sha256": symbol.digest(edited)}
    pp = put(tmp_path / "edited-profile.json", profile)
    process = subprocess.run([sys.executable, "-B", str(PIPE / "build_pipe_inference_input.py"), "--cad-manifest", str(tmp_path / "a/manifest.json"),
                              "--profile", str(pp), "--adjudication-ledger", str(frame / "模型工作识别记录.json"), "--output-dir", str(tmp_path / "attack")],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 2, process.stdout + process.stderr
    assert "source snapshot differs" in process.stderr
    assert not (tmp_path / "attack/管网推算输入.json").exists()


def test_nested_transformed_curve_snapshots_match_direct_and_prescoped_paths(tmp_path):
    mp, _ = package(tmp_path / "a", include_stretch=False, nested_pipe=True)
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request)
    targets = [o["object_id"] for o in report["objects"] if o["entity_type"] == "LINE" and o["instance_path"] and o["layer"] == "PIPE"]
    assert len(targets) == 5
    decide(request, report, targets)
    output = put(tmp_path / "report.json", symbol.build_census(request))
    context = symbol.pipe_context({"path": str(output), "sha256": symbol.digest(output)}, mp, "Model")
    spec = importlib.util.spec_from_file_location("snapshot_pipe_adapter", PIPE / "build_pipe_inference_input.py")
    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    manifest = json.loads(mp.read_text()); paths = manifest["artifacts"]
    rows = list(adapter.expanded_manifest_rows(Path(paths["layout_entities"]["path"]), Path(paths["block_definitions"]["path"]), manifest, 16, []))
    actual = [r for r in rows if r["record_id"] in targets]
    assert len(actual) == 5 and all(symbol.pipe_exclusion(r, context) is not None for r in actual)
    pp, lp = pipe_files(tmp_path, mp, output)
    process = subprocess.run([sys.executable, "-B", str(PIPE / "build_pipe_inference_input.py"), "--cad-manifest", str(mp), "--profile", str(pp),
                              "--adjudication-ledger", str(lp), "--output-dir", str(tmp_path / "direct")], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 0, process.stdout + process.stderr
    recognition = json.loads((tmp_path / "direct/管网识别报告.json").read_text(encoding="utf-8"))
    assert recognition["counts"]["symbol_explicitly_excluded_objects"] == 5
    header = {"row_type": "prescoped_header", "schema_version": adapter.PRESCOPED_SCHEMA,
              "cad_manifest_sha256": symbol.digest(mp), "layout_entities_sha256": paths["layout_entities"]["sha256"],
              "block_definitions_sha256": paths["block_definitions"]["sha256"], "layout": "Model", "bounds": None,
              "max_depth": 16, "block_expansion_failures": [], "source_total_rows": len(rows), "row_count": len(rows)}
    frozen_profile = json.loads(pp.read_text())
    for changed in (False, True):
        indexed = copy.deepcopy(rows)
        if changed:
            next(r for r in indexed if r["record_id"] == targets[0])["geometry"]["parts"][0]["end"] = [500, 501, 0]
        index = tmp_path / ("nested-changed.ndjson" if changed else "nested-valid.ndjson")
        index.write_text("".join(json.dumps(r) + "\n" for r in [header, *indexed]), encoding="utf-8")
        indexed_profile = copy.deepcopy(frozen_profile); indexed_profile["prescoped_rows"] = {"path": str(index), "sha256": symbol.digest(index)}
        indexed_path = put(tmp_path / ("nested-changed.json" if changed else "nested-valid.json"), indexed_profile)
        process = subprocess.run([sys.executable, "-B", str(PIPE / "build_pipe_inference_input.py"), "--cad-manifest", str(mp), "--profile", str(indexed_path),
                                  "--adjudication-ledger", str(lp), "--output-dir", str(tmp_path / ("indexed-changed" if changed else "indexed-valid"))],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
        assert process.returncode == (2 if changed else 0), process.stdout + process.stderr


def incomplete_geometry_package(folder, include_bad=True):
    folder.mkdir(parents=True)
    doc = ezdxf.new(); doc.units = 6; m = doc.modelspace()
    empty = doc.blocks.new("EMPTY"); m.add_blockref(empty.name, (100, 100))
    large = doc.blocks.new("LARGE_COMPLETE")
    for i in range(260): large.add_line((i, 0), (i, 1))
    m.add_blockref(large.name, (200, 200))
    m.add_line((0, 10), (9, 10), dxfattribs={"layer": "PIPE"})
    m.add_arc((0, 20), 2, 0, 270, dxfattribs={"layer": "PIPE"})
    if include_bad:
        bad = doc.blocks.new("BAD_CONTENT"); bad.add_spline([(0, 0), (1, 1), (2, 0)])
        bad.add_line((10, 0), (12, 0))  # healthy sibling must keep its own usable state
        m.add_blockref(bad.name, (300, 300))
        m.add_spline([(0, 0), (1, 1), (2, 0)])
        m.add_lwpolyline([(400, 400), (400, 400)])
    source = folder / "anonymous.dxf"; doc.saveas(source); h = symbol.digest(source)
    layout = [extract.convert(e, layout="Model", parent_block=None, source_hash=h, raw_unit="m", sequence=i) for i, e in enumerate(m)]
    definitions, blocks = [], []
    for b in doc.blocks:
        if b.is_any_layout: continue
        blocks.append({"block_name": b.name, "origin": list(b.block.dxf.base_point), "serialized": True, "direct_entity_count": len(b)})
        definitions.extend(extract.convert(e, layout="BlockDefinition", parent_block=b.name, source_hash=h, raw_unit="m", sequence=i) for i, e in enumerate(b))
    lp = folder / "layout.ndjson"; bp = folder / "blocks.ndjson"
    for path, rows in ((lp, layout), (bp, definitions)): path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    # Intentionally optimistic upstream summary: per-object unsupported facts stay unchanged.
    return put(folder / "manifest.json", {"schema_version": "qto-cad-neutral-evidence/1.0", "status": "PASS", "layouts": ["Model"],
        "authoritative_source": {"path": str(source), "sha256": h}, "exchange_dxf": {"path": str(source), "sha256": h}, "blocks": blocks,
        "artifacts": {"layout_entities": {"path": str(lp), "sha256": symbol.digest(lp), "count": len(layout)},
                      "block_definitions": {"path": str(bp), "sha256": symbol.digest(bp), "count": len(definitions)}}})


def test_object_curve_limits_override_aggregate_pass_and_propagate_to_parent(tmp_path):
    mp = incomplete_geometry_package(tmp_path / "source")
    request = request_for(tmp_path / "request.json", [("A", mp)])
    value = json.loads(request.read_text()); value["legends"] = []; put(request, value)
    report = symbol.build_census(request)
    assert report["census_status"] == "PARTIAL"
    bad = [o for o in report["objects"] if o["entity_type"] in {"SPLINE", "LWPOLYLINE"} or o["block_name"] == "BAD_CONTENT"]
    assert len(bad) == 4 and all(o["issues"] for o in bad)
    for target in bad:
        clean = tmp_path / "attempt.json"; put(clean, value); decide(clean, report, [target["object_id"]])
        with pytest.raises(ValueError, match="incomplete"): symbol.build_census(clean)
    healthy = next(o for o in report["objects"] if o["entity_type"] == "LINE" and o["owner_insert_id"] and o["layer"] == "0" and o["source_record_id"] != "")
    # The large block's complete lines and the healthy sibling of an unread spline stay usable.
    assert not healthy["issues"]
    healthy_sibling = next(o for o in report["objects"] if o["entity_type"] == "LINE" and o["owner_insert_id"] == next(p["object_id"] for p in bad if p["block_name"] == "BAD_CONTENT"))
    assert not healthy_sibling["issues"]
    decide(request, report, [healthy_sibling["object_id"]])
    assert len(symbol.build_census(request)["explicit_dispositions"]) == 1


def test_signature_resource_limit_and_empty_definition_are_not_missing_geometry(tmp_path):
    mp = incomplete_geometry_package(tmp_path / "source", include_bad=False)
    request = request_for(tmp_path / "request.json", [("A", mp)])
    value = json.loads(request.read_text()); value["legends"] = []; put(request, value)
    report = symbol.build_census(request)
    assert report["census_status"] == "SCOPED_CENSUS_COMPLETE"
    assert not any(o["issues"] for o in report["objects"])
    large = next(o for o in report["objects"] if o["block_name"] == "LARGE_COMPLETE")
    assert any("oversized signature" in issue for issue in shape(report, large["object_id"])["issues"])
    empty = next(o for o in report["objects"] if o["block_name"] == "EMPTY")
    assert not empty["issues"] and not shape(report, empty["object_id"])["geometry_object_ids"]


@pytest.mark.parametrize("kind", ["source", "manifest", "view", "layout", "blocks", "request"])
def test_output_hard_link_to_any_protected_input_is_rejected_without_byte_change(tmp_path, kind):
    mp, _ = package(tmp_path / "source", include_stretch=False)
    request = request_for(tmp_path / "request.json", [("A", mp)])
    report = symbol.build_census(request)
    manifest = json.loads(mp.read_text())
    if kind == "source": target = Path(manifest["authoritative_source"]["path"])
    elif kind == "manifest": target = mp
    elif kind == "layout": target = Path(manifest["artifacts"]["layout_entities"]["path"])
    elif kind == "blocks": target = Path(manifest["artifacts"]["block_definitions"]["path"])
    elif kind == "request": target = request
    else:
        target = tmp_path / "drawing-view.png"
        target.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        row = next(o for o in report["objects"] if o["entity_type"] == "ARC")
        value = json.loads(request.read_text())
        value["evidence"] = [{"evidence_id": "VIEW", "source_id": "A", "object_id": row["object_id"], "row_sha256": row["row_sha256"],
                              "kind": "drawing_view", "basis": "anonymous source view for input preservation test", "view": {"path": str(target), "sha256": symbol.digest(target)}}]
        put(request, value)
    alias = tmp_path / "output-alias.json"
    # A real same-volume file identity check, not a mocked samefile result or skip.
    os.link(target, alias)
    assert alias.samefile(target) and alias.resolve() != target.resolve()
    before = target.read_bytes()
    command = [sys.executable, "-B", str(SHARED / "census_cad_symbols.py"), "--request", str(request), "--output"]
    regular = tmp_path / "normal-report.json"
    for _ in range(2):  # new report and existing normal report must both work
        process = subprocess.run(command + [str(regular)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
        assert process.returncode == 0, process.stdout + process.stderr
        assert target.read_bytes() == before
    process = subprocess.run(command + [str(alias)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert process.returncode == 2, process.stdout + process.stderr
    assert target.read_bytes() == before and alias.read_bytes() == before
    assert "cannot overwrite" in process.stdout
