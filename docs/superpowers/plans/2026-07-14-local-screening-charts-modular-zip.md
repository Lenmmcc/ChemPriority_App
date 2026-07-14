# Local Screening Charts and Modular ZIP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the three existing local-screening figures in the one-click results dashboard and export every populated workflow module into its own ZIP folder while retaining the root aggregate workbook.

**Architecture:** Convert the local screening pipeline's PNG/PDF paths into `AutoWorkflowChart` byte objects before temporary paths leave scope, and carry them on `AutoWorkflowResult`. Use one ordered module-export contract in `src/auto_query_workflow.py` to build per-module workbooks and route chart prefixes into module-specific `figures/` folders; keep the existing aggregate workbook at ZIP root.

**Tech Stack:** Python 3.12, pandas, matplotlib, openpyxl, Streamlit, `zipfile`, `unittest`.

## Global Constraints

- Keep `Auto_Query_Workflow_Results.xlsx` at ZIP root.
- Show exactly the existing chemical-type distribution, DBE bubble, and Van Krevelen local-screening figures; do not change their plotting algorithms.
- Store chart payloads as PNG/PDF bytes, not temporary paths.
- Use ASCII ZIP folder and file names.
- Create module folders only when that module has a non-empty table or a chart.
- Do not change query order, cache behavior, concurrency settings, or other pages' download formats.
- Follow red-green-refactor for every production change.

---

### Task 1: Preserve local-screening figures in the workflow result

**Files:**
- Modify: `src/auto_query_workflow.py:96-109`
- Modify: `src/auto_query_workflow.py:142-224`
- Modify: `src/auto_query_workflow.py:403-441`
- Modify: `src/auto_query_workflow.py:555-606`
- Test: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: `ScreeningResult.figure_paths: dict[str, dict[str, Path]]`.
- Produces: `LocalScreeningOutput(tables, charts, warnings)` and `AutoWorkflowResult.charts: OrderedDict[str, AutoWorkflowChart]`.
- Produces chart keys `Local_Chemical_Type_Distribution`, `Local_DBE_Bubble_Plot`, and `Local_Van_Krevelen_Plot`.

- [ ] **Step 1: Write failing tests for loading local chart bytes**

Add imports for `SimpleNamespace`, `TemporaryDirectory`, `Path`, `AutoWorkflowChart`, `LocalScreeningOutput`, and `_load_local_screening_charts`. Add this test:

```python
def test_local_screening_chart_paths_become_portable_png_pdf_bytes(self):
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        figure_paths = {}
        for source_key in (
            "category_percent_donut_with_total",
            "compound_bubble_plot",
            "VanKrevelen",
        ):
            png_path = root / f"{source_key}.png"
            pdf_path = root / f"{source_key}.pdf"
            png_path.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
            pdf_path.write_bytes(b"%PDF-1.4 local")
            figure_paths[source_key] = {"png": png_path, "pdf": pdf_path}

        charts, warnings = _load_local_screening_charts(
            SimpleNamespace(figure_paths=figure_paths)
        )

    self.assertEqual(
        list(charts),
        [
            "Local_Chemical_Type_Distribution",
            "Local_DBE_Bubble_Plot",
            "Local_Van_Krevelen_Plot",
        ],
    )
    self.assertEqual(warnings, [])
    self.assertTrue(charts["Local_Chemical_Type_Distribution"].png.startswith(b"\x89PNG"))
    self.assertTrue(charts["Local_Van_Krevelen_Plot"].pdf.startswith(b"%PDF"))
```

Add a missing-file behavior test:

```python
def test_missing_local_screening_chart_is_skipped_with_warning(self):
    charts, warnings = _load_local_screening_charts(
        SimpleNamespace(
            figure_paths={
                "category_percent_donut_with_total": {
                    "png": Path("missing.png"),
                    "pdf": Path("missing.pdf"),
                }
            }
        )
    )

    self.assertEqual(charts, OrderedDict())
    self.assertEqual(len(warnings), 1)
    self.assertIn("Chemical Type Distribution", warnings[0])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_local_screening_chart_paths_become_portable_png_pdf_bytes tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_missing_local_screening_chart_is_skipped_with_warning -v
```

Expected: import failure because `_load_local_screening_charts` and `LocalScreeningOutput` do not exist.

- [ ] **Step 3: Add the chart and local-output contracts**

Place `AutoWorkflowChart` before `AutoWorkflowResult`, add the default chart collection, and define the local output:

