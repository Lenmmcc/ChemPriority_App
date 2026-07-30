import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

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

        expected_specs = {
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
            expected_specs,
        )
        self.assertEqual(len(calls), 8)
        pd.testing.assert_frame_equal(frame, original)

        export_frame = episuite_property_export_frame(frame)
        for internal_column, (label, _) in expected_specs.items():
            self.assertIn(label, export_frame.columns)
            self.assertNotIn(internal_column, export_frame.columns)
            self.assertIsInstance(export_frame.loc[0, label], float)
            self.assertEqual(
                export_frame.loc[0, label],
                frame.loc[0, internal_column],
            )
        pd.testing.assert_frame_equal(frame, original)

    def test_page_applies_number_config_only_to_properties(self):
        class FakeTab:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class FakeStreamlit:
            def __init__(self):
                self.dataframe_calls = []
                self.column_config = SimpleNamespace(
                    NumberColumn=lambda **kwargs: kwargs
                )

            def subheader(self, _label):
                return None

            def tabs(self, labels):
                return [FakeTab() for _ in labels]

            def dataframe(self, frame, **kwargs):
                self.dataframe_calls.append((frame, kwargs))

            def info(self, _message):
                return None

            def expander(self, _label, expanded=False):
                return FakeTab()

        page_path = Path("pages/3_EPISuite环境归趋.py")
        page_tree = ast.parse(page_path.read_text(encoding="utf-8"))
        selected_nodes = []
        for node in page_tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name)
                and target.id == "DETAIL_RESULT_SHEETS"
                for target in node.targets
            ):
                selected_nodes.append(node)
            elif (
                isinstance(node, ast.FunctionDef)
                and node.name == "render_epi_web_tables"
            ):
                selected_nodes.append(node)

        fake_st = FakeStreamlit()
        namespace = {
            "st": fake_st,
            "episuite_property_column_config": episuite_property_column_config,
        }
        isolated_page = ast.Module(body=selected_nodes, type_ignores=[])
        exec(compile(isolated_page, page_path, "exec"), namespace)

        property_columns = {
            "koawin_log_kow": [3.0],
            "koawin_kow": [1000.0],
            "koawin_log_koa": [5.0],
            "koawin_koa": [100000.0],
            "koawin_log_kaw": [-2.0],
            "koawin_kaw": [0.01],
            "tpsa_rdkit_a2": [20.23],
            "mr_rdkit_cm3_mol": [12.7598],
        }
        detail_sheets = namespace["DETAIL_RESULT_SHEETS"]
        tables = {
            sheet_name: (
                pd.DataFrame({"compound": ["A"], **property_columns})
                if sheet_name == "Properties"
                else pd.DataFrame({"value": [1.0]})
            )
            for sheet_name, _ in detail_sheets
        }
        tables["Raw_API_JSON"] = pd.DataFrame()

        namespace["render_epi_web_tables"](tables)

        self.assertEqual(len(fake_st.dataframe_calls), len(detail_sheets))
        calls_by_sheet = {
            sheet_name: kwargs
            for (sheet_name, _), (_, kwargs) in zip(
                detail_sheets,
                fake_st.dataframe_calls,
            )
        }
        self.assertEqual(
            set(calls_by_sheet["Properties"]["column_config"]),
            set(property_columns),
        )
        self.assertTrue(calls_by_sheet["Properties"]["column_config"])
        for sheet_name, _ in detail_sheets:
            if sheet_name != "Properties":
                self.assertEqual(
                    calls_by_sheet[sheet_name]["column_config"],
                    {},
                    msg=f"{sheet_name} unexpectedly received number formatting",
                )


if __name__ == "__main__":
    unittest.main()
