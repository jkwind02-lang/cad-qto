"""Lossless neutral line/conic geometry and affine measurements.

Coordinates in ``parts`` are WCS (block-local WCS for definitions). An elliptic
arc is c + u*cos(t) + v*sin(t); u/v need not stay perpendicular after an affine
transform. No professional classification or topology inference is performed.
"""
from __future__ import annotations

import copy
import math
from typing import Any

GEOMETRY_SCHEMA = "qto-cad-geometry/1.1"
IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
TAU = math.tau


def xyz(value):
    values = list(value)
    result = [float(values[0]), float(values[1]), float(values[2]) if len(values) > 2 else 0.]
    if not all(math.isfinite(n) for n in result):
        raise ValueError("几何坐标必须是有限数值")
    return result


def matrix16(matrix):
    if len(matrix) == 8:  # legacy planar affine matrix
        a, b, c, d, tx, ty, sz, tz = matrix
        matrix = (a, c, 0., tx, b, d, 0., ty, 0., 0., sz, tz, 0., 0., 0., 1.)
    if len(matrix) != 16 or not all(math.isfinite(float(n)) for n in matrix):
        raise ValueError("块变换必须是有限4×4仿射矩阵")
    result = tuple(float(n) for n in matrix)
    if result[12:] != (0., 0., 0., 1.):
        raise ValueError("不支持投影变换")
    return result


def transform_point(value, matrix):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y, z = xyz(value)
    m = matrix16(matrix)
    return [m[i]*x + m[i+1]*y + m[i+2]*z + m[i+3] for i in (0, 4, 8)]


def transform_vector(value, matrix):
    x, y, z = xyz(value)
    m = matrix16(matrix)
    return [m[i]*x + m[i+1]*y + m[i+2]*z for i in (0, 4, 8)]


def compose_matrix(parent, child):
    a, b = matrix16(parent), matrix16(child)
    return tuple(sum(a[r*4+k]*b[k*4+c] for k in range(4)) for r in range(4) for c in range(4))


def matrix_for_insert(row, origin):
    if row.get("insert_matrix_wcs") is not None:
        return matrix16(row["insert_matrix_wcs"])
    if row.get("extrusion") not in (None, [0, 0, 1], [0., 0., 1.]):
        raise ValueError("旧块缓存缺少三维变换，请重新提取")
    scales = row.get("scale_factors") or [1., 1., 1.]
    sx, sy, sz = (float(scales[i]) if i < len(scales) else 1. for i in range(3))
    rotation = float(row.get("rotation") or 0.)
    cs, sn = math.cos(rotation), math.sin(rotation)
    a, b, c, d = cs*sx, sn*sx, -sn*sy, cs*sy
    x, y, z = xyz([row.get("x", 0.), row.get("y", 0.), row.get("z", 0.)])
    ox, oy, oz = xyz(origin)
    return matrix16((a, b, c, d, x-a*ox-c*oy, y-b*ox-d*oy, sz, z-sz*oz))


def conic_point(part, t):
    cs, sn = math.cos(t), math.sin(t)
    return [c+u*cs+v*sn for c, u, v in zip(part["center"], part["u"], part["v"])]


def _integral(f, a, b, tolerance, depth=22):
    """Adaptive Simpson, failing explicitly when the requested error is unmet."""
    if a == b:
        return 0.
    if a > b:
        a, b = b, a
    def recurse(a, b, fa, fm, fb, estimate, tol, depth):
        mid = (a+b)/2
        fl, fr = f((a+mid)/2), f((mid+b)/2)
        left, right = (mid-a)*(fa+4*fl+fm)/6, (b-mid)*(fm+4*fr+fb)/6
        delta = left+right-estimate
        if abs(delta) <= 15*tol:
            return left+right+delta/15
        if depth <= 0:
            raise ValueError("曲线积分未达到误差要求；不得按弦长替代")
        return recurse(a, mid, fa, fl, fm, left, tol/2, depth-1)+recurse(mid, b, fm, fr, fb, right, tol/2, depth-1)
    # Split into short intervals to avoid periodic-function aliasing.
    n = max(1, math.ceil((b-a)/(math.pi/4)))
    total = 0.
    for i in range(n):
        lo, hi = a+(b-a)*i/n, a+(b-a)*(i+1)/n
        fa, fm, fb = f(lo), f((lo+hi)/2), f(hi)
        total += recurse(lo, hi, fa, fm, fb, (hi-lo)*(fa+4*fm+fb)/6, tolerance/n, depth)
    return total


