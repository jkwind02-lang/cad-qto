"""Finalize ports, entrypoint links and review metadata in isolated checkout."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
def edit(path,old,new):
    p=ROOT/path;s=p.read_text(encoding='utf-8-sig')
    if new in s:return
    if s.count(old)!=1:raise RuntimeError(path+': expected one replacement for '+repr(old[:80]))
    p.write_text(s.replace(old,new),encoding='utf-8',newline='\n')
edit('skills/qto-components-points/scripts/build_network_endpoint_handoff.py','''                    for optional in ("port_dn", "assumption_id", "port_source_id"):
                        if port.get(optional) is not None:
                            terminal[optional] = port[optional]''','''                    for optional in ("port_dn", "assumption_id", "port_source_id", "port_evidence_level", "space_id", "segment_id", "source_segment_id", "coordinate_unit"):
                        value = port.get(optional, raw.get(optional))
                        if value is not None:
                            terminal[optional] = value''')
edit('skills/qto-components-points/scripts/build_component_instances_from_neutral.py','''                                    port.setdefault("point", base["point"])''','''                                    point_space = port.pop("point_coordinate_space", "output")
                                    if point_space not in {"output", "drawing", "block"}:
                                        fail("端口坐标系必须为output/drawing/block")
                                    if "point" in port and point_space != "output":
                                        p = port["point"]
                                        if not isinstance(p, list) or len(p) not in (2, 3):
                                            fail("端口坐标必须为二维或三维坐标")
                                        p = [finite(v, "port.point") for v in p]
                                        if point_space == "block":
                                            p = transform_point(p, matrix)
                                        port["point"] = [v * factor for v in p]
                                    port.setdefault("point", base["point"])
                                    port.setdefault("space_id", str(rule.get("space_id") or scope.get("space_id") or target_layout))
                                    port.setdefault("coordinate_unit", coordinate_unit)''')
edit('skills/qto-electrical-routes/scripts/calculate_electrical_routes_reference.py','''  rows=json.loads(a.input.read_text(encoding="utf-8")).get("route_items")''','''  document=json.loads(a.input.read_text(encoding="utf-8-sig"))
  if document.get("schema")=="qto-electrical-path-ledger/1.0":
   from calculate_electrical_paths import calculate
   print(json.dumps(calculate(document),ensure_ascii=False,indent=2,allow_nan=False));return 0
  rows=document.get("route_items")''')
def append_once(path,heading,body):
    p=ROOT/path;s=p.read_text(encoding='utf-8-sig')
    if heading not in s:p.write_text(s.rstrip()+'\n\n'+heading+'\n\n'+body.rstrip()+'\n',encoding='utf-8',newline='\n')
append_once('skills/_qto-shared-evidence/references/人工思路识图与算量流程.md','## 分段、跨图与修改重算的执行接续','进入管网分段、跨层接口、电气共用路径或参数修改时，按[按人工算量顺序执行与重算](按人工算量顺序执行与重算.md)对应小节执行。复用本轮对象和证据，不另要求用户逐项填写；模型负责完成读图和输入准备。合成测试通过不代表原图识别或专业采用条件已闭合。')
for skill in ('qto-pipe-networks','qto-electrical-routes','qto-components-points','qto-coordinator'):
    append_once('skills/'+skill+'/SKILL.md','## 已识别对象的路径与重算接续','本轮对象关系已由模型读图形成后，按公共[按人工算量顺序执行与重算](../_qto-shared-evidence/references/按人工算量顺序执行与重算.md)使用完整曲线分段、跨图接口、端口坐标或电气路径台账；修改已确定的参数/规则时保留依赖重算结果。只读取与本任务相关的小节，不自动扩大专业范围。原始识图责任、候选成熟度和正式采用边界不变。')
append_once('skills/qto-electrical-routes/references/常规计算逻辑.md','## 路径台账执行入口','已由本轮读图识别路径、通道和回路时，使用`qto-electrical-path-ledger/1.0`及`scripts/calculate_electrical_paths.py`复算。现有参考计算入口也接受新契约；旧route_items保留。字段和作用边界见公共[执行与重算](../../_qto-shared-evidence/references/按人工算量顺序执行与重算.md)。新增脚本不识别原图回路，不能仅靠改输入schema升级业务采用。')
cap=ROOT/'skills/qto-electrical-routes/capability.json';c=json.loads(cap.read_text(encoding='utf-8-sig'))
new='按已识别的有序路径台账分别复算物理通道及回路线缆，核对共用路径、根数、端头和单位'
if new not in c['automatic_scope']:c['automatic_scope'].append(new)
c['known_limitations']=['原图回路、线缆根数及敷设关系仍需模型依据本次图纸识别；无已验证的全自动原图识别链','路径台账只执行明确输入，不自动确定预留、计量规则或正式采用']
cap.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
p=ROOT/'README.md';s=p.read_text(encoding='utf-8-sig').replace('0.1.1+codex.20260920','0.2.0+codex.20260920')
marker='## 本轮人工算量路径改进'
if marker not in s:
    intro='''## 本轮人工算量路径改进

当前版本 `0.2.0+codex.20260920`，沿用13个专业与协调入口。新增证据驱动的管网分段、图页隔离及跨层接口、空间索引、明确算式依赖重算，以及电气通道/回路分离的路径台账复算。模型仍负责识图，未核实部分不冒充确认量。

[人工算量路径复核](docs/人工算量计算路径复核_20260920.md) · [本轮修复与验证边界](docs/计算路径改进记录_20260920.md) · [运行输入与重算说明](skills/_qto-shared-evidence/references/按人工算量顺序执行与重算.md)

全部Python回归运行 `python -m pytest tests -q`；HTML契约及交互检查沿用下文命令。实际通过记录以对应提交的Actions日志为准；不把合成回归称为真实DWG或识图准确率验收。

'''
    s=s.replace('## 覆盖专业',intro+'## 覆盖专业',1)
p.write_text(s,encoding='utf-8',newline='\n')
p=ROOT/'仓库发布核对.json';r=json.loads(p.read_text(encoding='utf-8-sig'))
r['current_revision']={'version':'0.2.0+codex.20260920','baseline_commit':'48bf5cef731b321e6c28abb52a73a21b8a2d6356','scope':'人工算量路径的结构修复；详见docs/计算路径改进记录_20260920.md','validation':'以对应提交的Windows/Linux Actions实际日志为准；本字段不自证测试通过','local_installation_updated':False,'real_dwg_or_browser_visual_validation':False}
p.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
