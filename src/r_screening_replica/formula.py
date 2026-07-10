from __future__ import annotations

import math
import re
from collections import OrderedDict
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_ELEMENTS = ["C", "H", "O", "N", "S", "F", "Cl", "Br", "I"]
FORMULA_RE = re.compile(r"([A-Z][a-z]*)(-?\d*)")


def parse_formula(formula: Any) -> dict[str, float]:
    if formula is None or (isinstance(formula, float) and math.isnan(formula)):
        return {}
    text = re.sub(r"\s+", "", str(formula).strip())
    if not text:
        return {}

    counts: dict[str, float] = {}
    for element, count_text in FORMULA_RE.findall(text):
        if count_text == "":
            count = 1.0
        elif count_text == "-":
            count = -1.0
        elif count_text.startswith("-"):
            count = -float(count_text[1:])
        else:
            count = float(count_text)
        counts[element] = counts.get(element, 0.0) + count
    return counts


def calculate_ratios_and_dbe(formulas: pd.Series | list[Any]) -> pd.DataFrame:
    formula_list = list(formulas)
    parsed = [parse_formula(value) for value in formula_list]
    elements = _ordered_elements(parsed)

    rows: list[dict[str, Any]] = []
    for formula, atoms in zip(formula_list, parsed):
        row: dict[str, Any] = {"Formula": formula}
        for element in elements:
            row[f"{element}_count"] = _clean_count(atoms.get(element, 0.0))

        carbon = float(atoms.get("C", 0.0))
        hydrogen = float(atoms.get("H", 0.0))
        nitrogen = float(atoms.get("N", 0.0))
        sulfur = float(atoms.get("S", 0.0))
        oxygen = float(atoms.get("O", 0.0))
        halogen = sum(float(atoms.get(element, 0.0)) for element in ["F", "Cl", "Br", "I"])

        if carbon > 0:
            row["H.C"] = hydrogen / carbon
            row["O.C"] = oxygen / carbon
            row["N.C"] = nitrogen / carbon
            row["S.C"] = sulfur / carbon
            row["valid_C"] = True
            row["DBE"] = 1 + carbon - (hydrogen + halogen) / 2 + nitrogen / 2
        else:
            row["H.C"] = np.nan
            row["O.C"] = np.nan
            row["N.C"] = np.nan
            row["S.C"] = np.nan
            row["valid_C"] = False
            row["DBE"] = np.nan
        row["DBE_round"] = round(row["DBE"], 2) if pd.notna(row["DBE"]) else np.nan
        rows.append(row)

    return pd.DataFrame(rows)


def _ordered_elements(parsed: list[dict[str, float]]) -> list[str]:
    elements: OrderedDict[str, None] = OrderedDict()
    for atoms in parsed:
        for element in atoms:
            elements[element] = None
    for element in DEFAULT_ELEMENTS:
        elements[element] = None
    return list(elements.keys())


def _clean_count(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return value
