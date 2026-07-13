# MOL 结构解析与 SMILES 工作流整合设计

## 目标

让用户上传的 Excel 表中每一行 MOL 文本都能在四个工作流的起点被解析为 SMILES，并将经过明确决策的有效 `smiles` 传递到后续标识符补全、数据库查询、EPI 预测和综合筛查步骤。

覆盖当前界面中的：综合筛查流程、EPI Suite 环境归趋、化合物用途查询和一键批量查询。

## 方案选择

采用共享的“结构准备”模块，而不是在四个页面重复实现解析和冲突处理。

- 方案 A：分别在四页处理 MOL。实现快，但规则、提示和导出容易漂移。
- 方案 B：扩展现有 `src/mol_structure_parser.py`，页面只负责选择输入列、展示结果和传递数据。采用此方案；规则一致，单元测试覆盖集中。
- 方案 C：只在标识符补全模块解析 MOL。会遗漏 EPI-only 和综合筛查的直接路径，且上传时无法展示解析质量。

## 结构准备接口

在 `src/mol_structure_parser.py` 提供面向 DataFrame 的共享接口。它保留全部原始列和行顺序，并输出：

- `parsed_smiles`、`parsed_isomeric_smiles`、`parse_status`、`parse_warnings` 等既有解析审计字段；
- 标准 `smiles`：唯一供下游消费的有效结构标识；
- `smiles_source`：`原始 SMILES`、`MOL 解析`、`原始 SMILES（与 MOL 一致）`、`原始 SMILES（与 MOL 冲突）` 或空值；
- `smiles_decision_warning`：原始 SMILES 无效、MOL 解析失败、两种结构冲突等可审计提示。

MOL 文本列默认识别 `mol_text`、`mol`、`molfile`、`structure`（忽略大小写和两端空白）；页面允许用户从实际列中显式选择，覆盖自动识别。

原始 SMILES 列采用应用当前各页面的既有别名规则（如 `smiles`、`SMILES`、`canonical_smiles`），并允许页面映射显式指定。MOL 文本中缺失的 `M  END` 自动补齐后继续解析；每行失败仅写入状态和告警，不中断其它行。

## SMILES 决策规则

比较时使用 RDKit 解析后的 canonical isomeric SMILES，而不是直接比较字符串，避免等价写法造成误报。

| 原始 SMILES | MOL 解析 | 标准 `smiles` | 行为 |
| --- | --- | --- | --- |
| 空 | 成功 | MOL 解析 SMILES | 标记为 `MOL 解析` |
| 有效 | 无 MOL 或 MOL 失败 | 原始 SMILES | 保留原始值；MOL 失败时记录告警 |
| 有效 | 成功且结构相同 | 原始 SMILES | 标记为 `原始 SMILES（与 MOL 一致）` |
| 有效 | 成功但结构不同 | 原始 SMILES | 标记为 `原始 SMILES（与 MOL 冲突）`，允许继续 |
| 无效 | 成功 | MOL 解析 SMILES | 标记原始 SMILES 无效并使用 MOL 结果 |
| 无效或空 | 失败或缺失 | 空 | 保留解析状态和告警 |

“原始 SMILES”包括用户在输入表中已有的 SMILES，不会被 MOL 解析结果静默覆盖。冲突不阻塞整批任务。

## 页面接入与数据传递

四个页面均在读取 Excel 后、任何标准化或下游调用前运行结构准备。页面显示一段简洁汇总：MOL 行数、成功数、补齐 `M END` 数、冲突数和失败数；存在异常时显示可展开的行级审计表。

1. 综合筛查流程：每个上传工作簿的列映射增加可选的 MOL 文本列。结构准备后的标准 `smiles` 进入代表性表，继而进入标识符补全、EPI 和 Pov-LRTP/ToxPi。
2. EPI Suite 环境归趋：上传后先结构准备，再用标准 `smiles` 通过既有 EPI 校验和网页端预测。
3. 化合物用途查询：上传后结构准备；标识符补全与 EPA/ECHA 查询均接收标准 `smiles`。
4. 一键批量查询：自动检测或在列映射中选择 MOL 文本列；标准 `smiles` 进入代表性表、标识符补全和已选下游模块。

原始表、结构准备表及最终查询输入分别保存在页面状态中，避免 Streamlit 重运行时混用旧文件的结果。所有导出工作簿增加“结构解析与 SMILES 决策”表；一键流程另在结果包中包含该表。

## 名称与 SMILES 双通道查询

对具有名称和标准 `smiles` 的同一行，标识符补全和用途查询分别尝试名称与 SMILES。每条候选结果携带 `query_source`（名称或 SMILES）和原始查询值。

- 同一服务、同一化学实体命中的重复候选按稳定标识符优先去重：DTXSID，其次 ECHA ID、EC、CAS、canonical isomeric SMILES，最后规范化名称。
- 两种查询命中不同实体时，不静默混合身份信息：结构相关的主记录选择 SMILES 命中；名称命中的用途证据作为“名称匹配候选”保留，并附冲突告警。
- 只有名称或只有 SMILES 时，只运行可用通道。
- EPI、环境归趋和筛查等结构相关下游步骤只使用标准 `smiles`；用途查询界面与导出同时保留合并候选和来源，以供复核。

## 错误处理与兼容性

- 保持现有标准标识列 `compound`、`smiles`、`cas`、`ec`、`dtxsid`、`echa_id` 的接口不变。
- 不要求用户修改 Compound Discoverer 导出的列名；缺少可识别 MOL 列时保持现有路径。
- 缺少 `M  END` 的修复、单行结构损坏、无 SMILES 的行和外部服务查询失败均为可见的行级提示，不让无关记录失败。
- 不改变外部数据库的请求配置、缓存、并发或超时默认值。

## 验收与测试

测试覆盖以下内容：

1. 结构准备对只有 MOL、只有原始 SMILES、相同结构、冲突结构、无效原始 SMILES和解析失败行的决策结果。
2. canonical isomeric 比较识别不同 SMILES 写法对应的同一结构。
3. 各标准化路径接收决策后的 `smiles` 且保留输入行数、原始列和审计列。
4. 标识符解析的名称/SMILES 双通道候选合并、去重、冲突标记和主记录选择。
5. 四个 Streamlit 页面在读取上传文件后调用共享结构准备接口，并将结果传给现有下游函数。
6. 全仓库单元测试、页面/源码编译检查，以及提供的 Compound Discoverer 测试工作簿的端到端解析验证。
