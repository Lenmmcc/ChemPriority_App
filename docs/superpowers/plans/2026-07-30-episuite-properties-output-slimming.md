# EPI Suite Properties Output Slimming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant KOAWIN coefficient/log columns from public EPI Properties results, keep only the unique logKAW/TPSA/MR additions, and make page/CSV/Excel presentation match the existing Properties schema.

**Architecture:** Preserve the existing KOAWIN normalizer and its internal coefficient validation so warning behavior remains stable, but filter its output at the public enrichment boundary. Remove the now-unneeded custom display/export mapping, render EPI detail tables with their internal column names and hidden index, and write the same internal headers and numeric values to Excel.

**Tech Stack:** Python 3, pandas, RDKit, Streamlit, openpyxl-based test inspection, unittest.

## Global Constraints

- Public Properties output adds exactly `koawin_log_kaw`, `tpsa_rdkit_a2`, and `mr_rdkit_cm3_mol`, in that order.
- Public Properties output must not contain `koawin_log_kow`, `koawin_kow`, `koawin_log_koa`, `koawin_koa`, or `koawin_kaw`.
- Existing `log_kow_estimated` and `log_koa_estimated` remain the public logKOW/logKOA results.
- Keep all existing selected/estimated/experimental/type/units, identity, dermal, and compatibility fields.
- Keep KOAWIN raw coefficients internal for logKAW calculation and consistency warnings.
- Page, CSV, and Excel use internal English column names for the three retained additions.
- All EPI detail table render calls use `hide_index=True`.
- Excel cells remain numeric and preserve the underlying values; do not stringify or round the DataFrame.
- TPSA/MR algorithm, SMILES priority, warning behavior, EPI API requests, dependencies, cache, retry, and checkpoint behavior remain unchanged.
- Do not dynamically delete all-empty columns.
- Do not add enthalpy of vaporization ΔHvap.

---

### Task 1: Limit public enrichment to the three unique fields

**Files:**
- Modify: `src/episuite_properties.py:9-16,166-180`
- Modify: `tests/test_episuite_properties.py:250-293`
- Modify: `tests/test_episuite_cas_values.py:669-821`

**Interfaces:**
- Preserves: `extract_koawin_partition_fields(data: dict) -> tuple[dict, list[str]]` as the internal six-field coefficient/log normalizer.
- Preserves: `calculate_rdkit_descriptor_fields(api_smiles=None, epi_smiles=None, input_smiles=None) -> tuple[dict, list[str]]`.
- Changes: `build_epi_property_enrichment(data: dict, epi_smiles=None, input_smiles=None)` returns only `koawin_log_kaw`, `tpsa_rdkit_a2`, and `mr_rdkit_cm3_mol` as public fields.
- Consumed by: `src.episuite_io._build_properties_row`.

- [ ] **Step 1: Add a failing public-enrichment contract test**

Add this method to `EPISuitePropertyTests` in
`tests/test_episuite_properties.py`:

```python
    def test_public_enrichment_exposes_only_unique_partition_and_rdkit_fields(self):
        data = {
            "chemicalProperties": {"smiles": "CCO"},
            "logKow": {"estimatedValue": {"value": 3.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 5.0,
                    "model": {
                        "kow": 1000.0,
                        "koa": 100000.0,
                        "kaw": 0.01,
                        "logKoa": 5.0,
                    },
                }
            },
        }

        fields, warnings = build_epi_property_enrichment(data)

        self.assertEqual(
            list(fields),
            ["koawin_log_kaw", "tpsa_rdkit_a2", "mr_rdkit_cm3_mol"],
        )
        self.assertEqual(fields["koawin_log_kaw"], -2.0)
        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23)
        self.assertAlmostEqual(fields["mr_rdkit_cm3_mol"], 12.7598)
        for removed in (
            "koawin_log_kow",
            "koawin_kow",
            "koawin_log_koa",
            "koawin_koa",
            "koawin_kaw",
        ):
            self.assertNotIn(removed, fields)
        self.assertEqual(warnings, [])
```

- [ ] **Step 2: Update the categorized-table test to specify the slim schema**

Replace the eight-column expectation in
`EPISuiteCasValueTests.test_properties_include_partition_pairs_and_rdkit_descriptors`
with:

```python
        retained_columns = [
            "koawin_log_kaw",
            "tpsa_rdkit_a2",
            "mr_rdkit_cm3_mol",
        ]
        removed_columns = {
            "koawin_log_kow",
            "koawin_kow",
            "koawin_log_koa",
            "koawin_koa",
            "koawin_kaw",
        }

        positions = [properties.columns.get_loc(name) for name in retained_columns]
        self.assertEqual(positions, list(range(positions[0], positions[0] + 3)))
        self.assertEqual(
            properties.loc[0, "log_kow_estimated"],
            response["logKow"]["estimatedValue"]["value"],
        )
        self.assertEqual(
            properties.loc[0, "log_koa_estimated"],
            response["logKoa"]["estimatedValue"]["value"],
        )
        self.assertAlmostEqual(
            properties.loc[0, "koawin_log_kaw"],
            math.log10(response["logKoa"]["estimatedValue"]["model"]["kaw"]),
        )
        self.assertAlmostEqual(properties.loc[0, "tpsa_rdkit_a2"], 20.23)
        self.assertAlmostEqual(properties.loc[0, "mr_rdkit_cm3_mol"], 12.7598)

        for table_name, table in tables.items():
            for column in removed_columns:
                self.assertNotIn(column, table.columns, table_name)
```

