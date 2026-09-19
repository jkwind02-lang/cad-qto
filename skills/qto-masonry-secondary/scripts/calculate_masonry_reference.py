#!/usr/bin/env python3
"""Reference-only arithmetic for fully resolved masonry wall segments."""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path

REQ = ("id", "length_m", "height_m", "thickness_m", "opening_union_area_m2", "embedded_volume_m3", "evidence")

def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("input", type=Path); ap.add_argument("--output", type=Path); a=ap.parse_args()
    try:
        data=json.loads(a.input.read_text(encoding="utf-8")); rows=data.get("wall_segments")
        if not isinstance(rows,list) or not rows: raise ValueError("wall_segments必须为非空数组")
        out=[]
        for n,row in enumerate(rows):
            missing=[k for k in REQ if k not in row]
            if missing: raise ValueError(f"第{n}项缺少字段: {','.join(missing)}")
            if not isinstance(row["evidence"],str) or not row["evidence"].strip(): raise ValueError(f"{row['id']}缺少证据定位")
            vals=[row[k] for k in REQ[1:6]]
            if not all(finite(v) for v in vals): raise ValueError(f"{row['id']}存在非有限数值")
            l,h,t,o,e=vals
            if l<=0 or h<=0 or t<=0 or o<0 or e<0: raise ValueError(f"{row['id']}尺寸或扣减非法")
            gross=l*h
            if o>gross: raise ValueError(f"{row['id']}洞口并集大于毛墙面积")
            net_area=gross-o; before=net_area*t
            if e>before: raise ValueError(f"{row['id']}占位体积大于扣洞口后的墙体体积")
            out.append({"id":row["id"],"gross_area_m2":round(gross,6),"net_area_m2":round(net_area,6),"net_volume_m3":round(before-e,6),"evidence":row["evidence"]})
        result={"status":"REFERENCE_ONLY","business_adoption_allowed":False,"items":out,"limitations":["只验证已闭合输入的算术","不判断墙段、洞口或二次构件归属","不代表清单或合同量"]}
        text=json.dumps(result,ensure_ascii=False,indent=2)
        if a.output: a.output.write_text(text+"\n",encoding="utf-8")
        else: print(text)
        return 0
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(json.dumps({"status":"INPUT_REQUIRED","stop":True,"error":str(exc)},ensure_ascii=False,indent=2)); return 2
if __name__=="__main__": sys.exit(main())
