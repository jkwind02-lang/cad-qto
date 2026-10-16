#!/usr/bin/env python3
"""Measure drawing-backed electrical paths; count shared channels separately."""
from __future__ import annotations
import argparse, json, math
from collections import defaultdict
from pathlib import Path
SCHEMA='qto-electrical-path-ledger/1.0'
CHANNELS={'tray','trunking','conduit','busbar'}
CIRCUITS={'cable','wire'}
ROLES=('horizontal','vertical','offset')
BASES=('actual_drawn_path','network_model','device_distance','assumption')   # strongest first
ESTIMATE_BASES={'device_distance','assumption'}   # any part on these makes the whole item a scheme estimate
def number(v,label):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:
        raise ValueError(label+'必须为有限非负数')
    return float(v)
def text(v,label):
    if not isinstance(v,str) or not v.strip():raise ValueError(label+'缺少非空文字')
    return v.strip()
def evidence(r):
    refs=r.get('source_ids')
    if not isinstance(refs,list) or not refs or any(not isinstance(x,str) or not x.strip() for x in refs):raise ValueError('对象缺少图纸/项目依据source_ids')
    return refs
def rows(values,label):
    if not isinstance(values,list):raise ValueError(label+'须为数组')
    result={}
    for r in values:
        if not isinstance(r,dict):raise ValueError(label+'行须为对象')
        key=text(r.get('id'),label+'.id')
        if key in result:raise ValueError(label+'编号重复:'+key)
        result[key]=r
    return result
def logical_circuits(circuit_rows,byid):
    """Segments of one logical circuit (e.g. cable to the first lamp, then wire) chain head to tail without overlap."""
    groups=defaultdict(list)
    for key,r in circuit_rows.items():
        if r.get('logical_circuit_id') is not None:groups[text(r['logical_circuit_id'],key+'.logical_circuit_id')].append(key)
    out=[]
    for lid,keys in sorted(groups.items()):
        segs={k:circuit_rows[k] for k in keys}
        if len({s['system'] for s in segs.values()})>1:raise ValueError('同一逻辑回路的各段系统不一致:'+lid)
        starts=defaultdict(list);ends=defaultdict(list)
        for k,s in segs.items():starts[s['start_node']].append(k);ends[s['end_node']].append(k)
        if any(len(v)>1 for v in [*starts.values(),*ends.values()]):raise ValueError('同一逻辑回路有分叉或重复段，分支须另立逻辑回路:'+lid)
        heads=[k for k,s in segs.items() if s['start_node'] not in ends]
        order=heads[:1]
        while order and segs[order[-1]]['end_node'] in starts and len(order)<=len(segs):order.append(starts[segs[order[-1]]['end_node']][0])
        if len(heads)!=1 or len(order)!=len(segs):raise ValueError('同一逻辑回路的各段须首尾相接成一条:'+lid)
        seen=set()
        for k in order:
            if seen&set(segs[k]['path_ids']):raise ValueError('同一逻辑回路的各段路径重叠:'+lid+'/'+k)
            seen|=set(segs[k]['path_ids'])
        out.append({'logical_circuit_id':lid,'segment_ids':order,'start_node':segs[order[0]]['start_node'],'end_node':segs[order[-1]]['end_node'],
            'segments':[{'id':k,'kind':byid[k]['kind'],'spec':byid[k]['spec'],'parallel_count':byid[k]['parallel_count'],'length_m':byid[k]['length_m']} for k in order]})
    return out
