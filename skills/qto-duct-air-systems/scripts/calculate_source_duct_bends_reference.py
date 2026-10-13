"""Source-bound constant rectangular smooth bends, open at both ends.

Reads three concentric circular arcs and explicit radial/transverse dimensions.
No recognition, assumed radii, transitions, seams, flanges, end caps or net cutting.
"""
from __future__ import annotations
import argparse,hashlib,json,math,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_qto-shared-evidence/scripts'))
from qto_recognition_workbench import build_index
from qto_cad_source_contract import canonical_scale
from qto_source_rectangles import finite,text,read,write_new


def dot(a,b):return math.fsum(x*y for x,y in zip(a,b))
def norm(a):return math.sqrt(dot(a,a))
def sub(a,b):return [x-y for x,y in zip(a,b)]
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]


class Reader:
    def __init__(self,source,layout='Model'):
        self.index=build_index(source,layout=layout,authoritative_source=source.get('authoritative_source'))
        if self.index['source_identity_issues']:raise ValueError('source identity conflict')
        self.objects={o['object_id']:o for o in self.index['objects']};self.facts=[]
    def object(self,key):
        key=text(key,'source object id')
        if key not in self.objects:raise ValueError('source object not found: '+key)
        return self.objects[key]
    def note(self,b):
        o=self.object(b['object_id']);needle=text(b.get('text_contains'),'note text')
        if not o.get('text') or needle not in o['text']:raise ValueError('note does not match current source')
        self.facts.append(dict(kind='source_note',object_id=o['object_id'],text=o['text']))
        return o['text']
    def number(self,b,scale):
        t=self.note(b);m=re.search(text(b.get('pattern'),'numeric note pattern'),t);g=b.get('group',1)
        if not isinstance(g,int) or isinstance(g,bool) or g<1:raise ValueError('numeric capture group invalid')
        if m is None:raise ValueError('numeric note pattern has no match')
        try:v=float(m.group(g))
        except (IndexError,ValueError) as exc:raise ValueError('numeric note capture is not a number') from exc
        return finite(v,'section dimension',positive=True)*scale
    def arc(self,key,scale):
        o=self.object(key);parts=(o.get('geometry') or {}).get('parts')
        if not isinstance(parts,list) or len(parts)!=1 or parts[0].get('kind')!='ellipse_arc':raise ValueError('one source circular arc required')
        p=parts[0];u=p['u'];v=p['v'];ru=norm(u);rv=norm(v)
        if ru<=0 or abs(ru-rv)>1e-9*max(ru,rv) or abs(dot(u,v))>1e-9*ru*rv:raise ValueError('non-circular or non-uniform transformed arc')
        angle=abs(finite(p['sweep_param'],'arc sweep'))
        if angle<=1e-10 or angle>math.pi+1e-10:raise ValueError('bend sweep must be greater than zero and no more than 180 degrees')
        directions=[]
        for f in (0,.5,1):
            t=p['start_param']+f*p['sweep_param'];directions.append([(a*math.cos(t)+b*math.sin(t))/ru for a,b in zip(u,v)])
        a=dict(object_id=key,center_m=[finite(x,'arc center')*scale for x in p['center']],radius_m=ru*scale,angle_rad=angle,directions=directions,normal=[x/(ru*rv) for x in cross(u,v)])
        self.facts.append(dict(kind='source_circular_arc',**a));return a


