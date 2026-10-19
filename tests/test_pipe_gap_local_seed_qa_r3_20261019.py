"""R3 regression: independent weak seeds cannot inherit a cached bridge promotion."""
import copy
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import test_pipe_serial_gaps_t25_20261019 as cases


def seed(source,level='scenario',dn='DN80'):
    return dict(dn=dn,evidence_level=level,source_id=source,assumption_id='A-'+source)


def local_case(right_seeds=None):
    data=cases.network(.675,False)
    data['segments'][0]['dn_seeds']=[seed('LEFT','confirmed')]
    data['segments'][1]['dn_seeds']=[seed('RIGHT-WEAK')] if right_seeds is None else right_seeds
    data['segments'].append(dict(segment_id='TAIL',system='W',start=[3.675,0],end=[4.275,0],
                                source_ids=['EV-TAIL'],dn_seeds=[]))
    data['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['LEFT','RIGHT','TAIL'])
    data['valves']=[dict(valve_id='ON-LEFT',system='W',point=[.5,0],source_id='EV-ON-LEFT',instance_level='confirmed',
        connected_segment_ids=['LEFT'],connection_source_id='EV-ACTUAL-LEFT'),
        dict(valve_id='ON-TAIL',system='W',point=[3.975,0],source_id='EV-ON-TAIL',instance_level='confirmed',
        connected_segment_ids=['TAIL'],connection_source_id='EV-ACTUAL-TAIL')]
    return data


def layer(row):
    return row['dn'],row['evidence_level'],row['quantity_level'],tuple(row['source_ids']),tuple(row['assumption_ids'])


def summary_total(d):
    return sum(v for levels in d.values() for dns in levels.values() for v in dns.values())


@pytest.mark.parametrize('local_seeds',[[seed('RIGHT-WEAK')],[seed('RIGHT-WEAK-1'),seed('RIGHT-WEAK-2')]])
def test_weak_local_support_and_valve_stay_scenario_with_or_without_external_bridge(tmp_path,local_seeds):
    data=local_case(copy.deepcopy(local_seeds))
    with_bridge=cases.success(tmp_path,data,'with_bridge')
    excluded=copy.deepcopy(data)
    excluded['gap_exclusions']=[dict(a_segment='LEFT',a_end='end',b_segment='RIGHT',b_end='start')]
    without_bridge=cases.success(tmp_path,excluded,'without_bridge')
    assert len(with_bridge['topology_bridges'])==1 and without_bridge['topology_bridges']==[]
    expected_ids={s['source_id'] for s in local_seeds}
    expected_assumptions={s['assumption_id'] for s in local_seeds}
    for result in (with_bridge,without_bridge):
        rows={s['segment_id']:s for s in result['segments']}
        assert rows['LEFT']['evidence_level']=='confirmed' and rows['LEFT']['assumption_ids']==['A-LEFT']
        for sid in ('RIGHT','TAIL'):
            row=rows[sid]
            assert row['dn']=='DN80' and row['evidence_level']=='scenario'
            assert row['quantity_level']=='方案暂估量'
            assert set(row['source_ids'])==expected_ids and set(row['assumption_ids'])==expected_assumptions
            assert not row.get('requires_topology_assumption')
            independent=result['independent_dn_assignments'][sid]
            assert layer(independent)==layer(row)
        valves={v['valve_id']:v for v in result['valves']}
        assert valves['ON-LEFT']['dn_evidence_level']=='inferred'
        assert valves['ON-TAIL']['dn_evidence_level']=='scenario'
        assert set(valves['ON-TAIL']['dn_assumption_ids'])==expected_assumptions
        assert result['length_summary']['W']['方案暂估量']['DN80']==pytest.approx(2.6)
        assert summary_total(result['length_summary'])==summary_total(result['independent_drawn_length_summary'])==pytest.approx(3.6)
        assert result['conditional_drawn_segment_length']==0
        assert result['reconstructed_length']==pytest.approx(3.6)


@pytest.mark.parametrize('strong_level',['inferred','confirmed'])
def test_same_dn_independent_strong_seed_can_support_rule_tail_without_unrelated_gap(tmp_path,strong_level):
    data=local_case([seed('RIGHT-WEAK'),seed('RIGHT-STRONG',strong_level)])
    result=cases.success(tmp_path,data)
    rows={s['segment_id']:s for s in result['segments']}
    assert rows['LEFT']['evidence_level']=='confirmed'
    assert rows['RIGHT']['evidence_level']==strong_level
    assert rows['TAIL']['evidence_level']=='inferred'
    assert set(rows['TAIL']['source_ids'])=={'RIGHT-WEAK','RIGHT-STRONG'}
    assert 'A-GAP-0001' not in rows['TAIL']['assumption_ids']
    assert result['independent_dn_assignments']['TAIL']['evidence_level']=='inferred'
    assert next(v for v in result['valves'] if v['valve_id']=='ON-TAIL')['dn_evidence_level']=='inferred'
    assert summary_total(result['length_summary'])==summary_total(result['independent_drawn_length_summary'])==pytest.approx(3.6)


def test_local_other_dn_scenario_is_not_overridden_by_remote_confirmed_same_chain(tmp_path):
    result=cases.success(tmp_path,local_case([seed('RIGHT-WEAK','scenario','DN100')]))
    rows={s['segment_id']:s for s in result['segments']}
    assert rows['LEFT']['dn']=='DN80' and rows['LEFT']['evidence_level']=='confirmed'
    assert rows['RIGHT']['dn']==rows['TAIL']['dn']=='DN100'
    assert rows['TAIL']['evidence_level']=='scenario' and rows['TAIL']['assumption_ids']==['A-RIGHT-WEAK']
    assert result['independent_dn_assignments']['TAIL']['dn']=='DN100'


def test_conflicting_local_scenarios_remain_alternatives_not_remote_rule_dn(tmp_path):
    result=cases.success(tmp_path,local_case([seed('LOCAL-80','scenario','DN80'),seed('LOCAL-100','scenario','DN100')]))
    rows={s['segment_id']:s for s in result['segments']}
    assert rows['LEFT']['evidence_level']=='confirmed'
    assert rows['TAIL']['dn'] is None and rows['TAIL']['evidence_level']=='scenario'
    assert rows['TAIL']['alternatives']==['DN100','DN80']
    assert set(rows['TAIL']['assumption_ids'])=={'A-LOCAL-80','A-LOCAL-100'}
    assert result['independent_dn_assignments']['TAIL']['dn'] is None
    assert next(v for v in result['valves'] if v['valve_id']=='ON-TAIL')['dn'] is None
    assert summary_total(result['length_summary'])==summary_total(result['independent_drawn_length_summary'])==pytest.approx(3.6)


def test_without_local_seed_borrowed_dn_still_carries_real_gap_dependency(tmp_path):
    result=cases.success(tmp_path,local_case([]))
    rows={s['segment_id']:s for s in result['segments']}
    for sid in ('RIGHT','TAIL'):
        assert rows[sid]['evidence_level']=='scenario'
        assert set(rows[sid]['assumption_ids'])=={'A-LEFT','A-GAP-0001'}
        assert result['independent_dn_assignments'][sid]['dn'] is None
    assert set(next(v for v in result['valves'] if v['valve_id']=='ON-TAIL')['dn_assumption_ids'])=={'A-LEFT','A-GAP-0001'}


def test_two_candidate_bridges_keep_both_dependencies_on_remote_tail(tmp_path):
    data=cases.network(.5,False)
    data['segments'][0]['dn_seeds']=[seed('LEFT','confirmed')]
    data['segments'][1].update(start=[1.5,0],end=[1.7,0])
    data['segments'].append(dict(segment_id='TAIL',system='W',start=[2.2,0],end=[3.2,0],source_ids=['EV-TAIL'],dn_seeds=[]))
    data['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['LEFT','RIGHT','TAIL'])
    result=cases.success(tmp_path,data)
    assert len(result['topology_bridges'])==2
    tail=next(s for s in result['segments'] if s['segment_id']=='TAIL')
    assert tail['evidence_level']=='scenario'
    assert {'A-GAP-0001','A-GAP-0002'}<=set(tail['assumption_ids'])
    assert result['independent_dn_assignments']['TAIL']['dn'] is None


def test_multiple_remote_same_dn_sources_claim_only_a_sufficient_actual_path(tmp_path):
    data=cases.network(.5,False)
    data['segments'][0]['dn_seeds']=[seed('LEFT','confirmed')]
    data['segments'][1].update(start=[1.5,0],end=[1.7,0])
    data['segments'].append(dict(segment_id='FAR',system='W',start=[2.2,0],end=[3.2,0],
        source_ids=['EV-FAR'],dn_seeds=[seed('FAR','confirmed')]))
    data['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['LEFT','RIGHT','FAR'])
    result=cases.success(tmp_path,data)
    rows={s['segment_id']:s for s in result['segments']}
    assert rows['LEFT']['evidence_level']==rows['FAR']['evidence_level']=='confirmed'
    assert rows['LEFT']['assumption_ids']==['A-LEFT'] and rows['FAR']['assumption_ids']==['A-FAR']
    middle=rows['RIGHT']
    assert middle['evidence_level']=='scenario' and len(middle['source_ids'])==1
    source=middle['source_ids'][0]
    expected_gap=next(b['assumption_id'] for b in result['topology_bridges'] if source in {b['a']['segment_id'],b['b']['segment_id']})
    assert set(middle['assumption_ids'])=={'A-'+source,expected_gap}
    assert result['independent_dn_assignments']['RIGHT']['dn'] is None


def test_weak_local_seed_keeps_actual_bend_fitting_and_valve_spec_at_scenario(tmp_path):
    data=local_case()
    data['segments'][2]['end']=[3.675,.6]
    data['valves'][1]['point']=[3.675,.3]
    result=cases.success(tmp_path,data)
    fitting=next(f for f in result['fitting_candidates'] if f['type']=='弯头候选')
    assert fitting['quantity_level']=='方案暂估量'
    assert fitting['dn_combination']=='DN80×DN80'
    assert fitting['assumption_ids']==['A-RIGHT-WEAK']
    valve=next(v for v in result['valves'] if v['valve_id']=='ON-TAIL')
    assert valve['dn_evidence_level']=='scenario' and valve['dn_assumption_ids']==['A-RIGHT-WEAK']
    assert result['independent_dn_assignments']['TAIL']['evidence_level']=='scenario'
    assert result['metrics']['length_conservation_delta']==result['metrics']['independent_length_conservation_delta']==0
