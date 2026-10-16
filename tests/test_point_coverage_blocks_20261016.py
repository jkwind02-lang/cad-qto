"""d2 覆盖台账：块内候选、按图形定图页、按图页类型排除，并入 1800 三条
（登记 20261016-1709 中性来源只认顶层、1713、1728、1800 整类排除示例、详图页、图例异名）。

合成图（去标识，单位 mm）：
- 平面页 P1（0,0～10000,10000）：楼层块 FLOOR（内含 3 个端子箱 TB）、远基点块 ESD（基点在 x=2e6，图形在 P1 内）、
  箱框 BOXF×2、匿名名块 A$C0F17597C、轴号 AXIS、GPB×2、GPBX、LAMP；
- 图例页 LG（20000～30000）：TB、ESD（基点同样很远，图形落在 LG）、XREF$0$GPB、GPBL、A$C0F17597C 各一个；
- 系统图页 SYS（40000～50000）：BOXF×2；详图页 DT（60000～70000）：LAMP×1。
期望值按图面布置写出；反向核对用 ezdxf 独立列块内句柄，不用台账自己的数。
"""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
POINTS = ROOT / 'skills/qto-components-points/scripts'
SQUARE = [(-50, -50), (50, -50), (50, 50), (-50, 50)]
FAR = 2_000_000
TOP = {'FLOOR': ('FLOOR', (0, 0)), 'ESD_P': ('ESD', (FAR, 0)), 'BOXF1': ('BOXF', (5000, 2000)), 'BOXF2': ('BOXF', (6000, 2000)),
       'ANON': ('A$C0F17597C', (7000, 2000)), 'AXIS': ('AXIS', (8000, 2000)), 'GPB1': ('GPB', (1000, 6000)),
       'GPB2': ('GPB', (2000, 6000)), 'GPBX': ('GPBX', (3000, 6000)), 'LAMP': ('LAMP', (4000, 6000)),
       'LG_TB': ('TB', (21000, 9000)), 'ESD_L': ('ESD', (FAR + 20000, 0)), 'LG_GPB': ('XREF$0$GPB', (21000, 8000)),
       'LG_GPBL': ('GPBL', (21000, 7000)), 'LG_ANON': ('A$C0F17597C', (21000, 6000)),
       'SYS1': ('BOXF', (41000, 1000)), 'SYS2': ('BOXF', (42000, 1000)), 'DT_LAMP': ('LAMP', (61000, 1000))}
SHEETS = [['P1', '平面', 0, 0, 10000, 10000], ['LG', '图例', 20000, 0, 30000, 10000],
          ['SYS', '系统图', 40000, 0, 50000, 10000], ['DT', '详图', 60000, 0, 70000, 10000]]
LEGEND_FIELDS = ('drawing', 'legend_row', 'legend_handle', 'legend_text', 'symbol', 'symbol_kind', 'matched_symbols',
                 'plan_count', 'count_source', 'variant_flag', 'destination', 'target_or_reason', 'suspected_category',
                 'search_record_id')


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return path


