#!/usr/bin/env python3
"""Source-bound 2-D registration and review candidates; never changes quantities or IDs."""
from __future__ import annotations
import argparse
from collections import defaultdict
import copy
import hashlib
import itertools
import json
import math
import struct
from pathlib import Path
import sys

import qto_cad_geometry as geometry
import qto_cad_source_contract as source_contract
from qto_segment_graph import row_parts
from qto_review_contract import normalize_view

SCHEMA = 'qto-cross-drawing-registration/1.0'
CONTEXT = ('object_type', 'version', 'floor', 'scope_id')


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def text(value, label):
    if not isinstance(value, str) or not value.strip(): raise ValueError(label + '须为非空文字')
    return value.strip()
def number(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): raise ValueError(label + '须为有限数')
    if minimum is not None and value < minimum: raise ValueError(label + '低于允许值')
    return float(value)
def metre_coordinates(point):
    if any(not math.isfinite(v) or abs(v)>1e9 for v in point): raise ValueError('米坐标超出有限数/绝对值1e9m数值范围')
    return point
def index(rows, key, label):
    if not isinstance(rows, list): raise ValueError(label + '须为数组')
    result = {}
    for row in rows:
        ident = text(row.get(key), label + '.' + key)
        if ident in result: raise ValueError(label + '身份重复: ' + ident)
        result[ident] = row
    return result
def stream(path):
    with path.open(encoding='utf-8-sig') as file:
        for line in file:
            if line.strip(): yield json.loads(line)


