"""b1 点位覆盖台账（契约 docs/维护/点位覆盖台账契约.md，登记 R3、1056、1535）。

合成图（去标识）：平面图页 P1（0,0～10000,10000）上两个配电箱 BOX、一个灯 LAMP、一个轴号 AXIS、一个内含 BOX 的组合块 MID、
一个以文字作实例键的配电箱 AL1；图例图页 LG（20000,0～30000,10000）上 BOX、LAMP 各一个复制件；图页外再放一个 LAMP。
期望值按图面布置写出；反向核对用 ezdxf 独立数顶层块，不用台账自己的数。
"""
import csv
import hashlib
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
PLAN = {'BOX1': ('BOX', (1000, 1000)), 'BOX2': ('BOX', (2000, 1000)), 'LAMP': ('LAMP', (3000, 1000)),
        'AXIS': ('AXIS', (4000, 1000)), 'MID': ('MID', (6000, 1000)), 'LG_BOX': ('BOX', (21000, 9000)),
        'LG_LAMP': ('LAMP', (21000, 8000)), 'FAR': ('LAMP', (50000, 50000))}
FIELDS = ('drawing', 'legend_row', 'legend_handle', 'legend_text', 'symbol', 'symbol_kind', 'plan_count', 'count_source',
          'variant_flag', 'destination', 'target_or_reason', 'suspected_category', 'search_record_id')


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def drawing(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('cover')
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in ('BOX', 'LAMP', 'AXIS'):
        doc.blocks.new(name).add_lwpolyline(SQUARE, close=True)
    doc.blocks.new('MID').add_blockref('BOX', (100, 0))
    msp = doc.modelspace()
    handles = {key: msp.add_blockref(block, at).dxf.handle for key, (block, at) in PLAN.items()}
    handles['AL1'] = msp.add_text('AL1', dxfattribs={'insert': (5000, 1000)}).dxf.handle
    handles['IN_MID'] = next(iter(doc.blocks.get('MID'))).dxf.handle
    dxf = tmp / 'cover.dxf'; doc.saveas(dxf)
    neutral = tmp / 'neutral'
    done = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert done.returncode == 0, done.stdout + done.stderr
    write(tmp / 'sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'],
          [['P1', '平面', 0, 0, 10000, 10000], ['LG', '图例', 20000, 0, 30000, 10000]])
    return {'dir': tmp, 'dxf': dxf, 'neutral': neutral, 'h': handles}


def write(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return path


def tables_input(d, tmp, count_rows=None, **extra):
    h = d['h']
    rows = count_rows if count_rows is not None else [[h['BOX1'], '配电箱'], [h['BOX2'], '配电箱'], [h['AL1'], '配电箱']]
    write(tmp / 'count.csv', ['handle', 'cat'], rows)
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'scope_id': '合成',
            'drawings': [{'drawing': 'D1', 'neutral_dir': str(d['neutral'])}],
            'count_tables': [{'path': 'count.csv', 'constants': {'drawing': 'D1'}, 'columns': {'handle': 'handle', 'category': 'cat'}}],
            'sheets': [{'path': str(d['dir'] / 'sheets.csv'), 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}],
            **extra}
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


def by_symbol(report, dest):
    out = {}
    for g in report['groups']:
        if g['destination'] == dest:
            out[g['symbol']] = out.get(g['symbol'], 0) + g['count']
    return out


def test_tables_source_lists_every_top_level_block_and_legend_copies(drawing, tmp_path):
    _, report, legend = ledger(tmp_path, tables_input(drawing, tmp_path))
    top_inserts = sum(1 for e in ezdxf.readfile(drawing['dxf']).modelspace() if e.dxftype() == 'INSERT')
    assert report['candidate_total'] == top_inserts + 1 == sum(report['by_destination'].values())   # + AL1 text key (A14)
    assert by_symbol(report, '已计') == {'BOX': 2, '<TEXT>': 1}
    assert by_symbol(report, '待识别') == {'LAMP': 2, 'AXIS': 1, 'MID': 1}            # A08: unknown blocks stay visible
    assert by_symbol(report, '有据排除') == {'BOX': 1, 'LAMP': 1}                      # A10: legend-sheet copies
    far = next(g for g in report['groups'] if g['symbol'] == 'LAMP' and g['sheet'] == '图页未定')
    assert far['count'] == 1 and far['destination'] == '待识别'
    assert report['status'] == 'INCOMPLETE' and report['coverage_complete'] is False
    assert report['class_completeness']['配电箱']['complete'] is False   # unknown pending drags every class on D1
    rows = {r['symbol']: r for r in legend}
    assert rows['BOX']['plan_count'] == '2' and rows['BOX']['destination'] == '已计' and rows['BOX']['target_or_reason'] == '配电箱'
    assert rows['LAMP']['plan_count'] == '2' and rows['LAMP']['destination'] == ''      # plan + off-sheet, not the legend copy
    assert rows['BOX']['legend_handle'].upper() == drawing['h']['LG_BOX'].upper()


