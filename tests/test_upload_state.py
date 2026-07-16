import unittest
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import src.upload_state as upload_state
from src.auto_query_checkpoint import generate_run_token, load_checkpoint, save_checkpoint
from src.auto_query_workflow import (
    AutoWorkflowCheckpoint,
    AutoWorkflowMapping,
    AutoWorkflowResult,
)
from src.upload_state import (
    cached_uploads,
    clear_uploads,
    store_uploads,
    upload_bytes,
    upload_name,
)


class FakeUpload:
    def __init__(self, name, payload):
        self.name = name
        self._payload = payload

    def getvalue(self):
        return self._payload


class UploadStateTests(unittest.TestCase):
    def test_store_uploads_restores_multiple_files_without_streamlit_objects(self):
        state = {}

        files, changed = store_uploads(
            state,
            "screening_files",
            "screening_signature",
            [FakeUpload("A.xlsx", b"A"), FakeUpload("B.xlsx", b"B")],
        )

        self.assertTrue(changed)
        self.assertEqual(
            [(upload_name(file), upload_bytes(file)) for file in files],
            [("A.xlsx", b"A"), ("B.xlsx", b"B")],
        )
        self.assertEqual(
            [
                (upload_name(file), upload_bytes(file))
                for file in cached_uploads(state, "screening_files")
            ],
            [("A.xlsx", b"A"), ("B.xlsx", b"B")],
        )

    def test_store_uploads_detects_replaced_bytes(self):
        state = {}

        self.assertTrue(
            store_uploads(state, "files", "signature", [FakeUpload("A.xlsx", b"one")])[1]
        )
        self.assertFalse(
            store_uploads(state, "files", "signature", [FakeUpload("A.xlsx", b"one")])[1]
        )
        self.assertTrue(
            store_uploads(state, "files", "signature", [FakeUpload("A.xlsx", b"two")])[1]
        )

    def test_clear_uploads_removes_requested_input_and_result_keys(self):
        state = {
            "files": [{"name": "A.xlsx", "bytes": b"A"}],
            "signature": "signature",
            "result": object(),
        }

        clear_uploads(state, ("files", "signature", "result"))

        self.assertEqual(state, {})

    def test_settings_signature_is_stable_for_equivalent_nested_settings(self):
        self.assertTrue(hasattr(upload_state, "settings_signature"))
        settings_signature = upload_state.settings_signature
        first = {
            "mapping": {"compound_col": "Name", "group_area_cols": ["A", "B"]},
            "weights": {"peak_area": 0.4, "pbm": 0.4, "df": 0.2},
        }
        second = {
            "weights": {"df": 0.2, "pbm": 0.4, "peak_area": 0.4},
            "mapping": {"group_area_cols": ["A", "B"], "compound_col": "Name"},
        }

        self.assertEqual(settings_signature(first), settings_signature(second))

    def test_settings_change_clears_only_result_cache_and_updates_signature(self):
        self.assertTrue(hasattr(upload_state, "invalidate_results_on_settings_change"))
        invalidate_results_on_settings_change = upload_state.invalidate_results_on_settings_change
        result_keys = ("result", "charts", "zip")
        state = {
            "input_files": [{"name": "A.xlsx", "bytes": b"A"}],
            "result": object(),
            "charts": object(),
            "zip": object(),
        }
        original = {"run_epi": False, "axis": {"dbe_x_max": 60.0}}

        self.assertFalse(
            invalidate_results_on_settings_change(
                state,
                "settings_signature",
                original,
                result_keys,
            )
        )
        preserved_signature = state["settings_signature"]
        self.assertFalse(
            invalidate_results_on_settings_change(
                state,
                "settings_signature",
                {"axis": {"dbe_x_max": 60.0}, "run_epi": False},
                result_keys,
            )
        )
        self.assertEqual(state["settings_signature"], preserved_signature)
        self.assertIn("result", state)

        self.assertTrue(
            invalidate_results_on_settings_change(
                state,
                "settings_signature",
                {"run_epi": True, "axis": {"dbe_x_max": 60.0}},
                result_keys,
            )
        )

        self.assertEqual(set(state), {"input_files", "settings_signature"})
        self.assertNotEqual(state["settings_signature"], preserved_signature)

    def test_recovered_settings_mismatch_clears_session_results_but_keeps_disk_checkpoint(self):
        self.assertTrue(
            hasattr(
                upload_state,
                "invalidate_recovered_results_on_settings_mismatch",
            )
        )
        invalidate_recovered = (
            upload_state.invalidate_recovered_results_on_settings_mismatch
        )
        result_keys = ("result", "charts", "zip")
        checkpoint_keys = (
            "auto_query_run_token",
            "auto_query_checkpoint_manifest",
            "auto_query_partial_result",
            "auto_query_module_workbooks",
            "auto_query_checkpoint_warning",
        )
        current_settings = {"run_epi": False, "workers": 2}
        restored_settings = {"run_epi": True, "workers": 4}
        restored_signature = upload_state.settings_signature(restored_settings)
        current_signature = upload_state.settings_signature(current_settings)
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["A"]}),
            tables=OrderedDict(),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )
        token = generate_run_token()
        checkpoint = AutoWorkflowCheckpoint(
            run_id=generate_run_token(),
            input_signature="same-input",
            settings_signature=restored_signature,
            selected_steps=(),
            finished_steps=(),
            current_step=None,
            status="running",
            result=result,
            error_message="",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        state = {
            "input_files": [{"name": "A.xlsx", "bytes": b"A"}],
            "input_signature": "same-input",
            "result": result,
            "charts": {"old": object()},
            "zip": object(),
            "auto_query_run_token": token,
            "auto_query_checkpoint_manifest": {
                "input_signature": "same-input",
                "settings_signature": restored_signature,
            },
            "auto_query_partial_result": result,
            "auto_query_module_workbooks": {"old": object()},
            "auto_query_checkpoint_warning": "old warning",
        }

        with TemporaryDirectory() as temporary_root:
            save_checkpoint(
                token,
                checkpoint,
                "A.xlsx",
                OrderedDict(),
                root=temporary_root,
            )

            mismatch = invalidate_recovered(
                state,
                current_signature,
                result_keys,
                checkpoint_keys,
            )

            self.assertTrue(mismatch)
            self.assertEqual(
                state,
                {
                    "input_files": [{"name": "A.xlsx", "bytes": b"A"}],
                    "input_signature": "same-input",
                },
            )
            loaded = load_checkpoint(token, root=temporary_root)
            self.assertEqual(loaded.checkpoint.settings_signature, restored_signature)

    def test_page_6_compares_recovered_settings_before_existing_invalidation(self):
        page_text = next(Path("pages").glob("6_*.py")).read_text(encoding="utf-8")
        settings_block = page_text.split("result_settings = {", 1)[1].split(
            "start_run =", 1
        )[0]

        current_signature = "current_settings_signature = settings_signature(result_settings)"
        recovered_invalidation = (
            "invalidate_recovered_results_on_settings_mismatch("
        )
        self.assertIn(current_signature, settings_block)
        self.assertIn(recovered_invalidation, settings_block)
        self.assertLess(
            settings_block.index(recovered_invalidation),
            settings_block.index("invalidate_results_on_settings_change("),
        )
        mismatch_block = settings_block.split(
            "if recovered_settings_mismatch:", 1
        )[1].split("settings_changed =", 1)[0]
        self.assertIn('st.query_params.pop("run", None)', mismatch_block)
        self.assertIn("st.info(", mismatch_block)
        self.assertNotIn("delete_checkpoint", mismatch_block)

    def test_main_upload_pages_declare_a_cache_restore_path(self):
        expected = {
            "0_": ("cp_screening_input_files", "cached_uploads"),
            "1_": ("admet_input_bytes", "cached_input_bytes"),
            "2_": ("cached_df", "cached_filename"),
            "3_": ("epi_input_bytes", "cached_input_bytes"),
            "4_": ("use_query_input_bytes", "cached_input_bytes"),
            "6_": ("auto_query_input_files", "cached_uploads"),
        }

        for prefix, tokens in expected.items():
            page_path = next(Path("pages").glob(f"{prefix}*.py"))
            page_text = page_path.read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, page_text, f"{page_path.name} is missing {token}")

    def test_page_6_declares_all_checkpoint_session_keys_and_clears_the_current_token(self):
        page_text = next(Path("pages").glob("6_*.py")).read_text(encoding="utf-8")
        for key in (
            "auto_query_run_token",
            "auto_query_checkpoint_manifest",
            "auto_query_partial_result",
            "auto_query_module_workbooks",
            "auto_query_checkpoint_warning",
        ):
            self.assertIn(key, page_text)
        clear_block = page_text.split("def clear_auto_query_state", 1)[1].split(
            "st.set_page_config", 1
        )[0]
        self.assertIn("delete_checkpoint", clear_block)
        self.assertIn('st.query_params.pop("run", None)', clear_block)


if __name__ == "__main__":
    unittest.main()
