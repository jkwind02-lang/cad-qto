"""Source-bounded recovery helpers. No drawing names, handles or assumed heights."""
from __future__ import annotations
import copy
import math
import re
from collections import defaultdict, deque


def number(value, label, minimum=0.):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(label+'须为有限数值且不小于'+str(minimum))
    return float(value)


def atomic_outline_pairs(items, selected, paired_geometry, geometry, digest, entries, used):
    """Split every parallel overlap at all opposing source ends before matching.

    Mutual uniqueness is decided per atomic interval, not per original entity.
    Duplicate outlines remain ambiguous only on the intervals they actually cover.
    """
    eligible=[p for p in items if p['part']['kind']=='line' and len(selected.get(p['entry']['source_key'],[]))==1
              and selected[p['entry']['source_key']][0]['representation']=='outline']
    cuts={p['entry']['source_key']:{0.,1.} for p in eligible}
    overlaps=[]
    for i,a in enumerate(eligible):
        rule=selected[a['entry']['source_key']][0]
        width=rule['dimensions'].get('diameter',rule['dimensions'].get('width'))
        if width is None: continue
        tol=rule.get('pair_tolerance')
        if isinstance(tol,bool) or not isinstance(tol,(int,float)) or not 0<=tol<width/4:
            raise ValueError('轮廓配对容差必须显式且小于宽度四分之一')
        for b in eligible[i+1:]:
            if selected[b['entry']['source_key']] != [rule]: continue
            mid=paired_geometry(a,b,width,tol)
            if mid is None: continue
            fractions=mid['_source_fractions']
            overlaps.append((a,b,fractions,rule))
            for p,(lo,hi) in zip((a,b),fractions): cuts[p['entry']['source_key']].update((lo,hi))
    # All breakpoints on any parallel source line are projected onto its partner.
    # This closes cuts from a third outline before deciding mutual uniqueness.
    for _ in range(len(eligible)+1):
        changed=False
        for a,b,fr,_ in overlaps:
            for p,q,pr,qr in ((a,b,fr[0],fr[1]),(b,a,fr[1],fr[0])):
                pk,qk=p['entry']['source_key'],q['entry']['source_key']
                qp=q['part']; d=[y-x for x,y in zip(qp['start'],qp['end'])]; ll=sum(v*v for v in d)
                for t in list(cuts[pk]):
                    if not pr[0]-1e-12<=t<=pr[1]+1e-12:continue
                    pt=[x+(y-x)*t for x,y in zip(p['part']['start'],p['part']['end'])]
                    u=sum((v-x)*dv for v,x,dv in zip(pt,qp['start'],d))/ll
                    if qr[0]-1e-12<=u<=qr[1]+1e-12 and not any(abs(u-v)<1e-12 for v in cuts[qk]):
                        cuts[qk].add(max(0.,min(1.,u)));changed=True
        if not changed: break
    else: raise ValueError('轮廓分区未收敛，不能截断处理')
    atoms=[]
    for p in eligible:
        original=p['entry']; key=original['source_key']; values=sorted(cuts[key])
        if len(values)==2: atoms.append(p);continue
        original.update(disposition='excluded',reason='源part按全部实际重叠断点分区；父行仅作索引，子区间各有唯一去向',partition_children=[])
        used.add(key)
        for lo,hi in zip(values,values[1:]):
            if hi-lo<=1e-12: continue
            part={'kind':'line','start':[x+(y-x)*lo for x,y in zip(p['part']['start'],p['part']['end'])],
                  'end':[x+(y-x)*hi for x,y in zip(p['part']['start'],p['part']['end'])]}
            sk=key+'@'+format(lo,'.12g')+':'+format(hi,'.12g')
            child={**original,'source_key':sk,'geometry':geometry(part),'geometry_sha256':digest(part),'parent_source_part':key,
                   'source_fraction_range':[lo,hi],'raw_geometry_length':math.dist(part['start'],part['end']),
                   'disposition':'unrecognized','reason':'实际子区间尚无唯一对应轮廓','partition_children':[]}
            original['partition_children'].append(sk);entries.append(child)
            atoms.append({**p,'entry':child,'part':part,'_rule':selected[key][0]})
    neighbors=defaultdict(list)
    for i,a in enumerate(atoms):
        rule=a.get('_rule') or selected[a['entry']['source_key']][0]
        width=rule['dimensions'].get('diameter',rule['dimensions'].get('width'))
        if width is None: continue
        for j,b in enumerate(atoms[i+1:],i+1):
            br=b.get('_rule') or selected[b['entry']['source_key']][0]
            if rule != br:continue
            mid=paired_geometry(a,b,width,rule['pair_tolerance'])
            if mid is None:continue
            if any(lo>1e-10 or hi<1-1e-10 for lo,hi in mid['_source_fractions']):
                raise ValueError('轮廓原子区间未按完整交集闭合')
            mid.pop('_source_fractions')
            neighbors[i].append((j,mid));neighbors[j].append((i,mid))
    pairs=[];done=set()
    for i,a in enumerate(atoms):
        if i in done:continue
        opts=neighbors[i]
        if len(opts)==1 and len(neighbors[opts[0][0]])==1:
            j,mid=opts[0];done.update((i,j))
            pairs.append(([a,atoms[j]],mid,a.get('_rule') or selected[a['entry']['source_key']][0]))
        elif opts:
            a['entry'].update(reason='该实际子区间存在多重轮廓配对；保留全部候选，不影响其他唯一分区',
                              pairing_candidates=[atoms[j]['entry']['source_key'] for j,_ in opts])
    return pairs, {p['entry']['source_key'] for p in eligible}


