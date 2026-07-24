# Multi-File EPI Supplement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multiple-primary-workbook support to the one-click workflow, allow filename-associated EPI supplement workbooks and same-session EPI reuse, query/retry only unresolved compounds, and preserve the existing multi-sample Pov-LRTP/PBM/ToxPi semantics.

**Architecture:** Extract the existing comprehensive-page multi-file preparation into a shared pure module, then pass a prepared sample-level/compound-level bundle into the auto-query workflow. Add a separate EPI supplement engine for workbook inspection, row matching, completeness, provenance, conflict resolution, session-pool reuse, and minimal network scheduling; keep successful public API responses in the existing shared SQLite cache and user uploads in session/checkpoint state only.

**Tech Stack:** Python 3.14, pandas, openpyxl, Streamlit, SQLite, unittest, Streamlit `AppTest`, existing ChemPriority screening/EPI/checkpoint modules.

## Global Constraints

- Preserve the mathematical definition of Pov-LRTP, PBM, ToxPi weights, two-stage normalization, robustness analysis, and display limits.
- Preserve the existing multi-file meaning: each primary Excel workbook is one sample; calculate file-level Group Area means before cross-file DF and ToxPi aggregation.
- Associate EPI supplements to primary workbooks by filename only; never inspect chemical overlap to choose an association.
- Match EPI rows by CAS, then SMILES, then compound name.
- Apply field precedence `current upload > same-session pool > API cache/new request`; lower-priority sources fill nulls only.
- Do not persist user-uploaded EPI values in the global SQLite cache.
- Keep CAS-aware and CAS-less EPI API cache keys distinct.
- Treat a maximum of three automatic attempts as three attempts total, with retry waits before attempts two and three.
- Preserve complete evidence and audit tables in XLSX/ZIP/checkpoints even when previews are abbreviated.
- Preserve unrelated untracked documents and `outputs/`.

---

## File Structure

### New files

- `src/multi_file_screening.py` — primary-workbook types, mapping defaults, normalization, multi-file front-half preparation, and sample/compound representations.
- `src/episuite_supplement.py` — EPI workbook inspection/parsing, filename suggestions, identifier matching, completeness, merge, provenance, conflicts, and retry-target construction.
- `src/episuite_result_pool.py` — Streamlit-independent same-session EPI pool operations.
- `tests/test_multi_file_screening.py` — shared multi-file preparation and preserved screening semantics.
- `tests/test_episuite_supplement.py` — workbook round trips, matching, merge precedence, completeness, and query scheduling.
- `tests/test_episuite_result_pool.py` — contributor-scoped session-pool behavior.

### Modified files

- `src/query_retry.py` — exact WinError/SSL EOF recognition and exponential delay helper.
- `src/batch_runner.py` — callable retry-delay support without progress overcounting.
- `src/episuite_io.py` — use exponential delay and retain per-attempt activity events.
- `src/auto_query_workflow.py` — accept prepared multi-file input and EPI seed/pool data, export audits, and retry only EPI failures plus dependents.
- `src/auto_query_checkpoint.py` — schema v2 input-filename list and retry/mapping recovery with schema v1 read compatibility.
- `src/query_cache.py` — cache diagnostics and expired-row pruning.
- `pages/0_综合筛查流程.py` — consume shared multi-file helpers without changing visible behavior.
- `pages/3_EPISuite环境归趋.py` — round-trip result parsing, same-session pool contribution, and cache diagnostics.
- `pages/6_一键批量查询.py` — multiple primary files, per-file mapping, EPI supplements, completeness preview, shared pool, minimal query, manual retry, and cache controls.
- `tests/test_query_retry.py`
- `tests/test_batch_runner.py`
- `tests/test_episuite_cas_values.py`
- `tests/test_query_cache.py`
- `tests/test_cp_screening_workflow.py`
- `tests/test_auto_query_workflow.py`
- `tests/test_upload_state.py`
- `tests/test_structure_preparation_page_contract.py`

---

### Task 1: Recognize the reported transient EPI failures and implement exact retry timing

**Files:**
- Modify: `src/query_retry.py`
- Modify: `src/batch_runner.py`
- Modify: `src/episuite_io.py`
- Test: `tests/test_query_retry.py`
- Test: `tests/test_batch_runner.py`
- Test: `tests/test_episuite_cas_values.py`

**Interfaces:**
- Produces: `transient_retry_delay(completed_attempt: int, *, base_seconds: float = 1.0, jitter_fraction: float = 0.2, random_value: float | None = None) -> float`
- Changes `run_ordered_batch` parameter: `retry_delay_seconds: float | Callable[[int], float] = 0`
- Preserves: three attempts total and one progress completion per input item.

- [ ] **Step 1: Write failing exact-error and delay tests**

Add to `tests/test_query_retry.py`:

```python
def test_reported_winerror_and_ssl_eof_are_retryable(self):
    messages = (
        "无法连接 EPI Web Suite: [WinError 10054] 远程主机强迫关闭了一个现有的连接。",
        "无法连接 EPI Web Suite: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1010)",
        "SSLEOFError: EOF occurred in violation of protocol",
        "connection forcibly closed by remote host",
    )
    for message in messages:
        with self.subTest(message=message):
            self.assertTrue(is_transient_query_error(message))


def test_transient_retry_delay_is_exponential_with_bounded_jitter(self):
    self.assertEqual(
        transient_retry_delay(1, random_value=0.0),
        1.0,
    )
    self.assertEqual(
        transient_retry_delay(2, random_value=0.0),
        2.0,
    )
    self.assertEqual(
        transient_retry_delay(2, random_value=1.0),
        2.4,
    )
```

Add to `tests/test_batch_runner.py`:

```python
@patch("src.batch_runner.time.sleep")
def test_callable_retry_delay_receives_completed_attempt(self, sleep):
    attempts = 0

    def worker(_item):
        nonlocal attempts
        attempts += 1
        return "retry" if attempts < 3 else "done"

    run_ordered_batch(
        ["a"],
        worker,
        max_attempts=3,
        should_retry=lambda result: result.value == "retry",
        retry_delay_seconds=lambda completed_attempt: float(2 ** (completed_attempt - 1)),
    )

    self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])
```

Update imports for `patch` and `transient_retry_delay`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_retry tests.test_batch_runner -v
```

Expected: the reported error strings return `False`, `transient_retry_delay` is missing, and callable delay is unsupported.

- [ ] **Step 3: Implement exact classification and retry delay**

In `src/query_retry.py`, add the exact tokens and helper:

```python
import random

_TRANSIENT_TEXT = (
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "temporary failure",
    "name resolution",
    "remote end closed",
    "network is unreachable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "too many requests",
    "rate limit",
    "winerror 10054",
    "远程主机强迫关闭",
    "forcibly closed by remote host",
    "ssleoferror",
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
)


def transient_retry_delay(
    completed_attempt: int,
    *,
    base_seconds: float = 1.0,
    jitter_fraction: float = 0.2,
    random_value: float | None = None,
) -> float:
    completed = max(1, int(completed_attempt))
    base = max(0.0, float(base_seconds)) * (2 ** (completed - 1))
    fraction = min(1.0, max(0.0, float(jitter_fraction)))
    draw = random.random() if random_value is None else min(1.0, max(0.0, float(random_value)))
    return base + base * fraction * draw
```

In `src/batch_runner.py`, replace the fixed wait block with:

```python
retry_delay = (
    retry_delay_seconds
    if callable(retry_delay_seconds)
    else max(0.0, float(retry_delay_seconds or 0))
)

pending = list(range(total))
attempt = 1
while pending:
    pending = run_indices(pending, attempt)
    if pending and retry_delay:
        wait_seconds = (
            retry_delay(attempt)
            if callable(retry_delay)
            else retry_delay * attempt
        )
        if wait_seconds:
            time.sleep(wait_seconds)
    attempt += 1
