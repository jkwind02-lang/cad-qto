"""Inspect every actual array cell; unknown content cannot become unit absence."""
import copy
import json
import math
import os
from pathlib import Path

import pytest

from test_geometric_units_t27_20261019 import case, check, credential, save_source, units, sha, put, command


def nested(case, *, unit_text=None, zero=False):
    doc = case['doc']; leaf = doc.blocks.new('ARRAY_LEAF')
    line = leaf.add_line((0, 0), (1000, 0), dxfattribs={'layer': 'ROUTE'})
    if 'ROUTE' not in doc.layers: doc.layers.new('ROUTE')
    text = leaf.add_text(unit_text, dxfattribs={'insert': (1000, 1000), 'height': 100}) if unit_text else None
    outer = doc.blocks.new('ARRAY_OUTER')
    child = outer.add_blockref(leaf.name, (1000, 0), dxfattribs=dict(rotation=15,
        row_count=2, column_count=2, row_spacing=0 if zero else -3000, column_spacing=0 if zero else 4000))
    root = doc.modelspace().add_blockref(outer.name, (30000, 30000), dxfattribs=dict(rotation=30,
        row_count=2, column_count=2, row_spacing=0 if zero else -8000, column_spacing=0 if zero else 15000))
    return root, child, leaf, line, text


@pytest.mark.parametrize('zero', [False, True])
def test_complete_nested_cells_negative_spacing_rotation_and_zero_spacing_are_not_deduped(case, zero):
    root, child, _, _, _ = nested(case, zero=zero); save_source(case)
    result = check(case); search = result['unit_text_search']
    assert search['status'] == 'COMPLETE' and not search['uninspected_active_text_carriers']
    cells = search['checked_array_cells']
    assert len(cells) == 20  # four parent cells plus four children under each
    expected = {f'/{root.dxf.handle}[{r},{c}]/{child.dxf.handle}[{i},{j}]'
                for r in range(2) for c in range(2) for i in range(2) for j in range(2)}
    assert expected <= {r['instance_path'] for r in cells}
    if not zero:
        offset = next(r['offset_in_parent_coordinates'] for r in cells
                      if r['instance_path'] == f'/{root.dxf.handle}[1,1]')
        assert offset == pytest.approx([15000*math.cos(math.pi/6)+4000,
                                        7500-8000*math.cos(math.pi/6), 0])
    assert result['physical_unit_basis'] is None and result['allows_length_measurement'] is False


def test_si_inside_array_requires_every_real_in_frame_instance_and_exact_cell_chain(case):
    root, child, _, _, text = nested(case, unit_text='坐标单位：毫米')
    refs = [dict(handle=text.dxf.handle, insert_handles=[f'{root.dxf.handle}[{r},{c}]', f'{child.dxf.handle}[{i},{j}]'])
            for r in range(2) for c in range(2) for i in range(2) for j in range(2)]
    case['request']['coordinate_unit_refs'] = refs; save_source(case)
    result = credential(case)
    assert result['physical_unit_basis'] == 'source_coordinate_declaration'
    assert len(result['coordinate_unit_evidence']) == 16
    assert result['allows_length_measurement'] is True
    for mutation in ('bare', 'out_of_range', 'alias', 'missing_parent', 'not_all_instances'):
        req = copy.deepcopy(case['request'])
        if mutation == 'bare': req['coordinate_unit_refs'][0]['insert_handles'][0] = root.dxf.handle
        elif mutation == 'out_of_range': req['coordinate_unit_refs'][0]['insert_handles'][0] = root.dxf.handle+'[2,0]'
        elif mutation == 'alias': req['coordinate_unit_refs'][0]['insert_handles'][0] = root.dxf.handle+'[00,0]'
        elif mutation == 'missing_parent': req['coordinate_unit_refs'][0]['insert_handles'].pop(0)
        else: req['coordinate_unit_refs'].pop()
        with pytest.raises(ValueError): check(case, req)


