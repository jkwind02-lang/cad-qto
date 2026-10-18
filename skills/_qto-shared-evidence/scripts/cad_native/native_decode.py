"""Versioned native fields, decoded without importing proprietary modules.

Schemas come from static dwgInFields / filer-vtable evidence. No offset scans,
coordinate fitting, class-name heuristics, or quantity rules are used.
"""
import sys, struct, math, re
from ezdxf.lldxf.encoding import decode_dxf_unicode
from pathlib import Path
from .legacy_schema import NativeReader as PreviousReader
from .bitstream import ParseError

OLD_CLASSES = {'TCH_DBHVACDUCT', 'TCH_DBHFITTING', 'TCH_DBHEQUIPMENT'}
SUPPORTED_CLASSES = OLD_CLASSES | {'TCH_HVACDIMENSION', 'TCH_TEXT', 'TCH_MTEXT',
    'TCH_MULTILEADER', 'TCH_PIPE', 'TCH_PIPEFITTING', 'TCH_PIPEVALVE', 'TCH_RADIATOR',
    'TCH_PIPEMULTDIM', 'TCH_RUPTURE', 'TCH_ARROW', 'TCH_DRAWINGINDEX', 'TDBINTERFACERECORD',
    'TCH_LINESTAIR','TCH_HANDRAIL','TCH_SLAB'}
EVIDENCE = {'module_sha256': '66ffd0c2185c01dc264c4345cd48839d2908b462347e53dfb7d7f547eea22572',
    'entity_fields': '0x5dbf0/0x5cf70', 'wrapper': '0x217b0',
    'point2_permutation': '0x22550', 'point3_permutation': '0x225d0/0x226e0',
    'base8_text': '0x1c4f0+0x217b0; TCH_TEXT/TU-separated/context0 only',
    'text': '0x55e30/0x562b0', 'leader': '0x13370/0x163e0',
    'pipe': '0x37f00/0x39200', 'pipe_node': '0x38970/0x38510',
    'pipe_fitting': '0x39c10', 'pipe_valve': '0x38dc0/0x39fa0',
    'radiator': '0x3ffa0/0x3c0e0', 'pipe_dimension': '0x43fc0',
    'hvac_dimension': '0x33490', 'rupture_arrow': '0x15ae0/0x17c50/0x1a570'}


