"""V1 图面视图适配器（契约 docs/维护/图面视图映射契约.md）：映射 + 项目表格 + 中性布局 → 复核视图。

中性布局和表格全部是合成的；只验证搬运、句柄回取、平移、底图裁剪、未定位原因和双向校验，不含项目工程量。
"""
import copy, csv, hashlib, json, math, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / '_qto-shared-evidence' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qto_review_contract as c  # noqa: E402
import build_qto_review_html as builder  # noqa: E402
import review_comparison_fixture as fx  # noqa: E402

SCRIPT = 'build_review_scenes_from_tables.py'


def run(script, *args):
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                       stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}, p.stderr


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def seg(a, b): return {'kind': 'line', 'start': [*a, 0.0], 'end': [*b, 0.0]}


def rec(h, et, layer, bbox, parts=None, insert=None, text=None, parent=None, layout='Model'):
    """One neutral layout record in the same compact, key-sorted form as the real extractor writes."""
    d = {'bbox': bbox, 'entity_handle': h, 'entity_type': et, 'geometry': {'closed': False, 'parts': parts} if parts else None, 'layer': layer,
         'layout': layout, 'parent_block': parent, 'raw_geometry': {'entity_type': et, **({'insert': [*insert, 0.0]} if insert else {})},
         'record_id': 'CAD-' + h, 'rotation': 0.0, 'text': text, 'x': insert[0] if insert else None, 'y': insert[1] if insert else None}
    return json.dumps(d, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


ARC = {'kind': 'ellipse_arc', 'center': [8000.0, 21000.0, 0.0], 'u': [500.0, 0.0, 0.0], 'v': [0.0, 500.0, 0.0], 'start_param': 0.0, 'sweep_param': math.pi / 2}

ENTITIES = [
    rec('A1', 'LINE', 'E-WIRE', [1000, 1000, 3000, 1000], [seg((1000, 1000), (3000, 1000))]),
    rec('A2', 'LWPOLYLINE', 'E-WIRE', [3000, 1000, 5000, 3000], [seg((3000, 1000), (3000, 3000)), seg((3000, 3000), (5000, 3000))]),
    rec('B1', 'INSERT', 'E-DEV', [1900, 1900, 2100, 2100], insert=(2000, 2000)),
    rec('B2', 'LINE', 'E-DEV', [2400, 2400, 2600, 2400], [seg((2400, 2400), (2600, 2400))], parent='BLK'),
    rec('T1', 'TEXT', 'TEXT', [500, 500, 1500, 700], insert=(500, 500), text='配电箱AL1'),
    rec('H1', 'HATCH', 'HATCH', [100, 100, 900, 900], [seg((100, 100), (900, 100)), seg((900, 100), (900, 900))]),
    rec('D9', 'LINE', 'DEFPOINTS', [100, 7000, 900, 7000], [seg((100, 7000), (900, 7000))]),
    rec('X1', 'INSERT', 'E-DEV', [49900, 49900, 50100, 50100], insert=(50000, 50000)),
    rec('P0', 'LINE', 'E-WIRE', [1000, 4000, 2000, 4000], [seg((1000, 4000), (2000, 4000))], layout='Layout1'),
    rec('L9', 'LINE', '0', [1000, 21000, 2000, 21000], [seg((1000, 21000), (2000, 21000))]),
    rec('C9', 'ARC', '0', [8000, 21000, 8500, 21500], [ARC]),
    rec('Q1', 'LINE', 'E-WIRE', [21000, 1000, 22000, 1000], [seg((21000, 1000), (22000, 1000))]),
]


def neutral(tmp_path, entities=ENTITIES):
    d = tmp_path / 'neutral' / 'D1'; d.mkdir(parents=True, exist_ok=True)
    (d / 'CAD中性取证清单.json').write_text(json.dumps({'authoritative_source': {'path': '合成.dwg', 'sha256': fx.sha('dwg')}}, ensure_ascii=False), encoding='utf-8')
    (d / '中性布局图元.ndjson').write_text('\n'.join(entities) + '\n', encoding='utf-8')
    return tmp_path / 'neutral'


SHEETS = [['P1', '一层照明', '1F', '平面', '0', '0', '10000', '8000'], ['P2', '一层照明（右半）', '1F', '平面', '20000', '0', '30000', '8000'],
          ['P3', '二层照明', '2F', '平面', '0', '20000', '10000', '28000'], ['P4', '系统图', '', '系统图', '', '', '', '']]
FIX = [['F1', 'P1', 'B1', '灯1', 'WL1', ''], ['F2', 'P1', 'B2', '灯2', 'WL1', ''], ['F3', 'P1', 'ZZ', '灯3', 'WL1', ''], ['F4', 'P9', 'B1', '灯4', 'WL2', ''],
       ['F5', 'P1', 'X1', '灯5', 'WL2', ''], ['F6', 'P3', 'L9', '线L9', 'WL3', ''], ['F7', 'P1', 'A2', '线A2', 'WL3', ''], ['F8', 'P1', '', '估算回路', 'WL4', 'estimated'],
       ['F9', 'P3', 'C9', '弧C9', 'WL3', '']]
WIRE = [['W1', 'P1', '1000', '5000', '2000', '5000', '1.0'], ['W1', 'P1', '2000', '5000', '2000', '6000', '1.0'],
        ['W2', 'P1', '4000', '5000', '5000', '5000', '1.0'], ['W2', 'P1', '6000', '5000', '7000', '5000', '1.5']]
PTS = [['S1', 'P4', '100000', '100000'], ['S2', 'P4', '102000', '100000']]


def mapping(tmp_path, *, fix=FIX, wire=WIRE, **override):
    t = tmp_path / 'tables'
    write_csv(t / 'sheets.csv', ['图页', '图名', '楼层', '类型', 'x1', 'y1', 'x2', 'y2'], SHEETS)
    write_csv(t / 'fix.csv', ['编号', '图页', '句柄', '名称', '回路', '原因'], fix)
    write_csv(t / 'wire.csv', ['句柄', '图页', 'x1', 'y1', 'x2', 'y2', '长度'], wire)
    write_csv(t / 'pts.csv', ['编号', '图页', 'x', 'y'], PTS)
    m = {'schema': 'qto-review-scene-mapping/1.0',
         'view': {'project_id': '合成图面', 'review_id': 'V1', 'title': '合成图面视图', 'revision': 'r1', 'professional': 'qto-components-points'},
         'drawings': [{'id': 'D1'}],
         'background': {'layers_exclude': ['^DEFPOINTS$']},
         'sheets': [{'path': 'tables/sheets.csv', 'constants': {'drawing': 'D1'},
                     'columns': {'sheet_id': '图页', 'title': '图名', 'floor': '楼层', 'sheet_kind': '类型', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}],
         'objects': [
             {'key': 'fix', 'path': 'tables/fix.csv', 'columns': {'object_id': '编号', 'sheet': '图页', 'handle': '句柄', 'title': '名称', 'unlocated_reason': '原因'},
              'constants': {'category': '灯具', 'status': '待复核'}, 'attributes': {'回路': '回路'}},
             {'key': 'wire', 'path': 'tables/wire.csv', 'professional': 'qto-electrical-routes', 'group_by': ['图页', '句柄'],
              'columns': {'object_id': '句柄', 'sheet': '图页', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'},
              'constants': {'title': '走线 {句柄}', 'category': '走线', 'status': '待复核'}, 'attributes': {'段长': '长度'}},
             {'key': 'pt', 'path': 'tables/pts.csv', 'columns': {'object_id': '编号', 'sheet': '图页', 'x': 'x', 'y': 'y'},
              'constants': {'title': '系统图点', 'category': '点位', 'status': '待复核'}}]}
    for k, v in override.items(): m[k] = v
    p = tmp_path / 'mapping.json'; p.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    return p, m


def convert(tmp_path, *extra, **override):
    nroot = neutral(tmp_path)
    m, _ = mapping(tmp_path, **override)
    out, rep = tmp_path / 'view.json', tmp_path / 'report.json'
    code, summary, err = run(SCRIPT, '--mapping', m, '--neutral-root', nroot, '--output', out, '--report', rep, *extra)
    assert rep.exists(), err
    return code, summary, json.loads(rep.read_text(encoding='utf-8')), out


def by_id(view): return {o['id']: o for o in view['objects']}


# ---------- legal: handles, coordinates, merge, unlocated reasons, background ----------

def test_scene_mapping_builds_a_valid_view(tmp_path):
    code, summary, report, out = convert(tmp_path)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8'))
    c.normalize_view(v)
    o = by_id(v)
    assert len(v['objects']) == 13 and report['checks']['S-1'] == {'rows': 15, 'rows_in_objects': 15, 'skipped': 0, 'pass': True}
    assert o['F1']['kind'] == 'point' and o['F1']['points'] == [[2000.0, 2000.0]] and o['F1']['scene_id'] == 'D1|P1'
    assert o['F7']['kind'] == 'line' and o['F7']['points'] == [[3000.0, 1000.0], [3000.0, 3000.0], [5000.0, 3000.0]] and 'parts' not in o['F7']
    assert o['F6']['scene_id'] == 'D1|P3' and o['F6']['points'] == [[1000.0, 21000.0], [2000.0, 21000.0]]
    arc = o['F9']['points']
    assert o['F9']['kind'] == 'line' and len(arc) >= 5 and arc[0] == pytest.approx([8500.0, 21000.0]) and arc[-1] == pytest.approx([8000.0, 21500.0])
    # Unlocated: every reason the script can decide, plus the one the project declared.
    reasons = {k: o[k].get('unlocated_reason') for k in ('F2', 'F3', 'F4', 'F5', 'F8')}
    assert reasons == {'F2': 'handle_in_block', 'F3': 'handle_not_found', 'F4': 'sheet_unknown', 'F5': 'outside_sheet', 'F8': 'estimated'}
    assert all(o[k]['kind'] == 'unlocated' and o[k]['points'] == [] for k in reasons)
    assert o['F4']['scene_id'] is None and o['F2']['scene_id'] == 'D1|P1' and json.loads(o['F5']['attributes']['原坐标']) == [50000.0, 50000.0]
    assert 'CAD-B1' in o['F1']['source'] and 'fix.csv 第 2 行' in o['F1']['source']
    # Merge by (sheet, handle): touching pieces become one polyline, separate pieces become parts; lengths stay text.
    w1 = next(x for x in v['objects'] if x['catalog_key'] == 'wire|P1|W1'); w2 = next(x for x in v['objects'] if x['catalog_key'] == 'wire|P1|W2')
    assert w1['points'] == [[1000.0, 5000.0], [2000.0, 5000.0], [2000.0, 6000.0]] and 'parts' not in w1
    assert w2['parts'] == [[[4000.0, 5000.0], [5000.0, 5000.0]], [[6000.0, 5000.0], [7000.0, 5000.0]]] and w2['points'] == w2['parts'][0]
    assert w2['attributes']['段长'] == '1.0、1.5' and w2['quantities'] == [] and w2['title'] == '走线 W2' and w2['professional'] == 'qto-electrical-routes'
    assert '第 4、5 行' in w2['source']
    # Report counts by table and reason.
    fix = next(t for t in report['tables'] if t['key'] == 'fix')
    assert fix['unlocated'] == {'handle_in_block': 1, 'handle_not_found': 1, 'sheet_unknown': 1, 'outside_sheet': 1, 'estimated': 1}
    assert report['checks']['S-4']['handle_rows'] == 8 and report['checks']['S-4']['handle_not_found'] == 1 and report['checks']['S-4']['handle_in_block'] == 1


def test_scene_background_clips_model_space_by_sheet_and_filters(tmp_path):
    code, _, report, out = convert(tmp_path)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8'))
    scenes = {s['id']: s for s in v['scenes']}
    assert set(scenes) == {'D1|P1', 'D1|P3', 'D1|P4'}
    p1 = scenes['D1|P1']; items = v['background_sets'][p1['background_refs'][0]]
    lines = sorted(i['points'] for i in items if i['kind'] == 'line'); texts = [i for i in items if i['kind'] == 'text']
    # A1, A2 only: DEFPOINTS excluded, HATCH skipped, paper space and block children never taken, X1 outside the sheet.
    assert lines == [[[1000, 1000], [3000, 1000]], [[3000, 1000], [3000, 3000], [5000, 3000]]]
    assert texts == [{'kind': 'text', 'point': [500, 500], 'text': '配电箱AL1'}]
    s1 = next(s for s in report['scenes'] if s['id'] == 'D1|P1')
    assert s1['inserts_not_expanded'] == 1 and s1['skipped_HATCH'] == 1 and s1['background_items'] == 3 and s1['background_points'] == 6
    assert p1['bounds'] == [0.0, 0.0, 10000.0, 8000.0] and p1['sheet_kind'] == '平面' and 'bounds_basis' not in p1
    # No sheet frame: bounds from the objects' extent, padded 5% but at least 1 m.
    p4 = scenes['D1|P4']
    assert p4['bounds'] == [99000.0, 99000.0, 103000.0, 101000.0] and p4['bounds_basis'] == '按对象外包框推定'
    assert v['sources'] == [{'id': 'D1', 'title': 'D1（合成.dwg）', 'sha256': fx.sha('dwg')}]


def test_scene_background_key_is_stable_and_shared(tmp_path):
    _, _, r1, out1 = convert(tmp_path / 'a'); _, _, r2, out2 = convert(tmp_path / 'b')
    v1, v2 = (json.loads(p.read_text(encoding='utf-8')) for p in (out1, out2))
    assert v1['background_sets'] == v2['background_sets'] and r1['result_fingerprint'] == r2['result_fingerprint']


def test_scene_view_builds_a_page_with_parts_and_reasons(tmp_path):
    _, _, report, out = convert(tmp_path)
    page = tmp_path / 'page.html'
    r = builder.build(out, page, feedback_url='')
    assert r['quantity_changes'] == 0 and r['size_estimate']['html_bytes'] == page.stat().st_size


def test_scene_composite_overlay_shifts_handles_and_background(tmp_path):
    scenes = [{'id': 'C1', 'drawing': 'D1', 'title': '一层合并', 'base': 'P1', 'overlays': [{'sheet': 'P2', 'dx': -20000, 'dy': 0}]}]
    fix = FIX + [['G1', 'P2', 'Q1', '右半走线', 'WL5', '']]
    code, _, report, out = convert(tmp_path, scenes=scenes, fix=fix)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8')); o = by_id(v)
    assert o['G1']['scene_id'] == 'D1|C1' and o['G1']['points'] == [[1000.0, 1000.0], [2000.0, 1000.0]]
    assert o['F1']['scene_id'] == 'D1|C1' and o['F1']['points'] == [[2000.0, 2000.0]]
    c1 = next(s for s in v['scenes'] if s['id'] == 'D1|C1')
    assert c1['title'] == '一层合并' and c1['bounds'] == [0.0, 0.0, 10000.0, 8000.0]
    bg = v['background_sets'][c1['background_refs'][0]]
    assert [[1000, 1000], [2000, 1000]] in [i['points'] for i in bg if i['kind'] == 'line']
    assert {f['sheet']: (f['dx'], f['dy']) for f in next(s for s in report['scenes'] if s['id'] == 'D1|C1')['background_filters']} == {'P1': (0.0, 0.0), 'P2': (-20000.0, 0.0)}


def test_scene_base_shift_moves_base_sheet_objects_bounds_and_background(tmp_path):
    scenes = [{'id': 'C1', 'drawing': 'D1', 'base': 'P1', 'dx': 500, 'dy': -300, 'overlays': [{'sheet': 'P2', 'dx': -20000, 'dy': 0}]}]
    fix = FIX + [['G1', 'P2', 'Q1', '右半走线', 'WL5', '']]
    code, _, report, out = convert(tmp_path, scenes=scenes, fix=fix)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8')); o = by_id(v)
    c1 = next(s for s in v['scenes'] if s['id'] == 'D1|C1')
    assert c1['bounds'] == [500.0, -300.0, 10500.0, 7700.0] and c1['title'] == '一层照明'
    assert o['F1']['points'] == [[2500.0, 1700.0]] and o['G1']['points'] == [[1000.0, 1000.0], [2000.0, 1000.0]]
    lines = [i['points'] for i in v['background_sets'][c1['background_refs'][0]] if i['kind'] == 'line']
    assert [[1500, 700], [3500, 700]] in lines and [[1000, 1000], [2000, 1000]] in lines


def test_scene_repeated_sheet_with_different_frames_fails_only_when_used(tmp_path):
    global SHEETS
    saved = SHEETS
    try:
        SHEETS = saved + [['P2', '一层照明（右半）', '1F', '平面', '40000', '0', '50000', '8000']]
        code, _, report, _ = convert(tmp_path / 'a')
        assert code == 0, report
        assert report['sheet_conflicts_unused'] == [{'sheet': 'D1|P2', 'rows': ['tables/sheets.csv 第 3 行', 'tables/sheets.csv 第 6 行']}]
        code, _, report, out = convert(tmp_path / 'b', fix=FIX + [['G1', 'P2', 'Q1', '右半走线', 'WL5', '']])
        assert code == 2 and not out.exists() and any('改正' in p['message'] and p['where'].endswith('第 6 行') for p in report['problems'])
    finally:
        SHEETS = saved


def test_scene_estimate_writes_no_view(tmp_path):
    nroot = neutral(tmp_path); m, _ = mapping(tmp_path)
    code, summary, _ = run(SCRIPT, '--mapping', m, '--neutral-root', nroot, '--estimate', '--report', tmp_path / 'r.json')
    assert code == 0 and summary['status'] == 'ESTIMATE' and summary['size_estimate']['raw_json_bytes'] > 0
    assert not (tmp_path / 'view.json').exists()


# ---------- counter-examples: nothing written, every problem listed ----------

def test_scene_duplicate_rows_without_group_by_are_reported(tmp_path):
    nroot = neutral(tmp_path); p, mp = mapping(tmp_path)
    mp['objects'][1].pop('group_by'); mp['objects'][1]['constants']['catalog_key'] = 'wire|{图页}|{句柄}'
    p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'v.json', '--report', tmp_path / 'r.json')
    report = json.loads((tmp_path / 'r.json').read_text(encoding='utf-8'))
    assert code == 2 and report['status'] == 'INPUT_REQUIRED' and not (tmp_path / 'v.json').exists()
    dup = [x for x in report['problems'] if x['check'] == 'S-2']
    assert len(dup) == 2 and all('group_by' in x['message'] for x in dup) and {x['where'] for x in dup} == {'tables/wire.csv 第 3 行', 'tables/wire.csv 第 5 行'}


def test_scene_group_by_rejects_points_and_quantities(tmp_path):
    nroot = neutral(tmp_path); p, mp = mapping(tmp_path)
    mp['objects'][0]['group_by'] = ['回路']
    mp['objects'][1]['quantities'] = [{'name': '长度', 'unit': 'm', 'level': '规则推算量', 'column': '长度'}]
    p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'v.json', '--report', tmp_path / 'r.json')
    msgs = [x['message'] for x in json.loads((tmp_path / 'r.json').read_text(encoding='utf-8'))['problems']]
    assert code == 2 and any('不带数量' in s for s in msgs) and any('只用于线' in s for s in msgs)


def test_scene_bad_inputs_are_all_reported(tmp_path):
    nroot = neutral(tmp_path)
    fix = FIX + [['F10', 'P1', 'B1', '', 'WL1', ''], ['F11', 'P1', 'B1', '灯', 'WL1', 'handle_not_found']]
    p, mp = mapping(tmp_path, fix=fix)
    mp['objects'][2]['columns']['x'] = '横坐标'                      # missing column
    mp['objects'][0]['colour'] = 'red'                              # unknown field: reminder only
    mp['scenes'] = [{'id': 'C1', 'drawing': 'D1', 'base': 'P1', 'overlays': [{'sheet': 'P1', 'dx': 0, 'dy': 0}]}]
    p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'v.json', '--report', tmp_path / 'r.json')
    report = json.loads((tmp_path / 'r.json').read_text(encoding='utf-8')); msgs = ' | '.join(x['message'] for x in report['problems'])
    assert code == 2 and not (tmp_path / 'v.json').exists()
    assert '映射用到的列不存在：横坐标' in msgs and '一张图页只能属于一个场景' in msgs and '对象缺少 title' in msgs and '只能由项目声明' in msgs
    assert report['mapping_warnings'][0]['keys'] == ['colour']


