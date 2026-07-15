from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import matplotlib.pyplot as plt
import pandas as pd

from src.comptox_use import (
    build_functional_use_table,
    build_product_use_table,
    run_comptox_use_batch,
)
from src.cp_screening_workflow import (
    build_detection_frequency,
    build_group_area_mean_by_sample,
    build_pbm_toxpi_input,
    build_peak_area_long,
    calculate_pbm_toxpi,
)
from src.echa_ghs import run_echa_ghs_batch
from src.echa_use import DEFAULT_ECHA_BASE, run_echa_use_batch
from src.episuite_io import DEFAULT_EPI_WEB_API, run_epi_web_batch
from src.identifier_resolver import DEFAULT_PUBCHEM_BASE, REQUIRED_IDENTIFIER_COLUMNS, run_identifier_completion_batch
from src.mol_structure_parser import find_mol_text_column, prepare_structure_dataframe
from src.pov_lrtp_replica import run_pov_lrtp_batch
from src.plot_style import configure_plot_style
from src.r_screening_replica.pipeline import run_screening_pipeline
from src.r_screening_replica.schema import ScreeningConfig
from src.source_origin import run_source_origin_batch
from src.use_rose_plot import (
    build_compound_universe,
    extract_candidate_use_plot_data,
    extract_reported_functional_use_presence_data,
    extract_source_origin_pie_data,
    extract_top_predicted_functional_use_data,
    extract_top_reported_functional_use_data,
    figure_to_pdf_bytes,
    figure_to_png_bytes,
    generate_compound_classification_pie_plot,
    generate_reported_functional_use_pie_plot,
    generate_reported_functional_use_presence_plot,
    generate_top_predicted_functional_use_pie_plot,
    generate_use_rose_plot,
)


R_DF_STEP_LABEL = "化学类型图、DBE图、VK图与 DF"

AUTO_WORKFLOW_EXPORT_MODULES = (
    (
        "01_Local_Screening",
        "Local_Screening_Results.xlsx",
        (
            "Structure_Preparation",
            "Input_Check",
            "Elemental_Ratios_DBE",
            "Category_Summary",
            "DF_Table",
            "Sample_Peak_Area",
            "Group_Area_Raw_Long",
            "Group_Area_Mean_By_Sample",
            "Plot_Warnings",
        ),
        ("Local_",),
    ),
    (
        "02_Identifier_Completion",
        "Identifier_Completion_Results.xlsx",
        ("Identifier_Completion", "Identifier_Warnings"),
        (),
    ),
    (
        "03_EPI_Suite",
        "EPI_Suite_Results.xlsx",
        ("EPI_Results", "EPI_Raw_Results", "EPI_Errors"),
        (),
    ),
    (
        "04_EPA_CompTox",
        "EPA_CompTox_Results.xlsx",
        (
            "CompTox_Summary",
            "Product_Use_Categories",
            "Functional_Uses_Predicted",
            "Functional_Uses_Reported",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "CompTox_Errors",
        ),
        ("EPA_",),
    ),
    (
        "05_ECHA",
        "ECHA_Results.xlsx",
        (
            "ECHA_Use_Summary",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "ECHA_Use_Dossiers",
            "ECHA_Use_Errors",
            "ECHA_GHS_Summary",
            "ECHA_GHS_Classifications",
            "ECHA_GHS_Errors",
        ),
        ("ECHA_",),
    ),
    (
        "06_Source_Origin",
        "Source_Origin_Results.xlsx",
        (
            "Source_Origin_Summary",
            "Source_Origin_Evidence",
            "Source_Origin_Errors",
            "Source_Origin_Pie_Data",
        ),
        ("Source_",),
    ),
    (
        "07_Pov_LRTP_PBM_ToxPi",
        "Pov_LRTP_PBM_ToxPi_Results.xlsx",
        ("Pov_LRTP_Input", "Pov_LRTP", "ToxPi_Input", "ToxPi_Normalized", "ToxPi_Results"),
        (),
    ),
)

INTERNAL_TABLE_NAMES = {"CompTox_Candidates", "ECHA_Use_Candidates"}


@dataclass(frozen=True)
class AutoWorkflowMapping:
    compound_col: str = "Name"
    formula_col: str = "NIST Lib Hit Formula"
    peak_area_col: str = "Avg TIC"
    group_area_cols: list[str] = field(default_factory=list)
    mol_column: str | None = None
    smiles_col: str | None = None
    cas_col: str | None = None


