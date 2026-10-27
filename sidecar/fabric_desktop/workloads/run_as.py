"""Who runs each workload, with what credential, and where data and credentials go (plan items 71,
78 and 79). Read only, from what the app already holds: your sign-in on this PC, agent status
records, and the Fabric schedules Monitor > Fabric schedules last read. Nothing here signs in, rotates a
credential or changes access, and it makes no Fabric call.

Rows: ``{"place": {"kind": "pc" | "fabric_jobs" | "agent" | "fabric_schedule", "name"}, "env",
"runs_as": {"kind", "name"}, "credential": {"kind", "state", "expires_at", "last_token_at", "error"},
"boundary", "detail", "as_of", "live"}``. A credential's ``state`` is ``ok``, ``expiring`` (a
certificate within ``EXPIRING_DAYS``), ``expired``, ``failed`` (its last token request failed),
``signed_out``, ``not_visible`` (Fabric or Azure holds it and doesn't say) or ``unknown`` (an
older agent that doesn't report it). ``attention`` lists the rows to look at, most urgent first.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .agents import agent_state

EXPIRING_DAYS = 30
_RUNS_AS = {"service_principal": "Service principal", "managed_identity": "Managed identity", "user": "User sign-in"}
_URGENCY = {"expired": 0, "failed": 1, "expiring": 2}


def _when(text: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def pc_rows(user: str, policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """This PC and the Fabric jobs this app starts, both as you. ``policy`` is the organization's
    settings that bound them (app_settings: ``allow_fabric_runs``, ``allowed_tenants``,
    ``local_retention_days``), read, never set, here."""
    p = policy or {}
    you = {"kind": "you", "name": user} if user else {"kind": "signed_out", "name": ""}
    cred = {"kind": "user", "state": "ok" if user else "signed_out", "expires_at": None, "last_token_at": None, "error": ""}
    tenants = len(p.get("allowed_tenants") or [])
    pc_detail = "Runs and schedules you start here. Windows schedules run only while you are signed in to Windows."
    if p.get("local_retention_days"):
        pc_detail += f" Local run results are kept {p['local_retention_days']} days."
    if tenants:
        pc_detail += f" Your organization allows sign-in to {tenants} {'tenant' if tenants == 1 else 'tenants'}."
    fabric_on = p.get("allow_fabric_runs", True) is not False
    return [
        {"place": {"kind": "pc", "name": "This PC"}, "env": None, "runs_as": you, "credential": cred,
         "boundary": "Data is read and written on this PC; tables go to OneLake over HTTPS",
         "detail": pc_detail, "as_of": None, "live": True},
        {"place": {"kind": "fabric_jobs", "name": "Fabric jobs from this app"}, "env": None, "runs_as": you,
         "credential": cred,
         "boundary": "Runs on the workspace's capacity; this app sends the job request and data stays in Fabric" if fabric_on
         else "Turned off by your organization's policy: this app doesn't start jobs on Fabric capacity",
         "detail": "Run in Fabric and side-by-side runs", "as_of": None, "live": True, "allowed": fabric_on},
    ]


def _credential(c: Any, identity: Any, now: dt.datetime) -> dict[str, Any]:
    if not isinstance(c, dict):  # an older agent
        kind = "managed_identity" if identity == "managed_identity" else ""
        return {"kind": kind, "state": "unknown", "expires_at": None, "last_token_at": None, "error": ""}
    out = {"kind": c.get("kind") or "", "expires_at": c.get("expires_at"), "last_token_at": c.get("last_token_at"),
           "error": ""}
    expires, error = _when(c.get("expires_at")), c.get("last_token_error") or None
    if expires is not None and expires <= now:
        state = "expired"
    elif isinstance(error, dict):
        state, out["error"] = "failed", str(error.get("error") or "")
        out["error_at"] = error.get("at")
    elif expires is not None and expires - now <= dt.timedelta(days=EXPIRING_DAYS):
        state = "expiring"
    elif out["kind"] == "certificate" and expires is None:
        state = "unknown"  # the agent couldn't read its certificate's dates
    else:  # a secret's or managed identity's expiry isn't visible; its last token request worked
        state = "ok" if out["kind"] == "certificate" or out["last_token_at"] else "unknown"
    return out | {"state": state}


def agent_rows(by_env: dict[str, list[dict[str, Any]]], now: dt.datetime) -> list[dict[str, Any]]:
    """One row per agent, from its newest record across environments."""
    newest: dict[str, tuple[dict[str, Any], list[str]]] = {}
    for env, records in by_env.items():
        for r in records:
            name = str(r.get("agent") or "")
            if not name:
                continue
            held = newest.get(name)
            envs = (held[1] if held else []) + [env]
            if held is None or str(r.get("heartbeat_at") or "") > str(held[0].get("heartbeat_at") or ""):
                newest[name] = (r, envs)
            else:
                newest[name] = (held[0], envs)
    rows = []
    for name, (r, envs) in sorted(newest.items()):
        identity = r.get("identity")
        live = agent_state(r, now) == "live"
        rows.append({"place": {"kind": "agent", "name": name}, "env": envs[0] if len(envs) == 1 else None,
                     "runs_as": {"kind": identity or "unknown", "name": _RUNS_AS.get(str(identity), "")},
                     "credential": _credential(r.get("credential"), identity, now),
                     "boundary": f"Data on {name}'s server ({r.get('host') or 'host not given'}); tables go to "
                                 f"{', '.join(e.title() for e in envs)} OneLake",
                     "detail": "As set in its agent.toml; the app doesn't see its ids", "as_of": r.get("heartbeat_at"),
                     "live": live})
    return rows


def fabric_rows(schedules: list[dict[str, Any]], env_of: dict[str, str]) -> list[dict[str, Any]]:
    """Enabled Fabric schedules on items in the environments' workspaces: Fabric runs each as its owner."""
    rows = []
    for s in schedules:
        if not s.get("enabled"):
            continue
        owner = s.get("owner") if isinstance(s.get("owner"), dict) else {}
        rows.append({"place": {"kind": "fabric_schedule", "name": f"{s.get('workspace_name') or 'Fabric'} · {s.get('name') or 'item'}"},
                     "env": env_of.get(str(s.get("workspace_id"))),
                     "runs_as": {"kind": "schedule_owner" if owner else "unknown",
                                 "name": owner.get("displayName") or owner.get("userPrincipalName") or
                                 (f"{owner.get('type') or 'Principal'} {str(owner.get('id'))[:8]}…" if owner.get("id") else "")},
                     "credential": {"kind": "fabric", "state": "not_visible", "expires_at": None, "last_token_at": None, "error": ""},
                     "boundary": f"Runs on the capacity of workspace {s.get('workspace_name') or 'its workspace'}; data stays in Fabric",
                     "detail": s.get("text") or "", "as_of": None, "live": True})
    return rows


def build(user: str, by_env: dict[str, list[dict[str, Any]]], schedules: list[dict[str, Any]],
          env_of: dict[str, str], now: dt.datetime, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = pc_rows(user, policy) + agent_rows(by_env, now) + fabric_rows(schedules, env_of)
    attention = sorted((r for r in rows if r["credential"]["state"] in _URGENCY),
                       key=lambda r: (_URGENCY[r["credential"]["state"]], r["place"]["name"]))
    return {"rows": rows, "attention": [{"place": r["place"], "state": r["credential"]["state"],
                                         "expires_at": r["credential"].get("expires_at"), "live": r["live"]}
                                        for r in attention],
            "expiring_days": EXPIRING_DAYS}
