"""Source-bound route / wall or slab crossings, separated from installation quantities."""
from __future__ import annotations

from collections import Counter
import copy
import math
from pathlib import Path

from qto_penetration_sources import Sources, cg, fingerprint, number, point, reverse_part, text

SCHEMA = 'qto-penetration-request/1.0'
RESULT = 'qto-penetration-result/1.0'
EPS = 1e-9


def cross(a, b):
    return a[0]*b[1] - a[1]*b[0]


def sub(a, b):
    return [x-y for x, y in zip(a, b)]


def unique(values, tolerance=1e-10):
    out = []
    for value in sorted(values):
        if not out or abs(value-out[-1]) > tolerance:
            out.append(value)
    return out


def shape_signature(parts,closed=False):
    """An explicit physical identity may have reversed/cyclic drawing expressions."""
    forward=[p['part']for p in parts]
    reverse=[reverse_part(p)for p in reversed(forward)]
    sequences=[forward,reverse]
    if closed:
        sequences=[seq[i:]+seq[:i]for seq in sequences for i in range(len(seq))]or[[]]
    return min(fingerprint(seq)for seq in sequences)


def source_claim_intervals(parts):
    """Occupancy uses real source identity, never geometry or a whole-list hash."""
    claims={};missing=[]
    for item in parts:
        raw=item['source']
        if item['evidence_layer']=='scenario':
            continue  # A sourced endpoint pair is a hypothesis, not drawn occupancy.
        originals=raw.get('source_claims') or [raw]
        for claim in originals:
            path=claim.get('instance_path');index=claim.get('part_index');interval=claim.get('fraction_range')
            if (not claim.get('source_sha256') or not isinstance(path,str) or not path
                    or isinstance(index,bool) or not isinstance(index,int) or index<0
                    or not isinstance(interval,list) or len(interval)!=2):
                missing.append(claim);continue
            lo,hi=[number(v,'source.fraction_range')for v in interval]
            if not 0<=lo<hi<=1:
                missing.append(claim);continue
            value=dict(source_sha256=claim['source_sha256'],instance_path=path,
                       part_index=index,fraction_range=[lo,hi])
            claims[fingerprint(value)]=value
    return list(claims.values()),missing


def route_evidence_signature(parts):
    """Equal geometry cannot promote a derived or scenario alias to drawn."""
    forward=[dict(part=p['part'],evidence_layer=p['evidence_layer'],error_bound=p.get('error_bound',0.))for p in parts]
    reverse=[dict(p,part=reverse_part(p['part']))for p in reversed(forward)]
    return min(fingerprint(seq)for seq in (forward,reverse))


def check_source_claim_overlaps(routes,todo):
    occupied={};conflicts={}
    for route in routes:
        for claim in route['source_claims']:
            key=(claim['source_sha256'],claim['instance_path'],claim['part_index'])
            lo,hi=claim['fraction_range']
            for other,old in occupied.get(key,[]):
                if other is route:
                    continue
                a,b=old['fraction_range'];overlap=[max(lo,a),min(hi,b)]
                if overlap[0]>=overlap[1]:
                    continue  # Adjacent/disjoint intervals are legitimate partitions.
                evidence=[other['multiplicity'],route['multiplicity']]
                if (all(evidence) and len({(e['source_id'],e['text'],e['count'])for e in evidence})==1
                        and evidence[0]['index']!=evidence[1]['index']):
                    continue
                other['physical_identity_uncertain']=route['physical_identity_uncertain']=True
                ids=tuple(sorted((other['raw']['id'],route['raw']['id'])))
                detail=dict(source_sha256=key[0],instance_path=key[1],part_index=key[2],overlap_range=overlap)
                conflicts.setdefault(ids,{})[fingerprint(detail)]=detail
            occupied.setdefault(key,[]).append((route,claim))
    for ids,details in conflicts.items():
        todo.append(dict(problem='同一原part区间被多个物理管路重叠认领但缺有源根数与独立实例序号；未自动当作多管或重复删量',
                         route_ids=list(ids),source_overlaps=list(details.values())))


def conic_parameters(part, angle):
    a = part['start_param']; sweep = part['sweep_param']
    if sweep == 0:
        raise ValueError('零参数范围曲线不是有效管路')
    lo, hi = sorted((a, a+sweep))
    out = []
    for k in range(math.ceil((lo-angle)/(2*math.pi)-EPS), math.floor((hi-angle)/(2*math.pi)+EPS)+1):
        t = (angle + 2*math.pi*k-a)/sweep
        if -EPS <= t <= 1+EPS:
            out.append(max(0., min(1., t)))
    return out


