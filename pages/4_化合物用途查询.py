import hashlib
import io
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.comptox_use import (  # noqa: E402
    DEFAULT_API_BASE as COMPTOX_DEFAULT_API_BASE,
    DEFAULT_DASHBOARD_BASE,
    REQUIRED_IDENTIFIER_COLUMNS as COMPTOX_REQUIRED_IDENTIFIER_COLUMNS,
    TOP_N_DEFAULT as COMPTOX_TOP_N_DEFAULT,
    build_empty_summary_template as comptox_build_empty_summary_template,
    build_result_workbook as comptox_build_result_workbook,
    make_template_file as make_comptox_template_file,
    normalize_input_columns as normalize_comptox_input_columns,
    run_comptox_use_batch,
    validate_input as validate_comptox_input,
)
from src.echa_use import (  # noqa: E402
    DEFAULT_ECHA_BASE,
    REQUIRED_IDENTIFIER_COLUMNS as ECHA_REQUIRED_IDENTIFIER_COLUMNS,
    TOP_N_DEFAULT as ECHA_TOP_N_DEFAULT,
    build_empty_summary_template as echa_build_empty_summary_template,
    build_result_workbook as echa_build_result_workbook,
    make_template_file as make_echa_template_file,
    normalize_input_columns as normalize_echa_input_columns,
    run_echa_use_batch,
    validate_input as validate_echa_input,
)
from src.identifier_resolver import (  # noqa: E402
    DEFAULT_PUBCHEM_BASE,
    REQUIRED_IDENTIFIER_COLUMNS as RESOLVER_REQUIRED_IDENTIFIER_COLUMNS,
    build_empty_completed_template as resolver_build_empty_completed_template,
    build_result_workbook as resolver_build_result_workbook,
    make_template_file as make_resolver_template_file,
    normalize_input_columns as normalize_resolver_input_columns,
    run_identifier_completion_batch,
    validate_input as validate_resolver_input,
)
from src.use_rose_plot import (  # noqa: E402
    extract_use_rose_data,
    figure_to_pdf_bytes,
    figure_to_png_bytes,
    generate_combined_use_rose_plot,
    generate_use_rose_plot,
)


def show_dataframe(df):
    try:
        st.dataframe(df, width="stretch")
    except TypeError:
        st.dataframe(df, use_container_width=True)


INPUT_CACHE_KEYS = (
    "use_query_input_bytes",
    "use_query_input_name",
)
QUERY_RESULT_KEYS = (
    "identifier_input_signature",
    "identifier_completed_input_df",
    "identifier_completed_df",
    "identifier_warnings_df",
    "identifier_completion_notice",
    "comptox_use_summary",
    "comptox_use_candidates",
    "comptox_use_errors",
    "echa_use_summary",
    "echa_use_candidates",
    "echa_use_dossiers",
    "echa_use_errors",
)


def clear_query_results():
    for key in QUERY_RESULT_KEYS:
        st.session_state.pop(key, None)


def clear_cached_query_input():
    for key in INPUT_CACHE_KEYS:
        st.session_state.pop(key, None)
    clear_query_results()


st.set_page_config(
    page_title="化合物用途查询 - ChemPriority",
    page_icon="🔎",
    layout="wide",
)


st.title("🔎 化合物用途查询")
st.caption("上传化合物表格，分别连接 EPA CompTox Dashboard 和 ECHA CHEM 查询用途证据，并按证据强度取前五个用途。")
st.markdown("---")

left_col, right_col = st.columns([2, 1])

with left_col:
    st.subheader("1. 上传查询表")
    uploaded_file = st.file_uploader(
        "上传 Excel 文件",
        type=["xlsx", "xls"],
        help="可只包含 compound 和 smiles；建议补全后再做 EPA/ECHA 查询。",
    )

with right_col:
    st.subheader("输入模板")
    st.download_button(
        label="下载标识符补全模板",
        data=make_resolver_template_file(),
        file_name="Identifier_Completion_Input_Template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.download_button(
        label="下载 EPA 模板",
        data=make_comptox_template_file(),
        file_name="EPA_CompTox_Use_Input_Template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.download_button(
        label="下载 ECHA 模板",
        data=make_echa_template_file(),
        file_name="ECHA_Use_Input_Template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.info(
    "只有 SMILES 或 CAS 时，建议先运行“标识符补全”。系统会先用 PubChem 解析 CAS 或 SMILES，"
    "再用 EPA/ECHA 补全用途查询所需的 DTXSID、CAS、EC 和 ECHA ID。"
)

