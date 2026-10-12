"""Re-read source-bound rectangular footprints and explicit numeric notes.

This small geometry adapter never selects objects or interprets construction
roles. All quantities produced by callers remain model working references.
"""
from __future__ import annotations
import copy
import json
import math
import re
from pathlib import Path
from qto_cad_source_contract import canonical_scale
from qto_recognition_workbench import build_index


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be non-empty text')
    return value.strip()


def finite(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    if positive and value <= 0:
        raise ValueError(label + ' must be positive')
    return float(value)


class SourceRectangles:
    def __init__(self, source, unit_evidence, layout='Model'):
        source = copy.deepcopy(source)
        self.index = build_index(source, layout=layout, authoritative_source=source.get('authoritative_source'))
        if self.index['source_identity_issues']:
            raise ValueError('source identity conflict')
        self.objects = {o['object_id']: o for o in self.index['objects']}
        self.facts = []
        unit = text(unit_evidence.get('unit'), 'unit')
        self.scale = canonical_scale(unit)
        obj = self.object(unit_evidence['object_id'])
        needle = text(unit_evidence.get('text_contains'), 'unit evidence text')
        if not obj.get('text') or needle not in obj['text']:
            raise ValueError('unit note does not match current source')
        # A caller's unit name alone cannot establish source units.
        phrases = {'mm': ('毫米', 'millimeter'), 'm': ('长度单位为米', 'length unit: metres'),
                   'cm': ('厘米', 'centimeter'), 'in': ('英寸', 'inch'), 'ft': ('英尺', 'feet')}
        choices = [values for name, values in phrases.items() if canonical_scale(name) == self.scale]
        if not choices or not any(v in needle for v in choices[0]):
            raise ValueError('unit evidence must explicitly state the declared physical unit')
        raw = self.index.get('raw_unit')
        if raw != 'Unitless' and canonical_scale(raw) != self.scale:
            raise ValueError('declared unit conflicts with INSUNITS')
        self.facts.append(dict(kind='unit_note', object_id=obj['object_id'], text=obj['text'], unit=unit))

    def object(self, object_id):
        object_id = text(object_id, 'source object id')
        if object_id not in self.objects:
            raise ValueError('source object not found: ' + object_id)
        return self.objects[object_id]

    def number(self, binding):
        obj = self.object(binding['object_id'])
        content = obj.get('text')
        needle = text(binding.get('text_contains'), 'numeric note text')
        if not content or needle not in content:
            raise ValueError('numeric note does not match current source')
        pattern = text(binding.get('pattern'), 'numeric note pattern')
        match = re.search(pattern, content)
        if match is None:
            raise ValueError('numeric note pattern has no match')
        group = binding.get('group', 1)
        if not isinstance(group, int) or isinstance(group, bool) or group < 1:
            raise ValueError('numeric note capture group invalid')
        try:
            value = float(match.group(group))
        except (IndexError, ValueError) as exc:
            raise ValueError('numeric note capture is not a number') from exc
        value = finite(value, 'source numeric value')
        self.facts.append(dict(kind='numeric_note', object_id=obj['object_id'], text=content,
                               pattern=pattern, group=group, value=value, unit=binding.get('unit')))
        return value * canonical_scale(binding['unit'])

    def rectangle(self, object_id):
        from shapely.geometry import Polygon
        obj = self.object(object_id)
        g = obj.get('geometry') or {}
        parts = g.get('parts')
        if not g.get('closed') or not isinstance(parts, list) or len(parts) != 4:
            raise ValueError('footprint must be a source closed four-sided rectangle')
        if any(p.get('kind') != 'line' for p in parts):
            raise ValueError('curved footprint is unsupported')
        points = [p['start'] for p in parts]
        if any(math.dist(parts[i]['end'], parts[(i+1)%4]['start']) > 1e-7 for i in range(4)):
            raise ValueError('footprint is disconnected')
        if max(p[2] for p in points)-min(p[2] for p in points) > 1e-7:
            raise ValueError('footprint must be horizontal')
        vectors = [[parts[i]['end'][j]-parts[i]['start'][j] for j in (0,1)] for i in range(4)]
        lengths = [math.hypot(*v) for v in vectors]
        if any(v <= 0 for v in lengths):
            raise ValueError('degenerate footprint')
        for i in range(4):
            a,b=vectors[i],vectors[(i+1)%4]
            if abs(sum(x*y for x,y in zip(a,b))) > 1e-9*lengths[i]*lengths[(i+1)%4]:
                raise ValueError('footprint is not rectangular')
        polygon = Polygon([(p[0]*self.scale, p[1]*self.scale) for p in points])
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError('invalid footprint')
        self.facts.append(dict(kind='source_rectangle', object_id=obj['object_id'],
                               points_raw=points, raw_unit_scale_to_m=self.scale,
                               area_m2=polygon.area, perimeter_m=polygon.length))
        return polygon

    def note(self, binding):
        obj = self.object(binding['object_id'])
        needle = text(binding['text_contains'], 'note text')
        if not obj.get('text') or needle not in obj['text']:
            raise ValueError('note does not match current source')
        self.facts.append(dict(kind='scope_note', object_id=obj['object_id'], text=obj['text']))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
