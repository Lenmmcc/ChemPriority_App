# Reported 汇总饼图与全流程图表一致性设计

## 1. 目标

统一 ChemPriority 各流程的图表字体和统计口径，重构 EPA/ECHA reported 用途结果的汇总方式，拆分 Excel 中的 predicted 与 reported 数据，并为来源属性增加总数守恒的汇总饼图。

本次设计同时覆盖独立子流程和“一键批量查询”，不能只修改汇总页面。

## 2. 已确认的需求边界

1. 所有程序生成图的文字统一使用 Times New Roman。
2. DBE 图使用纯白背景并删除灰色网格或灰色底纹。
3. EPA predicted 饼图保留现有“每个化合物取最高预测概率项”的逻辑和总体圆环形式。
4. EPA/ECHA reported 不再使用逐化合物饼图或玫瑰图，改为总体圆环饼图。
5. EPA/ECHA reported 证据点图必须保留；ECHA 增加与 EPA 一致的证据点图。
6. reported 总体饼图中，每个化合物按唯一最多报道类别贡献一次计数；并列最高或无结果时归入 `Others`。
7. 所有饼图必须以完整的有效输入化合物清单为统计母集，不能因为无查询结果而少计化合物。
8. 来源属性固定使用 `Anthropogenic`、`Natural`、`Both`、`Unknown` 四类。
9. Excel 中 predicted 与 reported 必须位于不同工作表，不允许继续共用混合候选结果表。
10. 页面预览、PNG、PDF、完整工作簿、模块工作簿和 ZIP 必须保持一致。

## 3. 当前实现与问题

- `src/use_rose_plot.py` 已提供 EPA predicted 总体圆环饼图，但无结果化合物不会进入绘图数据。
- EPA reported 当前使用证据点图，不存在符合新口径的总体饼图。
- ECHA 当前使用逐化合物玫瑰图，不符合新的总体汇总要求。
- `src/auto_query_workflow.py` 将 EPA 候选结果保存在混合的 `CompTox_Candidates` 表中；一键批量工作簿也按该混合表导出。
- EPA 独立工作簿已有 `Functional_Uses_Predicted` 和 `Functional_Uses_Reported`，但仍保留混合的 `All_Use_Candidates`。
- 来源属性已有逐化合物判定结果，但没有统一的总体饼图。
- `src/r_screening_replica/plots.py` 的 DBE 图虽然设置了白色坐标区，仍绘制灰色主网格。
- 各绘图模块分别设置字体；`src/use_rose_plot.py` 和 `src/toxpi_calc.py` 当前优先使用 DejaVu Sans/Arial，而不是 Times New Roman。

## 4. 方案选择

采用“共享统计规则 + 各入口复用”的实现方式。

统一的逐化合物分类函数负责生成绘图数据，独立页面和一键批量页面只负责传入完整化合物母集、候选结果和标题。所有饼图、证据点图及 Excel 分类表均从相同的标准化结果生成。

不采用页面级重复实现，因为分别修改 `pages/4_化合物用途查询.py` 和 `pages/6_一键批量查询.py` 容易产生统计口径漂移。

## 5. 架构与组件边界

### 5.1 统一字体配置

新增集中式绘图样式模块 `src/plot_style.py`，负责：

- 检查并注册 Times New Roman；
- 设置 Matplotlib 的 `font.family`、PDF 字体嵌入和负号显示；
- 为 plotnine 图提供相同字体设置；
- 在字体不可用时返回明确警告，不静默替换为其他字体。

以下绘图模块统一调用该入口：

- `src/r_screening_replica/plots.py`
- `src/use_rose_plot.py`
- `src/toxpi_calc.py`
- `src/cp_screening_workflow.py`
- 页面中直接创建或导出的 Matplotlib 图

Streamlit 部署环境如需保证 Times New Roman，必须提供系统字体或具有合法使用权的字体文件。设计不允许提交来源不明的专有字体文件。

### 5.2 完整化合物母集

所有饼图使用去重后的有效输入化合物清单作为固定母集。

母集优先来自标识符补全后的查询输入；没有运行标识符补全时使用规范化后的原始输入。每个化合物生成稳定的标准化键，用于关联 EPA、ECHA 和来源属性结果。

重复输入行只保留一个化合物计数。查询失败、空结果或关联失败不会从母集中删除化合物。

### 5.3 标准化逐化合物分类表

共享分类函数输出至少包含：

