import io
import unittest
import warnings

import matplotlib.pyplot as plt
import pandas as pd

from src.toxpi_calc import (
    generate_multi_toxpi_plot,
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


if __name__ == "__main__":
    unittest.main()
