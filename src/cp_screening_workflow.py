from __future__ import annotations

from dataclasses import dataclass, field
import io
import math
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

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
    "ToxPi_Global_Screen",
    "ToxPi_Normalized",
    "ToxPi_Results",
    "ToxPi_Display",
    "ToxPi_Excluded",
    "ToxPi_Settings",
    "ToxPi_Robustness",
    "ToxPi_Robust_Stats",
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


@dataclass(frozen=True)
class PBMToxPiConfig:
    candidate_top_n: int = 100
    display_top_n: int = 20
    evidence_per_compound_top_n: int = 10
    evidence_global_use_top_n: int = 30
    weights: dict[str, float] = field(default_factory=lambda: dict(PBM_TOXPI_WEIGHTS))
    robustness_enabled: bool = True
    perturbation_fraction: float = 0.20
    n_iter: int = 1000
    seed: int = 123

    def __post_init__(self):
        if int(self.candidate_top_n) < 1:
            raise ValueError("Candidate Top N must be at least 1")
        if int(self.display_top_n) < 1:
            raise ValueError("Display Top N must be at least 1")
        if int(self.display_top_n) > int(self.candidate_top_n):
            raise ValueError("Display Top N cannot exceed Candidate Top N")
        if int(self.evidence_per_compound_top_n) < 1:
            raise ValueError("Evidence per-compound Top N must be at least 1")
        if int(self.evidence_global_use_top_n) < 1:
            raise ValueError("Evidence global-use Top N must be at least 1")
        perturbation_fraction = float(self.perturbation_fraction)
        if not math.isfinite(perturbation_fraction):
            raise ValueError("Weight perturbation must be finite")
        if perturbation_fraction < 0 or perturbation_fraction > 1:
            raise ValueError("Weight perturbation must be between 0% and 100%")
        if int(self.n_iter) < 1:
            raise ValueError("Robustness iterations must be at least 1")
        normalize_pbm_toxpi_weights(self.weights)


@dataclass
class PBMToxPiResult:
    config: PBMToxPiConfig
    source_metrics: pd.DataFrame
    global_screen: pd.DataFrame
    candidate_normalized: pd.DataFrame
    final_ranking: pd.DataFrame
    display_rows: pd.DataFrame
    normalized_weights: dict[str, float]
    effective_candidate_top_n: int
    effective_display_top_n: int
    robustness_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    robustness_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    robustness_correlations: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluded_rows: pd.DataFrame = field(default_factory=pd.DataFrame)

    def settings_table(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"setting": "requested_candidate_top_n", "value": self.config.candidate_top_n},
                {"setting": "candidate_top_n", "value": self.effective_candidate_top_n},
                {"setting": "requested_display_top_n", "value": self.config.display_top_n},
                {"setting": "display_top_n", "value": self.effective_display_top_n},
                {
                    "setting": "evidence_per_compound_top_n",
                    "value": self.config.evidence_per_compound_top_n,
                },
                {
                    "setting": "evidence_global_use_top_n",
                    "value": self.config.evidence_global_use_top_n,
                },
                {"setting": "robustness_enabled", "value": self.config.robustness_enabled},
                {"setting": "perturbation_fraction", "value": self.config.perturbation_fraction},
                {"setting": "robustness_iterations", "value": self.config.n_iter},
                {"setting": "robustness_seed", "value": self.config.seed},
                *[
                    {"setting": f"weight_{name}", "value": value}
                    for name, value in self.normalized_weights.items()
                ],
            ]
        )


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
        pov_metadata = {
            "Status": "Pov_LRTP_Status",
            "model_input_complete": "Pov_LRTP_model_input_complete",
            "Error": "Pov_LRTP_Error",
        }
        available_metadata = [column for column in pov_metadata if column in pov.columns]
        merged = base.merge(
            pov[["compound_key", score_col, *available_metadata]],
            on="compound_key",
            how="left",
        )
        merged = merged.rename(columns={score_col: "Scores", **pov_metadata})

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
            **{
                column: merged[column]
                for column in [
                    "Pov_LRTP_Status",
                    "Pov_LRTP_model_input_complete",
                    "Pov_LRTP_Error",
                ]
                if column in merged.columns
            },
        }
    )
    return output


