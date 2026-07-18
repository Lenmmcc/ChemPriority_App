# Auto-query Reliability and Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three-round transient failed-row retries, EPI CAS parse-null fallback, figure-bearing module downloads, and readable high-cardinality donut charts.

**Architecture:** Extend the shared ordered batch runner with opt-in result classification and add a small shared transient-error classifier used by every network adapter. Keep checkpointed module workbooks unchanged, but derive downloadable module ZIP packages from the workbook plus the existing chart allowlist. Limit rendered donut categories without changing exported audit tables.

**Tech Stack:** Python 3, pandas, matplotlib, Streamlit, openpyxl, `urllib`, `zipfile`, `unittest`, Streamlit `AppTest`.

## Global Constraints

- Use three total network rounds: the initial round plus at most two failed-row rounds.
- Retry only timeouts, connection failures, HTTP 408/425/429, and HTTP 5xx; ordinary HTTP 4xx failures are final.
- Preserve input/result order, cache behavior, concurrency settings, public table/chart names, checkpoint manifests, and full ZIP layout.
- EPI `HTTP 400: could not parse 'null'` falls back without CAS only when the original request has both CAS and a non-empty SMILES.
- Chart-bearing module downloads contain the existing XLSX plus PNG and PDF figures; chartless downloads remain XLSX.
- Donut audit tables remain complete; only rendered legends are grouped to the largest 11 categories plus `Others`.

---

### Task 1: Shared transient-error classification and ordered retry rounds

**Files:**
- Create: `src/query_retry.py`
- Modify: `src/batch_runner.py:1-100`
- Test: `tests/test_batch_runner.py`
- Test: `tests/test_query_retry.py`

**Interfaces:**
- Produces: `is_transient_query_error(error: object) -> bool` and `warning_frame_has_transient_error(frame: pandas.DataFrame | None) -> bool`.
- Produces: `run_ordered_batch(..., max_attempts=1, should_retry=None, retry_delay_seconds=0)` with backward-compatible defaults.
- Consumes: callers supply `should_retry(result: BatchResult) -> bool`.

- [ ] **Step 1: Write failing classifier tests**

```python
import unittest
import pandas as pd

from src.query_retry import is_transient_query_error, warning_frame_has_transient_error


class QueryRetryTests(unittest.TestCase):
    def test_transient_statuses_and_network_failures_are_retryable(self):
        for text in (
            "HTTP 408: request timeout",
            "HTTP 425: too early",
            "HTTP 429: rate limited",
            "HTTP 500: server error",
            "HTTP 502: bad gateway",
            "HTTP 503: unavailable",
            "HTTP 504: gateway timeout",
            "timed out",
            "connection reset by peer",
            "temporary failure in name resolution",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_transient_query_error(text))

    def test_validation_400_and_not_found_are_not_retryable(self):
        self.assertFalse(is_transient_query_error("HTTP 400: Invalid CAS ID"))
        self.assertFalse(is_transient_query_error("HTTP 404: not found"))

    def test_warning_frame_reads_message_column(self):
        frame = pd.DataFrame({"message": ["HTTP 503: unavailable"]})
        self.assertTrue(warning_frame_has_transient_error(frame))
        self.assertFalse(warning_frame_has_transient_error(pd.DataFrame({"message": ["HTTP 400: bad input"]})))
```

- [ ] **Step 2: Run classifier tests and verify RED**

Run: `python -m unittest tests.test_query_retry -v`

Expected: import failure because `src.query_retry` does not exist.

- [ ] **Step 3: Implement the minimal classifier**

