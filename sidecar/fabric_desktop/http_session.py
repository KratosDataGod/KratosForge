"""The one HTTP session the engine's Fabric, OneLake, Power BI and admin clients share.

Every client used to build its own ``requests.Session()`` or call ``requests.get``/``post``
directly, so each opened its own connections. ``session()`` gives them one connection pool, a
default timeout, and no cookie jar. The retry policy stays in ``retrying``: reads go through
``send_with_retry``; writes are sent once (a write that failed part way may have happened).

A client still takes an injected session, which wins over the shared one (tests rely on it).
"""

from __future__ import annotations

import os
import threading
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from requests.cookies import RequestsCookieJar

# Seconds to wait for a server when the caller names no timeout, so no request can hang forever.
# Every call in the engine names its own today, so this is only a backstop.
DEFAULT_TIMEOUT = 60
# Connections kept open per host. Requests beyond this still run (nothing waits), but their extra
# connections are closed after use instead of kept.
POOL_SIZE = 16


class _NoCookies(RequestsCookieJar):
    """A cookie jar that keeps nothing: the clients authenticate with bearer tokens, not cookies."""

    def set_cookie(self, cookie: Any, *args: Any, **kwargs: Any) -> None:
        return None


class _Session(requests.Session):
    """A pooled session with a default timeout. It keeps no cookies, so nothing one response sets
    is sent with a later request for another workspace, tenant or person."""

    def __init__(self) -> None:
        super().__init__()
        self.cookies = _NoCookies()
        # max_retries=0: urllib3 must not repeat requests on its own; retrying.send_with_retry decides.
        adapter = HTTPAdapter(pool_connections=POOL_SIZE, pool_maxsize=POOL_SIZE, max_retries=0)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def request(self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any) -> requests.Response:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = DEFAULT_TIMEOUT
        return super().request(method, url, *args, **kwargs)


_lock = threading.Lock()
_shared: requests.Session | None = None
_pid = 0


def session() -> requests.Session:
    """The shared pooled session, made on first use (and again in a child process, since a
    connection pool must not cross a fork)."""
    global _shared, _pid
    with _lock:
        if _shared is None or _pid != os.getpid():
            _shared, _pid = _Session(), os.getpid()
        return _shared


def reset() -> None:
    """Close the shared session; the next ``session()`` makes a new one."""
    global _shared
    with _lock:
        if _shared is not None:
            _shared.close()
        _shared = None
