"""Anonymous circular partitions, Euclidean midline and QA01 controls."""
import copy
import json
import math
from pathlib import Path
import pytest
from test_duct_batch_network_t28_t20_20261019 import fixture,build,consume,write,sha,builder,duct_case,run,DUCT
from test_duct_source_recovery_t28_batch9_20261019 import refresh,curve_data
from duct_equidistant_midline import pair_descriptor,materialize,Midline,closest_ellipse
from duct_curved_outline import overlaps


SETTINGS={'method':'euclidean_nested_ellipse','distance_tolerance':1e-8,'chord_tolerance':.5,'length_tolerance':.01,
          'basis':'anonymous true Euclidean equal-distance centreline; nonuniform source gap is length-only'}

def ellipse(a,b,start=0,sweep=math.pi/2):
    return {'kind':'ellipse_arc','center':[0.,0.,0.],'u':[float(a),0.,0.],'v':[0.,float(b),0.],'start_param':start,'sweep_param':sweep}


@pytest.mark.parametrize('seam,duplicate',[(False,False),(True,False),(False,True)])
def test_arc_staggered_and_overlap_parts_count_only_mutually_unique_intervals(tmp_path,seam,duplicate):
    c=fixture(tmp_path)
    def edit(m,doc):
        for key in ('oa','ob'):m.delete_entity(doc.entitydb[c['entities'][key].dxf.handle])
        intervals=[(1000,270,90),(1150,270,0),(1150,0,90)] if seam else [(1000,0,180),(1150,0,80),(1150,80,180)]
        if duplicate:intervals.append((1150,40,60))
        for radius,start,end in intervals:m.add_arc((3000,0),radius,start,end,dxfattribs={'layer':'DUCT-O'})
    refresh(c,edit);data=build(c)
    rows=[s for s in data['drawn_curve_segments'] if s['representation']=='outline']
    expected=1075*math.radians(160 if duplicate else 180)
    assert sum(builder.checked_geometry(s['geometry'])['length'] for s in rows)==pytest.approx(expected)
    coverage=[r for r in data['coverage']['rows'] if r.get('layer')=='DUCT-O'];bykey={r['source_key']:r for r in coverage}
    for parent in coverage:
        if parent.get('partition_children'):
            assert sum(bykey[k]['raw_geometry_length'] for k in parent['partition_children'])==pytest.approx(parent['raw_geometry_length'])
    if duplicate:assert any(r.get('pairing_candidates') and r['disposition']=='unrecognized' for r in coverage)
    result=consume(tmp_path,data)
    assert sum(s['length'] for s in result['drawn_curve_segments'] if s['representation']=='outline')==pytest.approx(expected/1000)


def test_arc_reverse_sweep_rotated_parameter_basis_and_other_plane():
    a={'part':ellipse(1000,1000)}
    b={'part':{**ellipse(1150,1150,start=math.pi/2,sweep=-math.pi/2),'u':[0.,1150.,0.],'v':[-1150.,0.,0.]}}
    # B physical domain is 90..180, so there is no positive angular overlap.
    assert overlaps(a,b,150,.01)==[]
    b['part']['start_param']=0
    fr=overlaps(a,b,150,.01)
    assert len(fr)==1 and fr[0]==pytest.approx((0,1,1,0))
    b['part']['center'][2]=100
    assert overlaps(a,b,150,.01)==[]


def independent_distance(x,y,a,b):
    # Separate minimizer over actual ellipse points, rather than the production
    # Lagrange multiplier or radial roots.  Dense seed + golden refinement.
    x,y=abs(x),abs(y);n=1024
    def f(t):return (a*math.cos(t)-x)**2+(b*math.sin(t)-y)**2
    j=min(range(n+1),key=lambda i:f(i*math.pi/(2*n)))
    lo=max(0,j-1)*math.pi/(2*n);hi=min(n,j+1)*math.pi/(2*n);gr=(math.sqrt(5)-1)/2
    c,d=hi-gr*(hi-lo),lo+gr*(hi-lo)
    for _ in range(90):
        if f(c)<f(d):hi,d=d,c;c=hi-gr*(hi-lo)
        else:lo,c=c,d;d=lo+gr*(hi-lo)
    return math.sqrt(min(f(lo),f(hi),f((lo+hi)/2)))


