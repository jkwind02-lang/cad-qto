"""Claude cross review of Codex 0.3.5 (df4a817): counterexamples and legal controls for its fixes.

Synthetic geometry only. Expected values are fixed from the drawn geometry beforehand,
never read back from the code under test.
"""
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for relative in ('skills/_qto-shared-evidence/scripts', 'skills/qto-pipe-networks/scripts', 'skills/qto-duct-air-systems/scripts'):
    sys.path.insert(0, str(ROOT / relative))
import infer_duct_fittings_vertical as duct
import qto_cad_source_contract as units
import build_pipe_inference_inputs_batch as batch


def seg(sid, system='SYS-A', start=(0, 0), end=(1000, 0), elev=None, floor='F1', width=1000, height=320, diameter=None):
    row = dict(segment_id=sid, system=system, start=list(start), end=list(end), floor_id=floor,
               size_evidence_level='确认', source_ids=['S-' + sid])
    if diameter is None:
        row.update(width=width, height=height)
    else:
        row['diameter'] = diameter
    if elev is not None:
        row['elevation'] = dict(value=elev, reference='center', origin='tag', evidence_level='确认', source_id='T-' + sid)
    return row


def data(segments, fittings=(), **extra):
    return dict(schema_version=duct.INPUT_SCHEMA, unit='mm', output_unit='m', parameters={'parameter_basis': 'synthetic'},
                segments=list(segments), fittings=list(fittings), **extra)


def infer(value):
    original = copy.deepcopy(value)
    result = duct.infer(value)
    assert value == original and result['business_adoption_allowed'] is False
    return result


def single(fid, bbox, sid='A', which='end', g=None, kind='elbow'):
    row = dict(fitting_id=fid, kind=kind, floor_id='F1', bbox=list(bbox), ends=[{'segment_id': sid, 'end': which}])
    if g is not None:
        row['single_end_geometry'] = g
    return row


def vertical(**extra):
    # block: flange 0..3, riser projection 35..355 (= duct height 320), centre 195, far flange to 385
    return dict(source_id='BLK', basis='synthetic turn block', endpoint_relation='at_connection', turn_plane='vertical', **extra)


def elevation(result, sid):
    return next(s['center_elevation'] for s in result['straight_segments'] if s['segment_id'] == sid)


# --- D1: a rotated block's axis-aligned bbox overstates its extent along the pipe ---

def rotated(angle_deg=45., extent=385., half=500.):
    a = math.radians(angle_deg)
    u, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    end = (1000 * u[0], 1000 * u[1])
    corners = [(end[0] + s * u[0] + p * n[0], end[1] + s * u[1] + p * n[1]) for s in (0, extent) for p in (-half, half)]
    bbox = [min(x for x, _ in corners), min(y for _, y in corners), max(x for x, _ in corners), max(y for _, y in corners)]
    return end, u, bbox


@pytest.mark.parametrize('angle', [45., 30., 5.])
def test_rotated_outer_face_needs_measured_extent_and_never_uses_bbox(angle):
    end, u, bbox = rotated(angle)
    r = infer(data([seg('A', end=end)], [single('E', bbox, g=vertical(flange_allowance=30))]))
    fit = r['fittings'][0]
    assert fit['centerline_length'] is None and fit['plane_extension_basis'] == 'rotated_bbox_upper_bound'
    assert any(p['kind'] == 'single_end_plane_unresolved' and 'extent_along_pipe' in p['reason'] for p in r['pending'])
    assert r['single_end_plane_scenarios']['rotated_bbox_count'] == 1
    r = infer(data([seg('A', end=end)], [single('E', bbox, g=vertical(flange_allowance=30, extent_along_pipe=385))]))
    assert r['fittings'][0]['centerline_length'] == pytest.approx(.195)
    assert r['fittings'][0]['plane_extension_basis'] == 'host_measured_along_pipe'


