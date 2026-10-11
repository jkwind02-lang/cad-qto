#!/usr/bin/env python3
"""Extract source-bound polygonal HATCH regions, never infer finish assignments.

Drawing semantics, view selection and material mapping remain model decisions.
Curved, non-planar or invalid hatches remain explicit failures.
Requires ezdxf and shapely. No source CAD changes are made.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import sys
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'))
from qto_cad_geometry import read_dxf_with_diagnostics

VERSION = 'qto-surface-regions/1.0'

class RegionError(ValueError):
    pass

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()

def polygon_from_rings(rings, *, allow_repair=False, repairs=None):
    """Normal HATCH islands use nesting parity, independent of winding direction."""
    if not rings: raise RegionError('no_boundary_paths')
    polys=[]
    for ring in rings:
        if len(ring)<4 or any(len(p)!=2 or any(not math.isfinite(v) for v in p) for p in ring):
            raise RegionError('invalid_ring_coordinates')
        if math.dist(ring[0],ring[-1])>1e-7: raise RegionError('open_boundary')
        p=Polygon(ring)
        if not p.is_valid:
            if not allow_repair:raise RegionError('invalid_or_zero_boundary')
            fixed=make_valid(p)
            if fixed.geom_type=='GeometryCollection':
                areas=[g for g in fixed.geoms if g.geom_type in ('Polygon','MultiPolygon')]
                fixed=unary_union(areas)
            if fixed.geom_type not in ('Polygon','MultiPolygon') or fixed.area<=0:raise RegionError('unrepairable_boundary')
            if repairs is not None:repairs.append({'kind':'make_valid','raw_shoelace_area_m2':p.area,'candidate_area_m2':fixed.area,'parts':len(fixed.geoms) if fixed.geom_type=='MultiPolygon' else 1})
            p=fixed
        if p.area<=0:raise RegionError('invalid_or_zero_boundary')
        if any(p.equals(q) for q in polys): raise RegionError('duplicate_boundary_path')
        polys.append(p)
    for i,p in enumerate(polys):
        for q in polys[:i]:
            if p.intersection(q).area>1e-10 and not (p.covers(q) or q.covers(p)):
                if not allow_repair:raise RegionError('crossing_boundary_paths')
                if repairs is not None:repairs.append({'kind':'crossing_path_parity','intersection_area_m2':p.intersection(q).area,'meaning':'按图形奇偶填充形成候选；不将交叉路径自动解释为物理孔洞'})
    geometry=Polygon()
    for p in sorted(polys,key=lambda p:-p.area): geometry=geometry.symmetric_difference(p)
    if geometry.is_empty or not geometry.is_valid: raise RegionError('invalid_island_result')
    return geometry

def reassemble_edge_rings(segments, tolerance, repairs):
    """Recover disjoint cycles using only endpoint joints within a declared tolerance.

    Every vertex must have degree two. No connection across a room or missing edge
    is invented; ambiguous proximity and branches fail instead of closing a polygon.
    """
    if not math.isfinite(tolerance) or tolerance<=0:raise RegionError('invalid_stitch_tolerance')
    nodes=[];grid={};adj=[];joints=[]
    def node(point):
        cell=tuple(math.floor(v/tolerance) for v in point);found=[]
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for idx in grid.get((cell[0]+dx,cell[1]+dy),[]):
                    if math.dist(point,nodes[idx])<=tolerance:found.append(idx)
        if len(found)>1:raise RegionError('ambiguous_endpoint_joint')
        if found:
            idx=found[0];distance=math.dist(point,nodes[idx])
            if distance>1e-12:joints.append({'source_m':point,'target_m':nodes[idx],'distance_m':distance})
            return idx
        idx=len(nodes);nodes.append(point);adj.append([]);grid.setdefault(cell,[]).append(idx);return idx
    for number,(a,b) in enumerate(segments):
        na,nb=node(a),node(b)
        if na==nb:raise RegionError('edge_collapsed_by_stitch_tolerance')
        adj[na].append((number,nb));adj[nb].append((number,na))
    if not adj or any(len(edges)!=2 for edges in adj):raise RegionError('edge_graph_not_closed_degree_two')
    remaining=set(range(len(segments)));rings=[]
    for initial in range(len(nodes)):
        available=[edge for edge in adj[initial] if edge[0] in remaining]
        if not available:continue
        ring=[nodes[initial]];current=initial
        while True:
            candidates=[edge for edge in adj[current] if edge[0] in remaining]
            if not candidates:raise RegionError('edge_graph_incomplete_cycle')
            edge,nxt=candidates[0];remaining.remove(edge);ring.append(nodes[nxt]);current=nxt
            if current==initial:break
        if len(ring)<4:raise RegionError('edge_graph_degenerate_cycle')
        rings.append(ring)
    if remaining:raise RegionError('edge_graph_unused_edges')
    if repairs is not None:repairs.append({'kind':'edge_cycles_reassembled','tolerance_m':tolerance,'input_edges':len(segments),'cycles':len(rings),'endpoint_joints':joints,'meaning':'只按原边端点连接重组；端点容差及重组仍保留候选，不跨断口造边'})
    return rings


def hatch_geometry(hatch, scale, *, allow_implicit=False, implicit_segments=None, allow_repair=False, repairs=None, max_implicit_gap_m=0.001, stitch_tolerance_m=0):
    if not math.isfinite(max_implicit_gap_m) or max_implicit_gap_m<=0:raise RegionError('invalid_max_implicit_gap')
    if not math.isfinite(stitch_tolerance_m) or stitch_tolerance_m<0:raise RegionError('invalid_stitch_tolerance')
    style=hatch.dxf.get('hatch_style',0)
    if style not in (0,1,2): raise RegionError('unknown_hatch_style')
    ext=hatch.dxf.get('extrusion',(0,0,1))
    if abs(ext[0])>1e-9 or abs(ext[1])>1e-9 or abs(ext[2]-1)>1e-9:
        raise RegionError('non_xy_plane')
    rings=[]
    # ezdxf's documented rendering filter honors EXTERNAL/OUTERMOST flags.
    # Filling style is a CAD fact, not proof that all physical holes are drawn.
    for path in hatch.paths.rendering_paths(style):
        if hasattr(path,'vertices'):
            if not path.is_closed: raise RegionError('open_polyline_path')
            if any(abs(p[2])>1e-12 for p in path.vertices): raise RegionError('curved_boundary')
            points=[[float(p[0])*scale,float(p[1])*scale] for p in path.vertices]
            if points and points[0]!=points[-1]:points.append(points[0])
        else:
            edges=list(path.edges)
            if any(type(e).__name__!='LineEdge' for e in edges):raise RegionError('curved_boundary')
            segments=[([float(v)*scale for v in e.start],[float(v)*scale for v in e.end]) for e in edges]
            gaps=[math.dist(segments[i-1][1],a) for i,(a,b) in enumerate(segments)]
            discontinuous=gaps and max(gaps)>1e-7
            disconnected_runs=segments and any(math.dist(segments[i-1][1],a)>max(1e-7,stitch_tolerance_m) for i,(a,b) in enumerate(segments) if i>0)
            if disconnected_runs and stitch_tolerance_m>0:
                if not allow_repair:raise RegionError('stitch_requires_geometry_repair')
                rings.extend(reassemble_edge_rings(segments,stitch_tolerance_m,repairs));continue
            points=[]
            for edge in edges:
                if type(edge).__name__!='LineEdge': raise RegionError('curved_boundary')
                start=[float(v)*scale for v in edge.start]
                end=[float(v)*scale for v in edge.end]
                if points and math.dist(points[-1],start)>1e-7:
                    if not allow_implicit: raise RegionError('disconnected_edges')
                    if math.dist(points[-1],start)>max_implicit_gap_m:raise RegionError('implicit_gap_exceeds_declared_limit')
                    if implicit_segments is not None: implicit_segments.append({'kind':'between_edges','start_m':points[-1],'end_m':start,'length_m':math.dist(points[-1],start)})
                    points.append(start)
                if not points:points.append(start)
                points.append(end)
            if points and math.dist(points[0],points[-1])<=1e-7:points[-1]=points[0]
            elif points:
                if not allow_implicit: raise RegionError('open_boundary')
                if math.dist(points[-1],points[0])>max_implicit_gap_m:raise RegionError('implicit_gap_exceeds_declared_limit')
                if implicit_segments is not None: implicit_segments.append({'kind':'closing_edge','start_m':points[-1],'end_m':points[0],'length_m':math.dist(points[-1],points[0])})
                points.append(points[0])
        rings.append(points)
    return polygon_from_rings(rings,allow_repair=allow_repair,repairs=repairs)

def measure_region_group(regions):
    """Union explicitly assigned regions from ONE physical view/material group.

    Callers must first assign the view and material; same coordinates in separate
    views or levels are not an automatic duplicate. No such inference here.
    """
    ids=[r['region_id'] for r in regions]
    if not ids or len(ids)!=len(set(ids)):raise RegionError('empty_or_repeated_region_ids')
    geometries=[]
    for r in regions:
        ps=[Polygon(p['exterior_m'],p.get('holes_m',[])) for p in r['polygons']]
        if any(not p.is_valid or p.area<=0 for p in ps):raise RegionError('invalid_group_polygon')
        geometries.append(unary_union(ps))
    g=unary_union(geometries)
    raw=sum(p.area for p in geometries)
    return {'region_ids':ids,'sum_before_union_m2':raw,'overlap_removed_m2':raw-g.area,
            'area_m2':g.area,'perimeter_m':g.length,'polygons':geometry_record(g),
            'candidate_region_ids':[r['region_id'] for r in regions if r['status']!='geometry_extracted']}

def geometry_record(geometry):
    parts=[geometry] if geometry.geom_type=='Polygon' else list(geometry.geoms)
    return [{'exterior_m':[list(p) for p in g.exterior.coords],
             'holes_m':[[list(p) for p in r.coords] for r in g.interiors]} for g in parts]

def locator_bounds(hatch,scale):
    points=[]
    for p in hatch.paths:
        if hasattr(p,'vertices'):points.extend((float(v[0])*scale,float(v[1])*scale) for v in p.vertices)
        else:
            for e in p.edges:
                if type(e).__name__=='LineEdge':points.extend([tuple(float(v)*scale for v in e.start),tuple(float(v)*scale for v in e.end)])
    # Locator only: curved edges are never approved from endpoint bounds.
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)] if points else None

def walk(entities, path=(), depth=0):
    if depth>32: raise RegionError('block_depth_exceeded')
    for i,e in enumerate(entities):
        source=e.source_of_copy or e
        key=str(source.dxf.get('handle') or i)
        identity=(*path,f'{i}:{key}')
        if e.dxftype()=='INSERT':
            if e.mcount>1:
                yield from walk(e.multi_insert(),(*identity,'array'),depth+1)
                continue
            skipped=[]
            children=list(e.virtual_entities(skipped_entity_callback=lambda entity,reason:skipped.append((entity,reason))))
            for entity,reason in skipped:
                yield identity,entity,'insert_transform_skipped:'+str(reason)
            yield from walk(children,identity,depth+1)
        else:yield identity,e,None

def extract(path, layer_regex, unit, unit_evidence, allow_implicit=False, allow_repair=False, max_implicit_gap_m=0.001, stitch_tolerance_m=0):
    doc,source_diagnostics=read_dxf_with_diagnostics(path)
    identity_issues=source_diagnostics['source_identity_issues']
    declared={'mm':4,'m':6}[unit]
    drawing_unit=int(doc.header.get('$INSUNITS',0))
    if drawing_unit not in (0,declared): raise RegionError('declared_unit_conflicts_with_insunits')
    scale={'mm':.001,'m':1.}[unit]
    pattern=re.compile(layer_regex)
    rows=[]; failures=[]; texts=[]; proxy_count=0
    for identity,e,failure in walk(doc.modelspace()):
        layer=e.dxf.layer
        if e.dxftype()=='ACAD_PROXY_ENTITY':proxy_count+=1
        if failure:
            failures.append({'entity_path':'/'.join(identity),'layer':layer,'reason':failure});continue
        if e.dxftype() in ('TEXT','MTEXT'):
            txt=e.dxf.text if e.dxftype()=='TEXT' else e.plain_text()
            if '平面图' in txt or 'CH' in txt or any(x in layer for x in ['房间名称','SPACE']):
                texts.append({'entity_path':'/'.join(identity),'text':txt,'point_m':[float(v)*scale for v in e.dxf.insert][:2],'layer':layer})
        if e.dxftype()!='HATCH' or not pattern.search(layer):continue
        key='/'.join(identity)
        base={'region_id':'REG-'+hashlib.sha256(key.encode()).hexdigest()[:16],
              'entity_path':key,'layer':layer,'pattern':e.dxf.pattern_name,
              'pattern_scale':e.dxf.pattern_scale,'pattern_angle':e.dxf.pattern_angle,
              'hatch_style':e.dxf.hatch_style,'boundary_path_count':len(e.paths),
              'entity_invisible':bool(e.dxf.get('invisible',0)),
              'layer_off':doc.layers.get(layer).is_off() if layer in doc.layers else None,
              'layer_frozen':doc.layers.get(layer).is_frozen() if layer in doc.layers else None,
              'rendered_path_count':len(list(e.paths.rendering_paths(e.dxf.hatch_style))),
              'locator_bounds_m':locator_bounds(e,scale),
              'pattern_lines':[{'angle':l.angle,'offset_drawing_units':list(l.offset),'dash_length_items':list(l.dash_length_items)} for l in e.pattern.lines] if e.pattern else []}
        try:
            implicit=[];repairs=[]
            g=hatch_geometry(e,scale,allow_implicit=allow_implicit,implicit_segments=implicit,allow_repair=allow_repair,repairs=repairs,max_implicit_gap_m=max_implicit_gap_m,stitch_tolerance_m=stitch_tolerance_m)
            geometry_status='candidate_geometry_repair' if repairs else ('candidate_implicit_segments' if implicit else 'geometry_extracted')
            base.update(status='candidate_source_identity_conflict' if identity_issues else geometry_status,
                        geometry_status_before_source_check=geometry_status,
                        source_identity_status='conflict' if identity_issues else 'no_conflict_detected',
                        implicit_segments=implicit,geometry_repairs=repairs,area_m2=g.area,perimeter_m=g.length,
                        bounds_m=list(g.bounds),anchor_m=list(g.representative_point().coords)[0],polygons=geometry_record(g))
            rows.append(base)
        except (RegionError,ValueError,TypeError,OverflowError) as exc:
            failures.append(dict(base,reason=str(exc)))
    return {'schema_version':VERSION,'source_dxf':str(path.resolve()),'source_sha256':sha256(path),
            'status':'partial_source_identity_conflict' if identity_issues else 'selected_regions_analyzed',
            'source_identity_status':'conflict' if identity_issues else 'no_conflict_detected',
            'source_identity_issues':identity_issues,'source_read_warnings':source_diagnostics['source_read_warnings'],
            'source_unit':unit,'insunits':drawing_unit,'unit_evidence':unit_evidence,
            'max_implicit_gap_m':max_implicit_gap_m,'stitch_tolerance_m':stitch_tolerance_m,
            'layer_regex':layer_regex,'allow_implicit_segments':allow_implicit,'allow_geometry_repair':allow_repair,'interpretation':'未分配视图及材料的中性面域；不得直接合计为工程量；隐式补线与拓扑修补只输出候选，必须回图确认',
            'regions':rows,'failures':failures,'locator_texts':texts,
            'proxy_entities_not_expanded':proxy_count}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dxf',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--layers',required=True)
    p.add_argument('--unit',choices=['mm','m'],required=True)
    p.add_argument('--unit-evidence',required=True)
    p.add_argument('--allow-implicit-segments',action='store_true',help='显式记录隐式闭合直线，输出候选面域而非已确认边界')
    p.add_argument('--allow-geometry-repair',action='store_true',help='自交拓扑修补仅输出候选，并保留修补差异')
    p.add_argument('--max-implicit-gap-mm',type=float,default=1.0,help='隐式补线最大长度毫米；超限失败，不自动跨房间造边')
    p.add_argument('--stitch-tolerance-mm',type=float,default=0.0,help='显式端点容差毫米；只重组所有顶点度数为2的闭环，结果仍候选')
    a=p.parse_args()
    if a.output.exists(): raise SystemExit('输出已存在，请使用新轮次文件名')
    before=sha256(a.source_dxf)
    result=extract(a.source_dxf,a.layers,a.unit,a.unit_evidence,a.allow_implicit_segments,a.allow_geometry_repair,a.max_implicit_gap_mm/1000,a.stitch_tolerance_mm/1000)
    if before!=sha256(a.source_dxf): raise SystemExit('源文件在提取过程中变化')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'regions':len(result['regions']),'failures':len(result['failures']),
          'proxy_entities_not_expanded':result['proxy_entities_not_expanded'],'output':str(a.output)},ensure_ascii=False))

if __name__=='__main__':main()