```python
import re
import pandas as pd

_TRANSIENT_HTTP = re.compile(r"\bHTTP\s+(408|425|429|5\d\d)\b", re.I)
_TRANSIENT_TEXT = (
    "timed out", "timeout", "connection reset", "connection refused",
    "temporary failure", "name resolution", "remote end closed",
    "network is unreachable", "service unavailable", "bad gateway",
    "gateway timeout", "too many requests", "rate limit",
)


def is_transient_query_error(error):
    if error is None:
        return False
    text = str(error).strip().lower()
    return bool(_TRANSIENT_HTTP.search(text)) or any(token in text for token in _TRANSIENT_TEXT)


def warning_frame_has_transient_error(frame):
    if not isinstance(frame, pd.DataFrame) or frame.empty or "message" not in frame.columns:
        return False
    return frame["message"].map(is_transient_query_error).any()
```

- [ ] **Step 4: Write failing ordered-round tests**

Append to `tests/test_batch_runner.py`:

```python
    def test_failed_items_retry_after_round_and_keep_original_order(self):
        calls = []
        attempts = {"b": 0, "c": 0}

        def worker(item):
            calls.append(item)
            attempts[item] = attempts.get(item, 0) + 1
            if item == "b" and attempts[item] < 3:
                return "retry"
            if item == "c":
                return "final"
            return item.upper()

        results = run_ordered_batch(
            ["a", "b", "c"], worker, delay_seconds=0,
            max_attempts=3,
            should_retry=lambda result: result.value == "retry",
            retry_delay_seconds=0,
        )

        self.assertEqual([result.value for result in results], ["A", "B", "final"])
        self.assertEqual(calls, ["a", "b", "c", "b", "b"])

    def test_non_retryable_exception_is_not_repeated(self):
        calls = []

        def worker(item):
            calls.append(item)
            raise ValueError("bad input")

        results = run_ordered_batch(
            ["a"], worker, max_attempts=3,
            should_retry=lambda result: False,
        )

        self.assertEqual(calls, ["a"])
        self.assertIsInstance(results[0].error, ValueError)
```

- [ ] **Step 5: Run ordered-round tests and verify RED**

Run: `python -m unittest tests.test_batch_runner -v`

Expected: `TypeError` for unsupported `max_attempts`/`should_retry` arguments.

- [ ] **Step 6: Implement round-based retry in `run_ordered_batch`**

Refactor the existing sequential/parallel execution into a private `run_indices(indices, attempt)` helper. Preserve the original `results` list and replace only the indices executed in later rounds. After each round:

```python
pending = [
    index for index in indices
    if attempt < attempt_limit and should_retry and should_retry(results[index])
]
if pending and retry_delay:
    time.sleep(retry_delay * attempt)
indices = pending
attempt += 1
```

Add `attempt` and `max_attempts` fields to emitted lifecycle dictionaries while retaining every existing key. Call the existing progress callback only when an original item reaches a final result, so progress never exceeds `total`.

- [ ] **Step 7: Run shared retry tests and verify GREEN**

Run: `python -m unittest tests.test_query_retry tests.test_batch_runner -v`

Expected: all tests pass.

- [ ] **Step 8: Commit shared retry primitives**

```powershell
git add src/query_retry.py src/batch_runner.py tests/test_query_retry.py tests/test_batch_runner.py
git commit -m "feat: retry transient failed query rows"
```

### Task 2: Opt all network batch adapters into three rounds

**Files:**
- Modify: `src/identifier_resolver.py:297-361`
- Modify: `src/episuite_io.py:384-458`
- Modify: `src/comptox_use.py:466-550`
- Modify: `src/echa_use.py:439-525`
- Modify: `src/echa_ghs.py:172-226`
- Modify: `src/source_origin.py:209-277`
- Test: `tests/test_identifier_resolver.py`
- Test: `tests/test_episuite_cas_values.py`
- Test: `tests/test_comptox_dashboard_mode.py`
- Test: `tests/test_echa_use.py`
- Test: `tests/test_echa_ghs.py`
- Test: `tests/test_source_origin.py`

**Interfaces:**
- Consumes: `warning_frame_has_transient_error(frame)` and the new `run_ordered_batch` parameters.
- Produces: identical public return tuples with transient stale failures replaced by the last attempt.

