import io

import pandas as pd
import streamlit as st

from src.auto_query_workflow import (
    AutoWorkflowConfig,
    AutoWorkflowMapping,
    PUBLIC_TABLE_NAMES,
    build_auto_workflow_charts,
    build_auto_workflow_zip,
    detect_default_mapping,
    read_input_workbook,
    run_auto_query_workflow,
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
from src.upload_state import cached_uploads, clear_uploads, store_uploads, upload_bytes


INPUT_CACHE_KEYS = (
    "auto_query_input_files",
    "auto_query_input_signature",
)
RESULT_CACHE_KEYS = (
    "auto_query_workflow_result",
    "auto_query_workflow_charts",
    "auto_query_workflow_zip",
)


def clear_auto_query_state():
    clear_uploads(st.session_state, (*INPUT_CACHE_KEYS, *RESULT_CACHE_KEYS))
    st.session_state.pop("auto_query_upload", None)


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
        clear_uploads(st.session_state, RESULT_CACHE_KEYS)
else:
    active_uploads = cached_uploads(st.session_state, "auto_query_input_files")

if not active_uploads:
    st.info("请先上传 Excel 文件。")
    st.stop()

st.success("已加载输入文件。")
if st.button("清空当前数据", key="auto_clear_cached_input"):
    clear_auto_query_state()
    st.rerun()

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
    with st.spinner("正在按顺序运行已选项目..."):
        result = run_auto_query_workflow(
            prepared_input_df,
            config=config,
            progress_callback=update_progress,
            activity_callback=update_activity,
        )
        status_box.info("查询环节已完成，正在汇总结果与生成图表...")
        charts = build_auto_workflow_charts(result)
        package = build_auto_workflow_zip(result, charts)
    st.session_state["auto_query_workflow_result"] = result
    st.session_state["auto_query_workflow_charts"] = charts
    st.session_state["auto_query_workflow_zip"] = package
    overall_progress_bar.progress(1.0)
    module_progress_bar.progress(1.0)
    status_box.success("一键批量查询完成。")

result = st.session_state.get("auto_query_workflow_result")
if result is not None:
    st.subheader("运行日志")
    _show_dataframe(result.step_status)

    if not result.warnings.empty:
        with st.expander("Warnings", expanded=False):
            _show_dataframe(result.warnings)

    table_names = [name for name in result.tables if name in PUBLIC_TABLE_NAMES]
    if table_names:
        selected_table = st.selectbox("查看结果表", table_names)
        _show_dataframe(result.tables[selected_table])
    structure_preparation = result.tables.get("Structure_Preparation")
    if isinstance(structure_preparation, pd.DataFrame):
        _render_structure_preparation_summary(structure_preparation)

    charts = st.session_state.get("auto_query_workflow_charts") or {}
    _render_result_dashboard(result, charts)

    package = st.session_state.get("auto_query_workflow_zip")
    if package is not None:
        st.download_button(
            "下载一键批量查询结果 ZIP",
            data=package.getvalue(),
            file_name="Auto_Query_Workflow_Results.zip",
            mime="application/zip",
        )
