"""Original-source block dimensions and oriented A-sheet boundaries, no SI adoption."""
import copy
import json
import math

import pytest

from test_geometric_units_t27_20261019 import case, check, credential, save_source, units, command, put, SHARED


def wrap(case, selected, *, name='NEST', base=(0, 0, 0), insert=(0, 0, 0), rotation=0., sx=1., sy=1.):
    doc = case['doc']; model = doc.modelspace()
    block = doc.blocks.new(name, base_point=base)
    for key in selected:
        entity = doc.entitydb[case['handles'][key]]
        model.unlink_entity(entity); block.add_entity(entity)
    ref = model.add_blockref(name, insert, dxfattribs={'rotation': rotation, 'xscale': sx, 'yscale': sy})
    for key in selected:
        target = case['request']['frame']['entity_refs'][0] if key == 'frame' else (
            case['request']['scale_text'] if key == 'scale' else case['request']['dimension_refs'][0] if key == 'dim' else
            case['request']['view_text_refs'][0] if key == 'title' else None)
        if target is not None:
            target['insert_handles'] = [ref.dxf.handle]
    return ref, block


@pytest.mark.parametrize('rotation,sx,sy', [(0, 1, 1), (33, 1, 1), (0, -1, 1), (27, 1, -1), (-21, -1, -1)])
def test_source_block_dim_rotation_and_mirror_preserve_display_and_world_units(case, rotation, sx, sy):
    wrap(case, ['dim'], base=(13000, 10000, 0), insert=(13000, 10000, 0), rotation=rotation, sx=sx, sy=sy)
    save_source(case)
    d = credential(case)['dimensions'][0]
    assert d['source_display_number'] == 6000.
    assert d['source_measurement'] == pytest.approx(6000.)
    assert d['insert_world_scale'] == pytest.approx(1.)
    assert d['measurement'] == pytest.approx(6000.)
    assert math.dist(*d['definition_points_wcs'].values()) == pytest.approx(6000.)
    assert d['dimscale'] == 5. and d['dimscale_role'].startswith('annotation_display_only')


def test_nested_nonunit_scales_cancel_in_actual_matrix_once(case):
    inner, block = wrap(case, ['dim'], name='INNER', sx=2., sy=2.)
    outer = case['doc'].blocks.new('OUTER')
    case['doc'].modelspace().unlink_entity(inner); outer.add_entity(inner)
    root = case['doc'].modelspace().add_blockref('OUTER', (0, 0), dxfattribs={'xscale': .5, 'yscale': .5})
    case['request']['dimension_refs'][0]['insert_handles'] = [root.dxf.handle, inner.dxf.handle]
    save_source(case)
    d = credential(case)['dimensions'][0]
    assert d['insert_world_scale'] == pytest.approx(1.)
    assert d['measurement'] == pytest.approx(6000.)
    assert d['source_display_number'] == 6000.


@pytest.mark.parametrize('scale', [2., .5, 100.])
def test_insert_must_not_scale_the_represented_dimension_number(case, scale):
    wrap(case, ['dim'], base=(13000,10000,0), insert=(13000,10000,0), sx=scale, sy=scale)
    save_source(case)
    with pytest.raises(ValueError, match='世界几何|内部'):
        check(case)


@pytest.mark.parametrize('rotation,sx,sy', [(30,1,1), (0,-1,1), (47,-1,1), (-17,1,-1)])
def test_oriented_frame_uses_real_edge_lengths_and_checked_scope(case, rotation, sx, sy):
    ref, _ = wrap(case, ['frame','scale','title','dim'], name='SHEET', base=(2000,3000,0),
                  insert=(150000,200000,0), rotation=rotation, sx=sx, sy=sy)
    save_source(case)
    matrix = ref.matrix44(); center = matrix.transform((42050,29700,0))
    case['scope']['bounds'] = dict(xmin=center.x-1000,ymin=center.y-1000,xmax_exclusive=center.x+1000,ymax_exclusive=center.y+1000)
    result = credential(case)
    assert result['frame']['paper'] == 'A1'
    assert sorted(result['frame']['actual_axis_spans']) == pytest.approx([59400,84100])
    assert len(result['frame']['polygon_wcs']) == 4
    assert result['dimensions'][0]['measurement'] == pytest.approx(6000.)
    assert units.verify_geometric_unit_credential(result,case['scope'])['status'] == 'INFERRED'


def test_rotated_scope_diagonal_inside_but_two_other_corners_outside_is_rejected(case):
    ref, _ = wrap(case,['frame','scale','title','dim'],rotation=30.)
    save_source(case)
    # A rectangle whose SW and NE corners lie on the long local x axis, while
    # the other two corners fall outside the actual short frame edges.
    a=ref.matrix44().transform((1000,29700,0));b=ref.matrix44().transform((83100,29700,0))
    case['scope']['bounds']=dict(xmin=a.x,ymin=a.y,xmax_exclusive=b.x,ymax_exclusive=b.y)
    box,_,_=units._frame(case['doc'],case['request']['frame'])
    assert units._inside(tuple(a),box) and units._inside(tuple(b),box)
    assert not units._inside((a.x,b.y),box) or not units._inside((b.x,a.y),box)
    with pytest.raises(ValueError,match='真实内部'):check(case)


