import io
import unittest
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.toxpi_calc import (
    generate_multi_toxpi_plot,
    generate_r_style_toxpi_plot,
    generate_toxpi_bar_plot,
    run_sensitivity_analysis,
)


class ToxPiPlotTextTests(unittest.TestCase):
    def setUp(self):
        self.toxic_cols = ["\u6025\u6027\u6bd2\u6027", "\u809d\u6bd2\u6027"]
        self.toxpi_agg = pd.DataFrame(
            {
                "compound": ["\u5316\u5408\u7269\u7532", "\u5316\u5408\u7269\u4e59"],
                "toxpi": [0.62, 0.31],
                f"norm_{self.toxic_cols[0]}": [0.8, 0.3],
                f"norm_{self.toxic_cols[1]}": [0.4, 0.2],
            }
        )
        self.toxpi_agg.attrs["toxic_cols"] = self.toxic_cols

    def test_result_figures_exclude_cjk_text(self):
        _, _, sensitivity_figure, _ = run_sensitivity_analysis(
            self.toxpi_agg,
            toxic_cols=self.toxic_cols,
            n_iter=20,
            seed=7,
        )
        figures = [
            generate_multi_toxpi_plot(self.toxpi_agg, toxic_cols=self.toxic_cols),
            generate_toxpi_bar_plot(self.toxpi_agg),
            sensitivity_figure,
        ]

        try:
            for figure in figures:
                self._assert_no_cjk_figure_text(figure)
                self.assert_times_new_roman(figure)
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    for image_format in ("png", "pdf"):
                        output = io.BytesIO()
                        figure.savefig(output, format=image_format)
                        self.assertGreater(len(output.getvalue()), 1_000)
        finally:
            for figure in figures:
                plt.close(figure)

    def _assert_no_cjk_figure_text(self, figure):
        figure.canvas.draw()
        text_values = [text.get_text() for text in figure.texts]

        for axis in figure.axes:
            text_values.extend([axis.get_title(), axis.get_xlabel(), axis.get_ylabel()])
            text_values.extend(text.get_text() for text in axis.texts)
            text_values.extend(text.get_text() for text in axis.get_xticklabels())
            text_values.extend(text.get_text() for text in axis.get_yticklabels())
            legend = axis.get_legend()
            if legend is not None:
                text_values.extend(text.get_text() for text in legend.get_texts())

        for legend in figure.legends:
            text_values.append(legend.get_title().get_text())
            text_values.extend(text.get_text() for text in legend.get_texts())

        self.assertFalse(
            any("\u4e00" <= char <= "\u9fff" for text in text_values for char in text),
            text_values,
        )

    def assert_times_new_roman(self, figure):
        texts = [
            text
            for text in figure.findobj(matplotlib.text.Text)
            if text.get_text().strip()
        ]
        self.assertTrue(texts)
        self.assertTrue(
            all(text.get_fontfamily()[0] == "Times New Roman" for text in texts)
        )

    def test_toxpi_page_configures_shared_plot_style_and_displays_warnings(self):
        page_source = Path("pages/2_ToxPi毒性评估.py").read_text(encoding="utf-8")
        self.assertIn("configure_plot_style", page_source)
        self.assertIn("st.warning", page_source)

    def test_r_style_toxpi_plot_uses_single_canvas_grid(self):
        norm_peak_area = np.linspace(1.0, 0.35, 15)
        norm_pbm = np.linspace(0.9, 0.3, 15)
        norm_df = np.linspace(0.8, 0.25, 15)
        toxpi_rows = pd.DataFrame(
            {
                "compound": [f"Compound {index:02d}" for index in range(15)],
                "toxpi": (norm_peak_area * 0.4) + (norm_pbm * 0.4) + (norm_df * 0.2),
                "norm_peak_area": norm_peak_area,
                "norm_pbm": norm_pbm,
                "norm_df": norm_df,
            }
        )
        toxpi_rows.attrs["toxic_cols"] = ["peak_area", "pbm", "df"]

        figure = generate_r_style_toxpi_plot(
            toxpi_rows,
            custom_weights={"peak_area": 0.4, "pbm": 0.4, "df": 0.2},
            toxic_cols=["peak_area", "pbm", "df"],
        )

        try:
            self.assertEqual(len(figure.axes), 1)
            axis = figure.axes[0]
            self.assertEqual(axis.name, "rectilinear")
            self.assertEqual(tuple(round(value, 1) for value in figure.get_size_inches()), (10.0, 8.0))
            self.assertEqual(len(axis.patches), 45)
            self.assertGreater(axis.patches[0].r, axis.patches[-3].r)
            self.assertAlmostEqual(axis.patches[0].r, 1.2)
            self.assertAlmostEqual(axis.patches[-3].r, 0.42)

            compound_texts = [text for text in axis.texts if text.get_text().startswith("Compound")]
            score_texts = [text for text in axis.texts if text.get_text().startswith("ToxPi:")]
            self.assertEqual(len(compound_texts), 15)
            self.assertEqual(len(score_texts), 15)
            self.assertEqual(compound_texts[0].get_position(), (0.0, -2.0))
            self.assertEqual(compound_texts[4].get_position(), (14.0, -2.0))
            self.assertEqual(compound_texts[5].get_position(), (0.0, -6.5))
            self.assertEqual(score_texts[0].get_position(), (0.0, -2.5))

            legend = figure.legends[0]
            self.assertEqual(legend.get_title().get_text(), "Metric")
            self.assertEqual([text.get_text() for text in legend.get_texts()], ["Peak area", "PBM scores", "DF"])
            self.assert_times_new_roman(figure)

            with warnings.catch_warnings():
                warnings.simplefilter("error")
                output = io.BytesIO()
                figure.savefig(output, format="png", dpi=300)
                self.assertGreater(len(output.getvalue()), 1_000)
        finally:
            plt.close(figure)


if __name__ == "__main__":
    unittest.main()
