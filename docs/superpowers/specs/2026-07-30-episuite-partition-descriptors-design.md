# EPI Suite 分配系数与 RDKit 描述符扩展设计

## 目标

扩展第三页“EPI Suite 环境归趋”的在线 API 结果，在现有
`Properties / 理化性质` 数据集中同时展示三组分配系数的 log10 值和
原始系数，并使用项目已有的 RDKit 根据查询结构计算 TPSA 和 MR。

页面结果与 `EPISuite_Fate_Report.xlsx` 必须使用同一份规范化数据，
保持字段、数值和缺失状态一致。

## 本次范围

新增以下八个可见数值字段：

| 内部列名 | 页面/Excel 标签 | 数据来源 |
| --- | --- | --- |
| `koawin_log_kow` | `logKOW（KOAWIN估算）` | KOAWIN 使用的 KOW |
| `koawin_kow` | `KOW（KOAWIN估算）` | `logKoa.estimatedValue.model.kow` |
| `koawin_log_koa` | `logKOA（KOAWIN估算）` | KOAWIN 结果 |
| `koawin_koa` | `KOA（KOAWIN估算）` | `logKoa.estimatedValue.model.koa` |
| `koawin_log_kaw` | `logKAW（KOAWIN估算）` | KOAWIN 使用的 KAW |
| `koawin_kaw` | `KAW（KOAWIN估算）` | `logKoa.estimatedValue.model.kaw` |
| `tpsa_rdkit_a2` | `TPSA（Å²，RDKit）` | RDKit TPSA |
| `mr_rdkit_cm3_mol` | `MR（cm³/mol，RDKit）` | RDKit Wildman-Crippen MR |

六个分配系数字段只加入 `Properties / 理化性质`。不在
`Core_Summary` 中重复，也不新增独立工作表。

## 明确排除

- 本次不查询、估算或展示汽化焓 `ΔHvap`。
- 不为 `ΔHvap` 增加空占位列。
- 不接入 PubChem、NIST 或其他外部实验数据库。
- 不删除或重命名现有 logKOW、logKOA、Henry 常数的所选值、估算值、
  实验值、来源类型或兼容字段。
- 不改变现有 EPI API 请求、CAS 到 SMILES 回退、批处理、缓存和检查点逻辑。
- 不将 RDKit TPSA/MR 表述为 EPI Suite API 结果。

## 分配系数数据契约

### 数据源

以 EPI Web API 的同一组 KOAWIN 模型结果为首选来源：

```text
logKoa.estimatedValue.model.kow
logKoa.estimatedValue.model.kaw
logKoa.estimatedValue.model.koa
logKoa.estimatedValue.model.logKoa
```

新列使用 `koawin_` 前缀，避免与当前 `log_kow_selected`、
`log_kow_estimated`、`log_kow_experimental`、`log_koa_selected` 等字段
混淆。现有字段继续表示各自模型段中的 selected / estimated /
experimental 语义；新字段表示 KOAWIN 计算时实际使用的一组分配系数。

### 数值关系

对正的有限系数，每组 log10 值与对应原始系数必须满足：

```text
koawin_log_kow = log10(koawin_kow)
koawin_log_koa = log10(koawin_koa)
koawin_log_kaw = log10(koawin_kaw)
```

三个原始系数优先保留 API 值；三个配对的 log10 字段由对应的正系数
计算，以保证每一对显示值内部一致。若 API 缺少原始系数而存在可用的
有限 log10 值，可由 `10 ** log10值` 恢复原始系数，再生成配对的 log10
字段。若输入值非数值、非有限值或原始系数小于等于零，不执行对数
计算，相应字段保持缺失。

按定义，KOAWIN 的三组系数应满足 `KOA = KOW / KAW`。程序在浮点容差内
检查这一关系；若 API 返回的三个原始系数不一致，不静默改写任一原始
系数，而是在 `Warnings` 中记录字段和化合物。API 单独返回的 logKOW 或
logKOA 与对应系数不一致时同样记录警告，现有模型字段仍保留其 API 值。

### 精度和显示

- DataFrame 和 Excel 单元格保留浮点数，不提前转成格式化字符串。
- 页面上的 log10 值采用常规十进制显示。
- 页面上的原始系数允许采用科学计数法，以适应极大或极小的分配系数。
- 显示格式不得改变底层值、Excel 数值类型或后续排序精度。

