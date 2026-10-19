"""Source-bound duct colour/material classification; never edits geometric quantities."""
from __future__ import annotations
import copy
import hashlib
import json
import re

SCHEMA = 'qto-duct-color-material/1.0'
COLOR_NAMES = {'红': (1, [255, 0, 0]), '黄': (2, [255, 255, 0]),
               '绿': (3, [0, 255, 0]), '青': (4, [0, 255, 255]),
               '蓝': (5, [0, 0, 255]), '紫': (6, [255, 0, 255]), '白': (7, [255, 255, 255])}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def checked_color(value):
    if not isinstance(value, dict) or set(value) != {'model', 'value'}:
        raise ValueError('材质颜色须明确aci或rgb及完整值')
    model, number = value['model'], value['value']
    if model == 'aci' and isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= 255:
        return value
    if model == 'rgb' and isinstance(number, list) and len(number) == 3 and all(
            isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 255 for n in number):
        return value
    raise ValueError('材质颜色值无效；ByLayer/ByBlock不是有效分档色')


def source_statements(raw):
    """Small explicit statement grammar; unsupported prose stays unresolved.

    Colour names express colour only; no material is hard-coded here. A rule
    must match an entire statement, not arbitrary tokens in a self-signed file.
    """
    statements = []
    for line in re.split(r'[\n。;；]', raw.replace('\\P', '\n')):
        line = re.sub(r'\s+', '', line)
        match = re.fullmatch(r'(?P<name>[红黄绿青蓝紫白])色风管(?:材质为|为|采用)(?P<material>[^,，:：=]{1,80})', line)
        if match:
            aci, rgb = COLOR_NAMES[match['name']]
            statements.append([match['material'], [{'model': 'aci', 'value': aci}, {'model': 'rgb', 'value': rgb}]])
            continue
        match = re.fullmatch(r'ACI(?:=|:|：)?(?P<aci>\d+)风管材质(?:=|:|：|为)(?P<material>[^,，:：=]{1,80})', line, re.I)
        if match:
            aci = int(match['aci'])
            if 1 <= aci <= 255:
                statements.append([match['material'], [{'model': 'aci', 'value': aci}]])
            continue
        match = re.fullmatch(r'RGB\((?P<r>\d+),(?P<g>\d+),(?P<b>\d+)\)风管材质(?:=|:|：|为)(?P<material>[^,，:：=]{1,80})', line, re.I)
        if match:
            rgb = [int(match[k]) for k in ('r', 'g', 'b')]
            if max(rgb) <= 255:
                statements.append([match['material'], [{'model': 'rgb', 'value': rgb}]])
    return statements


def bind_rules(profile, by_key, original_texts, source, layout, space, crop):
    rules = profile.get('color_material_rules', [])
    if not isinstance(rules, list):
        raise ValueError('color_material_rules须为数组')
    bound, ids = [], set()
    for rule in rules:
        rid = rule.get('rule_id')
        material = rule.get('material')
        if not isinstance(rid, str) or not rid.strip() or rid in ids:
            raise ValueError('颜色材质rule_id须非空且唯一')
        ids.add(rid)
        if not isinstance(material, str) or not material.strip() or material in {'未知', '未决', 'unknown'}:
            raise ValueError('颜色材质规则不得填默认未知材质')
        color = checked_color(rule.get('color'))
        scope = rule.get('scope', {})
        expected = {'source_sha256': source, 'layout': layout, 'space_id': space, 'crop': crop}
        if any(scope.get(k) != v for k, v in expected.items()):
            raise ValueError('颜色材质规则须绑定同版原图/布局/空间/裁剪范围')
        targets = scope.get('source_keys')
        if (not isinstance(targets, list) or not targets or any(not isinstance(k, str) or k not in by_key for k in targets)
                or len(targets) != len(set(targets))):
            raise ValueError('颜色材质规则须绑定非空且实际存在的唯一源实例范围')
        note = rule.get('note', {})
        key = note.get('source_key')
        row = by_key.get(key)
        definition = row.get('source_definition_record_id', row['record_id']) if row else None
        actual = original_texts.get(definition)
        if (row is None or row.get('entity_type') not in {'TEXT', 'MTEXT'} or actual is None
                or actual != note.get('expected_text')):
            raise ValueError('颜色材质图例/说明缺实际DXF文字句柄或原文不符；自签规则证据无效')
        if not any(mat == material and color in colors for mat, colors in source_statements(actual)):
            raise ValueError('实际图例/说明原文未明确支持所声明颜色与材质；禁止仅凭自签映射')
        # Every parsed, overlapping statement is evidence. Contradictions must
        # remain candidates even if a caller supplies just its preferred rule.
        statements = source_statements(actual)
        bound.append({'rule_id': rid, 'material': material, 'color': copy.deepcopy(color),
                      'scope': copy.deepcopy(scope), 'source_sha256': source,
                      'note': {'source_key': key, 'source_record_id': definition,
                               'entity_handle': row.get('entity_handle'), 'text': actual,
                               'text_sha256': hashlib.sha256(actual.encode('utf-8')).hexdigest(),
                               'geometry_sha256': fingerprint(row.get('geometry')), 'statements': statements}})
    return bound


