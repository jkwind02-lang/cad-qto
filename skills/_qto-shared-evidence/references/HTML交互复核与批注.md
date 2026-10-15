# 全专业HTML图面复核与批注

适用于13个专业的原图算量、对象复核和跨专业成果交付。原图算量有可定位对象时，默认随明细交付单文件HTML：逐对象点选回图、同类问题分组、填写结论及原因、例外取消勾选、保存批注、处理回执。用户指定纯表格/其他形态时遵从用户。任何情况下都不建立虚构图页：没有图面依据但有要用户拍板的事项、或要与第三方数量对照时，用 1.1 视图（可以没有图页），见第 6 节；1.0 视图仍要求至少一张真实图页。

使用同一份共享界面，不在每个项目重新写一套前端。界面不计算专业工程量、不把用户意见直接改成数字，也不替代所属专业的识图和重算。其余专业公式与成熟度保持原定义。

顶部默认只保留一行标题、批注数量、保存按钮和“说明与更多”。项目说明、分项汇总、范围限制及导入导出放在默认收起的面板中，展开时覆盖在图面上方，不压缩图面及填写区高度；窄屏允许分行。暂存失败、导入失败等操作提示在顶部直接显示，不随说明折叠。

## 1. 各专业提供什么

专业先完成工作识别、测量及分层结果，再准备`qto-review-view/1.0`数据。至少保留项目和本次复核范围、来源文件身份、图页坐标、稳定对象编号、数量单位/层级、依据、状态和问题组。

| 专业Skill | 适合回图的对象 | 数量及属性示例 |
|---|---|---|
| qto-pipe-networks | 管线、阀件、接口及断口 | DN、材质、m、个；水平/竖向分列 |
| qto-duct-air-systems | 风管路径及截面、风阀 | 长度m、展开面积m²、规格、保温 |
| qto-electrical-routes | 回路、桥架及线缆路径 | m、根数、回路/电缆规格 |
| qto-components-points | 构件及设备点位 | 个/台/樘/套；类型和供装范围独立 |
| qto-concrete-formwork | 构件轮廓、剖面与相交区 | 混凝土m³与模板m²分列 |
| qto-rebar | 构件/钢筋详图与料表定位 | 直径、形状、根数、m、kg |
| qto-structural-steel | 构件、节点及截面 | 件、长度、重量、防火/涂装面积 |
| qto-masonry-secondary | 墙段、洞口、二次构件 | m³、m²及洞口扣减依据 |
| qto-surfaces-envelope | 房间面域、立面及构造层 | m²；毛面积、洞口和侧壁分别列示 |
| qto-framing-supports | 龙骨或支承构件路径 | m、件、kg及构件规格 |
| qto-earthwork-terrain | 挖填边界、断面、标高点 | m³、标高、土体状态和计算口径 |
| qto-piles-groundwork | 桩位、桩段、处理区域 | 根、m、m³及桩径/长度 |
| qto-roads-siteworks | 道路、场坪面域及路缘线 | m²、m³、m及结构层 |

协调器按对象所属Skill合并视图和问题索引；不重复计量。即使单位相同，构造层、供装范围或计量口径不同也不自动相加。HTML顶部只显示专业已提供的分项汇总，不按选中对象临时拼一个混合合计。

## 2. 输入格式

使用项目工作目录保存视图JSON，专业数据不要写进共享插件。

