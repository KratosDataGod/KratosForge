"""Governance policies: the platform's registry of enterprise standards, and how they combine.

Lives next to core.py and, like it, runs in two places (the SQLite platform on this PC and
UDF_Platform in Fabric), so it is standard-library only. udf.py appends this file to core.py
to make the function app, so it uses core's names directly and adds its operations to
core.OPERATIONS.

Tables (T-SQL catalog.<table>; SQLite catalog__<table>):

    catalog.policy             one row per policy: its latest name, category, owner and status
    catalog.policy_version     every version: a draft (editable) or published (never changed again)
    catalog.policy_assignment  where a policy applies: enterprise, a domain, a project or an entity
    catalog.policy_event       each policy's history: created, edited, published, deprecated, ...

A policy is written as a draft, then published as version 1, 2, ... A published version's
definition and its SHA-256 hash are stored once and never updated; changing a policy means a
new draft and a new version. Deprecated policies still apply, with a warning; retired ones no
longer apply but stay readable with all their versions.

Resolution walks the scopes from the top: enterprise, domain, subdomain, project, entity. The
first assignment of a policy introduces it; each lower one may change its settings. Once an
assignment makes a policy mandatory, nothing below it may make it weaker (a lower enforcement
or severity, switching it off, an older version, a looser parameter); such a change is refused
when it's saved to the platform, and ignored with a reason when it comes from a project file.
Every step is returned, so a result can always be explained.

Who may change what:

    platform admins      create, edit, publish, deprecate and retire policies; assign them anywhere
    domain owners        assign policies to their own domain (and its subdomains), tightening only
    everyone             reading policies, their history and how they resolve
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from .core import (OPERATIONS, SCHEMA_HOOKS, Db, PlatformError, _audit, _column, _names, _now, _row, _upsert, _who, domain_id,
                   is_admin, require_admin)

POLICY_CATEGORIES = {
    "naming": "Naming standards",
    "schema": "Schema standards",
    "quality": "Data quality requirements",
    "classification": "Classification requirements",
    "ownership": "Ownership and stewardship",
    "contract": "Data contract requirements",
    "deployment": "Deployment readiness",
}
POLICY_STATUS = ("draft", "published", "deprecated", "retired")
SEVERITIES = ("low", "medium", "high", "critical")
# "off" is only for an assignment that switches a policy off below where it was introduced.
ENFORCEMENTS = ("off", "advisory", "warn", "block")
SCOPES = ("enterprise", "domain", "project", "entity")
# Which way a parameter gets stricter, so a mandatory policy's parameter may only move that way.
STRICTER = ("higher", "lower", "superset", "subset", "true", "fixed")
SETTINGS = ("enforcement", "severity", "mandatory", "version", "params")
_POLICY_TABLES: dict[str, list[tuple[str, str]]] = {
    "policy": [("id", "key"), ("name", "text"), ("category", "text"), ("owner", "text"), ("status", "text"),
               ("current_version", "text"), ("draft_version", "text"), ("created_at", "text"), ("created_by", "text"),
               ("updated_at", "text"), ("updated_by", "text")],
    "policy_version": [("id", "key"), ("policy_id", "text"), ("version", "text"), ("status", "text"),
                       ("definition", "long"), ("hash", "text"), ("note", "long"), ("created_at", "text"),
                       ("created_by", "text"), ("published_at", "text"), ("published_by", "text")],
    "policy_assignment": [("id", "key"), ("policy_id", "text"), ("scope", "text"), ("target", "text"),
                          ("settings", "long"), ("note", "long"), ("updated_at", "text"), ("updated_by", "text")],
    "policy_event": [("id", "key"), ("policy_id", "text"), ("version", "text"), ("action", "text"), ("at", "text"),
                     ("by_user", "text"), ("detail", "long")],
}


def ensure_policy_schema(db: Db) -> list[str]:
    """Create the policy tables that are missing. core.ensure_schema calls this; so does every
    policy operation, so a platform made before policies existed gets them on first use."""
    created = []
    for table, cols in _POLICY_TABLES.items():
        if not db.exists("catalog", table):
            db.run(f"CREATE TABLE {db.t('catalog', table)} ({', '.join(_column(db, n, k) for n, k in cols)})")
            created.append(f"catalog.{table}")
    if created:
        db.commit()
    return created


def _ready(db: Db) -> None:
    if not db.exists("catalog", "policy_event"):
        ensure_policy_schema(db)


def policies_ready(db: Db) -> bool:
    """Whether this platform has its policy tables. Only looks: unlike every other policy
    operation it never creates them (a platform repository's export asks this first)."""
    return all(db.exists("catalog", t) for t in _POLICY_TABLES)


def policy_id(value: Any) -> str:
    value = str(value or "").strip().lower().replace(" ", "-")
    if not value or len(value) > 64 or not all(c.isalnum() or c in "-_." for c in value) or not value[0].isalnum():
        raise PlatformError(f"{value!r} isn't a policy id: lowercase letters, digits, - _ or ., up to 64 characters")
    return value


def _date(value: Any, what: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return dt.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise PlatformError(f"{what} is a date like 2027-01-31") from None


def _choice(value: Any, allowed: tuple[str, ...] | list[str], what: str) -> str:
    if value not in allowed:
        raise PlatformError(f"{what} must be one of {', '.join(allowed)}")
    return value


def _param(name: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict) or "value" not in spec:
        raise PlatformError(f"parameter {name} needs a value, like {{\"value\": 40, \"stricter\": \"lower\"}}")
    value, stricter = spec["value"], spec.get("stricter", "fixed")
    _choice(stricter, STRICTER, f"parameter {name}'s stricter")
    if stricter in ("higher", "lower") and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise PlatformError(f"parameter {name} gets stricter {stricter}, so its value is a number")
    if stricter in ("superset", "subset") and not isinstance(value, list):
        raise PlatformError(f"parameter {name} gets stricter as a {stricter}, so its value is a list")
    if stricter == "true" and not isinstance(value, bool):
        raise PlatformError(f"parameter {name} gets stricter when true, so its value is true or false")
    return {"value": value, "stricter": stricter, "description": str(spec.get("description") or "")}


def clean_definition(d: dict[str, Any]) -> dict[str, Any]:
    """A policy definition with every field checked and defaulted, in a fixed shape."""
    d = dict(d or {})
    name = str(d.get("name") or "").strip()
    if not name:
        raise PlatformError("A policy needs a name")
    scopes = [s for s in SCOPES if s in (d.get("scopes") or SCOPES)]
    unknown = sorted(set(d.get("scopes") or []) - set(SCOPES))
    if unknown or not scopes:
        raise PlatformError(f"scopes are some of {', '.join(SCOPES)}")
    applies = d.get("applies_to") or {}
    if not isinstance(applies, dict) or set(applies) - {"layers", "environments", "asset_types"}:
        raise PlatformError("applies_to may list layers, environments and asset_types")
    effective, expires = _date(d.get("effective_from"), "Effective from"), _date(d.get("expires_on"), "Expires on")
    if effective and expires and expires <= effective:
        raise PlatformError("A policy must expire after it takes effect")
    params = d.get("params") or {}
    if not isinstance(params, dict):
        raise PlatformError("params is a set of named parameters")
    enforcement = _choice(d.get("enforcement", "warn"), ENFORCEMENTS[1:], "enforcement")
    return {
        "name": name, "description": str(d.get("description") or ""),
        "category": _choice(d.get("category"), tuple(POLICY_CATEGORIES), "category"),
        "owner": str(d.get("owner") or "").strip(),
        "severity": _choice(d.get("severity", "medium"), SEVERITIES, "severity"),
        "enforcement": enforcement, "mandatory": bool(d.get("mandatory", False)),
        "scopes": scopes,
        "applies_to": {k: sorted(str(x) for x in (applies.get(k) or [])) for k in ("asset_types", "environments", "layers")},
        "effective_from": effective, "expires_on": expires,
        "exception_eligible": bool(d.get("exception_eligible", True)),
        "remediation": str(d.get("remediation") or ""),
        "rule": str(d.get("rule") or "").strip(),
        "params": {str(k): _param(str(k), v) for k, v in sorted(params.items())},
    }


def definition_hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _event(db: Db, pid: str, version: Any, action: str, by: str, detail: Any = None) -> None:
    # Microseconds, so events in the same second list in the order they happened.
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    db.run(f"INSERT INTO {db.t('catalog', 'policy_event')} (id, policy_id, version, action, at, by_user, detail) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)",
           (uuid.uuid4().hex[:16], pid, str(version or ""), action, at, _who(by),
            json.dumps(detail, default=str) if detail is not None else None))
    _audit(db, by, f"policy {action}", pid + (f"@{version}" if version else ""), detail)


def _version_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["version"] = int(row["version"])
    out["definition"] = json.loads(row.get("definition") or "{}")
    if row.get("status") == "published":
        out["intact"] = definition_hash(out["definition"]) == row.get("hash")
    return out


def _versions(db: Db, pid: str) -> list[dict[str, Any]]:
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'policy_version')} WHERE policy_id = ?", (pid,))
    return sorted((_version_out(r) for r in rows), key=lambda v: v["version"])


