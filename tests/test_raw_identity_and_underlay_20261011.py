"""Native declarations and underlay facts; no project fixture or BOQ rules."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest
from ezdxf.math import Vec3

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SHARED))
import qto_cad_geometry as g
spec = importlib.util.spec_from_file_location('raw_identity_extract', SHARED/'extract_cad_neutral.py')
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


def duplicate_record(path, handle, edit=None):
    lines = path.read_bytes().splitlines(keepends=True)
    starts = [i for i in range(0, len(lines), 2) if int(lines[i].strip()) == 0]
    for start, end in zip(starts, starts[1:]+[len(lines)]):
        pairs = [(int(lines[i].strip()), lines[i+1].rstrip(b'\r\n')) for i in range(start, end, 2)]
        header = pairs[:next((j for j, p in enumerate(pairs) if p[0] == 100), len(pairs))]
        if (5, handle.encode()) in header or (105, handle.encode()) in header:
            record = b''.join(lines[start:end])
            repeated = edit(record) if edit else record
            path.write_bytes(b''.join(lines[:end])+repeated+b''.join(lines[end:]))
            return
    raise AssertionError('source record not found')


def resource_doc(kind):
    doc = ezdxf.new('R2018')
    doc.modelspace().add_line((0, 0), (3, 4))
    if kind == 'XRECORD':
        obj = doc.rootdict.add_xrecord('Reference')
        obj.extend([(1, 'payload'), (40, 1.0)])
    elif kind == 'DICTIONARY':
        obj = doc.rootdict.add_new_dict('Reference')
        obj.add_xrecord('child').extend([(1, 'child value')])
    else:
        obj = doc.materials.new('Surface')
    return doc, obj


@pytest.mark.parametrize('kind', ['MATERIAL', 'DICTIONARY', 'XRECORD'])
def test_identical_resource_repeat_keeps_complete_evidence_and_source(tmp_path, kind):
    doc, obj = resource_doc(kind)
    source = tmp_path/'input.dxf'; doc.saveas(source)
    duplicate_record(source, obj.dxf.handle)
    before = source.read_bytes()
    loaded, diag = g.read_dxf_with_diagnostics(source)
    assert len(loaded.modelspace()) == 1
    assert not diag['source_identity_issues']
    assert diag['status'] == 'PASS_WITH_WARNINGS'
    advisory = diag['source_identity_advisories'][0]
    assert len(advisory['raw_records']) == 2
    assert advisory['raw_records'][0]['raw_tags'] == advisory['raw_records'][1]['raw_tags']
    assert advisory['raw_records'][0]['line_start'] != advisory['raw_records'][1]['line_start']
    assert not diag['parser_entity_preservation_proven']
    assert diag['source_read_warnings'][0]['blocks_identity_binding'] is False
    assert source.read_bytes() == before
    assert diag['raw_identity_inspection']['source_sha256'] == hashlib.sha256(before).hexdigest()


@pytest.mark.parametrize('change', [(b'payload', b'payload '), (b'1.0', b'1'), (b'payload', b'another')])
def test_parsed_equivalence_and_same_type_do_not_replace_raw_tag_identity(tmp_path, change):
    doc, obj = resource_doc('XRECORD')
    source = tmp_path/'input.dxf'; doc.saveas(source)
    def edit(record):
        assert change[0] in record
        return record.replace(*change, 1)
    duplicate_record(source, obj.dxf.handle, edit)
    _, diag = g.read_dxf_with_diagnostics(source)
    assert diag['source_identity_issues'] and not diag['source_identity_advisories']


@pytest.mark.parametrize('kind', ['LINE', 'BLOCK_RECORD', 'LAYOUT'])
def test_identical_graphics_or_layout_identity_never_becomes_unique(tmp_path, kind):
    doc = ezdxf.new('R2018')
    if kind == 'LINE': obj = doc.modelspace().add_line((0, 0), (3, 4))
    elif kind == 'BLOCK_RECORD': obj = doc.blocks.new('Element').block_record
    else: obj = doc.layouts.new('Sheet').dxf_layout
    source = tmp_path/'input.dxf'; doc.saveas(source)
    duplicate_record(source, obj.dxf.handle)
    raw = g.inspect_raw_identity_declarations(source, [obj.dxf.handle])
    assert raw['verified_resource_repeats'] == []
    if kind == 'LINE':
        _, diag = g.read_dxf_with_diagnostics(source)
        assert diag['source_identity_issues']


@pytest.mark.parametrize('case', ['missing', 'conflicting_duplicate', 'unreported_duplicate', 'owner_conflict'])
def test_resource_reference_dependencies_cannot_bypass_identity_conflicts(tmp_path, case):
    doc, obj = resource_doc('XRECORD')
    target = doc.rootdict.add_xrecord('Other')
    target.extend([(1, 'target data')])
    if case == 'owner_conflict': obj.dxf.owner = target.dxf.handle
    else: obj.extend([(340, 'FFFFE' if case == 'missing' else target.dxf.handle)])
    source = tmp_path/'input.dxf'; doc.saveas(source)
    duplicate_record(source, obj.dxf.handle)
    selected = [obj.dxf.handle]
    if case != 'missing':
        duplicate_record(source, target.dxf.handle, lambda r: r.replace(b'target data', b'changed data'))
        if case != 'unreported_duplicate': selected.append(target.dxf.handle)
    raw = g.inspect_raw_identity_declarations(source, selected)
    assert obj.dxf.handle not in raw['verified_resource_repeats']
    assert raw['dependency_identity_issues'][obj.dxf.handle]


def test_cross_section_identity_is_not_non_graphical_repeat(tmp_path):
    doc, obj = resource_doc('XRECORD')
    line = doc.modelspace().add_line((0, 0), (2, 3))
    source = tmp_path/'input.dxf'; doc.saveas(source)
    data = source.read_bytes()
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    old = b'  5'+newline+line.dxf.handle.encode()+newline
    assert old in data
    source.write_bytes(data.replace(old, b'  5'+newline+obj.dxf.handle.encode()+newline, 1))
    _, diag = g.read_dxf_with_diagnostics(source)
    assert diag['source_identity_issues'] and not diag['source_identity_advisories']


@pytest.mark.parametrize('payload', [b'AutoCAD Binary DXF\r\n\x1a\0garbage', b'0\nSECTION\n2\nOBJECTS\n0\nXRECORD\n5\nAA\n1\n'])
def test_unverified_raw_input_never_proves_repeated_identity(tmp_path, payload):
    source = tmp_path/'input.dxf'; source.write_bytes(payload)
    raw = g.inspect_raw_identity_declarations(source, ['AA'])
    assert raw['status'] == 'unverified'
    assert not raw.get('verified_resource_repeats')


@pytest.mark.parametrize('mutation', ['bad_utf8', 'missing_eof'])
def test_complete_duplicate_records_do_not_prove_invalid_file_is_valid(tmp_path, mutation):
    doc, obj = resource_doc('MATERIAL')
    source = tmp_path/'input.dxf'; doc.saveas(source)
    duplicate_record(source, obj.dxf.handle)
    data = source.read_bytes()
    if mutation == 'bad_utf8':
        assert b'Surface' in data
        data = data.replace(b'Surface', b'Surfa\xffce')
    else:
        data = data[:data.rfind(b'  0')]
    source.write_bytes(data)
    raw = g.inspect_raw_identity_declarations(source, [obj.dxf.handle])
    assert raw['status'] == 'unverified' and not raw.get('verified_resource_repeats')
    if mutation == 'bad_utf8':
        _, diag = g.read_dxf_with_diagnostics(source)
        assert diag['source_identity_issues'] and not diag['source_identity_advisories']


@pytest.mark.parametrize('warning_mode', ['advisory_only', 'mixed_unknown', 'old_unclassified'])
def test_index_keeps_advisory_but_only_justified_warning_classification_allows_progress(tmp_path, monkeypatch, warning_mode):
    import qto_recognition_workbench as workbench
    doc, obj = resource_doc('MATERIAL')
    source = tmp_path/'input.dxf'; doc.saveas(source)
    duplicate_record(source, obj.dxf.handle)
    original_read = workbench.read_dxf_with_diagnostics
    def diagnosed(*args, **kwargs):
        document, diag = original_read(*args, **kwargs)
        if warning_mode == 'mixed_unknown':
            diag['source_read_warnings'].append({'level': 'WARNING', 'message': 'unclassified parser warning'})
        if warning_mode == 'old_unclassified':
            diag['schema_version'] = 'qto-cad-source-read-diagnostics/1.0'
            for warning in diag['source_read_warnings']:
                warning.pop('blocks_identity_binding', None)
        return document, diag
    monkeypatch.setattr(workbench, 'read_dxf_with_diagnostics', diagnosed)
    index = workbench.build_index({'path': str(source), 'sha256': extract.sha256(source)})
    assert index['source_identity_advisories']
    assert index['source_read_diagnostics']['source_read_warnings']
    assert bool(index['conversion_warnings']) == (warning_mode != 'advisory_only')


def underlay(fmt='pdf', filename='base.pdf', **kwargs):
    doc = ezdxf.new('R2018')
    definition = doc.add_underlay_def(filename, fmt=fmt, name='7')
    entity = doc.modelspace().add_underlay(definition, **kwargs)
    return doc, entity


def row(entity, context=None):
    return extract.convert(entity, layout='Model', parent_block=None, source_hash='a'*64,
        raw_unit='Millimeters', sequence=1, dependency_context=context)


@pytest.mark.parametrize('fmt', ['pdf', 'dwf', 'dgn'])
def test_underlay_is_native_reference_evidence_not_page_content(fmt):
    _, entity = underlay(fmt=fmt, insert=(10, 20, 30), scale=(2, 3, 4), rotation=30)
    entity.dxf.flags = 0
    entity.boundary_path = [(0, 0), (100, 200)]
    result = row(entity); ref = result['external_reference']
    assert result['geometry_capability'] == 'external_reference_evidence'
    assert result['geometry_status'] == 'requires_adapter'
    assert result['raw_length'] is None and result['geometry'] is None and result['bbox'] is None
    assert ref['declared_path'] == 'base.pdf' and ref['page_or_sheet'] == '7'
    assert ref['underlay_on'] is False and ref['content_status'] == 'not_read'
    assert ref['clipping_boundary_raw'] == [[0, 0], [100, 200]]
    assert ref['dependency']['status'] == 'unverified'


@pytest.mark.parametrize('normal', [(0, 0, 1), (0, 0, -1), (1, 2, 3)])
def test_underlay_nested_affine_preserves_signed_nonuniform_ocs_placement(normal):
    doc, entity = underlay(insert=(1e9, 200, 30), scale=(-2, 3, 4), rotation=31,
                           dxfattribs={'extrusion': normal})
    block = doc.blocks.new('Parent', base_point=(10, 20, 30))
    parent = doc.modelspace().add_blockref('Parent', (700, 800, 900),
        dxfattribs={'rotation': 23, 'xscale': 1.7, 'yscale': 0.9, 'zscale': 2})
    matrix = g.entity_geometry(parent)['insert_matrix_wcs']
    initial = row(entity)
    expanded = g.transform_row(initial, matrix, 'outer/underlay', 'Model')
    # Independent point construction from documented OCS rotation and scale.
    sample = Vec3(4, 5, 6)
    scaled_rotated = Vec3(sample.x*(-2), sample.y*3, sample.z*4).rotate_deg(31)
    native = entity.ocs().to_wcs(scaled_rotated + entity.dxf.insert)
    expected = parent.matrix44().transform(native)
    actual = g.transform_point(list(sample), expanded['external_reference']['reference_to_wcs'])
    assert actual == pytest.approx(list(expected), abs=1e-6)
    assert expanded['external_reference']['reference_to_definition_wcs'] == initial['external_reference']['reference_to_wcs']
    assert expanded['bbox'] is None and expanded['raw_length'] is None
    # A second affine composition cannot drop a nested ancestor.
    twice = g.transform_row(expanded, matrix, 'top/outer/underlay', 'Sheet')
    actual2 = g.transform_point(list(sample), twice['external_reference']['reference_to_wcs'])
    assert actual2 == pytest.approx(list(parent.matrix44().transform(expected)), abs=2e-6)


def test_underlay_missing_definition_is_explicit():
    doc = ezdxf.new('R2018')
    entity = doc.modelspace().new_entity('PDFUNDERLAY', {'underlay_def_handle': 'FFFFF', 'insert': (1, 2, 0)})
    result = row(entity)
    assert result['external_reference']['definition_status'] == 'missing'
    assert 'underlay_definition_missing' in result['geometry_issue_codes']
    assert result['external_reference']['dependency']['status'] == 'unverified'


def test_temporary_exchange_directory_is_never_assumed_original_context(tmp_path, monkeypatch):
    temporary = tmp_path/'export'; temporary.mkdir()
    (temporary/'base.pdf').write_bytes(b'not a decoded page')
    monkeypatch.chdir(temporary)
    _, entity = underlay()
    unknown = row(entity)['external_reference']['dependency']
    assert unknown['status'] == 'unverified' and unknown['resolved_path'] is None
    original = tmp_path/'original'; original.mkdir()
    context = {'original_cad_directory': str(original), 'basis': 'explicit_original_cad_directory'}
    missing = row(entity, context)['external_reference']['dependency']
    assert missing['status'] == 'missing_at_declared_path'
    (original/'base.pdf').write_bytes(b'an external file, content not read')
    present = row(entity, context)['external_reference']['dependency']
    assert present['status'] == 'exists_at_declared_path' and present['content_status'] == 'not_read'
    assert not present['search_paths_evaluated']


@pytest.mark.parametrize('declared', ['https://example.invalid/a.pdf', r'\\server\share\a.pdf', r'C:relative.pdf', r'\rootrelative.pdf'])
def test_remote_or_ambiguous_dependency_paths_stay_unverified(declared):
    _, entity = underlay(filename=declared)
    assert row(entity)['external_reference']['dependency']['status'] == 'unverified'


def test_cli_preserves_resource_advisory_and_frozen_origin_for_dependency(tmp_path):
    original = tmp_path/'original'; original.mkdir()
    dwg = original/'source.dwg'; dwg.write_bytes(b'origin fixture for hash binding')
    doc, entity = underlay(filename='./missing.pdf')
    obj = doc.rootdict.add_xrecord('Repeated'); obj.extend([(1, 'same')])
    exchange = tmp_path/'exchange.dxf'; doc.saveas(exchange)
    duplicate_record(exchange, obj.dxf.handle)
    before = exchange.read_bytes()
    receipt = {'output_sha256': extract.sha256(exchange), 'source': str(dwg),
        'source_sha256': extract.sha256(dwg), 'exchange_geometry_review_required': False}
    exchange.with_name(exchange.name+'.export.json').write_text(json.dumps(receipt), encoding='utf-8')
    output = tmp_path/'out'
    process = subprocess.run([sys.executable, '-B', str(SHARED/'extract_cad_neutral.py'),
        '--source-dxf', str(exchange), '--output-dir', str(output)], capture_output=True)
    assert process.returncode == 3  # external content is still pending
    manifest = json.loads((output/'CAD中性取证清单.json').read_text(encoding='utf-8'))
    coverage = json.loads((output/'CAD读取覆盖与待补读.json').read_text(encoding='utf-8'))
    assert not manifest['source_identity_issues'] and manifest['source_identity_advisories']
    assert manifest['neutral_extraction_status'] == 'PASS'
    ref = coverage['external_references'][0]['external_reference']
    assert ref['dependency']['resolution_basis'] == 'verified_export_receipt'
    assert ref['dependency']['resolved_path'] == str(original/'missing.pdf')
    assert manifest['read_coverage']['external_reference_count'] == 1
    assert exchange.read_bytes() == before
