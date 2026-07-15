# Two-Stage ToxPi, Custom Plot Axes, and Robustness Analysis Design

**Date:** 2026-07-15

## Goal

Improve the comprehensive screening and one-click batch workflows by allowing custom DBE and Van Krevelen plot ranges, replacing the single-pass PA/PBM/DF ToxPi calculation with a configurable two-stage normalization workflow, and adding custom weights plus reproducible ranking-robustness analysis.

## Scope

This design covers:

- custom X/Y axis ranges for the DBE and Van Krevelen plots;
- a two-stage PA/PBM/DF ToxPi workflow shared by the comprehensive screening page and one-click batch query page;
- configurable candidate and display limits, defaulting to Top 100 and Top 20;
- configurable Peak Area, PBM, and DF weights;
- configurable random weight perturbations, defaulting to plus or minus 20%;
- ToxPi ranking, radial plots, robustness results, workbooks, and ZIP exports on the one-click results surface.

The chemical-type percentage chart remains a donut chart and therefore does not receive X/Y axis controls. The standalone ADMETlab-oriented ToxPi page is not redesigned by this change; its existing behavior remains available.

## User-Facing Controls

Both `pages/0_综合筛查流程.py` and `pages/6_一键批量查询.py` expose the same relevant settings.

### Local screening plots

- DBE X range, default `0` to `60`.
- DBE Y range, default `0` to `30`.
- Van Krevelen X range, default `0` to `1.1`.
- Van Krevelen Y range, default `0` to `2.6`.

Each maximum must be greater than its corresponding minimum. The selected ranges apply to both the on-page previews and exported PNG/PDF files.

### Two-stage ToxPi

- Candidate limit, default Top 100.
- Final display/plot limit, default Top 20.
- Peak Area weight, default 40%.
- PBM weight, default 40%.
- DF weight, default 20%.

Weights are accepted as non-negative user values and normalized internally to sum to one. At least one weight must be positive. Candidate and display limits must be positive integers. Effective limits are capped to the available compound count, and the final display limit cannot exceed the effective candidate limit.

### Robustness analysis

- Enable/disable robustness analysis.
- Relative weight perturbation, default plus or minus 20%.
- Simulation count, default 1,000.
- Random seed, default 123.

The perturbation control is a bounded non-negative percentage. A 20% setting perturbs a baseline weight `w` within `0.8w` to `1.2w`; every simulated weight vector is then normalized to sum to one.

## Two-Stage ToxPi Method

### Compound-level source table

The scoring core first creates one compound-level record containing:

- mean Peak Area using the existing file/sample aggregation contract;
- mean PBM `Scores` from the Pov-LRTP/PBM result;
- DF from the detection-frequency result.

Peak Area continues to enter scoring as `log10(Peak Area)` for positive values. Non-positive or unavailable Peak Area values remain missing for normalization. Existing compound-key matching and aggregation rules remain unchanged.

### Stage 1: global screening

For every available compound:

1. Normalize `log10(Peak Area)` and PBM independently across the full compound universe using the existing robust 5th-to-95th-percentile scaling and clipping to `[0, 1]`.
2. Clip DF to `[0, 1]`, preserving its existing interpretation as a bounded detection frequency.
3. Normalize the user weights to sum to one.
4. Calculate the initial ToxPi score as the weighted sum of the three normalized indicators.
5. Sort descending and select the configurable candidate limit, default Top 100.

This stage resolves the incompatible raw scales of Peak Area, PBM, and DF while preserving standardized magnitude information. It replaces the earlier proposed raw-rank aggregation method.

To make candidate selection deterministic, ties at the selection boundary are resolved by the unrounded initial score, followed by the raw compound-level Peak Area, and finally the normalized compound identifier/name in ascending order.

### Stage 2: candidate-set refinement

For the selected candidates only:

1. Return to their compound-level source values rather than reusing Stage 1 normalized columns.
2. Re-normalize `log10(Peak Area)` and PBM within the candidate set using the same robust scaling method.
3. Clip DF to `[0, 1]` under the same bounded-frequency rule.
4. Recalculate the final ToxPi score with the same normalized user weights.
5. Sort descending to produce the full refined candidate ranking.
6. Select the configurable display limit, default Top 20, for plots and concise result display.

The second normalization expands differences within the high-priority candidate set without changing which compounds entered that set.

## Output Contract

The scoring function returns a structured result with separately identifiable tables:

- full compound-level source metrics;
- Stage 1 globally normalized screening results and initial rank;
- Stage 2 candidate-set normalized data;
- full refined candidate ranking;
- final display/plot rows;
- normalized weights and run settings;
- optional robustness summary and statistics.

Existing public names should remain understandable, but the workbook must no longer conflate Stage 1 and Stage 2 data. The comprehensive workbook and one-click root/module workbooks include explicit sheets for the global screen, candidate normalization, final ranking, displayed Top N, settings, and robustness outputs. Sheet names must remain within Excel's 31-character limit.

