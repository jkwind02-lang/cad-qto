"""Real-project failure mechanisms with independent, portable small fixtures."""
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest
from test_audit_regressions import row, geometry
from test_manual_routes import ROOT
from qto_cad_source_contract import check_unit_factor, verify_source_file


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.mark.parametrize('raw,out,factor', [('Millimeters','m',.001),('Meters','mm',1000),('Inches','m',.0254),('米','m',1)])
def test_physical_unit_conversions(raw,out,factor):
    assert check_unit_factor(raw,out,factor)==factor


@pytest.mark.parametrize('factor', [.0005,.002,.0011,1,True,float('nan'),float('inf')])
def test_multiplicity_and_loss_cannot_hide_in_unit_factor(factor):
    with pytest.raises(ValueError):check_unit_factor('Millimeters','m',factor)


@pytest.mark.parametrize('raw', ['Unitless',None,'未知'])
def test_unknown_units_never_produce_length(raw):
    with pytest.raises(ValueError):check_unit_factor(raw,'m',.001)


@pytest.fixture
def bundle(tmp_path):
    source=tmp_path/'source.dwg';source.write_bytes(b'current-drawing-fixture')
    exchange=tmp_path/'source.dxf';exchange.write_bytes(b'current-exchange-fixture')
    doc=ezdxf.new();model=doc.modelspace()
    entities=[model.add_line((0,0),(10000,0),dxfattribs={'layer':'PIPE'}),
              model.add_text('DN80',dxfattribs={'insert':(5000,100),'layer':'TEXT'})]
    rows=[row(e) for e in entities]
    for r in rows:r['source_sha256']=sha(source)
    stream=tmp_path/'layout.ndjson';stream.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
    blocks=tmp_path/'blocks.ndjson';blocks.write_text('',encoding='utf-8')
    manifest=dict(schema_version='qto-cad-neutral-evidence/1.0',geometry_schema_version=geometry.GEOMETRY_SCHEMA,
        authoritative_source=dict(path=str(source),sha256=sha(source)),exchange_dxf=dict(path=str(exchange),sha256=sha(exchange)),
        drawing_unit=dict(raw_unit='Millimeters'),blocks=[],artifacts={
        'layout_entities':dict(path=str(stream),sha256=sha(stream)), 'block_definitions':dict(path=str(blocks),sha256=sha(blocks))})
    evidence=dict(evidence_id='E',path=str(stream),sha256=sha(stream),source_sha256=sha(source),kind='cad_property',locator='small-model',observation='one 10m line and its DN80 label')
    entries=[]
    for rid in ['PIPE','TEXT']:
        entries.append(dict(adjudication_id=rid,subject_id=rid,professional_owner='qto-pipe-networks',source_id='fixture',
            recognized_by='model',recognized_at='2026-09-29T12:00:00+08:00',basis='drawing object review',scope_basis='fixture scope',identity_basis='source and handle',cross_check='10m hand length',
            decision='include',decision_origin='model',status='active',object_ids=[r['record_id'] for r in rows],evidence_ids=['E'],uncertainties=[]))
    ledger=dict(schema_version='qto-model-recognition-ledger/1.0',ledger_id='L',ledger_version='1',source_sha256=sha(source),adoption_scope='working',review_status='unreviewed',evidence=[evidence],entries=entries)
    lp=put(tmp_path/'ledger.json',ledger)
    profile=dict(schema_version='qto-pipe-recognition-profile/1.1',profile_id='P',profile_version='1',expected_source_sha256=sha(source),
        adjudication_ledger=dict(ledger_id='L',ledger_version='1',sha256=sha(lp)),scope=dict(layout='Model',space_id='F1',bounds=dict(xmin=-1,xmax_exclusive=10001,ymin=-1,ymax_exclusive=1001)),
        unit=dict(output_unit='m',factor_to_output=.001,source_id='drawing-unit'),
        layer_rules=[dict(rule_id='PIPE',adjudication_id='PIPE',layer_pattern='^PIPE$',system='W',decision='include',allowed_entity_types=['LINE'])],
        text_rules=[dict(rule_id='TEXT',adjudication_id='TEXT',layer_pattern='^TEXT$',system='W',text_pattern=r'DN(?P<dn>\d+)',disposition='seed',evidence_level='inferred',
            max_distance=1,min_second_candidate_margin=.1,segment_bindings={rows[1]['record_id']:'SEG-'+rows[0]['record_id']})],
        use_conventional_terminal_type_candidates=False,use_conventional_valve_type_candidates=False)
    return tmp_path,manifest,profile,lp,source,exchange


def run_bundle(bundle):
    folder,manifest,profile,ledger,*_=bundle
    p=put(folder/'profile.json',profile);m=put(folder/'manifest.json',manifest);output=folder/'result.json'
    process=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/build_pipe_inference_input.py'),
        '--cad-manifest',str(m),'--profile',str(p),'--adjudication-ledger',str(ledger),'--output-dir',str(folder/'work'),'--inference-output',str(output)],capture_output=True,text=True,encoding='utf-8')
    return process,json.loads(output.read_text(encoding='utf-8')) if output.exists() else None


def test_fresh_drawing_and_explicit_dn_binding_compute_independent_length(bundle):
    p,o=run_bundle(bundle)
    assert p.returncode==0,p.stdout+p.stderr
    assert o['metrics']['raw_length']==10
    assert o['length_summary']['W']['规则推算量']['DN80']==10


@pytest.mark.parametrize('key', ['source','exchange'])
def test_changed_original_or_exchange_rejects_internally_consistent_cache(bundle,key):
    file=bundle[4 if key=='source' else 5];file.write_bytes(file.read_bytes()+b'-changed')
    p,o=run_bundle(bundle);assert p.returncode!=0 and o is None
    assert '已改变' in p.stderr


