# Auto-Query Chart Refresh and CompTox Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure final per-file distribution charts match their exported tables and remove duplicate CompTox evidence returned by equivalent query variants.

**Architecture:** Gate derived chart discovery on module raw-table availability, preserve cumulative intermediate charts, and rebuild available charts during the final call to replace legacy stale bytes. Deduplicate CompTox candidates by chemical/evidence identity while retaining query-variant summaries and identity-conflict records.

**Tech Stack:** Python 3, pandas, matplotlib, unittest, Streamlit workflow exports.

## Global Constraints

- Preserve current `Others`, tie, no-result, and rare-category grouping semantics.
- Preserve public table names, chart names, PNG/PDF output, and per-file ZIP layout.
- Preserve independent name/SMILES/identifier resolution and conflict auditing.
- Do not stage or modify unrelated untracked files.

---

### Task 1: Make checkpoint charts data-dependent and refreshable

**Files:**
- Modify: `src/auto_query_workflow.py:1520-1599`
- Test: `tests/test_auto_query_file_views.py`
- Test: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: `AutoWorkflowResult.tables`, `AutoWorkflowResult.charts`, `completed_step`
- Produces: `available_chart_sources(result, completed_step=None)` without sources for modules whose raw result table is absent
- Produces: `update_auto_workflow_charts(result, completed_step=None)` that rebuilds available charts on the final call

- [ ] **Step 1: Write failing availability and stale-byte tests**

```python
def test_chart_update_waits_for_external_module_raw_results(self):
    result = example_result()
    result.tables.pop("CompTox_Candidates")
    charts, _ = update_auto_workflow_charts(result, completed_step="EPI Suite")
    for module_prefix in (
        "comptox_use__",
        "echa_reach_use__",
        "source_origin__",
    ):
        self.assertFalse(any(key.startswith(module_prefix) for key in charts))

def test_final_chart_update_replaces_stale_available_chart(self):
    result = example_result()
    key = "comptox_use__A__EPA_Product_Use_Category_Distribution"
    result.charts[key] = AutoWorkflowChart("stale", b"stale", b"stale")
    charts, _ = update_auto_workflow_charts(result)
    self.assertNotEqual(charts[key].png, b"stale")
    self.assertTrue(charts[key].png.startswith(b"\x89PNG"))
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_file_views -v
```

Expected: the first test finds prematurely generated EPA chart keys and the
second test retains `b"stale"`.

- [ ] **Step 3: Implement raw-table gating and final refresh**

Add a module-to-raw-table mapping in `src/auto_query_workflow.py`, skip a module
view when its raw table is absent, and change the existing-key guard to:

```python
refresh_existing = completed_step is None
if chart_key in charts and not refresh_existing:
    continue
```

- [ ] **Step 4: Run focused chart tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_file_views tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_epa_checkpoint_contains_available_per_file_charts -v
```

Expected: all selected tests pass.

### Task 2: Deduplicate equivalent CompTox evidence

**Files:**
- Modify: `src/comptox_use.py:305-462`
- Modify: `src/comptox_use.py:955-1050`
- Modify: `src/auto_query_workflow.py:925-982`
- Modify: `src/auto_query_file_views.py:313-355`
- Test: `tests/test_comptox_dashboard_mode.py`
- Test: `tests/test_auto_query_file_views.py`

**Interfaces:**
- Produces: `deduplicate_comptox_candidates(candidates_df) -> pd.DataFrame`
- Preserves: all summary query variants and `identity_conflict` warnings
- Changes: derived candidate evidence contains one row per compound, DTXSID,
  source type, and evidence signature

- [ ] **Step 1: Write failing same-DTXSID and projected-table tests**

```python
@patch("src.comptox_use.fetch_use_candidates")
@patch("src.comptox_use.resolve_dtxsid")
def test_same_dtxsid_query_variants_do_not_duplicate_evidence(
    self, resolve_dtxsid, fetch_use_candidates
):
    resolve_dtxsid.side_effect = [
        {"dtxsid": "DTXSID0000001", "status": "name", "message": ""},
        {"dtxsid": "DTXSID0000001", "status": "smiles", "message": ""},
    ]
    fetch_use_candidates.return_value = (
        [_candidate("product_category", raw_use="same evidence")],
        [],
    )
    summary, candidates, errors = comptox_use.run_comptox_use_batch(
        pd.DataFrame([{"compound": "Example", "smiles": "CCO"}]),
        delay_seconds=0,
    )
    self.assertEqual(len(summary), 2)
    self.assertEqual(len(candidates), 1)
    self.assertTrue(errors.empty)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode.CompToxDashboardModeTests.test_same_dtxsid_query_variants_do_not_duplicate_evidence -v
```

Expected: FAIL because two candidate rows are returned.

- [ ] **Step 3: Implement evidence-signature deduplication**

Add `deduplicate_comptox_candidates`, call it on each sequential batch candidate
frame and before derived auto-workflow/per-file tables, and call
`drop_duplicates()` on final public projections.

- [ ] **Step 4: Run CompTox and file-view regressions and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_auto_query_file_views tests.test_use_rose_plot -v
```

Expected: all selected tests pass, including the existing distinct-DTXSID
identity-conflict test.

### Task 3: Verify the complete workflow

**Files:**
- Verify: `src/auto_query_workflow.py`
- Verify: `src/auto_query_file_views.py`
- Verify: `src/comptox_use.py`
- Verify: `tests/test_auto_query_workflow.py`
- Verify: `tests/test_auto_query_file_views.py`
- Verify: `tests/test_comptox_dashboard_mode.py`

**Interfaces:**
- Consumes: supplied `comptox_use_Results.zip`
- Produces: chart/table count agreement for both per-file EPA workbooks

- [ ] **Step 1: Run all unit tests**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Compile application modules**

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0.

- [ ] **Step 3: Rebuild and inspect supplied EPA charts**

Load each `EPA_CompTox_Results.xlsx` from the supplied ZIP, regenerate the three
figures with the current shared renderers, and assert that legend compound
counts sum to the number of rows in each pie-data sheet.

- [ ] **Step 4: Review the final diff**

```powershell
git diff --check
git diff -- src/auto_query_workflow.py src/auto_query_file_views.py src/comptox_use.py tests/test_auto_query_workflow.py tests/test_auto_query_file_views.py tests/test_comptox_dashboard_mode.py
```

Expected: no whitespace errors and only the approved repair scope.