def scope_checks(claims,items):
    """A proven part of one object in one scope and path scheme is a lower bound only for the same object, scope and scheme.

    A complete quantity below it is a conflict; another scope or scheme is not comparable (no lower bound is put on it); an
    unknown vertical part is not taken as 0 (no vertical path, or listed pending), nor a total with pending parts as complete."""
    by_physical={r['physical_id']:r for r in items};out=[]
    for cid,c in claims.items():
        evidence(c)
        for field in ('physical_id','scope_id'):text(c.get(field),cid+'.'+field)
        if c.get('role') not in (*ROLES,'total'):raise ValueError('已证子量role须为horizontal/vertical/offset/total:'+cid)
        proven=number(c.get('proven_length_m'),cid+'.proven_length_m');tol=number(c.get('tolerance_m',0.001),cid+'.tolerance_m')
        r=by_physical.get(c['physical_id']);role=c['role']
        row={'claim_id':cid,'physical_id':c['physical_id'],'scope_id':c['scope_id'],'path_scheme_id':c.get('path_scheme_id'),'role':role,
             'proven_length_m':proven,'item_id':r['id'] if r else None,'item_length_m':None,'status':'not_comparable'}
        if r is None:row['reason']='台账里没有这个对象'
        elif r['scope_id'] is None:row['reason']='本项没有声明范围scope_id'
        elif (r['scope_id'],r['path_scheme_id'])!=(c['scope_id'],c.get('path_scheme_id')):row['reason']='范围或路径方案不同，不套下界'
        elif role=='total' and r['pending_components']:row['reason']='本项还有未决分量，不是完整量'
        elif role in r['pending_components']:row['reason']='本项这一向列为未决，不按0比'
        elif role!='total' and role not in r['roles_present']:row['reason']='本项没有这一向的路径，不按0比'
        else:
            value=r['length_m'] if role=='total' else r['parallel_count']*r['length_components_each_m'][role]
            row.update(item_length_m=value,status='scope_conflict' if value<proven-tol else 'consistent')
            if row['status']=='scope_conflict':r.setdefault('scope_conflict_claim_ids',[]).append(cid)
        out.append(row)
    return out
