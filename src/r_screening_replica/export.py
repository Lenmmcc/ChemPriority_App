from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_input_check_report(input_check: pd.DataFrame, output_dir: Path) -> Path:
    path = output_dir / "input_check_report.xlsx"
    input_check.to_excel(path, index=False)
    return path


def write_elemental_workbook(
    all_formulas: pd.DataFrame,
    compound_categories: pd.DataFrame,
    category_summary: pd.DataFrame,
    carbon_formulas: pd.DataFrame,
    non_carbon_compounds: pd.DataFrame,
    output_dir: Path,
) -> Path:
    path = output_dir / "elemental_ratios_with_DBE.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        all_formulas.to_excel(writer, sheet_name="all_formulas", index=False)
        compound_categories.to_excel(writer, sheet_name="compound_categories", index=False)
        category_summary.to_excel(writer, sheet_name="category_summary", index=False)
        carbon_formulas.to_excel(writer, sheet_name="carbon_formulas", index=False)
        non_carbon_compounds.to_excel(writer, sheet_name="non_carbon_compounds", index=False)
    return path


def write_dbe_workbook(dbe_table: pd.DataFrame, output_dir: Path) -> Path:
    path = output_dir / "DBE.xlsx"
    dbe_table.to_excel(path, index=False)
    return path


def write_sample_peak_area_long(sample_peak_area_long: pd.DataFrame, output_dir: Path) -> Path:
    path = output_dir / "sample_peak_area_long.xlsx"
    sample_peak_area_long.to_excel(path, index=False)
    return path
