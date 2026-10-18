"""Electrical evidence checks the declared exchange file independently of streams."""
import hashlib
import json
from pathlib import Path

import pytest

from test_electrical_network_partition_t35_20261019 import fixture, network


def with_exchange(tmp_path, relative=False):
    manifest, request, _, _ = fixture(tmp_path / 'source')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    exchange = manifest.parent / 'exchange-copy.dxf'
    exchange.write_bytes(Path(data['authoritative_source']['path']).read_bytes())
    data['exchange_dxf'] = {'path': exchange.name if relative else str(exchange),
                            'sha256': hashlib.sha256(exchange.read_bytes()).hexdigest()}
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    request['cad_manifest_sha256'] = digest
    for obj in request['endpoint_objects']:
        obj['endpoint_binding']['manifest_sha256'] = digest
    return manifest, request, exchange


@pytest.mark.parametrize('relative', [False, True])
def test_valid_exchange_is_recorded_and_network_still_measures(tmp_path, relative):
    manifest, request, exchange = with_exchange(tmp_path, relative)
    result = network.partition_network(manifest, request)
    assert next(c for c in result['connections'] if c['id'] == 'SUPPLY')['traced_path_length_m'] == pytest.approx(10.)
    assert str(exchange.resolve()) in result['provenance']['fingerprints']
    assert result['official_adoption'] is False


@pytest.mark.parametrize('damage', ['stale', 'missing'])
def test_unchanged_authority_and_streams_cannot_mask_invalid_exchange(tmp_path, damage):
    manifest, request, exchange = with_exchange(tmp_path)
    if damage == 'stale':
        exchange.write_bytes(exchange.read_bytes() + b'\n')
    else:
        exchange.rename(exchange.with_suffix('.moved'))
    with pytest.raises(ValueError, match='exchange_dxf'):
        network.partition_network(manifest, request)


def test_exchange_changed_during_use_invalidates_provenance(tmp_path):
    manifest, _, exchange = with_exchange(tmp_path)
    evidence = network.binding.Evidence(manifest, {
        'unit': {'factor_to_m': 1., 'basis': 'explicit source unit'},
        'layout': 'Model', 'expand_blocks': ['DEVICE'],
    })
    exchange.write_bytes(exchange.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='指纹改变'):
        evidence.provenance()
