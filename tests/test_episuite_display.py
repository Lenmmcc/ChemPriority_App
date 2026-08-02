import ast
import unittest
from pathlib import Path

import pandas as pd

from src import episuite_io


class EPISuiteDisplayTests(unittest.TestCase):
    def test_target_indicator_descriptions_include_partition_and_rdkit_context(self):
        descriptions = {
            item["endpoint"]: (item["model"], item["description"])
            for item in getattr(
                episuite_io,
                "TARGET_INDICATOR_DESCRIPTIONS",
                [],
            )
        }

        self.assertEqual(
            descriptions,
            {
                **{
                    item["endpoint"]: (item["model"], item["description"])
                    for item in episuite_io.FATE_ENDPOINTS
                    if item["endpoint"]
                    not in {"log_kow", "log_koc"}
                },
                "log_kow": (
                    "KOWWIN",
                    "辛醇/水分配系数 logKow（优先采用实验值；无实验值时采用 KOWWIN 估算值）",
                ),
                "log_koa": (
                    "KOAWIN",
                    "辛醇/空气分配系数 logKoa（优先采用实验值；无实验值时采用 KOAWIN 估算值）",
                ),
                "log_kaw": (
                    "KOAWIN",
                    "空气/水分配系数 logKaw（由 KOAWIN 的 KAW 取 log10）",
                ),
                "tpsa_rdkit_a2": (
                    "RDKit",
                    "拓扑极性表面积 TPSA（Å²；RDKit 结构计算值）",
                ),
                "mr_rdkit_cm3_mol": (
                    "RDKit",
                    "Wildman–Crippen 摩尔折射率 MR（cm³/mol；RDKit 结构计算值）",
                ),
                "log_koc": (
                    "KOCWIN",
                    "有机碳归一化吸附系数 logKoc（优先采用实验值；无实验值时采用 KOCWIN 的 MCI 估算值）",
                ),
            },
        )
        target_descriptions = getattr(
            episuite_io,
            "TARGET_INDICATOR_DESCRIPTIONS",
            [],
        )
        self.assertEqual(
            [item["endpoint"] for item in target_descriptions[:5]],
            [
                "log_kow",
                "log_koa",
                "log_kaw",
                "tpsa_rdkit_a2",
                "mr_rdkit_cm3_mol",
            ],
        )

    def test_display_only_indicators_do_not_expand_core_endpoint_keys(self):
        self.assertEqual(
            episuite_io.ENDPOINT_KEYS,
            [item["endpoint"] for item in episuite_io.FATE_ENDPOINTS],
        )
        for endpoint in (
            "log_koa",
            "log_kaw",
            "tpsa_rdkit_a2",
            "mr_rdkit_cm3_mol",
        ):
            self.assertNotIn(endpoint, episuite_io.ENDPOINT_KEYS)

    def test_page_uses_target_indicator_descriptions(self):
        page_source = Path("pages/3_EPISuite环境归趋.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("TARGET_INDICATOR_DESCRIPTIONS", page_source)
        self.assertIn(
            "pd.DataFrame(TARGET_INDICATOR_DESCRIPTIONS)",
            page_source,
        )
        self.assertNotIn("pd.DataFrame(FATE_ENDPOINTS)", page_source)

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