class Source:
    def __init__(self, raw, base):
        self.raw = copy.deepcopy(raw)
        for key in ('source_key', 'drawing_id', 'version', 'floor', 'scope_id'):
            text(raw.get(key), 'source.' + key)
        self.path = (base / text(raw.get('neutral_manifest'), 'neutral_manifest')).resolve()
        if sha(self.path) != text(raw.get('manifest_sha256'), 'manifest_sha256').lower(): raise ValueError('中性清单SHA不一致')
        self.manifest = manifest = load(self.path)
        if manifest.get('schema_version') != 'qto-cad-neutral-evidence/1.0' or manifest.get('geometry_schema_version') != geometry.GEOMETRY_SCHEMA or manifest.get('source_identity_issues'):
            raise ValueError('中性来源契约/完整WCS几何/源身份不满足')
        self.before = {str(self.path): sha(self.path)}
        authority = manifest.get('authoritative_source')
        source_contract.verify_source_file(authority, 'authoritative_source')
        self.before[str(Path(authority['path']).resolve())] = authority['sha256'].lower()
        exchange = manifest.get('exchange_dxf')
        if exchange is not None:
            source_contract.verify_source_file(exchange, 'exchange_dxf')
            self.before[str(Path(exchange['path']).resolve())] = exchange['sha256'].lower()
        self.raw['verified_source_files'] = dict(authoritative_source=copy.deepcopy(authority), exchange_dxf=copy.deepcopy(exchange))
        self.factor = number(raw.get('unit', {}).get('factor_to_m'), 'unit.factor_to_m', 0)
        if self.factor == 0: raise ValueError('unit.factor_to_m须大于0')
        text(raw.get('unit', {}).get('basis'), 'unit.basis')
        # Unknown engineering units need the existing declaration/corroboration workflow upstream.
        if not source_contract.unit_known(manifest.get('drawing_unit', {}).get('raw_unit')): raise ValueError('未知图纸单位不能用于米制配准；先补有证据的单位取证')
        source_contract.check_unit_factor(manifest['drawing_unit']['raw_unit'], 'm', self.factor)
        paths = {}
        for name in ('layout_entities', 'block_definitions'):
            artifact = manifest.get('artifacts', {}).get(name, {})
            path = (self.path.parent / text(artifact.get('path'), name + '.path')).resolve()
            if sha(path) != str(artifact.get('sha256', '')).lower(): raise ValueError(name + '源流SHA不一致')
            self.before[str(path)] = sha(path); paths[name] = path
        definitions = defaultdict(list)
        for row in stream(paths['block_definitions']):
            if row.get('row_type') == 'entity': definitions[str(row.get('parent_block') or '')].append(row)
        origins = {str(x['block_name']): x.get('origin', [0, 0, 0]) for x in manifest.get('blocks', [])}
        expand = set(raw.get('expand_blocks', [])); self.rows = {}; self.todo = []
        layout = text(raw.get('layout', 'Model'), 'layout')
        def walk(row, path, matrix=None, stack=()):
            actual = geometry.transform_row(row, matrix, path, layout) if matrix is not None else copy.deepcopy(row)
            actual['source_id'] = path
            # Identity matches the existing electrical object binding: a full nested path.
            actual['instance_path'] = actual.get('instance_path') or path
            if path in self.rows: raise ValueError('展开后源身份重复: ' + path)
            self.rows[path] = actual
            if row.get('entity_type') != 'INSERT': return
            name = str(row.get('block_name') or '')
            if name not in expand: return
            if name in stack or len(stack) >= 16 or name not in definitions: raise ValueError('展开块循环/超深/缺定义: ' + path)
            child_matrix = geometry.matrix_for_insert(row, origins.get(name, [0, 0, 0]))
            if matrix is not None: child_matrix = geometry.compose_matrix(matrix, child_matrix)
            for child in definitions[name]: walk(child, path + '/' + name + '/CAD-' + str(child['entity_handle']), child_matrix, (*stack, name))
        for row in stream(paths['layout_entities']):
            if row.get('row_type') == 'entity' and row.get('layout') == layout: walk(row, 'CAD-' + str(row['entity_handle']))

    def ref(self, raw):
        if raw.get('source_key') is not None and raw['source_key']!=self.raw['source_key']: raise ValueError('引用source_key与实际图源不一致')
        sid = text(raw.get('source_id'), '实体source_id')
        if sid not in self.rows: raise ValueError('无法回取完整源实体: ' + sid)
        row = self.rows[sid]
        if raw.get('instance_path') != row['instance_path']: raise ValueError('instance_path不匹配，禁止丢失嵌套身份')
        return row

    def evidence(self, ids):
        if not isinstance(ids, list) or not ids or any(s not in self.rows for s in ids): raise ValueError('工程依据须引用本图真实源实体')
        return copy.deepcopy(ids)

    def point(self, raw):
        row = self.ref(raw); locator = raw.get('locator')
        if locator == 'point' and all(k in row for k in ('x', 'y')): point = [row['x'], row['y'], row.get('z', 0)]
        elif locator in ('start', 'end'):
            parts, problem = row_parts(row)
            part_index = raw.get('part_index', 0)
            if isinstance(part_index, bool) or not isinstance(part_index, int) or part_index < 0 or problem or part_index >= len(parts) or parts[part_index]['kind'] != 'line': raise ValueError('控制点须为可回取直线端点')
            point = parts[part_index][locator]
        else: raise ValueError('locator须为可回取point/start/end')
        point = [number(v, '原始坐标') for v in point]
        return dict(source_key=self.raw['source_key'], source_id=row['source_id'], instance_path=row['instance_path'], locator=locator,
                    original_point=point, original_unit=copy.deepcopy(self.raw['unit']), point_m=metre_coordinates([v * self.factor for v in point]))

    def stable(self):
        if any(sha(p) != value for p, value in self.before.items()): raise ValueError('计算期间上游来源发生变化')


def transform(point, fit):
    x, y = point[:2]; c, s = math.cos(fit['rotation_rad']), math.sin(fit['rotation_rad'])
    return [c*x-s*y+fit['translation_m'][0], s*x+c*y+fit['translation_m'][1], *(point[2:] or [0.])]


