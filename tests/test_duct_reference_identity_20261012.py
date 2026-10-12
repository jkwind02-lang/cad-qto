"""Identity contract for explicitly enumerated reference duct sections."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/qto-duct-air-systems/scripts/calculate_straight_duct_reference.py'


def section(identifier='synthetic-segment-A', count=1):
    return {'id': identifier, 'system': 'synthetic fixture', 'shape': 'rectangle',
            'count': count, 'length_each_m': 3, 'width_m': .4, 'height_m': .2,
            'evidence': 'Synthetic arithmetic fixture; no project drawing or quantity.'}


def run(tmp_path, rows):
    p = tmp_path / 'input.json'
    p.write_text(json.dumps({'straight_sections': rows}, ensure_ascii=False), encoding='utf-8')
    result = subprocess.run([sys.executable, str(SCRIPT), str(p)],
                            capture_output=True, text=True, encoding='utf-8')
    return result, json.loads(result.stdout)


def test_same_stable_segment_cannot_enter_twice(tmp_path):
    original = section()
    result, data = run(tmp_path, [original, copy.deepcopy(original)])
    assert result.returncode == 2
    assert data['status'] == 'INPUT_REQUIRED'
    assert 'id重复' in data['error']
    assert 'items' not in data


@pytest.mark.parametrize('identifier', ['', '   ', None, True, 7])
def test_segment_identity_must_be_nonempty_text(tmp_path, identifier):
    result, data = run(tmp_path, [section(identifier)])
    assert result.returncode == 2
    assert 'id必须为非空文本' in data['error']


def test_distinct_segments_with_equal_geometry_stay_distinct(tmp_path):
    result, data = run(tmp_path, [section('synthetic-A'), section('synthetic-B')])
    assert result.returncode == 0
    assert data['status'] == 'REFERENCE_ONLY'
    assert data['business_adoption_allowed'] is False
    assert [r['id'] for r in data['items']] == ['synthetic-A', 'synthetic-B']
    assert sum(r['total_centerline_length_m'] for r in data['items']) == 6


def test_explicit_group_count_keeps_existing_arithmetic(tmp_path):
    result, data = run(tmp_path, [section(count=2)])
    assert result.returncode == 0
    assert data['items'][0]['total_centerline_length_m'] == 6
    assert data['items'][0]['theoretical_surface_m2'] == 7.2
