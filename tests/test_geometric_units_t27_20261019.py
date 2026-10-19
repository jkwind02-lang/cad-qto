"""T27: physical scale is inferred from original source, never self-entered spans."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
PIPE = ROOT / 'skills/qto-pipe-networks/scripts'
sys.path.insert(0, str(SHARED))
import extract_cad_neutral as extract
import qto_geometric_unit_evidence as units
from model_recognition_basis import recognition_basis


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return Path(path)


def command(script, *args):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    return subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', env=env)


@pytest.fixture
def case(tmp_path):
    doc = ezdxf.new('R2010'); doc.units = 0; m = doc.modelspace()
    frame = m.add_lwpolyline([(0, 0), (84100, 0), (84100, 59400), (0, 59400)], close=True)
    scale = m.add_text('1:100', dxfattribs={'insert': (80000, 2000)})
    title = m.add_text('首层给水平面图', dxfattribs={'insert': (1000, 2000)})
    pipe = m.add_line((10000, 30000), (20000, 30000), dxfattribs={'layer': 'PIPE'})
    dim = m.add_aligned_dim((10000, 10000), (16000, 10000), distance=1000,
                            override={'dimlfac': 1., 'dimscale': 5.})
    dim.render()
    path = tmp_path / 'source.dxf'; doc.saveas(path)
    h = sha(path)
    layout_rows = [extract.convert(e, layout=l.name, parent_block=None, source_hash=h, raw_unit='Unitless', sequence=i)
                   for l in doc.layouts for i, e in enumerate(l, 1)]
    block_rows = [extract.convert(e, layout='Model', parent_block=b.name, source_hash=h, raw_unit='Unitless', sequence=i)
                  for b in doc.blocks if not b.is_any_layout for i, e in enumerate(b, 1)]
    stream = tmp_path / 'layout.ndjson'; blocks = tmp_path / 'blocks.ndjson'
    for target, rows in ((stream, layout_rows), (blocks, block_rows)):
        target.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    manifest = dict(schema_version='qto-cad-neutral-evidence/1.0', geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(path), sha256=h), exchange_dxf=dict(path=str(path), sha256=h),
        drawing_unit=dict(insunits_code=0, raw_unit='Unitless'), blocks=[dict(block_name=b.name, origin=list(b.base_point)) for b in doc.blocks if not b.is_any_layout],
        artifacts={'layout_entities': dict(path=str(stream), sha256=sha(stream)), 'block_definitions': dict(path=str(blocks), sha256=sha(blocks))})
    request = dict(schema_version=units.REQUEST_SCHEMA, raw_unit='Millimeters', measurement_semantics='plan_to_scale',
        frame=dict(entity_refs=[dict(handle=frame.dxf.handle)]), scale_text=dict(handle=scale.dxf.handle),
        dimension_refs=[dict(handle=dim.dimension.dxf.handle)], view_text_refs=[dict(handle=title.dxf.handle)],
        basis='匿名原源图框外框、同图签比例和内部尺寸；仅作为规则推算')
    scope = dict(layout='Model', boundary_policy='contained', bounds=dict(xmin=0., ymin=0., xmax_exclusive=84100., ymax_exclusive=59400.))
    mp = put(tmp_path/'manifest.json', manifest)
    return dict(tmp=tmp_path, doc=doc, path=path, manifest=manifest, manifest_path=mp, request=request, scope=scope,
                handles={k:v.dxf.handle for k,v in dict(frame=frame,scale=scale,title=title,pipe=pipe,dim=dim.dimension).items()}, stream=stream)


def save_source(case):
    case['doc'].saveas(case['path'])
    for key in ('authoritative_source', 'exchange_dxf'):
        case['manifest'][key]['sha256'] = sha(case['path'])
    put(case['manifest_path'], case['manifest'])


def check(case, request=None, scope=None):
    return units.verify_geometric_unit_evidence(request or case['request'], case['manifest'], scope or case['scope'])


def credential(case):
    result = check(case)
    result['cad_manifest'] = dict(path=str(case['manifest_path']), sha256=sha(case['manifest_path']))
    return result


def bind_actual_recognition(case, data):
    ledger = dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='ACTUAL',ledger_version='1',
        source_sha256=case['manifest']['authoritative_source']['sha256'],adoption_scope='working',review_status='unreviewed',
        evidence=[dict(evidence_id='CAD',path=str(case['manifest_path']),sha256=sha(case['manifest_path']),
                       source_sha256=case['manifest']['authoritative_source']['sha256'],kind='cad_property',locator='actual source',observation='actual CAD snapshot')],
        entries=[dict(adjudication_id='P',subject_id='P',decision='include',status='active',professional_owner='qto-pipe-networks',
                      decision_origin='model',recognized_by='test-model',recognized_at='2026-10-19',source_id='CAD',evidence_ids=['CAD'],
                      object_ids=['CAD-'+case['handles']['pipe']],basis='actual source pipe',scope_basis='Model',
                      identity_basis='actual source handle',cross_check='actual original',uncertainties=['working'])])
    path=put(case['tmp']/'actual-ledger.json',ledger)
    data['recognition_basis']=recognition_basis(ledger,path)
    return data


def test_original_source_two_classes_and_dimscale_is_only_display(case):
    result = credential(case)
    assert result['frame']['checked_axes'] == 2
    assert result['frame']['paper'] == 'A1'
    assert result['dimensions'][0]['measurement'] == pytest.approx(6000.)
    assert result['dimensions'][0]['dimscale'] == 5.
    assert result['dimensions'][0]['physical_unit_proven_independently'] is False
    assert result['unit_text_search']['text_count'] >= 3
    assert result['evidence_level'] == 'inferred' and result['formal_adoption'] is False
    assert units.verify_geometric_unit_credential(result, case['scope'])['raw_unit'] == 'Millimeters'


def test_system_plan_title_is_a_plan_not_a_schematic(case):
    case['doc'].entitydb[case['handles']['title']].dxf.text='三层火灾自动报警系统平面'
    save_source(case)
    assert check(case)['status']=='INFERRED'


@pytest.mark.parametrize('target,state',[('frame','hidden'),('scale','off'),('dim','frozen'),('render','hidden'),('dim','annotative')])
def test_selected_evidence_must_be_visible_in_current_model(case,target,state):
    doc=case['doc'];entity=doc.entitydb[case['handles'][target]] if target!='render' else doc.blocks[doc.entitydb[case['handles']['dim']].dxf.geometry].query('MTEXT')[0]
    if state=='hidden':entity.dxf.invisible=1
    elif state in ('off','frozen'):
        layer=doc.layers.new('HIDDEN');entity.dxf.layer='HIDDEN'
        layer.off() if state=='off' else layer.freeze()
    else:
        doc.appids.new('AcadAnnotative');entity.set_xdata('AcadAnnotative',[(1000,'AnnotativeData')])
    save_source(case)
    with pytest.raises(ValueError,match='可见|注释性'):
        check(case)


@pytest.mark.parametrize('mutation,match', [
    ('one_class','第二类'), ('fake_two_numbers','引用'), ('one_axis','两轴'),
    ('dimlfac','DIMLFAC'), ('override','无覆盖'), ('render_mismatch','渲染数值'),
    ('no_render','渲染文字'), ('unit_text','单位文字'),
    ('system','系统'), ('wrong_scale','两轴'), ('known_insunits','INSUNITS'),
    ('paper_space','纸空间'), ('outside_scope','其他图框'), ('dimension_frame','DIMENSION')])
def test_refuse_unproven_or_conflicting_evidence(case, mutation, match):
    req, scope = copy.deepcopy(case['request']), copy.deepcopy(case['scope'])
    doc = case['doc']; model = doc.modelspace(); dim = doc.entitydb[case['handles']['dim']]
    if mutation == 'one_class': req['dimension_refs'] = []
    elif mutation == 'fake_two_numbers': req['frame'] = dict(paper_size_mm=[841,594], measured_drawing_units=[84100,59400])
    elif mutation == 'one_axis': doc.entitydb[case['handles']['frame']].set_points([(0,0),(84100,0),(84100,60000),(0,60000)])
    elif mutation == 'dimlfac':
        style = dim.override(); style.update({'dimlfac': 2.}); style.commit()
    elif mutation == 'override': dim.dxf.text = '6000'
    elif mutation == 'render_mismatch': doc.blocks[dim.dxf.geometry].query('MTEXT')[0].text = '8000'
    elif mutation == 'no_render': dim.dxf.geometry = 'NOT-A-BLOCK'
    elif mutation == 'unit_text': model.add_text('坐标单位毫米')
    elif mutation == 'system': doc.entitydb[case['handles']['title']].dxf.text = '给水系统图'
    elif mutation == 'wrong_scale': doc.entitydb[case['handles']['scale']].dxf.text = '1:50'
    elif mutation == 'known_insunits': doc.units = 4
    elif mutation == 'paper_space': scope['layout'] = 'Layout1'
    elif mutation == 'outside_scope': scope['bounds']['xmax_exclusive'] = 168200.
    elif mutation == 'dimension_frame': req['dimension_refs'] = req['frame']['entity_refs']
    save_source(case)
    with pytest.raises(ValueError, match=match): check(case, req, scope)


def test_source_stale_and_credential_scope_not_reusable(case):
    cred = credential(case)
    other = copy.deepcopy(case['scope']); other['bounds']['xmin'] = 1.
    with pytest.raises(ValueError, match='scope'): units.verify_geometric_unit_credential(cred, other)
    case['path'].write_bytes(case['path'].read_bytes()+b'\n')
    with pytest.raises(ValueError, match='改变'): units.verify_geometric_unit_credential(cred, case['scope'])


def test_opaque_active_text_carrier_is_partial_not_a_length_credential(case):
    case['doc'].modelspace().new_entity('OLE2FRAME', {})
    save_source(case)
    with pytest.raises(units.GeometricUnitUnresolved, match='尚未核清') as caught:
        check(case)
    partial=caught.value.partial_evidence
    assert partial['frame']['checked_axes']==2 and partial['dimensions']
    assert partial['raw_unit'] is None and partial['factor_to_output'] is None and partial['length'] is None
    assert partial['allows_length_measurement'] is False
    assert partial['unit_text_search']['uninspected_active_text_carriers'][0]['bbox'] is None
    with pytest.raises(ValueError,match='无效'):
        units.verify_geometric_unit_credential(partial,case['scope'])
    req=put(case['tmp']/'request.json',case['request']);scope=put(case['tmp']/'scope.json',case['scope'])
    output=case['tmp']/'partial.json'
    p=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',case['manifest_path'],'--request',req,'--scope',scope,'--output',output)
    assert p.returncode==2
    assert json.loads(output.read_text(encoding='utf-8'))['allows_length_measurement'] is False


def test_unused_definitions_and_proven_outside_carriers_are_not_scope_unit_declarations(case):
    doc=case['doc'];unused=doc.blocks.new('UNUSED')
    unused.add_text('图纸单位米');unused.new_entity('OLE2FRAME',{})
    image=doc.add_image_def(filename='anonymous-unread-image.png',size_in_pixel=(100,100))
    doc.modelspace().add_image(image,insert=(200000,200000),size_in_units=(1000,1000))
    doc.modelspace().add_text('图纸单位米',dxfattribs={'insert':(200000,100000)})
    save_source(case)
    result=check(case)
    assert result['unit_text_search']['status']=='COMPLETE'
    assert result['unit_text_search']['whole_document_unit_text_hits']
    assert len(result['unit_text_search']['excluded_outside_frame'])==2


def test_material_and_local_elevation_units_do_not_become_coordinate_units(case):
    model=case['doc'].modelspace()
    model.add_text('材料数量10m',dxfattribs={'insert':(1000,1000)})
    model.add_text('-0.9m',dxfattribs={'insert':(1000,3000)})
    save_source(case);result=check(case)
    assert result['metres_per_drawing_unit']==.001
    assert len(result['unit_text_search']['non_coordinate_unit_texts'])==2
    assert all(t['used_to_infer_coordinate_unit'] is False for t in result['unit_text_search']['non_coordinate_unit_texts'])


def test_door_codes_and_organization_labels_are_not_unit_declarations(case):
    model=case['doc'].modelspace()
    model.add_text('M1521',dxfattribs={'insert':(1000,1000)})
    model.add_text('设计单位：匿名公司',dxfattribs={'insert':(1000,3000)})
    save_source(case);assert check(case)['unit_text_search']['unit_text_matches']==[]


def test_uniform_frame_insert_and_anisotropic_or_rotated_rejected(case):
    doc = case['doc']; model = doc.modelspace(); original = doc.entitydb[case['handles']['frame']]
    block = doc.blocks.new('SHEET'); block.add_lwpolyline([(0,0),(841,0),(841,594),(0,594)], close=True)
    model.delete_entity(original)
    ins = model.add_blockref('SHEET', (0,0), dxfattribs={'xscale':100.,'yscale':100.})
    req = copy.deepcopy(case['request']); req['frame']['entity_refs'] = [dict(handle=list(block)[0].dxf.handle,insert_handles=[ins.dxf.handle])]
    save_source(case); assert check(case,req)['frame']['checked_axes']==2
    for field,value in (('yscale',99.),('rotation',30.)):
        ins.dxf.yscale=100.; ins.dxf.rotation=0.; setattr(ins.dxf,field,value);save_source(case)
        with pytest.raises(ValueError, match='非等比|旋转'):check(case,req)


def batch_input(case, request=None):
    evidence=[dict(evidence_id='EV-source',path=str(case['stream']),sha256=sha(case['stream']),kind='cad_property',locator='anonymous Model',observation='PIPE线层的图例识别依据，仅工作识别')]
    mapping=dict(schema_version='qto-pipe-batch-family-mapping/1.0',mapping_id='ANON',mapping_version='1',
        expected_source_sha256=case['manifest']['authoritative_source']['sha256'],recognized_by='test-model',recognized_at='2026-10-19',source_id='EV-source',
        evidence=evidence,unit=dict(raw_unit='Millimeters',factor_to_output=.001,output_unit='m',source_id='EV-source',geometry_evidence_by_frame={'A': request or case['request']}),
        families=[dict(family_id='PIPE',kind='layer',layer_pattern='^PIPE$',system='W',decision='include',evidence_ids=['EV-source'],basis='匿名原图图例及平面对应')],
        use_conventional_terminal_type_candidates=False,use_conventional_valve_type_candidates=False)
    frames=dict(schema_version='qto-pipe-batch-frames/1.0',frames=[dict(frame_id='A',name='平面A',bounds=[0,0,84100,59400])])
    return put(case['tmp']/'mapping.json',mapping), put(case['tmp']/'frames.json',frames)


def test_batch_adapter_accepts_geometry_and_invalid_is_null(case):
    mapping,frames=batch_input(case)
    out=case['tmp']/'batch'
    p=command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',case['manifest_path'],'--mapping',mapping,'--frames',frames,'--output-dir',out)
    assert p.returncode==0,(p.stdout,p.stderr)
    inputs=list(out.rglob('管网推算输入.json')); assert len(inputs)==1
    data=json.loads(inputs[0].read_text(encoding='utf-8'))
    assert data['unit_evidence_level']=='inferred' and data['segments'][0]['length']==pytest.approx(10.)
    report=json.loads(next(out.rglob('管网识别报告.json')).read_text(encoding='utf-8'))
    assert report['unit']['declaration'] is None and report['unit']['geometric_evidence']['evidence_level']=='inferred'
    req=copy.deepcopy(case['request']);req['dimension_refs']=[]
    mapping,frames=batch_input(case,req); failed=case['tmp']/'failed'
    p=command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',case['manifest_path'],'--mapping',mapping,'--frames',frames,'--output-dir',failed)
    assert p.returncode!=0
    null=json.loads(next(failed.rglob('单位未决.json')).read_text(encoding='utf-8'))
    assert null['raw_unit'] is None and null['factor_to_output'] is None and null['length'] is None
    assert not list(failed.rglob('管网推算输入.json'))


def test_infer_rechecks_source_scope_and_caps_confirmed_length(case):
    data=dict(schema_version='qto-pipe-network-inference-input/1.0',network_id='ANON',unit='m',
        geometric_unit_evidence=credential(case),measurement_scope=case['scope'],
        segments=[dict(segment_id='P',system='W',start=[10,30],end=[20,30],length=10.,source_ids=['CAD-'+case['handles']['pipe']],
                       dn_seeds=[dict(dn='25',evidence_level='confirmed',source_id='ANON-DN',basis='明确标注')])])
    bind_actual_recognition(case,data)
    source=put(case['tmp']/'infer.json',data);out=case['tmp']/'result.json'
    p=command(PIPE/'infer_pipe_network.py','--input',source,'--output',out)
    assert p.returncode==0,(p.stdout,p.stderr)
    result=json.loads(out.read_text(encoding='utf-8'))
    assert result['segments'][0]['quantity_level']=='规则推算量'
    assert result['unit_evidence_level']=='inferred'
    assert '图上无单位文字' in next(case['tmp'].glob('*复核摘要.md')).read_text(encoding='utf-8')
    data['segments'][0]['start']=[1000,30];data['segments'][0]['end']=[1010,30]
    source=put(case['tmp']/'outside.json',data)
    p=command(PIPE/'infer_pipe_network.py','--input',source,'--output',case['tmp']/'outside-result.json')
    assert p.returncode!=0 and 'scope' in p.stderr


def test_cli_never_overwrites_source(case):
    req=put(case['tmp']/'request.json',case['request']);scope=put(case['tmp']/'scope.json',case['scope'])
    before=sha(case['path'])
    p=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',case['manifest_path'],'--request',req,'--scope',scope,'--output',case['path'])
    assert p.returncode!=0 and sha(case['path'])==before
