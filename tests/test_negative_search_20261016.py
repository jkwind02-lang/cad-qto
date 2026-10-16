"""否定结论检索（登记 20261015-2019、20261015-1535 否定结论条）：中性取证包里检索“图上无”的对象，出检索记录。

中性取证包是合成的，只验证写法变体、三层读取、块按插入次数展开、图页定位、未读对象和检索状态；不含项目工程量。
"""
import csv, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills' / '_qto-shared-evidence' / 'scripts' / 'search_negative_evidence.py'
I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def m(x, y): return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, 0, 0, 0, 0, 1]


def rec(h, et, layer, x=0.0, y=0.0, text=None, block=None, matrix=None, attrs=None, parent=None, raw=None, obj=None):
    return {'entity_handle': h, 'entity_type': et, 'object_name': obj or et, 'layer': layer, 'layout': 'BlockDefinition' if parent else 'Model',
            'parent_block': parent, 'text': text, 'x': x, 'y': y, 'block_name': block, 'insert_matrix_wcs': matrix, 'attributes': attrs, 'raw_geometry': raw or {}}


LAYOUT = [
    rec('A1', 'TEXT', 'E-TEXT', 2100, 100, text='ZBN-YJV-4x150+1x70'),              # system sheet: main hit for the full spec
    rec('A2', 'TEXT', 'E-TEXT', 100, 200, text='YJV-4×1500'),                       # 4x150 must not hit 4x1500
    rec('A3', 'TEXT', 'E-TEXT', 100, 300, text='KVV-4x150'),                        # control cable: excluded from the secondary search
    rec('A4', 'MTEXT', 'E-TEXT', 4100, 300, text='\\A1;ZB-YJV-2(4X150+1X70)'),      # MTEXT codes stripped, parallel form, other model
    rec('B1', 'INSERT', 'E-BOX', 100, 100, block='WRAP', matrix=m(100, 100)),       # WRAP holds two TAG blocks: AL1-2 twice
    rec('B2', 'INSERT', 'E-BOX', 4100, 100, block='BOX', matrix=m(4100, 100), attrs={'NO': 'al12'}),
    rec('W1', 'TEXT', '0-水印标记', 500, 500, text='AL-1-2'),                         # watermark layer: listed, not a hit
    rec('X1', 'ACAD_PROXY_ENTITY', '0-电子印章', 600, 600),
    rec('B3', 'INSERT', 'E-BOX', 9000, 9000, block='TAG', matrix=m(9000, 9000)),    # off every sheet
]
BLOCKS = [
    rec('C1', 'TEXT', 'E-TEXT', 0, 0, text='AL-1-2', parent='TAG'),
    rec('C2', 'INSERT', 'E-BOX', 10, 0, block='TAG', matrix=m(10, 0), parent='WRAP'),
    rec('C3', 'INSERT', 'E-BOX', 20, 0, block='TAG', matrix=m(20, 0), parent='WRAP'),
    rec('C4', 'TEXT', 'E-TEXT', 0, 0, text='AL-1-2', parent='ORPHAN'),             # never inserted: listed apart, not counted
    rec('C5', 'MTEXT', 'DIM', 0, 0, text='AL-1-2', parent='*D7'),                   # dimension block: measured values only
    rec('C6', 'TEXT', 'E-TEXT', 4200, 600, text='AL1-2', parent='*T3'),             # table block drawn once in world coordinates
]
SHEETS = [['P1', '一层平面', '平面', 0, 0, 1000, 1000], ['S1', '配电系统图', '系统图', 2000, 0, 3000, 1000], ['T1', '设备材料表', '说明/材料表', 4000, 0, 5000, 1000]]


