import io
import re
import time

import pandas as pd

from src.batch_runner import run_ordered_batch
from src.echa_use import (
    DEFAULT_ECHA_BASE,
    REQUIRED_IDENTIFIER_COLUMNS,
    _build_url,
    _get_json,
    normalize_input_columns,
    resolve_substance,
    validate_input,
)
from src.query_cache import cache_control


CNL_OVERVIEW_PATH = "api-cnl-inventory/prominent/overview/info/{echa_id}"
CNL_CLASSIFICATIONS_PATH = "api-cnl-inventory/prominent/overview/classifications/{source}/{classification_id}"
CNL_PICTOGRAMS_PATH = "api-cnl-inventory/prominent/overview/pictograms/{source}/{classification_id}"
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
RETRYABLE_ERROR_MARKERS = (
    "unexpected_eof_while_reading",
    "remote end closed connection",
    "connection reset",
    "connection aborted",
    "timed out",
    "timeout",
    "temporary failure",
    "temporarily unavailable",
)

SUMMARY_COLUMNS = [
    "compound",
    "cas",
    "ec",
    "smiles",
    "input_echa_id",
    "matched_echa_id",
    "matched_name",
    "matched_cas",
    "matched_ec",
    "match_status",
    "query_status",
    "GHS危害分层",
    "分类来源",
    "最高关注类别",
    "signal_word",
    "pictogram_code",
    "pictogram_text",
    "hazard_statement_code",
    "hazard_statement_text",
    "harmonised_classification_count",
    "industry_classification_count",
    "not_classified_or_no_ghs",
    "ECHA链接",
    "notes",
]

CLASSIFICATION_COLUMNS = [
    "compound",
    "echa_id",
    "classification_id",
    "classification_source",
    "hazard_class_and_category_code",
    "hazard_statement_code",
    "hazard_statement_text",
    "signal_word",
    "pictogram_code",
    "pictogram_text",
    "display_order",
    "ECHA链接",
]

WARNING_COLUMNS = [
    "compound",
    "cas",
    "ec",
    "smiles",
    "echa_id",
    "stage",
    "message",
]


def run_echa_ghs_batch(
    input_df,
    base_url=DEFAULT_ECHA_BASE,
    timeout=90,
    delay_seconds=0.5,
    progress_callback=None,
):
    clean_df = normalize_input_columns(input_df)
    summary_rows = []
    classification_rows = []
    warning_rows = []
    total = len(clean_df)

    for pos, (_, row) in enumerate(clean_df.iterrows(), start=1):
        compound = _display_compound(row)
        try:
            resolution = resolve_substance(row, base_url=base_url, timeout=timeout)
            echa_id = _clean_cell(resolution.get("echa_id"))
            if not echa_id:
                summary_rows.append(_empty_summary_row(row, resolution, "未匹配到 ECHA 物质"))
                warning_rows.append(
                    _warning_row(
                        row,
                        echa_id,
                        "substance_resolution",
                        resolution.get("message", ""),
                    )
                )
            else:
                info = fetch_cnl_overview(echa_id, base_url=base_url, timeout=timeout)
                source = _classification_source(info)
                classification_id = _clean_cell(info.get("classificationId"))
                details = fetch_cnl_classifications(
                    source,
                    classification_id,
                    base_url=base_url,
                    timeout=timeout,
                )
                pictograms = fetch_cnl_pictograms(
                    source,
                    classification_id,
                    base_url=base_url,
                    timeout=timeout,
                )
                rows = _classification_rows(
                    row,
                    resolution,
                    info,
                    details,
                    pictograms,
                    base_url,
                )
                classification_rows.extend(rows)
                summary_rows.append(
                    _summary_row(
                        row,
                        resolution,
                        info,
                        rows,
                        pictograms,
                        "查询完成" if rows else "未查到 GHS 分类",
                        base_url,
                    )
                )
        except Exception as exc:
            summary_rows.append(_empty_summary_row(row, {"echa_id": pd.NA}, "查询失败", str(exc)))
            warning_rows.append(_warning_row(row, row.get("echa_id"), "cnl_inventory", str(exc)))

        if progress_callback:
            progress_callback(pos, total, compound)
        if delay_seconds and pos < total:
            time.sleep(delay_seconds)

    return (
        _ensure_columns(pd.DataFrame(summary_rows), SUMMARY_COLUMNS),
        _ensure_columns(pd.DataFrame(classification_rows), CLASSIFICATION_COLUMNS),
        _ensure_columns(pd.DataFrame(warning_rows), WARNING_COLUMNS),
    )


