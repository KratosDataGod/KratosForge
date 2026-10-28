"""Policy packages: a project's policies, taken from the platform once, so Check now can run without it (GOV-002).

A package is one JSON file holding what resolving a project's policies needs, and nothing else:

    project, taken_at, platform   which project, when, and from which platform (a SQLite file on this PC,
                                  or UDF_Platform in a Fabric workspace, by its ids)
    chain                         the scopes the project inherits from: enterprise, its domain and
                                  subdomain, the project
    resolved                      the policies in force when it was taken: id, name, version and SHA-256
    policies, versions            those policies (and any the chain or governance.toml names) and their
                                  published versions: each version's definition with its stored SHA-256
    assignments                   the platform's assignments on the chain
    exceptions                    the exceptions active when it was taken
    sha256                        the package's own hash, over everything above

Check now (and ``fabric-desktop governance check``) re-resolves from it with policy.resolve, the same
pure function the platform runs, on today's date and the project's current governance.toml, so an
exception or policy that has ended since is no longer applied.

**Hash integrity, not a signature.** ``verify`` refuses a package whose own SHA-256, or any version's
stored SHA-256, doesn't match what's in it: an accidental or partial edit is caught and the package is
never used as if valid. Someone who edits a definition and recomputes the hashes is not caught: that
needs a signing key, which this app doesn't have (no app registrations), so packages aren't signed.

Standard library only; it makes no platform, Fabric or OneLake call. Taking a package (``take``) is
given the platform's answers by the caller.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .policy import definition_hash, resolve

FORMAT = "kratosforge-policy-package"
FORMAT_VERSION = 1
FOLDER = "governance_packages"  # in the app's data folder: <project>.json, the latest package per project
_EXCEPTION_KEYS = ("id", "policy_id", "scope", "target", "until", "reason", "approved_by", "ended_at")


class PackageError(ValueError):
    """A package that can't be used: unreadable, for another project, or its hashes don't match."""


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def package_hash(package: dict[str, Any]) -> str:
    """The SHA-256 of everything in the package except its own ``sha256``."""
    return hashlib.sha256(_canonical({k: v for k, v in package.items() if k != "sha256"})).hexdigest()


def platform_label(platform: dict[str, Any]) -> str:
    if platform.get("kind") == "fabric":
        return f"UDF_Platform {platform.get('functions_id') or '?'} in workspace {platform.get('workspace_id') or '?'}"
    return f"the platform on this PC ({platform.get('name') or 'SQLite'})"


def platform_of(catalog: Any) -> dict[str, Any]:
    """Which platform a catalog client talks to, by ids only (no token, no URL with a secret)."""
    if getattr(catalog, "kind", "") == "fabric":
        return {"kind": "fabric", "workspace_id": str(getattr(catalog, "workspace_id", "") or ""),
                "functions_id": str(getattr(catalog, "functions_id", "") or "")}
    path = getattr(catalog, "path", None)
    return {"kind": "local", "name": Path(path).name if path else ""}


def take(project: str, platform: dict[str, Any], resolved: dict[str, Any], snapshot: dict[str, Any],
         exceptions: list[dict[str, Any]], taken_at: str = "") -> dict[str, Any]:
    """A package from the platform's answers: ``resolved`` (resolve_policies for the project), ``snapshot``
    (policy_snapshot) and ``exceptions`` (list_policy_exceptions, active). Refused when a version's
    definition doesn't match its stored SHA-256 in the platform: a package must never carry one."""
    chain = [(c["scope"], c["target"]) for c in resolved.get("chain") or []]
    if not chain:
        raise PackageError("The platform didn't say which scopes the project inherits from, so no package was taken.")
    assignments = [{k: a.get(k) for k in ("policy_id", "scope", "target")} | {"settings": a.get("settings") or {}}
                   for a in snapshot.get("assignments") or [] if (a.get("scope"), a.get("target")) in chain]
    wanted = {a["policy_id"] for a in assignments}
    wanted |= {str(p.get("id")) for p in (resolved.get("policies") or []) + (resolved.get("inactive") or [])}
    policies = {pid: {k: meta.get(k) for k in ("id", "name", "status", "current_version")}
                for pid, meta in sorted((snapshot.get("policies") or {}).items()) if pid in wanted}
    versions: dict[str, Any] = {}
    for key, v in sorted((snapshot.get("versions") or {}).items()):
        if key.split("@", 1)[0] not in policies:
            continue
        if definition_hash(v["definition"]) != v.get("hash"):
            raise PackageError(f"Version {v.get('version')} of {key.split('@', 1)[0]} doesn't match its stored SHA-256 in "
                               "the platform, so no package was taken. A platform admin should look at the policy's history.")
        versions[key] = {"version": int(v["version"]), "definition": v["definition"], "sha256": v["hash"]}
    out = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "project": project,
        "taken_at": taken_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "platform": platform, "chain": [{"scope": s, "target": t} for s, t in chain],
        "resolved": [{"id": p["id"], "name": p.get("name"), "version": int(p["version"]), "sha256": p.get("hash")}
                     for p in resolved.get("policies") or []],
        "policies": policies, "versions": versions, "assignments": assignments,
        "exceptions": [{k: e.get(k) for k in _EXCEPTION_KEYS} for e in exceptions
                       if e.get("policy_id") in policies and e.get("active", True) and not e.get("ended_at")],
    }
    out["sha256"] = package_hash(out)
    return out


