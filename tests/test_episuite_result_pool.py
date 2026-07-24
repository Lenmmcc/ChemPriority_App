import unittest

import pandas as pd

from src.episuite_result_pool import (
    clear_epi_pool,
    read_epi_pool,
    remove_epi_pool_contributor,
    upsert_epi_pool,
)


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


if __name__ == "__main__":
    unittest.main()
