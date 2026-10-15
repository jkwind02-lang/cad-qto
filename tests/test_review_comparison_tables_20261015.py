"""R1 生成器体量预估与表格输入（契约 docs/维护/复核页审核视图契约.md 第 11、12 节）。

表格全部是合成的；只验证搬运、改名和双向校验，不含项目工程量。
"""
import copy, csv, json, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / '_qto-shared-evidence' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qto_review_contract as c  # noqa: E402
import build_qto_review_html as builder  # noqa: E402
import review_comparison_fixture as fx  # noqa: E402


def run(script, *args):
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                       stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}, p.stderr


# ---------- generator: size estimate and problem list ----------

def test_builder_reports_size_estimate_and_comparison_counts(tmp_path):
    src = tmp_path / 'view.json'; src.write_text(json.dumps(fx.view(), ensure_ascii=False), encoding='utf-8')
    report = builder.build(src, tmp_path / 'page.html', feedback_url='')
    assert report['schema'] == 'qto-review-view/1.1'
    size = report['size_estimate']
    assert size['raw_json_bytes'] > size['gzip_bytes'] > 0 and size['html_bytes'] == (tmp_path / 'page.html').stat().st_size
    assert report['comparison_counts']['items'] == 6 and report['comparison_counts']['extra_items'] == 1
    assert report['consistent_band'] == {'consistent_band': 0.05, 'consistent_band_by_unit': {'个': 0, '台': 0}}
    assert any(e['kind'] == '第三方条目有误已修正' for e in report['comparison_exceptions'])
    assert report['quantity_changes'] == 0


def test_builder_1_0_report_has_no_comparison_keys(tmp_path):
    v = fx.view(); v['schema'] = 'qto-review-view/1.0'; del v['comparison']
    src = tmp_path / 'view.json'; src.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    report = builder.build(src, tmp_path / 'page.html', feedback_url='')
    assert report['schema'] == 'qto-review-view/1.0' and 'comparison_counts' not in report and 'size_estimate' in report


def test_builder_writes_full_problem_list_on_comparison_error(tmp_path):
    v = fx.view()
    fx.item(v, 'I1')['ours']['levels']['规则推算量'] = 31.0          # C3-1
    fx.item(v, 'I3')['third_parties'][0]['search_evidence'] = None   # C3-5
    src = tmp_path / 'view.json'; src.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
    code, out, _ = run('build_qto_review_html.py', '--input', src, '--output', tmp_path / 'page.html', '--local-only', '--report', tmp_path / 'r.json')
    assert code == 2 and out['status'] == 'INPUT_REQUIRED'
    assert {p['check'] for p in out['problems']} >= {'C3-1', 'C3-5'}
    saved = json.loads((tmp_path / 'r.json').read_text(encoding='utf-8'))
    assert saved['problems'] == out['problems'] and not (tmp_path / 'page.html').exists()


# ---------- table input ----------

