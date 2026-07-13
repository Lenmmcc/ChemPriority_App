"""Parse Compound Discoverer MOL text stored in table cells."""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


MOL_COLUMN_ALIASES = ("mol_text", "mol", "molfile", "structure")
SMILES_COLUMN_ALIASES = (
    "smiles",
    "canonical_smiles",
    "isomeric_smiles",
    "smile",
)
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


def find_smiles_column(columns: Iterable[object]) -> object | None:
    """Return the first recognised source-SMILES column name, if present."""
    for column in columns:
        if str(column).strip().casefold() in SMILES_COLUMN_ALIASES:
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


def _empty_parse_dataframe(input_df: pd.DataFrame) -> pd.DataFrame:
    result = input_df.copy()
    empty_result = _empty_result("未提供 MOL 列")
    for column in RESULT_COLUMNS:
        result[column] = empty_result[column]
    return result


def _canonical_isomeric_smiles(value: object) -> str:
    """Return canonical isomeric SMILES, or an empty string for invalid input."""
    if not _has_text(value):
        return ""
    try:
        mol = Chem.MolFromSmiles(str(value).strip())
        if mol is None:
            return ""
        return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    except Exception:
        return ""


def _decide_smiles(
    source_smiles: object,
    parsed_smiles: object,
    parsed_isomeric_smiles: object,
    parse_status: object,
) -> dict[str, str]:
    source_text = str(source_smiles).strip() if _has_text(source_smiles) else ""
    source_canonical = _canonical_isomeric_smiles(source_text)
    mol_succeeded = (
        parse_status == "成功"
        and _has_text(parsed_smiles)
        and _has_text(parsed_isomeric_smiles)
    )

    if not source_text:
        if mol_succeeded:
            return {
                "smiles": str(parsed_smiles),
                "smiles_source": "MOL 解析",
                "smiles_decision_warning": "",
            }
        return {"smiles": "", "smiles_source": "", "smiles_decision_warning": ""}

    if not source_canonical:
        if mol_succeeded:
            return {
                "smiles": str(parsed_smiles),
                "smiles_source": "MOL 解析",
                "smiles_decision_warning": "原始 SMILES 无效，已改用 MOL 解析结果。",
            }
        return {
            "smiles": "",
            "smiles_source": "",
            "smiles_decision_warning": "原始 SMILES 无效，且没有可用的 MOL 解析结果。",
        }

    if not mol_succeeded:
        return {
            "smiles": source_text,
            "smiles_source": "原始 SMILES",
            "smiles_decision_warning": "",
        }

    if source_canonical == str(parsed_isomeric_smiles):
        return {
            "smiles": source_text,
            "smiles_source": "原始 SMILES（与 MOL 一致）",
            "smiles_decision_warning": "",
        }
    return {
        "smiles": source_text,
        "smiles_source": "原始 SMILES（与 MOL 冲突）",
        "smiles_decision_warning": "原始 SMILES 与 MOL 解析结果冲突，保留原始 SMILES。",
    }


def prepare_structure_dataframe(
    input_df: pd.DataFrame,
    mol_column: object | None = None,
    smiles_column: object | None = None,
) -> pd.DataFrame:
    """Parse optional MOL text and choose a usable SMILES for every source row."""
    selected_mol_column = mol_column
    if selected_mol_column is None:
        selected_mol_column = find_mol_text_column(input_df.columns)

    if selected_mol_column is None:
        result = _empty_parse_dataframe(input_df)
    else:
        result = parse_mol_dataframe(input_df, mol_column=selected_mol_column)

    selected_smiles_column = smiles_column
    if selected_smiles_column is None:
        selected_smiles_column = find_smiles_column(input_df.columns)
    if selected_smiles_column is not None and selected_smiles_column not in input_df.columns:
        raise ValueError(f"SMILES 列不存在：{selected_smiles_column}")

    source_values: Iterable[object]
    if selected_smiles_column is None:
        source_values = [""] * len(result)
    else:
        source_values = input_df[selected_smiles_column]

    decisions = pd.DataFrame(
        [
            _decide_smiles(
                source_smiles,
                parsed_smiles,
                parsed_isomeric_smiles,
                parse_status,
            )
            for source_smiles, parsed_smiles, parsed_isomeric_smiles, parse_status in zip(
                source_values,
                result["parsed_smiles"],
                result["parsed_isomeric_smiles"],
                result["parse_status"],
            )
        ],
        index=result.index,
        columns=("smiles", "smiles_source", "smiles_decision_warning"),
    )
    for column in decisions:
        result[column] = decisions[column]
    return result


def summarize_structure_preparation(prepared_df: pd.DataFrame) -> dict[str, int]:
    """Summarize MOL parsing and source-SMILES decision outcomes."""
    empty_series = pd.Series("", index=prepared_df.index)
    parse_status = prepared_df.get("parse_status", empty_series)
    parse_warnings = prepared_df.get("parse_warnings", empty_series).fillna("")
    smiles_source = prepared_df.get("smiles_source", empty_series)
    return {
        "mol_rows": int((parse_status != "未提供 MOL 列").sum()),
        "parsed_success": int((parse_status == "成功").sum()),
        "repaired_m_end": int(
            parse_warnings.str.contains("已自动补齐 M END", regex=False).sum()
        ),
        "smiles_conflicts": int((smiles_source == "原始 SMILES（与 MOL 冲突）").sum()),
        "parse_failures": int((parse_status == "解析失败").sum()),
    }
