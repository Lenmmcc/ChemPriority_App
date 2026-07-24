import hashlib
import importlib
import io
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import pandas as pd
import streamlit as st


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cp_screening_workflow import (  # noqa: E402
    PBMToxPiConfig,
    build_pbm_toxpi_input,
    build_screening_workbook,
    calculate_pbm_toxpi,
    figure_to_png_pdf_bytes,
    generate_pbm_toxpi_bar_plot,
    generate_pbm_toxpi_robustness_plot,
)
from src.episuite_io import DEFAULT_EPI_WEB_API, run_epi_web_batch  # noqa: E402
from src.identifier_resolver import DEFAULT_PUBCHEM_BASE, run_identifier_completion_batch  # noqa: E402
from src.multi_file_screening import (  # noqa: E402
    PrimaryWorkbook,
    SampleColumnMapping,
    build_upload_structure_preparation_preview,
    default_sample_mapping,
    prepare_multi_file_screening,
    read_primary_workbooks,
)
from src.pov_lrtp_replica import run_pov_lrtp_batch  # noqa: E402
from src.query_cache import clear_query_cache, current_cache_path  # noqa: E402
from src.r_screening_replica.schema import ScreeningAxisRanges  # noqa: E402
from src.r_screening_replica.downstream import (  # noqa: E402
    build_epi_input_from_identifiers,
    build_identifier_input,
    build_pov_lrtp_input,
)
from src.upload_state import cached_uploads, clear_uploads, store_uploads  # noqa: E402
import src.toxpi_calc as toxpi_calc  # noqa: E402

if not hasattr(toxpi_calc, "generate_r_style_toxpi_plot"):
    toxpi_calc = importlib.reload(toxpi_calc)
generate_r_style_toxpi_plot = toxpi_calc.generate_r_style_toxpi_plot


DOWNSTREAM_PLOT_STATE_KEYS = (
    "cp_screening_bar_png",
    "cp_screening_bar_pdf",
    "cp_screening_radial_png",
    "cp_screening_radial_pdf",
    "cp_screening_radial_plot_version",
    "cp_screening_robustness_png",
    "cp_screening_robustness_pdf",
)

STATE_KEYS = (
    "cp_screening_front",
    "cp_screening_downstream",
    "cp_screening_workbook",
    *DOWNSTREAM_PLOT_STATE_KEYS,
    "cp_screening_settings_signature",
)

INPUT_CACHE_KEYS = (
    "cp_screening_input_files",
    "cp_screening_input_signature",
)

PER_SAMPLE_FRONT_HALF_FIGURES = [
    ("category_percent_donut_with_total", "Compound category distribution"),
    ("compound_bubble_plot", "DBE bubble plot"),
    ("VanKrevelen", "Van Krevelen plot"),
]

SUMMARY_FRONT_HALF_FIGURES = [
    ("boxplot_log_transformed", "Log peak area boxplot"),
]

TOXPI_RADIAL_PLOT_VERSION = "r_style_single_canvas_v2"

def clear_workflow_state():
    for key in STATE_KEYS:
        st.session_state.pop(key, None)


def clear_cached_input():
    clear_uploads(st.session_state, INPUT_CACHE_KEYS)
    st.session_state.pop("cp_screening_upload", None)
    clear_workflow_state()


def show_dataframe(df):
    try:
        st.dataframe(df, width="stretch")
    except TypeError:
        st.dataframe(df, use_container_width=True)


def read_file_bytes(path):
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def render_front_half_figures(front_state):
    screening_results = front_state.get("screening_results", [])
    summary_figure_paths = front_state.get("summary_figure_paths", {})
    has_summary_figures = any(
        summary_figure_paths.get(figure_key) for figure_key, _label in SUMMARY_FRONT_HALF_FIGURES
    )
    if not screening_results and not has_summary_figures:
        return

    st.subheader("R Front-half Figures")
    if has_summary_figures:
        with st.expander("All samples summary figures", expanded=True):
            render_figure_paths("all_samples", summary_figure_paths, SUMMARY_FRONT_HALF_FIGURES)
    if screening_results:
        render_figure_group(screening_results, PER_SAMPLE_FRONT_HALF_FIGURES)


def render_figure_group(screening_results, figure_specs):
    for sample_id, result in screening_results:
        with st.expander(f"{sample_id} figures", expanded=len(screening_results) == 1):
            render_figure_paths(sample_id, result.figure_paths, figure_specs)


