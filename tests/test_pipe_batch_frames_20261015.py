"""Batch frame entry for pipe inference inputs (registration 20261015-0026, 管网批量入口).

Synthetic multi-frame package: two good frames (one with a nested block pipe), one
frame no family covers, unfamilied objects, and an INSUNITS=0 drawing whose unit
comes only from a model-reviewed unit statement.
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
    tagged = {}
    tagged['a1'] = m.add_line((1000, 1000), (50000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['a2'] = m.add_line((50000, 1000), (50000, 40000), dxfattribs={'layer': 'PIPE'})
    tagged['valve'] = m.add_blockref('VALVE', (25000, 1000), dxfattribs={'layer': 'VALVE'})
    tagged['dn'] = m.add_text('DN80', dxfattribs={'insert': (20000, 1500), 'layer': 'TEXT'})
    tagged['mystery'] = m.add_line((60000, 60000), (70000, 60000), dxfattribs={'layer': 'MYSTERY'})
    tagged['unknown'] = m.add_blockref('UNKNOWN', (80000, 80000), dxfattribs={'layer': 'MISC'})
    tagged['unit'] = m.add_text('说明：本图尺寸单位为毫米，标高单位为米除外', dxfattribs={'insert': (5000, 95000), 'layer': 'NOTE'})
    tagged['b1'] = m.add_line((101000, 1000), (150000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['bblk'] = m.add_blockref('PIPEBLK', (150000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['c_mystery'] = m.add_line((10000, 210000), (20000, 210000), dxfattribs={'layer': 'MYSTERY'})
    h = sha(source)
    layout_rows = [extract.convert(e, layout='Model', parent_block=None, source_hash=h, raw_unit='Unitless', sequence=i) for i, e in enumerate(m, 1)]
    block_rows = []
    for name in ('VALVE', 'UNKNOWN', 'PIPEBLK'):
        for i, e in enumerate(doc.blocks.get(name), 1):
            block_rows.append(extract.convert(e, layout='Model', parent_block=name, source_hash=h, raw_unit='Unitless', sequence=i))
    stream = tmp_path / 'layout.ndjson'; stream.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in layout_rows) + '\n', encoding='utf-8')
    blocks = tmp_path / 'blocks.ndjson'; blocks.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in block_rows) + '\n', encoding='utf-8')
    manifest = dict(schema_version='qto-cad-neutral-evidence/1.0', geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(source), sha256=h), exchange_dxf=dict(path=str(exchange), sha256=sha(exchange)),
        drawing_unit=dict(insunits_code=0, raw_unit='Unitless'), blocks=[dict(block_name=n, origin=[0., 0., 0.]) for n in ('VALVE', 'UNKNOWN', 'PIPEBLK')],
        artifacts={'layout_entities': dict(path=str(stream), sha256=sha(stream)), 'block_definitions': dict(path=str(blocks), sha256=sha(blocks))})
    manifest_path = put(tmp_path / 'manifest.json', manifest)
    ids = {key: f'CAD-{entity.dxf.handle}' for key, entity in tagged.items()}
    mapping = dict(schema_version='qto-pipe-batch-family-mapping/1.0', mapping_id='MAP', mapping_version='1', expected_source_sha256=h,
        recognized_by='model-under-test', recognized_at='2026-10-15T01:00:00+08:00', source_id='fixture-legend', network_id_prefix='FW',
        evidence=[dict(evidence_id='EV-stream', path='layout.ndjson', sha256=sha(stream), kind='cad_property', locator='model space',
                       observation='PIPE 图层为消防水管中心线，VALVE 块为在线阀门，MYSTERY/UNKNOWN 图例未查明')],
        unit=dict(factor_to_output=.001, output_unit='m', source_id='EV-stream', raw_unit='Millimeters',
                  declaration=dict(raw_unit='Millimeters', annotation_object_ids=[ids['unit']], observation='图纸说明写明尺寸单位毫米',
                                   source_excerpt='本图尺寸单位为毫米', evidence_ids=['EV-stream'])),
        families=[
            dict(family_id='FW-PIPE', kind='layer', layer_pattern='^PIPE$', system='FW', decision='include', evidence_ids=['EV-stream'], basis='图例：PIPE 为消防水管', priority=10),
            dict(family_id='FW-VALVE', kind='block', layer_pattern='^VALVE$', block_pattern='^VALVE$', system='FW', decision='include', evidence_ids=['EV-stream'], basis='图例：阀门块', valve_type='gate'),
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


def test_each_frame_gets_its_own_input_and_unfamilied_objects_become_pending(package):
    p, summary, out = run(package)
    assert p.returncode == 3, p.stdout + p.stderr  # C1 fails; the others still complete
    assert summary['status'] == 'PARTIAL' and summary['business_adoption_allowed'] is False
    a, b, c = frame(summary, 'A1'), frame(summary, 'B1'), frame(summary, 'C1')
    assert a['status'] == 'REVIEW_REQUIRED' and b['status'] in {'READY', 'REVIEW_REQUIRED'} and c['status'] == 'FAILED'
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
    assert a['family_counts'] == {'FW-PIPE': 2, 'FW-VALVE': 1, 'FW-DN': 0} and a['text_family_hits'] == {'FW-DN': 1}
    pending = [json.loads(line) for line in (out / '待办清单.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    by_object = {item['object_id']: item for item in pending}
    assert by_object[package['ids']['mystery']]['reason'] == 'no_family' and by_object[package['ids']['mystery']]['frame_id'] == 'A1'
    assert by_object[package['ids']['unknown']]['entity_type'] == 'INSERT' and by_object[package['ids']['unknown']]['block_name'] == 'UNKNOWN'
    assert by_object[package['ids']['c_mystery']]['frame_id'] == 'C1'
    # the unfamilied line is still an unresolved object in the adapter's own closure list, not silently excluded
    dispositions = [json.loads(line) for line in (out / 'A1' / '管网中性对象处置清单.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    assert next(d for d in dispositions if d['object_id'] == package['ids']['mystery'])['disposition'] == 'unresolved'
    # failure keeps step, object class and the raw error text
    diag = next(d for d in summary['diagnostics'] if d['frame_id'] == 'C1')
    assert diag['step'] == 'adapter' and '管段' in diag['error'] and 'LINE' in diag['object_class']
    assert c['failed_step'] == 'adapter' and c['error'] == diag['error']
    # the per-frame ledger lists every object the family covered, as a working (unreviewed) record
    ledger = json.loads((out / 'A1' / '模型工作识别记录.json').read_text(encoding='utf-8'))
    pipe_entry = next(e for e in ledger['entries'] if e['adjudication_id'] == 'FW-PIPE')
    assert sorted(pipe_entry['object_ids']) == sorted([package['ids']['a1'], package['ids']['a2']])
    assert ledger['review_status'] == 'unreviewed' and ledger['adoption_scope'] == 'working'


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
    assert all(f['status'] == 'FAILED' for f in summary['frames'])
    assert all('单位' in d['error'] for d in summary['diagnostics']) and summary['diagnostics'][0]['object_class'] == '单位说明文字'
    assert not (out / 'A1' / '管网推算输入.json').exists()


def test_unit_statement_must_match_declared_unit(package):
    mapping = json.loads(json.dumps(package['mapping']))
    mapping['unit'].update(raw_unit='Meters', factor_to_output=1.0)
    mapping['unit']['declaration'].update(raw_unit='Meters', source_excerpt=None)
    mapping['unit']['declaration'].pop('source_excerpt')
    p, summary, out = run(package, mapping=mapping, )
    assert p.returncode == 2 and all('单位' in d['error'] for d in summary['diagnostics'])


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
