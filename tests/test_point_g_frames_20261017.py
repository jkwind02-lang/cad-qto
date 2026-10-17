"""第五批 G：图页表漏页提示（登记 20261016-1859）、图例页上的图框、按类别认领（20261016-1945）。

合成图（毫米）：平面 P1（0,0～10000,10000）、P2（10000,0～20000,10000），图例页 LG（20000,0～30000,10000）。图框块 A1
（10000×10000，基点在左下角）五个：正好盖 P1；比 P2 大一圈（放大 1.04，中心仍在 P2）；在图例页；跨 P1、P2 两页（中心在
分界线上）；放在所有图页外（漏页）。P1 上灯 LAMP 四只，计数表里三只白光灯、一只黄光灯；图例页上 LAMP 两个复制件。
边界清单（备课第二节 G）：
- 图框按图形中心定页，落不进任何图页的报 frame_outside_sheet_table（句柄、块名、插入点、中心），汇总单列未覆盖图框数，
  不为 0 时结论写“可能漏页”；图页框比图框小但中心在框内、图框跨两页、图框在图例页都不报；
- 第二张合成图（复验 D003、D022、D023 后补）：图页框只框图面区、图框中心落在框外，但图框与它重合过半 → 不报；
  图框中心在图页上、重合只有四分之一 → 不报；图页框比图框大一圈、伸进相邻图框 200 → 相邻图框照报；A0 图框里嵌着的图签（展开后的子候选）随外框，不单核，单列个数；
  只有填充、定不出范围的图框，插入点不在任何图页里 → 报 frame_extent_unknown，记“没核”，不算漏页；
- 报出的每个图框（可能漏页、定不出范围、整图没有图页表）逐条进 frame_todo 和 图框待办.csv（FR- 编号、图号、句柄、块名、
  插入点、范围）；frame_dispositions 按句柄（大小写不论）或编号写处置（封面、目录、空白、说明或图例、旧版副本、非本专业底图、
  已补进图页表、其他，basis 必填；中间三个取自电气逐框核图的处置表），
  写了的不再报、单列 disposed；没对上任何报出图框的处置 → frame_dispositions_unused；坏处置 → C-5；
- 整张图没有图页表：整图报 frames_without_sheet_table；frame_blocks 写块名正则，或引用 excluded_classes 的规则号，
  引用的规则号不存在 → 输入问题；没写 frame_blocks 时不核，结论写明没核；
- 图例页上命中 frame_blocks 的块不生成图例行，计数；填过的图框图例行丢掉并列出；
- matched_symbols 写“块名::类别”，同名块的实例按类别分给几行；类别写错（这个块名下没有该类别）→ C-6；
  同一块名一行写了类别、另一行没写 → 冲突；两行写同一类别 → 冲突；只写块名的照旧（两行各写自己的块名不算冲突）。
"""
import csv
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
POINTS = ROOT / 'skills/qto-components-points/scripts'
LEGEND = ('drawing', 'legend_row', 'legend_handle', 'legend_text', 'symbol', 'symbol_kind', 'matched_symbols', 'plan_count',
          'destination', 'target_or_reason')


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def write(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return path


@pytest.fixture(scope='module')
def drawing(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('frames')
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.blocks.new('A1').add_lwpolyline([(0, 0), (10000, 0), (10000, 10000), (0, 10000)], close=True)
    doc.blocks.new('LAMP').add_lwpolyline([(-50, -50), (50, -50), (50, 50), (-50, 50)], close=True)
    msp, h = doc.modelspace(), {}
    for key, at, scale in (('F_P1', (0, 0), 1), ('F_BIG', (9800, -200), 1.04), ('F_LG', (20000, 0), 1),
                           ('F_CROSS', (5000, 0), 1), ('F_MISS', (40000, 0), 1)):
        h[key] = msp.add_blockref('A1', at, dxfattribs={'xscale': scale, 'yscale': scale}).dxf.handle
    for key, at in (('W1', (1000, 1000)), ('W2', (2000, 1000)), ('W3', (3000, 1000)), ('Y1', (4000, 1000)),
                    ('LG_W', (21000, 9000)), ('LG_Y', (21000, 8000))):
        h[key] = msp.add_blockref('LAMP', at).dxf.handle
    dxf = tmp / 'frames.dxf'; doc.saveas(dxf)
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp / 'neutral')
    assert done.returncode == 0, done.stdout + done.stderr
    write(tmp / 'sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'],
          [['P1', '平面', 0, 0, 10000, 10000], ['P2', '平面', 10000, 0, 20000, 10000], ['LG', '图例', 20000, 0, 30000, 10000]])
    write(tmp / 'count.csv', ['handle', 'cat'], [[h['W1'], '白光灯'], [h['W2'], '白光灯'], [h['W3'], '白光灯'], [h['Y1'], '黄光灯']])
    return {'dir': tmp, 'neutral': tmp / 'neutral', 'h': h}


