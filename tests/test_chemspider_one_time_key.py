import unittest
from pathlib import Path

from src.chemspider_run import (
    MISSING_CHEMSPIDER_KEY_MESSAGE,
    prepare_chemspider_run_options,
)


class ChemSpiderOneTimeKeyTests(unittest.TestCase):
    def test_selected_key_is_trimmed_and_forwarded(self):
        options = prepare_chemspider_run_options(True, "  test-key  ")

        self.assertTrue(options.enabled)
        self.assertEqual(options.api_key, "test-key")
        self.assertEqual(options.warning, "")

    def test_selected_without_key_skips_only_chemspider(self):
        options = prepare_chemspider_run_options(True, "  ")

        self.assertFalse(options.enabled)
        self.assertIsNone(options.api_key)
        self.assertEqual(options.warning, MISSING_CHEMSPIDER_KEY_MESSAGE)

    def test_unselected_chemspider_discards_supplied_key(self):
        options = prepare_chemspider_run_options(False, "test-key")

        self.assertFalse(options.enabled)
        self.assertIsNone(options.api_key)
        self.assertEqual(options.warning, "")


class ChemSpiderPageSecurityTests(unittest.TestCase):
    def test_page_uses_a_clearing_password_form_without_secrets(self):
        page_source = (
            Path(__file__).resolve().parents[1]
            / "pages"
            / "4_化合物用途查询.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'with st.form("resolver_run_form", clear_on_submit=True):',
            page_source,
        )
        self.assertIn('type="password"', page_source)
        self.assertIn('autocomplete="off"', page_source)
        self.assertNotIn("CHEMSPIDER_API_KEY", page_source)
        self.assertNotIn("st.secrets", page_source)


class UseChartPageTests(unittest.TestCase):
    def test_epa_functional_use_uses_split_predicted_and_reported_plots(self):
        page_source = (
            Path(__file__).resolve().parents[1]
            / "pages"
            / "4_化合物用途查询.py"
        ).read_text(encoding="utf-8")

        self.assertIn("build_compound_universe", page_source)
        self.assertIn("extract_top_predicted_functional_use_data", page_source)
        self.assertIn("extract_top_reported_functional_use_data", page_source)
        self.assertIn("extract_source_origin_pie_data", page_source)
        self.assertIn("generate_reported_functional_use_pie_plot", page_source)
        self.assertIn("generate_reported_functional_use_presence_plot", page_source)
        self.assertIn("EPA_Top_Predicted_Functional_Use", page_source)
        self.assertIn("EPA_Reported_Functional_Use_Distribution", page_source)
        self.assertIn("EPA_Reported_Functional_Use_Evidence", page_source)
        self.assertIn("ECHA_Reported_Use_Distribution", page_source)
        self.assertIn("ECHA_Reported_Use_Evidence", page_source)
        self.assertIn("Source_Origin_Distribution", page_source)
        self.assertNotIn('"file_prefix": "ECHA_Use_Rose_Plot"', page_source)
