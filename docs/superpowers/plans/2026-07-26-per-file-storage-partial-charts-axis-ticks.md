# Per-File Results, Configurable Storage, Partial Charts, and Axis Ticks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate selected one-click results and exports by uploaded file, unify configurable query/checkpoint storage, preserve charts in intermediate checkpoints, and make the two reported Y-axis top ticks visible.

**Architecture:** Keep one external query pass over the deduplicated identity universe, propagate its stable identity key into every external result row, and derive per-file module views from the existing membership table. Resolve query and checkpoint paths through one storage service, generate charts cumulatively before every checkpoint, and keep combined Pov-LRTP/PBM/ToxPi semantics unchanged.

**Tech Stack:** Python 3, pandas, matplotlib, Streamlit, SQLite, openpyxl, ZIP, unittest, Streamlit AppTest

## Global Constraints

- Page previews and XLSX/ZIP exports use the same per-file grouping for local screening, EPA CompTox, ECHA, and source origin.
- Identifier completion and EPI Suite remain shared; Pov-LRTP/PBM/ToxPi remains the only cross-file combined result.
- External services are queried once per deduplicated identity, even when the identity belongs to multiple files.
- Existing two-stage ToxPi normalization, weights, eligibility, robustness, and display limits do not change.
- A custom storage root expands to `query_cache/chempriority_queries.sqlite3` and `checkpoints/auto_query_runs`.
- Existing specific environment variables override the unified root; legacy paths remain unchanged when no custom setting exists.
- Switching storage roots does not move, merge, or delete old data.
- Every checkpoint contains all PNG/PDF charts whose source tables are available at that stage.
- Existing unrelated untracked files remain untouched and unstaged.

---

### Task 1: Add the unified storage-path resolver and persistent preference

**Files:**
- Create: `src/storage_paths.py`
- Create: `tests/test_storage_paths.py`

**Interfaces:**
- Produces: `StoragePaths`
- Produces: `resolve_storage_paths(environ=None, preference_file=None, cwd=None) -> StoragePaths`
- Produces: `save_storage_root(root, preference_file=None) -> StoragePaths`
- Produces: `reset_storage_root(preference_file=None) -> StoragePaths`
- Produces: `default_storage_preference_file(environ=None, home=None) -> Path`

- [ ] **Step 1: Write failing precedence, persistence, and validation tests**

```python
class StoragePathTests(unittest.TestCase):
    def test_saved_root_expands_to_query_and_checkpoint_subpaths(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            preference = base / "config" / "storage.json"
            root = base / "selected-cache"

            saved = save_storage_root(root, preference_file=preference)
            resolved = resolve_storage_paths(
                environ={},
                preference_file=preference,
                cwd=base,
            )

            self.assertEqual(saved.storage_root, root.resolve())
            self.assertEqual(
                resolved.query_cache_path,
                root.resolve() / "query_cache" / "chempriority_queries.sqlite3",
            )
            self.assertEqual(
                resolved.checkpoint_root,
                root.resolve() / "checkpoints" / "auto_query_runs",
            )
            self.assertEqual(resolved.query_path_source, "saved setting")
            self.assertEqual(resolved.checkpoint_path_source, "saved setting")

    def test_specific_environment_paths_override_unified_and_saved_roots(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            preference = base / "storage.json"
            save_storage_root(base / "saved", preference_file=preference)
            resolved = resolve_storage_paths(
                environ={
                    "CHEMPRIORITY_STORAGE_ROOT": str(base / "unified"),
                    "CHEMPRIORITY_QUERY_CACHE_PATH": str(base / "query.sqlite3"),
                    "CHEMPRIORITY_CHECKPOINT_ROOT": str(base / "runs"),
                },
                preference_file=preference,
                cwd=base,
            )
            self.assertEqual(resolved.query_cache_path, (base / "query.sqlite3").resolve())
            self.assertEqual(resolved.checkpoint_root, (base / "runs").resolve())
            self.assertEqual(resolved.query_path_source, "environment")
            self.assertEqual(resolved.checkpoint_path_source, "environment")

    def test_no_setting_preserves_legacy_paths(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            resolved = resolve_storage_paths(
                environ={},
                preference_file=base / "missing.json",
                cwd=base,
            )
            self.assertEqual(
                resolved.query_cache_path,
                (base / ".cache" / "chempriority_queries.sqlite3").resolve(),
            )
            self.assertEqual(
                resolved.checkpoint_root,
                (base / ".cache" / "auto_query_runs").resolve(),
            )

    def test_relative_or_file_root_is_rejected_without_replacing_preference(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            preference = base / "storage.json"
            original = base / "original"
            save_storage_root(original, preference_file=preference)
            with self.assertRaisesRegex(ValueError, "absolute"):
                save_storage_root(Path("relative"), preference_file=preference)
            file_root = base / "not-a-directory"
            file_root.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "directory"):
                save_storage_root(file_root, preference_file=preference)
            self.assertEqual(
                resolve_storage_paths(
                    environ={},
                    preference_file=preference,
                    cwd=base,
                ).storage_root,
                original.resolve(),
            )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_storage_paths -v
```

Expected: `ModuleNotFoundError: No module named 'src.storage_paths'`.

- [ ] **Step 3: Implement the immutable resolver and atomic preference writes**

Create `src/storage_paths.py` with these concrete definitions:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 1
LEGACY_QUERY_CACHE_PATH = Path(".cache/chempriority_queries.sqlite3")
LEGACY_CHECKPOINT_ROOT = Path(".cache/auto_query_runs")


@dataclass(frozen=True)
class StoragePaths:
    storage_root: Path | None
    query_cache_path: Path
    checkpoint_root: Path
    query_path_source: str
    checkpoint_path_source: str
    preference_file: Path
    warning: str = ""


def default_storage_preference_file(environ=None, home=None) -> Path:
    environ = os.environ if environ is None else environ
    if environ.get("LOCALAPPDATA"):
        return Path(environ["LOCALAPPDATA"]) / "ChemPriority" / "storage.json"
    config_root = environ.get("XDG_CONFIG_HOME")
    if config_root:
        return Path(config_root) / "chempriority" / "storage.json"
    return Path(home or Path.home()) / ".config" / "chempriority" / "storage.json"