def plane_roots(part, normal, offset):
    """Exact line or sinusoidal conic intersection with a plane, including tangency."""
    if part['kind'] == 'line':
        a = sum(x*y for x, y in zip(part['start'], normal))-offset
        b = sum(x*y for x, y in zip(part['end'], normal))-offset
        if abs(a-b) <= 1e-13 * max(1., abs(a), abs(b)):
            return [], abs(a) <= EPS
        t = a/(a-b)
        return ([max(0., min(1., t))] if -EPS <= t <= 1+EPS else []), False
    c = sum(x*y for x, y in zip(part['center'], normal))-offset
    a = sum(x*y for x, y in zip(part['u'], normal))
    b = sum(x*y for x, y in zip(part['v'], normal))
    radius = math.hypot(a, b)
    if radius <= 1e-14:
        return [], abs(c) <= EPS
    ratio = -c/radius
    if abs(ratio) > 1+1e-12:
        return [], False
    angle = math.atan2(b, a); delta = math.acos(max(-1., min(1., ratio)))
    return unique(conic_parameters(part, angle-delta)+conic_parameters(part, angle+delta)), False


def poly_value(coefficients, x):
    value = 0.
    for c in reversed(coefficients):
        value = value*x+c
    return value


def real_roots(coefficients):
    """Degree <=4, derivative-isolated real roots; repeated roots are retained."""
    scale = max(map(abs, coefficients), default=0.)
    if scale == 0:
        return None
    c = [x/scale for x in coefficients]
    while len(c)>1 and abs(c[-1]) < 1e-14:
        c.pop()
    if len(c) == 1:
        return []
    if len(c) == 2:
        return [-c[0]/c[1]]
    bound = 1+max(abs(x/c[-1]) for x in c[:-1])
    critical = real_roots([i*c[i] for i in range(1, len(c))]) or []
    cuts = [-bound, *[x for x in critical if -bound < x < bound], bound]
    roots = [x for x in critical if abs(poly_value(c, x)) <= 1e-11 * max(1., abs(x)**(len(c)-1))]
    for lo, hi in zip(cuts, cuts[1:]):
        f, g = poly_value(c, lo), poly_value(c, hi)
        if f*g >= 0:
            continue
        for _ in range(100):
            mid = (lo+hi)/2; h = poly_value(c, mid)
            if f*h <= 0:
                hi = mid
            else:
                lo = mid; f = h
        roots.append((lo+hi)/2)
    return unique(roots)


def ellipse_coordinates(part, p):
    u, v = part['u'], part['v']; d = sub(p, part['center'])
    determinant = cross(u, v)
    if abs(determinant) <= 1e-14 * max(1., math.hypot(*u[:2])*math.hypot(*v[:2])):
        raise ValueError('墙边界圆锥曲线在平面退化；保留未决')
    return cross(d, v)/determinant, cross(u, d)/determinant


def parameter_at(part, p):
    if part['kind'] == 'line':
        v = sub(part['end'], part['start']); q = sub(p, part['start'])
        den = v[0]**2+v[1]**2
        if den <= 1e-24:
            return 0. if math.hypot(q[0], q[1]) <= EPS else None
        t = (q[0]*v[0]+q[1]*v[1])/den
        if -EPS <= t <= 1+EPS and abs(cross(v, q)) <= EPS*max(1., math.sqrt(den)):
            return max(0., min(1., t))
        return None
    a, b = ellipse_coordinates(part, p)
    if abs(a*a+b*b-1) > 1e-8:
        return None
    ts = conic_parameters(part, math.atan2(b, a))
    return ts[0] if ts else None


