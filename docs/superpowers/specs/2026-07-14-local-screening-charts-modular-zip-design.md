# 一键批量查询本地筛查图与模块化 ZIP 设计

## 目标

补齐“一键批量查询”结果总览中“本地筛查”模块缺失的化学类型图、DBE 气泡图和 Van Krevelen 图，并将最终 ZIP 按查询环节分目录组织，同时保留根目录中的完整汇总工作簿 `Auto_Query_Workflow_Results.xlsx`。

## 根因

本地筛查子流程 `run_screening_pipeline()` 已生成目标图，并通过 `ScreeningResult.figure_paths` 返回 PNG/PDF 路径。当前 `_run_r_replicate_df()` 只把筛查数据表加入 `AutoWorkflowResult.tables`，没有把 `figure_paths` 转换为最终工作流可使用的图表对象。因此：

- “本地筛查”结果标签只能看到数据表；
- `build_auto_workflow_charts()` 只能生成 EPA/ECHA 用途图；
- ZIP 只能包含用途图，无法包含本地筛查图。

## 结果对象与数据流

保留现有 `AutoWorkflowChart(title, png, pdf)` 作为所有图表的统一内存格式，并让 `AutoWorkflowResult` 持有工作流运行阶段已生成的图表集合。该字段提供空集合默认值，避免破坏现有构造代码和测试。

本地筛查完成后，读取以下三个 `ScreeningResult.figure_paths` 条目，将文件内容立即转换为字节：

| 原始图键 | 最终图键 | 页面标题 |
| --- | --- | --- |
| `category_percent_donut_with_total` | `Local_Chemical_Type_Distribution` | Chemical Type Distribution |
| `compound_bubble_plot` | `Local_DBE_Bubble_Plot` | DBE Bubble Plot |
| `VanKrevelen` | `Local_Van_Krevelen_Plot` | Van Krevelen Plot |

使用字节而不是临时路径作为最终结果，确保页面重跑和 ZIP 打包不依赖临时目录是否仍然存在。

`build_auto_workflow_charts()` 先复制运行阶段已有的本地筛查图，再生成并追加现有 EPA/ECHA 图。图键保持唯一，后续页面和 ZIP 共用同一份图表集合。

## 页面展示

“结果总览”继续按现有模块标签组织。`_result_dashboard_groups()` 将 `Local_` 前缀图表归入“本地筛查”；EPA/ECHA 的前缀映射保持不变。

在“本地筛查”标签内，先显示现有结果表，再依次显示：

1. Chemical Type Distribution；
2. DBE Bubble Plot；
3. Van Krevelen Plot。

页面直接复用图表对象中的 PNG 字节，不重复绘图，也不增加外部请求。

## ZIP 目录结构

ZIP 根目录保留完整汇总工作簿，另外按模块生成独立文件夹和模块专属工作簿：

```text
Auto_Query_Workflow_Results.xlsx
01_Local_Screening/
  Local_Screening_Results.xlsx
  figures/
    Chemical_Type_Distribution.png
    Chemical_Type_Distribution.pdf
    DBE_Bubble_Plot.png
    DBE_Bubble_Plot.pdf
    Van_Krevelen_Plot.png
    Van_Krevelen_Plot.pdf
02_Identifier_Completion/
  Identifier_Completion_Results.xlsx
03_EPI_Suite/
  EPI_Suite_Results.xlsx
04_EPA_CompTox/
  EPA_CompTox_Results.xlsx
  figures/
05_ECHA/
  ECHA_Results.xlsx
  figures/
06_Source_Origin/
  Source_Origin_Results.xlsx
07_Pov_LRTP_PBM_ToxPi/
  Pov_LRTP_PBM_ToxPi_Results.xlsx
```

文件夹名称使用 ASCII，避免不同解压工具和操作系统的编码差异。只为实际产生结果表或图表的模块创建文件夹。

每个模块工作簿只包含属于该模块的结果表。根目录汇总工作簿继续包含运行日志、代表性输入和所有结果表，维持已有下载兼容性。运行状态与全局警告同时保留在根目录汇总工作簿中，不在每个模块重复写入。

表格归属沿用页面现有分组契约：

- `01_Local_Screening`：结构准备、本地筛查、DF 和峰面积相关表；
- `02_Identifier_Completion`：标识符补全及警告；
- `03_EPI_Suite`：EPI 主结果、原始结果及错误；
- `04_EPA_CompTox`：CompTox 汇总、候选用途及错误；
- `05_ECHA`：ECHA 用途与 GHS/C&L 的汇总、明细及错误；
- `06_Source_Origin`：来源属性汇总、证据及错误；
- `07_Pov_LRTP_PBM_ToxPi`：Pov-LRTP、PBM 和 ToxPi 输入与结果。

EPA 图写入 `04_EPA_CompTox/figures/`，ECHA 图写入 `05_ECHA/figures/`，本地筛查图写入 `01_Local_Screening/figures/`。

## 错误处理

- 若本地筛查某张图未生成或文件为空，不中断整个工作流；跳过该图并在工作流警告表记录明确原因。
- 若某个模块没有任何表或图，不创建空文件夹。
- 单个模块工作簿写入失败时，不返回表面成功的 ZIP；让打包异常进入页面现有错误反馈链路。
- 保留当前图表生成异常行为，避免静默输出损坏的 PNG/PDF。

## 测试与验证

按测试驱动顺序实现以下回归：

1. 构造本地筛查输入，证明工作流结果包含三张本地筛查图，且 PNG/PDF 文件头有效；
2. 证明“本地筛查”页面分组包含 `Local_` 图键；
3. 证明 ZIP 根目录仍包含 `Auto_Query_Workflow_Results.xlsx`；
4. 证明 ZIP 为有结果的模块创建独立工作簿；
5. 证明本地筛查、EPA 和 ECHA 图分别进入各自模块的 `figures/`；
6. 证明无结果模块不产生空目录；
7. 运行 `python -m unittest discover -s tests -v`；
8. 运行 `python -m compileall app.py pages src`；
9. 启动本地 Streamlit，使用真实一键查询结果核对三张图的页面预览和 ZIP 目录。

## 范围边界

- 不改变本地筛查、EPA、ECHA 的绘图算法；
- 不改变查询顺序、缓存或并发设置；
- 不删除完整汇总工作簿；
- 不为当前没有图表算法的模块新增图表；
- 不改动其他页面的独立下载结构。
