# ChemPriority

ChemPriority 是一个面向污染物优先控制筛选的 Streamlit 工具。它把数据整理、ToxPi 综合评分、环境归趋预测和公开用途证据查询放在四个独立页面中，便于将化合物清单转化为可复核的筛选结果。

> 适合用于科研或业务中的**数据整理、候选物排序和证据初筛**。页面中的预测结果、用途候选和综合分数都不应直接替代人工核验、正式风险评估或监管结论。

## 项目用途与适用范围

| 模块 | 解决的问题 | 最小输入 | 主要输出 |
| --- | --- | --- | --- |
| ADMETlab 毒性数据获取 | 整理并校验待提交至 ADMETlab 的化合物清单 | `compound`、`smiles` | `ADMETlab_Validated_Input.xlsx` |
| ToxPi 毒性评估 | 按选择的毒性指标与权重计算综合分数，并检验排序稳健性 | `compound` + 至少 1 个数值型毒性指标 | Excel 报告及 ToxPi、柱状图、敏感性 PDF |
| EPI Suite 环境归趋预测 | 批量请求 EPI Web Suite，或整理外部 EPI Suite 结果 | `compound`、`smiles` | `EPISuite_Fate_Report.xlsx` |
| EPA/ECHA 用途查询 | 补全标识符，查询 EPA CompTox / ECHA CHEM 用途证据、ECHA GHS/C&L 危害分类和来源属性证据 | `compound`、`cas`、`ec`、`smiles`、`dtxsid`、`echa_id` 中任一可用字段 | 标识符、CompTox、ECHA 用途、ECHA GHS 和来源属性工作簿；用途图 |

四个模块可以独立使用。例如，已有毒性数值时可直接进入 ToxPi；只需要用途证据时可直接进入“化合物用途查询”。

## 快速开始

### 本地运行

在项目根目录执行。请使用一个能够安装 [requirements.txt](requirements.txt) 中依赖的 Python 环境。

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
~~~

浏览器打开后，从左侧导航栏进入相应页面。若已有虚拟环境，只需激活环境并执行后两行命令。

### 在线部署使用

若管理员已部署应用，直接打开其提供的网址即可。浏览器无需安装本项目依赖；但 EPI Web Suite、PubChem、EPA CompTox、ECHA CHEM 和 ChemSpider（可选）等功能需要部署服务器能够访问外部服务。

## 四个模块速查

### 1. ADMETlab 毒性数据获取

- 下载 `ADMETlab_Input_Template.xlsx`，填写 `compound` 和 `smiles`。
- 上传后，页面检查必需列、空值和重复的化合物名称。
- 当前版本**不自动提交或下载 ADMETlab 预测结果**；它只导出已校验的 `ADMETlab_Validated_Input.xlsx`，供用户后续在 ADMETlab 平台处理。

### 2. ToxPi 毒性评估与排序稳健性分析

- 上传包含 `compound` 和数值型毒性指标的 Excel。项目自带的 [data/data.xlsx](data/data.xlsx) 可作为示例。
- 在侧栏选择参与计算的指标，调整权重、Top K 和蒙特卡洛随机种子；可选地设置化合物分组和柱状图配色。
- 输出包括 `ToxPi_Calculated_Report.xlsx`、美化版/原始风玫瑰图、综合得分柱状图和各随机种子的敏感性分布图。
- 得分仅反映**本次选择的指标与权重**下的相对优先级，不表示固有风险或监管优先级。

### 3. EPI Suite 环境归趋预测

- 使用 `compound` 与 `smiles` 两列；页面提供 `EPISuite_Input_Template.xlsx`。
- 可直接调用默认 EPI Web Suite 页面端接口进行批量预测。
- 服务不可用时，下载 `EPISuite_Input_Package.zip`，在外部 EPI Suite / EPI Web Suite 完成计算后，将 CSV、Excel、TXT 或 DOC 文本结果上传回页面解析。
- 最终下载 `EPISuite_Fate_Report.xlsx`，其中保留输入、合并结果、原始解析结果和解析提示。

### 4. EPA/ECHA 用途查询

