from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

import src.auto_query_workflow as auto_query_workflow
from src.auto_query_file_views import (
    build_file_module_views,
    safe_export_names,
    scoped_chart_key,
)
from src.auto_query_workflow import (
    AutoWorkflowChart,
    AutoWorkflowMapping,
    AutoWorkflowResult,
    auto_input_from_multi_file_result,
    update_auto_workflow_charts,
)
from src.multi_file_screening import MultiFileScreeningResult


def example_result():
    membership = pd.DataFrame(
        {
            "primary_file": ["A.xlsx", "A.xlsx", "B.xlsx", "B.xlsx"],
            "sample_id": ["A", "A", "B", "B"],
            "identity_key": ["cas:a", "cas:shared", "cas:b", "cas:shared"],
            "compound": ["Only A", "Shared", "Only B", "Shared"],
            "cas": ["1-00-0", "64-17-5", "2-00-0", "64-17-5"],
            "smiles": ["A", "CCO", "B", "CCO"],
        }
    )
    tables = OrderedDict(
        [
            (
                "Input_File_Mappings",
                pd.DataFrame(
                    {
                        "file_name": ["A.xlsx", "B.xlsx"],
                        "sample_id": ["A", "B"],
                    }
                ),
            ),
            ("EPI_Primary_Membership", membership),
            (
                "Input_Check",
                pd.DataFrame(
                    {
                        "sample_id": ["A", "B"],
                        "compound": ["Only A", "Only B"],
                    }
                ),
            ),
            (
                "CompTox_Summary",
                pd.DataFrame(
                    {
                        "input_identity_key": [
                            "cas:a",
                            "cas:shared",
                            "cas:b",
                        ],
                        "compound": ["Only A", "Shared", "Only B"],
                    }
                ),
            ),
            (
                "CompTox_Candidates",
                pd.DataFrame(
                    {
                        "input_identity_key": [
                            "cas:a",
                            "cas:shared",
                            "cas:b",
                        ],
                        "compound": ["Only A", "Shared", "Only B"],
                        "source_type": ["product_category"] * 3,
                        "raw_use": ["A use", "Shared use", "B use"],
                        "use_cn": ["A use", "Shared use", "B use"],
                    }
                ),
            ),
            (
                "CompTox_Errors",
                pd.DataFrame(
                    {
                        "input_identity_key": ["unknown:key"],
                        "compound": ["Unknown"],
                        "message": ["unassigned"],
                    }
                ),
            ),
        ]
    )
    return AutoWorkflowResult(
        mapping=AutoWorkflowMapping(),
        representative_table=pd.DataFrame(),
        tables=tables,
        step_status=pd.DataFrame(),
        warnings=pd.DataFrame(),
        charts=OrderedDict(),
    )