def _policy(db: Db, pid: str) -> dict[str, Any]:
    row = _row(db, "policy", "id", policy_id(pid))
    if row is None:
        raise PlatformError(f"There's no policy {pid}")
    return row


def _policy_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in ("current_version", "draft_version"):
        out[k] = int(row[k]) if row.get(k) else None
    return out


def _require_open(row: dict[str, Any]) -> None:
    if row["status"] == "retired":
        raise PlatformError(f"{row['id']} is retired: its versions stay readable but it can't change any more")


# --------------------------------------------------------------------------- authoring


def create_policy(db: Db, policy: dict[str, Any], by: str = "") -> dict[str, Any]:
    """A new policy, as a draft (version 1). Platform admins only."""
    _ready(db)
    require_admin(db, by, "create governance policies")
    pid = policy_id(policy.get("id") or policy.get("name"))
    if _row(db, "policy", "id", pid):
        raise PlatformError(f"There's already a policy {pid}: open it and edit its draft")
    d = clean_definition(policy)
    now = _now()
    _upsert(db, "catalog", "policy", "id", {
        "id": pid, "name": d["name"], "category": d["category"], "owner": d["owner"], "status": "draft",
        "current_version": None, "draft_version": "1", "created_at": now, "created_by": _who(by),
        "updated_at": now, "updated_by": _who(by)})
    _upsert(db, "catalog", "policy_version", "id", {
        "id": f"{pid}@1", "policy_id": pid, "version": "1", "status": "draft", "definition": json.dumps(d),
        "hash": None, "note": str(policy.get("note") or ""), "created_at": now, "created_by": _who(by),
        "published_at": None, "published_by": None})
    _event(db, pid, 1, "created", by, {"name": d["name"], "category": d["category"]})
    db.commit()
    return get_policy(db, pid)


