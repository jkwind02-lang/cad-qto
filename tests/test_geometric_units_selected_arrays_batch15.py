"""T27: selected source evidence in actual nested DXF array instances.

The oracle uses ezdxf's native multi_insert expansion, independent of the
unit verifier's cell and ATTRIB resolver. No supplied size/scale is evidence.
"""
import copy
import json
import math
import os
from pathlib import Path

import pytest

from test_geometric_units_t27_20261019 import case, check, credential, save_source, units, put, command


def selected_array(case, *, negative=False, zero=False, attribute=None, four_lines=False):
    doc = case['doc']; model = doc.modelspace()
    leaf = doc.blocks.new('SELECTED_SHEET', base_point=(400, -600, 0))
    for name in ('frame', 'scale', 'title', 'dim', 'pipe'):
        model.move_to_layout(doc.entitydb[case['handles'][name]], leaf)
    paper = leaf.add_text('纸张尺寸：841×594mm', dxfattribs={'insert': (4000, 4000)})
    si = leaf.add_text('坐标单位：毫米', dxfattribs={'insert': (4000, 6000)})
    dn = leaf.add_text('DN25', dxfattribs={'insert': (15000, 30000)})
    outer = doc.blocks.new('SELECTED_PARENT', base_point=(300, 200, 0))
    child = outer.add_blockref(leaf.name, (3000, 7000), dxfattribs=dict(rotation=13,
        xscale=-1, yscale=1, row_count=2, column_count=2,
        row_spacing=0 if zero else -250000, column_spacing=0 if zero else 240000))
    root = model.add_blockref(outer.name, (800000, 700000), dxfattribs=dict(rotation=31,
        xscale=-1, yscale=1, row_count=2, column_count=2,
        row_spacing=0 if zero else (-1200000 if negative else 1200000),
        column_spacing=0 if zero else (-1300000 if negative else 1300000)))
    selected = (0, 0) if zero else (1, 1)
    chain = [f'{root.dxf.handle}[{selected[0]},{selected[1]}]',
             f'{child.dxf.handle}[{selected[0]},{selected[1]}]']
    refs = dict(frame=case['request']['frame']['entity_refs'][0], scale=case['request']['scale_text'],
                title=case['request']['view_text_refs'][0], dim=case['request']['dimension_refs'][0],
                paper=dict(handle=paper.dxf.handle), si=dict(handle=si.dxf.handle))
    case['request']['paper_size_text_refs'] = [refs['paper']]
    case['request']['coordinate_unit_refs'] = [refs['si']]
    for ref in refs.values(): ref['insert_handles'] = list(chain)
    if attribute:
        entity = doc.entitydb[refs[attribute]['handle']]
        position = child.matrix44().transform(entity.dxf.insert)
        text = entity.dxf.text
        leaf.delete_entity(entity)
        attr = child.add_attrib(attribute.upper(), text, insert=position)
        refs[attribute]['handle'] = attr.dxf.handle
    if four_lines:
        frame = doc.entitydb[case['handles']['frame']]
        points = list(frame.get_points('xy'))
        leaf.delete_entity(frame)
        case['request']['frame']['entity_refs'] = [dict(handle=leaf.add_line(a, b).dxf.handle,
            insert_handles=list(chain)) for a, b in zip(points, points[1:]+points[:1])]
    # Oracle: use native array expansion and native matrices, not units._ref.
    def native_cell(insert):
        cells = list(insert.multi_insert())
        return cells[0] if zero else cells[3]
    root_cell, child_cell = native_cell(root), native_cell(child)
    def world(point):
        return tuple(root_cell.matrix44().transform(child_cell.matrix44().transform(point)))
    route = doc.entitydb[case['handles']['pipe']]
    ends = [world(route.dxf.start), world(route.dxf.end)]
    # A contained scope around the actual route, within the rotated source frame.
    case['scope']['bounds'] = dict(xmin=min(p[0] for p in ends)-1000, ymin=min(p[1] for p in ends)-1000,
        xmax_exclusive=max(p[0] for p in ends)+1000, ymax_exclusive=max(p[1] for p in ends)+1000)
    save_source(case)
    return dict(root=root, child=child, leaf=leaf, refs=refs, world=world,
                native_root=root_cell, native_child=child_cell, route=route, dn=dn)


@pytest.mark.parametrize('negative,zero,four_lines', [(False, False, False), (True, False, False),
                                                    (False, True, False), (True, False, True)])