def test_rotated_centre_and_projection_evidence_are_unaffected():
    end, u, bbox = rotated()
    for g in (vertical(centerline_point=[end[0] + 195 * u[0], end[1] + 195 * u[1]]), vertical(riser_projection=[35, 355])):
        assert infer(data([seg('A', end=end)], [single('E', bbox, g=g)]))['fittings'][0]['centerline_length'] == pytest.approx(.195)


def test_measured_extent_must_agree_with_bbox():
    # axis-aligned: the bbox extent is exact, a different stated extent is a conflict
    r = infer(data([seg('A')], [single('E', [1000, -500, 1385, 500], g=vertical(flange_allowance=30, extent_along_pipe=420))]))
    assert r['fittings'][0]['centerline_length'] is None
    r = infer(data([seg('A')], [single('E', [1000, -500, 1385, 500], g=vertical(flange_allowance=30, extent_along_pipe=385))]))
    assert r['fittings'][0]['centerline_length'] == pytest.approx(.195)
    # rotated: the stated extent may not exceed the bbox upper bound
    end, u, bbox = rotated()
    r = infer(data([seg('A', end=end)], [single('E', bbox, g=vertical(flange_allowance=30, extent_along_pipe=5000))]))
    assert r['fittings'][0]['centerline_length'] is None


def test_axis_aligned_outer_face_control_and_round_duct():
    r = infer(data([seg('A', start=(1000, 0), end=(2000, 0))], [single('E', [615, -500, 1000, 500], which='start', g=vertical(flange_allowance=30))]))
    assert r['fittings'][0]['centerline_length'] == pytest.approx(.195)
    assert r['fittings'][0]['plane_extension_basis'] == 'axis_aligned_bbox'
    r = infer(data([seg('A', diameter=400)], [single('E', [1000, -200, 1450, 200], g=vertical(flange_allowance=30))]))
    assert r['fittings'][0]['centerline_length'] == pytest.approx(.220)   # 450 - 400/2 - 30


# --- Codex review of the patch (6f72bcd), D1-R1: near-axis bbox error is bounded, not assumed away ---

@pytest.mark.parametrize('angle', [.5, -.5, 89.5, 179.5])
def test_near_axis_wide_block_needs_measured_extent(angle):
    # 2000-wide block 0.5 deg off an axis: the bbox far corner overshoots by 2000*sin*cos = 17.45 mm (> 15 mm)
    end, u, bbox = rotated(angle, half=1000.)
    overshoot = 2000 * abs(math.sin(math.radians(angle)) * math.cos(math.radians(angle))) / 1000
    r = infer(data([seg('A', end=end, width=2000)], [single('E', bbox, g=vertical(flange_allowance=30))]))
    fit = r['fittings'][0]
    assert fit['centerline_length'] is None and fit['plane_extension_basis'] == 'rotated_bbox_upper_bound'
    assert fit['plane_extension_error_bound'] >= overshoot - 1e-9
    r = infer(data([seg('A', end=end, width=2000)], [single('E', bbox, g=vertical(flange_allowance=30, extent_along_pipe=385))]))
    assert r['fittings'][0]['centerline_length'] == pytest.approx(.195)


def test_near_axis_small_block_uses_bbox_within_stated_bound():
    # 320-wide block 0.5 deg off: overshoot 320*sin*cos = 2.79 mm, inside end_snap_tolerance; the bound is reported
    end, u, bbox = rotated(.5, half=160.)
    r = infer(data([seg('A', end=end, width=320)], [single('E', bbox, g=vertical(flange_allowance=30))]))
    fit = r['fittings'][0]
    assert fit['plane_extension_basis'] == 'axis_aligned_bbox'
    assert .00279 - 1e-6 <= fit['plane_extension_error_bound'] <= .005
    assert 0 <= fit['centerline_length'] - .195 <= fit['plane_extension_error_bound'] + 1e-9
    # where the bbox stands in, a stated extent must agree with it from both sides
    for wrong in (420, 360):
        r = infer(data([seg('A', end=end, width=320)], [single('E', bbox, g=vertical(flange_allowance=30, extent_along_pipe=wrong))]))
        assert r['fittings'][0]['centerline_length'] is None


