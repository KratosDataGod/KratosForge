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
import socket
import ssl
import sys
from pathlib import Path

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

OFFLINE_HINT = ("This PC couldn't reach the server. If it's offline, connect and try again. If your network "
                "blocks the address, ask IT to allow it (the enterprise install guide lists them under Network access).")

_HTTP = {"requests", "urllib3", "urllib", "http"}  # where an HTTP call's own errors come from
# urllib3's "couldn't connect" errors. Both subclass ConnectTimeoutError for old callers, though
# neither is a timeout: a name that didn't resolve, or a connection refused or unreachable.
_UNREACHABLE = {"NewConnectionError", "NameResolutionError"}
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


# How a refused certificate reads when only its text survives: OpenSSL's code, the class name in a
# wrapped error, and rustls' words (delta-rs, polars and object_store, which check against Windows'
# store too, through rustls-platform-verifier).
_CERT_TEXT = ("CERTIFICATE_VERIFY_FAILED", "SSLCertVerificationError", "invalid peer certificate")


# How a certificate refused for its dates reads: OpenSSL, Windows (truststore's and Schannel's words and
# codes) and rustls (delta-rs, polars, object_store). A PC clock that is wrong by hours or days gives the
# same refusal as a server whose certificate really expired, so the line asks to check the clock first.
_DATES_TEXT = ("certificate has expired", "certificate is not yet valid", "not within its validity period",
               "CERT_E_EXPIRED", "SEC_E_CERT_EXPIRED", "0x800B0101", "0x80090328",
               "certificate expired", "certificate not valid yet",  # rustls 0.23, on a line of their own
               "invalid peer certificate: Expired", "invalid peer certificate: NotValidYet")  # older rustls

CLOCK_HINT = ("HTTPS was refused because the server's certificate isn't valid at this PC's date and time. Check that "
              "Windows' date, time and time zone are right (Settings > Time & language > Set time automatically); "
              "if they are, the server's certificate has expired.")


def certificate_dates(exc_or_text: BaseException | str) -> bool:
    """Whether a refused certificate was refused for its dates (expired, or not valid yet), which a
    wrong PC clock causes too."""
    texts = [exc_or_text] if isinstance(exc_or_text, str) else [str(e) for e in _chain(exc_or_text)]
    return any(t.lower() in text.lower() for text in texts for t in _DATES_TEXT)


def certificate_refused(exc: BaseException) -> bool:
    """Whether ``exc`` (or what caused it) is a server certificate that couldn't be verified."""
    return any(isinstance(e, ssl.SSLCertVerificationError) or any(t in str(e) for t in _CERT_TEXT)
               for e in _chain(exc))


# A root certificate Windows trusts for server sign-in (or for every purpose), by its usage list.
_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
ROOTS_FILE = "windows-roots.pem"


def _pem_blocks(text: str) -> list[str]:
    """Each certificate in a PEM text, as one normalised block."""
    blocks, block = [], []
    for line in text.splitlines():
        line = line.strip()
        if line == "-----BEGIN CERTIFICATE-----":
            block = [line]
        elif block:
            block.append(line)
            if line == "-----END CERTIFICATE-----":
                blocks.append("\n".join(block) + "\n")
                block = []
    return blocks


def windows_roots_pem(folder: Path) -> Path | None:
    """The root certificates Windows trusts for HTTPS plus the public list Python ships (certifi),
    written to ``folder``/windows-roots.pem, for a client that reads a certificate file instead of
    asking Windows: gRPC, which Databricks Connect uses, otherwise trusts only the list it ships, so
    a network that inspects HTTPS refuses it. The public list stays in because the file replaces
    gRPC's own, and Windows adds a public root to its store only when it first needs it. None off
    Windows or when Windows' store can't be read. Public certificates only: nothing secret is written."""
    if sys.platform != "win32":
        return None
    try:
        windows = {_pem_blocks(ssl.DER_cert_to_PEM_cert(der))[0]
                   for der, encoding, trust in ssl.enum_certificates("ROOT")
                   if encoding == "x509_asn" and (trust is True or _SERVER_AUTH in trust)}
        if not windows:
            return None
        import certifi
        public = set(_pem_blocks(Path(certifi.where()).read_text(encoding="ascii")))
        text = "".join(sorted(windows | public))
        path = folder / ROOTS_FILE
        if not path.is_file() or path.read_text(encoding="ascii") != text:
            from . import jsonfile
            jsonfile.write_text(path, text, newline="\n")  # a unique temp file, swapped in
        return path
    except (OSError, ValueError, ImportError, IndexError) as exc:
        _log.warning("Windows' root certificates couldn't be written for gRPC: %s", exc)
        return None


