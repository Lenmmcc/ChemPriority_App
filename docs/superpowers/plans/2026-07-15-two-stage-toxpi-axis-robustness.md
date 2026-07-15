# Two-Stage ToxPi, Custom Plot Axes, and Robustness Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable DBE/VK axes and a shared two-stage PA/PBM/DF ToxPi workflow with custom weights, Top 100/Top 20 limits, robustness analysis, and complete one-click preview/export artifacts.

**Architecture:** Put all scoring, limit validation, deterministic ranking, and robustness behavior in `src/cp_screening_workflow.py`; carry axis ranges through `ScreeningConfig`; and make both Streamlit pages pass typed settings into the same cores. Preserve the public `ToxPi_Normalized` and `ToxPi_Results` table meanings as Stage 2 candidate normalization and full refined ranking while adding explicit global-screen, display, settings, and robustness tables.

**Tech Stack:** Python 3.9+, pandas, NumPy, SciPy, matplotlib, Streamlit, openpyxl, unittest.

## Global Constraints

- Keep the chemical-type chart as a donut chart without axis controls.
- DBE defaults: X `0..60`, Y `0..30`; Van Krevelen defaults: X `0..1.1`, Y `0..2.6`.
- ToxPi defaults: candidate Top 100, display Top 20, Peak Area/PBM/DF weights `0.4/0.4/0.2`.
- Robustness defaults: enabled, 1,000 simulations, seed 123, relative weight perturbation plus or minus 20%.
- Stage 1 uses the full compound universe; Stage 2 returns to source metrics and normalizes only the selected candidates.
- Use existing 5th-to-95th-percentile robust scaling for `log10(Peak Area)` and PBM; clip DF to `[0, 1]`.
- Keep previews, Excel files, and ZIP artifacts derived from the same result object.
- Preserve existing unrelated user changes and untracked files; stage only task-specific files.

---

## File Structure

- Modify `src/cp_screening_workflow.py`: typed ToxPi settings/results, two-stage calculation, robustness simulation, robustness plot, expanded workbook sheets.
- Modify `src/r_screening_replica/schema.py`: validated DBE/VK axis-range value object carried by `ScreeningConfig`.
- Modify `src/r_screening_replica/plots.py`: apply the configured limits to DBE/VK PNG and PDF figures.
- Modify `src/r_screening_replica/pipeline.py`: pass the plot ranges from `ScreeningConfig` to figure generation.
- Modify `src/auto_query_workflow.py`: carry shared settings, produce ToxPi tables/charts, and export them through the public module contract.
- Modify `pages/0_综合筛查流程.py`: add controls, pass settings, render two-stage and robustness results, and invalidate stale artifacts.
- Modify `pages/6_一键批量查询.py`: add controls and show the ToxPi tables/charts in the one-click dashboard.
- Modify `tests/test_cp_screening_workflow.py`: two-stage, weighting, limit, robustness, workbook, and comprehensive-page contracts.
- Modify `tests/test_r_screening_replica.py`: axis validation and rendered-axis tests.
- Modify `tests/test_auto_query_workflow.py`: typed config routing, public tables/charts, workbook, ZIP, and dashboard contracts.

---

### Task 1: Build the typed two-stage ToxPi scoring core

**Files:**
- Modify: `src/cp_screening_workflow.py:1-43,365-433,489-511`
- Test: `tests/test_cp_screening_workflow.py:438-511`

**Interfaces:**
- Produces: `PBMToxPiConfig`, `PBMToxPiResult`, `normalize_pbm_toxpi_weights()`, and `calculate_pbm_toxpi(toxpi_input, config=None)`.
- `PBMToxPiResult.global_screen` is the complete Stage 1 ranking; `candidate_normalized` is Stage 2 normalized data; `final_ranking` is the full candidate ranking; `display_rows` is the configured final Top N.
- Later tasks consume `PBMToxPiResult.settings_table()` and its four result tables.

- [ ] **Step 1: Write failing two-stage selection and re-normalization tests**

Add imports and tests that prove Stage 2 returns to source values and changes the candidate-set scale:

```python
from src.cp_screening_workflow import PBMToxPiConfig, PBMToxPiResult

def test_two_stage_toxpi_selects_global_candidates_then_renormalizes_source_metrics(self):
    toxpi_input = pd.DataFrame(
        {
            "compound": ["A", "B", "C", "D"],
            "Peak_Area": [1e9, 1e8, 1e6, 1e3],
            "Scores": [1.0, 9.0, 8.0, 2.0],
            "DF": [0.2, 0.9, 0.8, 0.1],
        }
    )
    config = PBMToxPiConfig(candidate_top_n=3, display_top_n=2)

    result = calculate_pbm_toxpi(toxpi_input, config=config)

    self.assertIsInstance(result, PBMToxPiResult)
    self.assertEqual(len(result.global_screen), 4)
    self.assertEqual(len(result.candidate_normalized), 3)
    self.assertEqual(len(result.final_ranking), 3)
    self.assertEqual(len(result.display_rows), 2)
    candidates = set(result.candidate_normalized["compound"])
    source = result.source_metrics.set_index("compound")
    self.assertEqual(
        result.candidate_normalized.set_index("compound").loc["B", "Scores"],
        source.loc["B", "Scores"],
    )
    self.assertEqual(candidates, set(result.global_screen.head(3)["compound"]))
    self.assertEqual(
        result.display_rows["compound"].tolist(),
        result.final_ranking.head(2)["compound"].tolist(),
    )

def test_two_stage_toxpi_caps_limits_for_small_inputs(self):
    data = pd.DataFrame(
        {"compound": ["A", "B"], "Peak_Area": [100, 10], "Scores": [2, 1], "DF": [1, 0]}
    )
    result = calculate_pbm_toxpi(
        data,
        config=PBMToxPiConfig(candidate_top_n=100, display_top_n=20),
    )
    self.assertEqual(len(result.final_ranking), 2)
    self.assertEqual(len(result.display_rows), 2)
    self.assertEqual(result.effective_candidate_top_n, 2)
    self.assertEqual(result.effective_display_top_n, 2)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow.CpScreeningWorkflowTests.test_two_stage_toxpi_selects_global_candidates_then_renormalizes_source_metrics tests.test_cp_screening_workflow.CpScreeningWorkflowTests.test_two_stage_toxpi_caps_limits_for_small_inputs -v
```

