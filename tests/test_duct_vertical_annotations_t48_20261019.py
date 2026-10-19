"""Anonymous source-to-quantity regressions; no project drawing or answer in Git."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/qto-duct-air-systems/scripts'
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
import bind_duct_vertical_annotations as binding
import infer_duct_fittings_vertical as duct


def run(*args):
    command = [sys.executable, '-B', *map(str, args)]
    process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=90)
    if os.environ.get('T48_CLI_LOG_PATH'):
        with Path(os.environ['T48_CLI_LOG_PATH']).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'command': command, 'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}, ensure_ascii=False)+'\n')
    return process


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'path': str(path), 'sha256': binding.sha(path)}


def case(tmp_path, text='风管下接高度2.3m', native=False, nested=False, extra=None, text_position=(3000, 1000), curved=False, arrow=(500,0)):
    doc = ezdxf.new('R2013'); doc.units = 4
    for layer in ('DIM_LEAD', 'DUCT'): doc.layers.new(layer)
    space = doc.blocks.new('CONTAINER') if nested else doc.modelspace()
    line = space.add_line((0, 0), (1000, 0), dxfattribs={'layer': 'DUCT'})
    if curved:
        space.add_arc((1500, 0), 1000, 90, 180, dxfattribs={'layer': 'DIM_LEAD'})
        space.add_line((1500, 1000), (3000, 1000), dxfattribs={'layer': 'DIM_LEAD'})
    elif native:
        space.add_leader([arrow, (1500, 1000), (3000, 1000)], dxfattribs={'layer': 'DIM_LEAD'})
    else:
        space.add_lwpolyline([arrow, (1500, 1000), (3000, 1000)], dxfattribs={'layer': 'DIM_LEAD'})
    space.add_text(text, dxfattribs={'layer': 'DIM_LEAD', 'insert': text_position, 'height': 100})
    if extra: extra(space)
    if nested:
        outer = doc.modelspace().add_blockref('CONTAINER', (10000, 20000), dxfattribs={'rotation': 90, 'xscale': -2, 'yscale': 3})
        path = f'CAD-{outer.dxf.handle}/CONTAINER/CAD-{line.dxf.handle}'
        start, end = [10000, 20000], [10000, 18000]
    else:
        path = 'CAD-' + line.dxf.handle; start, end = [0, 0], [1000, 0]
    dxf = tmp_path / 'source.dxf'; doc.saveas(dxf)
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp_path / 'neutral')
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    manifest = tmp_path / 'neutral/CAD中性取证清单.json'
    data = {'schema_version': duct.INPUT_SCHEMA, 'unit': 'mm', 'output_unit': 'm', 'package_id': 'SYN',
            'parameters': {'parameter_basis': '合成端点直接相连'}, 'floors': [{'floor_id': 'F', 'level': 0}],
            'segments': [{'segment_id': 'S', 'floor_id': 'F', 'system': 'SUPPLY', 'start': start, 'end': end,
                          'width': 1000, 'height': 500, 'size_evidence_level': '确认', 'source_ids': [path]}]}
    config = {'schema_version': binding.SCHEMA, 'neutral_manifest': {'path': str(manifest), 'sha256': binding.sha(manifest)},
              'duct_input': save(tmp_path / 'duct.json', data), 'layout': 'Model', 'leader_layer_pattern': '^DIM_LEAD$',
              'expand_blocks': ['CONTAINER'] if nested else [],
              'parameters': {'basis': '合成精确连接，距离均为图纸单位；不使用最近者', 'snap_tolerance': .001,
                             'text_tolerance': 1, 'target_tolerance': 1, 'endpoint_tolerance': .001},
              'scopes': [{'scope_id': 'F-PLAN', 'floor_id': 'F', 'bounds': [-10000, -10000, 50000, 50000], 'basis': '合成单层平面'}],
              'segment_bindings': [{'segment_id': 'S', 'source_id': path, 'part_index': 0}]}
    return data, config


def infer(data, config):
    return duct.infer({**data, 'vertical_annotation_input': config})


@pytest.mark.parametrize('native,nested', [(False, False), (True, False), (False, True), (True, True)])
def test_real_neutral_cli_chain_quantity_and_source(tmp_path, native, nested):
    data, config = case(tmp_path, native=native, nested=nested)
    cfg = tmp_path / 'binding.json'; save(cfg, config)
    augmented = tmp_path / 'augmented.json'; output = tmp_path / 'result.json'
    p = run(SCRIPTS / 'bind_duct_vertical_annotations.py', '--input', cfg, '--output', augmented)
    assert p.returncode == 0, p.stdout + p.stderr
    p = run(SCRIPTS / 'infer_duct_fittings_vertical.py', '--input', augmented, '--output', output)
    assert p.returncode == 0, p.stdout + p.stderr
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['metrics']['vertical_length_rule'] == pytest.approx(2.3)
    assert result['theoretical_surface_summary']['SUPPLY']['规则推算']['1000x500'] == pytest.approx(6.9)
    item = result['vertical_items'][0]
    assert item['kind'] == 'annotation_explicit_height' and item['length'] == 2.3
    assert not result['business_adoption_allowed'] and not item['human_reviewed']
    assert item['text'] == '风管下接高度2.3m'
    assert item['source_evidence']['coordinate_space'] == 'drawing_WCS'
    assert len(item['source_evidence']['source']['sha256']) == 64
    assert item['binding_evidence']['target']['instance_path'] == config['segment_bindings'][0]['source_id']
    if nested:
        assert item['pointer'] == pytest.approx([10000, 19000, 0])
        assert item['binding_evidence']['instance_transform'] != list(binding.geometry.IDENTITY)


@pytest.mark.parametrize('text,expected', [('风管上接高度2300mm', 2.3), ('风管下接高度 230 cm', 2.3), ('风管下接高度2.3米', 2.3)])
def test_explicit_height_units_are_independent_of_drawing_units(tmp_path, text, expected):
    data, cfg = case(tmp_path, text=text)
    assert infer(data, cfg)['metrics']['vertical_length_rule'] == pytest.approx(expected)


@pytest.mark.parametrize('text', ['风管下接高度2.3', '风管下接高度约2.3m', '风管下接高度2.3m 标高+3.2m',
                                 '风管下接高度2.3m 示意', '风管下接高度0m', '风管下接高度2.3m~3m', '风管下接高度2.3m 下接高度1m'])
def test_incomplete_and_conflicting_semantics_stay_null(tmp_path, text):
    data, cfg = case(tmp_path, text=text)
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == 0
    note = out['vertical_annotation_binding']['annotations'][0]
    assert note['length'] is None and note['status'] == 'unresolved'
    assert out['metrics']['totals_complete'] is False


def test_height_does_not_become_absolute_elevation(tmp_path):
    data, cfg = case(tmp_path)
    data['segments'][0]['elevation'] = {'value': 19, 'reference': 'center', 'source_id': 'EL', 'evidence_level': '确认'}
    cfg['duct_input'] = save(tmp_path / 'duct.json', data)
    out = infer(data, cfg)
    assert out['straight_segments'][0]['center_elevation'] == 19
    assert out['vertical_items'][0]['length'] == 2.3


def test_nearby_or_interior_line_is_not_a_leader(tmp_path):
    data, cfg = case(tmp_path)
    cfg['parameters']['text_tolerance'] = 0
    # Text is moved within the shoulder, not attached to its endpoint.
    path = Path(cfg['neutral_manifest']['path']); manifest = json.loads(path.read_text(encoding='utf-8'))
    stream = Path(manifest['artifacts']['layout_entities']['path'])
    rows = [json.loads(x) for x in stream.read_text(encoding='utf-8').splitlines()]
    for row in rows:
        if row.get('text'): row['x'] = 2500
    stream.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf-8')
    manifest['artifacts']['layout_entities']['sha256'] = binding.sha(stream)
    cfg['neutral_manifest'] = save(path, manifest)
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == 0
    assert out['vertical_annotation_binding']['annotations'][0].get('pointer') is None


def test_multi_target_and_cross_system_not_resolved_by_nearest(tmp_path):
    data, cfg = case(tmp_path, extra=lambda s: s.add_line((0, .5), (1000, .5), dxfattribs={'layer': 'DUCT'}))
    manifest = json.loads(Path(cfg['neutral_manifest']['path']).read_text(encoding='utf-8'))
    rows = [json.loads(x) for x in Path(manifest['artifacts']['layout_entities']['path']).read_text(encoding='utf-8').splitlines()]
    other = next(r for r in rows if r.get('layer') == 'DUCT' and r['start'][1] == .5)
    second = {**data['segments'][0], 'segment_id': 'OTHER', 'system': 'EXHAUST', 'start': [0, .5], 'end': [1000, .5]}
    data['segments'].append(second); cfg['duct_input'] = save(tmp_path / 'duct.json', data)
    cfg['segment_bindings'].append({'segment_id': 'OTHER', 'source_id': other['record_id'], 'part_index': 0})
    out = infer(data, cfg); note = out['vertical_annotation_binding']['annotations'][0]
    assert set(note['candidate_segment_ids']) == {'S', 'OTHER'}
    assert note['length'] is None


@pytest.mark.parametrize('field,value', [('floor_id', 'OTHER-FLOOR'), ('system', '未定')])
def test_unknown_system_and_cross_floor_block_quantity(tmp_path, field, value):
    data, cfg = case(tmp_path)
    data['floors'] = []
    data['segments'][0][field] = value; cfg['duct_input'] = save(tmp_path / 'duct.json', data)
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == 0
    assert out['vertical_annotation_binding']['annotations'][0]['height_fact']['length_m'] == 2.3


def test_unitless_keeps_explicit_height_and_raw_binding_candidates(tmp_path):
    data, cfg = case(tmp_path)
    p = Path(cfg['neutral_manifest']['path']); manifest = json.loads(p.read_text(encoding='utf-8'))
    manifest['drawing_unit']['raw_unit'] = 'Unitless'; cfg['neutral_manifest'] = save(p, manifest)
    out = infer(data, cfg); note = out['vertical_annotation_binding']['annotations'][0]
    assert note['height_fact']['length_m'] == 2.3 and note['candidate_segment_ids'] == ['S']
    assert note['length'] == 2.3 and note['status'] == 'bound'
    assert out['metrics']['vertical_length_rule'] == 2.3
    assert out['vertical_items'][0]['theoretical_surface_area'] is None
    assert out['theoretical_surface_summary'] == {}
    assert out['metrics']['surface_totals_complete'] is False
    assert out['metrics']['length_totals_complete'] is False
    assert out['metrics']['measured_length_rule'] is None
    assert out['metrics']['straight_length'] is None
    assert out['straight_segments'][0]['length'] is None
    assert out['conditional_on_input_unit']['metrics']['straight_length'] == 1
    assert out['length_summary']['SUPPLY']['规则推算']['1000x500'] == 2.3
    assert out['metrics']['totals_complete'] is False


def test_source_freshness_and_tampered_duct_input_are_rejected(tmp_path):
    data, cfg = case(tmp_path)
    bad = copy.deepcopy(data); bad['segments'][0]['start'] = [20, 0]
    with pytest.raises(ValueError, match='冻结来源不一致'): infer(bad, cfg)
    Path(cfg['duct_input']['path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='已改变'): infer(data, cfg)


def test_duplicate_identity_is_pending_and_existing_vertical_is_not_added_again(tmp_path):
    data, cfg = case(tmp_path)
    out = duct.infer(data)
    out['vertical_items'] = [{'vertical_id': 'OLD', 'segment_ids': ['S'], 'length': 2.3}]
    duct.append_annotation_verticals(duct.Network(data), {**data, 'vertical_annotation_input': cfg}, out)
    assert len(out['vertical_items']) == 1
    assert out['vertical_annotation_binding']['annotations'][0]['length'] is None


def test_default_input_and_metrics_are_unchanged(tmp_path):
    data, cfg = case(tmp_path)
    out = duct.infer(data)
    assert 'vertical_annotation_binding' not in out and out['metrics']['vertical_length_rule'] == 0


def test_cli_refuses_source_overwrite_and_retains_nonzero(tmp_path):
    data, cfg = case(tmp_path)
    config_path = tmp_path / 'binding.json'; save(config_path, cfg)
    before = binding.sha(cfg['duct_input']['path'])
    p = run(SCRIPTS / 'bind_duct_vertical_annotations.py', '--input', config_path, '--output', cfg['duct_input']['path'])
    assert p.returncode == 2 and '覆盖' in p.stderr
    assert binding.sha(cfg['duct_input']['path']) == before
    augmented = tmp_path / 'augmented.json'; save(augmented, {**data, 'vertical_annotation_input': cfg})
    p = run(SCRIPTS / 'infer_duct_fittings_vertical.py', '--input', augmented, '--output', cfg['duct_input']['path'])
    assert p.returncode == 2 and binding.sha(cfg['duct_input']['path']) == before


@pytest.mark.parametrize('nested', [False, True])
def test_baseline_shoulder_accepts_only_explicit_complete_chain(tmp_path, nested):
    data, cfg = case(tmp_path, text_position=(1800, 1100), nested=nested)
    cfg['parameters']['text_tolerance'] = 350 if nested else 150
    assert infer(data, cfg)['metrics']['vertical_length_rule'] == 0
    cfg['text_attachment_policy'] = 'terminal_or_baseline_shoulder'
    cfg['parameters']['shoulder_angle_tolerance_deg'] = 1
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == (0 if nested else 2.3)
    note = out['vertical_annotation_binding']['annotations'][0]
    assert note['text_attachment']['kind'] == 'baseline_shoulder'


def test_full_transformed_arc_is_kept_and_not_measured_as_height(tmp_path):
    data, cfg = case(tmp_path, nested=True, curved=True)
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == 2.3
    parts = out['vertical_annotation_binding']['annotations'][0]['leader_geometry']
    arc = next(s['part'] for s in parts if s['part']['kind'] == 'ellipse_arc')
    assert 'u' in arc and 'v' in arc and arc['sweep_param'] > 0
    assert math_dist(arc['u']) != math_dist(arc['v'])


def math_dist(vector):
    return sum(x*x for x in vector) ** .5


def test_existing_shared_review_html_contains_calculated_quantity_and_safe_text(tmp_path):
    from build_duct_vertical_annotation_review import project
    data, cfg = case(tmp_path, text='风管下接高度2.3m <img src=x onerror=alert()>')
    result = infer(data, cfg); view = project(result)
    assert view['objects'][0]['quantities'][0]['value'] is None
    assert view['objects'][0]['attributes']['高度事实']['length_m'] == 2.3
    result_path = tmp_path / 'result.json'; save(result_path, result)
    p = run(SCRIPTS / 'build_duct_vertical_annotation_review.py', '--input', result_path,
            '--output', tmp_path / 'view.json', '--html', tmp_path / 'review.html')
    assert p.returncode == 0, p.stdout + p.stderr
    page = (tmp_path / 'review.html').read_text(encoding='utf-8')
    assert '<img src=x onerror=alert()>' not in page
    assert (tmp_path / 'view.json').exists()


def test_unitless_needs_checked_geometry_and_keeps_its_evidence(tmp_path):
    data, cfg = case(tmp_path)
    p = Path(cfg['neutral_manifest']['path']); manifest = json.loads(p.read_text(encoding='utf-8'))
    manifest['drawing_unit']['raw_unit'] = 'Unitless'; cfg['neutral_manifest'] = save(p, manifest)
    cfg['unit_evidence'] = {'unit': 'mm', 'basis': '独立合成已知一米直管，源坐标跨度1000',
        'geometric_corroboration': [{'kind': 'known_component_size', 'object_ids': [cfg['segment_bindings'][0]['source_id']],
                                   'measured_drawing_units': 1000, 'real_length_m': 1, 'basis': '合成独立一米实体'}]}
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == 2.3
    assert out['vertical_annotation_binding']['evidence']['unit_corroboration'][0]['implied_metres_per_unit'] == .001
    cfg['unit_evidence']['geometric_corroboration'][0]['real_length_m'] = 10
    invalid = infer(data, cfg)
    assert invalid['vertical_annotation_binding']['annotations'][0]['length'] == 2.3
    assert invalid['vertical_items'][0]['theoretical_surface_area'] is None


def test_known_coordinate_unit_conflict_blocks_identity_instead_of_converting_silently(tmp_path):
    data, cfg = case(tmp_path)
    p = Path(cfg['neutral_manifest']['path']); manifest = json.loads(p.read_text(encoding='utf-8'))
    manifest['drawing_unit']['raw_unit'] = 'm'; cfg['neutral_manifest'] = save(p, manifest)
    out = infer(data, cfg)
    assert out['vertical_annotation_binding']['annotations'][0]['height_fact']['length_m'] == 2.3
    assert out['vertical_annotation_binding']['annotations'][0]['length'] is None


@pytest.mark.parametrize('extra', [
    lambda s: s.add_line((1500,1000),(1500,2000),dxfattribs={'layer':'DIM_LEAD'}),
    lambda s: s.add_text('风管上接高度1m',dxfattribs={'layer':'DIM_LEAD','insert':(500,0),'height':100}),
])
def test_branching_or_two_texts_cannot_share_a_height_binding(tmp_path, extra):
    data, cfg = case(tmp_path, extra=extra)
    out = infer(data, cfg)
    assert out['metrics']['vertical_length_rule'] == 0
    assert all(n['length'] is None for n in out['vertical_annotation_binding']['annotations'])


def test_source_part_mismatch_is_not_a_valid_handle_binding(tmp_path):
    data, cfg = case(tmp_path)
    data['segments'][0]['end'] = [900,0]; cfg['duct_input'] = save(tmp_path/'duct.json',data)
    out = infer(data,cfg)
    assert out['vertical_annotation_binding']['diagnostics']['binding_problems']
    assert out['vertical_annotation_binding']['annotations'][0]['length'] is None


def test_missing_source_and_changed_evidence_return_nonzero_without_output(tmp_path):
    data, cfg = case(tmp_path)
    p = Path(cfg['neutral_manifest']['path']); m = json.loads(p.read_text(encoding='utf-8'))
    layout_path = Path(m['artifacts']['layout_entities']['path'])
    layout_path.write_text(layout_path.read_text(encoding='utf-8')+'\n',encoding='utf-8')
    config_path=tmp_path/'cfg.json'; save(config_path,cfg)
    process=run(SCRIPTS/'bind_duct_vertical_annotations.py','--input',config_path,'--output',tmp_path/'never.json')
    assert process.returncode==2 and not (tmp_path/'never.json').exists()
    assert '已改变' in process.stderr


@pytest.mark.parametrize('text', ['风管下接高度2.3m以上', '风管下接高度小于2.3m', '风管下接高度2.3m≤3m',
                                 '风管下接高度' + '9'*400 + 'm'])
def test_limit_or_nonfinite_number_is_not_a_complete_length(text):
    fact, problem = binding.parse_height(text)
    assert problem


@pytest.mark.parametrize('text,expected', [
    ('水管下接高度2.4m', 0), ('冷媒管下接高度2.4m', 0), ('桥架下接高度2.4m', 0),
    ('风管及水管下接高度2.4m', 0), ('风管与另一风管下接高度2.4m', 0),
    ('风管下接高度2.4m 水管上接高度1.2m', 0),
    ('风管下接高度2.4m', 2.4), ('下接高度2.4m', 2.4), ('立管下接高度2.4m', 2.4),
])
def test_r2_source_subject_is_checked_in_actual_consumer_cli(tmp_path, text, expected):
    data, cfg = case(tmp_path, text=text)
    # A forged normalized subject/height must not override source TEXT.
    data['vertical_annotations'] = [{'subject': '风管', 'height': 999, 'unit': 'm'}]
    cfg['duct_input'] = save(tmp_path/'duct.json', data)
    cfg['annotations'] = [{'subject': '风管', 'height_fact': {'length_m': 999}}]
    config_path=tmp_path/'cfg.json'; save(config_path,cfg)
    bound=tmp_path/'bound.json'; result=tmp_path/'result.json'
    first=run(SCRIPTS/'bind_duct_vertical_annotations.py','--input',config_path,'--output',bound)
    assert first.returncode==0, first.stderr
    process=run(SCRIPTS/'infer_duct_fittings_vertical.py','--input',bound,'--output',result)
    assert process.returncode==0, process.stderr
    out=json.loads(result.read_text(encoding='utf-8'))
    assert out['metrics']['vertical_length_rule']==expected
    assert out['vertical_annotation_binding']['annotations'][0]['text']==text
    if expected==0:
        assert out['vertical_annotation_binding']['annotations'][0]['length'] is None


@pytest.mark.parametrize('entry', ['bind', 'infer', 'review_json', 'review_html'])
@pytest.mark.parametrize('protected_kind', ['source', 'input', 'layout'])
def test_r2_each_write_surface_refuses_hardlink_alias_without_touching_sources(tmp_path, entry, protected_kind):
    data, cfg=case(tmp_path)
    config_path=tmp_path/'config.json'; save(config_path,cfg)
    augmented=tmp_path/'augmented.json'; save(augmented,{**data,'vertical_annotation_input':cfg})
    result=tmp_path/'result.json'; save(result,infer(data,cfg))
    manifest=json.loads(Path(cfg['neutral_manifest']['path']).read_text(encoding='utf-8'))
    protected = {'source':Path(manifest['exchange_dxf']['path']),
                 'input': {'bind':config_path,'infer':augmented,'review_json':result,'review_html':result}[entry],
                 'layout':Path(manifest['artifacts']['layout_entities']['path'])}[protected_kind]
    alias=tmp_path/'protected-alias.json'; os.link(protected,alias)
    before=binding.sha(protected)
    if entry=='bind':
        process=run(SCRIPTS/'bind_duct_vertical_annotations.py','--input',config_path,'--output',alias)
    elif entry=='infer':
        process=run(SCRIPTS/'infer_duct_fittings_vertical.py','--input',augmented,'--output',alias)
    elif entry=='review_json':
        process=run(SCRIPTS/'build_duct_vertical_annotation_review.py','--input',result,'--output',alias)
    else:
        process=run(SCRIPTS/'build_duct_vertical_annotation_review.py','--input',result,'--output',tmp_path/'view.json','--html',alias)
        assert not (tmp_path/'view.json').exists(), 'HTML保护失败时不应先留下部分视图'
    assert process.returncode==2, process.stdout+process.stderr
    assert binding.sha(protected)==before and binding.sha(alias)==before


def test_r2_infer_keeps_legal_overwrite_of_non_source_previous_result(tmp_path):
    data,cfg=case(tmp_path)
    augmented=tmp_path/'augmented.json'; save(augmented,{**data,'vertical_annotation_input':cfg})
    output=tmp_path/'old-result.json'; output.write_text('old result',encoding='utf-8')
    p=run(SCRIPTS/'infer_duct_fittings_vertical.py','--input',augmented,'--output',output)
    assert p.returncode==0, p.stderr
    assert json.loads(output.read_text(encoding='utf-8'))['metrics']['vertical_length_rule']==2.3


def test_r2_reparse_parent_alias_to_protected_source_is_refused(tmp_path):
    data,cfg=case(tmp_path)
    augmented=tmp_path/'augmented.json'; save(augmented,{**data,'vertical_annotation_input':cfg})
    alias_dir=tmp_path/'directory-alias'
    if os.name=='nt':
        process=subprocess.run(['powershell','-NoProfile','-Command',
            'New-Item -ItemType Junction -Path $env:T48_JUNCTION_PATH -Target $env:T48_JUNCTION_TARGET | Out-Null'],
            env={**os.environ,'T48_JUNCTION_PATH':str(alias_dir),'T48_JUNCTION_TARGET':str(tmp_path)},
            stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8')
        assert process.returncode==0, process.stderr
    else:
        alias_dir.symlink_to(tmp_path,target_is_directory=True)
    source=tmp_path/'source.dxf'; before=binding.sha(source)
    p=run(SCRIPTS/'infer_duct_fittings_vertical.py','--input',augmented,'--output',alias_dir/'source.dxf')
    assert p.returncode==2 and binding.sha(source)==before


def test_r2_atomic_write_error_preserves_old_result_and_source_bytes(tmp_path,monkeypatch):
    source=tmp_path/'source'; source.write_bytes(b'original')
    old=tmp_path/'result'; old.write_bytes(b'old result')
    def denied(*args): raise OSError('simulated replacement failure')
    monkeypatch.setattr(binding.os,'replace',denied)
    with pytest.raises(OSError): binding.write_protected_json(old,{'new':1},[source],allow_existing=True)
    assert source.read_bytes()==b'original' and old.read_bytes()==b'old result'
    assert not list(tmp_path.glob('.qto-write-*.tmp'))


@pytest.mark.parametrize('other_arrow,expected', [((1000,.5),0), ((0,.5),4.8)])
def test_r2_source_ports_distinguish_same_end_from_actual_opposite_end(tmp_path,other_arrow,expected):
    def extra(space):
        space.add_lwpolyline([other_arrow,(1500,-1000),(3000,-1000)],dxfattribs={'layer':'DIM_LEAD'})
        space.add_text('风管下接高度2.4m',dxfattribs={'layer':'DIM_LEAD','insert':(3000,-1000),'height':100})
    data,cfg=case(tmp_path,text='风管下接高度2.4m',arrow=(1000,0),extra=extra)
    cfg['claimed_connection_ids']=['fake-A','fake-B']
    config_path=tmp_path/'config.json'; save(config_path,cfg)
    bound=tmp_path/'bound.json'; output=tmp_path/'result.json'
    p=run(SCRIPTS/'bind_duct_vertical_annotations.py','--input',config_path,'--output',bound)
    assert p.returncode==0,p.stderr
    p=run(SCRIPTS/'infer_duct_fittings_vertical.py','--input',bound,'--output',output)
    assert p.returncode==0,p.stderr
    out=json.loads(output.read_text(encoding='utf-8'))
    assert out['metrics']['vertical_length_rule']==expected
    notes=out['vertical_annotation_binding']['annotations']
    assert {n['source_connection_port']['end'] for n in notes} == ({'end'} if expected==0 else {'start','end'})
    assert all(n['length'] is None for n in notes) if expected==0 else all(n['length']==2.4 for n in notes)
