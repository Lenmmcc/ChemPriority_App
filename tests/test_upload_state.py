import unittest
from pathlib import Path

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
