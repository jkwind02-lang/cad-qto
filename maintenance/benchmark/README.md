# 全专业算量评价底座（基准工具）

只服务开发、校准和回归，不是用户入口，也不进运行包；已安装的 `qto-*` Skill 不依赖本目录。

2026-10-14 第二批迁移时从旧开发源 `新体系候选/_qto-benchmark` 并入（来源与哈希见插件维护目录 `迁移/旧开发源去向清单.csv`）。阶段一先在八层给排水真实图纸上建立两类基准：线网状对象与离散实例对象。模型或启发式方法只能生成候选；人工冻结 T1/T1.5 后，纯脚本评分器才比较对象集合、标签和数值。

## 目录

- `schemas/`：冻结的对象、标签、成果和评分结构。
- `scripts/`：构建对象全集、抽样、评分、运行时文字审计，以及阶段一的 DXF 导出与块定义提取。
- `tests/`：`run_mutation_tests.py` 是评分器合成变异回归，只验证脚本健壮性；`validate_phase1_artifacts.py` 校验一套真实基准产物，需要指向基准数据目录。

真实项目基准产物（对象全集、冻结输入、试标与标注）不进 Git，放在插件维护目录 `基准数据/`。历史成果不得作为标准答案输入。

## 与 `maintenance/evaluate_takeoff.py` 的分工

- `scripts/score_benchmark.py`：阶段一冻结标签（T1、T1.5、T2、T3）与成果清单的纯脚本评分。
- `maintenance/evaluate_takeoff.py`：两份独立成果的对比，复用本评分器的对象集合、重复和未决语义，不需要阶段一标签清单。参照权威的取值与人工底稿不能自动视为真值的限制见 `docs/独立算量评估接口_20261011.md`；参照建立前两边对比只称一致性检查，见 `docs/维护/门禁与基准.md`。

两者不重复，按用途选用。

## 回归与运行

- `tests/test_benchmark_scorer_mutations.py`（仓库回归）调用 `tests/run_mutation_tests.py`，要求 8 种变异全部检出且两次结果一致。
- 手工运行 `run_mutation_tests.py` 时必须用 `--output` 把报告写到仓库外；它的默认输出位置沿用旧目录结构，会落进本仓库。脚本保持与冻结工具链逐字节一致，因此没有改这个默认值。
- 冻结工具链核对（2026-10-14）：本目录与 `基准数据/…/试标V2/阶段一基准清单.json` 的 `toolchain` 11 个文件逐字节一致；试标 V1（登记 8 个文件）与"试标V2_替换记录"（登记 10 个文件）在各自登记范围内只有 `build_phase1_benchmark.py` 不同，它们由更早的版本构建。

## CAD 块闭包路径

阶段一基准固定采用：AutoCAD 隐藏实例一次性另存 DXF → `ezdxf` 批量解析 → 与 AutoCAD ActiveX 块元数据逐块核对名称、直接对象数和原点。

- 导出：`scripts/export_dxf_copy.ps1`（阶段一冻结 DXF 由它生成，保留以说明来源和重建方式）。新的 DWG 导出用运行包里的 `skills/_qto-shared-evidence/scripts/export_dwg_exchange_dxf.ps1`，它支持多个导出后端并检查 DXF 完整性。
- 转换：`scripts/convert_dxf_blocks.py`
- 构建：`scripts/build_phase1_benchmark.py`

`extract_full_block_definitions.ps1`、`extract_full_block_definitions.lsp` 只保留为诊断和无 DXF 条件下的后备路径。不得在已具备 DXF 路径时用逐对象 COM/LISP 全量遍历作为默认方案。

迁移时按文件命名规则改成英文名的 AutoLISP 与脚本文件（内容未改，靠环境变量加载，互不按文件名引用）：

| 原名 | 现名 |
|---|---|
| `导出基准DXF.lsp` | `scripts/export_benchmark_dxf.lsp` |
| `执行基准DXF导出.scr` | `scripts/run_export_benchmark_dxf.scr` |
| `提取完整块定义.lsp` | `scripts/extract_full_block_definitions.lsp` |
| `执行完整块定义提取.scr` | `scripts/run_extract_full_block_definitions.scr` |
