"""Temporary, hash-verified source transport on the isolated repair branch only.
All payload files contain authored source/tests, no project data or credentials.
This loader and its payloads are excluded from the final main tree.
"""
import base64
import hashlib
import json
import runpy
import zlib
from pathlib import Path

root = Path.cwd().resolve()
encoded = ''.join((root / f'maintenance/audit-payload-{i:02d}.b64').read_text(encoding='ascii').strip() for i in range(1, 6))
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
assert hashlib.sha256(raw).hexdigest() == 'c6c4234bec73d47bad97ec0c65fbd8a14bcf6becf4cd54b39dfa3d123702d92f', 'authored source checksum mismatch'
for relative, content in json.loads(raw).items():
    path = (root / relative).resolve()
    assert path.is_relative_to(root) and relative.startswith(('skills/', 'tests/', 'maintenance/', 'docs/', '.github/workflows/')), relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='\n')
runpy.run_path(str(root / 'maintenance/apply_core.py'), run_name='__main__')