_run_echa_ghs_batch_sequential = run_echa_ghs_batch


def run_echa_ghs_batch(
    input_df,
    base_url=DEFAULT_ECHA_BASE,
    timeout=90,
    delay_seconds=0.5,
    progress_callback=None,
    max_workers=1,
    cache_enabled=True,
):
    if int(max_workers or 1) <= 1:
        with cache_control(cache_enabled):
            return _run_echa_ghs_batch_sequential(
                input_df,
                base_url=base_url,
                timeout=timeout,
                delay_seconds=delay_seconds,
                progress_callback=progress_callback,
            )

    clean_df = normalize_input_columns(input_df)
    items = list(clean_df.iterrows())

    def process_row(item):
        _, row = item
        return _run_echa_ghs_batch_sequential(
            pd.DataFrame([row]),
            base_url=base_url,
            timeout=timeout,
            delay_seconds=0,
            progress_callback=None,
        )

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
    classification_frames = []
    warning_frames = []
    for result in batch_results:
        if result.error is not None:
            row = items[result.index][1]
            summary_frames.append(pd.DataFrame([_empty_summary_row(row, {"echa_id": pd.NA}, "查询失败", str(result.error))]))
            warning_frames.append(pd.DataFrame([_warning_row(row, row.get("echa_id"), "batch_worker", str(result.error))]))
            continue
        summary_df, classifications_df, warnings_df = result.value
        summary_frames.append(summary_df)
        classification_frames.append(classifications_df)
        warning_frames.append(warnings_df)

    summary = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    classifications = pd.concat(classification_frames, ignore_index=True) if classification_frames else pd.DataFrame()
    warnings = pd.concat(warning_frames, ignore_index=True) if warning_frames else pd.DataFrame()
    return (
        _ensure_columns(summary, SUMMARY_COLUMNS),
        _ensure_columns(classifications, CLASSIFICATION_COLUMNS),
        _ensure_columns(warnings, WARNING_COLUMNS),
    )


def fetch_cnl_overview(echa_id, base_url=DEFAULT_ECHA_BASE, timeout=90):
    return _get_json_with_retry(
        CNL_OVERVIEW_PATH.format(echa_id=_clean_cell(echa_id)),
        base_url=base_url,
        timeout=timeout,
    )


def fetch_cnl_classifications(source, classification_id, base_url=DEFAULT_ECHA_BASE, timeout=90):
    if not _clean_cell(classification_id):
        return []
    data = _get_json_with_retry(
        CNL_CLASSIFICATIONS_PATH.format(
            source=_source_path_segment(source),
            classification_id=_clean_cell(classification_id),
        ),
        base_url=base_url,
        timeout=timeout,
    )
    return data.get("items", []) if isinstance(data, dict) else []


def fetch_cnl_pictograms(source, classification_id, base_url=DEFAULT_ECHA_BASE, timeout=90):
    if not _clean_cell(classification_id):
        return []
    data = _get_json_with_retry(
        CNL_PICTOGRAMS_PATH.format(
            source=_source_path_segment(source),
            classification_id=_clean_cell(classification_id),
        ),
        base_url=base_url,
        timeout=timeout,
    )
    return data.get("items", []) if isinstance(data, dict) else []


def _get_json_with_retry(
    path,
    params=None,
    base_url=DEFAULT_ECHA_BASE,
    timeout=90,
    attempts=DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
):
    attempts = max(1, int(attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            return _get_json(path, params=params, base_url=base_url, timeout=timeout)
        except Exception as exc:
            if attempt >= attempts or not _is_retryable_error(exc):
                raise
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))


def _is_retryable_error(exc):
    message = str(exc).lower()
    if re.search(r"http\s+(429|500|502|503|504)\b", message, flags=re.I):
        return True
    return any(marker in message for marker in RETRYABLE_ERROR_MARKERS)


