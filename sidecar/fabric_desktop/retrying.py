"""Which errors are worth another try: the network, throttling, a busy table, a lost commit race,
Windows refusing a Delta log file another writer is renaming.

Everything else (a check that failed, bad config, a missing table, no permission) fails the same
way the next time, so the agent reports it instead of retrying it.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

# The one list of HTTP statuses worth another try. The Fabric client, the agent, the integrations API
# and the workload screens all read it, so they agree on what a passing outage is.
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


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