def test_pipe_parallelism_parameter_does_not_decide_bbox_use():
    end, u, bbox = rotated(5.)
    d = data([seg('A', end=end)], [single('E', bbox, g=vertical(flange_allowance=30))])
    d['parameters']['collinear_cross_limit'] = .1
    assert infer(d)['fittings'][0]['centerline_length'] is None
    d = data([seg('A')], [single('E', [1000, -500, 1385, 500], g=vertical(flange_allowance=30))])
    d['parameters']['collinear_cross_limit'] = .1
    assert infer(d)['fittings'][0]['centerline_length'] == pytest.approx(.195)


# --- D7: drawing noise behind the end is not a reversal ---

@pytest.mark.parametrize('x,expected', [(998, 0.), (995, 0.), (990, None)])
def test_already_at_center_tolerates_snap_noise_only(x, expected):
    g = vertical(centerline_point=[x, 0]); g['endpoint_relation'] = 'already_at_center'
    actual = infer(data([seg('A')], [single('E', [800, -500, 1185, 500], g=g)]))['fittings'][0]['centerline_length']
    assert actual == expected


# --- D8: a 0.3.4 host vertical link names what is missing ---

def test_legacy_vertical_link_error_names_turn_plane():
    link = dict(link_id='L1', kind='single_end_to_segment', fitting_id='E', to_segment_id='M', source_id='SEC', basis='section')
    parts = [seg('A', elev=3.), seg('M', start=(5000, -2000), end=(5000, 2000), elev=5.)]
    with pytest.raises(duct.InputError, match='turn_plane: vertical'):
        infer(data(parts, [single('E', [1000, -500, 1385, 500])], vertical_links=[link]))
    body = dict(source_id='B', basis='body', endpoint_relation='at_connection', body_length=200)
    with pytest.raises(duct.InputError, match='在线本体'):
        infer(data(parts, [single('E', [1000, -500, 1200, 500], g=body, kind='reducer')], vertical_links=[link]))
    # control: a declared vertical turn still links
    r = infer(data(parts, [single('E', [1000, -500, 1385, 500], g=vertical(centerline_point=[1195, 0]))], vertical_links=[link]))
    assert [v['kind'] for v in r['vertical_items']] == ['host_declared_link'] and r['vertical_items'][0]['length'] == pytest.approx(2.)


# --- D9 / D10: legacy policy echo and completeness of totals ---

def test_legacy_policy_is_reported_as_requested_not_applied():
    d = data([seg('A')], [single('E', [1000, -500, 1385, 500], g=vertical(flange_allowance=30))])
    d['parameters']['single_end_plane_policy'] = 'extension_full'
    s = infer(d)['single_end_plane_scenarios']
    assert s['policy'] == 'geometry_evidence' and s['legacy_policy_requested'] == 'extension_full' and s['legacy_policy_applied'] is False
    s = infer(data([seg('A')], [single('E', [1000, -500, 1385, 500], g=vertical(flange_allowance=30))]))['single_end_plane_scenarios']
    assert s['legacy_policy_requested'] is None


def test_totals_flag_known_parts_only():
    parts = [seg('A'), seg('B', start=(0, 5000), end=(1000, 5000))]
    known = single('E1', [1000, -500, 1385, 500], g=vertical(flange_allowance=30))
    unknown = single('E2', [1000, 4500, 1385, 5500], sid='B')
    r = infer(data(parts, [known, unknown]))
    m = r['metrics']
    assert m['totals_complete'] is False and m['length_unresolved_items'] >= 1
    assert m['fitting_length_rule'] == pytest.approx(.195) and r['single_end_plane_scenarios']['geometry_evidence'] is None
    r = infer(data([seg('A'), seg('B', start=(1000, 0), end=(2000, 0))]))
    assert r['metrics']['totals_complete'] is True and r['metrics']['length_unresolved_items'] == 0


