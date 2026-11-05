"""The access cache: who has which workspace role, and each lakehouse's OneLake data access roles,
kept in the central lakehouse as the ``fdacc_`` table family so OneLake > Access can show every
workspace without asking Fabric about each one every time.

A refresh asks Fabric about each workspace and lakehouse as the signed-in person and writes:

- one ``scans`` row per workspace and lakehouse it looked at: when, whether it could read the
  roles, and the snapshot it saw;
- the snapshot's rows (``workspace_roles`` or ``data_access_roles``) only when the snapshot is new.
  A snapshot's id is a fingerprint of what it holds, so an unchanged workspace adds one scan row
  and nothing else, and a workspace that goes back to an earlier state reuses that snapshot.

Like every central lakehouse family, rows are only inserted; the latest scan wins. A scan that
couldn't read the roles keeps the last snapshot that could, marked stale, never an empty list.
Only names, ids, types and roles are kept: no email addresses. The tables sit in their own
``access`` schema, which ``access_cache_protect`` keeps from DefaultReader.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import polars as pl

from . import access as access_mod
from .optimize import schema

S, B = pl.String, pl.Boolean
WORKSPACE, LAKEHOUSE = "workspace_roles", "data_access_roles"
GONE = "gone"  # a lakehouse the cache knew that its workspace no longer lists

TABLES_V1: dict[str, dict[str, schema.Dtype]] = {
    # state: ok (roles read), error (couldn't read them; problem says why), gone (no longer in its
    # workspace). snapshot_id is "" unless ok.
    "scans": {"scan_id": S, "kind": S, "workspace_id": S, "workspace_name": S, "item_id": S, "item_name": S,
              "snapshot_id": S, "state": S, "problem": S, "observed_at": S},
    # principal_type: User, Group, ServicePrincipal, ServicePrincipalProfile. guest: a #EXT# account.
    "workspace_roles": {"snapshot_id": S, "workspace_id": S, "principal_id": S, "principal_type": S,
                        "principal_name": S, "guest": B, "role": S},
    # paths, entra (object ids) and item_members are JSON lists, as access.describe_role gives them.
    "data_access_roles": {"snapshot_id": S, "workspace_id": S, "item_id": S, "role_name": S, "paths": S,
                          "entra": S, "item_members": S},
    "schema_version": schema.TABLES_V1["schema_version"],
}

# Its own schema, so DefaultReader can leave it out and only READER_ROLE's members read it.
FAMILY = schema.register(schema.Family("fdacc_", "Access cache", [
    schema.Migration(1, "Create the access cache tables", TABLES_V1)], schema="access"))
READER_ROLE = "AccessCacheReaders"


def _fingerprint(kind: str, workspace_id: str, item_id: str, rows: list[dict[str, Any]]) -> str:
    """The same rows for the same workspace or lakehouse always give the same id."""
    body = json.dumps([kind, workspace_id, item_id, sorted(json.dumps(r, sort_keys=True) for r in rows)])
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def workspace_rows(workspace_id: str, assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for a in assignments:
        p = access_mod.principal(a.get("principal") or {})
        out.append({"workspace_id": workspace_id, "principal_id": p["id"], "principal_type": p["type"],
                    "principal_name": p["name"], "guest": p["guest"], "role": a.get("role")})
    return out


def lakehouse_rows(workspace_id: str, item_id: str, roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in roles:
        d = access_mod.describe_role(r)
        out.append({"workspace_id": workspace_id, "item_id": item_id, "role_name": d["name"],
                    "paths": json.dumps(d["paths"]), "entra": json.dumps(d["entra"]),
                    "item_members": json.dumps(d["item_members"])})
    return out


def known_lakehouses(scans: pl.DataFrame | None, workspace_id: str) -> dict[str, str]:
    """The lakehouses of a workspace the cache knows and hasn't seen go: {item_id: item_name}."""
    last: dict[str, dict[str, Any]] = {}
    for r in sorted(_df(scans), key=lambda r: r["observed_at"]):
        if r["kind"] == LAKEHOUSE and r["workspace_id"] == workspace_id:
            last[r["item_id"]] = r
    return {k: r["item_name"] for k, r in last.items() if r["state"] != GONE}


def latest_snapshots(scans: pl.DataFrame | None) -> dict[tuple[str, str, str], str]:
    """The last snapshot each workspace or lakehouse was seen with (errors don't count)."""
    out: dict[tuple[str, str, str], str] = {}
    if scans is None or scans.is_empty():
        return out
    for r in scans.filter(pl.col("state") == "ok").sort("observed_at").iter_rows(named=True):
        out[(r["kind"], r["workspace_id"], r["item_id"] or "")] = r["snapshot_id"]
    return out


