"""Run reviewed integration edits in the isolated branch, never on main."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parents[1]
assert (ROOT/'skills/_qto-shared-evidence/scripts/qto_pipe_topology.py').is_file()
for path in ['maintenance/finish_route_edges.py','maintenance/finish_workflow.py','maintenance/update_runtime_manifest.py']:
    runpy.run_path(str(ROOT/path),run_name='__main__')