@pytest.fixture(scope='module')
def drawing(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('blocks')
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in ('TB', 'BOXF', 'A$C0F17597C', 'AXIS', 'GPB', 'XREF$0$GPB', 'GPBL', 'GPBX', 'LAMP'):
        doc.blocks.new(name).add_lwpolyline(SQUARE, close=True)
    esd = doc.blocks.new('ESD')   # base point (0,0); the drawing sits ~2e6 away from it (登记 1713)
    esd.add_lwpolyline([(x - FAR + 5000, y + 5000) for x, y in SQUARE], close=True)
    esd.add_attdef('NO', (-FAR + 5000, 5100))
    floor = doc.blocks.new('FLOOR')
    inner = [floor.add_blockref('TB', (1000 * i, 1000)).dxf.handle for i in (1, 2, 3)]
    msp = doc.modelspace()
    h = {key: msp.add_blockref(block, at).dxf.handle for key, (block, at) in TOP.items()}
    dxf = tmp / 'blocks.dxf'; doc.saveas(dxf)
    neutral = tmp / 'neutral'
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert done.returncode == 0, done.stdout + done.stderr
    write(tmp / 'sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], SHEETS)
    # reverse check source: the FLOOR children straight from the DXF, not from the ledger
    read = ezdxf.readfile(dxf)
    children = [e.dxf.handle for e in read.blocks.get('FLOOR') if e.dxftype() == 'INSERT']
    assert children == inner
    paths = [f"PT-CAD-{h['FLOOR'].upper()}/FLOOR/CAD-{c.upper()}" for c in inner]
    return {'dir': tmp, 'dxf': dxf, 'neutral': neutral, 'h': h, 'inner': inner, 'paths': paths}


def base_input(d, tmp, count_rows=None, legend_rows=None, drawing_extra=None, **extra):
    tmp.mkdir(parents=True, exist_ok=True)
    h = d['h']
    rows = count_rows if count_rows is not None else [[h['GPB1'], '端子板'], [h['GPB2'], '端子板'], [h['LAMP'], '灯']]
    write(tmp / 'count.csv', ['handle', 'cat'], rows)
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'scope_id': '合成',
            'drawings': [{'drawing': 'D1', 'neutral_dir': str(d['neutral']), **(drawing_extra or {})}],
            'count_tables': [{'path': 'count.csv', 'constants': {'drawing': 'D1'}, 'columns': {'handle': 'handle', 'category': 'cat'}}],
            'sheets': [{'path': str(d['dir'] / 'sheets.csv'), 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}],
            **extra}
    if legend_rows is not None:
        with open(tmp / 'legend.csv', 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=LEGEND_FIELDS); w.writeheader()
            for row in legend_rows:
                w.writerow({k: row.get(k, '') for k in LEGEND_FIELDS})
        data['legend_coverage'] = 'legend.csv'
    return data


def ledger(tmp, data):
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / 'input.json'; p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    done = run(POINTS / 'build_point_coverage_ledger.py', '--input', p, '--output-dir', tmp / 'out')
    if done.returncode:
        return done, None, None
    report = json.loads((tmp / 'out/点位覆盖台账.json').read_text(encoding='utf-8'))
    with open(tmp / 'out/图例覆盖表_待填.csv', encoding='utf-8-sig', newline='') as f:
        legend = list(csv.DictReader(f))
    return done, report, legend


def problems(done):
    assert done.returncode == 2, done.stdout
    return [p['message'] for p in json.loads(done.stderr)['problems']]


def where(report, symbol, sheet=None):
    """{destination: count} for a symbol, optionally on one sheet."""
    out = {}
    for g in report['groups']:
        if g['symbol'] == symbol and (sheet is None or g['sheet'] == sheet):
            out[g['destination']] = out.get(g['destination'], 0) + g['count']
    return out


def legend_of(legend, symbol):
    return next(r for r in legend if r['symbol'] == symbol)


# ---------- block-internal candidates (1709) ----------

def test_inner_boxes_are_candidates_and_counted_by_path_id(drawing, tmp_path):
    rows = [[p, '端子箱'] for p in drawing['paths']]
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, count_rows=rows, drawing_extra={'expand_blocks': ['FLOOR']}))
    groups = [g for g in report['groups'] if g['parent_block'] == 'FLOOR']
    assert sum(g['count'] for g in groups) == 3 and all(g['destination'] == '已计' and g['sheet'] == 'P1' for g in groups)
    assert where(report, 'FLOOR') == {'有据排除': 1}
    assert report['expanded_containers'] == {'D1': {'FLOOR': 3}}
    assert 'unexpanded_container_blocks' not in report['warnings']
    # reverse check: every DXF child of FLOOR is a candidate exactly once, under the adapter's path id
    ids = [i for g in report['groups'] for i in g['sample_ids'] if '/' in i]
    assert sorted(ids) == sorted(drawing['paths'])
    top_inserts = sum(1 for e in ezdxf.readfile(drawing['dxf']).modelspace() if e.dxftype() == 'INSERT')
    assert report['candidate_total'] == top_inserts + 3 == sum(report['by_destination'].values())


