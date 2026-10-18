"""Name and number evidence for the existing point adapter; this module never counts devices.

All composition/label links are explicit source-member links. Proximity and number
expansion are inspection clues only, never a physical-instance generator.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict


TEXT_TYPES = frozenset({'ATTDEF', 'TEXT', 'MTEXT', 'ATTRIB'})
IDENTITY_KEYS = ('source_id', 'drawing_id', 'drawing', 'floor', 'scope_id', 'version')


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def attribute_evidence(insert, definitions):
    """Compare actual instance attributes with corresponding definition tags, retaining ambiguity."""
    defaults = defaultdict(list)
    for row in definitions.get(str(insert.get('block_name') or ''), ()):
        if row.get('entity_type') != 'ATTDEF':
            continue
        raw = row.get('raw_geometry') or {}
        tag = clean(raw.get('tag')).casefold()
        if tag:
            defaults[tag].append({'value': clean(row.get('text')), 'source_record_id': row.get('record_id')})
    result = []
    for tag, value in (insert.get('attributes') or {}).items():
        values = defaults.get(clean(tag).casefold(), [])
        distinct = {d['value'] for d in values}
        state = ('definition_ambiguous' if len(distinct) > 1 else 'equals_definition_default'
                 if clean(value) in distinct else 'instance_value' if distinct else 'definition_default_unknown')
        result.append({'tag': tag, 'value': clean(value), 'default_status': state,
                       'definition_defaults': values, 'is_device_classification': False})
    return result


def normalize_name_condition(raw):
    if not isinstance(raw, dict) or not clean(raw.get('basis')):
        raise ValueError('name_attribute_condition 必须给出对象及 basis')
    tags = raw.get('tags', ['A', '名称', '设备名称', 'NAME'])
    if not isinstance(tags, list) or not tags or any(not clean(t) for t in tags):
        raise ValueError('name_attribute_condition.tags 必须为非空名称属性标签数组')
    if not isinstance(raw.get('pattern'), str) or not raw['pattern'].strip():
        raise ValueError('name_attribute_condition.pattern 必须为非空正则')
    return {'tags': {str(t).casefold() for t in tags}, 'pattern': re.compile(raw['pattern'], re.I),
            'basis': clean(raw['basis'])}


def match_name_condition(condition, evidence):
    selected = [r for r in evidence if r['tag'].casefold() in condition['tags'] and r['value']]
    hits = [r for r in selected if condition['pattern'].search(r['value'])]
    usable = [r for r in hits if r['default_status'] == 'instance_value']
    # Test all trusted name tags, including a conflicting value the current pattern would not match.
    if len({r['value'] for r in selected if r['default_status'] == 'instance_value'}) > 1:
        return False, '名称类属性冲突，不能按一个属性替整块分类'
    if usable:
        return True, condition['basis']
    if hits:
        return False, '缺省属性或缺少唯一默认值对照，名称不可信'
    return False, '未命中名称类实例属性'


class NameCandidates:
    def __init__(self, profile, known_evidence, source_sha256, factor, coordinate_unit):
        self.profile = profile
        self.known_evidence = known_evidence
        self.source_sha256 = source_sha256
        self.factor, self.unit = factor, coordinate_unit
        self.context = {k: profile.get('scope', {}).get(k) for k in IDENTITY_KEYS}
        self.rows = {}
        self.groups = {}

    def add(self, row, path, parent_block, point, layer, suppressed_by=None):
        kind = str(row.get('entity_type') or '').upper()
        if kind not in TEXT_TYPES | {'POINT'}:
            return
        oid = 'PT-' + path
        if oid in self.rows:
            raise ValueError('名称候选源实例重复: ' + oid)
        valid = isinstance(point, (list, tuple)) and len(point) == 3 and all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in point)
        self.rows[oid] = {**self.context, 'object_id': oid, 'physical_id': oid, 'instance_path': path,
                          'source_record_id': row.get('record_id'), 'source_sha256': self.source_sha256,
                          'entity_handle': row.get('entity_handle'), 'entity_type': kind,
                          'text': clean(row.get('text')), 'raw_text': row.get('raw_text', row.get('text')),
                          'parent_block': parent_block or None, 'layer': layer,
                          'point': [v * self.factor for v in point] if valid else None,
                          'coordinate_unit': self.unit, 'suppressed_by': suppressed_by,
                          'geometry_issues': row.get('geometry_issues') or [],
                          'is_definition_attribute': bool(parent_block and kind == 'ATTDEF')}

    def evidence(self, item, label):
        if not clean(item.get('basis')):
            raise ValueError(label + '.basis 必填')
        ids = item.get('evidence_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or not v.strip() for v in ids):
            raise ValueError(label + '.evidence_ids 必须为非空数组')
        if self.known_evidence is not None and set(ids) - self.known_evidence:
            raise ValueError(label + ' 引用未登记证据')

    def resolve_groups(self):
        used = {}
        raw = self.profile.get('exploded_device_groups', [])
        if not isinstance(raw, list):
            raise ValueError('exploded_device_groups 必须为数组')
        for i, group in enumerate(raw):
            label = f'exploded_device_groups[{i}]'
            if not isinstance(group, dict):
                raise ValueError(label + ' 必须为对象')
            self.evidence(group, label)
            name = group.get('name_object_id')
            marker = group.get('marker_object_id')
            points = group.get('point_object_ids', [])
            if not isinstance(points, list) or len(points) != len(set(points)):
                raise ValueError(label + '.point_object_ids 必须为不重复数组')
            members = [name, marker, *points]
            if len(members) != len(set(members)) or any(oid not in self.rows for oid in members):
                raise ValueError(label + ' 成员缺失、重复或不在本工作范围')
            n, m = self.rows[name], self.rows[marker]
            if n['entity_type'] != 'ATTDEF' or not n['text'] or n['text'] == '1':
                raise ValueError(label + ' 名称必须为非空ATTDEF，不是计数标记')
            if m['entity_type'] != 'ATTDEF' or m['text'] != '1':
                raise ValueError(label + ' 标记必须是原图ATTDEF文字1')
            if n['is_definition_attribute'] or m['is_definition_attribute']:
                raise ValueError(label + ' 块定义ATTDEF默认值不是炸开物理设备')
            if any(self.rows[p]['entity_type'] != 'POINT' for p in points):
                raise ValueError(label + ' point_object_ids 必须指向原图POINT')
            if any(self.rows[p]['suppressed_by'] or self.rows[p]['point'] is None or self.rows[p]['geometry_issues'] for p in members):
                raise ValueError(label + ' 成员被父实例承担/排除，或定位/几何取证不完整')
            for oid in members:
                if oid in used:
                    raise ValueError(label + ' 与其他设备共用组成成员: ' + oid)
                used[oid] = name
            self.groups[name] = {'name_object_id': name, 'marker_object_id': marker, 'point_object_ids': points,
                                 'basis': group['basis'], 'evidence_ids': group['evidence_ids'],
                                 'member_object_ids': members, 'binding_method': 'explicit_source_members',
                                 'automatic_distance_binding': False}
        return self.groups

    def candidates(self):
        """Top-level substantive ATTDEFs are visible as pending names, including controlled-object/title noise."""
        return [dict(row, candidate_kind='exploded_name', composition=self.groups.get(oid),
                     candidate_quantity=None, business_adoption_allowed=False)
                for oid, row in self.rows.items() if row['entity_type'] == 'ATTDEF' and not row['parent_block']
                and row['text'] and not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', row['text'])]

    def number_audit(self, adopted):
        """Full-scope label inventory with explicit range syntax. Expanded codes never produce instances."""
        config = self.profile.get('number_audit')
        if config is None:
            return None
        if not isinstance(config, dict):
            raise ValueError('number_audit 必须为对象')
        rules = config.get('rules')
        if not isinstance(rules, list) or not rules:
            raise ValueError('number_audit.rules 必须为非空数组')
        compiled = []
        rule_ids = set()
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get('pattern'), str) or not rule['pattern'].strip():
                raise ValueError('number_audit.rules 必须给出对象及非空pattern')
            if not clean(rule.get('rule_id')) or rule['rule_id'] in rule_ids:
                raise ValueError('number_audit规则缺少唯一rule_id')
            rule_ids.add(rule['rule_id'])
            self.evidence(rule, 'number_audit.rules')
            pattern = re.compile(rule['pattern'], re.I)
            ranged = 'end' in pattern.groupindex
            if ranged and (not {'prefix', 'start'} <= set(pattern.groupindex)
                           or rule.get('range_endpoints') != 'inclusive'):
                raise ValueError('编号区间必须声明 prefix/start/end 命名组及 range_endpoints=inclusive')
            limit = rule.get('max_expansion', 1000)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
                raise ValueError('max_expansion 必须为1至10000的整数')
            compiled.append((rule, pattern, ranged, limit))
        by_key = defaultdict(list)
        for row in adopted:
            if row.get('instance_key'):
                by_key[clean(row['instance_key'])].append(row['physical_id'])
        ids = {row['physical_id'] for row in adopted}
        links = {}
        raw_links = config.get('links', [])
        if not isinstance(raw_links, list):
            raise ValueError('number_audit.links 必须为数组')
        for link in raw_links:
            if not isinstance(link, dict):
                raise ValueError('number_audit.links成员必须为对象')
            self.evidence(link, 'number_audit.links')
            oid = link.get('text_object_id')
            if oid not in self.rows or oid in links:
                raise ValueError('编号关联文字不存在或重复')
            bindings = link.get('number_bindings', [])
            if not isinstance(bindings, list) or any(not isinstance(b, dict) for b in bindings):
                raise ValueError('number_bindings 必须为逐编号到原实例的对象数组')
            targets = link.get('physical_ids', [b.get('physical_id') for b in bindings])
            if not isinstance(targets, list) or len(targets) != len(set(targets)) or set(targets) - ids:
                raise ValueError('编号关联只能指向本轮现有唯一已计实例')
            if link.get('decision') == 'exclude':
                if targets:
                    raise ValueError('编号排除线索不能同时关联已计设备')
            elif link.get('decision') != 'link' or not targets:
                raise ValueError('编号关联decision只能为有对象的link或有据exclude')
            links[oid] = link
        result = []
        for oid, row in sorted(self.rows.items()):
            if row['entity_type'] not in TEXT_TYPES:
                continue
            hits = [(rule, pattern.fullmatch(row['text']), ranged, limit) for rule, pattern, ranged, limit in compiled]
            hits = [h for h in hits if h[1]]
            if not hits:
                continue
            codes, problem = [], None
            if len(hits) != 1:
                problem = '编号语法多规则冲突'
            else:
                rule, match, ranged, limit = hits[0]
                groups = match.groupdict()
                if ranged and groups.get('end'):
                    start, end = int(groups['start']), int(groups['end'])
                    if end < start or end - start + 1 > limit:
                        problem = '区间倒置或超过展开上限'
                    else:
                        width = len(groups['start'])
                        codes = [groups['prefix'] + str(i).zfill(width) + (groups.get('suffix') or '') for i in range(start, end + 1)]
                else:
                    codes = [groups.get('number') or row['text']]
            matches = [{'number': code, 'physical_ids': sorted(by_key.get(code, [])),
                        'status': 'matched' if len(by_key.get(code, [])) == 1 else 'missing' if not by_key.get(code) else 'ambiguous'}
                       for code in codes]
            link = links.get(oid)
            if link and link['decision'] == 'link':
                bindings = link.get('number_bindings')
                if bindings:
                    pairs = {clean(b.get('number')): b.get('physical_id') for b in bindings}
                    if len(pairs) != len(bindings) or set(pairs) != set(codes) or len(set(pairs.values())) != len(pairs):
                        raise ValueError('编号区间关联须逐项完整且唯一，不按标签个数或目标列表凑数量')
                    if set(pairs.values()) != set(link.get('physical_ids', pairs.values())):
                        raise ValueError('number_bindings 与 physical_ids 不一致')
                    matches = [{'number': code, 'physical_ids': [pairs[code]], 'status': 'matched'} for code in codes]
                elif len(codes) == 1 and len(link.get('physical_ids', [])) == 1:
                    matches = [{'number': codes[0], 'physical_ids': list(link['physical_ids']), 'status': 'matched'}]
                else:
                    raise ValueError('编号区间需写number_bindings逐项对明细；单号只可唯一关联一个实例')
            if row['is_definition_attribute'] or row['suppressed_by']:
                status, basis = 'excluded_annotation', '块定义属性或父实例内部文字，不单独表示物理设备'
            elif link and link['decision'] == 'exclude':
                status, basis = 'excluded_annotation', link['basis']
            elif problem:
                status, basis = 'unresolved', problem
            elif link:
                status, basis = 'linked', link['basis']
            elif matches and all(m['status'] == 'matched' for m in matches):
                status, basis = 'linked', '按本轮已计实例instance_key逐项反查；文字不新增实例'
            else:
                status, basis = 'unresolved', '编号未逐项唯一对上已计实例；须核对图符、位置、引线及备用/图例状态'
            result.append({**row, 'candidate_kind': 'number_label', 'numbers': codes, 'number_matches': matches,
                           'status': status, 'basis': basis, 'rule_ids': [h[0].get('rule_id') for h in hits],
                           'links': link, 'candidate_quantity': None, 'is_physical_instance': False,
                           'business_adoption_allowed': False})
        unmatched_links = set(links) - {r['object_id'] for r in result}
        if unmatched_links:
            raise ValueError('编号关联文字未命中声明语法: ' + '、'.join(sorted(unmatched_links)))
        return {'schema_version': 'qto-point-number-audit/1.0', 'source_sha256': self.source_sha256,
                'rows': result, 'status_counts': dict(Counter(r['status'] for r in result)),
                'label_count': len(result), 'physical_quantity': None, 'business_adoption_allowed': False}
