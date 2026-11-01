"""The shortcut command center: health, risk, access and needs of every OneLake shortcut.

Plain functions over the rows ``shortcuts.describe`` makes, so the rules are tested without
Fabric. ``shortcut_service.py`` reads Fabric and the platform and calls these.

Two ways a shortcut decides who reads it:

    OneLake shortcuts   each reader needs access in both places, the lakehouse with the shortcut
                        and the target: nobody reads more than the target allows.
    storage shortcuts   (ADLS Gen2, S3, Blob, ...) Fabric reads with the connection's key or
                        account, so anyone who can read the lakehouse reads the storage.
"""

from __future__ import annotations

import json
import re
from typing import Any

ENV_ORDER = ("dev", "test", "prod")
_ENV_WORDS = {"dev": ("dev", "development", "sandbox"), "test": ("test", "uat", "qa", "staging"),
              "prod": ("prod", "production", "prd", "live")}
RULE_TEXT = {
    "cross_env": "A shortcut may not point at another environment (Dev or Test at Prod, or Prod at Dev or Test)",
    "storage_role": "A storage shortcut should have a data access role on its folder",
    "deprecated_product": "A shortcut should not point at a deprecated data product",
    "connections": "Storage shortcuts use only the allowed connections",
    "who_creates": "Who may create shortcuts from KratosForge",
}


def env_of(workspace_id: str, workspace_name: str, known: dict[str, str]) -> str:
    """dev, test or prod: the metadata project's or platform's own say first, else a word in the name."""
    if workspace_id in known:
        return known[workspace_id]
    words = set(re.split(r"[^a-z0-9]+", str(workspace_name or "").lower()))
    for env, names in _ENV_WORDS.items():
        if words & set(names):
            return env
    return ""


def identity(s: dict[str, Any]) -> str:
    return f"{s['lakehouse_id']}|{s['full_path'].strip('/')}"


def target_key(s: dict[str, Any]) -> str:
    """The same target in two shortcuts gives the same key."""
    if s.get("target_type") == "OneLake":
        return f"onelake:{s.get('workspace_id')}/{s.get('item_id')}/{str(s.get('target_path') or '').strip('/')}".lower()
    return f"{s.get('target_type')}:{str(s.get('target') or '').strip('/')}".lower()


def exposure(s: dict[str, Any]) -> str:
    """identity: readers need the target too. connection: Fabric reads with the connection's account."""
    return "identity" if s.get("target_type") == "OneLake" else "connection"


def health_from_probe(s: dict[str, Any], host_status: int | None, status: int | None, message: str = "") -> dict[str, str]:
    """What a listing through OneLake says about a shortcut. ``host_status`` is the listing of the
    folder the shortcut sits in, ``status`` the shortcut's own (None: the request didn't go out)."""
    onelake = s.get("target_type") == "OneLake"
    if host_status in (401, 403):
        return {"health": "not_checked", "detail": "You can't read the lakehouse it's in, so it can't be checked"}
    if status is None:
        return {"health": "not_checked", "detail": f"The check didn't reach OneLake: {message}".strip(": ")}
    if 200 <= status < 300:
        return {"health": "healthy", "detail": ""}
    # OneLake was busy (429, 503), or failed on a OneLake target: that says nothing about the shortcut.
    # For an external target (ADLS, S3, GCS) OneLake passes on the storage's own failure as a 500, 502
    # or 504, which can be a real break, so those stay broken and the watch still posts them.
    if status in (429, 503) or (onelake and status >= 500):
        return {"health": "not_checked", "detail": f"OneLake was busy (it answered {status}), so this wasn't checked. "
                                                   "It's checked again next time"}
    if status == 404:
        return {"health": "broken", "detail": "The target table or folder isn't there any more (removed, renamed or moved)"
                if onelake else "The storage path isn't there any more"}
    if status in (401, 403):
        if onelake:
            return {"health": "unreachable",
                    "detail": "You have no access to the target, so the check can't tell whether it works for others"}
        return {"health": "broken",
                "detail": "The storage refused the connection's sign-in (an expired key or secret, or no rights on the storage)"}
    return {"health": "broken", "detail": f"OneLake answered {status}: {message[:200]}".strip()}


