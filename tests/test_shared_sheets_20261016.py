"""同号图页底图须全同（登记 20261016-1615 第二条）：合成报错列出差异字段和底图过滤条件的差集，--check-shared-sheets 只核不合成。

两包图面视图都由合成的中性布局转换而来，只差底图过滤条件；不含项目工程量。
"""
import copy, json, shutil
from pathlib import Path

import test_review_scene_mapping_20261016 as sm

SCRIPT = 'build_comparison_view_from_tables.py'
BG = {'source': 'neutral', 'layers_exclude': ['^DEFPOINTS$']}


def package(tmp_path, name, background=BG):
    """One package's scene view and scene conversion report, converted from the shared synthetic drawing."""
    d = tmp_path / name; d.mkdir()
    code, _, report, out = sm.convert(d, background=background)
    assert code == 0, report
    view, rep = tmp_path / f'{name}.json', tmp_path / f'{name}_图面转换检查.json'
    shutil.copy(out, view); shutil.copy(d / 'report.json', rep)
    return view, rep


def check(tmp_path, *views, reports=(), extra=()):
    rep = tmp_path / 'check.json'
    if rep.exists(): rep.unlink()
    args = [x for v in views for x in ('--base-view', v)] + [x for r in reports for x in ('--scene-report', r)]
    code, summary, err = sm.run(SCRIPT, '--check-shared-sheets', *args, '--report', rep, *extra)
    assert rep.exists(), err
    return code, summary, json.loads(rep.read_text(encoding='utf-8'))


def merge(tmp_path, *views, reports=()):
    (tmp_path / 'm').mkdir(exist_ok=True)
    m, _ = sm.tt.tables(tmp_path / 'm')
    out, rep = tmp_path / 'merged.json', tmp_path / 'merged_report.json'
    args = [x for v in views for x in ('--base-view', v)] + [x for r in reports for x in ('--scene-report', r)]
    code, _, _ = sm.tt.run(SCRIPT, '--mapping', m, '--output', out, '--report', rep, *args)
    return code, json.loads(rep.read_text(encoding='utf-8')), out


# ---------- counter-examples ----------

def test_check_lists_the_layer_only_one_package_excludes(tmp_path):
    a, ra = package(tmp_path, 'E-A')
    b, rb = package(tmp_path, 'E-B', {**BG, 'layers_exclude': ['^DEFPOINTS$', '^HATCH$']})
    code, summary, rep = check(tmp_path, a, b, reports=(ra, rb))
    assert code == 2 and rep['status'] == 'INPUT_REQUIRED' and rep['mode'] == 'check_shared_sheets'
    diff = [r for r in rep['shared_sheet_rows'] if not r['same']]
    assert diff and rep['shared_sheets'] == {'checked': len(rep['shared_sheet_rows']), 'same': len(rep['shared_sheet_rows']) - len(diff), 'different': len(diff)}
    assert summary['shared_sheets'] == rep['shared_sheets']
    for r in diff:
        assert r['views'] == ['E-A.json', 'E-B.json'] and 'background_refs' in r['fields']
        assert any('layers_exclude' in d and '只在 E-B.json：^HATCH$' in d and 'E-A.json：' not in d for d in r['details']), r['details']
    assert not (tmp_path / 'merged.json').exists()


def test_merge_error_names_the_differing_filter_when_scene_reports_are_given(tmp_path):
    a, ra = package(tmp_path, 'E-A')
    b, rb = package(tmp_path, 'E-B', {**BG, 'layers_exclude': []})
    code, rep, out = merge(tmp_path, a, b, reports=(ra, rb))
    assert code == 2 and not out.exists()
    assert '图页编号相同而内容不同' in rep['error'] and 'background_refs' in rep['error'] and '只在 E-A.json：^DEFPOINTS$' in rep['error'], rep['error']


def test_merge_error_without_scene_reports_says_how_to_get_the_difference(tmp_path):
    a, _ = package(tmp_path, 'E-A')
    b, _ = package(tmp_path, 'E-B', {**BG, 'layers_exclude': []})
    code, rep, out = merge(tmp_path, a, b)
    assert code == 2 and '不同的是' in rep['error'] and '加 --scene-report' in rep['error']


