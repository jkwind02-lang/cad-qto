"""ID23/T27: source-bound component dimensions supplement, never declare SI."""
import copy
import json
from pathlib import Path

import pytest

from test_geometric_units_t27_20261019 import (
    case, sha, put, command, save_source, check, credential,
    batch_input, SHARED, PIPE, units, extract,
)


@pytest.fixture
def component_case(case):
    doc = case['doc']; model = doc.modelspace()
    block = doc.blocks.new('COMPONENT')
    footprint = block.add_lwpolyline([(0,0),(6000,0),(6000,2000),(0,2000)], close=True)
    insert = model.add_blockref('COMPONENT', (30000,10000))
    identity = insert.add_attrib('COMPONENT_ID', 'P-01', (30000,10000))
    drawing = model.add_text('图号：A-01', dxfattribs={'insert':(1000,4000)})
    dn_label = model.add_text('DN25', dxfattribs={'insert':(15000,30000)})
    source = case['tmp']/'manufacturer.txt'
    source.write_text('文件编号：DIM-SCHEDULE；版本：R1；发布方：匿名制造商；资料角色：manufacturer_dimension_schedule\n'
                      '图号：A-01；图名：首层给水平面图；构件编号：P-01；外形X尺寸：6000 mm。\n', encoding='utf-8')
    case['manifest']['component_dimension_sources'] = [dict(source_id='DIM-SCHEDULE', path=str(source), sha256=sha(source),
        document='DIM-SCHEDULE', revision='R1', issuer='匿名制造商', role='manufacturer_dimension_schedule',
        adoption_basis='测试输入选择制造商原外形尺寸表R1作本图构件外形参考；非人工审批凭据',
        applies_to=dict(drawing_number='A-01',view_title='首层给水平面图'))]
    case['request']['dimension_refs'] = []
    case['request']['component_dimension_refs'] = [dict(authority_source_id='DIM-SCHEDULE',source_line=2,
        drawing_number_ref=dict(handle=drawing.dxf.handle),view_text_ref=dict(handle=case['handles']['title']),
        component_id_ref=dict(handle=identity.dxf.handle,insert_handles=[insert.dxf.handle]),
        footprint_ref=dict(handle=footprint.dxf.handle,insert_handles=[insert.dxf.handle]),measurement_axis='X')]
    case.update(authority=source, insert=insert, identity=identity, footprint=footprint, drawing=drawing, dn_label=dn_label)
    save_source(case)
    # Exchange rows must belong to the same modified DXF, as a real extraction
    # would. A stale fixture stream correctly fails existing source-binding.
    h=sha(case['path'])
    streams=dict(layout_entities=[extract.convert(e,layout=l.name,parent_block=None,source_hash=h,raw_unit='Unitless',sequence=i)
        for l in doc.layouts for i,e in enumerate(l,1)],
        block_definitions=[extract.convert(e,layout='Model',parent_block=b.name,source_hash=h,raw_unit='Unitless',sequence=i)
        for b in doc.blocks if not b.is_any_layout for i,e in enumerate(b,1)])
    for key,rows in streams.items():
        artifact=case['manifest']['artifacts'][key]
        target=Path(artifact['path'])
        target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
        artifact['sha256']=sha(target)
    put(case['manifest_path'],case['manifest'])
    return case


def change_authority(case, text):
    case['authority'].write_text(text,encoding='utf-8')
    case['manifest']['component_dimension_sources'][0]['sha256']=sha(case['authority'])
    put(case['manifest_path'],case['manifest'])


def test_component_independent_physical_dimension_is_not_coordinate_si(component_case):
    c=component_case; result=credential(c); row=result['component_dimensions'][0]
    assert result['dimensions']==[] and result['quantity_level']=='规则推算量'
    assert row['physical_dimension_metres']==6. and row['measurement']==6000.
    assert row['measurement_axis']=='X' and row['source_line']==2
    assert row['footprint_source_context']['instance_path'].endswith('/'+c['footprint'].dxf.handle)
    assert row['physical_dimension_proven_independently'] is True
    assert row['physical_unit_proven_independently'] is False
    assert result['physical_unit_basis'] is None and result['allows_length_measurement'] is False
    assert units.verify_geometric_unit_credential(result,c['scope'])['status']=='INFERRED'
    with pytest.raises(units.GeometricUnitUnresolved):
        units.verify_geometric_unit_consumption(result,c['manifest'],c['scope'],.001)


