#!/usr/bin/env python3
"""Source-geometric dimension checks; semantic decisions stay working/unreviewed.

The source DIMENSION is measured by the existing neutral geometry reader. This
module checks selected references and finite dimensional relationships, never
infers an engineering role from a layer, a nearby line or a plausible answer.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

from qto_cad_geometry import dimension_measurement_facts, read_dxf_with_diagnostics, transform_point, transform_vector
from qto_cad_source_contract import canonical_scale, verify_source_file
from qto_recognition_workbench import build_index

SCHEMA = 'qto-dimension-semantics/1.0'
ROLES = {'axis', 'surface', 'cut', 'object_point'}


def _text(v, name):
    if not isinstance(v, str) or not v.strip():
        raise ValueError(name + ' 必须为非空文字')
    return v.strip()


def _number(v, name, positive=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (positive and v <= 0):
        raise ValueError(name + ' 必须为有限' + ('正数' if positive else '数值'))
    return float(v)


def _registry(rows, key, name):
    if not isinstance(rows, list):
        raise ValueError(name + ' 必须为数组')
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(name + ' 每项必须为对象')
        rid = _text(row.get(key), name + '.' + key)
        if rid in out:
            raise ValueError(name + ' 身份重复: ' + rid)
        out[rid] = row
    return out


def _vec(a, b):
    return [b[i] - a[i] for i in range(3)]


def _dot(a, b):
    return math.fsum(x*y for x, y in zip(a, b))


def _unit(v):
    length = math.sqrt(_dot(v, v))
    if length <= 1e-15:
        raise ValueError('零长度指向')
    return [x/length for x in v]


def _parallel(a, b):
    return abs(abs(_dot(_unit(a), _unit(b))) - 1) <= 1e-8


def _projection_covector(direction, matrix):
    """Pull a local dot-product through an affine instance: c = A^-T u.

    A transformed arrow A*u describes the drawing direction, but it does not
    measure the same projection under non-uniform scaling. Keep both notions.
    """
    columns = [[matrix[i], matrix[i+4], matrix[i+8]] for i in range(3)]
    def cross(a, b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    duals = [cross(columns[1], columns[2]), cross(columns[2], columns[0]), cross(columns[0], columns[1])]
    determinant = _dot(columns[0], duals[0])
    if determinant == 0 or not math.isfinite(determinant):
        raise ValueError('尺寸实例变换不可逆，不能恢复原量测投影')
    unit = _unit(direction)
    result = [math.fsum(duals[j][i]*unit[j] for j in range(3))/determinant for i in range(3)]
    if not all(math.isfinite(x) for x in result):
        raise ValueError('尺寸投影变换不是有限值')
    return result


def _measurement_axis(fact):
    return (fact['measurement_covector_wcs'] if fact['selected']['measurement_kind'] == 'projected_length'
            else fact['measurement_direction_wcs'])


def _ancestor_issues(row, objects):
    issues = []
    while row:
        # A missing complete container bounding box alone is not a semantic
        # conflict. Dynamic/XREF/unsupported state must propagate to children.
        for issue in row.get('issues', []):
            if '范围仅覆盖已解析内容' not in issue and '没有可定位的完整几何' not in issue:
                issues.append({'object_id': row['object_id'], 'reason': issue})
        row = objects.get(row.get('parent_object_id'))
    return issues


def _rectangle_center_projection(row, definition_point, tolerance, measurement_direction, measurement_covector):
    geometry = row.get('geometry') or {}
    parts = geometry.get('parts', [])
    if geometry.get('closed') is not True or len(parts) != 4 or any(p.get('kind') != 'line' for p in parts):
        raise ValueError('中心投影只接受同一源实体的四条闭合直边，不能使用任意包围框')
    points = [p['start'] for p in parts]
    if any(math.dist(parts[i]['end'], points[(i+1) % 4]) > tolerance for i in range(4)):
        raise ValueError('矩形四边没有按源顺序首尾闭合')
    edges = [_vec(points[i], points[(i+1) % 4]) for i in range(4)]
    lengths = [math.sqrt(_dot(v, v)) for v in edges]
    if min(lengths) <= tolerance:
        raise ValueError('退化四边形不能提供矩形中心')
    unit_edges = [_unit(v) for v in edges]
    if any(abs(_dot(unit_edges[i], unit_edges[(i+1) % 4])) > 1e-8 for i in range(4)):
        raise ValueError('源四边形不是矩形，不能由包围框猜中心')
    a, b = unit_edges[0], unit_edges[1]
    normal = _unit([a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]])
    if any(abs(_dot(_vec(points[0], p), normal)) > tolerance for p in points[1:]):
        raise ValueError('矩形源点不共面')
    midpoint1 = [(points[0][i]+points[2][i])/2 for i in range(3)]
    midpoint2 = [(points[1][i]+points[3][i])/2 for i in range(3)]
    if math.dist(midpoint1, midpoint2) > tolerance:
        raise ValueError('两条源对角线没有共同中点')
    center = midpoint1
    offset = _vec(center, definition_point)
    if abs(_dot(offset, normal)) > tolerance:
        raise ValueError('尺寸定义点与源矩形不在同一平面')
    if abs(_dot(_unit(measurement_direction), normal)) > 1e-8:
        raise ValueError('尺寸量测方向不在源矩形平面内')
    # Covector preserves the original projection under affine block transforms;
    # normalization converts its station residual to a WCS length tolerance.
    projection_normal = _unit(measurement_covector)
    station_error = _dot(offset, projection_normal)
    if abs(station_error) > tolerance:
        raise ValueError('源矩形中心与尺寸定义点的量测投影站位不同')
    distance = math.sqrt(_dot(offset, offset))
    return {'point_wcs': center, 'source_center_wcs': center, 'source_vertices_wcs': points,
            'definition_point_wcs': definition_point, 'source_geometry_basis': 'four_closed_orthogonal_coplanar_source_edges',
            'source_plane_normal_wcs': normal, 'projection_offset_vector_wcs': offset,
            'projection_offset_distance_raw': distance, 'distance_to_definition_raw': distance,
            'measurement_station_error_raw': station_error, 'match_mode': 'measurement_projection',
            'point_coincident': distance <= tolerance, 'verification_mode': 'source_rectangle_center_projection'}


def _target_point(target, row, definition_point, tolerance, measurement_kind=None, measurement_direction=None, measurement_covector=None):
    selector = target.get('selector')
    if not isinstance(selector, dict) or set(selector) - {'kind', 'part_index', 'end'}:
        raise ValueError('target.selector 只接受原图线段的 endpoint/on_line 选择，不接受手写坐标')
    if selector.get('kind') == 'rectangle_center_projection':
        if set(selector) != {'kind'}:
            raise ValueError('矩形中心必须从完整源实体推导，不能指定任意分段或坐标')
        if measurement_kind != 'projected_length':
            raise ValueError('矩形中心投影必须显式使用 projected_length 解释')
        return _rectangle_center_projection(row, definition_point, tolerance, measurement_direction, measurement_covector)
    parts = (row.get('geometry') or {}).get('parts', [])
    i = selector.get('part_index', 0)
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(parts):
        raise ValueError('目标几何分段不存在')
    part = parts[i]
    if part.get('kind') != 'line':
        raise ValueError('当前只复核直线段端点或线内点，曲线目标保持未决')
    if selector.get('kind') == 'endpoint':
        end = selector.get('end')
        if end not in {'start', 'end'}:
            raise ValueError('endpoint 必须选择 start/end')
        p = part[end]
    elif selector.get('kind') == 'on_line':
        a, b = part['start'], part['end']
        delta = _vec(a, b)
        denom = _dot(delta, delta)
        if denom <= 0:
            raise ValueError('目标线段零长度')
        t = _dot(_vec(a, definition_point), delta)/denom
        if t < -tolerance/math.sqrt(denom) or t > 1 + tolerance/math.sqrt(denom):
            raise ValueError('尺寸定义点只在目标线延长线上，没有指向该源线段')
        p = [a[k] + max(0., min(1., t))*delta[k] for k in range(3)]
    else:
        raise ValueError('目标选择器必须为 endpoint/on_line')
    distance = math.dist(p, definition_point)
    if distance > tolerance:
        raise ValueError('尺寸定义点没有落在选定源目标上')
    return {'point_wcs': p, 'distance_to_definition_raw': distance,
            'definition_point_wcs': definition_point, 'match_mode': 'spatial_coincidence', 'point_coincident': True}


def review_dimension_semantics(document, base_dir=None):
    """Re-read sources and return source-point checks and conditional facts.

    Source file identity proves freshness only. Targets' engineering meanings
    and candidate selection are model assertions, never professional approval.
    """
    d = copy.deepcopy(document)
    if d.get('schema_version') != SCHEMA:
        raise ValueError('需要 ' + SCHEMA)
    if d.get('adoption_scope', 'working') != 'working' or d.get('review_status', 'unreviewed') != 'unreviewed':
        raise ValueError('本工具只能输出 working/unreviewed 模型识读')
    source_rows = _registry(d.get('sources'), 'source_id', 'sources')
    targets = _registry(d.get('targets', []), 'target_id', 'targets')
    identities = set()
    for target in targets.values():
        identity = tuple(target.get(k) for k in ('source_id', 'view_id', 'physical_object_key', 'reference_id'))
        if identity in identities:
            raise ValueError('同一对象测点不能用多个 target_id 重复登记')
        identities.add(identity)
    dimensions = _registry(d.get('dimensions'), 'dimension_id', 'dimensions')
    if not dimensions:
        raise ValueError('至少需要一个源尺寸')
    tolerance_m = _number(d.get('tolerance_m', 1e-6), 'tolerance_m', positive=True)
    if tolerance_m > .01:
        raise ValueError('几何指向容差不能大于 10mm；粗糙示意关系应保留未决')
    if 'tolerance_m' in d:
        _text(d.get('tolerance_basis'), 'tolerance_basis')
    sources, cache = {}, {}
    for sid, source in source_rows.items():
        source['path'] = str((Path(base_dir or '.')/source['path']).resolve())
        verify_source_file(source, '尺寸来源 ' + sid)
        _text(source.get('view_basis'), 'source.view_basis')
        sources[sid] = source

    def context(sid, layout):
        key = (sid, layout)
        if key not in cache:
            source = sources[sid]
            if Path(source['path']).suffix.lower() != '.dxf':
                raise ValueError('几何复核需要原始或有交换记录的 DXF')
            index = build_index(source, layout=layout)
            doc, diagnostic = read_dxf_with_diagnostics(source['path'])
            unit_scale = canonical_scale(index['raw_unit'])
            if source.get('raw_unit') is not None and canonical_scale(source['raw_unit']) != unit_scale:
                raise ValueError('声明单位与 DXF INSUNITS 不一致')
            cache[key] = (index, {o['object_id']: o for o in index['objects']}, doc, unit_scale)
        return cache[key]

    facts = {}
    for did, dim in dimensions.items():
        sid = dim.get('source_id')
        if sid not in sources:
            raise ValueError('尺寸引用不存在的 source_id')
        _text(dim.get('view_id'), 'dimension.view_id')
        _text(dim.get('basis'), 'dimension.basis')
        kind = dim.get('evidence_kind', 'cad_dimension')
        output = {'dimension_id': did, 'source_id': sid, 'source_sha256': sources[sid]['sha256'],
                  'view_id': dim['view_id'], 'basis': dim['basis'], 'adoption_scope': 'working',
                  'review_status': 'unreviewed', 'semantic_truth_verified': False,
                  'formal_adoption_allowed': False, 'status': 'unresolved',
                  'usable_for_working_calculation': False, 'issues': [], 'candidates': []}
        if kind == 'document_observation':
            _text(dim.get('locator'), 'document.locator')
            output.update(verification_mode='non_geometric_review', locator=dim['locator'])
            if 'observed_value' in dim:
                output['observed_candidate_m'] = _number(dim['observed_value'], 'observed_value', True)*canonical_scale(dim.get('unit'))
            output['issues'].append('document_observation_not_geometrically_verified')
            facts[did] = output
            continue
        if kind != 'cad_dimension':
            raise ValueError('未知尺寸证据类型')
        layout = dim.get('layout', 'Model')
        index, objects, doc, raw_scale = context(sid, layout)
        oid = dim.get('object_id')
        if oid not in objects or objects[oid]['entity_type'] != 'DIMENSION':
            raise ValueError('尺寸必须引用布局中真实 DIMENSION 的完整实例路径')
        row = objects[oid]
        measure = dimension_measurement_facts(doc.entitydb[row['source_handle']])
        output.update(verification_mode='source_definition_points', object_id=oid,
                      measurement_facts=measure, source_unit=index['raw_unit'])
        observation = dim.get('model_design_observation')
        if observation is not None:
            if not isinstance(observation, dict):
                raise ValueError('model_design_observation 必须为有定位的模型识读对象')
            for key in ('basis', 'locator', 'raw_text_excerpt'):
                _text(observation.get(key), 'model_design_observation.' + key)
            texts = [measure.get('text_override', '')] + [r['text'] for r in measure.get('rendered_text_candidates', [])]
            if observation['raw_text_excerpt'] not in texts:
                raise ValueError('模型识读原文必须对应源 DIMENSION 覆盖文字或其图块文字')
            output['model_design_observation'] = observation
            output['observed_candidate_m'] = _number(observation.get('value'), 'model_design_observation.value', True)*canonical_scale(observation.get('unit'))
            output['issues'].append('model_text_observation_remains_candidate')
        issues = output['issues']
        if index['source_identity_issues']:
            issues.append('source_identity_conflict')
        if index['conversion_warnings']:
            issues.append('source_exchange_review_required')
        inherited = _ancestor_issues(row, objects)
        if inherited:
            output['inherited_issues'] = inherited
            issues.append('source_or_parent_state_unverified')
        if not measure.get('linear_measurement_usable'):
            issues.append('dimension_value_requires_review')
        definition = measure.get('definition_points_wcs', {})
        if not all(key in definition for key in ('defpoint2', 'defpoint3')):
            issues.append('dimension_definition_points_missing')
            facts[did] = output
            continue
        p2, p3 = [transform_point(definition[key], row['instance_transform']) for key in ('defpoint2', 'defpoint3')]
        local_delta = _vec(definition['defpoint2'], definition['defpoint3'])
        if measure['dimtype'] == 0:
            from ezdxf.math import Vec3
            local_direction = list(doc.entitydb[row['source_handle']].ocs().to_wcs(Vec3.from_angle(math.radians(measure['angle_degrees']))))
        else:
            local_direction = local_delta
        output['measurement_direction_wcs'] = transform_vector(local_direction, row['instance_transform'])
        output['measurement_covector_wcs'] = _projection_covector(local_direction, row['instance_transform'])
        if math.dist(p2, p3) <= 1e-12:
            issues.append('zero_world_span')
        # Design length belongs to the DIMENSION annotation (including DIMLFAC).
        # INSERT scaling changes depiction, never silently multiplies design length.
        value = measure.get('scaled_measurement_candidate')
        design_scale = canonical_scale(dim['design_unit']) if dim.get('design_unit') else raw_scale
        if dim.get('design_unit'):
            _text(dim.get('design_unit_basis'), 'dimension.design_unit_basis')
        if measure.get('linear_factor') != 1:
            if not dim.get('design_unit') or not dim.get('annotation_scale_basis'):
                issues.append('scaled_annotation_unit_and_view_basis_required')
        if design_scale != raw_scale and measure.get('linear_factor') == 1:
            issues.append('annotation_unit_change_without_scale_evidence')
        output['design_candidate_m'] = value*design_scale if value is not None else None
        output['design_unit'] = dim.get('design_unit', index['raw_unit'])
        output['definition_points_wcs'] = [p2, p3]
        output['depiction_span_m'] = math.dist(p2, p3)*raw_scale
        output['depiction_to_design_scale_not_assumed'] = True
        candidate_rows = _registry(dim.get('candidates'), 'candidate_id', 'dimension.candidates')
        for cid, candidate in candidate_rows.items():
            _text(candidate.get('basis'), 'candidate.basis')
            if candidate.get('measurement_kind') not in {'aligned_length', 'projected_length'}:
                raise ValueError('候选 measurement_kind 必须明确 aligned_length/projected_length')
            ci = {'candidate_id': cid, 'basis': candidate['basis'], 'measurement_kind': candidate['measurement_kind'],
                  'endpoint_checks': [], 'issues': [], 'target_ids': []}
            if (candidate['measurement_kind'] == 'aligned_length') != (measure.get('dimtype') == 1):
                ci['issues'].append('dimension_projection_kind_mismatch')
            ends = candidate.get('endpoints')
            if not isinstance(ends, list) or len(ends) != 2:
                raise ValueError('每个解释必须按 defpoint2/defpoint3 顺序选择两个 target')
            for endpoint, point in zip(ends, (p2, p3)):
                if not isinstance(endpoint, dict) or set(endpoint) != {'target_id'}:
                    raise ValueError('尺寸两端仅以 target_id 引用源目标，不接受自写点或角色覆盖')
                tid = endpoint['target_id']
                if tid not in targets:
                    raise ValueError('候选指向不存在的 target_id')
                target = targets[tid]
                ci['target_ids'].append(tid)
                for required in ('physical_object_key', 'reference_id', 'basis', 'view_id'):
                    _text(target.get(required), 'target.' + required)
                if target.get('role') not in ROLES:
                    raise ValueError('target.role 必须明确 axis/surface/cut/object_point')
                if target.get('source_id') != sid or target.get('view_id') != dim['view_id']:
                    ci['issues'].append('target_source_or_view_mismatch')
                    continue
                target_row = objects.get(target.get('object_id'))
                if target_row is None:
                    ci['issues'].append('target_instance_path_missing')
                    continue
                inherited = _ancestor_issues(target_row, objects)
                if inherited:
                    ci['issues'].append('target_or_parent_state_unverified')
                try:
                    checked = _target_point(target, target_row, point, tolerance_m/raw_scale,
                                            candidate['measurement_kind'], output['measurement_direction_wcs'], output['measurement_covector_wcs'])
                    checked.update(target_id=tid, source_object_id=target_row['object_id'], role=target['role'],
                                   physical_object_key=target['physical_object_key'], reference_id=target['reference_id'])
                    ci['endpoint_checks'].append(checked)
                except ValueError as exc:
                    ci['issues'].append(str(exc))
            if len(ci['target_ids']) == 2 and ci['target_ids'][0] == ci['target_ids'][1]:
                ci['issues'].append('same_reference_at_both_ends')
            ci['geometry_points_match'] = len(ci['endpoint_checks']) == 2 and not ci['issues']
            output['candidates'].append(ci)
        chosen = dim.get('selected_candidate_id')
        if chosen is None:
            issues.append('interpretation_not_selected')
        elif chosen not in candidate_rows:
            raise ValueError('selected_candidate_id 不存在')
        else:
            _text(dim.get('selection_basis'), 'selection_basis')
            selected = next(c for c in output['candidates'] if c['candidate_id'] == chosen)
            output['selected_candidate_id'] = chosen
            output['selection_basis'] = dim['selection_basis']
            output['selected'] = selected
            if not selected['geometry_points_match']:
                issues.append('selected_interpretation_has_geometric_conflict')
        if not issues:
            output.update(status='geometrically_consistent_working_interpretation',
                          usable_for_working_calculation=True, value_m=output['design_candidate_m'])
        facts[did] = output

    # A reference identity names one measured point. An entire support line can
    # intersect many stations; its reused ID must not silently bridge them.
    bindings = {}
    for did, f in facts.items():
        for endpoint in f.get('selected', {}).get('endpoint_checks', []):
            bindings.setdefault(endpoint['target_id'], []).append((did, endpoint['point_wcs']))
    for tid, observations in bindings.items():
        if len(observations) < 2:
            continue
        source_unit = facts[observations[0][0]]['source_unit']
        if any(math.dist(observations[0][1], p)*canonical_scale(source_unit) > tolerance_m for _, p in observations[1:]):
            for did, _ in observations:
                facts[did]['issues'].append('same_reference_point_drift:' + tid)
                facts[did]['usable_for_working_calculation'] = False
                facts[did]['status'] = 'unresolved'

    constraints = _registry(d.get('constraints', []), 'constraint_id', 'constraints')
    checks = []
    for cid, c in constraints.items():
        _text(c.get('basis'), 'constraint.basis')
        kind = c.get('kind')
        involved = [c.get('whole'), c.get('part')] if kind == 'contained_length' else [c.get('total')] + [p.get('dimension_id') for p in c.get('parts', [])]
        if any(i not in facts for i in involved):
            raise ValueError('约束引用未知尺寸')
        check = {'constraint_id': cid, 'kind': kind, 'basis': c['basis'], 'dimension_ids': involved, 'issues': []}
        unavailable = any(not facts[i]['usable_for_working_calculation'] for i in involved)
        if unavailable:
            check['issues'].append('constraint_input_unresolved')
        if kind == 'contained_length':
            whole, part = (facts[i] for i in involved)
            whole_value = whole.get('value_m', whole.get('observed_candidate_m', whole.get('design_candidate_m')))
            part_value = part.get('value_m', part.get('observed_candidate_m', part.get('design_candidate_m')))
            if whole_value is not None and part_value is not None and part_value > whole_value + tolerance_m:
                check['issues'].append('component_design_length_exceeds_whole')
            if c['whole'] == c['part']:
                check['issues'].append('self_containment_not_a_check')
            if unavailable:
                check['status'] = 'unresolved_or_conflict'
                checks.append(check)
                continue
            if whole['view_id'] != part['view_id'] or whole['source_id'] != part['source_id']:
                check['issues'].append('cross_view_containment_requires_explicit_correspondence')
            a, b = whole['definition_points_wcs']; p, q = part['definition_points_wcs']
            if (whole['selected']['measurement_kind'] != part['selected']['measurement_kind']
                    or not _parallel(_measurement_axis(whole), _measurement_axis(part))):
                check['issues'].append('component_measurement_projection_mismatch')
            projected = whole['selected']['measurement_kind'] == 'projected_length'
            if not projected and not _parallel(_vec(a, b), _vec(p, q)):
                check['issues'].append('component_not_parallel_to_whole')
            axis = _unit(_measurement_axis(whole)); whole_station = _dot(_vec(a, b), axis)
            if whole_station < 0:
                axis = [-x for x in axis]; whole_station = -whole_station
            for endpoint in (p, q):
                station = _dot(_vec(a, endpoint), axis)
                nearest = [a[i] + station*axis[i] for i in range(3)]
                scale = canonical_scale(whole['source_unit'])
                if (station*scale < -tolerance_m or (station-whole_station)*scale > tolerance_m
                        or (not projected and math.dist(endpoint, nearest)*scale > tolerance_m)):
                    check['issues'].append('component_source_extent_outside_whole')
            whole_keys = {e['physical_object_key'] for e in whole['selected']['endpoint_checks']}
            part_keys = {e['physical_object_key'] for e in part['selected']['endpoint_checks']}
            if whole_keys != part_keys:
                check['issues'].append('containment_physical_identity_mismatch')
        elif kind == 'directed_chain':
            if unavailable:
                check['status'] = 'unresolved_or_conflict'
                checks.append(check)
                continue
            parts = c['parts']
            if not parts or len(set(involved[1:])) != len(parts) or c['total'] in involved[1:]:
                check['issues'].append('empty_or_duplicate_chain_segment')
            total = facts[c['total']]
            target_pair = total['selected']['target_ids']
            previous = target_pair[0]
            values = []
            total_direction = _measurement_axis(total)
            if _dot(total_direction, _vec(*total['definition_points_wcs'])) < 0:
                total_direction = [-x for x in total_direction]
            for part in parts:
                if set(part) != {'dimension_id', 'direction'} or isinstance(part['direction'], bool) or part['direction'] not in (1, -1):
                    raise ValueError('链段必须明确 dimension_id 和 direction=1/-1')
                f = facts[part['dimension_id']]
                pair = list(f['selected']['target_ids'])
                if part['direction'] == -1:
                    pair.reverse()
                if pair[0] != previous:
                    check['issues'].append('chain_endpoint_identity_or_direction_mismatch')
                previous = pair[1]
                if f['source_id'] != total['source_id'] or f['view_id'] != total['view_id']:
                    check['issues'].append('chain_cross_view_requires_correspondence')
                if (total['selected']['measurement_kind'] != f['selected']['measurement_kind']
                        or not _parallel(_measurement_axis(total), _measurement_axis(f))):
                    check['issues'].append('non_collinear_chain_needs_separate_projection_facts')
                a, b = f['definition_points_wcs']
                if _dot(total_direction, _vec(a, b))*part['direction'] <= 0:
                    check['issues'].append('chain_backtracks_or_has_zero_projection')
                values.append(f['value_m'])
            if previous != target_pair[1]:
                check['issues'].append('chain_does_not_end_at_total_reference')
            if not math.isclose(math.fsum(values), total['value_m'], abs_tol=tolerance_m, rel_tol=1e-9):
                check['issues'].append('design_dimension_chain_not_closed')
            check.update(total_m=total['value_m'], sum_parts_m=math.fsum(values))
        else:
            raise ValueError('不支持的约束，不能静默忽略: ' + str(kind))
        check['status'] = 'consistent' if not check['issues'] else 'unresolved_or_conflict'
        checks.append(check)
    for check in checks:
        if check['issues']:
            for did in check['dimension_ids']:
                facts[did]['usable_for_working_calculation'] = False
                facts[did]['status'] = 'unresolved'
                facts[did]['issues'].append('constraint_failed:' + check['constraint_id'])
    return {'schema_version': SCHEMA, 'adoption_scope': 'working', 'review_status': 'unreviewed',
            'formal_adoption_allowed': False, 'semantic_truth_verified': False,
            'sources': list(sources.values()), 'targets': list(targets.values()), 'dimensions': list(facts.values()),
            'constraints': checks, 'status': 'WORKING_CONSISTENT' if all(f['usable_for_working_calculation'] for f in facts.values()) else 'PARTIAL_BLOCKED'}


def steel_length_derivation(document, mapping, base_dir=None):
    """Prepare the existing steel arithmetic contract, never approve its evidence.

    Each end is an ordered dimension path from the selected span reference to
    the cut. The source-backed path prevents free or duplicate deductions.
    """
    review = review_dimension_semantics(document, base_dir)
    facts = {f['dimension_id']: f for f in review['dimensions']}
    targets = {t['target_id']: t for t in review['targets']}
    span = facts.get(mapping.get('span_dimension_id'))
    if not span or not span['usable_for_working_calculation']:
        raise ValueError('钢构件跨度尚未通过工作用尺寸复核')
    target_ids = span['selected']['target_ids']
    roles = [targets[t]['role'] for t in target_ids]
    span_source_points = [e['point_wcs'] for e in span['selected']['endpoint_checks']]
    if not _parallel(span['measurement_direction_wcs'], _vec(*span_source_points)):
        raise ValueError('投影跨度不能代替钢构件实际轴向长度')
    if roles == ['axis', 'axis']:
        span_basis = 'support_centres'
    elif roles == ['surface', 'surface']:
        span_basis = 'support_faces'
    elif roles == ['cut', 'cut']:
        span_basis = 'cut_faces'
    else:
        raise ValueError('钢构件跨度两端必须采用同一轴线/支承面/切口基准')
    ends = _registry(mapping.get('ends'), 'end_id', 'steel.ends')
    if set(ends) != {'start', 'end'}:
        raise ValueError('钢长度必须完整登记 start/end 两端')
    evidence = {span['dimension_id']: {'evidence_class': 'object_geometry', 'fact_status': 'candidate',
                                     'adoption_scope': 'working', 'dimension_semantic_review': span}}
    end_conditions = []
    used_spans = set()
    for end_id, reference in zip(('start', 'end'), target_ids):
        end = ends[end_id]
        path = end.get('dimension_ids')
        if not isinstance(path, list):
            raise ValueError('端部必须列出有源尺寸路径；切口起算可为空')
        current = reference
        center_to_face = 0.; face_to_cut = 0.; eids = []
        for did in path:
            f = facts.get(did)
            if not f or not f['usable_for_working_calculation']:
                raise ValueError('端部路径含未决尺寸')
            pair = f['selected']['target_ids']
            if current not in pair:
                raise ValueError('端部扣减与起算引用不连续')
            other = pair[1] if current == pair[0] else pair[0]
            canonical_pair = (f['source_id'], f['view_id'], frozenset(tuple(round(n, 12) for n in p) for p in f['definition_points_wcs']))
            if canonical_pair in used_spans or did == span['dimension_id']:
                raise ValueError('同一尺寸区段重复扣减')
            used_spans.add(canonical_pair)
            if f['source_id'] != span['source_id'] or f['view_id'] != span['view_id']:
                raise ValueError('端部尺寸跨图关系尚未验证')
            p, q = [e['point_wcs'] for e in f['selected']['endpoint_checks']]
            if not _parallel(f['measurement_direction_wcs'], _vec(p, q)):
                raise ValueError('投影尺寸不能代替端部轴向扣减')
            direction = _vec(p, q) if current == pair[0] else _vec(q, p)
            axis = _vec(*span_source_points)
            if not _parallel(axis, direction) or _dot(axis, direction)*(1 if end_id == 'start' else -1) <= 0:
                raise ValueError('端部扣减必须沿构件轴向向内，不得反向或扣偏移量')
            from_role, to_role = targets[current]['role'], targets[other]['role']
            if (from_role, to_role) == ('axis', 'surface'):
                center_to_face += f['value_m']
            elif (from_role, to_role) == ('surface', 'cut'):
                face_to_cut += f['value_m']
            else:
                raise ValueError('端部路径须明确 axis→surface→cut，不能跳过或重复基准')
            current = other
            eids.append(did)
            evidence[did] = {'evidence_class': 'object_geometry', 'fact_status': 'candidate',
                             'adoption_scope': 'working', 'dimension_semantic_review': f}
        if targets[current]['role'] != 'cut':
            raise ValueError('端部路径尚未到达实际切口')
        end_conditions.append({'end_id': end_id, 'axis_to_face_m': center_to_face,
                               'face_to_cut_m': face_to_cut, 'evidence_ids': eids or [span['dimension_id']]})
    derivation = {'span_m': span['value_m'], 'basis': _text(mapping.get('basis'), 'steel.basis'),
                  'span_basis': span_basis, 'evidence_ids': [span['dimension_id']], 'end_conditions': end_conditions}
    if sum(e['axis_to_face_m'] + e['face_to_cut_m'] for e in end_conditions) >= span['value_m']:
        raise ValueError('端部扣减之和必须小于跨度')
    return {'length_derivation': derivation, 'evidence': evidence, 'review': review,
            'adoption_scope': 'working', 'review_status': 'unreviewed', 'formal_adoption_allowed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--steel-mapping')
    args = parser.parse_args()
    input_path, output_path = Path(args.input).resolve(), Path(args.output).resolve()
    if output_path.exists():
        raise ValueError('输出已存在，不能覆盖源资料或旧复核记录')
    document = json.loads(input_path.read_text(encoding='utf-8-sig'))
    if args.steel_mapping:
        mapping = json.loads(Path(args.steel_mapping).read_text(encoding='utf-8-sig'))
        result = steel_length_derivation(document, mapping, input_path.parent)
    else:
        result = review_dimension_semantics(document, input_path.parent)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'status': result.get('status', result.get('review', {}).get('status')),
                      'output': str(output_path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
