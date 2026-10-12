"""Source-bound rectangular replacement regions; union before layer volumes."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_qto-shared-evidence/scripts'))
from qto_source_rectangles import SourceRectangles,text,finite,read,write_new


def calculate(data):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    if data.get('schema')!='qto-groundwork-regions-reference/1.0':raise ValueError('unsupported schema')
    reader=SourceRectangles(data['source'],data['unit_evidence'],data.get('layout','Model'))
    for note in data.get('scope_notes',[]):reader.note(note)
    regions=data.get('regions')
    if not isinstance(regions,list) or not regions:raise ValueError('regions must be non-empty')
    row_results=[];polygons=[];ids=set();source_ids=set()
    for row in regions:
        pid=text(row.get('physical_id'),'physical_id');oid=text(row.get('footprint_object_id'),'footprint_object_id')
        if pid in ids or oid in source_ids:raise ValueError('duplicate physical/source region')
        ids.add(pid);source_ids.add(oid)
        base=reader.rectangle(oid);p=list(base.exterior.coords)[:-1]
        if any(abs(p[i][0]-p[(i+1)%4][0])>1e-9 and abs(p[i][1]-p[(i+1)%4][1])>1e-9 for i in range(4)):
            raise ValueError('anisotropic width fraction requires axis-aligned rectangle')
        # Fraction and minimum are explicitly declared project rules, never defaults.
        ext=row['extension_rule'];reader.note(ext['source_note'])
        numerator=finite(ext['numerator'],'fraction numerator',positive=True)
        denominator=finite(ext['denominator'],'fraction denominator',positive=True)
        fraction=numerator/denominator
        needle=ext['source_note']['text_contains']
        if f'{numerator:g}/{denominator:g}' not in needle:raise ValueError('extension fraction is not bound to the note')
        minimum=finite(reader.number(ext['minimum_note']),'minimum extension',positive=True)
        x0,y0,x1,y1=base.bounds;dx=max((x1-x0)*fraction,minimum);dy=max((y1-y0)*fraction,minimum)
        polygon=Polygon([(x0-dx,y0-dy),(x1+dx,y0-dy),(x1+dx,y1+dy),(x0-dx,y1+dy)])
        polygons.append(polygon);row_results.append(dict(physical_id=pid,source_object_id=oid,base_bounds_m=list(base.bounds),
            extension_x_each_m=dx,extension_y_each_m=dy,region_bounds_m=list(polygon.bounds),area_m2=polygon.area))
    layers=data.get('layers')
    if not isinstance(layers,list) or len(layers)!=1:raise ValueError('exactly one flat vertical replacement layer per run')
    union=unary_union(polygons);separate=sum(p.area for p in polygons);layer_results=[];layer_ids=set()
    # All regions must belong to the same flat vertical layer footprint. Different
    # replacement elevations or varying thickness require separate 3D processing.
    for layer in layers:
        lid=text(layer.get('layer_id'),'layer_id')
        if lid in layer_ids:raise ValueError('duplicate replacement layer')
        layer_ids.add(lid)
        thickness=finite(reader.number(layer['thickness_note']),'layer thickness',positive=True)
        text(layer.get('material'),'material')
        if layer.get('earth_state') not in ['compacted','natural_dense','void','unspecified']:
            raise ValueError('invalid declared earth state')
        reader.note(layer['material_note']);reader.note(layer['same_vertical_extent_note'])
        layer_results.append(dict(layer_id=lid,material=layer['material'],earth_state=layer['earth_state'],
            thickness_m=thickness,union_area_m2=union.area,design_geometric_volume_m3=union.area*thickness))
    return dict(schema='qto-groundwork-regions-evidence/1.0',status='REFERENCE_ONLY',business_adoption_allowed=False,
        source_identity=reader.index['source'],authoritative_source=reader.index['authoritative_source'],source_facts=reader.facts,
        regions=row_results,individual_area_sum_m2=separate,union_area_m2=union.area,overlap_deduction_m2=separate-union.area,
        union_wkt=union.wkt,layers=layer_results,limitations=['Bound model selection and project extension rule; no automatic recognition',
         'Constant-thickness flat layers with the same vertical extent only; not general excavation, backfill or soil-state conversion',
         'CAD footprint reference must be compared with design dimensions before adoption'])


def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    try:
        if a.output.resolve()==a.input.resolve():raise ValueError('output must not replace input')
        result=calculate(read(a.input));result['input_sha256']=hashlib.sha256(a.input.read_bytes()).hexdigest();write_new(a.output,result)
        print(json.dumps({'status':'REFERENCE_ONLY','output':str(a.output),'regions':len(result['regions'])}));return 0
    except (OSError,ValueError,KeyError,TypeError) as e:
        print(json.dumps({'status':'INPUT_REQUIRED','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
