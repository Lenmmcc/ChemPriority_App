import io
import math
import re
import unicodedata

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch


# The rose plot is exported on hosts that may not include Chinese fonts. Keep
# all plot text ASCII and use Matplotlib's bundled cross-platform sans-serif.
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["pdf.fonttype"] = 42


USE_COLOR_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2",
    "#b279a2", "#ff9da6", "#9d755d", "#bab0ac", "#59a14f",
]


PLOT_DATA_COLUMNS = [
    "source",
    "compound",
    "compound_label",
    "rank",
    "use_cn",
    "use_label",
    "evidence_count",
    "angle_fraction",
    "angle_basis",
]

TOP_PREDICTED_FUNCTIONAL_COLUMNS = [
    "source",
    "compound",
    "compound_label",
    "use_cn",
    "use_label",
    "display_label",
    "probability",
    "status",
]

REPORTED_FUNCTIONAL_PRESENCE_COLUMNS = [
    "source",
    "compound",
    "compound_label",
    "use_cn",
    "use_label",
    "presence",
]

HIGH_CONFIDENCE_PROBABILITY_THRESHOLD = 0.8


def extract_use_rose_data(summary_df, source_label, use_prefix="用途"):
    """Convert legacy rank-style summary rows into long-form plot data."""
    if summary_df is None or summary_df.empty:
        return _empty_plot_data()

    use_ranks = _find_use_ranks(summary_df.columns, use_prefix=use_prefix)
    rows = []
    for compound_index, (_, row) in enumerate(summary_df.iterrows(), start=1):
        compound = _clean_text(row.get("compound")) or "未命名化合物"
        compound_label = _ascii_label(compound, f"Compound {compound_index}")
        entries = []
        for rank in use_ranks:
            use_cn = _clean_text(row.get(f"{use_prefix}{rank}"))
            if not use_cn:
                continue
            use_en = _clean_text(row.get(f"{use_prefix}{rank}_英文证据"))
            evidence = _to_number(row.get(f"{use_prefix}{rank}_证据数量"))
            entries.append(
                {
                    "rank": rank,
                    "use_cn": use_cn,
                    "use_label": _ascii_label(use_en, f"Use category {rank}"),
                    "evidence_count": evidence,
                }
            )

        if not entries:
            continue

        valid_total = sum(item["evidence_count"] for item in entries if not pd.isna(item["evidence_count"]))
        if valid_total > 0:
            for item in entries:
                evidence = 0.0 if pd.isna(item["evidence_count"]) else float(item["evidence_count"])
                angle_fraction = evidence / valid_total
                angle_basis = "evidence_count"
                rows.append(
                    {
                        "source": source_label,
                        "compound": compound,
                        "compound_label": compound_label,
                        "rank": item["rank"],
                        "use_cn": item["use_cn"],
                        "use_label": item["use_label"],
                        "evidence_count": item["evidence_count"],
                        "angle_fraction": angle_fraction,
                        "angle_basis": angle_basis,
                    }
                )
        else:
            equal_fraction = 1.0 / len(entries)
            for item in entries:
                rows.append(
                    {
                        "source": source_label,
                        "compound": compound,
                        "compound_label": compound_label,
                        "rank": item["rank"],
                        "use_cn": item["use_cn"],
                        "use_label": item["use_label"],
                        "evidence_count": item["evidence_count"],
                        "angle_fraction": equal_fraction,
                        "angle_basis": "equal_fallback",
                    }
                )

    return pd.DataFrame(rows)


