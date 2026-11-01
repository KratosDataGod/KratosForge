"""HTTPS behind a corporate network: trust the certificates Windows trusts.

Python's own HTTPS (requests, MSAL, urllib) checks servers against the certificate list it ships
(certifi), not against Windows. A network that inspects HTTPS re-signs every site with the
company's own root certificate, which IT adds to Windows but which that list doesn't hold, so
every call to Fabric, OneLake, Graph or Databricks failed with CERTIFICATE_VERIFY_FAILED.
``use_system_store()`` makes Python check against the Windows certificate store instead
(truststore, the same way pip does). ``REQUESTS_CA_BUNDLE`` still adds a certificate file on top.
Each process the engine starts calls it first: the engine (cli.py) and a notebook's kernel
(kernel_host.py)."""

from __future__ import annotations

import logging
import ssl
import sys

_log = logging.getLogger(__name__)
_done = False

HINT = ("HTTPS was refused because the server's certificate isn't trusted on this PC. If your network "
        "inspects HTTPS, ask IT to add its root certificate to Windows (KratosForge trusts the Windows "
        "certificate store), or set REQUESTS_CA_BUNDLE to a file that holds it.")


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


def certificate_refused(exc: BaseException) -> bool:
    """Whether ``exc`` (or what caused it) is a server certificate that couldn't be verified."""
    seen: set[int] = set()
    todo: list[BaseException | None] = [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            return True
        todo += [e.__cause__, e.__context__, *(a for a in getattr(e, "args", ()) if isinstance(a, BaseException))]
        reason = getattr(e, "reason", None)  # urllib3's MaxRetryError keeps the SSL error here
        if isinstance(reason, BaseException):
            todo.append(reason)
    return False
