# CompTox Functional-Use Vocabulary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the conservative CompTox predicted functional-use vocabulary while ensuring the predicted-use pie chart keeps every selected English EPA label and uses `Others` only for compounds without a valid predicted result.

**Architecture:** Keep Chinese classification in the existing ordered `USE_TRANSLATION_RULES` and preserve EPA's raw English label in candidate records. Keep highest-probability selection in `extract_top_predicted_functional_use_data`, but remove the predicted pie's presentation-layer category cap so rare valid labels are never relabeled as `Others`.

**Tech Stack:** Python 3, pandas, matplotlib, unittest, Streamlit export workflow.

## Global Constraints

- Preserve the original EPA English functional-use value and probability.
- The predicted pie groups by the raw English value, not the Chinese translation.
- Each compound contributes exactly one highest-probability predicted result whose `probability` is finite and within `[0, 1]`; do not substitute `evidence_count`.
- `Others` represents only compounds without a valid predicted result.
- Do not map `vinyl`.
- Do not change product-use, ECHA REACH, or reported functional-use semantics.
- Preserve all existing uncommitted workspace changes and do not commit or push without explicit user authorization.

---

### Task 1: Expand the explicit CompTox functional-use vocabulary

**Files:**
- Modify: `src/comptox_use.py:145-190`
- Test: `tests/test_comptox_dashboard_mode.py:579-615`

**Interfaces:**
- Consumes: `classify_use_cn(*texts) -> str`, which normalizes underscores, hyphens, slashes, whitespace, and case.
- Produces: the existing `use_cn` candidate field with a professional Chinese category or an empty string for unmapped nonblank terms.

- [ ] **Step 1: Write the failing vocabulary test**

Extend `test_functional_use_translation_handles_predicted_use_labels` with exact expected mappings:

```python
expected = {
    "crosslinker": "交联剂",
    "cross-linking_agent": "交联剂",
    "heat_stabilizer": "热稳定剂",
    "thermal stabilizer": "热稳定剂",
    "emollient": "润肤剂",
    "hair_conditioner": "护发剂",
    "buffering_agent": "缓冲剂",
    "photoinitiator": "光引发剂",
    "preservative": "防腐剂",
    "humectant": "保湿剂",
    "adhesion_promoter": "附着力促进剂",
    "wetting_agent": "润湿剂",
    "reducing_agent": "还原剂",
    "emulsion_stabilizer": "乳液稳定剂",
}
for raw_use, expected_cn in expected.items():
    self.assertEqual(comptox_use.classify_use_cn(raw_use), expected_cn)
self.assertEqual(comptox_use.classify_use_cn("vinyl"), "")
```

- [ ] **Step 2: Write the failing table and summary regression**

Add a predicted `crosslinker` candidate and assert:

```python
candidate = {
    "compound": "Example",
    "dtxsid": "DTXSID0000001",
    "source_type": "functional_use",
    "source": "dashboard:functional_use",
    "raw_use": "crosslinker",
    "use_cn": comptox_use.classify_use_cn("crosslinker"),
    "reported_use": "",
    "harmonized_use": "crosslinker",
    "evidence_count": 0.475,
    "probability": 0.475,
    "functional_use_source": "predicted",
}
functional_df = comptox_use.build_functional_use_table(
    pd.DataFrame([candidate]),
    functional_source="predicted",
)
self.assertEqual(functional_df.loc[0, "功能用途"], "交联剂")
self.assertEqual(functional_df.loc[0, "英文功能用途"], "crosslinker")
self.assertEqual(
    comptox_use._format_source_type_uses(
        [candidate],
        "functional_use",
        functional_source="predicted",
    ),
    "交联剂 (crosslinker, p=0.475)",
)
```

- [ ] **Step 3: Run the vocabulary tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode.CompToxDashboardModeTests.test_functional_use_translation_handles_predicted_use_labels -v
```

Expected: failure because `crosslinker` and the other new terms currently return an empty Chinese category.

- [ ] **Step 4: Add specific ordered translation rules**

Insert these rules before broader categories such as personal care, adhesive, additive, and monomer:

```python
(("crosslinker", "crosslinking agent", "cross linking agent"), "交联剂"),
(("heat stabilizer", "thermal stabilizer"), "热稳定剂"),
(("emollient",), "润肤剂"),
(("hair conditioner", "hair conditioning agent"), "护发剂"),
(("buffering agent",), "缓冲剂"),
(("photoinitiator", "photo initiator"), "光引发剂"),
(("preservative",), "防腐剂"),
(("humectant",), "保湿剂"),
(("adhesion promoter", "adhesion promoting agent"), "附着力促进剂"),
(("wetting agent",), "润湿剂"),
(("reducing agent",), "还原剂"),
(("emulsion stabilizer",), "乳液稳定剂"),
```

Handle the exact normalized labels `buffer` and `reducer` separately so they do not match inside unrelated phrases such as `unbuffered solution` or `friction reducer`.

Do not add a `vinyl` rule.

- [ ] **Step 5: Run the focused CompTox tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode -v
```

Expected: all CompTox dashboard-mode tests pass.

---

### Task 2: Preserve every valid English category in the predicted pie

**Files:**
- Modify: `src/use_rose_plot.py:1356-1405`
- Test: `tests/test_use_rose_plot.py:1034-1085`

**Interfaces:**
- Consumes: `extract_top_predicted_functional_use_data(candidates_df, source_label="EPA FC", compound_universe=None)`.
- Produces: one row per compound with raw English `use_label`; `generate_top_predicted_functional_use_pie_plot(plot_df, title)` renders every distinct selected English category.

- [ ] **Step 1: Write the failing no-category-collapse test**

