import unittest

import pandas as pd

from src.multi_file_screening import (
    PrimaryWorkbook,
    SampleColumnMapping,
    prepare_multi_file_screening,
)
from src.r_screening_replica.schema import ScreeningAxisRanges


class MultiFileScreeningTests(unittest.TestCase):
    def test_files_remain_samples_and_file_means_precede_df(self):
        samples = [
            PrimaryWorkbook(
                file_name="A.xlsx",
                sample_id="A",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [200000.0],
                        "P2": [0.0],
                        "SMILES": ["CCO"],
                    }
                ),
            ),
            PrimaryWorkbook(
                file_name="B.xlsx",
                sample_id="B",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [300000.0],
                        "P2": [300000.0],
                        "SMILES": ["CCO"],
                    }
                ),
            ),
        ]
        mappings = {
            sample.sample_id: SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="P1",
                group_area_cols=("P1", "P2"),
                smiles_col="SMILES",
            )
            for sample in samples
        }

        result = prepare_multi_file_screening(
            samples,
            mappings,
            detection_threshold=100000.0,
            axis_ranges=ScreeningAxisRanges(),
        )

        self.assertEqual(
            set(result.group_area_mean_by_sample["source_sample_id"]),
            {"A", "B"},
        )
        df_row = result.df_table.set_index("compound").loc["X"]
        self.assertEqual(df_row["total_sample_count"], 2)
        self.assertEqual(df_row["detected_sample_count"], 1)
        self.assertEqual(result.representative_table["Name"].tolist(), ["X"])

    def test_each_file_uses_only_its_selected_group_area_columns(self):
        samples = [
            PrimaryWorkbook(
                file_name="A.xlsx",
                sample_id="A",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [200000.0],
                        "P2": [0.0],
                    }
                ),
            ),
            PrimaryWorkbook(
                file_name="B.xlsx",
                sample_id="B",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [300000.0],
                        "P2": [0.0],
                    }
                ),
            ),
        ]
        mappings = {
            "A": SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="P1",
                group_area_cols=("P1",),
            ),
            "B": SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="P2",
                group_area_cols=("P2",),
            ),
        }

        result = prepare_multi_file_screening(
            samples,
            mappings,
            detection_threshold=100000.0,
            axis_ranges=ScreeningAxisRanges(),
        )

        means = result.group_area_mean_by_sample.set_index("source_sample_id")
        self.assertEqual(means.loc["A", "Group_Area_Mean"], 200000.0)
        self.assertEqual(means.loc["B", "Group_Area_Mean"], 0.0)
        detections = result.df_detection_table.set_index("source_sample_id")
        self.assertTrue(bool(detections.loc["A", "detected"]))
        self.assertFalse(bool(detections.loc["B", "detected"]))


if __name__ == "__main__":
    unittest.main()
