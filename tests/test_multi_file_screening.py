import unittest

import pandas as pd
import src.multi_file_screening as multi_file_screening

from src.cp_screening_workflow import build_pbm_toxpi_input
from src.multi_file_screening import (
    PrimaryWorkbook,
    SampleColumnMapping,
    prepare_multi_file_screening,
)
from src.r_screening_replica.schema import ScreeningAxisRanges


class MultiFileScreeningTests(unittest.TestCase):
    def test_primary_epi_universe_uses_hierarchical_identity_without_name_dedup(self):
        self.assertTrue(
            hasattr(multi_file_screening, "build_primary_epi_universe")
        )
        samples = [
            PrimaryWorkbook(
                file_name="A.xlsx",
                sample_id="A",
                data=pd.DataFrame(
                    {
                        "Name": ["Shared", "Shared duplicate"],
                        "Formula": ["C2H6O", "C2H6O"],
                        "Area": [10.0, 9.0],
                        "SMILES": ["CCO", "CCO"],
                        "CAS": ["64-17-5", "64-17-5"],
                    }
                ),
            ),
            PrimaryWorkbook(
                file_name="B.xlsx",
                sample_id="B",
                data=pd.DataFrame(
                    {
                        "Name": ["Shared", "Shared"],
                        "Formula": ["C3H8O", "C4H10O"],
                        "Area": [8.0, 7.0],
                        "SMILES": ["CCCO", "CCCCO"],
                        "CAS": ["71-23-8", "78-83-1"],
                    }
                ),
            ),
        ]
        mappings = {
            sample.sample_id: SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="Area",
                group_area_cols=("Area",),
                smiles_col="SMILES",
                cas_col="CAS",
            )
            for sample in samples
        }

        membership = multi_file_screening.build_primary_epi_membership(
            samples,
            mappings,
        )
        universe = multi_file_screening.build_primary_epi_universe(
            samples,
            mappings,
        )

        self.assertEqual(len(membership), 4)
        self.assertEqual(
            set(universe["cas"]),
            {"64-17-5", "71-23-8", "78-83-1"},
        )
        self.assertEqual(
            universe.loc[universe["compound"].eq("Shared"), "cas"].tolist(),
            ["64-17-5", "71-23-8", "78-83-1"],
        )

    def test_raw_representative_and_pbm_rows_preserve_multi_file_semantics(self):
        samples = [
            PrimaryWorkbook(
                file_name="A.xlsx",
                sample_id="A",
                data=pd.DataFrame(
                    {
                        "Name": ["X", "X", "Y"],
                        "Formula": ["C2H6O", "C2H6O", "C3H8O"],
                        "A1": [200000.0, 100000.0, 50000.0],
                        "A2": [0.0, 100000.0, 50000.0],
                        "Unused": [999999.0, 999999.0, 999999.0],
                    }
                ),
            ),
            PrimaryWorkbook(
                file_name="B.xlsx",
                sample_id="B",
                data=pd.DataFrame(
                    {
                        "Name": ["X", "Y"],
                        "Formula": ["C2H6O", "C3H8O"],
                        "B1": [999999.0, 999999.0],
                        "B2": [300000.0, 400000.0],
                    }
                ),
            ),
        ]
        mappings = {
            "A": SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="A1",
                group_area_cols=("A1", "A2"),
            ),
            "B": SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="B2",
                group_area_cols=("B2",),
            ),
        }

        result = prepare_multi_file_screening(
            samples,
            mappings,
            detection_threshold=100000.0,
            axis_ranges=ScreeningAxisRanges(),
        )

        self.assertEqual(len(result.group_area_raw_long), 8)
        self.assertEqual(
            set(
                result.group_area_raw_long[
                    ["source_sample_id", "sample_id"]
                ].itertuples(index=False, name=None)
            ),
            {("A", "A1"), ("A", "A2"), ("B", "B2")},
        )
        self.assertEqual(
            set(result.representative_table["Name"]),
            {"X", "Y"},
        )
        self.assertEqual(
            result.representative_table["compound_key"].nunique(),
            2,
        )

        pov_unique = result.representative_table[["Name"]].copy()
        pov_unique["Scores"] = [1.0, 2.0]
        self.assertEqual(pov_unique["Name"].nunique(), len(pov_unique))
        pbm_input = build_pbm_toxpi_input(
            result.df_table,
            pov_unique,
            peak_area_long=result.sample_peak_area,
        )
        self.assertEqual(len(pbm_input), 4)
        self.assertEqual(set(pbm_input["sample_id"]), {"A", "B"})
        self.assertEqual(
            pbm_input.groupby("sample_id").size().to_dict(),
            {"A": 2, "B": 2},
        )

    def test_duplicate_case_insensitive_primary_file_names_are_rejected(self):
        samples = [
            PrimaryWorkbook(
                file_name="Lake-A.xlsx",
                sample_id="Lake-A",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [200000.0],
                    }
                ),
            ),
            PrimaryWorkbook(
                file_name="lake-a.XLSX",
                sample_id="lake-a",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [0.0],
                    }
                ),
            ),
        ]
        mappings = {
            sample.sample_id: SampleColumnMapping(
                compound_col="Name",
                formula_col="Formula",
                peak_area_col="P1",
                group_area_cols=("P1",),
            )
            for sample in samples
        }

        with self.assertRaisesRegex(
            ValueError,
            "Duplicate primary file names",
        ):
            prepare_multi_file_screening(
                samples,
                mappings,
                detection_threshold=100000.0,
                axis_ranges=ScreeningAxisRanges(),
            )

    def test_duplicate_case_insensitive_sample_ids_are_rejected(self):
        samples = [
            PrimaryWorkbook(
                file_name="Lake-A.xlsx",
                sample_id="Lake",
                data=pd.DataFrame({"Name": ["X"], "P1": [200000.0]}),
            ),
            PrimaryWorkbook(
                file_name="Lake-B.xlsx",
                sample_id="lake",
                data=pd.DataFrame({"Name": ["X"], "P1": [0.0]}),
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "Duplicate primary sample IDs",
        ):
            prepare_multi_file_screening(
                samples,
                {},
                detection_threshold=100000.0,
                axis_ranges=ScreeningAxisRanges(),
            )

    def test_missing_explicit_mapping_excludes_file_from_all_sample_calculations(self):
        samples = [
            PrimaryWorkbook(
                file_name="A.xlsx",
                sample_id="A",
                data=pd.DataFrame(
                    {
                        "Name": ["X"],
                        "Formula": ["C2H6O"],
                        "P1": [200000.0],
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
                        "P1": [0.0],
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
            )
        }

        result = prepare_multi_file_screening(
            samples,
            mappings,
            detection_threshold=100000.0,
            axis_ranges=ScreeningAxisRanges(),
        )

        warning = result.warnings.set_index("sample_id").loc["B"]
        self.assertEqual(warning["stage"], "column_mapping")
        self.assertIn("mapping is missing", warning["message"].lower())
        self.assertEqual(result.df_table.loc[0, "total_sample_count"], 1)
        self.assertEqual(
            set(result.sample_peak_area["source_sample_id"]),
            {"A"},
        )
        self.assertEqual(
            {sample["name"] for sample in result.normalized_samples},
            {"A"},
        )
        mapping_audit = result.input_file_mappings.set_index("sample_id")
        self.assertEqual(mapping_audit.loc["B", "mapping_status"], "missing")
        self.assertFalse(bool(mapping_audit.loc["B", "participating"]))
        self.assertEqual(mapping_audit.loc["B", "file_name"], "B.xlsx")

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
