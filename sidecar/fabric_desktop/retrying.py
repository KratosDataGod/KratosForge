"""Which errors are worth another try: the network, throttling, a busy table, a lost commit race.

Everything else (a check that failed, bad config, a missing table, no permission) fails the same
way the next time, so the agent reports it instead of retrying it.
"""

from __future__ import annotations

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


def is_retryable(exc: BaseException) -> bool:
    import requests
    from deltalake.exceptions import CommitFailedError

    from .leases import LeaseBusy

    if isinstance(exc, (LeaseBusy, CommitFailedError, requests.ConnectionError, requests.Timeout,
                        ConnectionError, TimeoutError)):
        return True
    code = status_of(exc)
    if code is None:
        code = getattr(exc, "code", None)  # urllib's HTTPError; FabricError's code is a string
    return isinstance(code, int) and code in TRANSIENT_STATUS
