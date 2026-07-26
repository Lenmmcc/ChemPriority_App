from pathlib import Path
import unittest


class StreamlitDataframeWidthContractTests(unittest.TestCase):
    def test_streamlit_requirement_supports_stretch_width(self):
        requirements = {
            line.strip()
            for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("streamlit>=1.49,<2", requirements)

    def test_pages_do_not_use_deprecated_container_width_argument(self):
        offenders = []
        for path in sorted(Path("pages").glob("*.py")):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if "use_container_width" in line:
                    offenders.append(f"{path}:{line_number}")

        self.assertEqual(
            offenders,
            [],
            "Deprecated Streamlit dataframe width arguments remain: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