```

In `src/episuite_io.py`, import `transient_retry_delay` and pass:

```python
retry_delay_seconds=lambda completed_attempt: transient_retry_delay(
    completed_attempt,
    base_seconds=max(1.0, float(delay_seconds or 0)),
),
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_retry tests.test_batch_runner tests.test_episuite_cas_values -v
```

Expected: all tests pass; EPI still configures `max_attempts=3`.

- [ ] **Step 5: Commit**

```powershell
git add src/query_retry.py src/batch_runner.py src/episuite_io.py tests/test_query_retry.py tests/test_batch_runner.py tests/test_episuite_cas_values.py
git commit -m "fix: retry EPI connection reset and SSL EOF failures"
```

---

### Task 2: Round-trip ChemPriority EPI Excel downloads

**Files:**
- Create: `src/episuite_supplement.py`
- Create: `tests/test_episuite_supplement.py`
- Modify: `src/episuite_io.py`

**Interfaces:**
- Produces: `EPISupplementMapping`
- Produces: `EPIWorkbookInspection`
- Produces: `inspect_epi_workbook(data: bytes, file_name: str) -> EPIWorkbookInspection`
- Produces: `parse_epi_supplement(data: bytes, mapping: EPISupplementMapping) -> tuple[pd.DataFrame, pd.DataFrame]`
- Extends: `parse_uploaded_result(uploaded_file, sheet_name: str | int | None = None)`

- [ ] **Step 1: Write failing round-trip tests**

Create `tests/test_episuite_supplement.py` with:

```python
import io
import unittest

import pandas as pd

from src.episuite_io import build_result_workbook
from src.episuite_supplement import (
    EPISupplementMapping,
    inspect_epi_workbook,
    parse_epi_supplement,
)


