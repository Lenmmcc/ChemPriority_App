# Per-File Results and Complete Axis Ticks Design

**Date:** 2026-07-26

**Status:** User-approved approach; written specification awaiting review

**Scope:** one-click multi-file result presentation and export for local screening, EPA CompTox, ECHA, and source-origin modules; shared PA/PBM/DF ToxPi ranking and robustness figures

## 1. Goal

Make the one-click workflow preserve the uploaded-file boundary in both the page and downloadable artifacts for:

- local screening;
- EPA CompTox;
- ECHA REACH use and ECHA GHS/C&L;
- source-origin assessment.

Only the Pov-LRTP/PBM/ToxPi module remains a cross-file combined result. At the same time, make the normalized ToxPi ranking scale visibly include `1.0` and make the robustness histogram visibly include its top frequency tick.

## 2. Confirmed Requirements

1. The selected architecture is **query once, partition by primary file**.
2. Page previews and Excel/ZIP downloads must use the same per-file grouping.
3. A compound present in two uploaded files appears in both files' result sections.
4. External services must not be queried again merely because a compound belongs to more than one file.
5. Local screening remains calculated independently for each uploaded file.
6. EPA CompTox results are presented and exported independently for each uploaded file.
7. ECHA REACH use and ECHA GHS/C&L results are presented and exported independently for each uploaded file.
8. Source-origin results are presented and exported independently for each uploaded file.
9. Identifier completion and EPI Suite retain their existing shared workflow and audit semantics.
10. Pov-LRTP/PBM/ToxPi retains the existing cross-file aggregation, two-stage normalization, eligibility, weighting, robustness, and display semantics.
11. The ToxPi ranking bar chart visibly shows `0.0`, `0.5`, and `1.0`.
12. The robustness histogram visibly shows the top integer frequency tick.

## 3. Non-Goals

- Do not change external-query source selection, retry, cache, timeout, or concurrency behavior.
- Do not execute EPA, ECHA, or source-origin queries separately for each uploaded file.
- Do not split identifier-completion or EPI Suite results by file in this change.
- Do not change the PA/PBM/DF formula, weights, normalization stages, ranking rules, or candidate/display limits.
- Do not change the content of complete evidence tables merely to shorten a page preview.
- Do not redesign the independent local-screening, ToxPi, EPI, or chemical-use pages.

## 4. Current-State Findings

### 4.1 Multi-file identity is available but lost at module presentation

`src/multi_file_screening.py` already builds:

- an input-file mapping table;
- primary-file membership rows;
- a stable chemical `identity_key`;
- per-file local-screening results;
- a combined representative compound universe used by downstream modules.

`src/auto_query_workflow.py` then queries the combined representative universe and writes one table set per module. The one-click page and ZIP exporter consequently render those tables as one combined EPA, ECHA, and source-origin result.

### 4.2 Local screening is calculated per file but flattened

The multi-file preparation layer runs the R-replica screening pipeline separately for each participating file. Its tabular outputs are concatenated with `sample_id`, while the one-click dashboard and module workbook expose the concatenation as a single view. The individual result objects and their figure paths remain available during preparation but are not carried into the final per-file presentation.

### 4.3 External query rows have a one-input-row boundary

The EPA, ECHA, ECHA GHS/C&L, and source-origin batch runners already process each normalized input row independently before concatenating row results. This provides a safe point to attach the input `identity_key` to every summary, candidate, dossier, evidence, classification, and error row without repeating a network request.

### 4.4 Figure ticks rely on automatic limits

`generate_pbm_toxpi_bar_plot()` does not set a normalized Y-axis range or fixed ticks, so data below `1.0` can produce only `0.0` and `0.5`.

`generate_pbm_toxpi_robustness_plot()` leaves the Y upper limit at Matplotlib's automatic padded value. The next locator tick can therefore fall just above the visible range and its label is omitted.

## 5. Architecture

### 5.1 Preserve a stable query identity

Use the existing `identity_key` from the multi-file preparation layer as the internal join key.

The combined query input carries `identity_key` into each selected external batch runner. Because each batch result is assembled one input row at a time, the runner decorates every returned frame for that row with:

```text
input_identity_key
```

This column is provenance, not a new chemical identifier. It must never influence an external query.

The same identity may map to one or more primary files. The primary membership table remains the authoritative mapping:

```text
input_identity_key -> primary_file(s) -> sample_id(s)
```

