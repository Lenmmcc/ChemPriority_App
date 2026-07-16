# Auto Query Partial Checkpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every terminal one-click-query module immediately viewable and downloadable, persist recoverable partial results for 24 hours, and preserve the existing successful full-result ZIP contract.

**Architecture:** `run_auto_query_workflow()` remains synchronous and gains an optional typed checkpoint callback that receives cumulative immutable snapshots at module boundaries. A dedicated storage module serializes snapshots as versioned gzip JSON plus binary artifacts under `.cache/auto_query_runs/`, while page 6 owns Streamlit session state, recovery-token query parameters, download rendering, and graceful fallback when persistence or final export fails.

**Tech Stack:** Python 3, Streamlit 1.43+, pandas `orient="table"` JSON, gzip, openpyxl, `zipfile`, `unittest`, SHA-256, atomic `os.replace()` writes.

## Global Constraints

- Keep the workflow synchronous; do not add a background queue, worker thread, account system, permanent history, or cross-instance storage.
- Emit disk checkpoints only at module terminal boundaries, never after each compound.
- Preserve `AutoWorkflowResult`, query order, cache keys, timeouts, concurrency settings, and the existing successful ZIP layout.
- `checkpoint_callback` is optional and defaults to `None`; existing callers must continue to work unchanged.
- A checkpoint or module-export failure must not abort chemical queries.
- Store checkpoints for 24 hours from the most recent successful write under `.cache/auto_query_runs/<token_sha256>/`.
- Generate tokens with `secrets.token_urlsafe(32)`; accept only `[A-Za-z0-9_-]{32,128}` and never place the raw token in a server path or manifest.
- Serialize DataFrames with pandas `orient="table"` JSON compressed with gzip; never use Pickle.
- Write each artifact to a same-directory temporary path and commit it with `os.replace()`; replace `manifest.json` last.
- Use `on_click="ignore"` on every module, partial ZIP, and full ZIP download button.
- Raise the Streamlit floor to `streamlit>=1.43,<2`.
- Recovery restores results and downloads but does not automatically resume the next module; a rerun reuses the existing query cache.
- ECHA REACH and ECHA GHS are separate immediate checkpoint workbooks; the successful final ZIP continues to combine them in `05_ECHA/ECHA_Results.xlsx`.
- Do not modify `.gitignore`; its existing `.cache/` rule already covers the checkpoint root.

## File Structure

- Create `src/auto_query_checkpoint.py`: token validation, checkpoint serialization, atomic persistence, loading, deletion, and TTL cleanup only. It imports workflow data classes; the workflow module must not import this storage module.
- Create `tests/test_auto_query_checkpoint.py`: storage round-trip, corruption, token/path safety, TTL, and targeted deletion tests.
- Modify `src/auto_query_workflow.py`: checkpoint event models, module-boundary emissions, step-specific workbook export, and partial ZIP construction.
- Modify `pages/6_一键批量查询.py`: session keys, recovery query parameter, page-load restore, live module downloads, top-level failure handling, and non-rerunning downloads.
- Modify `tests/test_auto_query_workflow.py`: workflow event ordering, callback degradation, module exports, partial ZIP, and page source-contract tests.
- Modify `tests/test_upload_state.py`: page-6 checkpoint state-key and explicit-clear contract.
- Modify `requirements.txt`: Streamlit minimum version only.

---

### Task 1: Add typed module-boundary checkpoint events to the workflow

**Files:**
- Modify: `src/auto_query_workflow.py:1-15,227-245,287-664`
- Test: `tests/test_auto_query_workflow.py:1-30,595-700`

**Interfaces:**
- Consumes: existing `AutoWorkflowResult`, `record()`, `run_step()`, and module execution order.
- Produces: `AutoWorkflowCheckpointContext`, `AutoWorkflowCheckpoint`, `CheckpointCallback`, and `run_auto_query_workflow(..., checkpoint_context=None, checkpoint_callback=None)`.

- [ ] **Step 1: Write failing tests for success, failure, skip, final completion, and callback degradation**

Add these imports and tests to `tests/test_auto_query_workflow.py`:

```python
from src.auto_query_workflow import (
    AutoWorkflowCheckpointContext,
    # keep the existing imports
)

    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_workflow_emits_one_checkpoint_per_terminal_step_and_one_final_checkpoint(
        self,
        run_identifier,
        run_comptox,
    ):
        run_identifier.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Compound A"],
                    "smiles": [""],
                    "cas": [""],
                    "ec": [""],
                    "dtxsid": [""],
                    "echa_id": [""],
                }
            ),
            pd.DataFrame(),
        )
        run_comptox.side_effect = RuntimeError("EPA unavailable")
        checkpoints = []
        context = AutoWorkflowCheckpointContext(
            run_id="run-1",
            input_signature="input-sha",
            settings_signature="settings-sha",
            selected_steps=("标识符补全", "EPI Suite 环境归趋", "EPA CompTox 用途"),
        )

        result = run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                run_comptox=True,
                identifier_delay_seconds=0,
                use_delay_seconds=0,
            ),
            checkpoint_context=context,
            checkpoint_callback=checkpoints.append,
        )

        self.assertEqual(
            [checkpoint.current_step for checkpoint in checkpoints],
            ["标识符补全", "EPI Suite 环境归趋", "EPA CompTox 用途", None],
        )
        self.assertEqual(checkpoints[-1].status, "completed")
        self.assertEqual(checkpoints[-1].finished_steps, context.selected_steps)
        status_by_step = result.step_status.set_index("step")["status"].to_dict()
        self.assertEqual(status_by_step["EPI Suite 环境归趋"], "跳过")
        self.assertEqual(status_by_step["EPA CompTox 用途"], "失败")

    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_checkpoint_callback_failure_adds_warning_without_stopping_workflow(
        self,
        run_identifier,
    ):
        run_identifier.return_value = (_completed_identifier_rows(["Compound A"]), pd.DataFrame())

        result = run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                identifier_delay_seconds=0,
            ),
            checkpoint_context=AutoWorkflowCheckpointContext(
                run_id="run-2",
                input_signature="input-sha",
                settings_signature="settings-sha",
                selected_steps=("标识符补全",),
            ),
            checkpoint_callback=lambda checkpoint: (_ for _ in ()).throw(OSError("disk full")),
        )

        self.assertEqual(result.step_status.iloc[0]["status"], "完成")
        self.assertTrue(result.warnings["stage"].eq("Checkpoint").any())
        self.assertTrue(result.warnings["message"].str.contains("disk full").any())
```

