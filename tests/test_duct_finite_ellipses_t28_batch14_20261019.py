"""Anonymous actual finite ellipses and source-drift controls; not real DWG acceptance."""
import copy
import json
import math
from pathlib import Path
import pytest
from test_duct_curved_outline_t28_r2_20261019 import SETTINGS,ellipse,independent_distance
from test_duct_batch_network_t28_t20_20261019 import fixture,build,consume,write,sha,run,DUCT,builder,duct_case
from test_duct_source_recovery_t28_batch9_20261019 import refresh
from duct_equidistant_midline import Midline,pair_descriptor,materialize
from duct_elliptical_outline import arc_pair_descriptor,radial_intervals,fractions,invert_foot,foot_angle,support


def finite_fixture(tmp_path,case='stagger',units=4):
    c=fixture(tmp_path,units=units)
    def edit(m,doc):
        for key in ('oa','ob'):m.delete_entity(doc.entitydb[c['entities'][key].dxf.handle])
        pairs=[(2000,1000,.2,2.8),(2200,1100,.2,1.2),(2200,1100,1.2,2.8)]
        if case=='duplicate':pairs.append((2200,1100,.6,.9))
        elif case=='three_supports':pairs.append((2400,1200,.4,.8))
        elif case=='thick':pairs=[(500,100,.2,2.8),(2000,300,.2,2.8)]
        elif case=='seam':pairs=[(2000,1000,5.6,7.0),(2200,1100,5.6,6.1),(2200,1100,6.1,7.0)]
        elif case=='nonhomothetic':pairs=[(2000,1000,.2,2.8),(2170,1150,.2,1.2),(2170,1150,1.2,2.8)]
        for index,(a,b,start,end) in enumerate(pairs):
            center=(3001 if case=='eccentric' and index else 3000,0,0)
            m.add_ellipse(center,(a,0,0),ratio=b/a,start_param=start%math.tau,end_param=end%math.tau,dxfattribs={'layer':'DUCT-O'})
    refresh(c,edit)
    rule=next(r for r in c['profile']['rules'] if r['rule_id']=='OUTLINE');rule['equidistant_midline']=copy.deepcopy(SETTINGS)
    ep=Path(rule['evidence']['path']);ev=json.loads(ep.read_text(encoding='utf-8'));ev['equidistant_midline']=rule['equidistant_midline'];write(ep,ev);rule['evidence']['sha256']=sha(ep)
    write(c['pp'],c['profile']);return c


def source_conservation(data):
    rows=[r for r in data['coverage']['rows'] if r['layer']=='DUCT-O'];bykey={r['source_key']:r for r in rows}
    for parent in rows:
        if parent.get('partition_children'):
            children=[bykey[key] for key in parent['partition_children']]
            assert children[0]['source_fraction_range'][0]==0 and children[-1]['source_fraction_range'][1]==1
            assert all(a['source_fraction_range'][1]==b['source_fraction_range'][0] for a,b in zip(children,children[1:]))
            assert sum(r['raw_geometry_length'] for r in children)==pytest.approx(parent['raw_geometry_length'],abs=1e-6)
    return rows


def test_nonprincipal_actual_arcs_trim_to_global_nearest_feet_not_equal_parameters():
    a,b=ellipse(2000,1000,start=.2,sweep=2.6),ellipse(2200,1100,start=.4,sweep=2.1)
    core=pair_descriptor(support(a),support(b),SETTINGS);base=Midline(core)
    intervals_a,intervals_b=radial_intervals(a,base),radial_intervals(b,base)
    lo=max(intervals_a[0][0],intervals_b[0][0]);hi=min(intervals_a[0][1],intervals_b[0][1])
    assert lo!=pytest.approx(.4) and hi!=pytest.approx(2.5)
    descriptor=arc_pair_descriptor(a,b,SETTINGS,lo,hi);model=Midline(descriptor);geometry,result=materialize(descriptor)
    for theta in (lo,(lo+hi)/2,hi):
        point=model.point(theta)['point'];left=independent_distance(*point[:2],2000,1000);right=independent_distance(*point[:2],2200,1100)
        assert abs(left-right)<1e-6
        assert .2<foot_angle(model,'inner',theta)<2.8 and .4<foot_angle(model,'outer',theta)<2.5
    assert result['length']>result['polyline_chord_length'] and result['length_error_estimate']<=.01
    assert result['midline_evidence']['trimmed_arc_domain']['source_arcs']==[a,b]
    tampered=copy.deepcopy(geometry);tampered['derived_midline']['trimmed_arc_domain']['source_arcs'][0]['sweep_param']=.01
    with pytest.raises(ValueError,match='实际有限源弧'):builder.checked_geometry(tampered)


