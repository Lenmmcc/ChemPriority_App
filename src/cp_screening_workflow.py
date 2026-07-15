from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.plot_style import apply_figure_font, configure_plot_style


configure_plot_style()


EXPECTED_WORKBOOK_SHEETS = [
    "Input_Check",
    "Elemental_Ratios_DBE",
    "Category_Summary",
    "Sample_Peak_Area",
    "Group_Area_Raw_Long",
    "Group_Area_Mean_By_Sample",
    "DF_Table",
    "Identifier_Completion",
    "EPI_Results",
    "Pov_LRTP",
    "PBM_Scores",
    "ToxPi_Input",
    "ToxPi_Results",
    "Excluded_or_Failed",
    "Warnings",
]

PBM_TOXPI_WEIGHTS = {
    "peak_area": 0.4,
    "pbm": 0.4,
    "df": 0.2,
}

DEFAULT_TOXPI_PLOT_TOP_N = 15


@dataclass(frozen=True)
class SampleTable:
    sample_id: str
    data: pd.DataFrame


def build_detection_frequency(
    samples: Iterable[tuple[str, pd.DataFrame] | SampleTable],
    compound_col: str = "Name",
    peak_area_col: str | list[str] = "Group_Area",
    detection_threshold: float = 1e5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sample_tables = [_coerce_sample_table(sample) for sample in samples]
    peak_area_cols = _normalize_peak_area_cols(peak_area_col)
    if not sample_tables:
        return _empty_df_table(), _empty_sample_peak_area()

    mean_table = build_group_area_mean_by_sample(
        sample_tables,
        compound_col=compound_col,
        peak_area_cols=peak_area_cols,
    )
    if mean_table.empty:
        return _empty_df_table(), _empty_sample_peak_area()

    compound_labels = (
        mean_table.drop_duplicates("compound_key")
        .set_index("compound_key")["compound"]
        .to_dict()
    )
    all_keys = sorted(compound_labels)
    participating_sample_ids = list(dict.fromkeys(mean_table["source_sample_id"].astype(str)))
    participating_samples = [sample for sample in sample_tables if sample.sample_id in set(participating_sample_ids)]
    rows = []
    for sample in participating_samples:
        sample_mean = mean_table[mean_table["source_sample_id"].eq(sample.sample_id)]
        summary_by_key = sample_mean.set_index("compound_key") if not sample_mean.empty else pd.DataFrame()
        for key in all_keys:
            peak_area = np.nan
            if not summary_by_key.empty and key in summary_by_key.index:
                peak_area = summary_by_key.loc[key, "Group_Area_Mean"]
            detected = bool(pd.notna(peak_area) and float(peak_area) > detection_threshold)
            rows.append(
                {
                    "sample_id": sample.sample_id,
                    "source_sample_id": sample.sample_id,
                    "sample_point": "Group_Area_Mean",
                    "compound": compound_labels[key],
                    "compound_key": key,
                    "peak_area": peak_area,
                    "detected": detected,
                }
            )

    sample_peak_area = pd.DataFrame(rows)
    total_sample_count = len(participating_samples)
    if total_sample_count == 0:
        return _empty_df_table(), _empty_sample_peak_area()
    df_table = (
        sample_peak_area.groupby(["compound_key", "compound"], as_index=False)
        .agg(
            detected_sample_count=("detected", "sum"),
            total_sample_count=("sample_id", "nunique"),
            Peak_Area=("peak_area", "max"),
        )
        .sort_values("compound")
        .reset_index(drop=True)
    )
    df_table["DF"] = df_table["detected_sample_count"] / total_sample_count
    df_table["total_sample_count"] = total_sample_count
    df_table = df_table[
        [
            "compound",
            "compound_key",
            "detected_sample_count",
            "total_sample_count",
            "DF",
            "Peak_Area",
        ]
    ]

    return df_table, sample_peak_area.sort_values(["sample_id", "compound"]).reset_index(drop=True)


def build_group_area_mean_by_sample(
    samples: Iterable[tuple[str, pd.DataFrame] | SampleTable],
    compound_col: str = "Name",
    peak_area_cols: list[str] | None = None,
    formula_col: str | None = None,
) -> pd.DataFrame:
    sample_tables = [_coerce_sample_table(sample) for sample in samples]
    peak_area_cols = _normalize_peak_area_cols(peak_area_cols or [])
    if not sample_tables or not peak_area_cols:
        return _empty_group_area_mean_by_sample()

    frames = []
    for sample in sample_tables:
        available_peak_area_cols = [column for column in peak_area_cols if column in sample.data.columns]
        if not available_peak_area_cols:
            continue
        missing = [column for column in [compound_col] if column not in sample.data.columns]
        if missing:
            raise ValueError(f"{sample.sample_id} missing required columns: {', '.join(missing)}")

        id_cols = [compound_col]
        include_formula = bool(formula_col and formula_col in sample.data.columns)
        if include_formula:
            id_cols.append(str(formula_col))

        frame = sample.data[id_cols + available_peak_area_cols].copy()
        frame["source_sample_id"] = sample.sample_id
        frame["sample_id"] = sample.sample_id
        frame["compound"] = frame[compound_col].map(_clean_text)
        frame["compound_key"] = frame["compound"].map(_compound_key)
        if include_formula:
            frame["formula"] = frame[str(formula_col)]
        else:
            frame["formula"] = pd.NA

        numeric = frame[available_peak_area_cols].apply(pd.to_numeric, errors="coerce")
        frame["Group_Area_Mean"] = numeric.mean(axis=1, skipna=True)
        frame["Peak_Area"] = frame["Group_Area_Mean"]
        frame["group_area_count"] = numeric.notna().sum(axis=1)
        frame["group_area_columns"] = "; ".join(available_peak_area_cols)
        frame["ir_value"] = np.nan
        positive_mask = frame["Peak_Area"] > 0
        frame.loc[positive_mask, "ir_value"] = np.log10(frame.loc[positive_mask, "Peak_Area"])
        frame["log_concentration"] = frame["ir_value"]
        frame = frame.loc[frame["compound_key"].ne("")].copy()
        frames.append(
            frame[
                [
                    "source_sample_id",
                    "sample_id",
                    "compound",
                    "compound_key",
                    "formula",
                    "Group_Area_Mean",
                    "Peak_Area",
                    "ir_value",
                    "log_concentration",
                    "group_area_count",
                    "group_area_columns",
                ]
            ]
        )

    if not frames:
        return _empty_group_area_mean_by_sample()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["source_sample_id", "compound_key", "Group_Area_Mean"],
        ascending=[True, True, False],
        na_position="last",
    )
    collapsed = (
        combined.groupby(["source_sample_id", "compound_key"], as_index=False)
        .agg(
            sample_id=("sample_id", "first"),
            compound=("compound", "first"),
            formula=("formula", "first"),
            Group_Area_Mean=("Group_Area_Mean", "max"),
            Peak_Area=("Peak_Area", "max"),
            group_area_count=("group_area_count", "max"),
            group_area_columns=("group_area_columns", "first"),
        )
        .sort_values(["source_sample_id", "compound"])
        .reset_index(drop=True)
    )
    collapsed["ir_value"] = np.nan
    positive_mask = collapsed["Peak_Area"] > 0
    collapsed.loc[positive_mask, "ir_value"] = np.log10(collapsed.loc[positive_mask, "Peak_Area"])
    collapsed["log_concentration"] = collapsed["ir_value"]
    return collapsed[
        [
            "source_sample_id",
            "sample_id",
            "compound",
            "compound_key",
            "formula",
            "Group_Area_Mean",
            "Peak_Area",
            "ir_value",
            "log_concentration",
            "group_area_count",
            "group_area_columns",
        ]
    ]