def write_csv(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


ITEM_HEADER = ['条目', '来源', '楼栋', '分部', '名称', '特征', '单位', '确认', '规则推算', '方案暂估', '未决', '清单行', '序号', '清单量', '来源段量',
               '差额', '差额率', '结论', '修正', '修正说明', '缺图说明', '检索图纸', '检索目标', '检索方法', '命中数']


def tables(tmp_path, **override):
    """Synthetic professional tables equivalent to fixture view items I1-I6/E1 (no scenes, members become unlocated objects)."""
    d = tmp_path / 'tables'; d.mkdir(exist_ok=True)
    items = [
        ['I1', 'AUDIT', 'B1', 'D1', '配管', '暗配 JDG20', 'm', '', '30', '5', '', 'A1', '1', '34', '', '1', '2.9%', '', '', '', '', '', '', '', ''],
        ['I1', 'BID', 'B1', 'D1', '配管', '暗配 JDG20', 'm', '', '30', '5', '', '', '', '', '30', '5', '16.7%', '多算', '', '', '', '', '', '', ''],
        ['I2', 'AUDIT', 'B1', 'D2', '接线盒', '86 型', '个', '10', '', '', '', 'A2', '2', '11', '', '-1', '-9.1%', '', '是', '合成：规格写错', '', '', '', '', ''],
        ['I3', 'AUDIT', 'B2', 'D1', '配管', 'JDG25', 'm', '', '', '', '', 'A3', '3', '5', '', '', '', '图上无', '', '', '', 'P1;P2', 'JDG25;JDG 25', '文字检索', '0'],
        ['I4', 'AUDIT', 'B2', 'D1', '配管', 'JDG32', 'm', '0', '', '', '', 'A4', '4', '3', '', '', '', '归入他项', '', '', '', '', '', '', ''],
        ['I5', 'AUDIT', 'B2', 'D2', '桥架配管', '', 'm', '', '', '', '', 'A5', '5', '7', '', '', '', '缺图', '', '', '合成：无图', '', '', '', ''],
        ['I6', 'AUDIT', 'B1', 'D2', '配电箱', '', '台', '4', '', '', '', 'A6', '6', '', '', '', '', '清单无量', '', '', '', '', '', '', ''],
    ]
    write_csv(d / 'items.csv', ITEM_HEADER, override.get('items', items))
    write_csv(d / 'extra.csv', ['条目', '楼栋', '分部', '名称', '特征', '单位', '方案暂估'], [['E1', 'B1', 'D1', '电力电缆', 'YJV-4x25', 'm', '7']])
    write_csv(d / 'source_rows.csv', ['item_id', 'source_id', 'row_id', 'seq', 'row_name', 'row_unit', 'row_quantity'],
              [['I1', 'BID', 'T1', '1', '配管', 'm', '20'], ['I1', 'BID', 'T2', '2', '配管', 'm', '10']])
    write_csv(d / 'details.csv', ['明细', '条目', '层级', '数量', '单位', '依据', '区域', '聚合', '成员数量名', '成员表'], override.get('details', [
        ['I1#1', 'I1', '规则推算', '30', 'm', '3 回路合计', '1F', 'sum_members', '水平长度', 'circuit'],
        ['I1#2', 'I1', '方案暂估', '5', 'm', '出桥架估', '1F', '', '', ''],
        ['I2#1', 'I2', '确认', '10', '个', '逐个计数', '', '', '', ''],
        ['I6#1', 'I6', '确认', '4', '台', '系统图箱号', '', '', '', ''],
        ['E1#1', 'E1', '方案暂估', '7', 'm', '终点估算', '', '', '', '']]))
    write_csv(d / 'members.csv', ['回路', '明细', '楼栋', '水平', '起点箱'], [['WL1', 'I1#1', 'B1', '10', 'AL1'], ['WL2', 'I1#1', 'B1', '12', 'AL1'], ['WL3', 'I1#1', 'B1', '8', 'AL1']])
    write_csv(d / 'targets.csv', ['from_item', 'to_item'], [['I4', 'I1']])
    write_csv(d / 'issues.csv', ['编号', '类别', '标题', '正文', '影响', '做法', '楼栋', '原条目'],
              [['Q1', '需要定口径', '配管是否含出桥架段', '合成问题', '约 5 m', '现含', 'B1', ''], ['Q2', '已处理', '已处理事项', '仅留记录', '', '', 'B1、B2', 'E1-Q02']])
    write_csv(d / 'issue_links.csv', ['issue_id', 'item_id'], [['Q1', 'I1'], ['Q2', 'I3']])
    write_csv(d / 'issue_sources.csv', ['issue_id', '原条目', '原文'], [['Q1', 'E1-Q01', '原文']])
    write_csv(d / 'buildings.csv', ['楼栋', '名称', '合并自'], [['B1', '一号楼', ''], ['B1', '一号楼', '一号楼（配管部分）'], ['B2', '二号楼', '']])
    write_csv(d / 'audit_rows.csv', ['row_id'], [[f'A{n}'] for n in range(1, 7)])
    item_cols = {'id': '条目', 'source_id': '来源', 'dim.building': '楼栋', 'dim.division': '分部', 'name': '名称', 'features': '特征', 'unit': '单位',
                 '确认量': '确认', '规则推算量': '规则推算', '方案暂估量': '方案暂估', '未决量': '未决', 'row_id': '清单行', 'seq': '序号', 'row_quantity': '清单量',
                 'tp_quantity': '来源段量', 'difference': '差额', 'difference_rate': '差额率', 'conclusion': '结论', 'third_party_corrected': '修正',
                 'correction_note': '修正说明', 'missing_drawing_note': '缺图说明', 'search_drawings': '检索图纸', 'search_targets': '检索目标',
                 'search_method': '检索方法', 'search_hits': '命中数'}
    levels = {'level': {'确认': '确认量', '规则推算': '规则推算量', '方案暂估': '方案暂估量', '未决': '未决量'}}
    mapping = {
        'schema': 'qto-comparison-table-mapping/1.0',
        'view': {'project_id': '合成审核验证', 'review_id': 'R1 表格', 'title': '合成表格审核', 'revision': 'r1',
                 'sources': [{'id': 'LIST', 'title': '合成清单', 'sha256': fx.sha('list')}], 'scope_notes': ['合成数据']},
        'comparison': {'display_terms': {'third_party': '清单'},
                       'thresholds': {'difference_rate_alert': 0.2, 'consistent_band': 0.05, 'consistent_band_by_unit': {'个': 0, '台': 0}},
                       'sources': [{'id': 'AUDIT', 'kind': '审计清单', 'title': '合成审计', 'files': [{'title': '审计.xlsx', 'sha256': fx.sha('audit')}],
                                    'row_ids_table': {'path': 'tables/audit_rows.csv'}},
                                   {'id': 'BID', 'kind': '招标清单', 'title': '合成招标', 'files': [{'title': '招标.xlsx', 'sha256': fx.sha('bid')}], 'row_ids_from': 'items'}],
                       'dimensions': [{'id': 'building', 'title': '楼栋', 'role': 'overview', 'values_from': {'table': 'buildings', 'id': '楼栋', 'title': '名称', 'merged_from': '合并自'}},
                                      {'id': 'division', 'title': '分部', 'role': 'section', 'values': [{'id': 'D1', 'title': '配管'}, {'id': 'D2', 'title': '设备'}]}],
                       'member_tables': [{'id': 'circuit', 'title': '逐回路台账', 'columns': [{'key': 'attributes.起点箱', 'title': '起点箱'}, {'key': 'quantities.水平长度', 'title': '水平 m'}]}]},
        'tables': {
            'items': [{'path': 'tables/items.csv', 'constants': {'kind': 'third_party'}, 'columns': item_cols,
                       'value_maps': {'conclusion': {'清单无量': '第三方无量'}}, 'list_separators': {'search_drawings': ';', 'search_targets': ';'}},
                      {'path': 'tables/extra.csv', 'constants': {'kind': 'extra'},
                       'columns': {'id': '条目', 'dim.building': '楼栋', 'dim.division': '分部', 'name': '名称', 'features': '特征', 'unit': '单位', '方案暂估量': '方案暂估'}}],
            'source_rows': [{'path': 'tables/source_rows.csv'}],
            'detail_rows': [{'path': 'tables/details.csv', 'value_maps': levels,
                             'columns': {'id': '明细', 'item_id': '条目', 'level': '层级', 'quantity': '数量', 'unit': '单位', 'basis': '依据', 'area': '区域',
                                         'aggregation': '聚合', 'member_quantity_name': '成员数量名', 'member_table': '成员表'}}],
            'members': [{'path': 'tables/members.csv', 'constants': {'professional': 'qto-electrical-routes', 'category': '回路'},
                         'columns': {'object_id': '回路', 'detail_id': '明细', 'dim.building': '楼栋'},
                         'quantities': [{'name': '水平长度', 'unit': 'm', 'level': '规则推算量', 'column': '水平'}], 'attributes': {'起点箱': '起点箱'}}],
            'redirects': [{'path': 'tables/targets.csv', 'columns': {'item_id': 'from_item', 'target_item_id': 'to_item'}}],
            'issues': [{'path': 'tables/issues.csv', 'columns': {'id': '编号', 'handler': '类别', 'title': '标题', 'text': '正文', 'impact_text': '影响',
                                                               'current_practice': '做法', 'dim.building': '楼栋', 'source_refs': '原条目'},
                        'value_maps': {'handler': {'需要定口径': 'decide', '已处理': 'closed'}}, 'list_separators': {'dim.building': '、'}}],
            'issue_links': [{'path': 'tables/issue_links.csv'}],
            'issue_sources': [{'path': 'tables/issue_sources.csv', 'columns': {'ref': '原条目', 'text': '原文'}}],
            'buildings': [{'path': 'tables/buildings.csv'}]}}
    for k, v in override.get('mapping', {}).items():
        mapping[k] = v
    m = tmp_path / 'mapping.json'; m.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
    return m, mapping


def convert(tmp_path, *extra, **override):
    m, _ = tables(tmp_path, **override)
    out, rep = tmp_path / 'view.json', tmp_path / 'report.json'
    code, summary, err = run('build_comparison_view_from_tables.py', '--mapping', m, '--output', out, '--report', rep, *extra)
    return code, summary, json.loads(rep.read_text(encoding='utf-8')), out


def test_tables_convert_to_a_valid_view_with_same_results_as_fixture(tmp_path):
    code, summary, report, out = convert(tmp_path)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8'))
    n = c.normalize_view(v)
    items = {i['id']: i for i in n['comparison']['items']}
    assert [i['id'] for i in v['comparison']['items']] == ['I1', 'I2', 'I3', 'I4', 'I5', 'I6', 'E1']
    i1 = items['I1']
    assert [s['source_id'] for s in i1['third_parties']] == ['AUDIT', 'BID']
    assert [r['row_id'] for r in i1['third_parties'][1]['rows']] == ['T1', 'T2'] and i1['third_parties'][1]['quantity'] == 30.0
    assert i1['third_parties'][0]['conclusion'] == '一致' and items['I2']['third_parties'][0]['conclusion'] == '少算'
    assert items['I2']['third_parties'][0]['third_party_corrected'] is True
    assert items['I2']['third_parties'][0]['difference'] == {'value': -1.0, 'rate': -0.091}
    assert items['I3']['third_parties'][0]['search_evidence'] == {'drawings': ['P1', 'P2'], 'targets': ['JDG25', 'JDG 25'], 'method': '文字检索', 'hits': 0.0}
    assert items['I4']['third_parties'][0]['redirect_to'] == ['I1']
    assert items['I6']['third_parties'][0]['conclusion'] == '第三方无量' and items['I6']['third_parties'][0]['quantity'] is None
    assert items['E1']['kind'] == 'extra' and items['E1']['estimate_dominant'] is True
    assert i1['detail_rows'][0]['member_keys'] == ['成员|WL1', '成员|WL2', '成员|WL3']
    objs = {o['id']: o for o in v['objects']}
    assert objs['WL2']['kind'] == 'unlocated' and objs['WL2']['quantities'] == [{'name': '水平长度', 'value': 12.0, 'unit': 'm', 'level': '规则推算量'}]
    assert objs['WL2']['attributes'] == {'起点箱': 'AL1'} and objs['WL2']['dimensions'] == {'building': 'B1'}
    building = v['comparison']['dimensions'][0]
    assert building['values'] == [{'id': 'B1', 'title': '一号楼', 'merged_from': ['一号楼（配管部分）']}, {'id': 'B2', 'title': '二号楼'}]
    q = {x['id']: x for x in v['comparison']['issues']}
    assert q['Q1']['handler'] == 'decide' and q['Q1']['item_ids'] == ['I1'] and q['Q1']['source_refs'] == [{'ref': 'E1-Q01', 'text': '原文'}]
    assert q['Q2']['dimensions'] == {'building': ['B1', 'B2']} and q['Q2']['source_refs'] == ['E1-Q02']
    src = {s['id']: s for s in v['comparison']['sources']}
    assert src['AUDIT']['row_ids_origin'] == 'independent' and src['BID']['row_ids'] == ['T1', 'T2'] and src['BID']['row_ids_origin'] == 'items'
    assert any(e['kind'] == '来源行编号取自条目' and e['where'] == 'BID' for e in report['comparison_exceptions'])
    assert report['status'] == 'PASS' and report['quantity_changes'] == 0 and len(report['tables']) >= 10
    assert summary['status'] == 'PASS'


def test_tables_view_builds_a_page(tmp_path):
    code, _, _, out = convert(tmp_path)
    assert code == 0
    report = builder.build(out, tmp_path / 'page.html', feedback_url='')
    assert report['scene_count'] == 0 and report['comparison_counts']['member_objects'] == 3


def test_tables_bad_cells_are_all_reported_and_nothing_written(tmp_path):
    items = [
        ['I1', 'AUDIT', 'B1', 'D1', '配管', '暗配 JDG20', 'm', '', '三十', '5', '', 'A1', '1', '34', '', '1', '2.9%', '', '', '', '', '', '', '', ''],
        ['I1', 'BID', 'B2', 'D1', '配管', '暗配 JDG20', 'm', '', '30', '5', '', '', '', '', '', '5', '16.7%', '多算', '', '', '', '', '', '', ''],
        ['I2', 'AUDIT', 'B1', 'D2', '接线盒', '86 型', '个', '10', '', '', '', 'A2', '2', '11', '', '-1', '-9.1%', '', '也许', '', '', '', '', '', ''],
    ]
    code, summary, report, out = convert(tmp_path, items=items)
    assert code == 2 and not out.exists() and summary['problem_count'] == len(report['problems'])
    text = json.dumps(report['problems'], ensure_ascii=False)
    assert '不是数字：三十' in text                 # numbers are never guessed
    assert '条目级列与前一行不同' in text and 'dim.building' in text
    assert '须在 tp_quantity 写该段数量' in text     # multi-row segment without its own quantity
    assert '是否列只能写' in text


def test_tables_missing_column_and_unknown_detail_item(tmp_path):
    m, mapping = tables(tmp_path, details=[['X#1', 'NOPE', '确认', '1', 'm', '', '', '', '', '']])
    mapping['tables']['issues'][0]['columns']['title'] = '不存在的列'
    m.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run('build_comparison_view_from_tables.py', '--mapping', m, '--output', tmp_path / 'v.json')
    assert code == 2
    assert summary['problem_count'] >= 2


def test_tables_contract_checks_still_apply_after_conversion(tmp_path):
    details = [['I1#1', 'I1', '规则推算', '29', 'm', '3 回路合计', '1F', 'sum_members', '水平长度', 'circuit'],
               ['I1#2', 'I1', '方案暂估', '5', 'm', '', '1F', '', '', ''], ['I2#1', 'I2', '确认', '10', '个', '', '', '', '', ''],
               ['I6#1', 'I6', '确认', '4', '台', '', '', '', '', ''], ['E1#1', 'E1', '方案暂估', '7', 'm', '', '', '', '', '']]
    code, _, report, out = convert(tmp_path, details=details)
    assert code == 2 and not out.exists()
    assert {p['check'] for p in report['problems']} >= {'C3-1', 'C3-2'}


def test_tables_base_view_objects_are_referenced_not_duplicated(tmp_path):
    base = fx.view(); del base['comparison']; base['schema'] = 'qto-review-view/1.0'
    for o in base['objects']: o['catalog_key'] = '成员|' + o['id']
    bpath = tmp_path / 'base.json'; bpath.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8')
    code, _, report, out = convert(tmp_path, '--base-view', bpath)
    assert code == 0, report
    v = json.loads(out.read_text(encoding='utf-8'))
    assert len(v['objects']) == 3 and all(o['kind'] == 'line' and o['scene_id'] == 'P1' for o in v['objects'])
    assert len(v['scenes']) == 1 and {s['id'] for s in v['sources']} == {'LIST', 'DWG'}


def test_tables_refuse_existing_output_and_wrong_schema(tmp_path):
    m, mapping = tables(tmp_path)
    out = tmp_path / 'v.json'; out.write_text('{}', encoding='utf-8')
    code, summary, _ = run('build_comparison_view_from_tables.py', '--mapping', m, '--output', out)
    assert code == 2 and '已存在' in summary['error'] and out.read_text(encoding='utf-8') == '{}'
    mapping['schema'] = 'other'; m.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
    code, summary, _ = run('build_comparison_view_from_tables.py', '--mapping', m, '--output', tmp_path / 'v2.json')
    assert code == 2 and 'schema' in summary['error']
