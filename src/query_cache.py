import contextlib
import contextvars
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path


DEFAULT_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_CACHE_PATH = Path(
    os.environ.get("CHEMPRIORITY_QUERY_CACHE_PATH", ".cache/chempriority_queries.sqlite3")
)
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


def current_cache_path():
    return Path(_cache_path_var.get() or DEFAULT_CACHE_PATH)


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
