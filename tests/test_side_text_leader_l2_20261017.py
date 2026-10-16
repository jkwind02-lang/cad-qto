"""第五批 L2：旁注文字条件追引线（登记 20261016-1824「按引线对箱号」，0830、1426 剩余部分；并入 20261017-0027 相邻块定义里的旁注）。

合成图（毫米，输出米）。箱块 BOX 为 100×100 方框，引线画在 LEAD 层，箱号文字 AL# 在 NOTE 层；规则只写引线不写半径：
touch_tolerance 0.1 m、text_tolerance 0.3 m、snap_tolerance 0.01 m、max_segments 3。边界清单（备课第二节 L2）：
- 一条引线到一段文字 → 已绑定（多段线、两条直线串接、LEADER 实体、楼层块里的引线各一例）；
- 检查点一后（决策会话 10-17）：只有两只候选的引线到同一段文字、或引线一端落在两个候选外框之间分不清 → 引线未决；
- 一只候选的引线分叉到两段文字、两条引线各到一段 → 多段文字（与半径同）；
- 引线中途断开、文字在引线末端容差外、超过最多段数 → 不算引线（无文字），原因记 leader_notes；
- 引线只从另一个候选外框中间穿过 → 不算那个候选的引线；引线图层以外的线不走；
- 半径与引线都写：同一候选引线文字优先于半径内的其他文字；引线连到 A 的文字，别的候选不能凭半径认领或共用；
- 只分型的规则（role: classify）：半径内命中同一条规则的几只候选可以共用同一段文字；一只候选落在两段分型文字内照旧未决；
- 包外候选不参与认领（R2）；箱号作实例键时“文字无块”照旧列出。
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills/_qto-shared-evidence/scripts'
POINTS = ROOT / 'skills/qto-components-points/scripts'
SQUARE = [(-50, -50), (50, -50), (50, 50), (-50, 50)]
LEAD = {'layer': 'LEAD'}
LEADER = {'layer_pattern': '^lead$', 'touch_tolerance': 0.1, 'text_tolerance': 0.3, 'snap_tolerance': 0.01, 'max_segments': 3}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def note(space, value, x, y, layer='NOTE'):
    space.add_text(value, dxfattribs={'insert': (x, y), 'height': 100, 'layer': layer})


def box(msp, x, y):
    return 'PT-CAD-' + msp.add_blockref('BOX', (x, y)).dxf.handle


def build(tmp_path, draw, cond, scope=None, extra_rules=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in ('LEAD', 'NOTE', 'WIRE'):
        doc.layers.add(name)
    doc.blocks.new('BOX').add_lwpolyline(SQUARE, close=True)
    ids = draw(doc, doc.modelspace())
    dxf = tmp_path / 'leader.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    src = sha(dxf)
    boxes = sorted(v for k, v in ids.items() if k != 'FLOOR')
    rules = [{'rule_id': 'BOX', 'block_pattern': '^BOX$', 'decision': 'include', 'category': '配电箱', 'system': '动力',
              'spec': '箱', 'unit': '台', 'quantity_level': '规则推算量', 'text_condition': cond,
              'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]},
             *extra_rules]
    records = {'BOX': ('include', boxes), **{r['rule_id']: (r['decision'], [ids['FLOOR']]) for r in extra_rules}}
    entries = [{'adjudication_id': f'M-{rid}', 'subject_id': rid, 'professional_owner': 'qto-components-points',
                'decision': decision, 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
                'recognized_at': '2026-10-17T00:00:00+00:00', 'source_id': 'SYN', 'evidence_ids': ['G'],
                'object_ids': objects, 'basis': '合成箱号与引线', 'scope_basis': '全图', 'identity_basis': '源句柄',
                'cross_check': '按图面布置核对', 'uncertainties': ['合成']}
               for rid, (decision, objects) in records.items()]
    ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1',
              'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
              'evidence': [{'evidence_id': 'G', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                            'kind': 'cad_property', 'locator': 'Model', 'observation': '合成'}],
              'entries': entries}
    lp = tmp_path / 'ledger.json'; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': scope or {'layout': 'Model'}, 'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': [{'adjudication_id': f'M-{r["rule_id"]}', **r} for r in rules]}
    pp = tmp_path / 'profile.json'; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    result = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', neutral / 'CAD中性取证清单.json',
                 '--profile', pp, '--adjudication-ledger', lp, '--output-dir', out)
    return ids, result, out


def outcome(result, out):
    assert result.returncode == 0, result.stderr
    report = json.loads((out / '点位识别报告.json').read_text(encoding='utf-8'))
    disp = {row['physical_id']: row for row in map(json.loads, (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines())}
    count_input = json.loads((out / '点位唯一实例计数输入.json').read_text(encoding='utf-8'))
    adopted = {row['physical_id']: row for row in count_input['reconciled_instances']}
    return report, disp, adopted


def key_cond(**extra):
    return {'pattern': r'^AL\d+$', 'layer_pattern': '^NOTE$', 'as_instance_key': True, 'basis': '箱号经引线引出（作实例键）',
            'leader': dict(LEADER), **extra}


# ---------- leader only ----------

def draw_leaders(doc, msp):
    ids = {}
    ids['B1'] = box(msp, 0, 0)                                     # polyline leader
    msp.add_lwpolyline([(50, 50), (1500, 1500), (3000, 1500)], dxfattribs=LEAD); note(msp, 'AL1', 1550, 1600)
    ids['B2'] = box(msp, 10000, 0)                                 # two LINEs joined end to end
    msp.add_line((10050, 0), (11000, 0), dxfattribs=LEAD); msp.add_line((11000, 0), (12000, 500), dxfattribs=LEAD)
    note(msp, 'AL2', 12050, 550)
    ids['B3'] = box(msp, 20000, 0)                                 # branches to two texts
    msp.add_lwpolyline([(20050, 50), (21000, 1000), (22000, 1000)], dxfattribs=LEAD)
    msp.add_line((21000, 1000), (21000, 2000), dxfattribs=LEAD); note(msp, 'AL3', 22050, 1050); note(msp, 'AL4', 21050, 2050)
    ids['B4'], ids['B5'] = box(msp, 30000, 0), box(msp, 32000, 0)  # two leaders to one text
    msp.add_line((30050, 50), (30950, 1000), dxfattribs=LEAD); msp.add_line((31950, 50), (31050, 1000), dxfattribs=LEAD)
    note(msp, 'AL5', 31000, 1100)
    ids['B6'] = box(msp, 40000, 0)                                 # broken half way, and a second line reaching nothing
    msp.add_line((40050, 50), (41000, 1000), dxfattribs=LEAD); msp.add_line((41100, 1100), (42000, 1100), dxfattribs=LEAD)
    note(msp, 'AL6', 42050, 1150); msp.add_line((39950, -50), (39000, -1000), dxfattribs=LEAD)
    ids['B7'], ids['B8'] = box(msp, 50000, 0), box(msp, 50290, 0)  # leader end 0.095 m from both boxes
    msp.add_line((50145, 0), (51000, 1000), dxfattribs=LEAD); note(msp, 'AL7', 51050, 1050)
    ids['B9'] = box(msp, 60000, 0)                                 # a line on a layer the leader does not read
    msp.add_line((60050, 50), (61000, 1000), dxfattribs={'layer': 'WIRE'}); note(msp, 'AL8', 61050, 1050)
    ids['B10'], ids['B11'] = box(msp, 70000, 0), box(msp, 71000, 1000)   # passes straight through B11's box
    msp.add_line((70050, 50), (72000, 2000), dxfattribs=LEAD); note(msp, 'AL10', 72050, 2050)
    ids['B12'] = box(msp, 80000, 0)                                # text 0.5 m from the leader end
    msp.add_line((80050, 50), (81000, 1000), dxfattribs=LEAD); note(msp, 'AL12', 81500, 1000)
    ids['B13'] = box(msp, 90000, 0)                                # a LEADER entity
    msp.add_leader([(90050, 50), (90800, 800), (91500, 800)], dxfattribs=LEAD); note(msp, 'AL13', 91550, 850)
    ids['B15'] = box(msp, 100000, 0)                               # four segments, max_segments 3
    msp.add_lwpolyline([(100050, 50), (100500, 500), (101000, 500), (101500, 1000), (102000, 1000)], dxfattribs=LEAD)
    note(msp, 'AL15', 102050, 1050)
    ids['B16'] = box(msp, 110000, 0)                               # two leaders, each to its own text
    msp.add_line((110050, 50), (111000, 1000), dxfattribs=LEAD); note(msp, 'AL16', 111050, 1050)
    msp.add_line((109950, -50), (109000, -1000), dxfattribs=LEAD); note(msp, 'AL17', 108950, -950)
    ids['B18'] = box(msp, 120000, 0)                               # a leader, and a line at the box that reaches no text
    msp.add_line((120050, 50), (121000, 1000), dxfattribs=LEAD); note(msp, 'AL18', 121050, 1050)
    msp.add_line((119950, -50), (115000, -50), dxfattribs=LEAD)
    ids['B19'] = box(msp, 130000, 0)                               # the same box number drawn twice at the leader end
    msp.add_line((130050, 50), (131000, 1000), dxfattribs=LEAD); note(msp, 'AL19', 131050, 1050); note(msp, 'AL19', 131020, 1030)
    floor = doc.blocks.new('FLOOR')                                # leader and text inside a floor block
    inner = floor.add_blockref('BOX', (0, 0)).dxf.handle
    floor.add_lwpolyline([(50, 50), (800, 800), (1500, 800)], dxfattribs=LEAD); note(floor, 'AL20', 1550, 850)
    top = msp.add_blockref('FLOOR', (0, 20000)).dxf.handle
    ids['FLOOR'], ids['NESTED'] = f'PT-CAD-{top}', f'PT-CAD-{top}/FLOOR/CAD-{inner}'
    return ids


FLOOR_RULE = {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}


@pytest.fixture(scope='module')
def leaders(tmp_path_factory):
    ids, result, out = build(tmp_path_factory.mktemp('leaders'), draw_leaders, key_cond(), extra_rules=[FLOOR_RULE])
    return (ids, *outcome(result, out), out)


def test_one_leader_to_one_text_binds_it(leaders):
    ids, report, disp, adopted, out = leaders
    keys = {name: adopted[ids[name]]['instance_key'] for name in ('B1', 'B2', 'B10', 'B13', 'B18', 'B19', 'NESTED')}
    assert keys == {'B1': 'AL1', 'B2': 'AL2', 'B10': 'AL10', 'B13': 'AL13', 'B18': 'AL18', 'B19': 'AL19', 'NESTED': 'AL20'}
    assert len(disp[ids['B19']]['side_text']['same_text_ids']) == 1   # the copy is listed, not a branch
    b18 = disp[ids['B18']]['side_text']                           # the line that reaches no text is listed, not a second leader
    assert len(b18['leader_segment_ids']) == 1 and len(b18['other_line_segment_ids']) == 1
    assert 'other_line_segment_ids' not in disp[ids['B1']]['side_text']
    b1 = disp[ids['B1']]['side_text']
    assert b1['status'] == '已绑定' and b1['via'] == 'leader' and b1['distance'] == pytest.approx(0.111803, abs=1e-5)
    assert len(b1['leader_segment_ids']) == 2 and all(s.startswith('SEG-CAD-') for s in b1['leader_segment_ids'])
    assert len(disp[ids['B2']]['side_text']['leader_handles']) == 2      # two LINEs
    assert disp[ids['B13']]['side_text']['leader_segment_ids'][0].startswith('SEG-CAD-')   # the LEADER entity's vertices
    assert '/FLOOR/CAD-' in disp[ids['NESTED']]['side_text']['leader_segment_ids'][0]
    assert '经引线' in disp[ids['B1']]['basis'] and adopted[ids['B1']]['side_text']['via'] == 'leader'


def test_only_a_text_two_candidates_lead_to_is_unresolved(leaders):
    ids, report, disp, adopted, out = leaders
    for pair, problem in ((('B4', 'B5'), '两条引线到同一段文字'), (('B7', 'B8'), '引线端点落在两只候选之间，分不清')):
        for a, b in (pair, pair[::-1]):
            side = disp[ids[a]]['side_text']
            assert disp[ids[a]]['disposition'] == 'unresolved' and side['status'] == '引线未决', a
            assert [p['problem'] for p in side['leader_problems']] == [problem] and side['shared_with'] == [ids[b]], a
    assert disp[ids['B4']]['basis'].startswith('引线未决：')
    for name, texts in (('B3', ['AL3', 'AL4']), ('B16', ['AL16', 'AL17'])):   # one candidate, two texts: as with a radius
        side = disp[ids[name]]['side_text']
        assert disp[ids[name]]['disposition'] == 'unresolved' and side['status'] == '多段文字', name
        assert sorted(t['text'] for t in side['texts']) == texts and all(t['via'] == 'leader' for t in side['texts']), name
    for name in ('B6', 'B12', 'B15'):        # a line at the box that reaches no text is no leader; the reason is kept
        side = disp[ids[name]]['side_text']
        assert disp[ids[name]]['disposition'] == 'unresolved' and side['status'] == '无文字' and side['leader_notes'], name
    assert '超过 3 段' in disp[ids['B15']]['side_text']['leader_notes'][0]
    assert disp[ids['B6']]['side_text']['leader_notes'] == ['BOX 挨着外框的 2 组线没到符合的文字（1、1 段）']   # counted, not repeated
    assert disp[ids['B12']]['side_text']['leader_notes'] == ['BOX 挨着外框的线没到符合的文字（1 段）']
    assert '没到符合的文字' in disp[ids['B6']]['basis']


def test_foreign_layers_and_pass_through_are_no_leader(leaders):
    ids, report, disp, adopted, out = leaders
    for name in ('B9', 'B11'):
        assert disp[ids[name]]['side_text'] == {'status': '无文字', 'texts': [], 'shared_with': []}, name
        assert disp[ids[name]]['disposition'] == 'unresolved' and '没有引线' in disp[ids[name]]['basis']


def test_report_lists_unclaimed_keys_and_the_leader_graph(leaders):
    ids, report, disp, adopted, out = leaders
    side = report['side_text_binding']
    assert sorted(t['text'] for t in side['unclaimed_key_texts']) == ['AL12', 'AL15', 'AL6', 'AL8']
    assert side['status_counts'] == {'已绑定': 7, '引线未决': 4, '多段文字': 2, '无文字': 5}
    graph = side['leader_graphs'][0]
    assert graph['layer_pattern'] == '^lead$' and graph['rule_ids'] == ['BOX'] and graph['unsupported'] == {}
    assert graph['counts']['segments'] == 28 and graph['expanded_blocks'] == ['FLOOR']
    rows = (out / '点位旁注绑定.csv').read_text(encoding='utf-8-sig').splitlines()
    assert rows[0].startswith('kind,status,physical_id') and rows[0].endswith(',via,leader_handles,leader_problems')
    assert any(',leader,' in row and 'AL1' in row for row in rows)


# ---------- radius and leader together ----------

def draw_mixed(doc, msp):
    ids = {}
    ids['C1'] = box(msp, 0, 0)                                     # leader to AL1, radius holds only AL9
    msp.add_lwpolyline([(50, 50), (1500, 1500), (3000, 1500)], dxfattribs=LEAD); note(msp, 'AL1', 1550, 1600)
    note(msp, 'AL9', 1000, 0)
    ids['C2'] = box(msp, 10000, 0); note(msp, 'AL2', 10800, 0)    # no leader: the radius decides
    ids['C3'] = box(msp, 20000, 0)                                 # its leader's text also in C4's radius
    msp.add_lwpolyline([(20050, 50), (21500, 1500), (22000, 1500)], dxfattribs=LEAD); note(msp, 'AL3', 22050, 1600)
    ids['C4'] = box(msp, 22800, 1700)
    ids['C5'] = box(msp, 30000, 0)                                 # leader and radius agree
    msp.add_line((30050, 50), (30600, 600), dxfattribs=LEAD); note(msp, 'AL5', 30650, 650)
    return ids


def test_a_leader_wins_over_radii_and_takes_its_text_from_other_candidates(tmp_path):
    ids, result, out = build(tmp_path, draw_mixed, key_cond(radius=1.5))
    report, disp, adopted = outcome(result, out)
    assert adopted[ids['C1']]['instance_key'] == 'AL1' and disp[ids['C1']]['side_text']['via'] == 'leader'   # beats AL9 nearby
    assert adopted[ids['C2']]['instance_key'] == 'AL2' and disp[ids['C2']]['side_text'].get('via', 'radius') == 'radius'
    assert adopted[ids['C3']]['instance_key'] == 'AL3'
    c4 = disp[ids['C4']]['side_text']                              # AL3 lies in its radius, but C3's leader reaches it
    assert disp[ids['C4']]['disposition'] == 'unresolved' and c4['status'] == '无文字'
    assert [(t['text'], t['leader_of']) for t in c4['leader_texts_elsewhere']] == [('AL3', [ids['C3']])]
    assert '别的候选的引线' in disp[ids['C4']]['basis']
    assert adopted[ids['C5']]['instance_key'] == 'AL5' and disp[ids['C5']]['side_text']['via'] == 'leader'
    assert [t['text'] for t in report['side_text_binding']['unclaimed_key_texts']] == ['AL9']


# ---------- R2: the out-of-package candidate claims nothing ----------

def draw_scoped(doc, msp):
    ids = {'IN': box(msp, 500, 0), 'OUT': box(msp, 2600, 0)}
    msp.add_line((550, 50), (1400, 500), dxfattribs=LEAD); msp.add_line((2550, 50), (1600, 500), dxfattribs=LEAD)
    note(msp, 'AL1', 1500, 600)
    return ids


def test_out_of_package_candidate_claims_no_leader_text(tmp_path):
    scope = {'layout': 'Model', 'bounds': {'xmin': 0, 'xmax_exclusive': 2000, 'ymin': -100, 'ymax_exclusive': 1000}}
    ids, result, out = build(tmp_path, draw_scoped, key_cond(), scope=scope)
    report, disp, adopted = outcome(result, out)
    assert sorted(disp) == [ids['IN']] and adopted[ids['IN']]['instance_key'] == 'AL1'
    assert report['side_text_binding']['status_counts'] == {'已绑定': 1}


# ---------- 20261017-0027: a note inside a neighbouring block's definition ----------

def draw_doors(doc, msp):
    door = doc.blocks.new('DOOR')
    door.add_lwpolyline([(0, 0), (3000, 0)]); note(door, 'JLM', 1500, 300, layer='0')
    msp.add_blockref('DOOR', (0, 0))
    ids = {'P1': box(msp, 1000, 1000), 'P2': box(msp, 1700, 1000), 'S1': box(msp, 9000, 1000)}
    msp.add_blockref('DOOR', (8000, 0))
    return ids


def test_note_inside_a_neighbouring_block_binds_with_its_source(tmp_path):
    cond = {'pattern': '^JLM$', 'radius': 1.2, 'basis': '卷帘门块定义里的 JLM'}
    ids, result, out = build(tmp_path, draw_doors, cond)
    report, disp, adopted = outcome(result, out)
    s1 = disp[ids['S1']]['side_text']
    assert disp[ids['S1']]['disposition'] == 'adopted' and s1['status'] == '已绑定'
    assert s1['source_insert'].startswith('PT-CAD-') and s1['source_block'] == 'DOOR' and s1['text_handle']
    assert s1['text_id'] == f"{s1['source_insert'][3:]}/DOOR/{s1['text_handle']}"
    # a pair of buttons beside one door: one note in both radii is shared (the b2 rule), not bound twice
    for a, b in (('P1', 'P2'), ('P2', 'P1')):
        assert disp[ids[a]]['side_text']['status'] == '文字共用' and disp[ids[a]]['side_text']['shared_with'] == [ids[b]]


def draw_doors_and_fan(doc, msp):
    ids = draw_doors(doc, msp)
    note(msp, 'FAN', 9300, 1500)          # a second classifying note beside S1
    return ids


def test_a_classify_note_may_be_shared_by_the_candidates_in_its_radius(tmp_path):
    """Decision 10-17 ①: a rule declared classify-only lets several candidates share one note in their radii under that
    rule; a candidate in the radius of two different notes stays undecided; instance keys never share."""
    cond = {'pattern': '^(JLM|FAN)$', 'radius': 1.2, 'basis': '卷帘门块定义里的 JLM（只分型）', 'role': 'classify'}
    ids, result, out = build(tmp_path, draw_doors_and_fan, cond)
    report, disp, adopted = outcome(result, out)
    for a, b in (('P1', 'P2'), ('P2', 'P1')):
        side = disp[ids[a]]['side_text']
        assert disp[ids[a]]['disposition'] == 'adopted' and side['status'] == '已绑定' and side['shared_with'] == [ids[b]], a
        assert adopted[ids[a]]['side_text']['shared_with'] == [ids[b]] and '共用' in disp[ids[a]]['basis']
    s1 = disp[ids['S1']]['side_text']
    assert disp[ids['S1']]['disposition'] == 'unresolved' and s1['status'] == '多段文字'
    assert sorted(t['text'] for t in s1['texts']) == ['FAN', 'JLM']
    assert report['side_text_binding']['status_counts'] == {'已绑定': 2, '多段文字': 1}


# ---------- rejected inputs ----------

@pytest.mark.parametrize('cond,message', [
    (key_cond(leader={**LEADER, 'layer_pattern': None}), 'BOX.text_condition.leader.layer_pattern 必须是非空文本'),
    (key_cond(leader={**LEADER, 'touch_tolerance': -1}), 'BOX.text_condition.leader.touch_tolerance 不能为负'),
    (key_cond(leader={**LEADER, 'text_tolerance': 0}), 'BOX.text_condition.leader.text_tolerance 必须大于0'),
    (key_cond(leader={**LEADER, 'max_segments': 0}), 'BOX.text_condition.leader.max_segments 必须是正整数'),
    (key_cond(leader={**LEADER, 'max_segments': 2.5}), 'BOX.text_condition.leader.max_segments 必须是正整数'),
    (key_cond(leader=[1]), 'BOX.text_condition.leader 必须是对象'),
    ({'pattern': '^AL$', 'basis': 'x'}, 'BOX.text_condition.radius 必须是有限数值'),   # no leader: the radius is required
    ({'pattern': '^AL$', 'radius': 1, 'basis': 'x', 'role': 'share'}, 'BOX.text_condition.role 只能是 classify'),
    ({'pattern': '^AL$', 'radius': 1, 'basis': 'x', 'role': 'classify', 'as_instance_key': True},
     'BOX.text_condition.role 为 classify 时不能作实例键'),
])
def test_bad_leader_condition_is_rejected(tmp_path, cond, message):
    ids, result, out = build(tmp_path, lambda doc, msp: {'B': box(msp, 0, 0)}, cond)
    assert result.returncode == 2 and message in result.stderr