- [ ] **Step 2: Run the two tests and confirm the new interface is absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_workflow_emits_one_checkpoint_per_terminal_step_and_one_final_checkpoint tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_checkpoint_callback_failure_adds_warning_without_stopping_workflow -v
```

Expected: both tests fail on the missing `AutoWorkflowCheckpointContext` import or missing keyword arguments.

- [ ] **Step 3: Add checkpoint models and snapshot emission helpers**

In `src/auto_query_workflow.py`, keep its existing `Callable` import and add `datetime` and `timezone`, then add:

```python
from datetime import datetime, timezone


@dataclass(frozen=True)
class AutoWorkflowCheckpointContext:
    run_id: str
    input_signature: str
    settings_signature: str
    selected_steps: tuple[str, ...]


@dataclass(frozen=True)
class AutoWorkflowCheckpoint:
    run_id: str
    input_signature: str
    settings_signature: str
    selected_steps: tuple[str, ...]
    finished_steps: tuple[str, ...]
    current_step: str | None
    status: str
    result: AutoWorkflowResult
    error_message: str
    updated_at: str


CheckpointCallback = Callable[[AutoWorkflowCheckpoint], None]
```

Extend the workflow signature exactly as follows:

```python
def run_auto_query_workflow(
    input_df: pd.DataFrame,
    config: AutoWorkflowConfig | None = None,
    progress_callback: ProgressCallback | None = None,
    activity_callback: ActivityCallback | None = None,
    checkpoint_context: AutoWorkflowCheckpointContext | None = None,
    checkpoint_callback: CheckpointCallback | None = None,
) -> AutoWorkflowResult:
```

Inside the function, after `add_warning()`, add these complete nested helpers:

```python
    def current_result():
        current_warnings = pd.DataFrame(warning_rows, columns=["stage", "message"])
        current_tables = OrderedDict(tables)
        current_tables["Warnings"] = current_warnings
        return AutoWorkflowResult(
            mapping=mapping,
            representative_table=representative.copy(),
            tables=current_tables,
            step_status=pd.DataFrame(
                status_rows,
                columns=["step", "status", "rows", "message"],
            ),
            warnings=current_warnings,
            charts=OrderedDict(charts),
        )

    def emit_checkpoint(current_step, status="running", error_message=""):
        if checkpoint_callback is None or checkpoint_context is None:
            return
        selected = set(checkpoint_context.selected_steps)
        finished = tuple(
            row["step"]
            for row in status_rows
            if row["step"] in selected
        )
        checkpoint = AutoWorkflowCheckpoint(
            run_id=checkpoint_context.run_id,
            input_signature=checkpoint_context.input_signature,
            settings_signature=checkpoint_context.settings_signature,
            selected_steps=checkpoint_context.selected_steps,
            finished_steps=finished,
            current_step=current_step,
            status=status,
            result=current_result(),
            error_message=error_message,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            checkpoint_callback(checkpoint)
        except Exception as exc:
            add_warning("Checkpoint", f"临时恢复保存失败：{exc}")
```

Call `emit_checkpoint()` once at the end of each corresponding selected block, after all success/failure/skip tables and warnings for that block have been added. The insertions are:

```python
    if run_local_r_df:
        # existing local-screening run, table, chart, warning, and record code
        emit_checkpoint(R_DF_STEP_LABEL)

    if needs_identifier:
        # existing identifier run, table, warning, and record code
        emit_checkpoint("标识符补全")

    if run_epi_step:
        # existing EPI success/failure/skip code
        emit_checkpoint("EPI Suite 环境归趋")

    if config.run_comptox:
        # existing CompTox success/failure/audit-table code
        emit_checkpoint("EPA CompTox 用途")

    if config.run_echa_use:
        # existing ECHA-use success/failure/audit-table code
        emit_checkpoint("ECHA REACH 用途")

    if config.run_echa_ghs:
        # existing ECHA-GHS success/failure code
        emit_checkpoint("ECHA GHS/C&L 危害")

    if config.run_source_origin:
        # existing source-origin success/failure/audit-table code
        emit_checkpoint("来源属性评估")

    if config.run_pov_lrtp_toxpi:
        # existing Pov-LRTP/PBM/ToxPi success/failure code
        emit_checkpoint("Pov-LRTP / PBM / ToxPi")
```

The comments above identify existing blocks and are placement markers in this plan; do not copy the comments or create second conditionals. Insert only each `emit_checkpoint(...)` line immediately before its existing block ends. Immediately before the final return, call:

```python
    emit_checkpoint(None, status="completed")
```

Replace the final manual result construction with `return current_result()` so a callback failure warning is included in the returned result.

- [ ] **Step 4: Run focused workflow tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_workflow_emits_one_checkpoint_per_terminal_step_and_one_final_checkpoint tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_checkpoint_callback_failure_adds_warning_without_stopping_workflow tests.test_auto_query_progress -v
```

Expected: all selected tests pass; the emitted module order is identifier, EPI, CompTox, followed by a final checkpoint whose `current_step` is `None`.

- [ ] **Step 5: Commit the event protocol**

```powershell
git add src/auto_query_workflow.py tests/test_auto_query_workflow.py
git commit -m "feat: emit auto-query module checkpoints"
```

---

### Task 2: Build step-specific workbooks and an explicitly partial ZIP

**Files:**
- Modify: `src/auto_query_workflow.py:61-160,227-245,681-752`
- Test: `tests/test_auto_query_workflow.py:830-930`

**Interfaces:**
- Consumes: `AutoWorkflowResult`, `_build_module_workbook()`, `PUBLIC_TABLE_NAMES`, and existing final ZIP code.
- Produces: `AutoWorkflowModuleWorkbook`, `build_auto_workflow_module_workbook(result, step)`, and `build_auto_workflow_partial_zip(result, module_workbooks)`.

- [ ] **Step 1: Write failing tests for separate ECHA exports and the partial archive label**

Add to `tests/test_auto_query_workflow.py` imports and test class:

```python
from src.auto_query_workflow import (
    build_auto_workflow_module_workbook,
    build_auto_workflow_partial_zip,
    # keep the existing imports
)

    def test_checkpoint_module_workbooks_split_echa_use_and_ghs(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [
                    ("ECHA_Use_Summary", pd.DataFrame({"compound": ["Compound A"]})),
                    ("ECHA_GHS_Summary", pd.DataFrame({"compound": ["Compound A"]})),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        use_book = build_auto_workflow_module_workbook(result, "ECHA REACH 用途")
        ghs_book = build_auto_workflow_module_workbook(result, "ECHA GHS/C&L 危害")

        self.assertEqual(use_book.file_name, "ECHA_REACH_Use_Results.xlsx")
        self.assertEqual(ghs_book.file_name, "ECHA_GHS_CL_Results.xlsx")
        self.assertEqual(
            pd.ExcelFile(io.BytesIO(use_book.data)).sheet_names,
            ["ECHA_Use_Summary"],
        )
        self.assertEqual(
            pd.ExcelFile(io.BytesIO(ghs_book.data)).sheet_names,
            ["ECHA_GHS_Summary"],
        )

    def test_partial_zip_contains_only_named_partial_log_and_completed_module_books(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [("Identifier_Completion", pd.DataFrame({"compound": ["Compound A"]}))]
            ),
            step_status=pd.DataFrame(
                {"step": ["标识符补全"], "status": ["完成"], "rows": [1], "message": [""]}
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
        )
        module = build_auto_workflow_module_workbook(result, "标识符补全")

        package = build_auto_workflow_partial_zip(result, {module.slug: module})

        with zipfile.ZipFile(package) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "Partial_Auto_Query_Workflow_Results.xlsx",
                    "modules/Identifier_Completion_Results.xlsx",
                },
            )
```

- [ ] **Step 2: Run the export tests and verify missing symbols fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_checkpoint_module_workbooks_split_echa_use_and_ghs tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_partial_zip_contains_only_named_partial_log_and_completed_module_books -v
```

Expected: both tests fail because the two public builders do not exist.

- [ ] **Step 3: Add the step export map and public builders**

Add this immutable artifact model:

```python
@dataclass(frozen=True)
class AutoWorkflowModuleWorkbook:
    step: str
    slug: str
    file_name: str
    data: bytes
```

Add `AUTO_WORKFLOW_CHECKPOINT_EXPORTS` next to `AUTO_WORKFLOW_EXPORT_MODULES`. Copy the exact table tuples from the existing module map, but use these step keys and artifact identities:

```python
AUTO_WORKFLOW_CHECKPOINT_EXPORTS = {
    R_DF_STEP_LABEL: ("local_screening", "Local_Screening_Results.xlsx", AUTO_WORKFLOW_EXPORT_MODULES[0][2]),
    "标识符补全": ("identifier_completion", "Identifier_Completion_Results.xlsx", AUTO_WORKFLOW_EXPORT_MODULES[1][2]),
    "EPI Suite 环境归趋": ("epi_suite", "EPI_Suite_Results.xlsx", AUTO_WORKFLOW_EXPORT_MODULES[2][2]),
    "EPA CompTox 用途": ("comptox_use", "EPA_CompTox_Results.xlsx", AUTO_WORKFLOW_EXPORT_MODULES[3][2]),
    "ECHA REACH 用途": (
        "echa_reach_use",
        "ECHA_REACH_Use_Results.xlsx",
        (
            "ECHA_Use_Summary",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "ECHA_Use_Dossiers",
            "ECHA_Use_Errors",
        ),
    ),
    "ECHA GHS/C&L 危害": (
        "echa_ghs_cl",
        "ECHA_GHS_CL_Results.xlsx",
        ("ECHA_GHS_Summary", "ECHA_GHS_Classifications", "ECHA_GHS_Errors"),
    ),
    "来源属性评估": ("source_origin", "Source_Origin_Results.xlsx", AUTO_WORKFLOW_EXPORT_MODULES[5][2]),
    "Pov-LRTP / PBM / ToxPi": ("pov_lrtp_pbm_toxpi", "Pov_LRTP_PBM_ToxPi_Results.xlsx", AUTO_WORKFLOW_EXPORT_MODULES[6][2]),
}
```

Add these public builders below `_build_module_workbook()`:

```python
def build_auto_workflow_module_workbook(
    result: AutoWorkflowResult,
    step: str,
) -> AutoWorkflowModuleWorkbook | None:
    export = AUTO_WORKFLOW_CHECKPOINT_EXPORTS.get(step)
    if export is None:
        return None
    slug, file_name, candidates = export
    table_names = tuple(
        name
        for name in candidates
        if name in PUBLIC_TABLE_NAMES and isinstance(result.tables.get(name), pd.DataFrame)
    )
    if not table_names:
        return None
    return AutoWorkflowModuleWorkbook(
        step=step,
        slug=slug,
        file_name=file_name,
        data=_build_module_workbook(result, table_names).getvalue(),
    )


def build_auto_workflow_partial_zip(
    result: AutoWorkflowResult,
    module_workbooks: Mapping[str, AutoWorkflowModuleWorkbook],
) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Partial_Auto_Query_Workflow_Results.xlsx",
            build_auto_workflow_workbook(result).getvalue(),
        )
        for module in module_workbooks.values():
            archive.writestr(f"modules/{module.file_name}", module.data)
    buffer.seek(0)
    return buffer