def test_same_filters_with_other_keys_point_at_the_drawing_hash(tmp_path):
    a, ra = package(tmp_path, 'E-A')
    view = json.loads(a.read_text(encoding='utf-8')); rep = json.loads(ra.read_text(encoding='utf-8'))
    for s in view['scenes']:
        s['background_refs'] = ['X' + k[1:] for k in s['background_refs']]
    view['background_sets'] = {'X' + k[1:]: v for k, v in view['background_sets'].items()}
    for s in rep['scenes']: s['background_key'] = 'X' + s['background_key'][1:]
    b, rb = tmp_path / 'E-B.json', tmp_path / 'E-B_图面转换检查.json'
    b.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8'); rb.write_text(json.dumps(rep, ensure_ascii=False), encoding='utf-8')
    code, _, out = check(tmp_path, a, b, reports=(ra, rb))
    details = [d for r in out['shared_sheet_rows'] for d in r.get('details', [])]
    assert code == 2 and details and all('哈希' in d for d in details), details
    code, _, out = check(tmp_path, a, b, reports=(ra,))
    assert code == 2 and all(any('找不到' in d for d in r['details']) for r in out['shared_sheet_rows'])


def test_bounds_difference_shows_both_values(tmp_path):
    a, ra = package(tmp_path, 'E-A')
    view = json.loads(a.read_text(encoding='utf-8')); view['scenes'][0]['bounds'] = [-1, *view['scenes'][0]['bounds'][1:]]
    b = tmp_path / 'E-B.json'; b.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')
    code, _, out = check(tmp_path, a, b, reports=(ra,))
    row = next(r for r in out['shared_sheet_rows'] if not r['same'])
    assert code == 2 and row['fields'] == ['bounds'] and row['details'][0].startswith('bounds [') and '与 [-1' in row['details'][0]


def test_check_mode_argument_errors(tmp_path):
    a, ra = package(tmp_path, 'E-A')
    code, _, out = check(tmp_path, a)
    assert code == 2 and '至少要两份 --base-view' in out['error']
    code, _, out = check(tmp_path, a, a, extra=('--output', tmp_path / 'x.json'))
    assert code == 2 and '不写 --mapping、--output' in out['error'] and not (tmp_path / 'x.json').exists()
    bad = tmp_path / 'bad.json'; bad.write_text('{"status": "PASS"}', encoding='utf-8')
    code, _, out = check(tmp_path, a, a, reports=(bad,))
    assert code == 2 and '图面转换检查报告没有 scenes' in out['error']
    code, _, err = sm.run(SCRIPT, '--base-view', a, '--report', tmp_path / 'r2.json')
    assert code == 2 and '要写 --mapping 和 --output' in json.loads((tmp_path / 'r2.json').read_text(encoding='utf-8'))['error']


# ---------- legal ----------

def test_identical_packages_pass_and_still_merge(tmp_path):
    a, ra = package(tmp_path, 'E-A')
    b, rb = package(tmp_path, 'E-B')
    code, summary, rep = check(tmp_path, a, b, reports=(ra, rb))
    assert code == 0 and rep['status'] == 'PASS' and rep['shared_sheets']['different'] == 0 and rep['shared_sheets']['checked'] >= 2
    assert all(r['same'] and 'fields' not in r for r in rep['shared_sheet_rows'])
    assert 'error' not in rep and json.loads(a.read_text(encoding='utf-8')) == json.loads(b.read_text(encoding='utf-8'))


def test_raster_paths_relative_to_each_view_compare_by_target(tmp_path):
    a, _ = package(tmp_path, 'E-A')
    (tmp_path / 'img').mkdir(); (tmp_path / 'img' / 'p1.png').write_bytes(b'png')
    (tmp_path / 'sub').mkdir()
    va = json.loads(a.read_text(encoding='utf-8')); vb = copy.deepcopy(va)
    va['scenes'][0]['raster_path'] = 'img/p1.png'; vb['scenes'][0]['raster_path'] = '../img/p1.png'
    a.write_text(json.dumps(va, ensure_ascii=False), encoding='utf-8')
    b = tmp_path / 'sub' / 'E-B.json'; b.write_text(json.dumps(vb, ensure_ascii=False), encoding='utf-8')
    code, _, rep = check(tmp_path, a, b)
    assert code == 0 and rep['shared_sheets']['different'] == 0, rep
    vb['scenes'][0]['raster_path'] = '../img/p2.png'; b.write_text(json.dumps(vb, ensure_ascii=False), encoding='utf-8')
    code, _, rep = check(tmp_path, a, b)
    assert code == 2 and next(r for r in rep['shared_sheet_rows'] if not r['same'])['fields'] == ['raster_path']


