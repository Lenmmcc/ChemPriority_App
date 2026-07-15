from __future__ import annotations

import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from plotnine import (
    aes,
    element_blank,
    element_line,
    element_rect,
    element_text,
    geom_boxplot,
    geom_errorbar,
    geom_jitter,
    ggplot,
    guide_legend,
    labs,
    scale_color_manual,
    scale_fill_manual,
    theme,
    theme_bw,
)

from src.plot_style import (
    PLOT_FONT_FAMILY,
    apply_figure_font,
    configure_plot_style,
)

from .classification import CATEGORY_ORDER, DISPLAY_CATEGORY_LABELS
from .schema import ScreeningAxisRanges


PLOT_STYLE_WARNINGS = configure_plot_style()


SCIENTIFIC_COLORS = {
    "CH": "#D55E00",
    "CHO": "#E69F00",
    "CHON_Group": "#009E73",
    "CHOX_Group": "#CC79A7",
    "ELSE": "#0072B2",
}
DONUT_PERCENT_LABEL_FONT_SIZE = 10
DONUT_CENTER_FONT_SIZE = 18
BUBBLE_SIZES = {
    "Level 1": 3,
    "Level 2": 5,
    "Level 3": 7,
    "Level 4": 9,
    "Level 5+": 11,
}
AREA_LEVEL_LABELS = {
    "Level 1": "1.00e+05",
    "Level 2": "1.00e+06",
    "Level 3": "1.00e+07",
    "Level 4": "1.00e+08",
    "Level 5+": ">1.00e+09",
}
VK_REGIONS = [
    ("Lipids-like", 0.0, 0.3, 1.5, 2.0, 0.15, 1.75),
    ("Aliphatic/Peptides-like", 0.3, 0.7, 1.5, 2.2, 0.5, 1.85),
    ("Unsaturated hydrocarbons", 0.0, 0.1, 0.7, 1.5, 0.05, 1.1),
    ("CRAMs-like", 0.1, 0.7, 0.7, 1.5, 0.4, 1.1),
    ("Aromatic structures", 0.0, 0.7, 0.3, 0.7, 0.35, 0.5),
    ("Carbohydrates-like", 0.7, 1.0, 1.5, 2.5, 0.85, 2.0),
    ("Highly Oxygenated Compounds", 0.7, 1.0, 0.0, 1.5, 0.85, 0.75),
]


def generate_all_figures(
    category_summary: pd.DataFrame,
    dbe_table: pd.DataFrame,
    compound_categories: pd.DataFrame,
    sample_peak_area_long: pd.DataFrame,
    output_dir: Path,
    axis_ranges: ScreeningAxisRanges,
) -> tuple[dict[str, dict[str, Path]], list[str]]:
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    warnings_list = _font_warnings()
    figure_paths = {
        "category_percent_donut_with_total": save_category_donut(category_summary, figures_dir),
        "compound_bubble_plot": save_compound_bubble_plot(dbe_table, compound_categories, figures_dir, axis_ranges),
        "VanKrevelen": save_van_krevelen_plot(compound_categories, figures_dir, axis_ranges),
        "boxplot_log_transformed": save_boxplot_log_transformed(sample_peak_area_long, compound_categories, figures_dir),
    }
    return figure_paths, warnings_list


def save_category_donut(category_summary: pd.DataFrame, output_dir: Path) -> dict[str, Path]:
    paths = {
        "png": output_dir / "category_percent_donut_with_total.png",
        "pdf": output_dir / "category_percent_donut_with_total.pdf",
    }
    for fmt, path in paths.items():
        width, height = (10, 8) if fmt == "png" else (12, 8)
        fig, ax = plt.subplots(figsize=(width, height), facecolor="white")
        _draw_category_donut(ax, category_summary)
        apply_figure_font(fig)
        fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        plt.close(fig)
    return paths