def extract_candidate_use_plot_data(
    candidates_df,
    source_label,
    source_type=None,
    functional_source=None,
    use_key="category",
):
    """Aggregate complete use-detail rows into long-form plot data without truncating to Top-N."""
    if candidates_df is None or not isinstance(candidates_df, pd.DataFrame) or candidates_df.empty:
        return _empty_plot_data()

    rows = []
    filtered_df = candidates_df.copy()
    if source_type and "source_type" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["source_type"].eq(source_type)]
    if functional_source and not filtered_df.empty:
        filtered_df = filtered_df[
            filtered_df.apply(
                lambda row: _functional_source_bucket(row) == functional_source,
                axis=1,
            )
        ]
    if filtered_df.empty:
        return _empty_plot_data()

    for compound_index, (compound, compound_df) in enumerate(filtered_df.groupby("compound", sort=False), start=1):
        compound = _clean_text(compound) or "未命名化合物"
        compound_label = _ascii_label(compound, f"Compound {compound_index}")
        groups = {}
        for _, candidate in compound_df.iterrows():
            use_value, english_value = _candidate_use_values(candidate, use_key)
            if not use_value:
                continue
            evidence = _to_number(candidate.get("evidence_count"))
            if pd.isna(evidence):
                evidence = _to_number(candidate.get("probability"))
            key = _normalize_label_key(use_value)
            if key not in groups:
                groups[key] = {
                    "use_cn": use_value,
                    "use_label": _ascii_label(english_value or use_value, f"Use category {len(groups) + 1}"),
                    "evidence_count": 0.0,
                    "valid_evidence": False,
                }
            if not pd.isna(evidence):
                groups[key]["evidence_count"] += float(evidence)
                groups[key]["valid_evidence"] = True

        entries = sorted(
            groups.values(),
            key=lambda item: (item["evidence_count"], item["use_label"]),
            reverse=True,
        )
        if not entries:
            continue
        _append_angle_rows(rows, source_label, compound, compound_label, entries)

    return pd.DataFrame(rows, columns=PLOT_DATA_COLUMNS)


def extract_top_predicted_functional_use_data(candidates_df, source_label="EPA FC"):
    """Return one highest-probability predicted functional use per compound."""
    if candidates_df is None or not isinstance(candidates_df, pd.DataFrame) or candidates_df.empty:
        return _empty_top_predicted_functional_data()
    if "source_type" not in candidates_df.columns:
        return _empty_top_predicted_functional_data()

    functional_df = candidates_df[candidates_df["source_type"].eq("functional_use")].copy()
    if functional_df.empty:
        return _empty_top_predicted_functional_data()

    reported_keys_by_compound = {}
    reported_df = functional_df[
        functional_df.apply(lambda row: _functional_source_bucket(row) == "reported", axis=1)
    ]
    for compound, compound_df in reported_df.groupby("compound", sort=False):
        reported_keys_by_compound[_clean_text(compound)] = {
            key
            for _, candidate in compound_df.iterrows()
            for key in _functional_candidate_match_keys(candidate)
        }

    predicted_df = functional_df[
        functional_df.apply(lambda row: _functional_source_bucket(row) == "predicted", axis=1)
    ]
    if predicted_df.empty:
        return _empty_top_predicted_functional_data()

    rows = []
    for compound_index, (compound, compound_df) in enumerate(predicted_df.groupby("compound", sort=False), start=1):
        compound = _clean_text(compound) or "未命名化合物"
        best_candidate = None
        best_probability = -1.0
        for _, candidate in compound_df.iterrows():
            probability = _to_number(candidate.get("probability"))
            if pd.isna(probability):
                probability = _to_number(candidate.get("evidence_count"))
            if pd.isna(probability):
                continue
            if float(probability) > best_probability:
                best_probability = float(probability)
                best_candidate = candidate

        if best_candidate is None:
            continue

        use_value, english_value = _candidate_use_values(best_candidate, "raw")
        use_cn = _first_clean(best_candidate.get("use_cn"), use_value)
        use_label = _ascii_label(english_value or use_value, f"Use category {len(rows) + 1}")
        reported_keys = reported_keys_by_compound.get(_clean_text(compound), set())
        candidate_keys = _functional_candidate_match_keys(best_candidate)
        status = "reported" if reported_keys.intersection(candidate_keys) else "predicted"
        rows.append(
            {
                "source": source_label,
                "compound": compound,
                "compound_label": _ascii_label(compound, f"Compound {compound_index}"),
                "use_cn": use_cn,
                "use_label": use_label,
                "display_label": use_label,
                "probability": best_probability,
                "status": status,
            }
        )

    return pd.DataFrame(rows, columns=TOP_PREDICTED_FUNCTIONAL_COLUMNS)


