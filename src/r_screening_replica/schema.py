from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ScreeningConfig:
    sheet_name: int | str = 0
    compound_col: str = "Name"
    formula_col: str = "formula"
    group_area_col: str = "Group_Area"
    sample_cols: list[str] = field(default_factory=lambda: ["HH_alk", "WH_alk"])
    output_dir: Path | str = Path("outputs/r_screening_replica")

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)


@dataclass
class ScreeningResult:
    config: ScreeningConfig
    raw_data: pd.DataFrame
    input_check: pd.DataFrame
    all_formulas: pd.DataFrame
    compound_categories: pd.DataFrame
    category_summary: pd.DataFrame
    carbon_formulas: pd.DataFrame
    non_carbon_compounds: pd.DataFrame
    dbe_table: pd.DataFrame
    sample_peak_area_long: pd.DataFrame
    figure_paths: dict[str, dict[str, Path]]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
