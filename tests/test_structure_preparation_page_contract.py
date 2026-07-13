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


if __name__ == "__main__":
    unittest.main()
