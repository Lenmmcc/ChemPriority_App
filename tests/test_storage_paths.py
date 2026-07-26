from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.storage_paths import (
    default_storage_preference_file,
    reset_storage_root,
    resolve_storage_paths,
    save_storage_root,
)


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

    def test_unified_environment_root_overrides_saved_root(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            preference = base / "storage.json"
            save_storage_root(base / "saved", preference_file=preference)
            resolved = resolve_storage_paths(
                environ={"CHEMPRIORITY_STORAGE_ROOT": str(base / "unified")},
                preference_file=preference,
                cwd=base,
            )
            self.assertEqual(resolved.storage_root, (base / "unified").resolve())
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

    def test_invalid_preference_falls_back_with_warning(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            preference = base / "storage.json"
            preference.write_text("{broken", encoding="utf-8")
            resolved = resolve_storage_paths(
                environ={},
                preference_file=preference,
                cwd=base,
            )
            self.assertIsNone(resolved.storage_root)
            self.assertTrue(resolved.warning)
            self.assertEqual(
                resolved.query_cache_path,
                (base / ".cache" / "chempriority_queries.sqlite3").resolve(),
            )

    def test_reset_removes_only_the_preference(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            preference = base / "storage.json"
            root = base / "selected-cache"
            save_storage_root(root, preference_file=preference)
            marker = root / "query_cache" / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            reset_storage_root(preference_file=preference)

            self.assertFalse(preference.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_default_preference_uses_local_app_data_on_windows(self):
        path = default_storage_preference_file(
            environ={"LOCALAPPDATA": r"C:\Users\A\AppData\Local"}
        )
        self.assertEqual(
            path,
            Path(r"C:\Users\A\AppData\Local") / "ChemPriority" / "storage.json",
        )


if __name__ == "__main__":
    unittest.main()