def measure_part(part):
    if part["kind"] == "line":
        return math.dist(part["start"], part["end"]), 0.
    if part["kind"] != "ellipse_arc":
        raise ValueError("不支持的中性曲线类型")
    u, v = part["u"], part["v"]
    ru, rv = math.sqrt(sum(x*x for x in u)), math.sqrt(sum(x*x for x in v))
    sweep, start = float(part["sweep_param"]), float(part["start_param"])
    if max(ru, rv) == 0:
        return 0., 0.
    dot = sum(a*b for a, b in zip(u, v))
    if abs(ru-rv) <= 1e-12*max(ru, rv) and abs(dot) <= 1e-12*ru*rv:
        return abs(sweep)*(ru+rv)/2, 0.
    tolerance = max(1e-10, abs(sweep)*max(ru, rv)*1e-10)
    speed = lambda t: math.sqrt(sum((-a*math.sin(t)+b*math.cos(t))**2 for a, b in zip(u, v)))
    return _integral(speed, start, start+sweep, tolerance), tolerance


def part_extrema(part):
    if part["kind"] == "line":
        return [part["start"], part["end"]]
    start = float(part["start_param"])
    end = start+float(part["sweep_param"])
    lo, hi = min(start, end), max(start, end)
    params = [start, end]
    for u, v in zip(part["u"], part["v"]):
        t = math.atan2(v, u)
        for k in range(math.ceil((lo-t)/math.pi), math.floor((hi-t)/math.pi)+1):
            params.append(t+k*math.pi)
    return [conic_point(part, t) for t in params]


def summarize(geometry):
    if geometry.get("schema") != GEOMETRY_SCHEMA or geometry.get("coordinate_system") != "WCS":
        raise ValueError("几何契约或坐标系不支持，请重新提取")
    parts = geometry["parts"]
    if not parts:
        return {"anchor": None, "bbox": None, "bbox3d": None, "start": None, "end": None, "length": None, "signature": None, "length_error_estimate": None}
    measures = [measure_part(p) for p in parts]
    points = [p for part in parts for p in part_extrema(part)]
    start = parts[0]["start"] if parts[0]["kind"] == "line" else conic_point(parts[0], parts[0]["start_param"])
    last = parts[-1]
    end = last["end"] if last["kind"] == "line" else conic_point(last, last["start_param"]+last["sweep_param"])
    mins, maxs = [min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]
    return {"anchor": list(start), "bbox": [mins[0], mins[1], maxs[0], maxs[1]], "bbox3d": mins+maxs,
            "start": list(start), "end": list(end), "length": math.fsum(n for n, _ in measures),
            "length_error_estimate": math.fsum(e for _, e in measures),
            "signature": {"geometry_schema": GEOMETRY_SCHEMA, "part_count": len(parts), "closed": geometry.get("closed", False),
                          "parts": [dict(p, **{k: [v-start[i] for i,v in enumerate(p[k])] for k in ("start", "end", "center") if k in p}) for p in parts]}}


def transform_geometry(geometry, matrix):
    result = copy.deepcopy(geometry)
    for p in result["parts"]:
        if p["kind"] == "line":
            p["start"], p["end"] = transform_point(p["start"], matrix), transform_point(p["end"], matrix)
        elif p["kind"] == "ellipse_arc":
            p["center"] = transform_point(p["center"], matrix)
            p["u"], p["v"] = transform_vector(p["u"], matrix), transform_vector(p["v"], matrix)
        else:
            raise ValueError("未知几何类型，不能变换")
    return result


