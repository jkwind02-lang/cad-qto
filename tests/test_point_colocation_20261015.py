"""P1（登记 20261015-1535 重叠图元、20261015-1343 天正分解块）：同位置多图元归并与重复计数。

合成图（mm，输出 m）：
- LAMP 在 (1000,1000) 插入两次（完全重合）；(3000,1000) 与 (3017,1007) 各一次（错位约 18.4 mm）；
  (5000,1000) 与 (5100,1000) 各一次（相距 100 mm，两台不同灯）。
- BOX_A 与 BOX_B 同在 (7000,1000)，都归"配电箱"（同一位置两个块，疑似同一台箱画了两次）。
- VALVE_Body / VALVE_Cent / VALVE_Hand 同在 (9000,1000)：天正分解块的三个子块，是 1 个阀门。
- PUMP_Cent / PUMP_Hand 同在 (11000,1000)：缺 Body 主子块。
期望值按图面写出，不从被测代码反算。
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
PLACEMENTS = [('LAMP', 1000, 1000), ('LAMP', 1000, 1000), ('LAMP', 3000, 1000), ('LAMP', 3017, 1007),
              ('LAMP', 5000, 1000), ('LAMP', 5100, 1000), ('BOX_A', 7000, 1000), ('BOX_B', 7000, 1000),
              ('VALVE_Body', 9000, 1000), ('VALVE_Cent', 9000, 1000), ('VALVE_Hand', 9000, 1000),
              ('PUMP_Cent', 11000, 1000), ('PUMP_Hand', 11000, 1000)]
RULES = [('LAMP', '^LAMP$', '灯具'), ('BOX', '^BOX_[AB]$', '配电箱'), ('VALVE', '^(VALVE|PUMP)_(Body|Cent|Hand)$', '阀门'),
         ('DAMPER', '^DAMPER_(Body|CentUP|CentDN|Hand)$', '风阀')]
SQUARE = [(-50, -50), (50, -50), (50, 50), (-50, 50)]
# 天正分解风阀的子块形状（块内单位）：阀体 500×1000 框，叶片符号 350×350，执行器为一段竖线
SHAPES = {'DAMPER_Body': [(-250, -500), (250, -500), (250, 500), (-250, 500)],
          'DAMPER_CentUP': [(-175, -175), (175, -175), (175, 175), (-175, 175)],
          'DAMPER_CentDN': [(-175, -175), (175, -175), (175, 175), (-175, 175)],
          'DAMPER_Hand': [(0, 0), (1, 0), (1, 350), (0, 350)]}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=120)


def build(tmp_path, placements=PLACEMENTS, **profile_extra):
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = 4
    for name in sorted({placement[0] for placement in placements}):
        block = doc.blocks.new(name)
        block.add_lwpolyline(SHAPES.get(name, SQUARE), close=True)
    msp = doc.modelspace()
    ids = ['PT-CAD-' + msp.add_blockref(p[0], (p[1], p[2]), dxfattribs=p[3] if len(p) > 3 else {}).dxf.handle
           for p in placements]
    dxf = tmp_path / 'colocation.dxf'; doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    extracted = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    src = sha(dxf)
    entries = [{'adjudication_id': f'M-{rid}', 'subject_id': rid, 'professional_owner': 'qto-components-points',
                'decision': 'include', 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
                'recognized_at': '2026-10-15T00:00:00+00:00', 'source_id': 'SYN', 'evidence_ids': ['G'],
                'object_ids': ids, 'basis': '合成图例', 'scope_basis': '全图', 'identity_basis': '源句柄',
                'cross_check': '按图面布置核对', 'uncertainties': ['合成']} for rid, _, _ in RULES]
    ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1',
              'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
              'evidence': [{'evidence_id': 'G', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                            'kind': 'cad_property', 'locator': 'Model', 'observation': '合成'}], 'entries': entries}
    lp = tmp_path / 'ledger.json'; lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model'}, 'unit': {'factor_to_output': .001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': [{'rule_id': rid, 'adjudication_id': f'M-{rid}', 'block_pattern': pattern, 'decision': 'include',
                          'category': category, 'system': 'S', 'spec': category, 'unit': '个', 'quantity_level': '规则推算量',
                          'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}]}
                         for rid, pattern, category in RULES], **profile_extra}
    pp = tmp_path / 'profile.json'; pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    result = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', neutral / 'CAD中性取证清单.json',
                 '--profile', pp, '--adjudication-ledger', lp, '--output-dir', out)
    return ids, result, out


def outputs(out):
    read = lambda name: json.loads((out / name).read_text(encoding='utf-8'))
    return read('点位识别报告.json'), read('点位至网络端点交接.json'), read('点位数量汇总.json')


def groups_by_members(report, ids):
    return {tuple(sorted(ids.index(pid) for pid in group['physical_ids'])): group
            for group in report.get('duplicate_review', {}).get('groups', [])}


def test_colocated_instances_are_flagged_not_silently_counted(tmp_path):
    ids, result, out = build(tmp_path)
    assert result.returncode == 0, result.stderr
    report, handoff, _ = outputs(out)
    groups = groups_by_members(report, ids)
    assert groups[(0, 1)]['kind'] == 'coincident_duplicate'
    assert groups[(2, 3)]['kind'] == 'near_offset_duplicate' and abs(groups[(2, 3)]['max_distance'] - 0.018385) < 1e-5
    assert groups[(6, 7)]['kind'] == 'colocated_same_category'
    assert groups[(8, 9, 10)]['kind'] == 'colocated_same_category'   # 未声明子块合并时三个子块不能各算一台
    assert all(group['resolution'] == 'pending' for group in groups.values())
    assert report['status'] == 'REVIEW_REQUIRED' and report['point_workpackage_complete'] is False
    assert handoff['source_workpackage_complete'] is False and handoff['source_pending_duplicate_instance_count'] == 11
    # 不自动剔除：13 个插入仍全部在采用实例里，等待处置
    assert report['counts']['adopted_physical_instance_count'] == 13


def test_lamps_100mm_apart_are_two_devices(tmp_path):
    ids, result, out = build(tmp_path)
    report, _, _ = outputs(out)
    flagged = {pid for group in report.get('duplicate_review', {}).get('groups', []) for pid in group['physical_ids']}
    assert ids[4] not in flagged and ids[5] not in flagged


def test_declared_grouping_and_resolutions_close_the_count(tmp_path):
    def resolutions(ids):
        return [{'physical_ids': [ids[0], ids[1]], 'keep': [ids[0]], 'basis': '同块同点重复插入，保留一个'},
                {'physical_ids': [ids[2], ids[3]], 'keep': [ids[2]], 'basis': '错位约 18 mm 的重复副本'},
                {'physical_ids': [ids[6], ids[7]], 'keep': [ids[6], ids[7]], 'basis': '两台箱上下叠放，系统图有两台'}]
    grouping = [{'group_rule_id': 'TZ', 'block_pattern': '^(?P<stem>VALVE|PUMP)_(?P<role>Body|Cent|Hand)$',
                 'primary_role': 'Body', 'anchor_tolerance': 0.001, 'basis': '天正分解块同插入点子块'}]
    # 句柄按插入顺序确定，两次建图相同；先取 ID 再写处置
    probe_ids, _, _ = build(tmp_path / 'ids')
    ids, result, out = build(tmp_path / 'final', duplicate_resolutions=resolutions(probe_ids), instance_grouping=grouping)
    assert ids == probe_ids and result.returncode == 0, result.stderr
    report, handoff, summary = outputs(out)
    counts = report['counts']
    assert counts['merged_member_count'] == 2          # VALVE 的 Cent、Hand 并入 Body；PUMP 无 Body 不合并
    assert counts['duplicate_excluded_count'] == 2
    assert report['instance_grouping']['groups'][0]['primary_id'] == ids[8]
    assert [group['physical_ids'] for group in report['instance_grouping']['unresolved_groups']] == [[ids[11], ids[12]]]
    # PUMP 两个子块无主成员：不合并且列为未决分组，工作包仍不完整
    assert report['point_workpackage_complete'] is False and handoff['source_unresolved_grouping_count'] == 1
    adopted_ids = {row['physical_id'] for row in json.loads((out / '点位唯一实例计数输入.json').read_text(encoding='utf-8'))['reconciled_instances']}
    assert ids[1] not in adopted_ids and ids[3] not in adopted_ids and ids[9] not in adopted_ids and ids[10] not in adopted_ids
    assert {ids[0], ids[2], ids[6], ids[7], ids[8]} <= adopted_ids


def test_merge_count_with_only_valid_groups(tmp_path):
    placements = [p for p in PLACEMENTS if not p[0].startswith('PUMP')]
    probe_ids, _, _ = build(tmp_path / 'ids', placements=placements)
    resolutions = [{'physical_ids': [probe_ids[0], probe_ids[1]], 'keep': [probe_ids[0]], 'basis': '重复插入'},
                   {'physical_ids': [probe_ids[2], probe_ids[3]], 'keep': [probe_ids[2]], 'basis': '错位副本'},
                   {'physical_ids': [probe_ids[6], probe_ids[7]], 'keep': [probe_ids[6]], 'basis': '同一台箱'}]
    grouping = [{'group_rule_id': 'TZ', 'block_pattern': '^(?P<stem>VALVE)_(?P<role>Body|Cent|Hand)$',
                 'primary_role': 'Body', 'anchor_tolerance': 0.001, 'basis': '天正分解块'}]
    ids, result, out = build(tmp_path / 'final', placements=placements, duplicate_resolutions=resolutions, instance_grouping=grouping)
    assert result.returncode == 0, result.stderr
    report, handoff, summary = outputs(out)
    # 图面：灯 1+1+2=4 台，箱 1 台，阀 1 个 —— 共 6 个物理实例
    assert report['counts']['adopted_physical_instance_count'] == 6
    assert report['status'] == 'READY' and report['point_workpackage_complete'] is True
    assert handoff['source_workpackage_complete'] is True and handoff['source_physical_instance_count'] == 6


def test_offset_and_mirrored_sub_blocks_join_the_body_they_sit_in(tmp_path):
    # 宁波 D036 实测形态：叶片子块偏离阀体插入点 50～87.5 mm 且镜像插入（x 比例为负），执行器在阀体边上
    s = {'xscale': .2, 'yscale': .2}
    m = {'xscale': -.2, 'yscale': .2}
    placements = [('DAMPER_Body', 20000, 1000, s), ('DAMPER_CentUP', 20000, 1050, m), ('DAMPER_CentDN', 20000, 950, m),
                  ('DAMPER_Hand', 20000, 1100, s),
                  # 第二台转 90°、纵向比例 0.35：外形 ±175 × ±50，子块沿 x 错开 87.5 mm
                  ('DAMPER_Body', 21000, 1000, {'xscale': .2, 'yscale': .35, 'rotation': 90}),
                  ('DAMPER_CentUP', 21087.5, 1000, {'xscale': -.2, 'yscale': .2, 'rotation': 90}),
                  ('DAMPER_CentDN', 20912.5, 1000, {'xscale': -.2, 'yscale': .2, 'rotation': 90}),
                  ('DAMPER_CentUP', 25000, 1000, m)]   # 没有阀体的孤立子块
    grouping = [{'group_rule_id': 'TZ', 'block_pattern': '^(?P<stem>DAMPER)_(?P<role>Body|CentUP|CentDN|Hand)$',
                 'primary_role': 'Body', 'anchor_tolerance': 0.001, 'basis': '天正分解块：子块落在阀体外形框内'}]
    ids, result, out = build(tmp_path, placements=placements, instance_grouping=grouping)
    assert result.returncode == 0, result.stderr
    report, _, _ = outputs(out)
    members = {group['primary_id']: sorted(group['member_ids']) for group in report['instance_grouping']['groups']}
    assert members == {ids[0]: sorted(ids[0:4]), ids[4]: sorted(ids[4:7])}
    assert report['counts']['merged_member_count'] == 5
    assert [group['physical_ids'] for group in report['instance_grouping']['unresolved_groups']] == [[ids[7]]]
    # 图面：两台风阀 + 一个待查的孤立子块；子块不再各算一台
    assert report['counts']['adopted_physical_instance_count'] == 3
    assert report['point_workpackage_complete'] is False


def test_repeated_role_inside_one_body_is_not_merged(tmp_path):
    s = {'xscale': .2, 'yscale': .2}
    m = {'xscale': -.2, 'yscale': .2}
    placements = [('DAMPER_Body', 20000, 1000, s), ('DAMPER_CentUP', 20000, 1050, m), ('DAMPER_CentUP', 20000, 1060, m)]
    grouping = [{'group_rule_id': 'TZ', 'block_pattern': '^(?P<stem>DAMPER)_(?P<role>Body|CentUP|CentDN|Hand)$',
                 'primary_role': 'Body', 'anchor_tolerance': 0.001, 'basis': '天正分解块'}]
    ids, result, out = build(tmp_path, placements=placements, instance_grouping=grouping)
    assert result.returncode == 0, result.stderr
    report, _, _ = outputs(out)
    assert report['counts']['merged_member_count'] == 0
    assert [sorted(group['physical_ids']) for group in report['instance_grouping']['unresolved_groups']] == [sorted(ids)]


def test_same_block_turned_or_mirrored_in_place_is_flagged(tmp_path):
    # 宁波 D006 配电箱：同块相距 2.5 mm、一正一镜像，项目重叠清单列为重复
    placements = [('LAMP', 1000, 1000), ('LAMP', 1000, 1000, {'rotation': 90}),
                  ('LAMP', 3000, 1000), ('LAMP', 3002.5, 1000, {'xscale': -1, 'rotation': 180})]
    ids, result, out = build(tmp_path, placements=placements)
    assert result.returncode == 0, result.stderr
    report, _, _ = outputs(out)
    groups = groups_by_members(report, ids)
    assert groups[(0, 1)]['kind'] == 'colocated_same_category' and groups[(2, 3)]['kind'] == 'colocated_same_category'
    assert report['point_workpackage_complete'] is False


def test_configurable_tolerance_and_invalid_configs(tmp_path):
    ids, result, out = build(tmp_path / 'tight', duplicate_check={'near_tolerance': 0.01, 'tolerance_basis': '本图灯具最小间距 0.5 m'})
    report, _, _ = outputs(out)
    groups = groups_by_members(report, ids)
    assert (2, 3) not in groups and (0, 1) in groups
    _, result, _ = build(tmp_path / 'noanchor', instance_grouping=[{'group_rule_id': 'TZ', 'block_pattern': '^(?P<stem>VALVE)_(?P<role>Body|Cent)$',
                                                                     'primary_role': 'Body', 'basis': 'x'}])
    assert result.returncode == 2 and 'anchor_tolerance' in result.stderr
    _, result, _ = build(tmp_path / 'badref', duplicate_resolutions=[{'physical_ids': ['PT-CAD-NOPE', 'PT-CAD-NOPE2'], 'keep': ['PT-CAD-NOPE'], 'basis': 'x'}])
    assert result.returncode == 2 and '不存在' in result.stderr
