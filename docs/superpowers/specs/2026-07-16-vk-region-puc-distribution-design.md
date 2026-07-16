# VK 分区过滤与 PUC 汇总分布图设计

## 背景

应用已经允许用户自定义 Van Krevelen（VK）图的 X/Y 轴范围，但当前会无条件绘制全部分区名称。当坐标上限小于某个分区名称的预设中心坐标时，名称仍会出现在坐标轴外侧。

EPA CompTox Product-Use Category（PUC）当前按化合物分别绘制多个极坐标小饼图。目标是将其改成与 `EPA CompTox Reported Functional Use Distribution` 相同口径的单张汇总环形饼图。

## 目标

1. VK 分区名称的中心坐标不在当前 X/Y 轴范围内时，不绘制该分区的边框和名称。
2. PUC 每个化合物只归入一个主类别，并生成一张以化合物数量为统计单位的汇总环形饼图。
3. 第四页“化合物用途查询”和第六页“一键批量查询”采用同一套 PUC 提取及绘图逻辑。
4. PUC 图的 PNG、PDF 和明细数据继续进入现有展示与导出流程。

## 非目标

- 不改变 Functional Use Distribution 的现有分类结果。
- 不改变 CompTox 的查询、候选记录提取或 `Product_Use_Categories` 原始明细表。
- 不改变其他 EPA/ECHA 用途证据图。
- 不对 PUC 类别执行 Top-N 截断或低频类别合并。
- 不改变 VK 分区的标准坐标和名称。

## 方案选择

采用 PUC 专用分类入口，底层复用现有“唯一最高证据类别”规则。该方案比直接调用 reported Functional Use 函数具有更清楚的业务语义，同时避免修改 Functional Use 的现有输出。原 PUC 多图式 Rose Plot 将被单张 PUC Distribution 图替代，不保留重复图表。

## VK 分区显示规则

每个 VK 分区继续使用 `VK_REGIONS` 中的矩形范围和标签中心坐标。绘制前按当前 `ScreeningAxisRanges` 判断标签中心：

- `vk_x_min <= label_x <= vk_x_max` 且 `vk_y_min <= label_y <= vk_y_max`：绘制分区边框和名称；
- 任一条件不满足：跳过该分区的边框和名称；
- 标签中心恰好位于坐标轴边界时保留；
- 分区矩形部分超出坐标范围时，仍由 Matplotlib 按坐标轴范围裁剪。

这一规则同时处理 X 轴和 Y 轴缩小的情况，避免文字出现在图外或与右侧图例区域重叠。

## PUC 分类口径

PUC 分类数据只读取 `source_type == "product_category"` 的 CompTox 候选记录，并以英文原始 PUC 场景作为类别。

对每个输入化合物分别执行：

1. 按标准化后的 PUC 类别合并重复记录；
2. 将同一类别的有效 `evidence_count` 相加；
3. `evidence_count` 缺失、非数值或小于等于 0 时，该记录按 1 条证据计算；
4. 只有一个类别取得最高证据数时，将化合物归入该类别；
5. 最高证据数并列时归入 `Others`；
6. 没有有效 PUC 类别或没有 PUC 查询结果时归入 `Others`。

所有输入化合物均进入分类结果，因此饼图圆心总数始终等于去重后的输入化合物数。`Others` 只表示并列或缺失，不用于合并低频类别。

## 组件与数据流

### `src/use_rose_plot.py`

- 增加 PUC 专用分类提取函数，输出与现有 `COMPOUND_CLASSIFICATION_COLUMNS` 一致的表结构；
- 分类原因明确区分唯一最高、最高并列和无结果；
- 继续使用 `generate_compound_classification_pie_plot()` 渲染环形图；
- PUC 图脚注说明切片大小代表按主 PUC 类别统计的化合物数量，并解释 `Others`。

### `src/auto_query_workflow.py`

- 在 CompTox 结果准备阶段生成 `EPA_PUC_Pie_Data`；
- 将原 `rose` 图源替换为 `classification_pie` 图源；
- 图名和文件前缀改为：
  - `EPA CompTox Product-Use Category Distribution`
  - `EPA_Product_Use_Category_Distribution`
- 将 `EPA_PUC_Pie_Data` 纳入 CompTox 结果工作簿；
- 一键批量查询结果继续导出对应 PNG 和 PDF。

### `pages/4_化合物用途查询.py`

- 使用与一键批量查询相同的 PUC 专用提取函数；
- 将 PUC 数据源改为 `classification_pie`；
- 展示分类类别、证据数和分类原因；
- 下载文件使用新的 Distribution 文件前缀。

### VK 调用方

VK 分区过滤集中在 `_draw_van_krevelen()` 内完成，因此综合筛查和一键批量查询不需要各自实现过滤规则。

## 空数据与异常处理

- 输入化合物集合为空时，不生成 PUC 分类图，沿用现有无数据提示；
- 输入化合物存在但 PUC 候选为空时，生成全部为 `Others` 的分类表和环形图；
- 单条候选记录字段不完整时，仅忽略无有效类别的记录，不中断整批绘图；
- 图形导出继续使用现有 PNG/PDF 生成与字体兼容逻辑；
- 不吞掉编程错误或导出错误，继续由现有工作流记录警告。

## 兼容性与迁移

- `Product_Use_Categories` 原始明细表保持不变；
- 新增 `EPA_PUC_Pie_Data`，用于复核每个化合物的最终分类；
- 移除 PUC 的 `EPA_Product_Use_Category_Rose_Plot` 图形产物，替换为 `EPA_Product_Use_Category_Distribution`；
- `generate_use_rose_plot()` 保留，因为其他兼容路径和测试仍可能使用它；仅 PUC 不再通过该函数绘图。

## 测试与验收

### VK

- X 轴上限小于右侧分区标签中心时，右侧分区边框和名称均不存在；
- Y 轴范围排除分区标签中心时，该分区边框和名称均不存在；
- 标签中心位于坐标边界时分区仍保留；
- 默认范围下现有分区仍按预期显示；
- 自定义坐标范围和刻度测试继续通过。

### PUC 数据

- 重复 PUC 记录按类别累加证据；
- 唯一最高类别被正确选中；
- 最高值并列归入 `Others`；
- 无候选记录归入 `Others`；
- 缺失或非正证据数按 1 计；
- 输出行数等于去重后的输入化合物数。

### PUC 图与工作流

- 环形图圆心总数等于输入化合物数；
- 图例包含类别、数量和百分比；
- 第四页和一键批量查询均不再配置 PUC Rose Plot；
- `EPA_PUC_Pie_Data` 存在于结果表和工作簿；
- 新命名的 PNG/PDF 产物可以正常生成；
- 先运行相关单元测试，再运行完整测试集和页面/模块编译检查。

## 完成标准

当用户缩小 VK 坐标范围时，任何标签中心位于可视范围外的分区均不会出现；PUC 在第四页和一键批量查询中均显示为一张按化合物唯一主类别统计的汇总环形饼图，且其总数、分类明细和导出产物一致。
