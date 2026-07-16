"""Small helpers for keeping Streamlit uploads in session state."""

import hashlib
import json
from collections.abc import Mapping


def upload_name(upload):
    """Return the file name from a Streamlit upload or cached record."""
    if isinstance(upload, Mapping):
        return str(upload["name"])
    return str(upload.name)


def upload_bytes(upload):
    """Return immutable file bytes from a Streamlit upload or cached record."""
    if isinstance(upload, Mapping):
        return bytes(upload["bytes"])
    return bytes(upload.getvalue())


def upload_signature(files):
    """Build an order-sensitive signature from file names and contents."""
    digest = hashlib.sha256()
    for upload in files:
        payload = upload_bytes(upload)
        digest.update(upload_name(upload).encode("utf-8", errors="ignore"))
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def store_uploads(state, files_key, signature_key, files):
    """Store serializable upload records and report whether input changed."""
    records = [
        {"name": upload_name(upload), "bytes": upload_bytes(upload)}
        for upload in files
    ]
    signature = upload_signature(records)
    changed = state.get(signature_key) != signature
    state[files_key] = records
    state[signature_key] = signature
    return records, changed


def cached_uploads(state, files_key):
    """Return cached records or an empty list when no upload is stored."""
    return list(state.get(files_key) or [])


def clear_uploads(state, keys):
    """Remove named input or dependent-result state keys."""
    for key in keys:
        state.pop(key, None)


def settings_signature(settings):
    """Build a stable signature from JSON-compatible result settings."""
    payload = json.dumps(
        settings,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def invalidate_recovered_results_on_settings_mismatch(
    state,
    current_settings_signature,
    result_keys,
    checkpoint_keys,
    manifest_key="auto_query_checkpoint_manifest",
):
    """Drop restored session artifacts when their settings no longer match."""
    manifest = state.get(manifest_key)
    recovered_signature = (
        manifest.get("settings_signature") if isinstance(manifest, Mapping) else None
    )
    mismatch = bool(
        recovered_signature
        and current_settings_signature
        and recovered_signature != current_settings_signature
    )
    if mismatch:
        clear_uploads(state, (*result_keys, *checkpoint_keys))
    return mismatch


def invalidate_results_on_settings_change(state, signature_key, settings, result_keys):
    """Clear dependent results when an existing settings signature changes."""
    signature = settings_signature(settings)
    previous = state.get(signature_key)
    changed = previous is not None and previous != signature
    if changed:
        clear_uploads(state, result_keys)
    state[signature_key] = signature
    return changed
