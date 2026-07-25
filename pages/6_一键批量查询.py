import io
from collections import OrderedDict
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from src.auto_query_workflow import (
    AUTO_WORKFLOW_CHECKPOINT_EXPORTS,
    AutoWorkflowCheckpoint,
    AutoWorkflowCheckpointContext,
    AutoWorkflowConfig,
    AutoWorkflowEpiRetryError,
    AutoWorkflowMapping,
    AutoWorkflowResult,
    PUBLIC_TABLE_NAMES,
    auto_input_from_multi_file_result,
    build_auto_workflow_charts,
    build_auto_workflow_module_download,
    build_auto_workflow_module_workbook,
    build_auto_workflow_partial_zip,
    build_auto_workflow_zip,
    queryable_epi_retry_input,
    retry_auto_workflow_epi_failures,
    run_auto_query_workflow,
)
from src.auto_query_checkpoint import (
    CheckpointStorageError,
    ExpiredCheckpoint,
    cleanup_expired_checkpoints,
    delete_checkpoint,
    generate_run_token,
    load_checkpoint,
    save_checkpoint,
)
from src.cp_screening_workflow import PBMToxPiConfig
from src.episuite_io import ENDPOINT_KEYS
from src.episuite_result_pool import advance_epi_uploader_epoch, read_epi_pool
from src.episuite_supplement import (
    EPISupplementMapping,
    inspect_epi_workbook,
    parse_epi_supplement,
    resolve_epi_sources,
    suggest_primary_filename,
)
from src.image_safety import is_png_over_pixel_limit, png_dimensions
from src.multi_file_screening import (
    SampleColumnMapping,
    build_primary_epi_membership,
    build_primary_epi_universe,
    build_upload_structure_preparation_preview,
    default_sample_mapping,
    prepare_multi_file_screening,
    read_primary_workbooks,
)
from src.query_cache import clear_query_cache, current_cache_path
from src.mol_structure_parser import prepare_structure_dataframe, summarize_structure_preparation
from src.r_screening_replica.schema import ScreeningAxisRanges
from src.auto_query_progress import (
    build_selected_steps,
    create_progress_state,
    format_activity_message,
    progress_snapshot,
    record_activity_event,
)
from src.upload_state import (
    cached_uploads,
    clear_uploads,
    invalidate_recovered_results_on_settings_mismatch,
    invalidate_results_on_settings_change,
    settings_signature,
    store_uploads,
    typed_settings_value,
    upload_bytes,
    upload_name,
    upload_signature,
)


MAX_CHART_PIXELS = 50_000_000


INPUT_CACHE_KEYS = (
    "auto_query_input_files",
    "auto_query_input_signature",
)
EPI_SUPPLEMENT_CACHE_KEYS = (
    "auto_query_epi_supplement_files",
    "auto_query_epi_supplement_signature",
)
RESULT_CACHE_KEYS = (
    "auto_query_workflow_result",
    "auto_query_workflow_charts",
    "auto_query_workflow_zip",
)
CHECKPOINT_STATE_KEYS = (
    "auto_query_run_token",
    "auto_query_checkpoint_manifest",
    "auto_query_partial_result",
    "auto_query_module_workbooks",
    "auto_query_checkpoint_warning",
)
SETTINGS_SIGNATURE_KEY = "auto_query_settings_signature"
EPI_SUPPLEMENT_UPLOADER_EPOCH_KEY = (
    "auto_query_epi_supplement_uploader_epoch"
)


def _clear_epi_mapping_widget_state():
    for key in list(st.session_state):
        if str(key).startswith("auto_epi_"):
            st.session_state.pop(key, None)


def clear_epi_supplement_state():
    clear_uploads(
        st.session_state,
        (
            *EPI_SUPPLEMENT_CACHE_KEYS,
            *RESULT_CACHE_KEYS,
            *CHECKPOINT_STATE_KEYS,
        ),
    )
    _clear_epi_mapping_widget_state()
    advance_epi_uploader_epoch(
        st.session_state,
        EPI_SUPPLEMENT_UPLOADER_EPOCH_KEY,
    )
    st.query_params.pop("run", None)


def clear_auto_query_state():
    token = st.session_state.get("auto_query_run_token") or st.query_params.get("run")
    if token:
        try:
            delete_checkpoint(token)
        except (CheckpointStorageError, OSError):
            pass
    clear_uploads(
        st.session_state,
        (
            *INPUT_CACHE_KEYS,
            *EPI_SUPPLEMENT_CACHE_KEYS,
            *RESULT_CACHE_KEYS,
            *CHECKPOINT_STATE_KEYS,
        ),
    )
    st.session_state.pop(SETTINGS_SIGNATURE_KEY, None)
    st.session_state.pop("auto_query_upload", None)
    _clear_epi_mapping_widget_state()
    advance_epi_uploader_epoch(
        st.session_state,
        EPI_SUPPLEMENT_UPLOADER_EPOCH_KEY,
    )
    st.query_params.pop("run", None)


st.set_page_config(
    page_title="一键批量查询 - ChemPriority",
    page_icon="⚙️",
    layout="wide",
)

st.title("一键批量查询")
st.caption("上传统一格式 Excel，勾选需要运行的项目后，系统按依赖顺序逐项自动执行。")


def _column_index(columns, value):
    return columns.index(value) if value in columns else 0


def _optional_column_index(columns, value):
    options = ["", *columns]
    return options.index(value) if value in options else 0


def _widget_key(prefix, file_name, index):
    digest = settings_signature([prefix, file_name, index])[:12]
    return f"{prefix}_{digest}"


def _default_column(columns, candidates):
    casefolded = {
        str(column).strip().casefold(): column
        for column in columns
    }
    for candidate in candidates:
        matched = casefolded.get(str(candidate).strip().casefold())
        if matched is not None:
            return matched
    return None


def _epi_supplement_settings_payload(mapping):
    payload = asdict(mapping)
    for key in ("compound_col", "smiles_col", "cas_col"):
        selected = payload.get(key)
        payload[key] = (
            typed_settings_value(selected)
            if selected is not None
            else None
        )
    payload["endpoint_columns"] = {
        endpoint: typed_settings_value(selected)
        for endpoint, selected in mapping.endpoint_columns.items()
    }
    return payload


