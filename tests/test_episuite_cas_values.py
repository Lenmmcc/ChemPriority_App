import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

from src import episuite_io
from src.query_cache import cache_control, use_cache_path


ETHANOL_CAS_AND_SMILES_RESPONSE = {
    "chemicalProperties": {
        "cas": "000064-17-5",
        "smiles": "CCO",
        "name": "ETHANOL",
        "molecularFormula": "C2 H6 O1",
        "molecularWeight": 46.07,
    },
    "parameters": {
        "cas": "64-17-5",
        "smiles": "CCO",
    },
    "logKow": {
        "selectedValue": {"value": -0.31, "units": None, "valueType": "EXPERIMENTAL"},
        "estimatedValue": {"value": -0.1411999762058258, "units": "", "valueType": "ESTIMATED"},
        "experimentalValue": None,
    },
    "waterSolubilityFromWaterNt": {
        "selectedValue": {"value": 1000000.0, "units": "mg/L", "valueType": "EXPERIMENTAL"},
        "estimatedValue": {"value": 452462.28125, "units": "mg/L", "valueType": "ESTIMATED"},
        "experimentalValue": None,
    },
    "waterSolubilityFromLogKow": {
        "selectedValue": {"value": 1000000.0, "units": "mg/L", "valueType": "EXPERIMENTAL"},
        "estimatedValue": {"value": 857740.375, "units": "mg/L", "valueType": "ESTIMATED"},
        "experimentalValue": None,
    },
    "vaporPressure": {
        "selectedValue": {"value": 59.3, "units": "mmHg", "valueType": "EXPERIMENTAL"},
        "estimatedValue": {"value": 70.6, "units": "mmHg", "valueType": "ESTIMATED"},
        "experimentalValue": None,
    },
    "henrysLawConstant": {
        "selectedValue": {"value": 5.0e-6, "units": "atm-m3/mol", "valueType": "EXPERIMENTAL"},
        "estimatedValue": {"value": 6.1e-6, "units": "atm-m3/mol", "valueType": "ESTIMATED"},
        "experimentalValue": None,
    },
    "logKoc": {
        "selectedValue": {"value": 1.2, "units": "L/kg", "valueType": "EXPERIMENTAL"},
        "estimatedValue": {"value": 1.05, "units": "L/kg", "valueType": "ESTIMATED"},
        "experimentalValue": None,
    },
    "biodegradationRate": {
        "models": [
            {"name": "Ultimate Biodegradation Timeframe", "value": 2.1, "description": "weeks"},
            {"name": "MITI Linear Model Prediction", "value": 0.72, "description": "readily biodegradable"},
        ],
    },
    "atmosphericHalfLife": {
        "estimatedValue": {"value": 10.2, "units": "hours", "valueType": "ESTIMATED"},
        "estimatedHydroxylRadicalReactionRateConstant": {
            "value": 3.3e-12,
            "units": "cm3/molecule-sec",
            "valueType": "ESTIMATED",
        },
    },
    "bioconcentration": {
        "bioconcentrationFactor": 3.2,
        "logBioconcentrationFactor": 0.51,
        "bioaccumulationFactor": 4.8,
        "logBioaccumulationFactor": 0.68,
    },
    "sewageTreatmentModel": {
        "model": {
            "TotalRemoval": {"Percent": 88.1},
            "FinalEffluent": {"Percent": 11.9},
        },
    },
    "fugacityModel": {
        "model": {
            "Air": [{"MassAmount": 12.0, "HalfLife": 8.0}],
            "Water": [{"MassAmount": 70.0, "HalfLife": 180.0}],
            "Soil": [{"MassAmount": 15.0, "HalfLife": 300.0}],
            "Sediment": [{"MassAmount": 3.0, "HalfLife": 900.0}],
            "Persistence": 240.0,
        },
    },
    "waterVolatilization": {
        "riverHalfLifeHours": 4.5,
        "lakeHalfLifeHours": 33.0,
    },
    "ecosar": {
        "modelResults": [
            {
                "className": "Neutral Organics",
                "organism": "Fish",
                "duration": "96-hr",
                "endpoint": "LC50",
                "concentration": 13000.0,
                "units": "mg/L",
                "maxLogKow": 5.0,
                "warnings": ["Above solubility limit"],
            },
            {
                "className": "Neutral Organics",
                "organism": "Daphnid",
                "duration": "48-hr",
                "endpoint": "LC50",
                "concentration": 12000.0,
                "units": "mg/L",
            },
        ],
    },
}