def save_policy_draft(db: Db, id: str, policy: dict[str, Any], note: str = "", by: str = "") -> dict[str, Any]:
    """Change a policy's draft, starting a new draft version from the latest published one if
    there's no draft yet. A published version is never changed. Platform admins only."""
    _ready(db)
    row = _policy(db, id)
    require_admin(db, by, "edit governance policies")
    _require_open(row)
    d = clean_definition(policy)
    now = _now()
    if row.get("draft_version"):
        n = int(row["draft_version"])
        action = "edited"
    else:
        n = max((v["version"] for v in _versions(db, row["id"])), default=0) + 1
        action = "new draft"
    vid = f"{row['id']}@{n}"
    current = _row(db, "policy_version", "id", vid)
    if current is not None and current["status"] != "draft":
        raise PlatformError(f"Version {n} of {row['id']} is published and can't be changed")
    _upsert(db, "catalog", "policy_version", "id", {
        "id": vid, "policy_id": row["id"], "version": str(n), "status": "draft", "definition": json.dumps(d),
        "hash": None, "note": str(note or (current or {}).get("note") or ""),
        "created_at": (current or {}).get("created_at") or now, "created_by": (current or {}).get("created_by") or _who(by),
        "published_at": None, "published_by": None})
    fields = {"draft_version": str(n), "updated_at": now, "updated_by": _who(by)}
    if not row.get("current_version"):
        fields |= {"name": d["name"], "category": d["category"], "owner": d["owner"]}
    _set(db, row["id"], fields)
    _event(db, row["id"], n, action, by, {"note": note} if note else None)
    db.commit()
    return get_policy(db, row["id"])


def _set(db: Db, pid: str, fields: dict[str, Any]) -> None:
    db.run(f"UPDATE {db.t('catalog', 'policy')} SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
           [*fields.values(), pid])


def discard_policy_draft(db: Db, id: str, by: str = "") -> dict[str, Any]:
    """Throw away a draft. A policy that was never published goes with it."""
    _ready(db)
    row = _policy(db, id)
    require_admin(db, by, "discard policy drafts")
    if not row.get("draft_version"):
        raise PlatformError(f"{row['id']} has no draft")
    n = row["draft_version"]
    db.run(f"DELETE FROM {db.t('catalog', 'policy_version')} WHERE id = ? AND status = 'draft'", (f"{row['id']}@{n}",))
    if not row.get("current_version"):
        if db.rows(f"SELECT id FROM {db.t('catalog', 'policy_assignment')} WHERE policy_id = ?", (row["id"],)):
            raise PlatformError(f"{row['id']} is assigned somewhere; remove its assignments first")
        db.run(f"DELETE FROM {db.t('catalog', 'policy')} WHERE id = ?", (row["id"],))
        _event(db, row["id"], n, "deleted draft", by)
        db.commit()
        return {"id": row["id"], "deleted": True}
    _set(db, row["id"], {"draft_version": None, "updated_at": _now(), "updated_by": _who(by)})
    _event(db, row["id"], n, "discarded draft", by)
    db.commit()
    return get_policy(db, row["id"])


