# Auto-query reliability, module downloads, and pie-chart layout design

## Goal

Make network-backed ChemPriority workflows resilient to transient failures, ensure every per-module download includes its generated figures, prevent category legends from covering donut charts, and recover from the EPI Web Suite CAS-record `null` failure without hiding genuine invalid-input errors.

## Scope and boundaries

- Apply multi-round retry behavior to the shared network batch paths used by identifier completion, EPI Web Suite, EPA CompTox, ECHA use, ECHA GHS/C&L, and source-origin lookup.
- Keep the existing cache and concurrency controls. Successful calls remain reusable; only failed rows are candidates for a later round.
- Use three total rounds: the initial round plus at most two failed-row rounds.
- Retry only transient failures such as timeouts, connection errors, HTTP 408/425/429, and HTTP 5xx responses. Do not repeat deterministic validation failures or ordinary HTTP 4xx responses.
- Treat EPI Web Suite `HTTP 400: could not parse 'null'` as a special CAS-record failure only when a non-empty submitted SMILES is available. Retry that row immediately without CAS. Other HTTP 400 errors remain failures.
- Preserve existing public table and chart names, checkpoint compatibility, full-workflow ZIP layout, and result ordering.

## Considered approaches

### Module downloads

1. **Recommended: module ZIP when figures exist.** Package the module workbook plus each available PNG/PDF figure. Keep XLSX downloads for chartless modules. This preserves editable data and original-resolution figures without changing workbook rendering.
2. Embed bitmap figures into Excel worksheets. This makes a single workbook but loses the PDF originals, increases workbook size, and makes checkpoint/output compatibility more fragile.
3. Add separate download buttons for every workbook and figure. This is simple internally but creates a crowded interface and requires many clicks.

### Failed-row retry

1. **Recommended: shared batch-runner rounds with module-specific outcome classification.** Preserve input order and concurrency while rerunning only transiently failed rows. Successful calls are reused through the existing cache.
2. Repeat each HTTP request internally. This does not satisfy the requested end-of-round retry behavior and cannot recover failures already converted into warning rows.
3. Rerun whole workflow modules from page 6. This duplicates successful work, complicates output merging, and would not protect the same batch functions when used from other pages.

### Crowded donut charts

1. **Recommended: Top 11 categories plus `Others`, with an adjusted legend layout.** The plot remains readable and total-preserving, while the exported audit tables retain every compound-level classification.
2. Increase canvas size without grouping categories. This still fails for sufficiently long labels or many singleton categories.
3. Remove the legend and show a separate table only. This avoids overlap but makes downloaded figures hard to interpret by themselves.

## Components and data flow

### Shared retry engine

`src/batch_runner.py` will run the initial ordered batch, classify each `BatchResult` with a caller-supplied predicate, pause briefly, and run only eligible indices again. The newest result replaces the prior result at the same original index. Event payloads will include attempt information without removing existing fields.

Each network batch adapter will inspect its one-row warning/error frame and request another round only when the recorded error is transient. A shared text/exception classifier will keep the status policy consistent across adapters. Once a later round succeeds, its clean result replaces the earlier failed row, so stale failure rows do not leak into exports.

### EPI CAS fallback

`src/episuite_io.py` will validate/clean the submitted SMILES before calling the API. When the first call includes CAS and returns the exact EPI parse-null failure, the same SMILES is submitted without CAS and the result records a query note explaining the fallback. Existing 404 CAS-not-found fallback remains. Missing or literal-null SMILES are rejected locally rather than sent to the service.

### Module packages

`src/auto_query_workflow.py` will map a completed module to its existing chart allowlist. If one or more charts exist, it will build a module ZIP with:

- the existing module XLSX at the ZIP root;
- `figures/<chart-name>.png`;
- `figures/<chart-name>.pdf`.

`pages/6_一键批量查询.py` will use the package metadata to choose `.zip`/`application/zip` or the existing `.xlsx` MIME type. Partial ZIP exports will also include all currently generated charts, including charts recovered from checkpoints.

### Pie-chart grouping and layout

`src/use_rose_plot.py` will use one shared maximum of 12 displayed legend entries for high-cardinality classification donuts: the 11 largest categories and a total-preserving `Others` entry. Source-origin charts with four fixed categories remain unchanged in practice. The figure will reserve a dedicated legend region and use wrapped labels or multiple legend columns as needed. The underlying `EPA_PUC_Pie_Data`, reported-use tables, and other workbook sheets remain ungrouped audit data.

## Error handling

- Exhausted transient failures remain in their existing error/warning tables with the final error message.
- Retryable and non-retryable decisions are deterministic and testable from exception/status text.
- A failed module-package build will surface through the existing Streamlit warning path and must not discard the newest result, workbook, or charts.
- EPI CAS fallback is intentionally narrow: it requires a CAS-bearing request, a valid SMILES, and the parse-null signature.

## Verification

- Unit-test ordered three-round behavior, preservation of original ordering, replacement of stale failures, and non-retry of HTTP 400 validation errors.
- Add adapter regressions covering all six network batch entry points and their returned warning/error shapes.
- Add an EPI regression proving CAS + SMILES parse-null falls back to SMILES-only, while unrelated HTTP 400 errors do not.
- Add module-package tests that parse real ZIP payloads and confirm XLSX, PNG, and PDF members for chart-bearing modules; chartless modules remain XLSX.
- Add Streamlit behavior coverage for the per-module download filename, MIME type, payload, checkpoint reload, and no-rerun behavior.
- Add donut regressions confirming no more than 12 legend entries, correct `Others` aggregation, and preservation of the total compound count.
- Run targeted tests first, then `python -m unittest discover -s tests -v`, `python -m compileall app.py pages src`, and `git diff --check`.

## Out of scope

- Retrying invalid identifiers or malformed structures indefinitely.
- Changing API providers, endpoint URLs, cache storage, or concurrency defaults.
- Removing detailed low-frequency categories from exported tables.
- Embedding figures inside Excel worksheets.