- [ ] **Step 1: Add one failing adapter regression per module**

Use each module's existing patched one-row sequential function. The shared pattern is:

```python
attempts = 0

def transient_then_success(*args, **kwargs):
    nonlocal attempts
    attempts += 1
    if attempts == 1:
        return failed_summary, empty_details, pd.DataFrame(
            [{"message": "HTTP 503: unavailable"}]
        )
    return success_summary, success_details, pd.DataFrame(columns=["message"])
```

Patch the module's `_run_*_batch_sequential`, call its public parallel wrapper with one row and `delay_seconds=0`, and assert `attempts == 2`, the final summary is successful, and the final warning frame is empty. Match the exact tuple arity:

- identifier: `(completed, warnings)`;
- EPI: `(summary, raw, errors)` where the error column is `error` and its retry predicate reads that column;
- CompTox: `(summary, candidates, errors)`;
- ECHA use: `(summary, candidates, dossiers, warnings)`;
- ECHA GHS: `(summary, classifications, warnings)`;
- source origin: `(summary, evidence, warnings)`.

Add a paired HTTP 400 test to one adapter to assert a single call.

- [ ] **Step 2: Run the six focused test modules and verify RED**

Run:

```powershell
python -m unittest tests.test_identifier_resolver tests.test_episuite_cas_values tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_echa_ghs tests.test_source_origin -v
```

Expected: transient rows are attempted once, so the new `attempts == 2` assertions fail.

- [ ] **Step 3: Wire retry predicates into each adapter**

Import `warning_frame_has_transient_error` and add to each `run_ordered_batch` call:

```python
max_attempts=3,
retry_delay_seconds=max(1.0, float(delay_seconds or 0)),
should_retry=lambda result: (
    is_transient_query_error(result.error)
    if result.error is not None
    else warning_frame_has_transient_error(result.value[-1])
),
```

For EPI, add `error_frame_has_transient_error(frame)` in `src/query_retry.py` or generalize the frame helper with `columns=("message", "error")`; use the same deterministic classifier. Do not retry a successful result that merely contains informational notes.

- [ ] **Step 4: Run adapter regressions and verify GREEN**

Run the command from Step 2.

Expected: all six modules pass, transient rows run twice, and HTTP 400 runs once.

- [ ] **Step 5: Commit adapter opt-in**

```powershell
git add src/query_retry.py src/identifier_resolver.py src/episuite_io.py src/comptox_use.py src/echa_use.py src/echa_ghs.py src/source_origin.py tests/test_identifier_resolver.py tests/test_episuite_cas_values.py tests/test_comptox_dashboard_mode.py tests/test_echa_use.py tests/test_echa_ghs.py tests/test_source_origin.py
git commit -m "feat: retry failed rows across network modules"
```

### Task 3: EPI CAS parse-null fallback and local SMILES validation

**Files:**
- Modify: `src/episuite_io.py:254-283,345-458,1083-1093`
- Test: `tests/test_episuite_cas_values.py:120-257`

**Interfaces:**
- Produces: `_is_cas_fallback_error(error_text: str) -> bool` recognizing existing CAS-not-found and the exact parse-null signature.
- Preserves: `run_epi_web_batch(...) -> (results_df, raw_df, errors_df)`.

- [ ] **Step 1: Write failing EPI regressions**