def calculate(d):
    if d.get('schema')!=SCHEMA or d.get('unit')!='m':raise ValueError('路径契约或单位错误；先明确换算为m')
    project=text(d.get('project_id'),'project_id');paths=rows(d.get('paths'),'paths')
    for pid,r in paths.items():
        evidence(r);text(r.get('from_node'),pid+'.from_node');text(r.get('to_node'),pid+'.to_node')
        if r['from_node']==r['to_node']:raise ValueError('闭合路径须按实际节点分段:'+pid)
        number(r.get('length_m'),pid+'.length_m')
        if r['length_m']<=0:raise ValueError('路径长度须为正:'+pid)
        if r.get('role') not in ROLES:raise ValueError('路径须区分horizontal/vertical/offset')
        if r.get('length_basis') is not None:
            if r['length_basis'] not in BASES:raise ValueError('路径依据类别length_basis须为'+'/'.join(BASES)+':'+pid)
            ids=r.get('basis_ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or not x.strip() for x in ids):raise ValueError('写了length_basis的路径须有依据号basis_ids:'+pid)
    channel_rows=rows(d.get('channels',[]),'channels');circuit_rows=rows(d.get('circuits',[]),'circuits')
    physical=set();items=[];usage=defaultdict(lambda:{'channel_ids':[],'circuit_ids':[]});channel_paths={}
    for collection,allowed,usage_key in [(channel_rows,CHANNELS,'channel_ids'),(circuit_rows,CIRCUITS,'circuit_ids')]:
        for key,r in collection.items():
            evidence(r)
            if r.get('kind') not in allowed:raise ValueError('对象类型与通道/回路分组不符:'+key)
            for field in ('system','spec','start_node','end_node'):text(r.get(field),key+'.'+field)
            pid=text(r.get('physical_id'),key+'.physical_id')
            if pid in physical:raise ValueError('同一实物重复计量:'+pid)
            physical.add(pid)
            count=r.get('parallel_count')
            if isinstance(count,bool) or not isinstance(count,int) or count<=0:raise ValueError('根数/物理通道数须为正整数')
            refs=r.get('path_ids')
            if not isinstance(refs,list) or not refs or any(not isinstance(x,str) or x not in paths for x in refs):raise ValueError('路径引用为空或缺失:'+key)
            if len(set(refs))!=len(refs):raise ValueError('同一路径在单个实物内重复，须明确往返/回路的实际分段:'+key)
            pending=r.get('pending_components',[])
            if not isinstance(pending,list) or any(not isinstance(x,str) or not x.strip() for x in pending):raise ValueError('pending_components须为文字数组:'+key)
            current=r['start_node'];parts={'horizontal':0.,'vertical':0.,'offset':0.};by_basis=defaultdict(float);missing=[]
            for path_id in refs:
                p=paths[path_id]
                if current==p['from_node']:current=p['to_node']
                elif current==p['to_node']:current=p['from_node']
                else:raise ValueError('路径序列不连续:'+key+'/'+path_id)
                parts[p['role']]+=p['length_m'];usage[path_id][usage_key].append(key)
                if p.get('length_basis') is None:missing.append(path_id)
                else:by_basis[p['length_basis']]+=p['length_m']
            if current!=r['end_node']:raise ValueError('路径终端与回路/通道终端不一致:'+key)
            extras=rows(r.get('extras',[]),key+'.extras');extra=0.
            for xid,x in extras.items():
                evidence(x);text(x.get('basis'),xid+'.basis')
                if x.get('node_id') not in {r['start_node'],r['end_node']}:
                    raise ValueError('附加长度应定位到本项端头；中途预留须独立路径分段:'+xid)
                extra+=number(x.get('length_each_m'),xid+'.length_each_m')
            if usage_key=='channel_ids':channel_paths[key]=set(refs)
            else:
                cids=r.get('channel_ids',[])
                if not isinstance(cids,list) or any(not isinstance(c,str) or c not in channel_paths for c in cids):raise ValueError('引用不存在的已计算通道:'+key)
                if cids and not set(refs)<=set().union(*(channel_paths[c] for c in cids)):
                    raise ValueError('已声明通道未覆盖回路全部路径；脱离通道的支线应单列:'+key)
                cores=r.get('cable_core_count')
                if cores is not None and (isinstance(cores,bool) or not isinstance(cores,int) or cores<=0):raise ValueError('芯数必须为正整数且仅作规格属性')
            path_length=math.fsum(parts.values());total=count*(path_length+extra)
            if not math.isfinite(total):raise ValueError('计算结果溢出:'+key)
            items.append({'id':key,'physical_id':pid,'kind':r['kind'],'system':r['system'],'spec':r['spec'],
                'path_ids':refs,'start_node':r['start_node'],'end_node':r['end_node'],'parallel_count':count,
                'length_components_each_m':parts,'extra_each_m':extra,'length_m':total,'unit':'m',
                'calculation':f'{count} * ({path_length} + {extra})','source_ids':r['source_ids'],
                'cable_core_count':r.get('cable_core_count'),'official_adoption':False,
                # the level follows the weakest known basis of its paths; only the label changes, never the number
                'quantity_level':'方案暂估量' if ESTIMATE_BASES&set(by_basis) else '规则推算量',
                'length_basis':max(by_basis,key=BASES.index) if by_basis else None,
                'length_by_basis_each_m':{b:by_basis[b] for b in BASES if b in by_basis},'length_basis_missing_path_ids':missing,
                'roles_present':[x for x in ROLES if any(paths[i]['role']==x for i in refs)],
                'pending_components':pending,'complete_quantity':not pending,'scope_id':r.get('scope_id'),'path_scheme_id':r.get('path_scheme_id')})
            if usage_key=='circuit_ids':items[-1]['logical_circuit_id']=r.get('logical_circuit_id')
    logical=logical_circuits(circuit_rows,{r['id']:r for r in items})
    summaries=defaultdict(list)
    for r in items:summaries[(r['kind'],r['system'],r['spec'])].append(r['length_m'])
    unused=sorted(set(paths)-set(usage))
    checks=scope_checks(rows(d.get('scope_claims',[]),'scope_claims'),items)
    conflicts=[c['claim_id'] for c in checks if c['status']=='scope_conflict']
    missing=sorted(pid for pid,p in paths.items() if p.get('length_basis') is None)
    levels=defaultdict(int)
    for r in items:levels[r['quantity_level']]+=1
    return {'schema':'qto-electrical-path-result/1.0','project_id':project,'status':'REVIEW_REQUIRED' if unused or conflicts else 'REFERENCE_ONLY',
        'quantity_level_counts':dict(levels),'warnings':[{'warning':'length_basis_missing','path_ids':missing}] if missing else [],
        'scope_checks':checks,'scope_conflict_ids':conflicts,
        'items':items,'summary':[{'kind':k[0],'system':k[1],'spec':k[2],'unit':'m','length_m':math.fsum(v)} for k,v in sorted(summaries.items())],
        'shared_path_usage':dict(usage),'unused_path_ids':unused,'logical_circuits':logical,'business_adoption_allowed':False,
        'meaning':'已识别路径的参考复算；通道与回路分别计量，芯数不乘入电缆长度；未使用路径需模型解释归属，未证明原图完整'}
def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    try:
        result=calculate(json.loads(a.input.read_text(encoding='utf-8-sig')))
        content=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if a.output:
            if a.output.exists():raise ValueError('输出已存在，请使用新版本文件名')
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(content,encoding='utf-8')
        print(content);return 0
    except (OSError,ValueError,KeyError,TypeError,OverflowError) as exc:
        print(json.dumps({'status':'INPUT_REQUIRED','error':str(exc)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
