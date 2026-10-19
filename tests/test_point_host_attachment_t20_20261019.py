"""T20 anonymous source-DXF controls; these are code regressions, not real-DWG acceptance."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
POINT = ROOT / 'skills/qto-components-points/scripts'
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(POINT))
import point_host_attachment as pha


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return path


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True,
                          encoding='utf-8', stdin=subprocess.DEVNULL, timeout=180)


def case(tmp_path, *, duplicate_host=False, unknown_spec=False, space='S', second_point=False, attachment=True, outside_point=False):
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.layers.new('HOST'); doc.layers.new('SYMBOL')
    block = doc.blocks.new('OFFSET_FAMILY')
    block.add_lwpolyline([(10000, 0), (12000, 0), (12000, 1000), (10000, 1000)], close=True)
    # Notes and leader graphics are never the physical family outline.
    block.add_text('ANNOTATION', dxfattribs={'insert': (900000, 900000), 'height': 300})
    block.add_leader([(500000, 500000), (600000, 600000)])
    first = doc.modelspace().add_blockref('OFFSET_FAMILY', (0, 0), dxfattribs={'layer': 'SYMBOL'})
    pid = 'PT-CAD-'+first.dxf.handle
    pids = [pid]
    if second_point:
        second = doc.modelspace().add_blockref('OFFSET_FAMILY', (5000, 0), dxfattribs={'layer': 'SYMBOL'})
        pids.append('PT-CAD-'+second.dxf.handle)
    host = doc.modelspace().add_line((10000, 500, 0), (12000, 500, 0), dxfattribs={'layer': 'HOST'})
    hosts = [host]
    if duplicate_host:
        hosts.append(doc.modelspace().add_line((10000, 501, 0), (12000, 501, 0), dxfattribs={'layer': 'HOST'}))
    host_note = doc.modelspace().add_text('SYS DN100', dxfattribs={'insert': (10000, 1500), 'height': 100})
    outside = doc.modelspace().add_circle((11500, 500), 25) if outside_point else None
    dxf = tmp_path / 'anonymous.dxf'; doc.saveas(dxf)
    extracted = run(SHARED/'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', tmp_path/'neutral')
    assert extracted.returncode in (0, 3), extracted.stdout+extracted.stderr
    src = sha(dxf)
    ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'ANON', 'ledger_version': '1',
              'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
              'evidence': [{'evidence_id': 'OUTLINE', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                            'kind': 'cad_property', 'locator': 'Model', 'observation': 'anonymous offset rectangle'}],
              'entries': [{'adjudication_id': 'REC', 'subject_id': 'RULE', 'professional_owner': 'qto-components-points',
                           'decision': 'include', 'status': 'active', 'decision_origin': 'model', 'recognized_by': 'anonymous code fixture',
                           'recognized_at': '2026-10-19T00:00:00+00:00', 'source_id': 'ANON-DXF', 'evidence_ids': ['OUTLINE'],
                           'object_ids': pids, 'basis': 'one rectangle is one physical symbol', 'scope_basis': 'Model',
                           'identity_basis': 'one source INSERT', 'cross_check': 'separate outline', 'uncertainties': ['working recognition']}]}
    ledger['evidence'].append({'evidence_id': 'HOST-CLASS', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                              'kind': 'cad_property', 'locator': 'Model source note CAD-'+host_note.dxf.handle,
                              'observation': 'source note explicitly states SYS DN100 for the anonymous hosts',
                              'host_segment_classifications': [
                                  {'segment_id': f'H{i}', 'system': 'SYS', 'spec': 'DN100',
                                   'scope': {'kind': 'source_instance_part', 'layout': 'Model', 'space_id': space,
                                             'source_record_id': 'CAD-'+h.dxf.handle, 'instance_path': 'CAD-'+h.dxf.handle, 'part_index': 0},
                                   'basis': 'anonymous explicit source note applies to declared host part',
                                   'source_locator': 'Model note CAD-'+host_note.dxf.handle}
                                  for i, h in enumerate(hosts)]})
    lp = write(tmp_path/'ledger.json', ledger)
    segments = {'schema_version': 'qto-point-host-segments/1.0', 'source_sha256': src,
                'coordinate_system': 'WCS', 'coordinate_unit': 'mm', 'layout': 'Model',
                'segments': [{'segment_id': f'H{i}', 'source_record_id': 'CAD-'+h.dxf.handle,
                              'instance_path': 'CAD-'+h.dxf.handle, 'part_index': 0, 'space_id': space,
                              'system': 'SYS', 'spec': None if unknown_spec else 'DN100', 'classification_evidence_ids': ['HOST-CLASS']}
                             for i, h in enumerate(hosts)]}
    sp = write(tmp_path/'segments.json', segments)
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'ANON', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'ANON', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model', 'space_id': 'S'},
               'unit': {'factor_to_output': 1, 'output_coordinate_unit': 'mm', 'source_id': 'INSUNITS4'},
               'rules': [{'rule_id': 'RULE', 'adjudication_id': 'REC', 'block_pattern': '^OFFSET_FAMILY$', 'decision': 'include',
                          'category': 'anonymous component', 'system': 'SYS', 'spec': 'family', 'unit': '个', 'quantity_level': '规则推算量'}]}
    if attachment:
        profile['point_host_attachment'] = {'schema_version': pha.SCHEMA, 'tolerance': 5, 'large_offset_warning': 100,
                                            'segments': {'path': str(sp), 'sha256': sha(sp)},
                                            'outlets': [{'physical_id': p, 'quantity_outlet': 'independent_point', 'host_layers': ['HOST']} for p in pids]}
    pp = write(tmp_path/'profile.json', profile)
    return {'tmp': tmp_path, 'profile': profile, 'pp': pp, 'lp': lp, 'sp': sp, 'segments': segments, 'pids': pids, 'source': src,
            'ledger': ledger, 'outside_source_id': 'CAD-'+outside.dxf.handle if outside else None}


def adapt(c, name='out', expect=0):
    write(c['pp'], c['profile'])
    out = c['tmp']/name
    p = run(POINT/'build_component_instances_from_neutral.py', '--cad-manifest', c['tmp']/'neutral/CAD中性取证清单.json',
            '--profile', c['pp'], '--adjudication-ledger', c['lp'], '--output-dir', out)
    assert p.returncode == expect, p.stdout+p.stderr
    if expect:
        return p
    return out, json.loads((out/'点位识别报告.json').read_text(encoding='utf-8'))


def attachment_report(out):
    return json.loads((out/'点位位置与管段挂接.json').read_text(encoding='utf-8'))


def test_geometry_offset_and_old_profile_control(tmp_path):
    c = case(tmp_path)
    out, report = adapt(c)
    row = attachment_report(out)['rows'][0]
    assert row['placement']['insertion_point'] == [0, 0, 0]
    assert row['placement']['geometry_point'] == [11000, 500, 0]
    assert row['placement']['displacement'] == [11000, 500, 0]
    assert row['placement']['large_displacement_warning'] is True
    assert row['placement']['position_status'] == 'complete_geometry_extent'
    assert len(row['placement']['excluded_annotations']) == 2
    assert row['connection_proven'] is False and row['placement']['connection_port_verified'] is False
    assert row['host_status'] == 'unique_spatial_candidate'
    assert row['inherited_spec'] == 'DN100' and row['inherited_system'] == 'SYS'
    assert report['point_workpackage_complete'] is True
    c['profile'].pop('point_host_attachment')
    old, _ = adapt(c, 'legacy')
    count = json.loads((old/'点位唯一实例计数输入.json').read_text(encoding='utf-8'))
    assert count['reconciled_instances'][0]['point'] == [0, 0, 0]
    assert not (old/'点位位置与管段挂接.json').exists()


@pytest.mark.parametrize('variant', ['multiple', 'missing_spec', 'space', 'layer', 'no_segments'])
def test_host_ambiguities_preserve_independent_count(tmp_path, variant):
    c = case(tmp_path, duplicate_host=variant=='multiple', unknown_spec=variant=='missing_spec', space='OTHER' if variant=='space' else 'S')
    if variant == 'layer':
        c['profile']['point_host_attachment']['outlets'][0]['host_layers'] = ['DIFFERENT']
    if variant == 'no_segments':
        c['profile']['point_host_attachment'].pop('segments')
    out, report = adapt(c)
    row = attachment_report(out)['rows'][0]
    assert row['host_status'] == 'review_required' and row['host_problems']
    assert row['inherited_system'] is None and row['inherited_spec'] is None
    assert row['quantity_outlet_status'] == 'point_count_only'
    assert report['point_count_scope_complete'] is True


@pytest.mark.parametrize('variant', ['wrong_unit', 'wrong_source', 'fake_coordinates', 'unknown_path'])
def test_segment_source_mismatch_rejected(tmp_path, variant):
    c = case(tmp_path)
    data = c['segments']
    if variant == 'wrong_unit': data['coordinate_unit'] = 'm'
    elif variant == 'wrong_source': data['source_sha256'] = '0'*64
    elif variant == 'fake_coordinates': data['segments'][0]['start'] = [1, 2, 3]
    else: data['segments'][0]['instance_path'] = 'CAD-NOT-PRESENT'
    write(c['sp'], data); c['profile']['point_host_attachment']['segments']['sha256'] = sha(c['sp'])
    assert 'INPUT_REQUIRED' in adapt(c, expect=2).stderr


@pytest.mark.parametrize('variant', ['no_outlet', 'multiple_outlets', 'inline_unreceived'])
def test_outlet_conservation_blocks_unreceived_not_zero(tmp_path, variant):
    c = case(tmp_path, second_point=True)
    rows = c['profile']['point_host_attachment']['outlets']
    if variant == 'no_outlet': rows.pop(0)
    elif variant == 'multiple_outlets': rows.append(copy.deepcopy(rows[0]))
    else: rows[0]['quantity_outlet'] = 'inline_network'
    out, report = adapt(c)
    data = attachment_report(out)
    assert data['quantity_closure_complete'] is False and report['point_workpackage_complete'] is False
    assert data['independent_point_count'] == 1
    count = json.loads((out/'点位唯一实例计数输入.json').read_text(encoding='utf-8'))
    assert [r['physical_id'] for r in count['reconciled_instances']] == [c['pids'][1]]
    assert count['geometry_review']['workpackage_complete'] is (variant == 'inline_unreceived')


def test_source_leaf_transform_not_rotated_local_box(tmp_path):
    line = {'entity_type': 'LINE', 'record_id': 'L', 'layer': '0', 'geometry': {'schema': 'qto-cad-wcs-geometry/1.0',
            'coordinate_system': 'WCS', 'parts': [{'kind': 'line', 'start': [0, 0, 0], 'end': [2, 0, 0]}]}}
    import qto_cad_geometry as cg
    line['geometry']['schema'] = cg.GEOMETRY_SCHEMA
    child = {'entity_type': 'INSERT', 'record_id': 'C', 'block_name': 'B', 'x': 10, 'y': 0, 'rotation': 3.141592653589793/2}
    defs = {'A': [child], 'B': [line]}
    obj = pha.Attachment({'schema_version': pha.SCHEMA, 'tolerance': 1, 'outlets': []}, definitions=defs, origins={},
                         layout_rows=[], factor=1, coordinate_unit='mm', source_sha256='a'*64, layout='Model', space_id='S',
                         profile_path=tmp_path/'p.json', neutral_refs={})
    insert = {'entity_type': 'INSERT', 'record_id': 'T', 'block_name': 'A', 'x': 100, 'y': 50, 'scale_factors': [-2, 3, 1]}
    result = obj.placement(insert, pha.IDENTITY, 'T', [100, 50, 0])
    assert result['geometry_point'] == pytest.approx([80, 53, 0])
    assert result['source_members'][0]['instance_path'] == 'T/A/C/B/L'
    defs['B'].append({'entity_type': 'UNSUPPORTED', 'record_id': 'BAD'})
    partial = obj.placement(insert, pha.IDENTITY, 'T', [100, 50, 0])
    assert partial['position_status'] == 'partial_geometry_extent' and partial['geometry_problems']
    assert obj.candidates(partial, {'host_layers': ['HOST']})[0] == []
    defs['B'] = [{'entity_type': 'TEXT', 'record_id': 'TEXT', 'x': 1, 'y': 1}]
    assert obj.placement(insert, pha.IDENTITY, 'T', [100, 50, 0])['geometry_point'] is None


def pipe_input(c, report_path):
    report = json.loads(report_path.read_text(encoding='utf-8')); row = report['rows'][0]
    host = row['candidate_segments'][0]; identity = row['instance_identity']
    ep = write(c['tmp']/'connection.json', {'schema_version': 'qto-inline-connection-evidence/1.0',
               'source_sha256': c['source'], 'physical_id': row['physical_id'], 'segment_id': 'H0',
               'layout': 'Model', 'space_id': 'S', 'relationship': 'inline_connection', 'evidence_id': 'CONNECTION',
               'basis': 'anonymous fixture explicit inline relationship', 'source_locator': 'Model source rectangle and host H0'})
    data = {'schema_version': 'qto-pipe-network-inference-input/1.2', 'network_id': 'ANON-PIPE', 'unit': 'mm',
            **{k: report[k] for k in ('source_sha256', 'coordinate_system', 'coordinate_unit', 'layout', 'space_id')},
            'parameters': {'snap_tolerance': 1, 'gap_max': 5, 'valve_attach_max': 5},
            'object_selection': {'audit_id': 'ANON-AUDIT', 'status': 'READY', 'candidate_count': 1, 'included_count': 1,
                                 'excluded_count': 0, 'unresolved_count': 0, 'included_segment_ids': ['H0'],
                                 'source_ids': ['HOST-CLASS'], 'anomaly_groups': []},
            'endpoint_handoff': {'handoff_id': 'NO-TERMINALS', 'required': False, 'source_instance_count': 0,
                                 'delivered_count': 0, 'not_applicable_count': 0, 'unresolved_count': 0,
                                 'source_id': 'ANON-NO-TERMINALS', 'reason': 'anonymous inline-only test has no terminal ports'},
            'segments': [{'segment_id': 'H0', 'system': 'SYS', 'start': host['start'], 'end': host['end'], 'space_id': 'S',
                          'source_ids': [host['source_record_id']], 'source_record_id': host['source_record_id'],
                          'source_instance_path': host['instance_path'], 'source_sha256': c['source'],
                          'dn_seeds': [{'dn': 'DN100', 'evidence_level': 'inferred', 'source_id': 'HOST-CLASS'}]}],
            'valves': [{'valve_id': 'V', 'source_object_id': row['physical_id'], 'source_id': 'ANON-SOURCE',
                        'source_sha256': c['source'], 'source_instance_path': identity['instance_path'],
                        'source_record_id': identity['source_record_id'], 'quantity_unit': identity['unit'],
                        'attachment_request_sha256': row['request_sha256'], 'system': 'SYS', 'space_id': 'S',
                        'point': row['placement']['geometry_point'], 'connected_segment_ids': ['H0'],
                        'connection_source_id': 'CONNECTION', 'connection_evidence': {'path': str(ep), 'sha256': sha(ep)},
                        'valve_type': '截止阀', 'instance_level': 'inferred'}]}
    return write(c['tmp']/'pipe-input.json', data), data


def test_actual_consumer_and_replay_receipt_closes_unique_outlet(tmp_path):
    c = case(tmp_path); c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    out, _ = adapt(c)
    ip, _ = pipe_input(c, out/'点位位置与管段挂接.json')
    consumed = run(POINT/'receive_point_inline_components.py', '--attachment-report', out/'点位位置与管段挂接.json',
                   '--network-input', ip, '--output-dir', tmp_path/'received')
    assert consumed.returncode == 0, consumed.stdout+consumed.stderr
    rp = tmp_path/'received/管网在线附件数量接收.json'
    # Save a separate profile so the original request evidence remains frozen.
    c['pp'] = tmp_path/'profile-received.json'
    c['profile']['point_host_attachment']['receptions'] = [{'path': str(rp), 'sha256': sha(rp)}]
    final, report = adapt(c, 'final')
    row = attachment_report(final)['rows'][0]
    assert row['quantity_outlet_status'] == 'received_by_network'
    disposition_rows = [json.loads(line) for line in (final/'点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines()]
    assert disposition_rows[0]['disposition'] == 'network_received'
    assert disposition_rows[0]['host_attachment']['quantity_outlet_status'] == 'received_by_network'
    assert report['point_workpackage_complete'] is True
    assert report['counts']['adopted_physical_instance_count'] == 0
    assert not (final/'点位数量汇总.json').exists()
    assert row['connection_proven'] is False   # spatial layer itself still never creates connection proof
    assert row['receiving_quantity_rows'][0]['quantity'] == 1
    # Overwriting the original request profile invalidates its evidence, even when the new profile references this receipt.
    original_profile = tmp_path/'profile.json'; original_bytes = original_profile.read_bytes()
    write(original_profile, c['profile'])
    assert 'profile_ref' in adapt(c, 'overwritten-original-request', expect=2).stderr
    original_profile.write_bytes(original_bytes)
    # A frozen receipt is tied to the exact request. A changed segment classification cannot replay an old acceptance.
    original_segment = c['sp']
    c['segments']['segments'][0]['classification_evidence_ids'] = ['REVISED-HOST-CLASS']
    revised_segment = write(tmp_path/'revised-segment.json', c['segments'])
    c['profile']['point_host_attachment']['segments'] = {'path': str(revised_segment), 'sha256': sha(revised_segment)}
    stale, report = adapt(c, 'stale-request')
    assert attachment_report(stale)['rows'][0]['quantity_outlet_status'] == 'blocked'
    assert report['point_workpackage_complete'] is False
    c['segments']['segments'][0]['classification_evidence_ids'] = ['HOST-CLASS']
    c['profile']['point_host_attachment']['segments'] = {'path': str(original_segment), 'sha256': sha(original_segment)}
    # Even unchanged values with a different frozen classification record are new dependencies.
    original_ledger = c['lp']; original_observation = c['ledger']['evidence'][1]['observation']
    c['ledger']['evidence'][1]['observation'] += '; independently rechecked same values'
    c['lp'] = write(tmp_path/'revised-ledger.json', c['ledger'])
    c['profile']['adjudication_ledger']['sha256'] = sha(c['lp'])
    revised, report = adapt(c, 'stale-classification-proof')
    revised_row = attachment_report(revised)['rows'][0]
    assert revised_row['host_status'] == 'unique_spatial_candidate'
    assert revised_row['inherited_spec'] == 'DN100' and revised_row['quantity_outlet_status'] == 'blocked'
    c['lp'] = original_ledger; c['ledger']['evidence'][1]['observation'] = original_observation
    c['profile']['adjudication_ledger']['sha256'] = sha(original_ledger)
    # Two valid receiver references still constitute two quantity outlets, never one acceptance.
    c['profile']['point_host_attachment']['receptions'].append({'path': str(rp), 'sha256': sha(rp)})
    multi, report = adapt(c, 'multiple-receivers')
    assert attachment_report(multi)['rows'][0]['quantity_outlet_status'] == 'blocked'
    c['profile']['point_host_attachment']['receptions'].pop()
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'independent_point'
    assert '独立设备' in adapt(c, 'duplicate-outlet', expect=2).stderr
    # Fabricating a consumer result and merely updating hashes fails the independent runtime replay.
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    receipt = json.loads(rp.read_text(encoding='utf-8'))
    actual = Path(receipt['network_result']['path'])
    result = json.loads(actual.read_text(encoding='utf-8')); result['valves'][0]['dn'] = 'DN999'
    write(actual, result); receipt['network_result']['sha256'] = sha(actual); write(rp, receipt)
    c['profile']['point_host_attachment']['receptions'][0]['sha256'] = sha(rp)
    assert '消费输出不一致' in adapt(c, 'fabricated-result', expect=2).stderr


@pytest.mark.parametrize('variant', ['self_received', 'wrong_object', 'wrong_segment_coordinates', 'no_connection_evidence'])
def test_actual_consumer_rejects_claims_and_wrong_sources(tmp_path, variant):
    c = case(tmp_path); c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    out, _ = adapt(c)
    ip, data = pipe_input(c, out/'点位位置与管段挂接.json')
    if variant == 'self_received':
        data['valves'][0].pop('connected_segment_ids'); data['valves'][0]['received'] = True
    elif variant == 'wrong_object': data['valves'][0]['source_object_id'] = 'PT-OTHER'
    elif variant == 'wrong_segment_coordinates': data['segments'][0]['start'] = [0, 0, 0]
    else: data['valves'][0].pop('connection_evidence')
    write(ip, data)
    p = run(POINT/'receive_point_inline_components.py', '--attachment-report', out/'点位位置与管段挂接.json',
            '--network-input', ip, '--output-dir', tmp_path/'rejected')
    assert p.returncode == 2, p.stdout+p.stderr
    assert not (tmp_path/'rejected/管网在线附件数量接收.json').exists()


@pytest.mark.parametrize('variant', ['nonexistent_id', 'unrelated_existing_id', 'old_values', 'wrong_layout', 'wrong_space', 'wrong_path', 'wrong_part'])
def test_classification_needs_current_scoped_values_not_just_existing_id(tmp_path, variant):
    c = case(tmp_path)
    evidence = c['ledger']['evidence'][1]
    value = evidence['host_segment_classifications'][0]
    if variant == 'nonexistent_id': c['segments']['segments'][0]['classification_evidence_ids'] = ['DOES-NOT-EXIST']
    elif variant == 'unrelated_existing_id': c['segments']['segments'][0]['classification_evidence_ids'] = ['OUTLINE']
    elif variant == 'old_values': value['spec'] = 'DN80'
    elif variant == 'wrong_layout': value['scope']['layout'] = 'OTHER'
    elif variant == 'wrong_space': value['scope']['space_id'] = 'OTHER'
    elif variant == 'wrong_path': value['scope']['instance_path'] = 'CAD-OTHER'
    else: value['scope']['part_index'] = 1
    write(c['lp'], c['ledger']); c['profile']['adjudication_ledger']['sha256'] = sha(c['lp'])
    write(c['sp'], c['segments']); c['profile']['point_host_attachment']['segments']['sha256'] = sha(c['sp'])
    out, report = adapt(c)
    row = attachment_report(out)['rows'][0]
    assert row['host_status'] == 'review_required' and row['host_problems']
    assert row['inherited_system'] is None and row['inherited_spec'] is None
    assert report['point_count_scope_complete'] is True
    count = json.loads((out/'点位数量汇总.json').read_text(encoding='utf-8'))
    assert count['unique_instance_count'] == 1 and count['work_quantity_ready'] is True


def test_frozen_classification_source_control_and_changed_source(tmp_path):
    c = case(tmp_path)
    evidence = c['ledger']['evidence'].pop()
    write(c['lp'], c['ledger']); c['profile']['adjudication_ledger']['sha256'] = sha(c['lp'])
    bundle = write(tmp_path/'scoped-classification.json', {'schema_version': 'qto-host-segment-classifications/1.0',
                    'source_sha256': c['source'], 'evidence': [evidence]})
    c['segments']['classification_sources'] = [{'path': str(bundle), 'sha256': sha(bundle)}]
    write(c['sp'], c['segments']); c['profile']['point_host_attachment']['segments']['sha256'] = sha(c['sp'])
    out, _ = adapt(c)
    row = attachment_report(out)['rows'][0]
    assert row['host_status'] == 'unique_spatial_candidate' and row['inherited_spec'] == 'DN100'
    assert row['candidate_segments'][0]['classification_proofs'][0]['record_ref']['sha256'] == sha(bundle)
    # A valid bundle may refer to changed primary evidence; hash existence alone is insufficient.
    note = write(tmp_path/'changed-source.json', {'original': 'DN100'})
    evidence['path'] = str(note); evidence['sha256'] = sha(note)
    write(note, {'changed': 'DN80'})
    write(bundle, {'schema_version': 'qto-host-segment-classifications/1.0', 'source_sha256': c['source'], 'evidence': [evidence]})
    c['segments']['classification_sources'][0]['sha256'] = sha(bundle)
    write(c['sp'], c['segments']); c['profile']['point_host_attachment']['segments']['sha256'] = sha(c['sp'])
    changed, _ = adapt(c, 'changed-classification-source')
    assert attachment_report(changed)['rows'][0]['host_status'] == 'review_required'


def direct_pipe_result(c, ip, name):
    path = c['tmp']/(name+'.json')
    p = run(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py', '--input', ip, '--output', path)
    assert p.returncode == 0, p.stdout+p.stderr
    return path


def receipt_from_actual(c, request_path, ip, actual_path):
    report = json.loads(request_path.read_text(encoding='utf-8'))
    actual = json.loads(actual_path.read_text(encoding='utf-8'))
    items = []
    for row in report['rows']:
        if row['quantity_outlet'] != 'inline_network': continue
        host, identity = row['candidate_segments'][0], row['instance_identity']
        i, valve = next((i, v) for i, v in enumerate(actual['valves']) if v['source_object_id'] == row['physical_id'])
        items.append({'physical_id': row['physical_id'], 'source_sha256': report['source_sha256'],
                      'source_instance_path': identity['instance_path'], 'source_record_id': identity['source_record_id'],
                      'segment_id': host['segment_id'], 'system': host['system'], 'spec': host['spec'],
                      'quantity_type': 'inline_accessory', 'unit': identity['unit'], 'quantity': 1,
                      'quantity_level': identity['quantity_level'], 'status': 'received', 'layout': report['layout'],
                      'space_id': report['space_id'], 'request_sha256': row['request_sha256'],
                      'network_valve_index': i, 'network_valve_id': valve['valve_id']})
    return {'schema_version': 'qto-network-component-reception/1.0', 'professional_owner': 'qto-pipe-networks',
            'workpackage_id': actual['network_id'], **{k: report[k] for k in ('source_sha256', 'coordinate_system', 'coordinate_unit', 'layout', 'space_id')},
            'producer': str(pha.pipe_producer()), 'producer_sha256': sha(pha.pipe_producer()),
            'network_input': {'path': str(ip), 'sha256': sha(ip)}, 'network_result': {'path': str(actual_path), 'sha256': sha(actual_path)},
            'attachment_request': {'path': str(request_path), 'sha256': sha(request_path)}, 'quantity_items': items,
            'quantity_scope': {'request_physical_ids': sorted(row['physical_id'] for row in report['rows']),
                               'received_inline_physical_ids': sorted(row['physical_id'] for row in items),
                               'outside_request_physical_ids': sorted(set(v['source_object_id'] for v in actual['valves'])
                                                                    - {row['physical_id'] for row in report['rows']})}}


@pytest.mark.parametrize('variant', ['hidden_independent', 'nondiscrete_unit', 'wrong_connection_schema', 'missing_inline_item', 'duplicate_item', 'wrong_valve_id'])
def test_receipt_replay_shares_consumer_constraints_and_checks_full_result(tmp_path, variant):
    c = case(tmp_path, second_point=variant=='hidden_independent')
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    if variant == 'nondiscrete_unit': c['profile']['rules'][0]['unit'] = 'm'
    out, _ = adapt(c)
    request = out/'点位位置与管段挂接.json'
    ip, data = pipe_input(c, request)
    if variant == 'hidden_independent':
        other = copy.deepcopy(data['valves'][0]); other.update(valve_id='V-DUP', source_object_id=c['pids'][1],
                       source_instance_path=c['pids'][1][3:], source_record_id=c['pids'][1][3:], point=[16000, 500, 0])
        data['valves'].append(other)
    if variant == 'wrong_connection_schema':
        ep = Path(data['valves'][0]['connection_evidence']['path'])
        evidence = json.loads(ep.read_text(encoding='utf-8')); evidence['schema_version'] = 'unrelated/1.0'
        write(ep, evidence); data['valves'][0]['connection_evidence']['sha256'] = sha(ep)
    write(ip, data)
    if variant in {'hidden_independent', 'nondiscrete_unit', 'wrong_connection_schema'}:
        public = run(POINT/'receive_point_inline_components.py', '--attachment-report', request, '--network-input', ip, '--output-dir', tmp_path/'public-rejected')
        assert public.returncode == 2, public.stdout+public.stderr
    actual = direct_pipe_result(c, ip, 'actual-real-pipe-result')
    receipt = receipt_from_actual(c, request, ip, actual)
    if variant == 'missing_inline_item': receipt['quantity_items'] = []
    if variant == 'duplicate_item': receipt['quantity_items'].append(copy.deepcopy(receipt['quantity_items'][0]))
    if variant == 'wrong_valve_id': receipt['quantity_items'][0]['network_valve_id'] = 'ANOTHER'
    rp = write(tmp_path/'bypassed-public-cli-receipt.json', receipt)
    c['pp'] = tmp_path/'replay-profile.json'
    c['profile']['point_host_attachment']['receptions'] = [{'path': str(rp), 'sha256': sha(rp)}]
    result = adapt(c, 'replay-rejected', expect=2)
    assert 'INPUT_REQUIRED' in result.stderr
    assert not (tmp_path/'replay-rejected/点位数量汇总.json').exists()


def test_explicit_external_scope_and_legal_subset_are_not_rejected(tmp_path):
    c = case(tmp_path, second_point=True, outside_point=True)
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    out, _ = adapt(c); request = out/'点位位置与管段挂接.json'
    ip, data = pipe_input(c, request)
    other = copy.deepcopy(data['valves'][0]); external_pid = 'PT-'+c['outside_source_id']
    other.update(valve_id='OTHER-PACKAGE', source_object_id=external_pid, source_record_id=c['outside_source_id'],
                 source_instance_path=c['outside_source_id'], point=[11500, 500, 0], source_id='OTHER-PACKAGE-SOURCE')
    data['valves'].append(other); write(ip, data)
    public = run(POINT/'receive_point_inline_components.py', '--attachment-report', request, '--network-input', ip, '--output-dir', tmp_path/'external-subset')
    assert public.returncode == 0, public.stdout+public.stderr
    rp = tmp_path/'external-subset/管网在线附件数量接收.json'
    receipt = json.loads(rp.read_text(encoding='utf-8'))
    assert receipt['quantity_scope']['outside_request_physical_ids'] == [external_pid]
    assert len(receipt['quantity_items']) == 1
    actual = json.loads(Path(receipt['network_result']['path']).read_text(encoding='utf-8'))
    assert actual['metrics']['valve_count'] == 2
    c['pp'] = tmp_path/'subset-replay-profile.json'; c['profile']['point_host_attachment']['receptions'] = [{'path': str(rp), 'sha256': sha(rp)}]
    final, report = adapt(c, 'subset-replay')
    assert report['point_workpackage_complete'] is True
    count = json.loads((final/'点位数量汇总.json').read_text(encoding='utf-8'))
    assert count['unique_instance_count'] == 1 and count['work_quantity_ready'] is True
    assert attachment_report(final)['quantity_outlet_guarantee_scope'] == 'current_report_instances_only'
    receipt.pop('quantity_scope'); unscoped = write(tmp_path/'unscoped-external-receipt.json', receipt)
    c['profile']['point_host_attachment']['receptions'] = [{'path': str(unscoped), 'sha256': sha(unscoped)}]
    assert '旁路范围' in adapt(c, 'unscoped-rejected', expect=2).stderr


def all_bytes(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*') if p.is_file()}


@pytest.mark.parametrize('variant', ['same_named_input', 'existing_log', 'hardlink_input_log', 'hardlink_evidence_log',
                                    'existing_actual', 'existing_receipt', 'existing_summary', 'unrelated_existing_file'])
def test_consumer_refuses_existing_outputs_and_aliases_before_any_write(tmp_path, variant):
    c = case(tmp_path); c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    out, _ = adapt(c); request = out/'点位位置与管段挂接.json'
    ip, data = pipe_input(c, request)
    dest = tmp_path/'protected-output'; dest.mkdir()
    if variant == 'same_named_input':
        ip = write(dest/'实际管网消费日志.json', data)
    elif variant == 'hardlink_input_log':
        os.link(ip, dest/'实际管网消费日志.json')
    elif variant == 'hardlink_evidence_log':
        os.link(data['valves'][0]['connection_evidence']['path'], dest/'实际管网消费日志.json')
    else:
        name = {'existing_log': '实际管网消费日志.json', 'existing_actual': '管网实际消费结果.json',
                'existing_receipt': '管网在线附件数量接收.json', 'existing_summary': '管网实际消费结果_deadbeef00_复核摘要.md',
                'unrelated_existing_file': '已有资料.txt'}[variant]
        (dest/name).write_text('existing original data must stay unchanged', encoding='utf-8')
    before = all_bytes(tmp_path)
    p = run(POINT/'receive_point_inline_components.py', '--attachment-report', request, '--network-input', ip, '--output-dir', dest)
    assert p.returncode == 2, p.stdout+p.stderr
    assert all_bytes(tmp_path) == before  # no new outputs and no original/input/evidence byte changed


def directory_alias(tmp_path):
    target = tmp_path/'empty-real-directory'; target.mkdir()
    alias = tmp_path/'directory-alias'
    if os.name == 'nt':
        shell = shutil.which('pwsh') or shutil.which('powershell')
        assert shell, 'Windows reparse regression requires the available PowerShell runtime'
        script = tmp_path/'create-owned-junction.ps1'
        script.write_text('param($Link, $Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n', encoding='utf-8-sig')
        done = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(script), str(alias), str(target)],
                              capture_output=True, text=True, encoding='utf-8', stdin=subprocess.DEVNULL, timeout=30)
        assert done.returncode == 0, done.stdout+done.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)
    return alias, target


def test_consumer_refuses_reparse_output_directory(tmp_path):
    c = case(tmp_path); c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    out, _ = adapt(c); request = out/'点位位置与管段挂接.json'; ip, _ = pipe_input(c, request)
    alias, target = directory_alias(tmp_path)
    before = all_bytes(tmp_path)
    p = run(POINT/'receive_point_inline_components.py', '--attachment-report', request, '--network-input', ip, '--output-dir', alias)
    assert p.returncode == 2 and 'reparse' in p.stderr
    assert not list(target.iterdir()) and all_bytes(tmp_path) == before


@pytest.mark.parametrize('existing_empty', [False, True])
def test_new_consumer_outputs_have_final_business_refs_and_replay(tmp_path, existing_empty):
    c = case(tmp_path); c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet'] = 'inline_network'
    out, _ = adapt(c); request = out/'点位位置与管段挂接.json'; ip, _ = pipe_input(c, request)
    dest = tmp_path/'safe-new-output'
    if existing_empty: dest.mkdir()
    before = all_bytes(tmp_path)
    p = run(POINT/'receive_point_inline_components.py', '--attachment-report', request, '--network-input', ip, '--output-dir', dest)
    assert p.returncode == 0, p.stdout+p.stderr
    for rel, original in before.items(): assert (tmp_path/rel).read_bytes() == original
    assert len(list(dest.iterdir())) == 4
    rp = dest/'管网在线附件数量接收.json'; receipt = json.loads(rp.read_text(encoding='utf-8'))
    assert receipt['network_result']['path'] == str(dest/'管网实际消费结果.json')
    assert 'point-inline-consumer-' not in rp.read_text(encoding='utf-8')
    log = json.loads((dest/'实际管网消费日志.json').read_text(encoding='utf-8'))
    for ref in log['published_artifacts']:
        assert Path(ref['path']).is_file() and sha(Path(ref['path'])) == ref['sha256']
    summaries = list(dest.glob('*_复核摘要.md')); assert len(summaries) == 1
    # The existing producer hashes logical LF text before Windows write_text translates it to CRLF.
    summary_text_hash = hashlib.sha256(summaries[0].read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    assert summaries[0].name == '管网实际消费结果_'+summary_text_hash[:10]+'_复核摘要.md'
    assert 'point-inline-consumer-' not in summaries[0].read_text(encoding='utf-8')
    c['pp'] = tmp_path/'safe-replay-profile.json'; c['profile']['point_host_attachment']['receptions'] = [{'path': str(rp), 'sha256': sha(rp)}]
    final, report = adapt(c, 'safe-replay')
    assert report['point_workpackage_complete'] is True
    assert attachment_report(final)['rows'][0]['quantity_outlet_status'] == 'received_by_network'


@pytest.mark.parametrize('variant', ['profile_named_report', 'hardlink_to_profile', 'existing_report'])
def test_new_attachment_report_never_overwrites_inputs_or_existing_files(tmp_path, variant):
    c = case(tmp_path); dest = tmp_path/'attachment-output'; dest.mkdir()
    target = dest/'点位位置与管段挂接.json'
    if variant == 'profile_named_report': c['pp'] = write(target, c['profile'])
    elif variant == 'hardlink_to_profile': os.link(c['pp'], target)
    else: target.write_text('existing report bytes', encoding='utf-8')
    before = all_bytes(tmp_path)
    p = run(POINT/'build_component_instances_from_neutral.py', '--cad-manifest', tmp_path/'neutral/CAD中性取证清单.json',
            '--profile', c['pp'], '--adjudication-ledger', c['lp'], '--output-dir', dest)
    assert p.returncode == 2, p.stdout+p.stderr
    assert all_bytes(tmp_path) == before


def test_new_attachment_report_rejects_reparse_but_old_profile_defaults_remain(tmp_path):
    c = case(tmp_path); alias, target = directory_alias(tmp_path)
    before = all_bytes(tmp_path)
    p = run(POINT/'build_component_instances_from_neutral.py', '--cad-manifest', tmp_path/'neutral/CAD中性取证清单.json',
            '--profile', c['pp'], '--adjudication-ledger', c['lp'], '--output-dir', alias)
    assert p.returncode == 2 and 'reparse' in p.stderr
    assert not list(target.iterdir()) and all_bytes(tmp_path) == before
    c['profile'].pop('point_host_attachment'); write(c['pp'], c['profile'])
    legacy = tmp_path/'legacy-output'; legacy.mkdir(); kept = legacy/'点位位置与管段挂接.json'
    kept.write_text('unrelated old-profile file', encoding='utf-8'); original = kept.read_bytes()
    p = run(POINT/'build_component_instances_from_neutral.py', '--cad-manifest', tmp_path/'neutral/CAD中性取证清单.json',
            '--profile', c['pp'], '--adjudication-ledger', c['lp'], '--output-dir', legacy)
    assert p.returncode == 0, p.stdout+p.stderr
    assert kept.read_bytes() == original