def test_excluded_class_only_resolves_unknowns_and_version_conflict_is_separate(drawing, tmp_path):
    data = tables_input(drawing, tmp_path,
                        excluded_classes=[{'class_id': 'EX-01', 'drawing': '*', 'block_pattern': '^(AXIS|BOX)$', 'basis': '底图噪声'}],
                        version_conflicts=[{'conflict_id': 'VC-01', 'drawing': 'D1', 'object_ids': ['PT-CAD-' + drawing['h']['FAR'].upper()],
                                            'basis': '两版图纸不一致'}])
    _, report, _ = ledger(tmp_path, data)
    assert by_symbol(report, '已计')['BOX'] == 2                                    # counted BOX untouched by EX-01
    assert {w['class_id'] for w in report['warnings']['counted_matches_excluded_class']} == {'EX-01'}
    assert by_symbol(report, '有据排除').get('AXIS') == 1
    assert report['by_destination']['版本冲突'] == 1 and by_symbol(report, '待识别') == {'LAMP': 1, 'MID': 1}   # A11


def test_filled_legend_rows_drive_no_destination_and_suspected_classes(drawing, tmp_path):
    h = drawing['h']
    write(tmp_path / 'legend.csv', FIELDS, [
        ['D1', 'A002', h['LG_LAMP'], '吸顶灯', 'LAMP', '块', '', '', '', '待识别', '', '灯具', ''],
        ['D1', 'A001', h['LG_BOX'], '配电箱', 'BOX', '块', '5', '', '', '', '', '', ''],            # A17 plan_count conflict
        ['D1', 'M1', '', '', 'MID', '块', '', '', '', '', '', '其他', ''],
        ['D1', 'M2', '', '', 'AXIS', '块', '', '', '', '', '', '轴号', ''],
        ['D1', 'L12', '', '接地端子板', 'HATCH 实心三角', '非块', '27', '', '', '', '', '', 'SR-01'],  # A09
        ['D1', 'L13', '', '等电位箱', 'MEB', '非块', '0', '', '', '不计', '本图未布置', '', ''],       # A16
    ])
    _, report, legend = ledger(tmp_path, tables_input(drawing, tmp_path, legend_coverage='legend.csv'))
    assert report['by_destination']['无去向'] == 27
    assert report['candidate_total'] == len(PLAN) + 1 + 27
    pending = {r['legend_row']: r['reason'] for r in report['legend_rows_without_destination']}
    assert set(pending) == {'L12', 'L13'} and '待确认' in pending['L13']
    assert report['warnings']['plan_count_conflicts'] == [{'drawing': 'D1', 'legend_row': 'A001', 'filled': '5', 'automatic': '2'}]
    assert {r['legend_row']: r['plan_count'] for r in legend}['A001'] == '2'
    assert {r['legend_row']: r['suspected_category'] for r in legend}['A002'] == '灯具'   # filled values carried over
    # 端子板 row has no suspected class: unknown 27 drags 配电箱; giving it one leaves 配电箱 complete
    assert report['class_completeness']['配电箱']['complete'] is False
    with open(tmp_path / 'legend.csv', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    rows[5][11] = '接地端子板'; again = tmp_path / 'again'; again.mkdir()
    write(again / 'legend.csv', rows[0], rows[1:])
    _, report, _ = ledger(again, tables_input(drawing, again, legend_coverage='legend.csv'))
    assert report['class_completeness']['配电箱'] == {'complete': True, 'reasons': []}
    assert report['class_completeness']['灯具']['complete'] is False and report['class_completeness']['接地端子板']['complete'] is False


@pytest.mark.parametrize('case', ['missing', 'in_block', 'twice', 'counted_and_excluded', 'both_sources'])
def test_bad_tables_are_input_problems(drawing, tmp_path, case):
    h = drawing['h']
    rows = {'missing': [['FFFFF', '配电箱']], 'in_block': [[h['IN_MID'], '配电箱']],
            'twice': [[h['BOX1'], '配电箱'], [h['BOX1'].lower(), '箱']]}.get(case, [[h['BOX1'], '配电箱']])
    extra = {}
    if case == 'counted_and_excluded':
        write(tmp_path / 'ex.csv', ['handle'], [[h['BOX1']]])
        extra['handle_exclusions'] = [{'path': 'ex.csv', 'constants': {'drawing': 'D1', 'basis': '重叠'}, 'columns': {'handle': 'handle'}}]
    data = tables_input(drawing, tmp_path, count_rows=rows, **extra)
    if case == 'both_sources':
        data['drawings'][0]['reports'] = ['x.json']
    done, report, _ = ledger(tmp_path, data)
    assert done.returncode == 2 and report is None
    message = json.dumps(json.loads(done.stderr)['problems'], ensure_ascii=False)
    assert {'missing': '查不到', 'in_block': '块内图元', 'twice': '出现两次', 'counted_and_excluded': '既在计数表里又在排除表里',
            'both_sources': '同时写了 reports 和 neutral_dir'}[case] in message


def test_exclusion_table_and_uncovered_drawing(drawing, tmp_path):
    h = drawing['h']
    write(tmp_path / 'ex.csv', ['handle_b', 'handle_a'], [[h['LAMP'], h['BOX1']]])
    data = tables_input(drawing, tmp_path, handle_exclusions=[{'path': 'ex.csv', 'constants': {'drawing': 'D1', 'basis': '与 {handle_a} 重叠'},
                                                                'columns': {'handle': 'handle_b'}}])
    data['drawings'].append({'drawing': 'D2'})
    _, report, _ = ledger(tmp_path, data)
    assert any(g['symbol'] == 'LAMP' and g['destination'] == '有据排除' and g['category_or_basis'] == f"与 {h['BOX1']} 重叠"
               for g in report['groups'])
    assert report['uncovered_drawings'] == ['D2'] and report['by_drawing']['D2']['source'] == 'legend_only'


# ---------- source 1: adapter reports ----------

def adapter(tmp, unknown_blocks=0):
    """Adapter run on a drawing with BOX (rule), LAMP and MID (no rule), plus N distinct unknown blocks."""
    tmp.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in ('BOX', 'LAMP', *[f'U{i:02d}' for i in range(unknown_blocks)]):
        doc.blocks.new(name).add_lwpolyline(SQUARE, close=True)
    doc.blocks.new('MID').add_blockref('BOX', (100, 0))
    msp = doc.modelspace()
    boxes = ['PT-CAD-' + msp.add_blockref('BOX', at).dxf.handle for at in ((1000, 1000), (2000, 1000))]
    msp.add_blockref('LAMP', (3000, 1000)); msp.add_blockref('MID', (6000, 1000))
    for i in range(unknown_blocks):
        msp.add_blockref(f'U{i:02d}', (100 + 50 * i, 9000))
    dxf = tmp / 'a.dxf'; doc.saveas(dxf); src = sha(dxf)
    assert run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp / 'n').returncode == 0
    entry = {'adjudication_id': 'M-BOX', 'subject_id': 'BOX', 'professional_owner': 'qto-components-points', 'decision': 'include',
             'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试', 'recognized_at': '2026-10-16T00:00:00+00:00',
             'source_id': 'SYN', 'evidence_ids': ['G'], 'object_ids': boxes, 'basis': '合成图例', 'scope_basis': '全图',
             'identity_basis': '源句柄', 'cross_check': '按图面布置核对', 'uncertainties': ['合成']}
    ledger_doc = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1', 'source_sha256': src,
                  'adoption_scope': 'working', 'review_status': 'unreviewed',
                  'evidence': [{'evidence_id': 'G', 'path': str(dxf), 'sha256': src, 'source_sha256': src, 'kind': 'cad_property',
                                'locator': 'Model', 'observation': '合成'}], 'entries': [entry]}
    lp = tmp / 'ledger.json'; lp.write_text(json.dumps(ledger_doc, ensure_ascii=False), encoding='utf-8')
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model'}, 'unit': {'factor_to_output': .001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': [{'rule_id': 'BOX', 'adjudication_id': 'M-BOX', 'block_pattern': '^BOX$', 'decision': 'include',
                          'category': '配电箱', 'system': 'S', 'spec': '箱', 'unit': '台', 'quantity_level': '规则推算量',
                          'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}]}
    pp = tmp / 'profile.json'; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    done = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', tmp / 'n/CAD中性取证清单.json',
               '--profile', pp, '--adjudication-ledger', lp, '--output-dir', tmp / 'out')
    assert done.returncode == 0, done.stderr
    return tmp / 'out'


def test_adapter_writes_every_unresolved_instance_not_just_top15(tmp_path):
    out = adapter(tmp_path, unknown_blocks=17)
    report = json.loads((out / '点位识别报告.json').read_text(encoding='utf-8'))
    with open(out / '点位未决全量.csv', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    items = [json.loads(x) for x in (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines()]
    items = [x for x in items if x['disposition'] == 'unresolved']
    unresolved = sorted(x['physical_id'] for x in items)
    groups = {(x['block_name'], x['layer'], x.get('parent_block'), x['basis']) for x in items}   # U00..U16, LAMP, MID, BOX in MID
    assert len(groups) == 20 and len(report['unresolved_groups_top15']) == 15 and report['unresolved_group_count'] == 20
    assert {(r['block_name'], r['layer'], r['parent_block'] or None, r['basis']) for r in rows} == groups
    assert sorted(r['physical_id'] for r in rows) == unresolved and report['unresolved_instance_count'] == len(unresolved)
    assert report['artifacts']['unresolved_full_csv_sha256'] == sha(out / '点位未决全量.csv')


def test_report_source_mirrors_every_disposition_and_places_by_drawing_coordinates(tmp_path):
    out = adapter(tmp_path)
    (tmp_path / 'ledger').mkdir()
    write(tmp_path / 'ledger/sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], [['P1', '平面', 0, 0, 10000, 10000]])
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'drawings': [{'drawing': 'D1', 'reports': [str(out / '点位识别报告.json')]}],
            'sheets': [{'path': 'sheets.csv', 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}]}
    _, report, _ = ledger(tmp_path / 'ledger', data)
    dispositions = [json.loads(x) for x in (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines()]
    want = {'adopted': '已计', 'merged': '已计', 'excluded': '有据排除', 'not_applicable': '有据排除',
            'duplicate_excluded': '有据排除', 'unresolved': '待识别'}
    expected = {}
    for item in dispositions:
        expected[want[item['disposition']]] = expected.get(want[item['disposition']], 0) + 1
    assert report['candidate_total'] == len(dispositions)
    assert {k: v for k, v in report['by_destination'].items() if v} == expected
    assert all(g['sheet'] == 'P1' for g in report['groups'])            # m output × 1/0.001 back to drawing mm
    assert by_symbol(report, '已计') == {'BOX': 2} and by_symbol(report, '待识别') == {'LAMP': 1, 'MID': 1, 'BOX': 1}   # BOX in MID stays unresolved
    nested = next(g for g in report['groups'] if g['parent_block'] == 'MID')
    assert nested['destination'] == '待识别' and '/' in nested['sample_ids'][0]


def test_same_handle_on_two_drawings_is_two_candidates(tmp_path):
    out = adapter(tmp_path)   # PT-CAD-<handle> is unique only within one drawing
    report_path = str(out / '点位识别报告.json')
    data = {'schema_version': 'qto-point-coverage-input/1.0',
            'drawings': [{'drawing': 'D1', 'reports': [report_path]}, {'drawing': 'D2', 'reports': [report_path]}]}
    _, report, _ = ledger(tmp_path / 'ledger', data)
    n = len((out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines())
    assert report['candidate_total'] == 2 * n
    assert report['by_drawing']['D1']['已计'] == report['by_drawing']['D2']['已计'] == 2
