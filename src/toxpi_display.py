from __future__ import annotations

import pandas as pd


TOXPI_SCORE_FORMAT = "%.4f"
TOXPI_SCORE_COLUMNS = frozenset({"initial_toxpi", "toxpi"})


def format_toxpi_score(value) -> str:
    return f"{float(value):.4f}"


def toxpi_score_columns(frame: pd.DataFrame) -> tuple[str, ...]:
    return tuple(
        column for column in frame.columns if column in TOXPI_SCORE_COLUMNS
    )


def toxpi_dataframe_column_config(
    frame: pd.DataFrame,
    number_column_factory,
) -> dict:
    return {
        column: number_column_factory(format=TOXPI_SCORE_FORMAT)
        for column in toxpi_score_columns(frame)
    }
