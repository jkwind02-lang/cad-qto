"""Evidence adapter for the optional electrical candidate extractors, not quantity adoption."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import trace_drawn_routes as routes
import qto_cad_source_contract as source_contract

geometry = routes.cad_geometry
graph = routes.segment_graph


def finite(value, label, *, positive=False):
    value = routes.finite(value, label)
    if value < 0 or (positive and value == 0):
        raise ValueError(label + ' 须为正数' if positive else label + ' 须非负')
    return value


def required(value, label):
    return routes.text(value, label)


def identity_rows(values, label, key='id'):
    if not isinstance(values, list):
        raise ValueError(label + ' 须为数组')
    result = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(label + ' 行须为对象')
        ident = required(value.get(key), label + '.' + key)
        if ident in result:
            raise ValueError(label + ' 重复身份: ' + ident)
        result[ident] = value
    return result


def bounds(raw, label):
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(label + ' 须为米制[xmin,ymin,xmax,ymax]')
    values = [routes.finite(x, label) for x in raw]
    if values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError(label + ' 边界顺序无效')
    return values


def contains(point, box):
    return box[0] <= point[0] < box[2] and box[1] <= point[1] < box[3]


def text_value(row):
    return str(row.get('text') or row.get('plain_text') or '').replace('\\P', '\n')


def bound_result(raw, schema):
    if not isinstance(raw, dict):
        raise ValueError('缺绑定的候选来源')
    path = Path(required(raw.get('path'), '候选来源.path'))
    if not path.is_absolute():
        raise ValueError('候选来源须为明确绝对路径')
    digest = required(raw.get('sha256'), '候选来源.sha256').lower()
    if routes.sha256(path) != digest:
        raise ValueError('候选来源指纹不一致')
    value = routes.load_json(path)
    if value.get('schema') != schema or not value.get('provenance', {}).get('stable'):
        raise ValueError('候选来源契约/稳定来源无效')
    for source, expected in value['provenance']['fingerprints'].items():
        if routes.sha256(Path(source)) != expected:
            raise ValueError('候选上游中性来源已改变')
    return value


class Evidence:
    def __init__(self, manifest_path, request):
        self.path = Path(manifest_path)
        layout_path, block_path, self.manifest = routes.source_from_manifest(self.path)
        unit=request.get('unit',{})
        self.coordinate_only=unit.get('mode')=='cad_coordinates'
        if self.coordinate_only and request.get('schema')not in ('qto-design-clause-request/1.0','qto-electrical-circuit-request/1.0'):
            raise ValueError('桥架量测/寻路不能使用无工程单位的CAD坐标模式')
        self.factor = 1.0 if self.coordinate_only else finite(unit.get('factor_to_m'), 'unit.factor_to_m', positive=True)
        self.unit_basis = required(request.get('unit', {}).get('basis'), 'unit.basis')
        self.layout = required(request.get('layout', 'Model'), 'layout')
        self.before = {str(p): routes.sha256(p) for p in (self.path, layout_path, block_path)}
        self.dependencies = {}
        # Both the authority and its declared exchange file belong to this
        # evidence snapshot; unchanged streams do not validate a changed DXF.
        for label in ('authoritative_source', 'exchange_dxf'):
            reference = self.manifest.get(label)
            if reference is None:
                continue  # Older direct-source manifests may omit exchange_dxf.
            if not isinstance(reference, dict) or not reference.get('path') or not reference.get('sha256'):
                source_contract.verify_source_file(reference, label)  # Controlled contract error.
            source_path = Path(reference['path'])
            if not source_path.is_absolute():
                source_path = self.path.resolve().parent / source_path
            source_path = source_path.resolve()
            checked = dict(reference, path=str(source_path))
            source_contract.verify_source_file(checked, label)
            self.before[str(source_path)] = str(reference['sha256']).lower()
        definitions = defaultdict(list)
        for row in routes.load_stream(block_path):
            if row.get('row_type') == 'entity':
                definitions[str(row.get('parent_block') or '')].append(row)
        origins = {str(x['block_name']): x.get('origin', [0, 0, 0]) for x in self.manifest.get('blocks', [])}
        expand = set(request.get('expand_blocks', []))
        self.todo, self.rows = [], []
        self.max_depth = int(request.get('max_depth', 16))
        if self.max_depth < 1:
            raise ValueError('max_depth须大于0')

        def take(row, path, matrix=None, inherited=''):
            row = dict(row)
            own = str(row.get('layer') or '')
            if own in ('', '0') and inherited:
                row['layer'] = inherited
            if matrix is not None:
                row = geometry.transform_row(row, matrix, path, self.layout)
                if row.get('entity_type') in ('TEXT', 'MTEXT'):
                    angle = float(row.get('rotation', 0))
                    vector = geometry.transform_vector([math.cos(angle), math.sin(angle), 0], matrix)
                    row['rotation'] = math.atan2(vector[1], vector[0])
            row['source_id'] = path
            self.rows.append(row)

        def walk(row, path, matrix=None, inherited='', stack=()):
            own = str(row.get('layer') or '')
            layer = inherited if own in ('', '0') and inherited else own
            if row.get('entity_type') != 'INSERT':
                take(row, path, matrix, inherited)
                return
            name = str(row.get('block_name') or '')
            take(row, path, matrix, inherited)
            if name not in expand:
                if name not in request.get('opaque_blocks', []):
                    self.todo.append(dict(problem='块未显式展开，覆盖未闭合', source_id=path, block=name))
                return
            if name in stack or len(stack) >= self.max_depth or name not in definitions:
                self.todo.append(dict(problem='块循环/超深/缺定义', source_id=path, block=name))
                return
            child_matrix = geometry.matrix_for_insert(row, origins.get(name, [0, 0, 0]))
            if matrix is not None:
                child_matrix = geometry.compose_matrix(matrix, child_matrix)
            for child in definitions[name]:
                walk(child, path + '/' + name + '/CAD-' + str(child.get('entity_handle')),
                     child_matrix, layer, (*stack, name))

        for row in routes.load_stream(layout_path):
            if row.get('row_type') == 'entity' and row.get('layout') == self.layout:
                walk(row, 'CAD-' + str(row.get('entity_handle')))
        self.byid = {r['source_id']: r for r in self.rows}
        if len(self.byid) != len(self.rows):
            raise ValueError('展开后源实体身份冲突')
        raw_unit=self.manifest.get('drawing_unit',{}).get('raw_unit')
        self.checked_unit_declaration=None
        if not self.coordinate_only:
            declaration=unit.get('declaration',{})
            if source_contract.unit_known(raw_unit):
                source_contract.check_unit_factor(raw_unit,'m',self.factor)
            else:
                raw_unit=required(declaration.get('raw_unit'),'unit.declaration.raw_unit')
                self.refs(declaration.get('source_ids'),'unit.declaration.source_ids')
                strings=[text_value(self.byid[s])for s in declaration['source_ids']]
                source_contract.verify_unit_declaration_texts(strings,raw_unit,declaration.get('source_excerpt'))
                raw_bounds={sid:row.get('bbox')for sid,row in self.byid.items()if isinstance(row.get('bbox'),list)and len(row['bbox'])==4}
                self.checked_unit_declaration=source_contract.verify_unit_corroboration(raw_unit,declaration.get('geometric_corroboration'),raw_bounds)
                source_contract.check_unit_factor(raw_unit,'m',self.factor)
        self.texts = []
        for row in self.rows:
            if row.get('entity_type') in ('TEXT', 'MTEXT') and text_value(row):
                if row.get('x') is None or row.get('y') is None:
                    self.todo.append(dict(problem='文字缺WCS位置', source_id=row['source_id']))
                    continue
                self.texts.append(dict(source_id=row['source_id'], text=text_value(row),
                    point_m=[routes.finite(row[x], x) * self.factor for x in ('x', 'y')],
                    rotation=row.get('rotation', 0), layer=row.get('layer'),
                    source_handle=row.get('entity_handle')))

    def refs(self, ids, label):
        if not isinstance(ids, list) or not ids:
            raise ValueError(label + ' 必须包含本中性流源身份')
        if any(not isinstance(x, str) or x not in self.byid for x in ids):
            raise ValueError(label + ' 存在无法回读的源身份')
        return ids

    def lines(self, pattern):
        result = []
        for row in self.rows:
            if not re.search(pattern, str(row.get('layer') or '')):
                continue
            if row.get('entity_type') not in graph.CURVE_TYPES | graph.LEADER_TYPES:
                continue
            parts, problem = graph.row_parts(row)
            if problem:
                self.todo.append(dict(problem=problem, source_id=row['source_id']))
                continue
            for i, part in enumerate(parts):
                if part['kind'] != 'line':
                    self.todo.append(dict(problem='曲线需独立桥架/表格解释，未用弦替代', source_id=row['source_id']))
                    continue
                a = [x * self.factor for x in part['start']]
                b = [x * self.factor for x in part['end']]
                result.append(dict(id=row['source_id'] + ':' + str(i), source_id=row['source_id'],
                                   layer=row.get('layer'), start=a, end=b, length_m=math.dist(a, b)))
        return result

    def leader_chains(self, pattern, snap=0.0):
        """Native arrow direction, or unbranched LINE-chain alternatives; never nearest-text propagation."""
        lines=self.lines(pattern)
        native=defaultdict(list);plain=[];out=[]
        for line in lines:
            kind=self.byid[line['source_id']].get('entity_type')
            if kind=='LEADER':native[line['source_id']].append(line)
            elif kind=='LINE':
                a,b=line['start'],line['end']
                plain.append(dict(segment_id=line['id'],source_id=line['source_id'],layer=line['layer'],start=a,end=b,length=line['length_m'],
                    part=dict(kind='line',start=a,end=b),bbox3d=[min(a[i],b[i])for i in range(3)]+[max(a[i],b[i])for i in range(3)]))
        for sid,parts in native.items():
            out.append(dict(target=parts[0]['start'],text_end=parts[-1]['end'],source_ids=[sid],via='native_leader'))
        if plain:
            network=graph.build_graph(plain,snap_tolerance=snap,gap_tolerance=0.0,t_tolerance=snap)
            nodes={n['node_id']:n for n in network['nodes']}
            byseg={s['segment_id']:s for s in plain}
            for comp in network['components']:
                ends=comp['dangling_node_ids']
                member_nodes={n for s in network['segments']if s['segment_id']in comp['segment_ids']for n in (s['start_node'],s['end_node'])}
                if len(ends)!=2 or any(nodes[n]['degree']>2 for n in member_nodes):
                    self.todo.append(dict(problem='LINE引线有分支/闭环，方向不能唯一解释',source_ids=[byseg[s]['source_id']for s in comp['segment_ids']]))
                    continue
                ids=sorted({byseg[s]['source_id']for s in comp['segment_ids']})
                a,b=[nodes[n]['point']for n in ends]
                for target,text_end in ((a,b),(b,a)):
                    out.append(dict(target=target,text_end=text_end,source_ids=ids,via='line_chain_candidate'))
        return out

    def bind(self, raw, schema):
        result = bound_result(raw, schema)
        self.dependencies[raw['path']] = raw['sha256']
        self.dependencies.update(result['provenance']['fingerprints'])
        self.dependencies.update(result['provenance'].get('dependency_fingerprints_before', {}))
        return result

    def provenance(self):
        after = {path: routes.sha256(Path(path)) for path in self.before}
        dependency_after = {path: routes.sha256(Path(path)) for path in self.dependencies}
        if after != self.before or dependency_after != self.dependencies:
            raise ValueError('计算期间中性来源指纹改变，结果不可交付')
        return dict(manifest=str(self.path.resolve()), fingerprints=self.before, fingerprints_after=after,
                    dependency_fingerprints_before=self.dependencies, dependency_fingerprints_after=dependency_after,
                    stable=True, source=self.manifest.get('authoritative_source') or self.manifest.get('source'), unit_factor_to_m=None if self.coordinate_only else self.factor,
                    unit_basis=self.unit_basis, coordinate_unit='cad_unitless'if self.coordinate_only else'm',
                    measured_length_allowed=not self.coordinate_only,checked_unit_declaration=self.checked_unit_declaration)

    def port(self, raw, tolerance):
        """A source-bound connection point, never an insert anchor or outline center by itself."""
        if not isinstance(raw, dict):
            raise ValueError('端口须为结构化来源对象')
        if raw.get('manifest_sha256') != self.before[str(self.path)]:
            raise ValueError('端口原图来源命名空间不匹配，不能按同句柄认同物')
        physical = required(raw.get('physical_source_id'), 'port.physical_source_id')
        port = required(raw.get('port_source_id'), 'port.port_source_id')
        self.refs([physical, port], 'port source identities')
        owner, pointrow = self.byid[physical], self.byid[port]
        if owner.get('entity_type') != 'INSERT' or raw.get('physical_id') != physical:
            raise ValueError('端口physical_id须为当前来源的实际INSERT身份')
        expected_path = owner.get('instance_path') or physical
        if raw.get('instance_path') != expected_path:
            raise ValueError('端口instance_path不匹配实际实例来源')
        if not port.startswith(physical + '/'):
            raise ValueError('连接口未从该物理实例的块内取得，不能借用相邻实例同名端口')
        mode = raw.get('locator')
        if mode == 'point' and pointrow.get('entity_type') == 'POINT':
            coordinates = [pointrow.get(x) for x in ('x', 'y', 'z')]
        elif mode in ('line_start', 'line_end') and pointrow.get('entity_type') == 'LINE':
            coordinates = pointrow.get('start' if mode == 'line_start' else 'end')
        else:
            raise ValueError('端口定位只接受实例内POINT或有据连接线端；HATCH/文字/插入点无连接口资格')
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 3:
            raise ValueError('连接口缺完整WCS坐标')
        point = [routes.finite(v, 'port WCS coordinate') * self.factor for v in coordinates]
        declared = raw.get('point_m')
        if declared is not None:
            if not isinstance(declared, list) or len(declared) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in declared):
                raise ValueError('端口声明坐标无效')
            if math.dist(point, declared) > tolerance:
                raise ValueError('端口坐标不属于声明来源实例')
        required(raw.get('basis'), 'port.connection_basis')
        self.refs(raw.get('basis_source_ids'), 'port.basis_source_ids')
        return point


def finish(evidence, payload, todo):
    todo = [*evidence.todo, *todo]
    return dict(payload, status='REVIEW_REQUIRED' if todo else 'CANDIDATE_ONLY', todo=todo,
                provenance=evidence.provenance(), business_adoption_allowed=False, official_adoption=False)


def cli(calculate, description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--cad-manifest', required=True, type=Path)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError('输出已存在，使用新版本路径')
        request = routes.load_json(args.request)
        coordinate_only=request.get('unit',{}).get('mode')=='cad_coordinates'
        def rename(value,source,target):
            if isinstance(value,list):return[rename(v,source,target)for v in value]
            if isinstance(value,dict):
                return{(k[:-len(source)]+target if k.endswith(source)else k):rename(v,source,target)for k,v in value.items()}
            return value
        if coordinate_only:
            def contains_metric(value):
                if isinstance(value,dict):return any(k.endswith('_m')or contains_metric(v)for k,v in value.items())
                if isinstance(value,list):return any(contains_metric(v)for v in value)
                return False
            if contains_metric(request):raise ValueError('无工程单位的文字取证须使用*_cad坐标字段，不得把其标为米')
            request=rename(request,'_cad','_m')
        evidence = Evidence(args.cad_manifest, request)
        result = calculate(evidence, request)
        if coordinate_only:result=rename(result,'_m','_cad')
        result['request_provenance'] = dict(path=str(args.request.resolve()), sha256=routes.sha256(args.request))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps(dict(status=result['status'], output=str(args.output), todo_count=len(result['todo'])), ensure_ascii=False))
        return 3 if result['todo'] else 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        print(json.dumps(dict(status='INPUT_REQUIRED', error=str(exc)), ensure_ascii=False))
        return 2
