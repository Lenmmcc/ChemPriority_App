import contextlib
import contextvars
import hashlib
import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.storage_controls import render_storage_location_controls
from src.storage_paths import LEGACY_QUERY_CACHE_PATH, resolve_storage_paths


DEFAULT_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_CACHE_PATH = LEGACY_QUERY_CACHE_PATH
SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "x_api_key",
    "key",
    "token",
    "access_token",
    "secret",
    "password",
    "chemspider_key",
}

_cache_path_var = contextvars.ContextVar("chempriority_query_cache_path", default=None)
_cache_enabled_var = contextvars.ContextVar("chempriority_query_cache_enabled", default=True)


@dataclass(frozen=True)
class QueryCacheStats:
    path: Path
    size_bytes: int
    total_rows: int
    epi_rows: int
    expired_rows: int
    oldest_created_at: float | None
    newest_created_at: float | None
    readable: bool = True
    error_message: str | None = None


def current_cache_path():
    override = _cache_path_var.get()
    if override is not None:
        return Path(override)
    return Path(resolve_storage_paths().query_cache_path)


@contextlib.contextmanager
def use_cache_path(path):
    token = _cache_path_var.set(Path(path))
    try:
        yield
    finally:
        _cache_path_var.reset(token)


@contextlib.contextmanager
def cache_control(enabled=True):
    token = _cache_enabled_var.set(bool(enabled))
    try:
        yield
    finally:
        _cache_enabled_var.reset(token)


def is_cache_enabled():
    return bool(_cache_enabled_var.get())


def build_cache_key(source, version, parts):
    payload = {
        "source": str(source),
        "version": str(version),
        "parts": _sanitize_for_key(parts),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cached_call(
    source,
    version,
    key_parts,
    fetcher,
    ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
    cache_key=None,
    cache_enabled=None,
    cache_empty=False,
):
    if cache_enabled is None:
        cache_enabled = is_cache_enabled()
    if not cache_enabled:
        return fetcher()

    key = cache_key or build_cache_key(source, version, key_parts)
    cache = QueryCache(current_cache_path())
    cached = cache.get(source, key, ttl_seconds=ttl_seconds)
    if cached is not None:
        return cached

    value = fetcher()
    if cache_empty or _is_cacheable_value(value):
        cache.set(source, key, value)
    return value


def clear_query_cache(path=None):
    cache_path = Path(path) if path is not None else current_cache_path()
    for candidate in (cache_path, Path(str(cache_path) + "-wal"), Path(str(cache_path) + "-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def query_cache_stats(path=None, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS):
    cache_path = Path(path) if path is not None else current_cache_path()
    is_cache_file, size_bytes, inspection_error = _inspect_cache_file(cache_path)
    if not is_cache_file:
        return _empty_cache_stats(
            cache_path,
            size_bytes=size_bytes,
            readable=inspection_error is None,
            error_message=inspection_error,
        )

    cutoff = time.time() - float(ttl_seconds)
    try:
        with contextlib.closing(sqlite3.connect(cache_path, timeout=0.1)) as conn:
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
    except (OSError, sqlite3.Error) as exc:
        return _empty_cache_stats(
            cache_path,
            size_bytes=size_bytes,
            readable=False,
            error_message=str(exc),
        )

    return QueryCacheStats(
        path=cache_path,
        size_bytes=size_bytes,
        total_rows=int(row[0] or 0),
        epi_rows=int(row[1] or 0),
        expired_rows=int(row[2] or 0),
        oldest_created_at=float(row[3]) if row[3] is not None else None,
        newest_created_at=float(row[4]) if row[4] is not None else None,
    )


def prune_expired_cache(
    path=None,
    ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
    compact=True,
):
    cache_path = Path(path) if path is not None else current_cache_path()
    is_cache_file, size_bytes, inspection_error = _inspect_cache_file(cache_path)
    if not is_cache_file:
        return _empty_cache_stats(
            cache_path,
            size_bytes=size_bytes,
            readable=inspection_error is None,
            error_message=inspection_error,
        )

    cutoff = time.time() - float(ttl_seconds)
    try:
        with contextlib.closing(sqlite3.connect(cache_path, timeout=0.1)) as conn:
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
    except (OSError, sqlite3.Error) as exc:
        stats = query_cache_stats(cache_path, ttl_seconds=ttl_seconds)
        return QueryCacheStats(
            path=stats.path,
            size_bytes=stats.size_bytes,
            total_rows=stats.total_rows,
            epi_rows=stats.epi_rows,
            expired_rows=stats.expired_rows,
            oldest_created_at=stats.oldest_created_at,
            newest_created_at=stats.newest_created_at,
            readable=False,
            error_message=str(exc),
        )
    return query_cache_stats(cache_path, ttl_seconds=ttl_seconds)


def render_query_cache_controls(
    st_module,
    prefix,
    ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
    show_storage=True,
    on_storage_changed=None,
):
    if show_storage:
        render_storage_location_controls(
            st_module,
            prefix,
            on_change=on_storage_changed,
        )
    cache_path = current_cache_path()
    st_module.caption(f"缓存文件：{cache_path}")
    warning_shown = False

    if st_module.button(
        "清理过期记录",
        key=f"{prefix}_prune_expired_query_cache",
    ):
        before = query_cache_stats(cache_path, ttl_seconds=ttl_seconds)
        after = prune_expired_cache(cache_path, ttl_seconds=ttl_seconds)
        if after.readable:
            removed = max(0, before.total_rows - after.total_rows)
            st_module.success(f"已清理 {removed} 条过期查询缓存记录。")
        else:
            st_module.warning(f"过期缓存清理未完成：{after.error_message}")
            warning_shown = True

    confirm_clear = st_module.checkbox(
        "我确认清空全部查询缓存",
        key=f"{prefix}_confirm_clear_query_cache",
    )
    if st_module.button(
        "清空全部查询缓存",
        disabled=not confirm_clear,
        key=f"{prefix}_clear_all_query_cache",
    ):
        try:
            clear_query_cache(cache_path)
        except OSError as exc:
            st_module.warning(f"全部查询缓存清理未完成：{exc}")
            warning_shown = True
        else:
            st_module.success("全部查询缓存已清空。")

    stats = query_cache_stats(cache_path, ttl_seconds=ttl_seconds)
    if not stats.readable and not warning_shown:
        st_module.warning(f"缓存数据库无法读取：{stats.error_message}")

    total_rows = stats.total_rows if stats.readable else "不可读"
    epi_rows = stats.epi_rows if stats.readable else "不可读"
    expired_rows = stats.expired_rows if stats.readable else "不可读"
    newest_created_at = (
        _format_cache_timestamp(stats.newest_created_at)
        if stats.readable
        else "不可读"
    )
    labels_and_values = (
        ("文件大小", _format_cache_size(stats.size_bytes)),
        ("总记录", total_rows),
        ("EPI 记录", epi_rows),
        ("过期记录", expired_rows),
        ("最新写入", newest_created_at),
    )
    for column, (label, value) in zip(
        st_module.columns(len(labels_and_values)),
        labels_and_values,
    ):
        column.metric(label, value)
    return stats


class QueryCache:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else current_cache_path()

    def get(self, source, key, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS):
        self._ensure_schema()
        with contextlib.closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "SELECT value_json, created_at FROM query_cache WHERE source = ? AND cache_key = ?",
                    (str(source), str(key)),
                ).fetchone()
        if row is None:
            return None

        value_json, created_at = row
        if ttl_seconds is not None and time.time() - float(created_at) > float(ttl_seconds):
            return None
        try:
            return json.loads(value_json)
        except (TypeError, json.JSONDecodeError):
            return None

    def set(self, source, key, value, created_at=None):
        value_json = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        self.set_raw(source, key, value_json, created_at=created_at)

    def set_raw(self, source, key, value_json, created_at=None):
        self._ensure_schema()
        created = float(time.time() if created_at is None else created_at)
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO query_cache(source, cache_key, value_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source, cache_key)
                    DO UPDATE SET value_json = excluded.value_json, created_at = excluded.created_at
                    """,
                    (str(source), str(key), str(value_json), created),
                )

    def _ensure_schema(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS query_cache (
                        source TEXT NOT NULL,
                        cache_key TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (source, cache_key)
                    )
                    """
                )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)


