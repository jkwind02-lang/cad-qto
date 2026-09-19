#!/usr/bin/env python3
"""Recalculate evidence-bound quantities and report transitive review invalidation.

No eval, executable commands, guessed rules, or automatic official adoption.
The graph stores real calculation inputs (dimensions/elevations/count factors),
not only a free-text list saying that one result depends on another.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
from collections import defaultdict, deque

SCHEMA='qto-calculation-graph/1.0'
UNIT={'1':(1.,()),'m':(1.,(('L',1),)),'mm':(.001,(('L',1),)),'cm':(.01,(('L',1),)),
      'm2':(1.,(('L',2),)),'m²':(1.,(('L',2),)),'mm2':(1e-6,(('L',2),)),
      'm3':(1.,(('L',3),)),'m³':(1.,(('L',3),)),'kg':(1.,(('M',1),)),'t':(1000.,(('M',1),)),
      'kg/m':(1.,(('L',-1),('M',1))),'kg/m3':(1.,(('L',-3),('M',1)))}
for u in ['个','台','套','樘','根']:UNIT[u]=(1.,(('C:'+u,1),))
RANK={'确认量':3,'规则推算量':2,'方案暂估量':1,'未决量':0}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def number(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(label+'须为有限数值')
    return float(value)


def ids(rows,label):
    if not isinstance(rows,list):raise ValueError(label+'须为数组')
    result={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'].strip() or row['id'] in result:
            raise ValueError(label+'编号缺失或重复')
        result[row['id']]=row
    return result


def dims(left,right,sign=1):
    out=dict(left)
    for k,v in right:out[k]=out.get(k,0)+sign*v
    return tuple(sorted((k,v) for k,v in out.items() if v))


def expression(text, variables):
    """Return (SI value, dimensions). Names are explicit dependency aliases."""
    if not isinstance(text,str) or len(text)>3000:raise ValueError('计算式过长或无效')
    tree=ast.parse(text,mode='eval')
    if len(list(ast.walk(tree)))>120:raise ValueError('计算式复杂度超限')
    used=set()
    def calc(n):
        if isinstance(n,ast.Expression):return calc(n.body)
        if isinstance(n,ast.Constant):return number(n.value,'常数'),()
        if isinstance(n,ast.Name):
            if n.id not in variables:raise ValueError('公式引用未声明变量:'+n.id)
            used.add(n.id);return variables[n.id]
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            v,d=calc(n.operand);return (v if isinstance(n.op,ast.UAdd) else -v),d
        if isinstance(n,ast.BinOp):
            a,da=calc(n.left);b,db=calc(n.right)
            if isinstance(n.op,(ast.Add,ast.Sub)):
                if da!=db:raise ValueError('不同量纲不能加减')
                return (a+b if isinstance(n.op,ast.Add) else a-b),da
            if isinstance(n.op,ast.Mult):return a*b,dims(da,db)
            if isinstance(n.op,ast.Div):
                if b==0:raise ValueError('除数为零')
                return a/b,dims(da,db,-1)
            if isinstance(n.op,ast.Pow):
                if db or b not in (0.,1.,2.,3.):raise ValueError('仅支持0至3的无量纲整数幂')
                return a**int(b),tuple((k,v*int(b)) for k,v in da if v*int(b))
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in ('abs','sqrt','ceil') and len(n.args)==1 and not n.keywords:
            v,d=calc(n.args[0])
            if n.func.id=='abs':return abs(v),d
            if n.func.id=='ceil':
                if d:raise ValueError('向上取整只能作用于无量纲间距/数量因子')
                return float(math.ceil(v)),d
            if v<0 or any(x%2 for _,x in d):raise ValueError('开方数值或量纲无效')
            return math.sqrt(v),tuple((k,x//2) for k,x in d if x//2)
        raise ValueError('公式含未允许的表达式')
    value,dimension=calc(tree)
    if used!=set(variables):raise ValueError('依赖变量与实际公式不一致，禁止挂名依赖')
    number(value,'计算值')
    return value,dimension


def recalculate(document,previous=None):
    if document.get('schema')!=SCHEMA:raise ValueError('依赖计算契约版本无效')
    project=document.get('project_id')
    if not isinstance(project,str) or not project.strip():raise ValueError('缺少project_id')
    if previous is not None and (previous.get('schema')!='qto-calculation-run/1.0' or previous.get('project_id')!=project):
        raise ValueError('旧计算记录属于其他项目或版本')
    sources=ids(document.get('sources'), '来源');rules=ids(document.get('rules'),'规则');nodes=ids(document.get('nodes'),'计算节点')
    for label,collection in [('来源',sources),('规则',rules)]:
        for k,r in collection.items():
            h=r.get('sha256')
            if not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h.lower()):raise ValueError(label+k+'缺少有效内容哈希')
    previous_rows=ids(previous.get('nodes',[]),'旧节点') if previous else {}
    order=[];degree={};children=defaultdict(list)
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
    if len(order)!=len(nodes):raise ValueError('计算依赖成环:'+','.join(k for k in nodes if degree[k]))
    evaluated={};output=[];changed=[];affected=[];stale=[];errors=[]
    for key in order:
        r=nodes[key];unit=r.get('unit')
        if unit not in UNIT:raise ValueError(key+'单位未支持或未显式换算:'+str(unit))
        factor,dimension=UNIT[unit];refs=r.get('variables',{});dependencies=[evaluated[k] for k in refs.values()]
        sids=r.get('source_ids',[]);rids=r.get('rule_ids',[])
        if not isinstance(sids,list) or not isinstance(rids,list) or any(k not in sources for k in sids) or any(k not in rules for k in rids):
            raise ValueError(key+'引用未登记来源或规则')
        calculated='expression' in r
        if calculated and not rids:raise ValueError(key+'计算节点缺少规则依据')
        if not calculated and not sids:raise ValueError(key+'事实节点缺少定位来源')
        if not calculated and refs:raise ValueError(key+'无计算式却声明依赖')
        own_level=r.get('quantity_level','规则推算量' if calculated else '确认量')
        if own_level not in RANK:raise ValueError(key+'数量层级无效')
        semantic={k:v for k,v in r.items() if k not in ('approval','title','comment','updated_at')}
        own_fingerprint=digest({'node':semantic,'sources':{k:sources[k] for k in sids},'rules':{k:rules[k] for k in rids}})
        fingerprint=digest({'own':own_fingerprint,'dependencies':{a:evaluated[k]['fingerprint'] for a,k in refs.items()}})
        old=previous_rows.get(key);value=None;status='CALCULATED';reason=None
        try:
            if calculated:
                # Validate formula and dimensions even while values are unknown.
                checked={a:(evaluated[k]['si_value'] if evaluated[k]['si_value'] is not None else 1.,evaluated[k]['dimensions']) for a,k in refs.items()}
                test,dim=expression(r['expression'],checked)
                if dim!=dimension:raise ValueError('公式量纲与结果单位不一致')
                if any(n['value'] is None for n in dependencies):status='BLOCKED';reason='上游事实未确定'
                elif any(rules[k].get('status')=='disabled' for k in rids):status='BLOCKED';reason='所用规则已停用'
                else:value=test/factor
            else:
                v=r.get('value')
                if v is None:status='BLOCKED';reason='事实尚未确定'
                else:value=number(v,key+'.value')
            if value is not None and calculated and value<0 and r.get('allow_negative') is not True:raise ValueError('派生数量为负，请核对扣减或明确允许有符号结果')
        except (ValueError,ZeroDivisionError,OverflowError,SyntaxError) as exc:
            status='BLOCKED';reason=str(exc);errors.append({'node_id':key,'error':reason})
        level=min([own_level]+[n['quantity_level'] for n in dependencies],key=RANK.get) if value is not None else '未决量'
        if old is None or old.get('own_fingerprint')!=own_fingerprint:changed.append(key)
        if old is None or old.get('fingerprint')!=fingerprint:affected.append(key)
        approval=r.get('approval') or {}
        review='unreviewed'
        if approval:
            if approval.get('fingerprint')==fingerprint and approval.get('actor') not in (None,'model','codex','assistant','ai') and value is not None:
                review='current'
            else:review='stale';stale.append(key)
        elif old and old.get('review_state')=='current':
            if old.get('fingerprint')==fingerprint and value is not None:review='current'
            else:review='stale';stale.append(key)
        row={'id':key,'unit':unit,'value':value,'status':status,'reason':reason,'quantity_level':level,
             'expression':r.get('expression'),'variables':refs,'dependency_values':{a:evaluated[k]['value'] for a,k in refs.items()},
             'source_ids':sids,'rule_ids':rids,'own_fingerprint':own_fingerprint,'fingerprint':fingerprint,'review_state':review,
             'official_adoption':False}
        evaluated[key]={**row,'si_value':None if value is None else value*factor,'dimensions':dimension};output.append(row)
    return {'schema':'qto-calculation-run/1.0','project_id':project,'input_fingerprint':digest(document),
            'status':'REVIEW_REQUIRED' if any(r['status']=='BLOCKED' for r in output) or stale else 'CALCULATED',
            'nodes':output,'changed_inputs_or_rules':changed,'affected_nodes':affected,'stale_reviews':stale,'errors':errors,
            'removed_nodes':sorted(set(previous_rows)-set(nodes)),'evaluation_order':order,'computed_node_count':len(order),
            'business_adoption_allowed':False,'meaning':'按完整依赖重新计算并标识变化影响；不自动识别图纸变化，不自动升级正式采用'}


def markdown(run):
    lines=['# 工程量依赖重算核对','','状态：'+run['status'],'','| 成果/参数 | 当前值 | 单位 | 层级 | 复核状态 |','|---|---:|---|---|---|']
    for r in run['nodes']:
        esc=lambda x:str(x).replace('|','\\|').replace('\n',' ')
        lines.append('| '+ ' | '.join(esc(x) for x in (r['id'],'待确定' if r['value'] is None else format(r['value'],'.12g'),r['unit'],r['quantity_level'],r['review_state']))+' |')
    lines+=['','受影响节点：'+', '.join(run['affected_nodes']),'旧确认失效：'+', '.join(run['stale_reviews']),
            '','不同单位分别核对；未确定不按零计。结果不代表已获正式采用授权。']
    return '\n'.join(lines)+'\n'



def check_result_dependencies(data):
    """Detect missing references, cycles and transitive release violations."""
    items=data.get('工程量结果',[])
    byid={r.get('成果项编号'):r for r in items if isinstance(r,dict)}
    errors=[];degree={};children=defaultdict(list)
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
    return errors

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--previous',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:
        targets=[a.output,a.output.with_suffix('.md')]
        if any(t.exists() for t in targets):raise ValueError('输出已存在，请使用新版本文件名')
        data=json.loads(a.input.read_text(encoding='utf-8-sig'));old=json.loads(a.previous.read_text(encoding='utf-8-sig')) if a.previous else None
        run=recalculate(data,old);a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(run,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        a.output.with_suffix('.md').write_text(markdown(run),encoding='utf-8')
        print(json.dumps({'status':run['status'],'affected':len(run['affected_nodes']),'stale_reviews':len(run['stale_reviews']),'output':str(a.output)},ensure_ascii=False));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'INPUT_REQUIRED','error':str(exc)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
