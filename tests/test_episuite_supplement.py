import io
import inspect
import unittest

import pandas as pd

from src.episuite_io import build_result_workbook
from src.episuite_supplement import (
    EPISupplementMapping,
    inspect_epi_workbook,
    merge_network_epi,
    resolve_epi_sources,
    parse_epi_supplement,
    suggest_primary_filename,
)


def complete_epi_rows(compounds):
    compounds = list(compounds)
    count = len(compounds)
    return pd.DataFrame(
        {
            "compound": compounds,
            "smiles": ["C" * (position + 2) for position in range(count)],
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


class EPISupplementWorkbookTests(unittest.TestCase):
    def _report_bytes(self):
        input_df = pd.DataFrame(
            {"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]}
        )
        result_df = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
                "status": ["success"],
                "log_kow": [-0.31],
                "log_kow_experimental": [-0.31],
                "henry_atm_m3_mol": [5.0e-6],
            }
        )
        return build_result_workbook(input_df, merged_df=result_df).getvalue()

    def test_report_prefers_core_summary_over_validated_input(self):
        inspection = inspect_epi_workbook(
            self._report_bytes(),
            "EPISuite_Fate_Report.xlsx",
        )
        self.assertEqual(inspection.default_result_sheet, "Core_Summary")
        self.assertIn("Validated_Input", inspection.sheet_names)

    def test_core_summary_round_trips_as_epi_results(self):
        mapping = EPISupplementMapping(
            source_file="EPISuite_Fate_Report.xlsx",
            primary_file="Lake-A.xlsx",
            sheet_name="Core_Summary",
        )
        parsed, warnings = parse_epi_supplement(self._report_bytes(), mapping)
        self.assertEqual(parsed.loc[0, "compound"], "Ethanol")
        self.assertEqual(parsed.loc[0, "cas"], "64-17-5")
        self.assertEqual(parsed.loc[0, "log_kow"], -0.31)
        self.assertFalse(warnings.empty)

    def test_log_kow_uses_estimated_value_when_experimental_is_empty(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "log_kow_experimental": [pd.NA],
                    "log_kow_estimated": [1.5],
                }
            ).to_excel(writer, sheet_name="Core_Summary", index=False)

        parsed, _ = parse_epi_supplement(
            buffer.getvalue(),
            EPISupplementMapping("download.xlsx", "Lake-A.xlsx", "Core_Summary"),
        )

        self.assertEqual(parsed.loc[0, "log_kow"], 1.5)

    def test_existing_log_kow_is_not_overwritten_by_download_value_columns(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "log_kow": [2.5],
                    "log_kow_experimental": [1.5],
                    "log_kow_estimated": [1.0],
                }
            ).to_excel(writer, sheet_name="EPI_Results", index=False)

        parsed, _ = parse_epi_supplement(
            buffer.getvalue(),
            EPISupplementMapping("direct.xlsx", "Lake-A.xlsx", "EPI_Results"),
        )

        self.assertEqual(parsed.loc[0, "log_kow"], 2.5)

    def test_core_model_fields_survive_supplement_parsing(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            complete_epi_rows(["A"]).to_excel(
                writer,
                sheet_name="Core_Summary",
                index=False,
            )

        parsed, _ = parse_epi_supplement(
            buffer.getvalue(),
            EPISupplementMapping("complete.xlsx", "Lake-A.xlsx", "Core_Summary"),
        )

        self.assertEqual(parsed.loc[0, "molecular_weight"], 100.0)
        self.assertEqual(parsed.loc[0, "level3_air_half_life_hours"], 10.0)
        self.assertEqual(parsed.loc[0, "log_baf"], 1.0)
        self.assertEqual(parsed.loc[0, "status"], "success")

    def test_epi_results_sheet_is_second_recognized_format(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame({"note": ["not results"]}).to_excel(
                writer, sheet_name="Run_Log", index=False
            )
            pd.DataFrame(
                {"compound": ["A"], "smiles": ["CC"], "log_kow": [1.5]}
            ).to_excel(writer, sheet_name="EPI_Results", index=False)
        inspection = inspect_epi_workbook(buffer.getvalue(), "EPI_Suite_Results.xlsx")
        self.assertEqual(inspection.default_result_sheet, "EPI_Results")

    def test_filename_suggestion_uses_only_normalized_filename(self):
        self.assertEqual(
            suggest_primary_filename(
                "Lake-A_EPISuite_Fate_Report.xlsx",
                ["Lake-A.xlsx", "Lake-B.xlsx"],
            ),
            "Lake-A.xlsx",
        )
        self.assertIsNone(
            suggest_primary_filename(
                "unknown.xlsx",
                ["Lake-A.xlsx", "Lake-B.xlsx"],
            )
        )

    def test_uploaded_association_rejects_compound_found_only_in_other_primary(self):
        self.assertIn(
            "primary_membership",
            inspect.signature(resolve_epi_sources).parameters,
        )
        universe = pd.DataFrame(
            {
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", "22-22-2"],
            }
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["A.xlsx", "B.xlsx"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CC", "CCC"],
                "cas": ["11-11-1", "22-22-2"],
            }
        )
        uploaded = complete_epi_rows(["Shared"])
        uploaded["smiles"] = "CCC"
        uploaded["cas"] = "22-22-2"
        uploaded["primary_file"] = "A.xlsx"

        resolution = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            primary_membership=membership,
            require_core=True,
        )

        only_b = resolution.results.set_index("cas").loc["22-22-2"]
        self.assertFalse(bool(only_b["_source_matched"]))
        self.assertTrue(
            resolution.completeness.loc[
                resolution.results["cas"].eq("22-22-2"), "needs_query"
            ]
            .iloc[0]
        )
        audit = resolution.match_audit.iloc[0]
        self.assertEqual(audit["match_status"], "association_mismatch")
        self.assertEqual(audit["primary_file"], "A.xlsx")

    def test_uploaded_association_allows_globally_reused_compound_present_in_primary(self):
        self.assertIn(
            "primary_membership",
            inspect.signature(resolve_epi_sources).parameters,
        )
        universe = pd.DataFrame(
            {
                "compound": ["Shared"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
            }
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["A.xlsx", "B.xlsx"],
                "compound": ["Shared", "Shared"],
                "smiles": ["CCO", "CCO"],
                "cas": ["64-17-5", "64-17-5"],
            }
        )
        uploaded = complete_epi_rows(["Shared"])
        uploaded["smiles"] = "CCO"
        uploaded["cas"] = "64-17-5"
        uploaded["primary_file"] = "A.xlsx"

        resolution = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            primary_membership=membership,
            require_core=True,
        )

        self.assertTrue(bool(resolution.results.loc[0, "_source_matched"]))
        self.assertFalse(bool(resolution.completeness.loc[0, "needs_query"]))
        self.assertEqual(
            resolution.match_audit.loc[0, "association_status"],
            "matched",
        )

    def test_pool_without_primary_file_still_matches_global_universe(self):
        self.assertIn(
            "primary_membership",
            inspect.signature(resolve_epi_sources).parameters,
        )
        universe = pd.DataFrame(
            {"compound": ["B"], "smiles": ["CCC"], "cas": ["22-22-2"]}
        )
        membership = pd.DataFrame(
            {
                "primary_file": ["B.xlsx"],
                "compound": ["B"],
                "smiles": ["CCC"],
                "cas": ["22-22-2"],
            }
        )
        pool = complete_epi_rows(["B"])
        pool["smiles"] = "CCC"
        pool["cas"] = "22-22-2"

        resolution = resolve_epi_sources(
            universe,
            pd.DataFrame(),
            pool,
            primary_membership=membership,
            require_core=True,
        )

        self.assertTrue(bool(resolution.results.loc[0, "_source_matched"]))
        self.assertEqual(resolution.match_audit.loc[0, "match_status"], "matched")
        self.assertEqual(
            resolution.match_audit.loc[0, "association_status"],
            "not_applicable",
        )

    def test_custom_header_mapping_is_trimmed_and_case_insensitive(self):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(
                {
                    " Compound Name ": ["A"],
                    " Custom VP ": [0.125],
                }
            ).to_excel(writer, sheet_name="Manual Results", index=False)

        parsed, _ = parse_epi_supplement(
            buffer.getvalue(),
            EPISupplementMapping(
                source_file="manual.xlsx",
                primary_file="A.xlsx",
                sheet_name="Manual Results",
                compound_col="compound name",
                endpoint_columns={
                    "vapor_pressure_mm_hg": "custom vp",
                },
            ),
        )

        self.assertEqual(parsed.loc[0, "compound"], "A")
        self.assertEqual(parsed.loc[0, "vapor_pressure_mm_hg"], 0.125)

    def test_cas_match_wins_and_uploaded_values_are_not_overwritten(self):
        universe = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
            }
        )
        uploaded = pd.DataFrame(
            {
                "compound": ["Wrong display name"],
                "smiles": ["different"],
                "cas": ["64-17-5"],
                "log_kow": [-0.31],
                "henry_atm_m3_mol": [pd.NA],
                "source_file": ["Lake-A_EPI.xlsx"],
                "source_sheet": ["Core_Summary"],
                "source_row": [2],
                "source_priority": [0],
            }
        )
        pool = pd.DataFrame(
            {
                "compound": ["Ethanol"],
                "smiles": ["CCO"],
                "cas": ["64-17-5"],
                "log_kow": [99.0],
                "henry_atm_m3_mol": [5.0e-6],
            }
        )

        resolution = resolve_epi_sources(universe, uploaded, pool)

        self.assertEqual(resolution.results.loc[0, "log_kow"], -0.31)
        self.assertEqual(resolution.results.loc[0, "henry_atm_m3_mol"], 5.0e-6)
        self.assertEqual(resolution.match_audit.loc[0, "match_method"], "cas")
        self.assertTrue(
            resolution.provenance["source_type"]
            .isin(["uploaded", "session_pool"])
            .all()
        )

    def test_complete_upload_skips_query_and_core_missing_is_targeted(self):
        universe = pd.DataFrame(
            {"compound": ["A", "B"], "smiles": ["CC", "CCC"], "cas": ["", ""]}
        )
        uploaded = complete_epi_rows(["A", "B"])
        epi_only = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            require_core=False,
        )
        self.assertTrue(epi_only.query_input.empty)

        uploaded.loc[uploaded["compound"].eq("B"), "log_baf"] = pd.NA
        downstream = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            require_core=True,
        )
        self.assertEqual(downstream.query_input["compound"].tolist(), ["B"])

    def test_missing_status_requires_query(self):
        resolution = resolve_epi_sources(
            pd.DataFrame(
                {
                    "compound": ["A", "B"],
                    "smiles": ["CC", "CCC"],
                    "cas": ["", ""],
                }
            ),
            pd.DataFrame(
                {
                    "compound": ["A", "B"],
                    "smiles": ["CC", "CCC"],
                    "status": ["success", pd.NA],
                    "log_kow": [1.0, 2.0],
                }
            ),
            pd.DataFrame(),
        )

        self.assertFalse(bool(resolution.completeness.loc[1, "complete"]))
        self.assertTrue(bool(resolution.completeness.loc[1, "needs_query"]))
        self.assertEqual(resolution.query_input["compound"].tolist(), ["B"])

    def test_success_without_optional_endpoints_skips_query_and_audits_missing(self):
        resolution = resolve_epi_sources(
            pd.DataFrame(
                {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
            ),
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "smiles": ["CC"],
                    "status": [" SUCCESS "],
                }
            ),
            pd.DataFrame(),
        )

        self.assertTrue(resolution.query_input.empty)
        self.assertTrue(bool(resolution.completeness.loc[0, "complete"]))
        missing = resolution.completeness.loc[0, "missing_endpoint_fields"]
        self.assertIn("log_kow", missing)
        self.assertIn("henry_atm_m3_mol", missing)

    def test_non_success_statuses_require_query_even_with_recognized_endpoint(self):
        statuses = ["", "error", "timeout", "failed"]
        compounds = ["missing", "error", "timeout", "failed"]
        smiles = ["C" * (position + 2) for position in range(len(compounds))]
        resolution = resolve_epi_sources(
            pd.DataFrame(
                {"compound": compounds, "smiles": smiles, "cas": [""] * 4}
            ),
            pd.DataFrame(
                {
                    "compound": compounds,
                    "smiles": smiles,
                    "status": statuses,
                    "log_kow": [1.0] * 4,
                }
            ),
            pd.DataFrame(),
        )

        self.assertEqual(resolution.query_input["compound"].tolist(), compounds)
        self.assertFalse(resolution.completeness["complete"].any())

    def test_conflicting_identifier_targets_are_quarantined(self):
        universe = pd.DataFrame(
            {
                "compound": ["A", "B"],
                "smiles": ["CC", "CCC"],
                "cas": ["1-11-1", "2-22-2"],
            }
        )
        uploaded = pd.DataFrame(
            {
                "compound": ["A"],
                "smiles": ["CCC"],
                "cas": ["1-11-1"],
                "log_kow": [3.0],
            }
        )

        resolution = resolve_epi_sources(universe, uploaded, pd.DataFrame())

        self.assertEqual(len(resolution.match_audit), 1)
        self.assertEqual(resolution.match_audit.loc[0, "match_method"], "cas")
        self.assertEqual(resolution.match_audit.loc[0, "match_status"], "conflict")
        self.assertTrue(resolution.provenance.empty)
        self.assertEqual(resolution.query_input["compound"].tolist(), ["A", "B"])

    def test_repeated_source_smiles_is_not_skipped_to_force_name_matches(self):
        universe = pd.DataFrame(
            {
                "compound": ["A", "B"],
                "smiles": ["CC", "CCC"],
                "cas": ["", ""],
            }
        )
        uploaded = pd.DataFrame(
            {
                "compound": ["A", "B"],
                "smiles": ["CC", "CC"],
                "status": ["success", "success"],
                "log_kow": [1.0, 2.0],
            }
        )

        resolution = resolve_epi_sources(universe, uploaded, pd.DataFrame())

        self.assertEqual(
            resolution.match_audit["match_status"].tolist(),
            ["matched", "conflict"],
        )
        self.assertEqual(resolution.query_input["compound"].tolist(), ["B"])

    def test_ambiguous_identifier_is_quarantined_without_name_fallback(self):
        universe = pd.DataFrame(
            {
                "compound": ["A", "B"],
                "smiles": ["CC", "CC"],
                "cas": ["", ""],
            }
        )
        uploaded = pd.DataFrame(
            {
                "compound": ["A"],
                "smiles": ["CC"],
                "status": ["success"],
                "log_kow": [1.0],
            }
        )

        resolution = resolve_epi_sources(universe, uploaded, pd.DataFrame())

        self.assertEqual(resolution.match_audit.loc[0, "match_method"], "smiles")
        self.assertEqual(resolution.match_audit.loc[0, "match_status"], "ambiguous")
        self.assertTrue(resolution.provenance.empty)
        self.assertEqual(resolution.query_input["compound"].tolist(), ["A", "B"])

    def test_uploaded_priority_records_conflict_and_pool_only_fills_nulls(self):
        universe = pd.DataFrame(
            {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
        )
        uploaded = pd.DataFrame(
            {
                "compound": ["A", "A"],
                "smiles": ["CC", "CC"],
                "log_kow": [1.0, 2.0],
                "source_file": ["first.xlsx", "second.xlsx"],
                "source_priority": [0, 1],
            }
        )
        pool = pd.DataFrame(
            {
                "compound": ["A"],
                "smiles": ["CC"],
                "log_kow": [3.0],
                "henry_atm_m3_mol": [4.0e-6],
            }
        )

        resolution = resolve_epi_sources(universe, uploaded, pool)

        self.assertEqual(resolution.results.loc[0, "log_kow"], 1.0)
        self.assertEqual(resolution.results.loc[0, "henry_atm_m3_mol"], 4.0e-6)
        log_kow_conflicts = resolution.conflict_audit.loc[
            resolution.conflict_audit["field"].eq("log_kow")
        ]
        self.assertEqual(len(log_kow_conflicts), 2)
        self.assertTrue(log_kow_conflicts["adopted_source_file"].eq("first.xlsx").all())

    def test_uploaded_blank_values_allow_session_pool_to_fill(self):
        universe = pd.DataFrame(
            {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
        )
        uploaded = pd.DataFrame(
            {
                "compound": ["A"],
                "smiles": ["CC"],
                "status": ["   "],
                "log_kow": [""],
                "henry_atm_m3_mol": ["  "],
            }
        )
        pool = pd.DataFrame(
            {
                "compound": ["A"],
                "smiles": ["CC"],
                "status": ["success"],
                "log_kow": [2.0],
                "henry_atm_m3_mol": [5.0e-6],
            }
        )

        resolution = resolve_epi_sources(universe, uploaded, pool)

        self.assertEqual(resolution.results.loc[0, "status"], "success")
        self.assertEqual(resolution.results.loc[0, "log_kow"], 2.0)
        self.assertEqual(
            resolution.results.loc[0, "henry_atm_m3_mol"],
            5.0e-6,
        )
        self.assertTrue(resolution.query_input.empty)
        adopted = resolution.provenance.loc[
            resolution.provenance["field"].isin(
                ["status", "log_kow", "henry_atm_m3_mol"]
            )
        ]
        self.assertTrue(adopted["source_type"].eq("session_pool").all())
        self.assertFalse(
            resolution.conflict_audit["field"]
            .isin(["status", "log_kow", "henry_atm_m3_mol"])
            .any()
        )

    def test_identifier_molecular_weight_completes_downstream_core(self):
        universe = pd.DataFrame(
            {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
        )
        uploaded = complete_epi_rows(["A"]).drop(columns=["molecular_weight"])
        completed = pd.DataFrame(
            {
                "compound": ["A"],
                "smiles": ["CC"],
                "cas": [""],
                "pubchem_molecular_weight": [88.0],
            }
        )

        resolution = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            completed_identifiers=completed,
            require_core=True,
        )

        self.assertEqual(resolution.results.loc[0, "molecular_weight"], 88.0)
        self.assertTrue(resolution.query_input.empty)
        molecular_weight_source = resolution.provenance.loc[
            resolution.provenance["field"].eq("molecular_weight")
        ]
        self.assertEqual(len(molecular_weight_source), 1)
        self.assertEqual(
            molecular_weight_source.iloc[0]["source_type"],
            "identifier_completion",
        )
        self.assertEqual(molecular_weight_source.iloc[0]["value"], 88.0)

    def test_network_merge_fills_only_missing_values_and_rebuilds_query_targets(self):
        universe = pd.DataFrame(
            {"compound": ["A", "B"], "smiles": ["CC", "CCC"], "cas": ["", ""]}
        )
        uploaded = complete_epi_rows(["A", "B"])
        uploaded.loc[uploaded["compound"].eq("B"), "log_baf"] = pd.NA
        resolution = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            require_core=True,
        )
        network = pd.DataFrame(
            {
                "compound": ["B"],
                "smiles": ["CCC"],
                "status": ["success"],
                "log_kow": [99.0],
                "log_baf": [1.5],
                "source_priority": [1],
            }
        )
        events = (
            {
                "event": "started",
                "index": 0,
                "label": "B",
                "attempt": 1,
                "max_attempts": 3,
            },
            {
                "event": "completed",
                "index": 0,
                "label": "B",
                "attempt": 1,
                "max_attempts": 3,
                "elapsed_seconds": 0.25,
                "error": None,
            },
        )

        merged = merge_network_epi(
            resolution,
            network,
            pd.DataFrame({"compound": ["B"], "raw_json": ["{}"]}),
            pd.DataFrame(),
            attempt_events=events,
        )

        b_row = merged.results.loc[merged.results["compound"].eq("B")].iloc[0]
        self.assertEqual(b_row["log_kow"], 2.0)
        self.assertEqual(b_row["log_baf"], 1.5)
        self.assertTrue(merged.query_input.empty)
        self.assertEqual(len(merged.raw_results), 1)
        self.assertEqual(len(merged.query_attempts), 1)
        network_provenance = merged.provenance.loc[
            merged.provenance["source_type"].eq("network")
        ]
        self.assertTrue(network_provenance["source_priority"].eq(20_000).all())

    def test_successful_network_result_clears_failed_status_without_overwriting_values(self):
        universe = pd.DataFrame(
            {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
        )
        uploaded = complete_epi_rows(["A"])
        uploaded["status"] = "failed"
        resolution = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            require_core=True,
        )
        self.assertEqual(resolution.query_input["compound"].tolist(), ["A"])

        merged = merge_network_epi(
            resolution,
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "smiles": ["CC"],
                    "status": ["success"],
                    "log_kow": [99.0],
                }
            ),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        self.assertEqual(merged.results.loc[0, "status"], "success")
        self.assertEqual(merged.results.loc[0, "log_kow"], 2.0)
        self.assertTrue(merged.query_input.empty)
        self.assertCountEqual(
            merged.conflict_audit["field"].tolist(),
            ["status", "log_kow"],
        )

    def test_successful_network_result_recovers_every_non_success_status(self):
        for current_status in ("", "error", "timeout", "failed"):
            with self.subTest(current_status=current_status):
                universe = pd.DataFrame(
                    {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
                )
                uploaded = complete_epi_rows(["A"])
                uploaded["status"] = current_status
                resolution = resolve_epi_sources(
                    universe,
                    uploaded,
                    pd.DataFrame(),
                )
                self.assertEqual(
                    resolution.query_input["compound"].tolist(),
                    ["A"],
                )

                merged = merge_network_epi(
                    resolution,
                    pd.DataFrame(
                        {
                            "compound": ["A"],
                            "smiles": ["CC"],
                            "status": ["success"],
                            "log_kow": [99.0],
                        }
                    ),
                    pd.DataFrame(),
                    pd.DataFrame(),
                )

                self.assertEqual(merged.results.loc[0, "status"], "success")
                self.assertEqual(merged.results.loc[0, "log_kow"], 2.0)
                self.assertTrue(merged.query_input.empty)

    def test_unsuccessful_network_result_does_not_recover_or_fill_values(self):
        universe = pd.DataFrame(
            {"compound": ["A"], "smiles": ["CC"], "cas": [""]}
        )
        uploaded = complete_epi_rows(["A"])
        uploaded["status"] = "failed"
        uploaded["log_baf"] = pd.NA
        resolution = resolve_epi_sources(
            universe,
            uploaded,
            pd.DataFrame(),
            require_core=True,
        )

        merged = merge_network_epi(
            resolution,
            pd.DataFrame(
                {
                    "compound": ["A"],
                    "smiles": ["CC"],
                    "status": ["error"],
                    "log_baf": [9.0],
                }
            ),
            pd.DataFrame(),
            pd.DataFrame(
                {"compound": ["A"], "error": ["temporary network error"]}
            ),
        )

        self.assertEqual(merged.results.loc[0, "status"], "failed")
        self.assertTrue(pd.isna(merged.results.loc[0, "log_baf"]))
        self.assertEqual(merged.query_input["compound"].tolist(), ["A"])
        self.assertEqual(len(merged.errors), 1)