@pytest.fixture(scope='module')
def drawing2(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('frames2')
    doc = ezdxf.new('R2013'); doc.units = 4
    square = [(0, 0), (10000, 0), (10000, 10000), (0, 10000)]
    doc.blocks.new('A1').add_lwpolyline(square, close=True)
    doc.blocks.new('TB').add_lwpolyline([(0, 0), (2000, 0), (2000, 1000), (0, 1000)], close=True)
    a0 = doc.blocks.new('A0'); a0.add_lwpolyline(square, close=True); a0.add_blockref('TB', (8000, 0))
    doc.blocks.new('A2').add_hatch().paths.add_polyline_path(square, is_closed=True)
    msp, h = doc.modelspace(), {}
    for key, name, at in (('F_AREA', 'A1', (0, 0)), ('F_FULL', 'A1', (20000, 0)), ('F_NEXT', 'A1', (30000, 0)),
                          ('F_NEST', 'A0', (0, 20000)), ('F_FILL', 'A2', (40000, 0)), ('F_CORNER', 'A1', (0, 40000))):
        h[key] = msp.add_blockref(name, at).dxf.handle
    dxf = tmp / 'frames2.dxf'; doc.saveas(dxf)
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp / 'neutral')
    assert done.returncode == 3 and '"status": "PARTIAL"' in done.stdout, done.stdout + done.stderr   # the HATCH waits for an adapter
    sheets = [['S1', '平面', 500, 500, 4500, 9500], ['S3', '平面', 19800, -200, 30200, 10200], ['S4', '平面', 0, 20000, 4000, 30000],
              ['S6', '平面', 5000, 45000, 25000, 65000]]                  # F_CORNER: centre on S6's corner, a quarter shared
    write(tmp / 'sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], sheets)
    write(tmp / 'sheets_next.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], [*sheets, ['S5', '平面', 30500, 500, 34500, 9500]])
    write(tmp / 'count.csv', ['handle', 'cat'], [])
    return {'dir': tmp, 'neutral': tmp / 'neutral', 'h': h}


def ledger(d, tmp, legend=None, drawings=('D1',), sheets='sheets.csv', expand=(), **extra):
    tmp.mkdir(parents=True, exist_ok=True)
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'scope_id': '合成G',
            'drawings': [{'drawing': x, 'neutral_dir': str(d['neutral']), **({'expand_blocks': list(expand)} if expand else {})}
                         for x in drawings],
            'count_tables': [{'path': str(d['dir'] / 'count.csv'), 'constants': {'drawing': 'D1'}, 'columns': {'handle': 'handle', 'category': 'cat'}}],
            'sheets': [{'path': str(d['dir'] / sheets), 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}], **extra}
    if legend is not None:
        write(tmp / 'legend.csv', LEGEND, legend); data['legend_coverage'] = 'legend.csv'
    p = tmp / 'input.json'; p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    done = run(POINTS / 'build_point_coverage_ledger.py', '--input', p, '--output-dir', tmp / 'out')
    if done.returncode:
        return done, None, None
    report = json.loads((tmp / 'out/点位覆盖台账.json').read_text(encoding='utf-8'))
    with open(tmp / 'out/图例覆盖表_待填.csv', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    return done, report, rows


FRAME = [{'block_pattern': '^A1$'}]


def test_a_frame_outside_every_sheet_is_reported_and_the_others_are_not(drawing, tmp_path):
    h = drawing['h']
    done, report, _ = ledger(drawing, tmp_path, frame_blocks=FRAME)
    fc = report['frame_coverage']
    assert fc['checked'] is True and fc['frame_count'] == 5 and fc['uncovered_frame_count'] == 1
    assert fc['conclusion'] == '可能漏页：1 个图框不在图页表里'
    (miss,) = report['warnings']['frame_outside_sheet_table']
    assert miss['handle'] == h['F_MISS'].upper() and miss['block_name'] == 'A1' and miss['drawing'] == 'D1'
    assert miss['insert_point'] == pytest.approx([40000, 0]) and miss['geometry_centre'] == pytest.approx([45000, 5000])
    assert 'frames_without_sheet_table' not in report['warnings']
    assert '可能漏页' in done.stdout
    # the same frames named through the project's frame class rule
    _, again, _ = ledger(drawing, tmp_path / 'class', frame_blocks=[{'class_id': 'EX-FRAME'}],
                         excluded_classes=[{'class_id': 'EX-FRAME', 'drawing': '*', 'block_pattern': '^A1$', 'basis': '图框'}])
    assert again['frame_coverage']['uncovered_frame_count'] == 1 and again['frame_coverage']['rules'] == ['EX-FRAME']


def test_a_sheet_boxing_only_the_drawing_area_nested_title_blocks_and_frames_without_extent(drawing2, tmp_path):
    h = drawing2['h']
    rules = [{'block_pattern': '^(A0|A1|A2|TB)$'}]
    _, report, _ = ledger(drawing2, tmp_path / 'first', expand=('A0',), frame_blocks=rules)
    fc = report['frame_coverage']
    assert fc['frame_count'] == 6 and fc['nested_frames_skipped'] == 1
    assert fc['uncovered_frame_count'] == 1 and fc['unchecked_frame_count'] == 1
    assert fc['conclusion'] == '可能漏页：1 个图框不在图页表里；另有 1 个图框定不出范围，没核'
    (miss,) = report['warnings']['frame_outside_sheet_table']                  # the neighbour of S3, not F_AREA or F_NEST
    assert miss['handle'] == h['F_NEXT'].upper() and miss['geometry_box'] == pytest.approx([30000, 0, 40000, 10000])
    (unknown,) = report['warnings']['frame_extent_unknown']
    assert unknown['handle'] == h['F_FILL'].upper() and unknown['geometry_box'] is None
    assert unknown['insert_point'] == pytest.approx([40000, 0]) and unknown['placement_basis'].startswith('插入点')
    # the nested title block sits off every sheet (centre 9000, 20500) yet goes with its frame
    nested = [g for g in report['groups'] if g['symbol'] == 'TB']
    assert [(g['sheet'], g['count']) for g in nested] == [('图页未定', 1)]
    # S5 boxes the drawing area of F_NEXT: nothing is missing, the fill-only frame is still not checked
    _, again, _ = ledger(drawing2, tmp_path / 'next', sheets='sheets_next.csv', expand=('A0',), frame_blocks=rules)
    assert again['frame_coverage']['uncovered_frame_count'] == 0 and 'frame_outside_sheet_table' not in again['warnings']
    assert again['frame_coverage']['conclusion'] == '1 个图框定不出范围，没核；其余图框都在图页表里'


def test_every_reported_frame_is_a_to_do_row_until_the_project_writes_its_disposition(drawing2, tmp_path):
    h = drawing2['h']
    rules = [{'block_pattern': '^(A0|A1|A2|TB)$'}]
    _, report, _ = ledger(drawing2, tmp_path / 'todo', expand=('A0',), frame_blocks=rules)
    todo = report['frame_todo']
    assert [(t['todo_id'], t['kind'], t['handle'], t['block_name']) for t in todo] == [
        ('FR-0001', '可能漏页', h['F_NEXT'].upper(), 'A1'), ('FR-0002', '定不出范围', h['F_FILL'].upper(), 'A2')]
    assert todo[0]['insert_x'] == pytest.approx(30000) and [todo[0][k] for k in ('x1', 'y1', 'x2', 'y2')] == pytest.approx([30000, 0, 40000, 10000])
    assert todo[1]['x1'] is None and report['frame_coverage']['todo_count'] == 2
    with open(tmp_path / 'todo/out/图框待办.csv', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert [r['todo_id'] for r in rows] == ['FR-0001', 'FR-0002'] and rows[0]['drawing'] == 'D1' and rows[1]['kind'] == '定不出范围'
    fill_id = todo[1]['id']
    given = [{'drawing': 'D1', 'handle': h['F_NEXT'].lower(), 'disposition': '空白', 'basis': '核图：空白图框，没有内容'},
             {'drawing': 'D1', 'id': fill_id, 'disposition': '其他', 'basis': '核图：填充示意，不是图页'},
             {'drawing': 'D1', 'handle': 'FFFF', 'disposition': '封面', 'basis': '写错的句柄'}]
    _, after, _ = ledger(drawing2, tmp_path / 'done', expand=('A0',), frame_blocks=rules, frame_dispositions=given)
    fc = after['frame_coverage']
    assert fc['uncovered_frame_count'] == 0 and fc['unchecked_frame_count'] == 0 and fc['todo_count'] == 0 and after['frame_todo'] == []
    assert [(d['handle'], d['disposition'], d['kind']) for d in fc['disposed']] == [
        (h['F_NEXT'].upper(), '空白', '可能漏页'), (h['F_FILL'].upper(), '其他', '定不出范围')]
    assert fc['conclusion'] == '图框都在图页表里；2 个已写处置，不再报'
    assert 'frame_outside_sheet_table' not in after['warnings'] and 'frame_extent_unknown' not in after['warnings']
    (unused,) = after['warnings']['frame_dispositions_unused']
    assert unused['frame'] == 'FFFF' and unused['where'] == 'frame_dispositions[2]'


@pytest.mark.parametrize('value', ['说明或图例', '旧版副本', '非本专业底图'])
def test_frame_disposition_values_found_by_the_electrical_check(drawing2, tmp_path, value):
    h = drawing2['h']
    given = [{'drawing': 'D1', 'handle': h['F_NEXT'], 'disposition': value, 'basis': '电气逐框核图'}]
    _, report, _ = ledger(drawing2, tmp_path, expand=('A0',), frame_blocks=[{'block_pattern': '^(A0|A1|A2|TB)$'}],
                          frame_dispositions=given)
    assert [(d['handle'], d['disposition']) for d in report['frame_coverage']['disposed']] == [(h['F_NEXT'].upper(), value)]


@pytest.mark.parametrize('items, frames, message', [
    ([{'drawing': 'D1', 'handle': 'AB', 'disposition': '图例', 'basis': 'x'}], True, 'disposition 只能是'),
    ([{'drawing': 'D1', 'handle': 'AB', 'disposition': '封面', 'basis': ' '}], True, 'basis 必填'),
    ([{'drawing': 'D1', 'handle': 'AB', 'id': 'PT-CAD-AB', 'disposition': '封面', 'basis': 'x'}], True, '之一'),
    ([{'handle': 'AB', 'disposition': '封面', 'basis': 'x'}], True, '之一'),
    ([{'drawing': 'D1', 'handle': 'ab', 'disposition': '封面', 'basis': 'x'}, {'drawing': 'D1', 'handle': 'AB', 'disposition': '空白', 'basis': 'y'}],
     True, '两条处置'),
    ({'drawing': 'D1'}, True, '须是数组'),
    ([{'drawing': 'D1', 'handle': 'AB', 'disposition': '封面', 'basis': 'x'}], False, '没写 frame_blocks')])
def test_bad_frame_dispositions_are_input_problems(drawing2, tmp_path, items, frames, message):
    extra = {'frame_blocks': [{'block_pattern': '^A1$'}]} if frames else {}
    done, report, _ = ledger(drawing2, tmp_path, frame_dispositions=items, **extra)
    assert done.returncode == 2 and report is None
    problems = json.loads(done.stderr)['problems']
    assert any(p['check'] == 'C-5' and p['where'].startswith('frame_dispositions') and message in p['message'] for p in problems), problems


def test_no_frame_rule_means_not_checked_and_bad_rules_are_input_problems(drawing, tmp_path):
    _, report, _ = ledger(drawing, tmp_path)
    assert report['frame_coverage']['checked'] is False and '没写 frame_blocks' in report['frame_coverage']['conclusion']
    assert 'frame_outside_sheet_table' not in report['warnings']
    for name, rules in (('missing', [{'class_id': 'EX-NONE'}]), ('both', [{'class_id': 'EX-FRAME', 'block_pattern': '^A1$'}]),
                        ('empty', [{}]), ('regex', [{'block_pattern': '(A1'}]), ('array', {'block_pattern': '^A1$'})):
        done, report, _ = ledger(drawing, tmp_path / name, frame_blocks=rules,
                                 excluded_classes=[{'class_id': 'EX-FRAME', 'drawing': '*', 'block_pattern': '^A1$', 'basis': '图框'}])
        assert done.returncode == 2 and report is None, name
        problems = json.loads(done.stderr)['problems']
        assert any(p['where'].startswith('frame_blocks') for p in problems), (name, problems)
        if name == 'missing':
            assert any('EX-NONE' in p['message'] and '不在 excluded_classes 里' in p['message'] for p in problems)


def test_a_drawing_without_a_sheet_table_is_reported_whole(drawing, tmp_path):
    _, report, _ = ledger(drawing, tmp_path, drawings=('D1', 'D2'), frame_blocks=FRAME)
    (whole,) = report['warnings']['frames_without_sheet_table']
    assert whole['drawing'] == 'D2' and whole['frame_count'] == 5
    assert report['frame_coverage']['uncovered_frame_count'] == 1 + 5
    assert [w['drawing'] for w in report['warnings']['frame_outside_sheet_table']] == ['D1']


def test_frames_on_the_legend_sheet_make_no_legend_row(drawing, tmp_path):
    h = drawing['h']
    _, before, rows = ledger(drawing, tmp_path / 'before')
    assert 'A1' in {r['symbol'] for r in rows}                                 # without frame_blocks the frame is a legend row
    framed = [[r['drawing'], r['legend_row'], r['legend_handle'], '图框', r['symbol'], '块', '', '', '不计', '图框'] for r in rows if r['symbol'] == 'A1']
    _, report, rows = ledger(drawing, tmp_path / 'after', legend=framed, frame_blocks=FRAME)
    assert 'A1' not in {r['symbol'] for r in rows} and {r['symbol'] for r in rows} == {'LAMP'}
    assert report['frame_coverage']['legend_frames_skipped'] == {'D1': 1}
    assert [x['legend_handle'].upper() for x in report['frame_coverage']['legend_frame_rows_dropped']] == [h['F_LG'].upper()]
    assert 'legend_handle_not_on_legend_sheet' not in report['warnings']


def legend_rows(h, white, yellow):
    return [['D1', 'A001', h['LG_W'], '龙骨灯白光', 'LAMP', '块', white, '', '', ''],
            ['D1', 'A002', h['LG_Y'], '龙骨灯黄光', 'LAMP', '块', yellow, '', '', '']]


def test_one_block_name_is_split_between_legend_rows_by_category(drawing, tmp_path):
    h = drawing['h']
    _, report, rows = ledger(drawing, tmp_path / 'split', legend=legend_rows(h, 'LAMP::白光灯', 'LAMP::黄光灯'), frame_blocks=FRAME)
    count = {r['legend_row']: r['plan_count'] for r in rows}
    assert count == {'A001': '3', 'A002': '1'}
    assert 'matched_symbol_conflicts' not in report['warnings']
    # without categories both rows see all four lamps (the legend copies are not counted)
    _, _, rows = ledger(drawing, tmp_path / 'plain', legend=legend_rows(h, '', ''), frame_blocks=FRAME)
    assert {r['legend_row']: r['plan_count'] for r in rows} == {'A001': '4', 'A002': '4'}


@pytest.mark.parametrize('white, yellow, why', [
    ('LAMP::白光灯', '', '有的没写类别'), ('LAMP::白光灯', 'LAMP', '有的没写类别'), ('LAMP::白光灯', 'LAMP::白光灯', '同时对应')])
def test_mixed_or_repeated_claims_on_one_block_name_conflict(drawing, tmp_path, white, yellow, why):
    h = drawing['h']
    _, report, _ = ledger(drawing, tmp_path, legend=legend_rows(h, white, yellow), frame_blocks=FRAME)
    pending = {r['legend_row']: r['reason'] for r in report['legend_rows_without_destination']}
    assert {'A001', 'A002'} <= set(pending) and all(why in pending[x] for x in ('A001', 'A002'))
    assert report['warnings']['matched_symbol_conflicts']


def test_a_category_the_block_does_not_have_is_an_input_problem(drawing, tmp_path):
    h = drawing['h']
    done, report, _ = ledger(drawing, tmp_path, legend=legend_rows(h, 'LAMP::白光灯', 'LAMP::红光灯'), frame_blocks=FRAME)
    assert done.returncode == 2 and report is None
    (problem,) = json.loads(done.stderr)['problems']
    assert problem['check'] == 'C-6' and 'LAMP::红光灯' in problem['message'] and '没有该类别' in problem['message']
