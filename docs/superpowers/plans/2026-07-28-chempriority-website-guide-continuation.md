# ChemPriority 网站指南续写实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留原 DOCX 格式和第一部分内容的基础上，完成第 2—6 个模块的中文操作说明、最新界面截图、轻度校对和逐页视觉验证。

**Architecture:** 以桌面原 DOCX 为只读模板，在 `E:\pyproject\ToxPi_App\.codex_doc_work\guide` 中完成模板蒸馏、网站截图、文字稿、DOCX 构建和渲染检查。最终文件输出到工作区根目录，原桌面文件保持字节级不变。

**Tech Stack:** Microsoft Word、python-docx、OOXML、Streamlit、Microsoft Word COM、Poppler `pdftoppm`。

## Global Constraints

- 参考文件：`C:\Users\Administrator\Desktop\Chem_Priority网站指南.docx`。
- 参考文件 SHA-256：`47D10F277DC7DFB04ECD1FCF532BB99EA4FD5FF7E57DB1A2B1C9C6722E160F68`。
- 最终文件：`E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx`。
- 保留 A4、上下 2.54 cm、左右 3.18 cm、等线、正文约 11 磅、现有段落节奏和一级自动编号。
- 第一部分只做明显错字、大小写、标点和语句通顺校对，不改变技术结论、流程顺序、原图或截图顺序。
- 第 2—6 部分均使用“X.1 功能介绍 + X.2 操作”的结构。
- 截图来自当前本地 ChemPriority 网站，不将内部代码、调试信息或隐私数据带入文档。
- 不新增封面、目录、页眉、页脚、页码、复杂表格或装饰性视觉系统。
- 最终交付只包含 DOCX；PDF 和 PNG 仅用于内部检查。

---

### Task 1: 蒸馏原 DOCX 模板

**Files:**
- Read: `C:\Users\Administrator\Desktop\Chem_Priority网站指南.docx`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\artifact.md`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\template-style-evidence.json`

**Interfaces:**
- Consumes: 原 DOCX、参考文件 SHA-256。
- Produces: `artifact.md`，记录页面、段落、编号、图片和可编辑槽位；后续 DOCX 编辑必须遵守该契约。

- [ ] **Step 1: 复核参考文件未变化**

Run:

```powershell
Get-FileHash -Algorithm SHA256 'C:\Users\Administrator\Desktop\Chem_Priority网站指南.docx'
```

Expected: SHA-256 等于 `47D10F277DC7DFB04ECD1FCF532BB99EA4FD5FF7E57DB1A2B1C9C6722E160F68`。

- [ ] **Step 2: 运行章节和样式审计**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\Administrator\.codex\plugins\cache\openai-primary-runtime\documents\26.727.11326\skills\documents\scripts\section_audit.py' 'C:\Users\Administrator\Desktop\Chem_Priority网站指南.docx'
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\Administrator\.codex\plugins\cache\openai-primary-runtime\documents\26.727.11326\skills\documents\scripts\style_lint.py' 'C:\Users\Administrator\Desktop\Chem_Priority网站指南.docx' --json 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\template-style-evidence.json'
```

Expected: 1 个 A4 章节，8 个内嵌图片，无页眉页脚正文。

- [ ] **Step 3: 写入模板契约**

Create `artifact.md` with:

- 参考路径、SHA-256、9 页、1 个章节、8 个图片；
- A4 页面和上下 2.54 cm、左右 3.18 cm 页边距；
- 正文等线约 11 磅、1.158 倍行距、段后约 8 磅；
- 一级模块使用 `List Paragraph` 与真实自动编号；
- 第一部分段落和 8 个原图均为保留项；
- 第 2—6 部分标题后的空白段落为续写槽位；
- 标题、流程图、正文、截图、居中“图”标记的顺序规则。

- [ ] **Step 4: 自检模板契约**

Run:

```powershell
rg -n 'TBD|TODO|未确认|待补充' 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\artifact.md'
```

Expected: 无匹配。

### Task 2: 启动本地网站并采集最新截图

**Files:**
- Read: `E:\pyproject\ToxPi_App\app.py`
- Read: `E:\pyproject\ToxPi_App\pages\1_ADMETlab毒性数据获取.py`
- Read: `E:\pyproject\ToxPi_App\pages\2_ToxPi毒性评估.py`
- Read: `E:\pyproject\ToxPi_App\pages\3_EPISuite环境归趋.py`
- Read: `E:\pyproject\ToxPi_App\pages\4_化合物用途查询.py`
- Read: `E:\pyproject\ToxPi_App\pages\6_一键批量查询.py`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\screenshots\*.png`

