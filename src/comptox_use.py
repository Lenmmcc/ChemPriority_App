import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import pandas as pd

from src.batch_runner import run_ordered_batch
from src.query_cache import cached_call


REQUIRED_IDENTIFIER_COLUMNS = ["compound", "cas", "smiles", "dtxsid"]

# EPA's former public API hostname (api-ccte.epa.gov) no longer has a public
# DNS record.  Keep an explicit override for deployments that have a supported
# private/current API endpoint, but use the publicly reachable Dashboard by
# default.
DEFAULT_API_BASE = os.environ.get("COMPTOX_API_BASE", "").strip()
DEFAULT_DASHBOARD_BASE = "https://comptox.epa.gov/dashboard/"
DASHBOARD_REQUEST_ATTEMPTS = 3
DASHBOARD_RETRY_DELAY_SECONDS = 1.0

DASHBOARD_ONLY_QUERY_NOTE = (
    "未配置可用的 EPA API：结果来自 CompTox Dashboard 的产品用途类别和化学功能用途；"
    "不包含产品用途关键词。"
)

# This key is published in the CompTox Dashboard frontend bundle. Deployments can
# override it with COMPTOX_API_KEY if EPA changes access requirements.
DEFAULT_COMPTOX_API_KEY = os.environ.get(
    "COMPTOX_API_KEY", "546aa80b-f05c-4126-a303-18dd66eabb9d"
)

PRODUCT_USE_ENDPOINT = "ccdapp2/product-cat-puc/search/by-dtxsid"
PRODUCT_KEYWORD_ENDPOINT = "ccdapp2/product-cat-keyword/search/by-dtxsid"
FUNCTIONAL_USE_ENDPOINT = "ccdapp2/exposure-chemical-func-use/search/by-dtxsid"
CHEMICAL_SEARCH_ENDPOINT = "ccdapp1/search/chemical/equal-with-detail/"

DTXSID_RE = re.compile(r"\bDTXSID\d+\b", re.I)

USE_SOURCE_LABELS = {
    "product_category": "产品用途类别",
    "product_keyword": "产品用途关键词",
    "functional_use": "化学功能用途",
}

PRODUCT_USE_TABLE_COLUMNS = [
    "compound",
    "dtxsid",
    "产品用途类别",
    "英文产品用途类别",
    "general_category",
    "product_family",
    "product_type",
    "product_count",
    "description",
    "source",
    "CompTox产品用途链接",
]

FUNCTIONAL_USE_TABLE_COLUMNS = [
    "compound",
    "dtxsid",
    "功能用途",
    "英文功能用途",
    "来源类型",
    "预测概率",
    "reported_use",
    "harmonized_use",
    "evidence_count",
    "source",
    "CompTox功能用途链接",
]

WARNING_COLUMNS = [
    "compound",
    "cas",
    "smiles",
    "dtxsid",
    "query_source",
    "query_value",
    "stage",
    "message",
]

EVIDENCE_METADATA_COLUMNS = [
    "source_type",
    "中文名称",
    "EPA术语",
    "说明",
    "证据数量含义",
    "默认来源",
    "官方链接",
]

EVIDENCE_METADATA_ROWS = [
    {
        "source_type": "product_category",
        "中文名称": "产品用途类别",
        "EPA术语": "Product Use Category (PUC)",
        "说明": "描述化学物质出现在哪类产品或产品场景中，不说明其在配方中的技术功能。",
        "证据数量含义": "Dashboard/API 返回的 productCount、product_count 或 count；表示关联产品记录数量。",
        "默认来源": "CompTox Dashboard product-use-categories",
        "官方链接": "https://comptox.epa.gov/chemexpo/static/user_guide/data_overview.html",
    },
    {
        "source_type": "functional_use",
        "中文名称": "化学功能用途",
        "EPA术语": "Chemical Functional Use / Function Category (FC)",
        "说明": "描述化学物质在产品或工艺中的技术功能，例如溶剂、芳香剂、增塑剂；图表默认只使用 predicted 结果，reported 仅保留在明细表中。",
        "证据数量含义": "predicted 记录为预测概率；reported 记录为是否存在明确报道，展示为 1，不与预测概率相加。",
        "默认来源": "CompTox Dashboard chemical-functional-use",
        "官方链接": "https://comptox.epa.gov/chemexpo/static/user_guide/glossary.html",
    },
    {
        "source_type": "product_keyword",
        "中文名称": "产品用途关键词",
        "EPA术语": "Chemical List Presence Keyword",
        "说明": "来自清单或文本的宽泛用途关键词；默认 Dashboard 模式不返回该类证据。",
        "证据数量含义": "当前实现按单条关键词计数；与 PUC 或 FC 不同量纲。",
        "默认来源": "仅在配置可用自定义 EPA API 时可能返回",
        "官方链接": "https://www.epa.gov/chemical-research/chemical-and-products-database-cpdat",
    },
]

USE_TRANSLATION_RULES = [
    (("personal care", "cosmetic", "beauty", "skin care", "hair care", "toiletries"), "个人护理用品"),
    (("chemical intermediate", "intermediate", "intermediates"), "化学品中间体"),
    (("plasticizer", "phthalate"), "增塑剂"),
    (("uv absorber", "ultraviolet absorber", "sunscreen", "light stabilizer"), "紫外线吸收剂"),
    (("pesticide", "insecticide", "herbicide", "fungicide", "biocide"), "农药"),
    (("polycyclic aromatic", "polycyclic aromatic hydrocarbon", "pah"), "多环芳烃及其类似物"),
    (("pharmaceutical", "medicine", "drug", "therapeutic"), "医药用品"),
    (("fragrance", "perfume", "scent"), "芳香剂"),
    (("flavorant", "flavourant", "flavor", "flavour"), "调味剂"),
    (("antioxidant",), "抗氧化剂"),
    (("antimicrobial", "antibacterial", "antifungal"), "抗微生物剂"),
    (("skin protectant", "skin protector"), "皮肤保护剂"),
    (("skin conditioner", "skin conditioning"), "皮肤调理剂"),
    (("hardener", "curing agent"), "固化剂"),
    (("processing aid",), "加工助剂"),
    (("additive",), "添加剂"),
    (("flame retardant", "fire retardant"), "阻燃剂"),
    (("solvent",), "溶剂"),
    (("surfactant", "detergent"), "表面活性剂"),
    (("lubricating", "lubricant"), "润滑剂"),
    (("adhesive", "sealant", "binder"), "胶黏剂"),
    (("dye", "pigment", "colorant"), "染料/颜料"),
    (("cleaning", "cleaner", "disinfectant"), "清洁用品"),
    (("industrial product",), "工业用品"),
    (("construction", "building material"), "建筑材料"),
    (("paint", "coating", "stain"), "涂料/油漆"),
    (("medical", "dental"), "医疗/牙科用品"),
    (("furniture", "furnishing"), "家具用品"),
    (("food", "beverage"), "食品相关"),
    (("home maintenance", "household"), "家庭维护用品"),
    (("auto", "automotive"), "汽车用品"),
    (("arts", "crafts", "office"), "文具/办公用品"),
    (("monomer", "polymer"), "聚合物相关原料"),
    (("catalyst",), "催化剂"),
    (("hydraulic fluid",), "液压流体"),
]