def test_without_expand_the_container_is_reported_and_path_ids_are_refused(drawing, tmp_path):
    _, report, _ = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a'))
    warn = report['warnings']['unexpanded_container_blocks']
    assert [(w['block_name'], w['top_level_insert_count'], w['countable_inserts_per_instance']) for w in warn] == [('FLOOR', 1, 3)]
    assert warn[0]['symbols'] == {'TB': 3}
    done, _, _ = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', count_rows=[[drawing['paths'][0], '端子箱']]))
    assert any('未在 expand_blocks 声明展开' in m for m in problems(done))


def test_legal_without_countable_names_no_container_warning(drawing, tmp_path):
    # without the legend sheet and count table nothing marks TB as countable: no warning
    data = base_input(drawing, tmp_path, count_rows=[])
    data['sheets'] = []
    _, report, _ = ledger(tmp_path, data)
    assert 'unexpanded_container_blocks' not in report['warnings']


@pytest.mark.parametrize('case', ['lower_prefix', 'backslash', 'missing_level', 'block_case', 'wrong_handle', 'bare_inner',
                                  'counted_container', 'duplicate_case', 'top_missing'])
def test_bad_path_ids_are_input_problems(drawing, tmp_path, case):
    p, h = drawing['paths'][0], drawing['h']
    top, inner = h['FLOOR'].upper(), drawing['inner'][0].upper()
    rows = {'lower_prefix': [[p.replace('PT-CAD-', 'pt-cad-'), '端子箱']],
            'backslash': [[p.replace('/', '\\'), '端子箱']],
            'missing_level': [[f'PT-CAD-{top}/FLOOR', '端子箱']],
            'block_case': [[p.replace('/FLOOR/', '/floor/'), '端子箱']],
            'wrong_handle': [[f'PT-CAD-{top}/FLOOR/CAD-FFFFF', '端子箱']],
            'bare_inner': [[inner, '端子箱']],
            'counted_container': [[h['FLOOR'], '端子箱'], [p, '端子箱']],
            'duplicate_case': [[p, '端子箱'], [p.replace(inner, inner.lower()), '端子箱']],
            'top_missing': [['PT-CAD-FFFFF/FLOOR/CAD-1', '端子箱']]}[case]
    done, _, _ = ledger(tmp_path, base_input(drawing, tmp_path, count_rows=rows, drawing_extra={'expand_blocks': ['FLOOR']}))
    want = {'lower_prefix': '格式不对', 'backslash': '格式不对', 'missing_level': '格式不对', 'block_case': '大小写',
            'wrong_handle': '查不到', 'bare_inner': '写路径编号', 'counted_container': '容器本身不能再进计数表',
            'duplicate_case': '出现两次', 'top_missing': '顶层句柄在中性布局里查不到'}[case]
    assert any(want in m for m in problems(done)), problems(done)


def test_lower_case_hex_in_a_path_id_is_the_same_instance(drawing, tmp_path):
    p, inner = drawing['paths'][0], drawing['inner'][0].upper()
    rows = [[p.replace(inner, inner.lower()), '端子箱']]
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, count_rows=rows, drawing_extra={'expand_blocks': ['FLOOR']}))
    assert where(report, 'TB', 'P1') == {'已计': 1, '待识别': 2}


@pytest.mark.parametrize('extra, want', [({'expand_blocks': ['NOFLOOR']}, '块定义里没有这个块'),
                                         ({'expand_blocks': ['floor']}, '块定义里没有这个块'),
                                         ({'expand_blocks': []}, '非空的精确块名数组'),
                                         ({'expand_blocks': 'FLOOR'}, '非空的精确块名数组')])
def test_bad_expand_blocks_are_input_problems(drawing, tmp_path, extra, want):
    done, _, _ = ledger(tmp_path, base_input(drawing, tmp_path, drawing_extra=extra))
    assert any(want in m for m in problems(done))


def test_expand_blocks_on_a_report_drawing_is_refused(drawing, tmp_path):
    data = base_input(drawing, tmp_path)
    data['drawings'] = [{'drawing': 'D1', 'reports': ['x.json'], 'expand_blocks': ['FLOOR']}]
    done, _, _ = ledger(tmp_path, data)
    assert any('只用于“中性清单 + 计数表”来源' in m for m in problems(done))