def test_selected_array_frame_paper_dim_and_native_wcs_replay(case, negative, zero, four_lines):
    selected = selected_array(case, negative=negative, zero=zero, four_lines=four_lines)
    case['doc'].entitydb[case['handles']['dim']].dxf.insert = (400, 600, 0)
    save_source(case)
    result = credential(case)
    assert result['allows_length_measurement'] is True
    assert result['frame']['paper'] == 'SOURCE_DECLARED'
    assert result['frame']['actual_axis_spans'] == pytest.approx([84100, 59400])
    contexts = result['frame']['source_entities']
    assert len(contexts) == (4 if four_lines else 1)
    for context in contexts:
        assert context['source_ref']['insert_handles'] == selected['refs']['frame']['insert_handles']
        for native, actual in zip(context['source_placement_points'], context['placement_points_wcs']):
            assert actual == pytest.approx(selected['world'](native))
        assert [(r['row_count'], r['column_count']) for r in context['insert_chain']] == [(2, 2), (2, 2)]
    dimension = result['dimensions'][0]
    for key, actual in dimension['definition_points_wcs'].items():
        assert actual == pytest.approx(selected['world'](dimension['source_definition_points'][key]))
    source_render = dimension['rendered_text_source_placement_points'][0]
    offset = dimension['dimension_render_offset_ocs']
    assert dimension['rendered_text_point_wcs'] == pytest.approx(selected['world'](
        [source_render[i]+offset[i] for i in range(3)]))
    assert dimension['measurement'] == pytest.approx(6000.)
    assert len(result['unit_text_search']['checked_array_cells']) == 20
    matches = result['unit_text_search']['checked_coordinate_unit_matches']
    assert len(matches) == (16 if zero else 1)
    assert len({row['canonical_instance_path'] for row in matches}) == 1
    assert units.verify_geometric_unit_credential(result, case['scope'])['dimensions'] == result['dimensions']


@pytest.mark.parametrize('attribute', ['paper', 'scale', 'title', 'si'])
@pytest.mark.parametrize('zero', [False, True])
def test_selected_attrib_native_parent_placement_gets_cell_offset_only_once(case, attribute, zero):
    selected = selected_array(case, attribute=attribute, negative=True, zero=zero)
    result = credential(case)
    context = (result['frame']['paper_size_source_evidence'][0]['source_context'] if attribute == 'paper' else
               result['frame']['scale_source_context'] if attribute == 'scale' else
               result['view_source_contexts'][0] if attribute == 'title' else
               result['coordinate_unit_evidence'][0]['source_context'])
    native_attr = next(a for a in selected['native_child'].attribs if a.dxf.tag == attribute.upper())
    expected = selected['native_root'].matrix44().transform(native_attr.dxf.insert)
    assert context['placement_points_wcs'][0] == pytest.approx(tuple(expected))
    assert context['placement_rule'] == 'ATTRIB_parent_coordinates_plus_own_cell_offset_once_then_ancestors'
    assert context['source_ref']['handle'] == selected['refs'][attribute]['handle']
    bad = copy.deepcopy(case['request'])
    location = (bad['paper_size_text_refs'][0] if attribute == 'paper' else bad['scale_text'] if attribute == 'scale'
                else bad['view_text_refs'][0] if attribute == 'title' else bad['coordinate_unit_refs'][0])
    location['insert_handles'].pop()
    with pytest.raises(ValueError, match='源实体句柄|INSERT'):
        check(case, bad)


@pytest.mark.parametrize('target', ['frame', 'paper', 'dim', 'scale', 'title', 'si'])
@pytest.mark.parametrize('mutation', ['missing_cell', 'out_of_range', 'leading_zero', 'missing_parent', 'wrong_cell'])
def test_selected_real_array_refs_reject_false_or_misplaced_identity(case, target, mutation):
    selected = selected_array(case)
    ref = selected['refs'][target]
    if mutation == 'missing_cell': ref['insert_handles'][0] = selected['root'].dxf.handle
    elif mutation == 'out_of_range': ref['insert_handles'][0] = selected['root'].dxf.handle+'[2,0]'
    elif mutation == 'leading_zero': ref['insert_handles'][0] = selected['root'].dxf.handle+'[01,1]'
    elif mutation == 'missing_parent': ref['insert_handles'].pop(0)
    else: ref['insert_handles'][0] = selected['root'].dxf.handle+'[0,0]'
    with pytest.raises(ValueError): check(case)


