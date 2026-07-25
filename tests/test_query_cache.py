import contextlib
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import episuite_io
from src.episuite_result_pool import (
    build_uploaded_epi_pool_payload,
    read_epi_pool,
    upsert_epi_pool,
)
from src.query_cache import (
    QueryCache,
    build_cache_key,
    cached_call,
    clear_query_cache,
    prune_expired_cache,
    query_cache_stats,
    render_query_cache_controls,
    use_cache_path,
)


class QueryCacheTests(unittest.TestCase):
    def test_cached_call_reuses_successful_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "queries.sqlite3"
            calls = []

            def fetcher():
                calls.append("network")
                return {"ok": True, "items": [1]}

            with use_cache_path(cache_path):
                first = cached_call(
                    "pubchem",
                    "v1",
                    {"base_url": "https://example.test/", "path": "compound/1"},
                    fetcher,
                )
                second = cached_call(
                    "pubchem",
                    "v1",
                    {"base_url": "https://example.test/", "path": "compound/1"},
                    fetcher,
                )

            self.assertEqual(first, {"ok": True, "items": [1]})
            self.assertEqual(second, first)
            self.assertEqual(calls, ["network"])

    def test_cache_key_omits_sensitive_values(self):
        key_a = build_cache_key(
            "comptox",
            "v1",
            {
                "base_url": "https://example.test/",
                "path": "chemical",
                "params": {"q": "ethanol", "api_key": "secret-a"},
            },
        )
        key_b = build_cache_key(
            "comptox",
            "v1",
            {
                "base_url": "https://example.test/",
                "path": "chemical",
                "params": {"q": "ethanol", "api_key": "secret-b"},
            },
        )

        self.assertEqual(key_a, key_b)

    def test_expired_cache_entry_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "queries.sqlite3"
            cache = QueryCache(cache_path)
            cache.set("source", "key", {"old": True}, created_at=time.time() - 120)
            calls = []

            def fetcher():
                calls.append("network")
                return {"new": True}

            with use_cache_path(cache_path):
                value = cached_call(
                    "source",
                    "v1",
                    {"path": "x"},
                    fetcher,
                    ttl_seconds=30,
                )

            self.assertEqual(value, {"new": True})
            self.assertEqual(calls, ["network"])

    def test_corrupt_cached_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "queries.sqlite3"
            cache = QueryCache(cache_path)
            cache.set_raw("source", "key", "{not json")
            calls = []

            def fetcher():
                calls.append("network")
                return {"fresh": True}

            with use_cache_path(cache_path):
                value = cached_call(
                    "source",
                    "v1",
                    {"path": "x"},
                    fetcher,
                    cache_key="key",
                )

            self.assertEqual(value, {"fresh": True})
            self.assertEqual(calls, ["network"])

    def test_empty_values_are_not_cached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "queries.sqlite3"
            calls = []

            def fetcher():
                calls.append("network")
                return []

            with use_cache_path(cache_path):
                cached_call("source", "v1", {"path": "empty"}, fetcher)
                cached_call("source", "v1", {"path": "empty"}, fetcher)

            self.assertEqual(calls, ["network", "network"])

    def test_clear_query_cache_removes_cache_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "queries.sqlite3"
            cache = QueryCache(cache_path)
            cache.set("source", "key", {"ok": True})
            self.assertTrue(cache_path.exists())

            clear_query_cache(cache_path)

            self.assertFalse(cache_path.exists())

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
            self.assertIsNotNone(before.oldest_created_at)
            self.assertIsNotNone(before.newest_created_at)

            after = prune_expired_cache(path, ttl_seconds=30, compact=False)

            self.assertEqual(after.total_rows, 1)
            self.assertEqual(cache.get("epi_web_submit", "live"), {"ok": True})
            self.assertIsNone(cache.get("epi_web_submit", "expired"))

    def test_stats_for_missing_cache_are_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing.sqlite3"

            stats = query_cache_stats(path)

        self.assertEqual(stats.path, path)
        self.assertEqual(stats.total_rows, 0)
        self.assertEqual(stats.size_bytes, 0)
        self.assertEqual(stats.epi_rows, 0)
        self.assertEqual(stats.expired_rows, 0)
        self.assertIsNone(stats.oldest_created_at)
        self.assertIsNone(stats.newest_created_at)
        self.assertTrue(stats.readable)
        self.assertIsNone(stats.error_message)

    def test_stats_and_prune_tolerate_missing_schema_and_corrupt_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            no_schema_path = Path(tmpdir) / "no_schema.sqlite3"
            with contextlib.closing(sqlite3.connect(no_schema_path)) as conn:
                with conn:
                    conn.execute("CREATE TABLE unrelated (value TEXT)")

            no_schema_stats = query_cache_stats(no_schema_path)
            no_schema_after = prune_expired_cache(
                no_schema_path,
                compact=False,
            )

            self.assertEqual(no_schema_stats.total_rows, 0)
            self.assertEqual(no_schema_after.total_rows, 0)
            self.assertFalse(no_schema_stats.readable)
            self.assertFalse(no_schema_after.readable)
            self.assertTrue(no_schema_stats.error_message)
            self.assertTrue(no_schema_after.error_message)

            corrupt_path = Path(tmpdir) / "corrupt.sqlite3"
            corrupt_path.write_bytes(b"not a sqlite database")

            corrupt_stats = query_cache_stats(corrupt_path)
            corrupt_after = prune_expired_cache(corrupt_path, compact=False)

            self.assertEqual(corrupt_stats.total_rows, 0)
            self.assertEqual(corrupt_stats.size_bytes, len(b"not a sqlite database"))
            self.assertEqual(corrupt_after.total_rows, 0)
            self.assertFalse(corrupt_stats.readable)
            self.assertFalse(corrupt_after.readable)
            self.assertTrue(corrupt_stats.error_message)
            self.assertTrue(corrupt_after.error_message)

    def test_stats_and_prune_tolerate_locked_database_without_deleting_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "queries.sqlite3"
            cache = QueryCache(path)
            cache.set(
                "epi_web_submit",
                "expired",
                {"old": True},
                created_at=time.time() - 120,
            )
            lock = sqlite3.connect(path, timeout=0.1)
            try:
                lock.execute("BEGIN EXCLUSIVE")

                locked_stats = query_cache_stats(path, ttl_seconds=30)
                locked_after = prune_expired_cache(
                    path,
                    ttl_seconds=30,
                    compact=False,
                )
            finally:
                lock.rollback()
                lock.close()

            self.assertEqual(locked_stats.total_rows, 0)
            self.assertEqual(locked_after.total_rows, 0)
            self.assertFalse(locked_stats.readable)
            self.assertFalse(locked_after.readable)
            self.assertTrue(locked_stats.error_message)
            self.assertTrue(locked_after.error_message)
            self.assertEqual(
                cache.get("epi_web_submit", "expired", ttl_seconds=None),
                {"old": True},
            )

    def test_sqlite_cache_is_shared_across_instances_and_separates_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "queries.sqlite3"
            page_3_cache = QueryCache(path)
            page_3_cache.set("epi_web_submit", "same-key", {"page": 3})
            page_3_cache.set("other_source", "same-key", {"source": "other"})

            page_6_cache = QueryCache(path)

            self.assertEqual(
                page_6_cache.get("epi_web_submit", "same-key"),
                {"page": 3},
            )
            self.assertEqual(
                page_6_cache.get("other_source", "same-key"),
                {"source": "other"},
            )

    @patch("src.episuite_io._call_epi_web_api_uncached")
    def test_page_3_and_page_6_epi_paths_reuse_epi_web_submit_cache(
        self,
        uncached_submit,
    ):
        uncached_submit.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "queries.sqlite3"
            with patch("src.query_cache.DEFAULT_CACHE_PATH", path):
                page_3_result = episuite_io.call_epi_web_api(
                    "CCO",
                    cas="64-17-5",
                    api_url="https://example.test/api/submit",
                )
                page_6_result = episuite_io.call_epi_web_api(
                    "CCO",
                    cas="64-17-5",
                    api_url="https://example.test/api/submit",
                )
                stats = query_cache_stats(path)

        self.assertEqual(page_3_result, {"ok": True})
        self.assertEqual(page_6_result, page_3_result)
        uncached_submit.assert_called_once()
        self.assertEqual(stats.total_rows, 1)
        self.assertEqual(stats.epi_rows, 1)

    def test_uploaded_epi_session_pool_does_not_write_sqlite_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "queries.sqlite3"
            state = {}
            uploaded = pd.DataFrame(
                {
                    "compound": ["A"],
                    "log_kow": [1.5],
                    "source_file": ["uploaded.xlsx"],
                    "source_row": [2],
                }
            )
            with use_cache_path(path):
                results, provenance = build_uploaded_epi_pool_payload(uploaded)
                upsert_epi_pool(
                    state,
                    "epi-page:uploaded",
                    results,
                    provenance,
                )

            self.assertFalse(path.exists())

    def test_full_cache_clear_preserves_checkpoint_and_session_epi_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache_path = root / "queries.sqlite3"
            checkpoint_path = root / "checkpoint.json"
            checkpoint_path.write_text('{"status": "running"}', encoding="utf-8")
            QueryCache(cache_path).set("epi_web_submit", "one", {"ok": True})
            state = {}
            upsert_epi_pool(
                state,
                "epi-page:uploaded",
                pd.DataFrame({"compound": ["A"], "log_kow": [1.5]}),
                pd.DataFrame({"compound": ["A"], "source_file": ["upload.xlsx"]}),
            )
            before_results, before_provenance = read_epi_pool(state)

            clear_query_cache(cache_path)

            after_results, after_provenance = read_epi_pool(state)
            self.assertFalse(cache_path.exists())
            self.assertTrue(checkpoint_path.exists())
            pd.testing.assert_frame_equal(after_results, before_results)
            pd.testing.assert_frame_equal(after_provenance, before_provenance)

    def test_pages_use_shared_cache_diagnostic_renderer_and_guard_full_clear(self):
        project_root = Path(__file__).resolve().parents[1]
        for prefix in ("3", "6"):
            page = next((project_root / "pages").glob(f"{prefix}_*.py"))
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertIn("render_query_cache_controls(", source)
                self.assertNotIn('"清理本地查询缓存"', source)

    def test_cache_renderer_requires_confirmation_before_full_clear(self):
        class FakeMetric:
            def metric(self, label, value):
                return None

        class FakeStreamlit:
            def __init__(self):
                self.buttons = []
                self.checkboxes = []
                self.warnings = []

            def caption(self, value):
                return None

            def columns(self, count):
                return [FakeMetric() for _ in range(count)]

            def checkbox(self, label, **kwargs):
                self.checkboxes.append((label, kwargs))
                return False

            def button(self, label, **kwargs):
                self.buttons.append((label, kwargs))
                return False

            def success(self, value):
                return None

            def warning(self, value):
                self.warnings.append(value)

        fake_st = FakeStreamlit()
        with tempfile.TemporaryDirectory() as tmpdir:
            with use_cache_path(Path(tmpdir) / "missing.sqlite3"):
                render_query_cache_controls(fake_st, "test")

        self.assertEqual(
            [label for label, _ in fake_st.checkboxes],
            ["我确认清空全部查询缓存"],
        )
        full_clear = next(
            kwargs
            for label, kwargs in fake_st.buttons
            if label == "清空全部查询缓存"
        )
        self.assertTrue(full_clear["disabled"])
        self.assertEqual(fake_st.warnings, [])

    def test_cache_renderer_warns_when_database_is_unreadable(self):
        class FakeMetric:
            def metric(self, label, value):
                return None

        class FakeStreamlit:
            def __init__(self):
                self.warnings = []

            def caption(self, value):
                return None

            def columns(self, count):
                return [FakeMetric() for _ in range(count)]

            def checkbox(self, label, **kwargs):
                return False

            def button(self, label, **kwargs):
                return False

            def success(self, value):
                raise AssertionError("Unreadable cache must not report success")

            def warning(self, value):
                self.warnings.append(value)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "corrupt.sqlite3"
            path.write_bytes(b"not a sqlite database")
            fake_st = FakeStreamlit()

            with use_cache_path(path):
                render_query_cache_controls(fake_st, "test")

        self.assertEqual(len(fake_st.warnings), 1)
        self.assertIn("无法读取", fake_st.warnings[0])


if __name__ == "__main__":
    unittest.main()
