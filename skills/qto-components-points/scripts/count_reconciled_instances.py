#!/usr/bin/env python3
"""Count already reconciled unique instances; never performs semantic matching."""
import argparse,collections,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"_qto-shared-evidence"/"scripts"))
from model_recognition_basis import validate_working_basis
def num(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def main():
 p=argparse.ArgumentParser();p.add_argument("input",type=Path);a=p.parse_args()
 try:
  data=json.loads(a.input.read_text(encoding="utf-8"));rows=data.get("reconciled_instances")
  validate_working_basis(data.get("recognition_basis"))
  if not isinstance(rows,list) or not rows:raise ValueError("reconciled_instances必须为非空数组")
  seen=set();groups=collections.defaultdict(lambda:{"count":0,"area_m2":0.0,"area_complete":True})
  for i,r in enumerate(rows):
   for k in ("physical_id","category","system","spec","unit","evidence"):
    if not isinstance(r.get(k),str) or not r[k].strip():raise ValueError(f"第{i}项{k}缺失")
   if r["physical_id"] in seen:raise ValueError(f"重复physical_id: {r['physical_id']}")
   seen.add(r["physical_id"]);key=(r["category"],r["system"],r["spec"],r["unit"],r.get("quantity_level","未声明"));g=groups[key];g["count"]+=1
   w,h=r.get("width_m"),r.get("height_m")
   if w is None and h is None:g["area_complete"]=False
   elif not num(w) or not num(h) or w<=0 or h<=0:raise ValueError(f"{r['physical_id']}宽高必须同时为正数")
   else:g["area_m2"]+=w*h
  out=[]
  for k,g in sorted(groups.items()):
   out.append({"category":k[0],"system":k[1],"spec":k[2],"unit":k[3],"quantity_level":k[4],"count":g["count"],"derived_area_m2":round(g["area_m2"],6) if g["area_complete"] else None})
  print(json.dumps({"status":"REFERENCE_ONLY","business_adoption_allowed":False,"recognition_basis":data.get("recognition_basis"),"unique_instance_count":len(seen),"groups":out},ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,json.JSONDecodeError) as e:
  print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(e)},ensure_ascii=False,indent=2));return 2
if __name__=="__main__":sys.exit(main())