def _read_saved_root(preference_file: Path) -> tuple[Path | None, str]:
    if not preference_file.is_file():
        return None, ""
    try:
        payload = json.loads(preference_file.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported schema")
        root = Path(payload["storage_root"])
        if not root.is_absolute():
            raise ValueError("saved root is not absolute")
        return root.resolve(), ""
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return None, f"缓存位置设置无法读取：{exc}"


def resolve_storage_paths(environ=None, preference_file=None, cwd=None) -> StoragePaths:
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else Path(cwd)
    preference_file = Path(
        preference_file or default_storage_preference_file(environ=environ)
    )
    saved_root, warning = _read_saved_root(preference_file)
    unified_text = str(environ.get("CHEMPRIORITY_STORAGE_ROOT", "")).strip()
    unified_root = Path(unified_text).resolve() if unified_text else saved_root
    root_source = "environment" if unified_text else "saved setting"

    query_text = str(environ.get("CHEMPRIORITY_QUERY_CACHE_PATH", "")).strip()
    checkpoint_text = str(environ.get("CHEMPRIORITY_CHECKPOINT_ROOT", "")).strip()
    if query_text:
        query_path = Path(query_text).resolve()
        query_source = "environment"
    elif unified_root is not None:
        query_path = unified_root / "query_cache" / "chempriority_queries.sqlite3"
        query_source = root_source
    else:
        query_path = (cwd / LEGACY_QUERY_CACHE_PATH).resolve()
        query_source = "default"
    if checkpoint_text:
        checkpoint_root = Path(checkpoint_text).resolve()
        checkpoint_source = "environment"
    elif unified_root is not None:
        checkpoint_root = unified_root / "checkpoints" / "auto_query_runs"
        checkpoint_source = root_source
    else:
        checkpoint_root = (cwd / LEGACY_CHECKPOINT_ROOT).resolve()
        checkpoint_source = "default"
    return StoragePaths(
        storage_root=unified_root,
        query_cache_path=query_path,
        checkpoint_root=checkpoint_root,
        query_path_source=query_source,
        checkpoint_path_source=checkpoint_source,
        preference_file=preference_file,
        warning=warning,
    )


def _probe_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".chempriority-write-probe-{uuid4().hex}"
    try:
        with probe.open("wb") as handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        probe.unlink(missing_ok=True)


def save_storage_root(root, preference_file=None) -> StoragePaths:
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("缓存根目录必须是绝对路径")
    if root.exists() and not root.is_dir():
        raise ValueError("缓存根目录必须是目录")
    root = root.resolve()
    _probe_directory(root / "query_cache")
    _probe_directory(root / "checkpoints" / "auto_query_runs")
    preference = Path(preference_file or default_storage_preference_file())
    preference.parent.mkdir(parents=True, exist_ok=True)
    temporary = preference.with_name(f".{preference.name}.{uuid4().hex}.tmp")
    payload = {"schema_version": SCHEMA_VERSION, "storage_root": str(root)}
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, preference)
    finally:
        temporary.unlink(missing_ok=True)
    return resolve_storage_paths(preference_file=preference)


def reset_storage_root(preference_file=None) -> StoragePaths:
    preference = Path(preference_file or default_storage_preference_file())
    preference.unlink(missing_ok=True)
    return resolve_storage_paths(preference_file=preference)
```

- [ ] **Step 4: Run the storage tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_storage_paths -v
```

Expected: all storage-path tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/storage_paths.py tests/test_storage_paths.py
git commit -m "feat: resolve configurable ChemPriority storage paths"
```

---

### Task 2: Route query cache and checkpoints through the storage resolver

**Files:**
- Modify: `src/query_cache.py`
- Modify: `src/auto_query_checkpoint.py`
- Modify: `tests/test_query_cache.py`
- Modify: `tests/test_auto_query_checkpoint.py`

**Interfaces:**
- Changes: `current_cache_path()` resolves the saved/environment path at call time unless `use_cache_path()` is active.
- Produces: `current_checkpoint_root() -> Path`
- Changes: checkpoint public functions use `root=None` and resolve the active root inside each call.
- Preserves: explicit `root=` isolation and legacy no-setting paths.

- [ ] **Step 1: Write failing call-time path tests**

Add:

```python
def test_current_cache_path_uses_storage_resolver_at_call_time(self):
    first = Path("first.sqlite3")
    second = Path("second.sqlite3")
    with patch(
        "src.query_cache.resolve_storage_paths",
        side_effect=[
            SimpleNamespace(query_cache_path=first),
            SimpleNamespace(query_cache_path=second),
        ],
    ):
        self.assertEqual(current_cache_path(), first)
        self.assertEqual(current_cache_path(), second)


def test_checkpoint_default_root_is_resolved_at_each_call(self):
    with TemporaryDirectory() as first, TemporaryDirectory() as second:
        roots = [
            SimpleNamespace(checkpoint_root=Path(first)),
            SimpleNamespace(checkpoint_root=Path(second)),
        ]
        with patch(
            "src.auto_query_checkpoint.resolve_storage_paths",
            side_effect=roots,
        ):
            token_a = generate_run_token()
            token_b = generate_run_token()
            now = datetime(2026, 7, 26, tzinfo=timezone.utc)
            save_checkpoint(
                token_a,
                example_checkpoint(now),
                ["A.xlsx"],
                OrderedDict(),
            )
            save_checkpoint(
                token_b,
                example_checkpoint(now),
                ["B.xlsx"],
                OrderedDict(),
            )
        self.assertTrue(any(Path(first).iterdir()))
        self.assertTrue(any(Path(second).iterdir()))
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_cache tests.test_auto_query_checkpoint -v
```

Expected: resolver imports/current checkpoint root behavior are missing and the second checkpoint still uses the import-time default.

- [ ] **Step 3: Refactor query-cache resolution**

In `src/query_cache.py`:

```python
from src.storage_paths import LEGACY_QUERY_CACHE_PATH, resolve_storage_paths

DEFAULT_CACHE_PATH = LEGACY_QUERY_CACHE_PATH