def test_scene_refuses_existing_output_wrong_schema_and_missing_source_hash(tmp_path):
    nroot = neutral(tmp_path); p, mp = mapping(tmp_path)
    out = tmp_path / 'v.json'; out.write_text('{}', encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', out)
    assert code == 2 and '已存在' in summary['error'] and out.read_text(encoding='utf-8') == '{}'
    mp['schema'] = 'other'; p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'v2.json')
    assert code == 2 and 'schema' in summary['error']
    mp['schema'] = 'qto-review-scene-mapping/1.0'; p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
    (nroot / 'D1' / 'CAD中性取证清单.json').write_text('{"authoritative_source": {}}', encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'v3.json')
    assert code == 2 and any('原图 SHA256' in x['message'] for x in summary['first_problems'])


def test_scene_transform_applies_before_overlay_shift(tmp_path):
    nroot = neutral(tmp_path); p, mp = mapping(tmp_path)
    write_csv(tmp_path / 'tables' / 'm.csv', ['编号', '图页', 'cx', 'cy'], [['M1', 'P2', '1.0', '2.0']])
    mp['scenes'] = [{'id': 'C1', 'drawing': 'D1', 'base': 'P1', 'overlays': [{'sheet': 'P2', 'dx': -20000, 'dy': 0}]}]
    mp['objects'].append({'key': 'm', 'path': 'tables/m.csv', 'transform': {'scale': 1000, 'dx': 20000, 'dy': 0},
                          'columns': {'object_id': '编号', 'sheet': '图页', 'x': 'cx', 'y': 'cy'}, 'constants': {'title': '米制点', 'category': '点位', 'status': '待复核'}})
    mp['objects'].append({'key': 's', 'path': 'tables/m.csv', 'coords': 'scene',
                          'columns': {'object_id': '编号', 'sheet': '图页', 'x': 'cx', 'y': 'cy'}, 'constants': {'title': '场景点', 'category': '点位', 'status': '待复核'}})
    p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'v.json', '--report', tmp_path / 'r.json')
    assert code == 0, summary
    v = json.loads((tmp_path / 'v.json').read_text(encoding='utf-8'))
    pts = {o['catalog_key']: o['points'] for o in v['objects'] if o['id'] == 'M1'}
    assert pts == {'m|P2|M1': [[1000.0, 2000.0]], 's|P2|M1': [[1.0, 2.0]]}


