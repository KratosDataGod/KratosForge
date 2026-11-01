"""Connection profiles: which platforms a piece of work uses, where, and how it signs in (ADR-005, ADR-007).

A connection profile is the one place that says "this work uses Fabric workspace X and Databricks
workspace Y, runs on DuckDB by default, deploys Prod to these targets, and may (or may not) start
remote compute". Kinds: ``local`` (no platform), ``fabric``, ``databricks``, ``snowflake`` and
``hybrid`` (two or more platforms).
Only ``name`` and ``kind`` are required; everything else has a default, so a simple profile stays
simple and the advanced parts are there when needed.

**No secrets, ever.** A profile holds *references* to credentials, never the credential:

    fabric       {"method": "app-sign-in"}  (the app's own sign-in, the default)
                 {"method": "service-principal", "tenant_id", "client_id", "secret": "<ref>"}
                 {"method": "managed-identity" | "workload-identity", "client_id"}
    databricks   {"method": "cli-profile", "profile": "DEFAULT"}  (a ~/.databrickscfg profile)
                 {"method": "oauth-u2m"}  (browser sign-in, tokens kept by the Databricks CLI)
                 {"method": "oauth-m2m", "client_id", "secret": "<ref>"}
    snowflake    {"method": "oauth-local"}  (browser sign-in through Snowflake's built-in local application)
                 {"method": "keypair", "private_key_file": "C:\\keys\\sf.p8", "passphrase": "<ref>"}
                 {"method": "pat", "secret": "<ref>"}  (a programmatic access token)
                 {"method": "cli-connection", "connection": "dev"}  (a Snowflake CLI connection)

where ``<ref>`` is ``env:NAME`` (an environment variable) or ``credman:TARGET`` (Windows Credential
Manager). Load, save and import refuse any value that looks like a secret (redact.secrets, which
knows Databricks personal access tokens) and any secret-named field that isn't a reference. Export
drops machine-specific references so a shared file never points at someone else's secret.

A hybrid profile keeps each platform's sign-in, workspace and deploy target separate: it needs
connections to two or more platforms, and no two platforms may share a secret reference.

Schema 2 added Snowflake and hybrids other than Fabric + Databricks. A profile is always written with
the lowest schema that holds it, so one that uses only Fabric and Databricks stays schema 1 and still
opens in an app that knows only schema 1.

Validation here is offline (structure, references, mappings). Checking a sign-in or a workspace
against the platform is a later phase and sets ``validation`` when it runs.
"""

from __future__ import annotations

import configparser
import copy
import datetime as dt
import os
import re
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import redact
from .jsonfile import read_json, write_json

SCHEMA = 2
SUPPORTED_SCHEMAS = (1, 2)
KINDS = {"local": "This PC only", "fabric": "Microsoft Fabric", "databricks": "Azure Databricks",
         "snowflake": "Snowflake", "hybrid": "Two or more platforms"}
PLATFORMS = ("fabric", "databricks", "snowflake")
# What schema 1 knew; a profile that fits in it is written as schema 1.
_SCHEMA1_KINDS = ("local", "fabric", "databricks", "hybrid")
_SCHEMA1_PLATFORMS = ("fabric", "databricks")
ENVIRONMENTS = ("dev", "test", "staging", "prod")
METHODS = {
    "fabric": ("app-sign-in", "service-principal", "managed-identity", "workload-identity"),
    "databricks": ("cli-profile", "oauth-u2m", "oauth-m2m"),
    "snowflake": ("oauth-local", "keypair", "pat", "cli-connection"),
}
NEEDS_SECRET = {"service-principal", "oauth-m2m"}
REMOTE_POLICIES = ("ask", "policy", "never")
COPY_POLICIES = ("ask", "never")
VALIDATION = ("not_checked", "ok", "warnings", "failed")
# Where each Databricks cloud's workspaces live. Only Azure is turned on; the others are ready to add.
DATABRICKS_CLOUDS = {"azure": (".azuredatabricks.net", True), "aws": (".cloud.databricks.com", False),
                     "gcp": (".gcp.databricks.com", False)}
