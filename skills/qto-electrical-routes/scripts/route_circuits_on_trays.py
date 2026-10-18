#!/usr/bin/env python3
"""Route evidence-bound circuit candidates on confirmed tray identities using the shared graph."""
from collections import defaultdict
import heapq
import math
from qto_electrical_evidence import bound_result, cli, finite, finish, graph, identity_rows, required
from recognize_cable_trays import project

SCHEMA='qto-circuit-on-tray-request/1.0'


def shortest(edges, start, end, equal):
    adjacency=defaultdict(list)
    for row in edges:
        adjacency[row['start_node']].append((row['end_node'],row['length'],row['segment_id']))
        adjacency[row['end_node']].append((row['start_node'],row['length'],row['segment_id']))
    # Enumerate up to two simple shortest alternatives. Positive edge lengths prevent zero-length loops.
    queue=[(0.0,start,(),(start,))]
    best=None;solutions=[];best_to={start:0.0}
    while queue:
        length,node,path,visited=heapq.heappop(queue)
        if best is not None and length>best+equal:break
        if node==end:
            best=length if best is None else best
            if path not in solutions:solutions.append(path)
            if len(solutions)>1:break
            continue
        for other,weight,sid in adjacency[node]:
            if other in visited:continue
            nxt=length+weight
            if nxt>best_to.get(other,math.inf)+equal:continue
            best_to[other]=min(nxt,best_to.get(other,math.inf))
            heapq.heappush(queue,(nxt,other,(*path,sid),(*visited,other)))
    return best,solutions


