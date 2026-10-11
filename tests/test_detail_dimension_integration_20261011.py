"""Type-aware dimension facts must reach the actual detail-route calculator."""
import math
import hashlib
from pathlib import Path
import pytest
from test_pipe_detail_routes import fixture, changed, m


def test_aligned_vertical_without_optional_angle_can_calibrate(tmp_path):
    data, doc = fixture(tmp_path)
    dim = doc.entitydb[data['routes'][0]['dimension_paths'][0][0]]
    dim.dxf.dimtype = 33  # aligned + anonymous dimension picture
    dim.dxf.discard('angle')
    dim.dxf.actual_measurement = 10
    changed(data, doc)
    assert dim.get_measurement() == pytest.approx(0, abs=1e-12)
    route = m.measure(data)['routes'][0]
    assert route['axis_including_components_m'] == pytest.approx(16 + math.pi / 2)
    facts = route['calibration'][0]['measurement_evidence']
    assert facts['raw_measurement'] == 10
    assert facts['method'] == 'aligned_wcs_endpoint_distance'
    assert facts['notes']


def test_conflicting_stored_dimension_cannot_silently_calibrate(tmp_path):
    data, doc = fixture(tmp_path)
    dim = doc.entitydb[data['routes'][0]['dimension_paths'][0][0]]
    dim.dxf.actual_measurement = 20
    changed(data, doc)
    with pytest.raises(ValueError, match='尺寸证据冲突'):
        m.measure(data)


def test_duplicate_handles_cannot_identify_detail_route(tmp_path):
    data, doc = fixture(tmp_path)
    hidden = doc.blocks.new('UNREFERENCED')
    first = hidden.add_line((50, 50), (51, 50))
    second = hidden.add_line((50, 51), (51, 51))
    changed(data, doc)
    path = Path(data['drawing']['path'])
    raw = path.read_text(encoding='utf8')
    assert raw.count('\n  5\n' + second.dxf.handle + '\n') == 1
    raw = raw.replace('\n  5\n' + second.dxf.handle + '\n', '\n  5\n' + first.dxf.handle + '\n')
    path.write_text(raw, encoding='utf8')
    data['drawing']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='句柄重复'):
        m.measure(data)
