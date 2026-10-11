# CADINST 精确选择适配说明

当前增量仅提供模型选择结果到提取器之间的契约适配。

已支持：
- source_sha256/layout 结构校验
- CADINST 结构校验
- 显式 layer_names 字面选择
- 空选择诊断

未包含：
- 修改 extract_surface_regions.py
- DXF 遍历匹配 CADINST 实例
- HATCH 几何计算

后续接入必须复用 qto_cad_geometry 的实例展开和现有 HATCH 几何提取，不建立第二套几何引擎。