if uploaded_file is not None:
    uploaded_bytes = uploaded_file.getvalue()
    st.session_state["use_query_input_bytes"] = uploaded_bytes
    st.session_state["use_query_input_name"] = uploaded_file.name

cached_input_bytes = st.session_state.get("use_query_input_bytes")
cached_input_name = st.session_state.get("use_query_input_name")

if cached_input_bytes is None:
    st.info("请先上传包含 compound、cas、smiles、dtxsid、ec 或 echa_id 的 Excel 文件。")
    st.stop()

st.success(f"已加载输入文件：{cached_input_name}")
if st.button("清空当前数据", key="use_clear_cached_query_input"):
    clear_cached_query_input()
    st.rerun()

try:
    input_signature = hashlib.sha256(cached_input_bytes).hexdigest()
    raw_input_df = pd.read_excel(io.BytesIO(cached_input_bytes))
except Exception as exc:
    st.error(f"Excel 读取失败：{exc}")
    st.stop()

if st.session_state.get("identifier_input_signature") != input_signature:
    clear_query_results()
    st.session_state["identifier_input_signature"] = input_signature

resolver_input_df = normalize_resolver_input_columns(raw_input_df)
resolver_valid, resolver_message = validate_resolver_input(resolver_input_df)

completed_query_input_df = st.session_state.get("identifier_completed_input_df")
if isinstance(completed_query_input_df, pd.DataFrame) and not completed_query_input_df.empty:
    query_input_df = completed_query_input_df
    query_input_label = "当前 EPA/ECHA 查询使用标识符补全结果。"
else:
    query_input_df = raw_input_df
    query_input_label = "当前 EPA/ECHA 查询使用原始上传表。"

comptox_input_df = normalize_comptox_input_columns(query_input_df)
echa_input_df = normalize_echa_input_columns(query_input_df)

comptox_valid, comptox_message = validate_comptox_input(comptox_input_df)
echa_valid, echa_message = validate_echa_input(echa_input_df)

if not resolver_valid and not comptox_valid and not echa_valid:
    st.error("输入表格没有可用于标识符补全、EPA 或 ECHA 查询的标识列。")
    show_dataframe(raw_input_df)
    st.stop()

if resolver_valid:
    st.success(resolver_message)
else:
    st.warning(f"标识符补全输入检查未通过：{resolver_message}")

if comptox_valid:
    st.success(comptox_message)
else:
    st.warning(f"EPA 输入检查未通过：{comptox_message}")

if echa_valid:
    st.success(echa_message)
else:
    st.warning(f"ECHA 输入检查未通过：{echa_message}")

tab_input, tab_resolver, tab_epa, tab_echa, tab_rose, tab_output, tab_notes = st.tabs(
    ["输入数据", "标识符补全", "EPA CompTox 查询", "ECHA 查询", "用途图谱", "结果下载", "字段说明"]
)

with tab_input:
    st.subheader("待查询化合物")
    st.info(query_input_label)
    view_raw, view_resolver, view_epa, view_echa = st.tabs(["原始表格", "补全标准列", "EPA 标准列", "ECHA 标准列"])
    with view_raw:
        show_dataframe(raw_input_df)
    with view_resolver:
        show_dataframe(resolver_input_df[RESOLVER_REQUIRED_IDENTIFIER_COLUMNS])
        completed_df = st.session_state.get("identifier_completed_df")
        if completed_df is not None and not completed_df.empty:
            st.subheader("已补全标识符")
            show_dataframe(completed_df)
    with view_epa:
        show_dataframe(comptox_input_df[COMPTOX_REQUIRED_IDENTIFIER_COLUMNS])
    with view_echa:
        show_dataframe(echa_input_df[ECHA_REQUIRED_IDENTIFIER_COLUMNS])
    st.metric("化合物数量", len(raw_input_df))