GENERIC_USE_EXACT = {
    "not yet categorized",
    "not categorized",
    "uncategorized",
    "unknown",
    "raw materials",
}

GENERIC_USE_PATTERNS = (
    "not yet categorized",
    "not categorized",
    "uncategorized",
    "no data",
    "not specified",
)


def make_template_file():
    template_df = pd.DataFrame(
        {
            "compound": ["Bisphenol A", "Benzophenone", "Diphenylamine"],
            "cas": ["80-05-7", "119-61-9", "122-39-4"],
            "smiles": [
                "CC(C)(c1ccc(O)cc1)c1ccc(O)cc1",
                "O=C(c1ccccc1)c1ccccc1",
                "c1ccc(Nc2ccccc2)cc1",
            ],
            "dtxsid": ["DTXSID7020182", "DTXSID0021961", "DTXSID4021975"],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        template_df.to_excel(writer, sheet_name="CompTox_Input", index=False)
    buffer.seek(0)
    return buffer


def normalize_input_columns(df):
    normalized = df.copy()
    normalized.columns = [str(col).strip() for col in normalized.columns]

    rename_map = {}
    for col in normalized.columns:
        key = _normalize_key(col)
        if key in {
            "compound",
            "name",
            "compoundname",
            "chemical",
            "chemicalname",
            "化合物",
            "化合物名称",
            "名称",
            "物质名称",
            "污染物",
            "污染物名称",
        }:
            rename_map[col] = "compound"
        elif key in {"cas", "casrn", "casno", "casnumber", "cas号", "cas编号", "cas号码"}:
            rename_map[col] = "cas"
        elif key in {"smiles", "canonicalsmiles", "isomericsmiles", "结构式"}:
            rename_map[col] = "smiles"
        elif key in {"dtxsid", "dsstoxsubstanceid", "comptoxid", "comptoxid"}:
            rename_map[col] = "dtxsid"

    normalized = normalized.rename(columns=rename_map)
    for col in REQUIRED_IDENTIFIER_COLUMNS:
        if col not in normalized.columns:
            normalized[col] = pd.NA
    return normalized


def validate_input(df):
    available = [col for col in REQUIRED_IDENTIFIER_COLUMNS if col in df.columns]
    if not available:
        return False, "表格至少需要包含 compound、cas、smiles 或 dtxsid 中的一列。"

    usable_rows = df[REQUIRED_IDENTIFIER_COLUMNS].notna().any(axis=1).sum()
    if usable_rows == 0:
        return False, "没有可用于查询的化合物标识。"

    return True, f"输入数据检查通过，共 {usable_rows} 个可查询化合物。"


def build_use_query_variants(row) -> list[dict]:
    """Build independent name, SMILES, or identifier queries for one source row."""
    source = dict(row)
    compound = _clean_cell(source.get("compound"))
    smiles = _clean_cell(source.get("smiles"))

    def variant(query_source, query_value, **overrides):
        query = dict(source)
        query.update(overrides)
        query["query_source"] = query_source
        query["query_value"] = query_value
        return query

    if compound and smiles:
        return [
            variant("名称", compound, smiles="", cas="", dtxsid=""),
            variant("SMILES", smiles, compound="", cas="", dtxsid=""),
        ]

    for field in ("dtxsid", "cas"):
        value = _clean_cell(source.get(field))
        if value:
            return [variant("输入标识", value)]
    if compound:
        return [variant("名称", compound)]
    if smiles:
        return [variant("SMILES", smiles)]
    return [variant("输入标识", "")]


def _variant_identity_conflict(outcomes, identity_field):
    """Return whether independent name and SMILES resolutions identify different entities."""
    identities = {
        outcome["query_source"]: _clean_cell(outcome["resolution"].get(identity_field))
        for outcome in outcomes
    }
    name_identity = identities.get("名称", "")
    smiles_identity = identities.get("SMILES", "")
    return bool(name_identity and smiles_identity and name_identity != smiles_identity)


def run_comptox_use_batch(
    input_df,
    api_base=DEFAULT_API_BASE,
    api_key=None,
    timeout=45,
    delay_seconds=0.2,
    dashboard_fallback=True,
    progress_callback=None,
):
    clean_df = normalize_input_columns(input_df)
    summary_rows = []
    candidate_rows = []
    error_rows = []
    total = len(clean_df)
    query_note = _query_scope_note(api_base, dashboard_fallback)

    for pos, (_, row) in enumerate(clean_df.iterrows(), start=1):
        compound = _display_compound(row)
        outcomes = []
        for variant in build_use_query_variants(row):
            query_source = variant["query_source"]
            query_value = variant["query_value"]
            query_row = pd.Series(variant)
            try:
                resolution = resolve_dtxsid(
                    query_row,
                    api_base=api_base,
                    api_key=api_key,
                    timeout=timeout,
                )
                dtxsid = resolution.get("dtxsid")
                if _is_missing(dtxsid) or not _clean_cell(dtxsid):
                    outcomes.append(
                        {
                            "query_source": query_source,
                            "query_value": query_value,
                            "resolution": resolution,
                            "dtxsid": dtxsid,
                            "candidates": [],
                            "warnings": [
                                {
                                    "stage": "identifier_resolution",
                                    "message": resolution.get("message", "CompTox 未返回可用 DTXSID。"),
                                }
                            ],
                            "status": "未解析到 DTXSID",
                        }
                    )
                    continue

                candidates, warnings = fetch_use_candidates(
                    dtxsid,
                    api_base=api_base,
                    api_key=api_key,
                    timeout=timeout,
                    dashboard_fallback=dashboard_fallback,
                )
                outcomes.append(
                    {
                        "query_source": query_source,
                        "query_value": query_value,
                        "resolution": resolution,
                        "dtxsid": dtxsid,
                        "candidates": candidates,
                        "warnings": warnings,
                        "status": "查询完成" if candidates else "未查到用途数据",
                    }
                )
            except Exception as exc:
                outcomes.append(
                    {
                        "query_source": query_source,
                        "query_value": query_value,
                        "resolution": {"dtxsid": pd.NA, "status": "失败"},
                        "dtxsid": pd.NA,
                        "candidates": [],
                        "warnings": [{"stage": "unexpected_error", "message": str(exc)}],
                        "status": "查询失败",
                    }
                )

        identity_conflict = _variant_identity_conflict(outcomes, "dtxsid")
        for outcome in outcomes:
            is_primary_identity = bool(
                _clean_cell(outcome["dtxsid"])
                and (not identity_conflict or outcome["query_source"] == "SMILES")
            )
            summary_rows.append(
                _summary_row(
                    row,
                    outcome["resolution"],
                    outcome["candidates"],
                    outcome["status"],
                    query_note=query_note,
                    query_source=outcome["query_source"],
                    query_value=outcome["query_value"],
                    is_primary_identity=is_primary_identity,
                )
            )
            for candidate in outcome["candidates"]:
                candidate_rows.append(
                    {
                        "compound": compound,
                        "dtxsid": outcome["dtxsid"],
                        **candidate,
                        "query_source": outcome["query_source"],
                        "query_value": outcome["query_value"],
                        "is_primary_identity": is_primary_identity,
                    }
                )
            for warning in outcome["warnings"]:
                error_rows.append(
                    {
                        "compound": compound,
                        "cas": _clean_cell(row.get("cas")),
                        "smiles": _clean_cell(row.get("smiles")),
                        "dtxsid": _clean_cell(outcome["dtxsid"]),
                        "query_source": outcome["query_source"],
                        "query_value": outcome["query_value"],
                        "stage": warning.get("stage", "use_query"),
                        "message": warning.get("message", ""),
                    }
                )
        if identity_conflict:
            error_rows.append(
                {
                    "compound": compound,
                    "cas": _clean_cell(row.get("cas")),
                    "smiles": _clean_cell(row.get("smiles")),
                    "dtxsid": "",
                    "query_source": "名称 | SMILES",
                    "query_value": " | ".join(
                        outcome["query_value"]
                        for outcome in outcomes
                        if outcome["query_source"] in {"名称", "SMILES"}
                    ),
                    "stage": "identity_conflict",
                    "message": "名称与 SMILES 命中不同 DTXSID，已保留两组用途证据并将 SMILES 标为主身份。",
                }
            )

        if progress_callback:
            progress_callback(pos, total, compound)
        if delay_seconds and pos < total:
            time.sleep(delay_seconds)

    summary_df = pd.DataFrame(summary_rows)
    candidates_df = pd.DataFrame(candidate_rows)
    errors_df = pd.DataFrame(error_rows, columns=WARNING_COLUMNS)
    return summary_df, candidates_df, errors_df


_run_comptox_use_batch_sequential = run_comptox_use_batch


def run_comptox_use_batch(
    input_df,
    api_base=DEFAULT_API_BASE,
    api_key=None,
    timeout=45,
    delay_seconds=0.2,
    dashboard_fallback=True,
    progress_callback=None,
    max_workers=1,
    cache_enabled=True,
):
    if int(max_workers or 1) <= 1:
        from src.query_cache import cache_control

        with cache_control(cache_enabled):
            return _run_comptox_use_batch_sequential(
                input_df,
                api_base=api_base,
                api_key=api_key,
                timeout=timeout,
                delay_seconds=delay_seconds,
                dashboard_fallback=dashboard_fallback,
                progress_callback=progress_callback,
            )

    clean_df = normalize_input_columns(input_df)
    items = list(clean_df.iterrows())
    query_note = _query_scope_note(api_base, dashboard_fallback)

    def process_row(item):
        _, row = item
        return _run_comptox_use_batch_sequential(
            pd.DataFrame([row]),
            api_base=api_base,
            api_key=api_key,
            timeout=timeout,
            delay_seconds=0,
            dashboard_fallback=dashboard_fallback,
            progress_callback=None,
        )

    from src.query_cache import cache_control

    with cache_control(cache_enabled):
        batch_results = run_ordered_batch(
            items,
            process_row,
            max_workers=max_workers,
            delay_seconds=delay_seconds,
            progress_callback=progress_callback,
            label_func=lambda item: _display_compound(item[1]),
        )

    summary_frames = []
    candidate_frames = []
    error_frames = []
    for result in batch_results:
        if result.error is not None:
            row = items[result.index][1]
            compound = _display_compound(row)
            variant = build_use_query_variants(row)[0]
            summary_frames.append(
                pd.DataFrame([
                    _summary_row(
                        row,
                        {"dtxsid": pd.NA, "status": "失败"},
                        [],
                        "查询失败",
                        query_note=query_note,
                        query_source=variant["query_source"],
                        query_value=variant["query_value"],
                    )
                ])
            )
            error_frames.append(
                pd.DataFrame([
                    {
                        "compound": compound,
                        "cas": _clean_cell(row.get("cas")),
                        "smiles": _clean_cell(row.get("smiles")),
                        "dtxsid": _clean_cell(row.get("dtxsid")),
                        "query_source": variant["query_source"],
                        "query_value": variant["query_value"],
                        "stage": "batch_worker",
                        "message": str(result.error),
                    }
                ])
            )
            continue
        summary_df, candidates_df, errors_df = result.value
        summary_frames.append(summary_df)
        candidate_frames.append(candidates_df)
        error_frames.append(errors_df)

    summary = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    candidates = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    errors = pd.concat(error_frames, ignore_index=True) if error_frames else pd.DataFrame()
    return summary, candidates, errors


def resolve_dtxsid(row, api_base=DEFAULT_API_BASE, api_key=None, timeout=45):
    provided = _clean_cell(row.get("dtxsid"))
    if provided:
        match = DTXSID_RE.search(provided)
        if match:
            return {
                "dtxsid": match.group(0).upper(),
                "matched_name": _clean_cell(row.get("compound")),
                "matched_cas": _clean_cell(row.get("cas")),
                "status": "使用输入 DTXSID",
                "message": "",
            }

    for value in (row.get("compound"), row.get("cas"), row.get("smiles")):
        text = _clean_cell(value)
        if text:
            match = DTXSID_RE.search(text)
            if match:
                return {
                    "dtxsid": match.group(0).upper(),
                    "matched_name": _clean_cell(row.get("compound")),
                    "matched_cas": _clean_cell(row.get("cas")),
                    "status": "从输入文本识别 DTXSID",
                    "message": "",
                }

    search_terms = [
        ("cas", _clean_cell(row.get("cas"))),
        ("compound", _clean_cell(row.get("compound"))),
        ("smiles", _clean_cell(row.get("smiles"))),
    ]
    failures = []
    api_enabled = _api_is_configured(api_base)
    for term_type, term in search_terms:
        if not term:
            continue
        if api_enabled:
            try:
                data = _api_get_json(
                    CHEMICAL_SEARCH_ENDPOINT + urllib.parse.quote(term, safe=""),
                    api_base=api_base,
                    api_key=api_key,
                    timeout=timeout,
                )
                candidates = _extract_chemical_candidates(data)
                chosen = _choose_best_identifier_match(candidates, term, term_type)
                if chosen:
                    return {
                        "dtxsid": _get_any(chosen, ["dtxsid", "dsstoxSubstanceId"]),
                        "matched_name": _get_any(chosen, ["preferredName", "name", "label"]),
                        "matched_cas": _get_any(chosen, ["casrn", "cas", "casNumber"]),
                        "status": f"通过 {term_type} 匹配",
                        "message": "",
                    }
            except Exception as exc:
                failures.append(f"{term_type}: {exc}")

        try:
            candidates = _dashboard_search_chemical_candidates(term, timeout=timeout)
            chosen = _choose_best_identifier_match(candidates, term, term_type)
            if chosen:
                return {
                    "dtxsid": _get_any(chosen, ["dtxsid", "dsstoxSubstanceId"]),
                    "matched_name": _get_any(chosen, ["preferredName", "name", "label"]),
                    "matched_cas": _get_any(chosen, ["casrn", "cas", "casNumber"]),
                    "status": f"通过 Dashboard {term_type} 匹配",
                    "message": "",
                }
        except Exception as exc:
            failures.append(f"dashboard {term_type}: {exc}")

    message = "；".join(failures) if failures else "没有可用查询词。"
    return {
        "dtxsid": pd.NA,
        "matched_name": pd.NA,
        "matched_cas": pd.NA,
        "status": "未解析",
        "message": message,
    }


def fetch_use_candidates(
    dtxsid,
    api_base=DEFAULT_API_BASE,
    api_key=None,
    timeout=45,
    dashboard_fallback=True,
):
    candidates = []
    warnings = []

    api_calls = [
        (
            "product_category",
            PRODUCT_USE_ENDPOINT,
            _extract_product_category_candidates,
        ),
        (
            "product_keyword",
            PRODUCT_KEYWORD_ENDPOINT,
            _extract_product_keyword_candidates,
        ),
        (
            "functional_use",
            FUNCTIONAL_USE_ENDPOINT,
            _extract_functional_use_candidates,
        ),
    ]

    if _api_is_configured(api_base):
        for source_type, endpoint, extractor in api_calls:
            try:
                data = _api_get_json(
                    endpoint,
                    params={"id": dtxsid},
                    api_base=api_base,
                    api_key=api_key,
                    timeout=timeout,
                )
                candidates.extend(extractor(data, source=f"api:{source_type}"))
            except Exception as exc:
                warnings.append(
                    {
                        "stage": f"api:{source_type}",
                        "message": str(exc),
                    }
                )

    if dashboard_fallback:
        if not any(item["source_type"] == "product_category" for item in candidates):
            try:
                html = _dashboard_get_html(
                    f"chemical/product-use-categories/{dtxsid}",
                    timeout=timeout,
                )
                candidates.extend(_extract_dashboard_product_categories(html))
            except Exception as exc:
                warnings.append(
                    {
                        "stage": "dashboard:product_category",
                        "message": str(exc),
                    }
                )

        if not any(item["source_type"] == "functional_use" for item in candidates):
            try:
                html = _dashboard_get_html(
                    f"chemical/chemical-functional-use/{dtxsid}",
                    timeout=timeout,
                )
                candidates.extend(_extract_dashboard_functional_uses(html))
            except Exception as exc:
                warnings.append(
                    {
                        "stage": "dashboard:functional_use",
                        "message": str(exc),
                    }
                )

    return candidates, warnings


def build_result_workbook(input_df, summary_df=None, candidates_df=None, errors_df=None):
    if summary_df is None:
        summary_df = pd.DataFrame()
    if candidates_df is None:
        candidates_df = pd.DataFrame()
    if errors_df is None:
        errors_df = pd.DataFrame()
    if errors_df.empty:
        errors_df = pd.DataFrame(columns=WARNING_COLUMNS)
    else:
        for column in WARNING_COLUMNS:
            if column not in errors_df.columns:
                errors_df[column] = pd.NA
        errors_df = errors_df[WARNING_COLUMNS]

    mapping_df = pd.DataFrame(
        [
            {"英文关键词": " / ".join(keywords), "中文类别": label}
            for keywords, label in USE_TRANSLATION_RULES
        ]
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        normalize_input_columns(input_df)[REQUIRED_IDENTIFIER_COLUMNS].to_excel(
            writer, sheet_name="Input", index=False
        )
        summary_df.to_excel(writer, sheet_name="Use_Summary", index=False)
        build_product_use_table(candidates_df).to_excel(writer, sheet_name="Product_Use_Categories", index=False)
        build_functional_use_table(candidates_df, functional_source="predicted").to_excel(
            writer,
            sheet_name="Functional_Uses_Predicted",
            index=False,
        )
        build_functional_use_table(candidates_df, functional_source="reported").to_excel(
            writer,
            sheet_name="Functional_Uses_Reported",
            index=False,
        )
        candidates_df.to_excel(writer, sheet_name="All_Use_Candidates", index=False)
        errors_df.to_excel(writer, sheet_name="Warnings", index=False)
        build_evidence_metadata_table().to_excel(writer, sheet_name="Evidence_Metadata", index=False)
        mapping_df.to_excel(writer, sheet_name="CN_Mapping", index=False)
    buffer.seek(0)
    return buffer


def build_empty_summary_template(input_df):
    clean_df = normalize_input_columns(input_df)
    return pd.DataFrame(
        [
            _summary_row(row, {"dtxsid": pd.NA, "status": "待查询"}, [], "待查询")
            for _, row in clean_df.iterrows()
        ]
    )


def _summary_row(
    row,
    resolution,
    candidates,
    status,
    query_note="",
    query_source="",
    query_value="",
    is_primary_identity=False,
):
    output = {
        "compound": _display_compound(row),
        "cas": _clean_cell(row.get("cas")),
        "smiles": _clean_cell(row.get("smiles")),
        "input_dtxsid": _clean_cell(row.get("dtxsid")),
        "query_source": query_source,
        "query_value": query_value,
        "is_primary_identity": is_primary_identity,
        "matched_dtxsid": resolution.get("dtxsid", pd.NA),
        "matched_name": resolution.get("matched_name", pd.NA),
        "matched_cas": resolution.get("matched_cas", pd.NA),
        "match_status": resolution.get("status", pd.NA),
        "query_status": status,
        "query_notes": query_note,
        "产品用途类别": _format_source_type_uses(candidates, "product_category"),
        "已收集化学功能用途": _format_source_type_uses(
            candidates,
            "functional_use",
            functional_source="reported",
        ),
        "预测化学功能用途": _format_source_type_uses(
            candidates,
            "functional_use",
            functional_source="predicted",
        ),
    }
    dtxsid = resolution.get("dtxsid")
    if isinstance(dtxsid, str) and DTXSID_RE.search(dtxsid):
        product_url = urllib.parse.urljoin(DEFAULT_DASHBOARD_BASE, f"chemical/product-use-categories/{dtxsid}")
        functional_url = urllib.parse.urljoin(DEFAULT_DASHBOARD_BASE, f"chemical/chemical-functional-use/{dtxsid}")
        output["CompTox来源链接"] = (
            f"产品用途类别: {product_url}\n"
            f"化学功能用途: {functional_url}"
        )
    else:
        output["CompTox来源链接"] = pd.NA
    output["notes"] = resolution.get("message", "")
    return output


def _format_evidence_count(value):
    number = _to_number(value)
    if pd.isna(number):
        return "0"
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def _format_source_type_uses(candidates, source_type, functional_source=None):
    grouped = {}
    for candidate in candidates or []:
        if candidate.get("source_type") != source_type:
            continue
        if source_type == "functional_use" and functional_source:
            is_predicted = _is_predicted_functional_use(candidate)
            if functional_source == "predicted" and not is_predicted:
                continue
            if functional_source == "reported" and is_predicted:
                continue

        label_cn = candidate.get("use_cn") or "其他用途"
        raw_label = (
            candidate.get("raw_use")
            or candidate.get("harmonized_use")
            or candidate.get("reported_use")
            or label_cn
        )
        probability = _to_number(candidate.get("probability"))
        if source_type == "product_category":
            display_label = _clean_cell(raw_label) or _clean_cell(label_cn)
        else:
            display_label = _display_source_use_label(
                label_cn,
                raw_label,
                probability=probability if source_type == "functional_use" else pd.NA,
            )
        key = _normalize_key(display_label)
        if not key:
            continue

        evidence = _to_number(candidate.get("evidence_count"))
        if pd.isna(evidence) or evidence <= 0:
            evidence = 1

        specificity = candidate.get("specificity")
        if pd.isna(specificity):
            specificity = 0

        if key not in grouped:
            grouped[key] = {
                "label": display_label,
                "evidence_count": 0.0,
                "max_single_evidence": 0.0,
                "specificity": 0,
            }
        group = grouped[key]
        group["evidence_count"] += float(evidence)
        group["max_single_evidence"] = max(group["max_single_evidence"], float(evidence))
        group["specificity"] = max(group["specificity"], int(specificity or 0))

    if not grouped:
        return pd.NA

    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            item["evidence_count"],
            item["max_single_evidence"],
            item["specificity"],
            item["label"],
        ),
        reverse=True,
    )
    if source_type == "product_category":
        return "；".join(
            f"{item['label']} ({_format_evidence_count(item['evidence_count'])})"
            for item in ranked
        )
    return "；".join(item["label"] for item in ranked)


def _is_predicted_functional_use(candidate):
    source = _clean_cell(candidate.get("functional_use_source")).lower()
    if "pred" in source:
        return True
    if "report" in source or "collect" in source:
        return False
    probability = _to_number(candidate.get("probability"))
    if not pd.isna(probability):
        return True
    return "pred" in _functional_use_source_label(candidate).lower()


def build_product_use_table(candidates_df):
    if not isinstance(candidates_df, pd.DataFrame) or candidates_df.empty:
        return pd.DataFrame(columns=PRODUCT_USE_TABLE_COLUMNS)
    if "source_type" not in candidates_df.columns:
        return pd.DataFrame(columns=PRODUCT_USE_TABLE_COLUMNS)

    rows = []
    product_rows = candidates_df[candidates_df["source_type"].eq("product_category")]
    for _, row in product_rows.iterrows():
        dtxsid = _clean_cell(row.get("dtxsid"))
        rows.append(
            {
                "compound": _clean_cell(row.get("compound")),
                "dtxsid": dtxsid,
                "产品用途类别": _clean_cell(row.get("raw_use")) or _table_use_label(row.get("use_cn"), row.get("raw_use")),
                "英文产品用途类别": _clean_cell(row.get("raw_use")),
                "general_category": _clean_cell(row.get("general_category")),
                "product_family": _clean_cell(row.get("product_family")),
                "product_type": _clean_cell(row.get("product_type")),
                "product_count": _to_number(row.get("evidence_count")),
                "description": _clean_cell(row.get("description")),
                "source": _clean_cell(row.get("source")),
                "CompTox产品用途链接": (
                    urllib.parse.urljoin(DEFAULT_DASHBOARD_BASE, f"chemical/product-use-categories/{dtxsid}")
                    if dtxsid
                    else pd.NA
                ),
            }
        )

    if not rows:
        return pd.DataFrame(columns=PRODUCT_USE_TABLE_COLUMNS)
    output = pd.DataFrame(rows, columns=PRODUCT_USE_TABLE_COLUMNS)
    output["_sort_count"] = output["product_count"].map(lambda value: _to_number(value))
    output = output.sort_values(
        by=["compound", "_sort_count", "英文产品用途类别"],
        ascending=[True, False, True],
        na_position="last",
    )
    return output.drop(columns=["_sort_count"]).reset_index(drop=True)


def build_functional_use_table(candidates_df, functional_source=None):
    if not isinstance(candidates_df, pd.DataFrame) or candidates_df.empty:
        return pd.DataFrame(columns=FUNCTIONAL_USE_TABLE_COLUMNS)
    if "source_type" not in candidates_df.columns:
        return pd.DataFrame(columns=FUNCTIONAL_USE_TABLE_COLUMNS)

    rows = []
    functional_rows = candidates_df[candidates_df["source_type"].eq("functional_use")]
    if functional_source:
        functional_rows = functional_rows[
            functional_rows.apply(
                lambda row: _functional_use_source_label(row) == functional_source,
                axis=1,
            )
        ]
    for _, row in functional_rows.iterrows():
        dtxsid = _clean_cell(row.get("dtxsid"))
        probability = _to_number(row.get("probability"))
        english_use = _first_nonempty(
            row.get("harmonized_use"),
            row.get("raw_use"),
            row.get("reported_use"),
        )
        rows.append(
            {
                "compound": _clean_cell(row.get("compound")),
                "dtxsid": dtxsid,
                "功能用途": _table_use_label(row.get("use_cn"), english_use),
                "英文功能用途": english_use,
                "来源类型": _functional_use_source_label(row),
                "预测概率": probability if not pd.isna(probability) else pd.NA,
                "reported_use": _clean_cell(row.get("reported_use")),
                "harmonized_use": _clean_cell(row.get("harmonized_use")),
                "evidence_count": row.get("evidence_count"),
                "source": _clean_cell(row.get("source")),
                "CompTox功能用途链接": (
                    urllib.parse.urljoin(DEFAULT_DASHBOARD_BASE, f"chemical/chemical-functional-use/{dtxsid}")
                    if dtxsid
                    else pd.NA
                ),
            }
        )

    if not rows:
        return pd.DataFrame(columns=FUNCTIONAL_USE_TABLE_COLUMNS)
    output = pd.DataFrame(rows, columns=FUNCTIONAL_USE_TABLE_COLUMNS)
    output["_sort_probability"] = output["预测概率"].map(lambda value: _to_number(value))
    output["_sort_evidence"] = output["evidence_count"].map(lambda value: _to_number(value))
    output = output.sort_values(
        by=["compound", "_sort_probability", "_sort_evidence", "英文功能用途"],
        ascending=[True, False, False, True],
        na_position="last",
    )
    return output.drop(columns=["_sort_probability", "_sort_evidence"]).reset_index(drop=True)


def build_evidence_metadata_table():
    return pd.DataFrame(EVIDENCE_METADATA_ROWS, columns=EVIDENCE_METADATA_COLUMNS)


def _table_use_label(label_cn, raw_label):
    label = _clean_cell(label_cn)
    if label:
        return label
    return _display_use_label("其他用途", raw_label)


def _display_source_use_label(label_cn, raw_label, probability=pd.NA):
    label = _clean_cell(label_cn) or "其他用途"
    raw = _clean_cell(raw_label)
    probability_text = _format_probability(probability)
    if probability_text:
        raw = f"{raw}, p={probability_text}" if raw else f"p={probability_text}"
    if raw and _normalize_key(raw) != _normalize_key(label):
        return f"{label} ({raw})"
    return label


def _first_nonempty(*values):
    for value in values:
        text = _clean_cell(value)
        if text:
            return text
    return ""


def _format_probability(value):
    value = _to_number(value)
    if pd.isna(value):
        return ""
    return f"{float(value):.3f}"


def _functional_use_source_from_record(record, source, probability):
    explicit = _get_any(
        record,
        [
            "functionalUseSource",
            "functional_use_source",
            "sourceType",
            "source_type",
            "dataType",
            "type",
        ],
    )
    explicit_text = _clean_cell(explicit).lower()
    if "pred" in explicit_text:
        return "predicted"
    if "report" in explicit_text or "collect" in explicit_text:
        return "reported"
    if not pd.isna(probability) or "predicted" in _clean_cell(source).lower():
        return "predicted"
    return "reported"


def _functional_use_source_label(row):
    source = _clean_cell(row.get("functional_use_source")).lower() if hasattr(row, "get") else ""
    if source:
        return source
    probability = _to_number(row.get("probability")) if hasattr(row, "get") else pd.NA
    if not pd.isna(probability):
        return "predicted"
    raw_source = _clean_cell(row.get("source")).lower() if hasattr(row, "get") else ""
    if "predicted" in raw_source:
        return "predicted"
    return "reported"


def _extract_product_category_candidates(data, source):
    records = _find_dicts(
        data,
        lambda item: any(
            _get_any(item, names) is not pd.NA
            for names in (
                ["displayPuc", "display_puc", "pucName"],
                ["generalCategory"],
                ["productFamily"],
            )
        ),
    )
    candidates = []
    for record in records:
        label = _get_any(record, ["displayPuc", "display_puc", "pucName", "puc", "name"])
        general = _get_any(record, ["generalCategory", "general_category"])
        family = _get_any(record, ["productFamily", "product_family"])
        product_type = _get_any(record, ["productType", "product_type"])
        if pd.isna(label):
            label = _join_nonempty([general, family, product_type], ":")
        if pd.isna(label) or not str(label).strip():
            continue
        if _is_generic_use(label):
            continue

        evidence = _to_number(_get_any(record, ["productCount", "product_count", "count"]))
        description = _get_any(record, ["pucDescription", "description"])
        text_parts = [label, general, family, product_type]
        candidates.append(
            _candidate(
                source_type="product_category",
                source=source,
                raw_use=label,
                general_category=general,
                product_family=family,
                product_type=product_type,
                reported_use=pd.NA,
                harmonized_use=pd.NA,
                evidence_count=evidence,
                description=description,
                use_cn=classify_use_cn(*text_parts),
                specificity=_specificity(label),
            )
        )
    return candidates


def _extract_product_keyword_candidates(data, source):
    candidates = []
    for value in _collect_keyword_values(data):
        label = _clean_cell(value)
        if not label:
            continue
        if _is_generic_use(label):
            continue
        candidates.append(
            _candidate(
                source_type="product_keyword",
                source=source,
                raw_use=label,
                general_category=pd.NA,
                product_family=pd.NA,
                product_type=pd.NA,
                reported_use=label,
                harmonized_use=pd.NA,
                evidence_count=1,
                description=pd.NA,
                use_cn=classify_use_cn(label),
                specificity=_specificity(label),
            )
        )
    return candidates


def _extract_functional_use_candidates(data, source):
    records = _find_dicts(
        data,
        lambda item: any(
            _get_any(item, names) is not pd.NA
            for names in (
                ["harmonizedFunctionalUse", "harmonized_functional_use"],
                ["reportedFunctionalUse", "reported_functional_use"],
                ["probability", "predictedProbability", "predictionProbability"],
            )
        ),
    )
    groups = {}
    for record in records:
        harmonized = _get_any(record, ["harmonizedFunctionalUse", "harmonized_functional_use"])
        reported = _get_any(record, ["reportedFunctionalUse", "reported_functional_use"])
        probability = _to_number(
            _get_any(record, ["probability", "predictedProbability", "predictionProbability"])
        )
        label = harmonized if not pd.isna(harmonized) and str(harmonized).strip() else reported
        label = _clean_cell(label)
        if not label:
            continue
        if _is_generic_use(label):
            continue
        functional_use_source = _functional_use_source_from_record(record, source, probability)
        reported_text = _clean_cell(reported)

        if functional_use_source == "predicted" and not pd.isna(probability):
            key = ("predicted", _normalize_key(label))
            if key not in groups:
                groups[key] = {
                    "label": label,
                    "harmonized": harmonized,
                    "reported_values": set(),
                    "probability": pd.NA,
                    "functional_use_source": "predicted",
                }
            current_probability = groups[key]["probability"]
            if pd.isna(current_probability) or probability > current_probability:
                groups[key]["probability"] = probability

        if reported_text or functional_use_source != "predicted":
            key = ("reported", _normalize_key(label))
            if key not in groups:
                groups[key] = {
                    "label": label,
                    "harmonized": harmonized,
                    "reported_values": set(),
                    "probability": pd.NA,
                    "functional_use_source": "reported",
                }
            if reported_text:
                groups[key]["reported_values"].add(reported_text)

    candidates = []
    for group in groups.values():
        label = group["label"]
        reported_joined = " | ".join(sorted(group["reported_values"])) if group["reported_values"] else pd.NA
        probability = group["probability"]
        evidence_count = probability if not pd.isna(probability) else 1
        candidates.append(
            _candidate(
                source_type="functional_use",
                source=source,
                raw_use=label,
                general_category=pd.NA,
                product_family=pd.NA,
                product_type=pd.NA,
                reported_use=reported_joined,
                harmonized_use=group["harmonized"],
                evidence_count=evidence_count,
                description=(
                    f"Predicted probability={_format_probability(probability)}"
                    if not pd.isna(probability)
                    else pd.NA
                ),
                use_cn=classify_use_cn(label, reported_joined, group["harmonized"]),
                specificity=_specificity(label),
                probability=probability,
                functional_use_source=group["functional_use_source"],
            )
        )
    return sorted(
        candidates,
        key=lambda item: (
            _to_number(item.get("probability")) if not pd.isna(_to_number(item.get("probability"))) else -1,
            _to_number(item.get("evidence_count")) if not pd.isna(_to_number(item.get("evidence_count"))) else -1,
            _clean_cell(item.get("raw_use")),
        ),
        reverse=True,
    )


def _extract_dashboard_product_categories(html):
    records = _extract_nuxt_array_records(html, "pucData")
    return _extract_product_category_candidates(records, source="dashboard:product_category")


def _extract_dashboard_functional_uses(html):
    records = []
    records.extend(_extract_nuxt_array_records(html, "reportedFunctionalUse"))
    records.extend(_extract_nuxt_array_records(html, "predictedFunctionalUse"))
    if not records:
        records = _extract_nuxt_array_records(html, "FunctionalUse")
    return _extract_functional_use_candidates(records, source="dashboard:functional_use")


def _candidate(
    source_type,
    source,
    raw_use,
    general_category,
    product_family,
    product_type,
    reported_use,
    harmonized_use,
    evidence_count,
    description,
    use_cn,
    specificity,
    probability=pd.NA,
    functional_use_source="",
):
    return {
        "source_type": source_type,
        "source": source,
        "raw_use": _clean_cell(raw_use),
        "use_cn": use_cn,
        "general_category": _clean_cell(general_category),
        "product_family": _clean_cell(product_family),
        "product_type": _clean_cell(product_type),
        "reported_use": _clean_cell(reported_use),
        "harmonized_use": _clean_cell(harmonized_use),
        "evidence_count": evidence_count,
        "description": _clean_cell(description),
        "specificity": specificity,
        "probability": probability,
        "functional_use_source": _clean_cell(functional_use_source),
    }


def classify_use_cn(*texts):
    combined = _normalize_use_text(" ".join(_clean_cell(text) for text in texts if _clean_cell(text)))
    for keywords, label in USE_TRANSLATION_RULES:
        if any(keyword in combined for keyword in keywords):
            return label
    cleaned = combined.strip()
    if cleaned:
        return ""
    return "未分类"


def _normalize_use_text(text):
    text = _clean_cell(text).lower()
    text = re.sub(r"[_\-/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _display_use_label(label_cn, raw_label):
    raw = _clean_cell(raw_label)
    if label_cn == "其他用途":
        return f"其他用途：{raw}" if raw else label_cn
    if raw and _normalize_key(raw) != _normalize_key(label_cn):
        return f"{label_cn} ({raw})"
    return label_cn


def _is_generic_use(label):
    text = _clean_cell(label).lower()
    if not text:
        return True
    normalized = re.sub(r"\s+", " ", text).strip()
    if normalized in GENERIC_USE_EXACT:
        return True
    return any(pattern in normalized for pattern in GENERIC_USE_PATTERNS)


def _api_get_json(path, params=None, api_base=DEFAULT_API_BASE, api_key=None, timeout=45):
    base = _clean_cell(api_base)
    if not base:
        raise ValueError("未配置 EPA CompTox API 地址。")
    base = base if base.endswith("/") else base + "/"
    url = urllib.parse.urljoin(base, path)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    key = DEFAULT_COMPTOX_API_KEY if api_key is None else api_key.strip()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "ChemPriority CompTox use-query module",
    }
    if key:
        headers["x-api-key"] = key

    request = urllib.request.Request(url, headers=headers)

    def fetch():
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"连接失败: {exc.reason}") from exc
        return json.loads(raw.decode("utf-8", errors="replace"))

    return cached_call(
        "comptox_api",
        "v1",
        {"base": base, "path": path, "params": params},
        fetch,
    )


