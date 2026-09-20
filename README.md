# CAD 算量插件

按人工算量思路执行识图、测量、计算和回图复核的 Codex 插件，包含 **13 个专业 Skill、1 个协调入口和共享取证资源**。

当前版本 **`0.2.0+codex.20260920`**。模型负责依据本次图纸识别对象与工程关系，程序负责提取事实、确定性计算和已编码约束检查。安装或合成测试通过，不代表所有专业已达到原图自动识别成熟状态，也不替代具体项目计量依据及专业复核。

**主分支 `main` 已统一当前各工作分支的有效修改。** 获取最新版直接下载或更新主分支即可，包含人工算量路径修复、全专业完整HTML反馈、无需反馈码上传，以及腾讯云HTML文件名恢复。原工作分支保留用于追溯；各分支核对及整合方式见[主分支整合记录](docs/主分支整合记录_20260920.md)。

## 本轮改进与复核入口

[人工算量计算路径复核](docs/人工算量计算路径复核_20260920.md) · [计算路径改进记录](docs/计算路径改进记录_20260920.md) · [运行输入与重算说明](skills/_qto-shared-evidence/references/按人工算量顺序执行与重算.md)

本轮将有依据的管网分段、图页隔离与跨层接口、空间近邻索引、明确算式依赖重算，以及电气通道/回路分离的路径台账接入实际运行。保留原专业划分，不新增一套学习入口；模型仍承担识图，局部未知不冒充零量或确认量。

前一轮几何、块变换与批注版本修复见[第一轮修复记录](docs/修复记录_20260920.md)。该记录中的当时待办，以本轮改进记录说明的当前状态为准。

## 覆盖专业

| 领域 | Skill |
|---|---|
| 给排水、消防水、暖通水及工艺管道 | `qto-pipe-networks` |
| 风管和风系统 | `qto-duct-air-systems` |
| 电气线路、桥架、电线电缆 | `qto-electrical-routes` |
| 构件、设备和末端点位 | `qto-components-points` |
| 混凝土与模板 | `qto-concrete-formwork` |
| 钢筋 | `qto-rebar` |
| 主体钢结构 | `qto-structural-steel` |
| 砌体和二次结构 | `qto-masonry-secondary` |
| 饰面、防水保温和围护面板 | `qto-surfaces-envelope` |
| 龙骨及装饰支承骨架 | `qto-framing-supports` |
| 土方和场地地形 | `qto-earthwork-terrain` |
| 桩基及地基处理 | `qto-piles-groundwork` |
| 道路和室外场地 | `qto-roads-siteworks` |
| 多专业协调与对象归属 | `qto-coordinator` |

## 工作方式

先锁定范围和计量口径，通读说明、图例、平面、系统及详图。模型沿系统或构件关系建立对象、规格和连接证据，再分段测量、按已确定规则复算，逐系统回图查漏重。对象明细分别保留确认量、规则推算量、方案暂估和未决；修改后沿依赖重算并保留历史版本。

共享 HTML 支持点线面、多图页、对象定位、同类问题分组及成员例外；用户填写结论和原因后，由所属专业复核重算，不直接将文字批注覆盖工程量。批注可以随HTML保存或导出JSON。详见[共享复核说明](skills/_qto-shared-evidence/references/HTML交互复核与批注.md)。

全专业共用“保存并反馈”：同一次操作保存完整本地HTML并同步上传完全相同的文件，保留项目、图面、对象、确认结果和原因。腾讯云作为主站，Cloudflare作为备用；正常收件不向备用发送，网络故障时先查询主站，再按同一编号备用收件。维护者端点通过共用配置提供，用户确认整份HTML的发送范围后才联网，同事无需GitHub账号。主备服务已用合成HTML完成真实上传与私有取件验证；GitHub转交尚未配置。见[腾讯云主站与主备使用说明](feedback-service/腾讯云主站与主备使用说明.md)和[Cloudflare备用部署](feedback-service/Cloudflare部署说明.md)。

换电脑使用时，反馈上传无需腾讯云或Cloudflare账号、账号登录或反馈码；云端服务签发单次上传所需的临时权限，管理员取件权限独立保留。已配置的接收地址随插件和生成的HTML提供，仍可选择仅本地保存。腾讯云正式收件保留中文`.html`文件名，旧`.bin`收件在核对完整性后兼容恢复；云端文件名修复无需重做既有复核页面。

## 获取与安装

```powershell
git clone https://github.com/jkwind02-lang/cad-qto.git
```

仓库为私有，下载需要具备访问权限。根目录就是插件根目录，保留 `.codex-plugin/plugin.json` 和完整 `skills/`；不要只复制单个专业目录。

按当前宿主支持的本地插件流程注册并安装此目录；已有同名来源时先核对来源，避免覆盖其他插件。安装后确认实际版本、启用状态和运行路径，不能以GitHub更新替代本机安装验证。

本仓库不附带用户级市场配置。[使用说明](使用说明.md)和[维护说明](维护说明.md)保留原发布记录；其中绝对路径属于原维护机器，其他电脑应使用实际路径。原始DWG转换依赖本机已有AutoCAD或Core Console，仓库不包含CAD软件。

## 仓库结构

```text
.codex-plugin/plugin.json       当前插件版本
skills/qto-*/                  13个专业和1个协调入口
skills/_qto-shared-evidence/    共享取证、计算辅助、契约、HTML与运行说明
tests/                         实际模块的合成工程算例与HTML回归
docs/                          审查、修复和人工算量路径复核
maintenance/                   运行文件清单维护工具
feedback-service/              可选的问题接收服务（需维护者独立部署）
运行文件清单.json              当前运行文件的大小与SHA-256
仓库发布核对.json              当前版本的验证范围和历史基线
```

共享目录故意没有 `SKILL.md`，不作为第15个独立入口。通用校验器提示此目录缺少SKILL时，应结合专业入口与相对依赖检查判断，不应为消除提示增加错误入口。

## 验证

```powershell
python -m pip install -r tests/requirements.txt
python -m compileall -q skills tests
python -m pytest tests -q
python tests/run_html_review_tests.py --root skills --output-root .codex_work/html-review
node tests/run_html_review_ui_tests.mjs --root skills --report .codex_work/html-review/最新核验.json
node --test feedback-service/tests/*.test.mjs
```

整合后的本地主分支实际通过136项Python测试、20项HTML契约检查、26项交互逻辑检查和23项服务测试；222个运行文件与本机已安装版本逐项一致。证据见[发布核对](仓库发布核对.json)。最终主分支提交的Windows/Linux结果以其Actions日志为准，CI还执行Workers打包与7项运行时核验。测试不执行真实DWG、原生浏览器视觉或真实项目识别准确率验收。

修改运行文件后使用 `python maintenance/update_runtime_manifest.py` 重建清单，再运行测试。项目DWG、实际工程量、用户批注、账号凭据和运行缓存不得提交到仓库。

## 发布与兼容

首次上传的217个原发布文件及213个运行文件，属于提交`cac19cc2`的历史快照；当前修复版本不再宣称与初始包字节完全一致。开发源、仓库和安装副本应按实际维护流程同步；GitHub提交不会自动切换用户电脑上的已安装版本。

旧中性几何缓存须按第一轮版本要求重新提取，不能只改版本字段。旧意见保留历史及原因，不自动成为变化后数字的确认。原专业能力档位和正式采用边界没有因本轮合成测试而统一升级。仓库没有附加开源许可证，按私人维护用途保存。
