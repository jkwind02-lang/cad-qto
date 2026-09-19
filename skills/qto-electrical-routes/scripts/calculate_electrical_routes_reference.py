#!/usr/bin/env python3
"""Reference-only totals for explicit electrical route items."""
import argparse,json,math,sys
from pathlib import Path
ALLOWED={"tray","trunking","conduit","cable","wire","busbar"}
def num(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def main():
 p=argparse.ArgumentParser();p.add_argument("input",type=Path);a=p.parse_args()
 try:
  document=json.loads(a.input.read_text(encoding="utf-8-sig"))
  if document.get("schema")=="qto-electrical-path-ledger/1.0":
   from calculate_electrical_paths import calculate
   print(json.dumps(calculate(document),ensure_ascii=False,indent=2,allow_nan=False));return 0
  rows=document.get("route_items")
  if not isinstance(rows,list) or not rows:raise ValueError("route_items必须为非空数组")
  out=[]
  for i,r in enumerate(rows):
   for k in ("id","kind","system","spec","path_length_m","parallel_count","extra_each_m","evidence"):
    if k not in r:raise ValueError(f"第{i}项缺少{k}")
   if r["kind"] not in ALLOWED:raise ValueError(f"{r['id']} kind非法")
   if not num(r["path_length_m"]) or r["path_length_m"]<=0 or not num(r["extra_each_m"]) or r["extra_each_m"]<0:raise ValueError(f"{r['id']}路径或附加长度非法")
   if not isinstance(r["parallel_count"],int) or isinstance(r["parallel_count"],bool) or r["parallel_count"]<=0:raise ValueError(f"{r['id']}并行数量非法")
   total=r["parallel_count"]*(r["path_length_m"]+r["extra_each_m"])
   out.append({"id":r["id"],"kind":r["kind"],"system":r["system"],"spec":r["spec"],"parallel_count":r["parallel_count"],"physical_or_conductor_length_m":round(total,6),"evidence":r["evidence"]})
  print(json.dumps({"status":"REFERENCE_ONLY","business_adoption_allowed":False,"explicit_paths_only":True,"items":out},ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,json.JSONDecodeError) as e:
  print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(e)},ensure_ascii=False,indent=2));return 2
if __name__=="__main__":sys.exit(main())
