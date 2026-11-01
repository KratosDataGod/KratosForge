"""Access tokens for an Azure Databricks connection, from the references a connection profile keeps.

A profile never holds a token (connprofiles.py). When a remote session opens, the token comes from
where the person already keeps their sign-in, through mechanisms Databricks documents:

    cli-profile   the named profile in ~/.databrickscfg. A profile signed in with the Databricks CLI
                  (auth_type databricks-cli) gets its token from ``databricks auth token --profile``;
                  one with client_id and client_secret uses OAuth machine-to-machine; one with a
                  personal access token uses that token as it is.
    oauth-u2m     ``databricks auth token --host``, after ``databricks auth login --host`` once.
    oauth-m2m     OAuth client credentials at the workspace's ``/oidc/v1/token``, with the secret read
                  from its reference (env:NAME, or credman:TARGET in Windows Credential Manager).

Tokens live in memory for this session only, are never written anywhere, and never appear in an
error: messages pass through redact.secrets, and a CLI's output is never quoted. The host a token
is sent to is always checked to be an Azure Databricks workspace address first.
"""

from __future__ import annotations

import configparser
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .. import connprofiles, redact
from .runtimes import DRIVE, trusted_cli

CLI_TIMEOUT = 60
# A token this close to expiring is fetched again, so a statement never starts with one about to lapse.
REFRESH_SECONDS = 120
_DRIVE = DRIVE


class DatabricksAuthError(RuntimeError):
    """A token couldn't be had; the message says what the person can do."""


def check_host(host: str) -> str:
    """``https://<workspace host>`` in lower case, when ``host`` is an Azure Databricks workspace
    address: one form however it was written, so two addresses of one workspace compare equal."""
    host = (host or "").rstrip("/")
    found = connprofiles._host_problems({"cloud": "azure", "host": host}, "host")
    if not host or found:
        raise DatabricksAuthError(found[0] if found else "The connection has no workspace address (host).")
    return "https://" + (urlparse(host).hostname or "")


def resolve_secret(ref: str, environ: Mapping[str, str] | None = None) -> str:
    """The value behind ``env:NAME`` or ``credman:TARGET``. Never returned in an error."""
    if not isinstance(ref, str) or not connprofiles._REF.fullmatch(ref):
        raise DatabricksAuthError("The secret must be a reference (env:NAME or credman:TARGET).")
    kind, _, name = ref.partition(":")
    if kind == "env":
        value = (os.environ if environ is None else environ).get(name, "")
        if not value:
            raise DatabricksAuthError(f"The environment variable {name} isn't set on this PC.")
        return value
    return _credman_read(name)


# Windows' own limit on a credential's secret (CRED_MAX_CREDENTIAL_BLOB_SIZE, 5 * 512 bytes): a size
# beyond it isn't a credential Credential Manager wrote, so nothing past it is read.
CRED_MAX_BLOB = 5 * 512


def _advapi() -> Any:
    import ctypes
    return ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]


def _credman_read(target: str) -> str:
    """A generic credential's secret from Windows Credential Manager (CredReadW). Errors name the
    credential, never its secret."""
    if sys.platform != "win32":
        raise DatabricksAuthError("credman: references are read from Windows Credential Manager, on Windows only.")
    import ctypes
    from ctypes import wintypes

    class _Credential(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    advapi = _advapi()
    found = ctypes.POINTER(_Credential)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(found)):  # 1 = CRED_TYPE_GENERIC
        raise DatabricksAuthError(f"Windows Credential Manager has no generic credential named {target!r}.")
    try:
        size = int(found.contents.CredentialBlobSize)
        if size > CRED_MAX_BLOB:
            raise DatabricksAuthError(f"The credential {target!r} in Windows Credential Manager is larger than "
                                      f"Windows allows ({CRED_MAX_BLOB} bytes), so it wasn't read.")
        blob = ctypes.string_at(found.contents.CredentialBlob, size) if size else b""
    finally:
        advapi.CredFree(found)
    return credman_text(blob, target)


