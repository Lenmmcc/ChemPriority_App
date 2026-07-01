import unittest
from unittest.mock import patch

import pandas as pd

from src.echa_ghs import run_echa_ghs_batch


def _resolution(echa_id="100.001.133"):
    return {
        "echa_id": echa_id,
        "matched_name": "Bisphenol A",
        "matched_cas": "80-05-7",
        "matched_ec": "201-245-8",
        "status": "matched",
        "message": "",
    }


class EchaGhsBatchTests(unittest.TestCase):
    @patch("src.echa_ghs.resolve_substance", return_value=_resolution())
    @patch("src.echa_ghs._get_json")
    def test_harmonised_classification_extracts_danger_h_statements_and_pictograms(
        self, get_json, resolve_substance
    ):
        get_json.side_effect = [
            {
                "classificationId": "139506",
                "rmlId": "100.001.133",
                "signalWord": {"signalWordCode": "Dgr", "signalWordText": "Danger"},
                "type": "harmonised",
                "totalHarmonisedClassifications": 1,
                "totalIndustryClassifications": 22,
            },
            {
                "items": [
                    {
                        "classificationId": "139506",
                        "hazardClassAndCategoryCode": "Repr. 1B",
                        "hazardStatements": [
                            {
                                "hazardStatementCode": "H360F",
                                "hazardStatementText": "May damage fertility.",
                            }
                        ],
                    },
                    {
                        "classificationId": "139506",
                        "hazardClassAndCategoryCode": "STOT SE 3",
                        "hazardStatements": [
                            {
                                "hazardStatementCode": "H335",
                                "hazardStatementText": "May cause respiratory irritation.",
                            }
                        ],
                    },
                ]
            },
            {
                "items": [
                    {"code": "GHS08", "text": "Health hazard"},
                    {"code": "GHS07", "text": "Exclamation mark"},
                ]
            },
        ]

        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["Bisphenol A"], "cas": ["80-05-7"]}),
            delay_seconds=0,
        )

        self.assertTrue(errors.empty)
        self.assertEqual(summary.loc[0, "分类来源"], "harmonised")
        self.assertEqual(summary.loc[0, "signal_word"], "Danger")
        self.assertEqual(summary.loc[0, "GHS危害分层"], "一类GHS危害")
        self.assertEqual(summary.loc[0, "最高关注类别"], "Repr. 1B")
        self.assertIn("H360F", summary.loc[0, "hazard_statement_code"])
        self.assertIn("GHS08", summary.loc[0, "pictogram_code"])
        self.assertEqual(len(classifications), 2)
        self.assertEqual(classifications.loc[0, "hazard_class_and_category_code"], "Repr. 1B")
        self.assertEqual(classifications.loc[0, "pictogram_code"], "GHS08; GHS07")
        self.assertEqual(get_json.call_args_list[1].args[0], "api-cnl-inventory/prominent/overview/classifications/harmonised/139506")

    @patch("src.echa_ghs.resolve_substance", return_value=_resolution("100.000.001"))
    @patch("src.echa_ghs._get_json")
    def test_lower_category_classification_is_less_than_category_one(self, get_json, resolve_substance):
        get_json.side_effect = [
            {
                "classificationId": "200001",
                "rmlId": "100.000.001",
                "signalWord": {"signalWordText": "Warning"},
                "type": "industry",
                "totalHarmonisedClassifications": 0,
                "totalIndustryClassifications": 4,
            },
            {
                "items": [
                    {
                        "classificationId": "200001",
                        "hazardClassAndCategoryCode": "Skin Irrit. 2",
                        "hazardStatements": [
                            {
                                "hazardStatementCode": "H315",
                                "hazardStatementText": "Causes skin irritation.",
                            }
                        ],
                    }
                ]
            },
            {"items": [{"code": "GHS07", "text": "Exclamation mark"}]},
        ]

        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["Example"], "echa_id": ["100.000.001"]}),
            delay_seconds=0,
        )

        self.assertTrue(errors.empty)
        self.assertEqual(summary.loc[0, "分类来源"], "industry")
        self.assertEqual(summary.loc[0, "GHS危害分层"], "小于一类GHS危害")
        self.assertEqual(summary.loc[0, "最高关注类别"], "Skin Irrit. 2")
        self.assertEqual(classifications.loc[0, "hazard_statement_code"], "H315")

    @patch("src.echa_ghs.time.sleep")
    @patch("src.echa_ghs.resolve_substance", return_value=_resolution("100.052.821"))
    @patch("src.echa_ghs._get_json")
    def test_transient_cnl_failure_is_retried_and_keeps_success_result(
        self, get_json, resolve_substance, sleep
    ):
        get_json.side_effect = [
            RuntimeError("连接失败: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred"),
            {
                "classificationId": "503928",
                "rmlId": "100.052.821",
                "signalWord": {"signalWordCode": "Dgr", "signalWordText": "Danger"},
                "type": "industry",
                "totalHarmonisedClassifications": 0,
                "totalIndustryClassifications": 3,
            },
            {
                "items": [
                    {
                        "classificationId": "503928",
                        "hazardClassAndCategoryCode": "Eye Dam. 1",
                        "hazardStatements": [
                            {
                                "hazardStatementCode": "H318",
                                "hazardStatementText": "Causes serious eye damage.",
                            }
                        ],
                    }
                ]
            },
            {"items": [{"code": "GHS05", "text": "Corrosion"}]},
        ]

        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["3OCB"], "echa_id": ["100.052.821"]}),
            delay_seconds=0,
        )

        self.assertTrue(errors.empty)
        self.assertEqual(summary.loc[0, "query_status"], "查询完成")
        self.assertEqual(summary.loc[0, "GHS危害分层"], "一类GHS危害")
        self.assertEqual(summary.loc[0, "signal_word"], "Danger")
        self.assertEqual(classifications.loc[0, "pictogram_code"], "GHS05")
        self.assertEqual(get_json.call_count, 4)
        sleep.assert_called_once_with(1.0)

    @patch("src.echa_ghs.time.sleep")
    @patch("src.echa_ghs.resolve_substance", return_value=_resolution("100.052.821"))
    @patch("src.echa_ghs._get_json", side_effect=RuntimeError("HTTP 503: Service unavailable"))
    def test_persistent_retryable_failure_stays_query_failure(
        self, get_json, resolve_substance, sleep
    ):
        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["3OCB"], "echa_id": ["100.052.821"]}),
            delay_seconds=0,
        )

        self.assertTrue(classifications.empty)
        self.assertEqual(summary.loc[0, "query_status"], "查询失败")
        self.assertEqual(summary.loc[0, "GHS危害分层"], "无GHS数据或未分类")
        self.assertEqual(errors.loc[0, "stage"], "cnl_inventory")
        self.assertIn("HTTP 503", errors.loc[0, "message"])
        self.assertEqual(get_json.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("src.echa_ghs.resolve_substance", return_value=_resolution("100.001.409"))
    @patch("src.echa_ghs._get_json")
    def test_not_classified_or_empty_details_is_no_ghs_data(self, get_json, resolve_substance):
        get_json.side_effect = [
            {
                "classificationId": "139584",
                "rmlId": "100.001.409",
                "signalWord": {},
                "type": "industry",
                "totalHarmonisedClassifications": 0,
                "totalIndustryClassifications": 12,
                "notClassifiedOrNoGhs": "Not classified",
            },
            {"items": []},
            {"items": []},
        ]

        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["Diethyl phthalate"], "cas": ["84-66-2"]}),
            delay_seconds=0,
        )

        self.assertTrue(errors.empty)
        self.assertTrue(classifications.empty)
        self.assertEqual(summary.loc[0, "GHS危害分层"], "无GHS数据或未分类")
        self.assertEqual(summary.loc[0, "not_classified_or_no_ghs"], "Not classified")

    @patch(
        "src.echa_ghs.resolve_substance",
        return_value={
            "echa_id": pd.NA,
            "matched_name": pd.NA,
            "matched_cas": pd.NA,
            "matched_ec": pd.NA,
            "status": "未解析",
            "message": "not found",
        },
    )
    def test_unmatched_substance_keeps_summary_row_and_warning(self, resolve_substance):
        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["Unknown"]}),
            delay_seconds=0,
        )

        self.assertTrue(classifications.empty)
        self.assertEqual(summary.loc[0, "compound"], "Unknown")
        self.assertEqual(summary.loc[0, "GHS危害分层"], "无GHS数据或未分类")
        self.assertEqual(errors.loc[0, "stage"], "substance_resolution")
        self.assertIn("not found", errors.loc[0, "message"])

    @patch("src.echa_ghs.time.sleep")
    @patch("src.echa_ghs.resolve_substance", return_value=_resolution())
    @patch("src.echa_ghs._get_json", side_effect=RuntimeError("HTTP 500"))
    def test_interface_failure_keeps_summary_row_and_warning(self, get_json, resolve_substance, sleep):
        summary, classifications, errors = run_echa_ghs_batch(
            pd.DataFrame({"compound": ["Bisphenol A"]}),
            delay_seconds=0,
        )

        self.assertTrue(classifications.empty)
        self.assertEqual(summary.loc[0, "query_status"], "查询失败")
        self.assertEqual(summary.loc[0, "GHS危害分层"], "无GHS数据或未分类")
        self.assertEqual(errors.loc[0, "stage"], "cnl_inventory")
        self.assertIn("HTTP 500", errors.loc[0, "message"])
        self.assertEqual(get_json.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
