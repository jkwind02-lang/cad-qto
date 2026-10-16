"""第五批 K5：否定结论检索与点位覆盖台账同一套图页定位、全部命中明细、箱号整段词匹配。

登记 20261017-0012（检索按插入点定图页，与台账按图形定位不一致）、20261017-0015（只留 10 条样例；箱号短写加数字前边界
后搜不到完整编号里的短号）、20261017-0024（并入 0015：箱号后接尺寸时 text_pattern 对不上；判定对不上就整份拒绝）。
中性取证包是合成的（去标识），不含项目工程量。
"""
import csv, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills' / '_qto-shared-evidence' / 'scripts' / 'search_negative_evidence.py'
FAR = 1_000_000


def m(x, y): return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, 0, 0, 0, 0, 1]


def row(h, et, layer, parent=None, **kw):
    return {'entity_handle': h, 'entity_type': et, 'object_name': et, 'layer': layer, 'layout': 'BlockDefinition' if parent else 'Model',
            'parent_block': parent, 'raw_geometry': {}, **kw}


def text(h, x, y, value): return row(h, 'TEXT', 'E-TEXT', text=value, x=x, y=y)


LAYOUT = [
    # far base point, drawing on the legend sheet: placed by its geometry like the coverage ledger
    row('E1', 'INSERT', 'E-SYM', block_name='ESD', x=FAR, y=0, insert_matrix_wcs=m(FAR, 0), attributes={'NAME': '静电释放'}),
    row('E2', 'INSERT', 'E-SYM', block_name='ESDT', x=FAR, y=0, insert_matrix_wcs=m(FAR, 0)),   # holds an ATTDEF: incomplete extent
    text('T1', 500, 500, '01-F2-1B-LPB-N-3'),                 # full number with a floor prefix: main hit for 1B-LPB-N-3
    text('T2', 500, 600, '11B-LPB-N-3'),                      # digit next to digit: another box, only a near number
    text('T3', 500, 700, '01F21BLPBN3'),                      # written without separators: cannot tell, secondary only
    text('T4', 600, 500, '01-F1-1A-GPB-N-2  600*2000*400'),   # box number followed by its size: main hit for 1A-GPB-N-2
    text('T5', 600, 600, '1A-GPB-N-21'),                      # longer number: near only
    text('T6', 600, 700, '01-F1-1A-GPB-N-3  600*2000*400'),   # near, judged by a text_pattern on the number part
]
BLOCKS = [
    row('D1', 'LWPOLYLINE', 'E-SYM', parent='ESD', geometry={'parts': []}, bbox3d=[2450 - FAR, 450, 0, 2550 - FAR, 550, 0]),
    row('D2', 'LWPOLYLINE', 'E-SYM', parent='ESDT', geometry={'parts': []}, bbox3d=[2450 - FAR, 450, 0, 2550 - FAR, 550, 0]),
    row('D3', 'ATTDEF', 'E-SYM', parent='ESDT', x=2600 - FAR, y=500, z=0),
]
# BIG first: of the sheets holding a point the smallest wins, whatever the table order (the ledger's rule)
SHEETS = [['BIG', '总图', '平面', 0, 0, 10000, 10000], ['P1', '一层平面', '平面', 0, 0, 1000, 1000],
          ['LG', '图例', '图例', 2000, 0, 3000, 1000]]


