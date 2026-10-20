"""Complete source replay for native-unit and historical declared workbench input.

Historical inputs have no new unit consumption receipt. Their original explicit
unit evidence remains valid only after the actual native objects and the old
producer's declaration/ledger checks reproduce the delivered computations.
"""
import copy
import json
from pathlib import Path

from qto_cad_source_contract import verify_source_file


def verify_legacy_source_declaration(data):
    from qto_recognition_workbench import build_index, prepare_pipe
    info=data.get('workbench')
    if not isinstance(info,dict):
        raise ValueError('局部工作台缺可回源的原生产上下文')
    source=info.get('source')
    verify_source_file(source,'旧声明工作台实际交换来源')
    basis=data.get('recognition_basis') or {}
    ledger_ref=dict(path=basis.get('ledger_path'),sha256=basis.get('ledger_sha256'))
    verify_source_file(ledger_ref,'旧声明工作台实际识别台账')
    ledger_path=Path(ledger_ref['path'])
    ledger=json.loads(ledger_path.read_text(encoding='utf-8-sig'))
    context=info.get('index_context')
    if context is not None and not isinstance(context,dict):
        raise ValueError('工作台原索引上下文无效')
    authority=(context or {}).get('authoritative_source')
    if authority is not None:
        verify_source_file(authority,'工作台原索引权威来源')
        if authority.get('sha256')!=ledger.get('source_sha256'):
            raise ValueError('工作台原索引与实际识别台账权威来源不一致')
    used={eid for entry in ledger.get('entries',[]) if entry.get('status')=='active'
          for eid in entry.get('evidence_ids',[])}
    for evidence in ledger.get('evidence',[]):
        if evidence.get('sha256')!=ledger.get('source_sha256') or evidence.get('evidence_id') not in used:
            continue
        candidate=dict(path=str((ledger_path.parent/Path(evidence['path'])).resolve()),sha256=evidence['sha256'])
        verify_source_file(candidate,'旧声明权威原图证据')
        if authority is None:
            authority=source if source.get('sha256')==candidate['sha256'] else candidate
        break
    if authority is None:
        raise ValueError('旧明确单位声明缺实际原图版本证据，不能只凭工作台标签出量')
    index=build_index(source,authoritative_source=authority,layout=(context or {}).get('layout','Model'),
                      scope_bounds=(context or {}).get('bounds'))
    if index['index_sha256']!=info.get('index_sha256'):
        raise ValueError('工作台实际source/index/authority与原索引SHA不一致，不能换源或降格手填几何；历史局部index_scope/layout缺失时须用原index/operations重新apply，保留原单位证据类型')
    if index['raw_unit'] in ('Unitless','Unknown') and not isinstance(info.get('unit_declaration'),dict):
        raise ValueError('单位未设的历史工作台缺真实单位声明或几何消费凭据')
    segments=data.get('segments')
    bindings=info.get('bindings')
    if not isinstance(segments,list) or not isinstance(bindings,list) or len(bindings)!=len(segments):
        raise ValueError('旧声明工作台源对象绑定不完整')
    by_id={r['segment_id']:r for r in segments}
    if len(by_id)!=len(segments) or len({r.get('segment_id') for r in bindings})!=len(bindings):
        raise ValueError('旧声明工作台管段或绑定身份重复')
    assignments=[]
    for binding in bindings:
        segment=by_id.get(binding.get('segment_id'))
        if segment is None or binding['segment_id']!='SEG-'+str(binding.get('object_id')):
            raise ValueError('旧声明工作台管段与实际原生对象不一致')
        assignments.append(dict(adjudication_id=binding.get('adjudication_id'),object_ids=[binding.get('object_id')],
            role='pipe_centerline',system=segment.get('system'),space_id=segment.get('space_id','Model'),
            observation=binding.get('observation')))
    handoff=data.get('endpoint_handoff') or {}
    if not isinstance(info.get('production_operations'),dict) and handoff.get('unresolved_count',0):
        raise ValueError('历史未决端口缺原操作引用，须提供原操作快照才能完整重放')
    operations=dict(index_sha256=index['index_sha256'],network_id=data.get('network_id'),
        scope_object_ids=info.get('scope_object_ids'),scope_basis=info.get('scope_basis'),unit=data.get('unit'),
        unit_declaration=copy.deepcopy(info.get('unit_declaration')),parameters=copy.deepcopy(data.get('parameters')),
        parameter_basis=info.get('parameter_basis'),assignments=assignments,relations=copy.deepcopy(info.get('relations',[])),
        endpoint_review=dict(complete=handoff.get('source_workpackage_complete'),basis=handoff.get('reason')))
    if info.get('production_operations') is not None:
        if not isinstance(info['production_operations'],dict):
            raise ValueError('工作台原操作快照无效')
        operations=copy.deepcopy(info['production_operations'])
        if operations.get('index_sha256')!=info.get('index_sha256'):
            raise ValueError('工作台原操作与原索引绑定不一致')
    # Reuse the unchanged declaration checks: real text, adjudicated objects,
    # applies_to, excerpt, unit conflicts and any geometric corroboration.
    replay=prepare_pipe(index,operations,ledger_path)
    normalized=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    # Historical full-index inputs predate these replay aids. Their absence
    # does not exempt any old field or professional output from comparison.
    for key in ('index_context','production_operations'):
        if key not in info:
            replay['workbench'].pop(key,None)
    if normalized(replay)!=normalized(data):
        raise ValueError('工作台完整专业输出与原source/index/ledger/对象及操作重放不一致')
    return dict(verification_mode='complete_native_source_index_professional_replay',input_files={},
        recognition_ledger=ledger_ref,index_sha256=index['index_sha256'],
        dependency_fingerprints={r['path']:r['sha256'] for r in (source,authority,ledger_ref)})
