"""d1（登记 20261016-1709 适配器对楼层块内动态块一律 unresolved）：动态块状态裁决。

合成图：楼层块 FLOOR 内插 4 个端子箱 TB，顶层再插 1 个 FLOOR 和 1 个 TB。ezdxf 造不出真动态块，
按备课做法在中性记录上补问题代码（dynamic_state_not_evaluated 等），再重算清单里的 SHA256。
期望：只剩两种动态块代码、命中带裁决（精确块名 + 依据 + 证据）的 include 规则才采用；
阵列等其他代码、没有裁决、裁决没列到的实例照旧未决；裁决写错一律拒收。
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
DYN = ('dynamic_state_not_evaluated', '发现动态块数据；当前读取器未核验原生可见状态和参数求值结果')
ANON = ('anonymous_block_state_unverified', '匿名块名称符合动态块候选形式；名称不能确定语义，需核对是否含动态可见状态')
ARRAY = ('array_instances_not_expanded', '阵列块需逐实例展开；当前记录不能视为一个完整实物实例')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=120)


def patch_rows(neutral, key, issues_by_handle):
    """Give the neutral rows of these handles the issue codes/texts; rewrite the manifest hash."""
    manifest_path = neutral / 'CAD中性取证清单.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    stream = Path(manifest['artifacts'][key]['path'])
    rows = [json.loads(line) for line in stream.read_text(encoding='utf-8').splitlines() if line.strip()]
    hit = 0
    for row in rows:
        issues = issues_by_handle.get(row.get('entity_handle'))
        if not issues:
            continue
        row['geometry_issue_codes'] = [code for code, _ in issues]
        row['geometry_issues'] = [message for _, message in issues]
        row['geometry_status'] = 'unsupported'
        hit += 1
    assert hit == sum(1 for value in issues_by_handle.values() if value)
    stream.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    manifest['artifacts'][key]['sha256'] = sha(stream)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')


def build(tmp_path, adjudication='default', inner_codes=None, top_codes=None, rule_extra=None, listed=None,
          extra_rules=(), drop_codes=False):
    """inner_codes: per inner TB index -> issue list (None = no issue); default all four DYN."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.blocks.new('TB').add_lwpolyline(SQUARE, close=True)
    doc.blocks.new('OTHER').add_circle((0, 0), 30)
    floor = doc.blocks.new('FLOOR')
    inner = [floor.add_blockref('TB', (1000 * i, 0)).dxf.handle for i in range(4)]
    floor.add_blockref('OTHER', (0, 500))
    msp = doc.modelspace()
    top_floor = msp.add_blockref('FLOOR', (0, 0)).dxf.handle
    top_tb = msp.add_blockref('TB', (8000, 0)).dxf.handle
    dxf = tmp_path / 'floor.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    codes = [DYN] * 4 if inner_codes is None else inner_codes
    patch_rows(neutral, 'block_definitions', {h: ([c] if isinstance(c, tuple) else c) for h, c in zip(inner, codes)})
    if top_codes is not None:
        patch_rows(neutral, 'layout_entities', {top_tb: top_codes})
    if drop_codes:   # old neutral data: issue texts, no codes
        manifest = json.loads((neutral / 'CAD中性取证清单.json').read_text(encoding='utf-8'))
        for key in ('block_definitions', 'layout_entities'):
            stream = Path(manifest['artifacts'][key]['path'])
            rows = [json.loads(line) for line in stream.read_text(encoding='utf-8').splitlines() if line.strip()]
            for row in rows:
                row.pop('geometry_issue_codes', None)
            stream.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
            manifest['artifacts'][key]['sha256'] = sha(stream)
        (neutral / 'CAD中性取证清单.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    ids = [f'PT-CAD-{top_floor}/FLOOR/CAD-{h}' for h in inner]
    top_id = f'PT-CAD-{top_tb}'
    src = sha(dxf)

    def entry(rid, subject, objects):
        return {'adjudication_id': rid, 'subject_id': subject, 'professional_owner': 'qto-components-points',
                'decision': 'include', 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
                'recognized_at': '2026-10-16T00:00:00+00:00', 'source_id': 'SYN', 'evidence_ids': ['G'],
                'object_ids': objects, 'basis': '合成图例', 'scope_basis': '全图', 'identity_basis': '源句柄',
                'cross_check': '按图面布置核对', 'uncertainties': ['合成']}
    objects = ids + [top_id] if listed is None else listed
    entries = [entry('M-TB', 'TB', objects)] + [entry(f'M-{r["rule_id"]}', r['rule_id'], r.pop('_objects')) for r in extra_rules]
    if (rule_extra or {}).get('decision') in ('exclude', 'container'):   # the model record states the same decision as the rule
        entries[0]['decision'] = rule_extra['decision']
    ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1',
              'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
              'evidence': [{'evidence_id': 'G', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                            'kind': 'cad_property', 'locator': 'Model', 'observation': '合成'},
                           {'evidence_id': 'VIS', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                            'kind': 'drawing_view', 'locator': 'Model', 'observation': '端子箱可见状态逐个看过'}],
              'entries': entries}
    lp = tmp_path / 'ledger.json'; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    rule = {'rule_id': 'TB', 'adjudication_id': 'M-TB', 'block_pattern': '^TB$', 'decision': 'include',
            'category': '端子箱', 'system': 'E', 'spec': '箱', 'unit': '台', 'quantity_level': '规则推算量',
            'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}
    if adjudication == 'default':
        adjudication = {'block_names': ['TB'], 'basis': '端子箱可见状态只切换标注，与计数无关', 'evidence_ids': ['VIS']}
    if adjudication is not None:
        rule['dynamic_state_adjudication'] = adjudication
    rule.update(rule_extra or {})
    rules = [rule] + [{k: v for k, v in r.items()} for r in extra_rules]
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model'}, 'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': rules + [{'rule_id': 'FLOOR', 'adjudication_id': 'M-FLOOR', 'block_pattern': '^(FLOOR|OTHER)$',
                                  'decision': 'container'}]}
    ledger['entries'].append({**entry('M-FLOOR', 'FLOOR', [f'PT-CAD-{top_floor}', f'PT-CAD-{top_floor}/FLOOR/CAD-' +
                                                           floor.query('INSERT[name=="OTHER"]')[0].dxf.handle]),
                              'decision': 'container'})
    lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    profile['adjudication_ledger']['sha256'] = sha(lp)
    pp = tmp_path / 'profile.json'; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    result = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', neutral / 'CAD中性取证清单.json',
                 '--profile', pp, '--adjudication-ledger', lp, '--output-dir', out)
    return ids, top_id, result, out