@dataclass(frozen=True)
class AutoWorkflowConfig:
    mapping: AutoWorkflowMapping | None = None
    run_r_replicate_df: bool = True
    run_identifier: bool = True
    run_epi: bool = False
    run_comptox: bool = False
    run_echa_use: bool = False
    run_echa_ghs: bool = False
    run_source_origin: bool = False
    run_pov_lrtp_toxpi: bool = False
    detection_threshold: float = 1e5
    use_pubchem: bool = True
    use_epa_identifier: bool = True
    use_echa_identifier: bool = True
    use_chemspider: bool = False
    chemspider_api_key: str | None = None
    pubchem_base: str = DEFAULT_PUBCHEM_BASE
    echa_base: str = DEFAULT_ECHA_BASE
    epi_api_url: str = DEFAULT_EPI_WEB_API
    identifier_timeout: int = 60
    epi_timeout: int = 90
    use_timeout: int = 45
    echa_timeout: int = 90
    source_origin_timeout: int = 60
    identifier_delay_seconds: float = 0.2
    epi_delay_seconds: float = 0.2
    use_delay_seconds: float = 0.2
    echa_delay_seconds: float = 0.5
    source_origin_delay_seconds: float = 0.2
    identifier_max_workers: int = 3
    epi_max_workers: int = 3
    comptox_max_workers: int = 3
    echa_max_workers: int = 2
    echa_ghs_max_workers: int = 2
    source_origin_max_workers: int = 2
    cache_enabled: bool = True


@dataclass(frozen=True)
class AutoWorkflowChart:
    title: str
    png: bytes
    pdf: bytes


@dataclass
class AutoWorkflowResult:
    mapping: AutoWorkflowMapping
    representative_table: pd.DataFrame
    tables: OrderedDict[str, pd.DataFrame]
    step_status: pd.DataFrame
    warnings: pd.DataFrame
    charts: OrderedDict[str, AutoWorkflowChart] = field(default_factory=OrderedDict)


@dataclass
class LocalScreeningOutput:
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, AutoWorkflowChart]
    warnings: list[str] = field(default_factory=list)


ProgressCallback = Callable[[str, int, int, str], None]
ActivityCallback = Callable[[dict], None]


def read_input_workbook(file_or_path, sheet_name=0) -> pd.DataFrame:
    frame = pd.read_excel(file_or_path, sheet_name=sheet_name)
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def detect_default_mapping(columns) -> AutoWorkflowMapping:
    columns = [str(column).strip() for column in columns]
    compound_col = _first_existing(columns, ["Name", "compound", "Compound", "Chemical name"])
    formula_col = _first_existing(columns, ["NIST Lib Hit Formula", "formula", "Formula", "Molecular Formula"])
    peak_area_col = _first_existing(columns, ["Avg TIC", "Group_Area", "Peak_Area", "Peak area", "Area"])
    group_area_cols = [column for column in columns if _is_group_area_column(column)]
    mol_column = find_mol_text_column(columns)
    smiles_col = _first_existing(columns, ["smiles", "SMILES", "canonical_smiles"], default=None)
    cas_col = _first_existing(columns, ["cas", "CAS", "CASRN", "CAS No."], default=None)
    return AutoWorkflowMapping(
        compound_col=compound_col or (columns[0] if columns else ""),
        formula_col=formula_col or (columns[0] if columns else ""),
        peak_area_col=peak_area_col or (group_area_cols[0] if group_area_cols else (columns[0] if columns else "")),
        group_area_cols=group_area_cols,
        mol_column=mol_column,
        smiles_col=smiles_col,
        cas_col=cas_col,
    )


