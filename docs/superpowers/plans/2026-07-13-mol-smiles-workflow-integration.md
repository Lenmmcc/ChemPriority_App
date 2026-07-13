# MOL 结构解析与 SMILES 工作流整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在四个上传工作流起点将 Compound Discoverer MOL 文本解析并决策为标准 SMILES；名称和 SMILES 分别查询，合并带来源的结果，并安全传给下游。

**Architecture:** 扩展 `src/mol_structure_parser.py` 的纯 DataFrame 接口，统一产生有效 `smiles` 和审计字段。标识符补全、CompTox 与 ECHA 使用名称/SMILES 查询变体，SMILES 命中作为结构相关主记录，名称证据保留来源。页面只负责调用共享接口、显示摘要和导出审计。

**Tech Stack:** Python 3.9+, pandas, RDKit, Streamlit, openpyxl, unittest/mock.

## Global Constraints

- 标准标识列顺序保持为 `compound`、`smiles`、`cas`、`ec`、`dtxsid`、`echa_id`。
- 比较 MOL 与原始 SMILES 必须使用 RDKit canonical isomeric SMILES；不得比较原始字符串。
- 原始有效 SMILES 不被 MOL 结果覆盖；冲突和行失败仅告警，不阻断同批其它行。
- 外部服务默认地址、超时、缓存、并发、限速不变。
- 按 TDD 执行：先写失败测试、再写最小实现、再运行针对性测试。

---

### Task 1: 共享结构准备与 SMILES 决策

**Files:**
- Modify: `src/mol_structure_parser.py`
- Modify: `tests/test_mol_structure_parser.py`

**Interfaces:**
- Produces `prepare_structure_dataframe(input_df, mol_column=None, smiles_column=None) -> pd.DataFrame`。
- Produces `summarize_structure_preparation(prepared_df) -> dict[str, int]`。
- 保留原始列、既有 `RESULT_COLUMNS`，追加 `smiles`、`smiles_source`、`smiles_decision_warning`。

- [ ] **Step 1: Write the failing tests**

```python
def test_prepare_structure_dataframe_uses_mol_when_source_smiles_is_blank(self):
    result = prepare_structure_dataframe(pd.DataFrame({"structure": [VALID_ETHANOL_MOL]}))
    self.assertEqual(result.loc[0, "smiles"], "CCO")
    self.assertEqual(result.loc[0, "smiles_source"], "MOL 解析")

def test_prepare_structure_dataframe_keeps_original_smiles_on_conflict(self):
    result = prepare_structure_dataframe(pd.DataFrame({"smiles": ["c1ccccc1"], "structure": [VALID_ETHANOL_MOL]}))
    self.assertEqual(result.loc[0, "smiles"], "c1ccccc1")
    self.assertEqual(result.loc[0, "smiles_source"], "原始 SMILES（与 MOL 冲突）")
    self.assertIn("冲突", result.loc[0, "smiles_decision_warning"])

def test_prepare_structure_dataframe_compares_equivalent_smiles_structurally(self):
    result = prepare_structure_dataframe(pd.DataFrame({"SMILES": ["OCC"], "structure": [VALID_ETHANOL_MOL]}))
    self.assertEqual(result.loc[0, "smiles_source"], "原始 SMILES（与 MOL 一致）")
```

- [ ] **Step 2: Run the focused test to verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_mol_structure_parser -v`

Expected: FAIL because the structure-preparation functions do not exist.

- [ ] **Step 3: Implement the decision table**

```python
SMILES_DECISION_COLUMNS = ("smiles", "smiles_source", "smiles_decision_warning")

def canonicalize_isomeric_smiles(value: object) -> str:
    text = str(value).strip() if _has_text(value) else ""
    mol = Chem.MolFromSmiles(text) if text else None
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True) if mol else ""

def prepare_structure_dataframe(input_df, mol_column=None, smiles_column=None):
    selected_mol = mol_column if mol_column in input_df.columns else find_mol_text_column(input_df.columns)
    selected_smiles = smiles_column if smiles_column in input_df.columns else find_smiles_column(input_df.columns)
    parsed = parse_mol_dataframe(input_df, selected_mol) if selected_mol else _empty_parse_columns(input_df)
    return _apply_smiles_decisions(parsed, selected_smiles)
```

Implement all spec cases: blank original uses successful MOL; valid original wins when same or conflict; invalid original falls back to successful MOL; missing/failed MOL leaves valid original or blank. `summarize_structure_preparation` counts MOL rows, successes, `M END` repairs, conflicts and failures.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_mol_structure_parser -v`

Expected: PASS, including existing repair, explicit-H, blank, and per-row failure tests.

