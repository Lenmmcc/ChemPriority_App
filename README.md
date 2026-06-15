# ChemPriority 新手操作文档

ChemPriority 是一个面向污染物优先控制筛选的网页工具，用于整理化合物输入表、计算 ToxPi 毒性综合得分、预测 EPI Suite 环境归趋指标，并查询 EPA CompTox 与 ECHA CHEM 中的化合物用途证据。

本文档面向首次使用者，重点说明如何准备 Excel、进入各功能页面、完成操作并下载结果。

## 1. 开始使用

如果已经部署到服务器，请直接打开管理员提供的网址。进入网页后，左侧导航栏会显示 4 个功能页面：

1. `ADMETlab毒性数据获取`
2. `ToxPi毒性评估`
3. `EPISuite环境归趋`
4. `化合物用途查询`

如果需要在本机临时运行，请在项目根目录安装依赖后启动：

```bash
pip install -r requirements.txt
streamlit run app.py
```

启动后，浏览器会打开 ChemPriority 主页面。

## 2. 使用前准备

所有输入文件均使用 Excel 格式，推荐使用 `.xlsx`。请尽量保持第一行为字段名，每一行代表一个化合物。

| 功能模块 | 必需或建议字段 | 说明 |
| --- | --- | --- |
| ADMETlab 毒性数据获取 | `compound`, `smiles` | `compound` 为化合物名称，`smiles` 为结构式字符串。 |
| ToxPi 毒性评估 | `compound` + 至少 1 个数值型毒性指标列 | 系统会自动识别可转为数字的指标列，再由用户选择本次纳入计算的指标。 |
| EPI Suite 环境归趋 | `compound`, `smiles` | 用于调用 EPI Web Suite 或生成备用输入包。 |
| 化合物用途查询 | `compound`, `cas`, `ec`, `smiles`, `dtxsid`, `echa_id` | 不要求全部都有。只有 `smiles` 时建议先运行“标识符补全”。 |

ToxPi 示例表可参考项目中的 `data/data.xlsx`。其中包含 `compound`、`hERG Blockers-hERG`、`DILI`、`oral cavity`、`Skin sensitivity`、`carcinogenicity` 等毒性指标列。

使用前请检查：

- `compound` 不要为空，建议不要重复。
- `smiles`、`cas`、`ec`、`dtxsid`、`echa_id` 等标识符尽量准确。
- ToxPi 指标列必须能转为数字，空值过多会影响计算。
- 涉及 EPA、ECHA、PubChem、EPI Web Suite 的功能依赖外部网络，查询可能较慢，也可能因接口变化或网络限制失败。

## 3. ADMETlab 毒性数据获取

该页面用于整理和校验后续提交 ADMETlab 的输入表。目前 ADMETlab 自动连接路线尚未启用，页面主要提供输入校验和已校验表下载。

操作步骤：

1. 进入左侧导航栏的 `ADMETlab毒性数据获取`。
2. 点击右侧 `下载 Excel 模板`，可先下载 `ADMETlab_Input_Template.xlsx` 作为填写参考。
3. 准备包含 `compound` 和 `smiles` 两列的 Excel。
4. 在 `上传 Excel 文件` 处上传输入表。
5. 如果页面提示输入数据检查通过，可在 `输入数据` 标签页查看待提交化合物。
6. 在 `ADMETlab 连接` 标签页查看当前连接状态说明。
7. 在 `结果下载` 标签页点击下载，得到 `ADMETlab_Validated_Input.xlsx`。

常见提示：

- 缺少 `compound` 或 `smiles` 时，页面会提示缺少必要列。
- `compound` 或 `smiles` 有空值时，请先回到 Excel 中补齐或删除对应行。
- `compound` 重复时，请确认是否需要合并、重命名或保留重复记录。

## 4. ToxPi 毒性评估

该页面用于根据毒性指标计算 ToxPi 综合得分，生成风玫瑰图、柱状图，并进行排序稳健性分析。

操作步骤：