Rename the test to
`test_properties_include_only_unique_partition_log_and_rdkit_descriptors`.
Keep its existing Raw API JSON and Warnings assertions.

- [ ] **Step 3: Run the two new contracts and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties.EPISuitePropertyTests.test_public_enrichment_exposes_only_unique_partition_and_rdkit_fields tests.test_episuite_cas_values.EPISuiteCasValueTests.test_properties_include_only_unique_partition_log_and_rdkit_descriptors -v
```

Expected: both tests fail because the public enrichment still contains eight
fields and Properties still exposes the five removed columns.

- [ ] **Step 4: Filter only at the public enrichment boundary**

Add this constant near `PARTITION_COLUMN_ORDER` in
`src/episuite_properties.py`:

```python
PUBLIC_EPI_ENRICHMENT_ORDER = (
    "koawin_log_kaw",
    "tpsa_rdkit_a2",
    "mr_rdkit_cm3_mol",
)
```

Replace `build_epi_property_enrichment` with:

```python
def build_epi_property_enrichment(data: dict, epi_smiles=None, input_smiles=None):
    partition_fields, partition_warnings = extract_koawin_partition_fields(data)
    chemical = data.get("chemicalProperties", {})
    api_smiles = chemical.get("smiles") if isinstance(chemical, dict) else None
    descriptor_fields, descriptor_warnings = calculate_rdkit_descriptor_fields(
        api_smiles=api_smiles,
        epi_smiles=epi_smiles,
        input_smiles=input_smiles,
    )
    public_fields = {
        "koawin_log_kaw": partition_fields["koawin_log_kaw"],
        **descriptor_fields,
    }
    return (
        {column: public_fields[column] for column in PUBLIC_EPI_ENRICHMENT_ORDER},
        [*partition_warnings, *descriptor_warnings],
    )
```

Do not change `extract_koawin_partition_fields`; its raw coefficients remain
internal and its existing boundary tests continue to protect validation and
warning behavior.

- [ ] **Step 5: Run focused property/result-table tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_cas_values -v
```

Expected: all tests pass. Existing KOAWIN invalid-value, range-recovery,
relationship-warning, descriptor-priority, and non-fatal-warning tests remain
green.

- [ ] **Step 6: Commit the public schema change**

```powershell
git add src/episuite_properties.py tests/test_episuite_properties.py tests/test_episuite_cas_values.py
git commit -m "refactor: slim EPI property enrichment"
```

---

### Task 2: Remove special presentation mapping and hide detail-table indexes

**Files:**
- Delete: `src/episuite_display.py`
- Modify: `src/episuite_io.py:1-17,951-981`
- Modify: `pages/3_EPISuite环境归趋.py:1-34,148-179`
- Modify: `tests/test_episuite_display.py:1-169`
- Modify: `tests/test_episuite_cas_values.py:843-894`

**Interfaces:**
- Removes: `episuite_property_column_config(...)`.
- Removes: `episuite_property_export_frame(...)`.
- Preserves: `build_result_workbook(input_df, summary_df=None, errors_df=None, raw_df=None, epi_tables=None) -> io.BytesIO`.
- Preserves: `render_epi_web_tables(epi_tables) -> None`, now using Streamlit defaults with hidden indexes.

- [ ] **Step 1: Replace display-mapping tests with failing page behavior tests**

Rewrite `tests/test_episuite_display.py` so it no longer imports
`src.episuite_display`. Retain the existing AST isolation technique for
`render_epi_web_tables`, and assert the captured calls with:

```python
        namespace["render_epi_web_tables"](tables)

        detail_sheets = namespace["DETAIL_RESULT_SHEETS"]
        self.assertEqual(
            set(calls_by_sheet),
            {sheet_name for sheet_name, _ in detail_sheets},
        )
        for sheet_name, call in calls_by_sheet.items():
            self.assertEqual(call["width"], "stretch", sheet_name)
            self.assertIs(call["hide_index"], True, sheet_name)
            self.assertNotIn("column_config", call, sheet_name)
```

Add a source-contract test:

```python
    def test_page_no_longer_imports_special_property_display_policy(self):
        source = Path("pages/3_EPISuite环境归趋.py").read_text(encoding="utf-8")

        self.assertNotIn("episuite_property_column_config", source)
        self.assertIn("hide_index=True", source)
```