@pytest.mark.parametrize('mutation', ['missing_parent','wrong_parent','reversed_chain','hidden_parent','off_parent','frozen_parent','nonuniform','array','nonxy','special','override','render_hidden','render_off','source_display'])
def test_block_dimension_bad_source_transform_visibility_or_value_stays_unknown(case, mutation):
    inner, _ = wrap(case,['dim'],name='DIMBLOCK')
    req=case['request']; dim=case['doc'].entitydb[case['handles']['dim']]
    if mutation=='missing_parent':req['dimension_refs'][0].pop('insert_handles')
    elif mutation=='wrong_parent':
        other=case['doc'].blocks.new('OTHER');root=case['doc'].modelspace().add_blockref('OTHER',(0,0))
        req['dimension_refs'][0]['insert_handles']=[root.dxf.handle]
    elif mutation=='reversed_chain':req['dimension_refs'][0]['insert_handles']=[inner.dxf.handle,inner.dxf.handle]
    elif mutation=='hidden_parent':inner.dxf.invisible=1
    elif mutation in ('off_parent','frozen_parent'):
        layer=case['doc'].layers.new('NO_DIM');inner.dxf.layer='NO_DIM'
        layer.off() if mutation=='off_parent' else layer.freeze()
    elif mutation=='nonuniform':inner.dxf.yscale=2
    elif mutation=='array':inner.dxf.row_count=2
    elif mutation=='nonxy':inner.dxf.extrusion=(0,1,0)
    elif mutation=='special':dim.dxf.dimtype=3
    elif mutation=='override':dim.dxf.text='6000'
    elif mutation=='render_hidden':case['doc'].blocks[dim.dxf.geometry].query('MTEXT')[0].dxf.invisible=1
    elif mutation=='render_off':
        layer=case['doc'].layers.new('NO_RENDER');layer.off()
        case['doc'].blocks[dim.dxf.geometry].query('MTEXT')[0].dxf.layer='NO_RENDER'
    elif mutation=='source_display':case['doc'].blocks[dim.dxf.geometry].query('MTEXT')[0].text='12000'
    save_source(case)
    with pytest.raises(ValueError):check(case)


def test_rotated_or_mirrored_frame_cannot_hide_unit_conflict_or_opaque_carrier(case):
    ref, block=wrap(case,['frame','scale','title','dim'],rotation=31.,sx=-1.,sy=1.)
    center=ref.matrix44().transform((42050,29700,0))
    case['scope']['bounds']=dict(xmin=center.x-1000,ymin=center.y-1000,xmax_exclusive=center.x+1000,ymax_exclusive=center.y+1000)
    text=block.add_text('坐标单位米',dxfattribs={'insert':(10000,10000)})
    save_source(case)
    with pytest.raises(units.GeometricUnitUnresolved) as e:check(case)
    assert e.value.partial_evidence['raw_unit'] is None
    block.delete_entity(text)
    image=case['doc'].add_image_def('unread.png',(100,100))
    block.add_image(image,insert=(10000,10000),size_in_units=(1000,1000))
    save_source(case)
    with pytest.raises(units.GeometricUnitUnresolved) as e:check(case)
    assert e.value.partial_evidence['length'] is None
    assert e.value.partial_evidence['allows_length_measurement'] is False


def test_actual_cli_source_block_dimension_roundtrip(case):
    wrap(case,['dim'],base=(13000,10000,0),insert=(13000,10000,0),rotation=37,sx=-1)
    save_source(case)
    request=put(case['tmp']/'block-request.json',case['request']);scope=put(case['tmp']/'block-scope.json',case['scope'])
    output=case['tmp']/'actual-block-credential.json'
    result=command(SHARED/'qto_geometric_unit_evidence.py','--cad-manifest',case['manifest_path'],'--request',request,'--scope',scope,'--output',output)
    assert result.returncode==0,(result.stdout,result.stderr)
    actual=json.loads(output.read_text(encoding='utf-8'))
    assert actual['dimensions'][0]['source_display_number']==6000.
    assert actual['evidence_level']=='inferred' and actual['formal_adoption'] is False
    assert units.verify_geometric_unit_credential(actual,case['scope'])['status']=='INFERRED'


