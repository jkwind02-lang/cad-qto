"""Rendering evidence, including pixels; no engineering interpretations supplied."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET

import ezdxf
import numpy as np
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/_qto-shared-evidence/scripts"
sys.path.insert(0, str(SCRIPTS))
import render_cad_view as view
import qto_recognition_workbench as workbench


def source(doc, tmp_path):
    path = tmp_path / "source.dxf"
    doc.saveas(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def draw(src, tmp_path, **query):
    query = {"question": "观察图样中的线型、符号和标注", "background": "white", **query}
    return view.render_view(src, query, svg_path=tmp_path / "view.svg", png_path=tmp_path / "view.png",
                            output_path=tmp_path / "view.json", pixels=1200)


def pixels(tmp_path):
    return np.asarray(Image.open(tmp_path / "view.png").convert("RGB"))


def color_mask(array, channel):
    others = [i for i in range(3) if i != channel]
    # The source's 0.25 mm stroke can straddle pixels; retain colored antialiasing.
    return (array[:, :, channel] > 200) & (array[:, :, others[0]] < 195) & (array[:, :, others[1]] < 195)


def run_count(mask):
    values = mask.any(axis=0).astype(np.int8)
    return int((np.diff(np.pad(values, (1, 1))) == 1).sum())


def test_png_keeps_source_colors_and_actual_dash_gaps(tmp_path):
    doc = ezdxf.new()
    doc.linetypes.add("KNOWN_DASH", pattern=[10, 6, -4])
    doc.layers.add("red-dash", color=1, linetype="KNOWN_DASH")
    doc.layers.add("blue-solid", color=5)
    model = doc.modelspace()
    model.add_line((10, 10), (110, 10), dxfattribs={"layer": "red-dash"})
    model.add_line((10, 25), (110, 25), dxfattribs={"layer": "blue-solid"})
    src = source(doc, tmp_path)
    result = draw(src, tmp_path, bounds=[0, 0, 120, 40])
    array = pixels(tmp_path)
    red, blue = color_mask(array, 0), color_mask(array, 2)
    assert red.sum() > 100 and blue.sum() > 100
    assert run_count(red) >= 8
    assert run_count(blue) == 1
    svg = ET.parse(tmp_path / "view.svg")
    assert any(e.get("stroke") == "#ff0000" for e in svg.iter())
    assert not any(e.get("class") for e in svg.iter())
    assert result["kind"] == "derived_dxf_drawing_view"
    assert result["renderer"]["png_pixels"][0] == 1200
    assert hashlib.sha256(Path(src["path"]).read_bytes()).hexdigest() == src["sha256"]


def test_native_text_rotation_and_relative_position_survive_png(tmp_path):
    doc = ezdxf.new()
    model = doc.modelspace()
    model.add_text("ABCD", dxfattribs={"height": 4, "insert": (10, 10), "rotation": 0, "color": 3})
    model.add_text("ABCD", dxfattribs={"height": 4, "insert": (65, 10), "rotation": 90, "color": 1})
    draw(source(doc, tmp_path), tmp_path, bounds=[0, 0, 100, 50])
    array = pixels(tmp_path)
    yr, xr = np.where(color_mask(array, 0))
    yg, xg = np.where(color_mask(array, 1))
    assert yr.size and yg.size
    assert np.ptp(yr) > 2 * np.ptp(xr)
    assert np.ptp(xg) > 2 * np.ptp(yg)
    assert xr.min() > xg.max()


def test_bounds_are_actual_crop_not_only_fitted_extents(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 10), (30, 10), dxfattribs={"color": 1})
    doc.modelspace().add_line((100, 10), (130, 10), dxfattribs={"color": 5})
    result = draw(source(doc, tmp_path), tmp_path, bounds=[-5, 0, 40, 20])
    array = pixels(tmp_path)
    assert color_mask(array, 0).any() and not color_mask(array, 2).any()
    assert result["bounds_raw"] == [-5, 0, 40, 20]
    assert result["drawn_bounds_before_crop_raw"][2] == 130
    assert "before_window_crop" in result["diagnostics_scope"]


def test_layers_resolve_block_zero_and_do_not_unfreeze_hidden_layer(tmp_path):
    doc = ezdxf.new()
    doc.layers.add("Container", color=1)
    doc.layers.add("Pipe", color=5)
    doc.layers.add("Hidden", color=3)
    doc.layers.get("Hidden").off()
    block = doc.blocks.new("drawing-shape")
    block.add_line((0, 0), (50, 0), dxfattribs={"layer": "0"})
    block.add_line((0, 15), (50, 15), dxfattribs={"layer": "Pipe"})
    block.add_line((0, 30), (50, 30), dxfattribs={"layer": "Hidden"})
    doc.modelspace().add_blockref("drawing-shape", (10, 10), dxfattribs={"layer": "Container"})
    result = draw(source(doc, tmp_path), tmp_path, bounds=[0, 0, 80, 60], layers=["pipe", "Hidden"])
    array = pixels(tmp_path)
    assert color_mask(array, 2).any()
    assert not color_mask(array, 0).any() and not color_mask(array, 1).any()
    assert next(r for r in result["layers"] if r["name"] == "Hidden")["off"]


def test_model_chooses_paper_layout_without_object_assignments(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_circle((10, 10), 5, dxfattribs={"color": 1})
    sheet = doc.layouts.new("Equipment Detail")
    sheet.add_circle((30, 30), 8, dxfattribs={"color": 5})
    result = draw(source(doc, tmp_path), tmp_path, layout="Equipment Detail", bounds=[0, 0, 50, 50])
    array = pixels(tmp_path)
    assert color_mask(array, 2).any() and not color_mask(array, 0).any()
    assert result["layout"] == "Equipment Detail" and result["index_sha256"] is None


def test_missing_font_and_missing_character_are_disclosed(tmp_path):
    doc = ezdxf.new()
    doc.styles.new("missing", dxfattribs={"font": "qto-font-does-not-exist.ttf", "bigfont": "missing.shx"})
    doc.modelspace().add_text("DN32 \U0010ffff", dxfattribs={"height": 3, "style": "missing"})
    result = draw(source(doc, tmp_path), tmp_path)
    font = result["font_observations"][0]
    assert font["font_substitution"] and font["resolved_font"] != font["declared_font"]
    assert "U+10FFFF" in font["missing_codepoints"] and font["bigfont"] == "missing.shx"


def test_explicit_installed_font_override_is_recorded_and_does_not_edit_source(tmp_path):
    from ezdxf.fonts import fonts
    fonts.load()
    replacement = fonts.font_manager.fallback_font_name()
    doc = ezdxf.new()
    doc.modelspace().add_text("DN32", dxfattribs={"height": 3})
    src = source(doc, tmp_path)
    result = draw(src, tmp_path, font_overrides={"Standard": replacement})
    assert result["font_observations"][0]["explicit_override"] == replacement
    assert view._hash(src["path"]) == src["sha256"]


def test_external_image_is_only_frame_with_diagnostic(tmp_path):
    doc = ezdxf.new()
    image_def = doc.add_image_def(filename=str(tmp_path / "not-available.png"), size_in_pixel=(200, 100))
    doc.modelspace().add_image(image_def, insert=(10, 10), size_in_units=(40, 20))
    result = draw(source(doc, tmp_path), tmp_path, bounds=[0, 0, 70, 50])
    assert any(issue["entity_type"] == "IMAGE" for issue in result["display_issues"])
    assert result["renderer"]["external_images"] == "frames_only_not_loaded"
    assert not ET.parse(tmp_path / "view.svg").findall(".//{http://www.w3.org/2000/svg}image")


def test_unknown_underlay_and_spatial_text_remain_in_diagnostics(tmp_path):
    doc = ezdxf.new()
    underlay = doc.add_underlay_def("not-available.pdf", fmt="pdf", name="1")
    doc.modelspace().add_underlay(underlay, insert=(10, 10))
    doc.modelspace().add_text("3D TEXT", dxfattribs={"height": 3, "extrusion": (0, 1, 0)})
    result = draw(source(doc, tmp_path), tmp_path, bounds=[0, 0, 70, 50])
    assert any(row["entity_type"] == "PDFUNDERLAY" for row in result["display_issues"])
    assert any("3D text" in row["reason"] for row in result["skipped_entities"])


def test_complex_linetype_disclosure(tmp_path):
    doc = ezdxf.new(setup=True)
    doc.linetypes.new("TEXT_LINE", dxfattribs={"description": "text linetype",
        "pattern": 'A,.5,-.2,["X",STANDARD,S=.1],-.2', "length": 0.9})
    doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={"linetype": "TEXT_LINE"})
    result = draw(source(doc, tmp_path), tmp_path)
    assert any("TEXT_LINE" in row.get("linetypes", []) for row in result["display_issues"])


def test_source_and_existing_index_are_verified(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 0))
    src = source(doc, tmp_path)
    index = workbench.build_index(src)
    query = {"question": "观察来源", "bounds": [0, -5, 100, 5]}
    report = view.render_view(src, query, svg_path=tmp_path / "index-view.svg", index=index)
    assert report["index_sha256"] == index["index_sha256"]
    index["objects"][0]["text"] = "forged"
    with pytest.raises(ValueError, match="索引内容已改变"):
        view.render_view(src, query, svg_path=tmp_path / "forged.svg", index=index)
    Path(src["path"]).write_bytes(Path(src["path"]).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="已改变"):
        draw(src, tmp_path)


def test_authoritative_source_hash_and_conversion_sidecar_are_bound(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 0))
    src = source(doc, tmp_path)
    auth = tmp_path / "original.dwg"
    auth.write_bytes(b"test identity only, not a real DWG recognition acceptance")
    src["authoritative_source"] = {"path": str(auth), "sha256": view._hash(auth)}
    sidecar = Path(src["path"] + ".export.json")
    sidecar.write_text('{"test":"conversion record not validated by this renderer"}')
    result = draw(src, tmp_path)
    assert result["conversion"]["different_authoritative_file"]
    assert result["input_file_hashes"][str(sidecar)] == view._hash(sidecar)
    auth.write_bytes(b"changed")
    with pytest.raises(ValueError, match="权威来源已改变"):
        draw(src, tmp_path)


def test_mid_render_source_change_does_not_publish_view(tmp_path, monkeypatch):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 0))
    src = source(doc, tmp_path)
    original = view._renderer

    def changed(*args):
        result = original(*args)
        path = Path(src["path"])
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(view, "_renderer", changed)
    with pytest.raises(ValueError, match="已改变"):
        draw(src, tmp_path)
    assert not (tmp_path / "view.svg").exists() and not (tmp_path / "view.png").exists()


def test_source_change_between_initial_verification_and_hash_capture_is_rejected(tmp_path, monkeypatch):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 0))
    src = source(doc, tmp_path)
    original = view.verify_source_file

    def change_after_declared_verification(value, label):
        original(value, label)
        if label == "权威来源":
            path = Path(src["path"])
            path.write_bytes(path.read_bytes() + b"\n")

    monkeypatch.setattr(view, "verify_source_file", change_after_declared_verification)
    with pytest.raises(ValueError, match="声明校验与读取之间已改变"):
        draw(src, tmp_path)
    assert not (tmp_path / "view.svg").exists()


@pytest.mark.parametrize("changed_input", ["source", "index", "query"])
def test_cli_parsed_json_and_file_hash_use_same_bytes(tmp_path, monkeypatch, changed_input):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 0))
    src = source(doc, tmp_path)
    source_json = tmp_path / "source.json"
    source_json.write_text(json.dumps(src))
    index_json = tmp_path / "index.json"
    index_json.write_text(json.dumps(workbench.build_index(src)))
    query_json = tmp_path / "query.json"
    query_json.write_text(json.dumps({"question": "inspect"}))
    descriptor_flag = "--index" if changed_input == "index" else "--source"
    descriptor = index_json if changed_input == "index" else source_json
    changed_path = {"source": source_json, "index": index_json, "query": query_json}[changed_input]
    original = view._json_bound

    def changed(path):
        result = original(path)
        if Path(path) == changed_path:
            Path(path).write_bytes(Path(path).read_bytes() + b"\n")
        return result

    monkeypatch.setattr(view, "_json_bound", changed)
    monkeypatch.setattr(sys, "argv", ["render_cad_view.py", descriptor_flag, str(descriptor),
        "--query", str(query_json), "--svg", str(tmp_path / "view.svg"), "--output", str(tmp_path / "view.json")])
    assert view.main() == 2
    assert not (tmp_path / "view.svg").exists()


@pytest.mark.parametrize("alias", ["same-path", "hardlink", "symlink"])
def test_output_cannot_overwrite_evidence_or_alias(tmp_path, alias):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    src = source(doc, tmp_path)
    query_path = tmp_path / "query.json"
    query_path.write_text('{"question":"observe"}')
    output = query_path if alias == "same-path" else tmp_path / "alias.json"
    if alias == "hardlink":
        output.hardlink_to(query_path)
    elif alias == "symlink":
        output.symlink_to(query_path)
    before = query_path.read_bytes()
    with pytest.raises(ValueError):
        view.render_view(src, {"question": "observe"}, svg_path=tmp_path / "view.svg",
                         output_path=output, input_paths=[query_path])
    assert query_path.read_bytes() == before and not (tmp_path / "view.svg").exists()


@pytest.mark.parametrize("query", [
    {"question": ""}, {"question": "x", "bounds": [0, 0, 0, 1]},
    {"question": "x", "bounds": [0, 0, float("inf"), 1]},
    {"question": "x", "layers": ["absent"]}, {"question": "x", "layout": "absent"},
    {"question": "x", "font_overrides": {"Standard": "not-a-real-font.ttf"}},
    {"question": "x", "classify": "all-lines-as-pipes"},
])
def test_invalid_observation_query_fails_before_publishing(tmp_path, query):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    with pytest.raises(ValueError):
        view.render_view(source(doc, tmp_path), query, svg_path=tmp_path / "view.svg")
    assert not (tmp_path / "view.svg").exists()


def test_cli_relative_source_svg_and_png(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_circle((10, 10), 5, dxfattribs={"color": 1})
    src = source(doc, tmp_path)
    src["path"] = "source.dxf"  # Existing source descriptors resolve from cwd.
    (tmp_path / "source.json").write_text(json.dumps(src))
    (tmp_path / "query.json").write_text(json.dumps({"question": "inspect", "bounds": [0, 0, 20, 20]}))
    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "render_cad_view.py"),
        "--source", "source.json", "--query", "query.json", "--svg", "view.svg", "--png", "view.png",
        "--output", "view.json", "--pixels", "800"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "RENDERED"
    report = json.loads((tmp_path / "view.json").read_text())
    assert report["renderer"]["png_pixels"] == [800, 800]
    assert report["input_file_hashes"][str(tmp_path / "source.dxf")] == src["sha256"]