1. 进入左侧导航栏的 `ToxPi毒性评估`。
2. 在左侧 `ToxPi 控制台` 中上传污染物原始数据 Excel。
3. 确认表格包含 `compound` 列，以及至少 1 个数值型毒性指标列。
4. 在 `选择要纳入本次 ToxPi 计算的指标` 中勾选本次参与计算的指标。默认会选中系统识别到的全部数值型指标。
5. 在 `毒性因子权重` 中为每个指标设置权重。权重越高，该指标对综合得分影响越大。
6. 在 `稳健频次统计阈值 (Top K)` 中设置关注前多少名化合物。
7. 在 `蒙特卡洛随机种子列表` 中保留默认种子，或输入多个整数种子。
8. 如需区分化合物类别，可展开 `种类划定与分组配色`，设置分组名称和柱状图颜色。
9. 在主页面查看 3 个标签页：
   - `数据审查`：查看原始数据、归一化数据和 ToxPi 得分。
   - `ToxPi 图谱`：查看风玫瑰图和综合得分柱状图。
   - `排序稳健性`：查看蒙特卡洛扰动下的排序稳定性结果。

可下载结果：

| 下载内容 | 文件名 |
| --- | --- |
| 美化版 ToxPi 风玫瑰图 | `ToxPi_Plot_Beautified.pdf` |
| 原始版 ToxPi 风玫瑰图 | `ToxPi_Plot_Original.pdf` |
| 综合得分柱状图 | `ToxPi_Bar_Plot_Group_Colors.pdf` |
| 不同随机种子的敏感性分布图 | `Sensitivity_Distribution_seed_*.pdf` |
| 完整计算报告 | `ToxPi_Calculated_Report.xlsx` |

注意事项：

- 至少选择 1 个毒性指标，且所有权重之和必须大于 0。
- 如果所有化合物的毒性指标都为空，系统无法计算。
- ToxPi 得分越高，表示在当前指标和权重设置下综合优先级越高。
- 权重设置会影响排序，建议在报告中记录采用的指标和权重。

## 5. EPI Suite 环境归趋预测

该页面用于批量计算或整理 EPI Suite 环境归趋指标，包括物化性质、降解、生物富集和环境介质分配等结果。

操作步骤：

1. 进入左侧导航栏的 `EPISuite环境归趋`。
2. 点击右侧 `下载 Excel 模板`，可获得 `EPISuite_Input_Template.xlsx`。
3. 准备包含 `compound` 和 `smiles` 两列的 Excel。
4. 上传 Excel 后，页面会显示目标环境归趋指标，并检查输入数据。
5. 在 `输入数据` 标签页确认待预测化合物。

在线预测方式：

1. 打开 `网页端预测` 标签页。
2. 保持默认 `EPI Web Suite API 地址`，除非管理员要求修改。
3. 根据网络情况设置 `单个化合物超时时间（秒）` 和 `请求间隔（秒）`。
4. 点击 `开始网页端预测`。
5. 等待进度条完成，查看网页端预测结果和失败记录。

备用方式：

1. 如果在线预测不可用，打开 `备用输入包` 标签页。
2. 点击下载 `EPISuite_Input_Package.zip`。
3. 将输入包中的 SMILES 信息复制到外部 EPI Suite 或 EPI Web Suite 中计算。
4. 保存外部结果文件。
5. 回到 `解析外部结果` 标签页，上传 CSV、Excel、TXT 或 DOC 结果文件。
6. 页面会将外部结果解析为结构化表格，并与原始输入合并。

可下载结果：

| 下载内容 | 文件名 |
| --- | --- |
| EPI Suite 输入模板 | `EPISuite_Input_Template.xlsx` |
| EPI Suite 备用输入包 | `EPISuite_Input_Package.zip` |
| 环境归趋结果工作簿 | `EPISuite_Fate_Report.xlsx` |

注意事项：

- 在线预测依赖 EPI Web Suite 网页端 API，服务器必须能访问对应地址。
- 单个化合物可能因为 SMILES 不合法、网络超时或外部服务异常而失败。
- 失败记录会保存在页面和下载报告中，建议逐条检查。

## 6. 化合物用途查询

该页面用于查询化合物用途证据。系统可先补全标识符，再分别从 EPA CompTox Dashboard 和 ECHA CHEM 查询用途信息，并按证据强度保留前五个用途。

操作步骤：

1. 进入左侧导航栏的 `化合物用途查询`。
2. 根据已有数据下载合适模板：
   - `Identifier_Completion_Input_Template.xlsx`：用于标识符补全。
   - `EPA_CompTox_Use_Input_Template.xlsx`：用于 EPA CompTox 查询。
   - `ECHA_Use_Input_Template.xlsx`：用于 ECHA 查询。
