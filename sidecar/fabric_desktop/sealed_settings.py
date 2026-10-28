"""Values in the app's settings.json that are secrets, kept encrypted with Windows DPAPI.

A Teams webhook address lets anyone who has it post to the channel, so it is stored sealed: only
the same Windows account on the same PC reads it back (dpapi.py). Off Windows (the headless agent,
which takes its webhook from an environment variable), values stay as they are, like the sign-in
cache. A value saved before this is read as it is and sealed on the next save.

A sealed value this account can't decrypt (a password reset by an administrator, a moved profile, a
copy from another PC) reads as empty and is named in ``UNREADABLE``, so the app can say to enter it
again; it is kept as it is until it is set again.
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
# In settings as read, never written: {key: the sealed value this account can't decrypt}.
UNREADABLE = "_sealed_unreadable"


def seal(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as they are written: each secret value encrypted, when this PC can. A value that
    couldn't be decrypted and wasn't set again is written back as it was."""
    out = dict(settings)
    for key, sealed in (out.pop(UNREADABLE, None) or {}).items():
        if not out.get(key):
            out[key] = sealed
    if not dpapi.available():
        return out
    for key in SEALED_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value and not value.startswith(PREFIX):
            out[key] = PREFIX + base64.b64encode(dpapi.protect(value.encode("utf-8"), PURPOSE)).decode("ascii")
    return out


def unseal(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as they are used: each sealed value decrypted. One this account can't read is
    empty, and named in ``out[UNREADABLE]``, so it is set again rather than used."""
    out = dict(settings)
    unreadable: dict[str, str] = {}
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
            unreadable[key] = value
    if unreadable:
        out[UNREADABLE] = unreadable
    return out


def unreadable(settings: dict[str, Any], key: str) -> bool:
    """Whether ``key`` is saved but this account can't decrypt it (settings as ``unseal`` gives them)."""
    return key in (settings.get(UNREADABLE) or {})


def needs_sealing(settings: dict[str, Any]) -> bool:
    """Whether a secret is still stored in the clear where this PC could seal it."""
    return dpapi.available() and any(isinstance(settings.get(k), str) and settings[k]
                                     and not settings[k].startswith(PREFIX) for k in SEALED_KEYS)