def run_auto_query_workflow(
    input_df: pd.DataFrame,
    config: AutoWorkflowConfig | None = None,
    progress_callback: ProgressCallback | None = None,
    activity_callback: ActivityCallback | None = None,
) -> AutoWorkflowResult:
    config = config or AutoWorkflowConfig()
    mapping = config.mapping or detect_default_mapping(input_df.columns)
    audit_columns = {"parse_status", "smiles_source", "smiles_decision_warning"}
    if audit_columns.issubset(input_df.columns):
        prepared_input = input_df.copy()
    else:
        prepared_input = prepare_structure_dataframe(
            input_df,
            mol_column=mapping.mol_column,
            smiles_column=mapping.smiles_col,
        )
    normalized = _normalize_input(prepared_input, mapping)
    representative = build_representative_table(normalized, mapping)
    tables: OrderedDict[str, pd.DataFrame] = OrderedDict()
    charts: OrderedDict[str, AutoWorkflowChart] = OrderedDict()
    tables["Structure_Preparation"] = prepared_input
    status_rows = []
    warning_rows = []
    plot_warnings = configure_plot_style()
    for message in plot_warnings:
        warning_rows.append({"stage": "Plot style", "message": str(message)})
    if plot_warnings:
        tables["Plot_Warnings"] = pd.DataFrame({"warning": plot_warnings})

    completed_identifiers = pd.DataFrame()
    identifier_warnings = pd.DataFrame()
    epi_results = pd.DataFrame()
    epi_raw_results = pd.DataFrame()
    epi_errors = pd.DataFrame()
    comptox_summary = pd.DataFrame()
    comptox_candidates = pd.DataFrame()
    echa_summary = pd.DataFrame()
    echa_candidates = pd.DataFrame()
    echa_dossiers = pd.DataFrame()

    def record(step, status, rows=0, message=""):
        status_rows.append({"step": step, "status": status, "rows": int(rows or 0), "message": message})
        if activity_callback:
            activity_callback(
                {
                    "event": "stage_finished",
                    "step": step,
                    "status": status,
                    "rows": int(rows or 0),
                    "message": message,
                }
            )

    def activity_for(step, timeout_seconds):
        def forward(event):
            if activity_callback:
                activity_callback(
                    {
                        **event,
                        "step": step,
                        "timeout_seconds": int(timeout_seconds),
                    }
                )

        return forward

    def add_warning(stage, message):
        warning_rows.append({"stage": stage, "message": str(message)})

    def run_step(step, func):
        try:
            value = func()
        except Exception as exc:
            record(step, "失败", 0, str(exc))
            add_warning(step, str(exc))
            return None
        return value

    run_local_r_df = config.run_r_replicate_df or config.run_pov_lrtp_toxpi
    if run_local_r_df:
        local_value = run_step(
            R_DF_STEP_LABEL,
            lambda: _run_r_replicate_df(normalized, mapping, config.detection_threshold),
        )
        if local_value is not None:
            for key, table in local_value.tables.items():
                tables[key] = table
            charts.update(local_value.charts)
            for message in local_value.warnings:
                add_warning(R_DF_STEP_LABEL, message)
            record(R_DF_STEP_LABEL, "完成", len(local_value.tables.get("DF_Table", pd.DataFrame())))

    needs_identifier = any(
        [
            config.run_identifier,
            config.run_epi,
            config.run_comptox,
            config.run_echa_use,
            config.run_echa_ghs,
            config.run_source_origin,
            config.run_pov_lrtp_toxpi,
        ]
    )
    if needs_identifier:
        identifier_input = _build_identifier_input(representative)
        tables["Identifier_Input"] = identifier_input

        def identifier_progress(done, total, label):
            if progress_callback:
                progress_callback("标识符补全", done, total, label)

        identifier_value = run_step(
            "标识符补全",
            lambda: run_identifier_completion_batch(
                identifier_input,
                use_pubchem=config.use_pubchem,
                use_epa=config.use_epa_identifier or config.run_comptox or config.run_source_origin,
                use_echa=config.use_echa_identifier or config.run_echa_use or config.run_echa_ghs or config.run_source_origin,
                use_chemspider=config.use_chemspider,
                chemspider_api_key=config.chemspider_api_key,
                pubchem_base=config.pubchem_base,
                echa_base=config.echa_base,
                timeout=int(config.identifier_timeout),
                delay_seconds=float(config.identifier_delay_seconds),
                max_workers=int(config.identifier_max_workers),
                cache_enabled=bool(config.cache_enabled),
                progress_callback=identifier_progress,
                activity_callback=activity_for("标识符补全", config.identifier_timeout),
            ),
        )
        if identifier_value is not None:
            completed_identifiers, identifier_warnings = identifier_value
            tables["Identifier_Completion"] = completed_identifiers
            tables["Identifier_Warnings"] = identifier_warnings
            record("标识符补全", "完成", len(completed_identifiers))

    query_input = _query_input_from_identifiers(completed_identifiers)
    compound_universe = build_compound_universe(query_input)

    run_epi_step = config.run_epi or config.run_pov_lrtp_toxpi
    if run_epi_step:
        if query_input.empty:
            record("EPI Suite 环境归趋", "跳过", 0, "缺少可用于 EPI 的 SMILES。")
        else:
            epi_input = query_input.loc[query_input["smiles"].ne(""), ["compound", "smiles", "cas"]].reset_index(drop=True)
            tables["EPI_Input"] = epi_input

            def epi_progress(done, total, label):
                if progress_callback:
                    progress_callback("EPI Suite 环境归趋", done, total, label)

            epi_value = run_step(
                "EPI Suite 环境归趋",
                lambda: run_epi_web_batch(
                    epi_input,
                    api_url=config.epi_api_url,
                    timeout=int(config.epi_timeout),
                    delay_seconds=float(config.epi_delay_seconds),
                    max_workers=int(config.epi_max_workers),
                    cache_enabled=bool(config.cache_enabled),
                    progress_callback=epi_progress,
                    activity_callback=activity_for("EPI Suite 环境归趋", config.epi_timeout),
                ),
            )
            if epi_value is not None:
                epi_results, epi_raw_results, epi_errors = epi_value
                tables["EPI_Results"] = epi_results
                tables["EPI_Raw_Results"] = epi_raw_results
                tables["EPI_Errors"] = epi_errors
                record("EPI Suite 环境归趋", "完成", len(epi_results))

    if config.run_comptox:
        comptox_value = run_step(
            "EPA CompTox 用途",
            lambda: run_comptox_use_batch(
                query_input,
                timeout=int(config.use_timeout),
                delay_seconds=float(config.use_delay_seconds),
                dashboard_fallback=True,
                max_workers=int(config.comptox_max_workers),
                cache_enabled=bool(config.cache_enabled),
                activity_callback=activity_for("EPA CompTox 用途", config.use_timeout),
            ),
        )
        if comptox_value is not None:
            comptox_summary, comptox_candidates, comptox_errors = comptox_value
            tables["CompTox_Summary"] = comptox_summary
            tables["CompTox_Candidates"] = comptox_candidates
            tables["Product_Use_Categories"] = build_product_use_table(comptox_candidates)
            tables["Functional_Uses_Predicted"] = build_functional_use_table(
                comptox_candidates,
                functional_source="predicted",
            )
            tables["Functional_Uses_Reported"] = build_functional_use_table(
                comptox_candidates,
                functional_source="reported",
            )
            tables["EPA_Predicted_Pie_Data"] = extract_top_predicted_functional_use_data(
                comptox_candidates,
                compound_universe=compound_universe,
            )
            tables["EPA_Reported_Pie_Data"] = extract_top_reported_functional_use_data(
                comptox_candidates,
                compound_universe,
                source_label="EPA FC reported",
                source_type="functional_use",
                use_key="raw",
                require_reported_flag=True,
            )
            tables["CompTox_Errors"] = comptox_errors
            record("EPA CompTox 用途", "完成", len(comptox_summary))

    if config.run_echa_use:
        echa_value = run_step(
            "ECHA REACH 用途",
            lambda: run_echa_use_batch(
                query_input,
                base_url=config.echa_base,
                timeout=int(config.echa_timeout),
                delay_seconds=float(config.echa_delay_seconds),
                max_workers=int(config.echa_max_workers),
                cache_enabled=bool(config.cache_enabled),
                activity_callback=activity_for("ECHA REACH 用途", config.echa_timeout),
            ),
        )
        if echa_value is not None:
            echa_summary, echa_candidates, echa_dossiers, echa_errors = echa_value
            tables["ECHA_Use_Summary"] = echa_summary
            tables["ECHA_Use_Candidates"] = echa_candidates
            tables["ECHA_Uses_Reported"] = echa_candidates.copy()
            tables["ECHA_Reported_Pie_Data"] = extract_top_reported_functional_use_data(
                echa_candidates,
                compound_universe,
                source_label="ECHA reported",
                use_key="category",
                require_reported_flag=False,
            )
            tables["ECHA_Use_Dossiers"] = echa_dossiers
            tables["ECHA_Use_Errors"] = echa_errors
            record("ECHA REACH 用途", "完成", len(echa_summary))

    if config.run_echa_ghs:
        ghs_value = run_step(
            "ECHA GHS/C&L 危害",
            lambda: run_echa_ghs_batch(
                query_input,
                base_url=config.echa_base,
                timeout=int(config.echa_timeout),
                delay_seconds=float(config.echa_delay_seconds),
                max_workers=int(config.echa_ghs_max_workers),
                cache_enabled=bool(config.cache_enabled),
                activity_callback=activity_for("ECHA GHS/C&L 危害", config.echa_timeout),
            ),
        )
        if ghs_value is not None:
            ghs_summary, ghs_classifications, ghs_errors = ghs_value
            tables["ECHA_GHS_Summary"] = ghs_summary
            tables["ECHA_GHS_Classifications"] = ghs_classifications
            tables["ECHA_GHS_Errors"] = ghs_errors
            record("ECHA GHS/C&L 危害", "完成", len(ghs_summary))

    if config.run_source_origin:
        source_value = run_step(
            "来源属性评估",
            lambda: run_source_origin_batch(
                query_input,
                comptox_summary_df=comptox_summary,
                comptox_candidates_df=comptox_candidates,
                echa_summary_df=echa_summary,
                echa_candidates_df=echa_candidates,
                echa_dossiers_df=echa_dossiers,
                echa_base=config.echa_base,
                timeout=int(config.source_origin_timeout),
                delay_seconds=float(config.source_origin_delay_seconds),
                max_workers=int(config.source_origin_max_workers),
                cache_enabled=bool(config.cache_enabled),
                activity_callback=activity_for("来源属性评估", config.source_origin_timeout),
            ),
        )
        if source_value is not None:
            source_summary, source_evidence, source_errors = source_value
            tables["Source_Origin_Summary"] = source_summary
            tables["Source_Origin_Evidence"] = source_evidence
            tables["Source_Origin_Errors"] = source_errors
            tables["Source_Origin_Pie_Data"] = extract_source_origin_pie_data(
                source_summary,
                compound_universe,
            )
            record("来源属性评估", "完成", len(source_summary))

    if config.run_pov_lrtp_toxpi:
        toxpi_value = run_step(
            "Pov-LRTP / PBM / ToxPi",
            lambda: _run_pov_lrtp_toxpi(representative, completed_identifiers, epi_results, tables),
        )
        if toxpi_value is not None:
            for key, table in toxpi_value.items():
                tables[key] = table
            record("Pov-LRTP / PBM / ToxPi", "完成", len(toxpi_value.get("ToxPi_Results", pd.DataFrame())))

    warnings = pd.DataFrame(warning_rows, columns=["stage", "message"])
    tables["Warnings"] = warnings
    step_status = pd.DataFrame(status_rows, columns=["step", "status", "rows", "message"])
    return AutoWorkflowResult(
        mapping=mapping,
        representative_table=representative,
        tables=tables,
        step_status=step_status,
        warnings=warnings,
        charts=charts,
    )