@pytest.mark.parametrize('mode',['factor','raw_unit','unknown_metadata'])
def test_adapter_uses_dimensional_unit_contract(bundle,mode):
    if mode=='factor':bundle[2]['unit']['factor_to_output']=.0005
    if mode=='raw_unit':bundle[2]['unit']['raw_unit']='Meters'
    if mode=='unknown_metadata':bundle[1]['drawing_unit']['raw_unit']='Unitless'
    p,o=run_bundle(bundle);assert p.returncode!=0 and o is None and '单位' in p.stderr


@pytest.mark.parametrize('whole',[False,True])
def test_window_crossing_is_never_silent_full_object_length(bundle,whole):
    scope=bundle[2]['scope'];scope['bounds'].update(xmin=2000,xmax_exclusive=8000)
    if whole:scope.update(boundary_policy='whole_objects',boundary_source_id='explicit full object selection')
    p,o=run_bundle(bundle)
    if whole:
        assert p.returncode==0,p.stderr
        assert o['metrics']['raw_length']==10
        report=json.loads((bundle[0]/'work/管网识别报告.json').read_text(encoding='utf-8'))
        assert len(report['scope_boundary_objects'])==1
    else:
        assert p.returncode!=0 and o is None
        report=json.loads((bundle[0]/'work/管网识别报告.json').read_text(encoding='utf-8'))
        assert report['quantity'] is None and report['status']=='RECOGNITION_REQUIRED'


def test_whole_object_override_needs_basis(bundle):
    bundle[2]['scope']['boundary_policy']='whole_objects'
    p,o=run_bundle(bundle);assert p.returncode!=0 and o is None


@pytest.mark.parametrize('fault',['space','missing','system'])
def test_explicit_dn_binding_cannot_disappear_or_cross_partition(bundle,fault):
    r=bundle[2]['text_rules'][0]
    if fault=='space':r['space_id']='F2'
    if fault=='system':r['system']='RETURN'
    if fault=='missing':r['segment_bindings'][next(iter(r['segment_bindings']))]='SEG-MISSING'
    p,o=run_bundle(bundle);assert p.returncode!=0 and o is None


def test_missing_source_is_not_current_evidence(tmp_path):
    with pytest.raises(ValueError):verify_source_file({'path':str(tmp_path/'absent.dwg'),'sha256':'a'*64},'原图')


@pytest.mark.parametrize('mode',['ordinary','audited','tampered'])
def test_extraction_keeps_conversion_review_requirement(tmp_path,mode):
    source=tmp_path/'source.dxf';d=ezdxf.new();d.modelspace().add_line((0,0),(1000,0));d.saveas(source)
    receipt={'output_sha256':sha(source),'exchange_geometry_review_required':mode=='audited','backend':'test'}
    if mode=='tampered':receipt['output_sha256']='0'*64
    put(source.with_name(source.name+'.export.json'),receipt)
    target=tmp_path/'extract'
    p=subprocess.run([sys.executable,'-B',str(ROOT/'skills/_qto-shared-evidence/scripts/extract_cad_neutral.py'),
        '--source-dxf',str(source),'--output-dir',str(target)],capture_output=True,text=True,encoding='utf-8')
    if mode=='tampered':
        assert p.returncode==2 and not (target/'CAD中性取证清单.json').exists()
    else:
        m=json.loads((target/'CAD中性取证清单.json').read_text(encoding='utf-8'))
        assert m['exchange_conversion']['geometry_review_required']==(mode=='audited')
        assert m['status']==('PARTIAL' if mode=='audited' else 'PASS')


@pytest.mark.parametrize('factor',[.001,.0005])
def test_measurement_cli_cannot_halve_length_with_unit_factor(bundle,factor):
    folder,manifest,_,_,source,_=bundle
    artifact=manifest['artifacts']['layout_entities'];stream=Path(artifact['path'])
    meta=put(folder/'meta.json',dict(metadata=dict(source_sha256=sha(source)),entity_stream_sha256=sha(stream),source_unit='Millimeters'))
    value=dict(manifest_version='qto-pipe-centerline-run/1.0',calculation_id='unit-test',source=dict(source_file=str(source),entity_stream=str(stream),metadata=str(meta),expected_source_sha256=sha(source),expected_stream_sha256=sha(stream),source_id='S',cache_id='C'),
        selection=dict(layout='Model',bounds=dict(xmin=-1,xmax_exclusive=10001,ymin=-1,ymax_exclusive=1001),systems=[dict(name='W',layers=['PIPE'],evidence_ids=['EV-SYSTEM'])],
            raw_unit='Millimeters',output_unit='m',conversion_factor=factor,scope_evidence_ids=['EV-SCOPE'],unit_evidence_ids=['EV-UNIT'],rule_ids=['R-LENGTH'],
            allowed_entity_types=['LINE'],allowed_geometry_methods=['dxf_ezdxf_type_specific'],allowed_confidence=['A']))
    run=put(folder/'measure.json',value);out=folder/'measured.json'
    p=subprocess.run([sys.executable,'-B',str(ROOT/'skills/qto-pipe-networks/scripts/measure_pipe_centerlines.py'),'--manifest',str(run),'--output',str(out),'--details',str(folder/'detail.csv')],capture_output=True,text=True,encoding='utf-8')
    if factor==.001:
        assert p.returncode==0,p.stderr
        assert out.exists()
        import csv
        items=list(csv.DictReader((folder/'detail.csv').open(encoding='utf-8-sig')))
        assert float(items[0]['output_value'])==10
    else:assert p.returncode!=0 and not out.exists()
