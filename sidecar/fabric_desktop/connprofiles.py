"""Connection profiles: which platforms a piece of work uses, where, and how it signs in (ADR-005, ADR-007).

A connection profile is the one place that says "this work uses Fabric workspace X and Databricks
workspace Y, runs on DuckDB by default, deploys Prod to these targets, and may (or may not) start
remote compute". Kinds: ``local`` (no platform), ``fabric``, ``databricks`` and ``hybrid`` (both).
Only ``name`` and ``kind`` are required; everything else has a default, so a simple profile stays
simple and the advanced parts are there when needed.

**No secrets, ever.** A profile holds *references* to credentials, never the credential:

    fabric       {"method": "app-sign-in"}  (the app's own sign-in, the default)
                 {"method": "service-principal", "tenant_id", "client_id", "secret": "<ref>"}
                 {"method": "managed-identity" | "workload-identity", "client_id"}
    databricks   {"method": "cli-profile", "profile": "DEFAULT"}  (a ~/.databrickscfg profile)
                 {"method": "oauth-u2m"}  (browser sign-in, tokens kept by the Databricks CLI)
                 {"method": "oauth-m2m", "client_id", "secret": "<ref>"}

where ``<ref>`` is ``env:NAME`` (an environment variable) or ``credman:TARGET`` (Windows Credential
Manager). Load, save and import refuse any value that looks like a secret (redact.secrets, which
knows Databricks personal access tokens) and any secret-named field that isn't a reference. Export
drops machine-specific references so a shared file never points at someone else's secret.

A hybrid profile keeps each platform's sign-in, workspace and deploy target separate: it needs one
Fabric and one Databricks connection, and they may not share a secret reference.

Validation here is offline (structure, references, mappings). Checking a sign-in or a workspace
against the platform is a later phase and sets ``validation`` when it runs.
"""

from __future__ import annotations

import configparser
import copy
import datetime as dt
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import redact
from .jsonfile import read_json, write_json

SCHEMA = 1
KINDS = {"local": "This PC only", "fabric": "Microsoft Fabric", "databricks": "Azure Databricks",
         "hybrid": "Fabric and Databricks"}
PLATFORMS = ("fabric", "databricks")
ENVIRONMENTS = ("dev", "test", "staging", "prod")
METHODS = {
    "fabric": ("app-sign-in", "service-principal", "managed-identity", "workload-identity"),
    "databricks": ("cli-profile", "oauth-u2m", "oauth-m2m"),
}
NEEDS_SECRET = {"service-principal", "oauth-m2m"}
REMOTE_POLICIES = ("ask", "policy", "never")
COPY_POLICIES = ("ask", "never")
VALIDATION = ("not_checked", "ok", "warnings", "failed")
# Where each Databricks cloud's workspaces live. Only Azure is turned on; the others are ready to add.
DATABRICKS_CLOUDS = {"azure": (".azuredatabricks.net", True), "aws": (".cloud.databricks.com", False),
                     "gcp": (".gcp.databricks.com", False)}
_REF = re.compile(r"^(env:[A-Za-z_][A-Za-z0-9_]{0,127}|credman:[A-Za-z0-9 ._:/\\-]{1,200})$")
_MACHINE_REF = ("env:", "credman:")
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
    return base | conn


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with every optional part filled in with its default. Doesn't validate."""
    p = copy.deepcopy(data)
    p.setdefault("schema", SCHEMA)
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


def problems(data: dict[str, Any]) -> list[str]:
    """Every reason ``data`` (normalized or not) isn't a valid profile; empty when it is."""
    out = secret_problems(data)
    p = normalize(data)
    if p.get("schema") != SCHEMA:
        out.append(f"Profile schema {p.get('schema')} is newer than this app knows ({SCHEMA}). Update KratosForge.")
    if not isinstance(p.get("name"), str) or not _NAME.fullmatch(p["name"].strip() or "\x00"):
        out.append("A profile needs a name (up to 80 characters).")
    if not _ID.fullmatch(str(p.get("id"))):
        out.append("The profile id may use letters, digits, - and _ only.")
    kind = p.get("kind")
    if kind not in KINDS:
        out.append(f"kind must be one of: {', '.join(KINDS)}.")
    ids: list[str] = []
    by_platform: dict[str, list[dict[str, Any]]] = {"fabric": [], "databricks": []}
    for i, conn in enumerate(p["connections"]):
        where = f"connections[{i}]"
        platform = conn.get("platform")
        if platform not in PLATFORMS:
            out.append(f"{where}.platform must be fabric or databricks.")
            continue
        by_platform[platform].append(conn)
        if not _ID.fullmatch(str(conn.get("id"))):
            out.append(f"{where}.id may use letters, digits, - and _ only.")
        ids.append(conn["id"])
        cred = conn.get("credential") or {}
        method = cred.get("method")
        if method not in METHODS[platform]:
            out.append(f"{where}.credential.method must be one of: {', '.join(METHODS[platform])}.")
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
        out.append("A local profile has no platform connections; make it Fabric, Databricks or hybrid instead.")
    for platform in PLATFORMS:
        if kind in (platform, "hybrid") and not by_platform[platform]:
            out.append(f"A {KINDS[kind]} profile needs a {KINDS[platform]} connection.")
        if kind not in (platform, "hybrid") and by_platform[platform] and kind in KINDS and kind != "local":
            out.append(f"A {KINDS[kind]} profile can't have a {KINDS[platform]} connection; make it hybrid.")
    if kind == "hybrid":
        fabric_refs = {c["credential"].get("secret") for c in by_platform["fabric"]} - {None, ""}
        dbx_refs = {c["credential"].get("secret") for c in by_platform["databricks"]} - {None, ""}
        if fabric_refs & dbx_refs:
            out.append("Fabric and Databricks connections must use different credentials, not the same secret reference.")
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
    profile name (each person has their own) and the last validation. Safe to share or commit."""
    p = validate(profile)
    for conn in p["connections"]:
        cred = conn["credential"]
        for key in ("secret", "profile"):
            if isinstance(cred.get(key), str) and (key == "profile" or cred[key].startswith(_MACHINE_REF)):
                cred[key] = ""
                cred["needs_setup"] = True
    p["validation"] = {"status": "not_checked", "checked_at": None, "problems": []}
    p.pop("id")
    return {"kind": "kratosforge.connection-profile", "schema": SCHEMA, "profile": p}


def import_(document: dict[str, Any], existing_ids: set[str] | None = None) -> dict[str, Any]:
    """A profile from an exported document, with a new id. Credentials marked ``needs_setup`` are
    accepted with their method only and listed in validation, so the person adds their own."""
    if not isinstance(document, dict) or document.get("kind") != "kratosforge.connection-profile":
        raise ProfileError(["This file isn't a KratosForge connection profile."])
    found = secret_problems(document)
    if found:
        raise ProfileError(found)
    p = copy.deepcopy(document.get("profile") or {})
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