def distance_to_line(point, part):
    d=[b-a for a,b in zip(part['start'],part['end'])];ll=sum(v*v for v in d)
    if ll<=0:raise ValueError('零长引线')
    t=max(0.,min(1.,sum((x-a)*v for x,a,v in zip(point,part['start'],d))/ll))
    closest=[a+t*v for a,v in zip(part['start'],d)]
    return math.dist(point,closest)


def source_curve_point(part, fraction):
    if part['kind']=='line':return [a+fraction*(b-a) for a,b in zip(part['start'],part['end'])]
    import qto_cad_geometry as cg
    return cg.conic_point(part,part['start_param']+fraction*part['sweep_param'])


def source_curve_incidence(point, part):
    """A source parameter witness and actual residual, never a display chord.

    For an off-curve ellipse point this is a conservative incidence witness,
    not a nearest-distance measurement or physical length estimator.
    """
    if part['kind']=='line':
        d=[b-a for a,b in zip(part['start'],part['end'])];ll=sum(v*v for v in d)
        if ll<=0:raise ValueError('零长引线part')
        t=max(0.,min(1.,sum((x-a)*v for x,a,v in zip(point,part['start'],d))/ll))
        return t,math.dist(point,source_curve_point(part,t))
    if part['kind']!='ellipse_arc':raise ValueError('不支持的引线源曲线类型')
    u,v=part['u'],part['v'];w=[p-c for p,c in zip(point,part['center'])]
    uu=sum(x*x for x in u);vv=sum(x*x for x in v);uv=sum(x*y for x,y in zip(u,v));det=uu*vv-uv*uv
    if det<=0:raise ValueError('引线圆锥曲线退化')
    wu=sum(x*y for x,y in zip(w,u));wv=sum(x*y for x,y in zip(w,v))
    angle=math.atan2((wv*uu-wu*uv)/det,(wu*vv-wv*uv)/det)
    start,sweep=part['start_param'],part['sweep_param']
    if not math.isfinite(sweep) or sweep==0 or abs(sweep)>math.tau+1e-9:raise ValueError('引线曲线扫角无效')
    middle=round((start-angle)/math.tau);fractions=[0.,1.]
    for shift in range(middle-2,middle+3):
        fraction=(angle+shift*math.tau-start)/sweep
        if 0<=fraction<=1:fractions.append(fraction)
    return min(((f,math.dist(point,source_curve_point(part,f))) for f in fractions),key=lambda value:value[1])


