"""ID69: real browser consumes the shared app/audit location state, synthetic only.

CAD_QTO_PLAYWRIGHT_MODULE may point at an existing playwright package; no install.
Without a browser automation runtime this optional integration check is skipped.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(Path(__file__).parent))
import build_qto_review_html as builder
import review_comparison_fixture as fixture


def unlocated_view():
    v = fixture.view()
    v['scenes'].append({**copy.deepcopy(v['scenes'][0]), 'id': 'P2', 'title': '第二合成图页'})
    u1 = fixture.circuit(91, 0, scene=None)
    u1.update(id='U1', catalog_key='U1', title='未定位来源甲', unlocated_reason='estimated', unlocated_note='U1独有原因')
    u2 = copy.deepcopy(u1)
    u2.update(id='U2', catalog_key='U2', title='未定位来源乙', unlocated_reason='no_geometry', unlocated_note='U2独有原因',
              quantities=[{'name': '盒数', 'value': 10, 'unit': '个', 'level': '确认量'}])
    v['objects'] += [u1, u2]
    fixture.item(v, 'I1')['detail_rows'][0]['member_keys'].append('U1')
    fixture.item(v, 'I2')['detail_rows'][0].update(member_keys=['U2'], aggregation='sum_members', member_quantity_name='盒数')
    return v


def test_unlocated_note_follows_real_browser_location_and_close(tmp_path):
    module = os.environ.get('CAD_QTO_PLAYWRIGHT_MODULE', 'playwright')
    probe = subprocess.run(['node', '-e', 'require(process.argv[1])', module], capture_output=True, stdin=subprocess.DEVNULL)
    if probe.returncode:
        pytest.skip('现有Playwright模块不可用；设置CAD_QTO_PLAYWRIGHT_MODULE后运行真实浏览器检查')
    source = tmp_path / '通用定位合成输入.json'
    output = tmp_path / '通用定位合成页.html'
    source.write_text(json.dumps(unlocated_view(), ensure_ascii=False), encoding='utf-8')
    builder.build(source, output, feedback_url='')
    result = subprocess.run(['node', str(ROOT / 'tests/run_review_unlocated_browser_20261018.cjs'),
                             '--html', str(output), '--report', str(tmp_path / '真实浏览器回执.json')],
                            capture_output=True, encoding='utf-8', stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stdout + result.stderr
