#!/usr/bin/env python3
"""Reference-only totals for explicit constant-section pile groups."""
import argparse,json,math,sys
from pathlib import Path
def num(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def main():
 p=argparse.ArgumentParser();p.add_argument("input",type=Path);a=p.parse_args()
 try:
  rows=json.loads(a.input.read_text(encoding="utf-8")).get("pile_groups")
  if not isinstance(rows,list) or not rows:raise ValueError("pile_groups必须为非空数组")
  out=[]
  for i,r in enumerate(rows):
   for k in ("id","pile_type","count","length_each_m","section","evidence"):
    if k not in r:raise ValueError(f"第{i}项缺少{k}")
   if not isinstance(r["count"],int) or isinstance(r["count"],bool) or r["count"]<=0:raise ValueError(f"{r['id']}根数非法")
   if not num(r["length_each_m"]) or r["length_each_m"]<=0:raise ValueError(f"{r['id']}长度非法")
   sec=r["section"]
   if sec.get("shape")=="circle" and num(sec.get("diameter_m")) and sec["diameter_m"]>0:area=math.pi*sec["diameter_m"]**2/4
   elif sec.get("shape")=="rectangle" and num(sec.get("width_m")) and num(sec.get("height_m")) and sec["width_m"]>0 and sec["height_m"]>0:area=sec["width_m"]*sec["height_m"]
   else:raise ValueError(f"{r['id']}截面非法或不受支持")
   total=r["count"]*r["length_each_m"]
   out.append({"id":r["id"],"pile_type":r["pile_type"],"count":r["count"],"total_length_m":round(total,6),"theoretical_volume_m3":round(total*area,6),"evidence":r["evidence"]})
  print(json.dumps({"status":"REFERENCE_ONLY","business_adoption_allowed":False,"constant_section_only":True,"items":out},ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,json.JSONDecodeError) as e:
  print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(e)},ensure_ascii=False,indent=2));return 2
if __name__=="__main__":sys.exit(main())
