"""Anonymous source geometry continuity: symbolic routes never supply length."""
import copy
import json
from pathlib import Path
import ezdxf
import pytest
from test_duct_callout_bundle_t28_batch10_20261019 import source_case, bundle, PATTERN
from test_duct_batch_network_t28_t20_20261019 import write, sha, run, DUCT, SHARED, consume


PORT_PATTERN = (r'scope=(?P<scope_id>\S+) id=(?P<physical_id>\S+) '
                r'sys=(?P<system>\S+) floor=(?P<floor_id>\S+)')


def chain_case(tmp_path, *, branch=False, omitted=False, missing_port=False, elevations=(3., 7., 11.), sizes=None):
    sources = []
    for index, elevation in enumerate(elevations):
        floor = 'F' + str(index + 1)
        c, obs = source_case(tmp_path / floor, floor, elevation)
        if sizes and sizes[index] != (300, 200):
            from test_duct_source_recovery_t28_batch9_20261019 import refresh
            width, height = sizes[index]; size_text = f'{width}x{height}'
            actual = obs['identity_annotation']['expected_text'].replace('300x200', size_text)
            def edit(m, doc): doc.entitydb[obs['identity_annotation']['source_key'][4:]].dxf.text = actual
            refresh(c, edit)
            obs['identity_annotation']['expected_text'] = actual
            obs['identity_annotation']['pattern'] = PATTERN.replace('300x200', size_text)
            rule = next(rule for rule in c['profile']['rules'] if rule['representation'] == 'riser_projection')
            rule['section'] = {'shape': 'rectangle', 'width': width, 'height': height, 'unit': 'mm'}
            ep = Path(rule['evidence']['path']); evidence = json.loads(ep.read_text(encoding='utf-8'))
            evidence['section'] = rule['section']; write(ep, evidence); rule['evidence']['sha256'] = sha(ep)
            write(c['pp'], c['profile'])
        sources.append({'source_id': floor, 'manifest': {'path': str(c['manifest']), 'sha256': sha(c['manifest'])},
                        'profile': {'path': str(c['pp']), 'sha256': sha(c['pp'])}, 'observations': [obs]})
    doc = ezdxf.new(); doc.header['$INSUNITS'] = 4
    doc.layers.new('RISER-SCHEMATIC'); m = doc.modelspace()
    route = ['CAD-' + m.add_line((0, index * 100), (0, (index + 1) * 100),
                dxfattribs={'layer': 'RISER-SCHEMATIC'}).dxf.handle for index in range(len(elevations) - 1)]
    if branch:
        entity = (m.add_lwpolyline([(0, 100, .41421356237309503), (-100, 100, 0)], format='xyb', dxfattribs={'layer': 'RISER-SCHEMATIC'})
                  if branch == 'arc' else m.add_line((0, 100), (-100, 100), dxfattribs={'layer': 'RISER-SCHEMATIC'}))
        key = 'CAD-' + entity.dxf.handle
        if not omitted: route.append(key)
    ports = []
    for index in range(len(elevations)):
        floor = 'F' + str(index + 1); y = index * 100; layer = 'PORT-LEADER-' + floor
        doc.layers.new(layer)
        actual = 'scope=A id=R1 sys=SYS floor=' + floor
        tag = m.add_text(actual, dxfattribs={'insert': (500, y + 30), 'height': 5})
        keys = ['CAD-' + m.add_line(a, b, dxfattribs={'layer': layer}).dxf.handle for a, b in
                (((500, y + 30), (550, y + 30)), ((550, y + 30), (0, y)))]
        ports.append({'identity_annotation': {'source_key': 'CAD-' + tag.dxf.handle, 'expected_text': actual,
                      'pattern': PORT_PATTERN, 'basis': 'source riser and floor identifiers on the system diagram',
                      'source_locator': 'Model source riser port label'},
                      'leader_network': {'source_keys': keys, 'shoulder_source_key': keys[0],
                       'snap_tolerance': .001, 'text_tolerance': .001, 'target_tolerance': .001,
                       'target_contact': 'source_curve_node', 'basis': 'actual leader from label to path node',
                       'source_locator': 'Model source leader'}})
    source = tmp_path / 'continuity.dxf'; doc.saveas(source)
    neutral = tmp_path / 'schematic-neutral'
    process = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', source, '--output-dir', neutral)
    assert process.returncode in (0, 3), process.stderr
    manifest = neutral / 'CAD中性取证清单.json'
    if missing_port: ports.pop(1)
    cfg = {'schema_version': bundle.SCHEMA, 'package_id': 'ANON-CONTINUOUS-RISER', 'sources': sources,
           'continuity_diagrams': [{'manifest': {'path': str(manifest), 'sha256': sha(manifest)},
             'layout': 'Model', 'route_source_keys': route, 'port_annotations': ports,
             'snap_tolerance': .001, 'basis': 'source riser system diagram connected route with floor port labels',
             'source_locator': 'Model actual riser schematic'}]}
    bp = write(tmp_path / 'bundle.json', cfg)
    return bp, cfg, source


