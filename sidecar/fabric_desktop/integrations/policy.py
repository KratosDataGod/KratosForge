"""What the organization allows for external AI tools, from the IT policy file.

The same file as every other IT setting (app_settings.policy_path(), usually
``%ProgramData%\\FabricDesktop\\policy.json``), with these keys:

    integrations                    false: external AI tools are off on this PC, whatever the
                                    person chooses in Settings (default: the person decides)
    integrations_max_permissions    the permission categories a tool may be given, for example
                                    ["READ_METADATA", "READ_DATA"] (default: all that are offered)
    integrations_http               false: the local HTTP API can't be switched on (default: allowed)
    integrations_extensions         the extensions the person may turn on, by id, for example
                                    ["notebook-outline"]; [] allows none (default: any)
    receipt_redactions              parts of a receipt every export leaves out (the enterprise
                                    program's key, PR #84); external tools' exports honour it too

Nothing an external tool sends can change these: they are read from the file on every check.
"""

from __future__ import annotations

import json
from typing import Any

from .. import app_settings
from .registry import PERMISSIONS

KEYS = ("integrations", "integrations_max_permissions", "integrations_http", "integrations_extensions",
        "receipt_redactions")


def _read() -> dict[str, Any]:
    try:
        data = json.loads(app_settings.policy_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {k: data[k] for k in KEYS if isinstance(data, dict) and k in data}


def current() -> dict[str, Any]:
    """The policy as it applies now, with problems in the file reported rather than guessed at."""
    raw = _read()
    out: dict[str, Any] = {"enabled": None, "max_permissions": list(PERMISSIONS), "http": True, "extensions": None,
                           "problems": [], "set": sorted(raw)}
    if "integrations" in raw:
        if raw["integrations"] is False:
            out["enabled"] = False  # true means "the person decides", the same as no key
        elif not isinstance(raw["integrations"], bool):
            out["problems"].append("integrations should be true or false")
            out["enabled"] = False  # unreadable means off: fail closed
    if "integrations_max_permissions" in raw:
        value = raw["integrations_max_permissions"]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            unknown = [v for v in value if v not in PERMISSIONS]
            if unknown:
                out["problems"].append(f"integrations_max_permissions has unknown categories: {', '.join(unknown)}")
            out["max_permissions"] = [p for p in PERMISSIONS if p in value]
        else:
            out["problems"].append("integrations_max_permissions should be a list of permission categories")
            out["max_permissions"] = ["READ_METADATA"]  # fail closed to the least
    if "integrations_http" in raw:
        if isinstance(raw["integrations_http"], bool):
            out["http"] = raw["integrations_http"]
        else:
            out["problems"].append("integrations_http should be true or false")
            out["http"] = False
    if "integrations_extensions" in raw:
        value = raw["integrations_extensions"]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            out["extensions"] = list(value)
        else:
            out["problems"].append("integrations_extensions should be a list of extension ids")
            out["extensions"] = []  # fail closed: none
    return out


def receipt_redactions() -> list[str]:
    """The receipt parts IT requires every export to leave out. An unreadable value hides them all."""
    from ..receipts import REDACTIONS
    value = _read().get("receipt_redactions")
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v for v in value if v in REDACTIONS]
    return list(REDACTIONS)  # fail closed
