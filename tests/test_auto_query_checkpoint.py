from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

import src.auto_query_checkpoint as checkpoint_storage
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
                (
                    "Identifier_Completion",
                    pd.DataFrame(
                        {"compound": ["A", "B"], "score": [1.5, pd.NA]}
                    ),
                ),
            ]
        ),
        step_status=pd.DataFrame(
            {
                "step": ["标识符补全"],
                "status": ["完成"],
                "rows": [2],
                "message": [""],
            }
        ),
        warnings=pd.DataFrame(
            {"stage": ["Identifier"], "message": ["example"]}
        ),
        charts=OrderedDict(
            [
                (
                    "Local_DBE_Bubble_Plot",
                    AutoWorkflowChart("DBE", b"PNG", b"PDF"),
                )
            ]
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
            save_checkpoint(
                token,
                example_checkpoint(now),
                "input.xlsx",
                {module.slug: module},
                root=root,
                now=now,
            )

            loaded = load_checkpoint(
                token, root=root, now=now + timedelta(hours=1)
            )

            actual_table = loaded.checkpoint.result.tables[
                "Identifier_Completion"
            ]
            expected_table = example_checkpoint(now).result.tables[
                "Identifier_Completion"
            ]
            self.assertEqual(
                actual_table.columns.tolist(), expected_table.columns.tolist()
            )
            self.assertEqual(len(actual_table), len(expected_table))
            self.assertEqual(actual_table["compound"].tolist(), ["A", "B"])
            self.assertEqual(actual_table.loc[0, "score"], 1.5)
            self.assertEqual(
                actual_table["score"].isna().tolist(),
                expected_table["score"].isna().tolist(),
            )
            self.assertEqual(
                loaded.checkpoint.result.charts["Local_DBE_Bubble_Plot"].png,
                b"PNG",
            )
            self.assertEqual(
                loaded.module_workbooks["identifier_completion"].data, b"XLSX"
            )
            manifest = next(root.iterdir()) / "manifest.json"
            self.assertNotIn(token, manifest.read_text(encoding="utf-8"))

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
            run_dir = save_checkpoint(
                token, example_checkpoint(now), "input.xlsx", {}, root=root, now=now
            )
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
            save_checkpoint(
                first, example_checkpoint(now), "a.xlsx", {}, root=root, now=now
            )
            save_checkpoint(
                second, example_checkpoint(now), "b.xlsx", {}, root=root, now=now
            )

            self.assertEqual(
                cleanup_expired_checkpoints(
                    root=root, now=now + timedelta(hours=23, minutes=59)
                ),
                [],
            )
            with self.assertRaises(ExpiredCheckpoint):
                load_checkpoint(
                    first, root=root, now=now + timedelta(hours=24, seconds=1)
                )
            removed = cleanup_expired_checkpoints(
                root=root, now=now + timedelta(hours=24, seconds=1)
            )
            self.assertEqual(len(removed), 2)

            save_checkpoint(
                first, example_checkpoint(now), "a.xlsx", {}, root=root, now=now
            )
            save_checkpoint(
                second, example_checkpoint(now), "b.xlsx", {}, root=root, now=now
            )
            self.assertTrue(delete_checkpoint(first, root=root))
            self.assertIsNotNone(load_checkpoint(second, root=root, now=now))

    def test_manifest_paths_cannot_escape_the_validated_run_directory(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            run_dir = save_checkpoint(
                token, example_checkpoint(now), "input.xlsx", {}, root=root, now=now
            )
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["table_files"]["representative_table"] = "../outside.json.gz"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(CheckpointStorageError):
                load_checkpoint(token, root=root, now=now)

    def test_failed_manifest_commit_keeps_previous_module_revision_loadable(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            old_module = AutoWorkflowModuleWorkbook(
                step="标识符补全",
                slug="identifier_completion",
                file_name="Identifier_Completion_Results.xlsx",
                data=b"OLD-XLSX",
            )
            save_checkpoint(
                token,
                example_checkpoint(now),
                "input.xlsx",
                {old_module.slug: old_module},
                root=root,
                now=now,
            )
            new_module = AutoWorkflowModuleWorkbook(
                step=old_module.step,
                slug=old_module.slug,
                file_name=old_module.file_name,
                data=b"NEW-XLSX",
            )
            real_atomic_write = checkpoint_storage._atomic_write

            def fail_manifest_commit(path, payload):
                if path.name == "manifest.json":
                    raise OSError("simulated manifest commit failure")
                real_atomic_write(path, payload)

            with patch.object(
                checkpoint_storage,
                "_atomic_write",
                side_effect=fail_manifest_commit,
            ):
                with self.assertRaisesRegex(
                    OSError, "simulated manifest commit failure"
                ):
                    save_checkpoint(
                        token,
                        example_checkpoint(now + timedelta(hours=1)),
                        "input.xlsx",
                        {new_module.slug: new_module},
                        root=root,
                        now=now + timedelta(hours=1),
                    )

            loaded = load_checkpoint(token, root=root, now=now + timedelta(hours=1))
            self.assertEqual(
                loaded.module_workbooks["identifier_completion"].data,
                b"OLD-XLSX",
            )

    def test_cleanup_ignores_non_digest_directories_even_with_expired_manifests(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sentinel = root / "do-not-delete"
            sentinel.mkdir()
            (sentinel / "manifest.json").write_text(
                json.dumps(
                    {
                        "expires_at": (
                            datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat()
                        )
                    }
                ),
                encoding="utf-8",
            )

            removed = cleanup_expired_checkpoints(
                root=root,
                now=datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(removed, [])
            self.assertTrue(sentinel.is_dir())


if __name__ == "__main__":
    unittest.main()
