from __future__ import annotations

from dataclasses import dataclass, field
import io
from pathlib import Path

import pandas as pd

from src.episuite_io import ENDPOINT_KEYS, parse_table_result


RECOGNIZED_RESULT_SHEETS = ("Core_Summary", "EPI_Results")
CORE_MODEL_FIELDS = (
    "molecular_weight",
    "henry_atm_m3_mol",
    "log_kow",
    "level3_air_half_life_hours",
    "level3_water_half_life_hours",
    "level3_soil_half_life_hours",
    "log_baf",
)

_IDENTITY_COLUMNS = ("compound", "smiles", "cas")
_SOURCE_METADATA_COLUMNS = {
    "primary_file",
    "source_file",
    "source_sheet",
    "source_row",
    "source_priority",
    "source_type",
}
_AUDIT_METADATA_COLUMNS = (
    "_compound_key",
    "compound",
    "field",
    "value",
    "source_type",
    "source_file",
    "source_sheet",
    "source_row",
    "source_priority",
)


@dataclass(frozen=True)
class EPIWorkbookInspection:
    file_name: str
    sheet_names: tuple[str, ...]
    default_result_sheet: str | None


@dataclass(frozen=True)
class EPISupplementMapping:
    source_file: str
    primary_file: str
    sheet_name: str
    compound_col: str | None = None
    smiles_col: str | None = None
    cas_col: str | None = None
    endpoint_columns: dict[str, str] = field(default_factory=dict)
    priority: int = 0


@dataclass
class EPIResolution:
    results: pd.DataFrame
    raw_results: pd.DataFrame
    errors: pd.DataFrame
    completeness: pd.DataFrame
    provenance: pd.DataFrame
    match_audit: pd.DataFrame
    conflict_audit: pd.DataFrame
    query_attempts: pd.DataFrame
    query_input: pd.DataFrame


def inspect_epi_workbook(data: bytes, file_name: str) -> EPIWorkbookInspection:
    workbook = pd.ExcelFile(io.BytesIO(data))
    default_sheet = next(
        (name for name in RECOGNIZED_RESULT_SHEETS if name in workbook.sheet_names),
        None,
    )
    return EPIWorkbookInspection(
        file_name=str(file_name),
        sheet_names=tuple(workbook.sheet_names),
        default_result_sheet=default_sheet,
    )