def build_auto_workflow_workbook(result: AutoWorkflowResult) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        result.step_status.to_excel(writer, sheet_name="Run_Log", index=False)
        result.representative_table.to_excel(writer, sheet_name="Representative_Input", index=False)
        for name, table in result.tables.items():
            if name in INTERNAL_TABLE_NAMES:
                continue
            sheet_name = _safe_sheet_name(name)
            (table if table is not None else pd.DataFrame()).to_excel(writer, sheet_name=sheet_name, index=False)
    buffer.seek(0)
    return buffer


def _build_module_workbook(result: AutoWorkflowResult, table_names: tuple[str, ...]) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name in table_names:
            if name in INTERNAL_TABLE_NAMES:
                continue
            table = result.tables.get(name)
            if isinstance(table, pd.DataFrame) and not table.empty:
                table.to_excel(writer, sheet_name=_safe_sheet_name(name), index=False)
    buffer.seek(0)
    return buffer


def _module_chart_file_name(chart_key: str) -> str:
    return chart_key.removeprefix("Local_")


def build_auto_workflow_charts(result: AutoWorkflowResult) -> OrderedDict[str, AutoWorkflowChart]:
    charts: OrderedDict[str, AutoWorkflowChart] = OrderedDict(result.charts)
    for source_config in _auto_workflow_chart_sources(result):
        chart_df = _build_chart_data(source_config)
        if chart_df.empty:
            continue
        fig = None
        try:
            fig = _build_chart_figure(chart_df, source_config)
            charts[source_config["file_prefix"]] = AutoWorkflowChart(
                title=source_config["title"],
                png=figure_to_png_bytes(fig).getvalue(),
                pdf=figure_to_pdf_bytes(fig).getvalue(),
            )
        finally:
            if fig is not None:
                plt.close(fig)
    return charts