def proxy_refused(exc: BaseException) -> bool:
    """Whether ``exc`` is the proxy refusing the call (407 included) or not answering."""
    return any(any(c.__name__ == "ProxyError" for c in type(e).__mro__)
               or "Tunnel connection failed" in str(e) or "Proxy Authentication Required" in str(e)
               for e in _chain(exc))


def unreachable(exc: BaseException) -> bool:
    """Whether ``exc`` is an HTTP call that couldn't connect at all: offline, a name that didn't
    resolve, or a host that refused or couldn't be routed to (not a timeout, not the proxy)."""
    chain = _chain(exc)
    if not any(type(e).__module__.split(".")[0] in _HTTP for e in chain):
        return False
    return any(type(e).__name__ in _UNREACHABLE or isinstance(e, (socket.gaierror, ConnectionRefusedError))
               for e in chain)


def timed_out(exc: BaseException) -> bool:
    """Whether ``exc`` is a server or proxy that didn't answer within the call's timeout. A timeout
    with no HTTP library in its chain (a kernel, a lock, a subprocess) is something else."""
    chain = _chain(exc)
    return any(type(e).__module__.split(".")[0] in _HTTP for e in chain) \
        and any(c.__name__ in _TIMEOUTS for e in chain for c in type(e).__mro__ if type(e).__name__ not in _UNREACHABLE)


def explain(exc: BaseException) -> str | None:
    """One line that says what to do about a network failure, or None when it is something else."""
    if certificate_refused(exc):
        return CLOCK_HINT if certificate_dates(exc) else HINT
    if proxy_refused(exc):
        return PROXY_HINT
    if unreachable(exc):
        return OFFLINE_HINT
    if timed_out(exc):
        return TIMEOUT_HINT
    return None


# What pip prints when it couldn't reach the package index at all. pip then ends with "No matching
# distribution found", the same words as for a package that doesn't exist. Not "Could not fetch URL",
# which pip also prints when the index answered with an HTTP error.
_PIP_UNREACHABLE = ("NameResolutionError", "NewConnectionError", "Failed to establish a new connection",
                    "ConnectTimeoutError", "Read timed out", "ProxyError")


def pip_unreachable(log: str, fix: str) -> str | None:
    """One line saying pip couldn't reach the package index, from pip's output, or None. ``fix`` is
    what IT can do for this install (allow the index, or a folder or index of its own)."""
    if "CERTIFICATE_VERIFY_FAILED" in log or "SSLCertVerificationError" in log:  # the second: pip's truststore on Windows
        if certificate_dates(log):
            return ("pip couldn't check the package index's certificate at this PC's date and time, so it reported the "
                    "packages as not found. Check that Windows' date, time and time zone are right.")
        return ("pip couldn't check the package index's certificate, so it reported the packages as not found. "
                "If your network inspects HTTPS, ask IT to add its root certificate to Windows.")
    if "ProxyError" in log or "Tunnel connection failed" in log:
        return f"The proxy refused pip's connection to the package index, so it reported the packages as not found. {fix}"
    if any(marker in log for marker in _PIP_UNREACHABLE):
        return ("pip couldn't reach the package index from this PC (offline, or blocked by your network), so it "
                f"reported the packages as not found. If the PC is offline, connect and try again. {fix}")
    return None