def build_peak_area_long(
    samples: Iterable[tuple[str, pd.DataFrame] | SampleTable],
    compound_col: str = "Name",
    peak_area_cols: list[str] | None = None,
    formula_col: str | None = None,
) -> pd.DataFrame:
    sample_tables = [_coerce_sample_table(sample) for sample in samples]
    peak_area_cols = _normalize_peak_area_cols(peak_area_cols or [])
    if not sample_tables or not peak_area_cols:
        return _empty_peak_area_long()

    frames = []
    for sample in sample_tables:
        available_peak_area_cols = [column for column in peak_area_cols if column in sample.data.columns]
        if not available_peak_area_cols:
            continue
        missing = [column for column in [compound_col] if column not in sample.data.columns]
        if missing:
            raise ValueError(f"{sample.sample_id} missing required columns: {', '.join(missing)}")

        id_cols = [compound_col]
        include_formula = bool(formula_col and formula_col in sample.data.columns)
        if include_formula:
            id_cols.append(str(formula_col))

        frame = sample.data[id_cols + available_peak_area_cols].copy()
        long_df = frame.melt(
            id_vars=id_cols,
            value_vars=available_peak_area_cols,
            var_name="sample_id",
            value_name="Peak_Area",
        )
        long_df.insert(0, "source_sample_id", sample.sample_id)
        long_df["compound"] = long_df[compound_col].map(_clean_text)
        long_df["compound_key"] = long_df["compound"].map(_compound_key)
        if include_formula:
            long_df["formula"] = long_df[str(formula_col)]
        else:
            long_df["formula"] = pd.NA
        long_df["Peak_Area"] = pd.to_numeric(long_df["Peak_Area"], errors="coerce")
        long_df["ir_value"] = np.nan
        positive_mask = long_df["Peak_Area"] > 0
        long_df.loc[positive_mask, "ir_value"] = np.log10(long_df.loc[positive_mask, "Peak_Area"])
        long_df["log_concentration"] = long_df["ir_value"]
        long_df = long_df.loc[long_df["compound_key"].ne("")].copy()
        frames.append(
            long_df[
                [
                    "source_sample_id",
                    "sample_id",
                    "compound",
                    "compound_key",
                    "formula",
                    "Peak_Area",
                    "ir_value",
                    "log_concentration",
                ]
            ]
        )

    if not frames:
        return _empty_peak_area_long()
    return pd.concat(frames, ignore_index=True).sort_values(
        ["source_sample_id", "sample_id", "compound"]
    ).reset_index(drop=True)


