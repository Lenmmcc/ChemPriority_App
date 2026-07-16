import unittest
from pathlib import Path

import src.upload_state as upload_state
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


if __name__ == "__main__":
    unittest.main()