class EPISupplementWorkbookTests(unittest.TestCase):
    def _report_bytes(self):
        input_df = pd.DataFrame(
            {"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]}
        )
        result_df = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
                "status": ["success"],
                "log_kow": [-0.31],
                "henry_atm_m3_mol": [5.0e-6],
            }
        )
        return build_result_workbook(input_df, merged_df=result_df).getvalue()

    def test_report_prefers_core_summary_over_validated_input(self):
        inspection = inspect_epi_workbook(
            self._report_bytes(),
            "EPISuite_Fate_Report.xlsx",
        )
        self.assertEqual(inspection.default_result_sheet, "Core_Summary")
        self.assertIn("Validated_Input", inspection.sheet_names)

    def test_core_summary_round_trips_as_epi_results(self):
        mapping = EPISupplementMapping(
            source_file="EPISuite_Fate_Report.xlsx",
            primary_file="Lake-A.xlsx",
            sheet_name="Core_Summary",
        )
        parsed, warnings = parse_epi_supplement(self._report_bytes(), mapping)
        self.assertEqual(parsed.loc[0, "compound"], "Ethanol")
        self.assertEqual(parsed.loc[0, "cas"], "64-17-5")
        self.assertEqual(parsed.loc[0, "log_kow"], -0.31)
        self.assertTrue(warnings.empty)

    def test_epi_results_sheet_is_second_recognized_format(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame({"note": ["not results"]}).to_excel(
                writer, sheet_name="Run_Log", index=False
            )
            pd.DataFrame(
                {"compound": ["A"], "smiles": ["CC"], "log_kow": [1.5]}
            ).to_excel(writer, sheet_name="EPI_Results", index=False)
        inspection = inspect_epi_workbook(buffer.getvalue(), "EPI_Suite_Results.xlsx")
        self.assertEqual(inspection.default_result_sheet, "EPI_Results")
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_supplement -v
```

Expected: import failure because the supplement module does not exist.

- [ ] **Step 3: Implement workbook inspection and mapped parsing**

Create `src/episuite_supplement.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import io

import pandas as pd

from src.episuite_io import ENDPOINT_KEYS, parse_table_result


RECOGNIZED_RESULT_SHEETS = ("Core_Summary", "EPI_Results")


@dataclass(frozen=True)
class EPIWorkbookInspection:
    file_name: str
    sheet_names: tuple[str, ...]
    default_result_sheet: str | None


@dataclass(frozen=True)
class EPISupplementMapping:
    source_file: str
    primary_file: str
    sheet_name: str
    compound_col: str | None = None
    smiles_col: str | None = None
    cas_col: str | None = None
    endpoint_columns: dict[str, str] = field(default_factory=dict)
    priority: int = 0


def inspect_epi_workbook(data: bytes, file_name: str) -> EPIWorkbookInspection:
    workbook = pd.ExcelFile(io.BytesIO(data))
    default_sheet = next(
        (name for name in RECOGNIZED_RESULT_SHEETS if name in workbook.sheet_names),
        None,
    )
    return EPIWorkbookInspection(
        file_name=str(file_name),
        sheet_names=tuple(workbook.sheet_names),
        default_result_sheet=default_sheet,
    )


def parse_epi_supplement(
    data: bytes,
    mapping: EPISupplementMapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.read_excel(io.BytesIO(data), sheet_name=mapping.sheet_name)
    rename_map = {
        source: target
        for target, source in {
            "compound": mapping.compound_col,
            "smiles": mapping.smiles_col,
            "cas": mapping.cas_col,
            **mapping.endpoint_columns,
        }.items()
        if source and source in frame.columns
    }
    normalized = frame.rename(columns=rename_map)
    parsed, warnings = parse_table_result(
        normalized,
        source_name=mapping.source_file,
    )
    parsed["primary_file"] = mapping.primary_file
    parsed["source_sheet"] = mapping.sheet_name
    parsed["source_priority"] = int(mapping.priority)
    for endpoint in ENDPOINT_KEYS:
        if endpoint not in parsed.columns:
            parsed[endpoint] = pd.NA
    return parsed, warnings
```

Extend `parse_uploaded_result` in `src/episuite_io.py` so Excel callers may pass a sheet:

```python
def parse_uploaded_result(uploaded_file, sheet_name=None):
    name = uploaded_file.name
    suffix = Path(name).suffix.lower()
    raw = uploaded_file.getvalue()

    if suffix in {".xlsx", ".xls"}:
        selected_sheet = 0 if sheet_name is None else sheet_name
        return parse_table_result(
            pd.read_excel(io.BytesIO(raw), sheet_name=selected_sheet),
            source_name=name,
        )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_supplement tests.test_episuite_cas_values -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/episuite_supplement.py src/episuite_io.py tests/test_episuite_supplement.py
git commit -m "feat: round-trip EPI result workbooks"
```

---

### Task 3: Resolve filename association, row matches, completeness, provenance, and minimal query targets

**Files:**
- Modify: `src/episuite_supplement.py`
- Modify: `tests/test_episuite_supplement.py`

**Interfaces:**
- Produces: `EPIResolution`
- Produces: `suggest_primary_filename(supplement_name: str, primary_names: list[str]) -> str | None`
- Produces: `resolve_epi_sources(compound_universe, uploaded_results, pool_results, completed_identifiers=None, require_core=False) -> EPIResolution`
- Produces: `merge_network_epi(resolution: EPIResolution, network_results, network_raw, network_errors, attempt_events=()) -> EPIResolution`

- [ ] **Step 1: Write failing association, precedence, and completeness tests**

Add tests:

```python
def complete_epi_rows(compounds):
    compounds = list(compounds)
    count = len(compounds)
    return pd.DataFrame(
        {
            "compound": compounds,
            "smiles": ["CC"] * count,
            "cas": [""] * count,
            "status": ["success"] * count,
            "molecular_weight": [100.0] * count,
            "henry_atm_m3_mol": [1.0e-5] * count,
            "log_kow": [2.0] * count,
            "level3_air_half_life_hours": [10.0] * count,
            "level3_water_half_life_hours": [20.0] * count,
            "level3_soil_half_life_hours": [30.0] * count,
            "log_baf": [1.0] * count,
        }
    )


def test_filename_suggestion_uses_only_normalized_filename(self):
    self.assertEqual(
        suggest_primary_filename(
            "Lake-A_EPISuite_Fate_Report.xlsx",
            ["Lake-A.xlsx", "Lake-B.xlsx"],
        ),
        "Lake-A.xlsx",
    )
    self.assertIsNone(
        suggest_primary_filename(
            "unknown.xlsx",
            ["Lake-A.xlsx", "Lake-B.xlsx"],
        )
    )


def test_cas_match_wins_and_uploaded_values_are_not_overwritten(self):
    universe = pd.DataFrame(
        {
            "compound": ["Ethanol"],
            "smiles": ["CCO"],
            "cas": ["64-17-5"],
        }
    )
    uploaded = pd.DataFrame(
        {
            "compound": ["Wrong display name"],
            "smiles": ["different"],
            "cas": ["64-17-5"],
            "log_kow": [-0.31],
            "henry_atm_m3_mol": [pd.NA],
            "source_file": ["Lake-A_EPI.xlsx"],
            "source_sheet": ["Core_Summary"],
            "source_row": [2],
            "source_priority": [0],
        }
    )
    pool = pd.DataFrame(
        {
            "compound": ["Ethanol"],
            "smiles": ["CCO"],
            "cas": ["64-17-5"],
            "log_kow": [99.0],
            "henry_atm_m3_mol": [5.0e-6],
        }
    )

    resolution = resolve_epi_sources(universe, uploaded, pool)

    self.assertEqual(resolution.results.loc[0, "log_kow"], -0.31)
    self.assertEqual(resolution.results.loc[0, "henry_atm_m3_mol"], 5.0e-6)
    self.assertEqual(resolution.match_audit.loc[0, "match_method"], "cas")
    self.assertTrue(
        resolution.provenance["source_type"].isin(["uploaded", "session_pool"]).all()
    )


def test_complete_upload_skips_query_and_core_missing_is_targeted(self):
    universe = pd.DataFrame(
        {"compound": ["A", "B"], "smiles": ["CC", "CCC"], "cas": ["", ""]}
    )
    uploaded = complete_epi_rows(["A", "B"])
    epi_only = resolve_epi_sources(universe, uploaded, pd.DataFrame(), require_core=False)
    self.assertTrue(epi_only.query_input.empty)

    uploaded.loc[uploaded["compound"].eq("B"), "log_baf"] = pd.NA
    downstream = resolve_epi_sources(
        universe,
        uploaded,
        pd.DataFrame(),
        require_core=True,
    )
    self.assertEqual(downstream.query_input["compound"].tolist(), ["B"])
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_supplement -v
```

Expected: new interfaces are missing.

- [ ] **Step 3: Implement deterministic resolution**

Add these public types/constants to `src/episuite_supplement.py`:

```python
CORE_MODEL_FIELDS = (
    "molecular_weight",
    "henry_atm_m3_mol",
    "log_kow",
    "level3_air_half_life_hours",
    "level3_water_half_life_hours",
    "level3_soil_half_life_hours",
    "log_baf",
)


@dataclass
class EPIResolution:
    results: pd.DataFrame
    raw_results: pd.DataFrame
    errors: pd.DataFrame
    completeness: pd.DataFrame
    provenance: pd.DataFrame
    match_audit: pd.DataFrame
    conflict_audit: pd.DataFrame
    query_attempts: pd.DataFrame
    query_input: pd.DataFrame
```

Implement normalization with these exact match keys:

```python
def normalize_cas(value) -> str:
    text = clean_text(value)
    return text.replace(" ", "")


def normalize_smiles(value) -> str:
    return clean_text(value)


def normalize_name(value) -> str:
    return " ".join(clean_text(value).casefold().split())
```

Implement filename suggestion without workbook access:

```python
def suggest_primary_filename(
    supplement_name: str,
    primary_names: list[str],
) -> str | None:
    suffixes = ("_episuite_fate_report", "_episuite", "_epi")
    stem = Path(supplement_name).stem.casefold()
    for suffix in suffixes:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    candidates = [
        name for name in primary_names if Path(name).stem.casefold() == stem
    ]
    return candidates[0] if len(candidates) == 1 else None
```

Implement `resolve_epi_sources` as a field-level merge:

```python
def resolve_epi_sources(
    compound_universe: pd.DataFrame,
    uploaded_results: pd.DataFrame,
    pool_results: pd.DataFrame,
    completed_identifiers: pd.DataFrame | None = None,
    require_core: bool = False,
) -> EPIResolution:
    universe = prepare_universe(compound_universe, completed_identifiers)
    uploaded = prepare_source(uploaded_results, "uploaded", priority_default=0)
    pool = prepare_source(pool_results, "session_pool", priority_default=10_000)
    matched, match_audit = match_sources(universe, pd.concat([uploaded, pool], ignore_index=True))
    results, provenance, conflicts = merge_matched_fields(universe, matched)
    completeness = classify_completeness(
        universe,
        results,
        require_core=require_core,
    )
    query_keys = set(
        completeness.loc[completeness["needs_query"], "_compound_key"]
    )
    query_input = universe.loc[
        universe["_compound_key"].isin(query_keys),
        ["compound", "smiles", "cas"],
    ].reset_index(drop=True)
    return EPIResolution(
        results=results,
        raw_results=pd.DataFrame(),
        errors=pd.DataFrame(),
        completeness=completeness,
        provenance=provenance,
        match_audit=match_audit,
        conflict_audit=conflicts,
        query_attempts=pd.DataFrame(),
        query_input=query_input,
    )
```

Implement the referenced private helpers in the same module with no Streamlit dependencies. `merge_matched_fields` must sort uploaded rows by `source_priority`, then session rows; record unequal non-null candidates in `conflict_audit`; and fill only null fields after the adopted value is set.

Implement `merge_network_epi` by treating network values as priority 20,000, filling nulls only, appending raw/error rows, rebuilding completeness, and converting `activity_callback` events into `EPI_Query_Attempts`.

`classify_completeness` must implement these exact rules:

```python
recognized = results[list(ENDPOINT_KEYS)].notna().any(axis=1)
explicit_failure = results.get(
    "status",
    pd.Series("", index=results.index, dtype="string"),
).astype("string").str.casefold().eq("failed")
core_complete = results[list(CORE_MODEL_FIELDS)].apply(
    pd.to_numeric,
    errors="coerce",
).notna().all(axis=1)
complete = recognized & ~explicit_failure
if require_core:
    complete &= core_complete
completeness["needs_query"] = ~complete
```

`prepare_universe` must merge identifier-completion molecular weight into `molecular_weight` before this check. `match_sources` must emit exactly one audit row per source row with `match_method`, `match_status`, and `_compound_key`. `merge_network_epi` must recompute completeness and set its returned `query_input` to every still-unmatched, failed, or core-incomplete row.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_supplement -v
```

Expected: all matching, precedence, conflict, and query-target tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/episuite_supplement.py tests/test_episuite_supplement.py
git commit -m "feat: resolve EPI supplements and missing query targets"
```

---

### Task 4: Add the contributor-scoped same-session EPI result pool

**Files:**
- Create: `src/episuite_result_pool.py`
- Create: `tests/test_episuite_result_pool.py`
- Modify: `pages/3_EPISuite环境归趋.py`
- Modify: `tests/test_structure_preparation_page_contract.py`

**Interfaces:**
- Produces: `upsert_epi_pool(state, contributor_id, results, provenance) -> None`
- Produces: `read_epi_pool(state) -> tuple[pd.DataFrame, pd.DataFrame]`
- Produces: `remove_epi_pool_contributor(state, contributor_id) -> None`
- Produces: `clear_epi_pool(state) -> None`

- [ ] **Step 1: Write failing pool tests**

Create `tests/test_episuite_result_pool.py`:

```python
import unittest

import pandas as pd

from src.episuite_result_pool import (
    clear_epi_pool,
    read_epi_pool,
    remove_epi_pool_contributor,
    upsert_epi_pool,
)


class EPIResultPoolTests(unittest.TestCase):
    def test_contributors_merge_and_can_be_removed_independently(self):
        state = {}
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["A"], "log_kow": [1.0]}),
            pd.DataFrame({"compound": ["A"], "field": ["log_kow"]}),
        )
        upsert_epi_pool(
            state,
            "epi-page:two",
            pd.DataFrame({"compound": ["B"], "log_kow": [2.0]}),
            pd.DataFrame({"compound": ["B"], "field": ["log_kow"]}),
        )

        results, _ = read_epi_pool(state)
        self.assertEqual(results["compound"].tolist(), ["A", "B"])

        remove_epi_pool_contributor(state, "epi-page:one")
        results, _ = read_epi_pool(state)
        self.assertEqual(results["compound"].tolist(), ["B"])

        clear_epi_pool(state)
        self.assertEqual(read_epi_pool(state)[0].shape[0], 0)

    def test_pool_state_contains_serializable_records_not_dataframes(self):
        state = {}
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["A"], "log_kow": [1.0]}),
            pd.DataFrame(),
        )
        self.assertIsInstance(state["shared_epi_result_pool"], dict)
        self.assertIsInstance(
            state["shared_epi_result_pool"]["contributors"]["epi-page:one"]["results"],
            list,
        )
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_result_pool -v
```

Expected: module import failure.

- [ ] **Step 3: Implement pool helpers**

Create `src/episuite_result_pool.py`:

```python
from __future__ import annotations

