import unittest
import sys
import json
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.echa_use import resolve_substance
from src.identifier_resolver import (
    _best_echa_query,
    _pubchem_get_json,
    build_identifier_query_variants,
    build_epi_input_workbook,
    merge_identifier_resolutions,
    resolve_pubchem_by_cas,
    resolve_pubchem_by_name,
    run_identifier_completion_batch,
    resolve_chemspider,
)
from src.query_cache import use_cache_path


class EchaSearchOrderTests(unittest.TestCase):
    @patch("src.echa_use.search_substances", return_value=[])
    def test_resolve_substance_tries_smiles_before_compound(self, search_substances):
        resolve_substance(
            pd.Series({"smiles": "CCO", "compound": "Ethanol"}),
            base_url="https://example.test/",
        )

        self.assertEqual(
            [call.args[0] for call in search_substances.call_args_list],
            ["CCO", "Ethanol"],
        )

    def test_best_echa_query_retains_smiles_and_compound_fallback(self):
        query = _best_echa_query(
            {
                "echa_id": "",
                "ec": "",
                "cas": "",
                "smiles": "CCO",
                "compound": "Ethanol",
                "resolved_name": "",
            }
        )

        self.assertEqual(query["smiles"], "CCO")
        self.assertEqual(query["compound"], "Ethanol")


class IdentifierQueryVariantTests(unittest.TestCase):
    def test_build_identifier_query_variants_keeps_name_and_smiles_separate(self):
        variants = build_identifier_query_variants(
            pd.Series({"compound": "Ethanol", "smiles": "CCO"})
        )

        self.assertEqual(
            variants,
            [
                {"query_source": "名称", "query_value": "Ethanol"},
                {"query_source": "SMILES", "query_value": "CCO"},
            ],
        )

    def test_merge_same_cas_does_not_create_an_identity_conflict(self):
        merged, warnings = merge_identifier_resolutions(
            pd.Series({"compound": "Ethyl alcohol", "smiles": "CCO"}),
            {
                "pubchem_cid": "702",
                "smiles": "CCO",
                "cas": "64-17-5",
                "status": "Matched PubChem Name",
            },
            {
                "pubchem_cid": "999",
                "smiles": "OCC",
                "cas": "64-17-5",
                "status": "Matched PubChem SMILES",
            },
        )

        self.assertEqual(merged["primary_identity_source"], "SMILES")
        self.assertEqual(merged["identity_conflict"], "")
        self.assertEqual(warnings, [])