def test_three_floor_source_geometry_chain_real_cli_one_physical_outlet(tmp_path):
    bp, cfg, source = chain_case(tmp_path)
    data, _ = bundle.build(bp)
    assert len(data['riser_vertical_items']) == 1
    item = data['riser_vertical_items'][0]
    assert item['length'] == pytest.approx(8.)
    assert [row['length'] for row in item['intervals']] == pytest.approx([4., 4.])
    assert len(item['source_ids']) == len(set(item['source_ids'])) == 3
    assert item['continuity_evidence']['source_sha256'] == sha(source)
    assert item['continuity_evidence']['route_length_used_for_quantity'] is False
    assert len(data['coverage']['rows']) == 3
    assert all(row['disposition'] == 'counted' for row in data['coverage']['rows'])
    out = tmp_path / 'actual-consumption'
    proc = run(DUCT / 'build_duct_riser_bundle_from_neutral.py', '--bundle', bp, '--output-dir', out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads((out / '风管实际消费结果.json').read_text(encoding='utf-8'))
    assert result['metrics']['known_source_riser_length_rule'] == pytest.approx(8.)
    assert len(result['vertical_items']) == 1 and result['metrics']['totals_complete'] is False
    assert sum(result['length_summary']['SYS']['规则推算'].values()) == pytest.approx(8.)


@pytest.mark.parametrize('defect', ['no_diagram', 'branch', 'omitted_branch', 'omitted_arc_branch', 'missing_port', 'reverse_height', 'equal_height'])
def test_chain_requires_source_path_all_ports_and_monotone_elevations(tmp_path, defect):
    bp, cfg, _ = chain_case(tmp_path, branch='arc' if defect == 'omitted_arc_branch' else defect in {'branch', 'omitted_branch'},
                    omitted=defect in {'omitted_branch','omitted_arc_branch'}, missing_port=defect == 'missing_port',
                    elevations=(3., 12., 11.) if defect == 'reverse_height' else (3., 3., 11.) if defect == 'equal_height' else (3., 7., 11.))
    if defect == 'no_diagram': cfg.pop('continuity_diagrams')
    write(bp, cfg); data, _ = bundle.build(bp)
    assert not data['riser_vertical_items']
    assert all(row['disposition'] == 'unrecognized' for row in data['coverage']['rows'])
    assert all(row['height'] is None for row in data['riser_projection_candidates'])
    result = consume(tmp_path, data)
    assert not result['vertical_items']
    assert any(row['kind'] == 'cross_source_riser_identity_unresolved' for row in result['pending'])


def test_continuity_geometry_and_output_tampering_replayed(tmp_path):
    bp, cfg, source = chain_case(tmp_path); data, _ = bundle.build(bp)
    assert data['riser_vertical_items'], data['cross_source_identity_pending']
    data['riser_vertical_items'][0]['intervals'][0]['length'] = 999
    ip = write(tmp_path / 'forged.json', data); op = tmp_path / 'forged-result.json'
    proc = run(DUCT / 'infer_duct_fittings_vertical.py', '--input', ip, '--output', op)
    assert proc.returncode == 2 and not op.exists()
    source.write_bytes(source.read_bytes() + b'\n')
    with pytest.raises(ValueError): bundle.build(bp)


def test_two_floor_legacy_bundle_remains_independent_of_new_chain_proof(tmp_path):
    from test_duct_callout_bundle_t28_batch10_20261019 import bundle_case
    bp, *_ = bundle_case(tmp_path); data, _ = bundle.build(bp)
    assert len(data['riser_vertical_items']) == 1
    assert data['riser_vertical_items'][0]['length'] == pytest.approx(4.2)


def test_source_continuous_section_change_keeps_length_and_unknown_transition_surface(tmp_path):
    bp, cfg, source = chain_case(tmp_path, sizes=[(300, 200), (300, 200), (500, 250)])
    data, _ = bundle.build(bp)
    assert len(data['riser_vertical_items']) == 1
    item = data['riser_vertical_items'][0]
    assert item['length'] == pytest.approx(8.) and item['theoretical_surface_area'] is None
    assert item['known_theoretical_surface_area_subtotal'] == pytest.approx(4.)
    assert item['intervals'][0]['theoretical_surface_area'] == pytest.approx(4.)
    assert item['intervals'][1]['theoretical_surface_area'] is None
    assert item['intervals'][1]['from_section']['width'] == 300
    assert item['intervals'][1]['to_section']['width'] == 500
    result = consume(tmp_path, data)
    assert result['metrics']['known_source_riser_length_rule'] == pytest.approx(8.)
    assert sum(result['length_summary']['SYS']['规则推算'].values()) == pytest.approx(8.)
    areas = result['theoretical_surface_summary']['SYS']['规则推算']
    assert sum(value for value in areas.values() if value is not None) == pytest.approx(4.)
    assert sum(value is None for value in areas.values()) == 1
    assert any(row['kind'] == 'source_riser_transition_surface_unresolved' and row['length'] == pytest.approx(4.) for row in result['pending'])
    assert all(row['disposition'] == 'counted' for row in result['coverage']['rows'])


def test_bad_explicit_diagram_never_falls_back_to_legacy_two_endpoint_quantity(tmp_path):
    bp, cfg, _ = chain_case(tmp_path, elevations=(3., 7.), branch=True)
    data, _ = bundle.build(bp)
    assert not data['riser_vertical_items']
    assert all(row['disposition'] == 'unrecognized' for row in data['coverage']['rows'])
