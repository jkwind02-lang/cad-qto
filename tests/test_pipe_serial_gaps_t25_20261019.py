"""Anonymous T25 counterexamples and legal controls through the real CLIs."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / 'skills/qto-pipe-networks/scripts'
ENGINE = PIPE / 'infer_pipe_network.py'
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


def put(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def command(script, *args):
    return subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
                          stdin=subprocess.DEVNULL, env=ENV, capture_output=True, text=True, encoding='utf-8')


def network(gap=1.132, accessories=True):
    segments = [dict(segment_id='LEFT', system='W', start=[0, 0], end=[1, 0], source_ids=['EV-L'],
                     dn_seeds=[dict(dn='DN80', evidence_level='confirmed', source_id='EV-DN')]),
                dict(segment_id='RIGHT', system='W', start=[1+gap, 0], end=[3+gap, 0], source_ids=['EV-R'], dn_seeds=[])]
    data = dict(schema_version='qto-pipe-network-inference-input/1.2', network_id='ANONYMOUS-T25', unit='m',
                parameters=dict(snap_tolerance=.001, gap_max=.3), segments=segments,
                object_selection=dict(audit_id='ANONYMOUS-AUDIT', status='READY', candidate_count=2, included_count=2, excluded_count=0,
                                      unresolved_count=0, included_segment_ids=['LEFT', 'RIGHT'], source_ids=['EV-SELECTION']),
                valves=[], endpoint_handoff=dict(handoff_id='ANONYMOUS-NO-POINTS', required=False,
                    source_instance_count=0, delivered_count=0, not_applicable_count=0, unresolved_count=0,
                    source_id='EV-NO-POINTS', reason='匿名管道连续性实例，不含设备点位'))
    if accessories:
        cuts = [1, 1+gap/3, 1+2*gap/3, 1+gap]
        for i, (lo, hi) in enumerate(zip(cuts, cuts[1:]), 1):
            data['valves'].append(dict(valve_id=f'V{i}', source_object_id=f'OBJ-{i}', source_id=f'EV-V{i}',
                                       system='W', point=[(lo+hi)/2, 0], instance_level='inferred',
                                       coverage_bounds=[lo, -.1, 0, hi, .1, 0],
                                       coverage_geometry=dict(complete_source_outline=True,point_candidates=[],shapes=[dict(kind='closed_polygon',source_shape_id=f'OUTLINE-{i}',
                                           vertices=[[lo,-.1,0],[hi,-.1,0],[hi,.1,0],[lo,.1,0]])]),
                                       coverage_basis='transformed_instance_definition_extent'))
    return data


def run(tmp_path, data, name='case'):
    src, out = put(tmp_path / f'{name}.json', data), tmp_path / f'{name}.out.json'
    completed = command(ENGINE, '--input', src, '--output', out)
    result = json.loads(out.read_text(encoding='utf-8')) if out.is_file() else None
    return completed, result, src, out


def success(tmp_path, data, name='case'):
    completed, result, _, _ = run(tmp_path, data, name)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    return result


def test_three_instances_continuously_cover_long_gap_without_adding_pipe(tmp_path):
    result = success(tmp_path, network())
    assert len(result['topology_bridges']) == 1
    bridge = result['topology_bridges'][0]
    assert bridge['virtual_length'] == pytest.approx(1.132)
    assert bridge['coverage_evidence']['complete'] is True
    assert bridge['occupying_source_object_ids'] == ['OBJ-1', 'OBJ-2', 'OBJ-3']
    assert len(bridge['coverage_evidence']['instances']) == 3
    assert bridge['scenario_include'] is False and bridge['quantity_included'] is False
    assert result['metrics']['raw_length'] == pytest.approx(3)
    assert result['reconstructed_length'] == pytest.approx(3)
    assert sum(s['length'] for s in result['segments']) == pytest.approx(3)
    assert all(v['connection_method'] == 'unique_topology_bridge' and v['dn'] == 'DN80' for v in result['valves'])


def test_incomplete_chain_is_numeric_pending_without_fabricated_continuity(tmp_path):
    data = network()
    data['valves'][1]['coverage_bounds'][0] += .05
    for p in data['valves'][1]['coverage_geometry']['shapes'][0]['vertices']:
        if p[0] < 1+2*1.132/3-1e-8:p[0] += .05
    result = success(tmp_path, data)
    assert result['topology_bridges'] == []
    pending = result['rejected_gap_candidates'][0]
    assert pending['reason'] == 'incomplete_serial_axis_coverage'
    assert pending['conditional_incremental_length'] == pytest.approx(1.132)
    assert pending['coverage_evidence']['uncovered_axis_length'] == pytest.approx(.05)
    assert pending['quantity_included'] is False
    assert result['reconstructed_length'] == pytest.approx(3)
    summary = next(tmp_path.glob('*复核摘要.md')).read_text(encoding='utf-8')
    assert '串联附件连接待核候选' in summary and 'OBJ-2' in summary and '未建立闭合拓扑桥' in summary


def test_explicit_project_inclusive_axis_has_separate_layered_outlet(tmp_path):
    data = network()
    data['gap_classifications'] = [dict(a_segment='LEFT', a_end='end', b_segment='RIGHT', b_end='start',
        classification='已核对串联附件含轴长口径', quantity_decision='项目计量不扣附件轴长',
        quantity_action='include_occupied_axis', quantity_evidence_level='inferred',
        quantity_basis='匿名计量条款第1条：中心线不扣此类附件长度', source_id='EV-CLAUSE')]
    result = success(tmp_path, data)
    item = result['bridge_quantity_items'][0]
    assert item['included_in_reconstructed'] is True and item['quantity_level'] == '方案暂估量'
    assert result['metrics']['raw_length'] == pytest.approx(3)
    assert result['reconstructed_length'] == pytest.approx(4.132)
    # Same data with ordinary omission restoration must still stop at occupancy.
    data['gap_classifications'][0]['quantity_action'] = 'restore_omission'
    result = success(tmp_path, data, 'occupied')
    assert result['bridge_quantity_items'][0]['status'] == 'BLOCKED_OCCUPANCY'
    assert result['reconstructed_length'] == pytest.approx(3)


@pytest.mark.parametrize('gap', [.516, .675])
def test_unknown_dn_side_without_any_block_gets_scenario_bridge(tmp_path, gap):
    result = success(tmp_path, network(gap, False))
    assert len(result['topology_bridges']) == 1
    bridge = result['topology_bridges'][0]
    assert bridge['classification'] == '共线断口候选' and bridge['quantity_action'] == 'scenario'
    assert result['metrics']['raw_length'] == pytest.approx(3)
    assert result['reconstructed_length'] == pytest.approx(3)
    assert result['metrics']['virtual_bridge_length'] == pytest.approx(gap)
    assert {s['dn'] for s in result['segments']} == {'DN80'}
    assert result['metrics']['run_count'] == 1


def test_no_seed_stays_null_and_not_zero(tmp_path):
    data = network(.516, False); data['segments'][0]['dn_seeds'] = []
    result = success(tmp_path, data)
    assert len(result['topology_bridges']) == 1
    assert all(s['dn'] is None for s in result['segments'])
    assert result['bridge_quantity_items'][0]['dn'] is None


def test_bridge_does_not_create_false_terminal_before_real_endpoint(tmp_path):
    data = network(.675, False)
    data['roots'] = [dict(root_id='SOURCE',system='W',point=[0,0],source_id='EV-SOURCE')]
    data['terminals'] = [dict(terminal_id='ACTUAL-END',system='W',point=[3.675,0],source_id='EV-END',
                              terminal_type='匿名实际末端',evidence_level='confirmed',port_dn='DN80')]
    data['endpoint_handoff'].update(source_instance_count=1,delivered_count=1)
    result = success(tmp_path,data)
    assert len(result['terminals']) == 1
    segments = {s['segment_id']:s for s in result['segments']}
    assert segments['LEFT']['role'] == '支管'
    assert segments['RIGHT']['role'] == '末端连接'
    assert all(s['downstream_terminal_count'] == 1 for s in result['segments'])


def test_unknown_physical_scale_does_not_assume_one_meter(tmp_path):
    data = network(.675,False);data['unit']='drawing_units'
    result = success(tmp_path,data)
    assert result['topology_bridges'] == []
    assert result['unit'] == 'drawing_units'


def test_known_dn_on_unbroken_upstream_chain_blocks_conflicting_bridge(tmp_path):
    data = network(.675,False)
    data['segments'][0]['dn_seeds']=[]
    data['segments'][1]['dn_seeds']=[dict(dn='DN100',evidence_level='confirmed',source_id='EV-DN100')]
    data['segments'].append(dict(segment_id='UPSTREAM',system='W',start=[-1,0],end=[0,0],source_ids=['EV-U'],
        dn_seeds=[dict(dn='DN80',evidence_level='confirmed',source_id='EV-DN80')]))
    data['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['LEFT','RIGHT','UPSTREAM'])
    result=success(tmp_path,data)
    assert result['topology_bridges']==[]
    assert any(r['reason']=='known_dn_conflict' for r in result['rejected_gap_candidates'])


@pytest.mark.parametrize('change', ['dn', 'space', 'system', 'sideways', 'away', 'single_symbol', 'nested_symbol', 'uncovered', 'branch', 'drawn_overlap', 'multiple_target'])
def test_counterexamples_do_not_create_long_bridge(tmp_path, change):
    data = network()
    if change == 'dn':
        data['segments'][1]['dn_seeds'] = [dict(dn='DN100', evidence_level='confirmed', source_id='EV-OTHER-DN')]
    elif change == 'space':
        data['segments'][1]['space_id'] = 'ANOTHER-SPACE'
    elif change == 'system':
        data['segments'][1]['system'] = 'OTHER'
    elif change == 'sideways':
        for key in ('start', 'end'): data['segments'][1][key][1] = .03
    elif change == 'away':
        data['segments'][1]['start'], data['segments'][1]['end'] = [2.132, 0], [1.5, 0]
    elif change == 'single_symbol':
        data['valves'] = data['valves'][:1]
        data['valves'][0]['coverage_bounds'][3] = 2.132
    elif change == 'nested_symbol':
        data['valves'] = data['valves'][:2]
        data['valves'][0]['coverage_bounds'][3] = 2.132
    elif change == 'uncovered':
        data['valves'] = []
    else:
        if change == 'branch':
            seg = dict(segment_id='EXTRA', system='W', start=[1.5, -.5], end=[1.5, .5], source_ids=['EV-BRANCH'], dn_seeds=[])
        elif change == 'drawn_overlap':
            seg = dict(segment_id='EXTRA', system='W', start=[.5, 0], end=[3, 0], source_ids=['EV-OVERLAP'], dn_seeds=[])
        else:
            seg = dict(segment_id='EXTRA', system='W', start=[1.9, 0], end=[1.95, 0], source_ids=['EV-MULTI'], dn_seeds=[])
        data['segments'].append(seg)
        data['object_selection'].update(candidate_count=3, included_count=3, included_segment_ids=['LEFT', 'RIGHT', 'EXTRA'])
    result = success(tmp_path, data)
    assert not any(b['virtual_length'] > 1 for b in result['topology_bridges'])
    assert result['reconstructed_length'] == pytest.approx(result['metrics']['raw_length'])


def test_duplicate_instance_and_enlarged_coverage_tolerance_fail_cli(tmp_path):
    for name, data in [('duplicate', network()), ('tolerance', network())]:
        if name == 'duplicate': data['valves'][1]['source_object_id'] = 'OBJ-1'
        else: data['parameters']['accessory_coverage_tolerance'] = .5
        p, result, _, _ = run(tmp_path, data, name)
        assert p.returncode == 2 and result is None


@pytest.mark.parametrize('protected', [False, True])
def test_engine_does_not_overwrite_input_or_source_hardlink(tmp_path, protected):
    data = network(.516, False)
    original = tmp_path / '原始证据.json'
    if protected:
        original.write_text('原始来源不得写入', encoding='utf-8')
        data['protected_source_paths'] = [str(original)]
        source = put(tmp_path / '输入.json', data)
    else:
        source = put(original, data)
    alias = tmp_path / '输出.json'; os.link(original, alias)
    before = original.read_bytes()
    p = command(ENGINE, '--input', source, '--output', alias)
    assert p.returncode == 2 and '别名冲突' in p.stderr
    assert original.read_bytes() == before


def test_engine_summary_alias_is_checked_before_writing_json(tmp_path):
    data = network(.516, False)
    _, _, source, target = run(tmp_path, data)
    summary = next(tmp_path.glob('*复核摘要.md'))
    protected = tmp_path / '外部证据.txt'
    os.link(summary, protected)
    data['protected_source_paths'] = [str(protected)]
    put(source, data)
    before = target.read_bytes()
    p = command(ENGINE, '--input', source, '--output', target)
    assert p.returncode == 2 and '别名冲突' in p.stderr
    assert target.read_bytes() == before


def test_engine_rejects_source_alias_through_directory_junction(tmp_path):
    real = tmp_path/'真实目录';real.mkdir()
    alias = tmp_path/'目录别名'
    if os.name == 'nt':
        p = subprocess.run(['cmd','/c','mklink','/J',str(alias),str(real)],stdin=subprocess.DEVNULL,
                           capture_output=True,env=ENV)
        assert p.returncode == 0, p.stderr
    else:
        alias.symlink_to(real,target_is_directory=True)
    source=put(real/'输入.json',network(.516,False));before=source.read_bytes()
    p=command(ENGINE,'--input',source,'--output',alias/'输入.json')
    assert p.returncode==2 and '别名冲突' in p.stderr
    assert source.read_bytes()==before


def test_batch_and_single_entry_keep_instance_coverage_and_old_contract(tmp_path):
    # Real neutral CAD -> batch -> existing single adapter -> infer CLI.
    import ezdxf
    sys.path.insert(0, str(ROOT/'skills/_qto-shared-evidence/scripts'))
    import extract_cad_neutral as extract
    source = tmp_path/'source.dwg'; source.write_bytes(b'anonymous-serial-drawing')
    exchange = tmp_path/'source.dxf'; exchange.write_bytes(b'anonymous-serial-exchange')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    doc = ezdxf.new(); doc.units = 4; m = doc.modelspace()
    block = doc.blocks.new('ACCESSORY'); block.add_lwpolyline([(-200,-50),(200,-50),(200,50),(-200,50)], close=True)
    m.add_line((0,1000),(1000,1000),dxfattribs={'layer':'PIPE'})
    m.add_line((2200,1000),(4000,1000),dxfattribs={'layer':'PIPE'})
    for x in (1200,1600,2000): m.add_blockref('ACCESSORY',(x,1000),dxfattribs={'layer':'VALVE'})
    rows = [extract.convert(e, layout='Model', parent_block=None, source_hash=sha(source), raw_unit='Millimeters', sequence=i) for i,e in enumerate(m)]
    definitions = [extract.convert(e, layout='Model', parent_block='ACCESSORY', source_hash=sha(source), raw_unit='Millimeters', sequence=i) for i,e in enumerate(block)]
    stream, blocks = tmp_path/'layout.ndjson', tmp_path/'blocks.ndjson'
    stream.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    blocks.write_text(''.join(json.dumps(r)+'\n' for r in definitions),encoding='utf-8')
    manifest = put(tmp_path/'manifest.json',dict(schema_version='qto-cad-neutral-evidence/1.0', geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(source),sha256=sha(source)),exchange_dxf=dict(path=str(exchange),sha256=sha(exchange)),
        drawing_unit=dict(insunits_code=4,raw_unit='Millimeters'),blocks=[dict(block_name='ACCESSORY',origin=[0,0,0])],
        artifacts=dict(layout_entities=dict(path=str(stream),sha256=sha(stream)),block_definitions=dict(path=str(blocks),sha256=sha(blocks)))))
    mapping = put(tmp_path/'mapping.json',dict(schema_version='qto-pipe-batch-family-mapping/1.0',mapping_id='MAP',mapping_version='1',
        expected_source_sha256=sha(source),recognized_by='model-fixture',recognized_at='2026-10-19',source_id='EV-LEGEND',
        evidence=[dict(evidence_id='EV-STREAM',path=str(stream),sha256=sha(stream),kind='cad_property',locator='Model',observation='匿名图例：PIPE管道、VALVE在线附件')],
        unit=dict(factor_to_output=.001,output_unit='m',source_id='EV-STREAM',raw_unit='Millimeters'),
        families=[dict(family_id='P',kind='layer',layer_pattern='^PIPE$',system='W',decision='include',evidence_ids=['EV-STREAM'],basis='匿名图例管道'),
                  dict(family_id='V',kind='block',layer_pattern='^VALVE$',block_pattern='^ACCESSORY$',system='W',decision='include',evidence_ids=['EV-STREAM'],basis='匿名图例在线附件')]))
    frames = put(tmp_path/'frames.json',dict(schema_version='qto-pipe-batch-frames/1.0',frames=[dict(frame_id='F',bounds=[-100,-100,5000,2000])]))
    output = tmp_path/'batch'
    p = command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',manifest,'--mapping',mapping,'--frames',frames,'--output-dir',output,'--run-inference')
    assert p.returncode == 0, p.stdout+p.stderr
    result = json.loads((output/'F/管网推算证据.json').read_text(encoding='utf-8'))
    assert len(result['topology_bridges']) == 1
    assert len(result['topology_bridges'][0]['coverage_evidence']['instances']) == 3
    assert result['metrics']['raw_length'] == pytest.approx(2.8)
    single = tmp_path/'single'
    p = command(PIPE/'build_pipe_inference_input.py','--cad-manifest',manifest,'--profile',output/'F/识别配置.json',
        '--adjudication-ledger',output/'F/模型工作识别记录.json','--output-dir',single,'--inference-output',single/'证据.json')
    assert p.returncode == 0, p.stdout+p.stderr
    assert json.loads((single/'证据.json').read_text(encoding='utf-8'))['topology_bridges'][0]['coverage_evidence']['complete'] is True
    # A hardlink from a dynamic batch output to source evidence is rejected before writes.
    alias_dir = tmp_path/'alias_batch'; alias_dir.mkdir()
    os.link(stream,alias_dir/'批量转换汇总.json')
    before = stream.read_bytes()
    p = command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',manifest,'--mapping',mapping,'--frames',frames,'--output-dir',alias_dir)
    assert p.returncode == 2 and stream.read_bytes() == before
    alias_single=tmp_path/'alias_single';alias_single.mkdir()
    os.link(stream,alias_single/'管网推算输入.json')
    p=command(PIPE/'build_pipe_inference_input.py','--cad-manifest',manifest,'--profile',output/'F/识别配置.json',
        '--adjudication-ledger',output/'F/模型工作识别记录.json','--output-dir',alias_single)
    assert p.returncode==2 and stream.read_bytes()==before