Expected: FAIL because `PBMToxPiConfig` and `PBMToxPiResult` do not exist and the current function returns a tuple.

- [ ] **Step 3: Add typed configuration/result objects and weight validation**

Change the dataclass import to `from dataclasses import dataclass, field`, then add:

```python
@dataclass(frozen=True)
class PBMToxPiConfig:
    candidate_top_n: int = 100
    display_top_n: int = 20
    weights: dict[str, float] = field(default_factory=lambda: dict(PBM_TOXPI_WEIGHTS))
    robustness_enabled: bool = True
    perturbation_fraction: float = 0.20
    n_iter: int = 1000
    seed: int = 123

    def __post_init__(self):
        if int(self.candidate_top_n) < 1:
            raise ValueError("Candidate Top N must be at least 1")
        if int(self.display_top_n) < 1:
            raise ValueError("Display Top N must be at least 1")
        if int(self.display_top_n) > int(self.candidate_top_n):
            raise ValueError("Display Top N cannot exceed Candidate Top N")
        if float(self.perturbation_fraction) < 0 or float(self.perturbation_fraction) > 1:
            raise ValueError("Weight perturbation must be between 0% and 100%")
        if int(self.n_iter) < 1:
            raise ValueError("Robustness iterations must be at least 1")
        normalize_pbm_toxpi_weights(self.weights)


@dataclass
class PBMToxPiResult:
    config: PBMToxPiConfig
    source_metrics: pd.DataFrame
    global_screen: pd.DataFrame
    candidate_normalized: pd.DataFrame
    final_ranking: pd.DataFrame
    display_rows: pd.DataFrame
    normalized_weights: dict[str, float]
    effective_candidate_top_n: int
    effective_display_top_n: int
    robustness_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    robustness_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    robustness_correlations: pd.DataFrame = field(default_factory=pd.DataFrame)

    def settings_table(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"setting": "requested_candidate_top_n", "value": self.config.candidate_top_n},
                {"setting": "candidate_top_n", "value": self.effective_candidate_top_n},
                {"setting": "requested_display_top_n", "value": self.config.display_top_n},
                {"setting": "display_top_n", "value": self.effective_display_top_n},
                {"setting": "robustness_enabled", "value": self.config.robustness_enabled},
                {"setting": "perturbation_fraction", "value": self.config.perturbation_fraction},
                {"setting": "robustness_iterations", "value": self.config.n_iter},
                {"setting": "robustness_seed", "value": self.config.seed},
                *[
                    {"setting": f"weight_{name}", "value": value}
                    for name, value in self.normalized_weights.items()
                ],
            ]
        )


def normalize_pbm_toxpi_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    supplied = weights or PBM_TOXPI_WEIGHTS
    required = tuple(PBM_TOXPI_WEIGHTS)
    values = {name: float(supplied.get(name, 0.0)) for name in required}
    if any(value < 0 for value in values.values()):
        raise ValueError("ToxPi weights cannot be negative")
    total = sum(values.values())
    if total <= 0:
        raise ValueError("ToxPi weights must sum to a positive value")
    return {name: value / total for name, value in values.items()}
```

- [ ] **Step 4: Replace the single-stage calculation with source aggregation plus two scoring stages**

Implement helpers and replace `calculate_pbm_toxpi`:

