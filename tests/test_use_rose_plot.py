import unittest
import warnings

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

from src.echa_use import classify_use_cn
from src.use_rose_plot import (
    build_compound_universe,
    build_epa_echa_combined_rose_data,
    extract_source_origin_pie_data,
    extract_reported_functional_use_presence_data,
    extract_candidate_use_plot_data,
    extract_top_reported_functional_use_data,
    extract_top_predicted_functional_use_data,
    figure_to_pdf_bytes,
    figure_to_png_bytes,
    generate_combined_use_rose_plot,
    generate_compound_classification_pie_plot,
    generate_reported_functional_use_presence_plot,
    generate_reported_functional_use_pie_plot,
    generate_top_predicted_functional_use_lollipop_plot,
    generate_top_predicted_functional_use_pie_plot,
    generate_use_bar_plot,
    generate_use_rose_plot,
)


class UseRosePlotTests(unittest.TestCase):
    def test_reported_classification_uses_unique_top_tie_and_missing(self):
        universe = build_compound_universe(
            pd.DataFrame({"compound": ["A", "B", "C", "A"]})
        )
        candidates = pd.DataFrame(
            [
                {"compound": "A", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Solvent", "evidence_count": 3},
                {"compound": "A", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Catalyst", "evidence_count": 1},
                {"compound": "B", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Solvent", "evidence_count": 2},
                {"compound": "B", "source_type": "functional_use", "functional_use_source": "reported", "raw_use": "Catalyst", "evidence_count": 2},
            ]
        )

        result = extract_top_reported_functional_use_data(
            candidates,
            universe,
            source_label="EPA FC reported",
            source_type="functional_use",
            use_key="raw",
            require_reported_flag=True,
        ).set_index("compound")

        self.assertEqual(result.loc["A", "display_label"], "Solvent")
        self.assertEqual(result.loc["A", "classification_reason"], "unique_top_reported_category")
        self.assertEqual(result.loc["B", "display_label"], "Others")
        self.assertEqual(result.loc["B", "classification_reason"], "tie_for_top_reported_category")
        self.assertEqual(result.loc["C", "display_label"], "Others")
        self.assertEqual(result.loc["C", "classification_reason"], "no_reported_result")
        self.assertEqual(len(result), 3)

    def test_echa_reported_uses_the_same_unique_top_rule(self):
        universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B"]}))
        candidates = pd.DataFrame(
            [
                {"compound": "A", "use_en": "Industrial use", "use_cn": "Industrial use", "evidence_count": 2},
                {"compound": "A", "use_en": "Consumer use", "use_cn": "Consumer use", "evidence_count": 1},
                {"compound": "B", "use_en": "Industrial use", "use_cn": "Industrial use", "evidence_count": 1},
                {"compound": "B", "use_en": "Consumer use", "use_cn": "Consumer use", "evidence_count": 1},
            ]
        )

        result = extract_top_reported_functional_use_data(
            candidates,
            universe,
            source_label="ECHA reported",
            source_type=None,
            use_key="category",
            require_reported_flag=False,
        ).set_index("compound")

        self.assertEqual(result.loc["A", "display_label"], "Industrial use")
        self.assertEqual(result.loc["B", "display_label"], "Others")

    def test_echa_reported_aggregates_distinct_evidence_by_true_category(self):
        universe = build_compound_universe(pd.DataFrame({"compound": ["A"]}))
        candidates = pd.DataFrame(
            [
                {
                    "compound": "A",
                    "use_en": "Industrial manufacture",
                    "use_cn": "Industrial category",
                    "evidence_count": 2,
                },
                {
                    "compound": "A",
                    "use_en": "Industrial processing",
                    "use_cn": "Industrial category",
                    "evidence_count": 3,
                },
                {
                    "compound": "A",
                    "use_en": "Consumer use",
                    "use_cn": "Consumer category",
                    "evidence_count": 4,
                },
            ]
        )

        result = extract_top_reported_functional_use_data(
            candidates,
            universe,
            source_label="ECHA reported",
            source_type=None,
            use_key="category",
            require_reported_flag=False,
        ).iloc[0]

        self.assertEqual(result["display_label"], "Industrial category")
        self.assertEqual(result["evidence_count"], 5)
        self.assertEqual(result["classification_reason"], "unique_top_reported_category")

    def test_real_echa_chinese_categories_keep_distinct_stable_english_labels(self):
        industrial_category = classify_use_cn("industrial use")
        consumer_category = classify_use_cn("consumer use")
        universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B"]}))
        candidates = pd.DataFrame(
            [
                {
                    "compound": "A",
                    "use_en": "Industrial use at industrial sites",
                    "use_cn": industrial_category,
                    "evidence_count": 2,
                },
                {
                    "compound": "A",
                    "use_en": "Use at industrial sites",
                    "use_cn": industrial_category,
                    "evidence_count": 3,
                },
                {
                    "compound": "A",
                    "use_en": "Consumer use in household products",
                    "use_cn": consumer_category,
                    "evidence_count": 4,
                },
                {
                    "compound": "B",
                    "use_en": "Consumer use by the general public",
                    "use_cn": consumer_category,
                    "evidence_count": 1,
                },
            ]
        )

        result = extract_top_reported_functional_use_data(
            candidates,
            universe,
            source_label="ECHA reported",
            source_type=None,
            use_key="category",
            require_reported_flag=False,
        ).set_index("compound")

        self.assertEqual(result.loc["A", "evidence_count"], 5)
        self.assertEqual(result.loc["A", "display_label"], "Industrial use")
        self.assertEqual(result.loc["B", "display_label"], "Consumer use")
        self.assertNotEqual(
            result.loc["A", "display_label"], result.loc["B", "display_label"]
        )

        figure = generate_reported_functional_use_pie_plot(
            result.reset_index(), "ECHA reported"
        )
        try:
            legend_labels = {
                text.get_text()
                for legend in figure.legends
                for text in legend.get_texts()
            }
            self.assertEqual(len(figure.axes[0].patches), 2)
            self.assertEqual(
                legend_labels,
                {"Consumer use (1, 50.0%)", "Industrial use (1, 50.0%)"},
            )
        finally:
            plt.close(figure)

    def test_reported_classification_fails_closed_when_source_type_is_missing(self):
        universe = build_compound_universe(pd.DataFrame({"compound": ["A"]}))
        candidates = pd.DataFrame(
            [
                {
                    "compound": "A",
                    "raw_use": "Solvent",
                    "functional_use_source": "reported",
                    "evidence_count": 3,
                }
            ]
        )

        result = extract_top_reported_functional_use_data(
            candidates,
            universe,
            source_label="EPA FC reported",
            source_type="functional_use",
            use_key="raw",
            require_reported_flag=True,
        ).iloc[0]

        self.assertEqual(result["display_label"], "Others")
        self.assertEqual(result["classification_reason"], "no_reported_result")

    def test_literal_none_is_valid_only_as_a_compound_identifier(self):
        universe = build_compound_universe(pd.DataFrame({"compound": ["None"]}))
        candidates = pd.DataFrame(
            [
                {
                    "compound": "None",
                    "source_type": "functional_use",
                    "functional_use_source": "reported",
                    "raw_use": "None",
                    "evidence_count": 2,
                }
            ]
        )

        result = extract_top_reported_functional_use_data(
            candidates,
            universe,
            source_label="EPA FC reported",
            source_type="functional_use",
            use_key="raw",
            require_reported_flag=True,
        ).iloc[0]

        self.assertEqual(len(universe), 1)
        self.assertEqual(universe.loc[0, "compound_key"], "none")
        self.assertEqual(result["display_label"], "Others")
        self.assertEqual(result["classification_reason"], "no_reported_result")

    def test_predicted_fills_missing_universe_compound_as_others(self):
        universe = build_compound_universe(pd.DataFrame({"compound": ["A", "B"]}))
        candidates = pd.DataFrame(
            [
                {
                    "compound": "A",
                    "source_type": "functional_use",
                    "functional_use_source": "predicted",
                    "raw_use": "Solvent",
                    "probability": 0.91,
                }
            ]
        )

        result = extract_top_predicted_functional_use_data(
            candidates, compound_universe=universe
        ).set_index("compound")

        self.assertEqual(result.loc["A", "display_label"], "Solvent")
        self.assertEqual(result.loc["B", "display_label"], "Others")
        self.assertEqual(result.loc["B", "classification_reason"], "no_predicted_result")
        self.assertEqual(len(result), 2)

    def test_source_origin_maps_all_four_fixed_categories(self):
        universe = build_compound_universe(
            pd.DataFrame({"compound": ["Both", "Human", "Natural", "None"]})
        )
        summary = pd.DataFrame(
            [
                {"compound": "Both", "人为源证据数": 2, "天然源证据数": 1},
                {"compound": "Human", "人为源证据数": 1, "天然源证据数": 0},
                {"compound": "Natural", "人为源证据数": 0, "天然源证据数": 3},
            ]
        )

        result = extract_source_origin_pie_data(summary, universe)

        self.assertEqual(
            result.set_index("compound")["display_label"].to_dict(),
            {
                "Both": "Both",
                "Human": "Anthropogenic",
                "Natural": "Natural",
                "None": "Unknown",
            },
        )

    def test_reported_pie_uses_tiered_labels_footnote_and_keeps_rare_categories(self):
        rows = []
        for category, count in [("Major", 950), ("Medium", 40), ("Rare", 9), ("Tiny", 1)]:
            for index in range(count):
                compound = f"{category}-{index}"
                rows.append(
                    {
                        "compound_key": compound.lower(),
                        "compound": compound,
                        "display_label": category,
                    }
                )
        plot_df = pd.DataFrame(rows)

        figure = generate_reported_functional_use_pie_plot(plot_df, "Reported")
        try:
            axis_text = {text.get_text() for text in figure.axes[0].texts}
            figure_text = {text.get_text() for text in figure.texts}
            annotations = [
                item
                for item in figure.axes[0].texts
                if isinstance(item, matplotlib.text.Annotation)
            ]
            legend_labels = {
                text.get_text()
                for legend in figure.legends
                for text in legend.get_texts()
            }

            self.assertIn("95.0%", axis_text)
            self.assertIn("4.0%", {item.get_text() for item in annotations})
            self.assertNotIn("0.9%", axis_text)
            self.assertNotIn("0.1%", axis_text)
            self.assertTrue(any("Rare (9, 0.9%)" == label for label in legend_labels))
            self.assertTrue(any("Tiny (1, 0.1%)" == label for label in legend_labels))
            self.assertFalse(any(label.startswith("Others (") for label in legend_labels))
            self.assertIn(
                "Others includes compounds with no reported result or with a tie for the most frequently reported category.",
                figure_text,
            )
            self.assertTrue(
                all(
                    text.get_fontfamily()[0] == "Times New Roman"
                    for text in figure.findobj(matplotlib.text.Text)
                    if text.get_text().strip()
                )
            )
        finally:
            plt.close(figure)

    def test_compound_classification_pie_assigns_duplicate_compound_to_first_category(self):
        plot_df = pd.DataFrame(
            [
                {"compound_key": "compound-a", "display_label": "Alpha"},
                {"compound_key": "compound-a", "display_label": "Beta"},
                {"compound_key": "compound-b", "display_label": "Beta"},
            ]
        )

        figure = generate_compound_classification_pie_plot(plot_df, "Classification")
        try:
            axis_text = {text.get_text() for text in figure.axes[0].texts}
            legend_labels = {
                text.get_text()
                for legend in figure.legends
                for text in legend.get_texts()
            }

            self.assertIn("Total compounds\n2", axis_text)
            self.assertEqual(legend_labels, {"Alpha (1, 50.0%)", "Beta (1, 50.0%)"})
        finally:
            plt.close(figure)

    def test_candidate_detail_data_keeps_all_uses_without_top_limit(self):
        candidates_df = pd.DataFrame(
            [
                {
                    "compound": "Example",
                    "source_type": "product_category",
                    "raw_use": f"Scenario {index}",
                    "use_cn": "产品场景",
                    "evidence_count": index,
                }
                for index in range(1, 7)
            ]
        )

        plot_df = extract_candidate_use_plot_data(
            candidates_df,
            source_label="EPA PUC",
            source_type="product_category",
            use_key="raw",
        )

        self.assertEqual(len(plot_df), 6)
        self.assertEqual(set(plot_df["use_label"]), {f"Scenario {index}" for index in range(1, 7)})
        self.assertAlmostEqual(plot_df["angle_fraction"].sum(), 1.0)

    def test_functional_detail_data_filters_to_predicted_without_reported_duplicate(self):
        candidates_df = pd.DataFrame(
            [
                {
                    "compound": "Example",
                    "source_type": "functional_use",
                    "raw_use": "Flame retardant",
                    "use_cn": "阻燃剂",
                    "evidence_count": 1,
                    "functional_use_source": "reported",
                },
                {
                    "compound": "Example",
                    "source_type": "functional_use",
                    "raw_use": "flame_retardant",
                    "use_cn": "阻燃剂",
                    "evidence_count": 0.8873,
                    "probability": 0.8873,
                    "functional_use_source": "predicted",
                },
            ]
        )

        plot_df = extract_candidate_use_plot_data(
            candidates_df,
            source_label="EPA FC",
            source_type="functional_use",
            functional_source="predicted",
            use_key="raw",
        )

        self.assertEqual(plot_df["use_label"].tolist(), ["flame_retardant"])
        self.assertNotIn("Flame retardant", plot_df["use_label"].tolist())

    def test_combined_data_uses_epa_product_scenarios_and_echa_candidate_details(self):
        comptox_candidates = pd.DataFrame(
            [
                {
                    "compound": "Benzyl chloride",
                    "source_type": "product_category",
                    "raw_use": "Raw materials:coatings",
                    "use_cn": "工业用品",
                    "evidence_count": 4,
                },
                {
                    "compound": "Benzyl chloride",
                    "source_type": "functional_use",
                    "raw_use": "fragrance",
                    "use_cn": "芳香剂",
                    "evidence_count": 0.8,
                    "functional_use_source": "predicted",
                },
            ]
        )
        echa_candidates = pd.DataFrame(
            [
                {
                    "compound": "Benzyl chloride",
                    "raw_use": "Industrial use",
                    "use_cn": "工业用途",
                    "use_en": "Industrial use",
                    "evidence_count": 2,
                }
            ]
        )

        combined = build_epa_echa_combined_rose_data(comptox_candidates, echa_candidates)

        self.assertEqual(
            combined[combined["source"].eq("EPA")]["use_label"].tolist(),
            ["Raw materials:coatings"],
        )
        self.assertNotIn("fragrance", combined["use_label"].tolist())
        self.assertEqual(
            combined[combined["source"].eq("ECHA")]["use_label"].tolist(),
            ["Industrial use"],
        )

    def test_combined_plot_uses_two_independent_semicircles(self):
        epa = extract_candidate_use_plot_data(
            pd.DataFrame(
                [
                    {
                        "compound": "Ethanol",
                        "source_type": "product_category",
                        "raw_use": "Solvent",
                        "use_cn": "溶剂",
                        "evidence_count": 3,
                    }
                ]
            ),
            source_label="EPA",
            source_type="product_category",
            use_key="raw",
        )
        echa = extract_candidate_use_plot_data(
            pd.DataFrame(
                [
                    {
                        "compound": "Ethanol",
                        "raw_use": "Cleaning",
                        "use_cn": "Cleaning",
                        "evidence_count": 2,
                    }
                ]
            ),
            source_label="ECHA",
            use_key="category",
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
        plot_df = extract_candidate_use_plot_data(
            pd.DataFrame(
                [
                    {
                        "compound": "Ethanol",
                        "source_type": "product_category",
                        "raw_use": "Solvent",
                        "use_cn": "溶剂",
                        "evidence_count": 1,
                    }
                ]
            ),
            source_label="EPA",
            source_type="product_category",
            use_key="raw",
        )
        fig = generate_combined_use_rose_plot(plot_df, "Combined")
        try:
            self.assertIn("No ECHA data", [text.get_text() for text in fig.axes[0].texts])
        finally:
            plt.close(fig)

    def test_plot_exports_with_ascii_only_text(self):
        plot_df = extract_candidate_use_plot_data(
            pd.DataFrame(
                [
                    {
                        "compound": "中文化合物",
                        "source_type": "product_category",
                        "raw_use": "Deodorizer",
                        "use_cn": "除臭剂",
                        "evidence_count": 1,
                    }
                ]
            ),
            source_label="EPA",
            source_type="product_category",
            use_key="raw",
        )
        fig = None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fig = generate_use_rose_plot(plot_df, "EPA CompTox 用途风玫瑰图")
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

    def test_functional_use_bar_plot_exports_with_ascii_only_text(self):
        plot_df = extract_candidate_use_plot_data(
            pd.DataFrame(
                [
                    {
                        "compound": "中文化合物",
                        "source_type": "functional_use",
                        "raw_use": "fragrance",
                        "use_cn": "芳香剂",
                        "evidence_count": 0.91,
                        "probability": 0.91,
                        "functional_use_source": "predicted",
                    },
                    {
                        "compound": "中文化合物",
                        "source_type": "functional_use",
                        "raw_use": "antioxidant",
                        "use_cn": "抗氧化剂",
                        "evidence_count": 0.37,
                        "probability": 0.37,
                        "functional_use_source": "predicted",
                    },
                ]
            ),
            source_label="EPA FC",
            source_type="functional_use",
            functional_source="predicted",
            use_key="raw",
        )
        fig = None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fig = generate_use_bar_plot(plot_df, "EPA CompTox Functional Use Bar Plot")
                png_bytes = figure_to_png_bytes(fig)
                pdf_bytes = figure_to_pdf_bytes(fig)

            self.assertNotEqual(fig.axes[0].name, "polar")
            self.assertEqual(len(fig.axes[0].patches), 2)
            plot_text = [fig.axes[0].get_title(), fig.axes[0].get_xlabel()]
            plot_text.extend(text.get_text() for text in fig.texts)
            plot_text.extend(text.get_text() for text in fig.axes[0].get_yticklabels())
            self.assertTrue(all(text.isascii() for text in plot_text))
            self.assertGreater(len(png_bytes.getvalue()), 1_000)
            self.assertGreater(len(pdf_bytes.getvalue()), 1_000)
        finally:
            if fig is not None:
                plt.close(fig)

    def test_top_predicted_functional_use_data_keeps_one_highest_probability_per_compound(self):
        candidates_df = pd.DataFrame(
            [
                {
                    "compound": "Compound A",
                    "source_type": "functional_use",
                    "raw_use": "fragrance",
                    "use_cn": "芳香剂",
                    "evidence_count": 0.81,
                    "probability": 0.81,
                    "functional_use_source": "predicted",
                },
                {
                    "compound": "Compound A",
                    "source_type": "functional_use",
                    "raw_use": "antioxidant",
                    "use_cn": "抗氧化剂",
                    "evidence_count": 0.37,
                    "probability": 0.37,
                    "functional_use_source": "predicted",
                },
                {
                    "compound": "Compound A",
                    "source_type": "functional_use",
                    "raw_use": "fragrance",
                    "use_cn": "芳香剂",
                    "evidence_count": 1,
                    "functional_use_source": "reported",
                },
                {
                    "compound": "Compound B",
                    "source_type": "functional_use",
                    "raw_use": "solvent",
                    "use_cn": "溶剂",
                    "evidence_count": 0.52,
                    "probability": 0.52,
                    "functional_use_source": "predicted",
                },
            ]
        )

        plot_df = extract_top_predicted_functional_use_data(candidates_df)

        self.assertEqual(plot_df["compound"].tolist(), ["Compound A", "Compound B"])
        self.assertEqual(plot_df["use_label"].tolist(), ["fragrance", "solvent"])
        self.assertEqual(plot_df["status"].tolist(), ["reported", "predicted"])
        self.assertEqual(plot_df["display_label"].tolist(), ["fragrance", "solvent"])
        self.assertAlmostEqual(plot_df.loc[0, "probability"], 0.81)

    def test_functional_use_specialized_extractors_return_typed_empty_frames(self):
        self.assertEqual(
            extract_top_predicted_functional_use_data(pd.DataFrame()).columns.tolist(),
            [
                "source",
                "compound_key",
                "compound",
                "compound_label",
                "use_cn",
                "use_label",
                "display_label",
                "probability",
                "status",
                "classification_reason",
                "is_other",
            ],
        )
        self.assertEqual(
            extract_reported_functional_use_presence_data(pd.DataFrame()).columns.tolist(),
            [
                "source",
                "compound",
                "compound_label",
                "use_cn",
                "use_label",
                "presence",
            ],
        )

    def test_reported_functional_use_presence_data_is_binary_and_deduplicated(self):
        candidates_df = pd.DataFrame(
            [
                {
                    "compound": "Compound A",
                    "source_type": "functional_use",
                    "raw_use": "Intermediate",
                    "reported_use": "intermediate",
                    "harmonized_use": "Intermediate",
                    "use_cn": "中间体",
                    "evidence_count": 1,
                    "functional_use_source": "reported",
                },
                {
                    "compound": "Compound A",
                    "source_type": "functional_use",
                    "raw_use": "Intermediate",
                    "reported_use": "intermediates",
                    "harmonized_use": "Intermediate",
                    "use_cn": "中间体",
                    "evidence_count": 1,
                    "functional_use_source": "reported",
                },
                {
                    "compound": "Compound A",
                    "source_type": "functional_use",
                    "raw_use": "fragrance",
                    "use_cn": "芳香剂",
                    "evidence_count": 0.81,
                    "probability": 0.81,
                    "functional_use_source": "predicted",
                },
            ]
        )

        plot_df = extract_reported_functional_use_presence_data(candidates_df)

        self.assertEqual(len(plot_df), 1)
        self.assertEqual(plot_df.loc[0, "compound"], "Compound A")
        self.assertEqual(plot_df.loc[0, "use_label"], "Intermediate")
        self.assertEqual(plot_df.loc[0, "presence"], 1)

    def test_echa_reported_presence_uses_category_rows_without_source_flags(self):
        candidates_df = pd.DataFrame(
            [
                {
                    "compound": "Compound A",
                    "use_en": "Industrial use",
                    "use_cn": "Industrial use",
                },
                {
                    "compound": "Compound A",
                    "use_en": "Industrial use",
                    "use_cn": "Industrial use",
                },
            ]
        )

        plot_df = extract_reported_functional_use_presence_data(
            candidates_df,
            source_label="ECHA",
            source_type=None,
            use_key="category",
            require_reported_flag=False,
        )

        self.assertEqual(len(plot_df), 1)
        self.assertEqual(plot_df.loc[0, "use_label"], "Industrial use")
        self.assertEqual(plot_df.loc[0, "presence"], 1)

    def test_top_predicted_pie_plot_exports_with_ascii_only_text(self):
        plot_df = pd.DataFrame(
            [
                {
                    "compound": "中文化合物",
                    "compound_label": "Compound 1",
                    "use_cn": "芳香剂",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.81,
                    "status": "reported",
                },
                {
                    "compound": "Compound 2",
                    "compound_label": "Compound 2",
                    "use_cn": "催化剂",
                    "use_label": "catalyst",
                    "display_label": "catalyst",
                    "probability": 0.73,
                    "status": "predicted",
                },
            ]
        )
        fig = None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fig = generate_top_predicted_functional_use_pie_plot(
                    plot_df,
                    "EPA CompTox Top Predicted Functional Use Distribution",
                )
                png_bytes = figure_to_png_bytes(fig)
                pdf_bytes = figure_to_pdf_bytes(fig)

            self.assertNotEqual(fig.axes[0].name, "polar")
            self.assertGreaterEqual(len(fig.axes[0].patches), 2)
            plot_text = [fig.axes[0].get_title()]
            plot_text.extend(text.get_text() for text in fig.texts)
            plot_text.extend(text.get_text() for text in fig.axes[0].texts)
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

    def test_top_predicted_pie_plot_aggregates_by_functional_group(self):
        plot_df = pd.DataFrame(
            [
                {
                    "compound": "Compound A",
                    "compound_label": "Compound A",
                    "use_cn": "芳香剂",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.91,
                    "status": "reported",
                },
                {
                    "compound": "Compound B",
                    "compound_label": "Compound B",
                    "use_cn": "芳香剂",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.82,
                    "status": "predicted",
                },
                {
                    "compound": "Compound C",
                    "compound_label": "Compound C",
                    "use_cn": "催化剂",
                    "use_label": "catalyst",
                    "display_label": "catalyst",
                    "probability": 0.73,
                    "status": "predicted",
                },
            ]
        )
        fig = None

        try:
            fig = generate_top_predicted_functional_use_pie_plot(plot_df, "Top Predicted")
            legend_labels = [
                text.get_text()
                for legend in fig.legends
                for text in legend.get_texts()
            ]
            center_labels = [text.get_text() for text in fig.axes[0].texts]
            horizontal_lines = [
                line
                for line in fig.axes[0].lines
                if len(line.get_ydata()) == 2 and line.get_ydata()[0] == line.get_ydata()[1]
            ]

            self.assertEqual(len(fig.axes[0].patches), 2)
            self.assertTrue(any("fragrance" in label and "66.7%" in label for label in legend_labels))
            self.assertTrue(any("catalyst" in label and "33.3%" in label for label in legend_labels))
            self.assertIn("Total compounds\n3", center_labels)
            self.assertEqual(horizontal_lines, [])
        finally:
            if fig is not None:
                plt.close(fig)

    def test_top_predicted_lollipop_plot_exports_with_ascii_only_text(self):
        plot_df = pd.DataFrame(
            [
                {
                    "compound": "中文化合物",
                    "compound_label": "Compound 1",
                    "use_cn": "芳香剂",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.81,
                    "status": "reported",
                }
            ]
        )
        fig = None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fig = generate_top_predicted_functional_use_lollipop_plot(
                    plot_df,
                    "EPA CompTox Top Predicted Functional Use",
                )
                png_bytes = figure_to_png_bytes(fig)
                pdf_bytes = figure_to_pdf_bytes(fig)

            self.assertNotEqual(fig.axes[0].name, "polar")
            lollipop_lines = [
                line
                for line in fig.axes[0].lines
                if len(line.get_ydata()) == 2 and line.get_ydata()[0] == line.get_ydata()[1]
            ]
            self.assertEqual(len(lollipop_lines), 1)
            plot_text = [fig.axes[0].get_title(), fig.axes[0].get_xlabel()]
            plot_text.extend(text.get_text() for text in fig.texts)
            plot_text.extend(text.get_text() for text in fig.axes[0].get_yticklabels())
            plot_text.extend(text.get_text() for text in fig.axes[0].texts)
            self.assertTrue(all(text.isascii() for text in plot_text))
            self.assertGreater(len(png_bytes.getvalue()), 1_000)
            self.assertGreater(len(pdf_bytes.getvalue()), 1_000)
        finally:
            if fig is not None:
                plt.close(fig)

    def test_top_predicted_lollipop_plot_marks_high_confidence_threshold(self):
        plot_df = pd.DataFrame(
            [
                {
                    "compound": "Compound A",
                    "compound_label": "Compound A",
                    "use_cn": "fragrance",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.91,
                    "status": "predicted",
                },
                {
                    "compound": "Compound B",
                    "compound_label": "Compound B",
                    "use_cn": "catalyst",
                    "use_label": "catalyst",
                    "display_label": "catalyst",
                    "probability": 0.73,
                    "status": "predicted",
                },
            ]
        )
        fig = None

        try:
            fig = generate_top_predicted_functional_use_lollipop_plot(plot_df, "Top Predicted")
            threshold_lines = [
                line
                for line in fig.axes[0].lines
                if len(line.get_xdata()) == 2
                and all(abs(float(value) - 0.8) < 1e-9 for value in line.get_xdata())
                and line.get_linestyle() == "--"
            ]
            threshold_spans = [
                patch
                for patch in fig.axes[0].patches
                if hasattr(patch, "get_x")
                and hasattr(patch, "get_width")
                and abs(float(patch.get_x()) - 0.8) < 1e-9
                and abs(float(patch.get_x() + patch.get_width()) - 1.0) < 1e-9
            ]
            text_labels = [text.get_text() for text in fig.axes[0].texts]

            self.assertEqual(len(threshold_lines), 1)
            self.assertEqual(len(threshold_spans), 1)
            self.assertIn("High confidence >= 0.8", text_labels)
            self.assertGreaterEqual(fig.axes[0].get_ylim()[1], len(plot_df) + 0.5)
        finally:
            if fig is not None:
                plt.close(fig)

    def test_top_predicted_lollipop_plot_uses_same_color_for_same_functional_group(self):
        plot_df = pd.DataFrame(
            [
                {
                    "compound": "Compound A",
                    "compound_label": "Compound A",
                    "use_cn": "芳香剂",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.91,
                    "status": "reported",
                },
                {
                    "compound": "Compound B",
                    "compound_label": "Compound B",
                    "use_cn": "芳香剂",
                    "use_label": "fragrance",
                    "display_label": "fragrance",
                    "probability": 0.82,
                    "status": "predicted",
                },
                {
                    "compound": "Compound C",
                    "compound_label": "Compound C",
                    "use_cn": "催化剂",
                    "use_label": "catalyst",
                    "display_label": "catalyst",
                    "probability": 0.73,
                    "status": "predicted",
                },
            ]
        )
        fig = None

        try:
            fig = generate_top_predicted_functional_use_lollipop_plot(plot_df, "Top Predicted")
            colors = fig.axes[0].collections[0].get_facecolors()
            self.assertTrue((colors[1] == colors[2]).all())
            self.assertFalse((colors[0] == colors[1]).all())
        finally:
            if fig is not None:
                plt.close(fig)

    def test_reported_presence_plot_exports_with_ascii_only_text(self):
        plot_df = pd.DataFrame(
            [
                {
                    "compound": "中文化合物",
                    "compound_label": "Compound 1",
                    "use_cn": "中间体",
                    "use_label": "Intermediate",
                    "presence": 1,
                }
            ]
        )
        fig = None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fig = generate_reported_functional_use_presence_plot(
                    plot_df,
                    "EPA CompTox Reported Functional Use Evidence",
                )
                png_bytes = figure_to_png_bytes(fig)
                pdf_bytes = figure_to_pdf_bytes(fig)

            self.assertNotEqual(fig.axes[0].name, "polar")
            self.assertEqual(len(fig.axes[0].collections), 1)
            plot_text = [fig.axes[0].get_title(), fig.axes[0].get_xlabel(), fig.axes[0].get_ylabel()]
            plot_text.extend(text.get_text() for text in fig.texts)
            plot_text.extend(text.get_text() for text in fig.axes[0].get_xticklabels())
            plot_text.extend(text.get_text() for text in fig.axes[0].get_yticklabels())
            self.assertTrue(all(text.isascii() for text in plot_text))
            self.assertGreater(len(png_bytes.getvalue()), 1_000)
            self.assertGreater(len(pdf_bytes.getvalue()), 1_000)
        finally:
            if fig is not None:
                plt.close(fig)


if __name__ == "__main__":
    unittest.main()
