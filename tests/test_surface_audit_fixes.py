"""Independent geometry audit regressions for layer, OCS and XREF coverage.

The fixtures describe simple rectangles with analytic dimensions. They verify
both quantities and source locations; an empty result cannot pass as zero area.
"""
import hashlib
import importlib.util
import math
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "surface_audit_fixes",
    ROOT / "skills/qto-surfaces-envelope/scripts/extract_surface_regions.py",
)
surface = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(surface)
RECTANGLE = [(0, 0), (4, 0), (4, 3), (0, 3)]


def drawing():
    doc = ezdxf.new("R2018")
    doc.units = 6
    for layer in ("FLOOR", "WALL", "REFERENCES"):
        doc.layers.new(layer)
    return doc


def add_hatch(layout, layer="0", points=RECTANGLE, edge_path=False, **attributes):
    entity = layout.add_hatch(dxfattribs={"layer": layer, **attributes})
    if edge_path:
        path = entity.paths.add_edge_path()
        for start, end in zip(points, points[1:] + points[:1]):
            path.add_line(start, end)
    else:
        entity.paths.add_polyline_path(points, is_closed=True)
    return entity


def extract(doc, tmp_path, layers="^FLOOR$", **options):
    path = tmp_path / "source.dxf"
    doc.saveas(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = surface.extract(path, layers, "m", "synthetic 4 m by 3 m rectangle", **options)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert result["source_sha256"] == before
    return result


def transform_points(points, insert=(0, 0), scale=(1, 1), degrees=0):
    """Independent 2D affine reference: scale, rotate, then translate."""
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [(insert[0] + c * x * scale[0] - s * y * scale[1],
             insert[1] + s * x * scale[0] + c * y * scale[1]) for x, y in points]


def assert_region_at(region, expected):
    actual = Polygon(region["polygons"][0]["exterior_m"])
    reference = Polygon(expected)
    assert actual.hausdorff_distance(reference) < 1e-9
    assert actual.symmetric_difference(reference).area < 1e-9
    assert region["bounds_m"] == pytest.approx(reference.bounds)
    assert region["locator_bounds_m"] == pytest.approx(reference.bounds)
    assert region["geometry_coordinate_system"] == "WCS_XY"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("child_layer,expected_layer", [("0", "FLOOR"), ("WALL", "WALL")])
def test_layer_zero_inherits_insert_layer_and_explicit_child_layer_wins(
    tmp_path, nested, child_layer, expected_layer
):
    doc = drawing()
    leaf = doc.blocks.new("leaf")
    add_hatch(leaf, child_layer)
    name = "leaf"
    if nested:
        middle = doc.blocks.new("middle")
        middle.add_blockref("leaf", (5, 7), dxfattribs={"layer": "0"})
        name = "middle"
    doc.modelspace().add_blockref(name, (10, 20), dxfattribs={"layer": "FLOOR"})
    result = extract(doc, tmp_path, "^" + expected_layer + "$")
    assert not result["failures"]
    assert len(result["regions"]) == 1
    region = result["regions"][0]
    assert region["layer"] == expected_layer
    assert region["raw_layer"] == child_layer
    assert region["area_m2"] == pytest.approx(12)
    assert region["perimeter_m"] == pytest.approx(14)
    assert_region_at(region, transform_points(RECTANGLE, (15, 27) if nested else (10, 20)))
    excluded = "WALL" if expected_layer == "FLOOR" else "FLOOR"
    assert not extract(doc, tmp_path, "^" + excluded + "$")["regions"]


def test_direct_entities_keep_their_own_layers(tmp_path):
    doc = drawing()
    add_hatch(doc.modelspace(), "FLOOR")
    add_hatch(doc.modelspace(), "0", transform_points(RECTANGLE, (10, 0)))
    result = extract(doc, tmp_path)
    assert len(result["regions"]) == 1
    assert result["regions"][0]["layer"] == "FLOOR"
    assert result["regions"][0]["raw_layer"] == "FLOOR"
    zero = extract(doc, tmp_path, "^0$")
    assert len(zero["regions"]) == 1
    assert zero["regions"][0]["layer"] == "0"


def test_nested_nonuniform_transform_preserves_intermediate_layer(tmp_path):
    doc = drawing()
    leaf = doc.blocks.new("leaf")
    add_hatch(leaf)
    middle = doc.blocks.new("middle")
    middle.add_blockref("leaf", (5, 7), dxfattribs={"layer": "WALL", "rotation": 30})
    doc.modelspace().add_blockref("middle", (10, 20), dxfattribs={
        "layer": "FLOOR", "xscale": -2, "yscale": .5, "rotation": 31,
    })
    result = extract(doc, tmp_path, "^WALL$")
    assert not result["failures"]
    assert len(result["regions"]) == 1
    region = result["regions"][0]
    expected = transform_points(transform_points(RECTANGLE, (5, 7), degrees=30),
                                (10, 20), (-2, .5), 31)
    assert region["layer"] == "WALL" and region["raw_layer"] == "0"
    assert region["area_m2"] == pytest.approx(12)
    assert_region_at(region, expected)
    assert not extract(doc, tmp_path)["regions"]


@pytest.mark.parametrize("edge_path", [False, True])
@pytest.mark.parametrize("normal_z", [1, -1, -2])
def test_axial_ocs_handles_sign_and_elevation_in_geometry_and_locator(
    tmp_path, edge_path, normal_z
):
    doc = drawing()
    points = transform_points(RECTANGLE, (2, 3))
    add_hatch(doc.modelspace(), "FLOOR", points, edge_path=edge_path,
              extrusion=(0, 0, normal_z), elevation=(0, 0, 7))
    result = extract(doc, tmp_path)
    assert not result["failures"]
    assert len(result["regions"]) == 1
    region = result["regions"][0]
    assert region["area_m2"] == pytest.approx(12)
    assert region["perimeter_m"] == pytest.approx(14)
    expected = [(x if normal_z > 0 else -x, y) for x, y in points]
    assert_region_at(region, expected)


def test_mirrored_minsert_measures_each_cell_and_uses_world_positions(tmp_path):
    doc = drawing()
    block = doc.blocks.new("panel")
    add_hatch(block)
    insert = doc.modelspace().add_blockref("panel", (1, 2), dxfattribs={
        "layer": "FLOOR", "rotation": 31, "xscale": -2, "yscale": .5,
    })
    insert.grid((2, 3), (10, 12))
    result = extract(doc, tmp_path)
    assert not result["failures"]
    assert len(result["regions"]) == 6
    assert len({r["region_id"] for r in result["regions"]}) == 6
    assert sum(r["area_m2"] for r in result["regions"]) == pytest.approx(72)
    for row in range(2):
        for column in range(3):
            origin = transform_points([(column * 12, row * 10)], (1, 2), degrees=31)[0]
            expected = transform_points(RECTANGLE, origin, (-2, .5), 31)
            region = result["regions"][row * 3 + column]
            assert region["layer"] == "FLOOR" and region["raw_layer"] == "0"
            assert_region_at(region, expected)


def test_nested_minsert_expands_inner_and_outer_cells(tmp_path):
    doc = drawing()
    leaf = doc.blocks.new("leaf")
    add_hatch(leaf)
    middle = doc.blocks.new("middle")
    middle.add_blockref("leaf", (0, 0)).grid((1, 2), (0, 5))
    doc.modelspace().add_blockref("middle", (0, 0), dxfattribs={"layer": "FLOOR"}).grid((2, 1), (10, 0))
    result = extract(doc, tmp_path)
    assert not result["failures"]
    assert len(result["regions"]) == 4
    assert len({r["region_id"] for r in result["regions"]}) == 4
    assert sum(r["area_m2"] for r in result["regions"]) == pytest.approx(48)
    assert sorted(tuple(r["bounds_m"]) for r in result["regions"]) == [
        (0, 0, 4, 3), (0, 10, 4, 13), (5, 0, 9, 3), (5, 10, 9, 13),
    ]


def test_other_supported_block_curves_do_not_create_false_surface_failures(tmp_path):
    doc = drawing()
    block = doc.blocks.new("panel")
    add_hatch(block)
    block.add_circle((2, 1), 1)
    block.add_lwpolyline([(0, 0, 1), (2, 0, 0)], format="xyb")
    doc.modelspace().add_blockref("panel", (10, 20), dxfattribs={
        "layer": "FLOOR", "xscale": 2, "yscale": .5,
    })
    result = extract(doc, tmp_path)
    assert not result["failures"]
    assert result["status"] == "selected_regions_analyzed"
    assert len(result["regions"]) == 1
    assert result["regions"][0]["area_m2"] == pytest.approx(12)


def test_tilted_plane_is_explicit_failure_with_projected_world_locator(tmp_path):
    doc = drawing()
    add_hatch(doc.modelspace(), "FLOOR", transform_points(RECTANGLE, (2, 3)),
              extrusion=(0, .6, .8), elevation=(0, 0, 7))
    result = extract(doc, tmp_path)
    assert not result["regions"]
    assert result["status"] == "partial_selected_regions"
    assert len(result["failures"]) == 1
    failure = result["failures"][0]
    assert failure["reason"] == "non_xy_plane"
    assert "area_m2" not in failure
    assert failure["locator_bounds_m"] == pytest.approx([-6, -.6, -2, 1.8])
    assert failure["locator_coordinate_system"] == "WCS_XY_projection"
    assert result["coverage"]["selected_hatches_seen"] == 1
    assert result["coverage"]["selected_hatch_failures"] == 1


def test_mirrored_open_boundary_keeps_failure_and_candidate_segments_in_wcs(tmp_path):
    doc = drawing()
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "FLOOR", "extrusion": (0, 0, -1)})
    path = hatch.paths.add_edge_path()
    for a, b in [((2, 3), (6, 3)), ((6, 3), (6, 6)), ((6, 6), (2, 6))]:
        path.add_line(a, b)
    strict = extract(doc, tmp_path)
    assert strict["failures"][0]["reason"] == "open_boundary"
    assert strict["failures"][0]["locator_bounds_m"] == pytest.approx([-6, 3, -2, 6])
    candidate = extract(doc, tmp_path, allow_implicit=True, max_implicit_gap_m=3)
    region = candidate["regions"][0]
    assert region["status"] == "candidate_implicit_segments"
    segment = region["implicit_segments"][0]
    assert segment["start_m"] == pytest.approx([-2, 6])
    assert segment["end_m"] == pytest.approx([-2, 3])
    assert segment["length_m"] == pytest.approx(3)


