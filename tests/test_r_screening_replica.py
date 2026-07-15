import tempfile
import unittest
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import pandas as pd

from src.r_screening_replica import plots
from src.r_screening_replica import (
    ScreeningConfig,
    build_sample_peak_area_long,
    calculate_ratios_and_dbe,
    classify_compounds,
    run_screening_pipeline,
)
from src.r_screening_replica.plots import _draw_compound_bubble


class RScreeningReplicaUnitTests(unittest.TestCase):
    def test_dbe_bubble_has_white_background_and_no_grid(self):
        data = pd.DataFrame(
            {
                "Category": ["CH"],
                "carbon_count": [12],
                "DBE": [5.0],
                "area_level": pd.Categorical(["Level 2"]),
            }
        )
        fig, ax = plt.subplots()
        _draw_compound_bubble(ax, data)
        self.assertEqual(ax.get_facecolor(), mcolors.to_rgba("white"))
        self.assertEqual(fig.get_facecolor(), mcolors.to_rgba("white"))
        self.assertFalse(any(line.get_visible() for line in ax.get_xgridlines()))
        self.assertFalse(any(line.get_visible() for line in ax.get_ygridlines()))
        plt.close(fig)

    def test_formula_parser_calculates_ratios_and_dbe(self):
        result = calculate_ratios_and_dbe(pd.Series(["C16 H22 O4", "C6H6Cl2N"]))

        first = result.iloc[0]
        self.assertEqual(first["C_count"], 16)
        self.assertEqual(first["H_count"], 22)
        self.assertEqual(first["O_count"], 4)
        self.assertAlmostEqual(first["H.C"], 22 / 16)
        self.assertAlmostEqual(first["O.C"], 4 / 16)
        self.assertAlmostEqual(first["DBE"], 6.0)

        second = result.iloc[1]
        self.assertEqual(second["Cl_count"], 2)
        self.assertEqual(second["N_count"], 1)
        self.assertAlmostEqual(second["DBE"], 3.5)

    def test_classification_matches_r_template_groups(self):
        formulas = pd.Series([
            "C10 H22",
            "C10 H20 O2",
            "C10 H19 N O",
            "C10 H19 F O",
            "C10 H19 S",
        ])
        result = classify_compounds(calculate_ratios_and_dbe(formulas))

        counts = result["Category"].value_counts().to_dict()
        self.assertEqual(counts["CH"], 1)
        self.assertEqual(counts["CHO"], 1)
        self.assertEqual(counts["CHON_Group"], 1)
        self.assertEqual(counts["CHOX_Group"], 1)
        self.assertEqual(counts["ELSE"], 1)

    def test_sample_peak_area_long_uses_configured_sample_columns(self):
        raw = pd.DataFrame({
            "Name": ["A", "B"],
            "HH_alk": [100.0, 0.0],
            "WH_alk": [10000.0, 10.0],
        })

        long_df = build_sample_peak_area_long(raw, compound_col="Name", sample_cols=["HH_alk", "WH_alk"])

        self.assertEqual(len(long_df), 4)
        self.assertEqual(long_df["sample_id"].tolist(), ["HH_alk", "HH_alk", "WH_alk", "WH_alk"])
        self.assertAlmostEqual(long_df.loc[0, "ir_value"], 2.0)
        self.assertTrue(pd.isna(long_df.loc[1, "ir_value"]))
        self.assertAlmostEqual(long_df.loc[2, "ir_value"], 4.0)

    def test_boxplot_category_join_does_not_expand_duplicate_formulas(self):
        sample_long = pd.DataFrame({
            "compound": ["A", "B"],
            "formula": ["C10 H22", "C10 H22"],
            "sample_id": ["HH_alk", "HH_alk"],
            "Peak_area": [100.0, 1000.0],
            "ir_value": [2.0, 3.0],
            "log_concentration": [2.0, 3.0],
        })
        categories = pd.DataFrame({
            "Formula": ["C10 H22", "C10 H22"],
            "Category": ["CH", "CH"],
        })

        captured = {}
        original_summary = plots._boxplot_summary

        def capture_summary(plot_df):
            captured["rows"] = len(plot_df)
            return original_summary(plot_df)

        plots._boxplot_summary = capture_summary
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                plots.save_boxplot_log_transformed(sample_long, categories, Path(tmpdir))
        finally:
            plots._boxplot_summary = original_summary

        self.assertEqual(captured["rows"], 2)

    def test_category_donut_labels_show_uniform_percent_labels_and_smaller_center_text(self):
        summary = pd.DataFrame({
            "Category": ["CH", "CHO", "CHON_Group", "CHOX_Group"],
            "Count": [33, 54, 5, 2],
            "Percentage": [35.1, 57.4, 5.3, 2.1],
            "label": ["33\n(35.1%)", "54\n(57.4%)", "5\n(5.3%)", "2\n(2.1%)"],
        })

        fig, ax = plt.subplots(figsize=(10, 8))
        try:
            plots._draw_category_donut(ax, summary)
            percentage_labels = [text for text in ax.texts if "%" in text.get_text()]
            center_label = next(text for text in ax.texts if "Total number" in text.get_text())
            legend_title = ax.get_legend().get_title().get_text()
        finally:
            plt.close(fig)

        self.assertEqual([text.get_text() for text in percentage_labels], ["35.1%", "57.4%", "5.3%", "2.1%"])
        self.assertTrue(all("\n" not in text.get_text() and "(" not in text.get_text() for text in percentage_labels))
        self.assertEqual({text.get_fontsize() for text in percentage_labels}, {10.0})
        self.assertEqual(center_label.get_fontsize(), 18.0)
        self.assertEqual(ax.get_title(), "Compound Category Percentage Distribution")
        self.assertEqual(legend_title, "Compound Category")


class RScreeningReplicaIntegrationTests(unittest.TestCase):
    def test_pipeline_writes_expected_outputs_and_figures_for_small_workbook(self):
        raw = pd.DataFrame({
            "Name": ["A", "B", "C", "D"],
            "formula": ["C10 H22", "C10 H20 O2", "C10 H19 N O", "C10 H19 F O"],
            "Group_Area": [2e5, 2e6, 2e7, 2e8],
            "HH_alk": [1e5, 2e5, 3e5, 4e5],
            "WH_alk": [2e5, 3e5, 4e5, 5e5],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            input_path = tmp_path / "input.xlsx"
            raw.to_excel(input_path, index=False)

            result = run_screening_pipeline(
                input_path,
                ScreeningConfig(
                    output_dir=tmp_path / "out",
                    sample_cols=["HH_alk", "WH_alk"],
                ),
            )

            self.assertEqual(len(result.raw_data), 4)
            self.assertEqual(len(result.sample_peak_area_long), 8)
            self.assertTrue((tmp_path / "out" / "input_check_report.xlsx").exists())
            self.assertTrue((tmp_path / "out" / "elemental_ratios_with_DBE.xlsx").exists())
            self.assertTrue((tmp_path / "out" / "DBE.xlsx").exists())
            self.assertTrue((tmp_path / "out" / "sample_peak_area_long.xlsx").exists())

            for name in [
                "category_percent_donut_with_total",
                "compound_bubble_plot",
                "VanKrevelen",
                "boxplot_log_transformed",
            ]:
                png = tmp_path / "out" / "figures" / f"{name}.png"
                pdf = tmp_path / "out" / "figures" / f"{name}.pdf"
                self.assertGreater(png.stat().st_size, 1000)
                self.assertGreater(pdf.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
