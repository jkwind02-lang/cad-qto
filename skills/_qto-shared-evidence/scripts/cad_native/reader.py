"""Read retained Tianzheng data without Tianzheng runtime modules or QTO rules.

Every proxy is inventoried before schema dispatch. Unsupported classes retain
their ordered DXF tags and original bytes. Decoding and retention are distinct.
"""
import base64,hashlib,json,math,struct,sys
from collections import Counter,deque
from pathlib import Path
from ezdxf import readfile
from ezdxf.math import Matrix44,Vec3
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.lldxf.types import is_pointer_code
from .native_decode import NativeReader,parse_native,SUPPORTED_CLASSES
from .basic_data import usable_data,transform_data
from .bitstream import ParseError

VERSION='0.5.2-native-fields-inline'  # 0.5.1 decoder; 0.5.2 only excludes AutoCAD internal proxy objects

def sha_file(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def encode_value(value):
    if isinstance(value,(bytes,bytearray)):
        return {'type':'bytes','base64':base64.b64encode(value).decode('ascii')}
    if isinstance(value,bool):return {'type':'bool','value':value}
    if isinstance(value,int):return {'type':'integer','decimal':str(value)}
    if isinstance(value,float):
        result={'type':'float64','bits_hex':struct.pack('>d',value).hex()}
        if math.isfinite(value):result['value']=value
        return result
    if isinstance(value,str):return {'type':'string','value':value}
    if value is None:return {'type':'null'}
    return {'type':'sequence','items':[encode_value(x) for x in value]}

def decode_value(encoded):
    kind=encoded['type']
    if kind=='bytes':return base64.b64decode(encoded['base64'],validate=True)
    if kind=='integer':return int(encoded['decimal'])
    if kind=='float64':return struct.unpack('>d',bytes.fromhex(encoded['bits_hex']))[0]
    if kind in ('bool','string'):return encoded['value']
    if kind=='null':return None
    if kind=='sequence':return [decode_value(x) for x in encoded['items']]
    raise ValueError('unsupported value encoding')

def preserve_tags(tags):
    result=[{'code':tag.code,'value':encode_value(tag.value)} for tag in tags]
    # Re-encoding verifies exact bytes, floating bits, integers, Unicode and tag order.
    assert result==[{'code':tag['code'],'value':encode_value(decode_value(tag['value']))} for tag in result]
    return result

def export_record(entity,doc):
    tags=TagCollector.dxftags(entity,dxfversion=doc.dxfversion)
    result={'handle':entity.dxf.handle,'type':entity.dxftype(),'owner':entity.dxf.get('owner'),
        'dxf_attributes':{k:encode_value(v) for k,v in entity.dxf.all_existing_dxf_attribs().items()},
        'ordered_tags':preserve_tags(tags),'tag_roundtrip_verified':True}
    def plain(v):
        if isinstance(v,(str,int,float,bool)) or v is None:return v
        if isinstance(v,(bytes,bytearray)):return encode_value(v)
        return [plain(x) for x in v]
    if entity.dxftype() not in ('ACAD_PROXY_ENTITY','ACAD_PROXY_OBJECT'):
        geometry={'type':entity.dxftype(),'attributes':{k:plain(v) for k,v in entity.dxf.all_existing_dxf_attribs().items()}}
        if entity.dxftype()=='LWPOLYLINE':geometry['vertices_xy_startwidth_endwidth_bulge']=[list(p) for p in entity.get_points('xyseb')]
        elif entity.dxftype()=='POLYLINE':geometry['vertices']=[{k:plain(v) for k,v in e.dxf.all_existing_dxf_attribs().items()} for e in entity.vertices]
        elif entity.dxftype()=='SPLINE':
            for key in ('control_points','fit_points','knots','weights'):geometry[key]=plain(getattr(entity,key))
        elif entity.dxftype()=='MTEXT':geometry['text']=entity.text
        result['native_primitive']=geometry
    return result

def retained_sections(tags):
    # Object owner/reactor links belong to the DXF envelope, not the native ID table.
    starts=[i for i,t in enumerate(tags) if t.code==100 and t.value in ('AcDbProxyObject','AcDbProxyEntity')]
    if starts:tags=tags[starts[-1]+1:]
    sections={};references=[];issues=[];i=0
    while i<len(tags):
        tag=tags[i]
        if tag.code in (160,161,162):
            marker=tag.code;declared=int(tag.value);expected=311 if marker==162 else 310;i+=1;chunks=[]
            while i<len(tags) and tags[i].code==expected:chunks.append(bytes(tags[i].value));i+=1
            if marker in sections:issues.append(f'duplicate section marker {marker}')
            data=b''.join(chunks)
            if declared<0 or (declared if marker==160 else (declared+7)//8)>len(data):issues.append(f'invalid declared length {marker}')
            # Full ordered tags above retain duplicates and original chunk boundaries.
            sections[marker]={'declared':declared,'data':data,'group_code':expected}
            continue
        if tag.code in (330,340,350,360):references.append({'group_code':tag.code,'handle':tag.value})
        i+=1
    return sections,references,issues

def common_properties(sections,references):
    numeric=sections.get(161)
    if not numeric:return {'status':'RAW_PRESERVED_NO_NUMERIC_STREAM','properties_complete':False}
    inline=162 not in sections;string=sections.get(162,{'data':b'','declared':0})
    if inline and numeric['data'][:2] not in (b'\x09\x10',b'\x0a\x11'):return {'status':'RAW_PRESERVED_UNKNOWN_PROFILE','properties_complete':False}
    try:
        r=NativeReader(numeric['data'],string['data'],numeric['declared'],string['declared'],references,inline)
        r.base();r.result.update(status='COMMON_FIELDS_READ_DERIVED_PENDING',base_complete=True,properties_complete=True)
        r.result['consumed']=[r.d.position()] if inline else [r.d.position(),r.s.position()]
        r.result['consumed_references']={'used':r.reference_pos,'available':len(references)}
        return r.result
    except (ParseError,ValueError,OverflowError) as error:
        result=r.result if 'r' in locals() else {}
        result.update(status='RAW_PRESERVED_SCHEMA_UNSUPPORTED',properties_complete=False,reason=str(error))
        return result

def axes(matrix):
    return {'origin':list(matrix.transform((0,0,0))),
        'axis_x':list(matrix.transform_direction((1,0,0))),'axis_y':list(matrix.transform_direction((0,1,0))),
        'axis_z':list(matrix.transform_direction((0,0,1)))}

def instance_records(doc,objects):
    result=[];issues=[];indexed={o['handle']:o for o in objects}
    def walk(entities,prefix,matrix,stack,layout):
        if len(stack)>64:issues.append({'path':prefix,'reason':'block recursion limit'});return
        for e in entities:
            h=e.dxf.handle
            if e.dxftype()=='INSERT':
                name=e.dxf.name;block=doc.blocks.get(name)
                if block is None or name in stack:issues.append({'path':prefix+[h],'reason':'missing or cyclic block'});continue
                rows,cols=e.dxf.row_count,e.dxf.column_count
                if rows*cols>100000:issues.append({'path':prefix+[h],'reason':'array expansion bound; original array retained'});continue
                for row in range(rows):
                    for col in range(cols):
                        insert=e.copy() if rows*cols>1 else e
                        if rows*cols>1:
                            offset=Vec3(col*e.dxf.column_spacing,row*e.dxf.row_spacing).rotate_deg(e.dxf.rotation)
                            insert.dxf.insert+=offset
                            for key in ('row_count','column_count','row_spacing','column_spacing'):insert.dxf.discard(key)
                        label=f'{h}[row={row},column={col}]' if rows*cols>1 else h
                        walk(block,prefix+[label],insert.matrix44()@matrix,stack+[name],layout)
            elif h in indexed:
                properties=indexed[h]['decoded'].get('named_properties',{})
                points={k:v for k,v in properties.items() if isinstance(v,list) and len(v)==3 and
                    (k in ('Start','End') or k.startswith('Pt'))}
                result.append({'instance_path':prefix+[h],'definition_handle':h,'layout':layout,'transform':axes(matrix),
                    'named_points_wcs':{k:list(matrix.transform(p)) for k,p in points.items()},
                    'native_geometry_wcs':transform_data(indexed[h].get('basic_data',usable_data('',indexed[h]['decoded'])),matrix)})
    for layout in doc.layouts:walk(layout,[],Matrix44(),[],layout.name)
    return result,issues

def referenced_records(doc,objects):
    known={o['handle'] for o in objects};queue=deque();records={};blocks={};relations=[];issues=[]
    for obj in objects:
        for ref in obj['references']:
            handle=ref['handle'];target=doc.entitydb.get(handle) if handle!='0' else None
            relations.append({'source':obj['handle'],**ref,'target_exists':target is not None,
                'target_type':target.dxftype() if target else None,'null_reference':handle=='0'})
            if target is not None:queue.append(handle)
            elif handle!='0':issues.append({'source':obj['handle'],'target':handle,'reason':'missing referenced record'})
    visited=set()
    while queue:
        handle=queue.popleft()
        if handle in visited or handle in known:continue
        visited.add(handle)
        if len(visited)>100000:issues.append({'reason':'referenced-record bound reached'});break
        entity=doc.entitydb.get(handle)
        if entity is None:issues.append({'target':handle,'reason':'nested record missing'});continue
        records[handle]=export_record(entity,doc)
        if entity.dxftype()=='BLOCK_RECORD':
            block=doc.blocks.get(entity.dxf.name)
            if block is None:issues.append({'target':handle,'reason':'block contents missing'});continue
            members=[block.block,*list(block),block.endblk]
            blocks[handle]={'name':entity.dxf.name,'base_point':list(block.block.dxf.base_point),
                'member_handles':[e.dxf.handle for e in members]}
            queue.extend(e.dxf.handle for e in members)
        if entity.dxftype()=='INSERT':
            block=doc.blocks.get(entity.dxf.name)
            if block is not None:queue.append(block.block_record_handle)
            else:issues.append({'target':handle,'reason':'nested INSERT block missing'})
        # Preserve extension dictionaries/xrecords, but do not climb owner pointers
        # into unrelated whole drawings or reinterpret soft links as geometry.
        for tag in TagCollector.dxftags(entity,dxfversion=doc.dxfversion):
            if is_pointer_code(tag.code) and tag.code!=330 and tag.value!='0':queue.append(tag.value)
    return records,blocks,relations,issues

INTERNAL_PROXY_CLASS_PREFIXES=('ACDB','ACAD_')


def proxy_class_name(entity,doc):
    """Registered class name of a proxy (code 91 -> CLASSES), without decoding its payload."""
    tags=getattr(entity,'acdb_proxy_entity',None)
    if tags is None:tags=TagCollector.dxftags(entity,dxfversion=doc.dxfversion)
    ids=[t.value for t in tags if t.code==91]
    class_id=ids[0] if len(ids)==1 and isinstance(ids[0],int) else None
    classes=list(doc.classes)
    cls=classes[class_id-500] if class_id is not None and 0<=class_id-500<len(classes) else None
    return cls.dxf.name if cls else None


def internal_proxy_object_class(entity):
    """AutoCAD's own support objects written as ACAD_PROXY_OBJECT by non-AutoCAD exporters.

    ODA writes dynamic-block and associativity support records (ACDB_DYNAMICBLOCKPURGEPREVENTER_VERSION,
    ACDBASSOCNETWORK, ACAD_EVALUATION_GRAPH, ...) as proxies; AutoCAD writes them under their real
    class names. They are never Tianzheng components, so they stay in class_catalog only. Unresolved
    classes are kept: an unknown payload is retained rather than dropped.
    """
    if entity.dxftype()!='ACAD_PROXY_OBJECT':return None
    doc=getattr(entity,'doc',None)
    if doc is None:return None
    name=proxy_class_name(entity,doc)
    if name and name.upper().startswith(INTERNAL_PROXY_CLASS_PREFIXES):return name
    return None


def is_native_entity(entity):
    if entity.dxftype().startswith('TCH_'):return True
    if entity.dxftype() not in ('ACAD_PROXY_ENTITY','ACAD_PROXY_OBJECT'):return False
    return internal_proxy_object_class(entity) is None


def decode_entity(e,doc):
    classes=list(doc.classes)
    record=export_record(e,doc)
    tags=getattr(e,'acdb_proxy_entity',None)
    if tags is None:tags=TagCollector.dxftags(e,dxfversion=doc.dxfversion)
    record['original_proxy_tags']=preserve_tags(tags)
    ids=[t.value for t in tags if t.code==91]
    class_id=ids[0] if len(ids)==1 and isinstance(ids[0],int) else None
    cls=classes[class_id-500] if class_id is not None and 0<=class_id-500<len(classes) else None
    name=cls.dxf.name if cls else e.dxftype() if e.dxftype().startswith('TCH_') else 'UNRESOLVED_PROXY_CLASS'
    record.update(class_name=name,class_id=class_id,cpp_class=cls.dxf.cpp_class_name if cls else None)
    try:
        sections,references,issues=retained_sections(tags)
    except (TypeError,ValueError) as error:
        sections,references,issues={},[],[str(error)]
    record['references']=references;record['container_issues']=issues
    record['retained_payloads']={str(k):{'declared':v['declared'],'group_code':v['group_code'],
        'physical_bytes':len(v['data']),'sha256':hashlib.sha256(v['data']).hexdigest(),
        'base64':base64.b64encode(v['data']).decode('ascii')} for k,v in sections.items()}
    if issues:decoded={'status':'RAW_PRESERVED_INVALID_CONTAINER','properties_complete':False,'issues':issues}
    elif name in SUPPORTED_CLASSES:decoded=parse_native(name,sections,references)
    elif name.startswith('TCH_'):decoded=common_properties(sections,references)
    else:decoded={'status':'RAW_PRESERVED_CLASS_UNRESOLVED','properties_complete':False}
    # Remove legacy business-status flags; all source fields and coordinates remain.
    decoded.pop('usable_for_quantity',None);decoded.pop('geometry_verified',None)
    candidate=decoded.pop('geometry_candidate',None)
    if candidate:
        record['native_geometry_fields']={k:v for k,v in candidate.items() if k not in ('quantity_allowed','meaning')}
    record['decoded']=decoded
    record['basic_data']=usable_data(name,decoded)
    record['read_state']={'object_record_present':True,'original_proxy_tags_roundtrip_verified':True,
        'properties_complete':bool(decoded.get('properties_complete')),
        'derived_serialization_complete':decoded.get('status')=='SERIALIZATION_COMPLETE',
        'physical_solid_reconstruction_required_for_reading':False}
    return record


def read_document(doc):
    from .api import read_native_entity
    classes=list(doc.classes)
    objects=[];excluded=Counter()
    for e in doc.entitydb.values():
        if not e.is_alive:continue
        internal=internal_proxy_object_class(e)
        if internal:excluded[internal]+=1;continue
        if is_native_entity(e):objects.append(read_native_entity(e))
    referenced,blocks,relations,reference_issues=referenced_records(doc,objects)
    for obj in objects:
        symbol_refs={key:value for key,value in obj['decoded'].get('named_properties',{}).items()
            if isinstance(value,dict) and value.get('handle') in blocks}
        obj['basic_data']['native_symbol_geometry']={key:{'block_record':ref['handle'],
            'base_point':blocks[ref['handle']]['base_point'],
            'member_handles':blocks[ref['handle']]['member_handles'],
            'primitive_records_path':'referenced_records', 'coordinates':'source block coordinates'} for key,ref in symbol_refs.items()}
    instances,instance_issues=instance_records(doc,objects)
    return {'schema':'tianzheng-foundation/0.5','reader_version':VERSION,
        'scope':'all proxy entities and TCH records, their occurrence transforms, retained payloads and referenced records; no quantity or viewport filtering; AutoCAD internal support objects written as ACAD_PROXY_OBJECT are counted but not retained',
        'class_catalog':[{'name':c.dxf.name,'cpp_class':c.dxf.cpp_class_name,'application':c.dxf.app_name} for c in classes],
        'excluded_internal_proxy_objects':{'rule':'ACAD_PROXY_OBJECT whose registered class name starts with ACDB or ACAD_ (dynamic-block, associativity and evaluation-graph support objects; ODA exports them as proxies)',
            'count':sum(excluded.values()),'classes':dict(sorted(excluded.items()))},
        'objects':objects,'instances':instances,'referenced_records':referenced,'referenced_blocks':blocks,
        'reference_relations':relations,'reference_issues':reference_issues,'instance_issues':instance_issues,
        'summary':{'object_records':len(objects),'classes':dict(Counter(o['class_name'] for o in objects)),
            'excluded_internal_proxy_objects':sum(excluded.values()),
            'decoded_statuses':dict(Counter(o['decoded']['status'] for o in objects)),
            'properties_complete':sum(o['read_state']['properties_complete'] for o in objects),
            'derived_serialization_complete':sum(o['read_state']['derived_serialization_complete'] for o in objects),
            'raw_records_roundtrip_verified':len(objects),'instances':len(instances),'referenced_records':len(referenced),
            'referenced_blocks':len(blocks),'reference_issues':len(reference_issues),'instance_issues':len(instance_issues)},
        'runtime_module_status':'see independent process audit receipt',
        'limitations':['保留原始载荷不等于已经理解或解码全部字段。','已转成普通图元、且原生载荷已丢失的图纸不能凭空还原天正属性。',
            '现阶段从保留原生数据的DXF读取；DWG容器导出仍通过现有CAD引擎。']}


def read_foundation(path,original_dwg=None,expected_exchange_sha=None,expected_original_sha=None):
    path=Path(path).resolve();before=sha_file(path);original_before=sha_file(original_dwg) if original_dwg else None
    if expected_exchange_sha and before!=expected_exchange_sha:raise ValueError('DXF identity mismatch')
    if expected_original_sha and original_before!=expected_original_sha:raise ValueError('DWG identity mismatch')
    doc=readfile(path);result=read_document(doc)
    result['input']={'exchange_path':str(path),'exchange_sha256':before,
        'original_dwg':str(Path(original_dwg).resolve()) if original_dwg else None,'original_sha256':original_before,
        'dxf_version':doc.dxfversion,'insunits_original':doc.header.get('$INSUNITS',0)}
    assert sha_file(path)==before
    if original_dwg:assert sha_file(original_dwg)==original_before
    result['inputs_unchanged']=True
    return result


def write_result(result,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    json.loads(path.read_bytes().decode('utf8',errors='strict'))