def test_true_ellipse_midline_nearest_distances_not_average_semiaxes():
    desc=pair_descriptor(ellipse(2000,1000),ellipse(2200,1100),SETTINGS)
    model=Midline(desc);geometry,summary=materialize(desc)
    assert geometry['derived_midline']==desc
    for theta in (.12,.42,.78,1.17,1.45):
        p=model.point(theta)['point']
        left=independent_distance(*p[:2],2000,1000);right=independent_distance(*p[:2],2200,1100)
        assert abs(left-right)<1e-6
    theta=math.pi/4;r_average=1/math.hypot(math.cos(theta)/2100,math.sin(theta)/1050)
    p=[r_average*math.cos(theta),r_average*math.sin(theta)]
    assert abs(independent_distance(*p,2000,1000)-independent_distance(*p,2200,1100))>.1
    assert abs(model.point(theta)['radius']-r_average)>.1
    assert summary['length']>summary['polyline_chord_length']
    assert summary['length_error_estimate']<=SETTINGS['length_tolerance']
    assert summary['midline_evidence']['maximum_equidistance_residual_bound']<1e-6
    assert summary['midline_evidence']['display_chord_error_bound']<=SETTINGS['chord_tolerance']
    assert summary['start']==pytest.approx([2100,0,0])
    assert summary['end']==pytest.approx([0,1050,0])


def test_length_integral_matches_independent_dense_point_chord_refinement():
    desc=pair_descriptor(ellipse(2000,1000),ellipse(2200,1100),SETTINGS);model=Midline(desc)
    _,summary=materialize(desc);points=[model.point((math.pi/2)*j/4096)['point'] for j in range(4097)]
    refined=sum(math.dist(a,b) for a,b in zip(points,points[1:]))
    assert abs(summary['length']-refined)<summary['length_error_estimate']
    # Normal feet at the principal axes are known independently.
    assert model.point(0)['inner_distance']==pytest.approx(100)
    assert model.point(0)['outer_distance']==pytest.approx(100)
    assert model.point(math.pi/2)['inner_distance']==pytest.approx(50)


def ellipse_fixture(tmp_path, *, reverse=False, full=False):
    c=fixture(tmp_path)
    def edit(m,doc):
        for key in ('oa','ob'):m.delete_entity(doc.entitydb[c['entities'][key].dxf.handle])
        # Real ordinary CAD arcs in a nonuniform INSERT produce exact affine
        # ellipse parameter geometry in the existing basic-read/neutral path.
        block=doc.blocks.new('ANON-NONUNIFORM-OUTLINES')
        stop=360 if full else 90
        for radius in (1000,1100):block.add_arc((0,0),radius,0,stop,dxfattribs={'layer':'0'})
        m.add_blockref('ANON-NONUNIFORM-OUTLINES',(3000,0),dxfattribs={'layer':'DUCT-O','xscale':2,'yscale':1,'rotation':30 if reverse else 0})
    refresh(c,edit)
    rule=next(r for r in c['profile']['rules'] if r['rule_id']=='OUTLINE');rule['equidistant_midline']=copy.deepcopy(SETTINGS)
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'));ev['equidistant_midline']=rule['equidistant_midline'];write(ep,ev);rule['evidence']['sha256']=sha(ep)
    return c


@pytest.mark.parametrize('rotated',[False,True])
def test_affine_double_outline_builds_real_midline_and_actual_cli_length(tmp_path,rotated):
    c=ellipse_fixture(tmp_path,reverse=rotated);data=build(c)
    rows=[s for s in data['drawn_curve_segments'] if s['representation']=='outline']
    assert len(rows)==1
    row=rows[0];assert len(row['source_geometry_refs'])==2 and row['geometry_position_error_bound']<=.5
    assert row['surface_calculation_allowed'] is False and row['host_attachment_eligible'] is False
    result=consume(tmp_path,data)
    out=next(s for s in result['drawn_curve_segments'] if s['representation']=='outline')
    assert out['length']==pytest.approx(2.5423969935269447,abs=out['length_error_estimate'])
    assert out['length_measurement_method']=='euclidean_equidistant_implicit_curve_integral'
    assert out['theoretical_surface_area'] is None and out['length']!=out['polyline_chord_length']
    assert any(p['kind']=='derived_midline_section_semantics_unresolved' for p in result['pending'])
    assert len(result['source_curve_network']['endpoints'])>=2


