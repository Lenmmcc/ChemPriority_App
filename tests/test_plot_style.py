import unittest
from unittest.mock import patch

import matplotlib
import matplotlib.pyplot as plt

from src.plot_style import (
    PLOT_FONT_FAMILY,
    PLOT_FONT_WARNING,
    apply_figure_font,
    configure_plot_style,
)


class PlotStyleTests(unittest.TestCase):
    def test_configure_plot_style_sets_times_new_roman(self):
        configure_plot_style()
        self.assertEqual(matplotlib.rcParams["font.family"][0], "Times New Roman")
        self.assertEqual(matplotlib.rcParams["font.serif"][0], "Times New Roman")
        self.assertEqual(matplotlib.rcParams["pdf.fonttype"], 42)
        self.assertFalse(matplotlib.rcParams["axes.unicode_minus"])

    def test_missing_font_returns_explicit_warning(self):
        with patch("src.plot_style.font_available", return_value=False):
            self.assertEqual(configure_plot_style(), [PLOT_FONT_WARNING])

    def test_apply_figure_font_updates_every_text_artist(self):
        fig, ax = plt.subplots()
        ax.set_title("Title")
        ax.set_xlabel("X")
        ax.text(0.5, 0.5, "Body")
        apply_figure_font(fig)
        families = {
            text.get_fontfamily()[0]
            for text in fig.findobj(matplotlib.text.Text)
            if text.get_text()
        }
        self.assertEqual(families, {PLOT_FONT_FAMILY})
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
