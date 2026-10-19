"""T23: source-bound explicit seeds, ordered full leaders and multiline candidates."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest
from test_pipe_source_scope_contracts import bundle, run_bundle, put, sha
from test_pipe_batch_frames_20261015 import package, run as run_batch

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / 'skills/qto-pipe-networks/scripts'
sys.path.insert(0, str(PIPE))
from pipe_callout_stacks import bind_stack, targets

DUCK = ROOT / 'skills/qto-duct-air-systems/scripts/build_duct_multiline_callout_candidates.py'
spec = importlib.util.spec_from_file_location('duct_multiline_t23', DUCK)
duct = importlib.util.module_from_spec(spec); spec.loader.exec_module(duct)


def mutate_bundle(bundle, change):
    folder, manifest, profile, ledger_path, *_ = bundle
    path = Path(manifest['artifacts']['layout_entities']['path'])
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    change(rows, profile, ledger)
    path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    manifest['artifacts']['layout_entities']['sha256'] = sha(path)
    ledger['evidence'][0]['sha256'] = sha(path)
    put(ledger_path, ledger)
    profile['adjudication_ledger']['sha256'] = sha(ledger_path)


def duplicate_pipe(rows, profile, ledger):
    second = copy.deepcopy(rows[0]); second['record_id'] = 'CAD-SECOND'
    for key in ('start', 'end'):
        second[key][1] += 400
    if second.get('geometry'):
        for p in second['geometry']['parts']:
            for key in ('start', 'end'):
                p[key][1] += 400
    second['bbox'] = [0,400,10000,400]
    rows.append(second)
    for entry in ledger['entries']:
        entry['object_ids'].append(second['record_id'])
    oid = rows[1]['record_id']
    profile['text_rules'][0]['segment_bindings'][oid] = ['SEG-'+rows[0]['record_id'], 'SEG-CAD-SECOND']


def test_explicit_one_text_many_segments_runs_real_adapter_and_inference(bundle):
    mutate_bundle(bundle, duplicate_pipe)
    p, result = run_bundle(bundle)
    assert p.returncode == 0, p.stderr
    assert result['length_summary']['W']['规则推算量']['DN80'] == 20
    assert result['metrics']['dn_match_seed_count'] == 2
    assert result['recognition_basis']['review_status'] == 'unreviewed'
    assert result['recognition_basis']['formal_adoption_allowed'] is False


@pytest.mark.parametrize('fault', ['duplicate', 'outside', 'source', 'space', 'conflict', 'unconsumed'])
def test_many_binding_blocks_invalid_scope_and_conflicts(bundle, fault):
    def change(rows, profile, ledger):
        duplicate_pipe(rows, profile, ledger)
        rule = profile['text_rules'][0]
        oid = rows[1]['record_id']
        if fault == 'duplicate': rule['segment_bindings'][oid].append(rule['segment_bindings'][oid][0])
        if fault == 'outside': rule['segment_bindings'][oid].append('SEG-CAD-ABSENT')
        if fault == 'source': rows[-1]['source_sha256'] = 'e'*64
        if fault == 'space': rows[1]['space_id'] = 'OTHER'
        if fault == 'unconsumed': rule['segment_bindings']['CAD-ABSENT'] = rule['segment_bindings'][oid]
        if fault == 'conflict':
            other = copy.deepcopy(rows[1]); other['record_id'] = 'CAD-CONFLICT'; other['text'] = 'DN100'
            rows.append(other)
            for entry in ledger['entries']: entry['object_ids'].append('CAD-CONFLICT')
            rule['segment_bindings']['CAD-CONFLICT'] = rule['segment_bindings'][oid][0]
    mutate_bundle(bundle, change)
    p, result = run_bundle(bundle)
    assert p.returncode == 2 and result is None, p.stdout + p.stderr


def test_unbound_text_stays_scenario_even_under_seed_rule(bundle):
    def change(rows, profile, ledger):
        other = copy.deepcopy(rows[1]); other['record_id'] = 'CAD-UNBOUND'; other['text'] = 'DN80'
        rows.append(other)
        ledger['entries'][1]['object_ids'].append(other['record_id'])
    mutate_bundle(bundle, change)
    p, result = run_bundle(bundle)
    assert p.returncode == 0, p.stderr
    candidates = result['dn_match_candidates']
    assert next(c for c in candidates if c['source_id'] == 'EV-CAD-CAD-UNBOUND')['disposition'] == 'scenario'


def cross_system_pipe(rows, profile, ledger):
    duplicate_pipe(rows, profile, ledger)
    rows[-1]['layer'] = 'PIPE_RAIN'
    other_entry = copy.deepcopy(ledger['entries'][0])
    other_entry.update(adjudication_id='RAIN', subject_id='RAIN', object_ids=['CAD-SECOND'])
    ledger['entries'].append(other_entry)
    profile['layer_rules'].append(dict(rule_id='RAIN', adjudication_id='RAIN', layer_pattern='^PIPE_RAIN$', system='RAIN', decision='include', allowed_entity_types=['LINE']))


@pytest.mark.parametrize('proof', ['valid', 'missing', 'wrong_source', 'missing_target'])
def test_shared_label_can_seed_different_systems_only_with_each_target_evidence(bundle, proof):
    def change(rows, profile, ledger):
        cross_system_pipe(rows, profile, ledger)
        oid = rows[1]['record_id']; rule = profile['text_rules'][0]
        context = [dict(segment_id='SEG-'+rows[0]['record_id'], system='W', space_id='F1', source_sha256=rows[0]['source_sha256'],
            basis='drawing shows the common DN on the wet pipe', evidence_ids=['E']),
            dict(segment_id='SEG-CAD-SECOND', system='RAIN', space_id='F1', source_sha256=rows[0]['source_sha256'],
            basis='drawing shows the same common DN on the separate deluge pipe', evidence_ids=['E'])]
        if proof == 'wrong_source': context[1]['source_sha256'] = 'f'*64
        if proof == 'missing_target': context.pop()
        if proof != 'missing': rule['shared_annotation_targets'] = {oid: context}
    mutate_bundle(bundle, change)
    p, result = run_bundle(bundle)
    if proof != 'valid':
        assert p.returncode == 2 and result is None
    else:
        assert p.returncode == 0, p.stderr
        assert result['length_summary']['W']['规则推算量']['DN80'] == 10
        assert result['length_summary']['RAIN']['规则推算量']['DN80'] == 10
        assert result['metrics']['dn_match_seed_count'] == 2


def test_cross_system_nearby_pipe_never_gets_seed_by_propagation(bundle):
    def change(rows, profile, ledger):
        cross_system_pipe(rows, profile, ledger)
        profile['text_rules'][0]['segment_bindings'][rows[1]['record_id']] = 'SEG-'+rows[0]['record_id']
    mutate_bundle(bundle, change)
    p, result = run_bundle(bundle)
    assert p.returncode == 0, p.stderr
    other = next(s for s in result['segments'] if s['system'] == 'RAIN')
    assert other['dn'] is None
    assert all(c['segment_id'] != 'SEG-CAD-SECOND' for c in result['dn_match_candidates'])


def test_adapter_output_alias_cannot_destroy_current_source(bundle):
    source = bundle[4]; before = source.read_bytes()
    folder, manifest, profile, ledger, *_ = bundle
    p = subprocess.run([sys.executable, str(PIPE/'build_pipe_inference_input.py'), '--cad-manifest', str(put(folder/'m.json',manifest)),
        '--profile', str(put(folder/'p.json',profile)), '--adjudication-ledger', str(ledger), '--output-dir', str(folder/'out'),
        '--inference-output', str(source)], capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == 2 and '别名' in p.stderr and source.read_bytes() == before


def test_legacy_neutral_stream_and_string_binding_still_work(bundle):
    folder, manifest, profile, ledger_path, *_ = bundle
    stream = Path(manifest['artifacts']['layout_entities']['path'])
    frozen_sha = sha(stream)
    ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    ledger['source_sha256'] = frozen_sha
    ledger['evidence'][0]['source_sha256'] = frozen_sha
    put(ledger_path, ledger)
    profile['expected_source_sha256'] = frozen_sha
    profile['adjudication_ledger']['sha256'] = sha(ledger_path)
    profile['unit']['raw_unit'] = 'Millimeters'
    out = folder/'neutral_result.json'
    p = subprocess.run([sys.executable, str(PIPE/'build_pipe_inference_input.py'), '--neutral-stream', str(stream),
        '--profile', str(put(folder/'neutral_profile.json', profile)), '--adjudication-ledger', str(ledger_path),
        '--output-dir', str(folder/'neutral_work'), '--inference-output', str(out)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == 0, p.stderr
    result = json.loads(out.read_text(encoding='utf-8'))
    assert result['length_summary']['W']['规则推算量']['DN80'] == 10


def test_batch_seed_has_real_working_evidence_and_enters_inference(package):
    family = package['mapping']['families'][-1]
    family.update(disposition='seed', basis='按图面两根平行管共用DN80核对', evidence_ids=['EV-stream'],
        frame_pattern='^A1$', segment_bindings={package['ids']['dn']: ['SEG-'+package['ids']['a1'], 'SEG-'+package['ids']['a2']]})
    p, summary, output = run_batch(package, '--frame-id', 'A1', '--run-inference', mapping=package['mapping'])
    assert p.returncode == 0, p.stdout+p.stderr
    result = json.loads((output/'A1/管网推算证据.json').read_text(encoding='utf-8'))
    assert result['metrics']['dn_match_seed_count'] == 2
    ledger = json.loads((output/'A1/模型工作识别记录.json').read_text(encoding='utf-8'))
    entry = next(e for e in ledger['entries'] if e['adjudication_id'] == family['family_id'])
    assert entry['decision_origin'] == 'model' and 'confirmed_by' not in entry
    assert package['ids']['a1'] in entry['object_ids']


def stack_fixture(angle=0, reverse=False):
    h = 'a'*64
    def xy(p):
        x,y = p; return [x*math.cos(angle)-y*math.sin(angle), x*math.sin(angle)+y*math.cos(angle), 0.]
    def row(oid, kind, **extra):
        return dict(record_id=oid, entity_type=kind, source_sha256=h, layout='Model', **extra)
    # The crossing occurs on a middle leg; an endpoint chord misses both pipes.
    vertices = [xy((-2,3)), xy((0,3)), xy((0,-1)), xy((-2,-1))]
    rows = {'L': row('L','LEADER',raw_geometry=dict(vertices=vertices, path_type=0))}
    for i,y in enumerate((2,1)):
        p = xy((-3,y)); rows[f'T{i}'] = row(f'T{i}','TEXT', x=p[0], y=p[1], z=0, text=f'DN{80+i*20}')
        rows[f'P{i}'] = row(f'P{i}','LINE')
    pipes = {f'SEG-P{i}':dict(segment_id=f'SEG-P{i}',system='CW',space_id='F1',start=xy((-1,y)),end=xy((1,y))) for i,y in enumerate((2,1))}
    axis=xy((0,-1))
    if reverse: axis=[-v for v in axis]
    stack=dict(stack_id='S', source_sha256=h, unit='m', intersection_tolerance=.0001, tolerance_basis='图面误差校准',
        text_ids=['T1','T0'], segment_ids=['SEG-P1','SEG-P0'], leader_path=[dict(record_id='L',direction='reverse' if reverse else 'forward')], text_order_axis=axis)
    return rows,pipes,stack


def bind(rows, pipes, stack):
    return bind_stack(stack,rows,pipes,factor=1,output_unit='m',source_sha256='a'*64,system='CW',space_id='F1',applies=lambda oid:True)


@pytest.mark.parametrize('angle', [0,.51,math.pi/2,math.pi])
@pytest.mark.parametrize('reverse', [False,True])
def test_complete_bent_rotated_and_reversed_leader_order(angle,reverse):
    rows,pipes,stack = stack_fixture(angle,reverse)
    mappings, evidence=bind(rows,pipes,stack)
    assert mappings=={'T0':['SEG-P0'],'T1':['SEG-P1']}
    assert all(h['leader_part_index']==1 for h in evidence['intersections'])


@pytest.mark.parametrize('fault',['source','space','same_station','same_text_station','repeat_crossing','gap','nan','unit','duplicate_target','arc_leader','unlisted_crossing'])
def test_full_leader_refuses_ambiguous_and_invalid_input(fault):
    rows,pipes,stack=stack_fixture()
    if fault=='source':rows['L']['source_sha256']='b'*64
    if fault=='space':pipes['SEG-P0']['space_id']='F2'
    if fault=='same_station':pipes['SEG-P1']=dict(pipes['SEG-P0'],segment_id='SEG-P1')
    if fault=='same_text_station':rows['T1'].update(x=rows['T0']['x'],y=rows['T0']['y'])
    if fault=='repeat_crossing':rows['L']['raw_geometry']['vertices'] += [[1,-1,0],[1,3,0],[-2,3,0]]
    if fault=='gap':
        rows['L2']=dict(rows['L'],record_id='L2')
        stack['leader_path'].append(dict(record_id='L2',direction='forward'))
    if fault=='nan':stack['intersection_tolerance']=float('nan')
    if fault=='unit':stack['unit']='mm'
    if fault=='duplicate_target':stack['segment_ids'].append('SEG-P0')
    if fault=='arc_leader':rows['L']['raw_geometry']['path_type']=1
    if fault=='unlisted_crossing':pipes['SEG-EXTRA']=dict(segment_id='SEG-EXTRA',system='CW',space_id='F1',start=[-1,.5,0],end=[1,.5,0])
    with pytest.raises(ValueError):bind(rows,pipes,stack)


def test_stack_runs_actual_adapter_entry_not_only_geometry(bundle):
    def change(rows, profile, ledger):
        duplicate_pipe(rows,profile,ledger)
        first, txt, second=rows
        other=copy.deepcopy(txt);other['record_id']='CAD-T2';other['text']='DN100';other['y']=600;other['bbox']=[5000,600,5000,600]
        leader=dict(record_id='CAD-LEAD',row_type='entity',entity_type='LEADER',layout='Model',source_sha256=first['source_sha256'],
            raw_geometry=dict(vertices=[[4000,700,0],[5000,700,0],[5000,-200,0]],path_type=0),bbox=[4000,-200,5000,700],x=4000,y=700,z=0)
        rows.extend([other,leader])
        for e in ledger['entries']:e['object_ids'].extend([other['record_id'],leader['record_id']])
        rule=profile['text_rules'][0];rule['segment_bindings']={}
        rule['callout_stacks']=[dict(stack_id='STACK',source_sha256=first['source_sha256'],unit='m',intersection_tolerance=.0001,
            tolerance_basis='source review',text_ids=[txt['record_id'],other['record_id']],segment_ids=['SEG-'+first['record_id'],'SEG-CAD-SECOND'],
            leader_path=[dict(record_id=leader['record_id'],direction='forward')],text_order_axis=[0,-1])]
    mutate_bundle(bundle,change)
    p,result=run_bundle(bundle)
    assert p.returncode==0,p.stderr
    assert result['metrics']['dn_match_seed_count']==2
    generated=json.loads((bundle[0]/'work/管网推算输入.json').read_text(encoding='utf-8'))
    assert generated['callout_stack_evidence'][0]['method']=='complete_leader_crossing_order'


def duct_rows():
    rows={}
    for oid,content,y in [('C','防雨百叶',10),('S','600x400',9)]:
        rows[oid]=dict(record_id=oid,source_sha256='a'*64,entity_type='TEXT',layout='Model',x=0,y=y,z=0,rotation=0,text=content)
    groups=[dict(group_id='G',space_id='F1',layout='Model',object_ids=['C','S'])]
    return rows,groups


def pair(rows,groups,**extra):
    cfg=dict(source_sha256='a'*64,factor=1,spacing=1,tolerance=.05,alignment_tolerance=.1);cfg.update(extra)
    return duct.pair_lines(rows,groups,**cfg)


def test_category_next_size_candidates_do_not_create_device_counts():
    rows,groups=duct_rows(); result=pair(rows,groups)
    assert result['quantity'] is None and result['candidates'][0]['dimensions_mm']==[600,400]
    assert result['candidates'][0]['quantity_owner'] is None
    assert result['candidates'][0]['quantity_owner_status']=='待按物理对象判定唯一出口'


@pytest.mark.parametrize('fault',['source','space','duplicate','nan','unknown_rotation'])
def test_multiline_bad_source_scope_and_geometry_block(fault):
    rows,groups=duct_rows();kwargs={}
    if fault=='source':rows['S']['source_sha256']='b'*64
    if fault=='space':rows['S']['space_id']='F2'
    if fault=='duplicate':groups.append(copy.deepcopy(groups[0]))
    if fault=='nan':kwargs['spacing']=float('nan')
    if fault=='unknown_rotation':rows['C']['rotation']=None
    with pytest.raises(ValueError):pair(rows,groups,**kwargs)


def test_multiline_keeps_ambiguous_and_unpaired_lines():
    rows,groups=duct_rows();rows['S2']=dict(rows['S'],record_id='S2',text='800x400');groups[0]['object_ids'].append('S2')
    result=pair(rows,groups)
    assert len(result['candidates'])==2 and all(c['status']=='ambiguous' for c in result['candidates'])
    rows['S']['y']=8;rows['S2']['y']=8
    result=pair(rows,groups)
    assert result['candidates']==[] and len(result['lines'])==3


def test_multiline_mtext_uses_explicit_calibrated_line_spacing():
    rows,groups=duct_rows();rows['C'].update(entity_type='MTEXT',text='风管箱\\P600×400')
    del rows['S'];groups[0]['object_ids']=['C']
    result=pair(rows,groups)
    assert len(result['candidates'])==1
    assert result['candidates'][0]['category']=='风管箱'
    assert result['candidates'][0]['quantity_owner'] is None and result['candidates'][0]['quantity'] is None


def test_multiline_cli_and_output_alias_guard(bundle):
    folder, manifest, profile, ledger, source, _ = bundle
    stream=Path(manifest['artifacts']['layout_entities']['path'])
    rows=[json.loads(v) for v in stream.read_text().splitlines()]
    category=copy.deepcopy(rows[1]);category['record_id']='CAD-C';category.update(text='风口',x=1000,y=800,rotation=0)
    size=copy.deepcopy(category);size.update(record_id='CAD-S',text='600x400',y=500)
    stream.write_text('\n'.join(json.dumps(v) for v in [category,size]),encoding='utf-8')
    manifest['artifacts']['layout_entities']['sha256']=sha(stream)
    receipt=put(folder/'转换记录.json',dict(source='fixture conversion receipt'))
    manifest['exchange_conversion']=dict(path=str(receipt),sha256=sha(receipt))
    mp=put(folder/'dm.json',manifest)
    cfg=dict(schema_version='qto-duct-multiline-callout-input/1.0',cad_manifest=str(mp),expected_source_sha256=sha(source),
        dimension_unit='mm',unit=dict(output_unit='m',factor_to_output=.001),line_spacing=.3,line_spacing_tolerance=.01,alignment_tolerance=.01,
        spacing_evidence=dict(path=str(stream),sha256=sha(stream),source_sha256=sha(source),locator='category and next size row',observation='本图标注行距300毫米'),
        groups=[dict(group_id='G',layout='Model',space_id='F1',object_ids=['CAD-C','CAD-S'])])
    cp=put(folder/'dcfg.json',cfg);out=folder/'dresult.json'
    command=[sys.executable,str(DUCK),'--input',str(cp),'--output',str(out)]
    p=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stderr
    assert len(json.loads(out.read_text(encoding='utf-8'))['candidates'])==1
    before=source.read_bytes();command[-1]=str(source)
    p=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==2 and '别名' in p.stderr and source.read_bytes()==before
    before_receipt=receipt.read_bytes();command[-1]=str(receipt)
    p=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==2 and '别名' in p.stderr and receipt.read_bytes()==before_receipt



# R3 candidate QA: local text planes, including valid tilted planes and mirrored instances.
def test_multiline_different_parallel_planes_preserves_lines_without_candidate():
    rows, groups = duct_rows()
    rows['S']['z'] = 1.
    result = pair(rows, groups)
    assert result['candidates'] == [] and len(result['lines']) == 2
    assert result['rejected_pairs'][0]['reason'] == 'different_text_planes'
    assert result['rejected_pairs'][0]['normal_offset'] == 1.


@pytest.mark.parametrize('angle', [0., .73, math.pi/2])
@pytest.mark.parametrize('tilt', [0., .52])
@pytest.mark.parametrize('mirror', [False, True])
def test_multiline_local_plane_rotation_tilt_and_mirror_keep_old_candidates(angle, tilt, mirror):
    import qto_cad_geometry as cad
    rows, groups = duct_rows()
    original = pair(copy.deepcopy(rows), groups)['candidates']
    ca, sa, cb, sb = math.cos(angle), math.sin(angle), math.cos(tilt), math.sin(tilt)
    sign = -1. if mirror else 1.
    matrix = [sign*ca, -sa*cb, sa*sb, 3., sign*sa, ca*cb, -ca*sb, 2., 0., sb, cb, 4., 0., 0., 0., 1.]
    for row in rows.values():
        row['x'], row['y'], row['z'] = cad.transform_point([row['x'], row['y'], row['z']], matrix)
        row['instance_transform'] = matrix
    result = pair(rows, groups)
    assert result['candidates'] == original and result['rejected_pairs'] == []
    # The local normal, not world Z, excludes an offset between otherwise aligned lines.
    _, _, normal = duct._text_frame(rows['C'])
    for index, key in enumerate(('x','y','z')):
        rows['S'][key] += .02*normal[index]
    invalid = pair(rows, groups)
    assert invalid['candidates'] == [] and invalid['rejected_pairs'][0]['reason'] == 'different_text_planes'


def test_multiline_native_text_extrusion_uses_ocs_plane_instead_of_world_z():
    from ezdxf.math import OCS
    rows, groups = duct_rows()
    normal = [0., math.sin(.63), math.cos(.63)]
    ocs = OCS(normal)
    for row in rows.values():
        row['x'],row['y'],row['z'] = ocs.to_wcs([row['x'],row['y'],row['z']])
        row['extrusion'] = normal
    assert rows['C']['z'] != rows['S']['z']
    result = pair(rows, groups)
    assert len(result['candidates']) == 1 and result['rejected_pairs'] == []
    assert result['candidates'][0]['quantity'] is None and result['candidates'][0]['quantity_owner'] is None


@pytest.mark.parametrize('full_direction', [False, True])
def test_multiline_tilted_mtext_requires_complete_source_axes(full_direction):
    rows, groups = duct_rows()
    normal = [0., math.sin(.4), math.cos(.4)]
    down = [0., -math.cos(.4), math.sin(.4)]
    rows['C'].update(entity_type='MTEXT',x=0.,y=0.,z=0.,extrusion=normal)
    rows['S'].update(entity_type='MTEXT',x=down[0],y=down[1],z=down[2],extrusion=normal)
    if full_direction:
        for row in rows.values(): row['raw_geometry'] = dict(text_direction=[1.,0.,0.])
        assert len(pair(rows, groups)['candidates']) == 1
    else:
        with pytest.raises(ValueError, match='待核'):
            pair(rows, groups)