```

Add `Mapping` beside the existing `Callable` import from `typing`. Do not change `build_auto_workflow_zip()`; its ECHA folder and workbook remain combined.

- [ ] **Step 4: Run export regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_checkpoint_module_workbooks_split_echa_use_and_ghs tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_partial_zip_contains_only_named_partial_log_and_completed_module_books tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_auto_workflow_zip_groups_results_by_module -v
```

Expected: all three tests pass, including the unchanged final ZIP grouping test.

- [ ] **Step 5: Commit module exports**

```powershell
git add src/auto_query_workflow.py tests/test_auto_query_workflow.py
git commit -m "feat: export completed auto-query modules"
```

---

### Task 3: Persist safe, atomic, 24-hour disk checkpoints

**Files:**
- Create: `src/auto_query_checkpoint.py`
- Create: `tests/test_auto_query_checkpoint.py`

**Interfaces:**
- Consumes: `AutoWorkflowCheckpoint`, `AutoWorkflowChart`, `AutoWorkflowMapping`, `AutoWorkflowModuleWorkbook`, and `AutoWorkflowResult` from Tasks 1-2.
- Produces: `CheckpointStorageError`, `InvalidRunToken`, `ExpiredCheckpoint`, `LoadedAutoQueryCheckpoint`, `generate_run_token()`, `save_checkpoint()`, `load_checkpoint()`, `delete_checkpoint()`, and `cleanup_expired_checkpoints()`.

- [ ] **Step 1: Write failing round-trip, safety, corruption, TTL, and delete tests**

Create `tests/test_auto_query_checkpoint.py` with:

```python
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.auto_query_checkpoint import (
    CheckpointStorageError,
    ExpiredCheckpoint,
    InvalidRunToken,
    cleanup_expired_checkpoints,
    delete_checkpoint,
    generate_run_token,
    load_checkpoint,
    save_checkpoint,
)
from src.auto_query_workflow import (
    AutoWorkflowChart,
    AutoWorkflowCheckpoint,
    AutoWorkflowMapping,
    AutoWorkflowModuleWorkbook,
    AutoWorkflowResult,
)


def example_checkpoint(updated_at):
    result = AutoWorkflowResult(
        mapping=AutoWorkflowMapping(group_area_cols=["Group Area 1"]),
        representative_table=pd.DataFrame({"Name": ["A", "B"]}),
        tables=OrderedDict(
            [
                ("Identifier_Completion", pd.DataFrame({"compound": ["A", "B"], "score": [1.5, pd.NA]})),
            ]
        ),
        step_status=pd.DataFrame(
            {"step": ["标识符补全"], "status": ["完成"], "rows": [2], "message": [""]}
        ),
        warnings=pd.DataFrame({"stage": ["Identifier"], "message": ["example"]}),
        charts=OrderedDict(
            [("Local_DBE_Bubble_Plot", AutoWorkflowChart("DBE", b"PNG", b"PDF"))]
        ),
    )
    return AutoWorkflowCheckpoint(
        run_id="run-1",
        input_signature="input-sha",
        settings_signature="settings-sha",
        selected_steps=("标识符补全",),
        finished_steps=("标识符补全",),
        current_step="标识符补全",
        status="running",
        result=result,
        error_message="",
        updated_at=updated_at.isoformat(),
    )


class AutoQueryCheckpointTests(unittest.TestCase):
    def test_checkpoint_round_trip_preserves_frames_charts_and_module_workbooks(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            module = AutoWorkflowModuleWorkbook(
                step="标识符补全",
                slug="identifier_completion",
                file_name="Identifier_Completion_Results.xlsx",
                data=b"XLSX",
            )
            save_checkpoint(token, example_checkpoint(now), "input.xlsx", {module.slug: module}, root=root, now=now)

            loaded = load_checkpoint(token, root=root, now=now + timedelta(hours=1))

            pd.testing.assert_frame_equal(
                loaded.checkpoint.result.tables["Identifier_Completion"],
                example_checkpoint(now).result.tables["Identifier_Completion"],
                check_dtype=False,
            )
            self.assertEqual(loaded.checkpoint.result.charts["Local_DBE_Bubble_Plot"].png, b"PNG")
            self.assertEqual(loaded.module_workbooks["identifier_completion"].data, b"XLSX")
            self.assertNotIn(token, (root / next(root.iterdir()).name / "manifest.json").read_text(encoding="utf-8"))

    def test_invalid_tokens_are_rejected_before_path_resolution(self):
        with TemporaryDirectory() as temp_dir:
            for token in ("short", "../escape" + "x" * 40, "A" * 129):
                with self.subTest(token=token):
                    with self.assertRaises(InvalidRunToken):
                        load_checkpoint(token, root=Path(temp_dir))

    def test_missing_referenced_table_is_reported_as_storage_error(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            run_dir = save_checkpoint(token, example_checkpoint(now), "input.xlsx", {}, root=root, now=now)
            table_path = next((run_dir / "tables").glob("*.json.gz"))
            table_path.unlink()
            with self.assertRaises(CheckpointStorageError):
                load_checkpoint(token, root=root, now=now)

    def test_ttl_boundary_and_targeted_delete(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = generate_run_token()
            second = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            save_checkpoint(first, example_checkpoint(now), "a.xlsx", {}, root=root, now=now)
            save_checkpoint(second, example_checkpoint(now), "b.xlsx", {}, root=root, now=now)

            self.assertEqual(cleanup_expired_checkpoints(root=root, now=now + timedelta(hours=23, minutes=59)), [])
            with self.assertRaises(ExpiredCheckpoint):
                load_checkpoint(first, root=root, now=now + timedelta(hours=24, seconds=1))
            removed = cleanup_expired_checkpoints(root=root, now=now + timedelta(hours=24, seconds=1))
            self.assertEqual(len(removed), 2)

            save_checkpoint(first, example_checkpoint(now), "a.xlsx", {}, root=root, now=now)
            save_checkpoint(second, example_checkpoint(now), "b.xlsx", {}, root=root, now=now)
            self.assertTrue(delete_checkpoint(first, root=root))
            self.assertIsNotNone(load_checkpoint(second, root=root, now=now))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the storage test module and confirm the new module is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_checkpoint -v
```

Expected: import failure for `src.auto_query_checkpoint`.

- [ ] **Step 3: Implement the storage module with manifest-last commits**

Create `src/auto_query_checkpoint.py` with this public model and validation foundation:

```python
"""Short-lived, non-pickle persistence for page-6 auto-query checkpoints."""

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from typing import Any, Mapping
from uuid import uuid4

import pandas as pd

from src.auto_query_workflow import (
    AutoWorkflowChart,
    AutoWorkflowCheckpoint,
    AutoWorkflowMapping,
    AutoWorkflowModuleWorkbook,
    AutoWorkflowResult,
)


SCHEMA_VERSION = 1
TTL = timedelta(hours=24)
DEFAULT_CHECKPOINT_ROOT = Path(".cache/auto_query_runs")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class CheckpointStorageError(RuntimeError):
    pass


class InvalidRunToken(CheckpointStorageError):
    pass


class ExpiredCheckpoint(CheckpointStorageError):
    pass


@dataclass(frozen=True)
class LoadedAutoQueryCheckpoint:
    checkpoint: AutoWorkflowCheckpoint
    input_filename: str
    module_workbooks: OrderedDict[str, AutoWorkflowModuleWorkbook]
    manifest: dict[str, Any]


def _utc_now():
    return datetime.now(timezone.utc)


def generate_run_token():
    return secrets.token_urlsafe(32)


def _run_directory(token, root=DEFAULT_CHECKPOINT_ROOT):
    if not TOKEN_PATTERN.fullmatch(str(token)):
        raise InvalidRunToken("恢复令牌格式无效")
    root = Path(root).resolve()
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    run_dir = (root / digest).resolve()
    if run_dir.parent != root:
        raise InvalidRunToken("恢复路径越界")
    return run_dir


def _safe_name(value):
    value = str(value)
    if not NAME_PATTERN.fullmatch(value):
        raise CheckpointStorageError(f"不安全的检查点名称：{value}")
    return value


def _safe_file_name(value):
    value = str(value)
    if value in {".", ".."} or not FILE_NAME_PATTERN.fullmatch(value):
        raise CheckpointStorageError(f"不安全的文件名：{value}")
    return value


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _frame_bytes(frame):
    text = frame.to_json(orient="table", date_format="iso", index=False, force_ascii=False)
    return gzip.compress(text.encode("utf-8"))


def _read_frame(path):
    try:
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
        return pd.read_json(io.StringIO(text), orient="table")
    except Exception as exc:
        raise CheckpointStorageError(f"无法读取检查点表格 {path.name}：{exc}") from exc
```

