import streamlit as st


st.set_page_config(
    page_title="ChemPriority 污染物综合筛选与评估平台",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ChemPriority 污染物综合筛选与评估平台")
st.caption("面向污染物优先控制筛选的数据获取、用途识别、ToxPi 计算与环境归趋预测工具。")
st.markdown("---")

entry_tab, data_tab, note_tab = st.tabs(["功能入口", "数据格式", "部署说明"])

with entry_tab:
    st.subheader("六个功能模块")
    col_workflow, col_admet, col_toxpi, col_epi, col_use, col_auto = st.columns(6)

    with col_workflow:
        st.markdown("### 1. 综合筛查流程")
        st.write(
            "一次上传多个 CD 导出的样品 Excel，串联化学分类、DF、EPI Suite、Pov-LRTP、"
            "PBM 分数和 PA/PBM/DF ToxPi 排序。"
        )
        st.info("从左侧 App 后第一个页面进入“综合筛查流程”。")

    with col_admet:
        st.markdown("### 2. ADMETlab 毒性数据获取")
        st.write(
            "上传包含化合物名称和 SMILES 的 Excel 文件。ADMETlab 自动连接路线暂不启用，"
            "当前页面用于整理和校验后续批量提交清单。"
        )
        st.info("从左侧 App 后第二个页面进入“ADMETlab毒性数据获取”。")

    with col_toxpi:
        st.markdown("### 3. ToxPi 毒性评估")
        st.write(
            "上传实际毒性指标数据后自动识别数值型指标列，可选择本次纳入计算的指标，并完成归一化、加权评分、"
            "图表生成和排序稳定性分析。"
        )
        st.info("从左侧 App 后第三个页面进入“ToxPi毒性评估”。")

    with col_epi:
        st.markdown("### 4. EPI Suite 环境归趋")
        st.write(
            "通过 EPI Web Suite 网页端 API 计算物化性质、降解、生物富集和环境介质分配等指标。"
        )
        st.info("从左侧 App 后第四个页面进入“EPISuite环境归趋”。")

    with col_use:
        st.markdown("### 5. EPA/ECHA 用途查询")
        st.write(
            "上传化合物表格，连接 EPA CompTox Dashboard 和 ECHA CHEM 查询用途证据，"
            "可先补全 CAS、DTXSID、EC 和 ECHA ID，再按证据强度排序并提取前五个用途。"
        )
        st.info("从左侧 App 后第五个页面进入“化合物用途查询”。")

    with col_auto:
        st.markdown("### 6. 一键批量查询")
        st.write(
            "上传统一格式 Excel，勾选需要运行的项目后，按依赖顺序自动执行标识符补全、"
            "EPI、EPA/ECHA、来源属性和 ToxPi 等步骤。"
        )
        st.info("从左侧 App 后第六个页面进入“一键批量查询”。")

    st.markdown("---")
    st.metric("当前已隔离模块", "6 个")

with data_tab:
    st.subheader("综合筛查流程输入表格")
    st.write("可一次上传多个样品 Excel。默认使用 `Name`、`formula` 和 `Group_Area`，DF 按不同上传文件中的检出情况计算。")
    st.code("Name\nformula\nGroup_Area\n...可选 CAS/SMILES 和样品峰面积列", language="text")

    st.subheader("ADMETlab 输入表格")
    st.write("Excel 文件建议包含以下两列。")
    st.code("compound\nsmiles", language="text")

    st.subheader("ToxPi 输入表格")
    st.write("Excel 文件需要包含一列 `compound`，以及至少 1 个可转为数字的毒性指标列。系统会先识别候选指标，再由用户选择本次纳入 ToxPi 计算的指标。")
    st.code(
        "\n".join(
            [
                "compound",
                "carcinogenicity",
                "DILI",
                "genotoxicity",
                "hERG",
                "...其他数值型毒性指标列",
            ]
        ),
        language="text",
    )
    st.write(
        "原来的 15 个毒性指标仍然兼容；如果 Excel 里只有其中一部分，或有新的数值型毒性项目，也可以直接计算。"
    )

    st.subheader("EPI Suite 输入表格")
    st.write("建议复用 `compound` 和 `smiles` 两列。")

    st.subheader("EPA/ECHA 用途查询输入表格")
    st.write("只有 `smiles` 时可以先做标识符补全；EPA 建议包含 `compound`、`cas`、`smiles`、`dtxsid`；ECHA 建议包含 `compound`、`ec`、`cas`、`smiles`、`echa_id`。")
    st.code("compound\ncas\nec\nsmiles\ndtxsid\necha_id", language="text")

    st.subheader("一键批量查询输入表格")
    st.write("统一格式 Excel 默认识别 `Name`、`NIST Lib Hit Formula`、`Avg TIC` 和所有 `Group Area` 列。")
    st.code("Name\nNIST Lib Hit Formula\nAvg TIC\nGroup Area: sample-1\nGroup Area: sample-2", language="text")

with note_tab:
    st.subheader("线上使用方式")
    st.write(
        "ChemPriority 按 Streamlit 网页部署设计。六个模块保持页面隔离：ADMETlab 数据整理、ToxPi 算分、"
        "EPI Suite 环境归趋预测、EPA/ECHA 用途查询、综合筛查流程和一键批量查询分别维护，后续扩展时不需要改动原有 ToxPi 页面。"
    )
    st.write("部署前建议使用项目根目录的 `requirements.txt` 安装依赖，并确认服务器可以访问 EPA 和 ECHA 相关网页及接口。")