def publish_policy(db: Db, id: str, note: str = "", by: str = "") -> dict[str, Any]:
    """Publish the draft as a new version. From now on that version never changes. Platform admins only."""
    _ready(db)
    row = _policy(db, id)
    require_admin(db, by, "publish governance policies")
    _require_open(row)
    if not row.get("draft_version"):
        raise PlatformError(f"{row['id']} has no draft to publish")
    n = int(row["draft_version"])
    v = _row(db, "policy_version", "id", f"{row['id']}@{n}")
    if v is None or v["status"] != "draft":
        raise PlatformError(f"{row['id']} has no draft to publish")
    d = clean_definition(json.loads(v["definition"] or "{}"))
    now = _now()
    db.run(f"UPDATE {db.t('catalog', 'policy_version')} SET status = 'published', definition = ?, hash = ?, "
           "note = ?, published_at = ?, published_by = ? WHERE id = ? AND status = 'draft'",
           (json.dumps(d), definition_hash(d), str(note or v.get("note") or ""), now, _who(by), v["id"]))
    status = "deprecated" if row["status"] == "deprecated" else "published"
    _set(db, row["id"], {"current_version": str(n), "draft_version": None, "status": status, "name": d["name"],
                         "category": d["category"], "owner": d["owner"], "updated_at": now, "updated_by": _who(by)})
    _event(db, row["id"], n, "published", by, {"hash": definition_hash(d), "note": note} if note else
           {"hash": definition_hash(d)})
    db.commit()
    return get_policy(db, row["id"])


def set_policy_status(db: Db, id: str, status: str, note: str = "", by: str = "") -> dict[str, Any]:
    """Deprecate a published policy (it still applies, with a warning), retire it (it stops
    applying; nothing about it can change after), or bring a deprecated one back. Platform admins only."""
    _ready(db)
    row = _policy(db, id)
    require_admin(db, by, "deprecate or retire governance policies")
    _choice(status, ("published", "deprecated", "retired"), "status")
    _require_open(row)
    if not row.get("current_version"):
        raise PlatformError(f"{row['id']} was never published: discard its draft instead")
    if status == row["status"]:
        return get_policy(db, row["id"])
    if status == "retired" and not str(note or "").strip():
        raise PlatformError("Say why the policy is retired, so the people it applied to know")
    _set(db, row["id"], {"status": status, "updated_at": _now(), "updated_by": _who(by)})
    action = {"published": "restored", "deprecated": "deprecated", "retired": "retired"}[status]
    _event(db, row["id"], row["current_version"], action, by, {"note": note} if note else None)
    db.commit()
    return get_policy(db, row["id"])


# --------------------------------------------------------------------------- reading


def list_policies(db: Db, query: str = "", category: str = "", status: str = "") -> list[dict[str, Any]]:
    """Policies, by name, filtered by text (id, name, owner, category), category and status."""
    _ready(db)
    q = str(query or "").strip().lower()
    counts: dict[str, int] = {}
    for a in db.rows(f"SELECT policy_id FROM {db.t('catalog', 'policy_assignment')}"):
        counts[a["policy_id"]] = counts.get(a["policy_id"], 0) + 1
    out = []
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'policy')}"):
        if category and r["category"] != category or status and r["status"] != status:
            continue
        hay = " ".join(str(r.get(k) or "") for k in ("id", "name", "owner", "category")).lower()
        if q and q not in hay:
            continue
        out.append(_policy_out(r) | {"assignments": counts.get(r["id"], 0)})
    return sorted(out, key=lambda p: (str(p["name"] or "").lower(), p["id"]))


def get_policy(db: Db, id: str) -> dict[str, Any]:
    """A policy with every version (drafts and published), its assignments and its history."""
    _ready(db)
    row = _policy(db, id)
    versions = _versions(db, row["id"])
    current = next((v for v in versions if row.get("current_version") and v["version"] == int(row["current_version"])), None)
    draft = next((v for v in versions if row.get("draft_version") and v["version"] == int(row["draft_version"])), None)
    return _policy_out(row) | {"versions": versions, "current": current, "draft": draft,
                               "assignments": list_assignments(db, row["id"]), "history": policy_history(db, row["id"])}


def policy_history(db: Db, id: str, limit: int = 200) -> list[dict[str, Any]]:
    """What happened to a policy, newest first, with who did it and when."""
    _ready(db)
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'policy_event')} WHERE policy_id = ?", (policy_id(id),))
    rows = sorted(rows, key=lambda e: e["at"], reverse=True)[:int(limit)]
    return [dict(r, detail=json.loads(r["detail"]) if r.get("detail") else None) for r in rows]


