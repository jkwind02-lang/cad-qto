"""Synthetic qto-review-view/1.1 comparison fixture (R1). No project data, invented numbers only."""
import copy
import hashlib


def sha(label):
    return hashlib.sha256(label.encode('utf-8')).hexdigest()


def circuit(n, length, building='B1', scene='P1'):
    key = f'电气回路|E1|WL{n}'
    obj = {'id': f'WL{n}', 'catalog_key': key, 'professional': 'qto-electrical-routes', 'kind': 'line',
           'scene_id': scene, 'points': [[n * 100, 0], [n * 100, 1000]], 'title': f'回路 WL{n}', 'category': '回路',
           'detail': '合成回路', 'source': '合成图 P1', 'status': '规则推算',
           'quantities': [{'name': '水平长度', 'value': length, 'unit': 'm', 'level': '规则推算量'}],
           'attributes': {'起点箱': 'AL1', '回路号': f'WL{n}'}, 'dimensions': {'building': building}}
    if scene is None:
        obj.update(kind='unlocated', scene_id=None, points=[])
    return obj


def segment(source, rows, unit, quantity, total, conclusion=None, **extra):
    diff = None
    if quantity not in (None, 0) and total is not None and unit == extra.pop('ours_unit', unit):
        value = round(total - quantity, 6)
        diff = {'value': value, 'rate': round(value / quantity, 4)}
    seg = {'source_id': source, 'rows': rows, 'unit': unit, 'quantity': quantity, 'difference': diff}
    if conclusion is not None:
        seg['conclusion'] = conclusion
    seg.update(extra)
    return seg


def row(row_id, quantity, unit='m', name='配管', features='暗配 JDG20', seq='1', code='030411001001'):
    return {'row_id': row_id, 'seq': seq, 'code': code, 'name': name, 'features': features, 'unit': unit, 'quantity': quantity}


