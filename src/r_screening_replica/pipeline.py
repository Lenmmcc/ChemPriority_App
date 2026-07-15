from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .classification import classify_compounds, summarize_categories
from .export import (
    write_dbe_workbook,
    write_elemental_workbook,
    write_input_check_report,
    write_sample_peak_area_long,
)
from .formula import calculate_ratios_and_dbe
from .plots import generate_all_figures
from .schema import ScreeningConfig, ScreeningResult


def run_screening_pipeline(input_file: str | Path | Any, config: ScreeningConfig | None = None) -> ScreeningResult:
    config = config or ScreeningConfig()
    output_dir = config.output_path
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_data = pd.read_excel(input_file, sheet_name=config.sheet_name)
    raw_data.columns = [str(column).strip() for column in raw_data.columns]

    input_check = make_input_check_report(raw_data, config)
    early_missing = _missing_early_columns(raw_data, config)
    write_input_check_report(input_check, output_dir)
    if early_missing:
        raise ValueError(f"Cannot run early processing. Missing columns: {', '.join(early_missing)}")

    all_formulas = calculate_ratios_and_dbe(raw_data[config.formula_col])
    compound_categories = classify_compounds(all_formulas)
    category_summary = summarize_categories(compound_categories)
    carbon_formulas = all_formulas[all_formulas["valid_C"].astype(bool)].reset_index(drop=True)
    non_carbon_compounds = all_formulas[~all_formulas["valid_C"].astype(bool)].reset_index(drop=True)

    dbe_table = build_dbe_table(raw_data, all_formulas, config)
    sample_peak_area_long = build_sample_peak_area_long(
        raw_data,
        compound_col=config.compound_col,
        formula_col=config.formula_col,
        sample_cols=config.sample_cols,
    )

    write_elemental_workbook(
        all_formulas,
        compound_categories,
        category_summary,
        carbon_formulas,
        non_carbon_compounds,
        output_dir,
    )
    write_dbe_workbook(dbe_table, output_dir)
    write_sample_peak_area_long(sample_peak_area_long, output_dir)
    figure_paths, warnings_list = generate_all_figures(
        category_summary,
        dbe_table,
        compound_categories,
        sample_peak_area_long,
        output_dir,
        axis_ranges=config.axis_ranges,
    )

    return ScreeningResult(
        config=config,
        raw_data=raw_data,
        input_check=input_check,
        all_formulas=all_formulas,
        compound_categories=compound_categories,
        category_summary=category_summary,
        carbon_formulas=carbon_formulas,
        non_carbon_compounds=non_carbon_compounds,
        dbe_table=dbe_table,
        sample_peak_area_long=sample_peak_area_long,
        figure_paths=figure_paths,
        warnings=warnings_list,
        metadata={
            "output_dir": output_dir,
            "input_file": getattr(input_file, "name", str(input_file)),
        },
    )


def make_input_check_report(data: pd.DataFrame, config: ScreeningConfig) -> pd.DataFrame:
    early_required = [config.compound_col, config.formula_col, config.group_area_col, *config.sample_cols]
    missing = _missing_early_columns(data, config)
    parse_failures = 0
    if config.formula_col in data.columns:
        ratios = calculate_ratios_and_dbe(data[config.formula_col])
        parse_failures = int((~ratios["valid_C"].astype(bool)).sum())
    return pd.DataFrame(
        [
            {
                "check": "early_processing_columns",
                "status": "PASS" if not missing else "FAIL",
                "detail": ", ".join(missing),
            },
            {
                "check": "configured_sample_columns",
                "status": ", ".join(config.sample_cols),
                "detail": "Change sample_cols for other batches.",
            },
            {
                "check": "duplicate_compound_names",
                "status": str(int(data[config.compound_col].duplicated().sum())) if config.compound_col in data.columns else "NA",
                "detail": "Duplicate names are kept as separate rows.",
            },
            {
                "check": "formula_parse_failures",
                "status": str(parse_failures),
                "detail": "Rows without valid carbon formulas are excluded from category plots.",
            },
        ]
    )


def build_dbe_table(raw_data: pd.DataFrame, all_formulas: pd.DataFrame, config: ScreeningConfig) -> pd.DataFrame:
    return pd.DataFrame({
        "name": all_formulas["Formula"],
        "carbon_count": all_formulas["C_count"],
        "DBE": all_formulas["DBE"],
        "peak_area": pd.to_numeric(raw_data[config.group_area_col], errors="coerce"),
    })


def build_sample_peak_area_long(
    raw_data: pd.DataFrame,
    compound_col: str,
    sample_cols: list[str],
    formula_col: str | None = None,
) -> pd.DataFrame:
    formula_col = formula_col if formula_col in raw_data.columns else None
    id_cols = [compound_col] + ([formula_col] if formula_col else [])
    long_df = (
        raw_data[id_cols + sample_cols]
        .melt(id_vars=id_cols, value_vars=sample_cols, var_name="sample_id", value_name="Peak_area")
        .rename(columns={compound_col: "compound", formula_col or "": "formula"})
    )
    if "formula" not in long_df.columns:
        long_df["formula"] = np.nan
    long_df["Peak_area"] = pd.to_numeric(long_df["Peak_area"], errors="coerce")
    long_df["ir_value"] = np.nan
    positive_mask = long_df["Peak_area"] > 0
    long_df.loc[positive_mask, "ir_value"] = np.log10(long_df.loc[positive_mask, "Peak_area"])
    long_df["log_concentration"] = long_df["ir_value"]
    return long_df.sort_values(["sample_id", "compound"]).reset_index(drop=True)


def _missing_early_columns(data: pd.DataFrame, config: ScreeningConfig) -> list[str]:
    required = [config.compound_col, config.formula_col, config.group_area_col, *config.sample_cols]
    return [column for column in required if column not in data.columns]
