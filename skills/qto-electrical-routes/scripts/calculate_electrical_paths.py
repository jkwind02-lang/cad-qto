#!/usr/bin/env python3
"""Measure drawing-backed electrical paths; count shared channels separately."""
from __future__ import annotations
import argparse, json, math
import sys
from collections import defaultdict
from pathlib import Path
SCHEMA='qto-electrical-path-ledger/1.0'
sys.path.insert(0,str(Path(__file__).resolve().parent))
from electrical_endpoint_receipt import receive
CHANNELS={'tray','trunking','conduit','busbar'}
CIRCUITS={'cable','wire'}
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
def calculate(d,base_dir='.'):
    if d.get('schema')!=SCHEMA or d.get('unit')!='m':raise ValueError('路径契约或单位错误；先明确换算为m')
    project=text(d.get('project_id'),'project_id');paths=rows(d.get('paths'),'paths')
    for pid,r in paths.items():
        evidence(r);text(r.get('from_node'),pid+'.from_node');text(r.get('to_node'),pid+'.to_node')
        if r['from_node']==r['to_node']:raise ValueError('闭合路径须按实际节点分段:'+pid)
        number(r.get('length_m'),pid+'.length_m')
        if r['length_m']<=0:raise ValueError('路径长度须为正:'+pid)
        if r.get('role') not in ('horizontal','vertical','offset'):raise ValueError('路径须区分horizontal/vertical/offset')
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
            current=r['start_node'];parts={'horizontal':0.,'vertical':0.,'offset':0.}
            for path_id in refs:
                p=paths[path_id]
                if current==p['from_node']:current=p['to_node']
                elif current==p['to_node']:current=p['from_node']
                else:raise ValueError('路径序列不连续:'+key+'/'+path_id)
                parts[p['role']]+=p['length_m'];usage[path_id][usage_key].append(key)
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
                'cable_core_count':r.get('cable_core_count'),'quantity_level':'规则推算量','official_adoption':False})
    summaries=defaultdict(list)
    for r in items:summaries[(r['kind'],r['system'],r['spec'])].append(r['length_m'])
    unused=sorted(set(paths)-set(usage))
    receipt=receive(d,paths,items,base_dir)
    needs_review=bool(unused) or (receipt is not None and not receipt['identity_receipt_complete'])
    result={'schema':'qto-electrical-path-result/1.0','project_id':project,'status':'REVIEW_REQUIRED' if needs_review else 'REFERENCE_ONLY',
        'items':items,'summary':[{'kind':k[0],'system':k[1],'spec':k[2],'unit':'m','length_m':math.fsum(v)} for k,v in sorted(summaries.items())],
        'shared_path_usage':dict(usage),'unused_path_ids':unused,'business_adoption_allowed':False,
        'meaning':'已识别路径的参考复算；通道与回路分别计量，芯数不乘入电缆长度；未使用路径需模型解释归属，未证明原图完整'}
    if receipt is not None:result['endpoint_receipt']=receipt
    return result
def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    try:
        result=calculate(json.loads(a.input.read_text(encoding='utf-8-sig')),a.input.resolve().parent)
        content=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if a.output:
            if a.output.exists():raise ValueError('输出已存在，请使用新版本文件名')
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(content,encoding='utf-8')
        print(content);return 0
    except (OSError,ValueError,KeyError,TypeError,OverflowError) as exc:
        print(json.dumps({'status':'INPUT_REQUIRED','error':str(exc)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
