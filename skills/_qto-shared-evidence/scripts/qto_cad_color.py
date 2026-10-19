"""Neutral CAD colour facts and instance inheritance, without material semantics.

ACI 0 is ByBlock, 256 is ByLayer. TrueColor takes precedence. Layer 0
inherits the enclosing INSERT's effective layer; ByBlock takes its colour.
No missing layer, missing parent or old cache is assigned display-default white.
"""
from __future__ import annotations
import copy

SCHEMA = 'qto-cad-color/1.0'


def raw_color(entity):
    def get(name, default=None):
        try:
            return entity.dxf.get(name, default)
        except AttributeError:
            return default
    return {'aci': entity.dxf.get('color', 256),
            'aci_explicit': entity.dxf.hasattr('color'),
            'true_color': get('true_color'), 'color_name': get('color_name')}


def layer_colors(document):
    return [{'name': layer.dxf.name, 'entity_handle': layer.dxf.handle,
             'raw_color': raw_color(layer)} for layer in document.layers]


def _value(raw, *, layer=False):
    true = raw.get('true_color')
    if true is not None:
        if isinstance(true, int) and not isinstance(true, bool) and 0 <= true <= 0xffffff:
            return {'model': 'rgb', 'value': [(true >> 16) & 255, (true >> 8) & 255, true & 255]}, None
        return None, 'invalid_true_color'
    aci = raw.get('aci')
    if isinstance(aci, int) and not isinstance(aci, bool):
        if layer:
            aci = abs(aci)  # the sign records layer-off state, not another colour
        if 1 <= aci <= 255:
            return {'model': 'aci', 'value': aci}, None
    return None, None


def resolve_color(row, layers, parent=None, *, source_path=None):
    raw = row.get('raw_color')
    original = row.get('source_layer', row.get('layer'))
    effective = parent.get('effective_layer') if original == '0' and parent else original
    result = {'schema_version': SCHEMA, 'source_sha256': row.get('source_sha256'),
              'source_record_id': row.get('source_definition_record_id', row.get('record_id')),
              'source_instance_path': source_path or row.get('record_id'),
              'source_layer': original, 'effective_layer': effective,
              'raw_color': copy.deepcopy(raw), 'status': 'unresolved',
              'effective_color': None, 'trace': [], 'issues': []}
    trace = {'kind': 'entity', 'source_record_id': result['source_record_id'],
             'source_instance_path': result['source_instance_path'], 'entity_handle': row.get('entity_handle'),
             'source_layer': original, 'effective_layer': effective, 'raw_color': copy.deepcopy(raw)}
    result['trace'].append(trace)
    if not isinstance(raw, dict):
        result['issues'] = ['source_color_unavailable_reextract_neutral']
        return result
    value, problem = _value(raw)
    if problem:
        result['issues'].append(problem)
    elif value is not None:
        result['effective_color'] = value
    elif raw.get('aci') == 0 and not isinstance(raw.get('aci'), bool):
        if parent is not None:
            result['effective_color'] = copy.deepcopy(parent.get('effective_color'))
            result['trace'].append({'kind': 'byblock_parent', 'source_instance_path': parent.get('source_instance_path')})
            result['trace'].extend(copy.deepcopy(parent.get('trace', [])))
        if result['effective_color'] is None:
            result['issues'].append('byblock_parent_color_unresolved')
    elif raw.get('aci') == 256:
        matching = [item for item in layers if str(item.get('name', '')).casefold() == str(effective).casefold()]
        if len(matching) == 1:
            layer = matching[0]
            result['trace'].append({'kind': 'bylayer', 'layer': layer['name'],
                                    'layer_handle': layer.get('entity_handle'), 'raw_color': copy.deepcopy(layer.get('raw_color'))})
            value, problem = _value(layer.get('raw_color', {}), layer=True)
            result['effective_color'] = value
            if problem or value is None:
                result['issues'].append(problem or 'layer_color_unresolved')
        else:
            result['issues'].append('layer_color_missing_or_ambiguous')
    else:
        result['issues'].append('invalid_entity_aci')
    if original == '0' and parent:
        result['trace'].append({'kind': 'layer_zero_inheritance', 'parent_source_instance_path': parent.get('source_instance_path'),
                                'effective_layer': effective})
    if result['effective_color'] is not None:
        result['status'] = 'resolved'
    return result


def verify_exchange_colors(manifest, rows):
    """Check new neutral colour facts against the frozen DXF, not a signed JSON claim.

    Returns original DXF text by definition record id for material statements.
    Old neutral caches remain usable as colour-unknown; they cannot carry rules.
    """
    if manifest.get('color_schema_version') != SCHEMA:
        return {}
    from qto_cad_geometry import read_dxf_with_diagnostics
    document, _ = read_dxf_with_diagnostics(manifest['exchange_dxf']['path'])
    if manifest.get('layer_colors') != layer_colors(document):
        raise ValueError('颜色层表与实际冻结DXF不符')
    original_texts = {}
    layout_owners = {layout.block_record_handle: layout.name for layout in document.layouts}
    for row in rows:
        entity = document.entitydb.get(str(row.get('entity_handle', '')))
        if entity is None or row.get('raw_color') != raw_color(entity):
            raise ValueError('中性图元颜色与实际冻结DXF不符')
        original_layer = row.get('source_layer', row.get('layer'))
        if original_layer != entity.dxf.get('layer', '') or row.get('entity_type') != entity.dxftype():
            raise ValueError('颜色源图元类型/图层与实际DXF不符')
        definition_id = row.get('source_definition_record_id', row.get('record_id'))
        owner = str(entity.dxf.get('owner', ''))
        actual_owner = document.entitydb.get(owner)
        if (row.get('source_sha256') != manifest['authoritative_source']['sha256']
                or definition_id != 'CAD-'+str(entity.dxf.handle)
                or (row.get('parent_block') and (actual_owner is None or actual_owner.dxf.name != row['parent_block']))
                or (not row.get('parent_block') and layout_owners.get(owner) != row.get('layout'))
                or (entity.dxftype() == 'INSERT' and row.get('block_name') != entity.dxf.name)):
            raise ValueError('颜色源实例的原图/定义/布局/块引用与实际冻结DXF不符')
        if entity.dxftype() in {'TEXT', 'MTEXT'}:
            actual = entity.text if entity.dxftype() == 'MTEXT' else str(entity.dxf.text)
            if row.get('text') != actual:
                raise ValueError('材质说明原文与实际冻结DXF不符')
            original_texts[definition_id] = actual
    return original_texts


def resolve_manifest_color(row, manifest, parent=None, *, source_path=None):
    """Missing/unsupported colour contracts cannot promote arbitrary raw fields.

    Legacy geometry stays readable. A raw_color key in an old or hand-edited
    neutral file is not verified colour evidence, even if its artifact is rehashed.
    """
    supported = manifest.get('color_schema_version') == SCHEMA
    trusted = row if supported else {**row, 'raw_color': None}
    result = resolve_color(trusted, manifest.get('layer_colors', []) if supported else [],
                           parent, source_path=source_path)
    if not supported:
        result['issues'].append('unverified_color_schema')
    return result