# ---------- shared objects (registration 1615 first part): one physical object carried by several packages ----------

def twin_package(tmp_path, change):
    """E-B carries the same objects as E-A under its own catalog keys; change(objects by id) edits E-B before it is written."""
    a, _ = package(tmp_path, 'E-A')
    v = json.loads(a.read_text(encoding='utf-8'))
    for o in v['objects']: o['catalog_key'] = 'B|' + o['catalog_key']
    change({o['id']: o for o in v['objects']}, v)
    b = tmp_path / 'E-B.json'; b.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    return a, b


Q = [{'name': '数量', 'value': 1, 'unit': '个', 'level': '规则推算量'}]


def test_shared_objects_report_counts_twins_double_quantities_and_positions_apart(tmp_path):
    def change(by, v):
        by['S1']['points'] = [[by['S1']['points'][0][0] + 0.5, by['S1']['points'][0][1]]]     # within 1 mm: same marker
        by['S2']['points'] = [[by['S2']['points'][0][0] + 5, by['S2']['points'][0][1]]]       # 5 mm apart: flagged
        by['F1']['quantities'] = Q
    a, b = twin_package(tmp_path, change)
    va = json.loads(a.read_text(encoding='utf-8'))
    next(o for o in va['objects'] if o['id'] == 'F1')['quantities'] = Q
    a.write_text(json.dumps(va, ensure_ascii=False), encoding='utf-8')
    code, rep, out = merge(tmp_path, a, b)
    assert code == 0, rep
    so, n, f1 = rep['shared_objects'], len(va['objects']), next(o['catalog_key'] for o in va['objects'] if o['id'] == 'F1')
    assert so['objects'] == n and so['records'] == 2 * n and so['by_record_count'] == {'2': n}
    assert so['multi_quantity'] == 1 and so['multi_quantity_rows'] == [{'object_id': 'F1', 'records': [f1, 'B|' + f1]}]
    assert '可能跨包重复计量' in so['warning']
    assert so['coordinate_mismatch'] == 1 and [(r['object_id'], r['positions']) for r in so['coordinate_mismatch_rows']] == [('S2', 2)]
    s2 = next(o for o in va['objects'] if o['id'] == 'S2'); assert so['coordinate_mismatch_rows'][0]['scene'] == s2['scene_id']


def test_shared_objects_other_sheet_or_kind_is_not_a_position_mismatch(tmp_path):
    other = {}
    def change(by, v):
        other['scene'] = next(s['id'] for s in v['scenes'] if s['id'] != by['S1']['scene_id'])
        by['S1'].update(scene_id=other['scene'], points=[[0, 0]])                              # same object on another sheet (system diagram vs plan)
        by['S2'].update(kind='line', points=[by['S2']['points'][0], [by['S2']['points'][0][0] + 900, by['S2']['points'][0][1]]])
    a, b = twin_package(tmp_path, change)
    code, rep, _ = merge(tmp_path, a, b)
    so = rep['shared_objects']
    assert code == 0 and so['coordinate_mismatch'] == 0 and so['multi_quantity'] == 0 and 'warning' not in so and 'coordinate_mismatch_rows' not in so


def test_single_package_has_no_shared_objects(tmp_path):
    a, _ = package(tmp_path, 'E-A')
    code, rep, _ = merge(tmp_path, a)
    assert code == 0 and rep['shared_objects'] == {'objects': 0, 'records': 0, 'by_record_count': {}, 'multi_quantity': 0, 'coordinate_mismatch': 0,
                                                   'suspected_handle_collisions': 0}


def test_views_without_shared_sheets_pass_with_nothing_checked(tmp_path):
    a, _ = package(tmp_path, 'E-A')
    v = json.loads(a.read_text(encoding='utf-8'))
    for s in v['scenes']: s['id'] = s['id'].replace('D1|', 'D2|')
    b = tmp_path / 'E-B.json'; b.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    code, _, rep = check(tmp_path, a, b)
    assert code == 0 and rep['shared_sheets'] == {'checked': 0, 'same': 0, 'different': 0}