The one-click result dashboard shows:

- the final refined ranking table;
- the default Top 20 multi-panel ToxPi radial/pie plot;
- the default Top 20 ranking bar plot;
- the robustness correlation histogram when enabled;
- the Top-N entry-frequency table when enabled.

The one-click ZIP includes PNG and PDF versions of the radial plot, ranking bar plot, and robustness plot, together with the expanded PA/PBM/DF ToxPi workbook. Preview and export artifacts are generated from the same result object.

## Robustness Analysis

Robustness analysis operates on the Stage 2 candidate set, not the entire initial universe.

For each simulation:

1. Draw each of the three weights independently from a uniform relative range around its baseline value.
2. Normalize the perturbed weights to sum to one.
3. Recalculate candidate ToxPi scores using the fixed Stage 2 normalized indicator matrix.
4. Rank candidates by the simulated scores.
5. Record Spearman correlation with the baseline refined ranking.
6. Record whether each candidate enters the configured final display Top N.

The analysis returns:

- per-compound baseline score and Top-N entry frequency;
- mean and standard deviation of Spearman correlation;
- 2.5th and 97.5th percentile correlation bounds;
- a correlation histogram annotated with the baseline settings;
- reproducibility metadata including seed, simulations, perturbation, limits, and normalized weights.

Zero baseline weights remain zero during relative perturbation. Missing indicators follow the scoring core's established missing-data policy; the same policy is used for baseline and perturbed scores.

## Architecture

### Scoring core

`src/cp_screening_workflow.py` owns:

- configuration validation;
- compound-level metric aggregation;
- robust normalization;
- Stage 1 selection;
- Stage 2 recalculation;
- deterministic sorting;
- plot-row limiting;
- robustness simulation and statistics.

The page modules do not duplicate scoring rules.

### Plot configuration

`src/r_screening_replica/schema.py` carries validated DBE and Van Krevelen axis bounds. `src/r_screening_replica/plots.py` applies those bounds when creating both PNG and PDF figures. The chemical-type donut remains unchanged.

### Workflow integration

`src/auto_query_workflow.py` carries the shared plot and ToxPi settings through the one-click workflow, generates ToxPi artifacts, and places the new tables and charts into the public workbook/ZIP contract.

`pages/0_综合筛查流程.py` and `pages/6_一键批量查询.py` collect settings, invalidate stale session results when scoring-relevant settings change, display the returned artifacts, and provide downloads.

## State and Error Handling

- Axis bounds with `max <= min` block execution with a clear field-specific message.
- All-zero ToxPi weights block scoring.
- Invalid candidate/display limits block scoring or are safely capped when only the available row count is smaller.
- Robustness analysis requires at least two candidates; otherwise the main ToxPi result remains available and the robustness section reports why it was skipped.
- A fixed random seed makes repeated runs reproducible.
- A settings signature is stored with cached workflow results. Changing an axis range, limit, weight, perturbation, iteration count, or seed invalidates affected cached plots/results and requires regeneration.
- Partial query failures continue to be recorded through the existing warnings tables; they do not silently change the configured scoring universe.

## Testing Strategy

Focused tests will verify:

1. Stage 1 normalization uses the full compound universe and selects the configured candidate count.
2. Stage 2 returns to source metrics and re-normalizes only the candidate set.
3. Final plot rows are selected from Stage 2 scores, not Stage 1 scores.
4. Candidate/display limits are configurable and safely capped for small inputs.
5. Custom weights are normalized and can change both screening and final rankings.
6. All-zero weights and invalid limits fail clearly.
7. Default 20% and custom perturbation bounds are honored.
8. Fixed seeds reproduce robustness summaries.
9. Spearman statistics and Top-N entry frequencies use the configured final display limit.
10. DBE and Van Krevelen axes use custom bounds in generated figures.
11. The comprehensive page passes the shared settings to both plotting and scoring cores.
12. The one-click workflow exposes the ranking, radial, bar, and robustness artifacts in the dashboard and ZIP.
13. Root and module workbooks contain the explicit two-stage and robustness sheets without leaking internal-only tables.
14. Existing ToxPi, comprehensive screening, local screening plot, and one-click regression tests continue to pass.

Final verification runs the focused regression tests first, followed by:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

## Success Criteria

- Users can control DBE and Van Krevelen X/Y ranges, and previews match exports.
- Peak Area cannot dominate PBM and DF merely because of its raw numeric scale.
- Default processing performs global normalization, selects Top 100, re-normalizes that candidate set, and plots the refined Top 20.
- Candidate and display limits are user-configurable.
- Peak Area, PBM, and DF weights are user-configurable and consistently applied.
- Robustness analysis defaults to plus or minus 20%, is configurable and reproducible, and reports both overall rank stability and Top-N entry frequency.
- The one-click results page and ZIP contain the requested ToxPi plots and traceable intermediate/final tables.
