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
from pipe_diagram_source_parts import resolve_instance, source_interval, coverage_overlap, line_fraction
from qto_pipe_topology import project_part

REQUEST = 'qto-pipe-diagram-length-request/1.0'
RESULT = 'qto-pipe-diagram-length-candidates/1.0'
BASIS = 'pipe_centerline_route_length'
LEVELS = {'confirmed', 'inferred', 'scenario'}
DEPENDENCIES = ('qto_cad_geometry.py', 'qto_cad_source_contract.py',
                'qto_elevation_facts.py', 'recompute_qto_dependencies.py',
                'qto_human_review_evidence.py', 'qto_actor_identity.py',
                'qto_pipe_topology.py', 'qto_spatial.py')
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


def replay_digest(value):
    """Code locations are diagnostic; relative module identity and bytes bind.

    Only executable dependency locations are removed. Original drawing, source
    requests and other evidence paths remain part of the replayed semantics.
    """
    def canonical(row):
        if isinstance(row, dict):
            return {key: ([{k: canonical(v) for k, v in item.items() if k != 'path'} for item in child]
                    if key == 'runtime_dependencies' and isinstance(child, list) else canonical(child))
                    for key, child in row.items()}
        if isinstance(row, list):
            return [canonical(child) for child in row]
        return row
    return digest(canonical(value))


def finite(value):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value), '坐标及区段参数须为有限数值')
    return float(value)


def runtime_dependencies():
    return [{'module': 'qto-pipe-networks/scripts/' + Path(__file__).name,
             'path': str(Path(__file__).resolve()), 'sha256': sha(__file__)}] + [
        {'module': 'qto-pipe-networks/scripts/pipe_diagram_source_parts.py',
         'path': str(Path(__file__).with_name('pipe_diagram_source_parts.py')),
         'sha256': sha(Path(__file__).with_name('pipe_diagram_source_parts.py'))}] + [
        {'module': '_qto-shared-evidence/scripts/' + name,
         'path': str(SHARED / name), 'sha256': sha(SHARED / name)} for name in DEPENDENCIES]


def verify_runtime_dependencies(rows):
    require(isinstance(rows, list), '缺少系统图计长运行字节依赖')
    expected = {r['module']: r['sha256'] for r in runtime_dependencies()}
    require(all(isinstance(r, dict) and isinstance(r.get('module'), str) for r in rows),
            '系统图运行依赖格式不完整')
    require(len(rows) == len(expected) and {r['module']: r.get('sha256') for r in rows} == expected,
            '系统图计长代码或固定生产依赖闭包字节变化，必须重建并重新消费')
    # Verify current code by its fixed relative module identity and SHA above;
    # a historical absolute installation path is not an executable dependency.


class Drawing:
    def __init__(self, doc):
        self.doc = doc

    def resolve(self, path):
        return resolve_instance(self.doc, path)

    def annotation(self, path, bounds):
        entity, matrix = self.resolve(path)
        require(entity.dxftype() in {'TEXT', 'MTEXT', 'DIMENSION'},
                '当前系统图入口只读取TEXT/MTEXT及明确原生DIMENSION；属性须另走有源取证入口')
        raw = entity.plain_text() if entity.dxftype() == 'MTEXT' else entity.dxf.get('text', '')
        if entity.dxftype() == 'DIMENSION':
            p = entity.dxf.defpoint
        elif entity.dxftype() == 'TEXT':
            p = entity.ocs().to_wcs(entity.dxf.insert)
        else:
            p = entity.dxf.insert  # MTEXT insertion is already owner WCS.
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
        return source_interval(self, spec, bounds)


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


