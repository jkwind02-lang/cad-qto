"""Opt-in neutral duct paths and explicitly connected accessories for the legacy CLI.

Exact drawn curves are measured independently and never sent through straight-line
intersection heuristics. Their connection/fitting completion remains explicit.
"""
from __future__ import annotations
import copy
import json
import math
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'_qto-shared-evidence/scripts'))
import qto_cad_geometry as cg
from qto_cad_source_contract import canonical_scale


def checked_geometry(value):
    if not isinstance(value, dict):
        raise ValueError('完整几何缺失')
    def finite(node):
        if isinstance(node, dict):
            for v in node.values(): finite(v)
        elif isinstance(node, list):
            for v in node: finite(v)
        elif isinstance(node, (float, int)) and not isinstance(node,bool) and not math.isfinite(node):
            raise ValueError('几何含非法数值')
    finite(value)
    if not value.get('parts'):
        raise ValueError('完整几何不得为空')
    for part in value['parts']:
        keys = ('start','end') if part.get('kind') == 'line' else ('center','u','v')
        for key in keys:
            if not isinstance(part.get(key),list) or len(part[key])!=3 or any(isinstance(n,bool) or not isinstance(n,(int,float)) for n in part[key]):
                raise ValueError('几何坐标必须是完整WCS三维向量')
        if part.get('kind')=='ellipse_arc' and (any(isinstance(part.get(k),bool) or not isinstance(part.get(k),(int,float)) for k in ('start_param','sweep_param')) or part['sweep_param']==0 or abs(part['sweep_param'])>math.tau+1e-9):
            raise ValueError('曲线扫角无效')
    result=cg.summarize(value)
    if not math.isfinite(result['length']) or result['length']<=0:
        raise ValueError('曲线长度无效')
    return result


def append_drawn_and_inline(data, result, spec_of):
    if 'neutral_binding' in data:
        from build_duct_network_from_neutral import build, read_ref
        binding=data['neutral_binding']; base=Path(data.get('_input_base','.'))
        for name in ('manifest','profile','claims'):
            if name in binding: read_ref(binding[name],base)
        def path(name):
            p=Path(binding[name]['path']);return p if p.is_absolute() else base/p
        rebuilt,_=build(path('manifest'),path('profile'),path('claims') if 'claims' in binding else None)
        if any(data.get(k)!=v for k,v in rebuilt.items()):
            raise ValueError('中性来源/识别配置重建结果与实际风管输入不同，拒绝被改写的源几何或系统规格')
    curves=data.get('drawn_curve_segments',[])
    if not isinstance(curves,list): raise ValueError('drawn_curve_segments必须是数组')
    known_unit=data.get('unit')!='raw_DU'
    factor=canonical_scale(data['unit'])/canonical_scale(data.get('output_unit','m')) if known_unit else None
    ids={s['segment_id'] for s in data.get('segments',[])}
    physical_sources={key for s in data.get('segments',[]) for key in s.get('source_ids',[])}
    result['drawn_curve_segments']=[] if curves else result.get('drawn_curve_segments',[])
    for raw in curves:
        sid=raw.get('segment_id')
        if not isinstance(sid,str) or not sid or sid in ids: raise ValueError('曲线与直管segment_id必须全集唯一')
        ids.add(sid)
        keys=raw.get('source_ids',[])
        if len(keys)!=len(set(keys)) or any(k in physical_sources for k in keys): raise ValueError('完整曲线源实例与已有数量重复')
        physical_sources.update(keys)
        geo=checked_geometry(raw.get('geometry'))
        if raw.get('system') in (None,'','未知','未决') or not raw.get('source_ids'): raise ValueError('曲线须保留有据系统及源实例')
        row={**raw,'length':geo['length']*factor if known_unit else None,
             'raw_geometry_length':geo['length'],'length_error_estimate':geo['length_error_estimate']*factor if known_unit else None,
             'evidence_level':'规则推算','connection_status':'explicit_geometry_only', 'business_adoption_allowed':False}
        result['drawn_curve_segments'].append(row)
        if known_unit:
            spec=spec_of(raw); system=raw['system']; length=row['length']
            group=result['length_summary'].setdefault(system,{}).setdefault('规则推算',{})
            group[spec]=group.get(spec,0)+length
            from infer_duct_fittings_vertical import perimeter_m
            per=perimeter_m(raw,factor)
            if per is not None:
                areas=result['theoretical_surface_summary'].setdefault(system,{}).setdefault('规则推算',{})
                areas[spec]=areas.get(spec,0)+per*length
    if curves:
        total=sum(r['length'] for r in result['drawn_curve_segments']) if known_unit else None
        result['metrics']['drawn_curve_length_rule']=total
        if known_unit: result['metrics']['measured_length_rule']+=total
        result['part_totals']['drawn_curve']={'规则推算':total}
        graph,_=source_curve_network(data)
        result['source_curve_network']=graph
        result['pending'].extend(graph['pending'])
        result['metrics']['totals_complete']=False
    for item in data.get('riser_projection_candidates',[]):
        result['pending'].append({'kind':'vertical_projection_height_unresolved',**item,'length':None,'reason':'竖井平面弧投影不是立管竖长；未提供高度或上下端标高依据'})
    coverage=data.get('coverage',{})
    if coverage:
        result['coverage']=copy.deepcopy(coverage)
        result['metrics']['totals_complete']=False  # coverage != quantity completeness, even if all dispositions are assigned
        result['quantity_completeness']='requires_fittings_vertical_connections_and_scope_review'
    if not known_unit:
        result['source_xy_unit_verified']=False
        result['source_coordinate_unit']='raw_DU'
        # Preserve raw geometry only, never a guessed millimetre conditional total.
        for row in result['straight_segments']:
            row['raw_drawing_length']=row.pop('length'); row['length']=None
        for group in ('accessory_gaps','open_taps','vertical_items','vertical_scenarios'):
            for item in result.get(group,[]):
                if item.get('kind')=='annotation_explicit_height': continue
                for field in ('length','centerline_length','surface_area','theoretical_surface_area'):
                    if item.get(field) is not None:
                        item['raw_drawing_'+field]=item[field];item[field]=None
        for fitting in result.get('fittings',[]):
            if fitting.get('centerline_length') is not None:
                fitting['raw_drawing_centerline_length']=fitting['centerline_length'];fitting['centerline_length']=None
            for arm in fitting.get('arms',[]):
                if arm.get('length') is not None:arm['raw_drawing_length']=arm['length'];arm['length']=None
        for key in ('length_summary','theoretical_surface_summary','part_totals'):
            result[key]=None
        for key in list(result['metrics']):
            if 'length' in key or key in {'single_end_plane_default','single_end_plane_full','measured_length_rule'}:
                result['metrics'][key]=None
        result['metrics']['totals_complete']=False
        result['pending'].append({'kind':'source_xy_unit_unresolved','reason':'原图单位未知，未从尺寸或明示米制高度倒推平面单位，物理长度与面积为null'})
    if 'valves' in data:
        emit_inline(data,result,spec_of)
    if curves or coverage or 'valves' in data or not known_unit:
        result['metrics']['pending_count']=len(result['pending'])
        result['counts']['pending']=len(result['pending'])
        result['counts']['drawn_curves']=len(curves)
        result['meaning']+=' 完整曲线采用源曲线积分；覆盖去向不证明数量完整，所有新增成果仍是工作候选。'


