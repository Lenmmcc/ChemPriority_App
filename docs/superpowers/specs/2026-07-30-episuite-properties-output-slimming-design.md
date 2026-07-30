# EPI Suite Properties 输出精简设计

## 背景

对 `2026-07-30T15-35_export.csv` 的实际审计显示：

- 表含 19 条数据、67 列，并额外导出了一个无表头的行号索引列。
- `koawin_log_kow` 与既有 `log_kow_estimated` 在 19 条数据中完全相同。
- `koawin_log_koa` 与既有 `log_koa_estimated` 在 19 条数据中完全相同。
- `koawin_kow`、`koawin_koa`、`koawin_kaw` 是用户不再需要展示的原始系数。
- `koawin_log_kaw`、`tpsa_rdkit_a2`、`mr_rdkit_cm3_mol` 没有既有等价列，应继续保留。

本次变更采用保守精简原则：只删除已确认重复或明确不需要的列，不根据
单个批次的空值或常量值删除可能在其他化合物中有意义的字段。

## 对外结果契约

### 删除的新增字段

以下字段不得再出现在 `Properties` 页面表格、CSV 下载或 Excel
`Properties` 工作表中：

- `koawin_log_kow`
- `koawin_kow`
- `koawin_log_koa`
- `koawin_koa`
- `koawin_kaw`

### 保留的既有字段

KOW 和 KOA 的 log10 结果继续使用 EPI API 已有字段：

- `log_kow_estimated`
- `log_koa_estimated`

现有的 `selected`、`estimated`、`experimental`、`type`、`units` 字段契约
保持不变；即使某批数据中为空或内容相同，也不动态删除。

### 保留的新增字段

新增结果只保留以下三个唯一字段，并按此顺序放在 Properties 末尾：

1. `koawin_log_kaw`
2. `tpsa_rdkit_a2`
3. `mr_rdkit_cm3_mol`

三个字段在页面、CSV 和 Excel 中均直接使用内部英文列名，不再应用单独的
中文标签或专属数字格式。底层值保持数值型和完整精度。

## 内部计算与警告

KOAWIN 的 KOW、KOA、KAW 原始系数仍可在内部用于：

- 计算唯一缺失的 `koawin_log_kaw`；
- 校验 `KOA = KOW / KAW`；
- 检查 API direct log 与原始系数是否一致；
- 生成现有结构化 Warnings。

内部计算不得重新把原始系数或重复的 logKOW/logKOA 字段暴露到结果表。
无效、非正、非有限或超出可恢复范围的数值继续按现有规则留空，不中断整行
结果。

TPSA 与 MR 的 SMILES 优先级和算法保持不变：

1. EPI API `chemicalProperties.smiles`
2. 已保存的 `epi_smiles`
3. 输入 `smiles`

无法解析时两个字段留空，并向 `Warnings` 追加非致命警告。

## 页面与导出

- EPI 详情表统一使用 Streamlit 默认列名和默认数值显示。
- 所有 EPI 详情表调用 `st.dataframe` 时设置 `hide_index=True`，避免页面
  导出的 CSV 带出无表头行号列。
- 删除仅服务于新增八字段中文标签/格式的共享显示映射。
- Excel `Properties` 直接写入内部列名和数值，不再进行新增字段的表头重命名。
- 其他 EPI 工作表和原始 JSON 不变。

## 明确保留的列

本次不删除以下在样例中为空、恒定或与 selected 暂时相同的字段：

- CAS/EPI 名称、系统名称、flags 等身份和审计字段；
- experimental、type、units 字段；
- selected 与 estimated 字段；
- dermal permeability 相关字段；
- `smiles` 与 `epi_smiles`。

这些字段在其他化合物或包含实测值的响应中可能不同，不能依据单批数据永久
删除。

## 测试与验收

测试应证明：

- `Properties` 只含三个保留的新增字段，且顺序固定；
- 五个删除字段不出现在任何结果表或 Excel 表头；
- 既有 `log_kow_estimated`、`log_koa_estimated` 值保持不变；
- `koawin_log_kaw` 仍等于有效 KAW 的 log10；
- TPSA/MR 数值、SMILES 优先级和失败警告保持不变；
- 页面所有 EPI 详情表隐藏索引且不使用新增字段专属 column config；
- Excel 三个保留字段使用内部英文列名并保持数值类型和原始精度；
- 聚焦 EPI 回归、完整测试套件、模块编译和差异检查全部通过。

## 非目标

- 不动态删除全空列。
- 不合并 selected、estimated、experimental 字段。
- 不改变 EPI API 请求、响应缓存、重试或 checkpoint。
- 不改变 TPSA/MR 算法或引入新依赖。
- 不加入汽化焓 ΔHvap。
