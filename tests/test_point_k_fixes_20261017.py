"""第五批 K1、K2、K3、K4：点位适配器的空块定义、动态块处置与裁剪、包围盒的填充与注记、处置清单体积。

K1（登记 20261016-2359）：块定义存在但为空（清单 serialized=true、direct_entity_count=0）与缺定义分开；
被规则排除的空块不算展开失败，没被规则处置的空块只报提示；真缺定义照旧失败。
K2（登记 20261017-0012、20261017-0027 第 1、2 点）：父实例被 exclude/include 时，动态子块和普通子块一样
not_applicable；匿名动态块按精确块名（含 `*U|REF…`）或只按实例编号裁决；动态块参与 bounds 裁剪。
K4（登记 20261017-0027 第 3 点）：处置清单不逐行复制整条识别记录，体积不随 object_ids 放大。
ezdxf 造不出真动态块，按 d1 的做法在中性记录上补问题代码，再重算清单里的 SHA256。
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
ARRAY = ('array_instances_not_expanded', '阵列块需逐实例展开；当前记录不能视为一个完整实物实例')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable, '-B', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          stdin=subprocess.DEVNULL, timeout=180)


def manifest_of(neutral):
    return json.loads((neutral / 'CAD中性取证清单.json').read_text(encoding='utf-8'))


def save_manifest(neutral, manifest):
    (neutral / 'CAD中性取证清单.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')


def rewrite_stream(neutral, key, edit):
    """edit(rows) -> rows; rewrites the stream and its hash in the manifest."""
    manifest = manifest_of(neutral)
    stream = Path(manifest['artifacts'][key]['path'])
    rows = [json.loads(line) for line in stream.read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = edit(rows)
    stream.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    manifest['artifacts'][key]['sha256'] = sha(stream)
    save_manifest(neutral, manifest)


def patch_codes(neutral, key, issues_by_handle):
    def edit(rows):
        hit = 0
        for row in rows:
            issues = issues_by_handle.get(row.get('entity_handle'))
            if issues:
                row['geometry_issue_codes'] = [code for code, _ in issues]
                row['geometry_issues'] = [message for _, message in issues]
                row['geometry_status'] = 'unsupported'
                hit += 1
        assert hit == len(issues_by_handle)
        return rows
    rewrite_stream(neutral, key, edit)


def extract(tmp_path, doc, partial=False):
    """partial: the extractor reports anonymous `*U` inserts as retained failures (PARTIAL, exit 3) by design."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    dxf = tmp_path / 'k.dxf'
    doc.saveas(dxf)
    neutral = tmp_path / 'neutral'
    result = run(SHARED / 'extract_cad_neutral.py', '--source-dxf', dxf, '--output-dir', neutral)
    assert result.returncode == (3 if partial else 0), result.stdout + result.stderr
    return dxf, neutral