def build_pbm_toxpi_input(
    df_table: pd.DataFrame,
    pov_lrtp_results: pd.DataFrame,
    peak_area_long: pd.DataFrame | None = None,
    compound_col: str = "compound",
) -> pd.DataFrame:
    if df_table is None or df_table.empty:
        return pd.DataFrame(columns=["compound", "Peak_Area", "Scores", "DF"])

    df_base = df_table.copy()
    df_base["compound"] = df_base[compound_col].map(_clean_text)
    df_base["compound_key"] = df_base["compound"].map(_compound_key)

    if peak_area_long is not None and not peak_area_long.empty:
        peak_base = peak_area_long.copy()
        peak_base["compound"] = peak_base["compound"].map(_clean_text)
        peak_base["compound_key"] = peak_base["compound"].map(_compound_key)
        keep_cols = [
            column
            for column in ["source_sample_id", "sample_id", "compound", "compound_key", "Peak_Area"]
            if column in peak_base.columns
        ]
        base = peak_base[keep_cols].merge(
            df_base[["compound_key", "DF"]],
            on="compound_key",
            how="left",
        )
    else:
        base = df_base.copy()

    pov = pd.DataFrame() if pov_lrtp_results is None else pov_lrtp_results.copy()
    if pov.empty:
        merged = base.copy()
        merged["Scores"] = np.nan
    else:
        pov_name_col = "Name" if "Name" in pov.columns else "compound"
        pov[pov_name_col] = pov[pov_name_col].map(_clean_text)
        pov["compound_key"] = pov[pov_name_col].map(_compound_key)
        pov = pov.loc[pov["compound_key"].ne("")].drop_duplicates("compound_key", keep="first")
        score_col = "Scores" if "Scores" in pov.columns else "P_B_LRTP_score"
        merged = base.merge(
            pov[["compound_key", score_col]],
            on="compound_key",
            how="left",
        )
        merged = merged.rename(columns={score_col: "Scores"})

    output = pd.DataFrame(
        {
            **{
                column: merged[column]
                for column in ["source_sample_id", "sample_id"]
                if column in merged.columns
            },
            "compound": merged["compound"],
            "Peak_Area": pd.to_numeric(merged.get("Peak_Area"), errors="coerce"),
            "Scores": pd.to_numeric(merged.get("Scores"), errors="coerce"),
            "DF": pd.to_numeric(merged.get("DF"), errors="coerce"),
        }
    )
    return output


