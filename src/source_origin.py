import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from src.comptox_use import (
    DEFAULT_API_BASE as DEFAULT_COMPTOX_API_BASE,
    DEFAULT_DASHBOARD_BASE,
    run_comptox_use_batch,
)
from src.echa_use import DEFAULT_ECHA_BASE, run_echa_use_batch
from src.identifier_resolver import normalize_input_columns


REQUIRED_IDENTIFIER_COLUMNS = ["compound", "cas", "ec", "smiles", "dtxsid", "echa_id"]
CH_EBI_SEARCH_URL = "https://www.ebi.ac.uk/ols4/api/search"
COCONUT_BASE = "https://coconut.naturalproducts.net/"
COUNTED_CONFIDENCES = {"strong", "medium"}
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
RETRYABLE_ERROR_MARKERS = (
    "remote end closed connection",
    "connection reset",
    "connection aborted",
    "unexpected_eof_while_reading",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "temporary failure",
)

NATURAL_KEYWORDS = (
    "naturally occurring",
    "natural product",
    "natural products",
    "metabolite",
    "plant metabolite",
    "bacterial metabolite",
    "fungal metabolite",
    "microbial metabolite",
    "secondary metabolite",
    "endogenous",
    "alkaloid",
    "terpenoid",
)

SUMMARY_COLUMNS = [
    "compound",
    "cas",
    "ec",
    "smiles",
    "dtxsid",
    "echa_id",
    "来源属性",
    "证据等级",
    "人为源证据数",
    "天然源证据数",
    "主要人为源依据",
    "主要天然源依据",
    "证据链接",
    "notes",
]

EVIDENCE_COLUMNS = [
    "compound",
    "source_group",
    "source_name",
    "evidence_type",
    "evidence_label",
    "evidence_text",
    "matched_identifier",
    "confidence",
    "record_url",
]

WARNING_COLUMNS = [
    "compound",
    "cas",
    "ec",
    "smiles",
    "dtxsid",
    "echa_id",
    "source_name",
    "stage",
    "message",
]


def validate_input(df):
    clean_df = normalize_source_input_columns(df)
    usable_rows = clean_df[REQUIRED_IDENTIFIER_COLUMNS].notna().any(axis=1).sum()
    if usable_rows == 0:
        return False, "没有可用于来源属性评估的化合物标识。"
    return True, f"来源属性输入检查通过，共 {usable_rows} 个可评估化合物。"


def normalize_source_input_columns(df):
    normalized = normalize_input_columns(df)
    for column in REQUIRED_IDENTIFIER_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    return normalized[REQUIRED_IDENTIFIER_COLUMNS]


