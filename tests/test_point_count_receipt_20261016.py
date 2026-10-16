"""R1（登记 20261016-1044 点位算术计数与几何审查脱节）：计数入口绑定适配器的几何审查凭据。

用 R2 测试的合成图（边长 100 mm 方框块插两次）跑适配器，再改动计数输入喂给计数器。
K01、K02 改用合成的两行（不读真实项目文件）；A07 在适配器计数输入上加一个点。
算术与退出码不变：K01 仍 0 退出、数量 2，K02 仍 2 退出。
"""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_point_tolerance_units_20261016 import MM_OUT, DRAWING_OUT, POINTS, build

COUNTER = POINTS / 'count_reconciled_instances.py'


def count(tmp_path, data, name='input.json'):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    result = subprocess.run([sys.executable, '-B', str(COUNTER), str(path)], capture_output=True, text=True,
                            encoding='utf-8', stdin=subprocess.DEVNULL, timeout=60)
    return result.returncode, json.loads(result.stdout)


def adapter_input(out):
    return json.loads((out / '点位唯一实例计数输入.json').read_text(encoding='utf-8'))


def summary(out):
    return json.loads((out / '点位数量汇总.json').read_text(encoding='utf-8'))


def resolved(tmp_path):
    ids, _, _ = build(tmp_path / 'probe', unit=MM_OUT)
    ids, result, out = build(tmp_path / 'resolved', unit=MM_OUT,
                             duplicate_resolutions=[{'physical_id': None, 'physical_ids': ids, 'keep': [ids[0]],
                                                     'basis': '7 mm 重描，同一台箱'}])
    assert result.returncode == 0, result.stderr
    return out


def test_adapter_count_input_carries_a_matching_receipt_and_ready_only_when_reviewed(tmp_path):
    out = resolved(tmp_path)
    data = adapter_input(out)
    receipt = data['geometry_review']
    assert receipt['schema'] == 'qto-point-geometry-review/1.0' and receipt['instance_count'] == 1
    assert receipt['pending_group_count'] == 0 and receipt['workpackage_complete'] is True
    assert receipt['near_tolerance_mm_effective'] == 50.0
    s = summary(out)
    assert s['geometry_review']['status'] == 'matched' and s['work_quantity_ready'] is True
    assert s['unique_instance_count'] == 1 and s['business_adoption_allowed'] is False


def test_pending_overlay_matches_but_is_not_ready(tmp_path):
    _, result, out = build(tmp_path, unit=MM_OUT)
    assert result.returncode == 0, result.stderr
    s = summary(out)
    assert s['geometry_review']['status'] == 'matched' and s['work_quantity_ready'] is False
    assert '未处置重复候选组 1' in s['geometry_review']['reason'] and s['unique_instance_count'] == 2


def test_unbound_scale_is_not_ready(tmp_path):
    _, result, out = build(tmp_path, unit=DRAWING_OUT, insunits=0)
    assert result.returncode == 0, result.stderr
    s = summary(out)
    assert s['work_quantity_ready'] is False and '尺度未绑定' in s['geometry_review']['reason']


def test_a07_adding_one_point_to_a_reviewed_input_breaks_the_receipt(tmp_path):
    data = adapter_input(resolved(tmp_path))
    extra = copy.deepcopy(data['reconciled_instances'][0])
    extra['physical_id'] = 'PT-CAD-NEWBATCH-1'
    extra['point'] = [extra['point'][0] + 0.007, extra['point'][1]] + extra['point'][2:]
    data['reconciled_instances'].append(extra)
    code, out = count(tmp_path, data)
    assert code == 0 and out['unique_instance_count'] == 2
    assert out['geometry_review']['status'] == 'mismatch' and out['work_quantity_ready'] is False


def test_moving_a_point_breaks_the_receipt(tmp_path):
    data = adapter_input(resolved(tmp_path))
    data['reconciled_instances'][0]['point'][0] += 1.0
    code, out = count(tmp_path, data)
    assert code == 0 and out['geometry_review']['status'] == 'mismatch' and out['work_quantity_ready'] is False


def test_k01_hand_made_rows_are_arithmetic_only(tmp_path):
    basis = adapter_input(resolved(tmp_path))['recognition_basis']
    rows = [{'physical_id': f'K01-{i}', 'category': '灯具', 'system': 'S', 'spec': '应急灯', 'unit': '个',
             'evidence': '项目自造列表'} for i in (1, 2)]
    code, out = count(tmp_path, {'reconciled_instances': rows, 'recognition_basis': basis})
    assert code == 0 and out['unique_instance_count'] == 2
    assert out['geometry_review']['status'] == 'absent' and out['work_quantity_ready'] is False


def test_k02_duplicate_id_is_still_rejected(tmp_path):
    basis = adapter_input(resolved(tmp_path))['recognition_basis']
    row = {'physical_id': 'K02-1', 'category': '灯具', 'system': 'S', 'spec': '应急灯', 'unit': '个', 'evidence': '自造'}
    code, out = count(tmp_path, {'reconciled_instances': [row, dict(row)], 'recognition_basis': basis})
    assert code == 2 and out['status'] == 'INPUT_REQUIRED'


@pytest.mark.parametrize('scale', [None, '', 'guessed', 'BOUND'])
def test_missing_or_unknown_scale_status_is_not_ready(tmp_path, scale):
    # Cross-check B4A-R1-01: only the literal "unbound" used to block; a receipt without a known scale passed.
    data = adapter_input(resolved(tmp_path))
    if scale is None:
        del data['geometry_review']['scale_status']
    else:
        data['geometry_review']['scale_status'] = scale
    code, out = count(tmp_path, data)
    assert code == 0 and out['geometry_review']['status'] == 'matched' and out['work_quantity_ready'] is False
    assert '尺度状态' in out['geometry_review']['reason']


@pytest.mark.parametrize('scale', ['bound', 'explicit_output_units'])
def test_known_review_scales_stay_ready(tmp_path, scale):
    data = adapter_input(resolved(tmp_path))
    data['geometry_review']['scale_status'] = scale
    code, out = count(tmp_path, data)
    assert code == 0 and out['work_quantity_ready'] is True


def test_receipt_of_another_schema_is_a_mismatch(tmp_path):
    data = adapter_input(resolved(tmp_path))
    data['geometry_review']['schema'] = 'something-else/1.0'
    code, out = count(tmp_path, data)
    assert code == 0 and out['geometry_review']['status'] == 'mismatch'
