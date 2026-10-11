# 候选插件首次独立算量成果

## 状态与证据边界

两张原始 PNG 已由本轮模型实际打开查看，随后从 DXF 读取精确坐标、文字和标注，自行形成专业输入，实际运行插件原生对象审计、管网计算器、面层计算器及相应检查。以下是本轮首次独立结论。识别者为当前 ChatGPT 执行代理，无人工预填识别清单、无人工复核签认、无正式采用、无远端写入。

管网能力档位为 PROFESSIONAL_INFERENCE，输出限候选参考/规则推算/方案暂估，business_adoption_allowed=false；面层档位为 DETERMINISTIC_RECALC，输出限候选参考或局部试运行，business_adoption_allowed=false。两者均由模型准备源身份、对象、专业关系和复核位置；用户只需提供原图与范围，资料无法查明的决定性条件才需另问。本任务图内说明足够，无待补设计参数。

## 1. 服务区新设给水中心线

| DN | 水平中心线 m | 跨层中心线 m | 工作合计 m |
|---|---:|---:|---:|
| DN32 | 4.30 | 0.00 | 4.30 |
| DN50 | 8.30 | 4.70 | 13.00 |

同口径中心线总工作量 **17.30 m**。图号 P-01，Rev A；范围为新设 SUP 的 S-IN 至 U-OUT，含 F1/F2 的 R-A 层间连接。

| 对象 | 视图与计量部位 | DN | 长度 m | 原始实体 / 主要尺寸或标高 |
|---|---|---|---:|---|
| PIPE-A0 | F1 plan / S-IN to first plan elbow | DN50 | 3.60 | Model A0; dimension BA |
| PIPE-A1 | F1 plan / first plan elbow to R-A | DN50 | 2.40 | Model A1; dimension C0 |
| PIPE-A2 | F2 plan / R-A to RED-A centre | DN50 | 2.30 | Model A2; dimension C6 |
| PIPE-A3 | F2 plan / RED-A centre to upper plan elbow | DN32 | 2.40 | Model A3; dimension CC |
| PIPE-A4 | F2 plan / upper plan elbow to U-OUT | DN32 | 1.90 | Model A4; dimension D2 |
| PIPE-RA-V | F1/F2 plans + system diagram / One R-A riser, F1 PIPE CL to F2 PIPE CL | DN50 | 4.70 | A5/A7/DB 同 R-A; E0/E1 管中心标高 |

### 关键工程判断与回图依据

1. F1、F2 的图中 R-A 和系统图 R-A 是同一个物理立管：FA 明文说明，A5/A6、A7/A8、DB/DC 为位置与编号。F2 为便于同一三维网络计算，按两个 R-A 平面点之差平移对齐；原平面坐标完整保留。
2. 系统图 DE/DF 表明 R-A 是所标管中心之间的直竖段；E0 为 F1 PIPE CL +3.15 m，E1 为 F2 PIPE CL +7.85 m，因此真实竖长为 4.70 m。FFL +0.00/+4.60 只是楼面参照，未代替管中心。系统示意线 D7 的图上长度未用于算量。
3. B2/B3 的 RED-A 与系统图 DA/E4 对应同件；FE 说明 DN 在变径中心改变，F2 源 A2/A3 以 x=22000 mm 的该中心分界。规格由 AD/AE/AF/B0/B1 和系统 DD 逐段绑定，并被变径节点限制传播。
4. FD 规定中心线穿过在线管件连续计量、不加管件余量，因此不扣变径符号占位，也不另补符号长度。F1 A0 与现状排水 D3 的未标接点交叉不相连，D4/D5/FF 与图例提供依据。
5. 五条平面源线加一条来源标高推导的三维 R-A 输入，共六个计量对象；从 U-OUT 反查至 S-IN 路径连续。源平面长度分别与 BA、C0、C6、CC、D2 的标注一致；原生分项之和与全部所选几何长度一致。

逐项排除：

- Model D3：Existing DRN DN80; separate system and unmarked crossing is not a connection (D4,D5,FF).
- Model D6：NTS system view of F1 plan route; correspondence only (FC).
- Model D7：NTS system riser graphic; real vertical separately derived from E0,E1,DE,DF.
- Model D8：NTS system view before reducer; F2 source A2 owns quantity.
- Model D9：NTS system view after reducer; F2 sources A3,A4 own quantity.
- Model EC：Legend SUP example only (EB,101).
- Model EE：Legend DRN example only (EB,101).

架构参照、尺寸线、端口圆和立管圆均只作定位；圆周不计管长。计量端口以外、损耗、保温、支吊架和价格按 F8/100/101 排除。

### 原生拓扑诊断（不作为本次另计安装件工程量）

| 系统 | 候选类型 | DN组合 | 数量层级 | 候选数 |
|---|---|---|---|---:|
| SUP | 变径候选 | DN32×DN50 | 规则推算量 | 1 |
| SUP | 弯头候选 | DN32×DN32 | 规则推算量 | 1 |
| SUP | 弯头候选 | DN50×DN50 | 规则推算量 | 3 |

原生引擎的阀门候选数为 0，虚拟断口桥数为 0，断口增量为 0 m。这些是本输入的诊断；本次工程量出口仅为管长。图示 RED-A 已用于规格分界，三维路线转折所产生的弯头候选保留供复核，不追加设计或安装件清单。

本例的跨图对应和真实标高转换由模型通过来源绑定辅助脚本形成三维输入；原生引擎实际计算六条三维线段的几何长度和规格分组。其 cross_view_link_count=0、vertical_quantities=[] 表示未调用该引擎独立的跨图接口/竖段生成入口，并非未计算竖段；PIPE-RA-V 原生 segment 明细包含该量。

