"""Anonymous full-circle focal-midset controls; no project/DWG adoption claim."""
import copy
import json
import math
from pathlib import Path
import pytest
from test_duct_batch_network_t28_t20_20261019 import fixture,build,consume,write,sha,run,DUCT,builder
from test_duct_source_recovery_t28_batch9_20261019 import refresh
from duct_equidistant_midline import nested_circle_descriptor,materialize,CircleMidline,focal_ellipse_length


SETTINGS={'method':'euclidean_nested_circles','distance_tolerance':1e-6,'chord_tolerance':.5,'length_tolerance':.01,
          'basis':'anonymous source ring adopts Euclidean length only; section remains unresolved',
          'source_locator':'Model:anonymous full nested circular boundaries'}


def circle(r,center=(0.,0.,0.),start=.37,sweep=math.tau):
    return {'kind':'ellipse_arc','center':list(center),'u':[float(r),0.,0.],'v':[0.,float(r),0.],
            'start_param':start,'sweep_param':sweep}


def independent_simpson(a,b,n=32768):
    # Independent parameter-speed quadrature, no production elliptic series,
    # source-distance root, adaptive scheme, or display-chord measurement.
    h=(math.pi/2)/n
    f=lambda t:math.hypot(a*math.sin(t),b*math.cos(t))
    total=f(0)+f(math.pi/2)+4*sum(f(h*j) for j in range(1,n,2))+2*sum(f(h*j) for j in range(2,n,2))
    return 4*h*total/3


@pytest.mark.parametrize('ri,ro,d',[(400,1000,200),(10,1000,950),(100,2000,1700),(400,1000,0)])
def test_focal_midset_equidistance_full_annulus_and_true_length(ri,ro,d):
    desc=nested_circle_descriptor(circle(ri),circle(ro,(d,0,0),start=-1.2,sweep=-math.tau),SETTINGS)
    model=CircleMidline(desc);geometry,summary=materialize(desc)
    assert desc is not None and geometry['closed']
    a=(ri+ro)/2;c=d/2;b=math.sqrt(a*a-c*c)
    assert desc['analytic_model']['a']==a and desc['analytic_model']['b']==pytest.approx(b)
    for t in (0,.13,.8,math.pi/2,2.8,4.4,6.2):
        p=model.point(t)['point'];di=math.dist(p,(0,0,0))-ri;do=ro-math.dist(p,(d,0,0))
        assert di>0 and do>0
        assert abs(di-do)<=summary['midline_evidence']['maximum_equidistance_residual_bound']
        assert math.dist(p,(0,0,0))+math.dist(p,(d,0,0))==pytest.approx(ri+ro)
    independent=independent_simpson(a,b)
    bounds=summary['midline_evidence']['length_enclosure']
    assert bounds['lower_bound']<=independent<=bounds['upper_bound']
    assert abs(summary['length']-independent)<=summary['length_error_estimate']<=SETTINGS['length_tolerance']
    assert summary['length']>summary['polyline_chord_length']
    assert summary['midline_evidence']['display_chord_error_bound']<=SETTINGS['chord_tolerance']
    assert summary['midline_evidence']['length_is_polyline_chord_sum'] is False
    # No source midpoint/average-radius circle may replace the focal ellipse.
    if d:
        wrong=[d/2,a,0]
        assert abs((math.dist(wrong,(0,0,0))-ri)-(ro-math.dist(wrong,(d,0,0))))>1
        assert summary['length']<math.tau*a
    else:assert summary['length']==pytest.approx(math.tau*a,abs=summary['length_error_estimate'])


