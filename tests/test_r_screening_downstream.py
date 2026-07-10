import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.r_screening_replica.downstream import (
    DownstreamConfig,
    build_identifier_input,
    build_pov_lrtp_input,
    run_downstream_pipeline,
)


class RScreeningDownstreamTests(unittest.TestCase):
    def test_build_identifier_input_uses_screening_name_and_optional_identifiers(self):
        raw = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "formula": ["C2 H6 O"],
                "SMILES": [" CCO "],
                "CAS": [" 64-17-5 "],
            }
        )

        result = build_identifier_input(raw, smiles_col="SMILES", cas_col="CAS")

        self.assertEqual(
            result.to_dict("records"),
            [
                {
                    "compound": "Ethanol",
                    "smiles": "CCO",
                    "cas": "64-17-5",
                    "ec": "",
                    "dtxsid": "",
                    "echa_id": "",
                }
            ],
        )

    def test_build_pov_lrtp_input_combines_screening_pubchem_and_epi_outputs(self):
        screening = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "formula": ["C2 H6 O"],
                "Group_Area": [123.0],
                "HH_alk": [10.0],
                "WH_alk": [20.0],
            }
        )
        identifiers = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "pubchem_cid": ["702"],
                "pubchem_formula": ["C2H6O"],
                "pubchem_molecular_weight": [46.069],
                "pubchem_match_status": ["Matched PubChem Name"],
                "cas": ["64-17-5"],
            }
        )
        epi = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "molecular_weight": [46.07],
                "log_kow": [-0.31],
                "henry_atm_m3_mol": [5.0e-6],
                "log_baf": [0.68],
                "level3_air_half_life_hours": [8.0],
                "level3_water_half_life_hours": [180.0],
                "level3_soil_half_life_hours": [300.0],
                "level3_persistence_hours": [240.0],
            }
        )

        result = build_pov_lrtp_input(
            screening,
            identifiers,
            epi,
            sample_cols=["HH_alk", "WH_alk"],
        )

        expected_log_kaw = np.log10(5.0e-6 / (8.205736e-5 * 298.15))
        self.assertAlmostEqual(result.loc[0, "Log_Kaw_used"], expected_log_kaw, places=12)
        self.assertEqual(result.loc[0, "Name"], "Ethanol")
        self.assertEqual(result.loc[0, "Compound_CID"], "702")
        self.assertEqual(result.loc[0, "SMILES"], "CCO")
        self.assertEqual(result.loc[0, "Molecular_Weight"], 46.07)
        self.assertEqual(result.loc[0, "Log_Kow_used"], -0.31)
        self.assertEqual(result.loc[0, "Air_HL"], 8.0)
        self.assertEqual(result.loc[0, "Water_HL"], 180.0)
        self.assertEqual(result.loc[0, "Soil_HL"], 300.0)
        self.assertEqual(result.loc[0, "Log_BAF_Arnot_Gobas"], 0.68)
        self.assertTrue(bool(result.loc[0, "formula_match"]))
        self.assertTrue(bool(result.loc[0, "model_input_complete"]))

    @patch("src.r_screening_replica.downstream.run_pov_lrtp_batch")
    @patch("src.r_screening_replica.downstream.run_epi_web_batch")
    @patch("src.r_screening_replica.downstream.run_identifier_completion_batch")
    def test_run_downstream_pipeline_reuses_existing_resolver_epi_and_pov_modules(
        self,
        run_identifier_completion,
        run_epi_web,
        run_pov_lrtp,
    ):
        screening = pd.DataFrame(
            {
                "Name": ["Ethanol"],
                "formula": ["C2 H6 O"],
                "Group_Area": [123.0],
                "HH_alk": [10.0],
                "WH_alk": [20.0],
            }
        )
        run_identifier_completion.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Ethanol"],
                    "smiles": ["CCO"],
                    "cas": ["64-17-5"],
                    "pubchem_cid": ["702"],
                    "pubchem_formula": ["C2H6O"],
                    "pubchem_molecular_weight": [46.069],
                    "pubchem_match_status": ["Matched PubChem Name"],
                }
            ),
            pd.DataFrame(),
        )
        run_epi_web.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Ethanol"],
                    "molecular_weight": [46.07],
                    "log_kow": [-0.31],
                    "henry_atm_m3_mol": [5.0e-6],
                    "log_baf": [0.68],
                    "level3_air_half_life_hours": [8.0],
                    "level3_water_half_life_hours": [180.0],
                    "level3_soil_half_life_hours": [300.0],
                    "level3_persistence_hours": [240.0],
                }
            ),
            pd.DataFrame({"compound": ["Ethanol"], "raw_json": ["{}"]}),
            pd.DataFrame(),
        )
        run_pov_lrtp.return_value = pd.DataFrame({"Name": ["Ethanol"], "Scores": [1.23]})

        result = run_downstream_pipeline(
            screening,
            DownstreamConfig(sample_cols=["HH_alk", "WH_alk"], identifier_delay_seconds=0, epi_delay_seconds=0),
        )

        run_identifier_completion.assert_called_once()
        run_epi_web.assert_called_once()
        run_pov_lrtp.assert_called_once()
        self.assertEqual(result.pov_lrtp_results.loc[0, "Scores"], 1.23)
        self.assertEqual(result.epi_input.loc[0, "smiles"], "CCO")


if __name__ == "__main__":
    unittest.main()