def save_compound_bubble_plot(
    dbe_table: pd.DataFrame,
    compound_categories: pd.DataFrame,
    output_dir: Path,
    axis_ranges: ScreeningAxisRanges | None = None,
) -> dict[str, Path]:
    axis_ranges = axis_ranges or ScreeningAxisRanges()
    data = _bubble_data(dbe_table, compound_categories)
    paths = {
        "png": output_dir / "compound_bubble_plot.png",
        "pdf": output_dir / "compound_bubble_plot.pdf",
    }
    for path in paths.values():
        fig, ax = plt.subplots(figsize=(12, 8), facecolor="white")
        _draw_compound_bubble(ax, data, axis_ranges)
        apply_figure_font(fig)
        fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        plt.close(fig)
    return paths


def save_van_krevelen_plot(
    compound_categories: pd.DataFrame,
    output_dir: Path,
    axis_ranges: ScreeningAxisRanges | None = None,
) -> dict[str, Path]:
    axis_ranges = axis_ranges or ScreeningAxisRanges()
    data = compound_categories.copy()
    data["o_c"] = pd.to_numeric(data["O.C"], errors="coerce")
    data["h_c"] = pd.to_numeric(data["H.C"], errors="coerce")
    data = data.dropna(subset=["o_c", "h_c", "Category"])
    paths = {
        "png": output_dir / "VanKrevelen.png",
        "pdf": output_dir / "VanKrevelen.pdf",
    }
    for path in paths.values():
        fig, ax = plt.subplots(figsize=(12, 8), facecolor="white")
        _draw_van_krevelen(ax, data, axis_ranges)
        apply_figure_font(fig)
        fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        plt.close(fig)
    return paths


def save_boxplot_log_transformed(
    sample_peak_area_long: pd.DataFrame,
    compound_categories: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Path]:
    category_lookup = compound_categories[["Formula", "Category"]].drop_duplicates("Formula")
    plot_df = sample_peak_area_long.merge(
        category_lookup,
        left_on="formula",
        right_on="Formula",
        how="left",
    )
    plot_df = plot_df.dropna(subset=["log_concentration", "Category"]).copy()
    if plot_df.empty:
        plot_df = pd.DataFrame({
            "sample_id": ["sample"],
            "log_concentration": [0.0],
            "Category": ["ELSE"],
        })

    summary = _boxplot_summary(plot_df)
    plot_df["sample_id"] = pd.Categorical(plot_df["sample_id"], categories=list(summary["sample_id"]), ordered=True)
    summary["sample_id"] = pd.Categorical(summary["sample_id"], categories=list(summary["sample_id"]), ordered=True)

    p = (
        ggplot(plot_df, aes(x="sample_id", y="log_concentration"))
        + geom_boxplot(
            aes(fill="sample_id"),
            width=0.6,
            alpha=0.7,
            outlier_shape="",
            color="black",
            size=0.5,
            show_legend=False,
        )
        + geom_errorbar(
            summary,
            aes(x="sample_id", ymin="lower_whisker", ymax="upper_whisker"),
            width=0.2,
            color="black",
            size=0.5,
            inherit_aes=False,
        )
        + geom_errorbar(
            summary,
            aes(x="sample_id", ymin="median_value", ymax="median_value"),
            width=0.4,
            color="black",
            size=0.8,
            inherit_aes=False,
        )
        + geom_jitter(aes(color="Category"), width=0.1, size=1, alpha=0.7, show_legend=True)
        + scale_fill_manual(values={sample: "white" for sample in summary["sample_id"].astype(str)})
        + scale_color_manual(
            values=SCIENTIFIC_COLORS,
            name="Compound Category",
            labels=DISPLAY_CATEGORY_LABELS,
            guide=guide_legend(override_aes={"shape": "s", "size": 5, "alpha": 1}),
        )
        + labs(x="Sample Group", y="Log10(Peak Area)")
        + theme_bw()
        + theme(
            text=element_text(family=PLOT_FONT_FAMILY),
            panel_border=element_rect(color="black", size=0.5),
            axis_ticks=element_line(color="black", size=0.2),
            axis_line=element_line(color="black", size=0.2),
            panel_grid_major=element_blank(),
            panel_grid_minor=element_blank(),
            axis_text_x=element_text(
                color="black",
                size=10,
                rotation=45,
                ha="right",
                family=PLOT_FONT_FAMILY,
            ),
            axis_text_y=element_text(color="black", size=10),
            axis_title=element_text(color="black", size=12),
            legend_position="right",
            legend_key=element_rect(fill="white", color="white"),
            legend_text=element_text(size=10),
            legend_title=element_text(size=12, weight="bold"),
            plot_background=element_rect(fill="white", color="white"),
            panel_background=element_rect(fill="white", color="white"),
        )
    )
    paths = {
        "png": output_dir / "boxplot_log_transformed.png",
        "pdf": output_dir / "boxplot_log_transformed.pdf",
    }
    p.save(paths["png"], width=10, height=6, dpi=300, verbose=False)
    p.save(paths["pdf"], width=10, height=6, dpi=300, verbose=False)
    return paths