def transform(part,mirror=False):
    alpha,beta=.43,.61;sign=-1 if mirror else 1
    e1=[sign*math.cos(alpha),sign*math.sin(alpha),0.]
    e2=[-math.sin(alpha)*math.cos(beta),math.cos(alpha)*math.cos(beta),math.sin(beta)]
    e3=[math.sin(alpha)*math.sin(beta),-math.cos(alpha)*math.sin(beta),math.cos(beta)]
    result=copy.deepcopy(part)
    for key in ('center','u','v'):
        v=part[key];result[key]=[sum(v[j]*e[k] for j,e in enumerate((e1,e2,e3)))+(3000.,4000.,500.)[k]*(key=='center') for k in range(3)]
    return result


@pytest.mark.parametrize('mirror,reverse',[(False,False),(True,False),(False,True),(True,True)])
def test_wcs_oblique_rotation_mirror_arbitrary_seams_and_reverse_source_order(mirror,reverse):
    a,b=circle(400),circle(1000,(200,0,0),start=2.13,sweep=-math.tau)
    _,plain=materialize(nested_circle_descriptor(a,b,SETTINGS))
    a,b=transform(a,mirror),transform(b,mirror)
    if reverse:a,b=b,a
    desc=nested_circle_descriptor(a,b,SETTINGS);model=CircleMidline(desc);g,rotated=materialize(desc)
    assert rotated['length']==pytest.approx(plain['length'],abs=rotated['length_error_estimate']+plain['length_error_estimate'])
    assert rotated['bbox3d'][5]-rotated['bbox3d'][2]>1
    for t in (.1,.7,2.2,4.6):
        p=model.point(t)['point'];centers=desc['analytic_model']
        di=math.dist(p,centers['inner_center'])-400;do=1000-math.dist(p,centers['outer_center'])
        assert abs(di-do)<=rotated['midline_evidence']['maximum_equidistance_residual_bound']
        assert all(rotated['bbox3d'][k]<=p[k]<=rotated['bbox3d'][k+3] for k in range(3))
    assert builder.checked_geometry(g)['length']==rotated['length']


@pytest.mark.parametrize('case',['noncoplanar','tilted','crossing','tangent','outside','equal','finite','ellipse'])
def test_unproved_domains_rejected_without_average_radius_or_support_circle_fill(case):
    a,b=circle(400),circle(1000,(200,0,0))
    if case=='noncoplanar':b['center'][2]=.01
    elif case=='tilted':b['v']=[0.,1000*math.cos(.01),1000*math.sin(.01)]
    elif case=='crossing':b['center'][0]=700
    elif case=='tangent':b['center'][0]=600
    elif case=='outside':b['center'][0]=2000
    elif case=='equal':b=circle(400)
    elif case=='finite':a['sweep_param']=math.pi
    else:b['u'][0]=1000.05
    assert nested_circle_descriptor(a,b,SETTINGS) is None


def test_directed_series_enclosure_tightens_and_invalid_geometry_is_not_certified():
    a,b=505.,math.sqrt(505**2-475**2);reference=independent_simpson(a,b)
    _,coarse,ce=focal_ellipse_length(a,b,.001)
    length,fine,fe=focal_ellipse_length(a,b,1e-8)
    assert fe['lower_bound']<=reference<=fe['upper_bound']
    assert ce['lower_bound']<=reference<=ce['upper_bound']
    assert fine<coarse and abs(length-reference)<=fine
    desc=nested_circle_descriptor(circle(400),circle(1000,(200,0,0)),SETTINGS)
    geometry,_=materialize(desc);geometry['parts'][0]['start'][1]+=.01
    with pytest.raises(ValueError,match='重算不一致'):builder.checked_geometry(geometry)
    desc['analytic_model']['a']+=1
    with pytest.raises(ValueError,match='冻结源参数'):materialize(desc)
    invalid=circle(400);invalid['u'][0]=True
    with pytest.raises(ValueError,match='非布尔'):nested_circle_descriptor(invalid,circle(1000),SETTINGS)
    bad={**SETTINGS,'length_tolerance':1e-15}
    with pytest.raises(ValueError,match='计长容差'):nested_circle_descriptor(circle(400),circle(1000),bad)