def verify(package: Any, where: str = "The policy package") -> dict[str, Any]:
    """The package, when it is whole; else PackageError saying what doesn't match. Never returns a
    package whose hashes don't match."""
    retake = "It isn't used. Take a new one: Check now while the platform can be reached, or fabric-desktop governance package."
    if not isinstance(package, dict) or package.get("format") != FORMAT:
        raise PackageError(f"{where} isn't a KratosForge policy package. {retake}")
    if not isinstance(package.get("format_version"), int) or package["format_version"] > FORMAT_VERSION:
        raise PackageError(f"{where} was made by a newer KratosForge (format {package.get('format_version')!r}). Update the app, or take a new one.")
    stored, actual = str(package.get("sha256") or ""), package_hash(package)
    if stored != actual:
        raise PackageError(f"{where} was changed after it was taken: its SHA-256 is {actual[:12]}..., not the "
                           f"{(stored or 'missing')[:12]}... it was taken with. {retake}")
    try:
        for key, v in package["versions"].items():
            if definition_hash(v["definition"]) != v["sha256"]:
                raise PackageError(f"{where}: version {v['version']} of {key.split('@', 1)[0]} doesn't match its "
                                   f"stored SHA-256. {retake}")
        for p in package["resolved"]:
            v = package["versions"].get(f"{p['id']}@{p['version']}")
            if v is None or v["sha256"] != p["sha256"]:
                raise PackageError(f"{where}: {p['id']} version {p['version']} isn't in it with the SHA-256 it was "
                                   f"resolved with. {retake}")
        missing = [k for k in ("project", "taken_at", "platform", "chain", "policies", "assignments", "exceptions")
                   if k not in package]
        if missing:
            raise KeyError(", ".join(missing))
    except (KeyError, TypeError, AttributeError) as exc:
        raise PackageError(f"{where} is missing a part ({type(exc).__name__}: {exc}). {retake}") from exc
    return package


def read(path: Path, where: str = "") -> dict[str, Any]:
    """A package file, verified. Read as it is: a broken file is refused, never moved or repaired."""
    where = where or f"The policy package {path}"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackageError(f"{where} isn't there.") from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PackageError(f"{where} can't be read ({type(exc).__name__}: {exc}). It isn't used.") from exc
    return verify(data, where)


def resolve_offline(package: dict[str, Any], local: list[dict[str, Any]] | None = None, today: str = "") -> dict[str, Any]:
    """The project's policies in force, from a verified package alone, as resolve_policies gives them."""
    snap = {"policies": package["policies"], "assignments": package["assignments"],
            "versions": {k: {"version": v["version"], "definition": v["definition"], "hash": v["sha256"], "intact": True}
                         for k, v in package["versions"].items()}}
    chain = [(c["scope"], c["target"]) for c in package["chain"]]
    exceptions = [dict(e, note=f"Excepted until {e.get('until')}: {e.get('reason') or 'no reason given'}")
                  for e in package["exceptions"]]
    out = resolve(snap, chain, local, {}, today, exceptions)
    return out | {"project": package["project"]}


def age(taken_at: str, now: dt.datetime | None = None) -> str:
    """How old a package is, in words: "12 min", "5 h", "3 days"."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        then = dt.datetime.fromisoformat(str(taken_at))
    except ValueError:
        return "of unknown age"
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    minutes = max(0, int((now - then).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} days"


def summary(package: dict[str, Any], path: Path, now: dt.datetime | None = None) -> dict[str, Any]:
    """What a result says about the package it used: which file, from where, when and how old."""
    return {"file": str(path), "taken_at": package["taken_at"], "age": age(package["taken_at"], now),
            "platform": platform_label(package["platform"]), "sha256": package["sha256"],
            "policies": len(package["resolved"])}
