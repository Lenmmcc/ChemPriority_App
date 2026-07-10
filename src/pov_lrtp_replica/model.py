from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PovLrtpInput:
    name: str
    molar_mass: float
    log_kaw: float
    log_kow: float
    half_life_air_h: float
    half_life_water_h: float
    half_life_soil_h: float


@dataclass(frozen=True)
class EmissionScenarioResult:
    pov_days: float
    ctd_air_km: float
    ctd_water_km: float
    te_percent: float


@dataclass(frozen=True)
class PovLrtpResult:
    name: str
    pov_days: float
    ctd_km: float
    te_percent: float
    phi_air: float
    eair: EmissionScenarioResult
    ewater: EmissionScenarioResult
    esoil: EmissionScenarioResult


_COLUMN_ALIASES = {
    "name": ("Name", "Chemical name", "CHEM_NAME", "compound", "Compound"),
    "molar_mass": ("Molecular_Weight", "Molar mass", "Molecular Weight", "CHEM_MM"),
    "log_kaw": ("Log_Kaw_used", "LogKaw", "logKaw", "Log_Kaw", "CHEM_LogKaw"),
    "log_kow": ("Log_Kow_used", "LogKow", "logKow", "Log_Kow", "CHEM_LogKow"),
    "half_life_air_h": ("Air_HL", "HLair", "Air half-life", "CHEM_HLair"),
    "half_life_water_h": ("Water_HL", "HLwater", "Water half-life", "CHEM_HLwater"),
    "half_life_soil_h": ("Soil_HL", "HLsoil", "Soil half-life", "CHEM_HLsoil"),
}


def calculate_pov_lrtp(inputs: PovLrtpInput) -> PovLrtpResult:
    _validate_inputs(inputs)

    area, height, volume, phi, phi_oc, rho_sub, constants = _build_environment()

    tk = constants["TK"]
    gas_constant = constants["R"]
    windspeed = constants["windspeed"]
    waterspeed = constants["waterspeed"]

    kdeg = np.array(
        [
            math.log(2) / inputs.half_life_air_h,
            math.log(2) / inputs.half_life_water_h,
            math.log(2) / inputs.half_life_soil_h,
        ],
        dtype=float,
    )

    log_koa = inputs.log_kow - inputs.log_kaw
    kaw = 10.0 ** inputs.log_kaw
    kow = 10.0 ** inputs.log_kow
    koa = 10.0 ** log_koa

    koc_l_kg = kow * 0.35
    kqw_l_kg = koc_l_kg * phi_oc[2, 1]
    kqw = kqw_l_kg * rho_sub[2, 1] / 1000.0
    ksw_l_kg = koc_l_kg * phi_oc[2, 2]
    ksw = ksw_l_kg * rho_sub[2, 2] / 1000.0
    kqa = 0.42 * koa

    z_sub = np.zeros((3, 3), dtype=float)
    z = np.zeros(3, dtype=float)
    rho = np.zeros(3, dtype=float)

    z_sub[0, 0] = 1.0 / (gas_constant * tk)
    z_sub[0, 2] = z_sub[0, 0]
    z_sub[1, 1] = z_sub[0, 0] / kaw
    z_sub[1, 2] = z_sub[1, 1]
    z_sub[2, 0] = kqa * z_sub[0, 0]
    z_sub[2, 2] = ksw * z_sub[1, 1]
    z_sub[2, 1] = kqw * z_sub[1, 1]

    z[0] = (phi[0, 0] * z_sub[0, 0]) + (phi[2, 0] * z_sub[2, 0])
    rho[0] = (phi[0, 0] * rho_sub[0, 0]) + (phi[2, 0] * rho_sub[2, 0])
    z[1] = (phi[1, 1] * z_sub[1, 1]) + (phi[2, 1] * z_sub[2, 1])
    rho[1] = (phi[1, 1] * rho_sub[1, 1]) + (phi[2, 1] * rho_sub[2, 1])
    z[2] = (phi[0, 2] * z_sub[0, 2]) + (phi[1, 2] * z_sub[1, 2]) + (phi[2, 2] * z_sub[2, 2])
    rho[2] = (phi[0, 2] * rho_sub[0, 2]) + (phi[1, 2] * rho_sub[1, 2]) + (phi[2, 2] * rho_sub[2, 2])

    d_inter = _calculate_intermedia_d_values(area, height, phi, z_sub, z, constants)
    d = np.zeros((3, 5), dtype=float)
    d[0, 4] = volume[0] * z_sub[0, 0] * kdeg[0]
    d[1, 4] = volume[1] * z[1] * kdeg[1]
    d[2, 4] = volume[2] * z[2] * kdeg[2]

    for source in range(3):
        for destination in range(4):
            d[source, destination] = d_inter[source, destination, :].sum()

    fugacity, inventories, inventory_totals = _solve_emission_scenarios(d, z, volume)

    pov_em = np.zeros(3, dtype=float)
    ctd_air_em = np.full(3, -999.0, dtype=float)
    ctd_water_em = np.full(3, -999.0, dtype=float)
    te_em = np.zeros(3, dtype=float)

    ctd_air_em[0] = inventory_totals[0] / 100.0 * inventories[0, 0] / inventory_totals[0] * windspeed
    ctd_water_em[1] = inventory_totals[1] / 100.0 * inventories[1, 1] / inventory_totals[1] * waterspeed

    dte = windspeed * 1000.0 * math.sqrt(area[0]) * height[0] * z[0]
    for scenario in range(3):
        degradation_flux = sum(fugacity[compartment, scenario] * d[compartment, 4] for compartment in range(3))
        pov_em[scenario] = inventory_totals[scenario] / 24.0 / degradation_flux
        te_em[scenario] = (
            fugacity[0, scenario] ** 2 * (d[0, 1] + d[0, 2]) * dte
        ) / (100.0 ** 2) * 100.0

    eair = EmissionScenarioResult(
        pov_days=pov_em[0],
        ctd_air_km=ctd_air_em[0],
        ctd_water_km=ctd_water_em[0],
        te_percent=te_em[0],
    )
    ewater = EmissionScenarioResult(
        pov_days=pov_em[1],
        ctd_air_km=ctd_air_em[1],
        ctd_water_km=ctd_water_em[1],
        te_percent=te_em[1],
    )
    esoil = EmissionScenarioResult(
        pov_days=pov_em[2],
        ctd_air_km=ctd_air_em[2],
        ctd_water_km=ctd_water_em[2],
        te_percent=te_em[2],
    )

    return PovLrtpResult(
        name=inputs.name,
        pov_days=float(pov_em.max()),
        ctd_km=float(max(ctd_air_em[0], ctd_water_em[1])),
        te_percent=float(te_em.max()),
        phi_air=float(phi[2, 0] * z_sub[2, 0] / z[0]),
        eair=eair,
        ewater=ewater,
        esoil=esoil,
    )