def calculate(e,request):
    if request.get('schema')!=SCHEMA:raise ValueError('request.schema必须为'+SCHEMA)
    trays=e.bind(request.get('tray_candidates'),'qto-cable-tray-candidates/1.0')
    circuits=e.bind(request.get('circuit_candidates'),'qto-electrical-circuit-candidates/1.0')
    if trays['provenance']['fingerprints']!=e.before:
        raise ValueError('桥架候选不属于当前中性图来源')
    snap=finite(request.get('snap_tolerance_m'),'snap_tolerance_m',positive=True)
    attach=finite(request.get('endpoint_tolerance_m'),'endpoint_tolerance_m')
    equal=finite(request.get('equal_length_tolerance_m'),'equal_length_tolerance_m')
    confirmation=identity_rows(request.get('tray_confirmations'),'tray_confirmations',key='segment_id')
    candidates={s['id']:s for s in trays['segments']}
    if set(confirmation)-set(candidates):raise ValueError('确认引用不存在桥架段')
    todo,segments,physical=[],[],set()
    for sid,c in confirmation.items():
        row=candidates[sid]
        pid=required(c.get('physical_id'),'physical_id')
        if pid in physical:raise ValueError('同物理桥架重复确认，不按几何相似自动合并')
        physical.add(pid)
        e.refs(c.get('basis_source_ids'),'tray_confirmation.basis_source_ids')
        required(c.get('basis'),'tray_confirmation.basis')
        width=finite(c.get('width_m'),'tray_confirmation.width_m',positive=True)
        spec=required(c.get('spec'),'tray_confirmation.spec')
        mode=c.get('elevation_mode')
        if mode=='wcs_geometry':
            start_z,end_z=row['start'][2],row['end'][2]
        elif mode=='drawing_elevations':
            start_z,end_z=c.get('start_elevation_m'),c.get('end_elevation_m')
            e.refs(c.get('start_elevation_source_ids'),'start_elevation_source_ids')
            e.refs(c.get('end_elevation_source_ids'),'end_elevation_source_ids')
        else:
            raise ValueError('须明确elevation_mode；逐端标高或已核WCS三维几何，不以一标高覆盖起终端')
        if any(isinstance(z,bool)or not isinstance(z,(float,int))or not math.isfinite(z)for z in (start_z,end_z)):
            raise ValueError('确认桥架须有每端有限标高')
        datum=required(c.get('datum'),'tray_confirmation.datum')
        resolutions=c.get('resolved_problems',{})
        if any(not isinstance(resolutions.get(p),str) or not resolutions[p].strip() for p in row['problems']):
            todo.append(dict(problem='桥架冲突/未决尚未逐项闭合，未入寻路网',segment_id=sid,remaining=[p for p in row['problems']if not resolutions.get(p)]))
            continue
        a,b=[list(row[k][:2])+[float(z)]for k,z in (('start',start_z),('end',end_z))]
        if math.dist(a,b)<=0:raise ValueError('桥架三维段长度无效')
        segments.append(dict(segment_id=sid,physical_id=pid,start=a,end=b,spec=spec,width_m=width,
                             datum=datum,source_ids=row['source_ids'],confirmation=c,length=math.dist(a,b)))
    for sid in set(candidates)-set(confirmation):
        todo.append(dict(problem='桥架物理身份未确认，未入寻路网',segment_id=sid))
    if len({s['datum']for s in segments})>1:raise ValueError('不同标高基准不得直接拼接网络')
    ledger=identity_rows(circuits['circuits'],'circuits')
    endpoint_instances=identity_rows(circuits.get('endpoint_instances',[]),'endpoint_instances')
    bindings=identity_rows(request.get('circuit_bindings'),'circuit_bindings',key='circuit_id')
    cut={s['segment_id']:{0.0,1.0}for s in segments}
    endpoints={}
    for cid,binding in bindings.items():
        if cid not in ledger:raise ValueError('引用不存在回路')
        row=ledger[cid]
        if row['state']!=['normal_candidate'] or row['parallel_count'] is None or not row['fields'].get('spec'):
            todo.append(dict(problem='回路状态/根数/规格未闭合，不自动进入寻路',circuit_id=cid))
            continue
        if row.get('chosen_endpoint')is not None:
            known=endpoint_instances.get(row['chosen_endpoint'])
            if known is None:raise ValueError('回路已选终点缺实例来源记录')
            same_source=(circuits['provenance'].get('source')or{}).get('sha256')==(e.manifest.get('authoritative_source')or{}).get('sha256')
            if not same_source or known['physical_source_id']!=binding.get('to',{}).get('physical_source_id'):
                todo.append(dict(problem='已选终点与当前端口物理身份/原图来源不一致，未静默重配',circuit_id=cid))
                continue
        required(binding.get('basis'),'circuit_binding.basis')
        e.refs(binding.get('basis_source_ids'),'circuit_binding.basis_source_ids')
        choices={}
        for role in ('from','to'):
            endpoint=binding.get(role,{})
            p=e.port(endpoint,snap)
            possible=[]
            for segment in segments:
                dist,t,q=project(p,segment)
                if dist<=attach and -snap<=t<=1+snap:
                    possible.append((dist,segment,t,q))
            if not possible:
                todo.append(dict(problem='端点离网，未退直角距离',circuit_id=cid,endpoint=role))
                continue
            minimum=min(v[0]for v in possible)
            best=[v for v in possible if abs(v[0]-minimum)<=snap]
            if any(math.dist(best[0][3],v[3])>snap for v in best[1:]):
                todo.append(dict(problem='等距端点挂接多候选',circuit_id=cid,endpoint=role,segments=[v[1]['segment_id']for v in best]))
                continue
            # A nonzero off-tray tail is not part of the network-model path. Keep it separate and pending.
            if minimum>snap:
                todo.append(dict(problem='出桥架端口末段未计入网络长',circuit_id=cid,endpoint=role,distance_m=minimum))
            chosen=best[0]
            for _,s,t,_ in best:cut[s['segment_id']].add(max(0.0,min(1.0,t)))
            choices[role]=dict(point=chosen[3],physical_id=endpoint['physical_id'],offset_m=minimum,source=endpoint)
        if len(choices)==2:endpoints[cid]=choices
    # Split T contacts and explicit port projections before using the common 3D graph builder.
    for a in segments:
        for b in segments:
            if a is b:continue
            for point in (a['start'],a['end']):
                distance,t,_=project(point,b)
                if distance<=snap and 0<t<1:cut[b['segment_id']].add(t)
    parts=[];part_of={}
    for s in segments:
        cuts=sorted(cut[s['segment_id']])
        for a,b in zip(cuts,cuts[1:]):
            if (b-a)*s['length']<=snap/100:continue
            start=[s['start'][i]+a*(s['end'][i]-s['start'][i])for i in range(3)]
            end=[s['start'][i]+b*(s['end'][i]-s['start'][i])for i in range(3)]
            pid=s['physical_id']+':'+format(a,'.12g')+':'+format(b,'.12g')
            part=dict(segment_id=pid,source_id=s['segment_id'],layer='confirmed_tray',start=start,end=end,length=math.dist(start,end),
                      bbox3d=[min(start[i],end[i])for i in range(3)]+[max(start[i],end[i])for i in range(3)],
                      part=dict(kind='line',start=start,end=end),crosses_scope_edge=False)
            parts.append(part);part_of[pid]=dict(segment=s,fraction=[a,b])
    network=graph.build_graph(parts,snap_tolerance=snap,gap_tolerance=0.0,t_tolerance=snap)
    graph_edges=network['segments'];nodes=network['nodes']
    result=[];usage=defaultdict(list);paths={};channel_items=[]
    for edge in graph_edges:
        row=part_of[edge['segment_id']];s=row['segment']
        dxdy=math.dist(edge['start'][:2],edge['end'][:2]);dz=abs(edge['start'][2]-edge['end'][2])
        role='vertical'if dxdy<=snap and dz>snap else 'offset'if dz>snap else 'horizontal'
        paths[edge['segment_id']]=dict(id=edge['segment_id'],from_node=edge['start_node'],to_node=edge['end_node'],
            length_m=edge['length'],role=role,length_basis='network_model',basis_ids=[s['segment_id']],source_ids=s['source_ids'])
    for cid,ends in endpoints.items():
        pair_nodes=[]
        for role in ('from','to'):
            near=[n['node_id']for n in nodes if math.dist(n['point'],ends[role]['point'])<=snap]
            if len(near)!=1:
                todo.append(dict(problem='分段后端点挂接不唯一',circuit_id=cid,endpoint=role,candidates=near))
                break
            pair_nodes.append(near[0])
        if len(pair_nodes)!=2:continue
        if pair_nodes[0]==pair_nodes[1]:
            todo.append(dict(problem='同节点不能证明零长回路',circuit_id=cid));continue
        length,alternatives=shortest(graph_edges,*pair_nodes,equal)
        if not alternatives:
            todo.append(dict(problem='桥架网络断开，未退直角距离',circuit_id=cid));continue
        if len(alternatives)>1:
            todo.append(dict(problem='多条等长路径，不默认挑选',circuit_id=cid,candidate_paths=[list(x)for x in alternatives]));continue
        sequence=list(alternatives[0]);c=ledger[cid]
        pending=['terminal_extra']
        if any(ends[r]['offset_m']>snap for r in ('from','to')):pending.append('off_tray_tail')
        row=dict(id=cid,kind='cable',physical_id='CABLE-'+cid,system=required(bindings[cid].get('system'),'circuit.system'),
            spec=c['fields']['spec'],parallel_count=c['parallel_count'],start_node=pair_nodes[0],end_node=pair_nodes[1],
            path_ids=sequence,channel_ids=sorted({part_of[s]['segment']['physical_id']for s in sequence}),
            path_length_each_m=length,length_m=length*c['parallel_count'],length_basis='network_model',
            quantity_level='规则推算量',scope_id=request.get('scope_id'),pending_components=pending,complete_quantity=False,
            source_ids=c['source_ids'],endpoint_evidence=ends,official_adoption=False)
        result.append(row)
        for sid in sequence:usage[sid].append(dict(circuit_id=cid,parallel_count=c['parallel_count']))
    for s in segments:
        ordered=[p for p in graph_edges if p['source_id']==s['segment_id']]
        if not ordered:continue
        channel_items.append(dict(id=s['physical_id'],kind='tray',physical_id=s['physical_id'],system='tray_network',spec=s['spec'],
            parallel_count=1,start_node=ordered[0]['start_node'],end_node=ordered[-1]['end_node'],
            path_ids=[x['segment_id']for x in ordered],length_m=s['length'],source_ids=s['source_ids'],length_basis='network_model',
            quantity_level='规则推算量',official_adoption=False))
    for cid in set(ledger)-set(bindings):todo.append(dict(problem='回路无已核端口/网络绑定',circuit_id=cid))
    todo.extend(dict(problem='只完成沿网分量，竖向/端头附加须另有依据',circuit_id=r['id'],pending_components=r['pending_components'])for r in result)
    return finish(e,dict(schema='qto-circuit-on-tray-result/1.0',channel_items=channel_items,circuit_items=result,
        physical_tray_length_m=math.fsum(r['length_m']for r in channel_items),
        cable_traversal_length_m=math.fsum(r['length_m']for r in result),shared_path_usage=dict(usage),
        graph=dict(schema=network['schema'],counts=network['counts'],nodes=nodes),
        path_ledger=dict(schema='qto-electrical-path-ledger/1.0',unit='m',project_id=required(request.get('project_id'),'project_id'),
                         paths=list(paths.values()),channels=channel_items,circuits=result),
        upstream=dict(tray=request['tray_candidates'],circuits=request['circuit_candidates']),
        meaning='仅已核回路沿确认桥架的network_model候选；桥架物理长只计一次，各根电缆遍历分别计，不作完整三维安装量'),todo)


if __name__=='__main__':
    raise SystemExit(cli(calculate,__doc__))