Then add these complete save/load/delete/cleanup functions in the same file:

```python
def save_checkpoint(
    token,
    checkpoint,
    input_filename,
    module_workbooks,
    *,
    root=DEFAULT_CHECKPOINT_ROOT,
    now=None,
):
    now = now or _utc_now()
    run_dir = _run_directory(token, root)
    run_dir.mkdir(parents=True, exist_ok=True)
    revision = uuid4().hex
    frames = OrderedDict(
        [
            ("representative_table", checkpoint.result.representative_table),
            ("step_status", checkpoint.result.step_status),
            ("warnings", checkpoint.result.warnings),
            *checkpoint.result.tables.items(),
        ]
    )
    table_files = {}
    for name, frame in frames.items():
        safe_name = _safe_name(name)
        relative = Path("tables") / f"{revision}__{safe_name}.json.gz"
        _atomic_write(run_dir / relative, _frame_bytes(frame))
        table_files[name] = relative.as_posix()

    chart_files = {}
    for key, chart in checkpoint.result.charts.items():
        safe_key = _safe_name(key)
        png = Path("charts") / f"{revision}__{safe_key}.png"
        pdf = Path("charts") / f"{revision}__{safe_key}.pdf"
        _atomic_write(run_dir / png, chart.png)
        _atomic_write(run_dir / pdf, chart.pdf)
        chart_files[key] = {"title": chart.title, "png": png.as_posix(), "pdf": pdf.as_posix()}

    module_files = {}
    for slug, module in module_workbooks.items():
        safe_slug = _safe_name(slug)
        safe_file_name = _safe_file_name(module.file_name)
        relative = Path("modules") / f"{safe_slug}.xlsx"
        _atomic_write(run_dir / relative, module.data)
        module_files[slug] = {
            "step": module.step,
            "file_name": safe_file_name,
            "path": relative.as_posix(),
        }

    manifest_path = run_dir / "manifest.json"
    created_at = now.isoformat()
    if manifest_path.exists():
        try:
            created_at = json.loads(manifest_path.read_text(encoding="utf-8"))["created_at"]
        except Exception:
            created_at = now.isoformat()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "token_hash": run_dir.name,
        "run_id": checkpoint.run_id,
        "input_filename": str(input_filename),
        "input_signature": checkpoint.input_signature,
        "settings_signature": checkpoint.settings_signature,
        "selected_steps": list(checkpoint.selected_steps),
        "finished_steps": list(checkpoint.finished_steps),
        "current_step": checkpoint.current_step,
        "status": checkpoint.status,
        "error_message": checkpoint.error_message,
        "mapping": asdict(checkpoint.result.mapping),
        "table_files": table_files,
        "chart_files": chart_files,
        "module_files": module_files,
        "run_log": checkpoint.result.step_status.to_dict("records"),
        "warning_summary": checkpoint.result.warnings.to_dict("records"),
        "created_at": created_at,
        "updated_at": now.isoformat(),
        "expires_at": (now + TTL).isoformat(),
    }
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    )
    return run_dir


def _checked_relative_file(run_dir, relative):
    path = (run_dir / str(relative)).resolve()
    if run_dir.resolve() not in path.parents:
        raise CheckpointStorageError("检查点清单包含越界路径")
    if not path.is_file():
        raise CheckpointStorageError(f"检查点文件缺失：{relative}")
    return path


def load_checkpoint(token, *, root=DEFAULT_CHECKPOINT_ROOT, now=None):
    now = now or _utc_now()
    run_dir = _run_directory(token, root)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise CheckpointStorageError("找不到可恢复的检查点")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CheckpointStorageError(f"检查点清单损坏：{exc}") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("token_hash") != run_dir.name:
        raise CheckpointStorageError("检查点版本或令牌摘要不匹配")
    if now > datetime.fromisoformat(manifest["expires_at"]):
        raise ExpiredCheckpoint("检查点已超过 24 小时")

    frames = OrderedDict(
        (name, _read_frame(_checked_relative_file(run_dir, relative)))
        for name, relative in manifest["table_files"].items()
    )
    charts = OrderedDict()
    for key, entry in manifest["chart_files"].items():
        charts[key] = AutoWorkflowChart(
            title=entry["title"],
            png=_checked_relative_file(run_dir, entry["png"]).read_bytes(),
            pdf=_checked_relative_file(run_dir, entry["pdf"]).read_bytes(),
        )
    modules = OrderedDict()
    for slug, entry in manifest["module_files"].items():
        safe_slug = _safe_name(slug)
        modules[slug] = AutoWorkflowModuleWorkbook(
            step=entry["step"],
            slug=safe_slug,
            file_name=_safe_file_name(entry["file_name"]),
            data=_checked_relative_file(run_dir, entry["path"]).read_bytes(),
        )

    result = AutoWorkflowResult(
        mapping=AutoWorkflowMapping(**manifest["mapping"]),
        representative_table=frames.pop("representative_table"),
        tables=OrderedDict(
            (name, frame)
            for name, frame in frames.items()
            if name not in {"step_status", "warnings"}
        ),
        step_status=frames["step_status"],
        warnings=frames["warnings"],
        charts=charts,
    )
    checkpoint = AutoWorkflowCheckpoint(
        run_id=manifest["run_id"],
        input_signature=manifest["input_signature"],
        settings_signature=manifest["settings_signature"],
        selected_steps=tuple(manifest["selected_steps"]),
        finished_steps=tuple(manifest["finished_steps"]),
        current_step=manifest["current_step"],
        status=manifest["status"],
        result=result,
        error_message=manifest["error_message"],
        updated_at=manifest["updated_at"],
    )
    return LoadedAutoQueryCheckpoint(checkpoint, manifest["input_filename"], modules, manifest)


def delete_checkpoint(token, *, root=DEFAULT_CHECKPOINT_ROOT):
    run_dir = _run_directory(token, root)
    if not run_dir.exists():
        return False
    shutil.rmtree(run_dir)
    return True


def cleanup_expired_checkpoints(*, root=DEFAULT_CHECKPOINT_ROOT, now=None):
    now = now or _utc_now()
    root = Path(root).resolve()
    if not root.exists():
        return []
    removed = []
    for child in root.iterdir():
        if not child.is_dir() or not HASH_PATTERN.fullmatch(child.name):
            continue
        manifest_path = child / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expired = now > datetime.fromisoformat(manifest["expires_at"])
        except Exception:
            expired = False
        if expired and child.resolve().parent == root:
            shutil.rmtree(child)
            removed.append(child)
    return removed
```