def current_cache_path():
    override = _cache_path_var.get()
    if override is not None:
        return Path(override)
    return Path(resolve_storage_paths().query_cache_path)
```

Keep `use_cache_path()` unchanged so tests and scoped callers retain priority.

- [ ] **Step 4: Refactor checkpoint defaults to call-time resolution**

In `src/auto_query_checkpoint.py`:

```python
from src.storage_paths import LEGACY_CHECKPOINT_ROOT, resolve_storage_paths

DEFAULT_CHECKPOINT_ROOT = LEGACY_CHECKPOINT_ROOT


def current_checkpoint_root() -> Path:
    return Path(resolve_storage_paths().checkpoint_root)


def _resolved_root(root) -> Path:
    return current_checkpoint_root() if root is None else Path(root)


def _run_directory(token, root=None) -> Path:
    token = str(token)
    if not TOKEN_PATTERN.fullmatch(token):
        raise InvalidRunToken("恢复令牌格式无效")
    root = _resolved_root(root).resolve()
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    candidate = root / digest
    resolved = candidate.resolve()
    if candidate.parent != root or resolved != candidate:
        raise InvalidRunToken("恢复路径越界")
    return candidate
```

Change only the default declarations, without altering their explicit-root bodies:

- `save_checkpoint(token, checkpoint: AutoWorkflowCheckpoint, input_filenames: Iterable[str] | str, module_workbooks: Mapping[str, AutoWorkflowModuleWorkbook], *, root=None, now=None) -> Path`
- `load_checkpoint(token, *, root=None, now=None)`
- `delete_checkpoint(token, *, root=None) -> bool`
- `cleanup_expired_checkpoints(*, root=None, now=None)`

- [ ] **Step 5: Run cache/checkpoint tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_query_cache tests.test_auto_query_checkpoint -v
```

Expected: all tests pass, including explicit temporary roots and call-time defaults.

- [ ] **Step 6: Commit**

```powershell
git add src/query_cache.py src/auto_query_checkpoint.py tests/test_query_cache.py tests/test_auto_query_checkpoint.py
git commit -m "feat: use configured query and checkpoint storage"
```

---

### Task 3: Add shared storage controls and safe page-6 detachment

**Files:**
- Create: `src/storage_controls.py`
- Modify: `src/query_cache.py`
- Modify: `pages/0_综合筛查流程.py`
- Modify: `pages/3_EPISuite环境归趋.py`
- Modify: `pages/4_化合物用途查询.py`
- Modify: `pages/6_一键批量查询.py`
- Create: `tests/test_storage_controls.py`
- Modify: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Produces: `render_storage_location_controls(st_module, prefix, on_change=None)`
- Changes: `render_query_cache_controls(st_module, prefix, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS, show_storage=True, on_storage_changed=None)`
- Produces in page 6: `_detach_auto_query_recovery_for_storage_change()`
- Preserves: old checkpoint data on disk.

- [ ] **Step 1: Write failing fake-Streamlit and page-6 tests**

Define a complete minimal fake and test that:

```python
class FakeStreamlit:
    def __init__(self, root):
        self.root = str(root)
        self.success_messages = []
        self.error_messages = []

    def caption(self, value):
        return None

    def warning(self, value):
        return None

    def info(self, value):
        return None

    def text_input(self, label, **kwargs):
        return self.root

    def button(self, label, **kwargs):
        return label == "保存并切换"

    def success(self, value):
        self.success_messages.append(value)

    def error(self, value):
        self.error_messages.append(value)

    def rerun(self):
        return None


def test_save_storage_control_validates_and_calls_change_callback(self):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake = FakeStreamlit(root)
        resolved = StoragePaths(
            storage_root=root,
            query_cache_path=root / "query_cache" / "chempriority_queries.sqlite3",
            checkpoint_root=root / "checkpoints" / "auto_query_runs",
            query_path_source="saved setting",
            checkpoint_path_source="saved setting",
            preference_file=root / "storage.json",
        )
        changed = []
        with patch(
            "src.storage_controls.save_storage_root",
            return_value=resolved,
        ):
            render_storage_location_controls(
                fake,
                "test",
                on_change=lambda: changed.append(True),
            )
        self.assertEqual(changed, [True])
        self.assertIn("保存并切换", fake.success_messages[0])


def test_page_6_storage_switch_detaches_session_without_deleting_checkpoint(self):
    page_text = PAGE_6_PATH.read_text(encoding="utf-8")
    start = page_text.index(
        "def _detach_auto_query_recovery_for_storage_change():"
    )
    end = page_text.index("\n\n", start)
    function_text = page_text[start:end]
    self.assertIn('st.session_state.pop("auto_query_run_token", None)', function_text)
    self.assertIn('st.query_params.pop("run", None)', function_text)
    self.assertNotIn("delete_checkpoint(", function_text)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_storage_controls tests.test_auto_query_workflow -v
```

Expected: the storage-control module and page detachment function do not exist.

- [ ] **Step 3: Implement the shared storage component**

Create `src/storage_controls.py`:

```python
from src.storage_paths import (
    resolve_storage_paths,
    reset_storage_root,
    save_storage_root,
)


def render_storage_location_controls(st_module, prefix, on_change=None):
    paths = resolve_storage_paths()
    if paths.warning:
        st_module.warning(paths.warning)
    st_module.caption(f"查询缓存：{paths.query_cache_path}（{paths.query_path_source}）")
    st_module.caption(f"断点缓存：{paths.checkpoint_root}（{paths.checkpoint_path_source}）")
    environment_locked = (
        paths.query_path_source == "environment"
        and paths.checkpoint_path_source == "environment"
    )
    root_value = "" if paths.storage_root is None else str(paths.storage_root)
    selected = st_module.text_input(
        "缓存与断点存储根目录（绝对路径）",
        value=root_value,
        disabled=environment_locked,
        key=f"{prefix}_storage_root",
    )
    if environment_locked:
        st_module.info("当前路径由环境变量控制，页面设置不会覆盖管理员配置。")
        return paths
    if (
        paths.query_path_source == "environment"
        or paths.checkpoint_path_source == "environment"
    ):
        st_module.info("保存的根目录只影响未被专用环境变量覆盖的存储路径。")
    if st_module.button("保存并切换", key=f"{prefix}_save_storage_root"):
        try:
            paths = save_storage_root(selected)
        except (OSError, ValueError) as exc:
            st_module.error(f"缓存位置未更改：{exc}")
        else:
            if on_change is not None:
                on_change()
            st_module.success("缓存与断点存储位置已保存并切换。")
            st_module.rerun()
    if st_module.button("恢复默认位置", key=f"{prefix}_reset_storage_root"):
        paths = reset_storage_root()
        if on_change is not None:
            on_change()
        st_module.success("已恢复默认位置；旧目录中的数据未删除。")
        st_module.rerun()
    return paths
```