def emit_inline(data,result,spec_of):
    from duct_inline_reception import frozen_file, nonempty
    segments={s['segment_id']:s for s in data.get('segments',[])+data.get('drawn_curve_segments',[])}
    seen=set(); valves=[]
    for raw in data['valves']:
        vid=raw.get('valve_id'); pid=raw.get('source_object_id')
        if not nonempty(vid) or not nonempty(pid) or vid in seen or pid in seen:
            raise ValueError('风管附件物理ID和附件ID必须非空且唯一')
        seen.update((vid,pid))
        links=raw.get('connected_segment_ids')
        if not isinstance(links,list) or len(links)!=1 or links[0] not in segments:
            raise ValueError('风管在线附件必须显式连接到唯一实际直管段，不从空间候选自动建连接')
        host=segments[links[0]]
        if raw.get('system')!=host.get('system') or not nonempty(host.get('system')):
            raise ValueError('风管附件与宿主系统不一致')
        spec=spec_of(host)
        if '?' in spec or spec=='未知规格' or raw.get('quantity_unit') not in {'个','只','台','套'} or not nonempty(raw.get('valve_type')) or raw.get('valve_type') in {'未分类','未知','未决'} or any(host.get(k) is not None and host[k]<=0 for k in ('width','height','diameter')):
            raise ValueError('附件类别、离散单位或宿主规格未决')
        evidence,ref=frozen_file(raw.get('connection_evidence'),Path(data.get('_input_base','.')),'connection_evidence')
        expected={'schema_version':'qto-inline-connection-evidence/1.0','source_sha256':data.get('source_sha256'),
                  'evidence_id':raw.get('connection_source_id'),'physical_id':pid,'segment_id':links[0],
                  'layout':data.get('layout'),'space_id':data.get('space_id'),'relationship':'inline_connection'}
        if any(evidence.get(k)!=v or v is None for k,v in expected.items()) or not nonempty(evidence.get('basis')) or not nonempty(evidence.get('source_locator')):
            raise ValueError('风管在线附件连接依据/来源/范围无效')
        valves.append({**raw,'system':host['system'],'dn':spec,'spec':spec,'instance_count':1,
                       'attached_segment_ids':links,'connection_method':'evidence_bound_connection','connection_evidence':ref,
                       'quantity_level':'规则推算量','business_adoption_allowed':False})
    result['valves']=valves
    counts=Counter((v['system'],v['dn'],v['valve_type'],v['quantity_unit']) for v in valves)
    result['valve_summary']=[{'system':k[0],'dn':k[1],'valve_type':k[2],'unit':k[3],'instance_count':v} for k,v in sorted(counts.items())]
    result['metrics']['valve_count']=len(valves)

