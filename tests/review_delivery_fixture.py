"""海岚仓库 synthetic shared-delivery example; invented values, no project adapter."""
import copy
import hashlib


def scope(*included, excluded=()):
    return {'included': list(included), 'excluded': list(excluded)}


def refs(locator='合成记录/第1页'):
    return [{'source_id': 'RECORD', 'locator': locator}]


def evidence(method='measured'):
    return {'state': 'verified', 'method': method, 'source_refs': refs(),
            'input_facts': [{'id': 'F1', 'statement': '合成尺寸12 m已逐项核实', 'state': 'verified', 'source_refs': refs('合成记录/尺寸F1')}] if method == 'rule' else []}


def view(with_scene=True, decisions=False):
    def obj(key, title, value, unit, y):
        return {'id': key, 'catalog_key': key, 'professional': 'qto-pipe-networks', 'title': title,
                'category': '合成对象', 'detail': '合成验证，非工程采用量', 'source': '合成记录', 'status': '已核实',
                'kind': 'line' if with_scene else 'unlocated', 'scene_id': 'P1' if with_scene else None,
                'points': [[10, y], [90, y]] if with_scene else [],
                'quantities': [{'name': '原记录量', 'value': value, 'unit': unit, 'level': '确认量'}]}
    def row(rid, title, value, unit, state, member, scopes, method='measured'):
        return {'id': rid, 'title': title, 'quantity': value, 'unit': unit, 'state': state,
                'scope': scope(*scopes), 'coverage': {'state': 'complete', 'computed_scope': list(scopes), 'uncomputed_scope': []},
                'basis': '合成记录逐项核实', 'member_keys': member, 'evidence_member_keys': ['SOURCE'], 'evidence': evidence(method)}
    main = row('M1', '一号支路管道', 12, 'm', 'current', ['PIPE'], ['WL1', 'WL2'], 'rule')
    main['coverage'] = {'state': 'partial', 'computed_scope': ['WL1'], 'uncomputed_scope': ['WL2']}
    count = row('M2', '泵组设备', 2, '台', 'current', ['PUMP'], ['泵房'])
    reference = row('R1', '全支路方案参考', 20, 'm', 'reference', [], ['WL1', 'WL2'])
    reference['reference'] = {'relationship': 'includes_current', 'current_row_ids': ['M1'], 'prerequisites': ['WL2仍待竖向标高，示例方案长度不进入主量'], 'addition': 'do_not_add'}
    unknown = row('R2', '竖向长度待核', None, 'm', 'reference', [], ['WL2'])
    unknown['evidence'] = {'state': 'pending', 'method': 'rule', 'source_refs': [], 'input_facts': []}
    unknown['coverage'] = {'state': 'partial', 'computed_scope': [], 'uncomputed_scope': ['WL2']}
    unknown['reference'] = {'relationship': 'unknown', 'current_row_ids': [], 'prerequisites': ['补齐标高后回算'], 'addition': 'do_not_add'}
    old = row('H1', '退役方案旧量', 888, 'm', 'retired', ['OLD'], ['旧方案'])
    old['evidence_member_keys'] = ['OLD']; old['history_reason'] = '旧走向已退役，仅保留历史可读记录'
    def missing_child(circuit):
        return {'id': 'G-' + circuit, 'title': circuit + '标高缺口', 'scope': scope(circuit),
                'verified_facts': [{'statement': circuit + '的水平路径已核实', 'source_refs': refs(circuit + '/平面路径')}],
                'minimum_required': [{'id': 'NEED-' + circuit, 'description': circuit + '起终点标高及转接详图', 'provider': '设计单位',
                                      'scope': scope(circuit), 'impact': '仅影响' + circuit + '竖向长度；不改变其他回路',
                                      'recompute_action': '助手核实起终点及竖向路径，回算该回路并更新相应覆盖范围'}]}
    d = {'schema': 'qto-review-delivery/1.0', 'mode': 'decisions' if decisions else 'read_only', 'assistant_work_complete': True,
         'current_member_keys': ['PIPE', 'PUMP'], 'source_evidence_member_keys': ['SOURCE'], 'retired_member_keys': ['OLD'],
         'quantity_rows': [main, count, reference, unknown, old],
         'missing_groups': [{'id': 'G1', 'title': '支路标高缺口', 'scope': scope('WL1', 'WL2', excluded=['室外管网']),
                             'verified_facts': [], 'minimum_required': [], 'children': [missing_child('WL1'), missing_child('WL2')]}],
         'decision_targets': [{'kind': 'issue', 'id': 'D1', 'reason': '两种允许工艺中选择最终施工方案；不是补资料'}] if decisions else []}
    result = {'schema': 'qto-review-view/1.1' if decisions else 'qto-review-view/1.0', 'project_id': '海岚仓库合成测试',
              'review_id': '通用交付示例', 'title': '海岚仓库工程量与资料缺口', 'revision': '合成r1',
              'sources': [{'id': 'RECORD', 'title': '合成核查记录（无真实图纸或清单）', 'sha256': hashlib.sha256(b'hailan-fixture').hexdigest()}],
              'scenes': [{'id': 'P1', 'title': '合成示意图', 'source_id': 'RECORD', 'coordinate_unit': 'mm', 'y_axis': 'up', 'bounds': [0, 0, 100, 100]}] if with_scene else [],
              'objects': [obj('PIPE', '已证管段', 12, 'm', 10), obj('PUMP', '已证泵组', 2, '台', 30),
                          obj('SOURCE', '仅来源证据，旧记录量不采用', 999, 'm', 50), obj('OLD', '退役路径', 888, 'm', 70)],
              'groups': [], 'summary': [], 'scope_notes': ['合成测试数据；没有真实工程数量。'], 'delivery': d}
    if decisions:
        result['comparison'] = {'items': [], 'sources': [], 'dimensions': [], 'issues': [
            {'id': 'D1', 'handler': 'decide', 'nature': 'multi_method', 'title': '施工方案选择', 'text': '两种工艺均可，需业务选择。',
             'current_practice': '暂按方案甲', 'options': [{'id': 'A', 'text': '方案甲', 'impact': None}, {'id': 'B', 'text': '方案乙', 'impact': None}], 'current_option': 'A', 'item_ids': []}]}
    return result


def clone(value):
    return copy.deepcopy(value)