def distance_to_source_curve(point, part):
    return source_curve_incidence(point,part)[1]


def source_curve_contact_possible(point, part, tolerance):
    """Do not prove an omitted edge disconnected with an upper distance bound.

    Ellipse parameter projection gives a point on the curve, so its residual
    is an upper bound only. A full-ellipse singular-value lower bound can
    rule out contact; uncertainty blocks closure and never creates a join.
    """
    if part['kind']=='line':return distance_to_line(point,part)<=tolerance
    if part['kind']!='ellipse_arc':return True
    u,v=part['u'],part['v'];w=[p-c for p,c in zip(point,part['center'])]
    uu=sum(x*x for x in u);vv=sum(x*x for x in v);uv=sum(x*y for x,y in zip(u,v));det=uu*vv-uv*uv
    if det<=0:return True
    wu=sum(x*y for x,y in zip(w,u));wv=sum(x*y for x,y in zip(w,v))
    cosine=(wu*vv-wv*uv)/det;sine=(wv*uu-wu*uv)/det
    normal=[q-cosine*a-sine*b for q,a,b in zip(w,u,v)]
    largest=(uu+vv+math.hypot(uu-vv,2*uv))/2
    smallest_scale=math.sqrt(det/largest)
    lower_bound=math.hypot(math.sqrt(sum(x*x for x in normal)),smallest_scale*abs(math.hypot(cosine,sine)-1))
    # Roundoff only widens the unresolved band; it never supplies positive
    # incidence evidence to the source graph.
    rounding=128*math.ulp(max(1.,math.sqrt(uu),math.sqrt(vv),math.sqrt(sum(x*x for x in w))))
    return lower_bound<=tolerance+rounding