def calculate(data):
    if data.get('schema')!='qto-source-duct-bends-reference/1.0':raise ValueError('unsupported schema')
    reader=Reader(data['source'],data.get('layout','Model'));ue=data['unit_evidence'];unit=text(ue.get('unit'),'unit');scale=canonical_scale(unit)
    ur=reader if 'source' not in ue else Reader(ue['source'],ue.get('layout','Model'))
    needle=text(ue.get('text_contains'),'unit note');ur.note(ue)
    phrases={'mm':('毫米','尺寸单位为mm','length unit: mm'),'m':('长度单位为米','length unit: metres'),'cm':('厘米','length unit: cm')}
    if unit not in phrases or not any(x in needle for x in phrases[unit]):raise ValueError('unit evidence must explicitly state the declared physical unit')
    raw=reader.index.get('raw_unit')
    if raw!='Unitless' and canonical_scale(raw)!=scale:raise ValueError('unit conflicts with INSUNITS')
    rows=data.get('elbows')
    if not isinstance(rows,list) or not rows:raise ValueError('elbows must be non-empty')
    seen=set();used=set();items=[]
    for row in rows:
        allowed={'physical_id','inner_arc_object_id','centerline_object_id','outer_arc_object_id','radial_width_note','transverse_height_note'}
        if not isinstance(row,dict) or set(row)!=allowed:raise ValueError('bend row must contain only explicit source bindings; no count, assumed radius or answer override')
        ident=text(row.get('physical_id'),'physical_id')
        if ident in seen:raise ValueError('duplicate physical bend')
        seen.add(ident);keys=[row[x] for x in ['inner_arc_object_id','centerline_object_id','outer_arc_object_id']]
        if len(set(keys))!=3 or any(k in used for k in keys):raise ValueError('source arc reused or renamed and counted twice')
        used.update(keys);inner,centre,outer=[reader.arc(k,scale) for k in keys]
        for a in (inner,outer):
            if math.dist(a['center_m'],centre['center_m'])>1e-8:raise ValueError('arcs are not concentric')
            if abs(a['angle_rad']-centre['angle_rad'])>1e-9:raise ValueError('arc sweeps disagree')
            forward=max(math.dist(x,y) for x,y in zip(a['directions'],centre['directions']))
            reverse=max(math.dist(x,y) for x,y in zip(a['directions'][::-1],centre['directions']))
            if min(forward,reverse)>1e-8:raise ValueError('arcs do not cover the same sector')
        ri,rc,ro=[a['radius_m'] for a in [inner,centre,outer]]
        w=reader.number(row['radial_width_note'],scale);h=reader.number(row['transverse_height_note'],scale)
        if not 0<ri<rc<ro:raise ValueError('arc radius order invalid')
        if abs(ro-ri-w)>1e-8 or abs((ri+ro)/2-rc)>1e-8:raise ValueError('section width or centre radius disagrees with source arcs')
        theta=centre['angle_rad'];faces=dict(inner_curved=ri*theta*h,outer_curved=ro*theta*h,side_1=theta*(ro*ro-ri*ri)/2,side_2=theta*(ro*ro-ri*ri)/2)
        items.append(dict(physical_id=ident,source_arc_ids=keys,radial_width_m=w,transverse_height_m=h,inner_radius_m=ri,centre_radius_m=rc,outer_radius_m=ro,sweep_angle_rad=theta,total_centerline_length_m=rc*theta,open_shell_surface_m2=math.fsum(faces.values()),face_surface_m2=faces,end_caps_included=False,quantity_level='source geometry reference'))
    return dict(schema='qto-source-duct-bends-evidence/1.0',status='REFERENCE_ONLY',business_adoption_allowed=False,source_identity=reader.index['source'],authoritative_source=reader.index['authoritative_source'],unit_source_identity=ur.index['source'],source_facts=reader.facts,unit_facts=ur.facts,items=items,limitations=['Explicit concentric circular arcs and constant rectangular section only','Radial and transverse dimension correspondence is supplied by the model, not automatically recognized','Open theoretical shell only: no seams, thickness offsets, flanges, end caps, vanes or fabrication allowance','No assumed bend radius or extension to other unbound physical instances'])


def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:
        if a.input.resolve()==a.output.resolve():raise ValueError('output must not replace input')
        r=calculate(read(a.input));r['input_sha256']=hashlib.sha256(a.input.read_bytes()).hexdigest();write_new(a.output,r)
        print(json.dumps(dict(status=r['status'],items=len(r['items']),output=str(a.output))));return 0
    except (OSError,ValueError,KeyError,TypeError) as e:print(json.dumps(dict(status='INPUT_REQUIRED',error=str(e))),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
