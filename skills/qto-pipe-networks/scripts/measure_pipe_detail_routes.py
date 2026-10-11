"""Measure reviewed detail axes from source entities, never manual allowances.

This is a geometric subroute measurer, not an automatic valve-group recognizer.
All restored spans remain separate from drawn lengths and billable quantities.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'))
from qto_cad_geometry import (GEOMETRY_SCHEMA, IDENTITY, compose_matrix, conic_point, entity_geometry,
                              summarize, transform_geometry, dimension_measurement_facts,
                              read_dxf_with_diagnostics)
from qto_cad_source_contract import canonical_scale, verify_source_file

VERSION = 'qto-pipe-detail-routes/1.0'
UNITS = {1: 'in', 2: 'ft', 4: 'mm', 5: 'cm', 6: 'm', 7: 'km'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def part_point(part, fraction):
    require(isinstance(fraction, (int, float)) and not isinstance(fraction, bool)
            and math.isfinite(fraction) and 0 <= fraction <= 1, '源点参数须在0到1之间')
    if part['kind'] == 'line':
        return [a + (b-a)*fraction for a, b in zip(part['start'], part['end'])]
    return conic_point(part, part['start_param'] + part['sweep_param']*fraction)


class Drawing:
    def __init__(self, document):
        self.doc = document

    def resolve(self, path):
        require(isinstance(path, list) and path, '须提供完整图元实例路径')
        owner = self.doc.modelspace().block_record_handle
        matrix = IDENTITY
        for i, handle in enumerate(path):
            entity = self.doc.entitydb.get(str(handle).upper())
            require(entity is not None and entity.dxf.owner == owner, '图元路径不属于指定布局或父块')
            if i < len(path)-1:
                require(entity.dxftype() == 'INSERT', '路径中间节点必须是块实例')
                require(entity.dxf.row_count == entity.dxf.column_count == 1, '阵列块须先逐实例展开')
                matrix = compose_matrix(matrix, entity_geometry(entity)['insert_matrix_wcs'])
                owner = self.doc.blocks[entity.dxf.name].block_record_handle
        return entity, matrix

    def part(self, ref):
        entity, matrix = self.resolve(ref['path'])
        geometry = entity_geometry(entity)['geometry']
        require(geometry is not None, '所选实体不含可测量曲线')
        parts = transform_geometry(geometry, matrix)['parts']
        index = ref.get('part', 0)
        require(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(parts), '几何段索引无效')
        require('part' in ref or len(parts) == 1, '多段实体必须明确选择几何段')
        return parts[index]

    def point(self, ref):
        if 'intersection' in ref:
            require(len(ref['intersection']) == 2, '三通交点须引用两条源轴线')
            a, b = [self.part(r) for r in ref['intersection']]
            require(a['kind'] == b['kind'] == 'line', '三通交点只支持直线轴')
            u = [y-x for x,y in zip(a['start'],a['end'])]
            v = [y-x for x,y in zip(b['start'],b['end'])]
            w = [y-x for x,y in zip(a['start'],b['start'])]
            dot = lambda x,y: sum(p*q for p,q in zip(x,y))
            uu,vv,uv,wu,wv = dot(u,u),dot(v,v),dot(u,v),dot(w,u),dot(w,v)
            denominator = uu*vv-uv*uv
            require(denominator > 1e-12*uu*vv, '轴线平行或退化，不能求三通交点')
            t,s = (wu*vv-wv*uv)/denominator,(wu*uv-wv*uu)/denominator
            p = [x+t*d for x,d in zip(a['start'],u)]
            q = [x+s*d for x,d in zip(b['start'],v)]
            require(math.dist(p,q) <= 1e-6, '轴线异面，不能当作三通连接')
            entity, matrix = self.resolve(ref['enclosure_path'])
            geometry = entity_geometry(entity)['geometry']
            require(entity.dxftype() in ('LWPOLYLINE','POLYLINE') and geometry and geometry['closed'], '交点须有闭合三通轮廓证据')
            geometry = transform_geometry(geometry,matrix)
            require(bounds_contain(summarize(geometry)['bbox3d'],[p],1e-6), '轴线交点位于三通轮廓范围之外')
            return p
        return part_point(self.part(ref), ref['fraction'])


def bounds_contain(box, points, tolerance):
    return all(box[i]-tolerance <= p[i] <= box[i+3]+tolerance for p in points for i in range(3))


def overlapping_lines(a, b, tolerance):
    if a['kind'] != 'line' or b['kind'] != 'line':
        return False
    start, end = a['start'], a['end']
    length = math.dist(start, end)
    if length <= tolerance:
        return False
    direction = [(y-x)/length for x, y in zip(start, end)]
    projections = []
    for point in (b['start'], b['end']):
        t = sum((v-x)*d for v, x, d in zip(point, start, direction))
        if math.dist(point, [x+t*d for x, d in zip(start, direction)]) > tolerance:
            return False
        projections.append(t)
    return min(length, max(projections)) - max(0., min(projections)) > tolerance


def same_curve(a, b, tolerance):
    pa, pb = [[part_point(p, i/8) for i in range(9)] for p in (a, b)]
    return any(all(math.dist(x, y) <= tolerance for x, y in zip(pa, other)) for other in (pb, pb[::-1]))


def overlapping_conics(a, b, tolerance):
    """Detect partial overlaps even with reversed/rotated conic parameters."""
    if a['kind'] != 'ellipse_arc' or b['kind'] != 'ellipse_arc':
        return False
    if math.dist(a['center'], b['center']) > tolerance:
        return False
    dot = lambda x,y: sum(p*q for p,q in zip(x,y))
    u,v=a['u'],a['v'];uu,vv,uv=dot(u,u),dot(v,v),dot(u,v)
    det=uu*vv-uv*uv
    if det <= 1e-16*uu*vv:
        return False
    coeff=[]
    for vector in (b['u'],b['v']):
        wu,wv=dot(vector,u),dot(vector,v)
        x,y=(wu*vv-wv*uv)/det,(wv*uu-wu*uv)/det
        if math.dist(vector,[x*p+y*q for p,q in zip(u,v)]) > tolerance:
            return False
        coeff.append((x,y))
    # Equal conics differ only by an orthogonal parameter transform.
    if any(abs(dot(c,c)-1)>1e-8 for c in coeff) or abs(dot(*coeff))>1e-8:
        return False
    direction=1 if coeff[0][0]*coeff[1][1]-coeff[0][1]*coeff[1][0]>0 else -1
    offset=math.atan2(coeff[0][1],coeff[0][0])
    def intervals(start,sweep):
        if abs(sweep)>=math.tau-1e-10:return [(0.,math.tau)]
        if sweep<0:start,sweep=start+sweep,-sweep
        start%=math.tau;end=start+sweep
        return [(start,end)] if end<=math.tau else [(start,math.tau),(0.,end-math.tau)]
    first=intervals(a['start_param'],a['sweep_param'])
    second=intervals(offset+direction*b['start_param'],direction*b['sweep_param'])
    angular_tolerance=tolerance/max(math.sqrt(uu),math.sqrt(vv))
    return any(min(y,w)-max(x,z)>angular_tolerance for x,y in first for z,w in second)


def measure(data):
    import ezdxf
    require(data.get('schema_version') == VERSION, '详图路径输入版本不支持')
    require(isinstance(data.get('routes'), list) and data['routes'], '没有路径，不能报告已测量')
    require(isinstance(data.get('views'), list) and data['views'], '没有可定位主视图')
    verify_source_file(data.get('drawing'), 'DXF图纸')
    if data.get('export_receipt'):
        verify_source_file(data['export_receipt'], '转换凭据')
        receipt = json.loads(Path(data['export_receipt']['path']).read_text(encoding='utf-8-sig'))
        require(receipt.get('status') == 'PASS' and receipt.get('source_immutable_verified') is True
                and receipt.get('dxf_completion_verified') is True
                and receipt.get('exchange_geometry_review_required') is False, '转换来源未闭合')
        require(receipt.get('output_sha256') == data['drawing']['sha256'], '转换凭据与DXF不匹配')
        verify_source_file({'path': receipt['source'], 'sha256': receipt['source_sha256']}, '原始DWG')
    doc, read_diagnostics = read_dxf_with_diagnostics(data['drawing']['path'])
    require(not read_diagnostics['source_identity_issues'],
            '源图实体句柄重复，实例路径身份未闭合，须原生核验后重新取证')
    unit = UNITS.get(doc.units)
    require(unit is not None and canonical_scale(data.get('raw_unit')) == canonical_scale(unit), '单位声明与DXF不一致或单位未知')
    factor = canonical_scale(unit)
    tolerance = data.get('join_tolerance_raw', 0.01)
    require(isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool)
            and math.isfinite(tolerance) and 0 < tolerance*factor <= 0.001, '接续容差必须为正且不超过1毫米')
    drawing = Drawing(doc)
    views = {}
    for view in data['views']:
        require(view['view_id'] not in views, '视图编号重复')
        box = view['bounds3d']
        require(len(box) == 6 and all(math.isfinite(n) for n in box)
                and all(box[i] <= box[i+3] for i in range(3)), '视图范围无效')
        views[view['view_id']] = view
    route_ids, used, results = set(), [], []
    for route in data['routes']:
        require(all(isinstance(route.get(k), str) and route[k].strip()
                    for k in ('physical_group_id', 'physical_route_id', 'view_id')), '物理对象和视图编号不能为空')
        identity = (route['physical_group_id'], route['physical_route_id'])
        require(identity not in route_ids, '同一物理管路不得在多个视图重复计量')
        route_ids.add(identity)
        view = views[route['view_id']]
        require(view['role'] == 'primary', '辅助视图只作核对，不能新增同一物理量')
        require(route.get('review_note') and route.get('segments'), '须记录路径模型判读与范围')
        calibration = []
        for path in route.get('dimension_paths', []):
            entity, _ = drawing.resolve(path)
            require(len(path) == 1 and entity.dxftype() == 'DIMENSION'
                    and entity.dxf.dimtype % 16 in (0, 1), '标定须引用当前主视图的线性尺寸')
            require(bounds_contain(view['bounds3d'], [list(entity.dxf.defpoint2), list(entity.dxf.defpoint3)], tolerance), '尺寸不属于当前视图范围')
            override = entity.override().dimstyle_attribs
            scale = override.get('dimlfac', doc.dimstyles.get(entity.dxf.dimstyle).dxf.get('dimlfac', 1))
            require(math.isfinite(scale) and scale > 0, '尺寸线性系数无效')
            require(entity.dxf.get('text', '') in ('', '<>'), '覆盖的尺寸文字不能直接标定图形')
            measurement = dimension_measurement_facts(entity)
            require(measurement['linear_measurement_usable'] and
                    measurement['raw_measurement'] > tolerance,
                    '零长或退化尺寸、尺寸证据冲突不能作为比例标定: ' +
                    ', '.join(measurement['issue_codes']))
            require(math.isclose(scale, measurement['linear_factor'], rel_tol=1e-9),
                    '尺寸线性系数解析冲突')
            calibration.append({'path': path, 'raw_measurement': measurement['raw_measurement'],
                                'linear_factor': scale, 'measurement_evidence': measurement})
        require(calibration, '缺少原图尺寸标定，不能输出详图复原长度')
        scale = calibration[0]['linear_factor']
        require(all(math.isclose(c['linear_factor'], scale, rel_tol=1e-9) for c in calibration), '同一视图尺寸比例冲突')
        records, previous = [], None
        for segment in route['segments']:
            kind = segment['kind']
            require(kind in ('drawn', 'restored_axis', 'port_extension', 'junction_axis'), '路径段类型无效')
            if kind == 'drawn':
                part = drawing.part(segment['source'])
            elif kind in ('port_extension', 'junction_axis'):
                require(segment.get('reason'), '端口或三通复原必须记录原图判读理由')
                start,end = drawing.point(segment['from']),drawing.point(segment['to'])
                part = {'kind':'line','start':start,'end':end}
                axis_ref = segment['to']
                axis = drawing.part(axis_ref)
                require(axis['kind'] == 'line' and axis_ref.get('fraction') in (0,1), '复原须连接相邻中心直线端点')
                u = [b-a for a,b in zip(axis['start'],axis['end'])]
                norm = math.sqrt(sum(x*x for x in u))
                require(norm > tolerance, '中心轴线退化')
                t = sum((p-a)*v for p,a,v in zip(start,axis['start'],u))/(norm*norm)
                require(math.dist(start,[a+t*v for a,v in zip(axis['start'],u)]) <= tolerance, '复原端口或三通交点与源中心线不共线')
                if kind == 'port_extension':
                    cap = drawing.part(segment['from'])
                    require(cap['kind'] == 'line' and segment['from']['fraction'] == .5, '端口须取原图端面直线中点')
                    v = [b-a for a,b in zip(cap['start'],cap['end'])]
                    vnorm = math.sqrt(sum(x*x for x in v))
                    require(vnorm > tolerance and abs(sum(a*b for a,b in zip(u,v)))/(norm*vnorm) <= 1e-6, '管口端面与中心轴不垂直')
                else:
                    refs = segment['from'].get('intersection',[])
                    require(any(r['path'] == axis_ref['path'] and r.get('part',0) == axis_ref.get('part',0) for r in refs), '三通交点须由当前中心线参与求解')
            else:
                require(segment.get('reason') and segment.get('component_paths'), '断口必须有占位构件和复原理由')
                start, end = drawing.point(segment['from']), drawing.point(segment['to'])
                part = {'kind': 'line', 'start': start, 'end': end}
                # Only collinear axis gaps are supported. Bent/missing routes need new evidence.
                for ref in (segment['from'], segment['to']):
                    anchor_part = drawing.part(ref)
                    require(anchor_part['kind'] == 'line' and ref['fraction'] in (0, 1), '断口锚点须为相邻直线轴段端点')
                    direction = [b-a for a, b in zip(start, end)]
                    norm = math.sqrt(sum(x*x for x in direction))
                    require(norm > tolerance, '断口长度为零')
                    for p in (anchor_part['start'], anchor_part['end']):
                        t = sum((v-x)*d for v, x, d in zip(p, start, direction))/(norm*norm)
                        require(math.dist(p, [x+t*d for x, d in zip(start, direction)]) <= tolerance, '断口轴线不共线，禁止自动直连')
                for path in segment['component_paths']:
                    entity, matrix = drawing.resolve(path)
                    require(len(path) == 1 and entity.dxftype() == 'INSERT', '占位证据须是主视图中的构件实例')
                    p = entity_geometry(entity)['anchor']
                    # A sum-of-distances test defines an elongated ellipsoid:
                    # its allowed transverse offset grows with the gap length.
                    # Test the shortest 3D distance to this bounded axis instead.
                    fraction = sum((v-x)*d for v, x, d in zip(p, start, direction))/(norm*norm)
                    nearest = [x+max(0., min(1., fraction))*d for x, d in zip(start, direction)]
                    require(math.dist(p, nearest) <= tolerance,
                            '构件插入点不在断口轴段内；须补充可验证端口证据')
            geometry = {'schema': GEOMETRY_SCHEMA, 'coordinate_system': 'WCS', 'parts': [part]}
            facts = summarize(geometry)
            box = facts['bbox3d']
            require(bounds_contain(view['bounds3d'], [box[:3], box[3:]], tolerance), '路径越出当前视图范围')
            require(facts['length'] > tolerance, '路径存在零长度段')
            for old in used:
                require(not overlapping_lines(part, old, tolerance) and not same_curve(part, old, tolerance)
                        and not overlapping_conics(part, old, tolerance), '路径存在重复或重叠计量')
            used.append(part)
            start, end = facts['start'], facts['end']
            if segment.get('reverse'):
                start, end = end, start
            require(previous is None or math.dist(previous, start) <= tolerance, '路径不连续，不能宣称端到端长度')
            previous = end
            records.append({'kind': kind, 'source_evidence': segment, 'start': start, 'end': end,
                            'coordinate_length_m': facts['length']*factor,
                            'dimension_length_m': facts['length']*factor*scale})
        results.append({'physical_group_id': identity[0], 'physical_route_id': identity[1],
                        'view_id': route['view_id'], 'review_note': route['review_note'], 'calibration': calibration,
                        'drawn_axis_m': math.fsum(r['dimension_length_m'] for r in records if r['kind'] == 'drawn'),
                        'restored_axis_m': math.fsum(r['dimension_length_m'] for r in records if r['kind'] != 'drawn'),
                        'restored_inline_component_axis_m': math.fsum(r['dimension_length_m'] for r in records if r['kind'] == 'restored_axis'),
                        'restored_port_axis_m': math.fsum(r['dimension_length_m'] for r in records if r['kind'] == 'port_extension'),
                        'restored_junction_axis_m': math.fsum(r['dimension_length_m'] for r in records if r['kind'] == 'junction_axis'),
                        'axis_including_components_m': math.fsum(r['dimension_length_m'] for r in records),
                        'billable_pipe_length_m': None, 'installed_group_count': None,
                        'status': 'MODEL_REVIEWED_VIEW_SUBROUTE', 'segments': records})
    return {'schema_version': VERSION, 'status': 'GEOMETRY_MEASURED_SCOPE_LIMITED',
            'drawing': data['drawing'], 'export_receipt': data.get('export_receipt'),
            'raw_unit': unit, 'routes': results, 'review_state': 'working_unreviewed',
            'source_read_diagnostics': read_diagnostics,
            'limitations': ['仅计算已判读主视图中的所选路径，未识别全阀组或现场套数',
                            '轴线复原含阀件占位；未应用项目计量扣减，不能作为净管下料或完整安装量',
                            '未由辅助视图证明的出平面偏移、支管及高差不在本结果范围内']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = Path(args.input).read_bytes()
    result = measure(json.loads(raw.decode('utf-8-sig')))
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'routes': len(result['routes'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