The versioned table/chart filenames ensure an older committed manifest never points at partially overwritten snapshot data. Leave previous revision files in place until TTL deletion; this favors crash safety and keeps cleanup simple.

- [ ] **Step 4: Run storage tests and inspect the on-disk manifest**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_checkpoint -v
```

Expected: all storage tests pass. The round-trip temporary directory contains one SHA-256-named directory, a `manifest.json`, gzip JSON tables, chart bytes, and module bytes; no raw token appears in the manifest.

- [ ] **Step 5: Commit checkpoint persistence**

```powershell
git add src/auto_query_checkpoint.py tests/test_auto_query_checkpoint.py
git commit -m "feat: persist auto-query checkpoints"
```

---

### Task 4: Restore checkpoints and expose live module downloads on page 6

**Files:**
- Modify: `pages/6_一键批量查询.py:1-55,216-270,483-647`
- Modify: `tests/test_auto_query_workflow.py:1172-1330`
- Modify: `tests/test_upload_state.py:55-125`

**Interfaces:**
- Consumes: workflow context/callback and module builders from Tasks 1-2; storage API from Task 3; existing Streamlit upload and result dashboard helpers.
- Produces: session-backed live checkpoint rendering, `run` query-parameter restore, explicit clearing, and partial-result delivery.

- [ ] **Step 1: Add failing page source-contract tests**

Add to `tests/test_auto_query_workflow.py`:

```python
    def test_page_6_wires_checkpoint_restore_and_non_rerunning_downloads(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")

        for token in (
            "cleanup_expired_checkpoints(",
            'st.query_params.get("run")',
            'st.query_params["run"] = run_token',
            "load_checkpoint(",
            "save_checkpoint(",
            "delete_checkpoint(",
            "checkpoint_callback=handle_checkpoint",
            'on_click="ignore"',
            "Auto_Query_Workflow_Partial_Results.zip",
            "已恢复上次运行的部分结果",
            "上次运行未正常结束",
        ):
            self.assertIn(token, page_text)

    def test_page_6_renders_recovered_results_before_stopping_for_missing_upload(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        no_upload_block = page_text.split("if not active_uploads:", 1)[1].split("st.success", 1)[0]

        self.assertIn("auto_query_partial_result", no_upload_block)
        self.assertIn("_render_saved_results", no_upload_block)
        self.assertIn("st.stop()", no_upload_block)
```

Add to `tests/test_upload_state.py`:

```python
    def test_page_6_declares_all_checkpoint_session_keys_and_clears_the_current_token(self):
        page_text = next(Path("pages").glob("6_*.py")).read_text(encoding="utf-8")
        for key in (
            "auto_query_run_token",
            "auto_query_checkpoint_manifest",
            "auto_query_partial_result",
            "auto_query_module_workbooks",
            "auto_query_checkpoint_warning",
        ):
            self.assertIn(key, page_text)
        clear_block = page_text.split("def clear_auto_query_state", 1)[1].split("st.set_page_config", 1)[0]
        self.assertIn("delete_checkpoint", clear_block)
        self.assertIn('st.query_params.pop("run", None)', clear_block)
```

- [ ] **Step 2: Run page contract tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_page_6_wires_checkpoint_restore_and_non_rerunning_downloads tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_page_6_renders_recovered_results_before_stopping_for_missing_upload tests.test_upload_state.UploadStateTests.test_page_6_declares_all_checkpoint_session_keys_and_clears_the_current_token -v
```

Expected: all three tests fail on missing checkpoint wiring.

- [ ] **Step 3: Add imports, state keys, clear behavior, and reusable result rendering**

Import `OrderedDict`, `replace`, `datetime`, `timezone`, the new workflow models/builders, `settings_signature`, and the six storage functions. Add:

```python
CHECKPOINT_STATE_KEYS = (
    "auto_query_run_token",
    "auto_query_checkpoint_manifest",
    "auto_query_partial_result",
    "auto_query_module_workbooks",
    "auto_query_checkpoint_warning",
)


def clear_auto_query_state():
    token = st.session_state.get("auto_query_run_token") or st.query_params.get("run")
    if token:
        try:
            delete_checkpoint(token)
        except CheckpointStorageError:
            pass
    clear_uploads(
        st.session_state,
        (*INPUT_CACHE_KEYS, *RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
    )
    st.session_state.pop(SETTINGS_SIGNATURE_KEY, None)
    st.session_state.pop("auto_query_upload", None)
    st.query_params.pop("run", None)
```

Move the existing bottom result display into this complete helper and call it both for recovered and normal results:

```python
def _render_saved_results(result, charts, full_package=None, module_workbooks=None, partial=False):
    st.subheader("运行日志")
    _show_dataframe(result.step_status)
    if not result.warnings.empty:
        with st.expander("Warnings", expanded=False):
            _show_dataframe(result.warnings)
    table_names = [name for name in result.tables if name in PUBLIC_TABLE_NAMES]
    if table_names:
        selected_table = st.selectbox("查看结果表", table_names, key="auto_query_result_table")
        _show_dataframe(result.tables[selected_table])
    structure_preparation = result.tables.get("Structure_Preparation")
    if isinstance(structure_preparation, pd.DataFrame):
        _render_structure_preparation_summary(structure_preparation)
    _render_result_dashboard(result, charts)
    _render_module_downloads(result, module_workbooks or OrderedDict())
    if partial:
        partial_zip = build_auto_workflow_partial_zip(result, module_workbooks)
        st.download_button(
            "下载部分结果 ZIP",
            data=partial_zip.getvalue(),
            file_name="Auto_Query_Workflow_Partial_Results.zip",
            mime="application/zip",
            key="auto_query_partial_zip_download",
            on_click="ignore",
        )
    if full_package is not None:
        st.download_button(
            "下载一键批量查询结果 ZIP",
            data=full_package.getvalue(),
            file_name="Auto_Query_Workflow_Results.zip",
            mime="application/zip",
            key="auto_query_full_zip_download",
            on_click="ignore",
        )


def _render_module_downloads(result, module_workbooks):
    if result.step_status.empty:
        return
    st.subheader("已完成模块，可立即下载")
    modules_by_step = {module.step: (slug, module) for slug, module in module_workbooks.items()}
    for row in result.step_status.to_dict("records"):
        step = str(row["step"])
        warning_count = int(result.warnings["stage"].eq(step).sum()) if not result.warnings.empty else 0
        st.caption(
            f"{step}：{row['status']} · {int(row['rows'])} 行 · {warning_count} 条警告"
        )
        if row.get("message"):
            st.warning(str(row["message"]))
        export_definition = AUTO_WORKFLOW_CHECKPOINT_EXPORTS.get(step)
        preview = None
        if export_definition is not None:
            preview = next(
                (
                    result.tables[name]
                    for name in export_definition[2]
                    if isinstance(result.tables.get(name), pd.DataFrame)
                    and not result.tables[name].empty
                ),
                None,
            )
        if preview is not None:
            with st.expander(f"预览 {step} 关键结果", expanded=False):
                _show_dataframe(preview.head(20))
        export = modules_by_step.get(step)
        if export is None:
            st.caption("该模块当前没有可导出的结果表。")
            continue
        slug, module = export
        st.download_button(
            f"下载 {module.step}",
            data=module.data,
            file_name=module.file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"auto_query_module_download_{slug}",
            on_click="ignore",
        )
```

- [ ] **Step 4: Restore before upload-dependent controls and keep old/new inputs isolated**

Immediately before `st.file_uploader`, run cleanup and restore:

```python
try:
    cleanup_expired_checkpoints()
except OSError as exc:
    st.session_state["auto_query_checkpoint_warning"] = str(exc)

recovery_token = st.query_params.get("run")
if recovery_token and st.session_state.get("auto_query_run_token") != recovery_token:
    try:
        loaded = load_checkpoint(recovery_token)
    except ExpiredCheckpoint:
        st.warning("上次结果已超过 24 小时，不能恢复。")
        st.query_params.pop("run", None)
    except CheckpointStorageError as exc:
        st.warning(f"无法恢复上次结果：{exc}")
        st.query_params.pop("run", None)
    else:
        checkpoint = loaded.checkpoint
        st.session_state["auto_query_run_token"] = recovery_token
        st.session_state["auto_query_checkpoint_manifest"] = loaded.manifest
        st.session_state["auto_query_partial_result"] = checkpoint.result
        st.session_state["auto_query_workflow_result"] = checkpoint.result
        st.session_state["auto_query_module_workbooks"] = loaded.module_workbooks
        st.session_state["auto_query_workflow_charts"] = checkpoint.result.charts
        st.success("已恢复上次运行的部分结果。")
        st.caption("恢复网址包含短期访问令牌，请勿分享；临时结果 24 小时后过期，服务器重新部署后不保证保留。")
        if checkpoint.status == "running":
            st.warning("上次运行未正常结束；已完成结果可下载，重新运行会复用查询缓存。")
```

Replace the no-upload stop block with:

```python
if not active_uploads:
    recovered = st.session_state.get("auto_query_partial_result")
    if recovered is not None:
        _render_saved_results(
            recovered,
            st.session_state.get("auto_query_workflow_charts") or {},
            module_workbooks=st.session_state.get("auto_query_module_workbooks") or OrderedDict(),
            partial=True,
        )
    else:
        st.info("请先上传 Excel 文件。")
    st.stop()
```

Move the existing “清空当前数据” button above this no-upload block so it remains available on a recovered page with no upload. When `input_changed` is true, clear result and checkpoint session keys and remove `run` from query parameters, but do not delete the old disk directory. After resolving `active_uploads`, compare `st.session_state["auto_query_input_signature"]` with `st.session_state["auto_query_checkpoint_manifest"]["input_signature"]`; if both exist and differ, clear `RESULT_CACHE_KEYS` plus `CHECKPOINT_STATE_KEYS` and remove `run` before rendering controls. Starting a new run creates a new token; the old one remains recoverable until explicit deletion or TTL cleanup.

Capture the Boolean returned by `invalidate_results_on_settings_change()`. When it is true, clear `CHECKPOINT_STATE_KEYS` and remove `run`, while leaving the old disk checkpoint for TTL cleanup. This prevents a recovered result from being displayed as if it belonged to newly edited settings.

- [ ] **Step 5: Wire the live callback, session-first degradation, final export, and failed status**

After `selected_steps` has been built inside `if start_run:` and before creating the progress placeholders, create the run identity and mutable artifacts:

```python
    try:
        cleanup_expired_checkpoints()
    except OSError as exc:
        st.session_state["auto_query_checkpoint_warning"] = str(exc)
    run_token = generate_run_token()
    run_id = generate_run_token()
    st.query_params["run"] = run_token
    st.session_state["auto_query_run_token"] = run_token
    module_workbooks = OrderedDict()
    latest_checkpoint = [None]
    partial_container = st.empty()
    checkpoint_context = AutoWorkflowCheckpointContext(
        run_id=run_id,
        input_signature=st.session_state["auto_query_input_signature"],
        settings_signature=settings_signature(result_settings),
        selected_steps=tuple(selected_steps),
    )

    def handle_checkpoint(checkpoint):
        latest_checkpoint[0] = checkpoint
        st.session_state["auto_query_partial_result"] = checkpoint.result
        st.session_state["auto_query_workflow_result"] = checkpoint.result
        if checkpoint.current_step:
            try:
                module = build_auto_workflow_module_workbook(
                    checkpoint.result,
                    checkpoint.current_step,
                )
            except Exception as exc:
                st.session_state["auto_query_checkpoint_warning"] = f"模块导出失败：{exc}"
            else:
                if module is not None:
                    module_workbooks[module.slug] = module
        st.session_state["auto_query_module_workbooks"] = OrderedDict(module_workbooks)
        try:
            save_checkpoint(
                run_token,
                checkpoint,
                active_uploads[0]["name"],
                module_workbooks,
            )
        except Exception as exc:
            st.session_state["auto_query_checkpoint_warning"] = (
                f"临时恢复保存失败，本次结果仅保留在当前页面会话：{exc}"
            )
        partial_container.empty()
        with partial_container.container():
            _render_module_downloads(checkpoint.result, module_workbooks)
```

Immediately after defining `handle_checkpoint()`, persist an initial running checkpoint so an exception before the first module still has a recoverable manifest:

```python
    initial_result = AutoWorkflowResult(
        mapping=mapping,
        representative_table=build_representative_table(prepared_input_df, mapping),
        tables=OrderedDict([("Structure_Preparation", prepared_input_df.copy())]),
        step_status=pd.DataFrame(columns=["step", "status", "rows", "message"]),
        warnings=pd.DataFrame(columns=["stage", "message"]),
    )
    handle_checkpoint(
        AutoWorkflowCheckpoint(
            run_id=run_id,
            input_signature=checkpoint_context.input_signature,
            settings_signature=checkpoint_context.settings_signature,
            selected_steps=checkpoint_context.selected_steps,
            finished_steps=(),
            current_step=None,
            status="running",
            result=initial_result,
            error_message="",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    )
```

Import `AutoWorkflowResult`, `build_representative_table`, and `AUTO_WORKFLOW_CHECKPOINT_EXPORTS` from `src.auto_query_workflow`. The initial callback has no terminal step, so it creates no module workbook and no false progress entry.

Pass `checkpoint_context=checkpoint_context` and `checkpoint_callback=handle_checkpoint` to `run_auto_query_workflow()`. Replace the spinner block with a top-level `try` that separates query completion from chart/full-ZIP generation:

```python
    try:
        with st.spinner("正在按顺序运行已选项目..."):
            result = run_auto_query_workflow(
                prepared_input_df,
                config=config,
                progress_callback=update_progress,
                activity_callback=update_activity,
                checkpoint_context=checkpoint_context,
                checkpoint_callback=handle_checkpoint,
            )
            status_box.info("查询环节已完成，正在汇总结果与生成图表...")
            charts = build_auto_workflow_charts(result)
            result.charts = charts
            package = build_auto_workflow_zip(result, charts)
    except Exception as exc:
        status_box.error(f"运行未完整结束：{exc}")
        if latest_checkpoint[0] is not None:
            failed_checkpoint = replace(
                latest_checkpoint[0],
                status="failed",
                result=latest_checkpoint[0].result,
                error_message=str(exc),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            handle_checkpoint(failed_checkpoint)
        st.session_state["auto_query_checkpoint_warning"] = str(exc)
    else:
        st.session_state["auto_query_workflow_result"] = result
        st.session_state["auto_query_workflow_charts"] = charts
        st.session_state["auto_query_workflow_zip"] = package
        if latest_checkpoint[0] is not None:
            completed_checkpoint = replace(
                latest_checkpoint[0],
                status="completed",
                result=result,
                error_message="",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            handle_checkpoint(completed_checkpoint)
        overall_progress_bar.progress(1.0)
        module_progress_bar.progress(1.0)
        status_box.success("一键批量查询完成。")
```

After the run block, render `auto_query_workflow_result` with `_render_saved_results()`. Set `partial=True` whenever the full package is absent and module workbooks exist. Display `auto_query_checkpoint_warning` with `st.warning()` above the downloads.

- [ ] **Step 6: Run page and workflow contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_upload_state -v
```

Expected: all tests pass. Existing dashboard, progress, full ZIP, and upload-retention source contracts remain present alongside the new recovery contracts.

- [ ] **Step 7: Commit page integration**

```powershell
git add pages/6_一键批量查询.py tests/test_auto_query_workflow.py tests/test_upload_state.py
git commit -m "feat: recover and download partial auto-query results"
```

---

### Task 5: Pin compatibility and verify the complete failure/recovery matrix

**Files:**
- Modify: `requirements.txt:1`
- Modify: `tests/test_auto_query_checkpoint.py`
- Modify: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: all APIs and page behavior from Tasks 1-4.
- Produces: a verified release candidate with the Streamlit floor required by `on_click="ignore"`.

- [ ] **Step 1: Add final regression assertions for Streamlit and final-ZIP failure preservation**

Add to `tests/test_auto_query_workflow.py`:

```python
    def test_page_6_keeps_partial_artifacts_when_full_zip_build_fails(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        run_block = page_text.split("if start_run:", 1)[1]

        self.assertLess(run_block.index("checkpoint_callback=handle_checkpoint"), run_block.index("build_auto_workflow_zip"))
        self.assertIn("failed_checkpoint = replace(", run_block)
        self.assertIn("handle_checkpoint(failed_checkpoint)", run_block)
        self.assertIn("partial=True", page_text)

    def test_requirements_support_non_rerunning_download_buttons(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        self.assertIn("streamlit>=1.43,<2", requirements)
```

Add to `tests/test_auto_query_checkpoint.py`:

```python
    def test_uncommitted_temporary_artifacts_are_not_loadable(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            digest = __import__("hashlib").sha256(token.encode("ascii")).hexdigest()
            run_dir = root / digest
            (run_dir / "tables").mkdir(parents=True)
            (run_dir / "tables" / ".partial.tmp").write_bytes(b"partial")

            with self.assertRaises(CheckpointStorageError):
                load_checkpoint(token, root=root)
```

- [ ] **Step 2: Run the new assertions and confirm the version test fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_page_6_keeps_partial_artifacts_when_full_zip_build_fails tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_requirements_support_non_rerunning_download_buttons tests.test_auto_query_checkpoint.AutoQueryCheckpointTests.test_uncommitted_temporary_artifacts_are_not_loadable -v
```

Expected: the requirements assertion fails until the Streamlit floor is updated; the other two tests pass after Tasks 3-4.

- [ ] **Step 3: Raise only the Streamlit minimum version**

Change the first requirement to:

```text
streamlit>=1.43,<2
```

- [ ] **Step 4: Run all targeted checkpoint and page suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_checkpoint tests.test_auto_query_workflow tests.test_auto_query_progress tests.test_upload_state -v
```

Expected: all tests pass with no errors or failures.

- [ ] **Step 5: Run the complete repository test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: the full suite exits with code 0 and reports `OK`.

- [ ] **Step 6: Compile every application entry point and shared module**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: exit code 0 with no `SyntaxError` output.

- [ ] **Step 7: Perform a local Streamlit smoke test with cached or mocked data**

Run:

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

Verify in page 6:

1. Upload a small workbook and select at least two modules.
2. Confirm the first terminal module appears under “已完成模块，可立即下载” before the second module ends.
3. Download its XLSX and confirm the page does not rerun or reset progress.
4. Refresh the URL containing `?run=<token>` and confirm the recovery message, log, tables, and module download return without re-uploading.
5. Confirm a recovered `running` checkpoint says “上次运行未正常结束”.
6. Confirm “清空当前数据” removes the `run` query parameter and that reopening that URL no longer restores the deleted run.
7. Complete a normal run and compare the final ZIP names against `test_auto_workflow_zip_groups_results_by_module`; `05_ECHA/ECHA_Results.xlsx` must remain unchanged.

- [ ] **Step 8: Inspect the change set for accidental files and commit compatibility**

Run:

```powershell
git status --short
git diff --check
```

Expected: only the files named in this plan are modified; `git diff --check` produces no output. Do not stage unrelated pre-existing files under `docs/` or `outputs/`.

```powershell
git add requirements.txt tests/test_auto_query_checkpoint.py tests/test_auto_query_workflow.py
git commit -m "test: verify auto-query checkpoint recovery"
```

---

## Completion Evidence

Before reporting completion, record these exact results in the handoff:

- Targeted test command and total tests passed.
- Full `unittest discover` result and total tests passed.
- `compileall` exit code.
- Local smoke-test outcomes for immediate module download, refresh recovery, explicit clear, and unchanged final ZIP.
- The final `git status --short`, explicitly separating implementation files from unrelated pre-existing untracked files.
