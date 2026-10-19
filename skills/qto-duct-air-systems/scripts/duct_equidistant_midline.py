"""Euclidean midsets of thin, coaxial nested ellipses, with measured error.

The equal-distance locus is generally NOT an ellipse.  Its source descriptor
is retained; line parts are a bounded display tessellation, not the length
measurement.  Integration uses the implicit distance-gradient derivative.
"""
from __future__ import annotations
import copy
from functools import lru_cache
import json
import math

SCHEMA='qto-duct-euclidean-midline/1.0'
TAU=math.tau

def dot(a,b):return sum(x*y for x,y in zip(a,b))
def norm(v):return math.sqrt(dot(v,v))
def add(a,b):return [x+y for x,y in zip(a,b)]
def scale(a,n):return [x*n for x in a]
def sub(a,b):return [x-y for x,y in zip(a,b)]

def positive(v,label):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError(label+'须为有限正数')
    return float(v)

def principal(part):
    if part.get('kind')!='ellipse_arc':return None
    for key in ('center','u','v'):
        vector=part.get(key)
        if not isinstance(vector,list) or len(vector)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vector):
            raise ValueError('椭圆源参数须为完整有限非布尔WCS向量')
    for key in ('start_param','sweep_param'):
        if isinstance(part.get(key),bool) or not isinstance(part.get(key),(int,float)) or not math.isfinite(part[key]):raise ValueError('椭圆源扫角参数无效')
    if not 0<abs(part['sweep_param'])<=math.tau+1e-12:raise ValueError('椭圆源扫角须在一周以内且非零')
    u,v=part['u'],part['v'];uu,vv,uv=dot(u,u),dot(v,v),dot(u,v)
    disc=math.hypot(uu-vv,2*uv)
    a2,b2=(uu+vv+disc)/2,(uu+vv-disc)/2
    if not math.isfinite(a2) or not math.isfinite(b2):raise ValueError('椭圆参数超出有限数值运算范围')
    if b2<=0:return None
    a,b=math.sqrt(a2),math.sqrt(b2)
    if a-b<=1e-10*a:return None
    phi=.5*math.atan2(2*uv,uu-vv);cs,sn=math.cos(phi),math.sin(phi)
    e1=scale(add(scale(u,cs),scale(v,sn)),1/a)
    e2=scale(add(scale(u,-sn),scale(v,cs)),1/b)
    return {'a':a,'b':b,'e1':e1,'e2':e2,'phi':phi,'center':part['center']}

def quarter_domain(part,axes,reference):
    s1=dot(axes['e1'],reference['e1']);s2=dot(axes['e2'],reference['e2'])
    if abs(abs(s1)-1)>1e-10 or abs(abs(s2)-1)>1e-10:return None
    q=part['start_param']-axes['phi']
    start=math.atan2(s2*math.sin(q),s1*math.cos(q));sweep=part['sweep_param']*(1 if s1*s2>0 else -1)
    if abs(abs(sweep)-math.tau)<=1e-10:return {'start':0.,'sweep':math.copysign(math.tau,sweep),'quarters':{0,1,2,3}}
    start_q=round(start/(math.pi/2));sweep_q=round(sweep/(math.pi/2))
    if abs(start-start_q*math.pi/2)>1e-10 or abs(sweep-sweep_q*math.pi/2)>1e-10 or not 1<=abs(sweep_q)<=4:return None
    covered={((start_q+i) if sweep_q>0 else (start_q-i-1))%4 for i in range(abs(sweep_q))}
    return {'start':start_q*math.pi/2,'sweep':sweep_q*math.pi/2,'quarters':covered}

