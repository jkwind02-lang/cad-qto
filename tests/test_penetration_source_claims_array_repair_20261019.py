"""Real-DXF counterexamples and controls for source occupancy and unsupported arrays."""
import copy
import json

import pytest

import test_shared_penetrations_t30_20261019 as t30

pen=t30.pen


def pair(c,first=(0,.75),second=(.25,1)):
    route=c['request']['routes'][0]
    route['bindings'][0].update(part_index=0,fraction_range=list(first))
    other=copy.deepcopy(route)
    other.update(id='R2',physical_id='P2')
    other['bindings'][0]['fraction_range']=list(second)
    c['request']['routes'].append(other)
    return route,other


def assert_unknown(result):
    assert result['status']=='REVIEW_REQUIRED'
    assert result['quantities']['complete_spatial_count'] is None
    assert result['quantities']['spatial_crossing_count'] is None
    assert result['todo']
    assert not result['business_adoption_allowed']


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('alias',['none','CADINST','part_suffix'])
def test_partial_source_overlap_retains_both_claims_and_unknown(tmp_path,reverse,alias):
    c=t30.fixture(tmp_path/'source');a,b=pair(c)
    if alias=='CADINST':
        b['bindings'][0]['source_id']='CADINST-'+b['bindings'][0]['source_id']
    elif alias=='part_suffix':
        b['bindings'][0]['source_id']+='#0'
    b['bindings'][0]['direction']=-1
    if reverse:c['request']['routes'].reverse()
    result=t30.calc(c);assert_unknown(result)
    assert result['quantities']['physical_identity_candidate_count']==2
    assert result['quantities']['known_spatial_crossing_subtotal']==0
    overlap=next(t for t in result['todo'] if t.get('source_overlaps'))
    assert set(overlap['route_ids'])=={'R','R2'}
    assert overlap['source_overlaps'][0]['overlap_range']==[.25,.75]


@pytest.mark.parametrize('intervals,expected',[(((0,1),(0,1)),None),(((0,.4),(.4,1)),1),(((0,.4),(.45,1)),1)])
def test_complete_duplicate_and_disjoint_partitions_keep_correct_scope(tmp_path,intervals,expected):
    c=t30.fixture(tmp_path/'source');pair(c,*intervals)
    result=t30.calc(c)
    assert result['quantities']['complete_spatial_count']==expected
    if expected is None:assert_unknown(result)
    else:assert not result['todo']


def test_disjoint_partition_starting_inside_wall_is_incomplete_not_an_identity_conflict(tmp_path):
    c=t30.fixture(tmp_path/'source');pair(c,(0,.4),(.5,1))
    result=t30.calc(c);assert_unknown(result)
    assert not any(t.get('source_overlaps') for t in result['todo'])
    assert any(t.get('kind')=='partial_in_wall' for t in result['noncrossings'])


def test_same_instance_different_original_parts_are_not_the_same_claim(tmp_path):
    c=t30.fixture(tmp_path/'source',shape='twice')
    a,b=pair(c,(0,1),(0,1));b['bindings'][0]['part_index']=2
    result=t30.calc(c)
    assert result['quantities']['complete_spatial_count']==2
    assert not result['todo']


@pytest.mark.parametrize('mode',['valid','same_index','different_source_count'])
def test_overlapping_intervals_need_matching_sourced_count_and_distinct_indices(tmp_path,mode):
    c=t30.fixture(tmp_path/'source');m=c['doc'].modelspace()
    note=m.add_text('PIPE COUNT=2');other=m.add_text('PIPE COUNT=3');t30.refresh(c)
    a,b=pair(c)
    for i,route in enumerate((a,b),1):
        entity=other if mode=='different_source_count' and i==2 else note
        route['multiplicity_evidence']=dict(source_id='CAD-'+entity.dxf.handle,
            expected_text='PIPE COUNT=3' if entity is other else 'PIPE COUNT=2',
            pattern=r'PIPE COUNT=(?P<count>\d+)',instance_index=1 if mode=='same_index'else i)
    result=t30.calc(c)
    if mode=='valid':
        assert result['quantities']['complete_spatial_count']==2
        assert not result['todo']
    else:
        assert_unknown(result)
        assert result['quantities']['known_spatial_crossing_subtotal']==0


