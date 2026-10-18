#!/usr/bin/env python3
"""Evidence-bound electrical dedicated-support reference quantities.

This module builds the existing calculation graph; its arithmetic is executed
by recompute_qto_dependencies, never by a second support-specific evaluator.
It does not identify supports, design fixings, or verify load capacity.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

SHARED = Path(__file__).resolve().parents[2] / '_qto-shared-evidence' / 'scripts'
sys.path.insert(0, str(SHARED))
from recompute_qto_dependencies import recalculate, digest

SCHEMA = 'qto-electrical-supports/1.0'
LEVELS = {'确认量', '规则推算量', '方案暂估量', '未决量'}
SCOPE = {'included', 'excluded', 'unknown'}
ORIENTATIONS = {'horizontal', 'vertical', 'inclined'}


def required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '须为非空文字')
    return value


def number(value, label, positive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '须为有限数值')
    if value < 0 or (positive and value == 0) or (integer and int(value) != value):
        raise ValueError(label + '数值范围或整数要求无效')
    return value


def unique(rows, label):
    if not isinstance(rows, list):
        raise ValueError(label + '须为数组')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(label + '成员须为对象')
        ident = required(row.get('id'), label + '.id')
        if ident in result:
            raise ValueError(label + '重复身份:' + ident)
        result[ident] = row
    return result


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('来源定位须为JSON pointer')
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class Builder:
    def __init__(self, document, base_dir):
        if document.get('schema') != SCHEMA:
            raise ValueError('schema须为' + SCHEMA)
        self.document = document
        self.base = Path(base_dir).resolve()
        self.sources = unique(document.get('sources'), 'sources')
        self.contents = {}
        self.verified = []
        for sid, source in self.sources.items():
            if sid.startswith('PARAM:'):
                raise ValueError('sources不得占用PARAM保留身份')
            path = (self.base / required(source.get('path'), sid + '.path')).resolve()
            raw = path.read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            if source.get('sha256') != actual:
                raise ValueError(sid + '来源指纹过期或不匹配')
            self.verified.append(dict(id=sid, path=str(path), sha256=actual, bytes=len(raw)))
            try:
                self.contents[sid] = json.loads(raw.decode('utf-8-sig'))
            except (UnicodeError, ValueError):
                self.contents[sid] = None
        self.graph = dict(schema='qto-calculation-graph/1.0',
                          project_id=required(document.get('project_id'), 'project_id'),
                          sources=copy.deepcopy(list(self.sources.values())), rules=[], nodes=[])
        self.ids = set()
        self.segment_nodes = {}
        self.groups = []
        self.pending = []
        self.sharing_ids = set()

    def refs(self, ids, label):
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or any(i not in self.sources for i in ids):
            raise ValueError(label + '缺少、重复或未登记来源')
        return ids

    def basis(self, obj, label):
        required(obj.get('basis'), label + '.basis')
        return self.refs(obj.get('source_ids'), label + '.source_ids')

    def source(self, ident, content):
        ident = 'PARAM:' + ident
        if ident in self.sources:
            raise ValueError('保留来源身份冲突:' + ident)
        source = dict(id=ident, sha256=digest(content), meaning='本轮显式参数内容，不声明为原图事实')
        self.sources[ident] = source
        self.graph['sources'].append(source)
        return ident

    def rule(self, ident, content):
        rid = 'RULE:' + ident
        self.graph['rules'].append(dict(id=rid, sha256=digest(content), status='candidate', content=content))
        return rid

    def node(self, ident, unit, value=None, source_ids=None, level='确认量', expression=None, variables=None, rule=None):
        if ident in self.ids:
            raise ValueError('节点身份冲突:' + ident)
        self.ids.add(ident)
        node = dict(id=ident, unit=unit, quantity_level=level)
        if expression is None:
            node.update(value=value, source_ids=source_ids)
        else:
            node.update(expression=expression, variables=variables, rule_ids=[rule])
        self.graph['nodes'].append(node)
        return ident

    def measure(self, ident, obj, unit, positive=False, integer=False, unknown=True):
        if obj is None:
            if not unknown:
                raise ValueError(ident + '缺少参数')
            sid = self.source(ident, None)
            return self.node(ident, unit, None, [sid], '未决量')
        if not isinstance(obj, dict) or obj.get('unit') != unit or obj.get('quantity_level') not in LEVELS:
            raise ValueError(ident + '单位或数量层级无效，要求' + unit)
        refs = self.basis(obj, ident)
        value = obj.get('value')
        if value is not None:
            number(value, ident, positive, integer)
        sid = self.source(ident, obj)
        return self.node(ident, unit, value, refs + [sid], obj['quantity_level'] if value is not None else '未决量')

    def calc(self, ident, unit, expression, variables, rule, level='规则推算量'):
        return self.node(ident, unit, level=level, expression=expression, variables=variables, rule=rule)

    def total(self, ident, unit, nodes, rule, level='规则推算量'):
        if not nodes:
            sid = self.source(ident, {'empty_explicit_scope': True})
            return self.node(ident, unit, 0, [sid], level)
        variables = {'n' + str(i): n for i, n in enumerate(nodes)}
        return self.calc(ident, unit, '+'.join(variables), variables, rule, level)

    def build_segments(self):
        segments = unique(self.document.get('segments'), 'segments')
        if not segments:
            raise ValueError('segments不得为空')
        bound = set()
        for sid, seg in segments.items():
            for key in ('system', 'spec', 'physical_id'):
                required(seg.get(key), sid + '.' + key)
            if seg.get('orientation') not in ORIENTATIONS:
                raise ValueError(sid + 'orientation无效')
            up = seg.get('upstream')
            if not isinstance(up, dict):
                raise ValueError(sid + '缺上游对象绑定')
            source_id = up.get('source_id')
            self.refs([source_id], sid + '.upstream')
            source_document = self.contents[source_id]
            if not isinstance(source_document, dict) or source_document.get('schema') not in ('qto-electrical-object-package/1.0', 'qto-electrical-path-result/1.0'):
                raise ValueError(sid + '上游schema须为既有电气对象包或路径复算结果')
            if source_document.get('project_id') not in (None, self.document['project_id']):
                raise ValueError(sid + '上游项目身份不一致')
            key = (source_id, required(up.get('object_pointer'), sid + '.object_pointer'))
            if key in bound:
                raise ValueError('同一上游桥架对象被重复绑定')
            bound.add(key)
            origin = pointer(self.contents[source_id], key[1])
            if not isinstance(origin, dict) or origin.get('id') != seg.get('upstream_id'):
                raise ValueError(sid + '上游对象身份不匹配')
            metadata = origin
            if source_document['schema'] == 'qto-electrical-object-package/1.0' and 'kind' not in metadata:
                matches = [x for x in (source_document.get('quantity_result') or {}).get('items', []) if x.get('id') == origin['id']]
                if len(matches) != 1:
                    raise ValueError(sid + '对象包缺唯一同ID数量来源')
                metadata = matches[0]
            if metadata.get('kind') not in ('tray', 'trunking'):
                raise ValueError(sid + '上游必须为桥架或线槽物理通道')
            for field in ('physical_id', 'system', 'spec'):
                if field in metadata and metadata[field] != seg[field]:
                    raise ValueError(sid + '上游' + field + '不匹配')
            length_field = required(up.get('length_field'), sid + '.length_field')
            if length_field not in ('complete_length_m', 'known_length_m', 'known_path_length_each_m', 'length_m') or length_field not in origin:
                raise ValueError(sid + '上游长度字段无效或缺失')
            if 'unit' in metadata and metadata['unit'] != 'm':
                raise ValueError(sid + '上游单位须为m')
            upstream_length = origin.get(length_field)
            if upstream_length is not None:
                number(upstream_length, sid + '.上游长度')
            length = seg.get('length')
            if not isinstance(length, dict) or length.get('unit') != 'm' or length.get('value') != upstream_length:
                raise ValueError(sid + '长度单位须为m，且数值须与有源上游一致')
            if source_id not in length.get('source_ids', []):
                raise ValueError(sid + '长度来源须包括上游对象包')
            rank = {'未决量': 0, '方案暂估量': 1, '规则推算量': 2, '确认量': 3}
            if metadata.get('quantity_level') in rank and rank.get(length.get('quantity_level'), -1) > rank[metadata['quantity_level']]:
                raise ValueError(sid + '不得提升上游数量层级')
            self.segment_nodes[sid] = self.measure('SEG:' + sid + ':length', length, 'm')
            self.measure('SEG:' + sid + ':width', seg.get('width'), 'm', positive=True)
        return segments

    def build_group(self, gid, group, segments, used_segments, actual_ids, event_ids):
        prefix = 'GROUP:' + gid
        required(group.get('system'), gid + '.system')
        if group.get('working_count_basis') not in ('equivalent', 'rule_integer', 'actual_placements'):
            raise ValueError(gid + '.working_count_basis须显式选择')
        self.basis(group, gid)
        runs = unique(group.get('runs'), gid + '.runs')
        if not runs:
            raise ValueError(gid + '缺少独立支承通道段')
        length_nodes = []
        segment_ids = []
        orientations = set()
        for rid, run in runs.items():
            members = run.get('segment_ids')
            if not isinstance(members, list) or not members or len(members) != len(set(members)) or any(s not in segments for s in members):
                raise ValueError(gid + ':' + rid + '成员身份无效')
            if set(members) & used_segments:
                raise ValueError('桥架段重复分配到支架组')
            used_segments.update(members)
            primary = run.get('length_segment_id')
            if primary not in members:
                raise ValueError(rid + 'length_segment_id不是本段成员')
            if len(members) > 1:
                self.basis(run, rid + '.共架')
                sharing = required(run.get('shared_support_identity'), rid + '.shared_support_identity')
                if sharing in self.sharing_ids:
                    raise ValueError('共架支承身份重复:' + sharing)
                self.sharing_ids.add(sharing)
                lengths = [segments[s]['length']['value'] for s in members]
                if len(set(lengths)) != 1:
                    raise ValueError(rid + '共架成员长度不同，须先按共享支承范围分段')
            if any(segments[s]['system'] != group['system'] for s in members):
                raise ValueError(gid + '多系统综合支架不属于本入口')
            orientations.update(segments[s]['orientation'] for s in members)
            segment_ids.extend(members)
            length_nodes.append(self.segment_nodes[primary])
        if len(orientations) != 1:
            raise ValueError(gid + '水平/竖向/斜向须分组')
        rule = self.rule(gid + ':length', {'formula': 'unique support runs sum; shared members never multiply', 'runs': list(runs.values())})
        length = self.total(prefix + ':length', 'm', length_nodes, rule)
        spacing = self.measure(prefix + ':spacing', group.get('spacing'), 'm', positive=True)
        count_rule = self.rule(gid + ':count', {'formula': 'L/s, explicit rounding, unique net-addition events', 'rounding': group.get('rounding')})
        rounding = group.get('rounding')
        if not isinstance(rounding, dict) or rounding.get('scope') not in ('per_run', 'continuous_group') or rounding.get('method') not in ('ceil', 'ceil_plus_one'):
            raise ValueError(gid + '必须明确取整层级与方法')
        self.basis(rounding, gid + '.rounding')
        if rounding['scope'] == 'continuous_group':
            required(rounding.get('continuous_channel_id'), gid + '.continuous_channel_id')
            if rounding.get('run_sequence') != list(runs):
                raise ValueError(gid + '连续通道必须显式列有序run_sequence')
            specs = {segments[s]['spec'] for s in segment_ids}
            if len(specs) != 1:
                raise ValueError(gid + '连续规格取整不能混合不同规格')
        events = unique(group.get('events'), gid + '.events')
        event_scope = group.get('event_scope')
        if not isinstance(event_scope, dict) or event_scope.get('status') not in ('complete', 'pending', 'not_applicable'):
            raise ValueError(gid + '端部/转角/分支事件范围状态未明确')
        self.basis(event_scope, gid + '.event_scope')
        if event_scope['status'] == 'not_applicable' and events:
            raise ValueError(gid + '事件不适用声明与非空事件冲突')
        net = 0
        for eid, event in events.items():
            phys = required(event.get('physical_support_id'), eid + '.physical_support_id')
            if phys in event_ids:
                raise ValueError('重复端部/转角/分支支架事件身份:' + phys)
            event_ids.add(phys)
            self.basis(event, eid)
            if event.get('kind') not in ('end', 'corner', 'branch', 'equipment', 'anti_sway') or event.get('net_addition') not in (0, 1) or isinstance(event.get('net_addition'), bool):
                raise ValueError(eid + '事件类型或净增无效')
            refs = event.get('run_ids')
            if not isinstance(refs, list) or not refs or any(r not in runs for r in refs) or len(refs) != len(set(refs)):
                raise ValueError(eid + 'run_ids无效')
            if event['net_addition'] == 0:
                required(event.get('included_by'), eid + '.included_by')
            net += event['net_addition']
        event_source = self.source(gid + ':events', {'events': list(events.values()), 'scope': event_scope})
        event_node = self.node(prefix + ':event_increment', '1', None if event_scope['status'] == 'pending' else net, group['source_ids'] + event_scope['source_ids'] + [event_source], '规则推算量')
        equivalent = self.calc(prefix + ':equivalent', '1', 'l/s+e', dict(l=length, s=spacing, e=event_node), count_rule)
        operands = length_nodes if rounding['scope'] == 'per_run' else [length]
        rounded = []
        for i, operand in enumerate(operands):
            expression = 'ceil(l/s)' + ('+1' if rounding['method'] == 'ceil_plus_one' else '')
            rounded.append(self.calc(prefix + ':rounded:' + str(i), '1', expression, dict(l=operand, s=spacing), count_rule))
        base = self.total(prefix + ':rounded_base', '1', rounded, count_rule)
        rule_integer = self.calc(prefix + ':rule_integer', '1', 'n+e', dict(n=base, e=event_node), count_rule)
        placements = group.get('placements')
        placement_source_ids = []
        placement_scope = group.get('placement_scope')
        if placements is not None:
            if not isinstance(placement_scope, dict) or placement_scope.get('status') not in ('complete', 'partial'):
                raise ValueError(gid + '实际布点须明确完整/部分范围')
            self.basis(placement_scope, gid + '.placement_scope')
            unique(placements, gid + '.placements')
            for placement in placements:
                phys = required(placement.get('physical_support_id'), 'placement.physical_support_id')
                if phys in actual_ids:
                    raise ValueError('实际布点物理身份重复:' + phys)
                actual_ids.add(phys)
                self.basis(placement, phys)
                binding = placement.get('source_binding')
                if not isinstance(binding, dict):
                    raise ValueError(phys + '缺实际布点源记录绑定')
                psid = binding.get('source_id')
                self.refs([psid], phys + '.source_binding')
                record = pointer(self.contents[psid], binding.get('pointer'))
                fields = ('physical_support_id', 'run_ids', 'chainage_m')
                if not isinstance(record, dict) or any(record.get(k) != placement.get(k) for k in fields):
                    raise ValueError(phys + '实际布点与源记录不符')
                for rid in placement.get('run_ids', []):
                    if rid not in runs:
                        raise ValueError(phys + '布点跨出本支架组')
                if not placement.get('run_ids') or not isinstance(placement.get('chainage_m'), dict) or set(placement['chainage_m']) != set(placement['run_ids']):
                    raise ValueError(phys + '须给每个支承段的实际里程')
                for rid, chainage in placement['chainage_m'].items():
                    number(chainage, phys + '.chainage_m')
                    run_length = segments[runs[rid]['length_segment_id']]['length']['value']
                    if run_length is None or chainage > run_length:
                        raise ValueError(phys + '实际布点里程超出已知支承段')
                placement_source_ids.extend([psid] + placement['source_ids'])
        psource = self.source(gid + ':placements', {'placements': placements, 'scope': placement_scope})
        complete_placements = placements is not None and placement_scope['status'] == 'complete'
        actual = self.node(prefix + ':actual_placements', '1', len(placements) if complete_placements else None,
                           list(dict.fromkeys(placement_source_ids)) + [psource], '确认量' if complete_placements else '未决量')
        counts = dict(equivalent=equivalent, rule_integer=rule_integer, actual_placements=actual)
        row = dict(id=gid, system=group['system'], segment_ids=segment_ids, runs=list(runs.values()),
                   orientation=next(iter(orientations)), rounding=copy.deepcopy(rounding), events=list(events.values()),
                   working_count_basis=group['working_count_basis'], length_node=length, count_nodes=counts,
                   placement_scope=copy.deepcopy(placement_scope), observed_placement_count=None if placements is None else len(placements),
                   event_scope=copy.deepcopy(event_scope),
                   components=[], pending=[], construction=copy.deepcopy(group.get('construction')),
                   human_approval_claim=copy.deepcopy(group.get('human_approval_evidence')),
                   human_approval_verified=False, official_adoption=False)
        self.build_construction(prefix, row, group, counts)
        self.groups.append(row)

    def build_construction(self, prefix, row, group, counts):
        construction = group.get('construction')
        if construction is None:
            row['pending'].append('支架构造未提供；已知长度和副数分支继续')
            return
        if not isinstance(construction, dict) or construction.get('quantity_level') not in LEVELS:
            raise ValueError(prefix + '构造层级无效')
        self.basis(construction, prefix + '.construction')
        if construction.get('selection_status') not in ('selected', 'proposed', 'source_defined'):
            raise ValueError(prefix + '须明确方案选择状态')
        allowed = construction.get('applicable_orientations')
        if not isinstance(allowed, list) or not allowed or any(x not in ORIENTATIONS for x in allowed):
            raise ValueError(prefix + '构造适用方向无效')
        valid_orientation = row['orientation'] in allowed
        if not valid_orientation:
            row['pending'].append('当前构造不适用本组方向；附属量未决')
        rule = self.rule(prefix + ':construction', construction)
        level = construction['quantity_level']
        components = unique(construction.get('components'), prefix + '.components')
        if not components:
            row['pending'].append('构造无可量化构件')
        for cid, component in components.items():
            cp = prefix + ':COMP:' + cid
            if component.get('kind') not in ('crossarm', 'rod', 'connection'):
                raise ValueError(cp + '构件类型无效')
            self.basis(component, cp)
            scope = component.get('scope')
            if not isinstance(scope, dict) or set(scope) != {'supply', 'installation'} or any(v not in SCOPE for v in scope.values()):
                raise ValueError(cp + '须分列供货/安装范围')
            surface = component.get('surface')
            if surface not in ('galvanized', 'non_galvanized', 'unknown'):
                raise ValueError(cp + '须明确镀锌/非镀锌/未知')
            if component.get('paint_required') not in (True, False, None):
                raise ValueError(cp + 'paint_required无效')
            if component.get('paint_required') is not None and not isinstance(component['paint_required'], bool):
                raise ValueError(cp + 'paint_required须为布尔或null')
            if component.get('paint_required') is not None:
                self.basis(component.get('paint_basis') or {}, cp + '.paint_basis')
            if surface == 'galvanized' and component.get('paint_required') is True:
                raise ValueError(cp + '本入口非镀锌刷油分支不得套镀锌构件')
            if component['kind'] == 'connection' and component.get('count_unit') not in ('个', '套'):
                raise ValueError(cp + '连接件须分列个/套')
            count_each = self.measure(cp + ':count_each', component.get('count_each'), '1', integer=True)
            length_each = self.measure(cp + ':length_each', component.get('length_each'), 'm') if component['kind'] != 'connection' else None
            weight_each = self.measure(cp + ':unit_weight', component.get('unit_weight'), 'kg/m') if length_each else None
            # The exposed perimeter is a length. Multiplication by member length
            # yields m2, so a kg value cannot silently become paint area.
            perimeter = self.measure(cp + ':exposed_perimeter', component.get('exposed_perimeter'), 'm') if length_each else None
            mass_each = self.measure(cp + ':mass_each', component.get('mass_each'), 'kg') if not length_each else None
            area_each = self.measure(cp + ':area_each', component.get('area_each'), 'm2') if not length_each else None
            valid_source = self.source(cp + ':applicability', {'orientation': row['orientation'], 'allowed': allowed})
            applicability = self.node(cp + ':applicability', '1', 1 if valid_orientation else None, [valid_source], level if valid_orientation else '未决量')
            entry = dict(id=cid, kind=component['kind'], surface=surface, scope=copy.deepcopy(scope), bases={})
            for basis, count in counts.items():
                bp = cp + ':' + basis
                cn = self.calc(bp + ':count', '1', 'n*c*a', dict(n=count, c=count_each, a=applicability), rule, level)
                values = dict(member_count=cn)
                if length_each:
                    ln = self.calc(bp + ':length', 'm', 'n*l', dict(n=cn, l=length_each), rule, level)
                    mass = self.calc(bp + ':steel_mass', 'kg', 'l*w', dict(l=ln, w=weight_each), rule, level)
                    area = self.calc(bp + ':exposed_area', 'm2', 'l*p', dict(l=ln, p=perimeter), rule, level)
                    values.update(length_m=ln, steel_mass_kg=mass, exposed_area_m2=area)
                    if surface == 'non_galvanized' and component.get('paint_required') is True:
                        values.update(paint_mass_kg=self.calc(bp + ':paint_mass', 'kg', 'm', dict(m=mass), rule, level),
                                      paint_area_m2=self.calc(bp + ':paint_area', 'm2', 'a', dict(a=area), rule, level))
                    else:
                        reason = 'not_applicable' if surface == 'galvanized' or component.get('paint_required') is False else 'unknown'
                        values.update(paint_mass_kg=None, paint_area_m2=None, paint_status=reason)
                else:
                    values['connection_count_unit'] = component['count_unit']
                    mass = self.calc(bp + ':steel_mass', 'kg', 'n*m', dict(n=cn, m=mass_each), rule, level)
                    area = self.calc(bp + ':exposed_area', 'm2', 'n*a', dict(n=cn, a=area_each), rule, level)
                    values.update(steel_mass_kg=mass, exposed_area_m2=area)
                    if surface == 'non_galvanized' and component.get('paint_required') is True:
                        values.update(paint_mass_kg=self.calc(bp + ':paint_mass', 'kg', 'm', dict(m=mass), rule, level),
                                      paint_area_m2=self.calc(bp + ':paint_area', 'm2', 'a', dict(a=area), rule, level))
                    else:
                        values.update(paint_mass_kg=None, paint_area_m2=None,
                                      paint_status='not_applicable' if surface == 'galvanized' or component.get('paint_required') is False else 'unknown')
                entry['bases'][basis] = values
            row['components'].append(entry)

    def build(self):
        segments = self.build_segments()
        groups = unique(self.document.get('support_groups'), 'support_groups')
        if not groups:
            raise ValueError('support_groups不得为空')
        used_segments, actual_ids, event_ids = set(), set(), set()
        physical = [s['physical_id'] for s in segments.values()]
        if len(physical) != len(set(physical)):
            raise ValueError('桥架物理身份重复')
        for gid, group in groups.items():
            self.build_group(gid, group, segments, used_segments, actual_ids, event_ids)
        return self.graph, sorted(set(segments) - used_segments)


def calculate(document, base_dir, previous=None, previous_base_dir=None):
    builder = Builder(document, base_dir)
    graph, unused = builder.build()
    run = recalculate(graph, previous, base_dir, previous_base_dir)
    byid = {r['id']: r for r in run['nodes']}
    def quantity(node_id, display_unit=None):
        if node_id is None:
            return None
        r = byid[node_id]
        return dict(value=r['value'], unit=display_unit or r['unit'], quantity_level=r['quantity_level'],
                    graph_node_id=node_id, fingerprint=r['fingerprint'], status=r['status'],
                    reason=r['reason'], human_approval=False, official_adoption=False)
    groups = copy.deepcopy(builder.groups)
    systems = {}
    for group in groups:
        group['known_support_run_length'] = quantity(group.pop('length_node'))
        group['counts'] = {basis: quantity(node, '副') for basis, node in group.pop('count_nodes').items()}
        group['counts']['equivalent']['meaning'] = '按L/s及有据净增折算；小数副，不是实际安装布点'
        group['counts']['rule_integer']['meaning'] = '按显式连续通道/分段取整及有据净增的规则整数副；不是实际安装布点'
        group['counts']['actual_placements']['meaning'] = '唯一物理身份及源记录绑定的实际布点整数副；不由ceil推称'
        selected = group['construction'] is not None and group['construction'].get('selection_status') in ('selected', 'source_defined')
        group['working_count'] = group['counts'][group['working_count_basis']] if selected else None
        for component in group['components']:
            for basis, values in component['bases'].items():
                for key, node in list(values.items()):
                    if key not in ('paint_status', 'connection_count_unit') and node is not None:
                        values[key] = quantity(node, values.get('connection_count_unit') if key == 'member_count' and component['kind'] == 'connection' else None)
        system = systems.setdefault(group['system'], dict(group_ids=[], totals_by_count_basis={}))
        system['group_ids'].append(group['id'])
    # Summaries expose complete totals only; known subtotals remain visible when
    # one group is unknown. Each physical group contributes once per basis.
    def level_subtotals(quantities):
        return {level: math.fsum(q['value'] for q in quantities if q['quantity_level'] == level and q['value'] is not None)
                for level in sorted(LEVELS) if any(q['quantity_level'] == level for q in quantities)}
    for system, summary in systems.items():
        members = [g for g in groups if g['system'] == system]
        for basis in ('equivalent', 'rule_integer', 'actual_placements'):
            qs = [g['counts'][basis] for g in members]
            summary['totals_by_count_basis'][basis] = dict(unit='副', known_subtotal=math.fsum(q['value'] for q in qs if q['value'] is not None),
                complete_total=None if any(q['value'] is None for q in qs) else math.fsum(q['value'] for q in qs),
                unresolved_group_ids=[g['id'] for g in members if g['counts'][basis]['value'] is None],
                quantity_levels=sorted(set(q['quantity_level'] for q in qs)), subtotals_by_quantity_level=level_subtotals(qs),
                meaning='本口径候选量；已选工作量另列，不自动成为正式量', official_adoption=False)
        working = [g['working_count'] for g in members if g['working_count'] is not None]
        summary['selected_working_counts_by_basis'] = {
            basis: dict(unit='副', subtotals_by_quantity_level=level_subtotals([g['working_count'] for g in members
                    if g['working_count_basis'] == basis and g['working_count'] is not None]),
                group_ids=[g['id'] for g in members if g['working_count_basis'] == basis and g['working_count'] is not None])
            for basis in ('equivalent', 'rule_integer', 'actual_placements')}
        buckets = {}
        for group in members:
            for component in group['components']:
                for basis, values in component['bases'].items():
                    for metric, q in values.items():
                        if not isinstance(q, dict) or 'value' not in q:
                            continue
                        # Scope and treatment never disappear into a mixed kg total.
                        key = (basis, component['kind'], component['surface'],
                               component['scope']['supply'], component['scope']['installation'], metric, q['unit'])
                        buckets.setdefault(key, []).append((group['id'], component['id'], q))
        summary['component_totals'] = []
        for key, rows in sorted(buckets.items()):
            basis, kind, surface, supply, installation, metric, unit = key
            known = math.fsum(q['value'] for _, _, q in rows if q['value'] is not None)
            summary['component_totals'].append(dict(count_basis=basis, kind=kind, surface=surface,
                scope=dict(supply=supply, installation=installation), metric=metric, unit=unit,
                known_subtotal=known, complete_total=None if any(q['value'] is None for _, _, q in rows) else known,
                quantity_levels=sorted({q['quantity_level'] for _, _, q in rows}),
                subtotals_by_quantity_level=level_subtotals([q for _, _, q in rows]),
                unresolved_members=[dict(group_id=g, component_id=c) for g, c, q in rows if q['value'] is None]))
    result = dict(schema='qto-electrical-support-result/1.0', project_id=document['project_id'], status='REFERENCE_ONLY',
                  groups=groups, systems=systems, unused_segment_ids=unused, verified_sources=builder.verified,
                  input_content_sha256=digest(document), graph_content_sha256=digest(graph),
                  business_adoption_allowed=False, official_adoption=False, human_approval=False,
                  parameters_are_drawing_facts=False, automatic_detail_design=False, load_capacity_check=False,
                  calculation_engine='recompute_qto_dependencies.py:qto-calculation-graph/1.0',
                  pending=[dict(group_id=g['id'], reasons=g['pending']) for g in groups if g['pending']],
                  unresolved_nodes=[dict(id=r['id'], unit=r['unit'], reason=r['reason']) for r in run['nodes'] if r['value'] is None],
                  meaning='候选参考复算；已选方案不等于图纸事实、人工核准、正式采用或承载力验算')
    group_by_segment = {sid: g['id'] for g in groups for sid in g['segment_ids']}
    result['segments'] = [dict(id=sid, system=seg['system'], spec=seg['spec'], orientation=seg['orientation'],
                              support_group_id=group_by_segment.get(sid),
                              length=quantity(builder.segment_nodes[sid]), width=quantity('SEG:' + sid + ':width'),
                              count_attribution='引用唯一支架组；共架成员不再各计副数', upstream=copy.deepcopy(seg['upstream']))
                          for sid, seg in unique(document['segments'], 'segments').items()]
    if run['errors']:
        raise ValueError('依赖计算拒绝:' + json.dumps(run['errors'], ensure_ascii=False))
    for source in builder.verified:
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('计算期间来源变化:' + source['id'])
    return result, graph, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    try:
        if args.output_dir.exists():
            raise ValueError('输出目录已存在，须另存新版本')
        raw = args.input.read_bytes()
        document = json.loads(raw.decode('utf-8-sig'))
        previous = json.loads(args.previous.read_text(encoding='utf-8-sig')) if args.previous else None
        result, graph, run = calculate(document, args.input.resolve().parent, previous,
                                       args.previous.resolve().parent if args.previous else None)
        if hashlib.sha256(args.input.read_bytes()).digest() != hashlib.sha256(raw).digest():
            raise ValueError('计算期间输入发生变化')
        result['input_raw_sha256'] = hashlib.sha256(raw).hexdigest()
        result['script_raw_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        args.output_dir.mkdir(parents=True)
        for name, value in [('支架参考复算.json', result), ('计算依赖.json', graph), ('依赖复算.json', run)]:
            (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps(dict(status=result['status'], output_dir=str(args.output_dir), groups=len(result['groups']),
                              blocked_nodes=sum(r['value'] is None for r in run['nodes']),
                              business_adoption_allowed=False), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError, UnicodeError) as exc:
        print(json.dumps(dict(status='INPUT_REQUIRED', error=str(exc), business_adoption_allowed=False), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