# ---------- contract: parts and unlocated reasons ----------

def test_contract_parts_and_unlocated_reason_rules():
    v = fx.view(); del v['comparison']; v['schema'] = 'qto-review-view/1.0'
    line = next(o for o in v['objects'] if o['kind'] == 'line')
    ok = copy.deepcopy(v); o = next(x for x in ok['objects'] if x['id'] == line['id'])
    o['parts'] = [o['points'], [[0, 0], [1, 1]]]
    c.normalize_view(ok)
    for bad in ({'parts': [[[9, 9], [8, 8]]]}, {'parts': [line['points'], [[0, 0]]]}, {'kind': 'point', 'points': [[0, 0]], 'parts': [[[0, 0], [1, 1]]]}):
        w = copy.deepcopy(v); x = next(y for y in w['objects'] if y['id'] == line['id']); x.update(bad)
        with pytest.raises(ValueError, match='parts'): c.normalize_view(w)
    w = copy.deepcopy(v); next(y for y in w['objects'] if y['id'] == line['id'])['unlocated_reason'] = 'estimated'
    with pytest.raises(ValueError, match='未定位原因'): c.normalize_view(w)
    w = copy.deepcopy(v); x = next(y for y in w['objects'] if y['id'] == line['id']); x.update(kind='unlocated', points=[], unlocated_reason='guessed')
    with pytest.raises(ValueError, match='未定位原因'): c.normalize_view(w)
    x['unlocated_reason'] = 'handle_in_block'; c.normalize_view(w)


