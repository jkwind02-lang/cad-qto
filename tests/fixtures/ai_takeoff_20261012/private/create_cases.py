#!/usr/bin/env python3
"""Author-only original drawings and hand-derived oracle. No QTO imports.

The solver receives inputs/ only. This program, parameters, expected decisions,
and oracle remain private until the solver's candidate is frozen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

BASE = Path(__file__).resolve().parents[1]
PUBLIC = BASE / "inputs"
PRIVATE = BASE / "private"
PARAMETERS = {
    "pipe": {
        "lower_plan": [[0, 0], [3600, 0], [3600, 2400]],
        "upper_plan": [[3600, 2400], [5900, 2400], [8300, 2400], [8300, 4300]],
        "lower_pipe_cl_m": "3.15", "upper_pipe_cl_m": "7.85",
        "lower_floor_m": "0.00", "upper_floor_m": "4.60",
        "upstream_dn": "DN50", "downstream_dn": "DN32",
    },
    "surfaces": {
        "width_mm": 6800, "height_mm": 4100, "split_mm": 4400,
        "shaft": [800, 2200, 1500, 3100],
        "access": [5500, 2500, 6000, 3200],
        "screen_y_mm": 1400, "screen_height_mm": 1700,
        "ceiling_z_mm": 2800, "structure_z_mm": 3650,
    },
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def new_doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 4
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LUNITS"] = 2
    doc.header["$LUPREC"] = 2
    doc.styles.get("Standard").dxf.font = "DejaVuSans.ttf"
    layers = {
        "PIPING": (5, "CONTINUOUS"), "ANNOTATION": (7, "CONTINUOUS"),
        "DIMENSION": (8, "CONTINUOUS"), "CONTEXT": (8, "DASHED"),
        "SYMBOL": (7, "CONTINUOUS"), "BORDER": (8, "CONTINUOUS"),
        "FINISH": (3, "CONTINUOUS"), "OPENINGS": (1, "CONTINUOUS"),
        "FILL-A": (254, "CONTINUOUS"), "FILL-B": (253, "CONTINUOUS"),
    }
    for name, (color, ltype) in layers.items():
        doc.layers.new(name, dxfattribs={"color": color, "linetype": ltype})
    return doc


def line(space, a, b, layer="ANNOTATION", **attributes):
    return space.add_line(a, b, dxfattribs={"layer": layer, **attributes})


def poly(space, pts, layer="ANNOTATION", close=False, **attributes):
    return space.add_lwpolyline(pts, close=close, dxfattribs={"layer": layer, **attributes})


def rectangle(space, box, layer="ANNOTATION", **attributes):
    x0, y0, x1, y1 = box
    return poly(space, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer, True, **attributes)


def text(space, value, xy, size=180, layer="ANNOTATION", align=TextEntityAlignment.LEFT, **attributes):
    return space.add_text(value, dxfattribs={"height": size, "layer": layer, **attributes}).set_placement(xy, align=align)


def notes(space, rows, start, size=170, leading=310):
    for i, row in enumerate(rows):
        text(space, row, (start[0], start[1] - i * leading), size)


def dot(space, xy, radius=38):
    space.add_circle(xy, radius, dxfattribs={"layer": "SYMBOL"})


def dimension(space, a, b, offset):
    # Explicit extension/tick/text geometry, with no proprietary DIMENSION data.
    if a[1] == b[1]:
        y = a[1] + offset
        line(space, (a[0], a[1]), (a[0], y + 70), "DIMENSION")
        line(space, (b[0], b[1]), (b[0], y + 70), "DIMENSION")
        line(space, (a[0], y), (b[0], y), "DIMENSION")
        for x in (a[0], b[0]):
            line(space, (x - 55, y - 55), (x + 55, y + 55), "DIMENSION")
        text(space, str(abs(b[0] - a[0])), ((a[0] + b[0]) / 2, y + 65), 130,
             "DIMENSION", TextEntityAlignment.CENTER)
    elif a[0] == b[0]:
        x = a[0] + offset
        line(space, (a[0], a[1]), (x + 70, a[1]), "DIMENSION")
        line(space, (b[0], b[1]), (x + 70, b[1]), "DIMENSION")
        line(space, (x, a[1]), (x, b[1]), "DIMENSION")
        for y in (a[1], b[1]):
            line(space, (x - 55, y - 55), (x + 55, y + 55), "DIMENSION")
        text(space, str(abs(b[1] - a[1])), (x + 95, (a[1] + b[1]) / 2), 130, "DIMENSION", rotation=90)
    else:
        raise ValueError("Author dimensions must be axis aligned")


def offset_points(points, origin):
    return [(x + origin[0], y + origin[1]) for x, y in points]


def riser(space, xy, label="R-A", label_offset=(190, 180)):
    space.add_circle(xy, 130, dxfattribs={"layer": "SYMBOL"})
    text(space, label, (xy[0] + label_offset[0], xy[1] + label_offset[1]), 170)


def pipe_case():
    p = PARAMETERS["pipe"]
    directory = PUBLIC / "pipe"
    directory.mkdir(parents=True, exist_ok=True)
    doc = new_doc()
    ms = doc.modelspace()
    rectangle(ms, [0, 0, 43800, 16700], "BORDER")
    text(ms, "P-01 | WEST SERVICE SUPPLY | REV A | SYNTHETIC DESIGN", (750, 16000), 340)
    text(ms, "Native CAD: millimetres. Plans are full-size. System diagram is NOT TO SCALE.", (750, 15450), 230)

    lower_origin, upper_origin = (1800, 6700), (16100, 6700)
    lower = offset_points(p["lower_plan"], lower_origin)
    upper = offset_points(p["upper_plan"], upper_origin)
    text(ms, "LOWER LEVEL PLAN / F1", (1200, 14100), 290)
    text(ms, "UPPER LEVEL PLAN / F2", (16300, 14100), 290)
    text(ms, "SYSTEM DIAGRAM / NOT TO SCALE", (30900, 14100), 280)
    # Context includes an architectural grid passing through the pipe route.
    for ox, oy, title in ((800, 5700, "F1"), (15100, 5700, "F2")):
        rectangle(ms, [ox, oy, ox + 11700, oy + 6200], "CONTEXT", linetype="CONTINUOUS")
        line(ms, (ox + 3100, oy - 300), (ox + 3100, oy + 6600), "CONTEXT")
        text(ms, "GRID B", (ox + 2600, oy + 6500), 170, "CONTEXT")
    for a, b in zip(lower, lower[1:]):
        line(ms, a, b, "PIPING")
    for a, b in zip(upper, upper[1:]):
        line(ms, a, b, "PIPING")
    riser(ms, lower[-1])
    riser(ms, upper[0], label_offset=(-450, 520))
    text(ms, "S-IN", (lower[0][0] - 300, lower[0][1] - 350), 190)
    dot(ms, lower[0], 65)
    text(ms, "U-OUT", (upper[-1][0] + 220, upper[-1][1]), 190)
    dot(ms, upper[-1], 65)
    text(ms, "SUP  DN50", (lower[0][0] + 1000, lower[0][1] + 180), 210)
    text(ms, "SUP  DN50", (lower[-1][0] + 220, lower[-1][1] - 1300), 180, rotation=90)
    text(ms, "SUP DN50", (upper[0][0] + 260, upper[0][1] + 200), 190)
    text(ms, "SUP DN32", (upper[1][0] + 370, upper[1][1] + 200), 190)
    text(ms, "SUP DN32", (upper[-1][0] + 210, upper[2][1] + 550), 180, rotation=90)
    reducer = upper[1]
    poly(ms, [(reducer[0] - 90, reducer[1] - 95), (reducer[0] + 90, reducer[1]),
              (reducer[0] - 90, reducer[1] + 95)], "SYMBOL", True)
    text(ms, "RED-A / DN50 x DN32", (reducer[0] - 500, reducer[1] - 600), 170)
    poly(ms, [(reducer[0], reducer[1] - 130), (reducer[0], reducer[1] - 360)], "ANNOTATION")
    dimension(ms, lower[0], lower[1], -700)
    dimension(ms, lower[1], lower[2], 1000)
    dimension(ms, upper[0], upper[1], -1150)
    dimension(ms, upper[1], upper[2], -1150)
    dimension(ms, upper[2], upper[3], -650)

    # Drawn neighbouring system: same broad piping layer, explicit system label.
    crossing_x = lower_origin[0] + 2100
    line(ms, (crossing_x, lower_origin[1] - 600), (crossing_x, lower_origin[1] + 3300), "PIPING", color=30)
    text(ms, "DRN DN80 / EXISTING DRAIN", (crossing_x - 500, lower_origin[1] + 3650), 170)
    text(ms, "NO CONNECTION AT UNMARKED CROSSING", (1000, 5100), 170)

    # Topology is shown again in this separate view; its page geometry is NTS.
    x, y0, y1 = 34200, 6600, 11100
    line(ms, (31300, y0), (x, y0), "PIPING")
    line(ms, (x, y0), (x, y1), "PIPING")
    line(ms, (x, y1), (37900, y1), "PIPING")
    line(ms, (37900, y1), (40700, y1), "PIPING")
    poly(ms, [(37810, y1 - 95), (37990, y1), (37810, y1 + 95)], "SYMBOL", True)
    riser(ms, (x, (y0 + y1) / 2), label_offset=(-900, 100))
    notes(ms, ["SUP DN50", "R-A: straight vertical", "between indicated pipe CLs"], (x + 300, 9250), 180, 310)
    text(ms, "F1 PIPE CL +3.15 m", (30600, y0 - 450), 210)
    text(ms, "F2 PIPE CL +7.85 m", (30600, y1 + 430), 210)
    text(ms, "S-IN", (30800, y0 + 180), 190)
    text(ms, "U-OUT", (39800, y1 + 180), 190)
    text(ms, "RED-A", (37350, y1 - 430), 190)
    text(ms, "DN50", (35600, y1 + 180), 180)
    text(ms, "DN32", (38900, y1 + 180), 180)
    for y, label in ((5600, "F1 FFL +0.00 m"), (10300, "F2 FFL +4.60 m")):
        line(ms, (30600, y), (42300, y), "CONTEXT")
        text(ms, label, (38300, y + 180), 170, "CONTEXT")

    # Legend repeats actual symbol and line styles, as a normal drawing would.
    text(ms, "LEGEND / SYMBOL EXAMPLES ONLY", (900, 3900), 240)
    line(ms, (1000, 3300), (3100, 3300), "PIPING")
    text(ms, "SUP DNxx : new supply centreline", (3450, 3230), 170)
    line(ms, (1000, 2800), (3100, 2800), "PIPING", color=30)
    text(ms, "DRN : existing drain, separate system", (3450, 2730), 170)
    riser(ms, (1600, 2200), "R-x")
    text(ms, "Riser identifier links plans and system diagram", (3450, 2130), 170)
    poly(ms, [(1100, 1530), (1500, 1700), (1100, 1870)], "SYMBOL", True)
    text(ms, "Reducer symbol; DN shown on each side", (3450, 1630), 170)
    line(ms, (1000, 1100), (3100, 1100), "CONTEXT")
    text(ms, "Dashed grey: architectural reference", (3450, 1030), 170)

    text(ms, "DESIGN / MEASUREMENT NOTES", (16000, 3900), 240)
    notes(ms, [
        "1. Scope: new SUP from S-IN to U-OUT, including the connection between levels.",
        "2. Plan dimensions and coordinates locate horizontal pipe centrelines in millimetres.",
        "3. Riser R-A in the plans and system diagram represents the same physical riser.",
        "4. Pipe CL elevations are in metres from the project datum; FFL is not pipe CL.",
        "5. The system diagram explains connections and elevations; do not scale its drawn lengths.",
        "6. Centreline quantity includes continuous length through inline fittings. No fitting allowances.",
        "7. DN changes at the indicated reducer centre; there are no unshown offsets in this task.",
        "8. Lines crossing without a filled junction dot are not connected; DRN is outside SUP scope.",
        "9. End ports define the measurement limit. Do not add lengths beyond S-IN or U-OUT.",
        "10. The legend is explanatory only. No waste, insulation, supports, or cost quantities requested.",
    ], (16000, 3440), 180, 300)

    path = directory / "drawing.dxf"
    doc.saveas(path)
    (directory / "task.md").write_text(
        "# 原图算量任务：服务区给水管网\n\n"
        "请依据所附图纸，完成本服务区新设给水管网的中心线长度算量，按公称直径分项，并说明各图之间的对应关系及计量范围。"
        "图中的系统、构件、尺寸、标高和适用说明请自行读取、判断；不要把图例或重复视图直接累计。\n\n"
        "请使用现有算量技能自行形成专业输入并实际运行原生管网计算器，保留原生结果、逐项来源定位、扣除/排除说明及未决项。"
        "最后沿图回查范围，给出可复核的明细与分项合计。只计算工程量，不做价格、损耗或未给定的设计补充。\n\n"
        "资料格式：`drawing.dxf` 为原创、模型空间绘制的标准 DXF；`drawing.png` 是同图未经判读标记的显示副本。"
        "图内包含设计说明和图例，文字采用英文及通用工程缩写。CAD 坐标单位与标高单位按图上声明使用。"
        "本任务是合成案例，仅用于受限执行检验，不代表真实工程验收。\n", encoding="utf-8")
    return path


def fill(space, box, layer):
    x0, y0, x1, y1 = box
    h = space.add_hatch(color=256, dxfattribs={"layer": layer})
    h.paths.add_polyline_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], is_closed=True)


def surface_case():
    p = PARAMETERS["surfaces"]
    directory = PUBLIC / "surfaces"
    directory.mkdir(parents=True, exist_ok=True)
    doc = new_doc()
    ms = doc.modelspace()
    rectangle(ms, [0, 0, 25100, 13200], "BORDER")
    text(ms, "A-01 | STUDIO SUSPENDED CEILING | REV A | SYNTHETIC DESIGN", (650, 12500), 290)
    text(ms, "Native CAD: millimetres. Plan boundaries are full-size. Detail S-2 is schematic.", (650, 11980), 185)
    origin = (1600, 6100)
    ox, oy = origin
    w, h, split = p["width_mm"], p["height_mm"], p["split_mm"]
    text(ms, "RCP-01 / REFLECTED CEILING PLAN", (850, 10900), 240)
    fill(ms, [ox, oy, ox + split, oy + h], "FILL-A")
    fill(ms, [ox + split, oy, ox + w, oy + h], "FILL-B")
    rectangle(ms, [ox, oy, ox + w, oy + h], "FINISH")
    line(ms, (ox + split, oy), (ox + split, oy + h), "FINISH")
    text(ms, "CL-11", (ox + 2400, oy + 2450), 260)
    text(ms, "CL-24", (ox + split + 650, oy + 1950), 260)
    for name, box in (("SH-1", p["shaft"]), ("AP-1", p["access"])):
        x0, y0, x1, y1 = [v + (ox if i % 2 == 0 else oy) for i, v in enumerate(box)]
        rectangle(ms, [x0, y0, x1, y1], "OPENINGS")
        line(ms, (x0, y0), (x1, y1), "OPENINGS")
        line(ms, (x1, y0), (x0, y1), "OPENINGS")
        text(ms, name + " / SEE S-2", (x0 - 120, y1 + 220), 160)
        dimension(ms, (x0, y0), (x1, y0), -300)
        dimension(ms, (x1, y0), (x1, y1), 300)
    # Same plan includes a low screen projection, not a ceiling boundary.
    sy = oy + p["screen_y_mm"]
    line(ms, (ox, sy), (ox + w, sy), "CONTEXT")
    line(ms, (ox, sy - 100), (ox + w, sy - 100), "CONTEXT")
    text(ms, "SCREEN P-1 BELOW / REFER S-2", (ox + 900, sy - 450), 170, "CONTEXT")
    dimension(ms, (ox, oy), (ox + split, oy), -1050)
    dimension(ms, (ox + split, oy), (ox + w, oy), -1050)
    dimension(ms, (ox, oy + h), (ox + w, oy + h), 400)
    dimension(ms, (ox + w, oy), (ox + w, oy + h), 1250)

    text(ms, "S-2 / CEILING, SCREEN AND ACCESS DETAIL / NTS", (12100, 10900), 240)
    sx, sy0 = 13100, 6350
    line(ms, (sx, sy0), (sx + 9800, sy0), "CONTEXT")
    line(ms, (sx, sy0 + p["structure_z_mm"]), (sx + 9800, sy0 + p["structure_z_mm"]), "CONTEXT")
    line(ms, (sx, sy0 + p["ceiling_z_mm"]), (sx + 9800, sy0 + p["ceiling_z_mm"]), "FINISH")
    screen_x = sx + 2700
    rectangle(ms, [screen_x, sy0, screen_x + 100, sy0 + p["screen_height_mm"]], "SYMBOL")
    text(ms, "FFL +0.000", (sx + 100, sy0 - 310), 180)
    text(ms, "UNDERSIDE STRUCTURE +3.650", (sx + 4300, sy0 + p["structure_z_mm"] + 150), 180)
    text(ms, "CEILING FINISH +2.800", (sx + 100, sy0 + p["ceiling_z_mm"] + 180), 180)
    text(ms, "P-1 TOP +1.700", (screen_x + 300, sy0 + p["screen_height_mm"]), 180)
    text(ms, "Ceiling continues above screen", (screen_x + 300, sy0 + p["screen_height_mm"] - 320), 180)
    # Flush removable access panel detail, shown in the ceiling line.
    apx, apy = sx + 7350, sy0 + p["ceiling_z_mm"]
    rectangle(ms, [apx, apy - 75, apx + 550, apy + 75], "OPENINGS")
    poly(ms, [(apx + 270, apy - 120), (apx + 270, apy - 750), (apx + 1700, apy - 750)], "ANNOTATION")
    text(ms, "AP-1 / flush finish panel", (apx - 250, apy - 1100), 180)

    text(ms, "FINISH SCHEDULE AND PROJECT NOTES", (900, 3950), 245)
    notes(ms, [
        "CL-11 : acoustic ceiling panel. CL-24 : painted gypsum-board ceiling finish.",
        "Boundary between finishes is the solid finish division line in RCP-01.",
        "SH-1 : service shaft open through the ceiling; deduct its actual footprint.",
        "AP-1 : flush removable access panel with the SAME finish as its surrounding zone.",
        "For this finish-area takeoff AP-1 is retained in the material area, not deducted as a hole.",
        "P-1 : low screen below the ceiling. Ceiling material is continuous above it; no ceiling deduction.",
        "Measure horizontal NET ceiling finish area by material, from the shown internal finish boundary.",
        "No shaft reveals, vertical returns, extra layers, bulkheads or unshown projections in this scope.",
        "Do not count detail diagrams as additional surfaces. Framing and access-panel item counts are separate.",
        "Use actual geometric area for this task; no procurement factor, laps, waste or contract threshold.",
    ], (900, 3520), 180, 310)
    text(ms, "LEGEND", (18700, 3950), 230)
    line(ms, (18800, 3380), (20500, 3380), "FINISH")
    text(ms, "Finish edge / material division", (20800, 3300), 140)
    line(ms, (18800, 2770), (20500, 2770), "CONTEXT")
    text(ms, "Projected object below", (20800, 2690), 140)
    rectangle(ms, [19000, 1770, 19700, 2350], "OPENINGS")
    line(ms, (19000, 1770), (19700, 2350), "OPENINGS")
    line(ms, (19000, 2350), (19700, 1770), "OPENINGS")
    text(ms, "Tagged feature: consult detail", (20100, 2000), 140)
    path = directory / "drawing.dxf"
    doc.saveas(path)
    (directory / "task.md").write_text(
        "# 原图算量任务：工作室吊顶饰面\n\n"
        "请依据所附图纸完成本工作室吊顶饰面的净面积算量，按材料分项。请自行核对反射平面、做法表、说明和详图，"
        "确定表面范围及图中构造对本次面积的影响，并保留可回到图纸的计算依据。\n\n"
        "请使用现有面层算量技能自行形成专业输入并实际运行原生面层计算器，提供原生结果、分项明细、扣减与保留的依据、"
        "覆盖说明及未决项。不计龙骨、基层实体、设备点数或价格，也不套用未给定的损耗和经验系数。\n\n"
        "资料格式：`drawing.dxf` 为原创、模型空间绘制的标准 DXF；`drawing.png` 是同图未经判读标记的显示副本。"
        "必要设计说明与图例都在图中，文字采用英文及通用工程缩写；单位按图上声明使用。"
        "本任务是合成案例，仅用于受限执行检验，不代表真实工程验收。\n", encoding="utf-8")
    return path


def oracle_records(pipe_path, surface_path):
    p = PARAMETERS["pipe"]
    # Independent arithmetic from author design parameters, not DXF extraction
    # and never from the production calculator's outputs.
    horizontal = lambda pts: sum(Decimal(abs(b[0] - a[0]) + abs(b[1] - a[1])) / Decimal(1000)
                                 for a, b in zip(pts, pts[1:]))
    low = horizontal(p["lower_plan"])
    up_before = horizontal(p["upper_plan"][:2])
    up_after = horizontal(p["upper_plan"][1:])
    vertical = abs(Decimal(p["upper_pipe_cl_m"]) - Decimal(p["lower_pipe_cl_m"]))
    authored_at = datetime.now(timezone.utc).isoformat()

    def record(key, classification, specification, quantity, unit, basis, explanation):
        return {"record_id": key, "physical_object_key": key, "quantity_kind": "pipe_centerline_length" if unit == "m" else "net_ceiling_finish_area",
                "classification": classification, "specification": specification, "quantity": float(quantity), "unit": unit,
                "measurement_basis": basis, "status": "determined", "evidence_status": "supported",
                "evidence_refs": [explanation], "relations": {}}

    pipe_records = [
        record("SUP/F1/S-IN-to-R-A", "pipe", "DN50", low, "m", "continuous_centerline_including_inline_fittings", "P-01 F1 plan; full-size centreline and dimensions"),
        record("SUP/R-A/F1-to-F2", "pipe", "DN50", vertical, "m", "continuous_centerline_including_inline_fittings", "P-01 system diagram; pipe CL difference, straight vertical R-A, same physical riser in plans"),
        record("SUP/F2/R-A-to-RED-A", "pipe", "DN50", up_before, "m", "continuous_centerline_including_inline_fittings", "P-01 F2 plan upstream of RED-A"),
        record("SUP/F2/RED-A-to-U-OUT", "pipe", "DN32", up_after, "m", "continuous_centerline_including_inline_fittings", "P-01 F2 plan downstream of RED-A including elbow"),
    ]
    pipe_oracle = {
        "schema_version": "qto-authored-synthetic-oracle/1.0", "case_id": "pipe", "authored_at": authored_at,
        "source": {"path": str(pipe_path), "sha256": sha(pipe_path)}, "parameters": p,
        "origin": "Original design parameters and independent Decimal hand formulas; no production QTO result was consulted.",
        "scope_complete": True, "records": pipe_records,
        "arithmetic": {"lower_plan": "(3600+2400)/1000", "vertical": "abs(7.85-3.15)", "upper_before_reducer": "(5900-3600)/1000", "upper_after_reducer": "((8300-5900)+(4300-2400))/1000"},
        "summary_by_dn_m": {"DN50": float(low + up_before + vertical), "DN32": float(up_after)},
        "required_semantic_decisions": [
            "Map R-A between plans and diagram without counting it more than once.",
            "Use pipe CL elevations, not floor-to-floor elevation or schematic page distance.",
            "Exclude DRN and architectural context and the legend; do not connect the unmarked crossing.",
            "Keep the physical DN boundary at RED-A and do not propagate DN50 through it.",
            "Do not add elbow/reducer take-up lengths to the requested continuous centreline quantity.",
        ],
        "matching_policy": "Candidate may split or merge geometric primitives. Align by physical path spans and DN after candidate freeze; never require candidate to predict private IDs. Assess complete physical coverage and duplicate quantities separately.",
    }
    s = PARAMETERS["surfaces"]
    area = lambda box: Decimal(box[2] - box[0]) * Decimal(box[3] - box[1]) / Decimal(1000000)
    gross_left = Decimal(s["split_mm"]) * Decimal(s["height_mm"]) / Decimal(1000000)
    gross_right = Decimal(s["width_mm"] - s["split_mm"]) * Decimal(s["height_mm"]) / Decimal(1000000)
    shaft_area, access_area = area(s["shaft"]), area(s["access"])
    surface_records = [
        record("STUDIO/RCP-01/CL-11", "ceiling_finish", "CL-11 acoustic ceiling panel", gross_left - shaft_area, "m2", "horizontal_actual_net_finish_area", "A-01 RCP-01 boundary and SH-1 footprint; schedule and S-2 treatment"),
        record("STUDIO/RCP-01/CL-24", "ceiling_finish", "CL-24 painted gypsum-board ceiling finish", gross_right, "m2", "horizontal_actual_net_finish_area", "A-01 RCP-01 material zone; AP-1 retained under project note and S-2"),
    ]
    surface_oracle = {
        "schema_version": "qto-authored-synthetic-oracle/1.0", "case_id": "surfaces", "authored_at": authored_at,
        "source": {"path": str(surface_path), "sha256": sha(surface_path)}, "parameters": s,
        "origin": "Original design dimensions and independent Decimal rectangle arithmetic; no production QTO result was consulted.",
        "scope_complete": True, "records": surface_records,
        "arithmetic": {"CL-11": "4400*4100/1000000 - 700*900/1000000", "CL-24": "(6800-4400)*4100/1000000"},
        "audit_values_m2": {"gross_left": float(gross_left), "gross_right": float(gross_right), "shaft_deduction": float(shaft_area), "access_footprint_retained": float(access_area)},
        "required_semantic_decisions": [
            "Use the actual material division and the internal ceiling finish boundary.",
            "Deduct SH-1 as a true ceiling void, not AP-1 which retains its matching finish.",
            "Read S-2 and notes to see P-1 lies below continuous ceiling and causes no ceiling deduction.",
            "Do not count detail geometry, legend, screen, framing or additional layers as ceiling finish.",
        ],
        "matching_policy": "Match after freeze by physical finish zone and material. Candidate may use any IDs and may split geometry without changing total physical coverage. An aggregate alone does not prove correct deduction reasoning.",
    }
    for name, value in (("pipe_oracle.json", pipe_oracle), ("surfaces_oracle.json", surface_oracle)):
        write_json(PRIVATE / name, value)


def render_and_audit(paths):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    results = []
    for path in paths:
        doc = ezdxf.readfile(path)
        audit = doc.audit()
        if audit.errors or audit.fixes:
            raise RuntimeError("Author drawing failed clean DXF audit")
        fig = plt.figure(figsize=(22, 11))
        ax = fig.add_axes((0, 0, 1, 1))
        ctx = RenderContext(doc)
        config = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.COLOR)
        Frontend(ctx, MatplotlibBackend(ax), config=config).draw_layout(doc.modelspace(), finalize=True)
        fig.savefig(path.with_suffix(".png"), dpi=230, facecolor="white", bbox_inches="tight", pad_inches=.12)
        plt.close(fig)
        results.append({"source": str(path), "source_sha256": sha(path), "png_sha256": sha(path.with_suffix(".png")),
                        "DXF_readable": True, "DXF_audit_errors": len(audit.errors), "DXF_audit_fixes": len(audit.fixes),
                        "entity_types": sorted({e.dxftype() for e in doc.modelspace()}),
                        "author_qa": "Independent format and visual QA only; no QTO calculation or model solution run."})
    write_json(PRIVATE / "format_qa.json", results)


def main():
    if (PRIVATE / "release_manifest.json").exists():
        raise RuntimeError("Released cases are immutable. Use a new case version instead of overwriting.")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "README.md").write_text(
        "# 原创合成图纸任务\n\n"
        "各子目录中的 task.md 是相应算量任务，drawing.dxf 是设计原图，drawing.png 是同图显示副本。"
        "请基于这些公开资料和当前插件技能执行，不读取任务作者的生成程序、参数或参照答案。"
        "任何已经知道参照答案的执行都应披露，不称为盲测。\n\n"
        "图纸仅采用标准 CAD 图元，不依赖外参、专用代理对象或商业 CAD 插件。"
        "DXF 取证可使用当前仓库的 CAD 工具；模型应自行解释图面并形成原生计算器输入。"
        "请把自己的工作文件和结果写在独立求解目录，不改动输入资料。\n", encoding="utf-8")
    pipe, surface = pipe_case(), surface_case()
    oracle_records(pipe, surface)
    render_and_audit([pipe, surface])
    write_json(PRIVATE / "release_manifest.json", {
        "released_at": datetime.now(timezone.utc).isoformat(),
        "files": {str(p.relative_to(BASE)): sha(p) for p in sorted(PUBLIC.rglob("*")) if p.is_file()},
        "oracle_files": {p.name: sha(p) for p in sorted(PRIVATE.glob("*_oracle.json"))},
        "generator_sha256": sha(Path(__file__)),
        "independence": "Author-only design and oracle; the solver is to receive inputs/ only. Hashes prove consistency, not independent reasoning or access isolation.",
        "limitations": "Original small synthetic cases using supported representations; no claim of real-engineering accuracy or cross-project generalization.",
    })
    (PRIVATE / "README.md").write_text(
        "# Author / evaluator only\n\n"
        "Do not disclose this directory to the solver before a frozen candidate is received.\n"
        "Oracle arithmetic is derived from original design parameters in create_cases.py, not QTO outputs.\n"
        "The only public deliverable is ../inputs. Use the current release manifest to verify unchanged inputs.\n"
        "Candidate scoring must align physical extents after freeze, respecting legitimate primitive splits.\n"
        "Count missing/doubled physical work, wrong DN/material and unsupported rule interpretation separately from arithmetic.\n"
        "No existing benchmark answer, production script output or prior project input was used as an oracle.\n",
        encoding="utf-8")
    print("Public drawings prepared; author reference remains private.")


if __name__ == "__main__":
    main()