def parse_epi_supplement(
    data: bytes,
    mapping: EPISupplementMapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.read_excel(io.BytesIO(data), sheet_name=mapping.sheet_name)
    rename_map = {
        source: target
        for target, source in {
            "compound": mapping.compound_col,
            "smiles": mapping.smiles_col,
            "cas": mapping.cas_col,
            **mapping.endpoint_columns,
        }.items()
        if source and source in frame.columns
    }
    normalized = frame.rename(columns=rename_map)
    if "log_kow" not in normalized.columns:
        experimental = normalized.get("log_kow_experimental")
        estimated = normalized.get("log_kow_estimated")
        if experimental is not None:
            normalized["log_kow"] = (
                experimental.combine_first(estimated)
                if estimated is not None
                else experimental
            )
        elif estimated is not None:
            normalized["log_kow"] = estimated
    parsed, warnings = parse_table_result(
        normalized,
        source_name=mapping.source_file,
    )
    for column in CORE_MODEL_FIELDS:
        if column in normalized.columns:
            parsed[column] = pd.to_numeric(
                normalized[column].reset_index(drop=True),
                errors="coerce",
            )
    for column in ("status", "error", "query_note"):
        if column in normalized.columns:
            parsed[column] = normalized[column].reset_index(drop=True)
    parsed["primary_file"] = mapping.primary_file
    parsed["source_sheet"] = mapping.sheet_name
    parsed["source_priority"] = int(mapping.priority)
    for endpoint in ENDPOINT_KEYS:
        if endpoint not in parsed.columns:
            parsed[endpoint] = pd.NA
    return parsed, warnings


def clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def normalize_cas(value) -> str:
    text = clean_text(value)
    return text.replace(" ", "")


def normalize_smiles(value) -> str:
    return clean_text(value)


def normalize_name(value) -> str:
    return " ".join(clean_text(value).casefold().split())


def suggest_primary_filename(
    supplement_name: str,
    primary_names: list[str],
) -> str | None:
    suffixes = ("_episuite_fate_report", "_episuite", "_epi")
    stem = Path(supplement_name).stem.casefold()
    for suffix in suffixes:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    candidates = [
        name for name in primary_names if Path(name).stem.casefold() == stem
    ]
    return candidates[0] if len(candidates) == 1 else None


def prepare_universe(
    compound_universe: pd.DataFrame,
    completed_identifiers: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if compound_universe is None:
        compound_universe = pd.DataFrame()
    universe = compound_universe.copy().reset_index(drop=True)
    for column in _IDENTITY_COLUMNS:
        if column not in universe.columns:
            universe[column] = ""
        universe[column] = universe[column].map(clean_text)
    if "molecular_weight" not in universe.columns:
        universe["molecular_weight"] = pd.NA
    universe["_compound_key"] = [
        f"compound:{position}" for position in range(len(universe))
    ]

    completed = (
        completed_identifiers.copy().reset_index(drop=True)
        if isinstance(completed_identifiers, pd.DataFrame)
        else pd.DataFrame()
    )
    if completed.empty or universe.empty:
        return universe

    for column in _IDENTITY_COLUMNS:
        if column not in completed.columns:
            completed[column] = ""
        completed[column] = completed[column].map(clean_text)
    indexes = _universe_indexes(universe)
    for _, row in completed.iterrows():
        _, status, compound_key = _match_row(row, indexes)
        if status != "matched":
            continue
        position = universe.index[universe["_compound_key"].eq(compound_key)][0]
        for column in ("smiles", "cas"):
            if not clean_text(universe.at[position, column]):
                universe.at[position, column] = clean_text(row.get(column))
        molecular_weight = row.get("molecular_weight", pd.NA)
        if _is_null(molecular_weight):
            molecular_weight = row.get("pubchem_molecular_weight", pd.NA)
        if _is_null(universe.at[position, "molecular_weight"]) and not _is_null(
            molecular_weight
        ):
            universe.at[position, "molecular_weight"] = molecular_weight
    return universe


def prepare_source(
    source_results: pd.DataFrame,
    source_type: str,
    priority_default: int,
) -> pd.DataFrame:
    source = (
        source_results.copy().reset_index(drop=True)
        if isinstance(source_results, pd.DataFrame)
        else pd.DataFrame()
    )
    for column in _IDENTITY_COLUMNS:
        if column not in source.columns:
            source[column] = ""
        source[column] = source[column].map(clean_text)
    if "source_file" not in source.columns:
        source["source_file"] = ""
    if "source_sheet" not in source.columns:
        source["source_sheet"] = ""
    if "source_row" not in source.columns:
        source["source_row"] = source.index + 1
    if "source_priority" not in source.columns:
        source["source_priority"] = priority_default
    source["source_priority"] = pd.to_numeric(
        source["source_priority"], errors="coerce"
    ).fillna(priority_default)
    source["source_type"] = source_type
    source["_source_rank"] = {
        "uploaded": 0,
        "session_pool": 1,
        "network": 2,
    }.get(source_type, 3)
    source["_source_order"] = range(len(source))
    return source


def match_sources(
    universe: pd.DataFrame,
    sources: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    audit_columns = [
        "source_type",
        "source_file",
        "source_sheet",
        "source_row",
        "source_priority",
        "compound",
        "smiles",
        "cas",
        "match_method",
        "match_status",
        "_compound_key",
    ]
    if sources is None or sources.empty:
        return pd.DataFrame(), pd.DataFrame(columns=audit_columns)

    indexes = _universe_indexes(universe)
    ambiguous_source_smiles = _ambiguous_source_smiles(sources)
    matched_rows = []
    audit_rows = []
    for _, source_row in sources.iterrows():
        ignored_smiles = (
            {normalize_smiles(source_row.get("smiles"))}
            if _source_smiles_group(source_row) in ambiguous_source_smiles
            else set()
        )
        method, status, compound_key = _match_row(
            source_row,
            indexes,
            ignored_smiles=ignored_smiles,
        )
        audit_rows.append(
            {
                "source_type": source_row.get("source_type", ""),
                "source_file": source_row.get("source_file", ""),
                "source_sheet": source_row.get("source_sheet", ""),
                "source_row": source_row.get("source_row", pd.NA),
                "source_priority": source_row.get("source_priority", pd.NA),
                "compound": source_row.get("compound", ""),
                "smiles": source_row.get("smiles", ""),
                "cas": source_row.get("cas", ""),
                "match_method": method,
                "match_status": status,
                "_compound_key": compound_key if status == "matched" else pd.NA,
            }
        )
        if status == "matched":
            matched = source_row.to_dict()
            matched["_compound_key"] = compound_key
            matched["_match_method"] = method
            matched["_match_status"] = status
            matched_rows.append(matched)
    return pd.DataFrame(matched_rows), pd.DataFrame(
        audit_rows,
        columns=audit_columns,
    )


def merge_matched_fields(
    universe: pd.DataFrame,
    matched: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    results = _base_results(universe)
    provenance_rows = []
    conflict_rows = []
    if matched is None or matched.empty:
        return (
            _ensure_result_columns(results),
            pd.DataFrame(columns=_AUDIT_METADATA_COLUMNS),
            pd.DataFrame(columns=_conflict_columns()),
        )

    value_fields = _source_value_fields(matched)
    for field_name in value_fields:
        if field_name not in results.columns:
            results[field_name] = pd.NA

    ordered = matched.sort_values(
        ["_source_rank", "source_priority", "_source_order"],
        kind="stable",
    )
    positions = {
        compound_key: position
        for position, compound_key in results["_compound_key"].items()
    }
    for compound_key, source_group in ordered.groupby(
        "_compound_key",
        sort=False,
    ):
        position = positions.get(compound_key)
        if position is None:
            continue
        for field_name in value_fields:
            candidates = [
                row
                for _, row in source_group.iterrows()
                if field_name in row.index and not _is_null(row[field_name])
            ]
            if not candidates:
                continue
            adopted = candidates[0]
            results.at[position, field_name] = adopted[field_name]
            provenance_rows.append(
                _provenance_row(compound_key, field_name, adopted)
            )
            for candidate in candidates[1:]:
                if _values_equal(adopted[field_name], candidate[field_name]):
                    continue
                conflict_rows.append(
                    _conflict_row(
                        compound_key,
                        field_name,
                        adopted,
                        candidate,
                    )
                )
    return (
        _ensure_result_columns(results),
        pd.DataFrame(provenance_rows, columns=_AUDIT_METADATA_COLUMNS),
        pd.DataFrame(conflict_rows, columns=_conflict_columns()),
    )


def classify_completeness(
    universe: pd.DataFrame,
    results: pd.DataFrame,
    require_core: bool = False,
) -> pd.DataFrame:
    working = _ensure_result_columns(results.copy())
    recognized = working[list(ENDPOINT_KEYS)].notna().any(axis=1)
    explicit_failure = working.get(
        "status",
        pd.Series("", index=working.index, dtype="string"),
    ).astype("string").str.casefold().eq("failed").fillna(False)
    core_complete = working[list(CORE_MODEL_FIELDS)].apply(
        pd.to_numeric,
        errors="coerce",
    ).notna().all(axis=1)
    complete = recognized & ~explicit_failure
    if require_core:
        complete &= core_complete

    completeness = pd.DataFrame(
        {
            "_compound_key": working["_compound_key"],
            "compound": working["compound"],
            "recognized_endpoint": recognized,
            "explicit_failure": explicit_failure,
            "core_complete": core_complete,
            "complete": complete,
            "needs_query": ~complete,
            "require_core": bool(require_core),
        }
    )
    completeness["missing_core_fields"] = [
        ", ".join(
            field_name
            for field_name in CORE_MODEL_FIELDS
            if _is_null(working.at[position, field_name])
            or pd.isna(
                pd.to_numeric(
                    pd.Series([working.at[position, field_name]]),
                    errors="coerce",
                ).iloc[0]
            )
        )
        for position in working.index
    ]
    return completeness


def resolve_epi_sources(
    compound_universe: pd.DataFrame,
    uploaded_results: pd.DataFrame,
    pool_results: pd.DataFrame,
    completed_identifiers: pd.DataFrame | None = None,
    require_core: bool = False,
) -> EPIResolution:
    universe = prepare_universe(compound_universe, completed_identifiers)
    uploaded = prepare_source(
        uploaded_results,
        "uploaded",
        priority_default=0,
    )
    pool = prepare_source(
        pool_results,
        "session_pool",
        priority_default=10_000,
    )
    matched, match_audit = match_sources(
        universe,
        _append_frames(uploaded, pool),
    )
    results, provenance, conflicts = merge_matched_fields(universe, matched)
    completeness = classify_completeness(
        universe,
        results,
        require_core=require_core,
    )
    query_input = _query_input(universe, completeness)
    return EPIResolution(
        results=results,
        raw_results=pd.DataFrame(),
        errors=pd.DataFrame(),
        completeness=completeness,
        provenance=provenance,
        match_audit=match_audit,
        conflict_audit=conflicts,
        query_attempts=pd.DataFrame(),
        query_input=query_input,
    )


def merge_network_epi(
    resolution: EPIResolution,
    network_results: pd.DataFrame,
    network_raw: pd.DataFrame,
    network_errors: pd.DataFrame,
    attempt_events=(),
) -> EPIResolution:
    results = resolution.results.copy().reset_index(drop=True)
    universe = results[
        [
            column
            for column in results.columns
            if not column.startswith("_") or column == "_compound_key"
        ]
    ].copy()
    network = prepare_source(
        network_results,
        "network",
        priority_default=20_000,
    )
    network["source_priority"] = 20_000
    matched, network_match_audit = match_sources(universe, network)
    (
        results,
        network_provenance,
        network_conflicts,
    ) = _merge_network_fields(
        results,
        matched,
        resolution.provenance,
    )
    require_core = (
        bool(resolution.completeness["require_core"].iloc[0])
        if (
            not resolution.completeness.empty
            and "require_core" in resolution.completeness.columns
        )
        else False
    )
    completeness = classify_completeness(
        universe,
        results,
        require_core=require_core,
    )
    return EPIResolution(
        results=results,
        raw_results=_append_frames(resolution.raw_results, network_raw),
        errors=_append_frames(resolution.errors, network_errors),
        completeness=completeness,
        provenance=_append_frames(
            resolution.provenance,
            network_provenance,
        ),
        match_audit=_append_frames(
            resolution.match_audit,
            network_match_audit,
        ),
        conflict_audit=_append_frames(
            resolution.conflict_audit,
            network_conflicts,
        ),
        query_attempts=_append_frames(
            resolution.query_attempts,
            _attempt_events_frame(attempt_events),
        ),
        query_input=_query_input(results, completeness),
    )


def _universe_indexes(universe: pd.DataFrame) -> dict[str, dict[str, list[str]]]:
    indexes = {"cas": {}, "smiles": {}, "compound": {}}
    normalizers = {
        "cas": normalize_cas,
        "smiles": normalize_smiles,
        "compound": normalize_name,
    }
    for _, row in universe.iterrows():
        compound_key = row["_compound_key"]
        for column, normalizer in normalizers.items():
            value = normalizer(row.get(column))
            if value:
                indexes[column].setdefault(value, []).append(compound_key)
    return indexes


def _match_row(
    row: pd.Series,
    indexes: dict[str, dict[str, list[str]]],
    ignored_smiles: set[str] | None = None,
) -> tuple[str, str, str | None]:
    normalizers = (
        ("cas", normalize_cas),
        ("smiles", normalize_smiles),
        ("compound", normalize_name),
    )
    matches = []
    for method, normalizer in normalizers:
        value = normalizer(row.get(method))
        if method == "smiles" and value in (ignored_smiles or set()):
            continue
        candidates = set(indexes[method].get(value, ())) if value else set()
        if candidates:
            matches.append((method, candidates))
    if not matches:
        return "", "unmatched", None

    method, candidates = matches[0]
    if len(candidates) != 1:
        return method, "ambiguous", None
    compound_key = next(iter(candidates))
    return method, "matched", compound_key


def _source_smiles_group(row: pd.Series) -> tuple[str, str, str, str]:
    return (
        clean_text(row.get("source_type")),
        clean_text(row.get("source_file")),
        clean_text(row.get("source_sheet")),
        normalize_smiles(row.get("smiles")),
    )


def _ambiguous_source_smiles(
    sources: pd.DataFrame,
) -> set[tuple[str, str, str, str]]:
    names_by_group = {}
    for _, row in sources.iterrows():
        group = _source_smiles_group(row)
        smiles = group[-1]
        name = normalize_name(row.get("compound"))
        if smiles and name:
            names_by_group.setdefault(group, set()).add(name)
    return {
        group
        for group, names in names_by_group.items()
        if len(names) > 1
    }


def _base_results(universe: pd.DataFrame) -> pd.DataFrame:
    columns = [
        column
        for column in universe.columns
        if not column.startswith("_") or column == "_compound_key"
    ]
    return universe[columns].copy().reset_index(drop=True)


def _ensure_result_columns(results: pd.DataFrame) -> pd.DataFrame:
    for column in (*_IDENTITY_COLUMNS, "_compound_key"):
        if column not in results.columns:
            results[column] = ""
    for column in dict.fromkeys([*ENDPOINT_KEYS, *CORE_MODEL_FIELDS]):
        if column not in results.columns:
            results[column] = pd.NA
    return results


def _source_value_fields(source: pd.DataFrame) -> list[str]:
    excluded = {
        *_IDENTITY_COLUMNS,
        *_SOURCE_METADATA_COLUMNS,
        "_source_rank",
        "_source_order",
        "_compound_key",
        "_match_method",
        "_match_status",
    }
    return [
        column
        for column in source.columns
        if column not in excluded and not column.startswith("_")
    ]


def _is_null(value) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(result)
    except (TypeError, ValueError):
        return False


def _values_equal(first, second) -> bool:
    if _is_null(first) or _is_null(second):
        return _is_null(first) and _is_null(second)
    try:
        result = first == second
    except (TypeError, ValueError):
        return False
    try:
        return bool(result)
    except (TypeError, ValueError):
        return False


def _provenance_row(
    compound_key: str,
    field_name: str,
    source_row: pd.Series,
) -> dict:
    return {
        "_compound_key": compound_key,
        "compound": source_row.get("compound", ""),
        "field": field_name,
        "value": source_row.get(field_name, pd.NA),
        "source_type": source_row.get("source_type", ""),
        "source_file": source_row.get("source_file", ""),
        "source_sheet": source_row.get("source_sheet", ""),
        "source_row": source_row.get("source_row", pd.NA),
        "source_priority": source_row.get("source_priority", pd.NA),
    }


def _conflict_columns() -> list[str]:
    return [
        "_compound_key",
        "field",
        "adopted_value",
        "adopted_source_type",
        "adopted_source_file",
        "adopted_source_sheet",
        "adopted_source_row",
        "adopted_source_priority",
        "candidate_value",
        "candidate_source_type",
        "candidate_source_file",
        "candidate_source_sheet",
        "candidate_source_row",
        "candidate_source_priority",
    ]


def _conflict_row(
    compound_key: str,
    field_name: str,
    adopted: pd.Series | dict,
    candidate: pd.Series | dict,
) -> dict:
    return {
        "_compound_key": compound_key,
        "field": field_name,
        "adopted_value": adopted.get(field_name, adopted.get("value", pd.NA)),
        "adopted_source_type": adopted.get("source_type", ""),
        "adopted_source_file": adopted.get("source_file", ""),
        "adopted_source_sheet": adopted.get("source_sheet", ""),
        "adopted_source_row": adopted.get("source_row", pd.NA),
        "adopted_source_priority": adopted.get("source_priority", pd.NA),
        "candidate_value": candidate.get(field_name, candidate.get("value", pd.NA)),
        "candidate_source_type": candidate.get("source_type", ""),
        "candidate_source_file": candidate.get("source_file", ""),
        "candidate_source_sheet": candidate.get("source_sheet", ""),
        "candidate_source_row": candidate.get("source_row", pd.NA),
        "candidate_source_priority": candidate.get("source_priority", pd.NA),
    }


def _query_input(
    universe: pd.DataFrame,
    completeness: pd.DataFrame,
) -> pd.DataFrame:
    query_keys = set(
        completeness.loc[completeness["needs_query"], "_compound_key"]
    )
    return universe.loc[
        universe["_compound_key"].isin(query_keys),
        ["compound", "smiles", "cas"],
    ].reset_index(drop=True)


def _merge_network_fields(
    results: pd.DataFrame,
    matched: pd.DataFrame,
    existing_provenance: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    merged = _ensure_result_columns(results.copy())
    provenance_rows = []
    conflict_rows = []
    if matched is None or matched.empty:
        return (
            merged,
            pd.DataFrame(columns=_AUDIT_METADATA_COLUMNS),
            pd.DataFrame(columns=_conflict_columns()),
        )

    value_fields = _source_value_fields(matched)
    for field_name in value_fields:
        if field_name not in merged.columns:
            merged[field_name] = pd.NA
    positions = {
        compound_key: position
        for position, compound_key in merged["_compound_key"].items()
    }
    ordered = matched.sort_values(
        ["source_priority", "_source_order"],
        kind="stable",
    )
    for compound_key, source_group in ordered.groupby(
        "_compound_key",
        sort=False,
    ):
        position = positions.get(compound_key)
        if position is None:
            continue
        for field_name in value_fields:
            for _, candidate in source_group.iterrows():
                candidate_value = candidate.get(field_name, pd.NA)
                if _is_null(candidate_value):
                    continue
                adopted_value = merged.at[position, field_name]
                if (
                    field_name == "status"
                    and clean_text(adopted_value).casefold() == "failed"
                    and clean_text(candidate_value).casefold() != "failed"
                ):
                    # Status is query metadata, not a user-supplied model value.
                    merged.at[position, field_name] = candidate_value
                    provenance_rows.append(
                        _provenance_row(
                            compound_key,
                            field_name,
                            candidate,
                        )
                    )
                    continue
                if _is_null(adopted_value):
                    merged.at[position, field_name] = candidate_value
                    provenance_rows.append(
                        _provenance_row(
                            compound_key,
                            field_name,
                            candidate,
                        )
                    )
                    continue
                if _values_equal(adopted_value, candidate_value):
                    continue
                adopted = _adopted_source(
                    existing_provenance,
                    provenance_rows,
                    compound_key,
                    field_name,
                    adopted_value,
                )
                conflict_rows.append(
                    _conflict_row(
                        compound_key,
                        field_name,
                        adopted,
                        candidate,
                    )
                )
    return (
        merged,
        pd.DataFrame(provenance_rows, columns=_AUDIT_METADATA_COLUMNS),
        pd.DataFrame(conflict_rows, columns=_conflict_columns()),
    )


def _adopted_source(
    existing_provenance: pd.DataFrame,
    new_provenance: list[dict],
    compound_key: str,
    field_name: str,
    value,
) -> dict:
    combined = _append_frames(
        existing_provenance,
        pd.DataFrame(new_provenance),
    )
    if not combined.empty and {"_compound_key", "field"}.issubset(combined.columns):
        matches = combined.loc[
            combined["_compound_key"].eq(compound_key)
            & combined["field"].eq(field_name)
        ]
        if not matches.empty:
            return matches.iloc[-1].to_dict()
    return {
        "value": value,
        "source_type": "existing",
        "source_file": "",
        "source_sheet": "",
        "source_row": pd.NA,
        "source_priority": pd.NA,
    }


def _append_frames(first: pd.DataFrame, second: pd.DataFrame) -> pd.DataFrame:
    frames = [
        frame
        for frame in (first, second)
        if isinstance(frame, pd.DataFrame) and not frame.empty
    ]
    if not frames:
        columns = (
            list(first.columns)
            if isinstance(first, pd.DataFrame)
            else list(second.columns)
            if isinstance(second, pd.DataFrame)
            else []
        )
        return pd.DataFrame(columns=columns)
    columns = list(
        dict.fromkeys(
            column
            for frame in frames
            for column in frame.columns
        )
    )
    records = [
        record
        for frame in frames
        for record in frame.to_dict(orient="records")
    ]
    return pd.DataFrame.from_records(records, columns=columns)


def _attempt_events_frame(attempt_events) -> pd.DataFrame:
    attempts = {}
    order = []
    for event in attempt_events or ():
        row = dict(event)
        key = (
            row.get("index"),
            row.get("attempt"),
            row.get("label"),
        )
        if key not in attempts:
            attempts[key] = row
            order.append(key)
        else:
            attempts[key].update(row)
    rows = [attempts[key] for key in order]
    return pd.DataFrame(rows)