# ---------- comparison tables: locate-only member_refs and same-sheet scenes of several packages ----------

import test_review_comparison_tables_20261015 as tt  # noqa: E402

DETAIL_HEADER = ['明细', '条目', '层级', '数量', '单位', '依据', '区域', '聚合', '成员数量名', '成员表', '聚合说明']
DETAILS = [['I1#1', 'I1', '规则推算', '30', 'm', '3 回路合计', '1F', 'sum_members', '水平长度', 'circuit', ''],
           ['I1#2', 'I1', '方案暂估', '5', 'm', '出桥架估', '1F', 'declared', '', '', '按走线估，走线只定位'],
           ['I2#1', 'I2', '确认', '10', '个', '逐个计数', '', 'declared', '', '', '合成：走线经过的接线盒'],
           ['I6#1', 'I6', '确认', '4', '台', '系统图箱号', '', '', '', '', ''],
           ['E1#1', 'E1', '方案暂估', '7', 'm', '终点估算', '', '', '', '', '']]


def base_views(tmp_path, second=None):
    a = fx.view(); del a['comparison']; a['schema'] = 'qto-review-view/1.0'
    for o in a['objects']: o['catalog_key'] = '成员|' + o['id']
    b = copy.deepcopy(a); route = copy.deepcopy(a['objects'][0])
    route.update(id='R1', catalog_key='走线|R1', title='走线 R1', quantities=[], attributes={})
    b['objects'] = [route]; b['groups'] = []
    if second: second(b)
    paths = []
    for name, v in (('E-A.json', a), ('E-B.json', b)):
        p = tmp_path / name; p.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8'); paths.append(p)
    return paths


