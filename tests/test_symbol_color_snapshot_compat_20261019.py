"""Merged symbol/colour snapshot compatibility without weakening source checks."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

from test_symbol_geometry_20261019 import (put, request_for, decide, pipe_files,
    test_pipe_batch_cli_uses_same_explicit_object_exclusion as legal_batch)

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT/'skills/_qto-shared-evidence/scripts'
PIPE = ROOT/'skills/qto-pipe-networks/scripts'
sys.path.insert(0, str(SHARED)); sys.path.insert(0, str(PIPE))
import qto_symbol_geometry as symbol
import build_pipe_inference_input as adapter


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding='utf-8', timeout=180,
        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})


def source_case(tmp_path, *, color_schema=True):
    doc = ezdxf.new('R2013'); doc.units = 6
    doc.layers.new('PIPE', dxfattribs={'color': 5})
    leaf = doc.blocks.new('COLOR-LEAF')
    for a, b in zip([(0, 0), (2, 0), (2, 1), (0, 1)], [(2, 0), (2, 1), (0, 1), (0, 0)]):
        leaf.add_line(a, b, dxfattribs={'layer': '0', 'color': 0})
    leaf.add_line((5, 0), (7, 0), dxfattribs={'layer': '0'})
    outer = doc.blocks.new('COLOR-OUTER')
    outer.add_blockref('COLOR-LEAF', (0, 0), dxfattribs={'layer': '0', 'color': 0})
    m = doc.modelspace()
    first = m.add_blockref('COLOR-OUTER', (20, 20), dxfattribs={'layer': 'PIPE'})
    second = m.add_blockref('COLOR-OUTER', (40, 20), dxfattribs={'layer': 'PIPE', 'color': 1})
    m.add_line((0, 20), (10, 20), dxfattribs={'layer': 'PIPE'})
    m.add_arc((0, 40), 2, 0, 270, dxfattribs={'layer': 'PIPE', 'true_color': 0x0a141e})
    source = tmp_path/'匿名颜色嵌套源.dxf'; doc.saveas(source)
    process = run(SHARED/'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', tmp_path/'中性源')
    assert process.returncode == 0, process.stdout+process.stderr
    mp = tmp_path/'中性源/CAD中性取证清单.json'
    if not color_schema:
        manifest = json.loads(mp.read_text(encoding='utf-8')); manifest.pop('color_schema_version')
        put(mp, manifest)
    request = request_for(tmp_path/'request.json', [('A', mp)])
    value = json.loads(request.read_text(encoding='utf-8')); value['legends'] = []
    put(request, value)  # this fixture tests source snapshots, without a legend region
    return mp, request, 'CAD-'+first.dxf.handle, 'CAD-'+second.dxf.handle


@pytest.mark.parametrize('color_schema', [False, True])
def test_full_curve_snapshots_match_with_legacy_or_source_bound_colors(tmp_path, color_schema):
    mp, request, first, second = source_case(tmp_path, color_schema=color_schema)
    source = symbol.load_source('A', mp, symbol.digest(mp), ['Model'])
    objects, canonical_rows, _, _ = symbol.expand_source(source)
    manifest = source['manifest']; artifacts = manifest['artifacts']
    rows = list(adapter.expanded_manifest_rows(Path(artifacts['layout_entities']['path']),
        Path(artifacts['block_definitions']['path']), manifest, 16, [], target_layout='Model'))
    curves = [r for r in rows if r['entity_type'] in symbol.CURVE_TYPES]
    expected = {o['object_id']: o for o in objects}
    assert len(curves) == 12
    for row in curves:
        assert row == canonical_rows[row['record_id']]
        assert symbol.snapshot(row) == expected[row['record_id']]['row_sha256']
        assert row['color_evidence']['status'] == ('resolved' if color_schema else 'unresolved')
    if color_schema:
        first_block = [r for r in curves if r.get('instance_path', '').startswith(first+'/') and r['raw_color']['aci'] == 0]
        second_block = [r for r in curves if r.get('instance_path', '').startswith(second+'/') and r['raw_color']['aci'] == 0]
        assert {r['color_evidence']['effective_color']['value'] for r in first_block} == {5}
        assert {r['color_evidence']['effective_color']['value'] for r in second_block} == {1}
        layer_zero = [r for r in curves if r.get('instance_path', '').startswith(second+'/') and r['raw_color']['aci'] == 256]
        assert len(layer_zero) == 1 and layer_zero[0]['color_evidence']['effective_color'] == {'model': 'aci', 'value': 5}
    report = symbol.build_census(request)
    targets = [r['record_id'] for r in curves if r.get('instance_path')]
    decide(request, report, targets)
    census = put(tmp_path/'report.json', symbol.build_census(request))
    pp, lp = pipe_files(tmp_path, mp, census)
    process = run(PIPE/'build_pipe_inference_input.py', '--cad-manifest', mp, '--profile', pp,
        '--adjudication-ledger', lp, '--output-dir', tmp_path/'实际消费')
    put(tmp_path/'有无颜色契约实际消费日志.json', {'command': process.args, 'returncode': process.returncode,
        'stdout': process.stdout, 'stderr': process.stderr})
    assert process.returncode == 0, process.stdout+process.stderr
    recognized = json.loads((tmp_path/'实际消费/管网识别报告.json').read_text(encoding='utf-8'))
    assert recognized['counts']['symbol_explicitly_excluded_objects'] == 10


@pytest.mark.parametrize('change', ['change_color', 'remove_color'])
def test_rehashed_color_metadata_cannot_renew_a_source_curve_exclusion(tmp_path, change):
    legal_batch(tmp_path)
    frame = tmp_path/'batch/F1'
    profile = json.loads((frame/'识别配置.json').read_text(encoding='utf-8'))
    report = json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    target = report['explicit_dispositions'][0]['object_id']
    index = Path(profile['prescoped_rows']['path'])
    if not index.is_absolute(): index = frame/index
    records = [json.loads(line) for line in index.read_text(encoding='utf-8').splitlines() if line]
    row = next(r for r in records if r.get('record_id') == target)
    if change == 'remove_color': row.pop('color_evidence')
    else:
        row['color_evidence'].update(status='resolved', effective_color={'model': 'aci', 'value': 1}, issues=[])
    edited = tmp_path/'重签颜色索引.ndjson'
    edited.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
    profile['prescoped_rows'] = {'path': str(edited), 'sha256': symbol.digest(edited)}
    pp = put(tmp_path/'重签配置.json', profile)
    process = run(PIPE/'build_pipe_inference_input.py', '--cad-manifest', tmp_path/'a/manifest.json',
        '--profile', pp, '--adjudication-ledger', frame/'模型工作识别记录.json', '--output-dir', tmp_path/'拒收')
    put(tmp_path/'颜色索引重签拒收日志.json', {'command': process.args, 'returncode': process.returncode,
        'stdout': process.stdout, 'stderr': process.stderr})
    assert process.returncode == 2 and 'source snapshot differs' in process.stderr
    assert not (tmp_path/'拒收/管网推算输入.json').exists()


@pytest.mark.parametrize('module', ['qto_symbol_geometry.py', 'qto_cad_geometry.py', 'qto_cad_color.py'])
def test_independent_symbol_report_binds_its_own_producer_dependency_bytes(tmp_path, module):
    mp, request, _, _ = source_case(tmp_path)
    private = tmp_path/'独立普查源码'
    shutil.copytree(SHARED, private, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    report = tmp_path/'独立普查.json'
    create = run(private/'census_cad_symbols.py', '--request', request, '--output', report)
    assert create.returncode == 0, create.stdout+create.stderr
    dependencies = json.loads(report.read_text(encoding='utf-8'))['producer_dependencies']
    assert {r['module'].split('/')[-1] for r in dependencies} == {'qto_symbol_geometry.py', 'qto_cad_geometry.py', 'qto_cad_color.py'}
    verify = ("import sys;sys.path.insert(0,sys.argv[1]);import qto_symbol_geometry as s\n"
              "try:s.verify_report({'path':sys.argv[2],'sha256':s.digest(sys.argv[2])},sys.argv[3])\n"
              "except ValueError as e:print(str(e));raise SystemExit(2)\n")
    before = run('-c', verify, private, report, mp)
    assert before.returncode == 0, before.stdout+before.stderr
    target = private/module
    assert target.resolve().is_relative_to(private.resolve())
    target.write_bytes(target.read_bytes()+b'\n# anonymous producer byte identity control\n')
    after = run('-c', verify, private, report, mp)
    put(tmp_path/'独立普查依赖字节拒收日志.json', {'module': module,
        'create': {'command': create.args, 'returncode': create.returncode, 'stdout': create.stdout, 'stderr': create.stderr},
        'before': {'command': before.args, 'returncode': before.returncode, 'stdout': before.stdout, 'stderr': before.stderr},
        'after': {'command': after.args, 'returncode': after.returncode, 'stdout': after.stdout, 'stderr': after.stderr}})
    assert after.returncode == 2 and 'differs from recomputed source evidence' in after.stdout