def credman_text(blob: bytes, target: str) -> str:
    """A Credential Manager secret as text. Credential Manager's own editor and cmdkey save UTF-16;
    some tools save UTF-8. A UTF-16 secret of ASCII holds NUL bytes, so a blob with none (past its
    terminating NULs) that reads as UTF-8 is UTF-8: read as UTF-16 it would become other characters,
    a wrong secret. A blob that is neither is refused rather than guessed, and the error never
    quotes it."""
    text = None
    bare = blob.rstrip(b"\x00")   # without the terminating NULs some tools store with the secret
    if b"\x00" not in bare:
        try:
            text = bare.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is None and len(blob) % 2 == 0:
        try:
            text = blob.decode("utf-16-le")
        except UnicodeDecodeError:
            text = None
    if text is None:
        raise DatabricksAuthError(f"The credential {target!r} in Windows Credential Manager isn't readable text "
                                  "(UTF-16 or UTF-8). Save the secret again with Credential Manager or cmdkey.")
    text = text.rstrip("\x00")
    if not text:
        raise DatabricksAuthError(f"The credential {target!r} in Windows Credential Manager is empty.")
    return text


class TokenProvider:
    """``token()`` gives a current access token, fetching a new one only when it is near expiry."""

    def __init__(self, host: str, fetch: Callable[[], tuple[str, float | None]], method: str,
                 clock: Callable[[], float] = time.time):
        self.host = host
        self.method = method
        self._fetch = fetch
        self._clock = clock
        self._token = ""
        self._expires: float | None = None
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if not self._token or (self._expires is not None and self._clock() >= self._expires - REFRESH_SECONDS):
                self._token, self._expires = self._fetch()
                if not self._token:
                    raise DatabricksAuthError("Databricks gave no access token.")
            return self._token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}


def _cfg_section(profile: str, path: Path | None) -> dict[str, str]:
    path = path or connprofiles.databricks_config_path()
    parser = configparser.ConfigParser(interpolation=None, default_section="__none__")
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DatabricksAuthError(f"There's no Databricks CLI configuration at {path}. "
                                  "Sign in with: databricks auth login --host <workspace address>") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise DatabricksAuthError(f"{path} couldn't be read ({type(exc).__name__}).") from None
    except configparser.Error as exc:
        # configparser quotes the line it can't read, and that line may hold a token or a client
        # secret in a form redaction doesn't know: say where, never what.
        raise DatabricksAuthError(f"{path} isn't a valid Databricks CLI configuration{_where(exc)}. "
                                  "Fix it, or sign in again with: databricks auth login --host <workspace address>"
                                  ) from None
    if not parser.has_section(profile):
        raise DatabricksAuthError(f"{path} has no profile named {profile!r}.")
    return {k: v.strip() for k, v in parser.items(profile)}


def _where(exc: configparser.Error) -> str:
    """The line numbers a configparser error is about, without the text of those lines."""
    lines = [n for n, _line in getattr(exc, "errors", [])] or [getattr(exc, "lineno", None)]
    lines = [n for n in lines if isinstance(n, int)]
    return (" (line " + ", ".join(map(str, lines)) + ")") if lines else ""


def find_cli(environ: Mapping[str, str] | None = None) -> str | None:
    """The Databricks CLI from PATH's absolute folders only (runtimes.trusted_cli): on Windows only
    databricks.exe in a folder that starts with a drive letter, never the current folder."""
    return trusted_cli("databricks", environ)


def _cli_token(args: list[str], run: Callable[..., Any]) -> tuple[str, float | None]:
    """``databricks auth token ...``: the CLI's own token cache, refreshed by the CLI."""
    cli = find_cli()
    if not cli:
        raise DatabricksAuthError("The Databricks CLI isn't installed or isn't on PATH. Install it from "
                                  "https://docs.databricks.com/dev-tools/cli/install.html, then sign in.")
    try:
        done = run([cli, "auth", "token", *args], capture_output=True, text=True, timeout=CLI_TIMEOUT,
                   stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise DatabricksAuthError("The Databricks CLI didn't answer within a minute.") from None
    except OSError as exc:   # found on PATH, but Windows or a policy wouldn't start it
        raise DatabricksAuthError(f"The Databricks CLI at {cli} couldn't be started ({type(exc).__name__}). "
                                  "Reinstall it, or check that it is allowed to run.") from None
    if done.returncode != 0:
        # stderr says why (not signed in, expired refresh token); it never carries the token itself,
        # but it passes through redaction anyway.
        lines = (done.stderr or "").strip().splitlines()
        detail = _part(redact.secrets(lines[-1])) if lines else ""
        hint = " Sign in again with: databricks auth login " + " ".join(args)
        raise DatabricksAuthError(f"The Databricks CLI couldn't give a token{': ' + detail if detail else '.'}{hint}")
    try:
        data = json.loads(done.stdout)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("access_token", ""), str):
        raise DatabricksAuthError("The Databricks CLI's answer wasn't the JSON it documents. "
                                  "Update the CLI (0.205 or later).")
    return data.get("access_token") or "", _expiry(data.get("expiry"))


