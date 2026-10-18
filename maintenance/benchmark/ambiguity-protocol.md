# 电气歧义组与独立查漏试点评估协议

本协议属于维护工具，不进运行包。`scripts/evaluate_ambiguities.py` 新增显式全集分母、关系遗漏、额外输出、同口径数量误差及配对重复报告；既有阶段一 `score_benchmark.py` 与其冻结工具链保持原字节。旧评分器交集内采用/分类指标可能为100%而整体漏项FAIL；额外对象独立列在差异中，不能把这些百分比解释为全集准确率，也不据新增协议反推旧历史评分器违反其冻结契约。

## 三类文件及隔离

- 公开 `qto-ambiguity-input/1.0`：`benchmark_id`、`sources`、带SHA256的相对路径 `assets`、`groups` 和 `coverage_regions`。每组含 `group_id/source_id/split/isolation_keys/targets`；每个target含 `target_id/kind/question/answer_format/locator_handles`。`kind`取 `OBJECT/RELATION/JUDGMENT`。`isolation_keys` 显式列 `floor/frame/block_family` 数组；同源、同隔离键不能跨DEBUG/SEALED。无块族判断的线/文字题可用空数组，但须说明不验证跨样本块识别泛化，不能虚造独立块族。
- 维护区独立保存 `qto-ambiguity-reference/1.0`：绑定公开清单的文件SHA256；`authority.kind/decision_subject/human_approved` 如实标记判定主体。本批只支持 `INDEPENDENT_MODEL_FROM_SOURCE`、`human_approved=false`、`approval_evidence=null`。尚未实现与真实人工核准凭据系统的核验接口，因此保守拒绝 `HUMAN_REVIEWED_FROM_SOURCE`，即使提供字符串或自称凭据的对象也不接受；模型参照不得附带人工核准字段伪造权威。每个target有 `decision_status`（`DECIDABLE/INSUFFICIENT/ADJUDICATION_REQUIRED`）、`acceptable_conclusions`、`evidence_refs`、`basis`、`quantity`。可接受结论使用明确JSON值；不同合法表达须事前列入，不能看结果后选择性放宽。查漏参照含 `expected_objects/expected_relations/census_basis`；未形成全集时写null。
- 每次独立运行产生 `qto-ambiguity-run/1.0`：`input_sha256/run_id/version_label/pair_id/replicate/model_config/budget/human_review_minutes/claims/coverage_regions`。`model_config` 至少显式列模型和推理档 `model/effort`；`budget` 为相同的正数限制字典，不把实际消耗当限制。`claims` 每项含 `target_id/status/conclusion/evidence_refs/quantity`；未决 `status=UNRESOLVED` 时结论与数量必须null并给 `reason`。证据引用格式 `source_id:handle`，须能回到活动实例及关系，不以邻近文字代替连接证据。

参照、批注、结论及其叠加提示不得进入公开输入、运行包和共享记忆。公开目录采用资产白名单与逐文件SHA256，拒绝未登记文件、越界路径、符号链接及已知答案字段；JSON/GeoJSON以及逐行JSONL/NDJSON执行相同的递归字段检查，逐行格式错误时拒绝审计。运行包拒绝公开索引、每个已登记原图/文字/图片等资产及私有参照的原字节拷贝，以SHA256核对，改名或移动目录不能绕过；`--split-peer` 同样检查其他公开集合资产。运行包自身的上述结构化格式也检查已知私有字段，且不依赖是否传入私有参照。该审计能证明声明格式/路径/原字节的隔离，不能自动证明任意自然语言备注、图片或重新编码的内容没有暗示答案；封存前仍须独立检查提示内容。图像只能来自原图未加答案的显示；DXF渲染的字体、代理、外参等影响必须声明，原图身份不由局部截图代替。

## 输出与评分语义

公开targets是固定全集，遗漏仍留在分母；报告正确确定、错误确定、合理未决、可判却推迟、无证据未决、对象/关系/判断遗漏、额外对象。确定结论须匹配事前允许JSON值并引用参照相关原图证据；证据不足题不鼓励猜测。合理未决须null、理由和原图定位，不能写零。每个已登记查漏区另报对象与关系全集、遗漏及额外输出；未查全、参照全集未知或额外区域不能给PASS。歧义点全部正确也不抵消查漏失败。

`quantity` 仅在参照已有同单位、同数量层级与同起终范围的 `basis` 时计算绝对误差；不同单位/口径为INCOMPARABLE，缺值为null，不从单位相同自动推定可比。原图Unitless的实体原单位几何不等于安装工程量；不能以图面总长接近宣称语义改善。未知参照的额外确定数量为UNSUPPORTED_QUANTITY。

运行 `coverage_regions` 每项为 `{region_id,scanned_all_scope,object_ids,relation_ids}`。`object_ids/relation_ids` 命名规则由公开问题指定，参照全集不放公开清单。完整配电系统图查漏可明确统计“图示出线回路行”，须含备用行，但不得据此认定备用线缆已安装或设备实物已计数。工程交付的分区全图覆盖仍需另外执行，局部试点不替代正式验收。

配对比较要求同 `pair_id/replicate/input_sha256/model_config/budget`；基线与候选版本身份分别稳定且不同，所有run_id唯一。重复运行不增加歧义组或源图上下文数；只跑一次时波动为null。每侧至少三次才有“三次观察”的标识，但并不保证统计充分性。报告确定正确/未决等计数范围、全集通过率均值与波动、同口径数量波动、人审时间均值和实际记录次数。未人工计时必须null，脚本执行时间不能冒称人审耗时。

## 命令

```text
python maintenance/benchmark/scripts/evaluate_ambiguities.py audit --input-manifest <封存公开目录/任务索引.json> --split-peer <调试公开目录/任务索引.json> --reference <维护私有参照.json> --runtime-root <构建运行包> --output <维护区/隔离回执.json>
python maintenance/benchmark/scripts/evaluate_ambiguities.py score --input-manifest <公开目录/任务索引.json> --reference <维护私有参照.json> --run <独立运行.json> --output <维护区/评分.json>
python maintenance/benchmark/scripts/evaluate_ambiguities.py compare --input-manifest <公开目录/任务索引.json> --reference <维护私有参照.json> --baseline <基线1.json> <基线2.json> <基线3.json> --candidate <候选1.json> <候选2.json> <候选3.json> --output <维护区/配对报告.json>
```

PASS仅对应声明参照与试点范围。报告保留authority并固定 `formal_engineering_acceptance=false`；独立模型参照不是人工核准量。FAIL或INVALID退出2，审计PASS/评分PASS/完成配对比较退出0；“比较完成”不代表候选改善。公共回归仅用去标识化反例及合法对照，不将真实原图/参照入Git。
