"""Native OLE placement is only an exclusion envelope, never readable text."""
import json
import os
from pathlib import Path

import ezdxf
import pytest
from ezdxf.lldxf.tags import Tags

from test_geometric_units_t27_20261019 import case, save_source, put, command, SHARED, units
from test_component_geometric_units_t27_20261020 import component_case


def ole(layout, a=(100000., 2000., 0.), b=(101000., 1000., 0.), raw=None):
    entity = layout.new_entity('OLE2FRAME', {})
    if raw is None:
        raw = '100\nAcDbOle2Frame\n70\n2\n' + ''.join(
            f'{code}\n{value}\n' for code, value in zip((10,20,30,11,21,31), (*a,*b)))
        raw += '71\n2\n72\n0\n90\n0\n1\nOLE\n'
    entity.acdb_ole2frame = Tags.from_text(raw)
    return entity


def actual_cli(c, label, success):
    save_source(c)
    reopened = ezdxf.readfile(c['path'])
    search = units._search_unit_texts(reopened, (0.,0.,84100.,59400.))
    request=put(c['tmp']/'request.json', c['request'])
    scope=put(c['tmp']/'scope.json', c['scope'])
    output=c['tmp']/'credential.json'
    args=(SHARED/'qto_geometric_unit_evidence.py', '--cad-manifest', c['manifest_path'],
          '--request', request, '--scope', scope, '--output', output)
    p=command(*args)
    record=dict(label=label,args=list(map(str,args)),returncode=p.returncode,stdout=p.stdout,stderr=p.stderr,
                search=search,source=str(c['path']),output=str(output))
    put(c['tmp']/'actual-cli.json',record)
    archive=os.environ.get('QTO_OLE_EVIDENCE_DIR')
    if archive:
        target=Path(archive);target.mkdir(parents=True,exist_ok=True)
        with (target/'actual-cli.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
    assert (p.returncode==0) is success,(p.stdout,p.stderr)
    if success:
        result=json.loads(output.read_text(encoding='utf-8'))
        assert result['quantity_level']=='规则推算量'
        assert result['physical_unit_basis'] is None and not result['allows_length_measurement']
        assert result['unit_text_search']['status']=='COMPLETE'
    else:
        assert search['status']=='UNRESOLVED'
    return search


@pytest.mark.parametrize('mode', ['outside','inside','crossing','touch','missing','missing_y','duplicate',
    'orphan','nan','infinity','nonzero_z','different_z','degenerate','extra_coordinate','own_context'])
def test_native_ole_actual_public_cli(case,mode):
    c=case; raw=None;a=(100000.,2000.,0.);b=(101000.,1000.,0.)
    if mode=='inside': a=(1000.,2000.,0.);b=(2000.,1000.,0.)
    elif mode=='crossing': a=(84000.,2000.,0.);b=(85000.,1000.,0.)
    elif mode=='touch': a=(84100.,2000.,0.);b=(85100.,1000.,0.)
    elif mode=='nonzero_z': a=(100000.,2000.,1.);b=(101000.,1000.,1.)
    elif mode=='different_z': b=(101000.,1000.,1.)
    elif mode=='degenerate': b=(100000.,1000.,0.)
    e=ole(c['doc'].modelspace(),a,b)
    if mode=='missing': e.acdb_ole2frame=Tags.from_text('100\nAcDbOle2Frame\n')
    elif mode=='missing_y':
        # Tags.from_text can consume the following x as y. The remaining
        # orphan component and missing 11 still cannot prove an envelope.
        e.acdb_ole2frame=Tags.from_text('100\nAcDbOle2Frame\n10\n100000\n11\n101000\n21\n1000\n')
    elif mode=='duplicate': e.acdb_ole2frame.extend(Tags.from_text('10\n1\n20\n2\n30\n0\n'))
    elif mode=='orphan': e.acdb_ole2frame.extend(Tags.from_text('20\n123\n'))
    elif mode in ('nan','infinity'):
        e.acdb_ole2frame=Tags.from_text(f'100\nAcDbOle2Frame\n10\n{ "nan" if mode=="nan" else "inf" }\n20\n2000\n30\n0\n11\n101000\n21\n1000\n31\n0\n')
    elif mode=='extra_coordinate': e.acdb_ole2frame.extend(Tags.from_text('12\n1\n22\n2\n32\n0\n'))
    elif mode=='own_context':
        c['doc'].appids.new('AcadAnnotative');e.set_xdata('AcadAnnotative',[(1000,'unknown')])
    search=actual_cli(c,mode,mode=='outside')
    rows=search['excluded_outside_frame'] if mode=='outside' else search['uninspected_active_text_carriers']
    row=next(r for r in rows if r['handle']==e.dxf.handle)
    assert row['entity_type']=='OLE2FRAME' and '尚未读清' in row['reason']
    if mode not in ('outside','inside','crossing','touch'): assert row['bbox'] is None


@pytest.mark.parametrize('mode',['array','rotated_crossing','dynamic','xclip','anonymous_dynamic','non_xy','bad_transform'])
def test_full_native_parent_chain_does_not_misexclude(case,mode):
    c=case;doc=c['doc'];leaf=doc.blocks.new('OLE_LEAF');ole(leaf,(0.,1000.,0.),(1000.,0.,0.))
    parent=doc.blocks.new('OLE_PARENT')
    inner=parent.add_blockref('OLE_LEAF',(10000.,10000.),dxfattribs={'rotation':90.})
    top=doc.modelspace().add_blockref('OLE_PARENT',(20000.,20000.),dxfattribs={'rotation':90.})
    if mode=='array': inner.dxf.column_count=2;inner.dxf.column_spacing=100000.
    elif mode=='rotated_crossing':
        inner.dxf.insert=(0.,0.,0.);inner.dxf.rotation=0.
        top.dxf.insert=(-600.,1000.,0.);top.dxf.rotation=45.
    elif mode=='dynamic':
        top.dxf.insert=(200000.,200000.,0.)
        doc.appids.new('AcadAnnotative');top.set_xdata('AcadAnnotative',[(1000,'unknown')])
    elif mode=='xclip':
        top.dxf.insert=(200000.,200000.,0.)
        top.new_extension_dict().add_xrecord('ACAD_FILTER')
    elif mode=='anonymous_dynamic':
        unknown=doc.blocks.new('*U100')
        unknown.add_blockref('OLE_LEAF',(0.,0.))
        top.dxf.name='*U100';top.dxf.insert=(200000.,200000.,0.)
    elif mode=='non_xy': top.dxf.extrusion=(0.,1.,0.)
    elif mode=='bad_transform': top.dxf.zscale=float('nan')
    search=actual_cli(c,mode,False)
    retained=[r for r in search['uninspected_active_text_carriers'] if r['entity_type']=='OLE2FRAME']
    assert retained
    if mode=='array':
        assert len(retained)==1 and retained[0]['bbox']==pytest.approx([9000.,29000.,10000.,30000.])
        excluded=[r for r in search['excluded_outside_frame'] if r['entity_type']=='OLE2FRAME']
        assert len(excluded)==1 and '[0,1]' in excluded[0]['instance_path']
    elif mode=='rotated_crossing': assert retained[0]['bbox'][2]>0.
    else: assert all(r['bbox'] is None for r in retained)


def test_component_second_evidence_with_outside_native_ole(component_case):
    c=component_case;ole(c['doc'].modelspace())
    search=actual_cli(c,'component-outside',True)
    assert any(r['entity_type']=='OLE2FRAME' for r in search['excluded_outside_frame'])