def build_result_workbook(input_df, summary_df=None, classifications_df=None, errors_df=None):
    if summary_df is None:
        summary_df = pd.DataFrame(columns=SUMMARY_COLUMNS)
    if classifications_df is None:
        classifications_df = pd.DataFrame(columns=CLASSIFICATION_COLUMNS)
    if errors_df is None:
        errors_df = pd.DataFrame(columns=WARNING_COLUMNS)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        normalize_input_columns(input_df)[REQUIRED_IDENTIFIER_COLUMNS].to_excel(
            writer,
            sheet_name="Input",
            index=False,
        )
        _ensure_columns(summary_df, SUMMARY_COLUMNS).to_excel(
            writer,
            sheet_name="ECHA_GHS_Summary",
            index=False,
        )
        _ensure_columns(classifications_df, CLASSIFICATION_COLUMNS).to_excel(
            writer,
            sheet_name="ECHA_GHS_Classifications",
            index=False,
        )
        _ensure_columns(errors_df, WARNING_COLUMNS).to_excel(
            writer,
            sheet_name="ECHA_GHS_Warnings",
            index=False,
        )
    buffer.seek(0)
    return buffer


def build_empty_summary_template(input_df):
    clean_df = normalize_input_columns(input_df)
    return pd.DataFrame(
        [_empty_summary_row(row, {"status": "待查询"}, "待查询") for _, row in clean_df.iterrows()]
    )


def _classification_rows(row, resolution, info, details, pictograms, base_url):
    rows = []
    pictogram_code = _join_unique(pictogram.get("code") for pictogram in pictograms)
    pictogram_text = _join_unique(pictogram.get("text") for pictogram in pictograms)
    signal_word = _signal_word_text(info)
    classification_id = _clean_cell(info.get("classificationId"))
    source = _classification_source(info)
    echa_id = _clean_cell(resolution.get("echa_id"))

    for item in details or []:
        statements = item.get("hazardStatements") if isinstance(item, dict) else []
        statements = statements if isinstance(statements, list) and statements else [{}]
        for statement in statements:
            rows.append(
                {
                    "compound": _display_compound(row),
                    "echa_id": echa_id,
                    "classification_id": _clean_cell(item.get("classificationId")) or classification_id,
                    "classification_source": source,
                    "hazard_class_and_category_code": _clean_cell(
                        item.get("hazardClassAndCategoryCode")
                    ),
                    "hazard_statement_code": _clean_cell(statement.get("hazardStatementCode")),
                    "hazard_statement_text": _clean_cell(statement.get("hazardStatementText")),
                    "signal_word": signal_word,
                    "pictogram_code": pictogram_code,
                    "pictogram_text": pictogram_text,
                    "display_order": item.get("displayOrder", ""),
                    "ECHA链接": _echa_url(echa_id, base_url),
                }
            )
    return rows


def _summary_row(row, resolution, info, classifications, pictograms, status, base_url):
    hazard_codes = [item.get("hazard_class_and_category_code") for item in classifications]
    hazard_statement_codes = [item.get("hazard_statement_code") for item in classifications]
    hazard_statement_texts = [item.get("hazard_statement_text") for item in classifications]
    category_one = [code for code in hazard_codes if _is_category_one(code)]
    has_classification = bool(classifications)
    level = "无GHS数据或未分类"
    if has_classification:
        level = "一类GHS危害" if category_one else "小于一类GHS危害"

    return {
        "compound": _display_compound(row),
        "cas": _clean_cell(row.get("cas")),
        "ec": _clean_cell(row.get("ec")),
        "smiles": _clean_cell(row.get("smiles")),
        "input_echa_id": _clean_cell(row.get("echa_id")),
        "matched_echa_id": _clean_cell(resolution.get("echa_id")),
        "matched_name": _clean_cell(resolution.get("matched_name")),
        "matched_cas": _clean_cell(resolution.get("matched_cas")),
        "matched_ec": _clean_cell(resolution.get("matched_ec")),
        "match_status": _clean_cell(resolution.get("status")),
        "query_status": status,
        "GHS危害分层": level,
        "分类来源": _classification_source(info),
        "最高关注类别": _highest_attention_category(hazard_codes),
        "signal_word": _signal_word_text(info),
        "pictogram_code": _join_unique(pictogram.get("code") for pictogram in pictograms),
        "pictogram_text": _join_unique(pictogram.get("text") for pictogram in pictograms),
        "hazard_statement_code": _join_unique(hazard_statement_codes),
        "hazard_statement_text": _join_unique(hazard_statement_texts),
        "harmonised_classification_count": info.get("totalHarmonisedClassifications", 0),
        "industry_classification_count": info.get("totalIndustryClassifications", 0),
        "not_classified_or_no_ghs": _clean_cell(info.get("notClassifiedOrNoGhs")),
        "ECHA链接": _echa_url(resolution.get("echa_id"), base_url),
        "notes": _clean_cell(resolution.get("message")),
    }