@pytest.mark.parametrize('target', ['frame', 'paper', 'dim', 'scale', 'title', 'si'])
def test_zero_spacing_selected_alias_rejected_but_every_declared_cell_scanned(case, target):
    selected = selected_array(case, zero=True)
    selected['refs'][target]['insert_handles'][1] = selected['child'].dxf.handle+'[0,1]'
    with pytest.raises(ValueError, match='零间距cell'):
        check(case)
    search = units._search_unit_texts(case['doc'], units._frame(case['doc'],
        dict(entity_refs=[dict(handle=case['handles']['frame'], insert_handles=[selected['root'].dxf.handle+'[0,0]',
            selected['child'].dxf.handle+'[0,0]'])]))[0])
    assert len(search['checked_array_cells']) == 20


def test_case_normalized_native_dimension_identity_cannot_be_duplicated(case):
    selected = selected_array(case)
    original = case['request']['dimension_refs'][0]
    lower = dict(handle=original['handle'].lower(), insert_handles=[p.lower() for p in original['insert_handles']])
    case['request']['dimension_refs'].append(lower)
    with pytest.raises(ValueError, match='不重复'):
        check(case)


def test_tiny_nonzero_source_spacing_preserves_distinct_identity_despite_near_coincidence(case):
    selected = selected_array(case)
    selected['child'].dxf.row_spacing = selected['child'].dxf.column_spacing = 1e-9
    save_source(case)
    first = copy.deepcopy(selected['refs']['si']); second = copy.deepcopy(first)
    first['insert_handles'][1] = selected['child'].dxf.handle+'[0,0]'
    second['insert_handles'][1] = selected['child'].dxf.handle+'[1,1]'
    _, a = units._ref(case['doc'], first); _, b = units._ref(case['doc'], second)
    assert a.source_context['instance_path'] != b.source_context['instance_path']
    assert b.source_context['insert_chain'][-1]['row_spacing'] == 1e-9
    frame = copy.deepcopy(case['request']['frame'])
    box = units._frame(case['doc'], frame)[0]
    search = units._search_unit_texts(case['doc'], box)
    matches = [r for r in search['unit_text_matches'] if r['handle'] == first['handle']]
    assert len(matches) == 4 and len({r['canonical_instance_path'] for r in matches}) == 4


@pytest.mark.parametrize('mutation', ['nonxy', 'nonuniform', 'zmirror', 'dynamic', 'clip', 'override', 'negative_lfac'])
def test_new_selected_arrays_do_not_relax_existing_unsupported_contexts(case, mutation):
    selected = selected_array(case)
    child = selected['child']
    if mutation == 'nonxy': child.dxf.extrusion = (0, 1, 0)
    elif mutation == 'nonuniform': child.dxf.yscale = 2
    elif mutation == 'zmirror': child.dxf.zscale = -1
    elif mutation in ('dynamic', 'clip'):
        child.new_extension_dict().add_dictionary('ACAD_ENHANCEDBLOCK' if mutation == 'dynamic' else 'ACAD_FILTER')
    elif mutation == 'override': case['doc'].entitydb[case['handles']['dim']].dxf.text = '6000'
    else:
        style = case['doc'].entitydb[case['handles']['dim']].override()
        style.update(dict(dimlfac=-1)); style.commit()
    save_source(case)
    with pytest.raises(ValueError): check(case)


@pytest.mark.parametrize('mutation', ['opaque', 'unknown', 'conflict', 'component_size'])
def test_selected_array_unread_content_or_inapplicable_unit_keeps_null(case, mutation):
    selected = selected_array(case)
    if mutation in ('opaque', 'unknown'):
        selected['leaf'].new_entity('OLE2FRAME' if mutation == 'opaque' else 'TOLERANCE', {})
    elif mutation == 'conflict': selected['leaf'].add_text('坐标单位：米', dxfattribs={'insert': (3000, 9000)})
    else:
        case['doc'].entitydb[selected['refs']['si']['handle']].dxf.text = '500×150mm H=3.0'
    save_source(case)
    with pytest.raises(ValueError) as caught: check(case)
    if mutation != 'component_size':
        partial = caught.value.partial_evidence
        assert partial['raw_unit'] is partial['metres_per_drawing_unit'] is partial['length'] is None
        assert partial['allows_length_measurement'] is False
    else:
        assert '坐标/绘图单位原文' in str(caught.value)


