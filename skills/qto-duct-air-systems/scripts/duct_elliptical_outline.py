"""Finite coaxial thin-ellipse outlines: monotone nearest-foot partitions.

Only the portion whose two global nearest feet lie on the actual source arcs
is measured.  End-cap-only loci remain unrecognized.  This is not an arbitrary
ellipse skeleton, a constant cross section, or a fabrication development.
"""
from __future__ import annotations
import copy
import math
from collections import defaultdict
from duct_equidistant_midline import principal, pair_descriptor, Midline, materialize, dot, scale, closest_ellipse

TAU=math.tau

def support(part):
    axes=principal(part)
    if axes is None:return None
    e1,e2=axes['e1'],axes['e2']
    k=max(range(3),key=lambda i:abs(e1[i]))
    if e1[k]<0:e1=scale(e1,-1)
    normal=[e1[1]*e2[2]-e1[2]*e2[1],e1[2]*e2[0]-e1[0]*e2[2],e1[0]*e2[1]-e1[1]*e2[0]]
    k=max(range(3),key=lambda i:abs(normal[i]))
    if normal[k]<0:e2=scale(e2,-1)
    return {'kind':'ellipse_arc','center':copy.deepcopy(axes['center']),
            'u':scale(e1,axes['a']),'v':scale(e2,axes['b']),'start_param':0.,'sweep_param':TAU}

def same_frame(a,b):
    aa,bb=principal(a),principal(b);size=max(aa['a'],bb['a'],1.)
    return math.dist(aa['center'],bb['center'])<=size*1e-11 and all(math.dist(aa[k],bb[k])<=1e-10 for k in ('e1','e2'))

def same_shape(a,b):
    aa,bb=principal(a),principal(b)
    return same_frame(a,b) and all(abs(aa[k]-bb[k])<=64*math.ulp(max(aa[k],bb[k],1.)) for k in ('a','b'))

def source_domain(part,frame):
    a=principal(part);s1=dot(a['e1'],frame['e1']);s2=dot(a['e2'],frame['e2'])
    if min(abs(s1),abs(s2))<1-1e-10:raise ValueError('有限椭圆源弧未共享可证主轴')
    t=part['start_param']-a['phi']
    start=math.atan2(s2*math.sin(t),s1*math.cos(t))
    return start,part['sweep_param']*(1 if s1*s2>0 else -1)

def side_of(part,model):
    return 'inner' if abs(principal(part)['a']-model.inner['a'])<abs(principal(part)['a']-model.outer['a']) else 'outer'

def foot_angle(model,side,theta):
    q=model.point(theta)[side+'_foot'];a=getattr(model,side)
    raw=math.atan2(q[1]/a['b'],q[0]/a['a'])
    return raw+TAU*round((theta-raw)/TAU)

def foot_error(model):
    # Projection on the inner convex body is 1-Lipschitz.  The outer inward
    # projection is <= 1/(1-D/reach)-Lipschitz in this entire thin annulus.
    error=model.position_error_bound/(1-model.gap/model.reach)+model.settings['distance_tolerance']/32+model.alignment_error
    if error>=model.inner['b']/4:raise ValueError('有限椭圆落足误差不足以证明源弧归属')
    return 2*error/model.inner['b']

def invert_foot(model,side,target):
    quarter=math.pi/2;axis=round(target/quarter)
    if abs(target-axis*quarter)<1e-14:return axis*quarter,axis*quarter
    axes=getattr(model,side);other=model.outer if side=='inner' else model.inner
    cs,sn=math.cos(target),math.sin(target)
    q=[axes['a']*cs,axes['b']*sn];normal=[cs/axes['a'],sn/axes['b']]
    nn=math.hypot(*normal);normal=[v/nn for v in normal];direction=1 if side=='inner' else -1
    lo,hi=0.,model.gap;dt=model.settings['distance_tolerance'];derivative_lower=model.radial_gradient_lower**2/2
    # Invert a foot parameter along its exact source normal instead of nesting
    # a radial root inside an angular root.  The equal-distance derivative is
    # 1+n_inner.n_outer >= radial_gradient_lower**2/2 > 0.  D brackets the
    # normal root by the support/Hausdorff gap and outer reach condition.
    for _ in range(180):
        d=(lo+hi)/2;p=[q[k]+direction*d*normal[k] for k in (0,1)]
        equation=(p[0]/other['a'])**2+(p[1]/other['b'])**2
        if (side=='inner' and equation>=1) or (side=='outer' and equation<=1):f,err=d,0.
        else:
            foot,err=closest_ellipse(*p,other['a'],other['b'],dt*min(1.,derivative_lower)/256)
            f=d-math.dist(p,foot)
        if hi-lo<=dt/4:break
        if f>err:hi=d
        elif f<-err:lo=d
        else:
            allowance=(abs(f)+err)/derivative_lower
            lo,hi=max(lo,d-allowance),min(hi,d+allowance)
    else:raise ValueError('有限椭圆源端点法向落足反解未收敛')
    angle=math.atan2(p[1],p[0]);angle+=TAU*round((target-angle)/TAU)
    error=(hi-lo+2*model.alignment_error)/model.inner['b']
    return angle-error,angle+error