class PubChemCasTests(unittest.TestCase):
    @patch("src.identifier_resolver.urllib.request.urlopen")
    def test_pubchem_get_json_uses_query_cache(self, urlopen):
        response = unittest.mock.MagicMock()
        response.read.return_value = json.dumps({"IdentifierList": {"CID": [702]}}).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as tmpdir:
            with use_cache_path(Path(tmpdir) / "queries.sqlite3"):
                first = _pubchem_get_json(
                    "compound/name/Ethanol/cids/JSON",
                    base_url="https://example.test/",
                    timeout=1,
                )
                second = _pubchem_get_json(
                    "compound/name/Ethanol/cids/JSON",
                    base_url="https://example.test/",
                    timeout=1,
                )

        self.assertEqual(first, {"IdentifierList": {"CID": [702]}})
        self.assertEqual(second, {"IdentifierList": {"CID": [702]}})
        urlopen.assert_called_once()

    @patch("src.identifier_resolver._pubchem_get_json")
    def test_resolve_pubchem_by_name_returns_cid_and_smiles(self, get_json):
        get_json.side_effect = [
            {"IdentifierList": {"CID": [702]}},
            {
                "PropertyTable": {
                    "Properties": [
                        {"IUPACName": "ethanol", "CanonicalSMILES": "CCO"}
                    ]
                }
            },
            {
                "InformationList": {
                    "Information": [{"Synonym": ["Ethanol", "64-17-5"]}]
                }
            },
        ]

        result = resolve_pubchem_by_name("Ethanol", base_url="https://example.test/")

        self.assertEqual(result["pubchem_cid"], "702")
        self.assertEqual(result["smiles"], "CCO")
        self.assertEqual(result["cas"], "64-17-5")
        self.assertEqual(result["status"], "Matched PubChem Name")
        self.assertEqual(
            get_json.call_args_list[0].args[0],
            "compound/name/Ethanol/cids/JSON",
        )

    @patch("src.identifier_resolver._pubchem_get_json")
    def test_resolve_pubchem_by_name_returns_formula_and_molecular_weight(self, get_json):
        get_json.side_effect = [
            {"IdentifierList": {"CID": [702]}},
            {
                "PropertyTable": {
                    "Properties": [
                        {
                            "IUPACName": "ethanol",
                            "CanonicalSMILES": "CCO",
                            "MolecularFormula": "C2H6O",
                            "MolecularWeight": 46.069,
                        }
                    ]
                }
            },
            {
                "InformationList": {
                    "Information": [{"Synonym": ["Ethanol", "64-17-5"]}]
                }
            },
        ]

        result = resolve_pubchem_by_name("Ethanol", base_url="https://example.test/")

        self.assertEqual(result["pubchem_formula"], "C2H6O")
        self.assertEqual(result["pubchem_molecular_weight"], 46.069)
        self.assertIn("MolecularFormula,MolecularWeight", get_json.call_args_list[1].args[0])

    @patch("src.identifier_resolver._pubchem_get_json")
    def test_resolve_pubchem_by_cas_returns_cid_and_smiles(self, get_json):
        get_json.side_effect = [
            {"IdentifierList": {"CID": [702]}},
            {
                "PropertyTable": {
                    "Properties": [
                        {"IUPACName": "ethanol", "CanonicalSMILES": "CCO"}
                    ]
                }
            },
            {
                "InformationList": {
                    "Information": [{"Synonym": ["Ethanol", "64-17-5"]}]
                }
            },
        ]

        result = resolve_pubchem_by_cas("64-17-5", base_url="https://example.test/")

        self.assertEqual(result["pubchem_cid"], "702")
        self.assertEqual(result["smiles"], "CCO")
        self.assertEqual(result["cas"], "64-17-5")
        self.assertEqual(result["status"], "Matched PubChem CAS")
        self.assertEqual(
            get_json.call_args_list[0].args[0],
            "compound/name/64-17-5/cids/JSON",
        )


class ChemSpiderTests(unittest.TestCase):
    def test_chemspider_normalizes_first_match(self):
        record = types.SimpleNamespace(csid=42, smiles="CCO", common_name="ethanol", synonyms=["64-17-5"])
        module = types.SimpleNamespace(ChemSpider=lambda key: types.SimpleNamespace(search=lambda query: [record]))
        with patch.dict(sys.modules, {"chemspipy": module}):
            result = resolve_chemspider({"smiles": "CCO"}, api_key="test-key")
        self.assertEqual(result["csid"], "42")
        self.assertEqual(result["cas"], "64-17-5")


