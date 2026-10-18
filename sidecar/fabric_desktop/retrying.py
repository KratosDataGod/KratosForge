"""Which errors are worth another try: the network, throttling, a busy table, a lost commit race,
Windows refusing a Delta log file another writer is renaming.

Everything else (a check that failed, bad config, a missing table, no permission) fails the same
way the next time, so the agent reports it instead of retrying it.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import math
import re
import time
from typing import Any, Callable, Mapping, TypeVar

# The one list of HTTP statuses worth another try. The Fabric client, the agent, the integrations API
# and the workload screens all read it, so they agree on what a passing outage is.
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}

# The longest a server's Retry-After can make a client wait before one retry. A larger value (or an
# HTTP date hours away) is cut to this, so a misbehaving server can't stall the engine.
RETRY_AFTER_CAP = 60.0
# The shortest wait between tries, so "retry now" (0, or a date already past) never retries back to back.
RETRY_AFTER_FLOOR = 0.5

# Methods that are safe to send again after a 5xx or a dropped connection: they read and change nothing.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_DELTA_SECONDS = re.compile(r"\d+(\.\d+)?")


def retry_after(headers: Mapping[str, Any] | None, default: float, cap: float = RETRY_AFTER_CAP,
                now: dt.datetime | None = None) -> float:
    """How long to wait before another try, from a response's ``Retry-After`` header.

    The header is either delta-seconds ("120") or an HTTP date ("Wed, 21 Oct 2026 07:28:00 GMT",
    RFC 9110 10.2.3). A date already past means retry now. A missing, negative or unreadable
    value gives ``default``. The result is never less than RETRY_AFTER_FLOOR or more than ``cap``."""
    return max(RETRY_AFTER_FLOOR, _retry_after(headers, default, cap, now))


def _retry_after(headers: Mapping[str, Any] | None, default: float, cap: float,
                 now: dt.datetime | None) -> float:
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return min(default, cap)
    text = str(raw).strip()
    if _DELTA_SECONDS.fullmatch(text):
        wait = float(text)
        return min(wait, cap) if math.isfinite(wait) else min(default, cap)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return min(default, cap)
    if when is None:
        return min(default, cap)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)  # HTTP dates are always GMT
    wait = (when - (now or dt.datetime.now(dt.timezone.utc))).total_seconds()
    return max(0.0, min(wait, cap))


def should_retry(method: str, status: int) -> bool:
    """Whether a response is worth sending again. 429 means the server refused the request unread,
    so any method can be repeated. Other transient statuses only for reads: a write that got a 5xx
    may have happened, and repeating it could create or change something twice."""
    return status == 429 or (method.upper() in SAFE_METHODS and status in TRANSIENT_STATUS)


def send_with_retry(send: Callable[[], Any], method: str, sleep: Callable[[float], None] = time.sleep,
                    attempts: int = 5, default_wait: float = 2.0) -> Any:
    """Call ``send`` (one HTTP request) until it gets a response not worth repeating, at most
    ``attempts`` times, waiting as the server's ``Retry-After`` says (capped). A read is also repeated
    after a dropped connection or timeout; a write never is, since it may have reached the server.
    The last response is returned whatever its status; the caller decides what an error means."""
    import requests

    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            resp = send()
        except (requests.ConnectionError, requests.Timeout):
            if last or method.upper() not in SAFE_METHODS:
                raise
            sleep(min(default_wait * (2 ** attempt), RETRY_AFTER_CAP))
            continue
        if last or not should_retry(method, resp.status_code):
            return resp
        sleep(retry_after(getattr(resp, "headers", None), default_wait))
        close = getattr(resp, "close", None)
        if callable(close):
            close()  # a streamed response holds its connection until closed
    raise AssertionError("unreachable")


def status_of(exc: BaseException) -> int | None:
    """The HTTP status behind an error: FabricError keeps it in ``status``, requests in ``response``."""
    for status in (getattr(exc, "status", None), getattr(exc, "status_code", None),
                   getattr(getattr(exc, "response", None), "status_code", None)):
        if isinstance(status, int) and not isinstance(status, bool):
            return status
    return None


def windows_log_race(exc: BaseException) -> bool:
    """Windows refuses to open a Delta log file another process or thread is writing or renaming
    (os error 5 "Access is denied", os error 32 "being used by another process"). The file is
    complete a moment later, so the call is safe to repeat."""
    text = str(exc)
    return isinstance(exc, OSError) and "_delta_log" in text and ("os error 5)" in text or "os error 32)" in text)


T = TypeVar("T")


def local_retry(fn: Callable[[], T], attempts: int = 6, backoff: float = 0.05) -> T:
    """Repeat ``fn`` while it hits the Windows Delta log race, a bounded number of times. Only
    calls that are safe to repeat go through here; any other error is raised at once."""
    for attempt in range(attempts):
        try:
            return fn()
        except OSError as exc:
            if attempt == attempts - 1 or not windows_log_race(exc):
                raise
            time.sleep(backoff * (2 ** attempt))
    raise AssertionError("unreachable")


def is_retryable(exc: BaseException) -> bool:
    import requests
    from deltalake.exceptions import CommitFailedError

    from .leases import LeaseBusy

    if isinstance(exc, (LeaseBusy, CommitFailedError, requests.ConnectionError, requests.Timeout,
                        ConnectionError, TimeoutError)) or windows_log_race(exc):
        return True
    code = status_of(exc)
    if code is None:
        code = getattr(exc, "code", None)  # urllib's HTTPError; FabricError's code is a string
    return isinstance(code, int) and code in TRANSIENT_STATUS
