import unittest

import pandas as pd

from src.episuite_result_pool import (
    build_api_epi_pool_payload,
    build_uploaded_epi_pool_payload,
    clear_epi_pool,
    read_epi_pool,
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


if __name__ == "__main__":
    unittest.main()