def run_source_origin_batch(
    input_df,
    comptox_summary_df=None,
    comptox_candidates_df=None,
    echa_summary_df=None,
    echa_candidates_df=None,
    echa_dossiers_df=None,
    comptox_api_base=DEFAULT_COMPTOX_API_BASE,
    comptox_api_key=None,
    echa_base=DEFAULT_ECHA_BASE,
    timeout=60,
    delay_seconds=0.2,
    progress_callback=None,
):
    clean_df = normalize_source_input_columns(input_df)
    warning_rows = []

    if comptox_summary_df is None and comptox_candidates_df is None:
        try:
            comptox_summary_df, comptox_candidates_df, comptox_errors_df = run_comptox_use_batch(
                clean_df,
                api_base=comptox_api_base,
                api_key=comptox_api_key,
                timeout=timeout,
                delay_seconds=delay_seconds,
            )
            warning_rows.extend(
                _external_warnings(clean_df, comptox_errors_df, "EPA CompTox")
            )
        except Exception as exc:
            comptox_summary_df = pd.DataFrame()
            comptox_candidates_df = pd.DataFrame()
            warning_rows.append(_warning_row(None, "EPA CompTox", "auto_query", str(exc)))

    if echa_summary_df is None and echa_candidates_df is None and echa_dossiers_df is None:
        try:
            echa_summary_df, echa_candidates_df, echa_dossiers_df, echa_errors_df = run_echa_use_batch(
                clean_df,
                base_url=echa_base,
                timeout=timeout,
                delay_seconds=delay_seconds,
                max_dossiers=1,
            )
            warning_rows.extend(_external_warnings(clean_df, echa_errors_df, "ECHA CHEM"))
        except Exception as exc:
            echa_summary_df = pd.DataFrame()
            echa_candidates_df = pd.DataFrame()
            echa_dossiers_df = pd.DataFrame()
            warning_rows.append(_warning_row(None, "ECHA CHEM", "auto_query", str(exc)))

    summary_rows = []
    evidence_rows = []
    total = len(clean_df)

    for pos, (_, row) in enumerate(clean_df.iterrows(), start=1):
        compound = _display_compound(row)
        row_evidence = []
        row_evidence.extend(
            _human_evidence_for_row(
                row,
                comptox_summary_df=comptox_summary_df,
                comptox_candidates_df=comptox_candidates_df,
                echa_summary_df=echa_summary_df,
                echa_candidates_df=echa_candidates_df,
                echa_dossiers_df=echa_dossiers_df,
            )
        )

        try:
            row_evidence.extend(fetch_chebi_evidence(row, timeout=timeout))
        except Exception as exc:
            warning_rows.append(_warning_row(row, "ChEBI", "natural_lookup", str(exc)))

        try:
            row_evidence.extend(fetch_coconut_evidence(row, timeout=timeout))
        except Exception as exc:
            warning_rows.append(_warning_row(row, "COCONUT", "natural_lookup", str(exc)))

        row_evidence = _dedupe_evidence(_attach_compound(row_evidence, compound))
        evidence_rows.extend(row_evidence)
        summary_rows.append(_summary_row(row, row_evidence))

        if progress_callback:
            progress_callback(pos, total, compound)
        if delay_seconds and pos < total:
            time.sleep(delay_seconds)

    return (
        _ensure_columns(pd.DataFrame(summary_rows), SUMMARY_COLUMNS),
        _ensure_columns(pd.DataFrame(evidence_rows), EVIDENCE_COLUMNS),
        _ensure_columns(pd.DataFrame(warning_rows), WARNING_COLUMNS),
    )


def fetch_chebi_evidence(row, timeout=60):
    evidence = []
    for query in _natural_query_terms(row):
        data = _get_json_with_retry(
            CH_EBI_SEARCH_URL,
            params={"q": query, "ontology": "chebi", "rows": 5},
            timeout=timeout,
        )
        evidence.extend(_chebi_evidence_from_response(data, query))
        if evidence:
            return evidence[:3]
    return evidence


def fetch_coconut_evidence(row, timeout=60, base_url=COCONUT_BASE):
    evidence = []
    for query in _natural_query_terms(row, include_cas=False):
        url = _build_url("api/search", params={"query": query, "limit": 5}, base_url=base_url)
        data = _post_json_with_retry(url, {}, timeout=timeout)
        evidence.extend(_coconut_evidence_from_response(data, query, base_url, row=row))
        if evidence:
            return evidence[:3]
    return evidence


def build_result_workbook(input_df, summary_df=None, evidence_df=None, errors_df=None):
    if summary_df is None:
        summary_df = pd.DataFrame(columns=SUMMARY_COLUMNS)
    if evidence_df is None:
        evidence_df = pd.DataFrame(columns=EVIDENCE_COLUMNS)
    if errors_df is None:
        errors_df = pd.DataFrame(columns=WARNING_COLUMNS)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        normalize_source_input_columns(input_df).to_excel(writer, sheet_name="Input", index=False)
        _ensure_columns(summary_df, SUMMARY_COLUMNS).to_excel(
            writer, sheet_name="Source_Origin_Summary", index=False
        )
        _ensure_columns(evidence_df, EVIDENCE_COLUMNS).to_excel(
            writer, sheet_name="Source_Origin_Evidence", index=False
        )
        _ensure_columns(errors_df, WARNING_COLUMNS).to_excel(
            writer, sheet_name="Source_Origin_Warnings", index=False
        )
    buffer.seek(0)
    return buffer


