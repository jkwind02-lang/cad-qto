"""Anonymous T26 source/geometry/disposition counterexamples through real pipe CLIs."""
import copy
import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_pipe_source_scope_contracts import bundle

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/qto-pipe-networks/scripts'
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


def put(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


def run(tmp_path, data, engine='pipe_overlap_review.py', name='case', target=None):
    src = put(tmp_path / (name + '.json'), data)
    out = target or tmp_path / (name + '.out.json')
    done = subprocess.run([sys.executable, '-B', str(SCRIPTS / engine), '--input', str(src), '--output', str(out)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', env=ENV)
    return done, json.loads(out.read_text(encoding='utf-8')) if done.returncode == 0 and out.is_file() and out != src else None


def segment(key, a, b, **kwargs):
    return dict(segment_id=key, system='W', space_id='floor-plan-A', floor='F1',
                start=a, end=b, source_ids=['EV-' + key], **kwargs)


def request(*segments):
    return dict(schema_version='qto-pipe-overlap-request/1.0', unit='m', segments=list(segments),
                overlap_review=dict(enabled=True, elevation_mode='source_3d',
                    unit_source_ids=['ANONYMOUS-COORDINATES-M'], unit_basis='本匿名夹具坐标明确为米',
                    parameters=dict(angle_tolerance_deg=1., distance_tolerance=.005, min_overlap=.01,
                        elevation_tolerance=.001, source_ids=['ANONYMOUS-REVIEW-THRESHOLDS'],
                        basis='仅本匿名夹具显式给定的候选检索和标高区分阈值，不作为插件默认值')))


def succeeded(tmp_path, data, **kwargs):
    done, result = run(tmp_path, data, **kwargs)
    assert done.returncode == 0, done.stdout + done.stderr
    return result


def test_exact_and_partial_reversed_candidates_preserve_originals(tmp_path):
    data = request(segment('A', [0, 0], [3, 0]), segment('B', [3, 0], [0, 0]),
                   segment('C', [2, 0], [4, 0]), segment('D', [0, 1], [3, 1]))
    result = succeeded(tmp_path, data)
    assert result['counts']['candidate_pairs'] == 3
    pairs = {tuple(r['pair']): r for r in result['pairs']}
    assert pairs[('route:A:0', 'route:B:0')]['overlap_length'] == pytest.approx(3)
    assert pairs[('route:A:0', 'route:C:0')]['overlap_length'] == pytest.approx(1)
    assert all(r['resolution']['action'] == 'pending' and not r['quantity_included'] for r in pairs.values())
    assert result['deduplication_scenario']['deductions'] == []
    assert 'plan_only_no_elevation_proof' in result['pairs'][0]['uncertainties']


@pytest.mark.parametrize('field,value,reason', [('system', 'OTHER', 'different_system'),
    ('space_id', 'another-space', 'different_space_id'), ('floor', 'F2', 'different_floor'),
    ('material', 'copper', 'different_material'), ('dn', 'DN25', 'different_dn')])
def test_scope_and_specification_are_separate(tmp_path, field, value, reason):
    a, b = segment('A', [0, 0, 1], [3, 0, 1]), segment('B', [0, 0, 1], [3, 0, 1])
    a.update(material='steel', dn='DN80')
    b.update(material='steel', dn='DN80')
    b[field] = value
    result = succeeded(tmp_path, request(a, b))
    assert result['pairs'][0]['category'] == 'separated'
    assert reason in result['pairs'][0]['separation_reasons']
    assert not result['deduplication_scenario']['deductions']


def test_different_z_parallel_objects_and_vertical_3d(tmp_path):
    a = segment('A', [0, 0, 1], [3, 0, 1], physical_id='one')
    b = segment('B', [0, 0, 2], [3, 0, 2], physical_id='two')
    c, d = segment('C', [10, 0, 0], [10, 0, 3]), segment('D', [10, 0, 1], [10, 0, 4])
    result = succeeded(tmp_path, request(a, b, c, d))
    pairs = {tuple(r['pair']): r for r in result['pairs']}
    assert len(pairs) == 2
    assert 'different_elevation_or_3d_direction' in pairs[('route:A:0', 'route:B:0')]['separation_reasons']
    assert 'distinct_physical_objects' in pairs[('route:A:0', 'route:B:0')]['separation_reasons']
    assert pairs[('route:C:0', 'route:D:0')]['dimension'] == 3
    assert pairs[('route:C:0', 'route:D:0')]['overlap_length'] == pytest.approx(2)


def disposition(pair, action='drop_overlap', drop=None):
    return dict(pair=pair, action=action, drop_edge_id=drop or pair[1], basis='reviewed anonymous route expression',
                source_ids=['EV-REVIEW'], same_object_evidence=dict(basis='same pipe verified in source views', source_ids=['EV-SAME']))


def test_near_parallel_candidate_does_not_prove_same_object(tmp_path):
    data = request(segment('A', [0, 0], [3, 0]), segment('B', [0, .004], [3, .004]))
    result = succeeded(tmp_path, data)
    assert result['pairs'][0]['category'] == 'candidate'
    data['overlap_review'].update(resolutions=[disposition(['route:A:0', 'route:B:0'])])
    del data['overlap_review']['resolutions'][0]['same_object_evidence']
    done, _ = run(tmp_path, data, name='invalid')
    assert done.returncode == 2 and '同物证据' in done.stderr


def test_explicit_keep_and_pending_never_deduct(tmp_path):
    data = request(segment('A', [0, 0], [3, 0]), segment('B', [0, 0], [3, 0]), segment('C', [0, 0], [3, 0]))
    data['overlap_review'].update(resolutions=[disposition(['route:A:0', 'route:B:0'], 'keep_both'),
                                             disposition(['route:B:0', 'route:C:0'], 'pending')])
    result = succeeded(tmp_path, data)
    assert result['counts']['pending_pairs'] == 2
    assert result['deduplication_scenario']['deductions'] == []


def test_drop_is_separate_interval_union_scenario_and_cycles_blocked(tmp_path):
    data = request(segment('A', [0, 0], [3, 0]), segment('B', [1, 0], [4, 0]), segment('C', [2, 0], [5, 0]))
    data['overlap_review'].update(resolutions=[disposition(['route:A:0', 'route:B:0'], drop='route:B:0'),
                                             disposition(['route:A:0', 'route:C:0'], drop='route:C:0')])
    result = succeeded(tmp_path, data)
    assert sum(r['length'] for r in result['deduplication_scenario']['deductions']) == pytest.approx(3)
    assert result['deduplication_scenario']['applied_to_main_quantities'] is False
    data['overlap_review']['resolutions'].append(disposition(['route:B:0', 'route:C:0'], drop='route:B:0'))
    result = succeeded(tmp_path, data, name='transitive')
    assert result['deduplication_scenario']['status'] == 'PROPOSED_ONLY'
    assert sum(r['length'] for r in result['deduplication_scenario']['deductions']) == pytest.approx(4)
    data = request(*(segment(key, [0, 0], [3, 0]) for key in 'ABC'))
    data['overlap_review'].update(resolutions=[disposition(['route:A:0', 'route:B:0'], drop='route:A:0'),
        disposition(['route:B:0', 'route:C:0'], drop='route:B:0'), disposition(['route:A:0', 'route:C:0'], drop='route:C:0')])
    result = succeeded(tmp_path, data, name='cycle')
    assert result['deduplication_scenario']['status'] == 'DISPOSITION_CONFLICT'
    assert result['deduplication_scenario']['deductions'] == []


def import_source(tmp_path):
    src = tmp_path / 'evidence.csv'
    src.write_text('x1,y1,x2,y2,sys,space,handle,level\n0,0,3000,0,W,F1,H1,confirmed\n1000,0,4000,0,W,F1,gap,scenario\n,,,,W,F1,vertical,pending\n', encoding='utf-8')
    spec = dict(source_path=str(src), source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(), source_id='EV-TABLE', format='csv',
                unit='mm', unit_source_ids=['EV-UNIT'], unit_basis='source coordinate declaration in millimetres',
                columns=dict(start=['x1', 'y1'], end=['x2', 'y2'], system='sys', space_id='space', handle='handle', quantity_level='level'),
                rows=[dict(row_index=0, action='include', edge_id='import:A', edge_kind='drawn', source_ids=['EV-A'], basis='identified real line'),
                      dict(row_index=1, action='include', edge_id='import:B', edge_kind='label_gap', source_ids=['EV-B'], basis='identified label gap'),
                      dict(row_index=2, action='unresolved', source_ids=['EV-C'], basis='no end elevations')])
    data = request()
    data['overlap_review'].update(imported_edges=[spec])
    return src, spec, data


def test_hashed_source_import_includes_label_gap_and_full_instance_row(tmp_path):
    src, spec, data = import_source(tmp_path)
    before = src.read_bytes()
    result = succeeded(tmp_path, data)
    assert len(result['pairs']) == 1 and result['pairs'][0]['overlap_length'] == pytest.approx(2)
    assert {r['edge_kind'] for r in result['inventory']} == {'drawn', 'label_gap'}
    assert result['inventory'][1]['source']['row_index'] == 1
    assert result['inventory'][1]['source']['source_row']['level'] == 'scenario'
    assert result['inventory'][1]['source']['factor_to_output'] == .001
    assert len(result['unreviewed']) == 1 and not result['review_complete']
    assert src.read_bytes() == before


@pytest.mark.parametrize('change,match', [('hash', 'SHA256'), ('unit', '单位'), ('rows', '逐源行'), ('index', 'row_index')])
def test_import_cannot_replace_source_units_or_omit_rows(tmp_path, change, match):
    _, spec, data = import_source(tmp_path)
    if change == 'hash': spec['source_sha256'] = '0' * 64
    if change == 'unit': spec['unit'] = None
    if change == 'rows': spec['rows'].pop()
    if change == 'index': spec['rows'][1]['row_index'] = 0
    done, _ = run(tmp_path, data)
    assert done.returncode == 2 and match in done.stderr


def test_source_hardlink_and_output_alias_protection(tmp_path):
    src, _, data = import_source(tmp_path)
    before = src.read_bytes()
    out = tmp_path / 'hardlink.json'
    os.link(src, out)
    done, _ = run(tmp_path, data, target=out)
    assert done.returncode == 2 and '别名' in done.stderr
    assert src.read_bytes() == before
    done, _ = run(tmp_path, data, name='same', target=tmp_path / 'same.json')
    assert done.returncode == 2 and '别名' in done.stderr


@pytest.mark.parametrize('key,value', [('angle_tolerance_deg', 90), ('distance_tolerance', -1), ('min_overlap', float('nan')),
                                      ('elevation_tolerance', True)])
def test_invalid_parameters_are_not_accepted(tmp_path, key, value):
    data = request(segment('A', [0, 0], [1, 0]))
    data['overlap_review']['parameters'][key] = value
    done, _ = run(tmp_path, data)
    assert done.returncode == 2


def test_unknown_network_unit_is_not_assumed_metres(tmp_path):
    data = request(segment('A', [0, 0], [3, 0]), segment('B', [0, 0], [3, 0]))
    data['unit'] = 'unknown'
    result = succeeded(tmp_path, data)
    assert result['status'] == 'UNIT_UNRESOLVED' and not result['review_complete']
    assert result['inventory'] is None and result['pairs'] is None
    assert all(value is None for value in result['counts'].values())


def network():
    data = request(segment('A', [0, 0], [3, 0]), segment('B', [1, 0], [4, 0]))
    data.update(schema_version='qto-pipe-network-inference-input/1.0', network_id='ANONYMOUS-T26',
                parameters=dict(snap_tolerance=.001, gap_max=.2))
    for s in data['segments']:
        s['dn_seeds'] = [dict(dn='DN80', evidence_level='confirmed', source_id='EV-DN')]
    return data


def test_real_inference_entry_emits_review_and_preserves_dn_and_length(tmp_path):
    result = succeeded(tmp_path, network(), engine='infer_pipe_network.py')
    assert result['metrics']['raw_length'] == pytest.approx(6)
    assert result['metrics']['length_conservation_delta'] == pytest.approx(0)
    assert result['overlap_review']['counts']['candidate_pairs'] == 1
    assert all(a['dn'] == 'DN80' for a in result['independent_dn_assignments'].values())
    assert next(tmp_path.glob('*_共线重合.csv')).is_file()
    assert '管网共线重合审查' in next(tmp_path.glob('*复核摘要.md')).read_text(encoding='utf-8')


def test_actual_topology_bridge_is_in_review_inventory_without_adding_main_length(tmp_path):
    data = network()
    data['segments'][1].update(start=[3.1, 0], end=[4, 0])
    result = succeeded(tmp_path, data, engine='infer_pipe_network.py')
    bridges = [r for r in result['overlap_review']['inventory'] if r['edge_kind'] == 'topology_bridge']
    assert len(bridges) == 1
    assert bridges[0]['source']['bridge']['bridge_id'] == result['topology_bridges'][0]['bridge_id']
    assert bridges[0]['quantity_in_main_network'] is False
    assert len(bridges[0]['start']) == len(bridges[0]['end']) == 2
    assert bridges[0]['source']['source_dimensions'] == [2, 2]
    assert result['metrics']['raw_length'] == pytest.approx(3.9)


def test_duplicate_polyline_part_is_not_hidden_by_parent_identity(tmp_path):
    data = request(segment('A', [0, 0, 0], [0, 0, 0]))
    data['segments'][0]['geometry'] = dict(schema='qto-cad-geometry/1.1', coordinate_system='WCS', parts=[
        dict(kind='line', start=[0, 0, 0], end=[3, 0, 0]), dict(kind='line', start=[3, 0, 0], end=[0, 0, 0])])
    result = succeeded(tmp_path, data)
    assert len(result['pairs']) == 1 and result['pairs'][0]['pair'] == ['route:A:0', 'route:A:1']


def test_elevation_tolerance_and_identical_sloped_3d_routes(tmp_path):
    a, b = segment('A', [0, 0, 1], [3, 0, 1]), segment('B', [0, 0, 1.004], [3, 0, 1.004])
    c, d = segment('C', [10, 0, 0], [13, 0, 3]), segment('D', [10, 0, 0], [13, 0, 3])
    result = succeeded(tmp_path, request(a, b, c, d))
    pairs = {tuple(r['pair']): r for r in result['pairs']}
    assert pairs[('route:A:0', 'route:B:0')]['category'] == 'separated'
    assert pairs[('route:C:0', 'route:D:0')]['dimension'] == 3
    assert pairs[('route:C:0', 'route:D:0')]['overlap_length'] == pytest.approx(18 ** .5)


def test_sidecar_hardlink_is_checked_before_any_output_is_written(tmp_path):
    src, _, data = import_source(tmp_path)
    before = src.read_bytes()
    target = tmp_path / 'sidecar.out.json'
    os.link(src, target.with_suffix('.csv'))
    done, _ = run(tmp_path, data, target=target)
    assert done.returncode == 2 and '别名冲突' in done.stderr
    assert src.read_bytes() == before and not target.exists()


def test_recognition_profile_preserves_import_source_location_and_review_config(bundle):
    folder, manifest, profile, ledger, *_ = bundle
    src, spec, extra = import_source(folder)
    spec['source_path'] = src.name
    profile['overlap_review'] = extra['overlap_review']
    profile_path, manifest_path = put(folder / 'profile.json', profile), put(folder / 'manifest.json', manifest)
    output = folder / 'network.json'
    done = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'build_pipe_inference_input.py'),
        '--cad-manifest', str(manifest_path), '--profile', str(profile_path), '--adjudication-ledger', str(ledger),
        '--output-dir', str(folder / 'generated'), '--inference-output', str(output)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', env=ENV)
    assert done.returncode == 0, done.stdout + done.stderr
    generated = json.loads((folder / 'generated/管网推算输入.json').read_text(encoding='utf-8'))
    assert generated['overlap_review']['imported_edges'][0]['source_path'] == str(src.resolve())
    result = json.loads(output.read_text(encoding='utf-8'))
    assert {'import:A', 'import:B'} <= {r['edge_id'] for r in result['overlap_review']['inventory']}
    assert result['metrics']['raw_length'] == pytest.approx(10)


@pytest.mark.parametrize('configured', [False, True])
def test_no_explicit_enable_never_uses_universal_project_thresholds(tmp_path, configured):
    data = request(segment('A', [0, 0], [2, 0]), segment('B', [0, 0], [2, 0]))
    if configured:
        data['overlap_review'].pop('enabled')
    else:
        data.pop('overlap_review')
    result = succeeded(tmp_path, data)
    assert result['status'] == 'NOT_ENABLED'
    assert result['pairs'] is None and all(v is None for v in result['counts'].values())
    assert result['parameters']['engineering_threshold_defaults'] is None
    assert '未执行' in (tmp_path / 'case.out.csv').read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('field', ['angle_tolerance_deg', 'distance_tolerance', 'min_overlap', 'elevation_tolerance'])
def test_enabled_review_requires_every_applicable_threshold(tmp_path, field):
    data = request(segment('A', [0, 0, 1], [2, 0, 1]), segment('B', [0, 0, 1], [2, 0, 1]))
    data['overlap_review']['parameters'].pop(field)
    result = succeeded(tmp_path, data)
    assert result['status'] == 'PARAMETERS_REQUIRED'
    assert result['pairs'] is None and all(v is None for v in result['counts'].values())
    assert 'parameters.' + field in result['missing_parameters']


@pytest.mark.parametrize('field', ['unit_source_ids', 'unit_basis', 'elevation_mode', 'parameters.source_ids', 'parameters.basis'])
def test_enabled_review_requires_unit_threshold_sources_and_scope(tmp_path, field):
    data = request(segment('A', [0, 0], [2, 0]))
    if field.startswith('parameters.'):
        data['overlap_review']['parameters'].pop(field.split('.')[1])
    else:
        data['overlap_review'].pop(field)
    result = succeeded(tmp_path, data)
    assert result['status'] == 'PARAMETERS_REQUIRED' and field in result['missing_parameters']
    assert result['pairs'] is None


def test_two_dimensional_geometry_parts_keep_source_dimension_with_explicit_parameters(tmp_path):
    data = request(segment('A', [0, 0], [2, 0]), segment('B', [0, 0], [2, 0]))
    for row in data['segments']:
        row['geometry'] = dict(schema='qto-cad-geometry/1.1', coordinate_system='WCS',
                               parts=[dict(kind='line', start=row.pop('start'), end=row.pop('end'))])
    result = succeeded(tmp_path, data)
    assert result['pairs'][0]['dimension'] == 2
    assert 'plan_only_no_elevation_proof' in result['pairs'][0]['uncertainties']
    assert all(m['source']['source_point_dimensions'] == [2, 2] for m in result['pairs'][0]['members'])
    assert all(len(m['start']) == len(m['end']) == 2 for m in result['pairs'][0]['members'])


def test_explicit_plan_only_has_no_invented_z_threshold_and_rejects_3d_sources(tmp_path):
    data = request(segment('A', [0, 0], [2, 0]), segment('B', [0, 0], [2, 0]))
    data['overlap_review']['elevation_mode'] = 'plan_only'
    data['overlap_review']['parameters'].pop('elevation_tolerance')
    result = succeeded(tmp_path, data)
    assert len(result['pairs']) == 1 and result['pairs'][0]['dimension'] == 2
    assert result['parameters']['elevation_tolerance'] is None
    for row in data['segments']:
        row['start'].append(1)
        row['end'].append(1)
    result = succeeded(tmp_path, data, name='3d_needs_z_basis')
    assert result['status'] == 'PARAMETERS_REQUIRED' and result['pairs'] is None


def test_inference_without_enable_preserves_quantity_but_reports_no_review_result(tmp_path):
    data = network()
    data.pop('overlap_review')
    result = succeeded(tmp_path, data, engine='infer_pipe_network.py')
    assert result['metrics']['raw_length'] == pytest.approx(6)
    assert result['overlap_review']['status'] == 'NOT_ENABLED'
    assert result['overlap_review']['pairs'] is None
    assert result['overlap_review']['counts']['candidate_pairs'] is None


def run_builder_with_profile(bundle, profile, output_dir, inference_output):
    folder, manifest, _, ledger, *_ = bundle
    profile_path, manifest_path = put(folder / 'profile.json', profile), put(folder / 'manifest.json', manifest)
    return subprocess.run([sys.executable, '-B', str(SCRIPTS / 'build_pipe_inference_input.py'),
        '--cad-manifest', str(manifest_path), '--profile', str(profile_path), '--adjudication-ledger', str(ledger),
        '--output-dir', str(output_dir), '--inference-output', str(inference_output)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', env=ENV)


@pytest.mark.parametrize('fault', ['sidecar_alias', 'hash', 'columns', 'unit', 'rows', 'main_hardlink', 'mutual_alias', 'summary_hardlink'])
def test_builder_rejects_decisive_invalid_import_or_output_before_any_generated_write(bundle, fault):
    folder, _, profile, _, original, *_ = bundle
    src, spec, extra = import_source(folder)
    profile['overlap_review'] = extra['overlap_review']
    generated, output = folder / 'generated', folder / 'network.json'
    if fault == 'sidecar_alias':
        source = folder / 'network_共线重合.csv'
        source.write_bytes(src.read_bytes())
        src = source
        spec['source_path'] = str(source)
    elif fault == 'hash': spec['source_sha256'] = '0' * 64
    elif fault == 'columns': spec['columns']['start'][0] = 'missing_column'
    elif fault == 'unit': spec['unit'] = None
    elif fault == 'rows': spec['rows'].pop()
    elif fault == 'main_hardlink': os.link(original, output)
    elif fault == 'mutual_alias': output = generated / '管网推算输入.json'
    elif fault == 'summary_hardlink': os.link(src, folder / 'network_1234567890_复核摘要.md')
    before = {p: p.read_bytes() for p in (src, original)}
    done = run_builder_with_profile(bundle, profile, generated, output)
    assert done.returncode == 2, done.stdout + done.stderr
    assert not generated.exists(), list(generated.rglob('*')) if generated.exists() else None
    if fault != 'main_hardlink':
        assert not output.exists()
    assert all(path.read_bytes() == value for path, value in before.items())


def test_builder_rejects_lexical_junction_output_before_resolve_and_write(bundle):
    folder, _, profile, *_ = bundle
    target, alias = folder / 'real_output', folder / 'junction_output'
    target.mkdir()
    if os.name == 'nt':
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)], stdin=subprocess.DEVNULL,
                              capture_output=True, env=ENV)
        assert done.returncode == 0, done.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)
    done = run_builder_with_profile(bundle, profile, alias, folder / 'network.json')
    assert done.returncode == 2 and ('reparse' in done.stderr or '符号链接' in done.stderr)
    assert list(target.iterdir()) == []