## 2. 工作室吊顶饰面净面积

| 材料 | 毛面积 m² | 洞口并集扣减 m² | 其他扣减/增加 m² | 净面积 m² |
|---|---:|---:|---:|---:|
| CL-11 | 18.04 | 0.63 | 0.00 / 0.00 | 17.41 |
| CL-24 | 9.84 | 0.00 | 0.00 / 0.00 | 9.84 |

同一吊顶饰面层的分材净面积合计 **27.25 m²**。CL-11 为吸声吊顶板；CL-24 为涂饰石膏板吊顶饰面。两区相邻且不重叠，按水平净完成面计量，不包含龙骨和基层实体。

### 表面与构造判断

- RCP-01 内边界由闭合多段线 9A 给出，x=1600–8400 mm、y=6100–10200 mm；实线材料分界 9B 位于 x=6000 mm。CL-11 标签 9C 在西区，CL-24 标签 9D 在东区；E7/E8 明确做法和分区语义。区宽由 C6=4400、CC=2400 校准，总宽 D2=6800、总高 D8=4100 再独立核查。
- SH-1 是穿透吊顶的服务井，9E 的闭合足迹 x=2400–3100、y=8300–9200 mm，与 A7=700、AD=900 一致。E9 要求扣实际足迹；本区仅有这一个真实开口，原生 rectangle_openings_net 在所属表面内裁切并求并集，扣除 0.63 m²。
- AP-1 是 500×700 mm 的平齐可拆检修板，AE、B1、B7、BD 定位。EA/EB 和 S-2 的 E3/E5 明确周边同材质且本次饰面面积保留，因此其 0.35 m² 已包含在 CL-24 中，不再另加，也不扣孔。
- P-1 为低矮屏风，BE/BF 是下方投影，C0 转引 S-2；E1 标屏风顶 +1.700，E0 标吊顶饰面 +2.800，E2/EC 明确吊顶连续。屏风不切断材料区，亦不扣屏风投影。
- S-2 仅用于判断构造，未当作附加表面。EE 排除了井壁收边、竖向回边、额外构造层、跌级及未示投影；EF/F0 排除骨架、设备点数、采购系数、搭接、损耗和合同孔洞阈值。
- 原生 HATCH 面域取证得到两块毛面，与固体完成边界分区相符。HATCH 本身没有扣井孔，因此不能直接把填充面积作为净量；孔洞语义另由说明判定。两个源边框架在原生计算及绑定复算中均为 bound_consistent。

## 错误、修订与覆盖限制

- 两次中性提取均返回 PARTIAL / exit 3。原因是未实例化默认块定义 _CLOSEDFILLED 中的 SOLID（CAD-7B）未被通用几何解析器支持。Model 无 INSERT，所选管道和吊顶计量实体均可读，因此该定义不影响本次数量。告警原文件完整保留，未删除或改标为全图解析 PASS。
- 面层中性取证另列两项 HATCH 专业适配待办。第一次面域命令误把图层筛选填写为逗号列表 FILL-A,FILL-B；工具按正则使用，返回 0 regions，无报错。该零提取结果没有被当作零工程量。查看接口说明后改为 FILL-[AB]，另存新文件，得到 2 regions / 0 failures，第一次命令与空结果保留。
- 初步识别说明保存后扩展为原生模型工作识别记录契约，实际调用 load_working_entries 验证身份/证据；最终管网输入绑定该通过的记录与原生对象审计。初稿仍保留，不冒称人工裁决。
- 截至首次交付，范围内无未形成数字的管段或表面、无待补图纸条件。正式采用与人工复核均未发生。通用规则成熟度仍按技能声明，数值验证不代表工程验收或自动识图准确率。

## 主要产物

- `candidate.json`：两项分项、来源哈希、原生结果路径及 JSON Pointer。
- `pipe/native_input.json`、`pipe/native_result.json`：实际管网输入/原生结果。
- `pipe/object_audit_input.json`、`pipe/object_audit_result.json`、`pipe/working_recognition_ledger_v1.json`：审计和模型工作证据。
- `surfaces/native_manifest.json`、`surfaces/native_result.json`：实际面层输入/原生结果。
- `pipe/source_bound_details.json`、`surfaces/source_bound_geometry.json`、两项 `quantity_details.csv`：逐项源定位与公式。
- 两项 `review_overlay.png`、`review_view.json`、`review.html`：编号回图及离线点选复核（无上传功能）。
- `logs/*.json`、`*.stdout.txt`、`*.stderr.txt`：实际命令、UTC 开始/结束、退出码和完整日志。
- `freeze.json`：真实封存时间、插件 commit 及全部本轮产物哈希；冻结后不改变本次候选。

重现顺序见日志 argv：提取 → 面域适配 → prepare_inputs.py → 原生对象审计 → finalize_pipe_input.py → 两个原生计算器 → 复算和交付校验 → 回图复核 → 冻结。辅助脚本只处理本图明确来源的选择、坐标、字段映射与交付，不修改插件或原图。

## 实际验证与回图记录

管网公共最小成果校验 PASS；专业校验 PASS_WITH_LIMITATIONS，唯一警告为候选工作量尚非已核准正式量；用户汇总校验 PASS。面层公共最小成果校验 PASS、来源绑定复算 PASS。原生共享 HTML 生成检查 PASS，无历史批注和回执，未进行浏览器交互验收。

计算后本轮模型实际打开并查看了两张 review_overlay.png，逐项核对范围、规格分界、跨图对应及扣减/保留，未发现需要修订工程量的事项。return_review.json 记录实际查看对象、声明边界与候选值到原生 JSON Pointer 的一致性检查。