```python
def _compound_toxpi_source(toxpi_input: pd.DataFrame) -> pd.DataFrame:
    data = toxpi_input.copy()
    data["compound"] = data["compound"].map(_clean_text)
    for column in ("Peak_Area", "Scores", "DF"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    source = (
        data.loc[data["compound"].ne("")]
        .groupby("compound", as_index=False)
        .agg(Peak_Area=("Peak_Area", "mean"), Scores=("Scores", "mean"), DF=("DF", "mean"))
    )
    source["ir_value"] = np.nan
    mask = source["Peak_Area"] > 0
    source.loc[mask, "ir_value"] = np.log10(source.loc[mask, "Peak_Area"])
    return source


def _score_pbm_toxpi_stage(source: pd.DataFrame, weights: dict[str, float], score_name: str) -> pd.DataFrame:
    scored = source.copy()
    scored["norm_peak_area"] = _normalize_positive(scored["ir_value"])
    scored["norm_pbm"] = _normalize_positive(scored["Scores"])
    scored["norm_df"] = pd.to_numeric(scored["DF"], errors="coerce").clip(0, 1)
    matrix = scored[["norm_peak_area", "norm_pbm", "norm_df"]].to_numpy(dtype=float)
    vector = np.array([weights["peak_area"], weights["pbm"], weights["df"]], dtype=float)
    scored[score_name] = _weighted_indicator_scores(matrix, vector)
    return scored


def _weighted_indicator_scores(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
    valid = ~np.isnan(matrix)
    clean = np.nan_to_num(matrix, nan=0.0)
    denominators = valid @ weights
    numerators = (clean * weights).sum(axis=1)
    return np.divide(
        numerators,
        denominators,
        out=np.full(len(matrix), np.nan, dtype=float),
        where=denominators > 0,
    )


def _sort_toxpi_stage(frame: pd.DataFrame, score_col: str, rank_col: str) -> pd.DataFrame:
    ranked = frame.copy()
    ranked["_compound_key"] = ranked["compound"].map(lambda value: _clean_text(value).casefold())
    ranked = ranked.sort_values(
        [score_col, "Peak_Area", "_compound_key"],
        ascending=[False, False, True],
        na_position="last",
        kind="mergesort",
    ).drop(columns="_compound_key").reset_index(drop=True)
    ranked[rank_col] = np.arange(1, len(ranked) + 1)
    ranked.attrs["toxic_cols"] = ["peak_area", "pbm", "df"]
    return ranked


def calculate_pbm_toxpi(
    toxpi_input: pd.DataFrame,
    config: PBMToxPiConfig | None = None,
) -> PBMToxPiResult:
    config = config or PBMToxPiConfig()
    required = ["compound", "Peak_Area", "Scores", "DF"]
    missing = [column for column in required if column not in toxpi_input.columns]
    if missing:
        raise ValueError(f"Missing required ToxPi input columns: {', '.join(missing)}")
    weights = normalize_pbm_toxpi_weights(config.weights)
    source = _compound_toxpi_source(toxpi_input)
    global_screen = _sort_toxpi_stage(
        _score_pbm_toxpi_stage(source, weights, "initial_toxpi"),
        "initial_toxpi",
        "initial_rank",
    )
    candidate_n = min(int(config.candidate_top_n), len(global_screen))
    candidate_source = source.merge(
        global_screen.head(candidate_n)[["compound"]], on="compound", how="inner"
    )
    candidate_normalized = _score_pbm_toxpi_stage(candidate_source, weights, "toxpi")
    final_ranking = _sort_toxpi_stage(candidate_normalized, "toxpi", "final_rank")
    display_n = min(int(config.display_top_n), len(final_ranking))
    display_rows = final_ranking.head(display_n).copy()
    return PBMToxPiResult(
        config=config,
        source_metrics=source,
        global_screen=global_screen,
        candidate_normalized=candidate_normalized,
        final_ranking=final_ranking,
        display_rows=display_rows,
        normalized_weights=weights,
        effective_candidate_top_n=candidate_n,
        effective_display_top_n=display_n,
    )
```

- [ ] **Step 5: Add weight, validation, and deterministic tie tests**

```python
def test_two_stage_toxpi_normalizes_custom_weights_and_uses_them_in_both_stages(self):
    data = pd.DataFrame(
        {"compound": ["PA", "PBM"], "Peak_Area": [1e8, 1e2], "Scores": [1, 10], "DF": [0.5, 0.5]}
    )
    pa_result = calculate_pbm_toxpi(
        data,
        config=PBMToxPiConfig(candidate_top_n=2, display_top_n=2, weights={"peak_area": 8, "pbm": 1, "df": 1}),
    )
    pbm_result = calculate_pbm_toxpi(
        data,
        config=PBMToxPiConfig(candidate_top_n=2, display_top_n=2, weights={"peak_area": 1, "pbm": 8, "df": 1}),
    )
    self.assertEqual(pa_result.final_ranking.loc[0, "compound"], "PA")
    self.assertEqual(pbm_result.final_ranking.loc[0, "compound"], "PBM")
    self.assertAlmostEqual(sum(pa_result.normalized_weights.values()), 1.0)

def test_two_stage_toxpi_rejects_invalid_settings(self):
    with self.assertRaisesRegex(ValueError, "positive"):
        PBMToxPiConfig(weights={"peak_area": 0, "pbm": 0, "df": 0})
    with self.assertRaisesRegex(ValueError, "cannot exceed"):
        PBMToxPiConfig(candidate_top_n=10, display_top_n=20)
```

- [ ] **Step 6: Run the focused core suite and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow -v
```

Expected: all `test_cp_screening_workflow` tests pass after updating old tuple-unpacking assertions to inspect `result.candidate_normalized` and `result.final_ranking`.

- [ ] **Step 7: Commit the scoring core**

```powershell
git add src/cp_screening_workflow.py tests/test_cp_screening_workflow.py
git commit -m "feat: add two-stage PBM ToxPi scoring"
```

---

### Task 2: Add reproducible weight-perturbation robustness analysis

**Files:**
- Modify: `src/cp_screening_workflow.py:365-511`
- Test: `tests/test_cp_screening_workflow.py`

**Interfaces:**
- Consumes: `PBMToxPiConfig`, `PBMToxPiResult.final_ranking`, and normalized indicator columns from Task 1.
- Produces: `run_pbm_toxpi_robustness(result, config)`, populated robustness tables on `PBMToxPiResult`, and `generate_pbm_toxpi_robustness_plot()`.

- [ ] **Step 1: Write failing reproducibility, range, and Top-N frequency tests**

```python
from src.cp_screening_workflow import run_pbm_toxpi_robustness