def annotation_network_scope(rule, evidence, by_key, match, source_key):
    """Read a complete, source-selected leader tree and its multiple target tips.

    Text attachment is to an explicit shoulder source part, not nearest text.
    Targets must be unique against the full declared target selector.
    """
    binding=rule['annotation_binding'];cfg=binding.get('leader_network')
    if not cfg:return None
    if evidence.get('annotation_network')!=cfg:
        raise ValueError('引线网络与冻结识别依据不一致')
    keys=cfg.get('source_keys')
    if not isinstance(keys,list) or not keys or len(keys)!=len(set(keys)):
        raise ValueError('引线网络须给出完整且不重复的源实例')
    tol=number(cfg.get('snap_tolerance'),'引线端点容差')
    text_tol=number(cfg.get('text_tolerance'),'文字肩线容差')
    target_tol=number(cfg.get('target_tolerance'),'指向目标容差')
    if not cfg.get('basis') or not cfg.get('source_locator'):raise ValueError('引线网络缺适用范围依据')
    parts=[]
    for key in keys:
        row=by_key.get(key);geo=(row or {}).get('geometry') or {}
        if row is None or row.get('geometry_issues') or not geo.get('parts') or any(p['kind'] not in {'line','ellipse_arc'} for p in geo['parts']):
            raise ValueError('引线网络须使用已完整恢复的直线/圆锥曲线/多段线源几何')
        parts.extend((key,i,p) for i,p in enumerate(geo['parts']))
    originals=list(parts)
    # T-junctions can end inside a longer trunk entity. Node actual endpoint
    # incidences without extending or snapping a gap into a new leader segment.
    ends=[source_curve_point(p,f) for _,_,p in originals for f in (0.,1.)]
    parts=[]
    for key,i,p in originals:
        cuts={0.,1.}
        for end in ends:
            t,residual=source_curve_incidence(end,p)
            if 0<t<1 and residual<=tol and min(math.dist(end,source_curve_point(p,f)) for f in (0.,1.))>tol:cuts.add(t)
        ordered=sorted(cuts)
        for lo,hi in zip(ordered,ordered[1:]):
            if p['kind']=='line':piece={'kind':'line','start':source_curve_point(p,lo),'end':source_curve_point(p,hi)}
            else:piece={**p,'start_param':p['start_param']+lo*p['sweep_param'],'sweep_param':(hi-lo)*p['sweep_param']}
            parts.append((key,i,piece))
    nodes=[];edges=[]
    def node(point):
        near=[i for i,p in enumerate(nodes) if math.dist(p,point)<=tol]
        if len(near)>1:raise ValueError('引线端点容差导致多个节点合并候选')
        if near:return near[0]
        nodes.append(list(point));return len(nodes)-1
    for key,i,p in parts:edges.append((node(source_curve_point(p,0.)),node(source_curve_point(p,1.)),key,i,p))
    adj=defaultdict(set)
    for a,b,*_ in edges:
        if a==b:raise ValueError('引线端点容差吞掉非零线段')
        adj[a].add(b);adj[b].add(a)
    visited=set();todo=[0]
    while todo:
        n=todo.pop()
        if n in visited:continue
        visited.add(n);todo.extend(adj[n]-visited)
    status={'schema_version':'qto-duct-annotation-scope/1.0','tag_source_key':binding['source_key'],
            'leader_source_keys':keys,'target_part_keys':[],'business_adoption_allowed':False}
    if len(visited)!=len(nodes) or len(edges)!=len(nodes)-1:
        return {**status,'status':'unresolved','reason':'源引线不是单一无环树；未按几何最近关系绑定规格'}
    layers={by_key[k].get('layer') for k in keys}
    omitted=[]
    for key,row in by_key.items():
        if key in keys or row.get('layer') not in layers:continue
        for p in (row.get('geometry') or {}).get('parts',[]):
            if p['kind'] in {'line','ellipse_arc'} and any(source_curve_contact_possible(end,q,tol) or any(source_curve_contact_possible(qend,p,tol) for qend in (source_curve_point(q,0.),source_curve_point(q,1.))) for end in (source_curve_point(p,0.),source_curve_point(p,1.)) for _,_,q in originals):
                omitted.append(key);break
    if omitted:
        return {**status,'status':'unresolved','reason':'同源引线图层存在连接到当前链但未纳入的支路，不能裁链缩小规格适用范围','omitted_connected_source_keys':sorted(omitted)}
    text=by_key[binding['source_key']]
    anchor=[text.get(k) for k in ('x','y','z')]
    if anchor[2] is None:anchor[2]=0.
    # Expanded neutral rows already carry WCS text positions.
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in anchor):
        return {**status,'status':'unresolved','reason':'文字完整WCS锚点未恢复'}
    shoulder=cfg.get('shoulder_source_key'); shoulder_part=cfg.get('shoulder_part_index',0)
    chosen=[p for key,i,p in originals if key==shoulder and i==shoulder_part]
    if len(chosen)!=1:raise ValueError('须唯一声明实际文字肩线源part')
    part=chosen[0]
    if part['kind']!='line':
        return {**status,'status':'unresolved','reason':'实际文字肩线须为直线，弧形肩线/文字沿曲线排版尚无基线对应证明'}
    if distance_to_line(anchor,part)>text_tol:
        return {**status,'status':'unresolved','reason':'文字未贴合所声明源肩线，不能强绑规格'}
    terminals=[i for i in adj if len(adj[i])==1]
    angle=number(cfg.get('shoulder_angle_tolerance_deg',.5),'肩线角度容差')
    if angle>=45:raise ValueError('肩线角度容差须小于45度')
    rotation=text.get('rotation')
    if isinstance(rotation,bool) or not isinstance(rotation,(float,int)) or not math.isfinite(rotation) or text.get('extrusion') not in (None,[0.,0.,1.],[0,0,1]):
        return {**status,'status':'unresolved','reason':'文字基线方向未恢复，不能仅以锚点相近认作肩线'}
    baseline=[math.cos(rotation),math.sin(rotation),0.]
    if text.get('instance_transform'):
        import qto_cad_geometry as cg
        matrix=text['instance_transform'];zero=cg.transform_point([0,0,0],matrix);end=cg.transform_point(baseline,matrix)
        baseline=[a-b for a,b in zip(end,zero)]
    direction=[b-a for a,b in zip(part['start'],part['end'])]
    den=math.sqrt(sum(v*v for v in direction)*sum(v*v for v in baseline))
    cosine=abs(sum(a*b for a,b in zip(direction,baseline)))/den if den else 0.
    if cosine<math.cos(math.radians(angle))-1e-12:
        return {**status,'status':'unresolved','reason':'实际肩线与文字基线方向不符'}
    roots=[i for i in terminals if distance_to_line(nodes[i],part)<=tol]
    if len(roots)!=1:
        return {**status,'status':'unresolved','reason':'肩线必须有唯一文字侧末端，不能裁掉引线支路'}
    root=roots[0];tips=[i for i in terminals if i!=root]
    targets=[]
    for row in by_key.values():
        key=source_key(row)
        if key in keys or not match(rule.get('selector',{}),row,key):continue
        for i,p in enumerate((row.get('geometry') or {}).get('parts',[])):
            if p['kind']=='line' and not row.get('geometry_issues'):targets.append((key+'#'+str(i),p))
    if cfg.get('target_contact') in {'source_curve_endpoint','source_curve_node'}:
        import qto_cad_geometry as cg
        targets=[]
        for row in by_key.values():
            key=source_key(row)
            if key in keys or row.get('geometry_issues') or not match(rule.get('selector',{}),row,key):continue
            for i,p in enumerate((row.get('geometry') or {}).get('parts',[])):
                if p['kind']=='line':ends=[p['start'],p['end']]
                elif p['kind']=='ellipse_arc':ends=[cg.conic_point(p,p['start_param']),cg.conic_point(p,p['start_param']+p['sweep_param'])]
                else:continue
                targets.append((key+'#'+str(i),ends))
    elif cfg.get('target_contact') not in (None,'line_body'):
        raise ValueError('不支持的引线接触语义')
    if cfg.get('target_contact')=='source_curve_node':
        # A system-diagram port can be the common endpoint of two actual
        # route parts. Group endpoint incidence, never nearest line bodies.
        endpoint_nodes=[]
        for key,ends in targets:
            for end in ends:
                hits=[n for n in endpoint_nodes if math.dist(end,n['point'])<=tol]
                if len(hits)>1:
                    return {**status,'status':'unresolved','reason':'源路径端点容差造成多个节点候选'}
                if hits:
                    if any(math.dist(end,p)>tol for p in hits[0]['all_points']):
                        return {**status,'status':'unresolved','reason':'源路径端点存在传递吸附歧义'}
                    hits[0]['source_part_keys'].add(key);hits[0]['all_points'].append(end)
                else:endpoint_nodes.append({'point':list(end),'source_part_keys':{key},'all_points':[end]})
        bindings=[]
        for tip in tips:
            hits=[n for n in endpoint_nodes if math.dist(nodes[tip],n['point'])<=target_tol]
            if len(hits)!=1:
                return {**status,'status':'unresolved','reason':'引线末端没有唯一实际路径端点节点'}
            bindings.append({'point':hits[0]['point'],'source_part_keys':sorted(hits[0]['source_part_keys'])})
        if len({tuple(v['point']) for v in bindings})!=len(bindings):
            return {**status,'status':'unresolved','reason':'多条指向支路重复指向同一实际路径节点'}
        return {**status,'status':'unique_source_leader_tree','reason':'完整实际肩线与全部支路唯一对应路径节点',
                'target_part_keys':sorted({k for v in bindings for k in v['source_part_keys']}),
                'target_node_bindings':bindings,'text_anchor':anchor,'root_point':nodes[root],
                'tip_points':[nodes[i] for i in tips]}
    matches=[];branch_paths=[]
    parents={root:None};parent_edges={};todo=[root]
    while todo:
        current=todo.pop()
        for edge_index,edge in enumerate(edges):
            a,b=edge[:2]
            neighbour=b if a==current else a if b==current else None
            if neighbour is not None and neighbour not in parents:
                parents[neighbour]=current;parent_edges[neighbour]=edge_index;todo.append(neighbour)
    for tip in tips:
        hits=[key for key,p in targets if (min(math.dist(nodes[tip],end) for end in p)<=target_tol if cfg.get('target_contact')=='source_curve_endpoint' else distance_to_line(nodes[tip],p)<=target_tol)]
        if len(hits)!=1:
            return {**status,'status':'unresolved','reason':'引线末端没有唯一实际管段；多支路中任一歧义阻断本标注适用范围',
                    'tip_point':nodes[tip],'candidate_part_keys':hits}
        matches.append(hits[0])
        cursor=tip;path=[]
        while cursor!=root:
            edge=edges[parent_edges[cursor]]
            path.append({'source_key':edge[2],'part_index':edge[3],'geometry':edge[4]})
            cursor=parents[cursor]
        branch_paths.append({'target_part_key':hits[0],'tip_point':nodes[tip],'leader_path':list(reversed(path))})
    if len(matches)!=len(set(matches)):
        return {**status,'status':'unresolved','reason':'多个指向末端指向同一part，适用范围需复核'}
    return {**status,'status':'unique_source_leader_tree','reason':'完整肩线及全部指向支路逐一唯一对应',
            'target_part_keys':sorted(matches),'text_anchor':anchor,'root_point':nodes[root],
            'tip_points':[nodes[i] for i in tips],'target_branch_paths':branch_paths}