def test_no_coordinate_clause_remains_geometric_only_and_replay_checks_source_scope(case):
    selected = selected_array(case)
    selected['leaf'].delete_entity(case['doc'].entitydb[selected['refs']['si']['handle']])
    case['request']['coordinate_unit_refs'] = []
    save_source(case)
    result = credential(case)
    assert result['physical_unit_basis'] is None and result['allows_length_measurement'] is False
    with pytest.raises(units.GeometricUnitUnresolved) as caught:
        units.verify_geometric_unit_consumption(result, case['manifest'], case['scope'], .001)
    assert caught.value.partial_evidence['raw_unit'] is None
    changed = copy.deepcopy(case['scope']); changed['bounds']['xmin'] += 1
    with pytest.raises(ValueError, match='scope'):
        units.verify_geometric_unit_credential(result, changed)
    before = case['path'].read_bytes()
    case['path'].write_bytes(before+b'\n')
    with pytest.raises(ValueError): units.verify_geometric_unit_credential(result)
    case['path'].write_bytes(before)
    # Additive output context is never trusted as source fact.
    result['frame']['source_entities'][0]['placement_points_wcs'][0][0] += 999999
    replayed = units.verify_geometric_unit_credential(result)
    assert replayed['frame']['source_entities'] != result['frame']['source_entities']


