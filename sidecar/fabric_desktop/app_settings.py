"""App settings: what this person chose, what their organization set, and what the build ships with.

Three places, strongest first:

    policy    %ProgramData%\\FabricDesktop\\policy.json, pushed by IT (Intune, Group Policy files).
              Anything set here wins and shows as "Set by your organization".
    user      %LOCALAPPDATA%\\FabricDesktop\\app-settings.json, written by the Settings page (the
              sidecar's settings.json, beside it, holds other things).
    built in  engine.json next to the engine's python.exe (written by CI), or
              FABRIC_DESKTOP_BUILTIN_CLIENT_ID for builds that run a Python of their own.

With none of them, the app signs in through Microsoft's own Fabric sign-in app
(auth.MICROSOFT_CLIENT_ID), so nobody has to register an app.

The FABRIC_DESKTOP_CLIENT_ID environment variable still works, between policy and user, so
installs set up with setx keep signing in as before.

Keys:
    client_id        the Microsoft Entra app (client) ID the app signs in with; only for an
                     organization that wants its own app registration
    channel          "stable" (default) or "preview": which release the app updates to
    show_preview     true to show features still in preview (default true)
    kernel_process   true to run each notebook's cells in a Python process of its own, so Stop
                     and crashes end only that process (default false while in preview)
    cell_time_limit_minutes  with kernel_process: minutes a cell may run before it's interrupted;
                     0 (default) for no limit
    kernel_memory_limit_gb   with kernel_process: gigabytes the notebook's process may use before
                     it's ended; 0 (default) for no limit
    support_url      where "Report a problem" goes: an https link or a mailto: address
    platform         IT only: the organization's Platform workspaces, {"prod": {"workspace_id": ...}, ...},
                     so every PC joins the same platform without anyone picking it
    auto_update      IT only: false stops the app updating itself; IT then rolls out new builds
                     (default true)
    allow_fabric_runs  IT only: false stops the app starting jobs on Fabric capacity (Run in Fabric,
                     Fabric schedules, table maintenance, the Fabric side of proofs); work stays on
                     the PC (default true)
    allowed_tenants  IT only: Microsoft Entra tenant ids people may sign in to; any other account is
                     signed out again (default: any)
    local_retention_days  IT only: local copies of lakehouse data (file previews, staged writes, the
                     metadata framework's download cache) are deleted after this many days unused;
                     0 keeps them (default 30)

policy_problems() says what in the policy file was ignored and why, so a typo doesn't fail silently.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .auth import MICROSOFT_CLIENT_ID

CHANNELS = ("stable", "preview")
DEFAULTS: dict[str, Any] = {"client_id": MICROSOFT_CLIENT_ID, "channel": "stable", "show_preview": True, "support_url": "",
                            "platform": {}, "kernel_process": False, "auto_update": True, "allow_fabric_runs": True,
                            "allowed_tenants": [], "local_retention_days": 30,
                            "cell_time_limit_minutes": 0, "kernel_memory_limit_gb": 0}
TYPES: dict[str, type | tuple[type, ...]] = {"client_id": str, "channel": str, "show_preview": bool, "support_url": str,
                                            "platform": dict, "kernel_process": bool, "auto_update": bool,
                                            "allow_fabric_runs": bool, "allowed_tenants": list,
                                            "local_retention_days": int,
                                            "cell_time_limit_minutes": (int, float), "kernel_memory_limit_gb": (int, float)}
USER_KEYS = ("client_id", "channel", "show_preview", "kernel_process",  # support_url is for IT only
             "cell_time_limit_minutes", "kernel_memory_limit_gb")
LIMITS = {"cell_time_limit_minutes": 7 * 24 * 60, "kernel_memory_limit_gb": 1024}  # the largest each accepts
GUID_CHARS = set("0123456789abcdefABCDEF-")


def policy_path() -> Path:
    base = os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA") or "/etc"
    return Path(base) / "FabricDesktop" / "policy.json"


def user_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "FabricDesktop" / "app-settings.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in DEFAULTS} if isinstance(data, dict) else {}


def built_in_client_id() -> str:
    env = os.environ.get("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", "")
    if env:
        return env
    info = _read_engine_info()
    return str(info.get("client_id") or "")


def _read_engine_info() -> dict[str, Any]:
    try:
        return json.loads(Path(sys.executable).with_name("engine.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def policy_problems() -> list[str]:
    """What in the organization's policy file is ignored, and why; empty when all of it applies."""
    path = policy_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [f"{path} can't be read as JSON, so none of it applies: {exc}"]
    if not isinstance(data, dict):
        return [f"{path} must hold one JSON object, so none of it applies"]
    problems = []
    for key, value in data.items():
        if key not in DEFAULTS:
            problems.append(f"{key} isn't a setting (settings: {', '.join(DEFAULTS)})")
        elif not _typed(key, value):
            kind = TYPES[key]
            name = " or ".join(t.__name__ for t in kind) if isinstance(kind, tuple) else kind.__name__
            problems.append(f"{key} should be {name}, not {type(value).__name__}")
        elif key == "channel" and value not in CHANNELS:
            problems.append(f"channel must be one of {', '.join(CHANNELS)}")
        elif key == "client_id" and value and not valid_client_id(value):
            problems.append("client_id isn't an app (client) ID")
        elif key == "local_retention_days" and value < 0:
            problems.append("local_retention_days can't be negative (0 keeps local copies)")
        elif key in LIMITS and not 0 <= value <= LIMITS[key]:
            problems.append(f"{key} must be between 0 and {LIMITS[key]} (0 for no limit)")
        elif key == "allowed_tenants" and not all(isinstance(t, str) and valid_client_id(t) for t in value):
            problems.append("allowed_tenants must list tenant ids, like 00000000-0000-0000-0000-000000000000")
    return problems