def run_pov_lrtp_batch(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    statuses: list[str] = []
    errors: list[str] = []
    pov_values: list[float | None] = []
    ctd_values: list[float | None] = []
    te_values: list[float | None] = []

    for _, row in result.iterrows():
        try:
            model_input = _input_from_row(row)
            model_result = calculate_pov_lrtp(model_input)
        except Exception as exc:
            statuses.append("error")
            errors.append(str(exc))
            pov_values.append(None)
            ctd_values.append(None)
            te_values.append(None)
        else:
            statuses.append("ok")
            errors.append("")
            pov_values.append(model_result.pov_days)
            ctd_values.append(model_result.ctd_km)
            te_values.append(model_result.te_percent)

    result["Status"] = statuses
    result["Error"] = errors
    result["POV_days"] = pov_values
    result["CTD_km"] = ctd_values
    result["TE_percent"] = te_values
    result = _add_p_b_lrtp_scores(result)
    return result


def _solve_emission_scenarios(
    d: np.ndarray,
    z: np.ndarray,
    volume: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = np.zeros((3, 3), dtype=float)
    for compartment in range(3):
        matrix[compartment, compartment] = (
            d[compartment, 0]
            + d[compartment, 1]
            + d[compartment, 2]
            + d[compartment, 3]
            + d[compartment, 4]
        )
        for other in range(3):
            if other != compartment:
                matrix[compartment, other] = -d[other, compartment]

    fugacity = np.zeros((3, 3), dtype=float)
    inventories = np.zeros((3, 3), dtype=float)
    inventory_totals = np.zeros(3, dtype=float)
    for scenario in range(3):
        emissions = np.zeros(3, dtype=float)
        emissions[scenario] = 100.0
        fugacity[:, scenario] = np.linalg.solve(matrix, emissions)
        inventories[:, scenario] = fugacity[:, scenario] * z * volume
        inventory_totals[scenario] = inventories[:, scenario].sum()
    return fugacity, inventories, inventory_totals


def _calculate_intermedia_d_values(
    area: np.ndarray,
    height: np.ndarray,
    phi: np.ndarray,
    z_sub: np.ndarray,
    z: np.ndarray,
    constants: dict[str, float],
) -> np.ndarray:
    d_inter = np.zeros((3, 4, 4), dtype=float)
    mtc_awa = constants["MTCawa"]
    mtc_aww = constants["MTCaww"]
    mtc_rain = constants["MTCrain"]
    mtc_qd = constants["MTCQd"]
    mtc_as = constants["MTCas"]
    mtc_sw = constants["MTCsw"]
    mtc_sconv = constants["MTCsconv"]
    mtc_sabl = constants["MTCsabl"]
    mtc_wat_runoff = constants["MTCwatrunoff"]
    mtc_soil_runoff = constants["MTCsoilrunoff"]
    mtc_1_strat = constants["MTC1strat"]
    mtc_2_sink = constants["MTC2sink"]
    mtc_2_leach = constants["MTC2leach"]
    mtc_3_sink = constants["MTC3sink"]
    mtc_3_leach = constants["MTC3leach"]
    scav_ratio = constants["ScavRatio"]

    d_inter[0, 1, 0] = area[1] / (
        (1.0 / (mtc_awa * z_sub[0, 0])) + (1.0 / (mtc_aww * z_sub[1, 1]))
    )
    d_inter[0, 1, 1] = area[1] * phi[2, 0] * mtc_qd * z_sub[2, 0]
    d_inter[0, 1, 2] = area[1] * mtc_rain * z_sub[1, 1]
    d_inter[0, 1, 3] = area[1] * scav_ratio * mtc_rain * z_sub[2, 0] * phi[2, 0]

    d_inter[0, 2, 0] = area[2] * z_sub[0, 0] / (
        (z_sub[0, 0] / (mtc_as * z_sub[0, 0] + mtc_sw * z_sub[1, 1] + mtc_sconv * z_sub[2, 2]))
        + (1.0 / mtc_sabl)
    )
    d_inter[0, 2, 1] = area[2] * phi[2, 0] * mtc_qd * z_sub[2, 0]
    d_inter[0, 2, 2] = area[2] * mtc_rain * z_sub[1, 1]
    d_inter[0, 2, 3] = area[2] * scav_ratio * mtc_rain * z_sub[2, 0] * phi[2, 0]

    d_inter[1, 0, 0] = d_inter[0, 1, 0]
    d_inter[2, 0, 0] = d_inter[0, 2, 0]

    d_inter[2, 1, 0] = area[2] * mtc_wat_runoff * z_sub[1, 2]
    d_inter[2, 1, 1] = area[2] * mtc_soil_runoff * z_sub[2, 2]

    d_inter[0, 3, 0] = mtc_1_strat * area[0] * z_sub[0, 0]
    d_inter[1, 3, 0] = mtc_2_sink * area[1] * z_sub[2, 1]
    d_inter[1, 3, 1] = mtc_2_leach * area[1] * z_sub[1, 1]
    d_inter[2, 3, 0] = mtc_3_sink * area[2] * z_sub[2, 2]
    d_inter[2, 3, 1] = mtc_3_leach * area[2] * z_sub[1, 2]

    _ = height, z
    return d_inter


def _build_environment() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
    constants = {
        "TK": 298.15,
        "R": float(np.float32(8.314)),
        "MTCawa": 30.0,
        "MTCaww": 0.03,
        "MTCrain": 9.7e-5,
        "MTCQd": 10.8,
        "MTCas": 0.04,
        "MTCsw": 1.0e-5,
        "MTCsconv": 4.54e-7,
        "MTCsabl": 1.0,
        "MTCwatrunoff": 3.9e-5,
        "MTCsoilrunoff": 2.3e-8,
        "MTC1strat": 0.064,
        "ScavRatio": 200000.0,
        "windspeed": float(np.float32(14.4)),
        "waterspeed": float(np.float32(0.072)),
    }

    area = np.zeros(3, dtype=float)
    height = np.array([6000.0, 100.0, 0.1], dtype=float)
    area[0] = 510000000000000.0
    area[1] = 0.71 * area[0]
    area[2] = area[0] - area[1]
    volume = area * height

    phi = np.zeros((3, 3), dtype=float)
    phi[2, 0] = 0.00000000002
    phi[0, 0] = 1.0 - phi[2, 0]
    phi[2, 1] = 0.0000005
    phi[1, 1] = 1.0 - phi[2, 1]
    phi[0, 2] = 0.2
    phi[1, 2] = 0.3
    phi[2, 2] = 1.0 - phi[0, 2] - phi[1, 2]

    rho_sub = np.zeros((3, 3), dtype=float)
    rho_sub[0, 0] = (0.029 * 101325.0) / (constants["R"] * constants["TK"])
    rho_sub[2, 0] = 2400.0
    rho_sub[2, 1] = 2400.0
    rho_sub[1, 1] = 1000.0
    rho_sub[0, 2] = rho_sub[0, 0]
    rho_sub[1, 2] = rho_sub[1, 1]
    rho_sub[2, 2] = 2400.0

    phi_oc = np.zeros((3, 3), dtype=float)
    phi_oc[2, 1] = 0.1
    phi_oc[2, 2] = 0.02

    constants["MTC2sink"] = 0.0052 / (1000.0 * phi_oc[2, 1] * rho_sub[2, 1])
    constants["MTC2leach"] = 1.14e-4
    constants["MTC3sink"] = 0.05 * constants["MTCsconv"]
    constants["MTC3leach"] = 3.0e-6

    return area, height, volume, phi, phi_oc, rho_sub, constants


def _validate_inputs(inputs: PovLrtpInput) -> None:
    numeric_fields = {
        "molar_mass": inputs.molar_mass,
        "log_kaw": inputs.log_kaw,
        "log_kow": inputs.log_kow,
        "half_life_air_h": inputs.half_life_air_h,
        "half_life_water_h": inputs.half_life_water_h,
        "half_life_soil_h": inputs.half_life_soil_h,
    }
    for field, value in numeric_fields.items():
        if not isinstance(value, (int, float, np.number)) or not math.isfinite(float(value)):
            raise ValueError(f"{field} must be a finite number")

    if inputs.molar_mass < 0:
        raise ValueError("molar_mass must be >= 0")
    if inputs.half_life_air_h <= 0:
        raise ValueError("half_life_air_h must be > 0")
    if inputs.half_life_water_h <= 0:
        raise ValueError("half_life_water_h must be > 0")
    if inputs.half_life_soil_h <= 0:
        raise ValueError("half_life_soil_h must be > 0")


def _input_from_row(row: pd.Series) -> PovLrtpInput:
    return PovLrtpInput(
        name=str(_value_from_aliases(row, "name")),
        molar_mass=float(_value_from_aliases(row, "molar_mass")),
        log_kaw=float(_value_from_aliases(row, "log_kaw")),
        log_kow=float(_value_from_aliases(row, "log_kow")),
        half_life_air_h=float(_value_from_aliases(row, "half_life_air_h")),
        half_life_water_h=float(_value_from_aliases(row, "half_life_water_h")),
        half_life_soil_h=float(_value_from_aliases(row, "half_life_soil_h")),
    )


def _value_from_aliases(row: pd.Series, field: str) -> Any:
    for column in _COLUMN_ALIASES[field]:
        if column in row.index:
            return row[column]
    raise ValueError(f"Missing required column for {field}: {', '.join(_COLUMN_ALIASES[field])}")


def _add_p_b_lrtp_scores(result: pd.DataFrame) -> pd.DataFrame:
    result["P_B_LRTP_score"] = np.nan
    result["Scores"] = np.nan
    result["Score_Assumption"] = "log10(POV_days)+Log_BAF+log10(TE_percent_as_model_output)"

    if "Log_BAF_Arnot_Gobas" not in result.columns:
        return result

    pov = pd.to_numeric(result["POV_days"], errors="coerce")
    log_baf = pd.to_numeric(result["Log_BAF_Arnot_Gobas"], errors="coerce")
    te = pd.to_numeric(result["TE_percent"], errors="coerce")
    valid_score = pov.gt(0) & te.gt(0) & log_baf.notna()

    result.loc[valid_score, "P_B_LRTP_score"] = (
        np.log10(pov.loc[valid_score])
        + log_baf.loc[valid_score]
        + np.log10(te.loc[valid_score])
    )
    result.loc[valid_score, "Scores"] = result.loc[valid_score, "P_B_LRTP_score"]
    return result
