#!/usr/bin/env python3
"""Pair category/next-line size annotations; candidates only, never device counts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'qto-pipe-networks' / 'scripts'))
import build_pipe_inference_input as adapter
from pipe_callout_stacks import ids, number, point
from qto_cad_source_contract import check_unit_factor, unit_known


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def _unit_vector(value, label):
    vector = point(value, label)
    length = math.hypot(*vector)
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError(f'{label} 退化或溢出，文字平面待核')
    return [v/length for v in vector]


def _text_frame(row):
    """Full orthogonal WCS text axes; never infer a tilted plane from a Z rotation alone."""
    angle = row.get('rotation')
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle):
        raise ValueError('文字缺少有效方向，不能以默认水平配对')
    raw = row.get('raw_geometry') or {}
    normal = _unit_vector(row.get('extrusion') or raw.get('extrusion') or [0., 0., 1.], '文字法向')
    if row['entity_type'] == 'MTEXT':
        direction = raw.get('text_direction')
        if direction is not None:
            u = _unit_vector(direction, 'MTEXT完整方向')
            if abs(_dot(u, normal)) > 1e-9:
                raise ValueError('MTEXT方向不在其声明文字平面内，三维/轴向表达待核')
        elif abs(normal[0])+abs(normal[1]) > 1e-12:
            raise ValueError('倾斜MTEXT缺少完整text_direction，不能以二维rotation推定文字平面，待核')
        else:
            u = [math.cos(angle), math.sin(angle), 0.]
        down = _cross(u, normal)
    else:
        # TEXT/ATTRIB/ATTDEF rotation is in the source OCS, whose full extrusion is frozen by the extractor.
        from ezdxf.math import OCS
        ocs = OCS(normal)
        u = list(ocs.to_wcs([math.cos(angle), math.sin(angle), 0.]))
        down = list(ocs.to_wcs([math.sin(angle), -math.cos(angle), 0.]))
    matrix = row.get('instance_transform')
    if matrix is not None:
        import qto_cad_geometry
        u, down = qto_cad_geometry.transform_vector(u, matrix), qto_cad_geometry.transform_vector(down, matrix)
    u, down = _unit_vector(u, '文字横向'), _unit_vector(down, '文字下行方向')
    if abs(_dot(u, down)) > 1e-9:
        raise ValueError('剪切文字方向缺少正交文字平面，待原生核对')
    return u, down, _unit_vector(_cross(u, down), 'WCS文字法向')


def pair_lines(rows, groups, *, source_sha256, factor, spacing, tolerance, alignment_tolerance):
    """Spacing and alignment use output units, groups are explicit source/space scopes."""
    spacing = number(spacing, 'line_spacing', positive=True)
    tolerance = number(tolerance, 'line_spacing_tolerance')
    alignment_tolerance = number(alignment_tolerance, 'alignment_tolerance')
    number(factor, 'factor', positive=True)
    if tolerance >= spacing / 2:
        raise ValueError('行距容差须小于本图行距的一半，避免相邻行范围重叠')
    if not isinstance(groups, list) or not groups:
        raise ValueError('groups 须为有明确来源/空间的非空分组')
    candidates, lines, rejected_pairs, seen, group_ids = [], [], [], set(), set()
    for group in groups:
        gid = adapter.text(group.get('group_id'), 'group_id')
        if gid in group_ids:
            raise ValueError('group_id 重复')
        group_ids.add(gid)
        space = adapter.text(group.get('space_id'), 'group.space_id')
        layout = adapter.text(group.get('layout'), 'group.layout')
        for oid in ids(group.get('object_ids'), 'group.object_ids'):
            if oid in seen:
                raise ValueError(f'文字被多个分组重复占用: {oid}')
            seen.add(oid)
            row = rows.get(oid)
            if (row is None or row.get('source_sha256') != source_sha256 or row.get('layout') != layout
                    or (row.get('space_id') is not None and row['space_id'] != space)):
                raise ValueError(f'分组文字不存在或跨来源/空间: {oid}')
            if row.get('entity_type') not in adapter.TEXT_TYPES:
                raise ValueError('多行引注只消费中性文字，不直接消费设备块')
            origin = point([row.get('x'), row.get('y'), row.get('z', 0.)], '文字锚点')
            u, down, normal = _text_frame(row)
            raw = str(row.get('text') or '')
            split = raw.replace('\\P', '\n').splitlines() or [raw]
            for i, content in enumerate(split):
                # Internal MTEXT line spacing is not guessed from height; the caller supplies this drawing's spacing.
                anchor = point([origin[k]*factor + i*spacing*down[k] for k in range(3)], '换算后文字锚点')
                lines.append(dict(line_id=f'{oid}:{i}', object_id=oid, line_index=i, text=content.strip(),
                    point=anchor, u=u, down=down, normal=normal, group_id=gid, space_id=space, layout=layout))
    for category in lines:
        label = re.search(r'(防雨百叶|百叶|风管箱|风口)', category['text'])
        if not label:
            continue
        nearby = []
        for other in lines:
            if other is category or other['group_id'] != category['group_id']:
                continue
            delta = [b-a for a,b in zip(category['point'], other['point'])]
            down = sum(a*b for a,b in zip(delta,category['down']))
            along = abs(sum(a*b for a,b in zip(delta,category['u'])))
            same_angle = sum(a*b for a,b in zip(category['u'], other['u'])) > 1-1e-9
            if same_angle and abs(down-spacing) <= tolerance and along <= alignment_tolerance:
                # A projected next line may lie on another plane. Check both full frames and the
                # normal component of the separation; world Z alone would reject a valid tilted plane.
                same_frame = _dot(category['down'], other['down']) > 1-1e-9
                offset = abs(_dot(delta, category['normal']))
                other_offset = abs(_dot(delta, other['normal']))
                plane_tolerance = max(1e-10*max(1., spacing),
                    32*max(math.ulp(v) for p in (category['point'], other['point']) for v in p))
                if not same_frame or max(offset, other_offset) > plane_tolerance:
                    rejected_pairs.append(dict(category_line_id=category['line_id'], size_line_id=other['line_id'],
                        reason='different_text_planes' if same_frame else 'incompatible_text_axes',
                        status='review_required', normal_offset=offset, other_normal_offset=other_offset,
                        numerical_plane_tolerance=plane_tolerance))
                    continue
                nearby.append(other)
        valid = []
        for other in nearby:
            size = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*[xX×*＊]\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*', other['text'])
            if size and all(math.isfinite(float(v)) and float(v) > 0 for v in size.groups()):
                valid.append((other, [float(v) for v in size.groups()]))
        for other, size in valid:
            candidates.append(dict(candidate_id=f'DMC-{len(candidates)+1:05d}', category=label.group(0),
                category_text=category['text'], size_text=other['text'], dimensions_mm=size,
                category_line_id=category['line_id'], size_line_id=other['line_id'],
                group_id=category['group_id'], space_id=category['space_id'], source_sha256=source_sha256,
                status='candidate' if len(nearby)==1 and len(valid)==1 else 'ambiguous', evidence_level='scenario',
                quantity=None, quantity_owner=None, quantity_owner_status='待按物理对象判定唯一出口', review_status='unreviewed',
                formal_adoption_allowed=False))
    use = {}
    for candidate in candidates:
        use.setdefault(candidate['size_line_id'], []).append(candidate)
    for items in use.values():
        if len(items) > 1:
            for candidate in items:
                candidate['status'] = 'ambiguous'
    return dict(schema_version='qto-duct-multiline-callout-candidates/1.0', candidates=candidates,
        lines=[{k:v for k,v in line.items() if k not in {'u','down','normal'}} for line in lines],
        rejected_pairs=rejected_pairs, quantity=None, business_adoption_allowed=False,
        meaning='类别行与下一行尺寸的图面候选；不代表实物实例或设备数量')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        input_path = args.input.resolve()
        cfg = adapter.load_json(input_path)
        if cfg.get('schema_version') != 'qto-duct-multiline-callout-input/1.0':
            raise ValueError('多行引注输入版本无效')
        manifest_path = (input_path.parent / cfg['cad_manifest']).resolve()
        stream, blocks, manifest = adapter.source_from_manifest(manifest_path)
        expected = cfg['expected_source_sha256']
        if manifest['authoritative_source']['sha256'] != expected:
            raise ValueError('多行引注配置来源哈希不符')
        unit = cfg['unit']
        raw_unit = manifest['drawing_unit']['raw_unit']
        if not unit_known(raw_unit) and unit.get('output_unit') == 'drawing_units':
            factor = number(unit.get('factor_to_output'), 'factor_to_output', positive=True)
            if factor != 1:
                raise ValueError('未知坐标单位仅可保留原图单位，不能换算物理长度')
        else:
            factor = check_unit_factor(raw_unit, unit['output_unit'], unit['factor_to_output'])
        if cfg.get('dimension_unit') != 'mm':
            raise ValueError('本版尺寸行仅支持显式毫米；未知尺寸单位不推测')
        evidence = cfg['spacing_evidence']
        evidence_path = (input_path.parent / evidence['path']).resolve()
        if _sha(evidence_path) != evidence['sha256'] or evidence.get('source_sha256') != expected:
            raise ValueError('行距依据文件/来源哈希不符')
        adapter.text(evidence.get('locator'), 'spacing_evidence.locator')
        adapter.text(evidence.get('observation'), 'spacing_evidence.observation')
        protected = [input_path, manifest_path, stream, blocks, evidence_path]
        protected += [Path(manifest[key]['path']).resolve() for key in ('authoritative_source', 'exchange_dxf', 'exchange_conversion') if manifest.get(key)]
        output = args.output.resolve()
        if any(output == p or (output.exists() and p.exists() and output.samefile(p)) for p in protected):
            raise ValueError('输出路径与输入/来源文件别名冲突')
        requested = [oid for g in cfg['groups'] for oid in ids(g['object_ids'], 'group.object_ids')]
        selected, failures = {}, []
        layouts = {g['layout'] for g in cfg['groups']}
        for layout in layouts:
            for row in adapter.expanded_manifest_rows(stream, blocks, manifest, 16, failures, target_layout=layout):
                oid = row.get('record_id')
                if oid in requested:
                    if oid in selected:
                        raise ValueError('来源文字ID重复')
                    selected[oid] = row
        result = pair_lines(selected, cfg['groups'], source_sha256=expected, factor=factor,
            spacing=cfg['line_spacing'], tolerance=cfg['line_spacing_tolerance'], alignment_tolerance=cfg['alignment_tolerance'])
        result.update(input_sha256=_sha(input_path), spacing_evidence=evidence, coordinate_unit=unit['output_unit'],
                      expansion_failures=failures, scope_object_ids=requested)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        print(json.dumps(dict(status='CANDIDATES_ONLY', candidate_count=len(result['candidates']), quantity=None), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='INPUT_REQUIRED', error=str(exc)), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
