"""P2（登记 20261015-1316_codex）：点位主报告已知有未承接的模型识别对象时，端点交接不得声称来源完整。

合成图：同一方框+S 图符画两处，一处为块参照，一处为 7 条普通 LINE。模型工作识别台账显式列出两处 ID。
块适配器只能承接块参照；普通 LINE 组合须走独立计数与交接，不由本适配器自动猜测。
期望值按图面直接写出（2 个物理实例，块适配器承接 1 个），不从被测代码反算。
"""
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
SEGMENTS = [((-500, -500), (500, -500)), ((500, -500), (500, 500)), ((500, 500), (-500, 500)), ((-500, 500), (-500, -500)),
            ((-200, 300), (200, 300)), ((200, 300), (-200, -300)), ((-200, -300), (200, -300))]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=120)


def shape(layout, x, y):
    return [layout.add_line((x + a[0], y + a[1]), (x + b[0], y + b[1]), dxfattribs={'layer': 'SYMBOL'}).dxf.handle
            for a, b in SEGMENTS]


def build_case(tmp_path, mixed, declare_all):
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.layers.new('SYMBOL')
    shape(doc.blocks.new('SYNTHETIC_MARK'), 0, 0)
    msp = doc.modelspace()
    first = msp.add_blockref('SYNTHETIC_MARK', (1500, 1500), dxfattribs={'layer': 'SYMBOL'})
    if mixed:
        second_id = 'PT-CAD-' + shape(msp, 4500, 1500)[0]
    else:
        second_id = 'PT-CAD-' + msp.add_blockref('SYNTHETIC_MARK', (4500, 1500), dxfattribs={'layer': 'SYMBOL'}).dxf.handle
    first_id = 'PT-CAD-' + first.dxf.handle
    dxf = tmp_path / 'synthetic.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    src = sha(dxf)
    ids = [first_id, second_id] if declare_all else [first_id]
    ledger = {
        'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'SYN-LEDGER', 'ledger_version': '1',
        'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
        'evidence': [{'evidence_id': 'SYN-GEOMETRY', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                      'kind': 'cad_property', 'locator': 'Model 全图', 'observation': '合成方框+S 图符两处'}],
        'entries': [{
            'adjudication_id': 'MODEL-INCLUDE', 'subject_id': 'SYN-RULE', 'professional_owner': 'qto-components-points',
            'decision': 'include', 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
            'recognized_at': '2026-10-15T00:00:00+00:00', 'source_id': 'SYNTHETIC-DXF', 'evidence_ids': ['SYN-GEOMETRY'],
            'object_ids': ids, 'basis': '方框与 S 折线共同为 1 个合成构件', 'scope_basis': '整个 Model',
            'identity_basis': '普通图符以第一条外框线作稳定物理 ID', 'cross_check': '两处相距 3000，不重合',
            'uncertainties': ['合成工作识别，未人工复核']}],
    }
    lp = tmp_path / 'ledger.json'; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    profile = {
        'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'SYN-PROFILE', 'profile_version': '1',
        'expected_source_sha256': src,
        'adjudication_ledger': {'ledger_id': 'SYN-LEDGER', 'ledger_version': '1', 'sha256': sha(lp)},
        'scope': {'layout': 'Model', 'space_id': 'SYN', 'bounds': {'xmin': 0, 'xmax_exclusive': 6000, 'ymin': 0, 'ymax_exclusive': 3000}},
        'unit': {'factor_to_output': .001, 'output_coordinate_unit': 'm', 'source_id': 'SYN-DXF-INSUNITS-4'},
        'block_expansion': {'max_depth': 8}, 'consumer_skill': 'qto-electrical-routes',
        'rules': [{'rule_id': 'SYN-RULE', 'adjudication_id': 'MODEL-INCLUDE', 'block_pattern': '^SYNTHETIC_MARK$',
                   'decision': 'include', 'priority': 10, 'category': '合成构件', 'system': '合成系统', 'spec': '方框S',
                   'unit': '个', 'quantity_level': '规则推算量',
                   'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable',
                                      'reason': '合成构件对电气线路无端口'}]}],
    }
    pp = tmp_path / 'profile.json'; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    adapted = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', neutral / 'CAD中性取证清单.json',
                  '--profile', pp, '--adjudication-ledger', lp, '--output-dir', out)
    assert adapted.returncode == 0, adapted.stdout + adapted.stderr
    read = lambda name: json.loads((out / name).read_text(encoding='utf-8'))
    return second_id, read('点位识别报告.json'), read('点位端点交接输入.json'), read('点位至网络端点交接.json')


def test_mixed_drawing_gap_reaches_handoff(tmp_path):
    second_id, report, handoff_input, handoff = build_case(tmp_path, mixed=True, declare_all=True)
    assert report['point_workpackage_complete'] is False and report['unmatched_recognition_object_ids'] == [second_id]
    assert handoff_input['source_workpackage_complete'] is False
    assert handoff_input['source_unmatched_recognition_object_ids'] == [second_id]
    assert handoff['source_workpackage_complete'] is False and handoff['status'] == 'REVIEW_REQUIRED'
    assert [gap['object_id'] for gap in handoff['source_gaps']] == [second_id]
    # 已承接的合法子集照常交付；来源缺口与端口未决分开计
    assert handoff['source_physical_instance_count'] == 1 and handoff['not_applicable_count'] == 1
    assert handoff['unresolved_count'] == 0 and handoff['source_unresolved_instance_count'] == 0


@pytest.mark.parametrize('mixed,declare_all', [(False, True), (True, False)])
def test_complete_sources_stay_ready(tmp_path, mixed, declare_all):
    # 纯块两处全部承接；混合图只申报块子集（识别范围由台账声明，不据此判自动识图缺陷）
    _, report, handoff_input, handoff = build_case(tmp_path, mixed=mixed, declare_all=declare_all)
    assert report['point_workpackage_complete'] is True and report['unmatched_recognition_object_ids'] == []
    assert handoff_input['source_workpackage_complete'] is True
    assert handoff['source_workpackage_complete'] is True and handoff['status'] == 'READY'
    assert handoff.get('source_gaps', []) == []   # 合法对照：修复前后行为相同，新字段缺省按无缺口
    assert handoff['source_physical_instance_count'] == (2 if not mixed else 1)


def test_handoff_rejects_complete_claim_with_known_gaps(tmp_path):
    _, _, handoff_input, _ = build_case(tmp_path, mixed=True, declare_all=True)
    handoff_input['source_workpackage_complete'] = True
    bad = tmp_path / 'bad.json'; bad.write_text(json.dumps(handoff_input, ensure_ascii=False), encoding='utf-8')
    result = run(POINTS / 'build_network_endpoint_handoff.py', '--input', bad, '--output', tmp_path / 'bad_out.json')
    assert result.returncode == 2 and '不得为 true' in result.stderr
    # 去掉缺口后可声明完整；只剩来源未决实例时仍不得声明完整
    for extra, expected in (({}, 0), ({'source_unresolved_instance_count': 1}, 2)):
        variant = {**handoff_input, 'source_unmatched_recognition_object_ids': [], **extra}
        bad.write_text(json.dumps(variant, ensure_ascii=False), encoding='utf-8')
        result = run(POINTS / 'build_network_endpoint_handoff.py', '--input', bad, '--output', tmp_path / 'bad_out.json')
        assert result.returncode == expected, result.stderr
