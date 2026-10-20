"""Evidence-driven path preparation for the pipe engine.

The parent is a measured engineering route, not a graph edge. Curves remain
parametric; only evidenced connections and original geometric bends split it.
Drawing spaces never share nodes implicitly. Cross-view links have independent
height evidence and cannot turn page-coordinate distances into pipe quantities.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict, deque

from qto_cad_geometry import GEOMETRY_SCHEMA, conic_point, measure_part, summarize
from qto_elevation_facts import evaluate_elevation_link
from qto_spatial import BoundsIndex, PointIndex, nearby_pairs, point

SCHEMA = 'qto-pipe-route-preparation/1.0'


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(name+'须为有限数值')
    return float(value)


def text(value, name):
    if not isinstance(value,str) or not value.strip(): raise ValueError(name+'须为非空文字')
    return value.strip()


def evidence(row, name):
    values = row.get('source_ids')
    if not isinstance(values,list) or not values or any(not isinstance(v,str) or not v.strip() for v in values):
        raise ValueError(name+'缺少定位证据source_ids')
    text(row.get('basis'),name+'.basis')
    return values


def xyz(p):
    p = point(p)
    return list(p if len(p)==3 else (*p,0.))


def geometry_parts(row):
    g = row.get('geometry')
    if g is not None:
        if g.get('schema')!=GEOMETRY_SCHEMA or g.get('coordinate_system')!='WCS':
            raise ValueError('路线几何须为完整WCS参数几何，请重新提取')
        parts = copy.deepcopy(g.get('parts'))
        if not isinstance(parts,list) or not parts: raise ValueError('路线没有可用几何')
    else:
        a,b = xyz(row['start']),xyz(row['end'])
        measured = finite(row.get('length',math.dist(a,b)), 'length')
        if not math.isclose(measured,math.dist(a,b),rel_tol=1e-9,abs_tol=1e-8):
            raise ValueError('非直线只有起终点和总长度，不能用弦线建网；须补完整路径')
        parts = [{'kind':'line','start':a,'end':b}]
    for part in parts:
        if part.get('kind')=='line':
            part['start'],part['end']=xyz(part['start']),xyz(part['end'])
        elif part.get('kind')=='ellipse_arc':
            for k in ['center','u','v']: part[k]=xyz(part[k])
            for k in ['start_param','sweep_param']: part[k]=finite(part[k],k)
            if abs(part['sweep_param'])>math.tau+1e-8: raise ValueError('单弧参数超过一周，须分为独立路线')
        else: raise ValueError('暂不支持的路线几何，不能作为直线计算')
        if measure_part(part)[0]<=0: raise ValueError('路线含零长度段')
    for a,b in zip(parts,parts[1:]):
        if math.dist(part_point(a,1),part_point(b,0))>1e-7: raise ValueError('路径各段不连续，不能自动补长')
    return parts


def part_point(part, fraction):
    if part['kind']=='line': return [a+(b-a)*fraction for a,b in zip(part['start'],part['end'])]
    return conic_point(part,part['start_param']+part['sweep_param']*fraction)


def part_slice(part, a, b):
    if part['kind']=='line': return {'kind':'line','start':part_point(part,a),'end':part_point(part,b)}
    out=copy.deepcopy(part);out['start_param']+=part['sweep_param']*a;out['sweep_param']*=b-a
    return out


def part_tangent(part, fraction):
    if part['kind']=='line': return [b-a for a,b in zip(part['start'],part['end'])]
    t=part['start_param']+part['sweep_param']*fraction
    return [(-u*math.sin(t)+v*math.cos(t))*part['sweep_param'] for u,v in zip(part['u'],part['v'])]


def project_part(p, part):
    """Closest point via all stationary roots of the conic distance quartic."""
    p=xyz(p)
    if part['kind']=='line':
        a,b=part['start'],part['end'];v=[y-x for x,y in zip(a,b)];den=sum(x*x for x in v)
        t=max(0.,min(1.,sum((q-x)*v for q,x,v in zip(p,a,v))/den)) if den else 0.
        return math.dist(p,part_point(part,t)),t
    import numpy as np  # ezdxf's already-required dependency
    c,u,v=part['center'],part['u'],part['v'];w=[a-b for a,b in zip(c,p)]
    A=sum(a*b for a,b in zip(w,u));B=sum(a*b for a,b in zip(w,v))
    D=sum(x*x for x in v)-sum(x*x for x in u);E=sum(a*b for a,b in zip(u,v))
    coeff=[-B+E,-2*A-2*D,-6*E,-2*A+2*D,B+E]
    scale=max(map(abs,coeff)); angles=[math.pi]
    if scale:
        # np.roots strips only exact leading zeros; retain small coefficients.
        for z in np.roots([a/scale for a in coeff]):
            if abs(z.imag)<=1e-8*(1+abs(z.real)): angles.append(2*math.atan(float(z.real)))
    start,sweep=part['start_param'],part['sweep_param'];end=start+sweep;lo,hi=sorted((start,end))
    fractions=[0.,1.]
    for a in angles:
        for k in range(math.ceil((lo-a)/math.tau),math.floor((hi-a)/math.tau)+1):
            if sweep: fractions.append(max(0.,min(1.,(a+k*math.tau-start)/sweep)))
    return min((math.dist(p,part_point(part,t)),t) for t in fractions)


def project_route(p, parts):
    dist,i,t=min((d,i,t) for i,part in enumerate(parts) for d,t in [project_part(p,part)])
    return dist,i,t


def route_distance(p, row):
    parts=row.get('_parts') or geometry_parts(row)
    return project_route(p,parts)[0]


def route_geometry(parts):
    return {'schema':GEOMETRY_SCHEMA,'coordinate_system':'WCS','parts':parts,'closed':False}


class RouteIndex:
    def __init__(self, rows):
        self.rows={r['segment_id']:dict(r) for r in rows};self.indices={}
        groups=defaultdict(list)
        for key,r in self.rows.items():
            if not r.get('_parts'): r['_parts']=geometry_parts(r)
            b=summarize(route_geometry(r['_parts']))['bbox3d']
            groups[(r['system'],r.get('space_id','single-view'))].append((key,b))
        self.indices={g:BoundsIndex(v) for g,v in groups.items()}

    def nearest(self, p, system, space_id=None, limit=2):
        result=[]
        for (s,space),index in self.indices.items():
            if s==system and (space_id is None or space_id==space):
                result.extend(index.nearest(xyz(p),lambda k:route_distance(p,self.rows[k]),limit))
        return sorted(result,key=lambda x:(x[0],x[1]))[:limit]

    def within(self, p, system, maximum, space_id=None):
        p=xyz(p);box=[x-maximum for x in p]+[x+maximum for x in p];result=[]
        for (s,space),index in self.indices.items():
            if s!=system or (space_id is not None and space_id!=space): continue
            for key in index.query_box(box):
                d=route_distance(p,self.rows[key])
                if d<=maximum:result.append((d,key))
        return sorted(result,key=lambda x:(x[0],x[1]))


def prepare_network(raw):
    """Normalize a 1.x input, preserving source selection and parent quantities."""
    data=copy.deepcopy(raw);source=data.get('segments')
    for field in ('roots','terminals','valves','change_nodes','scope_boundary_nodes'):
        for item in data.get(field,[]):
            if item.get('coordinate_unit') is not None and item['coordinate_unit']!=data.get('unit'):
                raise ValueError('端口/节点坐标与网络单位不同，须先显式换算')
    if not isinstance(source,list) or not source: raise ValueError('segments须为非空数组')
    report={'schema':SCHEMA,'source_segment_ids':[],'parent_map':{},'source_object_selection':copy.deepcopy(data.get('object_selection')),
            'issues':[],'junctions':[],'interfaces':[],'vertical_quantities':[],'source_lengths':{},'source_length':0.,'derived_length':0.,'length_delta':0.}
    snap=finite(data.get('parameters',{}).get('snap_tolerance',.01),'snap_tolerance')
    if snap<0:raise ValueError('吸附容差不能为负')
    tol=max(1e-9,snap)
    originals={};parts_by_id={};cuts={};closed={}
    for r in source:
        sid=text(r.get('segment_id'),'segment_id')
        if sid in originals:raise ValueError('segment_id重复:'+sid)
        text(r.get('system'),sid+'.system');r['space_id']=text(r.get('space_id','single-view'),sid+'.space_id')
        originals[sid]=r;parts=geometry_parts(r);parts_by_id[sid]=parts
        actual=sum(measure_part(p)[0] for p in parts)
        if not math.isclose(actual,finite(r.get('length',actual),sid+'.length'),rel_tol=1e-9,abs_tol=1e-7):
            raise ValueError(sid+'的完整几何与声明长度不一致')
        closed[sid]=math.dist(part_point(parts[0],0),part_point(parts[-1],1))<=1e-8
        cuts[sid]={i:{0.,1.} for i in range(len(parts))}
        for i,p in enumerate(parts):
            if math.dist(part_point(p,0),part_point(p,1))<=1e-8:cuts[sid][i].add(.5)
        report['source_segment_ids'].append(sid);report['source_lengths'][sid]=actual
    physical=defaultdict(list)
    for sid,r in originals.items():
        if r.get('physical_id'):physical[r['physical_id']].append(sid)
    for pid,members in physical.items():
        if len(members)>1:raise ValueError('同一physical_id有多个计量出口，参考视图须先排除:'+str(pid))

    def locate(ref):
        sid=text(ref.get('segment_id'),'连接对象segment_id')
        if sid not in originals:raise ValueError('连接引用不存在对象:'+sid)
        parts=parts_by_id[sid]
        if ref.get('end') in ('start','end'):
            return sid,0,0.  # the end endpoint is resolved below
        if 'part_index' in ref or 'parameter' in ref:
            i=ref.get('part_index',0);t=finite(ref.get('parameter'),'parameter')
            if isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(parts) or not 0<=t<=1:
                raise ValueError('接管点段号或参数越界')
        elif 'point' in ref:
            d,i,t=project_route(ref['point'],parts)
            if d>tol:raise ValueError(sid+'接点不在所声明的曲线上')
        else:raise ValueError('接点须有end、参数或图面坐标')
        if t<=1e-10:t=0.
        if t>=1-1e-10:t=1.
        return sid,i,t

    locate_base=locate
    def locate(ref):
        if ref.get('end')=='end':
            sid=text(ref.get('segment_id'),'segment_id')
            if sid not in originals:raise ValueError('端点引用不存在:'+sid)
            return sid,len(parts_by_id[sid])-1,1.
        return locate_base(ref)

    # Preserve original geometric continuity, but never merge an artificial cut
    # with another route just because both happen to have the same coordinates.
    def vertex_key(s,i,t):
        t=round(t,12)
        if closed[s] and i==len(parts_by_id[s])-1 and t==1.: return (s,0,0.)
        if t==1. and i+1<len(parts_by_id[s]): return (s,i+1,0.)
        return (s,i,t)
    tokens={};reserved=set();explicit_ids=set()
    for j in data.get('junctions',[]):
        jid=text(j.get('junction_id'),'junction_id')
        if jid in explicit_ids:raise ValueError('junction_id重复:'+jid)
        explicit_ids.add(jid);evidence(j,jid)
        decision=j.get('decision')
        if decision not in ('connect','separate','unresolved'):raise ValueError('接点decision无效')
        refs=j.get('members')
        if not isinstance(refs,list) or len(refs)<2:raise ValueError('接点至少有两个成员')
        locs=[locate(r) for r in refs]
        if len(set(locs))!=len(locs):raise ValueError('接点成员重复')
        if len({(originals[s]['system'],originals[s]['space_id']) for s,_,_ in locs})!=1:
            raise ValueError('局部接管点跨系统或跨图页，须使用有依据的跨图接口')
        pts=[part_point(parts_by_id[s][i],t) for s,i,t in locs]
        if any(math.dist(a,b)>tol for n,a in enumerate(pts) for b in pts[n+1:]):raise ValueError('接管点成员超出连接容差')
        for s,i,t in locs:
            key=vertex_key(s,i,t)
            if key in reserved:raise ValueError('同一接点有相互覆盖的决定，请先合并裁决')
            reserved.add(key);cuts[s][i].add(t)
            tokens[key]=('join:'+jid) if decision=='connect' else ('separate:'+jid+':'+s)
        report['junctions'].append(copy.deepcopy(j))
        if decision=='unresolved':report['issues'].append({'type':'连接待核实','id':jid,'segment_ids':sorted({s for s,_,_ in locs}),'basis':j['basis']})

    # Explicitly located reducers are split before propagation boundaries bind.
    for m in data.get('change_nodes',[]):
        if m.get('segment_id') in originals:
            s,i,t=locate(m);cuts[s][i].add(t)
    for m in data.get('scope_boundary_nodes',[]):
        if m.get('segment_id') in originals:
            s,i,t=locate(m);cuts[s][i].add(t)

    interface_defs=[];link_ids=set();physical_links=set();interface_endpoint_pairs=set()
    for link in data.get('cross_view_links',[]):
        lid=text(link.get('link_id'),'link_id');pid=text(link.get('physical_id'),lid+'.physical_id');evidence(link,lid)
        if lid in link_ids or pid in physical_links:raise ValueError('跨图接口或竖向实物重复')
        link_ids.add(lid);physical_links.add(pid)
        if link.get('dn_continuity') is True and not link.get('dn_continuity_source_id'):
            raise ValueError('跨图规格连续性缺少独立依据')
        a,b=locate(link['a']),locate(link['b'])
        endpoint_pair=tuple(sorted((vertex_key(*a),vertex_key(*b))))
        if endpoint_pair in interface_endpoint_pairs:
            raise ValueError('同一跨图端口对被重复关联，不能通过换link_id或physical_id重复计量')
        interface_endpoint_pairs.add(endpoint_pair)
        if originals[a[0]]['system']!=originals[b[0]]['system']:raise ValueError('跨图接口不能串接不同系统')
        if link.get('system') is not None and link['system']!=originals[a[0]]['system']:
            raise ValueError('跨图接口声明系统与端口系统不同')
        if originals[a[0]]['space_id']==originals[b[0]]['space_id']:raise ValueError('同图局部连接应使用junctions')
        for s,i,t in (a,b):cuts[s][i].add(t)
        interface_defs.append((link,a,b))

    derived=[];ranges={};endpoint_refs=defaultdict(list)
    def token(s,i,t):
        key=vertex_key(s,i,t)
        if key in tokens:return tokens[key]
        # Adjacent geometric parts use the same vertex token.
        if t==1. and i+1<len(parts_by_id[s]):
            key=(s,i+1,0.)
            if key in tokens:return tokens[key]
        if t==0. and i>0:
            other=(s,i-1,1.)
            if other in tokens:return tokens[other]
        if (i==0 and t==0.) or (i==len(parts_by_id[s])-1 and t==1.):
            if closed[s]:return 'vertex:'+s+':closed'
            return None
        return 'vertex:'+s+':'+str(i+int(t==1.))+':'+str(0. if t==1. else round(t,12))
    for sid in sorted(originals):
        original=originals[sid];intervals=[]
        for i,part in enumerate(parts_by_id[sid]):
            marks=sorted(cuts[sid][i])
            for a,b in zip(marks,marks[1:]):
                if b-a<=1e-12:continue
                intervals.append((i,a,b,part_slice(part,a,b)))
        report['parent_map'][sid]=[]
        for n,(i,a,b,part) in enumerate(intervals):
            eid=sid if len(intervals)==1 else sid+'::edge'+str(n+1).zfill(4)
            if eid!=sid and eid in originals:raise ValueError('派生管段编号与源对象冲突:'+eid)
            summary=summarize(route_geometry([part]))
            row={**copy.deepcopy(original),'segment_id':eid,'parent_segment_id':sid,'geometry':route_geometry([part]),'_parts':[part],
                 'parent_geometry_interval':{'part':i,'from_fraction':a,'to_fraction':b},
                 'start':part_point(part,0),'end':part_point(part,1),'length':summary['length'],
                 'start_token':token(sid,i,a),'end_token':token(sid,i,b),
                 'tangent_start':part_tangent(part,0),'tangent_end':part_tangent(part,1),'dn_seeds':[]}
            derived.append(row);ranges[eid]=(sid,i,a,b);report['parent_map'][sid].append(eid)
            endpoint_refs[(sid,i,round(a,12))].append((eid,'start'))
            endpoint_refs[(sid,i,round(b,12))].append((eid,'end'))
    byid={r['segment_id']:r for r in derived}
    def child_refs(ref):
        s,i,t=locate(ref)
        exact=endpoint_refs.get((s,i,round(t,12)))
        if exact:return list(exact)
        return [(eid,None) for eid in report['parent_map'][s] if ranges[eid][1]==i and ranges[eid][2]<t<ranges[eid][3]]
    def seed_targets(sid,seed):
        kids=report['parent_map'][sid]
        if len(kids)==1:return kids
        if seed.get('applies_to_entire_object') is True:
            # User/model must explicitly establish a constant-spec parent.
            if not seed.get('scope_source_id'):raise ValueError('整对象规格继承缺少scope_source_id')
            return kids
        if any(k in seed for k in ('point','part_index','parameter','end')):
            targets=child_refs(dict(seed,segment_id=sid))
            if len(targets)==1:return [targets[0][0]]
        report['issues'].append({'type':'分段后规格适用区段待核实','segment_id':sid,'seed':copy.deepcopy(seed)})
        return []
    for sid,r in originals.items():
        for seed in r.get('dn_seeds',[]):
            for eid in seed_targets(sid,seed):byid[eid]['dn_seeds'].append(copy.deepcopy(seed))
    mapped=[]
    for c in data.get('dn_match_candidates',[]):
        sid=c.get('segment_id')
        if sid not in originals:raise ValueError('DN候选引用不存在对象')
        targets=seed_targets(sid,c) if c.get('disposition')!='excluded' else [report['parent_map'][sid][0]]
        if not targets:
            targets=[report['parent_map'][sid][0]];c.update(disposition='excluded',reason='分段后标注作用区段未定位，原候选保留待复核')
        for n,eid in enumerate(targets):
            m=copy.deepcopy(c);m['segment_id']=eid
            if len(targets)>1:m['candidate_id']=m['candidate_id']+'::'+str(n+1)
            m['parent_segment_id']=sid;mapped.append(m)
    if 'dn_match_candidates' in data:
        data['dn_match_candidates']=mapped
        if isinstance(data.get('dn_match_policy'),dict):
            data['dn_match_policy']['source_detected_candidate_count']=data['dn_match_policy'].get('detected_candidate_count')
            data['dn_match_policy']['detected_candidate_count']=len(mapped)
    for field in ('gap_classifications','gap_exclusions'):
        for row in data.get(field,[]):
            for side in ('a','b'):
                s=row[side+'_segment'];end=row[side+'_end'];refs=child_refs({'segment_id':s,'end':end})
                row[side+'_segment']=refs[0][0];row[side+'_end']=refs[0][1]
    for m in data.get('valves',[]):
        if 'connected_segment_ids' in m:
            targets=[]
            for sid in m['connected_segment_ids']:
                if sid not in originals:raise ValueError('阀门引用不存在管段')
                keys=report['parent_map'][sid]
                if len(keys)>1:
                    nearby=[eid for eid in keys if route_distance(m['point'],byid[eid])<=tol]
                    if not nearby:raise ValueError('阀门声明连接但位置不在派生管段上')
                    targets.extend(nearby)
                else:targets.extend(keys)
            m['connected_segment_ids']=list(dict.fromkeys(targets))
    for field in ('roots','terminals','change_nodes','scope_boundary_nodes'):
        for m in data.get(field,[]):
            if m.get('segment_id') in originals:
                sid=m['segment_id'];m.setdefault('space_id',originals[sid]['space_id'])
                if 'point' not in m:
                    s,i,t=locate(m);m['point']=part_point(parts_by_id[s][i],t)
                m['source_segment_id']=sid
                # Nodes at a cut may touch two children: use either child.
                m['segment_id']=child_refs(dict(m,segment_id=sid))[0][0]
    for link,a,b in interface_defs:
        ea=child_refs(link['a'])[0];eb=child_refs(link['b'])[0]
        if ea[1] is None or eb[1] is None:raise ValueError('跨图接口未正确切分到端点')
        item=copy.deepcopy(link);item.update(a={'segment_id':ea[0],'end':ea[1]},b={'segment_id':eb[0],'end':eb[1]},system=originals[a[0]]['system'])
        report['interfaces'].append(item)
        qty={'physical_id':link['physical_id'],'link_id':link['link_id'],'system':item['system'],'value':None,'unit':data.get('unit'),
             'quantity_level':'未决量','source_ids':link['source_ids'],'method':'仅跨图对应，不用图面坐标补管长','counted_elsewhere':False}
        typed = None
        if 'elevation_facts' in link:
            typed = evaluate_elevation_link(link['elevation_facts'], output_unit=data.get('unit'), expected_system=item['system'])
            qty['elevation_calculation'] = typed
            qty['source_ids'] = list(dict.fromkeys(link['source_ids'] + typed['source_ids']))
        owner=link.get('quantity_owner_segment_id')
        if owner is not None:
            if owner not in originals or originals[owner].get('physical_id')!=link['physical_id']:
                raise ValueError('跨图接口既有计量出口的实物身份不一致')
            if originals[owner]['system']!=item['system']:
                raise ValueError('跨图接口既有计量出口属于不同系统')
            qty.update(counted_elsewhere=True,quantity_owner_segment_id=owner)
        elif link['physical_id'] in physical:
            raise ValueError('竖向实物已经有计量出口，须显式引用，不得重复计算')
        elif typed is not None:
            qty.update(value=typed['value'],quantity_level=typed['quantity_level'],method=typed['method'],dn=link.get('dn'),
                       vertical_projection=typed['vertical_projection'],route_kind=typed['route_kind'])
            if typed['value'] is None:
                report['issues'].append({'type':'跨图接口标高或路线待核实','link_id':link['link_id'],
                                         'physical_id':link['physical_id'],'details':copy.deepcopy(typed['issues'])})
        elif link.get('route_is_vertical') is True and 'from_elevation' in link and 'to_elevation' in link:
            if not link.get('height_source_ids'):raise ValueError('竖向高度缺少证据')
            if link.get('elevation_unit')!=data.get('unit'):raise ValueError('标高与长度单位须先显式统一')
            if link.get('height_level') not in ('confirmed','inferred','scenario'):raise ValueError('高度层级无效')
            value=abs(finite(link['to_elevation'],'to_elevation')-finite(link['from_elevation'],'from_elevation'))
            qty.update(value=value,quantity_level={'confirmed':'确认量','inferred':'规则推算量','scenario':'方案暂估量'}[link['height_level']],
                       method='有对应依据的起止标高差',source_ids=link['source_ids']+link['height_source_ids'],dn=link.get('dn'))
        else:
            report['issues'].append({'type':'跨图接口竖向量待核实','link_id':link['link_id'],'physical_id':link['physical_id']})
        report['vertical_quantities'].append(qty)
    report['issues'].extend(unresolved_contacts(originals, parts_by_id, report['junctions'], tol))
    data['segments']=derived
    selection=data.get('object_selection')
    if isinstance(selection,dict):
        if set(selection.get('included_segment_ids',[]))!=set(originals) or selection.get('included_count')!=len(originals):
            raise ValueError('源对象审计与分段前路线不一致')
        selection['source_included_count']=len(originals);selection['source_included_segment_ids']=sorted(originals)
        added=len(derived)-len(originals)
        selection['included_count']=len(derived);selection['candidate_count']+=added;selection['included_segment_ids']=sorted(byid)
        selection['count_basis']='派生拓扑边；原始图元审计完整保留在route_preparation.source_object_selection'
    report['source_length']=math.fsum(report['source_lengths'].values());report['derived_length']=math.fsum(r['length'] for r in derived)
    report['length_delta']=report['derived_length']-report['source_length']
    if not math.isclose(report['source_length'],report['derived_length'],abs_tol=1e-7,rel_tol=1e-10):raise ValueError('分段前后长度不守恒')
    report['source_segment_ids'].sort()
    return data,report


def make_endpoint_nodes(endpoints, segments, tolerance):
    """Deterministic bounded snap clusters, isolated by system/view/junction token."""
    byid=segments;enriched=[]
    for e in endpoints:
        r=byid[e['segment_id']];enriched.append({**e,'space_id':r.get('space_id','single-view'),'token':r.get(e['end_name']+'_token')})
    groups=[(e['system'],e['space_id'],e['token']) for e in enriched]
    index=PointIndex([e['point'] for e in enriched],max(tolerance,1e-9),groups)
    parent=list(range(len(enriched)));members={i:[i] for i in parent};rejected=[]
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for a,b in index.pairs(tolerance):
        ra,rb=root(a),root(b)
        if ra==rb:continue
        if any(math.dist(enriched[x]['point'],enriched[y]['point'])>tolerance for x in members[ra] for y in members[rb]):
            rejected.append([a,b]);continue
        parent[rb]=ra;members[ra].extend(members.pop(rb))
    node_ids={};node_points={};mapping={};partitions={}
    for i,e in enumerate(enriched):
        r=root(i)
        if r not in node_ids:
            nid='N-'+str(len(node_ids)+1).zfill(5);node_ids[r]=nid
            node_points[nid]=tuple(sum(enriched[x]['point'][axis] for x in members[r])/len(members[r]) for axis in range(len(e['point'])))
            partitions[nid]=(e['system'],e['space_id'])
        mapping[i]=node_ids[r]
    return node_points,mapping,partitions,{'distance_checks':index.distance_checks,'rejected_transitive_pairs':rejected}


def gap_pairs(endpoints, open_ids, segments, maximum):
    candidates=[endpoints[i] for i in open_ids]
    groups=[(e['system'],segments[e['segment_id']].get('space_id','single-view')) for e in candidates]
    result=defaultdict(list)
    for a,b in nearby_pairs([e['point'] for e in candidates],maximum,groups):
        ea,eb=candidates[a],candidates[b]
        # Artificial cuts are already continuous; do not bridge separate or
        # unresolved drawing intersections by treating them as free pipe ends.
        if segments[ea['segment_id']].get(ea['end_name']+'_token') or segments[eb['segment_id']].get(eb['end_name']+'_token'):continue
        result[open_ids[a]].append(open_ids[b])
    return result


def bind_node(raw, node_points, segment_nodes, segments, maximum):
    system=raw['system'];space=raw.get('space_id')
    possible={r.get('space_id','single-view') for r in segments.values() if r['system']==system}
    if space is None:
        if len(possible)!=1:return None
        space=next(iter(possible))
    candidates=set()
    if raw.get('segment_id') is not None:
        r=segments.get(raw['segment_id'])
        if not r or r['system']!=system or r.get('space_id','single-view')!=space:return None
        candidates.update(segment_nodes[raw['segment_id']])
    else:
        for sid,r in segments.items():
            if r['system']==system and r.get('space_id','single-view')==space:candidates.update(segment_nodes[sid])
    p=xyz(raw['point']);ranked=sorted((math.dist(p,xyz(node_points[n])),n) for n in candidates)
    if not ranked or ranked[0][0]>maximum:return None
    if len(ranked)>1 and abs(ranked[1][0]-ranked[0][0])<=1e-9:return None
    return ranked[0][1]


def ambiguous_role_nodes(graph, roots_by_system):
    seen=set();ambiguous=set();all_roots={n for ns in roots_by_system.values() for n in ns}
    for start in sorted(graph):
        if start in seen:continue
        queue=[start];nodes=set();edges=0
        while queue:
            n=queue.pop()
            if n in nodes:continue
            nodes.add(n);seen.add(n);edges+=len(graph[n]);queue.extend(x[0] for x in graph[n] if x[0] not in nodes)
        if edges//2>=len(nodes) or len(nodes&all_roots)>1:ambiguous.update(nodes)
    return ambiguous




class NodeLocator:
    def __init__(self,node_points,partitions,segment_nodes,segments,cell_size):
        self.ids=sorted(node_points);self.points=node_points;self.partitions=partitions
        self.segment_nodes=segment_nodes;self.segments=segments;self.spaces=defaultdict(set)
        for system,space in partitions.values():self.spaces[system].add(space)
        self.index=PointIndex([xyz(node_points[n]) for n in self.ids],max(cell_size,1e-9),[partitions[n] for n in self.ids])
    def bind(self,raw,maximum):
        system=raw['system'];space=raw.get('space_id');sid=raw.get('segment_id')
        if sid is not None:
            segment=self.segments.get(sid)
            if not segment or segment['system']!=system:return None
            inferred_space=segment.get('space_id','single-view')
            if space is not None and space!=inferred_space:return None
            space=inferred_space
        if space is None:
            if len(self.spaces[system])!=1:return None
            space=next(iter(self.spaces[system]))
        p=xyz(raw['point'])
        allowed=set(self.segment_nodes[sid]) if sid else None
        ranked=sorted((math.dist(p,xyz(self.points[self.ids[i]])),self.ids[i]) for i in self.index.query(p,maximum,(system,space)) if allowed is None or self.ids[i] in allowed)
        if not ranked or (len(ranked)>1 and abs(ranked[0][0]-ranked[1][0])<=1e-9):return None
        return ranked[0][1]


def add_interfaces(report,endpoint_lookup,endpoint_node,graph):
    edges=[]
    for link in report['interfaces']:
        a=endpoint_node[endpoint_lookup[(link['a']['segment_id'],link['a']['end'])]]
        b=endpoint_node[endpoint_lookup[(link['b']['segment_id'],link['b']['end'])]]
        edge={**link,'node_a':a,'node_b':b}
        graph[a].append((b,'interface',link['link_id']));graph[b].append((a,'interface',link['link_id']));edges.append(edge)
    return edges


def finalize_network(output, report, segments, snap_stats):
    output['route_preparation']=report;output['spatial_diagnostics']=snap_stats
    output['vertical_quantities']=report['vertical_quantities']
    for r in output.get('segments',[]):
        source=segments[r['segment_id']]
        for key in ('parent_segment_id','parent_geometry_interval','space_id','start','end','geometry'):
            if key in source:r[key]=copy.deepcopy(source[key])
    output['metrics']['source_route_count']=len(report['source_segment_ids'])
    output['metrics']['source_route_length']=report['source_length']
    output['metrics']['split_length_conservation_delta']=report['length_delta']
    output['metrics']['cross_view_link_count']=len(report['interfaces'])
    output['metrics']['vertical_unresolved_count']=sum(q['value'] is None and not q['counted_elsewhere'] for q in report['vertical_quantities'])
    output['metrics']['route_issue_count']=len(report['issues'])
    if report['issues']:
        output['workflow_readiness']['status']='REVIEW_REQUIRED_WITH_RESULTS'
        output['business_result_status']='PARTIAL_CALCULATED'
    output['review_summary_scope']='已绘路径量、跨图竖向量和未决分别列示；不是未经复核的完整系统总量'
    return output


def review_markdown(output):
    esc=lambda v:str(v).replace('|','\\|').replace('\n',' ')
    lines=['# 管网逐路计算复核摘要','','本摘要为工作复核量，不代表正式采用。不同数量层级及单位分别列示。',
           '','## 已绘路径分规格量','','| 系统 | 层级 | 规格 | 长度 | 单位 |','|---|---|---|---:|---|']
    for system,levels in output.get('length_summary',{}).items():
        for level,dns in levels.items():
            for dn,value in dns.items():lines.append('| '+' | '.join(esc(x) for x in (system,level,dn,format(value,'.12g'),output['unit']))+' |')
    if output.get('restored_omission_length', 0):
        lines+=['','## 图示省略段恢复后的分规格工作量','','本表已包含上表已绘量；两个表不可相加。只恢复有图面依据的省略段，不自动加入阀门占位或竖向量。',
                '','| 系统 | 层级 | 规格 | 长度 | 单位 |','|---|---|---|---:|---|']
        for system,levels in output['reconstructed_length_summary'].items():
            for level,dns in levels.items():
                for dn,value in dns.items():lines.append('| '+' | '.join(esc(x) for x in (system,level,dn,format(value,'.12g'),output['unit']))+' |')
        lines+=['','恢复增量：'+format(output['restored_omission_length'],'.12g')+' '+output['unit']+'。每处断口分类、DN、来源与处理结果见bridge_quantity_items。']
    if output.get('fitting_summary'):
        lines+=['','## 范围内管件候选','','| 系统 | 类型 | 管口规格组合 | 层级 | 候选数 |','|---|---|---|---|---:|']
        for f in output['fitting_summary']:
            lines.append('| '+' | '.join(esc(f.get(k, '待核对')) for k in ('system','type','dn_combination','quantity_level','candidate_count'))+' |')
        lines+=['','外接管口只补充连接事实，不增加计量管长；归属范围外的管件不进入本表。']
    lines+=['','## 跨图竖向及接口量','','| 实物编号 | 系统 | 数量 | 单位 | 层级 | 计算/归属说明 |','|---|---|---:|---|---|---|']
    for q in output.get('vertical_quantities',[]):
        value='已由 '+q['quantity_owner_segment_id']+' 计量' if q['counted_elsewhere'] else '待确定' if q['value'] is None else format(q['value'],'.12g')
        lines.append('| '+' | '.join(esc(x) for x in (q['physical_id'],q['system'],value,q['unit'],q['quantity_level'],q['method']))+' |')
    lines+=['','## 仍需核对的工程关系','']
    for issue in output.get('route_preparation',{}).get('issues',[]):lines.append('- '+esc(issue.get('type'))+'：'+esc(json.dumps(issue,ensure_ascii=False)))
    if not output.get('route_preparation',{}).get('issues'):lines.append('本轮已编码路径检查未发现未决；仍需核对原图覆盖和实际识别。')
    lines+=['','分段前后长度差：'+str(output['route_preparation']['length_delta']),
            '原对象到派生管段对应、规格依据和端点记录保存在同版本JSON，不能仅凭本摘要判断范围完整。']
    return '\n'.join(lines)+'\n'


def scale_route_geometry(geometry, factor):
    if geometry is None:return None
    from qto_cad_geometry import transform_geometry
    return transform_geometry(geometry,(factor,0.,0.,0.,0.,factor,0.,0.,0.,0.,factor,0.,0.,0.,0.,1.))


def unresolved_contacts(originals,parts_by_id,junctions,tolerance):
    """Discover likely missed T-junctions without promoting geometry to authority."""
    rows=[{**r,'_parts':parts_by_id[s]} for s,r in originals.items()]
    index=RouteIndex(rows);issues=[];seen=set();decisions=[]
    for j in junctions:
        positions={}
        for m in j['members']:
            sid=m['segment_id'];parts=parts_by_id[sid]
            if m.get('end')=='start':p=part_point(parts[0],0)
            elif m.get('end')=='end':p=part_point(parts[-1],1)
            elif 'point' in m:p=xyz(m['point'])
            else:p=part_point(parts[m.get('part_index',0)],m['parameter'])
            positions[sid]=p
        decisions.append(positions)
    def decided_here(pair,p):
        return any(pair<=set(d) and all(math.dist(p,d[k])<=tolerance for k in pair) for d in decisions)
    # Endpoint-to-interior discovery is O(n log n + local candidates), not
    # drawing-wide pair comparison. Interior-interior intersections stay
    # disconnected unless the model supplies a drawing-backed junction.
    for sid,r in originals.items():
        parts=parts_by_id[sid]
        for end,p in [('start',part_point(parts[0],0)),('end',part_point(parts[-1],1))]:
            for dist,other in index.within(p,r['system'],tolerance,r['space_id']):
                if other==sid:continue
                _,i,t=project_route(p,parts_by_id[other]);last=len(parts_by_id[other])-1
                if (i==0 and t<=1e-9) or (i==last and t>=1-1e-9):continue
                pair={sid,other}
                if decided_here(pair,p):continue
                key=(tuple(sorted(pair)),tuple(round(x,8) for x in p))
                if key in seen:continue
                seen.add(key);issues.append({'type':'端点落在他管中部，连接待识图确认','segment_ids':sorted(pair),
                    'point':p,'candidate_member':{'segment_id':other,'part_index':i,'parameter':t},'default_connection':False})
    flat={};groups=defaultdict(list)
    for sid,r in originals.items():
        for n,part in enumerate(parts_by_id[sid]):
            if part['kind']!='line':continue
            key=(sid,n);flat[key]=part;a,b=part['start'],part['end']
            box=[min(x,y)-tolerance for x,y in zip(a,b)]+[max(x,y)+tolerance for x,y in zip(a,b)]
            groups[(r['system'],r['space_id'])].append((key,box))
    for entries in groups.values():
        idx=BoundsIndex(entries)
        for key,box in entries:
            sid,_=key;p=flat[key];a=p['start'];u=[y-x for x,y in zip(a,p['end'])];A=sum(x*x for x in u)
            for other in idx.query_box(box):
                if other<=key or other[0]==sid:continue
                pair={sid,other[0]};q=flat[other];b=q['start'];v=[y-x for x,y in zip(b,q['end'])];w=[x-y for x,y in zip(a,b)]
                B=sum(x*y for x,y in zip(u,v));C=sum(x*x for x in v);D=sum(x*y for x,y in zip(u,w));E=sum(x*y for x,y in zip(v,w));den=A*C-B*B
                if abs(den)<=1e-12*A*C:
                    cross=[w[1]*u[2]-w[2]*u[1],w[2]*u[0]-w[0]*u[2],w[0]*u[1]-w[1]*u[0]]
                    if math.sqrt(sum(x*x for x in cross)/A)>tolerance:continue
                    ts=[sum((x-y)*z for x,y,z in zip(pt,a,u))/A for pt in (b,q['end'])]
                    lo=max(0.,min(ts));hi=min(1.,max(ts))
                    if (hi-lo)*math.sqrt(A)<=tolerance:continue
                    pnt=part_point(p,(lo+hi)/2);issue='共线重叠路线，须核对重复表达或并行实物'
                else:
                    x=(B*E-C*D)/den;y=(A*E-B*D)/den
                    if not 1e-9<x<1-1e-9 or not 1e-9<y<1-1e-9:continue
                    pnt=part_point(p,x)
                    if math.dist(pnt,part_point(q,y))>tolerance:continue
                    issue='图内交叉但无连接依据，待识图确认'
                if decided_here(pair,pnt):continue
                marker=(tuple(sorted(pair)),tuple(round(x,8) for x in pnt))
                if marker in seen:continue
                seen.add(marker);issues.append({'type':issue,'segment_ids':sorted(pair),'point':pnt,'default_connection':False})
    return issues