def _draw_category_donut(ax: plt.Axes, category_summary: pd.DataFrame) -> None:
    summary = category_summary.copy()
    summary = summary[summary["Count"] > 0]
    colors = [SCIENTIFIC_COLORS.get(str(category), "#999999") for category in summary["Category"].astype(str)]
    wedges, _ = ax.pie(
        summary["Count"],
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.375, "edgecolor": "white", "linewidth": 0.5},
    )
    for wedge, percentage in zip(wedges, summary["Percentage"]):
        angle = math.radians((wedge.theta1 + wedge.theta2) / 2)
        label = f"{float(percentage):.1f}%"
        ax.text(
            0.84 * math.cos(angle),
            0.84 * math.sin(angle),
            label,
            ha="center",
            va="center",
            color="black",
            fontsize=DONUT_PERCENT_LABEL_FONT_SIZE,
            fontweight="bold",
        )
    total_count = int(summary["Count"].sum())
    ax.text(0, 0, f"Total number\n {total_count}", ha="center", va="center", fontsize=DONUT_CENTER_FONT_SIZE, fontweight="bold", color="black", linespacing=0.9)
    ax.set_title("Compound Category Percentage Distribution", fontsize=16, fontweight="bold", pad=10)
    legend_handles = [
        Patch(facecolor=SCIENTIFIC_COLORS[category], label=DISPLAY_CATEGORY_LABELS[category])
        for category in CATEGORY_ORDER
        if category in set(summary["Category"].astype(str))
    ]
    legend_kwargs = {
        "title": "Compound Category",
        "loc": "center left",
        "bbox_to_anchor": (1.0, 0.5),
        "frameon": False,
        "fontsize": 10,
        "title_fontsize": 12,
    }
    ax.legend(handles=legend_handles, **legend_kwargs)
    ax.set_aspect("equal")
    ax.axis("off")


def _bubble_data(dbe_table: pd.DataFrame, compound_categories: pd.DataFrame) -> pd.DataFrame:
    data = dbe_table.copy()
    data = data.rename(columns={"name": "Formula", "carbon_count": "C_count"})
    category_map = compound_categories.drop_duplicates("Formula").set_index("Formula")["Category"].to_dict()
    data["Category"] = data["Formula"].map(category_map)
    data["carbon_count"] = pd.to_numeric(data.get("C_count"), errors="coerce")
    data["DBE"] = pd.to_numeric(data["DBE"], errors="coerce")
    data["peak_area"] = pd.to_numeric(data["peak_area"], errors="coerce")
    data = data.dropna(subset=["carbon_count", "DBE", "peak_area", "Category"])
    data = data[data["peak_area"] >= 1e4].copy()
    data["area_level"] = pd.cut(
        data["peak_area"],
        bins=[1e5, 1e6, 1e7, 1e8, 1e9, np.inf],
        labels=["Level 1", "Level 2", "Level 3", "Level 4", "Level 5+"],
        right=False,
    )
    data = data.dropna(subset=["area_level"])
    return data


