from __future__ import annotations

import re

import pandas as pd


_TRANSIENT_HTTP = re.compile(r"\bHTTP\s+(408|425|429|5\d\d)\b", re.IGNORECASE)
_TRANSIENT_TEXT = (
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "temporary failure",
    "name resolution",
    "remote end closed",
    "network is unreachable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "too many requests",
    "rate limit",
)


def is_transient_query_error(error: object) -> bool:
    if error is None:
        return False
    text = str(error).strip().lower()
    return bool(_TRANSIENT_HTTP.search(text)) or any(
        token in text for token in _TRANSIENT_TEXT
    )


def warning_frame_has_transient_error(frame: pd.DataFrame | None) -> bool:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False
    for column in ("message", "error"):
        if column in frame.columns and frame[column].map(is_transient_query_error).any():
            return True
    return False
