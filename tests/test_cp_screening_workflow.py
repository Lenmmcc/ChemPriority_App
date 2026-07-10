import io
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

import src.cp_screening_workflow as workflow
from src.cp_screening_workflow import (
    EXPECTED_WORKBOOK_SHEETS,
    build_detection_frequency,
    build_peak_area_long,
    build_pbm_toxpi_input,
    build_screening_workbook,
    calculate_pbm_toxpi,
    limit_toxpi_plot_rows,
)


class CpScreeningWorkflowTests(unittest.TestCase):
    def test_detection_frequency_uses_uploaded_files_as_samples(self):
        samples = [
            (
                "S1.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B", "Compound A"],
                        "Group_Area": [100001, 50000, 200000],
                    }
                ),
            ),
            (
                "S2.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound C"],
                        "Group_Area": [100000, 300000],
                    }
                ),
            ),
            (
                "S3.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound B", "Compound A"],
                        "Group_Area": [110000, np.nan],
                    }
                ),
            ),
        ]

        df_table, sample_peak_area = build_detection_frequency(samples, detection_threshold=1e5)

        by_compound = df_table.set_index("compound")
        self.assertEqual(by_compound.loc["Compound A", "detected_sample_count"], 1)
        self.assertEqual(by_compound.loc["Compound A", "total_sample_count"], 3)
        self.assertAlmostEqual(by_compound.loc["Compound A", "DF"], 1 / 3)
        self.assertEqual(by_compound.loc["Compound B", "detected_sample_count"], 1)
        self.assertEqual(by_compound.loc["Compound C", "detected_sample_count"], 1)

        a_s1 = sample_peak_area[
            (sample_peak_area["compound"].eq("Compound A"))
            & (sample_peak_area["sample_id"].eq("S1"))
        ].iloc[0]
        self.assertEqual(a_s1["peak_area"], 200000)
        self.assertTrue(bool(a_s1["detected"]))

        a_s2 = sample_peak_area[
            (sample_peak_area["compound"].eq("Compound A"))
            & (sample_peak_area["sample_id"].eq("S2"))
        ].iloc[0]
        self.assertFalse(bool(a_s2["detected"]))

    def test_detection_frequency_uses_file_level_group_area_mean(self):
        samples = [
            (
                "S1.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "Group Area: S1-1": [100000, 50000],
                        "Group Area: S1-2": [100001, 250000],
                    }
                ),
            ),
            (
                "S2.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "Group Area: S1-1": [90000, 100000],
                        "Group Area: S1-2": [80000, np.nan],
                    }
                ),
            ),
        ]

        df_table, sample_peak_area = build_detection_frequency(
            samples,
            peak_area_col=["Group Area: S1-1", "Group Area: S1-2"],
            detection_threshold=1e5,
        )

        by_compound = df_table.set_index("compound")
        self.assertEqual(by_compound.loc["Compound A", "detected_sample_count"], 1)
        self.assertEqual(by_compound.loc["Compound B", "detected_sample_count"], 1)
        self.assertEqual(by_compound.loc["Compound A", "total_sample_count"], 2)
        self.assertAlmostEqual(by_compound.loc["Compound A", "DF"], 0.5)

        a_s1 = sample_peak_area[
            (sample_peak_area["compound"].eq("Compound A"))
            & (sample_peak_area["source_sample_id"].eq("S1"))
        ].iloc[0]
        self.assertEqual(a_s1["sample_point"], "Group_Area_Mean")
        self.assertAlmostEqual(a_s1["peak_area"], 100000.5)
        self.assertTrue(bool(a_s1["detected"]))

        b_s2 = sample_peak_area[
            (sample_peak_area["compound"].eq("Compound B"))
            & (sample_peak_area["source_sample_id"].eq("S2"))
        ].iloc[0]
        self.assertEqual(b_s2["sample_point"], "Group_Area_Mean")
        self.assertAlmostEqual(b_s2["peak_area"], 100000)
        self.assertFalse(bool(b_s2["detected"]))
        self.assertEqual(len(sample_peak_area), 4)

    def test_detection_frequency_allows_distinct_group_area_columns_per_file(self):
        samples = [
            (
                "HH-Aci.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "Group Area: HH-ACI": [200000, 50000],
                    }
                ),
            ),
            (
                "WH-Aci.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "Group Area: WH-ACI": [90000, 300000],
                    }
                ),
            ),
            (
                "YC-zhong-Level3.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "Group Area: 01YiChang-zhong-1": [120000, 100000],
                        "Group Area: 01YiChang-zhong-2": [80000, 300000],
                    }
                ),
            ),
        ]

        selected_cols = [
            "Group Area: HH-ACI",
            "Group Area: WH-ACI",
            "Group Area: 01YiChang-zhong-1",
            "Group Area: 01YiChang-zhong-2",
        ]
        df_table, sample_peak_area = build_detection_frequency(
            samples,
            peak_area_col=selected_cols,
            detection_threshold=1e5,
        )
        mean_df = workflow.build_group_area_mean_by_sample(
            samples,
            peak_area_cols=selected_cols,
        )
        raw_long = build_peak_area_long(samples, peak_area_cols=selected_cols)

        by_compound = df_table.set_index("compound")
        self.assertEqual(by_compound.loc["Compound A", "total_sample_count"], 3)
        self.assertEqual(by_compound.loc["Compound A", "detected_sample_count"], 1)
        self.assertAlmostEqual(by_compound.loc["Compound A", "DF"], 1 / 3)
        self.assertEqual(by_compound.loc["Compound B", "detected_sample_count"], 2)
        self.assertAlmostEqual(by_compound.loc["Compound B", "DF"], 2 / 3)
        self.assertEqual(len(sample_peak_area), 6)
        self.assertEqual(len(mean_df), 6)
        self.assertEqual(len(raw_long), 8)

        yc_a = mean_df[
            mean_df["source_sample_id"].eq("YC-zhong-Level3")
            & mean_df["compound"].eq("Compound A")
        ].iloc[0]
        self.assertAlmostEqual(yc_a["Group_Area_Mean"], 100000)

    def test_detection_frequency_skips_files_without_selected_group_area_columns(self):
        samples = [
            (
                "Included.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A"],
                        "Group Area: Included": [200000],
                    }
                ),
            ),
            (
                "Skipped.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A"],
                        "Group Area: Other": [300000],
                    }
                ),
            ),
        ]

        df_table, sample_peak_area = build_detection_frequency(
            samples,
            peak_area_col=["Group Area: Included"],
            detection_threshold=1e5,
        )

        self.assertEqual(df_table.loc[0, "total_sample_count"], 1)
        self.assertEqual(df_table.loc[0, "detected_sample_count"], 1)
        self.assertEqual(set(sample_peak_area["source_sample_id"]), {"Included"})

    def test_group_area_mean_ignores_blank_values_and_keeps_zero_values(self):
        helper = getattr(workflow, "build_group_area_mean_by_sample", None)
        self.assertIsNotNone(helper, "build_group_area_mean_by_sample should be implemented")
        samples = [
            (
                "Sample.xlsx",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "formula": ["C10H20", "C12H24"],
                        "Group Area: P1": [np.nan, 10.0],
                        "Group Area: P2": [0.0, np.nan],
                        "Group Area: P3": [100.0, 30.0],
                    }
                ),
            )
        ]

        mean_df = helper(
            samples,
            compound_col="Name",
            formula_col="formula",
            peak_area_cols=["Group Area: P1", "Group Area: P2", "Group Area: P3"],
        )

        by_compound = mean_df.set_index("compound")
        self.assertAlmostEqual(by_compound.loc["Compound A", "Group_Area_Mean"], 50.0)
        self.assertAlmostEqual(by_compound.loc["Compound A", "Peak_Area"], 50.0)
        self.assertAlmostEqual(by_compound.loc["Compound A", "ir_value"], np.log10(50.0))
        self.assertEqual(by_compound.loc["Compound A", "group_area_count"], 2)
        self.assertAlmostEqual(by_compound.loc["Compound B", "Group_Area_Mean"], 20.0)
        self.assertEqual(by_compound.loc["Compound B", "group_area_columns"], "Group Area: P1; Group Area: P2; Group Area: P3")

    def test_peak_area_long_preserves_compound_by_sample_point_rows(self):
        samples = [
            (
                "SampleFile",
                pd.DataFrame(
                    {
                        "Name": ["Compound A", "Compound B"],
                        "formula": ["C10H20", "C12H24"],
                        "Group Area: P1": [100.0, 0.0],
                        "Group Area: P2": [10000.0, 1000.0],
                    }
                ),
            )
        ]

        long_df = build_peak_area_long(
            samples,
            compound_col="Name",
            formula_col="formula",
            peak_area_cols=["Group Area: P1", "Group Area: P2"],
        )

        self.assertEqual(len(long_df), 4)
        self.assertEqual(set(long_df["source_sample_id"]), {"SampleFile"})
        self.assertEqual(set(long_df["sample_id"]), {"Group Area: P1", "Group Area: P2"})
        a_p2 = long_df[
            (long_df["compound"].eq("Compound A"))
            & (long_df["sample_id"].eq("Group Area: P2"))
        ].iloc[0]
        self.assertEqual(a_p2["Peak_Area"], 10000.0)
        self.assertAlmostEqual(a_p2["ir_value"], 4.0)

    def test_pbm_toxpi_input_uses_file_level_mean_rows_before_compound_average(self):
        df_table = pd.DataFrame(
            {
                "compound": ["Compound A", "Compound B"],
                "compound_key": ["compound a", "compound b"],
                "DF": [0.5, 1.0],
                "Peak_Area": [10000.0, 1000.0],
            }
        )
        pov_lrtp = pd.DataFrame({"Name": ["Compound A", "Compound B"], "Scores": [2.0, 4.0]})
        group_area_mean = pd.DataFrame(
            {
                "source_sample_id": ["File1", "File1", "File2"],
                "sample_id": ["File1", "File1", "File2"],
                "compound": ["Compound A", "Compound A", "Compound B"],
                "Peak_Area": [5050.0, 12000.0, 1000.0],
            }
        )

        toxpi_input = build_pbm_toxpi_input(df_table, pov_lrtp, peak_area_long=group_area_mean)
        normalized, toxpi_results = calculate_pbm_toxpi(toxpi_input)

        self.assertEqual(len(toxpi_input), 3)
        self.assertEqual(toxpi_input["compound"].tolist().count("Compound A"), 2)
        self.assertEqual(set(normalized["sample_id"].dropna()), {"File1", "File2"})

        by_compound = toxpi_results.set_index("compound")
        self.assertAlmostEqual(by_compound.loc["Compound A", "Peak_Area"], 8525.0)
        self.assertAlmostEqual(
            by_compound.loc["Compound A", "toxpi"],
            normalized.loc[normalized["compound"].eq("Compound A"), "toxpi"].mean(),
        )

    def test_pbm_scores_are_normalized_in_positive_direction_for_toxpi(self):
        toxpi_input = pd.DataFrame(
            {
                "compound": ["High", "Middle", "Low"],
                "Peak_Area": [300.0, 200.0, 100.0],
                "Scores": [9.0, 5.0, 1.0],
                "DF": [1.0, 0.5, 0.0],
            }
        )

        normalized, toxpi_results = calculate_pbm_toxpi(toxpi_input)

        normalized_by_compound = normalized.set_index("compound")
        self.assertGreater(
            normalized_by_compound.loc["High", "norm_pbm"],
            normalized_by_compound.loc["Low", "norm_pbm"],
        )
        self.assertEqual(toxpi_results["compound"].tolist(), ["High", "Middle", "Low"])
        self.assertGreater(toxpi_results.loc[0, "toxpi"], toxpi_results.loc[1, "toxpi"])
        self.assertGreater(toxpi_results.loc[1, "toxpi"], toxpi_results.loc[2, "toxpi"])

    def test_toxpi_radial_plot_rows_are_limited_for_web_preview(self):
        toxpi_results = pd.DataFrame(
            {
                "compound": [f"Compound {index:03d}" for index in range(30)],
                "toxpi": np.linspace(1.0, 0.1, 30),
                "norm_peak_area": np.linspace(1.0, 0.1, 30),
                "norm_pbm": np.linspace(0.9, 0.0, 30),
                "norm_df": np.linspace(0.8, 0.0, 30),
            }
        )
        toxpi_results.attrs["toxic_cols"] = ["peak_area", "pbm", "df"]

        plot_rows, omitted_count = limit_toxpi_plot_rows(toxpi_results, max_compounds=15)

        self.assertEqual(len(plot_rows), 15)
        self.assertEqual(omitted_count, 15)
        self.assertEqual(plot_rows["compound"].iloc[0], "Compound 000")
        self.assertEqual(plot_rows["compound"].iloc[-1], "Compound 014")
        self.assertEqual(plot_rows.attrs["toxic_cols"], ["peak_area", "pbm", "df"])

    def test_screening_workbook_contains_expected_sheets_even_for_empty_tables(self):
        self.assertIn("Group_Area_Raw_Long", EXPECTED_WORKBOOK_SHEETS)
        self.assertIn("Group_Area_Mean_By_Sample", EXPECTED_WORKBOOK_SHEETS)
        workbook_buffer = build_screening_workbook(
            {
                "Input_Check": pd.DataFrame({"status": ["ok"]}),
                "ToxPi_Results": pd.DataFrame({"compound": ["A"], "toxpi": [0.8]}),
            }
        )

        workbook_buffer.seek(0)
        workbook = load_workbook(io.BytesIO(workbook_buffer.getvalue()), read_only=True)
        self.assertEqual(workbook.sheetnames, EXPECTED_WORKBOOK_SHEETS)

    def test_warning_stage_helper_preserves_existing_stage_column(self):
        helper = getattr(workflow, "with_warning_stage", None)
        self.assertIsNotNone(helper, "with_warning_stage should be implemented")

        existing_stage = pd.DataFrame(
            {
                "message": ["already staged", "needs fallback"],
                "stage": ["PubChem", ""],
            }
        )
        staged_existing = helper(existing_stage, "identifier_warnings")
        self.assertEqual(staged_existing.columns.tolist(), ["stage", "message"])
        self.assertEqual(staged_existing["stage"].tolist(), ["PubChem", "identifier_warnings"])

        missing_stage = pd.DataFrame({"message": ["plain warning"]})
        staged_missing = helper(missing_stage, "epi_errors")
        self.assertEqual(staged_missing.columns.tolist(), ["stage", "message"])
        self.assertEqual(staged_missing.loc[0, "stage"], "epi_errors")

    def test_fourth_page_no_longer_exposes_epa_echa_combined_plot(self):
        page_text = Path("pages/4_化合物用途查询.py").read_text(encoding="utf-8")

        self.assertNotIn("EPA_ECHA_Combined", page_text)
        self.assertNotIn("generate_combined_use_rose_plot", page_text)
        self.assertNotIn("build_epa_echa_combined_rose_data", page_text)

    def test_comprehensive_screening_page_front_half_figures_are_rendered(self):
        page_path = Path("pages/0_综合筛查流程.py")
        page_text = page_path.read_text(encoding="utf-8")

        self.assertIn("render_front_half_figures(front_state)", page_text)
        self.assertNotIn("point_screening_results", page_text)
        self.assertNotIn("POINT_FRONT_HALF_FIGURES", page_text)
        self.assertIn("Group_Area_Mean", page_text)
        self.assertIn("build_group_area_mean_by_sample", page_text)
        self.assertIn("def with_warning_stage(", page_text)
        self.assertIn("with_warning_stage(table, key)", page_text)
        self.assertNotIn("    with_warning_stage,\n", page_text)
        workflow_tables_text = page_text.split("def workflow_tables", 1)[1]
        self.assertNotIn('insert(0, "stage"', workflow_tables_text)
        self.assertIn("st.image", page_text)
        self.assertIn("category_percent_donut_with_total", page_text)
        self.assertIn("compound_bubble_plot", page_text)
        self.assertIn("VanKrevelen", page_text)
        self.assertIn("PER_SAMPLE_FRONT_HALF_FIGURES", page_text)
        self.assertIn("SUMMARY_FRONT_HALF_FIGURES", page_text)
        self.assertIn("summary_figure_paths", page_text)
        self.assertIn("save_boxplot_log_transformed", page_text)
        per_sample_figures = page_text.split("PER_SAMPLE_FRONT_HALF_FIGURES", 1)[1].split("]", 1)[0]
        self.assertNotIn("boxplot_log_transformed", per_sample_figures)
        self.assertIn("TOXPI_RADIAL_MAX_COMPOUNDS", page_text)
        self.assertIn("TOXPI_RADIAL_PLOT_VERSION", page_text)
        self.assertIn("limit_toxpi_plot_rows", page_text)
        self.assertIn("generate_r_style_toxpi_plot", page_text)
        self.assertIn("import importlib", page_text)
        self.assertIn("import src.toxpi_calc as toxpi_calc", page_text)
        self.assertIn("importlib.reload(toxpi_calc)", page_text)
        self.assertNotIn("from src.toxpi_calc import generate_r_style_toxpi_plot", page_text)
        self.assertIn("label_wrap_width=20", page_text)
        self.assertIn("refresh_toxpi_radial_plot", page_text)
        self.assertIn('"cp_screening_radial_plot_version"', page_text)
        self.assertNotIn("generate_multi_toxpi_plot", page_text)
        self.assertIn("def render_sample_mapping_tabs(samples):", page_text)
        self.assertIn("mapping_tabs = st.tabs", page_text)
        self.assertIn("normalize_samples_for_mappings", page_text)
        self.assertIn("sample_mappings", page_text)
        self.assertIn('front_state["representative_table"]', page_text)


if __name__ == "__main__":
    unittest.main()