def package(root, did, layout=LAYOUT, blocks=BLOCKS, blocks_manifest=()):
    d = root / 'neutral' / did; d.mkdir(parents=True)
    for name, rows in (('中性布局图元.ndjson', layout), ('中性块定义图元.ndjson', blocks)):
        (d / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    (d / 'CAD中性取证清单.json').write_text(json.dumps({'blocks': list(blocks_manifest)}, ensure_ascii=False), encoding='utf-8')
    with open(root / f'{did}_图页.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['图页', '图名', '类型', 'x1', 'y1', 'x2', 'y2']); w.writerows(SHEETS)
    return {'id': did, 'group': '本楼栋', 'neutral_dir': f'neutral/{did}',
            'sheets': {'path': f'{did}_图页.csv', 'columns': {'sheet_id': '图页', 'title': '图名', 'sheet_kind': '类型'}, 'kind_map': {'说明/材料表': '表与说明'}}}


ITEMS = [
    {'item_id': 'A', 'conclusion': '图上无 ZBN-YJV-4x150+1x70', 'kind': 'spec', 'term': 'ZBN-YJV-4x150+1x70', 'secondary': {'exclude': ['KVV']}},
    {'item_id': 'B', 'conclusion': '图上无 4x150', 'kind': 'spec', 'term': '4x150', 'exclude': ['KVV']},
    {'item_id': 'C', 'conclusion': '图上无 AL1-2 箱', 'kind': 'box', 'term': 'AL1-2'},
    {'item_id': 'D', 'conclusion': '图上无 消防广播', 'kind': 'text', 'term': '消防广播'},
]


def run(tmp_path, items=ITEMS, drawings=None, **extra):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if drawings is None: drawings = [package(tmp_path, 'D1')]
    cfg = {'schema': 'qto-negative-search-input/1.0', 'drawings': drawings, 'items': items, **extra}
    p = tmp_path / 'input.json'; p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    r = subprocess.run([sys.executable, str(SCRIPT), '--input', str(p), '--output-dir', str(out)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=120)
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    data = json.loads((out / '否定结论检索记录.json').read_text(encoding='utf-8')) if (out / '否定结论检索记录.json').exists() else None
    return r.returncode, summary, data, out


def by_id(data): return {r['item_id']: r for r in data['records']}


# ---------- counter-examples and legal controls ----------

def test_full_spec_hit_is_placed_on_its_sheet(tmp_path):
    code, s, data, _ = run(tmp_path)
    assert code == 0, s
    a = by_id(data)['A']
    assert a['status'] == 'hit' and a['status_text'] == '有命中' and a['result']['hits'] == 1
    h = a['result']['samples'][0]
    assert (h['drawing'], h['sheet'], h['sheet_kind'], h['handle'], h['via']) == ('D1', 'S1', '系统图', 'A1', '布局文字')
    assert a['result']['secondary_hits'] == 1 and a['result']['secondary_samples'][0]['handle'] == 'A4'   # other model, parallel form, MTEXT codes


def test_spec_boundaries_part_of_longer_spec_and_excluded_family(tmp_path):
    _, _, data, _ = run(tmp_path)
    b = by_id(data)['B']
    assert b['result']['hits'] == 0 and b['status'] == 'no_hit_full' and b['status_text'] == '全覆盖未命中'   # 4x1500 and 4x150+1x70 are not 4x150
    assert {h['handle'] for h in b['result']['secondary_samples']} == {'A1', 'A4'}                       # longer specs only as secondary; KVV excluded
    assert any('4x150' in f for f in b['targets']['written_forms']) and '4×150' in b['targets']['written_forms']


def test_nested_blocks_count_by_insertions_attributes_and_tables(tmp_path):
    _, _, data, _ = run(tmp_path)
    c = by_id(data)['C']['result']
    vias = sorted((h['sheet'], h['via'], h['handle']) for h in c['samples'])
    # WRAP on P1 holds TAG twice; BOX attribute on T1; table block *T3 once on T1; TAG inserted off-sheet once.
    assert c['hits'] == 4 + 1 and vias.count(('P1', '块 TAG（插入 3 次）', 'B1')) == 2
    assert ('T1', '布局文字 属性 NO', 'B2') in vias and ('T1', '块 *T3（插入 1 次）', None) in [(a, b, None if b.startswith('块 *T') else c_) for a, b, c_ in vias]
    assert c['hits_by_sheet_kind'] == {'平面': 2, '表与说明': 2, '台账外': 1}
    assert c['unplaced_block_hits'] == [{'drawing': 'D1', 'block': 'ORPHAN', 'texts': 1}]          # never inserted: apart, not counted
    assert all('*D7' not in h['via'] for h in c['samples'])                                          # dimension values not searched
    assert c['non_drawing_hits'] == 1 and c['non_drawing_samples'][0]['handle'] == 'W1'               # watermark layer is not a hit


def test_stamps_are_non_drawing_and_nothing_found_is_full_coverage(tmp_path):
    _, _, data, out = run(tmp_path)
    d = by_id(data)['D']
    assert d['result']['hits'] == 0 and d['status'] == 'no_hit_full'
    assert d['result']['unread'] == [{'drawing': 'D1', 'kind': '代理对象', 'layer': '0-电子印章', 'class': 'non_drawing', 'count': 1}]
    assert data['reverse_check']['requested'] == data['reverse_check']['records'] == 4 and not data['reverse_check']['missing']
    rows = list(csv.DictReader(open(out / '否定结论检索记录.csv', encoding='utf-8-sig')))
    assert [r['检索记录编号'] for r in rows] == ['NS-A', 'NS-B', 'NS-C', 'NS-D'] and rows[3]['检索状态'] == '全覆盖未命中'
    assert rows[3]['未能读取'] == 'D1 代理对象 1（0-电子印章，非图面内容）' and rows[3]['未覆盖'] == '无'
    assert '表与说明 1' in rows[3]['检索图纸'] and rows[2]['未插入块定义命中'] == 'D1 ORPHAN 1 处'


def test_missing_drawing_and_unread_drawing_content_make_it_pending(tmp_path):
    code, _, data, _ = run(tmp_path, drawing_set=['D1', 'D9'])
    d = by_id(data)['D']
    assert code == 0 and d['status'] == 'no_hit_partial' and d['status_text'] == '待确认' and d['uncovered'] == [{'drawing': 'D9', 'reason': '没给中性取证包'}]
    ole = LAYOUT + [rec('X2', 'OLE2FRAME', 'E-PLAN', 700, 700)]
    _, _, data, _ = run(tmp_path / 'b', drawings=[package(tmp_path / 'b', 'D1', layout=ole)])
    d = by_id(data)['D']
    assert d['status'] == 'no_hit_partial' and {'drawing': 'D1', 'kind': '嵌入对象', 'layer': 'E-PLAN', 'class': 'may_contain', 'count': 1} in d['result']['unread']
    over = [{'drawing': 'D1', 'entity_type': 'OLE2FRAME', 'layer': 'E-PLAN', 'class': 'non_drawing', 'basis': '人工核对：图签 logo'}]
    _, _, data, _ = run(tmp_path / 'c', drawings=[package(tmp_path / 'c', 'D1', layout=ole)], unread_overrides=over)
    assert by_id(data)['D']['status'] == 'no_hit_full'


def test_context_drawings_and_excluded_sheets_do_not_flip_the_status(tmp_path):
    d1 = package(tmp_path, 'D1', layout=[r for r in LAYOUT if r['entity_handle'] != 'A1'])
    d2 = {**package(tmp_path, 'D2'), 'group': '相邻楼栋', 'role': 'context'}
    _, _, data, _ = run(tmp_path, drawings=[d1, d2])
    a = by_id(data)['A']
    assert a['status'] == 'no_hit_full' and a['result']['hits'] == 0 and a['result']['context_hits'] == 1 and a['result']['hits_by_group'] == {'相邻楼栋': 1}
    d3 = {**package(tmp_path / 'x', 'D1'), 'exclude_sheet_titles': '配电系统图'}
    _, _, data, _ = run(tmp_path / 'x', drawings=[d3])
    assert by_id(data)['A']['result']['hits'] == 0 and by_id(data)['A']['result']['context_hits'] == 1


def test_existing_results_are_listed_as_candidates_only(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    with open(tmp_path / '成果.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['编号', '规格', '数量']); w.writerows([['R1', 'ZB-YJV-4×150+1×70', '198.8'], ['R2', 'KVV-4x150', '3'], ['R3', 'ZBN-YJV-4*150+1*70', '5']])
    _, _, data, _ = run(tmp_path, existing_results=[{'label': 'E-1', 'path': '成果.csv', 'id_column': '编号', 'text_columns': ['规格']}])
    a = by_id(data)['A']
    assert [(e['source'], e['id'], e['match']) for e in a['result']['existing_results']] == [('E-1:2', 'R1', '次级'), ('E-1:4', 'R3', '同写法')]
    assert a['status'] == 'hit'   # candidates never change the status


def test_design_note_conduit_with_diameter_code_is_found(tmp_path):
    # D013 设计说明的写法（去标识）：“支路导线为ZB-BV-3*2.5mm2穿JDG%%C20管敷设”。%%C 是 CAD 的直径符号控制码。
    note = rec('N1', 'TEXT', '说明_t8$0$说明', 4100, 800, text='除注明外，照明、插座支路导线为ZB-BV-3*2.5mm2穿JDG%%C20管敷设。')
    other = [rec('N2', 'MTEXT', 'E-TEXT', 100, 800, text='SCΦ25'), rec('N3', 'TEXT', 'E-TEXT', 200, 800, text='SC DN32'), rec('N4', 'TEXT', 'E-TEXT', 300, 800, text='JDG200')]
    items = [{'item_id': 'W', 'conclusion': '图上无 ZB-BV-3x2.5', 'kind': 'spec', 'term': 'ZB-BV-3x2.5'},
             {'item_id': 'J', 'conclusion': '图上无 JDG20', 'kind': 'pipe', 'term': 'JDG20'},
             {'item_id': 'S', 'conclusion': '图上无 SC25', 'kind': 'pipe', 'term': 'SC25'},
             {'item_id': 'T', 'conclusion': '图上无 SC32', 'kind': 'pipe', 'term': 'SC32'},
             {'item_id': 'Q', 'conclusion': '图上无 SC20', 'kind': 'pipe', 'term': 'SC20'}]
    _, _, data, _ = run(tmp_path, items=items, drawings=[package(tmp_path, 'D1', layout=LAYOUT + [note] + other)])
    d = by_id(data)
    for k in 'WJ':
        assert d[k]['status'] == 'hit' and d[k]['result']['hits_by_sheet_kind'] == {'表与说明': 1}
        assert d[k]['result']['samples'][0]['sheet'] == 'T1' and d[k]['result']['samples'][0]['handle'] == 'N1'
    assert d['S']['result']['hits'] == 1 and d['T']['result']['hits'] == 1   # SCΦ25, SC DN32
    assert d['Q']['status'] == 'no_hit_full'                                  # JDG200 / SC25 are not SC20
    assert d['J']['result']['hits'] == 1                                      # JDG200 is not JDG20


def test_input_problems_are_all_reported(tmp_path):
    bad = [{'item_id': 'A', 'conclusion': 'x', 'kind': 'spec', 'term': 'ZBN-YJV'}, {'item_id': 'A', 'conclusion': 'x', 'kind': 'text', 'term': 'x'},
           {'item_id': 'B', 'kind': 'nope'}, {'item_id': 'C', 'conclusion': '', 'kind': 'text', 'term': 'x'}, {'item_id': 'E', 'conclusion': 'x', 'kind': 'text', 'term': 'x', 'patterns': ['(']},
           {'item_id': 'F', 'conclusion': 'x', 'kind': 'block'}]
    tmp_path.mkdir(exist_ok=True)
    d = package(tmp_path, 'D1'); d['sheets']['kind_map'] = {}
    code, s, data, _ = run(tmp_path, items=bad, drawings=[d, {**d}], unread_overrides=[{'drawing': 'D1', 'class': 'non_drawing'}])
    msgs = ' | '.join(s['problems'])
    assert code == 2 and data is None and s['status'] == 'INPUT_REQUIRED'
    for part in ("图纸编号 'D1' 为空或重复", "条目编号 'A' 为空或重复", 'B 的 kind 只能是', 'C 要写 conclusion', 'E 写法有误', 'F 写法有误：F：block 类要写 block_names',
                 'unread_overrides[0] 要写', '图页类型 说明/材料表 不认识'):
        assert part in msgs, (part, msgs)


def test_output_directory_is_never_overwritten(tmp_path):
    code, _, _, out = run(tmp_path)
    assert code == 0
    p = tmp_path / 'input.json'
    r = subprocess.run([sys.executable, str(SCRIPT), '--input', str(p), '--output-dir', str(out)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert r.returncode == 2 and '已有检索记录' in r.stdout
