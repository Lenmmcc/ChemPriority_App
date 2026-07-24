# Multi-File EPI Supplement and Recovery Design

**Date:** 2026-07-24

**Status:** User-approved design

**Scope:** `pages/3_EPISuite环境归趋.py`, `pages/6_一键批量查询.py`, the shared multi-file screening path, EPI parsing/merging, query retry/cache, and checkpoint recovery

## 1. Goal

Allow the one-click batch-query workflow to accept multiple primary Excel workbooks while preserving the existing multi-sample Pov-LRTP/PBM/ToxPi semantics. When EPI Suite is selected directly or required by Pov-LRTP/PBM/ToxPi, users may upload one or more supplementary EPI Excel workbooks, inspect and correct filename associations, validate completeness, skip redundant EPI requests, query only missing compounds, and retry only transient EPI failures.

The independent EPI page and the one-click workflow must share successful public API responses through the existing local query cache and share validated EPI tables within the current Streamlit session. User-uploaded EPI data must not enter the global SQLite query cache.

## 2. Confirmed Requirements

1. The one-click workflow accepts multiple primary Excel workbooks.
2. Each primary workbook retains an independent file/sample identity and independent column mapping.
3. Supplementary EPI workbooks accept multiple Excel files.
4. Supplement-to-primary association uses filenames only. Chemical overlap must not influence the suggested association.
5. Users may correct the primary file, result sheet, identifier columns, endpoint columns, and supplementary-file priority.
6. Row matching uses CAS, then SMILES, then compound name.
7. User-uploaded non-null values take precedence over session-pool, local-cache, and newly queried values.
8. Conflicting non-null uploaded values are audited and resolved by user-specified supplementary-file priority.
9. A complete upload skips EPI network calls. A partial upload queries only unmatched, failed, or downstream-core-incomplete compounds.
10. Transient network errors receive up to three automatic attempts with increasing delay and jitter.
11. The completed page exposes a button that retries only the remaining EPI failures and then reruns only EPI-dependent downstream calculations.
12. Validated results from the independent EPI page are automatically reusable by the one-click workflow in the same browser session.
13. The global SQLite cache remains shared across pages for successful public API responses only.
14. Cache diagnostics and expired-entry cleanup are exposed in the UI.
15. Existing checkpoint recovery remains available and includes all multi-file and EPI-supplement settings required to interpret restored results.

## 3. Non-Goals

- Do not change the mathematical definition of Pov-LRTP, PBM, ToxPi weights, two-stage normalization, robustness analysis, or display limits.
- Do not infer supplementary-file association from chemical contents.
- Do not persist user-uploaded EPI values in the global query cache.
- Do not reuse a CAS-less EPI API response as if it contained the experimental/database values available from a CAS-aware request.
- Do not require every optional EPI endpoint to be non-null before allowing an EPI-only workflow to continue.
- Do not redesign unrelated EPA, ECHA, source-origin, chart, or export behavior.

## 4. Current-State Findings

### 4.1 One-click input

`pages/6_一键批量查询.py` currently accepts one primary workbook and builds one `AutoWorkflowMapping`. `src/auto_query_workflow.py` consequently receives one DataFrame and one mapping. This path does not preserve multiple workbooks as separate samples.

### 4.2 Existing multi-file screening semantics

`pages/0_综合筛查流程.py` and `src/cp_screening_workflow.py` already implement the intended multi-file behavior:

- each uploaded workbook is a distinct sample;
- selected Group Area columns are averaged inside each file and compound;
- DF uses detected participating files divided by the participating-file count;
- sample-level peak-area rows retain `source_sample_id`;
- Pov-LRTP is calculated for the unique compound universe;
- the compound-level Pov/PBM result is merged back to compound-by-sample rows before ToxPi aggregation;
- files with no selected participating peak-area columns do not silently inflate the DF denominator.

The one-click workflow must reuse these semantics rather than concatenate raw rows into a single sample.

