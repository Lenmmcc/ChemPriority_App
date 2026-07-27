from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Mapping

import matplotlib.pyplot as plt
import pandas as pd

from src.comptox_use import (
    build_functional_use_table,
    build_product_use_table,
    deduplicate_comptox_candidates,
    run_comptox_use_batch,
)
from src.auto_query_file_views import (
    build_file_module_views,
    file_assignment_warnings,
    safe_export_names,
    scoped_chart_key,
)
from src.cp_screening_workflow import (
    PBMToxPiConfig,
    build_detection_frequency,
    build_group_area_mean_by_sample,
    build_pbm_toxpi_input,
    build_peak_area_long,
    calculate_pbm_toxpi,
    generate_pbm_toxpi_bar_plot,
    generate_pbm_toxpi_robustness_plot,
)
from src.echa_ghs import run_echa_ghs_batch
from src.echa_use import DEFAULT_ECHA_BASE, run_echa_use_batch
from src.episuite_io import DEFAULT_EPI_WEB_API, run_epi_web_batch
from src.episuite_supplement import (
    EPIResolution,
    merge_network_epi,
    prepare_universe,
    resolve_epi_sources,
)
from src.identifier_resolver import DEFAULT_PUBCHEM_BASE, REQUIRED_IDENTIFIER_COLUMNS, run_identifier_completion_batch
from src.mol_structure_parser import find_mol_text_column, prepare_structure_dataframe
from src.multi_file_screening import MultiFileScreeningResult
from src.pov_lrtp_replica import run_pov_lrtp_batch
from src.plot_style import configure_plot_style
from src.r_screening_replica.pipeline import run_screening_pipeline
from src.r_screening_replica.schema import ScreeningAxisRanges, ScreeningConfig
from src.source_origin import run_source_origin_batch
from src.toxpi_calc import generate_r_style_toxpi_plot
from src.use_rose_plot import (
    PRODUCT_USE_CATEGORY_OTHERS_NOTE,
    build_compound_universe,
    extract_candidate_use_plot_data,
    extract_reported_functional_use_presence_data,
    extract_source_origin_pie_data,
    extract_top_product_use_category_data,
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
            "Input_File_Mappings",
            "Input_Check",
            "Elemental_Ratios_DBE",
            "Category_Summary",
            "DF_Table",
            "Sample_Peak_Area",
            "Group_Area_Raw_Long",
            "Group_Area_Mean_By_Sample",
            "Plot_Warnings",
        ),
        (
            "Local_Chemical_Type_Distribution",
            "Local_DBE_Bubble_Plot",
            "Local_Van_Krevelen_Plot",
        ),
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
        (
            "EPI_Identity_Universe",
            "EPI_Primary_Membership",
            "EPI_Results",
            "EPI_Raw_Results",
            "EPI_Errors",
            "EPI_Completeness",
            "EPI_Source_Provenance",
            "EPI_Match_Audit",
            "EPI_Conflict_Audit",
            "EPI_Query_Attempts",
            "EPI_Retry_Input",
        ),
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
            "EPA_PUC_Pie_Data",
            "EPA_Predicted_Pie_Data",
            "EPA_Reported_Pie_Data",
            "CompTox_Errors",
        ),
        (
            "EPA_Product_Use_Category_Distribution",
            "EPA_Top_Predicted_Functional_Use",
            "EPA_Reported_Functional_Use_Distribution",
            "EPA_Reported_Functional_Use_Evidence",
        ),
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
        (
            "ECHA_Reported_Use_Distribution",
            "ECHA_Reported_Use_Evidence",
        ),
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
        ("Source_Origin_Distribution",),
    ),
    (
        "07_Pov_LRTP_PBM_ToxPi",
        "Pov_LRTP_PBM_ToxPi_Results.xlsx",
        (
            "Pov_LRTP_Input",
            "Pov_LRTP",
            "ToxPi_Input",
            "ToxPi_Global_Screen",
            "ToxPi_Normalized",
            "ToxPi_Results",
            "ToxPi_Display",
            "ToxPi_Settings",
            "ToxPi_Robustness",
            "ToxPi_Robust_Stats",
        ),
        ("ToxPi_Radial_Plot", "ToxPi_Ranking_Bar", "ToxPi_Robustness_Histogram"),
    ),
)

AUTO_WORKFLOW_CHECKPOINT_EXPORTS = {
    R_DF_STEP_LABEL: (
        "local_screening",
        "Local_Screening_Results.xlsx",
        AUTO_WORKFLOW_EXPORT_MODULES[0][2],
    ),
    "标识符补全": (
        "identifier_completion",
        "Identifier_Completion_Results.xlsx",
        AUTO_WORKFLOW_EXPORT_MODULES[1][2],
    ),
    "EPI Suite 环境归趋": (
        "epi_suite",
        "EPI_Suite_Results.xlsx",
        AUTO_WORKFLOW_EXPORT_MODULES[2][2],
    ),
    "EPA CompTox 用途": (
        "comptox_use",
        "EPA_CompTox_Results.xlsx",
        AUTO_WORKFLOW_EXPORT_MODULES[3][2],
    ),
    "ECHA REACH 用途": (
        "echa_reach_use",
        "ECHA_REACH_Use_Results.xlsx",
        (
            "ECHA_Use_Summary",
            "ECHA_Uses_Reported",
            "ECHA_Reported_Pie_Data",
            "ECHA_Use_Dossiers",
            "ECHA_Use_Errors",
        ),
    ),
    "ECHA GHS/C&L 危害": (
        "echa_ghs_cl",
        "ECHA_GHS_CL_Results.xlsx",
        ("ECHA_GHS_Summary", "ECHA_GHS_Classifications", "ECHA_GHS_Errors"),
    ),
    "来源属性评估": (
        "source_origin",
        "Source_Origin_Results.xlsx",
        AUTO_WORKFLOW_EXPORT_MODULES[5][2],
    ),
    "Pov-LRTP / PBM / ToxPi": (
        "pov_lrtp_pbm_toxpi",
        "Pov_LRTP_PBM_ToxPi_Results.xlsx",
        AUTO_WORKFLOW_EXPORT_MODULES[6][2],
    ),
}

AUTO_WORKFLOW_MODULE_CHARTS_BY_SLUG = {
    "local_screening": AUTO_WORKFLOW_EXPORT_MODULES[0][3],
    "identifier_completion": (),
    "epi_suite": (),
    "comptox_use": AUTO_WORKFLOW_EXPORT_MODULES[3][3],
    "echa_reach_use": (
        "ECHA_Reported_Use_Distribution",
        "ECHA_Reported_Use_Evidence",
    ),
    "echa_ghs_cl": (),
    "source_origin": AUTO_WORKFLOW_EXPORT_MODULES[5][3],
    "pov_lrtp_pbm_toxpi": AUTO_WORKFLOW_EXPORT_MODULES[6][3],
}

PUBLIC_TABLE_NAMES = frozenset(
    name
    for _, _, table_names, _ in AUTO_WORKFLOW_EXPORT_MODULES
    for name in table_names
) | frozenset({"Identifier_Input", "EPI_Input", "Warnings"})

PUBLIC_CHART_NAMES = frozenset(
    chart_name
    for _, _, _, chart_names in AUTO_WORKFLOW_EXPORT_MODULES
    for chart_name in chart_names
)

