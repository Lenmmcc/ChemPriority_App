import io
import unittest

import pandas as pd

from src.episuite_io import build_result_workbook
from src.episuite_supplement import (
    EPISupplementMapping,
    inspect_epi_workbook,
    parse_epi_supplement,
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
        self.assertTrue(warnings.empty)

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