def _draw_compound_bubble(ax: plt.Axes, data: pd.DataFrame, axis_ranges: ScreeningAxisRanges) -> None:
    ax.figure.patch.set_facecolor("white")
    ax.set_facecolor("white")
    for category in CATEGORY_ORDER:
        subset = data[data["Category"] == category]
        if subset.empty:
            continue
        sizes = subset["area_level"].astype(str).map(BUBBLE_SIZES).astype(float) ** 2 * 6
        ax.scatter(
            subset["carbon_count"],
            subset["DBE"],
            s=sizes,
            c=SCIENTIFIC_COLORS[category],
            alpha=0.8,
            marker="o",
            edgecolors="none",
            label=DISPLAY_CATEGORY_LABELS[category],
        )
    ax.set_xlim(*axis_ranges.dbe_xlim)
    ax.set_ylim(*axis_ranges.dbe_ylim)
    ax.grid(False)
    ax.set_title("DBE for all compounds", fontsize=16, fontweight="bold", loc="center", pad=28)
    ax.text(0.5, 1.01, "classified by elemental composition", ha="center", va="bottom", transform=ax.transAxes, fontsize=12)
    ax.set_xlabel("Carbon number", fontsize=16, fontweight="bold")
    ax.set_ylabel("DBE value", fontsize=16, fontweight="bold")
    ax.tick_params(axis="both", labelsize=14, colors="black", width=0.4, length=4)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.2)
    color_handles = [
        Line2D([0], [0], marker="s", linestyle="", markerfacecolor=SCIENTIFIC_COLORS[category], markeredgecolor="white", markersize=8, label=DISPLAY_CATEGORY_LABELS[category])
        for category in CATEGORY_ORDER
        if category in set(data["Category"])
    ]
    size_handles = [
        Line2D([0], [0], marker="o", linestyle="", color="gray", markersize=size, label=AREA_LEVEL_LABELS[level])
        for level, size in BUBBLE_SIZES.items()
        if level in set(data["area_level"].astype(str))
    ]
    first = ax.legend(handles=size_handles, title="Peak area", loc="upper left", bbox_to_anchor=(1.02, 1.0), frameon=False)
    ax.add_artist(first)
    ax.legend(handles=color_handles, title="Compound category", loc="upper left", bbox_to_anchor=(1.02, 0.55), frameon=False)


def _draw_van_krevelen(ax: plt.Axes, data: pd.DataFrame, axis_ranges: ScreeningAxisRanges) -> None:
    font_family = PLOT_FONT_FAMILY
    for category in CATEGORY_ORDER:
        subset = data[data["Category"] == category]
        if subset.empty:
            continue
        ax.scatter(
            subset["o_c"],
            subset["h_c"],
            s=20,
            c=SCIENTIFIC_COLORS[category],
            edgecolors="black",
            linewidths=0.5,
            alpha=0.8,
            marker="o",
            label=DISPLAY_CATEGORY_LABELS[category],
        )
    for label, xmin, xmax, ymin, ymax, label_x, label_y in VK_REGIONS:
        ax.add_patch(Rectangle((xmin, ymin), xmax - xmin, ymax - ymin, fill=False, edgecolor="#333333", linestyle="--", linewidth=1.0))
        ax.text(label_x, label_y, label, ha="center", va="center", fontsize=12, fontweight="bold", color="#333333", family=font_family)
    ax.set_xticks(np.arange(0, 1.21, 0.2))
    ax.set_yticks(np.arange(0, 2.61, 0.5))
    ax.set_xlim(*axis_ranges.vk_xlim)
    ax.set_ylim(*axis_ranges.vk_ylim)
    ax.set_xlabel("O/C Ratio", fontsize=16, fontweight="bold", color="black", family=font_family)
    ax.set_ylabel("H/C Ratio", fontsize=16, fontweight="bold", color="black", family=font_family)
    ax.set_title("Van Krevelen Diagram", fontsize=18, fontweight="bold", color="black", family=font_family)
    ax.tick_params(axis="both", colors="black", labelsize=14, width=0.5, length=4)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(1.5)
    ax.grid(False)
    ax.legend(title="Compound Types", loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=11, title_fontsize=12)


def _boxplot_summary(plot_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sample, group in plot_df.groupby("sample_id", sort=True):
        values = group["log_concentration"].dropna().astype(float)
        q1 = values.quantile(0.25)
        q3 = values.quantile(0.75)
        iqr = q3 - q1
        rows.append({
            "sample_id": sample,
            "lower_whisker": max(values.min(), q1 - 1.5 * iqr),
            "upper_whisker": min(values.max(), q3 + 1.5 * iqr),
            "median_value": values.median(),
        })
    return pd.DataFrame(rows)


def _font_warnings() -> list[str]:
    return list(PLOT_STYLE_WARNINGS)
