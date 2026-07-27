import importlib
import importlib.util
import unittest
from pathlib import Path

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

    def test_builds_four_decimal_number_column_config_without_mutating_frame(self):
        display = self._display_module()
        helper = getattr(display, "toxpi_dataframe_column_config", None)
        self.assertIsNotNone(
            helper,
            "shared display policy should build Streamlit column configuration",
        )
        frame = pd.DataFrame({"compound": ["A"], "toxpi": [0.62126]})
        original = frame.copy(deep=True)
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return kwargs

        config = helper(frame, factory)

        self.assertEqual(config, {"toxpi": {"format": "%.4f"}})
        self.assertEqual(calls, [{"format": "%.4f"}])
        pd.testing.assert_frame_equal(frame, original)

    def test_toxpi_page_uses_shared_four_decimal_table_policy(self):
        page_source = Path("pages/2_ToxPi毒性评估.py").read_text(encoding="utf-8")

        self.assertIn("toxpi_dataframe_column_config", page_source)
        self.assertIn("def show_toxpi_dataframe(", page_source)
        self.assertIn(
            'show_toxpi_dataframe(final_agg[["compound", "toxpi"]])',
            page_source,
        )
        self.assertIn("show_toxpi_dataframe(combined_summary)", page_source)


if __name__ == "__main__":
    unittest.main()
