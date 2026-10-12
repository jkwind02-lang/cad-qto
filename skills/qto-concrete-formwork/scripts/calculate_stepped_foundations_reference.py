"""Source-bound stepped rectangular foundations and their contact-face references.

Supports stacked, nested horizontal rectangular tiers with an optional rectangular
blinding offset. No holes, slopes, cups, adjacent-member deductions or formal BOQ.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'_qto-shared-evidence/scripts'))
from qto_source_rectangles import SourceRectangles, text, finite, read, write_new


def calculate(data):
    if data.get('schema') != 'qto-stepped-foundations-reference/1.0':
        raise ValueError('unsupported schema')
    reader = SourceRectangles(data['source'], data['unit_evidence'], data.get('layout','Model'))
    for binding in data.get('scope_notes', []):
        reader.note(binding)
    rows = data.get('foundations')
    if not isinstance(rows,list) or not rows:
        raise ValueError('foundations must be non-empty')
    result=[];seen=set();source_seen=set()
    for row in rows:
        identifier=text(row.get('physical_id'),'physical_id')
        if identifier in seen:
            raise ValueError('duplicate physical foundation')
        seen.add(identifier)
        tiers=row.get('tiers')
        if not isinstance(tiers,list) or not tiers:
            raise ValueError('tiers must be non-empty')
        previous=None;volume=0.;side=0.;tier_results=[];keys=[]
        for n,tier in enumerate(tiers):
            key=text(tier['footprint_object_id'],'footprint_object_id')
            if key in keys:
                raise ValueError('duplicate source tier')
            keys.append(key)
            polygon=reader.rectangle(key)
            height=finite(reader.number(tier['height_note']),'tier height',positive=True)
            if previous is not None and polygon.difference(previous).area > 1e-9:
                raise ValueError('upper tier extends beyond lower tier')
            previous=polygon
            v=polygon.area*height;a=polygon.length*height
            volume+=v;side+=a
            tier_results.append(dict(source_object_id=key,height_m=height,area_m2=polygon.area,
                                     perimeter_m=polygon.length,volume_m3=v,side_geometry_m2=a))
        signature=keys[0]
        if signature in source_seen:
            raise ValueError('same source foundation renamed and counted twice')
        source_seen.add(signature)
        first=reader.rectangle(keys[0]);blinding=row.get('blinding');b_result=None
        if blinding is not None:
            offset=finite(reader.number(blinding['offset_note']),'blinding offset',positive=True)
            thickness=finite(reader.number(blinding['thickness_note']),'blinding thickness',positive=True)
            polygon=first.buffer(offset,join_style='mitre')
            b_result=dict(area_m2=polygon.area,perimeter_m=polygon.length,thickness_m=thickness,
                          volume_m3=polygon.area*thickness,side_geometry_m2=polygon.length*thickness)
        areas={'tier_sides':side,'bottom':first.area,'exposed_horizontal_tops':first.area}
        if b_result:areas['blinding_sides']=b_result['side_geometry_m2']
        conditions=row.get('face_conditions',{})
        if set(conditions)!=set(areas):
            raise ValueError('face conditions must cover every declared face category')
        faces=[]
        for face,area in areas.items():
            condition=conditions[face]
            state=condition.get('state')
            if state not in ['included','excluded','scenario','unresolved']:
                raise ValueError('invalid face condition')
            text(condition.get('basis'),'face basis')
            bindings=condition.get('source_notes')
            if not isinstance(bindings,list) or not bindings:
                raise ValueError('face condition lacks source notes')
            for binding in bindings:reader.note(binding)
            faces.append(dict(face=face,geometry_area_m2=area,state=state,
                template_area_m2=None if state=='unresolved' else 0. if state=='excluded' else area,
                quantity_level='方案参考' if state=='scenario' else '模型工作参考',basis=condition['basis']))
        result.append(dict(physical_id=identifier,tiers=tier_results,concrete_volume_m3=volume,
                           blinding=b_result,contact_faces=faces))
    return dict(schema='qto-stepped-foundations-evidence/1.0',status='REFERENCE_ONLY',
                business_adoption_allowed=False,source_identity=reader.index['source'],
                authoritative_source=reader.index['authoritative_source'],source_facts=reader.facts,items=result,
                limitations=['No automatic object recognition or design priority decision',
                             'Stacked nested rectangular tiers only; no cups, slopes, holes or adjacent-member deduction',
                             'Face conditions are model interpretations and scenarios; no formal template adoption'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('input',type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    try:
        if args.output.resolve()==args.input.resolve():raise ValueError('output must not replace input')
        result=calculate(read(args.input));result['input_sha256']=hashlib.sha256(args.input.read_bytes()).hexdigest()
        write_new(args.output,result);print(json.dumps({'status':'REFERENCE_ONLY','output':str(args.output),'items':len(result['items'])}));return 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'INPUT_REQUIRED','error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