@pytest.mark.parametrize('side',['inner','outer'])
def test_source_normal_endpoint_inverse_matches_independent_radial_foot(side):
    model=Midline(pair_descriptor(ellipse(2000,1000,sweep=math.tau),ellipse(2170,1150,sweep=math.tau),SETTINGS))
    for target in (.23,.84,1.9,3.9,5.8):
        lo,hi=invert_foot(model,side,target)
        assert abs(foot_angle(model,side,(lo+hi)/2)-target)<1e-9


@pytest.mark.parametrize('case',['stagger','duplicate','seam','nonhomothetic'])
def test_actual_finite_dxf_build_and_actual_cli_consumer_preserve_partitions(tmp_path,case):
    c=finite_fixture(tmp_path,case);data=build(c);rows=source_conservation(data)
    curves=[r for r in data['drawn_curve_segments'] if r['representation']=='outline']
    assert len(curves)>=2
    assert all(r['geometry']['derived_midline'].get('trimmed_arc_domain') for r in curves)
    assert all(r['surface_calculation_allowed'] is False and r['host_attachment_eligible'] is False for r in curves)
    assert any(r['disposition']=='unrecognized' for r in rows) # endpoint safety margins never become complete quantity
    if case=='duplicate':
        assert any(r.get('pairing_candidates') for r in rows)
        duplicate=ellipse(2200,1100,start=.6,sweep=.3);duplicate['center']=[3000.,0.,0.]
        base=Midline(pair_descriptor(support(curves[0]['geometry']['derived_midline']['trimmed_arc_domain']['source_arcs'][0]),support(duplicate),SETTINGS))
        possible=radial_intervals(duplicate,base,possible=True)
        for row in curves:
            low,high=row['geometry']['derived_midline']['trimmed_arc_domain']['radial_interval']
            assert all(high<=a+1e-13 or low>=b-1e-13 for a,b in possible)
    assert all(ref.get('source_fraction_range') and ref.get('parent_source_part') for row in curves for ref in row['source_geometry_refs'])
    consumed=consume(tmp_path,data)
    outputs=[r for r in consumed['drawn_curve_segments'] if r['representation']=='outline']
    assert len(outputs)==len(curves) and all(r['length']>0 and r['theoretical_surface_area'] is None for r in outputs)
    assert consumed['metrics']['totals_complete'] is False
    assert sum(r['length'] for r in outputs)==pytest.approx(sum(builder.checked_geometry(r['geometry'])['length'] for r in curves)/1000)


@pytest.mark.parametrize('case',['three_supports','thick','eccentric'])
def test_complex_or_unproved_source_supports_are_not_hidden_by_local_pairs(tmp_path,case):
    c=finite_fixture(tmp_path,case);data=build(c);rows=source_conservation(data)
    assert not any(r['representation']=='outline' for r in data['drawn_curve_segments'])
    assert all(r['disposition']=='unrecognized' for r in rows)
    assert all(r['quantity'] is None for r in rows)