def test_inner_insert_without_any_matrix_is_an_input_problem(drawing, tmp_path):
    import shutil
    neutral = tmp_path / 'neutral'; shutil.copytree(drawing['neutral'], neutral)
    manifest = json.loads((neutral / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    stream = neutral / Path(manifest['artifacts']['block_definitions']['path']).name
    rows = [json.loads(x) for x in stream.read_text(encoding='utf-8').splitlines() if x.strip()]
    for row in rows:
        if row.get('entity_handle') == drawing['inner'][0]:
            row['insert_matrix_wcs'] = None; row['extrusion'] = [0, 0, -1]   # an old cache: no 3D transform to fall back on
    stream.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    manifest['artifacts']['block_definitions'].update(path=str(stream), sha256=sha(stream))
    manifest['artifacts']['layout_entities']['path'] = str(neutral / Path(manifest['artifacts']['layout_entities']['path']).name)
    (neutral / 'CAD中性取证清单.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    data = base_input(drawing, tmp_path, drawing_extra={'expand_blocks': ['FLOOR']})
    data['drawings'][0]['neutral_dir'] = str(neutral)
    done, _, _ = ledger(tmp_path, data)
    assert any('缺句柄或变换矩阵' in m for m in problems(done))


# ---------- sheet from geometry (1713) ----------

def test_far_base_point_block_is_placed_by_its_geometry(drawing, tmp_path):
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path))
    assert where(report, 'ESD') == {'待识别': 1, '有据排除': 1}
    assert where(report, 'ESD', 'P1') == {'待识别': 1}                    # plan instance, not 图页未定
    assert where(report, 'ESD', 'LG') == {'有据排除': 1}                  # legend copy found by geometry, auto-excluded
    mismatch = {w['id']: w for w in report['warnings']['insert_geometry_sheet_mismatch']}
    p = mismatch['PT-CAD-' + drawing['h']['ESD_P'].upper()]
    assert (p['insert_sheet'], p['geometry_sheet']) == ('图页未定', 'P1')
    assert mismatch['PT-CAD-' + drawing['h']['ESD_L'].upper()]['geometry_sheet'] == 'LG'
    assert len(mismatch) == 2                                            # symmetric blocks: insert and geometry agree
    # ESD holds an ATTDEF: since K3 (20261017-0031) it counts at its insertion point and the extent is marked incomplete
    assert report['placement_basis_counts']['图形范围中心'] == len(TOP) - 2
    assert report['placement_basis_counts']['图形范围中心（范围不完整：未含填充或注记按插入点计）'] == 2


def test_report_source_uses_the_adapter_geometry_box(tmp_path):
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.blocks.new('ESDN').add_lwpolyline([(x - FAR + 5000, y + 5000) for x, y in SQUARE], close=True)
    handle = doc.modelspace().add_blockref('ESDN', (FAR, 0)).dxf.handle
    dxf = tmp_path / 'a.dxf'; doc.saveas(dxf); src = sha(dxf)
    assert run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp_path / 'n').returncode == 0
    ledger_doc = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1', 'source_sha256': src,
                  'adoption_scope': 'working', 'review_status': 'unreviewed', 'evidence': [], 'entries': []}
    lp = tmp_path / 'ledger.json'; lp.write_text(json.dumps(ledger_doc), encoding='utf-8')
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model'}, 'unit': {'factor_to_output': .001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': []}
    pp = tmp_path / 'profile.json'; pp.write_text(json.dumps(profile), encoding='utf-8')
    done = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', tmp_path / 'n/CAD中性取证清单.json',
               '--profile', pp, '--adjudication-ledger', lp, '--output-dir', tmp_path / 'out')
    assert done.returncode == 0, done.stderr
    write(tmp_path / 'sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], SHEETS)
    data = {'schema_version': 'qto-point-coverage-input/1.0',
            'drawings': [{'drawing': 'D1', 'reports': [str(tmp_path / 'out/点位识别报告.json')]}],
            'sheets': [{'path': 'sheets.csv', 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}]}
    _, report, _ = ledger(tmp_path, data)
    assert where(report, 'ESDN', 'P1') == {'待识别': 1}
    assert report['warnings']['insert_geometry_sheet_mismatch'][0]['id'] == f'PT-CAD-{handle}'


# ---------- sheet kinds (1728, 1800) ----------

def test_default_keeps_legend_auto_exclusion_only(drawing, tmp_path):
    _, report, legend = ledger(tmp_path, base_input(drawing, tmp_path))
    assert report['auto_exclude_sheet_kinds'] == ['图例']
    assert where(report, 'BOXF', 'SYS') == {'待识别': 2}                  # 系统图 off by default
    assert report['auto_excluded_by_sheet_kind'] == {'图例': 5}          # TB, ESD, XREF$0$GPB, GPBL, A$C… on LG
    lg = [g for g in report['groups'] if g['sheet'] == 'LG']
    assert all(g['category_or_basis'] == '图例页上的复制件（A10）' for g in lg)


def test_system_sheets_auto_excluded_when_the_project_opens_it(drawing, tmp_path):
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, auto_exclude_sheet_kinds=['图例', '系统图']))
    assert where(report, 'BOXF', 'SYS') == {'有据排除': 2} and where(report, 'BOXF', 'P1') == {'待识别': 2}
    sys_groups = [g for g in report['groups'] if g['sheet'] == 'SYS']
    assert sys_groups[0]['category_or_basis'] == '系统图页上的示意，不单独计数（auto_exclude_sheet_kinds）'
    assert report['auto_excluded_by_sheet_kind'] == {'图例': 5, '系统图': 2}


