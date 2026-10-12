"""Remainders must not turn an incomplete match list into procurement advice."""
import copy
from test_instance_requirements_20261011 import fixture, existing
from qto_instance_requirements import evaluate, verify_result
from merge_pipe_instance_requirements import markdown


def test_empty_existing_list_retains_requirement_without_claiming_procurement(tmp_path):
    result = evaluate(fixture(tmp_path))
    assert sum(r['required_quantity'] for r in result['records']) == 2
    assert sum(r['unmatched_requirement_quantity'] for r in result['records']) == 2
    assert all(r['procurement_additional_quantity'] is None for r in result['records'])
    assert result['procurement_additional_quantity'] is None
    assert 'not procurement' in result['records'][0]['additional_quantity_meaning']
    verify_result(result)


def test_partial_matches_are_not_an_order_for_the_unmatched_port(tmp_path):
    data = fixture(tmp_path)
    existing(data)
    result = evaluate(data)
    rows = {r['subject_physical_id']:r for r in result['records']}
    assert rows['port-a']['unmatched_requirement_quantity'] == 0
    assert rows['port-b']['unmatched_requirement_quantity'] == 1
    assert all(r['procurement_additional_quantity'] is None for r in rows.values())


def test_missing_spec_keeps_known_requirement_count_and_no_procurement(tmp_path):
    data = fixture(tmp_path)
    data['subjects'][0]['facts']['dn']['value'] = None
    row = next(r for r in evaluate(data)['records'] if r['subject_physical_id']=='port-a')
    assert row['required_quantity'] == 1 and not row['specification_complete']
    assert row['unmatched_requirement_quantity'] == 1
    assert row['procurement_additional_quantity'] is None


def test_incompatible_match_remains_unresolved_not_an_assumed_shortfall(tmp_path):
    data = fixture(tmp_path)
    existing(data)['specification']['dn']='DN40'
    row = next(r for r in evaluate(data)['records'] if r['subject_physical_id']=='port-a')
    assert row['status']=='unresolved'
    assert row['unmatched_requirement_quantity'] is None
    assert row['procurement_additional_quantity'] is None


def test_repeated_observation_does_not_reduce_remainder_twice(tmp_path):
    data=fixture(tmp_path)
    obj=existing(data)
    other=copy.deepcopy(obj);other['observation_id']='cad-valve-a'
    data['existing_accessories'].append(other)
    row=next(r for r in evaluate(data)['records'] if r['subject_physical_id']=='port-a')
    assert row['existing_count']==1 and row['unmatched_requirement_quantity']==0


def test_user_summary_labels_empty_matches_as_unmatched_requirements(tmp_path):
    req=evaluate(fixture(tmp_path))
    # A minimal already-calculated network is enough for the report formatter;
    # source/quantity validation remains covered by the merge integration suite.
    result={'scope_id':'scope','meaning':'采购新增量未定','network_result':{'route_preparation':{'length_delta':0}},
            'accessory_summary':[],'instance_requirements':req}
    text=markdown(result)
    assert '未匹配需求' in text and '未提交现件匹配不等于没有现件' in text
    assert '新增余量' not in text