**Interfaces:**
- Consumes: 当前 Streamlit 页面、页面内置模板和工作区示例数据。
- Produces: 清晰、无隐私数据、宽度一致的 PNG 截图，供 Task 4 插入 DOCX。

- [ ] **Step 1: 启动 Streamlit**

Run:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8511
```

Expected: `http://127.0.0.1:8511/_stcore/health` 返回 HTTP 200 和 `ok`。

- [ ] **Step 2: 截取 ADMETlab 页面**

Capture:

- 页面顶部的“上传 ADMETlab 输入表”和“输入模板”；
- 上传由页面模板生成的两行示例后，“输入数据”标签页；
- “ADMETlab 连接”和“结果下载”标签页。

Expected filenames:

- `02-admet-upload.png`
- `02-admet-input.png`
- `02-admet-connection-download.png`

- [ ] **Step 3: 截取 ToxPi 页面**

Use a small workbook containing `compound` and at least three numeric toxicity indicators.

Capture:

- 侧栏上传、指标选择和权重设置；
- “数据审查”；
- “ToxPi 图谱”；
- “排序稳健性”。

Expected filenames:

- `03-toxpi-settings.png`
- `03-toxpi-review.png`
- `03-toxpi-plots.png`
- `03-toxpi-robustness.png`

- [ ] **Step 4: 截取 EPI Suite 页面**

Use the page's built-in example template.

Capture:

- 上传、输入模板和目标环境归趋指标；
- “网页端预测”设置；
- “备用输入包”和“解析外部结果”；
- “结果下载”。

Expected filenames:

- `04-epi-upload.png`
- `04-epi-web.png`
- `04-epi-fallback-parse.png`
- `04-epi-download.png`

- [ ] **Step 5: 截取化合物用途查询页面**

Use a small input with `compound`, `smiles` and `cas`, avoiding any private API key.

Capture:

- 输入模板与待查询化合物；
- 标识符补全选项；
- EPA、ECHA、GHS/C&L 与来源属性标签页；
- 用途图表和结果下载区域。

Expected filenames:

- `05-use-input.png`
- `05-use-resolver.png`
- `05-use-sources.png`
- `05-use-charts-download.png`

- [ ] **Step 6: 截取一键批量查询页面**

Use a small primary Excel workbook and do not launch long-running external requests.

Capture:

- 多文件上传、文件检查与逐文件列映射；
- 自动运行项目选择和 EPI 补充结果区域；
- 运行设置；
- 结果总览、进度恢复与下载区域。

Expected filenames:

- `06-batch-mapping.png`
- `06-batch-modules-epi.png`
- `06-batch-settings.png`
- `06-batch-results.png`

- [ ] **Step 7: 检查截图**

Run:

```powershell
Get-ChildItem 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\screenshots' -Filter '*.png' | Sort-Object Name | Select-Object Name,Length
```

Expected: 19 个非空 PNG；每张截图文字清晰，无浏览器地址栏、调试叠层、个人路径或密钥。

### Task 3: 撰写和校对中文正文