@pytest.mark.parametrize('carrier', ['OLE2FRAME', 'ACAD_PROXY_ENTITY', 'TOLERANCE', 'xref', 'missing', 'cycle', 'dynamic'])
def test_unknown_content_in_each_nested_cell_stays_unresolved(case, carrier):
    root, child, leaf, _, _ = nested(case)
    if carrier in ('OLE2FRAME', 'ACAD_PROXY_ENTITY', 'TOLERANCE'): leaf.new_entity(carrier, {})
    elif carrier == 'xref':
        refblock = case['doc'].blocks.new('UNREAD_XREF'); refblock.block.dxf.flags = 4
        leaf.add_blockref('UNREAD_XREF', (0, 0))
    elif carrier == 'missing': leaf.add_blockref('UNREAD_MISSING', (0, 0))
    elif carrier == 'cycle': leaf.add_blockref(leaf.name, (0, 0))
    else: leaf.block_record.new_extension_dict().add_dictionary('ACAD_ENHANCEDBLOCK')
    search = units._search_unit_texts(case['doc'], units._frame(case['doc'], case['request']['frame'])[0])
    assert search['status'] == 'UNRESOLVED'
    paths = [r['instance_path'] for r in search['uninspected_active_text_carriers']]
    assert any(f'/{root.dxf.handle}[1,1]/{child.dxf.handle}[1,1]' in p for p in paths)


@pytest.mark.parametrize('kind', ['conflict', 'opaque', 'attribute'])
def test_negative_spacing_second_cell_inside_cannot_be_hidden_by_first_cell_outside(case, kind):
    doc = case['doc']; block = doc.blocks.new('FAR_TO_NEAR')
    if kind == 'opaque': block.new_entity('OLE2FRAME', {})
    else: block.add_text('坐标单位：米', dxfattribs={'insert': (0, 0), 'height': 100})
    root = doc.modelspace().add_blockref(block.name, (100000, 30000),
        dxfattribs=dict(column_count=2, column_spacing=-60000))
    if kind == 'attribute':
        block.delete_entity(block.query('TEXT')[0])
        root.add_attrib('SI', '坐标单位：米', insert=(100000, 30000), dxfattribs={'height': 100})
    search = units._search_unit_texts(doc, units._frame(doc, case['request']['frame'])[0])
    rows = search['uninspected_active_text_carriers'] if kind == 'opaque' else search['unit_text_matches']
    assert any(f'/{root.dxf.handle}[0,1]' in r['instance_path'] for r in rows)
    if kind != 'opaque':
        assert any(f'/{root.dxf.handle}[0,0]' in r['instance_path'] for r in search['excluded_outside_frame'])
    assert search['status'] != 'COMPLETE'


@pytest.mark.parametrize('mutation', ['nonxy', 'nonfinite', 'over_limit'])
def test_unrecovered_array_context_does_not_claim_complete_unit_scan(case, mutation):
    root, _, _, _, _ = nested(case)
    if mutation == 'nonxy': root.dxf.extrusion = (0, 1, 0)
    elif mutation == 'nonfinite': root.dxf.row_spacing = float('inf')
    else: root.dxf.row_count = root.dxf.column_count = 1000
    search = units._search_unit_texts(case['doc'], units._frame(case['doc'], case['request']['frame'])[0])
    assert search['status'] == 'UNRESOLVED'
    assert any(r['handle'] == root.dxf.handle and r['bbox'] is None
               for r in search['uninspected_active_text_carriers'])


