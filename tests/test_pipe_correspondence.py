"""Manual totals must not establish physical scope, verticals or fixed lengths."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pipe_correspondence', ROOT / 'skills/qto-pipe-networks/scripts/audit_pipe_correspondence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def ledger(tmp_path):
    p = tmp_path / 'drawing.txt'; p.write_text('independent dimensions', encoding='utf-8')
    ref = [{'source_id': 'drawing', 'locator': 'detail:1'}]
    return dict(schema_version=module.VERSION,
        sources=[dict(source_id='drawing', kind='drawing', path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())],
        members=[dict(member_id='A', kind='device', identity='physical', identity_basis='drawing',
                      count=1, scope='L1', system='W', source_refs=ref)],
        parts=[dict(part_id='P', member_id='A', role='device_connection', extent_id='A:connection',
                    unit='m', specification='DN50', source_refs=ref,
                    basis=dict(kind='dimension_chain', lengths_m=[1, 2, 1], evidence_origin='drawing', source_refs=ref))],
        comparisons=[dict(comparison_id='C', part_ids=['P'], manual_quantity=4, manual_member_count=1,
                          expected_scopes=['L1'], expected_member_ids=['A'], system='W', unit='m',
                          specification='DN50', source_refs=ref, extent_coverage='complete', coverage_source_refs=ref)])


def result(d):
    return module.audit(d)['comparisons'][0]


def test_dimension_chain_has_explicit_complete_correspondence(tmp_path):
    r = result(ledger(tmp_path))
    assert r['correspondence_complete'] and r['calculated_quantity'] == 4


def test_equal_allowance_is_not_drawing_proof(tmp_path):
    d = ledger(tmp_path); d['parts'][0]['basis'].update(kind='allowance', value=4)
    r = result(d)
    assert r['arithmetic_match'] and not r['correspondence_complete']
    assert 'DRAWING_BASIS_INCOMPLETE' in r['issues']


def test_equal_total_with_wrong_members_and_scope_not_complete(tmp_path):
    d = ledger(tmp_path); other = dict(d['members'][0], member_id='B', scope='L2')
    d['members'].append(other); d['parts'][0]['member_id']='B'
    r = result(d)
    assert r['arithmetic_match'] and set(r['issues']) == {'SCOPE_MISMATCH', 'MEMBER_MISMATCH'}
    assert r['missing_member_ids']==['A'] and r['extra_member_ids']==['B']


def test_aggregate_count_never_proves_individual_identity(tmp_path):
    d = ledger(tmp_path); d['members'][0].update(identity='aggregate', count=5)
    d['comparisons'][0].update(manual_quantity=20, manual_member_count=5)
    r=result(d); assert r['arithmetic_match'] and not r['correspondence_complete']


def test_distinct_instances_with_same_name_are_retained(tmp_path):
    d=ledger(tmp_path); d['members'][0]['name']='same'
    d['members'].append(dict(d['members'][0], member_id='B'))
    d['parts'].append(dict(d['parts'][0], part_id='Q', member_id='B', extent_id='B:connection'))
    d['comparisons'][0].update(part_ids=['P','Q'], expected_member_ids=['A','B'], manual_quantity=8, manual_member_count=2)
    assert result(d)['correspondence_complete']


def test_common_part_cannot_multiply_device_count(tmp_path):
    d=ledger(tmp_path); d['parts'][0]['role']='valve_group'
    with pytest.raises(ValueError, match='阀组'):result(d)


def test_multi_view_duplicate_extent_rejected(tmp_path):
    d=ledger(tmp_path); d['parts'].append(dict(d['parts'][0], part_id='other-view'))
    with pytest.raises(ValueError, match='重复计量'):result(d)


def test_vertical_from_negative_datum_with_explicit_pair(tmp_path):
    d=ledger(tmp_path); d['members'][0]['kind']='route'; d['parts'][0]['role']='vertical'
    b=d['parts'][0]['basis']; b.update(kind='elevation_pair', from_m=-1, to_m=1,
        datum='project', runs=2, runs_source_refs=b['source_refs'], from_source_refs=b['source_refs'], to_source_refs=b['source_refs'])
    assert result(d)['correspondence_complete']


@pytest.mark.parametrize('case',['origin','dimension','scope','coverage','members','count','unit','spec','system',
                               'nan','negative','boolean','factor','repeat_part','repeat_member','source_changed','missing_locator'])
def test_correspondence_cannot_silently_accept_bad_input(tmp_path,case):
    d=ledger(tmp_path); c=d['comparisons'][0]; p=d['parts'][0]; b=p['basis']; m=d['members'][0]
    if case=='origin':b['evidence_origin']='manual'
    if case=='dimension':b['lengths_m']=[]
    if case=='scope':c['expected_scopes']=[]
    if case=='coverage':c.pop('coverage_source_refs')
    if case=='members':c['expected_member_ids']=['unknown']
    if case=='count':m['count']=2
    if case=='unit':c['unit']='个'
    if case=='spec':c['specification']='DN80'
    if case=='system':c['system']='S'
    if case=='nan':b['lengths_m']=[float('nan')]
    if case=='negative':b['lengths_m']=[-1]
    if case=='boolean':b['lengths_m']=[True]
    if case=='factor':b['runs']=2
    if case=='repeat_part':c['part_ids']=['P','P']
    if case=='repeat_member':d['members'].append(copy.deepcopy(m))
    if case=='source_changed':Path(d['sources'][0]['path']).write_text('changed')
    if case=='missing_locator':b['source_refs']=[{'source_id':'drawing'}]
    with pytest.raises(ValueError):result(d)


def test_difference_is_reported_never_added_as_missing_pipe(tmp_path):
    d=ledger(tmp_path);d['comparisons'][0]['manual_quantity']=10
    out=module.audit(d);r=out['comparisons'][0]
    assert r['difference']==-6 and len(out['parts'])==1 and 'QUANTITY_MISMATCH' in r['issues']


def test_missing_coverage_is_explicit(tmp_path):
    d=ledger(tmp_path);c=d['comparisons'][0];c.pop('expected_member_ids');c['extent_coverage']='partial'
    assert set(result(d)['issues'])=={'MEMBER_COVERAGE_UNKNOWN','EXTENT_COVERAGE_INCOMPLETE'}


@pytest.mark.parametrize('case',['geometry','instance','runs'])
def test_manual_reference_cannot_be_relabelled_as_drawing_proof(tmp_path,case):
    d=ledger(tmp_path)
    d['sources'].append(dict(d['sources'][0],source_id='workbook',kind='manual'))
    manual=[{'source_id':'workbook','locator':'Sheet1!A1'}]
    if case=='geometry':d['parts'][0]['basis']['source_refs']=manual
    if case=='instance':d['members'][0]['source_refs']=manual
    if case=='runs':d['parts'][0]['basis'].update(runs=2,runs_source_refs=manual)
    with pytest.raises(ValueError,match='人工底稿'):result(d)