def intersections(a, b):
    """XY line / affine-conic intersections, using full parameter geometry."""
    if b['kind'] == 'line':
        v = sub(b['end'], b['start']); n = [-v[1], v[0], 0.]
        if math.hypot(v[0], v[1]) <= EPS:
            return [], False
        ts, along = plane_roots(a, n, sum(x*y for x, y in zip(n, b['start'])))
        if along:
            # Endpoints of an overlap must split occupancy; a zero-width wall
            # does not become a solid solely because a route lies on its line.
            ts = [t for p in (point(b, 0), point(b, 1)) if (t := parameter_at(a, p)) is not None]
        return [t for t in ts if parameter_at(b, point(a, t)) is not None], along
    if a['kind'] == 'line':
        x0, y0 = ellipse_coordinates(b, a['start'])
        x1, y1 = ellipse_coordinates(b, a['end'])
        dx, dy = x1-x0, y1-y0
        roots = real_roots([x0*x0+y0*y0-1, 2*(x0*dx+y0*dy), dx*dx+dy*dy])
        along = roots is None
        return [t for t in roots or [] if -EPS<=t<=1+EPS and parameter_at(b, point(a, t)) is not None], along
    c0, c1 = ellipse_coordinates(b, a['center'])
    up = [a['center'][i]+a['u'][i] for i in range(3)]
    vp = [a['center'][i]+a['v'][i] for i in range(3)]
    ux, uy = ellipse_coordinates(b, up); vx, vy = ellipse_coordinates(b, vp)
    ux -= c0; uy -= c1; vx -= c0; vy -= c1
    aa=ux*ux+uy*uy; bb=vx*vx+vy*vy; cc=2*(ux*vx+uy*vy)
    dd=2*(c0*ux+c1*uy); ee=2*(c0*vx+c1*vy); ff=c0*c0+c1*c1-1
    # tan(theta/2) substitution gives a quartic; theta=pi is checked separately.
    polynomial=[aa+dd+ff, 2*cc+2*ee, -2*aa+4*bb+2*ff, -2*cc+2*ee, aa-dd+ff]
    roots=real_roots(polynomial)
    if roots is None:
        return [], True
    angles=[2*math.atan(r) for r in roots]
    if abs(aa-dd+ff) <= 1e-10:
        angles.append(math.pi)
    ts=unique([t for angle in angles for t in conic_parameters(a, angle)])
    return [t for t in ts if parameter_at(b, point(a, t)) is not None], False


def in_ring(p, parts):
    if any(parameter_at(part, p) is not None for part in parts):
        return 0
    crossings=0
    for part in parts:
        ts, _=plane_roots(part, [0.,1.,0.], p[1])
        for t in ts:
            q=point(part,t)
            if q[0] <= p[0]:
                continue
            if part['kind']=='line':
                dy=part['end'][1]-part['start'][1]
            else:
                angle=part['start_param']+part['sweep_param']*t
                dy=(-part['u'][1]*math.sin(angle)+part['v'][1]*math.cos(angle))*part['sweep_param']
            if (t<=1e-11 and dy<0)or(t>=1-1e-11 and dy>0):
                continue
            if abs(dy)>1e-10:
                crossings+=1
    return 1 if crossings%2 else -1


def validate_context(source, raw, label):
    for key in ('id','physical_id','space_id','floor_id'):
        text(raw.get(key), label+'.'+key)
    source.refs(raw.get('basis_source_ids'), label+'.basis_source_ids')
    text(raw.get('basis'),label+'.basis')


def build_parts(source, bindings, todo, ident):
    if not isinstance(bindings,list) or not bindings:
        raise ValueError(ident+'须绑定完整源曲线')
    parts=[]
    for raw in bindings:
        values, problem=source.binding(raw)
        if problem:
            todo.append(dict(object_id=ident, **problem))
        parts.extend(values)
    return parts


def continuous(parts, closed=False):
    if not parts:
        return False
    pairs=list(zip(parts,parts[1:]))
    if closed:
        pairs.append((parts[-1],parts[0]))
    return all(math.dist(point(a['part'],1),point(b['part'],0)) <= EPS for a,b in pairs)


def z_bounds(source, raw):
    if raw is None:
        return None
    datum=text(raw.get('datum_id'),'z_bounds.datum_id')
    source.refs(raw.get('datum_basis_source_ids'),'z_bounds.datum_basis_source_ids')
    low=source.scalar(raw.get('lower'),'z_bounds.lower');high=source.scalar(raw.get('upper'),'z_bounds.upper')
    if low>=high:
        raise ValueError('有源下界须小于上界')
    return dict(lower=low,upper=high,datum_id=datum,evidence=raw)


def ring_area(parts):
    total=0.
    for item in parts:
        p=item['part']
        if p['kind']=='line':
            total+=cross(p['start'],p['end'])
        else:
            a=p['start_param'];b=a+p['sweep_param'];c,u,v=p['center'],p['u'],p['v']
            total+=cross(c,u)*(math.cos(b)-math.cos(a))+cross(c,v)*(math.sin(b)-math.sin(a))+cross(u,v)*(b-a)
    return total/2


