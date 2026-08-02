# EPI Suite Properties 列名与顺序调整设计

## 背景

当前 `Properties` 结果已删除重复的 KOW、KOA、KAW 原始系数，但仍存在三个
展示问题：

- `log_koa_selected` 与当前所需结果契约重复，不再需要公开；
- `koawin_log_kaw` 的公开列名与同表的 `log_kow_*`、`log_koa_*` 命名风格
  不一致；
- `tpsa_rdkit_a2`、`mr_rdkit_cm3_mol` 位于表尾，与分子性质字段分离。

页面顶部“目标环境归趋指标”还需要补充 logKoa、logKaw、TPSA、MR，并明确
logKow、logKoa、logKoc 的实验值与估算值使用规则。

## 对外结果契约

`Properties` 页面表格、页面下载的 CSV 与 Excel `Properties` 工作表统一采用
以下契约：

1. 删除 `log_koa_selected`；
2. 删除公开列名 `koawin_log_kaw`，以 `log_kaw` 暴露同一数值；
3. `log_kaw`、`tpsa_rdkit_a2`、`mr_rdkit_cm3_mol` 依次插入到
   `log_koa_units` 和 `melting_point_selected` 之间。

目标相邻顺序必须严格为：

```text
log_koa_estimated
log_koa_experimental
log_koa_type
log_koa_units
log_kaw
tpsa_rdkit_a2
mr_rdkit_cm3_mol
melting_point_selected
```

`log_kaw` 的底层值、缺失规则和警告规则不变，只调整公开列名和位置。页面、
CSV 与 Excel 不保留 `koawin_log_kaw` 兼容别名，避免再次形成重复列。

## 内部边界

内部 KOAWIN 解析与一致性校验继续使用 `koawin_log_kaw`。该名称明确表示数值
来自 KOAWIN 模型中的 KAW 系数，适合保留在内部归一化层。

公开结果构造层负责把内部值映射为：

```text
koawin_log_kaw -> log_kaw
```

这样可以避免扩大修改到 KOAWIN 原始系数校验，也避免与 Pov-LRTP 模块内部的
`log_kaw` 输入语义产生不必要耦合。

`_build_properties_row(...)` 按以下阶段构造有序字段：

1. 身份与分子基础信息；
2. logKOW 和 logKOA 字段，并移除 `log_koa_selected`；
3. 公开的 `log_kaw`、TPSA、MR；
4. 熔点及其后的其他 Properties 字段。

DataFrame 的插入顺序作为页面、CSV 和 Excel 的共同列顺序来源，不增加页面或
Excel 专属的重命名、删除或重排逻辑。

## 目标环境归趋指标说明

页面说明表新增或更新以下条目：

```text
辛醇/水分配系数 logKow（优先采用实验值；无实验值时采用 KOWWIN 估算值）
辛醇/空气分配系数 logKoa（优先采用实验值；无实验值时采用 KOAWIN 估算值）
空气/水分配系数 logKaw（由 KOAWIN 的 KAW 取 log10）
拓扑极性表面积 TPSA（Å²；RDKit 结构计算值）
Wildman–Crippen 摩尔折射率 MR（cm³/mol；RDKit 结构计算值）
有机碳归一化吸附系数 logKoc（优先采用实验值；无实验值时采用 KOCWIN 的 MCI 估算值）
```

`FATE_ENDPOINTS` 和由其生成的 `ENDPOINT_KEYS` 继续只表示核心环境归趋查询
契约。新增 logKoa、logKaw、TPSA、MR 不直接加入 `FATE_ENDPOINTS`，避免扩展
补充导入、结果池和一键批量查询的字段集合。

新增独立的页面说明常量 `TARGET_INDICATOR_DESCRIPTIONS`：复用现有核心指标，
并在 logKow 后插入 logKoa、logKaw、TPSA、MR。页面“目标环境归趋指标”表格
改为读取该常量。`FATE_ENDPOINTS` 中 logKow、logKoc 的 description 同步采用
上述批准文本，因此核心指标和页面说明仍只有一个权威文字来源。

本次仅修改说明，不改变现有 logKow、logKoa、logKoc 数据解析、
selected/estimated/experimental 选取或计算逻辑。

## 页面与导出

- 页面继续直接展示 `Properties` DataFrame，并保持 `hide_index=True`；
- CSV 使用同一公开列名和顺序；
- Excel `Properties` 直接写入同一 DataFrame；
- 其他 EPI 工作表、Raw API JSON、Warnings 和缓存数据不变。

## 测试与验收

测试应证明：

- 公开 Properties 不含 `log_koa_selected`；
- 公开 Properties 不含 `koawin_log_kaw`；
- `log_kaw` 的值等于变更前 `koawin_log_kaw` 的值；
- `log_kaw`、TPSA、MR 严格位于 `log_koa_units` 与
  `melting_point_selected` 之间；
- Excel 表头与 Properties DataFrame 的字段名和顺序一致；
- 内部 KOAWIN 提取、系数关系校验和 Warnings 保持现有行为；
- 页面说明表包含 logKow、logKoa、logKaw、TPSA、MR 和 logKoc 的批准文本；
- `ENDPOINT_KEYS` 不增加 logKoa、logKaw、TPSA、MR；
- 聚焦 EPI 测试、完整测试套件、模块编译和差异检查全部通过。

## 非目标

- 不删除其他 selected、estimated、experimental、type 或 units 字段；
- 不改变 TPSA、MR 或 logKAW 的计算方法；
- 不改变 logKoc 结果值选择逻辑；
- 不动态删除全空列；
- 不调整其他结果表或桌面安装包。
- 不把页面说明新增项扩展为核心查询、补充导入或结果池字段。
