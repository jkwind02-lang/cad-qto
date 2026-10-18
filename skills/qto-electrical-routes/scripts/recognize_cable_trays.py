#!/usr/bin/env python3
"""Recognize conservative cable-tray candidates from neutral center/edge lines and annotations."""
from collections import defaultdict
import hashlib
import json
import math
import re
from qto_electrical_evidence import bounds, cli, contains, finite, finish, identity_rows, required, routes

SCHEMA = 'qto-cable-tray-request/1.0'


def project(point, line):
    a, b = line['start'], line['end']
    v = [b[i] - a[i] for i in range(3)]
    n = sum(x*x for x in v)
    t = sum((point[i] - a[i])*v[i] for i in range(3)) / n if n else 0
    q = [a[i] + max(0, min(1, t))*v[i] for i in range(3)]
    return math.dist(point, q), t, q


def pair(a, b, angle, overlap_min, width_range, ztol):
    av, bv = [[line['end'][i] - line['start'][i] for i in range(2)] for line in (a, b)]
    la, lb = math.hypot(*av), math.hypot(*bv)
    if la <= 0 or lb <= 0 or abs(sum(av[i]*bv[i] for i in range(2))/(la*lb)) < math.cos(math.radians(angle)):
        return None
    if max(a['start'][2], a['end'][2], b['start'][2], b['end'][2]) - min(a['start'][2], a['end'][2], b['start'][2], b['end'][2]) > ztol:
        return None
    u = [x/la for x in av]
    along = [sum((p[i]-a['start'][i])*u[i] for i in range(2)) for p in (b['start'], b['end'])]
    lo, hi = max(0, min(along)), min(la, max(along))
    width = abs((b['start'][0]-a['start'][0])*u[1] - (b['start'][1]-a['start'][1])*u[0])
    if hi - lo <= overlap_min or not width_range[0] <= width <= width_range[1]:
        return None
    # Center lies half way across the perpendicular offset, independent of edge direction.
    normal = [b['start'][i] - a['start'][i] - along[0]*u[i] for i in range(2)]
    start = [a['start'][i]+lo*u[i]+normal[i]/2 for i in range(2)] + [a['start'][2]]
    end = [a['start'][i]+hi*u[i]+normal[i]/2 for i in range(2)] + [a['end'][2]]
    return dict(start=start, end=end, width_m=width, overlap_m=hi-lo,
                partial_edges=abs(hi-lo-la)>overlap_min or abs(hi-lo-lb)>overlap_min)


