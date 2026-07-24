from __future__ import annotations

from dataclasses import dataclass, field
import io

import pandas as pd

from src.episuite_io import ENDPOINT_KEYS, parse_table_result


RECOGNIZED_RESULT_SHEETS = ("Core_Summary", "EPI_Results")
MISSING_ENDPOINT_WARNING_PREFIX = "以下目标指标没有在表格列名中识别到："


@dataclass(frozen=True)
class EPIWorkbookInspection:
    file_name: str
    sheet_names: tuple[str, ...]
    default_result_sheet: str | None


@dataclass(frozen=True)
class EPISupplementMapping:
    source_file: str
    primary_file: str
    sheet_name: str
    compound_col: str | None = None
    smiles_col: str | None = None
    cas_col: str | None = None
    endpoint_columns: dict[str, str] = field(default_factory=dict)
    priority: int = 0


def inspect_epi_workbook(data: bytes, file_name: str) -> EPIWorkbookInspection:
    workbook = pd.ExcelFile(io.BytesIO(data))
    default_sheet = next(
        (name for name in RECOGNIZED_RESULT_SHEETS if name in workbook.sheet_names),
        None,
    )
    return EPIWorkbookInspection(
        file_name=str(file_name),
        sheet_names=tuple(workbook.sheet_names),
        default_result_sheet=default_sheet,
    )


def parse_epi_supplement(
    data: bytes,
    mapping: EPISupplementMapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.read_excel(io.BytesIO(data), sheet_name=mapping.sheet_name)
    rename_map = {
        source: target
        for target, source in {
            "compound": mapping.compound_col,
            "smiles": mapping.smiles_col,
            "cas": mapping.cas_col,
            **mapping.endpoint_columns,
        }.items()
        if source and source in frame.columns
    }
    normalized = frame.rename(columns=rename_map)
    if "log_kow" not in normalized.columns:
        experimental = normalized.get("log_kow_experimental")
        estimated = normalized.get("log_kow_estimated")
        if experimental is not None:
            normalized["log_kow"] = (
                experimental.combine_first(estimated)
                if estimated is not None
                else experimental
            )
        elif estimated is not None:
            normalized["log_kow"] = estimated
    parsed, warnings = parse_table_result(
        normalized,
        source_name=mapping.source_file,
    )
    if not warnings.empty:
        warnings = warnings.loc[
            ~warnings["warning"].astype("string").str.startswith(
                MISSING_ENDPOINT_WARNING_PREFIX,
                na=False,
            )
        ].reset_index(drop=True)
    parsed["primary_file"] = mapping.primary_file
    parsed["source_sheet"] = mapping.sheet_name
    parsed["source_priority"] = int(mapping.priority)
    for endpoint in ENDPOINT_KEYS:
        if endpoint not in parsed.columns:
            parsed[endpoint] = pd.NA
    return parsed, warnings