def extract_reported_functional_use_presence_data(candidates_df, source_label="EPA FC"):
    """Return binary reported functional-use evidence dots by compound and use."""
    if candidates_df is None or not isinstance(candidates_df, pd.DataFrame) or candidates_df.empty:
        return _empty_reported_functional_presence_data()
    if "source_type" not in candidates_df.columns:
        return _empty_reported_functional_presence_data()

    functional_df = candidates_df[candidates_df["source_type"].eq("functional_use")].copy()
    if functional_df.empty:
        return _empty_reported_functional_presence_data()
    reported_df = functional_df[
        functional_df.apply(lambda row: _functional_source_bucket(row) == "reported", axis=1)
    ]
    if reported_df.empty:
        return _empty_reported_functional_presence_data()

    rows = []
    for compound_index, (compound, compound_df) in enumerate(reported_df.groupby("compound", sort=False), start=1):
        compound = _clean_text(compound) or "未命名化合物"
        seen = set()
        for _, candidate in compound_df.iterrows():
            use_value, english_value = _candidate_use_values(candidate, "raw")
            if not use_value:
                continue
            key = _normalize_label_key(use_value)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source": source_label,
                    "compound": compound,
                    "compound_label": _ascii_label(compound, f"Compound {compound_index}"),
                    "use_cn": _first_clean(candidate.get("use_cn"), use_value),
                    "use_label": _ascii_label(english_value or use_value, f"Use category {len(seen)}"),
                    "presence": 1,
                }
            )

    return pd.DataFrame(rows, columns=REPORTED_FUNCTIONAL_PRESENCE_COLUMNS)


def build_epa_echa_combined_rose_data(comptox_candidates_df=None, echa_candidates_df=None):
    frames = []
    if isinstance(comptox_candidates_df, pd.DataFrame) and not comptox_candidates_df.empty:
        frames.append(
            extract_candidate_use_plot_data(
                comptox_candidates_df,
                source_label="EPA",
                source_type="product_category",
                use_key="raw",
            )
        )
    if isinstance(echa_candidates_df, pd.DataFrame) and not echa_candidates_df.empty:
        frames.append(extract_candidate_use_plot_data(echa_candidates_df, source_label="ECHA", use_key="category"))
    frames = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not frames:
        return _empty_plot_data()
    return pd.concat(frames, ignore_index=True)


def generate_use_rose_plot(rose_df, title):
    if rose_df is None or rose_df.empty:
        raise ValueError("No use data is available for the rose plot.")

    rose_df = _prepare_plot_labels(rose_df)
    compounds = list(rose_df["compound"].drop_duplicates())
    use_colors = _build_use_color_map(rose_df["use_cn"].dropna().drop_duplicates())
    n_compounds = len(compounds)
    n_cols = min(3, n_compounds)
    n_rows = math.ceil(n_compounds / n_cols)

    fig_width = max(6.5, 4.2 * n_cols)
    fig_height = max(5.2, 4.4 * n_rows + min(len(use_colors), 18) * 0.08)
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        subplot_kw={"projection": "polar"},
        figsize=(fig_width, fig_height),
    )
    axes = np.asarray([axes]).flatten() if n_compounds == 1 else np.asarray(axes).flatten()
    fig.subplots_adjust(left=0.05, right=0.95, top=0.82, bottom=0.22, wspace=0.28, hspace=0.38)

    for ax_idx, ax in enumerate(axes):
        if ax_idx >= n_compounds:
            ax.axis("off")
            continue

        compound = compounds[ax_idx]
        data = rose_df[rose_df["compound"] == compound].sort_values("rank")
        widths = data["angle_fraction"].astype(float).to_numpy() * 2 * np.pi
        starts = np.concatenate(([0.0], np.cumsum(widths[:-1])))
        colors = [use_colors[value] for value in data["use_cn"]]

        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.bar(
            starts,
            np.ones(len(data)),
            width=widths,
            bottom=0.0,
            color=colors,
            edgecolor="white",
            linewidth=1.0,
            align="edge",
        )
        ax.set_ylim(0, 1.0)
        ax.set_yticklabels([])
        ax.set_xticklabels([])
        ax.grid(False)
        ax.spines["polar"].set_visible(False)
        ax.set_title(
            _ascii_label(compound, f"Compound {ax_idx + 1}"),
            fontsize=12,
            fontweight="bold",
            pad=12,
        )

    fig.suptitle(
        _ascii_label(title, "Use Rose Plot"),
        fontsize=16,
        fontweight="bold",
        y=0.95,
    )
    legend_data = rose_df[["use_cn", "use_label"]].drop_duplicates("use_cn")
    legend_items = [
        Patch(facecolor=use_colors[row.use_cn], label=row.use_label)
        for row in legend_data.sort_values("use_label").itertuples(index=False)
    ]
    legend_cols = min(4, max(1, len(legend_items)))
    fig.legend(
        handles=legend_items,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.04),
        ncol=legend_cols,
        frameon=False,
        fontsize=10,
        title="Use category",
        title_fontsize=11,
        handletextpad=0.5,
        columnspacing=1.2,
    )
    fig.text(
        0.98,
        0.01,
        "Sector angle = use evidence count / total evidence for this compound; radius = 1",
        ha="right",
        va="bottom",
        fontsize=9,
        color="#333333",
    )
    return fig