def render_figure_paths(owner_id, figure_paths, figure_specs):
    columns = st.columns(2)
    for index, (figure_key, label) in enumerate(figure_specs):
        with columns[index % 2]:
            st.markdown(f"**{label}**")
            paths = figure_paths.get(figure_key, {})
            png_bytes = read_file_bytes(paths.get("png"))
            if png_bytes:
                st.image(png_bytes)
            else:
                st.info("Figure was not generated for this sample.")

            safe_key = hashlib.sha1(f"{owner_id}:{figure_key}".encode("utf-8", errors="ignore")).hexdigest()
            download_cols = st.columns(2)
            with download_cols[0]:
                if png_bytes:
                    st.download_button(
                        "PNG",
                        data=png_bytes,
                        file_name=f"{owner_id}_{figure_key}.png",
                        mime="image/png",
                        key=f"front_png_{safe_key}",
                    )
            with download_cols[1]:
                pdf_bytes = read_file_bytes(paths.get("pdf"))
                if pdf_bytes:
                    st.download_button(
                        "PDF",
                        data=pdf_bytes,
                        file_name=f"{owner_id}_{figure_key}.pdf",
                        mime="application/pdf",
                        key=f"front_pdf_{safe_key}",
                    )


def column_index(columns, value):
    return columns.index(value) if value in columns else 0


def optional_column_index(columns, value):
    options = ["", *columns]
    return options.index(value) if value in columns else 0


