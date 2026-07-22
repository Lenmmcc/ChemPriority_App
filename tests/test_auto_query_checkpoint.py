from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
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


def make_directory_link(link, target):
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def set_tree_mtime(root, updated_at):
    timestamp = updated_at.timestamp()
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.utime(path, (timestamp, timestamp))
    os.utime(root, (timestamp, timestamp))


def ordered_checkpoint(updated_at):
    checkpoint = example_checkpoint(updated_at)
    checkpoint.result.tables = OrderedDict(
        [
            ("Z_Table", pd.DataFrame({"value": [1]})),
            ("A_Table", pd.DataFrame({"value": [2]})),
        ]
    )
    checkpoint.result.charts = OrderedDict(
        [
            ("Z_Chart", AutoWorkflowChart("Z", b"Z-PNG", b"Z-PDF")),
            ("A_Chart", AutoWorkflowChart("A", b"A-PNG", b"A-PDF")),
        ]
    )
    return checkpoint


def redirect_path_resolution(source, destination):
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == source:
            return destination
        return original_resolve(path, *args, **kwargs)

    return resolve


class AutoQueryCheckpointTests(unittest.TestCase):
    def test_checkpoint_root_can_be_overridden_before_process_import(self):
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            expected = Path(folder) / "ChemPriority" / "checkpoints"
            env = os.environ.copy()
            env["CHEMPRIORITY_CHECKPOINT_ROOT"] = str(expected)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    "from src.auto_query_checkpoint import DEFAULT_CHECKPOINT_ROOT; print(DEFAULT_CHECKPOINT_ROOT.resolve())",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
            ).strip()
        self.assertEqual(Path(output), expected.resolve())

    def test_repeated_saves_reuse_unchanged_content_addressed_artifacts(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            module = AutoWorkflowModuleWorkbook(
                step="标识符补全",
                slug="identifier_completion",
                file_name="Identifier_Completion_Results.xlsx",
                data=b"LARGE-UNCHANGED-XLSX",
            )
            checkpoint = example_checkpoint(now)
            large_table = pd.DataFrame(
                {
                    "compound": [f"Compound {index}" for index in range(10_000)],
                    "score": range(10_000),
                }
            )
            checkpoint.result.tables["Identifier_Completion"] = large_table

            for offset in range(4):
                run_dir = save_checkpoint(
                    token,
                    checkpoint,
                    "input.xlsx",
                    {module.slug: module},
                    root=root,
                    now=now + timedelta(minutes=offset),
                )

            unchanged_counts = {
                folder: len(list((run_dir / folder).iterdir()))
                for folder in ("tables", "charts", "modules")
            }
            self.assertEqual(
                unchanged_counts,
                {"tables": 4, "charts": 2, "modules": 1},
            )
            old_manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )

            changed = example_checkpoint(now + timedelta(minutes=5))
            changed.result.tables["Identifier_Completion"] = large_table.copy()
            changed.result.tables["Additional_Table"] = pd.DataFrame(
                {"compound": ["C"], "value": [3]}
            )
            changed.result.step_status = pd.concat(
                [
                    changed.result.step_status,
                    pd.DataFrame(
                        {
                            "step": ["EPA CompTox 用途"],
                            "status": ["失败"],
                            "rows": [0],
                            "message": ["service unavailable"],
                        }
                    ),
                ],
                ignore_index=True,
            )
            changed.result.warnings = pd.concat(
                [
                    changed.result.warnings,
                    pd.DataFrame(
                        {
                            "stage": ["EPA CompTox 用途"],
                            "message": ["service unavailable"],
                        }
                    ),
                ],
                ignore_index=True,
            )
            save_checkpoint(
                token,
                changed,
                "input.xlsx",
                {module.slug: module},
                root=root,
                now=now + timedelta(minutes=5),
            )

            changed_counts = {
                folder: len(list((run_dir / folder).iterdir()))
                for folder in ("tables", "charts", "modules")
            }
            self.assertEqual(changed_counts["tables"], unchanged_counts["tables"] + 3)
            self.assertEqual(changed_counts["charts"], unchanged_counts["charts"])
            self.assertEqual(changed_counts["modules"], unchanged_counts["modules"])
            for relative in old_manifest["table_files"].values():
                self.assertTrue((run_dir / relative).is_file())
            for entry in old_manifest["chart_files"].values():
                self.assertTrue((run_dir / entry["png"]).is_file())
                self.assertTrue((run_dir / entry["pdf"]).is_file())
            for entry in old_manifest["module_files"].values():
                self.assertTrue((run_dir / entry["path"]).is_file())

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

    def test_save_rejects_artifact_directory_junction_outside_run_directory(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "checkpoints"
            external = base / "external"
            root.mkdir()
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_text("UNCHANGED", encoding="utf-8")
            token = generate_run_token()
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            run_dir = root / digest
            run_dir.mkdir()
            make_directory_link(run_dir / "tables", external)
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)

            with self.assertRaisesRegex(CheckpointStorageError, "越界"):
                save_checkpoint(
                    token,
                    example_checkpoint(now),
                    "input.xlsx",
                    {},
                    root=root,
                    now=now,
                )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "UNCHANGED")
            self.assertEqual(
                {path.name for path in external.iterdir()}, {"sentinel.txt"}
            )

    def test_save_rejects_external_manifest_resolution_before_reading(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "checkpoints"
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            run_dir = save_checkpoint(
                token, example_checkpoint(now), "input.xlsx", {}, root=root, now=now
            )
            manifest_path = run_dir / "manifest.json"
            external_manifest = base / "external-manifest.json"
            external_manifest.write_text("SENTINEL", encoding="utf-8")
            read_paths = []
            original_read_text = Path.read_text

            def track_read(path, *args, **kwargs):
                read_paths.append(path)
                return original_read_text(path, *args, **kwargs)

            with patch.object(
                Path,
                "resolve",
                new=redirect_path_resolution(manifest_path, external_manifest),
            ), patch.object(Path, "read_text", new=track_read):
                with self.assertRaisesRegex(CheckpointStorageError, "越界"):
                    save_checkpoint(
                        token,
                        example_checkpoint(now + timedelta(hours=1)),
                        "input.xlsx",
                        {},
                        root=root,
                        now=now + timedelta(hours=1),
                    )

            self.assertNotIn(manifest_path, read_paths)
            self.assertEqual(
                external_manifest.read_text(encoding="utf-8"), "SENTINEL"
            )

    def test_load_rejects_external_manifest_resolution_before_reading(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "checkpoints"
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            run_dir = save_checkpoint(
                token, example_checkpoint(now), "input.xlsx", {}, root=root, now=now
            )
            manifest_path = run_dir / "manifest.json"
            external_manifest = base / "external-manifest.json"
            external_manifest.write_text("SENTINEL", encoding="utf-8")
            read_paths = []
            original_read_text = Path.read_text

            def track_read(path, *args, **kwargs):
                read_paths.append(path)
                return original_read_text(path, *args, **kwargs)

            with patch.object(
                Path,
                "resolve",
                new=redirect_path_resolution(manifest_path, external_manifest),
            ), patch.object(Path, "read_text", new=track_read):
                with self.assertRaisesRegex(CheckpointStorageError, "越界"):
                    load_checkpoint(token, root=root, now=now)

            self.assertNotIn(manifest_path, read_paths)
            self.assertEqual(
                external_manifest.read_text(encoding="utf-8"), "SENTINEL"
            )

    def test_cleanup_rejects_external_manifest_resolution_before_reading(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "checkpoints"
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            run_dir = save_checkpoint(
                token, example_checkpoint(now), "input.xlsx", {}, root=root, now=now
            )
            manifest_path = run_dir / "manifest.json"
            external_manifest = base / "external-manifest.json"
            external_manifest.write_text("SENTINEL", encoding="utf-8")
            read_paths = []
            original_read_text = Path.read_text

            def track_read(path, *args, **kwargs):
                read_paths.append(path)
                return original_read_text(path, *args, **kwargs)

            with patch.object(
                Path,
                "resolve",
                new=redirect_path_resolution(manifest_path, external_manifest),
            ), patch.object(Path, "read_text", new=track_read):
                self.assertEqual(
                    cleanup_expired_checkpoints(root=root, now=now), []
                )

            self.assertNotIn(manifest_path, read_paths)
            self.assertTrue(run_dir.is_dir())
            self.assertEqual(
                external_manifest.read_text(encoding="utf-8"), "SENTINEL"
            )

    def test_round_trip_preserves_table_chart_and_module_insertion_order(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = generate_run_token()
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            modules = OrderedDict(
                [
                    (
                        "z_module",
                        AutoWorkflowModuleWorkbook(
                            "Z step", "z_module", "Z.xlsx", b"Z-XLSX"
                        ),
                    ),
                    (
                        "a_module",
                        AutoWorkflowModuleWorkbook(
                            "A step", "a_module", "A.xlsx", b"A-XLSX"
                        ),
                    ),
                ]
            )
            save_checkpoint(
                token,
                ordered_checkpoint(now),
                "input.xlsx",
                modules,
                root=root,
                now=now,
            )

            loaded = load_checkpoint(token, root=root, now=now)

            self.assertEqual(
                list(loaded.checkpoint.result.tables), ["Z_Table", "A_Table"]
            )
            self.assertEqual(
                list(loaded.checkpoint.result.charts), ["Z_Chart", "A_Chart"]
            )
            self.assertEqual(
                list(loaded.module_workbooks), ["z_module", "a_module"]
            )

    def test_cleanup_uses_artifact_mtime_for_invalid_or_missing_manifest(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)
            run_dirs = []
            for manifest_state in ("corrupt", "missing"):
                token = generate_run_token()
                run_dir = save_checkpoint(
                    token,
                    example_checkpoint(old),
                    "input.xlsx",
                    {},
                    root=root,
                    now=old,
                )
                manifest_path = run_dir / "manifest.json"
                if manifest_state == "corrupt":
                    manifest_path.write_text("{", encoding="utf-8")
                else:
                    manifest_path.unlink()
                    (run_dir / "tables" / "orphan.tmp").write_bytes(b"partial")
                set_tree_mtime(run_dir, old)
                run_dirs.append(run_dir)

            self.assertEqual(
                cleanup_expired_checkpoints(
                    root=root, now=old + timedelta(hours=23, minutes=59)
                ),
                [],
            )
            self.assertTrue(all(run_dir.is_dir() for run_dir in run_dirs))

            removed = cleanup_expired_checkpoints(
                root=root, now=old + timedelta(hours=24, seconds=1)
            )

            self.assertEqual(set(removed), set(run_dirs))
            self.assertTrue(all(not run_dir.exists() for run_dir in run_dirs))

    def test_input_filename_is_cross_platform_basename_and_preserves_chinese(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)
            for input_path in (
                r"C:\server\uploads\中文样品.xlsx",
                "/srv/uploads/中文样品.xlsx",
            ):
                with self.subTest(input_path=input_path):
                    token = generate_run_token()
                    run_dir = save_checkpoint(
                        token,
                        example_checkpoint(now),
                        input_path,
                        {},
                        root=root,
                        now=now,
                    )

                    manifest = json.loads(
                        (run_dir / "manifest.json").read_text(encoding="utf-8")
                    )
                    loaded = load_checkpoint(token, root=root, now=now)

                    self.assertEqual(manifest["input_filename"], "中文样品.xlsx")
                    self.assertEqual(loaded.input_filename, "中文样品.xlsx")


if __name__ == "__main__":
    unittest.main()