PER_FILE_PUBLIC_TABLE_NAMES = frozenset(
    name
    for module_index in (0, 3, 4, 5)
    for name in AUTO_WORKFLOW_EXPORT_MODULES[module_index][2]
    if name != "Input_File_Mappings"
)


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
    axis_ranges: ScreeningAxisRanges = field(default_factory=ScreeningAxisRanges)
    toxpi_config: PBMToxPiConfig = field(default_factory=PBMToxPiConfig)
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
class AutoWorkflowPreparedInput:
    mapping: AutoWorkflowMapping
    prepared_input: pd.DataFrame
    representative_table: pd.DataFrame
    local_tables: OrderedDict[str, pd.DataFrame] = field(default_factory=OrderedDict)
    local_charts: OrderedDict[str, AutoWorkflowChart] = field(default_factory=OrderedDict)
    local_warnings: list[str] = field(default_factory=list)
    primary_membership: pd.DataFrame = field(default_factory=pd.DataFrame)
    epi_universe: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class AutoWorkflowResult:
    mapping: AutoWorkflowMapping
    representative_table: pd.DataFrame
    tables: OrderedDict[str, pd.DataFrame]
    step_status: pd.DataFrame
    warnings: pd.DataFrame
    charts: OrderedDict[str, AutoWorkflowChart] = field(default_factory=OrderedDict)


class AutoWorkflowEpiRetryError(RuntimeError):
    def __init__(self, message: str, result: AutoWorkflowResult):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class AutoWorkflowModuleWorkbook:
    step: str
    slug: str
    file_name: str
    data: bytes
    module_slug: str = ""
    primary_file: str = ""
    safe_export_name: str = ""


@dataclass(frozen=True)
class AutoWorkflowModuleDownload:
    file_name: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class AutoWorkflowCheckpointContext:
    run_id: str
    input_signature: str
    settings_signature: str
    selected_steps: tuple[str, ...]


@dataclass(frozen=True)
class AutoWorkflowCheckpoint:
    """Cumulative read-only view delivered to a checkpoint callback.

    The outer object is frozen, while DataFrames inside ``result`` are shared
    to avoid copying large cumulative tables. Callbacks must not mutate the
    result, its tables, or any other contained artifact.
    """

    run_id: str
    input_signature: str
    settings_signature: str
    selected_steps: tuple[str, ...]
    finished_steps: tuple[str, ...]
    current_step: str | None
    status: str
    result: AutoWorkflowResult
    error_message: str
    updated_at: str


@dataclass
class LocalScreeningOutput:
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, AutoWorkflowChart]
    warnings: list[str] = field(default_factory=list)


@dataclass
class PbmToxPiOutput:
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, AutoWorkflowChart]


ProgressCallback = Callable[[str, int, int, str], None]
ActivityCallback = Callable[[dict], None]
CheckpointCallback = Callable[[AutoWorkflowCheckpoint], None]


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


def prepare_legacy_auto_input(
    input_df: pd.DataFrame,
    config: AutoWorkflowConfig,
) -> AutoWorkflowPreparedInput:
    mapping = config.mapping or detect_default_mapping(input_df.columns)
    audit_columns = {"parse_status", "smiles_source", "smiles_decision_warning"}
    if audit_columns.issubset(input_df.columns):
        prepared_frame = input_df.copy()
    else:
        prepared_frame = prepare_structure_dataframe(
            input_df,
            mol_column=mapping.mol_column,
            smiles_column=mapping.smiles_col,
        )
    normalized = _normalize_input(prepared_frame, mapping)
    return AutoWorkflowPreparedInput(
        mapping=mapping,
        prepared_input=prepared_frame,
        representative_table=build_representative_table(normalized, mapping),
    )


def auto_input_from_multi_file_result(
    result: MultiFileScreeningResult,
) -> AutoWorkflowPreparedInput:
    local_tables = OrderedDict(result.tables)
    input_file_mappings = result.input_file_mappings.copy()
    file_column = next(
        (
            column
            for column in ("file_name", "source_file", "primary_file")
            if column in input_file_mappings.columns
        ),
        None,
    )
    safe_names = safe_export_names(input_file_mappings)
    if file_column is not None:
        input_file_mappings["safe_export_name"] = input_file_mappings[
            file_column
        ].map(safe_names)
    local_tables["Input_File_Mappings"] = input_file_mappings
    local_tables["Structure_Preparation"] = result.structure_preparation
    local_tables["DF_Table"] = result.df_table
    local_tables["Sample_Peak_Area"] = result.sample_peak_area
    local_tables["Group_Area_Raw_Long"] = result.group_area_raw_long
    local_tables["Group_Area_Mean_By_Sample"] = result.group_area_mean_by_sample
    local_charts = OrderedDict(result.charts)
    local_warnings = result.warnings.get(
        "message",
        pd.Series(dtype=str),
    ).tolist()
    sample_files = {}
    if file_column is not None and "sample_id" in input_file_mappings.columns:
        sample_files = {
            _clean_text(row["sample_id"]): _clean_text(row[file_column])
            for _, row in input_file_mappings.iterrows()
        }
    for sample_id, screening_result in result.screening_results:
        file_name = sample_files.get(_clean_text(sample_id), "")
        safe_name = safe_names.get(file_name)
        if not safe_name:
            continue
        file_charts, chart_warnings = _load_local_screening_charts(
            screening_result
        )
        for public_chart_name, chart in file_charts.items():
            local_charts[
                scoped_chart_key(
                    "local_screening",
                    safe_name,
                    public_chart_name,
                )
            ] = chart
        local_warnings.extend(
            f"{file_name}: {message}" for message in chart_warnings
        )
    return AutoWorkflowPreparedInput(
        mapping=AutoWorkflowMapping(
            compound_col="Name",
            formula_col="formula",
            peak_area_col="Group_Area",
            group_area_cols=["Group_Area"],
            smiles_col="SMILES_input",
            cas_col="CAS_input",
        ),
        prepared_input=result.structure_preparation,
        representative_table=result.representative_table,
        local_tables=local_tables,
        local_charts=local_charts,
        local_warnings=local_warnings,
        primary_membership=result.primary_membership,
        epi_universe=result.epi_universe,
    )