def risks(rows: list[dict[str, Any]], envs: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """Not broken, but worth a look: crossing environments, and two shortcuts with one target in a
    workspace. ``rows`` carry workspace_name/lakehouse_name of where they live; ``envs`` maps
    workspace ids to environments. Returns {shortcut identity: [{kind, text}]}."""
    out: dict[str, list[dict[str, str]]] = {}
    by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in rows:
        sid = identity(s)
        host = s.get("host_env") or ""
        tgt = s.get("target_env") or ""
        if host and tgt and host != tgt:
            out.setdefault(sid, []).append({"kind": "cross_env",
                                            "text": f"{host.title()} reads {tgt.title()}" + (
                                                ": a Dev or Test run reads live data" if tgt == "prod" else
                                                ": Prod depends on data that isn't released" if host == "prod" else "")})
        by_target.setdefault((s.get("host_workspace_id") or "", target_key(s)), []).append(s)
    for (_, _), same in by_target.items():
        if len(same) < 2:
            continue
        for s in same:
            others = [o for o in same if o is not s]
            out.setdefault(identity(s), []).append({"kind": "duplicate", "text": "Same target as " + ", ".join(
                f"{o['name']} in {o.get('lakehouse_name') or 'another lakehouse'}" for o in others[:3])})
    return out


def open_risks(s: dict[str, Any]) -> list[dict[str, Any]]:
    """The risks no platform admin has accepted."""
    return [r for r in s.get("risks") or [] if not r.get("accepted")]


def accept_risks(rows: list[dict[str, Any]], accepted: list[dict[str, Any]]) -> None:
    """Mark each risk a platform admin accepted with who and why ({by, reason, at})."""
    by = {(a["shortcut_id"], a["kind"]): a for a in accepted}
    for s in rows:
        for r in s.get("risks") or []:
            a = by.get((s["id"], r["kind"]))
            if a:
                r["accepted"] = {"by": a.get("by") or "", "reason": a.get("reason") or "", "at": a.get("at") or ""}


def same_env_target(s: dict[str, Any], envs: dict[str, str], lakehouses: list[dict[str, Any]]) -> dict[str, Any] | None:
    """For a shortcut that crosses environments: the lakehouse with the target's name in a workspace
    the metadata project or platform says is the host's environment. Names alone don't decide this."""
    host, name = s.get("host_env") or "", str(s.get("target_lakehouse_name") or "").lower()
    if not (host and name and s.get("target_type") == "OneLake"):
        return None
    for l in lakehouses:
        if envs.get(l.get("workspace_id") or "") == host and str(l.get("name") or "").lower() == name \
                and l.get("lakehouse_id") != s.get("item_id"):
            return {"workspace_id": l["workspace_id"], "workspace_name": l.get("workspace_name") or "",
                    "lakehouse_id": l["lakehouse_id"], "lakehouse_name": l.get("name") or "", "env": host}
    return None


def moved_candidates(name: str, gone_id: str, lakehouses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lakehouses with the lost lakehouse's name (any case), other than the lost one itself."""
    name = str(name or "").strip().lower()
    return [l for l in lakehouses if name and str(l.get("lakehouse_name") or "").lower() == name
            and l.get("lakehouse_id") != gone_id]


def port_index(products: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Where each published product port lives, keyed like a OneLake shortcut's target."""
    out = {}
    for p in products:
        for port in p.get("ports") or []:
            if port.get("lakehouse_id"):
                path = f"Tables/{port.get('schema_name') or 'dbo'}/{port.get('table_name')}"
                key = f"onelake:{port.get('workspace_id')}/{port['lakehouse_id']}/{path}".lower()
                out[key] = {"id": p.get("id"), "name": p.get("name"), "status": p.get("status"),
                            "access": p.get("access"), "owner": p.get("owner"), "port": port.get("port"),
                            "env": port.get("env"), "version": p.get("version")}
                if (port.get("schema_name") or "dbo") == "dbo":  # a non-schema lakehouse: Tables/<table>
                    out[f"onelake:{port.get('workspace_id')}/{port['lakehouse_id']}/tables/{port.get('table_name')}".lower()] = out[key]
    return out


def check_rules(rules: dict[str, Any], new: dict[str, Any], is_admin: bool) -> dict[str, list[str]]:
    """What the platform's shortcut rules say about a shortcut about to be made (or repointed).
    ``new``: {host_env, target_env, target_type, connection_id, product}."""
    blocked: list[str] = []
    warnings: list[str] = []

    def say(mode: str, text: str) -> None:
        if mode == "block":
            blocked.append(text)
        elif mode == "warn":
            warnings.append(text)

    if rules.get("who_creates") == "admins" and not is_admin:
        blocked.append("Only platform admins create shortcuts here. Send a request instead: Shortcuts > Requests")
    host, tgt = new.get("host_env") or "", new.get("target_env") or ""
    if host and tgt and host != tgt:
        say(rules.get("cross_env", "off"), f"It points from {host.title()} at {tgt.title()}. "
                                           "Point it at the same environment's copy")
    if new.get("target_type") and new.get("target_type") != "OneLake":
        say(rules.get("storage_role", "off"), "Everyone who can read this lakehouse will read the storage with the "
                                              "connection's account. Add a data access role on the folder to limit it")
        allowed = rules.get("allowed_connections") or []
        if allowed and new.get("connection_id") not in allowed:
            say(rules.get("connections", "off"), "That connection isn't one the platform team allows for shortcuts")
    product = new.get("product") or {}
    if product.get("status") == "deprecated":
        say(rules.get("deprecated_product", "off"), f"{product.get('name')} is deprecated. Point at its replacement")
    return {"blocked": blocked, "warnings": warnings}


def needs(rows: list[dict[str, Any]], requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Things that need a shortcut change: broken ones a managed table reads, ones pointing at a
    deprecated product, and requests waiting on the person looking."""
    out = []
    for s in rows:
        if s.get("health") == "broken" and s.get("used_by"):
            users = ", ".join(u["entity"] for u in s["used_by"][:3])
            out.append({"kind": "broken_in_use", "id": identity(s), "title": f"{s['name']} is broken and {users} read it",
                        "detail": f"Their runs fail until it's fixed. {s.get('health_detail') or ''}".strip()})
        product = s.get("product") or {}
        if product.get("status") == "deprecated":
            out.append({"kind": "deprecated", "id": identity(s), "title": f"{s['name']} points at a deprecated product",
                        "detail": f"{product.get('name')} is deprecated. Repoint it at the product that replaces it."})
        for r in s.get("risks") or []:
            if r["kind"] == "cross_env" and not r.get("accepted"):
                out.append({"kind": "cross_env", "id": identity(s), "title": f"{s['name']}: {r['text']}",
                            "detail": f"In {s.get('workspace_name')} / {s.get('lakehouse_name')}. Repoint it at the same environment."})
    for r in requests:
        if r.get("kind") == "shortcut" and r.get("status") == "waiting" and r.get("can_decide"):
            d = r.get("detail") or {}
            out.append({"kind": "request", "id": r["id"], "title": f"{r.get('requested_by') or 'Someone'} asks for shortcut {d.get('name')}",
                        "detail": f"In {d.get('workspace_name') or ''} / {d.get('lakehouse_name') or ''}: {r.get('reason') or ''}".strip()})
    return out


def counts(rows: list[dict[str, Any]], changes: list[dict[str, Any]], requests: list[dict[str, Any]]) -> dict[str, int]:
    return {"total": len(rows), "lakehouses": len({s["lakehouse_id"] for s in rows}),
            "workspaces": len({s.get("host_workspace_id") for s in rows}),
            "broken": sum(s.get("health") == "broken" for s in rows),
            "unreachable": sum(s.get("health") == "unreachable" for s in rows),
            "not_checked": sum(s.get("health") in (None, "", "not_checked") for s in rows),
            "connection": sum(exposure(s) == "connection" for s in rows),
            "risky": sum(bool(open_risks(s)) for s in rows),
            "changed_7d": len(changes),
            "waiting": sum(r.get("kind") == "shortcut" and r.get("status") == "waiting" and bool(r.get("can_decide"))
                           for r in requests)}


def readers_gap(here: list[dict[str, Any]], there: list[dict[str, Any]] | None, mode: str) -> dict[str, Any]:
    """Who reads the shortcut's lakehouse, against who the target lets in. ``there`` None: unknown."""
    ids_here = {r["id"] for r in here}
    out: dict[str, Any] = {"mode": mode, "here": len(ids_here)}
    if mode == "connection":
        out |= {"target": None, "gap": len(ids_here), "missing": [],
                "text": f"{len(ids_here)} read the storage through the connection's account"}
    elif there is None:
        out |= {"target": None, "gap": None, "missing": [], "text": "You can't see who the target lets in"}
    else:
        ids_there = {r["id"] for r in there}
        missing = [r for r in here if r["id"] not in ids_there]
        out |= {"target": len(ids_here & ids_there), "gap": len(missing), "missing": missing[:50],
                "text": "Matches" if not missing else
                f"{len(missing)} of {len(ids_here)} see it empty: they have no access to the target"}
    return out


def same_definition(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return json.dumps(a or {}, sort_keys=True) == json.dumps(b or {}, sort_keys=True)
