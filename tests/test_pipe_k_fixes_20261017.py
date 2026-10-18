"""Pipe batch entry K fixes (registrations 20261016-2359 pipe part, 20261017-0026, 20261017-0031 pipe part and
the K3 insertion-point guard for extents without fills).

Synthetic two-frame package:
- an empty block definition (serialized, no entities), inserted at top level and nested in a pipe-carrying block,
  next to a block whose definition really is missing (serialized=false);
- a polyline with a repeated vertex (one zero-length part) and a polyline whose only part has zero length;
- a base-map block whose insertion point lies outside every frame while its drawing lies inside A1, one that
  straddles A1 and B1, and a control whose insertion point is inside B1 while its drawing is outside it.
"""
import hashlib
import json
import shutil
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

BATCH = PIPE / 'build_pipe_inference_inputs_batch.py'
ADAPTER = PIPE / 'build_pipe_inference_input.py'
A = [0, 0, 100000, 100000]
B = [100000, 0, 200000, 100000]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


@pytest.fixture
def package(tmp_path):
    source = tmp_path / 'source.dwg'; source.write_bytes(b'k6-drawing-fixture')
    exchange = tmp_path / 'source.dxf'; exchange.write_bytes(b'k6-exchange-fixture')
    doc = ezdxf.new(); doc.units = 0; m = doc.modelspace()
    doc.blocks.new('AVE_RENDER')                      # serialized, no entities: not a missing definition
    holder = doc.blocks.new('HOLDER')
    holder.add_line((0, 0), (5000, 0), dxfattribs={'layer': 'PIPE'})
    holder.add_blockref('AVE_RENDER', (100, 100))
    ghost = doc.blocks.new('GHOST'); ghost.add_line((0, 0), (100, 0))   # definition deliberately not extracted
    base = doc.blocks.new('BASEMAP')
    base.add_line((10000, 60000), (40000, 60000)); base.add_line((10000, 60000), (10000, 80000))
    wide = doc.blocks.new('BASEMAP_W'); wide.add_line((90000, 0), (110000, 0))
    # K3 guard: drawing 200 m above the insertion point; HSYM also holds a fill, so its extent is incomplete
    hsym = doc.blocks.new('HSYM'); hsym.add_line((0, 200000), (1000, 200000))
    # T13 locates closed triangles; this test specifically needs an unknown fill to exercise the insertion guard.
    fill = hsym.add_hatch(); fill.paths.add_edge_path().add_line((0, 200000), (1000, 200000))
    lsym = doc.blocks.new('LSYM'); lsym.add_line((0, 200000), (1000, 200000))
    tagged = {}
    tagged['a1'] = m.add_line((1000, 1000), (50000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['dup'] = m.add_lwpolyline([(60000, 10000), (80000, 10000), (80000, 10000), (80000, 30000)], dxfattribs={'layer': 'PIPE'})
    tagged['zero'] = m.add_lwpolyline([(70000, 70000), (70000, 70000)], dxfattribs={'layer': 'PIPE'})
    tagged['render'] = m.add_blockref('AVE_RENDER', (5000, 90000), dxfattribs={'layer': 'MISC'})
    tagged['holder'] = m.add_blockref('HOLDER', (10000, 50000), dxfattribs={'layer': 'PIPE'})
    tagged['ghost'] = m.add_blockref('GHOST', (40000, 40000), dxfattribs={'layer': 'MISC'})
    tagged['unit'] = m.add_text('说明：本图坐标按毫米 1:1 绘制', dxfattribs={'insert': (5000, 95000), 'layer': 'NOTE'})
    # insertion point below every frame, drawing inside A1 (world 10000..40000 x 10000..30000)
    tagged['basemap'] = m.add_blockref('BASEMAP', (0, -50000), dxfattribs={'layer': 'BASE'})
    # one line from x 90000 to 110000 at y 20000: across A1 and B1
    tagged['wide'] = m.add_blockref('BASEMAP_W', (0, 20000), dxfattribs={'layer': 'BASE'})
    # control: insertion point inside B1, drawing above it (world y 110000..130000)
    tagged['above'] = m.add_blockref('BASEMAP', (120000, 50000), dxfattribs={'layer': 'BASE'})
    tagged['b1'] = m.add_line((101000, 1000), (150000, 1000), dxfattribs={'layer': 'PIPE'})
    tagged['hsym'] = m.add_blockref('HSYM', (90000, 90000), dxfattribs={'layer': 'HS'})
    tagged['lsym'] = m.add_blockref('LSYM', (91000, 90000), dxfattribs={'layer': 'HS'})
    h = sha(source)
    layout_rows = [extract.convert(e, layout='Model', parent_block=None, source_hash=h, raw_unit='Unitless', sequence=i) for i, e in enumerate(m, 1)]
    block_rows = []
    extracted = ('HOLDER', 'BASEMAP', 'BASEMAP_W', 'HSYM', 'LSYM')
    for name in extracted:
        for i, e in enumerate(doc.blocks.get(name), 1):
            block_rows.append(extract.convert(e, layout='Model', parent_block=name, source_hash=h, raw_unit='Unitless', sequence=i))
    stream = tmp_path / 'layout.ndjson'; stream.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in layout_rows) + '\n', encoding='utf-8')
    blocks = tmp_path / 'blocks.ndjson'; blocks.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in block_rows) + '\n', encoding='utf-8')
    inventory = [dict(block_name=n, origin=[0., 0., 0.], serialized=True, direct_entity_count=len(list(doc.blocks.get(n)))) for n in extracted]
    inventory += [dict(block_name='AVE_RENDER', origin=[0., 0., 0.], serialized=True, direct_entity_count=0),
                  dict(block_name='GHOST', origin=[0., 0., 0.], serialized=False, direct_entity_count=0)]
    manifest = dict(schema_version='qto-cad-neutral-evidence/1.0', geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(source), sha256=h), exchange_dxf=dict(path=str(exchange), sha256=sha(exchange)),
        drawing_unit=dict(insunits_code=0, raw_unit='Unitless'), blocks=inventory,
        artifacts={'layout_entities': dict(path=str(stream), sha256=sha(stream)), 'block_definitions': dict(path=str(blocks), sha256=sha(blocks))})
    manifest_path = put(tmp_path / 'manifest.json', manifest)
    ids = {key: f'CAD-{entity.dxf.handle}' for key, entity in tagged.items()}
    handles = {key: entity.dxf.handle for key, entity in tagged.items()}
    mapping = dict(schema_version='qto-pipe-batch-family-mapping/1.0', mapping_id='MAP', mapping_version='1', expected_source_sha256=h,
        recognized_by='model-under-test', recognized_at='2026-10-17T01:00:00+08:00', source_id='fixture-legend', network_id_prefix='WS',
        evidence=[dict(evidence_id='EV-stream', path='layout.ndjson', sha256=sha(stream), kind='cad_property', locator='model space',
                       observation='PIPE 图层为废水管中心线，BASE 图层 BASEMAP 块为建筑底图')],
        unit=dict(factor_to_output=.001, output_unit='m', source_id='EV-stream', raw_unit='Millimeters',
                  declaration=dict(raw_unit='Millimeters', annotation_object_ids=[ids['unit']], observation='图纸说明写明坐标按毫米绘制',
                                   applies_to='coordinates', source_excerpt='本图坐标按毫米 1:1 绘制', evidence_ids=['EV-stream'])),
        families=[
            dict(family_id='WS-PIPE', kind='layer', layer_pattern='^PIPE$', system='WS', decision='include', evidence_ids=['EV-stream'], basis='图例：PIPE 为废水管', priority=10),
            dict(family_id='X-BASE', kind='block', layer_pattern='^BASE$', block_pattern='^BASEMAP', decision='exclude', evidence_ids=['EV-stream'], basis='建筑底图块，不是管道'),
        ],
        use_conventional_terminal_type_candidates=False, use_conventional_valve_type_candidates=False)
    mapping_path = put(tmp_path / 'mapping.json', mapping)
    frames = put(tmp_path / 'frames.json', dict(schema_version='qto-pipe-batch-frames/1.0', frames=[
        dict(frame_id='A1', name='首层', bounds=A), dict(frame_id='B1', name='二层', bounds=B)]))
    return dict(tmp=tmp_path, manifest=manifest_path, mapping_path=mapping_path, frames=frames, ids=ids, handles=handles)