def policy_version(db: Db, id: str, version: int) -> dict[str, Any]:
    _ready(db)
    v = _row(db, "policy_version", "id", f"{policy_id(id)}@{int(version)}")
    if v is None:
        raise PlatformError(f"{id} has no version {version}")
    return _version_out(v)


# --------------------------------------------------------------------------- assignments


def _target(db: Db, scope: str, target: str) -> str:
    target = str(target or "").strip()
    if scope == "enterprise":
        return "*"
    if scope == "domain":
        did = domain_id(target)
        if _row(db, "domain", "id", did) is None:
            raise PlatformError(f"domain {did} isn't one of your organization's domains")
        return did
    if not target:
        raise PlatformError(f"Say which {scope} the policy applies to")
    if scope == "entity" and ":" not in target:
        raise PlatformError("An entity is named <project>:<entity>, like crm:silver.customer")
    return target


def _clean_settings(definition: dict[str, Any], versions: list[int], settings: dict[str, Any]) -> dict[str, Any]:
    settings = dict(settings or {})
    extra = sorted(set(settings) - set(SETTINGS))
    if extra:
        raise PlatformError(f"{', '.join(extra)} can't be set on an assignment; it takes {', '.join(SETTINGS)}")
    out: dict[str, Any] = {}
    if settings.get("enforcement") not in (None, ""):
        out["enforcement"] = _choice(settings["enforcement"], ENFORCEMENTS, "enforcement")
    if settings.get("severity") not in (None, ""):
        out["severity"] = _choice(settings["severity"], SEVERITIES, "severity")
    if settings.get("mandatory") is not None:
        out["mandatory"] = bool(settings["mandatory"])
    if settings.get("version") not in (None, "", 0):
        n = int(settings["version"])
        if n not in versions:
            raise PlatformError(f"version {n} isn't a published version")
        out["version"] = n
    params = settings.get("params") or {}
    for k, v in params.items():
        spec = definition.get("params", {}).get(k)
        if spec is None:
            raise PlatformError(f"{k} isn't one of this policy's parameters")
        _param(k, {"value": v, "stricter": spec["stricter"]})
        out.setdefault("params", {})[k] = v
    return out


def _chain(db: Db, scope: str, target: str) -> list[tuple[str, str]]:
    """The scopes above (and including) a target, from the top: what a policy inherits from."""
    chain = [("enterprise", "*")]
    if scope == "enterprise":
        return chain
    if scope == "domain":
        d = _row(db, "domain", "id", target) or {}
        return chain + ([("domain", d["parent_id"])] if d.get("parent_id") else []) + [("domain", target)]
    project = target.split(":", 1)[0]
    p = _row(db, "project", "name", project) or {}
    chain += [("domain", d) for d in (p.get("domain_id"), p.get("subdomain_id")) if d]
    chain.append(("project", project))
    if scope == "entity":
        chain.append(("entity", target))
    return chain


def _may_assign(db: Db, scope: str, target: str, by: str) -> None:
    if is_admin(db, by):
        return
    if scope == "domain":
        owners = set()
        d = _row(db, "domain", "id", target) or {}
        for did in (target, d.get("parent_id")):
            if did:
                owners |= _names((_row(db, "domain", "id", did) or {}).get("owners"))
        if _who(by) and _who(by) in owners:
            return
        raise PlatformError(f"Only platform admins, or the owners of {target}, can assign policies to it")
    require_admin(db, by, f"assign policies at {scope} scope")


def save_assignment(db: Db, policy: str, scope: str, target: str = "", settings: dict[str, Any] | None = None,
                    note: str = "", by: str = "") -> dict[str, Any]:
    """Apply a published policy at a scope, optionally changing its settings there. Under a
    mandatory assignment above, only stricter settings are accepted."""
    _ready(db)
    row = _policy(db, policy)
    _choice(scope, SCOPES, "scope")
    target = _target(db, scope, target)
    _may_assign(db, scope, target, by)
    _require_open(row)
    if not row.get("current_version"):
        raise PlatformError(f"{row['id']} isn't published yet: publish it before assigning it")
    versions = [v["version"] for v in _versions(db, row["id"]) if v["status"] == "published"]
    current = policy_version(db, row["id"], int(row["current_version"]))["definition"]
    if scope not in current["scopes"]:
        raise PlatformError(f"{row['id']} can be assigned at {', '.join(current['scopes'])} scope only")
    clean = _clean_settings(current, versions, settings or {})
    aid = f"{row['id']}|{scope}|{target}"
    # A change that a mandatory assignment above won't take is refused here, not saved and ignored later.
    above = [x for x in _chain(db, scope, target) if x != (scope, target)]
    snap = snapshot(db, policies=[row["id"]])
    snap["assignments"] = [a for a in snap["assignments"] if (a["scope"], a["target"]) in above]
    result = resolve(snap, above + [(scope, target)], local=[{"policy": row["id"], "scope": scope, "target": target,
                                                              "settings": clean}])
    refused = [c["message"] for c in result["conflicts"] if c["scope"] == scope and c["target"] == target]
    if refused:
        raise PlatformError("; ".join(refused))
    before = _row(db, "policy_assignment", "id", aid)
    _upsert(db, "catalog", "policy_assignment", "id", {
        "id": aid, "policy_id": row["id"], "scope": scope, "target": target, "settings": json.dumps(clean, sort_keys=True),
        "note": str(note or ""), "updated_at": _now(), "updated_by": _who(by)})
    _event(db, row["id"], clean.get("version") or row["current_version"], "assignment changed" if before else "assigned",
           by, {"scope": scope, "target": target, "settings": clean})
    db.commit()
    return _assignment_out(_row(db, "policy_assignment", "id", aid) or {})


