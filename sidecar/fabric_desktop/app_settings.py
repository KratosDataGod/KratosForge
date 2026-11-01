"""App settings: what this person chose, what their organization set, and what the build ships with.

Three places, strongest first:

    policy    %ProgramData%\\FabricDesktop\\policy.json, pushed by IT (Intune, Group Policy files).
              Anything set here wins and shows as "Set by your organization's policy".
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
    forge_animations  the first motion switch, kept so older settings and policy files still work:
                     false means motion_level 0 unless motion_level itself is set
    motion_level     visual intensity (docs/forge-animations.md): 0 Off, 1 Minimal (functional
                     transitions only), 2 Refined (default), 3 Expressive
    motion_wolf, motion_sparks, motion_ambient  true (default) to show that family of effects at
                     levels 2 and 3; motion_achievements the same, but false by default
    motion_follow_system  true (default): Windows' reduced-motion setting turns decorative motion
                     off whatever the level; false lets the chosen level apply anyway
    ui_explanations  "on_request" (default): each page's How this works panel and its one-line
                     explanation open from the title bar's info button; "beside": they show on the
                     page (Settings > Appearance > Layout)
    ui_preview_marks  "tags" (default): Preview features carry a Preview tag; "dots": a small dot,
                     with the tag kept on page titles
    ui_density       "comfortable" (default) or "compact": spacing in tables, lists and toolbars
    learn_mode       how much learning guidance shows beside your work (docs/learn.md): "expert"
                     findings, About and Dismiss; "professional" (default) adds a line on why and
                     Explain and Show example on request; "guided" opens Explain, adds the lesson
                     that covers a finding and suggests the next lesson on Learn's Home. The first
                     names, "focus" and "learning", still read as "expert" and "guided"
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
    allow_notebook_packages  IT only: false stops people installing packages into notebook
                     environments (environments.py); the engine's own packages still work
    allow_optimize_lakehouse  IT only: false stops Optimize creating, connecting to or writing to a
                     shared optimization lakehouse (default true)
    allow_replay_samples  IT only: false removes "A sample of the input data" from replay bundles
                     (replay.py), so a bundle never holds lakehouse data (default true)
    allowed_tenants  IT only: Microsoft Entra tenant ids people may sign in to; any other account is
                     signed out again (default: any)
    local_retention_days  IT only: local copies of lakehouse data (file previews, staged writes, the
                     metadata framework's download cache) are deleted after this many days unused;
                     0 keeps them (default 30)
    onelake_writes   "local_first" (default): a notebook cell's table writes are staged on this PC and
                     upload in the background while the cell goes on; the cell ends when they're in.
                     "direct": each write goes to OneLake before the next line runs
    onelake_compression  "zstd" (default) or "snappy": the Parquet codec for tables written to
                     OneLake from this PC. zstd files are about a quarter smaller, so they upload faster
    onelake_skip_unchanged  true (default): an overwrite whose rows match what this app last wrote to
                     the table uploads nothing and makes no new version
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
    sql_warehouse_writes  the Warehouse page: false refuses every change to a warehouse (DDL, DML,
                     EXEC), so the page only reads; IT can fix it either way (default true; each
                     change still needs its own confirm)
    wh_monitoring_views  the Warehouse page's Monitoring tab: false stops the app creating monitoring
                     views (KQL querysets) in Fabric; reading monitoring still works (default true)
    wh_monitoring_auto  the workspaces whose warehouses get monitoring views without asking again,
                     by workspace id; the person adds one when they tick it on the build confirm
    wh_open_in       what opening a warehouse or SQL analytics endpoint by its name does: "kratosforge"
                     opens the Warehouse page, "fabric" Fabric's own editor in the Fabric portal tab
                     (default kratosforge; Open in Fabric stays on every row)
    fabric_full_screen  the Fabric portal tab fills the window, with a slim bar to leave full
                     screen (default false; the tab's Full screen button sets it)
    telemetry        true to send daily usage counts (telemetry.py); off unless the person turns it on,
                     and IT can fix it either way (default false)
    telemetry_connection_string  IT only: an Application Insights connection string, so usage data
                     goes to the organization's own Azure Monitor instead of KratosBI's
    incident_sla     IT only: minutes to acknowledge and resolve an incident, per priority:
                     {"P1": {"acknowledge_minutes": 15, "resolve_minutes": 240}, ...}; 0 for no target
                     (default: workloads/sla.py DEFAULT_TARGETS)

    fabric_view_sign_in_hosts  IT only, read by the shell: exact host names of the organization's
                     own identity provider (AD FS, Okta, Ping), which the Fabric view may open during
                     sign-in, e.g. ["adfs.contoso.com"] (default: none)
    register_link_scheme  IT only, read by the shell: false stops the app registering kratosforge://
                     links for the Windows user, and removes a registration it made (default true)

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

from . import jsonfile
from .redact import one_line
from .auth import MICROSOFT_CLIENT_ID

CHANNELS = ("stable", "preview")
DEFAULTS: dict[str, Any] = {"client_id": MICROSOFT_CLIENT_ID, "channel": "stable", "show_preview": True, "support_url": "",
                            "platform": {}, "kernel_process": False, "auto_update": True, "allow_fabric_runs": True,
                            "allow_replay_samples": True, "allowed_tenants": [], "local_retention_days": 30, "telemetry": False,
                            "telemetry_connection_string": "",
                            "cell_time_limit_minutes": 0, "kernel_memory_limit_gb": 0,
                            "kernel_max_sessions": 5, "kernel_idle_minutes": 0,
                            "allow_optimize_lakehouse": True, "allow_notebook_packages": True, "forge_animations": True}
TYPES: dict[str, type | tuple[type, ...]] = {"client_id": str, "channel": str, "show_preview": bool, "support_url": str,
                                            "platform": dict, "kernel_process": bool, "auto_update": bool,
                                            "allow_fabric_runs": bool, "allow_replay_samples": bool,
                                            "allowed_tenants": list,
                                            "local_retention_days": int, "telemetry": bool,
                                            "telemetry_connection_string": str,
                                            "cell_time_limit_minutes": (int, float), "kernel_memory_limit_gb": (int, float),
                                            "kernel_max_sessions": (int, float), "kernel_idle_minutes": (int, float),
                                            "allow_optimize_lakehouse": bool, "allow_notebook_packages": bool,
                                            "forge_animations": bool}
USER_KEYS: tuple[str, ...] = ("client_id", "channel", "show_preview", "kernel_process", "telemetry",  # support_url is for IT only
             "cell_time_limit_minutes", "kernel_memory_limit_gb", "kernel_max_sessions", "kernel_idle_minutes",
             "forge_animations")
LIMITS = {"cell_time_limit_minutes": 7 * 24 * 60, "kernel_memory_limit_gb": 1024,
          "kernel_max_sessions": 20, "kernel_idle_minutes": 7 * 24 * 60}  # the largest each accepts
GUID_CHARS = set("0123456789abcdefABCDEF-")
# Settings an environment variable can set, between policy and user (load() reads them).
ENV_VARS = {"client_id": "FABRIC_DESKTOP_CLIENT_ID"}


# The SQL page's run limits (SQL-003, sql_exec.py): the smallest and largest each accepts.
SQL_RANGES: dict[str, tuple[int, int]] = {"sql_page_rows": (100, 10_000), "sql_time_limit_minutes": (0, 24 * 60),
                                          "sql_memory_limit_percent": (10, 90), "sql_max_concurrent": (1, 8),
                                          "sql_result_retention_hours": (0, 7 * 24)}
DEFAULTS.update({"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 60,
                 "sql_max_concurrent": 2, "sql_temp_dir": "", "sql_result_retention_hours": 24})
DEFAULTS["sql_warehouse_writes"] = True
TYPES.update({key: int for key in SQL_RANGES} | {"sql_temp_dir": str, "sql_warehouse_writes": bool})
USER_KEYS += (*SQL_RANGES, "sql_temp_dir", "sql_warehouse_writes")
DEFAULTS.update({"wh_monitoring_views": True, "wh_monitoring_auto": []})
TYPES.update({"wh_monitoring_views": bool, "wh_monitoring_auto": list})
USER_KEYS += ("wh_monitoring_views", "wh_monitoring_auto")

# Motion and effects (app/ui/motion.js): the visual intensity level and one switch per family.
MOTION_LEVELS = (0, 3)
MOTION_SWITCHES = ("motion_wolf", "motion_sparks", "motion_achievements", "motion_ambient", "motion_follow_system")
# Achievement celebrations are something people turn on (UX review, #361); the rest start on.
DEFAULTS.update({"motion_level": 2} | {key: key != "motion_achievements" for key in MOTION_SWITCHES})
TYPES.update({"motion_level": int} | {key: bool for key in MOTION_SWITCHES})
USER_KEYS += ("motion_level", *MOTION_SWITCHES)

# Owner coverage's last-used dates (central/core.py note_seen): personal data everyone who reads the
# platform's catalog sees, so it stays off until IT turns it on in the policy file. Policy only.
DEFAULTS["platform_presence"] = False
TYPES["platform_presence"] = bool
# Incident SLA targets (workloads/sla.py checks the shape and says what it ignored).
DEFAULTS["incident_sla"] = {}
TYPES["incident_sla"] = dict
# OneLake writes from notebooks on this PC (lakehouse.write_table, publish). onelake_writes is the
# default for notebooks that don't choose; each notebook can set its own.
CHOICES: dict[str, tuple[str, ...]] = {"onelake_writes": ("local_first", "direct"), "onelake_compression": ("zstd", "snappy")}
DEFAULTS.update({"onelake_writes": "local_first", "onelake_compression": "zstd", "onelake_skip_unchanged": True})
TYPES.update({"onelake_writes": str, "onelake_compression": str, "onelake_skip_unchanged": bool})
USER_KEYS += ("onelake_writes", "onelake_compression", "onelake_skip_unchanged")
# Opening a warehouse by its name, and the Fabric portal tab's full screen (app.js renderFabric).
CHOICES["wh_open_in"] = ("kratosforge", "fabric")
DEFAULTS.update({"wh_open_in": "kratosforge", "fabric_full_screen": False})
TYPES.update({"wh_open_in": str, "fabric_full_screen": bool})
USER_KEYS += ("wh_open_in", "fabric_full_screen")
# Layout (app/ui/features.js applyAppearance): how every page shows the same things; nothing is hidden.
CHOICES.update({"ui_explanations": ("beside", "on_request"), "ui_preview_marks": ("tags", "dots"),
                "ui_density": ("comfortable", "compact")})
DEFAULTS.update({"ui_explanations": "on_request", "ui_preview_marks": "tags", "ui_density": "comfortable"})
TYPES.update({"ui_explanations": str, "ui_preview_marks": str, "ui_density": str})
USER_KEYS += ("ui_explanations", "ui_preview_marks", "ui_density")
# Learn (app/ui/learn.js): guidance beside findings. Checks run the same in every mode.
CHOICES["learn_mode"] = ("expert", "professional", "guided")
DEFAULTS["learn_mode"] = "professional"
TYPES["learn_mode"] = str
USER_KEYS += ("learn_mode",)
# A value saved, or set by policy, under a name a setting no longer has, read as its new name.
RENAMED: dict[str, dict[str, str]] = {"learn_mode": {"focus": "expert", "learning": "guided"}}
# Databricks Connect (engines/dbconnect.py): whether the app may install its client from the
# hash-pinned lock files, and the package index to fetch them from (empty: the default index).
DEFAULTS.update({"allow_dbconnect_install": True, "dbconnect_index_url": ""})
TYPES.update({"allow_dbconnect_install": bool, "dbconnect_index_url": str})
# Read by integrations/policy.py, which decides what each means; listed here so the policy file has
# one list of settings.
INTEGRATION_KEYS = ("integrations", "integrations_max_permissions", "integrations_http", "integrations_extensions",
                    "integrations_editors", "receipt_redactions")
# Read only by the shell (main.rs), which applies them itself; listed so the file has one list of
# settings and policy_problems() can say what the shell will skip.
SHELL_KEYS = ("fabric_view_sign_in_hosts", "register_link_scheme", "engine_download_url")
POLICY_KEYS = (*DEFAULTS, *INTEGRATION_KEYS, *SHELL_KEYS)
# No real tenant has the nil GUID, so allowed_tenants of just this lets no account sign in.
NO_TENANT = "00000000-0000-0000-0000-000000000000"
# The restrictions among DEFAULTS, and the value each takes when the policy file or that key can't
# be read. Updates (auto_update, channel) are enforced by the shell, which reads the file itself.
RESTRICTED: dict[str, Any] = {"allow_fabric_runs": False, "allowed_tenants": [NO_TENANT], "show_preview": False,
                              "telemetry": False, "sql_warehouse_writes": False,
                              "wh_monitoring_views": False,
                              "allow_dbconnect_install": False,
                              "platform_presence": False,
                              "allow_replay_samples": False, "allow_optimize_lakehouse": False,
                              "allow_notebook_packages": False}


def policy_path() -> Path:
    base = os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA") or "/etc"
    return Path(base) / "FabricDesktop" / "policy.json"


def user_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "FabricDesktop" / "app-settings.json"


def _read(path: Path) -> dict[str, Any]:
    """The person's own settings. A file that can't be parsed (a write cut short, a bad hand edit) is
    moved aside as ``<name>.corrupt-<timestamp>`` rather than left for the next save to overwrite (QA-011)."""
    try:
        data = json.loads(jsonfile.read_text(path).lstrip("\ufeff"))
    except ValueError:  # UnicodeDecodeError too
        try:
            jsonfile.set_aside(path, then="your settings are back to their defaults until you change one")
        except OSError:
            pass
        return {}
    except OSError:
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
                "restrictive value until it's fixed: no runs in Fabric, Preview features, telemetry, "
                "external AI tools, package installs, the Optimize lakehouse and data samples in replay bundles off. "
                "Sign-in still works, and accounts from any tenant can sign in"), False


def _save_copy(saved: Path, text: str) -> None:
    try:
        if saved.exists() and saved.read_text(encoding="utf-8") == text:
            return
        jsonfile.write_text(saved, text)
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
            problems.append(f"{one_line(key, 80)} isn't a setting (settings: {', '.join(POLICY_KEYS)})")
        elif key in INTEGRATION_KEYS:
            continue  # integrations/policy.py checks these, below
        elif key == "fabric_view_sign_in_hosts":
            problems.extend(_sign_in_host_problems(value))
        elif key == "engine_download_url":
            problems.extend(_engine_url_problems(value, data.get("auto_update")))
        elif key == "register_link_scheme":
            if not isinstance(value, bool):
                problems.append(f"register_link_scheme should be bool, not {type(value).__name__}, so the app keeps "
                                "registering kratosforge:// links")
        elif not _typed(key, value):
            kind = TYPES[key]
            name = " or ".join(t.__name__ for t in kind) if isinstance(kind, tuple) else kind.__name__
            problems.append(f"{key} should be {name}, not {type(value).__name__}"
                            + (", so it takes its most restrictive value" if key in RESTRICTED else ""))
        elif key == "channel" and value not in CHANNELS:
            problems.append(f"channel must be one of {', '.join(CHANNELS)}")
        elif key in CHOICES and value not in (*CHOICES[key], *RENAMED.get(key, {})):
            problems.append(f"{key} must be one of {', '.join(CHOICES[key])}")
        elif key == "motion_level" and not MOTION_LEVELS[0] <= value <= MOTION_LEVELS[1]:
            problems.append(f"motion_level must be between {MOTION_LEVELS[0]} and {MOTION_LEVELS[1]}")
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


def dns_name(host: str) -> bool:
    """An exact DNS host name, as the shell's dns_name() in main.rs accepts it: two or more labels of
    letters, digits and inner hyphens, at most 63 each and 253 in all, not ending in a number. No
    wildcard, scheme, port, path, user name or trailing dot; non-ASCII names in their xn-- form."""
    labels = host.split(".")
    return (len(host) <= 253 and len(labels) >= 2 and not labels[-1].isdigit()
            and all(0 < len(l) <= 63 and not l.startswith("-") and not l.endswith("-")
                    and all(c.isascii() and (c.isalnum() or c == "-") for c in l) for l in labels))


def _sign_in_host_problems(value: Any) -> list[str]:
    """What the shell skips in fabric_view_sign_in_hosts (it reads the key itself)."""
    if not isinstance(value, list):
        return [f"fabric_view_sign_in_hosts should be a list of host names, not {type(value).__name__}, "
                "so the Fabric view opens no extra sign-in hosts"]
    bad = [v for v in value if not (isinstance(v, str) and dns_name(v))]
    return [f"fabric_view_sign_in_hosts: {json.dumps(v)} isn't a host name like adfs.contoso.com "
            "(no *, https://, port or path), so it's skipped" for v in bad]


def engine_url_ok(url: str) -> bool:
    """An engine folder as the shell's policy_engine_url() in main.rs accepts it: https, a host name
    with an optional port, and no user name, query, fragment, backslash, space or % escape."""
    if not url.startswith("https://") or not url.isascii() or any(c in url for c in " ?#@\\%"):
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    host, _, port = url[len("https://"):].split("/", 1)[0].partition(":")
    return dns_name(host) and (not _ or (port.isdigit() and len(port) <= 5))


def _engine_url_problems(value: Any, auto_update: Any) -> list[str]:
    """What the shell skips in engine_download_url (it reads the key itself)."""
    if not (isinstance(value, str) and engine_url_ok(value.strip())):
        return [f"engine_download_url: {json.dumps(value)} isn't an https folder address like "
                "https://files.contoso.com/kratosforge (no user name, ? or #), so the engine comes from GitHub"]
    if auto_update is not False:
        return ['engine_download_url is used only with "auto_update": false, so the engine comes from GitHub']
    ignored = os.environ.get("FABRIC_DESKTOP_ENGINE_URL_IGNORED", "")  # the shell's own check, at start
    if ignored:
        return [f"engine_download_url is ignored, so the engine comes from GitHub: {ignored}. Push policy.json "
                "as SYSTEM (Intune, Group Policy) or from an elevated prompt, so only administrators can change it"]
    return []


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
    if key == "motion_level":
        return MOTION_LEVELS[0] <= value <= MOTION_LEVELS[1]
    if key in CHOICES:
        return value in CHOICES[key]
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
        if key in ENV_VARS:
            candidates.append(("environment", os.environ.get(ENV_VARS[key])))
        if key in USER_KEYS:
            candidates.append(("user", user.get(key)))
        if key == "client_id":
            candidates.append(("built_in", built_in_client_id()))
        value, source = default, "default"
        for where, candidate in candidates:
            candidate = RENAMED.get(key, {}).get(candidate, candidate) if isinstance(candidate, str) else candidate
            if candidate not in (None, "") and _typed(key, candidate) and _in_range(key, candidate):
                value, source = candidate, where
                break
        if key == "channel" and value not in CHANNELS:
            value, source = default, "default"
        out[key] = {"value": value, "source": source}
    # The first motion switch turned off means Off: from the organization's policy always (unless it
    # sets motion_level itself), from the person's own choice unless they chose a level since.
    legacy, level = out["forge_animations"], out["motion_level"]
    if legacy["value"] is False and (
            (legacy["source"] == "policy" and level["source"] != "policy")
            or (legacy["source"] == "user" and level["source"] == "default")):
        out["motion_level"] = {"value": 0, "source": legacy["source"]}
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
        if current[key]["source"] == "environment":
            # Saving would say Saved for a value that never applies: the variable wins over this file.
            raise ValueError(f"The {ENV_VARS[key]} environment variable sets {key} on this PC, so a value saved "
                             f"here wouldn't be used. Remove the variable and restart KratosForge to set it here")
        if key == "channel" and new not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
        if key in CHOICES and new not in (None, "", *CHOICES[key]):
            raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
        if key in SQL_RANGES:
            new = _sql_number(key, new)
        if key == "sql_temp_dir":
            new = _sql_folder(new)
        if key == "client_id":
            new = str(new or "").strip()
            if new and not valid_client_id(new):
                raise ValueError("That isn't an app (client) ID. It looks like 00000000-0000-0000-0000-000000000000.")
        if key in ("show_preview", "kernel_process", "telemetry", "forge_animations", "onelake_skip_unchanged", *MOTION_SWITCHES):
            new = bool(new)
        if key == "motion_level" and new is not None and (
                isinstance(new, bool) or not isinstance(new, int) or not MOTION_LEVELS[0] <= new <= MOTION_LEVELS[1]):
            raise ValueError(f"motion_level must be a whole number from {MOTION_LEVELS[0]} to {MOTION_LEVELS[1]}")
        if key in LIMITS:
            new = _limit(key, new)
        if new in (None, ""):
            user.pop(key, None)
        else:
            user[key] = new
    jsonfile.write_json(user_path(), user)  # readers see the old file or the new one, never half (QA-011)
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
