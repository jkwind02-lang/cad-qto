#!/usr/bin/env python3
"""Build source-bound duct inputs and method-independent exact-instance coverage."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import subprocess
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'qto-pipe-networks/scripts'))
import build_pipe_inference_input as adapter
import qto_cad_geometry as cg
from qto_cad_source_contract import unit_known, canonical_scale
from duct_network_extensions import checked_geometry
from bind_duct_vertical_annotations import write_protected_json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'qto-components-points/scripts'))
from point_host_attachment import reject_reparse_chain

SCHEMA='qto-duct-network-profile/1.0'

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value): return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def text(value,label):
    if not isinstance(value,str) or not value.strip(): raise ValueError(label+'必须是非空文本')
    return value

def ref(path): return {'path':str(Path(path).resolve()),'sha256':sha(path)}
def read_ref(value,base):
    p=Path(value['path']); p=(base/p).resolve() if not p.is_absolute() else p.resolve()
    if sha(p)!=str(value.get('sha256','')).lower(): raise ValueError('冻结证据SHA不符:'+str(p))
    return json.loads(p.read_text(encoding='utf-8-sig')),ref(p)
def quantity_number(value,label,*,positive=False):
    try:finite=not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)
    except (OverflowError,ValueError):finite=False
    if not finite or value<0 or (positive and value<=0):
        raise ValueError(label+'必须是非bool的有限'+('正数' if positive else '非负数'))
    return float(value)


def verified_source_ref(value,base,protected):
    p=Path(value['path']);p=(base/p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_file() or sha(p)!=str(value.get('sha256','')).lower():raise ValueError('计量原始依据文件缺失或SHA变化')
    protected.append(p)
    return ref(p)


def external_length_measurement(actual,claim,source_row,unit,source_ref,quantity_ref,base,protected,source,layout,space):
    """Coverage is backed by an attributed current measurement, not a non-null cell.

    The CAD part supplies gross centreline coverage; net length has separately
    frozen deductions. Aggregate rows may contain other sources, but the present
    part's contribution and the aggregate conservation are checked independently.
    """
    if not isinstance(actual,dict):raise ValueError('外部数量行必须是对象')
    amount=quantity_number(actual.get('length'),'外部实际length',positive=True)
    if 'source_sha256' in actual and actual['source_sha256']!=source:raise ValueError('外部数量行声明了其他版本的原图SHA')
    ids=actual.get('source_ids')
    if (not isinstance(ids,list) or not ids or any(not isinstance(k,str) or not k for k in ids)
            or len(ids)!=len(set(ids)) or source_row['source_key'] not in ids):
        raise ValueError('实际数量行未绑定唯一完整源实例，缓冲范围不能证明已计')
    quantity_unit=text(actual.get('unit'),'外部数量unit')
    raw=unit=='raw_DU'
    if raw:
        if quantity_unit!='raw_DU':raise ValueError('未知源单位不能借外部物理单位倒推比例；只能核对raw_DU几何长度')
        dimension,kind,scale='drawing_length','duct_centerline_drawing_length',1.
    else:
        dimension,kind='length','duct_centerline_length'
        scale=canonical_scale(unit)/canonical_scale(quantity_unit)
    basis=actual.get('length_basis')
    if basis not in {'gross_centerline','net_centerline'}:raise ValueError('外部数量必须明确毛中心线或净中心线长度口径')
    expected={'quantity_dimension':dimension,'quantity_kind':kind,'unit':quantity_unit,'length_basis':basis}
    if any(actual.get(k)!=v or claim.get(k)!=v for k,v in expected.items()):
        raise ValueError('外部数量维度/单位/声明口径不一致')
    if actual.get('measurement_status') not in {'measured','calculated'} or actual.get('currentness_status')!='current':
        raise ValueError('外部数量未测量/待核/失效或不是当前结果，不能认已计')
    for key in ('status','quantity_status'):
        if key in actual and actual[key] not in {'measured','calculated','current','working'}:
            raise ValueError('外部数量状态与当前可用计量状态冲突')
    measurement_ref=claim.get('measurement_evidence')
    if not isinstance(measurement_ref,dict) or not measurement_ref.get('path') or not measurement_ref.get('sha256'):raise ValueError('外部已计声明缺少冻结计量依据path/sha256')
    proof,proof_ref=read_ref(measurement_ref,base);protected.append(Path(proof_ref['path']))
    if not isinstance(proof,dict):raise ValueError('冻结计量依据必须是对象')
    binding={'schema_version':'qto-duct-external-length-measurement/1.0','source_sha256':source,
             'source_key':source_row['source_key'],'geometry_sha256':source_row['geometry_sha256'],
             'layout':layout,'space_id':space,'source_coverage':'full_source_part',
             **expected,'measurement_status':actual['measurement_status'],'currentness_status':'current'}
    if any(proof.get(k)!=v for k,v in binding.items()) or not proof.get('basis') or not proof.get('source_locator'):
        raise ValueError('外部计量依据未绑定当前源part、完整覆盖、状态及口径')
    expected_row={**quantity_ref,'collection':claim['collection'],'index':claim['index'],'row_sha256':digest(actual)}
    supplied_row=proof.get('quantity_row')
    if not isinstance(supplied_row,dict):raise ValueError('计量依据缺实际数量行定位及指纹')
    resolved=verified_source_ref(supplied_row,Path(proof_ref['path']).parent,protected)
    if {**resolved,**{k:supplied_row.get(k) for k in ('collection','index','row_sha256')}}!=expected_row:
        raise ValueError('计量依据的实际数量文件/行/指纹不一致')
    cited=verified_source_ref(proof.get('source_evidence',{}),Path(proof_ref['path']).parent,protected)
    authoritative=verified_source_ref(source_ref,base,protected)
    if cited!=authoritative:raise ValueError('计量依据必须引用当前权威原图，不以外部单位认定源比例')
    gross=quantity_number(proof.get('gross_length'),'有据毛中心线长度',positive=True)
    attributed=quantity_number(proof.get('attributed_length'),'本源part实际计量贡献',positive=True)
    cad_length=quantity_number(quantity_number(source_row.get('raw_geometry_length'),'完整源几何长度',positive=True)*scale,'换算后的完整源几何长度',positive=True)
    rounding=quantity_number(proof.get('rounding_tolerance',0),'计量舍入容差')
    if rounding>cad_length*1e-4:raise ValueError('计量舍入容差过大，不能掩盖口径或数值不符')
    tolerance=rounding+max(1e-12,abs(cad_length)*1e-10)
    if abs(gross-cad_length)>tolerance:raise ValueError('本源part毛中心线计量依据与完整源几何/已核单位不符')
    deductions=proof.get('deductions')
    if not isinstance(deductions,list) or (basis=='gross_centerline' and deductions):
        raise ValueError('毛长不能夹带扣减；净长扣减须逐项列明')
    seen=set();deducted=0.;deduction_refs=[]
    for entry in deductions:
        value,record_ref=read_ref(entry,Path(proof_ref['path']).parent);protected.append(Path(record_ref['path']))
        if not isinstance(value,dict):raise ValueError('冻结扣减依据必须是对象')
        context={'schema_version':'qto-duct-length-deduction/1.0','source_sha256':source,'source_key':source_row['source_key'],
                 'geometry_sha256':source_row['geometry_sha256'],'layout':layout,'space_id':space}
        if any(value.get(k)!=v for k,v in context.items()) or not value.get('basis') or not value.get('source_locator'):
            raise ValueError('扣减依据缺少本源part范围、原因或定位')
        did=text(value.get('deduction_id'),'deduction_id')
        if did in seen or record_ref['sha256'] in seen:raise ValueError('同一扣减不得重复引用')
        seen.update((did,record_ref['sha256']))
        dunit=text(value.get('unit'),'扣减unit')
        if raw and dunit!='raw_DU':raise ValueError('未知源比例的raw_DU扣减不能混用物理单位')
        dscale=1. if raw else canonical_scale(dunit)/canonical_scale(quantity_unit)
        deducted=quantity_number(deducted+quantity_number(value.get('length'),'扣减长度',positive=True)*dscale,'换算扣减合计')
        basis_ref=verified_source_ref(value.get('basis_source',{}),Path(record_ref['path']).parent,protected)
        locator=(basis_ref['path'],basis_ref['sha256'],value['source_locator'])
        if locator in seen:raise ValueError('同一原始扣减条款/位置不得换ID重复扣减')
        seen.add(locator);deduction_refs.append({**record_ref,'basis_source':basis_ref})
    net=gross-deducted
    if net<=0 or abs(attributed-net)>tolerance:raise ValueError('本源part毛长、净量与有据扣减不守恒；零/负去向须另作有据排除')
    if attributed>amount+tolerance:raise ValueError('本源part计量贡献大于实际数量行')
    return {'proof':proof_ref,'quantity_row':expected_row,'source_evidence':cited,'quantity_dimension':dimension,
            'unit':quantity_unit,'length_basis':basis,'gross_length':gross,'deducted_length':deducted,
            'attributed_length':attributed,'row_length':amount,'row_source_ids':ids,'tolerance':tolerance,
            'deduction_evidence':deduction_refs,'physical_scale_verified':not raw,'business_adoption_allowed':False}


def check_external_groups(groups):
    for group in groups.values():
        try:total=math.fsum(v['attributed_length'] for v in group)
        except OverflowError as exc:raise ValueError('外部源part贡献合计溢出') from exc
        first=group[0];tolerance=max(v['tolerance'] for v in group)
        covered={v['quantity_source_key'] for v in group}
        if total>first['row_length']+tolerance or (covered==set(first['row_source_ids']) and abs(total-first['row_length'])>tolerance):
            raise ValueError('外部数量行与已声明源part贡献不守恒，不能用聚合行吞掉未计源part')

def source_key(row):
    return (row['instance_path']+'/'+row.get('source_definition_record_id',row['record_id']) if row.get('instance_path') else row['record_id'])
def point(part,end=False):
    return part['end' if end else 'start'] if part['kind']=='line' else cg.conic_point(part,part['start_param']+(part['sweep_param'] if end else 0))
def geometry(part): return {'schema':cg.GEOMETRY_SCHEMA,'coordinate_system':'WCS','parts':[part],'closed':False}

def match(selector,row,key):
    return ((not selector.get('source_keys') or key in selector['source_keys'])
       and re.search(selector.get('layer_pattern','.*'),str(row.get('layer',''))) is not None
       and (not selector.get('entity_types') or row.get('entity_type') in selector['entity_types']))

def scope_state(box,crop):
    if crop is None: return 'inside'
    if box is None: return 'unlocated'
    x1,y1,x2,y2=crop
    if box[2]<x1 or box[0]>x2 or box[3]<y1 or box[1]>y2: return 'outside'
    return 'inside' if x1<=box[0] and box[2]<=x2 and y1<=box[1] and box[3]<=y2 else 'crossing'

def section_values(section,unit):
    shape=section.get('shape')
    size_unit=section.get('unit')
    factor=canonical_scale(size_unit)/canonical_scale(unit) if unit_known(unit) and unit_known(size_unit) else None
    keys=('width','height') if shape=='rectangle' else ('diameter',) if shape=='circle' else ()
    if not keys: raise ValueError('截面shape必须为rectangle/circle')
    for key in keys:
        value=section.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0: raise ValueError('截面尺寸无效')
    return {key:section[key]*factor for key in keys} if factor is not None else {}, factor

def bind_tag(rule,evidence,by_key,unit):
    """Explicit source/tag relationship only; no nearest annotation adjudication."""
    tag=rule.get('annotation_binding')
    if not tag: return copy.deepcopy(rule.get('section')),None
    key=tag.get('source_key'); row=by_key.get(key)
    if row is None or row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}: raise ValueError('引注源实例缺失')
    actual=str(row.get('text',''))
    if actual!=tag.get('expected_text'): raise ValueError('引注内容与冻结识别配置不符')
    relationships=evidence.get('annotation_relationships',[])
    if not any(r.get('tag_source_key')==key and r.get('selector')==rule.get('selector') and r.get('basis') and r.get('source_locator') for r in relationships):
        raise ValueError('引注与管段缺少明确连接/模型判读关系，不能按最近标签定案')
    rect=re.search(r'(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)',actual)
    circle=re.search(r'[ΦφØøDd]\s*(\d+(?:\.\d+)?)',actual)
    if rect: section={'shape':'rectangle','width':float(rect[1]),'height':float(rect[2]),'unit':tag['dimension_unit']}
    elif circle: section={'shape':'circle','diameter':float(circle[1]),'unit':tag['dimension_unit']}
    else: raise ValueError('引注没有唯一可解析截面')
    elevation=None
    if tag.get('elevation_regex'):
        found=list(re.finditer(tag['elevation_regex'],actual))
        if len(found)!=1: raise ValueError('标高引注匹配不唯一')
        val=float(found[0].group('value'))
        elevation={'value':val*canonical_scale(tag['elevation_unit'])/canonical_scale(unit) if unit_known(unit) else None,
                   'reference':tag['elevation_reference'],'origin':'tag','source_id':key,'text':actual,'evidence_level':'规则推算'}
        if elevation['reference'] not in {'bottom','top','center'}: raise ValueError('标高基准未明')
    return section,elevation

def paired_geometry(a,b,width,tol):
    pa,pb=a['part'],b['part']
    if pa['kind']!=pb['kind']: return None
    if pa['kind']=='line':
        a0,a1,b0,b1=pa['start'],pa['end'],pb['start'],pb['end']
        if max(p[2] for p in (a0,a1,b0,b1))-min(p[2] for p in (a0,a1,b0,b1))>1e-9: return None
        da=[y-x for x,y in zip(a0,a1)]; db=[y-x for x,y in zip(b0,b1)]
        la=math.dist(a0,a1);lb=math.dist(b0,b1)
        if min(la,lb)<=0: return None
        u=[v/la for v in da]; v=[v/lb for v in db]
        if abs(abs(sum(x*y for x,y in zip(u,v)))-1)>1e-9: return None
        if sum(x*y for x,y in zip(u,v))<0: b0,b1=b1,b0
        origin=sum((b0[i]-a0[i])*u[i] for i in range(3))
        sep=math.sqrt(sum((b0[i]-a0[i]-origin*u[i])**2 for i in range(3)))
        if abs(sep-width)>tol: return None
        lo=max(0.,origin);hi=min(la,origin+lb)
        if hi-lo<=max(tol,1e-9):return None
        a_start=[a0[i]+lo*u[i] for i in range(3)];a_end=[a0[i]+hi*u[i] for i in range(3)]
        b_start=[b0[i]+(lo-origin)*u[i] for i in range(3)];b_end=[b0[i]+(hi-origin)*u[i] for i in range(3)]
        raw_b0,raw_b1=pb['start'],pb['end'];raw_db=[y-x for x,y in zip(raw_b0,raw_b1)]
        bf=sorted(sum((p[i]-raw_b0[i])*raw_db[i] for i in range(3))/(lb*lb) for p in (b_start,b_end))
        return {'kind':'line','start':[(x+y)/2 for x,y in zip(a_start,b_start)],'end':[(x+y)/2 for x,y in zip(a_end,b_end)],
                '_source_fractions':[[lo/la,hi/la],bf]}
    if pa['kind']=='ellipse_arc':
        if abs(pa['center'][2]-pb['center'][2])>1e-9 or any(abs(v[2])>1e-9 for v in (pa['u'],pa['v'],pb['u'],pb['v'])): return None
        if math.dist(pa['center'],pb['center'])>tol or abs(pa['start_param']-pb['start_param'])>1e-9 or abs(pa['sweep_param']-pb['sweep_param'])>1e-9: return None
        # Concentric circular boundaries only. Affine ellipses remain fully present in coverage as unresolved.
        ru=math.sqrt(sum(x*x for x in pa['u']));rv=math.sqrt(sum(x*x for x in pa['v']))
        su=math.sqrt(sum(x*x for x in pb['u']));sv=math.sqrt(sum(x*x for x in pb['v']))
        if min(ru,rv,su,sv)<=0 or abs(ru-rv)>tol or abs(su-sv)>tol: return None
        if abs(sum(x*y for x,y in zip(pa['u'],pa['v'])))>1e-9*ru*rv or abs(sum(x*y for x,y in zip(pb['u'],pb['v'])))>1e-9*su*sv: return None
        if abs(abs(ru-su)-width)>tol: return None
        if max(math.dist([x/ru for x in pa['u']],[x/su for x in pb['u']]),math.dist([x/rv for x in pa['v']],[x/sv for x in pb['v']]))>1e-9: return None
        return {**pa,'center':[(x+y)/2 for x,y in zip(pa['center'],pb['center'])], 'u':[(x+y)/2 for x,y in zip(pa['u'],pb['u'])],'v':[(x+y)/2 for x,y in zip(pa['v'],pb['v'])]}
    return None

def build(manifest_path,profile_path,claims_path=None):
    profile=adapter.load_json(profile_path)
    if profile.get('schema_version')!=SCHEMA: raise ValueError('风管识别配置契约无效')
    layout=text(profile.get('layout'),'layout');space=text(profile.get('space_id'),'space_id');floor=text(profile.get('floor_id'),'floor_id')
    stream,blocks,manifest=adapter.source_from_manifest(manifest_path)
    source=manifest['authoritative_source']['sha256']
    if profile.get('expected_source_sha256')!=source: raise ValueError('识别配置原图SHA不符')
    raw_unit=manifest['drawing_unit']['raw_unit']
    unit=({.001:'mm',.01:'cm',1.:'m',1000.:'km',.0254:'in',.3048:'ft'}[canonical_scale(raw_unit)] if unit_known(raw_unit) else 'raw_DU')
    crop=profile.get('crop')
    if crop is not None and (not isinstance(crop,list) or len(crop)!=4 or any(isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) for n in crop) or crop[2]<=crop[0] or crop[3]<=crop[1]): raise ValueError('裁剪边界无效')
    failures=[]
    rows=list(adapter.expanded_manifest_rows(stream,blocks,manifest,int(profile.get('max_block_depth',32)),failures,target_layout=layout))
    by_key={source_key(r):r for r in rows}
    if len(by_key)!=len(rows): raise ValueError('展开源实例身份重复')
    target=profile.get('target_layer_pattern'); text(target,'target_layer_pattern'); re.compile(target)
    protected=[manifest_path,profile_path,stream,blocks,Path(manifest['authoritative_source']['path']),Path(manifest['exchange_dxf']['path'])]
    entries=[]; parts=[]
    for r in rows:
        if re.search(target,str(r.get('layer',''))) is None: continue
        key=source_key(r); geo=r.get('geometry'); notes=r.get('geometry_issues',[])
        base={'source_record_id':r.get('source_definition_record_id',r['record_id']),'source_instance_path':key,
              'source_sha256':source,'layout':layout,'space_id':space,'layer':r.get('layer'),'entity_type':r.get('entity_type'),
              'instance_transform':r.get('instance_transform'),'geometry_issues':notes}
        raw_parts=geo.get('parts',[]) if isinstance(geo,dict) and not notes else []
        for i,part in enumerate(raw_parts or [None]):
            item={**base,'source_key':key+'#'+str(i) if raw_parts else key+'#unrecovered','part_index':i if raw_parts else None,
                  'geometry':geometry(part) if part else None,'geometry_sha256':digest(part) if part else None,
                  'disposition':'unrecognized','reason':'未匹配有据识别规则','quantity':None}
            try:
                info=checked_geometry(item['geometry']) if part else None
                item['raw_geometry_length']=info['length'] if info else None
                item['scope_state']=scope_state(info['bbox'] if info else r.get('scope_extent_bbox') or r.get('bbox'),crop)
                if item['scope_state']=='outside': item.update(disposition='excluded',reason='完整几何在明确裁剪范围之外；未截断入量')
                elif item['scope_state']=='crossing': item['reason']='完整曲线跨裁剪边界，未用端点或弦裁剪，需明确对象归属'
                elif item['scope_state']=='unlocated': item['reason']='无可恢复位置，不能静默归入裁剪范围'
            except (ValueError,KeyError,TypeError) as exc:
                item.update(geometry_issues=[*notes,str(exc)],scope_state='unlocated',raw_geometry_length=None,reason='完整几何未恢复')
            entries.append(item)
            if part is not None and item['scope_state']=='inside' and not item['geometry_issues']:
                parts.append({'entry':item,'row':r,'part':part,'key':key})
        if r.get('entity_type')=='INSERT' and any(k.startswith(key+'/'+str(r.get('block_name',''))+'/') for k in by_key):
            entries[-1].update(disposition='excluded',reason='已逐实例展开的容器；容器自身不另计，叶图元分别保留去向')
    selected={}; rules={}; classification=[]
    for rule in profile.get('rules',[]):
        rid=text(rule.get('rule_id'),'rule_id')
        if rid in rules: raise ValueError('rule_id重复')
        evidence,er=read_ref(rule['evidence'],profile_path.parent);protected.append(Path(er['path']))
        expected={'schema_version':'qto-duct-recognition-evidence/1.0','source_sha256':source,'layout':layout,'space_id':space,'rule_id':rid,
                  'selector':rule.get('selector'),'representation':rule.get('representation'),'system':rule.get('system'),'section':rule.get('section'),'crop':crop}
        if any(evidence.get(k)!=v for k,v in expected.items()) or not evidence.get('basis') or not evidence.get('source_locator'):
            raise ValueError('识别规则与有源证据的范围/系统/截面/画法不一致:'+rid)
        text(rule.get('system'),'system')
        if rule['system'] in {'未知','未决','unknown'}: raise ValueError('系统未决不能入量')
        section,elevation=bind_tag(rule,evidence,by_key,unit)
        values,_=section_values(section,unit) if section else ({},None)
        config={**rule,'section':section,'dimensions':values,'elevation':elevation,'classification_proof':{**er,'record':evidence}}
        rules[rid]=config
        if config['representation']=='exclude':
            for entry in entries:
                original=by_key.get(entry['source_instance_path'])
                if original and match(config.get('selector',{}),original,entry['source_instance_path']):
                    if entry['disposition']=='counted': raise ValueError('排除规则与已计对象冲突')
                    entry.update(disposition='excluded',reason=config.get('exclusion_reason') or evidence['basis'],classification_proof=config['classification_proof'])
        for item in parts:
            if match(rule.get('selector',{}),item['row'],item['key']): selected.setdefault(item['entry']['source_key'],[]).append(config)
    segments=[];curves=[];risers=[]; used=set()
    def add(items,part,rule):
        sid='DSEG-'+str(len(segments)+len(curves)+1).zfill(6)
        keys=[p['entry']['source_key'] for p in items]
        row={'segment_id':sid,'floor_id':floor,'system':rule['system'],'space_id':space,'source_sha256':source,'source_ids':keys,
             'source_record_id':items[0]['entry']['source_record_id'],'source_instance_path':items[0]['key'],
             'source_geometry_refs':[{k:p['entry'][k] for k in ('source_key','geometry_sha256','geometry','instance_transform','part_index')} for p in items],
             **rule['dimensions'],'section':rule['section'],'size_evidence_level':'规则推算','classification_proofs':[rule['classification_proof']],
             'representation':rule['representation'],'length_evidence_level':'规则推算' if rule['representation']=='outline' else '确认','geometry':geometry(part)}
        if rule.get('elevation') and rule['elevation']['value'] is not None: row['elevation']=rule['elevation']
        info=checked_geometry(geometry(part));row.update(start=info['start'],end=info['end'])
        if part['kind']=='line' and abs(part['end'][2]-part['start'][2])<=1e-9: segments.append(row)
        else: curves.append(row)
        for item in items:
            item['entry'].update(disposition='counted',reason='有据工作候选已交实际风管消费；不代表数量完整或正式采用',segment_id=sid,classification_proof=rule['classification_proof'])
            used.add(item['entry']['source_key'])
    for item in parts:
        sk=item['entry']['source_key']; hits=selected.get(sk,[])
        if len(hits)!=1:
            if len(hits)>1: item['entry'].update(disposition='unrecognized',reason='多个识别规则冲突，未按优先级静默选取')
            continue
        rule=hits[0];rep=rule['representation']
        if rep in {'centerline','single_line'}:
            add([item],item['part'],rule)
        elif rep=='riser_projection':
            risers.append({'candidate_id':'RISER-'+str(len(risers)+1).zfill(5),'source_ids':[sk],'geometry':item['entry']['geometry'],
                           'system':rule['system'],'section':rule['section'],'classification_proof':rule['classification_proof'],'height':None,'length':None,'evidence_level':'未决'})
            item['entry'].update(reason='有据竖井投影候选，缺竖向高度；仍未计量',candidate_id=risers[-1]['candidate_id'])
        elif rep not in {'outline','exclude'}: raise ValueError('不支持的风管画法:'+str(rep))
        elif rep=='exclude': item['entry'].update(disposition='excluded',reason=rule.get('exclusion_reason') or rule['classification_proof']['record']['basis'],classification_proof=rule['classification_proof'])
    for item in parts:
        sk=item['entry']['source_key'];hits=selected.get(sk,[])
        if len(hits)!=1 or hits[0]['representation']!='outline' or sk in used: continue
        rule=hits[0]
        width=rule['dimensions'].get('diameter',rule['dimensions'].get('width'))
        if width is None: item['entry']['reason']='截面尺寸或平面物理比例未知，外轮廓间距不能由尺寸倒推';continue
        tol=rule.get('pair_tolerance')
        if isinstance(tol,bool) or not isinstance(tol,(int,float)) or not 0<=tol<width/4: raise ValueError('轮廓配对容差必须显式且小于宽度四分之一')
        candidates=[]
        for other in parts:
            if other is item or other['entry']['source_key'] in used or selected.get(other['entry']['source_key'])!=[rule]: continue
            mid=paired_geometry(item,other,width,tol)
            if mid is not None: candidates.append((other,mid))
        if len(candidates)!=1: item['entry']['reason']='外轮廓无唯一完整配对；部分重叠、异面或多候选均未入量';continue
        other,mid=candidates[0]
        reverse=[q for q in parts if q is not other and q['entry']['source_key'] not in used and selected.get(q['entry']['source_key'])==[rule] and paired_geometry(other,q,width,tol) is not None]
        if len(reverse)!=1: item['entry']['reason']='外轮廓反向配对不唯一';continue
        source_items=[item,other]
        fractions=mid.pop('_source_fractions',None)
        if fractions:
            source_items=[]
            for parent,(lo,hi) in zip((item,other),fractions):
                lo=max(0.,min(1.,lo));hi=max(0.,min(1.,hi))
                if lo<=1e-12 and hi>=1-1e-12:source_items.append(parent);continue
                original=parent['entry'];used.add(original['source_key'])
                original.update(disposition='excluded',reason='完整源part按实际重叠与余段分区；父行自身不另计，区间子行逐一保留去向',partition_children=[])
                cuts=sorted(set([0.,lo,hi,1.]));p=parent['part']
                for low,high in zip(cuts,cuts[1:]):
                    if high-low<=1e-12:continue
                    trimmed={'kind':'line','start':[a+(b-a)*low for a,b in zip(p['start'],p['end'])],
                             'end':[a+(b-a)*high for a,b in zip(p['start'],p['end'])]}
                    key=original['source_key']+'@'+format(low,'.12g')+':'+format(high,'.12g')
                    child={**original,'source_key':key,'geometry':geometry(trimmed),'geometry_sha256':digest(trimmed),
                           'parent_source_part':original['source_key'],'source_fraction_range':[low,high],
                           'raw_geometry_length':math.dist(trimmed['start'],trimmed['end']),'disposition':'unrecognized',
                           'reason':'局部轮廓重叠之外的实际余段未识别','partition_children':[]}
                    entries.append(child);original['partition_children'].append(key)
                    if abs(low-lo)<1e-12 and abs(high-hi)<1e-12:
                        source_items.append({**parent,'entry':child,'part':trimmed})
        add(source_items,mid,rule)
    if claims_path:
        claims=adapter.load_json(claims_path);protected.append(claims_path)
        if claims.get('schema_version')!='qto-duct-coverage-claims/1.0' or claims.get('source_sha256')!=source: raise ValueError('方法无关覆盖声明来源无效')
        mapped={r['source_key']:r for r in entries};seen=set();quantity_groups={}
        for claim in claims.get('claims',[]):
            key=claim['source_key'];row=mapped.get(key)
            if key in seen or row is None or row.get('geometry_sha256')!=claim.get('geometry_sha256'): raise ValueError('覆盖声明实例重复、缺失或完整几何不符')
            seen.add(key)
            evidence,er=read_ref(claim['evidence'],claims_path.parent);protected.append(Path(er['path']))
            expected={'source_sha256':source,'source_key':key,'geometry_sha256':row['geometry_sha256'],'layout':layout,'space_id':space,'disposition':claim['disposition']}
            if any(evidence.get(k)!=v for k,v in expected.items()) or not evidence.get('basis') or not evidence.get('source_locator'): raise ValueError('覆盖声明缺乏逐实例有源证据')
            if claim['disposition']=='counted':
                if row.get('partition_children'):raise ValueError('分区父part只是索引，外部计量应逐实际子区间绑定，不能覆盖已有计量与余段')
                result,rr=read_ref(claim['quantity_ref'],claims_path.parent);protected.append(Path(rr['path']))
                if not isinstance(result,dict):raise ValueError('外部实际数量文件必须是对象')
                if result.get('currentness_status','current')!='current' or str(result.get('status','')).lower() in {'stale','invalid','failed','input_required','unresolved','待核','未测量','失效'}:raise ValueError('外部实际数量文件整体状态未就绪或失效')
                if 'source_sha256' in result and result['source_sha256']!=source:raise ValueError('外部实际数量文件原图版本不一致')
                values=result[claim['collection']];index=claim['index']
                if not isinstance(values,list) or isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(values):raise ValueError('实际数量行定位必须是有效非负整数')
                actual=values[index]
                measurement=external_length_measurement(actual,claim,row,unit,manifest['authoritative_source'],rr,claims_path.parent,protected,source,layout,space)
                measurement['quantity_source_key']=key
                group=(rr['path'],rr['sha256'],claim['collection'],index)
                quantity_groups.setdefault(group,[]).append(measurement)
                row['external_measurement_validation']=measurement
            elif claim['disposition']!='excluded': raise ValueError('覆盖声明去向无效')
            if row['disposition']=='counted': raise ValueError('外部声明与本轮已计对象重复')
            row.update(disposition=claim['disposition'],reason=evidence['basis'],external_claim=claim)
        check_external_groups(quantity_groups)
    for failure in failures:
        # Expansion diagnostics are retained even if the missing child has no recoverable location.
        entries.append({'source_key':str(failure.get('instance_path'))+'#expansion-failure','layer':None,'entity_type':'BLOCK_EXPANSION_FAILURE',
                        'source_sha256':source,'layout':layout,'space_id':space,'disposition':'unrecognized','reason':failure['kind'],'quantity':None,'diagnostic':failure})
    summary=Counter((e.get('layer'),e['disposition']) for e in entries)
    coverage={'schema_version':'qto-duct-layer-coverage/1.0','source_sha256':source,'layout':layout,'space_id':space,'crop':crop,
              'rows':entries,'summary':[{'layer':k[0],'disposition':k[1],'count':v} for k,v in sorted(summary.items(),key=lambda p:str(p[0]))],
              'dispositions_closed':all(e['disposition'] in {'counted','excluded'} for e in entries),'quantity_complete':False,
              'meaning':'完整源实例与part精确绑定；未采用宽缓冲或最近文字认定已计。闭合去向不证明完整工程量。'}
    data={'schema_version':'qto-duct-fitting-vertical-input/1.0','package_id':text(profile.get('package_id'),'package_id'),'network_id':profile['package_id'],
          'unit':unit,'output_unit':'m','source_sha256':source,'coordinate_system':'WCS','coordinate_unit':unit,'layout':layout,'space_id':space,
          'floors':[{'floor_id':floor}], 'parameters':profile['parameters'],'segments':segments,'drawn_curve_segments':curves,
          'riser_projection_candidates':risers,'coverage':coverage,'fittings':[],
          'neutral_binding':{'manifest':ref(manifest_path),'profile':ref(profile_path),'authoritative_source':manifest['authoritative_source'],
                            'exchange_dxf':manifest['exchange_dxf'],'layout_entities':ref(stream),'block_definitions':ref(blocks)}}
    if claims_path: data["neutral_binding"]["claims"]=ref(claims_path)
    return data,protected

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cad-manifest',type=Path,required=True);p.add_argument('--profile',type=Path,required=True);p.add_argument('--claims',type=Path);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    try:
        data,protected=build(a.cad_manifest.resolve(),a.profile.resolve(),a.claims.resolve() if a.claims else None)
        out=Path(os.path.abspath(a.output_dir));reject_reparse_chain(out)
        if out.exists() and (not out.is_dir() or any(out.iterdir())): raise ValueError('批量建网输出须另存新目录或空目录')
        paths=[out/'风管实际消费输入.json',out/'风管图层覆盖差集.json',out/'风管实际消费结果.json',out/'实际风管消费日志.json']
        from bind_duct_vertical_annotations import ensure_output_safe
        for target in paths: ensure_output_safe(target,protected)
        write_protected_json(paths[0],data,protected)
        write_protected_json(paths[1],data['coverage'],protected)
        producer=Path(__file__).with_name('infer_duct_fittings_vertical.py').resolve()
        command=[sys.executable,'-B',str(producer),'--input',str(paths[0]),'--output',str(paths[2])]
        process=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=180)
        write_protected_json(paths[3],{'command':command,'returncode':process.returncode,'stdout':process.stdout,'stderr':process.stderr,
                                     'producer':ref(producer),'input':ref(paths[0]),'business_adoption_allowed':False},protected)
        if process.returncode: raise ValueError('实际风管消费失败，输入/待办与真实退出码保留，见实际风管消费日志.json')
        print(json.dumps({'status':'WORKING_INPUT_WITH_COVERAGE','input':str(paths[0]),'segments':len(data['segments']),'curves':len(data['drawn_curve_segments']),'unrecognized':sum(r['disposition']=='unrecognized' for r in data['coverage']['rows']),'business_adoption_allowed':False},ensure_ascii=False));return 0
    except (ValueError,OSError,KeyError,TypeError,IndexError,subprocess.TimeoutExpired) as e:
        print(json.dumps({'status':'INPUT_REQUIRED','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__': raise SystemExit(main())

