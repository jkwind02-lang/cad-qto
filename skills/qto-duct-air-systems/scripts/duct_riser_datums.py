"""Read actual typed datum equations; shared elevation facts own the arithmetic."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import re
import qto_elevation_facts as ef


def datum_key(scope, kind, identifier):
    return json.dumps([scope, kind, identifier], ensure_ascii=False, separators=(',', ':'))


def read_transforms(config, base, protected, duct):
    specs = config.get('datum_transform_sources', [])
    if not isinstance(specs, list): raise ValueError('datum_transform_sources须为数组')
    transforms = []; facts = []; bindings = []; seen = set()
    for spec in specs:
        _, manifest_ref = duct.read_ref(spec.get('manifest'), base)
        manifest_path = Path(manifest_ref['path']); protected.append(manifest_path)
        stream, blocks, manifest = duct.adapter.source_from_manifest(manifest_path)
        protected.extend([stream, blocks, Path(manifest['authoritative_source']['path']), Path(manifest['exchange_dxf']['path'])])
        layout = duct.text(spec.get('layout'), 'datum.layout'); failures = []
        rows = list(duct.adapter.expanded_manifest_rows(stream, blocks, manifest, 32, failures, target_layout=layout))
        by_key = {duct.source_key(row): row for row in rows}
        if len(rows) != len(by_key): raise ValueError('基准源完整实例身份重复')
        annotations = spec.get('annotations')
        if not isinstance(annotations, list) or not annotations: raise ValueError('基准转换须有实际源等式文字')
        bindings.append({'manifest': manifest_ref, 'layout': layout, 'authoritative_source': manifest['authoritative_source'],
                         'exchange_dxf': manifest['exchange_dxf'], 'expansion_diagnostics': failures})
        for annotation in annotations:
            if not annotation.get('basis') or not annotation.get('source_locator'):
                raise ValueError('基准源等式缺语义依据或定位')
            key = annotation.get('source_key'); row = by_key.get(key)
            signature = (manifest['authoritative_source']['sha256'], layout, key)
            if signature in seen: raise ValueError('同一实际基准源等式不能重复输入')
            seen.add(signature)
            if row is None or row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:
                raise ValueError('基准源等式文字实例缺失')
            if row.get('text') != annotation.get('expected_text'): raise ValueError('基准等式文字与实际冻结源不符')
            found = re.fullmatch(annotation.get('pattern', ''), row['text'])
            required = ('scope_id','from_datum_id','from_datum_kind','to_datum_id','to_datum_kind','operator','offset','unit')
            if not found or any(not found.groupdict().get(k) for k in required):
                raise ValueError('实际基准等式须完整捕获作用域、两端类型和ID、加减号、偏移与单位')
            values = found.groupdict()
            kinds = {values['from_datum_kind'], values['to_datum_kind']}
            if kinds != {'absolute', 'project_common'}:
                raise ValueError('本适配只接明确project_common与absolute间的实际源等式')
            if values['operator'] not in {'+', '-'}: raise ValueError('基准源等式必须明确实际加减号')
            offset = float(values['offset'])
            if not math.isfinite(offset) or offset < 0: raise ValueError('基准等式偏移须为有限非负模量，符号单独取自实际源')
            offset = offset if values['operator'] == '+' else -offset
            tid = 'DATUM-' + duct.digest(list(signature))
            transforms.append({'transform_id': tid, 'from_datum': datum_key(values['scope_id'], values['from_datum_kind'], values['from_datum_id']),
                        'to_datum': datum_key(values['scope_id'], values['to_datum_kind'], values['to_datum_id']),
                        'value': offset, 'unit': values['unit'], 'source_ids': [tid], 'level': 'inferred', 'basis': annotation['basis']})
            facts.append({'transform_id': tid, **values, 'signed_offset': offset, 'source_key': key, 'source_text': row['text'],
                         'source_sha256': manifest['authoritative_source']['sha256'], 'manifest': manifest_ref,
                         'layout': layout, 'basis': annotation['basis'], 'source_locator': annotation['source_locator'],
                         'equation_semantics': 'z_to_equals_z_from_plus_signed_offset'})
    return transforms, facts, bindings


def normalize(observations, transforms, facts, duct):
    target = datum_key(observations[0]['scope_id'], observations[0]['datum_kind'], observations[0]['datum_id'])
    values = []; paths = []; used = set()
    for original in observations:
        source = datum_key(original['scope_id'], original['datum_kind'], original['datum_id'])
        path = ef.datum_path(transforms, source, target)
        if path is None:
            return observations, '端口基准ID或类型不同且无本作用域实际源等式，不默认同基准相减', None
        offset, transform_ids, source_ids, levels = path
        value = copy.copy(original)
        value['source_center_elevation_m'] = original['center_elevation_m']
        value['center_elevation_m'] = original['center_elevation_m'] + offset
        value['normalized_datum'] = json.loads(target)
        values.append(value); used.update(transform_ids)
        paths.append({'source_part_key': original['source_part_key'], 'source_datum': json.loads(source),
                      'target_datum': json.loads(target), 'offset_m': offset, 'transform_ids': transform_ids})
    record = None
    if used:
        record = {'target_datum': json.loads(target), 'paths': paths, 'source_facts': [f for f in facts if f['transform_id'] in used],
                  'evaluator': duct.ref(Path(ef.__file__).resolve()), 'semantic_review': 'source_equation_interpretation_not_human_approval'}
    return values, None, record