def propagate_source_curve_elevations(data, net, adjacency, resolved):
    """Bridge true tangential source joins into the existing same-run graph.

    No main/branch fallback is promoted to a same-height fact. Varying source-Z
    curves cannot carry a constant elevation through their whole geometry.
    """
    from duct_network_extensions import source_curve_network, checked_geometry
    from qto_cad_source_contract import canonical_scale
    graph,_=source_curve_network(data)
    rows={r['segment_id']:r for r in data.get('segments',[])+data.get('drawn_curve_segments',[])}
    curved={r['segment_id'] for r in data.get('drawn_curve_segments',[])}
    links={sid:set(v) for sid,v in adjacency.items()}
    constant={}
    for sid,row in rows.items():
        if row.get('geometry'):
            bbox=checked_geometry(row['geometry'])['bbox3d'];constant[sid]=abs(bbox[5]-bbox[2])<=1e-9
        else:constant[sid]=True
    for join in graph['joins']:
        a,b=join['a'][0],join['b'][0]
        if constant[a] and constant[b]:links.setdefault(a,set()).add(b);links.setdefault(b,set()).add(a)
    own={};pending=[]
    for sid,row in rows.items():
        if sid in net.segments:
            value,level,_=net.center_elevation(net.segments[sid])
            if value is not None:own[sid]=(value,level)
            continue
        elev=row.get('elevation')
        if not elev:continue
        value=elev.get('value')
        if value is None:continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('曲线标高须为有限非布尔数值')
        if elev.get('reference') not in {'bottom','top','center'} or not elev.get('source_id'):
            raise ValueError('曲线标高须有明确基准与来源')
        if elev.get('origin','tag') not in {'tag','system_diagram','section','host_propagated'}:
            raise ValueError('曲线标高来源无效')
        level=elev.get('evidence_level','确认' if elev.get('origin','tag')=='tag' else None)
        if level not in {'确认','规则推算','方案暂估'} or elev.get('origin')=='host_propagated' and level=='确认':
            raise ValueError('曲线标高证据层级无效')
        if elev.get('unit') is not None:value*=canonical_scale(elev['unit'])/canonical_scale(net.output_unit)
        if elev['reference']!='center':
            height=row.get('diameter',row.get('height'))
            if height is None:continue
            number(height,'曲线管高',1e-100)
            value+=(1 if elev['reference']=='bottom' else -1)*height*net.factor/2
        if not constant[sid]:
            pending.append({'kind':'curve_elevation_profile_unresolved','segment_id':sid,'reason':'完整源曲线Z有变化；单一标高不能按全段等高传播'})
            continue
        own[sid]=(value,level)
    result={};visited=set()
    for first in sorted(curved):
        if first in visited:continue
        todo=[first];component=set()
        while todo:
            sid=todo.pop()
            if sid in component:continue
            component.add(sid);todo.extend(links.get(sid,set())-component)
        visited.update(component)
        donors=sorted(s for s in component if s in own)
        values={round(own[s][0],9) for s in donors}
        for sid in sorted(component):
            if sid in own:
                value,level=own[sid];row={'value':value,'evidence_level':level,'origin':'source_tag','basis':'本段有源标高，单位已换算到输出单位','source_segment_ids':[sid]}
            elif len(values)==1 and constant[sid]:
                value=own[donors[0]][0];level='方案暂估' if any(own[s][1]=='方案暂估' for s in donors) else '规则推算'
                row={'value':value,'evidence_level':level,'origin':'script_propagated','basis':'沿真实曲线端点/切向及旧直线同路连接传播，不跨主支路边界','source_segment_ids':donors,'hops':None}
            else:
                reason='连续源路径内存在冲突标高' if len(values)>1 else '连续源路径内无可用等高标注'
                row={'value':None,'evidence_level':'未决','basis':reason,'source_segment_ids':donors,'hops':None}
                if len(values)>1:pending.append({'kind':'curve_run_elevation_conflict','segment_id':sid,'source_segment_ids':donors,'values':sorted(values),'reason':reason})
            if sid in net.segments:
                # A scenario from a branch must yield to a valid same-run source
                # continuation. An explicit local tag retains its own value.
                if sid not in own and (donors or resolved[sid].get('value') is None):resolved[sid]=row
            else:result[sid]=row
    return result,pending