def remove_assignment(db: Db, policy: str, scope: str, target: str = "", by: str = "") -> list[dict[str, Any]]:
    _ready(db)
    row = _policy(db, policy)
    _choice(scope, SCOPES, "scope")
    target = "*" if scope == "enterprise" else str(target or "").strip()
    _may_assign(db, scope, target, by)
    aid = f"{row['id']}|{scope}|{target}"
    if _row(db, "policy_assignment", "id", aid) is None:
        raise PlatformError(f"{row['id']} isn't assigned to {scope} {target}")
    db.run(f"DELETE FROM {db.t('catalog', 'policy_assignment')} WHERE id = ?", (aid,))
    _event(db, row["id"], "", "unassigned", by, {"scope": scope, "target": target})
    db.commit()
    return list_assignments(db, row["id"])


def _assignment_out(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row, settings=json.loads(row.get("settings") or "{}"))


def list_assignments(db: Db, policy: str = "", scope: str = "", target: str = "") -> list[dict[str, Any]]:
    _ready(db)
    rows = [_assignment_out(r) for r in db.rows(f"SELECT * FROM {db.t('catalog', 'policy_assignment')}")]
    rows = [r for r in rows if (not policy or r["policy_id"] == policy_id(policy)) and (not scope or r["scope"] == scope)
            and (not target or r["target"] == target)]
    return sorted(rows, key=lambda r: (SCOPES.index(r["scope"]), r["target"], r["policy_id"]))


def policy_applicability(db: Db, id: str) -> dict[str, Any]:
    """Where a policy applies: its assignments, and every registered project it reaches through them."""
    _ready(db)
    row = _policy(db, id)
    assignments = list_assignments(db, row["id"])
    projects = db.rows(f"SELECT name, domain_id, subdomain_id, team FROM {db.t('catalog', 'project')}")
    reached = []
    for p in sorted(projects, key=lambda p: p["name"]):
        via = [a for a in assignments
               if a["scope"] == "enterprise" or (a["scope"] == "domain" and a["target"] in (p.get("domain_id"), p.get("subdomain_id")))
               or (a["scope"] in ("project", "entity") and a["target"].split(":", 1)[0] == p["name"])]
        if via:
            r = resolve_policies(db, project=p["name"])
            got = next((x for x in r["policies"] if x["id"] == row["id"]), None)
            gone = next((x for x in r["inactive"] if x["id"] == row["id"]), None)
            reached.append({"project": p["name"], "domain": p.get("domain_id"), "subdomain": p.get("subdomain_id"),
                            "team": p.get("team"), "via": [_where(a) for a in via],
                            "enforcement": (got or {}).get("enforcement"), "version": (got or {}).get("version"),
                            "inactive": (gone or {}).get("reason")})
    return {"id": row["id"], "status": row["status"], "assignments": assignments, "projects": reached}


# --------------------------------------------------------------------------- resolution


def snapshot(db: Db, policies: list[str] | None = None) -> dict[str, Any]:
    """Everything resolution needs, as plain data: policies, their published versions and assignments.
    resolve() works on this alone, so it can run offline from a copy."""
    _ready(db)
    wanted = {policy_id(p) for p in policies} if policies else None
    out: dict[str, Any] = {"policies": {}, "versions": {}, "assignments": []}
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'policy')}"):
        if wanted is None or r["id"] in wanted:
            out["policies"][r["id"]] = _policy_out(r)
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'policy_version')} WHERE status = 'published'"):
        if r["policy_id"] in out["policies"]:
            v = _version_out(r)
            out["versions"][f"{r['policy_id']}@{v['version']}"] = {k: v[k] for k in ("version", "definition", "hash", "intact")}
    out["assignments"] = [a for a in list_assignments(db) if a["policy_id"] in out["policies"]]
    return out