def transform_row(row, matrix, instance_path, target_layout):
    result = copy.deepcopy(row)
    original = str(row.get("record_id") or "NO-ID")
    result.update(source_definition_record_id=original, record_id=f"CADINST-{instance_path}/{original}", instance_path=instance_path, layout=target_layout)
    result["candidate_physical_id"] = result["record_id"]
    result["instance_transform"] = list(matrix16(matrix))
    anchor = transform_point([row["x"], row["y"], row.get("z", 0.)], matrix) if row.get("x") is not None and row.get("y") is not None else None
    if anchor:
        result["x"], result["y"], result["z"] = anchor
    if row.get("geometry") is not None:
        geometry = transform_geometry(row["geometry"], matrix)
        summary = summarize(geometry)
        result.update(geometry=geometry, start=summary["start"], end=summary["end"], bbox=summary["bbox"], bbox3d=summary["bbox3d"], raw_length=summary["length"],
                      geometry_signature=summary["signature"], length_error_estimate=summary["length_error_estimate"], geometry_method="wcs_affine_parametric")
    else:
        result["start"], result["end"] = transform_point(row.get("start"), matrix), transform_point(row.get("end"), matrix)
        if row.get("entity_type") == "LINE" and result["start"] is not None and result["end"] is not None:
            result["raw_length"] = math.dist(result["start"], result["end"])
        elif row.get("raw_length") is not None:
            # Older caches do not contain enough geometry for reliable affine
            # length/bounds. Keep evidence, but do not substitute a chord.
            result["raw_length"] = None
            result["geometry_status"] = "requires_reextraction"
            result["geometry_issues"] = ["旧曲线缓存缺少完整几何，请重新提取后计算"]
        box = row.get("bbox3d")
        if box is not None:
            points = [transform_point([x,y,z], matrix) for x in (box[0],box[3]) for y in (box[1],box[4]) for z in (box[2],box[5])]
            result["bbox"] = [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
        else:
            # Unknown bounds must not silently discard an in-scope instance.
            result["bbox"] = None
    return result


def entity_geometry(entity: Any):
    """Extract full curves in WCS without exploding the source drawing."""
    from ezdxf.math import Vec3
    kind = entity.dxftype()
    parts, raw, issues = [], {"entity_type": kind}, []
    closed = bool(getattr(entity, "closed", False) or getattr(entity, "is_closed", False))
    anchor, insert_matrix = None, None
    extrusion = xyz(entity.dxf.get("extrusion", (0, 0, 1)))
    raw["extrusion"] = extrusion
    def line(a, b):
        parts.append({"kind": "line", "start": xyz(a), "end": xyz(b)})
    def arc(center, u, v, start, sweep):
        parts.append({"kind": "ellipse_arc", "center": xyz(center), "u": xyz(u), "v": xyz(v), "start_param": float(start), "sweep_param": float(sweep)})
    if kind == "LINE":
        line(entity.dxf.start, entity.dxf.end)
        raw.update(start=xyz(entity.dxf.start), end=xyz(entity.dxf.end))
    elif kind in {"LWPOLYLINE", "POLYLINE"}:
        if kind == "POLYLINE" and not (entity.is_2d_polyline or entity.is_3d_polyline):
            issues.append("网格/多面网不作为多段线中心线测量")
        else:
            is2d = kind == "LWPOLYLINE" or entity.is_2d_polyline
            if kind == "LWPOLYLINE":
                elevation = float(entity.dxf.elevation)
                values = [([float(x),float(y),elevation],float(b)) for x,y,b in entity.get_points("xyb")]
            else:
                elevation = float(entity.dxf.get("elevation", Vec3()).z)
                values = [(xyz(v.dxf.location),float(v.dxf.get("bulge",0.))) for v in entity.vertices]
                if is2d:
                    values = [([p[0],p[1],elevation],b) for p,b in values]
            raw.update(vertices=[p for p,b in values], bulges=[b for p,b in values], closed=closed, coordinate_system="OCS" if is2d else "WCS")
            ocs = entity.ocs()
            pairs = list(zip(values, values[1:]))
            if closed and len(values)>1:
                pairs.append((values[-1],values[0]))
            for (a,bulge),(b,_) in pairs:
                if abs(bulge)<1e-12 or not is2d:
                    line(ocs.to_wcs(a) if is2d else a, ocs.to_wcs(b) if is2d else b)
                else:
                    dx,dy=b[0]-a[0],b[1]-a[1]
                    chord=math.hypot(dx,dy)
                    if chord==0:
                        raise ValueError("弧段弦长为零")
                    factor=(1-bulge*bulge)/(4*bulge)
                    c=[(a[0]+b[0])/2-dy*factor,(a[1]+b[1])/2+dx*factor,a[2]]
                    radius=chord*(1+bulge*bulge)/(4*abs(bulge))
                    arc(ocs.to_wcs(c),ocs.ux*radius,ocs.uy*radius,math.atan2(a[1]-c[1],a[0]-c[0]),4*math.atan(bulge))
    elif kind in {"ARC","CIRCLE"}:
        ocs=entity.ocs()
        radius=float(entity.dxf.radius)
        start=math.radians(float(entity.dxf.start_angle)) if kind=="ARC" else 0.
        sweep=math.radians((float(entity.dxf.end_angle)-float(entity.dxf.start_angle))%360.) if kind=="ARC" else TAU
        raw.update(center=xyz(entity.dxf.center),radius=radius,start_param=start,sweep_param=sweep,coordinate_system="OCS")
        anchor=xyz(ocs.to_wcs(entity.dxf.center))
        arc(anchor,ocs.ux*radius,ocs.uy*radius,start,sweep)
        closed=kind=="CIRCLE"
    elif kind=="ELLIPSE":
        start,end=float(entity.dxf.start_param),float(entity.dxf.end_param)
        delta=end-start
        sweep=TAU if abs(delta)>=TAU-1e-12 else delta%TAU
        raw.update(center=xyz(entity.dxf.center),major_axis=xyz(entity.dxf.major_axis),ratio=float(entity.dxf.ratio),start_param=start,end_param=end,coordinate_system="WCS")
        anchor=xyz(entity.dxf.center)
        arc(anchor,entity.dxf.major_axis,entity.minor_axis,start,sweep)
        closed=abs(sweep-TAU)<1e-12
    elif kind=="INSERT":
        anchor=xyz(entity.ocs().to_wcs(entity.dxf.insert))
        m=entity.matrix44()
        zero=m.transform((0.,0.,0.))
        columns=[m.transform(p)-zero for p in ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))]
        insert_matrix=[float(columns[j][i]) if j<3 else float(zero[i]) for i in range(3) for j in range(4)]+[0.,0.,0.,1.]
        raw.update(insert=xyz(entity.dxf.insert),row_count=int(entity.dxf.row_count),column_count=int(entity.dxf.column_count))
        if entity.dxf.row_count>1 or entity.dxf.column_count>1:
            issues.append("阵列块需逐实例展开；当前记录不能视为一个完整实物实例")
    elif kind in {"TEXT","ATTRIB","ATTDEF"}:
        anchor=xyz(entity.ocs().to_wcs(entity.dxf.insert))
        raw["insert"]=xyz(entity.dxf.insert)
    elif kind=="MTEXT":
        anchor=xyz(entity.dxf.insert)  # MTEXT uses WCS, unlike TEXT.
    elif kind=="DIMENSION":
        anchor=xyz(entity.dxf.defpoint)
    elif kind=="SPLINE":
        raw.update(control_points=[xyz(p) for p in entity.control_points],fit_points=[xyz(p) for p in entity.fit_points],knots=list(entity.knots),weights=list(entity.weights),degree=int(entity.dxf.degree))
        issues.append("样条保留原始控制数据；本测量器不以控制折线或弦长冒充曲线")
    else:
        for field in ("insert","location","center","start"):
            value=entity.dxf.get(field) if entity.dxf.is_supported(field) else None
            if value is not None:
                anchor=xyz(value)
                break
    geometry={"schema":GEOMETRY_SCHEMA,"coordinate_system":"WCS","parts":parts,"closed":closed} if parts else None
    summary=summarize(geometry) if geometry else {"start":None,"end":None,"bbox":None,"bbox3d":None,"length":None,"signature":None,"length_error_estimate":None}
    anchor=anchor or summary.get("anchor")
    if not geometry and anchor is not None:
        summary["bbox"]=[anchor[0],anchor[1],anchor[0],anchor[1]]
        summary["bbox3d"]=anchor+anchor
    summary.update(anchor=anchor,geometry=geometry,raw_geometry=raw,geometry_status="supported" if geometry or not issues else "unsupported",geometry_issues=issues,insert_matrix_wcs=insert_matrix,extrusion=extrusion)
    return summary