def test_toxpi_robustness_is_reproducible_and_uses_configured_display_top_n(self):
    data = pd.DataFrame(
        {
            "compound": [f"C{i}" for i in range(6)],
            "Peak_Area": [1e8, 1e7, 1e6, 1e5, 1e4, 1e3],
            "Scores": [1, 4, 3, 6, 2, 5],
            "DF": [0.9, 0.2, 0.8, 0.4, 0.7, 0.1],
        }
    )
    config = PBMToxPiConfig(
        candidate_top_n=6,
        display_top_n=2,
        perturbation_fraction=0.35,
        n_iter=40,
        seed=77,
    )
    first = run_pbm_toxpi_robustness(calculate_pbm_toxpi(data, config), config)
    second = run_pbm_toxpi_robustness(calculate_pbm_toxpi(data, config), config)
    pd.testing.assert_frame_equal(first.robustness_summary, second.robustness_summary)
    pd.testing.assert_frame_equal(first.robustness_correlations, second.robustness_correlations)
    self.assertEqual(first.robustness_stats.loc[0, "perturbation_fraction"], 0.35)
    self.assertEqual(first.robustness_stats.loc[0, "display_top_n"], 2)
    self.assertTrue(first.robustness_summary["top_n_frequency_percent"].between(0, 100).all())
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow.CpScreeningWorkflowTests.test_toxpi_robustness_is_reproducible_and_uses_configured_display_top_n -v
```

Expected: FAIL because `run_pbm_toxpi_robustness` does not exist.

- [ ] **Step 3: Implement robustness simulation against the fixed Stage 2 matrix**

Add `from scipy.stats import spearmanr` and implement:

```python
def run_pbm_toxpi_robustness(
    result: PBMToxPiResult,
    config: PBMToxPiConfig,
) -> PBMToxPiResult:
    candidates = result.final_ranking.copy()
    if len(candidates) < 2:
        raise ValueError("At least 2 candidates are required for robustness analysis")
    cols = ["norm_peak_area", "norm_pbm", "norm_df"]
    matrix = candidates[cols].to_numpy(dtype=float)
    baseline = np.array([result.normalized_weights[name] for name in PBM_TOXPI_WEIGHTS])
    rng = np.random.default_rng(int(config.seed))
    lower = 1.0 - float(config.perturbation_fraction)
    upper = 1.0 + float(config.perturbation_fraction)
    multipliers = rng.uniform(lower, upper, size=(int(config.n_iter), len(baseline)))
    weights = multipliers * baseline
    weights = weights / weights.sum(axis=1, keepdims=True)
    baseline_ranks = candidates["final_rank"].to_numpy(dtype=float)
    counts = np.zeros(len(candidates), dtype=int)
    correlations = []
    top_n = result.effective_display_top_n
    for index, simulated_weights in enumerate(weights, start=1):
        scores = _weighted_indicator_scores(matrix, simulated_weights)
        order = np.argsort(-scores, kind="stable")
        ranks = np.empty(len(order), dtype=float)
        ranks[order] = np.arange(1, len(order) + 1)
        rho = spearmanr(baseline_ranks, ranks).statistic
        correlations.append({"iteration": index, "spearman_rho": 0.0 if np.isnan(rho) else float(rho)})
        counts[order[:top_n]] += 1
    correlation_df = pd.DataFrame(correlations)
    result.robustness_correlations = correlation_df
    result.robustness_summary = candidates[["compound", "toxpi", "final_rank"]].assign(
        top_n_frequency_percent=np.round(counts / int(config.n_iter) * 100, 2)
    )
    rho_values = correlation_df["spearman_rho"]
    result.robustness_stats = pd.DataFrame(
        [{
            "mean_rho": rho_values.mean(),
            "sd_rho": rho_values.std(ddof=0),
            "ci_lower": rho_values.quantile(0.025),
            "ci_upper": rho_values.quantile(0.975),
            "perturbation_fraction": float(config.perturbation_fraction),
            "iterations": int(config.n_iter),
            "seed": int(config.seed),
            "display_top_n": top_n,
        }]
    )
    return result
```

The shared `_weighted_indicator_scores` helper re-normalizes the active weights over each row's non-missing indicators. Baseline and simulated scoring therefore use exactly the same missing-data rule.

- [ ] **Step 4: Implement the robustness histogram**

```python
def generate_pbm_toxpi_robustness_plot(result: PBMToxPiResult):
    values = result.robustness_correlations["spearman_rho"]
    if values.empty:
        raise ValueError("Robustness correlations are empty")
    mean_rho = float(result.robustness_stats.loc[0, "mean_rho"])
    fig, ax = plt.subplots(figsize=(8, 5.5), facecolor="white")
    ax.hist(values, bins=min(30, max(5, int(np.sqrt(len(values))))), color="#2E8B57", edgecolor="black")
    ax.axvline(mean_rho, color="#D62728", linestyle="--", linewidth=1.3)
    ax.set_title("ToxPi Rank Robustness")
    ax.set_xlabel("Spearman correlation with baseline ranking")
    ax.set_ylabel("Frequency")
    ax.text(0.02, 0.96, f"Mean rho = {mean_rho:.3f}", transform=ax.transAxes, va="top")
    fig.tight_layout()
    return apply_figure_font(fig)