def nested(c,array_at=None,counts=None):
    d=c['doc'];leaf=d.blocks.new('LEAF');line=leaf.add_line((-2,0,1),(2,0,1))
    mid=d.blocks.new('MID')
    inner=mid.add_blockref('LEAF',(0,0,0),dxfattribs=dict(rotation=20,xscale=-1,yscale=2))
    outer=d.modelspace().add_blockref('MID',(0,0,0),dxfattribs=dict(rotation=10))
    if array_at:
        target=outer if array_at=='outer'else inner
        for key,value in counts.items():setattr(target.dxf,key,value)
    t30.refresh(c)
    sid='CAD-'+outer.dxf.handle+'/MID/CAD-'+inner.dxf.handle+'/LEAF/CAD-'+line.dxf.handle
    c['request']['routes'][0]['bindings']=[dict(source_id=sid)]
    return sid,outer,inner,line


@pytest.mark.parametrize('array_at',['outer','inner'])
@pytest.mark.parametrize('counts',[dict(row_count=2,row_spacing=.6),dict(column_count=2,column_spacing=.6),dict(row_count=3,column_count=4)])
def test_array_at_any_selected_ancestor_does_not_become_first_placement(tmp_path,array_at,counts):
    c=t30.fixture(tmp_path/'source');sid,outer,inner,line=nested(c,array_at,counts)
    result=t30.calc(c);assert_unknown(result)
    assert not result['events']
    assert result['quantities']['known_spatial_crossing_subtotal']==0
    issue=next(t for t in result['todo'] if t.get('source_id')==sid)
    ancestor=issue['source_diagnostics'][0]
    assert ancestor['source_id']==('CAD-'+outer.dxf.handle if array_at=='outer'else sid.split('/LEAF/')[0])
    assert 'MINSERT' in ancestor['problem']
    assert ancestor['array_counts']['row_count']==counts.get('row_count',1)
    assert ancestor['array_counts']['column_count']==counts.get('column_count',1)


def test_normal_nested_rotation_mirror_and_distinct_complete_instances_stay_valid(tmp_path):
    c=t30.fixture(tmp_path/'source');sid,outer,inner,line=nested(c)
    alias=copy.deepcopy(c['request']['routes'][0]);alias.update(id='ALIAS')
    alias['bindings'][0]['direction']=-1;c['request']['routes'].append(alias)
    result=t30.calc(c)
    assert result['quantities']['complete_spatial_count']==1
    assert len(result['duplicate_representations'])==1
    other=c['doc'].modelspace().add_blockref('MID',(0,0,0),dxfattribs=dict(rotation=10))
    t30.refresh(c)
    new=copy.deepcopy(c['request']['routes'][0]);new.update(id='R2',physical_id='P2')
    new['bindings'][0]['source_id']=sid.replace('CAD-'+outer.dxf.handle+'/','CAD-'+other.dxf.handle+'/',1)
    c['request']['routes'].append(new)
    result=t30.calc(c)
    assert result['quantities']['complete_spatial_count']==2


def test_unrelated_array_is_retained_in_provenance_without_erasing_valid_selection(tmp_path):
    c=t30.fixture(tmp_path/'source');route=copy.deepcopy(c['request']['routes'][0])
    nested(c,'outer',dict(row_count=2,row_spacing=.6))
    c['request']['routes']=[route]
    result=t30.calc(c)
    assert result['quantities']['complete_spatial_count']==1
    assert not result['todo']
    assert any('MINSERT' in t['problem'] for t in result['provenance']['source_reconstruction_issues'])


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('field,value',[('datum_id','OTHER'),('z_mode','plan_only'),('space_id','S2'),
    ('floor_id','F2'),('discipline','duct'),('system','OTHER')])