def classify_sources(items, rules):
    sources = []
    for item in items:
        entry = item['entry']
        sources.append({'source_key': entry['source_key'], 'source_instance_path': item['key'],
                        'parent_source_part': entry.get('parent_source_part'),
                        'source_fraction_range': entry.get('source_fraction_range'),
                        'geometry_sha256': entry['geometry_sha256'],
                        'color_evidence': copy.deepcopy(entry.get('color_evidence'))})
    result = {'schema_version': SCHEMA, 'sources': sources, 'color_status': 'unresolved',
              'effective_color': None, 'material': None, 'material_status': 'color_unresolved',
              'rules': [], 'business_adoption_allowed': False}
    colors = [v['color_evidence'] for v in sources]
    if not colors or any(not v or v.get('status') != 'resolved' or v.get('effective_color') is None for v in colors):
        return result
    values = [v['effective_color'] for v in colors]
    if any(v != values[0] for v in values):
        result.update(color_status='conflict', material_status='source_color_conflict')
        return result
    result.update(color_status='resolved', effective_color=copy.deepcopy(values[0]), material_status='no_source_material_rule')
    materials = set()
    for source_item in sources:
        matches = [r for r in rules if source_item['source_instance_path'] in r['scope']['source_keys'] and r['color'] == values[0]]
        if not matches:
            return result
        result['rules'].extend(copy.deepcopy(matches))
        for rule in matches:
            materials.add(rule['material'])
            for material, declared_colors in rule['note']['statements']:
                if values[0] in declared_colors:
                    materials.add(material)
    # Explicitly conflicting notes/rules never fall back to order or majority.
    if len(materials) != 1:
        result['material_status'] = 'material_rule_conflict'
        return result
    result.update(material=next(iter(materials)), material_status='source_statement_bound')
    return result


def append_material_quantities(data, result):
    """Attach classifications to actual consumer rows, preserving every raw total.

    Only drawn straight/curved segments are classified. Fittings, inferred
    verticals and accessory gaps are never given a material by propagation.
    """
    marked = any('color_material' in r for r in data.get('segments', []) + data.get('drawn_curve_segments', []))
    if data.get('color_material_schema_version') != SCHEMA:
        if marked or 'color_material_rules' in data:
            raise ValueError('颜色材质结果须从冻结中性来源重建，不能手填声明')
        return
    if 'neutral_binding' not in data:
        raise ValueError('颜色材质规则须绑定实际中性来源，拒绝自签输入')
    from infer_duct_fittings_vertical import perimeter_m, spec_of
    from qto_cad_source_contract import canonical_scale
    raw = {r['segment_id']: r for r in data.get('segments', []) + data.get('drawn_curve_segments', [])}
    rows, groups = [], {}
    factor = canonical_scale(data['unit']) if data.get('unit') != 'raw_DU' else None
    for kind, name in [('drawn_straight', 'straight_segments'), ('drawn_curve', 'drawn_curve_segments')]:
        for actual in result.get(name, []):
            original = raw[actual['segment_id']]
            classification = copy.deepcopy(original['color_material'])
            actual['color_material'] = classification
            actual['material'] = classification['material']
            per = perimeter_m(original, factor) if factor is not None and original.get('surface_calculation_allowed', True) else None
            length = actual.get('length')
            area = length * canonical_scale(result['unit']) * per if length is not None and per is not None else None
            row = {'segment_id': actual['segment_id'], 'quantity_scope': kind, 'system': actual['system'],
                   'spec': spec_of(original), 'evidence_level': actual.get('evidence_level'),
                   'size_evidence_level': original.get('size_evidence_level'),
                   'surface_evidence_level': '规则推算' if area is not None else None,
                   'source_ids': copy.deepcopy(original['source_ids']), 'color_material': classification,
                   'material': classification['material'], 'length': length, 'length_unit': result['unit'],
                   'theoretical_surface_area': area, 'area_unit': 'm2',
                   'classified_length': length if classification['material'] is not None else None,
                   'classified_theoretical_surface_area': area if classification['material'] is not None else None}
            rows.append(row)
            key = (row['system'], row['spec'], row['evidence_level'], row['material'], classification['material_status'])
            group = groups.setdefault(key, {'system': key[0], 'spec': key[1], 'evidence_level': key[2],
                'material': key[3], 'material_status': key[4], 'segment_ids': [], 'known_length': 0.,
                'known_theoretical_surface_area': 0., 'length_unknown_count': 0, 'area_unknown_count': 0})
            group['segment_ids'].append(row['segment_id'])
            if length is None: group['length_unknown_count'] += 1
            else: group['known_length'] += length
            if area is None: group['area_unknown_count'] += 1
            else: group['known_theoretical_surface_area'] += area
    for group in groups.values():
        group['length'] = group['known_length'] if not group['length_unknown_count'] else None
        group['theoretical_surface_area'] = group['known_theoretical_surface_area'] if not group['area_unknown_count'] else None
        group['length_known_count'] = len(group['segment_ids']) - group['length_unknown_count']
        group['area_known_count'] = len(group['segment_ids']) - group['area_unknown_count']
        if not group['length_known_count']: group['known_length'] = None
        if not group['area_known_count']: group['known_theoretical_surface_area'] = None
    result['color_material_rules'] = copy.deepcopy(data.get('color_material_rules', []))
    result['material_quantities'] = rows
    result['material_summary'] = list(groups.values())
    result['material_scope'] = {'schema_version': SCHEMA, 'included': ['drawn_straight', 'drawn_curve'],
        'not_classified': ['fittings', 'inferred_verticals', 'riser_links', 'accessory_gaps', 'open_taps'],
        'all_drawn_segments_material_known': bool(rows) and all(r['material'] is not None for r in rows),
        'complete_material_quantity': None, 'business_adoption_allowed': False,
        'meaning': '原始几何量保持；材质不明仍保留原量及null材质。分档只覆盖已绘管段，不证明整网材质量完整。'}
