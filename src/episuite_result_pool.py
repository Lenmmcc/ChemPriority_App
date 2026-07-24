from __future__ import annotations

import pandas as pd

from src.episuite_io import ENDPOINT_KEYS


POOL_KEY = "shared_epi_result_pool"
POOL_VERSION = 1


def upsert_epi_pool(state, contributor_id, results, provenance) -> None:
    pool = dict(state.get(POOL_KEY) or {})
    contributors = dict(pool.get("contributors") or {})
    contributors[str(contributor_id)] = {
        "results": pd.DataFrame(results).to_dict("records"),
        "provenance": pd.DataFrame(provenance).to_dict("records"),
    }
    state[POOL_KEY] = {
        "version": POOL_VERSION,
        "contributors": contributors,
    }


def read_epi_pool(state) -> tuple[pd.DataFrame, pd.DataFrame]:
    pool = state.get(POOL_KEY) or {}
    if pool.get("version") != POOL_VERSION:
        return pd.DataFrame(), pd.DataFrame()
    result_frames = []
    provenance_frames = []
    for contributor_id, payload in (pool.get("contributors") or {}).items():
        results = pd.DataFrame(payload.get("results") or [])
        provenance = pd.DataFrame(payload.get("provenance") or [])
        if not results.empty:
            results["pool_contributor_id"] = contributor_id
            result_frames.append(results)
        if not provenance.empty:
            provenance["pool_contributor_id"] = contributor_id
            provenance_frames.append(provenance)
    return (
        pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame(),
        pd.concat(provenance_frames, ignore_index=True)
        if provenance_frames
        else pd.DataFrame(),
    )


def build_api_epi_pool_payload(results, source_file) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.DataFrame(results).copy()
    if "status" in frame.columns:
        frame = frame.loc[frame["status"].eq("success")].copy()
    return _with_source_metadata(frame, source_type="api", source_file=source_file)


def build_uploaded_epi_pool_payload(merged_results) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.DataFrame(merged_results).copy()
    if "source_file" not in frame.columns:
        return pd.DataFrame(), pd.DataFrame()

    source_files = frame["source_file"].astype("string").str.strip()
    matched = frame.loc[source_files.notna() & source_files.ne("")].copy()
    endpoint_columns = [column for column in ENDPOINT_KEYS if column in matched.columns]
    if not endpoint_columns:
        return pd.DataFrame(), pd.DataFrame()
    adoptable = matched[endpoint_columns].notna().any(axis=1)
    matched = matched.loc[adoptable].copy()
    return _with_source_metadata(matched, source_type="uploaded")


def remove_stale_epi_pool_contributor(
    state, contributor_state_key, next_contributor_id
) -> bool:
    previous = state.get(contributor_state_key)
    if not previous or previous == str(next_contributor_id):
        return False
    remove_epi_pool_contributor(state, previous)
    state.pop(contributor_state_key, None)
    return True


def remove_epi_pool_contributor(state, contributor_id) -> None:
    pool = dict(state.get(POOL_KEY) or {})
    contributors = dict(pool.get("contributors") or {})
    contributors.pop(str(contributor_id), None)
    if contributors:
        state[POOL_KEY] = {
            "version": POOL_VERSION,
            "contributors": contributors,
        }
    else:
        state.pop(POOL_KEY, None)


def clear_epi_pool(state) -> None:
    state.pop(POOL_KEY, None)


def _with_source_metadata(
    results: pd.DataFrame,
    source_type: str,
    source_file=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    prepared = results.copy()
    prepared["source_type"] = source_type
    if source_file is not None:
        prepared["source_file"] = source_file
    elif "source_file" not in prepared.columns:
        prepared["source_file"] = pd.NA
    for column in ("source_sheet", "source_row"):
        if column not in prepared.columns:
            prepared[column] = pd.NA
    provenance_columns = [
        column
        for column in (
            "compound",
            "smiles",
            "cas",
            "source_type",
            "source_file",
            "source_sheet",
            "source_row",
        )
        if column in prepared.columns
    ]
    return prepared, prepared[provenance_columns].copy()