```

- [ ] **Step 5: Wire enabled robustness into `calculate_pbm_toxpi` and add disabled/small-set tests**

At the end of `calculate_pbm_toxpi`, create `result`, then:

```python
if config.robustness_enabled and len(result.final_ranking) >= 2:
    run_pbm_toxpi_robustness(result, config)
return result
```

Add tests proving `robustness_enabled=False` returns empty tables and a one-candidate result keeps its main ranking without failing.

- [ ] **Step 6: Run focused robustness tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow -v
```

Expected: all scoring and robustness tests pass.

- [ ] **Step 7: Commit the robustness core**

```powershell
git add src/cp_screening_workflow.py tests/test_cp_screening_workflow.py
git commit -m "feat: add ToxPi ranking robustness analysis"
```

---

### Task 3: Add validated DBE and Van Krevelen axis ranges

**Files:**
- Modify: `src/r_screening_replica/schema.py:1-25`
- Modify: `src/r_screening_replica/plots.py:79-149,309-390`
- Modify: `src/r_screening_replica/pipeline.py:45-56`
- Test: `tests/test_r_screening_replica.py`

**Interfaces:**
- Produces: `ScreeningAxisRanges` with `dbe_xlim`, `dbe_ylim`, `vk_xlim`, and `vk_ylim` properties.
- `ScreeningConfig.axis_ranges` is consumed by `generate_all_figures` and both save functions.

- [ ] **Step 1: Write failing validation and applied-axis tests**

```python
from src.r_screening_replica.schema import ScreeningAxisRanges
from src.r_screening_replica.plots import _draw_compound_bubble, _draw_van_krevelen

def test_screening_axis_ranges_validate_minimum_before_maximum(self):
    with self.assertRaisesRegex(ValueError, "DBE X"):
        ScreeningAxisRanges(dbe_x_min=5, dbe_x_max=5)

def test_dbe_and_vk_drawers_apply_custom_axis_ranges(self):
    ranges = ScreeningAxisRanges(
        dbe_x_min=10, dbe_x_max=40, dbe_y_min=2, dbe_y_max=18,
        vk_x_min=0.2, vk_x_max=0.9, vk_y_min=0.4, vk_y_max=2.1,
    )
    bubble_data = pd.DataFrame(
        {
            "carbon_count": [20.0],
            "DBE": [8.0],
            "area_level": ["Level 1"],
            "Category": ["CH"],
        }
    )
    vk_data = pd.DataFrame({"o_c": [0.5], "h_c": [1.2], "Category": ["CHO"]})
    fig, (dbe_ax, vk_ax) = plt.subplots(1, 2)
    _draw_compound_bubble(dbe_ax, bubble_data, ranges)
    _draw_van_krevelen(vk_ax, vk_data, ranges)
    self.assertEqual(dbe_ax.get_xlim(), (10.0, 40.0))
    self.assertEqual(dbe_ax.get_ylim(), (2.0, 18.0))
    self.assertEqual(vk_ax.get_xlim(), (0.2, 0.9))
    self.assertEqual(vk_ax.get_ylim(), (0.4, 2.1))
    plt.close(fig)
```

- [ ] **Step 2: Run the axis tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_r_screening_replica -v
```

Expected: FAIL because `ScreeningAxisRanges` and draw-helper range parameters do not exist.

- [ ] **Step 3: Add the validated range object to `schema.py`**

```python
@dataclass(frozen=True)
class ScreeningAxisRanges:
    dbe_x_min: float = 0.0
    dbe_x_max: float = 60.0
    dbe_y_min: float = 0.0
    dbe_y_max: float = 30.0
    vk_x_min: float = 0.0
    vk_x_max: float = 1.1
    vk_y_min: float = 0.0
    vk_y_max: float = 2.6

    def __post_init__(self):
        for label, lower, upper in (
            ("DBE X", self.dbe_x_min, self.dbe_x_max),
            ("DBE Y", self.dbe_y_min, self.dbe_y_max),
            ("Van Krevelen X", self.vk_x_min, self.vk_x_max),
            ("Van Krevelen Y", self.vk_y_min, self.vk_y_max),
        ):
            if float(upper) <= float(lower):
                raise ValueError(f"{label} maximum must be greater than minimum")

    @property
    def dbe_xlim(self):
        return (float(self.dbe_x_min), float(self.dbe_x_max))

    @property
    def dbe_ylim(self):
        return (float(self.dbe_y_min), float(self.dbe_y_max))

    @property
    def vk_xlim(self):
        return (float(self.vk_x_min), float(self.vk_x_max))

    @property
    def vk_ylim(self):
        return (float(self.vk_y_min), float(self.vk_y_max))
```

Add `axis_ranges: ScreeningAxisRanges = field(default_factory=ScreeningAxisRanges)` to `ScreeningConfig`.

- [ ] **Step 4: Thread the ranges through all plotting functions**

Change signatures and calls so `generate_all_figures(..., axis_ranges)`, `save_compound_bubble_plot(..., axis_ranges)`, and `save_van_krevelen_plot(..., axis_ranges)` all receive the same object. Replace fixed limits with:

```python
ax.set_xlim(*axis_ranges.dbe_xlim)
ax.set_ylim(*axis_ranges.dbe_ylim)
```

and:

```python
ax.set_xlim(*axis_ranges.vk_xlim)
ax.set_ylim(*axis_ranges.vk_ylim)
```

In `run_screening_pipeline`, call:

```python
figure_paths, warnings_list = generate_all_figures(
    category_summary,
    dbe_table,
    compound_categories,
    sample_peak_area_long,
    output_dir,
    axis_ranges=config.axis_ranges,
)
```

- [ ] **Step 5: Verify default compatibility and custom PNG/PDF generation**

Extend the small-workbook pipeline test with a custom `ScreeningAxisRanges`; assert both DBE/VK PNG and PDF paths exist, then inspect the draw-helper axes for exact limits. Keep existing default-output assertions unchanged.

- [ ] **Step 6: Run the local screening suite and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_r_screening_replica -v
```

