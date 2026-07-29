# EPI Suite 仅名称查询设计

## 目标

允许 EPI Suite 查询输入只包含化合物名称。`name`、`chemical_name` 等现有别名继续归一化为 `compound`；`smiles` 和 `cas` 改为可选字段。

仅名称输入必须先解析为 EPI Suite 可提交的化学结构。系统不得对模糊名称自动猜测。

## 范围

本次修改覆盖所有复用 `src.episuite_io.run_epi_web_batch` 的 EPI Suite 查询入口，包括：

- 第三页“EPI Suite 环境归趋”；
- 综合筛查流程；
- 一键批量查询及失败重试流程。

同时调整第三页的输入提示、模板和备用输入包，确保名称-only 数据通过校验后不会在预览、导出或查询阶段报错。

不修改 EPI Suite 结果字段、现有 CAS 回退规则或环境归趋计算逻辑。

## 方案选择

采用 EPI Suite 官方名称搜索接口，而不是先通过 PubChem 补全，也不要求用户逐行选择候选。

原因：

- 与 EPI Suite 官网的名称检索和身份选择数据源一致；
- 搜索结果直接提供 EPI Suite 使用的名称、CAS 和 SMILES；
- 批量查询无需人工干预；
- 严格精确匹配可以避免模糊名称自动命中错误实体。

## 输入契约

### 列要求

- 必需列：`compound`；
- 可选列：`smiles`、`cas`；
- `name`、`compound_name`、`chemical`、`chemical_name` 等继续归一化为 `compound`。

### 行要求

- `compound` 不得为空；
- `compound` 继续保持当前的重复名称检查；
- `smiles` 为空是合法输入；
- 有效 SMILES 存在时，优先沿用当前直接提交路径，不执行名称搜索。

## 查询流程

每行按以下顺序处理：

1. 清理 `compound`、`smiles` 和 `cas`。
2. 如果存在 SMILES，按现有逻辑提交 SMILES，并在有 CAS 时同时提交 CAS。
3. 如果缺少 SMILES，使用 `compound` 请求 EPI Suite 官方 `/api/search` 接口。
4. 对候选名称执行“去除首尾空格 + 忽略大小写”的完全一致比较，不做包含匹配、拼写纠正或其他模糊处理。
5. 如果存在多个完全一致候选，使用 EPI Suite 官方返回顺序中的第一个。
6. 候选必须包含有效 SMILES；候选 CAS 可为空。
7. 使用解析得到的 SMILES，并在存在 CAS 时同时提交 CAS。
8. 结果继续保留用户输入的 `compound`，同时记录实际提交的 SMILES、CAS 和名称解析说明。

官方搜索地址从提交地址的同源 `/api/search` 路径获得。默认提交地址仍为 `https://episuite.dev/api/submit`。自定义提交地址使用同源的相邻搜索路径，避免名称查询绕回默认服务。

## 失败与重试

以下情况按单行失败处理，不影响同批次其他行：

- 搜索结果为空；
- 没有名称完全一致的候选；
- 完全一致候选缺少 SMILES；
- 搜索接口返回 HTTP、网络或解析错误；
- 名称解析成功，但 EPI Suite 提交失败。

错误表必须写明失败阶段和原因。名称没有精确匹配时不得回退到搜索结果第一项，也不得自动改用 PubChem。

搜索和提交继续使用项目现有缓存、顺序保持、并发控制及瞬时错误重试框架。缓存键必须区分搜索词、搜索地址和结果数量限制。

现有 CAS 回退规则保持不变：仅当错误同时表示 HTTP 404 和 `Could not locate CAS ID`（或现有兼容的等价错误）时，才用同一 SMILES 去掉 CAS 后重试。

## 输出与可追溯性

成功行：

- `compound`：用户输入的原始名称；
- `smiles`：实际提交的 SMILES；
- `cas`：实际提交的 CAS；没有则为空；
- `query_note`：说明该结构由 EPI Suite 名称精确匹配获得，并记录匹配名称。

失败行继续进入结果表和错误表，状态为 `failed`，端点值为空。

原始结果表继续保存 EPI 返回的 `epi_cas`、`epi_smiles` 和完整 JSON。

## 页面与备用输入包

第三页调整为：

- 上传说明明确支持仅含 `name` 或 `compound` 的 Excel；
- 模板包含名称、SMILES、CAS 示例，并明确后两列可留空；
- 预览允许不存在 `smiles` 列；
- 查询说明解释名称精确匹配规则。

备用输入包保持现有文件兼容，并处理名称-only 行：

- `episuite_input.csv` 保留原始名称、可选 SMILES 和可选 CAS；
- SMILES-only 文件只包含已有 SMILES 的行；
- 新增可直接用于 EPI Suite 批量输入的检索词文件：有 SMILES 时写 SMILES，否则写名称；
- README 说明各文件适用场景和名称精确匹配限制。

## 一键批量查询兼容

一键批量查询不得再因所有 SMILES 为空而跳过可用的名称-only EPI 行。可查询条件改为：

- 有效 SMILES；或
- 有效 `compound` 名称。

失败重试采用相同条件。上传补充结果、结果池合并和文件名关联规则不变。

## 测试

按测试驱动方式增加回归覆盖：

1. `name` 列可以归一化并通过输入校验；
2. `compound` 存在而 `smiles` 缺失时可以通过校验；
3. 名称搜索忽略大小写和首尾空格进行完全一致匹配；
4. 多个完全一致候选时使用第一个；
5. 没有完全一致候选时失败且不调用提交接口；
6. 精确候选缺少 SMILES 时失败；
7. 名称搜索异常被记录，并与其他行隔离；
8. 名称解析成功后提交解析出的 SMILES 和 CAS；
9. 结果及原始追踪表记录实际提交标识符和查询说明；
10. 混合名称-only、SMILES-only 和 SMILES+CAS 输入保持原顺序；
11. 备用 ZIP 在名称-only 输入下可以生成；
12. 一键批量查询和失败重试允许名称-only 行进入 EPI 查询；
13. 原有 CAS 404 到 SMILES 的回退测试保持通过。

验证顺序：

1. 新增专项测试先失败；
2. 最小实现后专项测试通过；
3. EPI Suite 相关测试全部通过；
4. 全量 `unittest` 回归通过；
5. `python -m compileall app.py pages src` 通过；
6. `git diff --check` 通过。

## 验收标准

- 只有 `name` 或 `compound` 列的有效 Excel 可以在第三页通过校验并发起查询；
- 名称完全一致时，使用 EPI Suite 返回的 CAS 和 SMILES完成预测；
- 非完全一致名称不会被自动猜测；
- 单行名称解析失败不会终止整个批次；
- 综合筛查与一键批量查询共享相同行为；
- 既有 SMILES/CAS 查询、CAS 回退、结果表和导出行为无回归。