with tab_resolver:
    st.subheader("2. 标识符补全")
    st.write(
        "适用于只有 SMILES、CAS 或名称、缺少 EC/DTXSID/ECHA ID 的情况。"
        "系统会先用 PubChem 解析 CAS 或 SMILES，再尝试 EPA 补 DTXSID/CAS，"
        "最后按 ECHA ID、EC、CAS、SMILES、名称的顺序尝试补 ECHA ID。"
    )

    if not resolver_valid:
        st.error(resolver_message)

    col_source, col_timeout, col_delay = st.columns([2, 1, 1])
    with col_source:
        use_epa_resolver = st.checkbox(
            "使用 EPA 补全 DTXSID/CAS",
            value=True,
            key="resolver_use_epa",
            help="只有 SMILES 时，EPA 通常比 ECHA 更适合作为第一步匹配。",
        )
        use_pubchem_resolver = st.checkbox(
            "使用 PubChem 从 CAS 或 SMILES 预补全",
            value=True,
            key="resolver_use_pubchem",
            help="优先用 CAS、没有 CAS 时用 SMILES，从 PubChem 获得 CID、SMILES、名称和可用同义标识符。",
        )
        use_echa_resolver = st.checkbox(
            "使用 ECHA 补全 EC/ECHA ID",
            value=True,
            key="resolver_use_echa",
            help="ECHA 更依赖 ECHA ID、EC、CAS 或明确名称；只有 SMILES 时稳定性较弱。",
        )
    with col_timeout:
        resolver_timeout_seconds = st.number_input(
            "补全请求超时（秒）",
            min_value=20,
            max_value=240,
            value=60,
            step=10,
            key="resolver_timeout_seconds",
        )
    with col_delay:
        resolver_delay_seconds = st.number_input(
            "补全请求间隔（秒）",
            min_value=0.0,
            max_value=5.0,
            value=0.2,
            step=0.1,
            key="resolver_delay_seconds",
        )

    with st.expander("补全接口设置", expanded=False):
        resolver_api_base = st.text_input(
            "自定义 EPA CompTox API 地址（补全用，可选）",
            value=COMPTOX_DEFAULT_API_BASE,
            placeholder="留空，使用 CompTox Dashboard 匹配",
            help="EPA 旧直连 API 已下线。留空时会使用可访问的 CompTox Dashboard 进行 DTXSID 匹配。",
            key="resolver_epa_api_base",
        )
        resolver_api_key = st.text_input(
            "EPA API Key（补全用，可选）",
            value="",
            type="password",
            help="仅用于上方配置的自定义 EPA API。",
            key="resolver_epa_api_key",
        )
        resolver_echa_base = st.text_input(
            "ECHA CHEM 地址（补全用）",
            value=DEFAULT_ECHA_BASE,
            key="resolver_echa_base",
        )
        resolver_pubchem_base = st.text_input(
            "PubChem PUG REST 地址（补全用）",
            value=DEFAULT_PUBCHEM_BASE,
            key="resolver_pubchem_base",
        )

    if st.button("开始补全标识符", type="primary", disabled=not resolver_valid, key="resolver_start"):
        progress_bar = st.progress(0)
        status_box = st.empty()

        def update_resolver_progress(done, total, compound):
            progress_bar.progress(done / total)
            status_box.info(f"正在补全：{compound} ({done}/{total})")

        with st.spinner("正在补全标识符，请等待..."):
            completed_df, warnings_df = run_identifier_completion_batch(
                resolver_input_df,
                comptox_api_base=resolver_api_base,
                comptox_api_key=resolver_api_key.strip() or None,
                echa_base=resolver_echa_base,
                use_epa=use_epa_resolver,
                use_echa=use_echa_resolver,
                use_pubchem=use_pubchem_resolver,
                pubchem_base=resolver_pubchem_base,
                timeout=int(resolver_timeout_seconds),
                delay_seconds=float(resolver_delay_seconds),
                progress_callback=update_resolver_progress,
            )

        st.session_state["identifier_completed_df"] = completed_df
        st.session_state["identifier_warnings_df"] = warnings_df
        st.session_state["identifier_completed_input_df"] = completed_df[
            ["compound", "smiles", "cas", "ec", "dtxsid", "echa_id"]
        ].copy()
        st.session_state["identifier_completion_notice"] = (
            f"标识符补全完成：{len(completed_df)} 行，提示 {len(warnings_df)} 条。"
        )
        st.rerun()

    notice = st.session_state.pop("identifier_completion_notice", None)
    if notice:
        st.success(notice)

    completed_df = st.session_state.get("identifier_completed_df")
    warnings_df = st.session_state.get("identifier_warnings_df")
    if completed_df is None:
        st.subheader("补全结果预览")
        show_dataframe(resolver_build_empty_completed_template(resolver_input_df))
    else:
        st.subheader("补全结果")
        show_dataframe(completed_df)
        st.download_button(
            label="下载标识符补全结果",
            data=resolver_build_result_workbook(resolver_input_df, completed_df, warnings_df),
            file_name="Identifier_Completion_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="resolver_download_in_tab",
        )

    if warnings_df is not None and not warnings_df.empty:
        with st.expander("查看补全提示和失败记录", expanded=False):
            show_dataframe(warnings_df)

