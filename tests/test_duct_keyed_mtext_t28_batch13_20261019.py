"""One source MTEXT, actual keyed markers, one complete shared leader tree."""
import copy
import json
import re
from pathlib import Path
import pytest
from test_duct_source_recovery_t28_batch9_20261019 import leader_case, refresh
from test_duct_batch_network_t28_t20_20261019 import build, consume, write, sha, run, DUCT


def keyed_case(tmp_path, *, defect=None, branch_names=('A','B')):
    c = leader_case(tmp_path)
    original = copy.deepcopy(c['profile']['rules'][-1]['annotation_binding'])
    leaders = original['leader_network']['source_keys']
    actual = branch_names[0] + ':300x200 h=3.2 ' + branch_names[1] + ':500x250 h=4.1'
    if defect == 'extra_dimension': actual += ' C:600x300 h=4.1'
    if defect == 'actual_duplicate': actual += ' A:600x300 h=4.1'
    def edit(m, doc):
        m.delete_entity(doc.entitydb[c['entities']['note'].dxf.handle])
        tag = m.add_mtext(actual, dxfattribs={'insert': (0, 6500), 'char_height': 100, 'layer': 'DUCT-NOTE'})
        positions = [(400, 6200), (900, 6200)]
        if defect == 'trunk': positions[0] = (100, 6500)
        markers = [m.add_text(letter, dxfattribs={'insert': position, 'height': 30, 'layer': 'DUCT-NOTE'})
                   for letter, position in zip(branch_names, positions)]
        return tag, markers
    tag, markers = refresh(c, edit)
    binding = {**original, 'source_key': 'CAD-' + tag.dxf.handle, 'expected_text': actual,
        'keyed_branches': {'item_pattern': r'(?P<branch_id>' + '|'.join(map(re.escape,branch_names)) + r'):(?P<section_text>\d+x\d+ h=\d+(?:\.\d+)?)',
            'marker_pattern': r'(?P<branch_id>' + '|'.join(map(re.escape,branch_names)) + ')', 'marker_tolerance': .001,
            'markers': [{'source_key': 'CAD-' + marker.dxf.handle, 'expected_text': letter,
                         'source_leader_part': {'source_key': leaders[0] if defect == 'trunk' and index == 0 else leaders[-2 + index], 'part_index': 0},
                         'basis': 'source A/B marker attached to the actual terminal leader branch',
                         'source_locator': 'Model source branch marker'} for index, (letter, marker) in enumerate(zip(branch_names, markers))]}}
    if defect == 'missing_marker': binding['keyed_branches']['markers'].pop()
    if defect == 'duplicate_item':
        # Rebuild source is needed for source text changes; this case is tested
        # separately through frozen expected text mismatch.
        binding['expected_text'] += ' A:700x700 h=3.2'
    if defect == 'same_part': binding['keyed_branches']['markers'][1]['source_leader_part'] = copy.deepcopy(binding['keyed_branches']['markers'][0]['source_leader_part'])
    rule = c['profile']['rules'][-1]; rule.pop('annotation_binding'); rule['annotation_bindings'] = [binding]
    ep = Path(rule['evidence']['path']); proof = json.loads(ep.read_text(encoding='utf-8'))
    proof.pop('annotation_network', None); proof['annotation_groups'] = rule['annotation_bindings']
    write(ep, proof); rule['evidence']['sha256'] = sha(ep)
    return c


def test_keyed_single_mtext_shared_tree_actual_builder_consumer(tmp_path):
    c = keyed_case(tmp_path); data = build(c)
    lines = [s for s in data['segments'] if s['representation'] == 'single_line']
    assert sorted((s['width'], s['height']) for s in lines if s.get('width')) == [(300, 200), (500, 250)]
    assert sum(s.get('width') is None for s in lines) == 1
    scopes = data['annotation_scope_candidates']
    assert any(s.get('keyed_branch_bindings') and len(s['keyed_branch_bindings']) == 2 for s in scopes)
    result = consume(tmp_path, data)
    measured = {s['segment_id']: s for s in result['straight_segments']}
    for line in lines:
        if line.get('width'):
            assert measured[line['segment_id']]['center_elevation'] == pytest.approx(3.3 if line['width'] == 300 else 4.225)
    write(c['pp'], c['profile']); out = tmp_path / 'actual-cli'
    proc = run(DUCT / 'build_duct_network_from_neutral.py', '--cad-manifest', c['manifest'], '--profile', c['pp'], '--output-dir', out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((out / '实际风管消费日志.json').read_text(encoding='utf-8'))['returncode'] == 0


@pytest.mark.parametrize('defect', ['trunk', 'missing_marker', 'same_part', 'extra_dimension', 'actual_duplicate'])
def test_keyed_mtext_never_assigns_by_text_order_or_nearest_branch(tmp_path, defect):
    c = keyed_case(tmp_path, defect=defect); data = build(c)
    assert all(s.get('width') is None for s in data['segments'] if s['representation'] == 'single_line')
    assert any(s['status'] == 'unresolved' for s in data['annotation_scope_candidates'])
    result = consume(tmp_path, data)
    assert any(s['kind'] == 'annotation_scope_unresolved' for s in result['pending'])


def test_keyed_mtext_frozen_text_not_replaced_with_handfilled_sizes(tmp_path):
    c = keyed_case(tmp_path, defect='duplicate_item')
    with pytest.raises(ValueError, match='引注内容'): build(c)


def test_source_branch_identifier_is_not_misread_as_a_diameter(tmp_path):
    c = keyed_case(tmp_path, branch_names=('D1','D2')); data = build(c)
    assert sorted(s['width'] for s in data['segments'] if s['representation']=='single_line' and s.get('width')) == [300,500]