def view(with_scene=True):
    """A valid 1.1 view: two sources (audit + bid), every conclusion kind, leaf/sum/declared rows."""
    scene = None if not with_scene else 'P1'
    objects = [circuit(1, 10.0, scene=scene), circuit(2, 12.0, scene=scene), circuit(3, 8.0, scene=scene)]
    levels = lambda **kw: {k: kw.get(k) for k in ('确认量', '规则推算量', '方案暂估量', '未决量')}
    items = [
        {'id': 'I1', 'kind': 'third_party', 'dimensions': {'building': 'B1', 'division': 'D1', 'package': 'E1'},
         'ours': {'unit': 'm', 'quantity_name': '配管长度', 'levels': levels(规则推算量=30.0, 方案暂估量=5.0), 'note': '合成说明'},
         'third_parties': [segment('AUDIT', [row('A1', 34.0)], 'm', 34.0, 35.0),
                           segment('BID', [row('T1', 20.0), row('T2', 10.0)], 'm', 30.0, 35.0, conclusion='多算')],
         'detail_rows': [
             {'id': 'I1#1', 'level': '规则推算量', 'quantity': 30.0, 'unit': 'm', 'basis': '3 回路合计', 'area': '1F',
              'aggregation': 'sum_members', 'member_quantity_name': '水平长度', 'member_table': 'circuit',
              'member_keys': [o['catalog_key'] for o in objects]},
             {'id': 'I1#2', 'level': '方案暂估量', 'quantity': 5.0, 'unit': 'm', 'basis': '出桥架保护管估', 'area': '1F'}]},
        {'id': 'I2', 'kind': 'third_party', 'dimensions': {'building': 'B1', 'division': 'D2'},
         'ours': {'unit': '个', 'levels': levels(确认量=10.0)},
         'third_parties': [segment('AUDIT', [row('A2', 11.0, unit='个', name='接线盒', features='86 型', seq='2')], '个', 11.0, 10.0,
                                   third_party_corrected=True, correction_note='合成：清单特征写错规格，已按图纸规格计')],
         'detail_rows': [{'id': 'I2#1', 'level': '确认量', 'quantity': 10.0, 'unit': '个', 'basis': '逐个计数'}]},
        {'id': 'I3', 'kind': 'third_party', 'dimensions': {'building': 'B2', 'division': 'D1'},
         'ours': {'unit': 'm', 'levels': levels()},
         'third_parties': [segment('AUDIT', [row('A3', 5.0, seq='3')], 'm', 5.0, None, conclusion='图上无',
                                   search_evidence={'drawings': ['P1', 'P2'], 'targets': ['JDG25', 'JDG 25'], 'method': '中性取证包文字检索', 'hits': 0})],
         'detail_rows': []},
        {'id': 'I4', 'kind': 'third_party', 'dimensions': {'building': 'B2', 'division': 'D1'},
         'ours': {'unit': 'm', 'levels': levels(确认量=0)},
         'third_parties': [segment('AUDIT', [row('A4', 3.0, seq='4')], 'm', 3.0, None, conclusion='归入他项', redirect_to=['I1'])],
         'detail_rows': []},
        {'id': 'I5', 'kind': 'third_party', 'dimensions': {'building': 'B2', 'division': 'D2'},
         'ours': {'unit': 'm', 'levels': levels()},
         'third_parties': [segment('AUDIT', [row('A5', 7.0, seq='5')], 'm', 7.0, None, conclusion='缺图', missing_drawing_note='合成：桥架配管部分无图')],
         'detail_rows': []},
        {'id': 'I6', 'kind': 'third_party', 'dimensions': {'building': 'B1', 'division': 'D2'},
         'ours': {'unit': '台', 'levels': levels(确认量=4.0)},
         'third_parties': [segment('AUDIT', [row('A6', None, unit='台', name='配电箱', seq='6')], '台', None, 4.0, conclusion='第三方无量')],
         'detail_rows': [{'id': 'I6#1', 'level': '确认量', 'quantity': 4.0, 'unit': '台', 'basis': '系统图箱号'}]},
        {'id': 'E1', 'kind': 'extra', 'name': '电力电缆', 'features': 'YJV-4x25（清单外）', 'dimensions': {'building': 'B1', 'division': 'D1'},
         'ours': {'unit': 'm', 'levels': levels(方案暂估量=7.0)}, 'third_parties': [],
         'detail_rows': [{'id': 'E1#1', 'level': '方案暂估量', 'quantity': 7.0, 'unit': 'm', 'basis': '终点位置估算'}]},
    ]
    return {
        'schema': 'qto-review-view/1.1', 'project_id': '合成审核验证', 'review_id': 'R1 合成', 'title': '合成审核与复核',
        'revision': 'r1', 'sources': [{'id': 'DWG', 'title': '合成图纸', 'sha256': sha('dwg')}, {'id': 'LIST', 'title': '合成清单', 'sha256': sha('list')}],
        'scenes': [{'id': 'P1', 'title': '合成平面', 'source_id': 'DWG', 'coordinate_unit': 'mm', 'y_axis': 'up', 'bounds': [0, 0, 1000, 1000], 'background': []}] if with_scene else [],
        'objects': objects, 'groups': [], 'receipts': {}, 'summary': [], 'scope_notes': ['合成数据，不代表任何工程量'],
        'comparison': {
            'display_terms': {'third_party': '清单'},
            'handler_labels': {'confirm': '需设计或业主确认'},
            'thresholds': {'difference_rate_alert': 0.2, 'consistent_band': {'计量': 0.05, '计数': 0}, 'count_units': ['个', '台']},
            'dimensions': [
                {'id': 'building', 'title': '楼栋', 'role': 'overview', 'values': [{'id': 'B1', 'title': '一号楼', 'merged_from': ['一号楼（配管部分）']}, {'id': 'B2', 'title': '二号楼'}]},
                {'id': 'division', 'title': '分部', 'role': 'section', 'values': [{'id': 'D1', 'title': '配管'}, {'id': 'D2', 'title': '设备'}]},
                {'id': 'package', 'title': '分包', 'role': 'filter', 'values': [{'id': 'E1', 'title': 'E1'}]}],
            'sources': [
                {'id': 'AUDIT', 'kind': '审计清单', 'title': '合成审计清单', 'files': [{'title': '审计.xlsx', 'sha256': sha('audit')}], 'row_ids': ['A1', 'A2', 'A3', 'A4', 'A5', 'A6']},
                {'id': 'BID', 'kind': '招标清单', 'title': '合成招标清单', 'files': [{'title': '招标.xlsx', 'sha256': sha('bid')}], 'row_ids': ['T1', 'T2']}],
            'items': items,
            'issues': [
                {'id': 'Q1', 'handler': 'decide', 'title': '配管是否含出桥架段', 'text': '合成问题正文', 'impact_text': '约 5 m',
                 'current_practice': '现含', 'item_ids': ['I1'], 'dimensions': {'building': 'B1'}, 'source_refs': [{'ref': 'E1-Q01', 'text': '原文'}]},
                {'id': 'Q2', 'handler': 'closed', 'title': '已处理事项', 'text': '仅留记录', 'item_ids': ['I3'], 'dimensions': {'building': ['B1', 'B2']}, 'source_refs': ['E1-Q02']}],
            'member_tables': [{'id': 'circuit', 'title': '逐回路台账', 'columns': [{'key': 'attributes.起点箱', 'title': '起点箱'}, {'key': 'attributes.回路号', 'title': '回路号'}, {'key': 'quantities.水平长度', 'title': '水平 m'}]}],
        },
    }


def item(v, iid):
    return next(i for i in v['comparison']['items'] if i['id'] == iid)


def clone(v):
    return copy.deepcopy(v)
