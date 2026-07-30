import ast
import unittest
from pathlib import Path

import pandas as pd


class EPISuiteDisplayTests(unittest.TestCase):
    def test_page_uses_uniform_dataframe_options_for_all_detail_tables(self):
        class FakeTab:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class FakeStreamlit:
            def __init__(self):
                self.dataframe_calls = []

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
        namespace = {"st": fake_st}
        isolated_page = ast.Module(body=selected_nodes, type_ignores=[])
        exec(compile(isolated_page, page_path, "exec"), namespace)

        property_columns = {
            "koawin_log_kaw": [-2.0],
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
        for sheet_name, _ in detail_sheets:
            self.assertEqual(calls_by_sheet[sheet_name]["width"], "stretch")
            self.assertIs(calls_by_sheet[sheet_name]["hide_index"], True)
            self.assertNotIn("column_config", calls_by_sheet[sheet_name])

    def test_page_has_no_properties_only_display_mapping(self):
        page_source = Path("pages/3_EPISuite环境归趋.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("episuite_property_column_config", page_source)
        self.assertIn("hide_index=True", page_source)


if __name__ == "__main__":
    unittest.main()