- [ ] **Step 4: Reuse the component across pages**

Add to `render_query_cache_controls()`:

```python
if show_storage:
    render_storage_location_controls(
        st_module,
        prefix,
        on_change=on_storage_changed,
    )
```

Replace page-0 and page-4 bespoke cache captions/clear buttons with `render_query_cache_controls()`. Keep page-3 and page-6 cache metrics and prune/clear actions.

In page 6, define and pass:

```python
def _detach_auto_query_recovery_for_storage_change():
    clear_uploads(
        st.session_state,
        (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
    )
    st.session_state.pop("auto_query_run_token", None)
    st.query_params.pop("run", None)
```

Render the storage control before the first `cleanup_expired_checkpoints()` and `load_checkpoint()` call. Render later query-cache metrics with `show_storage=False`.

- [ ] **Step 5: Run page/cache tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_storage_controls tests.test_query_cache tests.test_auto_query_checkpoint tests.test_auto_query_workflow tests.test_cp_screening_workflow -v
```

Expected: storage settings are shared, page 6 resolves the new root before recovery, and switching does not delete the old token directory.

- [ ] **Step 6: Commit**

```powershell
git add src/storage_controls.py src/query_cache.py pages/0_综合筛查流程.py pages/3_EPISuite环境归趋.py pages/4_化合物用途查询.py pages/6_一键批量查询.py tests/test_storage_controls.py tests/test_auto_query_workflow.py
git commit -m "feat: configure cache and checkpoint storage in the app"
```

---

### Task 4: Propagate stable input identities through every external module

**Files:**
- Create: `src/query_identity.py`
- Modify: `src/auto_query_workflow.py`
- Modify: `src/comptox_use.py`
- Modify: `src/echa_use.py`
- Modify: `src/echa_ghs.py`
- Modify: `src/source_origin.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_comptox_dashboard_mode.py`
- Modify: `tests/test_echa_use.py`
- Modify: `tests/test_echa_ghs.py`
- Modify: `tests/test_source_origin.py`

**Interfaces:**
- Produces: `INPUT_IDENTITY_KEY = "input_identity_key"`
- Produces: `attach_input_identity(frame, row) -> pd.DataFrame`
- Changes: `query_input` retains one `input_identity_key` per deduplicated identity.
- Changes: every external result frame receives that exact key.

- [ ] **Step 1: Write one failing test per external batch**

Use the existing mocked one-row batch fixtures and assert:

```python
def _identity_input():
    return pd.DataFrame(
        {
            "compound": ["Shared"],
            "cas": ["64-17-5"],
            "input_identity_key": ["cas:64-17-5"],
        }
    )


summary, candidates, errors = run_comptox_use_batch(
    _identity_input(),
    delay_seconds=0,
    max_workers=1,
)
self.assertEqual(summary["input_identity_key"].tolist(), ["cas:64-17-5"])
self.assertTrue(candidates["input_identity_key"].eq("cas:64-17-5").all())
```

Add three explicit sibling tests:

```python
def test_echa_batch_outputs_retain_input_identity_key(self):
    summary, candidates, dossiers, errors = run_echa_use_batch(
        _identity_input(),
        delay_seconds=0,
    )
    for frame in (summary, candidates, dossiers, errors):
        self.assertIn("input_identity_key", frame.columns)
        if not frame.empty:
            self.assertTrue(frame["input_identity_key"].eq("cas:64-17-5").all())


def test_echa_ghs_batch_outputs_retain_input_identity_key(self):
    summary, classifications, errors = run_echa_ghs_batch(
        _identity_input(),
        delay_seconds=0,
    )
    for frame in (summary, classifications, errors):
        self.assertIn("input_identity_key", frame.columns)
        if not frame.empty:
            self.assertTrue(frame["input_identity_key"].eq("cas:64-17-5").all())


def test_source_origin_batch_outputs_retain_input_identity_key(self):
    summary, evidence, errors = run_source_origin_batch(
        _identity_input(),
        delay_seconds=0,
    )
    for frame in (summary, evidence, errors):
        self.assertIn("input_identity_key", frame.columns)
        if not frame.empty:
            self.assertTrue(frame["input_identity_key"].eq("cas:64-17-5").all())
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_echa_ghs tests.test_source_origin -v
```

Expected: `input_identity_key` is absent or source-origin normalization drops it.

- [ ] **Step 3: Add the shared identity decorator**

Create:

```python
INPUT_IDENTITY_KEY = "input_identity_key"


def input_identity_key(row) -> str:
    value = row.get(INPUT_IDENTITY_KEY, row.get("identity_key", ""))
    return "" if value is None or pd.isna(value) else str(value).strip()


def attach_input_identity(frame, row) -> pd.DataFrame:
    output = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    key = input_identity_key(row)
    if INPUT_IDENTITY_KEY in output.columns:
        output[INPUT_IDENTITY_KEY] = output[INPUT_IDENTITY_KEY].fillna(key)
    else:
        output.insert(0, INPUT_IDENTITY_KEY, key)
    return output
