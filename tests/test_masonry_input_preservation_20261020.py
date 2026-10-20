"""Anonymous CLI cases: calculation output must preserve its source facts."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/qto-masonry-secondary/scripts/calculate_masonry_reference.py'
WALL = {'wall_segments': [{'id': 'W-01', 'length_m': 4, 'height_m': 3,
    'thickness_m': 0.2, 'opening_union_area_m2': 2, 'embedded_volume_m3': 0.1,
    'evidence': 'anonymous dimensioned wall'}]}


def prepare(tmp_path):
    source = tmp_path / 'wall.json'
    original = (json.dumps(WALL, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    source.write_bytes(original)
    return source, original


def call(source, output=None, cwd=None):
    args = [sys.executable, '-B', str(SCRIPT), str(source)]
    if output is not None:
        args.extend(['--output', str(output)])
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8')


@pytest.mark.parametrize('alias_kind', ['exact', 'relative', 'hardlink'])
def test_output_alias_rejected_without_losing_input_facts(tmp_path, alias_kind):
    source, original = prepare(tmp_path)
    if alias_kind == 'exact':
        target = source
    elif alias_kind == 'relative':
        (tmp_path / 'nested').mkdir()
        target = Path('nested') / '..' / source.name
    else:
        target = tmp_path / 'different-name.json'
        os.link(source, target)
        assert source.samefile(target) and source.resolve() != target.resolve()
    done = call(source, target, cwd=tmp_path)
    result = json.loads(done.stdout)
    assert done.returncode == 2 and result['status'] == 'INPUT_REQUIRED'
    assert result['stop'] is True and '独立输出路径' in result['error']
    assert source.read_bytes() == original
    assert (tmp_path / target).read_bytes() == original
    assert json.loads(source.read_bytes()) == WALL


def test_distinct_existing_output_and_stdout_preserve_input_and_calculation(tmp_path):
    source, original = prepare(tmp_path)
    output = tmp_path / 'result.json'
    output.write_text('previous calculation', encoding='utf-8')
    written = call(source, output)
    standard = call(source)
    assert written.returncode == standard.returncode == 0
    assert source.read_bytes() == original
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result == json.loads(standard.stdout)
    assert result['status'] == 'REFERENCE_ONLY'
    assert result['business_adoption_allowed'] is False
    item = result['items'][0]
    assert item['gross_area_m2'] == 12
    assert item['net_area_m2'] == 10
    assert item['net_volume_m3'] == pytest.approx(1.9)
    assert item['evidence'] == WALL['wall_segments'][0]['evidence']


def test_new_distinct_output_preserves_input(tmp_path):
    source, original = prepare(tmp_path)
    output = tmp_path / 'new-result.json'
    done = call(source, output)
    assert done.returncode == 0 and output.is_file()
    assert source.read_bytes() == original
    assert json.loads(output.read_text(encoding='utf-8'))['items'][0]['net_volume_m3'] == pytest.approx(1.9)
