#!/usr/bin/env python3
"""Source-labelled vertical risers across separately frozen neutral manifests.

This is a vertical-only quantity outlet. Source projection curves stay in their
own coordinate systems; their arc lengths never become vertical quantities.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from collections import Counter, defaultdict

import build_duct_network_from_neutral as duct
from duct_source_recovery import annotation_network_scope
from bind_duct_vertical_annotations import ensure_output_safe, write_protected_json
from point_host_attachment import reject_reparse_chain
from qto_cad_source_contract import canonical_scale
from duct_riser_continuity import build_continuity
from duct_riser_datums import read_transforms, normalize

SCHEMA = 'qto-duct-riser-source-bundle/1.0'
BINDING = 'qto-duct-riser-source-bundle-binding/1.0'


def section_perimeter(section):
    return (2 * (section['width'] + section['height']) if section['shape'] == 'rectangle'
            else math.pi * section['diameter']) * canonical_scale(section['unit'])


def section_name(section):
    return ((f"{section['width']:g}x{section['height']:g}" if section['shape'] == 'rectangle'
             else f"D{section['diameter']:g}") + section['unit'])


def frozen_path(value, base, protected):
    _, ref = duct.read_ref(value, base)
    path = Path(ref['path']); protected.append(path)
    return path


def identity_text(observation, rows):
    raw = observation.get('identity_annotation')
    if not isinstance(raw, dict):
        return None, '缺有源身份文字；手填物理ID不能证明跨图同物'
    row = rows.get(raw.get('source_key'))
    if row is None or row.get('entity_type') not in {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}:
        return None, '身份文字源实例缺失'
    actual = row.get('text')
    if actual != raw.get('expected_text'):
        raise ValueError('跨清单身份文字与实际冻结源不符')
    if not raw.get('basis') or not raw.get('source_locator'):
        raise ValueError('跨清单身份文字缺语义依据或来源定位')
    found = re.fullmatch(raw.get('pattern', ''), actual)
    fields = ('scope_id', 'physical_id', 'system', 'floor_id', 'datum_id', 'datum_kind',
              'value', 'unit', 'reference', 'size_unit')
    if not found or any(not found.groupdict().get(k) for k in fields):
        return None, '实际源身份/系统/楼层/共同基准/端标高字段不完整；未用配置值补齐'
    values = found.groupdict()
    if values['datum_kind'] not in {'absolute', 'project_common'}:
        return None, '源标高并非明确绝对或项目共同基准，各层相对标高不相减'
    if values['reference'] not in {'center', 'top', 'bottom'}:
        return None, '实际源端标高未明确中心/顶/底'
    if observation.get('physical_riser_id') not in (None, values['physical_id']):
        raise ValueError('手填物理ID与实际源身份不同')
    # Reuse the actual text parser. Multiple sections in one source text remain
    # unresolved until a source relation proves which dimensions name this riser.
    tag = {**raw, 'dimension_unit': values['size_unit'], 'leader_network': observation.get('leader_network')}
    section, _ = duct.bind_tag({'annotation_binding': tag}, {'annotation_network': tag['leader_network']}, rows, 'm')
    values['section'] = section
    values['source_key'] = raw['source_key']
    values['source_text'] = actual
    values['source_locator'] = raw['source_locator']
    values['basis'] = raw['basis']
    value = float(values['value'])
    if not math.isfinite(value):
        raise ValueError('源标高必须是有限数值')
    size = section.get('diameter', section.get('height')) * canonical_scale(section['unit'])
    values['center_elevation_m'] = value * canonical_scale(values['unit']) + (
        size / 2 if values['reference'] == 'bottom' else -size / 2 if values['reference'] == 'top' else 0)
    return values, None


def build(bundle_path):
    bundle_path = Path(bundle_path).resolve()
    config = duct.adapter.load_json(bundle_path)
    if config.get('schema_version') != SCHEMA:
        raise ValueError('跨清单立管组契约无效')
    package_id = duct.text(config.get('package_id'), 'package_id')
    sources = config.get('sources')
    if not isinstance(sources, list) or len(sources) < 2:
        raise ValueError('跨清单立管组须至少两份独立冻结中性清单')
    protected = [bundle_path]; contexts = {}; seen_sources = set(); seen_source_parts = set(); candidates = []; coverage = []
    source_bindings = []; groups = defaultdict(list); scopes = []; pending = []; blocked_identities = set()
    for spec in sources:
        source_id = duct.text(spec.get('source_id'), 'source_id')
        if source_id in contexts or '::' in source_id:
            raise ValueError('source_id须唯一且不能含命名空间分隔符')
        manifest_path = frozen_path(spec['manifest'], bundle_path.parent, protected)
        profile_path = frozen_path(spec['profile'], bundle_path.parent, protected)
        profile = duct.adapter.load_json(profile_path)
        if profile.get('riser_links'):
            raise ValueError('源配置已有竖高出口；跨清单组装拒绝重复消费')
        data, inputs = duct.build(manifest_path, profile_path)
        protected.extend(inputs)
        signature = (data['source_sha256'], data['layout'], data['space_id'])
        if signature in seen_sources:
            raise ValueError('相同原图SHA/布局/空间不能更换source_id重复输入')
        seen_sources.add(signature)
        stream, blocks, manifest = duct.adapter.source_from_manifest(manifest_path)
        failures = []
        rows = list(duct.adapter.expanded_manifest_rows(stream, blocks, manifest,
                    int(profile.get('max_block_depth', 32)), failures, target_layout=profile['layout']))
        by_key = {duct.source_key(row): row for row in rows}
        if len(by_key) != len(rows):
            raise ValueError('源展开实例身份重复')
        raw_candidates = data['riser_projection_candidates']
        by_part = {key: row for row in raw_candidates for key in row['source_ids']}
        for key in by_part:
            origin=(data['source_sha256'],data['layout'],key)
            if origin in seen_source_parts:
                raise ValueError('同一原图布局源part不能更换space_id或source_id重复输入')
            seen_source_parts.add(origin)
        mapped = {}
        for row in raw_candidates:
            value = copy.deepcopy(row)
            value['candidate_id'] = source_id + '::' + value['candidate_id']
            value['source_ids'] = [source_id + '::' + key for key in value['source_ids']]
            value.update(source_context=source_id, source_sha256=data['source_sha256'],
                         coordinate_unit=data['unit'], layout=data['layout'], space_id=data['space_id'])
            candidates.append(value)
            for key in row['source_ids']: mapped[key] = value
        for row in data['coverage']['rows']:
            if row['source_key'] in by_part:
                value = copy.deepcopy(row)
                value.update(source_key=source_id + '::' + row['source_key'], source_context=source_id)
                coverage.append(value)
        source_bindings.append({'source_id': source_id, **data['neutral_binding'],
                                'coordinate_unit': data['unit'], 'riser_projection_count': len(raw_candidates),
                                'expansion_diagnostics': failures})
        contexts[source_id] = (spec, data, by_key, by_part, mapped)
    for source_id, (spec, data, rows, by_part, mapped) in contexts.items():
        observations = spec.get('observations', [])
        if not isinstance(observations, list): raise ValueError('observations须为数组')
        seen_observations = set(); used_parts = set()
        for obs in observations:
            values, reason = identity_text(obs, rows)
            if reason:
                pending.append({'source_context': source_id, 'reason': reason, 'length': None,
                                'identity_annotation': obs.get('identity_annotation')})
                continue
            if values['source_key'] in seen_observations:
                raise ValueError('同一源身份文字重复登记')
            seen_observations.add(values['source_key'])
            cfg = obs.get('leader_network')
            if not cfg or cfg.get('target_contact') != 'source_curve_endpoint':
                pending.append({'source_context': source_id, 'reason': '身份文字缺完整引线至真实投影端点的依据', 'length': None})
                continue
            selector = {'source_keys': sorted({key.rsplit('#', 1)[0] for key in by_part})}
            if not selector['source_keys']:
                pending.append({'source_context': source_id, 'reason': '源配置未识别任何立管投影', 'length': None})
                continue
            binding = {'source_key': values['source_key'], 'leader_network': cfg}
            scope = annotation_network_scope({'annotation_binding': binding, 'selector': selector},
                    {'annotation_network': cfg}, rows, duct.match, duct.source_key)
            scopes.append({**scope, 'source_context': source_id})
            if scope['status'] != 'unique_source_leader_tree' or len(scope['target_part_keys']) != 1:
                pending.append({'source_context': source_id, 'reason': '身份引线未唯一对应一个实际投影part', 'length': None})
                continue
            key = scope['target_part_keys'][0]
            if key not in by_part or key in used_parts:
                raise ValueError('源投影part缺失或被多个身份文字占用')
            used_parts.add(key)
            candidate = mapped[key]
            if candidate['system'] != values['system'] or candidate['section'] != values['section']:
                raise ValueError('跨图源身份文字的系统/截面与实际投影分类不一致')
            if data['floors'][0]['floor_id'] != values['floor_id']:
                raise ValueError('实际源楼层文字与识别配置楼层不一致')
            identity = (values['scope_id'], values['system'], values['physical_id'])
            groups[identity].append({**values, 'source_context': source_id, 'candidate': candidate,
                                     'source_part_key': source_id + '::' + key, 'leader_scope': scope})
        patterns={obs.get('identity_annotation',{}).get('pattern') for obs in observations if isinstance(obs.get('identity_annotation'),dict)}-{None,''}
        referenced={obs.get('identity_annotation',{}).get('source_key') for obs in observations if isinstance(obs.get('identity_annotation'),dict)}
        for key,row in rows.items():
            if key in referenced or row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:continue
            for pattern in patterns:
                hit=re.fullmatch(pattern,str(row.get('text','')))
                if hit and all(hit.groupdict().get(k) for k in ('scope_id','system','physical_id')):
                    identity=tuple(hit.group(k) for k in ('scope_id','system','physical_id'))
                    blocked_identities.add(identity)
                    pending.append({'source_context':source_id,'source_key':key,'physical_identity':list(identity),'reason':'同源存在未纳入对应的相同身份文字，不能裁掉重复/分支身份','length':None})
    continuity, continuity_pending, continuity_bindings, continuity_blocked = build_continuity(config, bundle_path.parent, groups, protected, duct)
    pending.extend(continuity_pending)
    datum_transforms, datum_facts, datum_bindings = read_transforms(config, bundle_path.parent, protected, duct)
    verticals = []
    for identity, observations in sorted(groups.items()):
        reason = None
        chain = continuity.get(identity)
        if chain: observations, continuity_evidence = chain
        observations, datum_reason, datum_conversion = normalize(observations, datum_transforms, datum_facts, duct)
        if identity in blocked_identities:
            reason = '同源身份文字尚有未纳入对应的实例，物理唯一性未闭合'
        elif identity in continuity_blocked:
            reason = '已提供的有源系统图路径/端口证据未闭合，不能退回两端直连旁路'
        elif len(observations) < 2 or len({v['source_context'] for v in observations}) != len(observations):
            reason = '跨清单物理身份缺独立端口来源，不默认补齐楼层或端点'
        elif len(observations) > 2 and not chain:
            reason = '多于两个端口缺完整有源系统图路径及逐层端口对应，中间层连续性未证'
        elif len({v['floor_id'] for v in observations}) != len(observations):
            reason = '同楼层重复投影尚无上下端身份，未按重复标签合并计长'
        elif datum_reason:
            reason = datum_reason
        elif not chain and any(v['section'] != observations[0]['section'] for v in observations[1:]):
            reason = '跨层截面不同；变径或物理管段边界未证，不能生成恒定截面立管'
        elif chain:
            differences = [b['center_elevation_m'] - a['center_elevation_m'] for a,b in zip(observations,observations[1:])]
            if not (all(v > 0 for v in differences) or all(v < 0 for v in differences)):
                reason = '实际路径顺序的端标高非严格单调，竖向连续链与源高度冲突；未用最大最小值掩盖折返'
        height = abs(observations[-1]['center_elevation_m'] - observations[0]['center_elevation_m'])
        if not reason and (not math.isfinite(height) or height <= 0):
            reason = '实际上下端标高未形成正竖高'
        if reason:
            pending.append({'physical_identity': list(identity), 'reason': reason, 'length': None})
            continue
        varying = any(v['section'] != observations[0]['section'] for v in observations[1:])
        section = ({'shape': 'variable', 'port_sections': [v['section'] for v in observations]} if varying else observations[0]['section'])
        dims = {} if varying else duct.section_values(section, 'm')[0]
        vid = 'RISER-' + duct.digest(list(identity))
        keys = [v['source_part_key'] for v in observations]
        facts = [{k: v[k] for k in ('source_context', 'source_key', 'source_part_key', 'source_text',
                 'scope_id', 'physical_id', 'system', 'floor_id', 'datum_id', 'datum_kind', 'value',
                 'unit', 'reference', 'center_elevation_m', 'source_center_elevation_m', 'normalized_datum',
                 'basis', 'source_locator', 'leader_scope')} for v in observations]
        item = {'vertical_id': vid, 'kind': 'source_riser_link', 'system': identity[1], 'section': section, **dims,
                'source_ids': keys, 'length': height, 'unit': 'm', 'height_method': 'cross_source_labelled_endpoint_difference',
                'height_facts': facts, 'theoretical_surface_area': None if varying else section_perimeter(section) * height,
                'quantity_outlet': 'one_physical_riser', 'evidence_level': '规则推算',
                'identity_method': 'actual_source_labels_and_unique_source_leaders',
                'business_adoption_allowed': False}
        if datum_conversion: item['datum_conversion'] = datum_conversion
        if chain:
            item['height_method'] = 'source_path_ordered_labelled_port_differences'
            item['continuity_evidence'] = continuity_evidence
            item['intervals'] = []
            for index, (a,b) in enumerate(zip(observations, observations[1:])):
                constant = a['section'] == b['section']
                interval_length = abs(b['center_elevation_m'] - a['center_elevation_m'])
                interval = {'interval_id': vid + '-INTERVAL-' + str(index + 1),
                    'from_source_part': a['source_part_key'], 'to_source_part': b['source_part_key'],
                    'from_floor_id': a['floor_id'], 'to_floor_id': b['floor_id'],
                    'from_center_elevation_m': a['center_elevation_m'], 'to_center_elevation_m': b['center_elevation_m'],
                    'length': interval_length, 'unit': 'm', 'section': a['section'] if constant else None,
                    'from_section': a['section'], 'to_section': b['section'],
                    'theoretical_surface_area': section_perimeter(a['section']) * interval_length if constant else None,
                    'evidence_level': '规则推算', 'quantity_outlet': 'detail_of_one_physical_riser'}
                if constant: interval.update(duct.section_values(a['section'], 'm')[0])
                else:
                    interval.update(specification='变径(' + section_name(a['section']) + '→' + section_name(b['section']) + ')',
                         transition_geometry_status='section_change_location_and_fabrication_shape_unresolved',
                         surface_pending_reason='两端截面与竖向连续链已证；变径起终边界、偏心/同心和加工形状未证，不能以恒定周长计面积')
                item['intervals'].append(interval)
            item['known_theoretical_surface_area_subtotal'] = sum(v['theoretical_surface_area'] or 0 for v in item['intervals'])
        verticals.append(item)
        for value in observations:
            value['candidate'].update(resolved_vertical_id=vid, floor_id=value['floor_id'], length=height, height=height,
                                      height_unit='m', evidence_level='规则推算')
        for row in coverage:
            if row['source_key'] in keys:
                row.update(disposition='counted', vertical_id=vid,
                           reason='跨清单源身份及真实引线/共同基准端标高已闭合；仅由一个立管出口计竖高')
    summary = Counter((row['source_context'], row['layer'], row['disposition']) for row in coverage)
    data = {'schema_version': 'qto-duct-fitting-vertical-input/1.0', 'package_id': package_id,
            'network_id': package_id, 'unit': 'm', 'output_unit': 'm', 'coordinate_unit': 'm',
            'coordinate_system': 'WCS', 'quantity_scope': 'vertical_risers_only',
            'floors': [{'floor_id': floor} for floor in sorted({v[1]['floors'][0]['floor_id'] for v in contexts.values()})],
            'parameters': {'parameter_basis': 'source-labelled endpoints; no default floor heights'},
            'segments': [], 'drawn_curve_segments': [], 'fittings': [],
            'riser_projection_candidates': candidates, 'riser_vertical_items': verticals,
            'annotation_scope_candidates': scopes,
            'cross_source_identity_pending': pending,
            'coverage': {'schema_version': 'qto-duct-layer-coverage/1.0', 'scope': 'vertical_projection_parts_only',
                         'rows': coverage, 'quantity_complete': False,
                         'dispositions_closed': all(v['disposition'] in {'counted', 'excluded'} for v in coverage),
                         'summary': [{'source_context': k[0], 'layer': k[1], 'disposition': k[2], 'count': count}
                                     for k, count in sorted(summary.items())],
                         'meaning': '只核本立管组投影part去向；源水平风管另由各清单完整建网消费，不重复计入。'},
            'neutral_binding': {'kind': BINDING, 'bundle': duct.ref(bundle_path), 'sources': source_bindings,
                                'continuity_diagrams': continuity_bindings, 'datum_transform_sources': datum_bindings},
            'business_adoption_allowed': False}
    return data, protected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        data, protected = build(args.bundle)
        out = Path(os.path.abspath(args.output_dir)); reject_reparse_chain(out)
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError('跨清单立管输出须为新目录或确实为空，拒绝覆盖')
        paths = [out / name for name in ('风管实际消费输入.json', '风管图层覆盖差集.json',
                 '风管实际消费结果.json', '实际风管消费日志.json')]
        for target in paths: ensure_output_safe(target, protected)
        write_protected_json(paths[0], data, protected)
        write_protected_json(paths[1], data['coverage'], protected)
        producer = Path(__file__).with_name('infer_duct_fittings_vertical.py')
        command = [sys.executable, '-B', str(producer), '--input', str(paths[0]), '--output', str(paths[2])]
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 encoding='utf-8', env=env, timeout=180)
        write_protected_json(paths[3], {'command': command, 'returncode': process.returncode,
                             'stdout': process.stdout, 'stderr': process.stderr,
                             'producer': duct.ref(producer), 'input': duct.ref(paths[0]),
                             'business_adoption_allowed': False}, protected)
        if process.returncode:
            raise ValueError('实际消费失败，保留输入/日志及真实退出码')
        print(json.dumps({'status': 'WORKING_VERTICAL_SOURCE_BUNDLE', 'input': str(paths[0]),
              'vertical_items': len(data['riser_vertical_items']), 'pending': len(data['cross_source_identity_pending']),
              'business_adoption_allowed': False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'INPUT_REQUIRED', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