def test_full_ellipse_allows_non_principal_source_start_and_closed_loop():
    a=ellipse(2000,1000,start=.24,sweep=math.tau);b=ellipse(2200,1100,start=-.37,sweep=-math.tau)
    desc=pair_descriptor(a,b,SETTINGS);g,v=materialize(desc)
    assert g['closed'] and v['length']==pytest.approx(4*2542.3969935269447,abs=.01)
    # Identical shear of both source circles is still a coaxial complete ellipse.
    a['u']=[2000.,0.,0.];a['v']=[300.,1000.,0.];b['u']=[2200.,0.,0.];b['v']=[330.,1100.,0.]
    desc=pair_descriptor(a,b,SETTINGS);assert desc is not None
    _,v=materialize(desc);assert v['length']>0 and v['length_error_estimate']<=.01


@pytest.mark.parametrize('case',['thick','crossing','endcap','noncoaxial'])
def test_unproved_ellipse_domains_are_not_filled_by_average_axes(case):
    a,b=ellipse(2000,1000),ellipse(2200,1100)
    if case=='thick':a,b=ellipse(500,100),ellipse(2000,300)
    elif case=='crossing':b=ellipse(2200,900)
    elif case=='endcap':a['start_param']=b['start_param']=.2
    else:b['center'][0]=1
    assert pair_descriptor(a,b,SETTINGS) is None


def test_tolerances_and_tessellation_tampering_cannot_certify_length():
    a,b=ellipse(2000,1000),ellipse(2200,1100)
    bad=copy.deepcopy(SETTINGS);bad['distance_tolerance']=1e-2
    with pytest.raises(ValueError):materialize(pair_descriptor(a,b,bad))
    geometry,_=materialize(pair_descriptor(a,b,SETTINGS));geometry['parts'][0]['start'][0]+=.1
    with pytest.raises(ValueError,match='重算不一致'):builder.checked_geometry(geometry)
    a['u'][0]=True
    with pytest.raises(ValueError,match='非布尔'):pair_descriptor(a,b,SETTINGS)
    # The interior major-axis medial set really has two equally near outer feet.
    with pytest.raises(ValueError,match='不唯一'):closest_ellipse(0.1,0,4,1,1e-10)


def test_qa01_two_curves_two_main_runs_block_old_straight_joint(tmp_path):
    common={'system':'SYS','space_id':'S','floor_id':'F','width':600,'height':300,'size_evidence_level':'规则推算'}
    ca={**ellipse(1000,1000,start=3*math.pi/2,sweep=math.pi/2),'center':[0.,1000.,0.]}
    cb={**ellipse(1000,1000,start=math.pi/2,sweep=math.pi/2),'center':[1000.,0.,0.]}
    data={'schema_version':'qto-duct-fitting-vertical-input/1.0','unit':'mm','package_id':'QA01','space_id':'S',
          'parameters':{'parameter_basis':'anonymous independently found two-main port counterexample','end_snap_tolerance':.01},'floors':[{'floor_id':'F'}],
          'segments':[{**common,'segment_id':'LEFT','start':[0,0],'end':[-1000,0],'source_ids':['ANON-LEFT'],
                       'elevation':{'value':2400,'unit':'mm','reference':'bottom','source_id':'LEFT-NOTE','origin':'tag'}},
                      {**common,'segment_id':'DOWN','start':[0,0],'end':[0,-1000],'source_ids':['ANON-DOWN']}],
          'drawn_curve_segments':[{**common,'segment_id':'CA','geometry':builder.geometry(ca),'source_ids':['ANON-CA']},
                                  {**common,'segment_id':'CB','geometry':builder.geometry(cb),'source_ids':['ANON-CB']}]}
    out=consume(tmp_path,data);down=next(s for s in out['straight_segments'] if s['segment_id']=='DOWN')
    assert down['center_elevation'] is None
    assert not out['joints'] and not out['source_curve_network']['joins']
    assert out['source_curve_network']['blocked_legacy_endpoint_pairs']
    assert any(p['kind']=='curve_port_legacy_joint_blocked' for p in out['pending'])
    assert out['metrics']['straight_length']==2
    assert out['metrics']['drawn_curve_length_rule']==pytest.approx(math.pi)


