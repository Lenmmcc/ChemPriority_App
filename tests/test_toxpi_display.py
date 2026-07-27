import importlib
import importlib.util
import unittest

import pandas as pd


class ToxPiDisplayTests(unittest.TestCase):
    def _display_module(self):
        spec = importlib.util.find_spec("src.toxpi_display")
        self.assertIsNotNone(spec, "src.toxpi_display should define the shared display policy")
        return importlib.import_module("src.toxpi_display")

    def test_formats_score_with_exactly_four_decimal_places(self):
        display = self._display_module()

        self.assertEqual(display.format_toxpi_score(0.62126), "0.6213")
        self.assertEqual(display.format_toxpi_score(0.62124), "0.6212")

    def test_identifies_only_toxpi_score_columns_in_source_order(self):
        display = self._display_module()
        frame = pd.DataFrame(
            columns=["compound", "initial_toxpi", "toxpi", "final_rank", "mean_rho"]
        )

        self.assertEqual(
            display.toxpi_score_columns(frame),
            ("initial_toxpi", "toxpi"),
        )


if __name__ == "__main__":
    unittest.main()