def pair_descriptor(pa,pb,settings):
    """Return only when nesting, principal axes, sector and reach are proved.

    For max support gap D < outer minimum curvature radius, all points in
    the annulus have a unique closest outer point.  The inner projection is
    unique by convexity.  Both distance normals have positive radial component,
    so each radial ray has precisely one zero of d_inner-d_outer.
    """
    aa,bb=principal(pa),principal(pb)
    if aa is None or bb is None:return None
    size=max(aa['a'],bb['a'])
    # Co-centred/coaxial is an exact structural case, allowing roundoff only.
    if math.dist(aa['center'],bb['center'])>size*1e-11:return None
    domain_a=quarter_domain(pa,aa,aa);domain_b=quarter_domain(pb,bb,aa)
    if domain_a is None or domain_b is None or domain_a['quarters']!=domain_b['quarters']:return None
    if aa['a']<bb['a'] and aa['b']<bb['b']:inner,outer=aa,bb
    elif bb['a']<aa['a'] and bb['b']<aa['b']:inner,outer=bb,aa
    else:return None
    gap=max(outer['a']-inner['a'],outer['b']-inner['b'])
    reach=outer['b']**2/outer['a']
    if not gap<reach:return None
    if settings.get('method')!='euclidean_nested_ellipse' or not settings.get('basis'):
        raise ValueError('椭圆中线须显式采用欧氏等距口径并给出工程依据')
    tolerances={k:positive(settings.get(k),k) for k in ('distance_tolerance','chord_tolerance','length_tolerance')}
    axis_error=size*max(norm(sub(aa[k],scale(bb[k],1 if dot(aa[k],bb[k])>=0 else -1))) for k in ('e1','e2'))
    alignment_error=math.dist(aa['center'],bb['center'])+axis_error
    if alignment_error>tolerances['distance_tolerance']/64:return None
    lower_gap=min(outer['a']**2-inner['a']**2,outer['b']**2-inner['b']**2)/(2*outer['a'])
    roundoff=64*math.ulp(max(size,*map(abs,aa['center']),1.))
    if tolerances['distance_tolerance']<8*roundoff or tolerances['distance_tolerance']>lower_gap/10000:
        raise ValueError('最近距离容差须覆盖浮点误差且足够小于可证最小轮廓间隔')
    if tolerances['chord_tolerance']>reach/100 or tolerances['length_tolerance']>size/10000:
        raise ValueError('椭圆中线显示/计长容差过大')
    return {'schema_version':SCHEMA,'source_boundaries':[copy.deepcopy(pa),copy.deepcopy(pb)],
            'settings':copy.deepcopy(settings)}


def nested_circle_descriptor(pa,pb,settings):
    """Full, strictly nested coplanar circles have a focal-ellipse midset.

    In the annulus, d_inner=|P-Ci|-ri and d_outer=ro-|P-Co|.
    Equality is therefore |P-Ci|+|P-Co|=ri+ro, not an averaged circle.
    Strict nesting puts the ENTIRE focal ellipse between the two boundaries
    and away from both centres, so both closest boundary feet are unique.
    Source-part identity is checked separately by the atomic source matcher.
    """
    if settings.get('method')!='euclidean_nested_circles':return None
    # Reuse finite/non-boolean source validation without classifying a circle
    # as an ellipse; principal() deliberately returns None for exact circles.
    principal(pa);principal(pb)
    from duct_curved_outline import circle, cross
    ca,cb=circle(pa),circle(pb)
    if ca is None or cb is None:return None
    if any(abs(abs(p['sweep_param'])-TAU)>64*math.ulp(TAU) for p in (pa,pb)):return None
    if ca['radius']==cb['radius']:return None
    i,o=(ca,cb) if ca['radius']<cb['radius'] else (cb,ca)
    size=max(o['radius'],*map(abs,i['center']),*map(abs,o['center']),1.)
    roundoff=256*math.ulp(size)
    def frame(c):
        x=c['x'];raw=c['y'];projection=sub(raw,scale(x,dot(x,raw)))
        y=scale(projection,1/norm(projection));n=cross(x,y);n=scale(n,1/norm(n))
        # Parameter-basis roundoff is included, never absorbed into section.
        return n,c['radius']*norm(sub(raw,y))+128*math.ulp(c['radius'])
    ni,ei=frame(i);no,eo=frame(o)
    if dot(ni,no)<0:no=scale(no,-1)
    delta=sub(o['center'],i['center']);offset=dot(delta,ni)
    normal_difference=norm(sub(ni,no))
    if abs(offset)>roundoff or normal_difference>256*math.ulp(1.):return None
    projected=sub(delta,scale(ni,offset));d=norm(projected)
    boundary_error=ei+eo+abs(offset)+o['radius']*normal_difference+roundoff
    gap=o['radius']-i['radius']-d
    if gap<=16*boundary_error:return None
    if not isinstance(settings.get('basis'),str) or not settings['basis'].strip():raise ValueError('完整嵌套圆中线须有显式欧氏口径依据')
    if not isinstance(settings.get('source_locator'),str) or not settings['source_locator'].strip():raise ValueError('完整嵌套圆中线须有原源定位')
    tolerances={k:positive(settings.get(k),k) for k in ('distance_tolerance','chord_tolerance','length_tolerance')}
    a=math.fsum((i['radius'],o['radius']))/2;c=d/2
    b=math.sqrt((a-c)*(a+c));condition=a/b
    position_error=16*boundary_error*condition**2
    length_geometry_error=TAU*(roundoff+position_error)
    if tolerances['distance_tolerance']<position_error or tolerances['distance_tolerance']>gap/1000:
        raise ValueError('完整嵌套圆距离容差须覆盖源/WCS舍入误差且小于可证最小间隔千分之一')
    if tolerances['chord_tolerance']<=position_error or tolerances['chord_tolerance']>a/100:
        raise ValueError('完整嵌套圆显示容差不足或过大')
    if tolerances['length_tolerance']<=4*length_geometry_error or tolerances['length_tolerance']>a/10000:
        raise ValueError('完整嵌套圆计长容差须覆盖几何舍入界且足够小')
    e1=scale(projected,1/d) if d else i['x']
    e2=cross(ni,e1);e2=scale(e2,1/norm(e2))
    model={'inner_center':i['center'],'outer_center':o['center'],'inner_radius':i['radius'],'outer_radius':o['radius'],
           'projected_outer_center':add(i['center'],projected),'center':add(i['center'],scale(projected,.5)),
           'e1':e1,'e2':e2,'a':a,'b':b,'focal_distance':d,'minimum_outline_gap':gap,
           'source_boundary_roundoff_bound':boundary_error,'position_error_bound':position_error,
           'length_geometry_roundoff_bound':length_geometry_error}
    return {'schema_version':SCHEMA,'construction':'full_nested_circle_focal_ellipse',
            'source_boundaries':[copy.deepcopy(pa),copy.deepcopy(pb)],'settings':copy.deepcopy(settings),'analytic_model':model}