def canonical_context_key(value):
    """Typed, recursive JSON identity matching review-app's contextValue.

    Objects ignore key order; arrays retain order and nesting. JSON numbers use browser
    Number/IEEE-754 identity (1 == 1.0 and -0 == 0), separately tagged from booleans.
    This key never replaces or changes the original business attribute value.
    """
    def typed(v):
        if v is None: return ['null']
        if isinstance(v,bool): return ['boolean',v]
        if isinstance(v,(int,float)):
            try: number=float(v)
            except OverflowError: number=-math.inf if v<0 else math.inf
            if number==0: number=0.
            return ['number',struct.pack('>d',number).hex()]
        if isinstance(v,str): return ['string',v]
        if isinstance(v,list): return ['array',[typed(x) for x in v]]
        if isinstance(v,dict):
            # JavaScript's sort compares UTF-16 code units, including non-BMP keys.
            keys=sorted(v,key=lambda k:k.encode('utf-16-be','surrogatepass'))
            return ['object',[[key,typed(v[key])] for key in keys]]
        raise ValueError('上下文须为JSON值')
    return json.dumps(typed(value),ensure_ascii=False,separators=(',',':'))


def context_present(value):
    return value is not None and not (isinstance(value,str) and not value.strip())


def annotate_number_drift(view):
    """Same-scene point warnings for existing comparison pages; no merger, ID or quantity mutation.

    Version/floor/scope conflicts remain visible as review differences. Missing context is named,
    so a colocation warning can never silently become a same-physical assertion.
    """
    units = {'m':.001,'mm':1.,'cm':.1}
    scenes={s['id']:s for s in view.get('scenes',[])}; rows=[]; grouped=defaultdict(list)
    for ob in view.get('objects',[]):
        ob.get('attributes',{}).pop('suspected_number_drifts',None)
        if ob.get('kind')=='point' and ob.get('points') and ob.get('scene_id') in scenes: grouped[ob['scene_id']].append(ob)
    for scene_id,objects in grouped.items():
        if scenes[scene_id].get('coordinate_unit') not in units: continue
        tolerance=units[scenes[scene_id]['coordinate_unit']]
        for a,b in itertools.combinations(objects,2):
            aa=a.get('attributes',{});ba=b.get('attributes',{})
            ta=aa.get('object_type') if context_present(aa.get('object_type')) else a.get('category')
            tb=ba.get('object_type') if context_present(ba.get('object_type')) else b.get('category')
            if a['id']==b['id'] or not context_present(ta) or canonical_context_key(ta)!=canonical_context_key(tb) or math.dist(a['points'][0],b['points'][0])>tolerance: continue
            row=dict(scene=scene_id,object_ids=[a['id'],b['id']],records=[a['catalog_key'],b['catalog_key']],object_type=ta,
                     context_conflicts=[k for k in ('version','floor','scope_id') if context_present(aa.get(k)) and context_present(ba.get(k)) and canonical_context_key(aa[k])!=canonical_context_key(ba[k])],
                     missing_context=[k for k in ('version','floor','scope_id') if not context_present(aa.get(k)) or not context_present(ba.get(k))],
                     state='candidate/unreviewed',automatic_merge_allowed=False,official=False,business_adoption=False)
            rows.append(row)
            for ob in (a,b): ob.setdefault('attributes',{}).setdefault('suspected_number_drifts',[]).append(copy.deepcopy(row))
    return rows


def line_candidate(pa,pb,tolerance):
    p,q=pa;r,s=pb;dx,dy=q[0]-p[0],q[1]-p[1];length=math.hypot(dx,dy)
    if length<=1e-12: return None
    offsets=[abs(dx*(x[1]-p[1])-dy*(x[0]-p[0]))/length for x in (r,s)]
    spans=sorted([((x[0]-p[0])*dx+(x[1]-p[1])*dy)/length for x in (r,s)])
    overlap=min(length,spans[1])-max(0.,spans[0])
    if max(offsets)<=tolerance and overlap>0: return max(offsets),overlap
    return None


