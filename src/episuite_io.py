import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.batch_runner import run_ordered_batch
from src.query_cache import cache_control, cached_call


REQUIRED_COLUMNS = ["compound", "smiles"]
OPTIONAL_COLUMNS = ["cas"]

DEFAULT_EPI_WEB_API = "https://episuite.dev/api/submit"

FATE_ENDPOINTS = [
    {
        "endpoint": "log_kow",
        "model": "KOWWIN",
        "description": "辛醇/水分配系数 logKow",
    },
    {
        "endpoint": "water_solubility_mg_l",
        "model": "WSKOWWIN / WATERNT",
        "description": "水溶解度，单位通常为 mg/L",
    },
    {
        "endpoint": "vapor_pressure_mm_hg",
        "model": "MPBPWIN",
        "description": "蒸气压，单位通常为 mm Hg",
    },
    {
        "endpoint": "henry_atm_m3_mol",
        "model": "HENRYWIN",
        "description": "亨利定律常数，单位通常为 atm-m3/mol",
    },
    {
        "endpoint": "log_koc",
        "model": "KOCWIN",
        "description": "有机碳归一化吸附系数 logKoc",
    },
    {
        "endpoint": "biowin_ultimate",
        "model": "BIOWIN",
        "description": "最终生物降解模型结果",
    },
    {
        "endpoint": "biowin_ready",
        "model": "BIOWIN",
        "description": "快速生物降解模型结果",
    },
    {
        "endpoint": "bcf",
        "model": "BCFBAF",
        "description": "生物富集因子 BCF",
    },
    {
        "endpoint": "baf",
        "model": "BCFBAF",
        "description": "生物放大因子 BAF",
    },
    {
        "endpoint": "atmosphere_oh_half_life_hours",
        "model": "AOPWIN",
        "description": "大气 OH 反应半衰期，单位通常为小时",
    },
    {
        "endpoint": "stp_total_removal_percent",
        "model": "STPWIN",
        "description": "污水处理厂总去除率，单位为百分比",
    },
    {
        "endpoint": "level3_air_percent",
        "model": "LEV3EPI",
        "description": "Level III 逸度模型空气分配百分比",
    },
    {
        "endpoint": "level3_water_percent",
        "model": "LEV3EPI",
        "description": "Level III 逸度模型水体分配百分比",
    },
    {
        "endpoint": "level3_soil_percent",
        "model": "LEV3EPI",
        "description": "Level III 逸度模型土壤分配百分比",
    },
    {
        "endpoint": "level3_sediment_percent",
        "model": "LEV3EPI",
        "description": "Level III 逸度模型沉积物分配百分比",
    },
    {
        "endpoint": "level3_persistence_hours",
        "model": "LEV3EPI",
        "description": "Level III 逸度模型整体持久性，单位为小时",
    },
    {
        "endpoint": "river_volatilization_half_life_hours",
        "model": "WATERNT",
        "description": "河流挥发半衰期，单位为小时",
    },
    {
        "endpoint": "lake_volatilization_half_life_hours",
        "model": "WATERNT",
        "description": "湖泊挥发半衰期，单位为小时",
    },
]

ENDPOINT_KEYS = [item["endpoint"] for item in FATE_ENDPOINTS]

EPI_WEB_RESULT_SHEETS = [
    "Core_Summary",
    "Properties",
    "Degradation",
    "Fate_Transport",
    "Bioaccumulation",
    "ECOSAR_Aquatic_Toxicity",
    "Model_Metadata",
    "Raw_API_JSON",
    "Warnings",
]

EPI_REPORT_HIDDEN_COMPAT_COLUMNS = [
    "log_kow",
    "log_kow_selected",
    "log_kow_units",
]

_NUMBER = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)"

TEXT_PATTERNS = {
    "log_kow": [
        rf"Log\s*Kow\s*\([^)]*estimate\)\s*[:=]\s*{_NUMBER}",
        rf"Log\s*KOW\s*[:=]\s*{_NUMBER}",
        rf"Log\s*Kow\s*[:=]\s*{_NUMBER}",
    ],
    "water_solubility_mg_l": [
        rf"Water\s+Solubility(?:\s*\([^)]*\))?\s*[:=]\s*{_NUMBER}\s*mg\s*/?\s*L",
        rf"Water\s+Sol\s*[:=]\s*{_NUMBER}\s*mg\s*/?\s*L",
    ],
    "vapor_pressure_mm_hg": [
        rf"Vapor\s+Pressure(?:\s*\([^)]*\))?\s*[:=]\s*{_NUMBER}\s*mm\s*Hg",
        rf"Vapor\s+Pr\s*[:=]\s*{_NUMBER}",
    ],
    "henry_atm_m3_mol": [
        rf"Henry(?:'s)?(?:\s+Law)?(?:\s+Constant|\s+LC)?[^:=]*[:=]\s*{_NUMBER}",
    ],
    "log_koc": [
        rf"Log\s*Koc[^:=]*[:=]\s*{_NUMBER}",
        rf"\bKoc[^:=]*(?:estimate|estimated)?[^:=]*[:=]\s*{_NUMBER}",
    ],
    "biowin_ultimate": [
        rf"Biowin\s*3[^:=]*[:=]\s*{_NUMBER}",
        rf"Ultimate\s+Biodegradation[^:=]*[:=]\s*{_NUMBER}",
    ],
    "biowin_ready": [
        rf"Biowin\s*5[^:=]*[:=]\s*{_NUMBER}",
        rf"Ready\s+Biodegradation[^:=]*[:=]\s*{_NUMBER}",
    ],
    "bcf": [
        rf"\bBCF\b[^:=]*[:=]\s*{_NUMBER}",
        rf"Bioconcentration\s+Factor[^:=]*[:=]\s*{_NUMBER}",
    ],
    "baf": [
        rf"\bBAF\b[^:=]*[:=]\s*{_NUMBER}",
        rf"Bioaccumulation\s+Factor[^:=]*[:=]\s*{_NUMBER}",
    ],
    "atmosphere_oh_half_life_hours": [
        rf"Half[-\s]*Life[^:\n]*(?:OH|hydroxyl)[^:=]*[:=]\s*{_NUMBER}\s*(?:hrs?|hours?)",
        rf"OH[^:\n]*Half[-\s]*Life[^:=]*[:=]\s*{_NUMBER}\s*(?:hrs?|hours?)",
    ],
    "stp_total_removal_percent": [
        rf"Total\s+removal[^:=]*[:=]\s*{_NUMBER}\s*%",
        rf"Total\s+Removal[^:=]*[:=]\s*{_NUMBER}",
    ],
    "level3_air_percent": [
        rf"\bAir\b[^:=]*[:=]\s*{_NUMBER}\s*%",
    ],
    "level3_water_percent": [
        rf"\bWater\b[^:=]*[:=]\s*{_NUMBER}\s*%",
    ],
    "level3_soil_percent": [
        rf"\bSoil\b[^:=]*[:=]\s*{_NUMBER}\s*%",
    ],
    "level3_sediment_percent": [
        rf"\bSediment\b[^:=]*[:=]\s*{_NUMBER}\s*%",
    ],
}

