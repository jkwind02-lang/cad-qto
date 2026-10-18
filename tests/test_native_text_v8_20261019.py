"""Anonymous versioned TCH_TEXT payloads; no project data or proprietary runtime."""
from copy import deepcopy
import base64
import math
from pathlib import Path
import os
import struct
import sys

import ezdxf
import pytest
from ezdxf.entities import DXFClass
from ezdxf.lldxf.tags import Tags
from ezdxf.lldxf.types import DXFBinaryTag, DXFTag

SHARED = Path(os.environ.get('CAD_QTO_SHARED', Path(__file__).resolve().parents[1] / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(SHARED))
from cad_native.native_decode import parse_native
from cad_native import read_document, read_native_entity
from cad_native.api import native_geometry_facts


class Writer:
    def __init__(self):
        self.bits = ''

    def uint(self, value, width):
        self.bits += f'{value:0{width}b}'

    def octets(self, values):
        for value in values:
            self.uint(value, 8)

    def rc(self, value):
        self.uint(value, 8)

    def bs(self, value):
        if value == 0:
            self.uint(2, 2)
        elif value <= 255:
            self.uint(1, 2)
            self.rc(value)
        else:
            self.uint(0, 2)
            self.octets(struct.pack('<H', value))

    def bl(self, value):
        if value == 0:
            self.uint(2, 2)
        elif 0 < value <= 255:
            self.uint(1, 2)
            self.rc(value)
        else:
            self.uint(0, 2)
            self.octets(struct.pack('<i', value))

    def bd(self, value):
        self.uint(0, 2)
        self.octets(struct.pack('<d', value))

    def text(self, value, inline=False):
        raw = value.encode('cp936') + b'\0' if inline else value.encode('utf-16le')
        self.bs(len(raw) if inline else len(raw) // 2)
        self.octets(raw)

    def payload(self):
        bits = self.bits + '0' * (-len(self.bits) % 8)
        return {'data': int(bits or '0', 2).to_bytes(len(bits) // 8, 'big'), 'declared': len(self.bits)}


def fixture(version=8, inline=False, rich=False, text_version=2):
    """Write the documented call order rather than adapting a project payload."""
    d, strings = Writer(), Writer()
    s = d if inline else strings
    refs = []
    guid = 'anonymous-format-identifier-for-tests'

    def text(value):
        s.text(value, inline)

    def scalar(value, kind, context):
        if not context:
            {'byte': d.rc, 'double': d.bd}[kind](value)
            return
        token = str(value)
        key, xor = (65, 5) if context == 16 else (223, 9)
        if context == 17:
            token = '@' + token[:1] + '#' + token[1:]
        encoded = ''.join(chr(key + (ord(c) ^ xor) // 26) + chr(key + (ord(c) ^ xor) % 26) for c in token)
        text(encoded)

    def reference(code):
        refs.append({'group_code': code, 'handle': format(40 + len(refs), 'X')})

    context = {8: 0, 9: 16, 10: 17}[version]
    d.rc(version)
    if version >= 9:
        d.rc(context)
    if version >= 10:
        text(guid)
    scalar(2.75, 'double', context)
    scalar(-1.25, 'double', context)
    scalar(3 if rich else 0, 'byte', context)
    if rich:
        for adjustment in (5, 3):
            scalar(adjustment + 1, 'byte', context)
            reference(330)
    d.bl(2 if rich else 0)
    if rich:
        text('label'); d.bs(101); text('匿名扩展字段')
        text('ref'); d.bs(208); reference(340)
    text('TDBXDATAMAP_BLOCK_BEGIN_')
    if rich:
        text('Count'); d.bl(3); d.bl(-7)
        text('Factor'); d.bl(5); d.bd(6.25)
        text('Note'); d.bl(8); text('匿名属性')
        text('Owner'); d.bl(19); reference(330)
        text('Symbol'); d.bl(21); reference(340)
    text('TDBXDATAMAP_BLOCK_END_')
    text('anonymous-tail')
    if version >= 10:
        scalar(0, 'byte', context)
    d.rc(text_version)
    tc = 16 if text_version >= 3 else 0
    if text_version >= 3:
        d.rc(tc)
    reference(340)
    text('匿名文字：样本甲')
    for value in (21.5, -13.25, 4.0):
        d.bd(value)
    scalar(8.25, 'double', tc)
    scalar(0.125, 'double', tc)
    scalar(2, 'byte', tc)
    if text_version > 1:
        scalar(1, 'byte', tc)
    if text_version > 3:
        reference(330); reference(330)
    sections = {161: d.payload()}
    if not inline:
        sections[162] = strings.payload()
    return sections, refs


@pytest.mark.parametrize('version,inline', [(8, False), (9, True), (9, False), (10, False)])
def test_legal_version_profiles_consume_all_streams_and_references(version, inline):
    sections, refs = fixture(version, inline)
    original = deepcopy((sections, refs))
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'SERIALIZATION_COMPLETE', result
    assert result['properties_complete'] is True
    assert result['fields']['text_content'] == '匿名文字：样本甲'
    assert result['fields']['text_position'] == [21.5, -13.25, 4.0]
    assert result['fields']['entity_scalar_0x10'] == 2.75
    assert result['fields']['entity_scalar_0x18'] == -1.25
    assert all(s['bit'] == s['declared_bits'] for s in result['consumed'])
    assert result['consumed_references'] == {'used': 1, 'available': 1}
    assert parse_native('TCH_TEXT', sections, refs) == result
    assert (sections, refs) == original


def test_v8_implicit_context_consumes_no_byte_and_raw_base_fields_align():
    sections, refs = fixture(rich=True)
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'SERIALIZATION_COMPLETE', result
    trace = {item['field']: item for item in result['trace']}
    assert trace['entity_version']['numeric_bits'] == [0, 8]
    assert trace['entity_context']['value'] == 0
    assert trace['entity_context']['numeric_bits'] == [8, 8]
    assert trace['entity_scalar_0x10']['numeric_bits'] == [8, 74]
    assert [item['value'] for item in result['fields']['entity_fields']][:1] == ['匿名扩展字段']
    assert result['named_properties']['Count'] == -7
    assert result['named_properties']['Factor'] == 6.25
    assert result['named_properties']['Note'] == '匿名属性'
    assert result['consumed_references'] == {'used': 6, 'available': 6}


@pytest.mark.parametrize('text_version', [0, 1, 2, 3, 4])
def test_v8_supported_text_wrapper_versions_keep_their_own_context(text_version):
    sections, refs = fixture(text_version=text_version)
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'SERIALIZATION_COMPLETE', result
    assert result['fields']['text_version'] == text_version
    assert result['fields'].get('text_context', 0) == (16 if text_version >= 3 else 0)


@pytest.mark.parametrize('version', [0, 7, 11, 255])
def test_unknown_base_version_stops_before_fields(version):
    sections, refs = fixture()
    sections[161]['data'] = bytes([version]) + sections[161]['data'][1:]
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'PARTIAL_UNSUPPORTED'
    assert result['properties_complete'] is False
    assert result['consumed'][0]['bit'] == 8
    assert result['consumed'][1]['bit'] == 0
    assert result['consumed_references']['used'] == 0


@pytest.mark.parametrize('version', [9, 10])
def test_v9_v10_still_reject_wrong_explicit_context(version):
    sections, refs = fixture(version)
    sections[161]['data'] = sections[161]['data'][:1] + b'\0' + sections[161]['data'][2:]
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'PARTIAL_UNSUPPORTED'
    assert result['properties_complete'] is False


def test_v8_is_not_promoted_to_other_native_classes_or_inline_container():
    sections, refs = fixture()
    for cls in ('TCH_MTEXT', 'TCH_PIPE', 'TCH_DBHVACDUCT'):
        result = parse_native(cls, sections, refs)
        assert result['status'] == 'PARTIAL_UNSUPPORTED'
        assert result['properties_complete'] is False
        assert result['consumed'][0]['bit'] == 8
    sections, refs = fixture(inline=True)
    assert parse_native('TCH_TEXT', sections, refs)['status'] == 'RAW_PRESERVED_UNKNOWN_PROFILE'


@pytest.mark.parametrize('marker', [161, 162])
def test_truncated_declared_stream_never_reports_complete(marker):
    sections, refs = fixture()
    sections[marker]['declared'] -= 1
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'PARTIAL_UNSUPPORTED'
    assert 'truncated' in result['stopped_at']['reason']


@pytest.mark.parametrize('mode', ['missing', 'wrong_style', 'wrong_base'])
def test_reference_table_missing_or_wrong_kind_remains_partial(mode):
    sections, refs = fixture(rich=mode == 'wrong_base')
    if mode == 'missing':
        refs = []
    else:
        refs[0]['group_code'] = 350
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'PARTIAL_UNSUPPORTED'
    assert 'reference' in result['stopped_at']['reason'] or 'object-ID' in result['stopped_at']['reason']


@pytest.mark.parametrize('tail', [161, 162, 'reference'])
def test_unconsumed_tail_keeps_partial_serialization(tail):
    sections, refs = fixture()
    if tail == 'reference':
        refs.append({'group_code': 340, 'handle': 'FF'})
    else:
        sections[tail]['data'] += b'\0'
        sections[tail]['declared'] += 1
    result = parse_native('TCH_TEXT', sections, refs)
    assert result['status'] == 'PARTIAL_UNCONSUMED_TAIL'
    assert result['fields']['text_content'] == '匿名文字：样本甲'


def test_v8_added_context_byte_or_changed_v9_header_cannot_fake_completion():
    sections, refs = fixture()
    sections[161]['data'] = sections[161]['data'][:1] + b'\x10' + sections[161]['data'][1:]
    sections[161]['declared'] += 8
    assert parse_native('TCH_TEXT', sections, refs)['status'] != 'SERIALIZATION_COMPLETE'
    sections, refs = fixture(9)
    sections[161]['data'] = b'\x08' + sections[161]['data'][1:]
    assert parse_native('TCH_TEXT', sections, refs)['status'] != 'SERIALIZATION_COMPLETE'


def test_public_v8_nested_instances_preserve_text_points_and_raw_payload():
    doc = ezdxf.new('R2018')
    doc.classes.register(DXFClass.new(dxfattribs={'name': 'TCH_TEXT', 'cpp_class_name': 'TDbText',
        'app_name': 'Anonymous', 'flags': 0, 'was_a_proxy': 1, 'is_an_entity': 1}))
    block = doc.blocks.new('匿名文字块')
    sections, refs = fixture()
    entity = block.new_entity('ACAD_PROXY_ENTITY', {})
    tags = [DXFTag(100, 'AcDbProxyEntity'), DXFTag(90, 498),
        DXFTag(91, 500 + [c.dxf.name for c in doc.classes].index('TCH_TEXT'))]
    for marker, data in sections.items():
        tags.extend([DXFTag(marker, data['declared']), DXFBinaryTag(310 if marker == 161 else 311, data['data'])])
    tags.append(DXFTag(340, '0'))
    entity.acdb_proxy_entity = Tags(tags)
    outer = doc.blocks.new('匿名父块')
    outer.add_blockref('匿名文字块', (7, 9, 2), dxfattribs={'rotation': 30, 'xscale': 2, 'yscale': 3})
    doc.modelspace().add_blockref('匿名父块', (80, 60, 5), dxfattribs={'rotation': 90,
        'row_count': 2, 'column_count': 2, 'row_spacing': 12, 'column_spacing': 15})
    result = read_native_entity(entity)
    assert result['decoded']['status'] == 'SERIALIZATION_COMPLETE', result
    assert result['basic_data']['text']['content'] == '匿名文字：样本甲'
    assert result['tag_roundtrip_verified'] is True
    for marker, data in sections.items():
        assert result['retained_payloads'][str(marker)]['declared'] == data['declared']
        assert base64.b64decode(result['retained_payloads'][str(marker)]['base64']) == data['data']
    native = read_document(doc)
    assert len(native['instances']) == 4
    assert len({tuple(i['instance_path']) for i in native['instances']}) == 4
    assert all(i['native_geometry_wcs']['points']['text_position'] for i in native['instances'])
    assert len({tuple(i['native_geometry_wcs']['points']['text_position']) for i in native['instances']}) == 4
    angle = math.radians(30)
    inner_x = 7 + 2 * 21.5 * math.cos(angle) - 3 * -13.25 * math.sin(angle)
    inner_y = 9 + 2 * 21.5 * math.sin(angle) + 3 * -13.25 * math.cos(angle)
    expected = [80 - inner_y, 60 + inner_x, 5 + 2 + 4]
    assert any(i['native_geometry_wcs']['points']['text_position'] == pytest.approx(expected) for i in native['instances'])
    assert read_document(doc) == native
    assert result['basic_data']['coordinate_basis'] == 'definition coordinates in original drawing units'
    facts = native_geometry_facts(entity)
    assert facts['length'] is None and facts['geometry'] is None
    assert facts['native_data']['geometry_semantics_complete'] is False
