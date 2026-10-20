"""Produce working physical pipe edges from replayed source lengths.

This is a quantity producer, separate from the earlier comparison receipt.
Physical identity is an explicit unreviewed interpretation; no formal adoption
or installation is performed. Page geometry never supplies diagram length.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import tempfile

from measure_pipe_diagram_lengths import (measure, sha, digest, replay_digest, require, text,
    runtime_dependencies as measurement_dependencies, verify_runtime_dependencies,
    write_new, RESULT as CANDIDATE, BASIS, Drawing, finite)
from receive_pipe_diagram_lengths import load_binding
from qto_cad_geometry import read_dxf_with_diagnostics, entity_geometry, transform_geometry
from qto_cad_source_contract import canonical_scale, verify_source_file, unit_tokens
from pipe_diagram_source_parts import unique_workbench_interval, coordinate_unit_annotation, span_parts

REQUEST = 'qto-pipe-physical-network-production/1.0'
RESULT = 'qto-pipe-physical-network/1.0'
PIPE = Path(__file__).resolve().parent
SHARED = PIPE.parents[1] / '_qto-shared-evidence/scripts'


def dependencies():
    # A deliberately conservative, fixed executable envelope. It covers the
    # inference CLI's branch imports as well as its static transitive imports.
    # These are invalidation dependencies, not a claim every file ran.
    paths = sorted(PIPE.rglob('*.py')) + sorted(SHARED.rglob('*.py'))
    return [{'module': str(p.relative_to(PIPE.parents[2])).replace('\\', '/'),
             'path': str(p.resolve()), 'sha256': sha(p)} for p in paths]


def verify_dependencies(rows):
    require(isinstance(rows, list), '缺少实物管网运行依赖')
    expected = {r['module']: r['sha256'] for r in dependencies()}
    require(len(rows) == len(expected) and
            {r['module']: r.get('sha256') for r in rows} == expected,
            '实物管网producer/consumer或固定依赖包络字节变化，须重建')
    # Historical absolute code paths are diagnostic. The fixed module/SHA map
    # above verifies the code actually executing at the current installation.


def replay_candidate(binding):
    saved = load_binding(binding, '系统图长度候选')
    require(saved.get('schema_version') == CANDIDATE, '须消费源计长候选')
    verify_runtime_dependencies(saved.get('runtime_dependencies'))
    original = load_binding(saved.get('source_request'), '原计长请求')
    rebuilt = measure(original)
    rebuilt['source_request'] = copy.deepcopy(saved['source_request'])
    require(replay_digest(saved) == replay_digest(rebuilt), '候选与原源重放不符')
    return rebuilt


def edge(row, source, origin):
    quantity = row['quantity']
    level = row['quantity_level']
    # Actual source values remain actual source values. Unreviewed physical
    # mapping makes the network working result at most rule-derived.
    if quantity is not None and level == '确认量':
        level = '规则推算量'
    return {'record_id': origin + ':' + row['record_id'],
        'physical_id': row['physical_id'], 'extent_id': row['extent_id'],
        'system': row['system'], 'specification': copy.deepcopy(row['specification']),
        'from_port_id': row['from_port_id'], 'to_port_id': row['to_port_id'],
        'quantity': quantity, 'unit': 'm', 'measurement_basis': BASIS,
        'quantity_level': level, 'source_quantity_level': row['quantity_level'],
        'source_span': copy.deepcopy(row['source_span']), 'drawing': copy.deepcopy(source),
        'origin': origin, 'measurements': copy.deepcopy(row.get('measurements', [])),
        'recognition_basis': copy.deepcopy(row['recognition_basis']),
        'physical_instance_status': row['physical_instance_status'],
        'issues': copy.deepcopy(row['issues']), 'formal_adoption_allowed': False}


def workbench_si_context(original):
    """Rebuild the complete existing T27 contract; never patch its output.

    A CAD-* reference is an evidence identity, not a raw DXF handle. Resolve it
    through the source index and actual assignment ledger after whole-input
    replay. Missing producer fields cannot downgrade this family to legacy.
    """
    family = (any(original.get(k) is not None for k in
                  ('workbench', 'geometric_unit_evidence', 'geometric_unit_consumption'))
              or any('workbench_object_ids' in r or isinstance(r.get('source_instance_path'), str)
                     for r in original.get('segments', [])))
    if not family:
        return None
    from qto_recognition_workbench import verify_workbench_unit_consumption, build_index
    from qto_geometric_unit_evidence import verify_geometric_unit_credential
    verify_workbench_unit_consumption(original, required=True)
    receipt = original.get('geometric_unit_consumption')
    require(isinstance(receipt, dict), '工作台原网缺实际单位消费凭据，不能降级手填源路径')
    recipe = receipt.get('workbench_production')
    require(isinstance(recipe, dict), '工作台原网缺完整生产契约')
    checked = verify_geometric_unit_credential(original.get('geometric_unit_evidence'), original.get('measurement_scope'))
    require(checked.get('physical_unit_basis') == 'source_coordinate_declaration'
            and checked.get('allows_length_measurement') is True,
            '工作台适配须有本scope原坐标SI声明，几何自洽或任意mm文字不能代替')
    scope = original['measurement_scope']
    require(scope.get('layout') == 'Model' and scope == checked['scope'], '工作台单位适用scope不一致')
    b = scope['bounds']
    bounds = [finite(b[k]) for k in ('xmin', 'ymin', 'xmax_exclusive', 'ymax_exclusive')]
    require(recipe['scope_bounds'] == bounds, '工作台局部索引与单位scope边界不一致')
    index = build_index(recipe['source'], layout=recipe['layout'],
        authoritative_source=recipe['authoritative_source'], scope_bounds=recipe['scope_bounds'])
    require(index['index_sha256'] == recipe['index_sha256'], '工作台原索引字节身份不能重建')
    require(recipe['source']['sha256'] == checked['source']['exchange_dxf']['sha256']
            and recipe['authoritative_source']['sha256'] == checked['source']['authoritative_source']['sha256'],
            '工作台源图/权威源与公共SI凭据不同')
    objects = {r['object_id']: r for r in index['objects']}
    require(len(objects) == len(index['objects']), '工作台索引实例身份重复')
    return {'index': index, 'objects': objects, 'recipe': recipe, 'scope': scope, 'bounds': bounds,
        'checked': checked, 'scale': checked['metres_per_drawing_unit']}


def workbench_si_span(context, original, original_segment, item, binding, span, source, bounds):
    """Resolve a current prepared line/curve edge to one actual source part interval."""
    require(binding['drawing']['sha256'] == context['recipe']['source']['sha256'],
            '工作台适配图纸不是公共SI及实际索引的同一交换源')
    unit_bounds = context['bounds']
    require(unit_bounds[0] <= bounds[0] < bounds[2] <= unit_bounds[2]
            and unit_bounds[1] <= bounds[1] < bounds[3] <= unit_bounds[3],
            '管段定位窗越出公共SI原scope，不能套用尺度')
    ids = original_segment.get('workbench_object_ids')
    require(isinstance(ids, list) and len(ids) == 1 and ids[0] in context['objects'],
            '工作台原路段须对应唯一实际索引对象')
    obj = context['objects'][ids[0]]
    path = [h.upper() for h in obj['source_path']]
    require(path and span['path'] == path,
            '源范围不对应工作台重放的完整实际INSERT实例路径')
    links = [r for r in original['workbench']['bindings']
             if r.get('object_id') == ids[0] and r.get('segment_id') == original_segment['segment_id']]
    require(len(links) == 1 and [h.upper() for h in links[0]['source_path']] == path,
            '实际生产赋值台账与原索引实例路径不符')
    parts = (obj.get('geometry') or {}).get('parts', [])
    target = (item.get('geometry') or {}).get('parts', [])
    require(len(target) == 1 and target[0]['kind'] in {'line', 'ellipse_arc'},
            '工作台适配只接当前生产的单个直线或参数曲线派生边')
    scale, output_scale = context['scale'], canonical_scale(original['unit'])
    (part, first, last), endpoints = unique_workbench_interval(parts, target[0], scale, output_scale)
    require(span['part'] == part and math.isclose(span['from_fraction'], first, abs_tol=1e-9, rel_tol=0)
            and math.isclose(span['to_fraction'], last, abs_tol=1e-9, rel_tol=0),
            '所选源part/起终分数与实际生产派生边不符')
    proofs = context['checked'].get('coordinate_unit_evidence', [])
    require(proofs, '当前公共凭据没有独立坐标SI原文')
    selected = binding.get('coordinate_unit_annotation_path')
    if selected is None:
        proof = proofs[0]
        selected = [*proof['source_ref'].get('insert_handles', []), proof['source_ref']['handle']]
    selected = [h.upper() for h in selected]
    proofs = [p for p in proofs if [h.upper() for h in
        [*p['source_ref'].get('insert_handles', []), p['source_ref']['handle']]] == selected]
    require(len(proofs) == 1, '单位来源没有绑定当前公共SI原文实例，任意mm文字不能适配')
    # The public contract locates SI declarations in its verified source frame;
    # the actual route remains confined to the unchanged measurement scope.
    # A unit note need not sit inside a small pipe-only localization window.
    note = coordinate_unit_annotation(source, selected, context['checked']['frame']['bbox'])
    require(note['text'].strip() == proofs[0]['source_text'].strip(), '公共SI原文实例读取内容不一致')
    expected_points = proofs[0]['source_context']['placement_points_wcs']
    actual_points = note.get('placement_points_wcs', [note['location']])
    require(len(actual_points) == len(expected_points) and all(
        math.dist(actual, expected) <= max(1e-9, 32*max(math.ulp(v) for v in actual+expected))
        for actual, expected in zip(actual_points, expected_points)),
        '坐标单位原文的原生实际放置与已回放公共源上下文不一致')
    evidence = {'method': 'replayed_workbench_source_coordinate_length', 'source_unit_note': note,
        'metres_per_source_coordinate': scale, 'source_input_segment': copy.deepcopy(original_segment),
        'source_resolution': {'workbench_object_id': ids[0], 'index_sha256': context['index']['index_sha256'],
            'canonical_source_reference': original_segment.get('source_instance_path'), 'full_instance_path': path,
            'part_index': part, 'from_fraction': first, 'to_fraction': last,
            'parent_segment_id': original_segment['segment_id'], 'replayed_edge_id': item['segment_id'],
            'public_unit_scope': copy.deepcopy(context['scope']), 'physical_unit_basis': 'source_coordinate_declaration',
            'unit_credential_fingerprint': digest(original['geometric_unit_evidence']),
            'recognition_ledger': copy.deepcopy(context['recipe']['recognition_ledger']),
            'whole_workbench_input_replayed': True}}
    return note, scale, endpoints, evidence


def exact_parent_ranges(items, part_count):
    """Prove coverage before affine mapping can alias distinct float cuts.

    Items are in ascending original parent order. Only after exact adjacency
    is established may internal cuts be removed from the measured set.
    """
    ranges = []
    for item in items:
        loc = item.get('parent_geometry_interval')
        require(isinstance(loc, dict) and set(loc) == {'part', 'from_fraction', 'to_fraction'},
                '原派生边缺可重放父参数范围，须从当前原输入重新生产')
        index, a, b = loc['part'], loc['from_fraction'], loc['to_fraction']
        require(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < part_count,
                '原派生边父part无效')
        require(all(isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
                    for t in (a, b)) and 0 <= a < b <= 1, '原派生边父参数无效')
        if ranges:
            pi, pa, pb = ranges[-1]
            if pi == index:
                require(pb == a, '原父part参数存在缺口、重叠或倒序，不能经源映射舍入吞并')
                ranges[-1] = (index, pa, b)
                continue
            require(index == pi+1 and pb == 1 and a == 0,
                    '原父part边界不连续或漏part，不能经源映射补齐')
        ranges.append((index, a, b))
    return ranges


def grouped_source_span(context, original, parent, items, binding, span, source, bounds, saved):
    """A bounded route is a continuous group of actual derived parent edges.

    Parent parameter provenance comes from the freshly replayed topology, never
    from a caller-supplied saved geometry or a nearest/equal-length match.
    """
    require('source_parts' in span, 'segment_ids组必须使用有向原生route_interval')
    sid = parent['segment_id']
    require(all(r.get('parent_segment_id') == sid for r in items), '有向组必须属于同一真实生产父路由')
    all_ids = saved['route_preparation']['parent_map'][sid]
    ids = [r['segment_id'] for r in items]
    positions = [all_ids.index(ident) for ident in ids]
    forward = len(positions) == 1 or positions[1] > positions[0]
    step = 1 if forward else -1
    require(positions == list(range(positions[0], positions[0]+step*len(positions), step)),
            '有向组不连续、漏中间边或重复边，不能伪装完整路由')
    if context:
        object_ids = parent.get('workbench_object_ids')
        require(isinstance(object_ids, list) and len(object_ids) == 1
                and object_ids[0] in context['objects'], '父路由缺真实工作台对象')
        obj = context['objects'][object_ids[0]]
        path = [h.upper() for h in obj['source_path']]
        native_parts = (obj.get('geometry') or {}).get('parts', [])
        require(native_parts, '工作台父对象缺完整源路线')
        interval = dict(from_part=0, from_fraction=0., to_part=len(native_parts)-1, to_fraction=1.)
    else:
        path, interval = parent.get('source_instance_path'), parent.get('source_route_interval')
        require(isinstance(path, list) and isinstance(interval, dict),
                '旧父路由缺完整source_instance_path/source_route_interval，不能靠调用者几何替代')
        source_binding = parent.get('source_drawing')
        require(isinstance(source_binding, dict), '旧父路由缺原输入绑定的source_drawing路径/SHA')
        verify_source_file(source_binding, '旧父路由原输入绑定图纸')
        require(source_binding == binding['drawing'], '调用者图纸与旧父路由原输入源绑定不符')
    require(path == span['path'], '有向组完整源实例路径与实际父路由不符')
    entity, matrix = source.resolve(path)
    full_geometry = entity_geometry(entity).get('geometry')
    require(full_geometry is not None, '原生父路线缺完整几何')
    full_parts = transform_geometry(full_geometry, matrix)['parts']
    require(full_parts and math.dist(full_parts[0].get('start', [0., 0., 0.]),
                full_parts[-1].get('end', [0., 0., 0.])) > 1e-8,
            '闭合原生父路线不进入当前有向组适配')
    whole = source.span(dict(path=path, route_interval=interval), bounds)
    native = whole['source_parts']
    if len(items) == 1:
        # A single derived edge has no ordering pair; its source traversal
        # supplies the direction rather than forcing the parent direction.
        forward = span['direction'] == whole['direction']
    raw_parts = (parent.get('geometry') or {}).get('parts', [])
    require(len(raw_parts) == len(native), '父路由完整几何与原源part数不符')
    if context:
        # Reuse the unchanged public SI and ledger replay, including selected
        # TEXT/ATTRIB carrier placement. Group geometry is checked below too.
        first_item = items[0]
        p = first_item['parent_geometry_interval']
        leg = source.span(dict(path=path, part=p['part'], from_fraction=p['from_fraction'],
                               to_fraction=p['to_fraction']), bounds)
        note, scale, _, evidence = workbench_si_span(context, original, parent, first_item,
            binding, leg, source, bounds)
    else:
        note = source.annotation(binding.get('coordinate_unit_annotation_path'), bounds)
        tokens = unit_tokens(note['text'])
        require(('绘图坐标单位' in note['text'] or 'drawing coordinate unit' in note['text'].lower())
                and len(tokens) == 1, '原管网源坐标单位须有明确适用声明')
        scale = next(iter(tokens))
        evidence = {'method': 'source_coordinate_length', 'source_unit_note': note,
            'metres_per_source_coordinate': scale, 'source_input_segment': copy.deepcopy(parent)}
    output_scale = canonical_scale(original['unit'])
    for raw_part, native_part in zip(raw_parts, native):
        require(raw_part.get('kind') == 'line', '有向旧网组只接真实直线路由')
        expected = [[v*scale for v in point] for point in native_part['page_endpoints']]
        actual = [[finite(v)*output_scale for v in raw_part[key]] for key in ('start', 'end')]
        require(all(math.dist(a, b) <= 1e-8 for a, b in zip(actual, expected)),
                '完整父路由几何与当前源part/方向/单位不符')
    # Original endpoint fields are also part of the route, not optional aliases
    # which could point at another physical port with the same total length.
    for key, point in zip(('start', 'end'), whole['page_endpoints']):
        raw_point = parent.get(key)
        require(isinstance(raw_point, list) and len(raw_point) in (2, 3), '父路由缺实际起终端点')
        actual = [finite(v)*output_scale for v in raw_point]+([0.] if len(raw_point) == 2 else [])
        require(math.dist(actual, [v*scale for v in point]) <= 1e-8, '父路由端口与完整原源起终不符')
    expected = []
    ordered = items if forward else items[::-1]
    for index, first_parameter, last_parameter in exact_parent_ranges(ordered, len(native)):
        leg = native[index]
        a, b = Fraction(leg['from_fraction']), Fraction(leg['to_fraction'])
        def mapped(t):
            t = Fraction(t)
            return a+(b-a)*t if leg['direction'] == 1 else b-(b-a)*t
        first, last = mapped(first_parameter), mapped(last_parameter)
        expected.append((leg['part'], min(first, last), max(first, last), leg['direction']))
    if not forward:
        expected = [(i, a, b, -d) for i, a, b, d in expected[::-1]]
    # Merge only adjacent exact source intervals within a native part. A gap,
    # including one ULP, cannot disappear under a geometric tolerance.
    merged = []
    for i, a, b, direction in expected:
        if merged and merged[-1][0] == i and merged[-1][3] == direction:
            pi, pa, pb, pd = merged[-1]
            require((pb == a if direction == 1 else pa == b), '派生组源参数有缺口或重叠')
            merged[-1] = (i, min(pa, a), max(pb, b), pd)
        else:
            merged.append((i, a, b, direction))
    actual = [(p['part'], p['from_fraction'], p['to_fraction'], p['direction']) for p in span['source_parts']]
    # Fraction-to-float equality is exact. An outer mapped boundary which the
    # float source contract cannot represent is refused, never rounded into
    # another range. Whole-parent and exactly representable partial ranges
    # remain compatible with the existing source_span contract.
    require(actual == merged, '有向组源part/参数/方向与实际派生链的精确映射不符；不能舍入合并范围')
    dns = {r.get('dn') for r in items}
    require(len({r['system'] for r in items}) == 1 and len(dns-{None}) <= 1,
            '连续组跨系统或已知DN，须按实际边分别映射')
    levels = {r.get('quantity_level', '未决量') for r in items}
    level = '未决量' if '未决量' in levels else '方案暂估量' if '方案暂估量' in levels else '规则推算量' if '规则推算量' in levels else '确认量'
    evidence['source_resolution'] = {**evidence.get('source_resolution', {}),
        'parent_segment_id': sid, 'replayed_segment_ids': ids,
        'parent_geometry_intervals': [copy.deepcopy(r['parent_geometry_interval']) for r in items],
        'full_instance_path': path, 'source_parts': copy.deepcopy(span['source_parts']),
        'whole_parent_source_geometry_replayed': True}
    combined = {**items[0], 'segment_id': 'group:'+digest(ids)[:24],
        'length': math.fsum(finite(r['length']) for r in items), 'quantity_level': level,
        'dn': None if None in dns else next(iter(dns))}
    return combined, note, scale, [[v*scale for v in p] for p in span['page_endpoints']], evidence


def replay_legacy(data):
    """Replay the existing producer, then source-check each direct length.

    The bounded adapter does not silently absorb legacy bridge/vertical or
    unknown identity records: they are preserved in the original result, and
    prevent a complete merged total until a dedicated source adapter exists.
    """
    original = load_binding(data.get('network_input'), '原管网输入')
    saved = load_binding(data.get('network_result'), '原管网结果')
    require(saved.get('evidence_version') == 'qto-pipe-network-inference-evidence/1.2',
            '只接原始1.2管网，不重复接收合并结果')
    require(saved.get('network_id') == data['network_id'] == original.get('network_id'), '管网身份不符')
    with tempfile.TemporaryDirectory(prefix='qto-network-replay-') as directory:
        path = Path(directory) / 'replayed.json'
        run = subprocess.run([sys.executable, '-B', str(PIPE/'infer_pipe_network.py'),
            '--input', data['network_input']['path'], '--output', str(path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
        require(run.returncode == 0, '原管网producer重放失败: ' + run.stderr)
        rebuilt = json.loads(path.read_text(encoding='utf-8-sig'))
    require(digest(rebuilt) == digest(saved), '原管网结果与固定producer原输入重放不符')
    workbench = workbench_si_context(original)
    mapping = data.get('base_extent_bindings')
    require(isinstance(mapping, list), '原管网须有逐实物源区间映射')
    saved_rows = {r['segment_id']: r for r in saved['segments']}
    mapped_ids = []
    for binding in mapping:
        group = binding.get('segment_ids')
        require(('segment_ids' in binding) != ('segment_id' in binding), '映射须在segment_id与segment_ids中恰选一种')
        ids = group if group is not None else [text(binding.get('segment_id'), 'segment_id')]
        require(isinstance(ids, list) and ids and all(isinstance(i, str) and i in saved_rows for i in ids),
                '映射组缺实际原边')
        mapped_ids.extend(ids)
    require(len(mapped_ids) == len(set(mapped_ids)) and set(mapped_ids) == set(saved_rows),
            '原管网实物映射差集不守恒或同边重复计量')
    raw = {r['segment_id']: r for r in original['segments']}
    rows = []
    for binding in mapping:
        items = [saved_rows[i] for i in binding.get('segment_ids', [binding.get('segment_id')])]
        item = items[0]
        drawing = binding.get('drawing')
        verify_source_file(drawing, '原管网实物DXF')
        require(Path(drawing['path']).is_absolute(), '原管网DXF须为绝对路径')
        doc, diagnostics = read_dxf_with_diagnostics(drawing['path'])
        require(not diagnostics['source_identity_issues'], '原管网DXF有源身份冲突诊断')
        source = Drawing(doc)
        bounds = binding.get('bounds')
        require(isinstance(bounds, list) and len(bounds) == 4, '原管网映射须有定位窗')
        bounds = [finite(v) for v in bounds]
        require(bounds[0] < bounds[2] and bounds[1] < bounds[3], '原管网定位窗退化')
        span = source.span(binding.get('source_span'), bounds)
        grouped = 'segment_ids' in binding
        require(grouped or 'source_parts' not in span, '有向多part须以segment_ids绑定实际连续派生链')
        parent = item.get('parent_segment_id', item['segment_id']) if workbench or grouped else item['segment_id']
        require(parent in raw, '原网络派生边缺少实际生产父路线')
        original_segment = raw[parent]
        if grouped:
            item, note, scale, coordinates, evidence = grouped_source_span(workbench, original,
                original_segment, items, binding, span, source, bounds, saved)
        elif workbench:
            note, scale, coordinates, evidence = workbench_si_span(
                workbench, original, original_segment, item, binding, span, source, bounds)
        else:
            require(original_segment.get('source_instance_path') == span['path'],
                    '原管网完整实例路径与源映射不符；不能靠同长认定实物')
            require((original_segment.get('source_part_index'), original_segment.get('source_from_fraction'),
                     original_segment.get('source_to_fraction')) ==
                    (span['part'], span['from_fraction'], span['to_fraction']),
                    '原管网源part/起终分数与源映射不符或缺失')
            note = source.annotation(binding.get('coordinate_unit_annotation_path'), bounds)
            tokens = unit_tokens(note['text'])
            require(('绘图坐标单位' in note['text'] or 'drawing coordinate unit' in note['text'].lower())
                    and len(tokens) == 1, '原管网源坐标单位须有明确适用声明')
            scale = next(iter(tokens)); old_scale = canonical_scale(original['unit'])
            coordinates = []
            for name in ('start', 'end'):
                point = original_segment.get(name)
                require(isinstance(point, list) and len(point) in {2, 3}, '原管网端点不完整')
                coordinates.append([finite(v)*old_scale for v in point] + ([0.] if len(point) == 2 else []))
            evidence = {'method': 'source_coordinate_length', 'source_unit_note': note,
                'metres_per_source_coordinate': scale, 'source_input_segment': copy.deepcopy(original_segment)}
        source_points = [[v*scale for v in point] for point in span['page_endpoints']]
        require(any(all(math.dist(a, b) <= 1e-8 for a, b in zip(coordinates, order))
                    for order in (source_points, source_points[::-1])),
                '原管网起终端点不对应同一源part物理区间')
        value = finite(item['length']) * canonical_scale(saved['unit'])
        require(math.isclose(value, span['page_coordinate_length']*scale, rel_tol=1e-9, abs_tol=1e-9),
                '原管网记录长度与完整源区间物理坐标长度不符')
        physical = {k: text(binding.get(k), k) for k in
                    ('physical_id', 'extent_id', 'from_port_id', 'to_port_id')}
        require(physical['from_port_id'] != physical['to_port_id'], '实际端口不能相同')
        recognition = binding.get('recognition_basis')
        require(isinstance(recognition, dict) and recognition.get('kind') == 'model_working_interpretation'
                and recognition.get('review_status') == 'unreviewed', '原管网映射须披露未复核模型身份')
        text(recognition.get('recognized_by'), '识别者')
        text(binding.get('basis'), '原管网实物对应依据')
        spec = binding.get('specification')
        require(isinstance(spec, dict) and spec.get('dn') == item.get('dn'), '原管网完整规格与DN不符')
        row = {**physical, 'record_id': item['segment_id'], 'system': item['system'],
            'specification': spec, 'quantity': value, 'quantity_level': item.get('quantity_level', '未决量'),
            'source_span': span, 'recognition_basis': recognition, 'physical_instance_status': 'actual',
            'issues': [], 'measurements': [{**evidence, 'replayed_edge_length_m': value}]}
        # A geometric working value cannot turn unknown DN into a determined spec.
        if row['quantity_level'] == '未决量':
            row['quantity'] = None
            row['issues'].append('原管网数量层级/规格未决，长度事实不升级规格采用资格')
        rows.append(edge(row, drawing, 'base'))
        verify_source_file(drawing, '适配后原管网源DXF')
    for binding in (data['network_input'], data['network_result']):
        verify_source_file(binding, '重放后原管网字节')
    extra = ([r for r in saved.get('bridge_quantity_items', []) if r.get('included_in_reconstructed')]
             + [r for r in saved.get('vertical_quantities', []) if r.get('counted_elsewhere') is not True])
    return rows, {'network_input': data['network_input'], 'network_result': data['network_result'],
        'producer': 'infer_pipe_network.py', 'status': 'REPLAYED_FROM_ORIGINAL_INPUT',
        'adapter_mode': 'replayed_workbench_physical_SI' if workbench else 'explicit_source_part_mapping',
        'result_fingerprint': digest(rebuilt), 'preserved_network': saved,
        'unadapted_length_records': extra}


def produce(data):
    require(data.get('schema_version') == REQUEST, '实物管网生产契约不符')
    runtime = dependencies()
    network = text(data.get('network_id'), 'network_id')
    text(data.get('scope_id'), 'scope_id'); text(data.get('scope_basis'), 'scope_basis')
    bindings = data.get('candidate_results')
    require(isinstance(bindings, list) and bindings, '须逐包绑定源计长候选')
    require(len({(r['path'], r['sha256']) for r in bindings}) == len(bindings), '候选包重复')
    rows, replays = [], []
    for index, binding in enumerate(bindings):
        rebuilt = replay_candidate(binding)
        require(rebuilt['scope_id'] == data['scope_id'], '源计长范围与生产范围不一致')
        source = rebuilt['source_bindings'][0]
        rows += [edge(row, source, 'diagram'+str(index)) for row in rebuilt['segments']]
        replays.append({'candidate': binding, 'source_request': rebuilt['source_request'],
            'input_fingerprint': rebuilt['input_fingerprint'], 'status': 'REPLAYED_FROM_SOURCE'})
    legacy = None
    if data.get('network_result') is not None or data.get('network_input') is not None:
        base, legacy = replay_legacy(data)
        rows = base + rows
    # The producer intentionally retains all alternative representations.
    # The consumer owns the sole deduplicated length outlet.
    for binding in bindings:
        verify_source_file(binding, '生产后候选字节')
    require(runtime == dependencies(), '生产期间运行依赖字节变化，须重新执行')
    return {'schema_version': RESULT, 'network_id': network, 'scope_id': data['scope_id'],
        'scope_basis': data['scope_basis'], 'input_fingerprint': digest(data),
        'runtime_dependencies': runtime, 'source_replays': replays,
        'legacy_replay': legacy, 'physical_edges': rows,
        'network_quantity_produced': True, 'quantity_outlet': 'consumer_required',
        'business_adoption_allowed': False, 'scope_complete': False,
        'recognition_status': 'model_working_interpretation_unreviewed'}


def main():
    parser = argparse.ArgumentParser(description='回源生产管网实物长度边，保留采用层级与全部未决')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        request_binding = {'path': str(args.input.resolve()), 'sha256': sha(args.input)}
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        result = produce(data)
        verify_source_file(request_binding, '生产后原请求')
        result['source_request'] = request_binding
        write_new(args.output, result, [args.input] + [r['path'] for r in result['runtime_dependencies']])
        print(json.dumps({'status': 'WORKING_NETWORK_PRODUCED', 'edges': len(result['physical_edges']),
                          'business_adoption_allowed': False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
