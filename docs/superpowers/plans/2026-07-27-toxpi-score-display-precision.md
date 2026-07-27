# ToxPi Score Display Precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display every user-visible ToxPi score with exactly four decimal places while preserving full-precision calculation, ranking, and export values.

**Architecture:** Add a small, pure shared display-policy module that owns the ToxPi score format and identifies ToxPi score columns. Plot generators and Streamlit page wrappers consume that policy only at rendering time; scientific DataFrames remain unchanged.

**Tech Stack:** Python 3.12, pandas, matplotlib, Streamlit 1.58, unittest

## Global Constraints

- ToxPi calculation and sorting must continue to use full floating-point precision.
- Existing two-stage PA/PBM/DF ranking, candidate/display limits, weights, and deterministic tie-breaking must not change.
- Excel and ZIP exports must retain numeric ToxPi values without rounding or string conversion.
- Only user-visible ToxPi score formatting changes; ranks and unrelated metrics keep their existing precision.
- Existing unrelated untracked files must remain untouched.

---

### Task 1: Shared four-decimal display policy and plot labels

**Files:**
- Create: `src/toxpi_display.py`
- Modify: `src/toxpi_calc.py`
- Modify: `tests/test_toxpi_plot_text.py`
- Create: `tests/test_toxpi_display.py`

**Interfaces:**
- Produces: `TOXPI_SCORE_FORMAT: str`, `format_toxpi_score(value) -> str`, and `toxpi_score_columns(frame: pd.DataFrame) -> tuple[str, ...]`.
- Consumes: pandas-compatible column names and numeric ToxPi score values.

- [ ] **Step 1: Write failing shared-policy and plot-label tests**

```python
# tests/test_toxpi_display.py
import unittest
import pandas as pd

from src.toxpi_display import format_toxpi_score, toxpi_score_columns


class ToxPiDisplayTests(unittest.TestCase):
    def test_formats_score_with_exactly_four_decimal_places(self):
        self.assertEqual(format_toxpi_score(0.42126), "0.4213")

    def test_identifies_only_toxpi_score_columns_in_source_order(self):
        frame = pd.DataFrame(
            columns=["compound", "initial_toxpi", "toxpi", "final_rank", "mean_rho"]
        )
        self.assertEqual(
            toxpi_score_columns(frame),
            ("initial_toxpi", "toxpi"),
        )
```

Update `tests/test_toxpi_plot_text.py` so the fixture uses `0.62126` and `0.31124`, then assert:

```python
self.assertIn("ToxPi: 0.6213", score_labels)
self.assertIn("ToxPi: 0.3112", score_labels)
self.assertIn("0.6213", bar_labels)
self.assertIn("0.3112", bar_labels)
```

Cover both `generate_multi_toxpi_plot` and `generate_r_style_toxpi_plot`, plus `generate_toxpi_bar_plot`.

- [ ] **Step 2: Run tests and verify the expected red state**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_toxpi_display tests.test_toxpi_plot_text -v
```

Expected: FAIL because `src.toxpi_display` does not exist and existing plot labels use two decimal places.

- [ ] **Step 3: Add the minimal shared display policy**

Create `src/toxpi_display.py`:

```python
from __future__ import annotations

import pandas as pd

TOXPI_SCORE_FORMAT = "%.4f"
TOXPI_SCORE_COLUMNS = frozenset({"initial_toxpi", "toxpi"})


def format_toxpi_score(value) -> str:
    return f"{float(value):.4f}"


def toxpi_score_columns(frame: pd.DataFrame) -> tuple[str, ...]:
    return tuple(
        column for column in frame.columns if column in TOXPI_SCORE_COLUMNS
    )
```

Import `format_toxpi_score` in `src/toxpi_calc.py` and replace only user-visible score labels:

```python
f"ToxPi: {format_toxpi_score(score)}"
f"ToxPi: {format_toxpi_score(float(score))}"
format_toxpi_score(height)
```

Keep the existing `ToxPi: NA` branch unchanged.

- [ ] **Step 4: Run focused tests and verify green**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_toxpi_display tests.test_toxpi_plot_text -v
```

Expected: all tests PASS with no new warnings.

- [ ] **Step 5: Commit the independently testable plot/display policy**

```powershell
git add src/toxpi_display.py src/toxpi_calc.py tests/test_toxpi_display.py tests/test_toxpi_plot_text.py
git commit -m "feat: show ToxPi plot scores to four decimals"
```

### Task 2: Four-decimal Streamlit table presentation