class Refresh:
    """The rows one refresh writes, gathered first so the refresh is three commits at most."""

    def __init__(self, known: dict[tuple[str, str, str], str], at: str, installation_id: str):
        self.known, self.at, self.installation_id = known, at, installation_id
        self.scans: list[dict[str, Any]] = []
        self.rows: dict[str, list[dict[str, Any]]] = {WORKSPACE: [], LAKEHOUSE: []}
        self.changed = 0

    def add(self, kind: str, workspace: dict[str, str], item: dict[str, str] | None,
            rows: list[dict[str, Any]] | None, problem: str | None = None, gone: bool = False) -> None:
        """One workspace (``item`` None) or lakehouse as Fabric showed it; ``rows`` None with a
        ``problem`` when its roles couldn't be read, or with ``gone`` when it no longer exists."""
        ws, item_id = workspace["id"], (item or {}).get("id") or ""
        snapshot = "" if rows is None else _fingerprint(kind, ws, item_id, rows)
        if rows is not None and self.known.get((kind, ws, item_id)) != snapshot:
            self.changed += 1
            key = "principal_id" if kind == WORKSPACE else "role_name"
            self.rows[kind] += [r | {"record_id": f"{snapshot}:{r[key]}", "snapshot_id": snapshot} for r in rows]
        self.scans.append({"record_id": f"{kind}:{ws}:{item_id}:{self.at}:{self.installation_id}",
                           "scan_id": f"{self.at}:{self.installation_id}", "kind": kind, "workspace_id": ws,
                           "workspace_name": workspace.get("name") or "", "item_id": item_id,
                           "item_name": (item or {}).get("name") or "", "snapshot_id": snapshot,
                           "state": GONE if gone else "error" if rows is None else "ok", "problem": problem or "",
                           "observed_at": self.at})


def _df(df: pl.DataFrame | None) -> list[dict[str, Any]]:
    return [] if df is None else df.to_dicts()


def view(scans: pl.DataFrame | None, workspace_roles: pl.DataFrame | None, data_access_roles: pl.DataFrame | None,
         visible: set[str]) -> list[dict[str, Any]]:
    """Every workspace in ``visible`` the cache has seen, with its people and its lakehouses' roles
    from the last scan that could read them. ``stale`` marks one whose latest scan failed."""
    by_target: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for s in sorted(_df(scans), key=lambda s: s["observed_at"]):
        if s["workspace_id"] in visible:
            by_target.setdefault((s["kind"], s["workspace_id"], s["item_id"] or ""), []).append(s)
    members: dict[str, list[dict[str, Any]]] = {}
    for r in _df(workspace_roles) + _df(data_access_roles):
        members.setdefault(r["snapshot_id"], []).append(r)

    def state(history: list[dict[str, Any]]) -> dict[str, Any]:
        last, good = history[-1], [s for s in history if s["state"] == "ok"]
        snap = good[-1]["snapshot_id"] if good else ""
        changed_at = None  # when the current snapshot was first seen, after the one before it
        for s in good:
            if s["snapshot_id"] != snap:
                changed_at = None
            elif changed_at is None:
                changed_at = s["observed_at"]
        return {"checked_at": last["observed_at"], "changed_at": changed_at, "known": bool(good),
                "stale": last["state"] != "ok", "problem": last["problem"] or None,
                "rows": members.get(snap, []) if snap else []}

    out: dict[str, dict[str, Any]] = {}
    for (kind, ws, item_id), history in sorted(by_target.items()):
        st, last = state(history), history[-1]
        if last["state"] == GONE:
            continue
        entry = out.setdefault(ws, {"workspace_id": ws, "workspace_name": last["workspace_name"], "people": [],
                                    "lakehouses": [], "checked_at": None, "stale": False, "problem": None,
                                    "known": False, "changed_at": None})
        if kind == WORKSPACE:
            entry.update({k: st[k] for k in ("checked_at", "changed_at", "stale", "problem", "known")},
                         workspace_name=last["workspace_name"] or entry["workspace_name"])
            entry["people"] = sorted(({"id": r["principal_id"], "name": r["principal_name"], "type": r["principal_type"],
                                       "guest": bool(r["guest"]), "role": r["role"]} for r in st["rows"]),
                                     key=lambda p: (p["type"] != "User", str(p["name"]).lower()))
        else:
            entry["lakehouses"].append({"item_id": item_id, "item_name": last["item_name"],
                                        **{k: st[k] for k in ("checked_at", "changed_at", "stale", "problem", "known")},
                                        "roles": sorted(({"name": r["role_name"], "paths": json.loads(r["paths"] or "[]"),
                                                          "entra": json.loads(r["entra"] or "[]"),
                                                          "item_members": json.loads(r["item_members"] or "[]")}
                                                         for r in st["rows"]), key=lambda r: str(r["name"]))})
    for entry in out.values():
        people = entry["people"]
        entry["summary"] = {"people": sum(p["type"] == "User" for p in people),
                            "groups": sum(p["type"] == "Group" for p in people),
                            "apps": sum(p["type"] not in ("User", "Group") for p in people),
                            "guests": sum(p["guest"] for p in people)}
    return sorted(out.values(), key=lambda w: str(w["workspace_name"]).lower())