def dimension_chain(drawing, fact, row, view):
    """A native dimension chain must partition one straight physical extent."""
    paths = fact.get('dimension_paths')
    require(isinstance(paths, list) and paths and len({tuple(p) for p in paths}) == len(paths),
            '尺寸链须逐项绑定不重复原生DIMENSION实例')
    span = drawing.span(row['source_span'], view['bounds'])
    if 'source_parts' in span:
        return route_dimension_chain(drawing, fact, span, view)
    require('page_curve' not in span, '线性尺寸链不能用曲线端点弦长代表曲线路径')
    start, end = span['page_endpoints']
    vector = [b-a for a, b in zip(start, end)]
    square = sum(v*v for v in vector)
    tolerance = max(math.sqrt(square)*1e-9, 16*max(math.ulp(v) for p in (start, end) for v in p))
    chain, issues = [], []
    for path in paths:
        value, annotation, issue = annotation_value(drawing, {**fact, 'annotation_path': path}, view['bounds'])
        entity, matrix = drawing.resolve(path)
        if entity.dxftype() != 'DIMENSION' or entity.dxf.dimtype & 7 not in {0, 1}:
            issues.append('尺寸链只接受原生线性或对齐DIMENSION，不从TEXT拼接')
            continue
        ends = [transform_point(list(entity.dxf.get(k)), matrix) for k in ('defpoint2', 'defpoint3')]
        drawing.inside(ends, view['bounds'])
        fractions = [sum((p[i]-start[i])*vector[i] for i in range(3))/square for p in ends]
        errors = [math.dist(p, [start[i]+t*vector[i] for i in range(3)]) for p, t in zip(ends, fractions)]
        axis, axis_issue = dimension_axis(entity, matrix)
        if any(e > tolerance for e in errors) or not all(-1e-9 <= t <= 1+1e-9 for t in fractions):
            issues.append('尺寸链延伸端点不属于所选同一直线实物范围')
        if abs(fractions[0]-fractions[1]) <= 1e-12:
            issues.append('尺寸链含零区间')
        if issue or axis_issue or value is None or (value is not None and value <= 0):
            issues.append(issue or axis_issue or '尺寸链须有正的明确物理尺寸')
        chain.append({'from_fraction': min(fractions), 'to_fraction': max(fractions),
            'value_m': value, 'source_annotation': annotation, 'extension_endpoints': ends,
            'axis_verification': axis})
    chain.sort(key=lambda s: s['from_fraction'])
    cursor = 0.
    for piece in chain:
        if not math.isclose(piece['from_fraction'], cursor, rel_tol=0, abs_tol=1e-9):
            issues.append('尺寸链有缺口或重叠，不能把分段值直接当完整长度')
        cursor = piece['to_fraction']
    if not math.isclose(cursor, 1., rel_tol=0, abs_tol=1e-9):
        issues.append('尺寸链未闭合到实际区段终点')
    return {'method': 'dimension_chain', 'value': None if issues else sum(p['value_m'] for p in chain),
        'level': fact['level'], 'source_dimension_chain_segments': chain,
        'binding_verification': 'source_dimension_chain_endpoints_verified',
        'basis': fact['basis'], 'issue': '; '.join(issues) or None}