```

- [ ] **Step 4: Preserve identity in combined query input**

Change `_build_identifier_input_from_epi_universe()` and `_query_input_from_identifiers()` so returned columns are:

```python
query_columns = [
    *REQUIRED_IDENTIFIER_COLUMNS,
    *(
        ["input_identity_key"]
        if "input_identity_key" in output.columns
        else []
    ),
]
```

Copy `identity_key` from the EPI universe/completed identifiers into `input_identity_key`. Legacy single-file input may leave it blank.

- [ ] **Step 5: Decorate each row's batch frames before concatenation**

In each concurrent batch aggregation loop:

```python
row = items[result.index][1]
summary_df, candidates_df, errors_df = result.value
summary_frames.append(attach_input_identity(summary_df, row))
candidate_frames.append(attach_input_identity(candidates_df, row))
error_frames.append(attach_input_identity(errors_df, row))
```

Use these concrete per-runner frame tuples:

```python
# ECHA REACH
summary_df, candidates_df, dossiers_df, errors_df = result.value
frames = tuple(
    attach_input_identity(frame, row)
    for frame in (summary_df, candidates_df, dossiers_df, errors_df)
)

# ECHA GHS/C&L
summary_df, classifications_df, errors_df = result.value
frames = tuple(
    attach_input_identity(frame, row)
    for frame in (summary_df, classifications_df, errors_df)
)

# Source origin
summary_df, evidence_df, errors_df = result.value
frames = tuple(
    attach_input_identity(frame, row)
    for frame in (summary_df, evidence_df, errors_df)
)
```

Append each tuple member to its existing accumulator. For every `result.error` branch, pass each generated failure frame through `attach_input_identity(frame, row)` before appending it.

Modify `normalize_source_input_columns()` to retain `input_identity_key`:

```python
columns = list(REQUIRED_IDENTIFIER_COLUMNS)
if INPUT_IDENTITY_KEY in normalized.columns:
    columns.append(INPUT_IDENTITY_KEY)
return normalized[columns]
```

- [ ] **Step 6: Run identity tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_echa_ghs tests.test_source_origin tests.test_auto_query_workflow -v
```

Expected: every result and error row retains the originating identity without additional external calls.

- [ ] **Step 7: Commit**

```powershell
git add src/query_identity.py src/auto_query_workflow.py src/comptox_use.py src/echa_use.py src/echa_ghs.py src/source_origin.py tests/test_auto_query_workflow.py tests/test_comptox_dashboard_mode.py tests/test_echa_use.py tests/test_echa_ghs.py tests/test_source_origin.py
git commit -m "feat: retain input identity across external query results"
```

---

### Task 5: Build deterministic per-file module views and local chart artifacts

**Files:**
- Create: `src/auto_query_file_views.py`
- Modify: `src/auto_query_workflow.py`
- Modify: `src/multi_file_screening.py`
- Create: `tests/test_auto_query_file_views.py`
- Modify: `tests/test_multi_file_screening.py`

**Interfaces:**
- Produces: `FileModuleView`
- Produces: `build_file_module_views(result) -> OrderedDict[str, OrderedDict[str, FileModuleView]]`
- Produces: `safe_export_names(input_mappings) -> OrderedDict[str, str]`
- Produces: `scoped_chart_key(module_slug, safe_export_name, chart_name) -> str`
- Changes: prepared local charts are scoped by primary file and survive the local checkpoint.

- [ ] **Step 1: Write failing two-file/shared-compound view tests**

Create a result with:

```python
membership = pd.DataFrame(
    {
        "primary_file": ["A.xlsx", "A.xlsx", "B.xlsx", "B.xlsx"],
        "sample_id": ["A", "A", "B", "B"],
        "identity_key": ["cas:a", "cas:shared", "cas:b", "cas:shared"],
        "compound": ["Only A", "Shared", "Only B", "Shared"],
    }
)
```

Assert:

```python
views = build_file_module_views(result)
epa_a = views["comptox_use"]["A.xlsx"].tables["CompTox_Summary"]
epa_b = views["comptox_use"]["B.xlsx"].tables["CompTox_Summary"]
self.assertEqual(set(epa_a["compound"]), {"Only A", "Shared"})
self.assertEqual(set(epa_b["compound"]), {"Only B", "Shared"})
self.assertNotIn("Only B", set(epa_a["compound"]))
self.assertNotIn("Only A", set(epa_b["compound"]))
```

Also assert local tables partition by `sample_id`, upload order follows `Input_File_Mappings`, safe-name collisions receive a deterministic hash suffix, and an unknown identity enters `unassigned` rather than every file.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_file_views tests.test_multi_file_screening -v
```

Expected: `src.auto_query_file_views` is missing and local chart artifacts are not prepared.

- [ ] **Step 3: Define the view model and safe names**

```python
@dataclass(frozen=True)
class FileModuleView:
    module_slug: str
    primary_file: str
    sample_id: str
    safe_export_name: str
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, AutoWorkflowChart]


def scoped_chart_key(module_slug, safe_export_name, chart_name):
    return f"{module_slug}__{safe_export_name}__{chart_name}"
```

Sanitize names to `[A-Za-z0-9_]+`; if two sanitized stems collide case-insensitively, append the first eight characters of `sha256(original_filename.casefold())`.

- [ ] **Step 4: Implement authoritative partition maps**

Build:

```python
identity_files = (
    membership.groupby("identity_key", sort=False)["primary_file"]
    .apply(lambda values: tuple(dict.fromkeys(values)))
    .to_dict()
)
```

Partition local raw tables by `sample_id` and external raw tables by `input_identity_key`. Build an `unassigned` view only for non-empty rows with blank/unknown identity keys and add a warning table describing them.

For each file, rebuild rather than filter these derived tables:

- `Product_Use_Categories`;
- `Functional_Uses_Predicted`;
- `Functional_Uses_Reported`;
- `EPA_PUC_Pie_Data`;
- `EPA_Predicted_Pie_Data`;
- `EPA_Reported_Pie_Data`;
- `ECHA_Reported_Pie_Data`;
- `Source_Origin_Pie_Data`.

Use the existing public builder/extractor functions with the file-specific candidate and compound universes.

- [ ] **Step 5: Preserve individual local charts**

In `auto_input_from_multi_file_result()`, iterate `result.screening_results`, map `sample_id` back to `file_name`, call `_load_local_screening_charts(screening_result)`, and insert each chart with:

```python
key = scoped_chart_key(
    "local_screening",
    safe_name,
    public_chart_name,
)
```

Add `safe_export_name` to `Input_File_Mappings`. Keep combined DF/sample audit tables unchanged for Pov-LRTP/PBM/ToxPi.

- [ ] **Step 6: Run per-file view tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_file_views tests.test_multi_file_screening tests.test_auto_query_workflow -v
```