```python
    @patch("src.episuite_io.call_epi_web_api")
    def test_parse_null_with_cas_falls_back_to_same_smiles_without_cas(self, call_api):
        call_api.side_effect = [
            RuntimeError("EPI Web Suite returned HTTP 400: could not parse 'null'"),
            ETHANOL_CAS_AND_SMILES_RESPONSE,
        ]
        input_df = pd.DataFrame({
            "compound": ["Example"],
            "smiles": ["CC(C)c1ccc2c(c1)CCC1C(C)CCCC21C"],
            "cas": ["5323-56-8"],
        })

        results, raw_rows, errors = episuite_io.run_epi_web_batch(input_df, delay_seconds=0)

        self.assertEqual(call_api.call_args_list[0].kwargs["cas"], "5323-56-8")
        self.assertIsNone(call_api.call_args_list[1].kwargs["cas"])
        self.assertEqual(call_api.call_args_list[1].args[0], input_df.loc[0, "smiles"])
        self.assertEqual(results.loc[0, "status"], "success")
        self.assertIn("CAS", results.loc[0, "query_note"])
        self.assertTrue(errors.empty)

    @patch("src.episuite_io.call_epi_web_api")
    def test_other_http_400_does_not_fall_back(self, call_api):
        call_api.side_effect = RuntimeError("EPI Web Suite returned HTTP 400: invalid structure")
        input_df = pd.DataFrame({"compound": ["Bad"], "smiles": ["bad"], "cas": ["1-11-1"]})

        results, _, errors = episuite_io.run_epi_web_batch(input_df, delay_seconds=0)

        call_api.assert_called_once()
        self.assertEqual(results.loc[0, "status"], "failed")
        self.assertEqual(len(errors), 1)

    def test_normalize_input_treats_literal_null_smiles_as_missing(self):
        normalized = episuite_io.normalize_input_columns(
            pd.DataFrame({"compound": ["Bad"], "smiles": ["null"]})
        )
        self.assertTrue(pd.isna(normalized.loc[0, "smiles"]))
```

- [ ] **Step 2: Run EPI tests and verify RED**

Run: `python -m unittest tests.test_episuite_cas_values -v`

Expected: parse-null request is not retried without CAS and literal `null` remains a string.

- [ ] **Step 3: Implement the narrow fallback**

Extend the existing input cleaners to treat case-insensitive `null` as missing. Rename/generalize the fallback predicate:

```python
def _is_cas_fallback_error(error_text):
    lowered = str(error_text).lower()
    return (
        ("http 404" in lowered and "could not locate cas id" in lowered)
        or ("http 400" in lowered and "could not parse 'null'" in lowered)
    )
```

In `process_row`, clean SMILES through `_clean_optional_text`; if it is empty, append a local failed row without calling the API. If CAS is present and `_is_cas_fallback_error(error_text)` is true, call `call_epi_web_api(smiles, cas=None, ...)` and retain a query note containing the first error.

- [ ] **Step 4: Run EPI tests and verify GREEN**

Run: `python -m unittest tests.test_episuite_cas_values -v`

Expected: all tests pass.

- [ ] **Step 5: Live-smoke the reported compound**

Run the repository EPI call for `CC(C)c1ccc2c(c1)CCC1C(C)CCCC21C` with CAS `5323-56-8` and assert the batch result status is `success`, the errors frame is empty, and `query_note` records the CAS fallback.

- [ ] **Step 6: Commit EPI correction**

```powershell
git add src/episuite_io.py tests/test_episuite_cas_values.py
git commit -m "fix: fall back from broken EPI CAS records"
```

### Task 4: Package per-module figures with downloads and partial ZIPs

**Files:**
- Modify: `src/auto_query_workflow.py:62-220,814-924`
- Modify: `pages/6_一键批量查询.py:319-417`
- Test: `tests/test_auto_query_workflow.py:1162-1209,1541-1855`

**Interfaces:**
- Produces: immutable `AutoWorkflowModuleDownload(file_name: str, mime: str, data: bytes)`.
- Produces: `build_auto_workflow_module_download(module, charts) -> AutoWorkflowModuleDownload`.
- Changes: `build_auto_workflow_partial_zip(result, module_workbooks, charts=None)`; the new argument is optional for compatibility.

- [ ] **Step 1: Write failing package payload tests**