def _render_sample_mapping_tabs(samples):
    sample_mappings = {}
    mapping_tabs = st.tabs([sample.sample_id for sample in samples])
    for index, (tab, sample) in enumerate(zip(mapping_tabs, samples)):
        with tab:
            columns = list(sample.data.columns)
            defaults = default_sample_mapping(sample)
            st.caption(
                f"{sample.file_name} | {len(sample.data)} 行 | {len(columns)} 列"
            )
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                compound_col = st.selectbox(
                    "化合物名称列",
                    columns,
                    index=_column_index(columns, defaults.compound_col),
                    key=_widget_key(
                        "auto_compound_col",
                        sample.file_name,
                        index,
                    ),
                )
            with col_b:
                formula_col = st.selectbox(
                    "分子式列",
                    columns,
                    index=_column_index(columns, defaults.formula_col),
                    key=_widget_key(
                        "auto_formula_col",
                        sample.file_name,
                        index,
                    ),
                )
            with col_c:
                peak_area_col = st.selectbox(
                    "默认峰面积列",
                    columns,
                    index=_column_index(columns, defaults.peak_area_col),
                    key=_widget_key(
                        "auto_peak_area_col",
                        sample.file_name,
                        index,
                    ),
                )

            group_area_cols = st.multiselect(
                "参与化学类型图、DBE图、VK图、DF 和 ToxPi 的 Group Area 列",
                columns,
                default=[
                    column
                    for column in defaults.group_area_cols
                    if column in columns
                ],
                key=_widget_key(
                    "auto_group_area_cols",
                    sample.file_name,
                    index,
                ),
            )
            optional_columns = ["", *columns]
            opt_a, opt_b, opt_c = st.columns(3)
            with opt_a:
                mol_column = st.selectbox(
                    "可选：MOL 文本列",
                    optional_columns,
                    index=_optional_column_index(
                        columns,
                        defaults.mol_column,
                    ),
                    key=_widget_key(
                        "auto_mol_col",
                        sample.file_name,
                        index,
                    ),
                ) or None
            with opt_b:
                smiles_col = st.selectbox(
                    "可选：已有 SMILES 列",
                    optional_columns,
                    index=_optional_column_index(
                        columns,
                        defaults.smiles_col,
                    ),
                    key=_widget_key(
                        "auto_smiles_col",
                        sample.file_name,
                        index,
                    ),
                ) or None
            with opt_c:
                cas_col = st.selectbox(
                    "可选：已有 CAS 列",
                    optional_columns,
                    index=_optional_column_index(columns, defaults.cas_col),
                    key=_widget_key(
                        "auto_cas_col",
                        sample.file_name,
                        index,
                    ),
                ) or None

            sample_mappings[sample.sample_id] = SampleColumnMapping(
                compound_col=compound_col,
                formula_col=formula_col,
                peak_area_col=peak_area_col,
                group_area_cols=tuple(group_area_cols),
                mol_column=mol_column,
                smiles_col=smiles_col,
                cas_col=cas_col,
            )
    return sample_mappings


def _render_epi_supplement_mappings(
    active_supplements,
    primary_names,
):
    mappings = []
    parsed_frames = []
    warnings = []
    for index, record in enumerate(active_supplements):
        source_file = upload_name(record)
        payload = upload_bytes(record)
        try:
            inspection = inspect_epi_workbook(payload, source_file)
        except Exception as exc:
            st.error(f"{source_file} 读取失败：{exc}")
            continue
        if not inspection.sheet_names:
            st.error(f"{source_file} 不包含可读取的工作表。")
            continue

        with st.expander(source_file, expanded=True):
            suggested = suggest_primary_filename(source_file, primary_names)
            primary_options = ["", *primary_names]
            selected_primary = st.selectbox(
                "关联主 Excel 文件",
                primary_options,
                index=(
                    primary_options.index(suggested)
                    if suggested in primary_options
                    else 0
                ),
                help=(
                    "系统只根据补充文件名建议关联；不会根据化学物重合自动选择。"
                    "请确认或手动修正。"
                ),
                key=_widget_key("auto_epi_primary", source_file, index),
            )
            default_sheet = (
                inspection.default_result_sheet
                or inspection.sheet_names[0]
            )
            selected_sheet = st.selectbox(
                "EPI 结果工作表",
                list(inspection.sheet_names),
                index=list(inspection.sheet_names).index(default_sheet),
                key=_widget_key("auto_epi_sheet", source_file, index),
            )
            try:
                sheet_frame = pd.read_excel(
                    io.BytesIO(payload),
                    sheet_name=selected_sheet,
                )
            except Exception as exc:
                st.error(f"{source_file} / {selected_sheet} 读取失败：{exc}")
                continue
            columns = list(sheet_frame.columns)
            optional_columns = ["", *columns]
            id_a, id_b, id_c = st.columns(3)
            identifier_defaults = {
                "compound": _default_column(
                    columns,
                    ("compound", "Name", "Chemical name"),
                ),
                "smiles": _default_column(
                    columns,
                    ("smiles", "SMILES", "canonical_smiles"),
                ),
                "cas": _default_column(
                    columns,
                    ("cas", "CAS", "CASRN", "CAS No."),
                ),
            }
            with id_a:
                compound_col = st.selectbox(
                    "化合物名称标识列",
                    optional_columns,
                    index=_optional_column_index(
                        columns,
                        identifier_defaults["compound"],
                    ),
                    key=_widget_key(
                        "auto_epi_compound",
                        source_file,
                        index,
                    ),
                    format_func=lambda value: str(value).strip(),
                ) or None
            with id_b:
                smiles_col = st.selectbox(
                    "SMILES 标识列",
                    optional_columns,
                    index=_optional_column_index(
                        columns,
                        identifier_defaults["smiles"],
                    ),
                    key=_widget_key(
                        "auto_epi_smiles",
                        source_file,
                        index,
                    ),
                    format_func=lambda value: str(value).strip(),
                ) or None
            with id_c:
                cas_col = st.selectbox(
                    "CAS 标识列",
                    optional_columns,
                    index=_optional_column_index(
                        columns,
                        identifier_defaults["cas"],
                    ),
                    key=_widget_key(
                        "auto_epi_cas",
                        source_file,
                        index,
                    ),
                    format_func=lambda value: str(value).strip(),
                ) or None

            priority = st.number_input(
                "来源优先级（数字越小越优先）",
                min_value=0,
                value=index,
                step=1,
                key=_widget_key("auto_epi_priority", source_file, index),
            )
            endpoint_columns = {}
            with st.expander("EPI 终点列映射", expanded=False):
                endpoint_groups = st.columns(2)
                for endpoint_index, endpoint in enumerate(ENDPOINT_KEYS):
                    default_endpoint = _default_column(columns, (endpoint,))
                    with endpoint_groups[endpoint_index % 2]:
                        selected = st.selectbox(
                            endpoint,
                            optional_columns,
                            index=_optional_column_index(
                                columns,
                                default_endpoint,
                            ),
                            key=_widget_key(
                                f"auto_epi_endpoint_{endpoint}",
                                source_file,
                                index,
                            ),
                            format_func=lambda value: str(value).strip(),
                        )
                    if selected:
                        endpoint_columns[endpoint] = selected

            mapping = EPISupplementMapping(
                source_file=source_file,
                primary_file=selected_primary,
                sheet_name=selected_sheet,
                compound_col=compound_col,
                smiles_col=smiles_col,
                cas_col=cas_col,
                endpoint_columns=endpoint_columns,
                priority=int(priority),
            )
            mappings.append(mapping)
            if not selected_primary:
                st.warning("请选择此补充文件对应的主 Excel 文件。")
                continue
            try:
                parsed, parse_warnings = parse_epi_supplement(payload, mapping)
            except Exception as exc:
                st.error(
                    f"{source_file} / {selected_sheet} 解析失败：{exc}"
                )
                continue
            parsed_frames.append(parsed)
            if not parse_warnings.empty:
                warning_table = parse_warnings.copy()
                if "source_file" in warning_table.columns:
                    warning_table["source_file"] = (
                        warning_table["source_file"]
                        .fillna(source_file)
                        .replace("", source_file)
                    )
                else:
                    warning_table.insert(0, "source_file", source_file)
                warnings.append(warning_table)
    return (
        mappings,
        (
            pd.concat(parsed_frames, ignore_index=True)
            if parsed_frames
            else pd.DataFrame()
        ),
        (
            pd.concat(warnings, ignore_index=True)
            if warnings
            else pd.DataFrame()
        ),
    )


