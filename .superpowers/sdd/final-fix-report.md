# Auto-query checkpoint final fix report

## Scope and baseline

- Baseline: `283d508`
- Design: `docs/superpowers/specs/2026-07-16-auto-query-partial-checkpoint-design.md`
- Production files changed:
  - `src/auto_query_checkpoint.py`
  - `src/auto_query_workflow.py`
  - `pages/6_一键批量查询.py`
- Test files changed:
  - `tests/test_auto_query_checkpoint.py`
  - `tests/test_auto_query_workflow.py`

## Important 1: checkpoint artifact amplification

### RED

`test_repeated_saves_reuse_unchanged_content_addressed_artifacts` saved the same checkpoint four times. The old revision-prefixed implementation produced:

- tables: 16 files, expected 4
- charts: 8 files, expected 2
- modules: 4 files, expected 1

### GREEN

- DataFrame payloads remain pandas `orient="table"` JSON, but gzip now uses `mtime=0` so identical frames have deterministic bytes.
- Table, PNG, PDF, and XLSX artifact paths are derived from the SHA-256 of their exact persisted bytes.
- An existing immutable content path is reused without another write.
- Repeating a checkpoint four times now stays at 4 table files, 2 chart files, and 1 module file, including an unchanged 10,000-row table.
- Adding one table plus genuinely changed step-status and warning frames adds exactly 3 table files and no chart/module files.
- Schema version 1, relative-path manifests, loader behavior, and manifest-last commit order are unchanged.
- Old referenced artifacts are retained until run TTL/deletion. There is no post-commit garbage collection, so an old or concurrent reader cannot lose files.
- Concurrent writers may race to atomically replace the same hash path only with identical bytes; each manifest is still committed last.
- Existing `test_failed_manifest_commit_keeps_previous_module_revision_loadable` remains green.

## Important 2: partial ZIP without module workbooks

### RED

`test_page_6_offers_partial_zip_when_workflow_fails_before_first_module_export` forced a top-level failure before the first module export. The session had a valid initial result and an empty `module_workbooks`, but the page exposed no partial ZIP.

### GREEN

- Saved results now request a partial ZIP whenever the full package is absent; module workbooks are optional.
- The initial/failed partial workbook always includes `Run_Log`, `Representative_Input`, `Structure_Preparation`, and `Warnings`.
- The behavior test verifies a valid one-file partial ZIP, an empty module mapping, and a failed disk checkpoint.

## Important 3: charts lost when the final ZIP fails

### RED

`test_page_6_keeps_partial_artifacts_when_full_zip_build_fails` returned a non-empty chart map and then raised in the full ZIP builder. The failure path produced no `auto_query_workflow_charts` session key because it rebuilt the failed checkpoint from the older module checkpoint result.

### GREEN

- Immediately after chart generation, the latest result and charts are installed in session state and persisted through `handle_checkpoint()` before full ZIP construction.
- The failed checkpoint is built from the most advanced session result.
- The behavior test verifies the generated chart in the page result, session chart map, failed disk checkpoint, and a fresh `?run` recovery session.

## Minor review

### Frozen checkpoint with shared DataFrames

Deep-copying every cumulative table at every module boundary was deliberately not added because it would multiply peak memory for large runs. `AutoWorkflowCheckpoint` now documents that its frozen outer object contains performance-shared DataFrames and that callbacks must treat all contents as read-only. `test_checkpoint_callback_contract_keeps_shared_result_frames_read_only` enforces both the API documentation and that page 6's callback neither assigns into `checkpoint.result` nor calls common in-place DataFrame mutators.

Accepted residual contract: a third-party callback can still violate the documented read-only rule at runtime; preventing that would require the rejected high-memory deep-copy behavior or a larger immutable-table API redesign.

### AppTest checkpoint-root isolation

Fixed without production API changes. `_isolated_page_checkpoint_storage()` patches the four page-imported storage functions and injects a per-test `TemporaryDirectory`. All AppTest persistence, recovery, cleanup, and explicit deletion in the affected tests now stay outside the repository `.cache` root.

## Verification

- RED bundle: 3 tests reproduced the three review defects as 2 failures and 1 error for the expected missing behaviors.
- Important GREEN bundle: 3/3 passed.
- Read-only contract RED: failed on missing callback documentation; GREEN: 1/1 passed.
- AppTest isolation/behavior bundle: 4/4 passed.
- Required coverage suites: `python -m unittest tests.test_auto_query_checkpoint tests.test_auto_query_workflow tests.test_upload_state -v` — 71/71 passed in 13.365 s.
- Full suite: `python -m unittest discover -s tests -v` — 282/282 passed in 23.312 s.
- Compile: `python -m compileall app.py pages src` — exit 0, no syntax errors.
- Final whitespace check: recorded after this report was added.

## Self-review

- No schema bump or migration was needed because manifests already support arbitrary safe relative artifact paths.
- Manifest remains the sole commit marker and is still atomically replaced after all referenced artifacts are durable.
- No old artifact is removed during save, preserving old-manifest and concurrent-reader safety.
- The successful full ZIP file/folder protocol is unchanged; the only workbook addition occurs when a result lacks its normal `Warnings` table, which covers the pre-module failure checkpoint.
- Partial ZIP rendering no longer depends on module workbook truthiness.
- The chart checkpoint happens before the only downstream operation under test that can fail, the full ZIP builder.
- No unresolved Critical or Important issue remains. The shared-DataFrame callback contract is the only accepted residual concern described above.
