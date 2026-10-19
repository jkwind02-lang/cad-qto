"""Fixed CAD producers for penetration calculations; no request-supplied code runs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
for directory in ('qto-pipe-networks', 'qto-duct-air-systems', 'qto-electrical-routes'):
    sys.path.insert(0, str(SKILLS / directory / 'scripts'))
import qto_cad_geometry as cg
import qto_cad_source_contract as sc
import extract_cad_neutral as extractor
import trace_drawn_routes as trace


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '须为非空文字')
    return value


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '须为有限数值')
    return float(value)


def reject_alias(path):
    """Check the spelling before resolve(), including Windows directory junctions."""
    target = Path(os.path.abspath(path))
    for part in (target, *target.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('路径含symlink/reparse别名: ' + str(part))
    return target


class Sources:
    def __init__(self, manifest_path, request):
        self.protected = {}
        for producer in (__file__,extractor.__file__,cg.__file__):
            self.protect(producer)
        self.manifest_path = self.protect(manifest_path)
        self.manifest = json.loads(self.manifest_path.read_text(encoding='utf-8-sig'))
        if self.manifest.get('schema_version') != 'qto-cad-neutral-evidence/1.0':
            raise ValueError('须使用固定CAD中性取证清单')
        if self.manifest.get('geometry_schema_version') != cg.GEOMETRY_SCHEMA or self.manifest.get('source_identity_issues'):
            raise ValueError('中性几何版本或源实例身份不成立')
        for reference in self.manifest.get('artifacts', {}).values():
            if isinstance(reference, dict) and reference.get('path'):
                self.reference(reference, self.manifest_path.parent)
        self.authoritative = self.reference(self.manifest['authoritative_source'], self.manifest_path.parent)
        self.exchange = self.reference(self.manifest['exchange_dxf'], self.manifest_path.parent)
        self.source_sha256 = self.manifest['authoritative_source']['sha256']
        if request.get('source_sha256') != self.source_sha256 or request.get('cad_manifest_sha256') != digest(self.manifest_path):
            raise ValueError('公共请求未冻结同版原图及中性清单SHA')
        if self.manifest.get('exchange_conversion'):
            self.reference(self.manifest['exchange_conversion'], self.manifest_path.parent)
        # Rebuild selected geometry/text from the real DXF using this plugin's
        # fixed extractor. Stream hashes or a self-declared producer are insufficient.
        self.document, self.read_diagnostics = cg.read_dxf_with_diagnostics(self.exchange)
        if self.read_diagnostics['source_identity_issues']:
            raise ValueError('真实DXF源身份冲突')
        self.layout = text(request.get('layout', 'Model'), 'layout')
        self.rows, self.todo = {}, []
        self.max_depth = request.get('max_depth', 32)
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or not 1 <= self.max_depth <= 64:
            raise ValueError('max_depth须在1至64之间')
        try:
            layout = self.document.layouts.get(self.layout)
        except KeyError as exc:
            raise ValueError('真实源不存在布局: ' + self.layout) from exc

        def walk(entity, path, matrix=None, stack=()):
            try:
                row = extractor.convert(entity, layout=self.layout, parent_block=None,
                                        source_hash=self.source_sha256, raw_unit='raw_DU', sequence=0)
            except (ValueError,TypeError,IndexError,ZeroDivisionError,OverflowError) as exc:
                # A malformed unrelated entity does not erase a supported selected
                # route. Keep its complete identity/error; selecting it stays unresolved.
                row=dict(record_id='CAD-'+str(entity.dxf.handle),entity_handle=str(entity.dxf.handle),
                         entity_type=entity.dxftype(),geometry=None,geometry_issues=[str(exc)],
                         text=extractor.entity_text(entity),layout=self.layout,source_sha256=self.source_sha256)
                self.rows[path]=dict(row,source_id=path)
                self.todo.append(dict(problem='真实源实体几何未恢复',source_id=path,error=str(exc),source_row=row))
                return
            if matrix is not None:
                row = cg.transform_row(row, matrix, path.rsplit('/CAD-', 1)[0], self.layout)
            row['source_id'] = path
            if path in self.rows:
                raise ValueError('原图完整实例身份重复: ' + path)
            self.rows[path] = row
            if entity.dxftype() != 'INSERT':
                return
            name = str(entity.dxf.name)
            counts = {key: entity.dxf.get(key, 1) for key in ('row_count', 'column_count')}
            if any(value != 1 for value in counts.values()):
                # matrix_for_insert is one placement, not a MINSERT expansion.
                # No child path is complete without an actual array-member key.
                self.todo.append(dict(problem='MINSERT阵列成员完整身份及展开尚未支持，不能只取第一放置',
                                      source_id=path, block=name, array_counts=counts, source_row=row))
                return
            if name in stack or len(stack) >= self.max_depth or name not in self.document.blocks:
                self.todo.append(dict(problem='块循环、超深或缺定义', source_id=path, block=name))
                return
            block = self.document.blocks[name]
            raw = extractor.convert(entity, layout=self.layout, parent_block=None,
                                    source_hash=self.source_sha256, raw_unit='raw_DU', sequence=0)
            local = cg.matrix_for_insert(raw, list(block.block.dxf.base_point))
            total = cg.compose_matrix(matrix, local) if matrix is not None else local
            for child in block:
                walk(child, path + '/' + name + '/CAD-' + str(child.dxf.handle), total, (*stack, name))

        for entity in layout:
            walk(entity, 'CAD-' + str(entity.dxf.handle))
        raw_unit = extractor.UNIT_NAMES.get(self.document.units, 'Unitless')
        declared = self.manifest.get('drawing_unit', {}).get('raw_unit')
        if sc.unit_known(raw_unit) != sc.unit_known(declared) or (sc.unit_known(raw_unit) and sc.canonical_scale(raw_unit) != sc.canonical_scale(declared)):
            raise ValueError('中性清单单位与真实DXF单位不同')
        self.factor = sc.canonical_scale(raw_unit) if sc.unit_known(raw_unit) else None
        unit = request.get('unit', {})
        if self.factor is None and unit.get('declaration'):
            declaration = unit['declaration']
            ids = self.refs(declaration.get('source_ids'), 'unit.declaration')
            sc.verify_unit_declaration_texts([self.rows[i].get('text', '') for i in ids],
                                             declaration['raw_unit'], declaration.get('source_excerpt'))
            sc.verify_unit_corroboration(declaration['raw_unit'], declaration.get('geometric_corroboration'),
                                        {i: r.get('bbox') for i, r in self.rows.items()})
            self.factor = sc.canonical_scale(declaration['raw_unit'])
        if unit.get('factor_to_m') is not None and (self.factor is None or number(unit['factor_to_m'], 'factor_to_m') != self.factor):
            raise ValueError('请求单位换算与有源单位不同')
        self.unit = 'm' if self.factor is not None else 'raw_DU'
        self.producer_runs = []

    def protect(self, path):
        path = reject_alias(path)
        if not path.is_file():
            raise ValueError('输入文件不存在: ' + str(path))
        self.protected[str(path)] = digest(path)
        return path

    def reference(self, ref, base):
        if not isinstance(ref, dict):
            raise ValueError('冻结文件引用须含path和sha256')
        path = Path(text(ref.get('path'), 'reference.path'))
        path = self.protect(base / path if not path.is_absolute() else path)
        if digest(path) != ref.get('sha256'):
            raise ValueError('冻结文件SHA变化: ' + str(path))
        return path

    def refs(self, ids, label):
        if not isinstance(ids, list) or not ids or any(i not in self.rows for i in ids):
            raise ValueError(label + '须引用本版真实源完整实例')
        return ids

    def scalar(self, spec, label):
        if not isinstance(spec, dict):
            raise ValueError(label + '须绑定有源数值文字')
        sid = text(spec.get('source_id'), label + '.source_id')
        self.refs([sid], label)
        original = self.rows[sid].get('text')
        if not isinstance(original, str) or original != spec.get('expected_text'):
            raise ValueError(label + '文字快照不符')
        match = re.fullmatch(text(spec.get('pattern'), label + '.pattern'), original)
        if match is None or not {'value','unit'} <= set(match.groupdict()):
            raise ValueError(label + '须由整条原文命名捕获value数值及unit单位')
        value = float(match.group('value'))
        if not math.isfinite(value):
            raise ValueError(label + '有源数值非有限')
        scale = sc.canonical_scale(text(spec.get('unit'), label + '.unit'))
        if scale != sc.canonical_scale(match.group('unit')):
            raise ValueError(label + '声明单位与实际捕获原文单位不同')
        return value * scale

    def multiplicity(self,spec):
        if not isinstance(spec,dict):
            return None
        sid=text(spec.get('source_id'),'multiplicity.source_id');self.refs([sid],'multiplicity')
        original=self.rows[sid].get('text')
        if original!=spec.get('expected_text'):
            raise ValueError('多根物理管实例依据原文变化')
        match=re.fullmatch(text(spec.get('pattern'),'multiplicity.pattern'),original or '')
        if match is None or not re.fullmatch(r'[1-9]\d*',match.groupdict().get('count','')):
            raise ValueError('多根物理管须从完整原文捕获正整数count')
        count=int(match.group('count'));index=spec.get('instance_index')
        if isinstance(index,bool)or not isinstance(index,int)or not 1<=index<=count:
            raise ValueError('多根物理管instance_index越界')
        return dict(source_id=sid,text=original,count=count,index=index)

    def binding(self, raw):
        if any(k in raw for k in ('geometry', 'start', 'end', 'points', 'producer', 'command')):
            raise ValueError('公共源绑定不接收手填几何/任意producer或命令')
        sid = text(raw.get('source_id'), 'binding.source_id')
        # Pipe adapter's CADINST- prefix is an alias of the same complete path.
        sid = sid.removeprefix('CADINST-')
        if '#' in sid:
            sid, index = sid.rsplit('#', 1)
            index = int(index)
            if raw.get('part_index')is not None and raw['part_index']!=index:
                raise ValueError('源键part后缀与part_index冲突')
        else:
            index = raw.get('part_index')
        if sid not in self.rows:
            related=[t for t in self.todo if sid.startswith(str(t.get('source_id'))+'/')]
            if related:
                return [],dict(problem='源块展开不完整，保留被请求的完整实例候选',source_id=sid,source_diagnostics=related)
        self.refs([sid], 'binding')
        row = self.rows[sid]
        if row.get('geometry_issues') or not row.get('geometry'):
            return [], dict(problem='源几何未恢复', source_id=sid, source_row=row)
        parts = row['geometry'].get('parts', [])
        if parts and not any(cg.measure_part(p)[0]>0 for p in parts):
            return [],dict(problem='原对象没有非零长度可用曲线，不能认作零穿越管路',source_id=sid,source_row=row)
        if index is None:
            indices = range(len(parts))
        elif isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(parts):
            raise ValueError('完整实例part_index无效')
        else:
            indices = [index]
        result = []
        interval = raw.get('fraction_range', [0., 1.])
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError('fraction_range须为原曲线参数比例区间')
        lo, hi = [number(v, 'fraction_range') for v in interval]
        if not 0 <= lo < hi <= 1 or (index is None and (lo, hi) != (0, 1)):
            raise ValueError('完整曲线区间无效或多part对象未指定part')
        direction = raw.get('direction', 1)
        if isinstance(direction, bool) or direction not in (-1, 1):
            raise ValueError('direction须为1或-1')
        for i in indices:
            original = parts[i]
            if original.get('kind') not in ('line', 'ellipse_arc'):
                return [], dict(problem='通用曲线形态尚未实现', source_id=sid, part_index=i, source_row=row)
            part = slice_part(original, lo, hi)
            if direction == -1:
                part = reverse_part(part)
            result.append(dict(part=scale_part(part, self.factor or 1.),
                               source=dict(source_sha256=self.source_sha256, source_id=sid,
                                           instance_path=sid, part_index=i, fraction_range=[lo, hi],
                                           direction=direction, layout=self.layout, raw_unit=self.manifest.get('drawing_unit'),
                                           original_geometry=copy.deepcopy(original), geometry_sha256=fingerprint(original)),
                               evidence_layer='drawn', error_bound=0.))
        return result, None

    def import_route(self, raw, base, context=None):
        """Replay only named built-in producers; an imported result never vouches for itself."""
        kind = raw.get('kind')
        if kind == 'duct_profile':
            import build_duct_network_from_neutral as duct
            if context and context['discipline']!='duct':
                raise ValueError('风管生产者不能换名为水管或桥架')
            path = self.reference(raw['profile'], base)
            claims = self.reference(raw['claims'], base) if raw.get('claims') else None
            data, protected = duct.build(self.manifest_path, path, claims)
            for p in protected:
                self.protect(p)
            segments = {s['segment_id']: s for s in data['segments'] + data['drawn_curve_segments']}
            segment = segments[text(raw.get('segment_id'), 'duct.segment_id')]
            if context and any(segment[k]!=context[k]for k in ('space_id','floor_id')):
                raise ValueError('风管上游与公共请求楼层/空间不一致')
            source_claims = []
            for ref in segment['source_geometry_refs']:
                coverage_row = next((r for r in data['coverage']['rows'] if r['source_key'] == ref['source_key']), None)
                parent = (coverage_row or {}).get('parent_source_part')
                interval = (coverage_row or {}).get('source_fraction_range')
                actual, problem = self.binding(dict(source_id=parent or ref['source_key'],
                                                     **({'fraction_range':interval} if interval else {})))
                if problem or not actual:
                    raise ValueError('风管上游原轮廓无法从真实DXF重建')
                # Atomic split outline parts retain an original fraction interval.
                candidate = ref['geometry']['parts'][0]
                if fingerprint(candidate) != fingerprint(actual[0]['source']['original_geometry']):
                    if not interval or not parent:
                        raise ValueError('风管生产者轮廓与真实源不符')
                    rebuilt, problem = self.binding(dict(source_id=parent, fraction_range=interval))
                    if problem or fingerprint(scale_part(candidate, self.factor or 1.)) != fingerprint(rebuilt[0]['part']):
                        raise ValueError('风管分区轮廓未通过原曲线区间重建')
                # Retain the real original-part intervals verified above. The
                # producer's whole reference-list hash cannot prove disjoint use.
                source_claims.extend(copy.deepcopy(p['source']) for p in actual)
            factor = sc.canonical_scale(data['unit']) if sc.unit_known(data['unit']) else 1.
            checked = duct.checked_geometry(segment['geometry'])
            midline = segment['geometry'].get('derived_midline')
            position_error = checked.get('geometry_position_error_bound',
                              checked.get('midline_evidence',{}).get('display_chord_error_bound',0.))
            position_error = number(position_error,'geometry_position_error_bound')
            if position_error<0 or (midline and position_error<=0):
                raise ValueError('衍生显示折线缺固定生产者计算的位置误差界')
            self.protect(duct.__file__)
            for name in ('duct_network_extensions','duct_source_recovery','duct_equidistant_midline'):
                module=sys.modules.get(name)
                if module and getattr(module,'__file__',None):
                    self.protect(module.__file__)
            result = [dict(part=scale_part(p, factor), evidence_layer='derived' if segment['representation'] == 'outline' else 'drawn',
                           error_bound=position_error * factor,
                           source=dict(source_sha256=self.source_sha256, upstream_segment_id=segment['segment_id'],
                                       producer='build_duct_network_from_neutral.py', producer_sha256=digest(duct.__file__),
                                       profile=dict(path=str(path), sha256=digest(path)), source_geometry_refs=segment['source_geometry_refs'],
                                       source_claims=source_claims,
                                       representation=segment['representation'], layout=self.layout,
                                       derived_midline=midline,midline_evidence=checked.get('midline_evidence'),
                                       actual_curve_length=checked.get('length'),length_error_estimate=checked.get('length_error_estimate'),
                                       upstream_coordinate_unit=data['unit'])) for p in segment['geometry']['parts']]
            self.producer_runs.append(dict(kind=kind, producer=str(duct.__file__), sha256=digest(duct.__file__), status='REBUILT'))
            return result
        if kind == 'electrical_request':
            import bind_electrical_objects as electrical
            if context and context['discipline']!='tray':
                raise ValueError('桥架固定生产者必须接桥架物理路径')
            path = self.reference(raw['request'], base)
            request = json.loads(path.read_text(encoding='utf-8-sig'))
            evidence = electrical.Evidence(self.manifest_path, request)
            result = electrical.calculate(evidence, request)
            for p in (*evidence.before, *evidence.dependencies):
                self.protect(p)
            obj = next(o for o in result['objects'] if o['id'] == raw['object_id'])
            original = next(o for o in request['objects'] if o['id'] == raw['object_id'])
            if original['kind'] != 'tray' or not obj['source_steps'] or obj.get('errors'):
                raise ValueError('电气公共穿越入口仅接有源桥架完整路径，不把电缆条数当桥架')
            parts = []
            for step in obj['source_steps']:
                if context and step['floor']!=context['floor_id']:
                    raise ValueError('桥架上游与公共请求楼层不同')
                rebuilt, problem = self.binding({k: step[k] for k in ('source_id', 'part_index', 'fraction_range', 'direction')})
                if problem:
                    raise ValueError('桥架原曲线不能回读')
                parts.extend(rebuilt)
            self.producer_runs.append(dict(kind=kind, producer=str(electrical.__file__), sha256=digest(electrical.__file__), status=result['status']))
            return parts
        if kind == 'pipe_segments':
            if context and context['discipline']!='pipe':
                raise ValueError('管网输入必须接水管物理路径')
            path = self.reference(raw['input'], base)
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if data.get('schema_version') != 'qto-pipe-network-inference-input/1.2' or data.get('source_sha256', self.source_sha256) != self.source_sha256:
                raise ValueError('管网输入契约/来源不符；只消费可按原part重建的实画段')
            segments = {s['segment_id']: s for s in data['segments']}
            parts = []
            for sid in raw['segment_ids']:
                segment = segments[sid]
                if context and segment.get('space_id')is not None and segment['space_id']!=context['space_id']:
                    raise ValueError('管网源段与公共请求空间不同')
                source_id = segment.get('source_instance_path') or segment.get('source_record_id')
                if source_id is None and sid.startswith('SEG-'):
                    source_id = sid[4:]
                if source_id is None:
                    raise ValueError('管网段缺完整源实例身份')
                rebuilt, problem = self.binding(dict(source_id=source_id, part_index=segment.get('source_part_index')))
                if problem:
                    raise ValueError('管网原part不能回读')
                factor = sc.canonical_scale(data['unit']) if sc.unit_known(data.get('unit')) else 1.
                geometry = segment.get('geometry', {}).get('parts')
                if geometry is None:
                    geometry = [dict(kind='line', start=segment['start'], end=segment['end'])]
                if fingerprint([scale_part(p, factor) for p in geometry]) != fingerprint([p['part'] for p in rebuilt]):
                    raise ValueError('管网输入几何不是完整原part；不能把断口桥冒充实画段')
                for part in rebuilt:
                    part['source']['upstream_segment_id'] = sid
                parts.extend(rebuilt)
            self.producer_runs.append(dict(kind=kind, source_input=str(path), sha256=digest(path), status='SOURCE_PARTS_REBUILT'))
            return parts
        raise ValueError('不支持的固定producer种类: ' + str(kind))

    def unchanged(self):
        for path, expected in self.protected.items():
            if digest(path) != expected:
                raise ValueError('计算期间来源已改变: ' + path)


def point(part, t):
    if part['kind'] == 'line':
        return [a + (b-a)*t for a, b in zip(part['start'], part['end'])]
    return cg.conic_point(part, part['start_param'] + part['sweep_param'] * t)


def slice_part(part, lo, hi):
    value = copy.deepcopy(part)
    if part['kind'] == 'line':
        value.update(start=point(part, lo), end=point(part, hi))
    else:
        value.update(start_param=part['start_param'] + part['sweep_param'] * lo,
                     sweep_param=part['sweep_param'] * (hi-lo))
    return value


def reverse_part(part):
    value = copy.deepcopy(part)
    if part['kind'] == 'line':
        value.update(start=part['end'], end=part['start'])
    else:
        value.update(start_param=part['start_param'] + part['sweep_param'], sweep_param=-part['sweep_param'])
    return value


def scale_part(part, factor):
    value = copy.deepcopy(part)
    for key in ('start', 'end', 'center', 'u', 'v'):
        if key in value:
            value[key] = [x * factor for x in value[key]]
    return value