```python
    def test_chart_module_download_is_zip_with_workbook_png_and_pdf(self):
        module = AutoWorkflowModuleWorkbook(
            step=R_DF_STEP_LABEL,
            slug="local_screening",
            file_name="Local_Screening_Results.xlsx",
            data=b"XLSX",
        )
        charts = OrderedDict({
            "Local_Chemical_Type_Distribution": AutoWorkflowChart(
                title="Chemical type", png=b"PNG", pdf=b"PDF"
            )
        })

        download = build_auto_workflow_module_download(module, charts)

        self.assertEqual(download.file_name, "Local_Screening_Results.zip")
        self.assertEqual(download.mime, "application/zip")
        with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
            self.assertEqual(set(archive.namelist()), {
                "Local_Screening_Results.xlsx",
                "figures/Chemical_Type_Distribution.png",
                "figures/Chemical_Type_Distribution.pdf",
            })

    def test_chartless_module_download_remains_xlsx(self):
        module = AutoWorkflowModuleWorkbook(
            step="标识符补全",
            slug="identifier_completion",
            file_name="Identifier_Completion_Results.xlsx",
            data=b"XLSX",
        )
        download = build_auto_workflow_module_download(module, OrderedDict())
        self.assertEqual(download.file_name, module.file_name)
        self.assertEqual(download.mime, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertEqual(download.data, module.data)
```

Add a partial-ZIP test passing charts and asserting `modules/<module>.xlsx` plus `modules/<slug>/figures/*.png` and `*.pdf` are present.

- [ ] **Step 2: Run package tests and verify RED**

Run: `python -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_chart_module_download_is_zip_with_workbook_png_and_pdf -v`

Expected: import/name failure because `build_auto_workflow_module_download` does not exist.

- [ ] **Step 3: Implement package metadata and ZIP builder**

Add:

```python
@dataclass(frozen=True)
class AutoWorkflowModuleDownload:
    file_name: str
    mime: str
    data: bytes
```

Find the module definition by workbook file name or slug, filter `charts` through its existing chart candidate tuple and `PUBLIC_CHART_NAMES`, and return XLSX metadata when none exist. For charts, write the existing workbook and both chart formats using `_module_chart_file_name`.

Update `build_auto_workflow_partial_zip` to include the same available chart files without mutating `result.charts` or checkpointed module objects.

- [ ] **Step 4: Update page 6 and write failing AppTest expectations first**

Before changing the renderer, update its AppTest fixture with a chart-bearing local module and assert:

```python
download = downloads[f"下载 {R_DF_STEP_LABEL}"]
self.assertEqual(download.proto.file_name, "Local_Screening_Results.zip")
payload = _app_test_download_payload(download, media_storages[-1])
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    self.assertIn("Local_Screening_Results.xlsx", archive.namelist())
    self.assertIn("figures/Chemical_Type_Distribution.png", archive.namelist())
```

Run that AppTest and confirm it fails because the button still serves XLSX.

- [ ] **Step 5: Wire package metadata into page 6**

Pass `charts` into `_render_module_downloads`, call `build_auto_workflow_module_download(module, charts)` for each completed module, and bind `data`, `file_name`, and `mime` from the returned object. Keep `on_click="ignore"`. Pass charts into partial-ZIP construction in saved, failed, recovered, and completed result paths.

- [ ] **Step 6: Run workflow and checkpoint regressions**

Run: `python -m unittest tests.test_auto_query_workflow tests.test_auto_query_checkpoint -v`

Expected: all tests pass; existing chartless checkpoint downloads remain byte-identical XLSX.

- [ ] **Step 7: Commit module packages**

```powershell
git add src/auto_query_workflow.py pages/6_一键批量查询.py tests/test_auto_query_workflow.py tests/test_auto_query_checkpoint.py
git commit -m "feat: include figures in module downloads"
```

### Task 5: Group crowded donut categories and reserve legend space

**Files:**
- Modify: `src/use_rose_plot.py:75-95,1028-1213`
- Test: `tests/test_use_rose_plot.py:74-102,400-450`

