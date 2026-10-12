import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'skills/_qto-shared-evidence/scripts/qto_evidence_locations.py'
spec = importlib.util.spec_from_file_location('loc', SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_legacy_annotation_is_accepted_after_pointer_resolution():
    assert mod.pointer_tokens('/surface_frames/CL-11; raw Model handles 9A,9B') == ('surface_frames', 'CL-11')


def test_real_json_key_with_semicolon_is_not_truncated():
    value = {'objects': {'a;b': {'ok': True}}}
    assert mod.resolve_tokens(value, mod.pointer_tokens('/objects/a;b')) == {'ok': True}


def test_invalid_legacy_path_is_still_rejected_by_resolution():
    try:
        mod.resolve_tokens({'objects': {}}, mod.pointer_tokens('/objects/not-found; explanation'))
    except ValueError:
        return
    raise AssertionError('missing JSON location must remain invalid')