def _dashboard_get_html(path, timeout=45):
    url = urllib.parse.urljoin(DEFAULT_DASHBOARD_BASE, path)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "ChemPriority CompTox dashboard fallback",
        },
    )
    retryable_http_statuses = {429, 500, 502, 503, 504}

    def fetch():
        last_error = None
        for attempt in range(1, DASHBOARD_REQUEST_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                return raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code not in retryable_http_statuses:
                    raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
                last_error = RuntimeError(f"HTTP {exc.code}: {url}")
            except urllib.error.URLError as exc:
                last_error = RuntimeError(f"连接失败: {exc.reason}")

            if attempt < DASHBOARD_REQUEST_ATTEMPTS:
                time.sleep(DASHBOARD_RETRY_DELAY_SECONDS * attempt)

        raise last_error

    return cached_call(
        "comptox_dashboard_html",
        "v1",
        {"base": DEFAULT_DASHBOARD_BASE, "path": path},
        fetch,
    )


def _api_is_configured(api_base):
    return bool(_clean_cell(api_base))


def _query_scope_note(api_base, dashboard_fallback):
    if _api_is_configured(api_base):
        return ""
    if dashboard_fallback:
        return DASHBOARD_ONLY_QUERY_NOTE
    return "未配置 EPA API，且未启用 CompTox Dashboard 查询。"


def _extract_chemical_candidates(data):
    return _find_dicts(data, lambda item: _get_any(item, ["dtxsid", "dsstoxSubstanceId"]) is not pd.NA)


def _dashboard_search_chemical_candidates(term, timeout=45):
    query = urllib.parse.urlencode({"input_type": "equalsDetails", "inputs": term})
    html = _dashboard_get_html(f"search-results?{query}", timeout=timeout)
    body, variables = _extract_nuxt_body_and_variables(html)
    if not body:
        return []

    candidates = []
    seen = set()
    for match in re.finditer(r"\bdtxsid\s*:", body):
        start = body.rfind("{", 0, match.start())
        end = _find_matching(body, start, "{", "}") if start != -1 else -1
        if start == -1 or end == -1:
            continue
        record = _parse_js_object(body[start : end + 1], variables)
        dtxsid = _get_any(record, ["dtxsid"])
        if _is_missing(dtxsid):
            continue
        dtxsid_key = str(dtxsid).upper()
        if dtxsid_key in seen:
            continue
        seen.add(dtxsid_key)
        candidates.append(record)
    return candidates


def _choose_best_identifier_match(candidates, term, term_type):
    if not candidates:
        return None
    term_norm = _normalize_key(term)

    def score(candidate):
        candidate_dtxsid = _normalize_key(_get_any(candidate, ["dtxsid", "dsstoxSubstanceId"]))
        candidate_cas = _normalize_key(_get_any(candidate, ["casrn", "cas", "casNumber"]))
        candidate_name = _normalize_key(_get_any(candidate, ["preferredName", "name", "label"]))
        candidate_smiles = _normalize_key(_get_any(candidate, ["smiles"]))
        value = 0
        if term_type == "cas" and candidate_cas == term_norm:
            value += 100
        if term_type == "compound" and candidate_name == term_norm:
            value += 100
        if term_type == "smiles" and candidate_smiles == term_norm:
            value += 100
        if candidate_dtxsid == term_norm:
            value += 100
        source_count = _to_number(_get_any(candidate, ["sources", "sourceCount", "cpdat"]))
        if not _is_missing(source_count):
            value += int(source_count)
        qc = _to_number(_get_any(candidate, ["qc", "quality"]))
        if not _is_missing(qc):
            value += max(0, 5 - int(qc))
        return value

    return sorted(candidates, key=score, reverse=True)[0]


def _find_dicts(data, predicate):
    found = []
    stack = [data]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if predicate(current):
                found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _collect_keyword_values(data):
    values = []
    stack = [data]
    keyword_keys = {
        "keyword",
        "keywords",
        "keywordsearch",
        "searchterm",
        "productkeyword",
        "displaypuc",
    }
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if _normalize_key(key) in keyword_keys:
                    if isinstance(value, list):
                        values.extend(value)
                    else:
                        values.append(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            values.append(current)
    return values


def _extract_nuxt_array_records(html, key):
    body, variables = _extract_nuxt_body_and_variables(html)
    if not body:
        return []
    array_text = _extract_js_array_after_key(body, key)
    if not array_text:
        return []
    records = []
    for item in _split_top_level(array_text):
        item = item.strip()
        if item.startswith("{") and item.endswith("}"):
            records.append(_parse_js_object(item, variables))
    return records


def _extract_nuxt_body_and_variables(html):
    marker = "window.__NUXT__="
    start = html.find(marker)
    if start == -1:
        return "", {}

    script_end = html.find("</script>", start)
    source = html[start + len(marker) : script_end if script_end != -1 else len(html)]
    function_start = source.find("(function(")
    if function_start == -1:
        return source, {}

    params_start = function_start + len("(function(")
    params_end = source.find("){", params_start)
    if params_end == -1:
        return source, {}

    params = [item.strip() for item in source[params_start:params_end].split(",") if item.strip()]
    body_start = source.find("{", params_end)
    body_end = _find_matching(source, body_start, "{", "}")
    if body_start == -1 or body_end == -1:
        return source, {}

    args_start = source.find("(", body_end)
    args_end = _find_matching(source, args_start, "(", ")") if args_start != -1 else -1
    variables = {}
    if args_start != -1 and args_end != -1:
        args = _split_top_level(source[args_start + 1 : args_end])
        for name, token in zip(params, args):
            variables[name] = _parse_js_value(token, {})

    return source[body_start + 1 : body_end], variables


def _extract_js_array_after_key(text, key):
    pattern = f"{key}:"
    start = text.find(pattern)
    if start == -1:
        return ""
    bracket = text.find("[", start + len(pattern))
    if bracket == -1:
        return ""
    end = _find_matching(text, bracket, "[", "]")
    if end == -1:
        return ""
    return text[bracket + 1 : end]


def _parse_js_object(text, variables):
    inner = text.strip()[1:-1]
    output = {}
    for pair in _split_top_level(inner):
        colon = _find_top_level_colon(pair)
        if colon == -1:
            continue
        key = pair[:colon].strip().strip("\"'")
        value = _parse_js_value(pair[colon + 1 :].strip(), variables)
        output[key] = value
    return output


def _parse_js_value(token, variables):
    token = token.strip()
    if not token:
        return pd.NA
    if token in variables:
        return variables[token]
    if token in {"null", "undefined", "void 0", "NaN"}:
        return pd.NA
    if token == "true":
        return True
    if token == "false":
        return False
    if token.startswith('"') and token.endswith('"'):
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return token[1:-1]
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1]
    if re.fullmatch(r"[-+]?\d+", token):
        return int(token)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[Ee][-+]?\d+)?", token):
        return float(token)
    return token