_PART_MAX = 300   # characters of the CLI's own reason kept in an error


def _part(text: str) -> str:
    """The CLI's reason as one bounded line: control characters folded to spaces, cut to _PART_MAX."""
    line = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return line if len(line) <= _PART_MAX else line[:_PART_MAX - 1].rstrip() + "…"


def _expiry(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _m2m_token(host: str, client_id: str, secret: str, http: Any, clock: Callable[[], float]) -> tuple[str, float | None]:
    """OAuth client credentials at the workspace's token endpoint (Databricks OAuth M2M)."""
    try:
        resp = http.post(f"{host}/oidc/v1/token", data={"grant_type": "client_credentials", "scope": "all-apis"},
                         auth=(client_id, secret), timeout=30)
    except Exception as exc:  # noqa: BLE001 - network problems become the person's message
        raise DatabricksAuthError(f"Couldn't reach {host}: {redact.secrets(type(exc).__name__)}.") from None
    if resp.status_code != 200:
        raise DatabricksAuthError(f"The workspace refused the service principal's sign-in (HTTP {resp.status_code}). "
                                  "Check the client ID, the secret and that the principal is in the workspace.")
    refused = DatabricksAuthError("The workspace's token endpoint didn't answer with the OAuth token it documents. "
                                  "Check the workspace address.")
    try:
        data = resp.json()
        lifetime = data.get("expires_in")
        seconds = float(lifetime) if lifetime is not None else None   # missing: no known expiry
    except (ValueError, TypeError, AttributeError):
        raise refused from None
    # A lifetime that is infinite, NaN or not positive would keep a token forever or fetch one on every call.
    if seconds is not None and not (math.isfinite(seconds) and seconds > 0):
        raise refused
    if not isinstance(data.get("access_token", ""), str):
        raise refused   # str() of an object would send its text as the bearer token
    return data.get("access_token") or "", (clock() + seconds) if seconds is not None else None


def provider_for(connection: dict[str, Any], *, run: Callable[..., Any] = subprocess.run, http: Any = None,
                 environ: Mapping[str, str] | None = None, cfg_path: Path | None = None,
                 clock: Callable[[], float] = time.time) -> TokenProvider:
    """A token provider for one Databricks connection from a profile (refs only, never a token)."""
    if connection.get("platform") != "databricks":
        raise DatabricksAuthError("This connection isn't a Databricks one.")
    found = connprofiles.secret_problems(connection)
    if found:
        raise DatabricksAuthError(found[0])
    if http is None:
        from ..http_session import session
        http = session()
    cred = connection.get("credential") or {}
    method = cred.get("method") or "oauth-u2m"
    if method == "cli-profile":
        name = str(cred.get("profile") or "")
        if not name:
            raise DatabricksAuthError("Name the Databricks CLI profile to use.")
        section = _cfg_section(name, cfg_path)
        host = check_host(connection.get("host") or section.get("host", ""))
        if section.get("host") and check_host(section["host"]) != host:
            raise DatabricksAuthError(f"The CLI profile {name!r} is for another workspace than this connection.")
        if section.get("token"):
            pat = section["token"]
            return TokenProvider(host, lambda: (pat, None), "pat", clock)
        if section.get("client_id") and section.get("client_secret"):
            cid, sec = section["client_id"], section["client_secret"]
            return TokenProvider(host, lambda: _m2m_token(host, cid, sec, http, clock), "oauth-m2m", clock)
        return TokenProvider(host, lambda: _cli_token(["--profile", name], run), "databricks-cli", clock)
    host = check_host(connection.get("host") or "")
    if method == "oauth-u2m":
        return TokenProvider(host, lambda: _cli_token(["--host", host], run), "oauth-u2m", clock)
    if method == "oauth-m2m":
        cid = str(cred.get("client_id") or "")
        ref = str(cred.get("secret") or "")
        if not cid or not ref:
            raise DatabricksAuthError("OAuth machine-to-machine needs client_id and a secret reference.")
        return TokenProvider(host, lambda: _m2m_token(host, cid, resolve_secret(ref, environ), http, clock),
                             "oauth-m2m", clock)
    raise DatabricksAuthError(f"Unknown Databricks sign-in method {method!r}.")