@pytest.mark.parametrize('mode', ['positive', 'negative_attribute', 'zero', 'opaque', 'conflict'])
def test_fresh_public_selected_array_credential_workbench_infer(case, mode):
    root_folder = Path(os.environ.get('CAD_QTO_BATCH15_UNIT_COMBINATION_ROOT') or Path(__file__).resolve().parents[1])
    shared = root_folder/'skills/_qto-shared-evidence/scripts'
    pipe = root_folder/'skills/qto-pipe-networks/scripts'
    selected = selected_array(case, negative=mode == 'negative_attribute', zero=mode == 'zero',
                              attribute='paper' if mode == 'negative_attribute' else None)
    if mode == 'opaque': selected['leaf'].new_entity('OLE2FRAME', {})
    elif mode == 'conflict': selected['leaf'].add_text('坐标单位：米', dxfattribs={'insert': (3000, 9000)})
    for block in case['doc'].blocks:
        for solid in list(block.query('SOLID')): block.delete_entity(solid)
    save_source(case); original_bytes = case['path'].read_bytes()
    exits = {}
    def run(name, script, *args):
        result = command(script, *args)
        exits[name] = result.returncode
        put(case['tmp']/'actual-selected-array-exits.json', exits)
        (case['tmp']/(name+'.stdout.log')).write_text(result.stdout, encoding='utf-8')
        (case['tmp']/(name+'.stderr.log')).write_text(result.stderr, encoding='utf-8')
        return result
    neutral = case['tmp']/'fresh-neutral'
    take = run('extract', shared/'extract_cad_neutral.py', '--source-dxf', case['path'],
               '--all-blocks', '--output-dir', neutral)
    assert take.returncode in (0, 3), take.stdout+take.stderr
    mp = neutral/'CAD中性取证清单.json'; mf = json.loads(mp.read_text(encoding='utf-8'))
    rp = put(case['tmp']/'unit-request.json', case['request'])
    sp = put(case['tmp']/'scope.json', case['scope']); cp = case['tmp']/'credential.json'
    produced = run('public_unit', shared/'qto_geometric_unit_evidence.py', '--cad-manifest', mp,
        '--request', rp, '--scope', sp, '--output', cp)
    if mode in ('opaque', 'conflict'):
        assert produced.returncode == 2, produced.stdout+produced.stderr
        unresolved = json.loads(cp.read_text(encoding='utf-8'))
        assert unresolved['raw_unit'] is unresolved['metres_per_drawing_unit'] is unresolved['length'] is None
        assert unresolved['allows_length_measurement'] is False
        return
    assert produced.returncode == 0, produced.stdout+produced.stderr
    cred = json.loads(cp.read_text(encoding='utf-8'))
    assert cred['allows_length_measurement'] and cred['frame']['source_entities'][0]['insert_chain']
    # Public producer rejects wrong cell / aliases / missing parent as actual DXF refs.
    for mutation in ('wrong_cell', 'alias', 'missing_parent'):
        bad = copy.deepcopy(case['request']); ref = bad['dimension_refs'][0]
        if mutation == 'wrong_cell': ref['insert_handles'][0] = selected['root'].dxf.handle+'[2,0]'
        elif mutation == 'alias': ref['insert_handles'][0] = selected['root'].dxf.handle+('[0,1]' if mode == 'zero' else '[01,1]')
        else: ref['insert_handles'].pop(0)
        bp = put(case['tmp']/(mutation+'-request.json'), bad); out = case['tmp']/(mutation+'-credential.json')
        rejected = run(mutation, shared/'qto_geometric_unit_evidence.py', '--cad-manifest', mp,
            '--request', bp, '--scope', sp, '--output', out)
        assert rejected.returncode == 2 and not out.exists()
    source = put(case['tmp']/'fresh-source.json', mf['exchange_dxf']); ip = case['tmp']/'index.json'
    bounds = case['scope']['bounds']; values = [bounds[k] for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')]
    ix = run('index', shared/'qto_recognition_workbench.py', 'index', '--source', source,
             '--bounds', *values, '--output', ip)
    assert ix.returncode == 0, ix.stdout+ix.stderr
    index = json.loads(ip.read_text(encoding='utf-8'))
    objects = [o for o in index['objects'] if o.get('source_handle') == selected['route'].dxf.handle]
    assert len(objects) == 1
    note = next(o for o in index['objects'] if o.get('source_handle') == selected['dn'].dxf.handle)
    ids = [objects[0]['object_id'], note['object_id']]; authority = index['authoritative_source']
    ledger = dict(schema_version='qto-model-recognition-ledger/1.0', ledger_id='SELECTED_ARRAY', ledger_version='1',
        source_sha256=authority['sha256'], adoption_scope='working', review_status='unreviewed',
        evidence=[dict(evidence_id='SRC', path=authority['path'], sha256=authority['sha256'],
            source_sha256=authority['sha256'], kind='cad_property', locator='selected complete array path', observation='native source route and DN')],
        entries=[dict(adjudication_id='PIPE', subject_id='selected-array', professional_owner='qto-pipe-networks', source_id='SRC',
            recognized_by='test-model', recognized_at='2026-10-20', basis='source array path', scope_basis='selected source instance',
            identity_basis='complete native path', cross_check='source line and DN', decision='include', decision_origin='model',
            status='active', object_ids=ids, evidence_ids=['SRC'], uncertainties=[])])
    lp = put(case['tmp']/'ledger.json', ledger); oid = ids[0]
    relations = [dict(relation_id='DN',kind='dn_annotation',target_object_id=oid,annotation_object_id=note['object_id'],
        annotation_text='DN25',dn='DN25',adjudication_id='PIPE',observation='actual source DN'),
        dict(relation_id='ROOT',kind='root',target=dict(object_id=oid,end='start'),adjudication_id='PIPE',observation='actual source start'),
        dict(relation_id='END',kind='terminal',target=dict(object_id=oid,end='end'),terminal_type='actual source end',
             port_dn='DN25',port_dn_annotation_object_ids=[note['object_id']],adjudication_id='PIPE',observation='actual source end')]
    ops = dict(index_sha256=index['index_sha256'],network_id='SELECTED_ARRAY',scope_object_ids=ids,scope_basis='one actual array source',
        unit='m',geometric_unit_evidence=cred,measurement_scope=case['scope'],factor_to_output=.001,
        parameters=dict(snap_tolerance=.0001,gap_max=.1),parameter_basis='source finite geometry',
        assignments=[dict(adjudication_id='PIPE',object_ids=[oid],role='pipe_centerline',system='W',observation='actual source line')],
        relations=relations,endpoint_review=dict(complete=False,basis='selected source line'))
    op = put(case['tmp']/'operations.json',ops); prepared = case['tmp']/'network-input.json'
    apply = run('apply', shared/'qto_recognition_workbench.py','apply','--index',ip,'--operations',op,'--ledger',lp,'--output',prepared)
    assert apply.returncode == 0, apply.stdout+apply.stderr
    output = case['tmp']/'network-result.json'
    infer = run('infer',pipe/'infer_pipe_network.py','--input',prepared,'--output',output)
    assert infer.returncode == 0, infer.stdout+infer.stderr
    actual = json.loads(output.read_text(encoding='utf-8'))
    assert len(actual['segments']) == 1 and actual['segments'][0]['length'] == pytest.approx(10.)
    case['path'].write_bytes(original_bytes+b'\n')
    stale_out = case['tmp']/'stale-result.json'
    stale = run('source_drift',pipe/'infer_pipe_network.py','--input',prepared,'--output',stale_out)
    assert stale.returncode == 2 and not stale_out.exists()
    case['path'].write_bytes(original_bytes)
    bad = json.loads(prepared.read_text(encoding='utf-8')); bad['measurement_scope']['bounds']['xmin'] += 1
    bp = put(case['tmp']/'wrong-scope-input.json',bad); scope_out = case['tmp']/'wrong-scope-result.json'
    changed = run('scope_drift',pipe/'infer_pipe_network.py','--input',bp,'--output',scope_out)
    assert changed.returncode == 2 and not scope_out.exists()
    restored = run('restored',pipe/'infer_pipe_network.py','--input',prepared,'--output',case['tmp']/'restored.json')
    assert restored.returncode == 0, restored.stdout+restored.stderr
