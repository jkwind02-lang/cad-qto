# CAD 算量插件

按人工算量思路执行识图、测量、计算和回图复核的 Codex 插件，包含 **13 个专业 Skill、1 个协调入口和共享取证资源**。

本仓库当前修复版本为 `0.2.0+codex.20260920`；初始发布基线为 `0.1.0+codex.20260914054325`，包含各专业规则、脚本、能力披露，以及支持批注的单文件 HTML 复核界面。工程量仍须以具体项目的有效资料和计量口径为依据；安装及测试通过不代表所有专业已达到自动识图成熟状态。

## 本轮人工算量路径改进

当前版本 `0.2.0+codex.20260920`，沿用13个专业与协调入口。新增证据驱动的管网分段、图页隔离及跨层接口、空间索引、明确算式依赖重算，以及电气通道/回路分离的路径台账复算。模型仍负责识图，未核实部分不冒充确认量。

[人工算量路径复核](docs/人工算量计算路径复核_20260920.md) · [本轮修复与验证边界](docs/计算路径改进记录_20260920.md) · [运行输入与重算说明](skills/_qto-shared-evidence/references/按人工算量顺序执行与重算.md)

全部Python回归运行 `python -m pytest tests -q`；HTML契约及交互检查沿用下文命令。实际通过记录以对应提交的Actions日志为准；不把合成回归称为真实DWG或识图准确率验收。

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

1. 读取说明、图例、平面图、系统图及相关详图，确定本次范围和计量依据。
2. 模型依据图纸识别对象、连接关系和规格，工具负责提取事实与确定性计算。
3. 逐对象形成明细、依据、假定和状态，分别保留确认量、规则推算量、方案暂估量和未决事项。
4. 回图复核并交付可读汇总；局部资料缺失不阻断无关对象的计算。
5. 用户可在共享 HTML 中点选定位、按同类问题批注，并填写结果和原因。所属专业复核重算后写回处理结果。

HTML 默认使用精简顶部、可调宽填写区、问题分组和成员例外；支持点线面、多图页及内嵌图像。批注可随 HTML 保存或单独导出 JSON。详见[共享复核说明](skills/_qto-shared-evidence/references/HTML交互复核与批注.md)。

## 获取与安装

```powershell
git clone https://github.com/jkwind02-lang/cad-qto.git
```

仓库为私有，下载时需要拥有该仓库访问权限的 GitHub 账号。仓库根目录就是插件根目录，必须保留 `.codex-plugin/plugin.json` 和完整 `skills/`，不要只复制某个专业目录。

在支持本地插件的 Codex 中，使用内置 `plugin-creator` 将这个根目录注册为个人插件来源，再按实际市场名称安装。可在克隆目录发出：

> 请将本目录已有的 cad-qto 插件注册并安装到我的个人插件市场，保留全部现有规则文件；先检查是否已有同名条目，避免覆盖其他来源，安装后确认实际版本和启用状态。

本仓库没有附带用户级市场配置，避免把原机器的路径、其他插件或账号设置带入另一台机器。原发布包的[使用说明](使用说明.md)和[维护说明](维护说明.md)原样保留；维护说明中的绝对路径属于原维护机器，其他电脑需按实际位置建立维护路径。

## 运行条件

- Codex 或支持相应 Skill 的宿主，以及文件读写和 Python 环境。
- 原始 DWG 的转换依赖本机可用的 AutoCAD 或 Core Console；插件不包含 CAD 软件。
- 具体专业需要的输入和第三方依赖，以各专业 `SKILL.md`、`capability.json` 及脚本说明为准。
- 共享 HTML 的合成回归使用 Python 和支持 `DecompressionStream` 的 Node.js 环境。

## 仓库结构

```text
.codex-plugin/plugin.json       插件清单及发布版本
skills/qto-*/                  13 个专业和 1 个协调入口
skills/_qto-shared-evidence/    共享取证、契约、脚本和 HTML 模板
tests/                         不含真实项目数据的 HTML 合成回归
运行文件清单.json              运行文件的大小与 SHA-256（数量见清单）
仓库发布核对.json              本次上传副本的验证记录
```

共享目录故意没有 `SKILL.md`，不作为第 15 个独立入口。通用插件校验器可能对此报“缺少 SKILL.md”；应结合实际安装、专业入口及相对依赖检查判断，不能为消除提示而增加错误入口。

## 验证共享复核功能

在仓库根目录运行，生成物写入被 Git 忽略的工作区：

```powershell
python tests/run_html_review_tests.py --root skills --output-root .codex_work/html-review
node tests/run_html_review_ui_tests.mjs --root skills --report .codex_work/html-review/最新核验.json
```

测试使用合成对象，覆盖 13 个专业的数据交接、批注隔离、保存恢复和界面逻辑。它们不执行真实 DWG 算量，也不是浏览器视觉验收或工程量准确率证明。

## 发布与后续维护

本次上传保留 217 个原发布文件的内容，新增仓库说明和独立测试。原机器的开发源、插件安装源及启用配置未因上传而切换；后续在原开发源完成修改和验证后，应同步发布包及本仓库，再提交新版本。仓库中的规则变更也必须经过验证和安装流程后才会影响本机实际运行。

仓库不包含工程 DWG、项目工程量、用户批注、账号凭据和运行缓存。不要把项目测试输出提交到本仓库。没有附加开源许可证，仓库按私人维护用途保存。

## 2026-09-20 审查修复

详见[修复记录与兼容边界](docs/修复记录_20260920.md)。本次修复几何保真、WCS变换、旧批注版本、组成员身份与部分量合计校验；新增实际模块回归测试。旧中性缓存须重新提取，旧意见保留为历史待复核，不自动转为当前数值确认。仓库更新不代表用户电脑已自动更新安装。

```powershell
python -m pytest tests/test_audit_regressions.py -q
```