with tab_epa:
    st.subheader("3. 查询用途类别")
    st.write(
        "系统会先把化合物匹配到 CompTox 的 DTXSID，再查询产品用途类别和化学功能用途。"
        "同一化合物有多个用途时，会按证据数量排序并取前五个。"
    )

    if not comptox_valid:
        st.error(comptox_message)

    st.info(
        "默认直接查询 CompTox Dashboard 的产品用途类别和化学功能用途。"
        "EPA 旧直连 API 已下线，因此默认结果不包含产品用途关键词。"
    )
    with st.expander("可选：配置自定义 EPA API", expanded=False):
        api_base = st.text_input(
            "自定义 EPA CompTox API 地址",
            value=COMPTOX_DEFAULT_API_BASE,
            placeholder="留空，使用 Dashboard 查询",
            help="仅在你已取得当前可用的 EPA API 地址时填写。留空不会请求旧 API 域名。",
        )
        api_key = st.text_input(
            "EPA API Key（可选）",
            value="",
            type="password",
            help="仅用于上方配置的自定义 API。",
        )

    col_timeout, col_delay, col_top = st.columns(3)
    with col_timeout:
        timeout_seconds = st.number_input("单个请求超时（秒）", min_value=10, max_value=180, value=45, step=5)
    with col_delay:
        delay_seconds = st.number_input("请求间隔（秒）", min_value=0.0, max_value=5.0, value=0.2, step=0.1)
    with col_top:
        top_n = st.number_input("保留用途数量", min_value=1, max_value=10, value=COMPTOX_TOP_N_DEFAULT, step=1)

    dashboard_fallback = True

    if st.button("开始查询用途", type="primary", disabled=not comptox_valid):
        progress_bar = st.progress(0)
        status_box = st.empty()

        def update_progress(done, total, compound):
            progress_bar.progress(done / total)
            status_box.info(f"正在处理：{compound} ({done}/{total})")

        with st.spinner("正在连接 CompTox，请等待..."):
            summary_df, candidates_df, errors_df = run_comptox_use_batch(
                comptox_input_df,
                api_base=api_base,
                api_key=api_key.strip() or None,
                timeout=int(timeout_seconds),
                delay_seconds=float(delay_seconds),
                top_n=int(top_n),
                dashboard_fallback=dashboard_fallback,
                progress_callback=update_progress,
            )

        st.session_state["comptox_use_summary"] = summary_df
        st.session_state["comptox_use_candidates"] = candidates_df
        st.session_state["comptox_use_errors"] = errors_df

        all_queries_completed = (
            not summary_df.empty
            and "query_status" in summary_df.columns
            and summary_df["query_status"].eq("查询完成").all()
        )
        if errors_df.empty:
            st.success("CompTox 用途查询完成。")
        elif all_queries_completed:
            st.success("CompTox 用途查询完成。")
            st.info(f"有 {len(errors_df)} 条查询提示，请查看失败记录后按需重试。")
        else:
            st.warning(f"查询完成，但有 {len(errors_df)} 条提示或失败记录。")

    summary_df = st.session_state.get("comptox_use_summary")
    candidates_df = st.session_state.get("comptox_use_candidates")
    errors_df = st.session_state.get("comptox_use_errors")

    if summary_df is None:
        st.subheader("结果预览")
        show_dataframe(comptox_build_empty_summary_template(comptox_input_df, top_n=COMPTOX_TOP_N_DEFAULT))
    else:
        st.subheader("前五用途结果")
        show_dataframe(summary_df)

    if candidates_df is not None and not candidates_df.empty:
        with st.expander("查看全部用途候选", expanded=False):
            show_dataframe(candidates_df)

    if errors_df is not None and not errors_df.empty:
        with st.expander("查看提示和失败记录", expanded=False):
            show_dataframe(errors_df)