def focal_ellipse_length(a,b,tolerance):
    """Directed Decimal enclosure of the COMPLETE elliptic-integral series.

    C=2*pi*a*(1-sum(t_n)), t_1=m/4,
    t_(n+1)/t_n=m*(2n-1)*(2n+1)/(4*(n+1)^2) < m < 1.
    The uncomputed positive tail is <= next_term/(1-m).  Thus this is an
    enclosure, not an adaptive quadrature estimate or sampled chord sum.
    DLMF 19.5.2: https://dlmf.nist.gov/19.5.E2
    """
    from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
    with localcontext() as ctx:
        ctx.prec=64
        da,db=Decimal.from_float(a),Decimal.from_float(b)
        pi0=Decimal('3.141592653589793238462643383279502884197169399375105820974944')
        pi1=pi0+Decimal('1e-60')
        ctx.rounding=ROUND_FLOOR;rlo=db/da;lo_ratio=rlo*rlo
        ctx.rounding=ROUND_CEILING;rhi=db/da;hi_ratio=rhi*rhi
        ctx.rounding=ROUND_FLOOR;mlo=max(Decimal(0),1-hi_ratio)
        ctx.rounding=ROUND_CEILING;mhi=max(Decimal(0),1-lo_ratio)
        if mhi>=1:raise ValueError('焦点椭圆计长未能证明级数收敛')
        ctx.rounding=ROUND_FLOOR;tlo=mlo/4
        ctx.rounding=ROUND_CEILING;thi=mhi/4
        slo,shi=Decimal(0),Decimal(0);target=Decimal.from_float(tolerance)
        for n in range(1,200001):
            ctx.rounding=ROUND_FLOOR;slo+=tlo
            ctx.rounding=ROUND_CEILING;shi+=thi
            numerator=(2*n-1)*(2*n+1);denominator=4*(n+1)**2
            ctx.rounding=ROUND_FLOOR;tlo=tlo*mlo*numerator/denominator
            ctx.rounding=ROUND_CEILING;thi=thi*mhi*numerator/denominator;tail=thi/(1-mhi)
            # Evaluate each factor with outward rounding.  A subtraction
            # reverses the direction, so compute its bound separately.
            ctx.rounding=ROUND_FLOOR;lower_factor=1-shi-tail;lower=2*pi0*da*lower_factor
            ctx.rounding=ROUND_CEILING;upper_factor=1-slo;upper=2*pi1*da*upper_factor
            if upper-lower<=target:
                center=float((upper+lower)/2);error=float((upper-lower)/2)+2*math.ulp(center)
                return center,error,{'lower_bound':math.nextafter(float(lower),-math.inf),
                    'upper_bound':math.nextafter(float(upper),math.inf),'series_terms':n,
                    'uncomputed_series_tail_bound':math.nextafter(float(tail),math.inf),'decimal_precision':64}
        raise ValueError('焦点椭圆计长级数未满足指定尾项界；未截断，也未退回弦长')


