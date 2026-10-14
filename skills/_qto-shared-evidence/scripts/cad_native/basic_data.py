"""Expose decoded fields in usable form; no quantity or physical-solid inference."""
import math


def usable_data(name, decoded):
    fields=decoded.get('fields',{}); props=decoded.get('named_properties',{})
    result={'coordinate_basis':'definition coordinates in original drawing units',
        'parameters':dict(props),'entity_fields':fields.get('entity_fields',[]),
        'points':{},'vectors':{},'texts':{},'ports':[],'polylines':{},'invalid_coordinate_fields':{},
        'serialized_curves':decoded.get('serialized_extents_edges',[]),
        'decoding_complete':decoded.get('status')=='SERIALIZATION_COMPLETE'}
    for item in decoded.get('property_entries',[]):
        kind=item['type_tag'];key=item['name'];value=item['value']
        if kind in (0x4005,0x4006):result['points'][key]=value
        elif kind in (0x4007,0x4008):result['vectors'][key]=value
        elif kind in (8,0x4009):result['texts'][key]=value
    for item in fields.get('entity_fields',[]):
        # Keep separate field namespaces and ordered entries, including duplicates.
        result.setdefault('extended_parameters',{})[item['name']]=item['value']
    for key,value in fields.items():
        if isinstance(value,list) and any(isinstance(v,dict) and 'nonfinite_float64_bits_le' in v for v in value):
            result['invalid_coordinate_fields'][key]=value
        if isinstance(value,str) and ('text_' in key or key=='text_content') and not key.endswith(('context','version')):
            result['texts'][key]=value
        if isinstance(value,list) and len(value) in (2,3) and all(isinstance(v,(int,float)) for v in value):
            if 'direction' in key or 'vector_' in key:result['vectors'][key]=value
            elif 'point_' in key or key.endswith(('_start','_end','_position')):result['points'][key]=value
    count=fields.get('pipe_node_interface_count',0)
    for i in range(count):
        prefix=f'pipe_node_interface_{i}'
        if prefix+'_position' not in fields:continue
        result['ports'].append({'position':fields[prefix+'_position'], 'direction':fields.get(prefix+'_direction'),
            'connected_object':fields.get(prefix+'_reference'), 'source_prefix':prefix})
    if name=='TCH_PIPE' and 'pipe_prototype_end' in fields:
        result['native_path']={'start':fields['pipe_prototype_start'],'end':fields['pipe_prototype_end'],
            'native_curve_parameter':fields['pipe_prototype_scalar_110'],
            'source':'TDbPipePrototype: 0xe0/0xf8/0x110'}
        if fields['pipe_prototype_scalar_110']==0:
            result['native_path']['kind']='LINE'
        else:result['native_path']['kind']='NATIVE_CURVE_PARAMETER_UNMAPPED'
    elif name=='TCH_DBHVACDUCT' and 'Start' in props and 'End' in props:
        result['native_path']={'start':props['Start'],'end':props['End'],
            'native_curve_parameter':props.get('R23'), 'source':'named Start/End/R23'}
    if name in ('TCH_TEXT','TCH_MTEXT'):
        result['text']={'content':fields.get('text_content'),'position':fields.get('text_position'),
            'style':fields.get('text_style'),'source_formatting_preserved':True}
    if name=='TCH_MULTILEADER':
        result['leader']={'anchor':fields.get('leader_point_120'),'vertices':fields.get('leader_points',[]),
            'text_above':fields.get('leader_text_170'),'text_below':fields.get('leader_text_178')}
    for key in ('arrow_polyline','handrail_path','slab_outer'):
        if key in fields:result['polylines'][key]={'vertices':fields[key],
            'serialized_elevation':fields.get('slab_elevation') if key=='slab_outer' else None,
            'serialized_normal':fields.get('slab_normal') if key=='slab_outer' else None,
            'coordinate_basis':'native definition points; explicit elevation retained separately for slab XY'}
    for i in range(fields.get('slab_holes_count',0)):
        key=f'slab_hole_{i}'
        if key in fields:result['polylines'][key]={'vertices':fields[key],'serialized_elevation':fields.get('slab_elevation'),
            'serialized_normal':fields.get('slab_normal'),'coordinate_basis':'native definition XY and explicit elevation'}
    if name=='TCH_DBHEQUIPMENT':
        result['equipment']={'native_name':props.get('S4'),'native_model':props.get('S5'),
            'system':props.get('S3'),'position':props.get('Pt0'),
            'symbol_references':{k:props[k] for k in ('Id3','Id4','Id5') if k in props}}
    return result


def transform_data(data, matrix):
    def point(value):return list(matrix.transform(tuple(value) if len(value)==3 else (*value,0)))
    def vector(value):return list(matrix.transform_direction(tuple(value) if len(value)==3 else (*value,0)))
    result={'points':{k:point(v) for k,v in data['points'].items()},
        'vectors':{k:vector(v) for k,v in data['vectors'].items()},
        'ports':[dict(p,position=point(p['position']),direction=vector(p['direction']) if p['direction'] else None) for p in data['ports']],
        'serialized_curves':[{'owner':g['owner'],'curves':[dict(c,points=[point(p) for p in c['points']]) for c in g['curves']]} for g in data['serialized_curves']]}
    result['polylines']={}
    for key,line in data.get('polylines',{}).items():
        vertices=[]
        for item in line['vertices']:
            p=item['point'];elevation=line.get('serialized_elevation')
            if len(p)==2 and isinstance(elevation,(int,float)):p=[*p,elevation]
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p):continue
            vertices.append(dict(item,point=point(p)))
        # Bulge remains a SOURCE parameter: nonuniform transforms can yield ellipses.
        result['polylines'][key]={'vertices':vertices,'source_bulges_retained':True,
            'transformed_curve_kind_inferred':False,'definition_transform_supplied':True}
    result['invalid_coordinate_fields']=data.get('invalid_coordinate_fields',{})
    if data.get('native_path'):
        result['native_path']=dict(data['native_path'],start=point(data['native_path']['start']),end=point(data['native_path']['end']))
    if data.get('leader'):
        leader=data['leader'];result['leader']=dict(leader,anchor=point(leader['anchor']) if leader['anchor'] else None,
            vertices=[point(p) for p in leader['vertices']])
    return result
