"""Source-bound diagram lengths; page geometry is location evidence only.

The model declares physical extents and annotation bindings. This bounded
candidate producer does not recognize arbitrary isometrics or produce network
quantities. Its receipt consumer always replays this producer from source.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

SHARED = Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
from qto_cad_geometry import (IDENTITY, compose_matrix, entity_geometry,
                              transform_geometry, transform_point, transform_vector,
                              read_dxf_with_diagnostics)
from qto_cad_source_contract import verify_source_file, canonical_scale, unit_tokens
from qto_elevation_facts import evaluate_elevation_link, weakest, QUANTITY_LEVELS

REQUEST = 'qto-pipe-diagram-length-request/1.0'
RESULT = 'qto-pipe-diagram-length-candidates/1.0'
BASIS = 'pipe_centerline_route_length'
LEVELS = {'confirmed', 'inferred', 'scenario'}
DEPENDENCIES = ('qto_cad_geometry.py', 'qto_cad_source_contract.py',
                'qto_elevation_facts.py', 'recompute_qto_dependencies.py',
                'qto_human_review_evidence.py', 'qto_actor_identity.py')
NUMBER = r'[+\-±]?(?:\d+(?:\.\d*)?|\.\d+)'
VALUE = re.compile(r'^\s*(?:(?:L|H|标高|管长|长度)\s*[=:：]?\s*)?(' + NUMBER +
                   r')\s*(mm|cm|m|ft|in|毫米|厘米|米|英尺|英寸)?\s*$', re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), label + '须为非空文本')
    return value.strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def finite(value):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value), '坐标及区段参数须为有限数值')
    return float(value)


def runtime_dependencies():
    return [{'module': 'qto-pipe-networks/scripts/' + Path(__file__).name,
             'path': str(Path(__file__).resolve()), 'sha256': sha(__file__)}] + [
        {'module': '_qto-shared-evidence/scripts/' + name,
         'path': str(SHARED / name), 'sha256': sha(SHARED / name)} for name in DEPENDENCIES]


def verify_runtime_dependencies(rows):
    require(isinstance(rows, list), '缺少系统图计长运行字节依赖')
    expected = {r['module']: r['sha256'] for r in runtime_dependencies()}
    require(all(isinstance(r, dict) and isinstance(r.get('module'), str) for r in rows),
            '系统图运行依赖格式不完整')
    require(len(rows) == len(expected) and {r['module']: r.get('sha256') for r in rows} == expected,
            '系统图计长代码或固定生产依赖闭包字节变化，必须重建并重新消费')
    for row in rows:
        verify_source_file(row, '原系统图生产代码')


class Drawing:
    def __init__(self, doc):
        self.doc = doc

    def resolve(self, path):
        require(isinstance(path, list) and path and all(isinstance(v, str) for v in path),
                '必须提供从Model到叶实体的完整INSERT实例路径')
        owner = self.doc.modelspace().block_record_handle
        matrix = IDENTITY
        for index, handle in enumerate(path):
            entity = self.doc.entitydb.get(handle.upper())
            require(entity is not None and entity.dxf.owner == owner, '源实例路径与父块/Model不符')
            require(not entity.dxf.get('invisible', 0), '隐藏源实体不能作当前图面证据')
            layer = self.doc.layers.get(entity.dxf.get('layer', '0'))
            require(layer is not None and not layer.is_off() and not layer.is_frozen(),
                    '关闭或冻结图层不属于当前图面证据')
            if index < len(path)-1:
                require(entity.dxftype() == 'INSERT', '路径中间节点必须是INSERT')
                require(entity.dxf.row_count == entity.dxf.column_count == 1,
                        '阵列INSERT须先形成逐实例身份，不能套乘数')
                matrix = compose_matrix(matrix, entity_geometry(entity)['insert_matrix_wcs'])
                owner = self.doc.blocks[entity.dxf.name].block_record_handle
        return entity, matrix

    def annotation(self, path, bounds):
        entity, matrix = self.resolve(path)
        require(entity.dxftype() in {'TEXT', 'MTEXT', 'DIMENSION'},
                '当前系统图入口只读取TEXT/MTEXT及明确原生DIMENSION；属性须另走有源取证入口')
        raw = entity.plain_text() if entity.dxftype() == 'MTEXT' else entity.dxf.get('text', '')
        p = entity.dxf.defpoint if entity.dxftype() == 'DIMENSION' else entity.dxf.insert
        location = [sum(matrix[row*4 + col] * p[col] for col in range(3)) + matrix[row*4+3]
                    for row in range(3)]
        # qto-cad matrices are row-major affine matrices (same contract as transform_geometry).
        self.inside([location], bounds)
        return {'path': copy.deepcopy(path), 'text': raw, 'location': location,
                'entity_type': entity.dxftype(), 'layer': entity.dxf.layer}

    @staticmethod
    def inside(points, bounds):
        require(all(bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3]
                    for p in points), '证据或源段越出本次diagram/view范围')

    def span(self, spec, bounds):
        require(isinstance(spec, dict), 'source_span须为源区段对象')
        entity, matrix = self.resolve(spec.get('path'))
        require(entity.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE'},
                '当前物理源段只接原生直线/多段线；代理和专业原生构件须另走取证，不加载未绑定的动态适配器')
        geometry = entity_geometry(entity).get('geometry')
        require(geometry is not None, '源实例没有可定位曲线')
        parts = transform_geometry(geometry, matrix)['parts']
        index = spec.get('part', 0)
        require(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(parts),
                '源part索引无效')
        require('part' in spec or len(parts) == 1, '多part源须明确区段')
        part = parts[index]
        require(part['kind'] == 'line', '当前计长入口只绑定直线物理分段；弯曲路径须先分段取证')
        a, b = finite(spec.get('from_fraction', 0)), finite(spec.get('to_fraction', 1))
        require(0 <= a < b <= 1, '源区段须为0到1内的正向非零区间')
        points = [[x + t*(y-x) for x, y in zip(part['start'], part['end'])] for t in (a, b)]
        self.inside(points, bounds)
        require(math.dist(*points) > 0, '零长源段不能绑定实物范围')
        return {'path': [h.upper() for h in spec['path']], 'part': index,
                'from_fraction': a, 'to_fraction': b, 'page_endpoints': points,
                'layer': entity.dxf.layer, 'page_coordinate_length': math.dist(*points),
                'page_coordinate_unit': 'raw_location_only', 'physical_length_from_coordinates': False}


def parallel(a, b):
    """Collinearity in one coordinate frame, independent of length or scale."""
    na, nb = math.hypot(*a), math.hypot(*b)
    if not na or not nb:
        return False
    u, v = [x/na for x in a], [x/nb for x in b]
    cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
    return math.hypot(*cross) <= 1e-9


def dimension_axis(entity, matrix):
    """DIM defpoints are WCS in the owning block; angle is in the DIM OCS.

    Verify the original relation, then apply the complete parent INSERT affine
    chain to both vectors. A page rotation can never turn a source projection
    into a full physical dimension. Explicit text values are never scaled.
    """
    local_ends = [list(entity.dxf.get(key)) for key in ('defpoint2', 'defpoint3')]
    local_delta = [b-a for a, b in zip(*local_ends)]
    for p in local_ends:
        for v in p:
            finite(v)
    ocs = entity.ocs()
    normal = list(ocs.uz)
    length = math.hypot(*local_delta)
    if not length or abs(sum(a*b for a, b in zip(normal, local_delta))) > length*1e-9:
        return None, '源DIMENSION端点不在尺寸OCS平面，不能作为已支持的沿轴尺寸'
    if entity.dxf.dimtype & 7 == 0:
        theta = math.radians(finite(entity.dxf.get('angle', 0)))
        local_axis = list(ocs.to_wcs((math.cos(theta), math.sin(theta), 0)))
    else:
        local_axis = local_delta
    # Reject collapsed or numerically degenerate affine parent chains explicitly.
    columns = [transform_vector(v, matrix) for v in ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))]
    lengths = [math.hypot(*v) for v in columns]
    if not all(lengths):
        return None, '父INSERT变换退化，尺寸方向不能核准'
    u, v, w = [[x/n for x in col] for col, n in zip(columns, lengths)]
    determinant = sum(a*b for a, b in zip(u, (v[1]*w[2]-v[2]*w[1], v[2]*w[0]-v[0]*w[2], v[0]*w[1]-v[1]*w[0])))
    if abs(determinant) <= 1e-12:
        return None, '父INSERT变换退化或病态，尺寸方向不能核准'
    world_axis, world_delta = transform_vector(local_axis, matrix), transform_vector(local_delta, matrix)
    verified = {'coordinate_basis': 'dimension_OCS_to_owner_WCS_then_complete_INSERT_chain',
        'dimension_ocs_normal': normal, 'owner_wcs_endpoints': local_ends,
        'owner_wcs_dimension_axis': local_axis, 'page_wcs_dimension_axis': world_axis,
        'page_wcs_extent_direction': world_delta, 'parent_matrix': list(matrix)}
    if not parallel(local_axis, local_delta) or not parallel(world_axis, world_delta):
        return verified, '源DIMENSION为投影尺寸，尺寸轴与管轴未在相同坐标基准沿轴对应'
    return verified, None


def annotation_value(drawing, spec, bounds):
    require(isinstance(spec, dict), '尺寸/标高须有源annotation绑定')
    source = drawing.annotation(spec.get('annotation_path'), bounds)
    match = VALUE.fullmatch(source['text'])
    if match is None:
        return None, source, '原文字不是本入口可唯一解析的单一长度或标高数值'
    value = float(match[1].replace('±', ''))
    unit = match[2]
    if unit is None:
        if not spec.get('unit_annotation_path'):
            return None, source, '原标注无物理单位且没有本视图适用单位条款'
        note = drawing.annotation(spec['unit_annotation_path'], bounds)
        tokens = unit_tokens(note['text'])
        # A bare unit word alone does not declare applicability to numeric annotations.
        require(('单位' in note['text'] or 'unit' in note['text'].lower()) and len(tokens) == 1,
                '单位条款须在原文明确单位且仅含一种长度单位')
        require(any(word in note['text'].lower() for word in
                    ('标高', '标注', '尺寸', '管长', '长度', 'annotation', 'elevation', 'length')),
                '原单位条款未说明适用于标注/标高/管长，坐标单位不能冒用为标注单位')
        require(spec.get('unit_applies_to') in {'annotation_values', 'pipe_length_elevation'},
                '单位条款必须声明适用于标注数值；不能冒用坐标单位')
        scale = next(iter(tokens))
        unit = {0.001: 'mm', .01: 'cm', 1.: 'm', .3048: 'ft', .0254: 'in'}.get(scale)
        require(unit is not None, '本入口不支持该标注单位')
        source['unit_annotation'] = note
        source['unit_applies_to'] = spec['unit_applies_to']
    source.update(value=value, unit=unit, value_m=value*canonical_scale(unit))
    return source['value_m'], source, None


def measurement(drawing, fact, row, view, source):
    require(isinstance(fact, dict), 'length_facts须逐项声明事实')
    level = fact.get('level')
    require(level in LEVELS, '事实必须声明confirmed/inferred/scenario')
    text(fact.get('basis'), '事实解释basis')
    method = fact.get('method')
    if method == 'unresolved':
        return {'method': method, 'value': None, 'level': level,
                'issue': text(fact.get('issue'), '缺证原因'), 'input_fact': copy.deepcopy(fact)}
    require(method in {'dimension', 'elevation'}, '只支持明确尺寸、同基准标高差或unresolved')
    require(fact.get('measurement_basis', BASIS) == BASIS, '当前长度只接受管中心线路径口径')
    if method == 'dimension':
        value, annotation, issue = annotation_value(drawing, fact, view['bounds'])
        if value is not None:
            require(value >= 0, '管长尺寸不能为负')
        binding = 'source_text_only_model_interpretation'
        if annotation['entity_type'] == 'DIMENSION':
            entity, matrix = drawing.resolve(fact['annotation_path'])
            dimension_type = entity.dxf.dimtype & 7
            if dimension_type not in {0, 1}:
                issue = '只支持沿该段轴向的线性DIMENSION；投影尺寸或自动raw测量值不作路径实长'
                value = None
            else:
                ends = [transform_point(list(entity.dxf.get(key)), matrix) for key in ('defpoint2', 'defpoint3')]
                span = drawing.span(row['source_span'], view['bounds'])
                target = span['page_endpoints']
                tolerance = max(math.dist(*target) * 1e-9,
                    16*max(math.ulp(v) for p in ends+target for v in p))
                matched = any(all(math.dist(a, b) <= tolerance for a, b in zip(ends, ordered))
                              for ordered in (target, target[::-1]))
                axis_check, axis_issue = dimension_axis(entity, matrix)
                annotation['extension_endpoints'] = ends
                annotation['axis_verification'] = axis_check
                if not matched:
                    issue = '源DIMENSION延伸端点不匹配所选完整物理源区段，不能误绑合法尺寸'
                    value = None
                elif axis_issue:
                    issue = axis_issue
                    value = None
                else:
                    binding = 'source_dimension_endpoints_verified'
        return {'method': method, 'value': value, 'level': level, 'source_annotation': annotation,
                'binding_verification': binding, 'issue': issue, 'basis': fact['basis']}
    ends = []
    issues = []
    for name, port in (('from', row['from_port_id']), ('to', row['to_port_id'])):
        end = fact.get(name)
        require(isinstance(end, dict), '标高差须给出两端源事实')
        value, annotation, issue = annotation_value(drawing, end, view['bounds'])
        kind = end.get('measurement_kind')
        datum = text(end.get('datum_id'), '标高基准身份')
        if kind not in {'pipe_center', 'equipment_port_center'}:
            issues.append('端点测点不是管中心/实际接口中心；转入既有typed标高换算入口')
        if issue:
            issues.append(issue)
        datum_source = None
        if end.get('datum_annotation_path'):
            datum_source = drawing.annotation(end['datum_annotation_path'], view['bounds'])
            if not any(token in datum_source['text'].lower() for token in ('基准', 'datum')):
                issues.append('所引文字不是明确的高程基准声明；不能只输入相同datum_id制造共同基准')
        else:
            issues.append('缺少本视图可定位的共基准声明，不能从同名楼层或数值推定')
        ends.append({'value': value, 'source_annotation': annotation, 'measurement_kind': kind,
                     'datum_id': datum, 'datum_source_annotation': datum_source, 'reference_id': port})
    if ends[0]['datum_id'] != ends[1]['datum_id']:
        issues.append('两端高程基准不同；须在既有typed标高入口提供有源换算')
    if all(e['datum_source_annotation'] for e in ends) and ends[0]['datum_source_annotation']['path'] != ends[1]['datum_source_annotation']['path']:
        issues.append('两端未引用同一原源基准声明；相同datum_id或同名零点不能代替基准对应')
    if fact.get('route_kind') != 'vertical':
        issues.append('未证明为纯竖直实物区段；标高差仅为投影，不能代替路径长度')
    result = {'method': method, 'value': None, 'level': weakest([level, 'inferred']),
              'endpoints': ends, 'binding_verification': 'model_working_port_and_datum_interpretation',
              'basis': fact['basis'], 'issue': '; '.join(issues) or None}
    if issues:
        return result
    # Build the existing typed-elevation producer's fully bound source facts.
    sid = 'diagram-source'
    fields = ('value', 'measurement_kind', 'datum_id', 'reference_id')
    spec = {'schema': 'qto-elevation-link/1.0', 'system': row['system'],
            'calculation_id': 'diagram:' + row['record_id'], 'sources': [{**source,
            'id': sid, 'locator': 'diagram/view ' + view['view_id'], 'facts': []}]}
    for index, name in enumerate(('from', 'to')):
        end = {k: ends[index][k] for k in fields}
        end.update(fact_id=name, unit='m', source_ids=[sid], basis=fact['basis'], level=level)
        spec[name] = end
        spec['sources'][0]['facts'].append({**{k: end[k] for k in fields}, 'id': name,
            'unit': 'm', 'system': row['system'], 'level': level})
    spec['route'] = {'kind': 'vertical', 'route_id': 'route', 'source_ids': [sid],
                     'basis': fact['basis'], 'level': level}
    spec['sources'][0]['facts'].append({'id': 'route', 'measurement_kind': 'route_shape',
        'route_kind': 'vertical', 'from_reference_id': row['from_port_id'],
        'to_reference_id': row['to_port_id'], 'system': row['system'], 'level': level})
    # This producer builds its own unreviewed graph: caller-supplied approvals,
    # prior review receipts and qto_review_contract's feedback branch cannot enter.
    calculation = evaluate_elevation_link(spec, 'm', expected_system=row['system'], previous_calculation=None)
    result.update(value=calculation['value'], elevation_calculation=calculation)
    return result


def measure(data):
    require(data.get('schema_version') == REQUEST, '系统图计长输入契约不符')
    text(data.get('scope_id'), 'scope_id')
    text(data.get('scope_basis'), 'scope_basis')
    require(data.get('measurement_basis', BASIS) == BASIS, '系统图计长口径不符')
    require(isinstance(data.get('segments'), list) and data['segments'], '须列出逐物理区段')
    verify_source_file(data.get('drawing'), '系统图DXF')
    source = copy.deepcopy(data['drawing'])
    require(Path(source['path']).is_absolute(), '图纸来源须为绝对路径')
    protected = [source]
    if data.get('export_receipt') is not None:
        receipt_binding = data['export_receipt']
        verify_source_file(receipt_binding, 'DWG转换凭据')
        receipt = json.loads(Path(receipt_binding['path']).read_text(encoding='utf-8-sig'))
        require(receipt.get('status') == 'PASS' and receipt.get('source_immutable_verified') is True
                and receipt.get('dxf_completion_verified') is True
                and receipt.get('exchange_geometry_review_required') is False
                and receipt.get('output_sha256') == source['sha256'], 'DWG/DXF转换身份未闭合')
        original = {'path': receipt['source'], 'sha256': receipt['source_sha256']}
        verify_source_file(original, '原DWG')
        protected.extend([copy.deepcopy(receipt_binding), original])
    doc, diagnostics = read_dxf_with_diagnostics(source['path'])
    require(not diagnostics['source_identity_issues'], '原DXF有身份冲突，须先核清')
    drawing = Drawing(doc)
    require(isinstance(data.get('views'), list) and data['views'], '须声明diagram/view')
    views = {}
    for view in data['views']:
        vid = text(view.get('view_id'), 'view_id')
        require(vid not in views and view.get('kind') in {'system', 'principle', 'isometric'},
                '视图身份重复或类型不支持')
        bounds = view.get('bounds')
        require(isinstance(bounds, list) and len(bounds) == 4, '视图须有本轮定位范围')
        bounds = [finite(v) for v in bounds]
        require(bounds[0] < bounds[2] and bounds[1] < bounds[3], '视图范围退化')
        require(view.get('level') in LEVELS, '视图声明须有采用层级')
        text(view.get('basis'), '视图解释')
        require(isinstance(view.get('title_paths'), list) and view['title_paths'], '视图须引用源图名/系统编号')
        views[vid] = {**copy.deepcopy(view), 'bounds': bounds,
            'source_titles': [drawing.annotation(p, bounds) for p in view['title_paths']]}
    ids, extents, port_pairs, spans, results = set(), set(), set(), [], []
    for row in data['segments']:
        require(isinstance(row, dict), 'segments条目须为对象')
        require(not ({'quantity', 'length', 'multiplier', 'count', 'raw_unit', 'drawing', 'drawing_id'} & set(row)),
                '逐段入口不接手填量、乘数、raw单位或跨源替换；多实例须逐个绑定')
        rid = text(row.get('record_id'), 'record_id')
        physical, extent = text(row.get('physical_id'), 'physical_id'), text(row.get('extent_id'), 'extent_id')
        system = text(row.get('system'), 'system')
        p0, p1 = text(row.get('from_port_id'), 'from_port_id'), text(row.get('to_port_id'), 'to_port_id')
        require(p0 != p1, '物理段两端口不能相同')
        require(rid not in ids and (system, physical, extent) not in extents
                and (system, frozenset((p0, p1))) not in port_pairs,
                '同一实物区段或实际端口对重复；应保留一个数量出口')
        ids.add(rid); extents.add((system, physical, extent)); port_pairs.add((system, frozenset((p0, p1))))
        view = views.get(row.get('view_id'))
        require(view is not None, '区段引用未登记视图')
        require(row.get('level') in LEVELS, '物理段绑定须有采用层级')
        text(row.get('basis'), '物理段范围及标注绑定解释')
        recognition = row.get('recognition_basis')
        require(isinstance(recognition, dict) and recognition.get('kind') == 'model_working_interpretation'
                and recognition.get('review_status') == 'unreviewed',
                '须披露模型识别身份及未复核状态，不能把输入解释冒充源图自动认定或人工确认')
        text(recognition.get('recognized_by'), '实际识别者')
        require(isinstance(row.get('specification'), dict), '须保留完整规格对象，未知规格字段保留null')
        span = drawing.span(row.get('source_span'), view['bounds'])
        for old in spans:
            require(not (span['path'] == old['path'] and span['part'] == old['part']
                and min(span['to_fraction'], old['to_fraction']) > max(span['from_fraction'], old['from_fraction']) + 1e-12),
                '同一源实例part区间重复绑定；不能换实物名重复计量')
        spans.append(span)
        facts = row.get('length_facts', [])
        require(isinstance(facts, list), 'length_facts须为数组')
        calculated = [measurement(drawing, f, row, view, source) for f in facts]
        known = [f for f in calculated if f['value'] is not None]
        conflict = bool(known) and any(not math.isclose(f['value'], known[0]['value'], rel_tol=1e-9, abs_tol=1e-9) for f in known)
        value = None if conflict or not known else known[0]['value']
        level = None if value is None else weakest([row['level'], view['level']] + [f['level'] for f in known])
        issues = [f['issue'] for f in calculated if f.get('issue')]
        if conflict:
            issues.append('同一物理范围的明确尺寸/标高结果冲突，不以任一值覆盖')
        if not calculated:
            issues.append('缺少明确尺寸/同基准标高端点；示意、压缩和轴测图面长度不计')
        instance_status = row.get('physical_instance_status', 'unresolved')
        require(instance_status in {'actual', 'typical_uninstantiated', 'unresolved'}, '物理实例状态不支持')
        quantity = value
        if known and all(f.get('binding_verification') == 'source_text_only_model_interpretation' for f in known):
            issues.append('有单位TEXT仅证明源文字数值，标注到实际区段的语义绑定未闭合；不生成该段数量')
            quantity = None
        if instance_status != 'actual':
            issues.append('尚未对应唯一实际物理实例；保留源区段尺寸，不作实际管段数量或典型套数乘计')
            quantity = None
        results.append({**copy.deepcopy(row), 'source_span': span, 'measurements': calculated,
            'physical_instance_status': instance_status, 'measured_extent_length': value,
            'measured_extent_level': QUANTITY_LEVELS[level] if level else '未决量',
            'quantity': quantity, 'unit': 'm', 'measurement_basis': BASIS,
            'quantity_level': QUANTITY_LEVELS[level] if level and quantity is not None else '未决量',
            'level': level if quantity is not None else None,
            'status': 'conflict' if conflict else 'calculated_candidate' if quantity is not None else 'unresolved',
            'issues': issues, 'quantity_owner': 'diagram_candidate:' + rid,
            'business_adoption_allowed': False})
    for binding in protected:
        verify_source_file(binding, '计算后来源复核')
    groups = {}
    for row in results:
        key = (row['system'], digest(row['specification']), row['quantity_level'])
        group = groups.setdefault(key, {'system': row['system'], 'specification': row['specification'],
            'quantity_level': row['quantity_level'], 'known_subtotal': 0., 'unit': 'm',
            'record_ids': [], 'unresolved_record_ids': []})
        group['record_ids'].append(row['record_id'])
        if row['quantity'] is None:
            group['unresolved_record_ids'].append(row['record_id'])
        else:
            group['known_subtotal'] += row['quantity']
    for group in groups.values():
        group['quantity'] = None if group['unresolved_record_ids'] else group['known_subtotal']
    return {'schema_version': RESULT, 'scope_id': data['scope_id'], 'scope_basis': data['scope_basis'],
        'input_fingerprint': digest(data), 'source_bindings': protected, 'source_read_diagnostics': diagnostics,
        'runtime_dependencies': runtime_dependencies(), 'views': list(views.values()), 'segments': results,
        'length_summary': list(groups.values()), 'unprocessed_record_ids': [r['record_id'] for r in results if r['quantity'] is None],
        'scope_complete': False, 'network_quantity_produced': False, 'business_adoption_allowed': False,
        'recognition_status': 'model_interpretation_not_automatically_verified'}


def write_new(path, result, protected=()):
    path = Path(path).resolve()
    require(not path.exists() and path not in {Path(p).resolve() for p in protected}, '拒绝覆盖来源或旧成果')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description='系统/原理图明确尺寸和同基准有据标高分段候选计长')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        result = measure(data)
        result['source_request'] = {'path': str(args.input.resolve()), 'sha256': sha(args.input)}
        write_new(args.output, result, [args.input] + [b['path'] for b in result['source_bindings']]
                  + [r['path'] for r in result['runtime_dependencies']])
        print(json.dumps({'status': 'CANDIDATE', 'segments': len(result['segments']),
                          'unprocessed': len(result['unprocessed_record_ids'])}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