Create thirteen compounds with thirteen distinct English labels:

```python
plot_df = pd.DataFrame(
    [
        {
            "compound": f"Compound {index:02d}",
            "compound_label": f"Compound {index:02d}",
            "use_cn": f"用途 {index:02d}",
            "use_label": f"raw_use_{index:02d}",
            "display_label": f"raw_use_{index:02d}",
            "probability": 0.9,
            "status": "predicted",
        }
        for index in range(13)
    ]
)
fig = generate_top_predicted_functional_use_pie_plot(plot_df, "Top Predicted")
try:
    legend_labels = [
        text.get_text()
        for legend in fig.legends
        for text in legend.get_texts()
    ]
    self.assertEqual(len(fig.axes[0].patches), 13)
    for index in range(13):
        self.assertTrue(
            any(f"raw_use_{index:02d}" in label for label in legend_labels)
        )
    self.assertFalse(any(label.startswith("Others") for label in legend_labels))
finally:
    plt.close(fig)
```

- [ ] **Step 2: Run the new pie test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_use_rose_plot.UseRosePlotTests.test_top_predicted_pie_keeps_all_existing_english_categories -v
```

Expected: failure because the current predicted pie caps categories and merges the remainder into `Others`.

- [ ] **Step 3: Remove only the predicted pie category cap**

In `_summarize_top_predicted_functional_use`, keep grouping by raw English `_use_key` and counting unique compounds, but remove the block that truncates to `TOP_PREDICTED_PIE_MAX_CATEGORIES` and synthesizes `__other__`.

The function should finish after sorting:

```python
summary = summary.sort_values(
    ["compound_count", "display_label"],
    ascending=[False, True],
).reset_index(drop=True)
return summary
```

Do not change the classification pie cap used by product-use or reported-use charts.

- [ ] **Step 4: Verify highest-probability and true-absence behavior**

Add a test using three universe compounds:

```python
candidates = pd.DataFrame(
    [
        _functional_candidate("A", "crosslinker", 0.80),
        _functional_candidate("A", "fragrance", 0.70),
        _functional_candidate("B", "specialty_unmapped_use", 0.90),
    ]
)
universe = pd.DataFrame(
    [
        {"compound_key": "a", "compound": "A", "compound_label": "A"},
        {"compound_key": "b", "compound": "B", "compound_label": "B"},
        {"compound_key": "c", "compound": "C", "compound_label": "C"},
    ]
)
result = extract_top_predicted_functional_use_data(
    candidates,
    compound_universe=universe,
)
self.assertEqual(
    result.set_index("compound")["use_label"].to_dict(),
    {"A": "crosslinker", "B": "specialty_unmapped_use", "C": "Others"},
)
self.assertEqual(
    result.set_index("compound")["is_other"].to_dict(),
    {"A": False, "B": False, "C": True},
)
```

- [ ] **Step 5: Reject invalid predicted probabilities**

Add rows with missing, nonnumeric, negative, above-one, and infinite `probability` values while retaining a numeric `evidence_count`. Assert that these rows are not selected and their universe compounds become `Others`. Add a tied valid-probability case and assert that stable input order selects the first row.

- [ ] **Step 6: Run the complete chart test module**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_use_rose_plot -v
```

Expected: all use-chart tests pass, including the new thirteen-category regression.

---

### Task 3: Verify the real workbook vocabulary and export contracts

**Files:**
- Verify: `C:/Users/Administrator/Downloads/comptox_use_Results (1)/EPA_CompTox_Results.xlsx`
- Verify: `src/comptox_use.py`
- Verify: `src/use_rose_plot.py`

**Interfaces:**
- Consumes: the workbook's `Functional_Uses_Predicted` and `EPA_Predicted_Pie_Data` sheets.
- Produces: evidence that all twelve intended terms map correctly while `vinyl` remains unmapped and the predicted pie keeps raw English labels.

- [ ] **Step 1: Reclassify every previously unmapped predicted term**

Read the workbook's predicted-use sheet and run each English value through `classify_use_cn`. Verify:

```text
crosslinker -> 交联剂
heat_stabilizer -> 热稳定剂
emollient -> 润肤剂
hair_conditioner -> 护发剂
buffer -> 缓冲剂
photoinitiator -> 光引发剂
preservative -> 防腐剂
humectant -> 保湿剂
adhesion_promoter -> 附着力促进剂
wetting_agent -> 润湿剂
reducer -> 还原剂
emulsion_stabilizer -> 乳液稳定剂
vinyl -> unmapped
```

- [ ] **Step 2: Rebuild predicted pie data from workbook candidates**

Verify that every compound with a valid predicted row has its highest-probability raw English `use_label`, `is_other == False`, and that only compounds without valid predicted rows have `use_label == "Others"`.

- [ ] **Step 3: Render and inspect the predicted-use pie**

Generate PNG and PDF with the current renderer. Verify:

- all legend category stems are English EPA labels;
- no valid low-frequency label is renamed to `Others`;
- slice counts sum to the compound total;
- PNG starts with the PNG signature and PDF starts with `%PDF`.

- [ ] **Step 4: Run focused and full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_use_rose_plot tests.test_auto_query_workflow tests.test_auto_query_file_views -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall app.py pages src
git diff --check
```

Expected: zero test failures, successful compilation, and no whitespace errors.

- [ ] **Step 5: Inspect final scope**

Run:

```powershell
git status --short
git diff --stat
git diff -- src/comptox_use.py src/use_rose_plot.py tests/test_comptox_dashboard_mode.py tests/test_use_rose_plot.py
```

Confirm that unrelated pre-existing files and uncommitted changes are untouched. Do not stage, commit, or push.