### 4.3 Existing EPI upload parsing

The independent EPI page already accepts multiple external result files, but the parser reads the first Excel sheet by default. A ChemPriority `EPISuite_Fate_Report.xlsx` starts with `Validated_Input`, followed by `Core_Summary`, so the current parser cannot reliably re-import its own download. The one-click module workbook instead exposes `EPI_Results`.

### 4.4 Retry classification

The EPI batch runner already requests up to three attempts for errors classified as transient. The exact user-reported strings containing `[WinError 10054]`, Chinese “remote host forcibly closed” wording, `UNEXPECTED_EOF_WHILE_READING`, and “EOF occurred in violation of protocol” are not currently recognized by the transient classifier.

### 4.5 Cache behavior

Both EPI pages use `run_epi_web_batch` and `call_epi_web_api`, so successful API responses are already shared through `.cache/chempriority_queries.sqlite3` when the API URL, SMILES, and optional CAS parameters produce the same cache key. Parsed user uploads are page-local session results and are not currently reused by the one-click page.

## 5. Architecture

Use a shared multi-file preparation layer and a separate EPI supplementation layer. Do not add more parsing and aggregation logic directly to the already-large Streamlit pages.

### 5.1 Shared multi-file preparation

Extract the pure multi-file parsing, mapping defaults, normalization, sample-level aggregation, and representative-compound construction currently embedded in `pages/0_综合筛查流程.py` into a focused shared module. Both the comprehensive page and the one-click page will call that module.

The shared preparation result contains two coordinated representations:

1. **Sample-level representation**
   - `source_file`
   - `source_sample_id`
   - original row number
   - compound identity
   - mapped Group Area columns
   - file-level `Group_Area_Mean`
   - DF detection state

2. **Compound-level representation**
   - one representative row per normalized compound key;
   - identifiers and formula candidates;
   - provenance listing every contributing primary file;
   - compound-level inputs for identifier completion, EPI, and Pov-LRTP.

The auto-query workflow consumes both representations. Local figures and DF use the sample-level representation. Identifier completion, EPI, and Pov-LRTP use the compound-level representation. PBM values are merged back by compound key before ToxPi consumes the sample-level rows.

### 5.2 EPI supplement layer

Add a focused EPI supplementation module rather than extending all behavior inside `src/episuite_io.py`.

It owns:

- workbook inspection;
- recognized result-sheet detection;
- supplementary-to-primary filename mapping records;
- identifier and endpoint column mapping;
- identity normalization;
- match audit;
- conflict detection;
- field-level provenance;
- module-aware completeness classification;
- construction of the minimal network-query queue;
- deterministic merge of upload, session-pool, cache/API, and retry results.

The module accepts bytes and mapping records rather than Streamlit upload objects, so it is testable without a page runtime.

### 5.3 Same-session EPI result pool

Add Streamlit-independent helpers that read and write a serializable EPI result pool in a supplied state mapping.

Each pool entry records:

- normalized CAS, SMILES, and compound-name keys;
- standardized EPI values;
- field-level provenance;
- contributor page/run ID;
- source type (`api`, `uploaded`, or `merged`);
- validation timestamp;
- completeness state.

The independent EPI page contributes validated results after either automatic prediction or uploaded-result parsing. The one-click page reads matching pool entries before considering its own supplements or network queries.

The EPI page's existing clear-current-data action removes entries contributed by that page/run. The one-click page's clear-current-data action removes only its inputs, results, and checkpoint. A separate explicit action clears the current session's EPI pool.

The pool is not written to the global SQLite query cache. A disk checkpoint may store the pool subset actually used by that run so a restored result retains provenance without exposing unrelated session entries.

## 6. User Interface

### 6.1 Primary workbooks

The one-click page changes its primary uploader to `accept_multiple_files=True`.

For each primary file, show a mapping tab containing:

- compound name;
- formula;
- default peak area;
- participating Group Area columns;
- optional MOL;
- optional SMILES;
- optional CAS.