class EPISuiteCasValueTests(unittest.TestCase):
    def test_normalize_input_columns_keeps_optional_cas(self):
        df = episuite_io.normalize_input_columns(
            pd.DataFrame(
                {
                    "Compound": ["Ethanol"],
                    "Canonical_SMILES": [" CCO "],
                    "CASRN": [" 64-17-5 "],
                }
            )
        )

        self.assertEqual(df.loc[0, "compound"], "Ethanol")
        self.assertEqual(df.loc[0, "smiles"], "CCO")
        self.assertEqual(df.loc[0, "cas"], "64-17-5")

    @patch("src.episuite_io.urllib.request.urlopen")
    def test_call_epi_web_api_sends_cas_and_smiles_when_cas_is_present(self, urlopen):
        response = unittest.mock.MagicMock()
        response.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        with cache_control(False):
            episuite_io.call_epi_web_api("CCO", cas="64-17-5", api_url="https://example.test/api/submit")

        request = urlopen.call_args.args[0]
        self.assertIn("smiles=CCO", request.full_url)
        self.assertIn("cas=64-17-5", request.full_url)

    @patch("src.episuite_io.urllib.request.urlopen")
    def test_call_epi_web_api_reuses_cached_response(self, urlopen):
        response = unittest.mock.MagicMock()
        response.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as tmpdir:
            with use_cache_path(Path(tmpdir) / "queries.sqlite3"):
                first = episuite_io.call_epi_web_api(
                    "CCO",
                    cas="64-17-5",
                    api_url="https://example.test/api/submit",
                )
                second = episuite_io.call_epi_web_api(
                    "CCO",
                    cas="64-17-5",
                    api_url="https://example.test/api/submit",
                )

        self.assertEqual(first, {"ok": True})
        self.assertEqual(second, {"ok": True})
        urlopen.assert_called_once()

    def test_extract_epi_web_summary_keeps_selected_estimated_and_experimental_values(self):
        summary = episuite_io.extract_epi_web_summary(
            "Ethanol",
            "CCO",
            ETHANOL_CAS_AND_SMILES_RESPONSE,
            cas="64-17-5",
        )

        self.assertEqual(summary["cas"], "64-17-5")
        self.assertEqual(summary["epi_cas"], "000064-17-5")
        self.assertEqual(summary["log_kow"], -0.31)
        self.assertEqual(summary["log_kow_selected"], -0.31)
        self.assertEqual(summary["log_kow_estimated"], -0.1411999762058258)
        self.assertEqual(summary["log_kow_experimental"], -0.31)
        self.assertEqual(summary["water_solubility_mg_l"], 1000000.0)
        self.assertEqual(summary["water_solubility_selected"], 1000000.0)
        self.assertEqual(summary["water_solubility_estimated"], 452462.28125)
        self.assertEqual(summary["water_solubility_experimental"], 1000000.0)
        self.assertEqual(summary["vapor_pressure_selected"], 59.3)
        self.assertEqual(summary["vapor_pressure_estimated"], 70.6)
        self.assertEqual(summary["vapor_pressure_experimental"], 59.3)
        self.assertEqual(summary["henry_selected"], 5.0e-6)
        self.assertEqual(summary["henry_estimated"], 6.1e-6)
        self.assertEqual(summary["henry_experimental"], 5.0e-6)
        self.assertEqual(summary["log_koc_selected"], 1.2)
        self.assertEqual(summary["log_koc_estimated"], 1.05)
        self.assertEqual(summary["log_koc_experimental"], 1.2)

    @patch("src.episuite_io.call_epi_web_api")
    def test_run_epi_web_batch_passes_optional_cas_and_records_raw_traceability(self, call_api):
        call_api.return_value = ETHANOL_CAS_AND_SMILES_RESPONSE
        input_df = pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]})

        results, raw_rows, errors = episuite_io.run_epi_web_batch(input_df, delay_seconds=0)

        call_api.assert_called_once_with("CCO", cas="64-17-5", api_url=episuite_io.DEFAULT_EPI_WEB_API, timeout=90)
        self.assertEqual(results.loc[0, "cas"], "64-17-5")
        self.assertEqual(raw_rows.loc[0, "cas"], "64-17-5")
        self.assertEqual(raw_rows.loc[0, "epi_cas"], "000064-17-5")
        self.assertTrue(errors.empty)

    @patch("src.episuite_io.call_epi_web_api")
    def test_run_epi_web_batch_retries_smiles_when_cas_is_not_located(self, call_api):
        call_api.side_effect = [
            RuntimeError("EPI Web Suite 返回 HTTP 404: Could not locate CAS ID, try again with SMILES if available"),
            ETHANOL_CAS_AND_SMILES_RESPONSE,
        ]
        input_df = pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]})

        results, raw_rows, errors = episuite_io.run_epi_web_batch(input_df, delay_seconds=0)

        self.assertEqual(call_api.call_args_list[0].kwargs["cas"], "64-17-5")
        self.assertIsNone(call_api.call_args_list[1].kwargs["cas"])
        self.assertEqual(results.loc[0, "status"], "success")
        self.assertIn("CAS 查询失败，已回退到 SMILES", results.loc[0, "query_note"])
        self.assertIn("CAS 查询失败，已回退到 SMILES", raw_rows.loc[0, "query_note"])
        self.assertTrue(errors.empty)

    @patch("src.episuite_io.call_epi_web_api")
    def test_run_epi_web_batch_keeps_order_and_isolates_parallel_row_failure(self, call_api):
        def fake_call(smiles, **kwargs):
            if smiles == "bad":
                raise RuntimeError("network failed")
            raw = dict(ETHANOL_CAS_AND_SMILES_RESPONSE)
            raw["parameters"] = {"smiles": smiles}
            return raw

        call_api.side_effect = fake_call
        input_df = pd.DataFrame(
            {
                "compound": ["A", "B", "C", "D", "E"],
                "smiles": ["a", "b", "bad", "d", "e"],
            }
        )

        results, raw_rows, errors = episuite_io.run_epi_web_batch(
            input_df,
            delay_seconds=0,
            max_workers=3,
        )

        self.assertEqual(results["compound"].tolist(), ["A", "B", "C", "D", "E"])
        self.assertEqual(results.loc[2, "status"], "failed")
        self.assertEqual(errors.loc[0, "compound"], "C")
        self.assertEqual(len(raw_rows), 4)

    @patch("src.episuite_io.call_epi_web_api")
    def test_build_epi_web_result_tables_splits_raw_json_into_category_tables(self, call_api):
        call_api.return_value = ETHANOL_CAS_AND_SMILES_RESPONSE
        input_df = pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]})
        core, raw_rows, errors = episuite_io.run_epi_web_batch(input_df, delay_seconds=0)

        tables = episuite_io.build_epi_web_result_tables(core, raw_rows, errors)

        expected = {
            "Core_Summary",
            "Properties",
            "Degradation",
            "Fate_Transport",
            "Bioaccumulation",
            "ECOSAR_Aquatic_Toxicity",
            "Model_Metadata",
            "Raw_API_JSON",
            "Warnings",
        }
        self.assertEqual(set(tables), expected)
        core_columns = set(tables["Core_Summary"].columns)
        self.assertNotIn("log_kow", core_columns)
        self.assertNotIn("log_kow_selected", core_columns)
        self.assertEqual(tables["Core_Summary"].loc[0, "log_kow_estimated"], -0.1411999762058258)
        self.assertEqual(tables["Core_Summary"].loc[0, "log_kow_experimental"], -0.31)
        self.assertEqual(tables["Core_Summary"].loc[0, "log_kow_type"], "EXPERIMENTAL")

        property_columns = set(tables["Properties"].columns)
        self.assertNotIn("log_kow_selected", property_columns)
        self.assertNotIn("log_kow_units", property_columns)
        self.assertEqual(tables["Properties"].loc[0, "log_kow_estimated"], -0.1411999762058258)
        self.assertEqual(tables["Properties"].loc[0, "log_kow_experimental"], -0.31)
        self.assertEqual(tables["Properties"].loc[0, "log_kow_type"], "EXPERIMENTAL")
        self.assertEqual(tables["Degradation"].loc[0, "biowin_Ultimate_Biodegradation_Timeframe"], 2.1)
        self.assertEqual(tables["Fate_Transport"].loc[0, "level3_water_percent"], 70.0)
        self.assertEqual(tables["Bioaccumulation"].loc[0, "bcf"], 3.2)
        self.assertEqual(len(tables["ECOSAR_Aquatic_Toxicity"]), 2)
        self.assertEqual(tables["ECOSAR_Aquatic_Toxicity"].loc[0, "endpoint"], "LC50")
        self.assertIn("raw_json", tables["Raw_API_JSON"].columns)

    @patch("src.episuite_io.call_epi_web_api")
    def test_result_workbook_writes_category_tables_and_raw_json(self, call_api):
        call_api.return_value = ETHANOL_CAS_AND_SMILES_RESPONSE
        input_df = pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]})
        core, raw_rows, errors = episuite_io.run_epi_web_batch(input_df, delay_seconds=0)

        workbook = episuite_io.build_result_workbook(
            input_df,
            merged_df=core,
            parsed_df=core,
            warnings_df=errors,
            raw_df=raw_rows,
        )
        sheets = set(load_workbook(workbook, read_only=True).sheetnames)

        self.assertIn("Core_Summary", sheets)
        self.assertIn("ECOSAR_Aquatic_Toxicity", sheets)
        self.assertIn("Raw_API_JSON", sheets)


if __name__ == "__main__":
    unittest.main()