def run_auto_query_workflow(
    input_df: pd.DataFrame,
    config: AutoWorkflowConfig | None = None,
    progress_callback: ProgressCallback | None = None,
    activity_callback: ActivityCallback | None = None,
    checkpoint_context: AutoWorkflowCheckpointContext | None = None,
    checkpoint_callback: CheckpointCallback | None = None,
    prepared_input: AutoWorkflowPreparedInput | None = None,
    epi_uploaded_results: pd.DataFrame | None = None,
    epi_pool_results: pd.DataFrame | None = None,
) -> AutoWorkflowResult:
    config = config or AutoWorkflowConfig()
    if prepared_input is None:
        prepared_input = prepare_legacy_auto_input(input_df, config)
    mapping = prepared_input.mapping
    prepared_frame = prepared_input.prepared_input
    representative = prepared_input.representative_table
    epi_identity_universe = (
        prepared_input.epi_universe.copy().reset_index(drop=True)
        if (
            isinstance(prepared_input.epi_universe, pd.DataFrame)
            and not prepared_input.epi_universe.empty
        )
        else pd.DataFrame()
    )
    tables: OrderedDict[str, pd.DataFrame] = OrderedDict()
    charts: OrderedDict[str, AutoWorkflowChart] = OrderedDict()
    tables["Structure_Preparation"] = prepared_frame
    if not epi_identity_universe.empty:
        tables["EPI_Identity_Universe"] = epi_identity_universe.copy()
        tables["EPI_Primary_Membership"] = (
            prepared_input.primary_membership.copy()
        )
    status_rows = []
    warning_rows = []
    plot_warnings = configure_plot_style()
    for message in plot_warnings:
        warning_rows.append({"stage": "Plot style", "message": str(message)})
    if plot_warnings:
        tables["Plot_Warnings"] = pd.DataFrame({"warning": plot_warnings})

    identifier_input = pd.DataFrame(columns=REQUIRED_IDENTIFIER_COLUMNS)
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
    source_summary = pd.DataFrame()

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

    def current_result():
        current_warnings = pd.DataFrame(warning_rows, columns=["stage", "message"])
        current_tables = OrderedDict(tables)
        current_tables["Warnings"] = current_warnings
        return AutoWorkflowResult(
            mapping=mapping,
            representative_table=representative.copy(),
            tables=current_tables,
            step_status=pd.DataFrame(
                status_rows,
                columns=["step", "status", "rows", "message"],
            ),
            warnings=current_warnings,
            charts=OrderedDict(charts),
        )

    def emit_checkpoint(current_step, status="running", error_message=""):
        partial = current_result()
        updated_charts, chart_messages = update_auto_workflow_charts(
            partial,
            completed_step=current_step,
        )
        charts.clear()
        charts.update(updated_charts)
        for message in chart_messages:
            warning = {
                "stage": "Chart generation",
                "message": str(message),
            }
            if warning not in warning_rows:
                warning_rows.append(warning)
        for assignment_warning in file_assignment_warnings(
            current_result()
        ).to_dict("records"):
            warning = {
                "stage": assignment_warning["stage"],
                "message": assignment_warning["message"],
            }
            if warning not in warning_rows:
                warning_rows.append(warning)
        if checkpoint_callback is None or checkpoint_context is None:
            return
        selected = set(checkpoint_context.selected_steps)
        finished = tuple(
            row["step"]
            for row in status_rows
            if row["step"] in selected
        )
        checkpoint = AutoWorkflowCheckpoint(
            run_id=checkpoint_context.run_id,
            input_signature=checkpoint_context.input_signature,
            settings_signature=checkpoint_context.settings_signature,
            selected_steps=checkpoint_context.selected_steps,
            finished_steps=finished,
            current_step=current_step,
            status=status,
            result=current_result(),
            error_message=error_message,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            checkpoint_callback(checkpoint)
        except Exception as exc:
            add_warning("Checkpoint", f"临时恢复保存失败：{exc}")

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
        has_prepared_local_output = bool(
            prepared_input.local_tables
            or prepared_input.local_charts
        )
        if has_prepared_local_output:
            local_runner = lambda: LocalScreeningOutput(
                tables=OrderedDict(prepared_input.local_tables),
                charts=OrderedDict(prepared_input.local_charts),
                warnings=list(prepared_input.local_warnings),
            )
        else:
            local_runner = lambda: _run_r_replicate_df(
                _normalize_input(prepared_frame, mapping),
                mapping,
                config.detection_threshold,
                config.axis_ranges,
            )
        local_value = run_step(
            R_DF_STEP_LABEL,
            local_runner,
        )
        if local_value is not None:
            for key, table in local_value.tables.items():
                tables[key] = table
            charts.update(local_value.charts)
            for message in local_value.warnings:
                add_warning(R_DF_STEP_LABEL, message)
            record(R_DF_STEP_LABEL, "完成", len(local_value.tables.get("DF_Table", pd.DataFrame())))
        if not has_prepared_local_output:
            for message in prepared_input.local_warnings:
                add_warning(R_DF_STEP_LABEL, message)
        emit_checkpoint(R_DF_STEP_LABEL)

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
        identifier_input = (
            _build_identifier_input_from_epi_universe(
                epi_identity_universe
            )
            if not epi_identity_universe.empty
            else _build_identifier_input(representative)
        )
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
            if not epi_identity_universe.empty:
                completed_identifiers = _restore_identity_keys(
                    completed_identifiers,
                    epi_identity_universe,
                )
            tables["Identifier_Completion"] = completed_identifiers
            tables["Identifier_Warnings"] = identifier_warnings
            record("标识符补全", "完成", len(completed_identifiers))
        emit_checkpoint("标识符补全")

    prepared_epi_universe = pd.DataFrame()
    if not epi_identity_universe.empty:
        prepared_epi_universe = prepare_universe(
            epi_identity_universe,
            completed_identifiers,
        )
        query_input = _build_identifier_input_from_epi_universe(
            prepared_epi_universe
        )
    else:
        query_input = _query_input_from_identifiers(
            identifier_input,
            completed_identifiers,
        )
    compound_universe = build_compound_universe(identifier_input)

    run_epi_step = config.run_epi or config.run_pov_lrtp_toxpi
    if run_epi_step:
        if not prepared_epi_universe.empty:
            epi_input = prepared_epi_universe.loc[
                :,
                [
                    column
                    for column in prepared_epi_universe.columns
                    if not column.startswith("_")
                ],
            ].reset_index(drop=True)
        else:
            epi_input = query_input.loc[
                :,
                ["compound", "smiles", "cas"],
            ].reset_index(drop=True)
        tables["EPI_Input"] = epi_input
        resolution = resolve_epi_sources(
            epi_input,
            pd.DataFrame()
            if epi_uploaded_results is None
            else epi_uploaded_results,
            pd.DataFrame()
            if epi_pool_results is None
            else epi_pool_results,
            completed_identifiers=completed_identifiers,
            require_core=bool(config.run_pov_lrtp_toxpi),
            primary_membership=prepared_input.primary_membership,
        )
        attempt_events = []
        epi_value = None

        def epi_progress(done, total, label):
            if progress_callback:
                progress_callback(
                    "EPI Suite 环境归趋",
                    done,
                    total,
                    label,
                )

        if (
            not resolution.query_input.empty
            and not resolution.query_input["smiles"].eq("").all()
        ):
            forward_epi_activity = activity_for(
                "EPI Suite 环境归趋",
                config.epi_timeout,
            )

            def record_epi_activity(event):
                attempt_events.append(dict(event))
                forward_epi_activity(event)

            epi_value = run_step(
                "EPI Suite 环境归趋",
                lambda: run_epi_web_batch(
                    resolution.query_input,
                    api_url=config.epi_api_url,
                    timeout=int(config.epi_timeout),
                    delay_seconds=float(config.epi_delay_seconds),
                    max_workers=int(config.epi_max_workers),
                    cache_enabled=bool(config.cache_enabled),
                    progress_callback=epi_progress,
                    activity_callback=record_epi_activity,
                ),
            )
            if epi_value is not None:
                network_results, network_raw, network_errors = epi_value
            else:
                network_results = pd.DataFrame()
                network_raw = pd.DataFrame()
                network_errors = pd.DataFrame()
            resolution = merge_network_epi(
                resolution,
                network_results,
                network_raw,
                network_errors,
                attempt_events,
            )

        epi_results = resolution.results
        epi_raw_results = resolution.raw_results
        epi_errors = resolution.errors
        tables["EPI_Results"] = resolution.results
        tables["EPI_Raw_Results"] = resolution.raw_results
        tables["EPI_Errors"] = resolution.errors
        tables["EPI_Completeness"] = resolution.completeness
        tables["EPI_Source_Provenance"] = resolution.provenance
        tables["EPI_Match_Audit"] = resolution.match_audit
        tables["EPI_Conflict_Audit"] = resolution.conflict_audit
        tables["EPI_Query_Attempts"] = resolution.query_attempts
        tables["EPI_Retry_Input"] = resolution.query_input.reset_index(
            drop=True
        )
        if resolution.query_input.empty:
            record("EPI Suite 环境归趋", "完成", len(epi_results))
        elif resolution.query_input["smiles"].eq("").all():
            record("EPI Suite 环境归趋", "跳过", 0, "缺少可用于 EPI 的 SMILES。")
        elif epi_value is not None:
            record("EPI Suite 环境归趋", "完成", len(epi_results))
        emit_checkpoint("EPI Suite 环境归趋")

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
            comptox_output_candidates = deduplicate_comptox_candidates(
                comptox_candidates
            )
            tables["Product_Use_Categories"] = build_product_use_table(
                comptox_output_candidates
            )
            tables["Functional_Uses_Predicted"] = build_functional_use_table(
                comptox_output_candidates,
                functional_source="predicted",
            )
            tables["Functional_Uses_Reported"] = build_functional_use_table(
                comptox_output_candidates,
                functional_source="reported",
            )
            tables["EPA_PUC_Pie_Data"] = extract_top_product_use_category_data(
                comptox_output_candidates,
                compound_universe,
            )
            tables["EPA_Predicted_Pie_Data"] = extract_top_predicted_functional_use_data(
                comptox_output_candidates,
                compound_universe=compound_universe,
            )
            tables["EPA_Reported_Pie_Data"] = extract_top_reported_functional_use_data(
                comptox_output_candidates,
                compound_universe,
                source_label="EPA FC reported",
                source_type="functional_use",
                use_key="raw",
                require_reported_flag=True,
            )
            tables["CompTox_Errors"] = comptox_errors
            record("EPA CompTox 用途", "完成", len(comptox_summary))

        if comptox_value is None:
            comptox_output_candidates = deduplicate_comptox_candidates(
                comptox_candidates
            )
            tables["Product_Use_Categories"] = build_product_use_table(
                comptox_output_candidates
            )
            tables["Functional_Uses_Predicted"] = build_functional_use_table(
                comptox_output_candidates,
                functional_source="predicted",
            )
            tables["Functional_Uses_Reported"] = build_functional_use_table(
                comptox_output_candidates,
                functional_source="reported",
            )
            tables["EPA_PUC_Pie_Data"] = extract_top_product_use_category_data(
                comptox_output_candidates,
                compound_universe,
            )
            tables["EPA_Predicted_Pie_Data"] = extract_top_predicted_functional_use_data(
                comptox_output_candidates,
                compound_universe=compound_universe,
            )
            tables["EPA_Reported_Pie_Data"] = extract_top_reported_functional_use_data(
                comptox_output_candidates,
                compound_universe,
                source_label="EPA FC reported",
                source_type="functional_use",
                use_key="raw",
                require_reported_flag=True,
            )
        emit_checkpoint("EPA CompTox 用途")

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

        if echa_value is None:
            echa_reported_audit = extract_top_reported_functional_use_data(
                echa_candidates,
                compound_universe,
                source_label="ECHA reported",
                use_key="category",
                require_reported_flag=False,
            )
            tables["ECHA_Uses_Reported"] = echa_reported_audit.copy()
            tables["ECHA_Reported_Pie_Data"] = echa_reported_audit
        emit_checkpoint("ECHA REACH 用途")

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
        emit_checkpoint("ECHA GHS/C&L 危害")

    if config.run_source_origin:
        source_value = run_step(
            "来源属性评估",
            lambda: run_source_origin_batch(
                query_input,
                comptox_summary_df=comptox_summary if config.run_comptox else None,
                comptox_candidates_df=comptox_candidates if config.run_comptox else None,
                echa_summary_df=echa_summary if config.run_echa_use else None,
                echa_candidates_df=echa_candidates if config.run_echa_use else None,
                echa_dossiers_df=echa_dossiers if config.run_echa_use else None,
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

        if source_value is None:
            tables["Source_Origin_Pie_Data"] = extract_source_origin_pie_data(
                source_summary,
                compound_universe,
            )
        emit_checkpoint("来源属性评估")

    if config.run_pov_lrtp_toxpi:
        pov_representative = _annotate_representative_identity_keys(
            representative,
            prepared_input.primary_membership,
            epi_identity_universe,
        )
        toxpi_value = run_step(
            "Pov-LRTP / PBM / ToxPi",
            lambda: _run_pov_lrtp_toxpi(
                pov_representative,
                completed_identifiers,
                epi_results,
                tables,
                config.toxpi_config,
            ),
        )
        if toxpi_value is not None:
            for key, table in toxpi_value.tables.items():
                tables[key] = table
            charts.update(toxpi_value.charts)
            record(
                "Pov-LRTP / PBM / ToxPi",
                "完成",
                len(toxpi_value.tables.get("ToxPi_Results", pd.DataFrame())),
            )
        emit_checkpoint("Pov-LRTP / PBM / ToxPi")

    emit_checkpoint(None, status="completed")
    return current_result()


def retry_auto_workflow_epi_failures(
    result: AutoWorkflowResult,
    config: AutoWorkflowConfig,
    progress_callback: ProgressCallback | None = None,
    activity_callback: ActivityCallback | None = None,
) -> AutoWorkflowResult:
    retry_input = result.tables.get("EPI_Retry_Input", pd.DataFrame()).copy()
    if retry_input.empty:
        return result
    query_input = queryable_epi_retry_input(retry_input)
    if query_input.empty:
        return result

    attempt_events = []

    def retry_activity(event):
        attempt_events.append(dict(event))
        if activity_callback is not None:
            activity_callback(event)

    batch_error = None
    try:
        network_results, network_raw, network_errors = run_epi_web_batch(
            query_input,
            api_url=config.epi_api_url,
            timeout=int(config.epi_timeout),
            delay_seconds=float(config.epi_delay_seconds),
            max_workers=int(config.epi_max_workers),
            cache_enabled=bool(config.cache_enabled),
            progress_callback=(
                None
                if progress_callback is None
                else lambda done, total, label: progress_callback(
                    "EPI Suite 环境归趋",
                    done,
                    total,
                    label,
                )
            ),
            activity_callback=retry_activity,
        )
    except Exception as exc:
        batch_error = exc
        network_results = pd.DataFrame()
        network_raw = pd.DataFrame()
        network_errors = pd.DataFrame()
    resolution = merge_network_epi(
        EPIResolution(
            results=result.tables.get("EPI_Results", pd.DataFrame()),
            raw_results=result.tables.get("EPI_Raw_Results", pd.DataFrame()),
            errors=result.tables.get("EPI_Errors", pd.DataFrame()),
            completeness=result.tables.get(
                "EPI_Completeness",
                pd.DataFrame(),
            ),
            provenance=result.tables.get(
                "EPI_Source_Provenance",
                pd.DataFrame(),
            ),
            match_audit=result.tables.get(
                "EPI_Match_Audit",
                pd.DataFrame(),
            ),
            conflict_audit=result.tables.get(
                "EPI_Conflict_Audit",
                pd.DataFrame(),
            ),
            query_attempts=result.tables.get(
                "EPI_Query_Attempts",
                pd.DataFrame(),
            ),
            query_input=retry_input,
        ),
        network_results,
        network_raw,
        network_errors,
        attempt_events,
    )
    updated_tables = OrderedDict(result.tables)
    updated_tables.update(
        {
            "EPI_Results": resolution.results,
            "EPI_Raw_Results": resolution.raw_results,
            "EPI_Errors": resolution.errors,
            "EPI_Completeness": resolution.completeness,
            "EPI_Source_Provenance": resolution.provenance,
            "EPI_Match_Audit": resolution.match_audit,
            "EPI_Conflict_Audit": resolution.conflict_audit,
            "EPI_Query_Attempts": resolution.query_attempts,
            "EPI_Retry_Input": resolution.query_input.reset_index(drop=True),
        }
    )
    updated = AutoWorkflowResult(
        mapping=result.mapping,
        representative_table=result.representative_table.copy(),
        tables=updated_tables,
        step_status=result.step_status.copy(),
        warnings=result.warnings.copy(),
        charts=OrderedDict(result.charts),
    )
    if batch_error is not None:
        raise AutoWorkflowEpiRetryError(str(batch_error), updated) from batch_error
    if not config.run_pov_lrtp_toxpi:
        return updated

    dependent_table_names = AUTO_WORKFLOW_EXPORT_MODULES[6][2]
    dependent_chart_names = AUTO_WORKFLOW_EXPORT_MODULES[6][3]
    for name in dependent_table_names:
        updated.tables.pop(name, None)
    for name in dependent_chart_names:
        updated.charts.pop(name, None)
    pov_representative = _annotate_representative_identity_keys(
        updated.representative_table,
        updated.tables.get("EPI_Primary_Membership", pd.DataFrame()),
        updated.tables.get("EPI_Identity_Universe", pd.DataFrame()),
    )
    toxpi_value = _run_pov_lrtp_toxpi(
        pov_representative,
        updated.tables.get("Identifier_Completion", pd.DataFrame()),
        resolution.results,
        updated.tables,
        config.toxpi_config,
    )
    updated.tables.update(toxpi_value.tables)
    updated.charts.update(toxpi_value.charts)
    return updated


def queryable_epi_retry_input(retry_input: pd.DataFrame) -> pd.DataFrame:
    if (
        not isinstance(retry_input, pd.DataFrame)
        or retry_input.empty
        or "smiles" not in retry_input.columns
    ):
        return pd.DataFrame(columns=getattr(retry_input, "columns", None))
    queryable = retry_input["smiles"].map(_clean_text).ne("")
    return retry_input.loc[queryable].copy().reset_index(drop=True)


def build_auto_workflow_workbook(result: AutoWorkflowResult) -> io.BytesIO:
    per_file_mode = _has_file_mappings(result)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        result.step_status.to_excel(writer, sheet_name="Run_Log", index=False)
        result.representative_table.to_excel(writer, sheet_name="Representative_Input", index=False)
        if not isinstance(result.tables.get("Warnings"), pd.DataFrame):
            result.warnings.to_excel(writer, sheet_name="Warnings", index=False)
        for name, table in result.tables.items():
            if name not in PUBLIC_TABLE_NAMES:
                continue
            if per_file_mode and name in PER_FILE_PUBLIC_TABLE_NAMES:
                continue
            sheet_name = _safe_sheet_name(name)
            (table if table is not None else pd.DataFrame()).to_excel(writer, sheet_name=sheet_name, index=False)
    buffer.seek(0)
    return buffer


def _build_module_workbook(result: AutoWorkflowResult, table_names: tuple[str, ...]) -> io.BytesIO:
    return _build_tables_workbook(result.tables, table_names)


def _build_tables_workbook(
    tables: Mapping[str, pd.DataFrame],
    table_names: tuple[str, ...],
) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name in table_names:
            if name not in PUBLIC_TABLE_NAMES:
                continue
            table = tables.get(name)
            if isinstance(table, pd.DataFrame):
                table.to_excel(writer, sheet_name=_safe_sheet_name(name), index=False)
    buffer.seek(0)
    return buffer


def build_auto_workflow_module_workbook(
    result: AutoWorkflowResult,
    step: str,
) -> AutoWorkflowModuleWorkbook | None:
    modules = build_auto_workflow_module_workbooks(result, step)
    return next(iter(modules.values()), None)


PER_FILE_MODULE_VIEW_SLUG = {
    "local_screening": "local_screening",
    "comptox_use": "comptox_use",
    "echa_reach_use": "echa_reach_use",
    "echa_ghs_cl": "echa_ghs_cl",
    "source_origin": "source_origin",
}


def build_auto_workflow_module_workbooks(
    result: AutoWorkflowResult,
    step: str,
) -> OrderedDict[str, AutoWorkflowModuleWorkbook]:
    export = AUTO_WORKFLOW_CHECKPOINT_EXPORTS.get(step)
    if export is None:
        return OrderedDict()
    slug, file_name, candidates = export
    view_slug = PER_FILE_MODULE_VIEW_SLUG.get(slug)
    views = (
        build_file_module_views(result).get(view_slug, OrderedDict())
        if view_slug is not None
        else OrderedDict()
    )
    modules = OrderedDict()
    for view in views.values():
        table_names = tuple(
            name
            for name in candidates
            if name in PUBLIC_TABLE_NAMES
            and isinstance(view.tables.get(name), pd.DataFrame)
        )
        if not table_names:
            continue
        unique_slug = f"{slug}__{view.safe_export_name}"
        modules[unique_slug] = AutoWorkflowModuleWorkbook(
            step=step,
            slug=unique_slug,
            file_name=file_name,
            data=_build_tables_workbook(
                view.tables,
                table_names,
            ).getvalue(),
            module_slug=slug,
            primary_file=view.primary_file,
            safe_export_name=view.safe_export_name,
        )
    if modules:
        return modules

    table_names = tuple(
        name
        for name in candidates
        if name in PUBLIC_TABLE_NAMES and isinstance(result.tables.get(name), pd.DataFrame)
    )
    if not table_names:
        return OrderedDict()
    modules[slug] = AutoWorkflowModuleWorkbook(
        step=step,
        slug=slug,
        file_name=file_name,
        data=_build_module_workbook(result, table_names).getvalue(),
        module_slug=slug,
    )
    return modules


EXPORT_FOLDER_BY_SLUG = {
    "local_screening": "01_Local_Screening",
    "identifier_completion": "02_Identifier_Completion",
    "epi_suite": "03_EPI_Suite",
    "comptox_use": "04_EPA_CompTox",
    "echa_reach_use": "05_ECHA",
    "echa_ghs_cl": "05_ECHA",
    "echa": "05_ECHA",
    "source_origin": "06_Source_Origin",
    "pov_lrtp_pbm_toxpi": "07_Pov_LRTP_PBM_ToxPi",
}


def module_archive_root(module):
    module_slug = module.module_slug or module.slug
    folder = EXPORT_FOLDER_BY_SLUG.get(module_slug, module_slug)
    if module.safe_export_name:
        return f"{folder}/{module.safe_export_name}"
    return folder


def _module_download_chart_keys(
    module: AutoWorkflowModuleWorkbook,
    charts: Mapping[str, AutoWorkflowChart],
) -> tuple[str, ...]:
    module_slug = module.module_slug or module.slug
    candidates = AUTO_WORKFLOW_MODULE_CHARTS_BY_SLUG.get(module_slug, ())
    if module.safe_export_name:
        return tuple(
            key
            for candidate in candidates
            for key in (
                scoped_chart_key(
                    module_slug,
                    module.safe_export_name,
                    candidate,
                ),
            )
            if key in charts
        )
    return tuple(
        key
        for key in candidates
        if key in PUBLIC_CHART_NAMES and key in charts
    )


def build_auto_workflow_module_download(
    module: AutoWorkflowModuleWorkbook,
    charts: Mapping[str, AutoWorkflowChart] | None = None,
) -> AutoWorkflowModuleDownload:
    available_charts = charts or {}
    chart_keys = _module_download_chart_keys(module, available_charts)
    if not chart_keys:
        return AutoWorkflowModuleDownload(
            file_name=module.file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=module.data,
        )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(module.file_name, module.data)
        for key in chart_keys:
            stem = _module_chart_file_name(key)
            archive.writestr(f"figures/{stem}.png", available_charts[key].png)
            archive.writestr(f"figures/{stem}.pdf", available_charts[key].pdf)
    return AutoWorkflowModuleDownload(
        file_name=Path(module.file_name).with_suffix(".zip").name,
        mime="application/zip",
        data=buffer.getvalue(),
    )


def build_auto_workflow_module_group_download(
    modules,
    charts: Mapping[str, AutoWorkflowChart] | None = None,
) -> AutoWorkflowModuleDownload:
    modules = tuple(modules)
    if len(modules) == 1 and not modules[0].safe_export_name:
        return build_auto_workflow_module_download(modules[0], charts)
    available_charts = charts or {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for module in modules:
            root = module_archive_root(module)
            archive.writestr(f"{root}/{module.file_name}", module.data)
            for key in _module_download_chart_keys(module, available_charts):
                stem = _module_chart_file_name(key)
                archive.writestr(
                    f"{root}/figures/{stem}.png",
                    available_charts[key].png,
                )
                archive.writestr(
                    f"{root}/figures/{stem}.pdf",
                    available_charts[key].pdf,
                )
    label = (modules[0].module_slug or modules[0].slug) if modules else "module"
    return AutoWorkflowModuleDownload(
        file_name=f"{label}_Results.zip",
        mime="application/zip",
        data=buffer.getvalue(),
    )


def build_auto_workflow_partial_zip(
    result: AutoWorkflowResult,
    module_workbooks: Mapping[str, AutoWorkflowModuleWorkbook],
    charts: Mapping[str, AutoWorkflowChart] | None = None,
) -> io.BytesIO:
    available_charts = charts if charts is not None else result.charts
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Partial_Auto_Query_Workflow_Results.xlsx",
            build_auto_workflow_workbook(result).getvalue(),
        )
        for module in module_workbooks.values():
            if module.safe_export_name:
                workbook_path = f"{module_archive_root(module)}/{module.file_name}"
                figures_root = f"{module_archive_root(module)}/figures"
            else:
                workbook_path = f"modules/{module.file_name}"
                figures_root = f"modules/{module.slug}/figures"
            archive.writestr(workbook_path, module.data)
            for key in _module_download_chart_keys(module, available_charts):
                stem = _module_chart_file_name(key)
                archive.writestr(
                    f"{figures_root}/{stem}.png",
                    available_charts[key].png,
                )
                archive.writestr(
                    f"{figures_root}/{stem}.pdf",
                    available_charts[key].pdf,
                )
    buffer.seek(0)
    return buffer


def _module_chart_file_name(chart_key: str) -> str:
    return chart_key.rsplit("__", 1)[-1].removeprefix("Local_")


def build_auto_workflow_charts(
    result: AutoWorkflowResult,
) -> OrderedDict[str, AutoWorkflowChart]:
    charts, messages = update_auto_workflow_charts(result)
    existing = (
        result.warnings.copy()
        if isinstance(result.warnings, pd.DataFrame)
        else pd.DataFrame(columns=["stage", "message"])
    )
    for column in ("stage", "message"):
        if column not in existing.columns:
            existing[column] = pd.Series(dtype="object")
    if "stage" in existing.columns:
        existing = existing.loc[
            ~existing["stage"].eq("Chart generation")
        ].copy()
    additions = pd.DataFrame(
        [
            {
                "stage": "Chart generation",
                "message": str(message),
            }
            for message in messages
        ],
        columns=["stage", "message"],
    )
    result.warnings = (
        pd.concat([existing, additions], ignore_index=True)
        .drop_duplicates(subset=["stage", "message"])
        .reset_index(drop=True)
    )
    result.tables["Warnings"] = result.warnings.copy()
    return charts


def update_auto_workflow_charts(result, completed_step=None):
    configured_sources = available_chart_sources(
        result,
        completed_step=completed_step,
    )
    available_keys = {chart_key for chart_key, _ in configured_sources}
    managed_scoped_prefixes = tuple(
        f"{module_slug}__"
        for module_slug in FILE_CHART_MODULE_REQUIRED_TABLES
    )
    charts = OrderedDict(
        (key, chart)
        for key, chart in result.charts.items()
        if (
            (
                key in PUBLIC_CHART_NAMES
                or any(
                    key.endswith(f"__{public_name}")
                    for public_name in PUBLIC_CHART_NAMES
                )
            )
            and (
                not key.startswith(managed_scoped_prefixes)
                or key in available_keys
            )
        )
    )
    warnings = []
    refresh_existing = completed_step is None
    for chart_key, source_config in configured_sources:
        if chart_key in charts and not refresh_existing:
            continue
        if refresh_existing:
            charts.pop(chart_key, None)
        fig = None
        try:
            chart_df = _build_chart_data(source_config)
            if chart_df.empty:
                continue
            fig = _build_chart_figure(chart_df, source_config)
            charts[chart_key] = AutoWorkflowChart(
                title=source_config["title"],
                png=figure_to_png_bytes(fig).getvalue(),
                pdf=figure_to_pdf_bytes(fig).getvalue(),
            )
        except Exception as exc:
            warnings.append(f"{source_config['title']}: {exc}")
        finally:
            if fig is not None:
                plt.close(fig)
    return charts, warnings


FILE_CHART_MODULE_REQUIRED_TABLES = OrderedDict(
    [
        ("comptox_use", "CompTox_Candidates"),
        ("echa_reach_use", "ECHA_Use_Candidates"),
        ("source_origin", "Source_Origin_Summary"),
    ]
)


def available_chart_sources(result, completed_step=None):
    views = build_file_module_views(result)
    configured = []
    found_file_view = any(
        views.get(module_slug)
        for module_slug in FILE_CHART_MODULE_REQUIRED_TABLES
    )
    for module_slug, required_table in FILE_CHART_MODULE_REQUIRED_TABLES.items():
        if required_table not in result.tables:
            continue
        for view in views.get(module_slug, {}).values():
            proxy_tables = OrderedDict(view.tables)
            for name in ("ToxPi_Results", "ToxPi_Settings"):
                table = result.tables.get(name)
                if isinstance(table, pd.DataFrame):
                    proxy_tables[name] = table
            proxy = AutoWorkflowResult(
                mapping=result.mapping,
                representative_table=result.representative_table,
                tables=proxy_tables,
                step_status=result.step_status,
                warnings=result.warnings,
                charts=OrderedDict(),
            )
            for source_config in _auto_workflow_chart_sources(proxy):
                configured.append(
                    (
                        scoped_chart_key(
                            module_slug,
                            view.safe_export_name,
                            source_config["file_prefix"],
                        ),
                        source_config,
                    )
                )
    if not found_file_view:
        configured.extend(
            (source["file_prefix"], source)
            for source in _auto_workflow_chart_sources(result)
        )
    return configured


def build_auto_workflow_zip(
    result: AutoWorkflowResult,
    charts: OrderedDict[str, AutoWorkflowChart] | None = None,
) -> io.BytesIO:
    charts = charts if charts is not None else build_auto_workflow_charts(result)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Auto_Query_Workflow_Results.xlsx", build_auto_workflow_workbook(result).getvalue())
        if _has_file_mappings(result):
            _write_per_file_full_exports(archive, result, charts)
            _write_shared_full_exports(archive, result, charts)
        else:
            for folder, workbook_name, table_candidates, chart_candidates in AUTO_WORKFLOW_EXPORT_MODULES:
                table_names = tuple(
                    name
                    for name in table_candidates
                    if name in PUBLIC_TABLE_NAMES
                    and isinstance(result.tables.get(name), pd.DataFrame)
                )
                chart_keys = tuple(
                    key
                    for key in charts
                    if key in PUBLIC_CHART_NAMES and key in chart_candidates
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


def _has_file_mappings(result):
    mappings = result.tables.get("Input_File_Mappings")
    return (
        isinstance(mappings, pd.DataFrame)
        and not mappings.empty
        and bool(safe_export_names(mappings))
    )


def _write_per_file_full_exports(archive, result, charts):
    views = build_file_module_views(result)
    definitions = (
        (
            "01_Local_Screening",
            "Local_Screening_Results.xlsx",
            AUTO_WORKFLOW_EXPORT_MODULES[0][2],
            AUTO_WORKFLOW_EXPORT_MODULES[0][3],
            "local_screening",
            ("local_screening",),
        ),
        (
            "04_EPA_CompTox",
            "EPA_CompTox_Results.xlsx",
            AUTO_WORKFLOW_EXPORT_MODULES[3][2],
            AUTO_WORKFLOW_EXPORT_MODULES[3][3],
            "comptox_use",
            ("comptox_use",),
        ),
        (
            "05_ECHA",
            "ECHA_Results.xlsx",
            AUTO_WORKFLOW_EXPORT_MODULES[4][2],
            AUTO_WORKFLOW_EXPORT_MODULES[4][3],
            "echa",
            ("echa_reach_use", "echa_ghs_cl"),
        ),
        (
            "06_Source_Origin",
            "Source_Origin_Results.xlsx",
            AUTO_WORKFLOW_EXPORT_MODULES[5][2],
            AUTO_WORKFLOW_EXPORT_MODULES[5][3],
            "source_origin",
            ("source_origin",),
        ),
    )
    for (
        folder,
        workbook_name,
        table_candidates,
        chart_candidates,
        view_slug,
        chart_slugs,
    ) in definitions:
        for view in views.get(view_slug, {}).values():
            root = f"{folder}/{view.safe_export_name}"
            table_names = tuple(
                name
                for name in table_candidates
                if name in PUBLIC_TABLE_NAMES
                and isinstance(view.tables.get(name), pd.DataFrame)
            )
            if table_names:
                workbook = _build_tables_workbook(view.tables, table_names)
                archive.writestr(
                    f"{root}/{workbook_name}",
                    workbook.getvalue(),
                )
            for candidate in chart_candidates:
                for chart_slug in chart_slugs:
                    key = scoped_chart_key(
                        chart_slug,
                        view.safe_export_name,
                        candidate,
                    )
                    if key not in charts:
                        continue
                    stem = _module_chart_file_name(key)
                    archive.writestr(
                        f"{root}/figures/{stem}.png",
                        charts[key].png,
                    )
                    archive.writestr(
                        f"{root}/figures/{stem}.pdf",
                        charts[key].pdf,
                    )
                    break


def _write_shared_full_exports(archive, result, charts):
    for module_index in (1, 2, 6):
        (
            folder,
            workbook_name,
            table_candidates,
            chart_candidates,
        ) = AUTO_WORKFLOW_EXPORT_MODULES[module_index]
        table_names = tuple(
            name
            for name in table_candidates
            if name in PUBLIC_TABLE_NAMES
            and isinstance(result.tables.get(name), pd.DataFrame)
        )
        if table_names:
            workbook = _build_module_workbook(result, table_names)
            archive.writestr(f"{folder}/{workbook_name}", workbook.getvalue())
        for key in chart_candidates:
            if key not in charts:
                continue
            stem = _module_chart_file_name(key)
            archive.writestr(
                f"{folder}/figures/{stem}.png",
                charts[key].png,
            )
            archive.writestr(
                f"{folder}/figures/{stem}.pdf",
                charts[key].pdf,
            )


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
    evidence_selection = _toxpi_evidence_chart_selection(result)
    comptox_candidates = result.tables.get("CompTox_Candidates")
    if (
        evidence_selection is not None
        and isinstance(comptox_candidates, pd.DataFrame)
        and not comptox_candidates.empty
    ):
        chart_sources.append(
            {
                "chart_type": "reported_presence",
                "source_label": "EPA FC reported",
                "candidates_df": comptox_candidates,
                "title": "EPA CompTox Reported Functional Use Evidence",
                "file_prefix": "EPA_Reported_Functional_Use_Evidence",
                **evidence_selection,
            }
        )

    puc_pie = result.tables.get("EPA_PUC_Pie_Data")
    if isinstance(puc_pie, pd.DataFrame) and not puc_pie.empty:
        chart_sources.append(
            {
                "chart_type": "classification_pie",
                "table_df": puc_pie,
                "title": "EPA CompTox Product-Use Category Distribution",
                "file_prefix": "EPA_Product_Use_Category_Distribution",
                "footnote": PRODUCT_USE_CATEGORY_OTHERS_NOTE,
            }
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
    if (
        evidence_selection is not None
        and isinstance(echa_candidates, pd.DataFrame)
        and not echa_candidates.empty
    ):
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
                **evidence_selection,
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


def _toxpi_evidence_chart_selection(result: AutoWorkflowResult) -> dict | None:
    toxpi_results = result.tables.get("ToxPi_Results")
    if (
        not isinstance(toxpi_results, pd.DataFrame)
        or toxpi_results.empty
        or "compound" not in toxpi_results.columns
        or "final_rank" not in toxpi_results.columns
    ):
        return None

    ranking = toxpi_results.copy()
    ranking["_final_rank"] = pd.to_numeric(ranking["final_rank"], errors="coerce")
    ranking = ranking.loc[ranking["_final_rank"].gt(0)].sort_values(
        "_final_rank",
        ascending=True,
        kind="mergesort",
    )
    if ranking.empty:
        return None
    compound_order = [
        compound
        for compound in ranking["compound"].map(_clean_text).tolist()
        if compound
    ]
    if not compound_order:
        return None

    defaults = PBMToxPiConfig()
    settings = result.tables.get("ToxPi_Settings")
    setting_values = {}
    if (
        isinstance(settings, pd.DataFrame)
        and not settings.empty
        and {"setting", "value"}.issubset(settings.columns)
    ):
        setting_values = dict(zip(settings["setting"], settings["value"]))

    def positive_int(name, default):
        value = pd.to_numeric(setting_values.get(name, default), errors="coerce")
        if pd.isna(value) or int(value) < 1:
            return int(default)
        return int(value)

    return {
        "compound_order": compound_order,
        "per_compound_top_n": positive_int(
            "evidence_per_compound_top_n",
            defaults.evidence_per_compound_top_n,
        ),
        "global_use_top_n": positive_int(
            "evidence_global_use_top_n",
            defaults.evidence_global_use_top_n,
        ),
    }


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
            compound_order=source_config.get("compound_order"),
            per_compound_top_n=source_config.get("per_compound_top_n"),
            global_use_top_n=source_config.get("global_use_top_n"),
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
        return generate_reported_functional_use_presence_plot(
            chart_df,
            source_config["title"],
            selection_note=chart_df.attrs.get("selection_note"),
        )
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


def _run_r_replicate_df(
    input_df: pd.DataFrame,
    mapping: AutoWorkflowMapping,
    detection_threshold: float,
    axis_ranges: ScreeningAxisRanges | None = None,
):
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
        axis_ranges=axis_ranges or ScreeningAxisRanges(),
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
    config: PBMToxPiConfig,
) -> PbmToxPiOutput:
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
    output = _build_pbm_toxpi_output(toxpi_input, config)
    return PbmToxPiOutput(
        tables=OrderedDict(
            [
                ("Pov_LRTP_Input", pov_lrtp_input),
                ("Pov_LRTP", pov_lrtp_results),
                *output.tables.items(),
            ]
        ),
        charts=output.charts,
    )


def _build_pbm_toxpi_output(
    toxpi_input: pd.DataFrame,
    config: PBMToxPiConfig,
) -> PbmToxPiOutput:
    toxpi_result = calculate_pbm_toxpi(toxpi_input, config)
    tables = OrderedDict(
        [
            ("ToxPi_Input", toxpi_input),
            ("ToxPi_Global_Screen", toxpi_result.global_screen),
            ("ToxPi_Normalized", toxpi_result.candidate_normalized),
            ("ToxPi_Results", toxpi_result.final_ranking),
            ("ToxPi_Display", toxpi_result.display_rows),
            ("ToxPi_Settings", toxpi_result.settings_table()),
            ("ToxPi_Robustness", toxpi_result.robustness_summary),
            ("ToxPi_Robust_Stats", toxpi_result.robustness_stats),
        ]
    )
    charts: OrderedDict[str, AutoWorkflowChart] = OrderedDict()
    if not toxpi_result.display_rows.empty:
        radial = generate_r_style_toxpi_plot(
            toxpi_result.display_rows,
            custom_weights=toxpi_result.normalized_weights,
            toxic_cols=["peak_area", "pbm", "df"],
            label_wrap_width=20,
        )
        charts["ToxPi_Radial_Plot"] = _auto_workflow_chart_from_figure(
            radial,
            "ToxPi Radial Plot",
        )
        bar = generate_pbm_toxpi_bar_plot(
            toxpi_result.display_rows,
            top_n=toxpi_result.effective_display_top_n,
        )
        charts["ToxPi_Ranking_Bar"] = _auto_workflow_chart_from_figure(
            bar,
            "ToxPi Ranking Bar",
        )
    if not toxpi_result.robustness_correlations.empty:
        robustness = generate_pbm_toxpi_robustness_plot(toxpi_result)
        charts["ToxPi_Robustness_Histogram"] = _auto_workflow_chart_from_figure(
            robustness,
            "ToxPi Robustness Histogram",
        )
    return PbmToxPiOutput(tables=tables, charts=charts)


def _auto_workflow_chart_from_figure(fig, title: str) -> AutoWorkflowChart:
    try:
        png = figure_to_png_bytes(fig).getvalue()
        pdf = figure_to_pdf_bytes(fig).getvalue()
        return AutoWorkflowChart(title=title, png=png, pdf=pdf)
    finally:
        plt.close(fig)


def _build_identifier_input(representative: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame()
    output["compound"] = representative.get("Name", pd.Series(dtype=object)).map(_clean_text)
    output["smiles"] = representative.get("SMILES_input", pd.Series([""] * len(representative))).map(_clean_text)
    output["cas"] = representative.get("CAS_input", pd.Series([""] * len(representative))).map(_clean_text)
    output["ec"] = ""
    output["dtxsid"] = ""
    output["echa_id"] = ""
    return output[REQUIRED_IDENTIFIER_COLUMNS]


def _build_identifier_input_from_epi_universe(
    epi_universe: pd.DataFrame,
) -> pd.DataFrame:
    output = pd.DataFrame(index=epi_universe.index)
    for column in REQUIRED_IDENTIFIER_COLUMNS:
        if column in epi_universe.columns:
            output[column] = epi_universe[column].map(_clean_text)
        else:
            output[column] = ""
    identity_source = (
        "input_identity_key"
        if "input_identity_key" in epi_universe.columns
        else "identity_key"
    )
    if identity_source in epi_universe.columns:
        output["input_identity_key"] = epi_universe[identity_source].map(
            _clean_text
        )
    query_columns = [
        *REQUIRED_IDENTIFIER_COLUMNS,
        *(
            ["input_identity_key"]
            if "input_identity_key" in output.columns
            else []
        ),
    ]
    return output[query_columns].reset_index(drop=True)


def _restore_identity_keys(
    completed_identifiers: pd.DataFrame,
    epi_universe: pd.DataFrame,
) -> pd.DataFrame:
    completed = completed_identifiers.copy().reset_index(drop=True)
    universe = epi_universe.copy().reset_index(drop=True)
    if "identity_key" not in completed.columns:
        completed["identity_key"] = ""
    else:
        completed["identity_key"] = completed["identity_key"].map(_clean_text)

    if len(completed) == len(universe) and "identity_key" in universe.columns:
        universe_keys = universe["identity_key"].map(_clean_text)
        for position in completed.index:
            if not _clean_text(completed.at[position, "identity_key"]):
                completed.at[position, "identity_key"] = universe_keys.iloc[position]
        return completed

    for position, row in completed.iterrows():
        if _clean_text(row.get("identity_key")):
            continue
        completed.at[position, "identity_key"] = _identity_key_for_row(
            row,
            universe,
            cas_columns=("cas",),
            smiles_columns=("smiles",),
            name_columns=("compound",),
        )
    return completed


def _annotate_representative_identity_keys(
    representative: pd.DataFrame,
    primary_membership: pd.DataFrame,
    epi_universe: pd.DataFrame,
) -> pd.DataFrame:
    membership = (
        primary_membership.copy().reset_index(drop=True)
        if isinstance(primary_membership, pd.DataFrame)
        else pd.DataFrame()
    )
    universe = (
        epi_universe.copy().reset_index(drop=True)
        if isinstance(epi_universe, pd.DataFrame)
        else pd.DataFrame()
    )
    candidates = universe if not universe.empty else membership
    if candidates.empty and "identity_key" not in representative.columns:
        return representative.copy()

    annotated = representative.copy().reset_index(drop=True)
    if "identity_key" not in annotated.columns:
        annotated["identity_key"] = ""
    else:
        annotated["identity_key"] = annotated["identity_key"].map(_clean_text)

    for position, row in annotated.iterrows():
        if _clean_text(row.get("identity_key")):
            continue
        lineage_key = _identity_key_from_lineage(row, membership)
        if lineage_key:
            annotated.at[position, "identity_key"] = lineage_key
            continue
        annotated.at[position, "identity_key"] = _identity_key_for_row(
            row,
            candidates,
            cas_columns=("CAS_input", "cas"),
            smiles_columns=("SMILES_input", "smiles"),
            name_columns=("Name", "compound"),
        )
    return annotated


def _identity_key_from_lineage(
    row: pd.Series,
    membership: pd.DataFrame,
) -> str:
    if membership.empty or "identity_key" not in membership.columns:
        return ""

    scoped = membership.copy()
    compared = False
    lineage_columns = (
        (("primary_file", "_primary_file", "source_primary_file"), "primary_file"),
        (("sample_id", "_sample_id", "source_sample_id"), "sample_id"),
        (("source_row", "_source_row"), "source_row"),
    )
    for source_columns, target_column in lineage_columns:
        if target_column not in scoped.columns:
            continue
        source_value = _first_clean_value(row, source_columns)
        if not source_value:
            continue
        compared = True
        scoped = scoped.loc[
            scoped[target_column].map(_clean_text).map(str.casefold).eq(
                source_value.casefold()
            )
        ]
    if not compared or scoped.empty:
        return ""
    keys = sorted(
        {
            _clean_text(value)
            for value in scoped["identity_key"]
            if _clean_text(value)
        }
    )
    return keys[0] if len(keys) == 1 else ""


def _identity_key_for_row(
    row: pd.Series,
    candidates: pd.DataFrame,
    *,
    cas_columns: tuple[str, ...],
    smiles_columns: tuple[str, ...],
    name_columns: tuple[str, ...],
) -> str:
    if candidates.empty or "identity_key" not in candidates.columns:
        return ""

    cas = _first_clean_value(row, cas_columns).replace(" ", "").casefold()
    if cas:
        return _unique_identity_key(
            candidates,
            "cas",
            cas,
            lambda value: _clean_text(value).replace(" ", "").casefold(),
        )

    smiles = _first_clean_value(row, smiles_columns)
    if smiles:
        return _unique_identity_key(
            candidates,
            "smiles",
            smiles,
            _clean_text,
        )

    name = " ".join(
        _first_clean_value(row, name_columns).casefold().split()
    )
    if not name:
        return ""
    return _unique_identity_key(
        candidates,
        "compound",
        name,
        lambda value: " ".join(_clean_text(value).casefold().split()),
    )


def _unique_identity_key(
    candidates: pd.DataFrame,
    column: str,
    expected: str,
    normalizer,
) -> str:
    if column not in candidates.columns:
        return ""
    matched = candidates.loc[
        candidates[column].map(normalizer).eq(expected)
    ]
    keys = sorted(
        {
            _clean_text(value)
            for value in matched["identity_key"]
            if _clean_text(value)
        }
    )
    return keys[0] if len(keys) == 1 else ""


def _first_clean_value(row: pd.Series, columns: tuple[str, ...]) -> str:
    for column in columns:
        value = _clean_text(row.get(column))
        if value:
            return value
    return ""


def _query_input_from_identifiers(
    identifier_input: pd.DataFrame,
    completed_identifiers: pd.DataFrame,
) -> pd.DataFrame:
    output = identifier_input.copy()
    for column in REQUIRED_IDENTIFIER_COLUMNS:
        if column not in output.columns:
            output[column] = ""
        output[column] = output[column].map(_clean_text)

    query_columns = [
        *REQUIRED_IDENTIFIER_COLUMNS,
        *(
            ["input_identity_key"]
            if "input_identity_key" in output.columns
            else []
        ),
    ]
    if completed_identifiers is None or completed_identifiers.empty:
        return output[query_columns].reset_index(drop=True)

    completed = completed_identifiers.copy()
    for column in REQUIRED_IDENTIFIER_COLUMNS:
        if column not in completed.columns:
            completed[column] = ""
        completed[column] = completed[column].map(_clean_text)
    completed_by_key = {
        _compound_key(row["compound"]): row
        for _, row in completed.iterrows()
        if _compound_key(row["compound"])
    }
    for index, compound in output["compound"].items():
        completed_row = completed_by_key.get(_compound_key(compound))
        if completed_row is None:
            continue
        for column in REQUIRED_IDENTIFIER_COLUMNS[1:]:
            enriched_value = _clean_text(completed_row[column])
            if enriched_value:
                output.at[index, column] = enriched_value
        if "input_identity_key" in output.columns:
            identity_value = _clean_text(
                completed_row.get(
                    "input_identity_key",
                    completed_row.get("identity_key"),
                )
            )
            if identity_value:
                output.at[index, "input_identity_key"] = identity_value
    return output[query_columns].reset_index(drop=True)


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