def _weaker(field: str, spec: dict[str, Any] | None, old: Any, new: Any) -> bool:
    """Whether ``new`` is weaker than ``old`` for a setting (or a parameter with its spec)."""
    if field == "enforcement":
        return ENFORCEMENTS.index(new) < ENFORCEMENTS.index(old)
    if field == "severity":
        return SEVERITIES.index(new) < SEVERITIES.index(old)
    if field == "mandatory":
        return bool(old) and not bool(new)
    if field == "version":
        return int(new) < int(old)
    how = (spec or {}).get("stricter", "fixed")
    if how == "higher":
        return new < old
    if how == "lower":
        return new > old
    if how == "superset":
        return not set(map(str, new)) >= set(map(str, old))
    if how == "subset":
        return not set(map(str, new)) <= set(map(str, old))
    if how == "true":
        return bool(old) and not bool(new)
    return new != old


def _where(a: dict[str, Any]) -> str:
    return a["scope"] if a["scope"] == "enterprise" else f"{a['scope']} {a['target']}"


def resolve(snap: dict[str, Any], chain: list[tuple[str, str]], local: list[dict[str, Any]] | None = None,
            context: dict[str, Any] | None = None, today: str = "") -> dict[str, Any]:
    """The policies in force at the bottom of ``chain`` ([(scope, target)], top first), and why.

    ``local`` adds assignments that aren't in the platform (a project's governance.toml), placed
    at their scope in the chain after the platform's own. ``context`` (layer, environment,
    asset_type) filters by each policy's applies_to. Pure and deterministic: the same inputs
    always give the same result, in the same order."""
    today = today or dt.date.today().isoformat()
    context = context or {}
    rows = [dict(a, origin="platform") for a in snap.get("assignments", [])]
    rows += [{"policy_id": str(a.get("policy") or a.get("policy_id") or ""), "scope": a.get("scope", "project"),
              "target": a.get("target", ""), "settings": a.get("settings") or {}, "origin": a.get("origin", "local")}
             for a in local or []]
    levels = {lvl: i for i, lvl in enumerate(chain)}
    by_policy: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for a in rows:
        lvl = (a["scope"], a["target"])
        if lvl in levels:
            by_policy.setdefault(a["policy_id"], []).append((levels[lvl], 0 if a["origin"] == "platform" else 1, a))
    policies, inactive, conflicts = [], [], []
    for pid in sorted(by_policy):
        meta = snap.get("policies", {}).get(pid)
        if meta is None or not meta.get("current_version"):
            inactive.append({"id": pid, "name": pid, "reason": "isn't a published policy in the registry", "steps": []})
            continue
        version, explicit, params, steps = 0, {}, {}, []
        locked_by = ""

        def definition(n: int) -> dict[str, Any]:
            return snap["versions"][f"{pid}@{n}"]["definition"]

        def now(field: str) -> Any:
            return explicit[field] if field in explicit else definition(version)[field]

        def param(name: str) -> Any:
            return params[name] if name in params else definition(version)["params"][name]["value"]

        for _, _, a in sorted(by_policy[pid], key=lambda x: (x[0], x[1])):
            where = {"scope": a["scope"], "target": a["target"], "origin": a["origin"]}
            settings = dict(a["settings"])
            pin = settings.pop("version", None)
            if pin and f"{pid}@{int(pin)}" not in snap["versions"]:
                conflicts.append(where | {"policy": pid, "message": f"{pid} has no published version {pin}"})
                pin = None
            locked = bool(version) and bool(now("mandatory"))

            def change(label: str, old: Any, new: Any, weaker: bool, apply: Any) -> None:
                if new == old:
                    return
                if locked and weaker:
                    msg = (f"{pid} is mandatory from {locked_by}: {label} can't go from {json.dumps(old)} to "
                           f"{json.dumps(new)} here, only stricter")
                    conflicts.append(where | {"policy": pid, "message": msg})
                    steps.append(where | {"change": label, "from": old, "to": new, "outcome": "ignored", "reason": msg})
                    return
                apply()
                steps.append(where | {"change": label, "from": old, "to": new, "outcome": "applied"})

            if not version:
                version = int(pin or meta["current_version"])
                steps.append(where | {"change": "introduced", "version": version, "outcome": "applied"})
            elif pin:
                def repin(n: int = int(pin)) -> None:
                    nonlocal version
                    version = n
                change("version", version, int(pin), int(pin) < version, repin)
            for field in ("enforcement", "severity", "mandatory"):
                if field in settings:
                    change(field, now(field), settings[field], _weaker(field, None, now(field), settings[field]),
                           lambda f=field: explicit.__setitem__(f, settings[f]))
            for name, new in sorted((settings.get("params") or {}).items()):
                spec = definition(version)["params"].get(name)
                if spec is None:
                    conflicts.append(where | {"policy": pid, "message": f"{pid} version {version} has no parameter {name}"})
                    continue
                change(name, param(name), new, _weaker("params", spec, param(name), new),
                       lambda n=name, v=new: params.__setitem__(n, v))
            if now("mandatory") and not locked_by:
                locked_by = _where(a)
        d = definition(version)
        state = {"version": version, "enforcement": now("enforcement"), "severity": now("severity"),
                 "mandatory": bool(now("mandatory")), "locked_by": locked_by,
                 "params": {k: param(k) for k in d["params"]}}
        version_row = snap["versions"][f"{pid}@{version}"]
        reason = ""
        if meta["status"] == "retired":
            reason = "was retired"
        elif state["enforcement"] == "off":
            reason = "is switched off here"
        elif d["effective_from"] and today < d["effective_from"]:
            reason = f"takes effect on {d['effective_from']}"
        elif d["expires_on"] and today >= d["expires_on"]:
            reason = f"expired on {d['expires_on']}"
        else:
            for key, ctx in (("layers", "layer"), ("environments", "environment"), ("asset_types", "asset_type")):
                if d["applies_to"].get(key) and context.get(ctx) and context[ctx] not in d["applies_to"][key]:
                    reason = f"applies to {ctx} {', '.join(d['applies_to'][key])} only"
        if reason:
            inactive.append({"id": pid, "name": d["name"], "reason": reason, "steps": steps})
            continue
        policies.append({
            "id": pid, "name": d["name"], "category": d["category"], "owner": d["owner"],
            "description": d["description"], "rule": d["rule"], "version": state["version"],
            "hash": version_row["hash"], "intact": version_row.get("intact", True), "enforcement": state["enforcement"],
            "severity": state["severity"], "mandatory": state["mandatory"], "locked_by": state.get("locked_by", ""),
            "params": dict(sorted(state["params"].items())), "remediation": d["remediation"],
            "exception_eligible": d["exception_eligible"], "expires_on": d["expires_on"],
            "deprecated": meta["status"] == "deprecated", "introduced_at": _where(steps[0]),
            "steps": steps})
    return {"chain": [{"scope": s, "target": t} for s, t in chain], "context": context, "today": today,
            "policies": policies, "inactive": inactive, "conflicts": conflicts}