The fake Streamlit `dataframe` method must accept `**kwargs`, store
`{"frame": frame, **kwargs}` for each active tab, and support both detail-table
and raw-preview calls without importing the full Streamlit page.

- [ ] **Step 2: Update the workbook contract to internal headers**

Rename
`test_workbook_properties_sheet_uses_labels_and_keeps_values_numeric` to
`test_workbook_properties_sheet_uses_internal_headers_and_keeps_values_numeric`.
Replace its label mapping with:

```python
        retained_columns = (
            "koawin_log_kaw",
            "tpsa_rdkit_a2",
            "mr_rdkit_cm3_mol",
        )
        removed_columns = (
            "koawin_log_kow",
            "koawin_kow",
            "koawin_log_koa",
            "koawin_koa",
            "koawin_kaw",
            "logKAW（KOAWIN估算）",
            "TPSA（Å²，RDKit）",
            "MR（cm³/mol，RDKit）",
        )

        properties = tables["Properties"]
        for column in retained_columns:
            self.assertIn(column, header)
            exported = data[header.index(column)]
            self.assertIsInstance(exported, (int, float))
            self.assertAlmostEqual(
                exported,
                properties.loc[0, column],
                places=14,
            )
        for column in removed_columns:
            self.assertNotIn(column, header)
```

- [ ] **Step 3: Run presentation/export contracts and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_display tests.test_episuite_cas_values.EPISuiteCasValueTests.test_workbook_properties_sheet_uses_internal_headers_and_keeps_values_numeric -v
```

Expected: failures show that the page still applies `column_config`, lacks
`hide_index=True`, and Excel still renames retained headers.

- [ ] **Step 4: Remove the page-specific display policy**

Delete this import from `pages/3_EPISuite环境归趋.py`:

```python
from src.episuite_display import episuite_property_column_config
```

Replace the non-empty branch in `render_epi_web_tables` with:

```python
            if table is not None and not table.empty:
                st.dataframe(
                    table,
                    hide_index=True,
                    width="stretch",
                )
```

Update the Raw API JSON preview call in the same function to:

```python
            st.dataframe(
                raw_table[preview_cols],
                hide_index=True,
                width="stretch",
            )
```

Do not add numeric formatting or translated labels.

- [ ] **Step 5: Write internal headers directly to Excel**

Delete this import from `src/episuite_io.py`:

```python
from src.episuite_display import episuite_property_export_frame
```

Replace the EPI worksheet loop in `build_result_workbook` with:

```python
        for sheet_name in EPI_WEB_RESULT_SHEETS:
            table = epi_tables.get(sheet_name, pd.DataFrame())
            table.to_excel(writer, sheet_name=sheet_name, index=False)
```

Delete `src/episuite_display.py`; no production code should import it after
this step.

- [ ] **Step 6: Run all focused EPI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_display tests.test_episuite_cas_values -v
```

Expected: all tests pass. The page behavior test proves every rendered detail
table hides its index and receives no special `column_config`; the workbook
test proves internal headers and numeric values survive export.

- [ ] **Step 7: Commit the presentation and export cleanup**

```powershell
git add pages/3_EPISuite环境归趋.py src/episuite_io.py tests/test_episuite_display.py tests/test_episuite_cas_values.py
git rm src/episuite_display.py
git commit -m "refactor: unify EPI property presentation"
```

---

### Task 3: Run repository-level verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes all deliverables from Tasks 1-2.
- Produces fresh evidence for schema, rendering, workbook values, regressions, importability, and committed scope.

- [ ] **Step 1: Run the focused EPI modules**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_display tests.test_episuite_cas_values -v
```

Expected: every test reports `ok`; zero failures and zero errors.

- [ ] **Step 2: Run the full repository suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: `OK` with zero failures and zero errors.

- [ ] **Step 3: Compile application modules**

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0 with no syntax errors.

- [ ] **Step 4: Check whitespace and working-tree scope**

Run separately:

```powershell
git diff --check
```

```powershell
git status --short
```

Expected: `git diff --check` exits 0; only unrelated pre-existing untracked
files may remain.

- [ ] **Step 5: Audit the final diff against the design**

```powershell
git diff 638ce4c..HEAD -- src/episuite_properties.py src/episuite_display.py src/episuite_io.py pages/3_EPISuite环境归趋.py tests/test_episuite_properties.py tests/test_episuite_display.py tests/test_episuite_cas_values.py
```

Expected:

- Public output contains only `koawin_log_kaw`, `tpsa_rdkit_a2`, and `mr_rdkit_cm3_mol`.
- The five removed fields appear only in negative assertions or internal normalizer tests, never in public table construction.
- No special Properties label/format helper remains.
- Detail-table rendering uses `hide_index=True`.
- Excel writes internal headers and numeric values.
- No EPI API, dependency, cache, retry, checkpoint, TPSA/MR algorithm, or ΔHvap change is present.