def _typed(key: str, value: Any) -> bool:
    """``value`` has the type ``key`` needs (true and false don't count as numbers)."""
    if isinstance(value, bool) and TYPES[key] is not bool:
        return False
    return isinstance(value, TYPES[key])


def _in_range(key: str, value: Any) -> bool:
    if key == "local_retention_days":
        return value >= 0
    if key in LIMITS:
        return 0 <= value <= LIMITS[key]
    return True


def tenant_allowed(tenant_id: str | None) -> bool:
    """Whether the organization lets people sign in to this tenant (any, unless policy lists some)."""
    allowed = [t.lower() for t in value("allowed_tenants") or []]
    return not allowed or (tenant_id or "").lower() in allowed


def valid_client_id(value: str) -> bool:
    value = value.strip()
    return len(value) == 36 and set(value) <= GUID_CHARS and value.count("-") == 4


def load() -> dict[str, Any]:
    """Each setting's value and where it came from: policy, environment, user, built_in or default."""
    policy, user = _read(policy_path()), _read(user_path())
    out: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        candidates = [("policy", policy.get(key))]
        if key == "client_id":
            candidates.append(("environment", os.environ.get("FABRIC_DESKTOP_CLIENT_ID")))
        if key in USER_KEYS:
            candidates.append(("user", user.get(key)))
        if key == "client_id":
            candidates.append(("built_in", built_in_client_id()))
        value, source = default, "default"
        for where, candidate in candidates:
            if candidate not in (None, "") and _typed(key, candidate) and _in_range(key, candidate):
                value, source = candidate, where
                break
        if key == "channel" and value not in CHANNELS:
            value, source = default, "default"
        out[key] = {"value": value, "source": source}
    return out


def value(key: str) -> Any:
    return load()[key]["value"]


def save(changes: dict[str, Any]) -> dict[str, Any]:
    """Saves this person's choices. A setting the organization fixed can't be changed here."""
    current = load()
    user = _read(user_path())
    for key, new in changes.items():
        if key not in USER_KEYS:
            raise ValueError(f"{key} is not a setting you can change")
        if current[key]["source"] == "policy":
            raise ValueError(f"Your organization sets {key}, so it can't be changed here")
        if key == "channel" and new not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
        if key == "client_id":
            new = str(new or "").strip()
            if new and not valid_client_id(new):
                raise ValueError("That isn't an app (client) ID. It looks like 00000000-0000-0000-0000-000000000000.")
        if key in ("show_preview", "kernel_process"):
            new = bool(new)
        if key in LIMITS:
            new = _limit(key, new)
        if new in (None, ""):
            user.pop(key, None)
        else:
            user[key] = new
    path = user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(user, indent=2), encoding="utf-8")
    return load()


def _limit(key: str, new: Any) -> float | None:
    """A limit setting: a number from 0 (no limit, not stored) to LIMITS[key]."""
    try:
        n = float(new or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None
    if not 0 <= n <= LIMITS[key]:
        raise ValueError(f"{key} must be between 0 and {LIMITS[key]}")
    return n or None
