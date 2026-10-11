"""Rectangular material partitions in one explicitly declared surface frame.

This module performs geometry only. The surface calculator resolves hashed
sources, evidence ownership, scenarios and the source-edge frame binding.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from qto_cad_source_contract import canonical_scale


class MaterialPartitionError(ValueError):
    pass


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise MaterialPartitionError(f'{label}须为非空文本')
    return value


def rectangle(value, label):
    if not isinstance(value, dict):
        raise MaterialPartitionError(f'{label}须为矩形事实对象')
    coords = value.get('rectangle')
    if not isinstance(coords, list) or len(coords) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in coords):
        raise MaterialPartitionError(f'{label}.rectangle须为四个有限数值')
    try:
        scale = canonical_scale(value.get('unit'))
    except ValueError as exc:
        raise MaterialPartitionError(f'{label}必须声明物理长度单位: {exc}') from exc
    coords = [float(v)*scale for v in coords]
    if not all(math.isfinite(v) for v in coords) or coords[0] >= coords[2] or coords[1] >= coords[3]:
        raise MaterialPartitionError(f'{label}须为有正宽高的有限矩形')
    if not math.isfinite((coords[2]-coords[0])*(coords[3]-coords[1])):
        raise MaterialPartitionError(f'{label}矩形面积溢出')
    return coords


def polygons(geometry):
    """Return polygons with holes intact; never count a hole as a second face."""
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        parts = [geometry]
    elif geometry.geom_type in {'MultiPolygon','GeometryCollection'}:
        return [p for child in sorted(geometry.geoms, key=lambda g:(g.bounds,g.area)) for p in polygons(child)]
    else:
        return []  # Touching boundaries have no area.
    return [{'outer': [[float(x),float(y)] for x,y in p.exterior.coords],
             'holes': [[[float(x),float(y)] for x,y in ring.coords] for ring in p.interiors],
             'area_m2': float(p.area)} for p in parts if p.area > 0]


def region_geometry(geometry):
    return {'area_m2':float(geometry.area),'polygons_m':polygons(geometry),
            'rectangles_m':rectangle_cells(geometry)}


def rectangle_cells(geometry):
    """Decompose the rectilinear result, preserving holes and disjoint pieces."""
    if geometry.is_empty or geometry.area==0:
        return []
    parts=polygons(geometry)
    xs=sorted({p[0] for part in parts for ring in [part['outer'],*part['holes']] for p in ring})
    _,bottom,_,top=geometry.bounds
    cells=[]
    for left,right in zip(xs,xs[1:]):
        if left>=right:continue
        clipped=intersect(geometry,box(left,bottom,right,top))
        for part in polygons(clipped):
            points=part['outer']
            ymin=min(p[1] for p in points);ymax=max(p[1] for p in points)
            if part['holes'] or not math.isclose((right-left)*(ymax-ymin),part['area_m2'],rel_tol=1e-12,abs_tol=1e-8):
                raise MaterialPartitionError('分区结果非可验证的正交矩形分解')
            cells.append([left,ymin,right,ymax])
    # Merge adjacent x slabs only when their vertical intervals are identical.
    merged_cells=[]
    for cell in sorted(cells,key=lambda r:(r[1],r[3],r[0],r[2])):
        if merged_cells and merged_cells[-1][1]==cell[1] and merged_cells[-1][3]==cell[3] and merged_cells[-1][2]==cell[0]:
            merged_cells[-1][2]=cell[2]
        else:merged_cells.append(cell[:])
    total=math.fsum((r[2]-r[0])*(r[3]-r[1]) for r in merged_cells)
    if not math.isclose(total,float(geometry.area),rel_tol=1e-12,abs_tol=1e-8):
        raise MaterialPartitionError('不重叠矩形分解面积与分区不一致')
    return sorted(merged_cells)


def area_only(geometry):
    """Discard boundary-only contacts before subsequent area overlays."""
    if geometry.is_empty:
        return Polygon()
    if geometry.geom_type=='Polygon':
        return geometry if geometry.area>0 else Polygon()
    if geometry.geom_type in {'MultiPolygon','GeometryCollection'}:
        parts=[area_only(child) for child in geometry.geoms]
        parts=[part for part in parts if not part.is_empty]
        return unary_union(parts) if parts else Polygon()
    return Polygon()


def merged(geometries):
    parts=[area_only(g) for g in geometries if not g.is_empty]
    return area_only(unary_union(parts)) if parts else Polygon()


def intersect(left,right):
    if left.is_empty or right.is_empty:
        return Polygon()
    return area_only(left.intersection(right))


def subtract(left,right):
    if left.is_empty:return Polygon()
    if right.is_empty:return area_only(left)
    return area_only(left.difference(right))


def partition_material_regions(plan):
    if not isinstance(plan, dict):
        raise MaterialPartitionError('材料分区须为对象')
    for name in ('partition_id','parent_surface_id','layer_scope_id'):
        text(plan.get(name),name)
    scenario = plan.get('scenario')
    if not isinstance(scenario,dict) or scenario.get('status') not in {'determined','conditional'}:
        raise MaterialPartitionError('材料分区须明确determined或conditional方案状态')
    for name in ('scenario_id','condition'):
        text(scenario.get(name),'scenario.'+name)
    for name in ('actual_openings','material_regions','unknown_regions'):
        if not isinstance(plan.get(name),list):
            raise MaterialPartitionError(f'{name}须显式给数组；缺字段不等于没有')
    if 'base_material' not in plan:
        raise MaterialPartitionError('base_material须明确有源对象或null，不默认补全材料')
    coverage=plan.get('coverage',{'mode':'full_surface'})
    if not isinstance(coverage,dict) or coverage.get('mode') not in {'full_surface','selected_regions'}:
        raise MaterialPartitionError('coverage.mode须为full_surface或有源selected_regions')
    selected_only=coverage['mode']=='selected_regions'
    if selected_only:
        for key in ('physical_object_id','basis','source_evidence_ref'):
            text(coverage.get(key),'coverage.'+key)
        if plan['base_material'] is not None:
            raise MaterialPartitionError('selected_regions只计显式区域，不能同时声明base_material补满母面')

    face_record = plan.get('face')
    if not isinstance(face_record,dict):
        raise MaterialPartitionError('缺少母面矩形')
    text(face_record.get('region_id'),'face.region_id')
    text(face_record.get('physical_object_id'),'face.physical_object_id')
    face_rect = rectangle(face_record,'face')
    face = box(*face_rect)
    audit_records = []

    def context(record, label):
        expected={'parent_surface_id':plan['parent_surface_id'],'layer_scope_id':plan['layer_scope_id'],
                  'scenario_id':scenario['scenario_id'],
                  'coordinate_system_id':plan.get('local_coordinate_frame',{}).get('coordinate_system_id')}
        for key,value in expected.items():
            if key in record and record[key] != value:
                raise MaterialPartitionError(f'{label}与母面{key}不一致，不能跨层、场景或坐标系拼入')

    context(face_record,'face')
    if 'coverage' in plan:
        context(coverage,'coverage')

    def read(record, kind, id_key):
        if not isinstance(record,dict):
            raise MaterialPartitionError(f'{kind}记录须为对象')
        identifier = text(record.get(id_key),kind+'.'+id_key)
        context(record,kind+'.'+identifier)
        physical = text(record.get('physical_object_id'),kind+'.physical_object_id')
        coords = rectangle(record,kind+'.'+identifier)
        geom = box(*coords)
        entry = {'kind':kind,id_key:identifier,'physical_object_id':physical,'rectangle_m':coords,
                 'raw_area_m2':float(geom.area),'source_binding':record.get('source_binding')}
        audit_records.append(entry)
        return geom,entry

    opening_shapes=[]; seen=set()
    for record in plan['actual_openings']:
        geom,entry=read(record,'actual_opening','opening_id')
        if entry['opening_id'] in seen:
            raise MaterialPartitionError('真实洞口opening_id重复')
        seen.add(entry['opening_id'])
        clipped=intersect(geom,face)
        entry.update(clipped_to_face=region_geometry(clipped))
        opening_shapes.append(clipped)
    opening_union=merged(opening_shapes)
    available=subtract(face,opening_union)

    materials={}; region_ids=set()
    for record in plan['material_regions']:
        geom,entry=read(record,'material_region','region_id')
        key=entry['region_id']
        if key in region_ids:
            raise MaterialPartitionError('材料/未知区域region_id重复')
        region_ids.add(key)
        material=text(record.get('material_id'),key+'.material_id')
        effective=intersect(geom,available)
        entry.update(material_id=material,effective_after_openings=region_geometry(effective))
        material_row=materials.setdefault(material,{'shapes':[],'region_ids':[],'physical_object_ids':[]})
        material_row['shapes'].append(effective)
        material_row['region_ids'].append(key)
        material_row['physical_object_ids'].append(entry['physical_object_id'])

    unknown_shapes=[]
    for record in plan['unknown_regions']:
        geom,entry=read(record,'unknown_region','region_id')
        key=entry['region_id']
        if key in region_ids:
            raise MaterialPartitionError('材料/未知区域region_id重复')
        region_ids.add(key)
        entry['reason']=text(record.get('reason'),key+'.reason')
        effective=intersect(geom,available)
        entry['effective_after_openings']=region_geometry(effective)
        unknown_shapes.append(effective)
    explicit_unknown=merged(unknown_shapes)
    for value in materials.values():
        value['union']=merged(value['shapes'])

    conflicts=[]; conflict_shapes=[]
    material_keys=sorted(materials)
    for index,left in enumerate(material_keys):
        for right in material_keys[index+1:]:
            intersection=intersect(materials[left]['union'],materials[right]['union'])
            if intersection.area > 0:
                conflicts.append({'material_ids':[left,right],**region_geometry(intersection)})
                conflict_shapes.append(intersection)
    conflict_union=merged(conflict_shapes)
    raw_material_union=merged([v['union'] for v in materials.values()])
    unassigned=subtract(available,merged([raw_material_union,explicit_unknown]))
    withheld=merged([explicit_unknown,conflict_union])
    output_materials={}
    for key,value in sorted(materials.items()):
        known=subtract(value['union'],withheld)
        output_materials[key]={
            'material_id':key,'region_ids':value['region_ids'],'physical_object_ids':sorted(set(value['physical_object_ids'])),
            'same_material_union_area_m2':float(value['union'].area),
            'same_material_overlap_removed_m2':float(math.fsum(g.area for g in value['shapes'])-value['union'].area),
            'withheld_for_unknown_or_conflict_m2':float(intersect(value['union'],withheld).area),
            'effective_known_geometry':region_geometry(known),'_known':known,'base_complement_area_m2':0.}

    base=plan['base_material'];base_audit=None
    if base is not None:
        if not isinstance(base,dict):
            raise MaterialPartitionError('base_material须为有源对象或null')
        context(base,'base_material')
        material=text(base.get('material_id'),'base_material.material_id')
        physical=text(base.get('physical_object_id'),'base_material.physical_object_id')
        basis=text(base.get('basis'),'base_material.basis')
        base_audit={'material_id':material,'physical_object_id':physical,'basis':basis,
                    'source_binding':base.get('source_binding'),**region_geometry(unassigned)}
        row=output_materials.setdefault(material,{'material_id':material,'region_ids':[],'physical_object_ids':[],
              'same_material_union_area_m2':0.,'same_material_overlap_removed_m2':0.,'withheld_for_unknown_or_conflict_m2':0.,
              '_known':Polygon(),'base_complement_area_m2':0.})
        row['_known']=merged([row['_known'],unassigned])
        row['effective_known_geometry']=region_geometry(row['_known'])
        row['base_complement_area_m2']=float(unassigned.area)
        row['physical_object_ids']=sorted(set(row['physical_object_ids']+[physical]))
        unassigned=Polygon()

    outside_selected_scope=unassigned if selected_only else Polygon()
    if selected_only:unassigned=Polygon()
    unknown=merged([withheld,unassigned])
    total_known=math.fsum(v['_known'].area for v in output_materials.values())
    total=float(total_known+unknown.area+outside_selected_scope.area)
    if not math.isfinite(total) or not math.isclose(total,float(available.area),rel_tol=1e-12,abs_tol=1e-8):
        raise MaterialPartitionError('材料分区几何守恒失败')
    for value in output_materials.values():
        value.pop('_known')
    return {
        'partition_id':plan['partition_id'],'parent_surface_id':plan['parent_surface_id'],
        'layer_scope_id':plan['layer_scope_id'],'scenario':scenario,
        'status':'partial' if unknown.area > 0 else ('selected_regions_measured' if selected_only else 'geometrically_partitioned'),
        'coverage':coverage,'full_surface_coverage':not selected_only and unknown.area==0,
        'face_rectangle_m':face_rect,'gross_face_area_m2':float(face.area),
        'actual_opening_union':region_geometry(opening_union),
        'actual_opening_overlap_removed_m2':float(math.fsum(g.area for g in opening_shapes)-opening_union.area),
        'net_face_area_m2':float(available.area),'material_regions':list(output_materials.values()),
        'material_conflicts':conflicts,'material_conflict_union':region_geometry(conflict_union),
        'explicit_unknown':region_geometry(explicit_unknown),
        'conflict_excluding_explicit_unknown':region_geometry(subtract(conflict_union,explicit_unknown)),
        'unassigned_without_base':region_geometry(unassigned),
        'outside_selected_scope':region_geometry(outside_selected_scope),
        'unknown_region':region_geometry(unknown),'known_material_total_area_m2':float(total_known),
        'base_material_complement':base_audit,'records':audit_records,
        'face_source_binding':face_record.get('source_binding'),
        'partition_source_binding':plan.get('source_binding'),
        'algorithm_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'area_conservation':True,'business_correctness_proven':False,
        'note':'几何守恒仅检查覆盖分账；异材料交叠和明确未知区域均保留未知，不设材料优先级。'
    }
