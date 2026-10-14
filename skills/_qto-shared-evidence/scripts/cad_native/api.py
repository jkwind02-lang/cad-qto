"""Shared adapter for the neutral extractor, context workbench and read-only CLI."""
from __future__ import annotations
import hashlib,json,weakref
from ezdxf.lldxf.tagwriter import TagCollector
from .reader import decode_entity,preserve_tags,is_native_entity,referenced_records

_CACHE=weakref.WeakKeyDictionary()


def read_native_entity(entity):
    if not is_native_entity(entity):return None
    doc=entity.doc
    if doc is None:raise ValueError('native object must have its source document for class resolution')
    tags=TagCollector.dxftags(entity,dxfversion=doc.dxfversion)
    identity={'tags':preserve_tags(tags),'classes':[(c.dxf.name,c.dxf.cpp_class_name) for c in doc.classes]}
    digest=hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
    key=str(entity.dxf.get('handle') or id(entity));cache=_CACHE.setdefault(doc,{})
    cached=cache.get(key)
    if cached is None or cached[0]!=digest:
        record=decode_entity(entity,doc);cache[key]=(digest,record)
    record=cache[key][1]
    # Symbol primitives are document dependencies, not part of the local proxy
    # payload signature. Refresh them even when the proxy itself is unchanged.
    referenced,blocks,relations,issues=referenced_records(doc,[record])
    symbol_refs={k:v for k,v in record['decoded'].get('named_properties',{}).items()
        if isinstance(v,dict) and v.get('handle') in blocks}
    record['basic_data']['native_symbol_geometry']={k:{'block_record':v['handle'],
        'base_point':blocks[v['handle']]['base_point'],'member_handles':blocks[v['handle']]['member_handles'],
        'primitive_records_path':'referenced_records','coordinates':'source block coordinates'} for k,v in symbol_refs.items()}
    record['reference_geometry']={'referenced_records':referenced,'referenced_blocks':blocks,'issues':issues}
    return record


def native_geometry_facts(entity):
    record=read_native_entity(entity);data=record['basic_data'];decoded=record['decoded']
    partial=decoded['status']!='SERIALIZATION_COMPLETE'
    usable=bool(decoded.get('properties_complete') or data['points'] or data['ports'] or data['texts'])
    anchor=None
    for key in ('text_position','pipe_fitting_position','pipe_attachment_position','Pt0','Start','pipe_prototype_start','line_stair_point_148'):
        p=data['points'].get(key)
        if p is not None:anchor=list(p) if len(p)==3 else [*p,0.];break
    issue_codes=[];issues=[]
    if record['class_name']=='UNRESOLVED_PROXY_CLASS':
        issue_codes=['entity_type_not_supported']
        issues=['代理类别未解析；原始标签和载荷保留，未取得的基础数据不写成零']
    elif partial:
        issue_codes=['native_schema_incomplete'];issues=['原生字段尚未全部解码：'+str(decoded.get('stopped_at',decoded.get('reason',decoded['status'])))]
    if data.get('invalid_coordinate_fields'):
        issue_codes.append('native_invalid_coordinate');issues.append('源数据包含不可用坐标，原始浮点位与其他已读字段保留')
    # Native point/curve fields are facts. They are not automatically converted
    # to a measurable centerline or to a complete physical surface.
    native={'class_name':record['class_name'],'class_id':record['class_id'],'cpp_class':record['cpp_class'],
        'read_status':decoded['status'],'read_state':record['read_state'],
        'basic_data':data,'decoded_fields':decoded.get('fields',{}),'references':record['references'],
        'reference_geometry':record['reference_geometry'],
        'stopped_at':decoded.get('stopped_at',decoded.get('reason',decoded['status'])) if partial else None,
        'geometry_semantics_complete':False,'decoder_baseline':'0.5.1-native-fields-inline'}
    path=data.get('native_path',{})
    return {'anchor':anchor,'start':path.get('start'),'end':path.get('end'),
        'bbox':None,'bbox3d':None,'length':None,'signature':None,'length_error_estimate':None,
        'geometry':None,'raw_geometry':{'entity_type':entity.dxftype(),'retained_payloads':record['retained_payloads']},
        'geometry_status':'requires_adapter' if usable else 'unsupported',
        'geometry_capability':'native_component_data' if usable else 'unsupported',
        'geometry_issue_codes':issue_codes,'geometry_issues':issues,
        'recommended_actions':['native_geometry_read_required'] if not usable else ['use_native_basic_data'],
        'insert_matrix_wcs':None,'extrusion':None,'external_reference':None,'native_data':native}
