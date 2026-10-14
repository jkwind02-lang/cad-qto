"""Public native-reader integration with generated data, no project fixtures."""
from pathlib import Path
import hashlib,json,os,re,struct,subprocess,sys
import ezdxf,pytest
from ezdxf.entities import DXFClass
from ezdxf.lldxf.tags import Tags
from ezdxf.lldxf.types import DXFTag,DXFBinaryTag

SHARED=Path(os.environ.get('CAD_QTO_SHARED',Path(__file__).resolve().parents[1]/'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0,str(SHARED))
from cad_native import read_native_entity,read_document
from qto_cad_geometry import entity_geometry,transform_point
from qto_recognition_workbench import build_index
import extract_cad_neutral as extract


class Bits:
    def __init__(self):self.value=''
    def uint(self,value,width):self.value+=f'{value:0{width}b}'
    def octets(self,data):
        for value in data:self.uint(value,8)
    def rc(self,value):self.uint(value,8)
    def bs(self,value):self.uint(1,2);self.rc(value)
    def bd(self,value):self.uint(0,2);self.octets(struct.pack('<d',value))
    def text(self,value):
        data=value.encode('cp936')+b'\0';self.bs(len(data));self.octets(data)
    def encoded(self,value):
        raw=''.join(chr(65+(ord(c)^5)//26)+chr(65+(ord(c)^5)%26) for c in value)
        self.text(raw)
    def payload(self):
        padded=self.value+'0'*((-len(self.value))%8)
        return int(padded,2).to_bytes(len(padded)//8,'big')


def native_text(space,text='示例文字',symbol=None):
    doc=space.doc
    if 'TCH_TEXT' not in doc.classes.classes:
        doc.classes.register(DXFClass.new(dxfattribs={'name':'TCH_TEXT','cpp_class_name':'TDbText',
            'app_name':'Synthetic','flags':0,'was_a_proxy':1,'is_an_entity':1}))
    names=[c.dxf.name for c in doc.classes]
    w=Bits();w.rc(9);w.rc(16)
    for value in ('1.0','0.0','0'):w.encoded(value)
    w.uint(2,2)
    w.text('TDBXDATAMAP_BLOCK_BEGIN_')
    if symbol:
        w.text('Id3');w.uint(1,2);w.rc(21)
    for value in ('TDBXDATAMAP_BLOCK_END_',''):w.text(value)
    w.rc(2);w.text(text)
    for value in (10.,20.,0.,3.5,0.):w.bd(value)
    w.rc(0);w.rc(0)
    e=space.new_entity('ACAD_PROXY_ENTITY',{})
    e.acdb_proxy_entity=Tags([DXFTag(100,'AcDbProxyEntity'),DXFTag(90,498),
        DXFTag(91,500+names.index('TCH_TEXT')),DXFTag(161,len(w.value)),
        DXFBinaryTag(310,w.payload()),*([DXFTag(340,symbol)] if symbol else []),DXFTag(340,'0')])
    return e


def identity(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def test_public_adapter_decodes_fields_without_quantity_geometry():
    doc=ezdxf.new('R2018');e=native_text(doc.modelspace())
    record=read_native_entity(e)
    assert record['class_name']=='TCH_TEXT'
    assert record['decoded']['status']=='SERIALIZATION_COMPLETE',record['decoded']
    row=extract.convert(e,layout='Model',parent_block=None,source_hash='a'*64,raw_unit='Unitless',sequence=1)
    assert row['native_data']['basic_data']['text']['content']=='示例文字'
    assert row['entity_handle']==e.dxf.handle
    assert row['raw_length'] is None and row['geometry'] is None
    assert row['native_data']['geometry_semantics_complete'] is False
    assert read_document(doc)['summary']['instances']==1


def test_cache_invalidates_after_source_tags_change():
    doc=ezdxf.new('R2018');e=native_text(doc.modelspace())
    assert read_native_entity(e)['decoded']['status']=='SERIALIZATION_COMPLETE'
    e.acdb_proxy_entity[4]=DXFBinaryTag(310,b'\xff')
    assert read_native_entity(e)['decoded']['status']!='SERIALIZATION_COMPLETE'


def test_reference_geometry_refreshes_without_local_proxy_changes():
    doc=ezdxf.new('R2018');symbol=doc.blocks.new('原生符号')
    line=symbol.add_line((0,0),(3,4));e=native_text(doc.modelspace(),symbol=symbol.block_record_handle)
    first=entity_geometry(e)['native_data']
    assert line.dxf.handle in first['basic_data']['native_symbol_geometry']['Id3']['member_handles']
    old=first['reference_geometry']['referenced_records'][line.dxf.handle]
    line.dxf.end=(30,40,0)
    new=entity_geometry(e)['native_data']['reference_geometry']['referenced_records'][line.dxf.handle]
    assert old!=new


def test_nested_rotated_scaled_array_positions_use_both_matrix_conventions(tmp_path):
    doc=ezdxf.new('R2018');inner=doc.blocks.new('内块');e=native_text(inner)
    outer=doc.blocks.new('外块');outer.add_blockref('内块',(7,8,9),dxfattribs={'rotation':30,'xscale':2,'yscale':3})
    doc.modelspace().add_blockref('外块',(100,200,30),dxfattribs={'rotation':90,'row_count':2,'column_count':2,'row_spacing':0,'column_spacing':40})
    path=tmp_path/'nested.dxf';doc.saveas(path)
    native=read_document(ezdxf.readfile(path));index=build_index(identity(path))
    active={tuple(re.sub(r'\[row=(\d+),column=(\d+)\]',r'[\1,\2]',p) for p in i['instance_path']):i for i in native['instances']}
    rows=[r for r in index['objects'] if r.get('native_data')]
    assert len(rows)==len(active)==4
    for row in rows:
        instance=active[tuple(row['source_path'])]
        expected=instance['native_geometry_wcs']['points']['text_position']
        assert row['native_geometry_wcs']['points']['text_position']==pytest.approx(expected)
        assert row['anchor']==pytest.approx(expected)
        assert expected==pytest.approx(transform_point([10,20,0],row['instance_transform']))
        assert row['raw_length'] is None and row['bbox'] is None
        assert row['text']=='示例文字'
    assert len({r['object_id'] for r in rows})==4


def test_unknown_native_keeps_bytes_and_diagnostic():
    doc=ezdxf.new('R2018');e=doc.modelspace().new_entity('ACAD_PROXY_ENTITY',{})
    e.acdb_proxy_entity=Tags([DXFTag(100,'AcDbProxyEntity'),DXFTag(90,498),DXFTag(91,99999),
        DXFTag(161,16),DXFBinaryTag(310,b'\xff\0')])
    facts=entity_geometry(e)
    assert facts['geometry_issue_codes']==['entity_type_not_supported']
    assert facts['native_data']['stopped_at']=='RAW_PRESERVED_CLASS_UNRESOLVED'
    assert read_document(doc)['summary']['raw_records_roundtrip_verified']==1


def test_read_only_cli_and_overwrite_protection(tmp_path):
    doc=ezdxf.new('R2018');native_text(doc.modelspace());doc.modelspace().add_line((0,0),(3,4))
    source=tmp_path/'source.dxf';doc.saveas(source);before=identity(source)
    out=tmp_path/'读取成果'
    command=[sys.executable,'-B',str(SHARED/'read_cad_basic.py'),'--source-dxf',str(source),'--output-dir',str(out)]
    process=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf8')
    assert process.returncode in (0,3),process.stderr
    receipt=json.loads((out/'CAD基础读取回执.json').read_text(encoding='utf8'))
    assert receipt['calculation_performed'] is False
    assert receipt['active_native_fully_decoded_instances']==1
    assert receipt['runtime_audit']['reader_guard']['blocked_attempts']==[]
    assert receipt['inputs_unchanged'] is True and identity(source)==before
    assert not list(out.glob('*工程量*'))
    blocked=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True)
    assert blocked.returncode==2 and identity(source)==before