Expected: A/shared/B membership is exact, no cross-file leakage occurs, and local chart bytes remain file-scoped.

- [ ] **Step 7: Commit**

```powershell
git add src/auto_query_file_views.py src/auto_query_workflow.py src/multi_file_screening.py tests/test_auto_query_file_views.py tests/test_multi_file_screening.py
git commit -m "feat: derive per-file auto-query module views"
```

---

### Task 6: Generate cumulative per-file charts before each checkpoint

**Files:**
- Modify: `src/auto_query_workflow.py`
- Modify: `src/auto_query_file_views.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_auto_query_file_views.py`

**Interfaces:**
- Produces: `update_auto_workflow_charts(result, completed_step=None) -> tuple[OrderedDict, list[str]]`
- Produces: `available_chart_sources(result, completed_step=None) -> list[tuple[str, dict]]`
- Changes: every module checkpoint carries cumulative available chart bytes.
- Preserves: combined ToxPi chart keys.

- [ ] **Step 1: Write failing staged-checkpoint tests**

Capture checkpoint callbacks and assert:

```python
epa_checkpoint = next(
    item for item in checkpoints
    if item.current_step == "EPA CompTox 用途"
)
self.assertIn(
    scoped_chart_key(
        "comptox_use",
        "A",
        "EPA_Product_Use_Category_Distribution",
    ),
    epa_checkpoint.result.charts,
)
toxpi_checkpoint = next(
    item for item in checkpoints
    if item.current_step == "Pov-LRTP / PBM / ToxPi"
)
self.assertIn("ToxPi_Ranking_Bar", toxpi_checkpoint.result.charts)
self.assertIn(
    scoped_chart_key(
        "comptox_use",
        "A",
        "EPA_Reported_Functional_Use_Evidence",
    ),
    toxpi_checkpoint.result.charts,
)
```

Add a forced figure failure and assert the checkpoint retains other charts and contains a `Chart generation` warning.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_auto_query_file_views -v
```

Expected: intermediate checkpoints have empty charts and dependent evidence charts appear only in the final post-run call.

- [ ] **Step 3: Implement idempotent chart updates**

Refactor chart generation:

```python
def update_auto_workflow_charts(result, completed_step=None):
    charts = OrderedDict(result.charts)
    warnings = []
    for chart_key, source_config in available_chart_sources(
        result,
        completed_step=completed_step,
    ):
        if chart_key in charts:
            continue
        fig = None
        try:
            chart_df = _build_chart_data(source_config)
            if chart_df.empty:
                continue
            fig = _build_chart_figure(chart_df, source_config)
            charts[chart_key] = AutoWorkflowChart(
                title=source_config["title"],
                png=figure_to_png_bytes(fig).getvalue(),
                pdf=figure_to_pdf_bytes(fig).getvalue(),
            )
        except Exception as exc:
            warnings.append(f"{source_config['title']}: {exc}")
        finally:
            if fig is not None:
                plt.close(fig)
    return charts, warnings
```

Implement `available_chart_sources()` by iterating `build_file_module_views(result)` for local/EPA/ECHA/source modules and passing each view through the existing source-config builders. Return `(scoped_chart_key(module_slug, view.safe_export_name, source_config["file_prefix"]), source_config)` pairs. Append the existing unscoped ToxPi source configs. Filter out source configs with `requires_toxpi=True` until `_toxpi_evidence_chart_selection(result)` returns a valid selection.

- [ ] **Step 4: Update charts before `emit_checkpoint()`**

Inside `run_auto_query_workflow()`:

```python
def update_charts_for(step):
    partial = current_result()
    updated, messages = update_auto_workflow_charts(
        partial,
        completed_step=step,
    )
    charts.clear()
    charts.update(updated)
    for message in messages:
        add_warning("Chart generation", message)
```

Call `update_charts_for(step)` after each module records its tables/status and before its `emit_checkpoint(step)`. Replace the final-only page call with the same idempotent updater.

- [ ] **Step 5: Verify cumulative charts and warning isolation**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_auto_query_file_views tests.test_auto_query_checkpoint -v
```

Expected: each checkpoint has the charts possible at that stage, prior bytes are preserved, and one chart failure does not fail the module.

- [ ] **Step 6: Commit**

```powershell
git add src/auto_query_workflow.py src/auto_query_file_views.py tests/test_auto_query_workflow.py tests/test_auto_query_file_views.py
git commit -m "feat: checkpoint charts as modules complete"
```

---

### Task 7: Export per-file workbooks and chart folders in live, partial, recovered, and full packages

**Files:**
- Modify: `src/auto_query_workflow.py`
- Modify: `src/auto_query_checkpoint.py`
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_auto_query_checkpoint.py`

**Interfaces:**
- Extends: `AutoWorkflowModuleWorkbook` with `module_slug`, `primary_file`, and `safe_export_name`.
- Produces: `build_auto_workflow_module_workbooks(result, step) -> OrderedDict[str, AutoWorkflowModuleWorkbook]`
- Changes: checkpoint schema version `3`, while loading versions `1` and `2`.
- Changes: affected module downloads contain one file folder per primary workbook.

- [ ] **Step 1: Write failing real-payload export tests**

Assert the full ZIP contains:

```python
expected = {
    "01_Local_Screening/A/Local_Screening_Results.xlsx",
    "01_Local_Screening/B/Local_Screening_Results.xlsx",
    "04_EPA_CompTox/A/EPA_CompTox_Results.xlsx",
    "04_EPA_CompTox/B/EPA_CompTox_Results.xlsx",
    "05_ECHA/A/ECHA_Results.xlsx",
    "05_ECHA/B/ECHA_Results.xlsx",
    "06_Source_Origin/A/Source_Origin_Results.xlsx",
    "06_Source_Origin/B/Source_Origin_Results.xlsx",
    "07_Pov_LRTP_PBM_ToxPi/Pov_LRTP_PBM_ToxPi_Results.xlsx",
}
self.assertTrue(expected.issubset(set(archive.namelist())))
```

Read both EPA workbooks and assert A-only/B-only/shared membership. Add a partial checkpoint round-trip asserting module metadata and PNG/PDF bytes survive.

- [ ] **Step 2: Run export/checkpoint tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_auto_query_checkpoint -v
```

