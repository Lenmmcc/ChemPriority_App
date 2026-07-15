from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ScreeningAxisRanges:
    dbe_x_min: float = 0.0
    dbe_x_max: float = 60.0
    dbe_y_min: float = 0.0
    dbe_y_max: float = 30.0
    vk_x_min: float = 0.0
    vk_x_max: float = 1.1
    vk_y_min: float = 0.0
    vk_y_max: float = 2.6

    def __post_init__(self):
        for label, lower, upper in (
            ("DBE X", self.dbe_x_min, self.dbe_x_max),
            ("DBE Y", self.dbe_y_min, self.dbe_y_max),
            ("Van Krevelen X", self.vk_x_min, self.vk_x_max),
            ("Van Krevelen Y", self.vk_y_min, self.vk_y_max),
        ):
            lower_value = float(lower)
            upper_value = float(upper)
            if not math.isfinite(lower_value) or not math.isfinite(upper_value):
                raise ValueError(f"{label} bounds must be finite")
            if upper_value <= lower_value:
                raise ValueError(f"{label} maximum must be greater than minimum")

    @property
    def dbe_xlim(self):
        return (float(self.dbe_x_min), float(self.dbe_x_max))

    @property
    def dbe_ylim(self):
        return (float(self.dbe_y_min), float(self.dbe_y_max))

    @property
    def vk_xlim(self):
        return (float(self.vk_x_min), float(self.vk_x_max))

    @property
    def vk_ylim(self):
        return (float(self.vk_y_min), float(self.vk_y_max))


@dataclass(frozen=True)
class ScreeningConfig:
    sheet_name: int | str = 0
    compound_col: str = "Name"
    formula_col: str = "formula"
    group_area_col: str = "Group_Area"
    sample_cols: list[str] = field(default_factory=lambda: ["HH_alk", "WH_alk"])
    output_dir: Path | str = Path("outputs/r_screening_replica")
    axis_ranges: ScreeningAxisRanges = field(default_factory=ScreeningAxisRanges)

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