def registration(raw, sources):
    akey = text(raw.get('from_source_key'), 'from_source_key'); bkey = text(raw.get('to_source_key'), 'to_source_key')
    a, b = sources[akey], sources[bkey]
    if akey == bkey: raise ValueError('配准需分别登记源图框/视图source_key')
    pairs = []
    for row in raw.get('control_pairs', []):
        text(row.get('basis'), '控制点对应basis')
        pairs.append(dict(from_ref=a.point(row['from_ref']), to_ref=b.point(row['to_ref']), basis=row['basis'],
                          from_basis_source_ids=a.evidence(row.get('from_basis_source_ids')), to_basis_source_ids=b.evidence(row.get('to_basis_source_ids'))))
    if len(pairs) < 2: raise ValueError('至少两个分别有来源的控制点对')
    ap = [p['from_ref']['point_m'] for p in pairs]; bp = [p['to_ref']['point_m'] for p in pairs]
    if len({tuple(p[:2]) for p in ap}) != len(ap) or len({tuple(p[:2]) for p in bp}) != len(bp): raise ValueError('重复控制点不能增加配准证据')
    model = raw.get('model', 'translation')
    if model not in ('translation', 'rigid_2d'): raise ValueError('仅支持translation或rigid_2d，不隐含缩放/镜像')
    ca = [math.fsum(p[i] for p in ap)/len(ap) for i in (0,1)]; cb = [math.fsum(p[i] for p in bp)/len(bp) for i in (0,1)]
    if max(math.dist(p[:2], ca) for p in ap) <= 1e-9 or max(math.dist(p[:2], cb) for p in bp) <= 1e-9: raise ValueError('控制点空间跨度退化')
    rotation = 0.
    if model == 'rigid_2d':
        if len(pairs) < 3: raise ValueError('rigid_2d至少三个控制点对，以保留残差检查')
        dot = math.fsum((p[0]-ca[0])*(q[0]-cb[0])+(p[1]-ca[1])*(q[1]-cb[1]) for p,q in zip(ap,bp))
        cross = math.fsum((p[0]-ca[0])*(q[1]-cb[1])-(p[1]-ca[1])*(q[0]-cb[0]) for p,q in zip(ap,bp))
        if math.hypot(dot,cross) <= 1e-18: raise ValueError('刚体配准旋转解退化')
        rotation = math.atan2(cross, dot)
    c, s = math.cos(rotation), math.sin(rotation)
    fit = dict(model=model, from_source_key=akey, to_source_key=bkey, rotation_rad=rotation,
               translation_m=[cb[0]-c*ca[0]+s*ca[1], cb[1]-s*ca[0]-c*ca[1]], coordinate_unit='m', scale=1.)
    residuals = [math.dist(transform(p, fit)[:2], q[:2]) for p,q in zip(ap,bp)]
    for pair, residual in zip(pairs, residuals): pair['residual_m'] = residual
    limit = number(raw.get('max_residual_m'), 'max_residual_m', 0)
    fit.update(control_pairs=pairs, rms_residual_m=math.sqrt(math.fsum(r*r for r in residuals)/len(residuals)), max_residual_m=max(residuals), allowed_residual_m=limit,
               accepted_for_candidate_search=max(residuals) <= limit, state='working/unreviewed', official=False, business_adoption=False)
    return fit