def resolve_riser_links(profile, candidates, by_key, read_ref, base, protected, section_values, unit):
    """One physical vertical outlet may own several floor projection aliases.

    Heights come from frozen actual source text, never projection arc length or
    an unlabelled floor difference. Floor identities and common datum are part
    of the explicit model correspondence evidence.
    """
    from bind_duct_vertical_annotations import parse_height
    from qto_cad_source_contract import canonical_scale
    by_part={k:r for r in candidates for k in r['source_ids']}
    seen_parts=set();seen_ids=set();out=[]
    def annotation(raw):
        if not isinstance(raw,dict):raise ValueError('竖向高度须引用实际源文字')
        key=raw.get('source_key');row=by_key.get(key)
        if row is None or row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'} or row.get('text')!=raw.get('expected_text'):
            raise ValueError('竖向高度文字与当前冻结源实例不一致')
        if not raw.get('basis') or not raw.get('source_locator'):raise ValueError('竖向高度文字缺来源定位及语义依据')
        return str(row['text'])
    for link in profile.get('riser_links',[]):
        pid=link.get('physical_riser_id');keys=link.get('projection_source_keys')
        if not isinstance(pid,str) or not pid or pid in seen_ids:raise ValueError('竖向物理ID须非空且唯一')
        seen_ids.add(pid)
        if not isinstance(keys,list) or not keys or len(keys)!=len(set(keys)) or any(k not in by_part or k in seen_parts for k in keys):
            raise ValueError('竖井投影组须完整绑定唯一未占用的源part')
        seen_parts.update(keys)
        floor_bindings=link.get('projection_floor_bindings')
        if (not isinstance(floor_bindings,list) or len(floor_bindings)!=len(keys)
                or {v.get('source_key') for v in floor_bindings}!=set(keys)
                or any(not isinstance(v.get('floor_id'),str) or not v['floor_id'] or not v.get('source_locator') for v in floor_bindings)):
            raise ValueError('每一竖井投影part须有唯一明确楼层与来源定位，不从同XY自动跨层配对')
        ev,ref=read_ref(link['evidence'],base);protected.append(__import__('pathlib').Path(ref['path']))
        expect={'schema_version':'qto-duct-riser-height-evidence/1.0','source_sha256':profile['expected_source_sha256'],
                'layout':profile['layout'],'space_id':profile['space_id'],'physical_riser_id':pid,
                'projection_source_keys':keys,'projection_floor_bindings':floor_bindings,'system':link.get('system'),'section':link.get('section')}
        if any(ev.get(k)!=v or v is None for k,v in expect.items()) or not ev.get('basis') or not ev.get('source_locator'):
            raise ValueError('竖向配对与有源证据的物理ID/系统/截面/范围不一致')
        if any(by_part[k]['system']!=link['system'] or by_part[k]['section']!=link['section'] for k in keys):
            raise ValueError('竖向配对不能跨系统或不等截面投影静默合并')
        explicit=ev.get('height_annotation');endpoints=ev.get('endpoint_annotations')
        if bool(explicit)==bool(endpoints):raise ValueError('竖高须唯一选择明示高度或上下端共同基准标高')
        if explicit:
            raw=annotation(explicit);fact,why=parse_height(raw)
            if why:raise ValueError('竖井明示高度语义未闭合:'+why)
            height=fact['length_m'];method='source_explicit_vertical_height';facts=[{**explicit,'parsed':fact}]
        else:
            if not isinstance(endpoints,list) or len(endpoints)!=2:raise ValueError('竖井上下端标高须恰好两项')
            if len({v.get('floor_id') for v in endpoints})!=2 or any(not v.get('floor_id') for v in endpoints):raise ValueError('竖井上下端须明确两个不同楼层')
            if {v['floor_id'] for v in floor_bindings}!={v['floor_id'] for v in endpoints}:
                raise ValueError('上下端标高的楼层与投影楼层对应不一致')
            if len({v.get('datum_id') for v in endpoints})!=1 or not endpoints[0].get('datum_id'):
                raise ValueError('竖井上下端标高须同一明确高程基准，不得混用各层相对标高')
            facts=[]
            section=link['section'];size=section.get('diameter',section.get('height'))
            half=size*canonical_scale(section['unit'])/2
            for ep in endpoints:
                raw=annotation(ep);found=list(re.finditer(ep.get('value_regex',''),raw))
                if len(found)!=1 or 'value' not in found[0].groupdict():raise ValueError('端标高须唯一解析命名value数值')
                val=float(found[0]['value']);number(abs(val),'竖井端标高')
                refname=ep.get('reference')
                if refname not in {'center','top','bottom'}:raise ValueError('竖井端标高须明确中心/顶/底基准')
                value=val*canonical_scale(ep['unit'])+(half if refname=='bottom' else -half if refname=='top' else 0)
                facts.append({**ep,'parsed_value':val,'center_elevation_m':value})
            height=abs(facts[1]['center_elevation_m']-facts[0]['center_elevation_m']);method='source_endpoint_center_elevation_difference'
        number(height,'竖井高度',1e-100)
        dims,_=section_values(link['section'],unit)
        section=link['section'];perimeter=(2*(section['width']+section['height']) if section['shape']=='rectangle' else math.pi*section['diameter'])*canonical_scale(section['unit'])
        item={'vertical_id':pid,'kind':'source_riser_link','system':link['system'],'section':section,**dims,
              'source_ids':keys,'projection_floor_bindings':floor_bindings,'projection_candidate_ids':sorted({by_part[k]['candidate_id'] for k in keys}),
              'length':height,'unit':'m','height_method':method,'height_facts':facts,'height_evidence':ref,
              'theoretical_surface_area':perimeter*height,'evidence_level':'规则推算','quantity_outlet':'one_physical_riser',
              'business_adoption_allowed':False}
        out.append(item)
        for binding in floor_bindings:
            by_part[binding['source_key']].update(resolved_vertical_id=pid,floor_id=binding['floor_id'],height=height,length=height,height_unit='m',evidence_level='规则推算')
    return out
