"""Batch frame entry for pipe inference inputs (registrations 20261015-0026, 0154, 0155, 0156).

Synthetic multi-frame package: two good frames (one with a nested block pipe), one
frame without any pipe, unfamilied objects, an excluded symbol family stated without
a system, a block whose definition is missing (one instance inside a frame, one
outside every frame), and an INSUNITS=0 drawing whose unit comes only from a
model-reviewed unit statement, plus a mixed m/mm statement and a sheet frame that
can corroborate the coordinate unit geometrically.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills' / '_qto-shared-evidence' / 'scripts'
PIPE = ROOT / 'skills' / 'qto-pipe-networks' / 'scripts'
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(PIPE))
import extract_cad_neutral as extract
import build_pipe_inference_inputs_batch as batch

BATCH = PIPE / 'build_pipe_inference_inputs_batch.py'
ADAPTER = PIPE / 'build_pipe_inference_input.py'
A = [0, 0, 100000, 100000]
B = [100000, 0, 200000, 100000]
C = [0, 200000, 100000, 300000]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


@pytest.fixture
def package(tmp_path):
    source = tmp_path / 'source.dwg'; source.write_bytes(b'batch-drawing-fixture')
    exchange = tmp_path / 'source.dxf'; exchange.write_bytes(b'batch-exchange-fixture')
    doc = ezdxf.new(); doc.units = 0; m = doc.modelspace()
    valve = doc.blocks.new('VALVE'); valve.add_circle((0, 0), 300)
    unknown = doc.blocks.new('UNKNOWN'); unknown.add_line((0, 0), (500, 500))
    pipe_block = doc.blocks.new('PIPEBLK'); pipe_block.add_line((0, 0), (10000, 0), dxfattribs={'layer': 'PIPE'})
    symbol = doc.blocks.new('SYMBOL'); symbol.add_line((0, 0), (800, 0)); symbol.add_line((0, 0), (0, 800))
    ghost = doc.blocks.new('GHOST'); ghost.add_line((0, 0), (100, 0))   # definition deliberately not extracted
    tagged = {}
    tagged['a1'] = m.add_line((1000, 1000), (50000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['a2'] = m.add_line((50000, 1000), (50000, 40000), dxfattribs={'layer': 'PIPE'})
    tagged['valve'] = m.add_blockref('VALVE', (25000, 1000), dxfattribs={'layer': 'VALVE'})
    tagged['dn'] = m.add_text('DN80', dxfattribs={'insert': (20000, 1500), 'layer': 'TEXT'})
    tagged['mystery'] = m.add_line((60000, 60000), (70000, 60000), dxfattribs={'layer': 'MYSTERY'})
    tagged['unknown'] = m.add_blockref('UNKNOWN', (80000, 80000), dxfattribs={'layer': 'MISC'})
    tagged['symbol'] = m.add_blockref('SYMBOL', (30000, 30000), dxfattribs={'layer': 'SYM'})
    tagged['unit'] = m.add_text('说明：本图坐标按毫米 1:1 绘制', dxfattribs={'insert': (5000, 95000), 'layer': 'NOTE'})
    tagged['b1'] = m.add_line((101000, 1000), (150000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['bblk'] = m.add_blockref('PIPEBLK', (150000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['ghost_in_b'] = m.add_blockref('GHOST', (120000, 50000), dxfattribs={'layer': 'MISC'})
    tagged['c_mystery'] = m.add_line((10000, 210000), (20000, 210000), dxfattribs={'layer': 'MYSTERY'})
    # outside every frame: a drawing-level broken block, a mixed-unit note and an A1 sheet frame at 1:100
    tagged['ghost_out'] = m.add_blockref('GHOST', (350000, 150000), dxfattribs={'layer': 'MISC'})
    tagged['mixed'] = m.add_text('图中所注尺寸除管长、标高以m计外，其余以mm计。', dxfattribs={'insert': (305000, 70000), 'layer': 'NOTE'})
    tagged['sheet'] = m.add_lwpolyline([(300000, 0), (384100, 0), (384100, 59400), (300000, 59400)], close=True, dxfattribs={'layer': 'TK'})
    h = sha(source)
    layout_rows = [extract.convert(e, layout='Model', parent_block=None, source_hash=h, raw_unit='Unitless', sequence=i) for i, e in enumerate(m, 1)]
    block_rows = []
    extracted = ('VALVE', 'UNKNOWN', 'PIPEBLK', 'SYMBOL')
    for name in extracted:
        for i, e in enumerate(doc.blocks.get(name), 1):
            block_rows.append(extract.convert(e, layout='Model', parent_block=name, source_hash=h, raw_unit='Unitless', sequence=i))
    stream = tmp_path / 'layout.ndjson'; stream.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in layout_rows) + '\n', encoding='utf-8')
    blocks = tmp_path / 'blocks.ndjson'; blocks.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in block_rows) + '\n', encoding='utf-8')
    manifest = dict(schema_version='qto-cad-neutral-evidence/1.0', geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(source), sha256=h), exchange_dxf=dict(path=str(exchange), sha256=sha(exchange)),
        drawing_unit=dict(insunits_code=0, raw_unit='Unitless'), blocks=[dict(block_name=n, origin=[0., 0., 0.]) for n in extracted],
        artifacts={'layout_entities': dict(path=str(stream), sha256=sha(stream)), 'block_definitions': dict(path=str(blocks), sha256=sha(blocks))})
    manifest_path = put(tmp_path / 'manifest.json', manifest)
    ids = {key: f'CAD-{entity.dxf.handle}' for key, entity in tagged.items()}
    mapping = dict(schema_version='qto-pipe-batch-family-mapping/1.0', mapping_id='MAP', mapping_version='1', expected_source_sha256=h,
        recognized_by='model-under-test', recognized_at='2026-10-15T01:00:00+08:00', source_id='fixture-legend', network_id_prefix='FW',
        evidence=[dict(evidence_id='EV-stream', path='layout.ndjson', sha256=sha(stream), kind='cad_property', locator='model space',
                       observation='PIPE 图层为消防水管中心线，VALVE 块为在线阀门，SYM 块为标注符号，MYSTERY/UNKNOWN 图例未查明')],
        unit=dict(factor_to_output=.001, output_unit='m', source_id='EV-stream', raw_unit='Millimeters',
                  declaration=dict(raw_unit='Millimeters', annotation_object_ids=[ids['unit']], observation='图纸说明写明坐标按毫米绘制',
                                   applies_to='coordinates', source_excerpt='本图坐标按毫米 1:1 绘制', evidence_ids=['EV-stream'])),
        families=[
            dict(family_id='FW-PIPE', kind='layer', layer_pattern='^PIPE$', system='FW', decision='include', evidence_ids=['EV-stream'], basis='图例：PIPE 为消防水管', priority=10),
            dict(family_id='FW-VALVE', kind='block', layer_pattern='^VALVE$', block_pattern='^VALVE$', system='FW', decision='include', evidence_ids=['EV-stream'], basis='图例：阀门块', valve_type='gate'),
            dict(family_id='X-SYM', kind='block', layer_pattern='^SYM$', block_pattern='^SYMBOL$', decision='exclude', evidence_ids=['EV-stream'], basis='标注符号块，不是管道'),
            dict(family_id='FW-DN', kind='text', layer_pattern='^TEXT$', text_pattern=r'DN(?P<dn>\d+)', system='FW', max_distance=2.0, min_second_candidate_margin=.1),
        ],
        use_conventional_terminal_type_candidates=False, use_conventional_valve_type_candidates=False)
    mapping_path = put(tmp_path / 'mapping.json', mapping)
    frames = put(tmp_path / 'frames.json', dict(schema_version='qto-pipe-batch-frames/1.0', frames=[
        dict(frame_id='A1', name='首层', bounds=A), dict(frame_id='B1', name='二层', bounds=B), dict(frame_id='C1', name='空框', bounds=C)]))
    return dict(tmp=tmp_path, manifest=manifest_path, mapping=mapping, mapping_path=mapping_path, frames=frames, ids=ids, stream=stream)


def run(pkg, *extra, mapping=None, frames=None):
    out = pkg['tmp'] / 'out'
    if out.exists():
        import shutil; shutil.rmtree(out)
    mp = put(pkg['tmp'] / 'mapping2.json', mapping) if mapping is not None else pkg['mapping_path']
    p = subprocess.run([sys.executable, '-B', str(BATCH), '--cad-manifest', str(pkg['manifest']), '--frames', str(frames or pkg['frames']),
                        '--mapping', str(mp), '--output-dir', str(out), *extra], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    summary = json.loads((out / '批量转换汇总.json').read_text(encoding='utf-8')) if (out / '批量转换汇总.json').exists() else None
    return p, summary, out


def frame(summary, frame_id):
    return next(f for f in summary['frames'] if f['frame_id'] == frame_id)


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def adapter_diags(summary):
    return [d for d in summary['diagnostics'] if d['frame_id'] is not None]


def test_each_frame_gets_its_own_input_and_unfamilied_objects_become_pending(package):
    p, summary, out = run(package)
    assert p.returncode == 0, p.stdout + p.stderr  # C1 has no pipe: NO_PIPES is not a failure (0154)
    assert summary['status'] == 'REVIEW_REQUIRED' and summary['business_adoption_allowed'] is False
    a, b, c = frame(summary, 'A1'), frame(summary, 'B1'), frame(summary, 'C1')
    assert a['status'] == 'REVIEW_REQUIRED' and b['status'] == 'REVIEW_REQUIRED' and c['status'] == 'NO_PIPES'
    ia = json.loads((out / 'A1' / '管网推算输入.json').read_text(encoding='utf-8'))
    ib = json.loads((out / 'B1' / '管网推算输入.json').read_text(encoding='utf-8'))
    assert ia['schema_version'] == 'qto-pipe-network-inference-input/1.2' and ia['network_id'] == 'FW-A1'
    assert sorted(s['length'] for s in ia['segments']) == pytest.approx([39, 49])
    assert len(ia['valves']) == 1 and ia['valves'][0]['valve_type'] == 'gate'
    assert ia['dn_match_candidates'][0]['dn'] == 'DN80' and ia['dn_match_candidates'][0]['disposition'] == 'scenario'
    assert ia['recognition_basis']['formal_adoption_allowed'] is False
    # the nested block pipe is expanded into frame B with its instance path, not dropped
    assert sorted(s['length'] for s in ib['segments']) == pytest.approx([10, 49])
    assert any(s['segment_id'].startswith('SEG-CADINST-') for s in ib['segments'])
    assert a['family_counts'] == {'FW-PIPE': 2, 'FW-VALVE': 1, 'X-SYM': 1, 'FW-DN': 0} and a['text_family_hits'] == {'FW-DN': 1}
    detail = {item['object_id']: item for item in lines(out / '待办明细.jsonl')}
    assert detail[package['ids']['mystery']]['reason'] == 'no_family' and detail[package['ids']['mystery']]['frame_id'] == 'A1'
    assert detail[package['ids']['unknown']]['entity_type'] == 'INSERT' and detail[package['ids']['unknown']]['block_name'] == 'UNKNOWN'
    assert detail[package['ids']['c_mystery']]['frame_id'] == 'C1'
    # the unfamilied line is still an unresolved object in the adapter's own closure list, not silently excluded
    dispositions = lines(out / 'A1' / '管网中性对象处置清单.jsonl')
    assert next(d for d in dispositions if d['object_id'] == package['ids']['mystery'])['disposition'] == 'unresolved'
    # the per-frame ledger lists every object the family covered, as a working (unreviewed) record
    ledger = json.loads((out / 'A1' / '模型工作识别记录.json').read_text(encoding='utf-8'))
    pipe_entry = next(e for e in ledger['entries'] if e['adjudication_id'] == 'FW-PIPE')
    assert sorted(pipe_entry['object_ids']) == sorted([package['ids']['a1'], package['ids']['a2']])
    assert ledger['review_status'] == 'unreviewed' and ledger['adoption_scope'] == 'working'


# --- 0154: selection before validation, excluded families, frames without pipes ---

def test_frame_without_pipes_is_its_own_status_not_a_failure(package):
    p, summary, out = run(package, '--frame-id', 'C1')
    assert p.returncode == 0 and summary['status'] == 'NO_PIPES', p.stdout + p.stderr
    c = frame(summary, 'C1')
    assert c['pending_line_objects'] == 1 and '未归族线对象' in c['reason'] and not (out / 'C1' / '识别配置.json').exists()
    assert not adapter_diags(summary)


def test_excluded_family_without_system_runs_and_owns_its_children(package):
    p, summary, out = run(package, '--frame-id', 'A1')
    assert p.returncode == 0, p.stdout + p.stderr
    assert summary['mapping']['defaulted_system_families'] == ['X-SYM']
    profile = json.loads((out / 'A1' / '识别配置.json').read_text(encoding='utf-8'))
    rule = next(r for r in profile['block_rules'] if r['rule_id'] == 'X-SYM')
    assert rule['decision'] == 'exclude' and rule['system'] == batch.EXCLUDED_SYSTEM_LABEL
    dispositions = {d['object_id']: d for d in lines(out / 'A1' / '管网中性对象处置清单.jsonl')}
    assert dispositions[package['ids']['symbol']]['disposition'] == 'excluded'
    children = [d for d in dispositions.values() if str(d.get('instance_path') or '').startswith(package['ids']['symbol'])]
    assert len(children) == 2 and all(d['disposition'] == 'excluded' for d in children)
    a = frame(summary, 'A1')
    assert a['pending_suppressed_by_owner'] == 2  # the symbol's lines are not pending: their owner is decided
    assert not any(str(item.get('instance_path') or '').startswith(package['ids']['symbol']) for item in lines(out / '待办明细.jsonl'))


def test_sheet_ledger_filters_before_validating_rows(package):
    csv_path = package['tmp'] / '图页台账.csv'
    csv_path.write_text('﻿图页编号,图名,x1,y1,x2,y2,状态\n'
                        'A1,首层,0,0,100000,100000,现行\n'
                        'B1,二层,100000,0,200000,100000,现行\n'
                        '待核(Q1?),首层自喷,0,0,100000,100000,现行\n'
                        '无编号,旧图,0,0,1,1,作废\n'
                        '无编号,旧图,0,0,1,1,作废\n'
                        ',空行,abc,0,1,1,作废\n', encoding='utf-8')
    # an explicit frame never touches the malformed rows
    p, summary, out = run(package, '--frame-id', 'A1', frames=csv_path)
    assert p.returncode == 0 and [f['frame_id'] for f in summary['frames']] == ['A1'], p.stdout + p.stderr
    # selecting 现行 reaches the malformed id: the whole batch stops with the row named ...
    p, summary, out = run(package, '--only-status', '现行', frames=csv_path)
    assert p.returncode == 2 and summary['status'] == 'INPUT_REQUIRED'
    assert '第 4 行' in summary['diagnostics'][0]['error'] and '图框编号只能含' in summary['diagnostics'][0]['error']
    # ... unless the host asks to skip invalid selected rows; duplicates among 作废 rows never matter
    p, summary, out = run(package, '--only-status', '现行', '--skip-invalid-frames', frames=csv_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert sorted(f['frame_id'] for f in summary['frames']) == ['A1', 'B1'] and summary['skipped_invalid_frames'] == ['待核(Q1?)']
    assert any(d.get('action') == 'skipped_invalid_frame' for d in summary['diagnostics'])
    with pytest.raises(batch.BatchInputError, match='图框编号重复'):
        batch.load_frames(csv_path, only_status='作废')
    with pytest.raises(batch.BatchInputError, match='未在图框清单中'):
        batch.load_frames(csv_path, frame_ids=['Z9'])


def test_adapter_failure_naming_a_family_is_classified_by_that_family(package):
    families = [dict(family_id='F-EXC-标注底图', kind='layer', layer_pattern='^标注$', decision='exclude'),
                dict(family_id='F-EXC', kind='layer', layer_pattern='^X$', decision='exclude')]
    label = batch.classify_adapter_failure('F-EXC-标注底图.system 必须是非空文本', {'entity_counts': {'ARC': 298}}, families)
    assert label.startswith('族 F-EXC-标注底图') and '^标注$' in label
    assert batch.classify_adapter_failure('其他错误', {'entity_counts': {'ARC': 2}}, families) == 'ARC:2'


# --- 0156: one read of the neutral stream, frame-scoped diagnostics, grouped pending ---

def test_frames_use_prescoped_rows_and_match_a_direct_adapter_run(package):
    p, summary, out = run(package)
    assert summary['scan']['neutral_stream_reads'] == 1
    report = json.loads((out / 'A1' / '管网识别报告.json').read_text(encoding='utf-8'))
    assert report['neutral_source']['prescoped_rows']['scoped_by'] == 'batch_entry_single_read'
    assert report['counts']['total_neutral_rows'] == summary['scan']['total_neutral_rows']
    # control: the same profile without the prescoped file, expanded by the adapter itself
    profile = json.loads((out / 'A1' / '识别配置.json').read_text(encoding='utf-8'))
    profile.pop('prescoped_rows')
    direct_profile = put(out / 'A1' / '识别配置_直读.json', profile)
    direct = out / 'A1_direct'
    run_direct = subprocess.run([sys.executable, '-B', str(ADAPTER), '--cad-manifest', str(package['manifest']), '--profile', str(direct_profile),
                                 '--adjudication-ledger', str(out / 'A1' / '模型工作识别记录.json'), '--output-dir', str(direct)],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert run_direct.returncode == 0, run_direct.stderr
    via_batch = {(d['object_id'], d['disposition']) for d in lines(out / 'A1' / '管网中性对象处置清单.jsonl')}
    via_direct = {(d['object_id'], d['disposition']) for d in lines(direct / '管网中性对象处置清单.jsonl')}
    assert via_batch == via_direct
    ia = json.loads((out / 'A1' / '管网推算输入.json').read_text(encoding='utf-8'))
    id_ = json.loads((direct / '管网推算输入.json').read_text(encoding='utf-8'))
    assert ia['segments'] == id_['segments'] and ia['valves'] == id_['valves']
    # the direct run still repeats the drawing-level failure; the prescoped run does not
    direct_report = json.loads((direct / '管网识别报告.json').read_text(encoding='utf-8'))
    assert any(u['kind'] == 'missing_block_definition' for u in direct_report['unresolved'])
    assert not any(u['kind'] == 'missing_block_definition' for u in report['unresolved'])


def test_drawing_level_failures_are_reported_once_and_frame_failures_stay_with_the_frame(package):
    p, summary, out = run(package)
    level = summary['drawing_level_expansion_failures']
    assert level['count'] == 1 and level['items'][0]['instance_path'] == package['ids']['ghost_out']
    assert sum(1 for d in summary['diagnostics'] if d['frame_id'] is None and d.get('kind') == 'missing_block_definition') == 1
    b = frame(summary, 'B1')
    assert b['block_expansion_failures'] == 1 and b['adapter_unresolved_kinds'].get('missing_block_definition') == 1
    assert frame(summary, 'A1')['block_expansion_failures'] == 0


def test_pending_children_are_grouped_under_their_top_level_block(package):
    p, summary, out = run(package, '--frame-id', 'A1')
    groups = {g['top_object_id']: g for g in lines(out / '待办清单.jsonl')}
    unknown = groups[package['ids']['unknown']]
    assert unknown['member_count'] == 2 and unknown['nested_members'] == 1 and unknown['top_is_pending'] is True
    assert unknown['block_name'] == 'UNKNOWN' and unknown['reasons'] == {'no_family': 2}
    assert groups[package['ids']['mystery']]['member_count'] == 1
    a = frame(summary, 'A1')
    assert a['pending_groups'] == 2 and a['pending_count'] == 3
    top = {(row['layer'], row['block_name']): row for row in summary['pending_top_level_summary']}
    assert top[('MISC', 'UNKNOWN')]['groups'] == 1 and top[('MISC', 'UNKNOWN')]['members'] == 2


def test_prescoped_rows_are_bound_to_profile_scope_and_bytes(package):
    p, summary, out = run(package, '--frame-id', 'A1')
    prescoped = out / 'A1' / '预扫描图元.ndjson'
    profile_path = out / 'A1' / '识别配置.json'

    def adapter_run():
        return subprocess.run([sys.executable, '-B', str(ADAPTER), '--cad-manifest', str(package['manifest']), '--profile', str(profile_path),
                               '--adjudication-ledger', str(out / 'A1' / '模型工作识别记录.json'), '--output-dir', str(out / 'A1_again')],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    raw = prescoped.read_text(encoding='utf-8').splitlines()
    prescoped.write_text('\n'.join(raw[:-1]) + '\n', encoding='utf-8')   # drop a row
    r = adapter_run()
    assert r.returncode == 2 and '哈希' in r.stderr
    header = json.loads(raw[0]); header['bounds']['xmax_exclusive'] = 150000
    prescoped.write_text('\n'.join([json.dumps(header, ensure_ascii=False)] + raw[1:]) + '\n', encoding='utf-8')
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    profile['prescoped_rows']['sha256'] = sha(prescoped)
    put(profile_path, profile)
    r = adapter_run()
    assert r.returncode == 2 and '坐标框' in r.stderr


# --- 0155: unit statements naming several units ---

def mixed_unit_mapping(package, **declaration):
    mapping = json.loads(json.dumps(package['mapping']))
    mapping['unit']['declaration'] = dict(raw_unit='Millimeters', annotation_object_ids=[package['ids']['mixed']], evidence_ids=['EV-stream'],
                                          observation='总说明：图中所注尺寸除管长、标高以m计外，其余以mm计', source_excerpt='其余以mm计', **declaration)
    return mapping


SHEET = dict(kind='frame_size_scale', paper_size_mm=[841,594], scale_denominator=100, measured_drawing_units=[84100,59400], basis='A1 图框幅面 841×594，图签比例 1:100')


def test_half_sentence_about_annotations_needs_geometric_corroboration(package):
    p, summary, out = run(package, '--frame-id', 'A1', mapping=mixed_unit_mapping(package, applies_to='annotation_values'))
    assert p.returncode == 2 and summary['status'] == 'INPUT_REQUIRED' and 'geometric_corroboration' in summary['diagnostics'][0]['error']
    # claiming the half sentence is about coordinates does not help while the cited text names two units
    p, summary, out = run(package, '--frame-id', 'A1', mapping=mixed_unit_mapping(package, applies_to='coordinates'))
    assert p.returncode == 2 and frame(summary, 'A1')['status'] == 'FAILED'
    assert '多种单位' in frame(summary, 'A1')['error'] and frame(summary, 'A1')['failed_step'] == 'adapter'


def test_sheet_frame_at_plot_scale_corroborates_the_coordinate_unit(package):
    mapping = mixed_unit_mapping(package, applies_to='annotation_values', geometric_corroboration=[dict(SHEET, object_ids=[package['ids']['sheet']])])
    p, summary, out = run(package, '--frame-id', 'A1', mapping=mapping)
    assert p.returncode == 0, p.stdout + p.stderr
    report = json.loads((out / 'A1' / '管网识别报告.json').read_text(encoding='utf-8'))
    declaration = report['unit']['declaration']
    assert declaration['coordinate_unit_basis'] == 'geometric_corroboration' and declaration['applies_to'] == 'annotation_values'
    assert declaration['geometric_corroboration'][0]['implied_metres_per_unit'] == pytest.approx(.001)
    ledger = json.loads((out / 'A1' / '模型工作识别记录.json').read_text(encoding='utf-8'))
    unit_entry = next(e for e in ledger['entries'] if e['adjudication_id'] == 'UNIT')
    assert package['ids']['sheet'] in unit_entry['object_ids']   # the sheet frame lies outside A1 yet reaches the adapter


@pytest.mark.parametrize('change,message', [
    (dict(scale_denominator=50), '不符'),                          # implies 0.5 mm per unit
    (dict(paper_size_mm=[900,594], measured_drawing_units=[90000,59400]), '实测边'),  # stated width is not the cited frame's extent
])
def test_wrong_corroboration_is_rejected(package, change, message):
    mapping = mixed_unit_mapping(package, applies_to='annotation_values',
                                 geometric_corroboration=[dict(SHEET, object_ids=[package['ids']['sheet']], **change)])
    p, summary, out = run(package, '--frame-id', 'A1', mapping=mapping)
    assert p.returncode == 2 and frame(summary, 'A1')['status'] == 'FAILED' and message in frame(summary, 'A1')['error']


def test_unit_declaration_must_state_what_it_applies_to(package):
    mapping = json.loads(json.dumps(package['mapping']))
    mapping['unit']['declaration'].pop('applies_to')
    p, summary, out = run(package, mapping=mapping)
    assert p.returncode == 2 and summary['status'] == 'INPUT_REQUIRED' and 'applies_to' in summary['diagnostics'][0]['error']


# --- earlier behaviour (0026) kept ---

def test_inference_runs_per_frame_when_requested(package):
    p, summary, out = run(package, '--run-inference', '--frame-id', 'A1')
    assert p.returncode == 0, p.stdout + p.stderr
    evidence = json.loads((out / 'A1' / '管网推算证据.json').read_text(encoding='utf-8'))
    assert evidence['metrics']['raw_length'] == pytest.approx(88)
    assert frame(summary, 'A1')['inference_output'].endswith('管网推算证据.json')


def test_unitless_drawing_without_unit_statement_produces_no_length(package):
    mapping = json.loads(json.dumps(package['mapping']))
    mapping['unit'].pop('declaration')
    p, summary, out = run(package, mapping=mapping)
    assert p.returncode == 2 and summary['status'] == 'FAILED'
    assert frame(summary, 'A1')['status'] == 'FAILED' and frame(summary, 'B1')['status'] == 'FAILED' and frame(summary, 'C1')['status'] == 'NO_PIPES'
    diags = adapter_diags(summary)
    assert diags and all('单位' in d['error'] for d in diags) and diags[0]['object_class'] == '单位说明文字'
    assert diags[0]['step'] == 'adapter' and frame(summary, 'A1')['error'] == diags[0]['error']
    assert not (out / 'A1' / '管网推算输入.json').exists()


def test_unit_statement_must_match_declared_unit(package):
    mapping = json.loads(json.dumps(package['mapping']))
    mapping['unit'].update(raw_unit='Meters', factor_to_output=1.0)
    mapping['unit']['declaration'].update(raw_unit='Meters')
    mapping['unit']['declaration'].pop('source_excerpt')
    p, summary, out = run(package, mapping=mapping, )
    assert p.returncode == 2 and adapter_diags(summary) and all('单位' in d['error'] for d in adapter_diags(summary))


def test_sheet_ledger_csv_frames_and_scale_factor_guard(package):
    csv_path = package['tmp'] / '图页台账.csv'
    csv_path.write_text('﻿图页编号,图名,楼层区域,专业系统,x1,y1,x2,y2,比例系数,状态,依据,备注\n'
                        'A1,首层消火栓平面图,A 首层,消火栓,0,0,100000,100000,1,现行,"图框文字 (1,1)",1:150\n'
                        'B1,二层消火栓平面图,A 二层,消火栓,100000,0,200000,100000,1,现行,图框文字,\n'
                        'X1,作废图,A 首层,消火栓,0,0,100000,100000,1,作废,图框文字,\n'
                        'S2,比例异常,A 首层,消火栓,0,0,100000,100000,2,现行,图框文字,\n', encoding='utf-8')
    frames = batch.load_frames(csv_path)
    assert [f['frame_id'] for f in frames] == ['A1', 'B1', 'X1', 'S2'] and frames[0]['bounds'] == [0, 0, 100000, 100000]
    assert frames[0]['system'] == '消火栓' and frames[0]['floor'] == 'A 首层' and frames[3]['scale_factor'] == 2
    p, summary, out = run(package, '--only-status', '现行', frames=csv_path)
    assert p.returncode == 3, p.stdout + p.stderr
    assert {f['frame_id']: f['status'] for f in summary['frames']} == {'S2': 'SKIPPED', 'A1': 'REVIEW_REQUIRED', 'B1': frame(summary, 'B1')['status']}
    assert '比例系数' in frame(summary, 'S2')['reason'] and not (out / 'S2').exists()


def test_frames_file_errors_are_diagnosed_not_crashed(package):
    bad = put(package['tmp'] / 'bad.json', dict(schema_version='qto-pipe-batch-frames/1.0', frames=[dict(frame_id='Z', bounds=[5, 5, 1, 1])]))
    p, summary, out = run(package, frames=bad)
    assert p.returncode == 2 and summary['status'] == 'INPUT_REQUIRED'
    assert summary['diagnostics'][0]['step'] == 'frames' and '坐标框无效' in summary['diagnostics'][0]['error']
