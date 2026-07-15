# Reported Summary and Chart Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every exported chart use Times New Roman, remove the DBE grey background, replace EPA/ECHA reported per-compound charts with total-preserving summary pies while retaining evidence dots, split predicted/reported Excel sheets, and add a total-preserving source-origin pie across standalone and one-click workflows.

**Architecture:** Put font policy in one plotting-style module and put compound-universe, one-row-per-compound classification, and donut rendering in `src/use_rose_plot.py`. Standalone workbook builders and `src/auto_query_workflow.py` consume those shared tables; pages render only shared outputs and never reimplement classification rules.

**Tech Stack:** Python 3.12, pandas, Matplotlib, plotnine, openpyxl, Streamlit, unittest.

## Global Constraints

- Every program-generated chart must use `Times New Roman`; unavailable fonts must produce an explicit warning and must not silently change the configured family.
- DBE Figure and Axes backgrounds must be white and all DBE grid lines must be disabled.
- EPA predicted keeps its existing highest-probability semantics and donut form; only missing compounds are added to `Others`.
- EPA/ECHA reported keep evidence-dot plots and add one total summary donut each; the ECHA per-compound rose plot is removed.
- Reported `Others` means only no reported result or a tie for the most frequently reported category; small valid categories must not be merged into it.
- Donut label thresholds are: `>= 5%` inside, `>= 1% and < 5%` outside with collision-avoiding leader lines, `< 1%` legend only.
- `Anthropogenic`, `Natural`, `Both`, and `Unknown` are the only source-origin pie categories.
- Each donut category sum must equal the deduplicated valid input compound count.
- Predicted and reported records must never share a user-visible Excel sheet.
- Standalone pages and “一键批量查询” must consume the same classification tables and chart functions.
- Preserve unrelated untracked files already present in the worktree; stage only files named in the current task.

---

## File Structure

- Create `src/plot_style.py`: Times New Roman availability, Matplotlib defaults, figure text enforcement, and warning text.
- Create `tests/test_plot_style.py`: centralized font policy tests.
- Modify `src/r_screening_replica/plots.py`: use shared font policy and remove DBE grey grid/background.
- Modify `src/toxpi_calc.py`: replace local sans-serif settings and apply the shared family to returned figures.
- Modify `src/cp_screening_workflow.py`: apply the shared family to PBM/ToxPi ranking figures.
- Modify `pages/2_ToxPi毒性评估.py`: surface the explicit missing-font warning on the standalone ToxPi page.
- Modify `src/use_rose_plot.py`: compound universe, predicted/reported/source classification, total-preserving donut renderer, tiered labels, generalized evidence dots.
- Modify `src/comptox_use.py`: remove mixed candidate workbook output and add predicted/reported pie-data sheets.
- Modify `src/echa_use.py`: export reported-only candidates and ECHA pie-data sheets.
- Modify `src/source_origin.py`: export source-origin pie data.
- Modify `src/auto_query_workflow.py`: build split tables, keep mixed candidates internal, create the new chart set, and update modular ZIP contents.
- Modify `pages/4_化合物用途查询.py`: render EPA predicted, EPA/ECHA reported pies and dots, and source-origin pie through shared functions.
- Modify `pages/6_一键批量查询.py`: expose split tables and source-origin charts in the result dashboard.
- Modify `tests/test_r_screening_replica.py`, `tests/test_toxpi_plot_text.py`, `tests/test_cp_screening_workflow.py`, `tests/test_use_rose_plot.py`, `tests/test_comptox_dashboard_mode.py`, `tests/test_echa_use.py`, `tests/test_source_origin.py`, `tests/test_auto_query_workflow.py`, and `tests/test_chemspider_one_time_key.py`: regression and contract coverage.

---

### Task 1: Centralize Times New Roman Policy

**Files:**
- Create: `src/plot_style.py`
- Create: `tests/test_plot_style.py`

**Interfaces:**
- Produces: `PLOT_FONT_FAMILY: str`, `PLOT_FONT_WARNING: str`, `font_available(name: str) -> bool`, `configure_plot_style() -> list[str]`, and `apply_figure_font(fig) -> object`.
- Consumes: Matplotlib `rcParams`, `font_manager`, and `matplotlib.text.Text`.

- [ ] **Step 1: Write failing centralized-font tests**

Create `tests/test_plot_style.py` with these tests:

```python
import unittest
from unittest.mock import patch

import matplotlib
import matplotlib.pyplot as plt

from src.plot_style import (
    PLOT_FONT_FAMILY,
    PLOT_FONT_WARNING,
    apply_figure_font,
    configure_plot_style,
)


class PlotStyleTests(unittest.TestCase):
    def test_configure_plot_style_sets_times_new_roman(self):
        configure_plot_style()
        self.assertEqual(matplotlib.rcParams["font.family"][0], "Times New Roman")
        self.assertEqual(matplotlib.rcParams["font.serif"][0], "Times New Roman")
        self.assertEqual(matplotlib.rcParams["pdf.fonttype"], 42)
        self.assertFalse(matplotlib.rcParams["axes.unicode_minus"])

    def test_missing_font_returns_explicit_warning(self):
        with patch("src.plot_style.font_available", return_value=False):
            self.assertEqual(configure_plot_style(), [PLOT_FONT_WARNING])

    def test_apply_figure_font_updates_every_text_artist(self):
        fig, ax = plt.subplots()
        ax.set_title("Title")
        ax.set_xlabel("X")
        ax.text(0.5, 0.5, "Body")
        apply_figure_font(fig)
        families = {
            text.get_fontfamily()[0]
            for text in fig.findobj(matplotlib.text.Text)
            if text.get_text()
        }
        self.assertEqual(families, {PLOT_FONT_FAMILY})
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_plot_style -v
```

Expected: import failure for `src.plot_style`.

- [ ] **Step 3: Implement the centralized style module**

Create `src/plot_style.py`:

```python
from __future__ import annotations

import matplotlib
import matplotlib.font_manager as font_manager
from matplotlib.text import Text


PLOT_FONT_FAMILY = "Times New Roman"
PLOT_FONT_WARNING = (
    "Times New Roman is not available. Install the font on the runtime host "
    "or provide a licensed font file before exporting publication figures."
)


def font_available(name: str) -> bool:
    return any(font.name == name for font in font_manager.fontManager.ttflist)


def configure_plot_style() -> list[str]:
    matplotlib.rcParams.update(
        {
            "font.family": [PLOT_FONT_FAMILY],
            "font.serif": [PLOT_FONT_FAMILY],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    return [] if font_available(PLOT_FONT_FAMILY) else [PLOT_FONT_WARNING]


def apply_figure_font(fig):
    for text in fig.findobj(Text):
        text.set_fontfamily(PLOT_FONT_FAMILY)
    return fig
```

