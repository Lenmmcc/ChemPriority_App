import unittest
import urllib.error
from unittest.mock import patch

import pandas as pd

from src import comptox_use


def _candidate(source_type):
    return {
        "source_type": source_type,
        "source": f"dashboard:{source_type}",
        "raw_use": "cleaning agent",
        "use_cn": "清洁用品",
        "general_category": "cleaning",
        "product_family": "",
        "product_type": "",
        "reported_use": "",
        "harmonized_use": "",
        "evidence_count": 1,
        "description": "",
        "specificity": 1,
    }


class CompToxDashboardModeTests(unittest.TestCase):
    def test_dashboard_mode_skips_unconfigured_api(self):
        with (
            patch.object(
                comptox_use,
                "_api_get_json",
                side_effect=AssertionError("the disabled API must not be called"),
            ),
            patch.object(comptox_use, "_dashboard_get_html", return_value="page"),
            patch.object(
                comptox_use,
                "_extract_dashboard_product_categories",
                return_value=[_candidate("product_category")],
            ),
            patch.object(
                comptox_use,
                "_extract_dashboard_functional_uses",
                return_value=[_candidate("functional_use")],
            ),
        ):
            candidates, warnings = comptox_use.fetch_use_candidates(
                "DTXSID0020153", api_base="", dashboard_fallback=True
            )

        self.assertEqual(warnings, [])
        self.assertEqual(
            {candidate["source_type"] for candidate in candidates},
            {"product_category", "functional_use"},
        )

    def test_dashboard_mode_resolves_dtxsid_without_api(self):
        record = {
            "dtxsid": "DTXSID0020153",
            "preferredName": "Benzyl chloride",
            "casrn": "100-44-7",
        }
        with (
            patch.object(
                comptox_use,
                "_api_get_json",
                side_effect=AssertionError("the disabled API must not be called"),
            ),
            patch.object(
                comptox_use,
                "_dashboard_search_chemical_candidates",
                return_value=[record],
            ),
        ):
            result = comptox_use.resolve_dtxsid(
                pd.Series({"cas": "100-44-7", "compound": "Benzyl chloride"}),
                api_base="",
            )

        self.assertEqual(result["dtxsid"], "DTXSID0020153")
        self.assertEqual(result["status"], "通过 Dashboard cas 匹配")

    def test_batch_surfaces_scope_note_instead_of_api_failures(self):
        with (
            patch.object(
                comptox_use,
                "fetch_use_candidates",
                return_value=([_candidate("product_category")], []),
            ),
        ):
            summary_df, _, errors_df = comptox_use.run_comptox_use_batch(
                pd.DataFrame(
                    [{"compound": "Benzyl chloride", "dtxsid": "DTXSID0020153"}]
                ),
                api_base="",
                delay_seconds=0,
            )

        self.assertTrue(errors_df.empty)
        self.assertEqual(
            summary_df.loc[0, "query_notes"], comptox_use.DASHBOARD_ONLY_QUERY_NOTE
        )

    def test_dashboard_request_retries_transient_network_failure(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return b"dashboard page"

        calls = []

        def urlopen(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise urllib.error.URLError("connection reset")
            return Response()

        with (
            patch.object(comptox_use.urllib.request, "urlopen", side_effect=urlopen),
            patch.object(comptox_use.time, "sleep") as sleep,
        ):
            page = comptox_use._dashboard_get_html("chemical/example", timeout=1)

        self.assertEqual(page, "dashboard page")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(comptox_use.DASHBOARD_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    unittest.main()
