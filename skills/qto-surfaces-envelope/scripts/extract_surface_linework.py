"""Source-bound straight linework polygonization; never infer rooms or finishes.

Only explicitly selected source segments are consumed. Intersections are noded,
but missing edges are not created. Model-interpreted measurement boundaries are
separate, source-endpoint-bound inputs, never silently drawn CAD evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from shapely import set_precision
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize_full, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'))
from qto_cad_geometry import (IDENTITY, compose_matrix, entity_geometry,
                              read_dxf_with_diagnostics, transform_geometry, transform_point)
from qto_cad_source_contract import canonical_scale, verify_source_file

VERSION = 'qto-surface-linework/1.0'
UNITS = {1:'in', 2:'ft', 4:'mm', 5:'cm', 6:'m', 7:'km'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve(doc, path, context_issues=None):
    """Resolve ownership and every instance transform, including explicit array cells."""
    require(isinstance(path, list) and path, '完整源实例路径必填')
    matrix, owner = IDENTITY, doc.modelspace().block_record_handle
    for index, step in enumerate(path):
        handle = step.get('handle') if isinstance(step, dict) else step
        require(isinstance(handle, str) and handle.strip(), '图元句柄无效')
        entity = doc.entitydb.get(handle.upper())
        require(entity is not None and entity.dxf.owner == owner, '源路径不属于模型空间或指定父块')
        if index == len(path)-1:
            require(not isinstance(step, dict), '阵列单元只能用于父块路径')
            return entity, matrix
        require(entity.dxftype() == 'INSERT', '源路径中间节点必须为 INSERT')
        rows, columns = int(entity.dxf.row_count), int(entity.dxf.column_count)
        if rows > 1 or columns > 1:
            require(isinstance(step, dict), '阵列实例须明确 row 与 column，不能只取首项')
            row, column = step.get('row'), step.get('column')
            require(type(row) is int and type(column) is int and 0 <= row < rows and 0 <= column < columns, '阵列单元越界')
            # DXF array spacing is rotated in the INSERT OCS, without block scale.
            from ezdxf.math import Vec3
            copy = entity.copy()
            offset = Vec3(column*entity.dxf.column_spacing, row*entity.dxf.row_spacing, 0).rotate_deg(entity.dxf.rotation)
            copy.dxf.insert = entity.dxf.insert + offset
            entity_for_matrix = copy
        else:
            require(not isinstance(step, dict), '普通块无需阵列单元标记')
            entity_for_matrix = entity
        block = doc.blocks.get(entity.dxf.name)
        require(block is not None and not block.block.is_xref and not block.block.is_xref_overlay, '外部参照须先受控读取，不能按普通块假定完整')
        parent_facts = entity_geometry(entity_for_matrix)
        codes = [code for code in parent_facts.get('geometry_issue_codes', [])
                 if code != 'array_instances_not_expanded']  # this cell was explicitly resolved above
        if codes and context_issues is not None:
            context_issues.append({'parent_path':path[:index+1], 'issue_codes':codes,
                                   'geometry_issues':parent_facts.get('geometry_issues', []),
                                   'recommended_actions':parent_facts.get('recommended_actions', [])})
        matrix = compose_matrix(matrix, parent_facts['insert_matrix_wcs'])
        owner = block.block_record_handle


def source_parts(doc, reference, context_issues=None):
    entity, matrix = resolve(doc, reference['path'], context_issues)
    require(entity.dxftype() in ('LINE', 'LWPOLYLINE', 'POLYLINE'), '普通线网当前只接受 LINE 与直线多段线')
    facts = entity_geometry(entity)
    require(facts.get('geometry') and not facts.get('geometry_issues'), '源实体没有可计算曲线')
    parts = transform_geometry(facts['geometry'], matrix)['parts']
    if 'part' in reference:
        index = reference['part']
        require(type(index) is int and 0 <= index < len(parts), '源段索引越界')
        return entity, [(index, parts[index])]
    return entity, list(enumerate(parts))


def endpoint(doc, reference, context_issues=None):
    require(reference.get('end') in ('start', 'end'), '解释边界必须引用真实线段端点')
    _, parts = source_parts(doc, reference, context_issues)
    require(len(parts) == 1 and parts[0][1]['kind'] == 'line', '端点引用须明确单一直线段')
    return parts[0][1][reference['end']]


def extract(data):
    require(data.get('schema_version') == VERSION, '普通线网输入版本不支持')
    source = data.get('drawing')
    verify_source_file(source, '线网源图')
    doc, source_diagnostics = read_dxf_with_diagnostics(source['path'])
    identity_issues = source_diagnostics['source_identity_issues']
    scale = canonical_scale(data.get('unit'))
    insunits = int(doc.header.get('$INSUNITS', 0))
    require(insunits == 0 or (insunits in UNITS and math.isclose(canonical_scale(UNITS[insunits]), scale)), '声明单位与 INSUNITS 冲突')
    require(isinstance(data.get('unit_evidence'), str) and data['unit_evidence'].strip(), '单位依据必填')
    scope = data.get('scope', {})
    require(scope.get('view_id') and scope.get('interpretation'), '视图范围和模型边界解释必填')
    bounds = scope.get('bounds_raw')
    require(isinstance(bounds, list) and len(bounds) == 4 and all(finite(v) for v in bounds) and bounds[0] < bounds[2] and bounds[1] < bounds[3], '范围须为有限非退化矩形')
    z = scope.get('plane_z_raw')
    tolerance = scope.get('plane_tolerance_m')
    require(finite(z) and finite(tolerance) and tolerance >= 0, '平面标高及其物理容差须明确')
    grid = data.get('precision_grid_m', 0)
    require(finite(grid) and grid >= 0, '精度网格须为非负物理长度')
    if grid:
        require(data.get('precision_evidence'), '精度归整须有当前图纸依据')
    ox, oy = bounds[:2]
    region = box(0, 0, (bounds[2]-ox)*scale, (bounds[3]-oy)*scale)
    def local(point):
        require(all(finite(v) for v in point) and len(point) == 3, '源点坐标无效')
        require(abs(point[2]-z)*scale <= tolerance, '源边不在选定平面，不能投影混层')
        return ((point[0]-ox)*scale, (point[1]-oy)*scale)
    def absolute(point):
        return [point[0]+ox*scale, point[1]+oy*scale]
    records, failures, excluded, normalized, source_context_issues = [], [], [], [], []
    selections = data.get('segments')
    require(isinstance(selections, list) and selections, '已解释的源线网选择不能为空')
    seen = set()
    for reference in selections:
        context = []
        try:
            entity, parts = source_parts(doc, reference, context)
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({'reference':reference, 'reason':str(exc)})
            continue
        source_context_issues.extend(dict(issue, selected_path=reference['path']) for issue in context)
        for index, part in parts:
            try:
                key = json.dumps([reference['path'], index], sort_keys=True)
                require(key not in seen, '源段重复选择')
                seen.add(key)
                require(part['kind'] == 'line', '曲线不得以端点弦线替代')
                a, b = local(part['start']), local(part['end'])
                line = LineString([a, b])
                require(line.length > 0, '零长源线段')
                info = {'path':reference['path'], 'part':index, 'layer':entity.dxf.layer, 'origin':'drawn'}
                if context:
                    info['context_issues'] = context
                if not region.covers(line):
                    excluded.append(dict(info, reason='源段跨出或位于视图范围之外；未裁切造边'))
                    continue
                if grid:
                    adjusted = set_precision(line, grid)
                    require(not adjusted.is_empty and adjusted.length > 0, '源段被精度网格压塌')
                    if not line.equals(adjusted):
                        normalized.append(dict(info, before_m=[absolute(p) for p in line.coords], after_m=[absolute(p) for p in adjusted.coords]))
                    line = adjusted
                records.append(dict(info, line=line))
            except (ValueError, KeyError, TypeError) as exc:
                failures.append({'reference':dict(reference, part=index), 'reason':str(exc)})
    links = data.get('interpreted_links', [])
    require(isinstance(links, list), '解释边界须为列表')
    link_ids = set()
    for link in links:
        require(link.get('id') and link['id'] not in link_ids, '解释边界编号缺失或重复')
        link_ids.add(link['id'])
        require(link.get('purpose') and link.get('basis') and link.get('surface_scope'), '解释边界须给出用途、依据和对应表面范围')
        maximum = link.get('max_length_m')
        require(finite(maximum) and maximum > 0, '解释边界须声明有依据的最大长度')
        context = []
        a, b = local(endpoint(doc, link['from'], context)), local(endpoint(doc, link['to'], context))
        source_context_issues.extend(dict(issue, interpreted_link_id=link['id']) for issue in context)
        line = LineString([a, b])
        require(0 < line.length <= maximum, '解释边界为零长或超过声明跨度')
        require(region.covers(line), '解释边界超出选定范围')
        # Both end points must participate in the selected boundary; unrelated
        # labels, another view, or an arbitrary coordinate cannot close the room.
        for point, reference in zip((Point(a), Point(b)), (link['from'], link['to'])):
            require(any(r['origin']=='drawn' and r['path']==reference['path'] and r['part']==reference.get('part',0)
                        and min(point.distance(Point(r['line'].coords[0])), point.distance(Point(r['line'].coords[-1]))) <= max(grid*math.sqrt(2), 1e-10)
                        for r in records), '解释边界端点未绑定已选源路径及线网端点')
        if grid:
            line = set_precision(line, grid)
        require(not line.is_empty and line.length > 0, '解释边界被精度归整压塌')
        require(not any(line.intersection(r['line']).length > 1e-10 for r in records), '解释边界与已画线或其他解释边界重叠')
        records.append({'origin':'interpreted', 'id':link['id'], 'from':link['from'], 'to':link['to'], 'purpose':link['purpose'], 'basis':link['basis'], 'surface_scope':link['surface_scope'], 'context_issues':context, 'line':line})
    require(records, '范围内没有可分析直线，须补读取而非输出零面积')
    lines = [r['line'] for r in records]
    _, _, _, raw_invalid = polygonize_full(lines)
    network = unary_union(lines)
    pieces = list(network.geoms) if hasattr(network, 'geoms') else [network]
    polygons, cuts, dangles, invalid = polygonize_full(pieces)
    epsilon = max(1e-10, network.length*1e-12)
    def provenance(line):
        refs = []
        for record in records:
            # Exact intersections after noding normally suffice; the buffer only
            # handles floating arithmetic, not drafting gaps or proximity ties.
            overlap = line.intersection(record['line']).length
            if overlap > epsilon or (line.length > epsilon and record['line'].buffer(epsilon, cap_style=2).covers(line)):
                refs.append({k:v for k,v in record.items() if k != 'line'})
        return refs
    def edge_record(line):
        return {'points_m':[absolute(p) for p in line.coords], 'length_m':line.length, 'sources':provenance(line)}
    def edges(geometry):
        return [edge_record(g) for g in geometry.geoms]
    faces = []
    for polygon in sorted(polygons.geoms, key=lambda p:(p.bounds, p.area)):
        rings = [polygon.exterior, *polygon.interiors]
        boundary = []
        for ring in rings:
            boundary.extend(edge_record(LineString([a,b])) for a,b in zip(ring.coords, list(ring.coords)[1:]))
        identity = hashlib.sha256(polygon.normalize().wkb).hexdigest()[:16]
        faces.append({'face_id':'FACE-'+identity, 'status':'candidate_face_requires_surface_interpretation', 'area_m2':polygon.area, 'perimeter_m':polygon.length,
                      'exterior_m':[absolute(p) for p in polygon.exterior.coords], 'holes_m':[[absolute(p) for p in r.coords] for r in polygon.interiors],
                      'anchor_m':absolute(polygon.representative_point().coords[0]), 'boundary':boundary})
    label_candidates = []
    for path in data.get('label_paths', []):
        context = []
        entity, matrix = resolve(doc, path, context)
        source_context_issues.extend(dict(issue, label_path=path) for issue in context)
        require(entity.dxftype() in ('TEXT','MTEXT'), '房间文字候选须引用 TEXT/MTEXT')
        facts = entity_geometry(entity)
        p = Point(local(transform_point(facts['anchor'], matrix)))
        text = entity.plain_text() if entity.dxftype() == 'MTEXT' else entity.dxf.text
        matches = []
        for face in faces:
            polygon = Polygon([(x-ox*scale,y-oy*scale) for x,y in face['exterior_m']], [[(x-ox*scale,y-oy*scale) for x,y in h] for h in face['holes_m']])
            if polygon.covers(p):
                matches.append({'face_id':face['face_id'], 'relation':'inside' if polygon.contains(p) else 'boundary'})
        label_candidates.append({'path':path, 'text':text, 'faces':matches, 'status':'spatial_candidate_only', 'context_issues':context})
    verify_source_file(source, '线网源图')
    return {'schema_version':VERSION, 'drawing':source, 'source_immutable_verified':True,
            'source_identity_status':'conflict' if identity_issues else 'no_conflict_detected',
            'source_identity_issues':identity_issues, 'source_read_warnings':source_diagnostics['source_read_warnings'],
            'unit':data['unit'], 'unit_evidence':data['unit_evidence'], 'insunits':insunits, 'scope':scope,
            'precision_grid_m':grid, 'coordinate_origin_raw':[ox,oy,z], 'status':'partial_selected_linework' if failures or excluded or source_context_issues or identity_issues else 'selected_linework_analyzed',
            'interpretation':'有效面仅是线网几何；不等同房间、装修材料或完整算量范围。解释分界单列，不自动补门洞。',
            'faces':faces, 'cut_edges':edges(cuts), 'dangles':edges(dangles), 'invalid_rings':edges(invalid), 'pre_noding_invalid_rings':edges(raw_invalid),
            'failures':failures, 'excluded':excluded, 'source_context_issues':source_context_issues, 'precision_changes':normalized, 'label_candidates':label_candidates,
            'drawn_length_before_union_m':sum(r['line'].length for r in records if r['origin']=='drawn'),
            'interpreted_length_m':sum(r['line'].length for r in records if r['origin']=='interpreted'),
            'network_length_m':network.length,
            'coincident_length_removed_m':sum(r['line'].length for r in records)-network.length}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), '输出已存在；使用新轮次文件名')
    result = extract(json.loads(args.input.read_text(encoding='utf-8-sig')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:len(result[k]) for k in ('faces','cut_edges','dangles','failures','excluded')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
