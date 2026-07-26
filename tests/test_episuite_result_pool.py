import unittest

import pandas as pd

from src.episuite_result_pool import (
    build_api_epi_pool_payload,
    build_uploaded_epi_pool_payload,
    clear_epi_pool,
    clear_tracked_epi_pool_contributor,
    make_epi_pool_contributor_id,
    make_uploaded_result_source_signature,
    read_epi_pool,
    replace_epi_pool_source_contributor,
    advance_epi_uploader_epoch,
    remove_epi_pool_contributor,
    remove_stale_epi_pool_contributor,
    upsert_epi_pool,
)
from src.episuite_io import merge_results_with_input


class EPIResultPoolTests(unittest.TestCase):
    def test_contributors_merge_and_can_be_removed_independently(self):
        state = {}
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["A"], "log_kow": [1.0]}),
            pd.DataFrame({"compound": ["A"], "field": ["log_kow"]}),
        )
        upsert_epi_pool(
            state,
            "epi-page:two",
            pd.DataFrame({"compound": ["B"], "log_kow": [2.0]}),
            pd.DataFrame({"compound": ["B"], "field": ["log_kow"]}),
        )

        results, _ = read_epi_pool(state)
        self.assertEqual(results["compound"].tolist(), ["A", "B"])

        remove_epi_pool_contributor(state, "epi-page:one")
        results, _ = read_epi_pool(state)
        self.assertEqual(results["compound"].tolist(), ["B"])

        clear_epi_pool(state)
        self.assertEqual(read_epi_pool(state)[0].shape[0], 0)

    def test_pool_state_contains_serializable_records_not_dataframes(self):
        state = {}
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["A"], "log_kow": [1.0]}),
            pd.DataFrame(),
        )
        self.assertIsInstance(state["shared_epi_result_pool"], dict)
        self.assertIsInstance(
            state["shared_epi_result_pool"]["contributors"]["epi-page:one"]["results"],
            list,
        )

    def test_republishing_contributor_replaces_only_its_previous_rows(self):
        state = {}
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["old"], "log_kow": [1.0]}),
            pd.DataFrame(),
        )
        upsert_epi_pool(
            state,
            "epi-page:two",
            pd.DataFrame({"compound": ["other"], "log_kow": [2.0]}),
            pd.DataFrame(),
        )
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["new"], "log_kow": [3.0]}),
            pd.DataFrame(),
        )

        results, _ = read_epi_pool(state)
        self.assertEqual(results["compound"].tolist(), ["new", "other"])

    def test_read_returns_isolated_dataframe_copies(self):
        state = {}
        upsert_epi_pool(
            state,
            "epi-page:one",
            pd.DataFrame({"compound": ["A"], "log_kow": [1.0]}),
            pd.DataFrame({"compound": ["A"], "source_file": ["a.xlsx"]}),
        )

        results, provenance = read_epi_pool(state)
        results.loc[0, "compound"] = "changed"
        provenance.loc[0, "source_file"] = "changed.xlsx"
        fresh_results, fresh_provenance = read_epi_pool(state)

        self.assertEqual(fresh_results.loc[0, "compound"], "A")
        self.assertEqual(fresh_provenance.loc[0, "source_file"], "a.xlsx")

    def test_uploaded_payload_includes_only_matched_adoptable_rows_with_provenance(self):
        input_df = pd.DataFrame(
            {"compound": ["matched", "unmatched"], "smiles": ["CC", "CCC"]}
        )
        parsed_df = pd.DataFrame(
            {
                "compound": ["matched"],
                "smiles": ["CC"],
                "log_kow": [1.5],
                "source_file": ["uploaded.xlsx"],
                "source_sheet": ["Core_Summary"],
                "source_row": [2],
            }
        )
        merged = merge_results_with_input(input_df, parsed_df)

        results, provenance = build_uploaded_epi_pool_payload(merged)

        self.assertEqual(results["compound"].tolist(), ["matched"])
        self.assertEqual(results["source_type"].tolist(), ["uploaded"])
        self.assertEqual(provenance.loc[0, "source_file"], "uploaded.xlsx")
        self.assertEqual(provenance.loc[0, "source_sheet"], "Core_Summary")
        self.assertEqual(provenance.loc[0, "source_row"], 2)

    def test_api_payload_marks_each_successful_result_with_source_metadata(self):
        results, provenance = build_api_epi_pool_payload(
            pd.DataFrame({"compound": ["A"], "status": ["success"], "log_kow": [1.0]}),
            source_file="EPI Web Suite API",
        )

        self.assertEqual(results.loc[0, "source_type"], "api")
        self.assertEqual(results.loc[0, "source_file"], "EPI Web Suite API")
        self.assertEqual(provenance.loc[0, "source_type"], "api")
        self.assertEqual(provenance.loc[0, "source_file"], "EPI Web Suite API")

    def test_input_signature_change_removes_only_stale_contributor(self):
        state = {"epi_pool_contributor_id": "epi-page:old"}
        upsert_epi_pool(
            state,
            "epi-page:old",
            pd.DataFrame({"compound": ["old"]}),
            pd.DataFrame(),
        )
        upsert_epi_pool(
            state,
            "other-page:one",
            pd.DataFrame({"compound": ["other"]}),
            pd.DataFrame(),
        )

        removed = remove_stale_epi_pool_contributor(
            state, "epi_pool_contributor_id", "epi-page:new"
        )

        self.assertTrue(removed)
        self.assertNotIn("epi_pool_contributor_id", state)
        results, _ = read_epi_pool(state)
        self.assertEqual(results["compound"].tolist(), ["other"])

    def test_same_input_signature_preserves_existing_contributor(self):
        state = {"epi_pool_contributor_id": "epi-page:same"}
        upsert_epi_pool(
            state,
            "epi-page:same",
            pd.DataFrame({"compound": ["A"]}),
            pd.DataFrame(),
        )

        removed = remove_stale_epi_pool_contributor(
            state, "epi_pool_contributor_id", "epi-page:same"
        )

        self.assertFalse(removed)
        self.assertEqual(read_epi_pool(state)[0]["compound"].tolist(), ["A"])

    def test_api_and_upload_contributors_preserve_separate_values_for_one_input(self):
        state = {}
        api_id = make_epi_pool_contributor_id("same-input", "api")
        upload_id = make_epi_pool_contributor_id("same-input", "uploaded")
        api_results, api_provenance = build_api_epi_pool_payload(
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "status": ["success"],
                    "henry_atm_m3_mol": [5.0e-6],
                }
            ),
            source_file="https://example.test/epi",
        )
        upload_results, upload_provenance = build_uploaded_epi_pool_payload(
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "log_kow": [1.5],
                    "source_file": ["upload.xlsx"],
                    "source_row": [2],
                }
            )
        )
        upsert_epi_pool(state, api_id, api_results, api_provenance)
        upsert_epi_pool(state, upload_id, upload_results, upload_provenance)

        results, provenance = read_epi_pool(state)

        self.assertEqual(set(results["source_type"]), {"api", "uploaded"})
        self.assertEqual(
            results.loc[results["source_type"].eq("api"), "henry_atm_m3_mol"].iloc[0],
            5.0e-6,
        )
        self.assertEqual(
            results.loc[results["source_type"].eq("uploaded"), "log_kow"].iloc[0],
            1.5,
        )
        self.assertEqual(set(provenance["source_type"]), {"api", "uploaded"})

    def test_republishing_upload_contributor_does_not_remove_api_contributor(self):
        state = {}
        api_id = make_epi_pool_contributor_id("same-input", "api")
        upload_id = make_epi_pool_contributor_id("same-input", "uploaded")
        upsert_epi_pool(
            state,
            api_id,
            pd.DataFrame({"compound": ["A"], "source_type": ["api"]}),
            pd.DataFrame(),
        )
        upsert_epi_pool(
            state,
            upload_id,
            pd.DataFrame({"compound": ["A"], "log_kow": [1.0], "source_type": ["uploaded"]}),
            pd.DataFrame(),
        )
        upsert_epi_pool(
            state,
            upload_id,
            pd.DataFrame({"compound": ["A"], "log_kow": [2.0], "source_type": ["uploaded"]}),
            pd.DataFrame(),
        )

        results, _ = read_epi_pool(state)
        self.assertEqual(set(results["source_type"]), {"api", "uploaded"})
        self.assertEqual(
            results.loc[results["source_type"].eq("uploaded"), "log_kow"].iloc[0],
            2.0,
        )

    def test_input_change_removes_api_and_upload_contributors(self):
        state = {
            "epi_api_pool_contributor_id": make_epi_pool_contributor_id("old", "api"),
            "epi_uploaded_pool_contributor_id": make_epi_pool_contributor_id("old", "uploaded"),
        }
        for contributor_id in state.copy().values():
            upsert_epi_pool(
                state,
                contributor_id,
                pd.DataFrame({"compound": [contributor_id]}),
                pd.DataFrame(),
            )

        for source_type, state_key in (
            ("api", "epi_api_pool_contributor_id"),
            ("uploaded", "epi_uploaded_pool_contributor_id"),
        ):
            remove_stale_epi_pool_contributor(
                state, state_key, make_epi_pool_contributor_id("new", source_type)
            )

        self.assertTrue(read_epi_pool(state)[0].empty)

    def test_replacing_upload_selection_removes_only_old_upload_contributor(self):
        state = {
            "epi_api_pool_contributor_id": make_epi_pool_contributor_id("input", "api"),
            "epi_uploaded_pool_contributor_id": make_epi_pool_contributor_id(
                "input", "uploaded"
            ),
            "epi_uploaded_pool_source_signature": "old-upload",
        }
        for contributor_id in (
            state["epi_api_pool_contributor_id"],
            state["epi_uploaded_pool_contributor_id"],
        ):
            upsert_epi_pool(
                state,
                contributor_id,
                pd.DataFrame({"compound": [contributor_id]}),
                pd.DataFrame(),
            )

        changed = replace_epi_pool_source_contributor(
            state,
            "epi_uploaded_pool_contributor_id",
            "epi_uploaded_pool_source_signature",
            "new-upload",
        )

        self.assertTrue(changed)
        self.assertNotIn("epi_uploaded_pool_contributor_id", state)
        self.assertEqual(
            read_epi_pool(state)[0]["compound"].tolist(),
            [make_epi_pool_contributor_id("input", "api")],
        )

    def test_same_upload_selection_does_not_remove_its_contributor(self):
        contributor_id = make_epi_pool_contributor_id("input", "uploaded")
        state = {
            "epi_uploaded_pool_contributor_id": contributor_id,
            "epi_uploaded_pool_source_signature": "same-upload",
        }
        upsert_epi_pool(
            state,
            contributor_id,
            pd.DataFrame({"compound": ["A"]}),
            pd.DataFrame(),
        )

        changed = replace_epi_pool_source_contributor(
            state,
            "epi_uploaded_pool_contributor_id",
            "epi_uploaded_pool_source_signature",
            "same-upload",
        )

        self.assertFalse(changed)
        self.assertEqual(read_epi_pool(state)[0]["compound"].tolist(), ["A"])

        clear_tracked_epi_pool_contributor(
            state,
            "epi_uploaded_pool_contributor_id",
            "epi_uploaded_pool_source_signature",
        )
        self.assertTrue(read_epi_pool(state)[0].empty)

    def test_selected_sheet_is_part_of_uploaded_result_source_signature(self):
        sheet_a = make_uploaded_result_source_signature(
            [("results.xlsx", b"same workbook bytes", "Core_Summary")]
        )
        sheet_b = make_uploaded_result_source_signature(
            [("results.xlsx", b"same workbook bytes", "EPI_Results")]
        )

        self.assertNotEqual(sheet_a, sheet_b)

    def test_new_sheet_with_empty_payload_removes_old_upload_but_preserves_api(self):
        api_id = make_epi_pool_contributor_id("input", "api")
        upload_id = make_epi_pool_contributor_id("input", "uploaded")
        state = {
            "epi_api_pool_contributor_id": api_id,
            "epi_uploaded_pool_contributor_id": upload_id,
            "epi_uploaded_pool_source_signature": make_uploaded_result_source_signature(
                [("results.xlsx", b"same workbook bytes", "Core_Summary")]
            ),
        }
        for contributor_id in (api_id, upload_id):
            upsert_epi_pool(
                state,
                contributor_id,
                pd.DataFrame({"compound": [contributor_id]}),
                pd.DataFrame(),
            )

        changed = replace_epi_pool_source_contributor(
            state,
            "epi_uploaded_pool_contributor_id",
            "epi_uploaded_pool_source_signature",
            make_uploaded_result_source_signature(
                [("results.xlsx", b"same workbook bytes", "EPI_Results")]
            ),
        )

        self.assertTrue(changed)
        self.assertEqual(read_epi_pool(state)[0]["compound"].tolist(), [api_id])

    def test_clearing_upload_state_advances_uploader_epoch_without_recovering_rows(self):
        contributor_id = make_epi_pool_contributor_id("input", "uploaded")
        state = {
            "epi_uploaded_pool_contributor_id": contributor_id,
            "epi_uploaded_pool_source_signature": "old-upload",
            "epi_result_uploader_epoch": 0,
        }
        upsert_epi_pool(
            state,
            contributor_id,
            pd.DataFrame({"compound": ["A"]}),
            pd.DataFrame(),
        )

        clear_tracked_epi_pool_contributor(
            state,
            "epi_uploaded_pool_contributor_id",
            "epi_uploaded_pool_source_signature",
        )
        next_epoch = advance_epi_uploader_epoch(state, "epi_result_uploader_epoch")

        self.assertEqual(next_epoch, 1)
        self.assertTrue(read_epi_pool(state)[0].empty)
        self.assertNotIn("epi_uploaded_pool_source_signature", state)

    def test_clearing_current_data_can_advance_input_and_result_uploader_epochs(self):
        state = {
            "epi_input_uploader_epoch": 4,
            "epi_result_uploader_epoch": 9,
        }

        input_epoch = advance_epi_uploader_epoch(state, "epi_input_uploader_epoch")
        result_epoch = advance_epi_uploader_epoch(state, "epi_result_uploader_epoch")

        self.assertEqual(input_epoch, 5)
        self.assertEqual(result_epoch, 10)


if __name__ == "__main__":
    unittest.main()
