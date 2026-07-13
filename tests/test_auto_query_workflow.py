from collections import OrderedDict
import zipfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.auto_query_workflow import (
    AutoWorkflowConfig,
    AutoWorkflowMapping,
    AutoWorkflowResult,
    R_DF_STEP_LABEL,
    build_auto_workflow_charts,
    build_auto_workflow_zip,
    detect_default_mapping,
    run_auto_query_workflow,
)
from src.mol_structure_parser import prepare_structure_dataframe


ETHANOL_MOL = """ethanol
  ChemPriority

  3  2  0  0  0  0  0  0  0  0  0
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.5000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.2500    1.2990    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""


class AutoQueryWorkflowTests(unittest.TestCase):
    def test_detect_default_mapping_for_level3_workbook_schema(self):
        columns = [
            "Checked",
            "Tags",
            "Name",
            "Calc. MW",
            "Avg TIC",
            "NIST Lib Hit Formula",
            "Group Area: 01YiChang-zhong-1",
            "Group Area: 01YiChang-zhong-2",
            "Group Area: 01YiChang-zhong-3",
        ]

        mapping = detect_default_mapping(columns)

        self.assertEqual(mapping.compound_col, "Name")
        self.assertEqual(mapping.formula_col, "NIST Lib Hit Formula")
        self.assertEqual(mapping.peak_area_col, "Avg TIC")
        self.assertEqual(
            mapping.group_area_cols,
            [
                "Group Area: 01YiChang-zhong-1",
                "Group Area: 01YiChang-zhong-2",
                "Group Area: 01YiChang-zhong-3",
            ],
        )

    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_auto_workflow_passes_mol_derived_smiles_to_identifier_step(self, run_identifier):
        self.assertTrue(hasattr(AutoWorkflowMapping, "mol_column"))
        input_df = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "NIST Lib Hit Formula": ["C2 H6 O"],
                "Avg TIC": [100.0],
                "Structure": [ETHANOL_MOL],
            }
        )
        run_identifier.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Ethanol"],
                    "smiles": ["CCO"],
                    "cas": ["64-17-5"],
                    "ec": [""],
                    "dtxsid": [""],
                    "echa_id": [""],
                }
            ),
            pd.DataFrame(),
        )

        result = run_auto_query_workflow(
            input_df,
            AutoWorkflowConfig(
                mapping=AutoWorkflowMapping(mol_column="Structure"),
                run_r_replicate_df=False,
                run_identifier=True,
                identifier_delay_seconds=0,
            ),
        )

        self.assertEqual(run_identifier.call_args.args[0].loc[0, "smiles"], "CCO")
        self.assertIn("Structure_Preparation", result.tables)

    def test_auto_workflow_reuses_prepared_mol_audit_without_reparsing(self):
        raw_input = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "NIST Lib Hit Formula": ["C2 H6 O"],
                "Avg TIC": [100.0],
                "Structure": [ETHANOL_MOL],
                "SMILES": ["not valid"],
            }
        )
        prepared_input = prepare_structure_dataframe(
            raw_input,
            mol_column="Structure",
            smiles_column="SMILES",
        )

        with patch(
            "src.auto_query_workflow.prepare_structure_dataframe",
            side_effect=AssertionError("prepared input must not be parsed again"),
        ):
            result = run_auto_query_workflow(
                prepared_input,
                AutoWorkflowConfig(
                    mapping=AutoWorkflowMapping(mol_column="Structure", smiles_col="SMILES"),
                    run_r_replicate_df=False,
                    run_identifier=False,
                ),
            )

        pd.testing.assert_frame_equal(result.tables["Structure_Preparation"], prepared_input)
        self.assertEqual(result.tables["Structure_Preparation"].loc[0, "smiles"], "CCO")
        self.assertEqual(result.tables["Structure_Preparation"].loc[0, "smiles_source"], "MOL 解析")
        self.assertIn("原始 SMILES 无效", result.tables["Structure_Preparation"].loc[0, "smiles_decision_warning"])

    @patch("src.auto_query_workflow.run_source_origin_batch")
    @patch("src.auto_query_workflow.run_echa_ghs_batch")
    @patch("src.auto_query_workflow.run_echa_use_batch")
    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_selected_network_steps_run_one_after_another(
        self,
        run_identifier,
        run_epi,
        run_comptox,
        run_echa_use,
        run_echa_ghs,
        run_source_origin,
    ):
        order = []
        input_df = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "NIST Lib Hit Formula": ["C2 H6 O"],
                "Avg TIC": [100.0],
                "Group Area: A": [100.0],
            }
        )
        completed = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
                "ec": ["200-578-6"],
                "dtxsid": ["DTXSID9020584"],
                "echa_id": ["100.000.526"],
            }
        )
        run_identifier.side_effect = lambda *args, **kwargs: (
            order.append("identifier") or completed,
            pd.DataFrame(),
        )
        run_epi.side_effect = lambda *args, **kwargs: (
            order.append("epi") or pd.DataFrame({"compound": ["Ethanol"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        run_comptox.side_effect = lambda *args, **kwargs: (
            order.append("comptox") or pd.DataFrame({"compound": ["Ethanol"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        run_echa_use.side_effect = lambda *args, **kwargs: (
            order.append("echa_use") or pd.DataFrame({"compound": ["Ethanol"]}),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        run_echa_ghs.side_effect = lambda *args, **kwargs: (
            order.append("echa_ghs") or pd.DataFrame({"compound": ["Ethanol"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        run_source_origin.side_effect = lambda *args, **kwargs: (
            order.append("source_origin") or pd.DataFrame({"compound": ["Ethanol"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        result = run_auto_query_workflow(
            input_df,
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                run_comptox=True,
                run_echa_use=True,
                run_echa_ghs=True,
                run_source_origin=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
                use_delay_seconds=0,
                echa_delay_seconds=0,
                source_origin_delay_seconds=0,
            ),
        )

        self.assertEqual(
            order,
            ["identifier", "epi", "comptox", "echa_use", "echa_ghs", "source_origin"],
        )
        self.assertIn("Identifier_Completion", result.tables)
        self.assertIn("Source_Origin_Summary", result.tables)
        self.assertEqual(result.step_status["status"].tolist(), ["完成"] * 6)

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_identifier_runs_as_dependency_when_epi_is_selected(self, run_identifier, run_epi):
        input_df = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "NIST Lib Hit Formula": ["C2 H6 O"],
                "Avg TIC": [100.0],
                "Group Area: A": [100.0],
            }
        )
        run_identifier.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Ethanol"],
                    "smiles": ["CCO"],
                    "cas": ["64-17-5"],
                    "ec": [""],
                    "dtxsid": [""],
                    "echa_id": [""],
                }
            ),
            pd.DataFrame(),
        )
        run_epi.return_value = (
            pd.DataFrame({"compound": ["Ethanol"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        result = run_auto_query_workflow(
            input_df,
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=False,
                run_epi=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
            ),
        )

        run_identifier.assert_called_once()
        run_epi.assert_called_once()
        self.assertEqual(result.step_status["step"].tolist(), ["标识符补全", "EPI Suite 环境归趋"])

    def test_new_page_uses_chart_specific_r_df_name(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertEqual(R_DF_STEP_LABEL, "化学类型图、DBE图、VK图与 DF")
        self.assertIn("化学类型图、DBE图、VK图与 DF", page_text)
        self.assertNotIn("R 复刻前半段", page_text)
        self.assertNotIn("前半段筛查", page_text)

    def test_auto_workflow_charts_are_generated_from_use_candidates(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A", "Compound B"]}),
            tables=OrderedDict(
                [
                    ("CompTox_Candidates", _example_comptox_candidates()),
                    ("ECHA_Use_Candidates", _example_echa_candidates()),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        charts = build_auto_workflow_charts(result)

        self.assertIn("EPA_Top_Predicted_Functional_Use", charts)
        self.assertIn("EPA_Product_Use_Category_Rose_Plot", charts)
        self.assertIn("EPA_Reported_Functional_Use_Evidence", charts)
        self.assertIn("ECHA_Use_Rose_Plot", charts)
        top_chart = charts["EPA_Top_Predicted_Functional_Use"]
        self.assertEqual(top_chart.title, "EPA CompTox Top Predicted Functional Use Distribution")
        self.assertTrue(top_chart.png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(top_chart.pdf.startswith(b"%PDF"))

    def test_auto_workflow_zip_contains_workbook_and_chart_files(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict([("CompTox_Candidates", _example_comptox_candidates())]),
            step_status=pd.DataFrame({"step": ["EPA CompTox 用途"], "status": ["完成"]}),
            warnings=pd.DataFrame(),
        )

        package = build_auto_workflow_zip(result)

        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertIn("Auto_Query_Workflow_Results.xlsx", names)
            self.assertIn("charts/EPA_Top_Predicted_Functional_Use.png", names)
            self.assertIn("charts/EPA_Top_Predicted_Functional_Use.pdf", names)
            self.assertIn("charts/EPA_Product_Use_Category_Rose_Plot.png", names)
            self.assertIn("charts/EPA_Reported_Functional_Use_Evidence.png", names)
            self.assertGreater(len(archive.read("Auto_Query_Workflow_Results.xlsx")), 1_000)
            self.assertTrue(archive.read("charts/EPA_Top_Predicted_Functional_Use.png").startswith(b"\x89PNG"))

    def test_page_6_previews_charts_and_downloads_zip(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertIn("build_auto_workflow_charts", page_text)
        self.assertIn("build_auto_workflow_zip", page_text)
        self.assertIn("st.image", page_text)
        self.assertIn("Auto_Query_Workflow_Results.zip", page_text)
        self.assertIn("application/zip", page_text)

    def test_page_6_groups_results_into_module_dashboard_tabs(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertIn("def _result_dashboard_groups", page_text)
        self.assertIn('"本地筛查"', page_text)
        self.assertIn('"标识符补全"', page_text)
        self.assertIn('"EPI Suite"', page_text)
        self.assertIn('"EPA CompTox"', page_text)
        self.assertIn('"ECHA"', page_text)
        self.assertIn('"来源属性"', page_text)
        self.assertIn('"Pov-LRTP / PBM / ToxPi"', page_text)

    def test_page_6_renders_module_dashboard_without_removing_exports(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertIn("def _render_result_dashboard", page_text)
        self.assertIn("st.tabs", page_text)
        self.assertIn("_result_dashboard_groups(result, charts)", page_text)
        self.assertIn('st.selectbox("查看结果表"', page_text)
        self.assertIn("Auto_Query_Workflow_Results.zip", page_text)


def _example_comptox_candidates():
    return pd.DataFrame(
        [
            {
                "compound": "Compound A",
                "source_type": "product_category",
                "raw_use": "Cleaner",
                "use_cn": "清洁剂",
                "evidence_count": 2,
            },
            {
                "compound": "Compound A",
                "source_type": "functional_use",
                "raw_use": "fragrance",
                "use_cn": "芳香剂",
                "evidence_count": 0.91,
                "probability": 0.91,
                "functional_use_source": "predicted",
            },
            {
                "compound": "Compound A",
                "source_type": "functional_use",
                "raw_use": "fragrance",
                "use_cn": "芳香剂",
                "evidence_count": 1,
                "functional_use_source": "reported",
            },
            {
                "compound": "Compound B",
                "source_type": "functional_use",
                "raw_use": "solvent",
                "use_cn": "溶剂",
                "evidence_count": 0.72,
                "probability": 0.72,
                "functional_use_source": "predicted",
            },
        ]
    )


def _example_echa_candidates():
    return pd.DataFrame(
        [
            {
                "compound": "Compound A",
                "category": "Industrial use",
                "use_cn": "工业用途",
                "evidence_count": 2,
            },
            {
                "compound": "Compound B",
                "category": "Professional use",
                "use_cn": "专业用途",
                "evidence_count": 1,
            },
        ]
    )


if __name__ == "__main__":
    unittest.main()
