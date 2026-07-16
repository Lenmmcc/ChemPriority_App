# VK Region Filtering and PUC Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide VK regions whose label centers fall outside custom axes, and replace the per-compound PUC rose plot with one compound-count distribution donut on both result pages.

**Architecture:** Keep VK visibility logic inside the shared VK drawer so every caller receives identical behavior. Add a PUC-specific classification entry point that reuses the existing unique-top evidence algorithm, then feed its classification table into the existing compound-classification donut renderer and both workflow surfaces.

**Tech Stack:** Python 3, pandas, Matplotlib, Streamlit, unittest, openpyxl

## Global Constraints

- A VK region is rendered only when `vk_x_min <= label_x <= vk_x_max` and `vk_y_min <= label_y <= vk_y_max`.
- A label center exactly on an axis boundary remains visible.
- PUC reads only `source_type == "product_category"` candidates and groups by the English raw PUC label.
- Each deduplicated input compound contributes exactly once: unique highest evidence category wins; ties and missing results become `Others`.
- Missing, non-numeric, zero, or negative `evidence_count` contributes `1.0` for that candidate.
- Do not truncate PUC categories or merge low-frequency categories into `Others`.
- Do not change the existing Functional Use Distribution classification result.
- Replace `EPA_Product_Use_Category_Rose_Plot` with `EPA_Product_Use_Category_Distribution`; do not retain a duplicate PUC rose plot.
- Preserve the existing `Product_Use_Categories` detail table and add `EPA_PUC_Pie_Data` as the classification audit table.

---

## File Structure

- Modify `src/r_screening_replica/plots.py`: central VK region visibility filter.
- Modify `src/use_rose_plot.py`: PUC classification entry point and PUC-specific explanatory note.
- Modify `src/auto_query_workflow.py`: PUC audit table, chart configuration, workbook/ZIP allowlists, and new artifact name.
- Modify `pages/4_化合物用途查询.py`: fourth-page PUC distribution data and renderer configuration.
- Modify `pages/6_一键批量查询.py`: expose `EPA_PUC_Pie_Data` in the EPA CompTox result group.
- Modify `tests/test_r_screening_replica.py`: VK X/Y/boundary regressions.
- Modify `tests/test_use_rose_plot.py`: PUC classification and donut regressions.
- Modify `tests/test_auto_query_workflow.py`: one-click tables, charts, workbooks, ZIP artifacts, and page-six surface.
- Modify `tests/test_cp_screening_workflow.py`: fourth-page configuration regression.

### Task 1: Filter VK Regions by Visible Label Center

**Files:**
- Modify: `src/r_screening_replica/plots.py:364-385`
- Test: `tests/test_r_screening_replica.py:50-95`

**Interfaces:**
- Consumes: `ScreeningAxisRanges.vk_xlim`, `ScreeningAxisRanges.vk_ylim`, and each `VK_REGIONS` tuple.
- Produces: `_vk_region_label_is_visible(label_x: float, label_y: float, axis_ranges: ScreeningAxisRanges) -> bool`; `_draw_van_krevelen()` uses it before adding a rectangle or label.

- [ ] **Step 1: Write failing VK visibility tests**

Add these methods to `RScreeningReplicaUnitTests`:

```python
def test_vk_omits_regions_with_label_centers_outside_x_range(self):
    ranges = ScreeningAxisRanges(vk_x_min=0.0, vk_x_max=0.7, vk_y_min=0.0, vk_y_max=2.6)
    data = pd.DataFrame({"o_c": [0.5], "h_c": [1.2], "Category": ["CHO"]})
    fig, ax = plt.subplots()
    try:
        _draw_van_krevelen(ax, data, ranges)
        labels = {text.get_text() for text in ax.texts}
        self.assertNotIn("Carbohydrates-like", labels)
        self.assertNotIn("Highly Oxygenated Compounds", labels)
        self.assertEqual(len(ax.patches), 5)
    finally:
        plt.close(fig)

def test_vk_omits_regions_with_label_centers_outside_y_range(self):
    ranges = ScreeningAxisRanges(vk_x_min=0.0, vk_x_max=1.1, vk_y_min=0.0, vk_y_max=1.8)
    data = pd.DataFrame({"o_c": [0.5], "h_c": [1.2], "Category": ["CHO"]})
    fig, ax = plt.subplots()
    try:
        _draw_van_krevelen(ax, data, ranges)
        labels = {text.get_text() for text in ax.texts}
        self.assertIn("Lipids-like", labels)
        self.assertNotIn("Aliphatic/Peptides-like", labels)
        self.assertNotIn("Carbohydrates-like", labels)
    finally:
        plt.close(fig)

def test_vk_keeps_region_when_label_center_is_on_axis_boundary(self):
    ranges = ScreeningAxisRanges(vk_x_min=0.0, vk_x_max=0.85, vk_y_min=0.0, vk_y_max=2.0)
    data = pd.DataFrame({"o_c": [0.5], "h_c": [1.2], "Category": ["CHO"]})
    fig, ax = plt.subplots()
    try:
        _draw_van_krevelen(ax, data, ranges)
        labels = {text.get_text() for text in ax.texts}
        self.assertIn("Carbohydrates-like", labels)
        self.assertIn("Highly Oxygenated Compounds", labels)
    finally:
        plt.close(fig)
```

- [ ] **Step 2: Run the VK tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_r_screening_replica.RScreeningReplicaUnitTests.test_vk_omits_regions_with_label_centers_outside_x_range tests.test_r_screening_replica.RScreeningReplicaUnitTests.test_vk_omits_regions_with_label_centers_outside_y_range tests.test_r_screening_replica.RScreeningReplicaUnitTests.test_vk_keeps_region_when_label_center_is_on_axis_boundary -v
```

Expected: the first two tests fail because out-of-range labels and patches are still present; the boundary test passes or remains green.

- [ ] **Step 3: Implement the inclusive visibility check**

Add immediately before `_draw_van_krevelen()`:

```python
def _vk_region_label_is_visible(
    label_x: float,
    label_y: float,
    axis_ranges: ScreeningAxisRanges,
) -> bool:
    x_min, x_max = axis_ranges.vk_xlim
    y_min, y_max = axis_ranges.vk_ylim
    return x_min <= label_x <= x_max and y_min <= label_y <= y_max

```

Replace the current `for label, xmin, ... in VK_REGIONS` block inside `_draw_van_krevelen()` with:

```python
for label, xmin, xmax, ymin, ymax, label_x, label_y in VK_REGIONS:
    if not _vk_region_label_is_visible(label_x, label_y, axis_ranges):
        continue
    ax.add_patch(
        Rectangle(
            (xmin, ymin),
            xmax - xmin,
            ymax - ymin,
            fill=False,
            edgecolor="#333333",
            linestyle="--",
            linewidth=1.0,
        )
    )
    ax.text(
        label_x,
        label_y,
        label,
        ha="center",
        va="center",
        fontsize=12,
        fontweight="bold",
        color="#333333",
        family=font_family,
    )
```

- [ ] **Step 4: Run all screening-plot tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_r_screening_replica -v
```

Expected: all `tests.test_r_screening_replica` tests pass.

- [ ] **Step 5: Commit the VK change**

```powershell
git add src/r_screening_replica/plots.py tests/test_r_screening_replica.py
git commit -m "fix: hide VK regions outside custom axes"
```

### Task 2: Add PUC Unique-Top Classification Data

**Files:**
- Modify: `src/use_rose_plot.py:58-214`
- Test: `tests/test_use_rose_plot.py:1-225`

**Interfaces:**
- Consumes: `candidates_df: pandas.DataFrame`, `compound_universe: pandas.DataFrame`, and the existing `extract_top_reported_functional_use_data()` evidence aggregation behavior.
- Produces: `extract_top_product_use_category_data(candidates_df, compound_universe, source_label="EPA PUC") -> pandas.DataFrame` with `COMPOUND_CLASSIFICATION_COLUMNS`; `PRODUCT_USE_CATEGORY_OTHERS_NOTE: str`.

- [ ] **Step 1: Write failing PUC classification tests**

Import `extract_top_product_use_category_data`, then add:

