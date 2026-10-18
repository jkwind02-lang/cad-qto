"""Same-baseline TEXT candidates, never a claim that adjacent labels are one object."""
import math
import unicodedata
from collections import defaultdict


def line_candidates(rows, sheet_of=None):
    pools=defaultdict(list); missing=[]
    for r in rows:
        if r.get('entity_type')!='TEXT' or not r.get('text'): continue
        x,y,a,h=r.get('x'),r.get('y'),r.get('rotation'),r.get('height')
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (x,y,a,h)) or h<=0:
            missing.append(r.get('handle'));continue
        factor=r.get('width_factor',1)
        if not isinstance(factor,(int,float)) or isinstance(factor,bool) or not math.isfinite(factor) or factor<=0:
            missing.append(r.get('handle'));continue
        page=sheet_of(r) if sheet_of else r.get('layout')
        if page is None:continue  # outside/ambiguous sheet is never joined across sheets
        angle=a%(2*math.pi);u=(math.cos(angle),math.sin(angle))
        key=(r.get('layout'),r.get('layer'),page,round(angle,5))
        pools[key].append((x*u[0]+y*u[1],-x*u[1]+y*u[0],r))
    out=[]
    for values in pools.values():
        baselines=[]
        for t,b,r in sorted(values,key=lambda z:(z[1],z[0])):
            line=next((g for g in baselines if abs(b-g[0][1])<0.5*min(r['height'],g[0][2]['height']) and .8<=r['height']/g[0][2]['height']<=1.25),None)
            if line is None:baselines.append([(t,b,r)])
            else:line.append((t,b,r))
        for line in baselines:
            chain=[]
            def emit():
                if len(chain)<2:return
                parts=[v[2] for v in chain]
                out.append({**parts[0],'text':''.join(p['text'] for p in parts),'via':'same_baseline_candidate',
                            'member_handles':[p['handle'] for p in parts],
                            'member_points':[[p['x'],p['y']] for p in parts],
                            'source_texts':[p['text'] for p in parts]})
            for v in sorted(line,key=lambda z:z[0]):
                if chain:
                    last=chain[-1];prev=last[2]
                    width=sum(1 if unicodedata.east_asian_width(c) in ('W','F') else .6 for c in prev['text'])*prev['height']*prev.get('width_factor',1)
                    delta=v[0]-last[0]
                    if delta<=0 or delta>width+2*min(prev['height'],v[2]['height']):
                        emit();chain=[]
                chain.append(v)
            emit()
    return out,missing