def _show_dataframe(frame):
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _render_structure_preparation_summary(prepared_df):
    summary = summarize_structure_preparation(prepared_df)
    st.caption("结构准备（MOL / SMILES）")
    labels = ["MOL 行", "解析成功", "修复 M END", "SMILES 冲突", "解析失败"]
    values = [
        summary["mol_rows"],
        summary["parsed_success"],
        summary["repaired_m_end"],
        summary["smiles_conflicts"],
        summary["parse_failures"],
    ]
    for column, label, value in zip(st.columns(5), labels, values):
        column.metric(label, value)
    if summary["smiles_conflicts"] or summary["parse_failures"]:
        with st.expander("查看结构准备审计记录", expanded=False):
            mask = prepared_df["smiles_source"].eq("原始 SMILES（与 MOL 冲突）") | prepared_df["parse_status"].eq("解析失败")
            _show_dataframe(prepared_df.loc[mask])


def _result_dashboard_groups(result, charts):
    definitions = [
        (
            "screening",
            "本地筛查",
            [
                "Structure_Preparation",
                "Input_Check",
                "Elemental_Ratios_DBE",
                "Category_Summary",
                "DF_Table",
                "Sample_Peak_Area",
                "Group_Area_Raw_Long",
                "Group_Area_Mean_By_Sample",
                "Plot_Warnings",
            ],
            ("Local_",),
        ),
        ("identifier", "标识符补全", ["Identifier_Completion", "Identifier_Warnings"], ()),
        ("epi", "EPI Suite", ["EPI_Results", "EPI_Raw_Results", "EPI_Errors"], ()),
        (
            "comptox",
            "EPA CompTox",
            [
                "CompTox_Summary",
                "Product_Use_Categories",
                "EPA_PUC_Pie_Data",
                "Functional_Uses_Predicted",
                "Functional_Uses_Reported",
                "EPA_Predicted_Pie_Data",
                "EPA_Reported_Pie_Data",
                "CompTox_Errors",
            ],
            ("EPA_",),
        ),
        (
            "echa",
            "ECHA",
            [
                "ECHA_Use_Summary",
                "ECHA_Uses_Reported",
                "ECHA_Reported_Pie_Data",
                "ECHA_Use_Dossiers",
                "ECHA_Use_Errors",
                "ECHA_GHS_Summary",
                "ECHA_GHS_Classifications",
                "ECHA_GHS_Errors",
            ],
            ("ECHA_",),
        ),
        (
            "source",
            "来源属性",
            [
                "Source_Origin_Summary",
                "Source_Origin_Evidence",
                "Source_Origin_Errors",
                "Source_Origin_Pie_Data",
            ],
            ("Source_",),
        ),
        (
            "toxpi",
            "Pov-LRTP / PBM / ToxPi",
            [
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
            ],
            ("ToxPi_",),
        ),
    ]
    available_charts = charts or {}
    groups = []
    for key, label, table_candidates, chart_prefixes in definitions:
        table_names = [
            name
            for name in table_candidates
            if isinstance(result.tables.get(name), pd.DataFrame) and not result.tables[name].empty
        ]
        chart_keys = [
            chart_key
            for chart_key in available_charts
            if any(chart_key.startswith(prefix) for prefix in chart_prefixes)
        ]
        if table_names or chart_keys:
            groups.append(
                {
                    "key": key,
                    "label": label,
                    "table_names": table_names,
                    "chart_keys": chart_keys,
                }
            )
    return groups


def _is_audit_table(table_name):
    return table_name.endswith(("_Errors", "_Warnings", "_Raw_Results")) or table_name in {
        "Structure_Preparation",
        "Plot_Warnings",
        "EPA_Predicted_Pie_Data",
        "EPA_Reported_Pie_Data",
        "ECHA_Reported_Pie_Data",
        "Source_Origin_Pie_Data",
        "ECHA_Use_Dossiers",
        "ECHA_GHS_Classifications",
        "ToxPi_Settings",
        "ToxPi_Robustness",
        "ToxPi_Robust_Stats",
    }


def _render_result_dashboard(result, charts):
    groups = _result_dashboard_groups(result, charts)
    if not groups:
        return

    st.subheader("结果总览")
    tabs = st.tabs([group["label"] for group in groups])
    for tab, group in zip(tabs, groups):
        with tab:
            for table_name in group["table_names"]:
                table = result.tables[table_name]
                if _is_audit_table(table_name):
                    with st.expander(table_name, expanded=False):
                        _show_dataframe(table)
                else:
                    st.caption(table_name)
                    _show_dataframe(table)
            _render_missing_evidence_chart_notice(group["key"], result, charts)
            for chart_key in group["chart_keys"]:
                chart = charts[chart_key]
                _render_chart_image(chart)


def _render_chart_image(chart):
    if is_png_over_pixel_limit(chart.png, MAX_CHART_PIXELS):
        width, height = png_dimensions(chart.png)
        st.warning(
            f"{chart.title} 未在页面显示：PNG 尺寸为 {width:,} × {height:,}，"
            f"超过 {MAX_CHART_PIXELS:,} 像素安全上限。请重新运行以生成受限图表。"
        )
        return
    st.image(chart.png, caption=chart.title)


def _render_missing_evidence_chart_notice(group_key, result, charts):
    definitions = {
        "comptox": ("EPA_Reported_Pie_Data", "EPA_Reported_Functional_Use_Evidence"),
        "echa": ("ECHA_Reported_Pie_Data", "ECHA_Reported_Use_Evidence"),
    }
    definition = definitions.get(group_key)
    if definition is None:
        return
    source_table, chart_key = definition
    if not isinstance(result.tables.get(source_table), pd.DataFrame) or chart_key in charts:
        return
    toxpi_results = result.tables.get("ToxPi_Results")
    has_valid_ranking = (
        isinstance(toxpi_results, pd.DataFrame)
        and not toxpi_results.empty
        and {"compound", "final_rank"}.issubset(toxpi_results.columns)
        and pd.to_numeric(toxpi_results["final_rank"], errors="coerce").gt(0).any()
    )
    if not has_valid_ranking:
        st.info(
            "逐化合物证据图未生成：请启用并成功完成 Pov-LRTP / PBM / ToxPi，"
            "以使用同一套 ToxPi Candidate Top N 排名筛选化合物。"
        )
    else:
        st.info("ToxPi Candidate Top N 化合物中没有可绘制的已报告用途证据。")


def _render_module_downloads(
    result,
    module_workbooks,
    charts=None,
    *,
    key_prefix="auto_query_module",
):
    charts = charts or {}
    if result.step_status.empty:
        return
    st.subheader("已完成模块，可立即下载")
    modules_by_step = {
        module.step: (slug, module) for slug, module in module_workbooks.items()
    }
    for row in result.step_status.to_dict("records"):
        step = str(row["step"])
        warning_count = 0
        if not result.warnings.empty and "stage" in result.warnings.columns:
            warning_count = int(result.warnings["stage"].eq(step).sum())
        rows_value = row.get("rows")
        row_count = 0 if pd.isna(rows_value) else int(rows_value)
        st.caption(
            f"{step}：{row['status']} · {row_count} 行 · {warning_count} 条警告"
        )
        if row.get("message"):
            st.warning(str(row["message"]))
        export_definition = AUTO_WORKFLOW_CHECKPOINT_EXPORTS.get(step)
        preview = None
        if export_definition is not None:
            preview = next(
                (
                    result.tables[name]
                    for name in export_definition[2]
                    if isinstance(result.tables.get(name), pd.DataFrame)
                    and not result.tables[name].empty
                ),
                None,
            )
        if preview is not None:
            with st.expander(f"预览 {step} 关键结果", expanded=False):
                _show_dataframe(preview.head(20))
        export = modules_by_step.get(step)
        if export is None:
            st.caption("该模块当前没有可导出的结果表。")
            continue
        slug, module = export
        try:
            download = build_auto_workflow_module_download(module, charts)
        except Exception as exc:
            st.warning(f"{module.step} 下载包生成失败：{exc}")
            continue
        st.download_button(
            f"下载 {module.step}",
            data=download.data,
            file_name=download.file_name,
            mime=download.mime,
            key=f"{key_prefix}_download_{slug}",
            on_click="ignore",
        )


