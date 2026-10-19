"""Anonymous T28/T20 source controls; these are local regressions, not DWG acceptance."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import ezdxf
import pytest

ROOT=Path(__file__).resolve().parents[1]
DUCT=ROOT/'skills/qto-duct-air-systems/scripts'
SHARED=ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0,str(DUCT))
import build_duct_network_from_neutral as builder
from test_point_host_attachment_t20_20261019 import case,adapt,pipe_input,attachment_report,sha,write,run,POINT


def fixture(tmp_path, *, units=4, mode='mixed'):
    doc=ezdxf.new('R2013');doc.units=units
    for layer in ('DUCT-C','DUCT-O','DUCT-ARC','DUCT-RISER','DUCT-NOTE','DUCT-SINGLE','DUCT-UNKNOWN','DUCT-BULGE','DUCT-NESTED'):
        doc.layers.new(layer)
    m=doc.modelspace(); entities={}
    entities['center']=m.add_line((0,0),(1000,0),dxfattribs={'layer':'DUCT-C'})
    entities['oa']=m.add_line((2000,0),(4000,0),dxfattribs={'layer':'DUCT-O'})
    entities['ob']=m.add_line((2000,150),(4000,150),dxfattribs={'layer':'DUCT-O'})
    entities['arc']=m.add_arc((0,3000),1000,0,90,dxfattribs={'layer':'DUCT-ARC'})
    entities['riser']=m.add_arc((7000,3000),500,0,180,dxfattribs={'layer':'DUCT-RISER'})
    entities['single']=m.add_line((0,6000),(1000,6000),dxfattribs={'layer':'DUCT-SINGLE'})
    entities['note']=m.add_text('300x200 h=3.2',dxfattribs={'insert':(500,6300),'height':100,'layer':'DUCT-NOTE'})
    entities['decoy']=m.add_text('999x999',dxfattribs={'insert':(500,6001),'height':100,'layer':'DUCT-NOTE'})
    entities['unknown']=m.add_line((10,5),(800,5),dxfattribs={'layer':'DUCT-UNKNOWN'})
    entities['bulge']=m.add_lwpolyline([(10000,0,1),(12000,0,0)],format='xyb',dxfattribs={'layer':'DUCT-BULGE'})
    leaf=doc.blocks.new('LEAF');leaf.add_arc((0,0),1000,0,180,dxfattribs={'layer':'0'})
    outer=doc.blocks.new('OUTER');outer.add_blockref('LEAF',(0,0),dxfattribs={'xscale':2,'yscale':1})
    entities['nest1']=m.add_blockref('OUTER',(15000,0),dxfattribs={'layer':'DUCT-NESTED'})
    entities['nest2']=m.add_blockref('OUTER',(22000,0),dxfattribs={'layer':'DUCT-NESTED','xscale':-1})
    path=tmp_path/'anonymous.dxf';doc.saveas(path)
    result=run(SHARED/'extract_cad_neutral.py','--source-dxf',path,'--output-dir',tmp_path/'neutral')
    assert result.returncode in (0,3),result.stderr
    profile={'schema_version':builder.SCHEMA,'expected_source_sha256':sha(path),'layout':'Model','space_id':'ANON-SPACE','floor_id':'ANON-FLOOR',
             'package_id':'ANON-DUCT','target_layer_pattern':'^DUCT-','parameters':{'parameter_basis':'anonymous bounded geometry controls'},'rules':[]}
    sections={'rect':{'shape':'rectangle','width':300,'height':200,'unit':'mm'},'round':{'shape':'circle','diameter':150,'unit':'mm'}}
    def rule(rid,layer,rep='centerline',section=None,**kwargs):
        value={'rule_id':rid,'selector':{'layer_pattern':'^'+layer+'$'},'representation':rep,'system':'SYS','section':section,**kwargs}
        ev={'schema_version':'qto-duct-recognition-evidence/1.0','source_sha256':profile['expected_source_sha256'],'layout':'Model','space_id':'ANON-SPACE',
            'crop':profile.get('crop'),'basis':'anonymous explicit source layer/geometry classification','source_locator':'Model:'+layer,
            **{k:value.get(k) for k in ('rule_id','selector','representation','system','section')}}
        ep=write(tmp_path/(rid+'-evidence.json'),ev);value['evidence']={'path':str(ep),'sha256':sha(ep)}
        profile['rules'].append(value);return value,ev,ep
    rule('CENTER','DUCT-C',section=sections['rect'])
    rule('OUTLINE','DUCT-O','outline',sections['round'],pair_tolerance=.1)
    rule('ARC','DUCT-ARC',section=sections['rect'])
    rule('BULGE','DUCT-BULGE',section=sections['rect'])
    rule('NESTED','DUCT-NESTED',section=sections['rect'])
    rule('RISER','DUCT-RISER','riser_projection',sections['rect'])
    r,e,ep=rule('SINGLE','DUCT-SINGLE','single_line',annotation_binding={'source_key':'CAD-'+entities['note'].dxf.handle,'expected_text':'300x200 h=3.2','dimension_unit':'mm','elevation_regex':r'h=(?P<value>\d+(?:\.\d+)?)','elevation_unit':'m','elevation_reference':'bottom'})
    e['annotation_relationships']=[{'tag_source_key':r['annotation_binding']['source_key'],'selector':r['selector'],'basis':'anonymous explicit leader/source relation','source_locator':'Model single-line note'}]
    write(ep,e);r['evidence']['sha256']=sha(ep)
    pp=write(tmp_path/'recognition.json',profile)
    return {'manifest':tmp_path/'neutral/CAD中性取证清单.json','profile':profile,'pp':pp,'entities':entities,'rule':rule,'source':path}

def build(c):
    write(c['pp'],c['profile']);return builder.build(c['manifest'],c['pp'])[0]

def consume(tmp_path,data,name='result'):
    ip=write(tmp_path/(name+'-input.json'),data);op=tmp_path/(name+'.json')
    result=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',op)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(op.read_text(encoding='utf-8'))


def test_mixed_build_actual_consumer_exact_curves_and_coverage(tmp_path):
    c=fixture(tmp_path);data=build(c)
    assert len(data['segments'])==3 and len(data['drawn_curve_segments'])==4
    assert len(data['riser_projection_candidates'])==1
    assert next(s for s in data['segments'] if s['representation']=='outline')['diameter']==150
    single=next(s for s in data['segments'] if s['representation']=='single_line')
    assert (single['width'],single['height'],single['elevation']['value'])==(300,200,3200)
    assert data['coverage']['quantity_complete'] is False
    unknown=[r for r in data['coverage']['rows'] if r['layer']=='DUCT-UNKNOWN']
    assert len(unknown)==1 and unknown[0]['disposition']=='unrecognized' # lies in the counted centreline's wide buffer
    out=consume(tmp_path,data)
    assert out['metrics']['straight_length']==2 and out['metrics']['straight_length_rule']==2
    arc=next(r for r in out['drawn_curve_segments'] if r['representation']=='centerline' and r['source_ids'][0].startswith('CAD-'+c['entities']['arc'].dxf.handle+'#'))
    assert arc['length']==pytest.approx(math.pi/2)
    assert out['metrics']['totals_complete'] is False
    assert any(p['kind']=='vertical_projection_height_unresolved' and p['length'] is None for p in out['pending'])
    nested=[r for r in out['drawn_curve_segments'] if '/OUTER/' in r['source_instance_path']]
    assert len(nested)==2 and nested[0]['length']==pytest.approx(nested[1]['length']) and nested[0]['length']>4
    assert nested[0]['source_instance_path']!=nested[1]['source_instance_path']


def test_unknown_source_unit_never_inferred_from_metre_elevation(tmp_path):
    c=fixture(tmp_path,units=0);data=build(c)
    assert data['unit']=='raw_DU' and not any(s.get('diameter') for s in data['segments'])
    out=consume(tmp_path,data)
    assert out['metrics']['straight_length'] is None and out['length_summary'] is None
    assert all(r['length'] is None for r in out['drawn_curve_segments'])
    assert out['source_xy_unit_verified'] is False
    assert all(g['length'] is None for g in out.get('accessory_gaps',[]))


def test_annotation_without_explicit_relationship_rejected(tmp_path):
    c=fixture(tmp_path);r=c['profile']['rules'][-1];ep=Path(r['evidence']['path']);ev=json.loads(ep.read_text())
    ev.pop('annotation_relationships');write(ep,ev);r['evidence']['sha256']=sha(ep)
    with pytest.raises(ValueError,match='最近标签'): build(c)


def test_geometry_or_system_tampering_rejected_by_real_consumer(tmp_path):
    c=fixture(tmp_path);data=build(c);data['segments'][0]['end'][0]+=100
    ip=write(tmp_path/'tampered-input.json',data);op=tmp_path/'tampered-out.json'
    result=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',op)
    assert result.returncode==2 and not op.exists()


def test_curve_crop_uses_full_extent_not_just_endpoints(tmp_path):
    c=fixture(tmp_path);c['profile']['crop']=[-100,2900,1100,3900]
    for r in c['profile']['rules']:
        ep=Path(r['evidence']['path']);ev=json.loads(ep.read_text());ev['crop']=c['profile']['crop'];write(ep,ev);r['evidence']['sha256']=sha(ep)
    data=build(c);arc=[r for r in data['coverage']['rows'] if r['layer']=='DUCT-ARC'][0]
    assert arc['scope_state']=='crossing' and arc['disposition']=='unrecognized'


def test_outline_ambiguity_partial_overlap_and_cross_plane_stay_pending(tmp_path):
    a={'part':{'kind':'line','start':[0,0,0],'end':[1000,0,0]}}
    b={'part':{'kind':'line','start':[0,150,0],'end':[1000,150,0]}}
    assert builder.paired_geometry(a,b,150,.1)['start']==[0,75,0]
    b['part']['start'][0]=500
    assert builder.paired_geometry(a,b,150,.1)['start']==[500,75,0]
    b={'part':{'kind':'line','start':[0,0,150],'end':[1000,0,150]}}
    assert builder.paired_geometry(a,b,150,.1) is None


def test_concentric_arc_outlines_keep_radius_and_sweep():
    def arc(radius):return {'part':{'kind':'ellipse_arc','center':[0,0,0],'u':[radius,0,0],'v':[0,radius,0],'start_param':0,'sweep_param':math.pi/2}}
    mid=builder.paired_geometry(arc(1000),arc(1150),150,.01)
    assert mid['u']==[1075,0,0] and builder.checked_geometry(builder.geometry(mid))['length']==pytest.approx(1075*math.pi/2)


def test_cli_protects_input_hardlink_and_output_alias(tmp_path):
    c=fixture(tmp_path);out=tmp_path/'occupied';out.mkdir();target=out/'风管实际消费输入.json';os.link(c['pp'],target);before=c['pp'].read_bytes()
    result=run(DUCT/'build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',c['pp'],'--output-dir',out)
    assert result.returncode==2 and c['pp'].read_bytes()==before


def duct_case(tmp_path):
    c=case(tmp_path)
    for item in c['ledger']['evidence'][-1]['host_segment_classifications']:item['spec']='D100'
    write(c['lp'],c['ledger']);c['profile']['adjudication_ledger']['sha256']=sha(c['lp'])
    c['segments']['segments'][0]['spec']='D100';write(c['sp'],c['segments']);c['profile']['point_host_attachment']['segments']['sha256']=sha(c['sp'])
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet']='inline_network'
    out,_=adapt(c);ip,original=pipe_input(c,out/'点位位置与管段挂接.json')
    data={**original,'schema_version':'qto-duct-fitting-vertical-input/1.0','package_id':'ANON-DUCT','floors':[{'floor_id':'F'}],
          'parameters':{'parameter_basis':'anonymous actual inline duct controls'},
          'segments':[{**original['segments'][0],'floor_id':'F','diameter':100,'size_evidence_level':'规则推算'}]}
    data['valves'][0]['valve_type']='anonymous component'
    write(ip,data);return c,out,ip,data


def test_duct_actual_reception_and_point_replay_unique_outlet(tmp_path):
    c,out,ip,data=duct_case(tmp_path);dest=tmp_path/'received'
    p=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert p.returncode==0,p.stdout+p.stderr
    rp=dest/'风管在线附件数量接收.json';receipt=json.loads(rp.read_text(encoding='utf-8'))
    assert receipt['professional_owner']=='qto-duct-air-systems' and receipt['quantity_items'][0]['spec']=='D100'
    result=json.loads((dest/'风管实际消费结果.json').read_text(encoding='utf-8'))
    assert result['metrics']['valve_count']==1 and result['valve_summary'][0]['instance_count']==1
    assert result['valves'][0]['point']==[11000,500,0]
    c['pp']=tmp_path/'received-profile.json';c['profile']['point_host_attachment']['receptions']=[{'path':str(rp),'sha256':sha(rp)}]
    final,report=adapt(c,'final');row=attachment_report(final)['rows'][0]
    assert row['placement']['insertion_point']==[0,0,0] and row['placement']['geometry_point']==[11000,500,0]
    assert row['quantity_outlet_status']=='received_by_network'
    # Idempotent replay changes no source and receives exactly once.
    repeat,_=adapt(c,'repeat');assert attachment_report(repeat)['inline_pending_count']==0
    c['profile']['point_host_attachment']['receptions'].append({'path':str(rp),'sha256':sha(rp)})
    duplicate,_=adapt(c,'duplicate');assert attachment_report(duplicate)['rows'][0]['quantity_outlet_status']=='blocked'


@pytest.mark.parametrize('kind',['point','connection','source','system','independent'])
def test_duct_reception_rejects_conflict_and_no_writes(tmp_path,kind):
    c,out,ip,data=duct_case(tmp_path);before=c['pp'].read_bytes()
    if kind=='point':data['valves'][0]['point']=[0,0,0]
    elif kind=='connection':data['valves'][0]['connected_segment_ids']=[]
    elif kind=='source':data['segments'][0]['source_instance_path']='OTHER'
    elif kind=='system':data['segments'][0]['system']='OTHER'
    elif kind=='independent':
        report=json.loads((out/'点位位置与管段挂接.json').read_text(encoding='utf-8'));report['rows'][0]['quantity_outlet']='independent_point';write(out/'点位位置与管段挂接.json',report)
    write(ip,data);dest=tmp_path/'reject'
    p=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert p.returncode==2 and not dest.exists() and c['pp'].read_bytes()==before

def test_true_arc_endpoint_tangents_join_actual_network_without_chord(tmp_path):
    arc={'kind':'ellipse_arc','center':[0,0,0],'u':[1000,0,0],'v':[0,1000,0],'start_param':0,'sweep_param':math.pi/2}
    common={'system':'SYS','space_id':'S','floor_id':'F','width':300,'height':200,'size_evidence_level':'规则推算'}
    data={'schema_version':'qto-duct-fitting-vertical-input/1.0','unit':'mm','package_id':'CURVED','space_id':'S',
          'parameters':{'parameter_basis':'explicit source ARC joins','end_snap_tolerance':.01},'floors':[{'floor_id':'F'}],
          'segments':[{**common,'segment_id':'A','start':[1000,-1000],'end':[1000,0],'source_ids':['ANON-A']},
                      {**common,'segment_id':'B','start':[0,1000],'end':[-1000,1000],'source_ids':['ANON-B']}],
          'drawn_curve_segments':[{**common,'segment_id':'ARC','geometry':builder.geometry(arc),'source_ids':['ANON-ARC']}]}
    out=consume(tmp_path,data)
    assert len(out['source_curve_network']['joins'])==2
    assert not out['source_curve_network']['pending']
    assert out['metrics']['measured_length_rule']==pytest.approx(2+math.pi/2)
    assert out['drawn_curve_segments'][0]['length']==pytest.approx(math.pi/2)
    assert out['counts']['accessory_gaps']==0
    data['segments'][0]['space_id']='OTHER'
    from duct_network_extensions import source_curve_network
    graph,_=source_curve_network(data)
    assert len(graph['joins'])==1 and graph['pending']


def external_measurement_case(c,directory,*,raw=False):
    directory.mkdir(parents=True,exist_ok=True)
    data=build(c);row=next(r for r in data['coverage']['rows'] if r['layer']=='DUCT-UNKNOWN')
    quantity_unit='raw_DU' if raw else 'm';dimension='drawing_length' if raw else 'length'
    kind='duct_centerline_drawing_length' if raw else 'duct_centerline_length'
    gross=row['raw_geometry_length'] if raw or data['unit']=='raw_DU' else row['raw_geometry_length']*.001
    actual={'source_ids':[row['source_key']],'length':gross,'unit':quantity_unit,'quantity_dimension':dimension,
            'quantity_kind':kind,'length_basis':'gross_centerline','measurement_status':'calculated','currentness_status':'current'}
    evidence={'source_sha256':data['source_sha256'],'source_key':row['source_key'],'geometry_sha256':row['geometry_sha256'],
              'layout':'Model','space_id':'ANON-SPACE','disposition':'counted','basis':'anonymous current external takeoff','source_locator':'frozen actual row 0'}
    ep=write(directory/'source-evidence.json',evidence)
    case={'c':c,'directory':directory,'data':data,'source_row':row,'actual':actual,'quantity_path':directory/'actual.json',
          'measurement_path':directory/'measurement.json','claims_path':directory/'claims.json','evidence_path':ep}
    case['measurement']={'schema_version':'qto-duct-external-length-measurement/1.0',**{k:v for k,v in evidence.items() if k not in {'disposition'}},
        'source_coverage':'full_source_part',**{k:actual[k] for k in ('unit','quantity_dimension','quantity_kind','length_basis','measurement_status','currentness_status')},
        'source_evidence':data['neutral_binding']['authoritative_source'],'gross_length':gross,'attributed_length':gross,'deductions':[]}
    case['claim']={'source_key':row['source_key'],'geometry_sha256':row['geometry_sha256'],'disposition':'counted',
        'evidence':{'path':str(ep),'sha256':sha(ep)},'collection':'items','index':0,
        **{k:actual[k] for k in ('unit','quantity_dimension','quantity_kind','length_basis')}}
    refresh_external_case(case)
    return case


def refresh_external_case(case):
    write(case['quantity_path'],{'items':[case['actual']]})
    quantity_ref={'path':str(case['quantity_path']),'sha256':sha(case['quantity_path'])}
    try:row_sha=builder.digest(case['actual'])
    except ValueError:row_sha='0'*64
    case['measurement']['quantity_row']={**quantity_ref,'collection':'items','index':0,'row_sha256':row_sha}
    write(case['measurement_path'],case['measurement'])
    case['claim']['quantity_ref']=quantity_ref
    case['claim']['measurement_evidence']={'path':str(case['measurement_path']),'sha256':sha(case['measurement_path'])}
    write(case['claims_path'],{'schema_version':'qto-duct-coverage-claims/1.0','source_sha256':case['data']['source_sha256'],'claims':[case['claim']]})


def external_cli(case,name='output'):
    c=case['c'];return run(DUCT/'build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',c['pp'],
                           '--claims',case['claims_path'],'--output-dir',case['directory']/name)


def test_method_independent_exact_coverage_claims_and_non_line_exclusion(tmp_path):
    c=fixture(tmp_path);case=external_measurement_case(c,tmp_path/'external')
    external,_=builder.build(c['manifest'],c['pp'],case['claims_path'])
    assert next(r for r in external['coverage']['rows'] if r['source_key']==case['source_row']['source_key'])['disposition']=='counted'
    assert external['coverage']['quantity_complete'] is False
    case['actual']['source_ids']=['OTHER#0'];refresh_external_case(case)
    with pytest.raises(ValueError,match='完整源实例'):builder.build(c['manifest'],c['pp'],case['claims_path'])
    c['rule']('NOTES','DUCT-NOTE','exclude',exclusion_reason='source annotation has no separate duct quantity')
    excluded=build(c);notes=[r for r in excluded['coverage']['rows'] if r['layer']=='DUCT-NOTE']
    assert notes and all(r['disposition']=='excluded' for r in notes)


def test_external_claim_actual_cli_rejects_non_measurements_units_status_and_missing_proof(tmp_path):
    c=fixture(tmp_path);before=c['source'].read_bytes()
    variants=[('text','length','未测量'),('zero','length',0),('negative','length',-10),('boolean','length',True),
              ('nan','length',float('nan')),('infinity','length',float('inf')),('overflow_integer','length',10**1000),
              ('area_unit','unit','m2'),('area_dimension','quantity_dimension','area'),
              ('pending','measurement_status','pending'),('stale','currentness_status','stale'),('conflicting_status','status','未测量')]
    for name,field,value in variants:
        case=external_measurement_case(c,tmp_path/name);case['actual'][field]=value;refresh_external_case(case)
        p=external_cli(case);assert p.returncode==2,(name,p.stdout,p.stderr)
        assert not (case['directory']/'output').exists()
    case=external_measurement_case(c,tmp_path/'missing-proof');case['claim'].pop('measurement_evidence')
    write(case['claims_path'],{'schema_version':'qto-duct-coverage-claims/1.0','source_sha256':case['data']['source_sha256'],'claims':[case['claim']]})
    assert external_cli(case).returncode==2
    case=external_measurement_case(c,tmp_path/'claim-unit-mismatch');case['claim']['unit']='cm'
    refresh_external_case(case);assert external_cli(case).returncode==2
    malformed=external_measurement_case(c,tmp_path/'malformed-proof');write(malformed['measurement_path'],[])
    malformed['claim']['measurement_evidence']['sha256']=sha(malformed['measurement_path'])
    write(malformed['claims_path'],{'schema_version':'qto-duct-coverage-claims/1.0','source_sha256':malformed['data']['source_sha256'],'claims':[malformed['claim']]})
    assert external_cli(malformed).returncode==2
    assert c['source'].read_bytes()==before


def test_external_claim_gross_and_evidenced_net_cli_controls(tmp_path):
    c=fixture(tmp_path);gross=external_measurement_case(c,tmp_path/'gross');p=external_cli(gross)
    assert p.returncode==0,p.stdout+p.stderr
    net=external_measurement_case(c,tmp_path/'net')
    amount=net['actual']['length']-.2;net['actual'].update(length=amount,length_basis='net_centerline')
    net['claim']['length_basis']='net_centerline';net['measurement'].update(length_basis='net_centerline',attributed_length=amount)
    clause=net['directory']/'anonymous-measurement-basis.txt';clause.write_text('Anonymous contract control: deduct 0.20 m occupied accessory length from this source part.',encoding='utf-8')
    record={'schema_version':'qto-duct-length-deduction/1.0',**{k:net['measurement'][k] for k in ('source_sha256','source_key','geometry_sha256','layout','space_id')},
            'deduction_id':'D1','length':200,'unit':'mm','basis':'anonymous current occupied-length deduction',
            'source_locator':'anonymous contract control paragraph 1','basis_source':{'path':str(clause),'sha256':sha(clause)}}
    dp=write(net['directory']/'deduction.json',record);net['measurement']['deductions']=[{'path':str(dp),'sha256':sha(dp)}];refresh_external_case(net)
    p=external_cli(net);assert p.returncode==0,p.stdout+p.stderr
    output=json.loads((net['directory']/'output/风管图层覆盖差集.json').read_text(encoding='utf-8'))
    row=next(r for r in output['rows'] if r['source_key']==net['source_row']['source_key'])
    assert row['disposition']=='counted' and row['external_measurement_validation']['attributed_length']==pytest.approx(amount)
    # Net length is deliberately shorter than full CAD coverage, with a frozen deduction.
    assert amount<row['raw_geometry_length']*.001
    missing=external_measurement_case(c,tmp_path/'net-missing-deduction');missing['actual'].update(length=amount,length_basis='net_centerline')
    missing['claim']['length_basis']='net_centerline';missing['measurement'].update(length_basis='net_centerline',attributed_length=amount)
    refresh_external_case(missing);assert external_cli(missing).returncode==2
    duplicate=external_measurement_case(c,tmp_path/'duplicate-deduction');duplicate['actual'].update(length=amount-.2,length_basis='net_centerline')
    duplicate['claim']['length_basis']='net_centerline';duplicate['measurement'].update(length_basis='net_centerline',attributed_length=amount-.2,
        deductions=[{'path':str(dp),'sha256':sha(dp)},{'path':str(dp),'sha256':sha(dp)}])
    refresh_external_case(duplicate);assert external_cli(duplicate).returncode==2


def test_external_claim_unknown_source_unit_does_not_infer_scale(tmp_path):
    c=fixture(tmp_path,units=0);physical=external_measurement_case(c,tmp_path/'physical')
    assert external_cli(physical).returncode==2
    raw=external_measurement_case(c,tmp_path/'raw',raw=True);p=external_cli(raw)
    assert p.returncode==0,p.stdout+p.stderr
    output=json.loads((raw['directory']/'output/风管实际消费结果.json').read_text(encoding='utf-8'))
    assert output['source_xy_unit_verified'] is False and output['metrics']['straight_length'] is None
    row=next(r for r in output['coverage']['rows'] if r['source_key']==raw['source_row']['source_key'])
    assert row['external_measurement_validation']['physical_scale_verified'] is False


def test_consumer_preserves_old_output_replacement_and_source_alias_guard(tmp_path):
    c=fixture(tmp_path);data=build(c);ip=write(tmp_path/'safe-input.json',data);out=tmp_path/'result.json';out.write_text('previous output',encoding='utf-8')
    p=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',out)
    assert p.returncode==0 and json.loads(out.read_text(encoding='utf-8'))['status']=='REFERENCE_ONLY'
    alias=tmp_path/'source-alias.json';os.link(c['source'],alias);before=c['source'].read_bytes()
    p=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',alias)
    assert p.returncode==2 and c['source'].read_bytes()==before


def test_public_builder_really_executes_existing_consumer(tmp_path):
    c=fixture(tmp_path);out=tmp_path/'bulk'
    p=run(DUCT/'build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',c['pp'],'--output-dir',out)
    assert p.returncode==0,p.stdout+p.stderr
    log=json.loads((out/'实际风管消费日志.json').read_text(encoding='utf-8'))
    actual=json.loads((out/'风管实际消费结果.json').read_text(encoding='utf-8'))
    assert log['returncode']==0 and log['command'][2].endswith('infer_duct_fittings_vertical.py')
    assert actual['source_curve_network']['edges'] and actual['metrics']['drawn_curve_length_rule']>0


def test_duct_reception_result_tampering_rejected_on_replay(tmp_path):
    c,out,ip,data=duct_case(tmp_path);dest=tmp_path/'received'
    p=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert p.returncode==0,p.stdout+p.stderr
    rp=dest/'风管在线附件数量接收.json';receipt=json.loads(rp.read_text(encoding='utf-8'));actual=dest/'风管实际消费结果.json'
    changed=json.loads(actual.read_text(encoding='utf-8'));changed['valves'][0]['valve_type']='篡改类别';write(actual,changed)
    receipt['network_result']['sha256']=sha(actual);write(rp,receipt)
    c['pp']=tmp_path/'tampered-profile.json';c['profile']['point_host_attachment']['receptions']=[{'path':str(rp),'sha256':sha(rp)}]
    p=adapt(c,'tampered-final',expect=2)
    assert '消费输出不一致' in p.stderr

def test_bulk_input_and_real_duct_point_receiver_share_full_wcs_host_contract(tmp_path):
    c=case(tmp_path)
    source_id=c['segments']['segments'][0]['source_record_id']
    source=Path(json.loads((c['tmp']/'neutral/CAD中性取证清单.json').read_text(encoding='utf-8'))['authoritative_source']['path'])
    profile={'schema_version':builder.SCHEMA,'package_id':'BULK-TO-POINT','expected_source_sha256':c['source'],'layout':'Model','space_id':'S','floor_id':'F',
             'target_layer_pattern':'^HOST$','parameters':{'parameter_basis':'anonymous integrated bulk host and inline instance'},'rules':[]}
    rule={'rule_id':'HOST','selector':{'source_keys':[source_id],'layer_pattern':'^HOST$'},'representation':'centerline','system':'SYS',
          'section':{'shape':'circle','diameter':100,'unit':'mm'}}
    ev={'schema_version':'qto-duct-recognition-evidence/1.0','source_sha256':c['source'],'layout':'Model','space_id':'S','crop':None,
        **rule,'basis':'anonymous original host classification','source_locator':source_id}
    ep=write(tmp_path/'bulk-host-evidence.json',ev);rule['evidence']={'path':str(ep),'sha256':sha(ep)};profile['rules']=[rule]
    pp=write(tmp_path/'bulk-profile.json',profile)
    data,_=builder.build(c['tmp']/'neutral/CAD中性取证清单.json',pp);sid=data['segments'][0]['segment_id']
    assert len(data['segments'][0]['start'])==3
    c['segments']['segments'][0].update(segment_id=sid,spec='D100');write(c['sp'],c['segments'])
    c['profile']['point_host_attachment']['segments']['sha256']=sha(c['sp'])
    fact=c['ledger']['evidence'][-1]['host_segment_classifications'][0];fact.update(segment_id=sid,spec='D100')
    write(c['lp'],c['ledger']);c['profile']['adjudication_ledger']['sha256']=sha(c['lp'])
    c['profile']['point_host_attachment']['outlets'][0]['quantity_outlet']='inline_network'
    out,_=adapt(c);_,old=pipe_input(c,out/'点位位置与管段挂接.json')
    valve=old['valves'][0];valve['connected_segment_ids']=[sid];valve['valve_type']='anonymous component'
    connection=Path(valve['connection_evidence']['path']);value=json.loads(connection.read_text(encoding='utf-8'));value['segment_id']=sid;write(connection,value);valve['connection_evidence']['sha256']=sha(connection)
    data['valves']=[valve]
    ip=write(tmp_path/'actual-bulk-duct.json',data);dest=tmp_path/'bulk-received'
    p=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert p.returncode==0,p.stdout+p.stderr
    rp=dest/'风管在线附件数量接收.json';c['pp']=tmp_path/'bulk-received-profile.json'
    c['profile']['point_host_attachment']['receptions']=[{'path':str(rp),'sha256':sha(rp)}]
    final,_=adapt(c,'bulk-final');assert attachment_report(final)['rows'][0]['quantity_outlet_status']=='received_by_network'

@pytest.mark.parametrize('kind',['outline_partial','curve'])
def test_generated_host_nonzero_z_actual_reception_no_same_xy_other_z(tmp_path,kind):
    c=case(tmp_path);doc=ezdxf.readfile(tmp_path/'anonymous.dxf');model=doc.modelspace()
    for entity in model:
        if entity.dxftype()=='INSERT':entity.dxf.insert=(entity.dxf.insert.x,entity.dxf.insert.y,350)
        if entity.dxftype()=='LINE' and entity.dxf.layer=='HOST':entity.dxf.layer='OLD-UNUSED'
    doc.layers.new('GENERATED-HOST')
    for z in (350,850):
        if kind=='curve':model.add_arc((11000,-500,z),1000,0,180,dxfattribs={'layer':'GENERATED-HOST'})
        else:
            model.add_line((10000,450,z),(12000,450,z),dxfattribs={'layer':'GENERATED-HOST'})
            model.add_line((10500,550,z),(12500,550,z),dxfattribs={'layer':'GENERATED-HOST'})
    doc.saveas(tmp_path/'anonymous.dxf');old=c['source'];new=sha(tmp_path/'anonymous.dxf');c['source']=new
    def replace(value):
        if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
        if isinstance(value,list):return [replace(v) for v in value]
        return new if value==old else value
    c['profile']=replace(c['profile']);c['ledger']=replace(c['ledger']);write(c['lp'],c['ledger']);c['profile']['adjudication_ledger']['sha256']=sha(c['lp'])
    fresh=tmp_path/'fresh';fresh.mkdir();c['tmp']=fresh
    p=run(SHARED/'extract_cad_neutral.py','--source-dxf',tmp_path/'anonymous.dxf','--output-dir',fresh/'neutral');assert p.returncode in (0,3),p.stderr
    rule={'rule_id':'HOST','selector':{'layer_pattern':'^GENERATED-HOST$'},'representation':'centerline' if kind=='curve' else 'outline',
          'system':'SYS','section':{'shape':'circle','diameter':100,'unit':'mm'},'pair_tolerance':.01}
    ev={'schema_version':'qto-duct-recognition-evidence/1.0','source_sha256':new,'layout':'Model','space_id':'S','crop':None,**rule,
        'basis':'anonymous full source geometry and distinct elevation planes','source_locator':'Model GENERATED-HOST'}
    ep=write(tmp_path/'generated-class.json',ev);rule['evidence']={'path':str(ep),'sha256':sha(ep)}
    cfg={'schema_version':builder.SCHEMA,'package_id':'GEN','expected_source_sha256':new,'layout':'Model','space_id':'S','floor_id':'F',
         'target_layer_pattern':'^GENERATED-HOST$','parameters':{'parameter_basis':'anonymous generated host planes','end_snap_tolerance':.01},'rules':[rule]}
    pp=write(tmp_path/'generated-profile.json',cfg)
    data,_=builder.build(fresh/'neutral/CAD中性取证清单.json',pp)
    raw=data['segments']+data['drawn_curve_segments'];assert len(raw)==2
    base=write(tmp_path/'frozen-generated-input.json',data)
    hosts={'schema_version':'qto-point-host-segments/1.0','source_sha256':new,'coordinate_system':'WCS','coordinate_unit':'mm','layout':'Model',
           'generated_duct_input':{'path':str(base),'sha256':sha(base)},'segments':[{'segment_id':s['segment_id']} for s in raw]}
    hp=write(tmp_path/'generated-point-hosts.json',hosts);c['profile']['point_host_attachment']['segments']={'path':str(hp),'sha256':sha(hp)}
    c['profile']['point_host_attachment']['outlets'][0].update(quantity_outlet='inline_network',host_layers=['GENERATED-HOST'])
    out,_=adapt(c);report=attachment_report(out);row=report['rows'][0]
    assert row['host_status']=='unique_spatial_candidate' and row['placement']['geometry_point']==[11000,500,350]
    assert len(row['candidate_segments'])==1 and row['candidate_segments'][0]['start'][2]==350
    host=row['candidate_segments'][0];identity=row['instance_identity'];sid=host['segment_id']
    evidence={'schema_version':'qto-inline-connection-evidence/1.0','source_sha256':new,'evidence_id':'CONN','physical_id':row['physical_id'],
              'segment_id':sid,'layout':'Model','space_id':'S','relationship':'inline_connection','basis':'anonymous explicit source connection','source_locator':'full 3D source host'}
    ep=write(tmp_path/'connection.json',evidence)
    data['valves']=[{'valve_id':'V','source_object_id':row['physical_id'],'source_sha256':new,'source_instance_path':identity['instance_path'],
        'source_record_id':identity['source_record_id'],'quantity_unit':identity['unit'],'attachment_request_sha256':row['request_sha256'],
        'system':'SYS','space_id':'S','point':row['placement']['geometry_point'],'connected_segment_ids':[sid],
        'connection_source_id':'CONN','connection_evidence':{'path':str(ep),'sha256':sha(ep)},'valve_type':identity['category']}]
    ip=write(tmp_path/'generated-actual.json',data);dest=tmp_path/'generated-received'
    p=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert p.returncode==0,p.stdout+p.stderr
    actual=json.loads((dest/'风管实际消费结果.json').read_text(encoding='utf-8'));assert actual['counts']['joints']==0
    rp=dest/'风管在线附件数量接收.json';c['pp']=tmp_path/'generated-received-profile.json';c['profile']['point_host_attachment']['receptions']=[{'path':str(rp),'sha256':sha(rp)}]
    final,_=adapt(c,'generated-final');assert attachment_report(final)['rows'][0]['quantity_outlet_status']=='received_by_network'
    if kind=='outline_partial':
        coverage=data['coverage']['rows'];children=[r for r in coverage if r.get('parent_source_part')]
        assert sum(r['disposition']=='counted' for r in children)==4
        assert sum(r['disposition']=='unrecognized' for r in children)==4
        assert all(s['length_evidence_level']=='规则推算' for s in data['segments'])

def test_generated_unknown_section_preserves_geometric_candidate(tmp_path):
    from duct_generated_hosts import generated_hosts
    c=fixture(tmp_path);rule=c['profile']['rules'][0];rule['section']=None
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'));ev['section']=None;write(ep,ev);rule['evidence']['sha256']=sha(ep)
    data=build(c);row=next(r for r in data['segments'] if r['source_record_id']=='CAD-'+c['entities']['center'].dxf.handle)
    ip=write(tmp_path/'unknown-generated.json',data)
    hosts=generated_hosts({'generated_duct_input':{'path':str(ip),'sha256':sha(ip)},'segments':[{'segment_id':row['segment_id']}]},tmp_path,
        source=data['source_sha256'],layout='Model',space='ANON-SPACE',coordinate_unit='mm',factor=1)
    assert len(hosts)==1 and hosts[0]['classification_problems'] and hosts[0]['spec']=='未知规格'

def test_external_claim_aggregate_conservation_and_source_protection(tmp_path):
    c=fixture(tmp_path);valid=external_measurement_case(c,tmp_path/'aggregate')
    valid['actual'].update(length=valid['actual']['length']+1,source_ids=[valid['source_row']['source_key'],'OUTSIDE-THIS-CLAIM#0'])
    refresh_external_case(valid);p=external_cli(valid);assert p.returncode==0,p.stdout+p.stderr
    under=external_measurement_case(c,tmp_path/'under-attribution');under['actual']['length']=.1;refresh_external_case(under)
    assert external_cli(under).returncode==2
    wide=external_measurement_case(c,tmp_path/'wide-tolerance');wide['actual'].update(length=1000000,source_ids=[wide['source_row']['source_key'],'OUTSIDE-THIS-CLAIM#0'])
    wide['measurement'].update(gross_length=100,attributed_length=100,rounding_tolerance=100);refresh_external_case(wide)
    assert external_cli(wide).returncode==2
    protected=external_measurement_case(c,tmp_path/'protected');out=protected['directory']/'output';out.mkdir()
    os.link(protected['quantity_path'],out/'风管实际消费输入.json');before=protected['quantity_path'].read_bytes()
    assert external_cli(protected).returncode==2 and protected['quantity_path'].read_bytes()==before


def test_external_claim_rejects_stale_source_and_explicit_exclusion_is_separate(tmp_path):
    c=fixture(tmp_path);stale=external_measurement_case(c,tmp_path/'old-source');stale['actual']['source_sha256']='0'*64;refresh_external_case(stale)
    assert external_cli(stale).returncode==2
    excluded=external_measurement_case(c,tmp_path/'excluded-zero');excluded['actual']['length']=0;refresh_external_case(excluded)
    excluded['claim']['disposition']='excluded';excluded['claim'].pop('measurement_evidence');excluded['claim'].pop('quantity_ref')
    ev=json.loads(excluded['evidence_path'].read_text(encoding='utf-8'));ev.update(disposition='excluded',basis='anonymous evidenced out-of-quantity scope, independently excluded')
    write(excluded['evidence_path'],ev);excluded['claim']['evidence']['sha256']=sha(excluded['evidence_path'])
    write(excluded['claims_path'],{'schema_version':'qto-duct-coverage-claims/1.0','source_sha256':excluded['data']['source_sha256'],'claims':[excluded['claim']]})
    p=external_cli(excluded);assert p.returncode==0,p.stdout+p.stderr
    result=json.loads((excluded['directory']/'output/风管图层覆盖差集.json').read_text(encoding='utf-8'))
    row=next(r for r in result['rows'] if r['source_key']==excluded['source_row']['source_key'])
    assert row['disposition']=='excluded' and 'external_measurement_validation' not in row