```python
def test_puc_classification_uses_unique_top_tie_missing_and_fallback_weight(self):
    universe = build_compound_universe(
        pd.DataFrame({"compound": ["A", "B", "C", "D", "E", "A"]})
    )
    candidates = pd.DataFrame(
        [
            {"compound": "A", "source_type": "product_category", "raw_use": "Food contact", "evidence_count": 2},
            {"compound": "A", "source_type": "product_category", "raw_use": "Food contact", "evidence_count": 1},
            {"compound": "A", "source_type": "product_category", "raw_use": "Consumer products", "evidence_count": 2},
            {"compound": "B", "source_type": "product_category", "raw_use": "Industrial", "evidence_count": 2},
            {"compound": "B", "source_type": "product_category", "raw_use": "Commercial", "evidence_count": 2},
            {"compound": "D", "source_type": "product_category", "raw_use": "Household", "evidence_count": None},
            {"compound": "E", "source_type": "product_category", "raw_use": "Manufacturing", "evidence_count": -3},
            {"compound": "A", "source_type": "functional_use", "raw_use": "Solvent", "evidence_count": 99},
        ]
    )

    result = extract_top_product_use_category_data(candidates, universe).set_index("compound")

    self.assertEqual(len(result), 5)
    self.assertEqual(result.loc["A", "display_label"], "Food contact")
    self.assertEqual(result.loc["A", "evidence_count"], 3)
    self.assertEqual(result.loc["A", "classification_reason"], "unique_top_product_use_category")
    self.assertEqual(result.loc["B", "display_label"], "Others")
    self.assertEqual(result.loc["B", "classification_reason"], "tie_for_top_product_use_category")
    self.assertEqual(result.loc["C", "classification_reason"], "no_product_use_category_result")
    self.assertEqual(result.loc["D", "evidence_count"], 1)
    self.assertEqual(result.loc["E", "evidence_count"], 1)

def test_puc_classification_returns_all_others_for_empty_candidates(self):
    universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B"]}))

    result = extract_top_product_use_category_data(pd.DataFrame(), universe)

    self.assertEqual(result["display_label"].tolist(), ["Others", "Others"])
    self.assertEqual(
        result["classification_reason"].tolist(),
        ["no_product_use_category_result", "no_product_use_category_result"],
    )

def test_puc_distribution_donut_preserves_compound_total(self):
    universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B", "C"]}))
    plot_df = extract_top_product_use_category_data(
        pd.DataFrame(
            [
                {"compound": "A", "source_type": "product_category", "raw_use": "Industrial", "evidence_count": 2},
                {"compound": "B", "source_type": "product_category", "raw_use": "Consumer", "evidence_count": 1},
            ]
        ),
        universe,
    )
    figure = generate_compound_classification_pie_plot(
        plot_df,
        "EPA CompTox Product-Use Category Distribution",
        footnote=PRODUCT_USE_CATEGORY_OTHERS_NOTE,
    )
    try:
        self.assertIn("Total compounds\n3", {text.get_text() for text in figure.axes[0].texts})
        legend_labels = {
            text.get_text()
            for legend in figure.legends
            for text in legend.get_texts()
        }
        self.assertEqual(
            legend_labels,
            {"Consumer (1, 33.3%)", "Industrial (1, 33.3%)", "Others (1, 33.3%)"},
        )
    finally:
        plt.close(figure)
```

Also import `PRODUCT_USE_CATEGORY_OTHERS_NOTE` in the test module.

- [ ] **Step 2: Run the PUC tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_use_rose_plot.UseRosePlotTests.test_puc_classification_uses_unique_top_tie_missing_and_fallback_weight tests.test_use_rose_plot.UseRosePlotTests.test_puc_classification_returns_all_others_for_empty_candidates tests.test_use_rose_plot.UseRosePlotTests.test_puc_distribution_donut_preserves_compound_total -v
```

Expected: import failure because the PUC function and note do not yet exist.

- [ ] **Step 3: Implement the PUC-specific entry point**

Add beside `REPORTED_OTHERS_NOTE`:

```python
PRODUCT_USE_CATEGORY_OTHERS_NOTE = (
    "Slice size = number of compounds by unique top product-use category. "
    "Others includes compounds with no product-use category result or with a tie "
    "for the highest-evidence category."
)
```

Add after `extract_top_reported_functional_use_data()`:

```python
def extract_top_product_use_category_data(
    candidates_df,
    compound_universe,
    source_label="EPA PUC",
):
    """Classify every universe compound by its unique top product-use category."""
    result = extract_top_reported_functional_use_data(
        candidates_df,
        compound_universe,
        source_label=source_label,
        source_type="product_category",
        use_key="raw",
        require_reported_flag=False,
    )
    if result.empty:
        return result
    reason_map = {
        "unique_top_reported_category": "unique_top_product_use_category",
        "tie_for_top_reported_category": "tie_for_top_product_use_category",
        "no_reported_result": "no_product_use_category_result",
    }
    result = result.copy()
    result["classification_reason"] = result["classification_reason"].replace(reason_map)
    return result