def calculate(e, request):
    if request.get('schema') != SCHEMA:
        raise ValueError('request.schema必须为' + SCHEMA)
    area = bounds(request.get('bounds_m'), 'bounds_m')
    center_pattern = request.get('center_layers')
    edge_pattern = request.get('edge_layers')
    if not center_pattern and not edge_pattern:
        raise ValueError('须写center_layers或edge_layers')
    tol = finite(request.get('pair_tolerance_m'), 'pair_tolerance_m', positive=True)
    angle = finite(request.get('parallel_angle_deg'), 'parallel_angle_deg')
    if angle >= 90:
        raise ValueError('parallel_angle_deg须小于90')
    ztol = finite(request.get('geometry_z_tolerance_m'), 'geometry_z_tolerance_m')
    width_range = request.get('width_range_m')
    if not isinstance(width_range, list) or len(width_range) != 2:
        raise ValueError('width_range_m须为两个正有限数')
    width_range=[finite(v,'width_range_m',positive=True)for v in width_range]
    if width_range[0] > width_range[1]:
        raise ValueError('须提供本图可识别width_range_m，不内置项目桥架宽度')
    centers = e.lines(center_pattern) if center_pattern else []
    edges = e.lines(edge_pattern) if edge_pattern else []
    todo, candidates, used = [], [], set()
    scope_records=[]
    closed_area=[area[0],area[1],math.nextafter(area[2],-math.inf),math.nextafter(area[3],-math.inf)]
    def scoped(parts,kind):
        included=[]
        for part in parts:
            inside=contains(part['start'],area)and contains(part['end'],area)
            hits=inside or routes.chord_hits_box(part['start'],part['end'],closed_area)
            status='inside'if inside else'crosses_scope'if hits else'outside'
            row=dict(source_id=part['source_id'],part_id=part['id'],role=kind,scope_status=status,
                start_m=part['start'],end_m=part['end'],source_length_m=part['length_m'],
                included_in_recognition=inside)
            scope_records.append(row)
            if inside:included.append(part)
            elif hits:todo.append(dict(problem='桥架源线跨出当前范围，未当完整段计入',source=row))
        return included
    centers=scoped(centers,'center');edges=scoped(edges,'edge')
    insert_paths=[r['source_id']for r in e.rows if r.get('entity_type')=='INSERT']
    def physical_context(line):
        ancestors=[p for p in insert_paths if line['source_id'].startswith(p+'/')]
        return max(ancestors,key=len)if ancestors else None
    def compatible_context(*lines):
        owners={physical_context(line)for line in lines}-{None}
        return len(owners)<=1
    edge_pairs = []
    for i, a in enumerate(edges):
        for b in edges[i+1:]:
            if not compatible_context(a,b):continue
            p = pair(a, b, angle, tol, width_range, ztol)
            if p:
                edge_pairs.append((a,b,p))
    if centers:
        for c in centers:
            matches = []
            for a,b,p in edge_pairs:
                if not compatible_context(c,a,b):continue
                mid = [(p['start'][i]+p['end'][i])/2 for i in range(3)]
                dist,t,_ = project(mid,c)
                cv=[c['end'][i]-c['start'][i]for i in range(2)]
                pv=[p['end'][i]-p['start'][i]for i in range(2)]
                denom=math.hypot(*cv)*math.hypot(*pv)
                parallel=denom>0 and abs(sum(cv[i]*pv[i]for i in range(2))/denom)>=math.cos(math.radians(angle))
                if parallel and dist <= tol and -tol <= t <= 1+tol and max(project(p[k],c)[0]for k in ('start','end'))<=tol:
                    matches.append((a,b,p))
            ids = [c['source_id']]
            width = None
            problems = []
            if len(matches)==1:
                a,b,p=matches[0]
                ids += [a['source_id'],b['source_id']]
                used.update([a['id'],b['id']])
                width=p['width_m']
                if p['partial_edges'] or abs(c['length_m']-p['overlap_m'])>tol:
                    problems.append('边线/中心线覆盖不齐，未外推宽度')
                    width=None
            else:
                problems.append('中心线边线宽度缺失/多候选')
            candidates.append(dict(source_ids=sorted(set(ids)), start=c['start'],end=c['end'],
                physical_instance_paths=sorted({physical_context(c)}-{None}),
                width_geometry_status='missing'if not matches else'ambiguous'if len(matches)>1 else'known'if width is not None else'partial',
                width_geometry_m=width, source_mode='center_with_edges',problems=problems))
    else:
        counts=defaultdict(int)
        for a,b,p in edge_pairs:
            counts[a['id']]+=1;counts[b['id']]+=1
        for a,b,p in edge_pairs:
            if counts[a['id']]!=1 or counts[b['id']]!=1:
                continue
            used.update([a['id'],b['id']])
            candidates.append(dict(source_ids=sorted({a['source_id'],b['source_id']}),start=p['start'],end=p['end'],
                physical_instance_paths=sorted({physical_context(a),physical_context(b)}-{None}),
                width_geometry_status='partial'if p['partial_edges']else'known',
                width_geometry_m=p['width_m'],source_mode='paired_edges',
                problems=['边线仅局部重叠，尾段未识别'] if p['partial_edges'] else []))
    for edge in edges:
        if edge['id'] not in used:
            todo.append(dict(problem='单边线/多重配对未定', source_id=edge['source_id']))
    annotation_re=re.compile(required(request.get('annotation_pattern'),'annotation_pattern'))
    annotation_layer=re.compile(required(request.get('annotation_layers'),'annotation_layers'))
    radius=finite(request.get('annotation_radius_m'),'annotation_radius_m',positive=True)
    width_factor=finite(request.get('annotation_width_factor_m'),'annotation_width_factor_m',positive=True)
    labels=[dict(t,match=annotation_re.search(t['text'])) for t in e.texts if contains(t['point_m'],area) and annotation_layer.search(str(t['layer'])) and annotation_re.search(t['text'])]
    leader_chains=e.leader_chains(request.get('leader_layers',r'(?!)'),finite(request.get('leader_snap_tolerance_m',0.0),'leader_snap_tolerance_m'))
    leader_text_tol=finite(request.get('leader_text_tolerance_m',radius),'leader_text_tolerance_m')
    bindings=identity_rows(request.get('annotation_bindings',[]),'annotation_bindings',key='segment_id')
    levels=identity_rows(request.get('elevation_bindings',[]),'elevation_bindings',key='segment_id')
    rows=[]
    for c in candidates:
        key=json.dumps([c['source_ids'],c['start'],c['end']],sort_keys=True,separators=(',',':'))
        ident='TRAY-'+hashlib.sha256(key.encode()).hexdigest()[:20]
        line=dict(start=c['start'],end=c['end'])
        possible=[]
        for text in labels:
            p3=text['point_m']+[c['start'][2]]
            distance,t,_=project(p3,line)
            if distance<=radius:
                possible.append(dict(source_id=text['source_id'],text=text['text'],distance_m=distance))
        chosen=None;via=None;binding_refs=[]
        if ident in bindings:
            binding=bindings[ident]
            e.refs(binding.get('basis_source_ids'),'annotation_binding.basis_source_ids')
            selected=binding.get('text_source_id')
            if selected not in {p['source_id'] for p in possible}:
                raise ValueError('明确旁注绑定不在本段候选范围')
            chosen=next(t for t in labels if t['source_id']==selected)
            via='explicit_evidence';binding_refs=binding['basis_source_ids']
        else:
            linked=[]
            for chain in leader_chains:
                arrow,tail=chain['target'],chain['text_end']
                if project(arrow,line)[0]<=tol:
                    for text in labels:
                        if math.dist(tail[:2],text['point_m'])<=leader_text_tol and text['source_id'] in {x['source_id']for x in possible}:
                            linked.append((text,chain))
            if len(linked)==1:
                chosen,chain=linked[0];via=chain['via'];binding_refs=chain['source_ids']
            elif len(linked)>1:
                c['problems'].append('多条引线旁注指向本段')
        spec=annotated_width=level=None
        parents=[r for r in e.rows if r.get('entity_type')=='INSERT' and any(s.startswith(r['source_id']+'/') for s in c['source_ids'])]
        attribute_evidence=[dict(source_id=r['source_id'],instance_values=r.get('attributes') or {},
            definition_defaults=[dict(source_id=x['source_id'],text=x.get('text')) for x in e.rows if x.get('entity_type')=='ATTDEF' and x['source_id'].startswith(r['source_id']+'/')])for r in parents]
        # Instance attributes are independent evidence, never replaced with ATTDEF text defaults.
        amap=request.get('attribute_tags',{})
        attribute_specs=[];attribute_levels=[];attribute_widths=[]
        attribute_width_factor=finite(request.get('attribute_width_factor_m',width_factor),'attribute_width_factor_m',positive=True)
        for attr in attribute_evidence:
            values=attr['instance_values']
            if amap.get('spec') in values:
                match=annotation_re.search(str(values[amap['spec']]))
                if match:attribute_specs.append((attr['source_id'],match))
            if amap.get('elevation') in values:
                raw=values[amap['elevation']]
                try: number=float(raw)
                except (ValueError,TypeError):c['problems'].append('实例标高属性不是唯一数值')
                else:
                    if not math.isfinite(number):raise ValueError('实例标高属性非有限数')
                    attribute_levels.append((attr['source_id'],number))
            if amap.get('width') in values:
                raw=values[amap['width']]
                try:number=float(raw)*attribute_width_factor
                except (ValueError,TypeError):c['problems'].append('实例宽度属性不是唯一正数')
                else:
                    if not math.isfinite(number)or number<=0:c['problems'].append('实例宽度属性不是唯一正数')
                    else:attribute_widths.append(dict(source_id=attr['source_id'],tag=amap['width'],raw_value=raw,
                        factor_to_m=attribute_width_factor,width_m=number,source_kind='instance_attribute'))
        if chosen:
            match=chosen['match'];groups=match.groupdict()
            spec=groups.get('spec') or match.group(0)
            if groups.get('width'):
                annotated_width=float(groups['width'])*width_factor
            if groups.get('elevation'):
                required(request.get('elevation_datum'),'elevation_datum')
                level=float(groups['elevation'])
        elif possible:
            c['problems'].append('仅有距离候选，无引线/明确绑定，不自动取最近规格')
        else:
            c['problems'].append('桥架规格无可绑定旁注')
        if len(attribute_specs)==1:
            attr_source,attr_match=attribute_specs[0];attr_spec=attr_match.groupdict().get('spec')or attr_match.group(0)
            if spec is not None and spec!=attr_spec:
                c['problems'].append('实例属性与旁注规格冲突')
            elif spec is None:
                spec=attr_spec;via='instance_attribute';binding_refs=[attr_source]
                if '桥架规格无可绑定旁注'in c['problems']:c['problems'].remove('桥架规格无可绑定旁注')
                if attr_match.groupdict().get('width'):annotated_width=float(attr_match.group('width'))*width_factor
        elif len(attribute_specs)>1:c['problems'].append('多层块实例属性规格多候选')
        if len(attribute_levels)==1:
            required(request.get('elevation_datum'),'elevation_datum')
            if level is not None and abs(level-attribute_levels[0][1])>ztol:c['problems'].append('实例属性与旁注标高冲突')
            else:level=attribute_levels[0][1]
        elif len(attribute_levels)>1:c['problems'].append('多层块实例标高多候选')
        width_attribute=attribute_widths[0]['width_m']if len(attribute_widths)==1 else None
        if len(attribute_widths)>1:c['problems'].append('多层块实例宽度多候选')
        if width_attribute is not None:
            if c['width_geometry_m']is not None and abs(width_attribute-c['width_geometry_m'])>tol:
                c['problems'].append('实例属性宽度与量得宽度冲突')
            if annotated_width is not None and abs(width_attribute-annotated_width)>tol:
                c['problems'].append('实例属性宽度与规格宽度冲突')
        known_widths=[v for v in (c['width_geometry_m'],annotated_width,width_attribute)if v is not None]
        width_conflict=bool(known_widths and max(known_widths)-min(known_widths)>tol)
        effective_width=known_widths[0]if known_widths and not width_conflict and len(attribute_widths)<=1 else None
        if effective_width is not None and c['width_geometry_status']=='missing'and(annotated_width is not None or width_attribute is not None)and'中心线边线宽度缺失/多候选'in c['problems']:
            c['problems'].remove('中心线边线宽度缺失/多候选')
        if annotated_width is not None and c['width_geometry_m'] is not None and abs(annotated_width-c['width_geometry_m'])>tol:
            c['problems'].append('量得宽度与标注宽度冲突')
        if ident in levels:
            levelrow=levels[ident]
            e.refs(levelrow.get('basis_source_ids'),'elevation_binding.basis_source_ids')
            required(levelrow.get('datum'),'elevation_binding.datum')
            proposed=float(levelrow['elevation_m'])
            if not math.isfinite(proposed):raise ValueError('标高非有限数')
            if level is not None and abs(level-proposed)>ztol:
                c['problems'].append('标注标高与显式标高冲突')
            else:level=proposed
        if level is None:c['problems'].append('工程标高/共同基准未定，CAD几何z不自动当设计标高')
        row=dict(c,id=ident,length_m=math.dist(c['start'],c['end']),spec=spec,
            width_attribute_m=width_attribute,effective_width_m=effective_width,attribute_width_evidence=attribute_widths,
            width_annotation_m=annotated_width,annotation_candidates=possible,
            annotation_source_id=chosen['source_id'] if chosen else None,
            annotation_binding=dict(via=via,basis_source_ids=binding_refs),elevation_m=level,
            block_attribute_evidence=attribute_evidence,
            elevation_binding=levels.get(ident),length_basis='network_model',quantity_level='规则推算量',
            status='REVIEW_REQUIRED' if c['problems'] else 'CANDIDATE',official_adoption=False)
        rows.append(row)
        for problem in c['problems']:todo.append(dict(problem=problem,segment_id=ident,source_ids=c['source_ids']))
    annotations=defaultdict(list)
    for row in rows:
        if row['annotation_source_id']:annotations[row['annotation_source_id']].append(row)
    for sid,bound in annotations.items():
        if len(bound)>1:
            for row in bound:
                row['problems'].append('同旁注指向多个物理段，需逐段复核');row['status']='REVIEW_REQUIRED'
                todo.append(dict(problem='同旁注指向多个物理段，需逐段复核',segment_id=row['id'],annotation_source_id=sid))
    unknown=(set(bindings)|set(levels))-{r['id']for r in rows}
    if unknown:raise ValueError('绑定引用未识别段:'+','.join(sorted(unknown)))
    if not rows:todo.append(dict(problem='范围内未形成桥架段候选，不是确认零量'))
    return finish(e,dict(schema='qto-cable-tray-candidates/1.0',segments=rows,
        coverage=dict(center_parts=len(centers),edge_parts=len(edges),unpaired_edge_parts=len(edges)-len(used),scope_records=scope_records,
            crossing_source_ids=sorted({r['source_id']for r in scope_records if r['scope_status']=='crosses_scope'}),
            outside_source_ids=sorted({r['source_id']for r in scope_records if r['scope_status']=='outside'})),
        meaning='桥架识别候选；弯通/竖向/支架不由平面线自动推数，确认物理身份与标高后才能沿网建模'),todo)


if __name__=='__main__':
    raise SystemExit(cli(calculate,__doc__))