def build_empty_summary_template(input_df):
    clean_df = normalize_source_input_columns(input_df)
    return pd.DataFrame(
        [
            {
                "compound": _display_compound(row),
                "cas": _clean_cell(row.get("cas")),
                "ec": _clean_cell(row.get("ec")),
                "smiles": _clean_cell(row.get("smiles")),
                "dtxsid": _clean_cell(row.get("dtxsid")),
                "echa_id": _clean_cell(row.get("echa_id")),
                "来源属性": "待评估",
                "证据等级": "",
                "人为源证据数": 0,
                "天然源证据数": 0,
                "主要人为源依据": "",
                "主要天然源依据": "",
                "证据链接": "",
                "notes": "",
            }
            for _, row in clean_df.iterrows()
        ]
    )


def _human_evidence_for_row(
    row,
    comptox_summary_df=None,
    comptox_candidates_df=None,
    echa_summary_df=None,
    echa_candidates_df=None,
    echa_dossiers_df=None,
):
    evidence = []
    evidence.extend(_comptox_candidate_evidence(row, comptox_candidates_df))
    if not any(item["source_name"] == "EPA CompTox" for item in evidence):
        evidence.extend(_comptox_summary_evidence(row, comptox_summary_df))

    evidence.extend(_echa_candidate_evidence(row, echa_candidates_df))
    if not any(item["source_name"] == "ECHA CHEM" for item in evidence):
        evidence.extend(_echa_summary_evidence(row, echa_summary_df))
    if not any(item["source_name"] == "ECHA CHEM" for item in evidence):
        evidence.extend(_echa_dossier_evidence(row, echa_dossiers_df))
    return evidence


def _comptox_candidate_evidence(row, candidates_df):
    evidence = []
    for _, candidate in _matching_rows(candidates_df, row).iterrows():
        label = _clean_cell(candidate.get("use_cn")) or _clean_cell(candidate.get("raw_use"))
        text = _first_text(
            candidate.get("raw_use"),
            candidate.get("harmonized_use"),
            candidate.get("reported_use"),
            candidate.get("description"),
            label,
        )
        if not label and not text:
            continue
        source_type = _clean_cell(candidate.get("source_type")) or "use"
        dtxsid = _clean_cell(candidate.get("dtxsid")) or _clean_cell(row.get("dtxsid"))
        evidence.append(
            {
                "source_group": "human",
                "source_name": "EPA CompTox",
                "evidence_type": source_type,
                "evidence_label": label or text,
                "evidence_text": text,
                "matched_identifier": dtxsid,
                "confidence": _comptox_candidate_confidence(candidate, source_type),
                "record_url": _comptox_url(dtxsid, source_type),
            }
        )
    return evidence


def _comptox_candidate_confidence(candidate, source_type):
    if source_type == "product_category":
        return "strong"
    if source_type == "functional_use":
        functional_source = _clean_cell(candidate.get("functional_use_source")).lower()
        probability = candidate.get("probability")
        if "predicted" in functional_source or _clean_cell(probability):
            return "medium"
        return "strong"
    return "medium"


def _comptox_summary_evidence(row, summary_df):
    evidence = []
    for _, summary in _matching_rows(summary_df, row).iterrows():
        for column, source_type in (
            ("产品用途类别", "product_category"),
            ("化学功能用途", "functional_use"),
            ("前五用途", "top_use_summary"),
        ):
            value = _clean_cell(summary.get(column))
            if not value:
                continue
            dtxsid = _clean_cell(summary.get("matched_dtxsid")) or _clean_cell(row.get("dtxsid"))
            evidence.append(
                {
                    "source_group": "human",
                    "source_name": "EPA CompTox",
                    "evidence_type": source_type,
                    "evidence_label": column,
                    "evidence_text": value,
                    "matched_identifier": dtxsid,
                    "confidence": "medium",
                    "record_url": _comptox_url(dtxsid, source_type),
                }
            )
    return evidence