class CircleMidline:
    """Analytic ellipse evaluation with bounded display chords and true length."""
    def __init__(self,descriptor):
        if not isinstance(descriptor,dict) or descriptor.get('schema_version')!=SCHEMA:raise ValueError('完整嵌套圆中线来源描述无效')
        expected=nested_circle_descriptor(*descriptor['source_boundaries'],descriptor['settings'])
        if expected!=descriptor:raise ValueError('完整嵌套圆中线与冻结源参数/唯一性证明不一致')
        self.descriptor=descriptor;self.model=descriptor['analytic_model'];self.settings=descriptor['settings']

    def point(self,t):
        m=self.model;cs,sn=math.cos(t),math.sin(t)
        point=[m['center'][k]+m['a']*cs*m['e1'][k]+m['b']*sn*m['e2'][k] for k in range(3)]
        tangent=[-m['a']*sn*m['e1'][k]+m['b']*cs*m['e2'][k] for k in range(3)]
        return {'point':point,'tangent':tangent}

    def materialize(self):
        import qto_cad_geometry as cg
        m=self.model;settings=self.settings;pe=m['position_error_bound'];a,b=m['a'],m['b']
        count=2**math.ceil(math.log2(max(4.,TAU*math.sqrt(a/(8*(settings['chord_tolerance']-pe))))))
        if count>65536:raise ValueError('焦点椭圆显示容差要求超过65536区间；未截断')
        points=[self.point(TAU*j/count)['point'] for j in range(count)]
        points.append(copy.deepcopy(points[0]))
        geo={'schema':cg.GEOMETRY_SCHEMA,'coordinate_system':'WCS',
             'parts':[{'kind':'line','start':p,'end':q} for p,q in zip(points,points[1:])],'closed':True}
        length,error,enclosure=focal_ellipse_length(a,b,settings['length_tolerance']/4)
        error+=m['length_geometry_roundoff_bound']
        if error>settings['length_tolerance']:raise ValueError('焦点椭圆计长误差界超过明确容差')
        chord_bound=a*(TAU/count)**2/8+pe
        evidence={'uniqueness_case':'strictly_nested_complete_coplanar_circles_focal_ellipse',
            'midset_equation':'distance_to_inner_center + distance_to_outer_center = inner_radius + outer_radius',
            'analytic_model':copy.deepcopy(m),'minimum_outline_gap_lower_bound':m['minimum_outline_gap']-2*m['source_boundary_roundoff_bound'],
            'source_alignment_roundoff_bound':m['source_boundary_roundoff_bound'],'position_error_bound':pe,
            'display_chord_error_bound':chord_bound,'maximum_equidistance_residual_bound':2*pe+2*m['source_boundary_roundoff_bound'],
            'maximum_closest_point_error_bound':m['source_boundary_roundoff_bound'],
            'length_geometry_roundoff_bound':m['length_geometry_roundoff_bound'],'length_enclosure':enclosure,
            'error_method':'directed_decimal_complete_elliptic_series_positive_tail_plus_geometry_roundoff',
            'length_is_polyline_chord_sum':False,'sample_interval_count':count,
            'constant_cross_section_proved':False,'source_outline_gap_range':[m['minimum_outline_gap'],m['outer_radius']-m['inner_radius']+m['focal_distance']]}
        summary=cg.summarize(geo);summary.update(length=length,length_error_estimate=error,
            polyline_chord_length=sum(math.dist(p,q) for p,q in zip(points,points[1:])),
            measurement_method='euclidean_nested_circle_focal_ellipse_complete_integral',
            endpoint_tangents=[self.point(0.)['tangent'],self.point(TAU)['tangent']],midline_evidence=evidence)
        # Analytic ellipse extrema, rather than a tessellated extent, protect
        # crop/coverage and downstream geometry bounds in any WCS plane.
        axes=[math.hypot(a*m['e1'][k],b*m['e2'][k])+pe for k in range(3)]
        summary['bbox3d']=[m['center'][k]-axes[k] for k in range(3)]+[m['center'][k]+axes[k] for k in range(3)]
        box=summary['bbox3d'];summary['bbox']=[box[0],box[1],box[3],box[4]]
        return geo,summary