# --- D11: one ambiguous physical block exported twice gives one pending item ---

def test_ambiguous_copies_collapse_but_declared_systems_stay_apart():
    parts = [seg('A', elev=3.), seg('B', 'SYS-B', (1200, 0), (2200, 0))]
    # D11's duplicate is an independently evidenced physical instance; overlap by itself is
    # insufficient (T46), including when system ownership is still ambiguous.
    block = dict(fitting_id='R1', kind='reducer', floor_id='F1', bbox=[990, -200, 1210, 200],
                 physical_identity=dict(source_id='SYN-DRAWING', instance_id='SYN-ONE-FITTING', basis='合成同物导出副本'))
    r = infer(data(parts, [block, dict(block, fitting_id='R2')]))
    assert [f['fitting_id'] for f in r['fittings']] == ['R1'] and r['duplicate_fittings'][0]['duplicate_of'] == 'R1'
    pend = [p for p in r['pending'] if p['kind'] == 'fitting_system_ambiguous']
    assert len(pend) == 1 and pend[0]['duplicate_fitting_ids'] == ['R2']
    r = infer(data(parts, [dict(block, system='SYS-A'), dict(block, fitting_id='R2', system='SYS-B')]))
    assert len(r['fittings']) == 2 and not r['duplicate_fittings']


@pytest.mark.parametrize('order', ['assigned_first', 'ambiguous_first'])
def test_system_named_like_the_internal_ambiguous_group_stays_apart(order):
    # Codex review of the patch, D11-R1: a real system may be called "ambiguous"
    parts = [seg('A', 'ambiguous', elev=3.), seg('B', 'SYS-B', (1200, 0), (2200, 0))]
    assigned = dict(fitting_id='R1', kind='reducer', floor_id='F1', bbox=[990, -200, 1210, 200], system='ambiguous')
    open_ = {k: v for k, v in assigned.items() if k != 'system'}; open_['fitting_id'] = 'R2'
    r = infer(data(parts, [assigned, open_] if order == 'assigned_first' else [open_, assigned]))
    assert sorted(f['fitting_id'] for f in r['fittings']) == ['R1', 'R2'] and not r['duplicate_fittings']
    pend = [p for p in r['pending'] if p['kind'] == 'fitting_system_ambiguous']
    assert [p['fitting_id'] for p in pend] == ['R2'] and pend[0]['duplicate_fitting_ids'] == []


# --- D12: coincident ends of different systems are flagged, never linked ---

def test_cross_system_contact_is_pending_but_not_linked():
    r = infer(data([seg('A', elev=3.), seg('B', 'SYS-B', (1000, 0), (2000, 0))]))
    assert elevation(r, 'B') is None and not r['joints']
    contact = [p for p in r['pending'] if p['kind'] == 'cross_system_contact']
    assert len(contact) == 1 and sorted(contact[0]['systems']) == ['SYS-A', 'SYS-B'] and r['counts']['cross_system_contacts'] == 1
    assert r['metrics']['totals_complete'] is True       # a hint, not a missing length
    # controls: same system is a joint without the hint; an accessory-sized gap across systems is plan overlap, no hint
    r = infer(data([seg('A', elev=3.), seg('B', 'SYS-A', (1000, 0), (2000, 0))]))
    assert len(r['joints']) == 1 and not any(p['kind'] == 'cross_system_contact' for p in r['pending'])
    r = infer(data([seg('A', elev=3.), seg('B', 'SYS-B', (1200, 0), (2200, 0))]))
    assert not r['cross_system_contacts'] and elevation(r, 'B') is None


def test_overlapping_other_system_does_not_block_same_system_gap():
    r = infer(data([seg('A', elev=3.), seg('B', 'SYS-B', (1000, 0), (2000, 0)), seg('C', 'SYS-A', (2000, 0), (3000, 0))]))
    assert elevation(r, 'B') is None and elevation(r, 'C') == 3. and len(r['accessory_gaps']) == 1