def outcome(result, out):
    assert result.returncode == 0, result.stderr
    report = json.loads((out / '点位识别报告.json').read_text(encoding='utf-8'))
    disp = {row['physical_id']: row for row in map(json.loads, (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines())}
    summary_path = out / '点位数量汇总.json'
    ready = json.loads(summary_path.read_text(encoding='utf-8'))['work_quantity_ready'] if summary_path.is_file() else None
    return report, disp, ready


def test_dynamic_inner_boxes_without_adjudication_stay_unresolved(tmp_path):
    ids, top_id, result, out = build(tmp_path, adjudication=None)
    report, disp, ready = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['unresolved'] * 4
    assert all(disp[i]['geometry_issue_codes'] == ['dynamic_state_not_evaluated'] for i in ids)
    assert report['point_workpackage_complete'] is False
    assert report['counts']['dynamic_state_adjudicated_count'] == 0
    assert disp[top_id]['disposition'] == 'adopted'   # the top-level TB has no issue
    assert ready is False


def test_dynamic_inner_boxes_with_exact_block_adjudication_are_adopted(tmp_path):
    ids, top_id, result, out = build(tmp_path)
    report, disp, ready = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['adopted'] * 4
    for n, i in enumerate(ids):
        assert disp[i]['dynamic_state_adjudication']['rule_id'] == 'TB'
        assert disp[i]['dynamic_state_adjudication']['evidence_ids'] == ['VIS']
        # 第五批 K2：动态块按块定义的当前图形参与范围裁剪（状态未核验，不参与外形归并）
        assert disp[i]['scope_geometry_bbox'] == pytest.approx([1000 * n - 50, -50, 1000 * n + 50, 50])
        assert disp[i]['scope_location_basis'] == 'transformed_geometry_extent_dynamic_state_unverified'
        assert '动态块状态裁决：端子箱可见状态只切换标注' in disp[i]['basis']
    assert 'dynamic_state_adjudication' not in disp[top_id]
    assert report['counts']['adopted_physical_instance_count'] == 5
    assert report['counts']['dynamic_state_adjudicated_count'] == 4
    assert report['dynamic_state_adjudication'][0]['unused_block_names'] == []
    assert report['point_workpackage_complete'] is True and report['block_expansion_failures'] == []
    assert ready is True
    # inner points are the insertion points through the FLOOR matrix (block unit mm -> m)
    assert disp[ids[2]]['point'][:2] == pytest.approx([2.0, 0.0])


def test_one_box_with_an_array_code_stays_unresolved(tmp_path):
    ids, _, result, out = build(tmp_path, inner_codes=[DYN, DYN, [DYN, ARRAY], DYN])
    report, disp, ready = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['adopted', 'adopted', 'unresolved', 'adopted']
    assert report['counts']['dynamic_state_adjudicated_count'] == 3
    assert report['point_workpackage_complete'] is False
    assert ready is False


def test_adjudication_listing_three_instances_leaves_the_fourth_unresolved(tmp_path):
    ids, _, _, _ = build(tmp_path / 'probe', adjudication=None)
    adjudication = {'block_names': ['TB'], 'basis': '逐个核对过可见状态', 'evidence_ids': ['VIS'], 'object_ids': ids[:3]}
    ids2, _, result, out = build(tmp_path / 'run', adjudication=adjudication)
    assert ids2 == ids   # same drawing, same handles
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['adopted'] * 3 + ['unresolved']
    assert report['point_workpackage_complete'] is False


def test_rule_hit_without_adjudication_for_this_block_stays_unresolved(tmp_path):
    # the adjudication names another block that exists (OTHER), not TB
    adjudication = {'block_names': ['OTHER'], 'basis': '另一种块', 'evidence_ids': ['VIS']}
    ids, _, result, out = build(tmp_path, adjudication=adjudication)
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['unresolved'] * 4
    assert report['dynamic_state_adjudication'][0]['unused_block_names'] == ['OTHER']


def test_top_level_dynamic_block_is_released_the_same_way(tmp_path):
    ids, top_id, result, out = build(tmp_path, adjudication=None, inner_codes=[None] * 4, top_codes=[DYN])
    _, disp, _ = outcome(result, out)
    assert disp[top_id]['disposition'] == 'unresolved'
    ids, top_id, result, out = build(tmp_path / 'b', inner_codes=[None] * 4, top_codes=[DYN])
    report, disp, ready = outcome(result, out)
    assert disp[top_id]['disposition'] == 'adopted' and disp[top_id]['dynamic_state_adjudication']['rule_id'] == 'TB'
    assert all('dynamic_state_adjudication' not in disp[i] for i in ids)
    assert ready is True


def test_anonymous_dynamic_candidate_code_is_released_too(tmp_path):
    ids, _, result, out = build(tmp_path, inner_codes=[ANON, DYN, [ANON, DYN], ANON])
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['adopted'] * 4
    assert disp[ids[2]]['dynamic_state_adjudication']['geometry_issue_codes'] == ['anonymous_block_state_unverified',
                                                                                  'dynamic_state_not_evaluated']


def test_issue_texts_without_codes_are_not_released(tmp_path):
    ids, _, result, out = build(tmp_path, drop_codes=True)
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['unresolved'] * 4


@pytest.mark.parametrize('adjudication, message', [
    ({'block_names': ['TB'], 'basis': '', 'evidence_ids': ['VIS']}, 'basis'),
    ({'block_names': ['TB'], 'basis': '   ', 'evidence_ids': ['VIS']}, 'basis'),
    ({'block_names': ['TB'], 'evidence_ids': ['VIS']}, 'basis'),
    ({'block_names': ['TB'], 'basis': '依据'}, 'evidence_ids'),
    ({'block_names': ['TB'], 'basis': '依据', 'evidence_ids': []}, 'evidence_ids'),
    ({'block_names': ['TB'], 'basis': '依据', 'evidence_ids': ['NOPE']}, '没有的证据'),
    ({'block_names': [], 'basis': '依据', 'evidence_ids': ['VIS']}, 'block_names'),
    ({'block_names': 'TB', 'basis': '依据', 'evidence_ids': ['VIS']}, 'block_names'),
    ({'block_names': ['T*'], 'basis': '依据', 'evidence_ids': ['VIS']}, '通配'),
    ({'block_names': ['.*'], 'basis': '依据', 'evidence_ids': ['VIS']}, '通配'),
    ({'block_names': ['*'], 'basis': '依据', 'evidence_ids': ['VIS']}, '通配'),
    ({'block_names': ['T?'], 'basis': '依据', 'evidence_ids': ['VIS']}, '通配'),
    ({'block_names': ['^TB$'], 'basis': '依据', 'evidence_ids': ['VIS']}, '通配'),
    ({'block_names': ['TB', 'TB'], 'basis': '依据', 'evidence_ids': ['VIS']}, '重复'),
    ({'block_names': ['TB2'], 'basis': '依据', 'evidence_ids': ['VIS']}, '没有实例'),
    ({'block_names': ['tb'], 'basis': '依据', 'evidence_ids': ['VIS']}, '没有实例'),
    ({'block_names': ['TB'], 'basis': '依据', 'evidence_ids': ['VIS'], 'object_ids': ['PT-CAD-FFFF/FLOOR/CAD-1']}, '不存在'),
    ({'block_names': ['TB'], 'basis': '依据', 'evidence_ids': ['VIS'], 'object_ids': []}, 'object_ids'),
    ('写成一句话', '必须是对象'),
])
def test_malformed_adjudication_is_rejected(tmp_path, adjudication, message):
    _, _, result, out = build(tmp_path, adjudication=adjudication)
    assert result.returncode == 2, result.stdout
    assert message in result.stderr
    assert not (out / '点位识别报告.json').exists()


@pytest.mark.parametrize('decision', ['container', 'unresolved'])
def test_adjudication_on_a_rule_that_neither_includes_nor_excludes_is_rejected(tmp_path, decision):
    _, _, result, _ = build(tmp_path, rule_extra={'decision': decision})
    assert result.returncode == 2 and '只能写在 include 或 exclude 规则上' in result.stderr


def test_exclude_rule_with_adjudication_excludes_dynamic_boxes_and_marks_them(tmp_path):
    # checkpoint 1: an exclusion rule may carry the same adjudication (exact names, basis, registered evidence)
    ids, top_id, result, out = build(tmp_path, rule_extra={'decision': 'exclude'})
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['excluded'] * 4
    assert all(disp[i]['dynamic_state_adjudication']['rule_id'] == 'TB' for i in ids)
    assert disp[top_id]['disposition'] == 'excluded' and 'dynamic_state_adjudication' not in disp[top_id]
    assert report['counts']['dynamic_state_adjudicated_count'] == 4 and report['point_workpackage_complete'] is True


def test_exclude_rule_without_adjudication_leaves_dynamic_boxes_unresolved(tmp_path):
    ids, top_id, result, out = build(tmp_path, adjudication=None, rule_extra={'decision': 'exclude'})
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['unresolved'] * 4
    assert disp[top_id]['disposition'] == 'excluded'
    assert report['counts']['dynamic_state_adjudicated_count'] == 0 and report['point_workpackage_complete'] is False


def test_adjudication_object_of_another_block_is_rejected(tmp_path):
    ids, top_id, _, _ = build(tmp_path / 'probe', adjudication=None)
    other = [i for i in json.loads((tmp_path / 'probe' / 'ledger.json').read_text(encoding='utf-8'))['entries'][-1]['object_ids']
             if i.count('/')][0]
    adjudication = {'block_names': ['TB'], 'basis': '依据', 'evidence_ids': ['VIS'], 'object_ids': [ids[0], other]}
    _, _, result, _ = build(tmp_path / 'run', adjudication=adjudication)
    assert result.returncode == 2 and '块名不在 block_names 里' in result.stderr