def fresh_array_production(case, root_folder, *, invalid=None):
    shared = root_folder/'skills/_qto-shared-evidence/scripts'
    pipe = root_folder/'skills/qto-pipe-networks/scripts'
    root, child, leaf, route, _ = nested(case)
    model = case['doc'].modelspace()
    clause = model.add_text('坐标单位：毫米', dxfattribs={'insert': (1000, 6000), 'height': 100})
    dn = model.add_text('DN25', dxfattribs={'insert': (1000, 5000), 'height': 100})
    if invalid == 'opaque': leaf.new_entity('OLE2FRAME', {})
    elif invalid == 'conflict': leaf.add_text('坐标单位：米', dxfattribs={'insert': (0, 0), 'height': 100})
    case['request']['coordinate_unit_refs'] = [dict(handle=clause.dxf.handle)]
    for block in case['doc'].blocks:
        for solid in list(block.query('SOLID')): block.delete_entity(solid)
    save_source(case); source_bytes = case['path'].read_bytes()
    neutral = case['tmp']/'fresh-neutral'
    take = command(shared/'extract_cad_neutral.py', '--source-dxf', case['path'], '--all-blocks', '--output-dir', neutral)
    assert take.returncode in (0, 3), take.stdout+take.stderr
    mp = neutral/'CAD中性取证清单.json'; mf = json.loads(mp.read_text(encoding='utf-8'))
    rp = put(case['tmp']/'fresh-unit-request.json', case['request']); sp = put(case['tmp']/'fresh-scope.json', case['scope'])
    cp = case['tmp']/'fresh-credential.json'
    result = command(shared/'qto_geometric_unit_evidence.py', '--cad-manifest', mp, '--request', rp, '--scope', sp, '--output', cp)
    exits = dict(extract=take.returncode, public_unit=result.returncode)
    if invalid:
        assert result.returncode == 2, result.stdout+result.stderr
        partial = json.loads(cp.read_text(encoding='utf-8'))
        assert partial['raw_unit'] is None and partial['allows_length_measurement'] is False
        put(case['tmp']/'actual-array-exits.json', exits)
        return
    assert result.returncode == 0, result.stdout+result.stderr
    cred = json.loads(cp.read_text(encoding='utf-8'))
    assert len(cred['unit_text_search']['checked_array_cells']) == 20
    source = put(case['tmp']/'fresh-source.json', mf['exchange_dxf']); ip = case['tmp']/'fresh-index.json'
    bounds = case['scope']['bounds']; values = [bounds[k] for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')]
    ix = command(shared/'qto_recognition_workbench.py', 'index', '--source', source, '--bounds', *values, '--output', ip)
    assert ix.returncode == 0, ix.stdout+ix.stderr
    index = json.loads(ip.read_text(encoding='utf-8'))
    objects = [o for o in index['objects'] if o.get('source_handle') == route.dxf.handle]
    assert len(objects) == 16
    note = next(o for o in index['objects'] if o.get('source_handle') == dn.dxf.handle)
    ids = [o['object_id'] for o in objects]+[note['object_id']]; authority = index['authoritative_source']
    ledger = dict(schema_version='qto-model-recognition-ledger/1.0', ledger_id='ARRAY', ledger_version='1',
        source_sha256=authority['sha256'], adoption_scope='working', review_status='unreviewed',
        evidence=[dict(evidence_id='SRC', path=authority['path'], sha256=authority['sha256'],
            source_sha256=authority['sha256'], kind='cad_property', locator='complete actual array paths', observation='source lines and DN')],
        entries=[dict(adjudication_id='PIPE', subject_id='array', professional_owner='qto-pipe-networks', source_id='SRC',
            recognized_by='test-model', recognized_at='2026-10-19', basis='actual array cells', scope_basis='selected actual instances',
            identity_basis='complete source paths', cross_check='source line and DN', decision='include', decision_origin='model',
            status='active', object_ids=ids, evidence_ids=['SRC'], uncertainties=[])])
    lp = put(case['tmp']/'fresh-ledger.json', ledger); relations = []
    for number, obj in enumerate(objects):
        oid = obj['object_id']
        relations.extend([
            dict(relation_id=f'DN{number}', kind='dn_annotation', target_object_id=oid, annotation_object_id=note['object_id'],
                 annotation_text='DN25', dn='DN25', adjudication_id='PIPE', observation='actual DN text'),
            dict(relation_id=f'ROOT{number}', kind='root', target=dict(object_id=oid,end='start'), adjudication_id='PIPE', observation='actual cell source start'),
            dict(relation_id=f'END{number}', kind='terminal', target=dict(object_id=oid,end='end'), terminal_type='actual source port',
                 port_dn='DN25',port_dn_annotation_object_ids=[note['object_id']],adjudication_id='PIPE',observation='actual source end')])
    ops = dict(index_sha256=index['index_sha256'],network_id='ARRAY',scope_object_ids=ids,scope_basis='actual source arrays',
        unit='m',geometric_unit_evidence=cred,measurement_scope=case['scope'],factor_to_output=.001,
        parameters=dict(snap_tolerance=.0001,gap_max=.1),parameter_basis='source finite geometry',
        assignments=[dict(adjudication_id='PIPE',object_ids=ids[:-1],role='pipe_centerline',system='W',observation='actual source lines')],
        relations=relations,endpoint_review=dict(complete=False,basis='selected array lines'))
    op = put(case['tmp']/'fresh-operations.json',ops); prepared = case['tmp']/'fresh-network-input.json'
    apply = command(shared/'qto_recognition_workbench.py','apply','--index',ip,'--operations',op,'--ledger',lp,'--output',prepared)
    assert apply.returncode == 0, apply.stdout+apply.stderr
    out = case['tmp']/'fresh-network-result.json'
    infer = command(pipe/'infer_pipe_network.py','--input',prepared,'--output',out)
    assert infer.returncode == 0, infer.stdout+infer.stderr
    actual = json.loads(out.read_text(encoding='utf-8'))
    assert len(actual['segments']) == 16
    assert sum(r['length'] for r in actual['segments']) == pytest.approx(16.)
    exits.update(index=ix.returncode,apply=apply.returncode,infer=infer.returncode)
    # Source and context drift must reject before another quantity artifact.
    case['path'].write_bytes(source_bytes+b'\n')
    stale = command(pipe/'infer_pipe_network.py','--input',prepared,'--output',case['tmp']/'stale-result.json')
    assert stale.returncode == 2 and not (case['tmp']/'stale-result.json').exists()
    case['path'].write_bytes(source_bytes)
    bad = json.loads(prepared.read_text(encoding='utf-8')); bad['measurement_scope']['bounds']['xmin'] += 1
    bp = put(case['tmp']/'wrong-scope-input.json',bad)
    changed = command(pipe/'infer_pipe_network.py','--input',bp,'--output',case['tmp']/'wrong-scope-result.json')
    assert changed.returncode == 2 and not (case['tmp']/'wrong-scope-result.json').exists()
    module = shared/'qto_geometric_unit_evidence.py'; module_bytes = module.read_bytes()
    try:
        module.write_bytes(module_bytes+b'\n# isolated array drift control\n')
        drift = command(pipe/'infer_pipe_network.py','--input',prepared,'--output',case['tmp']/'module-drift-result.json')
        assert drift.returncode == 2 and not (case['tmp']/'module-drift-result.json').exists()
    finally: module.write_bytes(module_bytes)
    restored = command(pipe/'infer_pipe_network.py','--input',prepared,'--output',case['tmp']/'restored-result.json')
    assert restored.returncode == 0, restored.stdout+restored.stderr
    exits.update(source_drift=stale.returncode,scope_drift=changed.returncode,module_drift=drift.returncode,restored=restored.returncode)
    put(case['tmp']/'actual-array-exits.json',exits)


@pytest.mark.parametrize('invalid', [None, 'opaque', 'conflict'])
def test_fresh_public_si_array_workbench_infer_production_and_rejection(case, invalid):
    path = os.environ.get('CAD_QTO_BATCH14_ARRAY_COMBINATION_ROOT')
    if not path: pytest.skip('requires explicit isolated combination with package25 owned workbench/infer')
    fresh_array_production(case, Path(path), invalid=invalid)
