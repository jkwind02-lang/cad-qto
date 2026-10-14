"""cad_native reader: AutoCAD internal support objects written as ACAD_PROXY_OBJECT are not inventoried.

Registered 20261015-0028: ODA writes dynamic-block / associativity support records
(ACDB_DYNAMICBLOCKPURGEPREVENTER_VERSION, ACDBASSOCNETWORK, ...) as proxies; the reader kept every
payload and recursed into referenced block records (1.9 GB on one sheet). Synthetic DXF only:
the proxy objects are injected as text because ezdxf has no constructor for ACAD_PROXY_OBJECT.
"""
import os
import sys
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFClass

SHARED = Path(os.environ.get('CAD_QTO_SHARED', Path(__file__).resolve().parents[1] / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(SHARED))
from cad_native import is_native_entity, read_document
from cad_native.reader import internal_proxy_object_class

INTERNAL = ('ACDB_DYNAMICBLOCKPURGEPREVENTER_VERSION', 'ACDBASSOCNETWORK', 'ACAD_EVALUATION_GRAPH')
PLUGIN = 'SOME_PLUGIN_OBJECT'


def register(doc, name, app, cpp):
    doc.classes.register(DXFClass.new(dxfattribs={'name': name, 'cpp_class_name': cpp, 'app_name': app,
                                                  'flags': 0, 'was_a_proxy': 1, 'is_an_entity': 0}))


def proxy_object(handle, class_id):
    return ('  0\nACAD_PROXY_OBJECT\n  5\n%s\n330\nC\n100\nAcDbProxyObject\n 90\n      499\n 91\n      %d\n'
            ' 93\n       16\n310\nFF00\n330\n0\n 94\n        0\n 95\n        0\n 70\n     0\n' % (handle, class_id))


def build(tmp_path):
    doc = ezdxf.new('R2018')
    for name in INTERNAL:
        register(doc, name, 'ObjectDBX Classes', 'AcDb' + name.title().replace('_', ''))
    register(doc, PLUGIN, 'ThirdPartyPlugin', 'ThirdPartyObject')
    doc.modelspace().add_line((0, 0), (1, 0))
    path = tmp_path / 'proxy.dxf'
    doc.saveas(path)
    names = [c.dxf.name for c in doc.classes]
    text = path.read_text(encoding='utf-8')
    tail = '  0\nENDSEC\n  0\nEOF\n'
    assert text.endswith(tail)
    body = ''.join(proxy_object('FA%02X' % index, 500 + names.index(name))
                   for index, name in enumerate([*INTERNAL, INTERNAL[0], PLUGIN], 1))
    path.write_text(text[:-len(tail)] + body + tail, encoding='utf-8')
    return ezdxf.readfile(path)


def test_internal_proxy_objects_are_counted_not_retained(tmp_path):
    doc = build(tmp_path)
    proxies = [e for e in doc.entitydb.values() if e.dxftype() == 'ACAD_PROXY_OBJECT']
    assert len(proxies) == 5
    classes = sorted(filter(None, (internal_proxy_object_class(e) for e in proxies)))
    assert classes == sorted([*INTERNAL, INTERNAL[0]])
    assert sum(is_native_entity(e) for e in proxies) == 1  # only the third-party plugin object

    result = read_document(doc)
    assert result['summary']['object_records'] == 1
    assert result['objects'][0]['class_name'] == PLUGIN
    assert result['objects'][0]['decoded']['status'] == 'RAW_PRESERVED_CLASS_UNRESOLVED'
    excluded = result['excluded_internal_proxy_objects']
    assert excluded['count'] == 4 and result['summary']['excluded_internal_proxy_objects'] == 4
    assert excluded['classes'] == {INTERNAL[0]: 2, INTERNAL[1]: 1, INTERNAL[2]: 1}
    # The class catalog still lists the excluded classes; nothing about them is lost from the drawing identity.
    assert {row['name'] for row in result['class_catalog']} >= set(INTERNAL)
    assert result['reader_version'].startswith('0.5.2')


def test_unresolved_proxy_object_is_still_retained(tmp_path):
    doc = ezdxf.new('R2018')
    doc.modelspace().add_line((0, 0), (1, 0))
    path = tmp_path / 'unresolved.dxf'
    doc.saveas(path)
    text = path.read_text(encoding='utf-8')
    tail = '  0\nENDSEC\n  0\nEOF\n'
    path.write_text(text[:-len(tail)] + proxy_object('FB01', 99999) + tail, encoding='utf-8')
    doc = ezdxf.readfile(path)
    result = read_document(doc)
    assert result['summary']['object_records'] == 1 and result['summary']['excluded_internal_proxy_objects'] == 0
    assert result['objects'][0]['class_name'] == 'UNRESOLVED_PROXY_CLASS'