def package(root):
    d = root / 'neutral' / 'D1'; d.mkdir(parents=True)
    for name, rows in (('中性布局图元.ndjson', LAYOUT), ('中性块定义图元.ndjson', BLOCKS)):
        (d / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    manifest = {'blocks': [{'block_name': n, 'origin': [0, 0, 0], 'serialized': True, 'direct_entity_count': c} for n, c in (('ESD', 1), ('ESDT', 2))]}
    (d / 'CAD中性取证清单.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    with open(root / 'D1_图页.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['图页', '图名', '类型', 'x1', 'y1', 'x2', 'y2']); w.writerows(SHEETS)
    return {'id': 'D1', 'neutral_dir': 'neutral/D1', 'sheets': {'path': 'D1_图页.csv', 'columns': {'sheet_id': '图页', 'title': '图名', 'sheet_kind': '类型'}}}


JUDGE_G = [{'text_pattern': '1AGPBN3(?![0-9A-Z])', 'judgment': '非同一对象', 'basis': 'N-3 是另一台箱'},
           {'text_pattern': '1AGPBN21$', 'judgment': '非同一对象', 'basis': 'N-21 是另一台箱'}]
ITEMS = [
    {'item_id': 'ESD', 'conclusion': '图上无 静电释放装置', 'kind': 'block', 'block_names': ['^ESDT?$']},
    {'item_id': 'NAME', 'conclusion': '图上无 静电释放', 'kind': 'text', 'term': '静电释放'},
    {'item_id': 'B', 'conclusion': '图上无 1B-LPB-N-3 箱', 'kind': 'box', 'term': '1B-LPB-N-3'},
    {'item_id': 'G', 'conclusion': '图上无 1A-GPB-N-2 箱', 'kind': 'box', 'term': '1A-GPB-N-2', 'secondary_judgments': JUDGE_G},
]


def run(tmp_path, items=ITEMS):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = {'schema': 'qto-negative-search-input/1.0', 'drawings': [package(tmp_path)], 'items': items}
    p = tmp_path / 'input.json'; p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    r = subprocess.run([sys.executable, str(SCRIPT), '--input', str(p), '--output-dir', str(out)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, encoding='utf-8', timeout=120)
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    data = json.loads((out / '否定结论检索记录.json').read_text(encoding='utf-8')) if (out / '否定结论检索记录.json').exists() else None
    detail = []
    if (out / '否定结论检索命中明细.csv').exists():
        with open(out / '否定结论检索命中明细.csv', encoding='utf-8-sig', newline='') as f:
            detail = list(csv.DictReader(f))
    return r.returncode, summary, data, detail


def by_id(data): return {r['item_id']: r for r in data['records']}


def test_far_base_point_block_is_placed_by_its_geometry_like_the_ledger(tmp_path):
    code, s, data, detail = run(tmp_path)
    assert code == 0, s
    esd = by_id(data)['ESD']['result']
    assert esd['hits'] == 2 and esd['hits_by_sheet_kind'] == {'图例': 2}      # not 台账外 by the far insertion point
    bases = {h['handle']: h['placement_basis'] for h in esd['samples']}
    assert bases == {'E1': '图形范围中心', 'E2': '图形范围中心（范围不完整：未含填充或注记按插入点计）'}
    assert esd['placement_basis_counts'] == {'图形范围中心': 1, '图形范围中心（范围不完整：未含填充或注记按插入点计）': 1}
    # an attribute value travels with its INSERT
    name = by_id(data)['NAME']['result']['samples'][0]
    assert (name['sheet'], name['placement_basis']) == ('LG', '图形范围中心')


def test_overlapping_sheets_take_the_smallest_and_text_keeps_its_own_point(tmp_path):
    code, s, data, detail = run(tmp_path)
    hit = by_id(data)['B']['result']['samples'][0]
    assert (hit['handle'], hit['sheet'], hit['placement_basis']) == ('T1', 'P1', '文字插入点')


def test_box_number_matches_as_whole_words(tmp_path):
    code, s, data, detail = run(tmp_path)
    b, g = by_id(data)['B'], by_id(data)['G']
    assert [h['handle'] for h in b['result']['samples']] == ['T1']                       # 01-F2- prefix
    assert sorted(h['handle'] for h in b['result']['secondary_samples']) == ['T2', 'T3']  # 11B…, 连写: near only
    assert [h['handle'] for h in g['result']['samples']] == ['T4']                       # followed by its size
    assert sorted(h['handle'] for h in g['result']['secondary_samples']) == ['T5', 'T6']
    assert b['targets']['pattern_forms'][0] == 'flat_sep' and any('整段词' in note for note in b['targets']['notes'])


def test_text_pattern_also_reads_each_whitespace_part(tmp_path):
    code, s, data, detail = run(tmp_path)
    g = by_id(data)['G']
    assert g['result']['secondary_judged'] == 2 and g['result']['secondary_unjudged'] == 0
    assert [j['matched'] for j in g['secondary_judgments']] == [1, 1]


def test_every_hit_is_listed_in_the_detail_file(tmp_path):
    code, s, data, detail = run(tmp_path)
    assert data['hit_detail']['rows'] == len(detail) and len(data['hit_detail']['sha256']) == 64
    rows = {(r['条目'], r['句柄']): r for r in detail}
    for record in data['records']:   # reverse check: the detail rows of an item are exactly its counted hits
        res = record['result']
        mine = [r for r in detail if r['条目'] == record['item_id']]
        assert sum(r['命中类别'] == '主命中' for r in mine) == res['hits']
        assert sum(r['命中类别'] == '次级' for r in mine) == res['secondary_hits']
    t6 = rows[('G', 'T6')]
    assert (t6['命中类别'], t6['判定'], t6['判定序号'], t6['图页']) == ('次级', '非同一对象', '0', 'P1')
    assert t6['统一写法'] == '01F11AGPBN3600X2000X400'
    assert rows[('B', 'T2')]['判定'] == '未判'


def test_stale_judgment_still_writes_the_hit_list(tmp_path):
    items = [dict(ITEMS[2], secondary_judgments=[{'handle': 'ZZ', 'judgment': '非同一对象', 'basis': '写错的句柄'}])]
    code, s, data, detail = run(tmp_path, items)
    assert code == 2 and data is None and '没有对上任何次级命中' in s['error']
    assert s['hit_detail'].endswith('否定结论检索命中明细.csv')
    assert sorted(r['句柄'] for r in detail) == ['T1', 'T2', 'T3']