def resolve_policies(db: Db, project: str = "", entity: str = "", domain: str = "", subdomain: str = "",
                     local: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None,
                     today: str = "") -> dict[str, Any]:
    """The policies in force for an enterprise-wide view, a domain, a project or one entity.

    A project's domain and subdomain come from the catalog unless they're given (a project
    that isn't registered yet). ``local`` is the project's own governance.toml entries."""
    _ready(db)
    chain: list[tuple[str, str]] = [("enterprise", "*")]
    if project:
        p = _row(db, "project", "name", project) or {}
        domain = domain or p.get("domain_id") or ""
        subdomain = subdomain or p.get("subdomain_id") or ""
    chain += [("domain", d) for d in (domain, subdomain) if d]
    if project:
        chain.append(("project", project))
        if entity:
            chain.append(("entity", f"{project}:{entity}"))
    out = resolve(snapshot(db), chain, local, context, today)
    return out | {"project": project, "entity": entity, "domain": domain, "subdomain": subdomain}


def policy_options(db: Db) -> dict[str, Any]:
    """The fixed lists a policy form offers."""
    return {"categories": POLICY_CATEGORIES, "statuses": list(POLICY_STATUS), "severities": list(SEVERITIES),
            "enforcements": list(ENFORCEMENTS[1:]), "scopes": list(SCOPES), "stricter": list(STRICTER)}


SCHEMA_HOOKS.append(ensure_policy_schema)
OPERATIONS.update({
    "ensure_policy_schema": ensure_policy_schema, "create_policy": create_policy,
    "save_policy_draft": save_policy_draft, "discard_policy_draft": discard_policy_draft,
    "publish_policy": publish_policy, "set_policy_status": set_policy_status, "list_policies": list_policies,
    "get_policy": get_policy, "policy_history": policy_history, "policy_version": policy_version,
    "save_assignment": save_assignment, "remove_assignment": remove_assignment, "list_assignments": list_assignments,
    "policy_applicability": policy_applicability, "resolve_policies": resolve_policies, "policy_snapshot": snapshot,
    "policy_options": policy_options, "policies_ready": policies_ready,
})
