"""Atomic circular outlines; full nested circles require source-pair identity."""
from __future__ import annotations
import math
from collections import defaultdict

def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def circle(part):
    if part.get('kind')!='ellipse_arc':return None
    u,v=part['u'],part['v'];ru,rv=math.sqrt(dot(u,u)),math.sqrt(dot(v,v))
    if min(ru,rv)<=0 or abs(ru-rv)>64*math.ulp(max(ru,rv)) or abs(dot(u,v))>64*math.ulp(ru*rv):return None
    x,y=[n/ru for n in u],[n/rv for n in v]
    return {'radius':(ru+rv)/2,'x':x,'y':y,'normal':cross(x,y),'center':part['center']}

def overlaps(a,b,width,tol):
    ca,cb=circle(a['part']),circle(b['part'])
    if ca is None or cb is None:return []
    scale=max(ca['radius'],cb['radius'],*map(abs,ca['center']),*map(abs,cb['center']),1.)
    if math.dist(ca['center'],cb['center'])>64*math.ulp(scale) or abs(abs(dot(ca['normal'],cb['normal']))-1)>64*math.ulp(1.):return []
    if abs(abs(ca['radius']-cb['radius'])-width)>tol:return []
    pa,pb=a['part'],b['part'];t=pb['start_param']
    v=[cb['x'][i]*math.cos(t)+cb['y'][i]*math.sin(t) for i in range(3)]
    a0,sa=pa['start_param'],pa['sweep_param'];b0=math.atan2(dot(v,ca['y']),dot(v,ca['x']))
    sb=pb['sweep_param']*(1 if dot(ca['normal'],cb['normal'])>0 else -1)
    amin,amax=sorted((a0,a0+sa));result=[]
    # The same physical interval can straddle the angular seam.  Every source
    # interval is <= one turn, so this finite shift range covers both seams.
    k0=math.floor((amin-b0)/math.tau)-2;k1=math.ceil((amax-b0)/math.tau)+2
    for k in range(k0,k1+1):
        shifted=b0+k*math.tau;bmin,bmax=sorted((shifted,shifted+sb))
        lo,hi=max(amin,bmin),min(amax,bmax)
        if hi-lo<=1e-12:continue
        f=[((theta-a0)/sa,(theta-shifted)/sb) for theta in (lo,hi)]
        f.sort(key=lambda v:v[0]);result.append((f[0][0],f[1][0],f[0][1],f[1][1]))
    return result