import pandas as pd


POOL_KEY = "shared_epi_result_pool"
POOL_VERSION = 1


def upsert_epi_pool(state, contributor_id, results, provenance) -> None:
    pool = dict(state.get(POOL_KEY) or {})
    contributors = dict(pool.get("contributors") or {})
    contributors[str(contributor_id)] = {
        "results": pd.DataFrame(results).to_dict("records"),
        "provenance": pd.DataFrame(provenance).to_dict("records"),
    }
    state[POOL_KEY] = {
        "version": POOL_VERSION,
        "contributors": contributors,
    }


def read_epi_pool(state) -> tuple[pd.DataFrame, pd.DataFrame]:
    pool = state.get(POOL_KEY) or {}
    if pool.get("version") != POOL_VERSION:
        return pd.DataFrame(), pd.DataFrame()
    result_frames = []
    provenance_frames = []
    for contributor_id, payload in (pool.get("contributors") or {}).items():
        results = pd.DataFrame(payload.get("results") or [])
        provenance = pd.DataFrame(payload.get("provenance") or [])
        if not results.empty:
            results["pool_contributor_id"] = contributor_id
            result_frames.append(results)
        if not provenance.empty:
            provenance["pool_contributor_id"] = contributor_id
            provenance_frames.append(provenance)
    return (
        pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame(),
        pd.concat(provenance_frames, ignore_index=True)
        if provenance_frames
        else pd.DataFrame(),
    )


def remove_epi_pool_contributor(state, contributor_id) -> None:
    pool = dict(state.get(POOL_KEY) or {})
    contributors = dict(pool.get("contributors") or {})
    contributors.pop(str(contributor_id), None)
    if contributors:
        state[POOL_KEY] = {
            "version": POOL_VERSION,
            "contributors": contributors,
        }
    else:
        state.pop(POOL_KEY, None)


def clear_epi_pool(state) -> None:
    state.pop(POOL_KEY, None)
```

- [ ] **Step 4: Integrate the independent EPI page**

In `pages/3_EPISuite环境归趋.py`:

```python
from src.episuite_result_pool import (
    clear_epi_pool,
    remove_epi_pool_contributor,
    upsert_epi_pool,
)

POOL_CONTRIBUTOR_KEY = "epi_pool_contributor_id"


def publish_epi_results_to_pool(results, provenance=None):
    contributor_id = f"epi-page:{st.session_state['epi_input_signature']}"
    previous = st.session_state.get(POOL_CONTRIBUTOR_KEY)
    if previous and previous != contributor_id:
        remove_epi_pool_contributor(st.session_state, previous)
    upsert_epi_pool(
        st.session_state,
        contributor_id,
        results,
        pd.DataFrame() if provenance is None else provenance,
    )
    st.session_state[POOL_CONTRIBUTOR_KEY] = contributor_id
```

Call `publish_epi_results_to_pool` after successful automatic results and after validated uploaded-result merge. Update `clear_cached_input` to remove only the stored contributor. Add a separately confirmed “清空当前会话 EPI 结果” button calling `clear_epi_pool`.

Replace default-first-sheet upload parsing with `inspect_epi_workbook`. Pass the recognized `Core_Summary` or `EPI_Results` sheet to `parse_uploaded_result`; when neither exists, show a sheet selector and parse only the selected sheet. This makes the independent page consume its own `EPISuite_Fate_Report.xlsx` correctly.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_result_pool tests.test_structure_preparation_page_contract tests.test_episuite_cas_values -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/episuite_result_pool.py pages/3_EPISuite环境归趋.py tests/test_episuite_result_pool.py tests/test_structure_preparation_page_contract.py
git commit -m "feat: share validated EPI results across pages"
```

---

### Task 5: Extract and verify the existing multi-file screening preparation

**Files:**
- Create: `src/multi_file_screening.py`
- Create: `tests/test_multi_file_screening.py`
- Modify: `pages/0_综合筛查流程.py`
- Modify: `tests/test_cp_screening_workflow.py`
- Modify: `tests/test_structure_preparation_page_contract.py`

**Interfaces:**
- Produces: `PrimaryWorkbook`
- Produces: `SampleColumnMapping`
- Produces: `MultiFileScreeningResult`
- Produces: `read_primary_workbooks(records) -> list[PrimaryWorkbook]`
- Produces: `default_sample_mapping(sample) -> SampleColumnMapping`
- Produces: `prepare_multi_file_screening(samples, mappings, detection_threshold, axis_ranges) -> MultiFileScreeningResult`

- [ ] **Step 1: Write a failing shared-module semantic test**

Create `tests/test_multi_file_screening.py`:

```python
import unittest

import pandas as pd

from src.multi_file_screening import (
    PrimaryWorkbook,
    SampleColumnMapping,
    prepare_multi_file_screening,
)
from src.r_screening_replica.schema import ScreeningAxisRanges


class MultiFileScreeningTests(unittest.TestCase):
    def test_files_remain_samples_and_file_means_precede_df(self):
        samples = [
            PrimaryWorkbook(
                file_name="A.xlsx",
                sample_id="A",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [200000.0],
                        "P2": [0.0],
                        "SMILES": ["CCO"],
                    }
                ),
            ),
            PrimaryWorkbook(
                file_name="B.xlsx",
                sample_id="B",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [300000.0],
                        "P2": [300000.0],
                        "SMILES": ["CCO"],
                    }
                ),
            ),
        ]
        mappings = {
            sample.sample_id: SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="P1",
                group_area_cols=("P1", "P2"),
                smiles_col="SMILES",
            )
            for sample in samples
        }

        result = prepare_multi_file_screening(
            samples,
            mappings,
            detection_threshold=100000.0,
            axis_ranges=ScreeningAxisRanges(),
        )

        self.assertEqual(
            set(result.group_area_mean_by_sample["source_sample_id"]),
            {"A", "B"},
        )
        df_row = result.df_table.set_index("compound").loc["X"]
        self.assertEqual(df_row["total_sample_count"], 2)
        self.assertEqual(df_row["detected_sample_count"], 1)
        self.assertEqual(result.representative_table["Name"].tolist(), ["X"])
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_multi_file_screening -v
```

Expected: module import failure.

- [ ] **Step 3: Move pure page-0 logic into the shared module**

Create `src/multi_file_screening.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pandas as pd


@dataclass
class PrimaryWorkbook:
    file_name: str
    sample_id: str
    data: pd.DataFrame
    content_bytes: bytes = b""


@dataclass(frozen=True)
class SampleColumnMapping:
    compound_col: str
    formula_col: str
    peak_area_col: str
    group_area_cols: tuple[str, ...] = ()
    mol_column: str | None = None
    smiles_col: str | None = None
    cas_col: str | None = None


@dataclass
class MultiFileScreeningResult:
    normalized_samples: list[dict]
    representative_table: pd.DataFrame
    structure_preparation: pd.DataFrame
    input_file_mappings: pd.DataFrame
    df_table: pd.DataFrame
    sample_peak_area: pd.DataFrame
    group_area_raw_long: pd.DataFrame
    group_area_mean_by_sample: pd.DataFrame
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    charts: dict = field(default_factory=dict)
    warnings: pd.DataFrame = field(default_factory=pd.DataFrame)
```