def radial_intervals(part,model,possible=False):
    start,sweep=source_domain(part,model.frame);side=side_of(part,model)
    if abs(abs(sweep)-TAU)<=1e-10:
        # A full source still has its own parameter seam.  Split there so
        # every counted source fraction is a contiguous original interval.
        seam=sum(invert_foot(model,side,start))/2%TAU
        return [(0.,seam),(seam,TAU)] if 1e-12<seam<TAU-1e-12 else [(0.,TAU)]
    low,high=sorted((start,start+sweep));margin=4*foot_error(model)
    if not possible and high-low<=2*margin:return []
    if possible:lo,hi=invert_foot(model,side,low-margin)[0],invert_foot(model,side,high+margin)[1]
    else:lo,hi=invert_foot(model,side,low+margin)[1],invert_foot(model,side,high-margin)[0]
    if hi<=lo:return []
    result=[]
    for k in range(math.floor(-hi/TAU)-1,math.ceil((TAU-lo)/TAU)+2):
        a,b=max(0.,lo+k*TAU),min(TAU,hi+k*TAU)
        if b-a>1e-12:result.append((a,b))
    return result

def fractions(part,model,low,high):
    start,sweep=source_domain(part,model.frame);side=side_of(part,model)
    values=[foot_angle(model,side,t) for t in (low,high)]
    mid=sum(values)/2;origin=start+sweep/2;shift=TAU*round((origin-mid)/TAU)
    out=sorted((v+shift-start)/sweep for v in values)
    error=foot_error(model)/abs(sweep)
    if out[0]<-2*error or out[1]>1+2*error:raise ValueError('落足反解超出完整原源弧参数区间')
    return [max(0.,out[0]),min(1.,out[1])],error

def arc_pair_descriptor(pa,pb,settings,low,high):
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (low,high)) or not 0<=low<high<=TAU:
        raise ValueError('有限椭圆中线径向区间无效')
    sa,sb=support(pa),support(pb)
    if sa is None or sb is None:return None
    descriptor=pair_descriptor(sa,sb,settings)
    if descriptor is None:return None
    model=Midline(descriptor)
    # Recompute from the complete original arcs; caller-supplied interval
    # assertions never replace source membership checks.
    for p in (pa,pb):
        if not any(a-1e-13<=low and high<=b+1e-13 for a,b in radial_intervals(p,model)):
            raise ValueError('中线最近落足不能完整落在两侧实际有限源弧内')
    source_ranges=[]
    for p in (pa,pb):
        fr,err=fractions(p,model,low,high)
        source_ranges.append({'fraction_range':fr,'fraction_error_bound':err})
    descriptor['trimmed_arc_domain']={'source_arcs':[copy.deepcopy(pa),copy.deepcopy(pb)],
        'radial_interval':[low,high],'source_foot_ranges':source_ranges,
        'foot_angle_error_bound':foot_error(model),
        'endpoint_policy':'global_nearest_feet_inside_actual_arcs_only; endpoint_only_loci_unrecognized',
        'monotonicity_basis':'bisector_tangent_positive_projection_on_both_boundary_tangents_and_outer_reach'}
    return descriptor

