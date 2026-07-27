# CompTox 功能用途词表与预测饼图设计

## 目标

扩展 EPA CompTox 预测功能用途的中英文映射，减少具有明确技术含义的用途被笼统显示为“其他用途”。表格使用中文专业类别并保留英文原词和概率；预测用途饼图继续按英文原词统计。

## 词表范围

新增以下保守、明确的映射。匹配时先标准化大小写，并将下划线、连字符和斜杠视为空格。

| 英文用途及同义形式 | 中文类别 |
| --- | --- |
| `crosslinker`, `crosslinking agent`, `cross-linking agent` | 交联剂 |
| `heat stabilizer`, `thermal stabilizer` | 热稳定剂 |
| `emollient` | 润肤剂 |
| `hair conditioner`, `hair conditioning agent` | 护发剂 |
| `buffer`, `buffering agent` | 缓冲剂 |
| `photoinitiator`, `photo initiator` | 光引发剂 |
| `preservative` | 防腐剂 |
| `humectant` | 保湿剂 |
| `adhesion promoter`, `adhesion promoting agent` | 附着力促进剂 |
| `wetting agent` | 润湿剂 |
| `reducer`, `reducing agent` | 还原剂 |
| `emulsion stabilizer` | 乳液稳定剂 |

为避免误把 `friction reducer` 等其他短语归为还原剂，单词 `buffer` 和 `reducer` 只在标准化后的单个字段完全相等时匹配；`buffering agent` 和 `reducing agent` 仍按明确短语匹配。导出的 `CN_Mapping` 审计页同时列出精确匹配词和短语规则。

`vinyl` 只描述结构或材料特征，缺少稳定的功能用途含义，因此暂不映射。

## 表格与摘要输出

- `Functional_Uses_Predicted` 中命中词表的记录显示中文专业类别。
- 英文原词、预测概率、来源字段保持不变。
- 汇总文本继续采用 `中文类别 (英文原词, p=概率)`，例如：
  `交联剂 (crosslinker, p=0.475)`。
- 未命中词表但存在英文用途时，继续显示 `其他用途 (英文原词, p=概率)`，不得丢弃英文证据。

## 预测功能用途饼图

- 每个化合物只选取有效预测概率最高的一项。
- 有效预测概率必须来自 `probability` 字段，且为 `[0, 1]` 范围内的有限数值；不得用 `evidence_count` 替代缺失或无效概率。
- 饼图聚合键和图例继续使用 EPA 返回的英文原词，不改成中文类别。
- 已有英文用途即为有效结果，即使尚无中文映射也不能归入 `Others`。
- 只有化合物完全没有有效预测用途或有效预测概率时，才归入 `Others`。
- 最高概率并列时沿用稳定的输入顺序选择第一项，不归入 `Others`。
- 每个化合物只计数一次，因此饼图各切片计数之和必须等于化合物总数。
- 预测用途饼图必须显示所有已选中的英文用途类别，不得因为类别较少见或超过固定类别数量而合并为 `Others`。
- `Others` 切片只汇总真正没有有效预测结果的化合物。

## 数据流

1. CompTox 返回预测功能用途和概率。
2. `classify_use_cn` 通过扩展后的显式词表生成中文类别。
3. 明细表和摘要使用中文类别，同时保留原始英文用途与概率。
4. `extract_top_predicted_functional_use_data` 按化合物选取最高概率项，并把英文原词写入 `use_label`。
5. 饼图按 `use_label` 聚合；无有效预测结果的化合物写入 `Others`。

## 测试与验收

- 逐项验证新增英文用途及下划线形式能映射到预期中文类别。
- 验证 `vinyl` 仍为未映射用途。
- 验证明细表和汇总把 `crosslinker` 显示为“交联剂”，同时保留原词和概率。
- 验证高概率未翻译用途仍以英文原词进入饼图数据，不成为 `Others`。
- 验证每个化合物只采用最高概率项。
- 验证没有预测结果的化合物才成为 `Others`。
- 验证超过原有显示上限时，已有英文用途仍全部保留，不合并为 `Others`。
- 验证饼图图例保留英文原词。
- 使用用户提供的 `EPA_CompTox_Results.xlsx` 中出现的未映射词复核词表覆盖率和饼图分类结果。

## 非目标

- 不修改 EPA 返回的原始英文词。
- 不使用模糊语义模型或自动翻译进行分类。
- 不修改产品用途类别、ECHA REACH 用途或 reported 功能用途的现有统计口径。
- 不直接改写用户提供的历史 Excel；修改应用逻辑，使新生成结果采用新词表。