def refs_convert(tmp_path, refs, second=None):
    m, mapping = tt.tables(tmp_path)
    tt.write_csv(tmp_path / 'tables' / 'details.csv', DETAIL_HEADER, DETAILS)
    mapping['tables']['detail_rows'][0]['columns']['aggregation_reason'] = '聚合说明'
    tt.write_csv(tmp_path / 'tables' / 'member_refs.csv', ['catalog_key', 'detail_id'], refs)
    mapping['tables']['member_refs'] = [{'path': 'tables/member_refs.csv'}]
    m.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
    out, rep = tmp_path / 'view.json', tmp_path / 'report.json'
    args = [x for p in base_views(tmp_path, second) for x in ('--base-view', p)]
    code, summary, _ = tt.run('build_comparison_view_from_tables.py', '--mapping', m, '--output', out, '--report', rep, *args)
    return code, json.loads(rep.read_text(encoding='utf-8')), out


def test_member_refs_locate_only_and_same_scene_merge(tmp_path):
    code, report, out = refs_convert(tmp_path, [['走线|R1', 'I1#2'], ['走线|R1', 'I2#1']])
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8'))
    rows = {r['id']: r for it in v['comparison']['items'] for r in it['detail_rows']}
    assert rows['I1#2']['member_keys'] == ['走线|R1'] and rows['I1#2']['locate_only_keys'] == ['走线|R1']
    assert rows['I2#1']['locate_only_keys'] == ['走线|R1']          # one route located from two rows: no C3-4 split needed
    assert report['member_refs'] == {'objects': 1, 'rows': ['I1#2', 'I2#1'], 'declared_rows_without_members': []}
    assert [s['id'] for s in v['scenes']] == ['P1'] and report['merged_scenes'] == [{'scene': 'P1', 'views': ['E-A.json', 'E-B.json']}]
    assert {o['catalog_key'] for o in v['objects']} >= {'走线|R1', '成员|' + fx.view()['objects'][0]['id']}


def test_member_refs_rejects_counted_rows_and_unknown_objects(tmp_path):
    code, report, out = refs_convert(tmp_path, [['走线|R9', 'I1#2'], ['走线|R1', 'I9#9']])
    msgs = ' | '.join(p['message'] for p in report['problems'])
    assert code == 2 and not out.exists() and '对象 走线|R9 不存在' in msgs and '明细 I9#9 不存在' in msgs
    (tmp_path / 'b').mkdir()
    code, report, out = refs_convert(tmp_path / 'b', [['走线|R1', 'I1#1']])
    assert code == 2 and 'declared' in report['error'] + json.dumps(report.get('problems', []), ensure_ascii=False)


def test_same_scene_id_with_different_content_stops_the_merge(tmp_path):
    def shift(b):
        for s in b['scenes']: s['bounds'] = [s['bounds'][0] + 1, *s['bounds'][1:]]
    code, report, out = refs_convert(tmp_path, [['走线|R1', 'I1#2']], second=shift)
    assert code == 2 and not out.exists() and '图页编号相同而内容不同：P1' in report['error'] and 'bounds' in report['error']