def calculate_pbm_toxpi(
    toxpi_input: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    weights = weights or PBM_TOXPI_WEIGHTS
    required = ["compound", "Peak_Area", "Scores", "DF"]
    missing = [column for column in required if column not in toxpi_input.columns]
    if missing:
        raise ValueError(f"Missing required ToxPi input columns: {', '.join(missing)}")

    data = toxpi_input.copy()
    data["compound"] = data["compound"].map(_clean_text)
    data["Peak_Area"] = pd.to_numeric(data["Peak_Area"], errors="coerce")
    data["Scores"] = pd.to_numeric(data["Scores"], errors="coerce")
    data["DF"] = pd.to_numeric(data["DF"], errors="coerce")
    data["ir_value"] = np.nan
    positive_area = data["Peak_Area"] > 0
    data.loc[positive_area, "ir_value"] = np.log10(data.loc[positive_area, "Peak_Area"])

    data["norm_peak_area"] = _normalize_positive(data["ir_value"])
    data["norm_pbm"] = _normalize_positive(data["Scores"])
    data["norm_df"] = data["DF"].clip(lower=0, upper=1)

    total_weight = sum(float(value) for value in weights.values())
    if total_weight <= 0:
        raise ValueError("ToxPi weights must sum to a positive value")
    normalized_weights = {key: float(value) / total_weight for key, value in weights.items()}

    data["toxpi"] = (
        data["norm_peak_area"] * normalized_weights["peak_area"]
        + data["norm_pbm"] * normalized_weights["pbm"]
        + data["norm_df"] * normalized_weights["df"]
    )

    toxpi_results = (
        data.groupby("compound", as_index=False)
        .agg(
            Peak_Area=("Peak_Area", "mean"),
            Scores=("Scores", "mean"),
            DF=("DF", "mean"),
            norm_peak_area=("norm_peak_area", "mean"),
            norm_pbm=("norm_pbm", "mean"),
            norm_df=("norm_df", "mean"),
            toxpi=("toxpi", "mean"),
        )
        .sort_values("toxpi", ascending=False)
        .reset_index(drop=True)
    )
    toxpi_results.attrs["toxic_cols"] = ["peak_area", "pbm", "df"]
    return data, toxpi_results


def limit_toxpi_plot_rows(
    toxpi_results: pd.DataFrame,
    max_compounds: int = DEFAULT_TOXPI_PLOT_TOP_N,
    score_col: str = "toxpi",
) -> tuple[pd.DataFrame, int]:
    if toxpi_results is None or toxpi_results.empty:
        return pd.DataFrame(), 0

    max_compounds = max(1, int(max_compounds))
    plot_rows = toxpi_results.copy()
    if score_col in plot_rows.columns:
        plot_rows[score_col] = pd.to_numeric(plot_rows[score_col], errors="coerce")
        plot_rows = plot_rows.sort_values(score_col, ascending=False, na_position="last")
    plot_rows = plot_rows.head(max_compounds).reset_index(drop=True)
    plot_rows.attrs.update(getattr(toxpi_results, "attrs", {}))
    omitted_count = max(0, len(toxpi_results) - len(plot_rows))
    return plot_rows, omitted_count


def build_screening_workbook(tables: dict[str, pd.DataFrame | None]) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name in EXPECTED_WORKBOOK_SHEETS:
            table = tables.get(sheet_name)
            if table is None:
                table = pd.DataFrame()
            table.to_excel(writer, sheet_name=sheet_name, index=False)
    buffer.seek(0)
    return buffer


def with_warning_stage(table: pd.DataFrame, fallback_stage: str) -> pd.DataFrame:
    warning_table = table.copy()
    if warning_table.empty:
        return warning_table

    if "stage" not in warning_table.columns:
        warning_table.insert(0, "stage", fallback_stage)
        return warning_table

    stage_values = warning_table["stage"].map(_clean_text)
    warning_table["stage"] = stage_values.mask(stage_values.eq(""), fallback_stage)
    return warning_table[["stage", *[column for column in warning_table.columns if column != "stage"]]]


def figure_to_png_bytes(fig) -> io.BytesIO:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=300, bbox_inches="tight", facecolor="white")
    buffer.seek(0)
    return buffer