def generate_use_bar_plot(plot_df, title):
    if plot_df is None or plot_df.empty:
        raise ValueError("No use data is available for the bar plot.")

    plot_df = _prepare_plot_labels(plot_df)
    compounds = list(plot_df["compound"].drop_duplicates())
    use_colors = _build_use_color_map(plot_df["use_cn"].dropna().drop_duplicates())
    n_compounds = len(compounds)
    n_cols = min(2, n_compounds)
    n_rows = math.ceil(n_compounds / n_cols)
    fig_width = max(6.8, 5.0 * n_cols)
    fig_height = max(4.6, 3.5 * n_rows + 0.25 * min(len(plot_df), 20))
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(fig_width, fig_height))
    axes = np.asarray([axes]).flatten() if n_compounds == 1 else np.asarray(axes).flatten()
    fig.subplots_adjust(left=0.12, right=0.96, top=0.84, bottom=0.16, wspace=0.35, hspace=0.42)

    for ax_idx, ax in enumerate(axes):
        if ax_idx >= n_compounds:
            ax.axis("off")
            continue

        compound = compounds[ax_idx]
        data = plot_df[plot_df["compound"] == compound].sort_values("rank")
        values = [
            1.0 if pd.isna(_to_number(value)) else max(float(_to_number(value)), 0.0)
            for value in data["evidence_count"]
        ]
        y_positions = np.arange(len(data))
        colors = [use_colors[value] for value in data["use_cn"]]
        bars = ax.barh(y_positions, values, color=colors, edgecolor="white", linewidth=0.8)
        ax.set_yticks(y_positions)
        ax.set_yticklabels(data["use_label"].tolist(), fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel("Probability or evidence count", fontsize=10)
        ax.set_title(_ascii_label(compound, f"Compound {ax_idx + 1}"), fontsize=12, fontweight="bold")
        ax.grid(axis="x", color="#dddddd", linewidth=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        max_value = max(values) if values else 1.0
        ax.set_xlim(0, max(max_value * 1.15, 1.0))
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_width() + max(max_value, 1.0) * 0.02,
                bar.get_y() + bar.get_height() / 2,
                _format_bar_value(value),
                va="center",
                fontsize=8,
                color="#333333",
            )

    fig.suptitle(_ascii_label(title, "Use Bar Plot"), fontsize=16, fontweight="bold", y=0.95)
    fig.text(
        0.98,
        0.02,
        "Bar length = predicted probability when available, otherwise reported evidence count.",
        ha="right",
        va="bottom",
        fontsize=9,
        color="#333333",
    )
    return fig


def generate_top_predicted_functional_use_lollipop_plot(plot_df, title):
    if plot_df is None or plot_df.empty:
        raise ValueError("No top predicted functional-use data is available.")

    data = plot_df.copy()
    data["compound_label"] = [
        _ascii_label(value, f"Compound {index + 1}")
        for index, value in enumerate(data.get("compound_label", data["compound"]))
    ]
    data["display_label"] = [
        _ascii_label(value, f"Use category {index + 1}")
        for index, value in enumerate(data["display_label"])
    ]
    data["probability"] = data["probability"].map(lambda value: _to_number(value))
    data = data.dropna(subset=["probability"]).sort_values("probability", ascending=True)
    if data.empty:
        raise ValueError("No valid predicted probability is available.")

    fig_height = max(3.8, 0.46 * len(data) + 1.8)
    fig, ax = plt.subplots(figsize=(8.4, fig_height))
    fig.subplots_adjust(left=0.28, right=0.9, top=0.86, bottom=0.16)
    y_positions = np.arange(len(data))
    color_keys = [_first_clean(use_cn, use_label) for use_cn, use_label in zip(data["use_cn"], data["use_label"])]
    use_colors = _build_use_color_map(dict.fromkeys(color_keys))
    colors = [use_colors[value] for value in color_keys]

    ax.axvspan(
        HIGH_CONFIDENCE_PROBABILITY_THRESHOLD,
        1.0,
        color="#f3d58b",
        alpha=0.22,
        linewidth=0,
        zorder=0,
    )
    ax.axvline(
        HIGH_CONFIDENCE_PROBABILITY_THRESHOLD,
        color="#4a4a4a",
        linestyle="--",
        linewidth=1.1,
        zorder=1,
    )
    ax.text(
        0.99,
        0.97,
        "High confidence >= 0.8",
        transform=ax.get_xaxis_transform(),
        ha="right",
        va="top",
        fontsize=8.5,
        color="#4a4a4a",
        clip_on=True,
    )

    for y_position, probability, color in zip(y_positions, data["probability"], colors):
        ax.plot([0, float(probability)], [y_position, y_position], color=color, linewidth=2.2)
    ax.scatter(data["probability"].astype(float), y_positions, s=72, color=colors, edgecolor="white", linewidth=0.9, zorder=3)

    label_offset = 0.025
    for y_position, probability, label in zip(y_positions, data["probability"], data["display_label"]):
        ax.text(
            min(float(probability) + label_offset, 1.02),
            y_position,
            label,
            va="center",
            fontsize=9,
            color="#333333",
        )

    ax.set_yticks(y_positions)
    ax.set_yticklabels(data["compound_label"].tolist(), fontsize=9)
    ax.set_ylim(-0.6, len(data) + 0.6)
    ax.set_xlim(0, 1.08)
    ax.set_xlabel("Highest predicted probability", fontsize=10)
    ax.set_title(_ascii_label(title, "Top Predicted Functional Use"), fontsize=14, fontweight="bold")
    ax.grid(axis="x", color="#dddddd", linewidth=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return fig


def generate_reported_functional_use_presence_plot(plot_df, title):
    if plot_df is None or plot_df.empty:
        raise ValueError("No reported functional-use evidence is available.")

    data = plot_df.copy()
    data["compound_label"] = [
        _ascii_label(value, f"Compound {index + 1}")
        for index, value in enumerate(data.get("compound_label", data["compound"]))
    ]
    data["use_label"] = [
        _ascii_label(value, f"Use category {index + 1}")
        for index, value in enumerate(data["use_label"])
    ]
    compounds = list(dict.fromkeys(data["compound_label"].tolist()))
    uses = sorted(dict.fromkeys(data["use_label"].tolist()))
    compound_positions = {compound: index for index, compound in enumerate(compounds)}
    use_positions = {use: index for index, use in enumerate(uses)}
    x_values = [use_positions[value] for value in data["use_label"]]
    y_values = [compound_positions[value] for value in data["compound_label"]]
    color_map = _build_use_color_map(uses)
    colors = [color_map[value] for value in data["use_label"]]

    fig_width = max(7.2, 0.55 * len(uses) + 3.0)
    fig_height = max(4.2, 0.38 * len(compounds) + 2.4)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    fig.subplots_adjust(left=0.24, right=0.96, top=0.84, bottom=0.42)
    ax.scatter(x_values, y_values, s=82, color=colors, edgecolor="white", linewidth=0.9)
    ax.set_xticks(range(len(uses)))
    ax.set_xticklabels(uses, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(len(compounds)))
    ax.set_yticklabels(compounds, fontsize=9)
    ax.set_xlabel("Reported functional use", fontsize=10)
    ax.set_ylabel("Compound", fontsize=10)
    ax.set_title(_ascii_label(title, "Reported Functional Use Evidence"), fontsize=14, fontweight="bold")
    ax.set_xlim(-0.6, max(len(uses) - 0.4, 0.6))
    ax.set_ylim(-0.6, max(len(compounds) - 0.4, 0.6))
    ax.invert_yaxis()
    ax.grid(color="#e5e5e5", linewidth=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return fig


def generate_combined_use_rose_plot(rose_df, title):
    """Render EPA and ECHA as equal left/right semicircles per compound."""
    if rose_df is None or rose_df.empty:
        raise ValueError("No use data is available for the combined use plot.")

    rose_df = _prepare_plot_labels(rose_df)
    compounds = list(rose_df["compound"].drop_duplicates())
    use_colors = _build_use_color_map(rose_df["use_cn"].dropna().drop_duplicates())
    n_compounds = len(compounds)
    n_cols = min(3, n_compounds)
    n_rows = math.ceil(n_compounds / n_cols)
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        subplot_kw={"projection": "polar"},
        figsize=(max(6.5, 4.2 * n_cols), max(5.2, 4.4 * n_rows + min(len(use_colors), 18) * 0.08)),
    )
    axes = np.asarray([axes]).flatten() if n_compounds == 1 else np.asarray(axes).flatten()

    source_config = {"EPA": (np.pi / 2, "EPA", "No EPA data"), "ECHA": (-np.pi / 2, "ECHA", "No ECHA data")}
    for index, ax in enumerate(axes):
        if index >= n_compounds:
            ax.axis("off")
            continue
        compound = compounds[index]
        compound_data = rose_df[rose_df["compound"] == compound]
        ax.set_theta_zero_location("E")
        ax.set_theta_direction(-1)
        ax.set_ylim(0, 1.15)
        ax.set_yticklabels([])
        ax.set_xticklabels([])
        ax.grid(False)
        ax.spines["polar"].set_visible(False)
        ax.plot([np.pi / 2, np.pi / 2], [0, 1], color="#333333", linewidth=0.8)
        ax.plot([3 * np.pi / 2, 3 * np.pi / 2], [0, 1], color="#333333", linewidth=0.8)
        for source, (start, label, empty_label) in source_config.items():
            data = compound_data[compound_data["source"] == source].sort_values("rank")
            if data.empty:
                ax.text(start + np.pi / 2, 0.5, empty_label, ha="center", va="center", fontsize=9, color="#666666")
                continue
            widths = data["angle_fraction"].astype(float).to_numpy() * np.pi
            starts = start + np.concatenate(([0.0], np.cumsum(widths[:-1])))
            ax.bar(starts, np.ones(len(data)), width=widths, bottom=0.0,
                   color=[use_colors[value] for value in data["use_cn"]], edgecolor="white",
                   linewidth=1.0, align="edge")
            ax.text(start + np.pi / 2, 1.08, label, ha="center", va="center", fontsize=10, fontweight="bold")
        ax.set_title(_ascii_label(compound, f"Compound {index + 1}"), fontsize=12, fontweight="bold", pad=14)

    fig.subplots_adjust(left=0.05, right=0.95, top=0.82, bottom=0.22, wspace=0.28, hspace=0.38)
    fig.suptitle(_ascii_label(title, "Combined Use Plot"), fontsize=16, fontweight="bold", y=0.95)
    legend_data = rose_df[["use_cn", "use_label"]].drop_duplicates("use_cn")
    handles = [Patch(facecolor=use_colors[row.use_cn], label=row.use_label) for row in legend_data.sort_values("use_label").itertuples(index=False)]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.04), ncol=min(4, max(1, len(handles))), frameon=False, fontsize=10, title="Use category", title_fontsize=11)
    fig.text(0.98, 0.01, "Left = EPA; right = ECHA. Each half is normalized within its source; values are not compared across sources.", ha="right", va="bottom", fontsize=9, color="#333333")
    return fig


