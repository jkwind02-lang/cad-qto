"""Conservative display-only envelopes; unknown display content is retained.

This is not an entity index, measurement or model recognition. Only primitive
WCS geometry that is guaranteed to fit a finite envelope may be skipped, with
a whole-window-size margin. Bulged outlines use the existing native curve
envelope, never endpoint-only boxes. Containers, annotations, proxies, wide
polylines and non-polyline hatch edges deliberately stay in the renderer.
"""
from __future__ import annotations

import math
import hashlib
import json
from qto_cad_geometry import entity_geometry


def known_display_envelope(entity, point_marker_size=None):
    """Return a conservative XY envelope, or None when not proven here."""
    try:
        kind = entity.dxftype()
        if entity.proxy_graphic or (kind in {'LINE','CIRCLE','ARC','LWPOLYLINE'} and entity.dxf.get('thickness', 0)):
            return None
        if kind == 'LINE':
            points = [entity.dxf.start, entity.dxf.end]
        elif kind == 'POINT':
            # Size is passed from Frontend.config after RenderContext resolves
            # document defaults/relative-size fallback. An anchor alone never
            # suffices: circle/square/cross markers may reach into the window.
            if isinstance(point_marker_size,bool) or not isinstance(point_marker_size,(int,float)) or not math.isfinite(point_marker_size) or point_marker_size<0:
                return None
            angle=float(entity.dxf.get('angle',0))
            extrusion=entity.dxf.get('extrusion',(0,0,1))
            if not math.isfinite(angle) or any(not math.isfinite(v) for v in extrusion) or sum(v*v for v in extrusion)==0:
                return None
            radius=2*point_marker_size
            center=entity.dxf.location
            points=[(center.x-radius,center.y-radius),(center.x+radius,center.y+radius)]
        elif kind in {'CIRCLE', 'ARC'}:
            center = entity.ocs().to_wcs(entity.dxf.center)
            radius = float(entity.dxf.radius)
            if not math.isfinite(radius) or radius <= 0:
                return None
            # A circle in any plane fits this WCS sphere. For arcs retaining
            # the complete circle is conservative and avoids angle guessing.
            points = [(center.x-radius, center.y-radius),
                      (center.x+radius, center.y+radius)]
        elif kind == 'LWPOLYLINE':
            vertices = list(entity.get_points('xyseb'))
            if entity.dxf.get('const_width', 0) or any(a or b for _, _, a, b, _ in vertices):
                return None
            if any(bulge for _, _, _, _, bulge in vertices):
                facts=entity_geometry(entity)
                if facts.get('geometry_issues') or not facts.get('geometry'):
                    return None
                return facts['bbox']
            points = list(entity.vertices_in_wcs())
        elif kind == 'HATCH':
            points = []
            from ezdxf.entities import LWPolyline
            for path in entity.paths:
                if not hasattr(path, 'vertices') or not path.is_closed:
                    return None
                outline=LWPolyline.new(dxfattribs={'elevation':entity.dxf.elevation.z,'extrusion':entity.dxf.extrusion})
                outline.set_points(path.vertices,format='xyb');outline.closed=True
                facts=entity_geometry(outline)
                if facts.get('geometry_issues') or not facts.get('geometry') or not facts.get('bbox'):
                    return None
                box=facts['bbox'];points.extend([(box[0],box[1]),(box[2],box[3])])
        else:
            return None
        if not points or any(not math.isfinite(float(p[i])) for p in points for i in (0, 1)):
            return None
        return [min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points)]
    except (AttributeError, TypeError, ValueError, ArithmeticError):
        return None


def proven_far_outside(entity, bounds, properties, point_marker_size=None):
    if bounds is None:
        return False
    # Standard DXF weights are at most 2.11mm on the 297mm display page. A
    # full viewport span of padding is intentionally much larger. For any
    # unusual/nonfinite weight, retain the entity rather than rely on it.
    weight = getattr(properties, 'lineweight', None)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0 or weight > 2.11:
        return False
    box = known_display_envelope(entity,point_marker_size)
    if box is None:
        return False
    margin = max(bounds[2]-bounds[0], bounds[3]-bounds[1])
    return (box[2] < bounds[0]-margin or box[0] > bounds[2]+margin
            or box[3] < bounds[1]-margin or box[1] > bounds[3]+margin)


def deterministic_pattern_baselines(polygon, min_distance, source_sha256):
    """Native HatchBaseLine arithmetic with source-bound small origin jitter.

    ezdxf 1.4.4 otherwise draws fresh global random factors for every hatch.
    Keeping its jitter range avoids corner intersections while making output
    independent of skipped distant hatches and the caller's random state.
    Source pattern definitions and geometry are never modified.
    """
    from ezdxf.math import Vec2
    from ezdxf.render.hatching import HatchBaseLine
    pattern=polygon.pattern
    if not pattern or not pattern.lines:
        return
    definitions=[[line.angle,list(line.base_point),list(line.offset),list(line.dash_length_items)] for line in pattern.lines]
    payload=json.dumps([source_sha256,polygon.dxf.get('handle'),definitions],sort_keys=True,separators=(',',':'),allow_nan=False)
    digest=hashlib.sha256(payload.encode('utf8')).digest()
    mean=sum(line.offset.magnitude for line in pattern.lines)/len(pattern.lines)
    factors=[.0003+.0007*int.from_bytes(digest[n:n+8],'big')/(2**64-1) for n in (0,8)]
    jitter=Vec2(factors[0]*mean,factors[1]*mean)
    for line in pattern.lines:
        yield HatchBaseLine(origin=line.base_point+jitter,direction=Vec2.from_deg_angle(line.angle),offset=line.offset,line_pattern=line.dash_length_items,min_hatch_line_distance=min_distance)