# An account identifier: <org>-<account>, or a legacy locator with an optional .region[.cloud].
# Never a URL: the address is always https://<account>.snowflakecomputing.com.
_SF_ACCOUNT = re.compile(r"^[A-Za-z0-9_]{1,100}(-[A-Za-z0-9_]{1,154}|(\.[A-Za-z0-9-]{1,40}){0,2})$")
_SF_NAME = re.compile(r"^[^\x00-\x1f]{1,255}$")
_REF = re.compile(r"^(env:[A-Za-z_][A-Za-z0-9_]{0,127}|credman:[A-Za-z0-9 ._:/\\-]{1,200})$")
_NAME = re.compile(r"^[^\x00-\x1f]{1,80}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ProfileError(ValueError):
    """The profile can't be used as it is; ``problems`` says every reason."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ---------------------------------------------------------------- secrets


def _looks_secret(text: str) -> bool:
    return redact.secrets(text) != text


def secret_problems(data: Any, path: str = "") -> list[str]:
    """Every place in ``data`` that holds a secret instead of a reference. Values are never repeated."""
    problems: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            where = f"{path}.{key}" if path else str(key)
            if redact.secret_key(key) and value not in (None, "", [], {}):
                if not (isinstance(value, str) and _REF.fullmatch(value)):
                    problems.append(f"{where} must be a reference (env:NAME or credman:TARGET), not a value.")
                    continue
            problems += secret_problems(value, where)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            problems += secret_problems(value, f"{path}[{i}]")
    elif isinstance(data, str) and _looks_secret(data):
        problems.append(f"{path or 'value'} looks like a token, key or password. Profiles keep references only.")
    return problems


# ---------------------------------------------------------------- shape


def _connection_defaults(conn: dict[str, Any]) -> dict[str, Any]:
    platform = conn.get("platform")
    base: dict[str, Any] = {"id": platform or "", "platform": platform, "label": ""}
    if platform == "fabric":
        base |= {"credential": {"method": "app-sign-in"}, "tenant_id": "", "workspace": {"id": "", "name": ""}}
    elif platform == "databricks":
        base |= {"cloud": "azure", "host": "", "credential": {"method": "oauth-u2m"},
                 "catalog": "", "schema": "", "compute": {"cluster_id": "", "warehouse_id": "", "serverless": False}}
    elif platform == "snowflake":
        base |= {"account": "", "user": "", "role": "", "warehouse": "", "database": "", "schema": "",
                 "credential": {"method": "oauth-local"}}
    return base | conn


def needed_schema(data: dict[str, Any]) -> int:
    """The lowest schema that holds ``data``: 1 unless it uses Snowflake or a hybrid that isn't
    exactly Fabric + Databricks."""
    platforms = {c.get("platform") for c in data.get("connections") or []}
    if data.get("kind") not in _SCHEMA1_KINDS or not platforms <= set(_SCHEMA1_PLATFORMS):
        return 2
    return 1


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with every optional part filled in with its default. Doesn't validate."""
    p = copy.deepcopy(data)
    if p.get("schema") in SUPPORTED_SCHEMAS or "schema" not in p:
        p["schema"] = needed_schema(p)
    p.setdefault("id", "p-" + uuid.uuid4().hex[:12])
    p.setdefault("description", "")
    p["connections"] = [_connection_defaults(c) for c in p.get("connections") or []]
    p["defaults"] = {"engine": "local-duckdb", "connection": "", "catalog": "", "schema": "", "namespace": ""} \
        | (p.get("defaults") or {})
    p["local"] = {"prefer_local": True, "max_memory_gb": None, "cache": {"enabled": True, "max_gb": 10}} \
        | (p.get("local") or {})
    p["environments"] = {env: dict((p.get("environments") or {}).get(env) or {}) for env in ENVIRONMENTS} \
        | {k: v for k, v in (p.get("environments") or {}).items() if k not in ENVIRONMENTS}
    p["runtime"] = {"python": "", "packages": [], "databricks_runtime": ""} | (p.get("runtime") or {})
    p["deploy"] = dict(p.get("deploy") or {})
    p["execution"] = {"remote_compute": "ask", "allowed_remote_engines": [],
                      "budget": {"remote_minutes_per_day": None}} | (p.get("execution") or {})
    p["data_access"] = {"cross_platform_copy": "ask"} | (p.get("data_access") or {})
    p["validation"] = {"status": "not_checked", "checked_at": None, "problems": []} | (p.get("validation") or {})
    return p


def _host_problems(conn: dict[str, Any], where: str) -> list[str]:
    cloud = conn.get("cloud")
    if cloud not in DATABRICKS_CLOUDS:
        return [f"{where}.cloud must be one of: {', '.join(DATABRICKS_CLOUDS)}."]
    suffix, enabled = DATABRICKS_CLOUDS[cloud]
    if not enabled:
        return [f"{where}: Databricks on {cloud.upper()} isn't supported yet; Azure Databricks is."]
    host = conn.get("host") or ""
    if not host:
        return []  # chosen later, from discovery
    url = urlparse(host)
    if url.scheme != "https" or not (url.hostname or "").endswith(suffix) or url.path not in ("", "/") \
            or url.username or url.password or url.query:
        return [f"{where}.host must be the workspace address, like https://adb-1234567890123456.7{suffix}"]
    return []


def _secret_ref(cred: dict[str, Any]) -> str | None:
    return cred.get("secret") or cred.get("passphrase")


def key_file_problem(path: Any, windows: bool | None = None) -> str:
    """Why ``path`` can't be a private key file, or "". On Windows only a drive-letter path: a UNC or
    \\\\?\\ path would make reading the key reach out to a share."""
    windows = os.name == "nt" if windows is None else windows
    if not isinstance(path, str) or not path.strip():
        return "name the private key file"
    if path.startswith(("\\\\", "//")):
        return "the private key file must be on this PC, not a network share"
    if windows:
        ok = re.fullmatch(r"[A-Za-z]:[\\/][^\x00-\x1f<>\"|?*]+", path) is not None
    else:
        ok = path.startswith("/") and "\x00" not in path
    if ok:
        return ""
    return "the private key file must be a full path" + (", like C:\\keys\\snowflake.p8" if windows else "")


# The credential fields each Snowflake sign-in method uses; anything else is refused.
_SF_CRED_FIELDS: dict[str, tuple[str, ...]] = {
    "oauth-local": (), "keypair": ("private_key_file", "passphrase"), "pat": ("secret",),
    "cli-connection": ("connection",)}


def _snowflake_problems(conn: dict[str, Any], where: str) -> list[str]:
    out = []
    account = conn.get("account") or ""
    if account and not _SF_ACCOUNT.fullmatch(account):
        out.append(f"{where}.account must be the account identifier, like myorg-myaccount (not a URL).")
    for field in ("user", "role", "warehouse", "database", "schema"):
        value = conn.get(field) or ""
        if value and not (isinstance(value, str) and _SF_NAME.fullmatch(value)):
            out.append(f"{where}.{field} must be a name (up to 255 characters).")
    cred = conn.get("credential") or {}
    method = cred.get("method")
    extra = sorted(str(k) for k in cred if k not in _SF_CRED_FIELDS.get(method, ("method",)) + ("method", "needs_setup"))
    if extra:  # names only: a stray field may hold a key or passphrase, so its value is never repeated
        out.append(f"{where}.credential can't hold {', '.join(extra)} for this sign-in method.")
    if method == "keypair":
        why = key_file_problem(cred.get("private_key_file"))
        if why:
            out.append(f"{where}: {why}.")
    elif method == "pat" and not cred.get("secret"):
        out.append(f"{where}: a programmatic access token needs a secret reference (env:NAME or credman:TARGET).")
    elif method == "cli-connection" and not cred.get("connection"):
        out.append(f"{where}: name the Snowflake CLI connection to use.")
    return out


def snowflake_host(account: str) -> str:
    """The address for a Snowflake account identifier; a profile never holds one of its own."""
    if not _SF_ACCOUNT.fullmatch(account or ""):
        raise ValueError("Not a Snowflake account identifier.")
    # Snowflake's URLs take a hyphen where an account name has an underscore.
    return f"https://{account.lower().replace('_', '-')}.snowflakecomputing.com"


_SECTIONS = ("defaults", "local", "environments", "runtime", "deploy", "execution", "data_access", "validation")
_TEXT_FIELDS = ("id", "platform", "label", "host", "cloud", "account", "tenant_id")


def _shape_problems(data: Any) -> list[str]:
    """Where ``data`` isn't shaped like a profile at all (a list where an object goes, a number for
    an address): a hand-edited or imported file says so instead of failing the check part way."""
    if not isinstance(data, dict):
        return ["A profile is a JSON object."]
    out = [f"{key} must be text." for key in ("id", "name", "kind", "description")
           if data.get(key) is not None and not isinstance(data[key], str)]
    conns = data.get("connections")
    if conns is not None and not isinstance(conns, list):
        out.append("connections must be a list.")
        conns = []
    for i, conn in enumerate(conns or []):
        where = f"connections[{i}]"
        if not isinstance(conn, dict):
            out.append(f"{where} must be an object.")
            continue
        for key in ("credential", "compute", "workspace"):
            if conn.get(key) is not None and not isinstance(conn[key], dict):
                out.append(f"{where}.{key} must be an object.")
        for key in _TEXT_FIELDS:
            if conn.get(key) is not None and not isinstance(conn[key], str):
                out.append(f"{where}.{key} must be text.")
    for key in _SECTIONS:
        if data.get(key) is not None and not isinstance(data[key], dict):
            out.append(f"{key} must be an object.")
    for key in ("environments", "deploy"):
        section = data.get(key)
        for env, mapping in (section.items() if isinstance(section, dict) else ()):
            if mapping is not None and not isinstance(mapping, dict):
                # Only a known environment is named: any other key is the file's own text.
                out.append(f"{key}.{env} must be an object." if env in ENVIRONMENTS
                           else f"{key} has an entry that isn't an object.")
    execution = data.get("execution")
    if isinstance(execution, dict) and execution.get("budget") is not None and not isinstance(execution["budget"], dict):
        out.append("execution.budget must be an object.")
    return out


def problems(data: dict[str, Any]) -> list[str]:
    """Every reason ``data`` (normalized or not) isn't a valid profile; empty when it is."""
    out = secret_problems(data)
    shape = _shape_problems(data)
    if shape:
        return out + shape
    p = normalize(data)
    if p.get("schema") not in SUPPORTED_SCHEMAS:
        out.append(f"Profile schema {p.get('schema')} is newer than this app knows ({SCHEMA}). Update KratosForge.")
    if not isinstance(p.get("name"), str) or not _NAME.fullmatch(p["name"].strip() or "\x00"):
        out.append("A profile needs a name (up to 80 characters).")
    if not _ID.fullmatch(str(p.get("id"))):
        out.append("The profile id may use letters, digits, - and _ only.")
    kind = p.get("kind")
    if kind not in KINDS:
        out.append(f"kind must be one of: {', '.join(KINDS)}.")
    ids: list[str] = []
    by_platform: dict[str, list[dict[str, Any]]] = {platform: [] for platform in PLATFORMS}
    for i, conn in enumerate(p["connections"]):
        where = f"connections[{i}]"
        platform = conn.get("platform")
        if platform not in PLATFORMS:
            out.append(f"{where}.platform must be one of: {', '.join(PLATFORMS)}.")
            continue
        by_platform[platform].append(conn)
        if not _ID.fullmatch(str(conn.get("id"))):
            out.append(f"{where}.id may use letters, digits, - and _ only.")
        ids.append(conn["id"])
        cred = conn.get("credential") or {}
        method = cred.get("method")
        if method not in METHODS[platform]:
            out.append(f"{where}.credential.method must be one of: {', '.join(METHODS[platform])}.")
        elif platform == "snowflake":
            out += _snowflake_problems(conn, where)
        elif method in NEEDS_SECRET and not (cred.get("client_id") and cred.get("secret")):
            out.append(f"{where}: {method} needs client_id and a secret reference (env:NAME or credman:TARGET).")
        elif method == "cli-profile" and not cred.get("profile"):
            out.append(f"{where}: name the Databricks CLI profile to use.")
        if platform == "databricks":
            out += _host_problems(conn, where)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        out.append(f"Connection ids must be different: {', '.join(dupes)}.")
    if kind == "local" and p["connections"]:
        out.append("A local profile has no platform connections; make it Fabric, Databricks, Snowflake or hybrid instead.")
    for platform in PLATFORMS:
        if kind == platform and not by_platform[platform]:
            out.append(f"A {KINDS[kind]} profile needs a {KINDS[platform]} connection.")
        if kind not in (platform, "hybrid") and by_platform[platform] and kind in KINDS and kind != "local":
            out.append(f"A {KINDS[kind]} profile can't have a {KINDS[platform]} connection; make it hybrid.")
    used = [platform for platform in PLATFORMS if by_platform[platform]]
    if kind == "hybrid" and len(used) < 2:
        has = ", ".join(KINDS[u] for u in used) or "none"
        out.append(f"A hybrid profile needs connections to two or more platforms (it has: {has}).")
    if kind == "hybrid":
        refs = {platform: {_secret_ref(c["credential"]) for c in by_platform[platform]} - {None, ""}
                for platform in used}
        for i, a in enumerate(used):
            for b in used[i + 1:]:
                if refs[a] & refs[b]:
                    out.append(f"{KINDS[a]} and {KINDS[b]} connections must use different credentials, "
                               "not the same secret reference.")
    d = p["defaults"]
    if d.get("connection") and d["connection"] not in ids:
        out.append(f"defaults.connection names {d['connection']!r}, which isn't one of this profile's connections.")
    for env, mapping in p["environments"].items():
        if env not in ENVIRONMENTS:
            out.append(f"environments.{env}: environments are {', '.join(ENVIRONMENTS)}.")
        for cid in mapping or {}:
            if cid not in ids:
                out.append(f"environments.{env} maps {cid!r}, which isn't one of this profile's connections.")
    for env, mapping in p["deploy"].items():
        if env not in ENVIRONMENTS:
            out.append(f"deploy.{env}: environments are {', '.join(ENVIRONMENTS)}.")
        for cid in mapping or {}:
            if cid not in ids:
                out.append(f"deploy.{env} targets {cid!r}, which isn't one of this profile's connections.")
    ex = p["execution"]
    if ex.get("remote_compute") not in REMOTE_POLICIES:
        out.append(f"execution.remote_compute must be one of: {', '.join(REMOTE_POLICIES)}.")
    if ex.get("remote_compute") == "never" and ex.get("allowed_remote_engines"):
        out.append("execution: remote compute is 'never', so no remote engines can be allowed.")
    budget = (ex.get("budget") or {}).get("remote_minutes_per_day")
    if budget is not None and (not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget < 0):
        out.append("execution.budget.remote_minutes_per_day must be a number of minutes, or empty.")
    if p["data_access"].get("cross_platform_copy") not in COPY_POLICIES:
        out.append("data_access.cross_platform_copy must be ask or never: data never moves between platforms on its own.")
    if p["validation"].get("status") not in VALIDATION:
        out.append(f"validation.status must be one of: {', '.join(VALIDATION)}.")
    return out


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """The normalized profile, or ProfileError listing every problem."""
    found = problems(data)
    if found:
        raise ProfileError(found)
    return normalize(data)


def default_profile() -> dict[str, Any]:
    """What the app does today, as a profile: this PC plus Fabric with the app's own sign-in."""
    return validate({"id": "default", "name": "This PC and Fabric", "kind": "fabric",
                     "description": "The app's own sign-in and the Dev, Test and Prod environments set up in Data mesh.",
                     "connections": [{"platform": "fabric"}]})


# ---------------------------------------------------------------- export and import


def export(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile without anything tied to this PC: machine-specific secret references, the CLI
    profile or connection name and key file (each person has their own) and the last validation.
    Safe to share or commit. Written with the lowest schema that holds it."""
    p = validate(profile)
    for conn in p["connections"]:
        cred = conn["credential"]
        for key in ("secret", "passphrase", "profile", "connection", "private_key_file"):
            value = cred.get(key)
            # Every one is blanked: a reference, name or path is this PC's, and a value must never travel.
            if isinstance(value, str) and value:
                cred[key] = ""
                cred["needs_setup"] = True
    p["validation"] = {"status": "not_checked", "checked_at": None, "problems": []}
    p.pop("id")
    return {"kind": "kratosforge.connection-profile", "schema": p["schema"], "profile": p}


def import_(document: dict[str, Any], existing_ids: set[str] | None = None) -> dict[str, Any]:
    """A profile from an exported document, with a new id. Credentials marked ``needs_setup`` are
    accepted with their method only and listed in validation, so the person adds their own."""
    if not isinstance(document, dict) or document.get("kind") != "kratosforge.connection-profile":
        raise ProfileError(["This file isn't a KratosForge connection profile."])
    found = secret_problems(document)
    if found:
        raise ProfileError(found)
    p = copy.deepcopy(document.get("profile") or {})
    shape = _shape_problems(p)
    if shape:
        raise ProfileError(shape)
    p["id"] = "p-" + uuid.uuid4().hex[:12]
    while existing_ids and p["id"] in existing_ids:
        p["id"] = "p-" + uuid.uuid4().hex[:12]
    todo = []
    for conn in p.get("connections") or []:
        cred = conn.get("credential") or {}
        if cred.pop("needs_setup", False):
            todo.append(f"{conn.get('id') or conn.get('platform')}: add your own sign-in")
            if cred.get("method") in NEEDS_SECRET and not cred.get("secret"):
                cred["secret"] = "env:SET_ME"
            if cred.get("method") == "cli-profile" and not cred.get("profile"):
                cred["profile"] = "DEFAULT"
            if cred.get("method") == "pat" and not cred.get("secret"):
                cred["secret"] = "env:SET_ME"
            if cred.get("method") == "cli-connection" and not cred.get("connection"):
                cred["connection"] = "default"
            if cred.get("method") == "keypair" and not cred.get("private_key_file"):
                cred["private_key_file"] = str(Path.home() / ".snowflake" / "SET_ME.p8")
    p["validation"] = {"status": "warnings" if todo else "not_checked", "checked_at": None, "problems": todo}
    return validate(p)


# ---------------------------------------------------------------- switching


def switch_plan(current: dict[str, Any] | None, new: dict[str, Any], sessions: list[dict[str, Any]],
                unsaved: list[str]) -> dict[str, Any]:
    """What switching from ``current`` to ``new`` must do first. Nothing is done here.

    Every open engine session closes: sessions may hold data read with the old profile's sign-in, and
    remote ones keep its compute running. Unsaved notebooks block the switch until they are saved or
    discarded, so no work is lost silently."""
    same = current is not None and current.get("id") == new.get("id")
    close = [] if same else [s for s in sessions]
    blockers = [] if same else [f"{name} has unsaved changes. Save or discard them first." for name in unsaved]
    message = "" if same else (
        f"Switching to {new.get('name')} closes {len(close)} open session{'s' if len(close) != 1 else ''}; "
        "cells and outputs stay. Variables don't carry over." if close else f"Switching to {new.get('name')}.")
    return {"same": same, "close_sessions": close, "blockers": blockers, "can_switch": not blockers, "message": message}


# ---------------------------------------------------------------- Databricks CLI profiles


def databricks_config_path() -> Path:
    return Path(os.environ.get("DATABRICKS_CONFIG_FILE") or Path.home() / ".databrickscfg")


def databricks_cli_profiles(path: Path | None = None) -> dict[str, Any]:
    """The profiles in the Databricks CLI's config file: name, host and how each signs in.

    Read-only, offline. Secret fields (token, client_secret, password) are never returned; a profile
    that keeps a personal access token in the file gets a warning, since OAuth (``databricks auth login``)
    keeps tokens out of plain text."""
    path = path or databricks_config_path()
    if not path.is_file():
        return {"path": str(path), "found": False, "profiles": []}
    parser = configparser.RawConfigParser(default_section="__no_defaults__")
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as exc:
        return {"path": str(path), "found": True, "profiles": [],
                "error": f"The file can't be read: {redact.secrets(type(exc).__name__)}."}
    profiles = []
    for name in parser.sections():
        section = parser[name]
        auth_type = section.get("auth_type", "")
        has_pat = bool(section.get("token"))
        if not auth_type:
            auth_type = "pat" if has_pat else ("oauth-m2m" if section.get("client_id") and section.get("client_secret")
                                              else ("databricks-cli" if section.get("host") else ""))
        host = section.get("host", "")
        profiles.append({
            "name": name, "host": host if not _looks_secret(host) else "", "auth_type": auth_type,
            "cluster_id": section.get("cluster_id", ""), "warehouse_id": section.get("warehouse_id", ""),
            "serverless": section.get("serverless_compute_id", "") == "auto",
            "warning": ("Keeps a personal access token in plain text in this file. Sign in with "
                        "'databricks auth login' instead." if has_pat else ""),
        })
    return {"path": str(path), "found": True, "profiles": profiles}


def connection_from_cli_profile(entry: dict[str, Any]) -> dict[str, Any]:
    """A Databricks connection that uses a CLI profile as it is (no secret is copied)."""
    host = entry.get("host") or ""
    cloud = next((c for c, (suffix, _) in DATABRICKS_CLOUDS.items() if (urlparse(host).hostname or "").endswith(suffix)),
                 "azure")
    return _connection_defaults({
        "id": re.sub(r"[^A-Za-z0-9_-]", "-", entry["name"])[:40] or "databricks", "platform": "databricks",
        "cloud": cloud, "host": host.rstrip("/"), "credential": {"method": "cli-profile", "profile": entry["name"]},
        "compute": {"cluster_id": entry.get("cluster_id", ""), "warehouse_id": entry.get("warehouse_id", ""),
                    "serverless": bool(entry.get("serverless"))}})


# ---------------------------------------------------------------- Snowflake CLI connections

# The connection fields kept from the Snowflake CLI's files; everything else is dropped unread.
_SF_KEEP = ("account", "user", "role", "warehouse", "database", "schema", "authenticator", "private_key_file")
# Fields that hold a secret: their presence earns a warning; their values are never kept.
_SF_SECRET_FIELDS = ("password", "token", "private_key_raw", "private_key_passphrase", "oauth_client_secret")
# Snowflake CLI authenticator -> the profile's method, for the ones KratosForge can sign in with.
_SF_AUTHENTICATORS = {"snowflake_jwt": "keypair", "programmatic_access_token": "pat",
                      "oauth_authorization_code": "oauth-local"}


def snowflake_config_dir(windows: bool | None = None) -> Path | None:
    """Where the Snowflake CLI keeps config.toml and connections.toml, by the CLI's own rules:
    SNOWFLAKE_HOME if it is set (and only if that folder exists: the CLI doesn't fall back), else
    ~/.snowflake if it exists, else the system default (%USERPROFILE%\\AppData\\Local\\snowflake on
    Windows, ~/.config/snowflake elsewhere). None when SNOWFLAKE_HOME names a missing folder."""
    windows = os.name == "nt" if windows is None else windows
    home_env = os.environ.get("SNOWFLAKE_HOME")
    if home_env:
        folder = Path(home_env).expanduser()
        return folder if folder.is_dir() else None
    dot = Path.home() / ".snowflake"
    if dot.is_dir():
        return dot
    return Path.home() / "AppData" / "Local" / "snowflake" if windows else Path.home() / ".config" / "snowflake"


def _read_toml(path: Path) -> dict[str, Any] | None:
    """The parsed file, or None when it can't be read. No exception text is kept: a parse error
    quotes the offending line, which may hold a secret."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None


def snowflake_cli_connections(folder: Path | None = None) -> dict[str, Any]:
    """The Snowflake CLI's connections: name, account, user, role, warehouse, database, schema and how
    each signs in. Read-only and offline.

    tomllib reads a file whole, so each connection is cut down to the fields in _SF_KEEP at once and
    the parsed data dropped. Passwords, tokens, raw keys and passphrases are never returned, logged
    or kept; a connection that keeps one in the file gets a warning."""
    folder = snowflake_config_dir() if folder is None else folder
    if folder is None:
        # The CLI finds nothing then and doesn't fall back, so neither does this.
        return {"path": os.environ.get("SNOWFLAKE_HOME", ""), "found": False, "default": None, "connections": []}
    config_path, connections_path = folder / "config.toml", folder / "connections.toml"
    result: dict[str, Any] = {"path": str(folder), "found": False, "default": None, "connections": []}
    raw: dict[str, Any] = {}
    if config_path.is_file():
        result["found"] = True
        config = _read_toml(config_path)
        if config is None:
            return result | {"error": f"Couldn't read {config_path}."}
        name = config.get("default_connection_name")
        result["default"] = name if isinstance(name, str) else None
        raw = config.get("connections") if isinstance(config.get("connections"), dict) else {}
        del config
    if connections_path.is_file():  # it wins over config.toml's [connections]
        result["found"] = True
        parsed = _read_toml(connections_path)
        if parsed is None:
            return result | {"error": f"Couldn't read {connections_path}."}
        raw = parsed
        del parsed
    if os.name != "nt":  # the CLI's own check is POSIX-only; Windows ACLs aren't mode bits
        for path in (config_path, connections_path):
            try:
                writable = path.is_file() and path.stat().st_mode & 0o022
            except OSError:  # gone since it was read
                writable = False
            if writable:
                result.setdefault("notes", []).append(f"{path} can be changed by other users of this computer "
                                                      "(chmod 0600 it).")
    for name, section in raw.items():
        if not isinstance(section, dict):
            continue
        kept = {k: section[k] for k in _SF_KEEP if isinstance(section.get(k), str) and not _looks_secret(section[k])}
        holds_secret = any(section.get(k) for k in _SF_SECRET_FIELDS)
        authenticator = (kept.get("authenticator") or "snowflake").lower()
        method = _SF_AUTHENTICATORS.get(authenticator)
        warnings = []
        if holds_secret:
            warnings.append("Keeps a password, token or key in plain text in this file. Use a key file, a token "
                            "reference or browser sign-in instead.")
        if authenticator == "externalbrowser":
            warnings.append("Uses browser single sign-on, which KratosForge doesn't support yet.")
        elif method is None:
            warnings.append(f"Signs in with {authenticator}, which KratosForge doesn't use. "
                            "Key pair, programmatic access tokens and OAuth sign-in work.")
        result["connections"].append({"name": str(name), **{k: kept.get(k, "") for k in _SF_KEEP},
                                      "method": method or "", "usable": method is not None, "warnings": warnings})
    return result


def connection_from_cli_connection(entry: dict[str, Any]) -> dict[str, Any]:
    """A Snowflake connection that uses a Snowflake CLI connection by name (no secret is copied)."""
    return _connection_defaults({
        "id": re.sub(r"[^A-Za-z0-9_-]", "-", entry["name"])[:40] or "snowflake", "platform": "snowflake",
        **{k: entry.get(k, "") for k in ("account", "user", "role", "warehouse", "database", "schema")},
        "credential": {"method": "cli-connection", "connection": entry["name"]}})


# ---------------------------------------------------------------- storage


class ProfileStore:
    """Profiles on this PC, one JSON file each, plus which one is active."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, profile_id: str) -> Path:
        if not _ID.fullmatch(profile_id):
            raise ProfileError(["The profile id may use letters, digits, - and _ only."])
        return self.root / f"{profile_id}.json"

    def list(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self.root.glob("*.json")):
            if path.name == "active.json":
                continue
            data = read_json(path, None)
            if isinstance(data, dict):
                out.append({"id": data.get("id"), "name": data.get("name"), "kind": data.get("kind"),
                            "status": (data.get("validation") or {}).get("status", "not_checked"),
                            "problems": len(problems(data))})
        return out

    def load(self, profile_id: str) -> dict[str, Any]:
        if profile_id == "default" and not self._path("default").exists():
            return default_profile()
        data = read_json(self._path(profile_id), None)
        if not isinstance(data, dict):
            raise ProfileError([f"There's no profile {profile_id!r}."])
        return validate(data)

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        profile = validate(data)
        write_json(self._path(profile["id"]), profile)
        return profile

    def delete(self, profile_id: str) -> None:
        if self.active() == profile_id:
            raise ProfileError(["Switch to another profile before deleting this one."])
        self._path(profile_id).unlink(missing_ok=True)

    def active(self) -> str:
        return (read_json(self.root / "active.json", {}) or {}).get("id") or "default"

    def set_active(self, profile_id: str) -> None:
        self.load(profile_id)
        write_json(self.root / "active.json", {"id": profile_id,
                                               "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