def figure_to_png_bytes(fig):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=300, bbox_inches="tight", facecolor="white")
    buffer.seek(0)
    return buffer


def figure_to_pdf_bytes(fig):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="pdf", dpi=300, bbox_inches="tight", facecolor="white")
    buffer.seek(0)
    return buffer


def _empty_plot_data():
    return pd.DataFrame(columns=PLOT_DATA_COLUMNS)


def _empty_top_predicted_functional_data():
    return pd.DataFrame(columns=TOP_PREDICTED_FUNCTIONAL_COLUMNS)


def _empty_reported_functional_presence_data():
    return pd.DataFrame(columns=REPORTED_FUNCTIONAL_PRESENCE_COLUMNS)


def _append_angle_rows(rows, source_label, compound, compound_label, entries):
    valid_total = sum(item["evidence_count"] for item in entries if item.get("valid_evidence"))
    if valid_total > 0:
        for rank, item in enumerate(entries, start=1):
            evidence = float(item["evidence_count"]) if item.get("valid_evidence") else 0.0
            rows.append(
                {
                    "source": source_label,
                    "compound": compound,
                    "compound_label": compound_label,
                    "rank": rank,
                    "use_cn": item["use_cn"],
                    "use_label": item["use_label"],
                    "evidence_count": int(evidence) if evidence.is_integer() else evidence,
                    "angle_fraction": evidence / valid_total,
                    "angle_basis": "evidence_count",
                }
            )
        return

    equal_fraction = 1.0 / len(entries)
    for rank, item in enumerate(entries, start=1):
        rows.append(
            {
                "source": source_label,
                "compound": compound,
                "compound_label": compound_label,
                "rank": rank,
                "use_cn": item["use_cn"],
                "use_label": item["use_label"],
                "evidence_count": pd.NA,
                "angle_fraction": equal_fraction,
                "angle_basis": "equal_fallback",
            }
        )