def transform_bounds(box, matrix):
    """Conservative 3D affine bounds; never flatten unknown Z to zero."""
    if not isinstance(box, (list, tuple)) or len(box) != 6:
        return None
    points = [transform_point([x,y,z],matrix) for x in (box[0],box[3]) for y in (box[1],box[4]) for z in (box[2],box[5])]
    return [min(p[i] for p in points) for i in range(3)]+[max(p[i] for p in points) for i in range(3)]


def definition_bounds(name, definitions, origins, max_depth=16, cache=None, stack=()):
    """A missing/unsupported child makes bounds unknown, not artificially small."""
    if name in stack or len(stack) >= max_depth or name not in definitions:
        return None
    if not stack and cache is not None and name in cache:
        return cache[name]
    boxes=[]
    for row in definitions[name]:
        if row.get("entity_type") == "INSERT":
            child=str(row.get("block_name") or "")
            box=definition_bounds(child,definitions,origins,max_depth,None,(*stack,name))
            if box is not None:
                box=transform_bounds(box,matrix_for_insert(row,origins.get(child,[0.,0.,0.])))
        elif row.get("geometry") is not None:
            box=row.get("bbox3d")
        else:
            box=None  # Point-only glyph extents are not a complete block envelope.
        if box is None:
            return None
        boxes.append(box)
    result=([min(b[i] for b in boxes) for i in range(3)]+[max(b[i] for b in boxes) for i in range(3)]) if boxes else None
    if not stack and cache is not None:
        cache[name]=result
    return result


def bounds_intersect(box, scope):
    if box is None or scope is None:
        return True
    return not (box[3] < scope["xmin"] or box[0] >= scope["xmax_exclusive"] or box[4] < scope["ymin"] or box[1] >= scope["ymax_exclusive"])
