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
    descriptor=json.loads(key);model=Midline(descriptor);geometry,summary=model.materialize()
    geometry['derived_midline']=descriptor
    return geometry,summary

def materialize(descriptor):
    return copy.deepcopy(_materialized(json.dumps(descriptor,sort_keys=True,separators=(',',':'),allow_nan=False)))

def validate_geometry(geometry):
    expected,summary=materialize(geometry['derived_midline'])
    if geometry!=expected:raise ValueError('等距中线显示几何与冻结源描述重算不一致')
    return summary
