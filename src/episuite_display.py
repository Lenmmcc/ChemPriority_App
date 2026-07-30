from __future__ import annotations

import pandas as pd


PROPERTY_DISPLAY_SPECS = {
    "koawin_log_kow": ("logKOW（KOAWIN估算）", "%.6f"),
    "koawin_kow": ("KOW（KOAWIN估算）", "%.6e"),
    "koawin_log_koa": ("logKOA（KOAWIN估算）", "%.6f"),
    "koawin_koa": ("KOA（KOAWIN估算）", "%.6e"),
    "koawin_log_kaw": ("logKAW（KOAWIN估算）", "%.6f"),
    "koawin_kaw": ("KAW（KOAWIN估算）", "%.6e"),
    "tpsa_rdkit_a2": ("TPSA（Å²，RDKit）", "%.6f"),
    "mr_rdkit_cm3_mol": ("MR（cm³/mol，RDKit）", "%.6f"),
}


def episuite_property_column_config(
    frame: pd.DataFrame,
    number_column_factory,
) -> dict:
    return {
        column: number_column_factory(label=label, format=number_format)
        for column, (label, number_format) in PROPERTY_DISPLAY_SPECS.items()
        if column in frame.columns
    }


def episuite_property_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    labels = {
        column: label
        for column, (label, _) in PROPERTY_DISPLAY_SPECS.items()
        if column in frame.columns
    }
    return frame.rename(columns=labels)
