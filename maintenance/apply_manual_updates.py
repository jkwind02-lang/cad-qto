"""One-time transport on an isolated branch; excluded from main."""
import base64, gzip, hashlib, json, runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if not (ROOT/'skills/_qto-shared-evidence/scripts/qto_pipe_topology.py').exists():
    pieces=[(ROOT/f'maintenance/manual-payload-{i:02d}.b64').read_text().strip() for i in range(1,5)]
    pieces[0]=pieces[0].replace('EbBEzBEYML','EbBEzYML')
    pieces[1]=pieces[1].replace('1Tf3BivKnUfJcX','1Tf3BivKn0TSStnUbjRFvXzu28+QVsnPFX74zf/4KbzpE7JjZZ0mIunhwxKg8p6XRJuavSq6o1bybixpw/V0EtESUapvxcbImGgqGMFOE0o07vqNEjnUfJcX')
    raw=gzip.decompress(base64.b64decode(''.join(pieces),validate=True))
    assert hashlib.sha256(raw).hexdigest()=='2d3aed31ee508737cef6dbda348f05c47471cf590f5b93e6c7544382ce491a6a','Transport integrity failure'
    for name,content in json.loads(raw).items():
        path=(ROOT/name).resolve();assert path.is_relative_to(ROOT) and '..' not in Path(name).parts
        if name=='maintenance/apply_manual_patch.py':
            content=content.replace('qto-pipe-neutral-adapter/1.4.0','qto-pipe-neutral-adapter/1.5.0').replace('qto-pipe-neutral-adapter/1.3.0','qto-pipe-neutral-adapter/1.4.0')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8',newline='\n')
    runpy.run_path(str(ROOT/'maintenance/apply_manual_patch.py'),run_name='__main__')
runpy.run_path(str(ROOT/'maintenance/update_runtime_manifest.py'),run_name='__main__')
