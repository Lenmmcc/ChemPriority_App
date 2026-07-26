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
    local_app_data = str(environ.get("LOCALAPPDATA", "")).strip()
    if local_app_data:
        return Path(local_app_data) / "ChemPriority" / "storage.json"
    config_root = str(environ.get("XDG_CONFIG_HOME", "")).strip()
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
        if root.exists() and not root.is_dir():
            raise ValueError("saved root is not a directory")
        return root.resolve(), ""
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return None, f"缓存位置设置无法读取：{exc}"


def _configured_path(value, cwd: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return (path if path.is_absolute() else cwd / path).resolve()


def resolve_storage_paths(environ=None, preference_file=None, cwd=None) -> StoragePaths:
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else Path(cwd)
    cwd = cwd.resolve()
    preference = Path(
        preference_file or default_storage_preference_file(environ=environ)
    )
    saved_root, warning = _read_saved_root(preference)

    environment_root = _configured_path(
        environ.get("CHEMPRIORITY_STORAGE_ROOT"),
        cwd,
    )
    unified_root = environment_root or saved_root
    root_source = "environment" if environment_root is not None else "saved setting"

    environment_query_path = _configured_path(
        environ.get("CHEMPRIORITY_QUERY_CACHE_PATH"),
        cwd,
    )
    environment_checkpoint_root = _configured_path(
        environ.get("CHEMPRIORITY_CHECKPOINT_ROOT"),
        cwd,
    )

    if environment_query_path is not None:
        query_cache_path = environment_query_path
        query_path_source = "environment"
    elif unified_root is not None:
        query_cache_path = (
            unified_root / "query_cache" / "chempriority_queries.sqlite3"
        )
        query_path_source = root_source
    else:
        query_cache_path = (cwd / LEGACY_QUERY_CACHE_PATH).resolve()
        query_path_source = "default"

    if environment_checkpoint_root is not None:
        checkpoint_root = environment_checkpoint_root
        checkpoint_path_source = "environment"
    elif unified_root is not None:
        checkpoint_root = unified_root / "checkpoints" / "auto_query_runs"
        checkpoint_path_source = root_source
    else:
        checkpoint_root = (cwd / LEGACY_CHECKPOINT_ROOT).resolve()
        checkpoint_path_source = "default"

    return StoragePaths(
        storage_root=unified_root,
        query_cache_path=query_cache_path,
        checkpoint_root=checkpoint_root,
        query_path_source=query_path_source,
        checkpoint_path_source=checkpoint_path_source,
        preference_file=preference,
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
        raise ValueError("cache root must be an absolute path")
    if root.exists() and not root.is_dir():
        raise ValueError("cache root must be a directory")
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
