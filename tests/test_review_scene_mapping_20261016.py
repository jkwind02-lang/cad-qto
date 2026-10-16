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
         'background': {'source': 'neutral', 'layers_exclude': ['^DEFPOINTS$']},
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


def test_sheet_length_factor_goes_to_its_scene_and_must_be_positive(tmp_path):
    nroot = neutral(tmp_path)

    def run_with(tag, p1_factor):
        p, mp = mapping(tmp_path)
        write_csv(tmp_path / 'tables' / 'sheets.csv', ['图页', '图名', '楼层', '类型', 'x1', 'y1', 'x2', 'y2', '实长系数'],
                  [[*r, p1_factor if r[0] == 'P1' else ''] for r in SHEETS])
        mp['sheets'][0]['columns']['length_factor'] = '实长系数'
        mp['scenes'] = [{'id': 'C1', 'drawing': 'D1', 'base': 'P1', 'overlays': [{'sheet': 'P2', 'dx': -20000, 'dy': 0}]}]
        p.write_text(json.dumps(mp, ensure_ascii=False), encoding='utf-8')
        out, rep = tmp_path / f'v_{tag}.json', tmp_path / f'r_{tag}.json'
        code, _, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', out, '--report', rep)
        return code, json.loads(rep.read_text(encoding='utf-8')), out

    code, report, out = run_with('ok', '0.05')
    assert code == 0, report['problems']
    scenes = {s['id']: s for s in json.loads(out.read_text(encoding='utf-8'))['scenes']}
    assert scenes['D1|C1']['length_factor'] == 0.05 and 'length_factor' not in scenes['D1|P3']
    code, report, out = run_with('bad', '-1')
    assert code == 2 and not out.exists()
    assert any(x['check'] == 'S-0' and 'length_factor 须为正数' in x['message'] for x in report['problems'])


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


# ---------- merged lines: every row placed by its own sheet, a row not found makes the whole object unlocated ----------

def probe(tmp_path, rows, scenes=None):
    nroot = neutral(tmp_path); p, m = mapping(tmp_path)
    write_csv(tmp_path / 'tables' / 'probe.csv', ['编号', '图页', '句柄'], rows)
    m['objects'] = [{'key': 'probe', 'path': 'tables/probe.csv', 'group_by': ['编号'], 'columns': {'object_id': '编号', 'sheet': '图页', 'handle': '句柄'},
                     'constants': {'title': '合并线', 'category': '走线', 'status': '待复核'}}]
    if scenes: m['scenes'] = scenes
    p.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    out, rep = tmp_path / 'v.json', tmp_path / 'r.json'
    code, _, err = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', out, '--report', rep)
    assert rep.exists(), err
    return code, json.loads(rep.read_text(encoding='utf-8')), json.loads(out.read_text(encoding='utf-8')) if out.exists() else None


def test_merged_line_with_a_row_not_located_is_unlocated_as_a_whole(tmp_path):
    code, r, v = probe(tmp_path / 'ok', [['G1', 'P1', 'A1'], ['G1', 'P1', 'A2']])
    assert code == 0 and v['objects'][0]['points'] == [[1000.0, 1000.0], [3000.0, 1000.0], [3000.0, 3000.0], [5000.0, 3000.0]]
    assert r['checks']['S-4']['failed_rows_in_located_objects'] == 0
    for name, bad, why in (('nf', 'ZZ', 'handle_not_found'), ('blk', 'B2', 'handle_in_block'), ('none', '', 'no_geometry')):
        code, r, v = probe(tmp_path / name, [['G1', 'P1', 'A1'], ['G1', 'P1', bad]])
        o = v['objects'][0]
        assert code == 0 and o['kind'] == 'unlocated' and o['points'] == [] and 'parts' not in o and o['unlocated_reason'] == why, (name, o)
        assert f'第 3 行 {why}' in o['unlocated_note'] and '不单独画' in o['unlocated_note']
        assert r['checks']['S-1']['rows_in_objects'] == 2 and r['checks']['S-4']['failed_rows_in_located_objects'] == 0 and r['checks']['S-4']['pass']


def test_merged_line_across_overlay_sheets_shifts_each_row(tmp_path):
    scenes = [{'id': 'C1', 'drawing': 'D1', 'base': 'P1', 'overlays': [{'sheet': 'P2', 'dx': -20000, 'dy': 0}]}]
    code, r, v = probe(tmp_path / 'a', [['G1', 'P1', 'A1'], ['G1', 'P2', 'Q1']], scenes)
    o = v['objects'][0]
    assert code == 0 and o['scene_id'] == 'D1|C1' and o['kind'] == 'line'
    assert o['parts'] == [[[1000.0, 1000.0], [3000.0, 1000.0]], [[1000.0, 1000.0], [2000.0, 1000.0]]]
    code, r, v = probe(tmp_path / 'b', [['G1', 'P1', 'A1'], ['G1', 'P3', 'L9']])
    assert code == 2 and v is None and any('落在不同场景' in p['message'] for p in r['problems'])