## RDKit 描述符数据契约

### 结构来源优先级

TPSA 和 MR 使用与本次 EPI 结果关联的结构，SMILES 选择顺序为：

1. API `chemicalProperties.smiles`；
2. 原始结果行保存的 `epi_smiles`；
3. 用户输入行的 `smiles`。

这一顺序保证 CAS、名称或回退查询成功后，优先使用 API 实际解析并
返回的标准化结构。仅名称或 CAS 输入的行只要 API 返回有效 SMILES，
也可以计算 TPSA 和 MR。

### 计算方法

- 使用 `rdkit.Chem.rdMolDescriptors.CalcTPSA` 的默认口径计算 TPSA。
- 使用 RDKit Wildman-Crippen MR 计算分子折射率。
- 输出保持数值类型，并在列名和页面标签中明确标记 `RDKit`。
- 不将 RDKit 结果写回 EPI 原始 JSON，也不作为 EPI 模型的输入参数。

### 缺失和失败

如果三种 SMILES 来源均为空，或 RDKit 无法解析最终 SMILES：

- `tpsa_rdkit_a2` 和 `mr_rdkit_cm3_mol` 保持缺失；
- 在 `Warnings` 中记录“RDKit 描述符未计算”及原因；
- 不将该化合物标记为 EPI 查询失败；
- 不影响该行其他理化性质、环境归趋和生态毒性结果。

## 数据流和展示

`build_epi_web_result_tables(...)` 继续作为在线结果的统一规范化入口。
`_build_properties_row(...)` 负责：

1. 提取现有理化性质；
2. 提取并校验 KOAWIN 的六个新字段；
3. 选择结构并计算两个 RDKit 描述符；
4. 将非致命解析或计算问题交给警告收集逻辑。

第三页继续从 `Properties` 表渲染“理化性质”标签。工作簿生成继续直接
写入同一 `Properties` DataFrame，因此页面与 Excel 不维护两套字段
映射。八个新字段在表中按 KOW、KOA、KAW、TPSA、MR 的顺序相邻排列。

`Raw_API_JSON` 保持不变，继续保存 API 原始响应供审计；RDKit 计算值只
存在于规范化 `Properties` 结果中。

## 兼容性

- 当前 `EPI_REPORT_HIDDEN_COMPAT_COLUMNS` 行为不变。
- 当前核心摘要字段及其 selected / estimated / experimental 含义不变。
- 旧 API 响应缺少 `logKoa.estimatedValue.model` 时，新分配系数字段为空，
  其他表仍正常生成。
- 外部 EPI Suite 文件导入若不包含这些结构化字段，保持为空，不伪造
  在线 API 结果。
- 已缓存的旧原始 JSON 可在重新构建分类表时获得新字段；不要求改变缓存键。

## 验证

### 单元测试

- 固定 API 响应夹具能提取六个 KOAWIN 字段。
- 三组 log10 值与原始系数满足定义关系。
- 缺少原始系数但存在可用 log10 值时可以恢复；非正、非有限和非数值
  系数保持缺失。
- `KOA = KOW / KAW` 在容差内通过检查；不一致时保留 API 原始系数并
  产生警告。
- API 返回值不一致时产生警告，不静默改写原始系数。
- TPSA/MR 优先使用 API 标准化 SMILES，并能回退到输入 SMILES。
- 仅名称或 CAS 查询在 API 返回 SMILES 后可计算 TPSA/MR。
- 无效 SMILES 只产生描述符警告，不使 EPI 结果失败。
- 现有 selected / estimated / experimental 字段保持不变。

### 页面和导出

- `Properties` 页面表包含八个新列，顺序与设计一致。
- `EPISuite_Fate_Report.xlsx` 的 `Properties` 工作表包含相同字段和数值。
- 新字段保持数值类型，原始系数可使用科学计数法显示。
- `Core_Summary` 不重复新增这些字段。
- `Raw_API_JSON` 未被 RDKit 结果修改。

### 回归检查

实施完成后至少运行：

```text
python -m unittest tests.test_episuite_cas_values -v
python -m unittest discover -s tests -v
python -m compileall app.py pages src
git diff --check
```

若第三页现有 AppTest 支持注入已完成查询状态，还应验证“理化性质”标签
实际渲染八个新字段；否则使用结果表和工作簿的端到端测试覆盖同一数据流。