def _render_saved_results(
    result,
    charts,
    full_package=None,
    module_workbooks=None,
    partial=False,
):
    module_workbooks = module_workbooks or OrderedDict()
    st.subheader("运行日志")
    _show_dataframe(result.step_status)
    if not result.warnings.empty:
        with st.expander("Warnings", expanded=False):
            _show_dataframe(result.warnings)
    table_names = [name for name in result.tables if name in PUBLIC_TABLE_NAMES]
    if table_names:
        selected_table = st.selectbox("查看结果表", table_names, key="auto_query_result_table")
        _show_dataframe(result.tables[selected_table])
    structure_preparation = result.tables.get("Structure_Preparation")
    if isinstance(structure_preparation, pd.DataFrame):
        _render_structure_preparation_summary(structure_preparation)
    _render_result_dashboard(result, charts)
    _render_module_downloads(result, module_workbooks, charts)
    if partial:
        try:
            partial_zip = build_auto_workflow_partial_zip(
                result,
                module_workbooks,
                charts=charts,
            )
        except Exception as exc:
            st.warning(f"部分结果 ZIP 生成失败：{exc}")
        else:
            st.download_button(
                "下载部分结果 ZIP",
                data=partial_zip.getvalue(),
                file_name="Auto_Query_Workflow_Partial_Results.zip",
                mime="application/zip",
                key="auto_query_partial_zip_download",
                on_click="ignore",
            )
    if full_package is not None:
        st.download_button(
            "下载一键批量查询结果 ZIP",
            data=full_package.getvalue(),
            file_name="Auto_Query_Workflow_Results.zip",
            mime="application/zip",
            key="auto_query_full_zip_download",
            on_click="ignore",
        )


try:
    cleanup_expired_checkpoints()
except (CheckpointStorageError, OSError) as exc:
    st.session_state["auto_query_checkpoint_warning"] = str(exc)