Expected: all local screening tests pass.

- [ ] **Step 7: Commit the plot-range change**

```powershell
git add src/r_screening_replica/schema.py src/r_screening_replica/plots.py src/r_screening_replica/pipeline.py tests/test_r_screening_replica.py
git commit -m "feat: add custom DBE and VK axis ranges"
```

---

### Task 4: Integrate settings and results into the comprehensive screening page

**Files:**
- Modify: `pages/0_综合筛查流程.py:22-87,121-159,531-641,674-741,814-1050`
- Modify: `src/cp_screening_workflow.py:17-33,436-464`
- Test: `tests/test_cp_screening_workflow.py:512-590`

**Interfaces:**
- Consumes: `ScreeningAxisRanges`, `PBMToxPiConfig`, `PBMToxPiResult`, robustness and plot helpers.
- Produces session state derived from one settings signature, and workbook sheets keyed by the shared result tables.

- [ ] **Step 1: Write failing comprehensive-page and workbook-contract tests**

```python
def test_screening_workbook_contains_two_stage_and_robustness_sheets(self):
    expected = {
        "ToxPi_Global_Screen",
        "ToxPi_Normalized",
        "ToxPi_Results",
        "ToxPi_Display",
        "ToxPi_Settings",
        "ToxPi_Robustness",
        "ToxPi_Robust_Stats",
    }
    self.assertTrue(expected.issubset(EXPECTED_WORKBOOK_SHEETS))

def test_comprehensive_page_exposes_shared_axis_toxpi_and_robustness_controls(self):
    page_text = Path("pages/0_综合筛查流程.py").read_text(encoding="utf-8")
    for token in (
        "ScreeningAxisRanges(", "PBMToxPiConfig(", "candidate_top_n",
        "display_top_n", "perturbation_fraction", "robustness_enabled",
        "ToxPi_Global_Screen", "ToxPi_Robustness",
    ):
        self.assertIn(token, page_text)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow.CpScreeningWorkflowTests.test_screening_workbook_contains_two_stage_and_robustness_sheets tests.test_cp_screening_workflow.CpScreeningWorkflowTests.test_comprehensive_page_exposes_shared_axis_toxpi_and_robustness_controls -v
```

Expected: FAIL because the sheets and controls are absent.

- [ ] **Step 3: Expand the workbook contract without removing existing public sheets**

Insert these names after `ToxPi_Input`/`ToxPi_Results` as appropriate:

```python
"ToxPi_Global_Screen",
"ToxPi_Normalized",
"ToxPi_Results",
"ToxPi_Display",
"ToxPi_Settings",
"ToxPi_Robustness",
"ToxPi_Robust_Stats",
```

Keep `ToxPi_Input` as the source input and `ToxPi_Results` as the full Stage 2 refined ranking.

- [ ] **Step 4: Add DBE/VK controls before front-half execution**

Add four paired numeric-input rows and construct one value object:

```python
axis_ranges = ScreeningAxisRanges(
    dbe_x_min=float(dbe_x_min), dbe_x_max=float(dbe_x_max),
    dbe_y_min=float(dbe_y_min), dbe_y_max=float(dbe_y_max),
    vk_x_min=float(vk_x_min), vk_x_max=float(vk_x_max),
    vk_y_min=float(vk_y_min), vk_y_max=float(vk_y_max),
)
```

Change `collect_front_half(samples, sample_mappings, detection_threshold)` to accept `axis_ranges` and place it into every per-sample `ScreeningConfig` and summary DBE save call. Catch `ValueError` beside the controls and call `st.stop()` with the field-specific message.

- [ ] **Step 5: Add two-stage ToxPi and robustness controls before downstream execution**

Use number inputs for candidate/display Top N, numeric weights, a robustness checkbox, perturbation percentage, iterations, and seed. Construct:

```python
toxpi_config = PBMToxPiConfig(
    candidate_top_n=int(candidate_top_n),
    display_top_n=int(display_top_n),
    weights={"peak_area": float(pa_weight), "pbm": float(pbm_weight), "df": float(df_weight)},
    robustness_enabled=bool(robustness_enabled),
    perturbation_fraction=float(perturbation_percent) / 100.0,
    n_iter=int(robustness_iterations),
    seed=int(robustness_seed),
)
```

Replace tuple unpacking with:

```python
toxpi_result = calculate_pbm_toxpi(toxpi_input, config=toxpi_config)
```

Store all fields separately in `cp_screening_downstream` and use `toxpi_result.display_rows` for both radial and bar plots. Pass `toxpi_result.normalized_weights` into the radial plot instead of `PBM_TOXPI_WEIGHTS`.

- [ ] **Step 6: Render all traceable tables and robustness artifacts**

Map workbook/page keys exactly:

