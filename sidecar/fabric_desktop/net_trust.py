"""HTTPS behind a corporate network: trust the certificates Windows trusts.

Python's own HTTPS (requests, MSAL, urllib) checks servers against the certificate list it ships
(certifi), not against Windows. A network that inspects HTTPS re-signs every site with the
company's own root certificate, which IT adds to Windows but which that list doesn't hold, so
every call to Fabric, OneLake, Graph or Databricks failed with CERTIFICATE_VERIFY_FAILED.
``use_system_store()`` makes Python check against the Windows certificate store instead
(truststore, the same way pip does). ``REQUESTS_CA_BUNDLE`` still adds a certificate file on top.
Each process the engine starts calls it first: the engine (cli.py) and a notebook's kernel
(kernel_host.py).

The proxy comes from requests: ``HTTPS_PROXY``/``NO_PROXY`` when set, else the proxy Windows is set
to (Internet Options). ``explain()`` turns a refused certificate, a proxy that refused the call and
a server that didn't answer in time into one line that says what to do."""

from __future__ import annotations

import logging
import ssl
import sys

_log = logging.getLogger(__name__)
_done = False

HINT = ("HTTPS was refused because the server's certificate isn't trusted on this PC. If your network "
        "inspects HTTPS, ask IT to add its root certificate to Windows (KratosForge trusts the Windows "
        "certificate store), or set REQUESTS_CA_BUNDLE to a file that holds it.")


PROXY_HINT = ("The proxy server refused the connection or couldn't be reached. KratosForge uses the proxy "
              "Windows is set to, or HTTPS_PROXY when it's set; a proxy that asks for a Windows sign-in of its own "
              "(407) isn't supported, so ask IT to allow the Microsoft and GitHub addresses without one.")

TIMEOUT_HINT = ("The server didn't answer in time. If your network uses a proxy, check that it allows this "
                "address and that the proxy Windows is set to (or HTTPS_PROXY) is right.")

_HTTP = {"requests", "urllib3", "urllib", "http"}  # where an HTTP call's own errors come from
_TIMEOUTS = {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectTimeoutError", "ReadTimeoutError", "TimeoutError"}


def use_system_store(platform: str | None = None) -> bool:
    """On Windows, check HTTPS certificates against the Windows certificate store. Returns whether
    it applies. Once per process; elsewhere (CI, an agent on Linux) Python's own list stays."""
    global _done
    if _done:
        return True
    if (platform or sys.platform) != "win32":
        return False
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception as exc:  # noqa: BLE001 - silent-ok: logged; HTTPS still works with Python's own list
        _log.warning("HTTPS keeps Python's own certificate list, not Windows': %s", exc)
        return False
    _done = True
    return True


def _chain(exc: BaseException) -> list[BaseException]:
    """``exc`` and everything that caused it, as requests, urllib3 and urllib wrap it."""
    seen: set[int] = set()
    out: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        out.append(e)
        todo += [e.__cause__, e.__context__, *(a for a in getattr(e, "args", ()) if isinstance(a, BaseException))]
        reason = getattr(e, "reason", None)  # urllib3's MaxRetryError and urllib's URLError keep the cause here
        if isinstance(reason, BaseException):
            todo.append(reason)
    return out


def certificate_refused(exc: BaseException) -> bool:
    """Whether ``exc`` (or what caused it) is a server certificate that couldn't be verified."""
    return any(isinstance(e, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)
               for e in _chain(exc))


def proxy_refused(exc: BaseException) -> bool:
    """Whether ``exc`` is the proxy refusing the call (407 included) or not answering."""
    return any(any(c.__name__ == "ProxyError" for c in type(e).__mro__)
               or "Tunnel connection failed" in str(e) or "Proxy Authentication Required" in str(e)
               for e in _chain(exc))


def timed_out(exc: BaseException) -> bool:
    """Whether ``exc`` is a server or proxy that didn't answer within the call's timeout. A timeout
    with no HTTP library in its chain (a kernel, a lock, a subprocess) is something else."""
    chain = _chain(exc)
    return any(type(e).__module__.split(".")[0] in _HTTP for e in chain) \
        and any(c.__name__ in _TIMEOUTS for e in chain for c in type(e).__mro__)


def explain(exc: BaseException) -> str | None:
    """One line that says what to do about a network failure, or None when it is something else."""
    if certificate_refused(exc):
        return HINT
    if proxy_refused(exc):
        return PROXY_HINT
    if timed_out(exc):
        return TIMEOUT_HINT
    return None