def atomic_elliptical_pairs(items,selected,geometry,digest,entries,used):
    eligible=[p for p in items if principal(p['part']) is not None and len(selected.get(p['entry']['source_key'],[]))==1
              and selected[p['entry']['source_key']][0]['representation']=='outline'
              and selected[p['entry']['source_key']][0].get('equidistant_midline')
              and selected[p['entry']['source_key']][0]['equidistant_midline'].get('method')!='euclidean_nested_circles']
    groups=[]
    for item in eligible:
        rule=selected[item['entry']['source_key']][0];sp=support(item['part'])
        found=next((g for g in groups if g['rule']==rule and same_frame(g['support'],sp)),None)
        if found is None:found={'rule':rule,'support':sp,'items':[]};groups.append(found)
        found['items'].append(item)
    output=[]
    for group in groups:
        members=group['items'];rule=group['rule'];shapes=[]
        width=rule['dimensions'].get('diameter',rule['dimensions'].get('width'))
        if width is None:
            for p in members:p['entry']['reason']='截面尺寸或平面物理比例未知，有限椭圆轮廓不能由尺寸倒推比例'
            continue
        tolerance=rule.get('pair_tolerance')
        if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or not 0<=tolerance<width/4:
            raise ValueError('有限椭圆轮廓配对容差必须显式且小于截面四分之一')
        for p in members:
            sp=support(p['part']);index=next((j for j,q in enumerate(shapes) if same_shape(q,sp)),None)
            if index is None:index=len(shapes);shapes.append(sp)
            p['_ellipse_support_index']=index
        # More than two distinct nested supports can represent concentric
        # ducts, duplicate physical objects or a complex contour.  Do not hide
        # that whole-source ambiguity behind a locally closed pair.
        if len(shapes)!=2:
            for p in members:p['entry']['reason']='同轴椭圆支持轮廓不是唯一两组，完整复杂轮廓保留，不能局部择二掩盖'
            continue
        core=pair_descriptor(shapes[0],shapes[1],rule['equidistant_midline'])
        if core is None:
            for p in members:p['entry']['reason']='椭圆支持轮廓未满足同轴严格嵌套薄环证明；未用均值半轴补量'
            continue
        model=Midline(core);ranges={p['entry']['source_key']:radial_intervals(p['part'],model) for p in members}
        possible_ranges={p['entry']['source_key']:radial_intervals(p['part'],model,possible=True) for p in members}
        cuts=sorted({0.,TAU,*(v for table in (ranges,possible_ranges) for rs in table.values() for interval in rs for v in interval)})
        records=[]
        for low,high in zip(cuts,cuts[1:]):
            if high-low<=1e-12:continue
            middle=(low+high)/2
            active=[p for p in members if any(a<=middle<=b for a,b in possible_ranges[p['entry']['source_key']])]
            left=[p for p in active if p['_ellipse_support_index']==0];right=[p for p in active if p['_ellipse_support_index']==1]
            if len(left)!=1 or len(right)!=1:
                if left and right:
                    for p in active:p['entry'].update(reason='有限椭圆实际落足区间有重复源轮廓；仅互唯一区间入量',pairing_candidates=sorted(q['entry']['source_key'] for q in active if q is not p))
                continue
            a,b=left[0],right[0]
            if any(not any(x-1e-13<=low and high<=y+1e-13 for x,y in ranges[p['entry']['source_key']]) for p in (a,b)):continue
            # Keep original source arcs in the descriptor.  Counted child
            # geometry separately retains its own exact source fractions.
            desc=arc_pair_descriptor(a['part'],b['part'],rule['equidistant_midline'],low,high)
            if desc is not None:records.append((a,b,desc))
        if not records:continue
        per_source=defaultdict(list)
        for index,(a,b,desc) in enumerate(records):
            for side,p in enumerate((a,b)):
                fr=desc['trimmed_arc_domain']['source_foot_ranges'][side]
                per_source[p['entry']['source_key']].append((index,side,fr))
        children={}
        for p in members:
            key=p['entry']['source_key'];claims=per_source[key]
            if not claims:continue
            values=sorted({0.,1.,*(v for _,_,fr in claims for v in fr['fraction_range'])})
            parent=p['entry'];parent.update(disposition='excluded',reason='完整有限椭圆源part按最近落足区间及余段分区；父行仅作索引',partition_children=[]);used.add(key)
            for low,high in zip(values,values[1:]):
                if high<=low:continue
                part={**p['part'],'start_param':p['part']['start_param']+p['part']['sweep_param']*low,'sweep_param':p['part']['sweep_param']*(high-low)}
                sk=key+'@'+format(low,'.17g')+':'+format(high,'.17g')
                import qto_cad_geometry as cg
                child={**parent,'source_key':sk,'geometry':geometry(part),'geometry_sha256':digest(part),
                    'parent_source_part':key,'source_fraction_range':[low,high],
                    'raw_geometry_length':cg.summarize(geometry(part))['length'],
                    'disposition':'unrecognized','quantity':None,'reason':'有限椭圆源弧余段/端点安全边界未形成互唯一中线区间','partition_children':[]}
                entries.append(child);parent['partition_children'].append(sk)
                item={**p,'entry':child,'part':part}
                matches=[(index,side,fr) for index,side,fr in claims if fr['fraction_range']==[low,high]]
                if len(matches)>1:raise ValueError('有限椭圆源落足区间重复消费')
                if matches:
                    index,side,fr=matches[0];child['source_fraction_error_bound']=fr['fraction_error_bound'];children[index,side]=item
        for index,(a,b,desc) in enumerate(records):
            if (index,0) not in children or (index,1) not in children:raise ValueError('有限椭圆原源分区未与中线数量出口闭合')
            geo,_=materialize(desc);output.append(([children[index,0],children[index,1]],geo,rule))
    return output,{p['entry']['source_key'] for p in eligible}