- [ ] **Step 5: Commit**

```bash
git add src/mol_structure_parser.py tests/test_mol_structure_parser.py
git commit -m "feat: prepare effective SMILES from MOL text"
```

### Task 2: 名称/SMILES 双通道标识符补全

**Files:**
- Modify: `src/identifier_resolver.py`
- Modify: `tests/test_identifier_resolver.py`

**Interfaces:**
- Produces `build_identifier_query_variants(row: pd.Series) -> list[dict]` with `query_source` of `名称` or `SMILES`.
- Produces `merge_identifier_resolutions(source_row, name_resolution, smiles_resolution) -> tuple[dict, list[dict]]`.
- Extends `RESOLVED_COLUMNS` with `name_query_status`, `smiles_query_status`, `primary_identity_source`, `identity_conflict`, without removing old columns.

- [ ] **Step 1: Write failing identity tests**

```python
@patch("src.identifier_resolver.resolve_pubchem_by_smiles")
@patch("src.identifier_resolver.resolve_pubchem_by_name")
def test_batch_queries_name_and_smiles_and_keeps_smiles_identity_primary(self, by_name, by_smiles):
    by_name.return_value = _pubchem_resolution("702", "CCO", "Ethanol", "64-17-5", "Name")
    by_smiles.return_value = _pubchem_resolution("241", "c1ccccc1", "Benzene", "71-43-2", "SMILES")
    completed, warnings = run_identifier_completion_batch(
        pd.DataFrame({"compound": ["Ethanol"], "smiles": ["c1ccccc1"]}),
        use_epa=False, use_echa=False, delay_seconds=0,
    )
    by_name.assert_called_once()
    by_smiles.assert_called_once()
    self.assertEqual(completed.loc[0, "pubchem_cid"], "241")
    self.assertEqual(completed.loc[0, "primary_identity_source"], "SMILES")
    self.assertEqual(completed.loc[0, "identity_conflict"], "是")
    self.assertTrue(warnings["stage"].eq("identity_conflict").any())
```

- [ ] **Step 2: Run the focused test to verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_identifier_resolver.IdentifierCompletionPubChemTests -v`

Expected: FAIL because current PubChem logic returns immediately after a successful name match.

- [ ] **Step 3: Resolve both variants, deduplicate, and choose the primary record**

```python
def _resolve_pubchem_variants(working, base_url, timeout):
    name = resolve_pubchem_by_name(working["compound"], base_url, timeout) if working["compound"] else None
    smiles = resolve_pubchem_by_smiles(working["smiles"], base_url, timeout) if working["smiles"] else None
    return name, smiles

def merge_identifier_resolutions(source_row, name_resolution, smiles_resolution):
    name_key = _resolution_identity_key(name_resolution)
    smiles_key = _resolution_identity_key(smiles_resolution)
    primary = dict(smiles_resolution or name_resolution or {})
    primary["name_query_status"] = _clean_cell((name_resolution or {}).get("status"))
    primary["smiles_query_status"] = _clean_cell((smiles_resolution or {}).get("status"))
    primary["primary_identity_source"] = "SMILES" if smiles_key else "名称"
    if name_key and smiles_key and name_key != smiles_key:
        primary["identity_conflict"] = "是"
        return primary, [_warning_row(source_row, "identity_conflict", "名称与 SMILES 命中不同化学实体")]
    primary["identity_conflict"] = ""
    return primary, []
```

Replace `_resolve_pubchem_for_working` early-return behavior. CAS fallback runs only if neither name nor SMILES returns a stable match. EPA/ECHA receive the primary SMILES identity when available; their status strings retain both query outcomes.

- [ ] **Step 4: Run resolver regressions**

Run: `.venv\Scripts\python.exe -m unittest tests.test_identifier_resolver -v`

Expected: PASS; update old name-first expectations to require two calls when both values exist, while retaining name-only, CAS-only, SMILES-only, ChemSpider and threaded tests.

- [ ] **Step 5: Commit**

```bash
git add src/identifier_resolver.py tests/test_identifier_resolver.py
git commit -m "feat: merge name and SMILES identifier matches"
```

### Task 3: CompTox/ECHA 用途双通道查询与来源保留

**Files:**
- Modify: `src/comptox_use.py`
- Modify: `src/echa_use.py`
- Modify: `tests/test_comptox_dashboard_mode.py`
- Modify: `tests/test_echa_use.py`

**Interfaces:**
- Each provider produces `build_use_query_variants(row) -> list[dict]`.
- Summary, candidate and warning rows gain `query_source` and `query_value`.
- Candidate groups retain distinct DTXSID/ECHA ID; exact duplicate candidate evidence merges its `query_source` values.

- [ ] **Step 1: Write failing provider tests**

```python
def test_comptox_runs_name_and_smiles_variants_and_tags_candidates(self):
    with patch("src.comptox_use.resolve_dtxsid") as resolve, patch("src.comptox_use.fetch_use_candidates") as fetch:
        resolve.side_effect = [_dtxsid("DTXSID_NAME"), _dtxsid("DTXSID_SMILES")]
        fetch.return_value = ([{"category": "solvent", "source": "test"}], [])
        _, candidates, _ = run_comptox_use_batch(pd.DataFrame({"compound": ["name"], "smiles": ["CCO"]}), delay_seconds=0)
    self.assertEqual(set(candidates["query_source"]), {"名称", "SMILES"})

