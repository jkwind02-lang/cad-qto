"""Review identified pipe lines and evidenced derived edges without changing quantities.

CSV imports are evidence snapshots, not executable producers. Every source row has
an explicit disposition; coordinates are read from the hashed source, never from
an importer-supplied replacement. Main-network lengths and DN remain untouched.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import stat
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'))
from qto_pipe_topology import geometry_parts
from qto_spatial import BoundsIndex
from pipe_gap_candidates import protect_outputs, source_paths

SCHEMA = 'qto-pipe-overlap-review/1.0'
UNITS = {'m': 1., 'mm': .001, 'cm': .01}
KINDS = {'drawn', 'label_gap', 'derived', 'topology_bridge'}


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + '须为非空文本')
    return value.strip()


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + '须为有限数值')
    return float(value)


def evidence(value, name):
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(name + '须为非空来源文本数组')
    return value


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def config(data):
    cfg = data.get('overlap_review', {})
    if not isinstance(cfg, dict):
        raise ValueError('overlap_review须为对象')
    return cfg


def review_source_paths(data, input_path):
    imports = config(data).get('imported_edges', [])
    if not isinstance(imports, list) or any(not isinstance(v, dict) for v in imports):
        raise ValueError('overlap_review.imported_edges须为对象数组')
    parent = Path(input_path).resolve().parent
    return [(parent / text(v.get('source_path'), 'imported_edges.source_path')).resolve() for v in imports]


def protect_review_outputs(outputs, sources):
    """Reject source aliases/hardlinks and existing reparse output components."""
    protect_outputs(outputs, sources)
    for value in outputs:
        p = Path(os.path.abspath(value))
        for component in [p, *p.parents]:
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if component.is_symlink() or (getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
                raise ValueError('输出路径含符号链接或reparse组件')


def parameters(data):
    unit = data.get('unit')
    cfg = config(data)
    enabled = cfg.get('enabled', False)
    if not isinstance(enabled, bool):
        raise ValueError('overlap_review.enabled须为布尔值，只有显式true才启用')
    raw = cfg.get('parameters', {})
    if not isinstance(raw, dict):
        raise ValueError('overlap_review.parameters须为对象')
    keys = ('angle_tolerance_deg', 'distance_tolerance', 'min_overlap', 'elevation_tolerance')
    values = {key: finite(raw[key], key) if raw.get(key) is not None else None for key in keys}
    angle = values['angle_tolerance_deg']
    if angle is not None and not 0 <= angle < 90:
        raise ValueError('共线角度容差须在[0,90)度')
    for key in ('distance_tolerance', 'min_overlap', 'elevation_tolerance'):
        if values[key] is not None and values[key] < 0:
            raise ValueError(key + '不能为负')
    missing = []
    if enabled:
        missing.extend('parameters.' + key for key in keys[:3] if values[key] is None)
        for field in ('unit_source_ids',):
            if not cfg.get(field):
                missing.append(field)
            else:
                evidence(cfg[field], 'overlap_review.' + field)
        if not cfg.get('unit_basis'):
            missing.append('unit_basis')
        else:
            text(cfg['unit_basis'], 'overlap_review.unit_basis')
        if not raw.get('source_ids'):
            missing.append('parameters.source_ids')
        else:
            evidence(raw['source_ids'], 'overlap_review.parameters.source_ids')
        if not raw.get('basis'):
            missing.append('parameters.basis')
        else:
            text(raw['basis'], 'overlap_review.parameters.basis')
        if cfg.get('elevation_mode') not in ('source_3d', 'plan_only'):
            missing.append('elevation_mode')
        elif cfg['elevation_mode'] == 'source_3d' and values['elevation_tolerance'] is None:
            missing.append('parameters.elevation_tolerance')
        elif cfg['elevation_mode'] == 'plan_only' and values['elevation_tolerance'] is not None:
            raise ValueError('plan_only不适用Z阈值；有三维来源应显式使用source_3d并提供Z依据')
    return {**values, 'enabled': enabled, 'unit': unit, 'source_ids': raw.get('source_ids', []),
            'basis': raw.get('basis'), 'elevation_mode': cfg.get('elevation_mode'),
            'unit_source_ids': cfg.get('unit_source_ids', []), 'unit_basis': cfg.get('unit_basis'),
            'missing_parameters': missing, 'engineering_threshold_defaults': None}


def imported_inventory(data, input_path):
    """Read only fixed CSV columns; hash binds all rows and their full metadata."""
    items, unreviewed, sources = [], [], []
    unit = data.get('unit')
    seen_paths, seen_ids = set(), set()
    for request, path in zip(config(data).get('imported_edges', []), review_source_paths(data, input_path)):
        expected = text(request.get('source_sha256'), 'source_sha256').lower()
        if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('source_sha256须为完整SHA256')
        if sha256(path) != expected:
            raise ValueError('导入边源文件SHA256不一致')
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in seen_paths:
            raise ValueError('导入边源文件重复或硬链接重复')
        seen_paths.add(identity)
        if request.get('format') != 'csv':
            raise ValueError('导入边只支持固定csv读取器，不执行producer或命令')
        source_id = text(request.get('source_id'), 'source_id')
        source_unit = request.get('unit')
        if source_unit not in UNITS or unit not in UNITS:
            raise ValueError('导入边源/输出单位须明确为m、mm或cm，不能默认')
        evidence(request.get('unit_source_ids'), 'unit_source_ids')
        text(request.get('unit_basis'), 'unit_basis')
        factor = UNITS[source_unit] / UNITS[unit]
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows, names = list(reader), reader.fieldnames or []
        columns = request.get('columns')
        if not isinstance(columns, dict):
            raise ValueError('columns须为对象')
        for key in ('start', 'end'):
            cols = columns.get(key)
            if not isinstance(cols, list) or len(cols) not in (2, 3) or any(c not in names for c in cols):
                raise ValueError('columns.' + key + '须引用源CSV的2或3个坐标列')
        if len(columns['start']) != len(columns['end']):
            raise ValueError('导入边起终点维度不同')
        for key, name in columns.items():
            if key not in ('start', 'end') and (not isinstance(name, str) or name not in names):
                raise ValueError('columns.' + key + '未引用源CSV字段')
        decisions = request.get('rows')
        if not isinstance(decisions, list) or len(decisions) != len(rows):
            raise ValueError('导入边须逐源行列全量处置，不能遗漏未导入实例')
        indexes = [v.get('row_index') for v in decisions if isinstance(v, dict)]
        if any(isinstance(i, bool) or not isinstance(i, int) for i in indexes) or sorted(indexes) != list(range(len(rows))):
            raise ValueError('导入边row_index须唯一完整覆盖源CSV数据行，首行为0')
        for decision in decisions:
            index = decision['row_index']
            row = rows[index]
            basis = text(decision.get('basis'), '导入边basis')
            src = evidence(decision.get('source_ids'), '导入边source_ids')
            provenance = {'source_id': source_id, 'path': str(path), 'sha256': expected,
                          'row_index': index, 'csv_line': index + 2, 'source_row': row,
                          'unit': source_unit, 'factor_to_output': factor,
                          'unit_source_ids': request['unit_source_ids'], 'unit_basis': request['unit_basis'],
                          'recognition_basis': basis, 'recognition_source_ids': src}
            action = decision.get('action')
            if action in ('exclude', 'unresolved'):
                unreviewed.append({'status': action, 'reason': basis, 'source': provenance})
                continue
            if action != 'include':
                raise ValueError('导入边action须为include/exclude/unresolved')
            edge_id = text(decision.get('edge_id'), 'edge_id')
            if edge_id in seen_ids:
                raise ValueError('导入边edge_id重复')
            seen_ids.add(edge_id)
            kind = decision.get('edge_kind')
            if kind not in KINDS:
                raise ValueError('导入边edge_kind无效')
            def coord(key):
                try:
                    values = [float(row[c]) for c in columns[key]]
                except (ValueError, TypeError):
                    raise ValueError('源CSV坐标无效，须将该行保留未决') from None
                return [finite(v, '源坐标') * factor for v in values]
            a, b = coord('start'), coord('end')
            if math.dist(a, b) <= 0:
                raise ValueError('导入边零长度须保留未决')
            def field(key):
                return row[columns[key]] if key in columns else None
            system = field('system') or None
            space = field('space_id')
            items.append({'edge_id': edge_id, 'parent_segment_id': edge_id, 'part_index': 0,
                          'start': a, 'end': b, 'system': system, 'space_id': space,
                          'floor': field('floor'), 'material': field('material'), 'dn': field('dn'),
                          'handle': field('handle'), 'physical_id': field('physical_id'),
                          'quantity_level': field('quantity_level'), 'edge_kind': kind,
                          'source_ids': src, 'source': provenance, 'quantity_in_main_network': False})
        sources.append({'path': str(path), 'sha256': expected, 'source_id': source_id,
                        'row_count': len(rows), 'disposition_count': len(decisions),
                        'unit': source_unit, 'unit_source_ids': request['unit_source_ids'], 'unit_basis': request['unit_basis']})
    return items, unreviewed, sources


def source_dimension(row, part_index=None):
    """Read actual source coordinate dimensions before topology adds a zero z."""
    geometry = row.get('geometry')
    if isinstance(geometry, dict):
        parts = geometry.get('parts', [])
        parts = [parts[part_index]] if part_index is not None else parts
        positions = []
        for part in parts:
            keys = ('start', 'end') if part.get('kind') == 'line' else ('center', 'u', 'v')
            positions.extend(part.get(key, []) for key in keys)
    else:
        positions = [row.get('start', []), row.get('end', [])]
    dimensions = [len(value) if isinstance(value, (list, tuple)) else 0 for value in positions]
    return (3 if dimensions and all(value == 3 for value in dimensions) else 2), dimensions


def inventory(data, input_path, bridges=(), prepared_segments=None):
    items, unreviewed, sources = imported_inventory(data, input_path)
    originals = {r['segment_id']: r for r in data.get('segments', [])}
    for row in data.get('segments', []):
        parent_id = text(row.get('segment_id'), 'segment_id')
        parts = geometry_parts(row)
        src = evidence(row.get('source_ids'), parent_id + '.source_ids')
        for i, part in enumerate(parts):
            edge_id = 'route:' + parent_id + ':' + str(i)
            if part['kind'] != 'line':
                unreviewed.append({'edge_id': edge_id, 'status': 'unsupported_geometry', 'geometry': part,
                                   'source_ids': src, 'reason': '共线审查只处理直线part；曲线保留原几何与数量'})
                continue
            dimension, source_dimensions = source_dimension(row, i if row.get('geometry') is not None else None)
            items.append({'edge_id': edge_id, 'parent_segment_id': parent_id, 'part_index': i,
                          'start': part['start'][:dimension], 'end': part['end'][:dimension],
                          'system': row['system'], 'space_id': row.get('space_id'), 'floor': row.get('floor'),
                          'material': row.get('material'), 'dn': row.get('dn'), 'physical_id': row.get('physical_id'),
                          'quantity_level': row.get('quantity_level'), 'edge_kind': 'drawn',
                          'dn_seeds': copy.deepcopy(row.get('dn_seeds', [])),
                          'source_ids': src, 'source': {'input_reference': 'OVERLAP-INPUT',
                              'segment_id': parent_id, 'part_index': i, 'source_point_dimensions': source_dimensions},
                          'quantity_in_main_network': True})
    prepared = prepared_segments or {}
    for bridge in bridges:
        a, b = bridge['a'], bridge['b']
        left, right = prepared[a['segment_id']], prepared[b['segment_id']]
        start, end = list(left[a['end']]), list(right[b['end']])
        dimensions = []
        for ref, segment in ((a, left), (b, right)):
            original = originals.get(segment.get('parent_segment_id') or ref['segment_id'], {})
            dimensions.append(source_dimension(original)[0])
        # Topology normalizes 2-D coordinates to z=0 internally. That is not
        # evidence of actual pipe elevation, including bridge-versus-bridge pairs.
        if any(d != 3 for d in dimensions):
            start, end = start[:2], end[:2]
        items.append({'edge_id': 'bridge:' + bridge['bridge_id'], 'parent_segment_id': bridge['bridge_id'],
                      'part_index': 0, 'start': start, 'end': end, 'system': bridge['system'],
                      'space_id': bridge.get('space_id'), 'floor': None, 'material': None, 'dn': None,
                      'physical_id': None, 'quantity_level': bridge.get('quantity_evidence_level') or bridge['evidence_level'],
                      'edge_kind': 'topology_bridge', 'source_ids': sorted(set(left['source_ids'] + right['source_ids'] + [bridge['source_id']])),
                      'source': {'bridge': copy.deepcopy(bridge), 'source_dimensions': dimensions,
                                 'a_source_ids': left['source_ids'], 'b_source_ids': right['source_ids']},
                      'quantity_in_main_network': False})
    ids = [r['edge_id'] for r in items]
    if len(ids) != len(set(ids)):
        raise ValueError('共线审查edge_id重复')
    return sorted(items, key=lambda r: r['edge_id']), unreviewed, sources


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def overlap(a, b, dimension, cfg):
    aa, ae, ba, be = (r[k][:dimension] for r, k in ((a, 'start'), (a, 'end'), (b, 'start'), (b, 'end')))
    la, lb = math.dist(aa, ae), math.dist(ba, be)
    if la <= 1e-12 or lb <= 1e-12:
        return None
    u, v = [x / la for x in sub(ae, aa)], [x / lb for x in sub(be, ba)]
    if abs(dot(u, v)) + 1e-14 < math.cos(math.radians(cfg['angle_tolerance_deg'])):
        return None
    lo, hi = sorted((dot(sub(ba, aa), u), dot(sub(be, aa), u)))
    lo, hi = max(0., lo), min(la, hi)
    if hi - lo <= cfg['min_overlap']:
        return None
    p, q = ([x + u * t for x, u in zip(aa, u)] for t in (lo, hi))
    def across(point):
        delta = sub(point, ba)
        along = dot(delta, v)
        return math.sqrt(max(0., dot(delta, delta) - along * along))
    offset = max(across(p), across(q))
    if offset > cfg['distance_tolerance'] + 1e-12:
        return None
    on_b = sorted((max(0., min(1., dot(sub(p, ba), v) / lb)), max(0., min(1., dot(sub(q, ba), v) / lb))))
    return {'overlap_length': hi - lo, 'dimension': dimension, 'from_point': p, 'to_point': q,
            'distance': offset, 'fractions': {a['edge_id']: [lo / la, hi / la], b['edge_id']: on_b}}


def candidates(items, cfg):
    tol = cfg['distance_tolerance']
    bounds = [(r['edge_id'], [min(r['start'][i], r['end'][i]) - tol for i in range(2)] +
               [max(r['start'][i], r['end'][i]) + tol for i in range(2)]) for r in items]
    index, by_id = BoundsIndex(bounds), {r['edge_id']: r for r in items}
    found = []
    for a in items:
        for key in sorted(index.query_box(index.boxes[a['edge_id']])):
            if key <= a['edge_id']:
                continue
            b = by_id[key]
            spatial = overlap(a, b, 3, cfg) if len(a['start']) == len(b['start']) == 3 else None
            planar = overlap(a, b, 2, cfg)
            geometry = spatial or planar
            if not geometry:
                continue
            reasons = []
            for name in ('system', 'space_id', 'floor', 'material', 'dn'):
                if a.get(name) and b.get(name) and a[name] != b[name]:
                    reasons.append('different_' + name)
            if a.get('physical_id') and b.get('physical_id') and a['physical_id'] != b['physical_id']:
                reasons.append('distinct_physical_objects')
            if not spatial and len(a['start']) == len(b['start']) == 3:
                reasons.append('different_elevation_or_3d_direction')
            elif spatial:
                axis_b = sub(b['end'], b['start'])
                denominator = dot(axis_b, axis_b)
                for p in (spatial['from_point'], spatial['to_point']):
                    t = max(0., min(1., dot(sub(p, b['start']), axis_b) / denominator))
                    z_b = b['start'][2] + t * axis_b[2]
                    if abs(p[2] - z_b) > cfg['elevation_tolerance'] + 1e-12:
                        reasons.append('different_elevation_or_3d_direction')
                        break
            uncertain = []
            if len(a['start']) != 3 or len(b['start']) != 3:
                uncertain.append('plan_only_no_elevation_proof')
            for name in ('system', 'space_id', 'floor'):
                if not a.get(name) or not b.get(name):
                    uncertain.append(name + '_not_proven')
            if a.get('physical_id') != b.get('physical_id') or not a.get('physical_id'):
                uncertain.append('same_physical_object_not_proven')
            found.append({'pair': [a['edge_id'], b['edge_id']], 'category': 'separated' if reasons else 'candidate',
                          'separation_reasons': reasons, 'uncertainties': uncertain, **geometry,
                          'members': [copy.deepcopy(a), copy.deepcopy(b)], 'resolution': {'action': 'pending'},
                          'quantity_included': False})
    for i, row in enumerate(found, 1):
        row['candidate_id'] = f'POV-{i:05d}'
    return found


def merge_spans(spans):
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(hi, out[-1][1]))
        else:
            out.append((lo, hi))
    return out


def resolve(rows, items, data):
    """Disposition proposals never rewrite the input quantity or DN summaries."""
    by_pair = {tuple(row['pair']): row for row in rows}
    seen = set()
    resolutions = config(data).get('resolutions', [])
    if not isinstance(resolutions, list):
        raise ValueError('overlap_review.resolutions须为数组')
    for res in resolutions:
        if not isinstance(res, dict) or not isinstance(res.get('pair'), list) or len(res['pair']) != 2:
            raise ValueError('共线处置pair须引用两个edge_id')
        pair = tuple(sorted(text(v, 'pair.edge_id') for v in res['pair']))
        if pair in seen or pair not in by_pair:
            raise ValueError('共线处置重复或不是本次候选')
        seen.add(pair)
        row, action = by_pair[pair], res.get('action')
        if action not in ('pending', 'keep_both', 'drop_overlap'):
            raise ValueError('共线action须为pending/keep_both/drop_overlap')
        text(res.get('basis'), '共线处置basis')
        evidence(res.get('source_ids'), '共线处置source_ids')
        if action == 'drop_overlap':
            if row['category'] != 'candidate':
                raise ValueError('不同空间/系统/标高/实物的重合不能扣减')
            if res.get('drop_edge_id') not in pair:
                raise ValueError('drop_edge_id须引用本对边')
            same = res.get('same_object_evidence')
            if not isinstance(same, dict):
                raise ValueError('drop_overlap须有同物证据，不按近重合认定同物')
            text(same.get('basis'), '同物basis')
            evidence(same.get('source_ids'), '同物source_ids')
            for member in row['members']:
                if not member.get('space_id') or not member.get('system'):
                    raise ValueError('扣减方案须先明确两段空间和系统')
        row['resolution'] = copy.deepcopy(res)
    cuts = defaultdict(list)
    for row in rows:
        res = row['resolution']
        if res['action'] == 'drop_overlap':
            sid = res['drop_edge_id']
            cuts[sid].append(tuple(row['fractions'][sid]))
    # Preserve the union of every connected overlap group, including transitive
    # dispositions. A surviving third expression may legitimately carry a span.
    conflicts = []
    by_id = {r['edge_id']: r for r in items}
    link = {}
    def root(key):
        while link.setdefault(key, key) != key:
            link[key] = link[link[key]]
            key = link[key]
        return key
    for row in rows:
        if row['category'] != 'candidate':
            continue
        a, b = (root(key) for key in row['pair'])
        link[max(a, b)] = min(a, b)
    groups = defaultdict(list)
    for row in rows:
        if row['category'] == 'candidate':
            groups[root(row['pair'][0])].append(row)
    for group in groups.values():
        ids = sorted({key for r in group for key in r['pair']})
        if not any(cuts[key] for key in ids):
            continue
        def xyz(p):
            return list(p) if len(p) == 3 else [*p, 0.]
        origin, end = xyz(by_id[ids[0]]['start']), xyz(by_id[ids[0]]['end'])
        length = math.dist(origin, end)
        axis = [v / length for v in sub(end, origin)]
        baseline, retained = [], []
        for key in ids:
            row = by_id[key]
            a, b = dot(sub(xyz(row['start']), origin), axis), dot(sub(xyz(row['end']), origin), axis)
            baseline.append(tuple(sorted((a, b))))
            at = 0.
            remaining = []
            for lo, hi in merge_spans(cuts[key]):
                if lo > at:
                    remaining.append((at, lo))
                at = max(at, hi)
            if at < 1.:
                remaining.append((at, 1.))
            retained.extend(tuple(sorted((a + (b - a) * lo, a + (b - a) * hi))) for lo, hi in remaining)
        before = math.fsum(b - a for a, b in merge_spans(baseline))
        after = math.fsum(b - a for a, b in merge_spans(retained))
        if before - after > max(1e-10, before * 1e-12):
            conflicts.extend(r['candidate_id'] for r in group if r['resolution']['action'] == 'drop_overlap')
    if conflicts:
        # Preserve all proposals and all original quantities on any conflict.
        return {'status': 'DISPOSITION_CONFLICT', 'conflict_candidates': conflicts,
                'deductions': [], 'applied_to_main_quantities': False}
    deductions = [{'edge_id': key, 'spans': merge_spans(values),
                   'length': math.fsum(b - a for a, b in merge_spans(values)) * math.dist(by_id[key]['start'], by_id[key]['end']),
                   'edge_kind': by_id[key]['edge_kind'], 'quantity_level': by_id[key].get('quantity_level'),
                   'quantity_in_main_network': by_id[key]['quantity_in_main_network']}
                  for key, values in sorted(cuts.items()) if values]
    return {'status': 'PROPOSED_ONLY', 'conflict_candidates': [], 'deductions': deductions,
            'applied_to_main_quantities': False,
            'meaning': '逐段处置后的独立扣减方案；不改原实画量、断口方案或DN强度；不同边种和数量层级不可混加'}


def review(data, input_path, bridges=(), prepared_segments=None):
    cfg = parameters(data)
    locations = [{'source_id': 'OVERLAP-INPUT', 'path': str(Path(input_path).resolve()), 'sha256': sha256(input_path)}]
    def not_run(status, meaning, missing=None):
        return {'schema': SCHEMA, 'status': status, 'parameters': cfg, 'inventory': None,
                'unreviewed': None, 'sources': None, 'source_paths': locations,
                'pairs': None, 'review_complete': False, 'coverage_complete': False,
                'counts': dict.fromkeys(('reviewed_edges', 'candidate_pairs', 'separated_pairs', 'pending_pairs', 'unreviewed_items')),
                'missing_parameters': missing if missing is not None else cfg['missing_parameters'],
                'deduplication_scenario': None, 'meaning': meaning,
                'business_adoption_allowed': False}
    if not cfg['enabled']:
        return not_run('NOT_ENABLED', '未显式开启共线审查，未搜索；候选和数量尚未计算，不代表零，原量保持不变')
    if data.get('unit') not in UNITS:
        return not_run('UNIT_UNRESOLVED', '源单位未明确，未按默认m或mm搜索；候选和数量未计算，原量保持不变')
    if cfg['missing_parameters']:
        return not_run('PARAMETERS_REQUIRED', '缺少适用参数或来源依据，审查未执行；不使用项目常数作为通用阈值，不报告零候选')
    items, unreviewed, sources = inventory(data, input_path, bridges, prepared_segments)
    if cfg['elevation_mode'] == 'plan_only' and any(len(row['start']) != 2 for row in items):
        return not_run('PARAMETERS_REQUIRED', 'plan_only只适用二维来源；本次含三维边，须明确source_3d及有据Z阈值，未搜索',
                       ['elevation_mode:source_3d', 'parameters.elevation_tolerance_and_basis'])
    rows = candidates(items, cfg)
    scenario = resolve(rows, items, data)
    unresolved = [r for r in rows if r['category'] == 'candidate' and r['resolution']['action'] == 'pending']
    unresolved_sources = [r for r in unreviewed if r['status'] != 'exclude']
    return {'schema': SCHEMA, 'status': 'REVIEW_REQUIRED' if unresolved or unresolved_sources or scenario['conflict_candidates'] else 'REVIEWED',
            'unit': data['unit'], 'parameters': cfg, 'inventory': items, 'unreviewed': unreviewed,
            'sources': sources, 'source_paths': locations,
            'pairs': rows, 'coverage_complete': not unresolved_sources,
            'review_complete': not unresolved_sources and not unresolved and not scenario['conflict_candidates'],
            'deduplication_scenario': scenario,
            'counts': {'reviewed_edges': len(items), 'candidate_pairs': sum(r['category'] == 'candidate' for r in rows),
                       'separated_pairs': sum(r['category'] == 'separated' for r in rows), 'pending_pairs': len(unresolved),
                       'unreviewed_items': len(unresolved_sources)},
            'business_adoption_allowed': False,
            'meaning': '共线仅形成审查候选和独立处置方案，二维、同物、并行实物及来源层级分别保留；不修改主量或DN'}


def markdown(result):
    lines = ['\n## 管网共线重合审查\n', f"状态：{result['status']}；单位：{result['parameters']['unit']}。{result['meaning']}。\n"]
    if result['pairs'] is None:
        lines.append('审查未执行；候选及其数量为空值，不是零。')
        if result.get('missing_parameters'):
            lines.append('待补：' + '、'.join(result['missing_parameters']) + '。')
        return '\n'.join(lines) + '\n'
    for row in result['pairs']:
        members = ' / '.join(row['pair'])
        reasons = ','.join(row['separation_reasons'] + row['uncertainties']) or '几何候选'
        lines.append(f"- {row['candidate_id']}：{members}；重合 {row['overlap_length']:.9g}；{row['category']}；{reasons}；{row['resolution']['action']}。")
    if result['unreviewed']:
        lines.append(f"\n另有 {len(result['unreviewed'])} 项源行处置/不支持几何，详见JSON unreviewed，不能据此报零。")
    scenario = result.get('deduplication_scenario', {})
    if scenario.get('conflict_candidates'):
        lines.append('\n处置冲突：' + ','.join(scenario['conflict_candidates']) + '；扣减方案未应用。')
    return '\n'.join(lines) + '\n'


def write_csv(path, result):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['candidate_id', 'edge_id_a', 'edge_id_b', 'category', 'overlap_length', 'unit',
                         'separation_reasons', 'uncertainties', 'action', 'drop_edge_id', 'source_a', 'source_b'])
        if result['pairs'] is None:
            writer.writerow(['', '', '', result['status'], '', result['parameters']['unit'], '',
                             '未执行；候选与数量未计算；' + '|'.join(result.get('missing_parameters', [])), '', '', '', ''])
            return
        for r in result['pairs']:
            writer.writerow([r['candidate_id'], *r['pair'], r['category'], r['overlap_length'], result['parameters']['unit'],
                             '|'.join(r['separation_reasons']), '|'.join(r['uncertainties']), r['resolution']['action'],
                             r['resolution'].get('drop_edge_id', ''), *[json.dumps(m['source'], ensure_ascii=False) for m in r['members']]])


def main():
    parser = argparse.ArgumentParser(description='审查已识别管网实画段和有源派生边的共线重合，不改原量')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or data.get('schema_version') not in {'qto-pipe-overlap-request/1.0',
                'qto-pipe-network-inference-input/1.0', 'qto-pipe-network-inference-input/1.1', 'qto-pipe-network-inference-input/1.2'}:
            raise ValueError('无效的管网共线审查输入版本')
        outputs = [args.output, args.output.with_suffix('.csv'), args.output.with_suffix('.md')]
        sources = source_paths(data, args.input) + review_source_paths(data, args.input)
        protect_review_outputs(outputs, sources)
        result = review(data, args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        write_csv(outputs[1], result)
        outputs[2].write_text(markdown(result), encoding='utf-8')
        print(json.dumps({'status': result['status'], 'counts': result.get('counts'), 'output': str(args.output.resolve())}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'INPUT_REQUIRED', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
