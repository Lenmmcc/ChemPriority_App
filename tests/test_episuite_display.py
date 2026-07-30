import unittest
from pathlib import Path

import pandas as pd

from src.episuite_display import (
    episuite_property_column_config,
    episuite_property_export_frame,
)


class EPISuiteDisplayTests(unittest.TestCase):
    def test_builds_labels_and_formats_without_mutating_frame(self):
        frame = pd.DataFrame(
            {
                "compound": ["A"],
                "koawin_log_kow": [3.0],
                "koawin_kow": [1000.0],
                "koawin_log_koa": [5.0],
                "koawin_koa": [100000.0],
                "koawin_log_kaw": [-2.0],
                "koawin_kaw": [0.01],
                "tpsa_rdkit_a2": [20.23],
                "mr_rdkit_cm3_mol": [12.7598],
            }
        )
        original = frame.copy(deep=True)
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return kwargs

        config = episuite_property_column_config(frame, factory)

        expected = {
            "koawin_log_kow": ("logKOW（KOAWIN估算）", "%.6f"),
            "koawin_kow": ("KOW（KOAWIN估算）", "%.6e"),
            "koawin_log_koa": ("logKOA（KOAWIN估算）", "%.6f"),
            "koawin_koa": ("KOA（KOAWIN估算）", "%.6e"),
            "koawin_log_kaw": ("logKAW（KOAWIN估算）", "%.6f"),
            "koawin_kaw": ("KAW（KOAWIN估算）", "%.6e"),
            "tpsa_rdkit_a2": ("TPSA（Å²，RDKit）", "%.6f"),
            "mr_rdkit_cm3_mol": ("MR（cm³/mol，RDKit）", "%.6f"),
        }
        self.assertEqual(
            {
                column: (settings["label"], settings["format"])
                for column, settings in config.items()
            },
            expected,
        )
        self.assertEqual(len(calls), 8)
        pd.testing.assert_frame_equal(frame, original)

        export_frame = episuite_property_export_frame(frame)
        self.assertIn("KOW（KOAWIN估算）", export_frame.columns)
        self.assertIn("TPSA（Å²，RDKit）", export_frame.columns)
        self.assertIsInstance(export_frame.loc[0, "KOW（KOAWIN估算）"], float)
        self.assertEqual(export_frame.loc[0, "KOW（KOAWIN估算）"], 1000.0)
        pd.testing.assert_frame_equal(frame, original)

    def test_page_uses_property_display_policy_only_for_properties(self):
        source = Path("pages/3_EPISuite环境归趋.py").read_text(encoding="utf-8")

        self.assertIn("episuite_property_column_config", source)
        self.assertIn('if sheet_name == "Properties"', source)
        self.assertIn("st.column_config.NumberColumn", source)
        self.assertIn("column_config=column_config", source)


if __name__ == "__main__":
    unittest.main()