def _echa_candidate_evidence(row, candidates_df):
    evidence = []
    for _, candidate in _matching_rows(candidates_df, row).iterrows():
        label = _clean_cell(candidate.get("use_cn")) or _clean_cell(candidate.get("raw_use"))
        text = _first_text(candidate.get("raw_use"), candidate.get("use_en"), label)
        if not label and not text:
            continue
        evidence.append(
            {
                "source_group": "human",
                "source_name": "ECHA CHEM",
                "evidence_type": _clean_cell(candidate.get("source_type")) or "reach_dossier_use",
                "evidence_label": label or text,
                "evidence_text": text,
                "matched_identifier": _clean_cell(candidate.get("echa_id")) or _clean_cell(row.get("echa_id")),
                "confidence": "strong",
                "record_url": _clean_cell(candidate.get("record_url")) or _clean_cell(candidate.get("dossier_url")),
            }
        )
    return evidence


def _echa_summary_evidence(row, summary_df):
    evidence = []
    for _, summary in _matching_rows(summary_df, row).iterrows():
        value = _clean_cell(summary.get("前五用途"))
        if not value:
            continue
        evidence.append(
            {
                "source_group": "human",
                "source_name": "ECHA CHEM",
                "evidence_type": "reach_use_summary",
                "evidence_label": "前五用途",
                "evidence_text": value,
                "matched_identifier": _clean_cell(summary.get("matched_echa_id")) or _clean_cell(row.get("echa_id")),
                "confidence": "medium",
                "record_url": _clean_cell(summary.get("ECHA搜索页面")),
            }
        )
    return evidence


def _echa_dossier_evidence(row, dossiers_df):
    evidence = []
    for _, dossier in _matching_rows(dossiers_df, row).iterrows():
        parsed_count = _to_int(dossier.get("parsed_use_count"))
        registration = _clean_cell(dossier.get("registration_number"))
        if parsed_count <= 0 and not registration:
            continue
        evidence.append(
            {
                "source_group": "human",
                "source_name": "ECHA CHEM",
                "evidence_type": "reach_dossier_registration",
                "evidence_label": registration or "REACH dossier",
                "evidence_text": _join_nonempty(
                    [
                        dossier.get("dossier_subtype"),
                        dossier.get("registration_role"),
                        f"parsed_use_count={parsed_count}" if parsed_count else "",
                    ]
                ),
                "matched_identifier": _clean_cell(dossier.get("echa_id")) or _clean_cell(row.get("echa_id")),
                "confidence": "medium" if parsed_count > 0 else "weak",
                "record_url": _clean_cell(dossier.get("dossier_url")),
            }
        )
    return evidence


def _chebi_evidence_from_response(data, query):
    docs = (((data or {}).get("response") or {}).get("docs") or []) if isinstance(data, dict) else []
    evidence = []
    query_norm = _normalize_key(query)
    for doc in docs:
        label = _clean_cell(doc.get("label"))
        obo_id = _clean_cell(doc.get("obo_id") or doc.get("short_form"))
        descriptions = doc.get("description") if isinstance(doc.get("description"), list) else []
        synonyms = []
        for key in ("exact_synonyms", "related_synonyms", "synonyms"):
            value = doc.get(key)
            if isinstance(value, list):
                synonyms.extend(_clean_cell(item) for item in value)
        combined = " ".join([label, *synonyms, *[_clean_cell(item) for item in descriptions]]).lower()
        has_natural_signal = any(keyword in combined for keyword in NATURAL_KEYWORDS)
        exact_match = query_norm in {_normalize_key(label), *[_normalize_key(item) for item in synonyms]}
        if not has_natural_signal and not exact_match:
            continue
        confidence = "strong" if has_natural_signal and exact_match else "medium" if has_natural_signal else "weak"
        evidence.append(
            {
                "source_group": "natural",
                "source_name": "ChEBI",
                "evidence_type": "chebi_natural_signal" if has_natural_signal else "chebi_candidate",
                "evidence_label": label or obo_id,
                "evidence_text": _join_nonempty(descriptions) or combined[:500],
                "matched_identifier": obo_id,
                "confidence": confidence,
                "record_url": _chebi_url(obo_id),
            }
        )
    return evidence