Move these functions from page 0 without changing their calculations:

- `parse_uploaded_workbooks` as `read_primary_workbooks`;
- `is_group_area_column`;
- `guess_peak_area_column`;
- `sample_mapping_defaults` as `default_sample_mapping`;
- `normalize_samples_for_mappings`;
- `build_upload_structure_preparation_preview`;
- `build_representative_screening_table`;
- `collect_front_half` as `prepare_multi_file_screening`;
- the private helpers directly used by those functions.

Convert between `SampleColumnMapping` and the existing dict layout only at the page boundary. Populate `input_file_mappings` with one row per primary file and JSON-safe lists for Group Area columns.

- [ ] **Step 4: Switch page 0 and existing tests to shared imports**

Replace page-local pure function definitions with imports:

```python
from src.multi_file_screening import (
    PrimaryWorkbook,
    SampleColumnMapping,
    default_sample_mapping,
    prepare_multi_file_screening,
    read_primary_workbooks,
)
```

Keep only Streamlit widget rendering in the page. Convert widget selections to `SampleColumnMapping`.

- [ ] **Step 5: Run multi-file regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_multi_file_screening tests.test_cp_screening_workflow tests.test_structure_preparation_page_contract -v
```

Expected: all pass, including existing file-level mean, DF, and ToxPi-input tests.

- [ ] **Step 6: Commit**

```powershell
git add src/multi_file_screening.py pages/0_综合筛查流程.py tests/test_multi_file_screening.py tests/test_cp_screening_workflow.py tests/test_structure_preparation_page_contract.py
git commit -m "refactor: share multi-file screening preparation"
```

---

### Task 6: Teach the auto-query workflow to consume prepared multi-file input and EPI seed data

**Files:**
- Modify: `src/auto_query_workflow.py`
- Modify: `src/episuite_supplement.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_episuite_supplement.py`

**Interfaces:**
- Produces: `AutoWorkflowPreparedInput`
- Produces: `auto_input_from_multi_file_result(result: MultiFileScreeningResult) -> AutoWorkflowPreparedInput`
- Adds `run_auto_query_workflow` keyword parameters: `prepared_input: AutoWorkflowPreparedInput | None = None`, `epi_uploaded_results: pd.DataFrame | None = None`, and `epi_pool_results: pd.DataFrame | None = None`
- Preserves: legacy single-DataFrame callers.

- [ ] **Step 1: Write failing workflow tests**

Add to `tests/test_auto_query_workflow.py`:

```python
def complete_epi_rows(compounds):
    compounds = list(compounds)
    count = len(compounds)
    return pd.DataFrame(
        {
            "compound": compounds,
            "smiles": ["CC"] * count,
            "cas": [""] * count,
            "status": ["success"] * count,
            "molecular_weight": [100.0] * count,
            "henry_atm_m3_mol": [1.0e-5] * count,
            "log_kow": [2.0] * count,
            "level3_air_half_life_hours": [10.0] * count,
            "level3_water_half_life_hours": [20.0] * count,
            "level3_soil_half_life_hours": [30.0] * count,
            "log_baf": [1.0] * count,
        }
    )


@patch("src.auto_query_workflow.run_epi_web_batch")
@patch("src.auto_query_workflow.run_identifier_completion_batch")
def test_complete_epi_seed_skips_network(
    self,
    run_identifier,
    run_epi,
):
    run_identifier.return_value = (
        _completed_identifier_rows(["Compound A"]),
        pd.DataFrame(),
    )
    seed = complete_epi_rows(["Compound A"])
    prepared = AutoWorkflowPreparedInput(
        mapping=AutoWorkflowMapping(),
        prepared_input=_workflow_input_rows(["Compound A"]),
        representative_table=build_representative_table(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowMapping(
                compound_col="Name",
                formula_col="formula",
                peak_area_col="Group_Area",
                group_area_cols=["Group_Area"],
                smiles_col="smiles",
            ),
        ),
        local_tables=OrderedDict(),
        local_charts=OrderedDict(),
        local_warnings=[],
    )

    result = run_auto_query_workflow(
        _workflow_input_rows(["Compound A"]),
        AutoWorkflowConfig(
            run_r_replicate_df=False,
            run_identifier=True,
            run_epi=True,
            identifier_delay_seconds=0,
            epi_delay_seconds=0,
        ),
        prepared_input=prepared,
        epi_uploaded_results=seed,
    )

    run_epi.assert_not_called()
    self.assertEqual(result.tables["EPI_Results"]["compound"].tolist(), ["Compound A"])
    self.assertTrue(result.tables["EPI_Completeness"]["needs_query"].eq(False).all())