- `source`
- `compound_key`
- `compound`
- `final_category`
- `evidence_count`
- `classification_reason`
- `is_other`

`classification_reason` 使用稳定的英文状态值，例如：

- `unique_top_reported_category`
- `tie_for_top_reported_category`
- `no_reported_result`
- `top_predicted_probability`
- `no_predicted_result`
- `anthropogenic_only`
- `natural_only`
- `both_source_types`
- `insufficient_source_evidence`

分类表既用于绘图，也作为 Excel 审计表导出。

## 6. 统计规则

### 6.1 EPA predicted

- 保留当前每个化合物选择最高预测概率项的逻辑。
- 不改变 predicted 类别匹配、颜色和圆环图语义。
- 没有 predicted 结果的化合物归入 `Others`。
- 每个化合物只贡献一次计数。

### 6.2 EPA reported

1. 仅选择 `functional_use` 且证据类型为 reported 的记录。
2. 使用与 predicted 图一致的功能用途类别标准化规则。
3. 对单个化合物按类别汇总 reported 支持次数；有效 `evidence_count` 可用时累计该值，否则按候选记录计数。
4. 只有一个类别具有严格最高次数时，将化合物归入该类别。
5. 两个或更多类别并列最高时归入 `Others`。
6. 没有有效 reported 候选记录时归入 `Others`。

### 6.3 ECHA reported

ECHA 使用与 EPA reported 相同的唯一最高项规则。类别优先从标准化英文用途字段取得，无法取得有效类别时视为无 reported 结果。

### 6.4 来源属性

来源属性从逐化合物汇总结果中的人为源和天然源证据数生成：

- 人为源证据数大于零、天然源证据数为零：`Anthropogenic`
- 人为源证据数为零、天然源证据数大于零：`Natural`
- 两者均大于零：`Both`
- 两者均为零、证据不足或查询无结果：`Unknown`

## 7. 图形设计

### 7.1 统一圆环饼图

EPA predicted、EPA reported、ECHA reported 和来源属性使用相同的总体圆环样式：

- 统一图幅、圆环宽度、起始角度和顺时针方向；
- 图例显示类别、化合物数量和百分比；
- 中心显示 `Total compounds` 和总数；
- 图例数量之和必须等于中心总数；
- 来源属性固定四类不合并；
- reported 图不因类别数量或占比小而把有效类别合并进 `Others`。

### 7.2 小占比标签

采用已确认的分级显示方案：

- 占比大于或等于 5%：百分比显示在扇区内部；
- 占比大于或等于 1% 且小于 5%：使用图外标签、引导线和自动避让；
- 占比小于 1%：图中不显示百分比，仅在图例显示完整类别、数量和百分比。

标签位置变化不能改变类别合并规则，也不能影响计数。

### 7.3 reported 说明文字

EPA/ECHA reported 图底部统一使用：

> Others includes compounds with no reported result or with a tie for the most frequently reported category.

### 7.4 证据点图

- EPA 保留现有 reported 证据点图。
- ECHA 使用相同的化合物—reported 类别点阵形式新增证据点图。
- 证据点图只展示实际 reported 证据；无结果化合物由总体饼图中的 `Others` 表达。
- 删除 ECHA 旧的逐化合物玫瑰图入口和导出文件。

### 7.5 DBE 图

- Figure 和 Axes 背景均设为纯白。
- 删除主网格和次网格，不保留灰色底纹。
- 保留黑色坐标轴、刻度、气泡颜色、大小编码和图例。

## 8. Excel 输出设计

### 8.1 EPA

EPA 独立工作簿、一键批量完整工作簿和 EPA 模块工作簿包含：

- `CompTox_Summary`
- `Product_Use_Categories`
- `Functional_Uses_Predicted`
- `Functional_Uses_Reported`
- `EPA_Predicted_Pie_Data`
- `EPA_Reported_Pie_Data`
- 当前流程对应的查询警告表
- `Evidence_Metadata`

`CompTox_Candidates` 可以作为内部下游处理数据继续存在，但不能再作为用户工作表导出。删除用户输出中的 `All_Use_Candidates`；任何用户可见审计表也不能混合 predicted 与 reported 功能用途记录。

### 8.2 ECHA

ECHA 输出包含：

- `ECHA_Use_Summary`
- `ECHA_Uses_Reported`
- `ECHA_Reported_Pie_Data`
- dossier 明细
- 查询警告表