```python
@dataclass(frozen=True)
class AutoWorkflowChart:
    title: str
    png: bytes
    pdf: bytes


@dataclass
class AutoWorkflowResult:
    mapping: AutoWorkflowMapping
    representative_table: pd.DataFrame
    tables: OrderedDict[str, pd.DataFrame]
    step_status: pd.DataFrame
    warnings: pd.DataFrame
    charts: OrderedDict[str, AutoWorkflowChart] = field(default_factory=OrderedDict)


@dataclass
class LocalScreeningOutput:
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, AutoWorkflowChart]
    warnings: list[str] = field(default_factory=list)
```

Add the exact figure contract and loader near `_run_r_replicate_df`:

```python
LOCAL_SCREENING_FIGURES = (
    (
        "category_percent_donut_with_total",
        "Local_Chemical_Type_Distribution",
        "Chemical Type Distribution",
    ),
    ("compound_bubble_plot", "Local_DBE_Bubble_Plot", "DBE Bubble Plot"),
    ("VanKrevelen", "Local_Van_Krevelen_Plot", "Van Krevelen Plot"),
)


def _load_local_screening_charts(screening_result):
    charts = OrderedDict()
    warnings = []
    for source_key, chart_key, title in LOCAL_SCREENING_FIGURES:
        paths = screening_result.figure_paths.get(source_key, {})
        png_path = paths.get("png")
        pdf_path = paths.get("pdf")
        try:
            png = Path(png_path).read_bytes() if png_path else b""
            pdf = Path(pdf_path).read_bytes() if pdf_path else b""
        except OSError as exc:
            warnings.append(f"{title}: {exc}")
            continue
        if not png.startswith(b"\x89PNG") or not pdf.startswith(b"%PDF"):
            warnings.append(f"{title}: generated PNG/PDF is missing or invalid.")
            continue
        charts[chart_key] = AutoWorkflowChart(title=title, png=png, pdf=pdf)
    return charts, warnings
```

- [ ] **Step 4: Run the two tests and verify GREEN**

Run the command from Step 2. Expected: both tests pass.

- [ ] **Step 5: Write a failing workflow-merging test**

Patch `_run_r_replicate_df` and assert local charts and warnings survive the top-level workflow:

```python
@patch("src.auto_query_workflow._run_r_replicate_df")
def test_workflow_preserves_local_screening_charts_and_warnings(self, mock_local):
    chart = AutoWorkflowChart("DBE Bubble Plot", b"\x89PNG\r\n\x1a\n", b"%PDF")
    mock_local.return_value = LocalScreeningOutput(
        tables=OrderedDict([("DF_Table", pd.DataFrame({"Name": ["A"]}))]),
        charts=OrderedDict([("Local_DBE_Bubble_Plot", chart)]),
        warnings=["Van Krevelen Plot: missing"],
    )

    result = run_auto_query_workflow(
        pd.DataFrame({"Name": ["A"], "NIST Lib Hit Formula": ["C2H6"], "Avg TIC": [2e5]}),
        AutoWorkflowConfig(run_identifier=False),
    )

    self.assertIs(result.charts["Local_DBE_Bubble_Plot"], chart)
    self.assertIn("Van Krevelen Plot: missing", result.warnings["message"].tolist())
```

- [ ] **Step 6: Run the workflow-merging test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_workflow_preserves_local_screening_charts_and_warnings -v
```

Expected: fail because `run_auto_query_workflow()` still treats the local output as a mapping and does not copy charts/warnings.

- [ ] **Step 7: Wire the local output through the workflow**

Initialize `charts` beside `tables`, merge the local output, and return charts:

```python
tables: OrderedDict[str, pd.DataFrame] = OrderedDict()
charts: OrderedDict[str, AutoWorkflowChart] = OrderedDict()
```

Replace the local merge block with:

```python
if local_value is not None:
    for key, table in local_value.tables.items():
        tables[key] = table
    charts.update(local_value.charts)
    for message in local_value.warnings:
        add_warning(R_DF_STEP_LABEL, message)
    record(R_DF_STEP_LABEL, "完成", len(local_value.tables.get("DF_Table", pd.DataFrame())))