def resolve(document, base_dir=None):
    if document.get('schema') != SCHEMA: raise ValueError('schema须为' + SCHEMA)
    base = Path(base_dir or '.')
    sources = {k: Source(v, base) for k,v in index(document.get('sources'), 'source_key', 'sources').items()}
    fit = registration(document['registration'], sources)
    objects = []; seen_instances=set()
    for raw in index(document.get('objects'), 'observation_id', 'objects').values():
        key = text(raw.get('source_key'), 'object.source_key'); source = sources[key]; row = source.ref(raw)
        identity=(key,row['instance_path'])
        if identity in seen_instances: raise ValueError('同一source_key与完整instance_path重复观察；先统一源实体观察身份')
        seen_instances.add(identity)
        ob = copy.deepcopy(raw)
        for field in ('physical_id','parallel_count','elevation_m'): ob.setdefault(field,None)
        for field in ('version','floor','scope_id'):
            if raw.get(field) is not None and raw[field] != source.raw[field]: raise ValueError('对象'+field+'与实际图源版本/楼层/范围不一致，不能用对象覆盖图源上下文')
        for field in CONTEXT:
            ob[field] = raw.get(field, source.raw.get(field))
            if ob[field] is not None: text(ob[field], 'object.' + field)
        ob['object_id'] = text(raw.get('object_id'), 'object.object_id')
        ob['source_identity'] = dict(source_key=key, source_id=row['source_id'], instance_path=row['instance_path'], drawing_id=source.raw['drawing_id'],
                                     source_sha256=source.manifest['authoritative_source']['sha256'], manifest_sha256=sha(source.path))
        parts, problem = row_parts(row)
        if raw.get('kind') == 'line':
            if problem or len(parts) != 1 or parts[0]['kind'] != 'line': raise ValueError('当前跨图线对应仅支持单一直线，曲线/多段须分对象')
            points = [parts[0]['start'], parts[0]['end']]
            ob['original_length_m'] = math.dist(*points) * source.factor
            if not math.isfinite(ob['original_length_m']) or ob['original_length_m']<=1e-12: raise ValueError('线对象须具有正的有限原图长度')
        elif raw.get('kind') == 'point': points = [source.point(dict(raw, locator='point'))['original_point']]
        else: raise ValueError('kind须为point或line')
        ob['original_points'] = copy.deepcopy(points); ob['original_unit'] = copy.deepcopy(source.raw['unit'])
        points_m = [metre_coordinates([v*source.factor for v in p]) for p in points]
        if key == fit['from_source_key']: points_m = [transform(p, fit) for p in points_m]
        elif key != fit['to_source_key']: raise ValueError('本次对象须属于所配准的两个source_key')
        ob['comparison_points_m'] = points_m
        if 'physical_id' in raw: ob['physical_id'] = raw['physical_id']
        objects.append(ob)
    threshold = number(document.get('candidate_tolerance_m'), 'candidate_tolerance_m', 0)
    candidates = []
    for a,b in itertools.combinations(objects,2):
        if a['source_key'] == b['source_key'] or a['kind'] != b['kind']: continue
        # Different known categories are not evidence of one object; versions/floors remain visible as conflicts.
        if a.get('object_type') and b.get('object_type') and a['object_type'] != b['object_type']: continue
        pa,pb = a['comparison_points_m'],b['comparison_points_m']
        overlap=None
        if a['kind']=='point': distance = math.dist(pa[0][:2],pb[0][:2])
        else:
            candidate=line_candidate(pa,pb,threshold)
            if candidate is None: continue
            distance,overlap=candidate
        if not fit['accepted_for_candidate_search'] or distance > threshold: continue
        conflicts = [f for f in CONTEXT if a.get(f) and b.get(f) and a[f] != b[f]]
        missing = [f for f in CONTEXT if not a.get(f) or not b.get(f)]
        candidates.append(dict(candidate_id='CAND-'+str(len(candidates)+1).zfill(4), observation_ids=[a['observation_id'],b['observation_id']],
                               object_ids=[a['object_id'],b['object_id']], distance_m=distance, comparison_overlap_m=overlap, context_conflicts=conflicts, missing_context=missing,
                               suspected_number_drift=a['object_id']!=b['object_id'], state='candidate/unreviewed', same_physical_proven=False,
                               automatic_merge_allowed=False, official=False, business_adoption=False))
    byid = {o['observation_id']:o for o in objects}; pairings=[]
    # Explicit interpretations do not manufacture human approval or physical IDs.
    for row in index(document.get('interpretations', []), 'interpretation_id', 'interpretations').values():
        ids = row.get('observation_ids')
        if not isinstance(ids,list) or len(ids)!=2 or len(set(ids))!=2: raise ValueError('同物解释须引用两个不同观察')
        a,b = [byid[v] for v in ids]
        if a['kind']!=b['kind'] or a['source_key']==b['source_key']: raise ValueError('同物解释对象种类/视图不满足')
        if any(not a.get(f) or not b.get(f) or a[f]!=b[f] for f in CONTEXT): raise ValueError('同物解释类别/版本/楼层/范围须有值且相同')
        if not fit['accepted_for_candidate_search']: raise ValueError('配准残差未通过，不能交接同物工作解释')
        text(row.get('basis'), '同物工程对应basis')
        if row.get('decision_origin') != 'model': raise ValueError('这里只接受model工作解释；人工审批走既有凭据契约')
        refs=row.get('source_refs')
        if not isinstance(refs,list) or len(refs)!=2: raise ValueError('同物解释须分别绑定两侧真实实体及工程依据')
        for ob in (a,b):
            matched=[r for r in refs if r.get('observation_id')==ob['observation_id']]
            if len(matched)!=1: raise ValueError('同物解释缺失/重复一侧引用')
            ref=matched[0]
            if any(ref.get(k)!=ob.get(k) for k in ('source_key','source_id','instance_path')): raise ValueError('同物解释引用与原始观察不一致')
            sources[ob['source_key']].evidence(ref.get('basis_source_ids'))
        pairings.append(dict(interpretation_id=row['interpretation_id'],observation_ids=ids,basis=row['basis'],source_refs=copy.deepcopy(refs),
                             state='working/unreviewed', same_physical_proven=False, automatic_merge_allowed=False, official=False,business_adoption=False))
    unions = []
    for raw in document.get('line_union_requests', []):
        pairing=next((p for p in pairings if p['interpretation_id']==raw.get('interpretation_id')),None)
        if not pairing: raise ValueError('线并集须引用已通过范围门的显式同物工程解释')
        a,b=[byid[v] for v in pairing['observation_ids']]
        if a['kind']!='line' or b['kind']!='line': raise ValueError('线并集须为两个直线观察')
        for ob in (a,b):
            if type(ob.get('parallel_count')) is not int or ob['parallel_count']!=1: raise ValueError('线并集前必须证实单根；多根/未知不得并集扣量')
            if ob.get('elevation_m') is None: raise ValueError('线并集前必须明确标高；未知不是0')
            number(ob['elevation_m'], 'elevation_m')
            sources[ob['source_key']].evidence(ob.get('multiplicity_elevation_basis_source_ids'))
        if a['elevation_m']!=b['elevation_m']: raise ValueError('不同标高不得并集')
        tolerance=number(raw.get('collinear_tolerance_m'), 'collinear_tolerance_m', 0)
        p,q=a['comparison_points_m']; r,s=b['comparison_points_m']; dx,dy=q[0]-p[0],q[1]-p[1]; length=math.hypot(dx,dy)
        if length<=1e-12: raise ValueError('零长线不能并集')
        if any(abs(dx*(x[1]-p[1])-dy*(x[0]-p[0]))/length > tolerance for x in (r,s)): raise ValueError('线不共线；不按等长或近邻强行并集')
        spans=sorted([sorted([0.,length]), sorted([((x[0]-p[0])*dx+(x[1]-p[1])*dy)/length for x in (r,s)])]); merged=[]
        for lo,hi in spans:
            if merged and lo<=merged[-1][1]: merged[-1][1]=max(hi,merged[-1][1])
            else: merged.append([lo,hi])
        unions.append(dict(interpretation_id=pairing['interpretation_id'],comparison_union_length_m=sum(hi-lo for lo,hi in merged),
                           original_lengths_m=[a['original_length_m'],b['original_length_m']],intervals_m=merged,
                           state='working/reference_only', quantity_mutation_allowed=False,official=False,business_adoption=False))
    for source in sources.values(): source.stable()
    return dict(schema=SCHEMA,registration=fit,objects=objects,candidates=candidates,pairings=pairings,line_unions=unions,
                sources=[s.raw for s in sources.values()],provenance=dict(stable=True,fingerprints={p:v for s in sources.values() for p,v in s.before.items()}),
                status='REFERENCE_ONLY' if fit['accepted_for_candidate_search'] else 'REVIEW_REQUIRED',
                state='working/unreviewed',official=False,business_adoption=False,quantity_mutation_allowed=False)