def test_alias_context_conflicts_reject_in_both_orders(tmp_path,reverse,field,value):
    c=t30.fixture(tmp_path/'source');alias=copy.deepcopy(c['request']['routes'][0]);alias['id']='ALIAS'
    alias[field]=value;c['request']['routes'].append(alias)
    if reverse:c['request']['routes'].reverse()
    with pytest.raises(ValueError,match='同一物理管路多个表达'):
        t30.calc(c)


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('kind',['multiplicity','datum_basis','scenario'])
def test_alias_evidence_and_multiplicity_conflicts_never_use_first_expression(tmp_path,reverse,kind):
    c=t30.fixture(tmp_path/'source');note=c['doc'].modelspace().add_text('PIPE COUNT=2');t30.refresh(c)
    original=c['request']['routes'][0];alias=copy.deepcopy(original);alias['id']='ALIAS'
    if kind=='multiplicity':
        alias['multiplicity_evidence']=dict(source_id='CAD-'+note.dxf.handle,expected_text='PIPE COUNT=2',
            pattern=r'PIPE COUNT=(?P<count>\d+)',instance_index=1)
    elif kind=='datum_basis':
        alias['z_basis_source_ids']=['CAD-'+note.dxf.handle]
    else:
        del alias['bindings'];sid=c['ids']['entity']
        alias['scenario_gaps']=[dict(hypothesis_id='SCENARIO',basis_source_ids=[c['ids']['note']],
            **{'from':dict(source_id=sid,part_index=0,endpoint='start'),
               'to':dict(source_id=sid,part_index=0,endpoint='end')})]
    c['request']['routes'].append(alias)
    if reverse:c['request']['routes'].reverse()
    with pytest.raises(ValueError,match='同一物理管路多个表达'):
        t30.calc(c)


def test_equivalent_aliases_keep_all_source_claims_for_other_routes(tmp_path):
    c=t30.fixture(tmp_path/'source');line=c['doc'].modelspace().add_line((-2,0,1),(2,0,1));t30.refresh(c)
    alias=copy.deepcopy(c['request']['routes'][0]);alias.update(id='ALIAS')
    alias['bindings']=[dict(source_id='CAD-'+line.dxf.handle)]
    other=copy.deepcopy(alias);other.update(id='R2',physical_id='P2')
    c['request']['routes'].extend([alias,other])
    result=t30.calc(c);assert_unknown(result)
    assert result['quantities']['physical_identity_candidate_count']==2


def test_missing_reconstructed_claim_identity_is_unknown_not_one_or_two(tmp_path,monkeypatch):
    c=t30.fixture(tmp_path/'source');original=t30.sources.Sources.binding
    def incomplete(self,raw):
        parts,problem=original(self,raw)
        for part in parts:part['source'].pop('instance_path',None)
        return parts,problem
    monkeypatch.setattr(t30.sources.Sources,'binding',incomplete)
    result=t30.calc(c);assert_unknown(result)
    assert result['quantities']['physical_identity_candidate_count']==1
    assert result['quantities']['known_spatial_crossing_subtotal']==0


@pytest.mark.parametrize('kind',['overlap','datum','array','normal'])
def test_actual_cli_keeps_input_hashes_and_expected_outcome(tmp_path,kind):
    c=t30.fixture(tmp_path/'source')
    if kind=='overlap':pair(c)
    elif kind=='datum':
        alias=copy.deepcopy(c['request']['routes'][0]);alias.update(id='ALIAS',datum_id='OTHER')
        c['request']['routes'].append(alias)
    elif kind=='array':nested(c,'inner',dict(column_count=2,column_spacing=.6))
    else:nested(c)
    request=t30.put(tmp_path/'request.json',c['request']);out=tmp_path/'output'
    before={str(p):t30.sha(p) for p in c['folder'].rglob('*') if p.is_file()}
    before[str(request)]=t30.sha(request)
    done=t30.run(t30.SHARED/'calculate_penetrations.py','--cad-manifest',c['manifest'],'--request',request,'--output-dir',out)
    assert all(t30.sha(p)==value for p,value in before.items())
    if kind=='datum':
        assert done.returncode==2 and '同一物理管路多个表达' in done.stderr
        assert not out.exists()
    else:
        assert done.returncode==0,done.stdout+done.stderr
        result=json.loads((out/'穿越计算结果.json').read_text(encoding='utf-8'))
        if kind=='normal':assert result['quantities']['complete_spatial_count']==1
        else:assert_unknown(result)