```

Pass `charts=charts` into `AutoWorkflowResult`. In `_run_r_replicate_df`, keep the existing table construction in a local `tables` variable and return:

```python
charts, chart_warnings = _load_local_screening_charts(screening_result)
return LocalScreeningOutput(tables=tables, charts=charts, warnings=chart_warnings)
```

Start `build_auto_workflow_charts()` with:

```python
charts = OrderedDict(result.charts)
```

- [ ] **Step 8: Run focused workflow tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: all `test_auto_query_workflow` tests pass.

- [ ] **Step 9: Commit Task 1**

```powershell
git add -- src/auto_query_workflow.py tests/test_auto_query_workflow.py
git commit -m "fix: preserve local screening charts"
```

---

### Task 2: Route local charts into the Local Screening dashboard tab

**Files:**
- Modify: `pages/6_一键批量查询.py:85-159`
- Test: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: the combined chart mapping returned by `build_auto_workflow_charts()`.
- Produces: the existing `_result_dashboard_groups()` output with `Local_` chart keys assigned to the `screening` group.

- [ ] **Step 1: Write a failing page contract test**

```python
def test_page_6_assigns_local_screening_charts_to_local_tab(self):
    page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
    screening_definition = page_text.split('"screening"', 1)[1].split('"identifier"', 1)[0]
    self.assertIn('("Local_",)', screening_definition)
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_page_6_assigns_local_screening_charts_to_local_tab -v
```

Expected: fail because the current local-screening chart prefix tuple is empty.

- [ ] **Step 3: Add the local chart prefix**

Change the final entry of the `screening` group definition from `()` to:

```python
("Local_",),
```

- [ ] **Step 4: Run the page and workflow tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- pages/6_一键批量查询.py tests/test_auto_query_workflow.py
git commit -m "fix: show local charts in batch results"
```

---

### Task 3: Export module-specific workbooks and chart folders

**Files:**
- Modify: `src/auto_query_workflow.py:412-456`
- Test: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: `AutoWorkflowResult.tables` and `OrderedDict[str, AutoWorkflowChart]`.
- Produces: `_auto_workflow_export_modules()`, `_build_module_workbook()`, and the new nested ZIP layout.

- [ ] **Step 1: Replace the existing ZIP assertions with a failing modular-layout test**

Construct a result containing populated local, identifier, EPA, and ECHA tables plus local/EPA/ECHA charts. Assert these exact entries:

```python
expected = {
    "Auto_Query_Workflow_Results.xlsx",
    "01_Local_Screening/Local_Screening_Results.xlsx",
    "01_Local_Screening/figures/Chemical_Type_Distribution.png",
    "01_Local_Screening/figures/Chemical_Type_Distribution.pdf",
    "02_Identifier_Completion/Identifier_Completion_Results.xlsx",
    "04_EPA_CompTox/EPA_CompTox_Results.xlsx",
    "04_EPA_CompTox/figures/EPA_Top_Predicted_Functional_Use.png",
    "05_ECHA/ECHA_Results.xlsx",
    "05_ECHA/figures/ECHA_Use_Rose_Plot.pdf",
}
self.assertTrue(expected.issubset(names))
self.assertFalse(any(name.startswith("03_EPI_Suite/") for name in names))
```

Also open each module workbook from the ZIP with `pd.ExcelFile(io.BytesIO(...))` and assert it contains only its module's expected sheet names.

- [ ] **Step 2: Run the modular ZIP test and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_auto_workflow_zip_groups_results_by_module -v
```

Expected: fail because the current archive only contains the root workbook and `charts/`.

- [ ] **Step 3: Add the ordered export contract**

Define this constant near the chart configuration:

```python
AUTO_WORKFLOW_EXPORT_MODULES = (
    (
        "01_Local_Screening",
        "Local_Screening_Results.xlsx",
        (
            "Structure_Preparation", "Input_Check", "Elemental_Ratios_DBE",
            "Category_Summary", "DF_Table", "Sample_Peak_Area",
            "Group_Area_Raw_Long", "Group_Area_Mean_By_Sample",
        ),
        ("Local_",),
    ),
    (
        "02_Identifier_Completion",
        "Identifier_Completion_Results.xlsx",
        ("Identifier_Completion", "Identifier_Warnings"),
        (),
    ),
    (
        "03_EPI_Suite",
        "EPI_Suite_Results.xlsx",
        ("EPI_Results", "EPI_Raw_Results", "EPI_Errors"),
        (),
    ),
    (
        "04_EPA_CompTox",
        "EPA_CompTox_Results.xlsx",
        ("CompTox_Summary", "CompTox_Candidates", "CompTox_Errors"),
        ("EPA_",),
    ),
    (
        "05_ECHA",
        "ECHA_Results.xlsx",
        (
            "ECHA_Use_Summary", "ECHA_Use_Candidates", "ECHA_Use_Dossiers",
            "ECHA_Use_Errors", "ECHA_GHS_Summary",
            "ECHA_GHS_Classifications", "ECHA_GHS_Errors",
        ),
        ("ECHA_",),
    ),
    (
        "06_Source_Origin",
        "Source_Origin_Results.xlsx",
        ("Source_Origin_Summary", "Source_Origin_Evidence", "Source_Origin_Errors"),
        (),
    ),
    (
        "07_Pov_LRTP_PBM_ToxPi",
        "Pov_LRTP_PBM_ToxPi_Results.xlsx",
        ("Pov_LRTP_Input", "Pov_LRTP", "ToxPi_Input", "ToxPi_Normalized", "ToxPi_Results"),
        (),
    ),
)
```

- [ ] **Step 4: Add the module-workbook helper**

```python
def _build_module_workbook(result: AutoWorkflowResult, table_names: tuple[str, ...]) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name in table_names:
            table = result.tables.get(name)
            if isinstance(table, pd.DataFrame) and not table.empty:
                table.to_excel(writer, sheet_name=_safe_sheet_name(name), index=False)
    buffer.seek(0)
    return buffer


