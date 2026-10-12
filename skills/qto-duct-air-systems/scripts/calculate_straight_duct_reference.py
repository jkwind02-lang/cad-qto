#!/usr/bin/env python3
"""Reference-only length and surface for explicit straight duct sections."""
import argparse,json,math,sys
from pathlib import Path
def num(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def main():
 p=argparse.ArgumentParser();p.add_argument("input",type=Path);a=p.parse_args()
 try:
  rows=json.loads(a.input.read_text(encoding="utf-8")).get("straight_sections")
  if not isinstance(rows,list) or not rows:raise ValueError("straight_sections必须为非空数组")
  out=[];seen=set()
  for i,r in enumerate(rows):
   for k in ("id","system","shape","count","length_each_m","evidence"):
    if k not in r:raise ValueError(f"第{i}项缺少{k}")
   if not isinstance(r["id"],str) or not r["id"].strip():raise ValueError(f"第{i}项id必须为非空文本")
   if r["id"] in seen:raise ValueError(f"直管段id重复: {r['id']}")
   seen.add(r["id"])
   if not isinstance(r["count"],int) or isinstance(r["count"],bool) or r["count"]<=0 or not num(r["length_each_m"]) or r["length_each_m"]<=0:raise ValueError(f"{r['id']}数量或长度非法")
   if r["shape"]=="rectangle" and num(r.get("width_m")) and num(r.get("height_m")) and r["width_m"]>0 and r["height_m"]>0:per=2*(r["width_m"]+r["height_m"]);spec=f"{r['width_m']}x{r['height_m']}m"
   elif r["shape"]=="circle" and num(r.get("diameter_m")) and r["diameter_m"]>0:per=math.pi*r["diameter_m"];spec=f"D{r['diameter_m']}m"
   else:raise ValueError(f"{r['id']}截面非法或不受支持")
   length=r["count"]*r["length_each_m"]
   out.append({"id":r["id"],"system":r["system"],"spec":spec,"total_centerline_length_m":round(length,6),"theoretical_surface_m2":round(per*length,6),"evidence":r["evidence"]})
  print(json.dumps({"status":"REFERENCE_ONLY","business_adoption_allowed":False,"straight_sections_only":True,"items":out},ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,json.JSONDecodeError) as e:
  print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(e)},ensure_ascii=False,indent=2));return 2
if __name__=="__main__":sys.exit(main())