def build_auto_workflow_zip(
    result: AutoWorkflowResult,
    charts: OrderedDict[str, AutoWorkflowChart] | None = None,
) -> io.BytesIO:
    charts = charts if charts is not None else build_auto_workflow_charts(result)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Auto_Query_Workflow_Results.xlsx", build_auto_workflow_workbook(result).getvalue())
        for folder, workbook_name, table_candidates, chart_prefixes in AUTO_WORKFLOW_EXPORT_MODULES:
            table_names = tuple(
                name
                for name in table_candidates
                if isinstance(result.tables.get(name), pd.DataFrame) and not result.tables[name].empty
            )
            chart_keys = tuple(
                key
                for key in charts
                if any(key.startswith(prefix) for prefix in chart_prefixes)
            )
            if not table_names and not chart_keys:
                continue
            if table_names:
                workbook = _build_module_workbook(result, table_names)
                archive.writestr(f"{folder}/{workbook_name}", workbook.getvalue())
            for key in chart_keys:
                stem = _module_chart_file_name(key)
                archive.writestr(f"{folder}/figures/{stem}.png", charts[key].png)
                archive.writestr(f"{folder}/figures/{stem}.pdf", charts[key].pdf)
    buffer.seek(0)
    return buffer


def build_representative_table(input_df: pd.DataFrame, mapping: AutoWorkflowMapping) -> pd.DataFrame:
    frame = pd.DataFrame()
    frame["Name"] = input_df[mapping.compound_col].map(_clean_text) if mapping.compound_col in input_df.columns else ""
    frame["formula"] = input_df[mapping.formula_col] if mapping.formula_col in input_df.columns else pd.NA
    area_cols = [column for column in mapping.group_area_cols if column in input_df.columns]
    if area_cols:
        frame["Group_Area"] = input_df[area_cols].apply(pd.to_numeric, errors="coerce").mean(axis=1, skipna=True)
    elif mapping.peak_area_col in input_df.columns:
        frame["Group_Area"] = pd.to_numeric(input_df[mapping.peak_area_col], errors="coerce")
    else:
        frame["Group_Area"] = pd.NA
    if "smiles" in input_df.columns:
        frame["SMILES_input"] = input_df["smiles"]
    if mapping.cas_col and mapping.cas_col in input_df.columns:
        frame["CAS_input"] = input_df[mapping.cas_col]
    frame["compound_key"] = frame["Name"].map(_compound_key)
    frame = frame.loc[frame["compound_key"].ne("")].copy()
    frame = frame.sort_values("Group_Area", ascending=False, na_position="last")
    return frame.drop_duplicates("compound_key", keep="first").reset_index(drop=True)