def _split_top_level(text):
    parts = []
    start = 0
    depth = 0
    quote = None
    escape = False
    for idx, char in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:idx])
            start = idx + 1
    tail = text[start:]
    if tail.strip():
        parts.append(tail)
    return parts


def _find_matching(text, start, opener, closer):
    if start < 0 or start >= len(text) or text[start] != opener:
        return -1
    depth = 0
    quote = None
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _find_top_level_colon(text):
    depth = 0
    quote = None
    escape = False
    for idx, char in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ":" and depth == 0:
            return idx
    return -1


def _get_any(record, names, default=pd.NA):
    if not isinstance(record, dict):
        return default
    key_map = {_normalize_key(key): value for key, value in record.items()}
    for name in names:
        value = key_map.get(_normalize_key(name), default)
        if value is not default and not _is_missing(value):
            return value
    return default


def _clean_cell(value):
    if _is_missing(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _display_compound(row):
    for key in ("compound", "cas", "dtxsid", "smiles"):
        value = _clean_cell(row.get(key))
        if value:
            return value
    return "未命名化合物"


def _to_number(value):
    if _is_missing(value):
        return pd.NA
    if isinstance(value, (int, float)):
        return value
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(value))
    return float(match.group(0)) if match else pd.NA


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, (list, dict, tuple, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _normalize_key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).strip().lower())


def _specificity(label):
    text = _clean_cell(label)
    if not text:
        return 0
    return min(text.count(":") + text.count(">") + 1, 5)


def _join_nonempty(values, separator):
    cleaned = [_clean_cell(value) for value in values if _clean_cell(value)]
    return separator.join(cleaned) if cleaned else pd.NA