**Files:**
- Modify: `pages/0_综合筛查流程.py`
- Modify: `pages/2_ToxPi毒性评估.py`
- Modify: `pages/6_一键批量查询.py`
- Modify: `tests/test_toxpi_display.py`
- Modify: `tests/test_cp_screening_workflow.py`
- Modify: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: `TOXPI_SCORE_FORMAT` and `toxpi_score_columns(frame)` from `src.toxpi_display`.
- Produces: Streamlit `column_config` mappings whose ToxPi score columns use `st.column_config.NumberColumn(format="%.4f")`.

- [ ] **Step 1: Write failing table-format tests**

Extend `tests/test_toxpi_display.py`:

```python
from pathlib import Path


def test_all_toxpi_pages_use_shared_four_decimal_table_policy(self):
    for path in (
        Path("pages/0_综合筛查流程.py"),
        Path("pages/2_ToxPi毒性评估.py"),
        Path("pages/6_一键批量查询.py"),
    ):
        source = path.read_text(encoding="utf-8")
        self.assertIn("toxpi_dataframe_column_config", source)
```

Add a pure helper test using a recording factory:

```python
from src.toxpi_display import toxpi_dataframe_column_config


def test_builds_four_decimal_number_column_config_without_mutating_frame(self):
    frame = pd.DataFrame({"compound": ["A"], "toxpi": [0.42126]})
    original = frame.copy(deep=True)
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return kwargs

    config = toxpi_dataframe_column_config(frame, factory)

    self.assertEqual(config, {"toxpi": {"format": "%.4f"}})
    self.assertEqual(calls, [{"format": "%.4f"}])
    pd.testing.assert_frame_equal(frame, original)
```

Add source-contract assertions to the existing comprehensive and one-click page tests confirming their dataframe wrappers pass `column_config=toxpi_dataframe_column_config(...)`.

- [ ] **Step 2: Run tests and verify the expected red state**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_toxpi_display tests.test_cp_screening_workflow tests.test_auto_query_workflow -v
```

Expected: FAIL because the shared table-config helper and page integrations do not exist.

- [ ] **Step 3: Implement the pure table-config helper and page integrations**

Add to `src/toxpi_display.py`:

```python
def toxpi_dataframe_column_config(frame: pd.DataFrame, number_column_factory) -> dict:
    return {
        column: number_column_factory(format=TOXPI_SCORE_FORMAT)
        for column in toxpi_score_columns(frame)
    }
```

In `pages/0_综合筛查流程.py`, update `show_dataframe`:

```python
column_config = toxpi_dataframe_column_config(df, st.column_config.NumberColumn)
st.dataframe(df, width="stretch", column_config=column_config)
```

In `pages/6_一键批量查询.py`, update `_show_dataframe`:

```python
st.dataframe(
    frame,
    width="stretch",
    hide_index=True,
    column_config=toxpi_dataframe_column_config(
        frame,
        st.column_config.NumberColumn,
    ),
)
```

In `pages/2_ToxPi毒性评估.py`, route the ToxPi score table and the multi-seed summary table through a local wrapper using the same shared helper. Do not alter the underlying `final_agg` or `combined_summary` DataFrames.

- [ ] **Step 4: Run focused page and policy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_toxpi_display tests.test_cp_screening_workflow tests.test_auto_query_workflow -v
```

Expected: all focused tests PASS.

- [ ] **Step 5: Commit the independently testable table integration**

```powershell
git add src/toxpi_display.py "pages/0_综合筛查流程.py" "pages/2_ToxPi毒性评估.py" "pages/6_一键批量查询.py" tests/test_toxpi_display.py tests/test_cp_screening_workflow.py tests/test_auto_query_workflow.py
git commit -m "feat: format ToxPi tables to four decimals"
```

### Task 3: Full-precision and regression verification

**Files:**
- Modify only if a verification failure reveals an in-scope defect.

**Interfaces:**
- Consumes: all Task 1 and Task 2 behavior.
- Produces: fresh verification evidence that display formatting does not change calculation, rank order, or workbook values.

- [ ] **Step 1: Verify calculation and workbook values remain full precision**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow -v
```

Expected: the existing precise score-order assertions and workbook tests PASS.

- [ ] **Step 2: Verify all ToxPi-focused behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_toxpi_display tests.test_toxpi_plot_text tests.test_cp_screening_workflow -v
```

Expected: all tests PASS.

- [ ] **Step 3: Compile application sources**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0 and no syntax errors.

- [ ] **Step 4: Run the complete regression suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests PASS with zero failures and zero errors.

- [ ] **Step 5: Review the final scope**

Run:

```powershell
git status --short
git diff --stat HEAD~2..HEAD
git diff --check HEAD~2..HEAD
```

Expected: only the planned source/test files are included; unrelated untracked files remain untouched; no whitespace errors are reported.