class NativeReader(PreviousReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.legacy_inline:
            self.text=self.inline_text
        self.result['schema_evidence_v05'] = EVIDENCE
        self.nodes = 0

    def inline_text(self):
        count=self.d.bs();self.require(count<=65536,'legacy string count bound')
        data=self.d.octets(count)
        if not count:return ''
        self.require(data.endswith(b'\0'),'legacy TV string lacks terminal NUL')
        return decode_dxf_unicode(data[:-1].decode('cp936',errors='strict'))

    def coordinate(self):
        selector=self.d.uint(2)
        if selector in (1,2):return 1.0 if selector==1 else 0.0
        self.require(selector==0,'reserved coordinate double selector')
        raw=self.d.octets(8);value=struct.unpack('<d',raw)[0]
        if math.isfinite(value):return value
        return {'nonfinite_float64_bits_le':raw.hex(),'classification':'NaN' if math.isnan(value) else 'infinity',
            'usable_as_coordinate':False}

    def reference(self, code):
        value = self.object_id()
        self.require(value['group_code'] == code, f'expected reference {code}, got {value["group_code"]}')
        return value

    def encoded(self, kind, context, identifier=None):
        if context!=17:return super().encoded(kind,context,identifier)
        raw=self.text();self.require(len(raw)%2==0 and len(raw)>=2,'invalid encoded scalar string length')
        guid=self.guid if identifier is None else identifier
        digit=lambda i:int(guid[i]) if i<len(guid) and guid[i] in '0123456789' else 0
        seed=(100*digit(4)+10*digit(16)+digit(30))&255 if len(guid)>35 else 0
        key=223 if seed<100 else 198
        # 0x211b0 stores 0x9d2+seed (seed<100), whose HIGH byte is the XOR.
        xor=((0x9d2+seed) if seed<100 else 0x9d2)>>8
        value=''.join(chr(((26*ord(raw[i])-27*key+ord(raw[i+1]))&65535)^xor) for i in range(0,len(raw),2))
        value=value[1:];self.require(len(value)>=2,'context17 scalar lacks padding');value=value[:1]+value[2:]
        self.require(bool(re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',value)),'decoded scalar is not an exact numeric token')
        result=float(value) if kind=='double' else int(value)
        if kind=='double':self.require(math.isfinite(result),'decoded scalar is non-finite')
        if kind=='int':self.require(-(2**31)<=result<2**31,'encoded integer outside signed 32-bit width')
        if kind in ('byte','short'):self.require(0<=result<=(255 if kind=='byte' else 65535),'encoded integer outside declared width')
        return result

    def signed(self, bits, value):
        return value - (1 << bits) if value >= (1 << (bits-1)) else value

    def raw_double(self):
        value=struct.unpack('<d',self.d.octets(8))[0]
        self.require(math.isfinite(value),'non-finite raw double')
        return value

    def entity_fields(self, count=None, depth=0):
        if count is None: count = self.d.bl()
        self.require(depth <= 32 and 0 <= count <= 4096, 'entity field table exceeds bounds')
        result = []
        for _ in range(count):
            self.nodes += 1
            self.require(self.nodes <= 16384, 'total entity field count exceeds bound')
            before = [self.d.pos, self.s.pos, self.reference_pos]
            name = self.text(); kind = self.d.bs()
            readers = {0: lambda: bool(self.d.uint(1)), 1: lambda: self.signed(8, self.d.rc()),
                2: lambda: self.signed(8, self.d.rc()), 3: self.d.rc,
                4: lambda: self.signed(16, self.d.bs()), 5: self.d.bs,
                6: self.d.bl, 7: lambda: self.d.bl() & 0xffffffff,
                100: lambda: self.entity_fields(depth=depth+1), 101: self.text,
                200: self.d.bd, 201: lambda: [self.raw_double(), self.raw_double()],
                202: self.d.point3, 203: lambda: [self.raw_double(), self.raw_double()],
                204: self.d.point3, 205: self.d.point3,
                206: lambda: self.reference(360), 207: lambda: self.reference(350),
                208: lambda: self.reference(340), 209: lambda: self.reference(330)}
            self.require(kind in readers, f'unsupported entity field type {kind}: {name}')
            value = readers[kind]()
            result.append({'name': name, 'type': kind, 'value': value,
                'numeric_bits': [before[0], self.d.pos], 'string_bits': [before[1], self.s.pos],
                'reference_indices': [before[2], self.reference_pos]})
        return result

    def base(self, class_name=None):
        version = self.field('entity_version', self.d.rc)
        self.require(version in (8, 9, 10), f'unsupported base version {version}')
        if version == 8:
            # 0x217b0 reads no context byte below threshold 9; scalars use the raw filer.
            self.require(class_name == 'TCH_TEXT' and not self.legacy_inline,
                'unsupported base8 class/container profile')
            context = self.field('entity_context', lambda: 0)
            self.result['stream_profile'] = 'TU-separated-base8-context0; implicit context, no context byte'
        else:
            context = self.field('entity_context', self.d.rc)
        self.require((version, context) in ((8, 0), (9, 16), (10, 17)), 'unsupported base version/context')
        if version >= 10: self.guid = self.field('entity_format_identifier', self.text)
        for offset in ('10', '18'): self.field(f'entity_scalar_0x{offset}', lambda: self.encoded('double', context))
        flags = self.field('entity_reference_flags', lambda: self.encoded('byte', context))
        self.require(flags & ~3 == 0, 'unknown entity reference flag')
        for mask, adjustment, label in ((1, 5, 'first'), (2, 3, 'second')):
            if flags & mask:
                count = self.field(label+'_reference_count', lambda: self.encoded('byte', context))-adjustment
                self.require(0 <= count <= 64, 'base reference count outside bound')
                for i in range(count): self.field(f'{label}_reference_{i}',
                    (lambda: self.reference(330)) if version == 8 else self.object_id)
        count = self.field('entity_fields_count', self.d.bl)
        self.field('entity_fields', lambda: self.entity_fields(count))
        self.field('entity_property_map', self.datamap)
        self.field('entity_tail_string', self.text)
        if version > 9: self.field('entity_tail_byte', lambda: self.encoded('byte', context))

    def wrapper(self, label, maximum, threshold, short=False):
        version = self.field(label+'_version', self.d.bs if short else self.d.rc)
        self.require(0 <= version <= maximum, f'unsupported {label} version {version}')
        context = self.field(label+'_context', self.d.rc) if version >= threshold else 0
        self.require(context in (0, 16, 17), f'unsupported {label} context {context}')
        return version, context

    def point(self, n, context):
        # The native DWG filer stores 2D points as 2RD and 3D points as 3BD.
        values = [self.raw_double() if n==2 else self.coordinate() for _ in range(n)]
        if context >= 17: return values[::-1] if n == 2 else [values[2], values[0], values[1]]
        return values

    def read(self, name, kind, context=0, identifier=None):
        readers = {'text': self.text, 'point2': lambda: self.point(2, context),
            'point3': lambda: self.point(3, context), 'vector3': lambda: self.point(3, context),
            'bool': lambda: bool(self.encoded('int', context, identifier)) if context else bool(self.d.uint(1)),
            'softref': lambda: self.reference(330), 'hardref': lambda: self.reference(340)}
        return self.field(name, readers[kind] if kind in readers else lambda: self.encoded(kind, context, identifier))

    def sequence(self, label, specifications, context=0, identifier=None):
        return {name: self.read(label+'_'+name, kind, context, identifier) for name, kind in specifications}

    def text_entity(self, multiline=False):
        version, c = self.wrapper('text', 4, 3)
        self.sequence('text', [('style', 'hardref'), ('content', 'text'), ('position', 'point3'),
            ('scalar_108', 'double'), ('scalar_110', 'double'), ('alignment', 'byte')], c)
        if version > 1: self.read('text_flag_124', 'byte', c)
        if version > 3:
            self.read('text_reference_128', 'softref', c); self.read('text_reference_130', 'softref', c)
        if multiline:
            _, c = self.wrapper('mtext', 2, 2)
            self.read('mtext_scalar_140', 'double', c); self.read('mtext_scalar_148', 'double', c)

    def symb_text(self):
        v, c = self.wrapper('symb_text', 4, 4)
        self.read('symb_text_style_100', 'hardref', c)
        self.read('symb_text_scalar_e8', 'double', c)
        if v >= 2:
            self.read('symb_text_scalar_f0', 'double', c); self.read('symb_text_style_108', 'hardref', c)
        if v >= 3: self.read('symb_text_short_f8', 'short', c)

    def leader(self):
        self.symb_text(); v, c = self.wrapper('leader', 7, 7)
        self.sequence('leader', [('byte_114', 'byte'), ('point_120', 'point2'), ('scalar_158', 'double'),
            ('byte_168', 'byte'), ('text_170', 'text'), ('text_178', 'text')], c)
        count = self.read('leader_point_count', 'byte', c)
        self.field('leader_points', lambda: [self.point(2, c) for _ in range(count)])
        if v >= 2: self.read('leader_scalar_160', 'double', c)
        if v >= 3: self.sequence('leader', [('scalar_180','double'), ('byte_118','byte'), ('scalar_188','double')], c)
        if v >= 4: self.sequence('leader', [('byte_190','byte'), ('point_198','point2')], c)
        if v >= 5: self.sequence('leader', [('point_1a8','point3'), ('point_1c0','point3')], c)

    def water_pipe(self):
        _, c = self.wrapper('pipe_prototype', 2, 2)
        self.sequence('pipe_prototype', [('byte_120','byte'), ('byte_11c','byte'), ('byte_124','byte'),
            ('start','point3'), ('end','point3'), ('scalar_110','double'), ('text_128','text'),
            ('ref_130','softref'), ('byte_138','byte'), ('ref_140','softref'), ('byte_148','byte')], c)
        for label in ('150', '180'):
            if label == '180': self.read('pipe_prototype_char_178','byte',c)
            count = self.read('pipe_prototype_count_'+label, 'byte', c)
            self.field('pipe_prototype_refs_'+label, lambda: [self.reference(330) for _ in range(count)])
        self.sequence('pipe_prototype', [('ref_1a8','hardref'), ('ref_1b0','hardref'), ('scalar_1b8','double'),
            ('point_1c8','point3'), ('bool_1c0','bool')], c)
        v, c = self.wrapper('water_pipe', 4, 3)
        self.sequence('water_pipe', [('scalar_230','double'), ('scalar_248','double'), ('scalar_238','double'),
            ('scalar_240','double'), ('short_222','short'), ('text_228','text'), ('scalar_260','double'), ('scalar_268','double')], c)
        if v >= 2: self.sequence('water_pipe', [('scalar_250','double'), ('text_258','text')], c)
        if v >= 4:
            self.read('water_pipe_text_278','text',c)
            count = self.read('water_pipe_extra_count','byte',c)
            self.field('water_pipe_extras', lambda: [{'text':self.text(), 'byte':self.encoded('byte',c)} for _ in range(count)])

    def pipe_node(self):
        v = self.field('pipe_node_version', self.d.rc); self.require(v <= 1, 'unsupported pipe node version')
        self.read('pipe_node_kind', 'byte')
        count = self.read('pipe_node_interface_count', 'byte')
        for i in range(count):
            label = f'pipe_node_interface_{i}'
            v, c = self.wrapper(label, 4, 3)
            self.sequence(label, [('byte_8','byte'), ('byte_c','byte'), ('byte_10','byte'), ('byte_14','byte'),
                ('text_18','text'), ('reference','softref'), ('position','point3'), ('direction','vector3')], c, '')
            if v >= 2: self.read(label+'_text_58','text',c)
        self.read('pipe_node_text_110','text')

    def pipe_fitting(self):
        self.pipe_node(); _, c = self.wrapper('pipe_fitting', 2, 2)
        self.read('pipe_fitting_position','point3',c)

    def pipe_valve(self):
        _, c = self.wrapper('pipe_attachment', 2, 2)
        self.sequence('pipe_attachment', [('kind','byte'), ('position','point3'), ('direction','vector3'), ('reference','softref')], c)
        v, c = self.wrapper('pipe_valve', 3, 3)
        self.sequence('pipe_valve', [('ref_128','hardref'), ('ref_130','hardref'), ('text_138','text'),
            ('scalar_150','double'), ('scalar_148','double'), ('bool_140','bool'), ('byte_180','byte')], c)
        if v >= 2: self.sequence('pipe_valve', [('scalar_158','double'), ('scalar_160','double'),
            ('scalar_170','double'), ('scalar_168','double'), ('bool_144','bool')], c)

    def radiator(self):
        self.pipe_node(); _, c = self.wrapper('label_style', 3, 2)
        self.sequence('label_style', [('reference','hardref'), ('scalar_18','double'), ('scalar_20','double')], c, '')
        v, c = self.wrapper('radiator', 4, 3)
        self.sequence('radiator', [('point_150','point3'), ('point_1b0','point3'), ('scalar_168','double'),
            ('scalar_178','double'), ('scalar_180','double'), ('scalar_188','double'), ('scalar_170','double'),
            ('bool_198','bool'), ('bool_19c','bool'), ('byte_148','byte'), ('scalar_190','double'),
            ('byte_1d0','byte'), ('byte_1a0','byte'), ('ref_1a8','hardref'), ('scalar_1c8','double')], c)
        if v >= 2: self.sequence('radiator', [('scalar_1d8','double'), ('text_1e0','text'), ('text_1e8','text'),
            ('byte_1f0','byte'), ('scalar_1f8','double'), ('scalar_200','double'), ('scalar_208','double'), ('scalar_210','double')], c)
        if v >= 4: self.sequence('radiator', [('short_218','short'), ('short_21a','short'), ('text_220','text'), ('text_228','text')], c)

    def pipe_dimension(self):
        self.symb_text(); v, c = self.wrapper('pipe_dimension', 3, 3)
        count = self.read('pipe_dimension_ref_count','byte',c)
        self.field('pipe_dimension_refs',lambda:[self.reference(330) for _ in range(count)])
        self.sequence('pipe_dimension',[('scalar_138','double'),('point_118','point2'),('point_128','point2')],c)
        if v >= 2: self.sequence('pipe_dimension',[('short_112','short'),('text_168','text')],c)

    def rupture(self):
        _, c = self.wrapper('rupture',2,2)
        self.sequence('rupture',[('start','point2'),('end','point2'),('byte_108','byte')],c)

    def arrow(self):
        self.symb_text(); v,c = self.wrapper('arrow',6,6)
        self.sequence('arrow',[('kind','byte'),('text_118','text'),('point_128','point3')],c)
        # 0x1a570 receives the underlying filer, not the encoded wrapper.
        self.read('arrow_polyline_kind','byte')
        count=self.read('arrow_polyline_count','short'); self.require(count<=4096,'arrow point bound')
        self.field('arrow_polyline',lambda:[{'point':self.point(2,0),'bulge':self.d.bd()} for _ in range(count)])
        if v>=2: self.read('arrow_scalar_158','double',c)
        if v>=3: self.read('arrow_byte_160','byte',c)
        if v>=4: self.sequence('arrow',[('scalar_168','double'),('bool_170','bool'),('text_120','text'),('point_140','point3')],c)

    def drawing_index(self):
        self.symb_text();v,c=self.wrapper('drawing_index',3,3)
        self.sequence('drawing_index',[('point_118','point2'),('scalar_128','double'),('scalar_130','double'),
            ('text_138','text'),('text_140','text')],c)
        if v>=2:self.sequence('drawing_index',[('text_148','text'),('ref_158','hardref'),('scalar_160','double'),
            ('scalar_168','double'),('byte_150','byte'),('byte_178','byte'),('scalar_170','double')],c)
        if v>=3:self.read('drawing_index_scalar_180','double',c)

    def interface_record(self):
        v=self.field('interface_record_version',self.d.bs);self.require(v<=4,'unsupported interface record version')
        c=self.field('interface_record_context',self.d.rc) if 2<=v<4 else 0
        self.require(c in (0,16,17),'unsupported interface record context')
        count=self.read('interface_record_count','int',c,'');self.require(0<=count<=4096,'interface record count bound')
        for i in range(count):self.sequence(f'interface_record_{i}',[('point_0','point3'),('point_18','point3'),
            ('direction','vector3'),('ref_48','softref'),('ref_50','softref')],c,'')

    def polyline(self,label):
        self.read(label+'_kind','byte');count=self.read(label+'_count','short')
        self.require(count<=4096,'polyline point count outside bound')
        return self.field(label,lambda:[{'point':self.point(2,0),'bulge':self.d.bd()} for _ in range(count)])

    def line_stair(self):
        v,c=self.wrapper('stair',4,4)
        self.sequence('stair',[('short_e2','short'),('scalar_e8','double'),('byte_100','byte')],c)
        if v>=2:self.sequence('stair',[('scalar_f0','double'),('scalar_f8','double')],c)
        if v>=3:self.sequence('stair',[('int_104','int'),('scalar_108','double'),('scalar_110','double'),
            ('scalar_118','double'),('ref_128','hardref'),('ref_120','hardref')],c)
        v,c=self.wrapper('line_stair',3,3)
        self.sequence('line_stair',[('scalar_138','double'),('scalar_140','double'),('point_148','point3'),('scalar_160','double')],c)
        if v>=2:self.read('line_stair_scalar_168','double',c)

    def handrail(self):
        _,c=self.wrapper('poly_path',3,3);self.read('poly_path_reference','softref',c)
        v,c=self.wrapper('handrail',3,3);kind=self.read('handrail_kind','byte',c)
        if kind==100:self.read('handrail_style','hardref',c)
        self.read('handrail_path_kind','byte',c);count=self.read('handrail_path_count','short',c)
        self.require(count<=4096,'handrail point count bound')
        self.field('handrail_path',lambda:[{'point':self.point(3,c),'bulge':self.encoded('double',c)} for _ in range(count)])
        self.read('handrail_scalar_110','double',c)
        if v>=2:self.read('handrail_scalar_118','double',c)
        self.sequence('handrail',[('scalar_108','double'),('byte_f8','byte'),('byte_120','byte')],c)

    def slab(self):
        v,c=self.wrapper('slab',3,3)
        self.sequence('slab',[('scalar_e8','double'),('int_f0','int'),('elevation','double')],c)
        self.polyline('slab_outer')
        count=self.read('slab_holes_count','short',c);self.require(count<=4096,'slab hole count bound')
        for i in range(count):self.polyline(f'slab_hole_{i}')
        if v>=2:self.read('slab_normal','vector3',c)

    def parse(self, class_name):
        try:
            if class_name=='TDBINTERFACERECORD':self.interface_record()
            else:self.base(class_name); self.result['base_complete']=True
            if class_name=='TDBINTERFACERECORD':pass
            elif class_name in OLD_CLASSES:
                self.interfaces()
                if class_name=='TCH_DBHVACDUCT':
                    self.pipe(); v=self.field('hvac_duct_version',self.d.bs); self.require(v<=2,'unsupported HVAC duct version')
                elif class_name=='TCH_DBHFITTING': self.fitting()
                else: self.equipment()
            elif class_name=='TCH_HVACDIMENSION':
                v=self.field('hvac_dimension_version',self.d.bs); self.require(v<=3,'unsupported HVAC dimension version')
                if v==1: self.field('hvac_dimension_map',self.datamap)
            else:
                dispatch={'TCH_TEXT':lambda:self.text_entity(), 'TCH_MTEXT':lambda:self.text_entity(True),
                    'TCH_MULTILEADER':self.leader,'TCH_PIPE':self.water_pipe,'TCH_PIPEFITTING':self.pipe_fitting,
                    'TCH_PIPEVALVE':self.pipe_valve,'TCH_RADIATOR':self.radiator,'TCH_PIPEMULTDIM':self.pipe_dimension,
                    'TCH_RUPTURE':self.rupture,'TCH_ARROW':self.arrow,'TCH_DRAWINGINDEX':self.drawing_index,
                    'TCH_LINESTAIR':self.line_stair,'TCH_HANDRAIL':self.handrail,'TCH_SLAB':self.slab}
                self.require(class_name in dispatch,'unsupported derived native class'); dispatch[class_name]()
            self.result['status']='SERIALIZATION_COMPLETE'
        except (ParseError,ValueError,OverflowError) as error:
            self.result['status']='PARTIAL_UNSUPPORTED' if self.result['fields'] else 'UNSUPPORTED'
            self.result['stopped_at']={'reason':str(error),'stream':getattr(error,'stream',None),'bit':getattr(error,'bit',None)}
        self.result['trace']=self.trace
        self.result['consumed']=[self.d.position()] if self.legacy_inline else [self.d.position(),self.s.position()]
        self.result['consumed_references']={'used':self.reference_pos,'available':len(self.references)}
        if self.result['status']=='SERIALIZATION_COMPLETE' and (self.d.pos!=self.d.limit or self.s.pos!=self.s.limit or self.reference_pos!=len(self.references)):
            self.result['status']='PARTIAL_UNCONSUMED_TAIL'
        self.result['properties_complete']=bool(self.result.get('base_complete'))
        return self.result


def parse_native(class_name, sections, references=()):
    numeric=sections.get(161)
    if not numeric:return {'status':'UNSUPPORTED_CONTAINER','reason':'numeric stream missing','properties_complete':False}
    inline=162 not in sections; string=sections.get(162,{'data':b'','declared':0})
    if inline and numeric['data'][:2] not in (b'\x09\x10',b'\x0a\x11') and class_name!='TDBINTERFACERECORD':
        return {'status':'RAW_PRESERVED_UNKNOWN_PROFILE','properties_complete':False}
    try:
        r=NativeReader(numeric['data'],string['data'],numeric['declared'],string['declared'],references,inline)
        if inline:r.result['stream_profile']='TV-inline-cp936; native schema version/context validated separately'
        return r.parse(class_name)
    except ParseError as error:return {'status':'INVALID_CONTAINER','reason':str(error),'properties_complete':False}