3. 上传包含 `compound`、`cas`、`ec`、`smiles`、`dtxsid` 或 `echa_id` 中任意可用字段的 Excel。
4. 在 `输入数据` 标签页检查原始表、补全标准列、EPA 标准列和 ECHA 标准列。

建议流程：

1. 如果只有 `smiles` 或标识符不完整，先打开 `标识符补全` 标签页。
2. 默认勾选 `使用 EPA 补全 DTXSID/CAS`、`使用 PubChem 从 SMILES 预补全`、`使用 ECHA 补全 EC/ECHA ID`。
3. 点击 `开始补全标识符`，等待补全完成。
4. 补全后，EPA/ECHA 查询会自动使用补全后的标识符表。
5. 打开 `EPA CompTox 查询` 标签页，确认参数后点击 `开始查询用途`。
6. 打开 `ECHA 查询` 标签页，确认参数后点击 `开始 ECHA 查询用途`。
7. 在 `结果下载` 标签页下载各类报告。

可下载结果：

| 下载内容 | 文件名 |
| --- | --- |
| 标识符补全结果 | `Identifier_Completion_Report.xlsx` |
| EPA CompTox 用途查询结果 | `CompTox_Use_Category_Report.xlsx` |
| ECHA REACH 用途证据结果 | `ECHA_REACH_Use_Evidence_Report.xlsx` |

注意事项：

- EPA 查询优先使用 `dtxsid`，没有时会尝试用 CAS、compound 或 SMILES 匹配。
- ECHA 查询更依赖 `echa_id`、`ec`、CAS 或明确名称；只有 SMILES 时稳定性较弱。
- 查询结果会保留用途候选、证据数量、失败记录和来源信息。
- 用途分类来自外部数据库和页面解析，正式使用前建议人工核对原始证据链接和英文用途描述。

## 7. 结果文件总览

| 模块 | 主要结果文件 | 用途 |
| --- | --- | --- |
| ADMETlab 毒性数据获取 | `ADMETlab_Validated_Input.xlsx` | 保存已通过校验的 ADMETlab 输入清单。 |
| ToxPi 毒性评估 | `ToxPi_Calculated_Report.xlsx` | 保存 ToxPi 得分、稳健性分析和本次指标信息。 |
| ToxPi 毒性评估 | `ToxPi_Plot_Beautified.pdf`, `ToxPi_Plot_Original.pdf`, `ToxPi_Bar_Plot_Group_Colors.pdf` | 保存 ToxPi 图谱和柱状图。 |
| EPI Suite 环境归趋 | `EPISuite_Fate_Report.xlsx` | 保存输入表、环境归趋结果、合并结果和解析提示。 |
| 化合物用途查询 | `Identifier_Completion_Report.xlsx` | 保存 PubChem、EPA、ECHA 标识符补全结果和提示。 |
| 化合物用途查询 | `CompTox_Use_Category_Report.xlsx` | 保存 EPA CompTox 用途结果、全部候选用途和失败记录。 |
| 化合物用途查询 | `ECHA_REACH_Use_Evidence_Report.xlsx` | 保存 ECHA 用途证据、候选用途、dossier 信息和失败记录。 |

## 8. 常见问题

### 上传后提示缺少必要列怎么办？

请检查 Excel 第一行字段名是否正确。常用字段包括 `compound`、`smiles`、`cas`、`ec`、`dtxsid`、`echa_id`。字段名前后不要有多余空格。

### ToxPi 页面没有识别到毒性指标怎么办？

请确认除 `compound` 外，至少有一列毒性指标可以转为数字。不要把数值写成带单位或说明文字的格式。

### 为什么外部查询很慢？

EPI Suite、EPA CompTox、ECHA 和 PubChem 查询都依赖外部网站或接口。化合物数量越多，耗时越长。建议先用少量样品测试，确认网络和字段无误后再批量运行。

### 查询完成但有失败记录，结果还能用吗？

可以先查看成功记录，但失败行需要单独核对。失败原因可能是网络超时、外部接口不可用、标识符无法匹配或化合物未被数据库收录。

### 是否可以只用部分模块？

可以。四个页面相互独立。若已有毒性指标，可直接使用 ToxPi；若只需要用途证据，可直接使用化合物用途查询。

### 输出结果是否可以直接作为最终结论？

不建议直接作为最终结论。ChemPriority 用于批量整理、评分和证据筛选，最终报告前仍应人工核对输入数据、权重设置、外部数据库证据和异常记录。
