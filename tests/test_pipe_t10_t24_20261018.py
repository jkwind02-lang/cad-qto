"""Synthetic T10 compact/full and T24 type-filter-first acceptance, no project data."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / 'skills/qto-pipe-networks/scripts'
sys.path[:0] = [str(ROOT/'skills/_qto-shared-evidence/scripts'), str(PIPE)]
import extract_cad_neutral as extract
import build_pipe_inference_input as adapter
import build_pipe_inference_inputs_batch as batch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def package(tmp, count=2, arc=False, arc_symbol=False):
    tmp.mkdir(parents=True)
    source=tmp/'source.dwg'; source.write_bytes(b'synthetic-source')
    exchange=tmp/'source.dxf'; exchange.write_bytes(b'synthetic-exchange')
    doc=ezdxf.new(); doc.units=4; model=doc.modelspace()
    for i in range(count):
        model.add_line((1000,1000+i*20),(2000,1000+i*20),dxfattribs={'layer':'PIPE'})
    if arc:
        model.add_arc((4000,4000),75,0,270,dxfattribs={'layer':'PIPE'})
    source_hash=sha(source)
    objects=[extract.convert(e,layout='Model',parent_block=None,source_hash=source_hash,
                             raw_unit='Millimeters',sequence=i) for i,e in enumerate(model,1)]
    stream=tmp/'layout.ndjson'
    stream.write_text(''.join(json.dumps(o,ensure_ascii=False)+'\n' for o in objects),encoding='utf-8')
    blocks=tmp/'blocks.ndjson'; blocks.write_text('',encoding='utf-8')
    manifest=put(tmp/'manifest.json',dict(schema_version='qto-cad-neutral-evidence/1.0',
        geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(source),sha256=source_hash),
        exchange_dxf=dict(path=str(exchange),sha256=sha(exchange)),
        drawing_unit=dict(insunits_code=4,raw_unit='Millimeters'),blocks=[],
        artifacts={'layout_entities':dict(path=str(stream),sha256=sha(stream)),
                   'block_definitions':dict(path=str(blocks),sha256=sha(blocks))}))
    family=dict(family_id='P',kind='layer',layer_pattern='^PIPE$',system='WS',decision='include',
        evidence_ids=['EV'],basis='synthetic drawn pipe',priority=10,
        allowed_entity_types=['LINE'] if arc_symbol else ['LINE','ARC'])
    families=[family]
    if arc_symbol:
        families.append(dict(family_id='X',kind='layer',layer_pattern='^PIPE$',decision='exclude',
            evidence_ids=['EV'],basis='synthetic ARC symbol only',priority=12,allowed_entity_types=['ARC']))
    mapping=put(tmp/'mapping.json',dict(schema_version='qto-pipe-batch-family-mapping/1.0',mapping_id='MAP',
        mapping_version='1',expected_source_sha256=source_hash,recognized_by='synthetic-test',
        recognized_at='2026-10-18T22:00:00+08:00',source_id='EV',network_id_prefix='SYNTHETIC',
        evidence=[dict(evidence_id='EV',path='layout.ndjson',sha256=sha(stream),kind='cad_property',
                       locator='Model synthetic',observation='line/arc scope explicitly stated')],
        unit=dict(factor_to_output=.001,output_unit='m',source_id='EV'),families=families,
        use_conventional_terminal_type_candidates=False,use_conventional_valve_type_candidates=False))
    frames=put(tmp/'frames.json',dict(schema_version='qto-pipe-batch-frames/1.0',
        frames=[dict(frame_id='F',name='合成图框',bounds=[0,0,10000,10000])]))
    return dict(tmp=tmp,manifest=manifest,mapping=mapping,frames=frames,objects=objects)


def run(pkg, name='compact', detail=None):
    out=pkg['tmp']/name
    command=[sys.executable,'-B',str(PIPE/'build_pipe_inference_inputs_batch.py'),
        '--cad-manifest',str(pkg['manifest']),'--mapping',str(pkg['mapping']),
        '--frames',str(pkg['frames']),'--output-dir',str(out)]
    if detail is not None:
        command+=['--disposition-detail',detail]
    p=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert p.returncode==0,p.stdout+p.stderr
    summary=json.loads((out/'批量转换汇总.json').read_text(encoding='utf-8'))
    return out,summary


def dispositions(out):
    return rows(out/'F/管网中性对象处置清单.jsonl')


def test_compact_record_refs_scale_linearly_and_resolve_bound_record(tmp_path):
    small,_=run(package(tmp_path/'small',80))
    large,_=run(package(tmp_path/'large',160))
    for out,n in [(small,80),(large,160)]:
        report=json.loads((out/'F/管网识别报告.json').read_text(encoding='utf-8'))
        assert report['disposition_detail']=='compact'
        ledger_path=out/'F/模型工作识别记录.json'
        assert report['adjudication_ledger']['sha256']==sha(ledger_path)
        ledger_source=json.loads(ledger_path.read_text(encoding='utf-8'))['source_sha256']
        _,entries=adapter.load_adjudication_ledger(ledger_path,ledger_source)
        dr=dispositions(out)
        assert len(dr)==n
        for row in dr:
            assert 'recognition_records' not in row
            assert 'object_ids' not in json.dumps(row)
            ref,=row['recognition_record_refs']
            authority=entries[ref['adjudication_id']]
            digest=hashlib.sha256(json.dumps(authority,ensure_ascii=False,sort_keys=True,
                separators=(',',':'),default=str).encode('utf-8')).hexdigest()
            assert ref['entry_sha256']==digest and ref['object_id_count']==n
            assert ref['rule_id'] in row['rule_ids'] and row['object_id'] in authority['object_ids']
    assert (large/'F/管网中性对象处置清单.jsonl').stat().st_size < 2.2*(small/'F/管网中性对象处置清单.jsonl').stat().st_size


def test_full_mode_preserves_original_records_and_quantity_disposition_basis(tmp_path):
    pkg=package(tmp_path/'source',8)
    compact,cs=run(pkg)
    full,fs=run(pkg,'full','full')
    assert cs['disposition_detail']=='compact' and fs['disposition_detail']=='full'
    cr,fr=dispositions(compact),dispositions(full)
    assert len(cr)==len(fr)==8
    for c,f in zip(cr,fr):
        authority,=f['recognition_records']
        ref,=f['recognition_record_refs']
        assert ref['object_id_count']==len(authority['object_ids'])==8
        assert c=={k:v for k,v in f.items() if k!='recognition_records'}
    ci=json.loads((compact/'F/管网推算输入.json').read_text(encoding='utf-8'))
    fi=json.loads((full/'F/管网推算输入.json').read_text(encoding='utf-8'))
    for key in ('segments','unit','parameters','dn_match_candidates'):
        assert ci.get(key)==fi.get(key)
    assert sum(s['length'] for s in ci['segments'])==8
    # Direct entry consumes the identical bound profile; full is not a batch-only option.
    direct=pkg['tmp']/'direct'
    p=subprocess.run([sys.executable,'-B',str(PIPE/'build_pipe_inference_input.py'),
        '--cad-manifest',str(pkg['manifest']),'--profile',str(compact/'F/识别配置.json'),
        '--adjudication-ledger',str(compact/'F/模型工作识别记录.json'),
        '--output-dir',str(direct),'--disposition-detail','full'],capture_output=True,text=True,encoding='utf-8',
        stdin=subprocess.DEVNULL)
    assert p.returncode==0,p.stdout+p.stderr
    assert rows(direct/'管网中性对象处置清单.jsonl')==fr


def test_type_only_exclusion_does_not_shadow_other_type_and_real_arc_is_kept(tmp_path):
    scoped,summary=run(package(tmp_path/'symbol',1,arc=True,arc_symbol=True))
    assert summary['status']=='READY'
    dr=dispositions(scoped)
    assert {r['entity_type']:r['disposition'] for r in dr}=={'LINE':'adopted','ARC':'excluded'}
    data=json.loads((scoped/'F/管网推算输入.json').read_text(encoding='utf-8'))
    assert len(data['segments'])==1 and data['segments'][0]['length']==1
    actual,summary=run(package(tmp_path/'actual',1,arc=True))
    assert summary['status']=='READY'
    assert all(r['disposition']=='adopted' for r in dispositions(actual))
    data=json.loads((actual/'F/管网推算输入.json').read_text(encoding='utf-8'))
    assert len(data['segments'])==2 and sum(s['length'] for s in data['segments'])>1


def test_direct_rule_selection_type_before_priority_and_real_conflict_stays_unresolved():
    base=dict(layer_re=re.compile('^PIPE$'),system='WS',decision='include')
    line=dict(base,rule_id='LINE',priority=1,allowed_entity_types=['LINE'])
    arc=dict(base,rule_id='ARC',priority=99,decision='exclude',allowed_entity_types=['ARC'])
    assert adapter.choose_layer_rule('PIPE',[line,arc],entity_type='LINE')==(line,[])
    assert adapter.choose_layer_rule('PIPE',[line,arc],entity_type='ARC')==(arc,[])
    assert adapter.choose_layer_rule('PIPE',[line,arc],entity_type='ELLIPSE')==(None,[])
    wider=dict(line,rule_id='WIDER',allowed_entity_types=['LINE','ARC'])
    selected,conflicts=adapter.choose_layer_rule('PIPE',[line,wider],entity_type='LINE')
    assert selected['rule_id']=='LINE' and not conflicts
    conflicting=dict(line,rule_id='OTHER',decision='exclude')
    selected,conflicts=adapter.choose_layer_rule('PIPE',[line,arc,conflicting],entity_type='LINE')
    assert selected is None and set(conflicts)=={'LINE','OTHER'}
    family=lambda r:dict(r,family_id=r['rule_id'],entity_types=set(r['allowed_entity_types']))
    families=dict(root=[],block=[],layer=[family(line),family(arc),family(conflicting)])
    selected,conflicts,reason=batch.classify_review_row('LINE','PIPE','',{'LINE','ARC','OTHER'},families)
    assert selected is None and set(conflicts)=={'LINE','OTHER'} and reason=='family_conflict'
    _,_,reason=batch.classify_review_row('ELLIPSE','PIPE','',{'LINE','ARC'},families)
    assert reason=='entity_type_not_covered'


def test_pending_checkpoint_mode_refs_and_full_remain_explicit(tmp_path):
    authority=dict(adjudication_id='R',subject_id='R',decision='exclude',object_ids=['X'])
    rule=dict(rule_id='R',authority=authority)
    for mode in ('compact','full'):
        out=tmp_path/mode;out.mkdir()
        dr={'X':dict(object_id='X',rule_ids=['R'],disposition='excluded')}
        adapter.save_pending_recognition(out,dr,{},None,'no calculated segments',[rule],mode)
        row,=rows(out/'管网中性对象处置清单.jsonl')
        assert bool(row['recognition_record_refs'])
        assert ('recognition_records' in row)==(mode=='full')
        report=json.loads((out/'管网识别报告.json').read_text(encoding='utf-8'))
        assert report['status']=='RECOGNITION_REQUIRED' and report['quantity'] is None
        assert report['disposition_detail']==mode
