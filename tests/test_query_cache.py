import tempfile
import time
import unittest
from pathlib import Path

from src.query_cache import (
    QueryCache,
    build_cache_key,
    cached_call,
    clear_query_cache,
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


if __name__ == "__main__":
    unittest.main()