def closest_ellipse(x,y,a,b,tol):
    """Global closest point, via the monotone Lagrange-multiplier root.

    Positive-quadrant reflection is valid by axis symmetry.  The inside major-
    axis medial set is explicitly rejected.  The bracket's point-box diagonal
    bounds closest-point error, rather than a number-of-iterations assertion.
    """
    sx=-1 if x<0 else 1;sy=-1 if y<0 else 1;x,y=abs(x),abs(y)
    equation=(x/a)**2+(y/b)**2
    if equation==1:return [sx*x,sy*y],0.
    if y==0:
        threshold=(a*a-b*b)/a
        if equation<1 and x<=threshold:raise ValueError('外轮廓最近点位于椭圆内侧中轴，最近点不唯一')
        return [sx*a,0.],0.
    if x==0:return [0.,sy*b],0.
    def g(lam):return (a*x/(a*a+lam))**2+(b*y/(b*b+lam))**2-1
    if equation>1:
        lo,hi=0.,max(a*x,b*y,a*a,b*b)
        while g(hi)>0:hi*=2
    else:
        lo,hi=math.nextafter(-b*b,0.),0.
        if g(lo)<=0:raise ValueError('最近点根未被有效括住，不能猜测椭圆半径')
    def q(lam):return [a*a*x/(a*a+lam),b*b*y/(b*b+lam)]
    for _ in range(180):
        qlo,qhi=q(lo),q(hi);error=math.dist(qlo,qhi)
        if error<=tol:
            qm=q((lo+hi)/2);return [sx*qm[0],sy*qm[1]],error
        mid=(lo+hi)/2
        if mid in (lo,hi):raise ValueError('最近点求解到达浮点分辨率但未满足误差要求')
        if g(mid)>0:lo=mid
        else:hi=mid
    raise ValueError('椭圆最近点求解未收敛')

