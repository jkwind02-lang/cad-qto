"""Atomic source-parameter partitions for concentric circular outlines."""
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
    out=[];done=set()
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
    return out,{p['entry']['source_key'] for p in eligible}