def _auto_workflow_chart_sources(result: AutoWorkflowResult) -> list[dict]:
    chart_sources = []
    comptox_candidates = result.tables.get("CompTox_Candidates")
    if isinstance(comptox_candidates, pd.DataFrame) and not comptox_candidates.empty:
        chart_sources.extend(
            [
                {
                    "chart_type": "rose",
                    "source_label": "EPA PUC",
                    "candidates_df": comptox_candidates,
                    "source_type": "product_category",
                    "use_key": "raw",
                    "title": "EPA CompTox Product-Use Category Rose Plot",
                    "file_prefix": "EPA_Product_Use_Category_Rose_Plot",
                },
                {
                    "chart_type": "reported_presence",
                    "source_label": "EPA FC reported",
                    "candidates_df": comptox_candidates,
                    "title": "EPA CompTox Reported Functional Use Evidence",
                    "file_prefix": "EPA_Reported_Functional_Use_Evidence",
                },
            ]
        )

    predicted_pie = result.tables.get("EPA_Predicted_Pie_Data")
    if isinstance(predicted_pie, pd.DataFrame) and not predicted_pie.empty:
        chart_sources.append(
            {
                "chart_type": "top_predicted_pie",
                "table_df": predicted_pie,
                "title": "EPA CompTox Top Predicted Functional Use Distribution",
                "file_prefix": "EPA_Top_Predicted_Functional_Use",
            }
        )

    epa_reported_pie = result.tables.get("EPA_Reported_Pie_Data")
    if isinstance(epa_reported_pie, pd.DataFrame) and not epa_reported_pie.empty:
        chart_sources.append(
            {
                "chart_type": "classification_pie",
                "table_df": epa_reported_pie,
                "pie_renderer": "reported",
                "title": "EPA CompTox Reported Functional Use Distribution",
                "file_prefix": "EPA_Reported_Functional_Use_Distribution",
            }
        )

    echa_candidates = result.tables.get("ECHA_Use_Candidates")
    if isinstance(echa_candidates, pd.DataFrame) and not echa_candidates.empty:
        chart_sources.append(
            {
                "chart_type": "reported_presence",
                "source_label": "ECHA reported",
                "candidates_df": echa_candidates,
                "source_type": None,
                "use_key": "category",
                "require_reported_flag": False,
                "title": "ECHA REACH Reported Use Evidence",
                "file_prefix": "ECHA_Reported_Use_Evidence",
            }
        )

    echa_reported_pie = result.tables.get("ECHA_Reported_Pie_Data")
    if isinstance(echa_reported_pie, pd.DataFrame) and not echa_reported_pie.empty:
        chart_sources.append(
            {
                "chart_type": "classification_pie",
                "table_df": echa_reported_pie,
                "pie_renderer": "reported",
                "title": "ECHA REACH Reported Use Distribution",
                "file_prefix": "ECHA_Reported_Use_Distribution",
            }
        )

    source_origin_pie = result.tables.get("Source_Origin_Pie_Data")
    if isinstance(source_origin_pie, pd.DataFrame) and not source_origin_pie.empty:
        chart_sources.append(
            {
                "chart_type": "classification_pie",
                "table_df": source_origin_pie,
                "title": "Source Origin Distribution",
                "file_prefix": "Source_Origin_Distribution",
                "fixed_categories": ("Anthropogenic", "Natural", "Both", "Unknown"),
            }
        )
    return chart_sources


