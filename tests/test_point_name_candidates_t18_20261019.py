"""T18: anonymous original DXF -> neutral -> adapter -> coverage -> existing count.

Expected physical objects follow the constructed drawing, not output counts. No real project data.
Run this file directly with a directory to retain a reproducible author evidence chain.
"""
import csv
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


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def run(case, name, *args):
    command = [sys.executable, '-B', *map(str, args)]
    done = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=180, stdin=subprocess.DEVNULL)
    save(case / (name + '_命令与退出码.json'), {'command': command, 'exit_code': done.returncode,
                                               'stdout': done.stdout, 'stderr': done.stderr})
    return done


def make_case(case, *, groups=True, orphan=False, default_include=False, duplicate_member=False):
    case.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.layers.add('NOTE')
    block = doc.blocks.new('ordinary_terminal')
    block.add_circle((0, 0), 100)
    block.add_attdef('A', (0, 0), text='默认名称', height=50)
    block.add_attdef('名称', (0, 50), text='默认名称', height=50)
    msp = doc.modelspace()
    real, ids = [], {}
    def pid(entity):
        return 'PT-CAD-' + entity.dxf.handle
    for n in range(1, 4):
        entity = msp.add_blockref('ordinary_terminal', (n * 2000, 1000))
        entity.add_auto_attribs({'A': '末端设备', '名称': '末端设备'})
        real.append(pid(entity))
        ids[f'number{n}'] = pid(msp.add_text(f'FAN-{n:02d}', dxfattribs={'insert': (n * 2000 + 100, 1000), 'layer': 'NOTE'}))
    default = msp.add_blockref('ordinary_terminal', (8000, 1000))
    default.add_auto_attribs({'A': '默认名称', '名称': '默认名称'})
    ids['default'] = pid(default)
    ids['name'] = pid(msp.add_attdef('N', (10000, 1000), text='合成感测端', height=100))
    ids['marker'] = pid(msp.add_attdef('Q', (10050, 1000), text='1', height=50))
    ids['point'] = pid(msp.add_point((10000, 1000)))
    ids['controlled'] = pid(msp.add_attdef('N', (12000, 1000), text='被控对象名称', height=100))
    ids['title'] = pid(msp.add_attdef('N', (15000, 1000), text='安装示意与图例', height=100))
    ids['spare'] = pid(msp.add_attdef('N', (18000, 1000), text='备用设备文字', height=100))
    ids['range'] = pid(msp.add_mtext(r'\H100;FAN-01~03', dxfattribs={'insert': (4000, 4000), 'layer': 'NOTE'}))
    ids['spare_number'] = pid(msp.add_text('FAN-90', dxfattribs={'insert': (18000, 1500), 'layer': 'NOTE'}))
    if orphan:
        ids['orphan'] = pid(msp.add_text('FAN-04', dxfattribs={'insert': (20000, 1000), 'layer': 'NOTE'}))
    dxf = case / '匿名名称与编号.dxf'; doc.saveas(dxf)
    done = run(case, '01_中性取证', SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', case / 'neutral')
    assert done.returncode in (0, 3), done.stdout + done.stderr
    source = sha(dxf)
    decisions = [('FAN', 'include', real), ('EXPLODED', 'include', [ids['name']]),
                 ('TEXT-NOISE', 'exclude', [ids[k] for k in ('controlled', 'title', 'spare')]),
                 ('DEFAULT', 'include' if default_include else 'exclude', [ids['default']])]
    entries = []
    for rid, decision, objects in decisions:
        entries.append({'adjudication_id': rid, 'subject_id': rid, 'professional_owner': 'qto-components-points',
                        'decision': decision, 'status': 'active', 'decision_origin': 'model',
                        'recognized_by': 'Codex合成图作者', 'recognized_at': '2026-10-19T00:00:00+08:00',
                        'source_id': 'SYN', 'evidence_ids': ['E'], 'object_ids': objects,
                        'basis': '按匿名原图实际布置与组成判断，非人工核定', 'scope_basis': 'Model合成平面',
                        'identity_basis': '一个实际符号一个原始句柄ID', 'cross_check': '属性默认值、标题、被控对象与备用分列',
                        'uncertainties': ['仅匿名DXF回归，未交叉宿主复测']})
    ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'T18-L', 'ledger_version': '1',
              'source_sha256': source, 'adoption_scope': 'working', 'review_status': 'unreviewed', 'entries': entries,
              'evidence': [{'evidence_id': 'E', 'path': str(dxf), 'sha256': source, 'source_sha256': source,
                            'kind': 'cad_property', 'locator': 'Model原始句柄及设备组成', 'observation': '三圆形属性设备及一个名称/1/POINT组成；其余为图例、受控、备用'}]}
    lp = save(case / '工作识别.json', ledger)
    name_rule = {'rule_id': 'FAN', 'adjudication_id': 'FAN', 'decision': 'include',
                 'category': '合成风机', 'system': '合成系统', 'spec': '合成规格', 'unit': '台',
                 'name_attribute_condition': {'tags': ['A', '名称'], 'pattern': '^末端设备$', 'basis': '实例属性不同于块定义默认值，原图符号与图例对应'},
                 'text_condition': {'pattern': r'^FAN-\d{2}$', 'radius': .3, 'as_instance_key': True, 'basis': '合成图独立旁注逐台编号'}}
    rules = [name_rule,
             {'rule_id': 'EXPLODED', 'adjudication_id': 'EXPLODED', 'decision': 'include', 'entity_types': ['ATTDEF'],
              'name_text_pattern': '^合成感测端$', 'category': '合成感测端', 'system': '合成系统', 'spec': '合成规格', 'unit': '个'},
             {'rule_id': 'TEXT-NOISE', 'adjudication_id': 'TEXT-NOISE', 'decision': 'exclude', 'entity_types': ['ATTDEF']},
             {'rule_id': 'DEFAULT', 'adjudication_id': 'DEFAULT', 'decision': 'include' if default_include else 'exclude',
              **({'category': '默认值假设备', 'system': '合成系统', 'spec': '合成规格', 'unit': '台',
                  'name_attribute_condition': {'tags': ['A'], 'pattern': '^默认名称$', 'basis': '负例：尝试按默认名计数'}} if default_include else {})}]
    group = {'name_object_id': ids['name'], 'marker_object_id': ids['marker'], 'point_object_ids': [ids['point']],
             'basis': '原图实际位置与符号组成逐个绑定，标记有偏移，不按固定距离猜测', 'evidence_ids': ['E']}
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'T18-P', 'profile_version': '1',
               'expected_source_sha256': source, 'adjudication_ledger': {'ledger_id': 'T18-L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': {'layout': 'Model', 'source_id': 'SYN', 'drawing_id': 'SYN-PLAN', 'floor': '1F', 'scope_id': '匿名设备点位', 'version': '1'},
               'unit': {'factor_to_output': .001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': rules, 'exploded_device_groups': ([group, group] if duplicate_member else [group]) if groups else [],
               'number_audit': {'rules': [
                   {'rule_id': 'RANGE', 'pattern': r'(?P<prefix>FAN-)(?P<start>\d{2})~(?P<end>\d{2})',
                    'range_endpoints': 'inclusive', 'max_expansion': 20, 'basis': '本合成编号范围从起号到终号均包含', 'evidence_ids': ['E']},
                   {'rule_id': 'SINGLE', 'pattern': r'(?P<number>FAN-\d{2})', 'basis': '本合成单台设备编号语法', 'evidence_ids': ['E']}],
                    'links': [{'text_object_id': ids['spare_number'], 'decision': 'exclude', 'physical_ids': [],
                               'basis': '原图明确备用文字，不构成已安装实例', 'evidence_ids': ['E']}]}}
    pp = save(case / '识别配置.json', profile)
    done = run(case, '02_点位适配', POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', case / 'neutral/CAD中性取证清单.json',
               '--profile', pp, '--adjudication-ledger', lp, '--output-dir', case / 'adapter')
    if duplicate_member:
        return {'done': done, 'ids': ids}
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((case / 'adapter/点位识别报告.json').read_text(encoding='utf-8'))
    dispositions = {r['physical_id']: r for r in map(json.loads, (case / 'adapter/点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines())}
    count = json.loads((case / 'adapter/点位唯一实例计数输入.json').read_text(encoding='utf-8'))
    numbers = json.loads((case / 'adapter/点位编号反查.json').read_text(encoding='utf-8'))
    with (case / '图页.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f); writer.writerow(['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'])
        writer.writerow(['P1', '平面', -1000, -1000, 25000, 10000])
    coverage_input = {'schema_version': 'qto-point-coverage-input/1.0', 'scope_id': '匿名设备点位',
                      'drawings': [{'drawing': 'SYN', 'reports': [str(case / 'adapter/点位识别报告.json')]}],
                      'sheets': [{'path': str(case / '图页.csv'), 'constants': {'drawing': 'SYN'},
                                  'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}]}
    cp = save(case / '覆盖输入.json', coverage_input)
    done = run(case, '03_覆盖台账', POINTS / 'build_point_coverage_ledger.py', '--input', cp, '--output-dir', case / 'coverage')
    assert done.returncode == 0, done.stdout + done.stderr
    coverage = json.loads((case / 'coverage/点位覆盖台账.json').read_text(encoding='utf-8'))
    done = run(case, '04_既有计数器', POINTS / 'count_reconciled_instances.py', case / 'adapter/点位唯一实例计数输入.json')
    assert done.returncode == 0, done.stdout + done.stderr
    summary = json.loads(done.stdout)
    save(case / '数量复算.json', summary)
    return dict(report=report, dispositions=dispositions, count=count, numbers=numbers, coverage=coverage, summary=summary,
                ids=ids, real=real, case=case, dxf=dxf)


def test_original_dxf_names_reach_real_coverage_and_existing_count(tmp_path):
    data = make_case(tmp_path)
    adopted = {r['physical_id']: r for r in data['count']['reconciled_instances']}
    assert set(adopted) == {*data['real'], data['ids']['name']}
    assert len(adopted) == 4  # two attributes on each circle do not double the three actual devices
    assert data['summary']['work_quantity_ready'] is True
    assert data['summary']['business_adoption_allowed'] is False
    assert data['coverage']['coverage_complete'] is True
    assert data['dispositions'][data['ids']['default']]['disposition'] == 'excluded'
    assert all(adopted[pid]['instance_path'] == pid[3:] and adopted[pid]['floor'] == '1F' for pid in adopted)
    row = next(r for r in data['numbers']['rows'] if r['object_id'] == data['ids']['range'])
    assert row['numbers'] == ['FAN-01', 'FAN-02', 'FAN-03']
    assert row['raw_text'] == r'\H100;FAN-01~03' and row['text'] == 'FAN-01~03'
    assert len(row['number_matches']) == 3 and row['is_physical_instance'] is False
    assert data['numbers']['physical_quantity'] is None
    assert data['coverage']['number_audit_rows']


def test_name_attdef_without_explicit_composition_is_pending_not_a_device(tmp_path):
    data = make_case(tmp_path, groups=False)
    assert data['dispositions'][data['ids']['name']]['disposition'] == 'unresolved'
    assert data['summary']['work_quantity_ready'] is False
    assert data['coverage']['coverage_complete'] is False
    assert data['report']['counts']['scoped_name_candidate_count'] == 4


def test_default_attribute_name_does_not_classify_a_real_block(tmp_path):
    data = make_case(tmp_path, default_include=True)
    row = data['dispositions'][data['ids']['default']]
    assert row['disposition'] == 'unresolved' and '缺省属性' in row['basis']
    assert all(r['default_status'] == 'equals_definition_default' for r in row['name_attribute_evidence'])
    assert data['summary']['work_quantity_ready'] is False


def test_orphan_number_reaches_coverage_and_blocks_full_workpackage(tmp_path):
    data = make_case(tmp_path, orphan=True)
    assert len(data['count']['reconciled_instances']) == 4
    assert data['report']['counts']['unresolved_number_label_count'] == 1
    assert data['summary']['work_quantity_ready'] is False
    assert data['coverage']['coverage_complete'] is False
    clue = next(r for r in data['coverage']['number_audit_rows'] if r['object_id'] == data['ids']['orphan'])
    assert clue['status'] == 'unresolved' and clue['candidate_quantity'] is None


def test_shared_composition_members_are_rejected(tmp_path):
    data = make_case(tmp_path, duplicate_member=True)
    assert data['done'].returncode == 2 and '共用组成成员' in data['done'].stderr


def audit_helper(text='FAN-01~03', endpoints='inclusive', flat=False, bindings=False):
    import importlib.util
    spec = importlib.util.spec_from_file_location('t18_name_evidence', POINTS / 'point_name_candidates.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    rule = {'rule_id': 'R', 'pattern': r'(?P<prefix>FAN-)(?P<start>\d{2})~(?P<end>\d{2})',
            'basis': '本图编号语法', 'evidence_ids': ['E'], 'max_expansion': 20}
    if endpoints:
        rule['range_endpoints'] = endpoints
    links = []
    if flat or bindings:
        link = {'text_object_id': 'PT-CAD-X', 'decision': 'link', 'basis': '逐原图明确关联', 'evidence_ids': ['E']}
        if flat:
            link['physical_ids'] = ['PT-CAD-A', 'PT-CAD-B', 'PT-CAD-C']
        if bindings:
            link['number_bindings'] = [{'number': f'FAN-{n:02d}', 'physical_id': 'PT-CAD-' + chr(64 + n)} for n in range(1, 4)]
        links.append(link)
    names = module.NameCandidates({'number_audit': {'rules': [rule], 'links': links}}, {'E'}, 'a' * 64, 1, 'm')
    names.add({'entity_type': 'TEXT', 'record_id': 'CAD-X', 'text': text}, 'CAD-X', '', [1, 2, 0], 'NOTE')
    return names.number_audit([{'physical_id': 'PT-CAD-' + c} for c in 'ABC'])


def test_range_needs_explicit_endpoints_and_itemized_physical_binding():
    with pytest.raises(ValueError, match='range_endpoints'):
        audit_helper(endpoints=None)
    with pytest.raises(ValueError, match='number_bindings'):
        audit_helper(flat=True)
    data = audit_helper(bindings=True)
    assert data['rows'][0]['status'] == 'linked'
    assert [r['physical_ids'] for r in data['rows'][0]['number_matches']] == [['PT-CAD-A'], ['PT-CAD-B'], ['PT-CAD-C']]
    assert data['physical_quantity'] is None


def test_conflicting_actual_name_tags_cannot_be_hidden_by_a_narrow_pattern():
    import importlib.util
    spec = importlib.util.spec_from_file_location('t18_names_conflict', POINTS / 'point_name_candidates.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    condition = module.normalize_name_condition({'tags': ['A', '名称'], 'pattern': '^风机$', 'basis': '本图名称图例'})
    evidence = [{'tag': 'A', 'value': '风机', 'default_status': 'instance_value'},
                {'tag': '名称', 'value': '感测端', 'default_status': 'instance_value'}]
    match, reason = module.match_name_condition(condition, evidence)
    assert match is False and '冲突' in reason


@pytest.mark.parametrize('value', ['FAN-03~01', 'FAN-01~99'])
def test_reversed_or_overlong_ranges_remain_pending(value):
    # The default upper expansion limit can be tightened by the current drawing profile.
    data = audit_helper(text=value)
    assert data['rows'][0]['numbers'] == []
    assert data['rows'][0]['status'] == 'unresolved'


if __name__ == '__main__':
    target = Path(sys.argv[1]).resolve()
    make_case(target / '合法对照')
    make_case(target / '无组成反例', groups=False)
    make_case(target / '默认属性反例', default_include=True)
    make_case(target / '孤立编号反例', orphan=True)
    make_case(target / '组成复用反例', duplicate_member=True)
    print(json.dumps({'retained_evidence': str(target)}, ensure_ascii=False))