def test_class_rule_limited_to_system_sheets_hits_only_those(drawing, tmp_path):
    classes = [{'class_id': 'EX-SYS', 'drawing': 'D1', 'block_pattern': '^BOXF$', 'sheet_kind': '系统图', 'basis': '系统图上的箱框'}]
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, excluded_classes=classes))
    assert where(report, 'BOXF', 'SYS') == {'有据排除': 2} and where(report, 'BOXF', 'P1') == {'待识别': 2}


@pytest.mark.parametrize('value, want', [(['详图'], 'detail_copy_sheets'), (['图例', '详图'], 'detail_copy_sheets'),
                                         (['平面'], '只能是 图例、系统图'), ('系统图', '须是图页类型的数组'), ([1], '须是图页类型的数组')])
def test_bad_auto_exclude_sheet_kinds_are_input_problems(drawing, tmp_path, value, want):
    done, _, _ = ledger(tmp_path, base_input(drawing, tmp_path, auto_exclude_sheet_kinds=value))
    assert any(want in m for m in problems(done))


@pytest.mark.parametrize('kind, want', [('详图', '不能按详图页限定'), ('楼层', 'sheet_kind 只能是')])
def test_bad_class_sheet_kind_is_an_input_problem(drawing, tmp_path, kind, want):
    classes = [{'class_id': 'EX', 'drawing': 'D1', 'block_pattern': '^LAMP$', 'sheet_kind': kind, 'basis': '试'}]
    done, _, _ = ledger(tmp_path, base_input(drawing, tmp_path, excluded_classes=classes))
    assert any(want in m for m in problems(done))


def test_detail_sheet_pending_stays_until_confirmed(drawing, tmp_path):
    h = drawing['h']
    rows = [[h['GPB1'], '端子板'], [h['GPB2'], '端子板']]            # LAMP not counted, so the detail copy is pending
    _, report, _ = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a', count_rows=rows))
    assert where(report, 'LAMP', 'DT') == {'待识别': 1}
    g = next(g for g in report['groups'] if g['sheet'] == 'DT')
    assert '详图页，可能是复制件，确认后写 detail_copy_sheets' in g['category_or_basis'] and g['suspected_category'] == ''
    assert report['unconfirmed_detail_sheets'] == [{'drawing': 'D1', 'sheet': 'DT', 'pending_count': 1}]
    assert '详图页，可能是复制件' not in report['class_completeness']   # the hint is not a fake class
    confirmed = [{'drawing': 'D1', 'sheet_id': 'DT', 'basis': '与 P1 一层照明同一套灯，详图只放大表达'}]
    _, report, _ = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', count_rows=rows, detail_copy_sheets=confirmed))
    assert where(report, 'LAMP', 'DT') == {'有据排除': 1}
    g = next(g for g in report['groups'] if g['sheet'] == 'DT')
    assert g['category_or_basis'] == '详图页上的复制件（已确认：与 P1 一层照明同一套灯，详图只放大表达）'
    assert report['auto_excluded_by_sheet_kind']['详图（已确认）'] == 1 and report['unconfirmed_detail_sheets'] == []


