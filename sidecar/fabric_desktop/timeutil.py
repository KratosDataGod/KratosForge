"""Times as the app keeps them: aware, in UTC, written to the second.

One rule for reading a time, so two times can always be compared or subtracted: a time without an
offset is UTC. Fabric's ``startTimeUtc`` and ``endTimeUtc`` carry no offset, and the app writes its
own times with ``+00:00``; read any other way, the two can't be subtracted (Python raises TypeError).
A missing or unreadable time is ``None``, never an error.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

UTC = dt.timezone.utc


def now_iso() -> str:
    """Now, as the app writes times: ``2026-11-16T20:30:00+00:00``."""
    return dt.datetime.now(UTC).isoformat(timespec="seconds")


def parse_time(value: Any) -> dt.datetime | None:
    """``value`` as an aware time, or None when it's missing or unreadable.

    Takes an ISO 8601 string (``Z`` or an offset, or none, which is UTC), a datetime (naive is UTC),
    or epoch seconds."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, bool) or not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        t = dt.datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def iso(value: Any) -> str | None:
    """``value`` (anything parse_time reads) as the app writes times, in UTC; None when unreadable."""
    t = parse_time(value)
    return t.astimezone(UTC).isoformat(timespec="seconds") if t else None


def seconds_between(start: Any, end: Any) -> float | None:
    """Seconds from ``start`` to ``end``, or None when either is missing or unreadable."""
    a, b = parse_time(start), parse_time(end)
    return (b - a).total_seconds() if a and b else None