class ChemSpiderSelectionTests(unittest.TestCase):
    def test_single_worker_batch_reports_lifecycle_activity(self):
        events = []

        run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"]}),
            use_epa=False,
            use_echa=False,
            use_pubchem=False,
            delay_seconds=0,
            max_workers=1,
            activity_callback=events.append,
        )

        self.assertEqual([event["event"] for event in events], ["started", "completed"])

    @patch("src.identifier_resolver.resolve_chemspider")
    def test_batch_skips_chemspider_when_user_disables_it(self, resolve_chemspider):
        completed, _ = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"]}),
            use_epa=False,
            use_echa=False,
            use_pubchem=False,
            use_chemspider=False,
            chemspider_api_key="test-key",
            delay_seconds=0,
        )

        resolve_chemspider.assert_not_called()
        self.assertEqual(completed.loc[0, "chemspider_match_status"], "")

    @patch("src.identifier_resolver.resolve_chemspider")
    def test_batch_uses_chemspider_when_user_enables_it(self, resolve_chemspider):
        resolve_chemspider.return_value = {
            "smiles": "CCO",
            "cas": "64-17-5",
            "preferred_name": "ethanol",
            "status": "Matched ChemSpider",
            "message": "",
        }

        completed, _ = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"], "smiles": ["CCO"]}),
            use_epa=False,
            use_echa=False,
            use_pubchem=False,
            use_chemspider=True,
            chemspider_api_key="test-key",
            delay_seconds=0,
        )

        resolve_chemspider.assert_called_once()
        self.assertEqual(completed.loc[0, "cas"], "64-17-5")
        self.assertEqual(
            completed.loc[0, "chemspider_match_status"], "Matched ChemSpider"
        )


