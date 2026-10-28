"""Values in the app's settings.json that are secrets, kept encrypted with Windows DPAPI.

A Teams webhook address lets anyone who has it post to the channel, so it is stored sealed: only
the same Windows account on the same PC reads it back (dpapi.py). That covers the alert address
(``teams_webhook``) and each platform notice route's address (``platform_notify.routes[].url``). Off Windows (the headless agent,
which takes its webhook from an environment variable), values stay as they are, like the sign-in
cache. A value saved before this is read as it is and sealed on the next save.

A sealed value this account can't decrypt (a password reset by an administrator, a moved profile, a
copy from another PC) reads as empty and is named in ``UNREADABLE``, so the app can say to enter it
again; it is kept as it is until it is set again. A route's address is named by the route's name
under ``UNREADABLE["platform_notify"]``, and kept until the routes are saved again.

Sealing covers settings.json from now on. A copy written in the clear before (a backup, a synced or
copied profile) isn't found or cleaned, so where that matters enter the address again and have
the channel's owner make a new webhook (security.md).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import Any

from . import dpapi
from .redact import describe

_log = logging.getLogger(__name__)

SEALED_KEYS = ("teams_webhook",)
ROUTES = "platform_notify"  # {"routes": [{"name": ..., "url": ...}, ...]}: each url is sealed
PREFIX = "dpapi:"
PURPOSE = b"KratosForge settings secret v1"
# In settings as read, never written: {key: the sealed value this account can't decrypt}.
UNREADABLE = "_sealed_unreadable"
_warned: set[str] = set()  # each unreadable value is logged once per process, not on every read


def _seal_one(value: Any) -> Any:
    if isinstance(value, str) and value and not value.startswith(PREFIX):
        return PREFIX + base64.b64encode(dpapi.protect(value.encode("utf-8"), PURPOSE)).decode("ascii")
    return value


def _unseal_one(key: str, value: Any) -> tuple[Any, str | None]:
    """``value`` decrypted, or ("", value) when this account can't read it."""
    if not (isinstance(value, str) and value.startswith(PREFIX)):
        return value, None
    try:
        if not dpapi.available():
            raise OSError("Windows DPAPI isn't available here")
        return dpapi.unprotect(base64.b64decode(value[len(PREFIX):]), PURPOSE).decode("utf-8"), None
    except (OSError, ValueError) as exc:
        if value not in _warned:
            _warned.add(value)
            _log.warning("The saved %s can't be read on this account or PC, so it's treated as not set: %s",
                         key, describe(exc))
        return "", value


def _routes(settings: dict[str, Any]) -> list[dict[str, Any]]:
    raw = settings.get(ROUTES)
    routes = raw.get("routes") if isinstance(raw, dict) else None
    return [r for r in routes if isinstance(r, dict)] if isinstance(routes, list) else []


def _with_routes(settings: dict[str, Any], change: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Replace ``settings[ROUTES]`` with a copy whose routes went through ``change``."""
    raw = settings.get(ROUTES)
    if isinstance(raw, dict) and isinstance(raw.get("routes"), list):
        settings[ROUTES] = dict(raw, routes=[change(dict(r)) if isinstance(r, dict) else r for r in raw["routes"]])


def seal(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as they are written: each secret value encrypted, when this PC can. A value that
    couldn't be decrypted and wasn't set again is written back as it was."""
    out = dict(settings)
    stash = out.pop(UNREADABLE, None) or {}
    for key, sealed in stash.items():
        if key != ROUTES and not out.get(key):
            out[key] = sealed
    by_name = stash.get(ROUTES) if isinstance(stash.get(ROUTES), dict) else {}
    if by_name:
        _with_routes(out, lambda r: dict(r, url=by_name[str(r.get("name", ""))])
                     if not r.get("url") and str(r.get("name", "")) in by_name else r)
    if not dpapi.available():
        return out
    for key in SEALED_KEYS:
        if key in out:
            out[key] = _seal_one(out[key])
    _with_routes(out, lambda r: dict(r, url=_seal_one(r["url"])) if "url" in r else r)
    return out


def unseal(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as they are used: each sealed value decrypted. One this account can't read is
    empty, and named in ``out[UNREADABLE]``, so it is set again rather than used."""
    out = dict(settings)
    unreadable: dict[str, Any] = {}
    for key in SEALED_KEYS:
        if key in out:
            out[key], foreign = _unseal_one(key, out[key])
            if foreign is not None:
                unreadable[key] = foreign
    lost: dict[str, str] = {}

    def route(r: dict[str, Any]) -> dict[str, Any]:
        if "url" not in r:
            return r
        url, foreign = _unseal_one(f"Teams address of platform route {r.get('name', '')}", r["url"])
        if foreign is not None:
            lost[str(r.get("name", ""))] = foreign
        return dict(r, url=url)
    _with_routes(out, route)
    if lost:
        unreadable[ROUTES] = lost
    if unreadable:
        out[UNREADABLE] = unreadable
    return out


def unreadable(settings: dict[str, Any], key: str) -> bool:
    """Whether ``key`` is saved but this account can't decrypt it (settings as ``unseal`` gives them)."""
    return key in (settings.get(UNREADABLE) or {})


def needs_sealing(settings: dict[str, Any]) -> bool:
    """Whether a secret is still stored in the clear where this PC could seal it."""
    clear = [settings.get(k) for k in SEALED_KEYS] + [r.get("url") for r in _routes(settings)]
    return dpapi.available() and any(isinstance(v, str) and v and not v.startswith(PREFIX) for v in clear)