- [ ] **Step 4: Run the centralized-font tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_plot_style -v
```

Expected: three tests pass and the command exits with code 0.

- [ ] **Step 5: Commit the centralized policy**

```powershell
git add src/plot_style.py tests/test_plot_style.py
git commit -m "feat: centralize plot font policy"
```

---

### Task 2: Apply the Font Policy and Remove the DBE Grey Background

**Files:**
- Modify: `src/r_screening_replica/plots.py:10-32,71-222,291-395`
- Modify: `src/toxpi_calc.py:5-15,300-628`
- Modify: `src/cp_screening_workflow.py:7,457-481`
- Modify: `pages/2_ToxPi毒性评估.py:1-20`
- Modify: `tests/test_r_screening_replica.py`
- Modify: `tests/test_toxpi_plot_text.py`
- Modify: `tests/test_cp_screening_workflow.py`

**Interfaces:**
- Consumes: `configure_plot_style()`, `apply_figure_font()`, and `PLOT_FONT_FAMILY` from Task 1.
- Produces: white/gridless DBE figures and Times New Roman text in local screening, ToxPi, sensitivity, and PBM/ToxPi ranking figures.

- [ ] **Step 1: Add failing DBE and figure-font regressions**

Add a direct DBE rendering test to `tests/test_r_screening_replica.py`:

```python
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt

from src.r_screening_replica.plots import _draw_compound_bubble


def test_dbe_bubble_has_white_background_and_no_grid(self):
    data = pd.DataFrame(
        {
            "Category": ["CH"],
            "carbon_count": [12],
            "DBE": [5.0],
            "area_level": pd.Categorical(["Level 2"]),
        }
    )
    fig, ax = plt.subplots()
    _draw_compound_bubble(ax, data)
    self.assertEqual(ax.get_facecolor(), mcolors.to_rgba("white"))
    self.assertEqual(fig.get_facecolor(), mcolors.to_rgba("white"))
    self.assertFalse(any(line.get_visible() for line in ax.get_xgridlines()))
    self.assertFalse(any(line.get_visible() for line in ax.get_ygridlines()))
    plt.close(fig)
```

Extend the shared assertion in `tests/test_toxpi_plot_text.py` and add the same assertion to the PBM/ToxPi figure test in `tests/test_cp_screening_workflow.py`:

```python
def assert_times_new_roman(self, figure):
    texts = [
        text
        for text in figure.findobj(matplotlib.text.Text)
        if text.get_text().strip()
    ]
    self.assertTrue(texts)
    self.assertTrue(
        all(text.get_fontfamily()[0] == "Times New Roman" for text in texts)
    )
```

Add a page-source assertion in `tests/test_toxpi_plot_text.py`:

```python
from pathlib import Path

page_source = Path("pages/2_ToxPi毒性评估.py").read_text(encoding="utf-8")
self.assertIn("configure_plot_style", page_source)
self.assertIn("st.warning", page_source)
```

- [ ] **Step 2: Run focused tests and verify the new assertions fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_r_screening_replica tests.test_toxpi_plot_text tests.test_cp_screening_workflow -v
```

Expected: DBE grid and DejaVu/Arial font assertions fail.

- [ ] **Step 3: Apply the shared policy in local-screening plots**

In `src/r_screening_replica/plots.py`, import and configure the policy once:

```python
from src.plot_style import (
    PLOT_FONT_FAMILY,
    apply_figure_font,
    configure_plot_style,
)

PLOT_STYLE_WARNINGS = configure_plot_style()
```

Replace `_font_warnings()` with:

```python
def _font_warnings() -> list[str]:
    return list(PLOT_STYLE_WARNINGS)
```

Before every Matplotlib `savefig`, enforce the family:

```python
apply_figure_font(fig)
fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
```

In the plotnine boxplot theme, set the family explicitly:

```python
theme(
    text=element_text(family=PLOT_FONT_FAMILY),
    axis_text_x=element_text(rotation=45, ha="right", family=PLOT_FONT_FAMILY),
)
```

Replace the DBE grid block in `_draw_compound_bubble` with:

```python
ax.figure.patch.set_facecolor("white")
ax.set_facecolor("white")
ax.grid(False)
```

- [ ] **Step 4: Apply the shared policy to ToxPi and CP figures**

In `src/toxpi_calc.py`, remove the local `font.sans-serif` block and add:

```python
from src.plot_style import apply_figure_font, configure_plot_style

configure_plot_style()
```

Immediately before each public generator returns its figure, use:

```python
return apply_figure_font(fig)
```

Apply this to the sensitivity histogram and to `generate_multi_toxpi_plot`, `generate_r_style_toxpi_plot`, and `generate_toxpi_bar_plot`.

In `src/cp_screening_workflow.py`, import the same functions, call `configure_plot_style()` at module load, and end `generate_pbm_toxpi_bar_plot` with:

```python
fig.tight_layout()
return apply_figure_font(fig)
```

In `pages/2_ToxPi毒性评估.py`, import `configure_plot_style` and display every returned warning once near the page heading:

```python
from src.plot_style import configure_plot_style

for plot_warning in configure_plot_style():
    st.warning(plot_warning)
```

- [ ] **Step 5: Run the focused chart tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_plot_style tests.test_r_screening_replica tests.test_toxpi_plot_text tests.test_cp_screening_workflow -v
```

Expected: all tests pass; DBE has no visible grid lines and every populated text artist reports Times New Roman.

- [ ] **Step 6: Commit local chart consistency**

```powershell
git add src/r_screening_replica/plots.py src/toxpi_calc.py src/cp_screening_workflow.py pages/2_ToxPi毒性评估.py tests/test_r_screening_replica.py tests/test_toxpi_plot_text.py tests/test_cp_screening_workflow.py
git commit -m "fix: standardize chart fonts and DBE background"
```

---

### Task 3: Build Total-Preserving Classification and Donut Functions

**Files:**
- Modify: `src/use_rose_plot.py:15-63,194-301,559-677,751-811,863-978`
- Modify: `tests/test_use_rose_plot.py`

**Interfaces:**
- Produces: `build_compound_universe(input_df) -> pd.DataFrame`.
- Produces: `extract_top_predicted_functional_use_data(candidates_df, source_label="EPA FC", compound_universe=None) -> pd.DataFrame`.
- Produces: `extract_top_reported_functional_use_data(candidates_df, compound_universe, source_label, source_type, use_key, require_reported_flag) -> pd.DataFrame`.
- Produces: `extract_source_origin_pie_data(summary_df, compound_universe) -> pd.DataFrame`.
- Produces: `generate_compound_classification_pie_plot(plot_df, title, footnote=None, max_categories=None, fixed_categories=None)`.
- Produces: `generate_reported_functional_use_pie_plot(plot_df, title)` and a generalized reported evidence extractor.
- Consumes: `apply_figure_font()` and `configure_plot_style()` from Task 1.

- [ ] **Step 1: Add failing universe and classification tests**

Extend imports in `tests/test_use_rose_plot.py` and add tests with an explicit three-compound universe:

```python
from src.use_rose_plot import (
    build_compound_universe,
    extract_source_origin_pie_data,
    extract_top_reported_functional_use_data,
    generate_reported_functional_use_pie_plot,
)