```

- [ ] **Step 4: Run the complete use-plot test module**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_use_rose_plot -v
```

Expected: all `tests.test_use_rose_plot` tests pass, including existing Functional Use tests unchanged.

- [ ] **Step 5: Commit the PUC data layer**

```powershell
git add src/use_rose_plot.py tests/test_use_rose_plot.py
git commit -m "feat: classify compounds by top PUC category"
```

### Task 3: Replace the One-Click PUC Rose Plot and Export Its Audit Table

**Files:**
- Modify: `src/auto_query_workflow.py:40-110, 475-525, 765-890`
- Modify: `pages/6_一键批量查询.py:96-140`
- Test: `tests/test_auto_query_workflow.py:420-465, 790-965, 1060-1090, 1370-1440`

**Interfaces:**
- Consumes: `extract_top_product_use_category_data(...)`, `PRODUCT_USE_CATEGORY_OTHERS_NOTE`, and `generate_compound_classification_pie_plot(...)` from Task 2.
- Produces: `result.tables["EPA_PUC_Pie_Data"]`; chart key `EPA_Product_Use_Category_Distribution`; module workbook sheet `EPA_PUC_Pie_Data`; PNG/PDF files under `04_EPA_CompTox/figures/`.

- [ ] **Step 1: Update one-click tests to require the new table and artifact names**

Make these exact expectation changes:

```python
# Imports and _example_pie_tables()
from src.use_rose_plot import extract_top_product_use_category_data

(
    "EPA_PUC_Pie_Data",
    extract_top_product_use_category_data(comptox_candidates, universe),
),

# Full-workflow expected tables
for table_name in (
    "Product_Use_Categories",
    "Functional_Uses_Predicted",
    "Functional_Uses_Reported",
    "EPA_PUC_Pie_Data",
    "EPA_Predicted_Pie_Data",
    "EPA_Reported_Pie_Data",
    "ECHA_Uses_Reported",
    "ECHA_Reported_Pie_Data",
    "Source_Origin_Pie_Data",
):
    self.assertIn(table_name, result.tables)
self.assertEqual(len(result.tables["EPA_PUC_Pie_Data"]), 3)

# Full-universe and partial-identifier audit loops
for table_name in (
    "EPA_PUC_Pie_Data",
    "EPA_Predicted_Pie_Data",
    "EPA_Reported_Pie_Data",
    "ECHA_Reported_Pie_Data",
    "Source_Origin_Pie_Data",
):
    table = result.tables[table_name]
    self.assertEqual(len(table), 3)
    self.assertEqual(table["compound_key"].nunique(), 3)

# Selected-module-exception table and fallback checks
for table_name, missing_label in (
    ("EPA_PUC_Pie_Data", "Others"),
    ("EPA_Predicted_Pie_Data", "Others"),
    ("EPA_Reported_Pie_Data", "Others"),
    ("ECHA_Uses_Reported", "Others"),
    ("ECHA_Reported_Pie_Data", "Others"),
    ("Source_Origin_Pie_Data", "Unknown"),
):
    table = result.tables[table_name]
    self.assertEqual(len(table), 3)
    self.assertEqual(table["compound_key"].nunique(), 3)
    self.assertEqual(set(table["display_label"]), {missing_label})

self.assertNotIn("EPA_PUC_Pie_Data", source_only.tables)

# Expected chart key
"EPA_Product_Use_Category_Distribution",

# Expected ZIP entries
"04_EPA_CompTox/figures/EPA_Product_Use_Category_Distribution.png",
"04_EPA_CompTox/figures/EPA_Product_Use_Category_Distribution.pdf",

# Expected EPA workbook sheets
[
    "Product_Use_Categories",
    "Functional_Uses_Predicted",
    "Functional_Uses_Reported",
    "EPA_PUC_Pie_Data",
    "EPA_Predicted_Pie_Data",
    "EPA_Reported_Pie_Data",
]
```