def barrier_parts(source, raw, todo):
    parts=build_parts(source,raw.get('boundary'),todo,raw['id'])
    points=[p for item in parts for p in cg.part_extrema(item['part'])]
    if points and max(p[2]for p in points)-min(p[2]for p in points)>EPS:
        todo.append(dict(object_id=raw['id'],problem='通用倾斜/非水平墙或楼板轮廓尚未实现，不能仅取XY强认棱柱',source_geometry=parts))
        parts=[]
    form=raw.get('form')
    if form not in ('closed_footprint','paired_faces','single_boundary'):
        raise ValueError('墙/楼板form须为closed_footprint、paired_faces或single_boundary')
    if parts and form=='closed_footprint' and not continuous(parts,True):
        raise ValueError('完整墙/楼板轮廓未闭合；不能暗补边界')
    if parts and form=='closed_footprint'and abs(ring_area(parts))<=1e-18:
        todo.append(dict(object_id=raw['id'],problem='闭合源边界没有可判定的非零平面面积，不能认作有效墙实体',source_geometry=parts))
        parts=[]
    if form=='paired_faces' and (len(parts)!=2 or any(v['part']['kind']!='line' for v in parts)):
        raise ValueError('paired_faces仅支持两条有源直墙面；复杂形态用闭合源轮廓')
    if form=='paired_faces':
        a,b=[p['part']for p in parts]
        va=sub(a['end'],a['start']);vb=sub(b['end'],b['start'])
        if abs(cross(va,vb))>EPS*max(1.,math.hypot(*va[:2])*math.hypot(*vb[:2])) or abs(cross(va,sub(b['start'],a['start'])))<=EPS:
            raise ValueError('墙两面须平行且有非零间距')
    zs=z_bounds(source,raw.get('z_bounds'))
    if source.factor is None and zs:
        todo.append(dict(object_id=raw['id'],problem='墙平面源单位未知，不能把米制高度拼成三维墙体'))
    attributes={}
    for key in ('wall_type','fire_rating'):
        value=raw.get(key)
        if value is not None:
            sid=value.get('source_id');source.refs([sid],key)
            original=source.rows[sid].get('text')
            if original!=value.get('expected_text') or value.get('value') not in original:
                raise ValueError(key+'必须逐项绑定可回读原文')
            attributes[key]=value
    return dict(raw=raw,parts=parts,form=form,z=zs,attributes=attributes)


def path_point(parts,t):
    i=min(len(parts)-1,max(0,int(math.floor(t))))
    return point(parts[i]['part'],t-i)


def footprint_state(p,barrier):
    parts=[p['part']for p in barrier['parts']]
    if barrier['form']=='closed_footprint':
        return in_ring(p,parts)
    if barrier['form']=='paired_faces':
        a,b=parts;v=sub(a['end'],a['start'])
        sa=cross(v,sub(p,a['start']));sb=cross(v,sub(p,b['start']))
        if abs(sa)<=EPS or abs(sb)<=EPS:
            return 0
        return 1 if sa*sb<0 else -1
    return -1


