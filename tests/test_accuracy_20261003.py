"""Hand-calculated counterexamples, run against the original or candidate source.

No expected quantity is obtained from the production measurement algorithm.
The array has two 10-unit lines at x=0 and x=1000. The gap case has a 4 m
straight route with 2 m drawn and 2 m occupied by a component.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
if not (DEFAULT_ROOT/'skills').is_dir():
    DEFAULT_ROOT = DEFAULT_ROOT/'修复后源码'
ROOT = Path(os.environ.get('CAD_QTO_SOURCE_ROOT', DEFAULT_ROOT))
SHARED = ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_cad_geometry as geometry

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

extract = module('accuracy_extract', 'skills/_qto-shared-evidence/scripts/extract_cad_neutral.py')
pipe = module('accuracy_pipe', 'skills/qto-pipe-networks/scripts/build_pipe_inference_input.py')
detail = module('accuracy_detail', 'skills/qto-pipe-networks/scripts/measure_pipe_detail_routes.py')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def array_case(tmp_path, nested=True, transformed=False):
    doc = ezdxf.new('R2018'); doc.units = 4
    leaf = doc.blocks.new('leaf'); leaf.add_line((0,0),(10,0))
    attrs = dict(row_count=1, column_count=2, column_spacing=1000)
    if nested:
        container = doc.blocks.new('container')
        arr = container.add_blockref('leaf', (0,0), dxfattribs=attrs)
        top = doc.modelspace().add_blockref('container', (50,20) if transformed else (0,0),
            dxfattribs=dict(rotation=90, xscale=-2, yscale=3) if transformed else {})
    else:
        arr = top = doc.modelspace().add_blockref('leaf', (0,0), dxfattribs=attrs)
    # Independent affine arithmetic: x=1000 -> (50,20-2*1000).
    x,y = (50,-1980) if transformed else (1000,0)
    scope = dict(xmin=x-1,xmax_exclusive=x+11,ymin=y-21,ymax_exclusive=y+1)
    source = tmp_path/'array.dxf'; doc.saveas(source)
    output = tmp_path/'neutral'; output.mkdir()
    h = sha(source)
    layouts = output/'中性布局图元.ndjson'; blocks = output/'中性块定义图元.ndjson'
    extract.write_stream(layouts, [(s.name,None,s) for s in doc.layouts],h,'mm')
    extract.write_stream(blocks, [('BlockDefinition',b.name,b) for b in doc.blocks if not b.is_any_layout],h,'mm')
    manifest = dict(schema_version='qto-cad-neutral-evidence/1.0',extractor_version=extract.VERSION,
        geometry_schema_version=geometry.GEOMETRY_SCHEMA,status='PARTIAL',authoritative_source=dict(path=str(source),sha256=h),
        drawing_unit=dict(raw_unit='mm'),blocks=[dict(block_name=b.name,origin=list(b.block.dxf.base_point)) for b in doc.blocks if not b.is_any_layout],
        artifacts=dict(layout_entities=dict(path=str(layouts),sha256=sha(layouts)),block_definitions=dict(path=str(blocks),sha256=sha(blocks))))
    mp = output/'CAD中性取证清单.json'; mp.write_text(json.dumps(manifest),encoding='utf-8')
    return doc,arr,top,scope,manifest,mp,layouts,blocks

@pytest.mark.parametrize('transformed',[False,True])
def test_nested_array_keeps_missing_instance_visible(tmp_path, transformed):
    doc,arr,top,scope,manifest,mp,layouts,blocks = array_case(tmp_path,transformed=transformed)
    failures=[]
    rows=list(pipe.expanded_manifest_rows(layouts,blocks,manifest,16,failures,target_layout='Model',scope_bounds=scope))
    assert len(failures)==1, '目标窗口含第2列，不能因第1列在窗口外而丢失阵列未决记录'
    assert failures[0]['kind']=='unsupported_insert_geometry'
    assert any(r.get('source_definition_record_id') == 'CAD-'+arr.dxf.handle for r in rows)
    assert not any(r['entity_type']=='LINE' for r in rows), '未展开阵列不冒充已计量管线'

@pytest.mark.parametrize('nested',[False,True])
def test_component_array_cli_delivers_unresolved_evidence(tmp_path,nested):
    doc,arr,top,scope,manifest,mp,layouts,blocks = array_case(tmp_path,nested=nested)
    ledger = dict(schema_version='qto-project-adjudication-ledger/1.0',ledger_id='array-review',ledger_version='1',
        source_sha256=manifest['authoritative_source']['sha256'],entries=[])
    lp=tmp_path/'ledger.json';lp.write_text(json.dumps(ledger),encoding='utf-8')
    profile=dict(schema_version='qto-component-recognition-profile/1.0',profile_id='array',profile_version='1',
        expected_source_sha256=ledger['source_sha256'],adjudication_ledger=dict(ledger_id='array-review',ledger_version='1',sha256=sha(lp)),
        rules=[dict(rule_id='review',decision='unresolved')],scope=dict(layout='Model',bounds=scope),
        unit=dict(factor_to_output=.001,output_coordinate_unit='m',source_id='DXF:INSUNITS=4'))
    pp=tmp_path/'profile.json';pp.write_text(json.dumps(profile),encoding='utf-8')
    out=tmp_path/'component'
    run=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-components-points/scripts/build_component_instances_from_neutral.py'),
        '--cad-manifest',str(mp),'--profile',str(pp),'--adjudication-ledger',str(lp),'--output-dir',str(out)],capture_output=True,text=True,encoding='utf-8')
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads((out/'点位识别报告.json').read_text(encoding='utf-8'))
    assert report['counts']['block_expansion_failure_count']==1
    assert report['status']=='REVIEW_REQUIRED' and not report['point_workpackage_complete']
    assert report['counts']['adopted_physical_instance_count']==0
    dispositions=[json.loads(x) for x in (out/'点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines()]
    row=next(r for r in dispositions if r['source_record_id']=='CAD-'+arr.dxf.handle)
    assert row['block_name']=='leaf' and row['disposition']=='unresolved'
    assert any(g['block_name']=='leaf' for g in report['unresolved_groups_top15'])

def test_regular_off_scope_block_still_prunes(tmp_path):
    doc=ezdxf.new();b=doc.blocks.new('single');line=b.add_line((0,0),(10,0));i=doc.modelspace().add_blockref('single',(0,0))
    row=lambda e,p:extract.convert(e,layout='BlockDefinition' if p else 'Model',parent_block=p,source_hash='a'*64,raw_unit='mm',sequence=1)
    defs={'single':[row(line,'single')]}
    assert geometry.definition_bounds('single',defs,{})==[0,0,0,10,0,0]
    assert not geometry.bounds_intersect(geometry.definition_bounds('single',defs,{}),dict(xmin=999,xmax_exclusive=1011,ymin=-1,ymax_exclusive=1))

def test_block_upper_bounds_include_every_coordinate():
    doc=ezdxf.new();b=doc.blocks.new('three-dimensional')
    entities=[b.add_line((-5,2,-3),(10,8,7)),b.add_line((20,-9,2),(25,11,15))]
    rows=[extract.convert(e,layout='BlockDefinition',parent_block=b.name,source_hash='a'*64,raw_unit='m',sequence=i) for i,e in enumerate(entities)]
    assert geometry.definition_bounds(b.name,{b.name:rows},{})==[-5,-9,-3,25,11,15]

@pytest.mark.parametrize('transformed',[False,True])
def test_long_block_intersecting_scope_is_not_lost(tmp_path,transformed):
    doc=ezdxf.new();leaf=doc.blocks.new('leaf');line=leaf.add_line((0,0),(1000,0))
    outer=doc.blocks.new('outer');outer.add_blockref('leaf',(0,0))
    top=doc.modelspace().add_blockref('outer',(50,20) if transformed else (0,0),
        dxfattribs=dict(rotation=90,xscale=-2,yscale=3) if transformed else {})
    x,y=(50,-1980) if transformed else (1000,0)
    scope=dict(xmin=x-1,xmax_exclusive=x+1,ymin=y-1,ymax_exclusive=y+1)
    row=lambda e,p:extract.convert(e,layout='BlockDefinition' if p else 'Model',parent_block=p,source_hash='a'*64,raw_unit='mm',sequence=1)
    lp=tmp_path/'layout.ndjson';bp=tmp_path/'blocks.ndjson'
    lp.write_text(json.dumps(row(top,None))+'\n',encoding='utf-8')
    bp.write_text(''.join(json.dumps(row(e,b.name))+'\n' for b in (leaf,outer) for e in b),encoding='utf-8')
    manifest=dict(blocks=[dict(block_name=b.name,origin=list(b.block.dxf.base_point)) for b in (leaf,outer)])
    failures=[]
    rows=list(pipe.expanded_manifest_rows(lp,bp,manifest,16,failures,target_layout='Model',scope_bounds=scope))
    curves=[r for r in rows if r['entity_type']=='LINE']
    assert len(curves)==1 and not failures
    assert curves[0]['raw_length']==pytest.approx(2000 if transformed else 1000)
    # This is an intersecting source entity, not a clipped scope quantity.
    assert curves[0]['source_definition_record_id']=='CAD-'+line.dxf.handle

def gap_case(tmp_path, unit='m', offset=.002, z=0, axial=2, angle=0):
    raw_factor={'m':1,'mm':1000,'in':1/.0254}[unit]
    def p(x,y,z=0):
        a=math.radians(angle)
        return ((20+x*math.cos(a)-y*math.sin(a))*raw_factor,(30+x*math.sin(a)+y*math.cos(a))*raw_factor,z*raw_factor)
    doc=ezdxf.new();doc.units={'m':6,'mm':4,'in':1}[unit];m=doc.modelspace()
    first=m.add_line(p(0,0),p(1,0));last=m.add_line(p(3,0),p(4,0))
    valve=doc.blocks.new('valve');valve.add_circle((0,0),.1*raw_factor)
    comp=m.add_blockref('valve',p(axial,offset,z))
    d=m.add_linear_dim(base=p(0,-1),p1=p(0,0),p2=p(4,0),angle=angle);d.render()
    source=tmp_path/'gap.dxf';doc.saveas(source)
    ref=lambda e,**kw:dict(path=[e.dxf.handle],**kw)
    data=dict(schema_version=detail.VERSION,drawing=dict(path=str(source),sha256=sha(source)),raw_unit=unit,
        join_tolerance_raw=.00001*raw_factor,views=[dict(view_id='A',role='primary',bounds3d=[-100*raw_factor]*3+[100*raw_factor]*3)],
        routes=[dict(physical_group_id='hand',physical_route_id='supply',view_id='A',review_note='手算4m直线；组件占位2m，不代替净管计量',
        dimension_paths=[[d.dimension.dxf.handle]],segments=[dict(kind='drawn',source=ref(first)),dict(kind='restored_axis',
        **{'from':ref(first,fraction=1),'to':ref(last,fraction=0)},component_paths=[[comp.dxf.handle]],reason='已绘轴线上的组件断口'),dict(kind='drawn',source=ref(last))])])
    return data

@pytest.mark.parametrize('unit,angle',[('m',0),('mm',0),('in',90),('mm',37)])
def test_off_axis_component_cannot_restore_two_metre_gap(tmp_path,unit,angle):
    data=gap_case(tmp_path,unit=unit,angle=angle)
    # 2 mm offset exceeds a 0.01 mm declared tolerance by 200 times.
    with pytest.raises(ValueError,match='构件插入点'):
        detail.measure(data)

def test_out_of_plane_component_is_also_rejected(tmp_path):
    with pytest.raises(ValueError,match='构件插入点'):
        detail.measure(gap_case(tmp_path,offset=0,z=.002))

@pytest.mark.parametrize('offset,axial',[(0,2),(.000009,2),(0,1),(0,3)])
def test_valid_component_gap_matches_independent_hand_length(tmp_path,offset,axial):
    result=detail.measure(gap_case(tmp_path,offset=offset,axial=axial))['routes'][0]
    assert result['drawn_axis_m']==pytest.approx(2)
    assert result['restored_axis_m']==pytest.approx(2)
    assert result['axis_including_components_m']==pytest.approx(4)
    assert result['billable_pipe_length_m'] is None

def test_component_beyond_segment_is_rejected(tmp_path):
    with pytest.raises(ValueError,match='构件插入点'):
        detail.measure(gap_case(tmp_path,offset=0,axial=3.002))
