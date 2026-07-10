from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.episuite_io import DEFAULT_EPI_WEB_API, run_epi_web_batch
from src.identifier_resolver import (
    DEFAULT_PUBCHEM_BASE,
    REQUIRED_IDENTIFIER_COLUMNS,
    run_identifier_completion_batch,
)
from src.pov_lrtp_replica import run_pov_lrtp_batch

from .formula import parse_formula


R_ATM_M3_MOL_K = 8.205736e-5
TEMPERATURE_K = 298.15


@dataclass(frozen=True)
class DownstreamConfig:
    compound_col: str = "Name"
    formula_col: str = "formula"
    group_area_col: str = "Group_Area"
    sample_cols: list[str] = field(default_factory=lambda: ["HH_alk", "WH_alk"])
    smiles_col: str | None = None
    cas_col: str | None = None
    use_pubchem: bool = True
    use_epa: bool = False
    use_echa: bool = False
    pubchem_base: str = DEFAULT_PUBCHEM_BASE
    identifier_timeout: int = 60
    identifier_delay_seconds: float = 0.2
    epi_api_url: str = DEFAULT_EPI_WEB_API
    epi_timeout: int = 90
    epi_delay_seconds: float = 0.2
    gas_constant_atm_m3_mol_k: float = R_ATM_M3_MOL_K
    temperature_k: float = TEMPERATURE_K


@dataclass
class DownstreamResult:
    identifier_input: pd.DataFrame
    completed_identifiers: pd.DataFrame
    identifier_warnings: pd.DataFrame
    epi_input: pd.DataFrame
    epi_results: pd.DataFrame
    epi_raw_results: pd.DataFrame
    epi_errors: pd.DataFrame
    pov_lrtp_input: pd.DataFrame
    pov_lrtp_results: pd.DataFrame


def build_identifier_input(
    screening_df: pd.DataFrame,
    compound_col: str = "Name",
    smiles_col: str | None = None,
    cas_col: str | None = None,
) -> pd.DataFrame:
    rows = []
    for _, row in screening_df.iterrows():
        rows.append(
            {
                "compound": _clean_cell(row.get(compound_col)),
                "smiles": _clean_cell(row.get(smiles_col)) if smiles_col else "",
                "cas": _clean_cell(row.get(cas_col)) if cas_col else "",
                "ec": "",
                "dtxsid": "",
                "echa_id": "",
            }
        )
    return pd.DataFrame(rows, columns=REQUIRED_IDENTIFIER_COLUMNS)


def build_epi_input_from_identifiers(completed_identifiers: pd.DataFrame) -> pd.DataFrame:
    if completed_identifiers is None or completed_identifiers.empty:
        return pd.DataFrame(columns=["compound", "smiles", "cas"])

    epi_input = completed_identifiers.copy()
    for column in ["compound", "smiles", "cas"]:
        if column not in epi_input.columns:
            epi_input[column] = ""
        epi_input[column] = epi_input[column].map(_clean_cell)

    epi_input = epi_input.loc[
        epi_input["compound"].ne("") & epi_input["smiles"].ne(""),
        ["compound", "smiles", "cas"],
    ].reset_index(drop=True)
    return epi_input