@pytest.mark.parametrize("flags", [4, 8])
def test_unloaded_xref_is_visible_outside_layer_filter_and_empty_local_block_is_not_xref(
    tmp_path, flags
):
    doc = drawing()
    add_hatch(doc.modelspace(), "FLOOR")
    doc.blocks.new("ordinary_empty")
    doc.modelspace().add_blockref("ordinary_empty", (0, 0), dxfattribs={"layer": "REFERENCES"})
    control = extract(doc, tmp_path)
    assert not control["failures"]
    assert control["status"] == "selected_regions_analyzed"
    doc.blocks.new("unloaded", dxfattribs={"flags": flags, "xref_path": "missing-floor.dwg"})
    doc.modelspace().add_blockref("unloaded", (0, 0), dxfattribs={"layer": "REFERENCES"})
    result = extract(doc, tmp_path)
    assert len(result["regions"]) == 1
    assert result["regions"][0]["area_m2"] == pytest.approx(12)
    assert len(result["failures"]) == 1
    failure = result["failures"][0]
    assert failure["reason"] == "xref_content_unavailable"
    assert failure["layer"] == "REFERENCES"
    assert failure["block_reference"]["block_name"] == "unloaded"
    assert failure["block_reference"]["xref_path"] == "missing-floor.dwg"
    assert failure["block_reference"]["direct_entity_count"] == 0
    assert result["status"] == "partial_selected_regions"
    assert result["coverage"]["selected_hatches_seen"] == 1
    assert result["coverage"]["selected_hatch_failures"] == 0
    assert result["coverage"]["unresolved_insert_instances"] == 1
    assert result["coverage"]["unseen_entities_possible"]