Expected: current exports contain one consolidated workbook and schema 2 has no per-file module metadata.

- [ ] **Step 3: Extend module workbook metadata**

```python
@dataclass(frozen=True)
class AutoWorkflowModuleWorkbook:
    step: str
    slug: str
    file_name: str
    data: bytes
    module_slug: str = ""
    primary_file: str = ""
    safe_export_name: str = ""
```

For affected steps, `build_auto_workflow_module_workbooks()` returns one workbook per `FileModuleView`. Shared identifier/EPI/ToxPi steps return one workbook.

- [ ] **Step 4: Persist schema-3 module metadata**

Set:

```python
SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = {1, 2, 3}
```

Save:

```python
module_files[slug] = {
    "step": module.step,
    "file_name": safe_file_name,
    "path": relative.as_posix(),
    "module_slug": module.module_slug or module.slug,
    "primary_file": module.primary_file,
    "safe_export_name": module.safe_export_name,
}
```

Load optional fields with empty/default values for schemas 1 and 2.

- [ ] **Step 5: Build per-file full and partial ZIP paths**

Use:

```python
EXPORT_FOLDER_BY_SLUG = {
    "local_screening": "01_Local_Screening",
    "identifier_completion": "02_Identifier_Completion",
    "epi_suite": "03_EPI_Suite",
    "comptox_use": "04_EPA_CompTox",
    "echa_reach_use": "05_ECHA",
    "echa_ghs_cl": "05_ECHA",
    "source_origin": "06_Source_Origin",
    "pov_lrtp_pbm_toxpi": "07_Pov_LRTP_PBM_ToxPi",
}


def module_archive_root(module):
    folder = EXPORT_FOLDER_BY_SLUG[module.module_slug or module.slug]
    if module.safe_export_name:
        return f"{folder}/{module.safe_export_name}"
    return folder
```

Write workbook and scoped charts below this root. The immediate download for an affected multi-file module is a ZIP containing all its file roots. Shared chartless modules remain XLSX.

- [ ] **Step 6: Run payload and compatibility tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_auto_query_checkpoint tests.test_upload_state -v
```

Expected: schemas 1/2/3 load, full/partial/recovered downloads have identical per-file layout, and ToxPi exists only once.

- [ ] **Step 7: Commit**

```powershell
git add src/auto_query_workflow.py src/auto_query_checkpoint.py tests/test_auto_query_workflow.py tests/test_auto_query_checkpoint.py
git commit -m "feat: export per-file module workbooks and charts"
```

---

### Task 8: Render nested file tabs while keeping shared and combined tabs intact

**Files:**
- Modify: `pages/6_一键批量查询.py`
- Modify: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Changes: local/EPA/ECHA/source top-level tabs contain nested primary-file tabs.
- Preserves: identifier and EPI shared tabs.
- Preserves: one combined Pov-LRTP/PBM/ToxPi tab with an explanatory caption.

- [ ] **Step 1: Write failing AppTest/page-contract tests**

Add a recovered two-file result and assert:

```python
labels = [tab.label for tab in app.get("tab")]
self.assertIn("A.xlsx", labels)
self.assertIn("B.xlsx", labels)
self.assertEqual(labels.count("Pov-LRTP / PBM / ToxPi"), 1)
self.assertIn(
    "汇总所有参与文件",
    " ".join(item.value for item in app.caption),
)
```

Add an empty ECHA view for B and assert its tab remains and shows `该文件没有可呈现的 ECHA 结果`.

- [ ] **Step 2: Run AppTest and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow -v
```

Expected: only one consolidated result table/figure set is rendered per affected top-level module.

- [ ] **Step 3: Render affected groups through file views**

In `_render_result_dashboard()`:

```python
MODULE_SLUG_BY_GROUP = {
    "screening": "local_screening",
    "comptox": "comptox_use",
    "echa": "echa",
    "source": "source_origin",
}


def _render_view_tables(tables):
    for table_name, table in tables.items():
        if _is_audit_table(table_name):
            with st.expander(table_name, expanded=False):
                _show_dataframe(table)
        else:
            st.caption(table_name)
            _show_dataframe(table)


per_file_groups = {"screening", "comptox", "echa", "source"}
if group["key"] in per_file_groups:
    file_views = build_file_module_views(result)[MODULE_SLUG_BY_GROUP[group["key"]]]
    file_tabs = st.tabs(list(file_views))
    for file_tab, (filename, view) in zip(file_tabs, file_views.items()):
        with file_tab:
            if not view.tables and not view.charts:
                st.info(f"该文件没有可呈现的 {group['label']} 结果")
                continue
            _render_view_tables(view.tables)
            for chart in view.charts.values():
                _render_chart_image(chart)
    continue
```

For `toxpi`, add:

```python
st.caption("本栏汇总所有参与文件，并按现有 PA/PBM/DF ToxPi 规则统一计算。")
```

Remove the redundant global table selectbox for per-file public module tables or limit it to shared/audit tables so it does not reintroduce a consolidated-only public view.

- [ ] **Step 4: Update module download rendering for multiple per-file workbooks**

Group `module_workbooks.values()` by `module.step`, build one download per completed step, and keep file-scoped workbooks/figures inside the returned ZIP.