@pytest.mark.parametrize('mode', ['single','duplicate','axis','wrong_instance','duplicate_id','other_drawing','other_view',
    'scope','frame_self','numeric_only','unit_conflict','metadata','unregistered','opaque','unreadable','complex_block'])
def test_component_refusals(component_case, mode):
    c=component_case; ref=c['request']['component_dimension_refs'][0]
    if mode=='single': c['request']['component_dimension_refs']=[]
    elif mode=='duplicate': c['request']['component_dimension_refs']*=2
    elif mode=='axis': ref['measurement_axis']='Y'
    elif mode=='wrong_instance':
        other=c['doc'].modelspace().add_blockref('COMPONENT',(50000,10000))
        ref['footprint_ref']['insert_handles']=[other.dxf.handle]
    elif mode=='duplicate_id':
        other=c['doc'].modelspace().add_blockref('COMPONENT',(50000,10000))
        other.add_attrib('COMPONENT_ID','P-01',(50000,10000))
    elif mode=='other_drawing': c['drawing'].dxf.text='图号：A-02'
    elif mode=='other_view':
        title=c['doc'].modelspace().add_text('首层给水平面图',dxfattribs={'insert':(2000,6000)})
        ref['view_text_ref']=dict(handle=title.dxf.handle)
    elif mode=='scope': c['scope']['bounds']['xmax_exclusive']=30001.
    elif mode=='frame_self': ref['footprint_ref']=dict(handle=c['handles']['frame'])
    elif mode=='numeric_only': change_authority(c,c['authority'].read_text(encoding='utf-8').replace('外形X尺寸：6000 mm','外形X尺寸：500×150'))
    elif mode=='unit_conflict': change_authority(c,c['authority'].read_text(encoding='utf-8').replace('6000 mm','6000 m'))
    elif mode=='metadata': c['manifest']['component_dimension_sources'][0]['revision']='R2'
    elif mode=='unregistered': c['manifest'].pop('component_dimension_sources')
    elif mode=='opaque': c['doc'].modelspace().new_entity('OLE2FRAME',{})
    elif mode=='unreadable':
        c['authority'].write_bytes(b'\xff\xfe\x00')
        c['manifest']['component_dimension_sources'][0]['sha256']=sha(c['authority'])
    elif mode=='complex_block': c['doc'].blocks.get('COMPONENT').add_line((0,0),(10000,0))
    save_source(c)
    with pytest.raises(ValueError): check(c)


def test_authority_duplicate_original_rows_are_not_independent(component_case):
    c=component_case; text=c['authority'].read_text(encoding='utf-8')
    change_authority(c,text+text.splitlines()[1]+'\n')
    with pytest.raises(ValueError,match='唯一'): check(c)


@pytest.mark.parametrize('mode', ['y_axis_metres','rotated_mirror','explicit_si'])
def test_component_legal_controls(component_case,mode):
    c=component_case
    if mode=='y_axis_metres':
        c['request']['component_dimension_refs'][0]['measurement_axis']='Y'
        change_authority(c,c['authority'].read_text(encoding='utf-8').replace('外形X尺寸：6000 mm','外形Y尺寸：2 m'))
    elif mode=='rotated_mirror':
        c['insert'].dxf.rotation=30.
        c['insert'].dxf.xscale=-1.
    else:
        text=c['doc'].modelspace().add_text('本图坐标单位：mm',dxfattribs={'insert':(1000,6000)})
        c['request']['coordinate_unit_refs']=[dict(handle=text.dxf.handle)]
    save_source(c);result=credential(c)
    assert result['status']=='INFERRED' and result['quantity_level']=='规则推算量'
    if mode=='explicit_si':
        consumed=units.verify_geometric_unit_consumption(result,c['manifest'],c['scope'],.001)
        assert str(c['authority']) in consumed['dependency_fingerprints']