- 输入至少包含 `compound`、`cas`、`ec`、`smiles`、`dtxsid` 或 `echa_id` 之一；字段越完整，匹配越稳定。
- 推荐先执行“标识符补全”，再分别运行 EPA CompTox、ECHA 用途、ECHA GHS危害和来源属性评估。
- EPA 默认通过 CompTox Dashboard 获取产品用途类别和化学功能用途；化学功能用途会单独列出表格，并保留 Dashboard 预测功能用途的 probability。ECHA 用途查询从 ECHA CHEM 及 REACH dossier 中提取用途证据。
- ECHA GHS危害查询从 C&L Inventory 读取协调分类或行业自分类，输出 GHS危害分层、H 语句、信号词和图标；临时网络中断、限流或服务端错误会自动重试。
- 来源属性评估会合并 EPA/ECHA 人为源证据与 ChEBI、COCONUT 天然源证据，输出天然源、人为源、兼具天然源和人为源或证据不足。
- 每个来源分别给出排序后的用途候选、完整候选清单、来源属性证据、提示/失败记录和可下载工作簿。用途风玫瑰图只对已得到用途结果的化合物绘制。

## 推荐使用路径

| 你的起点 | 推荐路径 |
| --- | --- |
| 已有化合物毒性数值 | 直接使用 ToxPi，选择指标和权重后导出报告。 |
| 只有化合物名称或 SMILES，需要用途信息 | 在用途查询页先补全标识符，再运行 EPA 与/或 ECHA 查询。 |
| 有 `compound + smiles`，需要环境归趋指标 | 使用 EPI Suite 页面；先尝试网页端预测，失败时走输入包和外部结果解析路线。 |
| 需要整理待预测的 ADMETlab 清单 | 使用 ADMETlab 页面做格式校验并下载已校验输入表。 |

## Excel 输入规则

- 推荐 `.xlsx` 格式；第一行为字段名，每一行表示一个化合物。
- `compound` 建议非空且唯一。对于 ADMETlab 和 EPI Suite，`compound` 与 `smiles` 都是必填项。
- ToxPi 会忽略常见元数据列，并将能转换成数值的其他列作为候选毒性指标；至少保留一个可用数值列。
- 用途查询支持 `compound`、`cas`、`ec`、`smiles`、`dtxsid`、`echa_id`。系统会统一字段名，但最好直接使用这些标准列名。
- 上传数据会在**同一浏览器会话**中保留，以便在该页面内切换标签页；点击页面中的“清空当前数据”会删除该页面的缓存和依赖结果。

## 管理员可选配置

ChemSpider 仅用于标识符补全阶段中 PubChem 未补齐的 CAS 或标准名称。没有配置密钥时，ChemSpider 选项会被禁用，其余功能仍可使用。

本地部署时，将密钥写入 `.streamlit/secrets.toml`；该文件已被 `.gitignore` 忽略，绝不能提交真实密钥。

~~~toml
CHEMSPIDER_API_KEY = "你的密钥"
~~~

托管部署时，请在对应平台的 Streamlit Secrets 中配置同名键。不要将密钥写入代码、Excel、截图、日志或导出的工作簿。

## 结果解释与使用边界

- 外部服务会限流、超时、改版或缺少目标化合物；查看下载工作簿中的提示、失败记录和原始候选，而非只看成功行。
- EPA 与 ECHA 的证据来源与覆盖范围不同，应分开阅读，不能将同名用途直接视为等价证据。
- ECHA GHS/C&L 结果是物质层面的危害分类，不是 REACH 用途证据，也不是地区赋存特征；无GHS数据或未分类不等于无危害，查询失败应查看提示/失败记录后重试。
- 来源属性是基于当前接入数据库的证据合并判断；证据不足不等于没有天然源或人为源，LOTUS 等天然产物库可作为后续人工复核来源。
- 用途图按用途候选的证据数量绘制；缺少有效数量时会采用等角度回退绘图。
- 使用结果前，应核对输入结构/标识符、匹配名称、原始英文用途或 dossier 链接，以及异常记录。

## 详细操作手册

[查看完整用户操作手册](docs/用户操作手册.md)：逐页操作步骤、下载文件说明、结果解释和故障排查。

## 项目结构

~~~text
app.py                       # Streamlit 首页
pages/                       # 四个业务页面
src/                         # ToxPi、EPI Suite、标识符和用途查询核心逻辑
data/data.xlsx               # ToxPi 示例数据
tests/                       # 回归测试
docs/用户操作手册.md          # 面向普通用户的完整手册
~~~