The filename stem is the default `sample_id`, while the full filename is retained for association and audit. Primary filenames must be unique. Duplicate full filenames block execution and ask the user to rename the files because filename-only supplement association would otherwise be ambiguous.

### 6.2 Conditional EPI supplement controls

When EPI is selected or Pov-LRTP/PBM/ToxPi causes EPI to be required, the run-settings area displays a multi-file EPI Excel uploader.

For each supplement, show:

- supplementary filename;
- associated primary filename;
- selected result sheet;
- identifier-column mapping;
- endpoint-column mapping when auto-detection is incomplete;
- priority among supplements associated with the same primary file.

Filename-only suggestion rules may normalize case and remove known output suffixes such as `_EPI`, `_EPISuite`, or `_EPISuite_Fate_Report`. They must not open the workbook or compare compounds to choose a primary file. If filename normalization produces zero or multiple candidates, the user must select the primary filename.

One supplement maps to exactly one primary file. One primary file may receive multiple supplements.

### 6.3 Completeness preview

Before execution, show:

- unique compound count;
- upload/session-pool matched count;
- EPI-only valid-row count;
- Pov-LRTP/PBM/ToxPi core-complete count;
- conflict count;
- unmatched count;
- planned network-query count.

If the planned query count is zero, the primary action states that uploaded/shared results will be used directly. Otherwise it states how many compounds will be queried.

### 6.4 Post-run actions

After a run with remaining retryable EPI failures, show “仅重试 EPI 失败项”. The action must:

- operate only on remaining retryable or core-incomplete EPI rows;
- preserve uploaded and successful values;
- avoid rerunning identifier completion and unrelated network modules;
- rerun Pov-LRTP/PBM/ToxPi only when those selected outputs depend on changed EPI rows;
- update tables, charts, module downloads, full ZIP, and checkpoint.

## 7. Workbook Parsing and Mapping

### 7.1 Recognized sheets

For Excel supplements, inspect sheet names without loading arbitrary first-sheet data into the result path.

Selection precedence:

1. `Core_Summary`
2. `EPI_Results`
3. a user-selected sheet

`Core_Summary` supports re-importing `EPISuite_Fate_Report.xlsx`. `EPI_Results` supports re-importing the one-click EPI module workbook.

### 7.2 Column mapping

Reuse existing EPI aliases where possible, but expose manual correction for:

- compound name;
- CAS;
- SMILES;
- every recognized endpoint needed by the selected workflow.

Unknown extra columns remain available in raw/audit output but do not silently become model inputs.

### 7.3 Identity normalization and precedence

For every primary and supplementary row, derive:

- normalized CAS key;
- normalized/canonicalizable SMILES key;
- whitespace/case-normalized compound-name key.

Match in this order:

1. CAS
2. SMILES
3. compound name

Once a higher-priority identifier uniquely matches a row, lower-priority keys cannot create a second match. If identifiers disagree, point to multiple primary compounds, or identify conflicting targets, quarantine the row in `EPI_Match_Audit` and do not merge it automatically.

## 8. Merge and Provenance Rules

The source order is:

1. user-uploaded supplementary result;
2. current-session validated EPI pool;
3. existing successful API cache or new API result;
4. manual retry result.

This is a field-level merge, not a whole-row replacement:

- a non-null uploaded value is retained;
- lower-priority sources fill null fields only;
- a lower-priority source never replaces a non-null uploaded value;
- multiple uploaded non-null values are resolved by user-specified supplement priority;
- every unequal non-null candidate is written to `EPI_Conflict_Audit`;
- adopted values retain source file, sheet, source row, source type, and priority.

Network-query output is merged only for the compound it queried. A successful retry updates missing or failed network-derived fields but does not replace uploaded values.

## 9. Completeness Rules

### 9.1 EPI-only selection

A compound is complete enough to skip a redundant query when:

