"""The pure benchmark scorer must detect each seeded mutation, deterministically; synthetic only."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_scorer_detects_all_mutations(tmp_path):
    out=tmp_path/'mutations.json'
    p=subprocess.run([sys.executable,str(ROOT/'maintenance/benchmark/tests/run_mutation_tests.py'),'--output',str(out)],capture_output=True,text=True,encoding='utf-8')
    assert p.returncode==0,p.stderr
    report=json.loads(out.read_text(encoding='utf-8'))
    assert report['baseline_status']=='PASS' and report['passed'] is True
    assert report['mutation_count']==report['detected_count']==report['deterministic_count']==8
