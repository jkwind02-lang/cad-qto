# CADINST 精确选择与 HATCH 工作面积

## 本轮能力及边界

新增入口 `skills/qto-surfaces-envelope/scripts/select_surface_regions.py`，将真实识图工作台的实例 ID 映射回 DXF HATCH，复用现有 HATCH 几何计算。支持模型空间、指定布局、嵌套 INSERT 与 MINSERT 单元的实例变换。既有 `extract_surface_regions.py` 及其图层正则参数未改动。

输出是所选填充的逐实例几何面积和包围框，不是完整装修工程量。它不判断填充是否属于某项做法，不自动去重重叠实例、不汇总构造层、不代替洞口扣减、合同口径或人工识图复核。仅选择正确对象仍不证明项目范围完整。

## 准备来源和索引

在仓库根目录运行；Python 依赖沿用仓库安装说明。先准备 source.json：

```json
{"path": "/实际绝对路径/drawing.dxf", "sha256": "该文件实际SHA-256"}
```

上例是格式示意，必须替换路径并对实际文件字节计算哈希。若来源是由 DWG 转换的 DXF，还应按照识图工作台的来源协议保留原始权威文件与转换证据；当前接口不认证转换完整性。

```sh
python skills/_qto-shared-evidence/scripts/qto_recognition_workbench.py index --source source.json --layout Model --output index.json
```

索引、选择文件和计算使用同一源文件及布局。CADINST ID 从真实索引读取，禁止凭空拼接；索引哈希、源文件身份和布局必须一致，源文件变更后重新生成索引及选择。

## 明确选择

selection.json 按实例选择：

```json
{
  "source_sha256": "与索引一致的实际SHA-256",
  "layout": "Model",
  "cadinst_ids": ["从当前索引复制的HATCH实例ID"]
}
```

也可将 cadinst_ids 替换为 `"layer_names": ["装修填充", "吊顶填充"]`。图层采用索引的 effective_layer 精确字面匹配，区分大小写，不是正则，也不做名称推断。任一请求层没有 HATCH 都报错，包括“部分层命中、部分层不存在”。

同时提供 cadinst_ids 和 layer_names 时取交集。任一实例 ID 无效、非 HATCH、重复，或字段为空、为 null、最终交集为空，均拒绝；不能把失败写成零数量。

## 执行

```sh
python skills/qto-surfaces-envelope/scripts/select_surface_regions.py --dxf drawing.dxf --index index.json --selection-file selection.json --unit mm --unit-evidence "本次图纸总说明的单位依据"
```

- --unit 仅接受 mm 或 m；输出面积为平方米，包围框为米。
- INSUNITS=4 必须声明 mm，INSUNITS=6 必须声明 m。
- INSUNITS=0 必须提供非空文字依据；程序只保留依据，不以关键词判断单位，也不验证文字是否真实适用于本图，须由识图者核实。
- 其他 INSUNITS 拒绝，不能仅用声明覆盖。
- 来源身份异常、索引失效、计算期间文件改变，以及带未核验外参/动态块提示的祖先 INSERT，均阻断正常数量输出。
- 成功：退出码 0，stdout 输出一个 JSON。
- 计算/输入失败：退出码 2，stderr 输出错误，stdout 不输出数量；命令参数错误由 argparse 报告。

## 输出及追溯

结果包含 source_sha256、index_sha256、layout、unit、unit_evidence、source_read_diagnostics、source_identity_issues、conversion_warnings，以及 regions 中各实例的 cadinst_id、region_id、area_m2、bounds_m、polygons 和 status。region_id 保持与 CADINST 相同，polygons 复用现有 geometry_record 序列化，status=geometry_extracted 仅表示几何提取成功，不表示工程语义已核准。

`formal_adoption_allowed=false`、`adoption_scope="working"` 明确限定为工作计算结果。读取/转换警告不能被忽略；程序成功并不证明原始 DWG 中外参、动态可见状态或整个专业范围均已完整取得。正式采用仍须原图证据、业务口径和专业复核闭合。

## 回归与验收边界

新增自动回归涵盖真实索引到 HATCH 的映射、布局绑定、毫米换算、旋转/非均匀缩放/嵌套块的面积与坐标、MINSERT 单元、源文件变更、外参祖先拒绝、单位冲突、字面图层和 CLI 失败不出量。

这些是合成 DXF 和接口回归，不是实际项目验收，也不能据此宣称实际算量准确率提升。当前改动位于 PR #5；CI 应以最终提交为准。未合并、未发布或未更新用户本机时，不应将分支能力表述为已经安装。

本轮代码与测试片段由项目内 Pro Chat 会话生成，逐段审查后提交，并以 GitHub CI 验证；未切换 ChatGPT Work 模式。

## 2026-10-12 接续：完整面域交接与专业传值回归

选择器现在保留完整多边形（外边界及内孔），可将已由模型明确属于同一物理视图、同一做法组的 regions 传入原有 `EXTRACT.measure_region_group(regions)`。分组必须先由识图者明确，程序不会按坐标或图层自动把不同楼层、不同做法合并。该函数保留实例列表、并集前面积、重叠扣除、净面积与多边形。

新增回归实际执行：保存合成 DXF → 真实工作台索引 → 选择两份重叠 HATCH（排除第三个未选实例）→ 原生面域并集 → 并集 JSON 证据及来源哈希 → 原专业计算器的 area_direct 输入 → 专业成果检查及绑定复算 CLI。构造值为原和 200、重叠 50、净面积 150 平方米，结果保持 reference；篡改并集证据文件后，计算及绑定复算均应拒绝。

这证明本次程序调用链真实传递了并集面积，不证明 `area_direct` 会自动把手填标量与 CAD 几何比对。当前 `area_direct` 没有新增 JSON Pointer 数值绑定；其输入应由上述实际计算结果生成，不能以登记了证据哈希就宣称数量天然一致。测试也未包含 AI 对图例、材料做法或现场图纸的独立判断。

真实项目原图尚未取得，原图独立识图、分组、洞口判断和回图验收继续待验证，不以这项合成回归替代。