def _build_chart_data(source_config: dict) -> pd.DataFrame:
    if source_config["chart_type"] == "classification_pie":
        return source_config["table_df"]
    if source_config["chart_type"] == "top_predicted_pie":
        return source_config["table_df"]
    if source_config["chart_type"] == "reported_presence":
        return extract_reported_functional_use_presence_data(
            source_config["candidates_df"],
            source_label=source_config["source_label"],
            source_type=source_config.get("source_type", "functional_use"),
            use_key=source_config.get("use_key", "raw"),
            require_reported_flag=source_config.get("require_reported_flag", True),
        )
    return extract_candidate_use_plot_data(
        source_config["candidates_df"],
        source_label=source_config["source_label"],
        source_type=source_config.get("source_type"),
        functional_source=source_config.get("functional_source"),
        use_key=source_config.get("use_key", "category"),
    )


def _build_chart_figure(chart_df: pd.DataFrame, source_config: dict):
    if source_config["chart_type"] == "top_predicted_pie":
        return generate_top_predicted_functional_use_pie_plot(chart_df, source_config["title"])
    if source_config["chart_type"] == "classification_pie":
        if source_config.get("pie_renderer") == "reported":
            return generate_reported_functional_use_pie_plot(chart_df, source_config["title"])
        return generate_compound_classification_pie_plot(
            chart_df,
            source_config["title"],
            footnote=source_config.get("footnote"),
            fixed_categories=source_config.get("fixed_categories"),
        )
    if source_config["chart_type"] == "reported_presence":
        return generate_reported_functional_use_presence_plot(chart_df, source_config["title"])
    return generate_use_rose_plot(chart_df, source_config["title"])


LOCAL_SCREENING_FIGURES = (
    (
        "category_percent_donut_with_total",
        "Local_Chemical_Type_Distribution",
        "Chemical Type Distribution",
    ),
    ("compound_bubble_plot", "Local_DBE_Bubble_Plot", "DBE Bubble Plot"),
    ("VanKrevelen", "Local_Van_Krevelen_Plot", "Van Krevelen Plot"),
)


def _load_local_screening_charts(screening_result):
    charts: OrderedDict[str, AutoWorkflowChart] = OrderedDict()
    warnings = []
    for source_key, chart_key, title in LOCAL_SCREENING_FIGURES:
        paths = screening_result.figure_paths.get(source_key, {})
        png_path = paths.get("png")
        pdf_path = paths.get("pdf")
        try:
            png = Path(png_path).read_bytes() if png_path else b""
            pdf = Path(pdf_path).read_bytes() if pdf_path else b""
        except OSError as exc:
            warnings.append(f"{title}: {exc}")
            continue
        if not png.startswith(b"\x89PNG") or not pdf.startswith(b"%PDF"):
            warnings.append(f"{title}: generated PNG/PDF is missing or invalid.")
            continue
        charts[chart_key] = AutoWorkflowChart(title=title, png=png, pdf=pdf)
    return charts, warnings


def _run_r_replicate_df(input_df: pd.DataFrame, mapping: AutoWorkflowMapping, detection_threshold: float):
    area_cols = [column for column in mapping.group_area_cols if column in input_df.columns]
    if not area_cols and mapping.peak_area_col in input_df.columns:
        area_cols = [mapping.peak_area_col]
    if not area_cols:
        raise ValueError("没有找到可用于化学类型图、DBE图、VK图与 DF 的 Group Area 或峰面积列。")

    working = pd.DataFrame()
    working["Name"] = input_df[mapping.compound_col].map(_clean_text) if mapping.compound_col in input_df.columns else ""
    working["formula"] = input_df[mapping.formula_col] if mapping.formula_col in input_df.columns else pd.NA
    for column in area_cols:
        working[column] = input_df[column]
    working["Group_Area_Mean"] = working[area_cols].apply(pd.to_numeric, errors="coerce").mean(axis=1, skipna=True)

    output_dir = Path(tempfile.mkdtemp(prefix="auto_workflow_r_df_"))
    config = ScreeningConfig(
        compound_col="Name",
        formula_col="formula",
        group_area_col="Group_Area_Mean",
        sample_cols=["Group_Area_Mean"],
        output_dir=output_dir,
    )
    screening_result = run_screening_pipeline(_dataframe_to_excel_bytes(working), config=config)
    df_table, sample_peak_area = build_detection_frequency(
        [("Uploaded", working)],
        compound_col="Name",
        peak_area_col=area_cols,
        detection_threshold=detection_threshold,
    )
    group_area_raw_long = build_peak_area_long(
        [("Uploaded", working)],
        compound_col="Name",
        formula_col="formula",
        peak_area_cols=area_cols,
    )
    group_area_mean = build_group_area_mean_by_sample(
        [("Uploaded", working)],
        compound_col="Name",
        formula_col="formula",
        peak_area_cols=area_cols,
    )
    tables = OrderedDict(
        [
            ("Input_Check", screening_result.input_check),
            ("Elemental_Ratios_DBE", screening_result.all_formulas),
            ("Category_Summary", screening_result.category_summary),
            ("DF_Table", df_table),
            ("Sample_Peak_Area", sample_peak_area),
            ("Group_Area_Raw_Long", group_area_raw_long),
            ("Group_Area_Mean_By_Sample", group_area_mean),
        ]
    )
    charts, chart_warnings = _load_local_screening_charts(screening_result)
    return LocalScreeningOutput(tables=tables, charts=charts, warnings=chart_warnings)


