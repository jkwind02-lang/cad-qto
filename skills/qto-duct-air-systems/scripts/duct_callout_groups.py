"""Multiple source callouts in one recognition rule, without guessed ordering."""
from __future__ import annotations
import copy
import math
import re
from duct_source_recovery import distance_to_source_curve


def bind_keyed_branches(local, proof, rows, unit, bind_tag, section_values, scope_fn, match, source_key):
    """Map actual keyed text fragments through actual marked terminal branches."""
    binding=local['annotation_binding'];cfg=binding['keyed_branches'];key=binding.get('source_key')
    row=rows.get(key)
    if row is None or row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:
        raise ValueError('多尺寸引注源实例缺失')
    actual=str(row.get('text',''))
    if actual!=binding.get('expected_text'):raise ValueError('引注内容与冻结识别配置不符')
    scope=scope_fn(local,proof,rows,match,source_key)
    def unresolved(reason):
        return {},{**scope,'status':'unresolved','reason':reason,'target_part_keys':[]}
    if scope['status']!='unique_source_leader_tree':return {},scope
    tolerance=cfg.get('marker_tolerance')
    if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or tolerance<0:
        raise ValueError('支路编号位置容差须为有限非负数')
    items={};spans=[];key_spans=[]
    for found in re.finditer(cfg.get('item_pattern',''),actual):
        values=found.groupdict();branch_id=values.get('branch_id');fragment=values.get('section_text')
        if not branch_id or not fragment:raise ValueError('合文解析须从实际文字捕获branch_id和section_text')
        if branch_id in items:return unresolved('同一实际合文的支路编号重复，未按出现顺序分配')
        fragment_start,fragment_end=found.span('section_text');spans.append((fragment_start,fragment_end))
        key_spans.append(found.span('branch_id'))
        tag={**binding,'expected_text':fragment};tag.pop('keyed_branches')
        fragment_rows={**rows,key:{**row,'text':fragment}}
        section,elevation=bind_tag({**local,'annotation_binding':tag},proof,fragment_rows,unit)
        dimensions,_=section_values(section,unit)
        if elevation:elevation={**elevation,'text':actual,'fragment_text':fragment,'fragment_span':[fragment_start,fragment_end]}
        items[branch_id]={'section':section,'dimensions':dimensions,'elevation':elevation,
                          'source_span':[fragment_start,fragment_end],'source_fragment':fragment}
    if not items:return unresolved('实际合文没有可解析的具名支路规格')
    dimensions=list(re.finditer(r'(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)|[ΦφØøDd]\s*(\d+(?:\.\d+)?)',actual))
    if any(not any(lo<=d.start() and d.end()<=hi for lo,hi in spans+key_spans) for d in dimensions):
        return unresolved('实际合文还有未分配的尺寸，不能只解析选中的部分')
    markers=cfg.get('markers')
    if not isinstance(markers,list) or not markers:raise ValueError('具名支路须给出实际源编号文字及实际引线part')
    per_part={};mapped_ids=set();seen_markers=set();facts=[]
    for marker in markers:
        marker_key=marker.get('source_key');source=rows.get(marker_key)
        if marker_key in seen_markers:raise ValueError('同一支路编号文字重复输入')
        seen_markers.add(marker_key)
        if source is None or source.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:
            raise ValueError('实际支路编号文字源实例缺失')
        if source.get('text')!=marker.get('expected_text'):raise ValueError('支路编号文字与实际源不符')
        if not marker.get('basis') or not marker.get('source_locator'):raise ValueError('支路编号缺语义依据或源定位')
        hit=re.fullmatch(cfg.get('marker_pattern',''),str(source.get('text','')))
        branch_id=hit.groupdict().get('branch_id') if hit else None
        if branch_id not in items or branch_id in mapped_ids:return unresolved('源支路编号缺合文唯一对应或重复编号')
        anchor=[source.get(k) for k in ('x','y','z')]
        if anchor[2] is None:anchor[2]=0.
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in anchor):
            return unresolved('实际支路编号完整WCS位置未恢复')
        selected=marker.get('source_leader_part',{})
        targets=[]
        for branch in scope.get('target_branch_paths',[]):
            if any(p['source_key']==selected.get('source_key') and p['part_index']==selected.get('part_index')
                   and distance_to_source_curve(anchor,p['geometry'])<=tolerance for p in branch['leader_path']):
                targets.append(branch['target_part_key'])
        if len(targets)!=1:return unresolved('实际编号未唯一贴附末梢分支，主干编号或邻近文字不能分配规格')
        target=targets[0]
        if target in per_part:return unresolved('多个实际编号指向同一目标，未按列表顺序或同尺寸合并')
        mapped_ids.add(branch_id);per_part[target]={**copy.deepcopy(items[branch_id]),'annotation_scope_status':'source_keyed_multisize_branch_bound'}
        facts.append({'branch_id':branch_id,'target_part_key':target,'marker_source_key':marker_key,
                      'marker_text':source['text'],'marker_anchor':anchor,'source_leader_part':selected,
                      'item_source_span':items[branch_id]['source_span'],'item_source_fragment':items[branch_id]['source_fragment']})
    if mapped_ids!=set(items) or set(per_part)!=set(scope['target_part_keys']):
        return unresolved('合文规格、实际编号与完整引线全部末梢未一一对应，不能裁掉未绑定支路')
    # A second marker attached to this same tree cannot be hidden by selecting
    # only one instance, even when its displayed key is identical.
    for marker_key,source in rows.items():
        if marker_key in seen_markers or marker_key==key or source.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:continue
        hit=re.fullmatch(cfg.get('marker_pattern',''),str(source.get('text','')))
        if not hit or hit.groupdict().get('branch_id') not in items:continue
        anchor=[source.get('x'),source.get('y'),source.get('z') or 0.]
        if all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in anchor) and any(
                distance_to_source_curve(anchor,p['geometry'])<=tolerance for branch in scope['target_branch_paths'] for p in branch['leader_path']):
            return unresolved('同源引线树还有未纳入的实际支路编号，不能裁掉重复/冲突标签')
    return per_part,{**scope,'keyed_branch_bindings':facts,'reason':'合文实际支路键、真实编号和完整末梢路径逐项唯一对应'}