class Midline:
    def __init__(self,descriptor):
        if not isinstance(descriptor,dict) or descriptor.get('schema_version')!=SCHEMA:raise ValueError('等距中线来源描述无效')
        arc_domain=descriptor.get('trimmed_arc_domain')
        core=copy.deepcopy(descriptor)
        if arc_domain is not None:
            from duct_elliptical_outline import arc_pair_descriptor
            expected=arc_pair_descriptor(*arc_domain['source_arcs'],descriptor['settings'],*arc_domain['radial_interval'])
            if expected!=descriptor:raise ValueError('有限椭圆源弧区间与最近落足证明不一致')
            core.pop('trimmed_arc_domain')
        pa,pb=core['source_boundaries'];settings=core['settings']
        if pair_descriptor(pa,pb,settings)!=core:raise ValueError('等距中线未满足唯一性与误差前置条件')
        aa,bb=principal(pa),principal(pb);domain=quarter_domain(pa,aa,aa)
        self.inner,self.outer=(aa,bb) if aa['a']<bb['a'] else (bb,aa)
        self.frame=aa;self.start,self.sweep=domain['start'],domain['sweep'];self.settings=settings
        self.arc_domain=arc_domain
        if arc_domain is not None:self.start,self.sweep=arc_domain['radial_interval'][0],arc_domain['radial_interval'][1]-arc_domain['radial_interval'][0]
        i,o=self.inner,self.outer
        self.gap=max(o['a']-i['a'],o['b']-i['b']);self.reach=o['b']**2/o['a']
        self.minimum_gap=min(o['a']**2-i['a']**2,o['b']**2-i['b']**2)/(2*o['a'])
        self.alignment_error=math.dist(aa['center'],bb['center'])+o['a']*max(norm(sub(aa[k],scale(bb[k],1 if dot(aa[k],bb[k])>=0 else -1))) for k in ('e1','e2'))
        self.radial_gradient_lower=(i['b']+o['b']-self.gap)/o['a']
        self.hessian_bound=i['a']/i['b']**2+(o['a']/o['b']**2)/(1-self.gap/self.reach)
        self.curvature_bound=self.hessian_bound/self.radial_gradient_lower
        self.speed_bound=o['a']*math.sqrt(1+(2/self.radial_gradient_lower)**2)
        self.cache={};self.max_residual=0.;self.max_closest_error=0.
        dt=settings['distance_tolerance'];qerr=dt/32+self.alignment_error;m=self.radial_gradient_lower
        self.position_error_bound=dt+2*self.alignment_error/m
        ge=self.hessian_bound*self.position_error_bound+8*qerr/self.minimum_gap
        if ge>=m/2:raise ValueError('等距中线梯度误差不能维持唯一根界')
        pe=self.position_error_bound
        self.speed_error_bound=pe+2*pe/m+(o['a']+pe)*(2*ge/m+4*ge/(m*m))
        self.length_root_error_bound=abs(self.sweep)*self.speed_error_bound
        if self.length_root_error_bound>=settings['length_tolerance']/4:
            raise ValueError('最近距离/位置误差不足以满足指定计长精度；须收紧distance_tolerance')

    def point(self,theta):
        if theta in self.cache:return self.cache[theta]
        cs,sn=math.cos(theta),math.sin(theta)
        # Principal-axis boundary points use exact zero coordinates.  This also
        # avoids a tiny sin(pi) disguising the known major-axis uniqueness case.
        if abs(cs)<1e-14:cs=0.
        if abs(sn)<1e-14:sn=0.
        i,o=self.inner,self.outer;dt=self.settings['distance_tolerance'];qtol=dt/32
        lo=1/math.hypot(cs/i['a'],sn/i['b']);hi=1/math.hypot(cs/o['a'],sn/o['b'])
        def evaluate(r):
            p=[r*cs,r*sn]
            qi,ei=closest_ellipse(*p,i['a'],i['b'],qtol);qo,eo=closest_ellipse(*p,o['a'],o['b'],qtol)
            di,do=math.dist(p,qi),math.dist(p,qo)
            return p,qi,qo,di,do,ei,eo
        for _ in range(180):
            r=(lo+hi)/2;p,qi,qo,di,do,ei,eo=evaluate(r);f=di-do;err=ei+eo
            if hi-lo<=dt:break
            if f>err:hi=r
            elif f<-err:lo=r
            else:
                allowance=(abs(f)+err)/self.radial_gradient_lower
                lo,hi=max(lo,r-allowance),min(hi,r+allowance)
        else:raise ValueError('等距中线径向根未满足误差要求')
        grad=[(p[k]-qi[k])/di+(qo[k]-p[k])/do for k in (0,1)]
        radial=grad[0]*cs+grad[1]*sn;angular=-grad[0]*sn+grad[1]*cs
        if radial<self.radial_gradient_lower/2:raise ValueError('等距中线径向梯度不符合可证下界')
        dr=-r*angular/radial;derivative=[dr*cs-r*sn,dr*sn+r*cs]
        e1,e2=self.frame['e1'],self.frame['e2'];center=self.frame['center']
        world=[center[k]+e1[k]*p[0]+e2[k]*p[1] for k in range(3)]
        tangent=[e1[k]*derivative[0]+e2[k]*derivative[1] for k in range(3)]
        result={'point':world,'tangent':tangent,'speed':math.hypot(r,dr),'radius':r,'inner_foot':qi,'outer_foot':qo,
                'inner_distance':di,'outer_distance':do,'equidistance_residual_bound':abs(f)+err+2*self.alignment_error,
                'position_error_bound':(hi-lo)/2+2*self.alignment_error/self.radial_gradient_lower,
                'closest_point_error_bound':max(ei,eo)+self.alignment_error}
        self.max_residual=max(self.max_residual,result['equidistance_residual_bound']);self.max_closest_error=max(self.max_closest_error,ei,eo)
        self.cache[theta]=result;return result

    def integral(self,tolerance):
        """Adaptive quadrature with a second stricter run checked by the caller."""
        start,end=sorted((self.start,self.start+self.sweep));error=0.
        def f(x):return self.point(x)['speed']
        def rec(a,b,fa,fm,fb,value,tol,depth):
            nonlocal error
            m=(a+b)/2;fl,fr=f((a+m)/2),f((m+b)/2)
            left,right=(m-a)*(fa+4*fl+fm)/6,(b-m)*(fm+4*fr+fb)/6
            delta=left+right-value
            if abs(delta)<=15*tol:
                error+=abs(delta)/15;return left+right+delta/15
            if depth<=0:raise ValueError('等距中线积分未满足误差估计；不能退回折线弦长')
            return rec(a,m,fa,fl,fm,left,tol/2,depth-1)+rec(m,b,fm,fr,fb,right,tol/2,depth-1)
        count=max(1,math.ceil((end-start)/(math.pi/4)));total=0.
        for j in range(count):
            a,b=start+(end-start)*j/count,start+(end-start)*(j+1)/count
            fa,fm,fb=f(a),f((a+b)/2),f(b)
            total+=rec(a,b,fa,fm,fb,(b-a)*(fa+4*fm+fb)/6,tolerance/count,24)
        return total,error

    def materialize(self):
        import qto_cad_geometry as cg
        span=abs(self.sweep);ctol=self.settings['chord_tolerance']
        requested=max(1.,span*self.speed_bound*math.sqrt(self.curvature_bound/(8*ctol)))
        count=2**math.ceil(math.log2(requested))
        if count>65536:raise ValueError('指定显示误差需要超过65536个中线区间；请显式调整容差或分范围，不截断')
        angles=[self.start+self.sweep*j/count for j in range(count+1)]
        points=[self.point(t)['point'] for t in angles]
        parts=[{'kind':'line','start':a,'end':b} for a,b in zip(points,points[1:])]
        tolerance=self.settings['length_tolerance'];first,e1=self.integral(tolerance/8);length,e2=self.integral(tolerance/16)
        refinement=abs(first-length);estimate=max(e1,e2,refinement)+self.length_root_error_bound
        if refinement>tolerance/4 or estimate>tolerance:raise ValueError('等距中线实际积分/加严复算未满足长度误差要求')
        chord_bound=self.curvature_bound*(self.speed_bound*span/count)**2/8+self.settings['distance_tolerance']+2*self.alignment_error/self.radial_gradient_lower
        geometry={'schema':cg.GEOMETRY_SCHEMA,'coordinate_system':'WCS','parts':parts,'closed':abs(span-math.tau)<1e-12}
        summary=cg.summarize(geometry);summary.update(length=length,length_error_estimate=estimate,
            polyline_chord_length=sum(math.dist(a,b) for a,b in zip(points,points[1:])),
            measurement_method='euclidean_equidistant_implicit_curve_integral',
            endpoint_tangents=[scale(self.point(t)['tangent'],1 if self.sweep>0 else -1) for t in (self.start,self.start+self.sweep)],
            midline_evidence={'uniqueness_case':('coaxial_nested_ellipse_trimmed_arc_monotone_feet_with_outer_reach_bound' if self.arc_domain else 'coaxial_nested_ellipse_principal_axis_sectors_with_outer_reach_bound'),
                'maximum_support_gap_bound':self.gap,'outer_reach_lower_bound':self.reach,'radial_gradient_lower_bound':self.radial_gradient_lower,
                'display_chord_error_bound':chord_bound,'position_error_bound':self.position_error_bound,
                'source_alignment_roundoff_bound':self.alignment_error,
                'maximum_equidistance_residual_bound':self.max_residual,'maximum_closest_point_error_bound':self.max_closest_error,
                'length_root_error_bound':self.length_root_error_bound,'adaptive_integral_error_estimate':max(e1,e2),
                'stricter_integral_difference':refinement,'error_method':'adaptive_quadrature_refinement_plus_bounded_distance_root_error',
                'length_is_polyline_chord_sum':False,'sample_interval_count':count})
        if self.arc_domain is not None:summary['midline_evidence']['trimmed_arc_domain']=copy.deepcopy(self.arc_domain)
        axis_error=[chord_bound*math.hypot(self.frame['e1'][k],self.frame['e2'][k]) for k in range(3)]
        summary['bbox3d']=[v-axis_error[k] for k,v in enumerate(summary['bbox3d'][:3])]+[v+axis_error[k] for k,v in enumerate(summary['bbox3d'][3:])]
        b=summary['bbox3d'];summary['bbox']=[b[0],b[1],b[3],b[4]]
        return geometry,summary

@lru_cache(maxsize=64)
def _materialized(key):
    descriptor=json.loads(key)
    model=CircleMidline(descriptor) if descriptor.get('construction')=='full_nested_circle_focal_ellipse' else Midline(descriptor)
    geometry,summary=model.materialize()
    geometry['derived_midline']=descriptor
    return geometry,summary

def materialize(descriptor):
    return copy.deepcopy(_materialized(json.dumps(descriptor,sort_keys=True,separators=(',',':'),allow_nan=False)))

def validate_geometry(geometry):
    expected,summary=materialize(geometry['derived_midline'])
    if geometry!=expected:raise ValueError('等距中线显示几何与冻结源描述重算不一致')
    return summary
