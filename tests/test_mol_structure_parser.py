import unittest

import pandas as pd
from rdkit import Chem

from src.mol_structure_parser import (
    find_mol_text_column,
    parse_mol_dataframe,
    parse_mol_text,
)


ETHANOL_MOL = """ethanol
  ChemPriority

  3  2  0  0  0  0  0  0  0  0  0
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.5000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.2500    1.2990    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""


class MolTextParserTests(unittest.TestCase):
    def test_finds_trimmed_case_insensitive_structure_header(self):
        self.assertEqual(find_mol_text_column(["Name", " Structure "]), " Structure ")

    def test_parses_a_valid_mol(self):
        result = parse_mol_text(ETHANOL_MOL)

        self.assertEqual(result["parse_status"], "成功")
        self.assertEqual(result["parsed_smiles"], "CCO")
        self.assertEqual(result["parsed_molecular_formula"], "C2H6O")
        self.assertEqual((result["parsed_atom_count"], result["parsed_bond_count"]), (3, 2))

    def test_repairs_missing_m_end_after_sdf_separator(self):
        result = parse_mol_text(ETHANOL_MOL.replace("M  END\n", "") + "$$$$\n")

        self.assertEqual(result["parse_status"], "成功")
        self.assertIn("已自动补齐 M END", result["parse_warnings"])
        self.assertTrue(result["normalized_molblock"].rstrip().endswith("M  END"))

    def test_removes_explicit_hydrogen_from_query_smiles(self):
        mol_with_explicit_hydrogens = Chem.MolToMolBlock(
            Chem.AddHs(Chem.MolFromSmiles("CCO"))
        )

        result = parse_mol_text(mol_with_explicit_hydrogens)

        self.assertEqual(result["parse_status"], "成功")
        self.assertEqual(result["parsed_smiles"], "CCO")

    def test_reports_blank_and_broken_records_without_smiles(self):
        blank = parse_mol_text(" ")
        broken = parse_mol_text("not a mol block")

        self.assertEqual(blank["parse_status"], "未提供 MOL 文本")
        self.assertEqual(broken["parse_status"], "解析失败")
        self.assertEqual(broken["parsed_smiles"], "")


class MolDataFrameParserTests(unittest.TestCase):
    def test_preserves_source_columns_and_isolates_row_failures(self):
        source = pd.DataFrame(
            {
                "Structure": [
                    ETHANOL_MOL.replace("M  END\n", "") + "$$$$\n",
                    "bad",
                    "",
                ],
                "smiles": ["source-smiles", "keep-me", "also-keep"],
            },
            index=[11, 12, 13],
        )

        result = parse_mol_dataframe(source)

        self.assertEqual(result.index.tolist(), [11, 12, 13])
        self.assertEqual(result["smiles"].tolist(), source["smiles"].tolist())
        self.assertEqual(
            result["parse_status"].tolist(),
            ["成功", "解析失败", "未提供 MOL 文本"],
        )
        self.assertEqual(result.loc[11, "parsed_smiles"], "CCO")
        self.assertEqual(result.loc[12, "parsed_smiles"], "")
        self.assertIn("已自动补齐 M END", result.loc[11, "parse_warnings"])

    def test_uses_explicit_column_and_rejects_missing_column(self):
        explicit = parse_mol_dataframe(
            pd.DataFrame({"raw_ctab": [ETHANOL_MOL]}),
            mol_column="raw_ctab",
        )

        self.assertEqual(explicit.loc[0, "parse_status"], "成功")
        with self.assertRaisesRegex(ValueError, "MOL 文本列"):
            parse_mol_dataframe(pd.DataFrame({"Name": ["ethanol"]}))
        with self.assertRaisesRegex(ValueError, "raw_ctab"):
            parse_mol_dataframe(
                pd.DataFrame({"Structure": [ETHANOL_MOL]}),
                mol_column="raw_ctab",
            )