def route_dimension_chain(drawing, fact, span, view):
    """Bind native dimension values to every directed native source leg.

    Page lengths only locate extension endpoints. Every selected leg must be
    partitioned exactly once by source DIMENSION intervals; no interpolation.
    """
    chain, issues = [], []
    for path in fact['dimension_paths']:
        value, annotation, issue = annotation_value(drawing, {**fact, 'annotation_path': path}, view['bounds'])
        entity, matrix = drawing.resolve(path)
        if entity.dxftype() != 'DIMENSION' or entity.dxf.dimtype & 7 not in {0, 1}:
            issues.append('原生路由尺寸链只接线性或对齐DIMENSION，不从TEXT拼接')
            continue
        ends = [transform_point(list(entity.dxf.get(k)), matrix) for k in ('defpoint2', 'defpoint3')]
        drawing.inside(ends, view['bounds'])
        axis, axis_issue = dimension_axis(entity, matrix)
        matches = []
        for index, leg in enumerate(span['source_parts']):
            tolerance = max(leg['page_coordinate_length']*1e-9,
                32*max(math.ulp(v) for point in leg['page_endpoints']+ends for v in point), 1e-10)
            projected = [line_fraction(p, *leg['page_endpoints']) for p in ends]
            if all(t is not None and -1e-9 <= t <= 1+1e-9 and error <= tolerance for t, error in projected):
                ts = sorted(t for t, _ in projected)
                if ts[1]-ts[0] > 1e-12:
                    matches.append((index, max(0., ts[0]), min(1., ts[1])))
        if len(matches) != 1:
            issues.append('原生尺寸端点不能唯一绑定所选有向路由的一腿，不跨腿补弦长或手选消歧')
            continue
        index, first, last = matches[0]
        leg = span['source_parts'][index]
        if issue or axis_issue or value is None or value <= 0:
            issues.append(issue or axis_issue or '路由每腿须有正的明确原生物理尺寸')
        chain.append({'route_leg': index, 'source_path': leg['path'], 'source_part': leg['part'],
            'source_from_fraction': leg['from_fraction'], 'source_to_fraction': leg['to_fraction'],
            'source_direction': leg['direction'], 'from_fraction': first, 'to_fraction': last,
            'value_m': value, 'source_annotation': annotation, 'extension_endpoints': ends,
            'axis_verification': axis})
    chain.sort(key=lambda p: (p['route_leg'], p['from_fraction'], p['to_fraction'], p['source_annotation']['path']))
    for index in range(len(span['source_parts'])):
        cursor = 0.
        for piece in (p for p in chain if p['route_leg'] == index):
            if not math.isclose(piece['from_fraction'], cursor, rel_tol=0, abs_tol=1e-9):
                issues.append('原生路由尺寸链有缺口或重叠，不能跨腿补齐')
            cursor = piece['to_fraction']
        if not math.isclose(cursor, 1., rel_tol=0, abs_tol=1e-9):
            issues.append('原生路由尺寸链未覆盖每腿完整选定参数区间')
    return {'method': 'dimension_chain', 'value': None if issues else sum(p['value_m'] for p in chain),
        'level': fact['level'], 'source_dimension_chain_segments': chain,
        'binding_verification': 'ordered_native_route_dimension_endpoints_and_axes_verified',
        'basis': fact['basis'], 'issue': '; '.join(sorted(set(issues))) or None}