def _empty_summary_row(row, resolution, status, notes=""):
    return {
        "compound": _display_compound(row),
        "cas": _clean_cell(row.get("cas")),
        "ec": _clean_cell(row.get("ec")),
        "smiles": _clean_cell(row.get("smiles")),
        "input_echa_id": _clean_cell(row.get("echa_id")),
        "matched_echa_id": _clean_cell(resolution.get("echa_id")),
        "matched_name": _clean_cell(resolution.get("matched_name")),
        "matched_cas": _clean_cell(resolution.get("matched_cas")),
        "matched_ec": _clean_cell(resolution.get("matched_ec")),
        "match_status": _clean_cell(resolution.get("status")),
        "query_status": status,
        "GHS危害分层": "无GHS数据或未分类",
        "分类来源": "",
        "最高关注类别": "",
        "signal_word": "",
        "pictogram_code": "",
        "pictogram_text": "",
        "hazard_statement_code": "",
        "hazard_statement_text": "",
        "harmonised_classification_count": 0,
        "industry_classification_count": 0,
        "not_classified_or_no_ghs": "",
        "ECHA链接": _echa_url(resolution.get("echa_id"), DEFAULT_ECHA_BASE),
        "notes": notes or _clean_cell(resolution.get("message")),
    }


def _warning_row(row, echa_id, stage, message):
    return {
        "compound": _display_compound(row),
        "cas": _clean_cell(row.get("cas")),
        "ec": _clean_cell(row.get("ec")),
        "smiles": _clean_cell(row.get("smiles")),
        "echa_id": _clean_cell(echa_id),
        "stage": stage,
        "message": _clean_cell(message),
    }


def _classification_source(info):
    source = _clean_cell(info.get("type")).lower() if isinstance(info, dict) else ""
    if source in {"harmonised", "harmonized"}:
        return "harmonised"
    if source == "industry":
        return "industry"
    if isinstance(info, dict) and int(info.get("totalHarmonisedClassifications") or 0) > 0:
        return "harmonised"
    if isinstance(info, dict) and int(info.get("totalIndustryClassifications") or 0) > 0:
        return "industry"
    return source


def _source_path_segment(source):
    return "harmonised" if _clean_cell(source).lower() in {"harmonised", "harmonized"} else "industry"


def _signal_word_text(info):
    signal_word = info.get("signalWord") if isinstance(info, dict) else {}
    if isinstance(signal_word, dict):
        return _clean_cell(signal_word.get("signalWordText") or signal_word.get("signalWordCode"))
    return _clean_cell(signal_word)


def _highest_attention_category(hazard_codes):
    cleaned = [_clean_cell(code) for code in hazard_codes if _clean_cell(code)]
    category_one = [code for code in cleaned if _is_category_one(code)]
    return _join_unique(category_one) or (cleaned[0] if cleaned else "")


def _is_category_one(value):
    return bool(re.search(r"(?<!\d)1[AB]?(?!\d)", _clean_cell(value), flags=re.I))


def _join_unique(values):
    output = []
    seen = set()
    for value in values:
        text = _clean_cell(value)
        if not text or text in seen:
            continue
        output.append(text)
        seen.add(text)
    return "; ".join(output)


def _display_compound(row):
    for column in ("compound", "cas", "ec", "smiles", "echa_id"):
        value = _clean_cell(row.get(column))
        if value:
            return value
    return "Unknown"


def _echa_url(echa_id, base_url):
    echa_id = _clean_cell(echa_id)
    return _build_url(echa_id, base_url=base_url) if echa_id else ""


def _clean_cell(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _ensure_columns(df, columns):
    output = df.copy()
    for column in columns:
        if column not in output.columns:
            output[column] = pd.NA
    return output[columns]