def test_cached_xref_remains_candidate_even_with_resolved_flag(tmp_path):
    doc = drawing()
    block = doc.blocks.new("cached", dxfattribs={"flags": 4 | 32, "xref_path": "floor.dwg"})
    add_hatch(block)
    doc.modelspace().add_blockref("cached", (10, 20), dxfattribs={"layer": "FLOOR"})
    result = extract(doc, tmp_path)
    assert result["failures"][0]["reason"] == "xref_identity_not_verified"
    assert result["failures"][0]["block_reference"]["definition_state"] == "cached_xref_children"
    region = result["regions"][0]
    assert region["status"] == "candidate_external_reference_unverified"
    assert region["area_m2"] == pytest.approx(12)
    assert_region_at(region, transform_points(RECTANGLE, (10, 20)))
    assert region["unverified_reference_paths"] == [result["failures"][0]["entity_path"]]
    assert surface.measure_region_group(result["regions"])["candidate_region_ids"] == [region["region_id"]]


def test_nested_unloaded_xref_reports_each_minsert_instance(tmp_path):
    doc = drawing()
    doc.blocks.new("unloaded", dxfattribs={"flags": 4, "xref_path": "missing.dwg"})
    middle = doc.blocks.new("middle")
    middle.add_blockref("unloaded", (1, 2), dxfattribs={"layer": "0"})
    doc.modelspace().add_blockref("middle", (0, 0), dxfattribs={"layer": "FLOOR"}).grid((2, 2), (10, 12))
    result = extract(doc, tmp_path)
    assert not result["regions"]
    assert len(result["failures"]) == 4
    assert len({r["entity_path"] for r in result["failures"]}) == 4
    assert {r["reason"] for r in result["failures"]} == {"xref_content_unavailable"}
    assert {r["layer"] for r in result["failures"]} == {"FLOOR"}
    assert result["coverage"]["unresolved_insert_instances"] == 4


@pytest.mark.parametrize("cyclic", [False, True])
def test_missing_or_recursive_block_definition_leaves_explicit_partial_coverage(tmp_path, cyclic):
    doc = drawing()
    if cyclic:
        block = doc.blocks.new("unresolved")
        block.add_blockref("unresolved", (0, 0))
    doc.modelspace().add_blockref("unresolved", (0, 0), dxfattribs={"layer": "FLOOR"})
    result = extract(doc, tmp_path)
    assert not result["regions"]
    assert len(result["failures"]) == 1
    assert result["failures"][0]["reason"] == ("cyclic_block_reference" if cyclic else "block_definition_missing")
    assert result["status"] == "partial_selected_regions"
    assert result["coverage"]["unseen_entities_possible"]
