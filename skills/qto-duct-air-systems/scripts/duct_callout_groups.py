"""Multiple source callouts in one recognition rule, without guessed ordering."""
from __future__ import annotations
import copy


def bind_groups(rule, evidence, rows, unit, bind_tag, section_values, scope_fn, match, source_key):
    groups = rule.get('annotation_bindings')
    if not isinstance(groups, list) or not groups:
        raise ValueError('多规格引注须为非空源引注组')
    if rule.get('annotation_binding') or rule.get('section') is not None:
        raise ValueError('多规格引注组不能同时声明整组默认截面或单引注')
    if evidence.get('annotation_groups') != groups:
        raise ValueError('多规格引注组与冻结识别证据不一致')
    per_part = {}; scopes = []; seen = set()
    for index, binding in enumerate(groups):
        if not isinstance(binding, dict) or not binding.get('leader_network'):
            raise ValueError('每一规格须来自实际文字及完整源引线树，不能手填逐目标规格')
        identity = binding.get('source_key')
        if identity in seen:
            raise ValueError('多规格组重复使用同一文字；多尺寸合文尚需逐分支源语义')
        seen.add(identity)
        local = {**rule, 'annotation_binding': binding}
        proof = {**evidence, 'annotation_network': binding['leader_network']}
        section, elevation = bind_tag(local, proof, rows, unit)
        scope = scope_fn(local, proof, rows, match, source_key)
        scope['annotation_group_index'] = index
        scopes.append(scope)
        if scope['status'] != 'unique_source_leader_tree':
            continue
        dimensions, _ = section_values(section, unit)
        value = {'section': section, 'dimensions': dimensions, 'elevation': elevation,
                 'annotation_group_index': index, 'annotation_scope_status': 'source_callout_group_bound'}
        for key in scope['target_part_keys']:
            if key in per_part:
                # Even identical values cannot prove two leaders refer to one physical scope.
                per_part[key] = None
            else:
                per_part[key] = copy.deepcopy(value)
    if any(scope['status']=='unresolved' for scope in scopes):
        return {},scopes
    conflicts = sorted(key for key, value in per_part.items() if value is None)
    if conflicts:
        scopes.append({'schema_version': 'qto-duct-annotation-scope/1.0', 'status': 'unresolved',
                       'reason': '多源引注同时指向同一源part，未按数组顺序或相同规格消解',
                       'target_part_keys': [], 'conflicting_part_keys': conflicts,
                       'business_adoption_allowed': False})
    return per_part, scopes