class IdentifierCompletionPubChemTests(unittest.TestCase):
    @patch("src.identifier_resolver.resolve_pubchem_by_smiles")
    @patch("src.identifier_resolver.resolve_pubchem_by_name")
    def test_batch_queries_name_and_smiles_and_keeps_smiles_identity_primary(
        self, resolve_by_name, resolve_by_smiles
    ):
        resolve_by_name.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "Ethanol",
            "cas": "64-17-5",
            "status": "Matched PubChem Name",
            "message": "",
        }
        resolve_by_smiles.return_value = {
            "pubchem_cid": "241",
            "smiles": "c1ccccc1",
            "preferred_name": "Benzene",
            "cas": "71-43-2",
            "status": "Matched PubChem SMILES",
            "message": "",
        }

        completed, warnings = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"], "smiles": ["c1ccccc1"]}),
            use_epa=False,
            use_echa=False,
            delay_seconds=0,
        )

        resolve_by_name.assert_called_once()
        resolve_by_smiles.assert_called_once()
        self.assertEqual(completed.loc[0, "pubchem_cid"], "241")
        self.assertEqual(completed.loc[0, "primary_identity_source"], "SMILES")
        self.assertEqual(completed.loc[0, "identity_conflict"], "是")
        self.assertEqual(completed.loc[0, "name_query_status"], "Matched PubChem Name")
        self.assertEqual(completed.loc[0, "smiles_query_status"], "Matched PubChem SMILES")
        self.assertTrue(warnings["stage"].eq("identity_conflict").any())

    @patch("src.identifier_resolver.resolve_pubchem_by_name")
    def test_batch_keeps_name_identity_when_smiles_is_not_supplied(self, resolve_by_name):
        resolve_by_name.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "pubchem_formula": "C2H6O",
            "pubchem_molecular_weight": 46.069,
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "ec": "",
            "dtxsid": "",
            "status": "Matched PubChem Name",
            "message": "",
        }

        completed, warnings = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"]}),
            use_epa=False,
            use_echa=False,
            delay_seconds=0,
        )

        self.assertEqual(completed.loc[0, "pubchem_cid"], "702")
        self.assertEqual(completed.loc[0, "pubchem_formula"], "C2H6O")
        self.assertEqual(completed.loc[0, "pubchem_molecular_weight"], 46.069)
        self.assertEqual(completed.loc[0, "pubchem_match_status"], "Matched PubChem Name")
        self.assertEqual(completed.loc[0, "primary_identity_source"], "名称")
        self.assertEqual(completed.loc[0, "cas"], "64-17-5")
        self.assertEqual(completed.loc[0, "smiles"], "CCO")
        self.assertTrue(warnings.empty)
        resolve_by_name.assert_called_once_with(
            "Ethanol",
            base_url="https://pubchem.ncbi.nlm.nih.gov/rest/pug/",
            timeout=60,
        )

    @patch("src.identifier_resolver.resolve_pubchem_by_name")
    def test_threaded_batch_preserves_name_resolution_columns(self, resolve_by_name):
        resolve_by_name.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "status": "Matched PubChem Name",
            "message": "",
        }

        completed, warnings = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol", "Ethanol"]}),
            use_epa=False,
            use_echa=False,
            delay_seconds=0,
            max_workers=2,
        )

        self.assertEqual(len(completed), 2)
        self.assertTrue(completed["name_query_status"].eq("Matched PubChem Name").all())
        self.assertTrue(completed["primary_identity_source"].eq("名称").all())
        self.assertTrue(warnings.empty)

    @patch("src.identifier_resolver.resolve_pubchem_by_cas")
    @patch("src.identifier_resolver.resolve_pubchem_by_name")
    def test_batch_falls_back_to_cas_when_name_has_no_cid(self, resolve_by_name, resolve_by_cas):
        resolve_by_name.return_value = {
            "pubchem_cid": "",
            "status": "PubChem Name not matched",
            "message": "No CID",
        }
        resolve_by_cas.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "ec": "",
            "dtxsid": "",
            "status": "Matched PubChem CAS",
            "message": "",
        }

        completed, _ = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"], "cas": ["64-17-5"]}),
            use_epa=False,
            use_echa=False,
            delay_seconds=0,
        )

        self.assertEqual(completed.loc[0, "pubchem_cid"], "702")
        self.assertEqual(completed.loc[0, "pubchem_match_status"], "Matched PubChem CAS")
        resolve_by_name.assert_called_once()
        resolve_by_cas.assert_called_once()

    @patch("src.identifier_resolver.resolve_substance")
    @patch("src.identifier_resolver.resolve_dtxsid")
    @patch("src.identifier_resolver.resolve_pubchem_by_name")
    def test_batch_queries_default_sources_from_original_compound_name(
        self, resolve_by_name, resolve_dtxsid, resolve_substance
    ):
        resolve_by_name.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "ec": "",
            "dtxsid": "",
            "status": "Matched PubChem Name",
            "message": "",
        }
        resolve_dtxsid.return_value = {
            "dtxsid": "DTXSID9020584",
            "matched_name": "Ethanol",
            "matched_cas": "111-11-1",
            "status": "Matched EPA Name",
            "message": "",
        }
        resolve_substance.return_value = {
            "echa_id": "100.000.526",
            "matched_name": "Ethanol",
            "matched_cas": "222-22-2",
            "matched_ec": "200-578-6",
            "status": "Matched ECHA Name",
            "message": "",
        }

        completed, warnings = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"]}),
            use_pubchem=True,
            use_epa=True,
            use_echa=True,
            delay_seconds=0,
        )

        epa_query = resolve_dtxsid.call_args.args[0]
        echa_query = resolve_substance.call_args.args[0]
        self.assertEqual(epa_query["compound"], "ethanol")
        self.assertEqual(epa_query["cas"], "64-17-5")
        self.assertEqual(epa_query["smiles"], "CCO")
        self.assertEqual(echa_query["compound"], "ethanol")
        self.assertEqual(echa_query["cas"], "64-17-5")
        self.assertEqual(echa_query["smiles"], "CCO")
        self.assertEqual(completed.loc[0, "cas"], "64-17-5")
        self.assertEqual(completed.loc[0, "smiles"], "CCO")
        self.assertEqual(completed.loc[0, "dtxsid"], "DTXSID9020584")
        self.assertEqual(completed.loc[0, "ec"], "200-578-6")
        self.assertEqual(completed.loc[0, "echa_id"], "100.000.526")
        self.assertTrue(warnings.empty)

    @patch("src.identifier_resolver.resolve_chemspider")
    @patch("src.identifier_resolver.resolve_pubchem_by_name")
    def test_batch_keeps_chemspider_optional_but_queries_it_when_enabled(
        self, resolve_by_name, resolve_chemspider
    ):
        resolve_by_name.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "ec": "",
            "dtxsid": "",
            "status": "Matched PubChem Name",
            "message": "",
        }
        resolve_chemspider.return_value = {
            "smiles": "CCO",
            "cas": "111-11-1",
            "preferred_name": "ethyl alcohol",
            "status": "Matched ChemSpider",
            "message": "",
        }

        completed, warnings = run_identifier_completion_batch(
            pd.DataFrame({"compound": ["Ethanol"]}),
            use_epa=False,
            use_echa=False,
            use_pubchem=True,
            use_chemspider=True,
            chemspider_api_key="test-key",
            delay_seconds=0,
        )

        self.assertEqual(resolve_chemspider.call_args.args[0]["compound"], "Ethanol")
        self.assertEqual(completed.loc[0, "cas"], "64-17-5")
        self.assertEqual(completed.loc[0, "smiles"], "CCO")
        self.assertEqual(
            completed.loc[0, "chemspider_match_status"], "Matched ChemSpider"
        )
        self.assertTrue(warnings.empty)

    @patch("src.identifier_resolver.resolve_pubchem_by_cas")
    def test_batch_fills_cid_and_smiles_from_cas(self, resolve_by_cas):
        resolve_by_cas.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "ec": "",
            "dtxsid": "",
            "status": "Matched PubChem CAS",
            "message": "",
        }

        completed, warnings = run_identifier_completion_batch(
            pd.DataFrame({"cas": ["64-17-5"]}),
            use_epa=False,
            use_echa=False,
            delay_seconds=0,
        )

        self.assertEqual(completed.loc[0, "pubchem_cid"], "702")
        self.assertEqual(completed.loc[0, "smiles"], "CCO")
        self.assertTrue(warnings.empty)

    @patch("src.identifier_resolver.resolve_pubchem_by_smiles")
    @patch("src.identifier_resolver.resolve_pubchem_by_cas")
    def test_batch_skips_cas_fallback_when_smiles_has_a_stable_identity(
        self, resolve_by_cas, resolve_by_smiles
    ):
        resolve_by_cas.return_value = {
            "pubchem_cid": "",
            "status": "PubChem CAS not matched",
            "message": "No CID",
        }
        resolve_by_smiles.return_value = {
            "pubchem_cid": "702",
            "smiles": "CCO",
            "preferred_name": "ethanol",
            "cas": "64-17-5",
            "ec": "",
            "dtxsid": "",
            "status": "Matched PubChem SMILES",
            "message": "",
        }

        completed, _ = run_identifier_completion_batch(
            pd.DataFrame({"cas": ["64-17-5"], "smiles": ["CCO"]}),
            use_epa=False,
            use_echa=False,
            delay_seconds=0,
        )

        self.assertEqual(completed.loc[0, "pubchem_cid"], "702")
        resolve_by_cas.assert_not_called()
        resolve_by_smiles.assert_called_once()


class IdentifierCompletionExportTests(unittest.TestCase):
    def test_epi_input_workbook_contains_only_identifier_columns(self):
        completed = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "pubchem_cid": ["702"],
                "cas": ["64-17-5"],
                "ec": ["200-578-6"],
                "dtxsid": ["DTXSID9020584"],
                "echa_id": ["100.000.526"],
                "completion_status": ["已补全"],
            }
        )

        workbook = build_epi_input_workbook(completed)
        exported = pd.read_excel(workbook, sheet_name="EPISuite_Input")

        self.assertEqual(
            list(exported.columns),
            ["compound", "smiles", "cas", "ec", "dtxsid", "echa_id"],
        )
        self.assertEqual(exported.loc[0, "compound"], "Ethanol")
        self.assertEqual(exported.loc[0, "smiles"], "CCO")
        self.assertNotIn("pubchem_cid", exported.columns)


if __name__ == "__main__":
    unittest.main()