with tab_echa:
    st.subheader("4. 查询 ECHA/REACH 注册用途证据")
    st.write(
        "系统会先匹配 ECHA CHEM 物质，再读取活跃 REACH dossier 中 3.5 用途和暴露信息目录，"
        "提取工业使用、专业使用、消费者使用、配制、制造和制品使用寿命等用途证据。"
    )

    if not echa_valid:
        st.error(echa_message)

    echa_base = st.text_input(
        "ECHA CHEM 地址",
        value=DEFAULT_ECHA_BASE,
        help="默认使用 ECHA CHEM 当前公开站点。",
    )

    col_timeout, col_delay, col_top, col_dossiers = st.columns(4)
    with col_timeout:
        echa_timeout_seconds = st.number_input(
            "ECHA 单个请求超时（秒）",
            min_value=30,
            max_value=300,
            value=90,
            step=10,
        )
    with col_delay:
        echa_delay_seconds = st.number_input(
            "ECHA 请求间隔（秒）",
            min_value=0.0,
            max_value=10.0,
            value=0.5,
            step=0.1,
        )
    with col_top:
        echa_top_n = st.number_input(
            "ECHA 保留用途数量",
            min_value=1,
            max_value=10,
            value=ECHA_TOP_N_DEFAULT,
            step=1,
        )
    with col_dossiers:
        max_dossiers = st.number_input(
            "每个化合物读取 dossier 数",
            min_value=1,
            max_value=5,
            value=1,
            step=1,
            help="数值越大证据越全，但 ECHA 查询会更慢。",
        )

    if st.button("开始 ECHA 查询用途", type="primary", disabled=not echa_valid):
        progress_bar = st.progress(0)
        status_box = st.empty()

        def update_echa_progress(done, total, compound):
            progress_bar.progress(done / total)
            status_box.info(f"正在处理：{compound} ({done}/{total})")

        with st.spinner("正在连接 ECHA CHEM，请等待..."):
            summary_df, candidates_df, dossiers_df, errors_df = run_echa_use_batch(
                echa_input_df,
                base_url=echa_base,
                timeout=int(echa_timeout_seconds),
                delay_seconds=float(echa_delay_seconds),
                top_n=int(echa_top_n),
                max_dossiers=int(max_dossiers),
                progress_callback=update_echa_progress,
            )

        st.session_state["echa_use_summary"] = summary_df
        st.session_state["echa_use_candidates"] = candidates_df
        st.session_state["echa_use_dossiers"] = dossiers_df
        st.session_state["echa_use_errors"] = errors_df

        all_queries_completed = (
            not summary_df.empty
            and "query_status" in summary_df.columns
            and summary_df["query_status"].eq("查询完成").all()
        )
        if errors_df.empty:
            st.success("ECHA 用途查询完成。")
        elif all_queries_completed:
            st.success("ECHA 用途查询完成。")
            st.info(f"有 {len(errors_df)} 条接口提示，但已成功提取用途结果。")
        else:
            st.warning(f"ECHA 查询完成，但有 {len(errors_df)} 条提示或失败记录。")

    echa_summary_df = st.session_state.get("echa_use_summary")
    echa_candidates_df = st.session_state.get("echa_use_candidates")
    echa_dossiers_df = st.session_state.get("echa_use_dossiers")
    echa_errors_df = st.session_state.get("echa_use_errors")

    if echa_summary_df is None:
        st.subheader("结果预览")
        show_dataframe(echa_build_empty_summary_template(echa_input_df, top_n=ECHA_TOP_N_DEFAULT))
    else:
        st.subheader("前五用途结果")
        show_dataframe(echa_summary_df)

    if echa_candidates_df is not None and not echa_candidates_df.empty:
        with st.expander("查看全部 ECHA 用途候选", expanded=False):
            show_dataframe(echa_candidates_df)

    if echa_dossiers_df is not None and not echa_dossiers_df.empty:
        with st.expander("查看已读取的 ECHA dossier", expanded=False):
            show_dataframe(echa_dossiers_df)

    if echa_errors_df is not None and not echa_errors_df.empty:
        with st.expander("查看 ECHA 提示和失败记录", expanded=False):
            show_dataframe(echa_errors_df)