COLUMN_ALIASES = {
    "compound": ["compound", "name", "chemical", "chemical_name", "chem_name"],
    "smiles": ["smiles", "smiles_notation", "canonical_smiles", "isomeric_smiles"],
    "cas": ["cas", "casrn", "cas_no", "cas_number", "cas号"],
    "log_kow": ["log_kow", "logkow", "log_kow_kowwin", "kowwin", "logp"],
    "water_solubility_mg_l": [
        "water_solubility",
        "water_solubility_mg_l",
        "watersol",
        "water_sol",
        "wskowwin",
    ],
    "vapor_pressure_mm_hg": ["vapor_pressure", "vapor_pressure_mm_hg", "vapor_pr", "mpbpwin"],
    "henry_atm_m3_mol": ["henry", "henry_law_constant", "henry_atm_m3_mol", "henry_lc"],
    "log_koc": ["log_koc", "logkoc", "koc", "kocwin"],
    "biowin_ultimate": ["biowin_3", "biowin3", "ultimate_biodegradation"],
    "biowin_ready": ["biowin_5", "biowin5", "ready_biodegradation"],
    "bcf": ["bcf", "bcf_baf", "bcfbaf", "bioconcentration_factor"],
    "baf": ["baf", "bioaccumulation_factor"],
    "atmosphere_oh_half_life_hours": [
        "atmosphere_oh_half_life",
        "atmosphere_oh_half_life_hours",
        "oh_half_life",
        "aopwin",
    ],
    "stp_total_removal_percent": ["stp_total_removal", "stp_total_removal_percent", "total_removal", "stpwin"],
    "level3_air_percent": ["level3_air", "level3_air_percent", "air_percent"],
    "level3_water_percent": ["level3_water", "level3_water_percent", "water_percent"],
    "level3_soil_percent": ["level3_soil", "level3_soil_percent", "soil_percent"],
    "level3_sediment_percent": ["level3_sediment", "level3_sediment_percent", "sediment_percent"],
    "level3_persistence_hours": ["level3_persistence", "level3_persistence_hours", "persistence"],
    "river_volatilization_half_life_hours": [
        "river_volatilization_half_life",
        "river_volatilization_half_life_hours",
    ],
    "lake_volatilization_half_life_hours": [
        "lake_volatilization_half_life",
        "lake_volatilization_half_life_hours",
    ],
}


