"""Values in the app's settings.json that are secrets, kept encrypted with Windows DPAPI.

A Teams webhook address lets anyone who has it post to the channel, so it is stored sealed: only
the same Windows account on the same PC reads it back (dpapi.py). Off Windows (the headless agent,
which takes its webhook from an environment variable), values stay as they are, like the sign-in
cache. A value saved before this is read as it is and sealed on the next save.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from . import dpapi
from .redact import describe

_log = logging.getLogger(__name__)

SEALED_KEYS = ("teams_webhook",)
PREFIX = "dpapi:"
PURPOSE = b"KratosForge settings secret v1"


def seal(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as they are written: each secret value encrypted, when this PC can."""
    out = dict(settings)
    if not dpapi.available():
        return out
    for key in SEALED_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value and not value.startswith(PREFIX):
            out[key] = PREFIX + base64.b64encode(dpapi.protect(value.encode("utf-8"), PURPOSE)).decode("ascii")
    return out


def unseal(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as they are used: each sealed value decrypted. One this account can't read
    (copied from another PC or account) is left empty, so it is set again rather than used."""
    out = dict(settings)
    for key in SEALED_KEYS:
        value = out.get(key)
        if not (isinstance(value, str) and value.startswith(PREFIX)):
            continue
        try:
            if not dpapi.available():
                raise OSError("Windows DPAPI isn't available here")
            out[key] = dpapi.unprotect(base64.b64decode(value[len(PREFIX):]), PURPOSE).decode("utf-8")
        except (OSError, ValueError) as exc:
            _log.warning("The saved %s can't be read on this account or PC, so it's treated as not set: %s",
                         key, describe(exc))
            out[key] = ""
    return out


def needs_sealing(settings: dict[str, Any]) -> bool:
    """Whether a secret is still stored in the clear where this PC could seal it."""
    return dpapi.available() and any(isinstance(settings.get(k), str) and settings[k]
                                     and not settings[k].startswith(PREFIX) for k in SEALED_KEYS)
