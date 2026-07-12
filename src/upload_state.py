"""Small helpers for keeping Streamlit uploads in session state."""

import hashlib
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