**Files:**
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\guide-content.md`
- Read: `E:\pyproject\ToxPi_App\docs\superpowers\specs\2026-07-28-chempriority-website-guide-continuation-design.md`
- Read: 当前五个页面文件及其输出逻辑。

**Interfaces:**
- Consumes: 设计说明、页面字段、截图清单。
- Produces: 与截图一一对应、可直接插入 DOCX 的完整文字稿。

- [ ] **Step 1: 校对第一部分**

Apply only:

- `execl` → `Excel`
- `EPIsuite` → `EPI Suite`
- `Toxpi` → `ToxPi`
- 统一中英文空格、引号和标点；
- 调整明显不通顺的句子，但不改变两阶段排名、DF、PBM、权重和稳健性逻辑。

- [ ] **Step 2: 撰写第 2 部分**

Include:

- ADMETlab 页面当前定位是输入准备、格式校验和外部 ADMETlab 3.0 衔接；
- 上传 Excel、下载模板、查看待提交化合物；
- 当前连接状态说明；
- 下载 `ADMETlab_Validated_Input.xlsx`；
- 不宣称网站已经自动完成 ADMETlab 在线预测。

- [ ] **Step 3: 撰写第 3 部分**

Include:

- 输入表、毒性指标、指标方向和权重；
- 归一化、ToxPi 得分和排序；
- 风玫瑰图、综合得分柱状图；
- 蒙特卡洛权重扰动、多随机种子和稳健性结果；
- Excel 与图片下载。

- [ ] **Step 4: 撰写第 4 部分**

Include:

- `compound`、`smiles`、可选 `cas`；
- 目标环境归趋指标选择；
- EPI Web Suite 自动预测及超时、间隔、并发与缓存；
- 自动接口不可用时的输入包和外部结果回传；
- 分类结构化结果、Raw API JSON 和 `EPISuite_Fate_Report.xlsx`。

- [ ] **Step 5: 撰写第 5 部分**

Include:

- 标识符补全来源和可选 ChemSpider Key；
- EPA CompTox 用途、ECHA REACH 用途、ECHA GHS/C&L 危害；
- 来源属性评估；
- 已报道与预测用途的区别；
- 图表、审计记录和各类结果工作簿。

- [ ] **Step 6: 撰写第 6 部分**

Include:

- 多文件上传、仅按文件名关联 EPI 补充结果；
- 每个文件独立列映射；
- 自动运行模块选择；
- EPI 补充按 `CAS → SMILES → 化合物名称` 匹配，只填空值并隔离冲突；
- 缓存、并发、ToxPi 参数、断点恢复、部分结果、重试和 ZIP/Excel 下载。

- [ ] **Step 7: 文字自检**

Run:

```powershell
rg -n 'execl|EPIsuite|Toxpi|TODO|TBD|待补充|图待插入' 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\guide-content.md'
```

Expected: 无匹配。

### Task 4: 构建续写后的 DOCX

**Files:**
- Read: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\reference.docx`
- Read: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\artifact.md`
- Read: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\guide-content.md`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\build_guide.py`
- Create: `E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx`

**Interfaces:**
- Consumes: 原 DOCX、模板契约、完整文字稿和 19 张截图。
- Produces: 保留原格式和原始图形的完整 DOCX。

- [ ] **Step 1: 编写确定性构建脚本**

The script must:

- load `reference.docx` with `python-docx`;
- replace only the approved first-section terms;
- retain all existing relationships and images;
- replace the empty content slots after sections 2—6 with the approved section text;
- reuse `Normal` and `List Paragraph`;
- create real subsection paragraphs and module page breaks;
- insert screenshots with preserved aspect ratio and maximum width 5.75 inches;
- center screenshots and their “图” marker;
- set every inserted body paragraph to the source rhythm;
- save only to `Chem_Priority网站指南_续写版.docx`.

- [ ] **Step 2: 运行构建脚本**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\build_guide.py'
```

Expected: final DOCX exists, is larger than the 795,415-byte reference, and opens without repair warnings.

- [ ] **Step 3: 复核原文件未变化**

Run:

```powershell
Get-FileHash -Algorithm SHA256 'C:\Users\Administrator\Desktop\Chem_Priority网站指南.docx'
```