def _module_chart_file_name(chart_key: str, chart_prefixes: tuple[str, ...]) -> str:
    for prefix in chart_prefixes:
        if chart_key.startswith(prefix):
            return chart_key[len(prefix):]
    return chart_key
```

- [ ] **Step 5: Replace the flat chart export with module routing**

Keep the root workbook write, then iterate over `AUTO_WORKFLOW_EXPORT_MODULES`. Select non-empty tables and matching chart keys. Skip the module when both collections are empty. Write the module workbook only when there is at least one selected table; write each chart to `<module>/figures/<stem>.png` and `.pdf` using `_module_chart_file_name()`.

Use this control flow:

```python
archive.writestr("Auto_Query_Workflow_Results.xlsx", build_auto_workflow_workbook(result).getvalue())
for folder, workbook_name, table_candidates, chart_prefixes in AUTO_WORKFLOW_EXPORT_MODULES:
    table_names = tuple(
        name for name in table_candidates
        if isinstance(result.tables.get(name), pd.DataFrame) and not result.tables[name].empty
    )
    chart_keys = tuple(
        key for key in charts
        if any(key.startswith(prefix) for prefix in chart_prefixes)
    )
    if not table_names and not chart_keys:
        continue
    if table_names:
        workbook = _build_module_workbook(result, table_names)
        archive.writestr(f"{folder}/{workbook_name}", workbook.getvalue())
    for key in chart_keys:
        stem = _module_chart_file_name(key, chart_prefixes)
        archive.writestr(f"{folder}/figures/{stem}.png", charts[key].png)
        archive.writestr(f"{folder}/figures/{stem}.pdf", charts[key].pdf)
```

- [ ] **Step 6: Run the modular ZIP test and verify GREEN**

Run the command from Step 2. Expected: pass.

- [ ] **Step 7: Run all workflow and plotting tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_cp_screening_workflow tests.test_use_rose_plot -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add -- src/auto_query_workflow.py tests/test_auto_query_workflow.py
git commit -m "feat: group batch exports by module"
```

---

### Task 4: Complete regression and live artifact verification

**Files:**
- Verify: `src/auto_query_workflow.py`
- Verify: `pages/6_一键批量查询.py`
- Verify: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Verifies the page, result object, aggregate workbook, module workbooks, and chart bytes as one completed flow.

- [ ] **Step 1: Run the full repository test suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: exit code 0 with no failures or errors.

- [ ] **Step 2: Compile the application**

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0.

- [ ] **Step 3: Generate a representative local-only workflow result**

Use a small dataframe with valid names, formulas, and peak areas above the detection threshold. Run `run_auto_query_workflow()` with `run_identifier=False`, then call `build_auto_workflow_charts()` and `build_auto_workflow_zip()`.

Assert in the verification command output:

- three `Local_` chart keys exist;
- every local PNG starts with the PNG signature and every PDF starts with `%PDF`;
- the ZIP contains the root aggregate workbook;
- `01_Local_Screening/Local_Screening_Results.xlsx` exists;
- all six local chart files exist below `01_Local_Screening/figures/`.

- [ ] **Step 4: Start Streamlit and inspect the local page**

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

Open “一键批量查询”, run the local screening step, and confirm the “本地筛查” tab displays Chemical Type Distribution, DBE Bubble Plot, and Van Krevelen Plot below the tables.

- [ ] **Step 5: Inspect the downloaded ZIP**

Confirm the root aggregate workbook remains available, populated modules have separate folders/workbooks, and no folder exists for an unselected empty module.

- [ ] **Step 6: Commit only if verification required a correction**

If no files changed, do not create an empty commit. If a correction was required, repeat its failing test first, then stage only `src/auto_query_workflow.py`, `pages/6_一键批量查询.py`, and `tests/test_auto_query_workflow.py` before committing with a message describing that correction.