def native_leader_binding(drawing, fact, row, annotation, bounds):
    """Only native attached L= text with an unambiguous interior arrow target."""
    if not fact.get('leader_path'):
        return False, None
    if not re.match(r'^\s*(?:L|管长|长度)\s*[=:：]', annotation['text'], re.I):
        return False, '原生引线文字未明确为L=/管长=/长度=，不能推定长度语义'
    entity, matrix = drawing.resolve(fact['leader_path'])
    if entity.dxftype() != 'LEADER' or not entity.dxf.get('has_arrowhead', 1):
        return False, '当前TEXT语义只接受有箭头的原生LEADER'
    if (fact['leader_path'][:-1] != fact['annotation_path'][:-1]
            or entity.dxf.get('annotation_handle', '').upper() != fact['annotation_path'][-1].upper()):
        return False, '原生LEADER未通过annotation_handle绑定同一实际文字实例'
    span = drawing.span(row['source_span'], bounds)
    if (span['from_fraction'], span['to_fraction']) != (0., 1.):
        return False, '引线长度须对应完整源part，部分区间缺少原生端点界定'
    vertices = [transform_point(list(p), matrix) for p in entity.vertices]
    if len(vertices) < 2:
        return False, '原生LEADER缺箭头路线'
    drawing.inside(vertices, bounds)
    point = vertices[0]
    a, b = span['page_endpoints']
    tolerance = max(span['page_coordinate_length']*1e-9,
        16*max(math.ulp(v) for p in (a, b, point) for v in p))
    if 'page_curve' in span:
        error, fraction = project_part(point, span['page_curve'])
    else:
        vector = [y-x for x, y in zip(a, b)]
        square = sum(v*v for v in vector)
        fraction = sum((point[i]-a[i])*vector[i] for i in range(3))/square
        error = math.dist(point, [a[i]+fraction*vector[i] for i in range(3)])
    if not (1e-9 < fraction < 1-1e-9) or error > tolerance:
        return False, '原生LEADER箭头未唯一落在所选完整源part内部'
    hits, unresolved = [], []

    def parts(entities, prefix, active):
        # All actual visible INSERT instances participate. Restricting the
        # search to the target's owner would miss a crossing sibling block.
        for other in entities:
            other_path = prefix + [other.dxf.handle]
            try:
                _, matrix = drawing.resolve(other_path)
            except ValueError:
                continue
            if other.dxftype() == 'INSERT':
                name = other.dxf.name
                if (other.dxf.row_count != 1 or other.dxf.column_count != 1
                        or name in active or len(prefix) >= 64):
                    unresolved.append('有阵列或循环块尚不能逐实例排除箭头歧义')
                    continue
                yield from parts(drawing.doc.blocks[name], other_path, active | {name})
            elif other.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE'}:
                geometry = entity_geometry(other).get('geometry')
                if geometry:
                    yield other_path, transform_geometry(geometry, matrix)['parts']
                else:
                    unresolved.append('可见原生曲线尚不能排除箭头歧义')

    for other_path, source_parts in parts(drawing.doc.modelspace(), [], set()):
        for index, part in enumerate(source_parts):
            if part['kind'] == 'ellipse_arc':
                u, v = part['u'], part['v']
                delta = [p-c for p, c in zip(point, part['center'])]
                uu, uv, vv = sum(x*x for x in u), sum(x*y for x, y in zip(u, v)), sum(x*x for x in v)
                determinant = uu*vv-uv*uv
                if determinant <= 1e-14*uu*vv or not uu or not vv:
                    unresolved.append('退化原生曲线不能排除箭头歧义')
                    continue
                du, dv = sum(x*y for x, y in zip(delta, u)), sum(x*y for x, y in zip(delta, v))
                cosine, sine = (du*vv-dv*uv)/determinant, (dv*uu-du*uv)/determinant
                theta = math.atan2(sine, cosine)
                def point_at(t):
                    return [c+a*math.cos(t)+b*math.sin(t) for c, a, b in zip(part['center'], u, v)]
                start, sweep = part['start_param'], part['sweep_param']
                angle_tolerance = tolerance/max(min(math.sqrt(uu), math.sqrt(vv)), tolerance)
                angular = ((theta-start) if sweep >= 0 else (start-theta)) % (2*math.pi)
                on_curve = math.dist(point, point_at(theta)) <= tolerance
                on_sweep = (abs(sweep) >= 2*math.pi-angle_tolerance or angular <= abs(sweep)+angle_tolerance
                    or min(math.dist(point, point_at(start)), math.dist(point, point_at(start+sweep))) <= tolerance)
                if on_curve and on_sweep:
                    hits.append((tuple(h.upper() for h in other_path), index))
                continue
            if part['kind'] != 'line':
                unresolved.append('原生曲线类型尚不能排除箭头歧义')
                continue
            x, y = part['start'], part['end']
            v = [q-p for p, q in zip(x, y)]; length2 = sum(q*q for q in v)
            if not length2:
                continue
            t = sum((point[i]-x[i])*v[i] for i in range(3))/length2
            if -1e-9 <= t <= 1+1e-9 and math.dist(point, [x[i]+t*v[i] for i in range(3)]) <= tolerance:
                hits.append((tuple(h.upper() for h in other_path), index))
    if unresolved:
        return False, '; '.join(sorted(set(unresolved)))
    if hits != [(tuple(span['path']), span['part'])]:
        return False, '原生LEADER箭头命中交叉/重合源段，不能用近邻选择物理范围'
    annotation['native_leader_binding'] = {'path': fact['leader_path'],
        'annotation_handle': entity.dxf.annotation_handle, 'arrow_page_point': point,
        'source_part_fraction': fraction, 'unique_target': hits[0],
        'vertices': vertices, 'semantic_scope': 'complete_source_curved_part' if 'page_curve' in span else 'complete_source_straight_part'}
    return True, None