def atomic_circular_pairs(items,selected,geometry,digest,entries,used):
    eligible=[p for p in items if circle(p['part']) and len(selected.get(p['entry']['source_key'],[]))==1
              and selected[p['entry']['source_key']][0]['representation']=='outline']
    explicit=[p for p in eligible if selected[p['entry']['source_key']][0].get('equidistant_midline',{}).get('method')=='euclidean_nested_circles']
    # A third outline remains a geometric competitor even when its rule name,
    # description, solver choice, or rule-match count differs.  Source scope
    # and actual system/plane decide applicability, not dictionary equality.
    competitors=[p for p in items if circle(p['part']) and any(
        r.get('representation')=='outline' for r in selected.get(p['entry']['source_key'],[]))]
    focal_pairs,focal_handled=complete_nested_circle_pairs(explicit,selected,used,competitors)
    eligible=[p for p in eligible if p['entry']['source_key'] not in focal_handled]
    cuts={p['entry']['source_key']:{0.,1.} for p in eligible};pairs=[]
    for i,a in enumerate(eligible):
        rule=selected[a['entry']['source_key']][0];width=rule['dimensions'].get('diameter',rule['dimensions'].get('width'))
        if width is None:continue
        tol=rule.get('pair_tolerance')
        if isinstance(tol,bool) or not isinstance(tol,(int,float)) or not math.isfinite(tol) or not 0<=tol<width/4:
            raise ValueError('圆弧轮廓配对容差必须显式且小于截面四分之一')
        for b in eligible[i+1:]:
            if selected[b['entry']['source_key']]!=[rule]:continue
            for fa0,fa1,fb0,fb1 in overlaps(a,b,width,tol):
                fr=(fa0,fa1,fb0,fb1);pairs.append((a,b,fr,rule))
                cuts[a['entry']['source_key']].update((fa0,fa1));cuts[b['entry']['source_key']].update((fb0,fb1))
    for _ in range(len(eligible)+2):
        changed=False
        for a,b,(a0,a1,b0,b1),_ in pairs:
            for p,q,p0,p1,q0,q1 in ((a,b,a0,a1,b0,b1),(b,a,b0,b1,a0,a1)):
                pk,qk=p['entry']['source_key'],q['entry']['source_key']
                for t in list(cuts[pk]):
                    if not min(p0,p1)-1e-12<=t<=max(p0,p1)+1e-12:continue
                    u=q0+(t-p0)*(q1-q0)/(p1-p0);u=max(0.,min(1.,u))
                    if not any(abs(u-v)<1e-12 for v in cuts[qk]):cuts[qk].add(u);changed=True
        if not changed:break
    else:raise ValueError('圆弧轮廓分区未收敛，未截断计量')
    atoms=defaultdict(list)
    for p in eligible:
        parent=p['entry'];key=parent['source_key'];values=sorted(max(0.,min(1.,v)) for v in cuts[key])
        if len(values)==2:
            atoms[key].append({**p,'_range':[0.,1.]});continue
        parent.update(disposition='excluded',reason='完整圆弧源part按全部实际角域交集断点分区；父行仅为索引',partition_children=[]);used.add(key)
        for lo,hi in zip(values,values[1:]):
            if hi-lo<=1e-12:continue
            part={**p['part'],'start_param':p['part']['start_param']+p['part']['sweep_param']*lo,
                  'sweep_param':p['part']['sweep_param']*(hi-lo)}
            sk=key+'@'+format(lo,'.12g')+':'+format(hi,'.12g')
            child={**parent,'source_key':sk,'geometry':geometry(part),'geometry_sha256':digest(part),
                   'parent_source_part':key,'source_fraction_range':[lo,hi],'raw_geometry_length':circle(part)['radius']*abs(part['sweep_param']),
                   'disposition':'unrecognized','reason':'圆弧实际子区间未形成互唯一配对','partition_children':[]}
            entries.append(child);parent['partition_children'].append(sk);atoms[key].append({**p,'entry':child,'part':part,'_range':[lo,hi]})
    neighbors=defaultdict(list);bykey={p['entry']['source_key']:p for group in atoms.values() for p in group}
    for a,b,(a0,a1,b0,b1),rule in pairs:
        ak,bk=a['entry']['source_key'],b['entry']['source_key']
        for aa in atoms[ak]:
            lo,hi=aa['_range']
            if lo<a0-1e-10 or hi>a1+1e-10:continue
            target=sorted((b0+(lo-a0)*(b1-b0)/(a1-a0),b0+(hi-a0)*(b1-b0)/(a1-a0)))
            hits=[bb for bb in atoms[bk] if max(abs(bb['_range'][i]-target[i]) for i in (0,1))<1e-10]
            if len(hits)!=1:raise ValueError('圆弧原子分区未在对侧完整闭合')
            bb=hits[0];x,y=aa['entry']['source_key'],bb['entry']['source_key']
            if y not in neighbors[x]:neighbors[x].append(y)
            if x not in neighbors[y]:neighbors[y].append(x)
    out=list(focal_pairs);done=set()
    for key,a in bykey.items():
        if key in done:continue
        options=neighbors[key]
        if len(options)!=1 or len(neighbors[options[0]])!=1:
            if options:a['entry'].update(reason='实际圆弧子区间多重配对，候选保留且不影响其他唯一区间',pairing_candidates=options)
            continue
        other=options[0];b=bykey[other];done.update((key,other));ca,cb=circle(a['part']),circle(b['part'])
        radius=(ca['radius']+cb['radius'])/2
        mid={**a['part'],'center':[(x+y)/2 for x,y in zip(ca['center'],cb['center'])],
             'u':[v*radius/ca['radius'] for v in a['part']['u']],'v':[v*radius/ca['radius'] for v in a['part']['v']]}
        parent=a['entry'].get('parent_source_part',key);out.append(([a,b],mid,selected[parent][0]))
    return out,{p['entry']['source_key'] for p in eligible}|focal_handled


def circle_support_competes(a,b):
    """Only geometric support competition; no solver settings or rule labels.

    Finite arcs retain their actual coverage and are never completed for
    measurement. Their supporting circles may nevertheless be alternative
    boundaries of a full ring, so they constrain uniqueness conservatively.
    """
    ca,cb=circle(a['part']),circle(b['part'])
    if ca is None or cb is None or ca['radius']==cb['radius']:return False
    na,nb=ca['normal'],cb['normal']
    na=[v/math.sqrt(dot(na,na)) for v in na];nb=[v/math.sqrt(dot(nb,nb)) for v in nb]
    if dot(na,nb)<0:nb=[-v for v in nb]
    if math.dist(na,nb)>256*math.ulp(1.):return False
    delta=[y-x for x,y in zip(ca['center'],cb['center'])];offset=dot(delta,na)
    size=max(ca['radius'],cb['radius'],*map(abs,ca['center']),*map(abs,cb['center']),1.)
    if abs(offset)>256*math.ulp(size):return False
    distance=math.sqrt(sum((v-offset*n)**2 for v,n in zip(delta,na)))
    return distance+min(ca['radius'],cb['radius'])<max(ca['radius'],cb['radius'])


def circle_scope_competes(a,b,selected):
    """Selectors partition evidence; source space and system partition scope."""
    if any(a['entry'].get(k)!=b['entry'].get(k) for k in ('source_sha256','layout','space_id','floor_id')):return False
    systems=lambda p:{r.get('system') for r in selected.get(p['entry']['source_key'],[]) if r.get('representation')=='outline'}
    return bool(systems(a)&systems(b))


