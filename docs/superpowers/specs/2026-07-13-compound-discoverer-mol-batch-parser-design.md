# Compound Discoverer MOL 批量解析设计

## 目标

为 Excel 工作表中每行保存的 Compound Discoverer MOL 文本批量生成可复用的 SMILES 与基础化学信息。第一阶段只提供独立 Python 批处理函数和回归测试，不新增 Streamlit 页面，也不改动原始工作簿。

## 已确认的输入

- 输入是 Excel 表格的一列 MOL 文本，不是结构图片，也不是独立 `.mol`/`.sdf` 文件上传。
- 每个单元格最多包含一个结构记录；批处理应处理所有数据行。
- 结构列优先匹配 `mol_text`、`mol`、`molfile`、`structure`（忽略大小写和首尾空格）。
- 用户提供的 `GC EI Compounds - 副本.xlsx` 包含工作表 `GCEICompounds`、508 条数据行和 `Structure` 列；其中 12 个单元格非空，全部以 `$$$$` 结束且缺少 `M  END`。

## 方案选择

使用 RDKit 作为分子图解析和化学有效性校验引擎。自行转换原子/键表会在芳香性、价态、形式电荷与立体化学上产生不可接受的维护风险；外部 Open Babel 程序则增加部署要求。

## 解析规则

1. 对非空单元格删除尾部的 SDF 记录分隔符 `$$$$`。
2. 若文本中没有 `M  END`，只在已有可识别的 MOL 原子/键计数行时于结构区末尾补入 `M  END`，并添加警告 `已自动补齐 M END`。计数行不强制带有 `V2000`，以兼容附件中的 Compound Discoverer 导出。
3. 使用 RDKit 解析并进行 sanitize。若解析或 sanitize 失败，该行不得生成 SMILES。
4. 成功时保留解析出的原始分子图以统计原子数和键数、导出标准化 MOL block；生成 canonical SMILES 和 isomeric SMILES 前移除显式氢原子，使 SMILES 可直接用于后续查询。同步输出分子式和精确质量。
5. 空单元格标记为 `未提供 MOL 文本`；无法识别结构列时批处理函数抛出清晰的输入错误；单条结构失败只影响该行。

## 批处理接口

新增 `src/mol_structure_parser.py`，公开：

```python
def find_mol_text_column(columns) -> str | None: ...
def parse_mol_text(mol_text: object) -> dict[str, object]: ...
def parse_mol_dataframe(input_df: pandas.DataFrame, mol_column: str | None = None) -> pandas.DataFrame: ...
```

`parse_mol_dataframe` 返回原始 DataFrame 的副本，并在末尾添加下列列：

`parsed_smiles`、`parsed_isomeric_smiles`、`parsed_molecular_formula`、`parsed_exact_mass`、`parsed_atom_count`、`parsed_bond_count`、`normalized_molblock`、`parse_status`、`parse_warnings`。

成功状态为 `成功`；空输入为 `未提供 MOL 文本`；其他单行异常为 `解析失败`。所有解析产物使用 `parsed_` 前缀，因此绝不覆盖输入中已有的 `smiles` 或其他业务列。后续网页可在用户确认后将 `parsed_smiles` 映射为标准 `smiles`。第一阶段的测试用例不依赖网络。

## 依赖与测试

- 在 `requirements.txt` 增加受版本约束的 RDKit 依赖。
- 新增 `tests/test_mol_structure_parser.py`。
- 测试正常的 Compound Discoverer MOL 文本、缺失 `M  END` 的修复、空文本、破损文本、结构列别名匹配、批量混合成功/失败行以及不覆盖已有 SMILES。
- 使用来自用户附件的结构格式作为测试夹具，但只保留最小化的结构文本，不提交原始工作簿或化合物结果数据。

## 非目标

- 不识别图片或 PDF 中的结构图。
- 不做网页 UI、Excel 导入导出按钮或与外部数据库的匹配。
- 不把自动解析结果静默提交给 EPA、ECHA 或 EPI Suite；这些集成将在独立的后续阶段处理。