with tab_rose:
    st.subheader("5. 用途风玫瑰图")
    st.write(
        "根据 EPA 或 ECHA 的 Top 用途结果绘图。每个化合物一个极坐标子图，"
        "每个用途扇区的角度按该用途证据数量占当前化合物 Top 用途证据总量的比例计算。"
    )

    rose_sources = {}
    comptox_summary_df = st.session_state.get("comptox_use_summary")
    if isinstance(comptox_summary_df, pd.DataFrame) and not comptox_summary_df.empty:
        rose_sources["EPA CompTox"] = {
            "source_label": "EPA",
            "summary_df": comptox_summary_df,
            "title": "EPA CompTox Use Rose Plot",
            "file_prefix": "EPA_Use_Rose_Plot",
        }

    echa_summary_df = st.session_state.get("echa_use_summary")
    if isinstance(echa_summary_df, pd.DataFrame) and not echa_summary_df.empty:
        rose_sources["ECHA REACH"] = {
            "source_label": "ECHA",
            "summary_df": echa_summary_df,
            "title": "ECHA REACH Use Rose Plot",
            "file_prefix": "ECHA_Use_Rose_Plot",
        }

    combined_rose_frames = []
    if isinstance(comptox_summary_df, pd.DataFrame) and not comptox_summary_df.empty:
        combined_rose_frames.append(extract_use_rose_data(comptox_summary_df, source_label="EPA"))
    if isinstance(echa_summary_df, pd.DataFrame) and not echa_summary_df.empty:
        combined_rose_frames.append(extract_use_rose_data(echa_summary_df, source_label="ECHA"))
    combined_rose_df = pd.concat(combined_rose_frames, ignore_index=True) if combined_rose_frames else pd.DataFrame()
    if not combined_rose_df.empty:
        rose_sources["EPA + ECHA Combined"] = {
            "source_label": "combined",
            "rose_df": combined_rose_df,
            "title": "EPA and ECHA Combined Use Plot",
            "file_prefix": "EPA_ECHA_Combined_Use_Plot",
        }

    if not rose_sources:
        st.info("请先完成 EPA CompTox 查询或 ECHA 查询。查询完成后，这里会显示用途风玫瑰图。")
    else:
        selected_source = st.radio(
            "选择图表数据来源",
            options=list(rose_sources.keys()),
            horizontal=True,
            key="use_rose_source",
        )
        source_config = rose_sources[selected_source]
        if source_config["source_label"] == "combined":
            rose_df = source_config["rose_df"]
        else:
            rose_df = extract_use_rose_data(
                source_config["summary_df"],
                source_label=source_config["source_label"],
            )

        if rose_df.empty:
            st.warning("当前结果中没有可用于绘图的用途数据。")
        else:
            if rose_df["angle_basis"].eq("equal_fallback").any():
                fallback_compounds = "、".join(
                    sorted(rose_df.loc[rose_df["angle_basis"].eq("equal_fallback"), "compound"].unique())
                )
                st.warning(
                    f"以下化合物缺少有效证据数量或证据总量为 0，已按用途数量等角度绘制：{fallback_compounds}"
                )

            with st.expander("查看图表数据", expanded=False):
                show_dataframe(
                    rose_df[
                        [
                            "source",
                            "compound_label",
                            "rank",
                            "use_label",
                            "evidence_count",
                            "angle_fraction",
                            "angle_basis",
                        ]
                    ]
                )

            if source_config["source_label"] == "combined":
                fig = generate_combined_use_rose_plot(rose_df, source_config["title"])
            else:
                fig = generate_use_rose_plot(rose_df, source_config["title"])
            st.pyplot(fig)

            png_buffer = figure_to_png_bytes(fig)
            pdf_buffer = figure_to_pdf_bytes(fig)
            plt.close(fig)

            col_png, col_pdf = st.columns(2)
            with col_png:
                st.download_button(
                    label="下载 PNG",
                    data=png_buffer,
                    file_name=f"{source_config['file_prefix']}.png",
                    mime="image/png",
                    key=f"rose_download_png_{source_config['source_label']}",
                )
            with col_pdf:
                st.download_button(
                    label="下载 PDF",
                    data=pdf_buffer,
                    file_name=f"{source_config['file_prefix']}.pdf",
                    mime="application/pdf",
                    key=f"rose_download_pdf_{source_config['source_label']}",
                )