def normalize_pbm_toxpi_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    supplied = PBM_TOXPI_WEIGHTS if weights is None else weights
    required = tuple(PBM_TOXPI_WEIGHTS)
    values = {name: float(supplied.get(name, 0.0)) for name in required}
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("ToxPi weights must be finite")
    if any(value < 0 for value in values.values()):
        raise ValueError("ToxPi weights cannot be negative")
    total = sum(values.values())
    if total <= 0:
        raise ValueError("ToxPi weights must sum to a positive value")
    return {name: value / total for name, value in values.items()}


def _compound_toxpi_source(toxpi_input: pd.DataFrame) -> pd.DataFrame:
    data = toxpi_input.copy()
    data["compound"] = data["compound"].map(_clean_text)
    for column in ("Peak_Area", "Scores", "DF"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["_input_exclusion_reason"] = ""
    data.loc[
        ~np.isfinite(data["Peak_Area"]) | data["Peak_Area"].le(0),
        "_input_exclusion_reason",
    ] = "Peak area missing or non-positive"
    data.loc[
        data["_input_exclusion_reason"].eq("") & ~np.isfinite(data["Scores"]),
        "_input_exclusion_reason",
    ] = "PBM score missing"
    data.loc[
        data["_input_exclusion_reason"].eq("")
        & (~np.isfinite(data["DF"]) | ~data["DF"].between(0, 1)),
        "_input_exclusion_reason",
    ] = "DF missing or outside [0, 1]"
    aggregations = {"Peak_Area": "mean", "Scores": "mean", "DF": "mean"}
    if "Pov_LRTP_Status" in data.columns:
        aggregations["Pov_LRTP_Status"] = lambda values: (
            "ok" if values.eq("ok").all() else values.loc[values.ne("ok")].iloc[0]
        )
    if "Pov_LRTP_model_input_complete" in data.columns:
        aggregations["Pov_LRTP_model_input_complete"] = lambda values: values.eq(True).all()
    if "Pov_LRTP_Error" in data.columns:
        aggregations["Pov_LRTP_Error"] = "first"
    aggregations["_input_exclusion_reason"] = lambda values: next(
        (
            reason
            for reason in (
                "Peak area missing or non-positive",
                "PBM score missing",
                "DF missing or outside [0, 1]",
            )
            if values.eq(reason).any()
        ),
        "",
    )
    source = data.groupby("compound", as_index=False).agg(aggregations)
    source["ir_value"] = np.nan
    mask = source["Peak_Area"] > 0
    source.loc[mask, "ir_value"] = np.log10(source.loc[mask, "Peak_Area"])
    return source


def _split_toxpi_eligibility(source: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = source.copy()
    for column in ("Peak_Area", "Scores", "DF"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    reasons = pd.Series("", index=data.index, dtype=object)
    reasons = reasons.mask(data["compound"].map(_clean_text).eq(""), "Compound name missing")
    if "_input_exclusion_reason" in data:
        reasons = reasons.mask(
            reasons.eq("") & data["_input_exclusion_reason"].ne(""),
            data["_input_exclusion_reason"],
        )
    reasons = reasons.mask(
        reasons.eq("") & (~np.isfinite(data["Peak_Area"]) | data["Peak_Area"].le(0)),
        "Peak area missing or non-positive",
    )
    reasons = reasons.mask(reasons.eq("") & ~np.isfinite(data["Scores"]), "PBM score missing")
    reasons = reasons.mask(
        reasons.eq("") & (~np.isfinite(data["DF"]) | ~data["DF"].between(0, 1)),
        "DF missing or outside [0, 1]",
    )
    if "Pov_LRTP_Status" in data:
        reasons = reasons.mask(
            reasons.eq("") & data["Pov_LRTP_Status"].ne("ok"),
            "Pov-LRTP status is not ok",
        )
    if "Pov_LRTP_model_input_complete" in data:
        reasons = reasons.mask(
            reasons.eq("") & data["Pov_LRTP_model_input_complete"].ne(True),
            "Pov-LRTP model input incomplete",
        )
    excluded = data.loc[reasons.ne("")].assign(exclusion_reason=reasons.loc[reasons.ne("")])
    return data.loc[reasons.eq("")].reset_index(drop=True), excluded.reset_index(drop=True)


def _score_pbm_toxpi_stage(source: pd.DataFrame, weights: dict[str, float], score_name: str) -> pd.DataFrame:
    scored = source.copy()
    scored["norm_peak_area"] = _normalize_positive(scored["ir_value"])
    scored["norm_pbm"] = _normalize_positive(scored["Scores"])
    scored["norm_df"] = pd.to_numeric(scored["DF"], errors="coerce").clip(0, 1)
    matrix = scored[["norm_peak_area", "norm_pbm", "norm_df"]].to_numpy(dtype=float)
    vector = np.array([weights["peak_area"], weights["pbm"], weights["df"]], dtype=float)
    scored[score_name] = _weighted_indicator_scores(matrix, vector)
    return scored


def _weighted_indicator_scores(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
    valid = ~np.isnan(matrix)
    clean = np.nan_to_num(matrix, nan=0.0)
    denominators = valid @ weights
    numerators = (clean * weights).sum(axis=1)
    return np.divide(
        numerators,
        denominators,
        out=np.full(len(matrix), np.nan, dtype=float),
        where=denominators > 0,
    )


def _sort_toxpi_stage(frame: pd.DataFrame, score_col: str, rank_col: str) -> pd.DataFrame:
    ranked = frame.copy()
    ranked["_compound_key"] = ranked["compound"].map(lambda value: _clean_text(value).casefold())
    ranked = ranked.sort_values(
        [score_col, "Peak_Area", "_compound_key"],
        ascending=[False, False, True],
        na_position="last",
        kind="mergesort",
    ).drop(columns="_compound_key").reset_index(drop=True)
    ranked[rank_col] = np.arange(1, len(ranked) + 1)
    ranked.attrs["toxic_cols"] = ["peak_area", "pbm", "df"]
    return ranked


def run_pbm_toxpi_robustness(
    result: PBMToxPiResult,
    config: PBMToxPiConfig,
) -> PBMToxPiResult:
    candidates = result.final_ranking.copy()
    if len(candidates) < 2:
        raise ValueError("At least 2 candidates are required for robustness analysis")

    cols = ["norm_peak_area", "norm_pbm", "norm_df"]
    matrix = candidates[cols].to_numpy(dtype=float)
    baseline = np.array([result.normalized_weights[name] for name in PBM_TOXPI_WEIGHTS])
    rng = np.random.default_rng(int(config.seed))
    lower = 1.0 - float(config.perturbation_fraction)
    upper = 1.0 + float(config.perturbation_fraction)
    multipliers = rng.uniform(lower, upper, size=(int(config.n_iter), len(baseline)))
    weights = multipliers * baseline
    weights = weights / weights.sum(axis=1, keepdims=True)
    baseline_ranks = candidates["final_rank"].to_numpy(dtype=float)
    counts = np.zeros(len(candidates), dtype=int)
    correlations = []
    top_n = result.effective_display_top_n

    for index, simulated_weights in enumerate(weights, start=1):
        scores = _weighted_indicator_scores(matrix, simulated_weights)
        order = np.argsort(-scores, kind="stable")
        ranks = np.empty(len(order), dtype=float)
        ranks[order] = np.arange(1, len(order) + 1)
        rho = spearmanr(baseline_ranks, ranks).statistic
        correlations.append(
            {"iteration": index, "spearman_rho": 0.0 if np.isnan(rho) else float(rho)}
        )
        counts[order[:top_n]] += 1

    correlation_df = pd.DataFrame(correlations)
    result.robustness_correlations = correlation_df
    result.robustness_summary = candidates[["compound", "toxpi", "final_rank"]].assign(
        top_n_frequency_percent=np.round(counts / int(config.n_iter) * 100, 2)
    )
    rho_values = correlation_df["spearman_rho"]
    result.robustness_stats = pd.DataFrame(
        [
            {
                "mean_rho": rho_values.mean(),
                "sd_rho": rho_values.std(ddof=0),
                "ci_lower": rho_values.quantile(0.025),
                "ci_upper": rho_values.quantile(0.975),
                "perturbation_fraction": float(config.perturbation_fraction),
                "iterations": int(config.n_iter),
                "seed": int(config.seed),
                "display_top_n": top_n,
            }
        ]
    )
    return result


def calculate_pbm_toxpi(
    toxpi_input: pd.DataFrame,
    config: PBMToxPiConfig | None = None,
) -> PBMToxPiResult:
    config = config or PBMToxPiConfig()
    required = ["compound", "Peak_Area", "Scores", "DF"]
    missing = [column for column in required if column not in toxpi_input.columns]
    if missing:
        raise ValueError(f"Missing required ToxPi input columns: {', '.join(missing)}")
    weights = normalize_pbm_toxpi_weights(config.weights)
    source, excluded_rows = _split_toxpi_eligibility(_compound_toxpi_source(toxpi_input))
    global_screen = _sort_toxpi_stage(
        _score_pbm_toxpi_stage(source, weights, "initial_toxpi"),
        "initial_toxpi",
        "initial_rank",
    )
    candidate_n = min(int(config.candidate_top_n), len(global_screen))
    candidate_source = source.merge(
        global_screen.head(candidate_n)[["compound"]], on="compound", how="inner"
    )
    candidate_normalized = _score_pbm_toxpi_stage(candidate_source, weights, "toxpi")
    final_ranking = _sort_toxpi_stage(candidate_normalized, "toxpi", "final_rank")
    display_n = min(int(config.display_top_n), len(final_ranking))
    display_rows = final_ranking.head(display_n).copy()
    result = PBMToxPiResult(
        config=config,
        source_metrics=source,
        global_screen=global_screen,
        candidate_normalized=candidate_normalized,
        final_ranking=final_ranking,
        display_rows=display_rows,
        normalized_weights=weights,
        effective_candidate_top_n=candidate_n,
        effective_display_top_n=display_n,
        excluded_rows=excluded_rows,
    )
    if config.robustness_enabled and len(result.final_ranking) >= 2:
        run_pbm_toxpi_robustness(result, config)
    return result


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


def figure_to_png_pdf_bytes(fig) -> tuple[io.BytesIO, io.BytesIO]:
    """Serialize a figure to both formats and always release it."""
    try:
        return figure_to_png_bytes(fig), figure_to_pdf_bytes(fig)
    finally:
        plt.close(fig)


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


def generate_pbm_toxpi_robustness_plot(result: PBMToxPiResult):
    if (
        result.robustness_correlations.empty
        or "spearman_rho" not in result.robustness_correlations.columns
    ):
        raise ValueError("Robustness correlations are empty")
    values = result.robustness_correlations["spearman_rho"]
    mean_rho = float(result.robustness_stats.loc[0, "mean_rho"])
    fig, ax = plt.subplots(figsize=(8, 5.5), facecolor="white")
    ax.hist(
        values,
        bins=min(30, max(5, int(np.sqrt(len(values))))),
        color="#2E8B57",
        edgecolor="black",
    )
    ax.axvline(mean_rho, color="#D62728", linestyle="--", linewidth=1.3)
    ax.set_title("ToxPi Rank Robustness")
    ax.set_xlabel("Spearman correlation with baseline ranking")
    ax.set_ylabel("Frequency")
    ax.text(0.02, 0.96, f"Mean rho = {mean_rho:.3f}", transform=ax.transAxes, va="top")
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