- it has a unique matched EPI result row;
- the row is not explicitly failed;
- at least one recognized EPI endpoint is present.

Missing optional endpoints remain visible in `EPI_Completeness` but do not force repeated requests because some models legitimately return no value outside their applicability domain.

### 9.2 Pov-LRTP/PBM/ToxPi selection

The core model inputs are:

- molecular weight, allowing the existing identifier/PubChem fallback;
- Henry's law constant;
- Log Kow;
- Level III air half-life;
- Level III water half-life;
- Level III soil half-life;
- Log BAF.

A compound enters the Pov-LRTP model only when these inputs are finite and valid for the downstream model. Uploaded/session values are checked after identifier completion so molecular-weight fallback is considered before a redundant EPI request is scheduled.

The network queue contains only:

- unmatched compounds;
- explicitly failed uploaded/session rows;
- compounds missing a selected-workflow core input that EPI may supply.

After all retries, unresolved compounds are exported with reasons and excluded from dependent ranking without blocking complete compounds.

## 10. Retry and Failure Handling

Extend transient classification to recognize:

- `[WinError 10054]`;
- remote-host-forcibly-closed wording in English and Chinese;
- `SSLEOFError`;
- `UNEXPECTED_EOF_WHILE_READING`;
- `EOF occurred in violation of protocol`;
- existing timeouts, connection resets, HTTP 408/425/429, and HTTP 5xx conditions.

Keep HTTP 400/404, invalid CAS, invalid structures, and missing SMILES non-retryable unless an existing explicit CAS-to-SMILES fallback applies.

Automatic retries:

- maximum three attempts total;
- increasing delay approximately 1 second before attempt two and 2 seconds before attempt three;
- small random jitter on each retry delay;
- one progress completion per compound, not per attempt;
- one audit row per attempt.

The final error table distinguishes:

- transient exhausted;
- deterministic input failure;
- unmatched supplement row;
- upload conflict;
- incomplete core model input;
- checkpoint/storage failure.

## 11. Cache Design

### 11.1 Global public API cache

Keep the existing SQLite query cache and exact request-key semantics. Both pages continue to reuse successful EPI API responses when URL, SMILES, and CAS inputs match. Failed requests and user uploads are not stored as successful public API responses.

Add read-only diagnostics:

- database size;
- total rows;
- EPI rows;
- oldest and newest timestamps;
- expired row count.

Add:

- “清理过期记录”, which deletes rows older than the configured TTL and reports reclaimed logical rows;
- “清空全部查询缓存”, guarded by explicit confirmation.

Database compaction must not corrupt or block active queries. If physical compaction cannot obtain the database lock promptly, retain the committed row deletion and report that physical file compaction was deferred.

### 11.2 Query-key compatibility

Do not merge CAS-aware and CAS-less cache entries. SMILES canonicalization may be used only if it is applied consistently before both cache lookup and API request and does not change the submitted structure. This design does not require changing existing cache keys.

## 12. Checkpoint and State Recovery

The combined primary-input signature is order-sensitive and includes every primary filename and byte payload. Supplement signatures similarly include filenames and bytes.

The settings signature includes:

- per-primary column mappings;
- supplement-to-primary filename mappings;
- selected sheets and columns;
- supplement priority;
- selected modules;
- completeness mode;
- retry settings;
- existing screening and ToxPi settings.

Checkpoint artifacts retain:

- standardized sample-level inputs;
- representative compound table;
- file and column mappings;
- the run-specific EPI pool subset;
- EPI completeness, provenance, match, conflict, and attempt audits;
- partial module results and downloads;
- remaining retry targets.

Changing files or mappings clears incompatible session results and detaches the current page from the old checkpoint. The old disk checkpoint remains subject to its existing TTL or explicit deletion.

## 13. Exports

Retain:

- `EPI_Results`
- `EPI_Raw_Results`
- `EPI_Errors`

Add:

