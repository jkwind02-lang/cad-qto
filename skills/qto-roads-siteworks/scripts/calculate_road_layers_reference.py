#!/usr/bin/env python3
"""Reference-only arithmetic for explicit constant-width road sections."""
import argparse,json,math,sys
from pathlib import Path
def num(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def main():
 p=argparse.ArgumentParser();p.add_argument("input",type=Path);a=p.parse_args()
 try:
  segs=json.loads(a.input.read_text(encoding="utf-8")).get("segments")
  if not isinstance(segs,list) or not segs:raise ValueError("segments必须为非空数组")
  out=[]
  for i,s in enumerate(segs):
   for k in ("id","length_m","width_m","layers","evidence"):
    if k not in s:raise ValueError(f"第{i}项缺少{k}")
   if not num(s["length_m"]) or not num(s["width_m"]) or s["length_m"]<=0 or s["width_m"]<=0:raise ValueError(f"{s['id']}长宽非法")
   if not isinstance(s["layers"],list) or not s["layers"]:raise ValueError(f"{s['id']}缺结构层")
   area=s["length_m"]*s["width_m"];layers=[]
   for layer in s["layers"]:
    if not isinstance(layer.get("name"),str) or not num(layer.get("thickness_m")) or layer["thickness_m"]<=0:raise ValueError(f"{s['id']}层名称或厚度非法")
    layers.append({"name":layer["name"],"area_m2":round(area,6),"thickness_m":layer["thickness_m"],"volume_m3":round(area*layer["thickness_m"],6)})
   out.append({"id":s["id"],"area_m2":round(area,6),"layers":layers,"evidence":s["evidence"]})
  print(json.dumps({"status":"REFERENCE_ONLY","business_adoption_allowed":False,"constant_width_only":True,"items":out},ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,json.JSONDecodeError) as e:
  print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(e)},ensure_ascii=False,indent=2));return 2
if __name__=="__main__":sys.exit(main())
