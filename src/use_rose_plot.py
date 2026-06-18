import io
import math
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch


plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial"]
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
                "rank",
                "use_cn",
                "evidence_count",
                "angle_fraction",
                "angle_basis",
            ]
        )

    use_ranks = _find_use_ranks(summary_df.columns)
    rows = []
    for _, row in summary_df.iterrows():
        compound = _clean_text(row.get("compound")) or "未命名化合物"
        entries = []
        for rank in use_ranks:
            use_cn = _clean_text(row.get(f"用途{rank}"))
            if not use_cn:
                continue
            evidence = _to_number(row.get(f"用途{rank}_证据数量"))
            entries.append({"rank": rank, "use_cn": use_cn, "evidence_count": evidence})

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
                        "rank": item["rank"],
                        "use_cn": item["use_cn"],
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
                        "rank": item["rank"],
                        "use_cn": item["use_cn"],
                        "evidence_count": item["evidence_count"],
                        "angle_fraction": equal_fraction,
                        "angle_basis": "equal_fallback",
                    }
                )

    return pd.DataFrame(rows)


def generate_use_rose_plot(rose_df, title):
    if rose_df is None or rose_df.empty:
        raise ValueError("没有可用于绘制用途风玫瑰图的数据。")

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
        ax.set_title(str(compound), fontsize=12, fontweight="bold", pad=12)

    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.95)
    legend_items = [
        Patch(facecolor=color, label=label)
        for label, color in sorted(use_colors.items(), key=lambda item: item[0])
    ]
    legend_cols = min(4, max(1, len(legend_items)))
    fig.legend(
        handles=legend_items,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.04),
        ncol=legend_cols,
        frameon=False,
        fontsize=10,
        title="用途类别",
        title_fontsize=11,
        handletextpad=0.5,
        columnspacing=1.2,
    )
    fig.text(
        0.98,
        0.01,
        "扇区角度 = 该用途证据数量 / 当前化合物 Top 用途证据总量；半径固定为 1",
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