In `test_chart_map_and_zip_use_exact_chart_allowlists`, replace only the old PUC key with `EPA_Product_Use_Category_Distribution`. In `test_page_6_groups_results_into_module_dashboard_tabs`, add:

```python
self.assertIn('"EPA_PUC_Pie_Data"', page_text)
```

Add a direct chart-shape assertion inside `test_auto_workflow_charts_are_generated_from_use_candidates`:

```python
puc_chart = charts["EPA_Product_Use_Category_Distribution"]
self.assertEqual(puc_chart.title, "EPA CompTox Product-Use Category Distribution")
self.assertTrue(puc_chart.png.startswith(b"\x89PNG\r\n\x1a\n"))
self.assertTrue(puc_chart.pdf.startswith(b"%PDF"))
self.assertNotIn("EPA_Product_Use_Category_Rose_Plot", charts)
```

- [ ] **Step 2: Run the one-click tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: failures report missing `EPA_PUC_Pie_Data`, the old Rose Plot chart key, and missing new ZIP/workbook entries.

- [ ] **Step 3: Build and export `EPA_PUC_Pie_Data`**

Import the new function and note. Add `"EPA_PUC_Pie_Data"` to the EPA module table allowlist. In both the successful and failed CompTox branches, add:

```python
tables["EPA_PUC_Pie_Data"] = extract_top_product_use_category_data(
    comptox_candidates,
    compound_universe,
)
```

This call must remain independent of candidate emptiness so an existing compound universe produces an all-`Others` table.

- [ ] **Step 4: Replace the one-click chart source**

Replace the candidates-based PUC `rose` source with:

```python
puc_pie = result.tables.get("EPA_PUC_Pie_Data")
if isinstance(puc_pie, pd.DataFrame) and not puc_pie.empty:
    chart_sources.append(
        {
            "chart_type": "classification_pie",
            "table_df": puc_pie,
            "title": "EPA CompTox Product-Use Category Distribution",
            "file_prefix": "EPA_Product_Use_Category_Distribution",
            "footnote": PRODUCT_USE_CATEGORY_OTHERS_NOTE,
        }
    )
```

Change the EPA module chart allowlist entry to `"EPA_Product_Use_Category_Distribution"`. Existing `_build_chart_data()` and `_build_chart_figure()` already route `classification_pie` tables through `generate_compound_classification_pie_plot()` and pass the configured footnote.

- [ ] **Step 5: Expose the audit table on page six**

Add `"EPA_PUC_Pie_Data"` immediately after `"Product_Use_Categories"` in the EPA CompTox table list in `_result_dashboard_groups()`:

```python
[
    "CompTox_Summary",
    "Product_Use_Categories",
    "EPA_PUC_Pie_Data",
    "Functional_Uses_Predicted",
    "Functional_Uses_Reported",
    "EPA_Predicted_Pie_Data",
    "EPA_Reported_Pie_Data",
    "CompTox_Errors",
]
```

- [ ] **Step 6: Run one-click tests and compile the changed modules**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
.\.venv\Scripts\python.exe -m compileall src/auto_query_workflow.py pages/6_一键批量查询.py
```

Expected: all one-click tests pass and compileall reports both files compiled without errors.

- [ ] **Step 7: Commit the one-click integration**

```powershell
git add src/auto_query_workflow.py pages/6_一键批量查询.py tests/test_auto_query_workflow.py
git commit -m "feat: show PUC distribution in one-click results"
```

### Task 4: Replace the Fourth-Page PUC Rose Plot

**Files:**
- Modify: `pages/4_化合物用途查询.py:75-90, 1055-1100, 1165-1235`
- Test: `tests/test_cp_screening_workflow.py:950-970`

**Interfaces:**
- Consumes: `extract_top_product_use_category_data(...)`, `PRODUCT_USE_CATEGORY_OTHERS_NOTE`, and `generate_compound_classification_pie_plot(...)` from Task 2.
- Produces: fourth-page chart config with `chart_type == "classification_pie"`, one PUC donut, visible classification audit rows, and PNG/PDF downloads named `EPA_Product_Use_Category_Distribution`.

- [ ] **Step 1: Write a failing fourth-page source-contract test**

Add to `tests/test_cp_screening_workflow.py`:

```python
def test_fourth_page_uses_single_puc_distribution_instead_of_rose_plot(self):
    page_text = Path("pages/4_化合物用途查询.py").read_text(encoding="utf-8")

    self.assertIn("extract_top_product_use_category_data", page_text)
    self.assertIn("EPA CompTox Product-Use Category Distribution", page_text)
    self.assertIn("EPA_Product_Use_Category_Distribution", page_text)
    self.assertIn("PRODUCT_USE_CATEGORY_OTHERS_NOTE", page_text)
    self.assertNotIn("EPA CompTox Product-Use Category Rose Plot", page_text)
    self.assertNotIn("EPA_Product_Use_Category_Rose_Plot", page_text)