~~~json
{
  "schema": "qto-review-view/1.0",
  "project_id": "本项目业务名称",
  "review_id": "本次楼层及计算范围",
  "title": "工程量复核",
  "revision": "本轮版本",
  "sources": [{"id":"DWG-1","title":"本轮原图","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
  "scenes": [{"id":"PLAN","title":"平面图","source_id":"DWG-1","coordinate_unit":"mm","y_axis":"up","bounds":[0,0,10000,8000],"background":[]}],
  "objects": [{"id":"P-001","catalog_key":"管线|P-001","professional":"qto-pipe-networks","scene_id":"PLAN","kind":"line","points":[[500,500],[3500,500]],"title":"支管P-001","category":"支路复核","status":"规则推算","detail":"按本支路及图注定位","source":"DWG-1 / 源对象定位","quantities":[{"name":"水平中心线","value":3.0,"unit":"m","level":"规则推算量"}],"attributes":{"规格":"按本项目图注"}}],
  "groups": [{"id":"G-1","title":"同类支路","category":"支路复核","status":"待复核","proposal":"核对本组连接判断，各自规格保留","reason":"同一图形及连接规则，例外可取消勾选","member_keys":["管线|P-001"]}],
  "receipts": {},
  "summary": [{"label":"水平中心线","value":3.0,"unit":"m","scope":"当前已识别支路"}],
  "scope_notes": ["尚未覆盖的安装内容及本轮采用条件"]
}
~~~

示例仅说明字段，实际来源SHA和数量必须由本项目提供。

- `project_id`与`review_id`使用稳定业务名称，不能按每轮时间随机生成，否则旧批注无法对应。`revision`表示本轮成果版本。
- `source_sha256`由生成器按项目、复核范围和排序后的来源文件SHA计算。换原图/项目/范围后不能沿用旧身份。多个DWG/PDF可以分别成为`sources`，图页通过`source_id`引用。
- 图页`coordinate_unit`与数量`unit`独立；CAD可以用mm，PDF用px。`y_axis`为`up`或`down`，不能混用未校准坐标。三维对象提供可定位平面/剖面/详图投影，原三维数据保留在属性中，不把三维量按二维投影重算。
- 对象`kind`支持`point`、`line`、`polygon`及`unlocated`。面域可带`holes`孔洞环。没有可靠定位时使用`unlocated`并说明原因，不生成假坐标，也不隐藏该项数量或未决。
- 图页`background`为`line/polygon`的`points`或`text`的`point/text/rotation/height`。可用`raster_path`嵌入原图PNG/JPEG；生成时转成内嵌图片，不依赖旁边文件、临时服务或外网。已有SVG用可信几何或原图栅格，不接受可执行外部SVG。
- 多图页共用底图时，放入顶层`background_sets`，图页以`background_refs`引用，避免重复嵌入；窗口只绘制当前可见范围。可用`default_category`指定初始待补资料类别，已解决对象仍在其他类别保留。
- `catalog_key`唯一对应复核记录；同一实物可以有不同问题记录，`id`应保持同一物理身份。跨专业应有不冲突的稳定编号。对象模式按专业、类别和物理ID去重显示，原记录与分组关联完整保留。
- `quantities`可列多个不同单位；未知数值为`null`，不得补0。`attributes`保存专业规格、公式、原始值及本轮采用依据。图面几何的缩放不会改变数量。
- `member_keys`确定整组的实际成员；生成器保留各成员数量、单位和属性快照。同一意见可确认判断方法，不能把不同规格对象统一改成某个数值。

## 3. 生成与保存

命令中的共享脚本位置按当前插件Skill根目录解析：

~~~text
python ../_qto-shared-evidence/scripts/build_qto_review_html.py --input 本轮复核视图.json --output 工程量图面复核_本轮.html --report HTML生成检查.json
~~~

已有批注时加`--feedback 用户保存的带批注HTML或JSON`。输出文件已经存在时生成器拒绝覆盖；使用新的业务版本名。大数据gzip内嵌并分行，列表和组成员分页，不为压缩丢明细。超过2MB/超长行给出性能复核提醒；按实际交付范围执行静态、保存恢复和必要的视觉检查。浏览器访问被策略拒绝时不换入口绕过，也不冒称原生浏览器验收。

**在线上传目前停用（2026-10-15 用户决定）**：`assets/review/feedback-endpoints.json`的`enabled`为`false`，生成的复核页只有本地保存，打开和保存都不联网，文件大小不设上限；带批注HTML由使用者手工转交维护者。收件地址和服务保留不动：恢复时把`enabled`改回`true`并发布新版，或生成时用下文的`--feedback-url`等参数显式指定。本节以下关于上传的说明，只在上传启用时适用。

共用生成器读取`assets/review/feedback-endpoints.json`中的维护者收件配置：腾讯云主站，Cloudflare备用。13个专业共用同一入口，勿在专业脚本中复制地址或秘密。生成时可用`--feedback-url`、`--feedback-provider tencent`及`--feedback-backup-url`显式覆盖；`--local-only`生成纯本地保存入口。尚未配置或`enabled`为`false`的安装版保持本地保存行为。打开HTML不联网，用户确认发送整份文件后即可上传，无需填写反馈码。

“保存并反馈”先请求浏览器保存完整带批注HTML，再上传完全相同的文件。内嵌图面、对象明细、项目身份、结论、原因、历史与草稿完整保留，服务器回执核对SHA-256及字节数。腾讯云正常收件时不发送Cloudflare；网络/服务故障先查询主站，仍无法确认时按同一提交编号发送备用站，回执显示实际位置与主站待核对状态。请求被拒绝、文件校验错误、超限、冲突或限流不触发备用。备用站必须配置为`backup`，不独立创建GitHub问题，避免回执丢失导致双重建单。

未内嵌的原始DWG及外部附件不会自动带上。仅保存到本机始终可用，不设大小上限；启用上传时单份上传上限20MiB，超限只保留本地文件，不截断图面。实际浏览器保存位置和大文件性能仍按交付范围核验。既有HTML不会被插件升级自动改写；重新生成时保留`--feedback`原意见。

上传是插件问题收集，不是当前项目已复算、已采用或规则已发布。维护人员取得完整HTML后按原项目上下文复核；其中草稿仍不是已确认结论。服务器只有在完整文件入库后才返回收件，收件与转交分别报告；同一页面打开期间未修改批注的失败重试使用同一份文件和编号。上传不要求反馈码，也不在HTML中内置上传密钥；代码仓库和接收服务维护源不得保存账户秘密。下载完整项目HTML需要独立维护者取件权限，公开代码仓库不开放工程资料。上传前须说明整份HTML含项目资料，由使用者确认发送范围。

用户操作统一为：选专业/问题组→点选或上一处/下一处回图→取消特殊成员→填结论与原因→保存批注→交回带批注HTML或JSON一份。提供宽填写区和拖动分隔线。草稿自动暂存但不当作已保存意见，修改历史不静默截断。新增标记保留所属专业、图页和坐标。跨专业/多单位组只共享判断意见，数量仍按每个成员独立保留。

## 4. 收到批注后的闭环

~~~text
python ../_qto-shared-evidence/scripts/read_qto_review_feedback.py --view 本轮复核视图.json --feedback 用户保存的HTML或JSON --output 收到批注交接_本轮.json
~~~

读取程序只解析数据，不执行HTML。它检查项目/来源身份、成员快照、选择与例外，保留所有原话和草稿，并输出供专业处理的对象清单；不会自动修改任何数量。旧成员或历史组已变化时先核对适用范围，不能静默把旧批注套到新对象。

由对象所属专业区分具体结果、确认当前结果、方法性意见、范围排除、暂缓及缺资料。先对照原图落实可判断部分，再重算受影响明细、汇总及跨专业交接；不能把“请核对”“按末端接口”当成用户已经确认了具体数值。

重新生成页面时把`receipts`写入对应批注键，例如`问题组|G-1`：

~~~json
{
  "status":"已应用并复算",
  "assistant_response":"本组2处按实际构造调整，各自规格保留",
  "source_annotation_updated_at":"与所处理的已存批注时间完全一致",
  "object_ids":["对象1","对象2"],
  "member_results":[{"object_id":"对象1","result":"当前采用结果","reason":"采用依据"}]
}
~~~

用户更新批注后，旧时间的回执显示“新意见仍待应用”，不继续冒充已处理。当前实际采用结果可放对象`adoption_result/adoption_reason/adoption_note`；它与用户原话分栏显示。

完成的问题更新`category/status`移出待补资料列表；明确暂缓或排除的转入相应类别，原对象和原批注保留。只有资料确缺、无法消解的设计冲突及真实范围选择继续作为待补。可推广的方法进入独立规则评估，不能因为勾选“供通用规则评估”就自动修改共享公式或直接采用项目参数。

## 5. 旧版CAD专用页兼容

先显式迁移旧`分组回图数据.json`，保留全部原对象字段与原批注：

~~~text
python ../_qto-shared-evidence/scripts/migrate_cad_review_view.py --input 旧复核视图.json --output 统一复核视图.json --project-id 本项目 --review-id 本次复核范围 --title 本项目图面复核
~~~

再用生成器及`--feedback`导入原1.0/1.1批注。旧格式必须绑定同一单来源SHA，不能跨项目默认接受；旧双视图及新增标记有显式场景映射。迁移只转换展示接口，不重分类、不计算新的专业数量。新项目直接使用通用格式，不用旧专用适配器起算。

运行实现：[生成器](../scripts/build_qto_review_html.py)、[数据契约](../scripts/qto_review_contract.py)、[批注读取](../scripts/read_qto_review_feedback.py)、[旧版视图适配器](../scripts/migrate_cad_review_view.py)。

## 6. 成果、待解决问题与对照审核（qto-review-view/1.1，可选扩展）

复核页就是最终成果页：显示本次算出的全部总量，按楼栋、分部、品类分类，能查明细组成并溯源到最初依据；重点是成果中没有依据或无法判断的内容，按问题性质分组，用共性问题答一类、解决一片；第三方数量只作对比，偏差按本次依据是否充分归因，清单的问题按类型整组答复。1.1 视图在 1.0 全部字段之外加可选的 `comparison` 节：条目（本次各层级量、明细行含已定不计、成员，可带第三方来源段）、个别问题和共性问题（性质、做法及影响量）。页面默认打开“成果”，另有“待解决问题”“对照审核”“图面复核”；可以没有图页，有图页时成员回图高亮。批注写 `qto-review-feedback/2.1`：共性问题答复对整个适用范围生效，个别处只写例外；读取时多出 `common_issue_actions`、`item_actions`、`issue_actions`、`conflicts`、`overlaps`。不带 `comparison` 的视图，页面与原来完全一致。

字段、表格输入脚本和检查规则见 [复核页：成果、待解决问题与对照审核](复核页待解决与对照审核.md)；运行实现另有 [表格输入](../scripts/build_comparison_view_from_tables.py)、[对照校验](../scripts/qto_review_comparison.py)。

## 2026-09-20 成果快照绑定（优先于旧版回执说明）

来源身份继续使用项目、复核范围和图纸哈希；另由规范化器重算对象/问题组 result_fingerprint。批注及成员快照须绑定该值。数量、规格、几何、采用依据变化时旧意见标为 stale，保留在历史中而不进入当前可执行 actions；缺失对象标为 unmatched。组成员逐一核对 object_id，不能仅比较 catalog_key。旧版无绑定批注不自动升级。页面标题或单纯revision标签变化不使工程判断失效。方法意见的理由可参考，旧数字确认不能继承。回执同时绑定 source_annotation_updated_at 与 source_result_fingerprint；两者任一不符都显示过期。