def _run_pov_lrtp_toxpi(
    representative: pd.DataFrame,
    completed_identifiers: pd.DataFrame,
    epi_results: pd.DataFrame,
    tables: OrderedDict[str, pd.DataFrame],
):
    if completed_identifiers is None or completed_identifiers.empty:
        raise ValueError("缺少标识符补全结果，无法运行 Pov-LRTP / PBM / ToxPi。")
    if epi_results is None or epi_results.empty:
        raise ValueError("缺少 EPI 结果，无法运行 Pov-LRTP / PBM / ToxPi。")

    from src.r_screening_replica.downstream import build_pov_lrtp_input

    pov_lrtp_input = build_pov_lrtp_input(
        representative,
        completed_identifiers,
        epi_results,
        compound_col="Name",
        formula_col="formula",
        group_area_col="Group_Area",
        sample_cols=[],
    )
    pov_lrtp_results = run_pov_lrtp_batch(pov_lrtp_input)
    df_table = tables.get("DF_Table", pd.DataFrame())
    sample_peak_area = tables.get("Group_Area_Mean_By_Sample", pd.DataFrame())
    toxpi_input = build_pbm_toxpi_input(df_table, pov_lrtp_results, peak_area_long=sample_peak_area)
    normalized_toxpi, toxpi_results = calculate_pbm_toxpi(toxpi_input)
    return OrderedDict(
        [
            ("Pov_LRTP_Input", pov_lrtp_input),
            ("Pov_LRTP", pov_lrtp_results),
            ("ToxPi_Input", toxpi_input),
            ("ToxPi_Normalized", normalized_toxpi),
            ("ToxPi_Results", toxpi_results),
        ]
    )


def _build_identifier_input(representative: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame()
    output["compound"] = representative.get("Name", pd.Series(dtype=object)).map(_clean_text)
    output["smiles"] = representative.get("SMILES_input", pd.Series([""] * len(representative))).map(_clean_text)
    output["cas"] = representative.get("CAS_input", pd.Series([""] * len(representative))).map(_clean_text)
    output["ec"] = ""
    output["dtxsid"] = ""
    output["echa_id"] = ""
    return output[REQUIRED_IDENTIFIER_COLUMNS]


def _query_input_from_identifiers(completed_identifiers: pd.DataFrame) -> pd.DataFrame:
    if completed_identifiers is None or completed_identifiers.empty:
        return pd.DataFrame(columns=REQUIRED_IDENTIFIER_COLUMNS)
    output = completed_identifiers.copy()
    for column in REQUIRED_IDENTIFIER_COLUMNS:
        if column not in output.columns:
            output[column] = ""
        output[column] = output[column].map(_clean_text)
    return output[REQUIRED_IDENTIFIER_COLUMNS].reset_index(drop=True)


def _normalize_input(input_df: pd.DataFrame, mapping: AutoWorkflowMapping) -> pd.DataFrame:
    normalized = input_df.copy()
    normalized.columns = [str(column).strip() for column in normalized.columns]
    missing = [
        column
        for column in [mapping.compound_col, mapping.formula_col]
        if column and column not in normalized.columns
    ]
    if missing:
        raise ValueError(f"输入表缺少必要列：{', '.join(missing)}")
    return normalized


def _dataframe_to_excel_bytes(frame: pd.DataFrame) -> io.BytesIO:
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


def _first_existing(columns, candidates, default=""):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return default


def _is_group_area_column(column) -> bool:
    text = str(column).strip().lower().replace("_", " ")
    return text.startswith("group area")


def _safe_sheet_name(value: str) -> str:
    invalid = set("[]:*?/\\")
    cleaned = "".join("_" if char in invalid else char for char in str(value)).strip()
    return (cleaned or "Sheet")[:31]


def _compound_key(value) -> str:
    return " ".join(_clean_text(value).lower().split())


def _clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text
