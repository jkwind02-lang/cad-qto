"""Repeated attribute tags preserve source identity across nested array cells."""
import hashlib
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
import qto_recognition_workbench as w


@pytest.mark.parametrize('array', [False, True])
@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('coincident', [False, True])
def test_repeated_tags_are_not_entity_identity(tmp_path, array, nested, coincident):
    doc = ezdxf.new()
    block = doc.blocks.new('device')
    block.add_line((0, 0), (100, 0))
    owner = doc.blocks.new('container') if nested else doc.modelspace()
    ins = owner.add_blockref('device', (500, 700), dxfattribs={
        'row_count': 2 if array else 1, 'column_count': 2 if array else 1,
        'row_spacing': 200, 'column_spacing': 300, 'rotation': 31})
    attrs = [ins.add_attrib('SAME', value, (500 if coincident else 500 + n * 10, 710))
             for n, value in enumerate(['alpha', 'beta', 'gamma'])]
    if nested:
        doc.modelspace().add_blockref('container', (9000, 12000),
                                     dxfattribs={'rotation': 53, 'xscale': 2, 'yscale': 2})
    path = tmp_path / 'repeated-tags.dxf'
    doc.saveas(path)
    source = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    index = w.build_index(source)
    rows = [r for r in index['objects'] if r['entity_type'] == 'ATTRIB']
    cells = 4 if array else 1
    assert len(rows) == 3 * cells
    assert len({r['object_id'] for r in rows}) == len(rows)
    for attr in attrs:
        selected = [r for r in rows if r['source_handle'] == attr.dxf.handle]
        assert len(selected) == cells
        assert all(r['text'] == attr.dxf.text for r in selected)
        assert all(r['source_path'][-1] == attr.dxf.handle for r in selected)
    devices = [r for r in index['objects'] if r['block_name'] == 'device']
    assert len(devices) == cells
    for row in devices:
        assert row['duplicate_attribute_tags'] == ['SAME']
        assert [a['source_handle'] for a in row['attribute_instances']] == [a.dxf.handle for a in attrs]
        assert [a['text'] for a in row['attribute_instances']] == ['alpha', 'beta', 'gamma']
        children = [r for r in rows if r['parent_object_id'] == row['object_id']]
        assert len(children) == 3
        assert len({tuple(r['anchor']) for r in children}) == (1 if coincident else 3)
        if not coincident:
            # Independent coordinate oracle uses the source attachment and parent
            # transform; array offsets are resolved in the INSERT owner's WCS.
            from ezdxf.math import Vec3
            r, c = row['array_cell'] or (0, 0)
            offset = Vec3(c * 300, r * 200).rotate_deg(31)
            matrix = row['instance_transform']
            from qto_cad_geometry import transform_point
            for child in children:
                original = next(a for a in attrs if a.dxf.handle == child['source_handle'])
                expected = transform_point(list(original.dxf.insert + offset), matrix)
                assert child['anchor'] == pytest.approx(expected)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']


def test_unique_tags_keep_existing_paths_and_values(tmp_path):
    doc = ezdxf.new(); doc.blocks.new('symbol').add_line((0, 0), (1, 0))
    ins = doc.modelspace().add_blockref('symbol', (2, 3))
    a = ins.add_attrib('A', 'one', (2, 4)); b = ins.add_attrib('B', 'two', (2, 5))
    path = tmp_path / 'ordinary.dxf'; doc.saveas(path)
    index = w.build_index(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    parent = next(r for r in index['objects'] if r['entity_type'] == 'INSERT')
    assert parent['attributes'] == {'A': 'one', 'B': 'two'}
    assert parent['duplicate_attribute_tags'] == []
    assert {r['object_id'] for r in index['objects'] if r['entity_type'] == 'ATTRIB'} == {
        f'CADINST-Model/{ins.dxf.handle}/{a.dxf.handle}',
        f'CADINST-Model/{ins.dxf.handle}/{b.dxf.handle}'}
