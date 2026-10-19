"""Anonymous colour/material controls, including the real neutral -> duct CLI chain."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT/'skills/_qto-shared-evidence/scripts'
DUCT = ROOT/'skills/qto-duct-air-systems/scripts'
sys.path.insert(0, str(SHARED)); sys.path.insert(0, str(DUCT))
import build_duct_network_from_neutral as builder
from qto_cad_color import resolve_color


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return path
def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding='utf-8', timeout=180,
        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})


def fixture(tmp_path, *, units=4, split=False, insert_override=False, different_curves=False):
    doc = ezdxf.new('R2013'); doc.units = units
    for name, color in [('DUCT-B', 5), ('DUCT-R', 1), ('DUCT-O', 5), ('DUCT-T', 1), ('NOTE', 7)]:
        doc.layers.new(name, dxfattribs={'color': color})
    doc.layers.get('DUCT-T').rgb = (10, 20, 30)
    m = doc.modelspace(); e = {}
    e['blue'] = m.add_line((0, 0), (1000, 0), dxfattribs={'layer': 'DUCT-B'})
    e['unbound'] = m.add_line((0, 2000), (1000, 2000), dxfattribs={'layer': 'DUCT-B'})
    e['true'] = m.add_arc((4000, 0), 1000, 0, 90, dxfattribs={'layer': 'DUCT-R', 'color': 5, 'true_color': 0x0a141e})
    e['layer_true'] = m.add_line((7000, 0), (8000, 0), dxfattribs={'layer': 'DUCT-T'})
    if different_curves:
        e['true_other_size'] = m.add_arc((7000, 5000), 2000, 0, 90, dxfattribs={'layer': 'DUCT-T'})
    e['oa'] = m.add_line((0, 4000), (2000, 4000), dxfattribs={'layer': 'DUCT-O', 'color': 5})
    e['ob'] = m.add_line((0, 4100), (1000 if split else 2000, 4100), dxfattribs={'layer': 'DUCT-O', 'color': 5 if split else 1})
    if split:
        e['oc'] = m.add_line((1000, 4100), (2000, 4100), dxfattribs={'layer': 'DUCT-O', 'color': 1})
    leaf = doc.blocks.new('LEAF')
    e['byblock'] = leaf.add_line((0, 0), (1000, 0), dxfattribs={'layer': '0', 'color': 0})
    e['bylayer'] = leaf.add_line((0, 300), (1000, 300), dxfattribs={'layer': '0'})
    e['fixed_layer'] = leaf.add_line((0, 600), (1000, 600), dxfattribs={'layer': 'DUCT-R'})
    outer = doc.blocks.new('OUTER')
    e['inner'] = outer.add_blockref('LEAF', (0, 0), dxfattribs={'layer': '0', 'color': 0})
    e['ib'] = m.add_blockref('OUTER', (10000, 0), dxfattribs={'layer': 'DUCT-B'})
    if insert_override: e['ib'].dxf.color = 1
    e['ir'] = m.add_blockref('OUTER', (10000, 3000), dxfattribs={'layer': 'DUCT-R'})
    e['blue_note'] = m.add_text('蓝色风管为不锈钢', dxfattribs={'layer': 'NOTE', 'insert': (0, 9000)})
    e['red_note'] = m.add_mtext('ACI1风管材质=铝板', dxfattribs={'layer': 'NOTE', 'insert': (0, 10000)})
    e['rgb_note'] = m.add_text('RGB(10,20,30)风管材质=复合风管', dxfattribs={'layer': 'NOTE', 'insert': (0, 11000)})
    e['bad_note'] = m.add_text('风管系统图例', dxfattribs={'layer': 'NOTE', 'insert': (0, 12000)})
    e['conflict_note'] = m.add_mtext('蓝色风管为不锈钢\\P蓝色风管为镀锌钢板', dxfattribs={'layer': 'NOTE', 'insert': (0, 13000)})
    source = tmp_path/'匿名颜色来源.dxf'; doc.saveas(source)
    extraction = run(SHARED/'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', tmp_path/'中性证据')
    assert extraction.returncode in (0, 3), extraction.stdout+extraction.stderr
    manifest = tmp_path/'中性证据/CAD中性取证清单.json'
    profile = {'schema_version': builder.SCHEMA, 'expected_source_sha256': sha(source), 'layout': 'Model',
        'space_id': 'SPACE', 'floor_id': 'F', 'package_id': 'ANON-COLOR', 'target_layer_pattern': '^DUCT-',
        'parameters': {'parameter_basis': 'anonymous source controls'}, 'rules': []}
    for layer in ['DUCT-B', 'DUCT-R', 'DUCT-O', 'DUCT-T']:
        rid = layer; rule = {'rule_id': rid, 'selector': {'layer_pattern': '^'+layer+'$'},
            'representation': 'outline' if layer == 'DUCT-O' else 'centerline', 'system': 'SYS',
            'section': {'shape': 'circle', 'diameter': 100, 'unit': 'mm'}}
        if layer == 'DUCT-O': rule['pair_tolerance'] = .1
        if different_curves and layer == 'DUCT-T': rule['section']['diameter'] = 200
        evidence = {'schema_version': 'qto-duct-recognition-evidence/1.0', 'source_sha256': sha(source),
            'layout': 'Model', 'space_id': 'SPACE', 'crop': None, 'basis': 'anonymous source classification',
            'source_locator': 'Model layer '+layer, **rule}
        path = write(tmp_path/(rid+'.json'), evidence); rule['evidence'] = {'path': str(path), 'sha256': sha(path)}
        profile['rules'].append(rule)
    pp = write(tmp_path/'识别配置.json', profile)
    c = {'profile': profile, 'pp': pp, 'manifest': manifest, 'source': source, 'e': e}
    return c


def build(c):
    write(c['pp'], c['profile'])
    return builder.build(c['manifest'], c['pp'])[0]


def key(c, name): return 'CAD-'+c['e'][name].dxf.handle
def add_rule(c, rid, targets, note='blue_note', material='不锈钢', color=None):
    row = {'rule_id': rid, 'material': material, 'color': color or {'model': 'aci', 'value': 5},
        'scope': {'source_sha256': sha(c['source']), 'layout': 'Model', 'space_id': 'SPACE', 'crop': None,
                  'source_keys': targets},
        'note': {'source_key': key(c, note), 'expected_text': c['e'][note].text if c['e'][note].dxftype() == 'MTEXT' else c['e'][note].dxf.text}}
    c['profile'].setdefault('color_material_rules', []).append(row)
    return row


def consume(tmp_path, data, name):
    ip = write(tmp_path/(name+'输入.json'), data); op = tmp_path/(name+'结果.json')
    process = run(DUCT/'infer_duct_fittings_vertical.py', '--input', ip, '--output', op)
    write(tmp_path/(name+'命令日志.json'), {'command': process.args, 'returncode': process.returncode,
        'stdout': process.stdout, 'stderr': process.stderr})
    assert process.returncode == 0, process.stdout+process.stderr
    return json.loads(op.read_text(encoding='utf-8'))


def test_real_chain_nested_color_rules_conflicts_and_quantities(tmp_path):
    c = fixture(tmp_path); before = build(c); no_rules = consume(tmp_path, before, '无规则')
    all_rows = before['segments']+before['drawn_curve_segments']
    nested = [r for r in all_rows if '/OUTER/' in r['source_instance_path']]
    assert len(nested) == 6
    for name in ['byblock', 'bylayer']:
        instances = [r for r in nested if r['source_record_id'] == key(c, name)]
        assert {r['color_material']['effective_color']['value'] for r in instances} == {1, 5}
        assert all(r['color_material']['sources'][0]['color_evidence']['trace'] for r in instances)
    fixed = [r for r in nested if r['source_record_id'] == key(c, 'fixed_layer')]
    assert all(r['color_material']['effective_color'] == {'model': 'aci', 'value': 1} for r in fixed)
    blue_targets = [r['source_instance_path'] for r in all_rows if r['color_material']['effective_color'] == {'model': 'aci', 'value': 5} and r['source_record_id'] != key(c, 'unbound')]
    add_rule(c, 'BLUE', blue_targets)
    red_targets = [r['source_instance_path'] for r in all_rows if r['color_material']['effective_color'] == {'model': 'aci', 'value': 1}]
    add_rule(c, 'RED', red_targets, 'red_note', '铝板', {'model': 'aci', 'value': 1})
    add_rule(c, 'RGB', [key(c, 'true'), key(c, 'layer_true')], 'rgb_note', '复合风管', {'model': 'rgb', 'value': [10, 20, 30]})
    data = build(c); out = consume(tmp_path, data, '有规则')
    assert out['metrics'] == no_rules['metrics'] and out['length_summary'] == no_rules['length_summary']
    assert out['theoretical_surface_summary'] == no_rules['theoretical_surface_summary']
    amounts = out['material_quantities']
    blue = next(r for r in amounts if r['source_ids'] == [key(c, 'blue')+'#0'])
    assert blue['material'] == '不锈钢' and blue['length'] == 1 and blue['theoretical_surface_area'] == pytest.approx(math.pi*.1)
    outline = next(r for r in amounts if r['color_material']['color_status'] == 'conflict')
    assert outline['material'] is None and outline['length'] == 2 and outline['classified_length'] is None
    unbound = next(r for r in amounts if r['source_ids'] == [key(c, 'unbound')+'#0'])
    assert unbound['material'] is None and unbound['length'] == 1
    assert out['material_scope']['complete_material_quantity'] is None
    cli = run(DUCT/'build_duct_network_from_neutral.py', '--cad-manifest', c['manifest'], '--profile', c['pp'], '--output-dir', tmp_path/'真实CLI')
    write(tmp_path/'真实CLI日志.json', {'command': cli.args, 'returncode': cli.returncode, 'stdout': cli.stdout, 'stderr': cli.stderr})
    assert cli.returncode == 0, cli.stdout+cli.stderr
    real = json.loads((tmp_path/'真实CLI/风管实际消费结果.json').read_text(encoding='utf-8'))
    assert real['material_quantities'] == amounts


def test_segmented_outline_never_uses_color_majority(tmp_path):
    c = fixture(tmp_path, split=True)
    add_rule(c, 'BLUE', [key(c, 'oa'), key(c, 'ob')])
    data = build(c); out = consume(tmp_path, data, '分段')
    outlines = [r for r in out['material_quantities'] if len(r['source_ids']) == 2]
    assert len(outlines) == 2 and sum(r['length'] for r in outlines) == 2
    assert sorted(r['material'] or '?' for r in outlines) == ['?', '不锈钢']
    assert any(s['source_fraction_range'] for r in outlines for s in r['color_material']['sources'])


@pytest.mark.parametrize('mutation', ['hash', 'scope', 'note', 'mapping', 'self_signed'])
def test_missing_or_forged_legend_not_admitted(tmp_path, mutation):
    c = fixture(tmp_path); rule = add_rule(c, 'BLUE', [key(c, 'blue')])
    if mutation == 'hash': rule['scope']['source_sha256'] = '0'*64
    if mutation == 'scope': rule['scope']['source_keys'] = ['CAD-NOT-PRESENT']
    if mutation == 'note': rule['note']['expected_text'] += '伪造'
    if mutation == 'mapping': rule['material'] = '镀锌钢板'
    if mutation == 'self_signed':
        rule['note'] = {'source_key': key(c, 'bad_note'), 'expected_text': '风管系统图例'}
        rule['evidence'] = {'basis': 'approved', 'material': '不锈钢'}
    with pytest.raises(ValueError): build(c)


def test_conflicting_source_text_and_separate_rules_stay_null(tmp_path):
    c = fixture(tmp_path); add_rule(c, 'BLUE', [key(c, 'blue')], 'conflict_note')
    data = build(c); blue = next(r for r in data['segments'] if r['source_record_id'] == key(c, 'blue'))
    assert blue['color_material']['material_status'] == 'material_rule_conflict'
    assert blue['color_material']['material'] is None
    # The consumer recomputes source bindings; changing just the purported material is rejected.
    blue['color_material']['material'] = '不锈钢'
    ip = write(tmp_path/'篡改输入.json', data); op = tmp_path/'拒收.json'
    result = run(DUCT/'infer_duct_fittings_vertical.py', '--input', ip, '--output', op)
    assert result.returncode == 2 and not op.exists()


@pytest.mark.parametrize('field', ['color', 'text'])
def test_rehashed_neutral_claim_cannot_replace_actual_source(tmp_path, field):
    c = fixture(tmp_path); manifest = json.loads(c['manifest'].read_text(encoding='utf-8'))
    stream = Path(manifest['artifacts']['layout_entities']['path'])
    rows = [json.loads(line) for line in stream.read_text(encoding='utf-8').splitlines()]
    row = next(r for r in rows if r['record_id'] == key(c, 'blue' if field == 'color' else 'blue_note'))
    if field == 'color': row['raw_color']['aci'] = 1
    else: row['text'] = '蓝色风管为铝板'
    stream.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    manifest['artifacts']['layout_entities']['sha256'] = sha(stream); write(c['manifest'], manifest)
    with pytest.raises(ValueError, match='实际冻结DXF'): build(c)


def test_missing_colors_and_parent_never_default_to_material():
    row = {'record_id': 'CAD-X', 'source_sha256': 'a'*64, 'layer': '0', 'raw_color': {'aci': 0, 'true_color': None}}
    evidence = resolve_color(row, [])
    assert evidence['status'] == 'unresolved' and evidence['effective_color'] is None
    row.pop('raw_color'); evidence = resolve_color(row, [])
    assert evidence['status'] == 'unresolved'
    row['raw_color'] = {'aci': 256, 'true_color': None}; evidence = resolve_color(row, [])
    assert evidence['effective_color'] is None and 'layer_color_missing_or_ambiguous' in evidence['issues']
    row['raw_color'] = {'aci': 0, 'true_color': 0x010203}; evidence = resolve_color(row, [])
    assert evidence['effective_color'] == {'model': 'rgb', 'value': [1, 2, 3]}


def test_unknown_unit_keeps_raw_quantities_unassigned_and_null_metre_total(tmp_path):
    c = fixture(tmp_path, units=0); add_rule(c, 'BLUE', [key(c, 'blue')])
    data = build(c); out = consume(tmp_path, data, '未知单位')
    assert out['metrics']['straight_length'] is None
    assert all(r['length'] is None and r['theoretical_surface_area'] is None for r in out['material_quantities'])
    assert all(g['length'] is None for g in out['material_summary'])


def test_layer_zero_bylayer_is_independent_of_parent_explicit_color(tmp_path):
    c = fixture(tmp_path, insert_override=True); data = build(c)
    nested = [r for r in data['segments'] if r['source_instance_path'].startswith(key(c, 'ib')+'/')]
    byblock = next(r for r in nested if r['source_record_id'] == key(c, 'byblock'))
    bylayer = next(r for r in nested if r['source_record_id'] == key(c, 'bylayer'))
    assert byblock['color_material']['effective_color'] == {'model': 'aci', 'value': 1}
    assert bylayer['color_material']['effective_color'] == {'model': 'aci', 'value': 5}


def test_legacy_cache_without_colors_remains_unknown(tmp_path):
    c = fixture(tmp_path); manifest = json.loads(c['manifest'].read_text(encoding='utf-8'))
    manifest.pop('color_schema_version'); manifest.pop('layer_colors')
    for artifact in ['layout_entities', 'block_definitions']:
        path = Path(manifest['artifacts'][artifact]['path'])
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        for row in rows: row.pop('raw_color')
        path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
        manifest['artifacts'][artifact]['sha256'] = sha(path)
    write(c['manifest'], manifest)
    data = build(c)
    assert all(r['color_material']['effective_color'] is None and r['color_material']['material'] is None
        for r in data['segments']+data['drawn_curve_segments'])
    add_rule(c, 'BLUE', [key(c, 'blue')])
    with pytest.raises(ValueError, match='实际DXF文字'): build(c)


def test_direct_consumer_cannot_self_sign_material_classification(tmp_path):
    c = fixture(tmp_path); add_rule(c, 'BLUE', [key(c, 'blue')]); data = build(c)
    data.pop('neutral_binding')
    ip = write(tmp_path/'无源自签输入.json', data); op = tmp_path/'拒收自签.json'
    process = run(DUCT/'infer_duct_fittings_vertical.py', '--input', ip, '--output', op)
    assert process.returncode == 2 and not op.exists() and '拒绝自签输入' in process.stderr


def test_known_different_curve_specs_remain_separate_material_groups(tmp_path):
    c = fixture(tmp_path, different_curves=True)
    add_rule(c, 'RGB', [key(c, 'true'), key(c, 'true_other_size')], 'rgb_note', '复合风管', {'model': 'rgb', 'value': [10, 20, 30]})
    out = consume(tmp_path, build(c), '不同规格曲线')
    curves = [r for r in out['material_quantities'] if r['quantity_scope'] == 'drawn_curve']
    assert {r['spec'] for r in curves} == {'D100', 'D200'}
    groups = [r for r in out['material_summary'] if r['material'] == '复合风管']
    assert {r['spec'] for r in groups} == {'D100', 'D200'} and len(groups) == 2
    assert sorted(r['length'] for r in curves) == pytest.approx([math.pi/2, math.pi])
    assert sorted(r['theoretical_surface_area'] for r in curves) == pytest.approx([math.pi**2*.05, math.pi**2*.2])


def test_removing_color_schema_cannot_resolve_rehashed_forged_raw_color(tmp_path):
    c = fixture(tmp_path); manifest = json.loads(c['manifest'].read_text(encoding='utf-8'))
    stream = Path(manifest['artifacts']['layout_entities']['path'])
    rows = [json.loads(line) for line in stream.read_text(encoding='utf-8').splitlines()]
    blue = next(r for r in rows if r['record_id'] == key(c, 'blue'))
    blue['raw_color'] = {'aci': 1, 'aci_explicit': True, 'true_color': None, 'color_name': None}
    stream.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    manifest['artifacts']['layout_entities']['sha256'] = sha(stream); write(c['manifest'], manifest)
    with pytest.raises(ValueError, match='实际冻结DXF'): build(c)
    manifest.pop('color_schema_version'); write(c['manifest'], manifest)
    process = run(DUCT/'build_duct_network_from_neutral.py', '--cad-manifest', c['manifest'],
        '--profile', c['pp'], '--output-dir', tmp_path/'无颜色契约兼容CLI')
    write(tmp_path/'无颜色契约兼容CLI日志.json', {'command': process.args, 'returncode': process.returncode,
        'stdout': process.stdout, 'stderr': process.stderr})
    assert process.returncode == 0, process.stdout+process.stderr
    result = json.loads((tmp_path/'无颜色契约兼容CLI/风管实际消费结果.json').read_text(encoding='utf-8'))
    assert all(r['color_material']['effective_color'] is None and r['material'] is None
        for r in result['material_quantities'])
    actual = next(r for r in result['material_quantities'] if r['source_ids'] == [key(c, 'blue')+'#0'])
    evidence = actual['color_material']['sources'][0]['color_evidence']
    assert evidence['status'] == 'unresolved' and 'unverified_color_schema' in evidence['issues']
    assert actual['length'] == 1  # legacy geometry remains usable; forged colour never becomes resolved


@pytest.mark.parametrize('module', ['_qto-shared-evidence/scripts/qto_cad_color.py',
                                  'qto-duct-air-systems/scripts/duct_color_material.py'])
def test_actual_inline_receipt_invalidates_on_color_dependency_bytes(tmp_path, module):
    from test_duct_batch_network_t28_t20_20261019 import duct_case
    c, report_dir, ip, _ = duct_case(tmp_path)
    private = tmp_path/'独立受测源码'
    shutil.copytree(ROOT/'skills', private/'skills', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    private_duct = private/'skills/qto-duct-air-systems/scripts'
    private_point = private/'skills/qto-components-points/scripts'
    received = tmp_path/'实际接收'
    process = run(private_duct/'receive_duct_inline_components.py', '--attachment-report',
        report_dir/'点位位置与管段挂接.json', '--network-input', ip, '--output-dir', received)
    assert process.returncode == 0, process.stdout+process.stderr
    receipt_path = received/'风管在线附件数量接收.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    dependencies = {Path(r['path']).name for r in receipt['producer_dependencies']}
    assert {'qto_cad_color.py', 'duct_color_material.py'} <= dependencies
    c['profile']['point_host_attachment']['receptions'] = [{'path': str(receipt_path), 'sha256': sha(receipt_path)}]
    pp = write(tmp_path/'含真实接收配置.json', c['profile'])
    command = [private_point/'build_component_instances_from_neutral.py', '--cad-manifest',
        c['tmp']/'neutral/CAD中性取证清单.json', '--profile', pp, '--adjudication-ledger', c['lp']]
    before = run(*command, '--output-dir', tmp_path/'变更前重放')
    assert before.returncode == 0, before.stdout+before.stderr
    target = private/'skills'/module
    assert target.resolve().is_relative_to(private.resolve()) and not target.is_symlink()
    target.write_bytes(target.read_bytes()+b'\n# anonymous dependency byte identity control\n')
    after = run(*command, '--output-dir', tmp_path/'变更后拒收')
    write(tmp_path/'颜色模块字节变更重放日志.json', {'module': str(target), 'receipt': str(receipt_path),
        'receipt_creation': {'command': process.args, 'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr},
        'before': {'command': before.args, 'returncode': before.returncode, 'stdout': before.stdout, 'stderr': before.stderr},
        'after': {'command': after.args, 'returncode': after.returncode, 'stdout': after.stdout, 'stderr': after.stderr}})
    assert after.returncode == 2 and '依赖已变化' in after.stderr