```

- [ ] **Step 2: Run the source-contract test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow.CpScreeningWorkflowTests.test_fourth_page_uses_single_puc_distribution_instead_of_rose_plot -v
```

Expected: failure because the fourth page still contains the old PUC title and file prefix.

- [ ] **Step 3: Build the fourth-page PUC classification table**

Import `extract_top_product_use_category_data` and `PRODUCT_USE_CATEGORY_OTHERS_NOTE`. Immediately after building `compound_universe`, add:

```python
epa_puc_pie_df = extract_top_product_use_category_data(
    comptox_candidates_df,
    compound_universe,
)
```

- [ ] **Step 4: Replace the PUC chart configuration**

Replace the old candidates-based source with:

```python
if not epa_puc_pie_df.empty:
    chart_sources["EPA CompTox 产品用途类别分布"] = {
        "chart_type": "classification_pie",
        "table_df": epa_puc_pie_df,
        "title": "EPA CompTox Product-Use Category Distribution",
        "file_prefix": "EPA_Product_Use_Category_Distribution",
        "footnote": PRODUCT_USE_CATEGORY_OTHERS_NOTE,
    }
```

The existing classification-data preview and generic classification renderer already display `display_label`, `evidence_count`, and `classification_reason`, and pass `footnote` to `generate_compound_classification_pie_plot()`.

- [ ] **Step 5: Run fourth-page tests and compile the page**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow -v
.\.venv\Scripts\python.exe -m compileall pages/4_化合物用途查询.py
```

Expected: all fourth-page workflow tests pass and the page compiles without errors.

- [ ] **Step 6: Commit the fourth-page integration**

```powershell
git add pages/4_化合物用途查询.py tests/test_cp_screening_workflow.py
git commit -m "feat: replace PUC rose plot with distribution donut"
```

### Task 5: Run Cross-Feature Verification

**Files:**
- Verify: `src/r_screening_replica/plots.py`
- Verify: `src/use_rose_plot.py`
- Verify: `src/auto_query_workflow.py`
- Verify: `pages/4_化合物用途查询.py`
- Verify: `pages/6_一键批量查询.py`
- Verify: `tests/`

**Interfaces:**
- Consumes: all deliverables from Tasks 1-4.
- Produces: evidence that focused tests, the complete suite, compilation, artifact naming, and repository scope are correct.

- [ ] **Step 1: Run focused regressions together**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_r_screening_replica tests.test_use_rose_plot tests.test_auto_query_workflow tests.test_cp_screening_workflow -v
```

Expected: all focused modules pass with `OK`.

- [ ] **Step 2: Run the complete repository test suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass with final status `OK`.

- [ ] **Step 3: Compile application modules and pages**

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: compilation completes without syntax errors.

- [ ] **Step 4: Check old PUC artifact names are absent from runtime code**

```powershell
rg -n "EPA_Product_Use_Category_Rose_Plot|EPA CompTox Product-Use Category Rose Plot" src pages
```

Expected: no matches and exit code 1. Tests and historical documentation may retain the old string only in negative assertions or migration notes.

- [ ] **Step 5: Review final scope**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Expected: `git diff --check` is silent; only the requested code/tests plus pre-existing unrelated untracked files are present; the four implementation commits appear in the recent log.