def test_duplicate_component_in_nested_instance_is_rejected(component_case):
    c=component_case; block=c['doc'].blocks.new('NESTED')
    insert=block.add_blockref('COMPONENT',(0,0));insert.add_attrib('EQUIPMENT_ID','P-01',(0,0))
    c['doc'].modelspace().add_blockref('NESTED',(50000,20000));save_source(c)
    with pytest.raises(ValueError,match='唯一物理实例'):check(c)


def test_unparsed_competing_authority_clause_is_not_ignored(component_case):
    c=component_case
    change_authority(c,c['authority'].read_text(encoding='utf-8')+'P-01的X尺寸可能为500×150mm，待确认\n')
    with pytest.raises(ValueError,match='未解析'):check(c)


@pytest.mark.parametrize('field', ['source','module','manifest','scope'])
def test_component_credential_drift(component_case,field):
    c=component_case; cred=copy.deepcopy(credential(c))
    if field=='source': c['authority'].write_text(c['authority'].read_text(encoding='utf-8')+'变更\n',encoding='utf-8')
    elif field=='module': cred['runtime_dependencies'][0]['sha256']='0'*64
    elif field=='manifest': c['manifest']['component_dimension_sources'][0]['adoption_basis']='different';put(c['manifest_path'],c['manifest'])
    elif field=='scope': c['scope']['bounds']['xmax_exclusive']=40000.
    with pytest.raises(ValueError): units.verify_geometric_unit_credential(cred,c['scope'])


def test_public_cli_then_existing_pipe_build_and_infer(component_case):
    c=component_case; request=put(c['tmp']/'request.json',c['request']);scope=put(c['tmp']/'scope.json',c['scope'])
    evidence=c['tmp']/'geometric.json'
    p=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',c['manifest_path'],'--request',request,'--scope',scope,'--output',evidence)
    assert p.returncode==0,(p.stdout,p.stderr)
    actual=json.loads(evidence.read_text(encoding='utf-8'));assert actual['component_dimensions']
    mapping,frames=batch_input(c);out=c['tmp']/'pipe-build'
    mapping_data=json.loads(mapping.read_text(encoding='utf-8'))
    mapping_data['families'].append(dict(family_id='DN',kind='text',layer_pattern='^0$',text_pattern=r'^DN(?P<dn>\d+)$',
        system='W',decision='include',disposition='seed',evidence_ids=['EV-source'],basis='匿名原源DN25与此管段明确绑定',
        max_distance=1.,min_second_candidate_margin=.1,
        segment_bindings={'CAD-'+c['dn_label'].dxf.handle:['SEG-CAD-'+c['handles']['pipe']]}))
    put(mapping,mapping_data)
    p=command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',c['manifest_path'],'--mapping',mapping,'--frames',frames,'--output-dir',out)
    assert p.returncode==0,(p.stdout,p.stderr)
    built=next(out.rglob('管网推算输入.json'));data=json.loads(built.read_text(encoding='utf-8'))
    assert data['unit_evidence_level']=='inferred' and data['segments'][0]['length']==10.
    assert data['geometric_unit_evidence']['component_dimensions']
    result_path=c['tmp']/'pipe-infer.json'
    p=command(PIPE/'infer_pipe_network.py','--input',built,'--output',result_path)
    assert p.returncode==0,(p.stdout,p.stderr)
    result=json.loads(result_path.read_text(encoding='utf-8'))
    assert result['unit_evidence_level']=='inferred'
    assert result['segments'][0]['quantity_level']=='规则推算量'


def test_output_cannot_overwrite_authority_source(component_case):
    c=component_case; original=c['authority'].read_bytes()
    p=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',c['manifest_path'],
        '--request',put(c['tmp']/'request.json',c['request']),'--scope',put(c['tmp']/'scope.json',c['scope']),'--output',c['authority'])
    assert p.returncode!=0 and c['authority'].read_bytes()==original