def typed_source_elevation(drawing, fact, row, view, source):
    """Source-read operands, delegated to the existing typed elevation engine.

    Only an explicit directed datum equation has automatic relation semantics.
    Measurement kind, actual ports and vertical route remain disclosed model
    interpretation. No input values, approvals or caller graphs are reused.
    """
    require(fact.get('route_kind') == 'vertical', 'typed系统图分支只接有据纯竖直范围')
    sid, source_facts, observations, datum_sources = 'diagram-source', [], [], {}
    spec = {'schema': 'qto-elevation-link/1.0', 'system': row['system'],
        'calculation_id': 'diagram:' + row['record_id'], 'datum_transforms': [],
        'sources': [{**source, 'id': sid, 'locator': 'diagram/view '+view['view_id'], 'facts': source_facts}]}

    def numeric(input_row, fid, kind, semantics=None):
        require(not ({'value', 'quantity', 'dn'} & set(input_row)), 'typed源操作数不接手填数值或DN替代外径')
        value, annotation, issue = annotation_value(drawing, input_row, view['bounds'])
        observations.append({'fact_id': fid, 'source_annotation': annotation})
        require(issue is None and value is not None, issue or 'typed操作数缺少明确源数值/单位')
        output = {'fact_id': fid, 'measurement_kind': kind, 'value': value, 'unit': 'm',
            'source_ids': [sid], 'level': fact['level'], 'basis': fact['basis'], **(semantics or {})}
        source_facts.append({**output, 'id': fid})
        return output

    kind_words = {'pipe_center': '管中心', 'equipment_port_center': '接口中心',
        'pipe_outer_bottom': '管外底', 'pipe_outer_top': '管外顶', 'finished_floor': '完成面',
        'structural_floor': '结构面', 'other_reference': '参照点'}
    for name, port in (('from', row['from_port_id']), ('to', row['to_port_id'])):
        end = fact.get(name)
        require(isinstance(end, dict), 'typed标高须给出两端原源事实')
        kind = end.get('measurement_kind'); require(kind in kind_words, 'typed测点类型无效')
        datum = text(end.get('datum_id'), 'typed基准身份')
        note = drawing.annotation(end.get('datum_annotation_path'), view['bounds'])
        datum_sources[name] = note
        require(datum in note['text'] and any(t in note['text'].lower() for t in ('基准', 'datum')),
                'typed基准声明须明确包含本端datum_id')
        semantics = drawing.annotation(end.get('measurement_semantics_annotation_path'), view['bounds'])
        require(kind_words[kind] in semantics['text'], 'typed测点定义源文与所报测点类型不符')
        observations += [{'side': name, 'datum_source': note, 'measurement_semantics_source': semantics}]
        is_center = kind in {'pipe_center', 'equipment_port_center'}
        reference = port if is_center else text(end.get('measurement_reference_id'), '非中心原测点身份')
        require(is_center or reference != port, '非中心原测点不能冒充实际中心端口')
        point = numeric(end, name, kind, {'datum_id': datum, 'reference_id': reference, 'system': row['system']})
        conversion = end.get('centerline_conversion')
        if is_center:
            require(conversion is None, '已是中心测点不得再次换算')
        else:
            require(isinstance(conversion, dict), '非中心测点须给出有源中心换算')
            method = 'outer_radius' if kind in {'pipe_outer_bottom', 'pipe_outer_top'} else 'signed_offset'
            require(conversion.get('method') == method, '测点换算方法与实际类型不符')
            operand_kind = 'outer_diameter' if method == 'outer_radius' else 'vertical_offset'
            word = '外径' if method == 'outer_radius' else '中心高差'
            definition = drawing.annotation(conversion.get('operand_semantics_annotation_path'), view['bounds'])
            require(word in definition['text'], '中心换算操作数须有实际外径/带符号中心高差定义，DN不能代外径')
            operand = numeric(conversion.get('operand', {}), name+':operand', operand_kind)
            cid = name+':centerline'
            converted = {'conversion_id': cid, 'method': method, 'target_reference_id': port,
                'source_ids': [sid], 'level': fact['level'], 'basis': fact['basis'],
                'outer_diameter' if method == 'outer_radius' else 'offset': operand}
            point['centerline_conversion'] = converted
            source_facts.append({'id': cid, 'measurement_kind': 'centerline_conversion', 'method': method,
                'from_fact_id': name, 'from_reference_id': reference, 'target_reference_id': port,
                'datum_id': datum, 'system': row['system'], 'operand_fact_id': name+':operand', 'level': fact['level']})
            observations.append({'side': name, 'conversion_operand_semantics_source': definition})
        spec[name] = point
    if (spec['from']['datum_id'] == spec['to']['datum_id']
            and datum_sources['from']['path'] != datum_sources['to']['path']):
        return {'method': 'typed_elevation', 'value': None, 'level': weakest([fact['level'], 'inferred']),
            'binding_verification': 'unresolved_distinct_source_datum_declarations', 'basis': fact['basis'],
            'source_observations': observations, 'elevation_calculation': None,
            'issue': '两端同名datum_id引用不同原源基准声明；不能把建筑/设备等独立基准按同名认同。须用原文可区分的基准身份及明确换算关系'}
    paths = fact.get('datum_transform_annotation_paths', [])
    require(isinstance(paths, list) and len({tuple(p) for p in paths}) == len(paths), '基准关系源路径重复或无效')
    equation = re.compile(r'^\s*[zZ]\(([^()=\s]+)\)\s*=\s*[zZ]\(([^()=\s]+)\)\s*([+\-](?:\d+(?:\.\d*)?|\.\d+))\s*(mm|cm|m|ft|in|毫米|厘米|米|英尺|英寸)\s*$', re.I)
    for index, path in enumerate(paths):
        annotation = drawing.annotation(path, view['bounds']); match = equation.fullmatch(annotation['text'])
        require(match is not None, '基准关系须为明确原源z(目标)=z(来源)+有单位高差，不能从相同名称推定')
        transform = {'transform_id': 'datum:'+str(index), 'measurement_kind': 'datum_offset',
            'from_datum': match[2], 'to_datum': match[1], 'value': float(match[3])*canonical_scale(match[4]),
            'unit': 'm', 'source_ids': [sid], 'level': fact['level'], 'basis': fact['basis']}
        source_facts.append({**transform, 'id': transform['transform_id']})
        spec['datum_transforms'].append(transform)
        observations.append({'transform_id': transform['transform_id'], 'source_annotation': annotation})
    route = {'kind': 'vertical', 'route_id': 'route', 'source_ids': [sid], 'level': fact['level'], 'basis': fact['basis']}
    spec['route'] = route
    source_facts.append({'id': 'route', 'measurement_kind': 'route_shape', 'route_kind': 'vertical',
        'from_reference_id': row['from_port_id'], 'to_reference_id': row['to_port_id'],
        'system': row['system'], 'level': fact['level']})
    calculation = evaluate_elevation_link(spec, 'm', expected_system=row['system'], previous_calculation=None)
    return {'method': 'typed_elevation', 'value': calculation['value'],
        'level': weakest([fact['level'], calculation['evidence_level'], 'inferred']),
        'binding_verification': 'source_read_typed_operands_model_port_and_route_interpretation',
        'basis': fact['basis'], 'source_observations': observations, 'elevation_calculation': calculation,
        'issue': None if calculation['value'] is not None else 'typed高程/测点/基准换算未闭合'}