def make_review(result, request):
    """Original scenes retain native units; registered display is a separate metre scene, without quantity."""
    sources = {s['source_key']:s for s in result['sources']}; objects=[]; scenes=[]
    def scene(ident,key,points,unit,title):
        xs=[p[0] for p in points];ys=[p[1] for p in points];span=max(max(xs)-min(xs),max(ys)-min(ys),1.);pad=span*.05
        return dict(id=ident,title=title,source_id=key,coordinate_unit=unit,y_axis='up',bounds=[min(xs)-pad,min(ys)-pad,max(xs)+pad,max(ys)+pad],background=[])
    grouped=defaultdict(list)
    for ob in result['objects']: grouped[ob['source_key']].append(ob)
    for key,rows in grouped.items():
        factor=sources[key]['unit']['factor_to_m'];unit={1:'m',.001:'mm',.01:'cm'}.get(factor,'CAD单位')
        scenes.append(scene('original|'+key,key,[p for o in rows for p in o['original_points']],unit,'原始坐标 · '+key))
    target=result['registration']['to_source_key'];aligned='registration|'+target
    scenes.append(scene(aligned,target,[p for o in result['objects'] for p in o['comparison_points_m']],'m','跨图配准工作对照（非原图量测）'))
    for ob in result['objects']:
        for is_aligned in (False,True):
            pts=ob['comparison_points_m'] if is_aligned else ob['original_points']; ck=('aligned|' if is_aligned else 'original|')+ob['observation_id']
            related=[c for c in result['candidates'] if ob['observation_id'] in c['observation_ids']]
            attrs={k:copy.deepcopy(ob.get(k)) for k in ('source_key','source_id','instance_path','physical_id','object_type','version','floor','scope_id','original_points','original_unit','original_length_m')}
            attrs.update(cross_drawing_candidates=related,coordinate_basis='registration_display_only' if is_aligned else 'original_WCS',official=False,business_adoption=False)
            objects.append(dict(id=ob['object_id'],catalog_key=ck,professional='qto-components-points',scene_id=aligned if is_aligned else 'original|'+ob['source_key'],
                                kind=ob['kind'],points=[p[:2] for p in pts],title=ob['object_id'],category='跨图对象复核',
                                detail='候选只供复核；原始源实体身份与坐标分别保留；不合并编号、不扣数量',source=json.dumps(ob['source_identity'],ensure_ascii=False),
                                status='疑似编号漂移，待复核' if any(c['suspected_number_drift'] for c in related) else '工作观察，待复核',quantities=[],attributes=attrs))
    view=dict(schema='qto-review-view/1.0',project_id=text(request.get('project_id'),'project_id'),review_id=text(request.get('review_id'),'review_id'),
              title=request.get('title','跨图配准与同物候选复核'),revision=request.get('revision','工作版'),
              sources=[dict(id=k,title=s['drawing_id']+' '+s['version'],sha256=s['manifest_sha256']) for k,s in sources.items()],
              scenes=scenes,objects=objects,groups=[],receipts={},official=False,business_adoption=False)
    annotate_number_drift(view)
    return normalize_view(view)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--build-review',action='store_true')
    args=parser.parse_args()
    try:
        request=load(args.input);result=resolve(request,args.input.resolve().parent)
        if args.output_dir.exists(): raise ValueError('输出目录已存在，请使用新版本名')
        args.output_dir.mkdir(parents=True)
        (args.output_dir/'跨图配准与候选.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.build_review:
            view=make_review(result,request);path=args.output_dir/'跨图复核视图.json';path.write_text(json.dumps(view,ensure_ascii=False,indent=2),encoding='utf-8')
            from build_qto_review_html import build
            build(path,args.output_dir/'跨图复核.html')
        print(json.dumps(dict(status=result['status'],candidates=len(result['candidates']),pairings=len(result['pairings']),official=False,business_adoption=False),ensure_ascii=False))
        return 0 if result['status']=='REFERENCE_ONLY' else 3
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='ERROR',error=str(exc),official=False,business_adoption=False),ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