def _coconut_evidence_from_response(data, query, base_url, row=None):
    records = []
    if isinstance(data, dict):
        payload = data.get("data")
        if isinstance(payload, dict):
            records = payload.get("data") or []
        elif isinstance(payload, list):
            records = payload
    evidence = []
    query_norm = _normalize_key(query)
    for record in records[:5]:
        if not _coconut_identity_matches(record, query, row):
            continue
        name = _clean_cell(record.get("name"))
        identifier = _clean_cell(record.get("identifier"))
        if not name and not identifier:
            continue
        organism_count = _to_int(record.get("organism_count"))
        citation_count = _to_int(record.get("citation_count"))
        collection_count = _to_int(record.get("collection_count"))
        exact_match = query_norm == _normalize_key(name)
        if organism_count > 0 and exact_match:
            confidence = "strong"
        elif organism_count > 0 or citation_count > 0 or collection_count > 0:
            confidence = "medium"
        else:
            confidence = "weak"
        evidence.append(
            {
                "source_group": "natural",
                "source_name": "COCONUT",
                "evidence_type": "coconut_natural_product_match",
                "evidence_label": name or identifier,
                "evidence_text": (
                    f"organism_count={organism_count}; citation_count={citation_count}; "
                    f"collection_count={collection_count}"
                ),
                "matched_identifier": identifier,
                "confidence": confidence,
                "record_url": _build_url("search", params={"query": identifier or name}, base_url=base_url),
            }
        )
    return evidence


def _coconut_identity_matches(record, query, row=None):
    if not isinstance(record, dict):
        return False

    record_names = _record_text_values(
        record,
        ("name", "preferred_name", "iupac_name", "synonyms", "aliases"),
    )
    record_identifiers = _record_text_values(
        record,
        ("identifier", "id", "coconut_id", "coconutId"),
    )
    record_cas_values = _record_text_values(
        record,
        ("cas", "cas_number", "casNumber", "cas_rn", "casRegistryNumber"),
    )
    record_smiles_values = _record_text_values(
        record,
        ("canonical_smiles", "smiles", "absolute_smiles", "unique_smiles"),
    )

    query_norm = _normalize_key(query)
    record_name_keys = {_normalize_key(value) for value in record_names if _clean_cell(value)}
    record_identifier_keys = {_normalize_key(value) for value in record_identifiers if _clean_cell(value)}
    if query_norm and query_norm in record_name_keys.union(record_identifier_keys):
        return True

    if hasattr(row, "get"):
        expected_names = [
            _clean_cell(row.get("resolved_name")),
            _clean_cell(row.get("compound")),
        ]
        expected_name_keys = {_normalize_key(value) for value in expected_names if _clean_cell(value)}
        if expected_name_keys.intersection(record_name_keys):
            return True

        expected_cas = _normalize_key(row.get("cas"))
        record_cas_keys = {_normalize_key(value) for value in record_cas_values if _clean_cell(value)}
        if expected_cas and expected_cas in record_cas_keys:
            return True

        expected_smiles = _normalize_smiles(row.get("smiles"))
        record_smiles_keys = {_normalize_smiles(value) for value in record_smiles_values if _clean_cell(value)}
        if expected_smiles and expected_smiles in record_smiles_keys:
            return True

    return False


def _summary_row(row, evidence_rows):
    human_count = _count_evidence(evidence_rows, "human")
    natural_count = _count_evidence(evidence_rows, "natural")
    if human_count and natural_count:
        source_label = "兼具天然源和人为源"
    elif human_count:
        source_label = "人为源"
    elif natural_count:
        source_label = "天然源"
    else:
        source_label = "证据不足"

    return {
        "compound": _display_compound(row),
        "cas": _clean_cell(row.get("cas")),
        "ec": _clean_cell(row.get("ec")),
        "smiles": _clean_cell(row.get("smiles")),
        "dtxsid": _clean_cell(row.get("dtxsid")),
        "echa_id": _clean_cell(row.get("echa_id")),
        "来源属性": source_label,
        "证据等级": _evidence_level(evidence_rows),
        "人为源证据数": human_count,
        "天然源证据数": natural_count,
        "主要人为源依据": _basis(evidence_rows, "human"),
        "主要天然源依据": _basis(evidence_rows, "natural"),
        "证据链接": _join_unique(item.get("record_url") for item in evidence_rows),
        "notes": _summary_notes(source_label, evidence_rows),
    }