def source_curve_network(data):
    """Candidate topology uses true WCS ends and oriented tangents, never chords."""
    rows=[(False,r) for r in data.get('segments',[])]+[(True,r) for r in data.get('drawn_curve_segments',[])]
    ends=[];edges=[];contacts=set();joins=[];pending=[]
    tolerance=data.get('parameters',{}).get('end_snap_tolerance',5.0)
    if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or tolerance<0:
        raise ValueError('曲线端点容差无效')
    for curved,row in rows:
        sid=row['segment_id'];g=row.get('geometry')
        if g:
            info=checked_geometry(g); parts=g['parts']
        else:
            def p3(p):return list(p)+[0.] if len(p)==2 else list(p)
            parts=[{'kind':'line','start':p3(row['start']),'end':p3(row['end'])}]
            info=checked_geometry({'schema':cg.GEOMETRY_SCHEMA,'coordinate_system':'WCS','parts':parts})
        for which,p in (('start',parts[0]),('end',parts[-1])):
            is_end=which=='end'
            if p['kind']=='line':
                pos=p['end' if is_end else 'start'];v=[b-a for a,b in zip(p['start'],p['end'])]
            else:
                t=p['start_param']+(p['sweep_param'] if is_end else 0)
                pos=cg.conic_point(p,t)
                v=[(-a*math.sin(t)+b*math.cos(t))*(1 if p['sweep_param']>0 else -1) for a,b in zip(p['u'],p['v'])]
            length=math.sqrt(sum(x*x for x in v))
            if not math.isfinite(length) or length<=0:raise ValueError('曲线端部切向退化')
            ends.append({'segment_id':sid,'end':which,'point':pos,'outward_tangent':[x/length*(1 if is_end else -1) for x in v],
                         'is_curve':curved,'system':row.get('system'),'space_id':row.get('space_id',data.get('space_id')),
                         'floor_id':row.get('floor_id'),'section':{k:row.get(k) for k in ('width','height','diameter')}})
        edges.append({'segment_id':sid,'kind':'complete_drawn_curve' if curved else 'drawn_line','geometry_length_raw':info['length'],
                      'length_method':'source_geometry_integral','system':row.get('system'),'space_id':row.get('space_id',data.get('space_id')),
                      'source_ids':row.get('source_ids',[])})
    candidates={i:[] for i in range(len(ends))}
    for i,a in enumerate(ends):
        for j in range(i+1,len(ends)):
            b=ends[j]
            if a['segment_id']==b['segment_id'] or not(a['is_curve'] or b['is_curve']):continue
            if any(a[k]!=b[k] for k in ('system','space_id','floor_id')):continue
            if math.dist(a['point'],b['point'])>tolerance:continue
            candidates[i].append(j);candidates[j].append(i)
            if not a['is_curve']:contacts.add((a['segment_id'],a['end']))
            if not b['is_curve']:contacts.add((b['segment_id'],b['end']))
    seen=set()
    for i,a in enumerate(ends):
        if not a['is_curve'] and not candidates[i]:continue
        nearby=candidates[i]
        if len(nearby)!=1 or len(candidates[nearby[0]])!=1:
            if a['is_curve']:
                pending.append({'kind':'drawn_curve_endpoint_unresolved','segment_id':a['segment_id'],'end':a['end'],
                                'reason':'曲线真实端点无唯一同系统同空间续接；保持开放端或歧义候选','candidate_segment_ids':[ends[j]['segment_id'] for j in nearby]})
            continue
        j=nearby[0];pair=tuple(sorted((i,j)))
        if pair in seen:continue
        seen.add(pair);b=ends[j]
        dot=sum(x*y for x,y in zip(a['outward_tangent'],b['outward_tangent']))
        if dot>-1+1e-6 or a['section']!=b['section']:
            pending.append({'kind':'drawn_curve_endpoint_unresolved','segment_id':a['segment_id'],'end':a['end'],
                            'reason':'真实端点相近但切向或截面不连续；需明确弯头/变径/接管依据','other_segment_id':b['segment_id']})
            continue
        joins.append({'a':[a['segment_id'],a['end']],'b':[b['segment_id'],b['end']],
                      'a_point':a['point'],'b_point':b['point'],'distance':math.dist(a['point'],b['point']),
                      'a_outward_tangent':a['outward_tangent'],'b_outward_tangent':b['outward_tangent'],
                      'system':a['system'],'space_id':a['space_id'],'status':'unique_source_endpoint_tangent_candidate'})
    return {'schema_version':'qto-duct-source-curve-network/1.0','edges':edges,'endpoints':ends,'joins':joins,
            'pending':pending,'endpoint_tolerance_raw':tolerance,'business_adoption_allowed':False},contacts
