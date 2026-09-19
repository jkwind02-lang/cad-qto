#!/usr/bin/env python3
"""Reference-only aggregation of explicitly scheduled framing members."""
import argparse,json,math,sys
from pathlib import Path
REQ=("id","system","role","spec","count","length_each_m","unit_mass_kg_m","evidence")
def num(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def main():
 p=argparse.ArgumentParser();p.add_argument("input",type=Path);a=p.parse_args()
 try:
  rows=json.loads(a.input.read_text(encoding="utf-8")).get("members")
  if not isinstance(rows,list) or not rows: raise ValueError("members必须为非空数组")
  out=[]
  for i,r in enumerate(rows):
   miss=[k for k in REQ if k not in r]
   if miss: raise ValueError(f"第{i}项缺少字段: {','.join(miss)}")
   if not isinstance(r["count"],int) or isinstance(r["count"],bool) or r["count"]<=0: raise ValueError(f"{r['id']} count必须为正整数")
   if not num(r["length_each_m"]) or r["length_each_m"]<=0: raise ValueError(f"{r['id']}单件长度非法")
   if not num(r["unit_mass_kg_m"]) or r["unit_mass_kg_m"]<=0: raise ValueError(f"{r['id']}单位质量非法")
   if not isinstance(r["evidence"],str) or not r["evidence"].strip(): raise ValueError(f"{r['id']}缺证据")
   total=r["count"]*r["length_each_m"]
   out.append({"id":r["id"],"system":r["system"],"role":r["role"],"spec":r["spec"],"count":r["count"],"total_length_m":round(total,6),"theoretical_mass_kg":round(total*r["unit_mass_kg_m"],6),"evidence":r["evidence"]})
  print(json.dumps({"status":"REFERENCE_ONLY","business_adoption_allowed":False,"items":out},ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,json.JSONDecodeError) as e:
  print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(e)},ensure_ascii=False,indent=2));return 2
if __name__=="__main__":sys.exit(main())
