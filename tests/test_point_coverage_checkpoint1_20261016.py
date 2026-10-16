"""第四批乙检查点一后修（决策会话核定）：覆盖台账两处。

1. 未确认详图页的 pending_count 只数整类排除之后仍待识别的（原先图框块被整类排除后仍计入，每页多 1）。
2. 整类排除排掉了平面页上的块、而同名块（去外部参照前缀）在本输入别处已计时提醒（D014 8C5 按钮盒），去向不变。

合成图：平面 P1（0～5000）上 BOX（计数）、REF$0$BOX（外部参照绑定的同名块）、AXIS；详图 DT（10000～20000）上两个 LAMP、一个 FRAME。
期望值按图面布置写出。
"""
import importlib.util
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cov_for_cp1', ROOT / 'tests' / 'test_point_coverage_ledger_20261016.py')
cov = importlib.util.module_from_spec(spec); spec.loader.exec_module(cov)
PLAN = {'BOX': ('BOX', (1000, 1000)), 'XBOX': ('REF$0$BOX', (2000, 1000)), 'AXIS': ('AXIS', (3000, 1000)),
        'L1': ('LAMP', (11000, 1000)), 'FRAME': ('FRAME', (12000, 1000)), 'L2': ('LAMP', (13000, 1000))}


@pytest.fixture(scope='module')
def drawing(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('cp1')
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in ('BOX', 'REF$0$BOX', 'AXIS', 'LAMP', 'FRAME'):
        doc.blocks.new(name).add_lwpolyline(cov.SQUARE, close=True)
    msp = doc.modelspace()
    handles = {key: msp.add_blockref(block, at).dxf.handle for key, (block, at) in PLAN.items()}
    dxf = tmp / 'cp1.dxf'; doc.saveas(dxf)
    done = cov.run(cov.SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp / 'neutral')
    assert done.returncode == 0, done.stdout + done.stderr
    cov.write(tmp / 'sheets.csv', ['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], [['P1', '平面', 0, 0, 5000, 5000], ['DT', '详图', 10000, 0, 20000, 5000]])
    return {'dir': tmp, 'h': handles}


def ledger(d, tmp, classes, detail_copy=None):
    tmp.mkdir(parents=True, exist_ok=True)
    cov.write(tmp / 'count.csv', ['handle', 'cat'], [[d['h']['BOX'], '按钮盒']])
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'drawings': [{'drawing': 'D1', 'neutral_dir': str(d['dir'] / 'neutral')}],
            'count_tables': [{'path': 'count.csv', 'constants': {'drawing': 'D1'}, 'columns': {'handle': 'handle', 'category': 'cat'}}],
            'sheets': [{'path': str(d['dir'] / 'sheets.csv'), 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}],
            'excluded_classes': classes, **({'detail_copy_sheets': detail_copy} if detail_copy else {})}
    done, report, _ = cov.ledger(tmp, data)
    assert done.returncode == 0, done.stderr
    return report


def pending_on(report, sheet):
    return sum(g['count'] for g in report['groups'] if g['sheet'] == sheet and g['destination'] == '待识别')


FRAME = {'class_id': 'EX-FRAME', 'drawing': '*', 'block_pattern': '^FRAME$', 'basis': '图框'}


def test_detail_pending_count_is_what_stays_pending_after_class_rules(drawing, tmp_path):
    report = ledger(drawing, tmp_path, [FRAME])
    assert report['unconfirmed_detail_sheets'] == [{'drawing': 'D1', 'sheet': 'DT', 'pending_count': 2}]   # two LAMP, not the frame
    assert pending_on(report, 'DT') == 2   # reverse check against the groups, not the counter
    frame = [g for g in report['groups'] if g['symbol'] == 'FRAME']
    assert len(frame) == 1 and frame[0]['destination'] == '有据排除' and frame[0]['category_or_basis'].startswith('EX-FRAME')


def test_detail_pending_count_without_class_rules_and_after_confirmation(drawing, tmp_path):
    report = ledger(drawing, tmp_path / 'none', [])
    assert report['unconfirmed_detail_sheets'] == [{'drawing': 'D1', 'sheet': 'DT', 'pending_count': 3}] == \
        [{'drawing': 'D1', 'sheet': 'DT', 'pending_count': pending_on(report, 'DT')}]
    report = ledger(drawing, tmp_path / 'ok', [FRAME], [{'drawing': 'D1', 'sheet_id': 'DT', 'basis': '大样放大的就是平面那两盏'}])
    assert report['unconfirmed_detail_sheets'] == [] and pending_on(report, 'DT') == 0


def test_class_rule_sweeping_a_block_counted_elsewhere_is_flagged(drawing, tmp_path):
    classes = [{'class_id': 'EX-NOTE', 'drawing': '*', 'block_pattern': r'^REF\$0\$', 'basis': '说明引用块'},
               {'class_id': 'EX-AXIS', 'drawing': '*', 'block_pattern': '^AXIS$', 'basis': '轴号'}, FRAME]
    report = ledger(drawing, tmp_path, classes)
    warned = report['warnings']['excluded_class_hits_counted_symbol']
    assert [(w['class_id'], w['sheet'], w['symbol'], w['excluded_count'], w['counted_on']) for w in warned] == \
        [('EX-NOTE', 'P1', 'REF$0$BOX', 1, ['D1'])]   # AXIS has no counted twin; FRAME is on a detail sheet
    assert {g['symbol']: g['destination'] for g in report['groups'] if g['sheet'] == 'P1'} == \
        {'BOX': '已计', 'REF$0$BOX': '有据排除', 'AXIS': '有据排除'}   # destinations unchanged


def test_no_flag_when_nothing_with_that_name_is_counted(drawing, tmp_path):
    report = ledger(drawing, tmp_path, [{'class_id': 'EX-AXIS', 'drawing': '*', 'block_pattern': '^AXIS$', 'basis': '轴号'}, FRAME])
    assert 'excluded_class_hits_counted_symbol' not in report['warnings']
