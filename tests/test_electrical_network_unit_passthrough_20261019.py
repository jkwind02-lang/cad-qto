"""Unitless network requests retain source-checked units; unproven units fail."""
import copy
import hashlib
import json

import ezdxf
import pytest

from test_electrical_network_partition_t35_20261019 import fixture, run, SHARED, network


@pytest.fixture
def unitless_request(tmp_path):
    folder = tmp_path / 'source'
    _, request, handles, _ = fixture(folder)
    source = folder / '匿名混连.dxf'
    doc = ezdxf.readfile(source)
    doc.units = 0
    note = doc.modelspace().add_text('坐标绘图单位为米', dxfattribs={'insert': (0, -3), 'height': .2})
    note_id = 'CAD-' + note.dxf.handle
    doc.saveas(source)
    neutral = folder / 'unitless-neutral'
    done = run([SHARED / 'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', neutral])
    assert done.returncode == 0, done.stdout + done.stderr
    manifest = neutral / 'CAD中性取证清单.json'
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    request['cad_manifest_sha256'] = manifest_sha
    request['source_sha256'] = json.loads(manifest.read_text(encoding='utf-8'))['authoritative_source']['sha256']
    for obj in request['endpoint_objects']:
        obj['endpoint_binding']['manifest_sha256'] = manifest_sha
    request['trace_request']['unit']['declaration'] = {
        'raw_unit': 'Meters', 'source_ids': [note_id], 'source_excerpt': '坐标绘图单位为米',
        'geometric_corroboration': [{
            'kind': 'known_component_size', 'object_ids': ['CAD-' + handles['TRUNK']],
            'real_length_m': 10., 'measured_drawing_units': 10., 'basis': 'Anonymous independently known ten metre span',
        }],
    }
    return manifest, request


def test_verified_unitless_network_retains_declaration_and_source_length(unitless_request):
    manifest, request = unitless_request
    before = copy.deepcopy(request)
    result = network.partition_network(manifest, request)
    assert request == before
    assert next(c for c in result['connections'] if c['id'] == 'SUPPLY')['traced_path_length_m'] == pytest.approx(10.)
    assert result['official_adoption'] is False


@pytest.mark.parametrize('invalid', ['missing', 'unit-text-conflict', 'geometry-conflict'])
def test_unproven_unitless_network_is_still_rejected(unitless_request, invalid):
    manifest, request = unitless_request
    unit = request['trace_request']['unit']
    if invalid == 'missing':
        unit.pop('declaration')
    elif invalid == 'unit-text-conflict':
        unit['declaration']['raw_unit'] = 'Millimeters'
    else:
        unit['declaration']['geometric_corroboration'][0]['real_length_m'] = 100.
    with pytest.raises(ValueError):
        network.partition_network(manifest, request)