def test_counted_on_detail_sheet_is_a_warning_not_a_change(drawing, tmp_path):
    h = drawing['h']
    rows = [[h['DT_LAMP'], '灯']]
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, count_rows=rows))
    assert where(report, 'LAMP', 'DT') == {'已计': 1}
    assert report['warnings']['counted_on_legend_or_detail_sheet'][0]['sheet'] == 'DT'


@pytest.mark.parametrize('spec, want', [({'drawing': 'D1', 'sheet_id': 'NOPE', 'basis': 'x'}, '图页表里没有'),
                                        ({'drawing': 'D1', 'sheet_id': 'P1', 'basis': 'x'}, '不是详图'),
                                        ({'drawing': 'D1', 'sheet_id': 'SYS', 'basis': 'x'}, '不是详图'),
                                        ({'drawing': 'D1', 'sheet_id': 'DT', 'basis': ' '}, '须写 drawing、sheet_id、basis'),
                                        ({'drawing': 'D9', 'sheet_id': 'DT', 'basis': 'x'}, '不在 drawings 里')])
def test_bad_detail_copy_sheets_are_input_problems(drawing, tmp_path, spec, want):
    done, _, _ = ledger(tmp_path, base_input(drawing, tmp_path, detail_copy_sheets=[spec]))
    assert any(want in m for m in problems(done))


def test_detail_copy_sheet_confirmed_twice_is_an_input_problem(drawing, tmp_path):
    spec = {'drawing': 'D1', 'sheet_id': 'DT', 'basis': 'x'}
    done, _, _ = ledger(tmp_path, base_input(drawing, tmp_path, detail_copy_sheets=[spec, spec]))
    assert any('重复确认' in m for m in problems(done))


# ---------- excluded class vs legend (1800 #1) ----------

def test_class_rule_hitting_a_legend_symbol_warns_but_keeps_destinations(drawing, tmp_path):
    classes = [{'class_id': 'EX-ANON', 'drawing': '*', 'block_pattern': '^A\\$C', 'basis': '匿名底图块'},
               {'class_id': 'EX-AXIS', 'drawing': '*', 'block_pattern': '^AXIS$', 'basis': '轴号'}]
    _, report, legend = ledger(tmp_path, base_input(drawing, tmp_path, excluded_classes=classes))
    warn = report['warnings']['excluded_class_hits_legend_symbol']
    anon_row = legend_of(legend, 'A$C0F17597C')['legend_row']
    assert [(w['class_id'], w['symbol'], w['legend_rows']) for w in warn] == [('EX-ANON', 'A$C0F17597C', [anon_row])]
    assert where(report, 'A$C0F17597C', 'P1') == {'有据排除': 1}          # hint only, destination unchanged
    assert where(report, 'AXIS', 'P1') == {'有据排除': 1}


def test_legal_class_rule_on_non_legend_blocks_does_not_warn(drawing, tmp_path):
    classes = [{'class_id': 'EX-AXIS', 'drawing': '*', 'block_pattern': '^AXIS$', 'basis': '轴号'}]
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, excluded_classes=classes))
    assert 'excluded_class_hits_legend_symbol' not in report['warnings']


