import math
import unittest

from src.episuite_properties import (
    build_epi_property_enrichment,
    calculate_rdkit_descriptor_fields,
    extract_koawin_partition_fields,
)


class EPISuitePropertyEnrichmentTests(unittest.TestCase):
    def test_extracts_api_coefficients_and_builds_paired_log10_values(self):
        data = {
            "logKow": {"estimatedValue": {"value": 3.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 5.0,
                    "model": {
                        "kow": 1000.0,
                        "kaw": 0.01,
                        "koa": 100000.0,
                        "logKoa": 5.0,
                    },
                }
            },
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertEqual(
            tuple(fields),
            (
                "koawin_log_kow",
                "koawin_kow",
                "koawin_log_koa",
                "koawin_koa",
                "koawin_log_kaw",
                "koawin_kaw",
            ),
        )
        self.assertEqual(fields["koawin_kow"], 1000.0)
        self.assertEqual(fields["koawin_koa"], 100000.0)
        self.assertEqual(fields["koawin_kaw"], 0.01)
        self.assertEqual(fields["koawin_log_kow"], 3.0)
        self.assertEqual(fields["koawin_log_koa"], 5.0)
        self.assertEqual(fields["koawin_log_kaw"], -2.0)
        self.assertEqual(warnings, [])

    def test_recovers_missing_coefficients_from_available_model_logs(self):
        data = {
            "logKow": {"estimatedValue": {"value": 2.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 6.0,
                    "model": {"logKoa": 6.0},
                }
            },
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertTrue(math.isclose(fields["koawin_kow"], 100.0))
        self.assertTrue(math.isclose(fields["koawin_koa"], 1000000.0))
        self.assertIsNone(fields["koawin_kaw"])
        self.assertIsNone(fields["koawin_log_kaw"])
        self.assertEqual(warnings, [])

    def test_preserves_inconsistent_api_coefficients_and_warns(self):
        data = {
            "logKow": {"estimatedValue": {"value": 2.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 4.0,
                    "model": {
                        "kow": 100.0,
                        "kaw": 0.1,
                        "koa": 5000.0,
                        "logKoa": 4.0,
                    },
                }
            },
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertEqual(fields["koawin_koa"], 5000.0)
        self.assertEqual(fields["koawin_log_koa"], math.log10(5000.0))
        self.assertIn("KOAWIN 原始系数关系不一致：KOA != KOW / KAW", warnings)
        self.assertIn("KOAWIN logKOA 与 KOA 不一致", warnings)

    def test_nonpositive_or_nonfinite_coefficients_stay_missing(self):
        data = {
            "logKoa": {
                "estimatedValue": {
                    "model": {
                        "kow": 0.0,
                        "kaw": float("inf"),
                        "koa": "not-a-number",
                    }
                }
            }
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertTrue(all(value is None for value in fields.values()))
        self.assertEqual(warnings, [])

    def test_rdkit_descriptors_prefer_api_smiles(self):
        fields, warnings = calculate_rdkit_descriptor_fields(
            api_smiles="CCO",
            epi_smiles="c1ccccc1",
            input_smiles="CC",
        )

        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23, places=6)
        self.assertAlmostEqual(fields["mr_rdkit_cm3_mol"], 12.7598, places=6)
        self.assertEqual(warnings, [])

    def test_rdkit_descriptors_fall_back_to_input_smiles(self):
        fields, warnings = calculate_rdkit_descriptor_fields(
            api_smiles="",
            epi_smiles=None,
            input_smiles="CCO",
        )

        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23, places=6)
        self.assertAlmostEqual(fields["mr_rdkit_cm3_mol"], 12.7598, places=6)
        self.assertEqual(warnings, [])

    def test_invalid_smiles_leaves_descriptors_empty_and_warns(self):
        fields, warnings = calculate_rdkit_descriptor_fields(
            api_smiles="not-a-smiles",
            epi_smiles=None,
            input_smiles=None,
        )

        self.assertIsNone(fields["tpsa_rdkit_a2"])
        self.assertIsNone(fields["mr_rdkit_cm3_mol"])
        self.assertEqual(warnings, ["RDKit 描述符未计算：SMILES 无法解析"])

    def test_missing_smiles_leaves_descriptors_empty_and_warns(self):
        fields, warnings = calculate_rdkit_descriptor_fields()

        self.assertIsNone(fields["tpsa_rdkit_a2"])
        self.assertIsNone(fields["mr_rdkit_cm3_mol"])
        self.assertEqual(warnings, ["RDKit 描述符未计算：缺少可用 SMILES"])

    def test_combined_enrichment_preserves_column_order(self):
        data = {
            "chemicalProperties": {"smiles": "CCO"},
            "logKow": {"estimatedValue": {"value": 3.0}},
            "logKoa": {
                "estimatedValue": {
                    "model": {
                        "kow": 1000.0,
                        "kaw": 0.01,
                        "koa": 100000.0,
                        "logKoa": 5.0,
                    }
                }
            },
        }

        fields, warnings = build_epi_property_enrichment(data)

        self.assertEqual(
            tuple(fields)[-2:],
            ("tpsa_rdkit_a2", "mr_rdkit_cm3_mol"),
        )
        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23, places=6)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
