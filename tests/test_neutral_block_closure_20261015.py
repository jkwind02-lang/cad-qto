"""extract_cad_neutral: only block definitions some layout draws are serialized by default.

Registered 20261014-2341: as-built sheets carry thousands of paste-residue (`A$C...`) and library
blocks no layout inserts; serializing all of them was 73% of an 18.5 GB package. Synthetic DXF only.
"""
import json
import os
import sys
from pathlib import Path

import ezdxf

SHARED = Path(os.environ.get('CAD_QTO_SHARED', Path(__file__).resolve().parents[1] / 'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0, str(SHARED))
import extract_cad_neutral as extract


def build(tmp_path):
    doc = ezdxf.new('R2018')
    nested = doc.blocks.new('NESTED')
    nested.add_line((0, 0), (1, 0))
    used = doc.blocks.new('USED')
    used.add_line((0, 0), (2, 0))
    used.add_blockref('NESTED', (0, 1))
    orphan = doc.blocks.new('A$C1234ORPHAN')
    for index in range(10):
        orphan.add_line((index, 0), (index, 1))
    orphan_child = doc.blocks.new('ORPHAN_CHILD')
    orphan_child.add_circle((0, 0), 1)
    orphan_parent = doc.blocks.new('ORPHAN_PARENT')
    orphan_parent.add_blockref('ORPHAN_CHILD', (0, 0))
    paper_only = doc.blocks.new('PAPER_ONLY')
    paper_only.add_line((0, 0), (3, 0))
    model = doc.modelspace()
    model.add_blockref('USED', (5, 5))
    model.add_line((0, 0), (10, 0))
    model.add_linear_dim(base=(0, 2), p1=(0, 0), p2=(10, 0)).render()
    doc.layouts.get('Layout1').add_blockref('PAPER_ONLY', (1, 1))
    path = tmp_path / 'closure.dxf'
    doc.saveas(path)
    return path


def run(tmp_path, dxf, *extra):
    out = tmp_path / ('all' if extra else 'closure')
    code = extract.main(['--source-dxf', str(dxf), '--output-dir', str(out), *extra])
    manifest = json.loads((out / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (out / '中性块定义图元.ndjson').read_text(encoding='utf-8').splitlines()]
    coverage = json.loads((out / 'CAD读取覆盖与待补读.json').read_text(encoding='utf-8'))
    return code, manifest, rows, coverage


def test_default_serializes_referenced_closure_only(tmp_path):
    dxf = build(tmp_path)
    code, manifest, rows, coverage = run(tmp_path, dxf)
    assert code in (0, 3)
    blocks = {row['block_name']: row for row in manifest['blocks']}
    dimension_blocks = [name for name in blocks if name.startswith('*D')]
    assert dimension_blocks, 'the rendered DIMENSION must own a *D geometry block'
    serialized = {row['parent_block'] for row in rows}
    # ezdxf's rendered dimension inserts its arrow block (e.g. `_CLOSEDFILLED`) inside `*D1`,
    # so that block is legitimately part of the closure; only the orphans must be absent.
    assert {'USED', 'NESTED', 'PAPER_ONLY', *dimension_blocks} <= serialized
    assert all(blocks[name]['referenced_from_layouts'] and blocks[name]['serialized'] for name in serialized)
    assert blocks['USED']['referenced_from_layouts'] and blocks['NESTED']['serialized']
    assert blocks['PAPER_ONLY']['serialized'], 'paper-space inserts count as layout references'
    for name in ('A$C1234ORPHAN', 'ORPHAN_PARENT', 'ORPHAN_CHILD'):
        assert blocks[name]['serialized'] is False and blocks[name]['referenced_from_layouts'] is False
    assert blocks['A$C1234ORPHAN']['direct_entity_count'] == 10, 'unreferenced blocks stay inventoried'
    serialization = manifest['block_definition_serialization']
    assert serialization['mode'] == 'referenced_closure'
    assert serialization['definition_block_count'] == len(blocks) == manifest['block_definition_count']
    assert serialization['unserialized_block_count'] == 3
    assert serialization['unserialized_entity_count'] == 10 + 1 + 1
    assert serialization['serialized_block_count'] == len(serialized)
    assert coverage['block_definition_serialization'] == serialization
    assert 'unreferenced block definitions are inventoried' in coverage['counting_basis']
    assert manifest['artifacts']['block_definitions']['count'] == len(rows)
    assert manifest['extractor_version'] == 'qto-cad-neutral-extractor/1.5.2'


def test_all_blocks_flag_restores_full_serialization(tmp_path):
    dxf = build(tmp_path)
    _, manifest, rows, _ = run(tmp_path, dxf, '--all-blocks')
    assert manifest['block_definition_serialization']['mode'] == 'all_blocks'
    assert manifest['block_definition_serialization']['unserialized_block_count'] == 0
    assert {row['parent_block'] for row in rows} >= {'USED', 'NESTED', 'A$C1234ORPHAN', 'ORPHAN_PARENT', 'ORPHAN_CHILD', 'PAPER_ONLY'}
    assert all(row['serialized'] for row in manifest['blocks'])
    assert sum(1 for row in rows if row['parent_block'] == 'A$C1234ORPHAN') == 10
