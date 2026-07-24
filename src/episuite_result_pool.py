from __future__ import annotations

import pandas as pd


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
