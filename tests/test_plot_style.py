from pathlib import Path
import unittest
from unittest.mock import patch

import matplotlib
import matplotlib.pyplot as plt

import src.plot_style as plot_style
from src.plot_style import (
    PLOT_FONT_FAMILY,
    configure_plot_style,
)


class PlotStyleTests(unittest.TestCase):
    def test_configure_plot_style_uses_the_active_host_font(self):
        configure_plot_style()
        expected_family = (
            "Times New Roman"
            if plot_style.font_available("Times New Roman")
            else "Liberation Serif"
        )
        self.assertEqual(PLOT_FONT_FAMILY, expected_family)
        self.assertEqual(matplotlib.rcParams["font.family"][0], expected_family)
        self.assertEqual(matplotlib.rcParams["font.serif"][0], expected_family)
        self.assertEqual(matplotlib.rcParams["pdf.fonttype"], 42)
        self.assertEqual(matplotlib.rcParams["ps.fonttype"], 42)
        self.assertFalse(matplotlib.rcParams["axes.unicode_minus"])

    def test_select_plot_font_prefers_times_new_roman_when_available(self):
        self.assertTrue(hasattr(plot_style, "select_plot_font"))
        with patch("src.plot_style.font_available", return_value=True):
            self.assertEqual(plot_style.select_plot_font(), "Times New Roman")

    def test_select_plot_font_uses_liberation_serif_when_times_is_missing(self):
        self.assertTrue(hasattr(plot_style, "select_plot_font"))

        def available(font_name):
            return font_name == "Liberation Serif"

        with patch("src.plot_style.font_available", side_effect=available):
            self.assertEqual(plot_style.select_plot_font(), "Liberation Serif")

    def test_missing_font_warning_names_both_supported_families(self):
        with patch("src.plot_style.font_available", return_value=False):
            self.assertEqual(
                configure_plot_style(),
                [
                    "Neither Times New Roman nor Liberation Serif is available. "
                    "Install the 'fonts-liberation' package on the runtime host "
                    "before exporting publication figures."
                ],
            )

    def test_streamlit_cloud_package_declaration_installs_liberation_fonts(self):
        package_file = Path(__file__).resolve().parents[1] / "packages.txt"
        self.assertTrue(package_file.is_file())
        self.assertEqual(
            package_file.read_text(encoding="utf-8").splitlines(),
            ["fonts-liberation"],
        )

    def test_apply_figure_font_updates_every_text_artist(self):
        fig, ax = plt.subplots()
        ax.set_title("Title")
        ax.set_xlabel("X")
        ax.text(0.5, 0.5, "Body")
        plot_style.apply_figure_font(fig)
        families = {
            text.get_fontfamily()[0]
            for text in fig.findobj(matplotlib.text.Text)
            if text.get_text()
        }
        self.assertEqual(families, {PLOT_FONT_FAMILY})
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
