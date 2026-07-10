from pathlib import Path
import unittest


class StreamlitPageOrderTests(unittest.TestCase):
    def test_comprehensive_screening_page_is_first_after_app(self):
        page_names = sorted(path.name for path in Path("pages").glob("*.py"))

        self.assertEqual(page_names[0], "0_综合筛查流程.py")
        self.assertNotIn("5_综合筛查流程.py", page_names)

    def test_homepage_describes_comprehensive_screening_as_first_page_after_app(self):
        app_text = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("### 1. 综合筛查流程", app_text)
        self.assertIn("从左侧 App 后第一个页面进入“综合筛查流程”。", app_text)
        self.assertNotIn("### 5. 综合筛查流程", app_text)
        self.assertNotIn("从左侧第五个页面进入“综合筛查流程”。", app_text)


if __name__ == "__main__":
    unittest.main()
