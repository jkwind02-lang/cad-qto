import copy,hashlib,importlib.util,math
from pathlib import Path
import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
FILE=ROOT/'skills/qto-duct-air-systems/scripts/calculate_source_duct_bends_reference.py'
sp=importlib.util.spec_from_file_location('source_duct_bends',FILE);mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)

def fixture(tmp_path,*,scale=1,mirror=False,rotation=0):
    doc=ezdxf.new();doc.units=0;m=doc.modelspace();b=doc.blocks.new('actual-bend')
    arcs=[b.add_arc((0,0),r,0,90) for r in (80,160,240)]
    ins=m.add_blockref('actual-bend',(17000,-8200),dxfattribs={'xscale':-scale if mirror else scale,'yscale':scale,'rotation':rotation})
    unit=m.add_text('尺寸单位为mm');w=m.add_text(str(160*scale));h=m.add_text(str(200*scale))
    path=tmp_path/'source.dxf';doc.saveas(path)
    note=lambda e:dict(object_id='CADINST-Model/'+e.dxf.handle,text_contains=e.dxf.text,pattern=r'^(\d+(?:\.\d+)?)$',group=1)
    ids=['CADINST-Model/'+ins.dxf.handle+'/'+e.dxf.handle for e in arcs]
    row=dict(physical_id='bend-1',inner_arc_object_id=ids[0],centerline_object_id=ids[1],outer_arc_object_id=ids[2],radial_width_note=note(w),transverse_height_note=note(h))
    data=dict(schema='qto-source-duct-bends-reference/1.0',source=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()),unit_evidence=dict(unit='mm',object_id='CADINST-Model/'+unit.dxf.handle,text_contains='尺寸单位为mm'),elbows=[row])
    return data,doc,arcs,path

def rewrite(data,doc,path):
    doc.saveas(path);data['source']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()

def test_open_shell_matches_four_independent_faces(tmp_path):
    data,_,_,_=fixture(tmp_path);r=mod.calculate(data);x=r['items'][0]
    # Inner/outer curved walls plus two annular-sector cheeks, open end interfaces.
    assert x['total_centerline_length_m']==pytest.approx(.25132741228718345)
    assert x['open_shell_surface_m2']==pytest.approx(.18095573684677208)
    assert x['face_surface_m2']['side_1']==pytest.approx(.04021238596594936)
    assert x['end_caps_included'] is False and r['business_adoption_allowed'] is False

@pytest.mark.parametrize('scale,mirror,rotation',[(1,True,0),(1,False,37),(2,True,37)])
def test_transformed_actual_instance(scale,mirror,rotation,tmp_path):
    data,_,_,_=fixture(tmp_path,scale=scale,mirror=mirror,rotation=rotation);x=mod.calculate(data)['items'][0]
    assert x['total_centerline_length_m']==pytest.approx(.25132741228718345*scale)
    assert x['open_shell_surface_m2']==pytest.approx(.18095573684677208*scale*scale)

@pytest.mark.parametrize('kind',['physical','source','arc-role','override','dimension','group','unit'])
def test_invalid_binding_or_duplicate_fails(kind,tmp_path):
    data,_,_,_=fixture(tmp_path);r=data['elbows'][0]
    if kind=='physical':data['elbows'].append(copy.deepcopy(r))
    if kind=='source':q=copy.deepcopy(r);q['physical_id']='renamed';data['elbows'].append(q)
    if kind=='arc-role':r['outer_arc_object_id']=r['centerline_object_id']
    if kind=='override':r['count']=4
    if kind=='dimension':r['radial_width_note']=r['transverse_height_note']
    if kind=='group':r['radial_width_note']['group']=True
    if kind=='unit':data['unit_evidence']['unit']='m'
    with pytest.raises(ValueError):mod.calculate(data)

@pytest.mark.parametrize('kind',['centre','sector','sweep','full-circle','ellipse'])
def test_incompatible_arcs_fail(kind,tmp_path):
    data,doc,arcs,path=fixture(tmp_path)
    if kind=='centre':arcs[2].dxf.center=(1,0,0)
    if kind=='sector':arcs[2].dxf.start_angle=10;arcs[2].dxf.end_angle=100
    if kind=='sweep':arcs[2].dxf.end_angle=80
    if kind=='full-circle':arcs[1].dxf.end_angle=0
    if kind=='ellipse':doc.modelspace().query('INSERT')[0].dxf.xscale=2
    rewrite(data,doc,path)
    with pytest.raises(ValueError):mod.calculate(data)

def test_current_source_hash_is_required(tmp_path):
    data,doc,arcs,path=fixture(tmp_path);arcs[2].dxf.radius=260;doc.saveas(path)
    with pytest.raises(ValueError):mod.calculate(data)

def test_reverse_arc_traversal_preserves_same_sector(tmp_path):
    # Equivalent sector under reversed basis, as emitted for transformed geometry.
    data,_,_,_=fixture(tmp_path);orig=mod.Reader.arc
    def reversed_arc(self,key,scale):
        a=orig(self,key,scale)
        if key==data['elbows'][0]['outer_arc_object_id']:a['directions']=a['directions'][::-1]
        return a
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod.Reader,'arc',reversed_arc)
        assert mod.calculate(data)['items'][0]['open_shell_surface_m2']==pytest.approx(.18095573684677208)