def ring_fixture(tmp_path,*,extra=None,identity=True,units=4,mirrored=False,finite=False):
    c=fixture(tmp_path,units=units)
    def edit(m,doc):
        for key in ('oa','ob'):m.delete_entity(doc.entitydb[c['entities'][key].dxf.handle])
        target=m
        if mirrored:target=doc.blocks.new('ANON-PAIRED-CIRCLES')
        if finite:target.add_arc((0,0),400,17,250,dxfattribs={'layer':'0' if mirrored else 'DUCT-O'})
        else:target.add_circle((0,0),400,dxfattribs={'layer':'0' if mirrored else 'DUCT-O'})
        target.add_circle((200,0),1000,dxfattribs={'layer':'0' if mirrored else 'DUCT-O'})
        if extra=='duplicate':target.add_circle((200,0),1000,dxfattribs={'layer':'0' if mirrored else 'DUCT-O'})
        if extra=='partial':target.add_arc((200,0),1000,20,80,dxfattribs={'layer':'0' if mirrored else 'DUCT-O'})
        if mirrored:m.add_blockref('ANON-PAIRED-CIRCLES',(3000,1000,200),dxfattribs={'layer':'DUCT-O','xscale':-1,'rotation':33,'extrusion':(0,.6,.8)})
    refresh(c,edit)
    baseline=build(c)
    keys=[r['source_key'] for r in baseline['coverage']['rows'] if r['layer']=='DUCT-O' and r.get('geometry')]
    rule=next(r for r in c['profile']['rules'] if r['rule_id']=='OUTLINE')
    settings=copy.deepcopy(SETTINGS)
    settings['source_pairings']=([{'physical_id':'ANON-DUCT-RING','source_part_keys':keys[:2],
        'basis':'anonymous explicit source identity binds this exact two-part ring','source_locator':'Model:paired ring source instances'}] if identity else [])
    rule['equidistant_midline']=settings
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'))
    ev['equidistant_midline']=settings;write(ep,ev);rule['evidence']['sha256']=sha(ep)
    return c,baseline,keys


@pytest.mark.parametrize('mirrored',[False,True])
def test_real_neutral_build_original_part_conservation_and_actual_consumer_length_only(tmp_path,mirrored):
    c,before,keys=ring_fixture(tmp_path,mirrored=mirrored)
    data=build(c);rows=[s for s in data['drawn_curve_segments'] if s['representation']=='outline']
    assert len(rows)==1
    row=rows[0];assert set(row['source_ids'])==set(keys) and row['midline_source_pairing']['physical_id']=='ANON-DUCT-RING'
    prior={r['source_key']:r for r in before['coverage']['rows']}
    current={r['source_key']:r for r in data['coverage']['rows']}
    for key in keys:
        assert current[key]['geometry']==prior[key]['geometry']
        assert current[key]['geometry_sha256']==prior[key]['geometry_sha256']
        assert current[key]['raw_geometry_length']==prior[key]['raw_geometry_length']
        assert current[key]['source_fraction_range']==[0,1] and current[key]['source_fraction_error_bound']==0
        assert current[key]['disposition']=='counted' and not current[key].get('partition_children')
    assert all(r.get('pairing_identity')==row['midline_source_pairing'] for r in row['source_geometry_refs'])
    assert row['diameter']==150 # nominal tag is deliberately unlike every actual outline gap
    assert row['surface_calculation_allowed'] is False and row['host_attachment_eligible'] is False
    assert row['source_cross_section_consistency']=='geometric_outline_gap_does_not_prove_source_section_semantics'
    result=consume(tmp_path,data)
    actual=next(r for r in result['drawn_curve_segments'] if r['representation']=='outline')
    expected=independent_simpson(700,math.sqrt(700**2-100**2))/1000
    assert actual['length']==pytest.approx(expected,abs=actual['length_error_estimate'])
    assert actual['length_measurement_method']=='euclidean_nested_circle_focal_ellipse_complete_integral'
    assert actual['theoretical_surface_area'] is None and actual['length']>actual['polyline_chord_length']
    assert any(p['kind']=='derived_midline_section_semantics_unresolved' for p in result['pending'])
    assert result['metrics']['totals_complete'] is False and data['coverage']['quantity_complete'] is False
    unknown=next(r for r in data['coverage']['rows'] if r['layer']=='DUCT-UNKNOWN')
    assert unknown['disposition']=='unrecognized' and unknown['quantity'] is None
    # Point attachment must still refuse this bounded display geometry.
    from duct_generated_hosts import generated_hosts
    ip=write(tmp_path/'源圈中线输入.json',data)
    selection={'generated_duct_input':{'path':str(ip),'sha256':sha(ip)},'segments':[{'segment_id':row['segment_id']}]}
    with pytest.raises(ValueError,match='误差|等距|宿主'):
        generated_hosts(selection,tmp_path,source=data['source_sha256'],layout=data['layout'],space=data['space_id'],coordinate_unit=data['coordinate_unit'],factor=1)


