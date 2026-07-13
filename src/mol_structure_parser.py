"""Parse Compound Discoverer MOL text stored in table cells."""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


MOL_COLUMN_ALIASES = ("mol_text", "mol", "molfile", "structure")
RESULT_COLUMNS = (
    "parsed_smiles",
    "parsed_isomeric_smiles",
    "parsed_molecular_formula",
    "parsed_exact_mass",
    "parsed_atom_count",
    "parsed_bond_count",
    "normalized_molblock",
    "parse_status",
    "parse_warnings",
)

_COUNTS_LINE_RE = re.compile(
    r"^\s*\d{1,3}\s+\d{1,3}(?:\s+[-0-9]+){7,}\s*$",
    re.MULTILINE,
)
_SDF_SEPARATOR_RE = re.compile(r"(?:\r?\n)?\$\$\$\$\s*$")


def _empty_result(status: str, warning: str = "") -> dict[str, object]:
    result = {column: "" for column in RESULT_COLUMNS}
    result["parse_status"] = status
    result["parse_warnings"] = warning
    return result


def _has_text(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) and pd.isna(value):
        return False
    return bool(str(value).strip())


def find_mol_text_column(columns: Iterable[object]) -> object | None:
    """Return the first recognised MOL-text column name, if present."""
    for column in columns:
        if str(column).strip().casefold() in MOL_COLUMN_ALIASES:
            return column
    return None


def _normalize_mol_text(value: object) -> tuple[str, list[str]]:
    text = str(value).rstrip()
    text = _SDF_SEPARATOR_RE.sub("", text)
    warnings: list[str] = []

    if "M  END" not in text:
        if not _COUNTS_LINE_RE.search(text):
            raise ValueError("未找到可修复的 MOL 原子/键计数行")
        text = f"{text.rstrip()}\nM  END\n"
        warnings.append("已自动补齐 M END")

    return text, warnings


def parse_mol_text(mol_text: object) -> dict[str, object]:
    """Parse one MOL-text cell without allowing a malformed record to raise."""
    if not _has_text(mol_text):
        return _empty_result("未提供 MOL 文本")

    try:
        text, warnings = _normalize_mol_text(mol_text)
        mol = Chem.MolFromMolBlock(
            text,
            sanitize=True,
            removeHs=False,
            strictParsing=False,
        )
        if mol is None:
            raise ValueError("RDKit 未能读取 MOL 结构")

        smiles_mol = Chem.RemoveHs(mol)
        return {
            "parsed_smiles": Chem.MolToSmiles(smiles_mol, canonical=True),
            "parsed_isomeric_smiles": Chem.MolToSmiles(
                smiles_mol,
                canonical=True,
                isomericSmiles=True,
            ),
            "parsed_molecular_formula": rdMolDescriptors.CalcMolFormula(mol),
            "parsed_exact_mass": Descriptors.ExactMolWt(mol),
            "parsed_atom_count": mol.GetNumAtoms(),
            "parsed_bond_count": mol.GetNumBonds(),
            "normalized_molblock": Chem.MolToMolBlock(mol),
            "parse_status": "成功",
            "parse_warnings": "；".join(warnings),
        }
    except Exception as exc:
        return _empty_result("解析失败", str(exc))


def parse_mol_dataframe(
    input_df: pd.DataFrame,
    mol_column: object | None = None,
) -> pd.DataFrame:
    """Parse each MOL-text cell while preserving source rows and columns."""
    selected_column = mol_column
    if selected_column is None:
        selected_column = find_mol_text_column(input_df.columns)
        if selected_column is None:
            raise ValueError(
                "未找到 MOL 文本列；请使用 mol_text、mol、molfile、structure，或显式指定列名。"
            )
    if selected_column not in input_df.columns:
        raise ValueError(f"MOL 文本列不存在：{selected_column}")

    result = input_df.copy()
    parsed_df = pd.DataFrame(
        [parse_mol_text(value) for value in result[selected_column]],
        index=result.index,
    )
    for column in RESULT_COLUMNS:
        result[column] = parsed_df[column]
    return result