def build_pov_lrtp_input(
    screening_df: pd.DataFrame,
    completed_identifiers: pd.DataFrame,
    epi_results: pd.DataFrame,
    compound_col: str = "Name",
    formula_col: str = "formula",
    group_area_col: str = "Group_Area",
    sample_cols: list[str] | None = None,
    gas_constant_atm_m3_mol_k: float = R_ATM_M3_MOL_K,
    temperature_k: float = TEMPERATURE_K,
) -> pd.DataFrame:
    sample_cols = sample_cols or []
    base = screening_df.copy()
    base["_compound_key"] = base[compound_col].map(_key)

    identifiers = _with_key(completed_identifiers, "compound")
    epi = _with_key(epi_results, "compound")

    merged = base.merge(
        _dedupe_by_key(identifiers),
        on="_compound_key",
        how="left",
        suffixes=("", "_identifier"),
    )
    merged = merged.merge(
        _dedupe_by_key(epi),
        on="_compound_key",
        how="left",
        suffixes=("", "_epi"),
    )

    henry = pd.to_numeric(merged.get("henry_atm_m3_mol"), errors="coerce")
    log_kaw = pd.Series(np.nan, index=merged.index, dtype=float)
    positive_henry = henry > 0
    log_kaw.loc[positive_henry] = np.log10(
        henry.loc[positive_henry] / (gas_constant_atm_m3_mol_k * temperature_k)
    )

    epi_mw = pd.to_numeric(merged.get("molecular_weight"), errors="coerce")
    pubchem_mw = pd.to_numeric(merged.get("pubchem_molecular_weight"), errors="coerce")

    output = pd.DataFrame(
        {
            "Name": merged[compound_col],
            "Formula": merged[formula_col] if formula_col in merged.columns else pd.NA,
            "Compound_CID": merged.get("pubchem_cid"),
            "SMILES": merged.get("smiles"),
            "Molecular_Weight": epi_mw.combine_first(pubchem_mw),
            "Log_Kaw_used": log_kaw,
            "Log_Kow_used": pd.to_numeric(merged.get("log_kow"), errors="coerce"),
            "Air_HL": pd.to_numeric(merged.get("level3_air_half_life_hours"), errors="coerce"),
            "Water_HL": pd.to_numeric(merged.get("level3_water_half_life_hours"), errors="coerce"),
            "Soil_HL": pd.to_numeric(merged.get("level3_soil_half_life_hours"), errors="coerce"),
            "Level3_Persistence_Hours": pd.to_numeric(merged.get("level3_persistence_hours"), errors="coerce"),
            "Log_BAF_Arnot_Gobas": pd.to_numeric(merged.get("log_baf"), errors="coerce"),
            "pubchem_status": merged.get("pubchem_match_status"),
            "formula_match": [
                _formulas_match(source, pubchem)
                for source, pubchem in zip(
                    merged[formula_col] if formula_col in merged.columns else [pd.NA] * len(merged),
                    merged.get("pubchem_formula", pd.Series([pd.NA] * len(merged), index=merged.index)),
                )
            ],
        }
    )

    for column in [group_area_col, *sample_cols]:
        if column in merged.columns:
            output[column] = merged[column]

    required_model_cols = [
        "Molecular_Weight",
        "Log_Kaw_used",
        "Log_Kow_used",
        "Air_HL",
        "Water_HL",
        "Soil_HL",
        "Log_BAF_Arnot_Gobas",
    ]
    output["model_input_complete"] = ~output[required_model_cols].isna().any(axis=1)
    return output


def run_downstream_pipeline(
    screening_df: pd.DataFrame,
    config: DownstreamConfig | None = None,
    progress_callback: Any | None = None,
) -> DownstreamResult:
    config = config or DownstreamConfig()
    identifier_input = build_identifier_input(
        screening_df,
        compound_col=config.compound_col,
        smiles_col=config.smiles_col,
        cas_col=config.cas_col,
    )
    completed_identifiers, identifier_warnings = run_identifier_completion_batch(
        identifier_input,
        use_pubchem=config.use_pubchem,
        use_epa=config.use_epa,
        use_echa=config.use_echa,
        pubchem_base=config.pubchem_base,
        timeout=config.identifier_timeout,
        delay_seconds=config.identifier_delay_seconds,
        progress_callback=progress_callback,
    )

    epi_input = build_epi_input_from_identifiers(completed_identifiers)
    epi_results, epi_raw_results, epi_errors = run_epi_web_batch(
        epi_input,
        api_url=config.epi_api_url,
        timeout=config.epi_timeout,
        delay_seconds=config.epi_delay_seconds,
        progress_callback=progress_callback,
    )

    pov_lrtp_input = build_pov_lrtp_input(
        screening_df,
        completed_identifiers,
        epi_results,
        compound_col=config.compound_col,
        formula_col=config.formula_col,
        group_area_col=config.group_area_col,
        sample_cols=config.sample_cols,
        gas_constant_atm_m3_mol_k=config.gas_constant_atm_m3_mol_k,
        temperature_k=config.temperature_k,
    )
    pov_lrtp_results = run_pov_lrtp_batch(pov_lrtp_input)

    return DownstreamResult(
        identifier_input=identifier_input,
        completed_identifiers=completed_identifiers,
        identifier_warnings=identifier_warnings,
        epi_input=epi_input,
        epi_results=epi_results,
        epi_raw_results=epi_raw_results,
        epi_errors=epi_errors,
        pov_lrtp_input=pov_lrtp_input,
        pov_lrtp_results=pov_lrtp_results,
    )


def _with_key(df: pd.DataFrame | None, compound_col: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["_compound_key"])
    keyed = df.copy()
    if compound_col not in keyed.columns:
        keyed[compound_col] = ""
    keyed["_compound_key"] = keyed[compound_col].map(_key)
    return keyed


def _dedupe_by_key(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.loc[df["_compound_key"].ne("")].drop_duplicates("_compound_key", keep="first")


def _formulas_match(source_formula: Any, pubchem_formula: Any) -> bool:
    source_atoms = parse_formula(source_formula)
    pubchem_atoms = parse_formula(pubchem_formula)
    if not source_atoms or not pubchem_atoms:
        return False
    return _normalized_atoms(source_atoms) == _normalized_atoms(pubchem_atoms)


def _normalized_atoms(atoms: dict[str, float]) -> dict[str, float]:
    return {
        element: float(count)
        for element, count in atoms.items()
        if abs(float(count)) > 1e-12
    }


def _key(value: Any) -> str:
    return _clean_cell(value).casefold()


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text