@pytest.mark.parametrize('context', ['xclip','disabled_xclip','dynamic_insert','dynamic_record','anonymous_dynamic','annotation','insert_annotative','record_annotative'])
def test_unrestored_clipping_dynamic_or_annotation_context_is_not_visible_evidence(case,context):
    from ezdxf.xclip import XClip
    ref,block=wrap(case,['dim'],name='*U100' if context=='anonymous_dynamic' else 'DIM_CONTEXT')
    if context in ('xclip','disabled_xclip'):
        clipping=XClip(ref);clipping.set_block_clipping_path([(0,0),(100,100)])
        if context=='disabled_xclip':clipping.disable_clipping()
    elif context in ('dynamic_insert','dynamic_record'):
        owner=ref if context=='dynamic_insert' else block.block_record
        owner.new_extension_dict().add_dictionary('ACAD_ENHANCEDBLOCK')
    elif context=='annotation':
        case['doc'].entitydb[case['handles']['dim']].new_extension_dict().add_dictionary('ACAD_CONTEXT_DATA_MANAGER')
    elif context in ('insert_annotative','record_annotative'):
        case['doc'].appids.new('AcadAnnotative')
        owner=ref if context=='insert_annotative' else block.block_record
        owner.set_xdata('AcadAnnotative',[(1000,'AnnotativeData')])
    save_source(case)
    with pytest.raises(ValueError,match='裁剪|动态|上下文'):check(case)


def test_unselected_dynamic_carrier_inside_frame_also_prevents_false_absence(case):
    doc=case['doc'];block=doc.blocks.new('*U101');block.add_line((10000,10000),(20000,10000))
    doc.modelspace().add_blockref(block.name,(0,0));save_source(case)
    with pytest.raises(units.GeometricUnitUnresolved) as caught:check(case)
    assert caught.value.partial_evidence['raw_unit'] is None
    assert any('动态' in r['reason'] for r in caught.value.partial_evidence['unit_text_search']['uninspected_active_text_carriers'])


def test_actual_infer_consumes_block_dimension_and_refuses_different_exchange(case):
    from test_geometric_unit_dependency_t27_20261019 import input_for,other_source
    from test_geometric_units_t27_20261019 import PIPE
    from pathlib import Path
    wrap(case,['dim'],base=(13000,10000,0),insert=(13000,10000,0),rotation=37,sx=-1)
    save_source(case)
    neutral=case['tmp']/'block-source-neutral'
    p=command(SHARED/'extract_cad_neutral.py','--source-dxf',case['path'],'--all-blocks','--output-dir',neutral)
    assert p.returncode in (0,3),(p.stdout,p.stderr)
    case['manifest_path']=neutral/'CAD中性取证清单.json'
    case['manifest']=json.loads(case['manifest_path'].read_text(encoding='utf-8'))
    case['stream']=Path(case['manifest']['artifacts']['layout_entities']['path'])
    data=input_for(case);valid=put(case['tmp']/'block-infer-input.json',data)
    output=case['tmp']/'block-infer-result.json'
    p=command(PIPE/'infer_pipe_network.py','--input',valid,'--output',output)
    assert p.returncode==0,(p.stdout,p.stderr)
    actual=json.loads(output.read_text(encoding='utf-8'))
    assert actual['geometric_unit_source_binding']['cad_manifest']['sha256']==data['geometric_unit_evidence']['cad_manifest']['sha256']
    assert actual['geometric_unit_evidence']['dimensions'][0]['source_display_number']==6000.
    data['geometric_unit_evidence']=other_source(case,same_authority=True)
    wrong=put(case['tmp']/'block-wrong-exchange.json',data);bad_output=case['tmp']/'block-wrong-result.json'
    p=command(PIPE/'infer_pipe_network.py','--input',wrong,'--output',bad_output)
    assert p.returncode==2 and '交换DXF版本' in p.stderr and not bad_output.exists(),(p.stdout,p.stderr)


def test_shuffled_four_lines_form_real_rotated_rectangle_but_skew_is_rejected(case):
    from ezdxf.math import Matrix44
    model=case['doc'].modelspace();model.delete_entity(case['doc'].entitydb[case['handles']['frame']])
    # Other evidence stays in the frame; rotate only a larger source rectangle
    # about its center, retaining true ISO edge lengths and a small inner scope.
    matrix=Matrix44.chain(Matrix44.translate(-42050,-29700,0),Matrix44.z_rotate(math.radians(5)),Matrix44.translate(42050,29700,0))
    vertices=[tuple(matrix.transform(p)) for p in [(0,0,0),(84100,0,0),(84100,59400,0),(0,59400,0)]]
    refs=[]
    for i in (2,0,3,1):
        e=model.add_line(vertices[(i+1)%4],vertices[i]);refs.append(dict(handle=e.dxf.handle))
    case['request']['frame']['entity_refs']=refs
    # This source's original scale/title near its lower edge would be outside
    # after rotation, so move their actual source insert positions into it.
    case['doc'].entitydb[case['handles']['scale']].dxf.insert=(70000,10000,0)
    case['doc'].entitydb[case['handles']['title']].dxf.insert=(10000,10000,0)
    case['scope']['bounds']=dict(xmin=20000,ymin=20000,xmax_exclusive=60000,ymax_exclusive=40000)
    save_source(case)
    assert check(case)['frame']['paper']=='A1'
    edge=case['doc'].entitydb[refs[0]['handle']];edge.dxf.end=(edge.dxf.end.x+1000,edge.dxf.end.y,0)
    save_source(case)
    with pytest.raises(ValueError,match='角点|矩形'):check(case)