def run(pkg, *extra):
    out = pkg['tmp'] / 'out'
    if out.exists():
        shutil.rmtree(out)
    p = subprocess.run([sys.executable, '-B', str(BATCH), '--cad-manifest', str(pkg['manifest']), '--frames', str(pkg['frames']),
                        '--mapping', str(pkg['mapping_path']), '--output-dir', str(out), *extra],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    summary = json.loads((out / '批量转换汇总.json').read_text(encoding='utf-8')) if (out / '批量转换汇总.json').exists() else None
    return p, summary, out


def frame(summary, frame_id):
    return next(f for f in summary['frames'] if f['frame_id'] == frame_id)


def report(out, frame_id):
    return json.loads((out / frame_id / '管网识别报告.json').read_text(encoding='utf-8'))


def dispositions(out, frame_id):
    return {d['object_id']: d for d in lines(out / frame_id / '管网中性对象处置清单.jsonl')}


# --- K1 pipe part (20261016-2359): an empty definition is a hint, a missing one still fails ---

def test_empty_block_is_a_hint_and_a_missing_definition_still_fails(package):
    p, summary, out = run(package, '--frame-id', 'A1')
    assert p.returncode == 0, p.stdout + p.stderr
    a = frame(summary, 'A1')
    assert a['block_expansion_failures'] == 1                       # only GHOST (serialized=false)
    assert a['empty_block_definitions'] == {'AVE_RENDER': 2}         # top level and nested in HOLDER
    rep = report(out, 'A1')
    missing = [u for u in rep['unresolved'] if u['kind'] == 'missing_block_definition']
    assert [u.get('block_name') for u in missing] == ['GHOST']
    assert rep['counts']['empty_block_instances'] == 2 and rep['empty_block_definitions'] == {'AVE_RENDER': 2}
    disp = dispositions(out, 'A1')
    render = disp[package['ids']['render']]
    assert render['disposition'] == 'not_applicable' and render['empty_definition_hint'] is True and '空块定义' in render['basis']
    pending = {item['object_id'] for item in lines(out / '待办明细.jsonl')}
    assert package['ids']['render'] not in pending and package['ids']['ghost'] in pending
    # the block around the nested empty definition still expands: its pipe reaches the input
    ia = json.loads((out / 'A1' / '管网推算输入.json').read_text(encoding='utf-8'))
    assert any(s['segment_id'].startswith('SEG-CADINST-') and s['length'] == pytest.approx(5) for s in ia['segments'])


# --- K6 (20261017-0026): zero-length parts are dropped, not fatal; an all-zero object is unresolved ---

def test_repeated_vertex_is_dropped_with_a_diagnostic_and_the_frame_still_infers(package):
    p, summary, out = run(package, '--frame-id', 'A1', '--run-inference')
    assert p.returncode == 0, p.stdout + p.stderr
    assert frame(summary, 'A1')['status'] != 'FAILED'
    rep = report(out, 'A1')
    dropped = {d['source_record_id']: d for d in rep['degenerate_segments']}
    dup = dropped[package['ids']['dup']]
    assert dup['dropped_part_indexes'] == [1] and dup['kept_part_count'] == 2
    assert dup['entity_handle'] == package['handles']['dup'] and '已剔除退化段' in dup['basis']
    assert rep['counts']['degenerate_segments_dropped'] == 1
    ia = json.loads((out / 'A1' / '管网推算输入.json').read_text(encoding='utf-8'))
    seg = next(s for s in ia['segments'] if s['segment_id'] == f"SEG-{package['ids']['dup']}")
    assert seg['length'] == pytest.approx(40)                      # length unchanged: the zero part adds nothing
    assert all(part['start'] != part['end'] for part in seg['geometry']['parts'])
    evidence = json.loads((out / 'A1' / '管网推算证据.json').read_text(encoding='utf-8'))
    assert evidence['metrics']['raw_length'] == pytest.approx(49 + 40 + 5)


def test_object_whose_every_part_has_zero_length_is_unresolved(package):
    p, summary, out = run(package, '--frame-id', 'A1')
    assert p.returncode == 0, p.stdout + p.stderr
    rep = report(out, 'A1')
    zero = [u for u in rep['unresolved'] if u['kind'] == 'degenerate_segment_geometry']
    assert [u['source_record_id'] for u in zero] == [package['ids']['zero']]
    assert zero[0]['entity_handle'] == package['handles']['zero']
    disp = dispositions(out, 'A1')[package['ids']['zero']]
    assert disp['disposition'] == 'unresolved' and '零长度' in disp['basis']
    ia = json.loads((out / 'A1' / '管网推算输入.json').read_text(encoding='utf-8'))
    assert f"SEG-{package['ids']['zero']}" not in {s['segment_id'] for s in ia['segments']}


# --- K6 (20261017-0031 pipe part): family ownership by the transformed definition extent, as scope_of ---

def test_base_map_block_with_far_insertion_point_is_owned_by_the_frame_its_drawing_lies_in(package):
    p, summary, out = run(package)
    assert p.returncode == 0, p.stdout + p.stderr
    a, b = frame(summary, 'A1'), frame(summary, 'B1')
    assert a['family_counts']['X-BASE'] == 2                       # BASEMAP (far anchor) and BASEMAP_W (straddles)
    assert b['family_counts']['X-BASE'] == 1                       # BASEMAP_W only; the control's drawing is outside B1
    owned = (package['ids']['basemap'], package['ids']['wide'])
    pending = lines(out / '待办明细.jsonl')
    assert not any(str(item.get('instance_path') or '').startswith(owned) for item in pending)
    assert not any(item['object_id'] == package['ids']['above'] for item in pending)
    assert a['pending_suppressed_by_owner'] >= 3 and b['pending_suppressed_by_owner'] >= 1
    disp = dispositions(out, 'A1')
    assert disp[package['ids']['basemap']]['disposition'] == 'excluded'
    children = [d for d in disp.values() if str(d.get('instance_path') or '').startswith(package['ids']['basemap'])]
    assert len(children) == 2 and all(d['disposition'] == 'excluded' for d in children)
    assert package['ids']['above'] not in dispositions(out, 'B1')


def test_incomplete_extent_keeps_an_instance_whose_insertion_point_is_in_the_frame(package):
    # K3 guard (decision 10-17): the fill is skipped, so HSYM's extent is incomplete and its insertion point keeps it
    # in A1; LSYM has the same drawing without a fill: its complete extent lies outside every frame
    p, summary, out = run(package)
    assert p.returncode == 0, p.stdout + p.stderr
    pending = {item['object_id']: item for item in lines(out / '待办明细.jsonl')}
    assert pending[package['ids']['hsym']]['frame_id'] == 'A1'
    assert package['ids']['lsym'] not in pending
    disp = dispositions(out, 'A1')
    assert disp[package['ids']['hsym']]['disposition'] == 'unresolved' and package['ids']['lsym'] not in disp
    rows = lines(out / 'A1' / '预扫描图元.ndjson')
    hsym = next(r for r in rows if r.get('record_id') == package['ids']['hsym'])
    assert hsym['scope_extent_notes'] == ['包围盒未含填充'] and hsym['scope_extent_bbox'][1] == pytest.approx(290000)


def test_direct_adapter_run_matches_the_prescoped_run(package):
    p, summary, out = run(package, '--frame-id', 'A1')
    assert p.returncode == 0, p.stdout + p.stderr
    profile = json.loads((out / 'A1' / '识别配置.json').read_text(encoding='utf-8'))
    profile.pop('prescoped_rows')
    direct_profile = put(out / 'A1' / '识别配置_直读.json', profile)
    direct = out / 'A1_direct'
    r = subprocess.run([sys.executable, '-B', str(ADAPTER), '--cad-manifest', str(package['manifest']), '--profile', str(direct_profile),
                        '--adjudication-ledger', str(out / 'A1' / '模型工作识别记录.json'), '--output-dir', str(direct)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stderr
    via_batch = {(d['object_id'], d['disposition']) for d in lines(out / 'A1' / '管网中性对象处置清单.jsonl')}
    via_direct = {(d['object_id'], d['disposition']) for d in lines(direct / '管网中性对象处置清单.jsonl')}
    assert via_batch == via_direct
    direct_report = json.loads((direct / '管网识别报告.json').read_text(encoding='utf-8'))
    assert direct_report['empty_block_definitions'] == {'AVE_RENDER': 2}
    assert [d['source_record_id'] for d in direct_report['degenerate_segments']] == [package['ids']['dup']]