def make_template_file():
    template_df = pd.DataFrame(
        {
            "compound": ["example_compound_1", "example_compound_2"],
            "smiles": ["CCO", "c1ccccc1"],
            "cas": ["64-17-5", "71-43-2"],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        template_df.to_excel(writer, sheet_name="EPISuite_Input", index=False)
    buffer.seek(0)
    return buffer


def normalize_input_columns(df):
    normalized = df.copy()
    normalized.columns = [str(col).strip() for col in normalized.columns]

    rename_map = {}
    for col in normalized.columns:
        key = _normalize_key(col)
        if key in {"compound", "name", "compoundname", "chemical", "chemicalname"}:
            rename_map[col] = "compound"
        elif key in {"smiles", "canonicalsmiles", "isomericsmiles"}:
            rename_map[col] = "smiles"
        elif key in {"cas", "casrn", "casno", "casnumber", "cas号", "cas编号"}:
            rename_map[col] = "cas"

    normalized = normalized.rename(columns=rename_map)
    for col in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
        if col in normalized.columns:
            normalized[col] = normalized[col].astype(str).str.strip()
            normalized[col] = normalized[col].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return normalized


def validate_input(df):
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        return False, f"缺少必要列：{', '.join(missing_cols)}"

    empty_rows = df[REQUIRED_COLUMNS].isna().any(axis=1).sum()
    if empty_rows > 0:
        return False, f"compound 或 smiles 存在空值，请先处理 {empty_rows} 行不完整数据。"

    duplicated = df["compound"].duplicated().sum()
    if duplicated > 0:
        return False, f"compound 存在 {duplicated} 个重复名称，请先确认是否需要合并或重命名。"

    return True, "输入数据检查通过。"


def build_input_zip(df):
    clean_df = df[input_columns_for_display(df)].copy()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_buffer = io.StringIO()
    clean_df.to_csv(csv_buffer, index=False)

    smiles_only = "\n".join(clean_df["smiles"].tolist()) + "\n"
    named_smi = "\n".join(
        f"{row.smiles}\t{row.compound}" for row in clean_df.itertuples(index=False)
    ) + "\n"
    paste_list = "\n".join(clean_df["smiles"].tolist())

    readme = "\n".join(
        [
            "EPI Suite input package",
            "",
            "Files:",
            "- episuite_input.csv: compound, SMILES, and optional CAS table for traceability.",
            "- episuite_smiles_only.txt: one SMILES per line; safest format for EPI Web Suite paste input.",
            "- episuite_named.smi: SMILES + compound name separated by tab; useful for cheminformatics tools.",
            "- episuite_paste_list.txt: SMILES list for direct copy/paste.",
            "",
            "Recommended workflow:",
            "1. Submit the SMILES list in EPI Suite or EPI Web Suite.",
            "2. Export or copy the EPI Suite result as CSV, Excel, TXT, or DOC.",
            "3. Upload that result back to the ChemPriority EPI Suite page for parsing.",
        ]
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("episuite_input.csv", csv_buffer.getvalue())
        zf.writestr("episuite_smiles_only.txt", smiles_only)
        zf.writestr("episuite_named.smi", named_smi)
        zf.writestr("episuite_paste_list.txt", paste_list)
        zf.writestr("README.txt", readme)
        zf.writestr("manifest.txt", f"created_at={timestamp}\ncount={len(clean_df)}\n")
    zip_buffer.seek(0)
    return zip_buffer


def build_empty_result_template(input_df):
    result = input_df[input_columns_for_display(input_df)].copy()
    for key in ENDPOINT_KEYS:
        result[key] = pd.NA
    return result


def input_columns_for_display(df):
    return REQUIRED_COLUMNS + [col for col in OPTIONAL_COLUMNS if col in df.columns]


def call_epi_web_api(smiles, api_url=DEFAULT_EPI_WEB_API, timeout=90, cas=None):
    request_params = {"smiles": smiles}
    cas = _clean_optional_text(cas)
    if cas:
        request_params["cas"] = cas
    return cached_call(
        "epi_web_submit",
        "v1",
        {"api_url": api_url, "params": request_params},
        lambda: _call_epi_web_api_uncached(smiles, api_url=api_url, timeout=timeout, cas=cas),
    )


def _call_epi_web_api_uncached(smiles, api_url=DEFAULT_EPI_WEB_API, timeout=90, cas=None):
    request_params = {"smiles": smiles}
    cas = _clean_optional_text(cas)
    if cas:
        request_params["cas"] = cas
    params = urllib.parse.urlencode(request_params)
    separator = "&" if "?" in api_url else "?"
    url = f"{api_url}{separator}{params}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ChemPriority EPISuite connector",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"EPI Web Suite 返回 HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 EPI Web Suite: {exc.reason}") from exc


def run_epi_web_batch(
    input_df,
    api_url=DEFAULT_EPI_WEB_API,
    timeout=90,
    delay_seconds=0.2,
    progress_callback=None,
    max_workers=1,
    cache_enabled=True,
):
    rows = []
    raw_rows = []
    errors = []
    items = list(input_df.iterrows())

    def display_compound(item):
        _, row = item
        return str(row.get("compound", "")).strip()

    def process_row(item):
        _, row = item
        compound = str(row.get("compound", "")).strip()
        smiles = str(row.get("smiles", "")).strip()
        cas = _clean_optional_text(row.get("cas"))
        query_note = ""
        row_rows = []
        row_raw_rows = []
        row_errors = []
        try:
            raw = call_epi_web_api(smiles, cas=cas, api_url=api_url, timeout=timeout)
        except Exception as exc:
            error_text = str(exc)
            if cas and _is_cas_not_located_error(error_text):
                try:
                    raw = call_epi_web_api(smiles, cas=None, api_url=api_url, timeout=timeout)
                    query_note = f"CAS 查询失败，已回退到 SMILES：{error_text}"
                except Exception as fallback_exc:
                    fallback_error = f"{error_text}; SMILES 回退失败：{fallback_exc}"
                    _append_failed_epi_row(row_rows, row_errors, compound, smiles, cas, fallback_error)
                    raw = None
                    query_note = ""
            else:
                _append_failed_epi_row(row_rows, row_errors, compound, smiles, cas, error_text)
                raw = None
                query_note = ""

        if raw is not None:
            _append_successful_epi_row(row_rows, row_raw_rows, compound, smiles, cas, raw, query_note=query_note)
        return row_rows, row_raw_rows, row_errors

    with cache_control(cache_enabled):
        batch_results = run_ordered_batch(
            items,
            process_row,
            max_workers=max_workers,
            delay_seconds=delay_seconds,
            progress_callback=progress_callback,
            label_func=display_compound,
        )

    for result, item in zip(batch_results, items):
        if result.error is not None:
            _, row = item
            compound = str(row.get("compound", "")).strip()
            smiles = str(row.get("smiles", "")).strip()
            cas = _clean_optional_text(row.get("cas"))
            _append_failed_epi_row(rows, errors, compound, smiles, cas, str(result.error))
            continue
        row_rows, row_raw_rows, row_errors = result.value
        rows.extend(row_rows)
        raw_rows.extend(row_raw_rows)
        errors.extend(row_errors)

    return pd.DataFrame(rows), pd.DataFrame(raw_rows), pd.DataFrame(errors)


def _append_successful_epi_row(rows, raw_rows, compound, smiles, cas, raw, query_note=""):
    rows.append(extract_epi_web_summary(compound, smiles, raw, cas=cas, query_note=query_note))
    chemical = raw.get("chemicalProperties", {})
    raw_rows.append(
        {
            "compound": compound,
            "smiles": smiles,
            "cas": cas or pd.NA,
            "epi_cas": chemical.get("cas"),
            "epi_smiles": chemical.get("smiles"),
            "query_note": query_note,
            "raw_json": json.dumps(raw, ensure_ascii=False),
        }
    )


def _append_failed_epi_row(rows, errors, compound, smiles, cas, error_text):
    errors.append({"compound": compound, "smiles": smiles, "cas": cas or pd.NA, "error": error_text})
    failed = {
        "compound": compound,
        "smiles": smiles,
        "cas": cas or pd.NA,
        "status": "failed",
        "error": error_text,
    }
    for key in ENDPOINT_KEYS:
        failed[key] = pd.NA
    rows.append(failed)


def _is_cas_not_located_error(error_text):
    normalized = str(error_text).lower()
    return "http 404" in normalized and "could not locate cas id" in normalized


def extract_epi_web_summary(compound, smiles, data, cas=None, query_note=""):
    chemical = data.get("chemicalProperties", {})
    log_kow_selected = _value_at(data, "logKow.selectedValue.value")
    water_solubility_selected = _first_value(
        data,
        [
            "waterSolubilityFromWaterNt.selectedValue.value",
            "waterSolubilityFromLogKow.selectedValue.value",
        ],
    )
    vapor_pressure_selected = _value_at(data, "vaporPressure.selectedValue.value")
    henry_selected = _value_at(data, "henrysLawConstant.selectedValue.value")
    log_koc_selected = _value_at(data, "logKoc.selectedValue.value")
    return {
        "compound": compound,
        "smiles": smiles,
        "cas": _clean_optional_text(cas) or pd.NA,
        "status": "success",
        "query_note": query_note,
        "epi_name": chemical.get("name"),
        "epi_systematic_name": chemical.get("systematicName"),
        "epi_cas": chemical.get("cas"),
        "epi_smiles": chemical.get("smiles"),
        "molecular_formula": chemical.get("molecularFormula"),
        "molecular_weight": chemical.get("molecularWeight"),
        "organic": chemical.get("organic"),
        "flags": chemical.get("flags"),
        "log_kow": log_kow_selected,
        "log_kow_selected": log_kow_selected,
        "log_kow_estimated": _value_at(data, "logKow.estimatedValue.value"),
        "log_kow_experimental": _experimental_or_selected_value(data, "logKow"),
        "log_kow_type": _value_at(data, "logKow.selectedValue.valueType"),
        "water_solubility_mg_l": water_solubility_selected,
        "water_solubility_selected": water_solubility_selected,
        "water_solubility_estimated": _first_section_value(
            data,
            [
                "waterSolubilityFromWaterNt",
                "waterSolubilityFromLogKow",
            ],
            "estimatedValue.value",
        ),
        "water_solubility_experimental": _first_experimental_or_selected_value(
            data,
            [
                "waterSolubilityFromWaterNt",
                "waterSolubilityFromLogKow",
            ],
        ),
        "water_solubility_type": _first_value(
            data,
            [
                "waterSolubilityFromWaterNt.selectedValue.valueType",
                "waterSolubilityFromLogKow.selectedValue.valueType",
            ],
        ),
        "vapor_pressure_mm_hg": vapor_pressure_selected,
        "vapor_pressure_selected": vapor_pressure_selected,
        "vapor_pressure_estimated": _value_at(data, "vaporPressure.estimatedValue.value"),
        "vapor_pressure_experimental": _experimental_or_selected_value(data, "vaporPressure"),
        "vapor_pressure_type": _value_at(data, "vaporPressure.selectedValue.valueType"),
        "henry_atm_m3_mol": henry_selected,
        "henry_selected": henry_selected,
        "henry_estimated": _value_at(data, "henrysLawConstant.estimatedValue.value"),
        "henry_experimental": _experimental_or_selected_value(data, "henrysLawConstant"),
        "henry_type": _value_at(data, "henrysLawConstant.selectedValue.valueType"),
        "log_koc": log_koc_selected,
        "log_koc_selected": log_koc_selected,
        "log_koc_estimated": _value_at(data, "logKoc.estimatedValue.value"),
        "log_koc_experimental": _experimental_or_selected_value(data, "logKoc"),
        "log_koc_type": _value_at(data, "logKoc.selectedValue.valueType"),
        "biowin_ultimate": _biowin_model_value(data, "Ultimate Biodegradation Timeframe"),
        "biowin_primary": _biowin_model_value(data, "Primary Biodegradation Timeframe"),
        "biowin_ready": _biowin_model_value(data, "MITI Linear Model Prediction"),
        "bcf": _value_at(data, "bioconcentration.bioconcentrationFactor"),
        "log_bcf": _value_at(data, "bioconcentration.logBioconcentrationFactor"),
        "baf": _value_at(data, "bioconcentration.bioaccumulationFactor"),
        "log_baf": _value_at(data, "bioconcentration.logBioaccumulationFactor"),
        "atmosphere_oh_half_life_hours": _value_at(data, "atmosphericHalfLife.estimatedValue.value"),
        "atmosphere_oh_rate_constant": _value_at(
            data,
            "atmosphericHalfLife.estimatedHydroxylRadicalReactionRateConstant.value",
        ),
        "stp_total_removal_percent": _value_at(data, "sewageTreatmentModel.model.TotalRemoval.Percent"),
        "stp_final_effluent_percent": _value_at(data, "sewageTreatmentModel.model.FinalEffluent.Percent"),
        "level3_air_percent": _fugacity_medium_value(data, "Air", "MassAmount"),
        "level3_water_percent": _fugacity_medium_value(data, "Water", "MassAmount"),
        "level3_soil_percent": _fugacity_medium_value(data, "Soil", "MassAmount"),
        "level3_sediment_percent": _fugacity_medium_value(data, "Sediment", "MassAmount"),
        "level3_air_half_life_hours": _fugacity_medium_value(data, "Air", "HalfLife"),
        "level3_water_half_life_hours": _fugacity_medium_value(data, "Water", "HalfLife"),
        "level3_soil_half_life_hours": _fugacity_medium_value(data, "Soil", "HalfLife"),
        "level3_sediment_half_life_hours": _fugacity_medium_value(data, "Sediment", "HalfLife"),
        "level3_persistence_hours": _value_at(data, "fugacityModel.model.Persistence"),
        "river_volatilization_half_life_hours": _value_at(data, "waterVolatilization.riverHalfLifeHours"),
        "lake_volatilization_half_life_hours": _value_at(data, "waterVolatilization.lakeHalfLifeHours"),
    }


def parse_uploaded_result(uploaded_file):
    name = uploaded_file.name
    suffix = Path(name).suffix.lower()
    raw = uploaded_file.getvalue()

    if suffix in {".xlsx", ".xls"}:
        return parse_table_result(pd.read_excel(io.BytesIO(raw)), source_name=name)
    if suffix == ".csv":
        return parse_table_result(pd.read_csv(io.BytesIO(raw)), source_name=name)

    text = extract_text(raw)
    return parse_text_result(text, source_name=name)


def parse_table_result(df, source_name="uploaded_table"):
    normalized_columns = {_normalize_key(col): col for col in df.columns}
    mapped = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_key = _normalize_key(alias)
            if alias_key in normalized_columns:
                mapped[target] = normalized_columns[alias_key]
                break

    rows = []
    warnings = []
    for idx, row in df.iterrows():
        parsed = {"source_file": source_name}
        parsed["source_row"] = idx + 2
        parsed["compound"] = _safe_value(row.get(mapped.get("compound"), pd.NA))
        parsed["smiles"] = _safe_value(row.get(mapped.get("smiles"), pd.NA))
        parsed["cas"] = _safe_value(row.get(mapped.get("cas"), pd.NA))
        for key in ENDPOINT_KEYS:
            parsed[key] = _safe_numeric(row.get(mapped.get(key), pd.NA))
        rows.append(parsed)

    missing_endpoint_cols = [key for key in ENDPOINT_KEYS if key not in mapped]
    if missing_endpoint_cols:
        warnings.append(
            {
                "source_file": source_name,
                "warning": "以下目标指标没有在表格列名中识别到：" + ", ".join(missing_endpoint_cols),
            }
        )

    return pd.DataFrame(rows), pd.DataFrame(warnings)


def parse_text_result(text, source_name="uploaded_text"):
    sections = split_episuite_sections(text)
    rows = []
    warnings = []

    for idx, section in enumerate(sections, start=1):
        parsed = {
            "source_file": source_name,
            "source_section": idx,
            "compound": _extract_label(section, [r"Chemical\s+Name\s*[:=]\s*(.+)"]),
            "smiles": _extract_label(section, [r"SMILES\s+Notation\s*[:=]\s*(.+)", r"SMILES\s*[:=]\s*(.+)"]),
            "cas": _extract_label(section, [r"CAS(?:\s+Registry)?(?:\s+Number)?\s*[:=]\s*(.+)"]),
        }
        for key in ENDPOINT_KEYS:
            parsed[key] = _extract_numeric(section, TEXT_PATTERNS[key])
        rows.append(parsed)

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        warnings.append({"source_file": source_name, "warning": "没有识别到可解析的 EPI Suite 文本段。"})
    else:
        endpoint_hits = result_df[ENDPOINT_KEYS].notna().sum().sum()
        if endpoint_hits == 0:
            warnings.append(
                {
                    "source_file": source_name,
                    "warning": "已读取文本，但没有匹配到目标环境归趋指标。请优先上传 CSV、Excel 或从 EPI Suite 复制完整结果文本。",
                }
            )

    return result_df, pd.DataFrame(warnings)


def merge_results_with_input(input_df, parsed_df):
    if parsed_df is None or parsed_df.empty:
        return build_empty_result_template(input_df)

    clean_input = input_df[input_columns_for_display(input_df)].copy()
    parsed = parsed_df.copy()

    for col in ["compound", "smiles"]:
        if col in parsed.columns:
            parsed[col] = parsed[col].astype("string").str.strip()

    if "compound" in parsed.columns and parsed["compound"].notna().any():
        merged = clean_input.merge(
            parsed.drop(columns=["smiles"], errors="ignore"),
            on="compound",
            how="left",
            suffixes=("", "_parsed"),
        )
    elif "smiles" in parsed.columns and parsed["smiles"].notna().any():
        merged = clean_input.merge(
            parsed.drop(columns=["compound"], errors="ignore"),
            on="smiles",
            how="left",
            suffixes=("", "_parsed"),
        )
    else:
        merged = clean_input.copy()
        for key in ENDPOINT_KEYS:
            merged[key] = parsed[key].iloc[0] if key in parsed.columns and len(parsed) == 1 else pd.NA

    for key in ENDPOINT_KEYS:
        if key not in merged.columns:
            merged[key] = pd.NA
    return merged


def slim_epi_report_columns(df):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    hidden_columns = [col for col in EPI_REPORT_HIDDEN_COMPAT_COLUMNS if col in df.columns]
    if not hidden_columns:
        return df
    return df.drop(columns=hidden_columns)


def build_epi_web_result_tables(core_df=None, raw_df=None, warnings_df=None):
    core = slim_epi_report_columns(_normalize_table(core_df))
    raw = _normalize_table(raw_df)
    warnings = _normalize_warning_table(warnings_df)

    properties_rows = []
    degradation_rows = []
    fate_rows = []
    bioaccumulation_rows = []
    ecosar_rows = []
    metadata_rows = []

    for _, raw_row in raw.iterrows():
        data = _parse_raw_json(raw_row.get("raw_json"))
        base = _base_epi_identity(raw_row, data)
        properties_rows.append(_build_properties_row(base, data))
        degradation_rows.append(_build_degradation_row(base, data))
        fate_rows.append(_build_fate_transport_row(base, data))
        bioaccumulation_rows.append(_build_bioaccumulation_row(base, data))
        ecosar_rows.extend(_build_ecosar_rows(base, data))
        metadata_rows.extend(_build_metadata_rows(base, data))

    return {
        "Core_Summary": core,
        "Properties": slim_epi_report_columns(pd.DataFrame(properties_rows)),
        "Degradation": pd.DataFrame(degradation_rows),
        "Fate_Transport": pd.DataFrame(fate_rows),
        "Bioaccumulation": pd.DataFrame(bioaccumulation_rows),
        "ECOSAR_Aquatic_Toxicity": pd.DataFrame(ecosar_rows),
        "Model_Metadata": pd.DataFrame(metadata_rows),
        "Raw_API_JSON": raw,
        "Warnings": warnings,
    }


def build_result_workbook(
    input_df,
    parsed_df=None,
    merged_df=None,
    warnings_df=None,
    raw_df=None,
    epi_tables=None,
):
    buffer = io.BytesIO()
    if parsed_df is None:
        parsed_df = pd.DataFrame()
    if merged_df is None:
        merged_df = build_empty_result_template(input_df)
    if warnings_df is None:
        warnings_df = pd.DataFrame(columns=["source_file", "warning"])
    elif warnings_df.empty and len(warnings_df.columns) == 0:
        warnings_df = pd.DataFrame(columns=["source_file", "warning"])
    if epi_tables is None:
        epi_tables = build_epi_web_result_tables(merged_df, raw_df, warnings_df)

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        input_df[input_columns_for_display(input_df)].to_excel(writer, sheet_name="Validated_Input", index=False)
        for sheet_name in EPI_WEB_RESULT_SHEETS:
            table = epi_tables.get(sheet_name, pd.DataFrame())
            table.to_excel(writer, sheet_name=sheet_name, index=False)
        if raw_df is None and parsed_df is not None and not parsed_df.empty:
            parsed_df.to_excel(writer, sheet_name="Parsed_Raw_Results", index=False)
    buffer.seek(0)
    return buffer


def _normalize_table(df):
    if df is None:
        return pd.DataFrame()
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame()
    return df.copy()


def _normalize_warning_table(df):
    if df is None:
        return pd.DataFrame(columns=["compound", "smiles", "cas", "warning"])
    warnings = df.copy()
    if warnings.empty and len(warnings.columns) == 0:
        return pd.DataFrame(columns=["compound", "smiles", "cas", "warning"])
    if "error" in warnings.columns and "warning" not in warnings.columns:
        warnings = warnings.rename(columns={"error": "warning"})
    if "warning" not in warnings.columns:
        warnings["warning"] = pd.NA
    return warnings


def _parse_raw_json(raw_json):
    if not _clean_optional_text(raw_json):
        return {}
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _base_epi_identity(raw_row, data):
    chemical = data.get("chemicalProperties", {})
    return {
        "compound": _clean_optional_text(raw_row.get("compound")),
        "smiles": _clean_optional_text(raw_row.get("smiles")),
        "cas": _clean_optional_text(raw_row.get("cas")),
        "epi_name": chemical.get("name"),
        "epi_cas": chemical.get("cas") or raw_row.get("epi_cas"),
        "epi_smiles": chemical.get("smiles") or raw_row.get("epi_smiles"),
    }


def _build_properties_row(base, data):
    chemical = data.get("chemicalProperties", {})
    row = {
        **base,
        "epi_systematic_name": chemical.get("systematicName"),
        "molecular_formula": chemical.get("molecularFormula"),
        "molecular_weight": chemical.get("molecularWeight"),
        "organic": chemical.get("organic"),
        "flags": chemical.get("flags"),
    }
    for prefix, section in [
        ("log_kow", "logKow"),
        ("log_koa", "logKoa"),
        ("melting_point", "meltingPoint"),
        ("boiling_point", "boilingPoint"),
        ("vapor_pressure", "vaporPressure"),
        ("henry", "henrysLawConstant"),
        ("log_koc", "logKoc"),
        ("aerosol_adsorption_fraction", "aerosolAdsorptionFraction"),
    ]:
        row.update(_selected_estimated_experimental_columns(prefix, data, section))
    row.update(
        {
            "water_solubility_waternt_selected": _value_at(data, "waterSolubilityFromWaterNt.selectedValue.value"),
            "water_solubility_waternt_estimated": _value_at(data, "waterSolubilityFromWaterNt.estimatedValue.value"),
            "water_solubility_waternt_experimental": _experimental_or_selected_value(data, "waterSolubilityFromWaterNt"),
            "water_solubility_wskow_selected": _value_at(data, "waterSolubilityFromLogKow.selectedValue.value"),
            "water_solubility_wskow_estimated": _value_at(data, "waterSolubilityFromLogKow.estimatedValue.value"),
            "water_solubility_wskow_experimental": _experimental_or_selected_value(data, "waterSolubilityFromLogKow"),
            "dermal_permeability_coefficient": _value_at(data, "dermalPermeability.permeabilityCoefficient.value"),
            "dermal_absorbed_dose": _value_at(data, "dermalPermeability.absorbedDose.value"),
            "dermal_lag_time": _value_at(data, "dermalPermeability.lagTime.value"),
        }
    )
    return row


def _build_degradation_row(base, data):
    row = {
        **base,
        "atmosphere_half_life_selected": _value_at(data, "atmosphericHalfLife.selectedValue.value"),
        "atmosphere_half_life_estimated": _value_at(data, "atmosphericHalfLife.estimatedValue.value"),
        "atmosphere_oh_rate_constant": _value_at(
            data,
            "atmosphericHalfLife.estimatedHydroxylRadicalReactionRateConstant.value",
        ),
        "atmosphere_ozone_rate_constant": _value_at(
            data,
            "atmosphericHalfLife.estimatedOzoneReactionRateConstant.value",
        ),
        "atmosphere_no3_rate_constant": _value_at(
            data,
            "atmosphericHalfLife.estimatedNitrateRadicalReactionRateConstant.value",
        ),
    }
    for model in data.get("biodegradationRate", {}).get("models", []):
        name = _safe_column_name(model.get("name"))
        if name:
            row[f"biowin_{name}"] = model.get("value", pd.NA)
            row[f"biowin_{name}_description"] = model.get("description", pd.NA)
    row.update(
        {
            "hydrolysis_acid_rate_constant": _value_at(data, "hydrolysis.acidRateConstant.value"),
            "hydrolysis_base_rate_constant": _value_at(data, "hydrolysis.baseRateConstant.value"),
            "hydrolysis_neutral_rate_constant": _value_at(data, "hydrolysis.neutralRateConstant.value"),
            "hydrocarbon_biodegradation_rate": _value_at(data, "hydrocarbonBiodegradationRate.estimatedValue.value"),
        }
    )
    return row


def _build_fate_transport_row(base, data):
    row = {
        **base,
        "stp_total_removal_percent": _value_at(data, "sewageTreatmentModel.model.TotalRemoval.Percent"),
        "stp_final_effluent_percent": _value_at(data, "sewageTreatmentModel.model.FinalEffluent.Percent"),
        "river_volatilization_half_life_hours": _value_at(data, "waterVolatilization.riverHalfLifeHours"),
        "lake_volatilization_half_life_hours": _value_at(data, "waterVolatilization.lakeHalfLifeHours"),
        "level3_persistence_hours": _value_at(data, "fugacityModel.model.Persistence"),
    }
    for medium in ["Air", "Water", "Soil", "Sediment"]:
        prefix = f"level3_{medium.lower()}"
        row[f"{prefix}_percent"] = _fugacity_medium_value(data, medium, "MassAmount")
        row[f"{prefix}_half_life_hours"] = _fugacity_medium_value(data, medium, "HalfLife")
        row[f"{prefix}_reaction_time_hours"] = _fugacity_medium_value(data, medium, "ReactionTime")
        row[f"{prefix}_advection_time_hours"] = _fugacity_medium_value(data, medium, "AdvectionTime")
    return row


def _build_bioaccumulation_row(base, data):
    return {
        **base,
        "bcf": _value_at(data, "bioconcentration.bioconcentrationFactor"),
        "log_bcf": _value_at(data, "bioconcentration.logBioconcentrationFactor"),
        "baf": _value_at(data, "bioconcentration.bioaccumulationFactor"),
        "log_baf": _value_at(data, "bioconcentration.logBioaccumulationFactor"),
        "biotransformation_half_life_days": _value_at(data, "bioconcentration.biotransformationHalfLifeDays"),
        "biotransformation_rate_constant": _value_at(data, "bioconcentration.biotransformationRateConstant"),
        "experimental_bcf": _value_at(data, "bioconcentration.experimentalBioconcentrationFactor"),
    }


def _build_ecosar_rows(base, data):
    rows = []
    for idx, result in enumerate(data.get("ecosar", {}).get("modelResults", []), start=1):
        rows.append(
            {
                **base,
                "result_index": idx,
                "qsar_class": _first_key(result, ["className", "class", "chemicalClass"]),
                "organism": _first_key(result, ["organism", "species"]),
                "duration": _first_key(result, ["duration", "exposureDuration"]),
                "endpoint": _first_key(result, ["endpoint", "effect"]),
                "concentration": _first_key(result, ["concentration", "value"]),
                "units": _first_key(result, ["units", "unit"]),
                "max_log_kow": _first_key(result, ["maxLogKow", "maximumLogKow"]),
                "warnings": _json_or_text(_first_key(result, ["warnings", "warning", "alerts"])),
            }
        )
    return rows


def _build_metadata_rows(base, data):
    rows = []
    for label, section in [
        ("LogKow", "logKow"),
        ("WaterSolubility_WaterNT", "waterSolubilityFromWaterNt"),
        ("WaterSolubility_WSKow", "waterSolubilityFromLogKow"),
        ("VaporPressure", "vaporPressure"),
        ("HenrysLawConstant", "henrysLawConstant"),
        ("LogKoa", "logKoa"),
        ("LogKoc", "logKoc"),
        ("MeltingPoint", "meltingPoint"),
        ("BoilingPoint", "boilingPoint"),
        ("AtmosphericHalfLife", "atmosphericHalfLife"),
        ("AerosolAdsorptionFraction", "aerosolAdsorptionFraction"),
    ]:
        section_data = data.get(section, {})
        if not isinstance(section_data, dict) or not section_data:
            continue
        rows.append(
            {
                **base,
                "model_section": label,
                "selected_value": _value_at(section_data, "selectedValue.value"),
                "selected_type": _value_at(section_data, "selectedValue.valueType"),
                "selected_units": _value_at(section_data, "selectedValue.units"),
                "estimated_value": _value_at(section_data, "estimatedValue.value"),
                "experimental_value": _experimental_or_selected_value(data, section),
                "has_model_output": bool(_value_at(section_data, "estimatedValue.model.output", default="")),
                "analog_count": len(data.get("analogs", []) or []),
            }
        )
    rows.append(
        {
            **base,
            "model_section": "ECOSAR",
            "selected_value": pd.NA,
            "selected_type": pd.NA,
            "selected_units": pd.NA,
            "estimated_value": len(data.get("ecosar", {}).get("modelResults", []) or []),
            "experimental_value": pd.NA,
            "has_model_output": bool(data.get("ecosar", {}).get("output")),
            "analog_count": len(data.get("analogs", []) or []),
        }
    )
    return rows


def _selected_estimated_experimental_columns(prefix, data, section):
    return {
        f"{prefix}_selected": _value_at(data, f"{section}.selectedValue.value"),
        f"{prefix}_estimated": _value_at(data, f"{section}.estimatedValue.value"),
        f"{prefix}_experimental": _experimental_or_selected_value(data, section),
        f"{prefix}_type": _value_at(data, f"{section}.selectedValue.valueType"),
        f"{prefix}_units": _value_at(data, f"{section}.selectedValue.units"),
    }


def _first_key(mapping, keys, default=pd.NA):
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        value = mapping.get(key, default)
        if not _is_missing(value):
            return value
    return default


def _safe_column_name(value):
    text = _clean_optional_text(value)
    if not text:
        return ""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
    return cleaned or ""


def _json_or_text(value):
    if _is_missing(value):
        return pd.NA
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def extract_text(raw):
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = raw.decode(encoding)
            if _looks_like_text(text):
                return text
        except UnicodeDecodeError:
            continue

    chunks = re.findall(rb"[\x09\x0A\x0D\x20-\x7E]{4,}", raw)
    return "\n".join(chunk.decode("latin-1", errors="ignore") for chunk in chunks)


def split_episuite_sections(text):
    cleaned = text.replace("\x00", "\n")
    starts = [match.start() for match in re.finditer(r"EPI\s+Suite\s+Results|SMILES\s+Notation\s*:", cleaned, re.I)]
    if not starts:
        return [cleaned.strip()] if cleaned.strip() else []

    sections = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(cleaned)
        chunk = cleaned[start:end].strip()
        if chunk:
            sections.append(chunk)
    return sections


def _normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _safe_value(value):
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    return text if text else pd.NA


def _safe_numeric(value):
    if pd.isna(value):
        return pd.NA
    if isinstance(value, (int, float)):
        return value
    match = re.search(_NUMBER, str(value))
    return float(match.group(1)) if match else pd.NA


def _clean_optional_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def _extract_numeric(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return pd.NA
    return pd.NA


def _extract_label(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = match.group(1).strip()
            value = re.split(r"[\r\n]", value)[0].strip()
            return value or pd.NA
    return pd.NA


def _looks_like_text(text):
    if not text:
        return False
    printable = sum(ch.isprintable() or ch.isspace() for ch in text[:2000])
    return printable / min(len(text), 2000) > 0.85


def _value_at(data, path, default=pd.NA):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
        if current is None:
            return default
    return current


def _first_value(data, paths, default=pd.NA):
    for path in paths:
        value = _value_at(data, path, default=default)
        if not _is_missing(value):
            return value
    return default


def _first_section_value(data, section_names, value_path, default=pd.NA):
    for section_name in section_names:
        value = _value_at(data, f"{section_name}.{value_path}", default=default)
        if not _is_missing(value):
            return value
    return default


def _experimental_or_selected_value(data, section_name, default=pd.NA):
    experimental_value = _value_at(data, f"{section_name}.experimentalValue.value", default=default)
    if not _is_missing(experimental_value):
        return experimental_value

    selected_type = _value_at(data, f"{section_name}.selectedValue.valueType", default="")
    if str(selected_type).upper() == "EXPERIMENTAL":
        return _value_at(data, f"{section_name}.selectedValue.value", default=default)
    return default


def _first_experimental_or_selected_value(data, section_names, default=pd.NA):
    for section_name in section_names:
        value = _experimental_or_selected_value(data, section_name, default=default)
        if not _is_missing(value):
            return value
    return default


def _is_missing(value):
    if value is None or value is pd.NA:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(result, bool):
        return result
    return False


def _biowin_model_value(data, model_name):
    for model in data.get("biodegradationRate", {}).get("models", []):
        if model.get("name") == model_name:
            return model.get("value", pd.NA)
    return pd.NA


def _fugacity_medium_value(data, medium, key):
    values = data.get("fugacityModel", {}).get("model", {}).get(medium)
    if not values or not isinstance(values, list) or not values[0]:
        return pd.NA
    return values[0].get(key, pd.NA)