class AutoQueryFileViewTests(unittest.TestCase):
    def test_external_rows_follow_exact_file_membership(self):
        views = build_file_module_views(example_result())
        epa_a = views["comptox_use"]["A.xlsx"].tables["CompTox_Summary"]
        epa_b = views["comptox_use"]["B.xlsx"].tables["CompTox_Summary"]
        self.assertEqual(set(epa_a["compound"]), {"Only A", "Shared"})
        self.assertEqual(set(epa_b["compound"]), {"Only B", "Shared"})
        self.assertNotIn("Only B", set(epa_a["compound"]))
        self.assertNotIn("Only A", set(epa_b["compound"]))

    def test_upload_order_and_local_sample_partition_are_preserved(self):
        views = build_file_module_views(example_result())
        self.assertEqual(list(views["local_screening"]), ["A.xlsx", "B.xlsx"])
        self.assertEqual(
            views["local_screening"]["A.xlsx"].tables["Input_Check"][
                "compound"
            ].tolist(),
            ["Only A"],
        )
        self.assertEqual(
            views["local_screening"]["B.xlsx"].tables["Input_Check"][
                "compound"
            ].tolist(),
            ["Only B"],
        )

    def test_unknown_identity_is_quarantined_in_unassigned_view(self):
        views = build_file_module_views(example_result())["comptox_use"]
        self.assertNotIn(
            "Unknown",
            set(views["A.xlsx"].tables["CompTox_Errors"]["compound"]),
        )
        self.assertNotIn(
            "Unknown",
            set(views["B.xlsx"].tables["CompTox_Errors"]["compound"]),
        )
        self.assertEqual(
            views["unassigned"].tables["CompTox_Errors"]["compound"].tolist(),
            ["Unknown"],
        )

    def test_safe_names_are_deterministic_and_collision_resistant(self):
        mappings = pd.DataFrame(
            {
                "file_name": ["A B.xlsx", "A-B.xlsx", "中文.xlsx"],
                "sample_id": ["one", "two", "three"],
            }
        )
        names = safe_export_names(mappings)
        self.assertEqual(list(names), list(mappings["file_name"]))
        self.assertNotEqual(names["A B.xlsx"].casefold(), names["A-B.xlsx"].casefold())
        self.assertTrue(all(name for name in names.values()))
        self.assertEqual(names, safe_export_names(mappings))

    def test_scoped_chart_key_uses_only_safe_segments(self):
        self.assertEqual(
            scoped_chart_key("comptox_use", "A", "EPA_PUC"),
            "comptox_use__A__EPA_PUC",
        )

    def test_chart_updates_are_file_scoped_and_cumulative(self):
        result = example_result()
        result.charts[
            "local_screening__A__Local_DBE_Bubble_Plot"
        ] = AutoWorkflowChart("Local", b"\x89PNG\r\n\x1a\nold", b"%PDF-old")

        charts, warnings = update_auto_workflow_charts(result)

        self.assertEqual(warnings, [])
        self.assertIn(
            "local_screening__A__Local_DBE_Bubble_Plot",
            charts,
        )
        self.assertIn(
            "comptox_use__A__EPA_Product_Use_Category_Distribution",
            charts,
        )
        self.assertIn(
            "comptox_use__B__EPA_Product_Use_Category_Distribution",
            charts,
        )
        self.assertTrue(
            charts[
                "comptox_use__A__EPA_Product_Use_Category_Distribution"
            ].png.startswith(b"\x89PNG")
        )
        self.assertTrue(
            charts[
                "comptox_use__B__EPA_Product_Use_Category_Distribution"
            ].pdf.startswith(b"%PDF")
        )

    def test_one_chart_failure_keeps_other_available_charts(self):
        result = example_result()
        original = auto_query_workflow._build_chart_figure
        calls = iter(range(100))
        with patch(
            "src.auto_query_workflow._build_chart_figure",
            side_effect=lambda data, config: (
                (_ for _ in ()).throw(RuntimeError("one chart failed"))
                if next(calls) == 0
                else original(data, config)
            ),
        ) as figure_builder:
            charts, warnings = update_auto_workflow_charts(result)

        self.assertTrue(warnings)
        self.assertTrue(charts)

    def test_multi_file_adapter_keeps_local_charts_scoped_by_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            chart_paths = {}
            for source, suffix in (
                ("category_percent_donut_with_total", "type"),
                ("compound_bubble_plot", "dbe"),
                ("VanKrevelen", "vk"),
            ):
                png = root / f"{suffix}.png"
                pdf = root / f"{suffix}.pdf"
                png.write_bytes(b"\x89PNG\r\n\x1a\nchart")
                pdf.write_bytes(b"%PDF-chart")
                chart_paths[source] = {"png": png, "pdf": pdf}
            multi = MultiFileScreeningResult(
                normalized_samples=[],
                representative_table=pd.DataFrame(),
                structure_preparation=pd.DataFrame(),
                input_file_mappings=pd.DataFrame(
                    {"file_name": ["A.xlsx"], "sample_id": ["A"]}
                ),
                df_table=pd.DataFrame(),
                sample_peak_area=pd.DataFrame(),
                group_area_raw_long=pd.DataFrame(),
                group_area_mean_by_sample=pd.DataFrame(),
                screening_results=[
                    ("A", SimpleNamespace(figure_paths=chart_paths))
                ],
            )

            prepared = auto_input_from_multi_file_result(multi)

        self.assertIn(
            scoped_chart_key(
                "local_screening",
                "A",
                "Local_DBE_Bubble_Plot",
            ),
            prepared.local_charts,
        )
        self.assertEqual(
            prepared.local_charts[
                "local_screening__A__Local_DBE_Bubble_Plot"
            ].png,
            b"\x89PNG\r\n\x1a\nchart",
        )


if __name__ == "__main__":
    unittest.main()
