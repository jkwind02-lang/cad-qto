"""Source formula extrema must preserve dimensions, all dependencies and uncertainty."""
import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
from recompute_qto_dependencies import expression, recalculate

L = (('L', 1),)


@pytest.mark.parametrize('formula,expected', [
    ('max(a,b)', 4), ('min(a,b)', 2), ('max(a,b,a+b)', 6),
    ('min(a,b,b-a)', 2), ('min(max(a,b),a+b)', 4),
    ('max(-a,-b)', -2), ('max(a,a)', 2),
])
def test_extrema_use_all_scalar_arguments_with_same_dimensions(formula, expected):
    names = {'a': (2., L), 'b': (4., L)}
    if formula == 'max(a,a)':
        names.pop('b')
    assert expression(formula, names) == (expected, L)


@pytest.mark.parametrize('formula,variables', [
    ('max(a,b)', {'a': (2., L), 'b': (3., (('M', 1),))}),
    ('min(a,0)', {'a': (2., L)}),
    ('max(a)', {'a': (2., L)}), ('min()', {}),
    ('max([a,b])', {'a': (2., L), 'b': (3., L)}),
    ('max(a,b,key=abs)', {'a': (2., L), 'b': (3., L)}),
    ('max(a,b)', {'a': (2., L), 'b': (3., L), 'extra': (1., L)}),
    ('min(a,b*b)', {'a': (2., ()), 'b': (1e308, ())}),
    ('max(a,b)', {'a': (2., L), 'b': (float('nan'), L)}),
    ('max(a,__import__("os"))', {'a': (2., ())}),
])
def test_invalid_extrema_never_hide_units_nonfinite_values_or_code(formula, variables):
    with pytest.raises((ValueError, OverflowError)):
        expression(formula, variables)


def graph():
    return {'schema': 'qto-calculation-graph/1.0', 'project_id': 'generic-rule-chain',
            'sources': [{'id': 'S', 'sha256': 'a' * 64}],
            'rules': [{'id': 'R', 'sha256': 'b' * 64}],
            'nodes': [
                {'id': 'height', 'unit': 'mm', 'value': 600, 'source_ids': ['S']},
                {'id': 'span', 'unit': 'm', 'value': 9, 'source_ids': ['S']},
                {'id': 'ratio', 'unit': '1', 'value': 12, 'source_ids': ['S']},
                {'id': 'control', 'unit': 'mm', 'expression': 'max(h,l/r)',
                 'variables': {'h': 'height', 'l': 'span', 'r': 'ratio'}, 'rule_ids': ['R']},
                {'id': 'derived', 'unit': 'm', 'expression': 'v*2',
                 'variables': {'v': 'control'}, 'rule_ids': ['R']}]}


def rows(result):
    return {r['id']: r for r in result['nodes']}


def test_mixed_units_control_branch_and_downstream_recompute():
    data = graph()
    first = recalculate(data)
    assert rows(first)['control']['value'] == 750
    assert rows(first)['derived']['value'] == 1.5
    data['nodes'][1]['value'] = 6
    second = recalculate(data, first)
    assert rows(second)['control']['value'] == 600
    assert rows(second)['derived']['value'] == 1.2
    assert set(second['affected_nodes']) == {'span', 'control', 'derived'}


def test_unchanged_winner_still_invalidates_changed_losing_dependency():
    data = graph()
    first = recalculate(data)
    data['nodes'][0]['value'] = 550
    second = recalculate(data, first)
    assert rows(first)['control']['value'] == rows(second)['control']['value'] == 750
    assert 'control' in second['affected_nodes'] and 'derived' in second['affected_nodes']


def test_unknown_candidate_not_replaced_with_zero_or_known_winner():
    data = graph()
    data['nodes'][1]['value'] = None
    result = recalculate(data)
    assert rows(result)['control']['value'] is None
    assert rows(result)['derived']['value'] is None
    assert rows(result)['control']['quantity_level'] == '未决量'


def test_weaker_evidence_propagates_even_if_other_candidate_currently_wins():
    data = graph()
    data['nodes'][0]['quantity_level'] = '方案暂估量'
    result = recalculate(data)
    assert rows(result)['control']['value'] == 750
    assert rows(result)['control']['quantity_level'] == '方案暂估量'
    assert result['business_adoption_allowed'] is False


def test_count_and_length_cannot_share_extremum():
    data = graph()
    data['nodes'][0]['unit'] = '个'
    result = recalculate(data)
    assert rows(result)['control']['value'] is None and result['errors']


@pytest.mark.parametrize('allow_negative', [False, True])
def test_rejected_negative_extremum_cannot_flow_into_positive_downstream(allow_negative):
    data = graph()
    data['nodes'][0].update(value=-2000)
    data['nodes'][1].update(value=-36)
    if allow_negative:
        data['nodes'][3]['allow_negative'] = True
    data['nodes'][4]['expression'] = 'abs(v)'
    result = recalculate(data)
    actual = rows(result)
    if allow_negative:
        assert actual['control']['value'] == -2000
        assert actual['derived']['value'] == 2
    else:
        assert actual['control']['value'] is None and actual['control']['status'] == 'BLOCKED'
        assert actual['derived']['value'] is None and actual['derived']['status'] == 'BLOCKED'
        assert actual['control']['quantity_level'] == actual['derived']['quantity_level'] == '未决量'


@pytest.mark.parametrize('failure_mode', ['output_conversion', 'source_conversion'])
def test_nonfinite_unit_conversion_is_blocked_before_downstream_use(failure_mode):
    data = graph()
    if failure_mode == 'output_conversion':
        data['nodes'][0].update(value=1e308, unit='m2')
        data['nodes'][1].update(value=1e308, unit='m2')
        data['nodes'][3].update(unit='mm2')
        data['nodes'][4].update(unit='m2', expression='abs(v)')
    else:
        data['nodes'][0].update(value=1e308, unit='t')
        data['nodes'][1].update(value=1., unit='t')
        data['nodes'][3].update(unit='kg')
        data['nodes'][4].update(unit='kg', expression='abs(v)')
    result = recalculate(data)
    actual = rows(result)
    assert actual['control']['value'] is None and actual['derived']['value'] is None
    if failure_mode == 'source_conversion':
        assert actual['height']['value'] is None
