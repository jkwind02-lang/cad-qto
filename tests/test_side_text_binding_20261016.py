"""b2（登记 20261015-0830 旁注区分、20261015-1426 箱号作实例键）：点位规则的旁注文字条件。

合成图（毫米，输出米，半径 0.6 m / 1.0 m）：
- 灯 L0～L7：旁注 a/b/c 区分三防、防爆、黄光；c 写成 MTEXT 格式码；没有旁注的走兜底规则；
  一段 a 落在 L4、L5 两盏灯的半径内（文字共用）；L6 半径内有 a、b 两段（多段文字）；L7 的 b 在半径外（合法对照）；
  楼层块 FLOOR 里的一盏灯，旁注写在楼层块里。
- 配电箱 GPB：箱号文字作实例键；一台没有箱号（无文字），一段箱号没有箱（文字无块），两台挂同一个箱号（同号多块）。
期望：只有半径内恰好一段、且不和别的候选共用的文字才绑定；歧义的记未决，不按远近硬绑。
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


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=120)


def note(space, value, x, y, layer='NOTE'):
    space.add_text(value, dxfattribs={'insert': (x, y), 'height': 100, 'layer': layer})


def build(tmp_path, rules_for, draw, scope=None, partial=False):
    """draw(doc, msp) -> {name: physical_id}; rules_for(ids) -> (rules, {rule_id: (decision, object_ids)}).
    partial: the extractor reports PARTIAL (exit 3) for a MINSERT and still writes the package."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = 4
    ids = draw(doc, doc.modelspace())
    dxf = tmp_path / 'side.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert extracted.returncode in ((0, 3) if partial else (0,)), extracted.stdout + extracted.stderr
    src = sha(dxf)
    rules, records = rules_for(ids)
    entries = [{'adjudication_id': f'M-{rid}', 'subject_id': rid, 'professional_owner': 'qto-components-points',
                'decision': decision, 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
                'recognized_at': '2026-10-16T00:00:00+00:00', 'source_id': 'SYN', 'evidence_ids': ['G'],
                'object_ids': objects, 'basis': '合成图例与旁注', 'scope_basis': '全图', 'identity_basis': '源句柄',
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


# ---------- lamps told apart by side letters ----------

LAMP_AT = {'L0': 0, 'L1': 3000, 'L2': 6000, 'L3': 9000, 'L4': 12000, 'L5': 12800, 'L6': 15000, 'L7': 18000}


def draw_lamps(doc, msp):
    doc.blocks.new('LAMP').add_lwpolyline(SQUARE, close=True)
    floor = doc.blocks.new('FLOOR')
    inner = floor.add_blockref('LAMP', (0, 0)).dxf.handle
    note(floor, 'c', 200, 200)                      # side note drawn inside the floor block
    ids = {name: 'PT-CAD-' + msp.add_blockref('LAMP', (x, 0)).dxf.handle for name, x in LAMP_AT.items()}
    top_floor = msp.add_blockref('FLOOR', (0, 10000)).dxf.handle
    ids['FLOOR'] = f'PT-CAD-{top_floor}'
    ids['NESTED'] = f'PT-CAD-{top_floor}/FLOOR/CAD-{inner}'
    note(msp, 'a', 200, 200)                        # L0
    note(msp, 'b', 3200, 200)                       # L1
    msp.add_mtext('\\Lc\\l', dxfattribs={'insert': (6200, 200), 'char_height': 100, 'layer': 'NOTE'})   # L2, MTEXT codes
    note(msp, 'a', 12400, 300)                      # 0.5 m from both L4 and L5: shared
    note(msp, 'a', 15200, 0); note(msp, 'b', 14800, 0)   # L6: two notes
    note(msp, 'b', 19000, 0)                        # 1.0 m from L7: outside 0.6 m
    note(msp, 'a', 3000, 400, layer='DIM')          # near L1 but on a layer the rules do not read
    return ids


def lamp_rules(ids, extra=None):
    lamps = [ids[name] for name in LAMP_AT] + [ids['NESTED']]
    side = lambda letter, what: {'pattern': f'^{letter}$', 'radius': 0.6, 'layer_pattern': '^NOTE$',
                                 'basis': f'图例：旁注 {letter} 为{what}'}
    common = {'block_pattern': '^LAMP$', 'decision': 'include', 'system': '照明', 'unit': '套', 'quantity_level': '规则推算量',
              'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}
    rules = [{'rule_id': 'LA', **common, 'priority': 10, 'category': '三防灯', 'spec': '三防', 'text_condition': side('a', '三防灯')},
             {'rule_id': 'LB', **common, 'priority': 10, 'category': '防爆灯', 'spec': '防爆', 'text_condition': side('b', '防爆灯')},
             {'rule_id': 'LC', **common, 'priority': 10, 'category': '黄光灯', 'spec': '黄光', 'text_condition': side('c', '黄光灯')},
             {'rule_id': 'L0', **common, 'category': '普通灯', 'spec': '普通'},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    for rule in rules:
        rule.update((extra or {}).get(rule['rule_id'], {}))
    records = {r['rule_id']: (r['decision'], lamps) for r in rules if r['rule_id'] != 'FLOOR'}
    records['FLOOR'] = ('container', [ids['FLOOR']])
    return rules, records


@pytest.fixture(scope='module')
def lamps(tmp_path_factory):
    ids, result, out = build(tmp_path_factory.mktemp('lamps'), lamp_rules, draw_lamps)
    return (ids, *outcome(result, out), out)


def test_one_note_in_radius_picks_the_lamp_type(lamps):
    ids, report, disp, adopted, out = lamps
    got = {name: adopted[ids[name]]['category'] for name in ('L0', 'L1', 'L2', 'NESTED')}
    assert got == {'L0': '三防灯', 'L1': '防爆灯', 'L2': '黄光灯', 'NESTED': '黄光灯'}
    bound = disp[ids['L2']]['side_text']
    assert bound['status'] == '已绑定' and bound['text'] == 'c' and bound['rule_id'] == 'LC' and bound['distance'] == pytest.approx(0.282843, abs=1e-5)
    assert adopted[ids['L0']]['side_text'] == {'text': 'a', 'text_id': adopted[ids['L0']]['side_text']['text_id'],
                                               'distance': pytest.approx(0.282843, abs=1e-5), 'rule_id': 'LA'}
    assert '旁注“a”' in disp[ids['L0']]['basis'] and '图例：旁注 a 为三防灯' in disp[ids['L0']]['basis']
    assert 'instance_key' not in adopted[ids['L0']]          # not a key rule


def test_no_note_falls_back_and_note_outside_radius_is_not_bound(lamps):
    ids, report, disp, adopted, out = lamps
    for name in ('L3', 'L7'):                                # L7's b is 1.0 m away (legal control)
        assert adopted[ids[name]]['category'] == '普通灯'
        assert disp[ids[name]]['side_text'] == {'status': '无文字', 'texts': [], 'shared_with': []}
        assert 'side_text' not in adopted[ids[name]]


def test_shared_note_and_two_notes_stay_unresolved(lamps):
    ids, report, disp, adopted, out = lamps
    for name in ('L4', 'L5'):
        row = disp[ids[name]]
        assert row['disposition'] == 'unresolved' and ids[name] not in adopted
        assert row['side_text']['status'] == '文字共用' and row['basis'].startswith('旁注文字共用：“a”')
    assert disp[ids['L4']]['side_text']['shared_with'] == [ids['L5']]
    l6 = disp[ids['L6']]
    assert l6['disposition'] == 'unresolved' and l6['side_text']['status'] == '多段文字'
    assert sorted(t['text'] for t in l6['side_text']['texts']) == ['a', 'b'] and l6['rule_ids'] == ['LA', 'LB', 'LC']
    assert report['point_workpackage_complete'] is False


def test_report_and_csv_list_every_candidate(lamps):
    ids, report, disp, adopted, out = lamps
    side = report['side_text_binding']
    assert side['text_rule_ids'] == ['LA', 'LB', 'LC'] and side['candidate_instance_count'] == 9
    assert side['status_counts'] == {'多段文字': 1, '已绑定': 4, '文字共用': 2, '无文字': 2}
    assert side['unclaimed_key_text_count'] == 0 and side['duplicate_instance_keys'] == []
    rows = (out / '点位旁注绑定.csv').read_text(encoding='utf-8-sig').splitlines()
    assert rows[0].startswith('kind,status,physical_id') and len(rows) == 1 + 9
    assert report['artifacts']['side_text_binding_csv'].endswith('点位旁注绑定.csv')


# ---------- box numbers as the instance key ----------

def draw_boxes(doc, msp):
    doc.blocks.new('GPB').add_lwpolyline(SQUARE, close=True)
    ids = {name: 'PT-CAD-' + msp.add_blockref('GPB', (x, 0)).dxf.handle
           for name, x in {'B0': 0, 'B1': 5000, 'B2': 10000, 'B3': 20000}.items()}
    note(msp, '1AL1', 300, 600); note(msp, '1AL2', 5300, 600)
    note(msp, '1AL1', 20300, 600)      # a second box drawn with the same number
    note(msp, '1AL9', 30000, 0)        # a number with no box
    note(msp, '照明', 10300, 600)      # B2: only a word that is not a box number
    return ids


def box_rules(ids, cond=None):
    rule = {'rule_id': 'BOX', 'block_pattern': '^GPB$', 'decision': 'include', 'category': '配电箱', 'system': '照明',
            'spec': '照明箱', 'unit': '台', 'quantity_level': '规则推算量',
            'text_condition': cond or {'pattern': r'^\d+A[LP]\w*$', 'radius': 1.0, 'as_instance_key': True,
                                       'basis': '箱号文字作实例键（GPB 属性是统一默认值）'},
            'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}
    return [rule], {'BOX': ('include', sorted(ids.values()))}


def test_box_number_becomes_instance_key_and_mismatches_are_listed(tmp_path):
    ids, result, out = build(tmp_path, box_rules, draw_boxes)
    report, disp, adopted = outcome(result, out)
    assert {adopted[ids[n]]['instance_key'] for n in ('B0', 'B1', 'B3')} == {'1AL1', '1AL2'}
    assert disp[ids['B0']]['instance_key'] == '1AL1'
    assert disp[ids['B2']]['disposition'] == 'unresolved' and disp[ids['B2']]['side_text']['status'] == '无文字'
    assert disp[ids['B2']]['basis'] == '半径内没有符合旁注条件的文字（无文字）：BOX'
    side = report['side_text_binding']
    assert [t['text'] for t in side['unclaimed_key_texts']] == ['1AL9'] and side['unclaimed_key_text_count'] == 1
    assert side['duplicate_instance_keys'] == [{'instance_key': '1AL1', 'physical_ids': sorted([ids['B0'], ids['B3']])}]
    rows = (out / '点位旁注绑定.csv').read_text(encoding='utf-8-sig').splitlines()
    assert sum(1 for row in rows if row.startswith('文字,文字无块')) == 1


def test_exclude_rule_can_take_a_side_note(tmp_path):
    def rules_for(ids):
        rules, records = box_rules(ids)
        rules.append({'rule_id': 'SPARE', 'block_pattern': '^GPB$', 'decision': 'exclude', 'priority': 10,
                      'text_condition': {'pattern': '^照明$', 'radius': 1.0, 'basis': '标“照明”的是示意框，不计'}})
        return rules, {**records, 'SPARE': ('exclude', records['BOX'][1])}
    ids, result, out = build(tmp_path, rules_for, draw_boxes)
    report, disp, adopted = outcome(result, out)
    assert disp[ids['B2']]['disposition'] == 'excluded' and disp[ids['B2']]['side_text']['text'] == '照明'
    assert ids['B2'] not in adopted and adopted[ids['B0']]['instance_key'] == '1AL1'


# ---------- Codex retest R2: the pre-pass prunes by the main walk's scope test ----------

BOUNDS = {'layout': 'Model', 'bounds': {'xmin': 900, 'xmax_exclusive': 1400, 'ymin': -100, 'ymax_exclusive': 100}}


def dev_rules(ids):
    rule = {'rule_id': 'DEV', 'block_pattern': '^(DEV|FAR)$', 'decision': 'include', 'category': '配电箱', 'system': '动力',
            'spec': '箱', 'unit': '台', 'quantity_level': '规则推算量',
            'text_condition': {'pattern': r'^DB-\w+$', 'radius': 0.5, 'as_instance_key': True, 'basis': '箱号作实例键'},
            'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}
    return [rule], {'DEV': ('include', sorted(ids.values()))}


def draw_bounded(second):
    """Codex's boundary drawing: 50x50 DEV at (1000,0) inside the package, note DB-BOUND at (1300,0); a second
    candidate 0.3 m on the other side of the note, outside the package (pruned), or a MINSERT there (no complete box)."""
    def draw(doc, msp):
        doc.blocks.new('DEV').add_lwpolyline([(-25, -25), (25, -25), (25, 25), (-25, 25)], close=True)
        ids = {'IN': 'PT-CAD-' + msp.add_blockref('DEV', (1000, 0)).dxf.handle}
        if second == 'outside':
            ids['OUT'] = 'PT-CAD-' + msp.add_blockref('DEV', (1600, 0)).dxf.handle
        elif second == 'minsert':
            ref = msp.add_blockref('DEV', (1600, 0)); ref.grid(size=(1, 2), spacing=(100, 100))
            ids['OUT'] = 'PT-CAD-' + ref.dxf.handle
        elif second == 'far_base':   # base point outside the package, drawing inside it
            doc.blocks.new('FAR').add_lwpolyline([(-725, -25), (-675, -25), (-675, 25), (-725, 25)], close=True)
            ids['FAR'] = 'PT-CAD-' + msp.add_blockref('FAR', (1900, 0)).dxf.handle
            note(msp, 'DB-FAR', 1900, 200)
        note(msp, 'DB-BOUND', 1300, 0)
        return ids
    return draw


def test_out_of_package_candidate_does_not_claim_the_note(tmp_path):
    ids, result, out = build(tmp_path, dev_rules, draw_bounded('outside'), scope=BOUNDS)
    report, disp, adopted = outcome(result, out)
    assert sorted(disp) == [ids['IN']]                       # the main walk prunes the outside block
    assert adopted[ids['IN']]['instance_key'] == 'DB-BOUND'
    assert adopted[ids['IN']]['side_text']['distance'] == pytest.approx(0.3)
    assert report['side_text_binding']['status_counts'] == {'已绑定': 1}


def test_far_base_point_with_drawing_inside_still_binds(tmp_path):
    ids, result, out = build(tmp_path, dev_rules, draw_bounded('far_base'), scope=BOUNDS)
    report, disp, adopted = outcome(result, out)
    # FAR's drawing sits at 1175..1225, inside the package although its base point (1900,0) is not: an insertion-point
    # filter would drop it. Distances are measured from the base point: DB-FAR is 0.2 m away, DB-BOUND 0.6 m.
    assert sorted(disp) == sorted([ids['IN'], ids['FAR']])
    assert adopted[ids['IN']]['instance_key'] == 'DB-BOUND' and adopted[ids['FAR']]['instance_key'] == 'DB-FAR'
    assert report['side_text_binding']['status_counts'] == {'已绑定': 2}


def test_candidate_without_a_complete_box_is_kept(tmp_path):
    ids, result, out = build(tmp_path, dev_rules, draw_bounded('minsert'), scope=BOUNDS, partial=True)
    report, disp, adopted = outcome(result, out)
    # The MINSERT has no complete envelope, so the main walk keeps it (unresolved) and so does the pre-pass:
    # it still claims the note, and the inside block stays unresolved rather than being bound by guess.
    assert disp[ids['OUT']]['disposition'] == 'unresolved'
    assert disp[ids['IN']]['disposition'] == 'unresolved' and disp[ids['IN']]['side_text']['status'] == '文字共用'


# ---------- rejected inputs and the unchanged path ----------

@pytest.mark.parametrize('cond,message', [
    ({'pattern': '^a$', 'radius': 0, 'basis': 'x'}, 'BOX.text_condition.radius 必须大于0'),
    ({'pattern': '^a$', 'radius': 1.0}, 'BOX.text_condition.basis 必须是非空文本'),
    ({'pattern': '(', 'radius': 1.0, 'basis': 'x'}, 'BOX.text_condition.pattern 正则表达式无效'),
    ({'pattern': '^a$', 'radius': 1.0, 'basis': 'x', 'as_instance_key': 'yes'}, 'BOX.text_condition.as_instance_key 必须是 true 或 false'),
])
def test_bad_text_condition_is_rejected(tmp_path, cond, message):
    ids, result, out = build(tmp_path, lambda ids: box_rules(ids, cond), draw_boxes)
    assert result.returncode == 2 and message in result.stderr


def test_text_condition_only_on_include_or_exclude(tmp_path):
    def rules_for(ids):
        rules, records = box_rules(ids)
        rules[0]['decision'] = 'container'
        return rules, {'BOX': ('container', records['BOX'][1])}
    ids, result, out = build(tmp_path, rules_for, draw_boxes)
    assert result.returncode == 2 and 'BOX.text_condition 只能写在 include 或 exclude 规则上' in result.stderr


def test_without_text_rules_nothing_new_is_written(tmp_path):
    def rules_for(ids):
        rules, records = box_rules(ids)
        rules[0].pop('text_condition')
        return rules, records
    ids, result, out = build(tmp_path, rules_for, draw_boxes)
    report, disp, adopted = outcome(result, out)
    assert 'side_text_binding' not in report and not (out / '点位旁注绑定.csv').exists()
    assert len(adopted) == 4 and not any('side_text' in row or 'instance_key' in row for row in [*adopted.values(), *disp.values()])
