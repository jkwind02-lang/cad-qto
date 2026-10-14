"""infer_pipe_network: a 2-D valve point on the unique topology bridge must not abort the network.

Registered 20261014-2226: node coordinates are lifted to 3-D, the valve point kept its input
dimension, and `distance()` failed with "同一网络坐标维度不一致" so no segment was quantified.
The 2-D input must give the same result as the identical input with the valve point in 3-D.
Legal controls: direct contact and evidence-bound connection paths are unchanged. Synthetic only.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/qto-pipe-networks/scripts/infer_pipe_network.py'
FIXTURE = Path(__file__).resolve().parent / 'fixtures/pipe_valve_bridge_2d/valve_2d_input.json'


def run(tmp_path, data, name):
    source = tmp_path / f'{name}.json'
    source.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    target = tmp_path / f'{name}.out.json'
    completed = subprocess.run([sys.executable, '-B', str(SCRIPT), '--input', str(source), '--output', str(target)],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    return completed, (json.loads(target.read_text(encoding='utf-8')) if target.exists() else None)


def comparable(result):
    result = copy.deepcopy(result)
    for key in list(result):
        if 'path' in key or key in ('input', 'generated_at', 'output'):
            result.pop(key)
    return result


def test_two_dimensional_valve_point_on_unique_bridge_matches_three_dimensional(tmp_path):
    base = json.loads(FIXTURE.read_text(encoding='utf-8'))
    assert base['valves'][0]['point'] == [1, 0]
    two_d, result_2d = run(tmp_path, base, 'valve_2d')
    assert two_d.returncode == 0, two_d.stdout + two_d.stderr
    lifted = copy.deepcopy(base)
    lifted['valves'][0]['point'] = [1, 0, 0]
    three_d, result_3d = run(tmp_path, lifted, 'valve_3d')
    assert three_d.returncode == 0, three_d.stdout + three_d.stderr

    valve = next(item for item in result_2d['valves'] if item['valve_id'] == 'V')
    assert valve['connection_method'] == 'unique_topology_bridge'
    assert valve['dn'] == 'DN25'
    assert valve['point'] == [1.0, 0.0, 0.0]
    assert comparable(result_2d) == comparable(result_3d)


def test_direct_contact_and_evidence_bound_paths_unchanged(tmp_path):
    base = json.loads(FIXTURE.read_text(encoding='utf-8'))
    contact = copy.deepcopy(base)
    contact['valves'][0]['point'] = [0.5, 0]  # on LEFT itself
    completed, result = run(tmp_path, contact, 'contact')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    valve = next(item for item in result['valves'] if item['valve_id'] == 'V')
    assert valve['connection_method'] == 'exact_segment_contact'

    bound = copy.deepcopy(base)
    bound['valves'][0].update(connected_segment_ids=['NEAR'], connection_source_id='EV-VALVE-CONN')
    completed, result = run(tmp_path, bound, 'bound')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    valve = next(item for item in result['valves'] if item['valve_id'] == 'V')
    assert valve['connection_method'] == 'evidence_bound_connection'
    assert valve['dn'] == 'DN65'