def _count_evidence(evidence_rows, group):
    return sum(
        1
        for item in evidence_rows
        if item.get("source_group") == group and item.get("confidence") in COUNTED_CONFIDENCES
    )


def _evidence_level(evidence_rows):
    confidences = {_clean_cell(item.get("confidence")) for item in evidence_rows}
    if "strong" in confidences:
        return "强"
    if "medium" in confidences:
        return "中"
    if "weak" in confidences:
        return "弱"
    return "无"


def _basis(evidence_rows, group):
    labels = []
    for item in evidence_rows:
        if item.get("source_group") != group or item.get("confidence") not in COUNTED_CONFIDENCES:
            continue
        label = _clean_cell(item.get("evidence_label"))
        source = _clean_cell(item.get("source_name"))
        labels.append(f"{source}: {label}" if source and label else label or source)
    return _join_unique(labels[:3])


def _summary_notes(source_label, evidence_rows):
    if source_label == "证据不足":
        if evidence_rows:
            return "仅查到弱候选证据，不能支撑天然源或人为源判定。"
        return "当前接入数据源未返回足够来源证据。"
    return "来源属性由 EPA/ECHA 人为源证据与 ChEBI/COCONUT 天然源证据合并判定。"


def _external_warnings(input_df, errors_df, source_name):
    if not isinstance(errors_df, pd.DataFrame) or errors_df.empty:
        return []
    rows = []
    for _, error in errors_df.iterrows():
        rows.append(
            {
                "compound": _clean_cell(error.get("compound")),
                "cas": _clean_cell(error.get("cas")),
                "ec": _clean_cell(error.get("ec")),
                "smiles": _clean_cell(error.get("smiles")),
                "dtxsid": _clean_cell(error.get("dtxsid")),
                "echa_id": _clean_cell(error.get("echa_id")),
                "source_name": source_name,
                "stage": _clean_cell(error.get("stage")),
                "message": _clean_cell(error.get("message")),
            }
        )
    return rows


def _warning_row(row, source_name, stage, message):
    row = row if row is not None else {}
    return {
        "compound": _display_compound(row) if row is not None else "",
        "cas": _clean_cell(row.get("cas")) if hasattr(row, "get") else "",
        "ec": _clean_cell(row.get("ec")) if hasattr(row, "get") else "",
        "smiles": _clean_cell(row.get("smiles")) if hasattr(row, "get") else "",
        "dtxsid": _clean_cell(row.get("dtxsid")) if hasattr(row, "get") else "",
        "echa_id": _clean_cell(row.get("echa_id")) if hasattr(row, "get") else "",
        "source_name": source_name,
        "stage": stage,
        "message": _clean_cell(message),
    }


def _matching_rows(df, row):
    if not isinstance(df, pd.DataFrame) or df.empty or "compound" not in df.columns:
        return pd.DataFrame()
    key = _normalize_key(_display_compound(row))
    return df[df["compound"].map(lambda value: _normalize_key(value) == key)]


def _attach_compound(evidence_rows, compound):
    output = []
    for item in evidence_rows:
        row = item.copy()
        row["compound"] = compound
        output.append(row)
    return output