If an external row cannot be assigned an input identity, it remains in the module's audit/error output under an explicit unassigned bucket and generates a warning. The implementation must not guess from an ambiguous compound name.

### 5.2 Build transient per-file views

Add a focused, Streamlit-independent per-file view builder. It consumes:

- `AutoWorkflowResult`;
- `Input_File_Mappings`;
- `EPI_Primary_Membership`;
- identity-annotated module tables;
- local tables carrying `sample_id`;
- local per-file figure artifacts.

It returns an ordered mapping in original upload order:

```text
primary filename
  -> safe export directory name
  -> sample ID
  -> local tables/charts
  -> EPA tables/charts
  -> ECHA tables/charts
  -> source-origin tables/charts
```

These views are derived from the cumulative result rather than persisted as duplicate query payloads. Checkpoint recovery can rebuild them from the stored result and membership tables.

### 5.3 Partition rules

#### Local screening

Partition local tables by `sample_id`. Use the individual screening result for that sample to load:

- chemical-type distribution;
- DBE bubble plot;
- Van Krevelen plot;
- any existing file-specific local figure included by the public chart allowlist.

Cross-file DF and sample-level audit tables needed by Pov-LRTP/PBM/ToxPi remain available to the combined module and root audit workbook.

#### EPA CompTox

Partition the identity-annotated:

- summary;
- candidate evidence;
- error rows.

Then rebuild product-use, predicted-use, reported-use, pie, and reported-evidence tables and figures from each file's candidate subset. Do not filter an already aggregated combined pie table.

#### ECHA

Partition the identity-annotated:

- REACH summary;
- reported-use candidates;
- dossiers;
- REACH errors;
- GHS/C&L summary;
- classifications;
- GHS/C&L errors.

Rebuild each file's reported-use distribution and evidence charts from that file's candidate universe.

#### Source origin

Partition the identity-annotated:

- summary;
- evidence;
- errors.

Rebuild the source-origin distribution for each file from the file-specific summary. EPA and ECHA evidence may be reused internally from their single combined query pass, but the displayed source-origin conclusion must be attached only to the primary files containing that identity.

#### Pov-LRTP/PBM/ToxPi

Do not partition. Continue to use:

- all participating files for DF;
- the existing representative chemical universe;
- candidate-only second-stage normalization;
- the current combined ranking and robustness analysis.

## 6. Page Presentation

Keep the existing top-level module tabs.

Inside the following tabs, create one nested tab per original uploaded filename, in upload order:

- local screening;
- EPA CompTox;
- ECHA;
- source origin.

Each nested tab shows only that file's public result tables and charts. Audit tables remain collapsed by default. The original filename is the visible label; the safe export name is not shown as a replacement.

Identifier completion and EPI Suite remain single shared tabs. Pov-LRTP/PBM/ToxPi remains one combined tab and explicitly captions that it aggregates all participating primary files.

If one file has no result for a selected module, keep its file tab and show a clear no-result message rather than silently removing the file.

## 7. Export Layout

### 7.1 Full ZIP

Use stable module folders with one safe subfolder per primary file:

```text
01_Local_Screening/<safe-file-stem>/Local_Screening_Results.xlsx
01_Local_Screening/<safe-file-stem>/figures/*.png
01_Local_Screening/<safe-file-stem>/figures/*.pdf

04_EPA_CompTox/<safe-file-stem>/EPA_CompTox_Results.xlsx
04_EPA_CompTox/<safe-file-stem>/figures/*.png
04_EPA_CompTox/<safe-file-stem>/figures/*.pdf

05_ECHA/<safe-file-stem>/ECHA_Results.xlsx
05_ECHA/<safe-file-stem>/figures/*.png
05_ECHA/<safe-file-stem>/figures/*.pdf

06_Source_Origin/<safe-file-stem>/Source_Origin_Results.xlsx
06_Source_Origin/<safe-file-stem>/figures/*.png
06_Source_Origin/<safe-file-stem>/figures/*.pdf
```

Identifier completion and EPI Suite retain their existing combined module files. Pov-LRTP/PBM/ToxPi retains its single combined module file and figure directory.

Safe directory names must be deterministic, Windows-compatible, and collision-resistant. If two filenames sanitize to the same stem, append a short deterministic suffix. `Input_File_Mappings` preserves the exact original filename, sample ID, and safe export name.

### 7.2 Immediate module downloads

For a per-file module, the immediate download is a ZIP containing one subfolder per primary file and that file's workbook/figures. A chartless single-file module may remain XLSX only where the current download contract already does so.