def complete_nested_circle_pairs(items,selected,used,competitors=None):
    """Match geometric candidates BEFORE accepting any declared pair identity.

    A declared pair cannot override a third compatible boundary.  Conversely,
    geometry alone cannot invent a physical identity.  Both source parts are
    consumed whole (fraction [0,1]), without replacing their original part or
    parameter start/sweep.  Partial circles remain unrecognized in this branch.
    """
    if not items:return [],set()
    from duct_equidistant_midline import nested_circle_descriptor,materialize
    handled={p['entry']['source_key'] for p in items};out=[];groups=[]
    # Build this graph ONCE over all applicable source outline parts.  Rule
    # grouping below is retained only for the existing identity/consumer
    # interface; it must never hide geometric alternatives from that graph.
    competitors=items if competitors is None else competitors
    by_source={p['entry']['source_key']:p for p in competitors};neighbors=defaultdict(list)
    for index,a in enumerate(competitors):
        ak=a['entry']['source_key']
        if ak in used:continue
        for b in competitors[index+1:]:
            bk=b['entry']['source_key']
            if bk in used or not circle_scope_competes(a,b,selected) or not circle_support_competes(a,b):continue
            neighbors[ak].append(bk);neighbors[bk].append(ak)
    reached=set(handled);pending=list(handled)
    while pending:
        for key in neighbors[pending.pop()]:
            if key not in reached:reached.add(key);pending.append(key)
    blocked={key for key in reached if len(neighbors[key])>1 or any(len(neighbors[k])>1 for k in neighbors[key])}
    for key in blocked:
        by_source[key]['entry'].update(disposition='unrecognized',quantity=None,
            reason='适用源范围内存在跨规则或多重几何轮廓配对；规则名称/描述及身份声明不能强选',pairing_candidates=neighbors[key])
    # Prevent a competing old-method outline from bypassing this unresolved
    # component in the subsequent concentric-circle path.
    handled.update(blocked)
    for item in items:
        rule=selected[item['entry']['source_key']][0]
        group=next((g for g in groups if g[0]==rule),None)
        if group is None:group=[rule,[]];groups.append(group)
        group[1].append(item)
    for rule,group in groups:
        if not rule['dimensions']:
            for p in group:p['entry'].update(reason='物理单位或有源截面未核，完整原part与null数量保留')
            continue
        settings=rule['equidistant_midline'];declared=settings.get('source_pairings')
        identities={};physical=set();known={p['entry']['source_key'] for p in group}
        if declared is None:declared=[]
        if not isinstance(declared,list):raise ValueError('完整嵌套圆source_pairings须为逐物理实例配对数组')
        for claim in declared:
            if not isinstance(claim,dict):raise ValueError('完整嵌套圆配对身份记录无效')
            keys=claim.get('source_part_keys');pid=claim.get('physical_id')
            if (not isinstance(keys,list) or len(keys)!=2
                or any(not isinstance(k,str) or not k or k not in known for k in keys) or len(set(keys))!=2
                or not isinstance(pid,str) or not pid.strip() or pid in physical
                or any(not isinstance(claim.get(k),str) or not claim[k].strip() for k in ('basis','source_locator'))):
                raise ValueError('完整嵌套圆配对须有唯一物理身份、两条真实源part及依据/定位')
            if any(k in identities for k in keys):raise ValueError('完整嵌套圆源part被多个物理配对重复占用')
            physical.add(pid)
            for key in keys:identities[key]=claim
        descriptors={}
        for index,a in enumerate(group):
            ak=a['entry']['source_key']
            a['entry'].update(reason='完整嵌套圆未满足唯一几何配对与有源物理身份；保留完整原part',quantity=None)
            if ak in used:continue
            for b in group[index+1:]:
                bk=b['entry']['source_key']
                if bk in used:continue
                descriptor=nested_circle_descriptor(a['part'],b['part'],settings)
                if descriptor is not None:descriptors[frozenset((ak,bk))]=descriptor
        done=set();bykey={p['entry']['source_key']:p for p in group}
        for a in group:
            ak=a['entry']['source_key'];options=neighbors[ak]
            if ak in done or ak in used:continue
            if len(options)!=1 or len(neighbors[options[0]])!=1:
                if options:a['entry'].update(reason='完整嵌套圆存在多重几何轮廓配对；源身份声明不能强选',pairing_candidates=options)
                continue
            bk=options[0];claim=identities.get(ak)
            if claim is None or identities.get(bk)!=claim or set(claim['source_part_keys'])!={ak,bk}:
                a['entry'].update(reason='几何互唯一但缺对应源part的唯一物理配对身份，未计量',pairing_candidates=options)
                continue
            descriptor=descriptors.get(frozenset((ak,bk)))
            if descriptor is None:
                a['entry'].update(reason='有限圆弧不能按完整支撑圆计量；端点骨架仍为通用余项')
                continue
            b=bykey[bk];done.update((ak,bk))
            mid,_=materialize(descriptor)
            for p in (a,b):p['entry'].update(source_fraction_range=[0.,1.],source_fraction_error_bound=0.,pairing_identity=claim)
            out.append(([a,b],mid,rule))
    return out,handled