def test_echa_runs_name_and_smiles_variants_and_preserves_sources(self):
    with patch("src.echa_use.resolve_substance") as resolve:
        resolve.side_effect = [_echa("100.000.001"), _echa("100.000.002")]
        summary, _, _, warnings = run_echa_use_batch(pd.DataFrame({"compound": ["name"], "smiles": ["CCO"]}), delay_seconds=0)
    self.assertEqual(set(summary["query_source"]), {"名称", "SMILES"})
    self.assertTrue(warnings["stage"].eq("identity_conflict").any())
```

- [ ] **Step 2: Run provider tests to verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use -v`

Expected: FAIL because each provider currently resolves one mixed row once.

- [ ] **Step 3: Execute query variants deterministically**

```python
def build_use_query_variants(row):
    base = {key: _clean_cell(row.get(key)) for key in REQUIRED_IDENTIFIER_COLUMNS}
    variants = []
    if base["compound"]:
        variants.append({**base, "query_source": "名称", "query_value": base["compound"], "smiles": "", "cas": "", "dtxsid": "", "echa_id": ""})
    if base["smiles"]:
        variants.append({**base, "query_source": "SMILES", "query_value": base["smiles"], "compound": "", "cas": "", "dtxsid": "", "echa_id": ""})
    return variants or [{**base, "query_source": "输入标识", "query_value": ""}]
```

Run existing resolution/fetch for each variant. Add both source fields to every output. For conflicting stable identities append one `identity_conflict` warning per input row, retain both candidate groups, and mark the SMILES group primary. Keep existing cache/concurrency wrappers and source-origin callers compatible.

- [ ] **Step 4: Run provider regressions**

Run: `.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/comptox_use.py src/echa_use.py tests/test_comptox_dashboard_mode.py tests/test_echa_use.py
git commit -m "feat: retain name and SMILES use-query evidence"
```

### Task 4: 批量工作流和综合筛查的 SMILES 传递

**Files:**
- Modify: `src/auto_query_workflow.py`
- Modify: `pages/0_综合筛查流程.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_cp_screening_workflow.py`

**Interfaces:**
- Extend `AutoWorkflowMapping` with `mol_column: str | None = None`.
- `AutoWorkflowResult.tables["Structure_Preparation"]` contains audit columns.
- `SMILES_input` is copied from effective `smiles`, not directly from an arbitrary source column.

- [ ] **Step 1: Write failing handoff tests**

```python
def test_auto_workflow_passes_mol_derived_smiles_to_identifier_step(self, run_identifier):
    run_identifier.return_value = (_completed("CCO"), pd.DataFrame())
    result = run_auto_query_workflow(_mol_input_frame(), config=_identifier_only_config())
    self.assertEqual(run_identifier.call_args.args[0].loc[0, "smiles"], "CCO")
    self.assertIn("Structure_Preparation", result.tables)

def test_screening_mapping_uses_effective_smiles_before_downstream_build(self):
    normalized, _, _ = normalize_samples_for_mappings(_mol_sample(), _mol_sample_mapping())
    self.assertEqual(normalized[0]["data"].loc[0, "SMILES_input"], "CCO")
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_cp_screening_workflow -v`

Expected: FAIL because these paths do not prepare MOL input.

- [ ] **Step 3: Prepare before mapping/normalization**

```python
prepared_input = prepare_structure_dataframe(input_df, mol_column=mapping.mol_column, smiles_column=mapping.smiles_col)
normalized = _normalize_input(prepared_input, mapping)
tables["Structure_Preparation"] = prepared_input[STRUCTURE_AUDIT_COLUMNS]

prepared = prepare_structure_dataframe(frame, mol_column=mapping.get("mol_column"), smiles_column=mapping.get("smiles_col"))
normalized[STANDARD_SMILES_COL] = prepared["smiles"]
```

