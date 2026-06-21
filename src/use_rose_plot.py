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


def extract_use_rose_data(summary_df, source_label):
    """Convert Top-use summary rows into long-form rose plot data."""
    if summary_df is None or summary_df.empty:
        return pd.DataFrame(
            columns=[
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
        )

    use_ranks = _find_use_ranks(summary_df.columns)
    rows = []
    for compound_index, (_, row) in enumerate(summary_df.iterrows(), start=1):
        compound = _clean_text(row.get("compound")) or "未命名化合物"
        compound_label = _ascii_label(compound, f"Compound {compound_index}")
        entries = []
        for rank in use_ranks:
            use_cn = _clean_text(row.get(f"用途{rank}"))
            if not use_cn:
                continue
            use_en = _clean_text(row.get(f"用途{rank}_英文证据"))
            evidence = _to_number(row.get(f"用途{rank}_证据数量"))
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
        "Sector angle = use evidence count / total Top-use evidence for this compound; radius = 1",
        ha="right",
        va="bottom",
        fontsize=9,
        color="#333333",
    )
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


def _find_use_ranks(columns):
    ranks = []
    for col in columns:
        match = re.fullmatch(r"用途(\d+)", str(col))
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
