from __future__ import annotations

import pandas as pd


DISPLAY_CATEGORY_LABELS = {
    "CH": "CH",
    "CHO": "CHO",
    "CHON_Group": "CH(O)N",
    "CHOX_Group": "CH(O)X",
    "ELSE": "ELSE",
}

CATEGORY_ORDER = ["CH", "CHO", "CHON_Group", "CHOX_Group", "ELSE"]


def classify_compounds(ratio_df: pd.DataFrame) -> pd.DataFrame:
    classified = ratio_df[ratio_df["valid_C"].astype(bool)].copy()
    classified["Category"] = classified.apply(_classify_row, axis=1)
    order_map = {category: idx for idx, category in enumerate(CATEGORY_ORDER)}
    classified["_category_order"] = classified["Category"].map(order_map).fillna(99)
    classified = classified.sort_values(["_category_order"]).drop(columns=["_category_order"])
    return classified.reset_index(drop=True)


def summarize_categories(compound_categories: pd.DataFrame) -> pd.DataFrame:
    summary = (
        compound_categories.groupby("Category", as_index=False)
        .size()
        .rename(columns={"size": "Count"})
    )
    present_order = [category for category in CATEGORY_ORDER if category in set(summary["Category"])]
    summary["Category"] = pd.Categorical(summary["Category"], categories=present_order, ordered=True)
    summary = summary.sort_values("Category").reset_index(drop=True)
    summary["Display_Category"] = summary["Category"].astype(str).map(DISPLAY_CATEGORY_LABELS)
    total = summary["Count"].sum()
    summary["Percentage"] = summary["Count"] / total * 100 if total else 0.0
    summary["ymax"] = summary["Percentage"].cumsum()
    summary["ymin"] = summary["ymax"] - summary["Percentage"]
    summary["label_position"] = (summary["ymax"] + summary["ymin"]) / 2
    summary["label"] = summary.apply(lambda row: f"{row['Count']}\n({row['Percentage']:.1f}%)", axis=1)
    return summary


def _classify_row(row: pd.Series) -> str:
    oxygen = _count(row, "O_count")
    nitrogen = _count(row, "N_count")
    sulfur = _count(row, "S_count")
    halogen = sum(_count(row, col) for col in ["F_count", "Cl_count", "Br_count", "I_count"])

    if oxygen == 0 and nitrogen == 0 and sulfur == 0 and halogen == 0:
        return "CH"
    if oxygen > 0 and nitrogen == 0 and sulfur == 0 and halogen == 0:
        return "CHO"
    if nitrogen > 0 and sulfur == 0 and halogen == 0:
        return "CHON_Group"
    if halogen > 0 and nitrogen == 0 and sulfur == 0:
        return "CHOX_Group"
    return "ELSE"


def _count(row: pd.Series, column: str) -> float:
    value = row[column] if column in row.index else 0
    return 0.0 if pd.isna(value) else float(value)