### 7.3 Root workbook and audit data

The complete root workbook may retain consolidated audit tables, but user-facing module workbooks must be per-file. Consolidated EPA/ECHA/source tables are internal dependencies and must not be exposed as the only public representation.

Checkpoint-generated partial downloads use the same per-file module layout for every completed affected module.

## 8. Axis Behavior

### 8.1 ToxPi ranking

The ToxPi score is normalized to `[0, 1]`. Set:

```text
Y limits: 0.0 to 1.0
Y ticks: 0.0, 0.5, 1.0
```

Use an explicit formatter with one decimal place so `1.0` is not rendered as `1`.

### 8.2 Robustness histogram

After drawing the histogram:

1. start the Y axis at zero;
2. use an integer major-tick locator;
3. calculate the locator ticks for the current histogram height;
4. raise the Y upper limit to the first locator tick at or above the automatically padded upper limit;
5. explicitly keep that aligned top tick in the visible tick sequence.

The top tick must equal the final Y upper limit. This avoids clipping the tallest bar while guaranteeing a visible top frequency label.

## 9. Failure and Audit Handling

- A file with no participating local area column retains a per-file warning and an empty local result view.
- A module failure for one identity is repeated only into the file views that contain that identity.
- An identity-assignment failure goes to an unassigned audit section and a workflow warning; it is not copied into every file.
- A chart-generation failure does not remove the corresponding table or workbook.
- Full evidence tables remain complete in Excel/ZIP even when a page preview is abbreviated.
- Existing checkpoint errors, external-query errors, and retry behavior remain unchanged.

## 10. Testing Strategy

Implementation follows test-first red/green cycles.

### 10.1 Per-file membership and query behavior

Use two primary files with:

- one compound unique to file A;
- one compound unique to file B;
- one compound shared by A and B.

Verify:

- each external batch receives each unique identity once;
- the shared identity appears in both file views;
- A-only and B-only identities never leak into the other file;
- ambiguous/unassigned rows are audited rather than guessed.

### 10.2 Local results

Verify independent file tables and local figure artifacts are preserved and exposed under the correct filename. Verify combined DF/sample audit inputs still feed the single Pov-LRTP/PBM/ToxPi calculation.

### 10.3 Derived tables and charts

For EPA, ECHA, and source origin, verify each per-file pie/evidence table and chart is rebuilt from that file's rows. A per-file distribution must not inherit counts from the other file.

### 10.4 Page behavior

Use Streamlit AppTest to verify:

- affected module tabs contain one nested tab per uploaded file;
- identifier and EPI remain shared;
- Pov-LRTP/PBM/ToxPi remains a single combined tab;
- empty file/module combinations show an explicit message.

### 10.5 Export behavior

Inspect actual XLSX and ZIP payloads:

- per-file folders exist in upload order;
- workbook sheets contain only the expected file's rows;
- a shared compound appears in both appropriate workbooks;
- exact original filenames remain in the mapping audit;
- partial/checkpoint downloads use the same layout;
- Pov-LRTP/PBM/ToxPi appears only once.

### 10.6 Axis ticks

Verify the ToxPi bar axis has limits `(0.0, 1.0)`, tick locations `(0.0, 0.5, 1.0)`, and labels `0.0`, `0.5`, `1.0`.

Verify the robustness histogram:

- starts at zero;
- uses integer ticks;
- has its top tick equal to the Y upper limit;
- retains all histogram bars inside the visible bounds.

### 10.7 Final verification

Run:

- targeted multi-file, auto-query, plotting, export, checkpoint, and AppTest regressions;
- `python -m unittest discover -s tests -v`;
- `python -m compileall app.py pages src`;
- `git diff --check`.

## 11. Acceptance Criteria

The change is complete when:

1. local screening, EPA CompTox, ECHA, and source-origin page results are separated by uploaded file;
2. the same four modules are separated by file in immediate, partial, recovered, and full downloads;
3. a compound shared across files appears in each applicable file result;
4. external queries still execute once per unique combined identity;
5. ambiguous identity assignment is auditable and never guessed;
6. identifier completion and EPI Suite retain their existing shared behavior;
7. Pov-LRTP/PBM/ToxPi remains one combined cross-file calculation and presentation;
8. the ToxPi ranking plot visibly labels `0.0`, `0.5`, and `1.0`;
9. the robustness histogram visibly labels its aligned top integer frequency;
10. all targeted tests, the full suite, compile check, and diff check pass.
