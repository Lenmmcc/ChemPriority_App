# Final Branch Review Fixes Report

Date: 2026-07-15

Base head: `5a7f93a0cf3355405f3a08e4fa1b4483e3be43d3`

Scope: the three Important findings in `final-review-fixes-brief.md` only. The two Minor findings were intentionally not changed.

## Important 1: immutable one-click compound universe

### RED

Command:

```text
python -m unittest \
  tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_identifier_exception_preserves_original_compound_universe \
  tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_partial_identifier_completion_enriches_without_dropping_original_rows \
  tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_selected_use_module_exceptions_create_full_universe_audit_tables_only -v
```

Expected failures observed:

- Identifier exception: downstream query input contained `0` rows instead of the original `3` compounds.
- Partial identifier completion: downstream query input contained only `Compound B` (`1/3` rows) instead of all three original compounds.
- Selected EPA/ECHA/source whole-step exceptions: `Product_Use_Categories` and the other public split/audit tables were absent.

### GREEN and behavior

- The immutable compound universe is built from the deduplicated valid original `identifier_input`.
- Identifier output enriches matching original rows by normalized compound key; it cannot remove original rows or add unrelated rows.
- An identifier exception still sends all three original compounds to selected downstream modules.
- A one-row partial completion enriches the matching row while preserving all `3` downstream rows.
- On selected whole-step exceptions:
  - EPA split tables exist; `EPA_Predicted_Pie_Data` and `EPA_Reported_Pie_Data` each contain `3` rows, `3` unique `compound_key` values, all classified `Others`.
  - `ECHA_Uses_Reported` and `ECHA_Reported_Pie_Data` each contain `3` rows, `3` unique keys, all classified `Others`.
  - `Source_Origin_Pie_Data` contains `3` rows, `3` unique keys, all classified `Unknown`.
- A source-only exception creates the source audit table but leaves EPA and ECHA audit tables absent.

The three focused regressions passed after the fix.

## Important 2: deterministic duplicate source-origin aggregation

### RED

Command:

```text
python -m unittest tests.test_use_rose_plot.UseRosePlotTests.test_source_origin_duplicate_rows_aggregate_presence_in_both_orders -v
```

Expected failures observed:

- Anthropogenic row followed by natural row classified the compound as `Natural`.
- Natural row followed by anthropogenic row classified the compound as `Anthropogenic`.

This reproduced last-row-wins behavior and row-order dependence.

### GREEN and behavior

Duplicate summary rows are grouped by normalized compound key. Anthropogenic and natural axes aggregate as binary presence (`max(value > 0)`), so the compound appears once, has an audit evidence count of `2`, and classifies as `Both` in either input order. The focused regression passed after the fix.

## Important 3: positive public export allowlists

### RED

Command:

```text
python -m unittest \
  tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_root_and_module_workbooks_use_exact_public_table_allowlists \
  tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_chart_map_and_zip_use_exact_chart_allowlists -v
```

Expected failures observed:

- Root workbook leaked `Unknown_External_Table`, obsolete `ECHA_Use_Rose_Plot`, and arbitrary `EPA_Arbitrary_Extra` / `ECHA_Arbitrary_Extra` tables.
- Chart map inherited six injected stale/unknown chart keys, including `ECHA_Use_Rose_Plot` and broad-prefix extras.

### GREEN and allowlist completeness self-check

- Public tables are a positive set derived from the seven explicit module table contracts, plus the legitimate root-only `Identifier_Input`, `EPI_Input`, and `Warnings` tables.
- Self-check count: `41` allowed public table names. The exact-set regression injects all 41 legitimate outputs and confirms all are preserved in the root workbook and their intended module workbooks.
- Public charts are exactly `10` keys: the existing three local-screening charts plus the approved seven use/source charts.
- The exact-set regression injects all 10 legitimate chart keys and six stale/unknown keys. The chart map and modular ZIP preserve exactly the 10 legitimate keys.
- ZIP module routing now uses exact allowed chart keys, not broad prefixes.
- `CompTox_Candidates`, `ECHA_Use_Candidates`, obsolete `ECHA_Use_Rose_Plot`, stale mixed names, and arbitrary external names remain absent from public workbook/chart/ZIP surfaces.

## Verification

- Focused modules:
  - `python -m unittest tests.test_auto_query_workflow tests.test_use_rose_plot tests.test_source_origin -v`
  - Result: `68` tests passed in `11.274s`.
- Full repository:
  - `python -m unittest discover -s tests -v`
  - Result: `211` tests passed in `18.743s`.
- Compilation:
  - `python -m compileall app.py pages src`
  - Result: success.
- Diff hygiene:
  - `git diff --check`
  - Result: success; only line-ending conversion warnings were emitted by Git on this Windows worktree.

## Files changed

- `src/auto_query_workflow.py`
- `src/use_rose_plot.py`
- `tests/test_auto_query_workflow.py`
- `tests/test_use_rose_plot.py`
- `.superpowers/sdd/final-review-fixes-report.md`

## Remaining concerns

- The exception-path EPA detail split tables are intentionally present but empty; the two EPA pie audit tables carry the full-universe `Others` classification required by the brief.
- `ECHA_Uses_Reported` uses a full-universe classification-shaped audit table only when the selected ECHA whole step throws; successful runs preserve the existing detailed candidate schema.
- Source-origin `evidence_count` in the pie audit is presence-based (`0`, `1`, or `2`) rather than a sum of duplicate evidence counts, preventing duplicate-row double counting as requested.
- No Minor review items were changed.
