from collections import OrderedDict
import ast
import importlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import zipfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.auto_query_workflow import (
    AutoWorkflowConfig,
    AutoWorkflowChart,
    AutoWorkflowMapping,
    AutoWorkflowResult,
    LocalScreeningOutput,
    R_DF_STEP_LABEL,
    _load_local_screening_charts,
    build_auto_workflow_charts,
    build_auto_workflow_workbook,
    build_auto_workflow_zip,
    detect_default_mapping,
    run_auto_query_workflow,
)
from src.mol_structure_parser import prepare_structure_dataframe
from src.use_rose_plot import (
    build_compound_universe,
    extract_source_origin_pie_data,
    extract_top_predicted_functional_use_data,
    extract_top_reported_functional_use_data,
)


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
    def test_page_6_auto_query_imports_resolve_to_real_exports(self):
        page_path = Path("pages/6_一键批量查询.py")
        page_tree = ast.parse(page_path.read_text(encoding="utf-8"))
        imported_names = {
            alias.name
            for node in ast.walk(page_tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "src.auto_query_workflow"
            for alias in node.names
        }
        workflow_module = importlib.import_module("src.auto_query_workflow")

        missing_exports = sorted(
            name for name in imported_names if not hasattr(workflow_module, name)
        )

        self.assertEqual(missing_exports, [])

    def test_local_screening_chart_paths_become_portable_png_pdf_bytes(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            figure_paths = {}
            for source_key in (
                "category_percent_donut_with_total",
                "compound_bubble_plot",
                "VanKrevelen",
            ):
                png_path = root / f"{source_key}.png"
                pdf_path = root / f"{source_key}.pdf"
                png_path.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                pdf_path.write_bytes(b"%PDF-1.4 local")
                figure_paths[source_key] = {"png": png_path, "pdf": pdf_path}

            charts, warnings = _load_local_screening_charts(
                SimpleNamespace(figure_paths=figure_paths)
            )

        self.assertEqual(
            list(charts),
            [
                "Local_Chemical_Type_Distribution",
                "Local_DBE_Bubble_Plot",
                "Local_Van_Krevelen_Plot",
            ],
        )
        self.assertEqual(warnings, [])
        self.assertTrue(charts["Local_Chemical_Type_Distribution"].png.startswith(b"\x89PNG"))
        self.assertTrue(charts["Local_Van_Krevelen_Plot"].pdf.startswith(b"%PDF"))

    def test_missing_local_screening_charts_are_skipped_with_warnings(self):
        charts, warnings = _load_local_screening_charts(
            SimpleNamespace(
                figure_paths={
                    "category_percent_donut_with_total": {
                        "png": Path("missing.png"),
                        "pdf": Path("missing.pdf"),
                    }
                }
            )
        )

        self.assertEqual(charts, OrderedDict())
        self.assertEqual(len(warnings), 3)
        self.assertIn("Chemical Type Distribution", warnings[0])

    @patch("src.auto_query_workflow._run_r_replicate_df")
    def test_workflow_preserves_local_screening_charts_and_warnings(self, mock_local):
        chart = AutoWorkflowChart("DBE Bubble Plot", b"\x89PNG\r\n\x1a\n", b"%PDF")
        mock_local.return_value = LocalScreeningOutput(
            tables=OrderedDict([("DF_Table", pd.DataFrame({"Name": ["A"]}))]),
            charts=OrderedDict([("Local_DBE_Bubble_Plot", chart)]),
            warnings=["Van Krevelen Plot: missing"],
        )

        result = run_auto_query_workflow(
            pd.DataFrame(
                {
                    "Name": ["A"],
                    "NIST Lib Hit Formula": ["C2H6"],
                    "Avg TIC": [2e5],
                }
            ),
            AutoWorkflowConfig(run_identifier=False),
        )

        self.assertIs(result.charts["Local_DBE_Bubble_Plot"], chart)
        self.assertIn("Van Krevelen Plot: missing", result.warnings["message"].tolist())

    @patch("src.auto_query_workflow.configure_plot_style", return_value=["font missing"])
    def test_batch_surfaces_plot_font_warning(self, configure_plot_style):
        result = run_auto_query_workflow(
            pd.DataFrame(
                {
                    "Name": ["A"],
                    "NIST Lib Hit Formula": ["C2H6O"],
                    "Avg TIC": [100.0],
                }
            ),
            config=AutoWorkflowConfig(
                mapping=AutoWorkflowMapping(
                    compound_col="Name",
                    formula_col="NIST Lib Hit Formula",
                    peak_area_col="Avg TIC",
                ),
                run_r_replicate_df=False,
                run_identifier=False,
            ),
        )

        configure_plot_style.assert_called_once_with()
        self.assertIn("font missing", result.warnings["message"].tolist())
        self.assertEqual(result.tables["Plot_Warnings"]["warning"].tolist(), ["font missing"])

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
                "Name": ["Compound A", "Compound B", "Compound C"],
                "NIST Lib Hit Formula": ["C2 H6 O", "C3 H8 O", "C4 H10 O"],
                "Avg TIC": [100.0, 90.0, 80.0],
                "Group Area: A": [100.0, 90.0, 80.0],
            }
        )
        completed = pd.DataFrame(
            {
                "compound": ["Compound A", "Compound B", "Compound C"],
                "smiles": ["CCO", "CCCO", "CCCCO"],
                "cas": ["64-17-5", "71-23-8", "71-36-3"],
                "ec": ["200-578-6", "200-746-9", "200-751-6"],
                "dtxsid": ["DTXSID9020584", "DTXSID6021963", "DTXSID1021740"],
                "echa_id": ["100.000.526", "100.000.682", "100.000.687"],
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
            order.append("comptox") or pd.DataFrame({"compound": ["Compound A", "Compound B"]}),
            _example_comptox_candidates(),
            pd.DataFrame(),
        )
        run_echa_use.side_effect = lambda *args, **kwargs: (
            order.append("echa_use") or pd.DataFrame({"compound": ["Compound A", "Compound B"]}),
            _example_echa_candidates(),
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
        for table_name in (
            "Product_Use_Categories",
            "Functional_Uses_Predicted",
            "Functional_Uses_Reported",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "Source_Origin_Pie_Data",
        ):
            self.assertIn(table_name, result.tables)
        self.assertEqual(len(result.tables["EPA_Reported_Pie_Data"]), 3)
        self.assertEqual(len(result.tables["ECHA_Reported_Pie_Data"]), 3)
        self.assertEqual(len(result.tables["Source_Origin_Pie_Data"]), 3)
        self.assertEqual(result.step_status["status"].tolist(), ["完成"] * 6)

    @patch("src.auto_query_workflow.run_source_origin_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_source_origin_only_leaves_upstream_use_inputs_as_none(
        self,
        run_identifier,
        run_source_origin,
    ):
        run_identifier.return_value = (_completed_identifier_rows(["Compound A"]), pd.DataFrame())
        run_source_origin.return_value = (
            pd.DataFrame({"compound": ["Compound A"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=False,
                run_source_origin=True,
                identifier_delay_seconds=0,
                source_origin_delay_seconds=0,
            ),
        )

        kwargs = run_source_origin.call_args.kwargs
        self.assertIsNone(kwargs["comptox_summary_df"])
        self.assertIsNone(kwargs["comptox_candidates_df"])
        self.assertIsNone(kwargs["echa_summary_df"])
        self.assertIsNone(kwargs["echa_candidates_df"])
        self.assertIsNone(kwargs["echa_dossiers_df"])

    @patch("src.auto_query_workflow.run_source_origin_batch")
    @patch("src.auto_query_workflow.run_echa_use_batch")
    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_source_origin_reuses_empty_results_from_executed_upstream_queries(
        self,
        run_identifier,
        run_comptox,
        run_echa_use,
        run_source_origin,
    ):
        run_identifier.return_value = (_completed_identifier_rows(["Compound A"]), pd.DataFrame())
        run_comptox.return_value = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        run_echa_use.return_value = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        run_source_origin.return_value = (
            pd.DataFrame({"compound": ["Compound A"]}),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=False,
                run_comptox=True,
                run_echa_use=True,
                run_source_origin=True,
                identifier_delay_seconds=0,
                use_delay_seconds=0,
                echa_delay_seconds=0,
                source_origin_delay_seconds=0,
            ),
        )

        kwargs = run_source_origin.call_args.kwargs
        for name in (
            "comptox_summary_df",
            "comptox_candidates_df",
            "echa_summary_df",
            "echa_candidates_df",
            "echa_dossiers_df",
        ):
            self.assertIsInstance(kwargs[name], pd.DataFrame)
            self.assertTrue(kwargs[name].empty)

    @patch("src.auto_query_workflow.run_source_origin_batch")
    @patch("src.auto_query_workflow.run_echa_use_batch")
    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_identifier_exception_preserves_original_compound_universe(
        self,
        run_identifier,
        run_comptox,
        run_echa_use,
        run_source_origin,
    ):
        compounds = ["Compound A", "Compound B", "Compound C"]
        run_identifier.side_effect = RuntimeError("identifier unavailable")
        run_comptox.return_value = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        run_echa_use.return_value = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        run_source_origin.return_value = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        result = run_auto_query_workflow(
            _workflow_input_rows(compounds),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_comptox=True,
                run_echa_use=True,
                run_source_origin=True,
                identifier_delay_seconds=0,
                use_delay_seconds=0,
                echa_delay_seconds=0,
                source_origin_delay_seconds=0,
            ),
        )

        for batch in (run_comptox, run_echa_use, run_source_origin):
            self.assertEqual(batch.call_args.args[0]["compound"].tolist(), compounds)
        for table_name in (
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "ECHA_Reported_Pie_Data",
            "Source_Origin_Pie_Data",
        ):
            table = result.tables[table_name]
            self.assertEqual(len(table), 3)
            self.assertEqual(table["compound_key"].nunique(), 3)

    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_partial_identifier_completion_enriches_without_dropping_original_rows(
        self,
        run_identifier,
        run_comptox,
    ):
        compounds = ["Compound A", "Compound B", "Compound C"]
        run_identifier.return_value = (
            _completed_identifier_rows(["Compound B"]),
            pd.DataFrame(),
        )
        run_comptox.return_value = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

        result = run_auto_query_workflow(
            _workflow_input_rows(compounds),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_comptox=True,
                identifier_delay_seconds=0,
                use_delay_seconds=0,
            ),
        )

        query_input = run_comptox.call_args.args[0]
        self.assertEqual(query_input["compound"].tolist(), compounds)
        self.assertEqual(query_input.loc[1, "smiles"], "CCO")
        self.assertEqual(query_input.loc[[0, 2], "smiles"].tolist(), ["", ""])
        for table_name in ("EPA_Predicted_Pie_Data", "EPA_Reported_Pie_Data"):
            table = result.tables[table_name]
            self.assertEqual(len(table), 3)
            self.assertEqual(table["compound_key"].nunique(), 3)

    @patch("src.auto_query_workflow.run_source_origin_batch")
    @patch("src.auto_query_workflow.run_echa_use_batch")
    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_selected_use_module_exceptions_create_full_universe_audit_tables_only(
        self,
        run_identifier,
        run_comptox,
        run_echa_use,
        run_source_origin,
    ):
        compounds = ["Compound A", "Compound B", "Compound C"]
        run_identifier.return_value = (_completed_identifier_rows(compounds), pd.DataFrame())
        run_comptox.side_effect = RuntimeError("EPA unavailable")
        run_echa_use.side_effect = RuntimeError("ECHA unavailable")
        run_source_origin.side_effect = RuntimeError("source unavailable")

        result = run_auto_query_workflow(
            _workflow_input_rows(compounds),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_comptox=True,
                run_echa_use=True,
                run_source_origin=True,
                identifier_delay_seconds=0,
                use_delay_seconds=0,
                echa_delay_seconds=0,
                source_origin_delay_seconds=0,
            ),
        )

        for table_name in (
            "Product_Use_Categories",
            "Functional_Uses_Predicted",
            "Functional_Uses_Reported",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "Source_Origin_Pie_Data",
        ):
            self.assertIn(table_name, result.tables)
        for table_name, missing_label in (
            ("EPA_Predicted_Pie_Data", "Others"),
            ("EPA_Reported_Pie_Data", "Others"),
            ("ECHA_Uses_Reported", "Others"),
            ("ECHA_Reported_Pie_Data", "Others"),
            ("Source_Origin_Pie_Data", "Unknown"),
        ):
            table = result.tables[table_name]
            self.assertEqual(len(table), 3)
            self.assertEqual(table["compound_key"].nunique(), 3)
            self.assertEqual(set(table["display_label"]), {missing_label})

        source_only = run_auto_query_workflow(
            _workflow_input_rows(compounds),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_source_origin=True,
                identifier_delay_seconds=0,
                source_origin_delay_seconds=0,
            ),
        )
        self.assertIn("Source_Origin_Pie_Data", source_only.tables)
        self.assertNotIn("EPA_Predicted_Pie_Data", source_only.tables)
        self.assertNotIn("ECHA_Reported_Pie_Data", source_only.tables)

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

    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_workflow_forwards_network_activity_with_stage_and_timeout(self, run_identifier):
        input_df = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "NIST Lib Hit Formula": ["C2 H6 O"],
                "Avg TIC": [100.0],
            }
        )
        completed = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
                "ec": [""],
                "dtxsid": [""],
                "echa_id": [""],
            }
        )

        def fake_identifier(*args, **kwargs):
            kwargs["activity_callback"](
                {
                    "event": "started",
                    "index": 0,
                    "total": 1,
                    "done": 0,
                    "label": "Ethanol",
                    "elapsed_seconds": 0.0,
                    "error": None,
                }
            )
            return completed, pd.DataFrame()

        run_identifier.side_effect = fake_identifier
        events = []
        result = run_auto_query_workflow(
            input_df,
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                identifier_timeout=12,
                identifier_delay_seconds=0,
            ),
            activity_callback=events.append,
        )

        self.assertEqual(events[0]["event"], "started")
        self.assertEqual(events[0]["step"], result.step_status.loc[0, "step"])
        self.assertEqual(events[0]["timeout_seconds"], 12)

    def test_new_page_uses_chart_specific_r_df_name(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertEqual(R_DF_STEP_LABEL, "化学类型图、DBE图、VK图与 DF")
        self.assertIn("化学类型图、DBE图、VK图与 DF", page_text)
        self.assertNotIn("R 复刻前半段", page_text)
        self.assertNotIn("前半段筛查", page_text)

    def test_auto_workflow_charts_are_generated_from_use_candidates(self):
        pie_tables = _example_pie_tables()
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A", "Compound B"]}),
            tables=OrderedDict(
                [
                    ("CompTox_Candidates", _example_comptox_candidates()),
                    ("ECHA_Use_Candidates", _example_echa_candidates()),
                    *pie_tables.items(),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        charts = build_auto_workflow_charts(result)

        expected = {
            "EPA_Product_Use_Category_Rose_Plot",
            "EPA_Top_Predicted_Functional_Use",
            "EPA_Reported_Functional_Use_Distribution",
            "EPA_Reported_Functional_Use_Evidence",
            "ECHA_Reported_Use_Distribution",
            "ECHA_Reported_Use_Evidence",
            "Source_Origin_Distribution",
        }
        self.assertTrue(expected.issubset(charts))
        self.assertNotIn("ECHA_Use_Rose_Plot", charts)
        top_chart = charts["EPA_Top_Predicted_Functional_Use"]
        self.assertEqual(top_chart.title, "EPA CompTox Top Predicted Functional Use Distribution")
        self.assertTrue(top_chart.png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(top_chart.pdf.startswith(b"%PDF"))

    def test_auto_workflow_zip_groups_results_by_module(self):
        local_chart = AutoWorkflowChart(
            title="Chemical Type Distribution",
            png=b"\x89PNG\r\n\x1a\nlocal",
            pdf=b"%PDF-1.4 local",
        )
        pie_tables = _example_pie_tables()
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [
                    ("DF_Table", pd.DataFrame({"Name": ["Compound A"], "DF": [1.0]})),
                    ("Identifier_Completion", pd.DataFrame({"compound": ["Compound A"]})),
                    ("CompTox_Candidates", _example_comptox_candidates()),
                    ("ECHA_Use_Candidates", _example_echa_candidates()),
                    ("Product_Use_Categories", _example_comptox_candidates().iloc[[0]].copy()),
                    ("Functional_Uses_Predicted", _example_comptox_candidates().iloc[[1, 3]].copy()),
                    ("Functional_Uses_Reported", _example_comptox_candidates().iloc[[2]].copy()),
                    ("ECHA_Uses_Reported", _example_echa_candidates()),
                    *pie_tables.items(),
                ]
            ),
            step_status=pd.DataFrame({"step": ["EPA CompTox 用途"], "status": ["完成"]}),
            warnings=pd.DataFrame(),
            charts=OrderedDict([("Local_Chemical_Type_Distribution", local_chart)]),
        )

        package = build_auto_workflow_zip(result)

        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            expected = {
                "Auto_Query_Workflow_Results.xlsx",
                "01_Local_Screening/Local_Screening_Results.xlsx",
                "01_Local_Screening/figures/Chemical_Type_Distribution.png",
                "01_Local_Screening/figures/Chemical_Type_Distribution.pdf",
                "02_Identifier_Completion/Identifier_Completion_Results.xlsx",
                "04_EPA_CompTox/EPA_CompTox_Results.xlsx",
                "04_EPA_CompTox/figures/EPA_Product_Use_Category_Rose_Plot.png",
                "04_EPA_CompTox/figures/EPA_Product_Use_Category_Rose_Plot.pdf",
                "04_EPA_CompTox/figures/EPA_Top_Predicted_Functional_Use.png",
                "04_EPA_CompTox/figures/EPA_Top_Predicted_Functional_Use.pdf",
                "04_EPA_CompTox/figures/EPA_Reported_Functional_Use_Distribution.png",
                "04_EPA_CompTox/figures/EPA_Reported_Functional_Use_Distribution.pdf",
                "04_EPA_CompTox/figures/EPA_Reported_Functional_Use_Evidence.png",
                "04_EPA_CompTox/figures/EPA_Reported_Functional_Use_Evidence.pdf",
                "05_ECHA/ECHA_Results.xlsx",
                "05_ECHA/figures/ECHA_Reported_Use_Distribution.png",
                "05_ECHA/figures/ECHA_Reported_Use_Distribution.pdf",
                "05_ECHA/figures/ECHA_Reported_Use_Evidence.png",
                "05_ECHA/figures/ECHA_Reported_Use_Evidence.pdf",
                "06_Source_Origin/Source_Origin_Results.xlsx",
                "06_Source_Origin/figures/Source_Origin_Distribution.png",
                "06_Source_Origin/figures/Source_Origin_Distribution.pdf",
            }
            self.assertTrue(expected.issubset(names))
            self.assertFalse(any(name.startswith("03_EPI_Suite/") for name in names))
            self.assertGreater(len(archive.read("Auto_Query_Workflow_Results.xlsx")), 1_000)
            self.assertTrue(
                archive.read("01_Local_Screening/figures/Chemical_Type_Distribution.png").startswith(
                    b"\x89PNG"
                )
            )

            local_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("01_Local_Screening/Local_Screening_Results.xlsx"))
            ).sheet_names
            identifier_sheets = pd.ExcelFile(
                io.BytesIO(
                    archive.read("02_Identifier_Completion/Identifier_Completion_Results.xlsx")
                )
            ).sheet_names
            epa_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("04_EPA_CompTox/EPA_CompTox_Results.xlsx"))
            ).sheet_names
            echa_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("05_ECHA/ECHA_Results.xlsx"))
            ).sheet_names
            source_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("06_Source_Origin/Source_Origin_Results.xlsx"))
            ).sheet_names
            root_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("Auto_Query_Workflow_Results.xlsx"))
            ).sheet_names

            self.assertEqual(local_sheets, ["DF_Table"])
            self.assertEqual(identifier_sheets, ["Identifier_Completion"])
            self.assertEqual(
                epa_sheets,
                [
                    "Product_Use_Categories",
                    "Functional_Uses_Predicted",
                    "Functional_Uses_Reported",
                    "EPA_Predicted_Pie_Data",
                    "EPA_Reported_Pie_Data",
                ],
            )
            self.assertEqual(echa_sheets, ["ECHA_Uses_Reported", "ECHA_Reported_Pie_Data"])
            self.assertEqual(source_sheets, ["Source_Origin_Pie_Data"])
            self.assertNotIn("CompTox_Candidates", root_sheets)
            self.assertNotIn("ECHA_Use_Candidates", root_sheets)

    def test_root_workbook_excludes_internal_candidate_tables(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [
                    ("CompTox_Candidates", _example_comptox_candidates()),
                    ("ECHA_Use_Candidates", _example_echa_candidates()),
                    ("Product_Use_Categories", _example_comptox_candidates().iloc[[0]].copy()),
                    ("ECHA_Uses_Reported", _example_echa_candidates()),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        sheets = pd.ExcelFile(build_auto_workflow_workbook(result)).sheet_names

        self.assertIn("Product_Use_Categories", sheets)
        self.assertIn("ECHA_Uses_Reported", sheets)
        self.assertNotIn("CompTox_Candidates", sheets)
        self.assertNotIn("ECHA_Use_Candidates", sheets)

    def test_root_and_module_workbooks_use_exact_public_table_allowlists(self):
        public_by_module = OrderedDict(
            [
                (
                    "01_Local_Screening/Local_Screening_Results.xlsx",
                    (
                        "Structure_Preparation",
                        "Input_Check",
                        "Elemental_Ratios_DBE",
                        "Category_Summary",
                        "DF_Table",
                        "Sample_Peak_Area",
                        "Group_Area_Raw_Long",
                        "Group_Area_Mean_By_Sample",
                        "Plot_Warnings",
                    ),
                ),
                (
                    "02_Identifier_Completion/Identifier_Completion_Results.xlsx",
                    ("Identifier_Completion", "Identifier_Warnings"),
                ),
                (
                    "03_EPI_Suite/EPI_Suite_Results.xlsx",
                    ("EPI_Results", "EPI_Raw_Results", "EPI_Errors"),
                ),
                (
                    "04_EPA_CompTox/EPA_CompTox_Results.xlsx",
                    (
                        "CompTox_Summary",
                        "Product_Use_Categories",
                        "Functional_Uses_Predicted",
                        "Functional_Uses_Reported",
                        "EPA_Predicted_Pie_Data",
                        "EPA_Reported_Pie_Data",
                        "CompTox_Errors",
                    ),
                ),
                (
                    "05_ECHA/ECHA_Results.xlsx",
                    (
                        "ECHA_Use_Summary",
                        "ECHA_Uses_Reported",
                        "ECHA_Reported_Pie_Data",
                        "ECHA_Use_Dossiers",
                        "ECHA_Use_Errors",
                        "ECHA_GHS_Summary",
                        "ECHA_GHS_Classifications",
                        "ECHA_GHS_Errors",
                    ),
                ),
                (
                    "06_Source_Origin/Source_Origin_Results.xlsx",
                    (
                        "Source_Origin_Summary",
                        "Source_Origin_Evidence",
                        "Source_Origin_Errors",
                        "Source_Origin_Pie_Data",
                    ),
                ),
                (
                    "07_Pov_LRTP_PBM_ToxPi/Pov_LRTP_PBM_ToxPi_Results.xlsx",
                    (
                        "Pov_LRTP_Input",
                        "Pov_LRTP",
                        "ToxPi_Input",
                        "ToxPi_Normalized",
                        "ToxPi_Results",
                    ),
                ),
            ]
        )
        root_only = ("Identifier_Input", "EPI_Input", "Warnings")
        public_tables = tuple(
            dict.fromkeys(
                [name for names in public_by_module.values() for name in names]
                + list(root_only)
            )
        )
        injected = OrderedDict(
            (name, pd.DataFrame({"value": [name]})) for name in public_tables
        )
        injected.update(
            {
                "CompTox_Candidates": pd.DataFrame({"value": ["internal"]}),
                "ECHA_Use_Candidates": pd.DataFrame({"value": ["internal"]}),
                "ECHA_Use_Rose_Plot": pd.DataFrame({"value": ["obsolete"]}),
                "EPA_Arbitrary_Extra": pd.DataFrame({"value": ["stale"]}),
                "ECHA_Arbitrary_Extra": pd.DataFrame({"value": ["stale"]}),
                "Unknown_External_Table": pd.DataFrame({"value": ["external"]}),
            }
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=injected,
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        root_sheets = set(pd.ExcelFile(build_auto_workflow_workbook(result)).sheet_names)
        self.assertEqual(
            root_sheets,
            {"Run_Log", "Representative_Input", *public_tables},
        )

        package = build_auto_workflow_zip(result, charts=OrderedDict())
        with zipfile.ZipFile(package) as archive:
            for workbook_path, expected_tables in public_by_module.items():
                sheets = pd.ExcelFile(io.BytesIO(archive.read(workbook_path))).sheet_names
                self.assertEqual(sheets, list(expected_tables))

    def test_chart_map_and_zip_use_exact_chart_allowlists(self):
        allowed_charts = (
            "Local_Chemical_Type_Distribution",
            "Local_DBE_Bubble_Plot",
            "Local_Van_Krevelen_Plot",
            "EPA_Product_Use_Category_Rose_Plot",
            "EPA_Top_Predicted_Functional_Use",
            "EPA_Reported_Functional_Use_Distribution",
            "EPA_Reported_Functional_Use_Evidence",
            "ECHA_Reported_Use_Distribution",
            "ECHA_Reported_Use_Evidence",
            "Source_Origin_Distribution",
        )
        stale_charts = (
            "Local_Unknown_Chart",
            "EPA_Arbitrary_Extra",
            "ECHA_Use_Rose_Plot",
            "ECHA_Arbitrary_Extra",
            "Source_Origin_Stale",
            "External_Chart",
        )
        chart = AutoWorkflowChart("chart", b"\x89PNG\r\n\x1a\n", b"%PDF-1.4")
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
            charts=OrderedDict((key, chart) for key in (*allowed_charts, *stale_charts)),
        )

        charts = build_auto_workflow_charts(result)

        self.assertEqual(set(charts), set(allowed_charts))
        package = build_auto_workflow_zip(result, charts=result.charts)
        with zipfile.ZipFile(package) as archive:
            figure_stems = {
                Path(name).stem
                for name in archive.namelist()
                if "/figures/" in name
            }
        self.assertEqual(
            figure_stems,
            {key.removeprefix("Local_") for key in allowed_charts},
        )

    def test_module_workbooks_keep_empty_public_split_sheets(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [
                    (
                        "Functional_Uses_Reported",
                        pd.DataFrame(columns=["compound", "raw_use"]),
                    ),
                    (
                        "ECHA_Uses_Reported",
                        pd.DataFrame(columns=["compound", "category"]),
                    ),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        package = build_auto_workflow_zip(result, charts=OrderedDict())

        with zipfile.ZipFile(package) as archive:
            epa_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("04_EPA_CompTox/EPA_CompTox_Results.xlsx"))
            ).sheet_names
            echa_sheets = pd.ExcelFile(
                io.BytesIO(archive.read("05_ECHA/ECHA_Results.xlsx"))
            ).sheet_names
        self.assertEqual(epa_sheets, ["Functional_Uses_Reported"])
        self.assertEqual(echa_sheets, ["ECHA_Uses_Reported"])

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
        self.assertIn('"Product_Use_Categories"', page_text)
        self.assertIn('"ECHA_Uses_Reported"', page_text)
        self.assertIn('"Source_Origin_Pie_Data"', page_text)
        self.assertNotIn('"CompTox_Candidates"', page_text)
        self.assertNotIn('"ECHA_Use_Candidates"', page_text)

    def test_page_6_routes_plot_warnings_to_screening_audit_tables(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        screening_definition = page_text.split('"screening"', 1)[1].split('"identifier"', 1)[0]

        self.assertIn('"Plot_Warnings"', screening_definition)
        self.assertIn('"Plot_Warnings"', page_text.split("def _is_audit_table", 1)[1])

    def test_page_6_assigns_local_screening_charts_to_local_tab(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        screening_definition = page_text.split('"screening"', 1)[1].split('"identifier"', 1)[0]

        self.assertIn('("Local_",)', screening_definition)

    def test_page_6_renders_module_dashboard_without_removing_exports(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertIn("def _render_result_dashboard", page_text)
        self.assertIn("st.tabs", page_text)
        self.assertIn("_result_dashboard_groups(result, charts)", page_text)
        self.assertIn('st.selectbox("查看结果表"', page_text)
        self.assertIn("Auto_Query_Workflow_Results.zip", page_text)


    def test_page_6_renders_detailed_overall_and_module_progress(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertIn("build_selected_steps", page_text)
        self.assertIn("format_activity_message", page_text)
        self.assertIn("总体进度", page_text)
        self.assertIn("当前模块进度", page_text)
        self.assertIn("activity_callback=update_activity", page_text)


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


def _workflow_input_rows(compounds):
    return pd.DataFrame(
        {
            "Name": compounds,
            "NIST Lib Hit Formula": ["C2H6O"] * len(compounds),
            "Avg TIC": [100.0] * len(compounds),
        }
    )


def _completed_identifier_rows(compounds):
    return pd.DataFrame(
        {
            "compound": compounds,
            "smiles": ["CCO"] * len(compounds),
            "cas": ["64-17-5"] * len(compounds),
            "ec": ["200-578-6"] * len(compounds),
            "dtxsid": ["DTXSID9020584"] * len(compounds),
            "echa_id": ["100.000.526"] * len(compounds),
        }
    )


def _example_pie_tables():
    universe = build_compound_universe(
        pd.DataFrame({"compound": ["Compound A", "Compound B", "Compound C"]})
    )
    comptox_candidates = _example_comptox_candidates()
    echa_candidates = _example_echa_candidates()
    source_summary = pd.DataFrame(
        {
            "compound": ["Compound A", "Compound B"],
            "人为源证据数": [2, 0],
            "天然源证据数": [0, 1],
        }
    )
    return OrderedDict(
        [
            (
                "EPA_Predicted_Pie_Data",
                extract_top_predicted_functional_use_data(
                    comptox_candidates,
                    compound_universe=universe,
                ),
            ),
            (
                "EPA_Reported_Pie_Data",
                extract_top_reported_functional_use_data(
                    comptox_candidates,
                    universe,
                    source_label="EPA FC reported",
                    source_type="functional_use",
                    use_key="raw",
                    require_reported_flag=True,
                ),
            ),
            (
                "ECHA_Reported_Pie_Data",
                extract_top_reported_functional_use_data(
                    echa_candidates,
                    universe,
                    source_label="ECHA reported",
                    use_key="category",
                    require_reported_flag=False,
                ),
            ),
            (
                "Source_Origin_Pie_Data",
                extract_source_origin_pie_data(source_summary, universe),
            ),
        ]
    )


if __name__ == "__main__":
    unittest.main()
