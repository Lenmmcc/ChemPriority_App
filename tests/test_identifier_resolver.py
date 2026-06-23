import unittest
from unittest.mock import patch

import pandas as pd

from src.echa_use import resolve_substance
from src.identifier_resolver import (
    _best_echa_query,
    resolve_pubchem_by_cas,
    run_identifier_completion_batch,
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