Add a screening `mol_column` selectbox beside optional SMILES/CAS. Preserve raw source columns and use effective SMILES only in normalized/downstream tables.

- [ ] **Step 4: Run handoff regressions**

Run: `.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_cp_screening_workflow tests.test_r_screening_downstream -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/auto_query_workflow.py pages/0_综合筛查流程.py tests/test_auto_query_workflow.py tests/test_cp_screening_workflow.py
git commit -m "feat: pass MOL-derived SMILES through batch workflows"
```

### Task 5: EPI、用途和一键页面的首步结构准备与导出

**Files:**
- Modify: `pages/3_EPISuite环境归趋.py`
- Modify: `pages/4_化合物用途查询.py`
- Modify: `pages/6_一键批量查询.py`
- Modify: `pages/0_综合筛查流程.py`
- Create: `tests/test_structure_preparation_page_contract.py`

**Interfaces:**
- Each page imports and calls `prepare_structure_dataframe` and `summarize_structure_preparation` before its first `normalize_*_input_columns` call.
- Exports include a `Structure_Preparation` / `结构解析与SMILES决策` sheet with audit fields and original identifiers.

- [ ] **Step 1: Write failing page-contract tests**

```python
PAGES = [Path("pages/0_综合筛查流程.py"), Path("pages/3_EPISuite环境归趋.py"), Path("pages/4_化合物用途查询.py"), Path("pages/6_一键批量查询.py")]

def test_all_target_pages_call_shared_structure_preparation(self):
    for path in PAGES:
        text = path.read_text(encoding="utf-8")
        self.assertIn("prepare_structure_dataframe", text, path.name)
        self.assertIn("summarize_structure_preparation", text, path.name)
```

- [ ] **Step 2: Run the contract test to verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_structure_preparation_page_contract -v`

Expected: FAIL because EPI, use, and one-click pages do not call the shared interface.

- [ ] **Step 3: Add the visible first-step summary and handoff**

```python
prepared_input_df = prepare_structure_dataframe(raw_input_df)
summary = summarize_structure_preparation(prepared_input_df)
st.caption(f"结构准备：MOL {summary['mol_rows']} 行；成功 {summary['parsed_success']}；冲突 {summary['smiles_conflicts']}；失败 {summary['parse_failures']}。")
if summary["smiles_conflicts"] or summary["parse_failures"]:
    with st.expander("结构解析与 SMILES 决策明细", expanded=False):
        st.dataframe(prepared_input_df[STRUCTURE_AUDIT_COLUMNS], use_container_width=True)
```

EPI normalizes `prepared_input_df`. The use page stores the prepared frame by input signature before resolver/CompTox/ECHA normalization. One-click prepares before mapping and includes the audit table in its result workbook. Screening shows per-workbook summaries and adds the audit table to its download workbook.

- [ ] **Step 4: Compile and run page-contract tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_structure_preparation_page_contract tests.test_streamlit_page_order -v`

Expected: PASS.

Run: `.venv\Scripts\python.exe -m compileall app.py pages src`

Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add pages/0_综合筛查流程.py pages/3_EPISuite环境归趋.py pages/4_化合物用途查询.py pages/6_一键批量查询.py tests/test_structure_preparation_page_contract.py
git commit -m "feat: add MOL preparation to upload workflows"
```

### Task 6: 全套回归和真实工作簿验证

**Files:**
- Modify only if verification exposes a defect in Tasks 1-5.

- [ ] **Step 1: Run automated tests**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests PASS.

- [ ] **Step 2: Compile the application**

Run: `.venv\Scripts\python.exe -m compileall app.py pages src`

Expected: exit code 0.

- [ ] **Step 3: Read-only smoke test with the supplied workbook**

Run:

```powershell
.venv\Scripts\python.exe -c "import pandas as pd; from src.mol_structure_parser import prepare_structure_dataframe,summarize_structure_preparation; df=pd.read_excel(r'C:\Users\Administrator\Desktop\GC EI Compounds - 副本.xlsx'); out=prepare_structure_dataframe(df); print(summarize_structure_preparation(out)); print({'rows_with_smiles': int(out['smiles'].astype(str).str.strip().ne('').sum())})"
```

Expected: nonempty Compound Discoverer structure rows parse, `M END` repairs are counted, and usable SMILES count is nonzero.

- [ ] **Step 4: Inspect the final state**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; only intentional feature changes are committed; pre-existing untracked files remain untouched.

- [ ] **Step 5: Commit only a verification correction, if required**

If Task 6 identifies a defect, stage exactly the corrected files from Tasks 1–5 and use this commit message:

```bash
git commit -m "fix: verify MOL SMILES workflow integration"
```
