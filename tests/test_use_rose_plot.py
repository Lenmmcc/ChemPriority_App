import unittest
import warnings

import matplotlib.pyplot as plt
import pandas as pd

from src.use_rose_plot import (
    build_epa_echa_combined_rose_data,
    extract_use_rose_data,
    figure_to_pdf_bytes,
    figure_to_png_bytes,
    generate_combined_use_rose_plot,
    generate_use_rose_plot,
)


class UseRosePlotTests(unittest.TestCase):
    def test_combined_plot_uses_two_independent_semicircles(self):
        epa = extract_use_rose_data(
            pd.DataFrame([{"compound": "Ethanol", "用途1": "Solvent", "用途1_英文证据": "Solvent", "用途1_证据数量": 3}]),
            source_label="EPA",
        )
        echa = extract_use_rose_data(
            pd.DataFrame([{"compound": "Ethanol", "用途1": "Cleaning", "用途1_英文证据": "Cleaning", "用途1_证据数量": 2}]),
            source_label="ECHA",
        )
        fig = generate_combined_use_rose_plot(pd.concat([epa, echa]), "Combined")
        try:
            self.assertEqual(len(fig.axes[0].patches), 2)
            self.assertAlmostEqual(sum(patch.get_width() for patch in fig.axes[0].patches), 2 * 3.141592653589793)
            self.assertIn("EPA", [text.get_text() for text in fig.axes[0].texts])
            self.assertIn("ECHA", [text.get_text() for text in fig.axes[0].texts])
        finally:
            plt.close(fig)

    def test_combined_plot_marks_missing_source(self):
        rose_df = extract_use_rose_data(
            pd.DataFrame([{"compound": "Ethanol", "用途1": "Solvent", "用途1_英文证据": "Solvent", "用途1_证据数量": 1}]),
            source_label="EPA",
        )
        fig = generate_combined_use_rose_plot(rose_df, "Combined")
        try:
            self.assertIn("No ECHA data", [text.get_text() for text in fig.axes[0].texts])
        finally:
            plt.close(fig)

    def test_uses_english_evidence_labels(self):
        summary_df = pd.DataFrame(
            [
                {
                    "compound": "p-Cymene",
                    "用途1": "除臭剂 Deodorizer",
                    "用途1_英文证据": "Deodorizer",
                    "用途1_证据数量": 3,
                    "用途2": "实验室用品 Laboratory supplies",
                    "用途2_英文证据": "Laboratory supplies",
                    "用途2_证据数量": 1,
                }
            ]
        )

        rose_df = extract_use_rose_data(summary_df, source_label="EPA")

        self.assertEqual(rose_df["use_label"].tolist(), ["Deodorizer", "Laboratory supplies"])
        self.assertEqual(rose_df["compound_label"].tolist(), ["p-Cymene", "p-Cymene"])
        self.assertAlmostEqual(rose_df["angle_fraction"].sum(), 1.0)

    def test_extracts_source_specific_use_prefix_for_separate_epa_plots(self):
        summary_df = pd.DataFrame(
            [
                {
                    "compound": "Benzyl chloride",
                    "产品场景用途1": "个人护理用品 (Personal care products)",
                    "产品场景用途1_英文证据": "Personal care products",
                    "产品场景用途1_证据数量": 12,
                    "功能用途1": "芳香剂 (Fragrance)",
                    "功能用途1_英文证据": "Fragrance",
                    "功能用途1_证据数量": 0.91,
                }
            ]
        )

        product_rose_df = extract_use_rose_data(summary_df, source_label="EPA PUC", use_prefix="产品场景用途")
        functional_rose_df = extract_use_rose_data(summary_df, source_label="EPA FC", use_prefix="功能用途")

        self.assertEqual(product_rose_df["source"].tolist(), ["EPA PUC"])
        self.assertEqual(product_rose_df["use_label"].tolist(), ["Personal care products"])
        self.assertEqual(functional_rose_df["source"].tolist(), ["EPA FC"])
        self.assertEqual(functional_rose_df["use_label"].tolist(), ["Fragrance"])

    def test_combined_data_uses_epa_product_scenarios_not_legacy_comprehensive_uses(self):
        comptox_summary = pd.DataFrame(
            [
                {
                    "compound": "Benzyl chloride",
                    "用途1": "Solvent",
                    "用途1_英文证据": "Solvent",
                    "用途1_证据数量": 99,
                    "产品场景用途1": "Raw materials:coatings",
                    "产品场景用途1_英文证据": "Raw materials:coatings",
                    "产品场景用途1_证据数量": 4,
                    "功能用途1": "Fragrance",
                    "功能用途1_英文证据": "Fragrance",
                    "功能用途1_证据数量": 0.8,
                }
            ]
        )
        echa_summary = pd.DataFrame(
            [
                {
                    "compound": "Benzyl chloride",
                    "用途1": "Industrial use",
                    "用途1_英文证据": "Industrial use",
                    "用途1_证据数量": 2,
                }
            ]
        )

        combined = build_epa_echa_combined_rose_data(comptox_summary, echa_summary)

        self.assertEqual(
            combined[combined["source"].eq("EPA")]["use_label"].tolist(),
            ["Raw materials:coatings"],
        )
        self.assertNotIn("Solvent", combined["use_label"].tolist())
        self.assertNotIn("Fragrance", combined["use_label"].tolist())
        self.assertEqual(
            combined[combined["source"].eq("ECHA")]["use_label"].tolist(),
            ["Industrial use"],
        )

    def test_plot_exports_with_ascii_only_text(self):
        summary_df = pd.DataFrame(
            [
                {
                    "compound": "中文化合物",
                    "用途1": "除臭剂",
                    "用途1_英文证据": "Deodorizer",
                    "用途1_证据数量": 1,
                }
            ]
        )
        rose_df = extract_use_rose_data(summary_df, source_label="EPA")
        fig = None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fig = generate_use_rose_plot(rose_df, "EPA CompTox 用途风玫瑰图")
                png_bytes = figure_to_png_bytes(fig)
                pdf_bytes = figure_to_pdf_bytes(fig)

            plot_text = [fig.axes[0].get_title()]
            plot_text.extend(text.get_text() for text in fig.texts)
            plot_text.extend(
                text.get_text()
                for legend in fig.legends
                for text in legend.get_texts()
            )
            self.assertTrue(all(text.isascii() for text in plot_text))
            self.assertGreater(len(png_bytes.getvalue()), 1_000)
            self.assertGreater(len(pdf_bytes.getvalue()), 1_000)
        finally:
            if fig is not None:
                plt.close(fig)


if __name__ == "__main__":
    unittest.main()
