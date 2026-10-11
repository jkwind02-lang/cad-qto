"""Portable independent cases for real arrow-to-pipe ownership leakage."""
import hashlib,importlib.util,json,os,subprocess,sys
from pathlib import Path
import ezdxf
import pytest

ROOT=Path(os.environ.get('CAD_QTO_SOURCE_ROOT',Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
def mod(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
ex=mod('ownership_extract',ROOT/'skills/_qto-shared-evidence/scripts/extract_cad_neutral.py')
adapter=mod('ownership_adapter',ROOT/'skills/qto-pipe-networks/scripts/build_pipe_inference_input.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):p.write_text(json.dumps(d),encoding='utf-8');return p

def case(tmp_path,decision,nested,unit,name,anchor_outside=False):
    factor={'mm':.001,'m':1.,'in':.0254}[unit]
    doc=ezdxf.new();doc.units={'mm':4,'m':6,'in':1}[unit]
    offset=-.75/factor if anchor_outside else 0
    b=doc.blocks.new(name);b.add_lwpolyline([(0,offset),(0,offset-.675/factor)],dxfattribs={'layer':'0'})
    if nested:
        outer=doc.blocks.new('unclassified_container');inner=outer.add_blockref(name,(0,0),dxfattribs={'layer':'PIPE'})
        top=doc.modelspace().add_blockref(outer.name,(20/factor,0),dxfattribs={'layer':'PIPE'})
    else:top=doc.modelspace().add_blockref(name,(20/factor,0),dxfattribs={'layer':'PIPE'})
    pipe_y=-1/factor if anchor_outside else 0
    doc.modelspace().add_line((0,pipe_y),(5/factor,pipe_y),dxfattribs={'layer':'PIPE'})
    source=tmp_path/'source.dxf';doc.saveas(source);identity=sha(source)
    layout=tmp_path/'layout.ndjson';blocks=tmp_path/'blocks.ndjson'
    rows=[ex.convert(e,layout='Model',parent_block=None,source_hash=identity,raw_unit=unit,sequence=i) for i,e in enumerate(doc.modelspace())]
    definitions=[ex.convert(e,layout='BlockDefinition',parent_block=bb.name,source_hash=identity,raw_unit=unit,sequence=i) for bb in doc.blocks if not bb.is_any_layout for i,e in enumerate(bb)]
    layout.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8');blocks.write_text(''.join(json.dumps(r)+'\n' for r in definitions),encoding='utf-8')
    manifest=dict(schema_version='qto-cad-neutral-evidence/1.0',geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(source),sha256=identity),exchange_dxf=dict(path=str(source),sha256=identity),drawing_unit=dict(raw_unit=unit),
        blocks=[dict(block_name=bb.name,origin=list(bb.block.dxf.base_point)) for bb in doc.blocks if not bb.is_any_layout],
        artifacts=dict(layout_entities=dict(path=str(layout),sha256=sha(layout)),block_definitions=dict(path=str(blocks),sha256=sha(blocks))))
    expanded=list(adapter.expanded_manifest_rows(layout,blocks,manifest,16,[]))
    curve_ids=[r['record_id'] for r in expanded if r['entity_type'] in ('LINE','LWPOLYLINE')]
    symbol_id=next(r['record_id'] for r in expanded if r['entity_type']=='INSERT' and r['block_name']==name)
    entries=[]
    for rid,dec,ids in [('CURVE','include',curve_ids)]+([] if decision=='none' else [('SYMBOL',decision,[symbol_id])]):
        entries.append(dict(adjudication_id=rid,subject_id=rid,professional_owner='qto-pipe-networks',source_id='fixture',recognized_by='Codex model',recognized_at='2026-10-03',
            basis='hand fixture: curve candidates defer to parent symbol identity',scope_basis='generated fixture',identity_basis='handles and transform path',cross_check='5m line and .675m symbol',
            decision=dec,decision_origin='model',status='active',object_ids=ids,evidence_ids=['RAW'],uncertainties=[]))
    ledger=dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='L',ledger_version='1',source_sha256=identity,adoption_scope='working',review_status='unreviewed',
        evidence=[dict(evidence_id='RAW',path=str(source),sha256=identity,source_sha256=identity,kind='cad_property',locator='generated source handles',observation='known 5m line and symbol')],entries=entries)
    lp=put(tmp_path/'ledger.json',ledger)
    profile=dict(schema_version='qto-pipe-recognition-profile/1.1',profile_id='P',profile_version='1',expected_source_sha256=identity,
        adjudication_ledger=dict(ledger_id='L',ledger_version='1',sha256=sha(lp)),scope=dict(layout='Model'),unit=dict(output_unit='m',factor_to_output=factor,source_id='source-unit'),
        layer_rules=[dict(rule_id='CURVE',adjudication_id='CURVE',layer_pattern='^PIPE$',system='W',decision='include',allowed_entity_types=['LINE','LWPOLYLINE'])],
        block_rules=[] if decision=='none' else [dict(rule_id='SYMBOL',adjudication_id='SYMBOL',layer_pattern='^PIPE$',block_pattern='^'+name+'$',system='W',decision=decision,valve_type='test accessory')],
        text_rules=[],use_conventional_terminal_type_candidates=False,use_conventional_valve_type_candidates=False)
    if anchor_outside:profile['scope']['bounds']=dict(xmin=-1/factor,xmax_exclusive=30/factor,ymin=-2/factor,ymax_exclusive=-.5/factor)
    mp=put(tmp_path/'manifest.json',manifest);pp=put(tmp_path/'profile.json',profile)
    process=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/build_pipe_inference_input.py'),
        '--cad-manifest',str(mp),'--profile',str(pp),'--adjudication-ledger',str(lp),'--output-dir',str(tmp_path/'out')],capture_output=True,text=True,encoding='utf-8')
    assert process.returncode==0,process.stdout+process.stderr
    result=json.loads((tmp_path/'out/管网推算输入.json').read_text(encoding='utf-8'))
    return result

@pytest.mark.parametrize('decision,nested,unit,name',[
    ('exclude',False,'mm','symbolA'),('exclude',True,'mm','symbolA'),
    ('exclude',False,'m','unrelatedName'),('exclude',True,'in','unrelatedName'),
    ('include',False,'mm','symbolA'),('include',True,'mm','symbolA'),
    ('include',False,'m','unrelatedName'),('include',True,'in','unrelatedName'),
])
def test_parent_identity_prevents_child_length_and_nested_double_count(tmp_path,decision,nested,unit,name):
    result=case(tmp_path,decision,nested,unit,name)
    assert sum(r['length'] for r in result['segments'])==pytest.approx(5,abs=1e-10)
    assert len(result['valves'])==(1 if decision=='include' else 0)

@pytest.mark.parametrize('nested',[False,True])
def test_unclassified_container_keeps_explicitly_selected_engineering_curves(tmp_path,nested):
    result=case(tmp_path,'none',nested,'m','engineeringBlock')
    assert sum(r['length'] for r in result['segments'])==pytest.approx(5.675)
    assert not result['valves']

@pytest.mark.parametrize('nested',[False,True])
def test_excluded_parent_anchor_outside_window_still_controls_inside_child(tmp_path,nested):
    result=case(tmp_path,'exclude',nested,'m','offsetSymbol',anchor_outside=True)
    assert sum(r['length'] for r in result['segments'])==pytest.approx(5)