@patch("src.auto_query_workflow.run_epi_web_batch")
@patch("src.auto_query_workflow.run_identifier_completion_batch")
def test_partial_epi_seed_queries_only_missing_compounds(
    self,
    run_identifier,
    run_epi,
):
    compounds = ["Compound A", "Compound B"]
    run_identifier.return_value = (_completed_identifier_rows(compounds), pd.DataFrame())
    run_epi.return_value = (
        complete_epi_rows(["Compound B"]),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    run_auto_query_workflow(
        _workflow_input_rows(compounds),
        AutoWorkflowConfig(
            run_r_replicate_df=False,
            run_identifier=True,
            run_epi=True,
            identifier_delay_seconds=0,
            epi_delay_seconds=0,
        ),
        epi_uploaded_results=complete_epi_rows(["Compound A"]),
    )
    self.assertEqual(
        run_epi.call_args.args[0]["compound"].tolist(),
        ["Compound B"],
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_complete_epi_seed_skips_network tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_partial_epi_seed_queries_only_missing_compounds -v
```

Expected: missing `AutoWorkflowPreparedInput` and unsupported keyword arguments.

- [ ] **Step 3: Add prepared-input type and legacy adapter**

In `src/auto_query_workflow.py`:

```python
@dataclass
class AutoWorkflowPreparedInput:
    mapping: AutoWorkflowMapping
    prepared_input: pd.DataFrame
    representative_table: pd.DataFrame
    local_tables: OrderedDict[str, pd.DataFrame] = field(default_factory=OrderedDict)
    local_charts: OrderedDict[str, AutoWorkflowChart] = field(default_factory=OrderedDict)
    local_warnings: list[str] = field(default_factory=list)
```

At workflow start:

```python
if prepared_input is None:
    prepared_input = prepare_legacy_auto_input(input_df, config)
mapping = prepared_input.mapping
prepared_frame = prepared_input.prepared_input
representative = prepared_input.representative_table
tables["Structure_Preparation"] = prepared_frame
```

When local screening is selected, use `prepared_input.local_tables/charts` when non-empty; otherwise preserve `_run_r_replicate_df`.

Add the conversion used by page 6:

```python
def auto_input_from_multi_file_result(
    result: MultiFileScreeningResult,
) -> AutoWorkflowPreparedInput:
    local_tables = OrderedDict(result.tables)
    local_tables["Input_File_Mappings"] = result.input_file_mappings
    local_tables["Structure_Preparation"] = result.structure_preparation
    local_tables["DF_Table"] = result.df_table
    local_tables["Sample_Peak_Area"] = result.sample_peak_area
    local_tables["Group_Area_Raw_Long"] = result.group_area_raw_long
    local_tables["Group_Area_Mean_By_Sample"] = result.group_area_mean_by_sample
    return AutoWorkflowPreparedInput(
        mapping=AutoWorkflowMapping(
            compound_col="Name",
            formula_col="formula",
            peak_area_col="Group_Area",
            group_area_cols=["Group_Area"],
            smiles_col="SMILES_input",
            cas_col="CAS_input",
        ),
        prepared_input=result.structure_preparation,
        representative_table=result.representative_table,
        local_tables=local_tables,
        local_charts=OrderedDict(result.charts),
        local_warnings=result.warnings.get("message", pd.Series(dtype=str)).tolist(),
    )
```

- [ ] **Step 4: Replace unconditional EPI querying with resolution/query/merge**

After identifier completion:

```python
resolution = resolve_epi_sources(
    epi_input,
    pd.DataFrame() if epi_uploaded_results is None else epi_uploaded_results,
    pd.DataFrame() if epi_pool_results is None else epi_pool_results,
    completed_identifiers=completed_identifiers,
    require_core=bool(config.run_pov_lrtp_toxpi),
)
attempt_events = []
if not resolution.query_input.empty:
    forward_epi_activity = activity_for(
        "EPI Suite 环境归趋",
        config.epi_timeout,
    )

    def record_epi_activity(event):
        attempt_events.append(dict(event))
        forward_epi_activity(event)

    network_results, network_raw, network_errors = run_epi_web_batch(
        resolution.query_input,
        api_url=config.epi_api_url,
        timeout=int(config.epi_timeout),
        delay_seconds=float(config.epi_delay_seconds),
        max_workers=int(config.epi_max_workers),
        cache_enabled=bool(config.cache_enabled),
        progress_callback=epi_progress,
        activity_callback=record_epi_activity,
    )
    resolution = merge_network_epi(
        resolution,
        network_results,
        network_raw,
        network_errors,
        attempt_events,
    )
epi_results = resolution.results
epi_raw_results = resolution.raw_results
epi_errors = resolution.errors
```

Export:

```python
tables["EPI_Results"] = resolution.results
tables["EPI_Raw_Results"] = resolution.raw_results
tables["EPI_Errors"] = resolution.errors
tables["EPI_Completeness"] = resolution.completeness
tables["EPI_Source_Provenance"] = resolution.provenance
tables["EPI_Match_Audit"] = resolution.match_audit
tables["EPI_Conflict_Audit"] = resolution.conflict_audit
tables["EPI_Query_Attempts"] = resolution.query_attempts
tables["EPI_Retry_Input"] = resolution.query_input.reset_index(drop=True)
```

Add the audit names to the EPI module export tuple and `PUBLIC_TABLE_NAMES`. Add `Input_File_Mappings` to the local-screening module export tuple and `PUBLIC_TABLE_NAMES`; populate it from `prepared_input.local_tables`.

- [ ] **Step 5: Run workflow and export tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_episuite_supplement -v
```

Expected: all pass; legacy single-file tests remain green.

- [ ] **Step 6: Commit**

```powershell
git add src/auto_query_workflow.py src/episuite_supplement.py tests/test_auto_query_workflow.py tests/test_episuite_supplement.py
git commit -m "feat: seed auto-query EPI from uploaded and session results"
```

---

### Task 7: Add multiple primary files and EPI mapping UI to the one-click page

**Files:**
- Modify: `pages/6_一键批量查询.py`
- Modify: `src/upload_state.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_upload_state.py`

**Interfaces:**
- Consumes: `PrimaryWorkbook`, `SampleColumnMapping`, `prepare_multi_file_screening`
- Consumes: `EPISupplementMapping`, `inspect_epi_workbook`, `parse_epi_supplement`, `suggest_primary_filename`
- Consumes: `read_epi_pool`
- Produces: settings-signature-safe primary/supplement mappings and `AutoWorkflowPreparedInput`.

- [ ] **Step 1: Write failing AppTest/page behavior tests**

Add an AppTest helper that injects two cached primary workbook records. Add tests:

```python
def test_page_6_accepts_multiple_primary_files_and_keeps_both_in_settings(self):
    app = _app_test_with_cached_workbooks(
        [
            ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
            ("Lake-B.xlsx", _app_test_workbook_bytes("Compound B")),
        ]
    )
    self.assertEqual(len(app.exception), 0)
    self.assertIn("Lake-A.xlsx", app.session_state["auto_query_primary_file_names"])
    self.assertIn("Lake-B.xlsx", app.session_state["auto_query_primary_file_names"])


def test_page_6_blocks_duplicate_primary_filenames(self):
    app = _app_test_with_cached_workbooks(
        [
            ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
            ("Lake-A.xlsx", _app_test_workbook_bytes("Compound B")),
        ]
    )
    self.assertTrue(
        any("文件名重复" in message.value for message in app.error)
    )


def test_page_6_blocks_duplicate_casefolded_sample_stems(self):
    app = _app_test_with_cached_workbooks(
        [
            ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
            ("lake-a.xls", _app_test_workbook_bytes("Compound B")),
        ]
    )
    self.assertTrue(
        any("样品名称重复" in message.value for message in app.error)
    )


def test_page_6_shows_epi_supplement_controls_when_pov_is_selected(self):
    app = _app_test_with_cached_workbooks(
        [("Lake-A.xlsx", _app_test_workbook_bytes("Compound A"))]
    )
    next(box for box in app.checkbox if box.label == "Pov-LRTP / PBM / ToxPi").check().run()
    self.assertTrue(
        any(
            uploader.label == "上传 EPI 补充 Excel"
            for uploader in app.get("file_uploader")
        )
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_upload_state -v
```

Expected: page remains single-file and lacks new state/controls.

- [ ] **Step 3: Convert primary upload and mapping**

Add `from pathlib import Path`, then change:

```python
uploaded_files = st.file_uploader(
    "上传一个或多个 Excel 文件",
    type=["xlsx", "xls"],
    accept_multiple_files=True,
    key="auto_query_upload",
)
```

Store all uploads with existing `store_uploads`. Read them through `read_primary_workbooks`. Reject duplicate case-insensitive full filenames and duplicate case-insensitive filename stems, because stems are the existing sample IDs:

```python
primary_names = [sample.file_name for sample in samples]
duplicates = sorted(
    name
    for name in primary_names
    if [item.casefold() for item in primary_names].count(name.casefold()) > 1
)
if duplicates:
    st.error("主 Excel 文件名重复，请重命名后重新上传：" + "、".join(duplicates))
    st.stop()

sample_stems = [Path(name).stem for name in primary_names]
duplicate_stems = sorted(
    stem
    for stem in sample_stems
    if [item.casefold() for item in sample_stems].count(stem.casefold()) > 1
)
if duplicate_stems:
    st.error("主 Excel 样品名称重复，请重命名后重新上传：" + "、".join(duplicate_stems))
    st.stop()
```

Render one mapping tab per sample using page-0-equivalent widgets and keys derived from `(prefix, full filename, index)`. Build `dict[sample_id, SampleColumnMapping]`.

- [ ] **Step 4: Add conditional EPI supplements and completeness preview**

Add supplement input state keys:

```python
EPI_SUPPLEMENT_CACHE_KEYS = (
    "auto_query_epi_supplement_files",
    "auto_query_epi_supplement_signature",
)
```

When `run_epi or run_pov_toxpi`:

```python
epi_uploads = st.file_uploader(
    "上传 EPI 补充 Excel",
    type=["xlsx", "xls"],
    accept_multiple_files=True,
    key="auto_query_epi_supplements",
)
```

For each file, inspect sheets and render primary-file, sheet, identifier-column, endpoint-column, and priority controls. Use `suggest_primary_filename` only as the default filename selection. Parse supplements, read the session pool, and show the counts returned by a preliminary `resolve_epi_sources`.

Persist supplement uploads through `store_uploads`. Build the checkpoint input signature from both upload groups:

```python
workflow_input_signature = settings_signature(
    {
        "primary": upload_signature(active_uploads),
        "epi_supplements": (
            upload_signature(active_epi_supplements)
            if active_epi_supplements
            else ""
        ),
    }
)
```

Use `workflow_input_signature` in `AutoWorkflowCheckpointContext` and recovery comparison. Any primary or supplement byte change clears current results/checkpoint state but does not clear the same-session EPI pool.

- [ ] **Step 5: Include all mappings in settings and run inputs**

Add `from dataclasses import asdict` and import `upload_signature` from `src.upload_state`, then build:

```python
result_settings["primary_mappings"] = [
    {
        "sample_id": sample_id,
        **asdict(mapping),
    }
    for sample_id, mapping in sample_mappings.items()
]
result_settings["epi_supplements"] = [
    asdict(mapping) for mapping in supplement_mappings
]
```

Prepare multi-file state, convert it to `AutoWorkflowPreparedInput`, and pass:

```python
result = run_auto_query_workflow(
    prepared_auto_input.prepared_input,
    config=config,
    prepared_input=prepared_auto_input,
    epi_uploaded_results=parsed_supplements,
    epi_pool_results=session_pool_results,
    progress_callback=update_progress,
    activity_callback=update_activity,
    checkpoint_context=checkpoint_context,
    checkpoint_callback=handle_checkpoint,
)
```

- [ ] **Step 6: Run AppTest and multi-file regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_upload_state tests.test_multi_file_screening tests.test_cp_screening_workflow -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add pages/6_一键批量查询.py src/upload_state.py tests/test_auto_query_workflow.py tests/test_upload_state.py
git commit -m "feat: accept multi-file EPI supplements in auto-query"
```

---

### Task 8: Retry only failed EPI rows and update only EPI-dependent outputs

**Files:**
- Modify: `src/auto_query_workflow.py`
- Modify: `pages/6_一键批量查询.py`
- Modify: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Produces: `retry_auto_workflow_epi_failures(result, config, progress_callback=None, activity_callback=None) -> AutoWorkflowResult`
- Preserves: successful/uploaded EPI rows and unrelated module tables/charts.

- [ ] **Step 1: Write a failing selective-retry test**

Add:

```python
@patch("src.auto_query_workflow.run_epi_web_batch")
def test_retry_epi_failures_queries_only_retry_input_and_preserves_unrelated_tables(
    self,
    run_epi,
):
    original = _result_with_epi_retry_input(["Failed B"])
    original.tables["CompTox_Summary"] = pd.DataFrame(
        {"compound": ["Unrelated"], "status": ["ok"]}
    )
    run_epi.return_value = (
        complete_epi_rows(["Failed B"]),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    retried = retry_auto_workflow_epi_failures(
        original,
        AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
    )

    self.assertEqual(run_epi.call_args.args[0]["compound"].tolist(), ["Failed B"])
    pd.testing.assert_frame_equal(
        retried.tables["CompTox_Summary"],
        original.tables["CompTox_Summary"],
    )
    self.assertTrue(retried.tables["EPI_Retry_Input"].empty)
```

Add a second test with `run_pov_lrtp_toxpi=True` asserting `_run_pov_lrtp_toxpi` is called once and its old tables/charts are replaced.

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_retry_epi_failures_queries_only_retry_input_and_preserves_unrelated_tables -v
```

Expected: retry function missing.

- [ ] **Step 3: Implement selective retry**

In `src/auto_query_workflow.py`:

```python
def retry_auto_workflow_epi_failures(
    result: AutoWorkflowResult,
    config: AutoWorkflowConfig,
    progress_callback: ProgressCallback | None = None,
    activity_callback: ActivityCallback | None = None,
) -> AutoWorkflowResult:
    retry_input = result.tables.get("EPI_Retry_Input", pd.DataFrame()).copy()
    if retry_input.empty:
        return result
    network_results, network_raw, network_errors = run_epi_web_batch(
        retry_input,
        api_url=config.epi_api_url,
        timeout=int(config.epi_timeout),
        delay_seconds=float(config.epi_delay_seconds),
        max_workers=int(config.epi_max_workers),
        cache_enabled=bool(config.cache_enabled),
        progress_callback=(
            None
            if progress_callback is None
            else lambda done, total, label: progress_callback(
                "EPI Suite 环境归趋", done, total, label
            )
        ),
        activity_callback=activity_callback,
    )
    updated = merge_retry_into_result(
        result,
        network_results,
        network_raw,
        network_errors,
    )
    if config.run_pov_lrtp_toxpi:
        updated = rebuild_epi_dependents(updated, config)
    return updated
```

Implement `merge_retry_into_result` through `merge_network_epi`; preserve non-EPI tables and unrelated charts by copying the ordered dictionaries and replacing only EPI and Pov/ToxPi keys.

- [ ] **Step 4: Add page button and checkpoint update**

Render the button only when `EPI_Retry_Input` is non-empty. On click:

```python
retried_result = retry_auto_workflow_epi_failures(
    saved_result,
    config,
    progress_callback=update_progress,
    activity_callback=update_activity,
)
st.session_state["auto_query_workflow_result"] = retried_result
charts = build_auto_workflow_charts(retried_result)
retried_result.charts = charts
st.session_state["auto_query_workflow_charts"] = charts
```

Rebuild the EPI module workbook, dependent Pov/ToxPi module workbook, partial/full ZIP, and save a completed/failed checkpoint through the existing handler.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: all pass and unrelated module mocks are not called by retry.

- [ ] **Step 6: Commit**

```powershell
git add src/auto_query_workflow.py pages/6_一键批量查询.py tests/test_auto_query_workflow.py
git commit -m "feat: retry only unresolved EPI rows"
```

---

### Task 9: Upgrade checkpoint metadata for multiple files and retry recovery

**Files:**
- Modify: `src/auto_query_checkpoint.py`
- Modify: `pages/6_一键批量查询.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_upload_state.py`

**Interfaces:**
- Changes: `LoadedAutoQueryCheckpoint.input_filenames: tuple[str, ...]`
- Changes the third `save_checkpoint` parameter to `input_filenames: Iterable[str] | str`
- Reads: schema v1 `input_filename` and schema v2 `input_filenames`.

- [ ] **Step 1: Write failing schema-v2/backward-compatibility tests**

Add:

```python
def test_checkpoint_round_trips_multiple_input_filenames_and_retry_table(self):
    result = _result_with_epi_retry_input(["B"])
    with TemporaryDirectory() as root:
        token = generate_run_token()
        save_checkpoint(
            token,
            _checkpoint_for(result),
            ["A.xlsx", "B.xlsx"],
            OrderedDict(),
            root=root,
        )
        loaded = load_checkpoint(token, root=root)
    self.assertEqual(loaded.input_filenames, ("A.xlsx", "B.xlsx"))
    self.assertEqual(
        loaded.checkpoint.result.tables["EPI_Retry_Input"]["compound"].tolist(),
        ["B"],
    )


def test_schema_v1_input_filename_loads_as_singleton_tuple(self):
    # Save a v2 checkpoint, rewrite only manifest schema/input fields to the
    # legacy representation, then prove the loader accepts it.
```

The second test must rewrite the isolated temporary manifest to:

```python
manifest["schema_version"] = 1
manifest["input_filename"] = manifest.pop("input_filenames")[0]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_upload_state tests.test_auto_query_workflow -v
```

Expected: current save accepts only one filename and loader exposes `input_filename`.

- [ ] **Step 3: Implement schema v2 and safe filename lists**

In `src/auto_query_checkpoint.py`:

```python
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}


@dataclass(frozen=True)
class LoadedAutoQueryCheckpoint:
    checkpoint: AutoWorkflowCheckpoint
    input_filenames: tuple[str, ...]
    module_workbooks: OrderedDict[str, AutoWorkflowModuleWorkbook]
    manifest: dict[str, Any]
```

Save:

```python
if isinstance(input_filenames, (str, Path)):
    input_filenames = (str(input_filenames),)
input_filenames = tuple(_input_basename(name) for name in input_filenames)
manifest["input_filenames"] = list(input_filenames)
```

Load:

```python
schema_version = manifest.get("schema_version")
if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
    raise CheckpointStorageError("检查点版本或令牌摘要不匹配")
input_filenames = (
    tuple(manifest["input_filenames"])
    if schema_version >= 2
    else (manifest["input_filename"],)
)
input_filenames = tuple(_input_basename(name) for name in input_filenames)
```

Update `cleanup_expired_checkpoints` to accept every version in `SUPPORTED_SCHEMA_VERSIONS`, so valid schema-v1 runs are not treated as corrupt. Update page 6 to pass all primary filenames. Keep mappings, EPI audits, and retry targets as ordinary checkpoint tables so content-addressed persistence remains unchanged.

- [ ] **Step 4: Run checkpoint and recovery tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_upload_state tests.test_auto_query_workflow -v
```

Expected: all schema v1/v2, AppTest recovery, payload, and no-rerun tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/auto_query_checkpoint.py pages/6_一键批量查询.py tests/test_auto_query_workflow.py tests/test_upload_state.py
git commit -m "feat: recover multi-file EPI checkpoints"
```

---

### Task 10: Add cache diagnostics and expired-entry cleanup

**Files:**
- Modify: `src/query_cache.py`
- Modify: `pages/3_EPISuite环境归趋.py`
- Modify: `pages/6_一键批量查询.py`
- Modify: `tests/test_query_cache.py`

**Interfaces:**
- Produces: `QueryCacheStats`
- Produces: `query_cache_stats(path=None, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS) -> QueryCacheStats`
- Produces: `prune_expired_cache(path=None, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS, compact=True) -> QueryCacheStats`

- [ ] **Step 1: Write failing cache diagnostic/prune tests**

Add to `tests/test_query_cache.py`:

```python
def test_stats_and_prune_expired_keep_live_entries(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "queries.sqlite3"
        cache = QueryCache(path)
        cache.set("epi_web_submit", "live", {"ok": True}, created_at=time.time())
        cache.set(
            "epi_web_submit",
            "expired",
            {"old": True},
            created_at=time.time() - 120,
        )
        before = query_cache_stats(path, ttl_seconds=30)
        self.assertEqual(before.total_rows, 2)
        self.assertEqual(before.epi_rows, 2)
        self.assertEqual(before.expired_rows, 1)

        after = prune_expired_cache(path, ttl_seconds=30, compact=False)
        self.assertEqual(after.total_rows, 1)
        self.assertEqual(cache.get("epi_web_submit", "live"), {"ok": True})
        self.assertIsNone(cache.get("epi_web_submit", "expired"))


def test_stats_for_missing_cache_are_zero(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        stats = query_cache_stats(Path(tmpdir) / "missing.sqlite3")
    self.assertEqual(stats.total_rows, 0)
    self.assertEqual(stats.size_bytes, 0)
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_cache -v
```

Expected: missing stats/prune interfaces.

- [ ] **Step 3: Implement diagnostics and pruning**

In `src/query_cache.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryCacheStats:
    path: Path
    size_bytes: int
    total_rows: int
    epi_rows: int
    expired_rows: int
    oldest_created_at: float | None
    newest_created_at: float | None


def query_cache_stats(path=None, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS):
    cache_path = Path(path) if path is not None else current_cache_path()
    if not cache_path.is_file():
        return QueryCacheStats(cache_path, 0, 0, 0, 0, None, None)
    cutoff = time.time() - float(ttl_seconds)
    with contextlib.closing(sqlite3.connect(cache_path, timeout=30)) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN source = 'epi_web_submit' THEN 1 ELSE 0 END),
                SUM(CASE WHEN created_at < ? THEN 1 ELSE 0 END),
                MIN(created_at),
                MAX(created_at)
            FROM query_cache
            """,
            (cutoff,),
        ).fetchone()
    return QueryCacheStats(
        cache_path,
        cache_path.stat().st_size,
        int(row[0] or 0),
        int(row[1] or 0),
        int(row[2] or 0),
        row[3],
        row[4],
    )


def prune_expired_cache(
    path=None,
    ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
    compact=True,
):
    cache_path = Path(path) if path is not None else current_cache_path()
    if not cache_path.is_file():
        return query_cache_stats(cache_path, ttl_seconds)
    cutoff = time.time() - float(ttl_seconds)
    with contextlib.closing(sqlite3.connect(cache_path, timeout=5)) as conn:
        with conn:
            conn.execute(
                "DELETE FROM query_cache WHERE created_at < ?",
                (cutoff,),
            )
        if compact:
            try:
                conn.execute("VACUUM")
            except sqlite3.OperationalError:
                pass
    return query_cache_stats(cache_path, ttl_seconds)
```

- [ ] **Step 4: Add page controls**

On pages 3 and 6, show size, total rows, EPI rows, newest time, and expired rows. Add “清理过期记录”. Guard full deletion with:

```python
confirm_clear = st.checkbox(
    "我确认清空全部查询缓存",
    key=f"{prefix}_confirm_clear_query_cache",
)
if st.button(
    "清空全部查询缓存",
    disabled=not confirm_clear,
    key=f"{prefix}_clear_all_query_cache",
):
    clear_query_cache()
    st.success("全部查询缓存已清空。")
```

- [ ] **Step 5: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_cache tests.test_episuite_cas_values tests.test_auto_query_workflow -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/query_cache.py pages/3_EPISuite环境归趋.py pages/6_一键批量查询.py tests/test_query_cache.py
git commit -m "feat: inspect and prune local query cache"
```

---

### Task 11: Validate exports, cross-page reuse, multi-file recovery, and the full application

**Files:**
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_structure_preparation_page_contract.py`
- Modify: `tests/test_upload_state.py`
- Modify: `README.md` only if existing usage text claims page 6 accepts one file.

**Interfaces:**
- Verifies the completed user-visible contract; produces no new production interface.

- [ ] **Step 1: Add final behavior tests**

Add AppTest/export tests that:

1. put a validated EPI result into `shared_epi_result_pool`;
2. load page 6 with two cached primary workbooks;
3. select EPI and Pov/ToxPi;
4. prove `run_epi_web_batch` receives only the unresolved compound;
5. parse the EPI module download and assert these sheets:

```python
{
    "EPI_Results",
    "EPI_Raw_Results",
    "EPI_Errors",
    "EPI_Completeness",
    "EPI_Source_Provenance",
    "EPI_Match_Audit",
    "EPI_Conflict_Audit",
    "EPI_Query_Attempts",
}
```

6. parse the complete workbook and assert `Input_File_Mappings`, `Group_Area_Mean_By_Sample`, and `DF_Table` preserve both primary files;
7. parse the full/partial ZIP and verify the same EPI audit sheets survive;
8. recover the checkpoint and verify both input filenames, mappings, pool-used rows, and retry targets;
9. click a download and prove run state and network-call counts are unchanged.

- [ ] **Step 2: Run the final targeted suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_retry tests.test_batch_runner tests.test_query_cache tests.test_episuite_cas_values tests.test_episuite_supplement tests.test_episuite_result_pool tests.test_multi_file_screening tests.test_cp_screening_workflow tests.test_upload_state tests.test_auto_query_workflow tests.test_structure_preparation_page_contract -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Run full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall app.py pages src
git diff --check
```

Expected:

- all tests pass;
- compileall exits 0;
- diff check produces no output.

- [ ] **Step 4: Inspect the final diff and requirements**

Run:

```powershell
git status --short
git diff --stat af3ca2d..HEAD
git diff --name-only af3ca2d..HEAD
```

Confirm:

- no unrelated untracked files are staged;
- all twelve acceptance criteria in the design have a test or direct verification;
- API cache keys remain CAS-sensitive;
- uploaded EPI values never enter `query_cache`;
- page 0 multi-file results remain unchanged.

- [ ] **Step 5: Request code review**

Invoke `superpowers:requesting-code-review` with:

- description: multi-file one-click input, EPI supplement/pool/minimal-query/retry/cache/checkpoint support;
- requirements: `docs/superpowers/specs/2026-07-24-multi-file-epi-supplement-design.md`;
- base SHA: `af3ca2d`;
- head SHA: current `HEAD`.

Fix all Critical and Important findings, then rerun Step 2 and Step 3.

- [ ] **Step 6: Commit final test/documentation adjustments**

```powershell
git add tests/test_auto_query_workflow.py tests/test_structure_preparation_page_contract.py tests/test_upload_state.py README.md
git commit -m "test: verify multi-file EPI recovery workflow"
```

If `README.md` did not require a change, omit it from `git add`.
