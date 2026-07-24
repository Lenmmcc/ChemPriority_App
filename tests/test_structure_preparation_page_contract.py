from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = PROJECT_ROOT / "pages"


def _page_source(prefix: str) -> str:
    page = next(PAGES_DIR.glob(f"{prefix}_*.py"))
    return page.read_text(encoding="utf-8")


class StructurePreparationPageContractTests(unittest.TestCase):
    def test_target_pages_reference_shared_structure_preparation_interfaces(self):
        for prefix in ("0", "3", "4", "6"):
            source = _page_source(prefix)
            with self.subTest(page=prefix):
                self.assertIn("prepare_structure_dataframe", source)
                self.assertIn("summarize_structure_preparation", source)

    def test_use_and_auto_query_pages_export_structure_preparation_audit(self):
        for prefix in ("4", "6"):
            source = _page_source(prefix)
            with self.subTest(page=prefix):
                self.assertIn("Structure_Preparation", source)

    def test_epi_result_and_resolver_tab_downloads_append_structure_preparation_audit(self):
        epi_source = _page_source("3")
        self.assertIn("def append_structure_preparation_sheet", epi_source)
        epi_workbook = epi_source.index("workbook_buffer = build_result_workbook(")
        epi_audit = epi_source.index("workbook_buffer = append_structure_preparation_sheet(", epi_workbook)
        self.assertLess(epi_workbook, epi_audit)

        use_source = _page_source("4")
        resolver_workbook = use_source.index("resolver_workbook_buffer = append_structure_preparation_sheet(")
        resolver_download = use_source.index('key="resolver_download_in_tab"')
        self.assertLess(resolver_workbook, resolver_download)

    def test_epi_summary_renders_before_input_normalization_and_validation(self):
        source = _page_source("3")

        prepare = source.index("prepared_input_df = prepare_structure_dataframe(raw_input_df)")
        summary = source.index("render_structure_preparation_summary(prepared_input_df)")
        normalizer = source.index("input_df = normalize_input_columns(prepared_input_df)")
        validator = source.index("is_valid, message = validate_input(input_df)")

        self.assertLess(prepare, summary)
        self.assertLess(summary, normalizer)
        self.assertLess(summary, validator)

    def test_epi_page_publishes_validated_results_to_the_same_session_pool(self):
        source = _page_source("3")

        self.assertIn("from src.episuite_result_pool import", source)
        self.assertIn(
            'def publish_epi_results_to_pool(results, provenance=None, source_type="api"):',
            source,
        )
        self.assertIn("upsert_epi_pool(", source)
        self.assertIn("build_api_epi_pool_payload", source)
        self.assertIn("build_uploaded_epi_pool_payload", source)
        self.assertIn('POOL_CONTRIBUTOR_STATE_KEYS = {', source)
        self.assertIn('"api": "epi_api_pool_contributor_id"', source)
        self.assertIn('"uploaded": "epi_uploaded_pool_contributor_id"', source)
        self.assertIn('source_type="api"', source)
        self.assertIn('source_type="uploaded"', source)
        self.assertIn('successful_web_results = web_results.loc[web_results["status"].eq("success")]', source)
        self.assertIn('publish_epi_results_to_pool(*api_pool_payload, source_type="api")', source)
        self.assertIn(
            'publish_epi_results_to_pool(*uploaded_pool_payload, source_type="uploaded")',
            source,
        )
        self.assertIn("remove_epi_pool_contributor", source)
        self.assertIn("remove_stale_epi_pool_contributor", source)
        self.assertIn("remove_epi_pool_source_contributor(\"uploaded\")", source)
        self.assertIn("replace_epi_pool_source_contributor", source)
        self.assertIn("epi_uploaded_pool_source_signature", source)
        self.assertIn("advance_epi_uploader_epoch", source)
        self.assertIn('INPUT_UPLOADER_EPOCH_KEY = "epi_input_uploader_epoch"', source)
        self.assertIn('key=f"epi_input_file_{input_uploader_epoch}"', source)
        self.assertIn('key=f"epi_result_files_{result_uploader_epoch}"', source)
        self.assertIn("make_uploaded_result_source_signature", source)
        self.assertIn("clear_epi_pool(st.session_state)", source)

    def test_epi_clear_current_data_resets_both_uploader_generations(self):
        source = _page_source("3")
        clear_start = source.index("def clear_cached_input():")
        clear_end = source.index("def remove_epi_pool_source_contributor", clear_start)
        clear_source = source[clear_start:clear_end]

        self.assertIn(
            "advance_epi_uploader_epoch(st.session_state, INPUT_UPLOADER_EPOCH_KEY)",
            clear_source,
        )
        self.assertIn(
            "advance_epi_uploader_epoch(st.session_state, RESULT_UPLOADER_EPOCH_KEY)",
            clear_source,
        )

    def test_epi_page_parses_recognized_result_workbook_sheets(self):
        source = _page_source("3")

        self.assertIn("inspect_epi_workbook", source)
        self.assertIn("inspection.default_result_sheet", source)
        self.assertIn("sheet_name=selected_sheet", source)

    def test_use_summary_renders_before_resolver_and_query_normalizers(self):
        source = _page_source("4")

        prepare = source.index('st.session_state["use_query_structure_prepared_df"] = prepare_structure_dataframe(raw_input_df)')
        summary = source.index("render_structure_preparation_summary(prepared_input_df)")
        resolver_normalizer = source.index("resolver_input_df = normalize_resolver_input_columns(prepared_input_df)")
        all_invalid_guard = source.index("if not resolver_valid and not comptox_valid and not echa_valid:")

        self.assertLess(prepare, summary)
        self.assertLess(summary, resolver_normalizer)
        self.assertLess(summary, all_invalid_guard)

    def test_screening_upload_summary_precedes_front_half_normalization(self):
        source = _page_source("0")
        preview_start = source.index("def build_upload_structure_preparation_preview(")
        preview_end = source.index("def normalize_samples_for_mappings", preview_start)
        preview_source = source[preview_start:preview_end]

        self.assertLess(
            preview_source.index("prepare_structure_dataframe("),
            preview_source.index("summarize_structure_preparation(prepared)"),
        )
        self.assertLess(
            source.index("build_upload_structure_preparation_preview(samples, sample_mappings)"),
            source.index("front_state = collect_front_half("),
        )


if __name__ == "__main__":
    unittest.main()
