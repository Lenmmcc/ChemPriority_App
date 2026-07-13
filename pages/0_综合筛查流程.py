import hashlib
import importlib
import io
import os
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cp_screening_workflow import (  # noqa: E402
    PBM_TOXPI_WEIGHTS,
    build_detection_frequency,
    build_group_area_mean_by_sample,
    build_peak_area_long,
    build_pbm_toxpi_input,
    build_screening_workbook,
    calculate_pbm_toxpi,
    figure_to_pdf_bytes,
    figure_to_png_bytes,
    generate_pbm_toxpi_bar_plot,
    limit_toxpi_plot_rows,
)
from src.episuite_io import DEFAULT_EPI_WEB_API, run_epi_web_batch  # noqa: E402
from src.identifier_resolver import DEFAULT_PUBCHEM_BASE, run_identifier_completion_batch  # noqa: E402
from src.mol_structure_parser import prepare_structure_dataframe  # noqa: E402
from src.pov_lrtp_replica import run_pov_lrtp_batch  # noqa: E402
from src.query_cache import clear_query_cache, current_cache_path  # noqa: E402
from src.r_screening_replica import ScreeningConfig, run_screening_pipeline  # noqa: E402
from src.r_screening_replica.downstream import (  # noqa: E402
    build_epi_input_from_identifiers,
    build_identifier_input,
    build_pov_lrtp_input,
)
from src.r_screening_replica.plots import save_boxplot_log_transformed, save_compound_bubble_plot  # noqa: E402
from src.upload_state import cached_uploads, clear_uploads, store_uploads, upload_bytes, upload_name  # noqa: E402
import src.toxpi_calc as toxpi_calc  # noqa: E402

if not hasattr(toxpi_calc, "generate_r_style_toxpi_plot"):
    toxpi_calc = importlib.reload(toxpi_calc)
generate_r_style_toxpi_plot = toxpi_calc.generate_r_style_toxpi_plot