def bind_groups(rule, evidence, rows, unit, bind_tag, section_values, scope_fn, match, source_key):
    groups = rule.get('annotation_bindings')
    if not isinstance(groups, list) or not groups:
        raise ValueError('多规格引注须为非空源引注组')
    if rule.get('annotation_binding') or rule.get('section') is not None:
        raise ValueError('多规格引注组不能同时声明整组默认截面或单引注')
    if evidence.get('annotation_groups') != groups:
        raise ValueError('多规格引注组与冻结识别证据不一致')
    per_part = {}; scopes = []; seen = set()
    for index, binding in enumerate(groups):
        if not isinstance(binding, dict) or not binding.get('leader_network'):
            raise ValueError('每一规格须来自实际文字及完整源引线树，不能手填逐目标规格')
        identity = binding.get('source_key')
        if identity in seen:
            raise ValueError('多规格组重复使用同一文字；多尺寸合文尚需逐分支源语义')
        seen.add(identity)
        local = {**rule, 'annotation_binding': binding}
        proof = {**evidence, 'annotation_network': binding['leader_network']}
        if binding.get('keyed_branches') is not None:
            values,scope=bind_keyed_branches(local,proof,rows,unit,bind_tag,section_values,scope_fn,match,source_key)
            scope['annotation_group_index']=index;scopes.append(scope)
            for part_key,value in values.items():
                per_part[part_key]=None if part_key in per_part else {**value,'annotation_group_index':index}
            continue
        section, elevation = bind_tag(local, proof, rows, unit)
        scope = scope_fn(local, proof, rows, match, source_key)
        scope['annotation_group_index'] = index
        scopes.append(scope)
        if scope['status'] != 'unique_source_leader_tree':
            continue
        dimensions, _ = section_values(section, unit)
        value = {'section': section, 'dimensions': dimensions, 'elevation': elevation,
                 'annotation_group_index': index, 'annotation_scope_status': 'source_callout_group_bound'}
        for key in scope['target_part_keys']:
            if key in per_part:
                # Even identical values cannot prove two leaders refer to one physical scope.
                per_part[key] = None
            else:
                per_part[key] = copy.deepcopy(value)
    if any(scope['status']=='unresolved' for scope in scopes):
        return {},scopes
    conflicts = sorted(key for key, value in per_part.items() if value is None)
    if conflicts:
        scopes.append({'schema_version': 'qto-duct-annotation-scope/1.0', 'status': 'unresolved',
                       'reason': '多源引注同时指向同一源part，未按数组顺序或相同规格消解',
                       'target_part_keys': [], 'conflicting_part_keys': conflicts,
                       'business_adoption_allowed': False})
    return per_part, scopes