def measurement(drawing, fact, row, view, source):
    require(isinstance(fact, dict), 'length_facts须逐项声明事实')
    level = fact.get('level')
    require(level in LEVELS, '事实必须声明confirmed/inferred/scenario')
    text(fact.get('basis'), '事实解释basis')
    method = fact.get('method')
    if method == 'unresolved':
        return {'method': method, 'value': None, 'level': level,
                'issue': text(fact.get('issue'), '缺证原因'), 'input_fact': copy.deepcopy(fact)}
    require(method in {'dimension', 'dimension_chain', 'elevation', 'typed_elevation'},
            '只支持明确尺寸/尺寸链、有源typed或同基准标高差或unresolved')
    require(fact.get('measurement_basis', BASIS) == BASIS, '当前长度只接受管中心线路径口径')
    if 'route_interval' in row.get('source_span', {}) and method != 'dimension_chain':
        return {'method': method, 'value': None, 'level': level,
            'issue': '有向多part原生路由当前仅接逐腿原生尺寸链，其他计长方式保留未决'}
    if method == 'dimension_chain':
        return dimension_chain(drawing, fact, row, view)
    if method == 'typed_elevation':
        return typed_source_elevation(drawing, fact, row, view, source)
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
                if 'page_curve' in span:
                    return {'method': method, 'value': None, 'level': level, 'source_annotation': annotation,
                        'binding_verification': 'unresolved_curve_linear_dimension',
                        'issue': '原生线性尺寸只证明弦线距离，不能代表曲线路径实长', 'basis': fact['basis']}
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
        elif value is not None and fact.get('leader_path'):
            verified, leader_issue = native_leader_binding(drawing, fact, row, annotation, view['bounds'])
            if verified:
                binding = 'source_native_leader_complete_part_verified'
            else:
                issue = leader_issue
                value = None
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
            require(not coverage_overlap(span, old),
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
