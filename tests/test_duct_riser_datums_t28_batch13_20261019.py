"""Typed source datum equations use the existing shared offset evaluator."""
import copy
import json
from pathlib import Path
import ezdxf
import pytest
from test_duct_callout_bundle_t28_batch10_20261019 import source_case, bundle
from test_duct_batch_network_t28_t20_20261019 import write, sha, run, DUCT, SHARED, consume


EQUATION = (r'scope=(?P<scope_id>\S+) z\((?P<to_datum_kind>absolute|project_common):(?P<to_datum_id>\w+)\)='
            r'z\((?P<from_datum_kind>absolute|project_common):(?P<from_datum_id>\w+)\)'
            r'(?P<operator>[+-])(?P<offset>\d+(?:\.\d+)?)(?P<unit>m|mm)')


def datum_case(tmp_path, *, conflict=False, scope='A', offset=100.):
    a, ao = source_case(tmp_path / 'lower', 'F1', 3.2, datum='P0', kind='project_common')
    b, bo = source_case(tmp_path / 'upper', 'F2', 107.4)
    doc = ezdxf.new(); doc.header['$INSUNITS'] = 4; m = doc.modelspace(); annotations = []
    for index, value in enumerate([offset, offset + 1] if conflict else [offset]):
        actual = f'scope={scope} z(absolute:ABS)=z(project_common:P0)+{value}m'
        note = m.add_text(actual, dxfattribs={'insert': (0, index * 100)})
        annotations.append({'source_key': 'CAD-' + note.dxf.handle, 'expected_text': actual, 'pattern': EQUATION,
                'basis': 'source drawing explicit signed project datum equation', 'source_locator': 'Model datum note'})
    source = tmp_path / 'datums.dxf'; doc.saveas(source); neutral = tmp_path / 'datum-neutral'
    proc = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', neutral)
    assert proc.returncode in (0, 3), proc.stderr
    manifest = neutral / 'CAD中性取证清单.json'
    cfg = {'schema_version': bundle.SCHEMA, 'package_id': 'ANON-TYPED-DATUM', 'sources': [
        {'source_id': name, 'manifest': {'path': str(c['manifest']), 'sha256': sha(c['manifest'])},
         'profile': {'path': str(c['pp']), 'sha256': sha(c['pp'])}, 'observations': [obs]} for name, c, obs in [('LOWER', a, ao), ('UPPER', b, bo)]],
        'datum_transform_sources': [{'manifest': {'path': str(manifest), 'sha256': sha(manifest)},
                                    'layout': 'Model', 'annotations': annotations}]}
    return write(tmp_path / 'bundle.json', cfg), cfg, source


def test_source_project_absolute_equation_actual_consumer(tmp_path):
    bp, cfg, source = datum_case(tmp_path); data, _ = bundle.build(bp)
    assert len(data['riser_vertical_items']) == 1
    item = data['riser_vertical_items'][0]; assert item['length'] == pytest.approx(4.2)
    assert item['datum_conversion']['evaluator']['sha256'] == sha(SHARED / 'qto_elevation_facts.py')
    assert item['datum_conversion']['source_facts'][0]['source_sha256'] == sha(source)
    result = consume(tmp_path, data); assert result['metrics']['known_source_riser_length_rule'] == pytest.approx(4.2)
    assert result['business_adoption_allowed'] is False
    out = tmp_path / 'actual-cli'; proc = run(DUCT / 'build_duct_riser_bundle_from_neutral.py', '--bundle', bp, '--output-dir', out)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize('defect', ['missing', 'wrong_scope', 'wrong_equation', 'conflict', 'source_tamper'])
def test_typed_datum_no_equal_name_or_handfilled_offset_shortcut(tmp_path, defect):
    bp, cfg, source = datum_case(tmp_path, conflict=defect == 'conflict', scope='OTHER' if defect == 'wrong_scope' else 'A')
    if defect == 'missing': cfg.pop('datum_transform_sources')
    if defect == 'wrong_equation': cfg['datum_transform_sources'][0]['annotations'][0]['expected_text'] = '100m'
    if defect == 'source_tamper': source.write_bytes(source.read_bytes() + b'\n')
    write(bp, cfg)
    if defect in {'wrong_equation', 'conflict', 'source_tamper'}:
        with pytest.raises(ValueError): bundle.build(bp)
    else:
        data, _ = bundle.build(bp); assert not data['riser_vertical_items']
        assert all(row['disposition'] == 'unrecognized' for row in data['coverage']['rows'])


def test_shared_datum_evaluator_is_actual_receipt_dependency():
    from duct_inline_reception import duct_dependencies
    assert any(Path(row['path']).name == 'qto_elevation_facts.py' for row in duct_dependencies())
