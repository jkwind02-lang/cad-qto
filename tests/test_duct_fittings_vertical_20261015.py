"""Duct fitting centreline and vertical runs (registration 20261015-0026, 风管管件与竖向段).

Synthetic plan: elbow junction, single-ended tap into a main, reducer halves, an
in-line accessory gap bridged for continuity but excluded from length, an in-floor
offset (上下翻) with one scenario-level elevation, a branch dropping onto a main, an
inter-floor riser matched across floors, and a single-ended elbow whose
destination is unknown (pending, never estimated).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills' / 'qto-duct-air-systems' / 'scripts' / 'infer_duct_fittings_vertical.py'
sys.path.insert(0, str(SCRIPT.parent))
import infer_duct_fittings_vertical as duct


def seg(sid, floor, start, end, w=1000, h=500, el=None, ref='bottom', level='确认', system='SME'):
    item = dict(segment_id=sid, floor_id=floor, system=system, start=start, end=end, width=w, height=h, size_evidence_level='确认', source_ids=[f'EV-{sid}'])
    if el is not None:
        item['elevation'] = dict(value=el, reference=ref, evidence_level=level, source_id=f'TAG-{sid}')
    return item


def fit(fid, floor, kind, bbox, **extra):
    return dict(fitting_id=fid, floor_id=floor, kind=kind, bbox=bbox, source_id=f'BLK-{fid}', **extra)


@pytest.fixture
def plan():
    return dict(
        schema_version='qto-duct-fitting-vertical-input/1.0', package_id='SYN', unit='mm', output_unit='m',
        floors=[dict(floor_id='1F', level=0.0, level_source_id='D035'), dict(floor_id='2F', level=5.5, level_source_id='D035', plan_offset=[0, 100000])],
        parameters=dict(parameter_basis='合成图：端点吻合 5mm，部件断口 30~2600mm，块范围容差 15mm'),
        segments=[
            seg('S1', '1F', [0, 0], [9500, 0], el=3.0),                       # main, BL 3.0 -> centre 3.25
            seg('S2', '1F', [10000, 500], [10000, 8000], el=3.0),             # after elbow E1
            seg('S3', '1F', [5000, 700], [5000, 4000], w=500, h=300),         # tap branch, no tag
            seg('S4', '1F', [12000, 0], [15000, 0], el=3.0),                  # reducer large side
            seg('S5', '1F', [15400, 0], [20000, 0], w=800, h=400, el=3.0),    # reducer small side
            seg('S6', '1F', [20400, 0], [25000, 0], w=800, h=400),            # across accessory gap, no tag
            seg('S7', '1F', [26000, 0], [30000, 0], w=800, h=400, el=1.5, level='方案暂估'),  # after offset, assumed elevation
            seg('S8', '1F', [0, 20000], [5000, 20000], w=500, h=300, el=2.0),  # single end, destination unknown
            seg('S10', '1F', [40000, 0], [45000, 0], el=3.0),                 # riser bottom
            seg('S11', '2F', [40000, 100000], [45000, 100000], w=800, h=400, el=2.0, ref='top'),  # riser top, centre 1.8
            seg('S12', '1F', [50000, 3000], [50000, 8000], w=500, h=300, el=1.0),  # branch dropping onto main S13
            seg('S13', '1F', [46000, 2800], [56000, 2800], el=3.0),
        ],
        fittings=[
            fit('E1', '1F', 'elbow', [9400, -600, 10600, 600]),
            fit('T1', '1F', 'tap', [4600, -100, 5400, 900]),
            fit('R1', '1F', 'reducer', [14900, -600, 15500, 600]),
            fit('E2', '1F', 'elbow', [24800, -600, 26200, 600]),
            fit('E2dup', '1F', 'elbow', [24810, -590, 26190, 590]),
            fit('E3', '1F', 'elbow', [4800, 19400, 6000, 20600]),
            fit('E4', '1F', 'elbow', [44800, -600, 46000, 600]),
            fit('E5', '2F', 'elbow', [44800, 99400, 46000, 100600]),
            fit('E6', '1F', 'elbow', [49400, 2400, 50600, 3600]),
        ])


def run(tmp_path, data):
    ip = tmp_path / 'in.json'; ip.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    op = tmp_path / 'out.json'; op.unlink(missing_ok=True)
    p = subprocess.run([sys.executable, '-B', str(SCRIPT), '--input', str(ip), '--output', str(op)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    return p, json.loads(op.read_text(encoding='utf-8')) if op.exists() else None


def by_id(items, key, value):
    return next(item for item in items if item[key] == value)


def test_fitting_arms_follow_centreline_junction_rules(tmp_path, plan):
    p, out = run(tmp_path, plan)
    assert p.returncode == 0, p.stdout + p.stderr
    assert out['status'] == 'REFERENCE_ONLY' and out['business_adoption_allowed'] is False
    f = {item['fitting_id']: item for item in out['fittings']}
    assert f['E1']['status'] == 'junction' and f['E1']['centerline_length'] == pytest.approx(1.0)
    assert f['E1']['junction'] == pytest.approx([10000, 0])
    assert f['T1']['status'] == 'tap_into_main' and f['T1']['main_segment_id'] == 'S1' and f['T1']['centerline_length'] == pytest.approx(0.7)
    assert f['R1']['status'] == 'coaxial_pair' and [a['length'] for a in f['R1']['arms']] == pytest.approx([0.2, 0.2])
    assert {a['spec'] for a in f['R1']['arms']} == {'1000x500', '800x400'}  # each half keeps its own side's spec
    assert f['E2']['status'] == 'offset_candidate' and f['E2']['centerline_length'] == pytest.approx(1.0)
    assert 'E2dup' not in f and out['duplicate_fittings'][0]['duplicate_of'] == 'E2'
    assert f['E3']['status'] == 'single_end' and f['E3']['centerline_length'] == pytest.approx(0.75)  # 1000 extent - 250 half width
    assert all(a['evidence_level'] == '规则推算' for item in out['fittings'] for a in item['arms'])


def test_accessory_gap_bridges_continuity_but_counts_no_length(tmp_path, plan):
    p, out = run(tmp_path, plan)
    gap = out['accessory_gaps'][0]
    assert sorted([gap['a'][0], gap['b'][0]]) == ['S5', 'S6'] and gap['length'] == pytest.approx(0.4) and gap['quantity_action'] == 'exclude'
    assert out['metrics']['accessory_gap_length_excluded'] == pytest.approx(0.4)
    s6 = by_id(out['straight_segments'], 'segment_id', 'S6')
    assert s6['center_elevation'] == pytest.approx(3.2) and s6['elevation_evidence_level'] == '规则推算' and 'S5' in s6['elevation_basis']
    plan['parameters']['accessory_gap_policy'] = 'include'
    _, included = run(tmp_path, plan)
    assert included['metrics']['measured_length_rule'] == pytest.approx(out['metrics']['measured_length_rule'] + 0.4)


def test_vertical_runs_are_tiered_by_evidence_and_unknown_destinations_stay_pending(tmp_path, plan):
    p, out = run(tmp_path, plan)
    v = {item['kind']: item for item in out['vertical_items']}
    offset = v['in_floor_offset']
    assert offset['fitting_ids'] == ['E2'] and offset['length'] == pytest.approx(abs(3.2 - 1.7)) and offset['evidence_level'] == '方案暂估'
    branch = v['branch_to_main']
    assert branch['fitting_ids'] == ['E6'] and branch['segment_ids'] == ['S12', 'S13']
    assert branch['length'] == pytest.approx(abs(1.15 - 3.25)) and branch['evidence_level'] == '规则推算'
    riser = v['inter_floor_riser']
    assert riser['fitting_ids'] == ['E4', 'E5'] and riser['length'] == pytest.approx((5.5 + 1.8) - (0 + 3.25)) and riser['evidence_level'] == '规则推算'
    pending = {item['kind']: item for item in out['pending']}
    assert pending['single_end_destination_unknown']['fitting_id'] == 'E3' and 'length' not in pending['single_end_destination_unknown']
    assert out['metrics']['vertical_length_rule'] == pytest.approx(2.1 + 4.05)
    assert out['metrics']['vertical_length_scenario'] == pytest.approx(1.5)
    assert out['length_summary']['SME']['方案暂估']['800x400'] == pytest.approx(1.5)
    assert out['length_summary']['SME']['确认']['1000x500'] == pytest.approx((9500 + 7500 + 3000 + 5000 + 10000) / 1000)


def test_missing_floor_level_makes_riser_pending(tmp_path, plan):
    plan['floors'][1].pop('level')
    _, out = run(tmp_path, plan)
    riser = by_id(out['vertical_items'], 'kind', 'inter_floor_riser')
    assert riser['length'] is None and riser['evidence_level'] == '未决'
    assert any(item['kind'] == 'vertical_inter_floor_riser_unresolved' for item in out['pending'])


def test_conflicting_equal_distance_elevations_are_not_averaged(tmp_path, plan):
    plan['segments'].append(seg('S9', '1F', [22000, 2000], [22000, 500], w=800, h=400, el=5.0))  # open tap onto S6: second tagged neighbour at one hop
    _, out = run(tmp_path, plan)
    assert out['open_taps'][0]['segment_id'] == 'S9' and out['open_taps'][0]['main_segment_id'] == 'S6'
    s6 = by_id(out['straight_segments'], 'segment_id', 'S6')
    assert s6['center_elevation'] is None and s6['elevation_evidence_level'] == '未决' and '多个不同标高' in s6['elevation_basis']


@pytest.mark.parametrize('mutate,message', [
    (lambda d: d['parameters'].pop('parameter_basis'), 'parameter_basis'),
    (lambda d: d.update(unit='Unitless'), '单位'),
    (lambda d: d['segments'][0].update(elevation=dict(value=3.0, reference='侧面', source_id='x')), 'reference'),
    (lambda d: d['fittings'][0].update(kind='弯头'), 'kind'),
    (lambda d: d['segments'][0].update(start=[0, 0], end=[0, 0]), '零长'),
])
def test_invalid_inputs_stop_with_reason(tmp_path, plan, mutate, message):
    mutate(plan)
    p, out = run(tmp_path, plan)
    assert p.returncode == 2 and out is None and message in p.stderr


def test_bottom_tag_without_height_cannot_give_centre_elevation(tmp_path, plan):
    by_id(plan['segments'], 'segment_id', 'S8').pop('height')   # isolated run: nothing to propagate from
    by_id(plan['segments'], 'segment_id', 'S1').pop('height')   # continuous run: centre comes from S2 across the elbow
    _, out = run(tmp_path, plan)
    s8 = by_id(out['straight_segments'], 'segment_id', 'S8')
    assert s8['center_elevation'] is None and '缺高度' in s8['elevation_basis'] and s8['spec'] == '500x?'
    s1 = by_id(out['straight_segments'], 'segment_id', 'S1')
    assert s1['center_elevation'] == pytest.approx(3.25) and s1['elevation_evidence_level'] == '规则推算' and 'S2' in s1['elevation_basis']
    assert by_id(out['pending'], 'kind', 'single_end_destination_unknown')['center_elevation'] is None


# --- registration 20261015-0152: single-end plane reading, host-declared vertical links, elevation origin ---

def test_single_end_plane_reports_both_readings_and_default_is_unchanged(tmp_path, plan):
    _, out = run(tmp_path, plan)
    e3 = by_id(out['fittings'], 'fitting_id', 'E3')
    arm = e3['arms'][0]
    assert arm['length'] == pytest.approx(0.75) and arm['plane_policy'] == 'extension_minus_half_width'
    assert arm['plane_scenarios'] == pytest.approx({'extension_minus_half_width': 0.75, 'extension_full': 1.0})
    sc = out['single_end_plane_scenarios']
    assert sc['policy'] == 'extension_minus_half_width' and sc['fitting_count'] >= 1
    assert sc['difference_full_minus_default'] == pytest.approx(sc['extension_full'] - sc['extension_minus_half_width'])
    assert sc['difference_full_minus_default'] >= 0.25 - 1e-9
    assert out['metrics']['single_end_plane_default'] == pytest.approx(sc['extension_minus_half_width'])
    # control: choosing the full-extension reading changes only the single-end arms
    plan['parameters']['single_end_plane_policy'] = 'extension_full'
    _, full = run(tmp_path, plan)
    assert by_id(full['fittings'], 'fitting_id', 'E3')['centerline_length'] == pytest.approx(1.0)
    assert by_id(full['fittings'], 'fitting_id', 'E1')['centerline_length'] == pytest.approx(1.0)
    assert full['single_end_plane_scenarios']['extension_full'] == pytest.approx(sc['extension_full'])


def test_unknown_plane_policy_is_rejected(tmp_path, plan):
    plan['parameters']['single_end_plane_policy'] = 'half'
    p, out = run(tmp_path, plan)
    assert p.returncode == 2 and out is None and 'single_end_plane_policy' in p.stderr


def test_host_declared_single_end_link_replaces_pending_and_is_marked(tmp_path, plan):
    plan['vertical_links'] = [dict(link_id='HL1', kind='single_end_to_segment', fitting_id='E3', to_segment_id='S1',
                                   source_id='SEC-01', basis='剖面显示 E3 下翻接主管 S1')]
    p, out = run(tmp_path, plan)
    assert p.returncode == 0, p.stderr
    item = by_id(out['vertical_items'], 'kind', 'host_declared_link')
    assert item['origin'] == '宿主判定' and item['link_id'] == 'HL1' and item['fitting_ids'] == ['E3']
    assert item['length'] == pytest.approx(abs(2.15 - 3.25)) and item['evidence_level'] == '规则推算'
    assert '宿主判定' in item['basis'] and 'SEC-01' in item['basis']
    assert not any(p.get('fitting_id') == 'E3' for p in out['pending'])
    assert out['metrics']['host_vertical_links'] == 1


def test_host_declared_segment_link_supersedes_automatic_offset(tmp_path, plan):
    plan['vertical_links'] = [dict(link_id='HL2', kind='segment_to_segment', segment_id='S6', to_segment_id='S7',
                                   source_id='SYS-01', basis='系统图上翻', evidence_level='确认')]
    _, out = run(tmp_path, plan)
    kinds = [item['kind'] for item in out['vertical_items']]
    assert 'in_floor_offset' not in kinds and kinds.count('host_declared_link') == 1
    item = by_id(out['vertical_items'], 'kind', 'host_declared_link')
    assert item['length'] == pytest.approx(1.5) and item['evidence_level'] == '方案暂估'  # S7 elevation is only a scenario


@pytest.mark.parametrize('link,message', [
    (dict(link_id='X', kind='single_end_to_segment', fitting_id='E1', to_segment_id='S1', source_id='s', basis='b'), '不是单端管件'),
    (dict(link_id='X', kind='segment_to_segment', segment_id='S1', to_segment_id='S1', source_id='s', basis='b'), 'segment_id'),
    (dict(link_id='X', kind='guess', segment_id='S1', to_segment_id='S2', source_id='s', basis='b'), 'kind'),
    (dict(link_id='X', kind='segment_to_segment', segment_id='S1', to_segment_id='S2', basis='b'), 'source_id'),
])
def test_invalid_host_links_stop_with_reason(tmp_path, plan, link, message):
    plan['vertical_links'] = [link]
    p, out = run(tmp_path, plan)
    assert p.returncode == 2 and out is None and message in p.stderr


def test_host_propagated_elevation_keeps_origin_and_cannot_claim_confirmed(tmp_path, plan):
    s3 = by_id(plan['segments'], 'segment_id', 'S3')
    s3['elevation'] = dict(value=2.5, reference='center', origin='host_propagated', evidence_level='规则推算', source_id='HOST-PROP-1')
    _, out = run(tmp_path, plan)
    row = by_id(out['straight_segments'], 'segment_id', 'S3')
    assert row['center_elevation'] == pytest.approx(2.5) and row['elevation_evidence_level'] == '规则推算'
    assert '宿主传播标高' in row['elevation_basis'] and 'HOST-PROP-1' in row['elevation_basis']
    s3['elevation']['evidence_level'] = '确认'
    p, out = run(tmp_path, plan)
    assert p.returncode == 2 and out is None
    s3['elevation'].pop('evidence_level')
    p, out = run(tmp_path, plan)
    assert p.returncode == 2 and 'evidence_level' in p.stderr