### 8.3 来源属性

来源属性输出包含：

- `Source_Origin_Summary`
- `Source_Origin_Pie_Data`
- `Source_Origin_Evidence`
- `Source_Origin_Warnings`

## 9. 页面与导出覆盖范围

### 9.1 `pages/4_化合物用途查询.py`

- 保留 EPA predicted 总体圆环饼图。
- 新增 EPA reported 总体圆环饼图。
- 保留 EPA reported 证据点图。
- 将 ECHA 逐化合物玫瑰图替换为 reported 总体圆环饼图。
- 新增 ECHA reported 证据点图。
- 新增来源属性总体圆环饼图。
- 同步更新页面说明和独立工作簿下载。

### 9.2 `src/auto_query_workflow.py` 与 `pages/6_一键批量查询.py`

- 复用相同的完整化合物母集和分类函数。
- 页面总览显示与独立页面一致的饼图和证据点图。
- 完整工作簿和模块工作簿使用拆分后的表结构。
- ZIP 内图形目录同步输出 PNG 和 PDF。
- 不再输出 ECHA 旧逐化合物玫瑰图。

### 9.3 其他子流程

- `pages/0_综合筛查流程.py` 及 `src/r_screening_replica/plots.py`：统一字体并删除 DBE 灰色背景。
- `pages/2_ToxPi毒性评估.py`、`src/toxpi_calc.py` 和 `src/cp_screening_workflow.py`：页面图、PNG 和 PDF 统一字体。

## 10. 异常处理

- 查询错误不等于删除化合物；reported 归入 `Others`，来源属性归入 `Unknown`。
- 无法标准化的 reported 类别视为无有效 reported 结果。
- 输入为空时不绘制空饼图，页面给出无可绘制化合物提示。
- 字体不可用时页面和批量警告表给出明确提示。
- 生成分类表后执行总数守恒检查；不守恒时阻止输出误导性饼图并记录内部错误。

## 11. 测试设计

### 11.1 分类规则单元测试

- EPA reported 存在唯一最高类别。
- EPA reported 多类别并列最高进入 `Others`。
- EPA reported 无结果进入 `Others`。
- ECHA reported 执行相同三类测试。
- EPA predicted 仍选择最高概率项。
- EPA predicted 无结果进入 `Others`。
- 来源属性正确映射四个固定类别。

### 11.2 总数守恒测试

分别验证 EPA predicted、EPA reported、ECHA reported 和来源属性：

`图例类别数量之和 = 分类表行数 = 去重后的有效输入化合物数`

### 11.3 图形测试

- 所有绘图模块使用 Times New Roman。
- DBE Figure/Axes 为白色且网格不可见。
- 大于或等于 5% 的标签位于图内。
- 大于或等于 1% 且小于 5% 的标签使用外部标注和引导线。
- 小于 1% 的百分比不出现在扇区周围，但图例保留其数据。
- reported 图包含固定英文说明。
- EPA/ECHA reported 证据点图可导出为 PNG/PDF。
- ECHA 旧玫瑰图不再出现在图形清单中。

### 11.4 Excel 测试

- predicted 和 reported 工作表均存在且互不混合。
- 混合候选表不再作为用户输出。
- 独立工作簿、完整工作簿和模块工作簿结构一致。
- 四张饼图分类表可追溯到完整化合物母集。

### 11.5 页面与全仓库验证

- 页面契约测试确认子页面和一键批量页面都调用共享统计函数。
- 先运行用途、来源属性、绘图和工作簿的针对性测试。
- 再运行 `python -m unittest discover -s tests -v`。
- 最后运行 `python -m compileall app.py pages src`。

## 12. 验收标准

1. 所有页面预览和 PNG/PDF 图中文字使用 Times New Roman。
2. DBE 图无灰色底纹和灰色网格。
3. EPA predicted 饼图保留且逻辑不变，无结果补入 `Others`。
4. EPA/ECHA reported 均具有总体圆环饼图和证据点图。
5. ECHA 不再生成逐化合物玫瑰图。
6. reported 并列最高或无结果进入 `Others`，且底部英文解释准确。
7. 来源属性饼图只使用四个指定英文类别。
8. 每张饼图的类别总数与去重后的有效输入化合物数一致。
9. Excel 中 predicted 与 reported 完全拆分。
10. 独立子流程和“一键批量查询”的统计、图形和导出结果一致。