def test_full_cli_production_consumption_and_source_context_drift(tmp_path):
    c=finite_fixture(tmp_path);out=tmp_path/'actual-cli'
    result=run(DUCT/'build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',c['pp'],'--output-dir',out)
    assert result.returncode==0,result.stdout+result.stderr
    actual=json.loads((out/'实际风管消费日志.json').read_text(encoding='utf-8'))
    assert actual['returncode']==0 and Path(actual['producer']['path']).name=='infer_duct_fittings_vertical.py'
    frozen=json.loads((out/'风管实际消费输入.json').read_text(encoding='utf-8'))
    frozen['neutral_binding']['layout_entities']['sha256']='0'*64
    ip=write(tmp_path/'bad-source-input.json',frozen);op=tmp_path/'bad-source-output.json'
    bad=run(DUCT/'infer_duct_fittings_vertical.py','--input',ip,'--output',op)
    assert bad.returncode==2 and not op.exists()
    c['profile']['space_id']='OTHER-SPACE';write(c['pp'],c['profile'])
    bad=run(DUCT/'build_duct_network_from_neutral.py','--cad-manifest',c['manifest'],'--profile',c['pp'],'--output-dir',tmp_path/'bad-context')
    assert bad.returncode==2 and not (tmp_path/'bad-context/风管实际消费结果.json').exists()


def test_new_ellipse_module_byte_drift_blocks_frozen_inline_receipt(tmp_path,monkeypatch):
    import duct_inline_reception as reception
    c,out,ip,data=duct_case(tmp_path);dest=tmp_path/'received'
    result=run(DUCT/'receive_duct_inline_components.py','--attachment-report',out/'点位位置与管段挂接.json','--network-input',ip,'--output-dir',dest)
    assert result.returncode==0,result.stdout+result.stderr
    receipt=json.loads((dest/'风管在线附件数量接收.json').read_text(encoding='utf-8'))
    assert 'duct_elliptical_outline.py' in {Path(r['path']).name for r in receipt['producer_dependencies']}
    actual=reception.digest
    monkeypatch.setattr(reception,'digest',lambda p:'0'*64 if Path(p).name=='duct_elliptical_outline.py' else actual(p))
    with pytest.raises(ValueError,match='依赖已变化'):reception.verify_duct_consumption(receipt,dest,replay=False)


def test_unknown_drawing_unit_never_inferred_from_ellipse_section(tmp_path):
    c=finite_fixture(tmp_path,units=0);data=build(c)
    assert data['unit']=='raw_DU' and not any(r['representation']=='outline' for r in data['drawn_curve_segments'])
    result=consume(tmp_path,data)
    assert result['metrics']['drawn_curve_length_rule'] is None and result['metrics']['totals_complete'] is False


@pytest.mark.parametrize('oblique',[False,True])
def test_finite_source_reverse_parameter_basis_and_oblique_wcs(oblique):
    a,b=ellipse(2000,1000,start=.2,sweep=2.6),ellipse(2200,1100,start=-.3,sweep=-2.2)
    b['v']=[-v for v in b['v']]
    if oblique:
        e1=[.8,.6,0.];e2=[-.3,.4,math.sqrt(.75)]
        for p in (a,b):
            p['u']=[p['u'][0]*v for v in e1];p['v']=[p['v'][1]*v for v in e2];p['center']=[3700.,-2400.,800.]
    core=pair_descriptor(support(a),support(b),SETTINGS);model=Midline(core)
    ra,rb=radial_intervals(a,model),radial_intervals(b,model)
    common=[(max(x,z),min(y,w)) for x,y in ra for z,w in rb if min(y,w)>max(x,z)]
    assert common
    for low,high in common:
        desc=arc_pair_descriptor(a,b,SETTINGS,low,high);geo,result=materialize(desc)
        assert result['length_error_estimate']<=.01 and result['length']>0
        assert all(0<=x<=1 for r in desc['trimmed_arc_domain']['source_foot_ranges'] for x in r['fraction_range'])
        if oblique:assert result['bbox3d'][5]>result['bbox3d'][2]
