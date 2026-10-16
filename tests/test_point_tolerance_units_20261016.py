"""R2（登记 20261016-1044 近似重叠容差随输出单位改变而漏检）：同位置审查容差绑定物理单位。

合成图：边长 100 mm 的方框块 BOX 插两次，第二次相对第一次偏移 dx（mm，块内单位与 INSUNITS 同）。
移植自 Codex 根因复盘反例 U01～U07、C01（纯合成，去标识），另加 A01（米与毫米输出同一布置候选一致）、
A03（显式小容差带依据正常采用）以及单位矛盾、推不出尺度两种边界。期望值按图面和物理尺寸写出。
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
SQUARE = [(-50, -50), (50, -50), (50, 50), (-50, 50)]
M_OUT = {'factor_to_output': .001, 'output_coordinate_unit': 'm'}
MM_OUT = {'factor_to_output': 1, 'output_coordinate_unit': 'mm'}
DRAWING_OUT = {'factor_to_output': 1, 'output_coordinate_unit': 'drawing_unit'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=120)


def build(tmp_path, dx=7.0, unit=M_OUT, insunits=4, listed=None, **profile_extra):
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = insunits
    doc.blocks.new('BOX').add_lwpolyline(SQUARE, close=True)
    msp = doc.modelspace()
    ids = ['PT-CAD-' + msp.add_blockref('BOX', point).dxf.handle for point in ((1000, 1000), (1000 + dx, 1000))]
    dxf = tmp_path / 'box.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    src = sha(dxf)
    entry = {'adjudication_id': 'M-BOX', 'subject_id': 'BOX', 'professional_owner': 'qto-components-points',
             'decision': 'include', 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
             'recognized_at': '2026-10-16T00:00:00+00:00', 'source_id': 'SYN', 'evidence_ids': ['G'],
             'object_ids': ids if listed is None else [ids[i] for i in listed], 'basis': '合成图例',
             'scope_basis': '全图', 'identity_basis': '源句柄', 'cross_check': '按图面布置核对', 'uncertainties': ['合成']}
    ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1',
              'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
              'evidence': [{'evidence_id': 'G', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                            'kind': 'cad_property', 'locator': 'Model', 'observation': '合成'}], 'entries': [entry]}
    lp = tmp_path / 'ledger.json'; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model'}, 'unit': {**unit, 'source_id': f'INSUNITS={insunits}'},
               'rules': [{'rule_id': 'BOX', 'adjudication_id': 'M-BOX', 'block_pattern': '^BOX$', 'decision': 'include',
                          'category': '配电箱', 'system': 'S', 'spec': '箱', 'unit': '台', 'quantity_level': '规则推算量',
                          'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}],
               **profile_extra}
    pp = tmp_path / 'profile.json'; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    result = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', neutral / 'CAD中性取证清单.json',
                 '--profile', pp, '--adjudication-ledger', lp, '--output-dir', out)
    return ids, result, out


def summary(result, out):
    assert result.returncode == 0, result.stderr
    report = json.loads((out / '点位识别报告.json').read_text(encoding='utf-8'))
    review = report['duplicate_review']
    return {'groups': review['candidate_group_count'], 'count': report['counts']['adopted_physical_instance_count'],
            'complete': report['point_workpackage_complete'], 'status': report['status'],
            'scale_status': review['scale_status'], 'near_mm': review['near_tolerance_mm_effective'],
            'members': [g['physical_ids'] for g in review['groups']], 'review': review}


def test_u01_metre_output_7mm_overlay_is_a_candidate(tmp_path):
    s = summary(*build(tmp_path)[1:])
    assert (s['groups'], s['count'], s['complete']) == (1, 2, False)
    assert s['near_mm'] == pytest.approx(50.0)


def test_u02_millimetre_output_default_is_50mm_not_005mm(tmp_path):
    s = summary(*build(tmp_path, unit=MM_OUT)[1:])
    assert (s['groups'], s['count'], s['complete']) == (1, 2, False)
    assert s['review']['near_tolerance'] == pytest.approx(50.0) and s['near_mm'] == pytest.approx(50.0)


def test_u03_drawing_unit_scale_comes_from_insunits_and_factor(tmp_path):
    s = summary(*build(tmp_path, unit=DRAWING_OUT)[1:])
    assert (s['groups'], s['count'], s['complete']) == (1, 2, False)
    assert s['scale_status'] == 'bound' and 'INSUNITS=4' in s['review']['scale_basis']


def test_u03_unknown_scale_is_incomplete_not_a_default_and_not_an_exit(tmp_path):
    s = summary(*build(tmp_path, unit=DRAWING_OUT, insunits=0)[1:])
    assert s['scale_status'] == 'unbound' and s['review']['review_performed'] is False
    assert (s['count'], s['complete'], s['status']) == (2, False, 'REVIEW_REQUIRED')
    assert s['near_mm'] is None and s['review']['near_tolerance'] is None


def test_unknown_scale_with_explicit_output_tolerance_and_basis_is_reviewed(tmp_path):
    s = summary(*build(tmp_path, unit=DRAWING_OUT, insunits=0,
                       duplicate_check={'near_tolerance': 50, 'tolerance_basis': '图框实测 1 单位 = 1 mm'})[1:])
    assert s['scale_status'] == 'explicit_output_units' and (s['groups'], s['complete']) == (1, False)


def test_u04_millimetre_output_explicit_50_with_basis(tmp_path):
    s = summary(*build(tmp_path, unit=MM_OUT, duplicate_check={'near_tolerance': 50, 'tolerance_basis': '本图设备最小间距 0.5 m'})[1:])
    assert (s['groups'], s['count'], s['complete']) == (1, 2, False)


def test_u05_resolved_overlay_keeps_one(tmp_path):
    ids, result, out = build(tmp_path / 'probe', unit=MM_OUT)
    ids, result, out = build(tmp_path / 'resolved', unit=MM_OUT,
                             duplicate_resolutions=[{'physical_ids': ids, 'keep': [ids[0]], 'basis': '7 mm 重描，同一台箱'}])
    s = summary(result, out)
    assert (s['groups'], s['count'], s['complete']) == (1, 1, True)


def test_u06_two_boxes_100mm_apart_stay_two(tmp_path):
    s = summary(*build(tmp_path, dx=100.0, unit=MM_OUT)[1:])
    assert (s['groups'], s['count'], s['complete']) == (0, 2, True)


def test_u07_two_real_boxes_at_one_point_kept_with_basis(tmp_path):
    ids, _, _ = build(tmp_path / 'probe', dx=0.0, unit=MM_OUT)
    ids, result, out = build(tmp_path / 'kept', dx=0.0, unit=MM_OUT,
                             duplicate_resolutions=[{'physical_ids': ids, 'keep': ids, 'basis': '系统图两台箱叠放'}])
    s = summary(result, out)
    assert (s['groups'], s['count'], s['complete']) == (1, 2, True)


def test_c01_unlisted_source_object_keeps_workpackage_incomplete(tmp_path):
    s = summary(*build(tmp_path, dx=100.0, unit=MM_OUT, listed=[0])[1:])
    assert (s['groups'], s['count'], s['complete']) == (0, 1, False)


def test_a01_metre_and_millimetre_output_flag_the_same_members(tmp_path):
    m = summary(*build(tmp_path / 'm')[1:])
    mm = summary(*build(tmp_path / 'mm', unit=MM_OUT)[1:])
    assert len(m['members']) == len(mm['members']) == 1
    assert m['near_mm'] == pytest.approx(mm['near_mm'])
    assert m['review']['groups'][0]['max_distance'] * 1000 == pytest.approx(mm['review']['groups'][0]['max_distance'])


def test_a03_explicit_tighter_tolerance_with_basis_is_used_as_given(tmp_path):
    s = summary(*build(tmp_path, duplicate_check={'near_tolerance_mm': 5, 'tolerance_basis': '本图设备紧贴布置，最小间距 6 mm'})[1:])
    assert (s['groups'], s['count'], s['complete']) == (0, 2, True)
    assert s['near_mm'] == pytest.approx(5.0) and s['review']['tolerance_source']['near'] == '显式（毫米）'


def test_output_tolerance_without_basis_is_rejected(tmp_path):
    _, result, _ = build(tmp_path, duplicate_check={'near_tolerance': 0.05})
    assert result.returncode == 2 and 'tolerance_basis' in result.stderr


def test_declared_unit_contradicting_insunits_and_factor_is_rejected(tmp_path):
    _, result, _ = build(tmp_path, unit={'factor_to_output': 1, 'output_coordinate_unit': 'm'})
    assert result.returncode == 2 and '矛盾' in result.stderr


def test_declared_unit_conflict_with_basis_is_kept_as_a_note(tmp_path):
    s = summary(*build(tmp_path, insunits=6, unit={**M_OUT, 'unit_conflict_basis': '图框实测：INSUNITS 写成米，实为毫米'})[1:])
    assert s['scale_status'] == 'bound' and s['near_mm'] == pytest.approx(50.0) and s['review']['scale_notes']


def test_unitless_drawing_with_declared_millimetres_is_bound_by_the_name(tmp_path):
    s = summary(*build(tmp_path, unit=MM_OUT, insunits=0)[1:])
    assert s['scale_status'] == 'bound' and s['groups'] == 1