def figure_to_pdf_bytes(fig) -> io.BytesIO:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="pdf", dpi=300, bbox_inches="tight", facecolor="white")
    buffer.seek(0)
    return buffer


def generate_pbm_toxpi_bar_plot(toxpi_results: pd.DataFrame, top_n: int = 15):
    plot_df = toxpi_results.head(top_n).copy()
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(plot_df["compound"], plot_df["toxpi"], color="#3B82F6", edgecolor="black", linewidth=0.6)
    ax.set_title("PA/PBM/DF ToxPi Ranking", fontsize=14, fontweight="bold")
    ax.set_xlabel("Compound")
    ax.set_ylabel("ToxPi Score")
    ax.tick_params(axis="x", rotation=90)
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.6)
    fig.tight_layout()
    return apply_figure_font(fig)


def _normalize_positive(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.dropna()
    output = pd.Series(np.nan, index=numeric.index, dtype=float)
    if valid.empty:
        return output

    q05, q95 = np.nanpercentile(valid, [5, 95])
    if q95 == q05:
        min_value = valid.min()
        max_value = valid.max()
        if max_value == min_value:
            output.loc[valid.index] = 0.0
        else:
            output.loc[valid.index] = (valid - min_value) / (max_value - min_value)
    else:
        output.loc[valid.index] = (valid - q05) / (q95 - q05)
    return output.clip(lower=0, upper=1)


def _coerce_sample_table(sample: tuple[str, pd.DataFrame] | SampleTable) -> SampleTable:
    if isinstance(sample, SampleTable):
        return sample
    sample_id, data = sample
    return SampleTable(sample_id=_sample_id(sample_id), data=data.copy())


def _normalize_peak_area_cols(peak_area_col: str | list[str]) -> list[str]:
    if peak_area_col is None:
        return []
    if isinstance(peak_area_col, str):
        return [peak_area_col] if peak_area_col else []
    return [str(column) for column in peak_area_col if str(column)]


def _row_peak_area(frame: pd.DataFrame, peak_area_cols: list[str]) -> pd.Series:
    if not peak_area_cols:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    numeric = frame[peak_area_cols].apply(pd.to_numeric, errors="coerce")
    return numeric.max(axis=1, skipna=True)


def _sample_id(value: object) -> str:
    text = _clean_text(value)
    if text.lower().endswith((".xlsx", ".xls")):
        text = text.rsplit(".", 1)[0]
    return text or "sample"


def _compound_key(value: object) -> str:
    return " ".join(_clean_text(value).lower().split())


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _empty_df_table() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "compound",
            "compound_key",
            "detected_sample_count",
            "total_sample_count",
            "DF",
            "Peak_Area",
        ]
    )


def _empty_sample_peak_area() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "sample_id",
            "source_sample_id",
            "sample_point",
            "compound",
            "compound_key",
            "peak_area",
            "detected",
        ]
    )


def _empty_peak_area_long() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_sample_id",
            "sample_id",
            "compound",
            "compound_key",
            "formula",
            "Peak_Area",
            "ir_value",
            "log_concentration",
        ]
    )


def _empty_group_area_mean_by_sample() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_sample_id",
            "sample_id",
            "compound",
            "compound_key",
            "formula",
            "Group_Area_Mean",
            "Peak_Area",
            "ir_value",
            "log_concentration",
            "group_area_count",
            "group_area_columns",
        ]
    )