def crossings_for(route,barrier,spatial):
    parts=route['parts'];boundaries=[p['part']for p in barrier['parts']]
    cuts=list(map(float,range(len(parts)+1)));boundary_cuts=[];face_cuts=[];along=False
    for i,part in enumerate(parts):
        for face_index,edge in enumerate(boundaries):
            roots,on=intersections(part['part'],edge);along|=on
            boundary_cuts.extend(i+t for t in roots);cuts.extend(i+t for t in roots)
            face_cuts.extend((i+t,face_index)for t in roots)
        if spatial:
            for height in (barrier['z']['lower'],barrier['z']['upper']):
                roots,on=plane_roots(part['part'],[0.,0.,1.],height)
                cuts.extend(i+t for t in roots)
    cuts=unique(cuts);boundary_cuts=unique(boundary_cuts)
    if barrier['form']=='single_boundary':
        events=[]
        for t in boundary_cuts:
            kind='boundary_intersection_candidate'
            if 0<t<len(parts):
                before=path_point(parts,max(0,t-1e-6));after=path_point(parts,min(len(parts),t+1e-6))
                for edge in boundaries:
                    if parameter_at(edge,path_point(parts,t))is not None and edge['kind']=='line':
                        v=sub(edge['end'],edge['start'])
                        if cross(v,sub(before,edge['start']))*cross(v,sub(after,edge['start']))>0:
                            kind='touch'
            events.append(dict(kind=kind,parameter=t,point=path_point(parts,t),entry=None,exit=None))
        if along:
            events.append(dict(kind='along_boundary',parameter=None,point=None,entry=None,exit=None))
        return events,along

    def state(t):
        p=path_point(parts,t);s=footprint_state(p,barrier)
        if spatial and s>=0:
            lo,hi=barrier['z']['lower'],barrier['z']['upper']
            if p[2]<lo-EPS or p[2]>hi+EPS:
                return -1
            if abs(p[2]-lo)<=EPS or abs(p[2]-hi)<=EPS:
                return 0
        return s

    states=[state((a+b)/2)for a,b in zip(cuts,cuts[1:])]
    events=[];i=0
    while i<len(states):
        if states[i]!=1:
            i+=1;continue
        j=i
        while j+1<len(states)and states[j+1]==1:
            j+=1
        lo,hi=cuts[i],cuts[j+1]
        complete=i>0 and j+1<len(states)and states[i-1]==-1 and states[j+1]==-1
        if barrier['form']=='paired_faces':
            # Infinite strip occupancy is only accepted when both finite source
            # faces were actually hit. No fabricated wall end caps.
            entering={face for t,face in face_cuts if abs(lo-t)<=1e-9}
            leaving={face for t,face in face_cuts if abs(hi-t)<=1e-9}
            complete &= any(a!=b for a in entering for b in leaving)
        events.append(dict(kind='crossing'if complete else'partial_in_wall',parameter=(lo+hi)/2,
                           entry=path_point(parts,lo),exit=path_point(parts,hi),point=path_point(parts,(lo+hi)/2),
                           source_parameter_range=[lo,hi]))
        i=j+1
    if (len(events)>1 and states and states[0]==states[-1]==1
            and math.dist(point(parts[0]['part'],0),point(parts[-1]['part'],1))<=EPS):
        first,last=events[0],events[-1]
        if first['kind']==last['kind']=='partial_in_wall':
            lo=last['source_parameter_range'][0];hi=first['source_parameter_range'][1]+len(parts)
            mid=((lo+hi)/2)%len(parts)
            merged=dict(kind='crossing',parameter=mid,entry=last['entry'],exit=first['exit'],point=path_point(parts,mid),
                        source_parameter_range=[lo,hi],wraps_closed_route=True)
            events=[merged,*events[1:-1]]
    for i,t in enumerate(cuts[1:-1],1):
        if states[i-1]==states[i]==-1 and state(t)==0:
            events.append(dict(kind='touch',parameter=t,point=path_point(parts,t),entry=None,exit=None))
    if 0 in states:
        events.append(dict(kind='along_boundary',parameter=None,point=None,entry=None,exit=None))
    return events,along


