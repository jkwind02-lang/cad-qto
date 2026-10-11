"""Known source handle ambiguity cannot disappear at professional adapters."""
import importlib.util
import json
from pathlib import Path
import pytest


@pytest.mark.parametrize('skill,script', [
    ('qto-pipe-networks', 'build_pipe_inference_input.py'),
    ('qto-components-points', 'build_component_instances_from_neutral.py'),
])
def test_neutral_identity_conflict_blocks_instance_adoption(tmp_path, skill, script):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('identity_' + skill.replace('-', '_'), root / 'skills' / skill / 'scripts' / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / 'source_manifest.json'
    path.write_text(json.dumps({'schema_version': 'qto-cad-neutral-evidence/1.0',
        'geometry_schema_version': module.cad_geometry.GEOMETRY_SCHEMA,
        'source_identity_issues': [{'kind': 'duplicate_entity_handle', 'entity_handle': 'AB'}]}), encoding='utf8')
    with pytest.raises(ValueError, match='源实体身份存在冲突'):
        module.source_from_manifest(path)