- `Input_File_Mappings`
- `EPI_Completeness`
- `EPI_Source_Provenance`
- `EPI_Match_Audit`
- `EPI_Conflict_Audit`
- `EPI_Query_Attempts`

These tables appear in the EPI module workbook, the complete workbook, partial/full ZIP exports where applicable, and recovered checkpoints. Full raw evidence remains exportable even when the page preview is abbreviated.

## 14. Testing Strategy

Implementation follows test-first red/green cycles.

### 14.1 Parsing and round-trip tests

- Re-import a generated `EPISuite_Fate_Report.xlsx` through `Core_Summary`.
- Re-import a generated one-click EPI module workbook through `EPI_Results`.
- Prove first-sheet `Validated_Input` is not silently treated as the result sheet.
- Verify manual sheet/column mappings.

### 14.2 Multi-file semantics

- Multiple primary files remain separate samples.
- Per-file Group Area means precede cross-file calculations.
- DF numerator and denominator use participating files.
- Pov-LRTP runs once per unique compound.
- PBM values return to compound-by-sample rows.
- Existing two-stage ToxPi normalization, eligibility, robustness, and display limits remain unchanged.

### 14.3 Association, match, and merge

- Filename-only association never inspects chemical overlap.
- Duplicate primary filenames block execution.
- CAS wins over SMILES and name; SMILES wins over name.
- Identifier disagreement is quarantined.
- Uploaded values beat pool and API values.
- Lower-priority sources fill nulls only.
- Conflicts record all candidates and the adopted source.

### 14.4 Completeness and network scheduling

- A complete upload causes zero EPI API calls.
- Partial uploads query only missing/core-incomplete compounds.
- EPI-only optional missing endpoints do not cause endless requests.
- Pov-LRTP mode requires the confirmed core inputs.
- Unresolved compounds are excluded without blocking complete compounds.

### 14.5 Cross-page reuse

- Automatic EPI results produced on the independent page appear in the same-session pool.
- Parsed uploaded results also appear after validation.
- The one-click page consumes matching pool entries without a network call.
- Clearing the EPI page removes its contributor entries.
- Clearing one-click state does not erase the shared pool.

### 14.6 Retry behavior

- Exact WinError 10054 and SSL EOF messages are retryable.
- HTTP 400/404 and invalid inputs are not retried.
- Attempt delays increase and progress does not overcount.
- The manual retry action targets only remaining EPI failures.
- Changed EPI rows rerun only EPI-dependent downstream stages.

### 14.7 Cache and checkpoint behavior

- Successful cache values are reused across both pages.
- Corrupt and expired values are ignored.
- Expired-entry cleanup preserves live entries.
- Concurrent reads/writes remain valid.
- Mapping and supplement settings survive checkpoint recovery.
- A settings mismatch does not display stale results under current controls.
- Downloads do not rerun the workflow.

### 14.8 Final verification

Run targeted tests first, then:

- `python -m unittest discover -s tests -v`
- `python -m compileall app.py pages src`
- `git diff --check`

App-level behavior tests must inspect actual XLSX/ZIP payloads and recovered Streamlit state rather than relying only on source-string assertions.

## 15. Acceptance Criteria

The design is complete when:

1. the one-click page accepts and independently maps multiple primary Excel files;
2. the original multi-file DF/PA/Pov/PBM/ToxPi semantics are preserved by regression tests;
3. multiple EPI supplement workbooks can be associated by filename and mapped by the user;
4. both ChemPriority EPI download formats round-trip correctly;
5. complete uploads and same-session pool entries skip redundant API calls;
6. partial data queries only unresolved compounds;
7. uploaded values are never silently overwritten;
8. exact WinError 10054 and SSL EOF failures retry automatically;
9. remaining EPI failures can be retried without rerunning unrelated modules;
10. query-cache diagnostics and expired cleanup work without invalidating live data;
11. checkpoint recovery restores mappings, provenance, partial results, and retry targets;
12. all targeted, full-suite, compile, and diff checks pass.