def test_qa01_control_two_straight_bend_still_propagates(tmp_path):
    data=curve_data();data['drawn_curve_segments']=[]
    data['segments'][0].update(start=[0,0],end=[-1000,0],elevation={'value':2.4,'reference':'bottom','source_id':'NOTE','origin':'tag'})
    data['segments'][1].update(start=[0,0],end=[0,-1000])
    out=consume(tmp_path,data)
    assert len(out['joints'])==1
    assert out['straight_segments'][1]['center_elevation']==pytest.approx(2.5)
    assert out['straight_segments'][1]['elevation_evidence_level']=='规则推算'


def test_ellipse_endpoint_position_error_cannot_cross_snap_tolerance():
    from duct_network_extensions import source_curve_network
    geometry,summary=materialize(pair_descriptor(ellipse(2000,1000),ellipse(2200,1100),SETTINGS))
    error=summary['midline_evidence']['position_error_bound'];point=summary['start']
    common={'system':'S','space_id':'S','floor_id':'F','diameter':150}
    data={'space_id':'S','parameters':{'end_snap_tolerance':1.},'segments':[
        {**common,'segment_id':'LINE','start':[point[0]+1,point[1]-100,point[2]],'end':[point[0]+1,point[1],point[2]],'source_ids':['LINE']}],
        'drawn_curve_segments':[{**common,'segment_id':'MID','geometry':geometry,'source_ids':['OUTER-A','OUTER-B']}]}
    graph,_=source_curve_network(data)
    assert not graph['joins'] and any(p['kind']=='drawn_curve_endpoint_uncertainty' for p in graph['pending'])
    assert any(e['position_error_bound']==error for e in graph['endpoints'] if e['segment_id']=='MID')


def test_nearly_circular_ellipse_is_not_average_radius_circle_even_inside_pair_tolerance():
    a,b={'part':ellipse(1000,1000.05)},{'part':ellipse(1150,1150.05)}
    assert builder.paired_geometry(a,b,150,.1) is None
    assert pair_descriptor(a['part'],b['part'],SETTINGS) is not None


def test_nonhomothetic_nested_ellipse_and_oblique_plane_preserve_true_distance_and_length():
    a,b=ellipse(2000,1000),ellipse(2170,1150)
    desc=pair_descriptor(a,b,SETTINGS);model=Midline(desc);_,plain=materialize(desc)
    for theta in (.2,.7,1.3):
        p=model.point(theta)['point']
        assert abs(independent_distance(*p[:2],2000,1000)-independent_distance(*p[:2],2170,1150))<1e-6
    alpha,beta=.3,.6
    e1=[math.cos(alpha),math.sin(alpha),0.]
    e2=[-math.sin(alpha)*math.cos(beta),math.cos(alpha)*math.cos(beta),math.sin(beta)]
    for p in (a,b):
        p['u']=[p['u'][0]*x for x in e1];p['v']=[p['v'][1]*x for x in e2];p['center']=[3000.,4000.,500.]
    derived,tilted=materialize(pair_descriptor(a,b,SETTINGS))
    assert tilted['length']==pytest.approx(plain['length'],abs=tilted['length_error_estimate']+plain['length_error_estimate'])
    assert tilted['bbox3d'][5]>tilted['bbox3d'][2] and derived['parts'][0]['start'][2]==pytest.approx(500)


@pytest.mark.parametrize('module',['duct_curved_outline.py','duct_equidistant_midline.py'])
def test_new_curve_module_byte_change_invalidates_t20_replay_dependency(tmp_path,monkeypatch,module):
    import duct_inline_reception as reception
    c,out,ip,data=duct_case(tmp_path);dest=tmp_path/'received'
    p=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert p.returncode==0,p.stdout+p.stderr
    receipt=json.loads((dest/'风管在线附件数量接收.json').read_text(encoding='utf-8'))
    assert module in {Path(r['path']).name for r in receipt['producer_dependencies']}
    reception.verify_duct_consumption(receipt,dest,replay=False)
    actual=reception.digest
    monkeypatch.setattr(reception,'digest',lambda p:'0'*64 if Path(p).name==module else actual(p))
    with pytest.raises(ValueError,match='依赖已变化'):reception.verify_duct_consumption(receipt,dest,replay=False)
