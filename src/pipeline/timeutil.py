"""All pipeline timestamps are stored and compared as UTC epoch milliseconds,
matching USGS's own `time`/`updated` fields exactly. That sidesteps timezone
round-trip bugs across SQLite (no native tz type) and Postgres — comparisons
are just integer comparisons. ISO 8601 strings only appear at the USGS HTTP
API boundary and in the digest email, where humans need to read them.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def iso_to_ms(value: str) -> int:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
