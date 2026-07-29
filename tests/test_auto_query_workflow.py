from collections import OrderedDict
import ast
import base64
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import importlib
import importlib.util
import io
import json
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import zipfile
import unittest
from unittest.mock import patch

import pandas as pd
import src.auto_query_workflow as auto_query_workflow
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
from streamlit.testing.v1 import AppTest

from src.auto_query_checkpoint import (
    CheckpointStorageError,
    cleanup_expired_checkpoints,
    delete_checkpoint,
    generate_run_token,
    load_checkpoint,
    save_checkpoint,
)
from src.auto_query_workflow import (
    AutoWorkflowCheckpoint,
    AutoWorkflowCheckpointContext,
    AutoWorkflowConfig,
    AutoWorkflowChart,
    AutoWorkflowEpiRetryError,
    AutoWorkflowMapping,
    AutoWorkflowModuleWorkbook,
    AutoWorkflowPreparedInput,
    AutoWorkflowResult,
    LocalScreeningOutput,
    R_DF_STEP_LABEL,
    _build_identifier_input_from_epi_universe,
    _load_local_screening_charts,
    _query_input_from_identifiers,
    auto_input_from_multi_file_result,
    build_auto_workflow_charts,
    build_auto_workflow_module_workbook,
    build_auto_workflow_partial_zip,
    build_auto_workflow_workbook,
    build_auto_workflow_zip,
    build_representative_table,
    detect_default_mapping,
    retry_auto_workflow_epi_failures,
    run_auto_query_workflow,
)
from src.cp_screening_workflow import PBMToxPiConfig
from src.mol_structure_parser import prepare_structure_dataframe
from src.multi_file_screening import MultiFileScreeningResult
from src.upload_state import upload_signature
from src.use_rose_plot import (
    build_compound_universe,
    extract_top_product_use_category_data,
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


def _app_test_workbook_bytes(compound=None):
    buffer = io.BytesIO()
    compounds = (
        [compound]
        if compound is not None
        else ["Compound A", "Compound B"]
    )
    pd.DataFrame(
        {
            "Name": compounds,
            "NIST Lib Hit Formula": ["C2H6O", "C3H8O"][: len(compounds)],
            "Avg TIC": [10.0, 20.0][: len(compounds)],
            "Group Area 1": [1.0, 2.0][: len(compounds)],
        }
    ).to_excel(buffer, index=False)
    return buffer.getvalue()


def _app_test_epi_workbook_bytes():
    buffer = io.BytesIO()
    complete_epi_rows(["Compound A"]).to_excel(
        buffer,
        sheet_name="Core_Summary",
        index=False,
    )
    return buffer.getvalue()


def _app_test_epi_datetime_header_workbook_bytes(endpoint_header):
    buffer = io.BytesIO()
    pd.DataFrame(
        {
            "compound": ["Compound A"],
            endpoint_header: [0.25],
        }
    ).to_excel(
        buffer,
        sheet_name="Manual Results",
        index=False,
    )
    return buffer.getvalue()


def _app_test_with_cached_workbook():
    upload = {"name": "smoke.xlsx", "bytes": _app_test_workbook_bytes()}
    return _app_test_with_cached_workbooks([(upload["name"], upload["bytes"])])


def _app_test_with_cached_workbooks(workbooks):
    uploads = [
        {"name": file_name, "bytes": content_bytes}
        for file_name, content_bytes in workbooks
    ]
    app = AppTest.from_file("pages/6_一键批量查询.py", default_timeout=20)
    app.session_state["auto_query_input_files"] = uploads
    app.session_state["auto_query_input_signature"] = upload_signature(uploads)
    return app.run(timeout=20)


def _app_test_download_payload(download_button, storage):
    return storage.get_file(Path(download_button.proto.url).name).content


def _capture_app_test_media_storage(storages):
    def create_storage(endpoint):
        storage = MemoryMediaFileStorage(endpoint)
        storages.append(storage)
        return storage

    return patch(
        "streamlit.testing.v1.app_test.MemoryMediaFileStorage",
        side_effect=create_storage,
    )


@contextmanager
def _isolated_page_checkpoint_storage(root):
    root = Path(root)

    def isolated_save(*args, **kwargs):
        kwargs["root"] = root
        return save_checkpoint(*args, **kwargs)

    def isolated_load(*args, **kwargs):
        kwargs["root"] = root
        return load_checkpoint(*args, **kwargs)

    def isolated_delete(*args, **kwargs):
        kwargs["root"] = root
        return delete_checkpoint(*args, **kwargs)

    def isolated_cleanup(*args, **kwargs):
        kwargs["root"] = root
        return cleanup_expired_checkpoints(*args, **kwargs)

    with (
        patch(
            "src.auto_query_checkpoint.save_checkpoint",
            side_effect=isolated_save,
        ),
        patch(
            "src.auto_query_checkpoint.load_checkpoint",
            side_effect=isolated_load,
        ),
        patch(
            "src.auto_query_checkpoint.delete_checkpoint",
            side_effect=isolated_delete,
        ),
        patch(
            "src.auto_query_checkpoint.cleanup_expired_checkpoints",
            side_effect=isolated_cleanup,
        ) as cleanup_mock,
    ):
        yield SimpleNamespace(cleanup=cleanup_mock)


class AutoQueryWorkflowTests(unittest.TestCase):
    def test_external_query_input_retains_prepared_identity_key(self):
        universe = pd.DataFrame(
            {
                "compound": ["Shared"],
                "cas": ["64-17-5"],
                "identity_key": ["cas:64-17-5"],
            }
        )
        identifier_input = _build_identifier_input_from_epi_universe(universe)
        self.assertEqual(
            identifier_input["input_identity_key"].tolist(),
            ["cas:64-17-5"],
        )

        completed = identifier_input.copy()
        completed["dtxsid"] = "DTXSID7020005"
        query_input = _query_input_from_identifiers(identifier_input, completed)
        self.assertEqual(
            query_input["input_identity_key"].tolist(),
            ["cas:64-17-5"],
        )

    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_epa_checkpoint_contains_available_per_file_charts(
        self,
        identifier_batch,
        comptox_batch,
    ):
        mappings = pd.DataFrame(
            {
                "file_name": ["A.xlsx", "B.xlsx"],
                "sample_id": ["A", "B"],
                "safe_export_name": ["A", "B"],
            }
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["A.xlsx", "B.xlsx"],
                "sample_id": ["A", "B"],
                "identity_key": ["cas:a", "cas:b"],
                "compound": ["Only A", "Only B"],
                "cas": ["1-00-0", "2-00-0"],
                "smiles": ["A", "B"],
            }
        )
        universe = pd.DataFrame(
            {
                "identity_key": ["cas:a", "cas:b"],
                "compound": ["Only A", "Only B"],
                "cas": ["1-00-0", "2-00-0"],
                "smiles": ["A", "B"],
            }
        )
        completed = pd.DataFrame(
            {
                "compound": ["Only A", "Only B"],
                "smiles": ["A", "B"],
                "cas": ["1-00-0", "2-00-0"],
                "ec": ["", ""],
                "dtxsid": ["", ""],
                "echa_id": ["", ""],
            }
        )
        identifier_batch.return_value = (completed, pd.DataFrame())
        candidates = pd.DataFrame(
            {
                "input_identity_key": [
                    "cas:a",
                    "cas:a",
                    "cas:b",
                    "cas:unknown",
                ],
                "compound": ["Only A", "Only A", "Only B", "Unknown"],
                "source_type": ["product_category"] * 4,
                "raw_use": ["A use", "A use", "B use", "Unknown use"],
                "use_cn": ["A use", "A use", "B use", "Unknown use"],
                "query_source": ["名称", "SMILES", "名称", "名称"],
            }
        )
        comptox_batch.return_value = (
            pd.DataFrame(
                {
                    "input_identity_key": [
                        "cas:a",
                        "cas:b",
                        "cas:unknown",
                    ],
                    "compound": ["Only A", "Only B", "Unknown"],
                }
            ),
            candidates,
            pd.DataFrame(columns=["input_identity_key"]),
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(),
            prepared_input=pd.DataFrame(),
            representative_table=pd.DataFrame(
                {
                    "Name": ["Only A", "Only B"],
                    "formula": ["A", "B"],
                    "Group_Area": [2.0, 1.0],
                }
            ),
            local_tables=OrderedDict(
                [
                    ("Input_File_Mappings", mappings),
                    ("EPI_Primary_Membership", membership),
                ]
            ),
            primary_membership=membership,
            epi_universe=universe,
        )
        checkpoints = []

        run_auto_query_workflow(
            pd.DataFrame(),
            AutoWorkflowConfig(
                run_r_replicate_df=True,
                run_identifier=False,
                run_comptox=True,
            ),
            prepared_input=prepared,
            checkpoint_context=AutoWorkflowCheckpointContext(
                run_id="run",
                input_signature="input",
                settings_signature="settings",
                selected_steps=("EPA CompTox 用途",),
            ),
            checkpoint_callback=checkpoints.append,
        )

        epa_checkpoint = next(
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.current_step
            and "CompTox" in checkpoint.current_step
        )
        self.assertIn(
            "comptox_use__A__EPA_Product_Use_Category_Distribution",
            epa_checkpoint.result.charts,
        )
        self.assertIn(
            "comptox_use__B__EPA_Product_Use_Category_Distribution",
            epa_checkpoint.result.charts,
        )
        root_pie = epa_checkpoint.result.tables["EPA_PUC_Pie_Data"]
        only_a = root_pie.loc[root_pie["compound"].eq("Only A")].iloc[0]
        self.assertEqual(only_a["evidence_count"], 1)
        self.assertTrue(
            (
                epa_checkpoint.result.warnings["stage"]
                == "File assignment"
            ).any()
        )
        self.assertTrue(
            epa_checkpoint.result.warnings["message"]
            .str.contains("unassigned")
            .any()
        )

    def test_page_6_storage_switch_detaches_without_deleting_checkpoint(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        start = page_text.index(
            "def _detach_auto_query_recovery_for_storage_change():"
        )
        end = page_text.index("\n\n", start)
        function_text = page_text[start:end]
        self.assertIn(
            'st.session_state.pop("auto_query_run_token", None)',
            function_text,
        )
        self.assertIn('st.query_params.pop("run", None)', function_text)
        self.assertNotIn("delete_checkpoint(", function_text)

    def test_page_6_selects_storage_before_checkpoint_recovery(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        storage_index = page_text.index("render_storage_location_controls(")
        cleanup_index = page_text.index("cleanup_expired_checkpoints()")
        load_index = page_text.index("loaded = load_checkpoint(recovery_token)")
        self.assertLess(storage_index, cleanup_index)
        self.assertLess(storage_index, load_index)

    def test_page_6_clears_only_epi_supplements_and_preserves_primary_and_pool(self):
        app = _app_test_with_cached_workbooks(
            [("Lake-A.xlsx", _app_test_workbook_bytes("Compound A"))]
        )
        supplement = {
            "name": "Lake-A_EPI.xlsx",
            "bytes": _app_test_epi_workbook_bytes(),
        }
        app.session_state["auto_query_epi_supplement_files"] = [supplement]
        app.session_state["auto_query_epi_supplement_signature"] = (
            upload_signature([supplement])
        )
        next(
            box
            for box in app.checkbox
            if box.label == "EPI Suite 环境归趋"
        ).check().run()
        app.session_state["auto_query_workflow_result"] = object()
        app.session_state["auto_query_checkpoint_manifest"] = {"old": True}
        pool_state = {
            "version": 1,
            "contributors": {
                "keep": {
                    "results": [{"compound": "Pool compound"}],
                    "provenance": [],
                }
            },
        }
        app.session_state["shared_epi_result_pool"] = pool_state
        previous_epoch = (
            app.session_state[
                "auto_query_epi_supplement_uploader_epoch"
            ]
            if "auto_query_epi_supplement_uploader_epoch"
            in app.session_state
            else 0
        )

        clear_button = next(
            button
            for button in app.button
            if button.label == "仅清空 EPI 补充文件"
        )
        clear_button.click().run()

        self.assertIn("auto_query_input_files", app.session_state)
        self.assertNotIn("auto_query_epi_supplement_files", app.session_state)
        self.assertNotIn(
            "auto_query_epi_supplement_signature",
            app.session_state,
        )
        self.assertNotIn("auto_query_workflow_result", app.session_state)
        self.assertNotIn("auto_query_checkpoint_manifest", app.session_state)
        self.assertEqual(
            app.session_state["shared_epi_result_pool"],
            pool_state,
        )
        self.assertEqual(
            app.session_state[
                "auto_query_epi_supplement_uploader_epoch"
            ],
            previous_epoch + 1,
        )
        self.assertFalse(
            any(
                str(key).startswith("auto_epi_")
                for key in app.session_state.filtered_state
            )
        )

    def test_page_6_isolates_bad_epi_supplement_and_keeps_valid_file(self):
        app = _app_test_with_cached_workbooks(
            [("Lake-A.xlsx", _app_test_workbook_bytes("Compound A"))]
        )
        supplements = [
            {"name": "broken.xlsx", "bytes": b"not an excel workbook"},
            {
                "name": "Lake-A_EPI.xlsx",
                "bytes": _app_test_epi_workbook_bytes(),
            },
        ]
        app.session_state["auto_query_epi_supplement_files"] = supplements
        app.session_state["auto_query_epi_supplement_signature"] = (
            upload_signature(supplements)
        )

        next(
            box
            for box in app.checkbox
            if box.label == "EPI Suite 环境归趋"
        ).check().run()

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(
            any("broken.xlsx 读取失败" in message.value for message in app.error)
        )
        self.assertTrue(
            any(
                expander.label == "Lake-A_EPI.xlsx"
                for expander in app.expander
            )
        )

    def test_page_6_signs_datetime_epi_header_mapping_without_losing_raw_header(self):
        endpoint_header = datetime(2026, 7, 25, 12, 30, 0)
        app = _app_test_with_cached_workbooks(
            [("Lake-A.xlsx", _app_test_workbook_bytes("Compound A"))]
        )
        supplement = {
            "name": "Lake-A_EPI.xlsx",
            "bytes": _app_test_epi_datetime_header_workbook_bytes(
                endpoint_header
            ),
        }
        app.session_state["auto_query_epi_supplement_files"] = [supplement]
        app.session_state["auto_query_epi_supplement_signature"] = (
            upload_signature([supplement])
        )

        next(
            box
            for box in app.checkbox
            if box.label == "EPI Suite 环境归趋"
        ).check().run()
        endpoint_mapping = next(
            selectbox
            for selectbox in app.selectbox
            if selectbox.label == "vapor_pressure_mm_hg"
        )
        endpoint_mapping.select(endpoint_header).run()

        self.assertEqual(len(app.exception), 0)
        self.assertIsInstance(
            app.session_state["auto_query_settings_signature"],
            str,
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_workflow_enforces_uploaded_primary_membership_before_epi_merge(
        self,
        run_identifier,
        run_epi,
    ):
        self.assertIn(
            "primary_membership",
            AutoWorkflowPreparedInput.__dataclass_fields__,
        )
        input_frame = _workflow_input_rows(["Only A", "Only B"])
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(
                compound_col="Name",
                formula_col="NIST Lib Hit Formula",
                peak_area_col="Avg TIC",
            ),
            prepared_input=input_frame,
            representative_table=build_representative_table(
                input_frame,
                AutoWorkflowMapping(
                    compound_col="Name",
                    formula_col="NIST Lib Hit Formula",
                    peak_area_col="Avg TIC",
                ),
            ),
            primary_membership=pd.DataFrame(
                {
                    "primary_file": ["A.xlsx", "B.xlsx"],
                    "compound": ["Only A", "Only B"],
                    "smiles": ["CC", "CCC"],
                    "cas": ["11-11-1", "22-22-2"],
                }
            ),
        )
        completed = _completed_identifier_rows(["Only A", "Only B"])
        completed["smiles"] = ["CC", "CCC"]
        completed["cas"] = ["11-11-1", "22-22-2"]
        run_identifier.return_value = (completed, pd.DataFrame())
        uploaded = complete_epi_rows(["Only B"])
        uploaded["smiles"] = "CCC"
        uploaded["cas"] = "22-22-2"
        uploaded["primary_file"] = "A.xlsx"
        run_epi.return_value = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        result = run_auto_query_workflow(
            input_frame,
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=False,
                run_epi=True,
                cache_enabled=False,
            ),
            prepared_input=prepared,
            epi_uploaded_results=uploaded,
        )

        retry_input = result.tables["EPI_Retry_Input"]
        self.assertIn("Only B", retry_input["compound"].tolist())
        audit = result.tables["EPI_Match_Audit"]
        self.assertIn(
            "association_mismatch",
            audit["match_status"].tolist(),
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_workflow_keeps_same_name_epi_identities_separate(
        self,
        run_identifier,
        run_epi,
    ):
        self.assertIn(
            "epi_universe",
            AutoWorkflowPreparedInput.__dataclass_fields__,
        )
        representative = pd.DataFrame(
            {
                "Name": ["Shared"],
                "formula": ["C2H6O"],
                "Group_Area": [100.0],
                "SMILES_input": ["CC"],
                "CAS_input": ["11-11-1"],
            }
        )
        epi_universe = pd.DataFrame(
            {
                "identity_key": ["cas:11-11-1", "cas:22-22-2"],
                "identity_status": ["resolved", "resolved"],
                "identity_candidates": ["[]", "[]"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", "22-22-2"],
            }
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["A.xlsx", "B.xlsx"],
                "identity_key": ["cas:11-11-1", "cas:22-22-2"],
                "identity_status": ["resolved", "resolved"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", "22-22-2"],
            }
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(),
            prepared_input=pd.DataFrame(),
            representative_table=representative,
            primary_membership=membership,
            epi_universe=epi_universe,
        )

        def preserve_identifier_rows(identifier_input, **kwargs):
            return identifier_input.copy(), pd.DataFrame()

        run_identifier.side_effect = preserve_identifier_rows
        run_epi.return_value = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        uploaded = complete_epi_rows(["Shared"])
        uploaded["smiles"] = "CCC"
        uploaded["cas"] = "22-22-2"
        uploaded["primary_file"] = "B.xlsx"

        result = run_auto_query_workflow(
            pd.DataFrame(),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_epi=True,
                cache_enabled=False,
            ),
            prepared_input=prepared,
            epi_uploaded_results=uploaded,
        )

        identifier_input = run_identifier.call_args.args[0]
        self.assertEqual(
            identifier_input["cas"].tolist(),
            ["11-11-1", "22-22-2"],
        )
        self.assertEqual(
            result.tables["EPI_Identity_Universe"][
                "identity_key"
            ].tolist(),
            ["cas:11-11-1", "cas:22-22-2"],
        )
        self.assertEqual(
            result.tables["EPI_Primary_Membership"][
                "identity_key"
            ].tolist(),
            ["cas:11-11-1", "cas:22-22-2"],
        )
        self.assertEqual(
            result.tables["EPI_Input"]["identity_key"].tolist(),
            ["cas:11-11-1", "cas:22-22-2"],
        )
        epi_results = result.tables["EPI_Results"].set_index("identity_key")
        self.assertEqual(len(epi_results), 2)
        self.assertFalse(
            bool(epi_results.loc["cas:11-11-1", "_source_matched"])
        )
        self.assertTrue(
            bool(epi_results.loc["cas:22-22-2", "_source_matched"])
        )
        self.assertEqual(
            result.tables["EPI_Retry_Input"]["identity_key"].tolist(),
            ["cas:11-11-1"],
        )
        self.assertEqual(
            result.tables["EPI_Match_Audit"]["identity_key"].tolist(),
            ["cas:22-22-2"],
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_workflow_enriches_partial_same_name_identity_without_overwriting_other(
        self,
        run_identifier,
        run_epi,
    ):
        representative = pd.DataFrame(
            {
                "Name": ["Shared"],
                "formula": ["C2H6O"],
                "Group_Area": [100.0],
                "SMILES_input": ["CC"],
                "CAS_input": ["11-11-1"],
            }
        )
        epi_universe = pd.DataFrame(
            {
                "identity_key": ["cas:11-11-1", "smiles:CCC"],
                "identity_status": ["resolved", "resolved"],
                "identity_candidates": ["[]", "[]"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", ""],
            }
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["A.xlsx", "B.xlsx"],
                "identity_key": ["cas:11-11-1", "smiles:CCC"],
                "identity_status": ["resolved", "resolved"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", ""],
            }
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(),
            prepared_input=pd.DataFrame(),
            representative_table=representative,
            primary_membership=membership,
            epi_universe=epi_universe,
        )

        def complete_partial_identity(identifier_input, **kwargs):
            completed = identifier_input.copy()
            completed.loc[completed["smiles"].eq("CCC"), "cas"] = "22-22-2"
            return completed, pd.DataFrame()

        run_identifier.side_effect = complete_partial_identity
        run_epi.return_value = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        uploaded = complete_epi_rows(["Shared"])
        uploaded["smiles"] = "CCC"
        uploaded["cas"] = "22-22-2"
        uploaded["primary_file"] = "B.xlsx"

        result = run_auto_query_workflow(
            pd.DataFrame(),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_epi=True,
                cache_enabled=False,
            ),
            prepared_input=prepared,
            epi_uploaded_results=uploaded,
        )

        epi_input = result.tables["EPI_Input"].set_index("identity_key")
        self.assertEqual(
            epi_input.loc["cas:11-11-1", "cas"],
            "11-11-1",
        )
        self.assertEqual(
            epi_input.loc["smiles:CCC", "cas"],
            "22-22-2",
        )
        results = result.tables["EPI_Results"].set_index("identity_key")
        self.assertFalse(
            bool(results.loc["cas:11-11-1", "_source_matched"])
        )
        self.assertTrue(
            bool(results.loc["smiles:CCC", "_source_matched"])
        )
        self.assertEqual(
            result.tables["EPI_Match_Audit"]["identity_key"].tolist(),
            ["smiles:CCC"],
        )

    @patch("src.auto_query_workflow._build_pbm_toxpi_output")
    @patch("src.auto_query_workflow.build_pbm_toxpi_input")
    @patch("src.auto_query_workflow.run_pov_lrtp_batch")
    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_formal_workflow_routes_representative_to_matching_epi_identity_for_pov(
        self,
        run_identifier,
        run_epi,
        run_pov,
        build_toxpi_input,
        build_toxpi_output,
    ):
        representative = pd.DataFrame(
            {
                "Name": ["Shared"],
                "formula": ["C3H8"],
                "Group_Area": [200.0],
                "SMILES_input": ["CCC"],
                "CAS_input": ["22-22-2"],
            }
        )
        epi_universe = pd.DataFrame(
            {
                "identity_key": ["cas:11-11-1", "cas:22-22-2"],
                "identity_status": ["resolved", "resolved"],
                "identity_candidates": ["[]", "[]"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", "22-22-2"],
            }
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["A.xlsx", "B.xlsx"],
                "sample_id": ["Sample A", "Sample B"],
                "source_row": [2, 2],
                "identity_key": ["cas:11-11-1", "cas:22-22-2"],
                "identity_status": ["resolved", "resolved"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", "22-22-2"],
            }
        )
        sample_rows = pd.DataFrame(
            {
                "source_sample_id": ["Sample A", "Sample B"],
                "sample_id": ["Sample A", "Sample B"],
                "compound": ["Shared", "Shared"],
                "peak_area": [100.0, 200.0],
            }
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(),
            prepared_input=pd.DataFrame(),
            representative_table=representative,
            primary_membership=membership,
            epi_universe=epi_universe,
            local_tables=OrderedDict(
                [
                    ("DF_Table", pd.DataFrame({"Name": ["Shared"]})),
                    ("Group_Area_Mean_By_Sample", sample_rows),
                ]
            ),
        )

        def complete_both_identities(identifier_input, **kwargs):
            completed = identifier_input.copy()
            completed["pubchem_cid"] = ["11", "22"]
            completed["pubchem_molecular_weight"] = [11.0, 22.0]
            completed["pubchem_formula"] = ["C2H6", "C3H8"]
            completed["pubchem_match_status"] = ["matched", "matched"]
            return completed, pd.DataFrame()

        run_identifier.side_effect = complete_both_identities
        uploaded = complete_epi_rows(["Shared", "Shared"])
        uploaded["smiles"] = ["CC", "CCC"]
        uploaded["cas"] = ["11-11-1", "22-22-2"]
        uploaded["molecular_weight"] = [111.0, 222.0]
        uploaded["log_kow"] = [1.0, 2.0]
        run_pov.side_effect = lambda frame: pd.DataFrame(
            {"Name": frame["Name"], "Scores": [1.0] * len(frame)}
        )
        build_toxpi_input.return_value = pd.DataFrame()
        build_toxpi_output.return_value = auto_query_workflow.PbmToxPiOutput(
            tables=OrderedDict(),
            charts=OrderedDict(),
        )

        result = run_auto_query_workflow(
            pd.DataFrame(),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_pov_lrtp_toxpi=True,
                cache_enabled=False,
            ),
            prepared_input=prepared,
            epi_uploaded_results=uploaded,
        )

        run_epi.assert_not_called()
        pov_input = result.tables["Pov_LRTP_Input"]
        self.assertEqual(len(pov_input), 1)
        self.assertEqual(pov_input.loc[0, "Compound_CID"], "22")
        self.assertEqual(pov_input.loc[0, "SMILES"], "CCC")
        self.assertEqual(pov_input.loc[0, "Molecular_Weight"], 222.0)
        self.assertEqual(pov_input.loc[0, "Log_Kow_used"], 2.0)
        downstream_input = run_pov.call_args.args[0]
        self.assertEqual(downstream_input.loc[0, "Compound_CID"], "22")
        self.assertEqual(downstream_input.loc[0, "Log_Kow_used"], 2.0)
        sample_input = build_toxpi_input.call_args.kwargs["peak_area_long"]
        self.assertEqual(len(sample_input), len(sample_rows))
        pd.testing.assert_frame_equal(sample_input, sample_rows)

    def test_page_6_accepts_multiple_primary_files_and_keeps_both_in_settings(self):
        app = _app_test_with_cached_workbooks(
            [
                ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
                ("Lake-B.xlsx", _app_test_workbook_bytes("Compound B")),
            ]
        )

        self.assertEqual(len(app.exception), 0)
        self.assertIn(
            "Lake-A.xlsx",
            app.session_state["auto_query_primary_file_names"],
        )
        self.assertIn(
            "Lake-B.xlsx",
            app.session_state["auto_query_primary_file_names"],
        )

    def test_page_6_blocks_duplicate_primary_filenames(self):
        app = _app_test_with_cached_workbooks(
            [
                ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
                ("Lake-A.xlsx", _app_test_workbook_bytes("Compound B")),
            ]
        )

        self.assertTrue(
            any("文件名重复" in message.value for message in app.error)
        )

    def test_page_6_blocks_duplicate_casefolded_sample_stems(self):
        app = _app_test_with_cached_workbooks(
            [
                ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
                ("lake-a.xls", _app_test_workbook_bytes("Compound B")),
            ]
        )

        self.assertTrue(
            any("样品名称重复" in message.value for message in app.error)
        )

    def test_page_6_shows_epi_supplement_controls_when_pov_is_selected(self):
        app = _app_test_with_cached_workbooks(
            [("Lake-A.xlsx", _app_test_workbook_bytes("Compound A"))]
        )

        next(
            box
            for box in app.checkbox
            if box.label == "Pov-LRTP / PBM / ToxPi"
        ).check().run()

        self.assertTrue(
            any(
                uploader.label == "上传 EPI 补充 Excel"
                for uploader in app.get("file_uploader")
            )
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_complete_epi_seed_skips_network(
        self,
        run_identifier,
        run_epi,
    ):
        run_identifier.return_value = (
            _completed_identifier_rows(["Compound A"]),
            pd.DataFrame(),
        )
        seed = complete_epi_rows(["Compound A"])
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(),
            prepared_input=_workflow_input_rows(["Compound A"]),
            representative_table=build_representative_table(
                _workflow_input_rows(["Compound A"]),
                AutoWorkflowMapping(
                    compound_col="Name",
                    formula_col="NIST Lib Hit Formula",
                    peak_area_col="Avg TIC",
                ),
            ),
            local_tables=OrderedDict(),
            local_charts=OrderedDict(),
            local_warnings=[],
        )

        result = run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
            ),
            prepared_input=prepared,
            epi_uploaded_results=seed,
        )

        run_epi.assert_not_called()
        self.assertEqual(
            result.tables["EPI_Results"]["compound"].tolist(),
            ["Compound A"],
        )
        self.assertTrue(
            result.tables["EPI_Completeness"]["needs_query"].eq(False).all()
        )
        expected_epi_sheets = {
            "EPI_Results",
            "EPI_Raw_Results",
            "EPI_Errors",
            "EPI_Completeness",
            "EPI_Source_Provenance",
            "EPI_Match_Audit",
            "EPI_Conflict_Audit",
            "EPI_Query_Attempts",
            "EPI_Retry_Input",
        }
        root_sheets = set(
            pd.ExcelFile(build_auto_workflow_workbook(result)).sheet_names
        )
        self.assertTrue(expected_epi_sheets.issubset(root_sheets))
        module = build_auto_workflow_module_workbook(
            result,
            "EPI Suite 环境归趋",
        )
        self.assertIsNotNone(module)
        module_sheets = set(
            pd.ExcelFile(io.BytesIO(module.data)).sheet_names
        )
        self.assertEqual(module_sheets, expected_epi_sheets)

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_partial_epi_seed_queries_only_missing_compounds(
        self,
        run_identifier,
        run_epi,
    ):
        compounds = ["Compound A", "Compound B"]
        run_identifier.return_value = (
            _completed_identifier_rows(compounds),
            pd.DataFrame(),
        )
        run_epi.return_value = (
            complete_epi_rows(["Compound B"]),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        result = run_auto_query_workflow(
            _workflow_input_rows(compounds),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
            ),
            epi_uploaded_results=complete_epi_rows(["Compound A"]),
        )

        self.assertEqual(
            run_epi.call_args.args[0]["compound"].tolist(),
            ["Compound B"],
        )
        self.assertTrue(
            result.tables["EPI_Completeness"]["needs_query"].eq(False).all()
        )

    @patch("src.auto_query_workflow._run_pov_lrtp_toxpi")
    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_multi_file_pool_failure_exports_and_checkpoint_round_trip(
        self,
        run_identifier,
        run_epi,
        run_pov,
    ):
        compounds = ["Uploaded A", "Pool B", "Network C"]
        representative = pd.DataFrame(
            {
                "Name": compounds,
                "formula": ["C2H6", "C3H8", "C4H10"],
                "Group_Area": [100.0, 200.0, 300.0],
            }
        )
        sample_rows = pd.DataFrame(
            {
                "source_sample_id": [
                    "Lake-A",
                    "Lake-A",
                    "Lake-A",
                    "Lake-B",
                    "Lake-B",
                    "Lake-B",
                ],
                "sample_id": [
                    "Lake-A",
                    "Lake-A",
                    "Lake-A",
                    "Lake-B",
                    "Lake-B",
                    "Lake-B",
                ],
                "compound": compounds * 2,
                "peak_area": [10.0, 20.0, 30.0, 15.0, 25.0, 35.0],
            }
        )
        mappings = pd.DataFrame(
            {
                "source_file": ["Lake-A.xlsx", "Lake-B.xlsx"],
                "sample_id": ["Lake-A", "Lake-B"],
                "compound_col": ["Name", "Name"],
                "formula_col": ["formula", "formula"],
            }
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(
                compound_col="Name",
                formula_col="formula",
                peak_area_col="Group_Area",
                group_area_cols=["Group_Area"],
            ),
            prepared_input=representative.copy(),
            representative_table=representative,
            local_tables=OrderedDict(
                [
                    ("Input_File_Mappings", mappings),
                    (
                        "DF_Table",
                        pd.DataFrame(
                            {
                                "compound": compounds,
                                "DF": [2, 2, 2],
                            }
                        ),
                    ),
                    ("Group_Area_Mean_By_Sample", sample_rows),
                ]
            ),
        )
        identifiers = pd.DataFrame(
            {
                "compound": compounds,
                "smiles": ["CC", "CCC", "CCCC"],
                "cas": ["11-11-1", "22-22-2", "33-33-3"],
                "ec": ["", "", ""],
                "dtxsid": ["", "", ""],
                "echa_id": ["", "", ""],
            }
        )
        run_identifier.return_value = (identifiers, pd.DataFrame())

        uploaded = complete_epi_rows(["Uploaded A"])
        uploaded.loc[:, "smiles"] = "CC"
        uploaded.loc[:, "cas"] = "11-11-1"
        pool = complete_epi_rows(["Pool B"])
        pool.loc[:, "smiles"] = "CCC"
        pool.loc[:, "cas"] = "22-22-2"
        failed_network = complete_epi_rows(["Network C"])
        failed_network.loc[:, "smiles"] = "CCCC"
        failed_network.loc[:, "cas"] = "33-33-3"
        failed_network.loc[:, "status"] = "failed"

        def fail_network_c(query_input, **kwargs):
            self.assertEqual(
                query_input["compound"].tolist(),
                ["Network C"],
            )
            kwargs["activity_callback"](
                {
                    "event": "failed",
                    "index": 0,
                    "attempt": 1,
                    "label": "Network C",
                    "error": "simulated transient exhaustion",
                }
            )
            return (
                failed_network,
                pd.DataFrame(
                    {
                        "compound": ["Network C"],
                        "status": ["failed"],
                    }
                ),
                pd.DataFrame(
                    {
                        "compound": ["Network C"],
                        "error": ["simulated transient exhaustion"],
                    }
                ),
            )

        run_epi.side_effect = fail_network_c
        run_pov.return_value = auto_query_workflow.PbmToxPiOutput(
            tables=OrderedDict(
                [
                    (
                        "Pov_LRTP",
                        pd.DataFrame(
                            {
                                "Name": ["Uploaded A", "Pool B"],
                                "Scores": [1.0, 2.0],
                            }
                        ),
                    )
                ]
            ),
            charts=OrderedDict(),
        )

        result = run_auto_query_workflow(
            representative,
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                run_pov_lrtp_toxpi=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
            ),
            prepared_input=prepared,
            epi_uploaded_results=uploaded,
            epi_pool_results=pool,
        )

        run_epi.assert_called_once()
        run_pov.assert_called_once()
        pd.testing.assert_frame_equal(
            run_pov.call_args.args[3]["Group_Area_Mean_By_Sample"],
            sample_rows,
        )
        self.assertEqual(
            result.tables["EPI_Retry_Input"]["compound"].tolist(),
            ["Network C"],
        )
        provenance = result.tables["EPI_Source_Provenance"]
        pool_provenance = provenance.loc[
            provenance["compound"].eq("Pool B")
        ]
        self.assertFalse(pool_provenance.empty)
        self.assertTrue(
            pool_provenance["source_type"].eq("session_pool").all()
        )
        self.assertEqual(
            result.tables["EPI_Query_Attempts"]["label"].tolist(),
            ["Network C"],
        )

        required_epi_sheets = {
            "EPI_Results",
            "EPI_Raw_Results",
            "EPI_Errors",
            "EPI_Completeness",
            "EPI_Source_Provenance",
            "EPI_Match_Audit",
            "EPI_Conflict_Audit",
            "EPI_Query_Attempts",
            "EPI_Retry_Input",
        }
        epi_module = build_auto_workflow_module_workbook(
            result,
            "EPI Suite 环境归趋",
        )
        local_module = build_auto_workflow_module_workbook(
            result,
            R_DF_STEP_LABEL,
        )
        pov_module = build_auto_workflow_module_workbook(
            result,
            "Pov-LRTP / PBM / ToxPi",
        )
        self.assertIsNotNone(epi_module)
        self.assertIsNotNone(local_module)
        self.assertIsNotNone(pov_module)
        self.assertTrue(
            required_epi_sheets.issubset(
                set(pd.ExcelFile(io.BytesIO(epi_module.data)).sheet_names)
            )
        )
        root_sheets = set(
            pd.ExcelFile(build_auto_workflow_workbook(result)).sheet_names
        )
        self.assertTrue(
            {
                "Input_File_Mappings",
                *required_epi_sheets,
            }.issubset(root_sheets)
        )
        self.assertNotIn("Group_Area_Mean_By_Sample", root_sheets)
        self.assertNotIn("DF_Table", root_sheets)
        self.assertEqual(
            set(result.tables["Input_File_Mappings"]["source_file"]),
            {"Lake-A.xlsx", "Lake-B.xlsx"},
        )
        self.assertEqual(
            set(result.tables["Group_Area_Mean_By_Sample"]["sample_id"]),
            {"Lake-A", "Lake-B"},
        )

        with zipfile.ZipFile(build_auto_workflow_zip(result)) as archive:
            full_epi_sheets = set(
                pd.ExcelFile(
                    io.BytesIO(
                        archive.read(
                            "03_EPI_Suite/EPI_Suite_Results.xlsx"
                        )
                    )
                ).sheet_names
            )
            self.assertTrue(required_epi_sheets.issubset(full_epi_sheets))
            full_root_sheets = set(
                pd.ExcelFile(
                    io.BytesIO(
                        archive.read("Auto_Query_Workflow_Results.xlsx")
                    )
                ).sheet_names
            )
            self.assertIn("Input_File_Mappings", full_root_sheets)

        modules = OrderedDict(
            (
                module.slug,
                module,
            )
            for module in (local_module, epi_module, pov_module)
        )
        with zipfile.ZipFile(
            build_auto_workflow_partial_zip(result, modules)
        ) as archive:
            partial_epi_sheets = set(
                pd.ExcelFile(
                    io.BytesIO(
                        archive.read(
                            f"modules/{epi_module.file_name}"
                        )
                    )
                ).sheet_names
            )
            self.assertTrue(required_epi_sheets.issubset(partial_epi_sheets))

        with TemporaryDirectory() as checkpoint_root:
            token = generate_run_token()
            save_checkpoint(
                token,
                _checkpoint_for(result),
                ["Lake-A.xlsx", "Lake-B.xlsx"],
                modules,
                root=checkpoint_root,
            )
            loaded = load_checkpoint(token, root=checkpoint_root)

        self.assertEqual(
            loaded.input_filenames,
            ("Lake-A.xlsx", "Lake-B.xlsx"),
        )
        self.assertEqual(
            set(
                loaded.checkpoint.result.tables[
                    "Input_File_Mappings"
                ]["source_file"]
            ),
            {"Lake-A.xlsx", "Lake-B.xlsx"},
        )
        self.assertEqual(
            loaded.checkpoint.result.tables[
                "EPI_Retry_Input"
            ]["compound"].tolist(),
            ["Network C"],
        )
        loaded_pool_provenance = loaded.checkpoint.result.tables[
            "EPI_Source_Provenance"
        ]
        self.assertTrue(
            loaded_pool_provenance.loc[
                loaded_pool_provenance["compound"].eq("Pool B"),
                "source_type",
            ].eq("session_pool").all()
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_failures_queries_only_retry_input_and_preserves_unrelated_tables(
        self,
        run_epi,
    ):
        original = _result_with_epi_retry_input(["Failed B"])
        original.tables["CompTox_Summary"] = pd.DataFrame(
            {"compound": ["Unrelated"], "status": ["ok"]}
        )
        original.charts["EPA_Product_Use_Category_Distribution"] = (
            AutoWorkflowChart("Unrelated", b"epa-png", b"epa-pdf")
        )
        run_epi.return_value = (
            complete_epi_rows(["Failed B"]),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        with (
            patch("src.auto_query_workflow.run_identifier_completion_batch") as run_identifier,
            patch("src.auto_query_workflow.run_comptox_use_batch") as run_comptox,
            patch("src.auto_query_workflow.run_echa_use_batch") as run_echa_use,
            patch("src.auto_query_workflow.run_echa_ghs_batch") as run_echa_ghs,
            patch("src.auto_query_workflow.run_source_origin_batch") as run_source_origin,
        ):
            retried = retry_auto_workflow_epi_failures(
                original,
                AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
            )

        self.assertEqual(
            run_epi.call_args.args[0]["compound"].tolist(),
            ["Failed B"],
        )
        self.assertEqual(
            retried.tables["EPI_Results"]["compound"].tolist(),
            ["Seed A", "Failed B"],
        )
        pd.testing.assert_frame_equal(
            retried.tables["CompTox_Summary"],
            original.tables["CompTox_Summary"],
        )
        self.assertIs(
            retried.charts["EPA_Product_Use_Category_Distribution"],
            original.charts["EPA_Product_Use_Category_Distribution"],
        )
        self.assertTrue(retried.tables["EPI_Retry_Input"].empty)
        run_identifier.assert_not_called()
        run_comptox.assert_not_called()
        run_echa_use.assert_not_called()
        run_echa_ghs.assert_not_called()
        run_source_origin.assert_not_called()

    @patch("src.auto_query_workflow._run_pov_lrtp_toxpi")
    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_failures_rebuilds_only_epi_dependent_pov_outputs(
        self,
        run_epi,
        run_pov,
    ):
        original = _result_with_epi_retry_input(["Failed B"])
        original.tables["Pov_LRTP"] = pd.DataFrame({"version": ["old"]})
        original.tables["ToxPi_Input"] = pd.DataFrame({"version": ["stale"]})
        original.tables["CompTox_Summary"] = pd.DataFrame(
            {"compound": ["Unrelated"], "status": ["ok"]}
        )
        original.charts["ToxPi_Radial_Plot"] = AutoWorkflowChart(
            "Old radial",
            b"old-png",
            b"old-pdf",
        )
        original.charts["ToxPi_Ranking_Bar"] = AutoWorkflowChart(
            "Stale bar",
            b"stale-png",
            b"stale-pdf",
        )
        original.charts["EPA_Product_Use_Category_Distribution"] = (
            AutoWorkflowChart("Unrelated", b"epa-png", b"epa-pdf")
        )
        run_epi.return_value = (
            complete_epi_rows(["Failed B"]),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        replacement_table = pd.DataFrame({"version": ["new"]})
        replacement_chart = AutoWorkflowChart(
            "New radial",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            b"new-pdf",
        )
        run_pov.return_value = auto_query_workflow.PbmToxPiOutput(
            tables=OrderedDict([("Pov_LRTP", replacement_table)]),
            charts=OrderedDict([("ToxPi_Radial_Plot", replacement_chart)]),
        )

        retried = retry_auto_workflow_epi_failures(
            original,
            AutoWorkflowConfig(
                run_epi=True,
                run_pov_lrtp_toxpi=True,
                epi_delay_seconds=0,
            ),
        )

        run_pov.assert_called_once()
        pd.testing.assert_frame_equal(
            run_pov.call_args.args[2],
            retried.tables["EPI_Results"],
        )
        pd.testing.assert_frame_equal(
            retried.tables["Pov_LRTP"],
            replacement_table,
        )
        self.assertNotIn("ToxPi_Input", retried.tables)
        self.assertIs(
            retried.charts["ToxPi_Radial_Plot"],
            replacement_chart,
        )
        self.assertNotIn("ToxPi_Ranking_Bar", retried.charts)
        pd.testing.assert_frame_equal(
            retried.tables["CompTox_Summary"],
            original.tables["CompTox_Summary"],
        )
        self.assertIs(
            retried.charts["EPA_Product_Use_Category_Distribution"],
            original.charts["EPA_Product_Use_Category_Distribution"],
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_batch_exception_exposes_updated_attempt_audit(
        self,
        run_epi,
    ):
        original = _result_with_epi_retry_input(["Failed B"])
        self.assertTrue(original.tables["EPI_Query_Attempts"].empty)

        def fail_after_activity(*args, **kwargs):
            kwargs["activity_callback"](
                {
                    "event": "started",
                    "index": 0,
                    "attempt": 1,
                    "label": "Failed B",
                    "evidence": "retry request started",
                }
            )
            raise RuntimeError("simulated EPI retry failure")

        run_epi.side_effect = fail_after_activity

        with self.assertRaises(AutoWorkflowEpiRetryError) as caught:
            retry_auto_workflow_epi_failures(
                original,
                AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
            )

        updated = caught.exception.result
        self.assertEqual(len(updated.tables["EPI_Query_Attempts"]), 1)
        self.assertEqual(
            updated.tables["EPI_Query_Attempts"]["label"].tolist(),
            ["Failed B"],
        )
        self.assertEqual(
            updated.tables["EPI_Retry_Input"]["compound"].tolist(),
            ["Failed B"],
        )
        self.assertIn(
            "simulated EPI retry failure",
            str(caught.exception),
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_queries_name_only_and_smiles_rows(self, run_epi):
        original = _result_with_epi_retry_input(
            ["Queryable B", "Name only C"]
        )
        for table_name in ("EPI_Results", "EPI_Retry_Input"):
            table = original.tables[table_name].copy()
            name_only = table["compound"].eq("Name only C")
            table.loc[name_only, ["smiles", "cas"]] = ""
            original.tables[table_name] = table
        run_epi.return_value = (
            complete_epi_rows(["Queryable B", "Name only C"]),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        retry_auto_workflow_epi_failures(
            original,
            AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
        )

        self.assertEqual(
            run_epi.call_args.args[0]["compound"].tolist(),
            ["Queryable B", "Name only C"],
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_skips_rows_without_name_or_smiles(self, run_epi):
        original = _result_with_epi_retry_input([""])
        retry_input = original.tables["EPI_Retry_Input"].copy()
        retry_input["compound"] = " "
        retry_input["smiles"] = " "
        original.tables["EPI_Retry_Input"] = retry_input

        retried = retry_auto_workflow_epi_failures(
            original,
            AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
        )

        self.assertIs(retried, original)
        run_epi.assert_not_called()

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_epi_batch_exception_preserves_attempt_audit_and_seed_resolution(
        self,
        run_identifier,
        run_epi,
    ):
        compounds = ["Compound A", "Compound B"]
        run_identifier.return_value = (
            _completed_identifier_rows(compounds),
            pd.DataFrame(),
        )

        def fail_after_activity(*args, **kwargs):
            callback = kwargs["activity_callback"]
            callback(
                {
                    "event": "started",
                    "index": 0,
                    "attempt": 1,
                    "label": "Compound B",
                    "evidence": "seed incomplete",
                }
            )
            callback(
                {
                    "event": "failed",
                    "index": 0,
                    "attempt": 1,
                    "label": "Compound B",
                    "error": "EPI batch unavailable",
                }
            )
            raise RuntimeError("EPI batch unavailable")

        run_epi.side_effect = fail_after_activity
        result = run_auto_query_workflow(
            _workflow_input_rows(compounds),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
            ),
            epi_uploaded_results=complete_epi_rows(["Compound A"]),
        )

        attempts = result.tables["EPI_Query_Attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts.loc[0, "attempt"], 1)
        self.assertEqual(attempts.loc[0, "label"], "Compound B")
        self.assertEqual(attempts.loc[0, "error"], "EPI batch unavailable")
        self.assertEqual(attempts.loc[0, "evidence"], "seed incomplete")
        seeded = result.tables["EPI_Results"].set_index("compound").loc[
            "Compound A"
        ]
        self.assertEqual(seeded["log_kow"], 2.0)
        completeness = result.tables["EPI_Completeness"].set_index("compound")
        self.assertFalse(bool(completeness.loc["Compound A", "needs_query"]))
        self.assertTrue(bool(completeness.loc["Compound B", "needs_query"]))
        self.assertFalse(result.tables["EPI_Source_Provenance"].empty)
        self.assertFalse(result.tables["EPI_Match_Audit"].empty)

    def test_multi_file_adapter_preserves_sample_representative_and_audit_exports(self):
        representative = pd.DataFrame(
            {
                "Name": ["Compound A", "Compound B"],
                "formula": ["C2H6O", "C3H8O"],
                "Group_Area": [10.0, 20.0],
                "compound_key": ["compound a", "compound b"],
            }
        )
        mappings = pd.DataFrame(
            {
                "source_file": ["sample-1.xlsx", "sample-2.xlsx"],
                "sample_id": ["Sample 1", "Sample 2"],
            }
        )
        multi_file_result = MultiFileScreeningResult(
            normalized_samples=[],
            representative_table=representative,
            structure_preparation=pd.DataFrame(
                {
                    "sample_id": ["Sample 1", "Sample 2"],
                    "Name": ["Compound A", "Compound A"],
                }
            ),
            input_file_mappings=mappings,
            df_table=pd.DataFrame({"Name": ["Compound A"], "DF": [2]}),
            sample_peak_area=pd.DataFrame(
                {
                    "sample_id": ["Sample 1", "Sample 2"],
                    "Name": ["Compound A", "Compound A"],
                    "peak_area": [10.0, 20.0],
                }
            ),
            group_area_raw_long=pd.DataFrame(
                {
                    "sample_id": ["Sample 1", "Sample 2"],
                    "Name": ["Compound A", "Compound A"],
                    "Group_Area": [10.0, 20.0],
                }
            ),
            group_area_mean_by_sample=pd.DataFrame(
                {
                    "sample_id": ["Sample 1", "Sample 2"],
                    "Name": ["Compound A", "Compound A"],
                    "Group_Area": [10.0, 20.0],
                }
            ),
            tables={"Input_Check": pd.DataFrame({"sample_id": ["Sample 1"]})},
        )

        prepared = auto_input_from_multi_file_result(multi_file_result)
        result = run_auto_query_workflow(
            pd.DataFrame({"legacy": ["must not be normalized"]}),
            AutoWorkflowConfig(
                run_r_replicate_df=True,
                run_identifier=False,
            ),
            prepared_input=prepared,
        )

        pd.testing.assert_frame_equal(result.representative_table, representative)
        pd.testing.assert_frame_equal(
            result.tables["Input_File_Mappings"][mappings.columns],
            mappings,
        )
        self.assertEqual(
            result.tables["Input_File_Mappings"]["safe_export_name"].tolist(),
            ["sample_1", "sample_2"],
        )
        pd.testing.assert_frame_equal(
            result.tables["Sample_Peak_Area"],
            multi_file_result.sample_peak_area,
        )
        root_sheets = set(
            pd.ExcelFile(build_auto_workflow_workbook(result)).sheet_names
        )
        self.assertIn("Input_File_Mappings", root_sheets)
        module = build_auto_workflow_module_workbook(result, R_DF_STEP_LABEL)
        self.assertIsNotNone(module)
        self.assertIn(
            "Input_File_Mappings",
            pd.ExcelFile(io.BytesIO(module.data)).sheet_names,
        )

    @patch("src.auto_query_workflow._run_pov_lrtp_toxpi")
    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_prepared_multi_file_tables_feed_pov_with_sample_rows(
        self,
        run_identifier,
        run_epi,
        run_pov,
    ):
        representative = pd.DataFrame(
            {
                "Name": ["Compound A", "Compound B"],
                "formula": ["C2H6O", "C3H8O"],
                "Group_Area": [10.0, 20.0],
                "compound_key": ["compound a", "compound b"],
            }
        )
        sample_rows = pd.DataFrame(
            {
                "source_sample_id": [
                    "Sample 1",
                    "Sample 1",
                    "Sample 2",
                    "Sample 2",
                ],
                "sample_id": [
                    "Sample 1",
                    "Sample 1",
                    "Sample 2",
                    "Sample 2",
                ],
                "compound": [
                    "Compound A",
                    "Compound B",
                    "Compound A",
                    "Compound B",
                ],
                "peak_area": [10.0, 5.0, 20.0, 15.0],
            }
        )
        run_identifier.return_value = (
            _completed_identifier_rows(["Compound A", "Compound B"]),
            pd.DataFrame(),
        )
        run_pov.return_value = auto_query_workflow.PbmToxPiOutput(
            tables=OrderedDict(),
            charts=OrderedDict(),
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=AutoWorkflowMapping(
                compound_col="Name",
                formula_col="formula",
                peak_area_col="Group_Area",
                group_area_cols=["Group_Area"],
            ),
            prepared_input=representative.copy(),
            representative_table=representative,
            local_tables=OrderedDict(
                [
                    ("DF_Table", pd.DataFrame({"compound": ["Compound A", "Compound B"]})),
                    ("Group_Area_Mean_By_Sample", sample_rows),
                ]
            ),
        )

        run_auto_query_workflow(
            pd.DataFrame({"legacy": ["ignored"]}),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                run_pov_lrtp_toxpi=True,
                identifier_delay_seconds=0,
                epi_delay_seconds=0,
            ),
            prepared_input=prepared,
            epi_uploaded_results=complete_epi_rows(
                ["Compound A", "Compound B"]
            ),
        )

        run_epi.assert_not_called()
        pd.testing.assert_frame_equal(run_pov.call_args.args[0], representative)
        pd.testing.assert_frame_equal(
            run_pov.call_args.args[3]["Group_Area_Mean_By_Sample"],
            sample_rows,
        )

    def test_checkpoint_callback_contract_keeps_shared_result_frames_read_only(self):
        self.assertIn("read-only", AutoWorkflowCheckpoint.__doc__ or "")
        self.assertIn("must not mutate", AutoWorkflowCheckpoint.__doc__ or "")

        page_tree = ast.parse(
            Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        )
        handler = next(
            node
            for node in ast.walk(page_tree)
            if isinstance(node, ast.FunctionDef) and node.name == "handle_checkpoint"
        )

        def checkpoint_result_expression(node):
            return ast.unparse(node).startswith("checkpoint.result")

        mutations = []
        for node in ast.walk(handler):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                mutations.extend(
                    ast.unparse(target)
                    for target in targets
                    if checkpoint_result_expression(target)
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and checkpoint_result_expression(node.func.value)
                and node.func.attr
                in {
                    "drop",
                    "drop_duplicates",
                    "fillna",
                    "insert",
                    "pop",
                    "rename",
                    "replace",
                    "set_axis",
                    "sort_index",
                    "sort_values",
                    "update",
                }
            ):
                mutations.append(ast.unparse(node))

        self.assertEqual(mutations, [])

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

    def test_one_click_toxpi_output_contains_two_stage_tables_and_three_charts(self):
        self.assertTrue(hasattr(auto_query_workflow, "_build_pbm_toxpi_output"))
        toxpi_input = pd.DataFrame(
            {
                "compound": ["A", "B", "C", "D"],
                "Peak_Area": [1e8, 1e7, 1e6, 1e5],
                "Scores": [1.0, 4.0, 2.0, 3.0],
                "DF": [0.9, 0.4, 0.7, 0.2],
            }
        )

        output = auto_query_workflow._build_pbm_toxpi_output(
            toxpi_input,
            PBMToxPiConfig(candidate_top_n=4, display_top_n=2, n_iter=20, seed=5),
        )

        self.assertTrue(
            {
                "ToxPi_Global_Screen",
                "ToxPi_Normalized",
                "ToxPi_Results",
                "ToxPi_Display",
                "ToxPi_Settings",
                "ToxPi_Robustness",
                "ToxPi_Robust_Stats",
            }.issubset(output.tables)
        )
        self.assertEqual(
            set(output.charts),
            {
                "ToxPi_Radial_Plot",
                "ToxPi_Ranking_Bar",
                "ToxPi_Robustness_Histogram",
            },
        )

    def test_one_click_toxpi_charts_and_tables_are_exported_in_module_zip(self):
        self.assertTrue(hasattr(auto_query_workflow, "_build_pbm_toxpi_output"))
        toxpi_input = pd.DataFrame(
            {
                "compound": ["A", "B", "C"],
                "Peak_Area": [1e7, 1e6, 1e5],
                "Scores": [1.0, 3.0, 2.0],
                "DF": [0.8, 0.3, 0.6],
            }
        )
        output = auto_query_workflow._build_pbm_toxpi_output(
            toxpi_input,
            PBMToxPiConfig(candidate_top_n=3, display_top_n=2, n_iter=10, seed=5),
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["A", "B", "C"]}),
            tables=output.tables,
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
            charts=output.charts,
        )

        package = build_auto_workflow_zip(result, charts=result.charts)

        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertIn(
                "07_Pov_LRTP_PBM_ToxPi/figures/ToxPi_Radial_Plot.png", names
            )
            self.assertIn(
                "07_Pov_LRTP_PBM_ToxPi/figures/ToxPi_Ranking_Bar.pdf", names
            )
            self.assertIn(
                "07_Pov_LRTP_PBM_ToxPi/figures/ToxPi_Robustness_Histogram.png",
                names,
            )
            workbook = pd.ExcelFile(
                io.BytesIO(
                    archive.read(
                        "07_Pov_LRTP_PBM_ToxPi/Pov_LRTP_PBM_ToxPi_Results.xlsx"
                    )
                )
            )
            self.assertIn("ToxPi_Global_Screen", workbook.sheet_names)
            self.assertIn("ToxPi_Robustness", workbook.sheet_names)

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

    @patch("src.auto_query_workflow._run_r_replicate_df")
    def test_warning_only_prepared_input_falls_back_to_legacy_local_screening(
        self,
        mock_local,
    ):
        mock_local.return_value = LocalScreeningOutput(
            tables=OrderedDict(
                [("DF_Table", pd.DataFrame({"Name": ["Compound A"]}))]
            ),
            charts=OrderedDict(),
            warnings=["legacy calculation warning"],
        )
        input_frame = _workflow_input_rows(["Compound A"])
        mapping = AutoWorkflowMapping(
            compound_col="Name",
            formula_col="NIST Lib Hit Formula",
            peak_area_col="Avg TIC",
        )
        prepared = AutoWorkflowPreparedInput(
            mapping=mapping,
            prepared_input=input_frame,
            representative_table=build_representative_table(
                input_frame,
                mapping,
            ),
            local_warnings=["prepared mapping warning"],
        )

        result = run_auto_query_workflow(
            input_frame,
            AutoWorkflowConfig(
                run_r_replicate_df=True,
                run_identifier=False,
            ),
            prepared_input=prepared,
        )

        mock_local.assert_called_once()
        self.assertIn("DF_Table", result.tables)
        warning_messages = result.warnings["message"].tolist()
        self.assertIn("legacy calculation warning", warning_messages)
        self.assertIn("prepared mapping warning", warning_messages)

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
            "EPA_PUC_Pie_Data",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "Source_Origin_Pie_Data",
        ):
            self.assertIn(table_name, result.tables)
        self.assertEqual(len(result.tables["EPA_PUC_Pie_Data"]), 3)
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
            "EPA_PUC_Pie_Data",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "ECHA_Reported_Pie_Data",
            "Source_Origin_Pie_Data",
        ):
            table = result.tables[table_name]
            self.assertEqual(len(table), 3)
            self.assertEqual(table["compound_key"].nunique(), 3)

    @patch("src.auto_query_workflow.run_epi_web_batch")
    @patch("src.auto_query_workflow.run_comptox_use_batch")
    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_workflow_emits_one_checkpoint_per_terminal_step_and_one_final_checkpoint(
        self,
        run_identifier,
        run_comptox,
        run_epi,
    ):
        run_identifier.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Compound A"],
                    "smiles": [""],
                    "cas": [""],
                    "ec": [""],
                    "dtxsid": [""],
                    "echa_id": [""],
                }
            ),
            pd.DataFrame(),
        )
        run_epi.return_value = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        run_comptox.side_effect = RuntimeError("EPA unavailable")
        checkpoints = []
        context = AutoWorkflowCheckpointContext(
            run_id="run-1",
            input_signature="input-sha",
            settings_signature="settings-sha",
            selected_steps=("标识符补全", "EPI Suite 环境归趋", "EPA CompTox 用途"),
        )

        result = run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                run_epi=True,
                run_comptox=True,
                identifier_delay_seconds=0,
                use_delay_seconds=0,
            ),
            checkpoint_context=context,
            checkpoint_callback=checkpoints.append,
        )

        self.assertEqual(
            [checkpoint.current_step for checkpoint in checkpoints],
            ["标识符补全", "EPI Suite 环境归趋", "EPA CompTox 用途", None],
        )
        self.assertEqual(checkpoints[-1].status, "completed")
        self.assertEqual(checkpoints[-1].finished_steps, context.selected_steps)
        status_by_step = result.step_status.set_index("step")["status"].to_dict()
        self.assertEqual(status_by_step["EPI Suite 环境归趋"], "完成")
        self.assertEqual(status_by_step["EPA CompTox 用途"], "失败")

    @patch("src.auto_query_workflow.run_identifier_completion_batch")
    def test_checkpoint_callback_failure_adds_warning_without_stopping_workflow(
        self,
        run_identifier,
    ):
        run_identifier.return_value = (_completed_identifier_rows(["Compound A"]), pd.DataFrame())

        result = run_auto_query_workflow(
            _workflow_input_rows(["Compound A"]),
            AutoWorkflowConfig(
                run_r_replicate_df=False,
                run_identifier=True,
                identifier_delay_seconds=0,
            ),
            checkpoint_context=AutoWorkflowCheckpointContext(
                run_id="run-2",
                input_signature="input-sha",
                settings_signature="settings-sha",
                selected_steps=("标识符补全",),
            ),
            checkpoint_callback=lambda checkpoint: (_ for _ in ()).throw(OSError("disk full")),
        )

        self.assertEqual(result.step_status.iloc[0]["status"], "完成")
        self.assertTrue(result.warnings["stage"].eq("Checkpoint").any())
        self.assertTrue(result.warnings["message"].str.contains("disk full").any())

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
        for table_name in (
            "EPA_PUC_Pie_Data",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
        ):
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
            "EPA_PUC_Pie_Data",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "Source_Origin_Pie_Data",
        ):
            self.assertIn(table_name, result.tables)
        for table_name, missing_label in (
            ("EPA_PUC_Pie_Data", "Others"),
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
        self.assertNotIn("EPA_PUC_Pie_Data", source_only.tables)
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
                    "smiles": [""],
                    "cas": [""],
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
                    *_example_toxpi_tables().items(),
                    *pie_tables.items(),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        charts = build_auto_workflow_charts(result)

        expected = {
            "EPA_Product_Use_Category_Distribution",
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
        puc_chart = charts["EPA_Product_Use_Category_Distribution"]
        self.assertEqual(puc_chart.title, "EPA CompTox Product-Use Category Distribution")
        self.assertTrue(puc_chart.png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(puc_chart.pdf.startswith(b"%PDF"))
        self.assertNotIn("EPA_Product_Use_Category_Rose_Plot", charts)

    def test_auto_workflow_presence_charts_use_toxpi_candidates_and_limits(self):
        candidates = pd.DataFrame(
            [
                {"compound": compound, "source_type": "functional_use", "raw_use": use, "evidence_count": evidence, "functional_use_source": "reported"}
                for compound, use, evidence in (
                    ("Compound A", "Solvent", 2),
                    ("Compound A", "Catalyst", 1),
                    ("Compound B", "Catalyst", 3),
                    ("Compound B", "Dye", 2),
                    ("Compound C", "Excluded", 99),
                )
            ]
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A", "Compound B", "Compound C"]}),
            tables=OrderedDict(
                [
                    ("CompTox_Candidates", candidates),
                    *_example_toxpi_tables(
                        compounds=("Compound B", "Compound A"),
                        per_compound_top_n=1,
                        global_use_top_n=2,
                    ).items(),
                    ("ToxPi_Display", pd.DataFrame({"compound": ["Compound B"]})),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        source = next(
            item
            for item in auto_query_workflow._auto_workflow_chart_sources(result)
            if item["file_prefix"] == "EPA_Reported_Functional_Use_Evidence"
        )
        plot_df = auto_query_workflow._build_chart_data(source)

        self.assertEqual(plot_df["compound"].tolist(), ["Compound B", "Compound A"])
        self.assertEqual(plot_df["use_label"].tolist(), ["Catalyst", "Solvent"])

    def test_auto_workflow_skips_presence_charts_without_toxpi_results(self):
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

        self.assertNotIn("EPA_Reported_Functional_Use_Evidence", charts)
        self.assertNotIn("ECHA_Reported_Use_Evidence", charts)
        self.assertIn("EPA_Reported_Functional_Use_Distribution", charts)
        self.assertIn("ECHA_Reported_Use_Distribution", charts)

    def test_auto_workflow_skips_presence_charts_without_valid_final_ranks(self):
        tables = OrderedDict(
            [
                ("CompTox_Candidates", _example_comptox_candidates()),
                ("ToxPi_Results", pd.DataFrame({"compound": ["Compound A"], "final_rank": [pd.NA]})),
                *_example_pie_tables().items(),
            ]
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=tables,
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        charts = build_auto_workflow_charts(result)

        self.assertNotIn("EPA_Reported_Functional_Use_Evidence", charts)
        self.assertIn("EPA_Reported_Functional_Use_Distribution", charts)

    def test_checkpoint_module_workbooks_split_echa_use_and_ghs(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [
                    ("ECHA_Use_Summary", pd.DataFrame({"compound": ["Compound A"]})),
                    ("ECHA_GHS_Summary", pd.DataFrame({"compound": ["Compound A"]})),
                ]
            ),
            step_status=pd.DataFrame(),
            warnings=pd.DataFrame(),
        )

        use_book = build_auto_workflow_module_workbook(result, "ECHA REACH 用途")
        ghs_book = build_auto_workflow_module_workbook(result, "ECHA GHS/C&L 危害")

        self.assertEqual(use_book.file_name, "ECHA_REACH_Use_Results.xlsx")
        self.assertEqual(ghs_book.file_name, "ECHA_GHS_CL_Results.xlsx")
        self.assertEqual(
            pd.ExcelFile(io.BytesIO(use_book.data)).sheet_names,
            ["ECHA_Use_Summary"],
        )
        self.assertEqual(
            pd.ExcelFile(io.BytesIO(ghs_book.data)).sheet_names,
            ["ECHA_GHS_Summary"],
        )

    def test_chart_module_download_is_zip_with_workbook_png_and_pdf(self):
        module = auto_query_workflow.AutoWorkflowModuleWorkbook(
            step=R_DF_STEP_LABEL,
            slug="local_screening",
            file_name="Local_Screening_Results.xlsx",
            data=b"XLSX",
        )
        charts = OrderedDict(
            {
                "Local_Chemical_Type_Distribution": AutoWorkflowChart(
                    title="Chemical type",
                    png=b"PNG",
                    pdf=b"PDF",
                )
            }
        )

        download = auto_query_workflow.build_auto_workflow_module_download(
            module, charts
        )

        self.assertEqual(download.file_name, "Local_Screening_Results.zip")
        self.assertEqual(download.mime, "application/zip")
        with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "Local_Screening_Results.xlsx",
                    "figures/Chemical_Type_Distribution.png",
                    "figures/Chemical_Type_Distribution.pdf",
                },
            )

    def test_chartless_module_download_remains_xlsx(self):
        module = auto_query_workflow.AutoWorkflowModuleWorkbook(
            step="标识符补全",
            slug="identifier_completion",
            file_name="Identifier_Completion_Results.xlsx",
            data=b"XLSX",
        )

        download = auto_query_workflow.build_auto_workflow_module_download(
            module, OrderedDict()
        )

        self.assertEqual(download.file_name, module.file_name)
        self.assertEqual(
            download.mime,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(download.data, module.data)

    def test_partial_zip_contains_only_named_partial_log_and_completed_module_books(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [("Identifier_Completion", pd.DataFrame({"compound": ["Compound A"]}))]
            ),
            step_status=pd.DataFrame(
                {"step": ["标识符补全"], "status": ["完成"], "rows": [1], "message": [""]}
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
        )
        module = build_auto_workflow_module_workbook(result, "标识符补全")

        package = build_auto_workflow_partial_zip(result, {module.slug: module})

        with zipfile.ZipFile(package) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "Partial_Auto_Query_Workflow_Results.xlsx",
                    "modules/Identifier_Completion_Results.xlsx",
                },
            )

    def test_partial_zip_includes_available_module_figures(self):
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [("DF_Table", pd.DataFrame({"Name": ["Compound A"], "DF": [1.0]}))]
            ),
            step_status=pd.DataFrame(
                {"step": [R_DF_STEP_LABEL], "status": ["完成"], "rows": [1], "message": [""]}
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
        )
        module = build_auto_workflow_module_workbook(result, R_DF_STEP_LABEL)
        charts = OrderedDict(
            {
                "Local_Chemical_Type_Distribution": AutoWorkflowChart(
                    title="Chemical type",
                    png=b"PNG",
                    pdf=b"PDF",
                )
            }
        )

        package = build_auto_workflow_partial_zip(
            result,
            {module.slug: module},
            charts=charts,
        )

        with zipfile.ZipFile(package) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "Partial_Auto_Query_Workflow_Results.xlsx",
                    "modules/Local_Screening_Results.xlsx",
                    "modules/local_screening/figures/Chemical_Type_Distribution.png",
                    "modules/local_screening/figures/Chemical_Type_Distribution.pdf",
                },
            )

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
                    *_example_toxpi_tables().items(),
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
                "04_EPA_CompTox/figures/EPA_Product_Use_Category_Distribution.png",
                "04_EPA_CompTox/figures/EPA_Product_Use_Category_Distribution.pdf",
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
                    "EPA_PUC_Pie_Data",
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
                        "Input_File_Mappings",
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
                    (
                        "EPI_Results",
                        "EPI_Raw_Results",
                        "EPI_Errors",
                        "EPI_Completeness",
                        "EPI_Source_Provenance",
                        "EPI_Match_Audit",
                        "EPI_Conflict_Audit",
                        "EPI_Query_Attempts",
                        "EPI_Retry_Input",
                    ),
                ),
                (
                    "04_EPA_CompTox/EPA_CompTox_Results.xlsx",
                    (
                        "CompTox_Summary",
                        "Product_Use_Categories",
                        "Functional_Uses_Predicted",
                        "Functional_Uses_Reported",
                        "EPA_PUC_Pie_Data",
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
                        "ToxPi_Global_Screen",
                        "ToxPi_Normalized",
                        "ToxPi_Results",
                        "ToxPi_Display",
                        "ToxPi_Settings",
                        "ToxPi_Robustness",
                        "ToxPi_Robust_Stats",
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
            "EPA_Product_Use_Category_Distribution",
            "EPA_Top_Predicted_Functional_Use",
            "EPA_Reported_Functional_Use_Distribution",
            "EPA_Reported_Functional_Use_Evidence",
            "ECHA_Reported_Use_Distribution",
            "ECHA_Reported_Use_Evidence",
            "Source_Origin_Distribution",
            "ToxPi_Radial_Plot",
            "ToxPi_Ranking_Bar",
            "ToxPi_Robustness_Histogram",
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

    def test_page_6_wires_checkpoint_restore_and_non_rerunning_downloads(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")

        for token in (
            "cleanup_expired_checkpoints(",
            'st.query_params.get("run")',
            'st.query_params["run"] = run_token',
            "load_checkpoint(",
            "save_checkpoint(",
            "delete_checkpoint(",
            "checkpoint_callback=handle_checkpoint",
            'on_click="ignore"',
            "Auto_Query_Workflow_Partial_Results.zip",
            "已恢复上次运行的部分结果",
            "上次运行未正常结束",
        ):
            self.assertIn(token, page_text)

    def test_checkpoint_round_trips_multiple_input_filenames_and_retry_table(self):
        result = _result_with_epi_retry_input(["B"])
        with TemporaryDirectory() as root:
            token = generate_run_token()
            save_checkpoint(
                token,
                _checkpoint_for(result),
                [r"C:\uploads\A.xlsx", "/srv/uploads/B.xlsx"],
                OrderedDict(),
                root=root,
            )
            loaded = load_checkpoint(token, root=root)

        self.assertEqual(loaded.input_filenames, ("A.xlsx", "B.xlsx"))
        self.assertEqual(
            loaded.checkpoint.result.tables["EPI_Retry_Input"][
                "compound"
            ].tolist(),
            ["B"],
        )

    def test_schema_v1_input_filename_loads_as_singleton_tuple(self):
        result = _result_with_epi_retry_input(["B"])
        with TemporaryDirectory() as root:
            token = generate_run_token()
            run_dir = save_checkpoint(
                token,
                _checkpoint_for(result),
                ["A.xlsx", "B.xlsx"],
                OrderedDict(),
                root=root,
            )
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["schema_version"] = 1
            manifest["input_filename"] = manifest.pop("input_filenames")[0]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            loaded = load_checkpoint(token, root=root)

        self.assertEqual(loaded.input_filenames, ("A.xlsx",))
        self.assertEqual(
            loaded.checkpoint.result.tables["EPI_Retry_Input"][
                "compound"
            ].tolist(),
            ["B"],
        )

    def test_unknown_future_checkpoint_schema_is_rejected(self):
        with TemporaryDirectory() as root:
            token = generate_run_token()
            run_dir = save_checkpoint(
                token,
                _checkpoint_for(_result_with_epi_retry_input(["B"])),
                ["A.xlsx", "B.xlsx"],
                OrderedDict(),
                root=root,
            )
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["schema_version"] = 4
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with self.assertRaises(CheckpointStorageError):
                load_checkpoint(token, root=root)

    def test_checkpoint_rejects_empty_or_invalid_input_filename_collections(self):
        invalid_values = (
            [],
            123,
            ["A.xlsx", None],
        )
        with TemporaryDirectory() as root:
            for input_filenames in invalid_values:
                with self.subTest(input_filenames=input_filenames):
                    with self.assertRaises(CheckpointStorageError):
                        save_checkpoint(
                            generate_run_token(),
                            _checkpoint_for(
                                _result_with_epi_retry_input(["B"])
                            ),
                            input_filenames,
                            OrderedDict(),
                            root=root,
                        )

    def test_page_6_recovery_shows_all_checkpoint_input_filenames(self):
        with TemporaryDirectory() as root:
            checkpoint_root = Path(root)
            token = generate_run_token()
            save_checkpoint(
                token,
                _checkpoint_for(_result_with_epi_retry_input(["B"])),
                ["Lake-A.xlsx", "Lake-B.xlsx"],
                OrderedDict(),
                root=checkpoint_root,
            )
            with _isolated_page_checkpoint_storage(checkpoint_root):
                app = AppTest.from_file(
                    "pages/6_一键批量查询.py",
                    default_timeout=20,
                )
                app.query_params["run"] = token
                app.run(timeout=20)

        self.assertEqual(list(app.exception), [])
        self.assertEqual(
            app.session_state["auto_query_primary_file_names"],
            ["Lake-A.xlsx", "Lake-B.xlsx"],
        )
        self.assertTrue(
            any(
                "Lake-A.xlsx" in caption.value
                and "Lake-B.xlsx" in caption.value
                for caption in app.caption
            )
        )

    def test_page_6_shows_epi_retry_button_only_for_nonempty_retry_input(self):
        app = _app_test_with_cached_workbook()
        app.session_state["auto_query_workflow_result"] = (
            _result_with_epi_retry_input([])
        )
        app.session_state["auto_query_workflow_charts"] = OrderedDict()
        app = app.run(timeout=20)

        self.assertNotIn(
            "仅重试未完成的 EPI 行",
            [button.label for button in app.button],
        )

        app.session_state["auto_query_workflow_result"] = (
            _result_with_epi_retry_input(["Failed B"])
        )
        app = app.run(timeout=20)

        self.assertIn(
            "仅重试未完成的 EPI 行",
            [button.label for button in app.button],
        )

    def test_page_6_missing_name_and_smiles_retry_input_shows_hint_without_button(self):
        app = _app_test_with_cached_workbook()
        original = _result_with_epi_retry_input([""])
        retry_input = original.tables["EPI_Retry_Input"].copy()
        retry_input["compound"] = " "
        retry_input["smiles"] = " "
        original.tables["EPI_Retry_Input"] = retry_input
        app.session_state["auto_query_workflow_result"] = original
        app.session_state["auto_query_workflow_charts"] = OrderedDict()

        app = app.run(timeout=20)

        self.assertNotIn(
            "仅重试未完成的 EPI 行",
            [button.label for button in app.button],
        )
        self.assertTrue(
            any("SMILES" in message.value for message in app.info)
        )

    def test_page_6_epi_retry_refreshes_exports_and_uses_checkpoint_handler(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")

        for token in (
            "retry_auto_workflow_epi_failures(",
            'result.tables.get("EPI_Retry_Input"',
            '"仅重试未完成的 EPI 行"',
            'build_auto_workflow_module_workbook(',
            '"EPI Suite 环境归趋"',
            '"Pov-LRTP / PBM / ToxPi"',
            "build_auto_workflow_partial_zip(",
            "build_auto_workflow_zip(",
            "handle_checkpoint(",
        ):
            self.assertIn(token, page_text)

    def test_page_6_epi_retry_click_updates_session_exports_and_checkpoint(self):
        app = _app_test_with_cached_workbooks(
            [
                ("Lake-A.xlsx", _app_test_workbook_bytes("Compound A")),
                ("Lake-B.xlsx", _app_test_workbook_bytes("Compound B")),
            ]
        )
        next(
            box
            for box in app.checkbox
            if box.label == "Pov-LRTP / PBM / ToxPi"
        ).check().run()
        original = _result_with_epi_retry_input(["Failed B"])
        app.session_state["auto_query_workflow_result"] = original
        app.session_state["auto_query_workflow_charts"] = OrderedDict()
        app = app.run(timeout=20)
        retried = _result_with_epi_retry_input([])
        replacement_chart = AutoWorkflowChart(
            "New radial",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            b"new-pdf",
        )
        retried.charts = OrderedDict(
            [("ToxPi_Radial_Plot", replacement_chart)]
        )
        epi_module = AutoWorkflowModuleWorkbook(
            step="EPI Suite 环境归趋",
            slug="epi_suite",
            file_name="EPI_Suite_Results.xlsx",
            data=b"epi-book",
        )
        pov_module = AutoWorkflowModuleWorkbook(
            step="Pov-LRTP / PBM / ToxPi",
            slug="pov_lrtp_pbm_toxpi",
            file_name="Pov_LRTP_PBM_ToxPi_Results.xlsx",
            data=b"pov-book",
        )

        with (
            patch(
                "src.auto_query_workflow.retry_auto_workflow_epi_failures",
                return_value=retried,
            ) as retry,
            patch(
                "src.auto_query_workflow.build_auto_workflow_module_workbook",
                side_effect=[epi_module, pov_module],
            ),
            patch(
                "src.auto_query_workflow.build_auto_workflow_partial_zip",
                return_value=io.BytesIO(b"partial-zip"),
            ) as build_partial,
            patch(
                "src.auto_query_workflow.build_auto_workflow_zip",
                return_value=io.BytesIO(b"full-zip"),
            ) as build_full,
            patch("src.auto_query_checkpoint.save_checkpoint") as save,
        ):
            app = next(
                button
                for button in app.button
                if button.label == "仅重试未完成的 EPI 行"
            ).click().run(timeout=20)

        retry.assert_called_once()
        build_partial.assert_called_once()
        build_full.assert_called_once()
        self.assertIs(
            app.session_state["auto_query_workflow_result"],
            retried,
        )
        self.assertIs(
            app.session_state["auto_query_workflow_charts"][
                "ToxPi_Radial_Plot"
            ],
            replacement_chart,
        )
        self.assertEqual(
            set(app.session_state["auto_query_module_workbooks"]),
            {"epi_suite", "pov_lrtp_pbm_toxpi"},
        )
        self.assertEqual(
            app.session_state["auto_query_workflow_zip"].getvalue(),
            b"full-zip",
        )
        self.assertGreaterEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[1].status, "completed")
        self.assertTrue(
            all(
                call.args[2] == ["Lake-A.xlsx", "Lake-B.xlsx"]
                for call in save.call_args_list
            )
        )
        self.assertEqual(list(app.exception), [])

    def test_page_6_epi_retry_zip_failure_removes_stale_full_zip_and_checkpoints_failure(
        self,
    ):
        app = _app_test_with_cached_workbook()
        original = _result_with_epi_retry_input(["Failed B"])
        app.session_state["auto_query_workflow_result"] = original
        app.session_state["auto_query_workflow_charts"] = OrderedDict()
        app.session_state["auto_query_workflow_zip"] = io.BytesIO(b"stale")
        app = app.run(timeout=20)
        retried = _result_with_epi_retry_input([])
        epi_module = AutoWorkflowModuleWorkbook(
            step="EPI Suite 环境归趋",
            slug="epi_suite",
            file_name="EPI_Suite_Results.xlsx",
            data=b"epi-book",
        )

        with (
            patch(
                "src.auto_query_workflow.retry_auto_workflow_epi_failures",
                return_value=retried,
            ),
            patch(
                "src.auto_query_workflow.build_auto_workflow_module_workbook",
                return_value=epi_module,
            ),
            patch(
                "src.auto_query_workflow.build_auto_workflow_partial_zip",
                return_value=io.BytesIO(b"partial-zip"),
            ),
            patch(
                "src.auto_query_workflow.build_auto_workflow_zip",
                side_effect=RuntimeError("simulated retry ZIP failure"),
            ),
            patch("src.auto_query_checkpoint.save_checkpoint") as save,
        ):
            app = next(
                button
                for button in app.button
                if button.label == "仅重试未完成的 EPI 行"
            ).click().run(timeout=20)

        self.assertIs(
            app.session_state["auto_query_workflow_result"],
            retried,
        )
        self.assertNotIn("auto_query_workflow_zip", app.session_state)
        self.assertIn(
            "simulated retry ZIP failure",
            app.session_state["auto_query_checkpoint_warning"],
        )
        self.assertEqual(save.call_args.args[1].status, "failed")
        self.assertEqual(list(app.exception), [])

    def test_page_6_epi_retry_batch_failure_checkpoints_updated_audit(self):
        app = _app_test_with_cached_workbook()
        original = _result_with_epi_retry_input(["Failed B"])
        updated = _result_with_epi_retry_input(["Failed B"])
        updated.tables["EPI_Query_Attempts"] = pd.DataFrame(
            [{"event": "started", "label": "Failed B", "attempt": 1}]
        )
        app.session_state["auto_query_workflow_result"] = original
        app.session_state["auto_query_workflow_charts"] = OrderedDict()
        app.session_state["auto_query_result_table"] = "EPI_Query_Attempts"
        app = app.run(timeout=20)

        retry_error = AutoWorkflowEpiRetryError(
            "simulated EPI retry failure",
            updated,
        )
        with (
            patch(
                "src.auto_query_workflow.retry_auto_workflow_epi_failures",
                side_effect=retry_error,
            ),
            patch("src.auto_query_checkpoint.save_checkpoint") as save,
        ):
            app = next(
                button
                for button in app.button
                if button.label == "仅重试未完成的 EPI 行"
            ).click().run(timeout=20)

        stored_checkpoint = save.call_args.args[1]
        self.assertEqual(stored_checkpoint.status, "failed")
        self.assertEqual(save.call_args.args[2], ["smoke.xlsx"])
        self.assertIs(stored_checkpoint.result, updated)
        self.assertEqual(
            len(stored_checkpoint.result.tables["EPI_Query_Attempts"]),
            1,
        )
        self.assertEqual(
            stored_checkpoint.result.tables["EPI_Retry_Input"][
                "compound"
            ].tolist(),
            ["Failed B"],
        )
        self.assertIs(
            app.session_state["auto_query_workflow_result"],
            updated,
        )
        rendered_frames = [
            element.value
            for element in app.dataframe
            if isinstance(element.value, pd.DataFrame)
        ]
        self.assertTrue(
            any(
                "label" in frame.columns
                and frame["label"].tolist() == ["Failed B"]
                for frame in rendered_frames
            )
        )
        self.assertEqual(list(app.exception), [])

    def test_page_6_epi_retry_module_export_failure_removes_stale_workbook(
        self,
    ):
        app = _app_test_with_cached_workbook()
        original = _result_with_epi_retry_input(["Failed B"])
        retried = _result_with_epi_retry_input([])
        stale_module = AutoWorkflowModuleWorkbook(
            step="EPI Suite 环境归趋",
            slug="epi_suite",
            file_name="EPI_Suite_Results.xlsx",
            data=b"stale-epi-book",
        )
        app.session_state["auto_query_workflow_result"] = original
        app.session_state["auto_query_workflow_charts"] = OrderedDict()
        app.session_state["auto_query_module_workbooks"] = OrderedDict(
            [("epi_suite", stale_module)]
        )
        app = app.run(timeout=20)

        with (
            patch(
                "src.auto_query_workflow.retry_auto_workflow_epi_failures",
                return_value=retried,
            ),
            patch(
                "src.auto_query_workflow.build_auto_workflow_module_workbook",
                side_effect=RuntimeError("simulated module export failure"),
            ),
            patch(
                "src.auto_query_workflow.build_auto_workflow_partial_zip",
            ) as build_partial,
            patch(
                "src.auto_query_workflow.build_auto_workflow_zip",
            ) as build_full,
            patch("src.auto_query_checkpoint.save_checkpoint") as save,
        ):
            app = next(
                button
                for button in app.button
                if button.label == "仅重试未完成的 EPI 行"
            ).click().run(timeout=20)

        self.assertNotIn(
            "epi_suite",
            app.session_state["auto_query_module_workbooks"],
        )
        self.assertGreaterEqual(build_partial.call_count, 1)
        for partial_call in build_partial.call_args_list:
            self.assertNotIn("epi_suite", partial_call.args[1])
        build_full.assert_not_called()
        stored_checkpoint = save.call_args.args[1]
        self.assertEqual(stored_checkpoint.status, "failed")
        self.assertIs(stored_checkpoint.result, retried)
        self.assertIn(
            "simulated module export failure",
            stored_checkpoint.error_message,
        )
        self.assertEqual(list(app.exception), [])

    def test_page_6_keeps_partial_artifacts_when_full_zip_build_fails(self):
        storage_stack = ExitStack()
        checkpoint_root = Path(
            storage_stack.enter_context(TemporaryDirectory())
        )
        storage_stack.enter_context(
            _isolated_page_checkpoint_storage(checkpoint_root)
        )
        app = _app_test_with_cached_workbook()
        run_token = None
        identifier_result = _completed_identifier_rows(
            ["Compound A", "Compound B"]
        )
        generated_chart = AutoWorkflowChart(
            "Post-query chart",
            b"\x89PNG\r\n\x1a\nPOST-QUERY",
            b"%PDF-1.4 POST-QUERY",
        )
        generated_charts = OrderedDict(
            [("Post_Query_Chart", generated_chart)]
        )
        media_storages = []
        try:
            app.checkbox[0].uncheck()
            with (
                _capture_app_test_media_storage(media_storages),
                patch(
                    "src.auto_query_workflow.run_identifier_completion_batch",
                    return_value=(identifier_result, pd.DataFrame()),
                ),
                patch(
                    "src.auto_query_workflow.build_auto_workflow_charts",
                    return_value=generated_charts,
                ),
                patch(
                    "src.auto_query_workflow.build_auto_workflow_zip",
                    side_effect=RuntimeError("simulated final ZIP failure"),
                ) as build_zip,
            ):
                next(
                    button
                    for button in app.button
                    if button.label == "开始一键运行"
                ).click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            build_zip.assert_called_once()
            run_token = app.session_state["auto_query_run_token"]
            self.assertIn(run_token, app.query_params["run"])
            self.assertEqual(
                app.session_state["auto_query_checkpoint_warning"],
                "simulated final ZIP failure",
            )
            self.assertIn(
                "identifier_completion",
                app.session_state["auto_query_module_workbooks"],
            )
            self.assertEqual(
                app.session_state["auto_query_workflow_charts"],
                generated_charts,
            )
            self.assertEqual(
                app.session_state["auto_query_workflow_result"].charts,
                generated_charts,
            )
            downloads = {
                button.label: button for button in app.get("download_button")
            }
            self.assertIn("下载 标识符补全", downloads)
            self.assertIn("下载部分结果 ZIP", downloads)

            stored = load_checkpoint(run_token, root=checkpoint_root)
            self.assertEqual(stored.checkpoint.status, "failed")
            self.assertEqual(
                stored.checkpoint.error_message, "simulated final ZIP failure"
            )
            self.assertIn("identifier_completion", stored.module_workbooks)
            self.assertEqual(
                stored.checkpoint.result.charts["Post_Query_Chart"].png,
                generated_chart.png,
            )

            module_payload = _app_test_download_payload(
                downloads["下载 标识符补全"], media_storages[-1]
            )
            partial_payload = _app_test_download_payload(
                downloads["下载部分结果 ZIP"], media_storages[-1]
            )
            self.assertTrue(module_payload.startswith(b"PK"))
            self.assertTrue(partial_payload.startswith(b"PK"))
            with zipfile.ZipFile(io.BytesIO(partial_payload)) as partial_zip:
                self.assertEqual(
                    set(partial_zip.namelist()),
                    {
                        "Partial_Auto_Query_Workflow_Results.xlsx",
                        "modules/Identifier_Completion_Results.xlsx",
                    },
                )

            recovered = AppTest.from_file(
                "pages/6_一键批量查询.py", default_timeout=20
            )
            recovered.query_params["run"] = run_token
            recovered.run(timeout=20)

            self.assertEqual(len(recovered.exception), 0)
            self.assertTrue(
                any(
                    message.value == "已恢复上次运行的部分结果。"
                    for message in recovered.success
                )
            )
            self.assertTrue(
                any(
                    message.value.startswith("上次运行未正常结束")
                    for message in recovered.warning
                )
            )
            recovered_downloads = {
                button.label for button in recovered.get("download_button")
            }
            self.assertIn("下载 标识符补全", recovered_downloads)
            self.assertIn("下载部分结果 ZIP", recovered_downloads)
            self.assertEqual(
                recovered.session_state["auto_query_workflow_charts"][
                    "Post_Query_Chart"
                ].pdf,
                generated_chart.pdf,
            )
            self.assertEqual(
                recovered.session_state["auto_query_workflow_result"].charts[
                    "Post_Query_Chart"
                ].png,
                generated_chart.png,
            )
        finally:
            if run_token:
                delete_checkpoint(run_token, root=checkpoint_root)
            storage_stack.close()

    def test_page_6_offers_partial_zip_when_workflow_fails_before_first_module_export(self):
        storage_stack = ExitStack()
        checkpoint_root = Path(
            storage_stack.enter_context(TemporaryDirectory())
        )
        storage_stack.enter_context(
            _isolated_page_checkpoint_storage(checkpoint_root)
        )
        app = _app_test_with_cached_workbook()
        run_token = None
        media_storages = []
        try:
            with (
                _capture_app_test_media_storage(media_storages),
                patch(
                    "src.auto_query_workflow.run_auto_query_workflow",
                    side_effect=RuntimeError("failed before first module"),
                ),
            ):
                next(
                    button
                    for button in app.button
                    if button.label == "开始一键运行"
                ).click().run(timeout=20)

            self.assertEqual(len(app.exception), 0)
            run_token = app.session_state["auto_query_run_token"]
            self.assertEqual(
                app.session_state["auto_query_module_workbooks"],
                OrderedDict(),
            )
            downloads = {
                button.label: button for button in app.get("download_button")
            }
            self.assertIn("下载部分结果 ZIP", downloads)
            partial_payload = _app_test_download_payload(
                downloads["下载部分结果 ZIP"], media_storages[-1]
            )
            with zipfile.ZipFile(io.BytesIO(partial_payload)) as partial_zip:
                self.assertEqual(
                    partial_zip.namelist(),
                    ["Partial_Auto_Query_Workflow_Results.xlsx"],
                )
                workbook = pd.ExcelFile(
                    io.BytesIO(
                        partial_zip.read(
                            "Partial_Auto_Query_Workflow_Results.xlsx"
                        )
                    )
                )
                self.assertTrue(
                    {
                        "Run_Log",
                        "Representative_Input",
                        "Input_File_Mappings",
                        "Warnings",
                    }.issubset(workbook.sheet_names)
                )
                self.assertNotIn(
                    "Structure_Preparation",
                    workbook.sheet_names,
                )

            stored = load_checkpoint(run_token, root=checkpoint_root)
            self.assertEqual(stored.checkpoint.status, "failed")
            self.assertEqual(stored.module_workbooks, OrderedDict())
        finally:
            if run_token:
                delete_checkpoint(run_token, root=checkpoint_root)
            storage_stack.close()

    def test_page_6_download_endpoint_returns_xlsx_without_rerunning(self):
        storage_stack = ExitStack()
        checkpoint_root = Path(
            storage_stack.enter_context(TemporaryDirectory())
        )
        storage_mocks = storage_stack.enter_context(
            _isolated_page_checkpoint_storage(checkpoint_root)
        )
        run_token = generate_run_token()
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [
                    (
                        "Identifier_Completion",
                        pd.DataFrame(
                            {"compound": ["Compound A"], "cas": ["64-17-5"]}
                        ),
                    )
                ]
            ),
            step_status=pd.DataFrame(
                {
                    "step": ["标识符补全"],
                    "status": ["完成"],
                    "rows": [1],
                    "message": [""],
                }
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
        )
        module = build_auto_workflow_module_workbook(result, "标识符补全")
        checkpoint = AutoWorkflowCheckpoint(
            run_id=generate_run_token(),
            input_signature="download-smoke-input",
            settings_signature="download-smoke-settings",
            selected_steps=("标识符补全", "EPI Suite 环境归趋"),
            finished_steps=("标识符补全",),
            current_step="EPI Suite 环境归趋",
            status="running",
            result=result,
            error_message="",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        media_storages = []
        try:
            save_checkpoint(
                run_token,
                checkpoint,
                "download-smoke.xlsx",
                {module.slug: module},
                root=checkpoint_root,
            )
            with _capture_app_test_media_storage(media_storages):
                app = AppTest.from_file(
                    "pages/6_一键批量查询.py", default_timeout=20
                )
                app.query_params["run"] = run_token
                app.run(timeout=20)

                self.assertEqual(len(app.exception), 0)
                downloads = {
                    button.label: button for button in app.get("download_button")
                }
                download = downloads["下载 标识符补全"]
                self.assertTrue(download.proto.ignore_rerun)
                token_before = app.session_state["auto_query_run_token"]
                result_before = app.session_state["auto_query_workflow_result"]
                status_before = result_before.step_status.to_json(
                    orient="split", force_ascii=False
                )
                execution_count_before = storage_mocks.cleanup.call_count

                payload = _app_test_download_payload(
                    download, media_storages[-1]
                )

                self.assertTrue(payload.startswith(b"PK"))
                self.assertIn(
                    "Identifier_Completion",
                    pd.ExcelFile(io.BytesIO(payload)).sheet_names,
                )
                self.assertEqual(
                    app.session_state["auto_query_run_token"], token_before
                )
                self.assertIs(
                    app.session_state["auto_query_workflow_result"], result_before
                )
                self.assertEqual(
                    app.session_state["auto_query_workflow_result"].step_status.to_json(
                        orient="split", force_ascii=False
                    ),
                    status_before,
                )
                self.assertEqual(
                    storage_mocks.cleanup.call_count, execution_count_before
                )
        finally:
            delete_checkpoint(run_token, root=checkpoint_root)
            storage_stack.close()

    def test_page_6_chart_module_download_contains_workbook_png_and_pdf(self):
        storage_stack = ExitStack()
        checkpoint_root = Path(
            storage_stack.enter_context(TemporaryDirectory())
        )
        storage_stack.enter_context(
            _isolated_page_checkpoint_storage(checkpoint_root)
        )
        run_token = generate_run_token()
        chart = AutoWorkflowChart(
            title="Chemical Type Distribution",
            png=base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            pdf=b"%PDF-1.4\n%%EOF",
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Compound A"]}),
            tables=OrderedDict(
                [("DF_Table", pd.DataFrame({"Name": ["Compound A"], "DF": [1.0]}))]
            ),
            step_status=pd.DataFrame(
                {
                    "step": [R_DF_STEP_LABEL],
                    "status": ["完成"],
                    "rows": [1],
                    "message": [""],
                }
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
            charts=OrderedDict(
                [("Local_Chemical_Type_Distribution", chart)]
            ),
        )
        module = build_auto_workflow_module_workbook(result, R_DF_STEP_LABEL)
        checkpoint = AutoWorkflowCheckpoint(
            run_id=generate_run_token(),
            input_signature="chart-download-input",
            settings_signature="chart-download-settings",
            selected_steps=(R_DF_STEP_LABEL,),
            finished_steps=(R_DF_STEP_LABEL,),
            current_step=None,
            status="completed",
            result=result,
            error_message="",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        media_storages = []
        try:
            save_checkpoint(
                run_token,
                checkpoint,
                "chart-download.xlsx",
                {module.slug: module},
                root=checkpoint_root,
            )
            with _capture_app_test_media_storage(media_storages):
                app = AppTest.from_file(
                    "pages/6_一键批量查询.py", default_timeout=20
                )
                app.query_params["run"] = run_token
                app.run(timeout=20)

                self.assertEqual(len(app.exception), 0)
                downloads = {
                    button.label: button for button in app.get("download_button")
                }
                download = downloads[f"下载 {R_DF_STEP_LABEL}"]
                self.assertTrue(download.proto.ignore_rerun)
                payload = _app_test_download_payload(
                    download, media_storages[-1]
                )
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    self.assertEqual(
                        set(archive.namelist()),
                        {
                            "Local_Screening_Results.xlsx",
                            "figures/Chemical_Type_Distribution.png",
                            "figures/Chemical_Type_Distribution.pdf",
                        },
                    )
        finally:
            delete_checkpoint(run_token, root=checkpoint_root)
            storage_stack.close()

    def test_requirements_support_non_rerunning_download_buttons(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        active_requirements = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        streamlit_requirements = [
            line
            for line in active_requirements
            if re.match(r"^streamlit(?=[<>=!~\s]|$)", line, flags=re.IGNORECASE)
        ]

        self.assertEqual(streamlit_requirements, ["streamlit>=1.49,<2"])

    def test_page_6_renders_recovered_results_before_stopping_for_missing_upload(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        no_upload_block = page_text.split("if not active_uploads:", 1)[1].split(
            "st.success", 1
        )[0]

        self.assertIn("auto_query_partial_result", no_upload_block)
        self.assertIn("_render_saved_results", no_upload_block)
        self.assertIn("st.stop()", no_upload_block)

    def test_page_6_uses_unique_keys_for_repeated_live_checkpoint_renders(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        self.assertIn("def handle_checkpoint", page_text)
        callback_block = page_text.split("def handle_checkpoint", 1)[1].split(
            "initial_result", 1
        )[0]

        self.assertIn("live_render_generation", callback_block)
        self.assertIn("key_prefix=", callback_block)
        module_renderer = page_text.split("def _render_module_downloads", 1)[1].split(
            "def ", 1
        )[0]
        self.assertIn("key_prefix", module_renderer)
        self.assertIn("slug", module_renderer)
        self.assertIn('on_click="ignore"', module_renderer)

    def test_page_6_discards_old_full_zip_before_installing_a_recovery(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        self.assertIn("loaded = load_checkpoint(recovery_token)", page_text)
        restore_success = page_text.split(
            "loaded = load_checkpoint(recovery_token)", 1
        )[1].split("uploaded_file = st.file_uploader", 1)[0]

        self.assertIn("RESULT_CACHE_KEYS", restore_success)
        self.assertIn("clear_uploads", restore_success)

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
        self.assertIn('"EPA_PUC_Pie_Data"', page_text)
        self.assertIn('"ECHA_Uses_Reported"', page_text)
        self.assertIn('"Source_Origin_Pie_Data"', page_text)
        self.assertNotIn('"CompTox_Candidates"', page_text)
        self.assertNotIn('"ECHA_Use_Candidates"', page_text)

    def test_page_6_renders_affected_results_in_per_file_tabs(self):
        workbook = _app_test_workbook_bytes()
        app = _app_test_with_cached_workbooks(
            [("A.xlsx", workbook), ("B.xlsx", workbook)]
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Shared"]}),
            tables=OrderedDict(
                [
                    (
                        "Input_File_Mappings",
                        pd.DataFrame(
                            {
                                "source_file": ["A.xlsx", "B.xlsx"],
                                "sample_id": ["A", "B"],
                            }
                        ),
                    ),
                    (
                        "EPI_Primary_Membership",
                        pd.DataFrame(
                            {
                                "identity_key": [
                                    "cas:64-17-5",
                                    "cas:64-17-5",
                                ],
                                "primary_file": ["A.xlsx", "B.xlsx"],
                                "sample_id": ["A", "B"],
                                "compound": ["Shared", "Shared"],
                            }
                        ),
                    ),
                    (
                        "CompTox_Summary",
                        pd.DataFrame(
                            {
                                "compound": ["Shared"],
                                "input_identity_key": ["cas:64-17-5"],
                            }
                        ),
                    ),
                    (
                        "ToxPi_Results",
                        pd.DataFrame(
                            {
                                "compound": ["Shared"],
                                "final_rank": [1],
                            }
                        ),
                    ),
                ]
            ),
            step_status=pd.DataFrame(
                columns=["step", "status", "rows", "message"]
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
        )
        app.session_state["auto_query_workflow_result"] = result
        app.session_state["auto_query_workflow_charts"] = OrderedDict()

        app = app.run(timeout=20)

        tab_labels = [tab.label for tab in app.tabs]
        self.assertIn("EPA CompTox", tab_labels)
        self.assertIn("Pov-LRTP / PBM / ToxPi", tab_labels)
        self.assertIn("A.xlsx", tab_labels)
        self.assertIn("B.xlsx", tab_labels)
        self.assertTrue(
            any(
                "本栏汇总所有参与文件" in caption.value
                for caption in app.caption
            )
        )
        result_table_select = next(
            selectbox
            for selectbox in app.selectbox
            if selectbox.label == "查看结果表"
        )
        self.assertNotIn("CompTox_Summary", result_table_select.options)
        self.assertIn("ToxPi_Results", result_table_select.options)
        self.assertIn("Input_File_Mappings", result_table_select.options)
        self.assertEqual(list(app.exception), [])

    def test_page_6_keeps_completed_empty_module_visible_for_every_file(self):
        workbook = _app_test_workbook_bytes()
        app = _app_test_with_cached_workbooks(
            [("A.xlsx", workbook), ("B.xlsx", workbook)]
        )
        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(),
            representative_table=pd.DataFrame({"Name": ["Shared"]}),
            tables=OrderedDict(
                [
                    (
                        "Input_File_Mappings",
                        pd.DataFrame(
                            {
                                "source_file": ["A.xlsx", "B.xlsx"],
                                "sample_id": ["A", "B"],
                            }
                        ),
                    ),
                    (
                        "EPI_Primary_Membership",
                        pd.DataFrame(
                            {
                                "identity_key": [
                                    "cas:64-17-5",
                                    "cas:64-17-5",
                                ],
                                "primary_file": ["A.xlsx", "B.xlsx"],
                                "sample_id": ["A", "B"],
                                "compound": ["Shared", "Shared"],
                            }
                        ),
                    ),
                    (
                        "CompTox_Summary",
                        pd.DataFrame(
                            columns=["compound", "input_identity_key"]
                        ),
                    ),
                ]
            ),
            step_status=pd.DataFrame(
                [
                    {
                        "step": "EPA CompTox 用途",
                        "status": "完成",
                        "rows": 0,
                        "message": "",
                    }
                ]
            ),
            warnings=pd.DataFrame(columns=["stage", "message"]),
        )
        app.session_state["auto_query_workflow_result"] = result
        app.session_state["auto_query_workflow_charts"] = OrderedDict()

        app = app.run(timeout=20)

        tab_labels = [tab.label for tab in app.tabs]
        self.assertIn("EPA CompTox", tab_labels)
        self.assertIn("A.xlsx", tab_labels)
        self.assertIn("B.xlsx", tab_labels)
        self.assertEqual(
            sum(
                message.value == "该文件在此模块中暂无结果。"
                for message in app.info
            ),
            2,
        )
        self.assertEqual(list(app.exception), [])

    def test_page_6_routes_plot_warnings_to_screening_audit_tables(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        screening_definition = page_text.split('"screening"', 1)[1].split('"identifier"', 1)[0]

        self.assertIn('"Plot_Warnings"', screening_definition)
        self.assertIn('"Plot_Warnings"', page_text.split("def _is_audit_table", 1)[1])

    def test_page_6_assigns_local_screening_charts_to_local_tab(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        screening_definition = page_text.split('"screening"', 1)[1].split('"identifier"', 1)[0]

        self.assertIn('("Local_",)', screening_definition)

    def test_page_6_exposes_typed_axis_and_toxpi_settings_and_dashboard_outputs(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        toxpi_definition = page_text.split('"toxpi"', 1)[1].split(
            "available_charts", 1
        )[0]

        for token in (
            "ScreeningAxisRanges(",
            "PBMToxPiConfig(",
            "candidate_top_n",
            "display_top_n",
            "evidence_per_compound_top_n",
            "evidence_global_use_top_n",
            "peak_area_weight",
            "pbm_weight",
            "df_weight",
            "robustness_enabled",
            "perturbation_percent",
            "robustness_iterations",
            "robustness_seed",
            "axis_ranges=axis_ranges",
            "toxpi_config=toxpi_config",
        ):
            self.assertIn(token, page_text)
        for table_name in (
            "ToxPi_Global_Screen",
            "ToxPi_Normalized",
            "ToxPi_Results",
            "ToxPi_Display",
            "ToxPi_Settings",
            "ToxPi_Robustness",
            "ToxPi_Robust_Stats",
        ):
            self.assertIn(f'"{table_name}"', toxpi_definition)
        self.assertIn('("ToxPi_",)', toxpi_definition)
        audit_definition = page_text.split("def _is_audit_table", 1)[1]
        self.assertIn('"ToxPi_Settings"', audit_definition)
        self.assertIn('"ToxPi_Robustness"', audit_definition)
        self.assertIn('"ToxPi_Robust_Stats"', audit_definition)

    def test_page_6_invalidates_cached_results_from_all_result_settings(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")
        start_index = page_text.index('start_run = st.button("开始一键运行"')

        self.assertIn("invalidate_results_on_settings_change(", page_text)
        invalidate_index = page_text.index("invalidate_results_on_settings_change(")
        self.assertLess(invalidate_index, start_index)
        settings_block = page_text.split("result_settings = {", 1)[1].split(
            "invalidate_results_on_settings_change(", 1
        )[0]
        for setting in (
            "compound_col",
            "formula_col",
            "peak_area_col",
            "group_area_cols",
            "mol_column",
            "smiles_col",
            "cas_col",
            "run_r_replicate_df",
            "run_identifier",
            "run_epi",
            "run_comptox",
            "run_echa_use",
            "run_echa_ghs",
            "run_source_origin",
            "run_pov_toxpi",
            "detection_threshold",
            "cache_enabled",
            "identifier_max_workers",
            "epi_max_workers",
            "comptox_max_workers",
            "echa_max_workers",
            "echa_ghs_max_workers",
            "source_origin_max_workers",
            "dbe_x_min",
            "dbe_x_max",
            "dbe_y_min",
            "dbe_y_max",
            "vk_x_min",
            "vk_x_max",
            "vk_y_min",
            "vk_y_max",
            "candidate_top_n",
            "display_top_n",
            "evidence_per_compound_top_n",
            "evidence_global_use_top_n",
            "peak_area_weight",
            "pbm_weight",
            "df_weight",
            "robustness_enabled",
            "perturbation_percent",
            "robustness_iterations",
            "robustness_seed",
        ):
            self.assertIn(setting, settings_block)
        self.assertIn("RESULT_CACHE_KEYS", page_text[invalidate_index:start_index])

    def test_page_6_skips_oversized_pngs_and_explains_missing_toxpi_evidence_charts(self):
        module_spec = importlib.util.find_spec("src.image_safety")
        self.assertIsNotNone(module_spec)
        if module_spec is None:
            return
        image_safety = importlib.import_module("src.image_safety")
        oversized_png = (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", 33_700, 6_159)
        )
        self.assertEqual(image_safety.png_dimensions(oversized_png), (33_700, 6_159))
        self.assertTrue(image_safety.is_png_over_pixel_limit(oversized_png, 50_000_000))

        page_path = Path("pages/6_一键批量查询.py")
        page_text = page_path.read_text(encoding="utf-8")
        self.assertIn("is_png_over_pixel_limit", page_text)
        self.assertIn("_render_chart_image", page_text)
        self.assertIn("_render_missing_evidence_chart_notice", page_text)
        self.assertIn(
            "if is_png_over_pixel_limit(chart.png, MAX_CHART_PIXELS)",
            page_text,
        )

        function_node = next(
            node
            for node in ast.parse(page_text).body
            if isinstance(node, ast.FunctionDef) and node.name == "_render_chart_image"
        )
        isolated_module = ast.fix_missing_locations(ast.Module(body=[function_node], type_ignores=[]))
        fake_streamlit = SimpleNamespace(warnings=[], images=[])
        fake_streamlit.warning = fake_streamlit.warnings.append
        fake_streamlit.image = lambda *args, **kwargs: fake_streamlit.images.append((args, kwargs))
        namespace = {
            "MAX_CHART_PIXELS": 50_000_000,
            "is_png_over_pixel_limit": image_safety.is_png_over_pixel_limit,
            "png_dimensions": image_safety.png_dimensions,
            "st": fake_streamlit,
        }
        exec(compile(isolated_module, str(page_path), "exec"), namespace)

        namespace["_render_chart_image"](
            SimpleNamespace(title="Oversized", png=oversized_png)
        )

        self.assertEqual(len(fake_streamlit.warnings), 1)
        self.assertEqual(fake_streamlit.images, [])

    def test_page_6_renders_module_dashboard_without_removing_exports(self):
        with open("pages/6_一键批量查询.py", encoding="utf-8") as page_file:
            page_text = page_file.read()

        self.assertIn("def _render_result_dashboard", page_text)
        self.assertIn("st.tabs", page_text)
        self.assertIn("_result_dashboard_groups(result, charts)", page_text)
        self.assertIn('st.selectbox("查看结果表"', page_text)
        self.assertIn("Auto_Query_Workflow_Results.zip", page_text)

    def test_page_6_formats_toxpi_tables_to_four_decimals(self):
        page_text = Path("pages/6_一键批量查询.py").read_text(encoding="utf-8")

        self.assertIn("toxpi_dataframe_column_config", page_text)
        show_dataframe_source = page_text.split("def _show_dataframe(frame):", 1)[1].split(
            "\ndef ",
            1,
        )[0]
        self.assertIn("column_config=toxpi_dataframe_column_config(", show_dataframe_source)
        self.assertIn("st.column_config.NumberColumn", show_dataframe_source)


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


def complete_epi_rows(compounds):
    compounds = list(compounds)
    count = len(compounds)
    return pd.DataFrame(
        {
            "compound": compounds,
            "smiles": ["CC"] * count,
            "cas": [""] * count,
            "status": ["success"] * count,
            "molecular_weight": [100.0] * count,
            "henry_atm_m3_mol": [1.0e-5] * count,
            "log_kow": [2.0] * count,
            "level3_air_half_life_hours": [10.0] * count,
            "level3_water_half_life_hours": [20.0] * count,
            "level3_soil_half_life_hours": [30.0] * count,
            "log_baf": [1.0] * count,
        }
    )


def _result_with_epi_retry_input(failed_compounds):
    failed_compounds = list(failed_compounds)
    compounds = ["Seed A", *failed_compounds]
    universe = pd.DataFrame(
        {
            "compound": compounds,
            "smiles": ["CO", *["CC"] * len(failed_compounds)],
            "cas": ["64-17-5", *["67-56-1"] * len(failed_compounds)],
        }
    )
    seed_rows = complete_epi_rows(["Seed A"])
    seed_rows["smiles"] = "CO"
    resolution = auto_query_workflow.resolve_epi_sources(
        universe,
        seed_rows,
        pd.DataFrame(),
    )
    tables = OrderedDict(
        [
            ("Identifier_Completion", _completed_identifier_rows(compounds)),
            ("EPI_Identity_Universe", universe.copy()),
            ("EPI_Results", resolution.results),
            ("EPI_Raw_Results", resolution.raw_results),
            ("EPI_Errors", resolution.errors),
            ("EPI_Completeness", resolution.completeness),
            ("EPI_Source_Provenance", resolution.provenance),
            ("EPI_Match_Audit", resolution.match_audit),
            ("EPI_Conflict_Audit", resolution.conflict_audit),
            ("EPI_Query_Attempts", resolution.query_attempts),
            ("EPI_Retry_Input", resolution.query_input),
        ]
    )
    return AutoWorkflowResult(
        mapping=AutoWorkflowMapping(),
        representative_table=pd.DataFrame(
            {
                "Name": compounds,
                "formula": ["C2H6O"] * len(compounds),
                "Group_Area": [100.0] * len(compounds),
            }
        ),
        tables=tables,
        step_status=pd.DataFrame(
            columns=["step", "status", "rows", "message"]
        ),
        warnings=pd.DataFrame(columns=["stage", "message"]),
    )


def _checkpoint_for(result):
    return AutoWorkflowCheckpoint(
        run_id=generate_run_token(),
        input_signature="checkpoint-input",
        settings_signature="checkpoint-settings",
        selected_steps=("EPI Suite 环境归趋",),
        finished_steps=(),
        current_step="EPI Suite 环境归趋",
        status="failed",
        result=result,
        error_message="retry incomplete",
        updated_at=datetime.now(timezone.utc).isoformat(),
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
                "EPA_PUC_Pie_Data",
                extract_top_product_use_category_data(comptox_candidates, universe),
            ),
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


def _example_toxpi_tables(
    compounds=("Compound A", "Compound B"),
    per_compound_top_n=10,
    global_use_top_n=30,
):
    return OrderedDict(
        [
            (
                "ToxPi_Results",
                pd.DataFrame(
                    {
                        "compound": list(compounds),
                        "final_rank": list(range(1, len(compounds) + 1)),
                    }
                ),
            ),
            (
                "ToxPi_Settings",
                pd.DataFrame(
                    {
                        "setting": [
                            "evidence_per_compound_top_n",
                            "evidence_global_use_top_n",
                        ],
                        "value": [per_compound_top_n, global_use_top_n],
                    }
                ),
            ),
        ]
    )


if __name__ == "__main__":
    unittest.main()