def test_same_handle_on_two_drawings_uses_each_rows_drawing(tmp_path):
    """Handle A1 exists on D1 and D2 at different places: a row is looked up on its own drawing, never the first row's."""
    def run_rows(where, rows):
        nroot = neutral(where)
        d2 = nroot / 'D2'; d2.mkdir()
        (d2 / 'CAD中性取证清单.json').write_text(json.dumps({'authoritative_source': {'path': '合成2.dwg', 'sha256': fx.sha('dwg2')}}, ensure_ascii=False), encoding='utf-8')
        moved = [rec('A1', 'LINE', 'E-WIRE', [1000, 1100, 3000, 1100], [seg((1000, 1100), (3000, 1100))])] + ENTITIES[1:]
        (d2 / '中性布局图元.ndjson').write_text('\n'.join(moved) + '\n', encoding='utf-8')
        p, m = mapping(where)
        m['drawings'] = [{'id': 'D1'}, {'id': 'D2'}]
        m['sheets'].append({**copy.deepcopy(m['sheets'][0]), 'constants': {'drawing': 'D2'}})
        write_csv(where / 'tables' / 'probe.csv', ['编号', '图纸', '图页', '句柄'], rows)
        m['objects'] = [{'key': 'probe', 'path': 'tables/probe.csv', 'group_by': ['编号'], 'columns': {'object_id': '编号', 'drawing': '图纸', 'sheet': '图页', 'handle': '句柄'},
                         'constants': {'title': '合并线', 'category': '走线', 'status': '待复核'}}]
        p.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
        out, rep = where / 'v.json', where / 'r.json'
        code, _, err = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', out, '--report', rep)
        assert rep.exists(), err
        return code, json.loads(rep.read_text(encoding='utf-8')), json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
    code, r, v = run_rows(tmp_path / 'ok', [['G2', 'D2', 'P1', 'A1']])
    assert code == 0 and v['objects'][0]['scene_id'] == 'D2|P1' and v['objects'][0]['points'] == [[1000.0, 1100.0], [3000.0, 1100.0]]
    code, r, v = run_rows(tmp_path / 'mixed', [['G1', 'D1', 'P1', 'A1'], ['G1', 'D2', 'P1', 'A1']])
    assert code == 2 and v is None and any('落在不同场景' in p['message'] and 'D1|P1' in p['message'] and 'D2|P1' in p['message'] for p in r['problems'])


# ---------- restored background from the exchange DXF (contract 7.1-7.5) ----------