```python
"ToxPi_Input": toxpi_input,
"ToxPi_Global_Screen": toxpi_result.global_screen,
"ToxPi_Normalized": toxpi_result.candidate_normalized,
"ToxPi_Results": toxpi_result.final_ranking,
"ToxPi_Display": toxpi_result.display_rows,
"ToxPi_Settings": toxpi_result.settings_table(),
"ToxPi_Robustness": toxpi_result.robustness_summary,
"ToxPi_Robust_Stats": toxpi_result.robustness_stats,
```

Generate/store the robustness PNG/PDF only when correlations exist. Replace the fixed `TOXPI_RADIAL_MAX_COMPOUNDS` message with `toxpi_result.effective_display_top_n`.

- [ ] **Step 7: Invalidate stale session artifacts when settings change**

Build a stable SHA-256 signature from the axis/ToxPi settings tuple. Store it under `cp_screening_settings_signature`; when it changes, clear `cp_screening_front`, downstream, workbook, and all plot byte keys before a new run. Add the signature key and robustness PNG/PDF keys to `STATE_KEYS`.

- [ ] **Step 8: Run comprehensive workflow regressions and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow tests.test_toxpi_plot_text -v
```

Expected: all tests pass and no fixed Top 15 assumption remains in the comprehensive-page contracts.

- [ ] **Step 9: Commit the comprehensive-page integration**

```powershell
git add src/cp_screening_workflow.py pages/0_综合筛查流程.py tests/test_cp_screening_workflow.py tests/test_toxpi_plot_text.py
git commit -m "feat: add configurable ToxPi analysis to screening"
```

---

### Task 5: Integrate ToxPi plots, robustness, and ranges into one-click batch query

**Files:**
- Modify: `src/auto_query_workflow.py:16-25,55-153,169-231,261-380,648-704,885-978`
- Modify: `pages/6_一键批量查询.py:6-36,86-211,330-485`
- Test: `tests/test_auto_query_workflow.py:69-140,703-810,872-1130`

**Interfaces:**
- Consumes: `ScreeningAxisRanges`, `PBMToxPiConfig`, `PBMToxPiResult`, radial/bar/robustness figure helpers.
- Produces: `_build_pbm_toxpi_output(toxpi_input, config)`, `PbmToxPiOutput(tables, charts)`, three public ToxPi chart keys, expanded module workbook tables, and dashboard grouping under the ToxPi tab.

- [ ] **Step 1: Write failing one-click result and export tests**

```python
def test_one_click_toxpi_output_contains_two_stage_tables_and_three_charts(self):
    toxpi_input = pd.DataFrame(
        {
            "compound": ["A", "B", "C", "D"],
            "Peak_Area": [1e8, 1e7, 1e6, 1e5],
            "Scores": [1.0, 4.0, 2.0, 3.0],
            "DF": [0.9, 0.4, 0.7, 0.2],
        }
    )
    output = _build_pbm_toxpi_output(
        toxpi_input,
        PBMToxPiConfig(candidate_top_n=4, display_top_n=2, n_iter=20, seed=5),
    )
    self.assertTrue({
        "ToxPi_Global_Screen", "ToxPi_Normalized", "ToxPi_Results",
        "ToxPi_Display", "ToxPi_Settings", "ToxPi_Robustness", "ToxPi_Robust_Stats",
    }.issubset(output.tables))
    self.assertEqual(
        set(output.charts),
        {"ToxPi_Radial_Plot", "ToxPi_Ranking_Bar", "ToxPi_Robustness_Histogram"},
    )

def test_one_click_toxpi_charts_and_tables_are_exported_in_module_zip(self):
    toxpi_input = pd.DataFrame(
        {
            "compound": ["A", "B", "C"],
            "Peak_Area": [1e7, 1e6, 1e5],
            "Scores": [1.0, 3.0, 2.0],
            "DF": [0.8, 0.3, 0.6],
        }
    )
    output = _build_pbm_toxpi_output(
        toxpi_input,
        PBMToxPiConfig(candidate_top_n=3, display_top_n=2, n_iter=10, seed=5),
    )
    result = AutoWorkflowResult(
        mapping=AutoWorkflowMapping(),
        representative_table=pd.DataFrame({"Name": ["A", "B", "C"]}),
        tables=output.tables,
        step_status=pd.DataFrame(),
        warnings=pd.DataFrame(),
        charts=output.charts,
    )
    package = build_auto_workflow_zip(result, charts=result.charts)
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        self.assertIn("07_Pov_LRTP_PBM_ToxPi/figures/ToxPi_Radial_Plot.png", names)
        self.assertIn("07_Pov_LRTP_PBM_ToxPi/figures/ToxPi_Ranking_Bar.pdf", names)
        self.assertIn("07_Pov_LRTP_PBM_ToxPi/figures/ToxPi_Robustness_Histogram.png", names)
        workbook = pd.ExcelFile(io.BytesIO(archive.read("07_Pov_LRTP_PBM_ToxPi/Pov_LRTP_PBM_ToxPi_Results.xlsx")))
        self.assertIn("ToxPi_Global_Screen", workbook.sheet_names)
        self.assertIn("ToxPi_Robustness", workbook.sheet_names)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: new tests fail because one-click ToxPi currently returns only tables and has no charts/config.

- [ ] **Step 3: Add typed nested settings to `AutoWorkflowConfig`**

Import all required shared helpers:

```python
from src.cp_screening_workflow import (
    PBMToxPiConfig,
    build_detection_frequency,
    build_group_area_mean_by_sample,
    build_pbm_toxpi_input,
    build_peak_area_long,
    calculate_pbm_toxpi,
    generate_pbm_toxpi_bar_plot,
    generate_pbm_toxpi_robustness_plot,
)
from src.r_screening_replica.schema import ScreeningAxisRanges
from src.toxpi_calc import generate_r_style_toxpi_plot
```