with tab_output:
    st.subheader("6. 下载结果工作簿")
    completed_df = st.session_state.get("identifier_completed_df")
    identifier_warnings_df = st.session_state.get("identifier_warnings_df")

    identifier_workbook_buffer = resolver_build_result_workbook(
        resolver_input_df,
        completed_df=completed_df,
        warnings_df=identifier_warnings_df,
    )

    st.download_button(
        label="下载标识符补全结果",
        data=identifier_workbook_buffer,
        file_name="Identifier_Completion_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    summary_df = st.session_state.get("comptox_use_summary")
    candidates_df = st.session_state.get("comptox_use_candidates")
    errors_df = st.session_state.get("comptox_use_errors")

    workbook_buffer = comptox_build_result_workbook(
        comptox_input_df,
        summary_df=summary_df,
        candidates_df=candidates_df,
        errors_df=errors_df,
    )

    st.download_button(
        label="下载 CompTox 用途查询结果",
        data=workbook_buffer,
        file_name="CompTox_Use_Category_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    echa_summary_df = st.session_state.get("echa_use_summary")
    echa_candidates_df = st.session_state.get("echa_use_candidates")
    echa_dossiers_df = st.session_state.get("echa_use_dossiers")
    echa_errors_df = st.session_state.get("echa_use_errors")

    echa_workbook_buffer = echa_build_result_workbook(
        echa_input_df,
        summary_df=echa_summary_df,
        candidates_df=echa_candidates_df,
        dossiers_df=echa_dossiers_df,
        errors_df=echa_errors_df,
    )

    st.download_button(
        label="下载 ECHA 用途查询结果",
        data=echa_workbook_buffer,
        file_name="ECHA_REACH_Use_Evidence_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

with tab_notes:
    st.subheader("查询逻辑")
    st.markdown(
        "\n".join(
            [
                "标识符补全：",
                "1. 优先保留输入表中已有的 CAS、EC、DTXSID 和 ECHA ID。",
                "2. 使用 PubChem 优先从 CAS、没有 CAS 时从 SMILES 匹配 CID、SMILES、名称、EC 和 DTXSID。",
                "3. 使用 EPA 尝试从 compound、CAS 或 SMILES 匹配 DTXSID 和 CAS。",
                "4. 使用 ECHA 按 ECHA ID、EC、CAS、SMILES、名称顺序匹配 ECHA ID 和 EC。",
                "5. 补全完成后，EPA/ECHA 查询会自动使用补全后的标识符表。",
                "",
                "EPA 查询：",
                "1. 优先使用输入表中的 `dtxsid`，否则用 CAS、compound、SMILES 去 CompTox 匹配。",
                "2. 查询产品用途类别、产品用途关键词和化学功能用途。",
                "3. 将英文用途映射为中文类别，例如个人护理用品、化学品中间体、增塑剂、农药等。",
                "4. 对同一中文类别合并证据数量，按证据数量排序，只在主表保留前五个用途。",
                "5. 完整候选用途会保存在下载文件的 `All_Use_Candidates` 工作表中。",
                "",
                "ECHA 查询：",
                "ECHA 查询会读取 ECHA CHEM 的物质匹配结果和 REACH dossier。结果中的中文用途类别来自 dossier 目录里的用途描述，",
                "下载文件会保留原始英文用途、dossier 编号和记录链接，便于后续人工核对。",
            ]
        )
    )
    st.link_button("打开 CompTox Dashboard", DEFAULT_DASHBOARD_BASE)
    st.link_button("打开 ECHA CHEM", DEFAULT_ECHA_BASE)