def _dedupe_evidence(evidence_rows):
    output = []
    seen = set()
    for item in evidence_rows:
        key = (
            item.get("source_group"),
            item.get("source_name"),
            item.get("evidence_type"),
            item.get("evidence_label"),
            item.get("matched_identifier"),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _natural_query_terms(row, include_cas=True):
    fields = ["resolved_name", "compound"]
    if include_cas:
        fields.append("cas")
    output = []
    seen = set()
    for field in fields:
        value = _clean_cell(row.get(field))
        if not value or value in seen:
            continue
        output.append(value)
        seen.add(value)
    return output


def _get_json(url, params=None, timeout=60):
    url = _build_url(url, params=params)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "ChemPriority source-origin module"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连接失败: {exc.reason}") from exc
    return json.loads(raw.decode("utf-8", errors="replace"))


def _get_json_with_retry(
    url,
    params=None,
    timeout=60,
    attempts=DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
):
    attempts = max(1, int(attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            return _get_json(url, params=params, timeout=timeout)
        except Exception as exc:
            if attempt >= attempts or not _is_retryable_error(exc):
                raise
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))


def _post_json(url, payload, timeout=60):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ChemPriority source-origin module",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连接失败: {exc.reason}") from exc
    return json.loads(raw.decode("utf-8", errors="replace"))


def _post_json_with_retry(
    url,
    payload,
    timeout=60,
    attempts=DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
):
    attempts = max(1, int(attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            return _post_json(url, payload, timeout=timeout)
        except Exception as exc:
            if attempt >= attempts or not _is_retryable_error(exc):
                raise
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))


def _is_retryable_error(exc):
    message = str(exc).lower()
    if re.search(r"http\s+(429|500|502|503|504)\b", message, flags=re.I):
        return True
    return any(marker in message for marker in RETRYABLE_ERROR_MARKERS)


def _build_url(path, params=None, base_url=None):
    if str(path).startswith(("http://", "https://")):
        url = str(path)
    else:
        base = base_url if base_url else ""
        if base and not base.endswith("/"):
            base += "/"
        url = urllib.parse.urljoin(base, str(path))
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def _comptox_url(dtxsid, source_type):
    dtxsid = _clean_cell(dtxsid)
    if not dtxsid:
        return ""
    path = "chemical/chemical-functional-use" if source_type == "functional_use" else "chemical/product-use-categories"
    return urllib.parse.urljoin(DEFAULT_DASHBOARD_BASE, f"{path}/{dtxsid}")


def _chebi_url(obo_id):
    obo_id = _clean_cell(obo_id)
    if not obo_id:
        return ""
    chebi_id = obo_id.replace("_", ":") if obo_id.startswith("CHEBI_") else obo_id
    return f"https://www.ebi.ac.uk/chebi/searchId.do?chebiId={urllib.parse.quote(chebi_id)}"


def _display_compound(row):
    if hasattr(row, "get"):
        for column in ("compound", "cas", "ec", "dtxsid", "echa_id", "smiles"):
            value = _clean_cell(row.get(column))
            if value:
                return value
    return "Unknown"


def _clean_cell(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "", _clean_cell(value).lower())


def _normalize_smiles(value):
    return re.sub(r"\s+", "", _clean_cell(value))


def _record_text_values(record, keys):
    values = []
    if not isinstance(record, dict):
        return values
    for key in keys:
        values.extend(_flatten_text_values(record.get(key)))
    return values


def _flatten_text_values(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        output = []
        for item in value:
            output.extend(_flatten_text_values(item))
        return output
    if isinstance(value, dict):
        output = []
        for item in value.values():
            output.extend(_flatten_text_values(item))
        return output
    text = _clean_cell(value)
    return [text] if text else []


def _first_text(*values):
    for value in values:
        text = _clean_cell(value)
        if text:
            return text
    return ""


def _join_nonempty(values, sep="；"):
    return sep.join(_clean_cell(value) for value in values if _clean_cell(value))


def _join_unique(values):
    output = []
    seen = set()
    for value in values:
        text = _clean_cell(value)
        if not text or text in seen:
            continue
        output.append(text)
        seen.add(text)
    return "；".join(output)


def _to_int(value):
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _ensure_columns(df, columns):
    output = df.copy()
    for column in columns:
        if column not in output.columns:
            output[column] = pd.NA
    return output[columns]