recovery_token = st.query_params.get("run")
if recovery_token and st.session_state.get("auto_query_run_token") != recovery_token:
    try:
        loaded = load_checkpoint(recovery_token)
    except ExpiredCheckpoint:
        st.warning("上次结果已超过 24 小时，不能恢复。")
        st.query_params.pop("run", None)
    except CheckpointStorageError as exc:
        st.warning(f"无法恢复上次结果：{exc}")
        st.query_params.pop("run", None)
    else:
        checkpoint = loaded.checkpoint
        clear_uploads(
            st.session_state,
            (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
        )
        st.session_state["auto_query_run_token"] = recovery_token
        st.session_state["auto_query_checkpoint_manifest"] = loaded.manifest
        st.session_state["auto_query_partial_result"] = checkpoint.result
        st.session_state["auto_query_workflow_result"] = checkpoint.result
        st.session_state["auto_query_module_workbooks"] = loaded.module_workbooks
        st.session_state["auto_query_workflow_charts"] = checkpoint.result.charts
        st.success("已恢复上次运行的部分结果。")
        st.caption(
            "恢复网址包含短期访问令牌，请勿分享；临时结果 24 小时后过期，"
            "服务器重新部署后不保证保留。"
        )
        if checkpoint.status in {"running", "failed"}:
            st.warning(
                "上次运行未正常结束；已完成结果可下载，"
                "重新运行会复用查询缓存。"
            )
            if checkpoint.error_message:
                st.caption(f"上次错误：{checkpoint.error_message}")


uploaded_files = st.file_uploader(
    "上传一个或多个 Excel 文件",
    type=["xlsx", "xls"],
    accept_multiple_files=True,
    help=(
        "每个文件按第一个工作表读取，并分别设置列映射；"
        "文件名 stem 作为 sample_id。"
    ),
    key="auto_query_upload",
)

if uploaded_files:
    active_uploads, input_changed = store_uploads(
        st.session_state,
        "auto_query_input_files",
        "auto_query_input_signature",
        uploaded_files,
    )
    if input_changed:
        clear_uploads(
            st.session_state,
            (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
        )
        st.query_params.pop("run", None)
else:
    active_uploads = cached_uploads(st.session_state, "auto_query_input_files")

if st.button("清空当前数据", key="auto_clear_cached_input"):
    clear_auto_query_state()
    st.rerun()

if not active_uploads:
    recovered = st.session_state.get("auto_query_partial_result")
    checkpoint_warning = st.session_state.get("auto_query_checkpoint_warning")
    if checkpoint_warning:
        st.warning(checkpoint_warning)
    if recovered is not None:
        _render_saved_results(
            recovered,
            st.session_state.get("auto_query_workflow_charts") or {},
            module_workbooks=st.session_state.get("auto_query_module_workbooks")
            or OrderedDict(),
            partial=True,
        )
    else:
        st.info("请先上传 Excel 文件。")
    st.stop()

primary_names = [upload_name(record) for record in active_uploads]
duplicate_names = sorted(
    {
        name
        for name in primary_names
        if sum(
            candidate.casefold() == name.casefold()
            for candidate in primary_names
        )
        > 1
    },
    key=str.casefold,
)
if duplicate_names:
    st.error(
        "主 Excel 文件名重复，请重命名后重新上传："
        + "、".join(duplicate_names)
    )
    st.stop()

sample_stems = [Path(name).stem for name in primary_names]
duplicate_stems = sorted(
    {
        stem
        for stem in sample_stems
        if sum(
            candidate.casefold() == stem.casefold()
            for candidate in sample_stems
        )
        > 1
    },
    key=str.casefold,
)
if duplicate_stems:
    st.error(
        "主 Excel 样品名称重复，请重命名后重新上传："
        + "、".join(duplicate_stems)
    )
    st.stop()

try:
    samples = read_primary_workbooks(active_uploads)
except Exception as exc:
    st.error(f"Excel 读取失败：{exc}")
    st.stop()

st.session_state["auto_query_primary_file_names"] = [
    sample.file_name for sample in samples
]
st.success(f"已加载输入文件：{len(samples)} 个。")

st.subheader("输入文件检查与逐文件列映射")
preview_rows = [
    {
        "sample_id": sample.sample_id,
        "file_name": sample.file_name,
        "rows": len(sample.data),
        "columns": len(sample.data.columns),
    }
    for sample in samples
]
_show_dataframe(pd.DataFrame(preview_rows))
sample_mappings = _render_sample_mapping_tabs(samples)
primary_epi_membership = build_primary_epi_membership(
    samples,
    sample_mappings,
)
primary_epi_universe = build_primary_epi_universe(
    samples,
    sample_mappings,
)

upload_structure_summary, prepared_input_df = (
    build_upload_structure_preparation_preview(samples, sample_mappings)
)
st.caption("多文件结构准备摘要")
_show_dataframe(upload_structure_summary)
if not prepared_input_df.empty:
    _render_structure_preparation_summary(prepared_input_df)

first_mapping = sample_mappings[samples[0].sample_id]
mapping = AutoWorkflowMapping(
    compound_col=first_mapping.compound_col,
    formula_col=first_mapping.formula_col,
    peak_area_col=first_mapping.peak_area_col,
    group_area_cols=list(first_mapping.group_area_cols),
    mol_column=first_mapping.mol_column,
    smiles_col=first_mapping.smiles_col,
    cas_col=first_mapping.cas_col,
)

with st.expander("查看各文件前 20 行", expanded=False):
    for sample in samples:
        st.caption(sample.file_name)
        _show_dataframe(sample.data.head(20))

st.subheader("选择自动运行项目")
col_left, col_right = st.columns(2)
with col_left:
    run_r_replicate_df = st.checkbox("化学类型图、DBE图、VK图与 DF", value=True)
    run_identifier = st.checkbox("标识符补全", value=True)
    run_epi = st.checkbox("EPI Suite 环境归趋", value=False)
    run_pov_toxpi = st.checkbox("Pov-LRTP / PBM / ToxPi", value=False)
with col_right:
    run_comptox = st.checkbox("EPA CompTox 用途", value=False)
    run_echa_use = st.checkbox("ECHA REACH 用途", value=False)
    run_echa_ghs = st.checkbox("ECHA GHS/C&L 危害", value=False)
    run_source_origin = st.checkbox("来源属性评估", value=False)

if run_pov_toxpi and not run_epi:
    st.info("Pov-LRTP / PBM / ToxPi 需要 EPI 结果；运行时会自动先执行 EPI Suite 环境归趋。")

active_epi_supplements = cached_uploads(
    st.session_state,
    "auto_query_epi_supplement_files",
)
supplement_mappings = []
parsed_supplements = pd.DataFrame()
session_pool_results, _ = read_epi_pool(st.session_state)
if run_epi or run_pov_toxpi:
    st.subheader("EPI 补充结果")
    epi_supplement_epoch = st.session_state.get(
        EPI_SUPPLEMENT_UPLOADER_EPOCH_KEY,
        0,
    )
    epi_uploads = st.file_uploader(
        "上传 EPI 补充 Excel",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key=f"auto_query_epi_supplements_{epi_supplement_epoch}",
    )
    if st.button(
        "仅清空 EPI 补充文件",
        key="auto_clear_epi_supplements",
    ):
        clear_epi_supplement_state()
        st.rerun()
    if epi_uploads:
        active_epi_supplements, epi_input_changed = store_uploads(
            st.session_state,
            "auto_query_epi_supplement_files",
            "auto_query_epi_supplement_signature",
            epi_uploads,
        )
        if epi_input_changed:
            clear_uploads(
                st.session_state,
                (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
            )
            st.query_params.pop("run", None)

    if active_epi_supplements:
        st.caption(
            "逐个确认补充文件关联。自动建议只使用文件名，不会根据化学物重合自动关联。"
        )
        (
            supplement_mappings,
            parsed_supplements,
            supplement_warnings,
        ) = _render_epi_supplement_mappings(
            active_epi_supplements,
            primary_names,
        )
        if not supplement_warnings.empty:
            with st.expander("EPI 补充文件解析警告", expanded=False):
                _show_dataframe(supplement_warnings)
    else:
        st.caption("可选：上传已有 EPI 结果，减少网络查询。")

    st.caption(f"同一会话 EPI 结果池：{len(session_pool_results)} 条。")
    preview_resolution = resolve_epi_sources(
        primary_epi_universe,
        parsed_supplements,
        session_pool_results,
        require_core=bool(run_pov_toxpi),
        primary_membership=primary_epi_membership,
    )
    completeness = preview_resolution.completeness
    match_audit = preview_resolution.match_audit
    preview_columns = st.columns(4)
    preview_columns[0].metric(
        "完整",
        (
            int(completeness["complete"].sum())
            if "complete" in completeness
            else 0
        ),
    )
    preview_columns[1].metric(
        "匹配",
        (
            int(match_audit["match_status"].eq("matched").sum())
            if "match_status" in match_audit
            else 0
        ),
    )
    preview_columns[2].metric(
        "冲突",
        len(preview_resolution.conflict_audit),
    )
    preview_columns[3].metric(
        "溯源",
        len(preview_resolution.provenance),
    )
    preview_tabs = st.tabs(["完整度", "匹配审计", "冲突审计", "来源溯源"])
    for tab, frame in zip(
        preview_tabs,
        (
            completeness,
            match_audit,
            preview_resolution.conflict_audit,
            preview_resolution.provenance,
        ),
    ):
        with tab:
            if frame.empty:
                st.caption("暂无记录。")
            else:
                _show_dataframe(frame)

workflow_input_signature = settings_signature(
    {
        "primary": upload_signature(active_uploads),
        "epi_supplements": (
            upload_signature(active_epi_supplements)
            if active_epi_supplements
            else ""
        ),
    }
)
checkpoint_manifest = st.session_state.get("auto_query_checkpoint_manifest") or {}
checkpoint_input_signature = checkpoint_manifest.get("input_signature")
if (
    workflow_input_signature
    and checkpoint_input_signature
    and workflow_input_signature != checkpoint_input_signature
):
    clear_uploads(
        st.session_state,
        (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
    )
    st.query_params.pop("run", None)

with st.expander("运行设置", expanded=False):
    col_threshold, col_cache = st.columns(2)
    with col_threshold:
        detection_threshold = st.number_input(
            "DF 检出阈值",
            min_value=0.0,
            value=1e5,
            step=10000.0,
            format="%.0f",
        )
    with col_cache:
        cache_enabled = st.checkbox("启用本地查询缓存", value=True)
        st.caption(f"缓存文件：{current_cache_path()}")
        if st.button("清理本地查询缓存", key="auto_clear_query_cache"):
            clear_query_cache()
            st.success("本地查询缓存已清理。")

    speed_a, speed_b, speed_c = st.columns(3)
    with speed_a:
        identifier_max_workers = st.number_input("标识符并发数", min_value=1, max_value=8, value=3, step=1)
        epi_max_workers = st.number_input("EPI 并发数", min_value=1, max_value=8, value=3, step=1)
    with speed_b:
        comptox_max_workers = st.number_input("CompTox 并发数", min_value=1, max_value=8, value=3, step=1)
        echa_max_workers = st.number_input("ECHA 用途并发数", min_value=1, max_value=8, value=2, step=1)
    with speed_c:
        echa_ghs_max_workers = st.number_input("ECHA GHS 并发数", min_value=1, max_value=8, value=2, step=1)
        source_origin_max_workers = st.number_input("来源属性并发数", min_value=1, max_value=8, value=2, step=1)

    st.caption("本地筛查图坐标范围")
    axis_dbe_x, axis_dbe_y, axis_vk_x, axis_vk_y = st.columns(4)
    with axis_dbe_x:
        dbe_x_min = st.number_input("DBE X 最小值", value=0.0)
        dbe_x_max = st.number_input("DBE X 最大值", value=60.0)
    with axis_dbe_y:
        dbe_y_min = st.number_input("DBE Y 最小值", value=0.0)
        dbe_y_max = st.number_input("DBE Y 最大值", value=30.0)
    with axis_vk_x:
        vk_x_min = st.number_input("Van Krevelen X 最小值", value=0.0)
        vk_x_max = st.number_input("Van Krevelen X 最大值", value=1.1)
    with axis_vk_y:
        vk_y_min = st.number_input("Van Krevelen Y 最小值", value=0.0)
        vk_y_max = st.number_input("Van Krevelen Y 最大值", value=2.6)

    st.caption("ToxPi 两阶段排名与稳健性")
    toxpi_top_n, toxpi_weights = st.columns(2)
    with toxpi_top_n:
        candidate_top_n = st.number_input("Candidate Top N", min_value=1, value=100, step=1)
        display_top_n = st.number_input("Display Top N", min_value=1, value=20, step=1)
        evidence_per_compound_top_n = st.number_input(
            "每个化合物证据 Top N",
            min_value=1,
            value=10,
            step=1,
        )
        evidence_global_use_top_n = st.number_input(
            "证据图全局用途 Top N",
            min_value=1,
            value=30,
            step=1,
        )
    with toxpi_weights:
        peak_area_weight = st.number_input("Peak Area 权重 (%)", min_value=0.0, value=40.0)
        pbm_weight = st.number_input("PBM 权重 (%)", min_value=0.0, value=40.0)
        df_weight = st.number_input("DF 权重 (%)", min_value=0.0, value=20.0)
    robustness_enabled = st.checkbox("启用 ToxPi 排名稳健性分析", value=True)
    robust_a, robust_b, robust_c = st.columns(3)
    with robust_a:
        perturbation_percent = st.number_input(
            "权重扰动 (%)", min_value=0.0, max_value=100.0, value=20.0
        )
    with robust_b:
        robustness_iterations = st.number_input("稳健性迭代次数", min_value=1, value=1000, step=1)
    with robust_c:
        robustness_seed = st.number_input("稳健性随机种子", value=123, step=1)

result_settings = {
    "mapping": {
        "compound_col": mapping.compound_col,
        "formula_col": mapping.formula_col,
        "peak_area_col": mapping.peak_area_col,
        "group_area_cols": list(mapping.group_area_cols),
        "mol_column": mapping.mol_column,
        "smiles_col": mapping.smiles_col,
        "cas_col": mapping.cas_col,
    },
    "primary_mappings": [
        {
            "sample_id": sample_id,
            **asdict(sample_mapping),
        }
        for sample_id, sample_mapping in sample_mappings.items()
    ],
    "epi_supplements": [
        _epi_supplement_settings_payload(supplement_mapping)
        for supplement_mapping in supplement_mappings
    ],
    "modules": {
        "run_r_replicate_df": bool(run_r_replicate_df),
        "run_identifier": bool(run_identifier),
        "run_epi": bool(run_epi),
        "run_comptox": bool(run_comptox),
        "run_echa_use": bool(run_echa_use),
        "run_echa_ghs": bool(run_echa_ghs),
        "run_source_origin": bool(run_source_origin),
        "run_pov_toxpi": bool(run_pov_toxpi),
    },
    "query": {
        "detection_threshold": float(detection_threshold),
        "cache_enabled": bool(cache_enabled),
        "identifier_max_workers": int(identifier_max_workers),
        "epi_max_workers": int(epi_max_workers),
        "comptox_max_workers": int(comptox_max_workers),
        "echa_max_workers": int(echa_max_workers),
        "echa_ghs_max_workers": int(echa_ghs_max_workers),
        "source_origin_max_workers": int(source_origin_max_workers),
    },
    "axis_bounds": {
        "dbe_x_min": float(dbe_x_min),
        "dbe_x_max": float(dbe_x_max),
        "dbe_y_min": float(dbe_y_min),
        "dbe_y_max": float(dbe_y_max),
        "vk_x_min": float(vk_x_min),
        "vk_x_max": float(vk_x_max),
        "vk_y_min": float(vk_y_min),
        "vk_y_max": float(vk_y_max),
    },
    "toxpi": {
        "candidate_top_n": int(candidate_top_n),
        "display_top_n": int(display_top_n),
        "evidence_per_compound_top_n": int(evidence_per_compound_top_n),
        "evidence_global_use_top_n": int(evidence_global_use_top_n),
        "peak_area_weight": float(peak_area_weight),
        "pbm_weight": float(pbm_weight),
        "df_weight": float(df_weight),
        "robustness_enabled": bool(robustness_enabled),
        "perturbation_percent": float(perturbation_percent),
        "robustness_iterations": int(robustness_iterations),
        "robustness_seed": int(robustness_seed),
    },
}
current_settings_signature = settings_signature(result_settings)
recovered_settings_mismatch = invalidate_recovered_results_on_settings_mismatch(
    st.session_state,
    current_settings_signature,
    RESULT_CACHE_KEYS,
    CHECKPOINT_STATE_KEYS,
)
if recovered_settings_mismatch:
    st.query_params.pop("run", None)
    st.info(
        "恢复结果的运行设置与当前页面设置不同，已从当前会话移除。"
        "原检查点仍保留 24 小时，可通过原恢复链接重新查看。"
    )
settings_changed = invalidate_results_on_settings_change(
    st.session_state,
    SETTINGS_SIGNATURE_KEY,
    result_settings,
    RESULT_CACHE_KEYS,
)
if settings_changed:
    clear_uploads(st.session_state, CHECKPOINT_STATE_KEYS)
    st.query_params.pop("run", None)

run_token = (
    st.session_state.get("auto_query_run_token")
    or st.query_params.get("run")
)
module_workbooks = OrderedDict(
    st.session_state.get("auto_query_module_workbooks") or OrderedDict()
)
latest_checkpoint = [None]
live_render_generation = [0]
partial_container = st.empty()


def handle_checkpoint(checkpoint, *, strict_module_export=False):
    latest_checkpoint[0] = checkpoint
    st.session_state["auto_query_partial_result"] = checkpoint.result
    st.session_state["auto_query_workflow_result"] = checkpoint.result
    if checkpoint.current_step:
        export = AUTO_WORKFLOW_CHECKPOINT_EXPORTS.get(
            checkpoint.current_step
        )
        if export is not None:
            module_workbooks.pop(export[0], None)
        try:
            module = build_auto_workflow_module_workbook(
                checkpoint.result,
                checkpoint.current_step,
            )
        except Exception as exc:
            st.session_state["auto_query_checkpoint_warning"] = (
                f"模块导出失败：{exc}"
            )
            st.session_state["auto_query_module_workbooks"] = OrderedDict(
                module_workbooks
            )
            if strict_module_export:
                raise
        else:
            if module is not None:
                module_workbooks[module.slug] = module
    st.session_state["auto_query_module_workbooks"] = OrderedDict(
        module_workbooks
    )
    if run_token:
        try:
            save_checkpoint(
                run_token,
                checkpoint,
                active_uploads[0]["name"],
                module_workbooks,
            )
        except Exception as exc:
            st.session_state["auto_query_checkpoint_warning"] = (
                "临时恢复保存失败，本次结果仅保留在当前页面会话："
                f"{exc}"
            )
    live_render_generation[0] += 1
    render_scope = f"auto_query_live_{live_render_generation[0]}"
    partial_container.empty()
    with partial_container.container():
        _render_module_downloads(
            checkpoint.result,
            module_workbooks,
            checkpoint.result.charts,
            key_prefix=render_scope,
        )


start_run = st.button("开始一键运行", type="primary")

if start_run:
    try:
        axis_ranges = ScreeningAxisRanges(
            dbe_x_min=float(dbe_x_min),
            dbe_x_max=float(dbe_x_max),
            dbe_y_min=float(dbe_y_min),
            dbe_y_max=float(dbe_y_max),
            vk_x_min=float(vk_x_min),
            vk_x_max=float(vk_x_max),
            vk_y_min=float(vk_y_min),
            vk_y_max=float(vk_y_max),
        )
    except ValueError as exc:
        st.error(f"坐标范围设置无效：{exc}")
        st.stop()
    try:
        toxpi_config = PBMToxPiConfig(
            candidate_top_n=int(candidate_top_n),
            display_top_n=int(display_top_n),
            evidence_per_compound_top_n=int(evidence_per_compound_top_n),
            evidence_global_use_top_n=int(evidence_global_use_top_n),
            weights={
                "peak_area": float(peak_area_weight) / 100.0,
                "pbm": float(pbm_weight) / 100.0,
                "df": float(df_weight) / 100.0,
            },
            robustness_enabled=bool(robustness_enabled),
            perturbation_fraction=float(perturbation_percent) / 100.0,
            n_iter=int(robustness_iterations),
            seed=int(robustness_seed),
        )
    except ValueError as exc:
        st.error(f"ToxPi 设置无效：{exc}")
        st.stop()

    try:
        multi_file_result = prepare_multi_file_screening(
            samples,
            sample_mappings,
            float(detection_threshold),
            axis_ranges,
        )
    except Exception as exc:
        st.error(f"多文件筛查准备失败：{exc}")
        st.stop()
    prepared_auto_input = auto_input_from_multi_file_result(
        multi_file_result
    )
    mapping = prepared_auto_input.mapping
    prepared_input_df = prepared_auto_input.prepared_input

    selected_steps = build_selected_steps(
        run_r_replicate_df=run_r_replicate_df,
        run_identifier=run_identifier,
        run_epi=run_epi,
        run_comptox=run_comptox,
        run_echa_use=run_echa_use,
        run_echa_ghs=run_echa_ghs,
        run_source_origin=run_source_origin,
        run_pov_lrtp_toxpi=run_pov_toxpi,
    )
    clear_uploads(
        st.session_state,
        (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
    )
    st.query_params.pop("run", None)
    try:
        cleanup_expired_checkpoints()
    except (CheckpointStorageError, OSError) as exc:
        st.session_state["auto_query_checkpoint_warning"] = str(exc)
    run_token = generate_run_token()
    run_id = generate_run_token()
    st.query_params["run"] = run_token
    st.session_state["auto_query_run_token"] = run_token
    module_workbooks = OrderedDict()
    latest_checkpoint = [None]
    live_render_generation = [0]
    checkpoint_context = AutoWorkflowCheckpointContext(
        run_id=run_id,
        input_signature=workflow_input_signature,
        settings_signature=settings_signature(result_settings),
        selected_steps=tuple(selected_steps),
    )

    initial_result = AutoWorkflowResult(
        mapping=prepared_auto_input.mapping,
        representative_table=prepared_auto_input.representative_table,
        tables=OrderedDict(prepared_auto_input.local_tables),
        step_status=pd.DataFrame(
            columns=["step", "status", "rows", "message"]
        ),
        warnings=pd.DataFrame(
            {
                "stage": ["本地筛查"] * len(prepared_auto_input.local_warnings),
                "message": prepared_auto_input.local_warnings,
            }
        ),
        charts=OrderedDict(prepared_auto_input.local_charts),
    )
    handle_checkpoint(
        AutoWorkflowCheckpoint(
            run_id=run_id,
            input_signature=checkpoint_context.input_signature,
            settings_signature=checkpoint_context.settings_signature,
            selected_steps=checkpoint_context.selected_steps,
            finished_steps=(),
            current_step=None,
            status="running",
            result=initial_result,
            error_message="",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    progress_state = create_progress_state(selected_steps)
    overall_label = st.empty()
    overall_progress_bar = st.progress(0)
    module_label = st.empty()
    module_progress_bar = st.progress(0)
    status_box = st.empty()

    def render_progress():
        snapshot = progress_snapshot(progress_state)
        overall_label.caption(
            f"总体进度：已完成 {snapshot['overall_finished']}/{snapshot['overall_total']} 个环节"
        )
        overall_progress_bar.progress(snapshot["overall_fraction"])
        step = snapshot["current_step"] or "等待第一个环节开始"
        if snapshot["module_total"]:
            module_label.caption(
                f"当前模块进度：{step}（已完成 {snapshot['module_done']}/{snapshot['module_total']} 条）"
            )
        else:
            module_label.caption(f"当前模块进度：{step}")
        module_progress_bar.progress(snapshot["module_fraction"])
        message = format_activity_message(snapshot)
        terminal = snapshot["last_terminal_event"] or {}
        if terminal.get("event") == "failed":
            status_box.warning(message)
        else:
            status_box.info(message)

    def update_activity(event):
        record_activity_event(progress_state, event)
        render_progress()

    def update_progress(step, done, total, label):
        if progress_state["current_step"] == step and progress_state["module_done"] >= done:
            return
        update_activity(
            {
                "event": "completed",
                "step": step,
                "index": max(0, done - 1),
                "total": total,
                "done": done,
                "label": label,
            }
        )

    config = AutoWorkflowConfig(
        mapping=mapping,
        run_r_replicate_df=run_r_replicate_df,
        run_identifier=run_identifier,
        run_epi=run_epi,
        run_comptox=run_comptox,
        run_echa_use=run_echa_use,
        run_echa_ghs=run_echa_ghs,
        run_source_origin=run_source_origin,
        run_pov_lrtp_toxpi=run_pov_toxpi,
        detection_threshold=float(detection_threshold),
        axis_ranges=axis_ranges,
        toxpi_config=toxpi_config,
        cache_enabled=bool(cache_enabled),
        identifier_max_workers=int(identifier_max_workers),
        epi_max_workers=int(epi_max_workers),
        comptox_max_workers=int(comptox_max_workers),
        echa_max_workers=int(echa_max_workers),
        echa_ghs_max_workers=int(echa_ghs_max_workers),
        source_origin_max_workers=int(source_origin_max_workers),
    )
    render_progress()
    try:
        with st.spinner("正在按顺序运行已选项目..."):
            result = run_auto_query_workflow(
                prepared_auto_input.prepared_input,
                config=config,
                prepared_input=prepared_auto_input,
                epi_uploaded_results=parsed_supplements,
                epi_pool_results=session_pool_results,
                progress_callback=update_progress,
                activity_callback=update_activity,
                checkpoint_context=checkpoint_context,
                checkpoint_callback=handle_checkpoint,
            )
            status_box.info(
                "查询环节已完成，正在汇总结果与生成图表..."
            )
            charts = build_auto_workflow_charts(result)
            result.charts = charts
            st.session_state["auto_query_workflow_result"] = result
            st.session_state["auto_query_workflow_charts"] = charts
            if latest_checkpoint[0] is not None:
                handle_checkpoint(
                    replace(
                        latest_checkpoint[0],
                        result=result,
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
            package = build_auto_workflow_zip(result, charts)
    except Exception as exc:
        status_box.error(f"运行未完整结束：{exc}")
        if latest_checkpoint[0] is not None:
            failed_checkpoint = replace(
                latest_checkpoint[0],
                status="failed",
                result=st.session_state["auto_query_workflow_result"],
                error_message=str(exc),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            handle_checkpoint(failed_checkpoint)
        st.session_state["auto_query_checkpoint_warning"] = str(exc)
    else:
        st.session_state["auto_query_workflow_result"] = result
        st.session_state["auto_query_workflow_charts"] = charts
        st.session_state["auto_query_workflow_zip"] = package
        if latest_checkpoint[0] is not None:
            completed_checkpoint = replace(
                latest_checkpoint[0],
                status="completed",
                result=result,
                error_message="",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            handle_checkpoint(completed_checkpoint)
        overall_progress_bar.progress(1.0)
        module_progress_bar.progress(1.0)
        status_box.success("一键批量查询完成。")
    partial_container.empty()

result = st.session_state.get("auto_query_workflow_result")
if result is not None:
    charts = st.session_state.get("auto_query_workflow_charts") or {}
    package = st.session_state.get("auto_query_workflow_zip")
    module_workbooks = st.session_state.get("auto_query_module_workbooks") or OrderedDict()
    retry_input = result.tables.get("EPI_Retry_Input", pd.DataFrame())
    retry_requested = False
    if isinstance(retry_input, pd.DataFrame) and not retry_input.empty:
        retry_query_input = queryable_epi_retry_input(retry_input)
        if retry_query_input.empty:
            st.info("未完成的 EPI 行缺少可查询的 SMILES，请先补充结构信息。")
        else:
            retry_requested = st.button(
                "仅重试未完成的 EPI 行",
                key="auto_query_retry_epi_failures",
            )
    if retry_requested:
        if not run_token:
            run_token = generate_run_token()
            st.session_state["auto_query_run_token"] = run_token
            st.query_params["run"] = run_token
        selected_steps = build_selected_steps(
            run_r_replicate_df=run_r_replicate_df,
            run_identifier=run_identifier,
            run_epi=True,
            run_comptox=run_comptox,
            run_echa_use=run_echa_use,
            run_echa_ghs=run_echa_ghs,
            run_source_origin=run_source_origin,
            run_pov_lrtp_toxpi=run_pov_toxpi,
        )
        manifest = st.session_state.get("auto_query_checkpoint_manifest") or {}
        retry_run_id = manifest.get("run_id") or generate_run_token()
        retry_input_signature = (
            manifest.get("input_signature") or workflow_input_signature
        )
        retry_settings_signature = (
            manifest.get("settings_signature")
            or settings_signature(result_settings)
        )
        finished_steps = list(
            manifest.get("finished_steps") or selected_steps
        )
        for step in (
            "EPI Suite 环境归趋",
            *(
                ("Pov-LRTP / PBM / ToxPi",)
                if run_pov_toxpi
                else ()
            ),
        ):
            if step not in finished_steps:
                finished_steps.append(step)
        retry_status = st.empty()
        retry_progress_bar = st.progress(0)

        def update_retry_progress(step, done, total, label):
            fraction = min(1.0, done / max(1, total))
            retry_progress_bar.progress(fraction)
            retry_status.info(
                f"{step}：{done}/{total}，当前 {label or '处理中'}"
            )

        def update_retry_activity(event):
            label = event.get("label") or ""
            if label:
                retry_status.info(f"EPI Suite 环境归趋：{label}")

        checkpoint_kwargs = {
            "run_id": retry_run_id,
            "input_signature": retry_input_signature,
            "settings_signature": retry_settings_signature,
            "selected_steps": tuple(selected_steps),
            "finished_steps": tuple(finished_steps),
        }
        try:
            axis_ranges = ScreeningAxisRanges(
                dbe_x_min=float(dbe_x_min),
                dbe_x_max=float(dbe_x_max),
                dbe_y_min=float(dbe_y_min),
                dbe_y_max=float(dbe_y_max),
                vk_x_min=float(vk_x_min),
                vk_x_max=float(vk_x_max),
                vk_y_min=float(vk_y_min),
                vk_y_max=float(vk_y_max),
            )
            toxpi_config = PBMToxPiConfig(
                candidate_top_n=int(candidate_top_n),
                display_top_n=int(display_top_n),
                evidence_per_compound_top_n=int(
                    evidence_per_compound_top_n
                ),
                evidence_global_use_top_n=int(evidence_global_use_top_n),
                weights={
                    "peak_area": float(peak_area_weight) / 100.0,
                    "pbm": float(pbm_weight) / 100.0,
                    "df": float(df_weight) / 100.0,
                },
                robustness_enabled=bool(robustness_enabled),
                perturbation_fraction=(
                    float(perturbation_percent) / 100.0
                ),
                n_iter=int(robustness_iterations),
                seed=int(robustness_seed),
            )
            retry_config = AutoWorkflowConfig(
                mapping=mapping,
                run_r_replicate_df=run_r_replicate_df,
                run_identifier=run_identifier,
                run_epi=True,
                run_comptox=run_comptox,
                run_echa_use=run_echa_use,
                run_echa_ghs=run_echa_ghs,
                run_source_origin=run_source_origin,
                run_pov_lrtp_toxpi=run_pov_toxpi,
                detection_threshold=float(detection_threshold),
                axis_ranges=axis_ranges,
                toxpi_config=toxpi_config,
                cache_enabled=bool(cache_enabled),
                identifier_max_workers=int(identifier_max_workers),
                epi_max_workers=int(epi_max_workers),
                comptox_max_workers=int(comptox_max_workers),
                echa_max_workers=int(echa_max_workers),
                echa_ghs_max_workers=int(echa_ghs_max_workers),
                source_origin_max_workers=int(source_origin_max_workers),
            )
            with st.spinner("正在重试未完成的 EPI 行..."):
                retried_result = retry_auto_workflow_epi_failures(
                    result,
                    retry_config,
                    progress_callback=update_retry_progress,
                    activity_callback=update_retry_activity,
                )
            charts = OrderedDict(retried_result.charts)
            retried_result.charts = charts
            st.session_state["auto_query_workflow_result"] = retried_result
            st.session_state["auto_query_workflow_charts"] = charts
            st.session_state.pop("auto_query_workflow_zip", None)
            package = None
            for current_step in (
                "EPI Suite 环境归趋",
                *(
                    ("Pov-LRTP / PBM / ToxPi",)
                    if retry_config.run_pov_lrtp_toxpi
                    else ()
                ),
            ):
                handle_checkpoint(
                    AutoWorkflowCheckpoint(
                        **checkpoint_kwargs,
                        current_step=current_step,
                        status="running",
                        result=retried_result,
                        error_message="",
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    ),
                    strict_module_export=True,
                )
            module_workbooks = OrderedDict(
                st.session_state.get("auto_query_module_workbooks")
                or OrderedDict()
            )
            build_auto_workflow_partial_zip(
                retried_result,
                module_workbooks,
                charts=charts,
            )
            package = build_auto_workflow_zip(retried_result, charts)
            st.session_state["auto_query_workflow_zip"] = package
            completed_checkpoint = AutoWorkflowCheckpoint(
                **checkpoint_kwargs,
                current_step=None,
                status="completed",
                result=retried_result,
                error_message="",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            handle_checkpoint(completed_checkpoint)
        except Exception as exc:
            failed_result = (
                exc.result
                if isinstance(exc, AutoWorkflowEpiRetryError)
                else st.session_state.get(
                    "auto_query_workflow_result",
                    result,
                )
            )
            failed_charts = OrderedDict(failed_result.charts)
            failed_result.charts = failed_charts
            st.session_state["auto_query_workflow_result"] = failed_result
            st.session_state["auto_query_workflow_charts"] = failed_charts
            st.session_state.pop("auto_query_workflow_zip", None)
            package = None
            failed_checkpoint = AutoWorkflowCheckpoint(
                **checkpoint_kwargs,
                current_step="EPI Suite 环境归趋",
                status="failed",
                result=failed_result,
                error_message=str(exc),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            handle_checkpoint(failed_checkpoint)
            st.session_state["auto_query_checkpoint_warning"] = str(exc)
            retry_status.error(f"EPI 重试未完成：{exc}")
        else:
            result = retried_result
            retry_progress_bar.progress(1.0)
            retry_status.success("EPI 重试完成。")
    checkpoint_warning = st.session_state.get("auto_query_checkpoint_warning")
    if checkpoint_warning:
        st.warning(checkpoint_warning)
    _render_saved_results(
        result,
        charts,
        full_package=package,
        module_workbooks=module_workbooks,
        partial=package is None,
    )