def calculate(manifest_path,request,base=None,request_path=None):
    if request.get('schema')!=SCHEMA:
        raise ValueError('公共穿越request.schema不符')
    source=Sources(manifest_path,request)
    if request_path:
        source.protect(request_path)
    base=Path(base or Path(manifest_path).parent)
    # Scope completeness is about the selected objects. Unrelated source errors
    # are still retained in provenance and cannot erase an otherwise valid branch.
    todo=[];routes=[];seen={};aliases=[]
    for raw in request.get('routes',[]):
        validate_context(source,raw,'route')
        if raw.get('discipline')not in ('pipe','duct','tray'):
            raise ValueError('公共穿越仅接水管、风管和桥架物理路径')
        if raw.get('z_mode')not in (None,'plan_only','source_wcs'):
            raise ValueError('z_mode须为plan_only或source_wcs')
        if raw.get('z_mode')=='source_wcs':
            source.refs(raw.get('z_basis_source_ids'),'route.z_basis_source_ids')
        if raw.get('import'):
            if raw.get('bindings'):
                raise ValueError('同一路径不能同时声明import和bindings')
            parts=source.import_route(raw['import'],base,raw)
        elif raw.get('bindings'):
            parts=build_parts(source,raw.get('bindings'),todo,raw['id'])
        else:
            parts=[]
        if parts and raw.get('scenario_gaps'):
            raise ValueError('原图段与方案补连须各自成行；不得把方案暗混入原图连续路径')
        if len(raw.get('scenario_gaps',[]))>1:
            raise ValueError('每行方案补连只表示一个有源端口对；多个补连分行保留')
        for gap in raw.get('scenario_gaps',[]):
            text(gap.get('hypothesis_id'),'gap.hypothesis_id');source.refs(gap.get('basis_source_ids'),'gap.basis')
            endpoints=[];refs=[]
            for name in ('from','to'):
                values,problem=source.binding(gap[name])
                if problem or len(values)!=1 or gap[name].get('endpoint')not in ('start','end'):
                    raise ValueError('方案补连必须绑定两个真实源part端点')
                endpoints.append(point(values[0]['part'],0 if gap[name]['endpoint']=='start'else 1));refs.append(values[0]['source'])
            parts.append(dict(part=dict(kind='line',start=endpoints[0],end=endpoints[1]),evidence_layer='scenario',error_bound=0.,
                              source=dict(hypothesis_id=gap['hypothesis_id'],endpoints=refs,basis_source_ids=gap['basis_source_ids'])))
        if parts and not continuous(parts):
            raise ValueError('物理管路parts未接续；不得暗补连线')
        if not parts:
            todo.append(dict(route_id=raw['id'],problem='路径没有可重建几何，穿越处数未知'))
        signature=shape_signature(parts)
        evidence_signature=route_evidence_signature(parts)
        multiplicity=source.multiplicity(raw.get('multiplicity_evidence'))
        claims,missing=source_claim_intervals(parts)
        if missing:
            todo.append(dict(route_id=raw['id'],problem='管路缺完整原源实例、part或区间身份，物理认领关系未决',source_claims=missing))
        context=(raw['discipline'],raw.get('system'),raw['space_id'],raw['floor_id'],
                 raw.get('z_mode')or'plan_only',raw.get('datum_id'),
                 tuple(sorted(set(raw.get('z_basis_source_ids')or[]))),source.unit,multiplicity)
        physical=raw['physical_id']
        if physical in seen:
            old=seen[physical]
            if signature!=old['signature'] or context!=old['context'] or evidence_signature!=old['evidence_signature']:
                raise ValueError('同一物理管路多个表达的几何、空间/楼层、基准、依据层级或多根实例不一致；须先明确同物与区间关系')
            old['source_claims'].extend(claims)
            old['physical_identity_uncertain']|=bool(missing)
            old['aliases'].append(raw['id']);aliases.append(dict(physical_id=physical,alias=raw['id'],primary=old['raw']['id']))
            continue
        route=dict(raw=raw,parts=parts,signature=signature,evidence_signature=evidence_signature,context=context,
                   aliases=[],multiplicity=multiplicity,source_claims=claims,physical_identity_uncertain=bool(missing))
        seen[physical]=route;routes.append(route)
    check_source_claim_overlaps(routes,todo)
    barriers=[];wall_seen={}
    for raw in request.get('barriers',[]):
        validate_context(source,raw,'barrier')
        if raw.get('kind')not in ('wall','slab'):
            raise ValueError('barrier.kind须为wall或slab')
        barrier=barrier_parts(source,raw,todo);signature=shape_signature(barrier['parts'],raw.get('form')=='closed_footprint')
        pid=raw['physical_id']
        if pid in wall_seen:
            old=wall_seen[pid]
            if signature!=old['signature']or barrier['z']!=old['z']or raw['floor_id']!=old['raw']['floor_id']or raw['space_id']!=old['raw']['space_id']:
                raise ValueError('同一物理墙/楼板的重复表示不一致，不能按点距自动归并')
            aliases.append(dict(physical_id=pid,alias=raw['id'],primary=old['raw']['id']));continue
        barrier['signature']=signature;wall_seen[pid]=barrier;barriers.append(barrier)
    if not barriers:
        todo.append(dict(problem='未提供有源墙/楼板范围；穿越总处数未知，不能写零'))
    events=[];noncrossings=[];pairs=[];uncertain=[]
    opening_rows=copy.deepcopy(request.get('openings',[]));openings=[];opening_seen={}
    for opening in opening_rows:
        validate_context(source,opening,'opening')
        text(opening.get('barrier_physical_id'),'opening.barrier_physical_id')
        opening['rebuilt_parts']=build_parts(source,opening.get('boundary'),todo,opening['id'])
        if not continuous(opening['rebuilt_parts'],True):
            raise ValueError('既有洞口须绑定完整闭合源边界')
        opening['rebuilt_z']=z_bounds(source,opening.get('z_bounds'))
        height=opening['rebuilt_z']
        height_key=(height['lower'],height['upper'],height['datum_id'])if height else None
        signature=(shape_signature(opening['rebuilt_parts'],True),opening['barrier_physical_id'],opening['floor_id'],opening['space_id'],height_key)
        if opening['physical_id']in opening_seen:
            old=opening_seen[opening['physical_id']]
            if old['signature']==signature:
                aliases.append(dict(physical_id=opening['physical_id'],alias=opening['id'],primary=old['opening']['id'],kind='opening'))
                continue
            old['opening']['identity_conflict']=True;opening['identity_conflict']=True
            todo.append(dict(problem='同物洞口的有源边界或高度矛盾，未强合为可用共用洞口',physical_id=opening['physical_id'],
                             source_openings=[old['opening'],opening]))
        else:
            opening_seen[opening['physical_id']]=dict(signature=signature,opening=opening)
        openings.append(opening)
    for route in routes:
        raw=route['raw']
        if not any(w['raw']['space_id']==raw['space_id']and w['raw']['floor_id']==raw['floor_id']for w in barriers):
            todo.append(dict(route_id=raw['id'],problem='没有本管路适用楼层/空间的墙或楼板资料；其他楼层排除不证明本范围零穿越'))
        for barrier in barriers:
            wall=barrier['raw'];pair=dict(route_id=raw['id'],barrier_id=wall['id'])
            if raw['space_id']!=wall['space_id']or raw['floor_id']!=wall['floor_id']:
                pairs.append(dict(**pair,status='different_space_or_floor'));continue
            if not route['parts']or not barrier['parts']:
                pairs.append(dict(**pair,status='source_geometry_unresolved'));continue
            margin=max((p.get('error_bound',0.)for p in route['parts']),default=0.)
            if margin>0:
                for item in route['parts']:
                    bounds=cg.part_extrema(item['part'])
                    lo=[min(p[i]for p in bounds)for i in(0,1)]
                    hi=[max(p[i]for p in bounds)for i in(0,1)]
                    close=False
                    for edge in barrier['parts']:
                        eb=cg.part_extrema(edge['part'])
                        if all(min(p[i]for p in eb)<=hi[i]+margin and max(p[i]for p in eb)>=lo[i]-margin for i in(0,1)):
                            close=True;break
                    if close:
                        uncertain.append(dict(**pair,classification='derived_boundary_uncertainty',position_error_bound=margin,
                                              coordinate_unit=source.unit,possible_crossing_count=None,
                                              source_geometry=route['parts'],barrier_source_geometry=barrier['parts']))
                        todo.append(dict(**pair,problem='墙边界进入衍生中线位置误差带，显示线未交也不能认无穿越'))
                        break
            spatial=bool(barrier['form']!='single_boundary'and source.factor is not None and raw.get('z_mode')=='source_wcs'and barrier['z']and raw.get('datum_id')==barrier['z']['datum_id'])
            if raw.get('z_mode')not in (None,'plan_only','source_wcs'):
                raise ValueError('z_mode须为plan_only或source_wcs')
            if raw.get('z_mode')=='source_wcs':
                source.refs(raw.get('z_basis_source_ids'),'route.z_basis_source_ids')
            try:
                found,along=crossings_for(route,barrier,spatial)
            except ValueError as exc:
                todo.append(dict(**pair,problem='通用几何计算待核，未输出伪零',error=str(exc),
                                 source_geometry=route['parts'],barrier_source_geometry=barrier['parts']))
                pairs.append(dict(**pair,status='source_geometry_unresolved'));continue
            if spatial and not found:
                plan,_=crossings_for(route,barrier,False)
                if any(p['kind']=='crossing'for p in plan):
                    noncrossings.append(dict(**pair,kind='different_elevation',plan_events=plan))
            if not spatial:
                todo.append(dict(**pair,problem='仅平面适用；三维源Z/墙高度/共同高程基准/单位尚未闭合'))
            pair_events=[]
            for event in found:
                part_indices=range(max(0,int(event.get('source_parameter_range',[event.get('parameter')or 0])[0])),
                                   min(len(route['parts']),int(math.ceil(event.get('source_parameter_range',[0,event.get('parameter')or 0])[-1]))+1))
                levels={route['parts'][i]['evidence_layer']for i in part_indices}
                kind=event['kind']
                if kind=='crossing':
                    kind='spatial_crossing'if spatial else'plan_crossing_candidate'
                    if 'scenario'in levels:
                        kind='scenario_crossing_candidate'
                    elif 'derived'in levels:
                        kind='derived_crossing_candidate'
                    if route['physical_identity_uncertain']:
                        kind='physical_identity_candidate'
                if kind in ('touch','along_boundary','partial_in_wall'):
                    if kind=='partial_in_wall':
                        todo.append(dict(**pair,problem='管路端点止于墙内或墙边界资料不完整，不能确认完整穿越'))
                    noncrossings.append({**pair,**event,'kind':kind,'spatial_applicable':spatial});continue
                record=dict(id='PEN-'+str(len(events)+1).zfill(6),**pair,route_physical_id=raw['physical_id'],
                            barrier_physical_id=wall['physical_id'],discipline=raw['discipline'],system=raw.get('system'),
                            floor_id=raw['floor_id'],space_id=raw['space_id'],coordinate_unit=source.unit,
                            **event,classification=kind,spatial_applicable=spatial,source_geometry=route['parts'],
                            barrier_source_geometry=barrier['parts'],attributes=barrier['attributes'],
                            opening_refs=[],new_opening_required=None,new_opening_candidate=True,
                            sleeve_quantity=None,firestop_quantity=None,firestop_volume=None,
                            installation_todo=['需项目构造及计量依据确定套管、封堵数量；尺寸/规则缺失，不由穿越处数推出体积'])
                for opening in openings:
                    if opening['barrier_physical_id']!=wall['physical_id']or opening['floor_id']!=raw['floor_id']or opening['space_id']!=raw['space_id']:
                        continue
                    ring=[p['part']for p in opening['rebuilt_parts']]
                    if record['point']and in_ring(record['point'],ring)>=0:
                        opening_z=opening['rebuilt_z']
                        in_height=bool(spatial and opening_z and opening_z['datum_id']==raw.get('datum_id')
                                       and all(opening_z['lower']<=p[2]<=opening_z['upper']for p in(record['entry'],record['exit'])if p))
                        record['opening_refs'].append(dict(id=opening['id'],physical_id=opening['physical_id'],
                                                          basis_source_ids=opening['basis_source_ids'],source_geometry=opening['rebuilt_parts'],
                                                          relation_status=('opening_identity_conflict'if opening.get('identity_conflict')else
                                                                           'centerline_within_sourced_opening_height'if in_height else'plan_opening_candidate'),
                                                          cross_section_fit_status='not_evaluated'))
                if record['opening_refs']:
                    # A centreline relation does not establish full cross-section
                    # clearance. Existing openings remain referenced; new drilling
                    # and sharing/fit still need their own sourced construction basis.
                    record.update(new_opening_required=None,new_opening_candidate=None)
                    record['installation_todo'].append('已有洞口保留引用；高度及管路截面适配未闭合时，是否需新开洞仍为空值')
                events.append(record);pair_events.append(record['id'])
            pairs.append(dict(**pair,status='evaluated_spatial'if spatial else'evaluated_plan_only',event_ids=pair_events,along_line_detected=along))
    # Near-boundary uncertainty of a derived display polyline must never silently
    # disappear as a zero. All derived routes remain candidate-level regardless.
    for route in routes:
        if any(p.get('error_bound',0)>0 for p in route['parts']):
            todo.append(dict(route_id=route['raw']['id'],problem='衍生中线带位置误差；显示折线近墙、切触及潜在穿越需按隐式曲线复核',
                             classification='derived_position_uncertainty',parts=route['parts']))
    counts=Counter(e['classification']for e in events)
    unknown=(bool(todo)or not routes or not barriers
             or any(p['status']not in ('evaluated_spatial','different_space_or_floor')for p in pairs)
             or any(e['classification']!='spatial_crossing'for e in events))
    groups={}
    for event in events:
        for opening in event['opening_refs']:
            if opening['relation_status']!='centerline_within_sourced_opening_height':
                continue
            key=opening['physical_id'];groups.setdefault(key,set()).add(event['id'])
    source.unchanged()
    return dict(schema=RESULT,status='REVIEW_REQUIRED'if unknown else'CALCULATED_WORKING_SCOPE',
                business_adoption_allowed=False,official_adoption=False,scope=request.get('scope'),coordinate_unit=source.unit,
                source_sha256=source.source_sha256,events=events,noncrossings=noncrossings,uncertain_candidates=uncertain,pair_coverage=pairs,
                duplicate_representations=aliases,shared_openings=[dict(opening_physical_id=k,event_ids=sorted(v),basis='同一有源洞口物理身份')for k,v in groups.items()if len(v)>1],
                quantities=dict(spatial_crossing_count=None if unknown else counts['spatial_crossing'],
                                known_spatial_crossing_subtotal=counts['spatial_crossing'],plan_crossing_candidate_count=counts['plan_crossing_candidate'],
                                boundary_intersection_candidate_count=counts['boundary_intersection_candidate'],
                                derived_crossing_candidate_count=counts['derived_crossing_candidate'],scenario_crossing_candidate_count=counts['scenario_crossing_candidate'],
                                physical_identity_candidate_count=counts['physical_identity_candidate'],
                                complete_spatial_count=None if unknown else counts['spatial_crossing'],new_opening_count=None,
                                sleeve_quantity=None,firestop_quantity=None,firestop_volume=None),
                todo=todo,provenance=dict(fingerprints=source.protected,producer_runs=source.producer_runs,
                                         fixed_source_reconstruction='extract_cad_neutral.convert + qto_cad_geometry',
                                         read_diagnostics=source.read_diagnostics,source_reconstruction_issues=source.todo,stable=True)),source
