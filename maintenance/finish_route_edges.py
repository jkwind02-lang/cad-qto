"""Idempotent integration patch used only on the isolated verification branch."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
S='skills/_qto-shared-evidence/scripts/'
def edit(path,old,new):
    p=ROOT/path;s=p.read_text(encoding='utf-8-sig')
    if new in s:return
    if s.count(old)!=1:raise RuntimeError(path+': expected one replacement for '+repr(old[:80]))
    p.write_text(s.replace(old,new),encoding='utf-8',newline='\n')
edit('tests/test_manual_routes.py','required=True,source_instance_count=1,source_physical_instance_count=1,source_endpoint_port_count=2,delivered_count=2','required=True,source_instance_count=2,source_physical_instance_count=1,source_endpoint_port_count=2,delivered_count=2')
edit(S+'qto_pipe_topology.py','    tokens={};reserved=set();explicit_ids=set()', '''    def vertex_key(s,i,t):
        t=round(t,12)
        if closed[s] and i==len(parts_by_id[s])-1 and t==1.: return (s,0,0.)
        if t==1. and i+1<len(parts_by_id[s]): return (s,i+1,0.)
        return (s,i,t)
    tokens={};reserved=set();explicit_ids=set()''')
edit(S+'qto_pipe_topology.py','            key=(s,i,round(t,12))\n            if key in reserved:', '            key=vertex_key(s,i,t)\n            if key in reserved:')
edit(S+'qto_pipe_topology.py','    def token(s,i,t):\n        key=(s,i,round(t,12))','    def token(s,i,t):\n        key=vertex_key(s,i,t)')
edit(S+'qto_pipe_topology.py', '''    for j in junctions:
        decisions.append({m['segment_id'] for m in j['members']})''','''    for j in junctions:
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
        return any(pair<=set(d) and all(math.dist(p,d[k])<=tolerance for k in pair) for d in decisions)''')
edit(S+'qto_pipe_topology.py','                if any(pair<=d for d in decisions):continue','                if decided_here(pair,p):continue')
edit(S+'qto_pipe_topology.py',"    return issues\n",'''    flat={};groups=defaultdict(list)
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
''')
edit(S+'qto_pipe_topology.py',"    data=copy.deepcopy(raw);source=data.get('segments')",'''    data=copy.deepcopy(raw);source=data.get('segments')
    for field in ('roots','terminals','valves','change_nodes'):
        for item in data.get(field,[]):
            if item.get('coordinate_unit') is not None and item['coordinate_unit']!=data.get('unit'):
                raise ValueError('端口/节点坐标与网络单位不同，须先显式换算')''')
edit(S+'recompute_qto_dependencies.py','from pathlib import Path','from pathlib import Path\nfrom collections import defaultdict, deque')
old='''    order=[];visiting=set();done=set()
    def visit(k):
        if k in visiting:raise ValueError('计算依赖成环:'+k)
        if k in done:return
        r=nodes[k];refs=r.get('variables',{})
        if not isinstance(refs,dict):raise ValueError(k+'变量映射无效')
        visiting.add(k)
        for alias,dep in refs.items():
            if not isinstance(alias,str) or not alias.isidentifier() or dep not in nodes:raise ValueError(k+'存在无效或缺失依赖')
            visit(dep)
        visiting.remove(k);done.add(k);order.append(k)
    for k in nodes:visit(k)'''
new='''    order=[];degree={};children=defaultdict(list)
    for k,r in nodes.items():
        refs=r.get('variables',{})
        if not isinstance(refs,dict):raise ValueError(k+'变量映射无效')
        for alias,dep in refs.items():
            if not isinstance(alias,str) or not alias.isidentifier() or not isinstance(dep,str) or dep not in nodes:raise ValueError(k+'存在无效或缺失依赖')
        degree[k]=len(set(refs.values()))
        for dep in set(refs.values()):children[dep].append(k)
    queue=deque(k for k in nodes if degree[k]==0)
    while queue:
        k=queue.popleft();order.append(k)
        for child in children[k]:
            degree[child]-=1
            if degree[child]==0:queue.append(child)
    if len(order)!=len(nodes):raise ValueError('计算依赖成环:'+','.join(k for k in nodes if degree[k]))'''
edit(S+'recompute_qto_dependencies.py',old,new)
old='''    errors=[];visiting=set();done=set()
    def visit(key):
        if key in visiting:errors.append('成果依赖成环:'+str(key));return
        if key in done:return
        visiting.add(key);r=byid[key];layer=r.get('成果分层') or {}
        for dep in layer.get('依赖成果项',[]):
            if dep not in byid:errors.append(str(key)+'依赖不存在成果:'+str(dep));continue
            visit(dep)
            other=byid[dep];ol=other.get('成果分层') or {}
            if layer.get('进入正式量') is True and (ol.get('进入正式量') is not True or other.get('结果',{}).get('数值') is None):
                errors.append(str(key)+'进入正式量但依赖成果未闭合:'+str(dep))
        visiting.remove(key);done.add(key)
    for key in byid:visit(key)
    return errors'''
new='''    errors=[];degree={};children=defaultdict(list)
    for key,r in byid.items():
        layer=r.get('成果分层') or {}
        if not isinstance(layer,dict):errors.append(str(key)+'成果分层类型无效');layer={}
        refs=layer.get('依赖成果项',[])
        if not isinstance(refs,list):errors.append(str(key)+'依赖成果项类型无效');refs=[]
        valid=set()
        for dep in refs:
            if not isinstance(dep,str) or dep not in byid:errors.append(str(key)+'依赖不存在成果:'+str(dep));continue
            valid.add(dep);other=byid[dep];ol=other.get('成果分层') or {};ol=ol if isinstance(ol,dict) else {}
            result=other.get('结果') or {};result=result if isinstance(result,dict) else {}
            if layer.get('进入正式量') is True and (ol.get('进入正式量') is not True or result.get('数值') is None):
                errors.append(str(key)+'进入正式量但依赖成果未闭合:'+str(dep))
        degree[key]=len(valid)
        for dep in valid:children[dep].append(key)
    queue=deque(k for k in byid if degree[k]==0);visited=0
    while queue:
        key=queue.popleft();visited+=1
        for child in children[key]:
            degree[child]-=1
            if degree[child]==0:queue.append(child)
    if visited!=len(byid):errors.append('成果依赖成环:'+','.join(str(k) for k in byid if degree[k]))
    return errors'''
edit(S+'recompute_qto_dependencies.py',old,new)