def adapt(tmp_path, dxf, neutral, rules, bounds=None, model_objects=None, extra_args=()):
    """Project ledger (independent adjudication) unless model_objects maps rule_id -> object_ids (model ledger)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = sha(dxf)
    if model_objects is None:
        entries = [{'adjudication_id': f'A-{r["rule_id"]}', 'subject_id': r['rule_id'], 'professional_owner': 'qto-components-points',
                    'decision': 'include' if r['decision'] == 'include' else 'exclude', 'status': 'active',
                    'confirmation_source': 'legend', 'confirmed_by': '张工', 'source_id': 'LEG', 'evidence_ids': ['图例']}
                   for r in rules if r['decision'] != 'unresolved']
        ledger = {'schema_version': 'qto-project-adjudication-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1',
                  'source_sha256': src, 'entries': entries}
    else:
        entries = [{'adjudication_id': f'A-{r["rule_id"]}', 'subject_id': r['rule_id'], 'professional_owner': 'qto-components-points',
                    'decision': r['decision'], 'status': 'active', 'decision_origin': 'model', 'recognized_by': '合成测试',
                    'recognized_at': '2026-10-17T00:00:00+00:00', 'source_id': 'SYN', 'evidence_ids': ['G'],
                    'object_ids': model_objects[r['rule_id']], 'basis': '合成图例', 'scope_basis': '全图',
                    'identity_basis': '源句柄', 'cross_check': '按图面布置核对', 'uncertainties': ['合成']}
                   for r in rules if r['decision'] != 'unresolved']
        ledger = {'schema_version': 'qto-model-recognition-ledger/1.0', 'ledger_id': 'L', 'ledger_version': '1',
                  'source_sha256': src, 'adoption_scope': 'working', 'review_status': 'unreviewed',
                  'evidence': [{'evidence_id': 'G', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                                'kind': 'cad_property', 'locator': 'Model', 'observation': '合成'},
                               {'evidence_id': 'VIS', 'path': str(dxf), 'sha256': src, 'source_sha256': src,
                                'kind': 'drawing_view', 'locator': 'Model', 'observation': '可见状态逐个看过'}],
                  'entries': entries}
    lp = tmp_path / 'ledger.json'
    lp.write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    full_rules = []
    for r in rules:
        rule = {'adjudication_id': f'A-{r["rule_id"]}', **r}
        if r['decision'] == 'include':
            rule = {'category': r['rule_id'], 'system': 'E', 'spec': '块', 'unit': '个', 'quantity_level': '规则推算量',
                    'network_ports': [{'port_id_suffix': 'NA', 'network_disposition': 'not_applicable', 'reason': '无端口'}],
                    **rule}
        full_rules.append(rule)
    scope = {'layout': 'Model'}
    if bounds is not None:
        scope['bounds'] = dict(zip(('xmin', 'ymin', 'xmax_exclusive', 'ymax_exclusive'), bounds))
    profile = {'schema_version': 'qto-component-recognition-profile/1.0', 'profile_id': 'P', 'profile_version': '1',
               'expected_source_sha256': src, 'adjudication_ledger': {'ledger_id': 'L', 'ledger_version': '1', 'sha256': sha(lp)},
               'scope': scope, 'unit': {'factor_to_output': 0.001, 'output_coordinate_unit': 'm', 'source_id': 'INSUNITS=4'},
               'rules': full_rules}
    pp = tmp_path / 'profile.json'
    pp.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    result = run(POINTS / 'build_component_instances_from_neutral.py', '--cad-manifest', neutral / 'CAD中性取证清单.json',
                 '--profile', pp, '--adjudication-ledger', lp, '--output-dir', out, *extra_args)
    return result, out


def outcome(result, out):
    assert result.returncode == 0, result.stderr
    report = json.loads((out / '点位识别报告.json').read_text(encoding='utf-8'))
    disp = {row['physical_id']: row for row in map(json.loads, (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines())}
    summary = out / '点位数量汇总.json'
    ready = json.loads(summary.read_text(encoding='utf-8'))['work_quantity_ready'] if summary.is_file() else None
    return report, disp, ready


def doc_with_empty(nested=False):
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.blocks.new('TB').add_lwpolyline(SQUARE, close=True)
    doc.blocks.new('EMPTY')   # a definition with no entities, like AVE_RENDER
    msp = doc.modelspace()
    handles = {'TB': msp.add_blockref('TB', (0, 0)).dxf.handle, 'EMPTY': msp.add_blockref('EMPTY', (300, 0)).dxf.handle}
    if nested:
        cont = doc.blocks.new('CONT')
        cont.add_lwpolyline(SQUARE, close=True)
        handles['inner_empty'] = cont.add_blockref('EMPTY', (20, 0)).dxf.handle
        handles['CONT'] = msp.add_blockref('CONT', (100000, 0)).dxf.handle
    return doc, handles


# ------------------------------------------------------------------ K1


def test_empty_definition_is_inventoried_as_serialized_with_no_entities(tmp_path):
    doc, _ = doc_with_empty()
    _, neutral = extract(tmp_path, doc)
    row = next(b for b in manifest_of(neutral)['blocks'] if b['block_name'] == 'EMPTY')
    assert row['serialized'] is True and row['direct_entity_count'] == 0


def test_excluded_empty_block_is_not_an_expansion_failure(tmp_path):
    doc, h = doc_with_empty()
    dxf, neutral = extract(tmp_path, doc)
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'EX-OTHER', 'block_pattern': '^EMPTY$', 'decision': 'exclude'}])
    report, disp, ready = outcome(result, out)
    assert disp[f'PT-CAD-{h["EMPTY"]}']['disposition'] == 'excluded'
    assert report['block_expansion_failures'] == [] and report['counts']['block_expansion_failure_count'] == 0
    assert report['point_workpackage_complete'] is True and ready is True
    empty = report['empty_block_definitions']
    assert empty['instance_count'] == 1 and empty['undisposed_instance_count'] == 0
    assert empty['blocks'] == [{'block_name': 'EMPTY', 'instance_count': 1, 'dispositions': {'excluded': 1}}]


def test_empty_block_without_any_rule_is_only_a_hint(tmp_path):
    doc, h = doc_with_empty()
    dxf, neutral = extract(tmp_path, doc)
    result, out = adapt(tmp_path, dxf, neutral, [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'}])
    report, disp, ready = outcome(result, out)
    row = disp[f'PT-CAD-{h["EMPTY"]}']
    assert row['disposition'] == 'not_applicable' and '空块定义' in row['basis']
    assert report['block_expansion_failures'] == []
    assert report['empty_block_definitions']['undisposed_instance_count'] == 1
    assert any('空块定义' in item for item in report['warnings'])
    assert report['point_workpackage_complete'] is True and ready is True


def test_rule_that_includes_an_empty_block_is_kept_but_warned(tmp_path):
    # an include rule is the project's call; the adapter counts it and says the definition draws nothing
    doc, h = doc_with_empty()
    dxf, neutral = extract(tmp_path, doc)
    result, out = adapt(tmp_path, dxf, neutral, [{'rule_id': 'ANY', 'block_pattern': '^(TB|EMPTY)$', 'decision': 'include'}])
    report, disp, _ = outcome(result, out)
    assert disp[f'PT-CAD-{h["EMPTY"]}']['disposition'] == 'adopted'
    assert any('空块定义' in item and '采用' in item for item in report['warnings'])


@pytest.mark.parametrize('how', ['unserialized', 'not_inventoried'])
def test_a_really_missing_definition_still_fails(tmp_path, how):
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.blocks.new('TB').add_lwpolyline(SQUARE, close=True)
    doc.blocks.new('GONE').add_circle((0, 0), 10)
    msp = doc.modelspace()
    msp.add_blockref('TB', (0, 0))
    gone = msp.add_blockref('GONE', (300, 0)).dxf.handle
    dxf, neutral = extract(tmp_path, doc)
    rewrite_stream(neutral, 'block_definitions', lambda rows: [r for r in rows if r.get('parent_block') != 'GONE'])
    manifest = manifest_of(neutral)
    if how == 'unserialized':
        for b in manifest['blocks']:
            if b['block_name'] == 'GONE':
                b['serialized'] = False
    else:
        manifest['blocks'] = [b for b in manifest['blocks'] if b['block_name'] != 'GONE']
    save_manifest(neutral, manifest)
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'EX', 'block_pattern': '^GONE$', 'decision': 'exclude'}])
    report, disp, ready = outcome(result, out)
    assert [f['kind'] for f in report['block_expansion_failures']] == ['missing_block_definition']
    assert report['block_expansion_failures'][0]['instance_path'] == f'CAD-{gone}'
    assert report['point_workpackage_complete'] is False and ready is False


def test_nested_empty_block_does_not_make_the_parent_unbounded(tmp_path):
    # CONT (square + an empty insert) lies far outside the scope; before K1 its bounds were unknown and it stayed
    doc, h = doc_with_empty(nested=True)
    dxf, neutral = extract(tmp_path, doc)
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'EX', 'block_pattern': '^(EMPTY|CONT)$', 'decision': 'exclude'}], bounds=(-1000, -1000, 1000, 1000))
    report, disp, _ = outcome(result, out)
    assert f'PT-CAD-{h["CONT"]}' not in disp
    assert not any(pid.startswith(f'PT-CAD-{h["CONT"]}/') for pid in disp)
    assert report['point_workpackage_complete'] is True


def test_empty_block_is_scoped_by_its_insertion_point(tmp_path):
    doc, h = doc_with_empty()
    far = doc.modelspace().add_blockref('EMPTY', (50000, 0)).dxf.handle
    dxf, neutral = extract(tmp_path, doc)
    result, out = adapt(tmp_path, dxf, neutral, [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'}],
                        bounds=(-1000, -1000, 1000, 1000))
    _, disp, _ = outcome(result, out)
    assert f'PT-CAD-{far}' not in disp
    assert disp[f'PT-CAD-{h["EMPTY"]}']['scope_location_basis'] == 'insertion_anchor_empty_definition'


# ------------------------------------------------------------------ K2


def floor_doc(inner_name='TB', parent_with_geometry=True):
    doc = ezdxf.new('R2013'); doc.units = 4
    doc.blocks.new(inner_name).add_lwpolyline(SQUARE, close=True)
    floor = doc.blocks.new('FLOOR')
    if parent_with_geometry:
        floor.add_line((-100, -100), (3100, -100))
    inner = [floor.add_blockref(inner_name, (1000 * i, 0)).dxf.handle for i in range(4)]
    msp = doc.modelspace()
    top_floor = msp.add_blockref('FLOOR', (0, 0)).dxf.handle
    top_tb = msp.add_blockref(inner_name, (8000, 0)).dxf.handle
    ids = [f'PT-CAD-{top_floor}/FLOOR/CAD-{x}' for x in inner]
    return doc, inner, ids, f'PT-CAD-{top_floor}', f'PT-CAD-{top_tb}', top_tb


@pytest.mark.parametrize('parent_decision', ['exclude', 'include'])
def test_dynamic_children_of_a_disposed_parent_are_not_applicable(tmp_path, parent_decision):
    doc, inner, ids, floor_id, top_id, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'BASE', 'block_pattern': '^FLOOR$', 'decision': parent_decision}])
    report, disp, _ = outcome(result, out)
    for i in ids:
        assert disp[i]['disposition'] == 'not_applicable'
        assert floor_id in disp[i]['basis'] and 'BASE' in disp[i]['basis']
        assert disp[i]['suppressed_by'] == {'physical_id': floor_id, 'rule_id': 'BASE', 'decision': parent_decision}
    assert report['block_expansion_failures'] == []
    assert report['counts']['object_dispositions'].get('unresolved', 0) == 0
    assert report['point_workpackage_complete'] is True


@pytest.mark.parametrize('parent_decision', ['exclude', 'include'])
def test_dynamic_children_of_a_dynamic_parent_follow_the_parent(tmp_path, parent_decision):
    # registration 20261017-0120 (FW D168 40F0): the base block is dynamic itself and released by its own rule
    doc, inner, ids, floor_id, top_id, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    patch_codes(neutral, 'layout_entities', {floor_id[len('PT-CAD-'):]: [DYN]})
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
             {'rule_id': 'BASE', 'block_pattern': '^FLOOR$', 'decision': parent_decision,
              'dynamic_state_adjudication': {'block_names': ['FLOOR'], 'basis': '底图块整块处置', 'evidence_ids': ['VIS']}}]
    result, out = adapt(tmp_path, dxf, neutral, rules, model_objects={'TB': [top_id], 'BASE': [floor_id]})
    report, disp, ready = outcome(result, out)
    assert disp[floor_id]['disposition'] == ('adopted' if parent_decision == 'include' else 'excluded')
    for i in ids:
        assert disp[i]['disposition'] == 'not_applicable'
        assert disp[i]['suppressed_by'] == {'physical_id': floor_id, 'rule_id': 'BASE', 'decision': parent_decision}
    assert report['counts']['object_dispositions'].get('unresolved', 0) == 0
    assert report['point_workpackage_complete'] is True


def test_dynamic_parent_without_adjudication_stays_unresolved_alone(tmp_path):
    # legal control: the parent's own state is still unverified, so it stays open; its children are not listed twice
    doc, inner, ids, floor_id, _, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    patch_codes(neutral, 'layout_entities', {floor_id[len('PT-CAD-'):]: [DYN]})
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'BASE', 'block_pattern': '^FLOOR$', 'decision': 'exclude'}])
    report, disp, _ = outcome(result, out)
    assert disp[floor_id]['disposition'] == 'unresolved'
    assert not any(i in disp for i in ids)
    assert report['point_workpackage_complete'] is False


def test_suppressed_child_with_an_array_code_still_stays_unresolved(tmp_path):
    doc, inner, ids, _, _, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {inner[0]: [DYN, ARRAY], inner[1]: [DYN]})
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'BASE', 'block_pattern': '^FLOOR$', 'decision': 'exclude'}])
    report, disp, _ = outcome(result, out)
    assert disp[ids[0]]['disposition'] == 'unresolved'
    assert disp[ids[1]]['disposition'] == 'not_applicable'
    assert report['point_workpackage_complete'] is False


def test_ordinary_children_of_an_excluded_parent_name_the_rule_too(tmp_path):
    doc, _, ids, floor_id, _, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    result, out = adapt(tmp_path, dxf, neutral, [
        {'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
        {'rule_id': 'BASE', 'block_pattern': '^FLOOR$', 'decision': 'exclude'}])
    _, disp, _ = outcome(result, out)
    assert all(disp[i]['disposition'] == 'not_applicable' and 'BASE' in disp[i]['basis'] for i in ids)


def test_anonymous_xref_style_block_name_is_an_exact_name(tmp_path):
    doc, inner, ids, _, top_id, _ = floor_doc(inner_name='*U|REF1')
    dxf, neutral = extract(tmp_path, doc, partial=True)
    names = {b['block_name'] for b in manifest_of(neutral)['blocks']}
    assert '*U|REF1' in names
    rules = [{'rule_id': 'TB', 'block_pattern': r'^\*U\|REF1$', 'decision': 'include',
              'dynamic_state_adjudication': {'block_names': ['*U|REF1'], 'basis': '逐个看过可见状态', 'evidence_ids': ['VIS']}},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    objects = {'TB': ids + [top_id], 'FLOOR': [ids[0].split('/')[0]]}
    result, out = adapt(tmp_path, dxf, neutral, rules, model_objects=objects)
    report, disp, ready = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids + [top_id]] == ['adopted'] * 5
    assert report['counts']['dynamic_state_adjudicated_count'] == 5 and ready is True


@pytest.mark.parametrize('name', ['T*', '*', '^TB$', 'T?'])
def test_wildcard_looking_names_that_are_not_blocks_are_still_rejected(tmp_path, name):
    doc, inner, ids, _, top_id, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include',
              'dynamic_state_adjudication': {'block_names': [name], 'basis': '依据', 'evidence_ids': ['VIS']}},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    result, out = adapt(tmp_path, dxf, neutral, rules, model_objects={'TB': ids + [top_id], 'FLOOR': [ids[0].split('/')[0]]})
    assert result.returncode == 2 and '通配' in result.stderr


def test_adjudication_by_instance_ids_alone(tmp_path):
    doc, inner, ids, _, top_id, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include',
              'dynamic_state_adjudication': {'object_ids': ids[:2], 'basis': '这两个看过', 'evidence_ids': ['VIS']}},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    result, out = adapt(tmp_path, dxf, neutral, rules, model_objects={'TB': ids + [top_id], 'FLOOR': [ids[0].split('/')[0]]})
    report, disp, _ = outcome(result, out)
    assert [disp[i]['disposition'] for i in ids] == ['adopted', 'adopted', 'unresolved', 'unresolved']
    assert report['dynamic_state_adjudication'][0]['block_names'] == []


def test_instance_only_adjudication_with_an_unknown_id_is_rejected(tmp_path):
    doc, inner, ids, _, top_id, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include',
              'dynamic_state_adjudication': {'object_ids': ['PT-CAD-FFFF'], 'basis': '依据', 'evidence_ids': ['VIS']}},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    result, _ = adapt(tmp_path, dxf, neutral, rules, model_objects={'TB': ids + [top_id], 'FLOOR': [ids[0].split('/')[0]]})
    assert result.returncode == 2 and '不存在' in result.stderr


def test_top_level_dynamic_block_takes_part_in_bounds(tmp_path):
    doc, inner, ids, floor_id, top_id, top_tb = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'layout_entities', {top_tb: [DYN]})
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    result, out = adapt(tmp_path / 'out_of_page', dxf, neutral, rules, bounds=(-500, -500, 5000, 500))
    _, disp, _ = outcome(result, out)
    assert top_id not in disp   # the far dynamic block no longer enters this page
    result, out = adapt(tmp_path / 'on_page', dxf, neutral, rules, bounds=(7000, -500, 9000, 500))
    _, disp, _ = outcome(result, out)
    assert disp[top_id]['scope_geometry_bbox'] == pytest.approx([7950, -50, 8050, 50])
    assert disp[top_id]['scope_location_basis'] == 'transformed_geometry_extent_dynamic_state_unverified'
    assert disp[top_id]['disposition'] == 'unresolved'   # bounds do not release the state


def test_dynamic_block_without_a_complete_extent_is_still_kept(tmp_path):
    # legal control: a definition holding an unmeasured spline has no complete extent, so the far block stays
    doc = ezdxf.new('R2013'); doc.units = 4
    tb = doc.blocks.new('TB')
    tb.add_lwpolyline(SQUARE, close=True)
    tb.add_spline([(0, 0), (30, 40), (60, 0), (90, 40)])
    far = doc.modelspace().add_blockref('TB', (8000, 0)).dxf.handle
    dxf, neutral = extract(tmp_path, doc, partial=True)   # the spline is a retained failure
    patch_codes(neutral, 'layout_entities', {far: [DYN]})
    result, out = adapt(tmp_path, dxf, neutral, [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'}],
                        bounds=(-500, -500, 5000, 500))
    _, disp, _ = outcome(result, out)
    row = disp[f'PT-CAD-{far}']
    assert row['scope_geometry_bbox'] is None and row['scope_location_basis'] == 'insertion_anchor_fallback_requires_review'


def test_dynamic_children_no_longer_unbound_their_container(tmp_path):
    doc, inner, ids, floor_id, _, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    patch_codes(neutral, 'block_definitions', {x: [DYN] for x in inner})
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    result, out = adapt(tmp_path, dxf, neutral, rules, bounds=(-500, -500, 1500, 500))
    _, disp, _ = outcome(result, out)
    assert [i in disp for i in ids] == [True, True, False, False]
    assert disp[floor_id]['scope_geometry_bbox'] is not None


# ------------------------------------------------------------------ K4


def k4_run(tmp_path, padding, extra_args=()):
    doc, _, ids, floor_id, top_id, _ = floor_doc()
    dxf, neutral = extract(tmp_path, doc)
    pad = [f'PT-CAD-PAD{n:05d}' for n in range(padding)]
    rules = [{'rule_id': 'TB', 'block_pattern': '^TB$', 'decision': 'include'},
             {'rule_id': 'FLOOR', 'block_pattern': '^FLOOR$', 'decision': 'container'}]
    result, out = adapt(tmp_path, dxf, neutral, rules, model_objects={'TB': ids + [top_id] + pad, 'FLOOR': [floor_id]},
                        extra_args=extra_args)
    assert result.returncode == 0, result.stderr
    return out


def test_disposition_rows_reference_the_record_instead_of_copying_it(tmp_path):
    out = k4_run(tmp_path, 0)
    rows = [json.loads(x) for x in (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines()]
    ruled = [row for row in rows if row.get('rule_ids')]
    assert ruled and all('recognition_record' not in row for row in rows)
    ref = next(row for row in ruled if row['rule_ids'] == ['TB'])['recognition_record_ref']
    assert ref['adjudication_id'] == 'A-TB' and ref['subject_id'] == 'TB' and ref['object_id_count'] == 5
    assert len(ref['entry_sha256']) == 64
    report = json.loads((out / '点位识别报告.json').read_text(encoding='utf-8'))
    assert report['disposition_detail'] == 'compact'


def test_disposition_volume_does_not_grow_with_listed_object_ids(tmp_path):
    small = (k4_run(tmp_path / 'a', 0) / '点位中性实例处置清单.jsonl').stat().st_size
    big = (k4_run(tmp_path / 'b', 3000) / '点位中性实例处置清单.jsonl').stat().st_size
    assert big - small < 400   # only the count digits change, not 3000 ids per row


def test_full_detail_switch_restores_the_records(tmp_path):
    out = k4_run(tmp_path, 0, extra_args=('--disposition-detail', 'full'))
    rows = [json.loads(x) for x in (out / '点位中性实例处置清单.jsonl').read_text(encoding='utf-8').splitlines()]
    row = next(r for r in rows if r.get('rule_ids') == ['TB'])
    assert row['recognition_record']['object_ids'][:1] and 'recognition_record_ref' in row


# ---------- K3（登记 20261017-0031 点位，决策 10-17 选 B 加护栏）：填充跳过、注记按插入点、不完整范围的护栏 ----------

sys.path.insert(0, str(SHARED))
import qto_cad_geometry as geometry   # noqa: E402

LINE_ROW = {'entity_type': 'LWPOLYLINE', 'geometry': {'parts': []}, 'bbox3d': [0, 0, 0, 100, 100, 0]}
HATCH_ROW = {'entity_type': 'HATCH', 'geometry': None, 'geometry_status': 'requires_adapter'}
SOLID_ROW = {'entity_type': 'SOLID', 'geometry': None, 'geometry_status': 'unsupported',
             'geometry_issues': ['不支持'], 'geometry_issue_codes': ['entity_type_not_supported']}
TEXT_ROW = {'entity_type': 'TEXT', 'geometry': None, 'x': -500, 'y': 50, 'z': 0, 'bbox3d': [-500, 50, 0, -500, 50, 0]}
SPLINE_ROW = {'entity_type': 'SPLINE', 'geometry': None, 'geometry_status': 'unsupported',
              'geometry_issues': ['未测'], 'geometry_issue_codes': ['spline_not_measured']}


@pytest.mark.parametrize('rows,box,notes', [
    ([LINE_ROW], [0, 0, 0, 100, 100, 0], set()),
    ([LINE_ROW, HATCH_ROW, SOLID_ROW], [0, 0, 0, 100, 100, 0], {'fill_skipped'}),
    ([LINE_ROW, TEXT_ROW], [-500, 0, 0, 100, 100, 0], {'annotation_as_insertion_point'}),
    ([HATCH_ROW, TEXT_ROW], None, set()),                    # only fills and labels: still no extent
    ([LINE_ROW, HATCH_ROW, SPLINE_ROW], None, set()),          # a spline still leaves the whole block unknown
    ([LINE_ROW, {**TEXT_ROW, 'x': None}], None, set()),         # a label without a position cannot be placed
])
def test_definition_extent_rules(rows, box, notes):
    got_box, got_notes = geometry.definition_extent('B', {'B': rows}, {})
    assert got_box == box and set(got_notes) == notes
    assert geometry.definition_bounds('B', {'B': rows}, {}) == box


def test_child_notes_reach_the_parent_and_the_streaming_folder_agrees():
    child_insert = {'entity_type': 'INSERT', 'block_name': 'C', 'x': 1000, 'y': 0, 'z': 0,
                    'rotation': 0, 'scale_factors': [1, 1, 1], 'extrusion': [0, 0, 1]}
    definitions = {'P': [LINE_ROW, child_insert], 'C': [LINE_ROW, HATCH_ROW, TEXT_ROW]}
    box, notes = geometry.definition_extent('P', definitions, {})
    assert box == [0, 0, 0, 1100, 100, 0] and set(notes) == {'fill_skipped', 'annotation_as_insertion_point'}
    folded = {}
    for name, rows in definitions.items():
        folder = geometry.DefinitionExtentFolder()
        for row in rows:
            folder.add(row)
        folded[name] = folder.rows()
    assert geometry.definition_extent('P', folded, {}) == (box, notes)
    assert len(folded['C']) <= 4


@pytest.mark.parametrize('box,notes,anchor,kept', [
    ([20000, 0, 0, 20100, 100, 0], frozenset(), [5000, 50, 0], False),                  # complete extent outside: pruned
    ([20000, 0, 0, 20100, 100, 0], frozenset({'fill_skipped'}), [5000, 50, 0], True),   # incomplete, anchor inside: guard
    ([20000, 0, 0, 20100, 100, 0], frozenset({'fill_skipped'}), [30000, 50, 0], False), # incomplete, anchor outside too
    ([20000, 0, 0, 20100, 100, 0], frozenset({'fill_skipped'}), None, True),            # unknown anchor: cannot prune
    (None, frozenset(), [30000, 50, 0], True),                                          # no extent: kept as before
])
def test_extent_in_scope_guard(box, notes, anchor, kept):
    scope = {'xmin': 0, 'ymin': 0, 'xmax_exclusive': 10000, 'ymax_exclusive': 10000}
    assert geometry.extent_in_scope(box, notes, anchor, scope) is kept


def k3_doc():
    """Frame (0,0)-(10000,10000). Square symbols, 100 mm. Each block name says what it holds."""
    doc = ezdxf.new('R2013'); doc.units = 4

    def square(block, dx=0):
        block.add_lwpolyline([(x + dx, y) for x, y in SQUARE], close=True)

    def hatch(block, dx=0):
        h = block.add_hatch(); h.paths.add_polyline_path([(x + dx, y) for x, y in SQUARE], is_closed=True)

    b = doc.blocks.new('TXTREACH'); square(b); b.add_text('7AL1', dxfattribs={'insert': (-15000, 0)})
    b = doc.blocks.new('HGUARD'); square(b, 15000); hatch(b, 15000)          # drawing 15 m to the right of the base point
    b = doc.blocks.new('PLAINOUT'); square(b, 15000)                         # same, no fill: complete extent
    b = doc.blocks.new('HATCHED'); square(b); hatch(b)
    b = doc.blocks.new('FILLTEXT'); hatch(b); b.add_text('X', dxfattribs={'insert': (0, 0)})
    b = doc.blocks.new('SPL'); square(b); b.add_spline([(0, 0), (30, 40), (60, 0), (90, 40)])
    msp = doc.modelspace()
    at = {'TXTREACH': (20000, 5000),   # base and square outside, the label lands at (5000, 5000) inside
          'HGUARD': (5000, 5000),      # base inside, square at (20000, 5000) outside, fill skipped
          'PLAINOUT': (5000, 6000),    # base inside, square outside, complete: pruned as before
          'HATCHED': (3000, 3000),     # inside
          'HATCHFAR': (50000, 5000),   # HATCHED block far outside: now pruned (the registration's case)
          'FILLTEXT': (60000, 5000),   # fills and labels only: no extent, kept
          'SPL': (70000, 5000)}        # spline: no extent, kept
    handles = {key: msp.add_blockref('HATCHED' if key == 'HATCHFAR' else key, xy).dxf.handle for key, xy in at.items()}
    return doc, handles


def test_k3_scope_and_basis_of_incomplete_extents(tmp_path):
    doc, h = k3_doc()
    dxf, neutral = extract(tmp_path, doc, partial=True)   # the spline is a retained failure
    rules = [{'rule_id': 'ALL', 'block_pattern': '^(TXTREACH|HGUARD|PLAINOUT|HATCHED|FILLTEXT|SPL)$', 'decision': 'include'}]
    result, out = adapt(tmp_path, dxf, neutral, rules, bounds=(0, 0, 10000, 10000))
    report, disp, _ = outcome(result, out)
    pid = {key: f'PT-CAD-{value}' for key, value in h.items()}
    reach = disp[pid['TXTREACH']]
    assert reach['scope_location_basis'] == 'transformed_geometry_extent_incomplete'
    assert reach['scope_extent_notes'] == ['注记按插入点计'] and reach['scope_geometry_bbox'] == pytest.approx([5000, 4950, 20050, 5050])
    guard = disp[pid['HGUARD']]
    assert guard['scope_location_basis'] == 'insertion_anchor_incomplete_extent_guard' and guard['scope_extent_notes'] == ['包围盒未含填充']
    assert pid['PLAINOUT'] not in disp and pid['HATCHFAR'] not in disp
    inside = disp[pid['HATCHED']]
    assert inside['scope_location_basis'] == 'transformed_geometry_extent_incomplete'
    assert inside['scope_geometry_bbox'] == pytest.approx([2950, 2950, 3050, 3050])
    for key in ('FILLTEXT', 'SPL'):
        assert disp[pid[key]]['scope_location_basis'] == 'insertion_anchor_fallback_requires_review'
        assert disp[pid[key]]['scope_geometry_bbox'] is None and 'scope_extent_notes' not in disp[pid[key]]
    counts = report['scope_extent_incomplete']['instances_by_note']
    assert counts == {'包围盒未含填充': 2, '注记按插入点计': 1, '插入点在包内、按护栏保留': 1}


def test_k3_coverage_ledger_places_sheets_by_the_same_extent(tmp_path):
    import csv
    doc, h = k3_doc()
    dxf, neutral = extract(tmp_path, doc, partial=True)
    with open(tmp_path / 'sheets.csv', 'w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f).writerows([['sheet', 'kind', 'x1', 'y1', 'x2', 'y2'], ['P1', '平面', 0, 0, 10000, 10000],
                                 ['R', '平面', 10000, 0, 100000, 10000]])
    with open(tmp_path / 'count.csv', 'w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f).writerows([['handle', 'cat'], [h['HATCHED'], '设备']])
    data = {'schema_version': 'qto-point-coverage-input/1.0', 'scope_id': 'K3',
            'drawings': [{'drawing': 'D1', 'neutral_dir': str(neutral)}],
            'count_tables': [{'path': 'count.csv', 'constants': {'drawing': 'D1'}, 'columns': {'handle': 'handle', 'category': 'cat'}}],
            'sheets': [{'path': str(tmp_path / 'sheets.csv'), 'constants': {'drawing': 'D1'},
                        'columns': {'sheet_id': 'sheet', 'sheet_kind': 'kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2'}}]}
    (tmp_path / 'input.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    done = run(POINTS / 'build_point_coverage_ledger.py', '--input', tmp_path / 'input.json', '--output-dir', tmp_path / 'out')
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((tmp_path / 'out/点位覆盖台账.json').read_text(encoding='utf-8'))
    # PLAINOUT complete; TXTREACH, HGUARD, HATCHED, HATCHFAR incomplete; FILLTEXT and SPL have no extent
    assert report['placement_basis_counts'] == {'图形范围中心': 1, '图形范围中心（范围不完整：未含填充或注记按插入点计）': 4,
                                                '插入点（定不出图形范围）': 2}
    mismatch = {w['id']: (w['insert_sheet'], w['geometry_sheet']) for w in report['warnings']['insert_geometry_sheet_mismatch']}
    assert mismatch == {f"PT-CAD-{h['HGUARD'].upper()}": ('P1', 'R'), f"PT-CAD-{h['PLAINOUT'].upper()}": ('P1', 'R')}