Then add to `AutoWorkflowConfig`:

```python
axis_ranges: ScreeningAxisRanges = field(default_factory=ScreeningAxisRanges)
toxpi_config: PBMToxPiConfig = field(default_factory=PBMToxPiConfig)
```

Change the local-screen call to pass `config.axis_ranges`, and place that object into its `ScreeningConfig`. Change the ToxPi call to pass `config.toxpi_config`.

- [ ] **Step 4: Return ToxPi tables and charts as one bounded output**

Add:

```python
@dataclass
class PbmToxPiOutput:
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, AutoWorkflowChart]
```

Implement `_build_pbm_toxpi_output(toxpi_input, config)` to calculate one `PBMToxPiResult`, build the exact table mapping, and create charts from `display_rows`. Change `_run_pov_lrtp_toxpi(..., config: PBMToxPiConfig)` to finish query-dependent input construction and then return this helper's output:

```python
radial = generate_r_style_toxpi_plot(
    toxpi_result.display_rows,
    custom_weights=toxpi_result.normalized_weights,
    toxic_cols=["peak_area", "pbm", "df"],
    label_wrap_width=20,
)
bar = generate_pbm_toxpi_bar_plot(
    toxpi_result.display_rows,
    top_n=toxpi_result.effective_display_top_n,
)
robustness = generate_pbm_toxpi_robustness_plot(toxpi_result) if not toxpi_result.robustness_correlations.empty else None
```

Convert figures to PNG/PDF bytes inside `try/finally` blocks and close every figure. Update `run_auto_query_workflow` to merge `output.tables` into `tables` and `output.charts` into `charts`.

- [ ] **Step 5: Expand exact public table/chart allowlists**

Extend the ToxPi module tuple with:

```python
(
    "Pov_LRTP_Input", "Pov_LRTP", "ToxPi_Input", "ToxPi_Global_Screen",
    "ToxPi_Normalized", "ToxPi_Results", "ToxPi_Display", "ToxPi_Settings",
    "ToxPi_Robustness", "ToxPi_Robust_Stats",
)
```

and the chart tuple with:

```python
("ToxPi_Radial_Plot", "ToxPi_Ranking_Bar", "ToxPi_Robustness_Histogram")
```

Update the exact-allowlist tests rather than weakening them to prefix-based acceptance.

- [ ] **Step 6: Add one-click page controls and settings construction**

Inside the run-settings expander, add DBE X min/max (`0`, `60`), DBE Y min/max (`0`, `30`), Van Krevelen X min/max (`0`, `1.1`), and Van Krevelen Y min/max (`0`, `2.6`). Add candidate Top N (`100`), display Top N (`20`), Peak Area/PBM/DF weights (`40`, `40`, `20`), robustness enabled (`True`), perturbation percent (`20`), iterations (`1000`), and seed (`123`). Construct `ScreeningAxisRanges` and `PBMToxPiConfig` before `AutoWorkflowConfig`, then pass them as `axis_ranges=axis_ranges` and `toxpi_config=toxpi_config`. Show field-specific validation errors and stop before starting network/query work.

- [ ] **Step 7: Add ToxPi charts to the ToxPi result-dashboard group**

Change the ToxPi group chart prefixes from empty to explicit keys:

```python
("ToxPi_",)
```

Add the new tables to the ToxPi group's table candidates. Keep robustness settings/statistics visible as audit/detail tables while showing `ToxPi_Results` and `ToxPi_Display` directly.

- [ ] **Step 8: Rebuild and verify ZIP/workbook/dashboard contracts**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_auto_query_progress -v
```

Expected: all one-click tests pass; all three ToxPi chart formats exist in the module ZIP; root/module workbooks use the updated exact allowlists; no network calls occur in tests.

- [ ] **Step 9: Commit the one-click integration**

```powershell
git add src/auto_query_workflow.py pages/6_一键批量查询.py tests/test_auto_query_workflow.py tests/test_auto_query_progress.py
git commit -m "feat: add ToxPi analysis to one-click results"
```

---

### Task 6: Run end-to-end regression and source verification

**Files:**
- Modify only if verification exposes a task-scoped defect.
- Verify: `app.py`, `pages/`, `src/`, `tests/`

**Interfaces:**
- Consumes all prior task outputs.
- Produces fresh evidence that the complete repository remains valid.

- [ ] **Step 1: Run the three focused regression groups**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow tests.test_r_screening_replica tests.test_auto_query_workflow -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the full repository suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all discovered tests pass with zero failures and zero errors.

- [ ] **Step 3: Compile all application modules and pages**

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0 and no syntax errors.

- [ ] **Step 4: Inspect the final diff and verify requirement coverage**

```powershell
git diff --check
git status --short
git diff --stat
```

Confirm explicitly:

- chemical-type donut unchanged;
- DBE/VK custom ranges reach preview and export generation;
- Stage 1 full-universe normalization and Top 100 selection;
- Stage 2 source-value re-normalization and Top 20 plots;
- shared custom weights in both stages and plots;
- configurable plus/minus perturbation and reproducible robustness outputs;
- one-click dashboard, workbook, and ZIP include the requested ToxPi artifacts;
- unrelated pre-existing untracked files are not staged.