Expected: SHA-256 remains `47D10F277DC7DFB04ECD1FCF532BB99EA4FD5FF7E57DB1A2B1C9C6722E160F68`。

### Task 5: 结构和内容检查

**Files:**
- Read: `E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\final-style-evidence.json`

**Interfaces:**
- Consumes: 完整 DOCX。
- Produces: 结构审计证据，供视觉 QA 判定。

- [ ] **Step 1: 检查章节、标题、图片和文本**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\Administrator\.codex\plugins\cache\openai-primary-runtime\documents\26.727.11326\skills\documents\scripts\section_audit.py' 'E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx'
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\Administrator\.codex\plugins\cache\openai-primary-runtime\documents\26.727.11326\skills\documents\scripts\style_lint.py' 'E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx' --json 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\final-style-evidence.json'
```

Expected:

- 1 个 A4 章节；
- 原 8 个图片仍存在，新增 19 个截图；
- 六个一级模块和 10 个新增二级小节均存在；
- 页面边距、正文样式和编号系统保持源文件规则。

- [ ] **Step 2: 检查禁用词和遗漏**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -c "from docx import Document; d=Document(r'E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx'); print('\n'.join(p.text for p in d.paragraphs))" | rg -n 'execl|EPIsuite|Toxpi|TODO|TBD|待补充|图待插入'
```

Expected: 无匹配。

### Task 6: 使用 Word 渲染并逐页视觉检查

**Files:**
- Read: `E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\final-render\guide.pdf`
- Create: `E:\pyproject\ToxPi_App\.codex_doc_work\guide\final-render\page-*.png`

**Interfaces:**
- Consumes: 结构检查通过的 DOCX。
- Produces: 每页 PNG 与视觉检查结论。

- [ ] **Step 1: 使用 Microsoft Word 后台导出 PDF**

Run a hidden Word COM export with `ExportAsFixedFormat(..., 17)`.

Expected: `guide.pdf` exists and is non-empty; Word opens the DOCX without a repair prompt.

- [ ] **Step 2: 将 PDF 转为逐页 PNG**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe' -png -r 120 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\final-render\guide.pdf' 'E:\pyproject\ToxPi_App\.codex_doc_work\guide\final-render\page'
```

Expected: PDF 的每一页均生成一个非空 PNG。

- [ ] **Step 3: 逐页检查**

Check every page at 100%:

- 无裁切、重叠、乱码、图片拉伸或模糊；
- 无仅含标题的大面积异常空白页；
- 标题不孤立在页尾；
- 截图和对应说明相邻；
- 原有第一部分图形和截图未损坏；
- 第 2—6 部分顺序完整。

- [ ] **Step 4: 修正并重新渲染**

If any issue is found, update only the relevant paragraph, image width, spacing or page break in `build_guide.py`, rebuild the DOCX, rerun Task 5 and render into a fresh `final-render-iteration-N` directory.

Expected: latest iteration passes every structural and visual check.

### Task 7: 最终交付

**Files:**
- Deliver: `E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx`

**Interfaces:**
- Consumes: 最新一次结构检查和视觉检查均通过的 DOCX。
- Produces: 用户可直接打开和继续编辑的最终网站指南。

- [ ] **Step 1: 记录最终文件信息**

Run:

```powershell
Get-Item 'E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx' | Select-Object FullName,Length,LastWriteTime
Get-FileHash -Algorithm SHA256 'E:\pyproject\ToxPi_App\Chem_Priority网站指南_续写版.docx'
```

Expected: 文件存在、非空、哈希可记录。

- [ ] **Step 2: 确认只交付最终 DOCX**

Do not present `artifact.md`, build scripts, PDFs or PNGs as deliverables.

- [ ] **Step 3: 交付说明**

Report:

- 已轻度校对第一部分；
- 已续写第 2—6 部分；
- 已插入当前网站界面截图；
- 已完成结构检查和逐页 Word 渲染检查；
- 桌面原文件保持不变。
