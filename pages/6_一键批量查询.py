import io
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.auto_query_workflow import (
    AUTO_WORKFLOW_CHECKPOINT_EXPORTS,
    AutoWorkflowCheckpoint,
    AutoWorkflowCheckpointContext,
    AutoWorkflowConfig,
    AutoWorkflowMapping,
    AutoWorkflowResult,
    PUBLIC_TABLE_NAMES,
    build_auto_workflow_charts,
    build_auto_workflow_module_workbook,
    build_auto_workflow_partial_zip,
    build_auto_workflow_zip,
    build_representative_table,
    detect_default_mapping,
    read_input_workbook,
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
    upload_bytes,
)


INPUT_CACHE_KEYS = (
    "auto_query_input_files",
    "auto_query_input_signature",
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


def clear_auto_query_state():
    token = st.session_state.get("auto_query_run_token") or st.query_params.get("run")
    if token:
        try:
            delete_checkpoint(token)
        except (CheckpointStorageError, OSError):
            pass
    clear_uploads(
        st.session_state,
        (*INPUT_CACHE_KEYS, *RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
    )
    st.session_state.pop(SETTINGS_SIGNATURE_KEY, None)
    st.session_state.pop("auto_query_upload", None)
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
            for chart_key in group["chart_keys"]:
                chart = charts[chart_key]
                st.image(chart.png, caption=chart.title)


def _render_module_downloads(
    result,
    module_workbooks,
    *,
    key_prefix="auto_query_module",
):
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
        st.download_button(
            f"下载 {module.step}",
            data=module.data,
            file_name=module.file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    _render_module_downloads(result, module_workbooks)
    if partial:
        try:
            partial_zip = build_auto_workflow_partial_zip(result, module_workbooks)
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


uploaded_file = st.file_uploader(
    "上传统一格式 Excel 文件",
    type=["xlsx", "xls"],
    accept_multiple_files=False,
    help="当前按 Sheet1 / 第一个工作表读取，默认识别 Name、NIST Lib Hit Formula、Avg TIC 和 Group Area 列。",
    key="auto_query_upload",
)

if uploaded_file is not None:
    active_uploads, input_changed = store_uploads(
        st.session_state,
        "auto_query_input_files",
        "auto_query_input_signature",
        [uploaded_file],
    )
    if input_changed:
        clear_uploads(
            st.session_state,
            (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
        )
        st.query_params.pop("run", None)
else:
    active_uploads = cached_uploads(st.session_state, "auto_query_input_files")

checkpoint_manifest = st.session_state.get("auto_query_checkpoint_manifest") or {}
current_input_signature = st.session_state.get("auto_query_input_signature")
checkpoint_input_signature = checkpoint_manifest.get("input_signature")
if (
    current_input_signature
    and checkpoint_input_signature
    and current_input_signature != checkpoint_input_signature
):
    clear_uploads(
        st.session_state,
        (*RESULT_CACHE_KEYS, *CHECKPOINT_STATE_KEYS),
    )
    st.query_params.pop("run", None)

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

st.success("已加载输入文件。")

try:
    input_df = read_input_workbook(io.BytesIO(upload_bytes(active_uploads[0])))
except Exception as exc:
    st.error(f"Excel 读取失败：{exc}")
    st.stop()

default_mapping = detect_default_mapping(input_df.columns)
columns = list(input_df.columns)

st.subheader("输入文件检查")
col_rows, col_cols, col_groups = st.columns(3)
with col_rows:
    st.metric("数据行数", len(input_df))
with col_cols:
    st.metric("列数", len(columns))
with col_groups:
    st.metric("Group Area 列", len(default_mapping.group_area_cols))

with st.expander("列识别与校正", expanded=False):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        compound_col = st.selectbox(
            "化合物名称列",
            columns,
            index=_column_index(columns, default_mapping.compound_col),
        )
    with col_b:
        formula_col = st.selectbox(
            "分子式列",
            columns,
            index=_column_index(columns, default_mapping.formula_col),
        )
    with col_c:
        peak_area_col = st.selectbox(
            "默认峰面积列",
            columns,
            index=_column_index(columns, default_mapping.peak_area_col),
        )

    group_area_cols = st.multiselect(
        "参与化学类型图、DBE图、VK图、DF 和 ToxPi 的 Group Area 列",
        columns,
        default=[column for column in default_mapping.group_area_cols if column in columns],
    )
    optional_columns = ["", *columns]
    opt_a, opt_b, opt_c = st.columns(3)
    with opt_a:
        mol_column = st.selectbox(
            "可选：MOL 文本列",
            optional_columns,
            index=_optional_column_index(columns, default_mapping.mol_column),
        ) or None
    with opt_b:
        smiles_col = st.selectbox(
            "可选：已有 SMILES 列",
            optional_columns,
            index=_optional_column_index(columns, default_mapping.smiles_col),
        ) or None
    with opt_c:
        cas_col = st.selectbox(
            "可选：已有 CAS 列",
            optional_columns,
            index=_optional_column_index(columns, default_mapping.cas_col),
        ) or None

mapping = AutoWorkflowMapping(
    compound_col=compound_col,
    formula_col=formula_col,
    peak_area_col=peak_area_col,
    group_area_cols=list(group_area_cols),
    mol_column=mol_column,
    smiles_col=smiles_col,
    cas_col=cas_col,
)

prepared_input_df = prepare_structure_dataframe(
    input_df,
    mol_column=mapping.mol_column,
    smiles_column=mapping.smiles_col,
)
_render_structure_preparation_summary(prepared_input_df)

with st.expander("查看前 20 行", expanded=False):
    _show_dataframe(input_df.head(20))

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
        "compound_col": compound_col,
        "formula_col": formula_col,
        "peak_area_col": peak_area_col,
        "group_area_cols": list(group_area_cols),
        "mol_column": mol_column,
        "smiles_col": smiles_col,
        "cas_col": cas_col,
    },
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
    partial_container = st.empty()
    checkpoint_context = AutoWorkflowCheckpointContext(
        run_id=run_id,
        input_signature=st.session_state["auto_query_input_signature"],
        settings_signature=settings_signature(result_settings),
        selected_steps=tuple(selected_steps),
    )

    def handle_checkpoint(checkpoint):
        latest_checkpoint[0] = checkpoint
        st.session_state["auto_query_partial_result"] = checkpoint.result
        st.session_state["auto_query_workflow_result"] = checkpoint.result
        if checkpoint.current_step:
            try:
                module = build_auto_workflow_module_workbook(
                    checkpoint.result,
                    checkpoint.current_step,
                )
            except Exception as exc:
                st.session_state["auto_query_checkpoint_warning"] = (
                    f"模块导出失败：{exc}"
                )
            else:
                if module is not None:
                    module_workbooks[module.slug] = module
        st.session_state["auto_query_module_workbooks"] = OrderedDict(
            module_workbooks
        )
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
                key_prefix=render_scope,
            )

    initial_result = AutoWorkflowResult(
        mapping=mapping,
        representative_table=build_representative_table(
            prepared_input_df,
            mapping,
        ),
        tables=OrderedDict(
            [("Structure_Preparation", prepared_input_df.copy())]
        ),
        step_status=pd.DataFrame(
            columns=["step", "status", "rows", "message"]
        ),
        warnings=pd.DataFrame(columns=["stage", "message"]),
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
                prepared_input_df,
                config=config,
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
            package = build_auto_workflow_zip(result, charts)
    except Exception as exc:
        status_box.error(f"运行未完整结束：{exc}")
        if latest_checkpoint[0] is not None:
            failed_checkpoint = replace(
                latest_checkpoint[0],
                status="failed",
                result=latest_checkpoint[0].result,
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
    checkpoint_warning = st.session_state.get("auto_query_checkpoint_warning")
    if checkpoint_warning:
        st.warning(checkpoint_warning)
    _render_saved_results(
        result,
        charts,
        full_package=package,
        module_workbooks=module_workbooks,
        partial=package is None and bool(module_workbooks),
    )
