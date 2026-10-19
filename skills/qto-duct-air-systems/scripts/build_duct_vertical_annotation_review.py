#!/usr/bin/env python3
"""Project the calculated annotation receipt to the existing shared review page.

No engineering calculation or approval mutation; conic sampling is display only.
"""
import argparse
import copy
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_qto-shared-evidence/scripts'))
from qto_cad_geometry import conic_point
from qto_review_contract import normalize_view
from build_qto_review_html import build
from bind_duct_vertical_annotations import ensure_output_safe, write_protected_json


def points(part):
    if part['kind'] == 'line':
        return [part['start'][:2], part['end'][:2]]
    return [conic_point(part, part['start_param'] + part['sweep_param'] * i / 48)[:2] for i in range(49)]


def project(result):
    receipt = result.get('vertical_annotation_binding')
    if result.get('schema_version') != 'qto-duct-fitting-vertical-result/1.0' or not receipt:
        raise ValueError('须为含竖向引注来源回执的风管推算结果')
    ev = receipt['evidence']; source = ev['source']
    view = {'schema': 'qto-review-view/1.0', 'project_id': result.get('package_id') or '风管工作识别',
            'review_id': 'duct-vertical-annotations', 'title': '风管竖向引注工作复核', 'revision': result['engine_version'],
            'sources': [{'id': 'CAD', 'title': '当前原图来源', 'sha256': source['sha256']}], 'scenes': [], 'objects': []}
    items = {i['annotation_id']: i for i in result['vertical_items'] if i.get('annotation_id')}
    for index, note in enumerate(receipt['annotations']):
        scene_id = f'ANNOTATION-{index + 1}'
        coordinate = note.get('pointer') or note.get('point')
        background = []
        for segment in note.get('leader_geometry', []):
            background.append({'kind': 'line', 'points': points(segment['part'])})
        if note.get('target'):
            background.append({'kind': 'line', 'points': points(note['target']['part'])})
        if note.get('point'):
            background.append({'kind': 'text', 'point': note['point'][:2], 'text': note['text']})
        item = items.get(note['annotation_id'])
        if coordinate is not None:
            all_points = [coordinate[:2], *[p for primitive in background for p in (primitive.get('points') or [primitive['point']])]]
            xs, ys = [p[0] for p in all_points], [p[1] for p in all_points]
            margin = max(max(xs) - min(xs), max(ys) - min(ys), 1) * .1
            view['scenes'].append({'id': scene_id, 'title': note['text'], 'coordinate_unit': ev['coordinate_unit'] or '原图单位未决',
                                   'source_id': 'CAD', 'bounds': [min(xs)-margin, min(ys)-margin, max(xs)+margin, max(ys)+margin],
                                   'background': background, 'y_axis': 'up'})
        view['objects'].append({'id': note['annotation_id'], 'catalog_key': 'duct-vertical/' + note['annotation_id'],
            'title': note['text'], 'category': '风管竖向引注', 'professional': 'qto-duct-air-systems',
            'detail': item['basis'] if item else '；'.join(note['reasons']), 'source': note['source_id'],
            'status': '规则推算待复核' if item else '未决', 'kind': 'point' if coordinate is not None else 'unlocated',
            'scene_id': scene_id if coordinate is not None else None, 'points': [coordinate[:2]] if coordinate is not None else [],
            'quantities': [{'name': '竖向中心线长度', 'value': item['length'] if item else None,
                            'unit': result['unit'], 'level': item['evidence_level'] if item else '未决'},
                           {'name': '竖段理论展开面积', 'value': item.get('theoretical_surface_area') if item else None,
                            'unit': result['unit'] + '2', 'level': '规则推算' if item and item.get('theoretical_surface_area') is not None else '未决'}],
            'attributes': {'原文': note['text'], '高度事实': copy.deepcopy(note['height_fact']),
                           '来源证据': copy.deepcopy(ev), '绑定证据': copy.deepcopy(note),
                           'business_adoption_allowed': False, 'human_reviewed': False}})
    if not view['scenes']:
        view['schema'] = 'qto-review-view/1.1'
    return normalize_view(view)


def main(argv=None):
    parser = argparse.ArgumentParser(description='将风管竖向引注结果投影到现有共享HTML复核链')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='共享复核视图JSON')
    parser.add_argument('--html', type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        view = project(data)
        # The projection also protects every upstream source named in the receipt.
        ev = data['vertical_annotation_binding']['evidence']
        protected = {Path(ev[k]['path']).resolve() for k in ('source', 'neutral_manifest', 'exchange_dxf', 'layout_entities', 'block_definitions')}
        protected.add(args.input)
        if ev.get('duct_input'):
            protected.add(Path(ev['duct_input']['path']))
        ensure_output_safe(args.output, protected)
        if args.html:
            ensure_output_safe(args.html, [*protected, args.output])
        write_protected_json(args.output, view, protected)
        if args.html:
            ensure_output_safe(args.html, [*protected, args.output])
            args.html.parent.mkdir(parents=True, exist_ok=True)
            build(args.output, args.html, feedback_url='')
        print(json.dumps({'status': 'REFERENCE_ONLY', 'objects': len(view['objects']), 'output': str(args.output)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'INPUT_REQUIRED', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
