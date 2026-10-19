"""Source DIM picture translation and independent visibility/text-placement counterexamples."""
import math

import pytest
from ezdxf.math import Matrix44

from test_geometric_units_t27_20261019 import case, check, credential, save_source, sha, units
from test_geometric_units_block_t27_batch11 import wrap


def clone_picture(doc, dim, offset):
    # Construct an anonymous source equivalent to a cloned DIM: only its
    # picture is stored at -offset, group12 restores the rendered location.
    # This setup is never called by the unit verifier or on project sources.
    inverse=Matrix44.translate(*[-v for v in offset])
    for child in doc.blocks[dim.dxf.geometry]:child.transform(inverse)
    dim.dxf.insert=offset


@pytest.mark.parametrize('rotation,sx,sy',[(0,1,1),(31,-1,1),(-19,1,-1)])
def test_clone_xy_picture_translation_matches_original_virtual_entities_once(case,rotation,sx,sy):
    doc=case['doc'];dim=doc.entitydb[case['handles']['dim']]
    original={k:tuple(dim.dxf.get(k)) for k in ('defpoint2','defpoint3')}
    offset=(650000.,155000.,0.);clone_picture(doc,dim,offset)
    parent,_=wrap(case,['dim'],base=(13000,10000,0),insert=(13000,10000,0),rotation=rotation,sx=sx,sy=sy)
    save_source(case);before=sha(case['path']);result=credential(case);d=result['dimensions'][0]
    source_virtual=next(e for e in dim.virtual_entities() if e.dxftype()=='MTEXT')
    expected=tuple(parent.matrix44().transform(source_virtual.dxf.insert))
    assert d['rendered_text_point_wcs']==pytest.approx(expected)
    assert d['dimension_render_offset_ocs']==offset
    assert d['source_definition_points']=={k:list(v) for k,v in original.items()}
    assert d['source_display_number']==6000. and d['source_measurement']==pytest.approx(6000.)
    assert d['measurement']==pytest.approx(6000.) and d['insert_world_scale']==pytest.approx(1.)
    assert sha(case['path'])==before


@pytest.mark.parametrize('mode',['unrestored_xy','z','nonfinite'])
def test_clone_translation_is_not_ignored_or_applied_to_definition_points(case,mode):
    dim=case['doc'].entitydb[case['handles']['dim']]
    dim.dxf.insert=(1000000,1000000,0) if mode=='unrestored_xy' else (0,0,5) if mode=='z' else (float('inf'),0,0)
    save_source(case)
    with pytest.raises(ValueError):check(case)


@pytest.mark.parametrize('owner_kind,marker',[('record','annotative'),('block','annotative'),('record','clip'),('record','dynamic')])
def test_render_block_own_context_cannot_be_hidden_by_plain_dim_and_mtext(case,owner_kind,marker):
    doc=case['doc'];dim=doc.entitydb[case['handles']['dim']];block=doc.blocks[dim.dxf.geometry]
    owner=block.block_record if owner_kind=='record' else block.block
    if marker=='annotative':
        doc.appids.new('AcadAnnotative');owner.set_xdata('AcadAnnotative',[(1000,'AnnotativeData'),(1070,1)])
    else:owner.new_extension_dict().add_dictionary('ACAD_FILTER' if marker=='clip' else 'ACAD_ENHANCEDBLOCK')
    assert not dim.has_xdata('AcadAnnotative')
    assert not block.query('MTEXT')[0].has_xdata('AcadAnnotative')
    save_source(case)
    with pytest.raises(ValueError,match='上下文'):check(case)


@pytest.mark.parametrize('target',['scale','title'])
@pytest.mark.parametrize('halign,valign',[(1,0),(2,0),(0,2),(3,0),(5,0)])
def test_text_real_placement_outside_frame_is_rejected_even_when_group10_inside(case,target,halign,valign):
    e=case['doc'].entitydb[case['handles'][target]]
    e.dxf.halign=halign;e.dxf.valign=valign;e.dxf.align_point=(-10000,2000,0)
    save_source(case)
    with pytest.raises(ValueError,match='框|位置'):check(case)


@pytest.mark.parametrize('target',['scale','title'])
def test_center_text_real_placement_inside_frame_with_group10_outside_is_legal(case,target):
    e=case['doc'].entitydb[case['handles'][target]]
    e.dxf.insert=(-20000,2000,0);e.dxf.halign=1;e.dxf.align_point=(40000,3000,0)
    save_source(case)
    assert check(case)['status']=='INFERRED'


@pytest.mark.parametrize('inside',[False,True])
def test_rendered_text_alignment_uses_real_source_anchor(case,inside):
    doc=case['doc'];dim=doc.entitydb[case['handles']['dim']];block=doc.blocks[dim.dxf.geometry]
    block.delete_entity(block.query('MTEXT')[0])
    text=block.add_text('6000',dxfattribs={'insert':(13000,11000),'halign':1,
        'align_point':(13000,11000) if inside else (-10000,11000)})
    save_source(case)
    if inside:assert check(case)['dimensions'][0]['rendered_source']['entity_handle']==text.dxf.handle
    else:
        with pytest.raises(ValueError,match='渲染文字.*图框'):check(case)


@pytest.mark.parametrize('actual_inside',[True,False])
def test_unit_text_search_uses_same_clone_picture_translation(case,actual_inside):
    doc=case['doc'];m=doc.modelspace();x=30000 if actual_inside else 230000
    extra=m.add_aligned_dim((x,30000),(x+6000,30000),distance=1000);extra.render()
    render=doc.blocks[extra.dimension.dxf.geometry].query('MTEXT')[0];render.text='坐标单位米'
    clone_picture(doc,extra.dimension,(1000000,155000,0) if actual_inside else (220000,0,0))
    save_source(case)
    if actual_inside:
        with pytest.raises(units.GeometricUnitUnresolved) as caught:check(case)
        partial=caught.value.partial_evidence
        assert partial['raw_unit'] is None and partial['length'] is None
        assert any(r['handle']==render.dxf.handle for r in partial['unit_text_search']['unit_text_matches'])
    else:
        result=check(case)
        assert any(r['handle']==render.dxf.handle for r in result['unit_text_search']['excluded_outside_frame'])


def test_unselected_render_block_annotation_context_leaves_unit_text_search_unknown(case):
    doc=case['doc'];extra=doc.modelspace().add_aligned_dim((30000,30000),(36000,30000),distance=1000);extra.render()
    doc.appids.new('AcadAnnotative')
    doc.blocks[extra.dimension.dxf.geometry].block_record.set_xdata('AcadAnnotative',[(1000,'AnnotativeData')])
    save_source(case)
    with pytest.raises(units.GeometricUnitUnresolved) as caught:check(case)
    assert any(r['handle']==extra.dimension.dxf.handle for r in caught.value.partial_evidence['unit_text_search']['uninspected_active_text_carriers'])


def test_clone_unit_actual_cli_and_real_pipe_source_binding(case):
    from test_geometric_units_block_t27_batch11 import test_actual_infer_consumes_block_dimension_and_refuses_different_exchange
    clone_picture(case['doc'],case['doc'].entitydb[case['handles']['dim']],(650000,155000,0))
    test_actual_infer_consumes_block_dimension_and_refuses_different_exchange(case)