def widget_key(prefix, sample_name, index):
    digest = hashlib.sha1(f"{prefix}:{sample_name}:{index}".encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def render_sample_mapping_tabs(samples):
    sample_mappings = {}
    mapping_tabs = st.tabs([sample.sample_id for sample in samples])
    for index, (tab, sample) in enumerate(zip(mapping_tabs, samples)):
        with tab:
            columns = list(sample.data.columns)
            defaults = default_sample_mapping(sample)
            st.caption(f"{sample.file_name} | {len(sample.data)} rows | {len(columns)} columns")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                compound_col = st.selectbox(
                    "化合物名称列",
                    columns,
                    index=column_index(columns, defaults.compound_col),
                    key=widget_key("cp_compound_col", sample.sample_id, index),
                )
            with col_b:
                formula_col = st.selectbox(
                    "分子式列",
                    columns,
                    index=column_index(columns, defaults.formula_col),
                    key=widget_key("cp_formula_col", sample.sample_id, index),
                )
            with col_c:
                peak_area_col = st.selectbox(
                    "默认峰面积列",
                    columns,
                    index=column_index(columns, defaults.peak_area_col),
                    key=widget_key("cp_peak_area_col", sample.sample_id, index),
                )

            default_sample_cols = [
                column for column in defaults.group_area_cols if column in columns
            ]
            sample_cols = st.multiselect(
                "参与绘图、DF 和 PA/ToxPi 的 Group Area 列",
                columns,
                default=default_sample_cols,
                help="单个 Excel 内选择多个 Group Area 时，会先按文件内均值进入 DF 和 ToxPi；箱线图仍保留原始点位长表。",
                key=widget_key("cp_sample_cols", sample.sample_id, index),
            )
            if not sample_cols and peak_area_col:
                st.info("未选择 Group Area 列；本文件不会参与化学类型图、DBE图、VK图、DF 和 PA/ToxPi。")

            optional_columns = [""] + columns
            opt_a, opt_b, opt_c = st.columns(3)
            with opt_a:
                mol_column = st.selectbox(
                    "可选：MOL 文本列",
                    optional_columns,
                    index=optional_column_index(columns, defaults.mol_column),
                    key=widget_key("cp_mol_col", sample.sample_id, index),
                ) or None
            with opt_b:
                smiles_col = st.selectbox(
                    "可选：已有 SMILES 列",
                    optional_columns,
                    index=optional_column_index(columns, defaults.smiles_col),
                    key=widget_key("cp_smiles_col", sample.sample_id, index),
                ) or None
            with opt_c:
                cas_col = st.selectbox(
                    "可选：已有 CAS 列",
                    optional_columns,
                    index=optional_column_index(columns, defaults.cas_col),
                    key=widget_key("cp_cas_col", sample.sample_id, index),
                ) or None

            sample_mappings[sample.sample_id] = SampleColumnMapping(
                compound_col=compound_col,
                formula_col=formula_col,
                peak_area_col=peak_area_col,
                group_area_cols=tuple(sample_cols),
                mol_column=mol_column,
                smiles_col=smiles_col,
                cas_col=cas_col,
            )
    return sample_mappings


def clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def with_warning_stage(table, fallback_stage):
    warning_table = table.copy()
    if warning_table.empty:
        return warning_table

    if "stage" not in warning_table.columns:
        warning_table.insert(0, "stage", fallback_stage)
        return warning_table

    stage_values = warning_table["stage"].map(clean_text)
    warning_table["stage"] = stage_values.mask(stage_values.eq(""), fallback_stage)
    return warning_table[["stage", *[column for column in warning_table.columns if column != "stage"]]]


def workflow_tables(front_state, downstream_state=None):
    downstream_state = downstream_state or {}
    front_tables = front_state.get("tables", {})
    warnings = []
    if isinstance(front_state.get("warnings"), pd.DataFrame):
        warnings.append(front_state["warnings"])
    for key in ["identifier_warnings", "epi_errors"]:
        table = downstream_state.get(key)
        if isinstance(table, pd.DataFrame) and not table.empty:
            warnings.append(with_warning_stage(table, key))

    pov_results = downstream_state.get("pov_lrtp_results", pd.DataFrame())
    excluded = pd.DataFrame()
    if isinstance(pov_results, pd.DataFrame) and not pov_results.empty:
        failed_mask = pov_results.get("Status", pd.Series(index=pov_results.index, dtype=object)).ne("ok")
        incomplete_mask = pov_results.get("model_input_complete", pd.Series(False, index=pov_results.index)).eq(False)
        excluded = pov_results[failed_mask | incomplete_mask].copy()

    return {
        "Input_Check": front_tables.get("Input_Check", pd.DataFrame()),
        "Elemental_Ratios_DBE": front_tables.get(
            "Elemental_Ratios_DBE",
            pd.DataFrame(),
        ),
        "Category_Summary": front_tables.get("Category_Summary", pd.DataFrame()),
        "Sample_Peak_Area": front_state.get("sample_peak_area", pd.DataFrame()),
        "Group_Area_Raw_Long": front_state.get("group_area_raw_long", pd.DataFrame()),
        "Group_Area_Mean_By_Sample": front_state.get("group_area_mean_by_sample", pd.DataFrame()),
        "DF_Table": front_state.get("df_table", pd.DataFrame()),
        "Structure_Preparation": front_state.get("structure_preparation_audit", pd.DataFrame()),
        "Identifier_Completion": downstream_state.get("completed_identifiers", pd.DataFrame()),
        "EPI_Results": downstream_state.get("epi_results", pd.DataFrame()),
        "Pov_LRTP": downstream_state.get("pov_lrtp_results", pd.DataFrame()),
        "PBM_Scores": downstream_state.get("pbm_scores", pd.DataFrame()),
        "ToxPi_Input": downstream_state.get("toxpi_input", pd.DataFrame()),
        "ToxPi_Global_Screen": downstream_state.get("toxpi_global_screen", pd.DataFrame()),
        "ToxPi_Normalized": downstream_state.get("toxpi_normalized", pd.DataFrame()),
        "ToxPi_Results": downstream_state.get("toxpi_results", pd.DataFrame()),
        "ToxPi_Display": downstream_state.get("toxpi_display", pd.DataFrame()),
        "ToxPi_Excluded": downstream_state.get("toxpi_excluded", pd.DataFrame()),
        "ToxPi_Settings": downstream_state.get("toxpi_settings", pd.DataFrame()),
        "ToxPi_Robustness": downstream_state.get("toxpi_robustness", pd.DataFrame()),
        "ToxPi_Robust_Stats": downstream_state.get("toxpi_robust_stats", pd.DataFrame()),
        "Excluded_or_Failed": excluded,
        "Warnings": pd.concat(warnings, ignore_index=True) if warnings else pd.DataFrame(),
    }


def refresh_toxpi_radial_plot(downstream_state, force=False):
    if not downstream_state:
        return
    if not force and st.session_state.get("cp_screening_radial_plot_version") == TOXPI_RADIAL_PLOT_VERSION:
        return

    radial_plot_rows = downstream_state.get("toxpi_display")
    if not isinstance(radial_plot_rows, pd.DataFrame) or radial_plot_rows.empty:
        return

    radial_fig = generate_r_style_toxpi_plot(
        radial_plot_rows,
        custom_weights=downstream_state["normalized_weights"],
        toxic_cols=["peak_area", "pbm", "df"],
        label_wrap_width=20,
    )
    radial_png, radial_pdf = figure_to_png_pdf_bytes(radial_fig)
    st.session_state["cp_screening_radial_png"] = radial_png
    st.session_state["cp_screening_radial_pdf"] = radial_pdf
    st.session_state["cp_screening_radial_plot_version"] = TOXPI_RADIAL_PLOT_VERSION


st.set_page_config(
    page_title="综合筛查流程 - ChemPriority",
    page_icon="🧭",
    layout="wide",
)

st.title("综合筛查流程 / CP Screening Workflow")
st.caption("多 Excel 样品上传，串联化学类型图、DBE图、VK图、DF、EPI Suite、Pov-LRTP、PBM 和 PA/PBM/DF ToxPi。")
st.markdown("---")

uploaded_files = st.file_uploader(
    "上传一个或多个 CD 导出的 Excel 文件",
    type=["xlsx", "xls"],
    accept_multiple_files=True,
    help="DF 按上传的 Excel 文件数作为样品总数计算；每个文件名默认作为 sample_id。",
    key="cp_screening_upload",
)

if uploaded_files:
    active_uploads, input_changed = store_uploads(
        st.session_state,
        "cp_screening_input_files",
        "cp_screening_input_signature",
        uploaded_files,
    )
    if input_changed:
        clear_workflow_state()
else:
    active_uploads = cached_uploads(st.session_state, "cp_screening_input_files")

if not active_uploads:
    st.info("请上传至少 1 个 Excel 文件。若需要 DF，建议一次上传多个样品 Excel。")
    st.stop()

st.success(f"已加载输入文件：{len(active_uploads)} 个。")
if st.button("清空当前数据", key="cp_screening_clear_cached_input"):
    clear_cached_input()
    st.rerun()

try:
    samples = read_primary_workbooks(active_uploads)
except Exception as exc:
    st.error(f"Excel 读取失败：{exc}")
    st.stop()

sample_mappings = {}

tab_upload, tab_front, tab_downstream, tab_results = st.tabs(["上传与列映射", "化学类型/DBE/VK与DF", "PBM/ToxPi", "下载结果"])

with tab_upload:
    st.subheader("1. 文件与列映射")
    st.metric("上传 Excel 数", len(samples))
    preview_rows = [
        {
            "sample_id": sample.sample_id,
            "file_name": sample.file_name,
            "rows": len(sample.data),
            "columns": len(sample.data.columns),
        }
        for sample in samples
    ]
    show_dataframe(pd.DataFrame(preview_rows))

    st.markdown("**每个 Excel 的列映射**")
    sample_mappings = render_sample_mapping_tabs(samples)
    upload_structure_summary, upload_structure_audit = build_upload_structure_preparation_preview(samples, sample_mappings)
    st.subheader("结构准备汇总")
    show_dataframe(upload_structure_summary)
    if not upload_structure_audit.empty:
        audit_mask = upload_structure_audit["smiles_source"].eq("原始 SMILES（与 MOL 冲突）") | upload_structure_audit["parse_status"].eq("解析失败")
        if audit_mask.any():
            with st.expander("查看结构准备审计记录", expanded=False):
                show_dataframe(upload_structure_audit.loc[audit_mask])

with tab_front:
    st.markdown("**DBE / Van Krevelen 坐标范围**")
    axis_col_min, axis_col_max = st.columns(2)
    with axis_col_min:
        dbe_x_min = st.number_input("DBE X 最小值", value=0.0, step=1.0)
    with axis_col_max:
        dbe_x_max = st.number_input("DBE X 最大值", value=60.0, step=1.0)
    axis_col_min, axis_col_max = st.columns(2)
    with axis_col_min:
        dbe_y_min = st.number_input("DBE Y 最小值", value=0.0, step=1.0)
    with axis_col_max:
        dbe_y_max = st.number_input("DBE Y 最大值", value=30.0, step=1.0)
    axis_col_min, axis_col_max = st.columns(2)
    with axis_col_min:
        vk_x_min = st.number_input("VK O/C 最小值", value=0.0, step=0.1)
    with axis_col_max:
        vk_x_max = st.number_input("VK O/C 最大值", value=1.1, step=0.1)
    axis_col_min, axis_col_max = st.columns(2)
    with axis_col_min:
        vk_y_min = st.number_input("VK H/C 最小值", value=0.0, step=0.1)
    with axis_col_max:
        vk_y_max = st.number_input("VK H/C 最大值", value=2.6, step=0.1)
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
        st.error(f"坐标范围无效：{exc}")
        st.stop()

with tab_downstream:
    st.markdown("**两阶段 ToxPi 设置**")
    toxpi_limit_col, toxpi_display_col = st.columns(2)
    with toxpi_limit_col:
        candidate_top_n = st.number_input(
            "阶段 1 候选 Top N",
            min_value=1,
            value=100,
            step=1,
        )
    with toxpi_display_col:
        display_top_n = st.number_input(
            "图表显示 Top N",
            min_value=1,
            value=20,
            step=1,
        )
    weight_col_pa, weight_col_pbm, weight_col_df = st.columns(3)
    with weight_col_pa:
        pa_weight = st.number_input("Peak area 权重", min_value=0.0, value=0.4, step=0.1)
    with weight_col_pbm:
        pbm_weight = st.number_input("PBM 权重", min_value=0.0, value=0.4, step=0.1)
    with weight_col_df:
        df_weight = st.number_input("DF 权重", min_value=0.0, value=0.2, step=0.1)

    robustness_enabled = st.checkbox("运行权重扰动稳健性分析", value=True)
    robust_col_fraction, robust_col_iterations, robust_col_seed = st.columns(3)
    with robust_col_fraction:
        perturbation_percent = st.number_input(
            "权重扰动（%）",
            min_value=0.0,
            max_value=100.0,
            value=20.0,
            step=5.0,
            disabled=not robustness_enabled,
        )
    with robust_col_iterations:
        robustness_iterations = st.number_input(
            "稳健性迭代次数",
            min_value=1,
            value=1000,
            step=100,
            disabled=not robustness_enabled,
        )
    with robust_col_seed:
        robustness_seed = st.number_input(
            "随机种子",
            min_value=0,
            value=123,
            step=1,
            disabled=not robustness_enabled,
        )
    try:
        toxpi_config = PBMToxPiConfig(
            candidate_top_n=int(candidate_top_n),
            display_top_n=int(display_top_n),
            weights={
                "peak_area": float(pa_weight),
                "pbm": float(pbm_weight),
                "df": float(df_weight),
            },
            robustness_enabled=bool(robustness_enabled),
            perturbation_fraction=float(perturbation_percent) / 100.0,
            n_iter=int(robustness_iterations),
            seed=int(robustness_seed),
        )
    except ValueError as exc:
        st.error(f"ToxPi 设置无效：{exc}")
        st.stop()

settings_payload = (
    axis_ranges.dbe_xlim,
    axis_ranges.dbe_ylim,
    axis_ranges.vk_xlim,
    axis_ranges.vk_ylim,
    toxpi_config.candidate_top_n,
    toxpi_config.display_top_n,
    tuple(sorted(toxpi_config.weights.items())),
    toxpi_config.robustness_enabled,
    toxpi_config.perturbation_fraction,
    toxpi_config.n_iter,
    toxpi_config.seed,
)
settings_signature = hashlib.sha256(repr(settings_payload).encode("utf-8")).hexdigest()
if st.session_state.get("cp_screening_settings_signature") != settings_signature:
    clear_workflow_state()
    st.session_state["cp_screening_settings_signature"] = settings_signature

with tab_front:
    st.subheader("2. 化学类型图、DBE图、VK图与 DF")
    detection_threshold = st.number_input(
        "DF 检出阈值",
        min_value=0.0,
        value=1e5,
        step=10000.0,
        format="%.0f",
        help="沿用 R 流程：Peak Area > 1E+05 计为检出。",
    )
    if st.button("运行化学类型图、DBE图、VK图和 DF", type="primary"):
        with st.spinner("正在处理多文件、生成化学类型图、DBE图、VK图和 DF..."):
            front_result = prepare_multi_file_screening(
                samples,
                sample_mappings,
                detection_threshold,
                axis_ranges,
            )
            front_state = {
                "output_root": front_result.output_root,
                "screening_results": front_result.screening_results,
                "summary_figure_paths": front_result.summary_figure_paths,
                "df_table": front_result.df_table,
                "df_detection_table": front_result.df_detection_table,
                "group_area_raw_long": front_result.group_area_raw_long,
                "group_area_mean_by_sample": front_result.group_area_mean_by_sample,
                "sample_peak_area": front_result.sample_peak_area,
                "representative_table": front_result.representative_table,
                "selected_peak_cols": front_result.selected_peak_cols,
                "sample_mappings": sample_mappings,
                "structure_preparation_summary": (
                    front_result.structure_preparation_summary
                ),
                "structure_preparation_audit": front_result.structure_preparation,
                "input_file_mappings": front_result.input_file_mappings,
                "tables": front_result.tables,
                "charts": front_result.charts,
                "warnings": front_result.warnings,
            }
        st.session_state["cp_screening_front"] = front_state
        for key in STATE_KEYS:
            if key not in {"cp_screening_front", "cp_screening_settings_signature"}:
                st.session_state.pop(key, None)
        st.success("化学类型图、DBE图、VK图和 DF 已完成。")

    front_state = st.session_state.get("cp_screening_front")
    if front_state:
        col_df, col_compounds = st.columns(2)
        with col_df:
            st.metric("DF 化合物数", len(front_state["df_table"]))
        with col_compounds:
            st.metric("参与样品文件数", len(samples))
        render_front_half_figures(front_state)
        st.subheader("DF_Table")
        show_dataframe(front_state["df_table"])
        st.subheader("Sample_Peak_Area")
        show_dataframe(front_state["sample_peak_area"])
        if not front_state["warnings"].empty:
            st.warning(f"化学类型图、DBE图、VK图与 DF 有 {len(front_state['warnings'])} 条提示或失败。")
            show_dataframe(front_state["warnings"])

with tab_downstream:
    st.subheader("3. PubChem / EPI Suite / Pov-LRTP / PA-PBM-DF ToxPi")
    front_state = st.session_state.get("cp_screening_front")
    if not front_state:
        st.info("请先运行“化学类型图、DBE图、VK图和 DF”。")
    else:
        col_provider, col_timeout, col_delay = st.columns([2, 1, 1])
        with col_provider:
            use_pubchem = st.checkbox("使用 PubChem 补全 SMILES", value=True)
            use_epa = st.checkbox("同时使用 EPA 补全 DTXSID", value=False)
            use_echa = st.checkbox("同时使用 ECHA 补全 EC/ECHA ID", value=False)
            pubchem_base = st.text_input("PubChem API base", value=DEFAULT_PUBCHEM_BASE)
            epi_api_url = st.text_input("EPI Web API", value=DEFAULT_EPI_WEB_API)
        with col_timeout:
            identifier_timeout = st.number_input("标识符超时（秒）", min_value=20, max_value=240, value=60, step=10)
            epi_timeout = st.number_input("EPI 超时（秒）", min_value=20, max_value=300, value=90, step=10)
        with col_delay:
            identifier_delay = st.number_input("标识符间隔（秒）", min_value=0.0, max_value=5.0, value=0.2, step=0.1)
            epi_delay = st.number_input("EPI 间隔（秒）", min_value=0.0, max_value=5.0, value=0.2, step=0.1)

        with st.expander("加速设置", expanded=False):
            query_cache_enabled = st.checkbox("启用本地查询缓存", value=True, key="screening_query_cache_enabled")
            identifier_max_workers = st.number_input(
                "标识符并发数",
                min_value=1,
                max_value=8,
                value=3,
                step=1,
                key="screening_identifier_max_workers",
            )
            epi_max_workers = st.number_input(
                "EPI 并发数",
                min_value=1,
                max_value=8,
                value=3,
                step=1,
                key="screening_epi_max_workers",
            )
            st.caption(f"缓存文件：{current_cache_path()}")
            if st.button("清理本地查询缓存", key="screening_clear_query_cache"):
                clear_query_cache()
                st.success("本地查询缓存已清理。")

        if st.button("运行下游 PBM/ToxPi", type="primary"):
            representative = front_state["representative_table"]
            identifier_input = build_identifier_input(
                representative,
                compound_col="Name",
                smiles_col="SMILES_input" if "SMILES_input" in representative.columns else None,
                cas_col="CAS_input" if "CAS_input" in representative.columns else None,
            )
            progress_bar = st.progress(0)
            status_box = st.empty()

            def update_progress(done, total, compound):
                if total:
                    progress_bar.progress(done / total)
                status_box.info(f"正在处理：{compound} ({done}/{total})")

            with st.spinner("正在补全标识符、调用 EPI Suite 并计算 Pov-LRTP..."):
                completed_identifiers, identifier_warnings = run_identifier_completion_batch(
                    identifier_input,
                    use_pubchem=use_pubchem,
                    use_epa=use_epa,
                    use_echa=use_echa,
                    pubchem_base=pubchem_base,
                    timeout=int(identifier_timeout),
                    delay_seconds=float(identifier_delay),
                    max_workers=int(identifier_max_workers),
                    cache_enabled=bool(query_cache_enabled),
                    progress_callback=update_progress,
                )
                epi_input = build_epi_input_from_identifiers(completed_identifiers)
                epi_results, epi_raw_results, epi_errors = run_epi_web_batch(
                    epi_input,
                    api_url=epi_api_url,
                    timeout=int(epi_timeout),
                    delay_seconds=float(epi_delay),
                    max_workers=int(epi_max_workers),
                    cache_enabled=bool(query_cache_enabled),
                    progress_callback=update_progress,
                )
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
                toxpi_input = build_pbm_toxpi_input(
                    front_state["df_table"],
                    pov_lrtp_results,
                    peak_area_long=front_state["sample_peak_area"],
                )
                toxpi_result = calculate_pbm_toxpi(toxpi_input, config=toxpi_config)
                pbm_scores = pov_lrtp_results[
                    [column for column in ["Name", "POV_days", "TE_percent", "Log_BAF_Arnot_Gobas", "P_B_LRTP_score", "Scores"] if column in pov_lrtp_results.columns]
                ].copy()

            st.session_state["cp_screening_downstream"] = {
                "representative_table": representative,
                "identifier_input": identifier_input,
                "completed_identifiers": completed_identifiers,
                "identifier_warnings": identifier_warnings,
                "epi_input": epi_input,
                "epi_results": epi_results,
                "epi_raw_results": epi_raw_results,
                "epi_errors": epi_errors,
                "pov_lrtp_input": pov_lrtp_input,
                "pov_lrtp_results": pov_lrtp_results,
                "pbm_scores": pbm_scores,
                "toxpi_input": toxpi_input,
                "toxpi_config": toxpi_result.config,
                "toxpi_source_metrics": toxpi_result.source_metrics,
                "toxpi_global_screen": toxpi_result.global_screen,
                "toxpi_normalized": toxpi_result.candidate_normalized,
                "toxpi_results": toxpi_result.final_ranking,
                "toxpi_display": toxpi_result.display_rows,
                "toxpi_excluded": toxpi_result.excluded_rows,
                "toxpi_settings": toxpi_result.settings_table(),
                "toxpi_robustness": toxpi_result.robustness_summary,
                "toxpi_robust_stats": toxpi_result.robustness_stats,
                "toxpi_robust_correlations": toxpi_result.robustness_correlations,
                "normalized_weights": toxpi_result.normalized_weights,
                "effective_candidate_top_n": toxpi_result.effective_candidate_top_n,
                "effective_display_top_n": toxpi_result.effective_display_top_n,
            }
            for key in DOWNSTREAM_PLOT_STATE_KEYS:
                st.session_state.pop(key, None)
            if not toxpi_result.display_rows.empty and toxpi_result.display_rows["toxpi"].notna().any():
                refresh_toxpi_radial_plot(st.session_state["cp_screening_downstream"], force=True)
                bar_fig = generate_pbm_toxpi_bar_plot(
                    toxpi_result.display_rows,
                    top_n=len(toxpi_result.display_rows),
                )
                bar_png, bar_pdf = figure_to_png_pdf_bytes(bar_fig)
                st.session_state["cp_screening_bar_png"] = bar_png
                st.session_state["cp_screening_bar_pdf"] = bar_pdf
            if not toxpi_result.robustness_correlations.empty:
                robustness_fig = generate_pbm_toxpi_robustness_plot(toxpi_result)
                robustness_png, robustness_pdf = figure_to_png_pdf_bytes(robustness_fig)
                st.session_state["cp_screening_robustness_png"] = robustness_png
                st.session_state["cp_screening_robustness_pdf"] = robustness_pdf
            st.session_state["cp_screening_workbook"] = build_screening_workbook(
                workflow_tables(front_state, st.session_state["cp_screening_downstream"])
            )
            st.success("PBM/ToxPi 已完成。")

    downstream_state = st.session_state.get("cp_screening_downstream")
    if downstream_state:
        st.subheader("ToxPi_Input")
        show_dataframe(downstream_state["toxpi_input"])
        st.subheader("ToxPi_Global_Screen")
        show_dataframe(downstream_state["toxpi_global_screen"])
        st.subheader("ToxPi_Normalized")
        show_dataframe(downstream_state["toxpi_normalized"])
        st.subheader("ToxPi_Results")
        show_dataframe(downstream_state["toxpi_results"])
        st.subheader("ToxPi_Display")
        show_dataframe(downstream_state["toxpi_display"])
        st.subheader("ToxPi_Excluded")
        show_dataframe(downstream_state.get("toxpi_excluded", pd.DataFrame()))
        st.subheader("ToxPi_Settings")
        show_dataframe(downstream_state["toxpi_settings"])
        if not downstream_state["toxpi_robustness"].empty:
            st.subheader("ToxPi_Robustness")
            show_dataframe(downstream_state["toxpi_robustness"])
            st.subheader("ToxPi_Robust_Stats")
            show_dataframe(downstream_state["toxpi_robust_stats"])
        st.subheader("Pov_LRTP")
        show_dataframe(downstream_state["pov_lrtp_results"])
        st.subheader("ToxPi 图")
        refresh_toxpi_radial_plot(downstream_state)
        radial_png = st.session_state.get("cp_screening_radial_png")
        if radial_png:
            effective_display_top_n = int(downstream_state["effective_display_top_n"])
            effective_candidate_top_n = int(downstream_state["effective_candidate_top_n"])
            omitted_count = max(0, effective_candidate_top_n - effective_display_top_n)
            if omitted_count:
                st.info(
                    f"ToxPi 图显示 Top {effective_display_top_n}；另有 {omitted_count} 个候选化合物保留在完整结果表和工作簿中。"
                )
            st.image(radial_png.getvalue())
        robustness_png = st.session_state.get("cp_screening_robustness_png")
        if robustness_png:
            st.subheader("ToxPi 稳健性图")
            st.image(robustness_png.getvalue())

with tab_results:
    st.subheader("4. 下载结果")
    front_state = st.session_state.get("cp_screening_front")
    downstream_state = st.session_state.get("cp_screening_downstream")
    if front_state and "cp_screening_workbook" not in st.session_state:
        st.session_state["cp_screening_workbook"] = build_screening_workbook(workflow_tables(front_state, downstream_state))

    workbook = st.session_state.get("cp_screening_workbook")
    if workbook:
        st.download_button(
            "下载综合筛查结果工作簿",
            data=workbook,
            file_name="CP_Screening_Workflow_Results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("请先运行化学类型图、DBE图、VK图与 DF，或继续运行下游 PBM/ToxPi。")

    col_png, col_pdf = st.columns(2)
    with col_png:
        bar_png = st.session_state.get("cp_screening_bar_png")
        st.download_button(
            "下载 ToxPi bar PNG",
            data=bar_png if bar_png else io.BytesIO(),
            file_name="PA_PBM_DF_ToxPi_Bar.png",
            mime="image/png",
            disabled=bar_png is None,
        )
        radial_png = st.session_state.get("cp_screening_radial_png")
        st.download_button(
            "下载 ToxPi radial PNG",
            data=radial_png if radial_png else io.BytesIO(),
            file_name="PA_PBM_DF_ToxPi_Radial.png",
            mime="image/png",
            disabled=radial_png is None,
        )
        robustness_png = st.session_state.get("cp_screening_robustness_png")
        st.download_button(
            "下载 ToxPi robustness PNG",
            data=robustness_png if robustness_png else io.BytesIO(),
            file_name="PA_PBM_DF_ToxPi_Robustness.png",
            mime="image/png",
            disabled=robustness_png is None,
        )
    with col_pdf:
        bar_pdf = st.session_state.get("cp_screening_bar_pdf")
        st.download_button(
            "下载 ToxPi bar PDF",
            data=bar_pdf if bar_pdf else io.BytesIO(),
            file_name="PA_PBM_DF_ToxPi_Bar.pdf",
            mime="application/pdf",
            disabled=bar_pdf is None,
        )
        radial_pdf = st.session_state.get("cp_screening_radial_pdf")
        st.download_button(
            "下载 ToxPi radial PDF",
            data=radial_pdf if radial_pdf else io.BytesIO(),
            file_name="PA_PBM_DF_ToxPi_Radial.pdf",
            mime="application/pdf",
            disabled=radial_pdf is None,
        )
        robustness_pdf = st.session_state.get("cp_screening_robustness_pdf")
        st.download_button(
            "下载 ToxPi robustness PDF",
            data=robustness_pdf if robustness_pdf else io.BytesIO(),
            file_name="PA_PBM_DF_ToxPi_Robustness.pdf",
            mime="application/pdf",
            disabled=robustness_pdf is None,
        )