def make_dxf(path, pipes=10, hidden_lamp=False):
    """Synthetic exchange DXF: a counted lamp symbol twice at one point and once not counted, a small switch block with a solid,
    a large building block (walls kept, pipes dropped, its pattern hatch counted), a frame inserted outside the sheet, hidden,
    Defpoints and non-plotting layers, aligned TEXT, two-line MTEXT and a dashed line."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    doc = ezdxf.new('R2010', setup=True); msp = doc.modelspace()
    doc.layers.add('E-LIGHT', color=3); doc.layers.add('WALL', color=7); doc.layers.add('PIPE', color=5)
    doc.layers.add('HIDDEN').off(); doc.layers.add('NOPLOT', plot=False)          # Defpoints comes with the template
    lamp = doc.blocks.new('LAMP'); lamp.add_circle((0, 0), 200)
    h = lamp.add_hatch(); h.set_pattern_fill('ANSI31', scale=10); h.paths.add_polyline_path([(-150, -150), (150, -150), (150, 150), (-150, 150)], is_closed=True)
    lamp.add_attdef('A', (0, -300), dxfattribs={'height': 100}); lamp.add_attdef('T', (250, 0), dxfattribs={'height': 100})
    sw = doc.blocks.new('SW'); sw.add_solid([(0, 0), (100, 0), (0, 100), (100, 100)]); sw.add_line((0, 0), (0, 300))
    big = doc.blocks.new('BIG')
    for k in range(3): big.add_line((500, 6000 + k * 100), (4500, 6000 + k * 100), dxfattribs={'layer': 'WALL'})
    for k in range(pipes): big.add_line((5000, 6000 + k * 50), (9000, 6000 + k * 50), dxfattribs={'layer': 'PIPE'})
    bh = big.add_hatch(dxfattribs={'layer': 'WALL'}); bh.set_pattern_fill('ANSI31', scale=10); bh.paths.add_polyline_path([(500, 7000), (900, 7000), (900, 7400)], is_closed=True)
    frame = doc.blocks.new('FRAME'); frame.add_line((5000, 0), (5000, 7000))
    handles = {}
    for name, p in (('L1', (2000, 2000)), ('L2', (2000, 2000.4)), ('L3', (6000, 2000))):
        ins = msp.add_blockref('LAMP', p, dxfattribs={'layer': 'E-LIGHT'}); ins.add_auto_attribs({'A': '吸顶灯', 'T': 't'})
        for a in ins.attribs:
            if a.dxf.tag == 'A': a.is_invisible = True
        handles[name] = ins.dxf.handle
    if hidden_lamp: handles['LH'] = msp.add_blockref('LAMP', (7000, 2000), dxfattribs={'layer': 'HIDDEN'}).dxf.handle
    msp.add_blockref('SW', (3000, 3000), dxfattribs={'layer': 'E-LIGHT'})
    handles['BIG'] = msp.add_blockref('BIG', (0, 0), dxfattribs={'layer': 'WALL'}).dxf.handle
    msp.add_blockref('FRAME', (-4000, 500))
    msp.add_text('配电箱AL1', height=350, dxfattribs={'layer': 'E-LIGHT'}).set_placement((5000, 1000), align=TextEntityAlignment.BOTTOM_CENTER)
    msp.add_mtext('第一行\\P第二行', dxfattribs={'char_height': 200, 'insert': (7000, 4000)})
    doc.styles.add('FIX', font='txt.shx').dxf.height = 250                       # height 0 on a text means "the style's height"
    for s, style, y in (('样式字高', 'FIX', 5000), ('无字高', 'Standard', 5500)):
        msp.add_text(s, dxfattribs={'style': style, 'insert': (7000, y)}).dxf.unprotected_set('height', 0.0)   # ezdxf would turn 0 into 2.5
    msp.add_line((1000, 4000), (4000, 4000), dxfattribs={'linetype': 'DASHED'})
    for y, lay in ((4500, 'HIDDEN'), (4600, 'Defpoints'), (4700, 'NOPLOT')): msp.add_line((1000, y), (4000, y), dxfattribs={'layer': lay})
    msp.add_line((20000, 1000), (21000, 1000))                                     # on another sheet
    doc.saveas(path)
    return handles


LAMP_XY = {'L1': ('2000', '2000'), 'L2': ('2000', '2000.4'), 'L3': ('6000', '2000'), 'LH': ('7000', '2000'), 'BIG': ('100', '100')}


def dxf_convert(tmp_path, background=None, with_handles=True, dxf_sha=None, dxf_path='D1.dxf', notes=None, lamps=('L1', 'L2'),
                second=None, hcase=str, hidden_lamp=False):
    """lamps: fixture symbols listed in D1's object table. second: a copy of D1 as drawing D2 (same handles), with these symbols listed for D2."""
    nroot = neutral(tmp_path); folder = nroot / 'D1'
    handles = make_dxf(folder / 'D1.dxf', hidden_lamp=hidden_lamp)
    mf = json.loads((folder / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    mf['exchange_dxf'] = {'path': dxf_path, 'sha256': dxf_sha or hashlib.sha256((folder / 'D1.dxf').read_bytes()).hexdigest()}
    (folder / 'CAD中性取证清单.json').write_text(json.dumps(mf, ensure_ascii=False), encoding='utf-8')
    rows = [[n, 'D1', 'P1', hcase(handles[n]), *LAMP_XY[n]] for n in lamps]
    if second is not None:
        import shutil
        shutil.copytree(folder, nroot / 'D2')
        mf['authoritative_source']['sha256'] = fx.sha('dwg2')
        (nroot / 'D2' / 'CAD中性取证清单.json').write_text(json.dumps(mf, ensure_ascii=False), encoding='utf-8')
        rows += [['P-D2', 'D2', 'P1', '', '3000', '3000']] + [[n + '-D2', 'D2', 'P1', handles[n], *LAMP_XY[n]] for n in second]   # D2 always has a row placed by coordinates only
    write_csv(tmp_path / 'tables' / 'lamps.csv', ['编号', '图纸', '图页', '句柄', 'x', 'y'], rows)
    p, m = mapping(tmp_path, background=background or {'large_block_points': 30})
    if second is not None:
        m['drawings'] = [{'id': 'D1'}, {'id': 'D2'}]
        m['sheets'].append({**copy.deepcopy(m['sheets'][0]), 'constants': {'drawing': 'D2'}})
    cols = {'object_id': '编号', 'drawing': '图纸', 'sheet': '图页', 'x': 'x', 'y': 'y', **({'handle': '句柄'} if with_handles else {})}
    m['objects'] = [{'key': 'lamp', 'path': 'tables/lamps.csv', 'columns': cols, 'constants': {'title': '灯', 'category': '灯具', 'status': '待复核'}}]
    if notes: m['symbol_notes'] = notes(handles, tmp_path / 'tables')
    p.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    out, rep = tmp_path / 'view.json', tmp_path / 'report.json'
    code, _, err = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', out, '--report', rep)
    assert rep.exists(), err
    return code, json.loads(rep.read_text(encoding='utf-8')), json.loads(out.read_text(encoding='utf-8')) if out.exists() else None, out


def test_dxf_background_restores_symbols_text_fill_and_dash(tmp_path):
    code, report, v, out = dxf_convert(tmp_path)
    assert code == 0, report
    c.normalize_view(v)
    sc = next(s for s in v['scenes'] if s['id'] == 'D1|P1'); items = v['background_sets'][sc['background_refs'][0]]
    texts = {i['text']: i for i in items if i['kind'] == 'text'}
    lines = [i for i in items if i['kind'] == 'line']; polys = [i for i in items if i['kind'] == 'polygon']
    two = {(tuple(i['points'][0]), tuple(i['points'][1])): i for i in lines if len(i['points']) == 2}
    # Visible attribute drawn, invisible one not; MTEXT line by line; TEXT keeps its height and alignment.
    assert 't' in texts and '吸顶灯' not in texts and {'第一行', '第二行'} <= set(texts)
    assert texts['配电箱AL1']['height'] == 350 and texts['配电箱AL1']['align'] == 'center' and texts['配电箱AL1']['baseline'] == 'bottom'
    assert texts['样式字高']['height'] == 250 and 'height' not in texts['无字高']
    # Counted symbol fully expanded: circle and pattern lines; the switch block's solid is a filled polygon.
    assert any(len(i['points']) > 8 and min(p[0] for p in i['points']) == 1800 for i in lines)
    assert sum(1 for (a, b) in two if all(1850 <= q <= 2150 for q in (*a, *b))) >= 3
    assert any(i.get('fill') for i in polys)
    # Dashed linetype carries a dash pattern; hidden, Defpoints and non-plotting layers are not drawn.
    assert two[((1000, 4000), (4000, 4000))].get('dash') and not any(((1000, y), (4000, y)) in two for y in (4500, 4600, 4700))
    # Frame inserted outside the sheet with content inside is taken; the other sheet's line is not.
    assert ((1000, 500), (1000, 7500)) in two and not any(q[0] >= 20000 for i in lines for q in i['points'])
    # Large block: walls drawn, pipes dropped and counted with its pattern hatch as "须回原图".
    assert sum(1 for (a, b) in two if a[0] == 500 and b[0] == 4500) == 3 and not any(a[0] == 5000 and b[0] == 9000 for (a, b) in two)
    assert sc['restoration'] == {'source': 'dxf', 'not_shown_total': 11, 'other_layers_top': [['PIPE', 10]],
                                 'not_shown': {'other_layers': 10, 'pattern_hatch': 1, 'image': 0, 'ole': 0, 'proxy': 0, 'xref_missing': 0}}
    s1 = next(s for s in report['scenes'] if s['id'] == 'D1|P1')
    assert s1['dropped_layers'] == {'PIPE': 10} and s1['restoration'] == sc['restoration'] and report['checks']['S-5']['pass']
    r = builder.build(out, tmp_path / 'page.html', feedback_url='')
    assert r['quantity_changes'] == 0


def test_dxf_symbol_reconciliation_counts_uncounted_and_duplicates(tmp_path):
    code, report, v, _ = dxf_convert(tmp_path / 'a')
    assert code == 0, report
    sc = next(s for s in v['scenes'] if s['id'] == 'D1|P1')
    assert sc['symbol_reconciliation'] == [{'block': 'LAMP', 'label': '吸顶灯', 'layer': 'E-LIGHT', 'on_sheet': 3, 'counted': 2, 'uncounted': 1, 'duplicates': 1,
                                            'uncounted_points': [[6000, 2000]], 'duplicate_points': [[2000, 2000]]}]
    assert next(s for s in report['scenes'] if s['id'] == 'D1|P1')['symbols'] == {'blocks': 1, 'on_sheet': 3, 'counted': 2, 'uncounted': 1, 'duplicates': 1}
    assert report['checks']['S-5']['object_handles'] == 2 and 'symbol_note' not in report['checks']['S-5']
    # No handle column: nothing is known as counted; symbol_layers still lists the blocks on that layer, all "uncounted".
    code, report, v, _ = dxf_convert(tmp_path / 'b', background={'large_block_points': 30, 'symbol_layers': ['^E-LIGHT$']}, with_handles=False)
    assert code == 0 and 'symbol_note' in report['checks']['S-5']
    rows = {r['block']: r for r in next(s for s in v['scenes'] if s['id'] == 'D1|P1')['symbol_reconciliation']}
    assert set(rows) == {'LAMP', 'SW'} and rows['LAMP']['counted'] == 0 and rows['LAMP']['uncounted'] == 3 and rows['SW']['on_sheet'] == 1


def test_dxf_symbol_notes_explain_removed_overlaps_other_sheets_and_same_items(tmp_path):
    # Contract 7.5.1. Fixture: L1 and L2 counted at one point (same position), L3 not counted.
    keys = ('on_sheet', 'counted', 'uncounted', 'duplicates', 'removed_overlap', 'counted_elsewhere', 'same_item_elsewhere')

    def shared(h, t):
        # The project's shared-facts overlap table read as is: only block rows, handle_b removed, handle_a kept; the line row must be skipped.
        write_csv(t / '重叠图元.csv', ['kind', 'sheet', 'layer', 'block', 'handle_a', 'handle_b', 'dup_len_m'],
                  [['块', 'P1', 'E-LIGHT', 'LAMP', h['L1'], h['L2'], '0.0'], ['线', 'P1', 'E-LIGHT', '', h['L1'], h['L3'], '0.0']])
        write_csv(t / '他图计入.csv', ['句柄', '说明'], [[h['L3'].lower(), '电力平面计入']])
        return [{'kind': 'removed_overlap', 'path': 'tables/重叠图元.csv', 'filter': {'kind': '^块$'}, 'columns': {'handle': 'handle_b', 'kept_handle': 'handle_a'},
                 'constants': {'drawing': 'D1', 'note': '与 {handle_a} 重叠，计数前剔除'}},
                {'kind': 'counted_elsewhere', 'path': 'tables/他图计入.csv', 'columns': {'handle': '句柄', 'note': '说明'}}]
    code, report, v, _ = dxf_convert(tmp_path / 'a', notes=shared, lamps=('L1',))      # the counted table is already deduplicated
    assert code == 0, report.get('problems')
    c.normalize_view(v)
    row = next(s for s in v['scenes'] if s['id'] == 'D1|P1')['symbol_reconciliation'][0]
    assert {k: row[k] for k in keys} == dict(zip(keys, (3, 1, 0, 0, 1, 1, 0)))
    assert row['removed_overlap_points'] == [[2000, 2000]] and row['counted_elsewhere_points'] == [[6000, 2000]] and row['same_item_elsewhere_points'] == []
    assert row['notes']['counted_elsewhere'] == ['电力平面计入'] and row['notes']['removed_overlap'][0].endswith('重叠，计数前剔除')
    assert [(t['kind'], t['rows'], t['used_on_these_sheets']) for t in report['checks']['S-5']['symbol_notes']] == [('removed_overlap', 1, 1), ('counted_elsewhere', 1, 1)]
    assert next(s for s in report['scenes'] if s['id'] == 'D1|P1')['symbols']['removed_overlap'] == 1

    def same_item(h, t):
        write_csv(t / '同一实物.csv', ['句柄', '说明'], [[h['L3'], '电力平面已计的同一个箱子']])
        return [{'kind': 'same_item_elsewhere', 'path': 'tables/同一实物.csv', 'columns': {'handle': '句柄', 'note': '说明'}}]
    code, report, v, _ = dxf_convert(tmp_path / 'b', notes=same_item)
    row = next(s for s in v['scenes'] if s['id'] == 'D1|P1')['symbol_reconciliation'][0]
    assert code == 0 and {k: row[k] for k in keys} == dict(zip(keys, (3, 2, 0, 1, 0, 0, 1)))   # unexplained same position stays

    def bad(h, t):
        write_csv(t / 'bad.csv', ['剔除', '保留', '说明', '类'], [[h['L2'], h['L2'], '自己', 'x'], [h['L3'], h['L1'], '', 'x']])
        write_csv(t / 'twice.csv', ['句柄', '说明'], [[h['L3'], '暖通计']])
        return [{'kind': 'removed_overlap', 'path': 'tables/bad.csv', 'columns': {'handle': '剔除', 'kept_handle': '保留', 'note': '说明'}},
                {'kind': 'counted_elsewhere', 'path': 'tables/twice.csv', 'columns': {'handle': '句柄', 'note': '说明'}},
                {'kind': 'same_item_elsewhere', 'path': 'tables/twice.csv', 'columns': {'handle': '句柄', 'note': '说明'}},
                {'kind': 'moved', 'path': 'tables/twice.csv'}]
    code, report, v, _ = dxf_convert(tmp_path / 'c', notes=bad)
    msgs = ' | '.join(x['message'] for x in report['problems'])
    assert code == 2 and v is None
    assert '剔除句柄与保留句柄相同' in msgs and '缺 note' in msgs and '不能再写进 same_item_elsewhere' in msgs and '对账说明 kind 只能是' in msgs


def symbol_rows(v, sid):
    return {r['block']: r for r in next(s for s in v['scenes'] if s['id'] == sid)['symbol_reconciliation']}


def test_dxf_symbols_are_reconciled_per_drawing_and_case_blind(tmp_path):
    # Codex V1-R2-H01: D2 is a copy of D1 with the same handles; only D1's table lists L1, L2 and the large block BIG.
    bg = {'large_block_points': 30, 'symbol_layers': ['^E-LIGHT$']}
    code, report, v, _ = dxf_convert(tmp_path / 'a', background=bg, lamps=('L1', 'L2', 'BIG'), second=())
    assert code == 0, report.get('problems')
    assert symbol_rows(v, 'D1|P1')['LAMP']['counted'] == 2 and 'BIG' in symbol_rows(v, 'D1|P1')
    d2 = symbol_rows(v, 'D2|P1')
    assert (d2['LAMP']['counted'], d2['LAMP']['uncounted']) == (0, 3) and 'BIG' not in d2
    # BIG counted on D1 only: drawn in full there (its pipes), still a large block on D2 (pipes dropped).
    pipes = lambda sid: any(i['kind'] == 'line' and i['points'][0][0] == 5000 and i['points'][-1][0] == 9000
                            for i in v['background_sets'][next(s for s in v['scenes'] if s['id'] == sid)['background_refs'][0]])
    assert pipes('D1|P1') and not pipes('D2|P1')
    # Both drawings list the lamps: both counted, each on its own drawing.
    code, report, v, _ = dxf_convert(tmp_path / 'b', background=bg, second=('L1', 'L2'))
    assert code == 0 and symbol_rows(v, 'D2|P1')['LAMP']['counted'] == 2 and symbol_rows(v, 'D1|P1')['LAMP']['counted'] == 2
    # Codex V1-R2-H02: object-table handles in lower case still count.
    code, report, v, _ = dxf_convert(tmp_path / 'c', hcase=str.lower)
    assert code == 0 and (symbol_rows(v, 'D1|P1')['LAMP']['counted'], symbol_rows(v, 'D1|P1')['LAMP']['uncounted']) == (2, 1)


def test_dxf_explained_symbols_never_make_same_position(tmp_path):
    # Codex V1-R2-H03: L1 and L2 are 0.4 mm apart; whichever one carries a note, the other is not a duplicate.
    keys = ('on_sheet', 'counted', 'uncounted', 'duplicates')
    for kind in ('counted_elsewhere', 'same_item_elsewhere', 'removed_overlap'):
        for who, other in (('L1', 'L2'), ('L2', 'L1')):
            def notes(h, t, kind=kind, who=who, other=other):
                write_csv(t / 'n.csv', ['句柄', '保留', '说明'], [[h[who], h[other], '说明']])
                return [{'kind': kind, 'path': 'tables/n.csv', 'columns': {'handle': '句柄', 'note': '说明', **({'kept_handle': '保留'} if kind == 'removed_overlap' else {})},
                         'constants': {'drawing': 'D1'}}]
            code, report, v, _ = dxf_convert(tmp_path / f'{kind}_{who}', notes=notes, lamps=('L3',))
            row = symbol_rows(v, 'D1|P1')['LAMP']
            assert code == 0 and {k: row[k] for k in keys} == dict(zip(keys, (3, 1, 1, 0))) and row[kind] == 1, (kind, who)
    # A counted symbol that also has a note is counted (the table wins) and is not a candidate either.
    def note_l1(h, t):
        write_csv(t / 'n.csv', ['句柄', '说明'], [[h['L1'], '电力平面计入']])
        return [{'kind': 'counted_elsewhere', 'path': 'tables/n.csv', 'columns': {'handle': '句柄', 'note': '说明'}, 'constants': {'drawing': 'D1'}}]
    code, report, v, _ = dxf_convert(tmp_path / 'counted', notes=note_l1, lamps=('L1', 'L3'))
    row = symbol_rows(v, 'D1|P1')['LAMP']
    assert code == 0 and {k: row[k] for k in keys} == dict(zip(keys, (3, 2, 1, 0))) and row['counted_elsewhere'] == 0


def test_dxf_removed_overlap_must_not_stay_a_counted_object(tmp_path):
    # Registration 0723 (contract 7.5.1): a removed handle still in the counted table is an S-5 conflict; the mapping must use the deduplicated table.
    def removed(h, t):
        write_csv(t / 'r.csv', ['剔除', '保留'], [[h['L2'], h['L1']]])
        return [{'kind': 'removed_overlap', 'path': 'tables/r.csv', 'columns': {'handle': '剔除', 'kept_handle': '保留'}, 'constants': {'drawing': 'D1', 'note': '计数前剔除'}}]
    code, report, v, _ = dxf_convert(tmp_path / 'in', notes=removed, lamps=('L1', 'L2'))
    assert code == 2 and v is None and any(p['check'] == 'S-5' and '已剔除的句柄仍在计数对象表里' in p['message'] for p in report['problems'])
    keys = ('on_sheet', 'counted', 'uncounted', 'duplicates', 'removed_overlap')
    code, report, v, _ = dxf_convert(tmp_path / 'out', notes=removed, lamps=('L1',))
    row = symbol_rows(v, 'D1|P1')['LAMP']
    assert code == 0 and {k: row[k] for k in keys} == dict(zip(keys, (3, 1, 1, 0, 1))) and row['uncounted_points'] == [[6000, 2000]]
    # The kept handle is missing from the table too: it shows as uncounted, nothing is hidden.
    code, report, v, _ = dxf_convert(tmp_path / 'kept', notes=removed, lamps=('L3',))
    row = symbol_rows(v, 'D1|P1')['LAMP']
    assert code == 0 and {k: row[k] for k in keys} == dict(zip(keys, (3, 1, 1, 0, 1))) and row['uncounted_points'] == [[2000, 2000]]


def test_dxf_hidden_layer_symbols_are_listed_apart(tmp_path):
    # A lamp on the switched-off layer is not drawn: out of the sheet count, listed as hidden; a counted object there is flagged.
    code, report, v, _ = dxf_convert(tmp_path, hidden_lamp=True, lamps=('L1', 'L2', 'LH'))
    assert code == 0, report.get('problems')
    c.normalize_view(v)
    row = symbol_rows(v, 'D1|P1')['LAMP']
    assert (row['on_sheet'], row['counted'], row['uncounted'], row['hidden'], row['hidden_counted'], row['hidden_points']) == (3, 2, 1, 1, 1, [[7000, 2000]])
    s = next(s for s in report['scenes'] if s['id'] == 'D1|P1')['symbols']
    assert (s['hidden'], s['hidden_counted']) == (1, 1)
    code, report, v, _ = dxf_convert(tmp_path / 'none')
    assert 'hidden' not in symbol_rows(v, 'D1|P1')['LAMP']


@pytest.mark.parametrize('sx,sy,rot,own', [(-2, 2, 30, 15), (2, -2, 15, 15), (2, 2, 30, 15), (1, 1, 0, 0), (-1.5, 1.5, 200, 40)])
def test_dxf_block_hatch_pattern_follows_the_insert_matrix(tmp_path, sx, sy, rot, own):
    # Codex V1-R2-H05: pattern lines of a hatch in a block are the definition's lines moved by the insert (mirror, rotation, scale, phase).
    import ezdxf
    from qto_dxf_background import build_dxf_backgrounds, hatch_lines
    doc = ezdxf.new('R2010'); blk = doc.blocks.new('H')
    h = blk.add_hatch(); h.set_pattern_fill('ANSI31', scale=10, angle=own); h.paths.add_polyline_path([(0, 0), (300, 0), (300, 200), (0, 200)], is_closed=True)
    ins = (5000, 4000)
    doc.modelspace().add_blockref('H', ins, dxfattribs={'xscale': sx, 'yscale': sy, 'rotation': rot})
    path = tmp_path / 'h.dxf'; doc.saveas(path)
    t = {'scene_id': 'S', 'sheet_bounds': [0, 0, 10000, 8000], 'dx': 0, 'dy': 0, 'include': [], 'exclude': [], 'symbol_layers': [], 'items': [], 'stats': __import__('collections').Counter(),
         'cfg': {'large_block_points': 2000, 'large_block_layers': [], 'flatten_mm': 1, 'duplicate_tolerance_mm': 1}}
    build_dxf_backgrounds(path, hashlib.sha256(path.read_bytes()).hexdigest(), [t], set(), 1, 3)
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    move = lambda p: (ins[0] + ca * sx * p[0] - sa * sy * p[1], ins[1] + sa * sx * p[0] + ca * sy * p[1])   # independent of ezdxf's matrix
    key = lambda p: (round(p[0], 1), round(p[1], 1))
    norm = lambda a, b: tuple(sorted(((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))), key=key))
    want = sorted((norm(move(a), move(b)) for a, b in hatch_lines(h)), key=lambda s: (key(s[0]), key(s[1])))
    got = sorted((norm(*it['points']) for it in t['items'] if it['kind'] == 'line'), key=lambda s: (key(s[0]), key(s[1])))
    assert len(got) == len(want) and all(math.dist(g[0], w[0]) < 0.05 and math.dist(g[1], w[1]) < 0.05 for g, w in zip(got, want))
    # Direction: ANSI31 is 45 degrees plus the hatch's own angle, mirrored and rotated with the insert.
    a0 = own + 45; ux, uy = math.cos(math.radians(a0)) * sx, math.sin(math.radians(a0)) * sy
    expect = (math.degrees(math.atan2(uy, ux)) + rot) % 180
    (x1, y1), (x2, y2) = got[0]
    assert abs(((math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180) - expect + 90) % 180 - 90) < 0.1


def test_dxf_background_inputs_must_match(tmp_path):
    code, report, v, _ = dxf_convert(tmp_path / 'sha', dxf_sha='0' * 64)
    assert code == 2 and v is None and any(p['check'] == 'S-5' and '哈希与取证清单不符' in p['message'] for p in report['problems'])
    code, report, v, _ = dxf_convert(tmp_path / 'file', dxf_path='nope.dxf')
    assert code == 2 and v is None and any(p['check'] == 'S-5' and '交换 DXF 不存在' in p['message'] for p in report['problems'])
    # No exchange DXF registered: no silent fall-back; the mapping must say source "neutral" to get the old background.
    nroot = neutral(tmp_path / 'none'); p, m = mapping(tmp_path / 'none', background={})
    code, summary, _ = run(SCRIPT, '--mapping', p, '--neutral-root', nroot, '--output', tmp_path / 'none' / 'v.json', '--report', tmp_path / 'none' / 'r.json')
    report = json.loads((tmp_path / 'none' / 'r.json').read_text(encoding='utf-8'))
    assert code == 2 and any(p['check'] == 'S-5' and 'source: "neutral"' in p['message'] for p in report['problems'])
    code, _, report, out = convert(tmp_path / 'neutral')
    v = json.loads(out.read_text(encoding='utf-8'))
    assert code == 0 and all(s['restoration']['source'] == 'neutral' and '块内内容' in s['restoration']['note'] for s in v['scenes'])
    assert report['checks']['S-5']['scenes_neutral_background'] == len(v['scenes']) and 'symbol_reconciliation' not in v['scenes'][0]


def test_contract_checks_restored_display_fields():
    v = fx.view(); del v['comparison']; v['schema'] = 'qto-review-view/1.0'
    s = v['scenes'][0]
    s['background'] = [{'kind': 'text', 'point': [0, 0], 'text': '轴', 'height': 350, 'align': 'center', 'baseline': 'bottom', 'width_factor': 0.8},
                       {'kind': 'polygon', 'points': [[0, 0], [1, 0], [1, 1]], 'color': '#112233', 'fill': '#112233'},
                       {'kind': 'line', 'points': [[0, 0], [1, 1]], 'dash': [5, 2.5]}]
    s['restoration'] = {'source': 'dxf', 'not_shown_total': 3, 'not_shown': {'other_layers': 2, 'pattern_hatch': 1}}
    s['symbol_reconciliation'] = [{'block': 'LAMP', 'label': '灯', 'layer': 'E', 'on_sheet': 2, 'counted': 1, 'uncounted': 1, 'duplicates': 0,
                                   'uncounted_points': [[1, 1]], 'duplicate_points': []}]
    s['length_factor'] = 0.05
    c.normalize_view(copy.deepcopy(v))
    bad = [(lambda w: w['scenes'][0]['background'][0].update(align='middle'), '对齐'),
           (lambda w: w['scenes'][0]['background'][0].update(height=0), '字高'),
           (lambda w: w['scenes'][0]['background'][2].update(fill='#112233'), '填充'),
           (lambda w: w['scenes'][0]['background'][2].update(dash=[-1]), '虚线'),
           (lambda w: w['scenes'][0]['restoration'].update(not_shown_total=9), '须回原图'),
           (lambda w: w['scenes'][0]['symbol_reconciliation'][0].update(counted=2), '符号对账'),
           (lambda w: w['scenes'][0]['symbol_reconciliation'][0].update(uncounted_points=[]), '符号对账'),
           (lambda w: w['scenes'][0].update(length_factor=0), '实长换算'),
           (lambda w: w['scenes'][0]['symbol_reconciliation'][0].update(removed_overlap=1), '符号对账说明类'),
           (lambda w: w['scenes'][0]['symbol_reconciliation'][0].update(hidden=2), '隐藏层符号')]
    for change, msg in bad:
        w = copy.deepcopy(v); change(w)
        with pytest.raises(ValueError, match=msg): c.normalize_view(w)
