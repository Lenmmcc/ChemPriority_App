import unittest

import numpy as np
import pandas as pd

from src.pov_lrtp_replica import PovLrtpInput, calculate_pov_lrtp, run_pov_lrtp_batch


class PovLrtpReplicaTests(unittest.TestCase):
    def test_single_calculation_matches_original_excel_safrole_result(self):
        result = calculate_pov_lrtp(
            PovLrtpInput(
                name="Safrole",
                molar_mass=154.21,
                log_kaw=-2.124,
                log_kow=3.92,
                half_life_air_h=2.59,
                half_life_water_h=900.0,
                half_life_soil_h=1800.0,
            )
        )

        self.assertAlmostEqual(result.pov_days, 94.8691214511751, places=9)
        self.assertAlmostEqual(result.ctd_km, 69.5167144749951, places=9)
        self.assertAlmostEqual(result.te_percent, 0.0003742050944792, places=12)

    def test_batch_calculation_matches_excel_macro_reference_rows(self):
        reference = pd.DataFrame(
            [
                {
                    "Name": "1,2-Benzenedicarboxylic acid, bis(2-methylpropyl) ester",
                    "Molecular_Weight": 278.34409064305294,
                    "Log_Kaw_used": -4.58009629591262,
                    "Log_Kow_used": 4.462999850511551,
                    "Air_HL": 27.717182436074992,
                    "Water_HL": 360.0,
                    "Soil_HL": 720.0,
                    "expected_pov": 43.196038658304523,
                    "expected_ctd": 498.29494420126446,
                    "expected_te": 0.31500576430867616,
                },
                {
                    "Name": "Phthalic acid, hex-3-yl octyl ester",
                    "Molecular_Weight": 362.5037950696904,
                    "Log_Kaw_used": -3.6200962586176497,
                    "Log_Kow_used": 7.483100086450577,
                    "Air_HL": 13.255889330848447,
                    "Water_HL": 360.0,
                    "Soil_HL": 720.0,
                    "expected_pov": 43.271170351888,
                    "expected_ctd": 491.06283751091138,
                    "expected_te": 0.29947591284887781,
                },
                {
                    "Name": "Phthalic acid, mono-sec-butyl ester",
                    "Molecular_Weight": 222.23762102529463,
                    "Log_Kaw_used": -7.550096089008577,
                    "Log_Kow_used": 2.766899891197681,
                    "Air_HL": 40.70062744678752,
                    "Water_HL": 360.0,
                    "Soil_HL": 720.0,
                    "expected_pov": 41.711589595638785,
                    "expected_ctd": 37.372273071256551,
                    "expected_te": 0.1221936215828955,
                },
            ]
        )

        result = run_pov_lrtp_batch(reference)

        self.assertEqual(result["Status"].tolist(), ["ok", "ok", "ok"])
        for i, row in result.iterrows():
            self.assertAlmostEqual(row["POV_days"], reference.loc[i, "expected_pov"], places=9)
            self.assertAlmostEqual(row["CTD_km"], reference.loc[i, "expected_ctd"], places=9)
            self.assertAlmostEqual(row["TE_percent"], reference.loc[i, "expected_te"], places=12)

    def test_batch_adds_scores_from_log_pov_log_baf_and_log_te(self):
        data = pd.DataFrame(
            [
                {
                    "Name": "1,2-Benzenedicarboxylic acid, bis(2-methylpropyl) ester",
                    "Molecular_Weight": 278.34409064305294,
                    "Log_Kaw_used": -4.58009629591262,
                    "Log_Kow_used": 4.462999850511551,
                    "Air_HL": 27.717182436074992,
                    "Water_HL": 360.0,
                    "Soil_HL": 720.0,
                    "Log_BAF_Arnot_Gobas": 1.52,
                }
            ]
        )

        result = run_pov_lrtp_batch(data)
        expected_score = (
            np.log10(result.loc[0, "POV_days"])
            + data.loc[0, "Log_BAF_Arnot_Gobas"]
            + np.log10(result.loc[0, "TE_percent"])
        )

        self.assertAlmostEqual(result.loc[0, "P_B_LRTP_score"], expected_score, places=12)
        self.assertAlmostEqual(result.loc[0, "Scores"], expected_score, places=12)
        self.assertEqual(
            result.loc[0, "Score_Assumption"],
            "log10(POV_days)+Log_BAF+log10(TE_percent_as_model_output)",
        )

    def test_batch_leaves_scores_blank_when_log_baf_is_missing(self):
        data = pd.DataFrame(
            [
                {
                    "Name": "Phthalic acid, mono-sec-butyl ester",
                    "Molecular_Weight": 222.23762102529463,
                    "Log_Kaw_used": -7.550096089008577,
                    "Log_Kow_used": 2.766899891197681,
                    "Air_HL": 40.70062744678752,
                    "Water_HL": 360.0,
                    "Soil_HL": 720.0,
                }
            ]
        )

        result = run_pov_lrtp_batch(data)

        self.assertTrue(np.isnan(result.loc[0, "P_B_LRTP_score"]))
        self.assertTrue(np.isnan(result.loc[0, "Scores"]))

    def test_rejects_nonpositive_half_life_inputs(self):
        with self.assertRaisesRegex(ValueError, "half_life_air_h must be > 0"):
            calculate_pov_lrtp(
                PovLrtpInput(
                    name="bad",
                    molar_mass=100.0,
                    log_kaw=-3.0,
                    log_kow=4.0,
                    half_life_air_h=0.0,
                    half_life_water_h=360.0,
                    half_life_soil_h=720.0,
                )
            )


if __name__ == "__main__":
    unittest.main()
