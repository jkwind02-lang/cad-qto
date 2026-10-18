#!/usr/bin/env python3
"""Measure drawing-backed electrical paths; count shared channels separately."""
from __future__ import annotations
import argparse, json, math, sys
from collections import defaultdict
from pathlib import Path
SCHEMA='qto-electrical-path-ledger/1.0'
CHANNELS={'tray','trunking','conduit','busbar'}
CIRCUITS={'cable','wire'}
ROLES=('horizontal','vertical','offset')
BASES=('actual_drawn_path','network_model','elevation_difference','device_distance','assumption')   # strongest first
EXTRA_BASES=('project_rule','actual_drawn_path','network_model','device_distance','assumption')
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
    by_physical=defaultdict(list);out=[]
    for r in items:by_physical[r['physical_id']].append(r)
    for cid,c in claims.items():
        evidence(c)
        for field in ('physical_id','scope_id'):text(c.get(field),cid+'.'+field)
        if c.get('role') not in (*ROLES,'total'):raise ValueError('已证子量role须为horizontal/vertical/offset/total:'+cid)
        proven=number(c.get('proven_length_m'),cid+'.proven_length_m');tol=number(c.get('tolerance_m',0.001),cid+'.tolerance_m')
        candidates=by_physical.get(c['physical_id'],[])
        matching=[r for r in candidates if (r['scope_id'],r['path_scheme_id'])==(c['scope_id'],c.get('path_scheme_id'))]
        r=matching[0] if len(matching)==1 else (candidates[0] if len(candidates)==1 else None);role=c['role']
        row={'claim_id':cid,'physical_id':c['physical_id'],'scope_id':c['scope_id'],'path_scheme_id':c.get('path_scheme_id'),'role':role,
             'proven_length_m':proven,'item_id':r['id'] if r else None,'item_length_m':None,'status':'not_comparable'}
        if r is None:row['reason']='台账里没有这个对象' if not candidates else '存在互斥方案，须指定对应范围和路径方案'
        elif r['scope_id'] is None:row['reason']='本项没有声明范围scope_id'
        elif (r['scope_id'],r['path_scheme_id'])!=(c['scope_id'],c.get('path_scheme_id')):row['reason']='范围或路径方案不同，不套下界'
        elif role=='total' and not r['complete_quantity']:row['reason']='本项还有未决分量，不是完整量'
        elif role!='total' and r['length_components_each_m'].get(role) is None:row['reason']='本项这一向长度未决，不按0比'
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
    project=text(d.get('project_id'),'project_id');paths=rows(d.get('paths'),'paths');path_basis={};elevations={}
    for pid,r in paths.items():
        evidence(r);text(r.get('from_node'),pid+'.from_node');text(r.get('to_node'),pid+'.to_node')
        if r['from_node']==r['to_node']:raise ValueError('闭合路径须按实际节点分段:'+pid)
        if r.get('role') not in ROLES:raise ValueError('路径须区分horizontal/vertical/offset')
        if r.get('length_basis') is not None:
            if r['length_basis'] not in BASES:raise ValueError('路径依据类别length_basis须为'+'/'.join(BASES)+':'+pid)
            ids=r.get('basis_ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or not x.strip() for x in ids):raise ValueError('写了length_basis的路径须有依据号basis_ids:'+pid)
        path_basis[pid]=r.get('length_basis')
        if r.get('length_basis')=='elevation_difference':
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_qto-shared-evidence'/'scripts'))
            from qto_electrical_basis import evaluate_electrical_elevation
            elevations[pid]=evaluate_electrical_elevation(r,d)
            path_basis[pid]=elevations[pid]['effective_length_basis']
        if r.get('length_m') is not None or pid not in elevations:
            number(r.get('length_m'),pid+'.length_m')
            if r['length_m']<=0:raise ValueError('路径长度须为正:'+pid)
    channel_rows=rows(d.get('channels',[]),'channels');circuit_rows=rows(d.get('circuits',[]),'circuits')
    if set(channel_rows)&set(circuit_rows):raise ValueError('通道和回路的对象id不能重号')
    physical=defaultdict(list);items=[];usage=defaultdict(lambda:{'channel_ids':[],'circuit_ids':[]});channel_paths={};alternatives=defaultdict(list)
    for collection,allowed,usage_key in [(channel_rows,CHANNELS,'channel_ids'),(circuit_rows,CIRCUITS,'circuit_ids')]:
        for key,r in collection.items():
            evidence(r)
            if r.get('kind') not in allowed:raise ValueError('对象类型与通道/回路分组不符:'+key)
            for field in ('system','spec','start_node','end_node'):text(r.get(field),key+'.'+field)
            pid=text(r.get('physical_id'),key+'.physical_id')
            alternative=r.get('alternative_group_id')
            if alternative is not None:
                alternative=text(alternative,key+'.alternative_group_id')
                text(r.get('scope_id'),key+'.scope_id');text(r.get('path_scheme_id'),key+'.path_scheme_id')
                alternatives[alternative].append(key)
            if physical[pid] and (not alternative or any(x.get('alternative_group_id')!=alternative for x in physical[pid])):
                raise ValueError('同一实物重复计量；互斥方案须显式声明相同alternative_group_id:'+pid)
            physical[pid].append(r)
            count=r.get('parallel_count')
            if isinstance(count,bool) or not isinstance(count,int) or count<=0:raise ValueError('根数/物理通道数须为正整数')
            refs=r.get('path_ids')
            if not isinstance(refs,list) or not refs or any(not isinstance(x,str) or x not in paths for x in refs):raise ValueError('路径引用为空或缺失:'+key)
            if len(set(refs))!=len(refs):raise ValueError('同一路径在单个实物内重复，须明确往返/回路的实际分段:'+key)
            pending=r.get('pending_components',[])
            if not isinstance(pending,list) or any(not isinstance(x,str) or not x.strip() for x in pending):raise ValueError('pending_components须为文字数组:'+key)
            current=r['start_node'];parts={'horizontal':0.,'vertical':0.,'offset':0.};by_basis=defaultdict(float);missing=[];unresolved=[];unknown_roles=set()
            for path_id in refs:
                p=paths[path_id]
                if current==p['from_node']:current=p['to_node']
                elif current==p['to_node']:current=p['from_node']
                else:raise ValueError('路径序列不连续:'+key+'/'+path_id)
                if path_id in elevations and (p['system']!=r['system'] or p['scope_id']!=r.get('scope_id')):
                    raise ValueError('有据标高路径与采用对象的system/scope_id不一致:'+key+'/'+path_id)
                if p.get('length_m') is None:
                    unresolved.append(path_id);unknown_roles.add(p['role'])
                else:
                    parts[p['role']]+=p['length_m']
                    if path_basis[path_id] is not None:by_basis[path_basis[path_id]]+=p['length_m']
                usage[path_id][usage_key].append(key)
                if path_basis[path_id] is None:missing.append(path_id)
            if current!=r['end_node']:raise ValueError('路径终端与回路/通道终端不一致:'+key)
            extras=rows(r.get('extras',[]),key+'.extras');extra=0.;extra_by_basis=defaultdict(float);missing_extras=[]
            for xid,x in extras.items():
                evidence(x);text(x.get('basis'),xid+'.basis')
                if x.get('node_id') not in {r['start_node'],r['end_node']}:
                    raise ValueError('附加长度应定位到本项端头；中途预留须独立路径分段:'+xid)
                extra+=number(x.get('length_each_m'),xid+'.length_each_m')
                basis=x.get('length_basis')
                if basis is None:missing_extras.append(xid)
                else:
                    if basis not in EXTRA_BASES:raise ValueError('extras依据类别length_basis须为'+'/'.join(EXTRA_BASES)+':'+xid)
                    ids=x.get('basis_ids')
                    if not isinstance(ids,list) or not ids or any(not isinstance(v,str) or not v.strip() for v in ids):
                        raise ValueError('写了length_basis的extras须有依据号basis_ids:'+xid)
                    extra_by_basis[basis]+=x['length_each_m']
            if usage_key=='channel_ids':channel_paths[key]=set(refs)
            else:
                cids=r.get('channel_ids',[])
                if not isinstance(cids,list) or any(not isinstance(c,str) or c not in channel_paths for c in cids):raise ValueError('引用不存在的已计算通道:'+key)
                chosen_groups={}
                for cid in cids:
                    channel=channel_rows[cid];group=channel.get('alternative_group_id')
                    if group:
                        if group in chosen_groups and chosen_groups[group]!=cid:
                            raise ValueError('回路不能同时采用同组互斥通道:'+key)
                        chosen_groups[group]=cid
                        if r.get('path_scheme_id')!=channel.get('path_scheme_id'):
                            raise ValueError('回路与所选互斥通道的path_scheme_id须一致:'+key)
                if cids and not set(refs)<=set().union(*(channel_paths[c] for c in cids)):
                    raise ValueError('已声明通道未覆盖回路全部路径；脱离通道的支线应单列:'+key)
                cores=r.get('cable_core_count')
                if cores is not None and (isinstance(cores,bool) or not isinstance(cores,int) or cores<=0):raise ValueError('芯数必须为正整数且仅作规格属性')
            path_length=math.fsum(parts.values());known_total=count*(path_length+extra);total=None if unresolved else known_total
            if not math.isfinite(known_total):raise ValueError('计算结果溢出:'+key)
            known_bases=set(by_basis)|set(extra_by_basis)
            weak=bool(ESTIMATE_BASES&known_bases)
            level='方案暂估量' if weak else (None if missing_extras or unresolved else '规则推算量')
            complete=not pending and not unresolved
            for role in unknown_roles:parts[role]=None
            items.append({'id':key,'physical_id':pid,'kind':r['kind'],'system':r['system'],'spec':r['spec'],
                'path_ids':refs,'start_node':r['start_node'],'end_node':r['end_node'],'parallel_count':count,
                'length_components_each_m':parts,'extra_each_m':extra,'length_m':total,'unit':'m',
                'known_length_m':known_total,'calculation':None if unresolved else f'{count} * ({path_length} + {extra})','source_ids':r['source_ids'],
                'cable_core_count':r.get('cable_core_count'),'official_adoption':False,
                # Preserve old unclassified path semantics, but never silently
                # classify an extra whose newly structured basis is unknown.
                'quantity_level':level,'basis_classification_complete':not missing and not missing_extras and not unresolved,
                'length_basis':max(by_basis,key=BASES.index) if by_basis else None,
                'length_by_basis_each_m':{b:by_basis[b] for b in BASES if b in by_basis},'length_basis_missing_path_ids':missing,
                'extra_by_basis_each_m':{b:extra_by_basis[b] for b in EXTRA_BASES if b in extra_by_basis},
                'length_basis_missing_extra_ids':missing_extras,'unresolved_path_ids':unresolved,
                'roles_present':[x for x in ROLES if any(paths[i]['role']==x for i in refs)],
                'pending_components':list(dict.fromkeys(pending+['path:'+v for v in unresolved])),
                'complete_quantity':complete,'scope_id':r.get('scope_id'),'path_scheme_id':r.get('path_scheme_id')})
            # The weakest overall basis includes extras; a project rule is a
            # sourced extra rule and does not alter the geometric path basis.
            overall=[b for b in known_bases if b in BASES]
            items[-1]['quantity_basis']=max(overall,key=BASES.index) if overall else ('project_rule' if extra_by_basis else None)
            if missing_extras:items[-1]['quantity_basis']=None if not weak else items[-1]['quantity_basis']
            if alternative:items[-1]['alternative_group_id']=alternative
            if usage_key=='circuit_ids':items[-1]['logical_circuit_id']=r.get('logical_circuit_id')
    byid={r['id']:r for r in items};alternative_groups=[]
    for aid,keys in alternatives.items():
        members=[byid[k] for k in keys]
        context={(r['physical_id'],r['scope_id'],r['system'],r['kind'],r['spec'],r['parallel_count']) for r in members}
        if len(members)<2 or len(context)!=1 or len({r['path_scheme_id'] for r in members})!=len(members):
            raise ValueError('互斥组须为同实物同范围同规格的两个以上不同路径方案:'+aid)
        alternative_groups.append({'id':aid,'physical_id':members[0]['physical_id'],'scope_id':members[0]['scope_id'],
            'item_ids':keys,'path_scheme_ids':[r['path_scheme_id'] for r in members],'combination_policy':'mutually_exclusive'})
    logical=logical_circuits(circuit_rows,{r['id']:r for r in items})
    summaries=defaultdict(list)
    for r in items:summaries[(r['kind'],r['system'],r['spec'],r.get('alternative_group_id',''),r.get('path_scheme_id') if r.get('alternative_group_id') else '')].append(r)
    unused=sorted(set(paths)-set(usage))
    checks=scope_checks(rows(d.get('scope_claims',[]),'scope_claims'),items)
    conflicts=[c['claim_id'] for c in checks if c['status']=='scope_conflict']
    missing=sorted(pid for pid,p in paths.items() if p.get('length_basis') is None)
    levels=defaultdict(int);warnings=[{'warning':'length_basis_missing','path_ids':missing}] if missing else []
    for r in items:
        if r['quantity_level'] is not None:levels[r['quantity_level']]+=1
        if r['length_basis_missing_extra_ids']:warnings.append({'warning':'extra_length_basis_missing','item_id':r['id'],'extra_ids':r['length_basis_missing_extra_ids']})
    summary=[]
    for k,members in sorted(summaries.items()):
        row={'kind':k[0],'system':k[1],'spec':k[2],'unit':'m',
             'length_m':None if any(r['length_m'] is None for r in members) else math.fsum(r['length_m'] for r in members)}
        if any(r['length_m'] is None for r in members):row['known_length_m']=math.fsum(r['known_length_m'] for r in members)
        if k[3]:row.update(alternative_group_id=k[3],path_scheme_id=k[4],combination_policy='mutually_exclusive')
        summary.append(row)
    return {'schema':'qto-electrical-path-result/1.0','project_id':project,'status':'REVIEW_REQUIRED' if unused or conflicts else 'REFERENCE_ONLY',
        'quantity_level_counts':dict(levels),'unclassified_quantity_count':sum(r['quantity_level'] is None for r in items),'warnings':warnings,
        'scope_checks':checks,'scope_conflict_ids':conflicts,
        'items':items,'summary':summary,'alternative_groups':alternative_groups,'elevation_checks':elevations,
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