def _candidate_use_values(candidate, use_key):
    raw = _first_clean(candidate.get("raw_use"), candidate.get("use_en"), candidate.get("harmonized_use"), candidate.get("reported_use"))
    category = _first_clean(candidate.get("use_cn"), raw)
    if use_key == "raw":
        return raw or category, raw or category
    return category, raw or category


def _functional_source_bucket(candidate):
    source = _clean_text(candidate.get("functional_use_source")).lower()
    if "pred" in source:
        return "predicted"
    if "report" in source or "collect" in source:
        return "reported"
    probability = _to_number(candidate.get("probability"))
    return "predicted" if not pd.isna(probability) else "reported"


def _functional_candidate_match_keys(candidate):
    values = []
    for field in ("raw_use", "use_en", "harmonized_use", "reported_use", "use_cn"):
        text = _clean_text(candidate.get(field))
        if not text:
            continue
        values.extend(part.strip() for part in text.split("|") if part.strip())

    keys = set()
    for value in values:
        normalized = _normalize_label_key(value)
        if normalized:
            keys.add(normalized)
    return keys


def _first_clean(*values):
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ""


def _normalize_label_key(value):
    text = _clean_text(value).lower()
    text = re.sub(r"[_\-/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find_use_ranks(columns, use_prefix="用途"):
    ranks = []
    pattern = re.compile(rf"{re.escape(use_prefix)}(\d+)")
    for col in columns:
        match = pattern.fullmatch(str(col))
        if match:
            ranks.append(int(match.group(1)))
    return sorted(ranks)


def _prepare_plot_labels(rose_df):
    """Add ASCII-only legend labels for callers that pass legacy rose data."""
    prepared = rose_df.copy()
    if "use_label" in prepared.columns:
        prepared["use_label"] = [
            _ascii_label(value, f"Use category {rank}")
            for value, rank in zip(prepared["use_label"], prepared["rank"])
        ]
        return prepared

    prepared["use_label"] = [
        _ascii_label(value, f"Use category {rank}")
        for value, rank in zip(prepared["use_cn"], prepared["rank"])
    ]
    return prepared


def _ascii_label(value, fallback):
    """Return a portable label that does not require a host-provided CJK font."""
    text = _clean_text(value).split("|", maxsplit=1)[0].strip()
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip()
    return ascii_text or fallback


def _build_use_color_map(use_labels):
    color_map = {}
    for idx, label in enumerate(use_labels):
        if idx < len(USE_COLOR_PALETTE):
            color_map[label] = USE_COLOR_PALETTE[idx]
        else:
            rgba = plt.get_cmap("tab20")(idx % 20)
            color_map[label] = "#{:02x}{:02x}{:02x}".format(
                int(rgba[0] * 255),
                int(rgba[1] * 255),
                int(rgba[2] * 255),
            )
    return color_map


def _to_number(value):
    try:
        parsed = pd.to_numeric(value, errors="coerce")
    except Exception:
        return pd.NA
    if pd.isna(parsed):
        return pd.NA
    return float(parsed)


def _format_bar_value(value):
    number = _to_number(value)
    if pd.isna(number):
        return ""
    return str(int(number)) if float(number).is_integer() else f"{number:.3g}"


def _clean_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text
