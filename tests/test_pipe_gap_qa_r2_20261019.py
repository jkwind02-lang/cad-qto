"""R2: virtual topology cannot upgrade quantities; exact rotated source outlines."""
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
PIPE=ROOT/'skills/qto-pipe-networks/scripts'
sys.path.insert(0,str(ROOT/'tests'))
import test_pipe_serial_gaps_t25_20261019 as cases


def totals(summary):
    return sum(v for levels in summary.values() for dns in levels.values() for v in dns.values())


def test_candidate_dn_and_every_spec_derivative_keep_gap_hypothesis(tmp_path):
    data=cases.network(.675,False)
    data['segments'][0]['dn_seeds'][0].update(evidence_level='inferred',assumption_id='A-DN-SOURCE')
    data['segments'].append(dict(segment_id='TURN',system='W',start=[3.675,0],end=[3.675,1],source_ids=['EV-TURN'],dn_seeds=[]))
    data['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['LEFT','RIGHT','TURN'])
    data['valves']=[dict(valve_id='V',system='W',point=[1.3,0],source_id='EV-V',source_object_id='V',instance_level='confirmed')]
    result=cases.success(tmp_path,data)
    byid={s['segment_id']:s for s in result['segments']}
    assert byid['LEFT']['evidence_level']=='inferred'
    assert byid['LEFT']['assumption_ids']==['A-DN-SOURCE']
    for sid in ('RIGHT','TURN'):
        row=byid[sid]
        assert row['dn']=='DN80' and row['evidence_level']=='scenario' and row['quantity_level']=='方案暂估量'
        assert {'A-GAP-0001','A-DN-SOURCE'}<=set(row['assumption_ids'])
        assert result['independent_dn_assignments'][sid]['dn'] is None
    assert totals(result['length_summary'])==pytest.approx(4)
    assert totals(result['independent_drawn_length_summary'])==pytest.approx(4)
    assert result['independent_drawn_length_summary']['W']['未决量']['DN待定']==pytest.approx(3)
    assert result['length_summary']['W']['规则推算量']['DN80']==pytest.approx(1)
    assert result['conditional_drawn_segment_length']==pytest.approx(3)
    valve=result['valves'][0]
    assert valve['instance_level']=='confirmed' and valve['dn_evidence_level']=='scenario'
    assert 'A-GAP-0001' in valve['dn_assumption_ids']
    fitting=next(f for f in result['fitting_candidates'] if f['type']=='弯头候选')
    assert fitting['quantity_level']=='方案暂估量' and 'A-GAP-0001' in fitting['assumption_ids']
    assert result['reconstructed_length']==pytest.approx(4)
    assert result['bridge_quantity_items'][0]['assumption_ids']==['A-DN-SOURCE','A-GAP-0001']


def test_independent_source_on_both_sides_is_neither_downgraded_nor_recounted(tmp_path):
    data=cases.network(.675,False)
    data['segments'][1]['dn_seeds']=[dict(dn='DN80',evidence_level='inferred',source_id='EV-R-DN',assumption_id='A-R-OWN')]
    result=cases.success(tmp_path,data)
    byid={s['segment_id']:s for s in result['segments']}
    assert byid['LEFT']['evidence_level']=='confirmed'
    assert byid['RIGHT']['evidence_level']=='inferred'
    assert byid['RIGHT']['assumption_ids']==['A-R-OWN']
    assert not byid['RIGHT'].get('requires_topology_assumption')
    assert totals(result['length_summary'])==totals(result['independent_drawn_length_summary'])==pytest.approx(3)
    assert result['conditional_drawn_segment_length']==0


def test_aabb_only_input_is_pending_never_complete(tmp_path):
    data=cases.network()
    for v in data['valves']:v.pop('coverage_geometry')
    result=cases.success(tmp_path,data)
    assert result['topology_bridges']==[]
    evidence=result['rejected_gap_candidates'][0]['coverage_evidence']
    assert evidence['complete'] is False
    assert evidence['uncovered_axis_length'] is None
    assert len(evidence['missing_geometry_or_port_evidence_ids'])==3


def package(tmp_path,pipe_angle,block_angle,skew):
    sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
    import extract_cad_neutral as extract
    doc=ezdxf.new();doc.units=4;m=doc.modelspace()
    b=doc.blocks.new('ANONYMOUS')
    b.add_lwpolyline([(-200,-100),(200,-100),(200,100),(-200,100)],close=True)
    b.add_point((-200,0));b.add_point((200,0))
    radians=math.radians(pipe_angle);axis=(math.cos(radians),math.sin(radians))
    def p(t):return (2000+t*axis[0],2000+t*axis[1])
    gap=1132 if skew else 1200
    m.add_line(p(-1000),p(0),dxfattribs={'layer':'PIPE'})
    m.add_line(p(gap),p(gap+1000),dxfattribs={'layer':'PIPE'})
    for t in ([185,565,946] if skew else [200,600,1000]):
        m.add_blockref('ANONYMOUS',p(t),dxfattribs={'layer':'VALVE','rotation':block_angle})
    drawing=tmp_path/'匿名源图.dxf';doc.saveas(drawing)
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    h=sha(drawing)
    rows=[extract.convert(e,layout='Model',parent_block=None,source_hash=h,raw_unit='Millimeters',sequence=i) for i,e in enumerate(m)]
    defs=[extract.convert(e,layout='Model',parent_block='ANONYMOUS',source_hash=h,raw_unit='Millimeters',sequence=i) for i,e in enumerate(b)]
    stream,blocks=tmp_path/'布局.ndjson',tmp_path/'块定义.ndjson'
    stream.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    blocks.write_text(''.join(json.dumps(r)+'\n' for r in defs),encoding='utf-8')
    manifest=cases.put(tmp_path/'清单.json',dict(schema_version='qto-cad-neutral-evidence/1.0',geometry_schema_version='qto-cad-geometry/1.1',
        authoritative_source=dict(path=str(drawing),sha256=h),exchange_dxf=dict(path=str(drawing),sha256=h),
        drawing_unit=dict(insunits_code=4,raw_unit='Millimeters'),blocks=[dict(block_name='ANONYMOUS',origin=[0,0,0])],
        artifacts=dict(layout_entities=dict(path=str(stream),sha256=sha(stream)),block_definitions=dict(path=str(blocks),sha256=sha(blocks)))))
    mapping=cases.put(tmp_path/'映射.json',dict(schema_version='qto-pipe-batch-family-mapping/1.0',mapping_id='R2',mapping_version='1',
        expected_source_sha256=h,recognized_by='model-R2-fixture',recognized_at='2026-10-19',source_id='EV-LEGEND',
        evidence=[dict(evidence_id='EV-S',path=str(stream),sha256=sha(stream),kind='cad_property',locator='Model',observation='匿名源图PIPE为管、VALVE为附件')],
        unit=dict(factor_to_output=.001,output_unit='m',source_id='EV-S',raw_unit='Millimeters'),
        families=[dict(family_id='P',kind='layer',layer_pattern='^PIPE$',system='W',decision='include',evidence_ids=['EV-S'],basis='匿名管道源几何'),
                  dict(family_id='V',kind='block',layer_pattern='^VALVE$',block_pattern='^ANONYMOUS$',system='W',decision='include',evidence_ids=['EV-S'],basis='匿名附件源几何')] ))
    frames=cases.put(tmp_path/'图框.json',dict(schema_version='qto-pipe-batch-frames/1.0',frames=[dict(frame_id='F',bounds=[0,0,6000,6000])]))
    return manifest,mapping,frames


@pytest.mark.parametrize('pipe_angle,block_angle,skew',[(0,45,True),(0,0,False),(45,45,False)])
def test_real_single_and_batch_use_actual_rotated_outline_not_enlarged_aabb(tmp_path,pipe_angle,block_angle,skew):
    manifest,mapping,frames=package(tmp_path,pipe_angle,block_angle,skew)
    out=tmp_path/'批量'
    p=cases.command(PIPE/'build_pipe_inference_inputs_batch.py','--cad-manifest',manifest,'--mapping',mapping,'--frames',frames,'--output-dir',out,'--run-inference')
    assert p.returncode==0,p.stdout+p.stderr
    results=[json.loads((out/'F/管网推算证据.json').read_text(encoding='utf-8'))]
    single=tmp_path/'单框'
    p=cases.command(PIPE/'build_pipe_inference_input.py','--cad-manifest',manifest,'--profile',out/'F/识别配置.json',
        '--adjudication-ledger',out/'F/模型工作识别记录.json','--output-dir',single,'--inference-output',single/'证据.json')
    assert p.returncode==0,p.stdout+p.stderr
    results.append(json.loads((single/'证据.json').read_text(encoding='utf-8')))
    for result in results:
        if skew:
            assert result['topology_bridges']==[]
            proof=result['rejected_gap_candidates'][0]['coverage_evidence']
            assert proof['complete'] is False
            assert proof['uncovered_axis_length']==pytest.approx(.2834718625761443)
            assert all(r['source_points_on_pipe_axis'] is False for r in proof['instances'])
        else:
            assert len(result['topology_bridges'])==1
            proof=result['topology_bridges'][0]['coverage_evidence']
            assert proof['complete'] is True and proof['uncovered_axis_length']==0
            assert all(r['geometry_proven'] for r in proof['instances'])