@pytest.mark.parametrize('case',['no_identity','duplicate','partial','finite','unknown_unit'])
def test_real_source_missing_identity_ambiguity_and_unsupported_domains_remain_null(tmp_path,case):
    c,prior,keys=ring_fixture(tmp_path,identity=case!='no_identity',extra=case if case in {'duplicate','partial'} else None,
        finite=case=='finite',units=0 if case=='unknown_unit' else 4)
    data=build(c)
    assert not [s for s in data['drawn_curve_segments'] if s['representation']=='outline']
    coverage=[r for r in data['coverage']['rows'] if r['source_key'] in keys]
    assert all(r['disposition']=='unrecognized' and r['quantity'] is None for r in coverage)
    assert all(r['raw_geometry_length'] is not None and r['geometry'] for r in coverage)
    if case in {'duplicate','partial'}:assert any(len(r.get('pairing_candidates',[]))>1 for r in coverage)
    if case=='unknown_unit':
        result=consume(tmp_path,data)
        assert result['metrics']['straight_length'] is None and result['length_summary'] is None


def test_real_consumer_rebuild_rejects_altered_source_identity_or_midline(tmp_path):
    c,_,_=ring_fixture(tmp_path);data=build(c)
    outline=next(s for s in data['drawn_curve_segments'] if s['representation']=='outline')
    outline['geometry']['derived_midline']['settings']['source_pairings'][0]['physical_id']='INVENTED'
    ip=write(tmp_path/'伪造配对输入.json',data);op=tmp_path/'不应生成.json'
    result=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',op)
    assert result.returncode==2 and not op.exists()
    rule=next(r for r in c['profile']['rules'] if r['rule_id']=='OUTLINE')
    rule['equidistant_midline']['source_pairings'][0]['source_part_keys'][1]='INVENTED#0'
    with pytest.raises(ValueError,match='不一致'):build(c)


@pytest.mark.parametrize('case',['duplicate_part','duplicate_physical','unknown_key','malformed_key'])
def test_source_identity_cannot_duplicate_or_invent_parts(tmp_path,case):
    c,_,keys=ring_fixture(tmp_path);rule=next(r for r in c['profile']['rules'] if r['rule_id']=='OUTLINE')
    claim=rule['equidistant_midline']['source_pairings'][0]
    if case=='duplicate_part':rule['equidistant_midline']['source_pairings'].append({**claim,'physical_id':'ANOTHER'})
    elif case=='duplicate_physical':rule['equidistant_midline']['source_pairings'].append(copy.deepcopy(claim))
    elif case=='unknown_key':claim['source_part_keys'][1]='ABSENT#0'
    else:claim['source_part_keys'][1]={}
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'))
    ev['equidistant_midline']=rule['equidistant_midline'];write(ep,ev);rule['evidence']['sha256']=sha(ep)
    with pytest.raises(ValueError,match='配对|源part'):build(c)
