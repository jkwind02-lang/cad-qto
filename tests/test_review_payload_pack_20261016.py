"""Page payload packing (review view contract 11.1): lossless, unpackable sets stay JSON, the page decodes like Python."""
import gzip, json, shutil, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
import qto_review_contract as c  # noqa: E402

CORE = ROOT / 'skills/_qto-shared-evidence/assets/review/review-core.js'


def sample():
    lamp = [{'kind': 'line', 'points': [[0, 0], [300, 0], [300, 300]], 'color': '#112233'},
            {'kind': 'polygon', 'points': [[0, 0], [10, 0], [10, 10]], 'color': '#112233', 'fill': '#112233'}]
    lamps = [{**it, 'points': [[x + dx, y + dy] for x, y in it['points']]}
             for dx in range(2_100_000, 2_300_000, 4000) for dy in (540_000, 548_000) for it in lamp]
    texts = [{'kind': 'text', 'point': [2_100_000 + n * 1000, 540_000], 'text': f'灯{n}', 'height': 350, 'align': 'left',
              'baseline': 'alphabetic', 'color': '#7c909c'} for n in range(20)]
    metre = [{'kind': 'line', 'points': [[12.345, -0.5], [13.0, 7.125]], 'dash': [0.5, 0.25]},
             {'kind': 'text', 'point': [-1.5, 2.25], 'text': 'B', 'height': 0.35}]
    odd = [{'kind': 'line', 'points': [[0.1234567, 0], [1, 1]]}]  # seven decimals: not exact at six, stays JSON
    return {'schema': 'x', 'objects': [{'id': 'o1', 'points': [[1, 2]]}],
            'scenes': [{'id': 'a', 'background': metre, 'background_refs': ['s1']}, {'id': 'b', 'background': odd}, {'id': 'c', 'background': []}],
            'background_sets': {'s1': lamps + texts, 's2': []}}


def head_of(raw):
    n = int.from_bytes(raw[8:12], 'big')
    return json.loads(raw[12:12 + n])


def test_pack_is_lossless_and_keeps_unpackable_sets_as_json():
    v = sample()
    raw = c.pack_payload(v)
    assert raw.startswith(c.PACK_MAGIC) and c.unpack_payload(raw) == v
    head = head_of(raw)
    cols = head['background_pack']['collections']
    assert {e.get('set', e.get('scene')): e['decimals'] for e in cols} == {'s1': 0, 0: 3}
    assert head['scenes'][1]['background'] == v['scenes'][1]['background'] and head['background_sets']['s1'] == []
    assert len(head['background_pack']['styles']) == 2 + 20 + 2
    plain = json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode()
    assert len(gzip.compress(raw, mtime=0)) < len(gzip.compress(plain, mtime=0))


def test_pack_without_sets_or_packable_items():
    v = {'schema': 'x', 'objects': [], 'scenes': [{'id': 'a', 'background': [{'kind': 'line', 'points': [[0, 0], [1, 1]]}]}]}
    raw = c.pack_payload(v)
    assert 'background_sets' not in head_of(raw) and c.unpack_payload(raw) == v
    bad = {'schema': 'x', 'objects': [], 'scenes': [{'id': 'a', 'background': [{'kind': 'line', 'points': [[0, 0, 5], [1, 1, 5]]}]}]}
    raw = c.pack_payload(bad)
    assert not raw.startswith(c.PACK_MAGIC) and c.unpack_payload(raw) == bad


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_page_reads_the_packed_payload_like_python(tmp_path):
    v = sample()
    p = tmp_path / 'payload.bin'
    p.write_bytes(c.pack_payload(v))
    code = ("const fs=require('fs'),vm=require('vm'),ctx=vm.createContext({Response,Uint8Array,console});"
            "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
            "ctx.QtoReviewCore.readPayload(new Uint8Array(fs.readFileSync(process.argv[2]))).then(d=>process.stdout.write(JSON.stringify(d)));")
    out = subprocess.run(['node', '-e', code, str(CORE), str(p)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == v
    broken = tmp_path / 'broken.bin'
    broken.write_bytes(p.read_bytes()[:-3])
    out = subprocess.run(['node', '-e', code.replace('.then(', '.catch(e=>{process.stdout.write(e.message);}).then(').replace("process.stdout.write(JSON.stringify(d))", "d&&process.stdout.write('decoded')"), str(CORE), str(broken)],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert '底图打包数据' in out.stdout
