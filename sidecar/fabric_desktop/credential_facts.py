"""What an agent says about its own credential: facts, never the credential (plan item 78).

Published in the agent's status record as ``credential``::

    {"kind": "certificate" | "secret" | "managed_identity" | "user",
     "expires_at": "...Z" | null, "last_token_at": "...Z" | null,
     "last_token_error": {"at": "...Z", "error": "..."} | null}

``expires_at`` is a certificate's ``not_valid_after``, read from ``auth.certificate_path``. A secret's
or a managed identity's expiry isn't visible to the agent, so it is null, which readers show as
unknown, never as "doesn't expire". ``last_token_*`` come from the agent's own token requests; the
error has secrets, ids, URLs, emails and user paths removed, since everyone who can read the
environment's LH_Meta can read the record. The path, thumbprint, key and password are never published.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
from pathlib import Path
from typing import Any

from .redact import describe, for_support

_log = logging.getLogger(__name__)

_cache: dict[tuple[str, float], str | None] = {}
_URL = re.compile(r"\b(?:https?|wss?)://\S+", re.I)


def _iso(t: dt.datetime | float | None) -> str | None:
    if t is None:
        return None
    when = t if isinstance(t, dt.datetime) else dt.datetime.fromtimestamp(t, dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def kind(auth: dict[str, Any]) -> str:
    t = auth.get("type", "")
    if t == "service_principal":
        return "certificate" if auth.get("certificate_path") else "secret"
    return str(t)


def certificate_expiry(path: str, password_env: str = "") -> str | None:
    """When the certificate at ``path`` stops being valid; None when it can't be read. Read again
    only when the file changes."""
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    if (path, mtime) not in _cache:
        _cache[path, mtime] = _read_expiry(Path(path), os.environ.get(password_env) if password_env else None)
    return _cache[path, mtime]


def _read_expiry(path: Path, password: str | None) -> str | None:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        data = path.read_bytes()
        if path.suffix.lower() in (".pfx", ".p12"):
            cert = pkcs12.load_key_and_certificates(data, password.encode() if password else None)[1]
        else:
            cert = x509.load_pem_x509_certificate(data)
        return _iso(cert.not_valid_after_utc) if cert is not None else None
    except Exception as exc:  # noqa: BLE001 - unreadable here: unknown, and the agent's sign-in says why
        _log.debug("Couldn't read the certificate's expiry: %s", describe(exc))
        return None


def facts(auth: dict[str, Any], credential: Any | None) -> dict[str, Any]:
    """The ``credential`` object for a status record. ``credential`` is the agent's credential if it
    has been made yet (it records its last token request), else None."""
    k = kind(auth)
    error = getattr(credential, "last_token_error", None)
    return {
        "kind": k,
        "expires_at": certificate_expiry(str(auth["certificate_path"]), str(auth.get("certificate_password_env") or ""))
        if k == "certificate" else None,
        "last_token_at": _iso(getattr(credential, "last_token_at", None)),
        "last_token_error": {"at": _iso(error[0]), "error": _URL.sub("[url]", for_support(error[1]))[:200]} if error else None,
    }