@pytest.mark.parametrize('dest', ['不计', '归他专业', '已计', '待识别', ''])
def test_class_hint_stays_whatever_the_legend_row_says(drawing, tmp_path, dest):
    # 不计 is often filled because the rule already excluded the plan blocks (real project: legend 暗装检测盒 A$C17F23191,
    # reason "平面同名块全部有据排除：EX-TESTBOX"), so the destination does not silence the hint.
    classes = [{'class_id': 'EX-ANON', 'drawing': '*', 'block_pattern': '^A\\$C', 'basis': '匿名底图块'}]
    _, _, legend = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a', excluded_classes=classes))
    row = {**legend_of(legend, 'A$C0F17597C'), 'destination': dest, 'target_or_reason': '照明' if dest == '已计' else ('x' if dest else ''),
           'search_record_id': 'S-1'}
    _, report, _ = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', excluded_classes=classes, legend_rows=[row]))
    assert [w['symbol'] for w in report['warnings']['excluded_class_hits_legend_symbol']] == ['A$C0F17597C']
    assert where(report, 'A$C0F17597C', 'P1') == {'有据排除': 1}


def test_class_rule_hitting_only_the_legend_sample_does_not_warn(drawing, tmp_path):
    # GPBL is shown only on the legend sheet; excluding that sample by name sweeps nothing away from the plans.
    classes = [{'class_id': 'EX-GPBL', 'drawing': '*', 'block_pattern': '^GPBL$', 'basis': '图例样例'}]
    _, report, _ = ledger(tmp_path, base_input(drawing, tmp_path, excluded_classes=classes))
    assert 'excluded_class_hits_legend_symbol' not in report['warnings']


# ---------- legend rows: xref prefix and matched_symbols (1800 #3) ----------

def load_module():
    spec = importlib.util.spec_from_file_location('ledger_mod', POINTS / 'build_point_coverage_ledger.py')
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode, before = True, sys.dont_write_bytecode   # no __pycache__ inside skills/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


@pytest.mark.parametrize('name, base', [('XREF$0$GPB', 'GPB'), ('A$0$B$0$C', 'C'), ('X$12$Y', 'Y'), ('GPB', 'GPB'),
                                        ('A$C0F17597C', 'A$C0F17597C'), ('$equip$00002540', '$equip$00002540'), ('*U12', '*U12')])
def test_xref_prefix_is_stripped_only_for_bound_xref_names(name, base):
    assert load_module().base_symbol(name) == base


def test_legend_xref_block_matches_the_plan_block(drawing, tmp_path):
    _, report, legend = ledger(tmp_path, base_input(drawing, tmp_path))
    row = legend_of(legend, 'XREF$0$GPB')
    assert row['plan_count'] == '2' and row['destination'] == '已计' and row['target_or_reason'] == '端子板'


def test_renamed_plan_block_matched_by_matched_symbols(drawing, tmp_path):
    _, _, first = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a'))
    row = dict(legend_of(first, 'GPBL'))
    assert row['plan_count'] == '0'                                     # same shape, other name: not matched by itself
    row.update(matched_symbols='GPBX', destination='待识别', suspected_category='端子板B')
    _, report, legend = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', legend_rows=[row]))
    out = legend_of(legend, 'GPBL')
    assert out['plan_count'] == '1' and out['count_source'] == 'matched_symbols' and out['matched_symbols'] == 'GPBX'
    assert not any(p['legend_row'] == row['legend_row'] and '实例数为 0' in p['reason']
                   for p in report['legend_rows_without_destination'])
    gpbx = next(g for g in report['groups'] if g['symbol'] == 'GPBX')
    assert gpbx['suspected_category'] == '端子板B'                       # suspected class reaches the matched block


def test_two_rows_claiming_one_block_is_a_conflict(drawing, tmp_path):
    _, _, first = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a'))
    a = dict(legend_of(first, 'GPBL'), matched_symbols='GPBX', destination='待识别')
    b = dict(legend_of(first, 'A$C0F17597C'), matched_symbols='GPBX', destination='待识别')
    _, report, _ = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', legend_rows=[a, b]))
    conflict = report['warnings']['matched_symbol_conflicts']
    assert conflict == [{'drawing': 'D1', 'symbol': 'GPBX', 'legend_rows': sorted([a['legend_row'], b['legend_row']])}]
    reasons = {p['legend_row']: p['reason'] for p in report['legend_rows_without_destination']}
    assert '冲突' in reasons[a['legend_row']] and '冲突' in reasons[b['legend_row']]
    assert report['coverage_complete'] is False


