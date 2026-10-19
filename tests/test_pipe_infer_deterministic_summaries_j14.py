"""Complete fresh JSON must survive independent hash-randomized replay."""
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
PIPE=ROOT/'skills/qto-pipe-networks/scripts'
sys.path.insert(0,str(PIPE))
from infer_pipe_network import deterministic_length_summary
from test_pipe_serial_gaps_t25_20261019 import network


def test_group_totals_use_exact_float_terms_independent_of_assignment_insertion_order():
    lengths=[1e16]+[1.]*32
    segments={f'S{i:02d}':{'system':'W','length':v} for i,v in enumerate(lengths)}
    assignments={k:{'quantity_level':'未决量','dn':None} for k in segments}
    expected=float(sum((Decimal.from_float(v) for v in lengths),Decimal(0)))
    forward=deterministic_length_summary(assignments,segments)
    reverse=deterministic_length_summary(dict(reversed(list(assignments.items()))),segments)
    assert forward==reverse=={'W':{'未决量':{'DN待定':expected}}}
    assert expected==10000000000000032.


def source_chain():
    data=network(.1,False)
    data['parameters'].update(snap_tolerance=1e-6,gap_max=.01)
    data['segments']=[];x=0.
    lengths=[.1,.2,.3,1.7,.55,.07,math.sqrt(2),math.pi/6,.81,.033]*3
    for i,value in enumerate(lengths):
        end=x+value
        data['segments'].append(dict(segment_id=f'branch-{i:03d}',system='W',start=[x,0.],end=[end,0.],source_ids=[f'raw-{i}'],dn_seeds=[]))
        x=end
    data['object_selection'].update(candidate_count=len(lengths),included_count=len(lengths),included_segment_ids=[r['segment_id'] for r in data['segments']])
    return data


@pytest.mark.parametrize('case',['unresolved_chain','gap_layers'])
def test_hash_seed_changes_preserve_entire_json_and_bytes_from_same_input(tmp_path,case):
    data=source_chain() if case=='unresolved_chain' else network(.675,False)
    if case=='gap_layers':
        data['segments'].append(dict(segment_id='TURN',system='W',start=[3.675,0.],end=[3.675,1.],source_ids=['raw-turn'],dn_seeds=[]))
        data['object_selection'].update(candidate_count=3,included_count=3,included_segment_ids=['LEFT','RIGHT','TURN'])
    source=tmp_path/'original-input.json';source.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    original=source.read_bytes();reference=None
    for seed in ('0','1','2','7','19','91','314159','random'):
        output=tmp_path/(seed+'.json')
        env={**os.environ,'PYTHONHASHSEED':seed,'PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8'}
        cmd=[sys.executable,'-B','-X','utf8',str(PIPE/'infer_pipe_network.py'),'--input',str(source),'--output',str(output)]
        r=subprocess.run(cmd,stdin=subprocess.DEVNULL,capture_output=True,encoding='utf-8',env=env)
        assert r.returncode==0,(seed,r.stdout,r.stderr)
        raw=output.read_bytes();parsed=json.loads(raw)
        if reference is None:reference=(raw,parsed)
        else:
            assert parsed==reference[1],seed
            assert raw==reference[0],seed
    assert source.read_bytes()==original
