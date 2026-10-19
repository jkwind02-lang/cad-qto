#!/usr/bin/env python3
"""Bind explicit vertical-height notes to source-bound straight duct centrelines.

Reads the neutral manifest and its unchanged source/artifacts, not a project-side
nearest-neighbour result. The common segment graph preserves full affine curves.
This module supplies working evidence only; no human or business approval.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

SHARED = Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_cad_geometry as geometry
import qto_segment_graph as graph
from qto_cad_source_contract import canonical_scale, unit_known, verify_source_file, verify_unit_corroboration

SCHEMA = 'qto-duct-vertical-annotation-binding/1.0'
HEIGHT = re.compile(r'(?P<subject>风管)?\s*(?P<direction>下接|上接)\s*高度\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|毫米|厘米|米)(?![A-Za-z])', re.I)
OTHER_SUBJECT = re.compile(r'水管|给水管|排水管|雨水管|消防管|喷淋管|冷媒管|制冷剂管|油管|气管|桥架|线槽|电缆|电线|电管|导管|母线|风口|风机|水泵|设备')


def ensure_output_safe(output, protected, *, allow_existing=False):
    """Resolve parent reparse points and compare file identity, including hardlinks."""
    output = Path(output)
    resolved = output.resolve()
    for raw in protected:
        path = Path(raw)
        if resolved == path.resolve():
            raise ValueError('输出不能覆盖输入、来源或证据（含路径别名）')
        if output.exists() and path.exists():
            # Unlike resolve(), samefile identifies two distinct hardlink names.
            if output.samefile(path):
                raise ValueError('输出不能覆盖输入、来源或证据（同文件/硬链接别名）')
    if not allow_existing and (output.exists() or output.is_symlink()):
        raise ValueError('输出不能覆盖已有成果或批注')


def write_protected_json(output, data, protected, *, allow_existing=False):
    """Serialize before writing; replace the directory entry without truncating aliases."""
    output = Path(output)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    ensure_output_safe(output, protected, allow_existing=allow_existing)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=output.parent,
                                         prefix='.qto-write-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        ensure_output_safe(output, protected, allow_existing=allow_existing)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_verified(ref, label):
    verify_source_file(ref, label)
    return Path(ref['path']).resolve()


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '须为非空文本')
    return value.strip()


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(label + '须为非负有限数值')
    return float(value)


def parse_height(raw):
    """Only unqualified full length declarations; never absolute elevations."""
    cleaned = raw.replace('\\P', '\n').strip()
    matches = list(HEIGHT.finditer(cleaned))
    if len(matches) != 1:
        return None, '须有唯一的上接/下接高度和明确长度单位'
    match = matches[0]
    value = float(match['value'])
    if not math.isfinite(value):
        return None, '高度数值须为有限数值'
    fact = {'value': value, 'unit': match['unit'].lower(),
            'length_m': value * canonical_scale(match['unit'].lower()),
            'direction': 'down' if match['direction'] == '下接' else 'up',
            'semantics': 'explicit_vertical_length', 'matched_text': match[0]}
    # Read discipline from the actual source text. A normalized subject/height
    # supplied by a caller cannot override an explicit non-duct subject.
    subjects = re.findall(r'([A-Za-z\u4e00-\u9fff]+管(?:道)?)\s*(?=下接|上接)', cleaned)
    fact['subject'] = '风管' if '风管' in cleaned else None
    fact['source_subjects'] = subjects
    if (OTHER_SUBJECT.search(cleaned) or cleaned.count('风管') > 1
            or any(not s.endswith('风管') and s not in {'管', '管道', '立管'} for s in subjects)):
        return fact, '原文含明确其他专业或多个对象主体，不能作为所指风管的竖向高度'
    # Additional instructions can change a length into an elevation/range/scenario.
    remainder = cleaned[:match.start()] + cleaned[match.end():]
    if (value <= 0 or re.search(r'标高|[~～至±]|约|暂|示意|按现场|不详|另详|不计|不下接|不上接|取消|\d', remainder)
            or re.search(r'标高|约|暂|示意|不详|取消|不下接|不上接|以上|以下|以内|以外|不小于|不大于|大于|小于|至少|至多|[<>≤≥]', cleaned)):
        return fact, '文字含标高、范围、示意、暂定或其他数值，完整竖向长度语义未闭合'
    return fact, None


def _expand(rows, definitions, origins, expand, layout):
    """Same paths and complete INSERT matrices as collect_segments; texts too."""
    out, problems = {}, []

    def take(row, path, matrix, layer, stack):
        if path in out:
            raise ValueError('完整实例来源身份重复: ' + path)
        out[path] = {'row': row, 'matrix': list(matrix) if matrix is not None else list(geometry.IDENTITY), 'layer': layer}
        if row.get('entity_type') != 'INSERT' or row.get('block_name') not in expand:
            return
        name = row['block_name']
        if name in stack or len(stack) >= graph.MAX_DEPTH or name not in definitions:
            problems.append({'source_id': path, 'reason': '展开块缺失、循环或超过深度'}); return
        child = geometry.matrix_for_insert(row, origins.get(name, [0, 0, 0]))
        composed = geometry.compose_matrix(matrix, child) if matrix is not None else child
        for item in definitions[name]:
            own = str(item.get('layer') or '')
            take(item, f"{path}/{name}/CAD-{item['entity_handle']}", composed,
                 layer if own in ('', '0') else own, (*stack, name))

    for row in rows:
        if row.get('layout') == layout:
            take(row, 'CAD-' + nonempty(row.get('entity_handle'), 'entity_handle'), None, str(row.get('layer') or ''), ())
    return out, problems


def bind_annotations(data, config):
    if not isinstance(config, dict) or config.get('schema_version') != SCHEMA:
        raise ValueError('vertical_annotation_input.schema_version 须为 ' + SCHEMA)
    duct_path = read_verified(config.get('duct_input'), '风管输入')
    original = json.loads(duct_path.read_text(encoding='utf-8-sig'))
    if {k: v for k, v in data.items() if k != 'vertical_annotation_input'} != original:
        raise ValueError('接入风管输入与冻结来源不一致；须重新绑定，不接受修改后的手写目标')
    manifest_path = read_verified(config.get('neutral_manifest'), '中性清单')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if manifest.get('schema_version') != 'qto-cad-neutral-evidence/1.0':
        raise ValueError('中性清单契约无效')
    if manifest.get('source_identity_issues'):
        raise ValueError('原始身份冲突须先处理，不能继续绑定')
    source = manifest.get('authoritative_source')
    read_verified(source, '原始CAD来源')
    read_verified(manifest.get('exchange_dxf'), '交换DXF来源')
    artifacts = manifest.get('artifacts') or {}
    layout_path = read_verified(artifacts.get('layout_entities'), '布局流')
    block_path = read_verified(artifacts.get('block_definitions'), '块定义流')
    rows = [json.loads(line) for line in layout_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    block_rows = [json.loads(line) for line in block_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    for row in [*rows, *block_rows]:
        if row.get('source_sha256') != source['sha256']:
            raise ValueError('中性对象与CAD来源SHA不一致: ' + str(row.get('record_id')))
    definitions = {}
    for row in block_rows:
        definitions.setdefault(row.get('parent_block'), []).append(row)
    origins = {b['block_name']: b.get('origin', [0, 0, 0]) for b in manifest.get('blocks', [])}
    layout = nonempty(config.get('layout'), 'layout')
    selected_rows = [r for r in rows if r.get('layout') == layout]
    expand = config.get('expand_blocks', [])
    if not isinstance(expand, list) or any(not isinstance(v, str) or not v for v in expand):
        raise ValueError('expand_blocks须为块名数组')
    instances, expansion_problems = _expand(rows, definitions, origins, expand, layout)
    params = config.get('parameters') or {}
    nonempty(params.get('basis'), 'parameters.basis')
    tolerances = {k: number(params.get(k), 'parameters.' + k) for k in
                  ('snap_tolerance', 'text_tolerance', 'target_tolerance', 'endpoint_tolerance')}
    attachment_policy = config.get('text_attachment_policy', 'terminal')
    if attachment_policy not in ('terminal', 'terminal_or_baseline_shoulder'):
        raise ValueError('text_attachment_policy无效')
    shoulder_angle = 0.
    if attachment_policy == 'terminal_or_baseline_shoulder':
        shoulder_angle = number(params.get('shoulder_angle_tolerance_deg'), 'parameters.shoulder_angle_tolerance_deg')
        if shoulder_angle >= 45:
            raise ValueError('肩线方向容差须小于45度，不得用宽角度模糊识别')
    pattern = re.compile(nonempty(config.get('leader_layer_pattern'), 'leader_layer_pattern'))
    all_curves = graph.collect_segments(selected_rows, definitions, origins, expand_blocks=expand)
    leader_collection = graph.collect_segments(selected_rows, definitions, origins, expand_blocks=expand, layer_pattern=pattern)
    leaders = leader_collection['segments']
    built = graph.build_graph(leaders, snap_tolerance=tolerances['snap_tolerance'], gap_tolerance=0.)
    nodes = {n['node_id']: n for n in built['nodes']}
    lead_by_id = {s['segment_id']: s for s in leaders}
    curves = {(s['source_id'], s['part_index']): s for s in all_curves['segments']}
    evidence = {'neutral_manifest': {'path': str(manifest_path), 'sha256': sha(manifest_path)},
                'duct_input': copy.deepcopy(config['duct_input']),
                'source': copy.deepcopy(source), 'layout_entities': copy.deepcopy(artifacts['layout_entities']),
                'block_definitions': copy.deepcopy(artifacts['block_definitions']),
                'exchange_dxf': copy.deepcopy(manifest['exchange_dxf']), 'layout': layout,
                'coordinate_space': 'drawing_WCS', 'coordinate_unit': None}
    unit_problem = None
    unit_conflict = False
    declared = manifest.get('drawing_unit', {}).get('raw_unit')
    try:
        if not unit_known(declared):
            unit_evidence = config.get('unit_evidence') or {}
            nonempty(unit_evidence.get('basis'), 'unit_evidence.basis')
            declared = nonempty(unit_evidence.get('unit'), 'unit_evidence.unit')
            boxes = {}
            for path, inst in instances.items():
                box = inst['row'].get('bbox3d')
                if box is not None:
                    b = geometry.transform_bounds(box, inst['matrix'])
                    boxes[path] = [b[0], b[1], b[3], b[4]]
            evidence['unit_corroboration'] = verify_unit_corroboration(declared, unit_evidence.get('geometric_corroboration'), boxes)
        if canonical_scale(declared) != canonical_scale(data.get('unit')):
            unit_conflict = True
            raise ValueError('原图与风管输入的平面坐标单位不同；须先转换中心线输入')
        evidence['coordinate_unit'] = declared
    except (TypeError, ValueError, KeyError) as exc:
        unit_problem = str(exc)
    scopes = config.get('scopes')
    if not isinstance(scopes, list) or not scopes:
        raise ValueError('scopes须显式给出同图楼层范围和依据')
    for scope in scopes:
        for key in ('scope_id', 'floor_id', 'basis'):
            nonempty(scope.get(key), 'scopes.' + key)
        box = scope.get('bounds')
        if not isinstance(box, list) or len(box) != 4 or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError('scopes.bounds须为有效原图坐标范围')

    def within(point, scope):
        b = scope['bounds']
        return b[0] <= point[0] < b[2] and b[1] <= point[1] < b[3]

    segment_inputs = {s['segment_id']: s for s in data.get('segments', [])}
    bindings = config.get('segment_bindings')
    if not isinstance(bindings, list) or len(bindings) != len(segment_inputs) or {b.get('segment_id') for b in bindings} != set(segment_inputs):
        raise ValueError('segment_bindings须逐一覆盖本输入全部风管段，不得漏掉可能竞争的对象')
    bound, binding_problems = [], []
    for binding in bindings:
        sid = binding['segment_id']; segment = segment_inputs[sid]
        index = binding.get('part_index')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError('part_index须为非负整数')
        path = nonempty(binding.get('source_id'), sid + '.source_id')
        curve = curves.get((path, index))
        problem = None
        if curve is None or curve['part']['kind'] != 'line':
            problem = '风管源曲线不存在或不是可接入现有直管消费者的直线'
        elif not instances[path]['row'].get('record_id'):
            problem = '风管源对象缺少record_id，不能只用坐标绑定'
        else:
            ends = [segment.get('start'), segment.get('end')]
            if any(not isinstance(p, list) or len(p) < 2 for p in ends):
                raise ValueError('风管输入端点无效: ' + sid)
            a, b = curve['start'][:2], curve['end'][:2]
            residual = min(max(math.dist(ends[0][:2], a), math.dist(ends[1][:2], b)),
                           max(math.dist(ends[0][:2], b), math.dist(ends[1][:2], a)))
            if residual > tolerances['endpoint_tolerance']:
                problem = '风管输入端点与原始曲线不符，不能仅凭句柄认领'
        if problem:
            binding_problems.append({'segment_id': sid, 'source_id': path, 'reason': problem}); continue
        bound.append({'segment': segment, 'curve': curve, 'instance': instances[path]})
    notes = []
    for path, inst in instances.items():
        row = inst['row']; raw = row.get('text')
        if not isinstance(raw, str) or not re.search(r'(?:下接|上接).*高度', raw) or not pattern.search(inst['layer']):
            continue
        anchor = [row.get('x'), row.get('y'), row.get('z') or 0.]
        point = geometry.transform_point(anchor, inst['matrix']) if all(isinstance(x, (int, float)) and math.isfinite(x) for x in anchor) else None
        height, problem = parse_height(raw)
        rotation = row.get('rotation') or 0.
        baseline = geometry.transform_vector([math.cos(math.radians(rotation)), math.sin(math.radians(rotation)), 0.], inst['matrix'])
        notes.append({'annotation_id': 'DVA-' + hashlib.sha256((source['sha256'] + '/' + path).encode()).hexdigest()[:20],
                      'source_id': path, 'source_record_id': row.get('record_id'), 'instance_path': path,
                      'source_object_id': row.get('entity_handle'), 'source_sha256': source['sha256'],
                      'instance_transform': inst['matrix'], 'coordinate_space': 'drawing_WCS',
                      'text_baseline': baseline,
                      'point': point, 'text': raw, 'height_fact': height, 'parse_problem': problem,
                      'candidate_segment_ids': [], 'length': None, 'evidence_level': '未决', 'status': 'unresolved'})
    # Baseline shoulder mode is explicit: one parallel tail and one nonparallel
    # pointer in the same complete simple chain. It is never arbitrary proximity.
    def parallel(a, b):
        den = math.hypot(*a[:2]) * math.hypot(*b[:2])
        return den > 0 and abs(a[0]*b[1] - a[1]*b[0]) / den <= math.sin(math.radians(shoulder_angle)) + 1e-12

    def similar_plan(matrix):
        x = geometry.transform_vector([1., 0., 0.], matrix)
        y = geometry.transform_vector([0., 1., 0.], matrix)
        nx, ny = math.sqrt(sum(v*v for v in x)), math.sqrt(sum(v*v for v in y))
        return nx > 0 and ny > 0 and abs(nx-ny) <= 1e-9*max(nx,ny) and abs(sum(a*b for a,b in zip(x,y))) <= 1e-9*nx*ny

    matches = {}
    for note in notes:
        options = []
        if note['point'] is not None:
            for comp in built['components']:
                terminals = comp['dangling_node_ids']
                terminal_matches = []
                for terminal in terminals:
                    if math.dist(note['point'], nodes[terminal]['point']) <= tolerances['text_tolerance']:
                        terminal_matches.append((comp, {'kind': 'terminal', 'node_id': terminal}))
                if terminal_matches:
                    options.extend(terminal_matches)
                elif attachment_policy == 'terminal_or_baseline_shoulder' and len(terminals) == 2:
                    shoulders = []
                    for sid in comp['segment_ids']:
                        s = lead_by_id[sid]
                        if s['part']['kind'] != 'line': continue
                        vector = [b-a for a,b in zip(s['start'], s['end'])]
                        d, fraction = graph._project(note['point'], s['part'])
                        if parallel(vector, note['text_baseline']) and d <= tolerances['text_tolerance'] and 0 < fraction < 1:
                            shoulders.append({'segment_id': sid, 'distance': d, 'fraction': fraction})
                    if len(shoulders) == 1:
                        along, away = [], []
                        for terminal in terminals:
                            end = nodes[terminal]['ends'][0]
                            direction = graph._outward(lead_by_id[end['segment_id']]['part'], end['end'])
                            (along if parallel(direction, note['text_baseline']) else away).append(terminal)
                        if len(along) == len(away) == 1:
                            options.append((comp, {'kind': 'baseline_shoulder', 'node_id': along[0], 'pointer_node_id': away[0], **shoulders[0]}))
        matches[note['annotation_id']] = options
    for note in notes:
        options = matches[note['annotation_id']]
        reasons = []
        if note['parse_problem']: reasons.append(note['parse_problem'])
        if not note['source_record_id']: reasons.append('文字源对象缺少record_id')
        if note['point'] is None: reasons.append('文字定位缺失')
        if len(options) != 1:
            reasons.append('文字端没有唯一完整引线组件')
        else:
            comp, attachment = options[0]
            text_terminal = attachment['node_id']
            if attachment['kind'] == 'baseline_shoulder' and not similar_plan(note['instance_transform']):
                reasons.append('非相似实例变换下的肩线文字关联须另核；不按基线方向自动采用')
            note['leader_component_id'] = comp['component_id']
            note['leader_geometry'] = [copy.deepcopy(lead_by_id[i]) for i in comp['segment_ids']]
            for member in note['leader_geometry']:
                inst = instances[member['source_id']]
                member.update(source_record_id=inst['row'].get('record_id'), instance_path=member['source_id'],
                              instance_transform=inst['matrix'], source_sha256=source['sha256'], coordinate_space='drawing_WCS')
                if not member['source_record_id']:
                    reasons.append('引线源对象缺少record_id')
                if attachment['kind'] == 'baseline_shoulder' and not similar_plan(inst['matrix']):
                    reasons.append('非相似引线实例变换下的肩线关联须另核')
            terminal_ids = comp['dangling_node_ids']
            if len(terminal_ids) != 2 or any(nodes[i]['degree'] > 2 for i in comp['node_ids']):
                reasons.append('引线分叉、闭环或没有唯一指向端')
            elif any(other['annotation_id'] != note['annotation_id'] and any(c['component_id'] == comp['component_id'] for c, _ in matches[other['annotation_id']]) for other in notes):
                reasons.append('同一引线关联多条竖向文字')
            else:
                target_node = next(i for i in terminal_ids if i != text_terminal)
                pointer = nodes[target_node]['point']; note['pointer'] = pointer
                note['text_attachment'] = {**attachment, 'point': nodes[text_terminal]['point'],
                                           'distance': attachment.get('distance', math.dist(note['point'], nodes[text_terminal]['point']))}
                # For native LEADER the first vertex is the arrow; a reversed text-side interpretation is invalid.
                native = {s['source_id'] for s in note['leader_geometry'] if s['entity_type'] == 'LEADER'}
                for path in native:
                    first = curves.get((path, 0))
                    if first is None or math.dist(pointer, first['start']) > tolerances['snap_tolerance']:
                        reasons.append('LEADER箭头端与文字推导的指向端冲突')
                ss = [s for s in scopes if within(note['point'], s) and within(pointer, s)]
                if len(ss) != 1:
                    reasons.append('文字及指向端没有唯一同层范围')
                else:
                    scope = ss[0]; note['scope'] = copy.deepcopy(scope)
                    hits = []
                    for item in bound:
                        d, fraction = graph._project(pointer, item['curve']['part'])
                        if d <= tolerances['target_tolerance']:
                            hits.append({**item, 'distance': d, 'fraction': fraction})
                    note['candidate_segment_ids'] = [i['segment']['segment_id'] for i in hits]
                    if len(hits) != 1:
                        reasons.append('引线指向端未命中唯一有据风管；不按最近者裁决')
                    else:
                        hit = hits[0]; segment = hit['segment']
                        note['target'] = {'segment_id': segment['segment_id'], 'source_id': hit['curve']['source_id'],
                                          'source_record_id': hit['instance']['row'].get('record_id'),
                                          'source_object_id': hit['instance']['row'].get('entity_handle'),
                                          'instance_path': hit['curve']['source_id'], 'instance_transform': hit['instance']['matrix'],
                                          'coordinate_space': 'drawing_WCS', 'part': copy.deepcopy(hit['curve']['part']),
                                          'part_index': hit['curve']['part_index'], 'distance': hit['distance'], 'fraction': hit['fraction']}
                        note['segment_id'] = segment['segment_id']
                        note['floor_id'] = segment.get('floor_id'); note['system'] = segment.get('system')
                        if segment.get('floor_id') != scope['floor_id'] or not segment.get('system') or segment.get('system') in ('未知', '未定', '未决') or (scope.get('system') is not None and segment['system'] != scope['system']):
                            reasons.append('所指风管跨楼层/系统或系统未决')
        # Identity, source curve matching and declared tolerances all remain in
        # the same raw drawing space. An unknown physical XY scale cannot erase
        # an independently stated height in metres. A *known conflicting* scale
        # does indicate that these input coordinates may be in different spaces.
        if unit_conflict: reasons.append('平面单位冲突: ' + unit_problem)
        note['dependency_pending'] = {'xy_unit': unit_problem} if unit_problem else {}
        if binding_problems: reasons.append('有输入管段来源绑定未闭合，竞争对象覆盖不完整')
        if expansion_problems or leader_collection['expansion_problems'] or leader_collection['geometry_problems']:
            reasons.append('源曲线/块展开存在缺口，须补读后排除竞争引线')
        note['reasons'] = list(dict.fromkeys(reasons))
        if not reasons:
            note.update(status='bound', evidence_level='规则推算', length=note['height_fact']['length_m'] / canonical_scale(data.get('output_unit', 'm')))
    return {'schema_version': SCHEMA, 'evidence': evidence, 'parameters': copy.deepcopy(params), 'annotations': notes,
            'diagnostics': {'binding_problems': binding_problems, 'expansion_problems': expansion_problems,
                            'geometry_problems': leader_collection['geometry_problems'], 'unsupported': leader_collection['unsupported'],
                            'unit_problem': unit_problem, 'unit_conflict': unit_conflict},
            'business_adoption_allowed': False, 'human_reviewed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='将中性DIM_LEAD/LEADER明确高度引注接入风管推算输入')
    parser.add_argument('--input', type=Path, required=True, help='绑定配置；须含duct_input来源文件及SHA')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.input.read_text(encoding='utf-8-sig'))
        duct_path = read_verified(config.get('duct_input'), '风管输入')
        data = json.loads(duct_path.read_text(encoding='utf-8-sig'))
        bind_annotations(data, config)  # Validate now; inference rechecks source freshness.
        data['vertical_annotation_input'] = config
        protected = {args.input.resolve(), duct_path}
        manifest = json.loads(Path(config['neutral_manifest']['path']).read_text(encoding='utf-8-sig'))
        protected.update(Path(r['path']).resolve() for r in [config['neutral_manifest'], manifest['authoritative_source'], manifest['exchange_dxf'], *manifest['artifacts'].values()] if r.get('path'))
        write_protected_json(args.output, data, protected)
        print(json.dumps({'status': 'REFERENCE_ONLY', 'output': str(args.output)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'INPUT_REQUIRED', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