def test_claiming_another_rows_own_legend_block_is_a_conflict(drawing, tmp_path):
    _, _, first = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a'))
    a = dict(legend_of(first, 'GPBL'), matched_symbols='GPB', destination='待识别')   # GPB belongs to the XREF$0$GPB row
    _, report, _ = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', legend_rows=[a]))
    rows = report['warnings']['matched_symbol_conflicts'][0]['legend_rows']
    assert a['legend_row'] in rows and legend_of(first, 'XREF$0$GPB')['legend_row'] in rows


@pytest.mark.parametrize('value, want', [('NOPE', 'NOPE'), ('@NOPE', '@NOPE'), ('GPBX；NOPE2', 'NOPE2')])
def test_matched_symbols_naming_nothing_is_an_input_problem(drawing, tmp_path, value, want):
    _, _, first = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a'))
    row = dict(legend_of(first, 'GPBL'), matched_symbols=value)
    done, _, _ = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', legend_rows=[row]))
    msgs = problems(done)
    assert any('matched_symbols 写了本图没有的块名' in m and want in m for m in msgs)


def test_one_to_one_category_gives_a_default_match(drawing, tmp_path):
    h = drawing['h']
    rows = [[h['GPB1'], '端子板'], [h['GPB2'], '端子板'], [h['GPBX'], '端子板B']]
    _, _, first = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a', count_rows=rows))
    row = dict(legend_of(first, 'GPBL'), destination='已计', target_or_reason='端子板B')
    _, report, legend = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', count_rows=rows, legend_rows=[row]))
    out = legend_of(legend, 'GPBL')
    assert (out['matched_symbols'], out['plan_count'], out['count_source']) == ('GPBX', '1', '类别一对一默认')
    assert not any(p['legend_row'] == row['legend_row'] for p in report['legend_rows_without_destination'])


def test_one_to_many_category_gets_no_default(drawing, tmp_path):
    h = drawing['h']
    rows = [[h['GPB1'], '端子板'], [h['GPB2'], '端子板'], [h['GPBX'], '端子板B']]
    _, _, first = ledger(tmp_path / 'a', base_input(drawing, tmp_path / 'a', count_rows=rows))
    a = dict(legend_of(first, 'GPBL'), destination='已计', target_or_reason='端子板B')
    b = dict(legend_of(first, 'A$C0F17597C'), destination='已计', target_or_reason='端子板B')
    _, report, legend = ledger(tmp_path / 'b', base_input(drawing, tmp_path / 'b', count_rows=rows, legend_rows=[a, b]))
    out = legend_of(legend, 'GPBL')
    assert out['matched_symbols'] == '' and out['plan_count'] == '0'
    reasons = {p['legend_row']: p['reason'] for p in report['legend_rows_without_destination']}
    assert reasons[a['legend_row']] == '实例数为 0、没有检索记录（待确认）'


def test_same_shape_other_name_without_match_stays_a_zero_row(drawing, tmp_path):
    _, report, legend = ledger(tmp_path, base_input(drawing, tmp_path))
    assert legend_of(legend, 'GPBL')['plan_count'] == '0'
    assert where(report, 'GPBX', 'P1') == {'待识别': 1}


def test_non_block_row_matched_to_blocks_adds_no_extra_candidate(drawing, tmp_path):
    row = {'drawing': 'D1', 'legend_row': 'L01', 'symbol': '端子板（线框画法）', 'symbol_kind': '非块', 'matched_symbols': 'GPBX',
           'destination': '已计', 'target_or_reason': '端子板B'}
    _, report, legend = ledger(tmp_path, base_input(drawing, tmp_path, legend_rows=[row]))
    assert legend_of(legend, '端子板（线框画法）')['plan_count'] == '1'
    assert not any(g['symbol'] == '端子板（线框画法）' for g in report['groups'])
