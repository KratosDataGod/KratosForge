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
    kernel_max_sessions      with kernel_process: how many notebooks keep a process of their own;
                     opening one more stops the least recently used idle one (default 5)
    kernel_idle_minutes      with kernel_process: stop a notebook's process after this many idle
                     minutes; 0 (default) keeps it
    support_url      where "Report a problem" goes: an https link or a mailto: address
    platform         IT only: the organization's Platform workspaces, {"prod": {"workspace_id": ...}, ...},
                     so every PC joins the same platform without anyone picking it
    auto_update      IT only: false stops the app updating itself; IT then rolls out new builds
                     (default true)
    allow_fabric_runs  IT only: false stops the app starting jobs on Fabric capacity (Run in Fabric,
                     Fabric schedules, table maintenance, the Fabric side of proofs); work stays on
                     the PC (default true)
    allow_optimize_lakehouse  IT only: false stops Optimize creating, connecting to or writing to a
                     shared optimization lakehouse (default true)
    allow_replay_samples  IT only: false removes "A sample of the input data" from replay bundles
                     (replay.py), so a bundle never holds lakehouse data (default true)
    allowed_tenants  IT only: Microsoft Entra tenant ids people may sign in to; any other account is
                     signed out again (default: any)
    local_retention_days  IT only: local copies of lakehouse data (file previews, staged writes, the
                     metadata framework's download cache) are deleted after this many days unused;
                     0 keeps them (default 30)
    sql_page_rows    the SQL page: rows on each page of a result (default 1000, 100 to 10000)
    sql_time_limit_minutes  the SQL page: minutes a query may run before it's stopped (default 10,
                     0 for no limit, at most 1440)
    sql_memory_limit_percent  the SQL page: share of this PC's memory one query may use (default 60,
                     10 to 90); a larger result spills to the temp folder
    sql_max_concurrent  the SQL page: queries that run at once; more wait in a queue (default 2, 1 to 8)
    sql_temp_dir     the SQL page: folder for spilled data and result snapshots (default: the
                     app data folder's sql-runs)
    sql_result_retention_hours  the SQL page: hours a finished query's result is kept for paging and
                     export (default 24, 0 keeps it only until the app closes, at most 168)
    telemetry        true to send daily usage counts (telemetry.py); off unless the person turns it on,
                     and IT can fix it either way (default false)
    telemetry_connection_string  IT only: an Application Insights connection string, so usage data
                     goes to the organization's own Azure Monitor instead of KratosBI's
    incident_sla     IT only: minutes to acknowledge and resolve an incident, per priority:
                     {"P1": {"acknowledge_minutes": 15, "resolve_minutes": 240}, ...}; 0 for no target
                     (default: workloads/sla.py DEFAULT_TARGETS)

The same file also holds the integrations_* and receipt_redactions keys; integrations/policy.py
reads them through read_policy() here, so both read the file by the same rules.

Restrictions fail closed. A policy file that exists but can't be read is replaced by the last copy
that could be read (policy-last-good.json next to the user's settings), as Group Policy keeps its
last applied settings; with no copy, and for a restriction with the wrong kind of value, that
restriction takes its most restrictive value (RESTRICTED here, and integrations/policy.py's for its
keys) rather than the default, except that an unreadable file with no copy doesn't stop sign-in. A missing file means no policy, and deletes the copy.
Other keys of the wrong kind are skipped, so the next place (environment, user, default) applies.
policy_problems() says what in the policy file was ignored or restricted and why, so a typo doesn't
fail silently.
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
                            "allow_replay_samples": True, "allowed_tenants": [], "local_retention_days": 30, "telemetry": False,
                            "telemetry_connection_string": "",
                            "cell_time_limit_minutes": 0, "kernel_memory_limit_gb": 0,
                            "kernel_max_sessions": 5, "kernel_idle_minutes": 0,
                            "allow_optimize_lakehouse": True}
TYPES: dict[str, type | tuple[type, ...]] = {"client_id": str, "channel": str, "show_preview": bool, "support_url": str,
                                            "platform": dict, "kernel_process": bool, "auto_update": bool,
                                            "allow_fabric_runs": bool, "allow_replay_samples": bool,
                                            "allowed_tenants": list,
                                            "local_retention_days": int, "telemetry": bool,
                                            "telemetry_connection_string": str,
                                            "cell_time_limit_minutes": (int, float), "kernel_memory_limit_gb": (int, float),
                                            "kernel_max_sessions": (int, float), "kernel_idle_minutes": (int, float),
                                            "allow_optimize_lakehouse": bool}
USER_KEYS = ("client_id", "channel", "show_preview", "kernel_process", "telemetry",  # support_url is for IT only
             "cell_time_limit_minutes", "kernel_memory_limit_gb", "kernel_max_sessions", "kernel_idle_minutes")
LIMITS = {"cell_time_limit_minutes": 7 * 24 * 60, "kernel_memory_limit_gb": 1024,
          "kernel_max_sessions": 20, "kernel_idle_minutes": 7 * 24 * 60}  # the largest each accepts
GUID_CHARS = set("0123456789abcdefABCDEF-")


# The SQL page's run limits (SQL-003, sql_exec.py): the smallest and largest each accepts.
SQL_RANGES: dict[str, tuple[int, int]] = {"sql_page_rows": (100, 10_000), "sql_time_limit_minutes": (0, 24 * 60),
                                          "sql_memory_limit_percent": (10, 90), "sql_max_concurrent": (1, 8),
                                          "sql_result_retention_hours": (0, 7 * 24)}
DEFAULTS.update({"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 60,
                 "sql_max_concurrent": 2, "sql_temp_dir": "", "sql_result_retention_hours": 24})
TYPES.update({key: int for key in SQL_RANGES} | {"sql_temp_dir": str})
USER_KEYS += (*SQL_RANGES, "sql_temp_dir")

# Incident SLA targets (workloads/sla.py checks the shape and says what it ignored).
DEFAULTS["incident_sla"] = {}
TYPES["incident_sla"] = dict
# Read by integrations/policy.py, which decides what each means; listed here so the policy file has
# one list of settings.
INTEGRATION_KEYS = ("integrations", "integrations_max_permissions", "integrations_http", "integrations_extensions",
                    "receipt_redactions")
POLICY_KEYS = (*DEFAULTS, *INTEGRATION_KEYS)
# No real tenant has the nil GUID, so allowed_tenants of just this lets no account sign in.
NO_TENANT = "00000000-0000-0000-0000-000000000000"
# The restrictions among DEFAULTS, and the value each takes when the policy file or that key can't
# be read. Updates (auto_update, channel) are enforced by the shell, which reads the file itself.
RESTRICTED: dict[str, Any] = {"allow_fabric_runs": False, "allowed_tenants": [NO_TENANT], "show_preview": False,
                              "telemetry": False}


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


def saved_policy_path() -> Path:
    """The last policy file that could be read, kept for when the file later breaks."""
    return user_path().with_name("policy-last-good.json")


def _policy_file() -> tuple[dict[str, Any], str | None, bool]:
    """(settings, why the file can't be used, whether the saved copy stands in for it)."""
    path = policy_path()
    saved = saved_policy_path()
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except FileNotFoundError:
        saved.unlink(missing_ok=True)  # IT removed the policy: an old copy mustn't come back later
        return {}, None, False
    except (OSError, ValueError) as exc:
        reason = f"can't be read as JSON ({exc})" if isinstance(exc, ValueError) else f"can't be opened ({exc})"
    else:
        if isinstance(data, dict):
            _save_copy(saved, text)
            return data, None, False
        reason = "must hold one JSON object"
    try:
        copy = json.loads(saved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        copy = None
    if isinstance(copy, dict):
        return copy, (f"{path} {reason}, so the last copy that could be read ({_saved_at(saved)}) applies "
                      "until it's fixed"), True
    return {}, (f"{path} {reason}, so none of it applies and every setting it can restrict takes its most "
                "restrictive value until it's fixed: no runs in Fabric, Preview features, telemetry and "
                "external AI tools off. Sign-in still works, and accounts from any tenant can sign in"), False


def _save_copy(saved: Path, text: str) -> None:
    try:
        if saved.exists() and saved.read_text(encoding="utf-8") == text:
            return
        saved.parent.mkdir(parents=True, exist_ok=True)
        tmp = saved.with_name(f"{saved.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, saved)
    except OSError:  # no copy this time; the policy itself still applies
        pass


def _saved_at(saved: Path) -> str:
    import datetime as dt
    try:
        return "saved " + dt.datetime.fromtimestamp(saved.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "saved earlier"


def read_policy() -> tuple[dict[str, Any], str | None]:
    """The policy file's settings, and why nothing can stand in for it when it exists but can't be
    read (then every restriction but sign-in takes its most restrictive value). A broken file is replaced by the
    last copy that could be read, as Group Policy keeps its last applied settings. No file: ({}, None)."""
    data, reason, from_copy = _policy_file()
    return data, None if from_copy else reason


def _restricted(key: str, policy: dict[str, Any], error: str | None) -> bool:
    """``key`` is a restriction the policy file means to set but can't: the file is unreadable, or
    the key has the wrong kind of value."""
    if key not in RESTRICTED:
        return False
    if error:
        return key != "allowed_tenants"  # people can still sign in and work on their PC (Chris, 2026-10-18)
    if key not in policy:
        return False
    v = policy[key]
    return not _typed(key, v) or (key == "allowed_tenants" and not all(isinstance(t, str) for t in v))


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
    """What in the organization's policy file is ignored or restricted, and why; empty when all of it applies."""
    data, reason, from_copy = _policy_file()
    if reason and not from_copy:
        return [reason]
    problems = [reason] if reason else []
    if not from_copy and user_can_change(policy_path()):
        problems.append(f"{policy_path()} can be changed by the account you're signed in to this PC with, so "
                        "anyone using it can change what it allows. Ask IT to push it so only administrators can change it")
    for key, value in data.items():
        if key not in POLICY_KEYS:
            problems.append(f"{key} isn't a setting (settings: {', '.join(POLICY_KEYS)})")
        elif key in INTEGRATION_KEYS:
            continue  # integrations/policy.py checks these, below
        elif not _typed(key, value):
            kind = TYPES[key]
            name = " or ".join(t.__name__ for t in kind) if isinstance(kind, tuple) else kind.__name__
            problems.append(f"{key} should be {name}, not {type(value).__name__}"
                            + (", so it takes its most restrictive value" if key in RESTRICTED else ""))
        elif key == "channel" and value not in CHANNELS:
            problems.append(f"channel must be one of {', '.join(CHANNELS)}")
        elif key in SQL_RANGES and not SQL_RANGES[key][0] <= value <= SQL_RANGES[key][1]:
            problems.append(f"{key} must be between {SQL_RANGES[key][0]} and {SQL_RANGES[key][1]}")
        elif key == "client_id" and value and not valid_client_id(value):
            problems.append("client_id isn't an app (client) ID")
        elif key == "local_retention_days" and value < 0:
            problems.append("local_retention_days can't be negative (0 keeps local copies)")
        elif key in LIMITS and not 0 <= value <= LIMITS[key]:
            problems.append(f"{key} must be between 0 and {LIMITS[key]} (0 for no limit)")
        elif key == "telemetry_connection_string" and value and not _telemetry_target(value):
            problems.append("telemetry_connection_string isn't an Application Insights connection string "
                            "(InstrumentationKey=...;IngestionEndpoint=https://...)")
        elif key == "allowed_tenants" and not all(isinstance(t, str) and valid_client_id(t) for t in value):
            problems.append("allowed_tenants must list tenant ids, like 11111111-2222-3333-4444-555555555555"
                            + ("; with other values in it, no account can sign in"
                               if not all(isinstance(t, str) for t in value) else ""))
        elif key == "incident_sla":
            from .workloads.sla import targets
            problems.extend(targets(value)[1])
    if any(key in data for key in INTEGRATION_KEYS):
        from .integrations import policy as integrations_policy
        problems += integrations_policy.current()["problems"]
    return problems


def user_can_change(path: Path) -> bool:
    """Whether the account running the app can change or replace ``path``: write it, delete it, or
    take over its permissions or its folder's (the folder's owner can do all of these). An
    administrator, who can change it anyway, isn't counted. Only a warning: the engine itself is in
    the person's own profile, so the policy file stops mistakes, not someone set on getting round
    it (security.md)."""
    try:
        if not path.is_file():
            return False
        if sys.platform == "win32":
            return _windows_can_change(path)
        if os.geteuid() == 0:
            return False
        folder = path.parent
        return (os.access(path, os.W_OK) or os.stat(path).st_uid == os.geteuid()
                or os.stat(folder).st_uid == os.geteuid()
                or (os.access(folder, os.W_OK) and not os.stat(folder).st_mode & 0o1000))
    except (OSError, AttributeError):
        return False


# Rights that let someone change or replace a file without writing to it first. Creating files in
# the folder isn't one: everyone may create files under %ProgramData%, but not replace IT's file.
_FILE_RIGHTS = (0x2, 0x10000, 0x40000, 0x80000)  # FILE_WRITE_DATA, DELETE, WRITE_DAC, WRITE_OWNER
_FOLDER_RIGHTS = (0x40, 0x40000, 0x80000)  # FILE_DELETE_CHILD, WRITE_DAC, WRITE_OWNER


def _windows_can_change(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes
    if ctypes.windll.shell32.IsUserAnAdmin():
        return False
    create = ctypes.windll.kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE)
    invalid = wintypes.HANDLE(-1).value

    def opens(target: Path, access: int) -> bool:
        # Opening only asks for the right; nothing is written. Share everything so IT's push isn't blocked.
        handle = create(str(target), access, 0x7, None, 3, 0x02000000, None)  # OPEN_EXISTING, BACKUP_SEMANTICS
        if handle in (None, invalid):
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return (any(opens(path, right) for right in _FILE_RIGHTS)
            or any(opens(path.parent, right) for right in _FOLDER_RIGHTS))


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


def _telemetry_target(text: str) -> bool:
    from .telemetry import valid_connection_string
    return valid_connection_string(text)


def tenant_allowed(tenant_id: str | None) -> bool:
    """Whether the organization lets people sign in to this tenant (any, unless policy lists some)."""
    allowed = [t.lower() for t in value("allowed_tenants") or []]
    return not allowed or (tenant_id or "").lower() in allowed


def valid_client_id(value: str) -> bool:
    value = value.strip()
    return len(value) == 36 and set(value) <= GUID_CHARS and value.count("-") == 4


def load() -> dict[str, Any]:
    """Each setting's value and where it came from: policy, environment, user, built_in or default."""
    policy, error = read_policy()
    user = _read(user_path())
    out: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if _restricted(key, policy, error):
            out[key] = {"value": RESTRICTED[key], "source": "policy"}
            continue
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
        if key in SQL_RANGES:
            new = _sql_number(key, new)
        if key == "sql_temp_dir":
            new = _sql_folder(new)
        if key == "client_id":
            new = str(new or "").strip()
            if new and not valid_client_id(new):
                raise ValueError("That isn't an app (client) ID. It looks like 00000000-0000-0000-0000-000000000000.")
        if key in ("show_preview", "kernel_process", "telemetry"):
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


def _sql_number(key: str, new: Any) -> int | None:
    """An SQL page limit: a whole number in SQL_RANGES[key]; empty goes back to the default."""
    if new in (None, ""):
        return None
    try:
        n = int(new)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a whole number") from None
    low, high = SQL_RANGES[key]
    if not low <= n <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return n


def _sql_folder(new: Any) -> str:
    """The SQL page's temp folder: an absolute folder path, or empty for the default."""
    text = str(new or "").strip()
    if text and not Path(text).is_absolute():
        raise ValueError("sql_temp_dir must be a full folder path, like C:\\Temp\\FabricDesktop")
    return text


def sql_limits() -> dict[str, Any]:
    """The SQL page's limits as they apply now: a value out of range (an old file, a typo) falls
    back to the default."""
    out: dict[str, Any] = {}
    for key in (*SQL_RANGES, "sql_temp_dir"):
        v = value(key)
        if key in SQL_RANGES and not SQL_RANGES[key][0] <= v <= SQL_RANGES[key][1]:
            v = DEFAULTS[key]
        out[key] = v
    return out