def _empty_cache_stats(
    path,
    size_bytes=0,
    readable=True,
    error_message=None,
):
    return QueryCacheStats(
        path=Path(path),
        size_bytes=int(size_bytes),
        total_rows=0,
        epi_rows=0,
        expired_rows=0,
        oldest_created_at=None,
        newest_created_at=None,
        readable=bool(readable),
        error_message=error_message,
    )


def _inspect_cache_file(path):
    cache_path = Path(path)
    try:
        path_stat = cache_path.stat()
    except FileNotFoundError as exc:
        if os.path.lexists(cache_path):
            return False, 0, str(exc)
        return False, 0, None
    except OSError as exc:
        return False, 0, str(exc)
    if not stat.S_ISREG(path_stat.st_mode):
        return False, 0, f"缓存路径不是普通文件：{cache_path}"
    return True, int(path_stat.st_size), None


def _format_cache_size(size_bytes):
    size = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _format_cache_timestamp(created_at):
    if created_at is None:
        return "无"
    try:
        return datetime.fromtimestamp(float(created_at)).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OSError, OverflowError, TypeError, ValueError):
        return "不可用"


def _sanitize_for_key(value):
    if isinstance(value, dict):
        return {
            str(key): _sanitize_for_key(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_key(item) for item in value]
    return value


def _is_sensitive_key(key):
    normalized = str(key).lower().replace("-", "_")
    if normalized in SENSITIVE_KEY_NAMES:
        return True
    return normalized.endswith(("_api_key", "_token", "_secret", "_password"))


def _is_cacheable_value(value):
    if value is None:
        return False
    if value == "":
        return False
    if isinstance(value, (list, tuple, dict, set)) and len(value) == 0:
        return False
    return True
