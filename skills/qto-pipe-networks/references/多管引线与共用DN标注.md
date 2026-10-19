# 多管引线与共用DN标注

本能力属于水管管网（含暖通水），不处理风管、设备数量或项目自己的管径设计/传播规则。仍调用 `build_pipe_inference_input.py` / `build_pipe_inference_inputs_batch.py`，将候选交给现有 `infer_pipe_network.py`；不另建传播引擎。

## 共用文字和批量seed

旧 `segment_bindings: {"CAD-T": "SEG-CAD-P1"}` 继续支持。经图面核对的同一DN共用于多个管段时，值可为不重复的非空列表：`{"CAD-T": ["SEG-CAD-P1", "SEG-CAD-P2"]}`。各目标须已通过本轮对象审计，空间和中性来源哈希一致，默认同系统，并在该文字规则的模型识别对象范围内。目标重复、不存在、无逐目标依据的跨系统、跨空间/来源、文字未消费或同一目标收到不同显式DN时阻断；不能为消除冲突强行覆盖。

模型seed须绑定当前来源和有效识别记录（含文字、所有目标管段及引线源ID）；人工与模型来源分别保留。旧单段字符串不要求改成列表。seed没有显式映射或完整引线栈时拒绝，距离/方向/第二候选差值不赋予seed资格。未绑定的文字仍保留scenario/excluded，所有检测候选留在 `dn_match_candidates` 并进入敏感性方案。

批量 `kind: text` 族可提供 `disposition: seed、decision: include、basis、evidence_ids、evidence_level: inferred` 和显式绑定。它实际生成模型 `working/unreviewed` 识别记录，没有人工确认字段。跨多个框的映射用 `segment_bindings_by_frame: {"F1": {…}, "F2": {…}}`；引线栈同理用 `callout_stacks_by_frame`。不要同时给全局和按框字段；按框缺少绑定的文字仍为scenario。未在本轮选框适用的内容不代表已验证。

旧 `--neutral-stream` 入口继续支持单段字符串绑定：配置/台账绑定中性流字节哈希，流内被绑定对象仍须有同一来源声明。可用 `expected_entity_source_sha256` 额外冻结该声明；这种入口未现场复核权威原图，不能宣称CAD清单入口的原图核验已完成。

## 两个不同系统共用同一DN

原图明确湿式/雨淋或其他独立管路共用同一文字时，`shared_annotation_targets[文字ID]` 必须逐目标列 `{segment_id, system, space_id, source_sha256, basis, evidence_ids}`，目标集合与 `segment_bindings` 完全相同；每个证据ID属于当前规则绑定识别记录。工具核对目标自身系统、同一空间/来源及依据，不从近邻推测跨系统共用。每目标在自身系统获得直接文字种子，沿各自系统传播；两条系统绝不因此相连。按框映射可用 `shared_annotation_targets_by_frame`。

DN栈中不同系统各有自己的标注时，栈提供 `segment_contexts`（同一逐目标结构，完整覆盖 `segment_ids`）。顺序绑定后每条文字只给对应目标；没有逐目标依据时仍只支持同系统栈。

## 多管DN栈

在文字规则 `callout_stacks[]` 中给出以下对象；所有坐标/引线来自中性取证，不手填替代端点。示例为匿名输入：

```json
{
  "stack_id": "STACK-1",
  "source_sha256": "当前权威原图SHA256",
  "unit": "m",
  "intersection_tolerance": 0.0001,
  "tolerance_basis": "本图局部线端与文字行距核对依据",
  "text_ids": ["CAD-T1", "CAD-T2"],
  "text_order_axis": [0, -1],
  "leader_path": [{"record_id": "CAD-L", "direction": "forward"}],
  "segment_ids": ["SEG-CAD-P1", "SEG-CAD-P2"]
}
```

`text_order_axis` 是模型依据本图标注习惯明确的WCS文字排序方向，按投影递增排序；不是脚本猜测的默认上下。`leader_path` 按遍历顺序列完整引线，各实体方向显式为 `forward/reverse`；`reverse` 同时反转实体内部所有折点顺序。引线支持LINE、直线部件的LWPOLYLINE/POLYLINE和直线LEADER（含中性块实例变换），完整多折点保持；跨多个源实体时必须连续。直接复用共享 `qto_segment_graph.row_parts` 取证几何。

每根所选管段与完整引线仅能有一个唯一交点，按引线累计长度排序，依次配对文字栈。折点处同一物理交点合并；同管被多次穿越、多个管段同站位、标注次序不唯一、共线重叠、断裂、标注数与交点数不等均拒绝。旋转图面通过明确WCS排序轴处理；弧形引线、MLEADER、曲线管段交点和非图面表达暂不支持，不以弦/近邻冒充。输入须列完整待绑定管段范围，模型仍负责判断图上是否遗漏管线。

输出 `callout_stack_evidence` 保留完整路径、逐管交点、部件序号、里程、容差与来源；各DN候选进原有 `dn_match_candidates`，确认/推算/方案和未复核身份仍由现有契约表达。自动顺序一致不等于正式采用。