- [ ] **Step 5: Run AppTest and payload tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auto_query_workflow tests.test_auto_query_file_views -v
```

Expected: nested tabs, empty-file notices, combined ToxPi caption, and per-file downloads all pass.

- [ ] **Step 6: Commit**

```powershell
git add pages/6_一键批量查询.py tests/test_auto_query_workflow.py
git commit -m "feat: present one-click results by uploaded file"
```

---

### Task 9: Fix the ToxPi ranking and robustness top ticks

**Files:**
- Modify: `src/cp_screening_workflow.py`
- Modify: `tests/test_cp_screening_workflow.py`

**Interfaces:**
- Changes: ranking Y limits/ticks are exactly `0.0..1.0` and `0.0/0.5/1.0`.
- Changes: robustness Y upper limit equals its final integer top tick.

- [ ] **Step 1: Write failing exact-axis tests**

```python
def test_toxpi_bar_plot_shows_full_normalized_axis(self):
    data = pd.DataFrame(
        {"compound": ["A", "B"], "toxpi": [0.78, 0.61]}
    )
    fig = generate_pbm_toxpi_bar_plot(data, top_n=2)
    try:
        ax = fig.axes[0]
        self.assertEqual(tuple(ax.get_ylim()), (0.0, 1.0))
        self.assertEqual(tuple(ax.get_yticks()), (0.0, 0.5, 1.0))
        fig.canvas.draw()
        self.assertEqual(
            [label.get_text() for label in ax.get_yticklabels()],
            ["0.0", "0.5", "1.0"],
        )
    finally:
        plt.close(fig)


def test_robustness_histogram_includes_aligned_top_frequency_tick(self):
    data = pd.DataFrame(
        {
            "compound": ["A", "B", "C"],
            "Peak_Area": [1e6, 1e4, 1e2],
            "Scores": [1, 5, 3],
            "DF": [0.9, 0.2, 0.6],
        }
    )
    config = PBMToxPiConfig(
        candidate_top_n=3,
        display_top_n=2,
        n_iter=30,
        seed=7,
    )
    result = run_pbm_toxpi_robustness(
        calculate_pbm_toxpi(data, config),
        config,
    )
    fig = generate_pbm_toxpi_robustness_plot(result)
    try:
        ax = fig.axes[0]
        y_min, y_max = ax.get_ylim()
        ticks = ax.get_yticks()
        self.assertEqual(y_min, 0.0)
        self.assertEqual(ticks[-1], y_max)
        self.assertTrue(all(float(tick).is_integer() for tick in ticks))
        tallest = max(patch.get_height() for patch in ax.patches)
        self.assertLessEqual(tallest, y_max)
    finally:
        plt.close(fig)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow -v
```

Expected: the ranking lacks fixed ticks and the robustness top tick does not equal the visible upper bound.

- [ ] **Step 3: Implement fixed normalized ticks**

```python
from matplotlib.ticker import FormatStrFormatter, MaxNLocator

ax.set_ylim(0.0, 1.0)
ax.set_yticks([0.0, 0.5, 1.0])
ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))
```

- [ ] **Step 4: Align robustness upper limit to an integer locator tick**

After drawing the histogram:

```python
locator = MaxNLocator(nbins="auto", integer=True, min_n_ticks=2)
auto_upper = float(ax.get_ylim()[1])
ticks = locator.tick_values(0.0, auto_upper)
visible = ticks[ticks >= 0.0]
top = float(next(tick for tick in visible if tick >= auto_upper))
final_ticks = visible[visible <= top]
ax.set_ylim(0.0, top)
ax.set_yticks(final_ticks)
```

- [ ] **Step 5: Run plot tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cp_screening_workflow -v
```

Expected: both exact-axis tests and existing plot/robustness tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/cp_screening_workflow.py tests/test_cp_screening_workflow.py
git commit -m "fix: show complete ToxPi and robustness axis ticks"
```

---

### Task 10: Verify end-to-end partial recovery, per-file outputs, storage switching, and full compatibility

**Files:**
- Modify: `tests/test_auto_query_workflow.py`
- Modify: `tests/test_upload_state.py`

**Interfaces:**
- Verifies the completed user-visible contract; adds no production interface.

- [ ] **Step 1: Add one complete end-to-end AppTest**

The test must:

1. cache two primary workbooks with one shared compound;
2. select local, EPA, ECHA, source origin, and Pov-LRTP/PBM/ToxPi;
3. mock each external batch and record received `input_identity_key` values;
4. capture a checkpoint after EPA and assert per-file EPA charts are present;
5. capture a checkpoint after ToxPi and assert combined ToxPi plus ranked EPA/ECHA evidence charts are present;
6. recover the checkpoint from a temporary custom root;
7. inspect nested file tabs;
8. download the live module ZIP, partial ZIP, and final ZIP;
9. inspect both A/B workbooks and PNG/PDF members;
10. prove the shared identity was queried once and appears in both file exports;
11. prove only one combined ToxPi workbook exists;
12. switch storage roots, confirm the session detaches, and prove the old checkpoint still loads when addressed with its explicit old root.

- [ ] **Step 2: Run the targeted regression suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_storage_paths tests.test_storage_controls tests.test_query_cache tests.test_auto_query_checkpoint tests.test_multi_file_screening tests.test_auto_query_file_views tests.test_comptox_dashboard_mode tests.test_echa_use tests.test_echa_ghs tests.test_source_origin tests.test_cp_screening_workflow tests.test_upload_state tests.test_auto_query_workflow -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Run the full application verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall app.py pages src
git diff --check
```

Expected:

- all tests pass;
- compileall exits `0`;
- diff check produces no output.

- [ ] **Step 4: Inspect scope and preserved user files**

Run:

```powershell
git status --short
git diff --stat e4f27d5..HEAD
git diff --name-only e4f27d5..HEAD
```

Confirm:

- no pre-existing untracked document or `outputs/` file is staged;
- no scientific formula or ToxPi normalization code changed outside the two axis-format functions;
- no cache/checkpoint migration or deletion code was added;
- no external batch is invoked once per primary file;
- every design acceptance criterion has a test or direct payload inspection.

- [ ] **Step 5: Request code review**

Invoke `superpowers:requesting-code-review` with:

- requirements: `docs/superpowers/specs/2026-07-26-per-file-results-and-axis-ticks-design.md`;
- base SHA: `2bbb392`;
- head SHA: current `HEAD`;
- scope: per-file one-click results/exports, configurable storage, staged checkpoint charts, and axis ticks.

Fix all Critical and Important findings, then rerun Step 2 and Step 3.

- [ ] **Step 6: Commit final regression adjustments**

```powershell
git add tests/test_auto_query_workflow.py tests/test_upload_state.py
git commit -m "test: verify per-file recovery and configurable storage"
```
