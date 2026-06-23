import unittest
import sys
import types
from unittest.mock import patch

import pandas as pd

from src.echa_use import resolve_substance
from src.identifier_resolver import (
    _best_echa_query,
    resolve_pubchem_by_cas,
    run_identifier_completion_batch,
    resolve_chemspider,
)


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


class PubChemCasTests(unittest.TestCase):
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
    def test_batch_falls_back_to_smiles_when_cas_has_no_cid(
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
        resolve_by_cas.assert_called_once()
        resolve_by_smiles.assert_called_once()


if __name__ == "__main__":
    unittest.main()
