# PBM/ToxPi eligibility design

## Goal

Ensure that the final PA/PBM/DF ToxPi ranking represents only compounds with a complete, successful Pov-LRTP/PBM result and usable PA and DF values.

## Scope

- Keep all input compounds visible in `ToxPi_Input` and `Excluded_or_Failed` for auditability.
- Define ToxPi eligibility as: non-empty compound name, finite positive `Peak_Area`, finite `Scores` (PBM), and finite `DF` in the inclusive range 0 to 1.
- Exclude ineligible compounds before global candidate screening, candidate-only normalization, final ranking, robustness analysis, and chart generation.
- Preserve the existing two-stage scoring, weights, Pov-LRTP calculation, PBM score formula, and exported table names.
- Add a separate `ToxPi_Excluded` table with input metrics and a clear exclusion reason. Include it in the workbook and surface it in the Streamlit page.

## Data flow

1. Build the existing ToxPi input table from PA, PBM `Scores`, and DF.
2. Validate each compound after compound-level aggregation.
3. Route eligible rows into global screening and the existing two-stage ToxPi calculation.
4. Route ineligible rows to `ToxPi_Excluded`; they receive no ToxPi score or rank and are absent from plots and robustness results.
5. Keep `Excluded_or_Failed` as the broader Pov-LRTP failure audit; `ToxPi_Excluded` records every row disqualified specifically from final ToxPi ranking.

## Error handling and tests

- A missing PBM score is explicitly labelled `PBM score missing`.
- Invalid PA or DF receives an indicator-specific exclusion reason.
- Existing valid inputs must retain the same ranking behavior.
- Regression tests must prove that a high-PA/high-DF row with missing PBM cannot enter the final ranking, and that excluded rows are auditable.