# --- frame corroboration: adjacent cases to the registration ---

@pytest.mark.parametrize('paper,measured,bbox,scale,ok', [
    ([841, 1189], [178350, 126150], [0, 0, 178350, 126150], 150, True),     # landscape frame, portrait-listed paper
    ([841, 1189], [178350, 126150], [0, 0, 126150, 178350], 150, False),    # measured listed in the wrong axis order
    ([420, 594], [84100, 59400], [0, 0, 84100, 59400], 200, False),         # A2 at 1:200 matches x only
])
def test_frame_corroboration_axis_order_and_single_edge_coincidence(paper, measured, bbox, scale, ok):
    proof = dict(kind='frame_size_scale', paper_size_mm=paper, measured_drawing_units=measured, scale_denominator=scale,
                 object_ids=['FRAME'], basis='synthetic frame')
    if ok:
        assert units.verify_unit_corroboration('mm', [proof], {'FRAME': bbox})[0]['checked_axes'] == 2
    else:
        with pytest.raises(ValueError):
            units.verify_unit_corroboration('mm', [proof], {'FRAME': bbox})


# --- B2 and nesting: unlocated objects through the real batch command ---

def run_batch(tmp_path, build, extra_families=(), pipes=True):
    import ezdxf
    import extract_cad_neutral as extract
    def put(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8'); return path
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / 'source.dwg'; source.write_bytes(b'synthetic source')
    doc = ezdxf.new(); doc.units = 4; m = doc.modelspace()
    if pipes:
        m.add_line((10, 20), (90, 20), dxfattribs={'layer': 'PIPE'})
        m.add_line((210, 80), (290, 80), dxfattribs={'layer': 'PIPE'})
    else:   # frames hold only a note: NO_PIPES unless something else keeps the batch open
        m.add_text('NOTE', dxfattribs={'insert': (50, 50), 'layer': 'NOTE'})
        m.add_text('NOTE', dxfattribs={'insert': (250, 50), 'layer': 'NOTE'})
    handles = build(doc, m)
    exchange = tmp_path / 'source.dxf'; doc.saveas(exchange)
    rows = [extract.convert(e, layout='Model', parent_block=None, source_hash=digest(source), raw_unit='Millimeters', sequence=i) for i, e in enumerate(m)]
    names = [b.name for b in doc.blocks if not b.name.startswith('*')]
    blocks = [extract.convert(e, layout='Model', parent_block=n, source_hash=digest(source), raw_unit='Millimeters', sequence=i)
              for n in names for i, e in enumerate(doc.blocks.get(n))]
    layout = tmp_path / 'entities.ndjson'; blockfile = tmp_path / 'blocks.ndjson'
    layout.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    blockfile.write_text(''.join(json.dumps(r) + '\n' for r in blocks), encoding='utf-8')
    manifest = put(tmp_path / 'manifest.json', dict(schema_version='qto-cad-neutral-evidence/1.0', geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source={'path': str(source), 'sha256': digest(source)}, exchange_dxf={'path': str(exchange), 'sha256': digest(exchange)},
        drawing_unit={'insunits_code': 4, 'raw_unit': 'Millimeters'}, blocks=[{'block_name': n, 'origin': [0, 0, 0]} for n in names],
        artifacts={'layout_entities': {'path': str(layout), 'sha256': digest(layout)}, 'block_definitions': {'path': str(blockfile), 'sha256': digest(blockfile)}}))
    frames = put(tmp_path / 'frames.json', dict(schema_version=batch.FRAMES_SCHEMA,
        frames=[{'frame_id': 'A', 'bounds': [0, 0, 100, 100]}, {'frame_id': 'B', 'bounds': [200, 0, 300, 100]}]))
    families = [dict(family_id='P', kind='layer', layer_pattern='^PIPE$', system='W', decision='include', evidence_ids=['E'], basis='synthetic')]
    mapping = put(tmp_path / 'mapping.json', dict(schema_version=batch.MAPPING_SCHEMA, mapping_id='SYN', mapping_version='1',
        expected_source_sha256=digest(source), recognized_by='synthetic recognition', recognized_at='2026-10-15T09:00:00+08:00',
        source_id='legend', network_id_prefix='W',
        evidence=[dict(evidence_id='E', path=str(layout), sha256=digest(layout), kind='cad_property', locator='Model', observation='synthetic')],
        unit={'raw_unit': 'Millimeters', 'output_unit': 'm', 'factor_to_output': .001, 'source_id': 'E'},
        families=families + list(extra_families), use_conventional_terminal_type_candidates=False, use_conventional_valve_type_candidates=False))
    before = {p: p.read_bytes() for p in (source, exchange, layout, blockfile, manifest, mapping, frames)}
    output = tmp_path / 'output'
    run = subprocess.run([sys.executable, '-B', str(ROOT / 'skills/qto-pipe-networks/scripts/build_pipe_inference_inputs_batch.py'),
                          '--cad-manifest', str(manifest), '--frames', str(frames), '--mapping', str(mapping), '--output-dir', str(output)],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert run.returncode == 0, run.stdout + run.stderr
    assert all(p.read_bytes() == old for p, old in before.items())
    summary = json.loads((output / '批量转换汇总.json').read_text(encoding='utf-8'))
    detail = [json.loads(line) for line in (output / '待办明细.jsonl').read_text(encoding='utf-8').splitlines()]
    unlocated = [json.loads(line) for line in Path(summary['drawing_level_unlocated_objects']['pending_detail']).read_text(encoding='utf-8').splitlines()]
    return summary, detail, unlocated, handles


def test_unlocated_objects_follow_the_same_family_rule_as_framed_objects(tmp_path):
    def build(doc, m):
        return dict(located=m.add_lwpolyline([(20, 50), (60, 50)], dxfattribs={'layer': 'DECOR'}).dxf.handle,
                    excluded=m.add_spline([(0, 0), (10, 30), (40, 0)], dxfattribs={'layer': 'DECOR'}).dxf.handle,
                    member=m.add_spline([(0, 0), (10, 30), (40, 0)], dxfattribs={'layer': 'PIPE'}).dxf.handle,
                    unknown=m.add_spline([(0, 0), (10, 30), (40, 0)], dxfattribs={'layer': 'MYST'}).dxf.handle)
    exclude = dict(family_id='X-DECOR', kind='layer', layer_pattern='^DECOR$', decision='exclude', evidence_ids=['E'], basis='decoration, not pipe')
    summary, detail, unlocated, h = run_batch(tmp_path, build, [exclude])
    assert all(r['object_id'] not in {'CAD-' + h['located'], 'CAD-' + h['excluded']} for r in detail)
    reasons = {r['object_id']: r['reason'] for r in unlocated}
    assert 'CAD-' + h['excluded'] not in reasons
    # PIPE is an include family whose default entity types cover SPLINE: a member that cannot be placed stays pending
    assert reasons == {'CAD-' + h['member']: 'unlocated_family_member', 'CAD-' + h['unknown']: 'unlocated_object'}
    info = summary['drawing_level_unlocated_objects']
    assert info['excluded_by_family'] == {'X-DECOR': 1} and info['count'] == 2 and summary['counts']['unlocated_excluded'] == 1


def test_only_excluded_unlocated_objects_do_not_hold_the_batch_for_review(tmp_path):
    def build(doc, m):
        return dict(excluded=m.add_spline([(0, 0), (10, 30), (40, 0)], dxfattribs={'layer': 'DECOR'}).dxf.handle)
    exclude = dict(family_id='X-DECOR', kind='layer', layer_pattern='^DECOR$', decision='exclude', evidence_ids=['E'], basis='decoration, not pipe')
    with_exclusion, _, unlocated, _ = run_batch(tmp_path / 'a', build, [exclude], pipes=False)
    without, _, unlocated_plain, _ = run_batch(tmp_path / 'b', build, pipes=False)
    assert {f['status'] for f in with_exclusion['frames']} == {f['status'] for f in without['frames']} == {'NO_PIPES'}
    assert not unlocated and with_exclusion['drawing_level_unlocated_objects']['excluded_count'] == 1
    assert with_exclusion['status'] == 'NO_PIPES'
    assert len(unlocated_plain) == 1 and without['status'] == 'REVIEW_REQUIRED'   # control: an unexplained object still holds it


def scan_unlocated(tmp_path, monkeypatch, rows, decision):
    import re
    family = dict(family_id='BLK', kind='block', decision=decision, system='W', priority=0, frame_re=None,
                  layer_re=re.compile('^MISC$'), block_re=re.compile('^BODY$'))
    (tmp_path / 'layout.ndjson').write_text('', encoding='utf-8'); (tmp_path / 'blocks.ndjson').write_text('', encoding='utf-8')
    monkeypatch.setattr(batch.adapter, 'expanded_manifest_rows', lambda *a, **k: iter(rows))
    frames = [{'frame_id': 'A', 'bounds': [0, 0, 100, 100]}]
    return batch.scan_frames(tmp_path / 'layout.ndjson', tmp_path / 'blocks.ndjson', {},
                             {'layout': 'Model', 'families': [family], 'block_expansion': {'max_depth': 5}}, frames, [], tmp_path)


@pytest.mark.parametrize('decision,pending_ids', [('exclude', []), ('include', ['ROOT'])])
def test_unlocated_instance_owned_by_a_block_family_carries_its_children(tmp_path, monkeypatch, decision, pending_ids):
    base = dict(row_type='entity', layout='Model', bbox=None)
    parent = dict(base, entity_type='INSERT', record_id='ROOT', layer='MISC', block_name='BODY')       # no bbox, no insertion point
    child = dict(base, entity_type='SPLINE', record_id='CADINST-ROOT/BODY/C1', layer='MISC', instance_path='ROOT/BODY')
    scan = scan_unlocated(tmp_path, monkeypatch, [parent, child], decision)
    detail, groups, suppressed = batch.group_pending({'pending': scan['drawing_level_pending'], 'owned_insert_keys': scan['drawing_level_owned']},
                                                     scan['insert_index'])
    # the child never appears on its own: an excluded owner is only counted, an included owner is the one pending item
    assert [d['object_id'] for d in detail] == pending_ids and suppressed == 1
    assert scan['drawing_level_excluded'] == ({'BLK': 1} if decision == 'exclude' else {})


def test_two_level_nested_child_follows_nearest_world_insertion_point(tmp_path):
    def build(doc, m):
        inner = doc.blocks.new('INN')
        spline = inner.add_spline([(0, 0), (5, 15), (20, 0)])
        line = inner.add_line((0, 0), (5, 0), dxfattribs={'layer': 'MYST'})
        doc.blocks.new('OUT').add_blockref('INN', (-200, 0))           # inner insertion lands at world (50, 50): frame A
        m.add_blockref('OUT', (250, 50), dxfattribs={'layer': 'MISC'})  # outer insertion at world (250, 50): frame B
        return dict(spline=spline.dxf.handle, line=line.dxf.handle)
    summary, detail, unlocated, h = run_batch(tmp_path, build)
    spline = [r for r in detail if r['object_id'].endswith('/CAD-' + h['spline'])]
    line = [r for r in detail if r['object_id'].endswith('/CAD-' + h['line'])]
    assert len(spline) == 1 and spline[0]['frame_id'] == 'A' and spline[0]['location_basis'] == 'ancestor_insertion_point'
    assert len(line) == 1 and line[0]['frame_id'] == 'A' and line[0]['location_basis'] == 'own_geometry'
    assert not unlocated
