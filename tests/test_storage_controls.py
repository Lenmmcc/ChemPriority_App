from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.storage_controls import render_storage_location_controls
from src.storage_paths import StoragePaths


class FakeStreamlit:
    def __init__(self, root, clicked="保存并切换"):
        self.root = str(root)
        self.clicked = clicked
        self.success_messages = []
        self.error_messages = []
        self.info_messages = []
        self.rerun_count = 0

    def caption(self, value):
        return None

    def warning(self, value):
        return None

    def info(self, value):
        self.info_messages.append(value)

    def text_input(self, label, **kwargs):
        return self.root

    def button(self, label, **kwargs):
        return label == self.clicked

    def success(self, value):
        self.success_messages.append(value)

    def error(self, value):
        self.error_messages.append(value)

    def rerun(self):
        self.rerun_count += 1


def example_paths(root, *, source="saved setting"):
    root = Path(root)
    return StoragePaths(
        storage_root=root,
        query_cache_path=root / "query_cache" / "chempriority_queries.sqlite3",
        checkpoint_root=root / "checkpoints" / "auto_query_runs",
        query_path_source=source,
        checkpoint_path_source=source,
        preference_file=root / "storage.json",
    )


class StorageControlTests(unittest.TestCase):
    def test_save_storage_control_validates_and_calls_change_callback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakeStreamlit(root)
            resolved = example_paths(root)
            changed = []
            with (
                patch(
                    "src.storage_controls.resolve_storage_paths",
                    return_value=resolved,
                ),
                patch(
                    "src.storage_controls.save_storage_root",
                    return_value=resolved,
                ) as save,
            ):
                render_storage_location_controls(
                    fake,
                    "test",
                    on_change=lambda: changed.append(True),
                )
            save.assert_called_once_with(str(root))
            self.assertEqual(changed, [True])
            self.assertIn("已保存并切换", fake.success_messages[0])
            self.assertEqual(fake.rerun_count, 1)

    def test_environment_owned_paths_disable_page_override(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakeStreamlit(root)
            resolved = example_paths(root, source="environment")
            with (
                patch(
                    "src.storage_controls.resolve_storage_paths",
                    return_value=resolved,
                ),
                patch("src.storage_controls.save_storage_root") as save,
            ):
                returned = render_storage_location_controls(fake, "test")
            self.assertEqual(returned, resolved)
            save.assert_not_called()
            self.assertTrue(fake.info_messages)

    def test_validation_error_does_not_call_change_callback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakeStreamlit(root)
            changed = []
            with (
                patch(
                    "src.storage_controls.resolve_storage_paths",
                    return_value=example_paths(root),
                ),
                patch(
                    "src.storage_controls.save_storage_root",
                    side_effect=ValueError("cache root must be an absolute path"),
                ),
            ):
                render_storage_location_controls(
                    fake,
                    "test",
                    on_change=lambda: changed.append(True),
                )
            self.assertEqual(changed, [])
            self.assertIn("未更改", fake.error_messages[0])
            self.assertEqual(fake.rerun_count, 0)


if __name__ == "__main__":
    unittest.main()
