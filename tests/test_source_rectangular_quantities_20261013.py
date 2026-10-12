"""Synthetic source binding and engineering invariants, not recognition accuracy."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(SHARED))
from qto_source_rectangles import SourceRectangles

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

cf=module('skills/qto-concrete-formwork/scripts/calculate_stepped_foundations_reference.py','new_cf')
pl=module('skills/qto-piles-groundwork/scripts/calculate_groundwork_regions_reference.py','new_pl')

@pytest.fixture
def drawing(tmp_path):
    doc=ezdxf.new();doc.units=0;m=doc.modelspace()
    notes={}
    for name,text in [('unit','长度单位为毫米；标高单位为米'),('tiers','F1，400/300'),
                      ('blind','垫层厚100mm，每边伸出100mm'),('replace','厚1.0m，每边超出宽度的1/4，并不小于0.5m'),
                      ('same','各区域同一底面标高-1.800m；级配砂石')]:
        notes[name]=m.add_text(text).dxf.handle
    rectangles=[]
    for x,y,w,h in [(0,0,2000,2000),(500,500,1000,1000),(2500,0,2000,2000)]:
        rectangles.append(m.add_lwpolyline([(x,y),(x+w,y),(x+w,y+h),(x,y+h)],close=True).dxf.handle)
    path=tmp_path/'source.dxf';doc.saveas(path)
    src={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    oid=lambda h:'CADINST-Model/'+h
    note=lambda n,s:{'object_id':oid(notes[n]),'text_contains':s}
    number=lambda n,s,p,g=1:{**note(n,s),'pattern':p,'group':g,'unit':'mm'}
    u={**note('unit','长度单位为毫米'),'unit':'mm'}
    faces={name:{'state':'scenario' if name.endswith('sides') else 'excluded','basis':'synthetic explicit face scenario',
                 'source_notes':[note('tiers','400/300')]} for name in ['tier_sides','bottom','exposed_horizontal_tops','blinding_sides']}
    f={'schema':'qto-stepped-foundations-reference/1.0','source':src,'unit_evidence':u,'foundations':[{
        'physical_id':'F1','tiers':[{'footprint_object_id':oid(h),'height_note':number('tiers','400/300',r'(\d+)/(\d+)',g)} for h,g in zip(rectangles[:2],[1,2])],
        'blinding':{'thickness_note':number('blind','厚100mm',r'厚(\d+)mm'), 'offset_note':number('blind','伸出100mm',r'伸出(\d+)mm')},'face_conditions':faces}]}
    extension={'source_note':note('replace','宽度的1/4'),'numerator':1,'denominator':4,
               'minimum_note':{**number('replace','不小于0.5m',r'不小于([\d.]+)m'),'unit':'m'}}
    g={'schema':'qto-groundwork-regions-reference/1.0','source':src,'unit_evidence':u,'regions':[{
       'physical_id':'R'+str(i),'footprint_object_id':oid(rectangles[i]),'extension_rule':extension} for i in [0,2]],
       'layers':[{'layer_id':'L1','material':'graded gravel','earth_state':'compacted','thickness_note':{
           **number('replace','厚1.0m',r'厚([\d.]+)m'),'unit':'m'},'material_note':note('same','级配砂石'),
           'same_vertical_extent_note':note('same','同一底面标高-1.800m')}]}
    return f,g,doc,path,rectangles,notes

def test_pair_volumes_and_face_scenario(drawing):
    f,*_=drawing;r=cf.calculate(f)['items'][0]
    assert r['concrete_volume_m3']==pytest.approx(1.9)
    assert r['blinding']['volume_m3']==pytest.approx(.484)
    assert r['contact_faces'][0]['template_area_m2']==pytest.approx(4.4)
    assert all(x['quantity_level']!='正式采用' for x in r['contact_faces'])

def test_real_overlap_union_is_not_sum(drawing):
    _,g,*_=drawing;r=pl.calculate(g)
    assert r['individual_area_sum_m2']==pytest.approx(18)
    assert r['overlap_deduction_m2']==pytest.approx(1.5)
    assert r['union_area_m2']==pytest.approx(16.5)
    assert r['layers'][0]['design_geometric_volume_m3']==pytest.approx(16.5)

@pytest.mark.parametrize('kind',['cf','pl'])
def test_fresh_source_required(drawing,kind):
    f,g,_,path,*_=drawing;path.write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='已改变'):(cf if kind=='cf' else pl).calculate(f if kind=='cf' else g)

def test_unit_note_cannot_be_renamed(drawing):
    f,*_=drawing;f['unit_evidence']['unit']='m'
    with pytest.raises(ValueError,match='physical unit'):cf.calculate(f)

def test_unitless_needs_current_note(drawing):
    f,*_=drawing;f['unit_evidence']['text_contains']='not in source'
    with pytest.raises(ValueError,match='unit note'):cf.calculate(f)

def test_numeric_text_cannot_be_invented(drawing):
    f,*_=drawing;f['foundations'][0]['tiers'][0]['height_note']['text_contains']='900/300'
    with pytest.raises(ValueError,match='numeric note'):cf.calculate(f)

@pytest.mark.parametrize('kind',['cf','pl'])
def test_physical_alias_repeat_rejected(drawing,kind):
    f,g,*_=drawing;data=f if kind=='cf' else g;key='foundations' if kind=='cf' else 'regions'
    row=copy.deepcopy(data[key][0]);row['physical_id']='renamed';data[key].append(row)
    with pytest.raises(ValueError,match='source|renamed'):(cf if kind=='cf' else pl).calculate(data)

def test_unresolved_face_stays_null(drawing):
    f,*_=drawing;f['foundations'][0]['face_conditions']['tier_sides']['state']='unresolved'
    r=cf.calculate(f)['items'][0]['contact_faces'][0]
    assert r['template_area_m2'] is None and r['geometry_area_m2']==pytest.approx(4.4)

def test_upper_outside_lower_rejected(drawing):
    f,_,_,_,rectangles,_=drawing;f['foundations'][0]['tiers'][1]['footprint_object_id']='CADINST-Model/'+rectangles[2]
    with pytest.raises(ValueError,match='beyond'):cf.calculate(f)

def test_fraction_must_match_source(drawing):
    _,g,*_=drawing;g['regions'][0]['extension_rule']['denominator']=3
    with pytest.raises(ValueError,match='fraction'):pl.calculate(g)

def test_source_rectangles_never_mutated(drawing):
    f,g,*_=drawing;before=copy.deepcopy((f,g));cf.calculate(f);pl.calculate(g)
    assert (f,g)==before