def test_reported_classification_uses_unique_top_tie_and_missing(self):
    universe = build_compound_universe(
        pd.DataFrame({"compound": ["A", "B", "C", "A"]})
    )
    candidates = pd.DataFrame(
        [
            {"compound": "A", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Solvent", "evidence_count": 3},
            {"compound": "A", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Catalyst", "evidence_count": 1},
            {"compound": "B", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Solvent", "evidence_count": 2},
            {"compound": "B", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Catalyst", "evidence_count": 2},
        ]
    )
    result = extract_top_reported_functional_use_data(
        candidates,
        universe,
        source_label="EPA FC reported",
        source_type="functional_use",
        use_key="raw",
        require_reported_flag=True,
    ).set_index("compound")
    self.assertEqual(result.loc["A", "display_label"], "Solvent")
    self.assertEqual(result.loc["A", "classification_reason"], "unique_top_reported_category")
    self.assertEqual(result.loc["B", "display_label"], "Others")
    self.assertEqual(result.loc["B", "classification_reason"], "tie_for_top_reported_category")
    self.assertEqual(result.loc["C", "display_label"], "Others")
    self.assertEqual(result.loc["C", "classification_reason"], "no_reported_result")
    self.assertEqual(len(result), 3)
```

Add equivalent tests for ECHA with `source_type=None`, `use_key="category"`, and `require_reported_flag=False`; add a predicted test proving a missing universe member becomes `Others`; add a source-origin test covering all four categories.

Use these concrete cases:

```python
def test_echa_reported_uses_the_same_unique_top_rule(self):
    universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B"]}))
    candidates = pd.DataFrame(
        [
            {"compound": "A", "use_en": "Industrial use", "use_cn": "Industrial use", "evidence_count": 2},
            {"compound": "A", "use_en": "Consumer use", "use_cn": "Consumer use", "evidence_count": 1},
            {"compound": "B", "use_en": "Industrial use", "use_cn": "Industrial use", "evidence_count": 1},
            {"compound": "B", "use_en": "Consumer use", "use_cn": "Consumer use", "evidence_count": 1},
        ]
    )
    result = extract_top_reported_functional_use_data(
        candidates,
        universe,
        source_label="ECHA reported",
        source_type=None,
        use_key="category",
        require_reported_flag=False,
    ).set_index("compound")
    self.assertEqual(result.loc["A", "display_label"], "Industrial use")
    self.assertEqual(result.loc["B", "display_label"], "Others")

def test_predicted_fills_missing_universe_compound_as_others(self):
    universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B"]}))
    candidates = pd.DataFrame(
        [
            {
                "compound": "A",
                "source_type": "functional_use",
                "functional_use_source": "predicted",
                "raw_use": "Solvent",
                "probability": 0.91,
            }
        ]
    )
    result = extract_top_predicted_functional_use_data(
        candidates, compound_universe=universe
    ).set_index("compound")
    self.assertEqual(result.loc["A", "display_label"], "Solvent")
    self.assertEqual(result.loc["B", "display_label"], "Others")
    self.assertEqual(result.loc["B", "classification_reason"], "no_predicted_result")
    self.assertEqual(len(result), 2)

def test_source_origin_maps_all_four_fixed_categories(self):
    universe = build_compound_universe(
        pd.DataFrame({"compound": ["Both", "Human", "Natural", "None"]})
    )
    summary = pd.DataFrame(
        [
            {"compound": "Both", "人为源证据数": 2, "天然源证据数": 1},
            {"compound": "Human", "人为源证据数": 1, "天然源证据数": 0},
            {"compound": "Natural", "人为源证据数": 0, "天然源证据数": 3},
        ]
    )
    result = extract_source_origin_pie_data(summary, universe)
    self.assertEqual(
        result.set_index("compound")["display_label"].to_dict(),
        {
            "Both": "Both",
            "Human": "Anthropogenic",
            "Natural": "Natural",
            "None": "Unknown",
        },
    )
```

- [ ] **Step 2: Add failing tiered-label and footnote tests**

Create a 1,000-compound classification frame with category counts 950, 40, 9, and 1. Assert:

```python
figure = generate_reported_functional_use_pie_plot(plot_df, "Reported")
axis_text = {text.get_text() for text in figure.axes[0].texts}
figure_text = {text.get_text() for text in figure.texts}
annotations = [
    item for item in figure.axes[0].texts
    if isinstance(item, matplotlib.text.Annotation)
]
self.assertIn("95.0%", axis_text)
self.assertIn("4.0%", {item.get_text() for item in annotations})
self.assertNotIn("0.9%", axis_text)
self.assertNotIn("0.1%", axis_text)
self.assertIn(
    "Others includes compounds with no reported result or with a tie for the most frequently reported category.",
    figure_text,
)
self.assertTrue(
    all(
        text.get_fontfamily()[0] == "Times New Roman"
        for text in figure.findobj(matplotlib.text.Text)
        if text.get_text().strip()
    )
)
```

Also assert that legend labels include the 0.9% and 0.1% categories and that reported summaries never collapse valid rare categories into `Others`.

- [ ] **Step 3: Run the focused use-plot suite and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_use_rose_plot -v
```

Expected: missing functions/signatures and total-preservation assertions fail.

- [ ] **Step 4: Add the compound universe and shared classification columns**

At the top of `src/use_rose_plot.py`, replace local font defaults with the Task 1 policy and define:

```python
from src.plot_style import apply_figure_font, configure_plot_style

configure_plot_style()

COMPOUND_UNIVERSE_COLUMNS = ["compound_key", "compound", "compound_label"]
COMPOUND_CLASSIFICATION_COLUMNS = [
    "source",
    "compound_key",
    "compound",
    "compound_label",
    "use_cn",
    "use_label",
    "display_label",
    "evidence_count",
    "classification_reason",
    "is_other",
]
REPORTED_OTHERS_NOTE = (
    "Others includes compounds with no reported result or with a tie for the "
    "most frequently reported category."
)
PIE_INSIDE_LABEL_MIN_PERCENT = 5.0
PIE_OUTSIDE_LABEL_MIN_PERCENT = 1.0
```

Implement the universe with stable identifier fallback and deduplication:

```python
def build_compound_universe(input_df):
    if input_df is None or not isinstance(input_df, pd.DataFrame) or input_df.empty:
        return pd.DataFrame(columns=COMPOUND_UNIVERSE_COLUMNS)
    rows = []
    seen = set()
    for position, (_, row) in enumerate(input_df.iterrows(), start=1):
        compound = _first_clean(
            row.get("compound"), row.get("Name"), row.get("name"), row.get("cas"),
            row.get("ec"), row.get("dtxsid"), row.get("echa_id"), row.get("smiles"),
        )
        if not compound:
            continue
        compound_key = _normalize_label_key(compound)
        if not compound_key or compound_key in seen:
            continue
        seen.add(compound_key)
        rows.append(
            {
                "compound_key": compound_key,
                "compound": compound,
                "compound_label": _ascii_label(compound, f"Compound {position}"),
            }
        )
    return pd.DataFrame(rows, columns=COMPOUND_UNIVERSE_COLUMNS)
```

- [ ] **Step 5: Implement reported and source-origin one-row-per-compound classification**

Implement `extract_top_reported_functional_use_data` so it filters the requested evidence stream, sums evidence within normalized category keys, detects strict maxima, and left-fills the universe:

```python
def extract_top_reported_functional_use_data(
    candidates_df,
    compound_universe,
    source_label,
    source_type=None,
    use_key="raw",
    require_reported_flag=True,
):
    universe = compound_universe.copy()
    if universe.empty:
        return pd.DataFrame(columns=COMPOUND_CLASSIFICATION_COLUMNS)
    candidates = candidates_df.copy() if isinstance(candidates_df, pd.DataFrame) else pd.DataFrame()
    if source_type is not None and "source_type" in candidates.columns:
        candidates = candidates[candidates["source_type"].eq(source_type)].copy()
    if require_reported_flag and not candidates.empty:
        candidates = candidates[
            candidates.apply(lambda row: _functional_source_bucket(row) == "reported", axis=1)
        ].copy()
    groups = {}
    for _, candidate in candidates.iterrows():
        compound_key = _normalize_label_key(candidate.get("compound"))
        if not compound_key:
            continue
        groups.setdefault(compound_key, []).append(candidate)

    rows = []
    for _, compound_row in universe.iterrows():
        totals = {}
        labels = {}
        use_cn_values = {}
        for candidate in groups.get(compound_row["compound_key"], []):
            use_value, english_value = _candidate_use_values(candidate, use_key)
            category_key = _normalize_label_key(english_value or use_value)
            if not category_key:
                continue
            weight = _to_number(candidate.get("evidence_count"))
            if pd.isna(weight) or float(weight) <= 0:
                weight = 1.0
            totals[category_key] = totals.get(category_key, 0.0) + float(weight)
            labels[category_key] = _ascii_label(english_value or use_value, "Reported use")
            use_cn_values[category_key] = _first_clean(candidate.get("use_cn"), use_value)

        highest = 0.0
        if not totals:
            winner = None
            reason = "no_reported_result"
        else:
            highest = max(totals.values())
            winners = [key for key, value in totals.items() if value == highest]
            winner = winners[0] if len(winners) == 1 else None
            reason = "unique_top_reported_category" if winner else "tie_for_top_reported_category"
        is_other = winner is None
        rows.append(
            {
                "source": source_label,
                **compound_row.to_dict(),
                "use_cn": "Others" if is_other else use_cn_values[winner],
                "use_label": "Others" if is_other else labels[winner],
                "display_label": "Others" if is_other else labels[winner],
                "evidence_count": highest,
                "classification_reason": reason,
                "is_other": is_other,
            }
        )
    return pd.DataFrame(rows, columns=COMPOUND_CLASSIFICATION_COLUMNS)
```

Implement `extract_source_origin_pie_data` by matching source summary rows to the universe and using numeric human/natural evidence counts. Return `Both`, `Anthropogenic`, `Natural`, or `Unknown` with the corresponding stable reason.

Use this implementation so missing summary rows remain in the universe as `Unknown`:

```python
def extract_source_origin_pie_data(summary_df, compound_universe):
    universe = compound_universe.copy()
    if universe.empty:
        return pd.DataFrame(columns=COMPOUND_CLASSIFICATION_COLUMNS)
    summary = summary_df.copy() if isinstance(summary_df, pd.DataFrame) else pd.DataFrame()
    summary_by_key = {
        _normalize_label_key(row.get("compound")): row
        for _, row in summary.iterrows()
        if _normalize_label_key(row.get("compound"))
    }
    rows = []
    for _, compound_row in universe.iterrows():
        source_row = summary_by_key.get(compound_row["compound_key"])
        human = _to_number(source_row.get("人为源证据数")) if source_row is not None else 0
        natural = _to_number(source_row.get("天然源证据数")) if source_row is not None else 0
        human = 0 if pd.isna(human) else float(human)
        natural = 0 if pd.isna(natural) else float(natural)
        if human > 0 and natural > 0:
            category, reason = "Both", "both_source_types"
        elif human > 0:
            category, reason = "Anthropogenic", "anthropogenic_only"
        elif natural > 0:
            category, reason = "Natural", "natural_only"
        else:
            category, reason = "Unknown", "insufficient_source_evidence"
        rows.append(
            {
                "source": "Source origin",
                **compound_row.to_dict(),
                "use_cn": category,
                "use_label": category,
                "display_label": category,
                "evidence_count": human + natural,
                "classification_reason": reason,
                "is_other": category == "Unknown",
            }
        )
    return pd.DataFrame(rows, columns=COMPOUND_CLASSIFICATION_COLUMNS)
```

- [ ] **Step 6: Preserve predicted semantics while filling missing compounds**

Change the existing signature to:

```python
def extract_top_predicted_functional_use_data(
    candidates_df,
    source_label="EPA FC",
    compound_universe=None,
):
```

Keep the existing highest-probability selection code, then, when `compound_universe` is provided, left-join selected results onto it and fill missing rows with:

```python
{
    "use_cn": "Others",
    "use_label": "Others",
    "display_label": "Others",
    "probability": pd.NA,
    "status": "no_predicted_result",
}
```

Add `compound_key`, `classification_reason`, and `is_other` to `TOP_PREDICTED_FUNCTIONAL_COLUMNS`. Existing selected rows receive the normalized compound key, `classification_reason="top_predicted_probability"`, and `is_other=False`; missing rows receive `classification_reason="no_predicted_result"` and `is_other=True`. Update the existing empty-frame column test accordingly. Do not alter probability ranking or the existing reported-match `status` for compounds that have predicted results.

- [ ] **Step 7: Generalize evidence dots for ECHA**

Change `extract_reported_functional_use_presence_data` to accept:

```python
def extract_reported_functional_use_presence_data(
    candidates_df,
    source_label="EPA FC",
    source_type="functional_use",
    use_key="raw",
    require_reported_flag=True,
):
```

For EPA, retain current filtering. For ECHA, pass `source_type=None`, `use_key="category"`, and `require_reported_flag=False`. Continue deduplicating the same normalized use once per compound; evidence dots do not add empty-compound rows.

- [ ] **Step 8: Implement the shared donut and tiered label placement**

Implement collision-avoiding external labels with a helper that separates left/right annotations, sorts by desired Y, and enforces a minimum gap:

```python
def _spread_external_labels(items, minimum_gap=0.12, lower=-0.92, upper=0.92):
    items = sorted(items, key=lambda item: item["desired_y"])
    previous = lower - minimum_gap
    for item in items:
        item["label_y"] = max(item["desired_y"], previous + minimum_gap)
        previous = item["label_y"]
    overflow = previous - upper
    if overflow > 0:
        for item in items:
            item["label_y"] -= overflow
    for index in range(len(items) - 2, -1, -1):
        allowed = items[index + 1]["label_y"] - minimum_gap
        items[index]["label_y"] = min(items[index]["label_y"], allowed)
    return items
```

In `generate_compound_classification_pie_plot`, aggregate unique compounds by `display_label`, optionally preserve a fixed category order, call `ax.pie` without `autopct`, and place labels as follows:

```python
for wedge, percent in zip(wedges, summary["percent"]):
    angle = math.radians((wedge.theta1 + wedge.theta2) / 2)
    if percent >= PIE_INSIDE_LABEL_MIN_PERCENT:
        ax.text(0.78 * math.cos(angle), 0.78 * math.sin(angle), f"{percent:.1f}%", ha="center", va="center")
    elif percent >= PIE_OUTSIDE_LABEL_MIN_PERCENT:
        side = 1 if math.cos(angle) >= 0 else -1
        external[side].append(
            {"angle": angle, "percent": percent, "desired_y": math.sin(angle)}
        )
```

After `_spread_external_labels`, create annotations with `arrowprops={"arrowstyle": "-", "color": "#555555", "linewidth": 0.8}`. Percentages below 1% receive no axis text but remain in the legend. Finish every returned figure with `apply_figure_font(fig)`.

Use a complete shared renderer shaped as follows:

```python
def generate_compound_classification_pie_plot(
    plot_df,
    title,
    footnote=None,
    max_categories=None,
    fixed_categories=None,
):
    if plot_df is None or plot_df.empty:
        raise ValueError("No compound classification data is available.")
    data = plot_df.copy()
    data["_compound_key"] = data["compound_key"].map(_normalize_label_key)
    data["_display_label"] = data["display_label"].map(
        lambda value: _ascii_label(value, "Others")
    )
    summary = (
        data.groupby("_display_label", sort=False)["_compound_key"]
        .nunique()
        .rename("compound_count")
        .reset_index()
        .rename(columns={"_display_label": "display_label"})
    )
    if fixed_categories:
        order = {label: index for index, label in enumerate(fixed_categories)}
        summary["_order"] = summary["display_label"].map(order).fillna(len(order))
        summary = summary.sort_values(["_order", "display_label"]).drop(columns="_order")
    else:
        summary = summary.sort_values(
            ["compound_count", "display_label"], ascending=[False, True]
        )
    if max_categories is not None and len(summary) > max_categories:
        kept = summary.head(max_categories - 1).copy()
        remainder_count = int(summary.iloc[max_categories - 1 :]["compound_count"].sum())
        summary = pd.concat(
            [
                kept,
                pd.DataFrame(
                    [{"display_label": "Others", "compound_count": remainder_count}]
                ),
            ],
            ignore_index=True,
        )
        summary = (
            summary.groupby("display_label", sort=False)["compound_count"]
            .sum()
            .reset_index()
        )
    total_count = int(summary["compound_count"].sum())
    summary["percent"] = summary["compound_count"] / total_count * 100
    color_map = _build_use_color_map(summary["display_label"].tolist())
    colors = [color_map[label] for label in summary["display_label"]]

    fig, ax = plt.subplots(figsize=(8.8, 6.4), facecolor="white")
    fig.subplots_adjust(left=0.06, right=0.72, top=0.86, bottom=0.12)
    wedges, _ = ax.pie(
        summary["compound_count"],
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 1.2},
    )
    external = {-1: [], 1: []}
    for wedge, percent in zip(wedges, summary["percent"]):
        angle = math.radians((wedge.theta1 + wedge.theta2) / 2)
        if percent >= PIE_INSIDE_LABEL_MIN_PERCENT:
            ax.text(
                0.78 * math.cos(angle),
                0.78 * math.sin(angle),
                f"{percent:.1f}%",
                ha="center",
                va="center",
                fontsize=9,
                fontweight="bold",
            )
        elif percent >= PIE_OUTSIDE_LABEL_MIN_PERCENT:
            side = 1 if math.cos(angle) >= 0 else -1
            external[side].append(
                {"angle": angle, "percent": percent, "desired_y": math.sin(angle)}
            )
    for side, items in external.items():
        for item in _spread_external_labels(items):
            ax.annotate(
                f"{item['percent']:.1f}%",
                xy=(0.98 * math.cos(item["angle"]), 0.98 * math.sin(item["angle"])),
                xytext=(1.22 * side, item["label_y"]),
                ha="left" if side > 0 else "right",
                va="center",
                fontsize=8.5,
                arrowprops={"arrowstyle": "-", "color": "#555555", "linewidth": 0.8},
            )
    ax.text(0, 0, f"Total compounds\n{total_count}", ha="center", va="center", fontsize=11, fontweight="bold")
    ax.set_title(_ascii_label(title, "Compound Distribution"), fontsize=14, fontweight="bold", pad=18)
    ax.set_aspect("equal")
    handles = [
        Patch(
            facecolor=color,
            edgecolor="white",
            label=f"{row.display_label} ({int(row.compound_count)}, {row.percent:.1f}%)",
        )
        for color, row in zip(colors, summary.itertuples(index=False))
    ]
    fig.legend(handles=handles, loc="center right", bbox_to_anchor=(0.99, 0.52), frameon=False, title="Category")
    if footnote:
        fig.text(0.99, 0.02, footnote, ha="right", va="bottom", fontsize=8.5, color="#333333")
    return apply_figure_font(fig)
```

`generate_reported_functional_use_pie_plot` calls the shared renderer with `REPORTED_OTHERS_NOTE` and `max_categories=None`. The existing predicted wrapper uses the shared renderer but preserves its existing category cap and title/legend semantics.

- [ ] **Step 9: Run use-plot tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_use_rose_plot -v
```

Expected: all existing and new use-plot tests pass; the total-preservation, tie, missing-result, threshold, footnote, and font assertions are green.

- [ ] **Step 10: Commit shared classification and donut rendering**

```powershell
git add src/use_rose_plot.py tests/test_use_rose_plot.py
git commit -m "feat: add total-preserving use summary pies"
```

---

### Task 4: Split Standalone Workbooks and Add Pie Audit Sheets

**Files:**
- Modify: `src/comptox_use.py:716-758`
- Modify: `src/echa_use.py:718-741`
- Modify: `src/source_origin.py:306-325`
- Modify: `tests/test_comptox_dashboard_mode.py:520-570`
- Modify: `tests/test_echa_use.py:143-178`
- Modify: `tests/test_source_origin.py:195-217`

**Interfaces:**
- Consumes: compound-universe and classification functions from Task 3.
- Produces: separate predicted/reported user sheets and per-compound pie-data sheets in every standalone workbook.

- [ ] **Step 1: Add failing EPA workbook structure assertions**

Extend the workbook test in `tests/test_comptox_dashboard_mode.py`:

```python
book = load_workbook(io.BytesIO(workbook.getvalue()), read_only=True)
self.assertIn("Functional_Uses_Predicted", book.sheetnames)
self.assertIn("Functional_Uses_Reported", book.sheetnames)
self.assertIn("EPA_Predicted_Pie_Data", book.sheetnames)
self.assertIn("EPA_Reported_Pie_Data", book.sheetnames)
self.assertNotIn("All_Use_Candidates", book.sheetnames)

predicted = pd.read_excel(io.BytesIO(workbook.getvalue()), sheet_name="Functional_Uses_Predicted")
reported = pd.read_excel(io.BytesIO(workbook.getvalue()), sheet_name="Functional_Uses_Reported")
self.assertEqual(set(predicted["来源类型"].dropna().str.lower()), {"predicted"})
self.assertEqual(set(reported["来源类型"].dropna().str.lower()), {"reported"})
```

Use a three-compound input with one missing predicted/reported result and assert both pie-data sheets have three rows.

- [ ] **Step 2: Add failing ECHA and source-origin workbook assertions**

In `tests/test_echa_use.py`, require `ECHA_Uses_Reported` and `ECHA_Reported_Pie_Data`, and require that `ECHA_All_Use_Candidates` is absent.

In `tests/test_source_origin.py`, require `Source_Origin_Pie_Data`; read the sheet and assert category sum equals input count and categories are a subset of the fixed four.

Use these workbook assertions:

```python
echa_book = load_workbook(io.BytesIO(workbook.getvalue()), read_only=True)
self.assertIn("ECHA_Uses_Reported", echa_book.sheetnames)
self.assertIn("ECHA_Reported_Pie_Data", echa_book.sheetnames)
self.assertNotIn("ECHA_All_Use_Candidates", echa_book.sheetnames)
echa_pie = pd.read_excel(io.BytesIO(workbook.getvalue()), sheet_name="ECHA_Reported_Pie_Data")
self.assertEqual(len(echa_pie), len(input_df.drop_duplicates("compound")))

# In tests/test_source_origin.py, assign the existing input frame before calling
# build_result_workbook: input_df = pd.DataFrame([_input_row("Caffeine")]).
source_book = load_workbook(io.BytesIO(workbook.getvalue()), read_only=True)
self.assertIn("Source_Origin_Pie_Data", source_book.sheetnames)
source_pie = pd.read_excel(io.BytesIO(workbook.getvalue()), sheet_name="Source_Origin_Pie_Data")
self.assertEqual(len(source_pie), len(input_df.drop_duplicates("compound")))
self.assertTrue(
    set(source_pie["display_label"]).issubset(
        {"Anthropogenic", "Natural", "Both", "Unknown"}
    )
)
```

- [ ] **Step 3: Run workbook tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin -v
```

Expected: missing pie-data sheets and obsolete mixed-sheet assertions fail.

- [ ] **Step 4: Update EPA workbook generation**

Import Task 3 functions in `src/comptox_use.py`. In `build_result_workbook`, compute:

```python
compound_universe = build_compound_universe(normalize_input_columns(input_df))
predicted_pie = extract_top_predicted_functional_use_data(
    candidates_df,
    source_label="EPA FC",
    compound_universe=compound_universe,
)
reported_pie = extract_top_reported_functional_use_data(
    candidates_df,
    compound_universe,
    source_label="EPA FC reported",
    source_type="functional_use",
    use_key="raw",
    require_reported_flag=True,
)
```

Write `EPA_Predicted_Pie_Data` and `EPA_Reported_Pie_Data`, retain `Product_Use_Categories`, `Functional_Uses_Predicted`, and `Functional_Uses_Reported`, and remove the `All_Use_Candidates` write. Do not delete the in-memory candidates dataframe because source-origin processing still consumes it.

- [ ] **Step 5: Update ECHA and source-origin workbook generation**

In `src/echa_use.py`, write candidates to `ECHA_Uses_Reported`, build the universe from normalized input, compute ECHA reported classification with `source_type=None`, `use_key="category"`, and `require_reported_flag=False`, and write `ECHA_Reported_Pie_Data`.

In `src/source_origin.py`, build the universe from `normalize_source_input_columns(input_df)`, compute `extract_source_origin_pie_data(summary_df, compound_universe)`, and write `Source_Origin_Pie_Data` between summary and evidence sheets.

- [ ] **Step 6: Run workbook-focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin -v
```

Expected: all tests pass; no user workbook contains a mixed predicted/reported candidate sheet.

- [ ] **Step 7: Commit standalone workbook changes**

```powershell
git add src/comptox_use.py src/echa_use.py src/source_origin.py tests/test_comptox_dashboard_mode.py tests/test_echa_use.py tests/test_source_origin.py
git commit -m "feat: split use workbook evidence sheets"
```

---

### Task 5: Integrate Split Tables and New Charts into One-Click Workflow

**Files:**
- Modify: `src/auto_query_workflow.py:30-105,378-461,487-659`
- Modify: `pages/6_一键批量查询.py:85-167`
- Modify: `tests/test_auto_query_workflow.py:41-599`

**Interfaces:**
- Consumes: split-table builders and classification functions from Tasks 3-4.
- Produces: internal raw candidates, user-visible split tables, six expected chart keys, root workbook sheets, module workbook sheets, and modular ZIP figures.

- [ ] **Step 1: Add failing one-click table and total-preservation tests**

Extend the mocked workflow test to include a third input compound without EPA/ECHA candidates. Assert:

```python
self.assertIn("Product_Use_Categories", result.tables)
self.assertIn("Functional_Uses_Predicted", result.tables)
self.assertIn("Functional_Uses_Reported", result.tables)
self.assertIn("EPA_Predicted_Pie_Data", result.tables)
self.assertIn("EPA_Reported_Pie_Data", result.tables)
self.assertIn("ECHA_Uses_Reported", result.tables)
self.assertIn("ECHA_Reported_Pie_Data", result.tables)
self.assertIn("Source_Origin_Pie_Data", result.tables)
self.assertEqual(len(result.tables["EPA_Reported_Pie_Data"]), 3)
self.assertEqual(len(result.tables["ECHA_Reported_Pie_Data"]), 3)
self.assertEqual(len(result.tables["Source_Origin_Pie_Data"]), 3)
```

The internal `CompTox_Candidates` and `ECHA_Use_Candidates` may remain in `result.tables`, but subsequent workbook assertions must prove they are not exported.

Add an explicit batch font-warning test:

```python
@patch("src.auto_query_workflow.configure_plot_style", return_value=["font missing"])
def test_batch_surfaces_plot_font_warning(self, configure_plot_style):
    result = run_auto_query_workflow(
        pd.DataFrame({"Name": ["A"]}),
        config=AutoWorkflowConfig(
            mapping=AutoWorkflowMapping(compound_col="Name"),
        ),
    )
    self.assertIn("font missing", result.warnings)
    self.assertEqual(result.tables["Plot_Warnings"]["warning"].tolist(), ["font missing"])
```

- [ ] **Step 2: Add failing chart-key and ZIP assertions**

Replace old expectations with:

```python
expected = {
    "EPA_Product_Use_Category_Rose_Plot",
    "EPA_Top_Predicted_Functional_Use",
    "EPA_Reported_Functional_Use_Distribution",
    "EPA_Reported_Functional_Use_Evidence",
    "ECHA_Reported_Use_Distribution",
    "ECHA_Reported_Use_Evidence",
    "Source_Origin_Distribution",
}
self.assertTrue(expected.issubset(charts))
self.assertNotIn("ECHA_Use_Rose_Plot", charts)
```

Update ZIP expectations to include PNG/PDF pairs under `04_EPA_CompTox/figures`, `05_ECHA/figures`, and `06_Source_Origin/figures`. Assert the root and module workbooks contain split sheets and do not contain `CompTox_Candidates` or `ECHA_Use_Candidates`.

- [ ] **Step 3: Run one-click tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: missing split tables/new chart keys and obsolete ECHA rose expectations fail.

- [ ] **Step 4: Build split tables immediately after each query**

Add `INTERNAL_TABLE_NAMES = {"CompTox_Candidates", "ECHA_Use_Candidates"}` near `AUTO_WORKFLOW_EXPORT_MODULES`.

Import the centralized font check in `src/auto_query_workflow.py`:

```python
from src.plot_style import configure_plot_style
```

Immediately after `tables` and `warnings` are initialized in `run_auto_query_workflow`, surface the font warning in both the result warning list and the exported audit tables:

```python
plot_warnings = configure_plot_style()
warnings.extend(plot_warnings)
if plot_warnings:
    tables["Plot_Warnings"] = pd.DataFrame({"warning": plot_warnings})
```

Add `Plot_Warnings` to the local-screening module table list and treat it as an audit table in `pages/6_一键批量查询.py`.

After query input is finalized, build one universe:

```python
compound_universe = build_compound_universe(query_input)
```

After EPA returns, retain `CompTox_Candidates` internally and add:

```python
tables["Product_Use_Categories"] = build_product_use_table(comptox_candidates)
tables["Functional_Uses_Predicted"] = build_functional_use_table(
    comptox_candidates, functional_source="predicted"
)
tables["Functional_Uses_Reported"] = build_functional_use_table(
    comptox_candidates, functional_source="reported"
)
tables["EPA_Predicted_Pie_Data"] = extract_top_predicted_functional_use_data(
    comptox_candidates, compound_universe=compound_universe
)
tables["EPA_Reported_Pie_Data"] = extract_top_reported_functional_use_data(
    comptox_candidates,
    compound_universe,
    source_label="EPA FC reported",
    source_type="functional_use",
    use_key="raw",
    require_reported_flag=True,
)
```

After ECHA returns, retain raw candidates internally, write `ECHA_Uses_Reported`, and create `ECHA_Reported_Pie_Data`. After source origin returns, create `Source_Origin_Pie_Data` from the same universe.

- [ ] **Step 5: Exclude internal tables from every workbook and dashboard**

In `build_auto_workflow_workbook`, skip names in `INTERNAL_TABLE_NAMES`:

```python
for name, table in result.tables.items():
    if name in INTERNAL_TABLE_NAMES:
        continue
    (table if table is not None else pd.DataFrame()).to_excel(
        writer, sheet_name=_safe_sheet_name(name), index=False
    )
```

Replace EPA/ECHA module table candidates in `AUTO_WORKFLOW_EXPORT_MODULES` with the split names. Add `("Source_",)` as the chart prefix for the source-origin module.

In `pages/6_一键批量查询.py`, replace `CompTox_Candidates` and `ECHA_Use_Candidates` in `_result_dashboard_groups` with their split user tables and pie audit tables; add `("Source_",)` to the source chart prefixes.

- [ ] **Step 6: Replace one-click chart configurations**

Keep the EPA product rose and predicted pie. Add a classification pie config for `EPA_Reported_Pie_Data`, keep the EPA evidence-dot config against internal candidates, replace the ECHA rose config with ECHA classification pie plus ECHA evidence-dot configs, and add a source-origin classification pie config.

Update `_build_chart_data` so classification configs return their already-built table. Update `_build_chart_figure` so:

```python
if source_config["chart_type"] == "classification_pie":
    return generate_compound_classification_pie_plot(
        chart_df,
        source_config["title"],
        footnote=source_config.get("footnote"),
        fixed_categories=source_config.get("fixed_categories"),
    )
```

Use `generate_reported_functional_use_pie_plot` for EPA/ECHA reported configs so the fixed `Others` note is guaranteed. Use fixed categories `("Anthropogenic", "Natural", "Both", "Unknown")` for source origin.

- [ ] **Step 7: Run one-click tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: all workflow, workbook, chart-key, dashboard-contract, and ZIP tests pass.

- [ ] **Step 8: Commit one-click integration**

```powershell
git add src/auto_query_workflow.py pages/6_一键批量查询.py tests/test_auto_query_workflow.py
git commit -m "feat: integrate reported summary charts in batch workflow"
```

---

### Task 6: Update the Standalone Use Page

**Files:**
- Modify: `pages/4_化合物用途查询.py:75-82,1033-1158,1210-1285,1300-1320`
- Modify: `tests/test_chemspider_one_time_key.py:51-67`

**Interfaces:**
- Consumes: shared universe, classification, donut, and generalized evidence-dot functions from Task 3.
- Produces: five use-chart choices plus source-origin pie on the standalone page; standalone workbook downloads use Task 4 builders.

- [ ] **Step 1: Replace page-source contract assertions**

Update `UseChartPageTests` to assert that the page source contains:

```python
self.assertIn("build_compound_universe", page_source)
self.assertIn("extract_top_predicted_functional_use_data", page_source)
self.assertIn("extract_top_reported_functional_use_data", page_source)
self.assertIn("extract_source_origin_pie_data", page_source)
self.assertIn("generate_reported_functional_use_pie_plot", page_source)
self.assertIn("generate_reported_functional_use_presence_plot", page_source)
self.assertIn("EPA_Reported_Functional_Use_Distribution", page_source)
self.assertIn("ECHA_Reported_Use_Distribution", page_source)
self.assertIn("ECHA_Reported_Use_Evidence", page_source)
self.assertIn("Source_Origin_Distribution", page_source)
self.assertNotIn('"file_prefix": "ECHA_Use_Rose_Plot"', page_source)
```

Keep the assertion for the existing EPA predicted file prefix.

- [ ] **Step 2: Run the page contract and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_chemspider_one_time_key.UseChartPageTests -v
```

Expected: new imports/file prefixes are absent and the obsolete ECHA rose assertion fails.

- [ ] **Step 3: Build shared page-level classification tables**

Import Task 3 functions. Before `chart_sources` is constructed, create:

```python
from src.plot_style import configure_plot_style
```

Then create the page-level classification tables:

```python
compound_universe = build_compound_universe(query_input_df)
epa_predicted_pie_df = extract_top_predicted_functional_use_data(
    comptox_candidates_df,
    source_label="EPA FC",
    compound_universe=compound_universe,
)
epa_reported_pie_df = extract_top_reported_functional_use_data(
    comptox_candidates_df,
    compound_universe,
    source_label="EPA FC reported",
    source_type="functional_use",
    use_key="raw",
    require_reported_flag=True,
)
echa_reported_pie_df = extract_top_reported_functional_use_data(
    echa_candidates_df,
    compound_universe,
    source_label="ECHA reported",
    source_type=None,
    use_key="category",
    require_reported_flag=False,
)
source_origin_pie_df = extract_source_origin_pie_data(
    source_origin_summary_df,
    compound_universe,
)
```

Pass the stored dataframes directly. The Task 3 extractors normalize `None` to empty dataframes internally while retaining every row in `compound_universe`, so missing results become `Others` or `Unknown` without page-local branches.

Near the page heading, display the same explicit font warning:

```python
for plot_warning in configure_plot_style():
    st.warning(plot_warning)
```

- [ ] **Step 4: Replace chart choices and rendering branches**

Keep EPA product rose and EPA predicted pie configs. Add EPA reported distribution and evidence configs. Replace ECHA rose with ECHA reported distribution and evidence configs. Add source origin distribution with fixed four-category order.

Use these file prefixes:

```python
"EPA_Top_Predicted_Functional_Use"
"EPA_Reported_Functional_Use_Distribution"
"EPA_Reported_Functional_Use_Evidence"
"ECHA_Reported_Use_Distribution"
"ECHA_Reported_Use_Evidence"
"Source_Origin_Distribution"
```

Render `classification_pie` data through the shared donut generator, render reported distributions through the reported wrapper, and retain `generate_reported_functional_use_presence_plot` for both evidence-dot choices. Remove the ECHA `generate_use_rose_plot` path only for reported ECHA; do not remove the EPA product-use rose chart.

- [ ] **Step 5: Update user-facing notes**

Replace wording that describes ECHA rose plots or reported-only detail behavior with concise text stating:

```text
EPA predicted retains the highest-probability summary. EPA and ECHA reported results use one unique most-reported category per compound; ties and missing results are counted as Others. Evidence-dot plots remain available for record-level review.
```

Add the source-origin four-category explanation and ensure chart captions remain English/ASCII-safe.

- [ ] **Step 6: Run standalone page and workbook contracts**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_chemspider_one_time_key tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin -v
```

Expected: all page and standalone workbook tests pass.

- [ ] **Step 7: Commit the standalone page update**

```powershell
git add pages/4_化合物用途查询.py tests/test_chemspider_one_time_key.py
git commit -m "feat: update standalone reported use charts"
```

---

### Task 7: Run Cross-Workflow Verification

**Files:**
- Verify: all files changed in Tasks 1-6
- Modify only if a verification failure exposes a requirement gap; keep any correction in the owning task's file and add a regression in the corresponding test module.

**Interfaces:**
- Consumes: all completed tasks.
- Produces: evidence that targeted behavior, full repository tests, compilation, workbook shapes, and chart exports are consistent.

- [ ] **Step 1: Run all targeted regression modules together**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_plot_style tests.test_r_screening_replica tests.test_toxpi_plot_text tests.test_cp_screening_workflow tests.test_use_rose_plot tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin tests.test_auto_query_workflow tests.test_chemspider_one_time_key -v
```

Expected: command exits 0 and ends with `OK`.

- [ ] **Step 2: Run the complete repository suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: command exits 0 with no failures or errors and ends with `OK`.

- [ ] **Step 3: Compile application and page modules**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0 and no `SyntaxError` or failed-compilation lines.

- [ ] **Step 4: Verify workbook sheet contracts from generated in-memory fixtures**

Run the workbook-focused tests again without verbose chart tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin tests.test_auto_query_workflow -v
```

Expected: separate predicted/reported sheets, pie-data sheets, root/module workbook parity, and ZIP path checks all pass.

- [ ] **Step 5: Inspect generated figures from synthetic fixtures**

Use the existing test fixtures to generate PNG and PDF bytes, then visually confirm:

- DBE background is white and gridless.
- EPA predicted remains a donut.
- EPA/ECHA reported donuts show total compounds and the fixed `Others` note.
- Small labels follow the selected inside/outside/legend-only thresholds without overlap.
- EPA/ECHA evidence dots remain readable.
- Source origin uses only `Anthropogenic`, `Natural`, `Both`, and `Unknown`.
- All visible chart text is Times New Roman on the current host.

Record any visual defect as a failing regression test before correcting it.

- [ ] **Step 6: Confirm the final diff scope**

Run:

```powershell
git status --short
git diff --check
git diff --stat f1d33da..HEAD
```

Expected: no whitespace errors; only planned source/test files and the approved design/plan documents are involved. Existing unrelated untracked files remain unmodified and unstaged.

- [ ] **Step 7: Commit verification-only corrections if required**

If Step 1-6 required a correction, stage only the corrected owning source and regression test, then commit:

```powershell
git commit -m "fix: close chart consistency regressions"
```

If no correction was required, do not create an empty commit.