**Interfaces:**
- Produces: `CLASSIFICATION_PIE_MAX_CATEGORIES = 12`.
- Changes: `generate_compound_classification_pie_plot(..., max_categories=CLASSIFICATION_PIE_MAX_CATEGORIES)` default while fixed four-category charts remain unchanged.
- Preserves: complete input DataFrame and total compound count.

- [ ] **Step 1: Write failing high-cardinality donut test**

```python
    def test_high_cardinality_donut_groups_tail_without_changing_total(self):
        plot_df = pd.DataFrame([
            {"compound_key": f"compound-{index}", "display_label": f"Category {index:02d}"}
            for index in range(20)
        ])

        figure = generate_compound_classification_pie_plot(plot_df, "Classification")
        try:
            labels = [
                text.get_text()
                for legend in figure.legends
                for text in legend.get_texts()
            ]
            self.assertEqual(len(labels), 12)
            self.assertTrue(any(label.startswith("Others (9,") for label in labels))
            self.assertIn("Total compounds\n20", {text.get_text() for text in figure.axes[0].texts})
        finally:
            plt.close(figure)
```

Add a renderer-geometry assertion after `figure.canvas.draw()` that the legend bounding box does not intersect the donut axes' tight bounding box.

- [ ] **Step 2: Run donut tests and verify RED**

Run: `python -m unittest tests.test_use_rose_plot -v`

Expected: 20 legend entries are produced and geometry overlaps for long labels.

- [ ] **Step 3: Implement grouping and layout**

Define `CLASSIFICATION_PIE_MAX_CATEGORIES = 12`, make it the default `max_categories`, and have `generate_reported_functional_use_pie_plot` use the same limit instead of `None`. Preserve fixed-category ordering before grouping. Use a wider figure and dedicated legend region, for example:

```python
fig, ax = plt.subplots(figsize=(11.5, 6.8), facecolor="white")
fig.subplots_adjust(left=0.05, right=0.58, top=0.86, bottom=0.12)
fig.legend(handles=handles, loc="center left", bbox_to_anchor=(0.60, 0.52), frameon=False, title="Category")
```

Wrap only display text for exceptionally long legend labels; do not modify audit labels or workbook values.

- [ ] **Step 4: Run plot tests and verify GREEN**

Run: `python -m unittest tests.test_use_rose_plot tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_source_origin -v`

Expected: all tests pass with at most 12 legend entries and preserved totals.

- [ ] **Step 5: Render and inspect a 40-category PNG**

Generate one local diagnostic PNG from synthetic long labels, open it with the image inspection tool, and confirm the title, donut, center total, percentages, legend, and footnote do not overlap. Do not add the diagnostic file to Git.

- [ ] **Step 6: Commit donut layout**

```powershell
git add src/use_rose_plot.py tests/test_use_rose_plot.py
git commit -m "fix: keep crowded donut legends readable"
```

### Task 6: Integration verification

**Files:**
- Verify only; modify production/tests only if a newly reproduced regression requires a focused red-green fix.

**Interfaces:**
- Consumes all prior task outputs.
- Produces fresh verification evidence and a clean scoped diff.

- [ ] **Step 1: Run all targeted regressions together**

```powershell
python -m unittest tests.test_batch_runner tests.test_query_retry tests.test_episuite_cas_values tests.test_identifier_resolver tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_echa_ghs tests.test_source_origin tests.test_use_rose_plot tests.test_auto_query_workflow tests.test_auto_query_checkpoint -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the complete repository suite**

Run: `python -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

- [ ] **Step 3: Compile application modules**

Run: `python -m compileall app.py pages src`

Expected: exit code 0.

- [ ] **Step 4: Check patch hygiene and exact scope**

```powershell
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: no whitespace errors; unrelated pre-existing untracked docs and `outputs/` remain untouched.

- [ ] **Step 5: Run final branch completion workflow**

Invoke `superpowers:finishing-a-development-branch`, present verified integration choices, and do not push or merge without the user's explicit choice.