STATE_KEYS = (
    "cp_screening_front",
    "cp_screening_downstream",
    "cp_screening_workbook",
    "cp_screening_bar_png",
    "cp_screening_bar_pdf",
    "cp_screening_radial_png",
    "cp_screening_radial_pdf",
    "cp_screening_radial_plot_version",
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

TOXPI_RADIAL_MAX_COMPOUNDS = 15
TOXPI_RADIAL_PLOT_VERSION = "r_style_single_canvas_v1"

STANDARD_COMPOUND_COL = "Name"
STANDARD_FORMULA_COL = "formula"
STANDARD_SMILES_COL = "SMILES_input"
STANDARD_CAS_COL = "CAS_input"


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


def parse_uploaded_workbooks(uploaded_files):
    samples = []
    for uploaded in uploaded_files:
        data = upload_bytes(uploaded)
        file_name = upload_name(uploaded)
        frame = pd.read_excel(io.BytesIO(data))
        frame.columns = [str(column).strip() for column in frame.columns]
        samples.append({"name": Path(file_name).stem, "file_name": file_name, "bytes": data, "data": frame})
    return samples


def guess_column(columns, candidates, fallback_index=0):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return columns[fallback_index] if columns else None


def ordered_workbook_columns(samples):
    columns = []
    seen = set()
    for sample in samples:
        for column in sample["data"].columns:
            if column not in seen:
                seen.add(column)
                columns.append(column)
    return columns


def is_group_area_column(column):
    text = str(column).strip().lower().replace("_", " ")
    return text.startswith("group area")


def guess_peak_area_column(columns):
    for candidate in ["Group_Area", "Peak_Area", "Peak area", "Area"]:
        if candidate in columns:
            return candidate
    group_area_columns = [column for column in columns if is_group_area_column(column)]
    if group_area_columns:
        return group_area_columns[0]
    return columns[0] if columns else None


def guess_sample_columns(columns, compound_col, formula_col, peak_area_col):
    preferred = [column for column in ["HH_alk", "WH_alk"] if column in columns]
    if preferred:
        return preferred
    group_columns = [
        column
        for column in columns
        if is_group_area_column(column) and column not in {compound_col, formula_col}
    ]
    if group_columns:
        return group_columns
    return [peak_area_col] if peak_area_col else []


def column_index(columns, value):
    return columns.index(value) if value in columns else 0


def widget_key(prefix, sample_name, index):
    digest = hashlib.sha1(f"{prefix}:{sample_name}:{index}".encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def group_area_columns(columns):
    return [column for column in columns if is_group_area_column(column)]


def sample_mapping_defaults(sample):
    columns = list(sample["data"].columns)
    compound_col = guess_column(columns, ["Name", "compound", "Compound", "Chemical name"])
    formula_col = guess_column(
        columns,
        ["formula", "Formula", "Molecular Formula", "NIST Lib Hit Formula"],
        fallback_index=0,
    )
    peak_area_col = guess_peak_area_column(columns)
    default_sample_cols = group_area_columns(columns) or ([peak_area_col] if peak_area_col else [])
    return {
        "compound_col": compound_col,
        "formula_col": formula_col,
        "peak_area_col": peak_area_col,
        "sample_cols": default_sample_cols,
        "mol_column": None,
        "smiles_col": None,
        "cas_col": None,
    }


def render_sample_mapping_tabs(samples):
    sample_mappings = {}
    mapping_tabs = st.tabs([sample["name"] for sample in samples])
    for index, (tab, sample) in enumerate(zip(mapping_tabs, samples)):
        with tab:
            columns = list(sample["data"].columns)
            defaults = sample_mapping_defaults(sample)
            st.caption(f"{sample['file_name']} | {len(sample['data'])} rows | {len(columns)} columns")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                compound_col = st.selectbox(
                    "化合物名称列",
                    columns,
                    index=column_index(columns, defaults["compound_col"]),
                    key=widget_key("cp_compound_col", sample["name"], index),
                )
            with col_b:
                formula_col = st.selectbox(
                    "分子式列",
                    columns,
                    index=column_index(columns, defaults["formula_col"]),
                    key=widget_key("cp_formula_col", sample["name"], index),
                )
            with col_c:
                peak_area_col = st.selectbox(
                    "默认峰面积列",
                    columns,
                    index=column_index(columns, defaults["peak_area_col"]),
                    key=widget_key("cp_peak_area_col", sample["name"], index),
                )

            default_sample_cols = [column for column in defaults["sample_cols"] if column in columns]
            sample_cols = st.multiselect(
                "参与绘图、DF 和 PA/ToxPi 的 Group Area 列",
                columns,
                default=default_sample_cols,
                help="单个 Excel 内选择多个 Group Area 时，会先按文件内均值进入 DF 和 ToxPi；箱线图仍保留原始点位长表。",
                key=widget_key("cp_sample_cols", sample["name"], index),
            )
            if not sample_cols and peak_area_col:
                st.info("未选择 Group Area 列；本文件不会参与化学类型图、DBE图、VK图、DF 和 PA/ToxPi。")

            optional_columns = [""] + columns
            opt_a, opt_b, opt_c = st.columns(3)
            with opt_a:
                mol_column = st.selectbox(
                    "可选：MOL 文本列",
                    optional_columns,
                    index=0,
                    key=widget_key("cp_mol_col", sample["name"], index),
                ) or None
            with opt_b:
                smiles_col = st.selectbox(
                    "可选：已有 SMILES 列",
                    optional_columns,
                    index=0,
                    key=widget_key("cp_smiles_col", sample["name"], index),
                ) or None
            with opt_c:
                cas_col = st.selectbox(
                    "可选：已有 CAS 列",
                    optional_columns,
                    index=0,
                    key=widget_key("cp_cas_col", sample["name"], index),
                ) or None

            sample_mappings[sample["name"]] = {
                "compound_col": compound_col,
                "formula_col": formula_col,
                "peak_area_col": peak_area_col,
                "sample_cols": sample_cols,
                "mol_column": mol_column,
                "smiles_col": smiles_col,
                "cas_col": cas_col,
            }
    return sample_mappings


def normalize_samples_for_mappings(samples, sample_mappings):
    normalized_samples = []
    selected_peak_cols = []
    seen_peak_cols = set()
    warnings = []

    for sample in samples:
        mapping = sample_mappings.get(sample["name"]) or sample_mapping_defaults(sample)
        frame = sample["data"]
        prepared = prepare_structure_dataframe(
            frame,
            mol_column=mapping.get("mol_column"),
            smiles_column=mapping.get("smiles_col"),
        )
        normalized = frame.copy()

        compound_col = mapping.get("compound_col")
        if compound_col in frame.columns:
            normalized[STANDARD_COMPOUND_COL] = frame[compound_col].map(clean_text)
        else:
            normalized[STANDARD_COMPOUND_COL] = ""
            warnings.append(
                {
                    "stage": "column_mapping",
                    "sample_id": sample["name"],
                    "message": f"Compound column is missing: {compound_col}",
                }
            )

        formula_col = mapping.get("formula_col")
        normalized[STANDARD_FORMULA_COL] = frame[formula_col] if formula_col in frame.columns else pd.NA

        available_peak_cols = [column for column in mapping.get("sample_cols", []) if column in frame.columns]
        for column in available_peak_cols:
            normalized[column] = frame[column]
            if column not in seen_peak_cols:
                selected_peak_cols.append(column)
                seen_peak_cols.add(column)

        peak_area_col = mapping.get("peak_area_col")
        if peak_area_col and peak_area_col in frame.columns and peak_area_col not in normalized.columns:
            normalized[peak_area_col] = frame[peak_area_col]

        normalized[STANDARD_SMILES_COL] = prepared["smiles"]
        cas_col = mapping.get("cas_col")
        if cas_col and cas_col in frame.columns:
            normalized[STANDARD_CAS_COL] = frame[cas_col]

        if not available_peak_cols:
            warnings.append(
                {
                    "stage": "column_mapping",
                    "sample_id": sample["name"],
                    "message": "No Group Area columns were selected for this file.",
                }
            )

        normalized_samples.append({**sample, "data": normalized, "column_mapping": mapping})

    warning_table = pd.DataFrame(warnings, columns=["stage", "sample_id", "message"])
    return normalized_samples, selected_peak_cols, warning_table


def row_peak_area(frame, peak_area_cols):
    available_cols = [column for column in peak_area_cols if column in frame.columns]
    if not available_cols:
        return pd.Series(pd.NA, index=frame.index, dtype="float64")
    return frame[available_cols].apply(pd.to_numeric, errors="coerce").mean(axis=1, skipna=True)


def dataframe_to_excel_bytes(frame):
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


def safe_path_name(value):
    text = clean_text(value) or "sample"
    return "".join(char if char.isalnum() or char in "._- " else "_" for char in text).strip() or "sample"


def build_representative_screening_table(samples, compound_col, formula_col, peak_area_col, sample_cols=None, smiles_col=None, cas_col=None):
    frames = []
    sample_cols = sample_cols or []
    for sample in samples:
        frame = sample["data"].copy()
        frame["sample_id"] = sample["name"]
        frame["Name"] = frame[compound_col].map(clean_text)
        frame["formula"] = frame[formula_col] if formula_col in frame.columns else pd.NA
        peak_area_cols = sample_cols or [peak_area_col]
        frame["Group_Area"] = row_peak_area(frame, peak_area_cols)
        if smiles_col and smiles_col in frame.columns:
            frame["SMILES_input"] = frame[smiles_col]
        if cas_col and cas_col in frame.columns:
            frame["CAS_input"] = frame[cas_col]
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    combined["compound_key"] = combined["Name"].map(compound_key)
    combined = combined[combined["compound_key"].ne("")].copy()
    combined = combined.sort_values("Group_Area", ascending=False, na_position="last")
    output_cols = ["Name", "formula", "Group_Area", "compound_key"]
    if "SMILES_input" in combined.columns:
        output_cols.append("SMILES_input")
    if "CAS_input" in combined.columns:
        output_cols.append("CAS_input")
    return combined.drop_duplicates("compound_key", keep="first")[output_cols].reset_index(drop=True)


def replace_dbe_bubble_with_thresholded_plot(result, detection_threshold):
    dbe_table = result.dbe_table.copy()
    peak_area = pd.to_numeric(dbe_table["peak_area"], errors="coerce")
    thresholded_dbe = dbe_table.loc[peak_area > detection_threshold].copy()
    figures_dir = result.config.output_path / "figures"
    result.figure_paths["compound_bubble_plot"] = save_compound_bubble_plot(
        thresholded_dbe,
        result.compound_categories,
        figures_dir,
    )
    result.metadata["dbe_plot_threshold"] = detection_threshold


def build_summary_figure_paths(screening_results, group_area_mean, output_root):
    if group_area_mean.empty or not screening_results:
        return {}

    category_frames = []
    for _sample_id, result in screening_results:
        if isinstance(result.compound_categories, pd.DataFrame) and not result.compound_categories.empty:
            category_frames.append(result.compound_categories)
    if not category_frames:
        return {}

    compound_categories = (
        pd.concat(category_frames, ignore_index=True)
        .drop_duplicates("Formula")
        .reset_index(drop=True)
    )
    summary_figures_dir = output_root / "summary" / "figures"
    summary_figures_dir.mkdir(parents=True, exist_ok=True)
    return {
        "boxplot_log_transformed": save_boxplot_log_transformed(
            group_area_mean,
            compound_categories,
            summary_figures_dir,
        )
    }


def collect_front_half(samples, sample_mappings, detection_threshold):
    output_root = Path(tempfile.mkdtemp(prefix="cp_screening_"))
    screening_results = []
    warnings = []
    normalized_samples, selected_peak_cols, mapping_warnings = normalize_samples_for_mappings(samples, sample_mappings)
    if not mapping_warnings.empty:
        warnings.extend(mapping_warnings.to_dict("records"))

    for sample in normalized_samples:
        file_sample_cols = [column for column in selected_peak_cols if column in sample["data"].columns]
        if not file_sample_cols:
            warnings.append(
                {
                    "stage": "R_front_half",
                    "sample_id": sample["name"],
                    "message": "No selected peak-area columns are present in this file.",
                }
            )
            continue
        mean_frame = sample["data"].copy()
        mean_frame["Group_Area_Mean"] = row_peak_area(mean_frame, file_sample_cols)
        config = ScreeningConfig(
            compound_col=STANDARD_COMPOUND_COL,
            formula_col=STANDARD_FORMULA_COL,
            group_area_col="Group_Area_Mean",
            sample_cols=["Group_Area_Mean"],
            output_dir=output_root / safe_path_name(sample["name"]) / "workbook",
        )
        try:
            result = run_screening_pipeline(dataframe_to_excel_bytes(mean_frame), config=config)
            replace_dbe_bubble_with_thresholded_plot(result, detection_threshold)
        except Exception as exc:
            warnings.append(
                {
                    "stage": "R_front_half",
                    "sample_id": sample["name"],
                    "message": str(exc),
                }
            )
        else:
            screening_results.append((sample["name"], result))
            for warning in result.warnings:
                warnings.append(
                    {
                        "stage": "R_front_half",
                        "sample_id": sample["name"],
                        "message": warning,
                    }
                )

    df_table, sample_peak_area = build_detection_frequency(
        [(sample["name"], sample["data"]) for sample in normalized_samples],
        compound_col=STANDARD_COMPOUND_COL,
        peak_area_col=selected_peak_cols,
        detection_threshold=detection_threshold,
    )
    group_area_raw_long = build_peak_area_long(
        [(sample["name"], sample["data"]) for sample in normalized_samples],
        compound_col=STANDARD_COMPOUND_COL,
        formula_col=STANDARD_FORMULA_COL,
        peak_area_cols=selected_peak_cols,
    )
    group_area_mean = build_group_area_mean_by_sample(
        [(sample["name"], sample["data"]) for sample in normalized_samples],
        compound_col=STANDARD_COMPOUND_COL,
        formula_col=STANDARD_FORMULA_COL,
        peak_area_cols=selected_peak_cols,
    )
    summary_figure_paths = build_summary_figure_paths(screening_results, group_area_mean, output_root)
    representative_peak_col = selected_peak_cols[0] if selected_peak_cols else ""

    return {
        "output_root": str(output_root),
        "screening_results": screening_results,
        "summary_figure_paths": summary_figure_paths,
        "df_table": df_table,
        "df_detection_table": sample_peak_area,
        "group_area_raw_long": group_area_raw_long,
        "group_area_mean_by_sample": group_area_mean,
        "sample_peak_area": group_area_mean,
        "representative_table": build_representative_screening_table(
            normalized_samples,
            STANDARD_COMPOUND_COL,
            STANDARD_FORMULA_COL,
            representative_peak_col,
            sample_cols=selected_peak_cols,
            smiles_col=STANDARD_SMILES_COL,
            cas_col=STANDARD_CAS_COL,
        ),
        "selected_peak_cols": selected_peak_cols,
        "sample_mappings": sample_mappings,
        "warnings": pd.DataFrame(warnings, columns=["stage", "sample_id", "message"]),
    }


def dataframe_with_sample(screening_results, attr_name):
    frames = []
    for sample_id, result in screening_results:
        table = getattr(result, attr_name)
        if isinstance(table, pd.DataFrame) and not table.empty:
            frame = table.copy()
            frame.insert(0, "sample_id", sample_id)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def compound_key(value):
    return " ".join(clean_text(value).lower().split())


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
    screening_results = front_state.get("screening_results", [])
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
        "Input_Check": dataframe_with_sample(screening_results, "input_check"),
        "Elemental_Ratios_DBE": dataframe_with_sample(screening_results, "all_formulas"),
        "Category_Summary": dataframe_with_sample(screening_results, "category_summary"),
        "Sample_Peak_Area": front_state.get("sample_peak_area", pd.DataFrame()),
        "Group_Area_Raw_Long": front_state.get("group_area_raw_long", pd.DataFrame()),
        "Group_Area_Mean_By_Sample": front_state.get("group_area_mean_by_sample", pd.DataFrame()),
        "DF_Table": front_state.get("df_table", pd.DataFrame()),
        "Identifier_Completion": downstream_state.get("completed_identifiers", pd.DataFrame()),
        "EPI_Results": downstream_state.get("epi_results", pd.DataFrame()),
        "Pov_LRTP": downstream_state.get("pov_lrtp_results", pd.DataFrame()),
        "PBM_Scores": downstream_state.get("pbm_scores", pd.DataFrame()),
        "ToxPi_Input": downstream_state.get("toxpi_input", pd.DataFrame()),
        "ToxPi_Results": downstream_state.get("toxpi_results", pd.DataFrame()),
        "Excluded_or_Failed": excluded,
        "Warnings": pd.concat(warnings, ignore_index=True) if warnings else pd.DataFrame(),
    }


def refresh_toxpi_radial_plot(downstream_state, force=False):
    if not downstream_state:
        return
    if not force and st.session_state.get("cp_screening_radial_plot_version") == TOXPI_RADIAL_PLOT_VERSION:
        return

    radial_plot_rows = downstream_state.get("radial_plot_rows")
    if not isinstance(radial_plot_rows, pd.DataFrame) or radial_plot_rows.empty:
        toxpi_results = downstream_state.get("toxpi_results")
        if not isinstance(toxpi_results, pd.DataFrame) or toxpi_results.empty:
            return
        radial_plot_rows, radial_omitted_count = limit_toxpi_plot_rows(
            toxpi_results,
            max_compounds=TOXPI_RADIAL_MAX_COMPOUNDS,
        )
        downstream_state["radial_plot_rows"] = radial_plot_rows
        downstream_state["radial_omitted_count"] = radial_omitted_count
    if radial_plot_rows.empty:
        return

    radial_fig = generate_r_style_toxpi_plot(
        radial_plot_rows,
        custom_weights=PBM_TOXPI_WEIGHTS,
        toxic_cols=["peak_area", "pbm", "df"],
        label_wrap_width=20,
    )
    st.session_state["cp_screening_radial_png"] = figure_to_png_bytes(radial_fig)
    st.session_state["cp_screening_radial_pdf"] = figure_to_pdf_bytes(radial_fig)
    st.session_state["cp_screening_radial_plot_version"] = TOXPI_RADIAL_PLOT_VERSION
    plt.close(radial_fig)


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
    samples = parse_uploaded_workbooks(active_uploads)
except Exception as exc:
    st.error(f"Excel 读取失败：{exc}")
    st.stop()

sample_mappings = {}

tab_upload, tab_front, tab_downstream, tab_results = st.tabs(["上传与列映射", "化学类型/DBE/VK与DF", "PBM/ToxPi", "下载结果"])

with tab_upload:
    st.subheader("1. 文件与列映射")
    st.metric("上传 Excel 数", len(samples))
    preview_rows = [
        {"sample_id": sample["name"], "file_name": sample["file_name"], "rows": len(sample["data"]), "columns": len(sample["data"].columns)}
        for sample in samples
    ]
    show_dataframe(pd.DataFrame(preview_rows))

    st.markdown("**每个 Excel 的列映射**")
    sample_mappings = render_sample_mapping_tabs(samples)

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
            front_state = collect_front_half(
                samples,
                sample_mappings,
                detection_threshold,
            )
        st.session_state["cp_screening_front"] = front_state
        st.session_state.pop("cp_screening_downstream", None)
        st.session_state.pop("cp_screening_workbook", None)
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
                normalized_toxpi, toxpi_results = calculate_pbm_toxpi(toxpi_input)
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
                "normalized_toxpi": normalized_toxpi,
                "toxpi_results": toxpi_results,
            }
            if not toxpi_results.empty and toxpi_results["toxpi"].notna().any():
                radial_plot_rows, radial_omitted_count = limit_toxpi_plot_rows(
                    toxpi_results,
                    max_compounds=TOXPI_RADIAL_MAX_COMPOUNDS,
                )
                st.session_state["cp_screening_downstream"]["radial_plot_rows"] = radial_plot_rows
                st.session_state["cp_screening_downstream"]["radial_omitted_count"] = radial_omitted_count
                refresh_toxpi_radial_plot(st.session_state["cp_screening_downstream"], force=True)
                bar_fig = generate_pbm_toxpi_bar_plot(toxpi_results)
                st.session_state["cp_screening_bar_png"] = figure_to_png_bytes(bar_fig)
                st.session_state["cp_screening_bar_pdf"] = figure_to_pdf_bytes(bar_fig)
                plt.close(bar_fig)
            st.session_state["cp_screening_workbook"] = build_screening_workbook(
                workflow_tables(front_state, st.session_state["cp_screening_downstream"])
            )
            st.success("PBM/ToxPi 已完成。")

    downstream_state = st.session_state.get("cp_screening_downstream")
    if downstream_state:
        st.subheader("ToxPi_Results")
        show_dataframe(downstream_state["toxpi_results"])
        st.subheader("Pov_LRTP")
        show_dataframe(downstream_state["pov_lrtp_results"])
        st.subheader("ToxPi 图")
        refresh_toxpi_radial_plot(downstream_state)
        radial_png = st.session_state.get("cp_screening_radial_png")
        if radial_png:
            omitted_count = int(downstream_state.get("radial_omitted_count", 0) or 0)
            if omitted_count:
                st.info(f"Radial ToxPi preview follows the original R logic: only Top {TOXPI_RADIAL_MAX_COMPOUNDS} compounds are plotted; {omitted_count} lower-ranked compounds remain in the full result tables and workbook.")
            st.image(radial_png.getvalue())

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
