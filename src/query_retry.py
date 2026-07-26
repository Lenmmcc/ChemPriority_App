from __future__ import annotations

import re
import random

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
    "winerror 10054",
    "远程主机强迫关闭",
    "forcibly closed by remote host",
    "ssleoferror",
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
)


def is_transient_query_error(error: object) -> bool:
    if error is None:
        return False
    text = str(error).strip().lower()
    return bool(_TRANSIENT_HTTP.search(text)) or any(
        token in text for token in _TRANSIENT_TEXT
    )


def transient_retry_delay(
    completed_attempt: int,
    *,
    base_seconds: float = 1.0,
    jitter_fraction: float = 0.2,
    random_value: float | None = None,
) -> float:
    completed = max(1, int(completed_attempt))
    base = max(0.0, float(base_seconds)) * (2 ** (completed - 1))
    fraction = min(1.0, max(0.0, float(jitter_fraction)))
    draw = (
        random.random()
        if random_value is None
        else min(1.0, max(0.0, float(random_value)))
    )
    return base + base * fraction * draw


def warning_frame_has_transient_error(frame: pd.DataFrame | None) -> bool:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False
    for column in ("message", "error"):
        if column in frame.columns and frame[column].map(is_transient_query_error).any():
            return True
    return False
